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


def _can_manage(user):
    """Owners and admins may run platform actions (grant credits, create tenants,
    change plans). Support is read-only."""
    return getattr(user, "platform_level", None) in ("owner", "admin")


def _is_owner(user):
    """Only owners manage the platform team and touch Django-superuser powers."""
    return getattr(user, "platform_level", None) == "owner"


@login_required
def platform_home(request):
    if not request.user.platform_level:
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
            if not request.user.can_platform("billing"):
                messages.error(request, "You don't have billing access.")
                return redirect("web:platform_home")
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
    if not request.user.platform_level:
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
            _cap_for = {
                "invite_user": "tenants", "member_status": "tenants",
                "toggle_active": "tenants", "grant_credits": "billing",
                "change_plan": "billing", "cancel_subscription": "billing"}
            need = _cap_for.get(action)
            if need and not request.user.can_platform(need):
                messages.error(request, "You don't have access for that action.")
                return redirect("web:platform_tenant", pk=pk)
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
    if not request.user.platform_level:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")
    if not request.user.can_platform("tenants"):
        messages.error(request, "You don't have tenant-management access.")
        return redirect("web:platform_home")
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
            # Apply platform defaults (best-effort — never block onboarding).
            from apps.administration.models import PlatformSettings
            cfg = PlatformSettings.load()
            if cfg.default_currency and hasattr(company, "currency"):
                try:
                    company.currency = cfg.default_currency
                    company.save(update_fields=["currency"])
                except Exception:                              # noqa: BLE001
                    pass
            start_trial(company, actor=request.user)
            try:
                if cfg.default_plan:
                    from apps.billing import services as billing
                    billing.change_plan(company, cfg.default_plan.code, actor=request.user)
                if cfg.starting_ai_credits and cfg.starting_ai_credits > 0:
                    from apps.ai_platform.gateway import topup_credits
                    topup_credits(company, cfg.starting_ai_credits, source="platform_default")
            except Exception:                                  # noqa: BLE001
                pass
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
def platform_list(request, section):
    """Console-native, read-only list pages for the platform records (users,
    plans, subscriptions, email logs, audit logs) — same app shell as the rest
    of the console, so every sidebar link lands on a consistent page. Full CRUD
    stays in Django admin, one click away. Superuser only."""
    if not request.user.platform_level:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")

    from apps.core.context import system_scope

    def _dt(v, fmt="%d %b %Y"):
        return v.strftime(fmt) if v else "—"

    ctx = {"active": section}
    with system_scope():
        if section == "users":
            from apps.identity.models import User
            ctx.update(
                title="Users", subtitle="Every account across all tenants.",
                columns=["Email", "Name", "Access", "Active", "Joined"],
                admin_url="/admin/identity/user/",
                rows=[[
                    u.email, u.get_full_name() or "—",
                    "Superuser" if u.is_superuser else ("Staff" if u.is_staff else "Member"),
                    "Yes" if u.is_active else "No", _dt(getattr(u, "date_joined", None)),
                ] for u in User.objects.order_by("email")[:500]])

        elif section == "plans":
            from apps.billing.models import Plan
            ctx.update(
                title="Plans", subtitle="Pricing and AI-credit allowances.",
                columns=["Name", "Tier", "Price / mo", "Annual", "AI credits / mo", "Active"],
                admin_url="/admin/billing/plan/",
                rows=[[
                    p.name, p.tier, f"R{p.price:.0f}", f"R{p.annual_price:.0f}",
                    f"{p.monthly_ai_credits:.0f}", "Yes" if p.is_active else "No",
                ] for p in Plan.objects.order_by("tier", "price")])

        elif section == "subscriptions":
            from apps.billing.models import Subscription
            ctx.update(
                title="Subscriptions", subtitle="Which tenant is on which plan.",
                columns=["Company", "Plan", "Status", "Period start", "Period end"],
                admin_url="/admin/billing/subscription/",
                rows=[[
                    getattr(s.company, "name", "—"), getattr(s.plan, "name", "—"),
                    s.get_status_display() if hasattr(s, "get_status_display") else s.status,
                    _dt(s.current_period_start), _dt(s.current_period_end),
                ] for s in Subscription.objects.select_related("company", "plan")
                    .order_by("company__name")[:500]])

        elif section == "emails":
            from apps.notifications.models import EmailLog
            ctx.update(
                title="Email logs", subtitle="Delivery history across the platform.",
                columns=["When", "To", "Subject", "Category", "Status"],
                admin_url="/admin/notifications/emaillog/",
                rows=[[
                    _dt(e.created_at, "%d %b %Y %H:%M"), e.to_email, e.subject,
                    e.get_category_display() if hasattr(e, "get_category_display") else getattr(e, "category", ""),
                    e.get_status_display() if hasattr(e, "get_status_display") else e.status,
                ] for e in EmailLog.objects.select_related("company").order_by("-created_at")[:300]])

        elif section == "audit":
            from apps.administration.models import AuditLog
            ctx.update(
                title="Audit logs", subtitle="Security and change trail.",
                columns=["When", "Actor", "Action", "Entity", "IP"],
                admin_url="/admin/administration/auditlog/",
                rows=[[
                    _dt(a.created_at, "%d %b %Y %H:%M"), getattr(a.user, "email", "—") or "—",
                    a.action, a.entity_type or "—", a.ip_address or "—",
                ] for a in AuditLog.objects.select_related("user").order_by("-created_at")[:300]])
        else:
            messages.error(request, "Unknown section.")
            return redirect("web:platform_home")

    return render(request, "web/platform/list.html", ctx)


@login_required
def platform_settings(request):
    """Platform Settings — environment/integration status (relevant info) plus
    the platform TEAM: add LulaWorks staff with an access level, change roles,
    or revoke access. Any platform staff can view; only owners manage the team."""
    if not request.user.platform_level:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")

    from django.conf import settings as dj

    from apps.administration.models import PlatformSettings
    from apps.core.context import system_scope
    from apps.identity import services as identity
    from apps.identity.models import User

    can_team = request.user.can_platform("team")
    can_settings = request.user.can_platform("settings")
    is_owner = _is_owner(request.user)

    if request.method == "POST":
        action = request.POST.get("action")
        team_actions = {"invite_staff", "set_role", "revoke"}
        config_actions = {"save_branding", "save_defaults", "save_billing",
                          "save_security", "send_test_email"}
        if action in team_actions and not can_team:
            messages.error(request, "You don't have team-management access.")
            return redirect("web:platform_settings")
        if action in config_actions and not can_settings:
            messages.error(request, "You don't have settings access.")
            return redirect("web:platform_settings")
        try:
            with system_scope():
                if action == "save_branding":
                    _save_platform_branding(request)
                    messages.success(request, "Branding & support details saved.")
                elif action == "save_defaults":
                    _save_platform_defaults(request)
                    messages.success(request, "New-tenant defaults saved.")
                elif action == "save_billing":
                    _save_platform_billing(request)
                    messages.success(request, "Billing & tax defaults saved.")
                elif action == "save_security":
                    _save_platform_security(request)
                    messages.success(request, "Security policy saved.")
                elif action == "send_test_email":
                    _send_test_email(request)
                elif action == "invite_staff":
                    identity.invite_platform_staff(
                        request.user,
                        email=request.POST.get("email", ""),
                        role=request.POST.get("role", ""),
                        first_name=request.POST.get("first_name", "").strip(),
                        last_name=request.POST.get("last_name", "").strip())
                    messages.success(request, "Invitation sent — they'll set their own password.")
                elif action == "set_role":
                    u = User.objects.filter(pk=request.POST.get("user")).first()
                    if u and u.id != request.user.id:
                        identity.set_platform_role(u, request.POST.get("role", ""))
                        messages.success(request, "Access level updated.")
                    else:
                        messages.error(request, "You can't change your own access here.")
                elif action == "revoke":
                    u = User.objects.filter(pk=request.POST.get("user")).first()
                    if u and u.id != request.user.id:
                        identity.revoke_platform_staff(u)
                        messages.success(request, "Platform access revoked.")
                    else:
                        messages.error(request, "You can't revoke your own access.")
                else:
                    messages.error(request, "Unknown action.")
        except (identity.PlatformStaffError, ValueError) as exc:
            messages.error(request, str(exc))
        except Exception as exc:                               # noqa: BLE001
            messages.error(request, f"Could not complete that: {exc}")
        return redirect("web:platform_settings")

    cfg = PlatformSettings.load()

    # ── Read-only status: integrations + system/health ──────────────────────
    anymail = getattr(dj, "ANYMAIL", {}) or {}
    email_backend = getattr(dj, "EMAIL_BACKEND", "").lower()
    integrations = [
        {"name": "AI · LulaAI", "ok": bool(getattr(dj, "GEMINI_API_KEY", "")
                                           or getattr(dj, "ANTHROPIC_API_KEY", "")),
         "detail": (getattr(dj, "AI_PROVIDER", "") or "—").title()},
        {"name": "Email · Brevo", "ok": bool(anymail.get("BREVO_API_KEY")),
         "detail": "HTTP API" if "anymail" in email_backend
                   else ("Console (dev)" if "console" in email_backend else "SMTP")},
        {"name": "SMS · Twilio", "ok": bool(getattr(dj, "TWILIO_AUTH_TOKEN", "")),
         "detail": "Task alerts"},
        {"name": "Payments", "ok": bool(getattr(dj, "PAYFAST_MERCHANT_ID", "")
                                        or getattr(dj, "STRIPE_SECRET_KEY", "")),
         "detail": "Checkout"},
        {"name": "Storage", "ok": bool(getattr(dj, "AWS_STORAGE_BUCKET_NAME", "")
                                       or not dj.DEBUG),
         "detail": "S3 / Spaces" if getattr(dj, "AWS_STORAGE_BUCKET_NAME", "") else "Local"},
    ]
    system_rows = [
        ("Environment", "Production" if not dj.DEBUG else "Development"),
        ("Site URL", getattr(dj, "SITE_URL", "") or "—"),
        ("From address", getattr(dj, "DEFAULT_FROM_EMAIL", "") or "—"),
        ("Time zone", getattr(dj, "TIME_ZONE", "UTC")),
    ]

    # AI status (read-only — provider/model/keys are environment-controlled).
    _now = timezone.now()
    with system_scope():
        from apps.ai_platform.models import AIUsageLog
        recent = AIUsageLog.objects.filter(created_at__gte=_now - timedelta(days=30))
        ai_calls_30d = recent.count()
        by_prov = list(recent.values("provider").annotate(
            calls=Count("id"), tin=Sum("tokens_in"), tout=Sum("tokens_out"),
            spend=Sum("cost"), credits=Sum("credits_used"),
            errors=Count("id", filter=Q(status="error"))).order_by("-calls"))
        # Which tenants drive AI spend (30d, top 8).
        ai_tenant_spend = list(recent.values("company__name").annotate(
            spend=Sum("cost"), calls=Count("id"), credits=Sum("credits_used"))
            .order_by("-spend")[:8])
        # Monthly AI spend trend (last 6 months).
        month0 = _now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        starts, s = [], month0
        for _ in range(6):
            starts.append(s)
            s = (s - timedelta(days=1)).replace(day=1)
        starts.reverse()
        ai_trend = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else (_now + timedelta(days=1))
            amt = (AIUsageLog.objects.filter(created_at__gte=start, created_at__lt=end)
                   .aggregate(s=Sum("cost"))["s"] or 0)
            ai_trend.append({"label": start.strftime("%b"), "amt": amt})
    _peak_t = max((r["spend"] or 0 for r in ai_tenant_spend), default=0) or 1
    for r in ai_tenant_spend:
        r["pct"] = int(round((r["spend"] or 0) * 100 / _peak_t))
    _peak_m = max((r["amt"] for r in ai_trend), default=0) or 1
    for r in ai_trend:
        r["pct"] = int(round(r["amt"] * 100 / _peak_m))
    ai_status = [
        ("Provider", (getattr(dj, "AI_PROVIDER", "") or "—").title()),
        ("Model", getattr(dj, "GEMINI_MODEL", "") or getattr(dj, "ANTHROPIC_MODEL", "") or "default"),
        ("API key", "Configured" if (getattr(dj, "GEMINI_API_KEY", "")
                                     or getattr(dj, "ANTHROPIC_API_KEY", "")) else "Not set"),
        ("Calls · 30d", ai_calls_30d),
    ]
    # Per-provider usage & estimated spend (30d) from OUR logs — vendors don't
    # expose a live balance; the billing link goes to where the real one lives.
    _p_label = {"claude": "Claude · Anthropic", "openai": "ChatGPT · OpenAI",
                "gemini": "Gemini · Google"}
    _p_billing = {"claude": "https://console.anthropic.com/settings/billing",
                  "openai": "https://platform.openai.com/settings/organization/billing",
                  "gemini": "https://console.cloud.google.com/billing"}
    ai_providers = [{
        "key": r["provider"],
        "label": _p_label.get(r["provider"], (r["provider"] or "—").title()),
        "calls": r["calls"], "tokens": (r["tin"] or 0) + (r["tout"] or 0),
        "spend": r["spend"] or 0, "credits": r["credits"] or 0, "errors": r["errors"],
        "billing": _p_billing.get(r["provider"], ""),
    } for r in by_prov]

    # Security posture — the live auth configuration (read-only) plus the one
    # tunable control (minimum password length).
    session_age = getattr(dj, "SESSION_COOKIE_AGE", 0) or 0
    security_posture = [
        ("HTTPS enforced", "Yes" if getattr(dj, "SECURE_SSL_REDIRECT", False) else "No"),
        ("Secure cookies", "Yes" if getattr(dj, "SESSION_COOKIE_SECURE", False) else "No"),
        ("Session lifetime", f"{session_age // 3600} h" if session_age else "Browser session"),
        ("Password rules", f"{len(getattr(dj, 'AUTH_PASSWORD_VALIDATORS', []))} active"),
    ]

    with system_scope():
        from apps.billing.models import Plan
        team = list(User.objects.filter(Q(platform_role__in=["owner", "admin", "support"])
                                        | Q(is_superuser=True)).order_by("email"))
        counts = {"companies": _safe_count("identity", "Company"),
                  "users": User.objects.count()}
        plans = list(Plan.objects.filter(is_active=True).order_by("tier", "price"))

    role_labels = dict(User.PlatformRole.choices)
    team_rows = [{
        "user": u,
        "level": u.platform_level,
        "role_label": ("Platform Owner" if u.platform_level == "owner"
                       else role_labels.get(u.platform_role, "—")),
        "is_me": u.id == request.user.id,
        "pending": not u.has_usable_password(),
    } for u in team]

    # Legend: what each department can do (plain-language capability summary).
    _cap_words = {"tenants": "Tenants", "billing": "Billing", "ai": "AI",
                  "team": "Team", "settings": "Settings", "support": "Support (read)"}
    role_caps = []
    for value, label in User.PlatformRole.choices:
        caps = User.PLATFORM_CAPS.get(value, set())
        words = [w for k, w in _cap_words.items() if k in caps]
        role_caps.append({"value": value, "label": label,
                          "summary": ", ".join(words) if words else "Console only"})

    return render(request, "web/platform/settings.html", {
        "active": "settings", "team": team_rows,
        "roles": User.PlatformRole.choices, "role_caps": role_caps,
        "is_owner": is_owner, "can_team": can_team, "can_settings": can_settings,
        "counts": counts, "cfg": cfg, "plans": plans,
        "integrations": integrations, "system": system_rows,
        "ai_status": ai_status, "ai_providers": ai_providers,
        "ai_tenant_spend": ai_tenant_spend, "ai_trend": ai_trend,
        "security_posture": security_posture,
        "test_email_to": request.user.email,
    })


def _save_platform_branding(request):
    """Validate and persist branding & support details from the Settings form."""
    from django.core.exceptions import ValidationError
    from django.core.validators import EmailValidator, URLValidator

    from apps.administration.models import PlatformSettings

    ev, uv = EmailValidator(), URLValidator()

    def _email(name, label):
        v = (request.POST.get(name) or "").strip()
        if v:
            try:
                ev(v)
            except ValidationError:
                raise ValueError(f"{label} is not a valid email address.")
        return v

    def _url(name, label):
        v = (request.POST.get(name) or "").strip()
        if v:
            try:
                uv(v)
            except ValidationError:
                raise ValueError(f"{label} is not a valid URL (include https://).")
        return v

    s = PlatformSettings.load()
    s.platform_name = (request.POST.get("platform_name") or "").strip() or "LulaWorks"
    s.support_email = _email("support_email", "Support email")
    s.sales_email = _email("sales_email", "Sales email")
    s.billing_email = _email("billing_email", "Billing email")
    s.reply_to = _email("reply_to", "Reply-to")
    s.terms_url = _url("terms_url", "Terms URL")
    s.privacy_url = _url("privacy_url", "Privacy URL")
    s.updated_by = request.user
    s.save()


def _save_platform_defaults(request):
    """Validate and persist the defaults new tenants inherit."""
    from decimal import Decimal, InvalidOperation

    from apps.administration.models import PlatformSettings
    from apps.billing.models import Plan

    s = PlatformSettings.load()
    plan_id = (request.POST.get("default_plan") or "").strip()
    s.default_plan = Plan.objects.filter(pk=plan_id).first() if plan_id else None
    try:
        s.trial_days = max(0, int(request.POST.get("trial_days") or 0))
    except (TypeError, ValueError):
        raise ValueError("Trial length must be a whole number of days.")
    try:
        s.starting_ai_credits = max(Decimal("0"), Decimal(request.POST.get("starting_ai_credits") or "0"))
    except (InvalidOperation, ValueError):
        raise ValueError("Starting AI credits must be a number.")
    s.auto_welcome = request.POST.get("auto_welcome") == "on"
    s.updated_by = request.user
    s.save()


def _save_platform_billing(request):
    """Validate and persist billing & tax defaults for new tenants."""
    from decimal import Decimal, InvalidOperation

    from apps.administration.models import PlatformSettings

    s = PlatformSettings.load()
    cur = (request.POST.get("default_currency") or "ZAR").strip().upper()[:3]
    s.default_currency = cur or "ZAR"
    try:
        vat = Decimal(request.POST.get("default_vat_rate") or "0")
        if vat < 0 or vat > 100:
            raise ValueError
        s.default_vat_rate = vat
    except (InvalidOperation, ValueError):
        raise ValueError("VAT rate must be a percentage between 0 and 100.")
    s.invoice_prefix = (request.POST.get("invoice_prefix") or "").strip().upper()[:12]
    s.updated_by = request.user
    s.save()


def _save_platform_security(request):
    """Persist the security policy (currently the platform-wide minimum password
    length, enforced by the dynamic validator)."""
    from apps.administration.models import PlatformSettings

    s = PlatformSettings.load()
    try:
        n = int(request.POST.get("password_min_length") or 0)
        if n < 6 or n > 128:
            raise ValueError
        s.password_min_length = n
    except (TypeError, ValueError):
        raise ValueError("Minimum password length must be between 6 and 128.")
    s.updated_by = request.user
    s.save()


def _send_test_email(request):
    """Send a branded test email to verify delivery from the Console."""
    from django.core.exceptions import ValidationError
    from django.core.validators import EmailValidator

    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email

    to = (request.POST.get("test_email_to") or request.user.email or "").strip()
    try:
        EmailValidator()(to)
    except ValidationError:
        messages.error(request, "Enter a valid email address to test.")
        return
    try:
        send_email(
            to=to, subject="LulaWorks — test email", template="generic", company=None,
            sent_by=request.user, category=EmailCategory.ACCOUNT,
            context={"heading": "It works ✓",
                     "body": "This is a test email sent from the LulaWorks Platform "
                             "Console. If you received it, outbound email is working."})
        messages.success(request, f"Test email sent to {to}. Check the inbox (and Email logs).")
    except Exception as exc:                                   # noqa: BLE001
        messages.error(request, f"Test email failed: {exc}")


def _safe_count(app_label, model_name):
    try:
        from django.apps import apps as _apps
        return _apps.get_model(app_label, model_name).objects.count()
    except Exception:
        return 0


@login_required
def platform_support(request):
    """LulaWorks Support desk — every ticket across all tenants, with a triage
    board. Any platform staff (all departments carry the 'support' capability)
    can view and work tickets."""
    if not request.user.can_platform("support"):
        messages.error(request, "The support desk is for platform staff only.")
        return redirect("web:dashboard")

    from apps.support.models import OPEN_STATUSES, SupportTicket, TicketStatus

    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")
    q = request.GET.get("q", "").strip()

    qs = SupportTicket.all_objects.select_related("company", "created_by", "assigned_agent")
    if status:
        qs = qs.filter(status=status)
    if priority:
        qs = qs.filter(priority=priority)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(subject__icontains=q))
    tickets = list(qs[:200])

    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    allt = SupportTicket.all_objects
    kpis = {
        "open": allt.filter(status__in=OPEN_STATUSES).count(),
        "high": allt.filter(status__in=OPEN_STATUSES,
                            priority__in=["high", "urgent"]).count(),
        "waiting": allt.filter(status=TicketStatus.WAITING_CUSTOMER).count(),
        "resolved_today": allt.filter(resolved_at__gte=today).count(),
    }
    return render(request, "web/platform/support.html", {
        "active": "support", "tickets": tickets, "kpis": kpis,
        "statuses": TicketStatus.choices, "f_status": status, "f_priority": priority, "q": q,
    })


@login_required
def platform_support_detail(request, pk):
    if not request.user.can_platform("support"):
        messages.error(request, "The support desk is for platform staff only.")
        return redirect("web:dashboard")

    from apps.identity.models import User
    from apps.support import services as support
    from apps.support.models import (
        SupportTicket, TicketPriority, TicketStatus,
    )

    ticket = (SupportTicket.all_objects
              .select_related("company", "created_by", "assigned_agent").filter(pk=pk).first())
    if ticket is None:
        messages.error(request, "Ticket not found.")
        return redirect("web:platform_support")

    ip = request.META.get("REMOTE_ADDR")
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "reply":
                support.add_message(ticket=ticket, sender=request.user,
                                    body=request.POST.get("body", ""), from_support=True,
                                    files=request.FILES.getlist("attachments"), ip=ip)
                messages.success(request, "Reply sent to the customer.")
            elif action == "note":
                support.add_message(ticket=ticket, sender=request.user,
                                    body=request.POST.get("body", ""), is_internal=True,
                                    from_support=True, ip=ip)
                messages.success(request, "Internal note added.")
            elif action == "assign":
                agent = User.objects.filter(pk=request.POST.get("agent")).first() \
                    if request.POST.get("agent") else request.user
                support.assign(ticket=ticket, agent=agent, actor=request.user, ip=ip)
                messages.success(request, "Ticket assigned.")
            elif action == "status":
                support.set_status(ticket=ticket, actor=request.user,
                                   status=request.POST.get("status", ""),
                                   from_support=True, ip=ip)
                messages.success(request, "Status updated.")
            elif action == "priority":
                support.set_priority(ticket=ticket, priority=request.POST.get("priority", ""),
                                    actor=request.user, ip=ip)
                messages.success(request, "Priority updated.")
            elif action == "escalate":
                support.set_priority(ticket=ticket, priority=TicketPriority.URGENT,
                                    actor=request.user, ip=ip)
                support.set_status(ticket=ticket, actor=request.user,
                                   status=TicketStatus.IN_PROGRESS, from_support=True, ip=ip)
                messages.success(request, "Ticket escalated.")
        except support.SupportError as exc:
            messages.error(request, str(exc))
        except Exception as exc:                               # noqa: BLE001
            messages.error(request, f"Could not complete that: {exc}")
        return redirect("web:platform_support_detail", pk=pk)

    from apps.core.context import system_scope
    with system_scope():
        agents = list(User.objects.filter(Q(platform_role__in=["owner", "admin", "support"])
                                          | Q(is_superuser=True)).order_by("email"))
    with system_scope():
        thread = list(ticket.messages.select_related("sender").prefetch_related("attachments"))
    from django.urls import reverse
    return render(request, "web/platform/support_detail.html", {
        "active": "support", "ticket": ticket, "thread": thread, "agents": agents,
        "statuses": TicketStatus.choices, "priorities": TicketPriority.choices,
        "poll_url": reverse("web:platform_support_messages", args=[ticket.id]),
        "send_url": reverse("web:platform_support_send", args=[ticket.id]),
        "is_support": True,
    })


def _platform_ticket(pk):
    from apps.support.models import SupportTicket
    return (SupportTicket.all_objects.select_related("company", "created_by")
            .filter(pk=pk).first())


@login_required
def platform_support_messages(request, pk):
    """Live-chat poll for a technician — the full stream (public + internal notes)."""
    from django.http import JsonResponse
    from apps.core.context import tenant_scope
    from apps.support import services as support
    from apps.support.models import TicketStatus
    if not request.user.can_platform("support"):
        return JsonResponse({"error": "forbidden"}, status=403)
    ticket = _platform_ticket(pk)
    if ticket is None:
        return JsonResponse({"error": "not found"}, status=404)
    # A technician isn't scoped to the ticket's company — read within it explicitly.
    with tenant_scope(ticket.company_id):
        msgs = list(ticket.messages.select_related("sender")
                    .prefetch_related("attachments").order_by("created_at"))
        data = [support.message_dict(m) for m in msgs]
    return JsonResponse({
        "messages": data,
        "status": ticket.get_status_display(),
        "closed": ticket.status == TicketStatus.CLOSED,
    })


@login_required
def platform_support_send(request, pk):
    """Live-chat send for a technician — a public reply, or an internal note when
    `internal=1`."""
    from django.http import JsonResponse
    from apps.core.context import tenant_scope
    from apps.support import services as support
    if not request.user.can_platform("support"):
        return JsonResponse({"error": "forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    ticket = _platform_ticket(pk)
    if ticket is None:
        return JsonResponse({"error": "not found"}, status=404)
    internal = request.POST.get("internal") in ("1", "true", "on")
    with tenant_scope(ticket.company_id):
        try:
            msg = support.add_message(ticket=ticket, sender=request.user,
                                      body=request.POST.get("body", ""), from_support=True,
                                      is_internal=internal,
                                      files=request.FILES.getlist("attachments"),
                                      ip=request.META.get("REMOTE_ADDR"))
        except support.SupportError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        data = support.message_dict(msg)
    return JsonResponse({"message": data})


@login_required
def platform_analytics(request):
    """Analytics overview — product & website event stream at a glance. Read for
    any platform staff."""
    if not request.user.platform_level:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")

    from apps.analytics.models import AnalyticsEvent

    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d7, d30 = now - timedelta(days=7), now - timedelta(days=30)
    qs = AnalyticsEvent.objects

    def _dau(since):
        return qs.filter(created_at__gte=since, user__isnull=False).values("user").distinct().count()

    dau, wau, mau = _dau(today), _dau(d7), _dau(d30)
    kpis = {
        "events_today": qs.filter(created_at__gte=today).count(),
        "events_7d": qs.filter(created_at__gte=d7).count(),
        "events_30d": qs.filter(created_at__gte=d30).count(),
        "dau": dau, "wau": wau, "mau": mau,
        "stickiness": round(dau * 100 / mau) if mau else 0,
        "active_companies": qs.filter(created_at__gte=d30, company__isnull=False)
                              .values("company").distinct().count(),
    }
    top_events = list(qs.filter(created_at__gte=d30).values("event_name")
                      .annotate(n=Count("id")).order_by("-n")[:10])
    peak = max((r["n"] for r in top_events), default=0) or 1
    for r in top_events:
        r["pct"] = int(round(r["n"] * 100 / peak))
    top_modules = list(qs.filter(created_at__gte=d30).exclude(module="")
                       .values("module").annotate(n=Count("id")).order_by("-n")[:8])
    top_sources = list(qs.filter(created_at__gte=d30).exclude(source="")
                       .values("source").annotate(n=Count("id")).order_by("-n")[:6])
    recent = list(qs.select_related("user", "company").order_by("-created_at")[:20])

    from apps.analytics import reports
    adoption = reports.feature_adoption()
    funnel = reports.activation_funnel()
    return render(request, "web/platform/analytics.html", {
        "active": "analytics", "sub": "overview", "kpis": kpis, "top_events": top_events,
        "top_modules": top_modules, "top_sources": top_sources, "recent": recent,
        "adoption": adoption, "funnel": funnel,
    })


@login_required
def platform_analytics_live(request):
    """Real-time JSON — activity in the last 5 minutes (polled by the dashboard)."""
    from django.http import JsonResponse

    if not request.user.platform_level:
        return JsonResponse({}, status=403)
    from apps.analytics.models import AnalyticsEvent
    since = timezone.now() - timedelta(minutes=5)
    recent = AnalyticsEvent.objects.filter(created_at__gte=since)
    top_ev = (recent.exclude(event_name="").values("event_name")
              .annotate(n=Count("id")).order_by("-n").first())
    top_pg = (recent.exclude(path="").values("path")
              .annotate(n=Count("id")).order_by("-n").first())
    return JsonResponse({
        "online": recent.exclude(session_id="").values("session_id").distinct().count()
                  + recent.exclude(anonymous_id="").filter(session_id="").values("anonymous_id").distinct().count(),
        "users": recent.filter(user__isnull=False).values("user").distinct().count(),
        "companies": recent.filter(company__isnull=False).values("company").distinct().count(),
        "events_5m": recent.count(),
        "per_min": round(recent.count() / 5, 1),
        "top_event": top_ev["event_name"] if top_ev else "—",
        "top_page": top_pg["path"] if top_pg else "—",
    })


@login_required
def platform_analytics_export(request, kind):
    """CSV export of an analytics view (events | health | adoption)."""
    import csv

    from django.http import HttpResponse

    if not request.user.platform_level:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")

    now = timezone.now()
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="lulaworks-{kind}-{now:%Y%m%d}.csv"'
    w = csv.writer(resp)

    if kind == "events":
        from apps.analytics.models import AnalyticsEvent
        w.writerow(["When", "Event", "Module", "Feature", "Company", "User", "Source", "Device"])
        for e in (AnalyticsEvent.objects.select_related("user", "company")
                  .order_by("-created_at")[:5000]):
            w.writerow([e.created_at.strftime("%Y-%m-%d %H:%M:%S"), e.event_name, e.module,
                        e.feature, getattr(e.company, "name", ""), getattr(e.user, "email", ""),
                        e.source, e.device])
    elif kind == "health":
        from apps.analytics import reports
        w.writerow(["Company", "Status", "Users", "Quotes", "Jobs", "Events 30d",
                    "Days since active", "Score"])
        for r in reports.company_health(limit=1000):
            w.writerow([r["company"].name, r["status"], r["users"], r["quotes"], r["jobs"],
                        r["events_30d"], r["days_since"] if r["days_since"] is not None else "",
                        r["score"]])
    elif kind == "adoption":
        from apps.analytics import reports
        a = reports.feature_adoption()
        w.writerow(["Module", "Companies using", "Adoption %",
                    f"(of {a['active_companies']} active)"])
        for r in a["rows"]:
            w.writerow([r["module"], r["companies"], r["pct"], ""])
    else:
        messages.error(request, "Unknown export.")
        return redirect("web:platform_analytics")
    return resp


@login_required
def platform_analytics_retention(request):
    if not request.user.platform_level:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")
    from apps.analytics import reports
    return render(request, "web/platform/analytics_retention.html", {
        "active": "analytics", "sub": "retention", "data": reports.retention_cohorts()})


@login_required
def platform_analytics_health(request):
    if not request.user.platform_level:
        messages.error(request, "The platform console is for platform administrators only.")
        return redirect("web:dashboard")
    from apps.analytics import reports
    rows = reports.company_health()
    counts = {"healthy": 0, "active": 0, "at_risk": 0, "dormant": 0, "new": 0}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return render(request, "web/platform/analytics_health.html", {
        "active": "analytics", "sub": "health", "rows": rows, "counts": counts})


@login_required
def platform_support_sla(request):
    """Support SLA & analytics — first-response and resolution times per priority,
    against target SLAs, plus current breaches. Read for any platform staff."""
    if not request.user.can_platform("support"):
        messages.error(request, "The support desk is for platform staff only.")
        return redirect("web:dashboard")

    from apps.support.models import OPEN_STATUSES, SupportTicket, TicketPriority

    # First-response SLA targets (hours) — the basis for future paid SLAs.
    TARGETS = {"urgent": 1, "high": 4, "normal": 24, "low": 48}

    now = timezone.now()
    d30 = now - timedelta(days=30)

    def _avg_hours(deltas):
        return round(sum(deltas) / len(deltas), 1) if deltas else None

    rows, breaches_total = [], 0
    all_open = 0
    for value, label in TicketPriority.choices:
        tks = list(SupportTicket.all_objects.filter(priority=value))
        resp = [(t.first_response_at - t.created_at).total_seconds() / 3600
                for t in tks if t.first_response_at]
        res = [(t.resolved_at - t.created_at).total_seconds() / 3600
               for t in tks if t.resolved_at]
        target = TARGETS.get(value, 24)
        # Breaching = still open, no first response, and past the target.
        breaching = [t for t in tks if t.status in OPEN_STATUSES and not t.first_response_at
                     and (now - t.created_at).total_seconds() / 3600 > target]
        open_n = sum(1 for t in tks if t.status in OPEN_STATUSES)
        all_open += open_n
        breaches_total += len(breaching)
        rows.append({
            "label": label, "value": value, "count": len(tks), "open": open_n,
            "target": target, "avg_response": _avg_hours(resp),
            "avg_resolution": _avg_hours(res), "breaching": len(breaching),
        })

    allt = SupportTicket.all_objects
    resolved_30 = allt.filter(resolved_at__gte=d30)
    all_resp = [(t.first_response_at - t.created_at).total_seconds() / 3600
                for t in allt.filter(first_response_at__isnull=False)]
    all_res = [(t.resolved_at - t.created_at).total_seconds() / 3600 for t in resolved_30]
    kpis = {
        "open": all_open, "breaching": breaches_total,
        "resolved_30d": resolved_30.count(),
        "avg_response": _avg_hours(all_resp), "avg_resolution": _avg_hours(all_res),
    }
    return render(request, "web/platform/support_sla.html", {
        "active": "support", "rows": rows, "kpis": kpis})


@login_required
def platform_kb(request):
    """Manage the LulaWorks Knowledge Base — the articles tenants read and the AI
    assistant is grounded in. View for any platform staff; edit needs settings."""
    if not request.user.can_platform("support"):
        messages.error(request, "The support desk is for platform staff only.")
        return redirect("web:dashboard")

    from django.utils.text import slugify

    from apps.core.context import system_scope
    from apps.support.models import KBArticle, TicketCategory

    can_edit = request.user.can_platform("settings")
    with system_scope():
        if request.method == "POST":
            if not can_edit:
                messages.error(request, "You don't have settings access.")
                return redirect("web:platform_kb")
            action = request.POST.get("action")
            try:
                if action == "save":
                    pk = request.POST.get("id") or ""
                    title = (request.POST.get("title") or "").strip()
                    if not title:
                        raise ValueError("A title is required.")
                    art = KBArticle.objects.filter(pk=pk).first() if pk else KBArticle()
                    art.title = title[:200]
                    if not art.slug:
                        base = slugify(title)[:200] or "article"
                        slug, i = base, 1
                        while KBArticle.objects.filter(slug=slug).exclude(pk=art.pk).exists():
                            i += 1
                            slug = f"{base}-{i}"
                        art.slug = slug
                    art.category = request.POST.get("category", "other")
                    art.summary = (request.POST.get("summary") or "")[:300]
                    art.body = request.POST.get("body", "")
                    art.tags = (request.POST.get("tags") or "")[:200]
                    art.is_published = request.POST.get("is_published") == "on"
                    art.save()
                    messages.success(request, "Article saved.")
                elif action == "delete":
                    KBArticle.objects.filter(pk=request.POST.get("id")).delete()
                    messages.success(request, "Article deleted.")
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("web:platform_kb")

        editing = None
        eid = request.GET.get("edit")
        if eid == "new":
            editing = KBArticle(is_published=True)
        elif eid:
            editing = KBArticle.objects.filter(pk=eid).first()
        articles = list(KBArticle.objects.all())

    return render(request, "web/platform/kb.html", {
        "active": "support", "articles": articles, "editing": editing,
        "categories": TicketCategory.choices, "can_edit": can_edit,
    })


@login_required
def platform_tenants_csv(request):
    """Download every tenant with plan, users, AI credits + 30-day usage."""
    import csv

    from django.http import HttpResponse

    if not request.user.platform_level:
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
