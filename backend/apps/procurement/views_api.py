from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import PurchaseOrder, Supplier
from .serializers import (
    POCreateSerializer,
    PurchaseOrderSerializer,
    SupplierSerializer,
)
from .services import create_purchase_order, three_way_match


class SupplierViewSet(TenantViewSet):
    model = Supplier
    serializer_class = SupplierSerializer
    search_fields = ["name", "categories"]
    ordering_fields = ["name", "performance_score"]
    required_perms = {
        "create": "procurement.manage",
        "update": "procurement.manage",
        "partial_update": "procurement.manage",
        "destroy": "procurement.manage",
    }

    def get_queryset(self):
        return Supplier.objects.all()


class PurchaseOrderViewSet(TenantViewSet):
    """Outbound POs (us → supplier). Sending is approval-gated; money is
    Golden-Rule gated at the serializer."""

    model = PurchaseOrder
    serializer_class = PurchaseOrderSerializer
    search_fields = ["number", "supplier__name"]
    required_perms = {"create": "procurement.manage"}

    def get_queryset(self):
        return PurchaseOrder.objects.all().prefetch_related("lines").select_related("supplier")

    def create(self, request, *args, **kwargs):
        payload = POCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        # Scoped resolve — 404s a supplier id from another tenant.
        supplier = get_object_or_404(Supplier.objects.all(), id=data["supplier"])
        po = create_purchase_order(
            request.user.active_company, request.user,
            supplier=supplier, lines=data["lines"],
            delivery_address=data.get("delivery_address", ""),
        )
        return Response(
            PurchaseOrderSerializer(po, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if not request.user.has_perm_code("po.approve"):
            return Response(
                {"error": {"code": "forbidden", "message": "Need po.approve."}},
                status=status.HTTP_403_FORBIDDEN,
            )
        po = self.get_object()
        po.status = "approved"
        po.approved_by = request.user
        po.save(update_fields=["status", "approved_by"])
        return Response(PurchaseOrderSerializer(po, context={"request": request}).data)

    @action(detail=True, methods=["get"])
    def match(self, request, pk=None):
        """3-way match result (PO ↔ GRN ↔ supplier invoice)."""
        return Response(three_way_match(self.get_object()))
