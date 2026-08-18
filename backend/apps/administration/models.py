"""Administration frameworks (DATA_MODEL §8, §11, §12).

AuditLog (immutable, append-only), CompanySettings, NumberingRule + sequence,
FeatureFlag definition/override, NotificationPreference.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import PlatformBaseModel


class AuditLog(models.Model):
    """Immutable append-only audit record (DATA_MODEL §11). No update/delete —
    enforced by convention + the absence of any mutating code path. High-volume;
    partition by month at scale."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="+", null=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    action = models.CharField(max_length=100, db_index=True)
    entity_type = models.CharField(max_length=80, blank=True)
    entity_id = models.UUIDField(null=True, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "created_at"])]

    def __str__(self):
        return f"{self.action} {self.entity_type} @ {self.created_at:%Y-%m-%d %H:%M}"


class CompanySettings(PlatformBaseModel):
    """Per-company operational configuration (DATA_MODEL §12)."""

    company = models.OneToOneField(
        "identity.Company", on_delete=models.CASCADE, related_name="settings"
    )
    business_hours = models.JSONField(default=dict, blank=True)
    working_days = models.JSONField(default=list, blank=True)
    public_holidays = models.JSONField(default=list, blank=True)
    # Breakdown work does not respect business hours — this records whether the
    # company answers out-of-hours, and on what number.
    emergency_support = models.BooleanField(default=False)
    emergency_hours = models.CharField(max_length=80, blank=True)  # "24/7", "18:00-06:00"
    emergency_note = models.CharField(max_length=200, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=15)
    approval_rules = models.JSONField(default=dict, blank=True)
    compliance_defaults = models.JSONField(default=dict, blank=True)

    # ── Locale & financial defaults ───────────────────────────────────────
    # Currency and timezone live on Company (they identify the tenant); these
    # are presentation and accounting choices that documents and reports read.
    date_format = models.CharField(max_length=24, default="d M Y")
    number_format = models.CharField(max_length=16, default="1 234,56")  # SA default
    language = models.CharField(max_length=8, default="en")
    financial_year_start_month = models.PositiveSmallIntegerField(default=3)  # SA: March
    week_starts_on = models.PositiveSmallIntegerField(default=0)   # 0=Monday

    # ── AI configuration ──────────────────────────────────────────────────
    # Per-company control over what LulaAI may do. The human-approval boundary
    # is NOT configurable — these switch features on and off, they never grant
    # the AI authority to approve, award, send or pay.
    ai_provider = models.CharField(max_length=16, blank=True)   # blank = platform default
    ai_language = models.CharField(max_length=8, default="en")
    ai_response_style = models.CharField(max_length=16, default="concise")
    ai_suggestions_enabled = models.BooleanField(default=True)
    ai_summaries_enabled = models.BooleanField(default=True)
    ai_cost_estimation_enabled = models.BooleanField(default=True)
    ai_task_generation_enabled = models.BooleanField(default=True)
    ai_compliance_detection_enabled = models.BooleanField(default=True)

    # ── Document templates (which layout each document type uses) ─────────
    document_templates = models.JSONField(default=dict, blank=True)

    # ── Commercial document terms & conditions ────────────────────────────
    # The standard wording each company configures once and has auto-inserted
    # into every generated document, so nobody copies terms into individual
    # quotations, invoices or delivery notes. Stored as plain text; each line
    # becomes a paragraph on the PDF.
    quotation_terms = models.TextField(blank=True)
    invoice_terms = models.TextField(blank=True)
    delivery_terms = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "company settings"

    def __str__(self):
        return f"Settings: {self.company}"


class NumberingRule(PlatformBaseModel):
    """Configurable per-company, per-document-type numbering (DATA_MODEL §12).

    `fmt` is a Python format template with {prefix}, {yy}/{yyyy}, {seq} tokens,
    e.g. 'QT-{yyyy}-{seq:06d}' -> 'QT-2026-000001'.
    """

    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="numbering_rules"
    )
    doc_type = models.CharField(max_length=40)  # quotation, invoice, project, po...
    prefix = models.CharField(max_length=12, blank=True)
    fmt = models.CharField(max_length=64, default="{prefix}-{yyyy}-{seq:06d}")
    reset_yearly = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "doc_type"], name="unique_numbering_per_type"
            )
        ]

    def __str__(self):
        return f"{self.company} · {self.doc_type}"


class NumberSequence(models.Model):
    """Atomic counter behind NumberingRule. Locked on allocation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="+"
    )
    doc_type = models.CharField(max_length=40)
    year = models.PositiveSmallIntegerField()
    last_seq = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "doc_type", "year"], name="unique_sequence_per_type_year"
            )
        ]

    def __str__(self):
        return f"{self.doc_type} {self.year}: {self.last_seq}"


class FeatureFlagDefinition(PlatformBaseModel):
    """Catalogue of switchable features (DATA_MODEL §8)."""

    key = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True)
    default_enabled = models.BooleanField(default=False)

    def __str__(self):
        return self.key


class FeatureFlagOverride(PlatformBaseModel):
    """Per-company override of a feature flag."""

    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="feature_flags"
    )
    key = models.CharField(max_length=64)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "key"], name="unique_flag_per_company")
        ]


class NotificationPreference(PlatformBaseModel):
    """Per-user channel preferences (DATA_MODEL §12). Governs system
    notifications TO the user — not outbound messages to third parties."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs"
    )
    email = models.BooleanField(default=True)
    sms = models.BooleanField(default=False)
    whatsapp = models.BooleanField(default=False)
    push = models.BooleanField(default=True)
    in_app = models.BooleanField(default=True)
    daily_summary = models.BooleanField(default=False)
    digest_frequency = models.CharField(max_length=16, default="instant")

    def __str__(self):
        return f"Prefs: {self.user}"


class PlatformSettings(models.Model):
    """Singleton — platform-wide owner configuration the SaaS operator edits from
    the Console Settings page: branding, support addresses, and the defaults new
    tenants inherit. Always one row (pk=1); read/write via ``load()``."""

    # ── Branding & support ──────────────────────────────────────────────────
    platform_name = models.CharField(max_length=120, default="LulaWorks")
    support_email = models.EmailField(blank=True)
    sales_email = models.EmailField(blank=True)
    billing_email = models.EmailField(blank=True)
    reply_to = models.EmailField(blank=True)
    terms_url = models.URLField(blank=True)
    privacy_url = models.URLField(blank=True)

    # ── New-tenant defaults ─────────────────────────────────────────────────
    default_plan = models.ForeignKey(
        "billing.Plan", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    trial_days = models.PositiveSmallIntegerField(default=30)
    starting_ai_credits = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    auto_welcome = models.BooleanField(default=True)

    # ── Billing & tax defaults (currency + VAT applied to new tenants) ───────
    default_currency = models.CharField(max_length=3, default="ZAR")
    default_vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=15)
    invoice_prefix = models.CharField(max_length=12, blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "Platform settings"
        verbose_name_plural = "Platform settings"

    def __str__(self):
        return self.platform_name

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
