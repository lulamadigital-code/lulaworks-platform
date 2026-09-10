"""Lulaworks Intelligence — the AI command centre (AI OS §17, §21).

One page that unifies what the AI layer knows and does: the exceptions that
need attention now, the predictions about what's coming, and the automations
watching in the background — every item clickable through to the real record.
It composes existing engines (attention, predictions, automations); it computes
nothing new of its own.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


@login_required
def ai_centre(request):
    if not request.user.has_perm_code("ai.generate"):
        messages.error(request, "AI features require the ai.generate permission.")
        return redirect("web:dashboard")

    company, user = request.user.active_company, request.user

    try:
        from apps.web.attention import attention_items
        attention = attention_items(company, user)
    except Exception:                                # noqa: BLE001
        attention = {"critical": [], "warning": [], "info": [], "total": 0,
                     "counts": {"critical": 0, "warning": 0}}

    try:
        from apps.ai_platform.predictions import predictions
        preds = predictions(company, user, limit=8)
    except Exception:                                # noqa: BLE001
        preds = []

    autos, recent_runs = [], []
    try:
        from apps.ai_platform.models import Automation, AutomationRun
        autos = list(Automation.objects.all()[:20])
        recent_runs = list(AutomationRun.objects.all()[:5])
    except Exception:                                # noqa: BLE001
        pass

    return render(request, "web/ai_centre.html", {
        "attention": attention,
        "attention_top": (attention["critical"] + attention["warning"])[:6],
        "predictions": preds,
        "automations": autos,
        "enabled_automations": [a for a in autos if a.enabled],
        "recent_runs": recent_runs,
    })
