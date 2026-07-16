from rest_framework.routers import DefaultRouter

from .views_api import RFQViewSet

router = DefaultRouter()
router.register("rfqs", RFQViewSet, basename="rfq")

urlpatterns = router.urls
