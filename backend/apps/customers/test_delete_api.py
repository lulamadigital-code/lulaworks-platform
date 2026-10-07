"""Deleting a customer from the app is a SOFT delete (disable): the record and
its history stay, it just leaves the active list. Gated on customers.manage."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "customers", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class CustomerDeleteTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["customers.manage"], "m@acme.co")
        self.viewer = _user(self.c, ["customers.manage"], "v2@acme.co")  # can see, used below
        self.readonly = _user(self.c, ["projects.view"], "ro@acme.co")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")

    def test_manage_soft_deletes(self):
        self.client.force_authenticate(self.mgr)
        r = self.client.delete(f"/api/v1/customers/{self.cust.id}/")
        self.assertEqual(r.status_code, 204)
        with tenant_scope(self.c.id):
            # Gone from the default (active) manager...
            self.assertFalse(Customer.objects.filter(pk=self.cust.id).exists())
            # ...but kept in all_objects (soft delete, restorable).
            self.assertTrue(
                Customer.all_objects.filter(pk=self.cust.id, is_deleted=True).exists())

    def test_requires_customers_manage(self):
        self.client.force_authenticate(self.readonly)
        r = self.client.delete(f"/api/v1/customers/{self.cust.id}/")
        self.assertEqual(r.status_code, 403)
        with tenant_scope(self.c.id):
            self.assertTrue(Customer.objects.filter(pk=self.cust.id).exists())
