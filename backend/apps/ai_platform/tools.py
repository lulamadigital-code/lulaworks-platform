"""Tool Registry (AI_PLATFORM §7) — least-privilege, tenant-scoped, audited.

THE AGENT SECURITY MODEL (the critical hardening): an AI agent executes strictly
within the invoking user's tenant context and RBAC permissions. Every tool call
is subject to the exact same company_id isolation and permission checks as if the
user made it directly — an agent can never read another tenant's data, and never
do what its user isn't allowed to do. No unrestricted database access. Every tool
call is audited. This closes the biggest risk in agentic systems: the AI as a
back door around security.
"""

from collections.abc import Callable
from dataclasses import dataclass

from apps.administration.services import record_audit


class ToolPermissionError(PermissionError):
    """Raised when the invoking user lacks the permission a tool requires."""


@dataclass
class Tool:
    name: str
    required_perm: str | None
    fn: Callable
    description: str = ""


_REGISTRY: dict[str, Tool] = {}


def register(name, required_perm=None, description=""):
    def deco(fn):
        _REGISTRY[name] = Tool(name=name, required_perm=required_perm, fn=fn,
                               description=description)
        return fn
    return deco


def available_tools(user) -> list[str]:
    """Tools the user is permitted to call (least-privilege view)."""
    return [t.name for t in _REGISTRY.values()
            if t.required_perm is None or user.has_perm_code(t.required_perm)]


def run_tool(name, user, **kwargs):
    """Execute a registered tool as the user. Enforces the user's RBAC (raises
    ToolPermissionError otherwise), relies on the ambient tenant for isolation,
    and audits the call. The tenant context must already be set by the caller
    (request or tenant_scope) — the tool reads only through tenant-scoped managers."""
    tool = _REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool '{name}'.")
    if tool.required_perm and not user.has_perm_code(tool.required_perm):
        record_audit(company=user.active_company, user=user, action="ai.tool_denied",
                     after={"tool": name, "required_perm": tool.required_perm})
        raise ToolPermissionError(
            f"Tool '{name}' requires '{tool.required_perm}', which the user lacks."
        )
    result = tool.fn(user, **kwargs)
    record_audit(company=user.active_company, user=user, action="ai.tool_call",
                 after={"tool": name})
    return result


# ── Registered tools (each reads only through tenant-scoped managers) ─────────

@register("search_suppliers", required_perm="procurement.manage",
          description="Top suppliers by performance score")
def _search_suppliers(user, *, limit=5):
    from apps.procurement.models import Supplier
    rows = Supplier.objects.all().order_by("-performance_score")[:limit]
    return [{"name": s.name, "performance_score": str(s.performance_score),
             "categories": s.categories} for s in rows]


@register("project_readiness", required_perm="projects.view",
          description="Compliance readiness for a project")
def _project_readiness(user, *, project):
    from apps.compliance.services import recompute_readiness
    return recompute_readiness(project)


@register("project_profitability", required_perm="finance.view_money",
          description="Live profitability for a project")
def _project_profitability(user, *, project):
    from apps.finance.services import profitability, rebuild_actuals_from_sources
    rebuild_actuals_from_sources(project, user)
    return profitability(project)


@register("project_profit_forecast", required_perm="finance.view_money",
          description="Project profit predictor")
def _project_profit_forecast(user, *, project):
    from apps.finance.services import profit_forecast, rebuild_actuals_from_sources
    rebuild_actuals_from_sources(project, user)
    return profit_forecast(project)


@register("commercial_dashboard", required_perm="finance.view_money",
          description="Portfolio commercial view")
def _commercial_dashboard(user):
    from apps.finance.services import commercial_dashboard
    return commercial_dashboard(user.active_company)
