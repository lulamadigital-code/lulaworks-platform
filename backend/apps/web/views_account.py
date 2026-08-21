"""Public account-lifecycle pages — no login required.

These are the pages the email links land on: accept an invitation (set your
first password and activate), and reset a forgotten password. Both take a
single-use, time-limited token and end by signing the user in. No password is
ever emailed; the user always chooses their own here.
"""

from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.identity.models import AccountToken
from apps.identity.services import (
    MemberError,
    accept_invitation,
    request_password_reset,
    reset_password,
)


def _token(token_str, purpose):
    return AccountToken.objects.filter(token=token_str, purpose=purpose).first()


@require_http_methods(["GET", "POST"])
def activate(request, token):
    """Accept an invitation: choose a password, activate, sign in."""
    tok = _token(token, AccountToken.Purpose.INVITE)
    valid = tok is not None and tok.is_valid
    if request.method == "POST" and valid:
        password = request.POST.get("password", "")
        confirm = request.POST.get("confirm", "")
        if password != confirm:
            messages.error(request, "The two passwords don't match.")
        else:
            try:
                user = accept_invitation(token, password=password)
            except MemberError as exc:
                messages.error(request, str(exc))
            else:
                login(request, user)
                messages.success(request, "Welcome to Lulaworks! Your account is active.")
                return redirect("web:dashboard")
    return render(request, "web/set_password.html", {
        "valid": valid, "token": token, "mode": "activate",
        "title": "Set your password",
        "intro": ("Choose a password to activate your account."
                  if valid else ""),
        "company": tok.company if tok else None,
        "action_url": "web:activate",
    })


@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    """Ask for a reset link. Always confirms the same way (no user enumeration)."""
    if request.method == "POST":
        request_password_reset(request.POST.get("email", ""))
        return render(request, "web/password_reset_sent.html")
    return render(request, "web/password_reset_request.html")


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request, token):
    """Complete a reset: choose a new password, sign in."""
    tok = _token(token, AccountToken.Purpose.RESET)
    valid = tok is not None and tok.is_valid
    if request.method == "POST" and valid:
        password = request.POST.get("password", "")
        confirm = request.POST.get("confirm", "")
        if password != confirm:
            messages.error(request, "The two passwords don't match.")
        else:
            try:
                user = reset_password(token, password=password)
            except MemberError as exc:
                messages.error(request, str(exc))
            else:
                login(request, user)
                messages.success(request, "Your password has been reset.")
                return redirect("web:dashboard")
    return render(request, "web/set_password.html", {
        "valid": valid, "token": token, "mode": "reset",
        "title": "Choose a new password",
        "intro": "Enter a new password for your account." if valid else "",
        "company": tok.company if tok else None,
        "action_url": "web:password_reset_confirm",
    })
