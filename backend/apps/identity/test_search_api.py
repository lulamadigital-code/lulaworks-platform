"""Global search API — permission-aware, tenant-scoped, shared with the web."""
from rest_framework.test import APIClient, APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


def _api(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


class GlobalSearchApiTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        with tenant_scope(self.c.id):
            from apps.customers.models import Customer
            from apps.quotes.models import Quotation
            Customer.objects.create(company=self.c, name="Sibanye Mining")
            Quotation.objects.create(company=self.c, number="QTN-55",
                                     client_name="Sibanye Mining", title="Pump job")

    def test_finds_customer_and_quotation(self):
        u = _user(self.c, ["customers.manage", "quotes.create"], "s@acme.co")
        r = _api(u).get("/api/v1/search/?q=Sibanye")
        self.assertEqual(r.status_code, 200)
        labels = {g["label"] for g in r.data["groups"]}
        self.assertIn("Customers", labels)
        self.assertIn("Quotations", labels)
        self.assertTrue(r.data["total"] >= 2)
        cust = next(g for g in r.data["groups"] if g["label"] == "Customers")
        self.assertEqual(cust["items"][0]["type"], "customer")
        self.assertTrue(cust["items"][0]["id"])

    def test_permission_scopes_groups(self):
        # Only customer access → no Quotations group even if the term matches one.
        u = _user(self.c, ["customers.manage"], "c@acme.co")
        r = _api(u).get("/api/v1/search/?q=Sibanye")
        labels = {g["label"] for g in r.data["groups"]}
        self.assertIn("Customers", labels)
        self.assertNotIn("Quotations", labels)

    def test_short_query_returns_nothing(self):
        u = _user(self.c, ["customers.manage"], "q@acme.co")
        r = _api(u).get("/api/v1/search/?q=S")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["total"], 0)

    def test_requires_auth(self):
        r = APIClient().get("/api/v1/search/?q=Sibanye")
        self.assertEqual(r.status_code, 401)
