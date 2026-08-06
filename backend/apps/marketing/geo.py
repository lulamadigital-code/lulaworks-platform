"""Locale/geo → currency detection for the public site.

Picks the visitor's currency automatically so a US visitor sees USD, a UK
visitor GBP, etc. Resolution order (first hit wins):

  1. Explicit ?currency= override (hidden; for support/QA) — then remembered.
  2. A currency already stored in the session (sticky once detected/chosen).
  3. The country from a CDN/proxy geo header (Cloudflare / CloudFront / etc.).
  4. The country from the browser's Accept-Language (e.g. en-US → US).
  5. Platform default (settings.DEFAULT_CURRENCY).

No external API calls and no GeoIP database — it uses the country a fronting CDN
already provides, degrading to locale then default. A real GeoIP lookup can slot
into `_country_from_ip` later without changing callers.
"""

from django.conf import settings

from apps.billing.models import SUPPORTED_CURRENCIES

# Countries → the currency we bill them in (only currencies we actually price in).
_EUROZONE = {
    "AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "IE", "IT", "LT",
    "LU", "LV", "MT", "NL", "PT", "SI", "SK", "HR",
}
COUNTRY_CURRENCY = {
    "US": "USD", "GB": "GBP", "AU": "AUD", "NZ": "AUD", "ZA": "ZAR",
    **{c: "EUR" for c in _EUROZONE},
}

# Geo country headers a fronting CDN/proxy may set (checked in order).
_COUNTRY_HEADERS = (
    "HTTP_CF_IPCOUNTRY",              # Cloudflare
    "HTTP_CLOUDFRONT_VIEWER_COUNTRY",  # AWS CloudFront
    "HTTP_X_APPENGINE_COUNTRY",        # Google
    "HTTP_X_COUNTRY_CODE",
    "HTTP_X_GEO_COUNTRY",
)


def _default() -> str:
    return getattr(settings, "DEFAULT_CURRENCY", "ZAR")


def _country_from_headers(request):
    for h in _COUNTRY_HEADERS:
        v = request.META.get(h)
        if v and len(v) == 2 and v.isalpha():
            return v.upper()
    return None


def _country_from_accept_language(request):
    al = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    first = al.split(",")[0].strip().replace("_", "-")  # e.g. "en-US"
    parts = first.split("-")
    if len(parts) >= 2 and len(parts[1]) == 2 and parts[1].isalpha():
        return parts[1].upper()
    return None


def _country_from_ip(request):
    """Placeholder for a real IP→country lookup (GeoIP2/MaxMind) if added later."""
    return None


def detect_currency(request) -> str:
    """The currency to show/bill this visitor, auto-detected from their location."""
    override = (request.GET.get("currency") or "").upper()
    if override in SUPPORTED_CURRENCIES:
        request.session["currency"] = override
        return override

    stored = request.session.get("currency")
    if stored in SUPPORTED_CURRENCIES:
        return stored

    country = (_country_from_headers(request)
               or _country_from_ip(request)
               or _country_from_accept_language(request))
    currency = COUNTRY_CURRENCY.get(country) if country else None
    if currency not in SUPPORTED_CURRENCIES:
        currency = _default()

    request.session["currency"] = currency
    return currency
