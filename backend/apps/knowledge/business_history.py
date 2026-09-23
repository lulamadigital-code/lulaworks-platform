"""Business History — the single, permission-aware facade over what already
exists (Business History §60/§68).

It COMPOSES, it does not duplicate. Canonical ERP records stay the source of
truth; this assembles one consistent view of a customer / supplier / job /
document from:

    • the transaction graph      (apps.web.relations.related_records)
    • the entity timeline        (customers.customer_timeline / execution.work_timeline)
    • the intelligence layer      (knowledge.intelligence — evidence-backed metrics)

Web, the API, mobile and LulaAI all call `history_for` so their view of the
business can never diverge. Money is gated once, here: a caller without
`finance.view_money` never receives amounts through the timeline or the graph —
the history is never a side channel (§5/§46).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── Identity & permissions ────────────────────────────────────────────────────

def _can_money(user) -> bool:
    return bool(user is not None
                and getattr(user, "is_authenticated", False)
                and user.has_perm_code("finance.view_money"))


def kind_of(obj) -> str:
    """The Business-History entity kind for a canonical record."""
    from apps.customers.models import Customer
    from apps.procurement.models import Supplier
    from apps.projects.models import Project
    from apps.quotes.models import (CommercialDocument, CustomerPurchaseOrder,
                                    Quotation)
    return {
        Customer: "customer", Supplier: "supplier", Project: "job",
        Quotation: "quotation", CustomerPurchaseOrder: "customer_po",
        CommercialDocument: "commercial_document",
    }.get(type(obj), type(obj).__name__.lower())


def resolve_subject(kind, pk):
    """Canonical record for a (kind, pk) — the graph's resolver, plus supplier."""
    from apps.procurement.models import Supplier
    from apps.web.relations import resolve_subject as _graph_resolve
    if kind == "supplier":
        return Supplier.objects.filter(pk=pk).first()
    return _graph_resolve(kind, pk)


def _entity_ref(obj, kind) -> dict:
    label = (getattr(obj, "display_name", "")
             or getattr(obj, "name", "")
             or getattr(obj, "number", "")
             or str(obj))
    return {"type": kind, "id": str(getattr(obj, "pk", "")), "label": str(label)}


# ── Summary (evidence-backed metrics from the intelligence layer) ─────────────

def _summary(obj, kind, user) -> dict:
    """Factual, provenance-carrying metrics where the intelligence layer covers
    the entity; an empty dict otherwise (never a fabricated number)."""
    try:
        from . import intelligence
        if kind == "customer":
            return intelligence.customer_intelligence(obj, user)
        if kind == "supplier":
            return intelligence.supplier_intelligence(obj, user)
    except Exception:                                # noqa: BLE001
        logger.exception("business_history summary failed for %s#%s",
                         kind, getattr(obj, "pk", None))
    return {}


# ── Timeline (normalised + money-gated) ───────────────────────────────────────

#: The one event shape every consumer receives, whatever the source.
def _event(when, kind, title, *, detail="", url="", amount=""):
    return {"when": when.isoformat() if hasattr(when, "isoformat") else when,
            "kind": kind, "title": title, "detail": detail, "url": url,
            "amount": amount}


def _timeline(obj, kind, user, *, limit) -> list:
    """A chronological, newest-first, clickable history — composed from the
    existing per-entity timelines and gated for money.

    Amounts (and money-bearing job detail) are dropped for a caller without
    `finance.view_money`, so the timeline can't leak billing the graph and
    summary already withhold."""
    can_money = _can_money(user)
    events: list = []
    try:
        if kind == "customer":
            from apps.customers.services import customer_timeline
            for r in customer_timeline(obj, limit=limit):
                events.append(_event(
                    r.get("when"), r.get("kind", ""), r.get("title", ""),
                    detail=r.get("detail", ""), url=r.get("url", ""),
                    amount=(r.get("amount", "") if can_money else "")))
        elif kind == "job":
            from apps.execution.models import FINANCIAL_REPORT_KINDS
            from apps.execution.work_execution import work_timeline
            fin = {str(k) for k in FINANCIAL_REPORT_KINDS}
            for r in work_timeline(obj):
                # work_timeline embeds money in the report `detail`; withhold that
                # line's detail from a non-finance caller rather than the event.
                detail = r.get("detail", "")
                if not can_money and (r.get("kind") in fin or r.get("amount")):
                    detail = ""
                events.append(_event(
                    r.get("when"), r.get("kind", ""),
                    r.get("label", "") or r.get("title", ""), detail=detail))
    except Exception:                                # noqa: BLE001 - never break the page
        logger.exception("business_history timeline failed for %s#%s",
                         kind, getattr(obj, "pk", None))
        return events
    # Newest-first, capped — one consistent order across every entity.
    events.sort(key=lambda e: e.get("when") or "", reverse=True)
    return events[:limit]


# ── The facade ────────────────────────────────────────────────────────────────

#: Payload fields that carry money — withheld from a caller without finance access.
_MONEY_EVENT_FIELDS = {"revenue_impact", "amount", "total", "value", "outstanding"}


def _humanize_event(ev, can_money) -> str:
    """A human line for one change/activity event (§19). Reads the event's own
    payload; falls back to spacing the CamelCase type name."""
    import re
    t, p = ev.type, (ev.payload or {})
    if t == "QuotationStatusChanged":
        return f"Status: {p.get('from', '?')} → {p.get('to', '?')}"
    if t == "QuotationUpdated":
        return "Quotation edited"
    if t == "VariationApproved":
        s = f"Variation {p.get('number', '')} approved".strip()
        if can_money and p.get("revenue_impact"):
            s += f" (R{p['revenue_impact']})"
        return s
    if t == "PaymentReceived":
        return f"Payment received on {p.get('invoice', '')}".strip()
    return re.sub(r"(?<!^)(?=[A-Z])", " ", t)      # "WorkCreated" -> "Work Created"


def change_history(obj, user, *, limit=50) -> list:
    """The change/activity history of ONE record, reconstructed from the DomainEvent
    backbone (status changes, edits, variations, payments…) — old→new where the
    event captured it, who and when (§19). Read-only; money-bearing payload fields
    are withheld without finance.view_money. Scoped to the record's own company
    (DomainEvent is not tenant-managed)."""
    from apps.core.models import DomainEvent

    company = getattr(obj, "company", None)
    can_money = _can_money(user)
    rows = (DomainEvent.objects
            .filter(company=company, subject_type=type(obj).__name__,
                    subject_id=obj.pk)
            .select_related("actor").order_by("-occurred_at")[:limit])
    out = []
    for ev in rows:
        payload = {k: v for k, v in (ev.payload or {}).items()
                   if can_money or k not in _MONEY_EVENT_FIELDS}
        actor = ""
        if ev.actor_id:
            actor = ev.actor.get_full_name() or ev.actor.email
        out.append({"when": ev.occurred_at.isoformat(), "type": ev.type,
                    "summary": _humanize_event(ev, can_money),
                    "actor": actor, "payload": payload})
    return out


def outcomes(obj, user) -> list:
    """Factual problems/outcomes on a JOB (§20) — never a subjective label like
    "bad job", only counted facts derived from canonical records: safety/incident
    reports, delays, being past its due date, and (with finance access) spend
    over budget. Empty for entities without an outcome signal. Read-only, tenant-
    scoped; authorised analytics can derive patterns from these facts elsewhere."""
    from apps.projects.models import Project, ProjectStatus
    if not isinstance(obj, Project):
        return []

    from django.db.models import Sum
    from django.utils import timezone

    from apps.core.context import tenant_scope
    from apps.execution.models import ReportKind, TaskReport, TaskResourceAllocation

    can_money = _can_money(user)
    found = []
    with tenant_scope(obj.company_id):
        reps = TaskReport.objects.filter(task__project=obj)
        incidents = reps.filter(kind=ReportKind.INCIDENT).count()
        if incidents:
            found.append({"type": "incident", "label": "Safety / incident reports",
                          "count": incidents})
        delays = reps.filter(kind=ReportKind.DELAY).count()
        if delays:
            found.append({"type": "delay", "label": "Delays / standing time",
                          "count": delays})
        today = timezone.localdate()
        if (obj.due_date and obj.status != ProjectStatus.COMPLETE
                and obj.due_date < today):
            found.append({"type": "overdue", "label": "Past due date, not complete",
                          "due_date": obj.due_date.isoformat(),
                          "days": (today - obj.due_date).days})
        if can_money and obj.budget_amount and obj.budget_amount > 0:
            spent = (TaskResourceAllocation.objects
                     .filter(task__project=obj, is_monetary=True)
                     .aggregate(s=Sum("amount_spent"))["s"] or 0)
            if spent > obj.budget_amount:
                found.append({"type": "over_budget", "label": "Spend exceeds budget",
                              "budget": str(obj.budget_amount), "spent": str(spent)})
    return found


def history_for(obj, user, *, kind=None, timeline_limit=60) -> dict:
    """One permission-aware Business-History view of a canonical record.

    Returns {entity, kind, permissions, summary, timeline, related} — the same
    shape for web, API, mobile and LulaAI. `summary` carries its own provenance
    (evidence document refs) from the intelligence layer; `related` and
    `timeline` are already money-gated for this user.
    """
    from apps.web.relations import related_records

    kind = kind or kind_of(obj)
    return {
        "entity": _entity_ref(obj, kind),
        "kind": kind,
        "permissions": {"money": _can_money(user)},
        "summary": _summary(obj, kind, user),
        "timeline": _timeline(obj, kind, user, limit=timeline_limit),
        "changes": change_history(obj, user),
        "outcomes": outcomes(obj, user),
        "related": related_records(obj, user),
    }


def history_for_kind(kind, pk, user, **kwargs) -> dict | None:
    """`history_for` addressed by (kind, pk) — for the API/mobile. None when the
    kind is unknown or the record isn't visible to this tenant."""
    obj = resolve_subject(kind, pk)
    if obj is None:
        return None
    return history_for(obj, user, kind=kind, **kwargs)


# ── Company-level history (the whole tenant's business, §56/§57) ──────────────

def _rate(n, d) -> dict:
    """A proportion that always carries its sample size (§52). None value when
    there's nothing to divide — never a fabricated 0% (§51)."""
    return {"value": round(100 * n / d, 1) if d else None, "won": n, "of": d}


#: DomainEvent subject_type (a model class name) → the web detail route for it, so
#: an event in the company feed is clickable. Records with no standalone page are
#: left un-linked (url "").
_SUBJECT_ROUTE = {
    "Quotation": "web:quotation_detail",
    "Project": "web:project_detail",
    "CommercialDocument": "web:commercial_document_detail",
    "CustomerPurchaseOrder": "web:customer_po_detail",
    "Customer": "web:customer_detail",
}


def company_timeline(company, user, *, limit=50) -> list:
    """The whole tenant's recent activity as ONE chronological feed, projected from
    the DomainEvent backbone (§57) — every quotation/job/payment/communication/
    change event, humanised, actor-attributed and clickable. Money-bearing payload
    fields are withheld without finance.view_money. Read-only; the canonical
    records stay the source of truth."""
    from django.urls import reverse

    from apps.core.context import tenant_scope
    from apps.core.models import DomainEvent

    can_money = _can_money(user)
    with tenant_scope(company.id):
        rows = list(DomainEvent.objects.filter(company=company)
                    .select_related("actor").order_by("-occurred_at")[:limit])
    out = []
    for ev in rows:
        url = ""
        route = _SUBJECT_ROUTE.get(ev.subject_type)
        if route and ev.subject_id:
            try:
                url = reverse(route, args=[ev.subject_id])
            except Exception:                            # noqa: BLE001
                url = ""
        payload = {k: v for k, v in (ev.payload or {}).items()
                   if can_money or k not in _MONEY_EVENT_FIELDS}
        actor = ""
        if ev.actor_id:
            actor = ev.actor.get_full_name() or ev.actor.email
        out.append({"when": ev.occurred_at.isoformat(), "type": ev.type,
                    "summary": _humanize_event(ev, can_money),
                    "subject_type": ev.subject_type, "subject_id": str(ev.subject_id or ""),
                    "url": url, "actor": actor, "payload": payload})
    return out


def company_history(company, user) -> dict:
    """Factual company-wide Business-History metrics, from canonical records.

    Every metric names its sample size; missing data is absent, not a false zero.
    Financial totals are withheld without `finance.view_money`. Tenant-scoped to
    `company` regardless of caller context (§47)."""
    from django.db.models import Sum

    from apps.core.context import tenant_scope
    from apps.customers.models import Customer
    from apps.projects.models import Project, ProjectStatus
    from apps.quotes.models import (CommercialDocument, CommercialDocumentPayment,
                                    Quotation, QuotationStatus)

    can_money = _can_money(user)
    won_statuses = [QuotationStatus.AWARDED, QuotationStatus.ACCEPTED]

    with tenant_scope(company.id):
        customers = Customer.objects.count()
        q_total = Quotation.objects.count()
        q_won = Quotation.objects.filter(status__in=won_statuses).count()
        jobs_total = Project.objects.count()
        jobs_complete = Project.objects.filter(status=ProjectStatus.COMPLETE).count()
        jobs_active = Project.objects.exclude(
            status__in=[ProjectStatus.COMPLETE, ProjectStatus.CANCELLED]).count()

        commercial = {}
        if can_money:
            invoices = CommercialDocument.objects.filter(
                kind=CommercialDocument.Kind.INVOICE).count()
            paid = CommercialDocumentPayment.objects.aggregate(s=Sum("amount"))["s"]
            commercial = {
                "invoice_count": invoices,
                "payments_received": str(paid) if paid is not None else None,
            }

    return {
        "customers": customers,
        "quotations": {"total": q_total, "won": q_won,
                       "conversion": _rate(q_won, q_total)},
        "jobs": {"total": jobs_total, "active": jobs_active, "complete": jobs_complete},
        "commercial": commercial,
        "money_visible": can_money,
        # The connected business as one recent activity feed (§57).
        "activity": company_timeline(company, user, limit=20),
    }
