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

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel

TWO = Decimal("0.01")


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
#: Finalized: issued to the customer or beyond. The document is now the
#: commercial source of truth, so the editor is closed — a change is a new
#: revision, never an overwrite. Wider than LOCKED_STATUSES (which is only the
#: customer's final answer) because "finalize" locks editing before sending.
FINALIZED_STATUSES = LOCKED_STATUSES | {QuotationStatus.ISSUED, QuotationStatus.SENT}
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
    def net_total(self) -> Decimal:
        """The value of the work, excluding VAT, after discounts."""
        return sum((line.line_total for line in self.lines.all()),
                   Decimal("0.00")).quantize(TWO)

    #: Kept for the existing PDF/API callers.
    @property
    def subtotal(self) -> Decimal:
        return self.net_total

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
    document = models.FileField(upload_to="customer_pos/%Y/", blank=True, null=True)
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
    file = models.FileField(upload_to="quotation_docs/%Y/")

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
        return self.status in (self.Status.FINALIZED, self.Status.SENT)
