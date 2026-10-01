from django.urls import path

from .api import BillingSummaryView

urlpatterns = [
    path("billing/summary/", BillingSummaryView.as_view(), name="billing-summary"),
]
