"""Two-tier supplier delete, identical to customers: a procurement admin
(procurement.manage) can DISABLE + restore; only the software owner (platform
owner/admin) can PERMANENTLY purge, and only an already-disabled supplier."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.procurement.models import Supplier


def _role(codes):
    role = Role.objects.create(name=f"R-{','.join(codes) or 'none'}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "procurement", "label": c})
        role.permissions.add(p)
    return role


def _user(company, codes, email, **extra):
    u = User.objects.create_user(email, "x", active_company=company, **extra)
    Membership.objects.create(user=u, company=company, role=_role(codes))
    return u


class SupplierDeleteTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.admin = _user(self.c, ["procurement.manage"], "pa@acme.co")
        self.readonly = _user(self.c, ["projects.view"], "ro@acme.co")
        self.owner = _user(self.c, [], "owner@acme.co", platform_role="owner")
        with tenant_scope(self.c.id):
            self.sup = Supplier.objects.create(company=self.c, name="Bolt Depot")

    def _disable(self):
        with tenant_scope(self.c.id):
            self.sup.delete()

    def test_admin_soft_deletes(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(f"/api/v1/suppliers/{self.sup.id}/")
        self.assertEqual(r.status_code, 204)
        with tenant_scope(self.c.id):
            self.assertFalse(Supplier.objects.filter(pk=self.sup.id).exists())
            row = Supplier.all_objects.get(pk=self.sup.id)
            self.assertTrue(row.is_deleted)
            self.assertEqual(row.deleted_by_id, self.admin.id)

    def test_soft_delete_requires_procurement_manage(self):
        self.client.force_authenticate(self.readonly)
        r = self.client.delete(f"/api/v1/suppliers/{self.sup.id}/")
        self.assertEqual(r.status_code, 403)

    def test_disabled_list_and_restore(self):
        self._disable()
        self.client.force_authenticate(self.admin)
        lst = self.client.get("/api/v1/suppliers/disabled/")
        self.assertEqual(lst.status_code, 200)
        rows = lst.data["results"] if isinstance(lst.data, dict) else lst.data
        self.assertIn(str(self.sup.id), [s["id"] for s in rows])
        r = self.client.post(f"/api/v1/suppliers/{self.sup.id}/restore/")
        self.assertEqual(r.status_code, 200)
        with tenant_scope(self.c.id):
            self.assertTrue(Supplier.objects.filter(pk=self.sup.id).exists())

    def test_procurement_admin_cannot_purge(self):
        self._disable()
        self.client.force_authenticate(self.admin)
        r = self.client.delete(f"/api/v1/suppliers/{self.sup.id}/purge/")
        self.assertEqual(r.status_code, 403)
        with tenant_scope(self.c.id):
            self.assertTrue(Supplier.all_objects.filter(pk=self.sup.id).exists())

    def test_platform_owner_can_purge_disabled(self):
        self._disable()
        self.client.force_authenticate(self.owner)
        r = self.client.delete(f"/api/v1/suppliers/{self.sup.id}/purge/")
        self.assertEqual(r.status_code, 204)
        with tenant_scope(self.c.id):
            self.assertFalse(Supplier.all_objects.filter(pk=self.sup.id).exists())

    def test_cannot_purge_active_supplier(self):
        self.client.force_authenticate(self.owner)
        r = self.client.delete(f"/api/v1/suppliers/{self.sup.id}/purge/")
        self.assertEqual(r.status_code, 404)
