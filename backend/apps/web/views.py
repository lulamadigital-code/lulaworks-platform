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
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.ai_platform.orchestrator import orchestrate
from apps.compliance.models import ComplianceItem
from apps.compliance.services import approve_item, recompute_readiness
from apps.compliance.services import override as override_gate
from apps.estimating.models import Estimate, EstimateStatus
from apps.estimating.services import approve_estimate, create_revision
from apps.execution.services import project_health
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


@login_required
def dashboard(request):
    """Portfolio home: attention list (compliance-blocked projects) for everyone;
    the commercial panel only for money-authorised managers (Golden Rule)."""
    projects = list(Project.objects.all().select_related("quotation"))
    attention = []
    for p in projects:
        r = recompute_readiness(p)
        if r["gate_status"] == "not_ready":
            attention.append({"project": p, "readiness": r})

    context = {
        "project_count": len(projects),
        "attention": attention,
        "in_execution": sum(1 for p in projects if p.status == ProjectStatus.IN_EXECUTION),
        "ready": sum(1 for p in projects if p.status == ProjectStatus.READY),
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
