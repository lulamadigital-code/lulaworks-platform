"""Items & Price History screen."""
from datetime import date
from decimal import Decimal
from django.test import TestCase
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.procurement.models import Supplier, SupplierPrice


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class PriceHistoryTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["procurement.manage"], "mgr@a.co")
        self.viewer = _user(self.c, ["projects.view"], "v@a.co")
        with tenant_scope(self.c.id):
            s = Supplier.objects.create(company=self.c, name="Hydraulics SA")
            for yr, p in [(2024, "145"), (2025, "158"), (2026, "173")]:
                SupplierPrice.objects.create(company=self.c, supplier=s, item_key="hydraulic pipe",
                    description="Hydraulic pipe", unit="m", unit_price=Decimal(p), date=date(yr, 3, 1))

    def test_browse_lists_items(self):
        self.client.force_login(self.mgr)
        r = self.client.get("/procurement/price-history/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Hydraulic pipe")

    def test_search_shows_intelligence(self):
        self.client.force_login(self.mgr)
        r = self.client.get("/procurement/price-history/?q=hydraulic+pipe")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Lowest")
        self.assertContains(r, "173.00")   # latest
        self.assertContains(r, "Hydraulics SA")

    def test_gated(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get("/procurement/price-history/").status_code, 302)
