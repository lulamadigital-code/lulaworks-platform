from django.urls import path
from rest_framework.routers import DefaultRouter

from .views_api import (
    CommercialDashboardView,
    CostCodeViewSet,
    CostEntryViewSet,
    InvoiceViewSet,
    VariationViewSet,
)

router = DefaultRouter()
router.register("cost-codes", CostCodeViewSet, basename="cost-code")
router.register("cost-entries", CostEntryViewSet, basename="cost-entry")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("variations", VariationViewSet, basename="variation")

urlpatterns = router.urls + [
    path("finance/commercial-dashboard/", CommercialDashboardView.as_view(),
         name="commercial-dashboard"),
]
