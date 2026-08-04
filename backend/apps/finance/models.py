"""Finance, Commercial & Payment engine (FINANCE.md / Module 10).

Manages the commercial lifecycle of every project — first cost to last payment —
so managers always know whether a project is making or losing money.

⚠️ BOUNDARY (owner instruction, §1): this is *operational* project finance
(budgets, costs, margins, claims, project profitability). It is NOT a general
ledger and does not replace Sage/Xero/QuickBooks — no chart-of-accounts, no trial
balance, no tax submissions. It integrates with them later via `integrations`.

All money here is Golden-Rule gated (admin/manager only).
"""

from decimal import Decimal

from django.db import models

from apps.core.models import TenantBaseModel

TWO = Decimal("0.01")


class CostCategory(models.TextChoices):
    LABOUR = "labour", "Labour"
    MATERIAL = "material", "Material"
    EQUIPMENT = "equipment", "Equipment"
    SUBCONTRACTOR = "subcontractor", "Subcontractor"
    TRAVEL = "travel", "Travel"
    ACCOMMODATION = "accommodation", "Accommodation"
    CONTINGENCY = "contingency", "Contingency"
    OTHER = "other", "Other"


class CostCode(TenantBaseModel):
    """Hierarchical, company-configurable cost coding (e.g. 1000 Labour → 1100
    Mechanical). Every cost line may carry one, for coded, contextual money."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=16, choices=CostCategory.choices)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_cost_code")
        ]

    def __str__(self):
        return f"{self.code} {self.name}"


class ProjectBudget(TenantBaseModel):
    """The financial baseline, created from the approved Estimate on award. Every
    actual is measured against it."""

    project = models.OneToOneField(
        "projects.Project", on_delete=models.CASCADE, related_name="budget"
    )
    source_estimate = models.ForeignKey(
        "estimating.Estimate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    revenue = models.DecimalField(max_digits=14, decimal_places=2, default=0)      # selling price
    expected_margin_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Budget {self.project}"

    @property
    def total_cost_budget(self) -> Decimal:
        return sum((line.amount for line in self.lines.all()), Decimal("0.00"))


class BudgetLine(TenantBaseModel):
    budget = models.ForeignKey(ProjectBudget, on_delete=models.CASCADE, related_name="lines")
    category = models.CharField(max_length=16, choices=CostCategory.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["budget", "category"]

    def __str__(self):
        return f"{self.category}: {self.amount}"


class CostSource(models.TextChoices):
    TIMESHEETS = "timesheets", "Timesheets (labour)"
    SUPPLIER_INVOICES = "supplier_invoices", "Supplier invoices (material)"
    FIELD_REPORTS = "field_reports", "Field reports (WES)"
    EXPENSE = "expense", "Expense"
    VARIATION = "variation", "Variation"
    MANUAL = "manual", "Manual adjustment"


class CostEntry(TenantBaseModel):
    """The convergence point — actual costs from every module land here, coded by
    project (+ optional cost code / work package). Auto-sourced entries are keyed
    by (project, category, source) so re-sync upserts rather than duplicates."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="cost_entries"
    )
    cost_code = models.ForeignKey(
        CostCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    work_package = models.ForeignKey(
        "execution.WorkPackage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    category = models.CharField(max_length=16, choices=CostCategory.choices)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    source = models.CharField(max_length=20, choices=CostSource.choices,
                              default=CostSource.MANUAL)
    source_ref = models.CharField(max_length=120, blank=True)
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "project", "category"])]

    def __str__(self):
        return f"{self.category} {self.amount} ({self.source})"


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    PARTIALLY_PAID = "partially_paid", "Partially paid"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"


class Invoice(TenantBaseModel):
    """Customer invoice / progress claim. Sending is human-approved (nothing
    auto-sends). Retention is first-class (mining/construction)."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="invoices"
    )
    number = models.CharField(max_length=32)
    client_name = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=InvoiceStatus.choices,
                              default=InvoiceStatus.DRAFT)
    is_progress_claim = models.BooleanField(default=False)
    percent_complete = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("15.00"))
    retention_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    retention_released = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_invoice_number")
        ]

    def __str__(self):
        return self.number

    @property
    def subtotal(self) -> Decimal:
        return sum((ln.line_total for ln in self.lines.all()), Decimal("0.00"))

    @property
    def vat_amount(self) -> Decimal:
        return (self.subtotal * self.vat_rate / 100).quantize(TWO)

    @property
    def retention_amount(self) -> Decimal:
        return (self.subtotal * self.retention_pct / 100).quantize(TWO)

    @property
    def total(self) -> Decimal:
        """Payable now = subtotal + VAT − retention held back."""
        return (self.subtotal + self.vat_amount - self.retention_amount).quantize(TWO)

    @property
    def paid(self) -> Decimal:
        return sum((p.amount for p in self.payments.all()), Decimal("0.00"))

    @property
    def outstanding(self) -> Decimal:
        return (self.total - self.paid).quantize(TWO)


class InvoiceLine(TenantBaseModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_code = models.ForeignKey(
        CostCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["invoice", "position"]

    def __str__(self):
        return self.description[:60]

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(TWO)


class Payment(TenantBaseModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=32, default="eft")
    reference = models.CharField(max_length=120, blank=True)  # POP reference
    is_retention = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.amount} on {self.invoice}"


class VariationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_CUSTOMER = "pending_customer", "Pending customer approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Variation(TenantBaseModel):
    """One entity, two views: operational (scope/schedule, Module 9) and commercial
    (value, budget impact, here). Customer approval is external → human-approved;
    on approval the budget updates automatically."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="variations"
    )
    number = models.CharField(max_length=32)
    description = models.CharField(max_length=500)
    reason = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=32, default="additional_scope")
    category = models.CharField(max_length=16, choices=CostCategory.choices,
                                default=CostCategory.OTHER)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    revenue_impact = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=VariationStatus.choices,
                              default=VariationStatus.DRAFT)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number
