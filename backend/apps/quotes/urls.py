from rest_framework.routers import DefaultRouter

from .commercial_api import CommercialDocumentViewSet
from .customer_po_api import CustomerPurchaseOrderViewSet
from .views_api import QuotationViewSet

router = DefaultRouter()
router.register("quotations", QuotationViewSet, basename="quotation")
router.register("commercial-documents", CommercialDocumentViewSet,
                basename="commercial-document")
router.register("customer-pos", CustomerPurchaseOrderViewSet,
                basename="customer-po")

urlpatterns = router.urls
