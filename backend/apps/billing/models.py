"""Billing & subscription (DATA_MODEL §7; SAAS_PLATFORM §2-7).

Plans are configurable DATA products (not hardcoded tiers). This is LulaWorks'
OWN SaaS revenue — separate from a tenant's project finance.
"""

from django.db import models

from apps.core.models import PlatformBaseModel


class Plan(PlatformBaseModel):
    """A subscription product. Entitlements drive feature flags + limits.

    Plans are DATA, not hardcoded tiers, so new plans (incl. a future Enterprise
    tier) drop in without touching the billing engine — the code only ever reads
    `tier` for ordering/comparison and `module_entitlements` for gating."""

    code = models.CharField(max_length=32, unique=True)  # starter, professional, business
    name = models.CharField(max_length=64)
    # Pricing currency. V1 charges in ZAR, but the field is here so plans can be
    # priced in USD/EUR/GBP/AUD/… as LulaWorks expands, with no schema change.
    currency = models.CharField(max_length=3, default="ZAR")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)          # monthly
    annual_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)   # yearly (~2 months free)
    billing_period = models.CharField(max_length=12, default="monthly")
    # Ordinal rank for upgrade/downgrade detection and display order. Higher =
    # bigger plan. Leaves gaps so an Enterprise tier can slot in later.
    tier = models.PositiveSmallIntegerField(default=0)
    is_popular = models.BooleanField(default=False)  # the "Most Popular" card
    max_users = models.PositiveIntegerField(default=4)
    storage_quota_bytes = models.BigIntegerField(default=1_073_741_824)
    monthly_ai_credits = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    api_access = models.BooleanField(default=False)
    support_level = models.CharField(max_length=32, default="standard")
    module_entitlements = models.JSONField(default=list, blank=True)   # gating keys
    features = models.JSONField(default=list, blank=True)              # human-readable, for the comparison UI
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["tier", "price"]

    def __str__(self):
        return self.name

    #: Display symbols for the currencies we're built to support. Amounts are
    #: always stored as Decimal; only presentation differs by currency.
    CURRENCY_SYMBOLS = {"ZAR": "R", "USD": "$", "EUR": "€", "GBP": "£", "AUD": "A$"}

    @property
    def currency_symbol(self) -> str:
        return self.CURRENCY_SYMBOLS.get(self.currency, self.currency + " ")

    def price_for(self, cycle: str):
        """Headline price for a billing cycle ('monthly' | 'annual')."""
        return self.annual_price if cycle == "annual" else self.price

    @property
    def annual_saving(self):
        """What annual billing saves vs 12 monthly payments (≈ two months)."""
        return (self.price * 12) - self.annual_price


class SubscriptionStatus(models.TextChoices):
    TRIAL = "trial", "Trial"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class BillingCycle(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    ANNUAL = "annual", "Annual"


class Subscription(PlatformBaseModel):
    """A company's subscription to a Plan (one active per company)."""

    company = models.OneToOneField(
        "identity.Company", on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.TRIAL
    )
    billing_cycle = models.CharField(
        max_length=12, choices=BillingCycle.choices, default=BillingCycle.MONTHLY
    )
    current_period_start = models.DateField(null=True, blank=True)
    current_period_end = models.DateField(null=True, blank=True)
    seats = models.PositiveIntegerField(default=1)
    # Set when usage exceeds the plan's limits after a downgrade: data stays
    # accessible, but creating new users is blocked until back within limit.
    is_over_limit = models.BooleanField(default=False)
    # Cancellation is graceful — access continues until current_period_end.
    cancel_at_period_end = models.BooleanField(default=False)
    overrides = models.JSONField(default=dict, blank=True)  # per-tenant limit overrides
    payfast_token = models.CharField(max_length=128, blank=True)

    def __str__(self):
        return f"{self.company} → {self.plan} ({self.status})"

    def limit(self, name: str, default=None):
        """Effective limit = subscription override → plan value → default."""
        if name in self.overrides:
            return self.overrides[name]
        return getattr(self.plan, name, default)

    @property
    def is_trialing(self) -> bool:
        return self.status == SubscriptionStatus.TRIAL

    @property
    def price(self):
        """What this company pays per cycle right now."""
        return self.plan.price_for(self.billing_cycle)


class CreditPack(PlatformBaseModel):
    """A one-off AI-credit top-up product. Bought on demand, added immediately."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    credits = models.DecimalField(max_digits=12, decimal_places=2)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return f"{self.name} ({self.credits} credits)"


class BillingTransaction(PlatformBaseModel):
    """Append-only SaaS billing history for a company: trial start, plan changes,
    renewals, credit-pack purchases, cancellations. This is LulaWorks' revenue
    record — distinct from a tenant's own customer invoices in apps.finance."""

    class Kind(models.TextChoices):
        TRIAL_STARTED = "trial_started", "Trial started"
        UPGRADE = "upgrade", "Plan upgraded"
        DOWNGRADE = "downgrade", "Plan downgraded"
        PLAN_CHANGE = "plan_change", "Plan changed"
        RENEWAL = "renewal", "Renewal"
        CREDIT_PACK = "credit_pack", "AI credit pack"
        CANCELLATION = "cancellation", "Cancelled"

    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="billing_transactions"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)   # ZAR charged
    credits = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # credits granted
    plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.company} · R{self.amount}"
