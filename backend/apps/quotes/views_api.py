from rest_framework import status
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import Quotation
from .serializers import QuotationCreateSerializer, QuotationSerializer
from .services import create_quotation


class QuotationViewSet(TenantViewSet):
    """Quotations — the first real business resource on the auto-scoping
    TenantViewSet. Listing/retrieval are tenant-isolated by the ambient manager;
    money fields are Golden-Rule-gated at the serializer."""

    model = Quotation
    serializer_class = QuotationSerializer
    search_fields = ["number", "client_name", "title"]
    ordering_fields = ["created_at", "number"]

    def get_queryset(self):
        return Quotation.objects.all().prefetch_related("lines")

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
