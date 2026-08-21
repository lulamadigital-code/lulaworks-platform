"""Payment service — the single seam between billing and any provider.

Billing calls begin_subscription_checkout / begin_pack_checkout and gets a URL
to send the customer to. When the provider confirms payment (a local return for
the mock gateway, a verified webhook for Stripe), complete_intent applies the
billing effect. Nothing in billing knows which provider was used.
"""

from decimal import Decimal

from django.db import transaction

from .gateways import get_gateway
from .models import CheckoutIntent, GatewayEvent


def _abs(request, viewname, *args):
    from django.urls import reverse
    return request.build_absolute_uri(reverse(viewname, args=args))


@transaction.atomic
def begin_subscription_checkout(request, company, plan_code, billing_cycle="monthly",
                                currency=None, gateway_code=None):
    """Create a pending subscription purchase and return the hosted-checkout URL."""
    from apps.billing.models import Plan
    plan = Plan.objects.get(code=plan_code, is_active=True)
    currency = currency or getattr(company, "currency", None) or "ZAR"
    gw = get_gateway(gateway_code)
    intent = CheckoutIntent.objects.create(
        company=company, kind=CheckoutIntent.Kind.SUBSCRIPTION, gateway=gw.code,
        plan_code=plan_code, billing_cycle=billing_cycle, currency=currency,
        amount=plan.price_in(currency, billing_cycle),
        description=f"Lulaworks {plan.name} ({billing_cycle})",
    )
    session = gw.create_checkout(
        intent=intent,
        success_url=_abs(request, "payments:return", intent.id),
        cancel_url=_abs(request, "payments:cancel", intent.id),
    )
    if session.external_id:
        intent.external_session_id = session.external_id
        intent.save(update_fields=["external_session_id", "updated_at"])
    return session


@transaction.atomic
def begin_pack_checkout(request, company, pack_code, gateway_code=None):
    """Create a pending credit-pack purchase and return the checkout URL."""
    from apps.billing.models import CreditPack
    pack = CreditPack.objects.get(code=pack_code, is_active=True)
    gw = get_gateway(gateway_code)
    intent = CheckoutIntent.objects.create(
        company=company, kind=CheckoutIntent.Kind.CREDIT_PACK, gateway=gw.code,
        pack_code=pack_code, currency=getattr(company, "currency", None) or "ZAR",
        amount=Decimal(pack.price), description=f"{pack.name}",
    )
    session = gw.create_checkout(
        intent=intent,
        success_url=_abs(request, "payments:return", intent.id),
        cancel_url=_abs(request, "payments:cancel", intent.id),
    )
    if session.external_id:
        intent.external_session_id = session.external_id
        intent.save(update_fields=["external_session_id", "updated_at"])
    return session


@transaction.atomic
def complete_intent(intent: CheckoutIntent, actor=None):
    """Apply the billing effect of a paid intent. Idempotent — a repeat call (a
    replayed webhook, a refreshed return page) is a no-op."""
    from apps.billing.services import change_plan, purchase_credit_pack
    if intent.status == CheckoutIntent.Status.COMPLETED:
        return intent
    if intent.kind == CheckoutIntent.Kind.SUBSCRIPTION:
        change_plan(intent.company, intent.plan_code, intent.billing_cycle,
                    currency=intent.currency, actor=actor)
    elif intent.kind == CheckoutIntent.Kind.CREDIT_PACK:
        purchase_credit_pack(intent.company, intent.pack_code, actor=actor)
    intent.status = CheckoutIntent.Status.COMPLETED
    intent.save(update_fields=["status", "updated_at"])
    return intent


def cancel_intent(intent: CheckoutIntent):
    if intent.status == CheckoutIntent.Status.PENDING:
        intent.status = CheckoutIntent.Status.CANCELLED
        intent.save(update_fields=["status", "updated_at"])
    return intent


@transaction.atomic
def process_webhook(gateway_code: str, payload: bytes, headers: dict):
    """Verify + handle a provider webhook. Idempotent via GatewayEvent."""
    gw = get_gateway(gateway_code)
    event = gw.parse_webhook(payload, headers)   # raises on bad signature
    if event.kind != "checkout.completed" or not event.intent_ref:
        return {"handled": False, "reason": event.kind}

    # Record-once: skip if we've already processed this provider event.
    rec, created = GatewayEvent.objects.get_or_create(
        gateway=gw.code, external_event_id=event.external_id or event.intent_ref,
        defaults={"kind": event.kind},
    )
    if not created and rec.processed:
        return {"handled": True, "duplicate": True}

    intent = CheckoutIntent.objects.filter(id=event.intent_ref).first()
    if intent is not None:
        complete_intent(intent)
    rec.processed = True
    rec.save(update_fields=["processed", "updated_at"])
    return {"handled": True, "intent": str(event.intent_ref)}
