"""Specialised AI agents (AI_PLATFORM §4) — a team of narrow AI employees, one
job each, mapped to their modules. Every agent is GROUNDED in the real modules
(deterministic-first, free, exact) and returns a Confidence-Engine result
(summary + findings + confidence + sources + assumptions + proposed actions).

Each agent declares `required_perm` — an agent never does what its invoking user
isn't allowed to do (the security model, enforced here + at the tool layer).
Agents SUGGEST/DRAFT only; side-effects are returned as human-approval proposals.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal

from . import governance, tools


@dataclass
class AgentResult:
    agent: str
    summary: str
    required_perm: str | None = None
    confidence: float = 0.95
    findings: list = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    proposed_actions: list[dict] = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d.pop("required_perm", None)
        return d


def _money_ok(user) -> bool:
    return user.has_perm_code("finance.view_money")


# ── RFQ / Document agent (Module 5) ───────────────────────────────────────────

def rfq_agent(company, user, *, project=None, quotation=None, **_) -> AgentResult:
    q = quotation or (project.quotation if project else None)
    if q is None:
        return AgentResult("rfq", "No RFQ/quotation in context.", confidence=0.3)
    missing = [f for f, v in (("site", q.site), ("title", q.title)) if not v]
    summary = f"RFQ for {q.client_name}" + (f" — {q.title}" if q.title else "")
    return AgentResult(
        "rfq", summary, required_perm="projects.view", confidence=0.9,
        findings=[{"client": q.client_name, "site": q.site or "(missing)"}],
        sources=["Quotation record"],
        assumptions=["Scope taken from the awarded quotation"],
        proposed_actions=([{"action": "request_missing_info",
                            "description": f"Ask client for: {', '.join(missing)}",
                            "requires_approval": False}] if missing else []),
    )


# ── Procurement agent (Module 6) ──────────────────────────────────────────────

def procurement_agent(company, user, **_) -> AgentResult:
    suppliers = tools.run_tool("search_suppliers", user, limit=3)
    findings = suppliers
    summary = (f"Top supplier by track record: {suppliers[0]['name']}"
               if suppliers else "No suppliers on record yet.")
    return AgentResult(
        "procurement", summary, required_perm="procurement.manage", confidence=0.85,
        findings=findings, sources=["Supplier performance ledger"],
        assumptions=["Ranked by weighted performance score"],
        proposed_actions=([governance.propose(
            "send_rfq", f"Send an RFQ to {suppliers[0]['name']}",
            supplier=suppliers[0]["name"])] if suppliers else []),
    )


# ── Estimating agent (Module 7) ───────────────────────────────────────────────

def estimating_agent(company, user, *, project=None, **_) -> AgentResult:
    from apps.estimating.models import Estimate, EstimateStatus
    from apps.estimating.services import calibration_advice
    q_id = project.quotation_id if project else None
    est = (Estimate.objects.filter(quotation_id=q_id, status=EstimateStatus.APPROVED)
           .order_by("-version").first()) if q_id else None
    if est is None:
        return AgentResult("estimating", "No approved estimate to analyse.",
                           required_perm="projects.view", confidence=0.4)
    advice = calibration_advice(company, est.work_type)
    summary = "Estimate is approved."
    findings = []
    if _money_ok(user):
        findings.append({"margin_pct": str(est.margin_pct), "risk_score": str(est.risk_score)})
        summary = f"Approved estimate at {est.margin_pct}% margin (risk {est.risk_score})."
    return AgentResult(
        "estimating", summary, required_perm="projects.view", confidence=0.85,
        findings=findings + [{"advice": a} for a in advice],
        sources=["Approved estimate", "Historical estimate-vs-actual variance"],
        assumptions=["Calibration drawn from prior projects of this work type"],
    )


# ── Compliance agent (Module 8) ───────────────────────────────────────────────

def compliance_agent(company, user, *, project=None, **_) -> AgentResult:
    if project is None:
        return AgentResult("compliance", "No project in context.", confidence=0.3)
    readiness = tools.run_tool("project_readiness", user, project=project)
    gate = readiness["gate_status"]
    blocking = readiness["blocking"]
    summary = (f"Project is compliance-ready ({readiness['overall']}%)."
               if gate in ("ready", "overridden")
               else f"NOT ready ({readiness['overall']}%) — "
                    f"{len(blocking)} mandatory item(s) open.")
    proposed = [{"action": "obtain_compliance_item",
                 "description": f"Obtain/approve: {b['name']} ({b['source']})",
                 "requires_approval": False} for b in blocking]
    return AgentResult(
        "compliance", summary, required_perm="projects.view", confidence=0.97,
        findings=blocking, sources=["Compliance readiness gate"],
        assumptions=["Only mandatory items block the gate"],
        proposed_actions=proposed,
    )


# ── Project Manager agent (Module 9) ──────────────────────────────────────────

def project_agent(company, user, *, project=None, **_) -> AgentResult:
    if project is None:
        return AgentResult("project", "No project in context.", confidence=0.3)
    from apps.execution.models import TaskStatus
    from apps.execution.services import recompute_project_progress
    tasks = list(project.tasks.all())
    blocked = [t for t in tasks if t.status == TaskStatus.BLOCKED]
    progress = recompute_project_progress(project)
    summary = f"{progress}% complete · {len(tasks)} task(s), {len(blocked)} blocked."
    return AgentResult(
        "project", summary, required_perm="projects.view", confidence=0.9,
        findings=[{"task": t.name, "blocked_reason": t.blocked_reason} for t in blocked],
        sources=["Execution task engine"],
        assumptions=["Progress = mean task progress"],
        proposed_actions=[{"action": "expedite", "description": f"Unblock: {t.name}",
                           "requires_approval": False} for t in blocked],
    )


# ── Commercial agent (Module 10) ──────────────────────────────────────────────

def commercial_agent(company, user, *, project=None, **_) -> AgentResult:
    if project is None:
        return AgentResult("commercial", "No project in context.",
                           required_perm="finance.view_money", confidence=0.3)
    prof = tools.run_tool("project_profitability", user, project=project)
    forecast = tools.run_tool("project_profit_forecast", user, project=project)
    return AgentResult(
        "commercial", forecast["narrative"], required_perm="finance.view_money",
        confidence=0.8,
        findings=[{"margin_pct": prof["margin_pct"], "gross_profit": prof["gross_profit"],
                   "verdict": forecast["verdict"], "contributors": forecast["contributors"]}],
        sources=["Live profitability", "Project profit predictor"],
        assumptions=["Forecast extrapolates current cost trend to completion"],
    )


# ── Executive agent (cross-module) ────────────────────────────────────────────

def executive_agent(company, user, **_) -> AgentResult:
    from apps.compliance.services import recompute_readiness
    from apps.projects.models import Project
    at_risk = []
    for p in Project.objects.all():
        r = recompute_readiness(p)
        if r["gate_status"] == "not_ready":
            at_risk.append({"project": p.number, "readiness": f"{r['overall']}%"})
    findings = [{"compliance_at_risk": at_risk}]
    summary = f"{len(at_risk)} project(s) blocked on compliance."
    if _money_ok(user):
        dash = tools.run_tool("commercial_dashboard", user)
        findings.append({"loss_making": dash["loss_making_projects"],
                         "portfolio_margin": dash["margin_pct"]})
        summary += (f" Portfolio margin {dash['margin_pct']}%, "
                    f"{len(dash['loss_making_projects'])} loss-making.")
    return AgentResult(
        "executive", summary, required_perm="projects.view", confidence=0.9,
        findings=findings, sources=["Compliance gates", "Commercial dashboard"],
        assumptions=["'At risk' = compliance gate not_ready"],
    )


AGENTS = {
    "rfq": rfq_agent, "procurement": procurement_agent, "estimating": estimating_agent,
    "compliance": compliance_agent, "project": project_agent,
    "commercial": commercial_agent, "executive": executive_agent,
}


def agent_required_perm(name) -> str | None:
    return {
        "rfq": "projects.view", "procurement": "procurement.manage",
        "estimating": "projects.view", "compliance": "projects.view",
        "project": "projects.view", "commercial": "finance.view_money",
        "executive": "projects.view",
    }.get(name)


def _decimalless(value):
    """JSON-safe: coerce any stray Decimals from grounded reads to strings."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _decimalless(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimalless(v) for v in value]
    return value
