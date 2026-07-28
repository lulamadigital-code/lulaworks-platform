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


class Customer(TenantBaseModel):
    """A client organisation. `code` is generated so people have something short
    to quote on paperwork; `client_name` fields elsewhere become a display
    fallback rather than the source of truth."""

    code = models.CharField(max_length=16, blank=True)
    name = models.CharField(max_length=255)                  # registered name
    trading_name = models.CharField(max_length=255, blank=True)
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
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    access_notes = models.CharField(max_length=255, blank=True)

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
