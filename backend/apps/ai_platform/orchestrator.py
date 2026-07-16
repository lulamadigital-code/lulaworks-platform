"""Lulama — the AI Operations Director (AI_PLATFORM §3).

Users don't juggle seven agents; they talk to Lulama. Lulama doesn't do the work
— it DECOMPOSES a request into agent tasks, DISPATCHES the agents the invoking
user is permitted to run, AGGREGATES their grounded results, and returns ONE
consolidated DRAFT for human review. It never commits the side-effects itself
(governance §9): every side-effecting action is surfaced as a human-approval
proposal, never executed by the AI.

Deterministic-first: agents ground their answers in the real modules for free.
The metered LLM (via the gateway) is an optional narrative enrichment.
"""

from decimal import Decimal

from django.utils import timezone

from apps.core.events import publish

from . import governance
from .agents import AGENTS, AgentResult, _decimalless, agent_required_perm
from .models import AIInteraction, ApprovalStatus, PromptTemplate
from .tools import ToolPermissionError

PROMPT_AGENT = "lulama_orchestrator"


# ── Request decomposition (deterministic keyword routing) ─────────────────────

_ROUTES = [
    (("prepare", "plan", "shutdown", "mobilise", "mobilize", "kick off", "set up project"),
     ["rfq", "procurement", "estimating", "compliance", "project", "commercial"]),
    (("readiness", "compliance", "can this project start", "can we start", "safe to start"),
     ["compliance"]),
    (("supplier", "procure", "buy ", "purchase"), ["procurement"]),
    (("profit", "margin", "money", "forecast", "budget", "losing", "commercial"),
     ["commercial"]),
    (("estimate", "pricing", "quote"), ["estimating"]),
    (("status", "progress", "task", "delay"), ["project"]),
    (("risk", "focus", "portfolio", "which project", "executive", "overview"), ["executive"]),
]

# Side-effect intents Lulama may detect in the request → proposed (never executed).
_INTENTS = [
    (("send the invoice", "send invoice", "invoice the customer"),
     ("send_invoice", "Send the customer invoice")),
    (("send the quote", "send quotation", "send the quotation"),
     ("send_quote", "Send the quotation to the customer")),
    (("pay ", "make payment", "approve payment"),
     ("approve_payment", "Approve/record a payment")),
    (("award", "issue po", "issue the po", "place the order"),
     ("issue_purchase_order", "Issue a purchase order to the supplier")),
    (("override compliance", "bypass compliance"),
     ("override_compliance", "Override the compliance gate")),
]


def decompose(request_text: str) -> list[str]:
    lower = request_text.lower()
    for triggers, agents in _ROUTES:
        if any(t in lower for t in triggers):
            return agents
    return ["executive"]


def _detected_proposals(request_text: str) -> list[dict]:
    lower = request_text.lower()
    proposals = []
    for triggers, (action, desc) in _INTENTS:
        if any(t in lower for t in triggers):
            proposals.append(governance.propose(action, desc))
    return proposals


def _prompt_version() -> str:
    tpl = PromptTemplate.objects.filter(agent=PROMPT_AGENT, is_active=True).first()
    return tpl.version if tpl else "v1"


# ── The orchestrator ──────────────────────────────────────────────────────────

def orchestrate(company, user, request_text, *, project=None, quotation=None) -> AIInteraction:
    """Decompose → dispatch permitted agents → aggregate ONE reviewable draft.

    Security model: an agent runs only if the invoking user holds its required
    permission; skipped agents are recorded (the AI never exceeds the user's RBAC).
    Governance: side-effecting intents are surfaced as human-approval proposals —
    the AI never executes them.
    """
    plan = decompose(request_text)
    results: list[AgentResult] = []
    omitted: list[dict] = []

    for name in plan:
        perm = agent_required_perm(name)
        if perm and not user.has_perm_code(perm):
            omitted.append({"agent": name, "reason": f"requires '{perm}'"})
            continue
        try:
            res = AGENTS[name](company, user, project=project, quotation=quotation)
        except ToolPermissionError as exc:
            omitted.append({"agent": name, "reason": str(exc)})
            continue
        results.append(res)

    proposed_actions = _detected_proposals(request_text)
    for r in results:
        proposed_actions.extend(r.proposed_actions)

    confidences = [r.confidence for r in results]
    overall = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

    consolidated = {
        "plan": plan,
        "headline": " ".join(r.summary for r in results) or "No permitted agent produced output.",
        "agents": [r.to_dict() for r in results],
        "omitted_agents": omitted,           # security-model transparency
        "proposed_actions": proposed_actions,  # governance: AI proposes, human approves
        "requires_human_approval": any(a.get("requires_approval") for a in proposed_actions),
    }

    interaction = AIInteraction.objects.create(
        company=company, request_text=request_text, agent="lulama",
        prompt_version=_prompt_version(), provider="deterministic",
        result=_decimalless(consolidated), confidence=Decimal(str(overall)),
        approval_status=ApprovalStatus.DRAFT,
        entity_type="Project" if project else "", entity_id=project.id if project else None,
        created_by=user, updated_by=user,
    )
    publish("AIDraftPrepared", company=company, subject=interaction, actor=user,
            payload={"request": request_text[:120], "agents": [r.agent for r in results]})
    return interaction


def record_decision(interaction, user, *, approved: bool) -> AIInteraction:
    """A human accepts or rejects the DRAFT. This records the decision only — it
    does NOT execute any side-effect (those run via each module's own
    permission-checked endpoints). Rejected drafts are kept for the prompt
    learning loop (AI_PLATFORM §6, §10)."""
    interaction.approval_status = (
        ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    )
    interaction.decided_by = user
    interaction.decided_at = timezone.now()
    interaction.updated_by = user
    interaction.save(update_fields=["approval_status", "decided_by", "decided_at",
                                    "updated_by", "updated_at"])
    return interaction
