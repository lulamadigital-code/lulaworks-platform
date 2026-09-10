"""AI Automations (§15) — user rules that act on live signals; safe actions run,
high-risk actions are proposed for approval, never executed."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.ai_platform import automations as au
from apps.ai_platform.models import Automation, AutomationRun
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class AutomationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["ai.generate", "procurement.manage"], "m@a.co")
        from apps.procurement.models import Supplier, SupplierPrice
        with tenant_scope(self.company.id):
            s = Supplier.objects.create(company=self.company, name="Hydraulics SA")
            for yr, price in [(2024, "100"), (2025, "120"), (2026, "150")]:
                SupplierPrice.objects.create(
                    company=self.company, supplier=s, item_key="hydraulic pipe",
                    description="Hydraulic pipe", unit="m", unit_price=Decimal(price),
                    date=date(yr, 3, 1))

    def test_notify_action_runs_and_logs(self):
        with tenant_scope(self.company.id):
            rule = Automation.objects.create(
                company=self.company, name="Warn on price rises",
                trigger=Automation.Trigger.PRICE_RISE,
                action=Automation.Action.NOTIFY_ME, created_by=self.mgr)
            run = au.run_automation(rule, self.mgr)
            self.assertEqual(run.result, AutomationRun.Result.DONE)
            self.assertGreaterEqual(run.matches, 1)
            rule.refresh_from_db()
            self.assertEqual(rule.run_count, 1)
            self.assertIsNotNone(rule.last_run)

    def test_high_risk_action_awaits_approval_not_executed(self):
        with tenant_scope(self.company.id):
            rule = Automation.objects.create(
                company=self.company, name="Follow up on price rises",
                trigger=Automation.Trigger.PRICE_RISE,
                action=Automation.Action.PROPOSE_FOLLOWUP, created_by=self.mgr)
            run = au.run_automation(rule, self.mgr)
            self.assertEqual(run.result, AutomationRun.Result.AWAITING_APPROVAL)
            # The proposal is not executed by the AI.
            item = run.detail["items"][0]
            self.assertTrue(item["awaiting_approval"])
            self.assertFalse(item["proposal"]["executed_by_ai"])

    def test_no_signal_is_nothing_to_do(self):
        with tenant_scope(self.company.id):
            rule = Automation.objects.create(
                company=self.company, name="Repeat customers",
                trigger=Automation.Trigger.CUSTOMER_DUE,
                action=Automation.Action.NOTIFY_ME, created_by=self.mgr)
            run = au.run_automation(rule, self.mgr)
            self.assertEqual(run.result, AutomationRun.Result.NOTHING)
            self.assertEqual(run.matches, 0)
