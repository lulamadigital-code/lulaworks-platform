from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import ComplianceItem, ComplianceRequirement
from .serializers import (
    ApproveSerializer,
    ComplianceItemSerializer,
    ComplianceRequirementSerializer,
    RejectSerializer,
)
from .services import approve_item, reject_item, submit_item


class ComplianceRequirementViewSet(TenantViewSet):
    """The per-tenant requirement library the discovery engine composes from."""

    model = ComplianceRequirement
    serializer_class = ComplianceRequirementSerializer
    search_fields = ["code", "name", "category"]
    required_perms = {
        "create": "compliance.manage",
        "update": "compliance.manage",
        "partial_update": "compliance.manage",
        "destroy": "compliance.manage",
    }

    def get_queryset(self):
        return ComplianceRequirement.objects.all()


class ComplianceItemViewSet(TenantViewSet):
    """Per-project compliance items. Approving/rejecting recomputes the readiness
    gate live (COMPLIANCE §8-9). Filter by ?project=<id>."""

    model = ComplianceItem
    serializer_class = ComplianceItemSerializer
    search_fields = ["name", "category", "status"]
    required_perms = {
        "submit": "compliance.manage",
        "approve": "compliance.override",   # approving compliance is a controlled act
        "reject": "compliance.override",
    }

    def get_queryset(self):
        qs = ComplianceItem.objects.all().select_related("project")
        project = self.request.query_params.get("project")
        return qs.filter(project_id=project) if project else qs

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        item = submit_item(
            self.get_object(), request.user,
            valid_from=request.data.get("valid_from"), expiry=request.data.get("expiry"),
        )
        return Response(ComplianceItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        payload = ApproveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = approve_item(self.get_object(), request.user, **payload.validated_data)
        return Response(ComplianceItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        payload = RejectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = reject_item(self.get_object(), request.user,
                           reason=payload.validated_data.get("reason", ""))
        return Response(ComplianceItemSerializer(item).data)
