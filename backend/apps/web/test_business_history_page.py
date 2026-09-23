"""The Business-History UI page renders the facade for any record."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.projects.models import Project
from apps.quotes.models import Quotation


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class BusinessHistoryPageTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, ["finance.view_money", "projects.view"], "mgr@acme.co")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")
            self.q = Quotation.objects.create(company=self.c, customer=self.cust,
                                              number="QTN-1", client_name="ABC Mining")
            self.job = Project.objects.create(company=self.c, number="JOB-1",
                                              client_name="ABC Mining", customer=self.cust)
        self.client.force_login(self.u)

    def test_customer_page_renders_facade(self):
        r = self.client.get(f"/business-history/customer/{self.cust.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ABC Mining")
        self.assertContains(r, "Business History")
        self.assertContains(r, "Related records")   # the transaction graph section

    def test_job_page_renders(self):
        r = self.client.get(f"/business-history/job/{self.job.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "JOB-1")

    def test_unknown_record_404(self):
        r = self.client.get(
            "/business-history/customer/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(r.status_code, 404)
