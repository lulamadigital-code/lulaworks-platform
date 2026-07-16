from rest_framework.routers import DefaultRouter

from .views_api import EstimateViewSet

router = DefaultRouter()
router.register("estimates", EstimateViewSet, basename="estimate")

urlpatterns = router.urls
