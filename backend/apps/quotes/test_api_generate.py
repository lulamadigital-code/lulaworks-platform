"""P0 mobile parity: the quotation API can raise a tax invoice / delivery note
(the create path the web had but the API didn't). Permission + business-rule
guard are enforced server-side — the mobile client just calls these."""
from rest_framework.test import APITestCase, APIClient

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
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


class QuotationGenerateApiTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.creator = _user(self.c, ["quotes.create", "finance.view_money"], "sales@acme.co")
        self.viewer = _user(self.c, ["quotes.download"], "view@acme.co")
        with tenant_scope(self.c.id):
            self.q = Quotation.objects.create(company=self.c, number="QTN-1",
                                              client_name="ABC Mining")

    def _api(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def test_invoice_requires_quotes_create(self):
        r = self._api(self.viewer).post(f"/api/v1/quotations/{self.q.pk}/invoice/")
        self.assertEqual(r.status_code, 403)

    def test_invoice_guarded_until_quote_approved(self):
        # A draft quotation can't be invoiced — the service rule, surfaced as 400.
        r = self._api(self.creator).post(f"/api/v1/quotations/{self.q.pk}/invoice/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("message", r.data["error"])

    def test_delivery_note_requires_quotes_create(self):
        r = self._api(self.viewer).post(
            f"/api/v1/quotations/{self.q.pk}/delivery-note/")
        self.assertEqual(r.status_code, 403)

    def test_delivery_note_guarded_until_invoice_exists(self):
        r = self._api(self.creator).post(
            f"/api/v1/quotations/{self.q.pk}/delivery-note/")
        self.assertEqual(r.status_code, 400)


class QuotationCreateDepthTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.creator = _user(self.c, ["quotes.create", "finance.view_money"], "s@acme.co")

    def test_create_links_customer_and_sets_vat_notes_lines(self):
        from apps.customers.models import Customer
        with tenant_scope(self.c.id):
            cust = Customer.objects.create(company=self.c, name="ABC Mining")
        api = APIClient()
        api.force_authenticate(self.creator)
        r = api.post('/api/v1/quotations/', {
            'client_name': 'ABC Mining', 'customer': str(cust.pk),
            'vat_rate': '15.00', 'notes': 'Net 30',
            'lines': [{'description': 'Pump overhaul', 'qty': 2, 'unit_price': 1500}],
        }, format='json')
        self.assertEqual(r.status_code, 201)
        with tenant_scope(self.c.id):
            q = Quotation.objects.get(pk=r.data['id'])
            self.assertEqual(q.customer_id, cust.pk)
            self.assertEqual(str(q.vat_rate), '15.00')
            self.assertEqual(q.notes, 'Net 30')
            self.assertEqual(q.lines.count(), 1)
