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

from apps.ai_platform.orchestrator import orchestrate
from apps.compliance.models import ComplianceItem
from apps.compliance.services import approve_item, recompute_readiness
from apps.compliance.services import override as override_gate
from apps.estimating.models import Estimate, EstimateStatus
from apps.estimating.services import approve_estimate, create_revision
from apps.execution.models import Task, WorkOrigin
from apps.execution.services import (
    complete_task,
    compute_task_readiness,
    create_work,
    project_health,
    start_task,
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
from apps.rfq.services import approve_rfq, ingest_rfq


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
    "draft": ("#c4c7d0", "Draft"), "planned": ("#c4c7d0", "Planned"),
    "ready": ("#fdab3d", "Ready"), "in_progress": ("#579bfc", "In progress"),
    "on_hold": ("#a25ddc", "On hold"), "blocked": ("#e2445c", "Blocked"),
    "awaiting_inspection": ("#a25ddc", "Quality check"), "completed": ("#00c875", "Completed"),
    "cancelled": ("#c4c7d0", "Cancelled"),
}
_ORIGIN_META = {
    "rfq": ("#a25ddc", "RFQ / Tender"), "manual": ("#579bfc", "Manual"),
    "recurring": ("#00c875", "Recurring"), "customer_request": ("#fdab3d", "Customer request"),
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


# ── Work (the unified engine: RFQ / manual / recurring — all one Work list) ───

@login_required
def work_list(request):
    """Every unit of work, whatever its origin — project tasks and standalone
    jobs in one list (the unified Work engine)."""
    qs = Task.objects.all().select_related("project", "assignee")
    origin = request.GET.get("origin")
    if origin:
        qs = qs.filter(origin=origin)
    scope = request.GET.get("scope")
    if scope == "standalone":
        qs = qs.filter(project__isnull=True)
    elif scope == "project":
        qs = qs.filter(project__isnull=False)
    return render(request, "web/work.html", {
        "tasks": qs, "origins": WorkOrigin.choices,
        "origin": origin, "scope": scope,
        "can_manage": request.user.has_perm_code("execution.manage"),
    })


@login_required
def work_new(request):
    if not request.user.has_perm_code("execution.manage"):
        messages.error(request, "You do not have permission to create work.")
        return redirect("web:work")
    if request.method == "POST":
        project = None
        if request.POST.get("project"):
            project = get_object_or_404(Project.objects.all(), pk=request.POST["project"])
        task = create_work(
            request.user.active_company, request.user,
            name=request.POST.get("name", "").strip() or "Untitled work",
            description=request.POST.get("description", "").strip(),
            origin=request.POST.get("origin") or WorkOrigin.MANUAL,
            project=project,
            is_billable=bool(request.POST.get("is_billable")),
            client_name=request.POST.get("client_name", "").strip(),
        )
        messages.success(request, f"Work created: {task.name}.")
        return redirect("web:work_detail", pk=task.id)
    return render(request, "web/work_new.html", {
        "origins": WorkOrigin.choices,
        "projects": Project.objects.all(),
    })


@login_required
def work_detail(request, pk):
    task = get_object_or_404(
        Task.objects.all().select_related("project", "assignee"), pk=pk)
    status, reason = compute_task_readiness(task)
    return render(request, "web/work_detail.html", {
        "task": task, "readiness_status": status, "readiness_reason": reason,
        "can_manage": request.user.has_perm_code("execution.manage"),
    })


@login_required
@require_POST
def work_start(request, pk):
    if not request.user.has_perm_code("execution.manage"):
        messages.error(request, "You do not have permission.")
        return redirect("web:work_detail", pk=pk)
    task = get_object_or_404(Task.objects.all(), pk=pk)
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
    if not request.user.has_perm_code("execution.manage"):
        messages.error(request, "You do not have permission.")
        return redirect("web:work_detail", pk=pk)
    task = get_object_or_404(Task.objects.all(), pk=pk)
    complete_task(task, request.user, actual_hours=request.POST.get("actual_hours") or None)
    messages.success(request, f"Completed: {task.name}.")
    return redirect("web:work_detail", pk=pk)


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
    if upload is None:
        messages.error(request, "Choose a file to upload.")
        return redirect("web:rfq")
    rfq = ingest_rfq(request.user.active_company, request.user,
                     uploaded_file=upload, original_name=upload.name)
    messages.success(request, f"Extracted {rfq.fields.count()} field(s) and "
                              f"{rfq.lines.count()} line(s). Review below.")
    return redirect("web:rfq_detail", pk=rfq.id)


@login_required
def rfq_detail(request, pk):
    rfq = get_object_or_404(
        RFQDocument.objects.all().prefetch_related("fields", "lines"), pk=pk)
    return render(request, "web/rfq_detail.html", {
        "rfq": rfq,
        "can_approve": (rfq.status not in ("approved", "rejected")
                        and request.user.has_perm_code("rfq.approve")),
    })


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
