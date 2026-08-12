"""Customer & Contact Management — the customer is an ORGANISATION.

The distinction this module exists to make: an enterprise contractor's client is
not a person and not a string. Harmony Mining is a company with branches, plants,
departments and dozens of people — the engineer who releases the RFQ is not the
manager who approves the quotation, and neither is the clerk who pays the
invoice. Storing "Harmony" in a text field loses all of that, and the cost shows
up later as a quotation emailed to the wrong person and an invoice nobody
approves.

    Customer ─┬─ Branches ── Sites (nestable: Plant 1 → Conveyor Area)
              ├─ Departments ── Contacts (roles + responsibilities)
              ├─ Contracts
              └─ Documents

Contacts carry RESPONSIBILITIES, not just titles. A title is a label; a
responsibility is functional — it tells LulaWorks who to send a quotation to and
who to copy, which is the whole point of modelling the organisation at all.
"""

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class CustomerStatus(models.TextChoices):
    PROSPECT = "prospect", "Prospect"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On hold"
    DORMANT = "dormant", "Dormant"
    BLACKLISTED = "blacklisted", "Blacklisted"


#: Job roles a contact may hold. A contact may hold several — a Maintenance
#: Planner is often also the Shutdown Coordinator.
CONTACT_ROLES = [
    "Engineering Manager", "Maintenance Planner", "Shutdown Coordinator",
    "Project Engineer", "Procurement Officer", "Buyer", "Supervisor",
    "Safety Officer", "Finance Officer", "Accounts Payable",
    "Accounts Receivable", "Operations Manager", "Mine Manager",
    "Technical Director", "General Manager",
]

#: What a contact is empowered to DO. These drive document routing, so they are
#: functional rather than descriptive — the difference between "Finance Manager"
#: (a label) and "approves invoices" (an instruction the system can act on).
RESPONSIBILITIES = {
    "release_rfq": "Can release RFQs",
    "approve_quotation": "Can approve quotations",
    "approve_po": "Can approve purchase orders",
    "sign_completion": "Can sign completion certificates",
    "approve_variation": "Can approve variations",
    "approve_invoice": "Can approve invoices",
    "receive_reports": "Receives progress reports",
    "receive_safety_file": "Receives safety files",
    "authorise_site_access": "Can authorise site access",
    "receive_invoice": "Receives invoices",
}


#: Uploads are namespaced under the owning company. A path is not an access
#: control, but keeping every tenant's files in their own `c/<id>/…` prefix means
#: a stray URL or a misconfigured bucket policy leaks one tenant, not all of them
#: — defence in depth for something that is cheap to get right at write time.
#: These live at module level so migrations can serialise them by import path.
def customer_logo_upload_path(instance, filename):
    return f"c/{instance.company_id}/customer_logos/{filename}"


def customer_doc_upload_path(instance, filename):
    return f"c/{instance.company_id}/customer_docs/{filename}"


#: What kind of client this is. Drives nothing structural yet — it exists so a
#: sales team can segment and report ("show me all the mines"), and so future
#: pricing/terms defaults can key off it without another migration.
class CustomerType(models.TextChoices):
    MINE = "mine", "Mine"
    INDUSTRIAL = "industrial", "Industrial / Manufacturing"
    CONSTRUCTION = "construction", "Construction"
    ENGINEERING = "engineering", "Engineering firm"
    UTILITY = "utility", "Utility / Power"
    GOVERNMENT = "government", "Government / Municipal"
    COMMERCIAL = "commercial", "Commercial"
    OTHER = "other", "Other"


class Customer(TenantBaseModel):
    """A client organisation. `code` is generated so people have something short
    to quote on paperwork; `client_name` fields elsewhere become a display
    fallback rather than the source of truth."""

    code = models.CharField(max_length=16, blank=True)
    name = models.CharField(max_length=255)                  # registered name
    trading_name = models.CharField(max_length=255, blank=True)
    customer_type = models.CharField(max_length=16, choices=CustomerType.choices,
                                     blank=True)
    #: ISO-ish language tag for future localisation of documents to this client
    #: (e.g. "en", "pt", "fr"). Stored now so the relationship carries it; no
    #: behaviour keys off it yet.
    preferred_language = models.CharField(max_length=12, blank=True)
    registration_no = models.CharField(max_length=64, blank=True)
    vat_no = models.CharField(max_length=32, blank=True)
    tax_no = models.CharField(max_length=32, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    logo = models.ImageField(upload_to=customer_logo_upload_path, blank=True, null=True)

    # Address
    country = models.CharField(max_length=64, default="South Africa")
    province = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=120, blank=True)
    physical_address = models.CharField(max_length=255, blank=True)
    postal_address = models.CharField(max_length=255, blank=True)
    postal_code = models.CharField(max_length=16, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # Contact (the switchboard — individuals live on CustomerContact)
    telephone = models.CharField(max_length=32, blank=True)
    mobile = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    # Commercial
    #: The code THIS customer uses for US in their system — "TRL0086" on a
    #: Western Platinum PO. Every client assigns a different one, it is how
    #: their AP clerk finds you, and a quotation without it can sit unmatched
    #: for weeks. It belongs to the relationship, not to us, which is why it
    #: lives here and not on the company profile.
    vendor_number = models.CharField(max_length=64, blank=True)
    vendor_portal = models.CharField(max_length=120, blank=True)  # Coupa, Ariba…
    vendor_note = models.CharField(max_length=255, blank=True)

    payment_terms_days = models.PositiveSmallIntegerField(default=30)
    payment_terms_note = models.CharField(max_length=120, blank=True)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True,
                                       blank=True)
    currency = models.CharField(max_length=8, default="ZAR")
    status = models.CharField(max_length=16, choices=CustomerStatus.choices,
                              default=CustomerStatus.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_customer_code"),
        ]

    def __str__(self):
        return self.trading_name or self.name

    @property
    def display_name(self) -> str:
        return self.trading_name or self.name

    @property
    def is_tradeable(self) -> bool:
        """Whether new work should be accepted — blacklisted and on-hold clients
        are the ones a salesperson quotes by accident."""
        return self.status in (CustomerStatus.ACTIVE, CustomerStatus.PROSPECT)


class CustomerBranch(TenantBaseModel):
    """A customer location. Harmony has Welkom, Virginia and Moab Khotsong, each
    with its own management and its own way of doing things."""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="branches")
    name = models.CharField(max_length=160)
    physical_address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    province = models.CharField(max_length=64, blank=True)
    manager_name = models.CharField(max_length=160, blank=True)
    telephone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)

    class Meta:
        ordering = ["customer", "name"]

    def __str__(self):
        return f"{self.customer} · {self.name}"


class CustomerSite(TenantBaseModel):
    """A place work actually happens. Nestable, because a mine is Plant 1 →
    Conveyor Area → Crusher, and a project is assigned to the specific place,
    not to the mine in general."""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="sites")
    branch = models.ForeignKey(CustomerBranch, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="sites")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="children")
    name = models.CharField(max_length=160)
    site_code = models.CharField(max_length=32, blank=True)
    description = models.CharField(max_length=255, blank=True)
    physical_address = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    access_notes = models.CharField(max_length=255, blank=True)
    #: Safety requirements to get on site — induction, PPE, permits. The crew
    #: needs this BEFORE they arrive, so it lives on the site, not in someone's head.
    safety_requirements = models.TextField(blank=True)
    #: Who to call when you're at the gate. A contact at the customer, so it stays
    #: correct if that person's number changes.
    site_contact = models.ForeignKey("CustomerContact", on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["customer", "name"]

    def __str__(self):
        return self.full_path

    @property
    def full_path(self) -> str:
        """"Welkom / Plant 1 / Crusher" — what a person on site would say."""
        names, node, guard = [], self, 0
        while node is not None and guard < 10:
            names.append(node.name)
            node = node.parent
            guard += 1
        if self.branch_id:
            names.append(self.branch.name)
        return " / ".join(reversed(names))


class CustomerDepartment(TenantBaseModel):
    """Engineering, Procurement, Finance… Departments matter because documents
    are routed to them, and because the same company behaves differently
    depending on which one you are dealing with."""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="departments")
    branch = models.ForeignKey(CustomerBranch, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="departments")
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=32, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["customer", "name"]

    def __str__(self):
        return f"{self.customer} · {self.name}"


#: A sensible starting set for a mining/industrial client.
DEFAULT_DEPARTMENTS = [
    "Engineering", "Maintenance", "Operations", "Procurement", "Projects",
    "Safety", "Finance", "Supply Chain", "Stores",
]


class CustomerContact(TenantBaseModel):
    """A person at the customer. A customer may have dozens, and knowing which
    one does what is the difference between a quotation that gets approved and
    one that sits in an inbox."""

    class Method(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        MOBILE = "mobile", "Mobile"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        LEFT = "left", "No longer there"
        DO_NOT_CONTACT = "do_not_contact", "Do not contact"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="contacts")
    department = models.ForeignKey(CustomerDepartment, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="contacts")
    branch = models.ForeignKey(CustomerBranch, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="contacts")
    full_name = models.CharField(max_length=160)
    job_title = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=32, blank=True)
    mobile = models.CharField(max_length=32, blank=True)
    extension = models.CharField(max_length=12, blank=True)
    whatsapp = models.CharField(max_length=32, blank=True)
    preferred_method = models.CharField(max_length=10, choices=Method.choices,
                                        default=Method.EMAIL)
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.ACTIVE)

    #: Labels — what they are called. A contact may hold several.
    roles = models.JSONField(default=list, blank=True)
    #: Functional — what they may DO. These drive document routing.
    responsibilities = models.JSONField(default=list, blank=True)

    is_primary = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-is_primary", "full_name"]
        indexes = [models.Index(fields=["company", "customer", "status"])]

    def __str__(self):
        return self.full_name

    @property
    def is_contactable(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def reach(self) -> str:
        return self.email or self.mobile or self.telephone or ""

    def can(self, responsibility: str) -> bool:
        return responsibility in (self.responsibilities or [])

    def responsibility_labels(self) -> list[str]:
        return [RESPONSIBILITIES[r] for r in (self.responsibilities or [])
                if r in RESPONSIBILITIES]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_primary:
            CustomerContact.objects.filter(customer_id=self.customer_id).exclude(
                pk=self.pk).update(is_primary=False)


class CustomerContract(TenantBaseModel):
    """A standing agreement — a rate contract, a framework agreement, an SLA.
    Work performed under one inherits its terms."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        TERMINATED = "terminated", "Terminated"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="contracts")
    reference = models.CharField(max_length=64)
    title = models.CharField(max_length=200, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "reference"]

    def __str__(self):
        return self.reference

    @property
    def is_expiring(self) -> bool:
        from datetime import timedelta

        from django.utils import timezone
        if not self.end_date or self.status != self.Status.ACTIVE:
            return False
        return self.end_date <= timezone.localdate() + timedelta(days=60)


class CustomerDocument(TenantBaseModel):
    """Vendor forms, signed contracts, site rules, safety requirements — the
    paperwork a client sends you that you need again six months later."""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="documents")
    name = models.CharField(max_length=200)
    doc_type = models.CharField(max_length=48, blank=True)
    file = models.FileField(upload_to=customer_doc_upload_path)
    expires_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


# ══════════════════════════════════════════════════════════════════════════════
# CRM — the relationship layer that sits AROUND the customer record.
#
# The customer models above answer "who is this organisation and how is it
# structured". The models below answer the questions a sales team lives in:
# who might become a customer (Lead), what deals are in flight (Opportunity),
# what we've done and must still do about them (Activity), what was said
# (Interaction), and what we must not forget (CustomerNote).
#
# The intended flow, and the reason this is a pipeline and not a flat list:
#
#     Lead ─▶ Opportunity ─▶ Customer ─▶ Quotation ─▶ Job ─▶ Invoice ─▶ Payment
#      │          │                          ▲
#      └── convert┴──────────────────────────┘
#
# A Lead is a stranger; converting it mints a real Customer. An Opportunity is a
# named deal against a Customer; winning it is expected to produce a Quotation.
# Nothing here sends anything or moves money — it records intent and history,
# leaving the actual quotation/job/invoice objects as the single source of truth
# (see the relationship helpers in services.py).
# ══════════════════════════════════════════════════════════════════════════════


#: Where a lead came from — the input to any "which channel actually converts?"
#: question a sales manager will eventually ask.
LEAD_SOURCES = [
    "Website", "Referral", "Cold call", "Email", "Trade show", "Tender portal",
    "Existing customer", "Social media", "Advertising", "Walk-in", "Other",
]


class Lead(TenantBaseModel):
    """A potential customer we have NOT yet qualified into a real relationship.

    Kept deliberately separate from Customer: a lead is cheap, disposable and
    often junk, and we don't want half-real strangers polluting the customer
    list, the routing engine or the reports. When a lead is real, converting it
    creates a proper Customer (and optionally an Opportunity) and stamps
    `converted_customer` so the history survives the promotion.
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        UNQUALIFIED = "unqualified", "Unqualified"
        CONVERTED = "converted", "Converted"
        LOST = "lost", "Lost"

    # Who they are — free text, because we know almost nothing yet.
    company_name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=160, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=32, blank=True)
    mobile = models.CharField(max_length=32, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    customer_type = models.CharField(max_length=16, choices=CustomerType.choices,
                                     blank=True)
    city = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=64, blank=True)

    source = models.CharField(max_length=48, blank=True)   # one of LEAD_SOURCES
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.NEW, db_index=True)
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2,
                                          null=True, blank=True)
    currency = models.CharField(max_length=8, default="ZAR")

    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    # Promotion trail: set when the lead becomes a real customer.
    converted_customer = models.ForeignKey(Customer, on_delete=models.SET_NULL,
                                           null=True, blank=True,
                                           related_name="source_leads")
    converted_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"])]

    def __str__(self):
        return self.company_name

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.NEW, self.Status.CONTACTED,
                               self.Status.QUALIFIED)

    @property
    def display_contact(self) -> str:
        return self.contact_name or self.company_name


#: The deal pipeline. Ordered — the integer position drives the board columns
#: and the "how far through the funnel" reporting. WON/LOST are terminal.
class OpportunityStage(models.TextChoices):
    LEAD = "lead", "Lead"
    QUALIFIED = "qualified", "Qualified"
    QUOTE_REQUESTED = "quote_requested", "Quotation requested"
    QUOTE_SENT = "quote_sent", "Quotation sent"
    NEGOTIATION = "negotiation", "Negotiation"
    WON = "won", "Won"
    LOST = "lost", "Lost"


#: Position of each stage in the funnel (for ordering the board + progress %).
OPPORTUNITY_STAGE_ORDER = {
    OpportunityStage.LEAD: 0,
    OpportunityStage.QUALIFIED: 1,
    OpportunityStage.QUOTE_REQUESTED: 2,
    OpportunityStage.QUOTE_SENT: 3,
    OpportunityStage.NEGOTIATION: 4,
    OpportunityStage.WON: 5,
    OpportunityStage.LOST: 5,
}
OPEN_OPPORTUNITY_STAGES = [
    OpportunityStage.LEAD, OpportunityStage.QUALIFIED,
    OpportunityStage.QUOTE_REQUESTED, OpportunityStage.QUOTE_SENT,
    OpportunityStage.NEGOTIATION,
]


class Opportunity(TenantBaseModel):
    """A named, in-flight deal against a real Customer.

    This is the object a pipeline report sums: an estimated value, a stage, a
    probability and an expected close date. Winning one is expected to produce a
    Quotation — we hold a nullable link to it rather than duplicating line items,
    keeping the quotation as the source of commercial truth.
    """

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE,
                                 related_name="opportunities")
    #: Where it came from, when it was promoted out of a raw Lead.
    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="opportunities")
    site = models.ForeignKey(CustomerSite, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name="opportunities")
    contact = models.ForeignKey(CustomerContact, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="opportunities")

    title = models.CharField(max_length=200)
    reference = models.CharField(max_length=48, blank=True)  # client's enquiry ref
    description = models.TextField(blank=True)
    stage = models.CharField(max_length=20, choices=OpportunityStage.choices,
                             default=OpportunityStage.LEAD, db_index=True)
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2,
                                          null=True, blank=True)
    currency = models.CharField(max_length=8, default="ZAR")
    #: 0–100. Defaults track the stage but can be overridden by a salesperson.
    probability = models.PositiveSmallIntegerField(default=10)
    expected_close_date = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=48, blank=True)

    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")

    #: The deliverable a won deal produced. Optional and nullable — a deal can be
    #: won on a handshake before the quotation object exists.
    quotation = models.ForeignKey("quotes.Quotation", on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name="opportunities")

    closed_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "stage"])]
        verbose_name_plural = "opportunities"

    def __str__(self):
        return self.title

    @property
    def is_open(self) -> bool:
        return self.stage in OPEN_OPPORTUNITY_STAGES

    @property
    def is_won(self) -> bool:
        return self.stage == OpportunityStage.WON

    @property
    def is_lost(self) -> bool:
        return self.stage == OpportunityStage.LOST

    @property
    def weighted_value(self):
        """Estimated value × probability — what a forecast actually sums."""
        from decimal import Decimal
        if self.estimated_value is None:
            return Decimal("0")
        return (self.estimated_value * Decimal(self.probability) / Decimal(100))


class Activity(TenantBaseModel):
    """Something to DO, or something that was done — a call, a meeting, a site
    visit, a follow-up, a reminder.

    Activities are the CRM's to-do engine: an open activity with a due date is a
    task on someone's list; a completed one is a fact in the history. An activity
    can hang off any of the relationship anchors (customer / lead / opportunity /
    contact) so it shows up everywhere it's relevant without being duplicated.
    """

    class Type(models.TextChoices):
        CALL = "call", "Call"
        MEETING = "meeting", "Meeting"
        SITE_VISIT = "site_visit", "Site visit"
        FOLLOW_UP = "follow_up", "Follow-up"
        EMAIL = "email", "Email"
        REMINDER = "reminder", "Reminder"
        TASK = "task", "Task"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    activity_type = models.CharField(max_length=16, choices=Type.choices,
                                     default=Type.FOLLOW_UP)
    subject = models.CharField(max_length=200)
    detail = models.TextField(blank=True)

    # Anchors — any subset may be set. At least one should be, but the DB stays
    # permissive so a quick "call John tomorrow" needn't be fully wired first.
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, null=True,
                                 blank=True, related_name="activities")
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="activities")
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, null=True,
                                    blank=True, related_name="activities")
    contact = models.ForeignKey(CustomerContact, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="activities")

    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.OPEN, db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")

    completed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "due_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "status", "due_at"]),
            models.Index(fields=["company", "assigned_to", "status"]),
        ]
        verbose_name_plural = "activities"

    def __str__(self):
        return self.subject

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        return bool(self.is_open and self.due_at and self.due_at < timezone.now())


class Interaction(TenantBaseModel):
    """A record of communication that HAPPENED — an email, a phone call, a
    meeting, a WhatsApp exchange, a note of what was said.

    Distinct from Activity (which is forward-looking, a thing to do): an
    Interaction is the log of what actually passed between us and the client. It
    is the "Communication History" the spec asks for, and the raw material for
    "last activity" on the customer dashboard.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone call"
        MEETING = "meeting", "Meeting"
        SITE_VISIT = "site_visit", "Site visit"
        WHATSAPP = "whatsapp", "WhatsApp"
        NOTE = "note", "Internal note"
        OTHER = "other", "Other"

    class Direction(models.TextChoices):
        INBOUND = "in", "Inbound"
        OUTBOUND = "out", "Outbound"
        INTERNAL = "internal", "Internal"

    channel = models.CharField(max_length=12, choices=Channel.choices,
                               default=Channel.NOTE)
    direction = models.CharField(max_length=10, choices=Direction.choices,
                                 default=Direction.OUTBOUND)
    subject = models.CharField(max_length=200, blank=True)
    summary = models.TextField()
    occurred_at = models.DateTimeField(db_index=True)

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, null=True,
                                 blank=True, related_name="interactions")
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="interactions")
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, null=True,
                                    blank=True, related_name="interactions")
    contact = models.ForeignKey(CustomerContact, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="interactions")

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["company", "customer", "-occurred_at"])]

    def __str__(self):
        return self.subject or f"{self.get_channel_display()} · {self.occurred_at:%Y-%m-%d}"


class CustomerNote(TenantBaseModel):
    """A free-form note about a client — the standing knowledge that isn't a
    field: "prefers morning deliveries", "always request site induction",
    "finance requires a PO reference on every invoice".

    Pinned notes float to the top because they're the ones a person needs to see
    before they act, not scroll to find.
    """

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, null=True,
                                 blank=True, related_name="crm_notes")
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="crm_notes")
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, null=True,
                                    blank=True, related_name="crm_notes")
    contact = models.ForeignKey(CustomerContact, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="crm_notes")
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return (self.body[:60] + "…") if len(self.body) > 60 else self.body
