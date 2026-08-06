"""Mock gateway — the safe offline default.

No external calls, no keys. It sends the customer to a local page that simulates
a provider's hosted checkout (Pay / Cancel). This keeps the *exact same flow* as
a real gateway (checkout → success → intent completed → billing applied), so
switching to Stripe changes only the redirect target and the webhook trigger —
never the subscription logic. Ideal for dev, demos and tests.
"""

from django.urls import reverse

from . import register
from .base import CheckoutSession, PaymentGateway, WebhookEvent


@register
class MockGateway(PaymentGateway):
    code = "mock"
    label = "Test (no real charge)"

    def create_checkout(self, *, intent, success_url, cancel_url) -> CheckoutSession:
        # A local page that stands in for the provider's hosted checkout.
        url = reverse("payments:mock_checkout", args=[intent.id])
        return CheckoutSession(url=url, external_id=f"mock_{intent.id}")

    def confirm_payment(self, intent) -> bool:
        # Test gateway: reaching the return page means the tester clicked "Pay".
        return True

    def parse_webhook(self, payload: bytes, headers: dict) -> WebhookEvent:
        # The mock flow completes via the local checkout page, not a webhook.
        return WebhookEvent(kind="ignored")
