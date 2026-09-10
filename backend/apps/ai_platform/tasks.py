"""Background jobs for the AI platform — the daily automation sweep (§15)."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def run_scheduled_automations():
    """Daily (Celery beat): fire every enabled automation across all tenants,
    each as its creator and within its own company scope. Safe actions run;
    high-risk actions are proposed for approval — never sent automatically."""
    from apps.ai_platform.automations import run_all_tenants
    try:
        return run_all_tenants()
    except Exception as exc:                          # noqa: BLE001
        logger.warning("Scheduled automations failed: %s", exc)
        return {"automations_run": 0, "items_handled": 0}
