"""Payment idempotency — a retry (lost response / offline re-send) with the same
key must never create a second payment. Fix for the audit CRITICAL."""
from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.quotes.models import CommercialDocumentPayment
from apps.quotes.services import create_direct_invoice, record_payment


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class PaymentIdempotencyTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, ["quotes.create", "finance.view_money",
                                "finance.manage", "invoices.approve"], "f@acme.co")
        with tenant_scope(self.c.id):
            self.doc = create_direct_invoice(
                self.c, self.u, client_name="ABC", lines=[
                    {"description": "Callout", "qty": Decimal("1"),
                     "unit": "each", "unit_price": Decimal("1000")}],
                vat_rate=Decimal("15"))

    def test_same_key_records_one_payment(self):
        with tenant_scope(self.c.id):
            p1 = record_payment(self.doc, self.u, amount=Decimal("500"),
                                idempotency_key="abc-123")
            p2 = record_payment(self.doc, self.u, amount=Decimal("500"),
                                idempotency_key="abc-123")
            self.assertEqual(p1.id, p2.id)  # same row, not a duplicate
            self.assertEqual(
                CommercialDocumentPayment.objects.filter(
                    document=self.doc, idempotency_key="abc-123").count(), 1)

    def test_no_key_is_not_deduped(self):
        # Legacy/web path without a key keeps prior behaviour (two distinct POPs).
        with tenant_scope(self.c.id):
            record_payment(self.doc, self.u, amount=Decimal("100"))
            record_payment(self.doc, self.u, amount=Decimal("100"))
            self.assertEqual(
                CommercialDocumentPayment.objects.filter(document=self.doc).count(), 2)

    def test_api_retry_same_key_one_payment(self):
        api = APIClient()
        api.force_authenticate(self.u)
        body = {"amount": "750", "idempotency_key": "net-retry-1"}
        r1 = api.post(f"/api/v1/commercial-documents/{self.doc.id}/payment/", body, format="json")
        r2 = api.post(f"/api/v1/commercial-documents/{self.doc.id}/payment/", body, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertIn(r2.status_code, (200, 201))
        with tenant_scope(self.c.id):
            self.assertEqual(
                CommercialDocumentPayment.objects.filter(
                    document=self.doc, idempotency_key="net-retry-1").count(), 1)
