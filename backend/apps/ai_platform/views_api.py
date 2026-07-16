from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api import TenantViewSet
from apps.core.middleware import set_tenant_from_request

from .gateway import credit_balance
from .models import AIInteraction, ApprovalStatus
from .orchestrator import orchestrate, record_decision
from .serializers import AIInteractionSerializer, AskSerializer, DecisionSerializer
from .tools import available_tools


class AIInteractionViewSet(TenantViewSet):
    """Lulama, the AI Operations Director. Ask produces ONE consolidated draft;
    decision records human acceptance/rejection (never executes side-effects)."""

    model = AIInteraction
    serializer_class = AIInteractionSerializer
    required_perms = {"ask": "ai.generate", "decision": "ai.generate"}

    def get_queryset(self):
        return AIInteraction.objects.all()

    @action(detail=False, methods=["post"])
    def ask(self, request):
        from apps.projects.models import Project
        from apps.quotes.models import Quotation
        payload = AskSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        project = quotation = None
        if data.get("project"):
            project = get_object_or_404(Project.objects.all(), id=data["project"])
        if data.get("quotation"):
            quotation = get_object_or_404(Quotation.objects.all(), id=data["quotation"])
        interaction = orchestrate(request.user.active_company, request.user, data["request"],
                                  project=project, quotation=quotation)
        return Response(AIInteractionSerializer(interaction).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def decision(self, request, pk=None):
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        interaction = record_decision(self.get_object(), request.user,
                                      approved=payload.validated_data["approved"])
        return Response(AIInteractionSerializer(interaction).data)


class AIDashboardView(APIView):
    """AI operations dashboard (AI_PLATFORM §10): credits, agent activity, recent
    drafts + decisions, and the tools this user is permitted to invoke."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        set_tenant_from_request(request)
        if not request.user.has_perm_code("ai.generate"):
            return Response({"error": {"code": "forbidden", "message": "Need ai.generate."}},
                            status=status.HTTP_403_FORBIDDEN)
        company = request.user.active_company
        qs = AIInteraction.objects.all()
        return Response({
            "credits_remaining": str(credit_balance(company)),
            "interactions": qs.count(),
            "awaiting_review": qs.filter(approval_status=ApprovalStatus.DRAFT).count(),
            "approved": qs.filter(approval_status=ApprovalStatus.APPROVED).count(),
            "rejected": qs.filter(approval_status=ApprovalStatus.REJECTED).count(),
            "available_tools": available_tools(request.user),
        })
