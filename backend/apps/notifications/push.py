"""Push channel — provider-abstracted, honestly gated.

Same philosophy as the email/SMS/AI layers: no module talks to a vendor SDK; it
calls send_push(), which fans out to the user's registered devices via the
configured provider. The provider is chosen by settings.PUSH_PROVIDER (FCM to
start). Push is governed per user by NotificationPreference.push (default on).

HONEST GATING (AI-OS §3 / launch-readiness): if no provider credentials are
configured, send_push does NOT pretend to deliver — it logs and returns a
skipped result, and callers carry on. Nothing fakes a "sent" push. Delivery
turns on the moment FCM credentials are present in the environment; no code
change, mirroring how email/SMS providers plug in.

Device tokens are registered by the client (see apps.identity device endpoints)
and stored in PushDevice.
"""
import logging

from django.conf import settings
from django.utils import timezone

from .models import PushDevice

logger = logging.getLogger(__name__)


class PushError(RuntimeError):
    pass


class NotConfiguredError(PushError):
    """Raised/handled when push is requested but no provider credentials exist."""


# ── Device registration ────────────────────────────────────────────────────────

def register_device(user, *, token, platform="android"):
    """Upsert a device token for `user`. A token is globally unique and may move
    between users (a shared/reflashed device), so we re-point it rather than
    duplicate. Returns the PushDevice."""
    token = (token or "").strip()
    if not token:
        raise PushError("A device token is required.")
    if platform not in dict(PushDevice.Platform.choices):
        platform = PushDevice.Platform.ANDROID
    company = getattr(user, "active_company", None)
    device, _created = PushDevice.all_objects.update_or_create(
        token=token,
        defaults={"user": user, "platform": platform, "active": True,
                  "company": company, "last_seen": timezone.now(),
                  "is_deleted": False},
    )
    return device


def unregister_device(user, *, token):
    """Deactivate a token on logout/uninstall. Scoped to the owner so one user
    can't disable another's device. Idempotent."""
    PushDevice.objects.filter(user=user, token=(token or "").strip()).update(
        active=False)


# ── Providers ──────────────────────────────────────────────────────────────────

class PushProvider:
    name = "base"

    def send(self, tokens, *, title, body, data):  # pragma: no cover - interface
        """Send to many tokens; return (sent_count, stale_tokens)."""
        raise NotImplementedError


class FCMProvider(PushProvider):
    """Firebase Cloud Messaging HTTP v1. Needs a service-account credential,
    supplied via the environment (never in code or the client):
      * settings.FCM_PROJECT_ID
      * settings.FCM_CREDENTIALS_FILE  (path to the service-account JSON)
    Lazy-imports google-auth so the dependency is only needed once push is
    actually switched on."""

    name = "fcm"
    _SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

    def _access_token(self):
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2 import service_account  # type: ignore
        creds = service_account.Credentials.from_service_account_file(
            settings.FCM_CREDENTIALS_FILE, scopes=self._SCOPES)
        creds.refresh(Request())
        return creds.token

    def send(self, tokens, *, title, body, data):
        import requests
        project = getattr(settings, "FCM_PROJECT_ID", "")
        cred_file = getattr(settings, "FCM_CREDENTIALS_FILE", "")
        if not (project and cred_file):
            raise NotConfiguredError("FCM_PROJECT_ID / FCM_CREDENTIALS_FILE not set.")
        token_header = self._access_token()
        url = f"https://fcm.googleapis.com/v1/projects/{project}/messages:send"
        headers = {"Authorization": f"Bearer {token_header}",
                   "Content-Type": "application/json"}
        sent, stale = 0, []
        # Stringify data — FCM data payloads must be string→string.
        str_data = {k: str(v) for k, v in (data or {}).items()}
        for tok in tokens:
            payload = {"message": {
                "token": tok,
                "notification": {"title": title, "body": body},
                "data": str_data,
            }}
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=10)
                if r.status_code == 200:
                    sent += 1
                elif r.status_code in (404, 410):
                    stale.append(tok)  # UNREGISTERED — token is dead
                else:
                    logger.warning("FCM send failed (%s): %s", r.status_code, r.text[:200])
            except Exception as exc:  # noqa: BLE001 - one bad token mustn't stop the rest
                logger.warning("FCM send error: %s", exc)
        return sent, stale


def _provider():
    name = getattr(settings, "PUSH_PROVIDER", "fcm")
    if name == "fcm":
        return FCMProvider()
    return None


def is_configured() -> bool:
    """Whether push can actually be delivered right now."""
    return bool(getattr(settings, "FCM_PROJECT_ID", "")
                and getattr(settings, "FCM_CREDENTIALS_FILE", ""))


# ── The one call modules use ────────────────────────────────────────────────────

def send_push(user, *, title, body="", url="", data=None) -> dict:
    """Push a notification to all of `user`'s active devices. Never raises — a
    push problem must not break the business action. Returns
    {"sent": n, "skipped": bool, "reason": str}."""
    result = {"sent": 0, "skipped": False, "reason": ""}
    if not user:
        return {**result, "skipped": True, "reason": "no_user"}

    tokens = list(PushDevice.objects.filter(user=user, active=True)
                  .values_list("token", flat=True))
    if not tokens:
        return {**result, "skipped": True, "reason": "no_devices"}

    provider = _provider()
    if provider is None or not is_configured():
        # Honest: we have devices but no way to deliver. Do not fake it.
        logger.info("Push skipped (provider not configured) for user=%s", user.pk)
        return {**result, "skipped": True, "reason": "not_configured"}

    payload = dict(data or {})
    if url:
        payload["url"] = url
    try:
        sent, stale = provider.send(tokens, title=title, body=body, data=payload)
        if stale:
            PushDevice.objects.filter(token__in=stale).update(active=False)
        result["sent"] = sent
    except NotConfiguredError:
        return {**result, "skipped": True, "reason": "not_configured"}
    except Exception as exc:  # noqa: BLE001 - resilient
        logger.warning("send_push failed: %s", exc)
        result["reason"] = "error"
    return result
