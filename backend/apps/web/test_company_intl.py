"""International company profile — country is the source of truth (drives
currency), and the banking form is country-aware and backend-validated."""
from django.core.management import call_command
from django.test import TestCase

from apps.administration.models import CompanySettings
from apps.identity.models import (Company, CompanyBankAccount, CompanyContact,
                                  Membership, Permission, Role, User)
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


class PhoneWidgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def setUp(self):
        self.company = Company.objects.create(name="Acme", country_code="ZA", currency="ZAR")
        get_profile(self.company)
        CompanySettings.objects.get_or_create(company=self.company)
        self.mgr = _user(self.company, ["company.manage"], "m@a.co")
        self.client.force_login(self.mgr)

    def test_contact_phone_stored_as_e164(self):
        r = self.client.post("/company/", {
            "section": "contact", "email": "a@acme.co",
            "phone": "0821234567", "phone__cc": "ZA"})
        self.assertEqual(r.status_code, 302)
        self.company.refresh_from_db()
        self.assertEqual(self.company.phone, "+27821234567")

    def test_country_code_picker_applies(self):
        # A UK number entered with the GB country code normalises to +44.
        self.client.post("/company/", {
            "section": "contact", "email": "a@acme.co",
            "mobile": "020 7946 0958", "mobile__cc": "GB"})
        self.company.refresh_from_db()
        self.assertEqual(self.company.mobile, "+442079460958")

    def test_invalid_phone_rejected_nothing_saved(self):
        self.client.post("/company/", {
            "section": "contact", "email": "a@acme.co",
            "phone": "12", "phone__cc": "ZA"})
        self.company.refresh_from_db()
        self.assertEqual(self.company.phone, "")      # invalid → not saved

    def test_member_phone_normalised(self):
        self.client.post("/company/contacts/", {
            "action": "add", "full_name": "Thabo", "email": "t@acme.co",
            "phone": "0821234567", "phone__cc": "ZA"})
        c = CompanyContact.objects.get(company=self.company, full_name="Thabo")
        self.assertEqual(c.phone, "+27821234567")


class AddressValidationWebTests(TestCase):
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

    def test_sa_valid_address_saved(self):
        company = self._mgr("ZA")
        r = self.client.post("/company/", {
            "section": "address", "country_code": "ZA", "street_address": "1 Main",
            "city": "Secunda", "province": "Mpumalanga", "postal_code": "2302"})
        self.assertEqual(r.status_code, 302)
        company.refresh_from_db()
        self.assertEqual(company.postal_code, "2302")

    def test_sa_bad_postal_rejected(self):
        company = self._mgr("ZA")
        self.client.post("/company/", {
            "section": "address", "country_code": "ZA", "street_address": "1 Main",
            "city": "Secunda", "province": "Mpumalanga", "postal_code": "12"})
        company.refresh_from_db()
        self.assertEqual(company.postal_code, "")      # invalid → nothing saved

    def test_us_country_aware_labels(self):
        self._mgr("US")
        page = self.client.get("/company/")
        self.assertContains(page, "ZIP code")           # not "Postal code"
        self.assertContains(page, "State")

    def test_us_bad_zip_rejected(self):
        company = self._mgr("US")
        self.client.post("/company/", {
            "section": "address", "country_code": "US", "street_address": "1 Main",
            "city": "NYC", "province": "NY", "postal_code": "notazip"})
        company.refresh_from_db()
        self.assertEqual(company.postal_code, "")


class StatutoryEngineWebTests(TestCase):
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

    def test_sa_statutory_writes_typed_columns(self):
        company = self._mgr("ZA")
        self.client.post("/company/", {
            "section": "compliance", "vat_registered": "1",
            "stat__bbbee_level": "Level 2", "stat__coida_no": "CO-123",
            "stat__cidb_grading": "3CE"})
        company.compliance.refresh_from_db()
        self.assertEqual(company.compliance.bbbee_level, "Level 2")   # typed column
        self.assertEqual(company.compliance.coida_no, "CO-123")
        self.assertTrue(company.compliance.vat_registered)

    def test_us_statutory_writes_json_no_cipc(self):
        company = self._mgr("US")
        page = self.client.get("/company/")
        self.assertNotContains(page, "CIDB")
        self.assertNotContains(page, "B-BBEE")
        self.assertContains(page, "EIN")
        self.client.post("/company/", {"section": "compliance", "stat__ein": "12-3456789"})
        company.compliance.refresh_from_db()
        self.assertEqual(company.compliance.other_registrations.get("ein"), "12-3456789")

    def test_us_bad_ein_rejected(self):
        company = self._mgr("US")
        self.client.post("/company/", {"section": "compliance", "stat__ein": "bad"})
        company.compliance.refresh_from_db()
        self.assertNotEqual(company.compliance.other_registrations.get("ein"), "bad")


class PostalAddressWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def _mgr(self, country_code="ZA"):
        company = Company.objects.create(name="Acme", country_code=country_code, currency="ZAR")
        get_profile(company)
        CompanySettings.objects.get_or_create(company=company)
        mgr = _user(company, ["company.manage"], f"mp-{country_code}@a.co")
        self.client.force_login(mgr)
        return company

    def test_bad_postal_code_rejected(self):
        company = self._mgr("ZA")
        self.client.post("/company/", {
            "section": "postal", "postal_address": "PO Box 1", "postal_city": "Secunda",
            "postal_code_postal": "12"})     # invalid SA postal
        company.refresh_from_db()
        self.assertEqual(company.postal_code_postal, "")

    def test_valid_postal_saved(self):
        company = self._mgr("ZA")
        self.client.post("/company/", {
            "section": "postal", "postal_address": "PO Box 1", "postal_city": "Secunda",
            "postal_code_postal": "2302"})
        company.refresh_from_db()
        self.assertEqual(company.postal_code_postal, "2302")

    def test_same_as_physical_skips_validation(self):
        company = self._mgr("ZA")
        r = self.client.post("/company/", {
            "section": "postal", "postal_same_as_physical": "1",
            "postal_code_postal": "12"})     # invalid, but skipped
        self.assertEqual(r.status_code, 302)
        company.refresh_from_db()
        self.assertTrue(company.postal_same_as_physical)


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
