"""Support ticket JSON API (mobile Help & Support)."""
from rest_framework.routers import DefaultRouter

from .views_api import SupportTicketViewSet

router = DefaultRouter()
router.register("support-tickets", SupportTicketViewSet, basename="support-ticket")

urlpatterns = router.urls
