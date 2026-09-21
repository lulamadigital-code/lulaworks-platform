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
    """Start checkout for a plan. Payment goes through the gateway abstraction
    (apps.payments); on success the subscription is activated. The gateway is
    provider-agnostic — mock in dev, Stripe/PayFast/… in production."""
    if not _can_manage(request):
        messages.error(request, "You do not have permission to manage billing.")
        return redirect("web:billing")
    from apps.payments.services import begin_subscription_checkout
    plan_code = request.POST.get("plan_code", "")
    cycle = request.POST.get("billing_cycle", "monthly")
    if cycle not in dict(BillingCycle.choices):
        cycle = "monthly"
    # Contact-sales plans (price 0, e.g. Enterprise) are sold per contract, not
    # self-served — a platform admin sets them up with custom limits.
    from apps.billing.models import Plan
    _p = Plan.objects.filter(code=plan_code, is_active=True).first()
    if _p is not None and _p.price == 0 and _p.annual_price == 0:
        messages.info(request, "Enterprise is tailored to your team — contact "
                               "sales@lulaworks.com and we'll set you up.")
        return redirect("web:billing")
    try:
        session = begin_subscription_checkout(request, _company(request), plan_code, cycle)
    except Exception:
        messages.error(request, "That plan could not be selected. Please try again.")
        return redirect("web:billing")
    return redirect(session.url)


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
    """Start checkout for an AI credit pack (via the payment gateway); credits
    are added on successful payment."""
    if not _can_manage(request):
        messages.error(request, "You do not have permission to manage billing.")
        return redirect("web:billing")
    from apps.payments.services import begin_pack_checkout
    pack_code = request.POST.get("pack_code", "")
    try:
        session = begin_pack_checkout(request, _company(request), pack_code)
    except Exception:
        messages.error(request, "That credit pack could not be purchased.")
        return redirect("web:billing")
    return redirect(session.url)
