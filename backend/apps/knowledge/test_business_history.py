"""Business History facade (knowledge.business_history) — the one permission-aware
surface every consumer shares. Proves it composes the graph + timeline +
intelligence and gates money once, so it can never become a billing side channel.
"""
from datetime import date
from uuid import uuid4

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.business_history import history_for, history_for_kind
from apps.quotes.models import (CommercialDocument, CommercialDocumentPayment,
                                Quotation)


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class BusinessHistoryFacadeTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.finance = _user(self.c, ["finance.view_money"], "cfo@acme.co")
        self.field = _user(self.c, ["projects.view"], "worker@acme.co")

    def _customer(self):
        with tenant_scope(self.c.id):
            cust = Customer.objects.create(company=self.c, name="ABC Mining")
            q = Quotation.objects.create(company=self.c, customer=cust,
                                         number="QTN-1", client_name="ABC Mining")
            inv = CommercialDocument.objects.create(
                company=self.c, quotation=q, kind=CommercialDocument.Kind.INVOICE,
                number="INV-1")
            CommercialDocumentPayment.objects.create(
                company=self.c, document=inv, date=date(2026, 1, 15), amount=5000)
            return cust, q

    def test_facade_shape_for_finance_user(self):
        cust, _q = self._customer()
        with tenant_scope(self.c.id):
            h = history_for(cust, self.finance)
        self.assertEqual(h["kind"], "customer")
        self.assertTrue(h["permissions"]["money"])
        # One consistent contract for every consumer.
        for key in ("entity", "summary", "timeline", "related"):
            self.assertIn(key, h)
        self.assertIsInstance(h["summary"], dict)
        # The customer-level graph fans out to quotations/jobs/POs.
        self.assertIn("Quotations", [s["title"] for s in h["related"]])

    def test_facade_gates_the_graph_on_a_quotation(self):
        _cust, q = self._customer()
        with tenant_scope(self.c.id):
            fin = [s["title"] for s in history_for(q, self.finance)["related"]]
            fld = [s["title"] for s in history_for(q, self.field)["related"]]
        self.assertIn("Invoices", fin)             # finance reaches billing…
        self.assertIn("Payments", fin)
        self.assertNotIn("Invoices", fld)          # …a field worker never does
        self.assertNotIn("Payments", fld)

    def test_facade_gates_money_for_field_worker(self):
        cust, _q = self._customer()
        with tenant_scope(self.c.id):
            h = history_for(cust, self.field)
        self.assertFalse(h["permissions"]["money"])
        titles = [s["title"] for s in h["related"]]
        self.assertNotIn("Invoices", titles)       # no billing via the graph…
        self.assertNotIn("Payments", titles)
        # …and no amounts via the timeline (the gate is applied once, in the facade)
        self.assertTrue(all(e["amount"] == "" for e in h["timeline"]))

    def test_timeline_is_normalised_and_clickable(self):
        cust, _q = self._customer()
        with tenant_scope(self.c.id):
            h = history_for(cust, self.finance)
        self.assertTrue(h["timeline"])             # the quotation/invoice show up
        ev = h["timeline"][0]
        for key in ("when", "kind", "title", "detail", "url", "amount"):
            self.assertIn(key, ev)

    def test_history_for_kind_unknown_returns_none(self):
        with tenant_scope(self.c.id):
            self.assertIsNone(history_for_kind("customer", uuid4(), self.finance))
            self.assertIsNone(history_for_kind("bogus", uuid4(), self.finance))

    def test_api_endpoint_serves_the_same_facade(self):
        from rest_framework.test import APIClient
        cust, _q = self._customer()
        api = APIClient()
        api.force_authenticate(self.finance)
        r = api.get(f"/api/v1/business-history/customer/{cust.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["kind"], "customer")
        self.assertTrue(r.data["permissions"]["money"])
        self.assertIn("related", r.data)
