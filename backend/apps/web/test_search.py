"""Global search — tenant (permission-aware) and platform (staff-only)."""
from django.test import TestCase

from apps.core.context import system_scope, tenant_scope
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


class TenantSearchTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme Co")
        with tenant_scope(self.c.id):
            Customer.objects.create(company=self.c, name="Zenith Mining")

    def test_finds_customer_with_permission(self):
        u = _user(self.c, ["customers.manage"], "mgr@acme.co")
        self.client.force_login(u)
        r = self.client.get("/search/?q=Zenith")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Zenith Mining")
        self.assertContains(r, "Customers")

    def test_hidden_without_permission(self):
        u = _user(self.c, ["work.edit"], "field@acme.co")   # no customer access
        self.client.force_login(u)
        r = self.client.get("/search/?q=Zenith")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Zenith Mining")

    def test_short_query_no_results(self):
        u = _user(self.c, ["customers.manage"], "s@acme.co")
        self.client.force_login(u)
        r = self.client.get("/search/?q=Z")
        self.assertNotContains(r, "Zenith Mining")


class PlatformSearchTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@lulaworks.com", "x")
        self.owner.is_superuser = True
        self.owner.is_staff = True
        self.owner.save()
        with system_scope():
            Company.objects.create(name="Zenith Mining Pty")
        self.client.force_login(self.owner)

    def test_platform_finds_company(self):
        r = self.client.get("/platform/search/?q=Zenith")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Zenith Mining Pty")
        self.assertContains(r, "Companies")

    def test_platform_finds_user(self):
        r = self.client.get("/platform/search/?q=owner@lula")
        self.assertContains(r, "owner@lulaworks.com")

    def test_non_staff_blocked(self):
        c = Company.objects.create(name="Tenant Co")
        u = _user(c, ["projects.view"], "plain@tenant.co")
        self.client.force_login(u)
        r = self.client.get("/platform/search/?q=Zenith", follow=True)
        self.assertNotContains(r, "Zenith Mining Pty")
