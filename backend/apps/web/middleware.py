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


class RequestIDMiddleware:
    """Stamp every request with a short correlation id (request.request_id) and
    echo it as the X-Request-ID response header. It ties a customer's error
    reference to the matching server log line without exposing anything sensitive.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import uuid
        request.request_id = uuid.uuid4().hex[:12]
        response = self.get_response(request)
        try:
            response["X-Request-ID"] = request.request_id
        except Exception:                                      # noqa: BLE001
            pass
        return response

    def process_exception(self, request, exception):
        # Stash the exception CLASS NAME only (never the message/trace) so the
        # 500 handler can record safe technical context.
        request._lw_exc_type = type(exception).__name__
        return None
