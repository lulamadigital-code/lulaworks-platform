"""Task chat — task-scoped messages whose access is enforced by the backend: a
participant (assigned to the task) or a manager can read/post; no one else can,
and never across tasks or companies."""
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.core.context import tenant_scope
from apps.execution.models import Assignment, Task
from apps.identity.models import Company, Membership, Permission, Role, User


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("po", "PO")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


class TaskChatTests(APITestCase):
    def setUp(self):
        self.company = make_company()
        manage = Permission.objects.create(codename="execution.manage",
                                            module="execution", label="M")
        edit = Permission.objects.create(codename="work.edit", module="work", label="E")
        mgr_role = Role.objects.create(name="Ops", is_system=True)
        mgr_role.permissions.add(manage, edit)
        worker_role = Role.objects.create(name="Worker", is_system=True)
        worker_role.permissions.add(edit)

        self.mgr = User.objects.create_user("mgr@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.mgr, company=self.company, role=mgr_role)
        self.worker = User.objects.create_user("w@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=worker_role)
        self.stranger = User.objects.create_user("s@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.stranger, company=self.company, role=worker_role)

        with tenant_scope(self.company.id):
            self.task = Task.objects.create(company=self.company, name="Supply job")
            Assignment.objects.create(task=self.task, user=self.worker,
                                      role=Assignment.Role.EXECUTOR)

    def _url(self):
        return f"/api/v1/task-messages/?task={self.task.id}"

    def test_participant_can_post_and_read(self):
        self.client.force_authenticate(self.worker)
        p = self.client.post("/api/v1/task-messages/",
                             {"task": str(self.task.id), "body": "On my way"}, format="json")
        self.assertEqual(p.status_code, 201, p.data)
        self.assertFalse(p.data["is_system"])
        r = self.client.get(self._url())
        self.assertEqual(len(r.data.get("results", r.data)), 1)

    def test_manager_can_read_and_post(self):
        self.client.force_authenticate(self.mgr)
        p = self.client.post("/api/v1/task-messages/",
                             {"task": str(self.task.id), "body": "Verify quantity"}, format="json")
        self.assertEqual(p.status_code, 201)
        r = self.client.get(self._url())
        self.assertEqual(len(r.data.get("results", r.data)), 1)

    def test_non_participant_cannot_read_or_post(self):
        self.client.force_authenticate(self.stranger)
        r = self.client.get(self._url())
        self.assertEqual(len(r.data.get("results", r.data)), 0)
        p = self.client.post("/api/v1/task-messages/",
                             {"task": str(self.task.id), "body": "sneaky"}, format="json")
        self.assertEqual(p.status_code, 403)

    def test_empty_message_rejected(self):
        self.client.force_authenticate(self.worker)
        p = self.client.post("/api/v1/task-messages/",
                             {"task": str(self.task.id), "body": "   "}, format="json")
        self.assertEqual(p.status_code, 400)

    def test_list_without_task_returns_nothing(self):
        self.client.force_authenticate(self.worker)
        r = self.client.get("/api/v1/task-messages/")
        self.assertEqual(len(r.data.get("results", r.data)), 0)

    def test_start_task_posts_a_system_message(self):
        self.client.force_authenticate(self.worker)
        self.client.post(f"/api/v1/tasks/{self.task.id}/start/")
        r = self.client.get(self._url())
        rows = r.data.get("results", r.data)
        system = [m for m in rows if m["is_system"]]
        self.assertEqual(len(system), 1)
        self.assertIn("started the task", system[0]["body"])
