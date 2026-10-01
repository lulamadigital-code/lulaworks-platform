"""Plan seat-limit enforcement on invite (§9). Blocks a new seat when the plan's
included users are used up and there's no per-seat overage."""
from rest_framework.test import APIClient, APITestCase

from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u, role


class SeatLimitTests(APITestCase):
    def setUp(self):
        # No Subscription row → limit falls back to company.max_users (hard cap,
        # no per-seat overage).
        self.c = Company.objects.create(name="Acme", max_users=1)
        self.admin, self.role = _user(self.c, ["users.invite"], "admin@acme.co")

    def test_invite_blocked_at_seat_limit(self):
        api = APIClient()
        api.force_authenticate(self.admin)
        # Company already has 1 active member (the admin) == max_users 1.
        r = api.post("/api/v1/users/", {
            "email": "new@acme.co", "role": str(self.role.id),
        }, format="json")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.data["error"]["code"], "seat_limit")
        self.assertFalse(Membership.objects.filter(user__email="new@acme.co").exists())

    def test_invite_allowed_with_headroom(self):
        self.c.max_users = 5
        self.c.save(update_fields=["max_users"])
        api = APIClient()
        api.force_authenticate(self.admin)
        r = api.post("/api/v1/users/", {
            "email": "new2@acme.co", "role": str(self.role.id),
        }, format="json")
        self.assertEqual(r.status_code, 201)
