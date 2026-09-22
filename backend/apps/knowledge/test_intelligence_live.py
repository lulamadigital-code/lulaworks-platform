"""P1-1: customer_intelligence projects LIVE ERP records (projects.Project), not
just the imported archive — so the intelligence reflects current business."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.intelligence import customer_intelligence
from apps.projects.models import Project


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class LiveCustomerIntelligenceTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.finance = _user(self.c, ["finance.view_money"], "cfo@acme.co")
        self.field = _user(self.c, ["projects.view"], "worker@acme.co")

    def _customer_with_live_job(self):
        with tenant_scope(self.c.id):
            cust = Customer.objects.create(company=self.c, name="ABC Mining")
            Project.objects.create(company=self.c, number="JOB-1",
                                   client_name="ABC Mining", customer=cust,
                                   work_type="hydraulic")
            return cust

    def test_live_job_alone_is_found(self):
        # Before P1-1 a customer with only live (un-imported) work returned
        # found=False. Now the live ERP record surfaces.
        cust = self._customer_with_live_job()
        with tenant_scope(self.c.id):
            intel = customer_intelligence(cust, self.finance)
        self.assertTrue(intel["found"])
        self.assertEqual(intel["job_count"], 1)
        self.assertEqual(intel["sources_summary"], {"live": 1, "imported": 0})
        self.assertEqual(intel["last_job"]["origin"], "live")
        self.assertEqual(intel["last_job"]["work_type"], "hydraulic")

    def test_empty_customer_still_not_found(self):
        with tenant_scope(self.c.id):
            cust = Customer.objects.create(company=self.c, name="No History Co")
            intel = customer_intelligence(cust, self.finance)
        self.assertFalse(intel["found"])

    def test_field_worker_sees_the_job_without_value(self):
        cust = self._customer_with_live_job()
        with tenant_scope(self.c.id):
            intel = customer_intelligence(cust, self.field)
        self.assertTrue(intel["found"])
        self.assertFalse(intel["money_visible"])
        self.assertIsNone(intel["total_value"])
        self.assertIsNone(intel["last_job"]["value"])   # money gated
