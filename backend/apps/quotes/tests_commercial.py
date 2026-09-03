"""API tests for Commercial Documents — the two hard rules especially:
delivery notes never carry prices (§15), and invoice money is Golden-Rule gated.
"""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User

from apps.customers.services import create_customer

from .models import QuotationStatus
from .services import (
    create_delivery_document,
    create_invoice_document,
    create_quotation,
)


class CommercialDocumentAPITests(APITestCase):
    def setUp(self):
        # A fully set-up company so issuing an invoice PDF isn't blocked by the
        # progressive company-setup requirements (identity + tax + banking).
        self.company = Company.objects.create(
            name="Lulama", street_address="1 Main Rd", city="Johannesburg",
            phone="011 555 0000", registration_no="2020/123456/07")
        from apps.identity.models import CompanyBankAccount
        CompanyBankAccount.objects.create(
            company=self.company, bank_name="FNB", account_name="Lulama",
            account_number="620000000", is_default=True)
        codes = ["finance.view_money", "invoices.approve", "quotes.download",
                 "quotes.approve"]
        perms = [Permission.objects.create(codename=c, module=c.split(".")[0], label=c)
                 for c in codes]
        role = Role.objects.create(name="Commercial", is_system=True)
        role.permissions.add(*perms)
        worker_role = Role.objects.create(name="Worker", is_system=True)

        self.user = User.objects.create_user(
            "sales@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.user, company=self.company, role=role)
        self.worker = User.objects.create_user(
            "hand@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=worker_role)

        with tenant_scope(self.company.id):
            customer = create_customer(self.company, self.user, name="Sasol",
                                       seed_departments=False)
            quote = create_quotation(
                self.company, self.user, client_name="Sasol", title="Overhaul",
                lines=[{"description": "Pump strip", "qty": 2, "unit": "job",
                        "unit_price": "500.00"}])
            # Commercial documents can only be raised from an approved quote
            # that has a customer on file.
            quote.customer = customer
            quote.status = QuotationStatus.APPROVED
            quote.save(update_fields=["customer", "status"])
            self.invoice = create_invoice_document(quote, self.user)
            self.delivery = create_delivery_document(
                quote, self.user, delivery_address="Secunda plant")

    def _auth(self, u):
        self.client.force_authenticate(user=u)

    def test_delivery_note_never_carries_prices(self):
        self._auth(self.user)  # even a full money user
        r = self.client.get(f"/api/v1/commercial-documents/{self.delivery.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("total", r.data)
        for line in r.data["lines"]:
            self.assertNotIn("unit_price", line)
            self.assertNotIn("line_total", line)
            self.assertIn("qty", line)  # quantities only

    def test_invoice_shows_money_for_permitted_user(self):
        self._auth(self.user)
        r = self.client.get(f"/api/v1/commercial-documents/{self.invoice.id}/")
        self.assertIsNotNone(r.data["total"])
        self.assertIn("unit_price", r.data["lines"][0])

    def test_invoice_withholds_money_without_permission(self):
        self._auth(self.worker)  # no finance.view_money
        r = self.client.get(f"/api/v1/commercial-documents/{self.invoice.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["total"])
        self.assertNotIn("unit_price", r.data["lines"][0])

    def test_kind_filter(self):
        self._auth(self.user)
        inv = self.client.get("/api/v1/commercial-documents/?kind=invoice")
        self.assertTrue(all(d["kind"] == "invoice" for d in inv.data["results"]))

    def test_record_payment_reduces_outstanding(self):
        self._auth(self.user)
        r = self.client.post(
            f"/api/v1/commercial-documents/{self.invoice.id}/payment/",
            {"amount": "100.00"}, format="json")
        self.assertEqual(r.status_code, 201)
        after = self.client.get(
            f"/api/v1/commercial-documents/{self.invoice.id}/").data
        self.assertEqual(after["amount_paid"], "100.00")

    def test_payment_rejected_on_delivery_note(self):
        self._auth(self.user)
        r = self.client.post(
            f"/api/v1/commercial-documents/{self.delivery.id}/payment/",
            {"amount": "100.00"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_pdf_routes_by_kind(self):
        self._auth(self.user)
        for doc in (self.invoice, self.delivery):
            resp = self.client.get(f"/api/v1/commercial-documents/{doc.id}/pdf/")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp["Content-Type"], "application/pdf")
