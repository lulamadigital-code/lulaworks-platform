"""Location → currency detection for the public site.

Picks the visitor's currency from their **location** (not their browser
language, which is unreliable — a South African browser often sends en-GB).
Resolution order, first hit wins:

  1. Explicit ?currency= override (hidden; for support/QA) — remembered.
  2. A currency already stored in the session (sticky once confidently found).
  3. Country from a CDN/proxy geo header (Cloudflare CF-IPCountry / CloudFront …).
  4. Country from a lightweight IP geolocation lookup (one call per session).
  5. Platform default (settings.DEFAULT_CURRENCY) — not stored, so a later
     confident detection can still correct it.

Only *confident* results (override / header / IP) are cached in the session, so
a transient miss never sticks the wrong currency. Best accuracy comes from
fronting the site with Cloudflare (adds CF-IPCountry, no external call).
"""

import ipaddress
import urllib.request

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


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")
    return ip


def _is_public_ip(ip) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _country_from_ip(request):
    """Best-effort IP → ISO country via a free geolocation service. Skips
    private/loopback IPs and fails closed (None) on any error. The visitor's IP
    is sent to the geolocation provider — a standard practice for this feature."""
    ip = _client_ip(request)
    if not ip or not _is_public_ip(ip):
        return None
    try:
        req = urllib.request.Request(
            f"https://ipapi.co/{ip}/country/",
            headers={"User-Agent": "Lulaworks/1.0"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            code = resp.read().decode().strip().upper()
        return code if len(code) == 2 and code.isalpha() else None
    except Exception:
        return None


def detect_currency(request) -> str:
    """The currency to show this visitor.

    Precedence — a registered account's currency is an attribute of the account,
    NOT of where the person happens to be sitting:
      1. An explicit choice this visit (?currency=…), remembered for the session.
      2. A logged-in user's OWN company currency — set when they registered, and
         unchanged if they log in while travelling in another country.
      3. An anonymous visitor's location (geo/IP), cached to avoid repeat lookups.
      4. The platform default.
    """
    # 1. Deliberate choice (e.g. the currency switcher). Wins for everyone.
    override = (request.GET.get("currency") or "").upper()
    if override in SUPPORTED_CURRENCIES:
        request.session["ccy_pref"] = override
        return override
    pref = request.session.get("ccy_pref")
    if pref in SUPPORTED_CURRENCIES:
        return pref

    # 2. Registered user → their company's currency, wherever they are logged in.
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        company_ccy = getattr(getattr(user, "active_company", None), "currency", None)
        if company_ccy in SUPPORTED_CURRENCIES:
            return company_ccy

    # 3. Anonymous visitor → their location (cached; never overrides a login).
    geo = request.session.get("ccy_geo")
    if geo in SUPPORTED_CURRENCIES:
        return geo
    country = _country_from_headers(request) or _country_from_ip(request)
    currency = COUNTRY_CURRENCY.get(country) if country else None
    if currency in SUPPORTED_CURRENCIES:
        request.session["ccy_geo"] = currency   # confident → sticky (anon only)
        return currency

    return _default()   # unsure → platform default, not cached
