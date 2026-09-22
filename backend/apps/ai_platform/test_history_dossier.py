"""P3 — supplier & customer HISTORY questions answered by LulaAI from live +
imported records, permission-aware and evidence-backed (§32/§34)."""
from datetime import date

from django.test import TestCase

from apps.ai_platform.assistant import ask, classify
from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.procurement.models import Supplier, SupplierPrice
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


class SupplierHistoryTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.buyer = _user(self.c, ["procurement.manage"], "buyer@acme.co")
        self.worker = _user(self.c, ["projects.view"], "worker@acme.co")
        with tenant_scope(self.c.id):
            sup = Supplier.objects.create(company=self.c, name="Hydraulics SA")
            SupplierPrice.objects.create(
                company=self.c, supplier=sup, item_key="hydraulic hose",
                description='Hydraulic Hose 2"', unit_price=470, date=date(2026, 1, 20))

    def test_classify_routes_supplier_history(self):
        intent, params = classify("what have we bought from Hydraulics SA?")
        self.assertEqual(intent, "historical_supplier")
        self.assertEqual(params["supplier_name"], "Hydraulics SA")

    def test_answer_names_supplier_and_counts_purchases(self):
        with tenant_scope(self.c.id):
            out = ask(self.c, self.buyer, "what have we bought from Hydraulics SA?")
        self.assertEqual(out["intent"], "historical_supplier")
        self.assertIn("Hydraulics SA", out["answer"])
        self.assertIn("purchase", out["answer"].lower())
        self.assertTrue(out["items"])

    def test_denied_without_procurement(self):
        with tenant_scope(self.c.id):
            out = ask(self.c, self.worker, "what have we bought from Hydraulics SA?")
        self.assertTrue(out.get("denied"))


class CustomerHistoryTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["customers.manage"], "mgr@acme.co")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")
            Project.objects.create(company=self.c, number="JOB-1", client_name="ABC Mining",
                                   customer=self.cust, work_type="hydraulic")

    def test_classify_strips_trailing_filler(self):
        intent, params = classify("have we worked with ABC Mining before?")
        self.assertEqual(intent, "historical_customer")
        self.assertEqual(params["customer_name"], "ABC Mining")   # "before" trimmed

    def test_answer_confirms_relationship_from_live_job(self):
        with tenant_scope(self.c.id):
            out = ask(self.c, self.mgr, "have we worked with ABC Mining before?")
        self.assertEqual(out["intent"], "historical_customer")
        self.assertIn("ABC Mining", out["answer"])
        self.assertTrue(out["answer"].lower().startswith("yes"))
