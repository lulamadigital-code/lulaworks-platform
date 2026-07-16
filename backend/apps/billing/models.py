"""Billing & subscription (DATA_MODEL §7; SAAS_PLATFORM §2-7).

Plans are configurable DATA products (not hardcoded tiers). This is LulaWorks'
OWN SaaS revenue — separate from a tenant's project finance.
"""

from django.db import models

from apps.core.models import PlatformBaseModel


class Plan(PlatformBaseModel):
    """A subscription product. Entitlements drive feature flags + limits."""

    code = models.CharField(max_length=32, unique=True)  # starter, business, ...
    name = models.CharField(max_length=64)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_period = models.CharField(max_length=12, default="monthly")
    max_users = models.PositiveIntegerField(default=4)
    storage_quota_bytes = models.BigIntegerField(default=1_073_741_824)
    monthly_ai_credits = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    api_access = models.BooleanField(default=False)
    support_level = models.CharField(max_length=32, default="standard")
    module_entitlements = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return self.name


class SubscriptionStatus(models.TextChoices):
    TRIAL = "trial", "Trial"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class Subscription(PlatformBaseModel):
    """A company's subscription to a Plan (one active per company)."""

    company = models.OneToOneField(
        "identity.Company", on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.TRIAL
    )
    current_period_start = models.DateField(null=True, blank=True)
    current_period_end = models.DateField(null=True, blank=True)
    seats = models.PositiveIntegerField(default=1)
    overrides = models.JSONField(default=dict, blank=True)  # per-tenant limit overrides
    payfast_token = models.CharField(max_length=128, blank=True)

    def __str__(self):
        return f"{self.company} → {self.plan} ({self.status})"

    def limit(self, name: str, default=None):
        """Effective limit = subscription override → plan value → default."""
        if name in self.overrides:
            return self.overrides[name]
        return getattr(self.plan, name, default)
