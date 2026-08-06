"""Provider administration — the non-secret settings an admin controls, plus
the read-only status/health/usage the AI Settings console shows.

Keys are never touched here: `ai_configured` reads them from the environment.
This module owns only enable/priority/model overrides and the derived views over
usage. Everything a provider adapter needs to actually run still comes from
settings (the key) + routing (which task goes where).
"""

from collections import defaultdict
from decimal import Decimal

from django.conf import settings

from .models import AIProviderSetting, AIUsageLog
from .providers import ai_configured

#: The providers the platform knows how to talk to, with their default order.
KNOWN_PROVIDERS = [
    ("gemini", "Gemini", 10),
    ("claude", "Claude", 20),
    ("openai", "OpenAI", 30),
]


def ensure_provider_settings() -> None:
    """Create the settings row for any known provider that has none yet, so the
    admin console always shows the full set. Idempotent."""
    for name, _label, priority in KNOWN_PROVIDERS:
        AIProviderSetting.objects.get_or_create(
            provider=name, defaults={"priority": priority, "enabled": True})


def _setting(name: str) -> AIProviderSetting | None:
    return AIProviderSetting.objects.filter(provider=name).first()


def is_enabled(name: str) -> bool:
    """Whether an admin has left this provider switched on. Defaults to on for a
    provider with no row yet (nothing configured = nothing disabled)."""
    row = _setting(name)
    return row.enabled if row else True


def priority(name: str) -> int:
    row = _setting(name)
    if row:
        return row.priority
    return dict((n, p) for n, _l, p in KNOWN_PROVIDERS).get(name, 100)


def model_for(name: str) -> str:
    """The model an admin pinned, else the environment default for the provider."""
    row = _setting(name)
    if row and row.model_override:
        return row.model_override
    return {
        "claude": settings.ANTHROPIC_MODEL,
        "openai": getattr(settings, "OPENAI_MODEL", "gpt-4o"),
        "gemini": settings.GEMINI_MODEL,
    }.get(name, "")


def set_enabled(name: str, enabled: bool) -> AIProviderSetting:
    ensure_provider_settings()
    row = _setting(name)
    row.enabled = bool(enabled)
    row.save(update_fields=["enabled", "updated_at"])
    return row


def set_priority(name: str, value: int) -> AIProviderSetting:
    ensure_provider_settings()
    row = _setting(name)
    row.priority = max(0, int(value))
    row.save(update_fields=["priority", "updated_at"])
    return row


def set_model_override(name: str, model: str) -> AIProviderSetting:
    ensure_provider_settings()
    row = _setting(name)
    row.model_override = (model or "").strip()[:64]
    row.save(update_fields=["model_override", "updated_at"])
    return row


# ── Status + health ───────────────────────────────────────────────────────────

def provider_status() -> list[dict]:
    """A row per provider for the console: configured (has a key), enabled,
    effective model, priority, and a plain-English state. Never exposes the key
    itself — only whether one is present."""
    ensure_provider_settings()
    rows = []
    for name, label, _p in KNOWN_PROVIDERS:
        configured = ai_configured(name)
        enabled = is_enabled(name)
        if not configured:
            state = "No API key set"
        elif not enabled:
            state = "Disabled by admin"
        else:
            state = "Ready"
        rows.append({
            "provider": name, "label": label,
            "configured": configured, "enabled": enabled,
            "model": model_for(name), "priority": priority(name),
            "state": state, "ready": configured and enabled,
        })
    return rows


def test_connection(name: str) -> dict:
    """A live health check: make the smallest possible call and report ok/error.
    Returns immediately (no call) when the provider has no key — the common case
    before go-live — so the button is safe to press in any environment."""
    if not ai_configured(name):
        return {"ok": False, "detail": "No API key configured."}
    if not is_enabled(name):
        return {"ok": False, "detail": "Provider is disabled."}
    from .providers import get_provider
    try:
        provider = get_provider(name)
        resp = provider.complete("ping", max_tokens=5)
        return {"ok": True, "detail": f"OK — responded as {name}.",
                "tokens": resp.tokens_in + resp.tokens_out}
    except Exception as exc:  # noqa: BLE001 - health check reports, never raises
        return {"ok": False, "detail": str(exc)[:200]}


# ── Usage statistics (from the append-only log) ───────────────────────────────

def usage_stats(company=None, *, days=30) -> dict:
    """Credit + call totals overall and per provider, for the console. Scoped to
    a company when given, else the whole platform."""
    from datetime import timedelta

    from django.utils import timezone

    since = timezone.now() - timedelta(days=days)
    qs = AIUsageLog.objects.filter(created_at__gte=since)
    if company is not None:
        qs = qs.filter(company=company)

    per_provider = defaultdict(lambda: {"calls": 0, "ok": 0, "failovers": 0,
                                        "credits": Decimal("0"), "tokens": 0})
    total_calls = total_credits = total_failovers = 0
    for log in qs.only("provider", "status", "credits_used", "tokens_in",
                       "tokens_out"):
        p = per_provider[log.provider]
        p["calls"] += 1
        total_calls += 1
        if log.status == "ok":
            p["ok"] += 1
        elif log.status == "failover":
            p["failovers"] += 1
            total_failovers += 1
        p["credits"] += log.credits_used or Decimal("0")
        p["tokens"] += (log.tokens_in or 0) + (log.tokens_out or 0)
        total_credits += float(log.credits_used or 0)

    return {
        "days": days,
        "total_calls": total_calls,
        "total_credits": Decimal(str(total_credits)),
        "total_failovers": total_failovers,
        "per_provider": [{"provider": k, **v} for k, v in sorted(per_provider.items())],
    }
