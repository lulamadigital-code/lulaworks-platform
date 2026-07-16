from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from apps.compliance.models import ComplianceRequirement
from apps.compliance.serializers import ComplianceItemSerializer
from apps.compliance.services import override as override_gate
from apps.compliance.services import recompute_readiness
from apps.core.api import TenantViewSet
from apps.quotes.models import Quotation

from .models import Project
from .serializers import AwardSerializer, OverrideSerializer, ProjectSerializer
from .services import award_quotation


class ProjectViewSet(TenantViewSet):
    """Projects — the execution aggregate root. Created by awarding a quotation;
    the compliance gate governs whether it can enter execution."""

    model = Project
    serializer_class = ProjectSerializer
    search_fields = ["number", "client_name", "title", "work_type"]
    ordering_fields = ["created_at", "number"]
    required_perms = {
        "create": "projects.create",
        "override": "compliance.override",
        "capture_actuals": "execution.manage",
    }

    def get_queryset(self):
        return Project.objects.all().select_related("quotation")

    def create(self, request, *args, **kwargs):
        """Award a quotation → create the project (fires compliance discovery)."""
        payload = AwardSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        quotation = get_object_or_404(Quotation.objects.all(), id=data["quotation"])
        project = award_quotation(
            request.user.active_company, request.user, quotation=quotation,
            work_type=data.get("work_type", ""), mine=data.get("mine", ""),
            site=data.get("site", ""),
        )
        return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def readiness(self, request, pk=None):
        """The live Work Readiness gate: per-category %, overall %, gate status,
        and what's blocking (COMPLIANCE §9)."""
        return Response(recompute_readiness(self.get_object()))

    @action(detail=True, methods=["get"])
    def compliance(self, request, pk=None):
        """The project's composed compliance checklist."""
        items = self.get_object().compliance_items.all()
        return Response(ComplianceItemSerializer(items, many=True).data)

    @action(detail=True, methods=["post"])
    def override(self, request, pk=None):
        """Authorised, audited passage past the gate (compliance.override)."""
        payload = OverrideSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        project = self.get_object()
        req = None
        if payload.validated_data.get("requirement"):
            req = get_object_or_404(
                ComplianceRequirement.objects.all(), id=payload.validated_data["requirement"]
            )
        try:
            override_gate(project, request.user, reason=payload.validated_data["reason"],
                          requirement=req)
        except ValueError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(recompute_readiness(project))

    # ── Execution/operations surface (Module 9) — execution services imported
    # lazily so the projects root stays free of an import-time execution dependency.
    @action(detail=True, methods=["get"])
    def health(self, request, pk=None):
        """Live composite project health (budget dimension Golden-Rule gated)."""
        from apps.execution.services import project_health
        return Response(project_health(self.get_object(), request.user))

    @action(detail=True, methods=["get"], url_path="progress-report")
    def progress_report(self, request, pk=None):
        """Daily progress report. ?audience=customer strips cost + internal issues."""
        from apps.execution.services import daily_progress_report
        audience = request.query_params.get("audience", "internal")
        return Response(daily_progress_report(self.get_object(), audience=audience,
                                              user=request.user))

    @action(detail=True, methods=["post"], url_path="capture-actuals")
    def capture_actuals(self, request, pk=None):
        """Push execution actuals into the estimate — closes the Module 7 loop."""
        from apps.execution.services import capture_project_actuals
        try:
            result = capture_project_actuals(self.get_object(), request.user)
        except ValueError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)

    # ── Commercial/finance surface (Module 10) — all money, so every endpoint
    # requires finance.view_money (Golden Rule). Finance services lazy-imported.
    def _need_money(self, request):
        return bool(request.user.has_perm_code("finance.view_money"))

    def _forbidden(self):
        return Response({"error": {"code": "forbidden", "message": "Need finance.view_money."}},
                        status=status.HTTP_403_FORBIDDEN)

    @action(detail=True, methods=["post"], url_path="create-budget")
    def create_budget(self, request, pk=None):
        """Create the budget baseline from the approved estimate (Module 10 §3)."""
        if not self._need_money(request):
            return self._forbidden()
        from apps.finance.services import create_budget_from_estimate
        budget = create_budget_from_estimate(self.get_object(), request.user)
        if budget is None:
            return Response({"error": {"code": "invalid",
                                       "message": "No approved estimate for this project."}},
                            status=status.HTTP_400_BAD_REQUEST)
        from apps.finance.services import budget_vs_actual
        return Response(budget_vs_actual(self.get_object()), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def budget(self, request, pk=None):
        """Live budget vs actual per category (rebuilds actuals from source first)."""
        if not self._need_money(request):
            return self._forbidden()
        from apps.finance.services import budget_vs_actual, rebuild_actuals_from_sources
        project = self.get_object()
        rebuild_actuals_from_sources(project, request.user)
        return Response(budget_vs_actual(project))

    @action(detail=True, methods=["get"])
    def profitability(self, request, pk=None):
        """Live profitability: revenue, actual cost, gross profit, margin, variance."""
        if not self._need_money(request):
            return self._forbidden()
        from apps.finance.services import profitability, rebuild_actuals_from_sources
        project = self.get_object()
        rebuild_actuals_from_sources(project, request.user)
        return Response(profitability(project))

    @action(detail=True, methods=["get"], url_path="profit-forecast")
    def profit_forecast(self, request, pk=None):
        """Project Profit Predictor — explainable final-outcome forecast (Module 10 §10)."""
        if not self._need_money(request):
            return self._forbidden()
        from apps.finance.services import profit_forecast, rebuild_actuals_from_sources
        project = self.get_object()
        rebuild_actuals_from_sources(project, request.user)
        return Response(profit_forecast(project))
