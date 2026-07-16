"""Estimating services (ESTIMATING.md / Module 7).

Cost engines (deterministic-first, reusing the Procurement price ledger),
risk scoring, version control, a margin/discount approval evaluator, the
Golden-Rule-safe quotation generator, and the Pricing-Intelligence learning
loop (estimate → actuals → variance → calibration).
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Avg
from django.utils import timezone

from apps.administration.services import next_number, record_audit
from apps.core.events import publish
from apps.procurement.models import SupplierPrice
from apps.procurement.services import normalise
from apps.quotes.services import create_quotation

from .models import (
    CostCategory,
    Estimate,
    EstimateActual,
    EstimateSection,
    EstimateStatus,
    LineSource,
)

TWO = Decimal("0.01")


# ── Estimate creation ─────────────────────────────────────────────────────────

@transaction.atomic
def create_estimate(company, user, *, client_name, title="", work_type="", quotation=None,
                    markup_pct=Decimal("20"), sections=None) -> Estimate:
    """Create a draft estimate with optional pre-built sections/lines.

    `sections` = [{category, name?, lines: [{description, qty, unit, unit_cost,
    source?, confidence?, source_ref?, lead_time_days?}]}]
    """
    est = Estimate.objects.create(
        company=company, number=next_number(company, "estimate"),
        client_name=client_name, title=title, work_type=work_type,
        quotation=quotation, markup_pct=markup_pct,
        created_by=user, updated_by=user,
    )
    for pos, sec in enumerate(sections or [], start=1):
        section = EstimateSection.objects.create(
            company=company, estimate=est, category=sec["category"],
            name=sec.get("name", ""), position=pos,
        )
        for lpos, line in enumerate(sec.get("lines", []), start=1):
            section.lines.create(
                company=company, position=lpos, description=line["description"],
                qty=line.get("qty", 1), unit=line.get("unit", "each"),
                unit_cost=line.get("unit_cost", 0),
                source=line.get("source", LineSource.MANUAL),
                confidence=line.get("confidence", 0),
                source_ref=line.get("source_ref", ""),
                lead_time_days=line.get("lead_time_days"),
            )
    recompute_risk(est)
    publish("EstimateCreated", company=company, subject=est, actor=user,
            payload={"number": est.number, "client": client_name})
    return est


# ── Cost engines (propose lines; the estimator edits — never auto-approve) ─────

def propose_material_lines(company, items) -> list[dict]:
    """Material engine: price each requested item from the historical price
    ledger (latest supplier price, avg as fallback). Deterministic + free —
    runs before any AI. `items` = [{description, qty, unit?}]."""
    proposals = []
    for it in items:
        key = normalise(it["description"])
        latest = (
            SupplierPrice.objects.filter(item_key=key).order_by("-date").first()
        )
        avg = SupplierPrice.objects.filter(item_key=key).aggregate(a=Avg("unit_price"))["a"]
        if latest:
            proposals.append({
                "description": it["description"], "qty": it.get("qty", 1),
                "unit": it.get("unit", latest.unit), "unit_cost": latest.unit_price,
                "source": LineSource.LEDGER,
                "confidence": Decimal("0.90") if avg else Decimal("0.70"),
                "source_ref": f"ledger: {latest.supplier.name} @ {latest.date:%Y-%m-%d}",
            })
        else:
            proposals.append({
                "description": it["description"], "qty": it.get("qty", 1),
                "unit": it.get("unit", "each"), "unit_cost": Decimal("0.00"),
                "source": LineSource.MANUAL, "confidence": Decimal("0.00"),
                "source_ref": "no price history — enter manually",
            })
    return proposals


def propose_labour_line(company, *, description, hours, rate, work_type="") -> dict:
    """Labour engine: propose hours calibrated by historical variance for this
    work type (Pricing Intelligence §10), priced at the given rate."""
    factor, note = labour_calibration(company, work_type)
    adj_hours = (Decimal(hours) * factor).quantize(Decimal("0.001"))
    return {
        "description": description, "qty": adj_hours, "unit": "hour",
        "unit_cost": Decimal(rate), "source": LineSource.HISTORICAL,
        "confidence": Decimal("0.85") if note else Decimal("0.60"),
        "source_ref": note or f"base {hours}h @ R{rate}/hr",
    }


# ── Risk analysis (Module 7 §5) ───────────────────────────────────────────────

def recompute_risk(estimate) -> Decimal:
    """Score 0-100 from thin margin, low-confidence lines, and long lead times."""
    flags = []
    score = Decimal("0")

    if estimate.margin_pct < 15:
        score += 30
        flags.append(f"Thin margin ({estimate.margin_pct}%)")

    lines = [ln for sec in estimate.sections.all() for ln in sec.lines.all()]
    if lines:
        low_conf = [ln for ln in lines if ln.confidence and ln.confidence < Decimal("0.5")]
        if low_conf:
            score += min(len(low_conf) * 10, 30)
            flags.append(f"{len(low_conf)} low-confidence cost line(s)")
        long_lead = [ln for ln in lines if ln.lead_time_days and ln.lead_time_days > 30]
        if long_lead:
            score += min(len(long_lead) * 10, 20)
            flags.append(f"{len(long_lead)} long-lead item(s)")
        priced = [ln for ln in lines if ln.unit_cost == 0]
        if priced:
            score += 20
            flags.append(f"{len(priced)} unpriced line(s)")

    estimate.risk_score = min(score, Decimal("100")).quantize(TWO)
    estimate.risk_flags = flags
    estimate.save(update_fields=["risk_score", "risk_flags", "updated_at"])
    return estimate.risk_score


# ── Approval workflow (margin/discount thresholds; Module 7 §8) ────────────────

DEFAULT_APPROVAL = {
    "auto_approve_min_margin": 30,  # ≥ this and no deep discount → estimator only
    "min_margin_pct": 15,           # < this → needs estimating.approve
    "max_discount_pct": 10,         # > this → needs estimating.approve
}


def approval_required(estimate) -> dict:
    """Evaluate whether an estimate needs a higher approver, keyed on margin and
    discount (reads CompanySettings.approval_rules, else sensible defaults)."""
    from apps.administration.models import CompanySettings
    from apps.core.context import system_scope
    with system_scope():
        cs = CompanySettings.objects.filter(company=estimate.company).first()
    rules = {**DEFAULT_APPROVAL, **((cs.approval_rules or {}).get("estimate", {}) if cs else {})}

    reasons = []
    if estimate.margin_pct < Decimal(str(rules["min_margin_pct"])):
        reasons.append(f"margin {estimate.margin_pct}% below {rules['min_margin_pct']}%")
    if estimate.discount_pct > Decimal(str(rules["max_discount_pct"])):
        reasons.append(f"discount {estimate.discount_pct}% above {rules['max_discount_pct']}%")

    required = bool(reasons)
    return {"required": required, "perm": "estimating.approve" if required else None,
            "reasons": reasons}


def submit_for_approval(estimate, user) -> Estimate:
    gate = approval_required(estimate)
    estimate.status = (
        EstimateStatus.AWAITING_APPROVAL if gate["required"] else EstimateStatus.REVIEW
    )
    estimate.updated_by = user
    estimate.save(update_fields=["status", "updated_by", "updated_at"])
    return estimate


def approve_estimate(estimate, user) -> Estimate:
    """Approve. Only an APPROVED estimate can generate a quotation."""
    estimate.status = EstimateStatus.APPROVED
    estimate.approved_by = user
    estimate.approved_at = timezone.now()
    estimate.updated_by = user
    estimate.save(update_fields=[
        "status", "approved_by", "approved_at", "updated_by", "updated_at"])
    record_audit(company=estimate.company, user=user, action="estimate.approved",
                 entity=estimate, after={"number": estimate.number, "version": estimate.version})
    publish("EstimateApproved", company=estimate.company, subject=estimate, actor=user,
            payload={"number": estimate.number, "selling_price": str(estimate.selling_price)})
    return estimate


# ── Version control — revisions never overwrite (Module 7 §7) ──────────────────

@transaction.atomic
def create_revision(estimate, user, *, reason="") -> Estimate:
    """Deep-copy an estimate into a new version; mark the prior SUPERSEDED.
    History is permanent — nothing is overwritten."""
    root = estimate.parent or estimate
    latest_version = max(
        [root.version] + [r.version for r in root.revisions.all()] + [estimate.version]
    )
    new = Estimate.objects.create(
        company=estimate.company, number=estimate.number, parent=root,
        version=latest_version + 1, quotation=estimate.quotation,
        title=estimate.title, client_name=estimate.client_name, work_type=estimate.work_type,
        contingency_pct=estimate.contingency_pct, markup_pct=estimate.markup_pct,
        discount_pct=estimate.discount_pct, revision_reason=reason,
        created_by=user, updated_by=user,
    )
    for sec in estimate.sections.all():
        nsec = EstimateSection.objects.create(
            company=new.company, estimate=new, category=sec.category,
            name=sec.name, position=sec.position,
        )
        for ln in sec.lines.all():
            nsec.lines.create(
                company=new.company, position=ln.position, description=ln.description,
                qty=ln.qty, unit=ln.unit, unit_cost=ln.unit_cost, source=ln.source,
                confidence=ln.confidence, source_ref=ln.source_ref,
                lead_time_days=ln.lead_time_days,
            )
    estimate.status = EstimateStatus.SUPERSEDED
    estimate.save(update_fields=["status", "updated_at"])
    recompute_risk(new)
    return new


# ── Quotation generation — Golden Rule at the document boundary (Module 7 §1) ──

def generate_quotation(estimate, user) -> "object":
    """Derive an EXTERNAL quotation from an APPROVED estimate. The quotation
    carries the SELLING PRICE ONLY — never cost, markup or margin. This is the
    Financial Golden Rule applied at the document boundary.

    One customer-facing line per cost section, priced by distributing the
    estimate's selling price across sections in proportion to their cost.
    """
    if estimate.status != EstimateStatus.APPROVED:
        raise ValueError("Only an approved estimate can generate a quotation.")

    price = estimate.selling_price
    direct = estimate.direct_cost
    labels = dict(CostCategory.choices)
    lines = []
    for sec in estimate.sections.all():
        if sec.subtotal <= 0:
            continue
        share = (sec.subtotal / direct) if direct else Decimal("0")
        lines.append({
            "description": sec.name or labels.get(sec.category, sec.category),
            "qty": 1, "unit": "lot",
            "unit_price": (price * share).quantize(TWO),  # SELLING price share, no cost leaked
        })
    # Rounding remainder onto the last line so the quotation total == selling price.
    if lines:
        drift = price - sum(ln["unit_price"] for ln in lines)
        lines[-1]["unit_price"] = (lines[-1]["unit_price"] + drift).quantize(TWO)

    quote = create_quotation(
        estimate.company, user, client_name=estimate.client_name,
        title=estimate.title, lines=lines,
    )
    estimate.quotation = quote
    estimate.save(update_fields=["quotation", "updated_at"])
    return quote


# ── Pricing-Intelligence learning loop (Module 7 §10 — the moat) ───────────────

def capture_actuals(estimate, user, actuals) -> list[EstimateActual]:
    """Capture actual costs at execution/closeout, per category, and record the
    variance for future calibration. `actuals` = [{category, actual_cost,
    source?}]. Estimated cost per category is taken from the estimate itself."""
    est_by_cat = {}
    for sec in estimate.sections.all():
        est_by_cat[sec.category] = est_by_cat.get(sec.category, Decimal("0")) + sec.subtotal

    created = []
    for a in actuals:
        cat = a["category"]
        row = EstimateActual.objects.create(
            company=estimate.company, estimate=estimate, category=cat,
            estimated_cost=est_by_cat.get(cat, Decimal("0")).quantize(TWO),
            actual_cost=Decimal(a["actual_cost"]).quantize(TWO),
            source=a.get("source", ""),
        )
        created.append(row)
    publish("EstimateActualsCaptured", company=estimate.company, subject=estimate, actor=user,
            payload={"number": estimate.number, "rows": len(created)})
    return created


def labour_calibration(company, work_type) -> tuple[Decimal, str]:
    """Return (multiplier, note) for labour on this work type, learned from past
    estimate-vs-actual variance. >1 means we historically under-estimated."""
    if not work_type:
        return Decimal("1"), ""
    rows = EstimateActual.objects.filter(
        estimate__work_type=work_type, category=CostCategory.LABOUR, estimated_cost__gt=0
    )
    avg = rows.aggregate(a=Avg("actual_cost"))["a"]
    est = rows.aggregate(e=Avg("estimated_cost"))["e"]
    if not avg or not est:
        return Decimal("1"), ""
    factor = (avg / est).quantize(Decimal("0.001"))
    pct = ((factor - 1) * 100).quantize(TWO)
    direction = "exceeded" if pct >= 0 else "under-ran"
    note = (f"'{work_type}' labour historically {direction} estimate by "
            f"{abs(pct)}% (n={rows.count()}) — applied ×{factor}")
    return factor, note


def calibration_advice(company, work_type) -> list[str]:
    """Human-readable estimating advice from historical variances, per category."""
    advice = []
    for cat, label in CostCategory.choices:
        rows = EstimateActual.objects.filter(
            estimate__work_type=work_type, category=cat, estimated_cost__gt=0
        )
        n = rows.count()
        if n < 1:
            continue
        est = rows.aggregate(e=Avg("estimated_cost"))["e"]
        act = rows.aggregate(a=Avg("actual_cost"))["a"]
        if not est:
            continue
        pct = ((act - est) / est * 100).quantize(TWO)
        if abs(pct) >= 5:
            direction = "exceeded" if pct >= 0 else "came in under"
            advice.append(
                f"{label}: projects of this type historically {direction} estimate "
                f"by {abs(pct)}% (n={n}) — consider adjusting."
            )
    return advice
