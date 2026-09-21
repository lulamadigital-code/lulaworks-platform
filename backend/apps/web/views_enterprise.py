"""Enterprise governance surfaces — the audit-log viewer and API-key manager.

Both are gated twice: the acting user must hold ``company.manage``, and the
tenant must hold the matching Enterprise entitlement (``audit_log`` /
``api_access``). When the permission is present but the entitlement is not, the
page still renders with an honest upgrade prompt rather than a 403 — the data
was captured all along and appears the moment the tenant moves to Enterprise.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

import secrets

from django.contrib.auth import login as auth_login
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import has_feature
from apps.enterprise.models import (
    ApiKey, AuditAction, AuditEvent, SSOConfig, SSOProtocol, SSOStatus,
)
from apps.enterprise import services as gov
from apps.enterprise import oidc


def _can(user):
    return user.has_perm_code("company.manage")


# ── Audit log ─────────────────────────────────────────────────────────────────

@login_required
def audit_log(request):
    if not _can(request.user):
        messages.error(request, _("The audit log is available to company administrators."))
        return redirect("web:settings")
    company = request.user.active_company
    entitled = has_feature(company, "audit_log")

    page = None
    action = request.GET.get("action", "")
    actions = [{"value": a.value, "label": a.label} for a in AuditAction]
    if entitled:
        events = AuditEvent.objects.filter(company=company)
        if action:
            events = events.filter(action=action)
        page = Paginator(events, 40).get_page(request.GET.get("page"))

    return render(request, "web/audit_log.html", {
        "nav_section": "settings", "entitled": entitled,
        "page": page, "action": action, "actions": actions,
    })


# ── API keys ──────────────────────────────────────────────────────────────────

@login_required
def api_keys(request):
    if not _can(request.user):
        messages.error(request, _("API keys are available to company administrators."))
        return redirect("web:settings")
    company = request.user.active_company
    entitled = has_feature(company, "api_access")
    keys = ApiKey.objects.filter(company=company) if entitled else ApiKey.objects.none()
    new_token = request.session.pop("new_api_token", None)
    return render(request, "web/api_keys.html", {
        "nav_section": "settings", "entitled": entitled,
        "keys": keys, "new_token": new_token,
    })


@login_required
@require_POST
def api_key_create(request):
    if not _can(request.user):
        return redirect("web:settings")
    company = request.user.active_company
    if not has_feature(company, "api_access"):
        messages.error(request, _("API access is not enabled on your plan."))
        return redirect("web:api_keys")
    name = request.POST.get("name", "").strip() or _("API key")
    key, token = gov.issue_api_key(company, name, actor=request.user)
    gov.record(AuditAction.API_KEY_CREATED, company=company, actor=request.user,
               request=request, summary=_("Created API key “%(n)s”") % {"n": key.name},
               target=key)
    # Show the raw token exactly once, on the next render.
    request.session["new_api_token"] = token
    messages.success(request, _("API key created. Copy it now — it won't be shown again."))
    return redirect("web:api_keys")


@login_required
@require_POST
def api_key_revoke(request, pk):
    if not _can(request.user):
        return redirect("web:settings")
    company = request.user.active_company
    key = get_object_or_404(ApiKey.objects.filter(company=company), pk=pk)
    gov.revoke_api_key(key, actor=request.user)
    gov.record(AuditAction.API_KEY_REVOKED, company=company, actor=request.user,
               request=request, summary=_("Revoked API key “%(n)s”") % {"n": key.name},
               target=key)
    messages.success(request, _("API key revoked."))
    return redirect("web:api_keys")


# ── Single Sign-On (config surface only) ──────────────────────────────────────

@login_required
def sso_settings(request):
    if not _can(request.user):
        messages.error(request, _("SSO is available to company administrators."))
        return redirect("web:settings")
    company = request.user.active_company
    entitled = has_feature(company, "sso")
    cfg = SSOConfig.objects.filter(company=company).first() if entitled else None

    if request.method == "POST" and entitled:
        action = request.POST.get("action", "save")
        if cfg is None:
            cfg = SSOConfig(company=company)

        # Enable / disable act on the stored config without re-reading the form.
        if action in ("enable", "disable"):
            if action == "enable":
                if cfg.protocol != SSOProtocol.OIDC or not cfg.oidc_ready:
                    messages.error(request, _("Add your OIDC issuer, client ID and client secret before enabling SSO."))
                    return redirect("web:sso_settings")
                cfg.status = SSOStatus.ACTIVE
                cfg.save(update_fields=["status", "updated_at"])
                gov.record(AuditAction.SSO_CONFIGURED, company=company, actor=request.user,
                           request=request, summary=_("Enabled SSO sign-in"),
                           target=cfg, protocol=cfg.protocol, enabled=True)
                messages.success(request, _("SSO is now live. Members with your email domains can sign in through your provider; password sign-in still works."))
            else:
                cfg.status = SSOStatus.CONFIGURED
                cfg.save(update_fields=["status", "updated_at"])
                gov.record(AuditAction.SSO_CONFIGURED, company=company, actor=request.user,
                           request=request, summary=_("Disabled SSO sign-in"),
                           target=cfg, protocol=cfg.protocol, enabled=False)
                messages.success(request, _("SSO sign-in disabled."))
            return redirect("web:sso_settings")

        cfg.protocol = (request.POST.get("protocol") or SSOProtocol.SAML)
        cfg.saml_idp_entity_id = request.POST.get("saml_idp_entity_id", "").strip()
        cfg.saml_idp_sso_url = request.POST.get("saml_idp_sso_url", "").strip()
        cfg.saml_idp_x509_cert = request.POST.get("saml_idp_x509_cert", "").strip()
        cfg.oidc_issuer = request.POST.get("oidc_issuer", "").strip()
        cfg.oidc_client_id = request.POST.get("oidc_client_id", "").strip()
        cfg.allowed_domains = request.POST.get("allowed_domains", "").strip()
        cfg.notes = request.POST.get("notes", "").strip()
        # Client secret: only overwrite when a new value is supplied (blank keeps
        # the stored one); never echoed back to the page.
        new_secret = request.POST.get("oidc_client_secret", "").strip()
        if new_secret:
            cfg.set_client_secret(new_secret)

        has_saml = cfg.saml_idp_entity_id and cfg.saml_idp_sso_url and cfg.saml_idp_x509_cert
        has_oidc = cfg.oidc_issuer and cfg.oidc_client_id
        configured = has_saml if cfg.protocol == SSOProtocol.SAML else has_oidc

        if action == "request":
            if not configured:
                messages.error(request, _("Fill in your identity provider details before requesting activation."))
                cfg.save()
                return redirect("web:sso_settings")
            cfg.status = SSOStatus.ACTIVATION_REQUESTED
            cfg.requested_at = timezone.now()
            cfg.requested_by = request.user
            cfg.save()
            gov.record(AuditAction.SSO_CONFIGURED, company=company, actor=request.user,
                       request=request, summary=_("Requested SSO activation (%(p)s)")
                       % {"p": cfg.get_protocol_display()}, target=cfg,
                       protocol=cfg.protocol)
            messages.success(request, _("Activation requested. For SAML, Lulaworks completes the secure setup with you. For OIDC you can enable it yourself once issuer, client ID and secret are saved."))
        else:
            if cfg.status == SSOStatus.NOT_CONFIGURED and configured:
                cfg.status = SSOStatus.CONFIGURED
            cfg.save()
            gov.record(AuditAction.SSO_CONFIGURED, company=company, actor=request.user,
                       request=request, summary=_("Updated SSO configuration"),
                       target=cfg, protocol=cfg.protocol)
            messages.success(request, _("SSO configuration saved."))
        return redirect("web:sso_settings")

    return render(request, "web/sso.html", {
        "nav_section": "settings", "entitled": entitled, "cfg": cfg,
        "protocols": SSOProtocol.choices,
        "sp": gov.sso_sp_details(request, company) if entitled else None,
    })


# ── SSO sign-in flow (OIDC Authorization Code) — public, no login required ─────

def _oidc_redirect_uri(request):
    return request.build_absolute_uri(reverse("web:sso_callback"))


def sso_login(request):
    """Entry point for SSO sign-in. The visitor gives their work email; if it
    maps to a tenant with live OIDC SSO, we start the Authorization Code flow."""
    if request.user.is_authenticated:
        return redirect("web:dashboard")
    email = (request.POST.get("email") or request.GET.get("email") or "").strip().lower()
    if request.method != "POST" and not email:
        return render(request, "web/sso_login.html", {})

    cfg = gov.active_sso_for_email(email)
    if cfg is None:
        messages.error(request, _("SSO isn't set up for that email domain. Sign in with your email and password instead."))
        return render(request, "web/sso_login.html", {"email": email})

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    request.session["sso_state"] = state
    request.session["sso_nonce"] = nonce
    request.session["sso_company"] = str(cfg.company_id)
    try:
        url = oidc.authorization_url(cfg, _oidc_redirect_uri(request), state, nonce)
    except oidc.OIDCError as exc:
        messages.error(request, str(exc))
        return render(request, "web/sso_login.html", {"email": email})
    return redirect(url)


def sso_callback(request):
    """OIDC redirect URI. Verifies the provider's response, then signs in ONLY an
    existing active member of the pinned tenant (no just-in-time provisioning)."""
    if request.user.is_authenticated:
        return redirect("web:dashboard")

    state = request.session.pop("sso_state", None)
    nonce = request.session.pop("sso_nonce", None)
    company_id = request.session.pop("sso_company", None)
    err = request.GET.get("error")
    if err:
        messages.error(request, _("Your provider reported: %(e)s") % {"e": err})
        return redirect("web:login")
    if not state or request.GET.get("state") != state or not company_id:
        messages.error(request, _("The sign-in could not be verified. Please try again."))
        return redirect("web:login")

    from apps.identity.models import Company
    company = Company.objects.filter(pk=company_id).first()
    cfg = SSOConfig.all_objects.filter(company=company, status=SSOStatus.ACTIVE).first() \
        if company else None
    if cfg is None or not has_feature(company, "sso"):
        messages.error(request, _("SSO is no longer available for that account."))
        return redirect("web:login")

    try:
        tokens = oidc.exchange_code(cfg, request.GET.get("code", ""), _oidc_redirect_uri(request))
        claims = oidc.verify_id_token(cfg, tokens["id_token"], nonce)
    except oidc.OIDCError as exc:
        gov.record(AuditAction.LOGIN_FAILED, company=company, request=request,
                   summary=_("SSO sign-in failed: %(e)s") % {"e": exc})
        messages.error(request, str(exc))
        return redirect("web:login")

    email = (claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is False:
        messages.error(request, _("Your provider did not supply a verified email address."))
        return redirect("web:login")

    membership = gov.sso_member_for(company, email)
    if membership is None:
        gov.record(AuditAction.LOGIN_FAILED, company=company, request=request,
                   summary=_("SSO sign-in denied — %(e)s is not a member") % {"e": email},
                   target_type="user", target_id=email)
        messages.error(request, _("%(e)s isn't a member of this workspace. Ask an administrator to invite you first.") % {"e": email})
        return redirect("web:login")

    user = membership.user
    if user.active_company_id != company.id:
        user.active_company = company
        user.save(update_fields=["active_company"])
    auth_login(request, user)
    gov.record(AuditAction.LOGIN, company=company, actor=user, request=request,
               summary=_("Signed in via SSO"), sso=True)
    return redirect("web:dashboard")
