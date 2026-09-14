"""Legacy data normalization — brings existing currency/bank data onto the
reference foundation without destroying anything. Idempotent."""
from django.core.management import call_command
from django.test import TestCase

from apps.identity.models import Company, CompanyBankAccount, User


class NormalizeCompanyDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def _run(self):
        call_command("normalize_company_data", verbosity=0)

    def test_currency_text_normalised_to_iso(self):
        c = Company.objects.create(name="Acme", country_code="ZA", currency="South African Rand")
        self._run()
        c.refresh_from_db()
        self.assertEqual(c.currency, "ZAR")

    def test_currency_symbol_normalised(self):
        c = Company.objects.create(name="Acme2", country_code="GB", currency="£")
        self._run()
        c.refresh_from_db()
        self.assertEqual(c.currency, "GBP")

    def test_bank_name_matched_to_directory(self):
        c = Company.objects.create(name="Acme3", country_code="ZA", currency="ZAR")
        a = CompanyBankAccount.objects.create(company=c, bank_name="FNB",
                                              account_name="Acme3", account_number="123")
        self._run()
        a.refresh_from_db()
        self.assertIsNotNone(a.bank)                 # alias FNB → First National Bank
        self.assertIn("First National", a.bank.name)
        self.assertFalse(a.needs_review)

    def test_unmatched_bank_flagged_not_destroyed(self):
        c = Company.objects.create(name="Acme4", country_code="ZA", currency="ZAR")
        a = CompanyBankAccount.objects.create(company=c, bank_name="Some Village Co-op",
                                              account_name="Acme4", account_number="456")
        self._run()
        a.refresh_from_db()
        self.assertIsNone(a.bank)
        self.assertTrue(a.needs_review)
        self.assertEqual(a.bank_name, "Some Village Co-op")   # original kept

    def test_idempotent(self):
        c = Company.objects.create(name="Acme5", country_code="ZA", currency="Rand")
        self._run()
        self._run()                                  # second run must not error/regress
        c.refresh_from_db()
        self.assertEqual(c.currency, "ZAR")

    def test_email_lowercased_when_safe(self):
        u = User.objects.create_user("Mixed.Case@Acme.CO", "x")
        self._run()
        u.refresh_from_db()
        self.assertEqual(u.email, "mixed.case@acme.co")
