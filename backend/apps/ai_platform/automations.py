"""AI Automation Engine (AI OS §15) — user-configurable "when X, do Y" rules.

Each automation watches a trigger (a live prediction signal) and applies an
action when it fires. Safe actions (notify the user) run immediately; anything
that would reach outside the company — sending a follow-up, say — is only ever
*proposed* for human approval, never executed by the automation. This keeps the
brief's rule: high-risk actions require a human.
"""
from __future__ import annotations

from django.utils import timezone

from .governance import propose
from .models import Automation, AutomationRun
from .predictions import (predict_job_delays, predict_price_rises,
                          predict_repeat_customers)

# Trigger → the predictor whose hits fire the rule.
_TRIGGER_SIGNAL = {
    Automation.Trigger.PRICE_RISE: predict_price_rises,
    Automation.Trigger.CUSTOMER_DUE: predict_repeat_customers,
    Automation.Trigger.JOB_AT_RISK: predict_job_delays,
}


def run_automation(automation: Automation, user) -> AutomationRun:
    """Evaluate one automation against current signals and apply its action.
    Records and returns an AutomationRun. Never raises on an action failure."""
    company = automation.company
    signal = _TRIGGER_SIGNAL.get(automation.trigger)
    try:
        # Predictors return Prediction dataclasses; _apply_action reads dicts.
        hits = [p.as_dict() for p in (signal(company) if signal else [])]
    except Exception:                                # noqa: BLE001
        hits = []

    result = AutomationRun.Result.NOTHING
    handled = []
    for pred in hits:
        try:
            outcome = _apply_action(automation, user, pred)
            handled.append(outcome)
        except Exception:                            # noqa: BLE001
            continue

    if handled:
        # Awaiting-approval wins the summary — it needs a human next.
        if any(h.get("awaiting_approval") for h in handled):
            result = AutomationRun.Result.AWAITING_APPROVAL
        else:
            result = AutomationRun.Result.DONE

    run = AutomationRun.objects.create(
        company=company, automation=automation, result=result,
        matches=len(handled), detail={"items": handled}, created_by=user)
    automation.run_count += 1
    automation.last_run = timezone.now()
    automation.save(update_fields=["run_count", "last_run", "updated_at"])
    return run


def _apply_action(automation: Automation, user, pred) -> dict:
    statement = pred.get("statement", "")
    if automation.action == Automation.Action.NOTIFY_ME:
        _notify(automation.company, user, title=automation.name, body=statement)
        return {"subject": pred.get("subject"), "action": "notified",
                "statement": statement}
    if automation.action == Automation.Action.PROPOSE_FOLLOWUP:
        # High-risk (reaches a customer/supplier) — PROPOSE, never send.
        proposal = propose("send_followup", statement,
                           subject=pred.get("subject"), source=pred.get("source"))
        return {"subject": pred.get("subject"), "action": "proposed",
                "awaiting_approval": True, "statement": statement,
                "proposal": proposal}
    return {"subject": pred.get("subject"), "action": "none", "statement": statement}


def _notify(company, user, *, title, body):
    try:
        from apps.notifications.dispatch import notify
        notify(company, user, title=title, body=body, email=False)
    except Exception:                                # noqa: BLE001
        pass


def run_all(company, user) -> list[AutomationRun]:
    """Run every enabled automation for the tenant (a manual 'run all')."""
    runs = []
    for a in Automation.objects.filter(enabled=True):
        runs.append(run_automation(a, user))
    return runs


def run_all_tenants() -> dict:
    """Scheduled sweep across every tenant: run each enabled automation as the
    person who created it (so 'notify me' reaches the right inbox and permission
    checks are theirs). Cross-tenant, but each run is scoped to its own company.
    Resilient — one failing automation never stops the rest."""
    from apps.core.context import tenant_scope

    autos = list(Automation.all_objects.filter(enabled=True)
                 .select_related("company", "created_by"))
    ran, matched = 0, 0
    for a in autos:
        if not a.created_by_id:
            continue                        # no one to act as / notify
        try:
            with tenant_scope(a.company_id):
                run = run_automation(a, a.created_by)
            ran += 1
            matched += run.matches
        except Exception:                    # noqa: BLE001
            continue
    return {"automations_run": ran, "items_handled": matched}
