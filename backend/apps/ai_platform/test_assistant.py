"""LulaAI assistant tests: deterministic intent routing over permission-checked,
tenant-scoped tools — grounded answers, permission denial, and no hallucination."""
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.execution.models import Task
from apps.identity.models import Company, Membership, Permission, Role, User


def _company(name="Lula AI"):
    return Company.objects.create(name=name)


def _user_with(company, codenames, email="ai@lula.co.za"):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for code in codenames:
        p, _ = Permission.objects.get_or_create(
            codename=code, defaults={"module": "x", "label": code})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ClassifyTests(APITestCase):
    def test_intents(self):
        from apps.ai_platform.assistant import classify
        self.assertEqual(classify("Show me overdue tasks")[0], "overdue_tasks")
        self.assertEqual(classify("Which invoices are unpaid?")[0], "unpaid_invoices")
        self.assertEqual(classify("quotations awaiting approval")[0],
                         "quotations_awaiting_approval")
        self.assertEqual(classify("Supplier price for hydraulic pipe")[0], "supplier_prices")
        i, p = classify("Which customers haven't been contacted in 45 days?")
        self.assertEqual(i, "uncontacted_customers")
        self.assertEqual(p["days"], 45)
        self.assertEqual(classify("what is the weather")[0], "unknown")


class AskGroundedTests(APITestCase):
    def test_overdue_tasks_answered_from_data(self):
        from apps.ai_platform.assistant import ask
        c = _company()
        with tenant_scope(c.id):
            u = _user_with(c, ["ai.generate", "projects.view"])
            Task.objects.create(company=c, name="Late job",
                                due_date=timezone.localdate() - timedelta(days=2))
            res = ask(c, u, "show me overdue tasks")
        self.assertEqual(res["intent"], "overdue_tasks")
        self.assertEqual(len(res["items"]), 1)
        self.assertIn("Tasks", res["sources"])
        self.assertFalse(res.get("denied"))

    def test_not_found_never_invents(self):
        from apps.ai_platform.assistant import ask
        c = _company()
        with tenant_scope(c.id):
            u = _user_with(c, ["ai.generate", "projects.view"], email="np@lula.co.za")
            res = ask(c, u, "show me overdue tasks")     # none exist
        self.assertEqual(res["items"], [])
        self.assertIn("couldn't find", res["answer"].lower())


class AskPermissionTests(APITestCase):
    def test_money_question_denied_without_permission(self):
        from apps.ai_platform.assistant import ask
        c = _company()
        with tenant_scope(c.id):
            # has AI + projects, but NOT finance.view_money
            u = _user_with(c, ["ai.generate", "projects.view"], email="emp@lula.co.za")
            res = ask(c, u, "which invoices are unpaid?")
        self.assertTrue(res["denied"])
        self.assertIn("access", res["answer"].lower())

    def test_capabilities_reflect_permissions(self):
        from apps.ai_platform.assistant import ask
        c = _company()
        with tenant_scope(c.id):
            u = _user_with(c, ["ai.generate", "projects.view"], email="cap@lula.co.za")
            res = ask(c, u, "hello what can you do")
        self.assertEqual(res["intent"], "unknown")
        self.assertIn("overdue", res["answer"].lower())
        # a projects-only user is never offered supplier/invoice capabilities
        self.assertNotIn("supplier", res["answer"].lower())
        self.assertNotIn("invoice", res["answer"].lower())
