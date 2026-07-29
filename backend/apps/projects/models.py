"""Project — the execution aggregate root (BUSINESS_WORKFLOW §1, RFQ-first).

A Project is created when a Quotation is AWARDED. Compliance (Module 8, Phase 5)
gates whether it may enter execution; Project Execution (Module 9, Phase 6) will
extend this same root with tasks, job cards and field ops.

The compliance gate is a HARD execution gate: a project stays `pending_compliance`
until Work Readiness passes (all mandatory compliance satisfied) or an authorised
override is recorded.
"""

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class ProjectStatus(models.TextChoices):
    PENDING_COMPLIANCE = "pending_compliance", "Pending compliance"
    READY = "ready", "Ready for site"
    IN_EXECUTION = "in_execution", "In execution"
    COMPLETE = "complete", "Complete"
    CANCELLED = "cancelled", "Cancelled"


class ProjectPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class Project(TenantBaseModel):
    number = models.CharField(max_length=32)
    quotation = models.ForeignKey(
        "quotes.Quotation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="projects",
    )
    title = models.CharField(max_length=255, blank=True)
    client_name = models.CharField(max_length=255)
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="projects",
    )
    site = models.CharField(max_length=255, blank=True)
    mine = models.CharField(max_length=255, blank=True)
    work_type = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=24, choices=ProjectStatus.choices, default=ProjectStatus.PENDING_COMPLIANCE
    )
    awarded_at = models.DateTimeField(null=True, blank=True)

    # ── Work Details (operational header) ────────────────────────────────────
    # Who owns delivery, when it must run, what it may cost, and how far a field
    # GPS check-in may drift from the expected site before it's flagged.
    work_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="managed_projects",
    )
    priority = models.CharField(
        max_length=8, choices=ProjectPriority.choices, default=ProjectPriority.NORMAL
    )
    planned_start = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    budget_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gps_tolerance_m = models.PositiveIntegerField(default=500)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_project_number")
        ]

    def __str__(self):
        return self.number
