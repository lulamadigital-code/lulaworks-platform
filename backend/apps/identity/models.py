"""Identity, tenancy & RBAC (DATA_MODEL §4-6).

Company = tenant. User = platform identity (email login), linked to companies
via Membership (multi-company ready). Access via a granular Permission engine —
never `is_admin`.
"""

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from apps.core.models import PlatformBaseModel, UUIDModel

from .managers import UserManager


def default_currency():
    """New-company currency from platform config (not a hard-coded country)."""
    from django.conf import settings
    return getattr(settings, "DEFAULT_CURRENCY", "ZAR")


def default_timezone():
    """New-company timezone from platform config; each company can change it."""
    from django.conf import settings
    return getattr(settings, "TIME_ZONE", "UTC")


class SubscriptionStatus(models.TextChoices):
    TRIAL = "trial", "Trial"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class Company(PlatformBaseModel):
    """The tenant. Non-tenant platform table (it *is* the tenant)."""

    name = models.CharField(max_length=255)
    trading_name = models.CharField(max_length=255, blank=True)
    registration_no = models.CharField(max_length=64, blank=True)
    vat_no = models.CharField(max_length=32, blank=True)
    industry = models.CharField(max_length=64, blank=True)
    company_size = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=64, blank=True)  # neutral: set per company
    province = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=64, blank=True)
    # Locale defaults derive from platform config (not a hard-coded country), so
    # a company anywhere gets sensible neutral defaults it can then change.
    timezone = models.CharField(max_length=64, default=default_timezone)
    currency = models.CharField(max_length=8, default=default_currency)
    # VAT / sales-tax rate applied to this company's NEW invoices (0 = none).
    # Configurable per company so tax is never a single-country assumption.
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Tax engine config (apps.tax). Label + whether prices are quoted tax-inclusive
    # (EU-style) and whether cross-border B2B reverse charge applies.
    tax_name = models.CharField(max_length=24, blank=True)     # VAT / GST / Sales Tax
    prices_include_tax = models.BooleanField(default=False)
    reverse_charge_enabled = models.BooleanField(default=False)
    logo = models.ImageField(upload_to="company_logos/", blank=True, null=True)
    brand_primary = models.CharField(max_length=9, blank=True)
    brand_secondary = models.CharField(max_length=9, blank=True)
    # Up to four letters that begin every commercial document reference — the
    # quotation number and, from it, invoice/delivery-note references. Uppercase.
    document_prefix = models.CharField(max_length=4, blank=True)

    # ── Statutory identity ────────────────────────────────────────────────
    # These appear on every quotation, invoice and tender submission. SARS
    # requires the VAT number on a tax invoice, and most mines will not load a
    # supplier without a CSD number.
    tax_reference_no = models.CharField(max_length=32, blank=True)
    company_type = models.CharField(max_length=40, blank=True)   # Pty Ltd, CC, Sole Prop…
    year_established = models.PositiveSmallIntegerField(null=True, blank=True)

    # ── Contact ───────────────────────────────────────────────────────────
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    phone_secondary = models.CharField(max_length=32, blank=True)
    mobile = models.CharField(max_length=32, blank=True)
    whatsapp = models.CharField(max_length=32, blank=True)
    emergency_phone = models.CharField(max_length=32, blank=True)
    website = models.URLField(blank=True)
    facebook = models.CharField(max_length=200, blank=True)
    linkedin = models.CharField(max_length=200, blank=True)
    twitter = models.CharField(max_length=200, blank=True)

    # ── Physical address (country/province/city are above) ────────────────
    suburb = models.CharField(max_length=120, blank=True)
    street_address = models.CharField(max_length=255, blank=True)
    postal_code = models.CharField(max_length=16, blank=True)

    # ── Postal address ────────────────────────────────────────────────────
    postal_same_as_physical = models.BooleanField(default=True)
    postal_address = models.CharField(max_length=255, blank=True)
    postal_city = models.CharField(max_length=120, blank=True)
    postal_code_postal = models.CharField(max_length=16, blank=True)
    postal_country = models.CharField(max_length=64, blank=True)

    # ── Business profile (what you tell a client you do) ──────────────────
    description = models.TextField(blank=True)
    services_offered = models.JSONField(default=list, blank=True)
    specialisations = models.JSONField(default=list, blank=True)
    industries_served = models.JSONField(default=list, blank=True)
    employee_count = models.PositiveIntegerField(null=True, blank=True)
    vehicle_count = models.PositiveIntegerField(null=True, blank=True)
    site_count = models.PositiveIntegerField(null=True, blank=True)
    operating_countries = models.JSONField(default=list, blank=True)
    operating_provinces = models.JSONField(default=list, blank=True)

    subscription_status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.TRIAL
    )
    ai_credit_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    storage_quota_bytes = models.BigIntegerField(default=1_073_741_824)  # 1 GB
    storage_used_bytes = models.BigIntegerField(default=0)
    max_users = models.PositiveIntegerField(default=4)
    max_projects = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "companies"
        ordering = ["name"]

    def __str__(self):
        return self.name


class User(UUIDModel, AbstractBaseUser, PermissionsMixin):
    """Platform identity — email login. Tenant link is via Membership; the
    *active* company drives the ambient tenant context."""

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    mobile = models.CharField(max_length=32, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    status = models.CharField(max_length=16, default="active")
    mfa_enabled = models.BooleanField(default=False)

    # The company the user is currently operating in (multi-company: switch +
    # reissue token). Drives ambient tenant context (DATA_MODEL §1/§5).
    active_company = models.ForeignKey(
        Company, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # Set when a manager creates the account with a temporary password. Every
    # web page redirects to the change-password screen until it is cleared, so
    # an admin-chosen credential is never a usable long-lived password.
    must_change_password = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.get_full_name() or self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        """Fallback avatar when there is no photo — initials, else the first
        two characters of the email so there is never a blank circle."""
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return "".join(p[0] for p in parts[:2]).upper()
        return (self.email or "?")[:2].upper()

    @property
    def company_id(self):
        """Read by TenantMiddleware / TenantManager to set the ambient tenant."""
        return self.active_company_id

    def active_membership(self):
        if not self.active_company_id:
            return None
        return self.memberships.filter(company_id=self.active_company_id, status="active").first()

    def has_perm_code(self, codename: str) -> bool:
        """Granular RBAC check (never `is_admin`). Superuser bypasses."""
        if self.is_superuser:
            return True
        membership = self.active_membership()
        if not membership or not membership.role_id:
            return False
        return membership.role.permissions.filter(codename=codename).exists()


class Permission(PlatformBaseModel):
    """Granular permission catalogue, e.g. `projects.create`, `finance.view_money`."""

    codename = models.CharField(max_length=100, unique=True)
    module = models.CharField(max_length=64)
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["module", "codename"]

    def __str__(self):
        return self.codename


class Role(PlatformBaseModel):
    """A named permission set. company=null → platform default template
    (cloneable per tenant)."""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, blank=True, related_name="roles"
    )
    name = models.CharField(max_length=64)
    is_system = models.BooleanField(default=False)
    permissions = models.ManyToManyField(Permission, related_name="roles", blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="unique_role_per_company")
        ]

    def __str__(self):
        return self.name


class Membership(PlatformBaseModel):
    """User ↔ Company link with a role (multi-company from day one)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, related_name="memberships")
    job_title = models.CharField(max_length=120, blank=True)
    department = models.CharField(max_length=120, blank=True)
    employee_number = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, default="active")
    invited_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "company"], name="unique_membership")
        ]

    def __str__(self):
        return f"{self.user} @ {self.company} ({self.role})"


# ══════════════════════════════════════════════════════════════════════════════
# Company profile — the single source of truth every other module reads from.
#
# The rule this module exists to enforce: company identity is entered ONCE.
# Quotations, invoices, purchase orders, RFQs, delivery notes, safety files and
# PDF headers all read from here. Nothing re-asks for a VAT number.
#
# These hang off Company (a platform table, not a tenant table) so they are
# scoped by their FK rather than by the ambient tenant manager.
# ══════════════════════════════════════════════════════════════════════════════


class CompanyBankAccount(PlatformBaseModel):
    """Banking detail printed on invoices so a client can actually pay you.

    Multiple accounts are normal — operational, payroll, and a foreign-currency
    account for cross-border work. Exactly one is the default; that is the one
    documents use unless told otherwise.
    """

    class AccountType(models.TextChoices):
        CHEQUE = "cheque", "Cheque / Current"
        SAVINGS = "savings", "Savings"
        TRANSMISSION = "transmission", "Transmission"
        FOREIGN = "foreign", "Foreign currency"

    company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                related_name="bank_accounts")
    bank_name = models.CharField(max_length=120)
    account_name = models.CharField(max_length=160)
    account_number = models.CharField(max_length=40)
    branch_name = models.CharField(max_length=120, blank=True)
    branch_code = models.CharField(max_length=20, blank=True)
    account_type = models.CharField(max_length=16, choices=AccountType.choices,
                                    default=AccountType.CHEQUE)
    swift_code = models.CharField(max_length=16, blank=True)
    currency = models.CharField(max_length=8, default="ZAR")
    is_default = models.BooleanField(default=False)
    label = models.CharField(max_length=60, blank=True)   # "Operational", "Payroll"

    class Meta:
        ordering = ["-is_default", "bank_name"]

    def __str__(self):
        return f"{self.bank_name} · {self.masked_number}"

    @property
    def masked_number(self) -> str:
        """Account numbers are shown masked in lists — the full number belongs
        on the invoice, not on a screen someone is sharing."""
        digits = (self.account_number or "").strip()
        return f"••••{digits[-4:]}" if len(digits) > 4 else digits

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Exactly one default per company — setting one clears the rest.
        if self.is_default:
            CompanyBankAccount.objects.filter(company_id=self.company_id).exclude(
                pk=self.pk).update(is_default=False)


class CompanyContact(PlatformBaseModel):
    """A named person a client or supplier deals with — separate from platform
    Users, because the finance contact on your invoices may not have a login."""

    class Method(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        MOBILE = "mobile", "Mobile"
        WHATSAPP = "whatsapp", "WhatsApp"

    company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                related_name="contacts")
    full_name = models.CharField(max_length=160)
    job_title = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    mobile = models.CharField(max_length=32, blank=True)
    extension = models.CharField(max_length=12, blank=True)
    preferred_method = models.CharField(max_length=10, choices=Method.choices,
                                        default=Method.EMAIL)
    # The contact printed on outgoing documents when no other is specified.
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.job_title})" if self.job_title else self.full_name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_primary:
            CompanyContact.objects.filter(company_id=self.company_id).exclude(
                pk=self.pk).update(is_primary=False)


class CompanyCompliance(PlatformBaseModel):
    """Statutory registrations. In South African contracting these decide whether
    you may even bid: no CSD number, no state work; no CIDB grading, no
    construction tender above threshold; B-BBEE level moves your scorecard."""

    company = models.OneToOneField(Company, on_delete=models.CASCADE,
                                   related_name="compliance")
    vat_registered = models.BooleanField(default=False)
    income_tax_no = models.CharField(max_length=32, blank=True)
    paye_no = models.CharField(max_length=32, blank=True)
    uif_no = models.CharField(max_length=32, blank=True)
    coida_no = models.CharField(max_length=32, blank=True)       # Workmen's comp
    coida_expiry = models.DateField(null=True, blank=True)
    bbbee_level = models.CharField(max_length=16, blank=True)
    bbbee_expiry = models.DateField(null=True, blank=True)
    csd_supplier_no = models.CharField(max_length=32, blank=True)
    cidb_grading = models.CharField(max_length=16, blank=True)
    industry_certifications = models.JSONField(default=list, blank=True)
    iso_certifications = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name_plural = "company compliance"

    def __str__(self):
        return f"Compliance: {self.company}"

    def expiring(self, within_days=60):
        """Registrations lapsing soon — the ones that quietly disqualify you
        from a tender if nobody is watching."""
        from datetime import timedelta

        from django.utils import timezone
        horizon = timezone.localdate() + timedelta(days=within_days)
        out = []
        for label, value in (("COIDA letter of good standing", self.coida_expiry),
                             ("B-BBEE certificate", self.bbbee_expiry)):
            if value and value <= horizon:
                out.append({"name": label, "expires": value,
                            "expired": value < timezone.localdate()})
        return out


class CompanyDocument(PlatformBaseModel):
    """Supporting statutory documents — the pack a client asks for during
    vendor onboarding (CIPC certificate, tax clearance, B-BBEE affidavit…)."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                related_name="documents")
    name = models.CharField(max_length=160)
    doc_type = models.CharField(max_length=40, blank=True)
    file = models.FileField(upload_to="company_docs/%Y/")
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone
        return bool(self.expires_on and self.expires_on < timezone.localdate())


class CompanyBranding(PlatformBaseModel):
    """The visual assets documents are built from. Separate images because a
    letterhead, an email signature logo and a report cover are different shapes —
    forcing one file to serve all three is why generated documents look wrong."""

    company = models.OneToOneField(Company, on_delete=models.CASCADE,
                                   related_name="branding")
    email_logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    invoice_logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    report_logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    letterhead = models.ImageField(upload_to="branding/", blank=True, null=True)
    stamp = models.ImageField(upload_to="branding/", blank=True, null=True)
    signature = models.ImageField(upload_to="branding/", blank=True, null=True)
    seal = models.ImageField(upload_to="branding/", blank=True, null=True)

    class Meta:
        verbose_name_plural = "company branding"

    def __str__(self):
        return f"Branding: {self.company}"

    def for_document(self, kind: str):
        """The right logo for a document, falling back to the main company logo
        so a document is never logo-less just because one slot is empty."""
        chosen = {"invoice": self.invoice_logo, "report": self.report_logo,
                  "email": self.email_logo}.get(kind)
        return chosen or self.company.logo
