"""Manager-web middleware.

`ForcePasswordChangeMiddleware` closes the loop on admin-created accounts: a
manager sets a temporary password and reads it out, so that credential has been
spoken aloud and possibly written down. Until the holder replaces it, every page
in the manager web redirects to the change-password screen. The account can sign
in and do exactly one thing.
"""

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class IdleTimeoutMiddleware:
    """Sign a manager-web user out after SESSION_IDLE_TIMEOUT seconds of no
    activity (sliding — each request resets the clock). Guards the session web
    only; the JWT API/mobile app uses its own token lifetime. Set the timeout to
    0 to disable."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = int(getattr(settings, "SESSION_IDLE_TIMEOUT", 0) or 0)

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (self.timeout > 0 and user is not None and user.is_authenticated
                and self._is_web(request)):
            now = int(time.time())
            last = request.session.get("last_activity")
            if last is not None and now - last > self.timeout:
                logout(request)             # flushes the session
                messages.info(request,
                              "You were signed out after a period of inactivity.")
                return redirect("web:login")
            request.session["last_activity"] = now
        return self.get_response(request)

    @staticmethod
    def _is_web(request) -> bool:
        # Only the session-authenticated manager web; leave the API/static alone.
        return not request.path.startswith(
            ("/api/", "/static/", "/media/", "/health", "/e/collect"))


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


class CompanySetupMiddleware:
    """DEPRECATED — retired 2026-09-03. This used to full-screen-LOCK a new
    company owner onto the Company Profile page until every essential field was
    filled in. That's the wrong model: owners must be able to enter and explore
    LulaWorks immediately. Company setup is now a PROGRESSIVE REQUIREMENT — a
    missing setting blocks only the specific action that needs it (issuing an
    invoice, generating a PDF), via apps.identity.company_setup. This class is a
    no-op kept only so an old MIDDLEWARE entry can't break; it is no longer
    wired in settings and can be deleted once no environment references it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)


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


class AdminAccessMiddleware:
    """Hide Django's built-in admin from everyone who isn't a signed-in
    superuser.

    The un-branded ``/admin/`` login is a classic brute-force / drive-by target.
    Rather than serve it to the public, we return a plain 404 for the entire
    ``/admin/`` tree unless the request already carries an authenticated,
    active, superuser session. Superusers reach the admin *after* signing in
    through the normal web login (``/login/``), which sets the same Django
    session the admin honours; the Platform Console's deep admin links keep
    working for them. The admin's own login page is deliberately NOT exposed —
    to an attacker the entire admin simply does not exist, so there is nothing
    to hammer. (If a superuser's session has lapsed, they sign in again at the
    ordinary ``/login/`` page, not at ``/admin/``.)

    Must sit AFTER ``AuthenticationMiddleware`` so ``request.user`` is resolved.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path == "/admin" or path.startswith("/admin/"):
            user = getattr(request, "user", None)
            allowed = bool(
                user is not None
                and user.is_authenticated
                and user.is_active
                and user.is_superuser
            )
            if not allowed:
                from django.http import HttpResponseNotFound

                return HttpResponseNotFound(
                    "<h1>Not Found</h1>"
                    "<p>The requested resource was not found on this server.</p>"
                )
        return self.get_response(request)
