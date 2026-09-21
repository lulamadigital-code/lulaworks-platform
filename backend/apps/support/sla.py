"""Support SLA policy engine — turns a tenant's plan into concrete first-response
and resolution targets, and ranks open tickets for the triage board so the
tightest commitments surface first.

The service tier comes from the plan's ``support_level`` (Enterprise's
``dedicated`` is the top tier and pairs with the ``dedicated_support``
entitlement). Every plan gets *some* SLA — Enterprise simply gets the tightest
targets and priority routing. Targets are calendar-hours from ticket creation,
kept as plain data so they are easy to reason about and test.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as _dt_timezone

from django.utils import timezone

from .models import OPEN_STATUSES

# Service tiers, worst → best. "standard"/"email" are the entry tier; the plan's
# raw support_level maps onto one of these.
TIERS = ["email", "priority", "highest", "dedicated"]

_TIER_ALIASES = {"": "email", "standard": "email", "basic": "email"}

TIER_LABELS = {
    "email": "Standard",
    "priority": "Priority",
    "highest": "Business",
    "dedicated": "Dedicated (Enterprise)",
}

# First-response targets in hours, by tier × ticket priority.
RESPONSE_HOURS = {
    "email":     {"urgent": 8, "high": 24, "normal": 48, "low": 72},
    "priority":  {"urgent": 4, "high": 8,  "normal": 24, "low": 48},
    "highest":   {"urgent": 2, "high": 4,  "normal": 8,  "low": 24},
    "dedicated": {"urgent": 1, "high": 2,  "normal": 4,  "low": 8},
}

# Resolution targets = response target × this tier factor (a fuller-service tier
# both responds and resolves faster).
_RESOLUTION_FACTOR = {"email": 6, "priority": 5, "highest": 4, "dedicated": 3}

_FAR_FUTURE = datetime.max.replace(tzinfo=_dt_timezone.utc)


def normalize_tier(level) -> str:
    """Map a plan's raw support_level onto a known tier (fail-safe to entry)."""
    level = (level or "").strip().lower()
    level = _TIER_ALIASES.get(level, level)
    return level if level in RESPONSE_HOURS else "email"


def tier_label(tier: str) -> str:
    return TIER_LABELS.get(normalize_tier(tier), TIER_LABELS["email"])


def company_tier(company) -> str:
    """A company's support tier, from its subscription plan. Fail-safe to entry."""
    sub = getattr(company, "subscription", None)
    plan = getattr(sub, "plan", None)
    return normalize_tier(getattr(plan, "support_level", None))


def tier_map(companies) -> dict:
    """{company_id: tier} for a set of companies in ONE query — avoids an N+1 on
    the triage board. `companies` is any iterable of Company objects or ids."""
    from apps.billing.models import Subscription
    ids = {getattr(c, "id", c) for c in companies if c is not None}
    if not ids:
        return {}
    rows = (Subscription.all_objects.filter(company_id__in=ids)
            .select_related("plan").values_list("company_id", "plan__support_level"))
    return {cid: normalize_tier(level) for cid, level in rows}


def response_target_hours(tier: str, priority: str) -> int:
    return RESPONSE_HOURS[normalize_tier(tier)].get(priority or "normal", 24)


def resolution_target_hours(tier: str, priority: str) -> int:
    tier = normalize_tier(tier)
    return response_target_hours(tier, priority) * _RESOLUTION_FACTOR[tier]


@dataclass
class SLASnapshot:
    tier: str
    tier_label: str
    priority: str
    is_open: bool
    # first response
    response_target_hours: int
    response_due_at: object
    response_met: bool          # a first response was recorded at all
    response_on_time: bool      # …and within target
    response_breached: bool     # still open, no response yet, past target
    # resolution
    resolution_target_hours: int
    resolution_due_at: object
    resolved: bool
    resolution_on_time: bool
    resolution_breached: bool   # still open, past resolution target
    # convenience for UI/sorting
    breached: bool              # response OR resolution breach, and still open
    hours_to_response_due: float  # negative = overdue
    urgency: float              # smaller = more urgent (overdue first)

    @property
    def badge(self) -> str:
        if not self.is_open:
            return "met" if self.response_on_time else "closed"
        if self.breached:
            return "breached"
        if self.hours_to_response_due <= 2:
            return "due-soon"
        return "on-track"


def snapshot(ticket, tier=None, now=None) -> SLASnapshot:
    """Compute the live SLA position of one ticket. Pass a precomputed `tier`
    (from `tier_map`) on list pages to avoid a per-ticket query."""
    now = now or timezone.now()
    if tier is None:
        tier = company_tier(getattr(ticket, "company", None))
    tier = normalize_tier(tier)
    priority = ticket.priority or "normal"
    is_open = ticket.status in OPEN_STATUSES
    created = ticket.created_at

    r_hours = response_target_hours(tier, priority)
    r_due = created + timedelta(hours=r_hours)
    response_met = ticket.first_response_at is not None
    response_on_time = response_met and ticket.first_response_at <= r_due
    response_breached = is_open and not response_met and now > r_due

    x_hours = resolution_target_hours(tier, priority)
    x_due = created + timedelta(hours=x_hours)
    resolved = ticket.resolved_at is not None
    resolution_on_time = resolved and ticket.resolved_at <= x_due
    resolution_breached = is_open and not resolved and now > x_due

    breached = is_open and (response_breached or resolution_breached)
    hours_to_due = (r_due - now).total_seconds() / 3600.0
    # Sort key: closed tickets sink; among open, breached & soonest-due first.
    urgency = hours_to_due if is_open else 1e9

    return SLASnapshot(
        tier=tier, tier_label=tier_label(tier), priority=priority, is_open=is_open,
        response_target_hours=r_hours, response_due_at=r_due,
        response_met=response_met, response_on_time=response_on_time,
        response_breached=response_breached,
        resolution_target_hours=x_hours, resolution_due_at=x_due, resolved=resolved,
        resolution_on_time=resolution_on_time, resolution_breached=resolution_breached,
        breached=breached, hours_to_response_due=round(hours_to_due, 1), urgency=urgency,
    )


def route_key(ticket, snap: SLASnapshot):
    """Ordering key for the triage board (ascending = most urgent first):
    open before closed, breached before not, then soonest response-due, with the
    Dedicated tier edging ahead on ties."""
    tier_rank = TIERS.index(snap.tier)          # dedicated = 3 (highest)
    due = snap.response_due_at or _FAR_FUTURE
    return (not snap.is_open, not snap.breached, due, -tier_rank)
