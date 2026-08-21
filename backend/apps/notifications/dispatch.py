"""The Notification Service — one call, the right channels.

A module doesn't decide "write an in-app notification AND maybe an email". It
raises a notification for a user and this dispatches it to the channels the user
allows: in-app always, email when the user has login access and hasn't opted
out. SMS / WhatsApp / push are future channels that plug in here — the same seam,
so modules never change when a channel is added.
"""

import logging

from .models import EmailCategory
from .service import send_email

logger = logging.getLogger(__name__)


def _email_allowed(user, category) -> bool:
    """Whether this user should get an email for this category. Employees without
    login access are never emailed system notifications; users can opt out via
    their preferences."""
    if not user or not getattr(user, "email", ""):
        return False
    if not getattr(user, "is_active", True):
        return False
    try:
        prefs = user.notification_prefs
    except Exception:
        prefs = None
    if prefs is None:
        return True                              # default on until they choose
    if not prefs.email:
        return False
    # Category-specific opt-outs (marketing/billing get their own switch later;
    # for now email master-switch governs all transactional categories).
    return True


def notify(company, user, *, title, body="", url="", category=EmailCategory.SYSTEM,
           email_template="generic", email_subject=None, email_context=None,
           email=True, task=None, verb=""):
    """Raise a notification for `user`: always in-app, plus email when allowed.

    Returns {"notification": <Notification|None>, "email": <EmailLog|None>}.
    Never raises on a delivery problem — a notification failing must not break
    the business action that triggered it.
    """
    result = {"notification": None, "email": None}

    # In-app — reuse the execution notification store (the canonical inbox). The
    # store is tenant-scoped, so bind the company explicitly: this must work from
    # a Celery task or a signal handler, not only inside a request.
    try:
        from apps.core.context import tenant_scope
        from apps.execution.services import notify as inapp_notify
        if company is not None:
            with tenant_scope(company.id):
                result["notification"] = inapp_notify(
                    user, task=task, verb=verb or category, title=title,
                    body=body, url=url)
        else:
            result["notification"] = inapp_notify(
                user, task=task, verb=verb or category, title=title, body=body, url=url)
    except Exception as exc:  # noqa: BLE001 - resilient: email can still go
        logger.warning("In-app notification failed: %s", exc)

    # Email — when the channel is on for this user + category.
    if email and _email_allowed(user, category):
        ctx = {"heading": title, "body": body}
        if url:
            ctx.update({"cta_url": url, "cta_label": "Open in Lulaworks"})
        ctx.update(email_context or {})
        try:
            result["email"] = send_email(
                to=user.email, subject=email_subject or title,
                template=email_template, context=ctx, company=company,
                to_name=(user.get_full_name() or "").strip(), category=category)
        except Exception as exc:  # noqa: BLE001 - never break the caller
            logger.warning("Notification email failed: %s", exc)

    return result
