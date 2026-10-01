"""Read-only billing summary API — gated on company.manage, never mutates."""
from rest_framework.test import APIClient, APITestCase

from apps.billing.models import Plan, Subscription
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class BillingSummaryApiTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme", ai_credit_balance=120)
        self.plan = Plan.objects.create(
            code="business", name="Business", tier=3, price=1999,
            max_users=10, monthly_ai_credits=8000,
            features=["Everything in Pro", "Priority support"])
        Subscription.objects.create(company=self.c, plan=self.plan,
                                    status="active", currency="ZAR", seats=10)
        self.admin = _user(self.c, ["company.manage"], "admin@acme.co")
        self.member = _user(self.c, ["projects.view"], "worker@acme.co")

    def _api(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def test_requires_company_manage(self):
        r = self._api(self.member).get("/api/v1/billing/summary/")
        self.assertEqual(r.status_code, 403)

    def test_requires_auth(self):
        r = APIClient().get("/api/v1/billing/summary/")
        self.assertEqual(r.status_code, 401)

    def test_returns_plan_and_usage(self):
        r = self._api(self.admin).get("/api/v1/billing/summary/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "active")
        self.assertEqual(r.data["plan"]["name"], "Business")
        self.assertTrue(r.data["credits"]["balance"].startswith("120"))
        self.assertTrue(r.data["credits"]["monthly"].startswith("8000"))
        self.assertEqual(r.data["seats"]["included"], 10)
        self.assertGreaterEqual(r.data["seats"]["used"], 2)  # admin + member

    def test_no_subscription_degrades(self):
        # A company without a Subscription row still answers (trial defaults).
        c2 = Company.objects.create(name="NoSub")
        admin2 = _user(c2, ["company.manage"], "a2@nosub.co")
        r = self._api(admin2).get("/api/v1/billing/summary/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["plan"])
