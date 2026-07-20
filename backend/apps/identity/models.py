"""Identity, tenancy & RBAC (DATA_MODEL §4-6).

Company = tenant. User = platform identity (email login), linked to companies
via Membership (multi-company ready). Access via a granular Permission engine —
never `is_admin`.
"""

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from apps.core.models import PlatformBaseModel, UUIDModel

from .managers import UserManager


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
    country = models.CharField(max_length=64, default="South Africa")
    province = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=64, blank=True)
    timezone = models.CharField(max_length=64, default="Africa/Johannesburg")
    currency = models.CharField(max_length=8, default="ZAR")
    logo = models.ImageField(upload_to="company_logos/", blank=True, null=True)
    brand_primary = models.CharField(max_length=9, blank=True)
    brand_secondary = models.CharField(max_length=9, blank=True)

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
