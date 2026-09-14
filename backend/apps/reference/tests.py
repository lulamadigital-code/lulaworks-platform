"""Reference data + validation engine — the country-driven foundation the whole
platform relies on. Seeds the real reference data once, then exercises each
service the way the forms and API will."""
from django.core.management import call_command
from django.test import TestCase

from apps.reference.models import Bank, Country, CountryCurrency, Currency
from apps.reference.services import (AddressValidationService,
                                     BankDirectoryService,
                                     BankingValidationService, CountryService,
                                     CurrencyService, EmailValidationService,
                                     PhoneValidationService)


class SeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_seed_populates_core_tables(self):
        self.assertTrue(Country.objects.filter(code="ZA").exists())
        self.assertTrue(Currency.objects.filter(code="ZAR").exists())
        self.assertTrue(Bank.objects.filter(country_id="ZA", name__icontains="Capitec").exists())

    def test_seed_is_idempotent(self):
        c1, b1 = Country.objects.count(), Bank.objects.count()
        call_command("seed_reference")
        self.assertEqual((c1, b1), (Country.objects.count(), Bank.objects.count()))

    def test_south_africa_sorts_first(self):
        first = CountryService.list_for_picker()[0]
        self.assertEqual(first.code, "ZA")


class CurrencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_country_drives_currency(self):
        self.assertEqual(CurrencyService.for_country("ZA").code, "ZAR")
        self.assertEqual(CurrencyService.for_country("GB").code, "GBP")
        self.assertEqual(CurrencyService.for_country("US").code, "USD")

    def test_exceptional_mapping_overrides_default(self):
        # A configured default CountryCurrency wins over the country's own FK.
        eur = Currency.objects.get(code="EUR")
        zw = Country.objects.filter(code="ZW").first() or Country.objects.create(code="ZW", name="Zimbabwe")
        CountryCurrency.objects.update_or_create(country=zw, currency=eur, defaults={"is_default": True})
        self.assertEqual(CurrencyService.for_country("ZW").code, "EUR")


class BankDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_sa_banks_major_first(self):
        banks = BankDirectoryService.for_country("ZA")
        self.assertTrue(banks[0].is_major)

    def test_search_matches_name(self):
        hits = BankDirectoryService.for_country("ZA", q="cap")
        self.assertIn("Capitec Bank", [b.name for b in hits])

    def test_match_alias(self):
        b = BankDirectoryService.match("ZA", "FNB")
        self.assertIsNotNone(b)
        self.assertIn("First National", b.name)


class BankingValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_iban_checksum(self):
        self.assertTrue(BankingValidationService.iban("GB33BUKB20201555555555").valid)
        self.assertEqual(BankingValidationService.iban("GB00BUKB20201555555555").code,
                         "INVALID_IBAN_CHECKSUM")

    def test_swift_structure(self):
        self.assertTrue(BankingValidationService.swift_bic("absazajj").valid)   # normalised upper
        self.assertFalse(BankingValidationService.swift_bic("ABC").valid)

    def test_za_requires_branch_code(self):
        errs = BankingValidationService.validate("ZA", {
            "account_name": "Acme", "account_number": "1234567890"})
        self.assertTrue(any(e.field == "branch_code" for e in errs))

    def test_za_valid_passes(self):
        errs = BankingValidationService.validate("ZA", {
            "account_name": "Acme", "account_number": "1234567890", "branch_code": "470010"})
        self.assertEqual(errs, [])

    def test_us_bad_routing_number(self):
        errs = BankingValidationService.validate("US", {
            "account_name": "Acme", "account_number": "123456", "routing_number": "12"})
        self.assertTrue(any(e.code == "INVALID_ROUTING" for e in errs))


class PhoneTests(TestCase):
    def test_valid_numbers(self):
        self.assertEqual(PhoneValidationService.validate("0821234567", "ZA").e164, "+27821234567")
        self.assertTrue(PhoneValidationService.validate("(202) 555-0182", "US").valid)
        self.assertTrue(PhoneValidationService.validate("+44 20 7946 0958", "GB").valid)

    def test_invalid_number(self):
        self.assertFalse(PhoneValidationService.validate("12", "ZA").valid)

    def test_mobile_type_detected(self):
        self.assertEqual(PhoneValidationService.validate("0821234567", "ZA").number_type, "mobile")


class AddressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_sa_postal_rule(self):
        errs = AddressValidationService.validate("ZA", {
            "street_address": "1 Main", "city": "Secunda", "province": "MP", "postal_code": "12"})
        self.assertTrue(any(e.field == "postal_code" for e in errs))

    def test_sa_valid_address(self):
        errs = AddressValidationService.validate("ZA", {
            "street_address": "1 Main", "city": "Secunda", "province": "MP", "postal_code": "2302"})
        self.assertEqual(errs, [])

    def test_us_zip(self):
        errs = AddressValidationService.validate("US", {
            "street_address": "1 Main", "city": "NYC", "province": "NY", "postal_code": "10001"})
        self.assertEqual(errs, [])


class StatutoryEngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_country_specific_rules(self):
        from apps.reference.services import StatutoryService
        za = {r["key"] for r in StatutoryService.rules("ZA")}
        us = {r["key"] for r in StatutoryService.rules("US")}
        gb = {r["key"] for r in StatutoryService.rules("GB")}
        self.assertIn("bbbee_level", za)
        self.assertIn("cidb_grading", za)
        self.assertIn("ein", us)
        self.assertNotIn("bbbee_level", us)      # no B-BBEE for the US
        self.assertNotIn("cidb_grading", us)     # no CIDB for the US
        self.assertIn("company_no", gb)
        self.assertNotIn("bbbee_level", gb)

    def test_sa_rules_map_to_typed_columns(self):
        from apps.reference.services import StatutoryService
        by_key = {r["key"]: r for r in StatutoryService.rules("ZA")}
        self.assertEqual(by_key["bbbee_level"]["column"], "bbbee_level")
        self.assertEqual(by_key["coida_expiry"]["field_type"], "date")

    def test_bbbee_not_required(self):
        from apps.reference.services import StatutoryService
        by_key = {r["key"]: r for r in StatutoryService.rules("ZA")}
        self.assertFalse(by_key["bbbee_level"]["required"])

    def test_ein_format_validation(self):
        from apps.reference.services import StatutoryService
        errs = StatutoryService.validate("US", {"ein": "not-an-ein"})
        self.assertTrue(any(e.field == "ein" for e in errs))
        self.assertEqual(StatutoryService.validate("US", {"ein": "12-3456789"}), [])


class DocumentRulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_recommendations_country_specific(self):
        from apps.reference.services import recommended_documents
        za = " ".join(recommended_documents("ZA")).lower()
        us = " ".join(recommended_documents("US")).lower()
        self.assertIn("cipc", za)
        self.assertIn("b-bbee", za)
        self.assertNotIn("cipc", us)             # no SA docs for the US
        self.assertNotIn("b-bbee", us)
        self.assertIn("ein", us)


class ReferenceApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def setUp(self):
        from apps.identity.models import Company, User
        company = Company.objects.create(name="Acme", country_code="ZA")
        self.user = User.objects.create_user("api@a.co", "x", active_company=company)
        self.client.force_login(self.user)

    def test_statutory_rules_endpoint(self):
        r = self.client.get("/api/v1/reference/countries/US/statutory-rules/")
        self.assertEqual(r.status_code, 200)
        keys = {f["key"] for f in r.json()["fields"]}
        self.assertIn("ein", keys)
        self.assertNotIn("bbbee_level", keys)

    def test_documents_endpoint(self):
        r = self.client.get("/api/v1/reference/countries/ZA/documents/")
        self.assertEqual(r.status_code, 200)
        names = " ".join(d["name"] for d in r.json()["documents"]).lower()
        self.assertIn("cipc", names)

    def test_requires_auth(self):
        self.client.logout()
        r = self.client.get("/api/v1/reference/countries/ZA/statutory-rules/")
        self.assertIn(r.status_code, (401, 403))


class KenyaConfigTests(TestCase):
    """Kenya is a first-class country configuration — added purely as seed data,
    no engine code changes. Proves 'add a country once' end to end."""
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_currency_and_banks(self):
        from apps.reference.services import BankDirectoryService, CurrencyService
        self.assertEqual(CurrencyService.for_country("KE").code, "KES")
        names = [b.name for b in BankDirectoryService.for_country("KE")]
        self.assertIn("Equity Bank", names)
        self.assertIn("KCB Bank", names)

    def test_statutory_is_kenyan_not_generic(self):
        from apps.reference.services import StatutoryService
        keys = {f["key"] for f in StatutoryService.rules("KE")}
        self.assertIn("kra_pin", keys)
        self.assertIn("nssf_no", keys)
        self.assertNotIn("bbbee_level", keys)     # no SA fields
        self.assertNotIn("ein", keys)             # no US fields

    def test_kra_pin_validation(self):
        from apps.reference.services import StatutoryService
        self.assertEqual(StatutoryService.validate("KE", {"kra_pin": "A012345678Z"}), [])
        self.assertTrue(any(e.field == "kra_pin"
                            for e in StatutoryService.validate("KE", {"kra_pin": "nope"})))

    def test_address_and_postal(self):
        from apps.reference.services import AddressValidationService
        rule = AddressValidationService.rule("KE")
        self.assertIn("County", [f["label"] for f in rule.fields])
        self.assertIsNotNone(AddressValidationService.validate_postal_code("KE", "1"))   # bad
        self.assertIsNone(AddressValidationService.validate_postal_code("KE", "00100"))  # good

    def test_documents_are_kenyan(self):
        from apps.reference.services import recommended_documents
        docs = " ".join(recommended_documents("KE")).lower()
        self.assertIn("kra pin", docs)
        self.assertNotIn("cipc", docs)            # no SA docs


class NewZealandConfigTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference")

    def test_currency_is_nzd_and_banks(self):
        from apps.reference.services import BankDirectoryService, CurrencyService
        self.assertEqual(CurrencyService.for_country("NZ").code, "NZD")   # NZD, not AUD
        names = [b.name for b in BankDirectoryService.for_country("NZ")]
        self.assertTrue(any("Kiwibank" in n for n in names))
        self.assertTrue(any("BNZ" in n or "Bank of New Zealand" in n for n in names))

    def test_statutory_is_nz(self):
        from apps.reference.services import StatutoryService
        keys = {f["key"] for f in StatutoryService.rules("NZ")}
        self.assertIn("nzbn", keys)
        self.assertIn("ird", keys)
        self.assertNotIn("bbbee_level", keys)
        self.assertNotIn("kra_pin", keys)          # not Kenya's either

    def test_ird_validation(self):
        from apps.reference.services import StatutoryService
        self.assertEqual(StatutoryService.validate("NZ", {"ird": "012-345-678"}), [])
        self.assertTrue(any(e.field == "ird"
                            for e in StatutoryService.validate("NZ", {"ird": "xx"})))

    def test_postcode_and_docs(self):
        from apps.reference.services import (AddressValidationService,
                                             recommended_documents)
        self.assertIsNone(AddressValidationService.validate_postal_code("NZ", "6011"))
        self.assertIsNotNone(AddressValidationService.validate_postal_code("NZ", "60"))
        docs = " ".join(recommended_documents("NZ")).lower()
        self.assertIn("nzbn", docs)
        self.assertNotIn("cipc", docs)


class EmailTests(TestCase):
    def test_normalise(self):
        self.assertEqual(EmailValidationService.normalize("  John@Example.COM "), "john@example.com")

    def test_valid_invalid(self):
        self.assertTrue(EmailValidationService.validate("john@example.com").valid)
        for bad in ("john@", "john example.com", "@example.com"):
            self.assertFalse(EmailValidationService.validate(bad).valid)
