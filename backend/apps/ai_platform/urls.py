from django.urls import path
from rest_framework.routers import DefaultRouter

from .views_api import AIDashboardView, AIInteractionViewSet

router = DefaultRouter()
router.register("ai/interactions", AIInteractionViewSet, basename="ai-interaction")

urlpatterns = router.urls + [
    path("ai/dashboard/", AIDashboardView.as_view(), name="ai-dashboard"),
]
