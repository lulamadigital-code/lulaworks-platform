"""Our Clients — delete (soft) a client, permission-gated."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ClientDeleteTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["customers.manage"], "mgr@a.co")
        self.viewer = _user(self.c, ["projects.view"], "v@a.co")
        with tenant_scope(self.c.id):
            self.client_obj = Customer.objects.create(company=self.c, name="ABC Mining")

    def test_manager_can_delete(self):
        self.client.force_login(self.mgr)
        r = self.client.post(f"/procurement/clients/{self.client_obj.id}/delete/")
        self.assertEqual(r.status_code, 302)
        with tenant_scope(self.c.id):
            self.assertFalse(Customer.objects.filter(pk=self.client_obj.id).exists())   # hidden
            self.assertTrue(Customer.all_objects.filter(pk=self.client_obj.id,
                                                        is_deleted=True).exists())       # recoverable

    def test_viewer_cannot_delete(self):
        self.client.force_login(self.viewer)
        self.client.post(f"/procurement/clients/{self.client_obj.id}/delete/")
        with tenant_scope(self.c.id):
            self.assertTrue(Customer.objects.filter(pk=self.client_obj.id).exists())     # still there
