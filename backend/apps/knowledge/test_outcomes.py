"""P1 §20 — factual job outcomes/problems (facts, never subjective labels),
money-gated."""
from datetime import date, timedelta

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.execution.models import (ReportKind, Task, TaskReport,
                                   TaskResourceAllocation, AllocationKind)
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.business_history import outcomes
from apps.projects.models import Project


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class JobOutcomesTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.finance = _user(self.c, ["finance.view_money"], "cfo@acme.co")
        self.field = _user(self.c, ["projects.view"], "worker@acme.co")
        with tenant_scope(self.c.id):
            self.p = Project.objects.create(
                company=self.c, number="JOB-1", client_name="ABC Mining",
                due_date=date.today() - timedelta(days=5), budget_amount=1000)
            t = Task.objects.create(company=self.c, project=self.p, name="Strip pump")
            for _ in range(2):
                TaskReport.objects.create(company=self.c, task=t,
                                          kind=ReportKind.INCIDENT, title="Near miss")
            TaskReport.objects.create(company=self.c, task=t,
                                      kind=ReportKind.DELAY, title="Waiting for crane")
            TaskResourceAllocation.objects.create(
                company=self.c, task=t, kind=AllocationKind.CASH_ADVANCE,
                is_monetary=True, amount_allocated=1000, amount_spent=1500)

    def _types(self, user):
        with tenant_scope(self.c.id):
            return {o["type"]: o for o in outcomes(self.p, user)}

    def test_facts_are_counted_not_labelled(self):
        t = self._types(self.finance)
        self.assertEqual(t["incident"]["count"], 2)
        self.assertEqual(t["delay"]["count"], 1)
        self.assertIn("overdue", t)                       # past due, not complete
        self.assertEqual(t["overdue"]["days"], 5)
        self.assertIn("over_budget", t)                   # 1500 spent vs 1000 budget
        self.assertEqual(t["over_budget"]["spent"], "1500.00")
        # No subjective labels — only typed facts.
        for o in t.values():
            self.assertNotIn("bad", o["label"].lower())

    def test_over_budget_withheld_without_finance(self):
        t = self._types(self.field)
        self.assertIn("incident", t)                      # operational facts stay
        self.assertIn("overdue", t)
        self.assertNotIn("over_budget", t)                # money fact withheld

    def test_non_job_has_no_outcomes(self):
        from apps.customers.models import Customer
        with tenant_scope(self.c.id):
            cust = Customer.objects.create(company=self.c, name="ABC Mining")
            self.assertEqual(outcomes(cust, self.finance), [])
