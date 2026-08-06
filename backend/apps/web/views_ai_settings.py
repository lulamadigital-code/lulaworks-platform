"""AI Settings — the super-admin console for the AI Orchestration Platform.

Shows which provider serves which task, each provider's connection status and
model, and the credit/usage stats — and lets an admin enable/disable a provider,
retune priority, and run a live connection test. It never shows or accepts an
API key: keys live in the environment only.

End users never see this or the provider names; they interact with LulaAI. This
page is for the person who runs the account (company.manage — the "Company Super
Administrator").
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.ai_platform import provider_admin as pa
from apps.ai_platform.routing import DEFAULT_TASK_ROUTES, TaskType, route


def _can(user):
    return user.has_perm_code("company.manage")


#: Task rows for the "which model does what" panel — label + the live chain.
_TASK_LABELS = [
    (TaskType.EXTRACTION, "Document extraction"),
    (TaskType.REASONING, "Reasoning & analysis"),
    (TaskType.GENERATION, "Drafting & suggestions"),
    (TaskType.SUMMARY, "Summaries"),
    (TaskType.IMAGE, "Image understanding"),
    (TaskType.CLASSIFICATION, "Document classification"),
    (TaskType.MATCHING, "Product / supplier matching"),
    (TaskType.CHAT, "LulaAI chat"),
]


@login_required
def ai_settings(request):
    company = request.user.active_company
    can = _can(request.user)
    if not can:
        # Read-only glimpse is fine, but the controls are gated.
        messages.info(request, "AI settings are managed by a company administrator.")

    task_routes = []
    for task, label in _TASK_LABELS:
        live = route(task)                      # configured + enabled, in order
        preferred = DEFAULT_TASK_ROUTES.get(task, [])
        task_routes.append({
            "task": task, "label": label,
            "preferred": preferred,
            "active": live[0] if live else None,
            "chain": live,
        })

    return render(request, "web/ai_settings.html", {
        "providers": pa.provider_status(),
        "task_routes": task_routes,
        "usage": pa.usage_stats(company),
        "can_manage": can,
    })


@login_required
@require_POST
def ai_provider_toggle(request, provider):
    if not _can(request.user):
        messages.error(request, "You do not have permission to change AI settings.")
        return redirect("web:ai_settings")
    enabled = request.POST.get("enabled") == "1"
    pa.set_enabled(provider, enabled)
    messages.success(request, f"{provider.title()} {'enabled' if enabled else 'disabled'}.")
    return redirect("web:ai_settings")


@login_required
@require_POST
def ai_provider_priority(request, provider):
    if not _can(request.user):
        messages.error(request, "You do not have permission to change AI settings.")
        return redirect("web:ai_settings")
    value = request.POST.get("priority", "").strip()
    if value.isdigit():
        pa.set_priority(provider, int(value))
        model = request.POST.get("model_override")
        if model is not None:
            pa.set_model_override(provider, model)
        messages.success(request, f"{provider.title()} settings updated.")
    else:
        messages.error(request, "Priority must be a number.")
    return redirect("web:ai_settings")


@login_required
@require_POST
def ai_provider_test(request, provider):
    """Live connection test — returns JSON so the page can show the result
    without a reload. Reports 'no key' cleanly before go-live."""
    if not _can(request.user):
        return JsonResponse({"ok": False, "detail": "Not allowed."}, status=403)
    return JsonResponse(pa.test_connection(provider))
