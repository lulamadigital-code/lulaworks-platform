"""JSON API for Commercial Documents — tax invoices and delivery notes.

Both are generated from a quotation and share one lifecycle
(draft → approved → finalized → sent). Two hard rules shape the serialization:
  * A **delivery note carries no prices** (§15) — quantities only, always.
  * Invoice money (line prices, totals, outstanding, payments) is withheld from
    users without finance.view_money (Golden Rule).
Both are enforced here in `_serialize`, not left to the client.
"""
from datetime import date as _date

from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import CommercialDocument, CommercialDocumentPayment
from .pdf import delivery_note_pdf_bytes, invoice_pdf_bytes
from .services import (
    commercial_document_next_statuses,
    transition_commercial_document,
)

_STATUS_LABELS = dict(CommercialDocument.Status.choices)


def _lines(doc, *, with_prices):
    out = []
    for ln in doc.quotation.lines.all():
        row = {"description": ln.description, "qty": str(ln.qty), "unit": ln.unit}
        if with_prices:
            row["unit_price"] = str(ln.unit_price)
            row["line_total"] = str(ln.line_total)
        out.append(row)
    return out


def _serialize(doc, request):
    base = {
        "id": str(doc.id),
        "kind": doc.kind,
        "number": doc.number,
        "status": doc.status,
        "status_label": _STATUS_LABELS.get(doc.status, doc.status),
        "client_name": doc.quotation.client_name,
        "site": doc.quotation.site,
        "quotation_number": doc.quotation.number,
        "created_at": doc.created_at,
    }
    if doc.kind == CommercialDocument.Kind.DELIVERY:
        base.update({
            "lines": _lines(doc, with_prices=False),   # §15 — never priced
            "delivery_date": doc.delivery_date,
            "delivery_address": doc.delivery_address,
            "delivery_notes": doc.delivery_notes,
        })
    else:  # invoice
        money = request.user.has_perm_code("finance.view_money")
        base.update({
            "lines": _lines(doc, with_prices=money),
            "total": str(doc.invoice_amount) if money else None,
            "amount_paid": str(doc.amount_paid) if money else None,
            "outstanding": str(doc.outstanding) if money else None,
            "payment_state": doc.payment_state,
            "payments": ([{"date": p.date, "amount": str(p.amount),
                           "method": p.method, "reference": p.reference}
                          for p in doc.payments.all()] if money else []),
        })
    return base


class _Stub(serializers.Serializer):
    """DRF needs a serializer_class; list/retrieve are fully overridden below."""


class CommercialDocumentViewSet(TenantViewSet):
    """Tax invoices + delivery notes. Filter by ?kind=invoice|delivery,
    ?customer=<id>, ?quotation=<id>."""

    model = CommercialDocument
    serializer_class = _Stub
    search_fields = ["number", "quotation__client_name"]

    def get_queryset(self):
        qs = (CommercialDocument.objects.all()
              .select_related("quotation")
              .prefetch_related("quotation__lines", "payments"))
        p = self.request.query_params
        if p.get("kind"):
            qs = qs.filter(kind=p["kind"])
        if p.get("customer"):
            qs = qs.filter(quotation__customer_id=p["customer"])
        if p.get("quotation"):
            qs = qs.filter(quotation_id=p["quotation"])
        return qs

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        data = [_serialize(d, request) for d in page]
        return self.get_paginated_response(data)

    def retrieve(self, request, *args, **kwargs):
        return Response(_serialize(self.get_object(), request))

    @action(detail=True, methods=["get"])
    def workflow(self, request, pk=None):
        doc = self.get_object()
        return Response({
            "status": doc.status,
            "status_label": _STATUS_LABELS.get(doc.status, doc.status),
            "next": [{"value": s, "label": _STATUS_LABELS.get(s, s)}
                     for s in commercial_document_next_statuses(doc)],
        })

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        if not (request.user.has_perm_code("invoices.approve")
                or request.user.has_perm_code("quotes.approve")):
            return Response({"error": {"code": "forbidden",
                             "message": "Need invoices.approve or quotes.approve."}},
                            status=status.HTTP_403_FORBIDDEN)
        to_status = request.data.get("to_status")
        doc = self.get_object()
        try:
            doc = transition_commercial_document(doc, request.user, to_status)
        except Exception as exc:  # noqa: BLE001
            return Response({"error": {"code": "conflict", "message": str(exc)}},
                            status=status.HTTP_409_CONFLICT)
        return Response(_serialize(doc, request))

    @action(detail=True, methods=["post"])
    def payment(self, request, pk=None):
        """Record a payment against a tax invoice."""
        if not (request.user.has_perm_code("finance.manage")
                or request.user.has_perm_code("invoices.approve")):
            return Response({"error": {"code": "forbidden",
                             "message": "Need finance.manage or invoices.approve."}},
                            status=status.HTTP_403_FORBIDDEN)
        doc = self.get_object()
        if doc.kind != CommercialDocument.Kind.INVOICE:
            return Response({"error": {"code": "invalid",
                             "message": "Only tax invoices take payments."}},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            amount = request.data.get("amount")
            payment = CommercialDocumentPayment.objects.create(
                company=doc.company, document=doc,
                date=request.data.get("date") or _date.today(),
                amount=amount, method=request.data.get("method", "eft"),
                reference=request.data.get("reference", ""),
                created_by=request.user, updated_by=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"id": str(payment.id), "outstanding": str(doc.outstanding)},
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        if not request.user.has_perm_code("quotes.download"):
            return Response({"error": {"code": "forbidden", "message": "Need quotes.download."}},
                            status=status.HTTP_403_FORBIDDEN)
        doc = self.get_object()
        pdf_bytes = (invoice_pdf_bytes(doc)
                     if doc.kind == CommercialDocument.Kind.INVOICE
                     else delivery_note_pdf_bytes(doc))
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{doc.number}.pdf"'
        return resp
