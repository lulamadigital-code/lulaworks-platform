"""Manager-web Billing pages — subscription, plan changes, AI credit packs.

Thin controllers over apps.billing.services (all the rules live there). Every
mutating action is gated on the `billing.manage` permission and is POST-only.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.billing.models import BillingCycle
from apps.billing.services import (
    cancel_subscription,
    change_plan,
    purchase_credit_pack,
    start_trial,
    subscription_overview,
)


def _company(request):
    return request.user.active_company


def _can_manage(request) -> bool:
    return request.user.has_perm_code("billing.manage")


@login_required
def billing(request):
    """The Billing page: current plan, usage, plan comparison, credit packs,
    history. A company with no subscription is placed on the free trial on first
    view (spec: every new company gets a trial, no card required)."""
    company = _company(request)
    if getattr(company, "subscription", None) is None:
        start_trial(company, actor=request.user)

    ctx = subscription_overview(company)
    # Which prices the comparison shows; defaults to the company's current cycle.
    selected_cycle = request.GET.get("cycle") or ctx.get("billing_cycle") or "monthly"
    if selected_cycle not in dict(BillingCycle.choices):
        selected_cycle = "monthly"
    ctx.update({
        "can_manage": _can_manage(request),
        "selected_cycle": selected_cycle,
        "current_plan_id": ctx["plan"].id if ctx["plan"] else None,
        "current_tier": ctx["plan"].tier if ctx["plan"] else -1,
    })
    return render(request, "web/billing.html", ctx)


@login_required
@require_POST
def billing_change_plan(request):
    if not _can_manage(request):
        messages.error(request, "You do not have permission to manage billing.")
        return redirect("web:billing")
    plan_code = request.POST.get("plan_code", "")
    cycle = request.POST.get("billing_cycle", "monthly")
    if cycle not in dict(BillingCycle.choices):
        cycle = "monthly"
    try:
        sub = change_plan(company := _company(request), plan_code, cycle, actor=request.user)
    except Exception:
        messages.error(request, "That plan could not be selected. Please try again.")
        return redirect("web:billing")
    verb = "upgraded" if sub else "changed"
    messages.success(request, f"Plan {verb} to {sub.plan.name} ({sub.get_billing_cycle_display()}).")
    if sub.is_over_limit:
        messages.warning(
            request,
            "You're above the new plan's limits — your data is safe, but adding "
            "users is blocked until you're back within the limit.",
        )
    return redirect("web:billing")


@login_required
@require_POST
def billing_cancel(request):
    if not _can_manage(request):
        messages.error(request, "You do not have permission to manage billing.")
        return redirect("web:billing")
    cancel_subscription(_company(request), actor=request.user)
    messages.success(
        request,
        "Subscription will cancel at the end of the current billing period. "
        "Your data stays intact.",
    )
    return redirect("web:billing")


@login_required
@require_POST
def billing_buy_credits(request):
    if not _can_manage(request):
        messages.error(request, "You do not have permission to manage billing.")
        return redirect("web:billing")
    pack_code = request.POST.get("pack_code", "")
    try:
        pack = purchase_credit_pack(_company(request), pack_code, actor=request.user)
    except Exception:
        messages.error(request, "That credit pack could not be purchased.")
        return redirect("web:billing")
    messages.success(request, f"{pack.name} added — {pack.credits:.0f} AI credits are now available.")
    return redirect("web:billing")
