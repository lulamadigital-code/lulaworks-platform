"""Payment records — provider-agnostic.

These sit between the billing module and whichever gateway is configured. A
CheckoutIntent captures *what* is being bought; the gateway captures *how* the
money moves. On a successful payment the intent is completed, which applies the
billing effect (activate subscription / add credits).
"""

from django.db import models

from apps.core.models import PlatformBaseModel


class PaymentCustomer(PlatformBaseModel):
    """Maps a company to a customer record in a specific provider (e.g. a Stripe
    customer id), so repeat payments reuse saved methods."""

    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="payment_customers"
    )
    gateway = models.CharField(max_length=32)
    external_customer_id = models.CharField(max_length=128)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "gateway"], name="unique_company_gateway_customer")
        ]

    def __str__(self):
        return f"{self.company} @ {self.gateway}"


class CheckoutIntent(PlatformBaseModel):
    """A pending purchase. Kind decides the billing effect applied on success."""

    class Kind(models.TextChoices):
        SUBSCRIPTION = "subscription", "Subscription"
        CREDIT_PACK = "credit_pack", "AI credit pack"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="checkout_intents"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    gateway = models.CharField(max_length=32)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)

    # What's being bought (one set is used depending on kind).
    plan_code = models.CharField(max_length=32, blank=True)
    billing_cycle = models.CharField(max_length=12, blank=True)
    pack_code = models.CharField(max_length=32, blank=True)

    currency = models.CharField(max_length=3, default="ZAR")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)

    external_session_id = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.company} · {self.currency} {self.amount} ({self.status})"


class GatewayEvent(PlatformBaseModel):
    """Every processed webhook, for idempotency + audit. A provider may deliver
    the same event more than once; we process each external id at most once."""

    gateway = models.CharField(max_length=32)
    external_event_id = models.CharField(max_length=128)
    kind = models.CharField(max_length=64, blank=True)
    processed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gateway", "external_event_id"], name="unique_gateway_event")
        ]

    def __str__(self):
        return f"{self.gateway}:{self.external_event_id}"
