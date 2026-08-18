"""The one entry point for analytics — `track()`. Everything else calls this.

Design rules:
- NON-BLOCKING & FAIL-OPEN: analytics must never slow down or break a business
  operation. Every path is wrapped; a failure is swallowed.
- PRIVACY FIRST: metadata is scrubbed of anything that looks sensitive, and only
  small JSON scalars are kept. Never pass document/quotation/invoice contents.
"""
import re

# Client-emitted events are allow-listed — the browser can't invent event names.
ALLOWED_CLIENT_EVENTS = {
    "page_view", "button_click", "navigation_click", "form_start", "form_submit",
    "form_error", "search", "file_download", "video_start", "video_complete",
    "pricing_viewed", "faq_opened", "accordion_opened",
}

# Keys we refuse to store even if a caller passes them by mistake.
_SENSITIVE = re.compile(
    r"pass|token|secret|auth|cvv|card|otp|ssn|api[_-]?key|prompt|content|body|"
    r"document|attachment|bank|account[_-]?number", re.I)

_MAX_META_KEYS = 20
_MAX_STR = 200


def _scrub(metadata):
    """Keep only small, non-sensitive scalar properties."""
    out = {}
    if not isinstance(metadata, dict):
        return out
    for k, v in list(metadata.items())[:_MAX_META_KEYS]:
        if not isinstance(k, str) or _SENSITIVE.search(k):
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:_MAX_STR]
        # dicts/lists/objects are dropped — no nested business data
    return out


def _device(ua):
    ua = (ua or "").lower()
    if "tablet" in ua or "ipad" in ua:
        return "tablet"
    if "mobi" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    return "desktop"


def _browser(ua):
    ua = ua or ""
    for token, name in (("Edg", "Edge"), ("OPR", "Opera"), ("Chrome", "Chrome"),
                        ("Firefox", "Firefox"), ("Safari", "Safari")):
        if token in ua:
            return name
    return ""


def _source(request):
    """Classify acquisition source from UTM params / referrer (coarse)."""
    if request is None:
        return ""
    utm = request.GET.get("utm_source") or request.COOKIES.get("lw_utm_source")
    if utm:
        return utm[:40]
    ref = request.META.get("HTTP_REFERER", "")
    if not ref:
        return "direct"
    host = re.sub(r"^https?://([^/]+).*$", r"\1", ref).lower()
    if any(s in host for s in ("google.", "bing.", "duckduckgo")):
        return "organic"
    if any(s in host for s in ("facebook.", "linkedin.", "youtube.", "twitter.",
                               "x.com", "instagram.", "whatsapp")):
        return "social"
    return "referral"


def _anon_id(request):
    if request is None:
        return ""
    return request.COOKIES.get("lw_aid", "")[:40]


def track(event_name, *, request=None, user=None, company=None, module="",
          feature="", source="", metadata=None, path=""):
    """Record one analytics event. Safe to call from anywhere; never raises."""
    try:
        u = user
        if u is None and request is not None:
            ru = getattr(request, "user", None)
            u = ru if getattr(ru, "is_authenticated", False) else None
        comp_id = getattr(company, "id", None) or getattr(company, "pk", None)
        if comp_id is None and u is not None:
            comp_id = getattr(u, "active_company_id", None)

        payload = {
            "event_name": event_name[:64],
            "user_id": getattr(u, "id", None),
            "company_id": comp_id,
            "module": (module or "")[:40],
            "feature": (feature or "")[:64],
            "path": (path or (getattr(request, "path", "") if request else ""))[:300],
            "metadata": _scrub(metadata or {}),
        }
        if request is not None:
            ua = request.META.get("HTTP_USER_AGENT", "")
            payload.update(
                session_id=(getattr(getattr(request, "session", None), "session_key", "") or "")[:40],
                anonymous_id=_anon_id(request),
                device=_device(ua), browser=_browser(ua),
                source=(source or _source(request))[:40])
        else:
            payload["source"] = (source or "")[:40]
        _record(payload)
    except Exception:                                          # noqa: BLE001
        pass  # analytics must never break the caller


def _record(payload):
    """Persist the event. A single indexed INSERT is cheap, so we write inline
    (fail-open) rather than depend on a worker being healthy — losing events to a
    lagging/stale worker would defeat the point. `record_event` stays available
    for future batch/offload use."""
    _write(payload)


def _write(payload):
    from .models import AnalyticsEvent
    AnalyticsEvent.objects.create(
        event_name=payload.get("event_name", "event"),
        user_id=payload.get("user_id"), company_id=payload.get("company_id"),
        session_id=payload.get("session_id", ""), anonymous_id=payload.get("anonymous_id", ""),
        path=payload.get("path", ""), module=payload.get("module", ""),
        feature=payload.get("feature", ""), source=payload.get("source", ""),
        device=payload.get("device", ""), browser=payload.get("browser", ""),
        metadata=payload.get("metadata", {}))
