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
from django.db.models import Sum
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
        from apps.ai_platform.models import AIUsageLog
        from apps.billing.models import Subscription, SubscriptionStatus
        from apps.identity.models import Company, User
        from apps.notifications.models import EmailLog, EmailStatus

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

        ctx["recent_companies"] = list(companies.order_by("-created_at")[:8])

    return render(request, "web/platform/console.html", ctx)
