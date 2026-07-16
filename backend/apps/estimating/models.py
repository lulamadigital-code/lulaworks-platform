"""Estimating & Quotation Intelligence (ESTIMATING.md / Module 7).

⚠️ THE INTERNAL/EXTERNAL SPLIT (Financial Golden Rule at the document boundary):
  • Estimate  (this app) — INTERNAL: full cost build-up, supplier costs, markup,
    margin, risk. Money Golden-Rule gated.
  • Quotation (quotes app) — EXTERNAL: selling price only. Never exposes cost,
    markup or margin. Derived from an APPROVED Estimate (see services.generate_quotation).

An Estimate is versioned: revisions are never overwritten — a new version is
created and the prior one is marked SUPERSEDED (audit-permanent history).
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel

TWO = Decimal("0.01")


class CostCategory(models.TextChoices):
    LABOUR = "labour", "Labour"
    MATERIAL = "material", "Material"
    EQUIPMENT = "equipment", "Equipment"
    SUBCONTRACTOR = "subcontractor", "Subcontractor"
    INDIRECT = "indirect", "Indirect"


class EstimateStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW = "review", "Internal review"
    AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"


class LineSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    LEDGER = "ledger", "Price ledger"       # deterministic, from Procurement §10
    HISTORICAL = "historical", "Historical"  # calibrated from past actuals
    AI = "ai", "AI"


class Estimate(TenantBaseModel):
    """Internal, versioned cost estimate. Everything money here is Golden-Rule gated."""

    number = models.CharField(max_length=32)
    quotation = models.ForeignKey(
        "quotes.Quotation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="estimates",
    )
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="revisions"
    )
    version = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=255, blank=True)
    client_name = models.CharField(max_length=255)
    work_type = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=20, choices=EstimateStatus.choices, default=EstimateStatus.DRAFT
    )

    # Commercial dials (all cost-side — Golden-Rule gated at the serializer).
    contingency_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    markup_pct = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # Risk (Module 7 §5) — score 0-100, higher = riskier.
    risk_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    risk_flags = models.JSONField(default=list, blank=True)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    revision_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Same number across revisions; each (number, version) pair is unique.
            models.UniqueConstraint(
                fields=["company", "number", "version"], name="unique_estimate_number_version"
            )
        ]

    def __str__(self):
        return f"{self.number} v{self.version}"

    # ── Cost build-up (all internal) ────────────────────────────────────────
    @property
    def direct_cost(self) -> Decimal:
        return sum((s.subtotal for s in self.sections.all()), Decimal("0.00"))

    @property
    def contingency_amount(self) -> Decimal:
        return (self.direct_cost * Decimal(self.contingency_pct) / 100).quantize(TWO)

    @property
    def total_cost(self) -> Decimal:
        return (self.direct_cost + self.contingency_amount).quantize(TWO)

    # ── Price derivation (markup then discount) ─────────────────────────────
    @property
    def price_before_discount(self) -> Decimal:
        return (self.total_cost * (1 + Decimal(self.markup_pct) / 100)).quantize(TWO)

    @property
    def selling_price(self) -> Decimal:
        return (self.price_before_discount * (1 - Decimal(self.discount_pct) / 100)).quantize(TWO)

    @property
    def margin_amount(self) -> Decimal:
        return (self.selling_price - self.total_cost).quantize(TWO)

    @property
    def margin_pct(self) -> Decimal:
        """Effective margin = profit / selling price (after discount)."""
        price = self.selling_price
        if not price:
            return Decimal("0.00")
        return (self.margin_amount / price * 100).quantize(TWO)


class EstimateSection(TenantBaseModel):
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name="sections")
    category = models.CharField(max_length=16, choices=CostCategory.choices)
    name = models.CharField(max_length=120, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["estimate", "position"]

    def __str__(self):
        return f"{self.get_category_display()}"

    @property
    def subtotal(self) -> Decimal:
        return sum((line.line_cost for line in self.lines.all()), Decimal("0.00"))


class EstimateLine(TenantBaseModel):
    """A single cost build-up line. Carries its provenance + confidence so the
    estimator can see *why* it was proposed (Confidence-Engine pattern)."""

    section = models.ForeignKey(EstimateSection, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    unit = models.CharField(max_length=32, default="each")
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    source = models.CharField(max_length=16, choices=LineSource.choices, default=LineSource.MANUAL)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=0)  # 0-1
    source_ref = models.CharField(max_length=255, blank=True)  # "historical avg 118h", supplier…
    lead_time_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["section", "position"]

    def __str__(self):
        return self.description[:60]

    @property
    def line_cost(self) -> Decimal:
        return (self.qty * self.unit_cost).quantize(TWO)


class EstimateActual(TenantBaseModel):
    """Pricing-Intelligence loop (Module 7 §10): actuals captured at execution/
    closeout, per cost category, so estimate-vs-actual variance can calibrate
    future estimates. Tenant-private."""

    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name="actuals")
    category = models.CharField(max_length=16, choices=CostCategory.choices)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2)
    actual_cost = models.DecimalField(max_digits=14, decimal_places=2)
    source = models.CharField(max_length=40, blank=True)  # supplier_invoice | timesheet | …
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["estimate", "category"]

    def __str__(self):
        return f"{self.category}: est {self.estimated_cost} / act {self.actual_cost}"

    @property
    def variance(self) -> Decimal:
        return (self.actual_cost - self.estimated_cost).quantize(TWO)

    @property
    def variance_pct(self) -> Decimal:
        if not self.estimated_cost:
            return Decimal("0.00")
        return (self.variance / self.estimated_cost * 100).quantize(TWO)
