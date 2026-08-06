"""Tax engine: jurisdiction lookup, company defaults, and the compute_tax
decision — domestic rate, inclusive/exclusive, and cross-border reverse charge."""

from decimal import Decimal

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company

from .models import TaxJurisdiction
from .services import apply_company_jurisdiction_defaults, compute_tax, jurisdiction_for


def _seed():
    TaxJurisdiction.objects.create(name="South Africa", code="ZA", tax_name="VAT", rate=15)
    TaxJurisdiction.objects.create(name="Germany", code="DE", tax_name="VAT", rate=19,
                                   prices_include_tax=True, reverse_charge_region="EU")
    TaxJurisdiction.objects.create(name="France", code="FR", tax_name="VAT", rate=20,
                                   prices_include_tax=True, reverse_charge_region="EU")
    TaxJurisdiction.objects.create(name="United States", code="US", tax_name="Sales Tax", rate=0)


class TaxEngineTests(TestCase):
    def setUp(self):
        _seed()

    def _customer(self, company, **kw):
        with tenant_scope(company.id):
            return Customer.objects.create(company=company, **kw)

    def test_jurisdiction_lookup_is_case_insensitive(self):
        self.assertEqual(jurisdiction_for("south africa").tax_name, "VAT")
        self.assertIsNone(jurisdiction_for("Narnia"))

    def test_apply_country_defaults(self):
        c = Company.objects.create(name="Berlin Bau", country="Germany")
        self.assertTrue(apply_company_jurisdiction_defaults(c))
        c.refresh_from_db()
        self.assertEqual(c.default_tax_rate, Decimal("19"))
        self.assertEqual(c.tax_name, "VAT")
        self.assertTrue(c.prices_include_tax)

    def test_domestic_tax(self):
        c = Company.objects.create(name="SA Co", country="South Africa",
                                   default_tax_rate=Decimal("15"), tax_name="VAT")
        d = compute_tax(c)
        self.assertEqual(d.rate, Decimal("15"))
        self.assertEqual(d.tax_name, "VAT")
        self.assertFalse(d.reverse_charge)

    def test_reverse_charge_for_registered_cross_border_business(self):
        c = Company.objects.create(name="DE Co", country="Germany",
                                   default_tax_rate=Decimal("19"), tax_name="VAT",
                                   reverse_charge_enabled=True)
        cust = self._customer(c, name="FR Client", country="France", vat_no="FR123456")
        d = compute_tax(c, customer=cust)
        self.assertTrue(d.reverse_charge)
        self.assertEqual(d.rate, Decimal("0"))
        self.assertIn("Reverse charge", d.note)

    def test_no_reverse_charge_when_company_disables_it(self):
        c = Company.objects.create(name="DE Co2", country="Germany",
                                   default_tax_rate=Decimal("19"), tax_name="VAT",
                                   reverse_charge_enabled=False)
        cust = self._customer(c, name="FR Client", country="France", vat_no="FR123456")
        d = compute_tax(c, customer=cust)
        self.assertFalse(d.reverse_charge)
        self.assertEqual(d.rate, Decimal("19"))

    def test_unregistered_cross_border_customer_keeps_domestic_rate(self):
        c = Company.objects.create(name="DE Co3", country="Germany",
                                   default_tax_rate=Decimal("19"), tax_name="VAT",
                                   reverse_charge_enabled=True)
        cust = self._customer(c, name="Consumer", country="France")  # no tax number
        d = compute_tax(c, customer=cust)
        self.assertFalse(d.reverse_charge)     # not a registered business → normal tax
        self.assertEqual(d.rate, Decimal("19"))

    def test_inclusive_flag_flows_from_company(self):
        c = Company.objects.create(name="Incl Co", country="Germany",
                                   default_tax_rate=Decimal("19"), tax_name="VAT",
                                   prices_include_tax=True)
        self.assertTrue(compute_tax(c).inclusive)
