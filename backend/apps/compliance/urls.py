from rest_framework.routers import DefaultRouter

from .views_api import ComplianceItemViewSet, ComplianceRequirementViewSet

router = DefaultRouter()
router.register("compliance-requirements", ComplianceRequirementViewSet,
                basename="compliance-requirement")
router.register("compliance-items", ComplianceItemViewSet, basename="compliance-item")

urlpatterns = router.urls
