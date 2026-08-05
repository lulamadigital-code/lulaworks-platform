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


def check_user_seat(company, current_user_count: int) -> EntitlementResult:
    sub = _subscription(company)
    limit = sub.limit("max_users", company.max_users) if sub else company.max_users
    if current_user_count >= limit:
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
    """The monthly AI-credit allowance in force (trial is capped at 100)."""
    from .models import SubscriptionStatus
    if subscription.status == SubscriptionStatus.TRIAL:
        return TRIAL_CREDITS
    return Decimal(subscription.plan.monthly_ai_credits)


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
        billing_cycle=BillingCycle.MONTHLY, current_period_start=today,
        current_period_end=today + timedelta(days=TRIAL_DAYS), seats=TRIAL_USERS,
        overrides={"max_users": TRIAL_USERS, "storage_quota_bytes": TRIAL_STORAGE_BYTES},
    )
    _sync_company_limits(company, TRIAL_USERS, TRIAL_STORAGE_BYTES)
    _reset_credits_to(company, TRIAL_CREDITS)
    _log(company, BillingTransaction_kind("TRIAL_STARTED"),
         "30-day Professional trial started", credits=TRIAL_CREDITS, plan=plan)
    return sub


@transaction.atomic
def change_plan(company, plan_code: str, billing_cycle: str = "monthly", actor=None):
    """Move a company onto a plan. Activates immediately, preserves all data.
    Upgrades raise limits + top up credits now; downgrades keep data and flag
    over-limit if current usage exceeds the smaller plan."""
    from .models import Plan, Subscription, SubscriptionStatus
    plan = Plan.objects.get(code=plan_code, is_active=True)
    today = timezone.localdate()
    sub = getattr(company, "subscription", None)
    prev_tier = sub.plan.tier if sub is not None else -1

    if sub is None:
        sub = Subscription(company=company)
    sub.plan = plan
    sub.billing_cycle = billing_cycle
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
    _log(company, kind, f"Switched to {plan.name} ({billing_cycle})",
         amount=plan.price_for(billing_cycle), plan=plan)
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
    over = (active_user_count(company) > company.max_users) or (
        company.storage_used_bytes > company.storage_quota_bytes
    )
    if sub.is_over_limit != over:
        sub.is_over_limit = over
        sub.save(update_fields=["is_over_limit", "updated_at"])
    return over


# ══════════════════════════════════════════════════════════════════════════════
# Enforcement & overview
# ══════════════════════════════════════════════════════════════════════════════

def can_add_user(company) -> EntitlementResult:
    """Gate on inviting a new licensed user (seat limit + over-limit lock)."""
    sub = _subscription(company)
    if sub is not None and sub.is_over_limit:
        return EntitlementResult(
            False,
            reason="You're over your plan's user limit after a downgrade — "
                   "upgrade or remove a user before adding more.",
        )
    return check_user_seat(company, active_user_count(company))


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


def subscription_overview(company) -> dict:
    """Everything the Billing page and dashboard widgets render — one source."""
    from .models import CreditPack, Plan
    sub = getattr(company, "subscription", None)
    today = timezone.localdate()

    credits_remaining = _credit_balance(company)
    users = active_user_count(company)
    storage = storage_status(company)

    trial_days_left = None
    if sub is not None and sub.is_trialing and sub.current_period_end:
        trial_days_left = max(0, (sub.current_period_end - today).days)

    return {
        "subscription": sub,
        "plan": sub.plan if sub else None,
        "billing_cycle": sub.billing_cycle if sub else "monthly",
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
        "employee_count": employee_count(company),
        "plans": list(Plan.objects.filter(is_active=True).order_by("tier")),
        "packs": list(CreditPack.objects.filter(is_active=True).order_by("price")),
        "history": list(company.billing_transactions.all()[:20]),
    }


def BillingTransaction_kind(name: str):
    """Resolve a BillingTransaction.Kind member by attribute name (kept as a
    tiny helper so the lifecycle code above reads cleanly and imports stay lazy)."""
    from .models import BillingTransaction
    return getattr(BillingTransaction.Kind, name)
