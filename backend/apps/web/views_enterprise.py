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

from django.utils import timezone

from apps.billing.services import has_feature
from apps.enterprise.models import (
    ApiKey, AuditAction, AuditEvent, SSOConfig, SSOProtocol, SSOStatus,
)
from apps.enterprise import services as gov


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
        cfg.protocol = (request.POST.get("protocol") or SSOProtocol.SAML)
        cfg.saml_idp_entity_id = request.POST.get("saml_idp_entity_id", "").strip()
        cfg.saml_idp_sso_url = request.POST.get("saml_idp_sso_url", "").strip()
        cfg.saml_idp_x509_cert = request.POST.get("saml_idp_x509_cert", "").strip()
        cfg.oidc_issuer = request.POST.get("oidc_issuer", "").strip()
        cfg.oidc_client_id = request.POST.get("oidc_client_id", "").strip()
        cfg.allowed_domains = request.POST.get("allowed_domains", "").strip()
        cfg.notes = request.POST.get("notes", "").strip()

        # Has the admin supplied enough to be considered "configured"?
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
            messages.success(request, _("Activation requested. Lulaworks will complete the secure setup with you and confirm when SSO is live."))
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
