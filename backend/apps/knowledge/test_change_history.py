"""P1 §19 — change history: a record's own change/activity log, reconstructed
from the DomainEvent backbone, actor-attributed and money-gated."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.core.events import publish
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.business_history import change_history, history_for
from apps.quotes.models import Quotation


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ChangeHistoryTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.finance = _user(self.c, ["finance.view_money"], "cfo@acme.co")
        self.field = _user(self.c, ["projects.view"], "worker@acme.co")
        with tenant_scope(self.c.id):
            self.q = Quotation.objects.create(company=self.c, number="QTN-1",
                                              client_name="ABC Mining")
        publish("QuotationStatusChanged", company=self.c, subject=self.q,
                actor=self.finance, payload={"from": "draft", "to": "sent"})
        publish("VariationApproved", company=self.c, subject=self.q,
                actor=self.finance, payload={"number": "VAR-1", "revenue_impact": "40000"})

    def test_reconstructs_changes_with_actor_and_readable_summary(self):
        with tenant_scope(self.c.id):
            rows = change_history(self.q, self.finance)
        self.assertEqual(len(rows), 2)
        summaries = [r["summary"] for r in rows]
        self.assertIn("Status: draft → sent", summaries)
        self.assertTrue(any("Variation VAR-1 approved" in s for s in summaries))
        self.assertTrue(all(r["actor"] == "cfo@acme.co" for r in rows))

    def test_money_fields_withheld_without_finance(self):
        with tenant_scope(self.c.id):
            rows = change_history(self.q, self.field)
        var = next(r for r in rows if r["type"] == "VariationApproved")
        self.assertNotIn("revenue_impact", var["payload"])   # money stripped
        self.assertNotIn("(R40000)", var["summary"])

    def test_facade_includes_changes(self):
        with tenant_scope(self.c.id):
            h = history_for(self.q, self.finance)
        self.assertIn("changes", h)
        self.assertTrue(h["changes"])
