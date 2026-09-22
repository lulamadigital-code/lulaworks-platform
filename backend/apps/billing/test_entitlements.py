"""Plan → entitlement matrix + negative enforcement (the plan actually controls
what the customer can use, resolved from the seeded configuration)."""
from django.core.management import call_command
from django.test import TestCase

from apps.core.context import system_scope


class EntitlementMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with system_scope():
            call_command("seed_platform")

    def _company_on(self, code):
        from apps.billing.models import Plan, Subscription, SubscriptionStatus
        from apps.identity.models import Company
        with system_scope():
            co = Company.objects.create(name=f"Co {code}", currency="ZAR")
            plan = Plan.objects.get(code=code)
            Subscription.objects.create(company=co, plan=plan, currency="ZAR",
                                        status=SubscriptionStatus.ACTIVE)
            co.refresh_from_db()
            return co

    def test_matrix_from_seeded_config(self):
        from apps.billing.entitlements import entitlements_for
        # The contract each plan must honour (a representative slice per tier).
        expected = {
            "starter": {"basic_procurement": True, "ai_extraction": False,
                        "supplier_intelligence": False, "approval_workflows": False,
                        "sso": False, "api_access": False},
            "professional": {"basic_procurement": True, "ai_extraction": True,
                             "supplier_intelligence": True, "gps_checkin": True,
                             "approval_workflows": False, "sso": False},
            "business": {"ai_extraction": True, "approval_workflows": True,
                         "compliance_management": True, "procurement_analytics": True,
                         "sso": False, "api_access": False},
            "enterprise": {"ai_extraction": True, "approval_workflows": True,
                           "sso": True, "api_access": True, "audit_log": True},
        }
        with system_scope():
            for code, caps in expected.items():
                ent = entitlements_for(self._company_on(code))
                for cap, want in caps.items():
                    self.assertEqual(ent.has(cap), want, f"{code}.{cap} should be {want}")

    def test_inheritance(self):
        from apps.billing.entitlements import entitlements_for
        with system_scope():
            # Business inherits Professional inherits Starter.
            biz = entitlements_for(self._company_on("business"))
            for cap in ("basic_procurement", "ai_extraction", "supplier_intelligence",
                        "gps_checkin", "compliance_management"):
                self.assertTrue(biz.has(cap), f"business should inherit {cap}")

    def test_limits_resolve(self):
        from apps.billing.entitlements import entitlements_for
        with system_scope():
            pro = entitlements_for(self._company_on("professional"))
            self.assertEqual(pro.limit("users"), 10)
            self.assertEqual(pro.limit("storage.gb"), 50)
            self.assertEqual(pro.limit("ai.credits.monthly"), 2000)

    def test_gateway_blocks_extraction_on_starter(self):
        from apps.ai_platform.gateway import run_task
        from apps.billing.entitlements import PlanFeatureRequired
        with system_scope():
            starter = self._company_on("starter")
            # Extraction task is Professional+ → blocked even before credits.
            with self.assertRaises(PlanFeatureRequired):
                run_task(starter, None, "quotation_scope_extraction", "doc text")

    def test_gateway_allows_extraction_on_professional(self):
        from apps.ai_platform.gateway import run_task
        from apps.billing.entitlements import PlanFeatureRequired
        from apps.ai_platform.gateway import InsufficientCreditsError
        with system_scope():
            pro = self._company_on("professional")
            # Not a PlanFeatureRequired (may fail later on credits/providers — that's
            # fine; the point is the FEATURE gate does not block Professional).
            try:
                run_task(pro, None, "quotation_scope_extraction", "doc text")
            except PlanFeatureRequired:
                self.fail("Professional must not be blocked from AI extraction")
            except (InsufficientCreditsError, Exception):
                pass
