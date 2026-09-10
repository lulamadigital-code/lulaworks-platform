"""Historical price intelligence (AI OS §11/§18) — what we paid, who's cheapest,
how it trended — computed from the append-only supplier price ledger."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company
from apps.procurement.models import Supplier, SupplierPrice
from apps.procurement.services import price_intelligence, record_prices


class PriceIntelligenceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        with tenant_scope(self.company.id):
            self.a = Supplier.objects.create(company=self.company, name="Hydraulics SA")
            self.b = Supplier.objects.create(company=self.company, name="PumpCo")
            # Supplier A: rising price over three years.
            for yr, price in [(2024, "145"), (2025, "158"), (2026, "173")]:
                SupplierPrice.objects.create(
                    company=self.company, supplier=self.a, item_key="hydraulic pipe",
                    description="Hydraulic pipe", unit="m", unit_price=Decimal(price),
                    date=date(yr, 3, 1))
            # Supplier B: one cheaper recent price.
            SupplierPrice.objects.create(
                company=self.company, supplier=self.b, item_key="hydraulic pipe",
                description="Hydraulic pipe", unit="m", unit_price=Decimal("151"),
                date=date(2026, 6, 1))

    def test_summarises_history(self):
        with tenant_scope(self.company.id):
            pi = price_intelligence(self.company, "hydraulic pipe")
        self.assertTrue(pi["found"])
        self.assertEqual(pi["point_count"], 4)
        # Most recent point is PumpCo @ 151.
        self.assertEqual(pi["last_supplier"], "PumpCo")
        self.assertEqual(pi["last_price"], "151.00")
        # Cheapest by latest price is PumpCo.
        self.assertEqual(pi["cheapest_supplier"], "PumpCo")
        self.assertEqual(pi["min_price"], "145.00")
        self.assertEqual(pi["max_price"], "173.00")
        # Two suppliers surfaced.
        self.assertEqual({s["supplier"] for s in pi["suppliers"]}, {"Hydraulics SA", "PumpCo"})

    def test_unknown_item_is_empty_not_invented(self):
        with tenant_scope(self.company.id):
            pi = price_intelligence(self.company, "unobtanium widget")
        self.assertFalse(pi["found"])
        self.assertEqual(pi["points"], [])

    def test_record_prices_skips_priceless_lines(self):
        with tenant_scope(self.company.id):
            n = record_prices(self.company, self.a, [
                {"description": "Gasket", "unit": "each", "unit_price": "42"},
                {"description": "Vague item", "unit": "each", "unit_price": ""},  # no price
            ])
        self.assertEqual(n, 1)  # only the priced line recorded
