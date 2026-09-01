"""seed_platform is the single source of the permission catalogue, role
templates and subscription plans. These lock in the launch-critical facts:
the Company Owner really can manage customers/CRM, and every plan grants AI
credits (so paid tenants aren't silently on 0)."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.billing.models import Plan
from apps.identity.models import Permission, Role


class SeedPlatformTests(TestCase):
    def setUp(self):
        call_command("seed_platform", stdout=StringIO())

    def test_customer_and_crm_permissions_exist(self):
        for code in ("customers.manage", "crm.manage"):
            self.assertTrue(Permission.objects.filter(codename=code).exists(),
                            f"{code} must be in the seeded catalogue")

    def test_company_owner_can_manage_customers_and_crm(self):
        owner = Role.objects.get(company=None, name="Company Owner")
        codes = set(owner.permissions.values_list("codename", flat=True))
        # Company Owner is the all-permissions role — it must include the new ones.
        self.assertIn("customers.manage", codes)
        self.assertIn("crm.manage", codes)

    def test_operations_manager_has_crm(self):
        ops = Role.objects.get(company=None, name="Operations Manager")
        codes = set(ops.permissions.values_list("codename", flat=True))
        self.assertIn("customers.manage", codes)
        self.assertIn("crm.manage", codes)

    def test_every_active_plan_grants_ai_credits(self):
        plans = Plan.objects.filter(is_active=True)
        self.assertTrue(plans.exists())
        for p in plans:
            self.assertGreater(p.monthly_ai_credits, 0,
                               f"plan {p.code} must grant monthly AI credits")

    def test_reseed_is_idempotent(self):
        before = Permission.objects.count(), Role.objects.count(), Plan.objects.count()
        call_command("seed_platform", stdout=StringIO())
        after = Permission.objects.count(), Role.objects.count(), Plan.objects.count()
        self.assertEqual(before, after)
