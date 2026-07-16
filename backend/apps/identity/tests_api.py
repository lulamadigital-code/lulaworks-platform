"""Identity/company management API tests: /me, company, users (invite),
roles, permissions — with scoping and RBAC enforcement."""

from rest_framework import status
from rest_framework.test import APITestCase

from .models import Company, Membership, Permission, Role, User


class IdentityAPITests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        self.other_company = Company.objects.create(name="Rival")

        self.invite_perm = Permission.objects.create(
            codename="users.invite", module="identity", label="Invite"
        )
        self.money_perm = Permission.objects.create(
            codename="finance.view_money", module="finance", label="Money"
        )
        self.admin_role = Role.objects.create(name="Admin", is_system=True)
        self.admin_role.permissions.add(self.invite_perm, self.money_perm)
        self.worker_role = Role.objects.create(name="Worker", is_system=True)

        self.admin = User.objects.create_user("admin@lulama.co.za", "pass12345",
                                               active_company=self.company)
        Membership.objects.create(user=self.admin, company=self.company, role=self.admin_role)
        self.worker = User.objects.create_user("thabo@lulama.co.za", "pass12345",
                                               active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=self.worker_role)
        # a member of the other company (must never appear for Lulama)
        outsider = User.objects.create_user("x@rival.co.za", "pass12345",
                                            active_company=self.other_company)
        Membership.objects.create(user=outsider, company=self.other_company, role=self.worker_role)

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def test_me_returns_user_company_and_permissions(self):
        self.auth(self.admin)
        resp = self.client.get("/api/v1/me/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["user"]["email"], "admin@lulama.co.za")
        self.assertEqual(resp.data["active_company"]["name"], "Lulama")
        self.assertIn("users.invite", resp.data["permissions"])

    def test_me_requires_auth(self):
        self.assertEqual(self.client.get("/api/v1/me/").status_code, 401)

    def test_users_list_scoped_to_company(self):
        self.auth(self.admin)
        resp = self.client.get("/api/v1/users/")
        emails = {m["user"]["email"] for m in resp.data["results"]}
        self.assertEqual(emails, {"admin@lulama.co.za", "thabo@lulama.co.za"})
        self.assertNotIn("x@rival.co.za", emails)  # tenant scoping

    def test_invite_requires_permission(self):
        self.auth(self.worker)  # worker lacks users.invite
        resp = self.client.post("/api/v1/users/", {
            "email": "new@lulama.co.za", "role": str(self.worker_role.id)
        })
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_invite_creates_membership_in_own_company(self):
        self.auth(self.admin)
        resp = self.client.post("/api/v1/users/", {
            "email": "new@lulama.co.za", "first_name": "New",
            "role": str(self.worker_role.id), "job_title": "Fitter",
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        m = Membership.objects.get(user__email="new@lulama.co.za")
        self.assertEqual(m.company, self.company)

    def test_company_update_requires_perm(self):
        self.auth(self.worker)
        resp = self.client.patch("/api/v1/company/", {"city": "Rustenburg"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_company_retrieve_returns_own(self):
        self.auth(self.worker)
        resp = self.client.get("/api/v1/company/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Lulama")

    def test_roles_include_platform_templates(self):
        self.auth(self.admin)
        resp = self.client.get("/api/v1/roles/")
        names = {r["name"] for r in resp.data["results"]}
        self.assertIn("Admin", names)
