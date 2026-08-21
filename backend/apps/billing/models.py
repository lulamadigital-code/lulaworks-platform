"""Billing & subscription (DATA_MODEL §7; SAAS_PLATFORM §2-7).

Plans are configurable DATA products (not hardcoded tiers). This is Lulaworks'
OWN SaaS revenue — separate from a tenant's project finance.
"""

from django.db import models

from apps.core.models import PlatformBaseModel

# Currencies Lulaworks is built to price in. Amounts are stored as Decimal; only
# the display symbol differs. Order here is the display order in the UI selector.
CURRENCY_SYMBOLS = {"ZAR": "R", "USD": "$", "EUR": "€", "GBP": "£", "AUD": "A$"}
SUPPORTED_CURRENCIES = list(CURRENCY_SYMBOLS)


def currency_symbol(code: str) -> str:
    return CURRENCY_SYMBOLS.get(code, (code or "") + " ")


class Plan(PlatformBaseModel):
    """A subscription product. Entitlements drive feature flags + limits.

    Plans are DATA, not hardcoded tiers, so new plans (incl. a future Enterprise
    tier) drop in without touching the billing engine — the code only ever reads
    `tier` for ordering/comparison and `module_entitlements` for gating."""

    code = models.CharField(max_length=32, unique=True)  # starter, professional, business
    name = models.CharField(max_length=64)
    # Pricing currency. V1 charges in ZAR, but the field is here so plans can be
    # priced in USD/EUR/GBP/AUD/… as Lulaworks expands, with no schema change.
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

    @property
    def currency_symbol(self) -> str:
        return currency_symbol(self.currency)

    def symbol_for(self, currency: str = None) -> str:
        return currency_symbol(currency or self.currency)

    def price_for(self, cycle: str):
        """Base-currency headline price for a billing cycle ('monthly'|'annual')."""
        return self.annual_price if cycle == "annual" else self.price

    def price_in(self, currency: str, cycle: str = "monthly"):
        """Price for a given currency + cycle. Falls back to the plan's base
        currency price when no explicit price exists for `currency`."""
        if currency and currency != self.currency:
            pp = self.prices.filter(currency=currency).first()
            if pp is not None:
                return pp.annual if cycle == "annual" else pp.monthly
        return self.price_for(cycle)

    def annual_saving_in(self, currency: str):
        """Annual saving (≈ two months) in the given currency."""
        return (self.price_in(currency, "monthly") * 12) - self.price_in(currency, "annual")

    @property
    def available_currencies(self):
        """Currencies this plan is priced in (base + any PlanPrice), in display order."""
        priced = {self.currency} | set(self.prices.values_list("currency", flat=True))
        return [c for c in SUPPORTED_CURRENCIES if c in priced]

    @property
    def annual_saving(self):
        """What annual billing saves vs 12 monthly payments (≈ two months)."""
        return (self.price * 12) - self.annual_price


class PlanPrice(PlatformBaseModel):
    """A plan's price in one currency. One Plan (its limits/features/tier) can
    carry many prices — so Lulaworks lists local prices per region without
    duplicating the plan. The plan's own price/annual_price is the base currency."""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="prices")
    currency = models.CharField(max_length=3)
    monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    annual = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["plan", "currency"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "currency"], name="unique_plan_currency")
        ]

    def __str__(self):
        return f"{self.plan.code} {self.currency} {self.monthly}"


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
    # The currency this company is billed in (set from Company.currency on
    # subscribe). Amounts come from the plan's PlanPrice for this currency.
    currency = models.CharField(max_length=3, default="ZAR")
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
        """What this company pays per cycle right now, in its billing currency."""
        return self.plan.price_in(self.currency, self.billing_cycle)

    @property
    def currency_symbol(self) -> str:
        return currency_symbol(self.currency)


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
    renewals, credit-pack purchases, cancellations. This is Lulaworks' revenue
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
