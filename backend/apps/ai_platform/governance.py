"""AI Governance (AI_PLATFORM §9) — the locked human-approval boundary, applied
to AI. ONE consistent safety spine across the platform.

  The AI MAY:   suggest · draft · summarise · recommend · predict.
  The AI MAY NOT: approve payments · award contracts · delete business data ·
                  approve compliance · send documents · make any irreversible
                  business decision — without explicit human approval.

Lulama and every agent prepare drafts; a human always presses the button. This
module classifies actions so the orchestrator can *propose* a forbidden action
(flagged for human approval) but never execute it.
"""

# Actions the AI must never perform itself — it may only PROPOSE them.
FORBIDDEN_ACTIONS = {
    "approve_payment",
    "record_payment",
    "award_contract",
    "issue_invoice",
    "send_invoice",
    "send_quote",
    "send_document",
    "send_rfq",
    "issue_purchase_order",
    "approve_compliance",
    "override_compliance",
    "approve_estimate",
    "approve_variation",
    "delete_data",
}


def is_forbidden(action: str) -> bool:
    return action in FORBIDDEN_ACTIONS


def propose(action: str, description: str, **detail) -> dict:
    """Wrap a side-effecting action as a PROPOSAL for human review. Forbidden
    actions always carry requires_approval=True; the executor is the human, via
    the relevant module's own (permission-checked) endpoint — never the AI."""
    return {
        "action": action,
        "description": description,
        "requires_approval": True if is_forbidden(action) else False,
        "executed_by_ai": False,   # invariant: the AI never executes; it proposes
        **detail,
    }
