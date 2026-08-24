"""API tests for the Customers & Contacts endpoints — list/search, permission
gating on writes, and the Golden-Rule money gate on the overview."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User

from .services import create_customer


class CustomerAPITests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        manage = Permission.objects.create(
            codename="customers.manage", module="customers", label="Manage customers")
        money = Permission.objects.create(
            codename="finance.view_money", module="finance", label="See money")

        admin_role = Role.objects.create(name="Admin", is_system=True)
        admin_role.permissions.add(manage, money)
        worker_role = Role.objects.create(name="Worker", is_system=True)

        self.admin = User.objects.create_user(
            "admin@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.admin, company=self.company, role=admin_role)
        self.worker = User.objects.create_user(
            "thabo@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=worker_role)

        with tenant_scope(self.company.id):
            self.customer = create_customer(
                self.company, self.admin, name="Harmony Mining", seed_departments=False)

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def test_list_returns_customers(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/customers/")
        self.assertEqual(r.status_code, 200)
        names = [c["name"] for c in r.data["results"]]
        self.assertIn("Harmony Mining", names)

    def test_search_filters(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/customers/?search=Harmony")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 1)

    def test_worker_cannot_create(self):
        self._auth(self.worker)
        r = self.client.post("/api/v1/customers/", {"name": "New Co"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_admin_create_generates_code(self):
        self._auth(self.admin)
        r = self.client.post("/api/v1/customers/", {"name": "Sibanye"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["code"], "customer code should be generated")

    def test_overview_hides_money_without_permission(self):
        self._auth(self.worker)  # no finance.view_money
        r = self.client.get(f"/api/v1/customers/{self.customer.id}/overview/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("outstanding_value", r.data)
        self.assertIn("contacts", r.data)

    def test_overview_shows_money_with_permission(self):
        self._auth(self.admin)  # has finance.view_money
        r = self.client.get(f"/api/v1/customers/{self.customer.id}/overview/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("outstanding_value", r.data)

    def test_contacts_action(self):
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/customers/{self.customer.id}/contacts/")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.data, list)

    # ── CRM (Phase 5) ────────────────────────────────────────────────────────
    def test_timeline_returns_list(self):
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/customers/{self.customer.id}/timeline/")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.data, list)

    def test_worker_cannot_log_interaction(self):
        self._auth(self.worker)  # no crm.manage / customers.manage
        r = self.client.post(
            f"/api/v1/customers/{self.customer.id}/log-interaction/",
            {"summary": "Called", "channel": "phone"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_admin_can_log_and_note_and_it_hits_the_timeline(self):
        self._auth(self.admin)  # has customers.manage
        before = len(self.client.get(
            f"/api/v1/customers/{self.customer.id}/timeline/").data)
        li = self.client.post(
            f"/api/v1/customers/{self.customer.id}/log-interaction/",
            {"summary": "Called re pump quote", "channel": "phone"}, format="json")
        self.assertEqual(li.status_code, 201)
        note = self.client.post(
            f"/api/v1/customers/{self.customer.id}/add-note/",
            {"body": "Prefers mornings"}, format="json")
        self.assertEqual(note.status_code, 201)
        after = len(self.client.get(
            f"/api/v1/customers/{self.customer.id}/timeline/").data)
        self.assertGreater(after, before)

    def test_log_interaction_requires_summary(self):
        self._auth(self.admin)
        r = self.client.post(
            f"/api/v1/customers/{self.customer.id}/log-interaction/",
            {"summary": ""}, format="json")
        self.assertEqual(r.status_code, 400)
