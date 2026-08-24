"""Work Management Engine (MODULE 8) — the operational heart of Lulaworks.

Everything in Lulaworks is Work. Work enters through many doors (RFQ, manual,
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
from django.utils import timezone

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
    MANUAL = "manual", "Manual job"
    PROJECT = "project", "Project"
    CUSTOMER_REQUEST = "customer_request", "Customer request"
    RECURRING = "recurring", "Recurring maintenance"
    INTERNAL = "internal", "Internal company job"
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
    # Expected work location — GPS check-ins on this task are measured against it.
    site_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    site_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
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
    #: What must be in place before this task may be completed — a list of keys
    #: from COMPLETION_REQUIREMENTS ("checklist", "report", "photo", "receipt").
    #: Empty = no gate. Configurable per task (a job-type template can seed it).
    completion_requirements = models.JSONField(default=list, blank=True)
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
    # A report FK lets photos/receipts/invoices attach to an operational
    # TaskReport (fuel receipt, supplier invoice, site photo) while still
    # carrying the owning task for one uniform file surface.
    report = models.ForeignKey("execution.TaskReport", on_delete=models.CASCADE,
                               null=True, blank=True, related_name="attachments")
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


class AttendanceEvent(TenantBaseModel):
    """A single time & attendance event for a worker — clock in/out, a break, or
    a site arrival/departure. Event-based (NOT continuous tracking): the app
    records a point in time (and optionally where), never a live trail.

    `occurred_at` is the moment the event happened on the device — supplied by
    the client so an event captured OFFLINE keeps its real time and syncs later;
    `created_at` (from the base model) is when the server received it. A
    correction request is an event with status=PENDING for a manager to review;
    a worker can never silently rewrite the record."""

    class Kind(models.TextChoices):
        CLOCK_IN = "clock_in", "Clock in"
        CLOCK_OUT = "clock_out", "Clock out"
        BREAK_START = "break_start", "Break start"
        BREAK_END = "break_end", "Break end"
        SITE_ARRIVAL = "site_arrival", "Site arrival"
        SITE_DEPARTURE = "site_departure", "Site departure"

    class Status(models.TextChoices):
        RECORDED = "recorded", "Recorded"
        PENDING = "pending", "Pending review"        # a correction request
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="attendance_events")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="attendance_events")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.RECORDED)
    source = models.CharField(max_length=16, default="mobile")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["company", "user", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.user} · {self.get_kind_display()} · {self.occurred_at:%Y-%m-%d %H:%M}"


class TaskMessage(TenantBaseModel):
    """A message on a task's chat — the business-context conversation between the
    people on a job. Scoped to the task's participants (its assignments) plus
    authorised managers; the backend enforces access, never the client.

    A SYSTEM message (author is null) records an operational event in the same
    thread — "Sipho started the task", "expense added" — so the conversation
    doubles as an auditable history."""

    class Kind(models.TextChoices):
        TEXT = "text", "Message"
        SYSTEM = "system", "System event"
        IMAGE = "image", "Photo"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.TEXT)
    body = models.TextField(blank=True)
    image = models.FileField(upload_to="chat/%Y/%m/", null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["company", "task", "created_at"]),
        ]

    def __str__(self):
        who = self.author or "system"
        return f"{who} · {self.task_id} · {self.body[:40]}"


# ─────────────────────────────────────────────────────────────────────────────
# WORK EXECUTION SYSTEM — a Task is an operational record, not a checkbox.
#
# Money set aside (allocations), evidence captured in the field (reports: fuel,
# material, time/attendance, progress — each GPS-stamped), and the purchase
# lines extracted from supplier invoices all hang off the Task, so a manager
# can open any Work and answer, at any moment: who is responsible, where the
# team is, how much was allocated, how much was actually spent, what materials
# were bought, and what receipts/photos exist — without spreadsheets or WhatsApp.
# ─────────────────────────────────────────────────────────────────────────────


class AllocationKind(models.TextChoices):
    TRANSPORT = "transport", "Transport"
    FUEL_ADVANCE = "fuel_advance", "Fuel advance"
    TOLL = "toll", "Toll fees"
    FOOD = "food", "Food allowance"
    ACCOMMODATION = "accommodation", "Accommodation"
    CASH_ADVANCE = "cash_advance", "Cash advance"
    PURCHASE_BUDGET = "purchase_budget", "Purchase budget"
    VEHICLE = "vehicle", "Company vehicle"
    EQUIPMENT = "equipment", "Equipment"
    PPE = "ppe", "PPE"
    OTHER = "other", "Other"


#: Allocation kinds that are logistics grants rather than cash to reconcile.
NON_MONETARY_ALLOCATIONS = frozenset({
    AllocationKind.VEHICLE, AllocationKind.EQUIPMENT, AllocationKind.PPE,
})


class AllocationStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    ISSUED = "issued", "Issued"
    RECONCILED = "reconciled", "Reconciled"
    CANCELLED = "cancelled", "Cancelled"


class TaskResourceAllocation(TenantBaseModel):
    """Operational resources set aside for a task BEFORE work starts — fuel,
    tolls, a cash advance, a company vehicle, PPE. Monetary allocations later
    reconcile against actual spend captured on TaskReports (`amount_spent` is a
    service-maintained rollup of the reports booked against this allocation)."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE,
                             related_name="cost_allocations")
    kind = models.CharField(max_length=20, choices=AllocationKind.choices)
    label = models.CharField(max_length=200, blank=True)
    is_monetary = models.BooleanField(default=True)
    amount_allocated = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=AllocationStatus.choices,
                              default=AllocationStatus.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["kind", "-created_at"]
        indexes = [models.Index(fields=["company", "task"])]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.amount_allocated}"

    @property
    def remaining(self) -> Decimal:
        return self.amount_allocated - self.amount_spent

    @property
    def is_over_budget(self) -> bool:
        return self.is_monetary and self.amount_spent > self.amount_allocated


class ReportKind(models.TextChoices):
    PROGRESS = "progress", "Progress update"
    TIME_EVENT = "time_event", "Time & attendance"
    FUEL = "fuel", "Fuel purchase"
    MATERIAL = "material", "Material purchase"
    EXPENSE = "expense", "Other expense"
    GENERAL = "general", "General / evidence"


#: Report kinds that book actual money against the task.
FINANCIAL_REPORT_KINDS = frozenset({
    ReportKind.FUEL, ReportKind.MATERIAL, ReportKind.EXPENSE,
})


class ExtractionStatus(models.TextChoices):
    NONE = "none", "No document"
    PENDING = "pending", "Awaiting review"
    CONFIRMED = "confirmed", "Confirmed"


class TaskReport(TenantBaseModel):
    """A single operational event on a task: a progress note, a time/attendance
    check-in, a fuel or material purchase — each stamped with who (employee),
    when (reported_at) and where (GPS). This is the row that turns a task into
    an auditable field record."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="reports")
    kind = models.CharField(max_length=12, choices=ReportKind.choices,
                            default=ReportKind.PROGRESS)
    title = models.CharField(max_length=200)
    event = models.CharField(max_length=80, blank=True)  # time_event label e.g. "Arrived at site"
    reported_at = models.DateTimeField(default=timezone.now)
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="task_reports")
    notes = models.TextField(blank=True)

    # ── Location (GPS verification) ─────────────────────────────────────────
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_accuracy_m = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    distance_m = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    location_flagged = models.BooleanField(default=False)  # beyond tolerance → review

    # ── Financial (fuel / material / expense) ───────────────────────────────
    supplier = models.CharField(max_length=200, blank=True)  # name as written on the receipt
    # Once a material receipt is confirmed, the seller is matched into (or added
    # to) the Suppliers database, and this points at that record — so the receipt
    # is traceable and future buys know where we bought this before.
    supplier_ref = models.ForeignKey(
        "procurement.Supplier", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="receipts")
    invoice_number = models.CharField(max_length=80, blank=True)
    document_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="ZAR")
    allocation = models.ForeignKey(TaskResourceAllocation, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="reports")
    extraction_status = models.CharField(max_length=10, choices=ExtractionStatus.choices,
                                         default=ExtractionStatus.NONE)

    class Meta:
        ordering = ["-reported_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "task", "kind"]),
            models.Index(fields=["company", "location_flagged"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.title}"

    @property
    def is_financial(self) -> bool:
        return self.kind in FINANCIAL_REPORT_KINDS

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class TaskReportItem(TenantBaseModel):
    """A line item extracted from a supplier invoice on a material-purchase
    report. Denormalises `task` so material rollups don't traverse reports."""

    report = models.ForeignKey(TaskReport, on_delete=models.CASCADE, related_name="items")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="material_items")
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    unit = models.CharField(max_length=20, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["report", "id"]
        indexes = [models.Index(fields=["company", "task"])]

    def __str__(self):
        return f"{self.quantity} × {self.description}"
