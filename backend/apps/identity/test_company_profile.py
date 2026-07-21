"""Company profile — the single source of truth for company identity.

The invariant worth protecting: company information is stored ONCE and every
document reads it from here. A test that a VAT number typed on the profile
appears on a generated quotation is what stops the codebase drifting back to
per-module copies.
"""

from django.test import TestCase

from apps.administration.models import CompanySettings
from apps.identity.models import Company, CompanyBankAccount, CompanyContact
from apps.identity.profile import (
    add_bank_account,
    add_contact,
    completeness,
    default_bank_account,
    document_header,
    get_profile,
    postal_address_lines,
    physical_address_lines,
    set_default_bank_account,
)


def make_company(**kw):
    defaults = dict(name="Lulama Projects (Pty) Ltd", registration_no="2019/456789/07",
                    vat_no="4230192837", tax_reference_no="9012345678",
                    company_type="Pty Ltd", industry="Contracting",
                    email="accounts@lulama.co.za", phone="+27 13 656 1122",
                    street_address="14 Nywerheid Street", city="Secunda",
                    province="Mpumalanga", postal_code="2302")
    defaults.update(kw)
    return Company.objects.create(**defaults)


class ProfileAccessTests(TestCase):
    def test_get_profile_creates_the_one_to_one_rows(self):
        """Callers must never have to handle a missing compliance/branding side."""
        company = make_company()
        profile = get_profile(company)
        self.assertIsNotNone(profile.compliance)
        self.assertIsNotNone(profile.branding)
        self.assertTrue(CompanySettings.objects.filter(company=company).exists())

    def test_get_profile_is_idempotent(self):
        company = make_company()
        get_profile(company)
        get_profile(company)
        self.assertEqual(company.compliance.pk, get_profile(company).compliance.pk)


class BankAccountTests(TestCase):
    def test_first_account_becomes_the_default_automatically(self):
        company = make_company()
        account = add_bank_account(company, bank_name="FNB", account_name="Lulama",
                                   account_number="62845019273")
        self.assertTrue(account.is_default)
        self.assertEqual(default_bank_account(company), account)

    def test_only_one_default_survives(self):
        company = make_company()
        first = add_bank_account(company, bank_name="FNB", account_name="L",
                                 account_number="1111")
        second = add_bank_account(company, bank_name="Absa", account_name="L",
                                  account_number="2222")
        self.assertFalse(second.is_default)

        set_default_bank_account(second)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertEqual(default_bank_account(company), second)
        self.assertEqual(
            CompanyBankAccount.objects.filter(company=company, is_default=True).count(), 1)

    def test_account_numbers_are_masked_for_display(self):
        company = make_company()
        account = add_bank_account(company, bank_name="FNB", account_name="L",
                                   account_number="62845019273")
        self.assertEqual(account.masked_number, "••••9273")
        # The full number is still stored — the invoice needs it.
        self.assertEqual(account.account_number, "62845019273")


class ContactTests(TestCase):
    def test_first_contact_becomes_primary_and_only_one_stays(self):
        company = make_company()
        a = add_contact(company, full_name="Ronny Maluleke", job_title="MD")
        self.assertTrue(a.is_primary)
        b = add_contact(company, full_name="Naledi Dlamini", job_title="Finance")
        b.is_primary = True
        b.save()
        a.refresh_from_db()
        self.assertFalse(a.is_primary)
        self.assertEqual(CompanyContact.objects.filter(
            company=company, is_primary=True).count(), 1)


class AddressTests(TestCase):
    def test_postal_falls_back_to_physical_when_marked_the_same(self):
        """The checkbox means "don't make me type it twice" — so nothing is
        stored twice either."""
        company = make_company(postal_same_as_physical=True)
        self.assertEqual(postal_address_lines(company), physical_address_lines(company))

    def test_separate_postal_address_is_used_when_given(self):
        company = make_company(postal_same_as_physical=False,
                               postal_address="PO Box 1234", postal_city="Secunda",
                               postal_code_postal="2302")
        lines = postal_address_lines(company)
        self.assertIn("PO Box 1234", lines)
        self.assertNotIn("14 Nywerheid Street", lines)


class DocumentHeaderTests(TestCase):
    """The payoff: one call gives a document everything it needs about us."""

    def test_header_carries_identity_address_and_banking(self):
        company = make_company()
        add_bank_account(company, bank_name="FNB", account_name="Lulama Projects",
                         account_number="62845019273", branch_code="250655")
        add_contact(company, full_name="Ronny Maluleke", job_title="MD",
                    email="ronny@lulama.co.za")

        header = document_header(company)

        self.assertEqual(header["registration_no"], "2019/456789/07")
        self.assertEqual(header["vat_no"], "4230192837")
        self.assertIn("14 Nywerheid Street", header["address_lines"])
        self.assertEqual(header["bank"]["account_number"], "62845019273")
        self.assertEqual(header["contact"]["name"], "Ronny Maluleke")

    def test_header_omits_banking_rather_than_inventing_it(self):
        header = document_header(make_company())
        self.assertIsNone(header["bank"])
        self.assertIsNone(header["contact"])

    def test_trading_name_is_preferred_for_display(self):
        company = make_company(trading_name="Lulama Projects")
        self.assertEqual(document_header(company)["display_name"], "Lulama Projects")


class CompletenessTests(TestCase):
    def test_empty_company_scores_low_and_names_the_consequences(self):
        company = Company.objects.create(name="Bare Co")
        score = completeness(company)
        self.assertLess(score["overall"], 40)
        self.assertTrue(score["blocks"])
        self.assertTrue(any("payment details" in b for b in score["blocks"]))

    def test_a_filled_profile_scores_high_and_blocks_nothing(self):
        company = make_company()
        get_profile(company)
        add_bank_account(company, bank_name="FNB", account_name="L", account_number="1111")
        c = company.compliance
        c.income_tax_no = "9012345678"
        c.csd_supplier_no = "MAAA0123456"
        c.save()

        score = completeness(company)
        self.assertGreaterEqual(score["overall"], 90)
        self.assertEqual(score["blocks"], [])

    def test_vat_registered_without_a_number_is_flagged(self):
        """A tax invoice is not valid without the number — say so, don't score it."""
        company = make_company(vat_no="")
        profile = get_profile(company)
        profile.compliance.vat_registered = True
        profile.compliance.save()
        add_bank_account(company, bank_name="FNB", account_name="L", account_number="1")

        blocks = completeness(company)["blocks"]
        self.assertTrue(any("VAT" in b for b in blocks))


class QuotationPdfUsesProfileTests(TestCase):
    """The rule the whole module exists for: change it here, it changes there."""

    def test_profile_details_appear_on_the_generated_quotation(self):
        import pdfplumber

        from apps.core.context import tenant_scope
        from apps.quotes.models import Quotation
        from apps.quotes.pdf import quotation_pdf_bytes

        company = make_company()
        get_profile(company)
        add_bank_account(company, bank_name="First National Bank",
                         account_name="Lulama Projects", account_number="62845019273",
                         branch_code="250655")

        with tenant_scope(company.id):
            quote = Quotation.objects.create(
                company=company, number="QT-TEST-1", client_name="Sasol", site="Secunda")
            quote.lines.create(company=company, position=1, description="Steel pipe",
                               qty=10, unit_price=486)
            pdf = quotation_pdf_bytes(quote)

        import io
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            text = doc.pages[0].extract_text()

        self.assertIn("2019/456789/07", text)          # registration number
        self.assertIn("4230192837", text)              # VAT number
        self.assertIn("14 Nywerheid Street", text)     # address
        self.assertIn("62845019273", text)             # banking, so a client can pay
        self.assertIn("250655", text)

    def test_company_name_is_not_printed_twice_without_a_logo(self):
        """Regression: the name stood in for the missing logo AND headed the
        identity block, printing it twice."""
        import io

        import pdfplumber

        from apps.core.context import tenant_scope
        from apps.quotes.models import Quotation
        from apps.quotes.pdf import quotation_pdf_bytes

        company = make_company(trading_name="Lulama Projects")
        get_profile(company)
        with tenant_scope(company.id):
            quote = Quotation.objects.create(company=company, number="QT-TEST-2",
                                             client_name="Sasol")
            quote.lines.create(company=company, position=1, description="X", qty=1,
                               unit_price=1)
            pdf = quotation_pdf_bytes(quote)

        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            first_line = doc.pages[0].extract_text().splitlines()[0]
        self.assertEqual(first_line.count("Lulama Projects"), 1)
