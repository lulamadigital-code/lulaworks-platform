"""Push channel — device registration API + honest gating of delivery.

Delivery itself needs FCM credentials (configured per-environment); these tests
prove the registration contract and that send_push/notify degrade honestly
(never fake a send) when the provider isn't configured."""
from rest_framework.test import APIClient, APITestCase

from apps.identity.models import Company, Membership, Role, User
from apps.notifications.models import PushDevice


def _user(company, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class PushDeviceApiTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, "u@acme.co")
        self.other = _user(self.c, "o@acme.co")

    def _api(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def test_register_requires_auth(self):
        r = APIClient().post("/api/v1/me/devices/", {"token": "t1"}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_register_creates_device(self):
        r = self._api(self.u).post("/api/v1/me/devices/",
                                   {"token": "tok-1", "platform": "android"}, format="json")
        self.assertEqual(r.status_code, 201)
        d = PushDevice.objects.get(token="tok-1")
        self.assertEqual(d.user_id, self.u.id)
        self.assertTrue(d.active)
        self.assertEqual(d.company_id, self.c.id)

    def test_register_is_idempotent_and_repoints_token(self):
        self._api(self.u).post("/api/v1/me/devices/", {"token": "shared"}, format="json")
        # Same token registered by another user (reflashed device) → re-pointed.
        self._api(self.other).post("/api/v1/me/devices/", {"token": "shared"}, format="json")
        self.assertEqual(PushDevice.objects.filter(token="shared").count(), 1)
        self.assertEqual(PushDevice.objects.get(token="shared").user_id, self.other.id)

    def test_register_rejects_empty_token(self):
        r = self._api(self.u).post("/api/v1/me/devices/", {"token": ""}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_unregister_deactivates_only_own(self):
        self._api(self.u).post("/api/v1/me/devices/", {"token": "mine"}, format="json")
        # Another user can't deactivate it.
        self._api(self.other).delete("/api/v1/me/devices/", {"token": "mine"}, format="json")
        self.assertTrue(PushDevice.objects.get(token="mine").active)
        # The owner can.
        r = self._api(self.u).delete("/api/v1/me/devices/", {"token": "mine"}, format="json")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(PushDevice.objects.get(token="mine").active)


class PushGatingTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, "g@acme.co")

    def test_send_push_skips_when_no_devices(self):
        from apps.notifications.push import send_push
        res = send_push(self.u, title="Hi")
        self.assertTrue(res["skipped"])
        self.assertEqual(res["reason"], "no_devices")

    def test_send_push_skips_honestly_when_unconfigured(self):
        from apps.notifications.push import register_device, send_push
        register_device(self.u, token="tok-x")
        res = send_push(self.u, title="Hi", body="there")
        # Has a device, but FCM isn't configured in tests → never faked.
        self.assertEqual(res["sent"], 0)
        self.assertTrue(res["skipped"])
        self.assertEqual(res["reason"], "not_configured")

    def test_notify_includes_push_result_and_never_raises(self):
        from apps.notifications.dispatch import notify
        out = notify(self.c, self.u, title="Job updated", body="x")
        self.assertIn("push", out)  # push seam ran
