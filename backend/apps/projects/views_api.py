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
