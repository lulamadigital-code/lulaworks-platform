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
from django.db.models import Count, Q, Sum
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

        # Churn (cancelled tenants + cancellations this month) and storage used.
        ctx["subs_cancelled"] = subs.filter(status=SubscriptionStatus.CANCELLED).count()
        try:
            from apps.billing.models import BillingTransaction as _BT
            ctx["cancels_30d"] = _BT.objects.filter(
                kind=_BT.Kind.CANCELLATION, created_at__gte=d30).count()
        except Exception:
            ctx["cancels_30d"] = 0
        try:
            from apps.storage.models import StorageFile
            total_bytes = StorageFile.objects.aggregate(s=Sum("file_size"))["s"] or 0
        except Exception:
            total_bytes = 0
        gb = total_bytes / (1024 ** 3)
        ctx["storage_display"] = (f"{gb:.1f} GB" if gb >= 1
                                  else f"{total_bytes / (1024 ** 2):.0f} MB")

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

        # Subscriptions donut (active / trial / cancelled / none) — the
        # dashboard's signature visual, coloured by status.
        none_count = ctx["companies_total"] - subs.count()
        donut_items = [
            ("Active", ctx["subs_active"], "#00c875"),
            ("Trial", ctx["subs_trial"], "#fdab3d"),
            ("Cancelled", ctx["subs_cancelled"], "#e2445c"),
            ("No plan", max(none_count, 0), "#c4c7d0"),
        ]
        ctx["donut"] = _donut_segments(donut_items)

        # Tenants per plan (bar).
        from collections import Counter
        plan_counts = Counter()
        for s in subs.select_related("plan"):
            plan_counts[getattr(s.plan, "name", "—")] += 1
        plan_rows = sorted(({"name": n, "count": c} for n, c in plan_counts.items()),
                           key=lambda r: -r["count"])
        pk_peak = max((r["count"] for r in plan_rows), default=0) or 1
        for r in plan_rows:
            r["pct"] = int(round(r["count"] * 100 / pk_peak))
        ctx["plan_rows"] = plan_rows

    return render(request, "web/platform/console.html", ctx)


def _donut_segments(items):
    """SVG donut segments for (label, count, colour) tuples — dash/gap/rotate
    computed server-side, matching the operations dashboard."""
    from math import pi
    circ = 2 * pi * 54
    total = sum(c for _, c, _ in items) or 0
    segs, cum = [], 0.0
    for label, count, color in items:
        if not count:
            continue
        pct = count / total if total else 0
        segs.append({
            "label": label, "count": count, "color": color, "pct": round(pct * 100),
            "dash": round(pct * circ, 2), "gap": round((1 - pct) * circ, 2),
            "rotate": round(-90 + cum * 360, 2),
        })
        cum += pct
    return {"segments": segs, "total": total}


@login_required
def platform_tenant(request, pk):
    """Branded management page for one tenant — plan, credits, status — so the
    owner rarely needs Django admin. Superuser only."""
    if not request.user.is_superuser:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")

    from decimal import Decimal

    from apps.core.context import system_scope

    with system_scope():
        from apps.ai_platform.gateway import credit_balance, topup_credits
        from apps.billing import services as billing
        from apps.billing.models import BillingTransaction, Plan
        from apps.identity import services as identity
        from apps.identity.models import Company, Membership, Role

        company = Company.objects.filter(pk=pk).first()
        if company is None:
            messages.error(request, "Tenant not found.")
            return redirect("web:platform_home")

        if request.method == "POST":
            action = request.POST.get("action")
            try:
                if action == "invite_user":
                    role = Role.objects.filter(pk=request.POST.get("role")).first()
                    identity.invite_member(
                        company, request.user,
                        email=request.POST.get("email", ""), role=role,
                        first_name=request.POST.get("first_name", "").strip(),
                        last_name=request.POST.get("last_name", "").strip())
                    messages.success(request, "Invitation sent.")
                    return redirect("web:platform_tenant", pk=pk)
                if action == "member_status":
                    m = Membership.objects.filter(company=company,
                                                  pk=request.POST.get("membership")).first()
                    if m:
                        identity.set_member_status(
                            m, request.user, active=request.POST.get("active") == "1")
                        messages.success(request, "Member updated.")
                    return redirect("web:platform_tenant", pk=pk)
                if action == "grant_credits":
                    amt = Decimal(request.POST.get("amount") or "0")
                    if amt <= 0:
                        raise ValueError
                    bal = topup_credits(company, amt, source="platform_grant")
                    messages.success(request, f"Granted {amt:g} credits (balance {bal:g}).")
                elif action == "change_plan":
                    billing.change_plan(company, request.POST.get("plan_code", ""),
                                        actor=request.user)
                    messages.success(request, "Plan changed.")
                elif action == "toggle_active":
                    company.is_active = not company.is_active
                    company.save(update_fields=["is_active", "updated_at"])
                    messages.success(request, "Company "
                                     + ("activated." if company.is_active else "deactivated."))
                elif action == "cancel_subscription":
                    billing.cancel_subscription(company, actor=request.user)
                    messages.success(request, "Subscription set to cancel at period end.")
                else:
                    messages.error(request, "Unknown action.")
            except Exception as exc:                       # noqa: BLE001
                messages.error(request, f"Could not complete that: {exc}")
            return redirect("web:platform_tenant", pk=pk)

        sub = getattr(company, "subscription", None)
        ctx = {
            "company": company,
            "sub": sub,
            "plan": getattr(getattr(sub, "plan", None), "name", "—"),
            "status": sub.status if sub else "none",
            "credits": credit_balance(company),
            "users": Membership.objects.filter(company=company).count(),
            "plans": list(Plan.objects.filter(is_active=True).order_by("tier")),
            "history": list(BillingTransaction.objects.filter(company=company)
                            .order_by("-created_at")[:10]),
            "members": list(Membership.objects.filter(company=company)
                            .select_related("user", "role").order_by("user__email")),
            "roles": list(Role.objects.filter(Q(company=company) | Q(company=None))
                          .order_by("name")),
        }
    return render(request, "web/platform/tenant.html", ctx)


@login_required
def platform_create_tenant(request):
    """Onboard a new tenant from the console — creates the company on a trial
    and invites the owner by activation link (no password is ever set/emailed)."""
    if not request.user.is_superuser:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")
    if request.method != "POST":
        return redirect("web:platform_home")

    from apps.core.context import system_scope

    name = request.POST.get("company_name", "").strip()
    email = request.POST.get("owner_email", "").strip()
    full = request.POST.get("owner_name", "").strip()
    if not name or not email:
        messages.error(request, "A company name and an owner email are required.")
        return redirect("web:platform_home")

    with system_scope():
        from apps.billing.services import start_trial
        from apps.identity import services as identity
        from apps.identity.models import Company, Role, User

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "A user with that email already exists.")
            return redirect("web:platform_home")
        try:
            company = Company.objects.create(name=name)
            start_trial(company, actor=request.user)
            first, _, last = full.partition(" ")
            owner_role = Role.objects.filter(company=None, name="Company Owner").first()
            identity.invite_member(company, request.user, email=email, role=owner_role,
                                   first_name=first, last_name=last)
            messages.success(request, f"Created {name} on a trial and invited {email}.")
            return redirect("web:platform_tenant", pk=company.id)
        except Exception as exc:                           # noqa: BLE001
            messages.error(request, f"Could not create the tenant: {exc}")
            return redirect("web:platform_home")


@login_required
def platform_tenants_csv(request):
    """Download every tenant with plan, users, AI credits + 30-day usage."""
    import csv

    from django.http import HttpResponse

    if not request.user.is_superuser:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")

    from apps.core.context import system_scope

    now = timezone.now()
    d30 = now - timedelta(days=30)
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="lulaworks-tenants-{now:%Y%m%d}.csv"'
    w = csv.writer(resp)
    w.writerow(["Company", "Active", "Plan", "Status", "Users",
                "AI credits", "AI credits used (30d)", "Created"])

    with system_scope():
        from apps.ai_platform.gateway import credit_balance
        from apps.ai_platform.models import AIUsageLog
        from apps.billing.models import Subscription
        from apps.identity.models import Company, Membership

        user_counts = dict(Membership.objects.values_list("company").annotate(n=Count("id")))
        used = dict(AIUsageLog.objects.filter(created_at__gte=d30)
                    .values_list("company").annotate(s=Sum("credits_used")))
        subs = {s.company_id: s for s in Subscription.objects.select_related("plan")}
        for c in Company.objects.order_by("name"):
            sub = subs.get(c.id)
            w.writerow([
                c.name, "yes" if c.is_active else "no",
                getattr(getattr(sub, "plan", None), "name", ""),
                sub.status if sub else "none",
                user_counts.get(c.id, 0), credit_balance(c),
                used.get(c.id) or 0,
                c.created_at.strftime("%Y-%m-%d") if getattr(c, "created_at", None) else "",
            ])
    return resp
