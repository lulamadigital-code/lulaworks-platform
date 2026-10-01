"""Plan-entitlement gate on RFQ extraction (§9): a Starter plan (no
rfq_extraction) is blocked; Professional (has it) is allowed."""
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from apps.billing.models import Plan, Subscription
from apps.identity.models import Company, Membership, Permission, Role, User


def _company_on(plan_entitlements):
    c = Company.objects.create(name="Acme")
    plan = Plan.objects.create(code=f"p{Plan.objects.count()}", name="P", tier=1,
                               module_entitlements=plan_entitlements)
    Subscription.objects.create(company=c, plan=plan, status="active")
    return c


def _user(company):
    role = Role.objects.create(name=f"R{Role.objects.count()}", is_system=True)
    for code in ("rfq.upload", "rfq.approve"):
        p, _ = Permission.objects.get_or_create(
            codename=code, defaults={"module": "rfq", "label": code})
        role.permissions.add(p)
    u = User.objects.create_user(f"u{User.objects.count()}@acme.co", "x",
                                 active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class RfqPlanGateTests(APITestCase):
    _TEXT = "RFQ from Acme\n1 Pump 2 each\n"

    def test_starter_blocked(self):
        c = _company_on(["basic_procurement", "pdf_export"])  # no rfq_extraction
        self.client.force_authenticate(_user(c))
        r = self.client.post("/api/v1/rfqs/from-text/", {"text": self._TEXT}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_pro_allowed(self):
        c = _company_on(["basic_procurement", "rfq_extraction"])
        self.client.force_authenticate(_user(c))
        r = self.client.post("/api/v1/rfqs/from-text/", {"text": self._TEXT}, format="json")
        self.assertEqual(r.status_code, 201)
