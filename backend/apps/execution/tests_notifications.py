"""API tests for in-app notifications — list, unread count, mark-read."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Membership, Role, User

from .models import Notification
from .tests import make_company


class NotificationAPITests(APITestCase):
    def setUp(self):
        self.company = make_company()
        role = Role.objects.create(name="Field", is_system=True)
        self.user = User.objects.create_user(
            "field@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.user, company=self.company, role=role)
        # A second user's notification must never leak into the first's list.
        self.other = User.objects.create_user(
            "other@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.other, company=self.company, role=role)
        with tenant_scope(self.company.id):
            Notification.objects.create(
                company=self.company, user=self.user, title="You were assigned a task")
            Notification.objects.create(
                company=self.company, user=self.other, title="Not yours")

    def test_list_is_scoped_to_the_user(self):
        self.client.force_authenticate(self.user)
        r = self.client.get("/api/v1/notifications/")
        self.assertEqual(r.status_code, 200)
        titles = [n["title"] for n in r.data["results"]]
        self.assertEqual(titles, ["You were assigned a task"])

    def test_unread_count(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.get("/api/v1/notifications/unread/").data["count"], 1)

    def test_mark_all_read(self):
        self.client.force_authenticate(self.user)
        r = self.client.post("/api/v1/notifications/mark-read/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 0)
        self.assertEqual(
            self.client.get("/api/v1/notifications/unread/").data["count"], 0)
