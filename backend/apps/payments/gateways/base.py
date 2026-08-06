"""Payment gateway abstraction.

The billing module talks to a PaymentGateway, never to a specific provider. A
provider (Stripe, PayFast, Paystack, Peach, Flutterwave, …) implements this
interface; adding one is a new subclass + registry entry, with zero change to
subscription/billing logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CheckoutSession:
    """Where to send the customer to pay. `url` is a provider-hosted page (so raw
    card data never touches LulaWorks). `external_id` is the provider's session id."""
    url: str
    external_id: str = ""


@dataclass
class WebhookEvent:
    """A provider webhook, normalised. `intent_ref` is our CheckoutIntent id,
    carried through the provider in metadata so we can reconcile the payment."""
    kind: str                     # normalised: "checkout.completed" | "checkout.cancelled" | "ignored"
    intent_ref: str = ""
    external_id: str = ""
    raw: dict = field(default_factory=dict)


class PaymentGateway(ABC):
    """Interface every payment provider implements."""

    code = "base"          # unique provider key, e.g. "stripe", "payfast"
    label = "Base"

    @property
    def is_configured(self) -> bool:
        """True when this provider has the credentials it needs to run live."""
        return True

    @abstractmethod
    def create_checkout(self, *, intent, success_url: str, cancel_url: str) -> CheckoutSession:
        """Start a hosted payment for a CheckoutIntent (subscription or credit
        pack) and return where to send the customer."""

    @abstractmethod
    def parse_webhook(self, payload: bytes, headers: dict) -> WebhookEvent:
        """Verify + normalise an incoming provider webhook into a WebhookEvent.
        Must raise on an invalid signature."""

    def confirm_payment(self, intent) -> bool:
        """SECURITY: has this intent actually been paid, per the provider?

        The success/return URL is user-reachable and must never be trusted on its
        own to grant a subscription. The return view calls this to verify payment
        server-side with the provider before activating anything; the signed
        webhook is the other authoritative path. Default is False (deny) so an
        unimplemented provider can never accidentally grant access."""
        return False

    def customer_portal_url(self, *, company, return_url: str):
        """Optional: a provider-hosted portal for managing payment methods /
        cancelling. Providers that don't support one return None."""
        return None
