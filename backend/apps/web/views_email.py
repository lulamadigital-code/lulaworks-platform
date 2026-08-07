"""Email history — the admin's window into everything LulaWorks has sent.

Reads the platform EmailLog scoped to the current company, with a status filter
and a manual resend for failures. Gated on company.manage.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.notifications.models import EmailLog, EmailStatus


def _can(user):
    return user.has_perm_code("company.manage")


@login_required
def email_history(request):
    if not _can(request.user):
        messages.error(request, "Email history is available to company administrators.")
        return redirect("web:company_profile")
    company = request.user.active_company
    logs = EmailLog.objects.filter(company=company)
    status = request.GET.get("status", "")
    if status:
        logs = logs.filter(status=status)

    tabs = [{"value": s.value, "label": s.label,
             "count": EmailLog.objects.filter(company=company, status=s.value).count()}
            for s in EmailStatus]
    page = Paginator(logs, 30).get_page(request.GET.get("page"))
    return render(request, "web/email_history.html", {
        "page": page, "status": status, "tabs": tabs,
        "total": EmailLog.objects.filter(company=company).count(),
    })


@login_required
def email_detail(request, pk):
    if not _can(request.user):
        return redirect("web:company_profile")
    log = get_object_or_404(
        EmailLog.objects.filter(company=request.user.active_company), pk=pk)
    return render(request, "web/email_detail.html", {"log": log})


@login_required
@require_POST
def email_resend(request, pk):
    if not _can(request.user):
        messages.error(request, "You do not have permission to resend email.")
        return redirect("web:email_history")
    log = get_object_or_404(
        EmailLog.objects.filter(company=request.user.active_company), pk=pk)
    from apps.notifications.service import resend
    resend(log, sent_by=request.user)
    messages.success(request, f"Resent to {log.to_email}.")
    return redirect("web:email_history")
