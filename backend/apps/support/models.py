"""LulaWorks Support Center — the customer's relationship with LulaWorks Support
(distinct from CRM, which is the customer's relationship with THEIR clients).

Tickets are tenant-scoped (a company only ever sees its own); platform support
staff read across tenants via `system_scope()`. Threaded messages carry an
internal/external flag so support's private notes never reach the customer.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


def support_upload_path(instance, filename):
    cid = getattr(instance, "company_id", None) or "platform"
    return f"support/{cid}/{filename}"


class TicketCategory(models.TextChoices):
    ACCOUNT = "account", "Account & Login"
    CRM = "crm", "CRM"
    QUOTATIONS = "quotations", "Quotations"
    JOBS = "jobs", "Jobs"
    TASKS = "tasks", "Tasks"
    PROCUREMENT = "procurement", "Procurement"
    SUPPLIERS = "suppliers", "Suppliers"
    INVOICES = "invoices", "Invoices"
    DELIVERY_NOTES = "delivery_notes", "Delivery Notes"
    BILLING = "billing", "Payments & Billing"
    AI = "ai", "AI"
    WHATSAPP = "whatsapp", "WhatsApp"
    EMAIL = "email", "Email"
    MOBILE = "mobile", "Mobile App"
    TECHNICAL = "technical", "Technical Problem"
    OTHER = "other", "Other"


class TicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In Progress"
    WAITING_CUSTOMER = "waiting_customer", "Waiting for Customer"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


#: Statuses a customer still counts as "an open matter".
OPEN_STATUSES = {TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.WAITING_CUSTOMER}


class SupportTicket(TenantBaseModel):
    number = models.CharField(max_length=16, unique=True, editable=False, db_index=True)
    subject = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=TicketCategory.choices,
                                default=TicketCategory.OTHER)
    priority = models.CharField(max_length=10, choices=TicketPriority.choices,
                                default=TicketPriority.NORMAL)
    status = models.CharField(max_length=20, choices=TicketStatus.choices,
                              default=TicketStatus.OPEN, db_index=True)
    description = models.TextField()

    related_module = models.CharField(max_length=40, blank=True)
    related_ref = models.CharField(max_length=120, blank=True)

    # Platform support agent handling the ticket (a LulaWorks staff user).
    assigned_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    # Safe technical context attached from the error monitor (no secrets/traces).
    error_reference = models.CharField(max_length=24, blank=True)
    error_context = models.JSONField(default=dict, blank=True)

    # SLA milestones — recorded now so SLAs can be layered on later.
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-last_activity_at"]
        indexes = [models.Index(fields=["company", "status"])]

    def __str__(self):
        return f"{self.number} — {self.subject}"

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES


class SupportMessage(TenantBaseModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    body = models.TextField()
    # Internal notes are visible only to LulaWorks support, never to the customer.
    is_internal = models.BooleanField(default=False)
    # True when written by LulaWorks support (vs the customer).
    from_support = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"msg on {self.ticket_id}"


class SupportAttachment(TenantBaseModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(SupportMessage, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="attachments")
    file = models.FileField(upload_to=support_upload_path)
    name = models.CharField(max_length=200)
    size = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name
