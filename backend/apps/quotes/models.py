"""Quotation Management Engine (MODULE 5) — the commercial gateway.

A quotation is not a document. It is the commercial contract between customer
and contractor, and once awarded it becomes the single source of truth that
every task, material issue, labour hour, invoice and payment traces back to.

Three things follow from that, and they shape this module:

1. **Cost and price are different numbers.** The old model stored only a selling
   price, which means margin could not be computed at all — you could not tell a
   profitable quote from a loss-making one. Every line now carries a cost, a
   markup and a discount, and the selling price is DERIVED.

2. **Awarded quotations become read-only.** You may not quietly edit the thing
   you contracted to deliver. Changes after award are revisions or variations,
   both of which leave a trail.

3. **VAT is a display mode, not a second set of numbers.** A quote priced
   inclusive and one priced exclusive hold the same underlying values; only the
   presentation and the arithmetic direction differ. Storing both invites them
   to disagree.
"""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TenantBaseModel

TWO = Decimal("0.01")


# ── Tenant-namespaced upload paths ────────────────────────────────────────────
#
# Uploaded files land under the owning company's own directory, never a shared
# date bucket, so one tenant's storage can never be walked into from another's.
# Module-level so migrations can reference them by import path.

def po_upload_path(instance, filename):
    return f"c/{instance.company_id}/customer_pos/{filename}"


def quotation_doc_upload_path(instance, filename):
    return f"c/{instance.company_id}/quotation_docs/{filename}"


def template_import_upload_path(instance, filename):
    return f"c/{instance.company_id}/template_imports/{filename}"


class QuotationStatus(models.TextChoices):
    """The internal approval chain, then the customer's answer.

    DRAFT → REVIEW → MANAGER → COMMERCIAL → APPROVED → ISSUED → the customer
    decides. `sent` and `lost` are kept as aliases of ISSUED/REJECTED so existing
    rows and code keep working.
    """

    DRAFT = "draft", "Draft"
    REVIEW = "review", "Estimator review"
    MANAGER_APPROVAL = "manager_approval", "Manager approval"
    COMMERCIAL_APPROVAL = "commercial_approval", "Commercial approval"
    APPROVED = "approved", "Approved (internal)"
    ISSUED = "issued", "Issued to customer"
    SENT = "sent", "Sent"                       # legacy alias of ISSUED
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    ACCEPTED = "accepted", "Accepted"
    AWARDED = "awarded", "Awarded"
    REJECTED = "rejected", "Rejected"
    LOST = "lost", "Lost"                       # legacy alias of REJECTED
    EXPIRED = "expired", "Expired"

#: The internal chain, in order — used to offer "the next step".
APPROVAL_CHAIN = [
    QuotationStatus.DRAFT, QuotationStatus.REVIEW, QuotationStatus.MANAGER_APPROVAL,
    QuotationStatus.COMMERCIAL_APPROVAL, QuotationStatus.APPROVED,
    QuotationStatus.ISSUED,
]
#: Once here, the commercial terms are fixed. Editing is a revision, not an edit.
LOCKED_STATUSES = {QuotationStatus.AWARDED, QuotationStatus.ACCEPTED,
                   QuotationStatus.REJECTED, QuotationStatus.LOST,
                   QuotationStatus.EXPIRED}
#: Finalized: approved or beyond. Approval is the final commercial version — the
#: document is now the source of truth, so the editor is closed and a change is a
#: new revision, never an overwrite. (ISSUED/SENT are kept in the set for any
#: legacy rows; the live workflow ends at APPROVED — there is no separate
#: "finalize" or "send" step.)
FINALIZED_STATUSES = LOCKED_STATUSES | {QuotationStatus.APPROVED,
                                        QuotationStatus.ISSUED, QuotationStatus.SENT}
#: Statuses that count as "still in play" for the pipeline.
OPEN_STATUSES = set(APPROVAL_CHAIN) | {QuotationStatus.SENT,
                                       QuotationStatus.REVISION_REQUESTED}


class VatMode(models.TextChoices):
    EXCLUSIVE = "exclusive", "Prices exclude VAT"
    INCLUSIVE = "inclusive", "Prices include VAT"


#: Default quotation types. Each implies a different shape of work, which is why
#: the type drives which fields an estimator is shown.
#: (key, label, the sections this kind of job is usually priced in). The
#: emphasis is what makes the type useful rather than decorative: a Plant Hire
#: quote leads with mobilisation and standby, a Labour Hire one with rates and
#: overtime, and neither should present the other's empty fields.
DEFAULT_QUOTATION_TYPES = [
    ("supply", "Supply", ["Materials", "Delivery"]),
    ("labour_hire", "Labour Hire", ["Labour", "Overtime", "Travel", "Accommodation"]),
    ("plant_hire", "Plant Hire", ["Equipment", "Operator", "Mobilisation", "Standby"]),
    ("mechanical_repair", "Mechanical Repair",
     ["Labour", "Materials", "Consumables", "Equipment"]),
    ("electrical_repair", "Electrical Repair", ["Labour", "Materials", "Testing"]),
    ("maintenance", "Maintenance", ["Labour", "Consumables", "Callout"]),
    ("shutdown", "Shutdown Work",
     ["Labour", "Materials", "Equipment", "Project management", "Contingency"]),
    ("installation", "Installation", ["Labour", "Materials", "Equipment", "Transport"]),
    ("construction", "Construction",
     ["Labour", "Materials", "Equipment", "Transport", "Preliminaries"]),
    ("fabrication", "Fabrication", ["Labour", "Materials", "Consumables", "Coating"]),
    ("engineering", "Engineering Services", ["Professional fees", "Disbursements"]),
    ("inspection", "Inspection", ["Labour", "Equipment", "Reporting"]),
    ("project_management", "Project Management",
     ["Management fees", "Resources", "Disbursements"]),
    ("consulting", "Consulting", ["Professional fees", "Disbursements"]),
    ("emergency", "Emergency Breakdown", ["Callout", "Labour", "Materials"]),
    ("preventative", "Preventative Maintenance", ["Labour", "Consumables"]),
    ("rental", "Rental", ["Equipment", "Delivery", "Collection"]),
    ("transport", "Transportation", ["Transport", "Loading"]),
    ("other", "Other", []),
]


class QuotationType(TenantBaseModel):
    """Per-company quotation types. Seeded from the defaults; administrators add
    their own. `emphasis` records which sections matter for this type so the
    builder can lead with them rather than showing every field to everyone."""

    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=80)
    emphasis = models.JSONField(default=list, blank=True)
    default_markup_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "label"]
        constraints = [
            models.UniqueConstraint(fields=["company", "key"], name="uniq_quote_type"),
        ]

    def __str__(self):
        return self.label


class QuotationSource(models.TextChoices):
    """How the quotation came into being — the five creation routes."""

    BLANK = "blank", "Created from scratch"
    RFQ = "rfq", "From an uploaded RFQ"
    SCOPE = "scope", "From a scope of work"
    TEXT = "text", "From a text description"
    COPY = "copy", "Copied from an existing quotation"


class Quotation(TenantBaseModel):
    number = models.CharField(max_length=32)
    title = models.CharField(max_length=255, blank=True)

    # ── Who it is for. `client_name` and `site` stay as denormalised display
    # values so a quotation issued last year still reads as it was issued.
    client_name = models.CharField(max_length=255)
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations",
    )
    branch = models.ForeignKey(
        "customers.CustomerBranch", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations",
    )
    customer_site = models.ForeignKey(
        "customers.CustomerSite", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations",
    )
    department = models.ForeignKey(
        "customers.CustomerDepartment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations",
    )
    contact = models.ForeignKey(
        "customers.CustomerContact", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations",
    )
    site = models.CharField(max_length=255, blank=True)

    # ── What kind of work, and how it was created
    quotation_type = models.ForeignKey(
        QuotationType, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations",
    )
    source = models.CharField(max_length=12, choices=QuotationSource.choices,
                              default=QuotationSource.BLANK)
    source_rfq = models.ForeignKey(
        "rfq.RFQDocument", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations_created",
    )
    scope_of_work = models.TextField(blank=True)

    # ── Commercial terms
    status = models.CharField(max_length=24, choices=QuotationStatus.choices,
                              default=QuotationStatus.DRAFT)
    vat_mode = models.CharField(max_length=10, choices=VatMode.choices,
                                default=VatMode.EXCLUSIVE)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("15.00"))
    #: An overall discount on the whole quotation (in the quote currency), on top
    #: of any per-line discounts. Subtracted from the subtotal before VAT.
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2,
                                          default=Decimal("0.00"))
    currency = models.CharField(max_length=8, default="ZAR")
    validity_date = models.DateField(null=True, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)

    # ── References that let a customer find this in their own system
    #: Snapshotted from the customer at creation. A quotation issued last year
    #: keeps the vendor code it was issued under even if they re-register you.
    vendor_number = models.CharField(max_length=64, blank=True)
    customer_reference = models.CharField(max_length=64, blank=True)
    rfq_reference = models.CharField(max_length=64, blank=True)
    project_reference = models.CharField(max_length=64, blank=True)

    # ── Revisions: a new version supersedes, it does not overwrite
    revision = models.PositiveSmallIntegerField(default=0)
    supersedes = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="revisions",
    )

    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations_prepared",
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)

    exclusions = models.TextField(blank=True)
    assumptions = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    #: Per-document override of the company's default look. Null → use the
    #: company default template for quotations, then the plain layout.
    template = models.ForeignKey(
        "DocumentTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    #: Pinned at finalise: the exact template VERSION this document was rendered
    #: with, so a later edit to the template never changes an issued quotation.
    #: Null while still editable → resolve the live template head.
    template_version = models.ForeignKey(
        "DocumentTemplateVersion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number", "revision"],
                                    name="unique_quote_number_rev")
        ]
        indexes = [models.Index(fields=["company", "status"])]

    def __str__(self):
        return self.display_number

    @property
    def display_number(self) -> str:
        return f"{self.number} rev {self.revision}" if self.revision else self.number

    # ── Money ────────────────────────────────────────────────────────────────
    #
    # Computed from the lines every time rather than stored. A cached total that
    # disagrees with its lines is the classic quotation bug, and it always
    # surfaces in front of a customer.

    @property
    def lines_total(self) -> Decimal:
        """The sum of the line prices — before the overall quotation discount."""
        return sum((line.line_total for line in self.lines.all()),
                   Decimal("0.00")).quantize(TWO)

    @property
    def net_total(self) -> Decimal:
        """The value of the work, excluding VAT, after per-line discounts and the
        overall quotation discount. The discount is clamped to [0, subtotal] so a
        negative value can never inflate the total and an over-discount can never
        drive it below zero."""
        discount = self.discount_amount or Decimal("0.00")
        if discount < 0:
            discount = Decimal("0.00")
        net = self.lines_total - discount
        return (net if net > 0 else Decimal("0.00")).quantize(TWO)

    #: The subtotal shown above the discount line — the lines before the overall
    #: discount. (Kept as the name existing PDF/API callers use.)
    @property
    def subtotal(self) -> Decimal:
        return self.lines_total

    @property
    def vat_amount(self) -> Decimal:
        """The VAT figure. On an inclusive quote it is extracted from within the
        prices. On an exclusive quote it is a MEMO — the VAT that will be added
        when the tax invoice is raised, not added to the quotation itself."""
        if self.vat_mode == VatMode.INCLUSIVE:
            gross = self.net_total
            return (gross - gross / (1 + self.vat_rate / Decimal("100"))).quantize(TWO)
        return (self.net_total * self.vat_rate / Decimal("100")).quantize(TWO)

    @property
    def total(self) -> Decimal:
        """The quotation total is the sum of its line prices. A VAT-exclusive
        quotation does NOT add VAT here — VAT is applied on the tax invoice — so
        the total equals the net either way (VAT is already inside the prices on
        an inclusive quote)."""
        return self.net_total

    @property
    def invoice_total(self) -> Decimal:
        """What the tax invoice will come to — the quotation total plus VAT when
        the quote is exclusive (inclusive already contains it)."""
        if self.vat_mode == VatMode.INCLUSIVE:
            return self.net_total
        return (self.net_total + self.vat_amount).quantize(TWO)

    @property
    def total_cost(self) -> Decimal:
        return sum((line.total_cost for line in self.lines.all()),
                   Decimal("0.00")).quantize(TWO)

    @property
    def gross_profit(self) -> Decimal:
        """Excluding VAT — VAT is never yours."""
        base = self.net_total - self.vat_amount if self.vat_mode == VatMode.INCLUSIVE \
            else self.net_total
        return (base - self.total_cost).quantize(TWO)

    @property
    def margin_pct(self):
        """None while any cost is missing — see `margin_pct` on the line."""
        base = self.net_total - self.vat_amount if self.vat_mode == VatMode.INCLUSIVE \
            else self.net_total
        if not self.has_costs or not base:
            return None
        return (self.gross_profit / base * 100).quantize(TWO)

    @property
    def has_costs(self) -> bool:
        """True only when EVERY line carries a cost. A partially costed quote
        has a margin that looks better than it is."""
        lines = list(self.lines.all())
        return bool(lines) and all(line.has_cost for line in lines)

    @property
    def uncosted_lines(self) -> list:
        """The lines nobody has priced the cost of — what to fix before
        trusting the margin."""
        return [line for line in self.lines.all() if not line.has_cost]

    # ── State ────────────────────────────────────────────────────────────────

    @property
    def is_locked(self) -> bool:
        """Awarded or decided: the commercial terms are fixed. Editing now would
        change what was contracted without leaving a trace."""
        return self.status in LOCKED_STATUSES

    @property
    def is_finalized(self) -> bool:
        """Issued to the customer or beyond — the editor is closed. A change from
        here is a new revision, never an overwrite of the sent document."""
        return self.status in FINALIZED_STATUSES

    @property
    def is_editable(self) -> bool:
        """Still in internal preparation, so the estimator may edit it."""
        return self.status not in FINALIZED_STATUSES

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone
        return bool(self.validity_date and self.is_open
                    and self.validity_date < timezone.localdate())

    @property
    def awarded_value(self) -> Decimal:
        """What the customer actually ordered — which may be less than quoted if
        the work was awarded in stages."""
        return sum((po.value for po in self.customer_pos.all()),
                   Decimal("0.00")).quantize(TWO)


class QuotationSection(TenantBaseModel):
    """A named group of lines — Labour, Materials, Consumables, Equipment.
    Contractors price in sections and customers read them that way."""

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                  related_name="sections")
    name = models.CharField(max_length=120)
    position = models.PositiveSmallIntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["quotation", "position"]

    def __str__(self):
        return self.name

    @property
    def net_total(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()),
                   Decimal("0.00")).quantize(TWO)


class LineCategory(models.TextChoices):
    LABOUR = "labour", "Labour"
    MATERIAL = "material", "Materials"
    CONSUMABLE = "consumable", "Consumables"
    EQUIPMENT = "equipment", "Equipment"
    TRANSPORT = "transport", "Transport"
    ACCOMMODATION = "accommodation", "Accommodation"
    SUBCONTRACTOR = "subcontractor", "Subcontractor"
    MANAGEMENT = "management", "Project management"
    CONTINGENCY = "contingency", "Contingency"
    OTHER = "other", "Other"


class QuotationLine(TenantBaseModel):
    """A priced line. COST is what it costs you; SELLING PRICE is derived from
    cost + markup − discount, so margin is always knowable.

    `unit_price` may still be set directly (an estimator quoting a rate they were
    given); in that case cost may be zero and the margin is simply unknown rather
    than wrong.
    """

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                  related_name="lines")
    section = models.ForeignKey(QuotationSection, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    item_no = models.CharField(max_length=16, blank=True)
    description = models.CharField(max_length=500)
    category = models.CharField(max_length=16, choices=LineCategory.choices,
                                default=LineCategory.OTHER)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit = models.CharField(max_length=32, default="each")

    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    markup_pct = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    supplier = models.ForeignKey(
        "procurement.Supplier", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    labour_category = models.CharField(max_length=80, blank=True)
    equipment = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    #: Set when LulaAI proposed this line and a human accepted it — provenance
    #: worth keeping when reviewing why a quote was priced as it was.
    ai_suggested = models.BooleanField(default=False)

    class Meta:
        ordering = ["quotation", "position"]

    def __str__(self):
        return self.description[:60]

    def save(self, *args, **kwargs):
        # A negative quantity or price is never a real line — it is bad data that
        # would quietly corrupt every total that sums the lines. Reject it at the
        # boundary rather than let it into the money. Coerce first: callers often
        # assign strings ("20") that the DecimalField only converts on load, so
        # compare against the numeric value, not the raw attribute.
        def _num(field):
            value = getattr(self, field)
            if value is None or isinstance(value, Decimal):
                return value
            try:
                return Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError(f"{field.replace('_', ' ')} must be a number.")

        for field in ("qty", "unit_cost", "unit_price", "discount_pct"):
            setattr(self, field, _num(field))
        if self.qty is not None and self.qty < 0:
            raise ValidationError("Quantity cannot be negative.")
        if self.unit_cost is not None and self.unit_cost < 0:
            raise ValidationError("Unit cost cannot be negative.")
        if self.unit_price is not None and self.unit_price < 0:
            raise ValidationError("Unit price cannot be negative.")
        if self.discount_pct is not None and not (0 <= self.discount_pct <= 100):
            raise ValidationError("Discount percent must be between 0 and 100.")
        super().save(*args, **kwargs)

    @property
    def computed_unit_price(self) -> Decimal:
        """Cost + markup − discount. What the price SHOULD be."""
        price = self.unit_cost * (1 + self.markup_pct / Decimal("100"))
        if self.discount_pct:
            price *= (1 - self.discount_pct / Decimal("100"))
        return price.quantize(TWO)

    @property
    def effective_unit_price(self) -> Decimal:
        """The price actually used: an explicitly entered one wins, otherwise the
        computed one. An estimator quoting a given rate should not have it
        silently recalculated."""
        return self.unit_price or self.computed_unit_price

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.effective_unit_price).quantize(TWO)

    @property
    def total_cost(self) -> Decimal:
        return (self.qty * self.unit_cost).quantize(TWO)

    @property
    def gross_profit(self) -> Decimal:
        return (self.line_total - self.total_cost).quantize(TWO)

    @property
    def margin_pct(self):
        """None when there is no cost to measure against.

        Returning 100% for a line with no cost captured would be a lie a
        quotation could be approved on — "no cost recorded" and "costs nothing"
        are very different statements.
        """
        if not self.has_cost or not self.line_total:
            return None
        return (self.gross_profit / self.line_total * 100).quantize(TWO)

    @property
    def has_cost(self) -> bool:
        """False means margin on this line is unknown, not zero."""
        return bool(self.unit_cost)


class CustomerPurchaseOrder(TenantBaseModel):
    """The customer's PO — their instruction to proceed.

    A quotation may carry several: large work is often awarded in stages, and
    each stage arrives as its own PO against the same quotation.

    NOTE the direction. "Purchase order" means two opposite things to a
    contractor: the one the CUSTOMER sends you (this model, `customer_pos`) and
    the ones YOU send suppliers (procurement.PurchaseOrder, `purchase_orders`).
    Conflating them is how a receivable gets counted as a payable, so the
    accessors are deliberately different words.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETE = "complete", "Complete"
        CANCELLED = "cancelled", "Cancelled"

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                  related_name="customer_pos")
    po_number = models.CharField(max_length=64)
    po_date = models.DateField(null=True, blank=True)
    value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    document = models.FileField(upload_to=po_upload_path, blank=True, null=True)
    issued_by = models.ForeignKey(
        "customers.CustomerContact", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="purchase_orders_issued",
    )
    department = models.ForeignKey(
        "customers.CustomerDepartment", on_delete=models.SET_NULL, null=True,
        blank=True, related_name="purchase_orders",
    )
    approved_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.RECEIVED)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-po_date", "po_number"]

    def __str__(self):
        return self.po_number


class QuotationEvent(TenantBaseModel):
    """Every state change and customer response, kept forever.

    A quotation that was rejected and later awarded tells a story worth being
    able to reconstruct — and "who approved this, and when?" must always have
    an answer.
    """

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                  related_name="events")
    verb = models.CharField(max_length=40)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, blank=True)
    note = models.CharField(max_length=500, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True, related_name="+")
    #: Set when the CUSTOMER responded rather than someone internal.
    customer_contact = models.ForeignKey(
        "customers.CustomerContact", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.verb}: {self.from_status} → {self.to_status}"


class QuotationDocument(TenantBaseModel):
    """Everything the quotation was built from or sent with — the RFQ, drawings,
    a BOQ, site photos. Kept attached so the file is findable next year."""

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                  related_name="documents")
    name = models.CharField(max_length=200)
    doc_type = models.CharField(max_length=40, blank=True)
    file = models.FileField(upload_to=quotation_doc_upload_path)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class CommercialDocument(TenantBaseModel):
    """A tax invoice or delivery note generated FROM a quotation. It carries the
    quotation's reference (INV-<ref>-01, DN-<ref>-01), so the whole commercial
    chain — quotation → PO → invoice → delivery note — stays tied to one number
    and nothing is re-keyed. The PDF is generated on demand from the quotation."""

    class Kind(models.TextChoices):
        INVOICE = "invoice", "Tax invoice"
        DELIVERY = "delivery", "Delivery note"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        FINALIZED = "finalized", "Finalized"
        SENT = "sent", "Sent"

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                  related_name="commercial_documents")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    number = models.CharField(max_length=48)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.DRAFT)
    purchase_order = models.ForeignKey(
        CustomerPurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="commercial_documents")

    #: Per-document override of the company's default look for this doc type.
    template = models.ForeignKey(
        "DocumentTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    #: Pinned at finalise — the exact template version used (immutable history).
    template_version = models.ForeignKey(
        "DocumentTemplateVersion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    # Delivery-note operational fields (a delivery note carries no prices).
    delivery_date = models.DateField(null=True, blank=True)
    delivery_address = models.CharField(max_length=255, blank=True)
    driver = models.CharField(max_length=120, blank=True)
    receiver_name = models.CharField(max_length=120, blank=True)
    delivery_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"],
                                    name="unique_commercial_doc_number"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_finalized(self) -> bool:
        # Approval is the final step — an approved document is the read-only
        # commercial record. FINALIZED/SENT remain for any legacy rows.
        return self.status in (self.Status.APPROVED, self.Status.FINALIZED,
                               self.Status.SENT)


# ══════════════════════════════════════════════════════════════════════════════
# Document Designer — companies keep THEIR document branding, not ours.
#
# A business that has issued the same quotation layout for fifteen years should
# not have to abandon it to adopt LulaWorks. A DocumentTemplate lets a company
# hold several looks per document type, pick a default, and override it on a
# single document — without touching code.
#
# Deliberately config-over-code: the visual variety is a small set of real
# BASE LAYOUTS (classic / modern / compact) parameterised by a JSON `config`
# (colours, font, logo position, header/footer, watermark, page numbering, which
# fields show, banking, terms). The named looks the market expects — Engineering,
# Mining, Corporate, Logistics — are PRESET CONFIGS over those layouts, not
# fifteen bespoke renderers. This is what lets a visual designer or AI-assisted
# "import my existing document" be added later without a redesign: they will just
# produce more `config`, and the renderer already knows how to read it.
# ══════════════════════════════════════════════════════════════════════════════


class DocumentType(models.TextChoices):
    QUOTATION = "quotation", "Quotation"
    INVOICE = "invoice", "Tax invoice"
    DELIVERY = "delivery", "Delivery note"


class BaseLayout(models.TextChoices):
    CLASSIC = "classic", "Classic"      # the letterhead LulaWorks shipped with
    MODERN = "modern", "Modern"         # accent bar, logo can move
    COMPACT = "compact", "Compact"      # tighter, fits more on a page


#: The full set of switches a template may set, with the values used when a
#: template (or the whole feature) is absent — so a company with no templates
#: renders exactly as before. `resolve` / `effective_config` in services merge a
#: template's stored `config` over this. Kept flat and JSON-serialisable so the
#: same shape can come from a form today or an AI importer tomorrow.
DEFAULT_CONFIG = {
    "accent_color": "",           # "" → company brand_primary → LulaWorks teal
    "secondary_color": "",
    "font": "Helvetica",          # Helvetica | Times-Roman | Courier (print-safe)
    "logo_position": "right",     # left | center | right (logo side; identity fills the rest)
    "header_note": "",            # small line under the letterhead
    "footer_note": "",            # small line in the page footer
    "watermark_text": "",         # e.g. "DRAFT", "QUOTATION"
    "show_watermark": False,
    "page_numbering": True,
    # Which blocks/fields appear. Defaults reproduce today's documents.
    "show_banking": True,
    "show_signature": True,
    "show_terms": True,
    "show_vat_number": True,
    "show_registration_number": True,
    "show_customer_po": True,
    "show_project_reference": False,
    "show_qr": False,
    "terms_override": "",         # non-empty replaces the company's stored terms
}

#: Values a `config` may legitimately hold, so a form/import can't smuggle in
#: unknown keys or unsafe values. Enforced in services.clean_config.
ALLOWED_FONTS = ["Helvetica", "Times-Roman", "Courier"]
ALLOWED_LOGO_POSITIONS = ["left", "center", "right"]


# ── HTML engine: the structured `design` a custom-built or AI-imported template
# carries on its version. The visual builder edits this; the HTML renderer turns
# it into a document deterministically. Kept as a small, validated schema (not
# freeform HTML) so the no-code builder is simple and the output is always safe.

#: Ordered document sections a design may show/hide/reorder. Each maps to a block
#: the HTML renderer knows how to populate from real data — never freeform markup.
TEMPLATE_SECTIONS = [
    ("letterhead", "Letterhead (logo + company)"),
    ("document_meta", "Document title, reference & date"),
    ("parties", "Customer / bill-to"),
    ("scope", "Scope of work"),
    ("items", "Items table"),
    ("totals", "Totals (subtotal / VAT / total)"),
    ("banking", "Banking details"),
    ("terms", "Terms & conditions"),
    ("signature", "Signature area"),
    ("footer", "Footer note"),
]
TEMPLATE_SECTION_KEYS = [k for k, _ in TEMPLATE_SECTIONS]

#: Columns the items table may show, in order. `amount`/`unit_price` are dropped
#: automatically on a delivery note (which carries quantities, never prices).
TEMPLATE_ITEM_COLUMNS = [
    ("item_no", "No."), ("description", "Description"), ("qty", "Qty"),
    ("unit", "Unit"), ("unit_price", "Unit price"), ("amount", "Amount"),
]
TEMPLATE_ITEM_COLUMN_KEYS = [k for k, _ in TEMPLATE_ITEM_COLUMNS]

ALLOWED_FONT_FAMILIES = ["Helvetica", "Arial", "Times New Roman", "Georgia",
                         "Courier New", "Verdana", "Trebuchet MS"]

#: Structural knobs — what actually makes documents look DIFFERENT (not just
#: recoloured). The HTML generator honours each; the visual builder exposes them.
ALLOWED_HEADER_STYLES = ["band", "plain", "minimal", "centered", "split", "sidebar"]
ALLOWED_TABLE_STYLES = ["lines", "striped", "bordered", "plain"]
ALLOWED_TOTALS_STYLES = ["plain", "boxed", "highlighted"]
ALLOWED_SECTION_STYLES = ["plain", "bar", "underline"]

#: The design a new HTML template starts from — a clean, complete document.
DEFAULT_DESIGN = {
    "branding": {
        "accent_color": "#0E6E6E",
        "secondary_color": "",
        "font_family": "Helvetica",
        "logo_position": "left",
        "header_style": "band",
    },
    "sections": [{"key": k, "visible": True} for k in TEMPLATE_SECTION_KEYS],
    "columns": list(TEMPLATE_ITEM_COLUMN_KEYS),
    "table_style": "lines",
    "totals_style": "plain",
    "section_title_style": "plain",
    "header_note": "",
    "footer_note": "",
}

#: The field library the visual builder advertises (grouped {{tokens}}). These are
#: documentation for the user; the renderer fills real values by key, so a token
#: can never inject data from another tenant. Extensible — add a row, wire it in
#: build_context. (Method 2/3 of the template system.)
TEMPLATE_FIELD_LIBRARY = {
    "Company": ["company.name", "company.registration_number", "company.tax_number",
                "company.email", "company.phone", "company.website", "company.address",
                "company.logo"],
    "Customer": ["customer.name", "customer.vat_number", "customer.address",
                 "customer.email"],
    "Contact": ["contact.name", "contact.email", "contact.phone"],
    "Document": ["document.reference", "document.date", "document.prepared_by",
                 "document.title", "document.type"],
    "Job": ["job.title", "job.site", "job.scope_of_work"],
    "Items": ["items", "item.description", "item.quantity", "item.unit",
              "item.unit_price", "item.amount"],
    "Financial": ["subtotal", "discount", "vat", "total"],
    "Banking": ["company.bank_name", "company.account_name", "company.account_number",
                "company.branch_code"],
    "Terms": ["terms_and_conditions"],
}

#: Built-in HTML-engine "looks" — genuinely DIFFERENT structures (not recolours),
#: seeded per company alongside the ReportLab presets. Each is (name, description,
#: design). The design varies header layout, table style, totals and section-title
#: treatment — the things that actually make documents look different.
BUILTIN_HTML_LOOKS = [
    ("Sidebar", "Coloured left rail with your logo; content on the right.", {
        "branding": {"accent_color": "#0E6E6E", "header_style": "sidebar",
                     "logo_position": "left", "font_family": "Helvetica"},
        "table_style": "striped", "totals_style": "plain",
        "section_title_style": "underline"}),
    ("Centered", "Logo and company centred — formal and clean.", {
        "branding": {"accent_color": "#1F3A5F", "header_style": "centered",
                     "logo_position": "center", "font_family": "Georgia"},
        "table_style": "plain", "totals_style": "plain",
        "section_title_style": "underline"}),
    ("Split Header", "Two-tone header band with a strong document title.", {
        "branding": {"accent_color": "#0B72E7", "secondary_color": "#08356E",
                     "header_style": "split", "logo_position": "left"},
        "table_style": "lines", "totals_style": "boxed",
        "section_title_style": "plain"}),
    ("Bordered Ledger", "Ruled table and boxed totals — an accounting look.", {
        "branding": {"accent_color": "#37474F", "header_style": "plain",
                     "logo_position": "right", "font_family": "Arial"},
        "table_style": "bordered", "totals_style": "boxed",
        "section_title_style": "bar"}),
    ("Minimal Line", "Thin rules and lots of whitespace — understated.", {
        "branding": {"accent_color": "#111827", "header_style": "minimal",
                     "logo_position": "left", "font_family": "Helvetica"},
        "table_style": "plain", "totals_style": "plain",
        "section_title_style": "plain"}),
    ("Statement", "Striped rows and a highlighted total.", {
        "branding": {"accent_color": "#2CA01C", "header_style": "band",
                     "logo_position": "left"},
        "table_style": "striped", "totals_style": "highlighted",
        "section_title_style": "bar"}),
]


class TemplateEngine(models.TextChoices):
    """How a template is rendered. The ReportLab engine draws today's built-in
    looks from `config`; the HTML engine (WeasyPrint) renders a stored HTML/CSS
    body with {{field}} placeholders — used for custom-built and AI-imported
    designs. Both coexist; a document just points at a template of either kind."""
    REPORTLAB = "reportlab", "LulaWorks (built-in)"
    HTML = "html", "HTML / CSS"


class TemplateOrigin(models.TextChoices):
    """Where a template came from — drives the library UI and, later, a
    marketplace. `builtin` = seeded LulaWorks look; `custom` = built in the
    visual editor; `imported` = reconstructed by LulaAI from an uploaded doc."""
    BUILTIN = "builtin", "LulaWorks template"
    CUSTOM = "custom", "Custom"
    IMPORTED = "imported", "Imported"


class DocumentTemplate(TenantBaseModel):
    """A named template ("family/head") for one document type. `is_default` marks
    the company's default for that type (at most one per company+type, kept true
    by the service). For the ReportLab engine `config` holds the switches above;
    for the HTML engine the design lives on the current DocumentTemplateVersion.

    This row is the stable handle a document points at; its editable history is
    the chain of DocumentTemplateVersions, so a finalised document can pin the
    exact version it was rendered with and never change under a later edit."""

    doc_type = models.CharField(max_length=12, choices=DocumentType.choices)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    engine = models.CharField(max_length=12, choices=TemplateEngine.choices,
                              default=TemplateEngine.REPORTLAB)
    origin = models.CharField(max_length=12, choices=TemplateOrigin.choices,
                              default=TemplateOrigin.BUILTIN)
    base_layout = models.CharField(max_length=12, choices=BaseLayout.choices,
                                   default=BaseLayout.CLASSIC)
    is_default = models.BooleanField(default=False)
    #: True for the seeded built-ins; a company can add its own on top.
    is_builtin = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    #: Archived templates are kept (a past document may reference one) but hidden
    #: from the picker; restore clears this. Never hard-deleted.
    archived_at = models.DateTimeField(null=True, blank=True)
    #: The version a NEW document rendered with this template will use. History is
    #: the reverse `versions` relation; this is just the pointer to the head.
    current_version = models.ForeignKey(
        "DocumentTemplateVersion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    class Meta:
        ordering = ["doc_type", "-is_default", "name"]
        indexes = [models.Index(fields=["company", "doc_type", "is_default"]),
                   models.Index(fields=["company", "doc_type", "archived_at"])]

    def __str__(self):
        return f"{self.get_doc_type_display()} · {self.name}"

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_html(self) -> bool:
        return self.engine == TemplateEngine.HTML

    def merged_config(self) -> dict:
        """This template's config filled in with the defaults for anything unset
        — the shape the PDF builders actually read."""
        out = dict(DEFAULT_CONFIG)
        out.update(self.config or {})
        return out


class DocumentTemplateVersion(TenantBaseModel):
    """An immutable snapshot of a template at one point in time. Every save makes
    a new version; a finalised document pins the version it used so a later edit
    to the template never alters an already-issued quotation/invoice/DN.

    Holds BOTH engines' payloads: `config`/`base_layout` for ReportLab, and
    `html`/`css`/`design` for the HTML engine (populated by the visual builder or
    an AI import in a later phase — blank for today's built-ins)."""

    template = models.ForeignKey(DocumentTemplate, on_delete=models.CASCADE,
                                 related_name="versions")
    version = models.PositiveIntegerField(default=1)
    engine = models.CharField(max_length=12, choices=TemplateEngine.choices,
                              default=TemplateEngine.REPORTLAB)
    base_layout = models.CharField(max_length=12, choices=BaseLayout.choices,
                                   default=BaseLayout.CLASSIC)
    config = models.JSONField(default=dict, blank=True)
    #: HTML engine payload (future phases). `design` is the structured spec
    #: (sections/fields/columns) an AI import or the visual editor produces.
    html = models.TextField(blank=True)
    css = models.TextField(blank=True)
    design = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["template", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["template", "version"],
                                    name="unique_template_version"),
        ]

    def __str__(self):
        return f"{self.template.name} v{self.version}"

    def merged_config(self) -> dict:
        out = dict(DEFAULT_CONFIG)
        out.update(self.config or {})
        return out


class TemplateImport(TenantBaseModel):
    """Method 3 — a company uploads a document it already uses and LulaAI
    reconstructs its VISUAL STRUCTURE into a reusable HTML template `design`.

    The upload is analysed (deterministically first, AI-enriched when credits are
    available) into a proposed `design` + `warnings` (low-confidence fields the
    user must confirm). AI identifies layout only — it never invents business
    data. On approval a DocumentTemplate (origin=imported) is created from the
    design; the upload is kept tenant-isolated for the audit trail."""

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        READY = "ready", "Ready for review"
        FAILED = "failed", "Analysis failed"
        SAVED = "saved", "Saved as template"

    doc_type = models.CharField(max_length=12, choices=DocumentType.choices)
    source_file = models.FileField(upload_to=template_import_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.UPLOADED)
    #: The reconstructed design (same schema the visual builder + renderer use).
    design = models.JSONField(default=dict, blank=True)
    #: Low-confidence findings for the user to confirm (list of {field, message}).
    warnings = models.JSONField(default=list, blank=True)
    #: The raw features the analyser pulled from the document (audit / debugging).
    features = models.JSONField(default=dict, blank=True)
    ai_used = models.BooleanField(default=False)
    credits_used = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    error = models.CharField(max_length=255, blank=True)
    saved_template = models.ForeignKey(DocumentTemplate, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"])]

    def __str__(self):
        return f"Import: {self.original_name or self.source_file.name}"


#: The built-in library seeded for every company (services.seed_document_templates).
#: (doc_type, name, base_layout, is_default, config-overrides). Configs are sparse
#: — only what differs from DEFAULT_CONFIG — because that is exactly what a preset
#: IS: a small, named set of deviations from the plain document.
BUILTIN_TEMPLATES = [
    # Quotations
    ("quotation", "Classic", "classic", True, {}),
    ("quotation", "Modern", "modern", False,
     {"logo_position": "left", "accent_color": "#0E6E6E"}),
    ("quotation", "Corporate", "classic", False,
     {"accent_color": "#1F3A5F", "font": "Times-Roman"}),
    ("quotation", "Engineering", "modern", False,
     {"accent_color": "#14549B", "show_project_reference": True,
      "header_note": "Engineering & Technical Services"}),
    ("quotation", "Minimal", "compact", False,
     {"accent_color": "#333333", "show_banking": False, "show_signature": False}),
    ("quotation", "Mining", "modern", False,
     {"accent_color": "#B9711A", "secondary_color": "#22303B",
      "show_project_reference": True}),
    # Tax invoices
    ("invoice", "Standard", "classic", True, {}),
    ("invoice", "Corporate", "classic", False,
     {"accent_color": "#1F3A5F", "font": "Times-Roman"}),
    ("invoice", "Compact", "compact", False, {"show_signature": False}),
    # Delivery notes
    ("delivery", "Standard", "classic", True, {}),
    ("delivery", "Warehouse", "compact", False,
     {"show_signature": True, "header_note": "Goods received in good order"}),
    ("delivery", "Logistics", "modern", False,
     {"accent_color": "#0A8F57", "header_note": "Proof of Delivery"}),
]


#: House styles that echo the look of well-known billing / procurement tools, so a
#: company can pick a familiar, professional document without a designer. These are
#: LAYOUT + CONFIG only — the rendered document carries the company's OWN name,
#: logo and details; no third-party brand appears on it. The names are colour-led
#: style labels shown only in the template picker. Each expands across all three
#: document types so a company gets a consistent set.
#:   (label, base_layout, config-overrides)
HOUSE_STYLES = [
    # Clean invoicing tools → the 'modern' coloured-band look, logo on the left.
    ("FreshBooks Blue", "modern",
     {"accent_color": "#0B72E7", "logo_position": "left"}),
    ("Xero Teal", "modern",
     {"accent_color": "#13B5EA", "logo_position": "left"}),
    ("QuickBooks Green", "modern",
     {"accent_color": "#2CA01C", "logo_position": "left"}),
    ("Sage Green", "modern",
     {"accent_color": "#00875A", "logo_position": "left"}),
    ("Jira Blue", "modern",
     {"accent_color": "#0052CC", "logo_position": "left"}),
    # Enterprise procurement → the 'compact' dense look, logo right, order refs on.
    ("SAP Blue", "compact",
     {"accent_color": "#0A6ED1", "logo_position": "right",
      "show_project_reference": True, "show_customer_po": True}),
    ("Ariba Procurement", "compact",
     {"accent_color": "#1A5276", "logo_position": "right",
      "show_project_reference": True, "show_customer_po": True}),
]

#: Expand every house style across the three document types (never a default —
#: they sit alongside the built-in defaults for the company to choose).
BUILTIN_TEMPLATES += [
    (doc_type, label, layout, False, cfg)
    for label, layout, cfg in HOUSE_STYLES
    for doc_type in ("quotation", "invoice", "delivery")
]

#: The named LulaWorks library the picker groups by document type. These round out
#: the industry looks the market expects (Modern / Corporate / Engineering /
#: Construction / Industrial / Minimal). Only entries not already present above are
#: added — sync matches on (doc_type, name), so there are no duplicates.
BUILTIN_TEMPLATES += [
    # Quotation — Modern/Corporate/Engineering/Minimal already exist above.
    ("quotation", "Construction", "modern", False,
     {"accent_color": "#B45309", "show_project_reference": True,
      "header_note": "Construction & Civil Works"}),
    ("quotation", "Industrial", "compact", False,
     {"accent_color": "#37474F", "show_project_reference": True}),
    # Tax invoice.
    ("invoice", "Modern", "modern", False, {"accent_color": "#0B72E7"}),
    ("invoice", "Engineering", "modern", False,
     {"accent_color": "#14549B", "show_project_reference": True,
      "header_note": "Engineering & Technical Services"}),
    ("invoice", "Industrial", "compact", False, {"accent_color": "#37474F"}),
    ("invoice", "Minimal", "compact", False,
     {"accent_color": "#333333", "show_signature": False}),
    # Delivery note.
    ("delivery", "Modern", "modern", False, {"accent_color": "#0B72E7"}),
    ("delivery", "Corporate", "classic", False,
     {"accent_color": "#1F3A5F", "font": "Times-Roman"}),
    ("delivery", "Industrial", "compact", False,
     {"accent_color": "#37474F", "header_note": "Goods received in good order"}),
    ("delivery", "Minimal", "compact", False, {"accent_color": "#333333"}),
]
