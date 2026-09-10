"""AI Automations console (AI OS §15) — create "when X, do Y" rules and run
them. Safe actions run; high-risk actions are proposed for approval."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.ai_platform.automations import run_all, run_automation
from apps.ai_platform.models import Automation


def _can(user):
    return user.has_perm_code("ai.generate")


@login_required
def automations(request):
    if not _can(request.user):
        messages.error(request, "AI features require the ai.generate permission.")
        return redirect("web:dashboard")
    rules = list(Automation.objects.all().prefetch_related("runs"))
    return render(request, "web/automations.html", {
        "rules": rules,
        "triggers": Automation.Trigger.choices,
        "actions": Automation.Action.choices})


@login_required
@require_POST
def automation_create(request):
    if not _can(request.user):
        messages.error(request, "AI features require the ai.generate permission.")
        return redirect("web:dashboard")
    name = (request.POST.get("name") or "").strip()
    trigger = request.POST.get("trigger")
    action = request.POST.get("action")
    valid_t = {t for t, _ in Automation.Trigger.choices}
    valid_a = {a for a, _ in Automation.Action.choices}
    if not name or trigger not in valid_t or action not in valid_a:
        messages.error(request, "Give the automation a name, a trigger and an action.")
        return redirect("web:automations")
    Automation.objects.create(name=name, trigger=trigger, action=action,
                              created_by=request.user, updated_by=request.user)
    messages.success(request, f"Automation “{name}” created.")
    return redirect("web:automations")


@login_required
@require_POST
def automation_run(request, pk):
    if not _can(request.user):
        messages.error(request, "AI features require the ai.generate permission.")
        return redirect("web:dashboard")
    rule = get_object_or_404(Automation.objects.all(), pk=pk)
    run = run_automation(rule, request.user)
    if run.result == "awaiting_approval":
        messages.success(request, f"“{rule.name}” prepared {run.matches} item(s) for your approval.")
    elif run.matches:
        messages.success(request, f"“{rule.name}” handled {run.matches} item(s).")
    else:
        messages.info(request, f"“{rule.name}” ran — nothing to act on right now.")
    return redirect("web:automations")


@login_required
@require_POST
def automation_toggle(request, pk):
    if not _can(request.user):
        messages.error(request, "AI features require the ai.generate permission.")
        return redirect("web:dashboard")
    rule = get_object_or_404(Automation.objects.all(), pk=pk)
    rule.enabled = not rule.enabled
    rule.save(update_fields=["enabled", "updated_at"])
    messages.success(request, f"“{rule.name}” {'enabled' if rule.enabled else 'paused'}.")
    return redirect("web:automations")


@login_required
@require_POST
def automations_run_all(request):
    if not _can(request.user):
        messages.error(request, "AI features require the ai.generate permission.")
        return redirect("web:dashboard")
    runs = run_all(request.user.active_company, request.user)
    handled = sum(r.matches for r in runs)
    messages.success(request, f"Ran {len(runs)} automation(s) — {handled} item(s) handled.")
    return redirect("web:automations")
