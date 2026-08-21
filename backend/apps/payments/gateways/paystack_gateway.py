"""Paystack adapter — a strong fit for Africa (NGN, GHS, ZAR, KES, USD).

Uses Paystack's hosted checkout (Initialize Transaction → authorization_url), so
raw card data never touches Lulaworks. Payment is confirmed server-side (verify
endpoint) and webhooks are authenticated with the HMAC-SHA512 signature Paystack
sends. Dependency-free (stdlib urllib). Activates when PAYSTACK_SECRET_KEY is set.

Go-live: a Paystack account, PAYSTACK_SECRET_KEY, and a webhook pointed at
/payments/webhook/paystack/ for the `charge.success` event. (V1 charges one
transaction per cycle; Paystack Subscriptions for auto-recurring can be layered
on later without touching billing logic.)
"""

import hashlib
import hmac
import json
import urllib.request
from decimal import Decimal

from django.conf import settings

from . import register
from .base import CheckoutSession, PaymentGateway, WebhookEvent

_API = "https://api.paystack.co"
_MINOR_UNIT = 100  # Paystack expects the smallest currency unit


def _billing_email(company) -> str:
    """Paystack needs a customer email (for its receipt). Use the company's
    owner/first active member; fall back to a deterministic address."""
    m = (company.memberships.filter(status="active")
         .select_related("user").order_by("joined_at").first())
    if m and m.user and m.user.email:
        return m.user.email
    return f"billing+{company.id}@lulaworks.app"


@register
class PaystackGateway(PaymentGateway):
    code = "paystack"
    label = "Card (Paystack)"

    @property
    def _secret(self):
        return getattr(settings, "PAYSTACK_SECRET_KEY", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._secret)

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            _API + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self._secret}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    def create_checkout(self, *, intent, success_url, cancel_url) -> CheckoutSession:
        amount_minor = int((Decimal(intent.amount) * _MINOR_UNIT).quantize(Decimal("1")))
        payload = {
            "email": _billing_email(intent.company),
            "amount": amount_minor,
            "currency": intent.currency,
            "reference": str(intent.id),
            "callback_url": success_url,
            "metadata": {"intent_ref": str(intent.id), "cancel_url": cancel_url},
        }
        data = self._call("POST", "/transaction/initialize", payload).get("data") or {}
        return CheckoutSession(url=data.get("authorization_url", ""),
                               external_id=data.get("reference", str(intent.id)))

    def confirm_payment(self, intent) -> bool:
        ref = intent.external_session_id or str(intent.id)
        data = self._call("GET", f"/transaction/verify/{ref}").get("data") or {}
        return data.get("status") == "success"

    def parse_webhook(self, payload: bytes, headers: dict) -> WebhookEvent:
        # Authenticate: HMAC-SHA512 of the raw body with the secret key.
        sent = headers.get("HTTP_X_PAYSTACK_SIGNATURE", "")
        expected = hmac.new(self._secret.encode(), payload, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(sent, expected):
            raise ValueError("invalid Paystack webhook signature")

        event = json.loads(payload.decode() or "{}")
        if event.get("event") == "charge.success":
            d = event.get("data") or {}
            ref = (d.get("metadata") or {}).get("intent_ref") or d.get("reference", "")
            return WebhookEvent(kind="checkout.completed", intent_ref=ref,
                                external_id=d.get("reference", ""), raw=event)
        return WebhookEvent(kind="ignored", raw=event)
