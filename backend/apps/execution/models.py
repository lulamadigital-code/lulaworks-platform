"""Project Execution & Operations (PROJECT_EXECUTION.md / Module 9).

The insight that sets the architecture: a task isn't "done / not-done" — its
*readiness* is COMPUTED from real-world dependencies (predecessors, materials,
compliance), exactly as Module 8 computes project readiness. Execution hangs off
the `projects.Project` aggregate root created on award.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class WorkPackage(TenantBaseModel):
    """WBS node — a self-referential tree per project (unlimited depth). The flat
    task list generalises into Shutdown → Mechanical → {Pumps, Gearboxes} …"""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="work_packages"
    )
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    name = models.CharField(max_length=200)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["project", "position"]

    def __str__(self):
        return self.name


class TaskStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PLANNED = "planned", "Planned"
    READY = "ready", "Ready"
    IN_PROGRESS = "in_progress", "In progress"
    ON_HOLD = "on_hold", "On hold"
    BLOCKED = "blocked", "Blocked"
    AWAITING_INSPECTION = "awaiting_inspection", "Awaiting inspection"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class TaskPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class Task(TenantBaseModel):
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="tasks"
    )
    work_package = models.ForeignKey(
        WorkPackage, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=8, choices=TaskPriority.choices,
                                default=TaskPriority.NORMAL)
    status = models.CharField(max_length=20, choices=TaskStatus.choices,
                              default=TaskStatus.DRAFT)
    predecessors = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="successors"
    )
    # Real-world dependency inputs that drive computed readiness:
    blocks_on_compliance = models.BooleanField(default=True)  # needs the project gate open
    material_po = models.ForeignKey(
        "procurement.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    assignee = models.ForeignKey(
        "execution.Resource", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    estimated_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    actual_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    progress_pct = models.PositiveSmallIntegerField(default=0)
    blocked_reason = models.CharField(max_length=255, blank=True)  # computed cache

    class Meta:
        ordering = ["project", "created_at"]

    def __str__(self):
        return self.name


class Resource(TenantBaseModel):
    """An assignable resource. Employees/equipment carry a lightweight compliance
    profile (expiry dates) so allocation can refuse to mobilise someone/something
    that legally can't be on site (Module 9 §4 — the standout control)."""

    class Kind(models.TextChoices):
        EMPLOYEE = "employee", "Employee"
        EQUIPMENT = "equipment", "Equipment"
        VEHICLE = "vehicle", "Vehicle"
        SUBCONTRACTOR = "subcontractor", "Subcontractor"
        TOOL = "tool", "Tool"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=64, blank=True)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # cost rate
    # Compliance profile (employee: medical/induction; equipment: inspection):
    medical_expiry = models.DateField(null=True, blank=True)
    induction_expiry = models.DateField(null=True, blank=True)
    inspection_expiry = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    def compliance_issues(self, on_date) -> list[str]:
        """Expired credentials that would make this resource invalid on `on_date`."""
        issues = []
        for label, field in (("medical", self.medical_expiry),
                             ("induction", self.induction_expiry),
                             ("inspection", self.inspection_expiry)):
            if field and field < on_date:
                issues.append(f"{label} expired {field:%Y-%m-%d}")
        return issues


class ResourceAllocation(TenantBaseModel):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="allocations")
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="allocations"
    )
    task = models.ForeignKey(
        Task, on_delete=models.SET_NULL, null=True, blank=True, related_name="allocations"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.CharField(max_length=255, blank=True)
    # Non-empty when the allocation was forced past a warning (audited trail).
    override_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["resource", "start_date"]

    def __str__(self):
        return f"{self.resource} → {self.project} [{self.start_date}..{self.end_date}]"


class Timesheet(TenantBaseModel):
    """Clock time against a task — the source of ACTUAL labour hours that closes
    the Module 7 Pricing-Intelligence loop. Requires supervisor approval."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="timesheets")
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="timesheets")
    date = models.DateField()
    hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.resource} · {self.date} · {self.hours}h"

    @property
    def total_hours(self) -> Decimal:
        return self.hours + self.overtime_hours

    @property
    def labour_cost(self) -> Decimal:
        return (self.total_hours * self.resource.hourly_rate).quantize(Decimal("0.01"))
