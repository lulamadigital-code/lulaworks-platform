from django.urls import path
from rest_framework.routers import DefaultRouter

from .views_api import (AIContextView, AIDashboardView, AIInteractionViewSet,
                        AIPredictionsView, AssistantView)

router = DefaultRouter()
router.register("ai/interactions", AIInteractionViewSet, basename="ai-interaction")

urlpatterns = router.urls + [
    path("ai/dashboard/", AIDashboardView.as_view(), name="ai-dashboard"),
    # LulaAI assistant (redesign) — mobile parity with the web console.
    path("ai/assistant/ask/", AssistantView.as_view(), name="ai-assistant-ask"),
    path("ai/assistant/execute/", AssistantView.as_view(), {"mode": "execute"},
         name="ai-assistant-execute"),
    path("ai/assistant/brief/", AssistantView.as_view(), name="ai-assistant-brief"),
    # AI Context Engine — grounded, permission-scoped context for one record.
    path("ai/context/<str:kind>/<uuid:pk>/", AIContextView.as_view(),
         name="ai-context"),
    path("ai/predictions/", AIPredictionsView.as_view(), name="ai-predictions"),
]
