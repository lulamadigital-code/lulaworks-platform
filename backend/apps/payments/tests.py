"""Payments: gateway abstraction + the checkout→complete flow.

Uses the mock gateway (default) so the whole money path is exercised with no
external calls — the same path Stripe would drive in production.
"""

from decimal import Decimal

from django.test import RequestFactory, TestCase, override_settings

from apps.ai_platform.gateway import credit_balance
from django.test import Client

from apps.billing.models import CreditPack, Plan, PlanPrice, Subscription, SubscriptionStatus
from apps.billing.services import GB
from apps.identity.models import Company, Membership, User

from .gateways import available_gateways, get_gateway
from .models import CheckoutIntent
from .services import (
    begin_subscription_checkout,
    complete_intent,
    process_webhook,
)


def _plans():
    Plan.objects.create(code="starter", name="Starter", tier=1, price=Decimal("299"),
                        annual_price=Decimal("2990"), max_users=2,
                        storage_quota_bytes=5 * GB, monthly_ai_credits=Decimal("300"))
    pro = Plan.objects.create(code="professional", name="Professional", tier=2,
                              price=Decimal("1299"), annual_price=Decimal("12990"),
                              max_users=10, storage_quota_bytes=50 * GB,
                              monthly_ai_credits=Decimal("2000"))
    PlanPrice.objects.create(plan=pro, currency="USD", monthly=Decimal("79"), annual=Decimal("790"))
    CreditPack.objects.create(code="pack_500", name="500 AI Credits",
                              credits=Decimal("500"), price=Decimal("199"))
    return pro


class GatewayRegistryTests(TestCase):
    def test_defaults_to_mock(self):
        self.assertEqual(get_gateway().code, "mock")

    @override_settings(PAYMENT_GATEWAY="stripe")
    def test_selects_configured_gateway(self):
        self.assertEqual(get_gateway().code, "stripe")

    def test_explicit_code_wins(self):
        self.assertEqual(get_gateway("mock").code, "mock")


class CheckoutFlowTests(TestCase):
    def setUp(self):
        self.pro = _plans()
        self.company = Company.objects.create(name="US Co", currency="USD")
        self.rf = RequestFactory()

    def _request(self):
        r = self.rf.get("/billing/")
        return r

    def test_begin_subscription_checkout_creates_intent_and_url(self):
        session = begin_subscription_checkout(self._request(), self.company, "professional", "monthly")
        self.assertTrue(session.url)                       # a place to pay
        intent = CheckoutIntent.objects.get()
        self.assertEqual(intent.kind, CheckoutIntent.Kind.SUBSCRIPTION)
        self.assertEqual(intent.currency, "USD")
        self.assertEqual(intent.amount, Decimal("79"))     # priced in the company's currency
        self.assertEqual(intent.status, CheckoutIntent.Status.PENDING)

    def test_completing_subscription_intent_activates_and_bills_in_currency(self):
        begin_subscription_checkout(self._request(), self.company, "professional", "monthly")
        intent = CheckoutIntent.objects.get()
        complete_intent(intent)
        intent.refresh_from_db()
        self.company.refresh_from_db()
        sub = self.company.subscription
        self.assertEqual(intent.status, CheckoutIntent.Status.COMPLETED)
        self.assertEqual(sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(sub.plan, self.pro)
        self.assertEqual(sub.currency, "USD")
        self.assertEqual(sub.price, Decimal("79"))
        self.assertGreaterEqual(credit_balance(self.company), Decimal("2000"))

    def test_complete_is_idempotent(self):
        begin_subscription_checkout(self._request(), self.company, "professional", "monthly")
        intent = CheckoutIntent.objects.get()
        complete_intent(intent)
        first = credit_balance(self.company)
        complete_intent(intent)          # replayed webhook / refreshed return page
        self.assertEqual(credit_balance(self.company), first)   # no double credit

    def test_credit_pack_intent_adds_credits(self):
        intent = CheckoutIntent.objects.create(
            company=self.company, kind=CheckoutIntent.Kind.CREDIT_PACK, gateway="mock",
            pack_code="pack_500", currency="USD", amount=Decimal("199"))
        complete_intent(intent)
        self.assertEqual(credit_balance(self.company), Decimal("500"))

    def test_mock_webhook_is_noop(self):
        # The mock gateway completes via the local return, not a webhook.
        result = process_webhook("mock", b"{}", {})
        self.assertFalse(result["handled"])


class PaystackAndSecurityTests(TestCase):
    def setUp(self):
        self.pro = _plans()
        self.company = Company.objects.create(name="Naija Co", currency="ZAR")

    def test_paystack_is_registered(self):
        self.assertIn("paystack", available_gateways())
        self.assertIn("stripe", available_gateways())
        self.assertEqual(get_gateway("paystack").code, "paystack")

    def test_confirm_payment_denies_by_default(self):
        # No provider session recorded → Stripe verification returns False
        # without any external call (early return).
        intent = CheckoutIntent.objects.create(
            company=self.company, kind=CheckoutIntent.Kind.SUBSCRIPTION, gateway="stripe",
            plan_code="professional", billing_cycle="monthly", currency="ZAR", amount=1299)
        self.assertFalse(get_gateway("stripe").confirm_payment(intent))

    @override_settings(PAYSTACK_SECRET_KEY="sk_test_x")
    def test_paystack_webhook_rejects_forged_signature(self):
        with self.assertRaises(ValueError):
            get_gateway("paystack").parse_webhook(b'{"event":"charge.success"}',
                                                  {"HTTP_X_PAYSTACK_SIGNATURE": "wrong"})

    def test_return_url_cannot_grant_subscription_without_payment(self):
        """SECURITY: hitting the success/return URL without the provider
        confirming payment must NOT activate a subscription."""
        user = User.objects.create_user(email="owner@co.za", password="pw12345!")
        user.active_company = self.company
        user.save(update_fields=["active_company"])
        Membership.objects.create(company=self.company, user=user, status="active")
        # Stripe intent with no session recorded → confirm_payment() is False.
        intent = CheckoutIntent.objects.create(
            company=self.company, kind=CheckoutIntent.Kind.SUBSCRIPTION, gateway="stripe",
            plan_code="professional", billing_cycle="monthly", currency="ZAR", amount=1299)
        client = Client()
        client.force_login(user)
        client.get(f"/billing/checkout/{intent.id}/return/")
        intent.refresh_from_db()
        self.assertEqual(intent.status, CheckoutIntent.Status.PENDING)     # not completed
        self.assertFalse(Subscription.objects.filter(company=self.company).exists())  # no sub granted
