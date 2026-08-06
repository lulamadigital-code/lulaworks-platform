from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("billing/checkout/<uuid:intent_id>/pay/", views.mock_checkout, name="mock_checkout"),
    path("billing/checkout/<uuid:intent_id>/return/", views.payment_return, name="return"),
    path("billing/checkout/<uuid:intent_id>/cancel/", views.payment_cancel, name="cancel"),
    # Provider webhooks, e.g. /payments/webhook/stripe/
    path("payments/webhook/<str:gateway>/", views.webhook, name="webhook"),
]
