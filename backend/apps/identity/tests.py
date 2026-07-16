"""Foundation tests: RBAC permission engine + JWT auth + health.

Deep tenant-isolation tests land in Phase 2 with the first business model that
inherits TenantBaseModel; the manager's fail-closed behaviour is unit-checked
in apps/core/tests.py.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Company, Membership, Permission, Role, User


class RBACTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        self.money = Permission.objects.create(
            codename="finance.view_money", module="finance", label="View money"
        )
        self.admin_role = Role.objects.create(name="Admin", is_system=True)
        self.admin_role.permissions.add(self.money)
        self.worker_role = Role.objects.create(name="Worker", is_system=True)

        self.admin = User.objects.create_user("admin@lulama.co.za", "pass12345",
                                               active_company=self.company)
        Membership.objects.create(user=self.admin, company=self.company, role=self.admin_role)
        self.worker = User.objects.create_user("thabo@lulama.co.za", "pass12345",
                                                active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=self.worker_role)

    def test_admin_has_money_permission(self):
        self.assertTrue(self.admin.has_perm_code("finance.view_money"))

    def test_worker_denied_money_permission(self):
        self.assertFalse(self.worker.has_perm_code("finance.view_money"))

    def test_no_membership_denied(self):
        stranger = User.objects.create_user("x@x.co.za", "pass12345")
        self.assertFalse(stranger.has_perm_code("finance.view_money"))

    def test_superuser_bypasses(self):
        su = User.objects.create_superuser("root@lulama.co.za", "pass12345")
        self.assertTrue(su.has_perm_code("anything.at.all"))


class JWTAuthTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        self.user = User.objects.create_user("admin@lulama.co.za", "pass12345",
                                             active_company=self.company)

    def test_obtain_token(self):
        resp = self.client.post(reverse("token_obtain_pair"),
                                {"email": "admin@lulama.co.za", "password": "pass12345"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_wrong_password_rejected(self):
        resp = self.client.post(reverse("token_obtain_pair"),
                                {"email": "admin@lulama.co.za", "password": "wrong"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_token(self):
        obtain = self.client.post(reverse("token_obtain_pair"),
                                  {"email": "admin@lulama.co.za", "password": "pass12345"})
        resp = self.client.post(reverse("token_refresh"),
                                {"refresh": obtain.data["refresh"]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)


class HealthTests(APITestCase):
    def test_health(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
