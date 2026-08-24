"""API test for the ?mine=1 task filter — the field worker's 'My tasks'."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Membership, Role, User

from .models import Assignment, Task
from .tests import award_ready_project, make_company


class MyTasksFilterTests(APITestCase):
    def test_mine_returns_only_assigned_tasks(self):
        company = make_company()
        role = Role.objects.create(name="Field", is_system=True)
        user = User.objects.create_user(
            "field@lulama.co.za", "pass12345", active_company=company)
        Membership.objects.create(user=user, company=company, role=role)

        with tenant_scope(company.id):
            project = award_ready_project(company)
            mine = Task.objects.create(
                company=company, project=project, name="Collect pipes",
                blocks_on_compliance=False)
            Task.objects.create(
                company=company, project=project, name="Someone else's",
                blocks_on_compliance=False)
            Assignment.objects.create(company=company, task=mine, user=user)

        self.client.force_authenticate(user=user)
        all_tasks = self.client.get("/api/v1/tasks/")
        mine_only = self.client.get("/api/v1/tasks/?mine=1")

        self.assertEqual(mine_only.status_code, 200)
        names_all = {t["name"] for t in all_tasks.data["results"]}
        names_mine = {t["name"] for t in mine_only.data["results"]}
        self.assertIn("Someone else's", names_all)   # visible in the full list
        self.assertEqual(names_mine, {"Collect pipes"})  # but not in "mine"
