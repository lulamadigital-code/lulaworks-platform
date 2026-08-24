from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import Quotation, QuotationStatus
from .pdf import quotation_pdf_bytes
from .serializers import QuotationCreateSerializer, QuotationSerializer
from .services import create_quotation, next_statuses, transition

_STATUS_LABELS = dict(QuotationStatus.choices)


class QuotationViewSet(TenantViewSet):
    """Quotations — the first real business resource on the auto-scoping
    TenantViewSet. Listing/retrieval are tenant-isolated by the ambient manager;
    money fields are Golden-Rule-gated at the serializer. Filter by ?customer=.
    """

    model = Quotation
    serializer_class = QuotationSerializer
    search_fields = ["number", "client_name", "title"]
    ordering_fields = ["created_at", "number"]
    required_perms = {"create": "quotes.create"}

    def get_queryset(self):
        qs = Quotation.objects.all().prefetch_related("lines")
        customer = self.request.query_params.get("customer")
        return qs.filter(customer_id=customer) if customer else qs

    def create(self, request, *args, **kwargs):
        payload = QuotationCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        quote = create_quotation(
            request.user.active_company, request.user, **payload.validated_data
        )
        return Response(
            QuotationSerializer(quote, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def workflow(self, request, pk=None):
        """The current status and the sensible next steps, as {value,label} — so
        the mobile client can render the right transition buttons."""
        quote = self.get_object()
        return Response({
            "status": quote.status,
            "status_label": _STATUS_LABELS.get(quote.status, quote.status),
            "next": [{"value": s, "label": _STATUS_LABELS.get(s, s)}
                     for s in next_statuses(quote)],
        })

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        """Move the quotation along its lifecycle. The service enforces which
        transitions are legal; an illegal one returns 409."""
        if not (request.user.has_perm_code("quotes.approve")
                or request.user.has_perm_code("quotes.create")):
            return Response({"error": {"code": "forbidden",
                             "message": "Need quotes.create or quotes.approve."}},
                            status=status.HTTP_403_FORBIDDEN)
        to_status = request.data.get("to_status")
        if not to_status:
            return Response({"error": {"code": "invalid", "message": "to_status is required."}},
                            status=status.HTTP_400_BAD_REQUEST)
        quote = self.get_object()
        try:
            quote = transition(quote, request.user,
                               to_status=to_status, note=request.data.get("note", ""))
        except Exception as exc:  # noqa: BLE001 — surface a clean message, not a 500
            return Response({"error": {"code": "conflict", "message": str(exc)}},
                            status=status.HTTP_409_CONFLICT)
        return Response(
            QuotationSerializer(quote, context={"request": request}).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """The official generated quotation PDF (same renderer as the web)."""
        if not request.user.has_perm_code("quotes.download"):
            return Response({"error": {"code": "forbidden", "message": "Need quotes.download."}},
                            status=status.HTTP_403_FORBIDDEN)
        quote = self.get_object()
        pdf = quotation_pdf_bytes(quote)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{quote.number}.pdf"'
        return resp
