"""Derived analytics — feature adoption, activation funnel, retention cohorts and
company health. Read-only aggregations over the event stream and (for funnels
that must be meaningful from day one) the real domain tables.

All queries are cross-tenant (platform-owner view): tenant models are read via
`all_objects`; the event table is a plain model. Nothing here writes.
"""
from datetime import timedelta

from django.utils import timezone


def _distinct_company_ids(model):
    """Company ids that have at least one row of `model` (cross-tenant)."""
    if model is None:
        return set()
    mgr = getattr(model, "all_objects", None) or model.objects
    return set(mgr.exclude(company__isnull=True)
               .values_list("company_id", flat=True).distinct())


def _get(app_label, name):
    from django.apps import apps
    try:
        return apps.get_model(app_label, name)
    except Exception:                                          # noqa: BLE001
        return None


# ── Feature adoption ─────────────────────────────────────────────────────────
#: The headline modules we report adoption for (event `module` values).
ADOPTION_MODULES = [
    ("quotations", "Quotations"), ("jobs", "Jobs"), ("crm", "CRM"),
    ("procurement", "Procurement"), ("invoices", "Invoices"),
    ("ai", "AI · LulaAI"), ("support", "Support"),
]


def feature_adoption(days=30):
    """% of active companies (any event in the window) that used each module."""
    from apps.analytics.models import AnalyticsEvent
    since = timezone.now() - timedelta(days=days)
    ev = AnalyticsEvent.objects.filter(created_at__gte=since).exclude(company__isnull=True)
    active = set(ev.values_list("company_id", flat=True).distinct())

    used_by_module = {}
    for cid, module in ev.exclude(module="").values_list("company_id", "module").distinct():
        used_by_module.setdefault(module, set()).add(cid)
    # Fold AI usage in from the metered log, whether or not an event was emitted.
    ail = _get("ai_platform", "AIUsageLog")
    if ail is not None:
        ai_ids = set(ail.objects.filter(created_at__gte=since)
                     .exclude(company__isnull=True).values_list("company_id", flat=True))
        used_by_module.setdefault("ai", set()).update(ai_ids)
        active |= ai_ids

    total = len(active) or 1
    rows = []
    for key, label in ADOPTION_MODULES:
        n = len(used_by_module.get(key, set()))
        rows.append({"module": label, "companies": n, "pct": round(n * 100 / total)})
    rows.sort(key=lambda r: -r["pct"])
    return {"active_companies": len(active), "rows": rows}


# ── Activation funnel (real domain data — meaningful from day one) ────────────
def activation_funnel():
    Company = _get("identity", "Company")
    total = Company.objects.count() if Company else 0

    with_customer = _distinct_company_ids(_get("customers", "Customer"))
    with_quote = _distinct_company_ids(_get("quotes", "Quotation"))
    with_job = _distinct_company_ids(_get("execution", "Task"))

    paid = set()
    Sub = _get("billing", "Subscription")
    if Sub is not None:
        paid = set(Sub.objects.filter(status="active")
                   .exclude(company__isnull=True).values_list("company_id", flat=True))

    steps = [
        {"label": "Signed up", "n": total},
        {"label": "Added a customer", "n": len(with_customer)},
        {"label": "Created a quotation", "n": len(with_quote)},
        {"label": "Created a job", "n": len(with_job)},
        {"label": "Paying subscription", "n": len(paid)},
    ]
    base = total or 1
    prev = None
    for s in steps:
        s["pct"] = round(s["n"] * 100 / base)
        s["drop"] = (round((prev - s["n"]) * 100 / prev) if prev else 0) if prev else 0
        prev = s["n"] or prev
    # Activated = added a customer AND created a quotation (the "aha" milestone).
    activated = len(with_customer & with_quote)
    return {"steps": steps, "total": total, "activated": activated,
            "activation_rate": round(activated * 100 / base)}


# ── Company health (real signals — immediately useful) ───────────────────────
def company_health(limit=100):
    Company = _get("identity", "Company")
    if Company is None:
        return []
    from django.db.models import Count, Max

    from apps.analytics.models import AnalyticsEvent
    now = timezone.now()
    d30 = now - timedelta(days=30)

    quote_ids = _count_by_company(_get("quotes", "Quotation"))
    job_ids = _count_by_company(_get("execution", "Task"))
    user_counts = _count_by_company(_get("identity", "Membership"))
    ev30 = dict(AnalyticsEvent.objects.filter(created_at__gte=d30)
                .exclude(company__isnull=True).values_list("company_id")
                .annotate(n=Count("id")).values_list("company_id", "n"))
    last_seen = dict(AnalyticsEvent.objects.exclude(company__isnull=True)
                     .values_list("company_id")
                     .annotate(m=Max("created_at")).values_list("company_id", "m"))

    rows = []
    for c in Company.objects.all()[:limit]:
        q, j = quote_ids.get(c.id, 0), job_ids.get(c.id, 0)
        e = ev30.get(c.id, 0)
        seen = last_seen.get(c.id)
        days_since = (now - seen).days if seen else None
        # Simple, documented score: breadth of use + recent activity.
        score = min(100, q * 4 + j * 4 + e)
        if days_since is None:
            status = "new" if e == 0 and q == 0 else "dormant"
        elif days_since <= 7:
            status = "healthy" if score >= 20 else "active"
        elif days_since <= 30:
            status = "at_risk"
        else:
            status = "dormant"
        rows.append({"company": c, "users": user_counts.get(c.id, 0), "quotes": q,
                     "jobs": j, "events_30d": e, "days_since": days_since,
                     "score": score, "status": status})
    rows.sort(key=lambda r: (r["days_since"] if r["days_since"] is not None else 9999))
    return rows


def _count_by_company(model):
    if model is None:
        return {}
    from django.db.models import Count
    mgr = getattr(model, "all_objects", None) or model.objects
    return dict(mgr.exclude(company__isnull=True).values_list("company_id")
                .annotate(n=Count("id")).values_list("company_id", "n"))


# ── Retention cohorts (by signup month → active months; grows with data) ─────
def retention_cohorts(months=6):
    from apps.analytics.models import AnalyticsEvent
    from apps.identity.models import Company

    now = timezone.now()
    month0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    starts = []
    s = month0
    for _ in range(months):
        starts.append(s)
        s = (s - timedelta(days=1)).replace(day=1)
    starts.reverse()

    # Companies active per month (any event that month).
    active_by_month = {}
    for cid, ts in (AnalyticsEvent.objects.exclude(company__isnull=True)
                    .values_list("company_id", "created_at")):
        key = ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        active_by_month.setdefault(key, set()).add(cid)

    cohorts = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else (now + timedelta(days=1))
        cohort_ids = set(Company.objects.filter(created_at__gte=start, created_at__lt=end)
                         .values_list("id", flat=True))
        if not cohort_ids:
            cohorts.append({"label": start.strftime("%b %Y"), "size": 0, "cells": []})
            continue
        cells = []
        for j in range(i, len(starts)):
            m = starts[j]
            active = len(cohort_ids & active_by_month.get(m, set()))
            cells.append({"n": active, "pct": round(active * 100 / len(cohort_ids))})
        cohorts.append({"label": start.strftime("%b %Y"), "size": len(cohort_ids), "cells": cells})
    return {"months": [s.strftime("%b") for s in starts], "cohorts": cohorts}
