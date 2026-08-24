"""Time & Attendance — event-based clock/break/site events, offline-friendly
timestamps, and manager-reviewed corrections a worker can never self-approve."""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.identity.models import Company, Membership, Permission, Role, User


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("po", "PO")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


class AttendanceTests(APITestCase):
    def setUp(self):
        self.company = make_company()
        # A field worker (no special perm) and a manager (timesheet.approve).
        approve = Permission.objects.create(codename="timesheet.approve",
                                            module="execution", label="Approve")
        self.worker_role = Role.objects.create(name="Worker", is_system=True)
        self.mgr_role = Role.objects.create(name="Manager", is_system=True)
        self.mgr_role.permissions.add(approve)
        self.worker = User.objects.create_user("w@lulama.co.za", "x",
                                                active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=self.worker_role)
        self.mgr = User.objects.create_user("m@lulama.co.za", "x",
                                             active_company=self.company)
        Membership.objects.create(user=self.mgr, company=self.company, role=self.mgr_role)
        self.now = timezone.now()

    def _post(self, kind, mins_ago=0, **extra):
        return self.client.post("/api/v1/attendance-events/", {
            "kind": kind,
            "occurred_at": (self.now - timedelta(minutes=mins_ago)).isoformat(),
            **extra}, format="json")

    def test_clock_in_is_open_to_any_worker_and_forces_self(self):
        self.client.force_authenticate(self.worker)
        r = self._post("clock_in", 60, latitude="-26.2", longitude="28.0")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "recorded")
        self.assertEqual(r.data["user"], self.worker.id)

    def test_today_summary_excludes_breaks(self):
        self.client.force_authenticate(self.worker)
        self._post("clock_in", 240)     # 4h ago
        self._post("break_start", 150)  # worked 90m
        self._post("break_end", 120)    # 30m break
        # still working → +120m ⇒ 210m
        s = self.client.get("/api/v1/attendance-events/today/").data["summary"]
        self.assertEqual(s["state"], "working")
        self.assertAlmostEqual(round(s["worked_seconds"] / 60), 210, delta=1)
        self._post("clock_out", 5)      # 115m more ⇒ 205m
        s2 = self.client.get("/api/v1/attendance-events/today/").data["summary"]
        self.assertEqual(s2["state"], "clocked_out")
        self.assertAlmostEqual(round(s2["worked_seconds"] / 60), 205, delta=1)

    def test_offline_event_keeps_its_device_time(self):
        self.client.force_authenticate(self.worker)
        earlier = (self.now - timedelta(hours=3)).replace(microsecond=0)
        r = self._post("clock_in", 180)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["occurred_at"][:16], earlier.isoformat()[:16])

    def test_worker_cannot_self_approve_a_correction(self):
        self.client.force_authenticate(self.worker)
        # even submitting status=approved is ignored on create
        cr = self.client.post("/api/v1/attendance-events/", {
            "kind": "clock_in", "is_correction": True,
            "status": "approved", "note": "forgot"}, format="json")
        self.assertEqual(cr.status_code, 201)
        self.assertEqual(cr.data["status"], "pending")
        # and PATCH is manager-only
        patched = self.client.patch(f"/api/v1/attendance-events/{cr.data['id']}/",
                                    {"status": "approved"}, format="json")
        self.assertEqual(patched.status_code, 403)

    def test_manager_reviews_and_approves_correction(self):
        self.client.force_authenticate(self.worker)
        cr = self.client.post("/api/v1/attendance-events/", {
            "kind": "clock_in", "is_correction": True, "note": "forgot"}, format="json")
        cid = cr.data["id"]

        self.client.force_authenticate(self.mgr)
        queue = self.client.get("/api/v1/attendance-events/?pending=1")
        ids = [e["id"] for e in queue.data.get("results", queue.data)]
        self.assertIn(cid, ids)
        ap = self.client.patch(f"/api/v1/attendance-events/{cid}/",
                               {"status": "approved"}, format="json")
        self.assertEqual(ap.status_code, 200)
        self.assertEqual(ap.data["status"], "approved")

    def test_worker_only_sees_own_events(self):
        # worker A records; worker B must not see A's row in their own list
        other = User.objects.create_user("b@lulama.co.za", "x",
                                          active_company=self.company)
        Membership.objects.create(user=other, company=self.company, role=self.worker_role)
        self.client.force_authenticate(self.worker)
        self._post("clock_in", 30)
        self.client.force_authenticate(other)
        mine = self.client.get("/api/v1/attendance-events/")
        self.assertEqual(len(mine.data.get("results", mine.data)), 0)
