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
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    status = models.CharField(max_length=12, choices=CampaignStatus.choices,
                              default=CampaignStatus.DRAFT, db_index=True)

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
