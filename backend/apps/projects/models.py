"""Project — the execution aggregate root (BUSINESS_WORKFLOW §1, RFQ-first).

A Project is created when a Quotation is AWARDED. Compliance (Module 8, Phase 5)
gates whether it may enter execution; Project Execution (Module 9, Phase 6) will
extend this same root with tasks, job cards and field ops.

The compliance gate is a HARD execution gate: a project stays `pending_compliance`
until Work Readiness passes (all mandatory compliance satisfied) or an authorised
override is recorded.
"""

from django.db import models

from apps.core.models import TenantBaseModel


class ProjectStatus(models.TextChoices):
    PENDING_COMPLIANCE = "pending_compliance", "Pending compliance"
    READY = "ready", "Ready for site"
    IN_EXECUTION = "in_execution", "In execution"
    COMPLETE = "complete", "Complete"
    CANCELLED = "cancelled", "Cancelled"


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

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_project_number")
        ]

    def __str__(self):
        return self.number
