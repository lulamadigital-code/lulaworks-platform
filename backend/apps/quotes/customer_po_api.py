"""JSON API for Customer Purchase Orders — the Sales→Ops bridge.

A customer PO is captured (uploaded + AI-extracted, or typed), matched to the
quotation it confirms, then converted into operational work (a job). This API is
a thin driver over the SAME services the web workspace uses
(apps.web.views_po) — extraction, suggestion, work initiation — so there is one
source of truth and no mobile-only business rules.

`value` is money (Golden Rule): the PO value, suggestion values and variance are
withheld from a user without finance.view_money.
"""
from datetime import date as _date

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import CustomerPurchaseOrder, Quotation
from .services import (
    QuotationError,
    initiate_work_from_quotation,
    po_line_variance,
    po_variance,
    suggest_quotations_for_po,
)

_STATUS_LABELS = dict(CustomerPurchaseOrder.Status.choices)


def _can_see(user):
    return any(user.has_perm_code(p) for p in
               ("quotes.create", "quotes.approve", "quotes.download", "projects.view"))


def _can_edit(user):
    return user.has_perm_code("quotes.create")


def _dec_or_none(raw):
    from decimal import Decimal, InvalidOperation
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, TypeError):
        return None


def _date_or_none(raw):
    try:
        return _date.fromisoformat((raw or "").strip()) if raw else None
    except (ValueError, TypeError):
        return None


def _save_po_lines(po, lines, user):
    """Persist line items read off a PO document (best-effort — skip blanks).
    Mirrors apps.web.views_po._save_po_lines."""
    from .models import CustomerPurchaseOrderLine
    for i, ln in enumerate(lines or []):
        if not isinstance(ln, dict):
            continue
        desc = (str(ln.get("description") or "")).strip()
        if not desc:
            continue
        CustomerPurchaseOrderLine.objects.create(
            company=po.company, purchase_order=po, position=i,
            description=desc[:500], qty=_dec_or_none(ln.get("qty")) or 1,
            unit=(str(ln.get("unit") or "each"))[:32],
            unit_price=_dec_or_none(ln.get("unit_price")) or 0,
            created_by=user, updated_by=user)


def _serialize(po, request, *, detail=False):
    money = request.user.has_perm_code("finance.view_money")
    base = {
        "id": str(po.id),
        "po_number": po.po_number,
        "client_name": po.client_name,
        "customer_display": po.customer_display,
        "site": po.site,
        "po_date": po.po_date,
        "status": po.status,
        "status_label": _STATUS_LABELS.get(po.status, po.status),
        "is_matched": po.is_matched,
        "value": str(po.value) if money else None,
        "quotation": str(po.quotation_id) if po.quotation_id else None,
        "quotation_number": po.quotation.number if po.quotation_id else None,
        "has_document": bool(po.document),
        "created_at": po.created_at,
    }
    if detail:
        base["lines"] = [
            {"description": ln.description, "qty": str(ln.qty), "unit": ln.unit,
             "unit_price": (str(ln.unit_price) if money else None)}
            for ln in po.lines.all()
        ]
        base["notes"] = po.notes
    return base


class _Stub(serializers.Serializer):
    """DRF needs a serializer_class; list/retrieve/create are overridden."""


class CustomerPurchaseOrderViewSet(TenantViewSet):
    """Customer POs: capture → match → convert. Filter ?bucket=unmatched|matched
    |converted|closed is left to the client; the list returns all POs."""

    model = CustomerPurchaseOrder
    serializer_class = _Stub
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    search_fields = ["po_number", "client_name", "quotation__number"]

    def get_queryset(self):
        return (CustomerPurchaseOrder.objects.all()
                .select_related("quotation", "quotation__customer")
                .prefetch_related("lines"))

    def list(self, request, *args, **kwargs):
        if not _can_see(request.user):
            return Response({"error": {"code": "forbidden",
                             "message": "No access to purchase orders."}},
                            status=status.HTTP_403_FORBIDDEN)
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response([_serialize(p, request) for p in page])

    def retrieve(self, request, *args, **kwargs):
        if not _can_see(request.user):
            return Response({"error": {"code": "forbidden",
                             "message": "No access to purchase orders."}},
                            status=status.HTTP_403_FORBIDDEN)
        po = self.get_object()
        data = _serialize(po, request, detail=True)
        money = request.user.has_perm_code("finance.view_money")
        if po.is_matched:
            if money:
                data["variance"] = _jsonable(po_variance(po))
                data["line_variance"] = _jsonable(po_line_variance(po))
        else:
            data["suggestions"] = [
                {"quotation": str(s["quotation"].id),
                 "number": s["quotation"].number,
                 "client_name": s["quotation"].client_name,
                 "score": s["score"], "reason": s["reason"]}
                for s in suggest_quotations_for_po(
                    request.user.active_company, po_number=po.po_number,
                    client_name=po.client_name,
                    value=(po.value if money else None))
            ]
        return Response(data)

    def create(self, request, *args, **kwargs):
        """Capture a PO — upload a document (auto-extract) or type the fields."""
        if not _can_edit(request.user):
            return Response({"error": {"code": "forbidden",
                             "message": "Need quotes.create."}},
                            status=status.HTTP_403_FORBIDDEN)
        from apps.core.uploads import validate_upload
        from apps.knowledge.document_intelligence import (extract_po_fields,
                                                          extract_text_from_upload)
        data = {k: (request.data.get(k) or "").strip() if isinstance(request.data.get(k), str)
                else request.data.get(k)
                for k in ("po_number", "client_name", "site", "value", "po_date", "notes")}
        f = request.FILES.get("document")
        extracted_lines = []
        if f:
            try:
                validate_upload(f)
            except ValidationError as exc:
                return Response({"error": {"code": "invalid", "message": exc.messages[0]}},
                                status=status.HTTP_400_BAD_REQUEST)
            fields = extract_po_fields(extract_text_from_upload(f),
                                       company=request.user.active_company,
                                       user=request.user, use_ai=True)
            f.seek(0)
            for key in ("po_number", "value", "po_date"):
                if not data.get(key) and fields.get(key):
                    data[key] = str(fields[key])
            if not data.get("client_name") and fields.get("client"):
                data["client_name"] = str(fields["client"])
            extracted_lines = fields.get("lines") or []
        if not any([data.get("po_number"), data.get("value"), data.get("client_name"), f]):
            return Response({"error": {"code": "invalid",
                             "message": "Add a PO number, or upload the PO document."}},
                            status=status.HTTP_400_BAD_REQUEST)
        po = CustomerPurchaseOrder.objects.create(
            company=request.user.active_company,
            po_number=data.get("po_number")
            or f"PO-{_date.today():%Y%m%d}-{CustomerPurchaseOrder.objects.count() + 1:04d}",
            client_name=data.get("client_name") or "", site=data.get("site") or "",
            value=_dec_or_none(data.get("value")) or 0,
            po_date=_date_or_none(data.get("po_date")),
            notes=(data.get("notes") or "")[:255],
            document=f or None, created_by=request.user, updated_by=request.user)
        _save_po_lines(po, extracted_lines, request.user)
        return Response(_serialize(po, request, detail=True),
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def extract(self, request):
        """Stateless: read an uploaded PO and return its fields. Saves nothing —
        the Add form pre-fills from this."""
        if not _can_edit(request.user):
            return Response({"error": {"code": "forbidden", "message": "Need quotes.create."}},
                            status=status.HTTP_403_FORBIDDEN)
        from apps.knowledge.document_intelligence import (extract_po_fields,
                                                          extract_text_from_upload)
        f = request.FILES.get("document")
        if not f:
            return Response({"error": {"code": "no_file", "message": "Upload the PO as 'document'."}},
                            status=status.HTTP_400_BAD_REQUEST)
        fields = extract_po_fields(extract_text_from_upload(f),
                                   company=request.user.active_company,
                                   user=request.user, use_ai=True)
        return Response(_jsonable(fields))

    @action(detail=True, methods=["post"])
    def link(self, request, pk=None):
        """Link (or relink) the PO to a quotation."""
        if not _can_edit(request.user):
            return Response({"error": {"code": "forbidden", "message": "Need quotes.create."}},
                            status=status.HTTP_403_FORBIDDEN)
        po = self.get_object()
        quote = get_object_or_404(Quotation.objects.all(),
                                  pk=request.data.get("quotation"))
        po.quotation = quote
        po.status = CustomerPurchaseOrder.Status.ACKNOWLEDGED
        po.updated_by = request.user
        po.save(update_fields=["quotation", "status", "updated_by", "updated_at"])
        return Response(_serialize(po, request, detail=True))

    @action(detail=True, methods=["post"], url_path="create-job")
    def create_job(self, request, pk=None):
        """Convert the matched PO into operational work (project · phases · tasks)."""
        if not request.user.has_perm_code("projects.create"):
            return Response({"error": {"code": "forbidden",
                             "message": "Need projects.create."}},
                            status=status.HTTP_403_FORBIDDEN)
        po = self.get_object()
        if not po.is_matched:
            return Response({"error": {"code": "invalid",
                             "message": "Match the PO to a quotation first."}},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            project = initiate_work_from_quotation(po.quotation, request.user)
        except QuotationError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        po.status = CustomerPurchaseOrder.Status.IN_PROGRESS
        po.save(update_fields=["status", "updated_at"])
        return Response({"project": {"id": str(project.id), "number": project.number}},
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        if not _can_edit(request.user):
            return Response({"error": {"code": "forbidden", "message": "Need quotes.create."}},
                            status=status.HTTP_403_FORBIDDEN)
        po = self.get_object()
        new = request.data.get("status")
        if new not in dict(CustomerPurchaseOrder.Status.choices):
            return Response({"error": {"code": "invalid", "message": "Unknown status."}},
                            status=status.HTTP_400_BAD_REQUEST)
        po.status = new
        po.save(update_fields=["status", "updated_at"])
        return Response(_serialize(po, request, detail=True))


def _jsonable(value):
    """Coerce Decimals/dates in a service dict to JSON-safe primitives."""
    from decimal import Decimal
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
