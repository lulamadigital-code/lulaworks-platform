from rest_framework.routers import DefaultRouter

from .views_api import QuotationViewSet

router = DefaultRouter()
router.register("quotations", QuotationViewSet, basename="quotation")

urlpatterns = router.urls
