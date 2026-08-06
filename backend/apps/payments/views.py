"""Payment endpoints: the post-checkout return/cancel, the mock hosted-checkout
page (test gateway), and the provider webhook receiver."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import CheckoutIntent
from .services import cancel_intent, complete_intent, process_webhook


def _own_intent(request, intent_id):
    """The intent, only if it belongs to the signed-in user's company."""
    intent = get_object_or_404(CheckoutIntent, id=intent_id)
    if intent.company_id != getattr(request.user, "active_company_id", None):
        return None
    return intent


@login_required
def mock_checkout(request, intent_id):
    """Stand-in for a provider's hosted checkout page (test gateway only)."""
    intent = _own_intent(request, intent_id)
    if intent is None:
        return redirect("web:billing")
    return render(request, "payments/mock_checkout.html", {"intent": intent})


@login_required
def payment_return(request, intent_id):
    """Success return from checkout. SECURITY: the return URL is user-reachable,
    so we verify payment with the provider before activating anything; the signed
    webhook is the other authoritative path. Both call the idempotent
    complete_intent, so whichever lands first wins and the second is a no-op."""
    from .gateways import get_gateway
    intent = _own_intent(request, intent_id)
    if intent is None:
        return redirect("web:billing")
    if get_gateway(intent.gateway).confirm_payment(intent):
        complete_intent(intent, actor=request.user)
        messages.success(request, "Payment successful — your account has been updated.")
    else:
        messages.info(
            request,
            "Thanks! We're confirming your payment with the provider — your "
            "account will update as soon as it clears.",
        )
    return redirect("web:billing")


@login_required
def payment_cancel(request, intent_id):
    intent = _own_intent(request, intent_id)
    if intent is not None:
        cancel_intent(intent)
    messages.info(request, "Checkout cancelled — no changes were made.")
    return redirect("web:billing")


@csrf_exempt
@require_POST
def webhook(request, gateway):
    """Provider → server webhook. Signature is verified inside process_webhook;
    a bad signature returns 400."""
    try:
        result = process_webhook(gateway, request.body, request.META)
    except Exception as exc:  # invalid signature / malformed payload
        return HttpResponseBadRequest(f"webhook error: {exc}")
    return HttpResponse("ok" if result.get("handled") else "ignored")
