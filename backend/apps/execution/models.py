"""Work Management Engine (MODULE 8) — the operational heart of LulaWorks.

Everything in LulaWorks is Work. Work enters through many doors (RFQ, manual,
recurring maintenance, breakdown callout, internal job …) but every piece of it
flows through ONE engine: one hierarchy, one lifecycle, one team model, one
dependency model, one comment/file/notification/AI surface.

    Company → Workspace → Work ─┬─ Standalone Work
                                └─ Project → Phase → Task → Subtask → Checklist

The insight that sets the architecture: a task isn't "done / not-done" — its
*readiness* is COMPUTED from real-world dependencies (predecessors, materials,
compliance), exactly as Module 5 computes project readiness.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class Workspace(TenantBaseModel):
    """A container for work — a division, branch, or department. Every company
    gets a default workspace so single-team businesses never have to think about
    it, while enterprise contractors can segment thousands of work items."""

    name = models.CharField(max_length=120)
    key = models.SlugField(max_length=40)
    description = models.CharField(max_length=255, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "key"], name="uniq_workspace_key"),
        ]

    def __str__(self):
        return self.name


class Phase(TenantBaseModel):
    """A named band of work inside a project (Planning → Procurement →
    Compliance → Execution → Commissioning → Commercial → Closure). Users add,
    rename, reorder and remove phases; tasks hang off them."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="phases"
    )
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["project", "position"]

    def __str__(self):
        return self.name

    @property
    def progress_pct(self) -> int:
        """Rolled up from the phase's tasks."""
        rows = [t.progress_pct for t in self.tasks.exclude(status=TaskStatus.CANCELLED)]
        return round(sum(rows) / len(rows)) if rows else 0


DEFAULT_PHASES = [
    "Planning", "Procurement", "Compliance", "Execution",
    "Commissioning", "Commercial", "Closure",
]


class WorkPackage(TenantBaseModel):
    """WBS node — a self-referential tree per project (unlimited depth), for
    contractors who structure work more deeply than phases allow."""

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
    """The default lifecycle. Companies extend/rename it via StatusDefinition —
    these keys stay as the canonical engine states the services reason about."""

    DRAFT = "draft", "Draft"
    READY = "ready", "Ready"
    ASSIGNED = "assigned", "Assigned"
    ACCEPTED = "accepted", "Accepted"
    IN_PROGRESS = "in_progress", "In progress"
    WAITING = "waiting", "Waiting"
    BLOCKED = "blocked", "Blocked"
    QUALITY_CHECK = "quality_check", "Quality check"
    CLIENT_SIGNOFF = "client_signoff", "Client sign-off"
    COMPLETED = "completed", "Completed"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


#: States the engine treats as finished — readiness is never recomputed for them.
TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.CLOSED, TaskStatus.CANCELLED}
#: States a human put the task in deliberately — the engine won't override them.
MANUAL_STATUSES = {TaskStatus.WAITING, TaskStatus.QUALITY_CHECK,
                   TaskStatus.CLIENT_SIGNOFF, TaskStatus.ACCEPTED}
#: Forward path the UI uses to offer "the next sensible step".
LIFECYCLE_ORDER = [
    TaskStatus.DRAFT, TaskStatus.READY, TaskStatus.ASSIGNED, TaskStatus.ACCEPTED,
    TaskStatus.IN_PROGRESS, TaskStatus.WAITING, TaskStatus.BLOCKED,
    TaskStatus.QUALITY_CHECK, TaskStatus.CLIENT_SIGNOFF, TaskStatus.COMPLETED,
    TaskStatus.CLOSED,
]


class TaskPriority(models.TextChoices):
    CRITICAL = "critical", "Critical"
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"
    PLANNING = "planning", "Planning"


class RiskLevel(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class WorkOrigin(models.TextChoices):
    """How the work started — the ONLY real difference between jobs. Everything
    else flows through the same engine (the unified workflow)."""

    RFQ = "rfq", "RFQ / Tender"
    MANUAL = "manual", "Manual work"
    PROJECT = "project", "Project"
    CUSTOMER_REQUEST = "customer_request", "Customer request"
    RECURRING = "recurring", "Recurring maintenance"
    INTERNAL = "internal", "Internal company work"
    BREAKDOWN = "breakdown", "Breakdown / emergency callout"
    PREVENTATIVE = "preventative", "Preventative maintenance"


class StatusDefinition(TenantBaseModel):
    """Per-company status customisation. `key` maps onto a canonical TaskStatus
    so the engine keeps working; `label` and `colour` are what users see."""

    class Category(models.TextChoices):
        OPEN = "open", "Open"
        ACTIVE = "active", "Active"
        STUCK = "stuck", "Stuck"
        DONE = "done", "Done"

    key = models.CharField(max_length=32)
    label = models.CharField(max_length=60)
    colour = models.CharField(max_length=16, default="#c4c7d0")
    category = models.CharField(max_length=8, choices=Category.choices,
                                default=Category.OPEN)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["company", "key"], name="uniq_status_key"),
        ]

    def __str__(self):
        return self.label


class Task(TenantBaseModel):
    """A unit of Work. It may belong to a Project (large, phased, gated jobs) or
    stand alone (a small job — "Replace faulty DB board"); the only difference is
    how it started (`origin`). One task engine serves both, so a two-person shop
    and a mine shutdown use the same primitives."""

    workspace = models.ForeignKey(
        Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, null=True, blank=True,
        related_name="tasks",
    )
    phase = models.ForeignKey(
        Phase, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    origin = models.CharField(max_length=20, choices=WorkOrigin.choices,
                              default=WorkOrigin.MANUAL)
    # Standalone billable work carries its own client (project work bills via the
    # project); internal work isn't billed.
    is_billable = models.BooleanField(default=False)
    client_name = models.CharField(max_length=255, blank=True)
    site = models.CharField(max_length=255, blank=True)
    department = models.CharField(max_length=120, blank=True)
    work_package = models.ForeignKey(
        WorkPackage, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=TaskPriority.choices,
                                default=TaskPriority.MEDIUM)
    status = models.CharField(max_length=20, choices=TaskStatus.choices,
                              default=TaskStatus.DRAFT)
    risk_level = models.CharField(max_length=8, choices=RiskLevel.choices,
                                  default=RiskLevel.LOW)
    labels = models.JSONField(default=list, blank=True)
    predecessors = models.ManyToManyField(
        "self", symmetrical=False, blank=True, through="TaskDependency",
        through_fields=("to_task", "from_task"), related_name="successors",
    )
    # Real-world dependency inputs that drive computed readiness:
    blocks_on_compliance = models.BooleanField(default=True)  # needs the project gate open
    material_po = models.ForeignKey(
        "procurement.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    assignee = models.ForeignKey(
        "execution.Resource", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks",
    )
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    estimated_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    actual_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    progress_pct = models.PositiveSmallIntegerField(default=0)
    blocked_reason = models.CharField(max_length=255, blank=True)  # computed cache
    ai_summary = models.TextField(blank=True)  # LulaAI draft — advisory only

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "due_date"]),
        ]

    def __str__(self):
        return self.name

    @property
    def is_standalone(self) -> bool:
        return self.project_id is None

    @property
    def is_open(self) -> bool:
        return self.status not in TERMINAL_STATUSES

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        return bool(self.due_date and self.is_open
                    and self.due_date < timezone.localdate())

    @property
    def owner(self):
        """The single accountable person (Assignment role OWNER)."""
        row = self.assignments.filter(role=Assignment.Role.OWNER).first()
        return row.user if row else None

    def team(self, role=None):
        qs = self.assignments.select_related("user")
        if role:
            qs = qs.filter(role=role)
        return [a.user for a in qs]

    def checklist_progress(self) -> int:
        """Progress rolled up from checklist items — the ground truth on site."""
        items = list(self.checklist_items.all())
        if not items:
            return self.progress_pct
        return round(100 * sum(1 for i in items if i.is_done) / len(items))


class Assignment(TenantBaseModel):
    """Work is a team sport — a work item is never limited to one assignee.
    One OWNER is accountable; EXECUTORs do the work; WATCHERs are notified but
    cannot modify; APPROVERs sign off completion, compliance and commercials."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EXECUTOR = "executor", "Execution team"
        WATCHER = "watcher", "Watcher"
        APPROVER = "approver", "Approver"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="work_assignments")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.EXECUTOR)

    class Meta:
        ordering = ["role", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["task", "user", "role"],
                                    name="uniq_task_user_role"),
        ]

    def __str__(self):
        return f"{self.user} · {self.get_role_display()}"


class Subtask(TenantBaseModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="subtasks")
    name = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="+")
    due_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return self.name


class ChecklistItem(TenantBaseModel):
    """The smallest unit — what the person on site actually ticks off."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist_items")
    subtask = models.ForeignKey(Subtask, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="checklist_items")
    label = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return self.label


class TaskDependency(TenantBaseModel):
    """A typed link between two tasks. Classic scheduling types (FS/SS/FF/SF)
    sit alongside the real-world waits a contractor actually hits — approval,
    delivery, compliance — so "why is this blocked?" always has a real answer."""

    class Kind(models.TextChoices):
        FINISH_TO_START = "fs", "Finish to start"
        START_TO_START = "ss", "Start to start"
        FINISH_TO_FINISH = "ff", "Finish to finish"
        START_TO_FINISH = "sf", "Start to finish"
        BLOCKED_UNTIL = "blocked_until", "Blocked until"
        WAITING_APPROVAL = "waiting_approval", "Waiting for approval"
        WAITING_DELIVERY = "waiting_delivery", "Waiting for delivery"
        WAITING_COMPLIANCE = "waiting_compliance", "Waiting for compliance"

    from_task = models.ForeignKey(Task, on_delete=models.CASCADE,
                                  related_name="outgoing_dependencies")
    to_task = models.ForeignKey(Task, on_delete=models.CASCADE,
                                related_name="incoming_dependencies")
    kind = models.CharField(max_length=20, choices=Kind.choices,
                            default=Kind.FINISH_TO_START)
    lag_days = models.SmallIntegerField(default=0)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["from_task", "to_task", "kind"],
                                    name="uniq_task_dependency"),
        ]

    def __str__(self):
        return f"{self.from_task} → {self.to_task} ({self.get_kind_display()})"


class Comment(TenantBaseModel):
    """Threaded conversation on a work item. Internal comments stay inside the
    company; customer-visible ones are what a client portal would ever show."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="replies")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, related_name="+")
    body = models.TextField()
    is_internal = models.BooleanField(default=True)
    mentions = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                      related_name="comment_mentions")

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author}: {self.body[:40]}"


class Attachment(TenantBaseModel):
    """A file on a work item. Versioned — re-uploading the same filename bumps
    the version rather than silently replacing evidence."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="attachments")
    file = models.FileField(upload_to="work/%Y/%m/")
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    version = models.PositiveSmallIntegerField(default=1)
    kind = models.CharField(max_length=20, default="document")  # document|photo|video|voice

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_name} v{self.version}"


class AutomationRule(TenantBaseModel):
    """"When X happens, do Y" — configured per company, never hard-coded.
    Automations move information; they never approve, award, send or pay."""

    class Trigger(models.TextChoices):
        TASK_COMPLETED = "task_completed", "When a task is completed"
        TASK_BLOCKED = "task_blocked", "When a task becomes blocked"
        TASK_OVERDUE = "task_overdue", "When a task goes overdue"
        STATUS_CHANGED = "status_changed", "When status changes"
        COMMENT_ADDED = "comment_added", "When a comment is added"

    class Action(models.TextChoices):
        NOTIFY_OWNER = "notify_owner", "Notify the owner"
        NOTIFY_APPROVERS = "notify_approvers", "Notify the approvers"
        NOTIFY_WATCHERS = "notify_watchers", "Notify the watchers"
        SET_STATUS = "set_status", "Set status"
        UNLOCK_SUCCESSORS = "unlock_successors", "Recompute successor readiness"

    name = models.CharField(max_length=140)
    trigger = models.CharField(max_length=24, choices=Trigger.choices)
    conditions = models.JSONField(default=dict, blank=True)
    action = models.CharField(max_length=24, choices=Action.choices)
    params = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Notification(TenantBaseModel):
    """In-app notification. Email/push/SMS are delivery channels layered on top
    of the same row, so "what did we tell this person?" has one answer."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="notifications")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="notifications")
    verb = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=400, blank=True)
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "user", "is_read"])]

    def __str__(self):
        return self.title


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
