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

from apps.billing.services import has_feature
from apps.enterprise.models import ApiKey, AuditAction, AuditEvent
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
