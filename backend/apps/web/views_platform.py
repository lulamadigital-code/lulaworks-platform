"""Platform Console — the SaaS owner's dashboard.

A superuser-only view of the whole platform (all tenants) in the app's own
design system: KPI tiles, a signups chart, and section cards that link into the
Django admin for the raw CRUD. This is the pretty front door; the admin stays
the tool behind it.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import redirect, render
from django.utils import timezone


@login_required
def platform_home(request):
    if not request.user.is_superuser:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")

    from apps.core.context import system_scope

    now = timezone.now()
    d30 = now - timedelta(days=30)
    ctx = {}

    with system_scope():
        from apps.ai_platform.gateway import credit_balance, topup_credits
        from apps.ai_platform.models import AIUsageLog
        from apps.billing.models import Subscription, SubscriptionStatus
        from apps.identity.models import Company, Membership, User
        from apps.notifications.models import EmailLog, EmailStatus

        # ── Owner action: grant AI credits to a tenant ──────────────────────
        if request.method == "POST" and request.POST.get("action") == "grant_credits":
            try:
                target = Company.objects.get(pk=request.POST.get("company"))
                amount = Decimal(request.POST.get("amount") or "0")
                if amount <= 0:
                    raise ValueError
                new_balance = topup_credits(target, amount, source="platform_grant")
                messages.success(
                    request, f"Granted {amount:g} AI credits to {target.name} "
                    f"(new balance {new_balance:g}).")
            except (Company.DoesNotExist, ValueError, Exception):
                messages.error(request, "Enter a valid company and a positive credit amount.")
            return redirect("web:platform_home")

        companies = Company.objects.all()
        ctx["companies_total"] = companies.count()
        ctx["companies_active"] = companies.filter(is_active=True).count()
        ctx["users_total"] = User.objects.count()
        ctx["users_active"] = User.objects.filter(is_active=True).count()

        subs = Subscription.objects.select_related("plan")
        active_subs = list(subs.filter(status=SubscriptionStatus.ACTIVE))
        ctx["subs_active"] = len(active_subs)
        ctx["subs_trial"] = subs.filter(status=SubscriptionStatus.TRIAL).count()
        mrr = Decimal("0")
        for s in active_subs:
            mrr += getattr(s.plan, "price", None) or Decimal("0")
        ctx["mrr"] = mrr

        ctx["ai_calls_30d"] = AIUsageLog.objects.filter(created_at__gte=d30).count()
        ctx["ai_credits_30d"] = (
            AIUsageLog.objects.filter(created_at__gte=d30)
            .aggregate(s=Sum("credits_used"))["s"] or Decimal("0"))
        ctx["emails_30d"] = EmailLog.objects.filter(
            created_at__gte=d30, status=EmailStatus.SENT).count()

        # New companies per month, last 6 months → a simple bar chart.
        buckets = []
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        starts = []
        s = month_start
        for _ in range(6):
            starts.append(s)
            # step back one month
            s = (s - timedelta(days=1)).replace(day=1)
        starts.reverse()
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else (now + timedelta(days=1))
            n = companies.filter(created_at__gte=start, created_at__lt=end).count()
            buckets.append({"label": start.strftime("%b"), "n": n})
        peak = max((b["n"] for b in buckets), default=0) or 1
        for b in buckets:
            b["pct"] = int(round(b["n"] * 100 / peak))
        ctx["signups"] = buckets

        # Revenue per month (actual billed amounts) → trend chart.
        from apps.billing.models import BillingTransaction
        rev = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else (now + timedelta(days=1))
            amt = (BillingTransaction.objects.filter(created_at__gte=start, created_at__lt=end)
                   .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            rev.append({"label": start.strftime("%b"), "amt": amt})
        peak_rev = max((r["amt"] for r in rev), default=Decimal("0")) or Decimal("1")
        for r in rev:
            r["pct"] = int(round(r["amt"] * 100 / peak_rev))
        ctx["revenue"] = rev
        ctx["revenue_total"] = sum((r["amt"] for r in rev), Decimal("0"))

        # ── Per-tenant table: plan, users, AI credits + 30-day usage ────────
        user_counts = dict(
            Membership.objects.values_list("company").annotate(n=Count("id")))
        used_by_company = dict(
            AIUsageLog.objects.filter(created_at__gte=d30)
            .values_list("company").annotate(s=Sum("credits_used")))
        subs_by_company = {s.company_id: s for s in subs}
        tenants = []
        for c in companies.order_by("name")[:100]:
            sub = subs_by_company.get(c.id)
            tenants.append({
                "company": c,
                "plan": getattr(getattr(sub, "plan", None), "name", "—"),
                "status": sub.status if sub else "none",
                "users": user_counts.get(c.id, 0),
                "credits": credit_balance(c),
                "used_30d": used_by_company.get(c.id) or Decimal("0"),
            })
        ctx["tenants"] = tenants

        # AI usage by tenant (top consumers, last 30 days).
        name_by_id = {c.id: c.name for c in companies}
        ai_rows = sorted(
            ({"name": name_by_id.get(cid, "—"), "used": used}
             for cid, used in used_by_company.items() if used),
            key=lambda r: -r["used"])[:8]
        peak_ai = max((r["used"] for r in ai_rows), default=Decimal("0")) or Decimal("1")
        for r in ai_rows:
            r["pct"] = int(round(r["used"] * 100 / peak_ai))
        ctx["ai_by_tenant"] = ai_rows

    return render(request, "web/platform/console.html", ctx)
