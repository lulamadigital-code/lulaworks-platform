"""Finance services (FINANCE.md / Module 10) — the money loop closing.

Project Budget from the approved Estimate, actuals convergence from every module,
the live Profitability Engine, the explainable Project Profit Predictor, the
invoice/retention/progress-claim lifecycle, variation budget impact, and the
commercial dashboard.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.administration.services import next_number, record_audit
from apps.core.events import publish

from .models import (
    BudgetLine,
    CostCategory,
    CostEntry,
    CostSource,
    Invoice,
    InvoiceStatus,
    ProjectBudget,
    Variation,
    VariationStatus,
)

TWO = Decimal("0.01")


# ── Project Budget from the approved Estimate (the baseline, §3) ──────────────

@transaction.atomic
def create_budget_from_estimate(project, user=None) -> ProjectBudget | None:
    """Create the project's budget baseline from its approved estimate. Category
    cost lines + revenue (selling price) + expected margin. Idempotent per project."""
    from apps.estimating.models import Estimate, EstimateStatus
    if not project.quotation_id:
        return None
    estimate = (
        Estimate.objects.filter(quotation_id=project.quotation_id,
                                status=EstimateStatus.APPROVED)
        .order_by("-version").first()
    )
    if estimate is None:
        return None

    budget, _ = ProjectBudget.objects.get_or_create(
        project=project,
        defaults={"company": project.company, "created_by": user, "updated_by": user},
    )
    budget.source_estimate = estimate
    budget.revenue = estimate.selling_price
    budget.expected_margin_pct = estimate.margin_pct
    budget.save(update_fields=["source_estimate", "revenue", "expected_margin_pct", "updated_at"])

    # Rebuild category budget lines from the estimate sections.
    budget.lines.all().delete()
    by_cat: dict[str, Decimal] = {}
    for section in estimate.sections.all():
        by_cat[section.category] = by_cat.get(section.category, Decimal("0")) + section.subtotal
    # Estimate categories map 1:1 onto finance categories (labour/material/…).
    for category, amount in by_cat.items():
        BudgetLine.objects.create(company=project.company, budget=budget,
                                  category=category, amount=amount.quantize(TWO))
    if estimate.contingency_amount:
        BudgetLine.objects.create(company=project.company, budget=budget,
                                  category=CostCategory.CONTINGENCY,
                                  amount=estimate.contingency_amount)
    publish("BudgetCreated", company=project.company, subject=project, actor=user,
            payload={"project": project.number, "revenue": str(budget.revenue)})
    return budget


# ── Actuals convergence — costs from every module land here (§4) ──────────────

def _upsert_cost(project, *, category, amount, source, source_ref="", user=None):
    entry, _ = CostEntry.objects.get_or_create(
        project=project, category=category, source=source,
        defaults={"company": project.company, "created_by": user, "updated_by": user},
    )
    entry.amount = Decimal(amount).quantize(TWO)
    entry.source_ref = source_ref
    entry.date = timezone.localdate()
    entry.updated_by = user
    entry.save(update_fields=["amount", "source_ref", "date", "updated_by", "updated_at"])
    return entry


def rebuild_actuals_from_sources(project, user=None) -> dict:
    """Pull actual costs from the operational modules into the CostEntry ledger:
    labour from approved timesheets, material from 3-way-matchable supplier
    invoices, and the crew's field spend (material/fuel/expense receipts captured
    on the job's tasks — WES). Manual/variation entries are left untouched
    (upsert by source), so nothing double-counts."""
    from apps.execution.services import project_actual_costs
    from apps.execution.work_execution import project_field_spend
    costs = project_actual_costs(project)
    _upsert_cost(project, category=CostCategory.LABOUR, amount=costs["labour"],
                 source=CostSource.TIMESHEETS, source_ref="approved timesheets", user=user)
    _upsert_cost(project, category=CostCategory.MATERIAL, amount=costs["material"],
                 source=CostSource.SUPPLIER_INVOICES, source_ref="supplier invoices", user=user)

    # WES field spend — real cash/card money captured on the job's tasks, keyed by
    # its own source so it sums alongside (never overwrites) procurement material.
    # Field spend spans a *dynamic* set of categories, so we replace the prior
    # field entries wholesale: that way an edited or deleted receipt — a category
    # that drops to zero — self-corrects instead of leaving a stale ledger row.
    field = project_field_spend(project)
    with transaction.atomic():
        project.cost_entries.filter(source=CostSource.FIELD_REPORTS).delete()
        for category, amount in field.items():
            _upsert_cost(project, category=category, amount=amount,
                         source=CostSource.FIELD_REPORTS, source_ref="field reports", user=user)

    return {"labour": costs["labour"], "material": costs["material"], "field": field}


def actual_cost(project) -> Decimal:
    return sum((e.amount for e in project.cost_entries.all()), Decimal("0.00"))


def budget_vs_actual(project) -> dict:
    """Per-category budget vs actual + variance (live convergence)."""
    budget = getattr(project, "budget", None)
    budgeted = {}
    if budget:
        for line in budget.lines.all():
            budgeted[line.category] = budgeted.get(line.category, Decimal("0")) + line.amount
    actual = {}
    for e in project.cost_entries.all():
        actual[e.category] = actual.get(e.category, Decimal("0")) + e.amount

    rows = []
    for cat in sorted(set(budgeted) | set(actual)):
        b = budgeted.get(cat, Decimal("0"))
        a = actual.get(cat, Decimal("0"))
        rows.append({"category": cat, "budget": str(b), "actual": str(a),
                     "variance": str((a - b).quantize(TWO))})
    return {"lines": rows,
            "total_budget": str(sum(budgeted.values(), Decimal("0")).quantize(TWO)),
            "total_actual": str(sum(actual.values(), Decimal("0")).quantize(TWO))}


# ── Live Profitability Engine (§9) ────────────────────────────────────────────

def profitability(project) -> dict:
    """Estimated vs actual: revenue, cost, gross profit, margin, budget variance."""
    budget = getattr(project, "budget", None)
    revenue = budget.revenue if budget else Decimal("0")
    total_budget = budget.total_cost_budget if budget else Decimal("0")
    cost = actual_cost(project)
    gross_profit = (revenue - cost).quantize(TWO)
    margin = (gross_profit / revenue * 100).quantize(TWO) if revenue else Decimal("0.00")
    return {
        "revenue": str(revenue), "actual_cost": str(cost.quantize(TWO)),
        "gross_profit": str(gross_profit), "margin_pct": str(margin),
        "budget_cost": str(total_budget.quantize(TWO)),
        "budget_variance": str((cost - total_budget).quantize(TWO)),
    }


# ── Project Profit Predictor (§10 — the early-warning moat) ───────────────────

def profit_forecast(project) -> dict:
    """Continuously forecast the FINAL outcome from current trend — so managers can
    act before a project turns unprofitable. Explainable: cites the numbers and the
    largest contributors."""
    from apps.execution.services import recompute_project_progress
    budget = getattr(project, "budget", None)
    revenue = budget.revenue if budget else Decimal("0")
    total_budget = budget.total_cost_budget if budget else Decimal("0")
    cost = actual_cost(project)

    completion = Decimal(recompute_project_progress(project)) / 100  # 0..1
    consumed = (cost / total_budget) if total_budget else Decimal("0")

    # Trend extrapolation: at X% complete having spent Y, projected final = cost / X.
    if completion > 0:
        projected_cost = (cost / completion).quantize(TWO)
    else:
        projected_cost = total_budget
    projected_profit = (revenue - projected_cost).quantize(TWO)
    projected_margin = (projected_profit / revenue * 100).quantize(TWO) if revenue else Decimal("0")
    planned_margin = budget.expected_margin_pct if budget else Decimal("0")

    # Largest contributors: categories whose actual outpaces its share of completion.
    contributors = []
    if budget:
        bva = {r["category"]: r for r in budget_vs_actual(project)["lines"]}
        for cat, row in bva.items():
            b = Decimal(row["budget"])
            a = Decimal(row["actual"])
            expected_so_far = (b * completion).quantize(TWO)
            if a > expected_so_far and a > 0:
                over = (a - expected_so_far).quantize(TWO)
                contributors.append({"category": cat, "over_run": str(over)})
    contributors.sort(key=lambda c: Decimal(c["over_run"]), reverse=True)

    delta = (projected_margin - planned_margin).quantize(TWO)
    verdict = "on track" if delta >= -2 else ("watch" if delta >= -8 else "at risk")
    narrative = (
        f"At {int(completion * 100)}% complete, {int(consumed * 100)}% of budget consumed. "
        f"Projected final cost R{projected_cost} vs budget R{total_budget.quantize(TWO)}; "
        f"projected margin {projected_margin}% vs planned {planned_margin}%."
    )
    if contributors:
        narrative += " Largest contributors: " + ", ".join(
            f"{c['category']} (+R{c['over_run']})" for c in contributors[:3]) + "."

    return {
        "completion_pct": int(completion * 100), "budget_consumed_pct": int(consumed * 100),
        "projected_final_cost": str(projected_cost), "projected_profit": str(projected_profit),
        "projected_margin_pct": str(projected_margin), "planned_margin_pct": str(planned_margin),
        "verdict": verdict, "contributors": contributors, "narrative": narrative,
    }


# ── Invoicing / progress claims / retention / payments (§7-8) ─────────────────

@transaction.atomic
def create_invoice(project, user, *, client_name=None, lines=None, retention_pct=0,
                   vat_rate=Decimal("15.00"), due_date=None, is_progress_claim=False,
                   percent_complete=0) -> Invoice:
    invoice = Invoice.objects.create(
        company=project.company, project=project, number=next_number(project.company, "invoice"),
        client_name=client_name or project.client_name, vat_rate=vat_rate,
        retention_pct=retention_pct, due_date=due_date, is_progress_claim=is_progress_claim,
        percent_complete=percent_complete, created_by=user, updated_by=user,
    )
    for pos, ln in enumerate(lines or [], start=1):
        invoice.lines.create(company=project.company, position=pos,
                             description=ln["description"], qty=ln.get("qty", 1),
                             unit_price=ln.get("unit_price", 0))
    return invoice


def create_progress_claim(project, user, *, percent_complete, retention_pct=10) -> Invoice:
    """A progress claim bills a % of the contract value (budget revenue), less the
    portion already claimed, with retention held back."""
    budget = getattr(project, "budget", None)
    contract = budget.revenue if budget else Decimal("0")
    pct = Decimal(str(percent_complete))
    already = sum(
        (i.percent_complete for i in project.invoices.filter(is_progress_claim=True)),
        Decimal("0"),
    )
    this_claim_pct = pct - already
    amount = (contract * this_claim_pct / 100).quantize(TWO)
    return create_invoice(
        project, user, retention_pct=retention_pct, is_progress_claim=True,
        percent_complete=pct,
        lines=[{"description": f"Progress claim to {pct}% complete", "qty": 1,
                "unit_price": amount}],
    )


def issue_invoice(invoice, user) -> Invoice:
    """Mark an invoice issued. Actual sending to the customer is a human action
    (locked human-approval boundary) — nothing here auto-sends."""
    invoice.status = InvoiceStatus.ISSUED
    invoice.issue_date = timezone.localdate()
    invoice.updated_by = user
    invoice.save(update_fields=["status", "issue_date", "updated_by", "updated_at"])
    record_audit(company=invoice.company, user=user, action="invoice.issued", entity=invoice,
                 after={"number": invoice.number, "total": str(invoice.total)})
    return invoice


def record_payment(invoice, user, *, amount, date=None, method="eft", reference="",
                   is_retention=False) -> Invoice:
    invoice.payments.create(company=invoice.company, date=date or timezone.localdate(),
                            amount=Decimal(amount), method=method, reference=reference,
                            is_retention=is_retention, created_by=user, updated_by=user)
    outstanding = invoice.outstanding
    if outstanding <= 0:
        invoice.status = InvoiceStatus.PAID
    elif invoice.paid > 0:
        invoice.status = InvoiceStatus.PARTIALLY_PAID
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


def outstanding_retention(project) -> Decimal:
    """Retention held and not yet released across the project's invoices."""
    total = Decimal("0")
    for inv in project.invoices.all():
        if not inv.retention_released:
            total += inv.retention_amount
    return total.quantize(TWO)


# ── Variations — commercial view, budget impact on approval (§6) ──────────────

@transaction.atomic
def approve_variation(variation, user) -> Variation:
    """Approve a variation (customer approval is external → human-approved). On
    approval the budget updates automatically: revenue + a category cost line."""
    variation.status = VariationStatus.APPROVED
    variation.updated_by = user
    variation.save(update_fields=["status", "updated_by", "updated_at"])

    budget = getattr(variation.project, "budget", None)
    if budget:
        budget.revenue = (budget.revenue + variation.revenue_impact).quantize(TWO)
        budget.save(update_fields=["revenue", "updated_at"])
        if variation.estimated_cost:
            BudgetLine.objects.create(
                company=variation.company, budget=budget, category=variation.category,
                amount=variation.estimated_cost,
            )
    publish("VariationApproved", company=variation.company, subject=variation, actor=user,
            payload={"number": variation.number, "revenue_impact": str(variation.revenue_impact)})
    return variation


# ── Commercial dashboard (§11) ────────────────────────────────────────────────

def commercial_dashboard(company) -> dict:
    """Portfolio commercial view: revenue, cost, margin, loss-making projects,
    outstanding invoices (aged), outstanding retention. Golden-Rule gated upstream."""
    from apps.projects.models import Project
    today = timezone.localdate()

    total_revenue = total_cost = Decimal("0")
    loss_making = []
    for project in Project.objects.all():
        p = profitability(project)
        rev, cost = Decimal(p["revenue"]), Decimal(p["actual_cost"])
        total_revenue += rev
        total_cost += cost
        if cost > rev and rev > 0:
            loss_making.append({"project": project.number,
                                "gross_profit": p["gross_profit"], "margin_pct": p["margin_pct"]})

    outstanding = overdue = Decimal("0")
    aging = {"current": Decimal("0"), "30": Decimal("0"), "60": Decimal("0"), "90+": Decimal("0")}
    retention = Decimal("0")
    for inv in Invoice.objects.exclude(status=InvoiceStatus.DRAFT):
        out = inv.outstanding
        outstanding += out
        if not inv.retention_released:
            retention += inv.retention_amount
        if inv.due_date and out > 0:
            days = (today - inv.due_date).days
            if days > 0:
                overdue += out
                bucket = "30" if days <= 30 else ("60" if days <= 60 else "90+")
                aging[bucket] += out
            else:
                aging["current"] += out
        elif out > 0:
            aging["current"] += out

    gp = (total_revenue - total_cost).quantize(TWO)
    margin = (gp / total_revenue * 100).quantize(TWO) if total_revenue else Decimal("0.00")
    return {
        "revenue": str(total_revenue.quantize(TWO)), "cost": str(total_cost.quantize(TWO)),
        "gross_profit": str(gp), "margin_pct": str(margin),
        "loss_making_projects": loss_making,
        "outstanding_invoiced": str(outstanding.quantize(TWO)),
        "overdue": str(overdue.quantize(TWO)),
        "aging": {k: str(v.quantize(TWO)) for k, v in aging.items()},
        "outstanding_retention": str(retention.quantize(TWO)),
    }
