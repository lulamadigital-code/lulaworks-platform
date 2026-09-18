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
from datetime import date
from decimal import Decimal

from .models import HistoricalJob, HistoricalLineItem

_MIN_DATE = date.min

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


# ── Item price behaviour + forecast ──────────────────────────────────────────
def item_price_forecast(query, user, *, limit=24) -> dict:
    """How an item's PURCHASE price has behaved over time, and where it is likely
    to head next — from the historical purchase ledger (HistoricalLineItem), so it
    works off imported history even before a supplier is confirmed into the live
    price ledger.

    Deterministic and honest (AI OS §16/§19): the projection is a transparent
    average-step extrapolation shown only with >=3 dated prices; with fewer we
    report the behaviour and say a forecast needs more history. Purchase prices
    are procurement's own domain, so figures are shown (the page already requires
    procurement.manage). Never invents a price."""
    key = item_key(query)
    if len(key) < 2:
        return {"found": False}
    rows = list(HistoricalLineItem.objects
                .filter(item_key__icontains=key, direction__in=_PURCHASE,
                        unit_price__isnull=False)
                .select_related("document").order_by("occurred_on", "created_at"))
    if not rows:
        return {"found": False}

    # Dated points power the trend/forecast; undated priced rows still count.
    dated = [r for r in rows if r.occurred_on and r.unit_price is not None]
    prices = [r.unit_price for r in rows if r.unit_price is not None]
    lo = min(prices)
    hi = max(prices)
    avg = sum(prices, Decimal("0")) / len(prices)
    last = dated[-1].unit_price if dated else prices[-1]
    first = dated[0].unit_price if dated else prices[0]

    trend = "flat"
    change_pct = None
    forecast = None
    if len(dated) >= 2 and first:
        change_pct = float((last - first) / first * 100)
        trend = "up" if change_pct > 2 else "down" if change_pct < -2 else "flat"
    if len(dated) >= 3:
        # Average step between consecutive dated prices → next likely price.
        steps = [dated[i].unit_price - dated[i - 1].unit_price
                 for i in range(1, len(dated))]
        avg_step = sum(steps, Decimal("0")) / len(steps)
        projected = last + avg_step
        if projected < 0:
            projected = Decimal("0")
        # Consistency of direction → a simple confidence.
        ups = sum(1 for s in steps if s > 0)
        downs = sum(1 for s in steps if s < 0)
        agree = max(ups, downs) / len(steps) if steps else 0
        forecast = {
            "next_price": str(projected.quantize(Decimal("0.01"))),
            "direction": trend,
            "confidence": round(0.4 + 0.5 * agree, 2),
            "basis": f"{len(dated)} dated prices, average change "
                     f"{avg_step.quantize(Decimal('0.01'))} per step",
        }

    # A ready-to-draw sparkline over the dated prices (SVG 300x60), plus the
    # projected next point as a dashed continuation — geometry only, no styling.
    spark = None
    if len(dated) >= 2:
        seq = [d.unit_price for d in dated]
        if forecast:
            seq = seq + [Decimal(forecast["next_price"])]
        smin, smax = min(seq), max(seq)
        span = (smax - smin) or Decimal("1")
        W, H, pad = 300, 60, 6
        n = len(seq)
        def _xy(i, val):
            x = pad + (W - 2 * pad) * (i / (n - 1 if n > 1 else 1))
            y = H - pad - (H - 2 * pad) * float((val - smin) / span)
            return f"{x:.1f},{y:.1f}"
        hist_pts = [_xy(i, seq[i]) for i in range(len(dated))]
        spark = {
            "history": " ".join(hist_pts),
            "forecast_seg": (f"{hist_pts[-1]} {_xy(n - 1, seq[-1])}" if forecast else ""),
            "w": W, "h": H,
        }

    return {
        "found": True, "query": query, "item_key": key,
        "point_count": len(prices),
        "min": str(lo), "max": str(hi), "average": str(avg.quantize(Decimal("0.01"))),
        "first": str(first), "last": str(last),
        "trend": trend,
        "change_pct": (round(change_pct, 1) if change_pct is not None else None),
        "forecast": forecast,
        "spark": spark,
        "points": [{"date": r.occurred_on.isoformat() if r.occurred_on else None,
                    "price": str(r.unit_price),
                    "supplier": r.party_name or "",
                    "source": _doc_ref(r.document)} for r in rows[-limit:]],
    }


# ── Price analytics (item explorer + overview) ───────────────────────────────
def _trend_of(seq):
    """(trend, change_pct) from a date-ordered price sequence."""
    if len(seq) < 2 or not seq[0]:
        return "flat", None
    pct = float((seq[-1] - seq[0]) / seq[0] * 100)
    return ("up" if pct > 2 else "down" if pct < -2 else "flat"), round(pct, 1)


def price_analytics(user, *, query="", limit=200) -> dict:
    """Analytics over the historical PURCHASE ledger: a portfolio overview plus a
    per-item explorer (count, min/avg/last, trend, suppliers) — ranked, and
    filtered to a search term when given. Purchase prices are procurement's own
    domain (the page requires procurement.manage), so figures are shown.

    One pass over the ledger; deterministic and evidence-free of invention."""
    key = item_key(query) if query else ""
    qs = (HistoricalLineItem.objects
          .filter(direction__in=_PURCHASE, unit_price__isnull=False)
          .order_by("occurred_on", "created_at")
          .values("item_key", "description", "party_name", "unit",
                  "unit_price", "line_total", "occurred_on"))
    rows = list(qs)
    if not rows:
        return {"found": False, "query": query,
                "overview": {"items": 0, "points": 0, "suppliers": 0}}

    groups: dict = {}
    suppliers: set = set()
    dates = []
    spend = Decimal("0")
    for r in rows:
        k = r["item_key"] or (r["description"] or "").lower()[:160]
        g = groups.setdefault(k, {"item_key": k, "description": r["description"],
                                  "prices": [], "suppliers": set(),
                                  "last_date": None, "unit": r["unit"] or ""})
        g["prices"].append((r["occurred_on"], r["unit_price"]))
        if r["party_name"]:
            g["suppliers"].add(r["party_name"])
            suppliers.add(r["party_name"])
        if r["occurred_on"]:
            dates.append(r["occurred_on"])
            if g["last_date"] is None or r["occurred_on"] > g["last_date"]:
                g["last_date"] = r["occurred_on"]
        if r["line_total"] is not None:
            spend += r["line_total"]

    def _item(g) -> dict:
        vals = [p for _d, p in g["prices"] if p is not None]
        dated = [p for d, p in sorted(g["prices"], key=lambda x: (x[0] or _MIN_DATE))
                 if d and p is not None]
        seq = dated or vals
        trend, pct = _trend_of(seq)
        avg = sum(vals, Decimal("0")) / len(vals)
        return {
            "item_key": g["item_key"], "description": g["description"], "unit": g["unit"],
            "count": len(vals), "min": str(min(vals)), "max": str(max(vals)),
            "average": str(avg.quantize(Decimal("0.01"))),
            "last": str(seq[-1]) if seq else None,
            "last_date": g["last_date"].isoformat() if g["last_date"] else None,
            "suppliers": len(g["suppliers"]), "trend": trend, "change_pct": pct,
        }

    items = [_item(g) for g in groups.values()]
    # Explorer: search filter + rank (most price points first, then most recent).
    shown = items
    if key:
        shown = [it for it in items if key in (it["item_key"] or "")]
    shown.sort(key=lambda it: (it["count"], it["last_date"] or ""), reverse=True)

    def _movers(direction):
        pool = [it for it in items if it["count"] >= 3 and it["change_pct"] is not None
                and it["trend"] == direction]
        pool.sort(key=lambda it: abs(it["change_pct"]), reverse=True)
        return pool[:5]

    return {
        "found": True, "query": query, "item_key": key,
        "overview": {
            "items": len(items), "points": len(rows), "suppliers": len(suppliers),
            "date_from": (min(dates).isoformat() if dates else None),
            "date_to": (max(dates).isoformat() if dates else None),
            "spend": (str(spend.quantize(Decimal("0.01"))) if spend else None),
        },
        "movers_up": _movers("up"),
        "movers_down": _movers("down"),
        "top_volume": sorted(items, key=lambda it: it["count"], reverse=True)[:6],
        "items": shown[:limit],
        "match_count": len(shown),
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
