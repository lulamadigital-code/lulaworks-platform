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
        "restore": "procurement.manage",
        "disabled": "procurement.manage",
        # "purge" is intentionally absent — gated on platform ownership inline.
    }

    def get_queryset(self):
        return Supplier.objects.all()

    # ── Delete model (same two-tier rule as customers) ───────────────────────
    # A tenant procurement admin (procurement.manage) can DISABLE a supplier — a
    # recoverable soft-delete that keeps the row and its price/PO history. Only
    # the software owner (platform owner/admin) can PERMANENTLY purge it, and
    # only once it is already disabled.
    def perform_destroy(self, instance):
        """DELETE /suppliers/<id>/ → soft delete (disable)."""
        from apps.core.events import publish

        name = instance.name
        instance.deleted_by = self.request.user
        instance.save(update_fields=["deleted_by"])
        instance.delete()  # soft: is_deleted=True
        publish("SupplierDisabled", company=instance.company, subject=instance,
                actor=self.request.user, payload={"name": name})

    @action(detail=False, methods=["get"])
    def disabled(self, request):
        """The tenant's disabled suppliers — the app's 'Disabled' view."""
        qs = Supplier.all_objects.filter(
            company=request.user.active_company, is_deleted=True).order_by("name")
        return Response(SupplierSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Bring a disabled supplier back (procurement.manage), tenant-scoped."""
        from apps.core.events import publish

        supplier = get_object_or_404(
            Supplier.all_objects.filter(
                company=request.user.active_company, is_deleted=True),
            pk=pk)
        supplier.is_deleted = False
        supplier.deleted_at = None
        supplier.deleted_by = None
        supplier.updated_by = request.user
        supplier.save(update_fields=[
            "is_deleted", "deleted_at", "deleted_by", "updated_by", "updated_at"])
        publish("SupplierRestored", company=supplier.company, subject=supplier,
                actor=request.user)
        return Response(SupplierSerializer(supplier).data)

    @action(detail=True, methods=["delete", "post"])
    def purge(self, request, pk=None):
        """PERMANENTLY delete a supplier — software-owner only, and only one that
        is already disabled (deliberate two-step)."""
        from apps.core.events import publish

        if not request.user.is_platform_admin:
            return Response({"error": {"code": "forbidden", "message":
                "Only the software owner can permanently delete a supplier. "
                "A procurement admin can disable it instead."}},
                status=status.HTTP_403_FORBIDDEN)
        supplier = get_object_or_404(
            Supplier.all_objects.filter(
                company=request.user.active_company, is_deleted=True),
            pk=pk)
        name, company = supplier.name, supplier.company
        publish("SupplierDeleted", company=company, subject=supplier,
                actor=request.user, payload={"name": name})  # before it's gone
        supplier.delete(hard=True)  # real removal
        return Response(status=status.HTTP_204_NO_CONTENT)


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
