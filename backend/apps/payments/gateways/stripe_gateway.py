"""Stripe adapter.

Uses Stripe Checkout (provider-hosted) so raw card data never touches Lulaworks.
Activates only when STRIPE_SECRET_KEY is configured; otherwise it's inert and the
registry falls back to the mock gateway. The `stripe` SDK is imported lazily so
it's an optional dependency.

Going live needs: a Stripe account (verify availability for your legal entity's
country), STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, and a webhook pointed at
/payments/webhook/stripe/ for the `checkout.session.completed` event.
"""

from decimal import Decimal

from django.conf import settings

from . import register
from .base import CheckoutSession, PaymentGateway, WebhookEvent

# Stripe expects the smallest currency unit (cents). Zero-decimal currencies
# (JPY, etc.) are the exception; the ones we price in are all 2-decimal.
_MINOR_UNIT = 100
_INTERVAL = {"monthly": "month", "annual": "year"}


@register
class StripeGateway(PaymentGateway):
    code = "stripe"
    label = "Card (Stripe)"

    @property
    def _secret(self):
        return getattr(settings, "STRIPE_SECRET_KEY", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._secret)

    def _client(self):
        import stripe  # lazy: optional dependency
        stripe.api_key = self._secret
        return stripe

    def create_checkout(self, *, intent, success_url, cancel_url) -> CheckoutSession:
        stripe = self._client()
        amount_minor = int((Decimal(intent.amount) * _MINOR_UNIT).quantize(Decimal("1")))
        currency = intent.currency.lower()

        if intent.kind == "subscription":
            line_item = {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": f"Lulaworks {intent.plan_code.title()}"},
                    "unit_amount": amount_minor,
                    "recurring": {"interval": _INTERVAL.get(intent.billing_cycle, "month")},
                },
                "quantity": 1,
            }
            mode = "subscription"
        else:  # credit_pack — one-off payment
            line_item = {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": intent.description or "Lulaworks AI credits"},
                    "unit_amount": amount_minor,
                },
                "quantity": 1,
            }
            mode = "payment"

        session = stripe.checkout.Session.create(
            mode=mode,
            line_items=[line_item],
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(intent.id),
            metadata={"intent_ref": str(intent.id)},
        )
        return CheckoutSession(url=session.url, external_id=session.id)

    def confirm_payment(self, intent) -> bool:
        """Server-side check that the Checkout Session was actually paid."""
        if not intent.external_session_id:
            return False
        stripe = self._client()
        session = stripe.checkout.Session.retrieve(intent.external_session_id)
        return session.get("payment_status") == "paid"

    def parse_webhook(self, payload: bytes, headers: dict) -> WebhookEvent:
        stripe = self._client()
        secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
        sig = headers.get("HTTP_STRIPE_SIGNATURE", "")
        # Raises SignatureVerificationError on tampering → caller returns 400.
        event = stripe.Webhook.construct_event(payload, sig, secret)

        if event["type"] == "checkout.session.completed":
            obj = event["data"]["object"]
            return WebhookEvent(
                kind="checkout.completed",
                intent_ref=(obj.get("metadata") or {}).get("intent_ref", "")
                or obj.get("client_reference_id", ""),
                external_id=event["id"],
                raw=event,
            )
        return WebhookEvent(kind="ignored", external_id=event["id"], raw=event)

    def customer_portal_url(self, *, company, return_url):
        from apps.payments.models import PaymentCustomer
        pc = PaymentCustomer.objects.filter(company=company, gateway=self.code).first()
        if pc is None:
            return None
        stripe = self._client()
        session = stripe.billing_portal.Session.create(
            customer=pc.external_customer_id, return_url=return_url
        )
        return session.url
