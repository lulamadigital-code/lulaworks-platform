"""The single entitlement engine — what a company can actually USE.

Resolution: Plan.module_entitlements (+ inherited, they're built cumulatively in
seed_platform) → Subscription.overrides (numeric limits only) → effective access.
Enterprise feature codes come from the plan too; overrides never grant a feature.

Everything asks this facade — `entitlements_for(company).has("ai_extraction")` or
`.limit("users")` — instead of scattering `if plan == …` checks. The backend is
authoritative; the UI reads the same `.capabilities()` map so it can never show a
feature the backend will reject.
"""
from decimal import Decimal

# capability code → (human label, first plan that includes it). The label/plan is
# for display + upgrade prompts; the source of truth for WHO HAS it is the plan's
# module_entitlements (seeded), never this table.
CAPABILITIES = [
    ("basic_procurement", "Basic procurement", "Starter"),
    ("basic_dashboard", "Basic dashboard", "Starter"),
    ("pdf_export", "PDF export", "Starter"),
    ("excel_export", "Excel export", "Starter"),
    ("ai_extraction", "AI document extraction", "Professional"),
    ("supplier_intelligence", "Supplier intelligence", "Professional"),
    ("price_history", "Product price history", "Professional"),
    ("gps_checkin", "GPS check-ins", "Professional"),
    ("time_tracking", "Time tracking", "Professional"),
    ("team_management", "Team management", "Professional"),
    ("advanced_dashboard", "Advanced dashboards", "Professional"),
    ("approval_workflows", "Advanced approvals", "Business"),
    ("compliance_management", "Compliance management", "Business"),
    ("procurement_analytics", "Procurement analytics", "Business"),
    ("advanced_reporting", "Advanced reporting", "Business"),
    ("multi_team", "Multi-team management", "Business"),
    ("sso", "Single sign-on (SSO)", "Enterprise"),
    ("api_access", "API access", "Enterprise"),
    ("audit_log", "Audit log", "Enterprise"),
    ("dedicated_support", "Dedicated support & SLA", "Enterprise"),
]
_LABELS = {code: (label, plan) for code, label, plan in CAPABILITIES}

# limit alias → (override key, plan attribute)
_LIMIT_KEYS = {
    "users": ("max_users", "max_users"),
    "users.max": ("max_users", "max_users"),
    "storage.gb": ("storage_quota_bytes", "storage_quota_bytes"),
    "ai.credits.monthly": ("monthly_ai_credits", "monthly_ai_credits"),
}


class PlanFeatureRequired(Exception):
    """Raised when a company tries to use a capability its plan doesn't include."""

    def __init__(self, code, message=""):
        self.code = code
        self.label, self.required_plan = _LABELS.get(code, (code, ""))
        self.message = message or (
            f"{self.label} is available on {self.required_plan} and above."
            if self.required_plan else f"Your plan doesn't include {self.label}.")
        super().__init__(self.message)


class Entitlements:
    def __init__(self, company):
        self.company = company

    def has(self, code) -> bool:
        from .services import has_feature
        return has_feature(self.company, code)

    def require(self, code):
        if not self.has(code):
            raise PlanFeatureRequired(code)

    def limit(self, name):
        """Effective numeric limit (honours overrides). `storage.gb` in GB, others raw."""
        sub = getattr(self.company, "subscription", None)
        key, attr = _LIMIT_KEYS.get(name, (name, name))
        plan = getattr(sub, "plan", None)
        default = getattr(plan, attr, None) if plan else getattr(self.company, attr, None)
        val = int(sub.limit(key, default)) if sub and default is not None else default
        if name == "storage.gb" and val is not None:
            return round(val / (1024 ** 3), 1)
        return val

    def remaining(self, name):
        if name in ("ai.credits.monthly", "ai_credits"):
            from apps.ai_platform.gateway import credit_balance
            return float(credit_balance(self.company) or Decimal("0"))
        if name in ("storage.gb", "storage"):
            used = (getattr(self.company, "storage_used_bytes", 0) or 0) / (1024 ** 3)
            return round(max((self.limit("storage.gb") or 0) - used, 0), 1)
        if name in ("users", "users.max"):
            from .services import active_user_count
            return max((self.limit("users") or 0) - active_user_count(self.company), 0)
        return None

    def capabilities(self) -> list:
        """The full ✓/✕ list for admin + customer display, in tier order."""
        return [{"code": c, "label": lbl, "plan": pl, "enabled": self.has(c)}
                for c, lbl, pl in CAPABILITIES]


def entitlements_for(company) -> Entitlements:
    return Entitlements(company)


def require_feature(code):
    """View decorator: block a feature the plan doesn't include with a friendly
    upgrade message instead of a bare 403."""
    from functools import wraps
    from django.contrib import messages
    from django.shortcuts import redirect

    def deco(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            company = getattr(request.user, "active_company", None)
            if company is not None and not entitlements_for(company).has(code):
                exc = PlanFeatureRequired(code)
                messages.warning(request, f"{exc.message} Upgrade under Billing to unlock it.")
                return redirect("web:billing")
            return view(request, *args, **kwargs)
        return _wrapped
    return deco
