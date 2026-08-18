"""SMS channel — provider-abstracted, opt-in, for time-critical field alerts.

Same philosophy as the email and AI layers: no module talks to a vendor SDK; it
calls send_sms(), which logs the message and hands delivery to the worker. The
provider is chosen by settings.SMS_PROVIDER — Twilio to start, swappable to a
South-African local provider (Clickatell / BulkSMS / SMSPortal — cheaper for ZA
numbers) by adding an adapter and changing one env var, no module change.

SMS is deliberately narrow: short operational alerts to field staff who won't
read email, and OPT-IN per user (NotificationPreference.sms, default off),
because every message costs money.
"""

import logging
import time

from django.conf import settings

from .models import EmailStatus, SmsLog

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


class SmsError(RuntimeError):
    pass


class NotConfiguredError(SmsError):
    """Raised when SMS is requested but no provider/credentials are set."""


# ── Providers ─────────────────────────────────────────────────────────────────

class SmsProvider:
    name = "base"

    def send(self, to: str, body: str) -> str:  # pragma: no cover - interface
        """Send one SMS; return the provider message id."""
        raise NotImplementedError


class TwilioProvider(SmsProvider):
    name = "twilio"

    def send(self, to, body):
        sid = settings.TWILIO_ACCOUNT_SID
        token = settings.TWILIO_AUTH_TOKEN
        # A `whatsapp:`-prefixed recipient routes over WhatsApp using the WhatsApp
        # sender; otherwise it's a normal SMS. Same Twilio account either way.
        is_whatsapp = (to or "").startswith("whatsapp:")
        if is_whatsapp:
            wa = getattr(settings, "TWILIO_WHATSAPP_FROM", "")
            sender = wa if wa.startswith("whatsapp:") else f"whatsapp:{wa}"
            if not wa:
                raise NotConfiguredError("TWILIO_WHATSAPP_FROM is not set.")
        else:
            sender = settings.TWILIO_FROM_NUMBER
        if not (sid and token and sender):
            raise NotConfiguredError("Twilio credentials are not set.")
        try:
            from twilio.rest import Client  # lazy — no hard dependency
        except ImportError as exc:  # pragma: no cover - optional extra
            raise NotConfiguredError("twilio SDK not installed (pip install twilio).") from exc
        client = Client(sid, token)
        msg = client.messages.create(to=to, from_=sender, body=body)
        return getattr(msg, "sid", "") or ""


_PROVIDERS = {"twilio": TwilioProvider}


def sms_configured() -> bool:
    """True when a provider is selected and its credentials are present."""
    name = getattr(settings, "SMS_PROVIDER", "")
    if name == "twilio":
        return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN
                    and settings.TWILIO_FROM_NUMBER)
    return False


def whatsapp_configured() -> bool:
    """True when Twilio is set up with a WhatsApp sender."""
    return bool(getattr(settings, "SMS_PROVIDER", "") == "twilio"
                and settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN
                and getattr(settings, "TWILIO_WHATSAPP_FROM", ""))


def get_sms_provider() -> SmsProvider:
    name = getattr(settings, "SMS_PROVIDER", "")
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise NotConfiguredError(f"No SMS provider configured (SMS_PROVIDER='{name}').")
    return cls()


# ── Opt-in gate ───────────────────────────────────────────────────────────────

def sms_allowed(user) -> bool:
    """SMS is OPT-IN: the user must have a mobile number, be active, and have
    switched SMS on (default off — it costs per message)."""
    if not user or not getattr(user, "mobile", "") or not getattr(user, "is_active", True):
        return False
    try:
        prefs = user.notification_prefs
    except Exception:
        return False                 # no prefs row → SMS stays off (opt-in)
    return bool(prefs and prefs.sms)


# ── Service ───────────────────────────────────────────────────────────────────

def send_sms(*, to, body, company=None, category=None, sent_by=None, related=None,
             channel="sms", now=False) -> SmsLog:
    """Queue an SMS (or WhatsApp with channel='whatsapp') and record it. Returns
    the SmsLog. `now=True` sends inline (tests / commands); otherwise the Celery
    worker delivers it."""
    from .models import EmailCategory
    dest = (to or "").strip()
    if channel == "whatsapp" and dest and not dest.startswith("whatsapp:"):
        dest = f"whatsapp:{dest}"
    log = SmsLog.objects.create(
        company=company, to_number=dest, body=body[:480],
        category=category or EmailCategory.TASK,
        entity_type=related.__class__.__name__ if related is not None else "",
        entity_id=getattr(related, "id", None) if related is not None else None,
        sent_by=sent_by, status=EmailStatus.QUEUED,
    )
    if now or getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        deliver_now(log.id)
    else:
        from .tasks import deliver_sms
        try:
            deliver_sms.delay(str(log.id))
        except Exception:            # broker down → send inline
            deliver_now(log.id)
    log.refresh_from_db()
    return log


def deliver_now(log_id) -> SmsLog:
    """Send one queued SMS through the configured provider and update its log."""
    from django.utils import timezone as djtz
    log = SmsLog.objects.get(id=log_id)
    if log.status == EmailStatus.SENT:
        return log
    log.status = EmailStatus.SENDING
    log.attempts += 1
    log.save(update_fields=["status", "attempts", "updated_at"])
    try:
        provider = get_sms_provider()
        message_id = provider.send(log.to_number, log.body)
    except Exception as exc:  # noqa: BLE001 - recorded, retried by the task
        log.error = str(exc)[:1000]
        log.status = EmailStatus.FAILED
        log.provider = getattr(settings, "SMS_PROVIDER", "")
        log.save(update_fields=["error", "status", "provider", "updated_at"])
        raise
    log.status = EmailStatus.SENT
    log.sent_at = djtz.now()
    log.error = ""
    log.provider = getattr(settings, "SMS_PROVIDER", "")
    log.provider_message_id = message_id or ""
    log.save(update_fields=["status", "sent_at", "error", "provider",
                            "provider_message_id", "updated_at"])
    return log
