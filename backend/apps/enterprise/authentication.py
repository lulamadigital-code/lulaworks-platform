"""API-key authentication — an *additional* way to authenticate the same v1 API,
alongside the first-party JWT the web/mobile apps use.

Key auth is the surface gated on the Enterprise ``api_access`` entitlement: the
mobile app (short-lived user JWTs) stays open to every plan, while long-lived
programmatic keys only work for tenants that hold ``api_access``. Losing the
entitlement disables every key without deleting it.

Header:  ``Authorization: Api-Key <token>``   (or ``X-Api-Key: <token>``)
"""
from rest_framework import authentication, exceptions

from apps.core.context import set_current_company

from .services import resolve_api_key, touch_api_key


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Api-Key"

    def _extract(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if header:
            parts = header.split()
            if len(parts) == 2 and parts[0].lower() == self.keyword.lower():
                return parts[1]
        return request.META.get("HTTP_X_API_KEY") or None

    def authenticate(self, request):
        token = self._extract(request)
        if not token:
            return None  # not an API-key request — let JWT/session try

        key = resolve_api_key(token)
        if key is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")

        # Entitlement gate — kept close to the request so a downgrade takes effect
        # immediately, without touching the keys themselves.
        from apps.billing.services import has_feature
        if not has_feature(key.company, "api_access"):
            raise exceptions.AuthenticationFailed(
                "API access is not enabled on this plan.")

        owner = key.created_by
        if owner is None or not getattr(owner, "is_active", False):
            raise exceptions.AuthenticationFailed("API key owner is inactive.")

        # Bind the tenant authoritatively to the key's company (defence in depth;
        # set_tenant_from_request also reads request.auth.company_id).
        set_current_company(key.company_id)
        touch_api_key(key)
        return (owner, key)

    def authenticate_header(self, request):
        return self.keyword
