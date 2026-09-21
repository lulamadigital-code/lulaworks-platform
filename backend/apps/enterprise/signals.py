"""Audit-trail signal handlers — the sign-in history every tenant keeps.

Kept minimal and fail-safe: `record()` swallows its own errors, so a signal
never breaks authentication.
"""
from django.contrib.auth import signals
from django.dispatch import receiver

from .models import AuditAction
from .services import record


@receiver(signals.user_logged_in)
def _on_login(sender, request, user, **kwargs):
    company = getattr(user, "active_company", None)
    record(AuditAction.LOGIN, company=company, actor=user, request=request,
           summary="Signed in")


@receiver(signals.user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    if user is None:
        return
    company = getattr(user, "active_company", None)
    record(AuditAction.LOGOUT, company=company, actor=user, request=request,
           summary="Signed out")


@receiver(signals.user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    # Resolve the tenant from the attempted email so the failed attempt lands in
    # the right company's trail; skip silently if we can't (unknown email).
    email = (credentials or {}).get("username") or (credentials or {}).get("email")
    if not email:
        return
    try:
        from apps.identity.models import User
        user = User.objects.filter(email__iexact=email).first()
    except Exception:
        user = None
    if user is None:
        return
    company = getattr(user, "active_company", None)
    if company is None:
        return
    record(AuditAction.LOGIN_FAILED, company=company, actor=None, request=request,
           summary=f"Failed sign-in for {email}", target_type="user",
           target_id=str(user.pk))
