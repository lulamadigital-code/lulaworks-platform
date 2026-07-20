"""Manager-web middleware.

`ForcePasswordChangeMiddleware` closes the loop on admin-created accounts: a
manager sets a temporary password and reads it out, so that credential has been
spoken aloud and possibly written down. Until the holder replaces it, every page
in the manager web redirects to the change-password screen. The account can sign
in and do exactly one thing.
"""

from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Redirect authenticated users with `must_change_password` to the change
    screen. Only guards the session-authenticated manager web — the JWT API is
    unaffected (the Flutter app has its own flow)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated
                and getattr(user, "must_change_password", False)
                and self._is_guarded(request)):
            return redirect("web:change_password")
        return self.get_response(request)

    @staticmethod
    def _is_guarded(request) -> bool:
        """Guard the manager web only, and never the pages needed to escape the
        gate (the change screen itself, signing out, and static assets)."""
        path = request.path
        if path.startswith(("/api/", "/static/", "/media/", "/admin/", "/health")):
            return False
        allowed = {reverse("web:change_password"), reverse("web:logout"),
                   reverse("web:login")}
        return path not in allowed
