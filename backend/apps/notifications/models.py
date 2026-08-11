"""Email & Notification platform — the record of every message LulaWorks sends.

Design intent (the whole reason this is a platform service, not per-module code):
every outbound email in the system — CRM, HR invites, auth resets, quotations,
invoices, billing receipts, task alerts — is created through one service and
logged here. That gives a single audit trail, one retry path, one place to
change branding or swap providers, and one history an admin can inspect.

The log is PLATFORM-level (not tenant-scoped) with an OPTIONAL company, because
some emails have no tenant context yet — a password reset or an invitation is
sent before the recipient belongs to any company.
"""

from django.conf import settings
from django.db import models

from apps.core.models import PlatformBaseModel


class EmailStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


#: Broad buckets, so history can be filtered and (later) preferences applied
#: per category. Not the specific template — that's `template`.
class EmailCategory(models.TextChoices):
    ACCOUNT = "account", "Account"          # welcome, invite, verify, activate
    SECURITY = "security", "Security"        # password reset, new login
    BILLING = "billing", "Billing"          # receipts, trial, payment
    DOCUMENT = "document", "Document"        # quotation/invoice/DN/PO/RFQ
    TASK = "task", "Task & approval"
    CRM = "crm", "Customer communication"
    SYSTEM = "system", "System"
    MARKETING = "marketing", "Marketing"


class EmailLog(PlatformBaseModel):
    """One outbound email: who it went to, what it was, whether it arrived, and
    the exact rendered body (so history shows precisely what was sent)."""

    # Tenant context is optional — auth/invite emails predate company membership.
    company = models.ForeignKey("identity.Company", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")

    to_email = models.EmailField()
    to_name = models.CharField(max_length=160, blank=True)
    cc = models.JSONField(default=list, blank=True)          # list[str]
    reply_to = models.EmailField(blank=True)

    subject = models.CharField(max_length=255)
    template = models.CharField(max_length=64)               # e.g. "welcome"
    category = models.CharField(max_length=16, choices=EmailCategory.choices,
                                default=EmailCategory.SYSTEM)

    # The rendered message is stored so the async worker just sends it, and so
    # the audit trail is the real content, not a promise of it.
    html_body = models.TextField(blank=True)
    text_body = models.TextField(blank=True)
    #: Attachment filenames only (never the bytes) — for the history view.
    attachment_names = models.JSONField(default=list, blank=True)
    #: How to REBUILD attachments at delivery time: [{"kind","id","name"}]. The
    #: worker regenerates each document (e.g. a quotation PDF) from its source
    #: record, so large bytes never ride in the task queue and the attachment is
    #: always the current version. See notifications.attachments.
    attachment_spec = models.JSONField(default=list, blank=True)

    # Optional business record this email is about (Quotation, Invoice, …).
    entity_type = models.CharField(max_length=48, blank=True)
    entity_id = models.UUIDField(null=True, blank=True)

    status = models.CharField(max_length=12, choices=EmailStatus.choices,
                              default=EmailStatus.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    provider = models.CharField(max_length=64, blank=True)   # the backend used
    sent_at = models.DateTimeField(null=True, blank=True)

    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.template} → {self.to_email} [{self.status}]"

    @property
    def is_sent(self) -> bool:
        return self.status == EmailStatus.SENT


class SmsLog(PlatformBaseModel):
    """One outbound SMS. The same audit shape as EmailLog but for the SMS
    channel — so the message history shows email and SMS side by side, and SMS
    gets the same retry + provider-swap the platform gives email. SMS is
    reserved for time-critical operational alerts to field staff (see
    dispatch); it is opt-in per user (NotificationPreference.sms, default off)
    because it costs per message."""

    company = models.ForeignKey("identity.Company", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")
    to_number = models.CharField(max_length=32)
    body = models.CharField(max_length=480)          # ~3 SMS segments max
    category = models.CharField(max_length=16, choices=EmailCategory.choices,
                                default=EmailCategory.TASK)

    entity_type = models.CharField(max_length=48, blank=True)
    entity_id = models.UUIDField(null=True, blank=True)

    status = models.CharField(max_length=12, choices=EmailStatus.choices,
                              default=EmailStatus.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    provider = models.CharField(max_length=32, blank=True)
    provider_message_id = models.CharField(max_length=64, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "-created_at"])]

    def __str__(self):
        return f"SMS → {self.to_number} [{self.status}]"
