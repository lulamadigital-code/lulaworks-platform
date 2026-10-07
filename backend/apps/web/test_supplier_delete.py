"""Web (server-rendered) supplier delete flow: a procurement admin can disable
and restore; a platform owner can permanently purge a disabled supplier. Also
proves the suppliers list, its ?show=disabled view and the detail page render."""
from django.test import Client
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.procurement.models import Supplier


def _mk(company, codes, email, **extra):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "procurement", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company, **extra)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class SupplierWebDeleteTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.admin = _mk(self.c, ["procurement.manage"], "pa@acme.co")
        self.owner = _mk(self.c, ["procurement.manage"], "owner@acme.co",
                         platform_role="owner")
        with tenant_scope(self.c.id):
            self.sup = Supplier.objects.create(company=self.c, name="Bolt Depot")

    def test_pages_render(self):
        cl = Client(); cl.force_login(self.admin)
        self.assertEqual(cl.get(reverse("web:suppliers")).status_code, 200)
        self.assertEqual(
            cl.get(reverse("web:suppliers") + "?show=disabled").status_code, 200)
        self.assertEqual(
            cl.get(reverse("web:supplier_detail", args=[self.sup.id])).status_code, 200)

    def test_admin_disable_then_restore(self):
        cl = Client(); cl.force_login(self.admin)
        cl.post(reverse("web:supplier_delete", args=[self.sup.id]))
        with tenant_scope(self.c.id):
            self.assertTrue(Supplier.all_objects.get(pk=self.sup.id).is_deleted)
        cl.post(reverse("web:supplier_restore", args=[self.sup.id]))
        with tenant_scope(self.c.id):
            self.assertFalse(Supplier.all_objects.get(pk=self.sup.id).is_deleted)

    def test_admin_cannot_purge_but_owner_can(self):
        with tenant_scope(self.c.id):
            self.sup.delete()  # disabled
        admin_cl = Client(); admin_cl.force_login(self.admin)
        admin_cl.post(reverse("web:supplier_hard_delete", args=[self.sup.id]))
        with tenant_scope(self.c.id):
            self.assertTrue(Supplier.all_objects.filter(pk=self.sup.id).exists())
        owner_cl = Client(); owner_cl.force_login(self.owner)
        owner_cl.post(reverse("web:supplier_hard_delete", args=[self.sup.id]))
        with tenant_scope(self.c.id):
            self.assertFalse(Supplier.all_objects.filter(pk=self.sup.id).exists())
