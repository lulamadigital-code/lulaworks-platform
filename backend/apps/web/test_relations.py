"""Business transaction graph (relations.related_records) — P0 hardening:
permission-aware money gating, the payment extension, deterministic ordering and
the per-section cap. The graph must never leak billing to a user who can't see
money, and must stay fast for a customer with many records.
"""
from datetime import date

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.quotes.models import (CommercialDocument, CommercialDocumentPayment,
                                Quotation)
from apps.web.relations import _MAX_PER_SECTION, related_records


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


def _titles(sections):
    return [s["title"] for s in sections]


class RelatedRecordsPermissionTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.finance = _user(self.c, ["finance.view_money"], "cfo@acme.co")
        self.field = _user(self.c, ["projects.view"], "worker@acme.co")

    def _chain(self):
        """A quotation with one invoice, one delivery note, and a payment."""
        with tenant_scope(self.c.id):
            q = Quotation.objects.create(company=self.c, number="QTN-1",
                                         client_name="ABC Mining")
            inv = CommercialDocument.objects.create(
                company=self.c, quotation=q, kind=CommercialDocument.Kind.INVOICE,
                number="INV-1")
            CommercialDocument.objects.create(
                company=self.c, quotation=q, kind=CommercialDocument.Kind.DELIVERY,
                number="DN-1")
            CommercialDocumentPayment.objects.create(
                company=self.c, document=inv, date=date(2026, 1, 15), amount=5000)
            return q, inv

    def test_finance_user_sees_invoices_and_payments(self):
        q, _inv = self._chain()
        with tenant_scope(self.c.id):
            titles = _titles(related_records(q, self.finance))
        self.assertIn("Invoices", titles)
        self.assertIn("Payments", titles)
        self.assertIn("Delivery notes", titles)

    def test_field_worker_never_sees_billing_via_the_graph(self):
        q, _inv = self._chain()
        with tenant_scope(self.c.id):
            titles = _titles(related_records(q, self.field))
        # Delivery notes are operational evidence — a field worker keeps them…
        self.assertIn("Delivery notes", titles)
        # …but invoices and payments are money, and must not leak here (§5/§46).
        self.assertNotIn("Invoices", titles)
        self.assertNotIn("Payments", titles)

    def test_no_user_withholds_financial_sections(self):
        q, _inv = self._chain()
        with tenant_scope(self.c.id):
            titles = _titles(related_records(q))          # e.g. an unauthenticated path
        self.assertNotIn("Invoices", titles)
        self.assertNotIn("Payments", titles)

    def test_invoice_detail_reaches_its_payments(self):
        _q, inv = self._chain()
        with tenant_scope(self.c.id):
            pay_finance = _titles(related_records(inv, self.finance))
            pay_field = _titles(related_records(inv, self.field))
        self.assertIn("Payments", pay_finance)       # the chain extends to Payment
        self.assertNotIn("Payments", pay_field)


class RelatedRecordsOrderingTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="BigCo")
        self.u = _user(self.c, ["projects.view"], "ops@bigco.co")

    def test_customer_section_is_capped_newest_first_with_more_flag(self):
        with tenant_scope(self.c.id):
            cust = Customer.objects.create(company=self.c, name="ABC Mining")
            # More quotations than the cap; created in order QTN-000 … QTN-021.
            for i in range(_MAX_PER_SECTION + 2):
                Quotation.objects.create(company=self.c, customer=cust,
                                         number=f"QTN-{i:03d}", client_name="ABC Mining")
            sections = related_records(cust, self.u)

        quotes = next(s for s in sections if s["title"] == "Quotations")
        # Capped to the section limit, and flagged that more exist.
        self.assertEqual(len(quotes["items"]), _MAX_PER_SECTION)
        self.assertTrue(quotes["more"])
        labels = [it["label"] for it in quotes["items"]]
        # Deterministic newest-first: the last created is present, the first is not.
        self.assertIn(f"QTN-{_MAX_PER_SECTION + 1:03d}", labels)
        self.assertNotIn("QTN-000", labels)
