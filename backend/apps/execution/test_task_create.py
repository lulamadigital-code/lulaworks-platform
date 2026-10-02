"""Task creation via the API — minimal payload the mobile form sends
({project, name[, due_date]}) must succeed for an execution.manage user."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.projects.models import Project


class TaskCreateTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        role = Role.objects.create(name="Mgr", is_system=True)
        p, _ = Permission.objects.get_or_create(
            codename="execution.manage", defaults={"module": "execution", "label": "x"})
        role.permissions.add(p)
        self.u = User.objects.create_user("m@acme.co", "x", active_company=self.c)
        Membership.objects.create(user=self.u, company=self.c, role=role)
        viewer_role = Role.objects.create(name="Viewer", is_system=True)
        self.viewer = User.objects.create_user("v@acme.co", "x", active_company=self.c)
        Membership.objects.create(user=self.viewer, company=self.c, role=viewer_role)
        with tenant_scope(self.c.id):
            self.proj = Project.objects.create(company=self.c, title="Job A")

    def test_create_minimal(self):
        self.client.force_authenticate(self.u)
        r = self.client.post("/api/v1/tasks/",
                             {"project": str(self.proj.id), "name": "Replace seal"},
                             format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["name"], "Replace seal")

    def test_create_with_due_date(self):
        self.client.force_authenticate(self.u)
        r = self.client.post("/api/v1/tasks/", {
            "project": str(self.proj.id), "name": "Test pressure",
            "due_date": "2026-11-01"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["due_date"], "2026-11-01")

    def test_requires_execution_manage(self):
        # A role without execution.manage is refused — this is what hides the
        # mobile 'New task' button and would 403 the create.
        self.client.force_authenticate(self.viewer)
        r = self.client.post("/api/v1/tasks/",
                             {"project": str(self.proj.id), "name": "x"}, format="json")
        self.assertEqual(r.status_code, 403)
