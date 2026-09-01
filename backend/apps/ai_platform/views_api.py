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
    """LulaAI, the AI Operations Director. Ask produces ONE consolidated draft;
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
                                  project=project, quotation=quotation, enrich=data.get("enrich"))
        return Response(AIInteractionSerializer(interaction).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def decision(self, request, pk=None):
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        interaction = record_decision(self.get_object(), request.user,
                                      approved=payload.validated_data["approved"])
        return Response(AIInteractionSerializer(interaction).data)


class AssistantView(APIView):
    """LulaAI assistant (redesign) over REST — the same grounded, permission-
    checked brain the web console uses (apps.ai_platform.assistant), so mobile
    reaches parity: grounded answers, draft→confirm writes, and the daily brief.

    POST /ai/assistant/ask/     {message, ctx_type?, ctx_id?}  → answer|draft
    POST /ai/assistant/execute/ {action, ...fields}           → confirmed write
    GET  /ai/assistant/brief/                                  → daily briefing
    """

    permission_classes = [IsAuthenticated]

    # Confirmed-write fields, whitelisted per action (mirrors the web view).
    _EXEC_FIELDS = {
        "create_task": ("title", "assignee", "due", "notes"),
        "send_customer_email": ("to", "subject", "body", "customer_id"),
        "send_whatsapp_text": ("phone", "text"),
    }

    def _guard(self, request):
        set_tenant_from_request(request)
        if not request.user.has_perm_code("ai.generate"):
            return Response(
                {"error": {"code": "forbidden", "message": "AI features require the "
                           "ai.generate permission."}},
                status=status.HTTP_403_FORBIDDEN)
        return None

    def post(self, request, mode=None):
        denied = self._guard(request)
        if denied:
            return denied
        from .assistant import ask, execute
        company, user = request.user.active_company, request.user

        if mode == "execute":
            action = (request.data.get("action") or "").strip()
            fields = self._EXEC_FIELDS.get(action)
            if not fields:
                return Response({"error": {"code": "bad_action",
                                 "message": "Unknown action."}},
                                status=status.HTTP_400_BAD_REQUEST)
            params = {k: (str(request.data.get(k) or "")).strip() for k in fields}
            return Response(execute(company, user, action, params))

        # default: ask
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response({"error": {"code": "empty", "message": "Ask a question."}},
                            status=status.HTTP_400_BAD_REQUEST)
        ctx = None
        ct, cid = request.data.get("ctx_type"), request.data.get("ctx_id")
        if ct and cid:
            ctx = {"type": ct, "id": cid}
        return Response(ask(company, user, message, context=ctx))

    def get(self, request, mode=None):
        denied = self._guard(request)
        if denied:
            return denied
        from .briefing import daily_brief
        return Response(daily_brief(request.user.active_company, request.user))


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
