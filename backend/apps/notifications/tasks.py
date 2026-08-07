"""Background delivery — the web request never waits on SMTP.

send_email() records the email and queues this; the worker delivers it and
retries on failure with exponential backoff. Failures stay in the log (status
FAILED) for an admin to inspect and resend.
"""

from celery import shared_task

from .service import MAX_ATTEMPTS, deliver_now


@shared_task(bind=True, max_retries=MAX_ATTEMPTS - 1, default_retry_delay=60)
def deliver_email(self, log_id):
    """Deliver one queued EmailLog. Retries (60s, then backing off) up to the
    budget, then leaves the row FAILED for manual resend."""
    try:
        deliver_now(log_id)
    except Exception as exc:  # noqa: BLE001 - deliver_now already logged it FAILED
        # Exponential-ish backoff: 60s, 120s, 240s…
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
