"""Customer deletion policy: a tenant user can only DISABLE (recoverable soft-
delete) a customer; a real, permanent removal is reserved for a platform
owner/admin. The canonical records the customer is attached to are never harmed
by a disable."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.quotes.models import Quotation


def _user(company, codes, email, *, superuser=False):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company,
                                 is_superuser=superuser, is_staff=superuser)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class CustomerSoftDeleteTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.manager = _user(self.c, ["customers.manage"], "mgr@acme.co")
        self.worker = _user(self.c, ["projects.view"], "worker@acme.co")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")
            self.quote = Quotation.objects.create(company=self.c, customer=self.cust,
                                                  number="QTN-1", client_name="ABC Mining")

    def _alive(self):
        with tenant_scope(self.c.id):
            return Customer.objects.filter(pk=self.cust.pk).exists()

    def _exists_at_all(self):
        return Customer.all_objects.filter(pk=self.cust.pk).exists()

    def test_disable_soft_deletes_and_keeps_related_records(self):
        self.client.force_login(self.manager)
        r = self.client.post(f"/customers/{self.cust.pk}/delete/")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self._alive())            # hidden from the tenant's lists…
        self.assertTrue(self._exists_at_all())     # …but kept (recoverable)
        with tenant_scope(self.c.id):
            self.assertTrue(Customer.all_objects.get(pk=self.cust.pk).is_deleted)
            # the quotation it was attached to is untouched
            self.assertTrue(Quotation.objects.filter(pk=self.quote.pk).exists())

    def test_disable_requires_permission(self):
        self.client.force_login(self.worker)
        self.client.post(f"/customers/{self.cust.pk}/delete/")
        self.assertTrue(self._alive())             # a worker cannot delete

    def test_restore_brings_it_back(self):
        self.client.force_login(self.manager)
        self.client.post(f"/customers/{self.cust.pk}/delete/")
        self.assertFalse(self._alive())
        r = self.client.post(f"/customers/{self.cust.pk}/restore/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self._alive())

    def test_disabled_list_shows_soft_deleted(self):
        self.client.force_login(self.manager)
        self.client.post(f"/customers/{self.cust.pk}/delete/")
        r = self.client.get("/customers/?show=disabled")
        self.assertContains(r, "ABC Mining")


class CustomerHardDeleteTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.manager = _user(self.c, ["customers.manage"], "mgr@acme.co")
        self.owner = _user(self.c, ["customers.manage"], "owner@acme.co", superuser=True)
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")

    def _disable(self):
        with tenant_scope(self.c.id):
            self.cust.delete()                     # soft

    def _exists_at_all(self):
        return Customer.all_objects.filter(pk=self.cust.pk).exists()

    def test_tenant_user_cannot_hard_delete(self):
        self._disable()
        self.client.force_login(self.manager)      # has customers.manage, not owner
        self.client.post(f"/customers/{self.cust.pk}/purge/")
        self.assertTrue(self._exists_at_all())     # still there — refused

    def test_platform_owner_can_hard_delete_a_disabled_customer(self):
        self._disable()
        self.client.force_login(self.owner)
        r = self.client.post(f"/customers/{self.cust.pk}/purge/")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self._exists_at_all())    # really gone

    def test_hard_delete_only_applies_to_a_disabled_customer(self):
        # An alive (not-yet-disabled) customer can't be purged — enforces the
        # deliberate two-step (disable, then purge).
        self.client.force_login(self.owner)
        r = self.client.post(f"/customers/{self.cust.pk}/purge/")
        self.assertEqual(r.status_code, 404)
        self.assertTrue(self._exists_at_all())
