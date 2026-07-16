from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api import TenantViewSet
from apps.core.middleware import set_tenant_from_request

from .models import CostCode, CostEntry, Invoice, Variation
from .serializers import (
    CostCodeSerializer,
    CostEntrySerializer,
    InvoiceCreateSerializer,
    InvoiceSerializer,
    PaymentSerializer,
    VariationSerializer,
)
from .services import (
    approve_variation,
    commercial_dashboard,
    create_invoice,
    issue_invoice,
    record_payment,
)


class CostCodeViewSet(TenantViewSet):
    model = CostCode
    serializer_class = CostCodeSerializer
    search_fields = ["code", "name"]
    required_perms = {"create": "finance.manage", "update": "finance.manage",
                      "partial_update": "finance.manage", "destroy": "finance.manage"}

    def get_queryset(self):
        return CostCode.objects.all()


class CostEntryViewSet(TenantViewSet):
    """The actual-cost ledger (convergence point). Read + manual adjustments.
    Filter by ?project=<id>."""

    model = CostEntry
    serializer_class = CostEntrySerializer
    required_perms = {"create": "finance.manage", "update": "finance.manage",
                      "partial_update": "finance.manage", "destroy": "finance.manage"}

    def get_queryset(self):
        qs = CostEntry.objects.all().select_related("project")
        project = self.request.query_params.get("project")
        return qs.filter(project_id=project) if project else qs


class InvoiceViewSet(TenantViewSet):
    """Invoices / progress claims. Sending is human-approved — nothing auto-sends.
    Money is Golden-Rule gated. Filter by ?project=<id>."""

    model = Invoice
    serializer_class = InvoiceSerializer
    search_fields = ["number", "client_name", "status"]
    required_perms = {"create": "finance.manage", "issue": "invoices.approve",
                      "record_payment": "finance.manage"}

    def get_queryset(self):
        qs = Invoice.objects.all().prefetch_related("lines", "payments").select_related("project")
        project = self.request.query_params.get("project")
        return qs.filter(project_id=project) if project else qs

    def create(self, request, *args, **kwargs):
        from apps.projects.models import Project
        payload = InvoiceCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        project = get_object_or_404(Project.objects.all(), id=data["project"])
        invoice = create_invoice(
            project, request.user, client_name=data.get("client_name"),
            lines=data.get("lines", []), retention_pct=data.get("retention_pct", 0),
            due_date=data.get("due_date"),
        )
        return Response(InvoiceSerializer(invoice, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        invoice = issue_invoice(self.get_object(), request.user)
        return Response(InvoiceSerializer(invoice, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="record-payment")
    def record_payment(self, request, pk=None):
        payload = PaymentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        invoice = record_payment(self.get_object(), request.user, **payload.validated_data)
        return Response(InvoiceSerializer(invoice, context={"request": request}).data)


class VariationViewSet(TenantViewSet):
    """Variations — commercial view. Approval updates the budget automatically.
    Filter by ?project=<id>."""

    model = Variation
    serializer_class = VariationSerializer
    required_perms = {"create": "finance.manage", "approve": "invoices.approve"}

    def get_queryset(self):
        qs = Variation.objects.all().select_related("project")
        project = self.request.query_params.get("project")
        return qs.filter(project_id=project) if project else qs

    def perform_create(self, serializer):
        from apps.administration.services import next_number
        serializer.save(created_by=self.request.user, updated_by=self.request.user,
                        number=next_number(self.request.user.active_company, "variation"))

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        variation = approve_variation(self.get_object(), request.user)
        return Response(VariationSerializer(variation, context={"request": request}).data)


class CommercialDashboardView(APIView):
    """Portfolio commercial view — entirely money, so gated by finance.view_money."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        set_tenant_from_request(request)
        if not request.user.has_perm_code("finance.view_money"):
            return Response({"error": {"code": "forbidden", "message": "Need finance.view_money."}},
                            status=status.HTTP_403_FORBIDDEN)
        return Response(commercial_dashboard(request.user.active_company))
