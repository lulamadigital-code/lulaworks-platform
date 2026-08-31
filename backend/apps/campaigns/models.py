"""Marketing module — Phase 1 (foundation).

A tenant's Marketing runs ON the CRM, never a second customer database:
* a :class:`Segment` is a saved, reusable *filter* over CRM leads or customers —
  membership is evaluated live (see :mod:`apps.campaigns.services`), so it always
  reflects reality;
* a :class:`Campaign` targets a segment over a channel. Phase 1 records the
  campaign, audience and content; the channel sending (email/WhatsApp) and the
  live result metrics arrive in later phases — the metric fields exist now so the
  schema and analytics are ready.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel

AUDIENCE_CHOICES = [("leads", "Leads"), ("customers", "Customers")]


class Segment(TenantBaseModel):
    """A saved slice of the CRM (leads or customers) matching a set of filters."""

    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    audience = models.CharField(max_length=12, choices=AUDIENCE_CHOICES,
                                default="leads")
    # Filter criteria as {key: value}; interpreted by services.segment_queryset.
    criteria = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["company", "audience"])]

    def __str__(self):
        return self.name


class CampaignChannel(models.TextChoices):
    EMAIL = "email", "Email"
    WHATSAPP = "whatsapp", "WhatsApp"
    OTHER = "other", "Other / manual"


class CampaignStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Campaign(TenantBaseModel):
    """A marketing campaign to a segment over a channel."""

    name = models.CharField(max_length=160)
    objective = models.CharField(max_length=255, blank=True)
    channel = models.CharField(max_length=12, choices=CampaignChannel.choices,
                               default=CampaignChannel.EMAIL)
    segment = models.ForeignKey(Segment, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="campaigns")
    subject = models.CharField(max_length=200, blank=True)   # email subject / title
    content = models.TextField(blank=True)                   # message body / template
    # WhatsApp only: the name of the Meta-approved message template to send. When
    # set, the campaign sends as that template; blank sends `content` as text
    # (only valid inside a 24h customer-care window — see whatsapp.py).
    wa_template_name = models.CharField(max_length=120, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    status = models.CharField(max_length=12, choices=CampaignStatus.choices,
                              default=CampaignStatus.DRAFT, db_index=True)
    # What the campaign cost to run (ad spend, list, agency…) — drives ROI.
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Result metrics — populated by the channel phases; kept here so the schema
    # and analytics are ready before sending is wired.
    sent = models.PositiveIntegerField(default=0)
    delivered = models.PositiveIntegerField(default=0)
    opened = models.PositiveIntegerField(default=0)
    clicked = models.PositiveIntegerField(default=0)
    replied = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    unsubscribed = models.PositiveIntegerField(default=0)
    converted = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"])]

    def __str__(self):
        return self.name

    @property
    def is_editable(self) -> bool:
        return self.status in (CampaignStatus.DRAFT, CampaignStatus.SCHEDULED)

    @property
    def can_send(self) -> bool:
        return (self.channel in (CampaignChannel.EMAIL, CampaignChannel.WHATSAPP)
                and self.segment_id is not None
                and self.status in (CampaignStatus.DRAFT, CampaignStatus.SCHEDULED,
                                    CampaignStatus.RUNNING))


class CampaignSend(TenantBaseModel):
    """One recipient of one campaign — the per-person send + engagement record.

    Ties a campaign to who it went to (and their CRM origin), the underlying
    EmailLog for delivery status, and open tracking. Unique per (campaign, email)
    so a re-send never double-mails the same address."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (unsubscribed)"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE,
                                 related_name="sends")
    channel = models.CharField(max_length=12, default="email")   # email | whatsapp
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)          # WhatsApp destination
    name = models.CharField(max_length=160, blank=True)
    lead = models.ForeignKey("customers.Lead", on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="+")
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="+")
    email_log = models.ForeignKey("notifications.EmailLog", on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name="+")
    wa_message_id = models.CharField(max_length=128, blank=True, db_index=True)  # Meta message id
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.PENDING)
    # For email = "opened"; for WhatsApp = "read" (reused so analytics stay unified).
    opened = models.BooleanField(default=False)
    opened_at = models.DateTimeField(null=True, blank=True)
    # WhatsApp engagement, fed by the delivery webhook.
    delivered = models.BooleanField(default=False)
    replied = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["campaign", "email", "phone"],
                                    name="unique_campaign_recipient"),
        ]
        indexes = [models.Index(fields=["company", "campaign"])]

    def __str__(self):
        return f"{self.campaign_id} → {self.email or self.phone}"


class WhatsAppConnection(TenantBaseModel):
    """A company's own WhatsApp Business (Meta Cloud API) connection. Marketing
    WhatsApp always sends from the tenant's own number — never a shared Lulaworks
    one — so each company connects its own here (owner/admin only)."""

    company = models.OneToOneField("identity.Company", on_delete=models.CASCADE,
                                   related_name="whatsapp_connection")
    phone_number_id = models.CharField(max_length=64, blank=True)   # Meta phone number id
    waba_id = models.CharField(max_length=64, blank=True)           # WhatsApp Business Acct id
    display_number = models.CharField(max_length=32, blank=True)    # human-readable, e.g. +27…
    # SECURITY: a long-lived access token. Stored per-tenant (can't be ENV like the
    # AI keys). Never rendered back to the browser; masked in the UI.
    access_token = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        verbose_name = "WhatsApp connection"

    def __str__(self):
        return f"WhatsApp {self.display_number or self.phone_number_id}"

    @property
    def is_connected(self) -> bool:
        return bool(self.is_active and self.phone_number_id and self.access_token)


class EmailSuppression(TenantBaseModel):
    """A marketing-email opt-out. A recipient who unsubscribes is suppressed for
    the company: marketing sends skip them, forever, unless removed. Transactional
    email (invoices, resets) is never affected."""

    email = models.EmailField()
    reason = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "email"],
                                    name="unique_email_suppression"),
        ]

    def __str__(self):
        return self.email
