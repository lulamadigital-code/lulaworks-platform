"""Execution services (PROJECT_EXECUTION.md / Module 9).

Computed task readiness (mirrors the project gate), compliance-aware resource
allocation (double-booking + expired-credential refusal), the actuals capture
that closes the Module 7 Pricing-Intelligence loop, the composite project health
score, and the customer/internal progress-report split (Golden Rule).
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.compliance.services import can_start, recompute_readiness
from apps.core.events import publish

from .models import ResourceAllocation, Task, TaskStatus, Timesheet

TWO = Decimal("0.01")


# ── Computed task readiness (Module 9 §3 — the core insight) ──────────────────

def compute_task_readiness(task) -> tuple[str, str]:
    """Compute a task's readiness from real-world dependencies. Returns
    (status, blocked_reason). A task is READY only when its predecessors are
    complete, the compliance gate is open (if required), and its materials are
    delivered — task-level readiness mirroring the project gate."""
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        return task.status, ""

    reasons = []

    incomplete = task.predecessors.exclude(status=TaskStatus.COMPLETED)
    if incomplete.exists():
        names = ", ".join(incomplete.values_list("name", flat=True)[:3])
        reasons.append(f"waiting on predecessor: {names}")

    if task.blocks_on_compliance and not can_start(task.project):
        reasons.append("project not compliance-ready")

    if task.material_po_id:
        outstanding = sum((line.outstanding for line in task.material_po.lines.all()),
                          Decimal("0"))
        if outstanding > 0:
            reasons.append(f"materials not delivered (PO {task.material_po.number})")

    if reasons:
        return TaskStatus.BLOCKED, "; ".join(reasons)
    # No blockers: an in-progress task stays in progress; otherwise it's Ready.
    if task.status == TaskStatus.IN_PROGRESS:
        return TaskStatus.IN_PROGRESS, ""
    return TaskStatus.READY, ""


def refresh_task_status(task, *, save=True) -> Task:
    """Recompute and persist a task's readiness (unless it's already terminal or
    manually on hold)."""
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ON_HOLD,
                       TaskStatus.AWAITING_INSPECTION):
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
    task.updated_by = user
    task.save(update_fields=["status", "blocked_reason", "updated_by", "updated_at"])

    from apps.projects.models import ProjectStatus
    project = task.project
    if project.status == ProjectStatus.READY:
        project.status = ProjectStatus.IN_EXECUTION
        project.save(update_fields=["status", "updated_at"])
    publish("TaskStarted", company=task.company, subject=task, actor=user,
            payload={"task": task.name, "project": project.number})
    return task


def complete_task(task, user, *, actual_hours=None) -> Task:
    task.status = TaskStatus.COMPLETED
    task.progress_pct = 100
    task.blocked_reason = ""
    if actual_hours is not None:
        task.actual_hours = actual_hours
    task.updated_by = user
    task.save(update_fields=["status", "progress_pct", "blocked_reason", "actual_hours",
                             "updated_by", "updated_at"])
    # Successors may now become ready (event-driven recompute).
    for succ in task.successors.all():
        refresh_task_status(succ)
    recompute_project_progress(task.project)
    publish("TaskCompleted", company=task.company, subject=task, actor=user,
            payload={"task": task.name})
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
