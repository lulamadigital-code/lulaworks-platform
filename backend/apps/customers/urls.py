from rest_framework.routers import DefaultRouter

from .views_api import (
    ActivityViewSet,
    CustomerContactViewSet,
    CustomerViewSet,
    LeadViewSet,
    OpportunityViewSet,
)

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("customer-contacts", CustomerContactViewSet, basename="customer-contact")
router.register("leads", LeadViewSet, basename="lead")
router.register("opportunities", OpportunityViewSet, basename="opportunity")
router.register("activities", ActivityViewSet, basename="activity")

urlpatterns = router.urls
