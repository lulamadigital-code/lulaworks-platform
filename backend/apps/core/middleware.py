"""TenantMiddleware — sets the ambient tenant from the authenticated user's
active company for the duration of the request (DATA_MODEL §1)."""

from .context import clear_current_company, set_current_company


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        company_id = None
        user = getattr(request, "user", None)
        # JWTAuthentication runs in the view (DRF), so request.user may be lazy
        # here; the reliable source is the token's company claim, which
        # JWTAuthentication attaches. We resolve from the user's active company
        # once authenticated. For DRF views, set_tenant_from_request() (below)
        # is also called in the base viewset as defence in depth.
        if user is not None and getattr(user, "is_authenticated", False):
            company_id = getattr(user, "company_id", None)
        set_current_company(company_id)
        try:
            response = self.get_response(request)
        finally:
            clear_current_company()
        return response


def set_tenant_from_request(request) -> None:
    """Called by the DRF base viewset after authentication resolves the user —
    guarantees the tenant is bound even though DRF authenticates inside the view."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        set_current_company(getattr(user, "company_id", None))
