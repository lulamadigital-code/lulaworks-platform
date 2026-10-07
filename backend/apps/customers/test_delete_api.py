"""The two-tier customer delete model, matching the web:

  • a tenant COMPANY ADMIN (customers.manage) can DISABLE a customer — a
    recoverable soft-delete that keeps the row and its whole history, and can
    restore it;
  • only the SOFTWARE OWNER (platform owner/admin) can PERMANENTLY purge it,
    and only once it is already disabled.
"""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User


def _role(codes):
    role = Role.objects.create(name=f"R-{','.join(codes) or 'none'}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "customers", "label": c})
        role.permissions.add(p)
    return role


def _user(company, codes, email, **extra):
    u = User.objects.create_user(email, "x", active_company=company, **extra)
    Membership.objects.create(user=u, company=company, role=_role(codes))
    return u


class CustomerDeleteTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.admin = _user(self.c, ["customers.manage"], "admin@acme.co")       # company admin
        self.readonly = _user(self.c, ["projects.view"], "ro@acme.co")          # no manage
        # The software owner: platform staff, operating inside this tenant. Holds
        # NO customers.manage — platform ownership alone authorises the purge.
        self.owner = _user(self.c, [], "owner@acme.co", platform_role="owner")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")

    def _disable(self):
        with tenant_scope(self.c.id):
            self.cust.delete()  # soft

    # ── Company admin: soft delete + restore ────────────────────────────────
    def test_admin_soft_deletes(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(f"/api/v1/customers/{self.cust.id}/")
        self.assertEqual(r.status_code, 204)
        with tenant_scope(self.c.id):
            self.assertFalse(Customer.objects.filter(pk=self.cust.id).exists())
            row = Customer.all_objects.get(pk=self.cust.id)
            self.assertTrue(row.is_deleted)
            self.assertEqual(row.deleted_by_id, self.admin.id)

    def test_soft_delete_requires_customers_manage(self):
        self.client.force_authenticate(self.readonly)
        r = self.client.delete(f"/api/v1/customers/{self.cust.id}/")
        self.assertEqual(r.status_code, 403)
        with tenant_scope(self.c.id):
            self.assertTrue(Customer.objects.filter(pk=self.cust.id).exists())

    def test_disabled_list_shows_soft_deleted(self):
        self._disable()
        self.client.force_authenticate(self.admin)
        r = self.client.get("/api/v1/customers/disabled/")
        self.assertEqual(r.status_code, 200)
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertIn(str(self.cust.id), [c["id"] for c in rows])
        # And the active list does NOT include it.
        active = self.client.get("/api/v1/customers/")
        arows = active.data["results"] if isinstance(active.data, dict) else active.data
        self.assertNotIn(str(self.cust.id), [c["id"] for c in arows])

    def test_admin_can_restore(self):
        self._disable()
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"/api/v1/customers/{self.cust.id}/restore/")
        self.assertEqual(r.status_code, 200)
        with tenant_scope(self.c.id):
            self.assertTrue(Customer.objects.filter(pk=self.cust.id).exists())

    # ── Software owner: hard delete (purge) ─────────────────────────────────
    def test_company_admin_cannot_purge(self):
        self._disable()
        self.client.force_authenticate(self.admin)  # has customers.manage, NOT platform
        r = self.client.delete(f"/api/v1/customers/{self.cust.id}/purge/")
        self.assertEqual(r.status_code, 403)
        with tenant_scope(self.c.id):
            self.assertTrue(Customer.all_objects.filter(pk=self.cust.id).exists())

    def test_platform_owner_can_purge_disabled(self):
        self._disable()
        self.client.force_authenticate(self.owner)
        r = self.client.delete(f"/api/v1/customers/{self.cust.id}/purge/")
        self.assertEqual(r.status_code, 204)
        with tenant_scope(self.c.id):
            self.assertFalse(Customer.all_objects.filter(pk=self.cust.id).exists())

    def test_cannot_purge_active_customer(self):
        # Purge is only ever allowed on an already-disabled customer (two-step).
        self.client.force_authenticate(self.owner)
        r = self.client.delete(f"/api/v1/customers/{self.cust.id}/purge/")
        self.assertEqual(r.status_code, 404)
        with tenant_scope(self.c.id):
            self.assertTrue(Customer.objects.filter(pk=self.cust.id).exists())
