"""Task creation via the API must work like the web New-Work wizard: gated on
work.create (not just execution.manage), routed through create_work so the
creator is assigned as owner and the task appears in their My Tasks."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.projects.models import Project


def _role(name, codes):
    role = Role.objects.create(name=name, is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "work", "label": c})
        role.permissions.add(p)
    return role


class TaskCreateTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = User.objects.create_user("m@acme.co", "x", active_company=self.c)
        Membership.objects.create(user=self.mgr, company=self.c,
                                  role=_role("Mgr", ["work.create"]))  # web gate, no execution.manage
        self.viewer = User.objects.create_user("v@acme.co", "x", active_company=self.c)
        Membership.objects.create(user=self.viewer, company=self.c,
                                  role=_role("Viewer", ["projects.view"]))
        with tenant_scope(self.c.id):
            self.proj = Project.objects.create(company=self.c, title="Job A")

    def test_work_create_permission_can_create(self):
        # A role with work.create (but NOT execution.manage) can create — web parity.
        self.client.force_authenticate(self.mgr)
        r = self.client.post("/api/v1/tasks/",
                             {"project": str(self.proj.id), "name": "Replace seal"},
                             format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["name"], "Replace seal")

    def test_creator_is_owner_and_shows_in_my_tasks(self):
        # The created task must appear in the creator's My Tasks (?mine=1) — this
        # was the bug: bare tasks had no assignee and never showed up.
        self.client.force_authenticate(self.mgr)
        self.client.post("/api/v1/tasks/",
                        {"project": str(self.proj.id), "name": "Test pressure"},
                        format="json")
        mine = self.client.get("/api/v1/tasks/?mine=1")
        self.assertEqual(mine.status_code, 200)
        names = [t["name"] for t in (mine.data.get("results") or mine.data)]
        self.assertIn("Test pressure", names)

    def test_without_work_perm_forbidden(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.post("/api/v1/tasks/",
                             {"project": str(self.proj.id), "name": "x"}, format="json")
        self.assertEqual(r.status_code, 403)
