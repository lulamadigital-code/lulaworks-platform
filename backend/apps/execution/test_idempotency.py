"""Offline re-send idempotency — a field report or clock event flushed twice on
reconnect must de-duplicate on its client key. Fix for the audit CRITICAL."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.execution.models import AttendanceEvent, Task, TaskReport
from apps.identity.models import Company, Membership, Permission, Role, User


class OfflineIdempotencyTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        edit = Permission.objects.create(codename="work.edit", module="work", label="E")
        role = Role.objects.create(name="Worker", is_system=True)
        role.permissions.add(edit)
        self.worker = User.objects.create_user("w@lulama.co.za", "x",
                                               active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=role)
        with tenant_scope(self.company.id):
            self.task = Task.objects.create(company=self.company, name="Job")
        self.client.force_authenticate(self.worker)

    def test_task_report_resend_dedupes(self):
        body = {"task": str(self.task.id), "kind": "progress",
                "title": "Arrived", "idempotency_key": "rep-key-1"}
        r1 = self.client.post("/api/v1/task-reports/", body, format="json")
        r2 = self.client.post("/api/v1/task-reports/", body, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r1.data["id"], r2.data["id"])
        with tenant_scope(self.company.id):
            self.assertEqual(
                TaskReport.objects.filter(idempotency_key="rep-key-1").count(), 1)

    def test_task_report_without_key_not_deduped(self):
        body = {"task": str(self.task.id), "kind": "progress", "title": "A"}
        self.client.post("/api/v1/task-reports/", body, format="json")
        self.client.post("/api/v1/task-reports/", body, format="json")
        with tenant_scope(self.company.id):
            self.assertEqual(TaskReport.objects.filter(task=self.task).count(), 2)

    def test_attendance_resend_dedupes(self):
        body = {"kind": "clock_in",
                "occurred_at": "2026-10-01T06:00:00Z",
                "idempotency_key": "att-key-1"}
        r1 = self.client.post("/api/v1/attendance-events/", body, format="json")
        r2 = self.client.post("/api/v1/attendance-events/", body, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertIn(r2.status_code, (200, 201))
        with tenant_scope(self.company.id):
            self.assertEqual(
                AttendanceEvent.objects.filter(idempotency_key="att-key-1").count(), 1)
