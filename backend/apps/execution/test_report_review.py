"""Report review loop (§16/§36): a manager returns a report with a comment, the
author resubmits, the manager approves — with backend-enforced permissions."""
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.core.context import tenant_scope
from apps.execution.models import Task
from apps.identity.models import Company, Membership, Permission, Role, User


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("po", "PO")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


class ReportReviewTests(APITestCase):
    def setUp(self):
        self.company = make_company()
        edit = Permission.objects.create(codename="work.edit", module="work", label="E")
        approve = Permission.objects.create(codename="work.approve", module="work", label="A")
        worker_role = Role.objects.create(name="Worker", is_system=True)
        worker_role.permissions.add(edit)
        mgr_role = Role.objects.create(name="Manager", is_system=True)
        mgr_role.permissions.add(edit, approve)

        self.worker = User.objects.create_user("w@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=worker_role)
        self.mgr = User.objects.create_user("m@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.mgr, company=self.company, role=mgr_role)
        self.stranger = User.objects.create_user("s@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.stranger, company=self.company, role=worker_role)
        with tenant_scope(self.company.id):
            self.task = Task.objects.create(company=self.company, name="Job")

    def _report(self):
        self.client.force_authenticate(self.worker)
        r = self.client.post("/api/v1/task-reports/",
                             {"task": str(self.task.id), "kind": "progress",
                              "title": "Done"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "submitted")
        return r.data["id"]

    def test_full_review_loop(self):
        rid = self._report()
        # worker can't approve their own
        self.client.force_authenticate(self.worker)
        self.assertEqual(
            self.client.post(f"/api/v1/task-reports/{rid}/approve/").status_code, 403)
        # manager returns with a comment (comment required)
        self.client.force_authenticate(self.mgr)
        self.assertEqual(
            self.client.post(f"/api/v1/task-reports/{rid}/return/", {}, format="json").status_code,
            400)
        ret = self.client.post(f"/api/v1/task-reports/{rid}/return/",
                               {"comment": "Add a photo"}, format="json")
        self.assertEqual(ret.data["status"], "returned")
        self.assertEqual(len(ret.data["comments"]), 1)
        # worker resubmits
        self.client.force_authenticate(self.worker)
        rs = self.client.post(f"/api/v1/task-reports/{rid}/resubmit/",
                              {"comment": "Added"}, format="json")
        self.assertEqual(rs.data["status"], "submitted")
        self.assertEqual(len(rs.data["comments"]), 2)
        # manager approves
        self.client.force_authenticate(self.mgr)
        ap = self.client.post(f"/api/v1/task-reports/{rid}/approve/")
        self.assertEqual(ap.data["status"], "approved")
        self.assertEqual(str(ap.data["reviewed_by"]), str(self.mgr.id))
        self.assertIsNotNone(ap.data["reviewed_at"])

    def test_stranger_cannot_review_or_comment(self):
        rid = self._report()
        self.client.force_authenticate(self.stranger)
        self.assertEqual(
            self.client.post(f"/api/v1/task-reports/{rid}/approve/").status_code, 403)
        self.assertEqual(
            self.client.post(f"/api/v1/task-reports/{rid}/comment/",
                             {"body": "hi"}, format="json").status_code, 403)

    def test_author_can_comment(self):
        rid = self._report()
        self.client.force_authenticate(self.worker)
        r = self.client.post(f"/api/v1/task-reports/{rid}/comment/",
                             {"body": "FYI"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["comments"]), 1)
