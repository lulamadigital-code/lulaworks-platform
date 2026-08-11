"""Work Management Engine services (MODULE 8).

One engine for every kind of work. Computed task readiness (mirrors the project
gate) now understands TYPED dependencies — the real-world waits a contractor
actually hits (approval, delivery, compliance) alongside the classic scheduling
links. Around it sit the team model, the hierarchy roll-up, comments, files,
notifications and automations, plus the Module 9 carry-overs: compliance-aware
resource allocation, the actuals capture that closes the Pricing-Intelligence
loop, the composite health score, and the customer/internal report split.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.compliance.services import can_start, recompute_readiness
from apps.core.events import publish

from .models import (
    MANUAL_STATUSES,
    TERMINAL_STATUSES,
    Assignment,
    ResourceAllocation,
    Task,
    TaskDependency,
    TaskStatus,
    Timesheet,
)

TWO = Decimal("0.01")

#: A predecessor counts as satisfied once it reaches one of these.
DONE_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CLOSED)

#: Dependency kinds satisfied by the predecessor *starting* rather than finishing.
START_KINDS = {TaskDependency.Kind.START_TO_START, TaskDependency.Kind.START_TO_FINISH}

#: Human phrasing per dependency kind, so "why is this blocked?" reads plainly.
_DEP_PHRASE = {
    TaskDependency.Kind.FINISH_TO_START: "waiting for {name} to finish",
    TaskDependency.Kind.FINISH_TO_FINISH: "waiting for {name} to finish",
    TaskDependency.Kind.START_TO_START: "waiting for {name} to start",
    TaskDependency.Kind.START_TO_FINISH: "waiting for {name} to start",
    TaskDependency.Kind.BLOCKED_UNTIL: "blocked until {name}",
    TaskDependency.Kind.WAITING_APPROVAL: "waiting for approval of {name}",
    TaskDependency.Kind.WAITING_DELIVERY: "waiting for delivery on {name}",
    TaskDependency.Kind.WAITING_COMPLIANCE: "waiting for compliance on {name}",
}


# ── Computed task readiness (the core insight) ────────────────────────────────

def _dependency_reasons(task) -> list[str]:
    """Unsatisfied incoming dependencies, phrased for a human."""
    reasons = []
    for dep in task.incoming_dependencies.select_related("from_task"):
        pred = dep.from_task
        if dep.kind in START_KINDS:
            satisfied = pred.status not in (TaskStatus.DRAFT, TaskStatus.READY,
                                            TaskStatus.ASSIGNED, TaskStatus.ACCEPTED)
        else:
            satisfied = pred.status in DONE_STATUSES
        if not satisfied:
            phrase = _DEP_PHRASE.get(dep.kind, "waiting on {name}")
            reasons.append(phrase.format(name=pred.name))
    return reasons


def compute_task_readiness(task) -> tuple[str, str]:
    """Compute a task's readiness from real-world dependencies. Returns
    (status, blocked_reason). A task is READY only when its dependencies are
    satisfied, the compliance gate is open (if required), and its materials are
    delivered — task-level readiness mirroring the project gate."""
    if task.status in TERMINAL_STATUSES:
        return task.status, ""

    reasons = _dependency_reasons(task)

    # Standalone work (no project) has no compliance gate — only project work does.
    if task.blocks_on_compliance and task.project_id and not can_start(task.project):
        reasons.append("project not compliance-ready")

    if task.material_po_id:
        outstanding = sum((line.outstanding for line in task.material_po.lines.all()),
                          Decimal("0"))
        if outstanding > 0:
            reasons.append(f"materials not delivered (PO {task.material_po.number})")

    if reasons:
        return TaskStatus.BLOCKED, "; ".join(reasons)[:255]
    # No blockers: keep an already-active state; otherwise the task is Ready.
    if task.status == TaskStatus.IN_PROGRESS:
        return TaskStatus.IN_PROGRESS, ""
    if task.status == TaskStatus.ASSIGNED:
        return TaskStatus.ASSIGNED, ""
    return TaskStatus.READY, ""


def refresh_task_status(task, *, save=True) -> Task:
    """Recompute and persist a task's readiness. Terminal states and states a
    human deliberately chose (waiting / quality check / sign-off / accepted) are
    left alone — the engine advises, it doesn't overrule people."""
    if task.status in TERMINAL_STATUSES or task.status in MANUAL_STATUSES:
        return task
    status, reason = compute_task_readiness(task)
    if (status, reason) != (task.status, task.blocked_reason):
        task.status = status
        task.blocked_reason = reason
        if save:
            task.save(update_fields=["status", "blocked_reason", "updated_at"])
    return task


def start_task(task, user) -> Task:
    """Begin a task — only if it computes READY. Moves the project into execution
    on first start. The compliance gate is enforced here (hard execution gate)."""
    status, reason = compute_task_readiness(task)
    if status != TaskStatus.READY:
        raise ValueError(f"Task is not ready: {reason}")
    task.status = TaskStatus.IN_PROGRESS
    task.blocked_reason = ""
    task.started_at = task.started_at or timezone.now()
    task.updated_by = user
    task.save(update_fields=["status", "blocked_reason", "started_at",
                             "updated_by", "updated_at"])

    from apps.projects.models import ProjectStatus
    project = task.project
    if project and project.status == ProjectStatus.READY:
        project.status = ProjectStatus.IN_EXECUTION
        project.save(update_fields=["status", "updated_at"])
    publish("TaskStarted", company=task.company, subject=task, actor=user,
            payload={"task": task.name, "project": project.number if project else "standalone"})
    notify_team(task, verb="task_started", title=f"Started: {task.name}", actor=user)
    run_automations(task, TaskStatus.IN_PROGRESS, actor=user)
    return task


def complete_task(task, user, *, actual_hours=None) -> Task:
    task.status = TaskStatus.COMPLETED
    task.progress_pct = 100
    task.blocked_reason = ""
    task.completed_at = timezone.now()
    if actual_hours is not None:
        task.actual_hours = actual_hours
    task.updated_by = user
    task.save(update_fields=["status", "progress_pct", "blocked_reason", "actual_hours",
                             "completed_at", "updated_by", "updated_at"])
    # Successors may now become ready (event-driven recompute).
    for succ in task.successors.all():
        refresh_task_status(succ)
    if task.project_id:
        recompute_project_progress(task.project)
    publish("TaskCompleted", company=task.company, subject=task, actor=user,
            payload={"task": task.name})
    notify_team(task, verb="task_completed", title=f"Completed: {task.name}", actor=user, email=True)
    run_automations(task, TaskStatus.COMPLETED, actor=user)
    return task


def due_date_from_duration(company, days, start=None):
    """Turn "this takes N days" into a real deadline using the company calendar.

    Calendar arithmetic promises clients dates the gate is locked. This walks
    the configured week and the public-holiday list instead, so a five-day job
    started on a Thursday lands the following Thursday rather than on Tuesday.
    """
    from apps.administration.hours import add_working_days
    from django.utils import timezone
    return add_working_days(company, start or timezone.localdate(), days)


def default_workspace(company):
    """Every company has one — created on demand so a two-person business never
    has to think about workspaces."""
    from .models import Workspace
    workspace, _ = Workspace.objects.get_or_create(
        company=company, key="general",
        defaults={"name": "General", "is_default": True},
    )
    return workspace


@transaction.atomic
def create_work(company, user, *, name, origin=None, project=None, description="",
                is_billable=False, client_name="", assignee=None,
                blocks_on_compliance=None, workspace=None, phase=None,
                priority=None, risk_level=None, site="", department="",
                due_date=None, estimated_hours=None, labels=None,
                owner=None, executors=(), watchers=(), approvers=()) -> Task:
    """The single entry point for Work, whatever its origin — the universal New
    Work wizard behind one function. Project work is compliance-gated; standalone
    work is not. Returns a task with its readiness already computed and its team
    in place."""
    from .models import TaskPriority, WorkOrigin
    origin = origin or WorkOrigin.MANUAL
    if blocks_on_compliance is None:
        blocks_on_compliance = project is not None  # only project work is gated
    task = Task.objects.create(
        company=company, project=project, phase=phase,
        workspace=workspace or default_workspace(company),
        origin=origin, name=name, description=description, is_billable=is_billable,
        client_name=client_name or (project.client_name if project else ""),
        site=site or (getattr(project, "site", "") if project else ""),
        department=department,
        priority=priority or TaskPriority.MEDIUM,
        risk_level=risk_level or "low",
        due_date=due_date,
        estimated_hours=estimated_hours or 0,
        labels=list(labels or []),
        assignee=assignee, blocks_on_compliance=blocks_on_compliance,
        created_by=user, updated_by=user,
    )
    set_team(task, owner=owner, executors=executors, watchers=watchers,
             approvers=approvers)
    refresh_task_status(task)
    publish("WorkCreated", company=company, subject=task, actor=user,
            payload={"name": name, "origin": origin,
                     "standalone": project is None, "billable": is_billable})
    notify_team(task, verb="task_assigned", title=f"New job: {task.name}", actor=user, email=True)
    return task


def recompute_project_progress(project) -> int:
    """Project progress = mean task progress across non-cancelled tasks."""
    tasks = project.tasks.exclude(status=TaskStatus.CANCELLED)
    n = tasks.count()
    if not n:
        return 0
    return round(sum(t.progress_pct for t in tasks) / n)


# ── Compliance-aware resource allocation (Module 9 §4 — the differentiator) ────

class AllocationError(Exception):
    def __init__(self, warnings):
        self.warnings = warnings
        super().__init__("; ".join(warnings))


def allocation_warnings(resource, *, start_date, end_date, exclude_id=None) -> list[str]:
    """Double-booking + expired-credential checks for a proposed allocation."""
    warnings = []
    clash = ResourceAllocation.objects.filter(
        resource=resource
    ).filter(Q(start_date__lte=end_date) & Q(end_date__gte=start_date))
    if exclude_id:
        clash = clash.exclude(id=exclude_id)
    for a in clash:
        warnings.append(
            f"{resource.name} already allocated to {a.project.number} "
            f"[{a.start_date:%Y-%m-%d}..{a.end_date:%Y-%m-%d}]"
        )
    warnings.extend(resource.compliance_issues(start_date))
    return warnings


@transaction.atomic
def allocate_resource(company, user, *, resource, project, start_date, end_date, task=None,
                      force=False, override_reason="") -> ResourceAllocation:
    """Allocate a resource. Refuses (raises AllocationError) on a double-booking or
    an expired credential unless `force=True` with a reason (audited trail). This
    is the control that won't let you mobilise someone who legally can't be on the
    mine."""
    warnings = allocation_warnings(resource, start_date=start_date, end_date=end_date)
    if warnings and not force:
        raise AllocationError(warnings)
    alloc = ResourceAllocation.objects.create(
        company=company, resource=resource, project=project, task=task,
        start_date=start_date, end_date=end_date,
        override_reason=override_reason if warnings else "",
        created_by=user, updated_by=user,
    )
    if warnings:
        publish("ResourceAllocationForced", company=company, subject=alloc, actor=user,
                payload={"resource": resource.name, "warnings": warnings,
                         "reason": override_reason})
    return alloc


# ── Timesheets + the actuals loop that closes Module 7 (§5) ────────────────────

def approve_timesheet(timesheet, user) -> Timesheet:
    timesheet.approved = True
    timesheet.approved_by = user
    timesheet.updated_by = user
    timesheet.save(update_fields=["approved", "approved_by", "updated_by", "updated_at"])
    return timesheet


def _approved_estimate(project):
    """The approved estimate behind this project's quotation, if any."""
    if not project.quotation_id:
        return None
    from apps.estimating.models import Estimate, EstimateStatus
    return (
        Estimate.objects.filter(quotation_id=project.quotation_id,
                                status=EstimateStatus.APPROVED)
        .order_by("-version").first()
    )


def project_actual_costs(project) -> dict:
    """Actuals so far: labour from APPROVED timesheets, material from supplier
    invoices on the project's POs (Procurement §9)."""
    labour = Decimal("0")
    for ts in Timesheet.objects.filter(task__project=project, approved=True):
        labour += ts.labour_cost

    material = Decimal("0")
    if project.quotation_id:
        from apps.procurement.models import SupplierInvoice
        material = SupplierInvoice.objects.filter(
            purchase_order__quotation_id=project.quotation_id
        ).aggregate(t=Sum("total_excl"))["t"] or Decimal("0")

    return {"labour": labour.quantize(TWO), "material": Decimal(material).quantize(TWO)}


def capture_project_actuals(project, user) -> dict:
    """Push execution actuals into the estimate — closing the Pricing-Intelligence
    loop (Module 7 §10). Labour from timesheets, material from supplier invoices."""
    estimate = _approved_estimate(project)
    if estimate is None:
        raise ValueError("No approved estimate to capture actuals against.")
    costs = project_actual_costs(project)
    from apps.estimating.services import capture_actuals
    rows = capture_actuals(estimate, user, [
        {"category": "labour", "actual_cost": costs["labour"], "source": "timesheets"},
        {"category": "material", "actual_cost": costs["material"], "source": "supplier_invoices"},
    ])
    return {"estimate": estimate.number, "captured": costs, "rows": len(rows)}


# ── Project health score (Module 9 §8, composite) ─────────────────────────────

def project_health(project, user=None) -> dict:
    """Live composite health. Compliance reuses the Module 8 readiness; budget
    reuses the estimate + captured actuals. Budget dimension is Golden-Rule gated."""
    compliance = recompute_readiness(project)["overall"]
    progress = recompute_project_progress(project)

    tasks = list(project.tasks.exclude(status=TaskStatus.CANCELLED))
    blocked = [t for t in tasks if t.status == TaskStatus.BLOCKED]
    safety = 100 if compliance == 100 else max(0, compliance)
    quality = round((1 - len(blocked) / len(tasks)) * 100) if tasks else 100

    dimensions = {"progress": progress, "compliance": compliance,
                  "safety": safety, "quality": quality}

    can_view_money = bool(user and getattr(user, "is_authenticated", False)
                          and user.has_perm_code("finance.view_money"))
    if can_view_money:
        estimate = _approved_estimate(project)
        if estimate and estimate.total_cost:
            spent = sum(project_actual_costs(project).values())
            ratio = spent / estimate.total_cost
            budget = 100 if ratio <= 1 else max(0, round((2 - float(ratio)) * 100))
            dimensions["budget"] = budget

    overall = round(sum(dimensions.values()) / len(dimensions))
    return {"overall": overall, "dimensions": dimensions,
            "blocked_tasks": [{"name": t.name, "reason": t.blocked_reason} for t in blocked]}


# ── Daily progress report — customer/internal split (Module 9 §6, Golden Rule) ─

def daily_progress_report(project, *, audience="internal", user=None) -> dict:
    """Customer version shows progress + safety only — no cost, no margin, no
    internal issues (same Financial-Golden-Rule split as Estimate/Quotation)."""
    tasks = list(project.tasks.all())
    completed = [t for t in tasks if t.status == TaskStatus.COMPLETED]
    in_progress = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
    blocked = [t for t in tasks if t.status == TaskStatus.BLOCKED]
    compliance = recompute_readiness(project)

    report = {
        "project": project.number, "date": timezone.localdate().isoformat(),
        "progress_pct": recompute_project_progress(project),
        "tasks_completed": len(completed), "tasks_total": len(tasks),
        "compliance_ready": compliance["gate_status"] in ("ready", "overridden"),
    }

    if audience == "customer":
        return report  # progress + safety only — internal detail withheld

    # Internal: full operational + cost detail.
    labour_hours = Timesheet.objects.filter(task__project=project).aggregate(
        h=Sum("hours"))["h"] or Decimal("0")
    report.update({
        "in_progress": [t.name for t in in_progress],
        "blocked": [{"name": t.name, "reason": t.blocked_reason} for t in blocked],
        "labour_hours": str(labour_hours),
        "actual_costs": {k: str(v) for k, v in project_actual_costs(project).items()},
    })
    return report


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 8 — Work Management Engine
# ══════════════════════════════════════════════════════════════════════════════

# ── Team (work is never limited to one assignee) ──────────────────────────────

def set_team(task, *, owner=None, executors=(), watchers=(), approvers=()) -> Task:
    """Attach people to work by ROLE. Owner is singular and accountable; the rest
    are sets. Called by the wizard and by the team editor alike."""
    if owner is not None:
        task.assignments.filter(role=Assignment.Role.OWNER).delete()
        Assignment.objects.get_or_create(company=task.company, task=task, user=owner,
                                         role=Assignment.Role.OWNER)
    for role, people in ((Assignment.Role.EXECUTOR, executors),
                         (Assignment.Role.WATCHER, watchers),
                         (Assignment.Role.APPROVER, approvers)):
        for person in people:
            if person:
                Assignment.objects.get_or_create(company=task.company, task=task,
                                                 user=person, role=role)
    return task


def add_member(task, user, role) -> Assignment:
    if role == Assignment.Role.OWNER:
        task.assignments.filter(role=Assignment.Role.OWNER).delete()
    assignment, created = Assignment.objects.get_or_create(
        company=task.company, task=task, user=user, role=role)
    if created:
        notify(user, task=task, verb="task_assigned",
               title=f"You were added to {task.name}",
               body=f"Role: {assignment.get_role_display()}", email=True)
    return assignment


def remove_member(task, user, role) -> int:
    deleted, _ = task.assignments.filter(user=user, role=role).delete()
    return deleted


def has_work_perm(user, code) -> bool:
    """Granular Module 8 permission check. `execution.manage` is the umbrella
    that implies every work.* permission, so existing roles keep working."""
    return bool(user.has_perm_code(code) or user.has_perm_code("execution.manage"))


def can_modify(task, user) -> bool:
    """Watchers are read-only; everyone else on the work (plus anyone holding
    execution.manage) may modify it. The view still enforces RBAC on top."""
    if user.has_perm_code("execution.manage"):
        return True
    roles = set(task.assignments.filter(user=user).values_list("role", flat=True))
    return bool(roles - {Assignment.Role.WATCHER})


# ── Hierarchy: phases, subtasks, checklists, and the progress roll-up ─────────

def ensure_default_phases(project, user=None) -> list:
    """Give a project the standard contractor phase set on first use."""
    from .models import DEFAULT_PHASES, Phase
    if project.phases.exists():
        return list(project.phases.all())
    return [
        Phase.objects.create(company=project.company, project=project, name=name,
                             position=i, created_by=user, updated_by=user)
        for i, name in enumerate(DEFAULT_PHASES)
    ]


def add_phase(project, user, *, name, position=None):
    from .models import Phase
    if position is None:
        position = project.phases.count()
    return Phase.objects.create(company=project.company, project=project, name=name,
                                position=position, created_by=user, updated_by=user)


def reorder_phases(project, ordered_ids) -> None:
    """Persist a drag-and-drop reorder in one pass."""
    lookup = {str(p.id): p for p in project.phases.all()}
    for position, pid in enumerate(ordered_ids):
        phase = lookup.get(str(pid))
        if phase and phase.position != position:
            phase.position = position
            phase.save(update_fields=["position", "updated_at"])


def add_subtask(task, user, *, name, assignee=None, due_date=None):
    from .models import Subtask
    return Subtask.objects.create(
        company=task.company, task=task, name=name, assignee=assignee,
        due_date=due_date, position=task.subtasks.count(),
        created_by=user, updated_by=user,
    )


def add_checklist_item(task, user, *, label, subtask=None):
    from .models import ChecklistItem
    return ChecklistItem.objects.create(
        company=task.company, task=task, subtask=subtask, label=label,
        position=task.checklist_items.count(), created_by=user, updated_by=user,
    )


def toggle_checklist_item(item, user, *, done=None):
    """Tick/untick, then roll the progress back up the hierarchy."""
    item.is_done = (not item.is_done) if done is None else bool(done)
    item.done_by = user if item.is_done else None
    item.done_at = timezone.now() if item.is_done else None
    item.save(update_fields=["is_done", "done_by", "done_at", "updated_at"])
    rollup_progress(item.task, user)
    return item


def rollup_progress(task, user=None) -> int:
    """Checklist → subtask → task → phase → project. Progress is DERIVED from
    what people actually ticked off, not typed in by a manager."""
    for subtask in task.subtasks.all():
        items = list(subtask.checklist_items.all())
        if items:
            done = all(i.is_done for i in items)
            if subtask.is_done != done:
                subtask.is_done = done
                subtask.save(update_fields=["is_done", "updated_at"])

    checklist = list(task.checklist_items.all())
    subtasks = list(task.subtasks.all())
    if checklist:
        pct = round(100 * sum(1 for i in checklist if i.is_done) / len(checklist))
    elif subtasks:
        pct = round(100 * sum(1 for s in subtasks if s.is_done) / len(subtasks))
    else:
        pct = task.progress_pct

    if pct != task.progress_pct:
        task.progress_pct = pct
        if user:
            task.updated_by = user
        task.save(update_fields=["progress_pct", "updated_by", "updated_at"])
    if task.project_id:
        recompute_project_progress(task.project)
    return pct


# ── Typed dependencies ────────────────────────────────────────────────────────

def link_tasks(from_task, to_task, *, kind=None, lag_days=0) -> TaskDependency:
    """Create a typed dependency and immediately re-evaluate the dependent task."""
    if from_task.id == to_task.id:
        raise ValueError("A task cannot depend on itself.")
    if _would_cycle(from_task, to_task):
        raise ValueError("That link would create a circular dependency.")
    kind = kind or TaskDependency.Kind.FINISH_TO_START
    dep, _ = TaskDependency.objects.get_or_create(
        company=to_task.company, from_task=from_task, to_task=to_task, kind=kind,
        defaults={"lag_days": lag_days},
    )
    refresh_task_status(to_task)
    return dep


def _would_cycle(from_task, to_task, _seen=None) -> bool:
    """True if `from_task` already (transitively) depends on `to_task`."""
    seen = _seen if _seen is not None else set()
    for dep in from_task.incoming_dependencies.select_related("from_task"):
        pred = dep.from_task
        if pred.id == to_task.id:
            return True
        if pred.id not in seen:
            seen.add(pred.id)
            if _would_cycle(pred, to_task, seen):
                return True
    return False


def unlink_tasks(from_task, to_task, *, kind=None) -> int:
    qs = TaskDependency.objects.filter(from_task=from_task, to_task=to_task)
    if kind:
        qs = qs.filter(kind=kind)
    count, _ = qs.delete()
    refresh_task_status(to_task)
    return count


def blocked_tasks(company=None):
    """Everything currently stuck, newest first — the list a manager works from."""
    return Task.objects.filter(status=TaskStatus.BLOCKED).select_related("project")


# ── Lifecycle transitions ─────────────────────────────────────────────────────

def transition(task, user, *, to_status, note="") -> Task:
    """Move a task through the lifecycle. Start and completion keep their richer
    services (timestamps, successor recompute, project transition); every other
    state is a straightforward, audited move."""
    if to_status == task.status:
        return task
    if to_status == TaskStatus.IN_PROGRESS:
        return start_task(task, user)
    if to_status == TaskStatus.COMPLETED:
        return complete_task(task, user)

    previous = task.status
    task.status = to_status
    if to_status == TaskStatus.CLOSED:
        task.completed_at = task.completed_at or timezone.now()
    if to_status not in (TaskStatus.BLOCKED, TaskStatus.WAITING):
        task.blocked_reason = ""
    elif note:
        task.blocked_reason = note[:255]
    task.updated_by = user
    task.save(update_fields=["status", "blocked_reason", "completed_at",
                             "updated_by", "updated_at"])

    publish("TaskStatusChanged", company=task.company, subject=task, actor=user,
            payload={"task": task.name, "from": previous, "to": to_status})
    notify_team(task, verb="status_changed",
                title=f"{task.name} → {task.get_status_display()}", actor=user, email=True)
    run_automations(task, to_status, actor=user)
    return task


def next_statuses(task) -> list:
    """The sensible forward moves from here — what the UI offers as buttons."""
    from .models import LIFECYCLE_ORDER
    if task.status in TERMINAL_STATUSES:
        return []
    try:
        index = LIFECYCLE_ORDER.index(task.status)
    except ValueError:
        return [TaskStatus.READY]
    return LIFECYCLE_ORDER[index + 1: index + 4]


# ── Comments, files ───────────────────────────────────────────────────────────

def add_comment(task, user, *, body, parent=None, is_internal=True, mentions=()):
    from .models import Comment
    comment = Comment.objects.create(
        company=task.company, task=task, parent=parent, author=user, body=body,
        is_internal=is_internal, created_by=user, updated_by=user,
    )
    people = [m for m in mentions if m]
    if people:
        comment.mentions.set(people)
        for person in people:
            notify(person, task=task, verb="mentioned",
                   title=f"{user} mentioned you on {task.name}", body=body[:200])
    notify_team(task, verb="comment_added", title=f"New comment on {task.name}",
                body=body[:200], actor=user)
    run_automations(task, task.status, actor=user, trigger="comment_added")
    return comment


def add_attachment(task, user, *, uploaded_file, comment=None, kind="document"):
    """Store a file against the work item. Re-uploading the same filename bumps
    the version rather than overwriting evidence."""
    from .models import Attachment
    name = getattr(uploaded_file, "name", "file")
    previous = Attachment.objects.filter(task=task, original_name=name).count()
    attachment = Attachment.objects.create(
        company=task.company, task=task, comment=comment, file=uploaded_file,
        original_name=name, content_type=getattr(uploaded_file, "content_type", "") or "",
        size_bytes=getattr(uploaded_file, "size", 0) or 0,
        version=previous + 1, kind=kind, created_by=user, updated_by=user,
    )
    notify_team(task, verb="file_uploaded", title=f"File added to {task.name}",
                body=name, actor=user)
    return attachment


# ── Notifications ─────────────────────────────────────────────────────────────

def notify(user, *, task=None, verb="", title="", body="", url="", email=False):
    """Write an in-app notification (the canonical inbox) and — when `email` is
    set — also send the branded email through the Email & Notification platform,
    honouring the user's preferences and login access. In-app is always written;
    email is opt-in per event so routine chatter (comments, files) doesn't spam."""
    from .models import Notification
    if user is None:
        return None
    company = task.company if task else user.active_company
    full_url = url or (f"/work/{task.id}/" if task else "")
    note = Notification.objects.create(
        company=company, user=user, task=task, verb=verb, title=title, body=body,
        url=full_url,
    )
    if email:
        _email_notification(user, company, title=title, body=body, url=full_url)
    return note


def _email_notification(user, company, *, title, body, url):
    """Send a task/notification email via the platform if the channel is allowed
    for this user. Never raises — a notification failing must not break the
    business action that triggered it."""
    from apps.notifications.dispatch import _email_allowed
    from apps.notifications.models import EmailCategory
    if not _email_allowed(user, EmailCategory.TASK):
        return
    from django.conf import settings
    from apps.notifications.service import send_email
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    ctx = {"heading": title, "body": body}
    if url:
        ctx.update({"cta_url": (site + url) if url.startswith("/") else url,
                    "cta_label": "Open in LulaWorks"})
    try:
        send_email(to=user.email, subject=title, template="generic", context=ctx,
                   company=company, to_name=(user.get_full_name() or "").strip(),
                   category=EmailCategory.TASK)
    except Exception:  # noqa: BLE001 - resilient: in-app notification already stands
        pass


def notify_team(task, *, verb, title, body="", actor=None, email=False):
    """Fan out to everyone attached to the work — including watchers, who exist
    precisely to be told without being able to change anything. The actor never
    notifies themselves."""
    recipients = {a.user for a in task.assignments.select_related("user")}
    recipients.discard(actor)
    return [notify(u, task=task, verb=verb, title=title, body=body, email=email)
            for u in recipients]


def run_overdue_reminders(today=None) -> int:
    """Daily sweep (Celery beat): email each person who has open work now past its
    due date — one summary per person, not per task. Platform-wide (uses
    all_objects to cross tenants). Returns the number of people emailed."""
    from django.utils import timezone
    from .models import Assignment, Task, TERMINAL_STATUSES
    today = today or timezone.localdate()

    overdue = (Task.all_objects
               .filter(due_date__lt=today)
               .exclude(status__in=TERMINAL_STATUSES)
               .values_list("id", "company_id"))
    task_ids = [t[0] for t in overdue]
    if not task_ids:
        return 0

    # Group overdue tasks by assignee (one email each). all_objects: the sweep
    # is platform-wide, outside any single tenant's context.
    per_user = {}
    for a in (Assignment.all_objects.filter(task_id__in=task_ids)
              .select_related("user", "task", "task__company")):
        per_user.setdefault(a.user, set()).add(a.task)

    emailed = 0
    for user, tasks in per_user.items():
        company = next(iter(tasks)).company
        n = len(tasks)
        _email_notification(
            user, company,
            title=f"You have {n} overdue task{'s' if n != 1 else ''}",
            body=("These jobs are past their due date: "
                  + ", ".join(sorted(t.name for t in tasks)[:8])
                  + ("…" if n > 8 else "") + "."),
            url="/work/")
        emailed += 1
    return emailed


def unread_count(user) -> int:
    from .models import Notification
    return Notification.objects.filter(user=user, is_read=False).count()


def mark_notifications_read(user, ids=None) -> int:
    from .models import Notification
    qs = Notification.objects.filter(user=user, is_read=False)
    if ids:
        qs = qs.filter(id__in=ids)
    return qs.update(is_read=True)


# ── Automations (they move information; they never approve, send or pay) ──────

def run_automations(task, status, *, actor=None, trigger=None):
    """Evaluate the company's active rules for this event. Deliberately narrow:
    automations notify, recompute, and set status — the human-approval boundary
    means they never approve, award, send or pay."""
    from .models import AutomationRule
    if trigger is None:
        trigger = {
            TaskStatus.COMPLETED: AutomationRule.Trigger.TASK_COMPLETED,
            TaskStatus.BLOCKED: AutomationRule.Trigger.TASK_BLOCKED,
        }.get(status, AutomationRule.Trigger.STATUS_CHANGED)

    fired = []
    for rule in AutomationRule.objects.filter(trigger=trigger, is_active=True):
        if not _conditions_match(rule.conditions, task):
            continue
        _apply_action(rule, task, actor)
        fired.append(rule.name)
    return fired


def _conditions_match(conditions, task) -> bool:
    for field, expected in (conditions or {}).items():
        actual = getattr(task, field, None)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _apply_action(rule, task, actor):
    from .models import AutomationRule
    action = rule.action
    if action == AutomationRule.Action.NOTIFY_OWNER:
        notify(task.owner, task=task, verb="automation",
               title=rule.name, body=f"{task.name} → {task.get_status_display()}")
    elif action == AutomationRule.Action.NOTIFY_APPROVERS:
        for person in task.team(Assignment.Role.APPROVER):
            notify(person, task=task, verb="approval_required", title=rule.name,
                   body=f"{task.name} needs your approval")
    elif action == AutomationRule.Action.NOTIFY_WATCHERS:
        for person in task.team(Assignment.Role.WATCHER):
            notify(person, task=task, verb="automation", title=rule.name,
                   body=task.name)
    elif action == AutomationRule.Action.UNLOCK_SUCCESSORS:
        for succ in task.successors.all():
            refresh_task_status(succ)
    elif action == AutomationRule.Action.SET_STATUS:
        target = (rule.params or {}).get("status")
        if target and target != task.status:
            task.status = target
            task.save(update_fields=["status", "updated_at"])


# ── The per-work dashboard ────────────────────────────────────────────────────

def work_dashboard(task, user=None) -> dict:
    """Everything the work item's own dashboard shows, computed in one place so
    the web, the API and the Flutter app can never drift apart."""
    children = list(task.children.all())
    subtasks = list(task.subtasks.all())
    checklist = list(task.checklist_items.all())
    status, reason = compute_task_readiness(task)
    blockers = [r for r in reason.split("; ") if r]

    data = {
        "task": task,
        "progress_pct": task.progress_pct,
        "computed_status": status,
        "blockers": blockers,
        "is_overdue": task.is_overdue,
        "subtasks_done": sum(1 for s in subtasks if s.is_done),
        "subtasks_total": len(subtasks),
        "checklist_done": sum(1 for c in checklist if c.is_done),
        "checklist_total": len(checklist),
        "children_open": sum(1 for c in children if c.is_open),
        "children_total": len(children),
        "team": {role: task.team(role) for role, _ in Assignment.Role.choices},
        "dependencies": list(task.incoming_dependencies.select_related("from_task")),
        "dependents": list(task.outgoing_dependencies.select_related("to_task")),
        "comments": list(task.comments.select_related("author").filter(parent__isnull=True)),
        "attachments": list(task.attachments.all()),
        "next_statuses": next_statuses(task),
    }
    if task.project_id:
        data["phases"] = list(task.project.phases.all())
        data["compliance"] = recompute_readiness(task.project)
    return data


def portfolio_report(company=None) -> dict:
    """Cross-work reporting: completion, blockers, overdue, workload."""
    tasks = list(Task.objects.all().select_related("project"))
    open_tasks = [t for t in tasks if t.is_open]
    by_status, by_origin, workload = {}, {}, {}
    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1
        by_origin[t.origin] = by_origin.get(t.origin, 0) + 1
    for a in Assignment.objects.filter(role=Assignment.Role.EXECUTOR).select_related("user"):
        workload[str(a.user)] = workload.get(str(a.user), 0) + 1

    completed = [t for t in tasks if t.status in DONE_STATUSES]
    return {
        "total": len(tasks),
        "open": len(open_tasks),
        "completed": len(completed),
        "completion_pct": round(100 * len(completed) / len(tasks)) if tasks else 0,
        "blocked": [t for t in open_tasks if t.status == TaskStatus.BLOCKED],
        "overdue": [t for t in open_tasks if t.is_overdue],
        "by_status": by_status,
        "by_origin": by_origin,
        "workload": workload,
    }
