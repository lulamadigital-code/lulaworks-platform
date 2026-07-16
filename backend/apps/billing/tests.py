from decimal import Decimal

from django.test import TestCase

from apps.identity.models import Company

from .models import Plan, Subscription
from .services import check_module, check_user_seat


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
