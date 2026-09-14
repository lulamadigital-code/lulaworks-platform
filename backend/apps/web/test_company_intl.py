"""International company profile — country is the source of truth (drives
currency), and the banking form is country-aware and backend-validated."""
from django.core.management import call_command
from django.test import TestCase

from apps.administration.models import CompanySettings
from apps.identity.models import (Company, CompanyBankAccount, Membership,
                                  Permission, Role, User)
from apps.identity.profile import get_profile


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class CompanyCountryCurrencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def setUp(self):
        self.company = Company.objects.create(name="Acme", country_code="ZA", currency="ZAR")
        get_profile(self.company)
        CompanySettings.objects.get_or_create(company=self.company)
        self.mgr = _user(self.company, ["company.manage"], "m@a.co")
        self.client.force_login(self.mgr)

    def test_saving_country_derives_currency(self):
        r = self.client.post("/company/", {"section": "address", "country_code": "GB",
                                           "city": "London", "street_address": "1 High St"})
        self.assertEqual(r.status_code, 302)
        self.company.refresh_from_db()
        self.assertEqual(self.company.country_code, "GB")
        self.assertEqual(self.company.currency, "GBP")
        self.assertIn("United Kingdom", self.company.country)

    def test_profile_page_shows_country_picker_and_derived_currency(self):
        page = self.client.get("/company/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="country_code"')
        self.assertContains(page, "South Africa")
        self.assertContains(page, "ZAR")           # currency shown as info


class BankingValidationWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def _mgr(self, country_code):
        company = Company.objects.create(name="Acme", country_code=country_code, currency="ZAR")
        get_profile(company)
        CompanySettings.objects.get_or_create(company=company)
        mgr = _user(company, ["company.manage"], f"m-{country_code}@a.co")
        self.client.force_login(mgr)
        return company

    def test_sa_valid_bank_account_created(self):
        company = self._mgr("ZA")
        self.client.post("/company/bank/", {
            "action": "add", "bank_name": "Capitec Bank", "account_name": "Acme",
            "account_number": "1234567890", "branch_code": "470010", "account_type": "cheque"})
        self.assertEqual(CompanyBankAccount.objects.filter(company=company).count(), 1)
        acc = CompanyBankAccount.objects.get(company=company)
        self.assertIsNotNone(acc.bank)              # matched to the directory
        self.assertEqual(acc.branch_code, "470010")

    def test_gb_bad_iban_rejected(self):
        company = self._mgr("GB")
        self.client.post("/company/bank/", {
            "action": "add", "bank_name": "Barclays", "account_name": "Acme",
            "account_number": "12345678", "branch_code": "200000",
            "iban": "GB00BUKB20201555555555", "account_type": "cheque"})
        self.assertEqual(CompanyBankAccount.objects.filter(company=company).count(), 0)

    def test_gb_valid_iban_accepted(self):
        company = self._mgr("GB")
        self.client.post("/company/bank/", {
            "action": "add", "bank_name": "Barclays", "account_name": "Acme",
            "account_number": "12345678", "branch_code": "200000",
            "iban": "GB33BUKB20201555555555", "account_type": "cheque"})
        self.assertEqual(CompanyBankAccount.objects.filter(company=company).count(), 1)
