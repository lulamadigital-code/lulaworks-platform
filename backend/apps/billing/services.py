"""Entitlement engine with graceful degradation (SAAS_PLATFORM §6).

Every gated action consults `check()`, which returns allow / warn / block —
the app informs + offers an upgrade rather than failing unexpectedly.
"""

from dataclasses import dataclass

WARN_RATIO = 0.9  # warn at 90% of a limit


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
        return EntitlementResult(False, reason=f"User limit ({limit}) reached — upgrade to add more.")
    if current_user_count >= int(limit * WARN_RATIO):
        return EntitlementResult(True, warn=True, reason=f"Approaching user limit ({limit}).")
    return EntitlementResult(True)


def check_module(company, module_key: str) -> EntitlementResult:
    sub = _subscription(company)
    entitled = sub.plan.module_entitlements if sub else []
    if module_key in entitled or not entitled:
        return EntitlementResult(True)
    return EntitlementResult(False, reason=f"'{module_key}' is not in your plan — upgrade to enable.")
