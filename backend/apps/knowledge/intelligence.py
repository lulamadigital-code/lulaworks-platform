"""Historical Intelligence retrieval — the ONE place the imported archive
(HistoricalJob + HistoricalLineItem, alongside the live procurement price ledger)
is turned into contextual insight for the record a user is looking at. Both the
module Intelligence panels and LulaAI call this, so there is a single, shared
implementation (LULAWORKS AI OS §14, §21).

Guarantees:
* READ-ONLY and evidence-backed — every insight can name its source document.
* PERMISSION-SCOPED — revenue figures (job values, what we charged customers)
  are withheld without finance.view_money; supplier purchase prices are the
  procurement pages' own domain (those pages already require procurement.manage).
* TENANT-SCOPED — models resolve through their tenant managers; the caller must
  hold tenant context.
* NO INVENTION — no history means an empty result, never a guess.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .models import HistoricalJob, HistoricalLineItem

_DISMISSED = HistoricalJob.Status.DISMISSED
_PURCHASE = (HistoricalLineItem.Direction.PURCHASE,
             HistoricalLineItem.Direction.SUPPLIER_QUOTE)
_SALE = (HistoricalLineItem.Direction.SALE, HistoricalLineItem.Direction.QUOTE)


def can_money(user) -> bool:
    try:
        return bool(user.has_perm_code("finance.view_money"))
    except Exception:  # noqa: BLE001
        return False


def item_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()[:160]


def _doc_ref(doc):
    """Provenance for an insight: the source document, openable by the user."""
    if doc is None:
        return None
    return {"id": str(doc.id), "filename": doc.filename,
            "doc_type": doc.get_doc_type_display(),
            "date": doc.document_date.isoformat() if doc.document_date else None}


def _job_dict(job, money):
    docs = [_doc_ref(d) for d in job.documents.all()[:5]]
    return {
        "id": str(job.id), "title": job.title, "work_type": job.work_type,
        "value": (str(job.value) if (money and job.value is not None) else None),
        "currency": job.currency,
        "occurred_on": job.occurred_on.isoformat() if job.occurred_on else None,
        "status": job.status,
        "sources": [d for d in docs if d],
    }


def _line_dict(li, money):
    show = money or li.direction in _PURCHASE  # purchase prices are procurement's own
    return {
        "description": li.description, "item_key": li.item_key,
        "direction": li.direction,
        "unit": li.unit,
        "unit_price": (str(li.unit_price) if (show and li.unit_price is not None) else None),
        "currency": li.currency,
        "party": li.party_name,
        "occurred_on": li.occurred_on.isoformat() if li.occurred_on else None,
        "source": _doc_ref(li.document),
    }


# ── Customer ─────────────────────────────────────────────────────────────────
def customer_intelligence(customer, user) -> dict:
    """Historical relationship for a customer: previous jobs (count, last, value),
    similar work, and what we historically quoted/charged them — from the imported
    archive, evidence-backed."""
    money = can_money(user)
    jobs = list(HistoricalJob.objects.filter(customer_id=str(customer.pk))
                .exclude(status=_DISMISSED)
                .prefetch_related("documents")
                .order_by("-occurred_on", "-created_at"))
    charged = list(HistoricalLineItem.objects
                   .filter(party_kind=HistoricalLineItem.Party.CUSTOMER,
                           party_id=str(customer.pk), direction__in=_SALE)
                   .select_related("document").order_by("-occurred_on")[:8])
    if not jobs and not charged:
        return {"found": False}
    last = jobs[0] if jobs else None
    total = None
    if money and jobs:
        vals = [j.value for j in jobs if j.value is not None]
        total = str(sum(vals, Decimal("0"))) if vals else None
    similar = (sum(1 for j in jobs[1:] if last and j.work_type and j.work_type == last.work_type)
               if last else 0)
    return {
        "found": True,
        "job_count": len(jobs),
        "similar_count": similar,
        "last_job": _job_dict(last, money) if last else None,
        "total_value": total,
        "money_visible": money,
        "jobs": [_job_dict(j, money) for j in jobs[:6]],
        "charged_items": [_line_dict(li, money) for li in charged],
    }


# ── Supplier ─────────────────────────────────────────────────────────────────
def supplier_intelligence(supplier, user) -> dict:
    """Historical purchases from a supplier: items bought, last price per item,
    last purchase, volume — evidence-backed."""
    lines = list(HistoricalLineItem.objects
                 .filter(party_kind=HistoricalLineItem.Party.SUPPLIER,
                         party_id=str(supplier.pk), direction__in=_PURCHASE)
                 .select_related("document").order_by("-occurred_on"))
    if not lines:
        return {"found": False}
    by_item = {}
    for li in lines:
        by_item.setdefault(li.item_key or li.description.lower(), li)  # first = latest
    return {
        "found": True,
        "purchase_count": len(lines),
        "item_count": len(by_item),
        "last_purchase": lines[0].occurred_on.isoformat() if lines[0].occurred_on else None,
        "items": [_line_dict(li, True) for li in list(by_item.values())[:12]],
    }


# ── Item / price ─────────────────────────────────────────────────────────────
def item_price_intelligence(query, user, *, limit=8) -> dict:
    """Historical prices for an item, kept STRICTLY separate: what we PAID
    suppliers vs what we CHARGED/QUOTED customers (AI OS §10). Evidence-backed."""
    money = can_money(user)
    key = item_key(query)
    if len(key) < 2:
        return {"found": False}
    base = (HistoricalLineItem.objects.filter(item_key__icontains=key)
            .select_related("document").order_by("-occurred_on"))
    purchases = [li for li in base if li.direction in _PURCHASE][:limit]
    sales = [li for li in base if li.direction in _SALE][:limit]
    if not purchases and not sales:
        return {"found": False}

    def _avg(rows):
        vals = [li.unit_price for li in rows if li.unit_price is not None]
        return str(sum(vals, Decimal("0")) / len(vals)) if vals else None

    return {
        "found": True, "query": query, "item_key": key, "money_visible": money,
        "purchases": {
            "count": len(purchases),
            "last": _line_dict(purchases[0], money) if purchases else None,
            "average": _avg(purchases),          # purchase prices always shown
            "items": [_line_dict(li, money) for li in purchases],
        },
        "sales": {
            "count": len(sales),
            "last": _line_dict(sales[0], money) if sales else None,
            "average": (_avg(sales) if money else None),
            "items": [_line_dict(li, money) for li in sales],
        },
    }


# ── Similar jobs ─────────────────────────────────────────────────────────────
def similar_jobs(user, *, work_type="", keywords="", customer_id="",
                 exclude_job_id=None, limit=5) -> list:
    """Historical jobs resembling the one in hand — by work type, keyword, and/or
    the same customer — for Job/Quotation Intelligence. Evidence-backed."""
    money = can_money(user)
    qs = HistoricalJob.objects.exclude(status=_DISMISSED).prefetch_related("documents")
    from django.db.models import Q
    cond = Q()
    if work_type:
        cond |= Q(work_type__iexact=work_type)
    for kw in [w for w in re.split(r"\W+", keywords or "") if len(w) > 3][:4]:
        cond |= Q(title__icontains=kw) | Q(work_type__icontains=kw)
    if customer_id:
        cond |= Q(customer_id=str(customer_id))
    if cond:
        qs = qs.filter(cond)
    if exclude_job_id:
        qs = qs.exclude(pk=exclude_job_id)
    return [_job_dict(j, money) for j in qs.order_by("-occurred_on", "-created_at")[:limit]]
