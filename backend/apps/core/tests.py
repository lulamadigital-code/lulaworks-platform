"""Core foundation tests: ambient tenancy (fail-closed + isolation) and
soft-delete, exercised on a throwaway concrete TenantBaseModel."""

from django.db import connection, models
from django.test import TestCase

from apps.identity.models import Company

from .context import get_current_company, system_scope, tenant_scope
from .managers import TenantMissingError
from .models import TenantBaseModel


class Widget(TenantBaseModel):
    """Test-only concrete tenant model. Defined in tests.py so makemigrations
    never picks it up; its table is created/dropped manually below."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = "core"


class TenancyManagerTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as se:
            se.create_model(Widget)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as se:
            se.delete_model(Widget)
        super().tearDownClass()

    def setUp(self):
        self.c1 = Company.objects.create(name="Tenant One")
        self.c2 = Company.objects.create(name="Tenant Two")

    def test_fail_closed_without_tenant(self):
        with self.assertRaises(TenantMissingError):
            list(Widget.objects.all())

    def test_isolation_between_tenants(self):
        with tenant_scope(self.c1.id):
            Widget.objects.create(name="w1")
        with tenant_scope(self.c2.id):
            Widget.objects.create(name="w2")
        with tenant_scope(self.c1.id):
            names = list(Widget.objects.values_list("name", flat=True))
        self.assertEqual(names, ["w1"])

    def test_save_stamps_tenant_from_context(self):
        with tenant_scope(self.c1.id):
            w = Widget.objects.create(name="auto")
            self.assertEqual(w.company_id, self.c1.id)

    def test_system_scope_sees_all_tenants(self):
        with tenant_scope(self.c1.id):
            Widget.objects.create(name="w1")
        with tenant_scope(self.c2.id):
            Widget.objects.create(name="w2")
        with system_scope():
            self.assertEqual(Widget.objects.count(), 2)

    def test_soft_delete_hides_row(self):
        with tenant_scope(self.c1.id):
            w = Widget.objects.create(name="gone")
            w.delete()
            self.assertEqual(Widget.objects.count(), 0)
            self.assertEqual(Widget.all_objects.filter(id=w.id).count(), 1)

    def test_context_clears(self):
        with tenant_scope(self.c1.id):
            self.assertEqual(get_current_company(), self.c1.id)
        self.assertIsNone(get_current_company())
