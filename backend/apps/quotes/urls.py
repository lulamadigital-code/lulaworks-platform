from rest_framework.routers import DefaultRouter

from .commercial_api import CommercialDocumentViewSet
from .views_api import QuotationViewSet

router = DefaultRouter()
router.register("quotations", QuotationViewSet, basename="quotation")
router.register("commercial-documents", CommercialDocumentViewSet,
                basename="commercial-document")

urlpatterns = router.urls
