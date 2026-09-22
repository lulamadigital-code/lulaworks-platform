"""P3 — graph-aware LulaAI retrieval: a price-history question is answered from
STRUCTURED records (paid vs charged), permission-aware and evidence-backed (§32-§34)."""
from datetime import date

from django.test import TestCase

from apps.ai_platform.assistant import ask, classify
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.models import HistoricalLineItem, ImportBatch, ImportedDocument


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class PriceHistoryRetrievalTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.proc_fin = _user(self.c, ["procurement.manage", "finance.view_money"],
                              "buyer@acme.co")
        self.proc = _user(self.c, ["procurement.manage"], "buyer2@acme.co")
        self.worker = _user(self.c, ["projects.view"], "worker@acme.co")
        with tenant_scope(self.c.id):
            batch = ImportBatch.objects.create(company=self.c)
            self.doc = ImportedDocument.objects.create(
                company=self.c, batch=batch, filename="Hydraulics SA invoice.pdf",
                document_date=date(2026, 1, 20))
            HistoricalLineItem.objects.create(
                company=self.c, document=self.doc,
                direction=HistoricalLineItem.Direction.PURCHASE,
                party_kind=HistoricalLineItem.Party.SUPPLIER, party_name="Hydraulics SA",
                description='Hydraulic Hose 2"', item_key="hydraulic hose",
                unit_price=470, occurred_on=date(2026, 1, 20))
            HistoricalLineItem.objects.create(
                company=self.c, document=self.doc,
                direction=HistoricalLineItem.Direction.SALE,
                party_kind=HistoricalLineItem.Party.CUSTOMER, party_name="ABC Mining",
                description='Hydraulic Hose 2"', item_key="hydraulic hose",
                unit_price=690, occurred_on=date(2026, 1, 25))

    def test_classify_routes_price_question_to_structured_tool(self):
        intent, params = classify("what did we pay for hydraulic hose?")
        self.assertEqual(intent, "item_price_history")
        self.assertEqual(params["item"], "hydraulic hose")

    def test_answer_is_sourced_and_shows_paid_and_charged_for_finance(self):
        with tenant_scope(self.c.id):
            out = ask(self.c, self.proc_fin, "what did we pay for hydraulic hose?")
        self.assertEqual(out["intent"], "item_price_history")
        self.assertIn("Last paid", out["answer"])
        self.assertIn("charged", out["answer"].lower())          # finance sees charged
        self.assertTrue(out["items"])
        self.assertIn("Hydraulics SA invoice.pdf", out["sources"])   # evidence (§33)

    def test_charged_side_hidden_without_finance_permission(self):
        with tenant_scope(self.c.id):
            out = ask(self.c, self.proc, "what did we pay for hydraulic hose?")
        self.assertIn("Last paid", out["answer"])                # paid is procurement's
        self.assertNotIn("charged", out["answer"].lower())       # charged withheld

    def test_denied_without_procurement_permission(self):
        with tenant_scope(self.c.id):
            out = ask(self.c, self.worker, "what did we pay for hydraulic hose?")
        self.assertTrue(out.get("denied"))
