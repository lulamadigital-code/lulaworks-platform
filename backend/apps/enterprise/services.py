"""Enterprise governance services — recording the audit trail and minting /
verifying API keys.

`record()` is deliberately fail-safe: auditing must never break the action it is
recording, so any error is swallowed (and logged) rather than propagated.
"""
import hashlib
import logging
import secrets

from django.utils import timezone

from .models import ApiKey, AuditEvent

log = logging.getLogger(__name__)

TOKEN_PREFIX = "lwk"  # Lulaworks key


# ── Audit trail ───────────────────────────────────────────────────────────────

def _actor_label(user) -> str:
    if user is None:
        return "System"
    name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    email = getattr(user, "email", "") or ""
    if name and email:
        return f"{name} <{email}>"
    return name or email or str(user)


def _client(request):
    """(ip, user_agent) from a request, tolerant of proxies and absence."""
    if request is None:
        return None, ""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")) or None
    ua = (request.META.get("HTTP_USER_AGENT", "") or "")[:300]
    return ip, ua


def record(action, *, company=None, actor=None, request=None, summary="",
           target=None, target_type="", target_id="", **metadata):
    """Append one audit event. Resolves the tenant from ``company`` →
    ``actor.active_company`` → the request user; snapshots the actor and client.
    Returns the AuditEvent, or None on any failure (never raises)."""
    try:
        if actor is None and request is not None:
            u = getattr(request, "user", None)
            actor = u if (u is not None and getattr(u, "is_authenticated", False)) else None
        if company is None and actor is not None:
            company = getattr(actor, "active_company", None)
        if company is None:
            return None  # nothing to scope to — skip silently
        if target is not None and not target_type:
            target_type = target.__class__.__name__.lower()
            target_id = str(getattr(target, "pk", "") or "")
        ip, ua = _client(request)
        # all_objects: the company is set explicitly, so bypass the tenant-scoped
        # manager (the login signal records with no ambient tenant in context).
        return AuditEvent.all_objects.create(
            company=company, action=action, actor=actor,
            actor_label=_actor_label(actor), summary=summary[:300],
            target_type=target_type[:60], target_id=str(target_id)[:120],
            ip=ip, user_agent=ua, metadata=metadata or {},
        )
    except Exception:  # pragma: no cover - auditing must never break the caller
        log.exception("audit.record failed for action=%s", action)
        return None


# ── API keys ──────────────────────────────────────────────────────────────────

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_api_key(company, name, actor=None, scopes=None):
    """Create a key and return (ApiKey, raw_token). The raw token is shown once
    and never stored — only its SHA-256 hash is kept."""
    secret = secrets.token_urlsafe(36)
    token = f"{TOKEN_PREFIX}_{secret}"
    prefix = token[:12]                       # e.g. lwk_ab12cd34 — visible id
    key = ApiKey.objects.create(
        company=company, name=name.strip()[:120] or "API key",
        token_prefix=prefix, last_four=token[-4:], key_hash=_hash(token),
        scopes=scopes or [], created_by=actor,
    )
    return key, token


def resolve_api_key(token: str):
    """Return the active ApiKey for a raw token, or None. Constant-time hash
    compare; ignores revoked keys. Does NOT check entitlement — the caller
    (authentication class) does, so the check stays close to the request."""
    if not token or not token.startswith(f"{TOKEN_PREFIX}_"):
        return None
    prefix = token[:12]
    digest = _hash(token)
    for key in ApiKey.all_objects.filter(token_prefix=prefix, revoked_at__isnull=True):
        if secrets.compare_digest(key.key_hash, digest):
            return key
    return None


def touch_api_key(key):
    """Record use, throttled to once a minute to avoid a write per request."""
    now = timezone.now()
    if key.last_used_at and (now - key.last_used_at).total_seconds() < 60:
        return
    ApiKey.all_objects.filter(pk=key.pk).update(last_used_at=now)


def revoke_api_key(key, actor=None):
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])
    return key


# ── SSO configuration (config surface only — no live handshake) ───────────────

def sso_sp_details(request, company):
    """The Service-Provider values a tenant enters into their IdP. Derived from
    the request's own origin so they are correct for this deployment. Marked
    provisional in the UI — these become live only once Lulaworks activates SSO
    against a real IdP library."""
    base = request.build_absolute_uri("/").rstrip("/")
    cid = getattr(company, "id", "")
    return {
        "sp_entity_id": f"{base}/sso/metadata/{cid}/",
        "saml_acs_url": f"{base}/sso/saml/acs/",
        "oidc_redirect_uri": f"{base}/sso/oidc/callback/",
    }
