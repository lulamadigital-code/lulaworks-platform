"""Customer contacts REST API — the "People we work with" list behind the
mobile CRM hub: a searchable roster across every customer, with the customer
name attached for display."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer, CustomerContact
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ContactsApiTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["crm.manage", "customers.manage"], "mgr@acme.co")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="Zenith Mining")
            for i in range(10):
                CustomerContact.objects.create(
                    company=self.c, customer=self.cust,
                    full_name=f"Person {i:02d}", job_title="Buyer",
                    email=f"p{i}@zenith.co", is_primary=(i == 0))
            CustomerContact.objects.create(
                company=self.c, customer=self.cust,
                full_name="Thabo Molefe", job_title="Procurement Lead",
                email="thabo@zenith.co")

    def test_list_returns_all_with_customer_name(self):
        self.client.force_authenticate(self.mgr)
        r = self.client.get("/api/v1/customer-contacts/")
        self.assertEqual(r.status_code, 200)
        results = r.data.get("results") or r.data
        # 11 contacts created; at least 8 so the hub preview is populated.
        self.assertGreaterEqual(len(results), 8)
        self.assertEqual(results[0]["customer_name"], "Zenith Mining")
        # Primary sorts first.
        self.assertTrue(results[0]["is_primary"])

    def test_search_by_name(self):
        self.client.force_authenticate(self.mgr)
        r = self.client.get("/api/v1/customer-contacts/?search=Thabo")
        self.assertEqual(r.status_code, 200)
        results = r.data.get("results") or r.data
        names = [x["full_name"] for x in results]
        self.assertEqual(names, ["Thabo Molefe"])
