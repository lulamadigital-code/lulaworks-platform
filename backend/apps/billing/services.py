"""Subscription, entitlement & billing engine (SAAS_PLATFORM §2-7).

Two layers:
  * Entitlements — every gated action consults a `check()` returning
    allow / warn / block, so the app informs + offers an upgrade rather than
    failing unexpectedly.
  * Subscription lifecycle — trial, plan changes (upgrade/downgrade), renewal,
    cancellation, and AI-credit-pack purchases. Effective limits are written to
    the cached ``Company.max_users`` / ``storage_quota_bytes`` that enforcement
    already reads, and AI credits flow through the append-only credit ledger.

Plans are DATA (apps.billing.models.Plan), so adding a plan — including a future
Enterprise tier — needs no code change here.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

WARN_RATIO = 0.9  # warn at 90% of a limit

GB = 1024 ** 3

# ── Free trial (spec): 30 days of Professional features, capped ───────────────
TRIAL_DAYS = 30
TRIAL_PLAN_CODE = "professional"
TRIAL_USERS = 2
TRIAL_STORAGE_BYTES = 2 * GB
TRIAL_CREDITS = Decimal("100")
STORAGE_WARN_RATIO = 0.8  # notify admins past 80% (spec)


@dataclass
class EntitlementResult:
    allowed: bool
    warn: bool = False
    reason: str = ""

    @property
    def status(self) -> str:
        if not self.allowed:
            return "block"
        return "warn" if self.warn else "allow"


def _subscription(company):
    return getattr(company, "subscription", None)


def _seat_price(sub) -> Decimal:
    """Per-user overage rate in force (0 = hard cap, no overage)."""
    if sub is None:
        return Decimal("0")
    return Decimal(sub.limit("per_seat_price", sub.plan.per_seat_price) or 0)


def check_user_seat(company, current_user_count: int) -> EntitlementResult:
    sub = _subscription(company)
    limit = int(sub.limit("max_users", company.max_users) if sub else company.max_users)
    seat_price = _seat_price(sub)
    if current_user_count >= limit:
        # Over the included seats: allow with a billing note when the plan has
        # per-seat overage; otherwise it's a hard cap (must upgrade).
        if seat_price > 0:
            sym = sub.currency_symbol if sub else "R"
            return EntitlementResult(
                True, warn=True,
                reason=f"Beyond your {limit} included seats — extra users are "
                       f"billed at {sym}{seat_price:g}/user/month.")
        return EntitlementResult(
            False, reason=f"User limit ({limit}) reached — upgrade to add more."
        )
    if current_user_count >= int(limit * WARN_RATIO):
        return EntitlementResult(
            True, warn=True, reason=f"Approaching user limit ({limit})."
        )
    return EntitlementResult(True)


def check_module(company, module_key: str) -> EntitlementResult:
    sub = _subscription(company)
    entitled = sub.plan.module_entitlements if sub else []
    if module_key in entitled or not entitled:
        return EntitlementResult(True)
    return EntitlementResult(
        False, reason=f"'{module_key}' is not in your plan — upgrade to enable."
    )


def has_feature(company, module_key: str) -> bool:
    """Plain boolean entitlement check for templates/guards."""
    return check_module(company, module_key).allowed


# ══════════════════════════════════════════════════════════════════════════════
# Counts & limits
# ══════════════════════════════════════════════════════════════════════════════

def active_user_count(company) -> int:
    """Users who consume a licence = active memberships (login accounts)."""
    from apps.identity.models import Membership
    return Membership.objects.filter(company=company, status="active").count()


def employee_count(company) -> int:
    """Workforce members (technicians, drivers, …) — never licence-limited."""
    from apps.core.context import tenant_scope
    from apps.execution.models import Resource
    with tenant_scope(company.id):
        return Resource.objects.filter(kind="employee").count()


def effective_monthly_credits(subscription) -> Decimal:
    """The monthly AI-credit allowance in force (trial is capped at 100). Honours
    a per-tenant `monthly_ai_credits` override — how a custom Enterprise deal gets
    a bespoke allowance without a new plan."""
    from .models import SubscriptionStatus
    if subscription.status == SubscriptionStatus.TRIAL:
        return TRIAL_CREDITS
    return Decimal(subscription.limit("monthly_ai_credits",
                                      subscription.plan.monthly_ai_credits))


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _add_period(start: date, cycle: str) -> date:
    """End date one billing period after `start` (calendar-correct)."""
    if cycle == "annual":
        try:
            return start.replace(year=start.year + 1)
        except ValueError:  # 29 Feb → 28 Feb next year
            return start.replace(year=start.year + 1, day=28)
    month = start.month + 1
    year = start.year
    if month > 12:
        month, year = 1, year + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def _sync_company_limits(company, max_users: int, storage_bytes: int) -> None:
    """Write the effective plan limits onto the cached Company fields that the
    seat + storage enforcement read."""
    company.max_users = max_users
    company.storage_quota_bytes = storage_bytes
    company.save(update_fields=["max_users", "storage_quota_bytes", "updated_at"])


def _credit_balance(company) -> Decimal:
    from apps.ai_platform.gateway import credit_balance
    return credit_balance(company)


def _topup_to_floor(company, target) -> None:
    """Raise the credit balance UP to `target` (never reduces) — used on plan
    change so an upgrade grants the new allowance immediately."""
    from apps.ai_platform.gateway import allocate_credits
    target = Decimal(target)
    shortfall = target - _credit_balance(company)
    if shortfall > 0:
        allocate_credits(company, shortfall, source="plan_change")


def _reset_credits_to(company, target) -> None:
    """Set the credit balance exactly to `target` (the monthly reset / trial grant)."""
    from apps.ai_platform.gateway import allocate_credits
    delta = Decimal(target) - _credit_balance(company)
    if delta != 0:
        allocate_credits(company, delta, source="cycle_reset")


def _log(company, kind, description, *, amount=0, credits=0, plan=None):
    from .models import BillingTransaction
    return BillingTransaction.objects.create(
        company=company, kind=kind, description=description,
        amount=Decimal(amount), credits=Decimal(credits), plan=plan,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Subscription lifecycle
# ══════════════════════════════════════════════════════════════════════════════

@transaction.atomic
def start_trial(company, actor=None):
    """Begin a 30-day Professional trial (idempotent). No card required: capped
    at 2 users / 2 GB / 100 credits, then the company must pick a paid plan."""
    from .models import BillingCycle, Plan, Subscription, SubscriptionStatus
    existing = getattr(company, "subscription", None)
    if existing is not None:
        return existing
    plan = Plan.objects.get(code=TRIAL_PLAN_CODE)
    today = timezone.localdate()
    sub = Subscription.objects.create(
        company=company, plan=plan, status=SubscriptionStatus.TRIAL,
        billing_cycle=BillingCycle.MONTHLY,
        currency=getattr(company, "currency", None) or "ZAR",
        current_period_start=today,
        current_period_end=today + timedelta(days=TRIAL_DAYS), seats=TRIAL_USERS,
        overrides={"max_users": TRIAL_USERS, "storage_quota_bytes": TRIAL_STORAGE_BYTES},
    )
    _sync_company_limits(company, TRIAL_USERS, TRIAL_STORAGE_BYTES)
    _reset_credits_to(company, TRIAL_CREDITS)
    _log(company, BillingTransaction_kind("TRIAL_STARTED"),
         "30-day Professional trial started", credits=TRIAL_CREDITS, plan=plan)
    _notify_billing(
        company, subject="Your Lulaworks trial has started",
        heading="Welcome to your 30-day trial",
        body=(f"Your 30-day Professional trial is active until "
              f"{sub.current_period_end:%d %B %Y}. Explore everything — no card "
              "required. We'll remind you before it ends."))
    return sub


# ── Billing notifications ─────────────────────────────────────────────────────

def _billing_admins(company):
    """Users who should receive billing email — active members who can manage the
    company. Never emails field-only employees."""
    from apps.identity.models import Membership
    seen, users = set(), []
    for m in (Membership.objects.filter(company=company, status="active",
                                        role__permissions__codename="company.manage")
              .select_related("user")):
        u = m.user
        if u and u.email and u.id not in seen:
            seen.add(u.id)
            users.append(u)
    return users


def _notify_billing(company, *, subject, heading, body, cta_url="", cta_label=""):
    """Send a billing email to the company's admins (receipts, renewals,
    reminders). Honours preferences + login access; never raises."""
    try:
        from apps.notifications.dispatch import _email_allowed
        from apps.notifications.models import EmailCategory
        from apps.notifications.service import send_email
    except Exception:  # notifications app unavailable
        return
    ctx = {"heading": heading, "body": body}
    if cta_url:
        ctx.update({"cta_url": cta_url, "cta_label": cta_label or "Open billing"})
    for user in _billing_admins(company):
        if not _email_allowed(user, EmailCategory.BILLING):
            continue
        try:
            send_email(to=user.email, subject=subject, template="generic",
                       context=ctx, company=company,
                       to_name=(user.get_full_name() or "").strip(),
                       category=EmailCategory.BILLING)
        except Exception:  # noqa: BLE001 - a receipt failing must not break billing
            pass


AGREEMENT_SALT = "enterprise-agreement"


def proposed_limits(ov: dict) -> dict:
    """The agreed (draft) limits — held under `proposed_limits` until finalized,
    with a fallback to legacy top-level keys for pre-staging deals."""
    ov = ov or {}
    return ov.get("proposed_limits") or {
        k: ov.get(k) for k in ("max_users", "storage_quota_bytes", "monthly_ai_credits")}


def agreement_token(company, ov) -> str:
    """A signed, self-contained token carrying the agreed-terms snapshot, so the
    public confirmation page shows exactly what was emailed without a DB lookup
    or login. Limits come from the proposed (draft) terms."""
    from django.core import signing
    p = proposed_limits(ov)
    return signing.dumps({
        "c": str(company.id), "ref": ov.get("contract_ref", ""),
        "price": str(ov.get("contract_price") or ""),
        "users": p.get("max_users"), "credits": p.get("monthly_ai_credits"),
        "storage": p.get("storage_quota_bytes"),
        "term": ov.get("contract_term_months"), "end": ov.get("contract_end", ""),
        "note": ov.get("contract_note", ""),
    }, salt=AGREEMENT_SALT)


def read_agreement_token(token: str, max_age_days: int = 90):
    """Return the terms snapshot from a token, or None if invalid/expired."""
    from django.core import signing
    try:
        return signing.loads(token, salt=AGREEMENT_SALT,
                             max_age=max_age_days * 86400)
    except signing.BadSignature:
        return None


def _site_base() -> str:
    from django.conf import settings
    return (getattr(settings, "SITE_URL", "") or "https://www.lulaworks.com").rstrip("/")


def send_enterprise_agreement(company) -> int:
    """Email the tenant's admins the agreed Enterprise terms with Accept /
    Request-a-change buttons (a secure confirmation page). Returns the recipient
    count; raises ValueError if there's nothing agreed to send yet."""
    sub = getattr(company, "subscription", None)
    ov = (sub.overrides if sub else None) or {}
    price = str(ov.get("contract_price") or "").strip()
    if not price:
        raise ValueError("Set the agreed price in Custom terms before sending the agreement.")

    from apps.notifications.dispatch import _email_allowed
    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email

    admins = [u for u in _billing_admins(company)
              if _email_allowed(u, EmailCategory.BILLING)]
    if not admins:
        raise ValueError("This customer has no billing admin who can receive email.")

    sales = ""
    try:
        from apps.administration.models import PlatformSettings
        sales = (PlatformSettings.load().sales_email or "").strip()
    except Exception:  # noqa: BLE001
        sales = ""
    cc = ([sales] if sales else []) + [u.email for u in admins[1:]]

    def _gb(b):
        return f"{int(b) / (1024 ** 3):.0f} GB" if b else ""

    def _commafmt(v):
        try:
            return f"{int(v):,}"
        except (TypeError, ValueError):
            return v or ""

    review_url = f"{_site_base()}/agreement/{agreement_token(company, ov)}/"
    p = proposed_limits(ov)
    primary = admins[0]
    ctx = {
        "heading": "Your Enterprise plan — agreed terms",
        "first_name": (primary.first_name or "").strip(),
        "ref": ov.get("contract_ref", ""), "users": p.get("max_users"),
        "credits": _commafmt(p.get("monthly_ai_credits")),
        "storage": _gb(p.get("storage_quota_bytes")), "price": price,
        "term": ov.get("contract_term_months"), "end": ov.get("contract_end", ""),
        "note": ov.get("contract_note", ""),
        "accept_url": f"{review_url}?do=accept",
        "change_url": f"{review_url}?do=change",
    }
    send_email(
        to=primary.email, to_name=(primary.get_full_name() or "").strip(),
        subject="Your Lulaworks Enterprise agreement — please confirm",
        template="enterprise_agreement", context=ctx, company=company,
        category=EmailCategory.BILLING, cc=cc, reply_to=sales)
    return len(admins) + (1 if sales else 0)


def record_agreement_response(company, *, accepted: bool, message="", snapshot=None):
    """Record the customer's response on the subscription and notify sales."""
    sub = getattr(company, "subscription", None)
    if sub is None:
        return
    ov = dict(sub.overrides or {})
    now = timezone.now().isoformat()
    if accepted:
        ov["agreement_accepted_at"] = now
        ov["agreement_accepted_snapshot"] = snapshot or {}
        ov.pop("agreement_change_request", None)
    else:
        ov["agreement_change_request"] = {"at": now, "message": message[:2000]}
    sub.overrides = ov
    sub.save(update_fields=["overrides", "updated_at"])

    sales = ""
    try:
        from apps.administration.models import PlatformSettings
        sales = (PlatformSettings.load().sales_email or "").strip()
    except Exception:  # noqa: BLE001
        sales = ""
    if not sales:
        return
    try:
        from apps.notifications.models import EmailCategory
        from apps.notifications.service import send_email
        if accepted:
            subject = f"✓ {company.name} accepted their Enterprise agreement"
            body = (f"{company.name} accepted the Enterprise agreement"
                    f"{' (' + snapshot.get('ref', '') + ')' if snapshot and snapshot.get('ref') else ''}"
                    f" on {now[:10]}. You can finalise their account.")
        else:
            subject = f"{company.name} requested changes to their Enterprise agreement"
            body = (f"{company.name} asked to change their proposed Enterprise terms:\n\n"
                    f"{message}\n\nUpdate the terms and re-send the agreement.")
        send_email(to=sales, subject=subject, template="generic",
                   context={"heading": subject, "body": body}, company=company,
                   category=EmailCategory.BILLING)
    except Exception:  # noqa: BLE001
        pass


def _enterprise_contracts():
    """(subscription, contract_end date, overrides) for every subscription that
    has a stored contract_end (i.e. a negotiated Enterprise deal)."""
    from datetime import date as _date
    from .models import Subscription
    out = []
    for sub in Subscription.all_objects.select_related("company"):
        ov = sub.overrides or {}
        end = ov.get("contract_end")
        if not end:
            continue
        try:
            out.append((sub, _date.fromisoformat(end), ov))
        except (ValueError, TypeError):
            continue
    return out


def upcoming_renewals(within_days: int = 60):
    """Enterprise contracts renewing within `within_days` (includes overdue, i.e.
    negative days_left), soonest first — for the console renewals view/badge."""
    today = timezone.localdate()
    rows = []
    for sub, end, ov in _enterprise_contracts():
        days = (end - today).days
        if days <= within_days:
            rows.append({
                "company": sub.company, "sub": sub, "end": end, "days_left": days,
                "ref": ov.get("contract_ref", ""), "price": ov.get("contract_price", ""),
                "term": ov.get("contract_term_months"),
            })
    rows.sort(key=lambda r: r["days_left"])
    return rows


#: Remind about renewals this many days out.
RENEWAL_REMINDER_DAYS = 30


def run_renewal_reminders(today=None, window_days: int = RENEWAL_REMINDER_DAYS):
    """Daily sweep: email sales a digest of Enterprise contracts entering their
    renewal window. Reminds once per contract_end (a renewed contract, with a new
    end date, re-arms). Never raises."""
    today = today or timezone.localdate()
    due = []
    for sub, end, ov in _enterprise_contracts():
        days = (end - today).days
        if days <= window_days and ov.get("renewal_reminded_for") != end.isoformat():
            due.append((sub, end, days, ov))
    if not due:
        return {"reminded": 0}

    sales = ""
    try:
        from apps.administration.models import PlatformSettings
        sales = (PlatformSettings.load().sales_email or "").strip()
    except Exception:  # noqa: BLE001
        sales = ""
    if sales:
        lines = ["These Enterprise contracts are up for renewal:", ""]
        for sub, end, days, ov in due:
            when = f"overdue by {-days} days" if days < 0 else f"in {days} days"
            lines.append(f"• {sub.company.name} — renews {end.isoformat()} ({when})"
                         f"  ·  {ov.get('contract_price', '')}  ·  {ov.get('contract_ref', '')}")
        lines += ["", "Reach out to confirm renewal terms before the date."]
        try:
            from apps.notifications.models import EmailCategory
            from apps.notifications.service import send_email
            send_email(to=sales, subject=f"Enterprise renewals due ({len(due)})",
                       template="generic",
                       context={"heading": "Upcoming Enterprise renewals",
                                "body": "\n".join(lines)},
                       category=EmailCategory.BILLING)
        except Exception:  # noqa: BLE001
            pass

    for sub, end, days, ov in due:
        nov = dict(sub.overrides or {})
        nov["renewal_reminded_for"] = end.isoformat()
        sub.overrides = nov
        sub.save(update_fields=["overrides", "updated_at"])
    return {"reminded": len(due)}


#: How many days before a trial ends to send the reminder.
TRIAL_REMINDER_DAYS = 3


def run_trial_reminders(today=None) -> dict:
    """Daily sweep (Celery beat): warn companies whose trial ends soon, and tell
    those whose trial expired yesterday. Platform-wide (Subscription is not
    tenant-scoped). Returns counts for logging/tests."""
    from .models import Subscription, SubscriptionStatus
    today = today or timezone.localdate()
    reminded = expired = 0

    ending = Subscription.objects.filter(
        status=SubscriptionStatus.TRIAL,
        current_period_end=today + timedelta(days=TRIAL_REMINDER_DAYS))
    for sub in ending.select_related("company"):
        _notify_billing(
            sub.company, subject="Your Lulaworks trial ends soon",
            heading=f"{TRIAL_REMINDER_DAYS} days left in your trial",
            body=(f"Your Professional trial ends on {sub.current_period_end:%d %B %Y}. "
                  "Choose a plan to keep your data, users and AI credits — nothing "
                  "is lost, you just pick up where you left off."))
        reminded += 1

    just_expired = Subscription.objects.filter(
        status=SubscriptionStatus.TRIAL, current_period_end=today - timedelta(days=1))
    for sub in just_expired.select_related("company"):
        _notify_billing(
            sub.company, subject="Your Lulaworks trial has ended",
            heading="Your trial has ended",
            body=("Your 30-day trial has ended. Your data is safe — choose a plan "
                  "any time to continue where you left off."))
        expired += 1

    return {"reminded": reminded, "expired": expired}


@transaction.atomic
def change_plan(company, plan_code: str, billing_cycle: str = "monthly",
                currency: str = None, actor=None):
    """Move a company onto a plan. Activates immediately, preserves all data.
    Upgrades raise limits + top up credits now; downgrades keep data and flag
    over-limit if current usage exceeds the smaller plan. Bills in `currency`
    (defaults to the company's currency)."""
    from .models import Plan, Subscription, SubscriptionStatus
    plan = Plan.objects.get(code=plan_code, is_active=True)
    today = timezone.localdate()
    currency = currency or getattr(company, "currency", None) or "ZAR"
    sub = getattr(company, "subscription", None)
    prev_tier = sub.plan.tier if sub is not None else -1

    if sub is None:
        sub = Subscription(company=company)
    sub.plan = plan
    sub.billing_cycle = billing_cycle
    sub.currency = currency
    sub.status = SubscriptionStatus.ACTIVE
    sub.cancel_at_period_end = False
    sub.current_period_start = today
    sub.current_period_end = _add_period(today, billing_cycle)
    sub.seats = plan.max_users
    sub.overrides = {}
    sub.save()

    _sync_company_limits(company, plan.max_users, plan.storage_quota_bytes)
    _topup_to_floor(company, plan.monthly_ai_credits)
    recompute_over_limit(company)

    if prev_tier < 0 or plan.tier == prev_tier:
        kind = BillingTransaction_kind("PLAN_CHANGE")
    elif plan.tier > prev_tier:
        kind = BillingTransaction_kind("UPGRADE")
    else:
        kind = BillingTransaction_kind("DOWNGRADE")
    price = plan.price_in(currency, billing_cycle)
    _log(company, kind, f"Switched to {plan.name} ({billing_cycle}, {currency})",
         amount=price, plan=plan)
    direction = ("upgraded to" if plan.tier > prev_tier
                 else "changed to" if plan.tier == prev_tier or prev_tier < 0
                 else "moved to")
    # Contact-sales plans (Enterprise) have a R0 list price — never state that.
    # Pricing is the negotiated contract, sent separately via the agreement email.
    if plan.price == 0 and plan.annual_price == 0:
        _body = (f"Your subscription has been {direction} the {plan.name} plan. "
                 f"Your agreed pricing and terms are confirmed separately by your "
                 f"account manager. Your current period runs to "
                 f"{sub.current_period_end:%d %B %Y}.")
    else:
        _body = (f"Your subscription has been {direction} the {plan.name} plan "
                 f"({billing_cycle}), billed at {currency} {price:,.2f}. Your new "
                 f"period runs to {sub.current_period_end:%d %B %Y}.")
    _notify_billing(
        company, subject=f"Your Lulaworks plan: {plan.name}",
        heading=f"You're now on {plan.name}", body=_body)
    try:
        from apps.enterprise.services import record
        from apps.enterprise.models import AuditAction
        record(AuditAction.PLAN_CHANGED, company=company, actor=actor,
               summary=f"Plan {direction} {plan.name} ({billing_cycle})",
               plan=plan.code, cycle=billing_cycle, currency=currency)
    except Exception:
        pass
    return sub


@transaction.atomic
def cancel_subscription(company, actor=None):
    """Graceful cancel — access stays until current_period_end; data is kept."""
    sub = getattr(company, "subscription", None)
    if sub is None:
        return None
    sub.cancel_at_period_end = True
    sub.save(update_fields=["cancel_at_period_end", "updated_at"])
    _log(company, BillingTransaction_kind("CANCELLATION"),
         "Subscription set to cancel at the end of the current period")
    return sub


@transaction.atomic
def purchase_credit_pack(company, pack_code: str, actor=None):
    """Buy a one-off AI-credit pack — added to the balance immediately."""
    from apps.ai_platform.gateway import topup_credits
    from .models import CreditPack
    pack = CreditPack.objects.get(code=pack_code, is_active=True)
    topup_credits(company, pack.credits, source=f"pack:{pack.code}")
    _log(company, BillingTransaction_kind("CREDIT_PACK"),
         f"Purchased {pack.name}", amount=pack.price, credits=pack.credits)
    _notify_billing(
        company, subject=f"Receipt — {pack.name}",
        heading="Thank you for your purchase",
        body=(f"Your purchase of {pack.name} ({pack.credits:g} AI credits) for "
              f"{pack.price:,.2f} was successful. The credits have been added to "
              "your balance."))
    return pack


@transaction.atomic
def renew_cycle(company, today: date | None = None):
    """Monthly heartbeat (Celery beat). Resets AI credits to the plan's monthly
    allowance, and — when the billing period has elapsed — rolls the period
    forward, converts a lapsed trial to suspended, or applies a pending cancel."""
    from .models import SubscriptionStatus
    sub = getattr(company, "subscription", None)
    if sub is None:
        return None
    today = today or timezone.localdate()

    if sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL):
        _reset_credits_to(company, effective_monthly_credits(sub))

    if sub.current_period_end and today >= sub.current_period_end:
        if sub.cancel_at_period_end:
            sub.status = SubscriptionStatus.CANCELLED
            sub.save(update_fields=["status", "updated_at"])
        elif sub.status == SubscriptionStatus.TRIAL:
            # Trial elapsed without conversion → must choose a paid plan.
            sub.status = SubscriptionStatus.SUSPENDED
            sub.save(update_fields=["status", "updated_at"])
        else:
            sub.current_period_start = today
            sub.current_period_end = _add_period(today, sub.billing_cycle)
            sub.save(update_fields=["current_period_start", "current_period_end", "updated_at"])
            _log(company, BillingTransaction_kind("RENEWAL"),
                 f"{sub.plan.name} renewed", amount=sub.price, plan=sub.plan)
    return sub


def recompute_over_limit(company) -> bool:
    """Flag the subscription over-limit when usage exceeds the current plan (e.g.
    right after a downgrade). Data stays accessible; new users get blocked."""
    sub = getattr(company, "subscription", None)
    if sub is None:
        return False
    # With per-seat overage, exceeding the user cap is billed, not a lock — only
    # storage (and users on a hard-cap plan) trip the over-limit state.
    users_over = active_user_count(company) > company.max_users and _seat_price(sub) <= 0
    over = users_over or (company.storage_used_bytes > company.storage_quota_bytes)
    if sub.is_over_limit != over:
        sub.is_over_limit = over
        sub.save(update_fields=["is_over_limit", "updated_at"])
    return over


# ══════════════════════════════════════════════════════════════════════════════
# Enforcement & overview
# ══════════════════════════════════════════════════════════════════════════════

def can_add_user(company) -> EntitlementResult:
    """Gate on inviting a new licensed user (seat limit + over-limit lock).
    With per-seat overage, extra users are allowed and billed rather than blocked."""
    sub = _subscription(company)
    # The post-downgrade lock still applies only when there's no overage; if the
    # plan bills extra seats, adding users is always allowed (they're charged).
    if sub is not None and sub.is_over_limit and _seat_price(sub) <= 0:
        return EntitlementResult(
            False,
            reason="You're over your plan's user limit after a downgrade — "
                   "upgrade or remove a user before adding more.",
        )
    return check_user_seat(company, active_user_count(company))


def included_seats(company) -> int:
    sub = _subscription(company)
    return int(sub.limit("max_users", company.max_users) if sub else company.max_users)


def billable_extra_seats(company) -> int:
    """Active users beyond the included seats (0 unless over on an overage plan)."""
    return max(0, active_user_count(company) - included_seats(company))


def effective_monthly_price(company) -> Decimal:
    """Recurring monthly-equivalent charge: base plan price + billed overage
    seats, in the subscription's currency. Enterprise (price 0) contributes its
    recorded contract price via the base being 0 here (handled by callers)."""
    sub = _subscription(company)
    if sub is None:
        return Decimal("0")
    if sub.plan.price == 0:
        base = Decimal("0")
    elif sub.billing_cycle == "annual" and sub.plan.annual_price:
        base = Decimal(sub.plan.price_in(sub.currency, "annual")) / 12
    else:
        base = Decimal(sub.plan.price_in(sub.currency, "monthly"))
    return base + Decimal(billable_extra_seats(company)) * _seat_price(sub)


def storage_status(company) -> dict:
    """Storage usage snapshot, with an 80% warning flag for admins."""
    used = int(company.storage_used_bytes or 0)
    quota = int(company.storage_quota_bytes or 1)
    pct = min(100, round(used / quota * 100)) if quota else 0
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "used_gb": round(used / GB, 2),
        "quota_gb": round(quota / GB, 1),
        "pct": pct,
        "warn": pct >= int(STORAGE_WARN_RATIO * 100),
        "full": used >= quota,
    }


def priced_plans(currency: str) -> list:
    """Active plans with their prices resolved for `currency` — templates can't
    call price_in(currency, cycle) with args, so we flatten it here. Used by both
    the public pricing page and the in-app billing page."""
    from .models import Plan
    rows = []
    for p in Plan.objects.filter(is_active=True).order_by("tier"):
        rows.append({
            "id": p.id, "code": p.code, "name": p.name, "tier": p.tier,
            "is_popular": p.is_popular, "features": p.features,
            "max_users": p.max_users, "monthly_ai_credits": p.monthly_ai_credits,
            "storage_quota_bytes": p.storage_quota_bytes,
            "per_seat_price": p.per_seat_price,
            "symbol": p.symbol_for(currency),
            "monthly": p.price_in(currency, "monthly"),
            "annual": p.price_in(currency, "annual"),
            "annual_saving": p.annual_saving_in(currency),
            # A zero-priced plan is sold per contract ("Contact sales"), not
            # self-serve — the UI shows a Contact button instead of a price.
            "contact_sales": (p.price == 0 and p.annual_price == 0),
        })
    return rows


def supported_currencies() -> list:
    from .models import SUPPORTED_CURRENCIES
    return SUPPORTED_CURRENCIES


def subscription_overview(company) -> dict:
    """Everything the Billing page and dashboard widgets render — one source."""
    from .models import CreditPack
    sub = getattr(company, "subscription", None)
    today = timezone.localdate()

    credits_remaining = _credit_balance(company)
    users = active_user_count(company)
    storage = storage_status(company)

    trial_days_left = None
    if sub is not None and sub.is_trialing and sub.current_period_end:
        trial_days_left = max(0, (sub.current_period_end - today).days)

    from .models import currency_symbol
    currency = (sub.currency if sub else None) or getattr(company, "currency", None) or "ZAR"

    return {
        "subscription": sub,
        "plan": sub.plan if sub else None,
        "billing_cycle": sub.billing_cycle if sub else "monthly",
        "currency": currency,
        "currency_symbol": currency_symbol(currency),
        "status": sub.status if sub else "none",
        "is_trialing": bool(sub and sub.is_trialing),
        "trial_days_left": trial_days_left,
        "next_billing_date": sub.current_period_end if sub else None,
        "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
        "is_over_limit": bool(sub and sub.is_over_limit),
        "credits_remaining": credits_remaining,
        "credits_monthly": effective_monthly_credits(sub) if sub else Decimal("0"),
        "storage": storage,
        "user_count": users,
        "user_limit": company.max_users,
        "included_seats": included_seats(company),
        "extra_seats": billable_extra_seats(company),
        "per_seat_price": _seat_price(sub),
        "extra_seat_cost": Decimal(billable_extra_seats(company)) * _seat_price(sub),
        "effective_monthly": effective_monthly_price(company),
        "employee_count": employee_count(company),
        "plans": priced_plans(currency),
        "packs": list(CreditPack.objects.filter(is_active=True).order_by("price")),
        "history": list(company.billing_transactions.all()[:20]),
    }


def BillingTransaction_kind(name: str):
    """Resolve a BillingTransaction.Kind member by attribute name (kept as a
    tiny helper so the lifecycle code above reads cleanly and imports stay lazy)."""
    from .models import BillingTransaction
    return getattr(BillingTransaction.Kind, name)
