from decimal import Decimal

from django.test import TestCase

from apps.ai_platform.gateway import credit_balance
from apps.identity.models import Company, Membership, User

from .models import CreditPack, Plan, Subscription, SubscriptionStatus
from .services import (
    GB,
    can_add_user,
    cancel_subscription,
    change_plan,
    check_module,
    check_user_seat,
    purchase_credit_pack,
    renew_cycle,
    start_trial,
    subscription_overview,
)


class EntitlementTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama", max_users=10)
        self.plan = Plan.objects.create(
            code="business", name="Business", price=Decimal("799"), max_users=15,
            module_entitlements=["accounting", "dashboards"],
        )
        self.sub = Subscription.objects.create(company=self.company, plan=self.plan, seats=15)

    def test_seat_within_limit(self):
        self.assertTrue(check_user_seat(self.company, 5).allowed)

    def test_seat_warns_near_limit(self):
        result = check_user_seat(self.company, 14)  # 14/15 ≥ 90%
        self.assertTrue(result.allowed)
        self.assertTrue(result.warn)

    def test_seat_blocks_at_limit(self):
        result = check_user_seat(self.company, 15)
        self.assertFalse(result.allowed)
        self.assertIn("upgrade", result.reason.lower())

    def test_subscription_override_beats_plan(self):
        self.sub.overrides = {"max_users": 3}
        self.sub.save()
        self.assertFalse(check_user_seat(self.company, 3).allowed)

    def test_module_entitlement(self):
        self.assertTrue(check_module(self.company, "accounting").allowed)
        self.assertFalse(check_module(self.company, "payroll").allowed)

    def test_result_status_string(self):
        self.assertEqual(check_user_seat(self.company, 5).status, "allow")
        self.assertEqual(check_user_seat(self.company, 15).status, "block")


def _seed_plans():
    starter = Plan.objects.create(
        code="starter", name="Starter", tier=1, price=Decimal("299"),
        annual_price=Decimal("2990"), max_users=2, storage_quota_bytes=5 * GB,
        monthly_ai_credits=Decimal("300"))
    pro = Plan.objects.create(
        code="professional", name="Professional", tier=2, is_popular=True,
        price=Decimal("1299"), annual_price=Decimal("12990"), max_users=10,
        storage_quota_bytes=50 * GB, monthly_ai_credits=Decimal("2000"))
    biz = Plan.objects.create(
        code="business", name="Business", tier=3, price=Decimal("3999"),
        annual_price=Decimal("39990"), max_users=50, storage_quota_bytes=200 * GB,
        monthly_ai_credits=Decimal("8000"))
    CreditPack.objects.create(code="pack_500", name="500 AI Credits",
                              credits=Decimal("500"), price=Decimal("199"))
    return starter, pro, biz


class SubscriptionLifecycleTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lula", max_users=4)
        self.starter, self.pro, self.biz = _seed_plans()

    def _members(self, n):
        for i in range(n):
            u = User.objects.create(email=f"u{i}@lula.co")
            Membership.objects.create(company=self.company, user=u, status="active")

    def test_start_trial_grants_pro_features_capped(self):
        sub = start_trial(self.company)
        self.company.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.TRIAL)
        self.assertEqual(sub.plan, self.pro)
        self.assertEqual(self.company.max_users, 2)
        self.assertEqual(self.company.storage_quota_bytes, 2 * GB)
        self.assertEqual(credit_balance(self.company), Decimal("100"))
        # Idempotent — a second call doesn't re-grant credits.
        again = start_trial(self.company)
        self.assertEqual(again.pk, sub.pk)
        self.assertEqual(credit_balance(self.company), Decimal("100"))

    def test_annual_saving_is_about_two_months(self):
        # 1299*12 - 12990 = 2598 ≈ two months.
        self.assertEqual(self.pro.annual_saving, Decimal("2598"))

    def test_upgrade_raises_limits_and_credits(self):
        change_plan(self.company, "starter")
        self.assertEqual(credit_balance(self.company), Decimal("300"))
        change_plan(self.company, "professional")
        self.company.refresh_from_db()
        self.assertEqual(self.company.max_users, 10)
        self.assertEqual(self.company.storage_quota_bytes, 50 * GB)
        self.assertGreaterEqual(credit_balance(self.company), Decimal("2000"))
        self.assertEqual(self.company.subscription.status, SubscriptionStatus.ACTIVE)

    def test_downgrade_keeps_data_but_blocks_new_users(self):
        change_plan(self.company, "business")   # 50 users
        self._members(3)
        change_plan(self.company, "starter")    # 2 users → over limit
        self.company.refresh_from_db()
        self.assertTrue(self.company.subscription.is_over_limit)
        self.assertFalse(can_add_user(self.company).allowed)          # new users blocked
        self.assertEqual(Membership.objects.filter(company=self.company).count(), 3)  # data kept

    def test_annual_cycle_sets_year_long_period(self):
        sub = change_plan(self.company, "professional", billing_cycle="annual")
        self.assertEqual(sub.billing_cycle, "annual")
        span = (sub.current_period_end - sub.current_period_start).days
        self.assertGreaterEqual(span, 365)

    def test_credit_pack_adds_immediately(self):
        change_plan(self.company, "starter")     # balance 300
        purchase_credit_pack(self.company, "pack_500")
        self.assertEqual(credit_balance(self.company), Decimal("800"))

    def test_renew_resets_credits_to_monthly(self):
        change_plan(self.company, "starter")      # 300
        purchase_credit_pack(self.company, "pack_500")  # 800
        renew_cycle(self.company)
        self.assertEqual(credit_balance(self.company), Decimal("300"))

    def test_cancel_is_graceful(self):
        change_plan(self.company, "professional")
        cancel_subscription(self.company)
        self.company.refresh_from_db()
        self.assertTrue(self.company.subscription.cancel_at_period_end)
        # Access (data) untouched — status still active until period end.
        self.assertEqual(self.company.subscription.status, SubscriptionStatus.ACTIVE)

    def test_overview_shape(self):
        start_trial(self.company)
        ov = subscription_overview(self.company)
        self.assertEqual(len(ov["plans"]), 3)
        self.assertEqual(len(ov["packs"]), 1)
        self.assertEqual(ov["user_limit"], 2)
        self.assertTrue(ov["is_trialing"])
        self.assertEqual(ov["credits_remaining"], Decimal("100"))
        self.assertIn("pct", ov["storage"])
