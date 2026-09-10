"""AI Prediction Engine (AI OS §16) — foresight, grounded and honest.

Every prediction is derived from real ERP data and carries its confidence, the
reasoning behind it, the supporting numbers, and the source records. Nothing is
presented as fact: statements are phrased as likelihoods, and a predictor that
lacks data simply returns nothing rather than guessing.

Deterministic by design — these are transparent heuristics over the business
data, not a black box, so a user can always see *why*. An LLM can later phrase
them more naturally, but the signal stays grounded here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone


@dataclass
class Prediction:
    kind: str
    subject: str
    subject_id: str
    statement: str
    confidence: float                       # 0..1
    reasoning: list[str] = field(default_factory=list)
    supporting: dict = field(default_factory=dict)
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "subject": self.subject, "subject_id": self.subject_id,
            "statement": self.statement, "confidence": round(self.confidence, 2),
            "reasoning": self.reasoning, "supporting": self.supporting,
            "source": self.source,
        }


# ── individual predictors ─────────────────────────────────────────────────────
def predict_price_rises(company, *, limit=5, threshold=8.0) -> list[Prediction]:
    """Items whose supplier price is trending upward — budget/quote accordingly."""
    from apps.procurement.models import SupplierPrice
    from apps.procurement.services import price_intelligence

    keys = list(SupplierPrice.objects.values_list("item_key", "description")
                .distinct()[:60])
    out: list[Prediction] = []
    seen: set[str] = set()
    for key, desc in keys:
        if key in seen:
            continue
        seen.add(key)
        pi = price_intelligence(company, desc or key)
        if not pi.get("found") or pi.get("trend") != "up":
            continue
        pct = pi.get("change_pct") or 0
        if pct < threshold:
            continue
        conf = min(0.9, 0.4 + pct / 100 + 0.05 * min(pi["point_count"], 6))
        out.append(Prediction(
            kind="price_rise", subject=desc or key, subject_id="",
            statement=f"{desc or key} is likely to keep rising — up {pct}% across "
                      f"{pi['point_count']} recorded prices.",
            confidence=conf,
            reasoning=[f"Price moved from the earliest to latest average by {pct}%.",
                       f"Cheapest current supplier: {pi.get('cheapest_supplier')} "
                       f"at {pi.get('cheapest_price')}."],
            supporting={"change_pct": pct, "last_price": pi.get("last_price"),
                        "points": pi["point_count"]},
            source="Supplier price history"))
    out.sort(key=lambda p: p.confidence, reverse=True)
    return out[:limit]


def predict_repeat_customers(company, *, limit=5) -> list[Prediction]:
    """Customers with a regular quoting cadence who are now due for another."""
    from apps.customers.models import Customer
    from apps.quotes.models import Quotation

    today = timezone.localdate()
    out: list[Prediction] = []
    for cust in Customer.objects.all()[:200]:
        dates = sorted(q.created_at.date() for q in
                       Quotation.objects.filter(customer=cust).only("created_at"))
        if len(dates) < 3:
            continue
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        gaps = [g for g in gaps if g > 0]
        if len(gaps) < 2:
            continue
        avg_gap = sum(gaps) / len(gaps)
        since = (today - dates[-1]).days
        if avg_gap <= 0 or since < avg_gap or since > avg_gap * 3:
            continue                        # not yet due, or gone cold
        # Regularity: lower spread → higher confidence.
        spread = max(gaps) - min(gaps)
        regularity = max(0.0, 1 - spread / (avg_gap * 2 or 1))
        conf = min(0.85, 0.45 + 0.3 * regularity)
        out.append(Prediction(
            kind="repeat_customer", subject=cust.display_name, subject_id=str(cust.id),
            statement=f"{cust.display_name} is likely due for another quotation.",
            confidence=conf,
            reasoning=[f"They've requested {len(dates)} quotes, on average every "
                       f"~{int(avg_gap)} days.",
                       f"It's been {since} days since the last one."],
            supporting={"quotes": len(dates), "avg_gap_days": int(avg_gap),
                        "days_since_last": since},
            source=f"Quotations · {cust.display_name}"))
    out.sort(key=lambda p: p.confidence, reverse=True)
    return out[:limit]


def predict_job_delays(company, *, limit=5) -> list[Prediction]:
    """Active jobs with overdue tasks or a passed due date — likely to slip."""
    from apps.projects.models import Project

    today = timezone.localdate()
    out: list[Prediction] = []
    active = Project.objects.exclude(
        status__in=["completed", "closed", "cancelled", "archived"])
    for job in active[:200]:
        tasks = list(job.tasks.all()) if hasattr(job, "tasks") else []
        overdue = [t for t in tasks if getattr(t, "is_overdue", False)]
        past_due = bool(job.due_date and job.due_date < today)
        if not overdue and not past_due:
            continue
        open_n = sum(1 for t in tasks if getattr(t, "is_open", True))
        ratio = (len(overdue) / open_n) if open_n else (1.0 if past_due else 0)
        conf = min(0.9, 0.4 + 0.4 * ratio + (0.15 if past_due else 0))
        reasons = []
        if overdue:
            reasons.append(f"{len(overdue)} of {open_n or len(tasks)} open tasks are overdue.")
        if past_due:
            reasons.append(f"The job due date ({job.due_date}) has already passed.")
        out.append(Prediction(
            kind="job_delay", subject=getattr(job, "number", "") or job.title,
            subject_id=str(job.id),
            statement=f"{getattr(job, 'title', '') or job.number} is at risk of missing "
                      "its deadline.",
            confidence=conf, reasoning=reasons,
            supporting={"overdue_tasks": len(overdue), "open_tasks": open_n,
                        "past_due": past_due},
            source=f"Job · {getattr(job, 'number', '') or job.title}"))
    out.sort(key=lambda p: p.confidence, reverse=True)
    return out[:limit]


# ── aggregator ────────────────────────────────────────────────────────────────
_PREDICTORS = [
    ("price_rise", "procurement.manage", predict_price_rises),
    ("repeat_customer", "customers.manage", predict_repeat_customers),
    ("job_delay", "projects.view", predict_job_delays),
]


def predictions(company, user, *, limit=20) -> list[dict]:
    """Every prediction the user is permitted to see, most-confident first. Each
    predictor is gated on the same permission that guards its underlying data."""
    out: list[Prediction] = []
    for _kind, perm, fn in _PREDICTORS:
        try:
            if perm and not user.has_perm_code(perm):
                continue
            out.extend(fn(company))
        except Exception:                            # noqa: BLE001
            continue
    out.sort(key=lambda p: p.confidence, reverse=True)
    return [p.as_dict() for p in out[:limit]]
