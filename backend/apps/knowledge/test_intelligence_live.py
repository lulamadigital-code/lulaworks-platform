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


class LiveSupplierIntelligenceTests(TestCase):
    """P1: supplier_intelligence projects the LIVE procurement ledger
    (procurement.SupplierPrice), not just the imported archive."""

    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.finance = _user(self.c, ["finance.view_money"], "cfo3@acme.co")
        self.worker = _user(self.c, ["projects.view"], "worker3@acme.co")

    def _supplier_with_live_price(self):
        from datetime import date
        from apps.procurement.models import Supplier, SupplierPrice
        with tenant_scope(self.c.id):
            sup = Supplier.objects.create(company=self.c, name="Hydraulics SA")
            SupplierPrice.objects.create(
                company=self.c, supplier=sup, item_key="hydraulic hose 2in",
                description='Hydraulic Hose 2"', unit="each", unit_price=470,
                date=date(2026, 1, 20))
            return sup

    def test_live_supplier_price_surfaces(self):
        from apps.knowledge.intelligence import supplier_intelligence
        sup = self._supplier_with_live_price()
        with tenant_scope(self.c.id):
            intel = supplier_intelligence(sup, self.finance)
        self.assertTrue(intel["found"])
        self.assertEqual(intel["purchase_count"], 1)
        self.assertEqual(intel["sources_summary"], {"live": 1, "imported": 0})
        item = intel["items"][0]
        self.assertEqual(item["origin"], "live")
        self.assertEqual(item["unit_price"], "470.00")   # purchase prices always show

    def test_purchase_price_shown_even_without_finance_perm(self):
        # Purchase prices are procurement's own domain (§10) — a non-finance user
        # still sees them here.
        from apps.knowledge.intelligence import supplier_intelligence
        sup = self._supplier_with_live_price()
        with tenant_scope(self.c.id):
            intel = supplier_intelligence(sup, self.worker)
        self.assertEqual(intel["items"][0]["unit_price"], "470.00")

    def test_empty_supplier_not_found(self):
        from apps.knowledge.intelligence import supplier_intelligence
        from apps.procurement.models import Supplier
        with tenant_scope(self.c.id):
            sup = Supplier.objects.create(company=self.c, name="Nobody Ltd")
            intel = supplier_intelligence(sup, self.finance)
        self.assertFalse(intel["found"])
