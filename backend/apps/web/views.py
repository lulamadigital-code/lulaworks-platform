"""Manager web dashboards (server-rendered HTML + HTMX).

Session-authenticated pages for office/manager users — the data-heavy surface that
suits a DOM web app. Deliberately separate from the JWT API (which the Flutter
field app uses). Reuses the exact same services, so there is one source of truth
for readiness, health and profitability.

Tenancy: the ambient TenantMiddleware binds the tenant from request.user for the
whole request, so `Project.objects.all()` is already tenant-scoped here.
Golden Rule: money is computed/shown only for users with `finance.view_money`.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.ai_platform.decomposition import (
    apply_decomposition,
    propose_decomposition,
    record_proposal,
)
from apps.ai_platform.orchestrator import orchestrate
from apps.compliance.models import ComplianceItem
from apps.compliance.services import approve_item, recompute_readiness
from apps.compliance.services import override as override_gate
from apps.estimating.models import Estimate, EstimateStatus
from apps.estimating.services import approve_estimate, create_revision
from apps.execution.models import (
    Assignment,
    ChecklistItem,
    Phase,
    RiskLevel,
    Subtask,
    Task,
    TaskDependency,
    TaskPriority,
    TaskStatus,
    WorkOrigin,
)
from apps.execution.services import (
    add_attachment,
    add_checklist_item,
    add_comment,
    add_member,
    add_phase,
    add_subtask,
    complete_task,
    compute_task_readiness,
    create_work,
    ensure_default_phases,
    has_work_perm,
    link_tasks,
    mark_notifications_read,
    portfolio_report,
    project_health,
    remove_member,
    start_task,
    toggle_checklist_item,
    transition,
    unread_count,
    work_dashboard,
)
from apps.identity.models import Membership, Role
from apps.identity.services import (
    MemberError,
    assignable_users,
    company_members,
    member_work,
    # aliased: `add_member` already means "add someone to a work item"
    add_member as add_company_member,
    selectable_roles,
    set_member_role,
    set_member_status,
    set_password,
)
from apps.finance.models import Invoice
from apps.finance.services import (
    budget_vs_actual,
    commercial_dashboard,
    create_progress_claim,
    profit_forecast,
    profitability,
    rebuild_actuals_from_sources,
    record_payment,
)
from apps.procurement.models import GRN, GRNLine, PurchaseOrder, Supplier
from apps.procurement.services import three_way_match
from apps.projects.models import Project, ProjectStatus
from apps.quotes.models import Quotation, QuotationStatus
from apps.quotes.pdf import quotation_pdf_bytes
from apps.quotes.services import update_quotation
from apps.rfq.models import RFQDocument
from apps.rfq.models import RFQLineItem
from apps.rfq.services import (
    add_line_item,
    approve_rfq,
    delete_line_item,
    ingest_rfq,
    ingest_rfq_text,
    update_line_item,
)


def login_view(request):
    if request.user.is_authenticated:
        return redirect("web:dashboard")
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("email", "").strip(),
            password=request.POST.get("password", ""),
        )
        if user is not None:
            login(request, user)
            return redirect("web:dashboard")
        messages.error(request, "Invalid email or password.")
    return render(request, "web/login.html")


def logout_view(request):
    logout(request)
    return redirect("web:login")


def _can_view_money(user) -> bool:
    return user.has_perm_code("finance.view_money")


_STATUS_META = {
    "draft": ("#c4c7d0", "Draft"), "ready": ("#579bfc", "Ready"),
    "assigned": ("#579bfc", "Assigned"), "accepted": ("#a25ddc", "Accepted"),
    "in_progress": ("#fdab3d", "In progress"), "waiting": ("#fdab3d", "Waiting"),
    "blocked": ("#e2445c", "Blocked"), "quality_check": ("#a25ddc", "Quality check"),
    "client_signoff": ("#a25ddc", "Client sign-off"), "completed": ("#00c875", "Completed"),
    "closed": ("#676879", "Closed"), "cancelled": ("#c4c7d0", "Cancelled"),
}
_ORIGIN_META = {
    "rfq": ("#a25ddc", "RFQ / Tender"), "manual": ("#579bfc", "Manual work"),
    "project": ("#17a2b8", "Project"),
    "customer_request": ("#fdab3d", "Customer request"),
    "recurring": ("#00c875", "Recurring maintenance"),
    "internal": ("#676879", "Internal work"),
    "breakdown": ("#e2445c", "Breakdown / callout"),
    "preventative": ("#0e7c8c", "Preventative maintenance"),
}


def _donut(status_counts, total):
    """Build SVG donut segments (dash/gap/rotate computed server-side — no JS)."""
    from math import pi
    circumference = 2 * pi * 54
    segments, cum = [], 0.0
    for status, cnt in sorted(status_counts.items(), key=lambda x: -x[1]):
        color, label = _STATUS_META.get(status, ("#17a2b8", status.replace("_", " ").title()))
        pct = cnt / total if total else 0
        segments.append({
            "color": color, "label": label, "count": cnt, "pct": round(pct * 100),
            "dash": round(pct * circumference, 2), "gap": round((1 - pct) * circumference, 2),
            "rotate": round(-90 + cum * 360, 2),
        })
        cum += pct
    return {"segments": segments, "circ": round(circumference, 2)}


@login_required
def dashboard(request):
    """Portfolio home: KPI widgets + charts, plus the compliance attention list.
    The commercial panel is finance-only (Golden Rule)."""
    from collections import Counter

    projects = list(Project.objects.all().select_related("quotation"))
    attention = []
    for p in projects:
        r = recompute_readiness(p)
        if r["gate_status"] == "not_ready":
            attention.append({"project": p, "readiness": r})

    work = list(Task.objects.all())
    status_counts = Counter(t.status for t in work)
    origin_counts = Counter(t.origin for t in work)
    max_origin = max(origin_counts.values(), default=1)
    origins = [
        {"label": lbl, "color": col, "count": origin_counts.get(key, 0),
         "pct": round(origin_counts.get(key, 0) / max_origin * 100) if max_origin else 0}
        for key, (col, lbl) in _ORIGIN_META.items() if origin_counts.get(key, 0)
    ]

    context = {
        "project_count": len(projects),
        "attention": attention,
        "in_execution": sum(1 for p in projects if p.status == ProjectStatus.IN_EXECUTION),
        "ready": sum(1 for p in projects if p.status == ProjectStatus.READY),
        "work_total": len(work),
        "work_completed": status_counts.get("completed", 0),
        "work_blocked": status_counts.get("blocked", 0),
        "donut": _donut(status_counts, len(work)),
        "origins": origins,
        "can_view_money": _can_view_money(request.user),
    }
    if context["can_view_money"]:
        context["commercial"] = commercial_dashboard(request.user.active_company)
    return render(request, "web/dashboard.html", context)


@login_required
def projects_list(request):
    projects = Project.objects.all().select_related("quotation")
    return render(request, "web/projects.html", {"projects": projects})


@login_required
def project_detail(request, pk):
    project = get_object_or_404(Project.objects.all(), pk=pk)
    readiness = recompute_readiness(project)
    context = {
        "project": project,
        "readiness": readiness,
        "health": project_health(project, request.user),
        "checklist": project.compliance_items.all(),
        "can_view_money": _can_view_money(request.user),
        "can_compliance": request.user.has_perm_code("compliance.override"),
        "can_finance": request.user.has_perm_code("finance.manage"),
        "today": timezone.localdate().isoformat(),
    }
    if context["can_view_money"]:
        rebuild_actuals_from_sources(project, request.user)
        context["profitability"] = profitability(project)
        context["forecast"] = profit_forecast(project)
        context["budget"] = budget_vs_actual(project)
    return render(request, "web/project_detail.html", context)


@login_required
def readiness_partial(request, pk):
    """HTMX partial — re-renders just the readiness gate card, live."""
    project = get_object_or_404(Project.objects.all(), pk=pk)
    return render(request, "web/_readiness.html",
                  {"project": project, "readiness": recompute_readiness(project)})


# ══════════════════════════════════════════════════════════════════════════════
# Work Management Engine (MODULE 8) — one engine, many views
# ══════════════════════════════════════════════════════════════════════════════

#: Board columns — the lifecycle collapsed into the lanes a manager works in.
BOARD_LANES = [
    ("draft", "Draft", [TaskStatus.DRAFT]),
    ("ready", "Ready", [TaskStatus.READY, TaskStatus.ASSIGNED, TaskStatus.ACCEPTED]),
    ("active", "In progress", [TaskStatus.IN_PROGRESS]),
    ("stuck", "Stuck", [TaskStatus.BLOCKED, TaskStatus.WAITING]),
    ("review", "Review", [TaskStatus.QUALITY_CHECK, TaskStatus.CLIENT_SIGNOFF]),
    ("done", "Done", [TaskStatus.COMPLETED, TaskStatus.CLOSED]),
]

STATUS_TONE = {
    TaskStatus.COMPLETED: "ok", TaskStatus.CLOSED: "ok",
    TaskStatus.BLOCKED: "bad", TaskStatus.WAITING: "warn",
    TaskStatus.IN_PROGRESS: "warn",
    TaskStatus.QUALITY_CHECK: "purple", TaskStatus.CLIENT_SIGNOFF: "purple",
}

PRIORITY_TONE = {"critical": "bad", "high": "warn", "medium": "info",
                 "low": "", "planning": ""}


def _filtered_work(request):
    """One filter pipeline feeding every view — changing the view must never
    change the data."""
    qs = (Task.objects.all()
          .select_related("project", "phase", "assignee", "workspace")
          .prefetch_related("assignments__user"))
    f = request.GET
    if f.get("origin"):
        qs = qs.filter(origin=f["origin"])
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("priority"):
        qs = qs.filter(priority=f["priority"])
    if f.get("project"):
        qs = qs.filter(project_id=f["project"])
    if f.get("q"):
        qs = qs.filter(name__icontains=f["q"])
    scope = f.get("scope")
    if scope == "standalone":
        qs = qs.filter(project__isnull=True)
    elif scope == "project":
        qs = qs.filter(project__isnull=False)
    if f.get("mine"):
        qs = qs.filter(assignments__user=request.user).distinct()
    return qs


@login_required
def work_list(request):
    """Every unit of work, whatever its origin — one dataset, six lenses.
    The `view` parameter changes presentation only; filters are shared."""
    view = request.GET.get("view", "list")
    tasks = list(_filtered_work(request))

    ctx = {
        "tasks": tasks, "view": view,
        "origins": WorkOrigin.choices, "statuses": TaskStatus.choices,
        "priorities": TaskPriority.choices,
        "projects": Project.objects.all(),
        "f": request.GET,
        "status_tone": STATUS_TONE, "priority_tone": PRIORITY_TONE,
        "can_manage": has_work_perm(request.user, "work.edit"),
        "report": portfolio_report(),
    }

    if view == "board":
        ctx["lanes"] = [
            {"key": key, "label": label,
             "tasks": [t for t in tasks if t.status in members]}
            for key, label, members in BOARD_LANES
        ]
    elif view == "calendar":
        buckets = {}
        for t in tasks:
            if t.due_date:
                buckets.setdefault(t.due_date, []).append(t)
        ctx["calendar"] = sorted(buckets.items())
        ctx["undated"] = [t for t in tasks if not t.due_date]
    elif view == "workload":
        load = {}
        for t in tasks:
            for a in t.assignments.all():
                if a.role == Assignment.Role.WATCHER:
                    continue
                row = load.setdefault(a.user, {"user": a.user, "open": 0, "overdue": 0,
                                               "hours": Decimal("0")})
                if t.is_open:
                    row["open"] += 1
                    row["hours"] += t.estimated_hours or Decimal("0")
                if t.is_overdue:
                    row["overdue"] += 1
        rows = sorted(load.values(), key=lambda r: -r["open"])
        peak = max([r["open"] for r in rows], default=0) or 1
        for r in rows:
            r["pct"] = round(100 * r["open"] / peak)
        ctx["workload"] = rows
        ctx["unassigned"] = [t for t in tasks if t.is_open and not t.assignments.all()]

    return render(request, "web/work.html", ctx)


@login_required
def work_new(request):
    """The universal New Work wizard — the same front door for every origin."""
    if not has_work_perm(request.user, "work.create"):
        messages.error(request, "You do not have permission to create work.")
        return redirect("web:work")

    company = request.user.active_company
    if request.method == "POST":
        project = None
        if request.POST.get("project"):
            project = get_object_or_404(Project.objects.all(), pk=request.POST["project"])
        phase = None
        if request.POST.get("phase"):
            phase = get_object_or_404(Phase.objects.all(), pk=request.POST["phase"])

        owner = _user_or_none(request.POST.get("owner"))
        executors = [_user_or_none(u) for u in request.POST.getlist("executors")]
        watchers = [_user_or_none(u) for u in request.POST.getlist("watchers")]
        approvers = [_user_or_none(u) for u in request.POST.getlist("approvers")]
        labels = [s.strip() for s in request.POST.get("labels", "").split(",") if s.strip()]

        task = create_work(
            company, request.user,
            name=request.POST.get("name", "").strip() or "Untitled work",
            description=request.POST.get("description", "").strip(),
            origin=request.POST.get("origin") or WorkOrigin.MANUAL,
            project=project, phase=phase,
            priority=request.POST.get("priority") or TaskPriority.MEDIUM,
            risk_level=request.POST.get("risk_level") or RiskLevel.LOW,
            site=request.POST.get("site", "").strip(),
            department=request.POST.get("department", "").strip(),
            due_date=request.POST.get("due_date") or None,
            estimated_hours=_decimal_or_none(request.POST.get("estimated_hours")),
            labels=labels,
            is_billable=bool(request.POST.get("is_billable")),
            client_name=request.POST.get("client_name", "").strip(),
            owner=owner or request.user,
            executors=executors, watchers=watchers, approvers=approvers,
        )
        for line in request.POST.get("checklist", "").splitlines():
            if line.strip():
                add_checklist_item(task, request.user, label=line.strip())
        for f in request.FILES.getlist("attachments"):
            add_attachment(task, request.user, uploaded_file=f)

        messages.success(request, f"Work created: {task.name}.")
        return redirect("web:work_detail", pk=task.id)

    return render(request, "web/work_new.html", {
        "origins": WorkOrigin.choices,
        "priorities": TaskPriority.choices,
        "risks": RiskLevel.choices,
        "projects": Project.objects.all(),
        "people": _company_users(request.user),
    })


@login_required
def work_detail(request, pk):
    """The work item's own dashboard — progress, blockers, team, hierarchy,
    conversation and files in one place."""
    task = get_object_or_404(
        Task.objects.all().select_related("project", "phase", "assignee"), pk=pk)
    ctx = work_dashboard(task, request.user)
    ctx.update({
        "can_manage": has_work_perm(request.user, "work.edit"),
        "people": _company_users(request.user),
        "roles": Assignment.Role.choices,
        "dep_kinds": TaskDependency.Kind.choices,
        "status_tone": STATUS_TONE, "priority_tone": PRIORITY_TONE,
        "sibling_tasks": Task.objects.all().exclude(pk=task.pk)[:100],
        "subtasks": task.subtasks.prefetch_related("checklist_items"),
        "loose_checklist": task.checklist_items.filter(subtask__isnull=True),
    })
    return render(request, "web/work_detail.html", ctx)


def _work_guard(request, pk, code="work.edit"):
    """Shared permission gate for the work action endpoints. Each action names
    the granular permission it needs; `execution.manage` covers them all."""
    task = get_object_or_404(Task.objects.all(), pk=pk)
    if not has_work_perm(request.user, code):
        messages.error(request, "You do not have permission.")
        return task, False
    return task, True


@login_required
@require_POST
def work_start(request, pk):
    task, allowed = _work_guard(request, pk)
    if allowed:
        try:
            start_task(task, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Started: {task.name}.")
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_complete(request, pk):
    task, allowed = _work_guard(request, pk)
    if allowed:
        complete_task(task, request.user,
                      actual_hours=request.POST.get("actual_hours") or None)
        messages.success(request, f"Completed: {task.name}.")
    return redirect("web:work_detail", pk=pk)


#: Lifecycle moves that are more than an edit — they need their own permission.
_TRANSITION_PERM = {
    TaskStatus.QUALITY_CHECK: "work.approve",
    TaskStatus.CLIENT_SIGNOFF: "work.approve",
    TaskStatus.CLOSED: "work.close",
}


@login_required
@require_POST
def work_transition(request, pk):
    """Move the work through the lifecycle. Sign-off and closure are held to a
    higher permission than an ordinary edit."""
    target = request.POST.get("status")
    task, allowed = _work_guard(request, pk, _TRANSITION_PERM.get(target, "work.edit"))
    if allowed:
        if target not in dict(TaskStatus.choices):
            messages.error(request, "Unknown status.")
        else:
            try:
                transition(task, request.user, to_status=target,
                           note=request.POST.get("note", ""))
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"{task.name} → {task.get_status_display()}.")
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_subtask_add(request, pk):
    task, allowed = _work_guard(request, pk)
    if allowed and request.POST.get("name", "").strip():
        add_subtask(task, request.user, name=request.POST["name"].strip(),
                    assignee=_user_or_none(request.POST.get("assignee")),
                    due_date=request.POST.get("due_date") or None)
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_checklist_add(request, pk):
    task, allowed = _work_guard(request, pk)
    if allowed and request.POST.get("label", "").strip():
        subtask = None
        if request.POST.get("subtask"):
            subtask = get_object_or_404(Subtask.objects.all(), pk=request.POST["subtask"])
        add_checklist_item(task, request.user, label=request.POST["label"].strip(),
                           subtask=subtask)
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_checklist_toggle(request, pk, item_id):
    """Ticking an item rolls progress up the hierarchy — anyone on the work may
    do it (that is the point of a checklist), watchers excepted."""
    task = get_object_or_404(Task.objects.all(), pk=pk)
    item = get_object_or_404(ChecklistItem.objects.filter(task=task), pk=item_id)
    from apps.execution.services import can_modify
    if can_modify(task, request.user):
        toggle_checklist_item(item, request.user)
    else:
        messages.error(request, "You do not have permission.")
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_comment_add(request, pk):
    task = get_object_or_404(Task.objects.all(), pk=pk)
    body = request.POST.get("body", "").strip()
    if body:
        parent = None
        if request.POST.get("parent"):
            parent = task.comments.filter(pk=request.POST["parent"]).first()
        add_comment(task, request.user, body=body, parent=parent,
                    is_internal=not request.POST.get("customer_visible"))
    for f in request.FILES.getlist("files"):
        add_attachment(task, request.user, uploaded_file=f)
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_file_add(request, pk):
    task, allowed = _work_guard(request, pk, "work.files")
    if allowed:
        for f in request.FILES.getlist("files"):
            add_attachment(task, request.user, uploaded_file=f,
                           kind=request.POST.get("kind", "document"))
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_member(request, pk):
    task, allowed = _work_guard(request, pk, "work.assign")
    if allowed:
        user = _user_or_none(request.POST.get("user"))
        role = request.POST.get("role")
        if user and role in dict(Assignment.Role.choices):
            if request.POST.get("remove"):
                remove_member(task, user, role)
            else:
                add_member(task, user, role)
    return redirect("web:work_detail", pk=pk)


@login_required
@require_POST
def work_link(request, pk):
    task, allowed = _work_guard(request, pk)
    if allowed:
        other = Task.objects.filter(pk=request.POST.get("from_task")).first()
        if other:
            try:
                link_tasks(other, task, kind=request.POST.get("kind") or None)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Dependency added.")
    return redirect("web:work_detail", pk=pk)


@login_required
def work_decompose(request, pk):
    """LulaAI proposes a breakdown for this work. Read-only: computing a proposal
    writes nothing, so a manager can ask as often as they like and only what they
    tick ever becomes real."""
    task = get_object_or_404(Task.objects.all(), pk=pk)
    if not request.user.has_perm_code("ai.generate"):
        messages.error(request, "You do not have permission to use LulaAI.")
        return redirect("web:work_detail", pk=pk)

    draft = propose_decomposition(
        request.user.active_company, request.user,
        name=task.name, description=task.description,
        origin=task.origin, project=task.project,
    )
    record_proposal(request.user.active_company, request.user, task, draft)
    return render(request, "web/work_decompose.html", {
        "task": task, "draft": draft,
        "existing_checklist": {c.label.lower() for c in task.checklist_items.all()},
        "can_manage": has_work_perm(request.user, "work.edit"),
    })


@login_required
@require_POST
def work_decompose_apply(request, pk):
    """Create ONLY the ticked items. The draft is recomputed (it is deterministic)
    and the posted indexes select from it — nothing is trusted from the client
    except which items the human approved."""
    task, allowed = _work_guard(request, pk)
    if not allowed:
        return redirect("web:work_detail", pk=pk)
    if not request.user.has_perm_code("ai.generate"):
        messages.error(request, "You do not have permission to use LulaAI.")
        return redirect("web:work_detail", pk=pk)

    draft = propose_decomposition(
        request.user.active_company, request.user,
        name=task.name, description=task.description,
        origin=task.origin, project=task.project, enrich=False,
    )
    checklist_indexes = {int(i) for i in request.POST.getlist("checklist") if i.isdigit()}
    phase_indexes = {int(i) for i in request.POST.getlist("phases") if i.isdigit()}
    applied = apply_decomposition(
        task, request.user, draft,
        checklist_indexes=checklist_indexes, phase_indexes=phase_indexes,
        apply_hours=bool(request.POST.get("apply_hours")),
    )
    record_proposal(request.user.active_company, request.user, task, draft, applied=applied)

    if applied["checklist"] or applied["phases"] or applied["hours_set"]:
        messages.success(
            request,
            f"Added {applied['checklist']} checklist item(s)"
            f"{', ' + str(applied['phases']) + ' phase(s)' if applied['phases'] else ''}"
            f"{', set the estimate' if applied['hours_set'] else ''}.")
    else:
        messages.error(request, "Nothing selected — nothing was created.")
    return redirect("web:work_detail", pk=pk)


@login_required
def notifications(request):
    from apps.execution.models import Notification
    rows = Notification.objects.filter(user=request.user).select_related("task")[:100]
    if request.method == "POST":
        mark_notifications_read(request.user)
        return redirect("web:notifications")
    return render(request, "web/notifications.html",
                  {"rows": rows, "unread": unread_count(request.user)})


@login_required
@require_POST
def project_phase_add(request, pk):
    project = get_object_or_404(Project.objects.all(), pk=pk)
    if not has_work_perm(request.user, "work.edit"):
        messages.error(request, "You do not have permission.")
    elif request.POST.get("seed"):
        ensure_default_phases(project, request.user)
        messages.success(request, "Default phases added.")
    elif request.POST.get("name", "").strip():
        add_phase(project, request.user, name=request.POST["name"].strip())
        messages.success(request, "Phase added.")
    return redirect("web:project_detail", pk=pk)


# ══════════════════════════════════════════════════════════════════════════════
# People — the company's own members (the pool every work picker reads from)
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def people(request):
    """Who works here. Managing members needs `users.invite`; everyone else may
    see the team (they work with these people)."""
    company = request.user.active_company
    members = company_members(company)
    return render(request, "web/people.html", {
        "members": members,
        "active_count": sum(1 for m in members if m.status == "active"),
        "roles": selectable_roles(),
        "can_manage": request.user.has_perm_code("users.invite"),
        # Surfaced once, immediately after creation — never stored or re-shown.
        "new_password": request.session.pop("new_member_password", None),
        "new_email": request.session.pop("new_member_email", None),
    })


@login_required
@require_POST
def people_add(request):
    if not request.user.has_perm_code("users.invite"):
        messages.error(request, "You do not have permission to manage members.")
        return redirect("web:people")

    role = Role.objects.filter(pk=request.POST.get("role")).first()
    if role is None:
        messages.error(request, "Choose a role for the new member.")
        return redirect("web:people")

    try:
        membership, temp_password = add_company_member(
            request.user.active_company, request.user,
            email=request.POST.get("email", ""),
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            job_title=request.POST.get("job_title", ""),
            mobile=request.POST.get("mobile", ""),
            role=role,
        )
    except MemberError as exc:
        messages.error(request, str(exc))
        return redirect("web:people")

    if temp_password:
        # Carried in the session for exactly one render, then popped.
        request.session["new_member_password"] = temp_password
        request.session["new_member_email"] = membership.user.email
        messages.success(request, f"{membership.user.email} added.")
    else:
        messages.success(
            request,
            f"{membership.user.email} already had a LulaWorks account and has "
            "been added to this company with their existing password.")
    return redirect("web:people")


@login_required
@require_POST
def people_role(request, pk):
    if not request.user.has_perm_code("users.invite"):
        messages.error(request, "You do not have permission to manage members.")
        return redirect("web:people")
    membership = get_object_or_404(
        Membership.objects.filter(company=request.user.active_company), pk=pk)
    role = Role.objects.filter(pk=request.POST.get("role")).first()
    if role is None:
        messages.error(request, "Unknown role.")
    else:
        set_member_role(membership, role)
        messages.success(request, f"{membership.user.email} is now {role.name}.")
    return redirect("web:people")


@login_required
@require_POST
def people_status(request, pk):
    """Deactivate or restore. Never deletes — a departed employee stays attached
    to the work, timesheets and sign-offs they touched."""
    if not request.user.has_perm_code("users.invite"):
        messages.error(request, "You do not have permission to manage members.")
        return redirect("web:people")
    membership = get_object_or_404(
        Membership.objects.filter(company=request.user.active_company), pk=pk)
    activate = bool(request.POST.get("activate"))
    try:
        set_member_status(membership, request.user, active=activate)
    except MemberError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"{membership.user.email} " + ("restored." if activate else "deactivated."))
    return redirect("web:people")


@login_required
def person_detail(request, pk):
    """One member: who they are, what they are on right now, and what they have
    finished. Reads through Assignment, so every role they hold shows up."""
    membership = get_object_or_404(
        Membership.objects.select_related("user", "role")
                  .filter(company=request.user.active_company), pk=pk)
    work = member_work(membership.user, request.user.active_company)
    return render(request, "web/person_detail.html", {
        "membership": membership,
        "person": membership.user,
        "work": work,
        "roles": selectable_roles(),
        "can_manage": request.user.has_perm_code("users.invite"),
        "is_me": membership.user_id == request.user.id,
    })


@login_required
def profile(request):
    """Your own profile — the one page every member can edit for themselves,
    whatever their role. Adding a photo is the first thing a new member does
    after signing in with the password they were handed."""
    user = request.user
    if request.method == "POST":
        if request.POST.get("remove_avatar") and user.avatar:
            user.avatar.delete(save=False)
            user.avatar = None
            user.save(update_fields=["avatar"])
            messages.success(request, "Photo removed.")
            return redirect("web:profile")

        upload = request.FILES.get("avatar")
        if upload is not None:
            error = _validate_avatar(upload)
            if error:
                messages.error(request, error)
                return redirect("web:profile")
            user.avatar = upload

        user.first_name = request.POST.get("first_name", "").strip()
        user.last_name = request.POST.get("last_name", "").strip()
        user.mobile = request.POST.get("mobile", "").strip()
        user.save(update_fields=["first_name", "last_name", "mobile", "avatar"])
        messages.success(request, "Profile updated.")
        return redirect("web:profile")

    membership = Membership.objects.filter(
        company=user.active_company, user=user).select_related("role").first()
    return render(request, "web/profile.html", {
        "person": user, "membership": membership,
        "work": member_work(user, user.active_company),
    })


#: Anything bigger is a phone photo nobody needs as an avatar.
MAX_AVATAR_BYTES = 5 * 1024 * 1024
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _validate_avatar(upload):
    """Reject by declared type AND by actually decoding the file — a renamed
    executable must not be stored just because it ends in .png."""
    if upload.size > MAX_AVATAR_BYTES:
        return "That image is larger than 5 MB — please use a smaller one."
    if upload.content_type not in ALLOWED_AVATAR_TYPES:
        return "Please upload a JPEG, PNG, WebP or GIF image."
    try:
        from PIL import Image
        image = Image.open(upload)
        image.verify()          # raises unless this really is an image
    except Exception:           # noqa: BLE001 - any decode failure is a rejection
        return "That file is not a readable image."
    finally:
        upload.seek(0)
    return None


@login_required
def change_password(request):
    """Choose your own password. Forced (via middleware) for accounts a manager
    created with a temporary one; available to anyone otherwise."""
    forced = request.user.must_change_password
    if request.method == "POST":
        current = request.POST.get("current_password", "")
        new = request.POST.get("new_password", "")
        confirm = request.POST.get("confirm_password", "")

        if not request.user.check_password(current):
            messages.error(request, "Your current password is not correct.")
        elif len(new) < 10:
            messages.error(request, "Choose at least 10 characters.")
        elif new != confirm:
            messages.error(request, "The two new passwords do not match.")
        elif new == current:
            messages.error(request, "The new password must be different.")
        else:
            set_password(request.user, new)
            # Changing the hash rotates the session auth hash — keep them signed in.
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)
            messages.success(request, "Password updated.")
            return redirect("web:dashboard")

    return render(request, "web/change_password.html", {"forced": forced})


# ── Small helpers for the work forms ──────────────────────────────────────────

def _company_users(user):
    """The pool every team picker offers: ACTIVE members of the acting user's
    company. Someone who has left stops being assignable but keeps their name on
    the work they already did."""
    return assignable_users(user.active_company)


def _user_or_none(value):
    if not value:
        return None
    from apps.identity.models import User
    return User.objects.filter(pk=value).first()


def _decimal_or_none(value):
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return None


# ── Quotations (view · review · edit · download PDF) ──────────────────────────

@login_required
def quotations_list(request):
    return render(request, "web/quotations.html", {
        "quotations": Quotation.objects.all().prefetch_related("lines"),
        "can_view_money": _can_view_money(request.user),
    })


@login_required
def quotation_detail(request, pk):
    quote = get_object_or_404(Quotation.objects.all().prefetch_related("lines"), pk=pk)
    return render(request, "web/quotation_detail.html", {
        "quote": quote,
        "can_view_money": _can_view_money(request.user),
        "can_edit": (quote.status == QuotationStatus.DRAFT
                     and request.user.has_perm_code("quotes.create")),
    })


@login_required
def quotation_edit(request, pk):
    quote = get_object_or_404(Quotation.objects.all().prefetch_related("lines"), pk=pk)
    if not request.user.has_perm_code("quotes.create"):
        messages.error(request, "You do not have permission to edit quotations.")
        return redirect("web:quotation_detail", pk=pk)
    if quote.status != QuotationStatus.DRAFT:
        messages.error(request, "Only draft quotations can be edited.")
        return redirect("web:quotation_detail", pk=pk)

    if request.method == "POST":
        # Rebuild lines from the posted rows (parallel arrays); blank rows dropped.
        descriptions = request.POST.getlist("description")
        qtys = request.POST.getlist("qty")
        units = request.POST.getlist("unit")
        prices = request.POST.getlist("unit_price")
        lines = [
            {"description": d, "qty": q, "unit": u, "unit_price": p}
            for d, q, u, p in zip(descriptions, qtys, units, prices, strict=False)
        ]
        update_quotation(
            quote, request.user,
            title=request.POST.get("title", ""),
            client_name=request.POST.get("client_name", ""),
            site=request.POST.get("site", ""),
            vat_rate=request.POST.get("vat_rate"),
            validity_date=request.POST.get("validity_date") or None,
            notes=request.POST.get("notes", ""),
            lines=lines,
        )
        messages.success(request, f"Quotation {quote.number} saved.")
        return redirect("web:quotation_detail", pk=pk)

    # a couple of blank rows so the manager can add lines
    return render(request, "web/quotation_edit.html", {
        "quote": quote, "blank_rows": range(3),
    })


@login_required
def quotation_pdf(request, pk):
    quote = get_object_or_404(Quotation.objects.all().prefetch_related("lines"), pk=pk)
    pdf = quotation_pdf_bytes(quote)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{quote.number}.pdf"'
    return resp


# ── RFQ (the front door: upload → extract → review → approve → quotation) ─────

@login_required
def rfq_list(request):
    return render(request, "web/rfq.html", {
        "rfqs": RFQDocument.objects.all(),
        "can_upload": request.user.has_perm_code("rfq.upload"),
    })


@login_required
@require_POST
def rfq_upload(request):
    if not request.user.has_perm_code("rfq.upload"):
        messages.error(request, "You do not have permission to upload RFQs.")
        return redirect("web:rfq")
    upload = request.FILES.get("file")
    pasted = request.POST.get("text", "").strip()

    if upload is not None:
        rfq = ingest_rfq(request.user.active_company, request.user,
                         uploaded_file=upload, original_name=upload.name)
    elif pasted:
        try:
            rfq = ingest_rfq_text(
                request.user.active_company, request.user, text=pasted,
                original_name=request.POST.get("label", ""))
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("web:rfq")
    else:
        messages.error(request, "Attach a document or paste the RFQ text.")
        return redirect("web:rfq")

    messages.success(request, f"Extracted {rfq.fields.count()} field(s) and "
                              f"{rfq.lines.count()} line(s). Review below.")
    return redirect("web:rfq_detail", pk=rfq.id)


@login_required
def rfq_detail(request, pk):
    rfq = get_object_or_404(
        RFQDocument.objects.all().prefetch_related("fields", "lines"), pk=pk)
    editable = (rfq.status not in ("approved", "rejected")
                and request.user.has_perm_code("rfq.upload"))
    context = {
        "rfq": rfq,
        "can_edit": editable,
        "can_approve": (rfq.status not in ("approved", "rejected")
                        and request.user.has_perm_code("rfq.approve")),
    }
    return render(request, "web/rfq_detail.html", context)


@login_required
@require_POST
def rfq_line(request, pk):
    """Add, edit or remove a line by hand — the reviewer has the last word."""
    rfq = get_object_or_404(RFQDocument.objects.all(), pk=pk)
    if not request.user.has_perm_code("rfq.upload"):
        messages.error(request, "You do not have permission to edit this RFQ.")
        return redirect("web:rfq_detail", pk=pk)
    if rfq.status in ("approved", "rejected"):
        messages.error(request, "This RFQ is closed — it can no longer be edited.")
        return redirect("web:rfq_detail", pk=pk)

    action = request.POST.get("action", "add")
    if action == "add":
        description = request.POST.get("description", "").strip()
        if not description:
            messages.error(request, "Enter a description.")
        else:
            add_line_item(rfq, request.user, description=description,
                          qty=_decimal_or_none(request.POST.get("qty")) or 1,
                          unit=request.POST.get("unit", "each").strip() or "each",
                          unit_price=_decimal_or_none(request.POST.get("unit_price")))
            messages.success(request, "Line added.")
    else:
        line = get_object_or_404(RFQLineItem.objects.filter(rfq=rfq),
                                 pk=request.POST.get("line"))
        if action == "delete":
            delete_line_item(line)
            messages.success(request, "Line removed.")
        else:
            update_line_item(
                line, request.user,
                description=request.POST.get("description"),
                qty=_decimal_or_none(request.POST.get("qty")),
                unit=request.POST.get("unit"),
                unit_price=_decimal_or_none(request.POST.get("unit_price")))
            messages.success(request, "Line updated.")
    return redirect("web:rfq_detail", pk=pk)


@login_required
@require_POST
def rfq_approve(request, pk):
    """Human approval (never automatic) → creates the Quotation + Project DNA."""
    if not request.user.has_perm_code("rfq.approve"):
        messages.error(request, "You do not have permission to approve RFQs.")
        return redirect("web:rfq_detail", pk=pk)
    rfq = get_object_or_404(RFQDocument.objects.all(), pk=pk)
    client_name = request.POST.get("client_name", "").strip()
    if not client_name:
        messages.error(request, "Enter the client name to create the quotation.")
        return redirect("web:rfq_detail", pk=pk)
    approve_rfq(rfq, request.user, client_name=client_name)
    rfq.refresh_from_db()
    messages.success(request, f"Approved → quotation {rfq.quotation.number} created. "
                              "Next: build an estimate, then award to a project.")
    return redirect("web:rfq_detail", pk=pk)


# ── Estimates ─────────────────────────────────────────────────────────────────

@login_required
def estimates_list(request):
    estimates = Estimate.objects.all().order_by("-created_at")
    return render(request, "web/estimates.html",
                  {"estimates": estimates, "can_view_money": _can_view_money(request.user)})


@login_required
def estimate_detail(request, pk):
    estimate = get_object_or_404(Estimate.objects.all().prefetch_related("sections__lines"), pk=pk)
    can_approve = (estimate.status in (EstimateStatus.REVIEW, EstimateStatus.AWAITING_APPROVAL)
                   and request.user.has_perm_code("estimating.approve"))
    return render(request, "web/estimate_detail.html", {
        "estimate": estimate,
        "can_view_money": _can_view_money(request.user),
        "can_approve": can_approve,
        "can_revise": (estimate.status != EstimateStatus.SUPERSEDED
                       and request.user.has_perm_code("estimating.manage")),
    })


@login_required
@require_POST
def estimate_approve(request, pk):
    if not request.user.has_perm_code("estimating.approve"):
        messages.error(request, "You do not have permission to approve estimates.")
        return redirect("web:estimate_detail", pk=pk)
    estimate = get_object_or_404(Estimate.objects.all(), pk=pk)
    approve_estimate(estimate, request.user)
    messages.success(request, f"Estimate {estimate.number} approved.")
    return redirect("web:estimate_detail", pk=pk)


# ── Procurement ───────────────────────────────────────────────────────────────

@login_required
def suppliers_list(request):
    suppliers = Supplier.objects.all().order_by("-performance_score", "name")
    return render(request, "web/suppliers.html", {"suppliers": suppliers})


@login_required
def purchase_orders_list(request):
    pos = PurchaseOrder.objects.all().select_related("supplier").prefetch_related("lines")
    return render(request, "web/purchase_orders.html",
                  {"purchase_orders": pos, "can_view_money": _can_view_money(request.user)})


@login_required
def po_detail(request, pk):
    po = get_object_or_404(
        PurchaseOrder.objects.all().select_related("supplier").prefetch_related("lines"), pk=pk)
    can_approve = (po.status in ("draft", "pending_approval")
                   and request.user.has_perm_code("po.approve"))
    return render(request, "web/po_detail.html", {
        "po": po,
        "match": three_way_match(po),
        "can_view_money": _can_view_money(request.user),
        "can_approve": can_approve,
        "can_receive": request.user.has_perm_code("procurement.manage"),
    })


@login_required
@require_POST
def po_approve(request, pk):
    if not request.user.has_perm_code("po.approve"):
        messages.error(request, "You do not have permission to approve purchase orders.")
        return redirect("web:po_detail", pk=pk)
    po = get_object_or_404(PurchaseOrder.objects.all(), pk=pk)
    po.status = "approved"
    po.approved_by = request.user
    po.save(update_fields=["status", "approved_by"])
    messages.success(request, f"Purchase order {po.number} approved.")
    return redirect("web:po_detail", pk=pk)


# ── Commercial (finance only) ─────────────────────────────────────────────────

@login_required
def commercial(request):
    if not _can_view_money(request.user):
        messages.error(request, "Commercial data requires the finance permission.")
        return redirect("web:dashboard")
    dash = commercial_dashboard(request.user.active_company)
    aging = dash["aging"]
    # "90+" can't be reached via template dot-notation, so pre-build ordered rows.
    aging_rows = [("Current", aging["current"]), ("30 days", aging["30"]),
                  ("60 days", aging["60"]), ("90+ days", aging["90+"])]
    return render(request, "web/commercial.html", {
        "commercial": dash,
        "aging_rows": aging_rows,
        "invoices": Invoice.objects.all().select_related("project").prefetch_related(
            "lines", "payments"),
        "can_finance": request.user.has_perm_code("finance.manage"),
    })


# ── Lulama (AI orchestrator) ──────────────────────────────────────────────────

@login_required
def lulama(request):
    if not request.user.has_perm_code("ai.generate"):
        messages.error(request, "AI features require the ai.generate permission.")
        return redirect("web:dashboard")
    context = {"projects": Project.objects.all(), "request_text": "Prepare this project"}
    if request.method == "POST":
        project = None
        pid = request.POST.get("project")
        if pid:
            project = get_object_or_404(Project.objects.all(), pk=pid)
        text = request.POST.get("request", "").strip() or "Give me a status overview"
        context["request_text"] = text
        context["selected_project"] = pid
        context["interaction"] = orchestrate(
            request.user.active_company, request.user, text, project=project)
    return render(request, "web/lulama.html", context)


# ── Operating actions (managers actually do the work here) ────────────────────

def _to_decimal(raw, default="0"):
    try:
        return Decimal(str(raw or default))
    except (InvalidOperation, TypeError):
        return Decimal(default)


@login_required
@require_POST
def compliance_item_approve(request, pk):
    """Approve a compliance item from the project page — the gate recomputes live."""
    if not request.user.has_perm_code("compliance.override"):
        messages.error(request, "You do not have permission to approve compliance items.")
        return redirect("web:dashboard")
    item = get_object_or_404(ComplianceItem.objects.all().select_related("project"), pk=pk)
    expiry = request.POST.get("expiry") or None
    approve_item(item, request.user, expiry=expiry)
    messages.success(request, f"Approved: {item.name}.")
    return redirect("web:project_detail", pk=item.project_id)


@login_required
@require_POST
def project_override(request, pk):
    """Authorised override of the compliance gate — reason required, audited."""
    if not request.user.has_perm_code("compliance.override"):
        messages.error(request, "You do not have permission to override compliance.")
        return redirect("web:project_detail", pk=pk)
    project = get_object_or_404(Project.objects.all(), pk=pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "An override needs a reason.")
        return redirect("web:project_detail", pk=pk)
    override_gate(project, request.user, reason=reason)
    messages.success(request, "Compliance gate overridden (audited).")
    return redirect("web:project_detail", pk=pk)


@login_required
@require_POST
def po_receive(request, pk):
    """Record goods received (a GRN) against a PO — feeds the 3-way match."""
    if not request.user.has_perm_code("procurement.manage"):
        messages.error(request, "You do not have permission to receive goods.")
        return redirect("web:po_detail", pk=pk)
    po = get_object_or_404(PurchaseOrder.objects.all().prefetch_related("lines"), pk=pk)
    company = request.user.active_company
    grn = GRN.objects.create(company=company, purchase_order=po, seq=po.grns.count() + 1,
                             date=timezone.localdate(), received_by=request.user,
                             created_by=request.user, updated_by=request.user)
    received = 0
    for line in po.lines.all():
        qty = _to_decimal(request.POST.get(f"qty_{line.id}"))
        if qty > 0:
            GRNLine.objects.create(company=company, grn=grn, po_line=line,
                                   description=line.description, qty_received=qty,
                                   created_by=request.user, updated_by=request.user)
            received += 1
    if received:
        messages.success(request, f"Goods received on {po.number} ({received} line(s)).")
    else:
        grn.delete(hard=True)
        messages.error(request, "Enter a received quantity on at least one line.")
    return redirect("web:po_detail", pk=pk)


@login_required
@require_POST
def estimate_revise(request, pk):
    """Create a new estimate revision (the prior one is superseded, never overwritten)."""
    if not request.user.has_perm_code("estimating.manage"):
        messages.error(request, "You do not have permission to revise estimates.")
        return redirect("web:estimate_detail", pk=pk)
    estimate = get_object_or_404(Estimate.objects.all(), pk=pk)
    new = create_revision(estimate, request.user, reason=request.POST.get("reason", "").strip())
    messages.success(request, f"Created revision v{new.version}.")
    return redirect("web:estimate_detail", pk=new.id)


@login_required
@require_POST
def project_progress_claim(request, pk):
    """Raise a progress claim (a % of the contract value, with retention held)."""
    if not request.user.has_perm_code("finance.manage"):
        messages.error(request, "You do not have permission to raise claims.")
        return redirect("web:project_detail", pk=pk)
    project = get_object_or_404(Project.objects.all(), pk=pk)
    pct = _to_decimal(request.POST.get("percent_complete"))
    retention = _to_decimal(request.POST.get("retention"), "10")
    invoice = create_progress_claim(project, request.user, percent_complete=pct,
                                    retention_pct=retention)
    messages.success(request, f"Progress claim {invoice.number} raised (draft).")
    return redirect("web:project_detail", pk=pk)


@login_required
@require_POST
def invoice_payment(request, pk):
    """Record a customer payment against an invoice (POP)."""
    if not request.user.has_perm_code("finance.manage"):
        messages.error(request, "You do not have permission to record payments.")
        return redirect("web:commercial")
    invoice = get_object_or_404(Invoice.objects.all(), pk=pk)
    amount = _to_decimal(request.POST.get("amount"))
    if amount <= 0:
        messages.error(request, "Enter a payment amount.")
        return redirect("web:commercial")
    record_payment(invoice, request.user, amount=amount,
                   reference=request.POST.get("reference", ""))
    messages.success(request, f"Payment of R{amount} recorded on {invoice.number}.")
    return redirect("web:commercial")
