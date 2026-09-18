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


# ── Unified purchase-price stream ────────────────────────────────────────────
# Both procurement price pages read the SAME purchase-price points: the live
# ledger (procurement.SupplierPrice) and the imported historical ledger
# (HistoricalLineItem purchases), merged and de-duplicated. That is why
# /procurement/prices/ (raw list) and /procurement/price-history/ (analytics)
# agree — one source, two views.
def purchase_points(*, query="", cap=5000) -> list[dict]:
    """Normalised purchase-price points from both ledgers, newest first, deduped
    on (item, price, date, supplier). Each: description, item_key, unit, price
    (Decimal), supplier, date (date|None), source (doc ref|None), origin."""
    from apps.procurement.models import SupplierPrice

    key = item_key(query) if query else ""
    out: list[dict] = []
    seen: set = set()

    def push(desc, unit, price, supplier, dt, source, origin):
        if price is None:
            return
        ik = item_key(desc)
        if key and key not in ik:
            return
        dedup = (ik, str(price), dt.isoformat() if dt else "", (supplier or "").lower())
        if dedup in seen:
            return
        seen.add(dedup)
        out.append({"description": desc, "item_key": ik, "unit": unit or "",
                    "price": price, "supplier": supplier or "", "date": dt,
                    "source": source, "origin": origin})

    # Live ledger first (so a confirmed import's live copy wins any dedup tie).
    sp = SupplierPrice.objects.select_related("supplier")
    if key:
        sp = sp.filter(item_key__icontains=key)
    for r in sp.order_by("-date")[:cap]:
        push(r.description, r.unit, r.unit_price,
             getattr(r.supplier, "name", "") or "", r.date, None, "live")
    # Imported historical ledger.
    li = (HistoricalLineItem.objects
          .filter(direction__in=_PURCHASE, unit_price__isnull=False)
          .select_related("document"))
    if key:
        li = li.filter(item_key__icontains=key)
    for r in li.order_by("-occurred_on")[:cap]:
        push(r.description, r.unit, r.unit_price, r.party_name or "",
             r.occurred_on, _doc_ref(r.document), "imported")

    out.sort(key=lambda p: (p["date"] or _MIN_DATE), reverse=True)
    return out


# ── Item price behaviour + forecast ──────────────────────────────────────────
def item_price_forecast(query, user, *, limit=24) -> dict:
    """How an item's PURCHASE price has behaved over time, and where it is likely
    to head next — over the UNIFIED purchase stream (live + imported), so it works
    off imported history even before a supplier is confirmed, and agrees with the
    raw ledger page.

    Deterministic and honest (AI OS §16/§19): the projection is a transparent
    average-step extrapolation shown only with >=3 dated prices; with fewer we
    report the behaviour and say a forecast needs more history. Purchase prices
    are procurement's own domain, so figures are shown."""
    key = item_key(query)
    if len(key) < 2:
        return {"found": False}
    pts = purchase_points(query=query)
    if not pts:
        return {"found": False}
    # Oldest → newest for trend/forecast.
    pts_asc = sorted(pts, key=lambda p: (p["date"] or _MIN_DATE))
    dated = [p for p in pts_asc if p["date"]]
    prices = [p["price"] for p in pts_asc]
    lo, hi = min(prices), max(prices)
    avg = sum(prices, Decimal("0")) / len(prices)
    seq = [p["price"] for p in dated] or prices
    last, first = seq[-1], seq[0]

    trend, change_pct = _trend_of(seq)
    forecast = None
    if len(dated) >= 3:
        steps = [seq[i] - seq[i - 1] for i in range(1, len(seq))]
        avg_step = sum(steps, Decimal("0")) / len(steps)
        projected = last + avg_step
        if projected < 0:
            projected = Decimal("0")
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

    # A ready-to-draw sparkline (SVG 300x60); dashed continuation = projection.
    spark = None
    if len(dated) >= 2:
        vseq = list(seq)
        if forecast:
            vseq = vseq + [Decimal(forecast["next_price"])]
        smin, smax = min(vseq), max(vseq)
        span = (smax - smin) or Decimal("1")
        W, H, pad = 300, 60, 6
        n = len(vseq)

        def _xy(i, val):
            x = pad + (W - 2 * pad) * (i / (n - 1 if n > 1 else 1))
            y = H - pad - (H - 2 * pad) * float((val - smin) / span)
            return f"{x:.1f},{y:.1f}"
        hist_pts = [_xy(i, vseq[i]) for i in range(len(dated))]
        spark = {
            "history": " ".join(hist_pts),
            "forecast_seg": (f"{hist_pts[-1]} {_xy(n - 1, vseq[-1])}" if forecast else ""),
            "w": W, "h": H,
        }

    return {
        "found": True, "query": query, "item_key": key,
        "point_count": len(prices),
        "min": str(lo), "max": str(hi), "average": str(avg.quantize(Decimal("0.01"))),
        "first": str(first), "last": str(last),
        "trend": trend,
        "change_pct": change_pct,
        "forecast": forecast,
        "spark": spark,
        "points": [{"date": p["date"].isoformat() if p["date"] else None,
                    "price": str(p["price"]), "supplier": p["supplier"],
                    "source": p["source"]} for p in pts[:limit]],
    }


# ── Price analytics (item explorer + overview) ───────────────────────────────
def _trend_of(seq):
    """(trend, change_pct) from a date-ordered price sequence."""
    if len(seq) < 2 or not seq[0]:
        return "flat", None
    pct = float((seq[-1] - seq[0]) / seq[0] * 100)
    return ("up" if pct > 2 else "down" if pct < -2 else "flat"), round(pct, 1)


def price_analytics(user, *, query="", limit=200) -> dict:
    """Analytics over the UNIFIED purchase stream (live + imported): a portfolio
    overview plus a per-item explorer (count, min/avg/last, trend, suppliers) —
    ranked, and filtered to a search term when given. Same source as the raw
    ledger page, so the two agree. Purchase prices are procurement's own domain."""
    key = item_key(query) if query else ""
    pts = purchase_points(query=query)
    if not pts:
        return {"found": False, "query": query,
                "overview": {"items": 0, "points": 0, "suppliers": 0}}

    groups: dict = {}
    suppliers: set = set()
    dates = []
    for p in pts:
        k = p["item_key"] or (p["description"] or "").lower()[:160]
        g = groups.setdefault(k, {"item_key": k, "description": p["description"],
                                  "prices": [], "suppliers": set(),
                                  "last_date": None, "unit": p["unit"]})
        g["prices"].append((p["date"], p["price"]))
        if p["supplier"]:
            g["suppliers"].add(p["supplier"].lower())
            suppliers.add(p["supplier"].lower())
        if p["date"]:
            dates.append(p["date"])
            if g["last_date"] is None or p["date"] > g["last_date"]:
                g["last_date"] = p["date"]

    def _item(g) -> dict:
        vals = [pr for _d, pr in g["prices"] if pr is not None]
        dated = [pr for d, pr in sorted(g["prices"], key=lambda x: (x[0] or _MIN_DATE))
                 if d and pr is not None]
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
            "items": len(items), "points": len(pts), "suppliers": len(suppliers),
            "date_from": (min(dates).isoformat() if dates else None),
            "date_to": (max(dates).isoformat() if dates else None),
            "spend": None,
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
