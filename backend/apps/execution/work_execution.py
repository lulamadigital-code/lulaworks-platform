"""Work Execution System — services that turn a Task into an operational record.

A task in Lulaworks is not a checkbox. It carries the money set aside for it
(:class:`TaskResourceAllocation`), the evidence captured in the field
(:class:`TaskReport` — fuel, material, time/attendance, progress, each
GPS-stamped) and the purchase lines extracted from supplier invoices
(:class:`TaskReportItem`). These services are the single place that:

* verifies a field check-in happened where it should have (GPS + tolerance),
* reconciles what was allocated against what was actually spent,
* rolls a task up into the numbers a manager needs (allocated / spent /
  remaining / materials / documents / latest location), and
* assembles the operational timeline — one auditable story per task and Work.

The web, the API and the Flutter app all read from here so they can never drift.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.events import publish

from .models import (
    NON_MONETARY_ALLOCATIONS,
    Assignment,
    AllocationKind,
    AllocationStatus,
    ExtractionStatus,
    FINANCIAL_REPORT_KINDS,
    ReportKind,
    TaskReport,
    TaskReportItem,
    TaskResourceAllocation,
)

_ZERO = Decimal("0.00")
DEFAULT_GPS_TOLERANCE_M = 500

#: How a field report's kind books into the finance cost ledger. The values are
#: finance ``CostCategory`` codes (kept as plain strings to avoid a finance
#: import here — finance is the downstream consumer, not us).
REPORT_KIND_TO_COST_CATEGORY = {
    ReportKind.MATERIAL: "material",
    ReportKind.FUEL: "equipment",
    ReportKind.EXPENSE: "other",
}


def _dec(value, default=_ZERO) -> Decimal:
    """Coerce anything the web/API hands us (str, int, float, None) to Decimal."""
    if value in (None, ""):
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# GPS verification
# ─────────────────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    radius = 6_371_000.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlmb = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def verify_report_location(report, *, tolerance_m=None) -> TaskReport:
    """Measure a report's GPS against its task's expected site and flag drift.

    No coordinates on the report, or no expected coordinates on the task, means
    there is nothing to verify — the report is left unflagged. Mutates and
    returns ``report`` (the caller saves)."""
    task = report.task
    if report.latitude is None or report.longitude is None \
            or task.site_latitude is None or task.site_longitude is None:
        report.distance_m = None
        report.location_flagged = False
        return report

    distance = haversine_m(report.latitude, report.longitude,
                           task.site_latitude, task.site_longitude)
    if tolerance_m is None:
        tolerance_m = (task.project.gps_tolerance_m if task.project_id
                       else DEFAULT_GPS_TOLERANCE_M)
    report.distance_m = Decimal(str(round(distance, 1)))
    report.location_flagged = distance > float(tolerance_m)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Resource allocation + reconciliation
# ─────────────────────────────────────────────────────────────────────────────

def allocate_task_resource(task, user, *, kind, amount_allocated=0, label="",
                           is_monetary=None, notes="", status=None):
    """Set aside an operational resource for a task before work starts."""
    if is_monetary is None:
        is_monetary = kind not in NON_MONETARY_ALLOCATIONS
    alloc = TaskResourceAllocation.objects.create(
        company=task.company, task=task, kind=kind, label=label,
        is_monetary=is_monetary, amount_allocated=_dec(amount_allocated),
        status=status or AllocationStatus.REQUESTED,
        requested_by=user, created_by=user, updated_by=user, notes=notes,
    )
    publish("TaskResourceAllocated", company=task.company, subject=task, actor=user,
            payload={"kind": kind, "amount": str(alloc.amount_allocated)})
    return alloc


def reconcile_allocation(allocation, *, save=True) -> TaskResourceAllocation:
    """Recompute ``amount_spent`` from the reports booked against this allocation."""
    total = allocation.reports.aggregate(s=Sum("amount"))["s"] or _ZERO
    allocation.amount_spent = total
    if save:
        allocation.save(update_fields=["amount_spent", "updated_at"])
    return allocation


# ─────────────────────────────────────────────────────────────────────────────
# Task reports — the field record
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_task_report(task, user, *, kind=ReportKind.PROGRESS, title, event="",
                       notes="", employee=None, reported_at=None,
                       latitude=None, longitude=None, gps_accuracy_m=None,
                       supplier="", invoice_number="", document_date=None,
                       amount=0, vat_amount=0, currency="ZAR",
                       allocation=None, extraction_status=None):
    """Record an operational event on a task and verify where it happened.

    Financial reports (fuel/material/expense) booked against an allocation
    re-reconcile it so ``amount_spent`` stays live."""
    report = TaskReport(
        company=task.company, task=task, kind=kind, title=title, event=event,
        notes=notes, employee=employee or user,
        reported_at=reported_at or timezone.now(),
        latitude=latitude, longitude=longitude, gps_accuracy_m=gps_accuracy_m,
        supplier=supplier, invoice_number=invoice_number, document_date=document_date,
        amount=_dec(amount), vat_amount=_dec(vat_amount), currency=currency or "ZAR",
        allocation=allocation,
        extraction_status=extraction_status or ExtractionStatus.NONE,
        created_by=user, updated_by=user,
    )
    verify_report_location(report)
    report.save()
    if allocation is not None:
        reconcile_allocation(allocation)
    publish("TaskReportCreated", company=task.company, subject=task, actor=user,
            payload={"kind": kind, "title": title, "amount": str(report.amount)})
    return report


def learn_supplier_from_receipt(report, user):
    """A confirmed material receipt feeds the Suppliers database: match/add the
    seller and record its item prices, then link the report to that supplier so
    the receipt is traceable and future buys know where we bought this before.

    No-op for non-material reports or receipts without a supplier name. Returns
    the Supplier (or None)."""
    if report.kind != ReportKind.MATERIAL or not (report.supplier or "").strip():
        return None
    from apps.procurement.services import learn_from_receipt

    supplier, _prices, _created = learn_from_receipt(
        report.company, user, supplier_name=report.supplier,
        items=list(report.items.all()),
        date=report.document_date or report.reported_at.date(),
        currency=report.currency)
    if supplier is not None and report.supplier_ref_id != supplier.id:
        report.supplier_ref = supplier
        report.save(update_fields=["supplier_ref", "updated_at"])
    return supplier


def add_report_item(report, *, description, quantity=1, unit="", unit_price=0,
                    line_total=None, user=None):
    """Attach an extracted invoice line to a material-purchase report."""
    qty = _dec(quantity, Decimal("1"))
    price = _dec(unit_price)
    total = _dec(line_total) if line_total is not None else (qty * price)
    return TaskReportItem.objects.create(
        company=report.company, report=report, task=report.task,
        description=description, quantity=qty, unit=unit,
        unit_price=price, line_total=total,
        created_by=user, updated_by=user,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rollups — the numbers a manager needs
# ─────────────────────────────────────────────────────────────────────────────

def task_financials(task) -> dict:
    """Allocated vs spent for a task, plus the materials it bought."""
    allocations = list(task.cost_allocations.all())
    allocated = sum((a.amount_allocated for a in allocations if a.is_monetary), _ZERO)

    fin_reports = [r for r in task.reports.all() if r.kind in FINANCIAL_REPORT_KINDS]
    spent = sum((r.amount for r in fin_reports), _ZERO)

    by_kind: dict[str, Decimal] = {}
    for r in fin_reports:
        by_kind[r.kind] = by_kind.get(r.kind, _ZERO) + r.amount

    material_items = list(task.material_items.all())
    materials_total = sum((r.amount for r in fin_reports
                           if r.kind == ReportKind.MATERIAL), _ZERO)

    return {
        "allocated": allocated,
        "spent": spent,
        "remaining": allocated - spent,
        "over_budget": allocated > 0 and spent > allocated,
        "by_kind": by_kind,
        "materials_total": materials_total,
        "materials_count": len(material_items),
        "material_items": material_items,
        "allocations": allocations,
    }


def project_field_spend(project) -> dict[str, Decimal]:
    """Actual field money captured against a project's tasks, grouped by finance
    cost category.

    This is real cash/card spend the crew recorded on site (material, fuel and
    other expenses on :class:`TaskReport`) — money that never touched a
    procurement PO. It is the WES half of the money loop: finance converges it
    into the cost ledger so a manager's profitability reflects what was actually
    spent, not only what came through supplier invoices."""
    totals: dict[str, Decimal] = {}
    rows = (
        TaskReport.objects.filter(task__project=project, kind__in=FINANCIAL_REPORT_KINDS)
        .values("kind")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        category = REPORT_KIND_TO_COST_CATEGORY.get(row["kind"], "other")
        totals[category] = totals.get(category, _ZERO) + (row["total"] or _ZERO)
    return totals


def task_operational_dashboard(task, user=None) -> dict:
    """Everything a task's operational hub shows — one place, no drift.

    Answers, for a single task: who's on it, what's outstanding, how much money
    is allocated/spent/left, what materials were bought, how many documents
    exist, where the team last was, and the full timeline."""
    reports = list(task.reports.select_related("employee").all())  # -reported_at first
    located = [r for r in reports if r.has_location]
    checklist = list(task.checklist_items.all())
    subtasks = list(task.subtasks.all())
    outstanding = [c.label for c in checklist if not c.is_done] \
        + [s.name for s in subtasks if not s.is_done]

    # Golden Rule: the task budget (allocated/spent/remaining) is company money.
    # Only surface it to someone who may view money; everyone else gets the
    # operational hub with the financials withheld.
    can_view_money = bool(user and getattr(user, "is_authenticated", False)
                          and user.has_perm_code("finance.view_money"))

    return {
        "task": task,
        "progress_pct": task.progress_pct,
        "team": {role: task.team(role) for role, _ in Assignment.Role.choices},
        "outstanding": outstanding,
        "checklist": checklist,
        "subtasks": subtasks,
        "completion": task_completion_status(task),
        "can_view_money": can_view_money,
        "financials": task_financials(task) if can_view_money else None,
        "reports": reports,
        "flagged_reports": [r for r in reports if r.location_flagged],
        "latest_report": reports[0] if reports else None,
        "latest_gps": located[0] if located else None,
        "map_points": [
            {"lat": float(r.latitude), "lng": float(r.longitude),
             "title": r.title, "flagged": r.location_flagged,
             "when": r.reported_at}
            for r in located
        ],
        "documents": task.attachments.count(),
        "timeline": task_timeline(task, reports=reports),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Operational timeline — one auditable story
# ─────────────────────────────────────────────────────────────────────────────

def _detail_for(report) -> str:
    if report.kind in FINANCIAL_REPORT_KINDS:
        bits = [report.supplier, f"{report.currency} {report.amount}".strip()]
        return " · ".join(b for b in bits if b and b.strip())
    return report.notes or report.event or ""


def task_timeline(task, *, reports=None) -> list[dict]:
    """Chronological (oldest-first) record of everything that happened on a task:
    creation, allocations, start, every field report, completion."""
    events: list[dict] = []

    def add(when, kind, label, detail=""):
        if when is not None:
            events.append({"when": when, "kind": kind, "label": label, "detail": detail})

    add(task.created_at, "created", "Task created", task.name)
    for a in task.cost_allocations.all():
        detail = (str(a.amount_allocated) if a.is_monetary else a.label) or ""
        add(a.created_at, "allocation", f"Allocated {a.get_kind_display()}", detail)
    add(task.started_at, "started", "Task started")
    for r in (reports if reports is not None else task.reports.all()):
        add(r.reported_at, r.kind, r.title, _detail_for(r))
    add(task.completed_at, "completed", "Task completed")

    events.sort(key=lambda e: e["when"])
    return events


def work_timeline(project) -> list[dict]:
    """The Work-level audit trail: the commercial events that created it (from
    the linked quotation) merged with every field report across its tasks —
    Quotation Approved → Work Created → … → Payment."""
    events: list[dict] = []

    def add(when, kind, label, detail=""):
        if when is not None:
            events.append({"when": when, "kind": kind, "label": label, "detail": detail})

    quote = project.quotation
    if quote is not None:
        for ev in quote.events.all():
            add(ev.created_at, ev.verb, ev.verb.replace("_", " ").capitalize(), ev.note)
    add(project.awarded_at, "work_created", "Work created", project.number)

    for task in project.tasks.all():
        for r in task.reports.all():
            add(r.reported_at, r.kind, f"{task.name}: {r.title}", _detail_for(r))
        add(task.completed_at, "task_completed", f"{task.name} completed")

    events.sort(key=lambda e: e["when"])
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Time & Attendance — event-based, never a continuous trail
# ─────────────────────────────────────────────────────────────────────────────

def attendance_summary(events) -> dict:
    """Fold a day's ordered attendance events into the numbers the app shows:
    current state, when it started, and seconds actually worked (breaks removed).

    `events` must be this user's events for one day, ascending by occurred_at.
    Live (state == "working") worked-seconds include the time since the last
    resume up to now, so the app can show a ticking elapsed."""
    from .models import AttendanceEvent as AE

    state = "clocked_out"          # clocked_out | working | on_break
    worked = 0                     # accumulated seconds actually worked
    last_active = None             # start of the current working stretch
    clock_in_at = None
    since = None                   # when the current state began

    for e in events:
        t = e.occurred_at
        if e.kind == AE.Kind.CLOCK_IN:
            state, last_active, clock_in_at, since = "working", t, t, t
        elif e.kind == AE.Kind.BREAK_START:
            if state == "working" and last_active:
                worked += (t - last_active).total_seconds()
            state, since, last_active = "on_break", t, None
        elif e.kind == AE.Kind.BREAK_END:
            state, last_active, since = "working", t, t
        elif e.kind == AE.Kind.CLOCK_OUT:
            if state == "working" and last_active:
                worked += (t - last_active).total_seconds()
            state, since, last_active, clock_in_at = "clocked_out", t, None, None
        # site_arrival / site_departure don't change clock state.

    if state == "working" and last_active:
        worked += (timezone.now() - last_active).total_seconds()

    return {
        "state": state,
        "since": since.isoformat() if since else None,
        "clock_in_at": clock_in_at.isoformat() if clock_in_at else None,
        "worked_seconds": int(max(0, worked)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task chat — task-scoped conversation, access enforced server-side
# ─────────────────────────────────────────────────────────────────────────────

def task_participant_ids(task) -> set:
    """User ids assigned to the task (any role) — its chat participants."""
    return set(task.assignments.values_list("user_id", flat=True))


def can_access_task_chat(user, task) -> bool:
    """Who may read/post a task's chat: a participant (assigned to it) or a
    manager (execution.manage). Superusers always can."""
    if getattr(user, "is_superuser", False):
        return True
    if user.has_perm_code("execution.manage"):
        return True
    return user.id in task_participant_ids(task)


def broadcast_task_message(task_id, message: dict):
    """Push a serialized message to the task's WebSocket group so connected
    participants see it instantly. Best-effort — realtime must never break the
    HTTP save (the 10s poll is the fallback)."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            f"task_chat_{task_id}",
            {"type": "chat.message", "message": message})
    except Exception as exc:                                    # noqa: BLE001
        import logging
        logging.getLogger("lulaworks.chat").warning(
            "task chat broadcast failed: %r", exc)


def create_task_message(task, author, body: str = "", image=None):
    """Post a message (text and/or a photo) to a task chat: save it, notify the
    other participants, and broadcast it to anyone watching in realtime. Shared
    by the mobile API and the web console so both behave identically."""
    from .models import TaskMessage
    from .serializers import TaskMessageSerializer
    from .services import notify_team
    msg = TaskMessage.objects.create(
        task=task, company=task.company, author=author,
        kind=TaskMessage.Kind.IMAGE if image else TaskMessage.Kind.TEXT,
        body=body, image=image)
    notify_team(task, verb="task_message", title=f"New message on {task.name}",
                body=(body or "Photo")[:120], actor=author)
    broadcast_task_message(task.id, TaskMessageSerializer(msg).data)
    return msg


def post_system_message(task, body: str):
    """Record a SYSTEM event in the task thread (author=null). Best-effort — a
    chat note must never break the operation that triggered it."""
    from .models import TaskMessage
    from .serializers import TaskMessageSerializer
    try:
        msg = TaskMessage.objects.create(task=task, company=task.company,
                                         kind=TaskMessage.Kind.SYSTEM, body=body)
        broadcast_task_message(task.id, TaskMessageSerializer(msg).data)
        return msg
    except Exception:                                          # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Completion rules — a task can't be completed while required evidence is missing
# ─────────────────────────────────────────────────────────────────────────────

#: key → (human label, predicate(task) -> bool "is this requirement met?")
COMPLETION_REQUIREMENTS = {
    "checklist": ("All checklist items ticked",
                  lambda t: not t.checklist_items.filter(is_done=False).exists()
                  and not t.subtasks.filter(is_done=False).exists()),
    "report":    ("At least one field report",
                  lambda t: t.reports.exists()),
    "photo":     ("A photo attached",
                  lambda t: any(r.attachments.filter(kind="photo").exists()
                                for r in t.reports.all())),
    "receipt":   ("A purchase receipt captured",
                  lambda t: t.reports.filter(
                      kind__in=["material", "fuel", "expense"]).exists()),
}


def available_completion_requirements() -> list:
    """The catalogue of requirements a task can be gated on (key + label) — what
    the manager's editor offers to toggle."""
    return [{"key": k, "label": label} for k, (label, _) in COMPLETION_REQUIREMENTS.items()]


def task_completion_status(task) -> dict:
    """Evaluate this task's completion requirements. Returns whether it can be
    completed and, if not, what's still missing — the same shape the Task Detail
    shows and the complete-gate enforces, so the app and server never disagree.
    Also carries the enabled keys + the full catalogue so a manager can edit them."""
    keys = task.completion_requirements or []
    reqs = []
    for key in keys:
        entry = COMPLETION_REQUIREMENTS.get(key)
        if not entry:
            continue
        label, met = entry
        reqs.append({"key": key, "label": label, "met": bool(met(task))})
    missing = [r["label"] for r in reqs if not r["met"]]
    return {"ok": not missing, "requirements": reqs, "missing": missing,
            "enabled": list(keys),
            "available": available_completion_requirements()}
