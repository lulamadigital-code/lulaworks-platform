from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.api import TenantViewSet
from apps.quotes.serializers import QuotationSerializer

from .models import RFQDocument, RFQStatus
from .serializers import (
    ApproveSerializer,
    ExtractedFieldSerializer,
    RFQDocumentSerializer,
    RFQLineItemSerializer,
)
from .services import approve_rfq, ingest_rfq


class RFQViewSet(TenantViewSet):
    """RFQ Intelligence pipeline. Upload (multipart) → deterministic extraction →
    review (edit fields/lines) → approve → Quotation + Project DNA. Never
    auto-approves (human-approval boundary)."""

    model = RFQDocument
    serializer_class = RFQDocumentSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    required_perms = {"create": "rfq.upload"}

    def get_queryset(self):
        return RFQDocument.objects.all().prefetch_related("fields", "lines")

    def create(self, request, *args, **kwargs):
        upload = request.FILES.get("file")
        if not upload:
            return Response(
                {"error": {"code": "no_file", "message": "Upload a PDF as 'file'."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rfq = ingest_rfq(
            request.user.active_company, request.user,
            uploaded_file=upload, original_name=upload.name,
        )
        return Response(
            RFQDocumentSerializer(rfq, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["patch"])
    def review(self, request, pk=None):
        """Human edits to extracted fields / lines before approval."""
        rfq = self.get_object()
        for item in request.data.get("fields", []):
            field = rfq.fields.filter(id=item.get("id")).first()
            if field:
                field.approved_value = item.get("approved_value", field.value)
                field.review_status = "edited"
                field.save(update_fields=["approved_value", "review_status"])
        for item in request.data.get("lines", []):
            line = rfq.lines.filter(id=item.get("id")).first()
            if line:
                for f in ("description", "qty", "unit", "unit_price"):
                    if f in item:
                        setattr(line, f, item[f])
                line.save()
        # Re-fetch fresh — get_object() prefetched fields/lines, so serialising
        # the same instance would return the pre-edit cached values.
        fresh = self.get_queryset().get(pk=rfq.pk)
        return Response(RFQDocumentSerializer(fresh, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if not request.user.has_perm_code("rfq.approve"):
            return Response(
                {"error": {"code": "forbidden", "message": "Need rfq.approve."}},
                status=status.HTTP_403_FORBIDDEN,
            )
        rfq = self.get_object()
        if rfq.status == RFQStatus.APPROVED:
            return Response(
                {"error": {"code": "conflict", "message": "Already approved."}},
                status=status.HTTP_409_CONFLICT,
            )
        payload = ApproveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        rfq = approve_rfq(rfq, request.user, client_name=payload.validated_data["client_name"])
        return Response({
            "rfq": RFQDocumentSerializer(rfq, context={"request": request}).data,
            "quotation": QuotationSerializer(rfq.quotation, context={"request": request}).data,
        }, status=status.HTTP_201_CREATED)


# expose serializers used only for schema/tests
__all__ = ["RFQViewSet", "ExtractedFieldSerializer", "RFQLineItemSerializer"]
