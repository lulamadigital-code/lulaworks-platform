"""Populate the international reference tables. Idempotent — safe to re-run on
every deploy. Countries + currency names come from pycountry (ISO 3166 / 4217);
calling codes from libphonenumber; the country→currency map, bank directory and
banking/address rules are curated here (SA first-class, top international next).
"""
from django.core.management.base import BaseCommand

# ── Currencies (symbol + decimals; names filled from pycountry) ───────────────
CURRENCY_SYMBOLS = {
    "ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€", "AUD": "A$", "CAD": "C$",
    "INR": "₹", "NGN": "₦", "KES": "KSh", "AED": "د.إ", "SGD": "S$", "BRL": "R$",
    "JPY": "¥", "NZD": "NZ$", "GHS": "₵", "BWP": "P", "NAD": "N$", "ZMW": "ZK",
}
ZERO_DECIMAL = {"JPY"}

# ── Country → default currency (curated majors + eurozone) ────────────────────
_EUROZONE = ["AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT",
             "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES"]
COUNTRY_CCY = {
    "ZA": "ZAR", "US": "USD", "GB": "GBP", "AU": "AUD", "CA": "CAD", "IN": "INR",
    "NG": "NGN", "KE": "KES", "AE": "AED", "SG": "SGD", "BR": "BRL", "JP": "JPY",
    "NZ": "NZD", "GH": "GHS", "BW": "BWP", "NA": "NAD", "ZM": "ZMW",
    **{c: "EUR" for c in _EUROZONE},
}

_AFRICA = {"ZA", "NG", "KE", "GH", "BW", "NA", "ZM", "ZW", "MZ", "AO", "TZ",
           "UG", "RW", "ET", "EG", "MA", "SN", "CI", "CM", "MW", "LS", "SZ"}
_EUROPE = set(_EUROZONE) | {"GB", "CH", "NO", "SE", "DK", "PL", "CZ", "RO"}
_MAJOR = {"US", "GB", "AU", "CA", "DE", "FR", "NL", "IE", "IN", "AE", "SG",
          "BR", "JP", "NZ", "NG", "KE", "GH"}

# ── Bank directory (SA full + top international) ───────────────────────────────
# (name, short_name, bank_code, swift_bic, [aliases], is_major)
BANKS = {
    "ZA": [
        ("Absa Bank", "Absa", "632005", "ABSAZAJJ", ["ABSA"], True),
        ("Capitec Bank", "Capitec", "470010", "CABLZAJJ", [], True),
        ("First National Bank (FNB)", "FNB", "250655", "FIRNZAJJ", ["FNB", "First National Bank"], True),
        ("Nedbank", "Nedbank", "198765", "NEDSZAJJ", [], True),
        ("Standard Bank", "Standard Bank", "051001", "SBZAZAJJ", ["SBSA"], True),
        ("Investec Bank", "Investec", "580105", "IVESZAJJ", [], True),
        ("African Bank", "African Bank", "430000", "AFRCZAJJ", [], False),
        ("Discovery Bank", "Discovery", "679000", "DISCZAJJ", [], False),
        ("TymeBank", "TymeBank", "678910", "CBZAZAJJ", ["Tyme"], False),
        ("Bidvest Bank", "Bidvest", "462005", "BIDBZAJJ", [], False),
        ("Bank Zero", "Bank Zero", "888000", "", [], False),
        ("Sasfin Bank", "Sasfin", "683000", "SASFZAJJ", [], False),
        ("Ubank", "Ubank", "431010", "TEBAZAJJ", [], False),
        ("Standard Chartered (SA)", "StanChart", "730020", "SCBLZAJJ", [], False),
    ],
    "US": [
        ("JPMorgan Chase", "Chase", "", "CHASUS33", ["Chase"], True),
        ("Bank of America", "BofA", "", "BOFAUS3N", ["BofA"], True),
        ("Wells Fargo", "Wells Fargo", "", "WFBIUS6S", [], True),
        ("Citibank", "Citi", "", "CITIUS33", ["Citi"], True),
        ("U.S. Bank", "US Bank", "", "USBKUS44", [], False),
        ("PNC Bank", "PNC", "", "PNCCUS33", [], False),
    ],
    "GB": [
        ("Barclays", "Barclays", "", "BARCGB22", [], True),
        ("HSBC UK", "HSBC", "", "HBUKGB4B", ["HSBC"], True),
        ("Lloyds Bank", "Lloyds", "", "LOYDGB2L", [], True),
        ("NatWest", "NatWest", "", "NWBKGB2L", [], True),
        ("Santander UK", "Santander", "", "ABBYGB2L", [], False),
        ("Monzo", "Monzo", "", "MONZGB2L", [], False),
    ],
    "AU": [
        ("Commonwealth Bank", "CommBank", "", "CTBAAU2S", ["CBA", "CommBank"], True),
        ("Westpac", "Westpac", "", "WPACAU2S", [], True),
        ("ANZ", "ANZ", "", "ANZBAU3M", [], True),
        ("NAB", "NAB", "", "NATAAU33", ["National Australia Bank"], True),
    ],
    "CA": [
        ("Royal Bank of Canada (RBC)", "RBC", "", "ROYCCAT2", ["RBC"], True),
        ("TD Bank", "TD", "", "TDOMCATT", ["Toronto-Dominion"], True),
        ("Scotiabank", "Scotiabank", "", "NOSCCATT", [], True),
        ("BMO", "BMO", "", "BOFMCAM2", ["Bank of Montreal"], False),
    ],
    "IN": [
        ("State Bank of India (SBI)", "SBI", "", "SBININBB", ["SBI"], True),
        ("HDFC Bank", "HDFC", "", "HDFCINBB", [], True),
        ("ICICI Bank", "ICICI", "", "ICICINBB", [], True),
        ("Axis Bank", "Axis", "", "AXISINBB", [], False),
    ],
    "NG": [
        ("Access Bank", "Access", "", "ABNGNGLA", [], True),
        ("Guaranty Trust Bank (GTBank)", "GTBank", "", "GTBINGLA", ["GTB"], True),
        ("Zenith Bank", "Zenith", "", "ZEIBNGLA", [], True),
        ("First Bank of Nigeria", "First Bank", "", "FBNINGLA", [], False),
    ],
    "KE": [
        ("Equity Bank", "Equity", "", "EQBLKENA", [], True),
        ("KCB Bank", "KCB", "", "KCBLKENX", [], True),
        ("Co-operative Bank", "Co-op", "", "KCOOKENA", [], False),
        ("Stanbic Bank Kenya", "Stanbic", "", "SBICKENX", [], False),
    ],
    "DE": [
        ("Deutsche Bank", "Deutsche", "", "DEUTDEFF", [], True),
        ("Commerzbank", "Commerzbank", "", "COBADEFF", [], True),
        ("Sparkasse", "Sparkasse", "", "", [], False),
    ],
    "FR": [
        ("BNP Paribas", "BNP Paribas", "", "BNPAFRPP", [], True),
        ("Crédit Agricole", "Crédit Agricole", "", "AGRIFRPP", [], True),
        ("Société Générale", "SocGen", "", "SOGEFRPP", [], True),
        ("Groupe BPCE (Banque Populaire)", "BPCE", "", "CCBPFRPP", [], True),
        ("Crédit Mutuel", "Crédit Mutuel", "", "CMCIFRPP", [], False),
        ("La Banque Postale", "Banque Postale", "", "PSSTFRPP", [], False),
    ],
    "AE": [
        ("Emirates NBD", "ENBD", "", "EBILAEAD", [], True),
        ("First Abu Dhabi Bank (FAB)", "FAB", "", "NBADAEAA", [], True),
        ("Abu Dhabi Commercial Bank", "ADCB", "", "ADCBAEAA", [], False),
    ],
    "NZ": [
        ("ANZ Bank New Zealand", "ANZ", "", "ANZBNZ22", [], True),
        ("ASB Bank", "ASB", "", "ASBBNZ2A", [], True),
        ("Bank of New Zealand (BNZ)", "BNZ", "", "BKNZNZ22", ["BNZ"], True),
        ("Westpac New Zealand", "Westpac", "", "WPACNZ2W", [], True),
        ("Kiwibank", "Kiwibank", "", "KIWINZ22", [], False),
    ],
}

# ── Banking field rules (which fields + how they validate) ────────────────────
_F = lambda key, label, required, validator, hint="": {
    "key": key, "label": label, "required": required, "validator": validator, "hint": hint}

BANKING_RULES = {
    "ZA": dict(branch_label="Branch code", show_swift=False,
               branch_hint="SA banks use a universal branch code (auto-filled when you pick your bank).",
               fields=[_F("account_number", "Account number", True, "account_number"),
                       _F("branch_code", "Branch code", True, "branch_code")]),
    "US": dict(branch_label="Routing number (ABA)", show_swift=True,
               branch_hint="9-digit ABA routing number.",
               fields=[_F("account_number", "Account number", True, "account_number"),
                       _F("routing_number", "Routing number (ABA)", True, "routing_number"),
                       _F("swift_code", "SWIFT/BIC", False, "swift_bic", "For international payments.")]),
    "GB": dict(branch_label="Sort code", show_swift=True,
               branch_hint="6-digit sort code.",
               fields=[_F("account_number", "Account number", True, "account_number"),
                       _F("branch_code", "Sort code", True, "sort_code"),
                       _F("iban", "IBAN", False, "iban", "For international / SEPA payments."),
                       _F("swift_code", "SWIFT/BIC", False, "swift_bic")]),
    "AU": dict(branch_label="BSB", show_swift=True,
               branch_hint="6-digit BSB number.",
               fields=[_F("account_number", "Account number", True, "account_number"),
                       _F("branch_code", "BSB", True, "bsb"),
                       _F("swift_code", "SWIFT/BIC", False, "swift_bic")]),
    "CA": dict(branch_label="Transit + institution no.", show_swift=True,
               branch_hint="5-digit transit and 3-digit institution number.",
               fields=[_F("account_number", "Account number", True, "account_number"),
                       _F("branch_code", "Transit number", True, "branch_code"),
                       _F("swift_code", "SWIFT/BIC", False, "swift_bic")]),
    "IN": dict(branch_label="IFSC code", show_swift=True,
               branch_hint="11-character IFSC of the branch.",
               fields=[_F("account_number", "Account number", True, "account_number"),
                       _F("branch_code", "IFSC code", True, "ifsc"),
                       _F("swift_code", "SWIFT/BIC", False, "swift_bic")]),
    "KE": dict(branch_label="Bank branch code", show_swift=True,
               branch_hint="Your bank's branch code.",
               fields=[_F("account_number", "Account number", True, "account_number"),
                       _F("branch_code", "Bank branch code", True, "branch_code"),
                       _F("swift_code", "SWIFT/BIC", False, "swift_bic",
                          "For international payments.")]),
    # NZ account numbers embed the bank + branch (BB-bbbb-AAAAAAA-SS), so there's
    # no separate branch field — the full number is captured in one field.
    "NZ": dict(branch_label="Account number", show_swift=True,
               branch_hint="The full NZ account number includes the bank and branch.",
               fields=[_F("account_number", "Account number", True, "account_number",
                          "e.g. 12-3456-7890123-00"),
                       _F("swift_code", "SWIFT/BIC", False, "swift_bic",
                          "For international payments.")]),
}
# SEPA members share an IBAN-based rule.
_SEPA_RULE = dict(branch_label="IBAN", show_swift=True,
                  branch_hint="IBAN routes SEPA transfers.",
                  fields=[_F("iban", "IBAN", True, "iban"),
                          _F("swift_code", "BIC/SWIFT", False, "swift_bic")])
for _c in _EUROZONE:
    BANKING_RULES.setdefault(_c, _SEPA_RULE)

# ── Address field rules + postal validation ──────────────────────────────────
_ADDR = lambda *fs: [{"key": k, "label": l, "required": r} for (k, l, r) in fs]
ADDRESS_RULES = {
    "ZA": dict(fields=_ADDR(("street_address", "Street address", True), ("suburb", "Suburb", False),
                            ("city", "City", True), ("province", "Province", True)),
               postal_label="Postal code", postal_regex=r"\d{4}", postal_hint="Enter a valid 4-digit SA postal code."),
    "US": dict(fields=_ADDR(("street_address", "Street address", True), ("city", "City", True),
                            ("province", "State", True)),
               postal_label="ZIP code", postal_regex=r"\d{5}(-\d{4})?", postal_hint="Enter a valid ZIP or ZIP+4 code."),
    "GB": dict(fields=_ADDR(("street_address", "Address line 1", True), ("city", "Town/City", True),
                            ("province", "County", False)),
               postal_label="Postcode", postal_regex=r"[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}",
               postal_hint="Enter a valid UK postcode."),
    "AU": dict(fields=_ADDR(("street_address", "Street address", True), ("city", "Suburb/City", True),
                            ("province", "State", True)),
               postal_label="Postcode", postal_regex=r"\d{4}", postal_hint="Enter a valid 4-digit postcode."),
    "CA": dict(fields=_ADDR(("street_address", "Street address", True), ("city", "City", True),
                            ("province", "Province", True)),
               postal_label="Postal code", postal_regex=r"[A-Za-z]\d[A-Za-z]\s*\d[A-Za-z]\d",
               postal_hint="Enter a valid Canadian postal code (A1A 1A1)."),
    "KE": dict(fields=_ADDR(("street_address", "Street / building", True), ("suburb", "Estate / area", False),
                            ("city", "Town / City", True), ("province", "County", True)),
               postal_label="Postal code", postal_regex=r"\d{5}",
               postal_hint="Enter a valid 5-digit Kenyan postal code."),
    "NZ": dict(fields=_ADDR(("street_address", "Street address", True), ("suburb", "Suburb", False),
                            ("city", "Town / City", True), ("province", "Region", False)),
               postal_label="Postcode", postal_regex=r"\d{4}",
               postal_hint="Enter a valid 4-digit NZ postcode."),
    "FR": dict(fields=_ADDR(("street_address", "Adresse (line 1)", True), ("suburb", "Adresse (line 2)", False),
                            ("city", "Ville / Commune", True)),
               postal_label="Code postal", postal_regex=r"\d{5}",
               postal_hint="Enter a valid 5-digit French postal code."),
}


# ── Statutory registration rules (SA maps to typed columns; others → JSON) ────
# (key, label, authority, required, field_type, column, validator, placeholder, help)
_S = lambda key, label, authority="", required=False, field_type="text", column="", \
    validator="", placeholder="", help="": dict(
        key=key, label=label, authority=authority, required=required,
        field_type=field_type, column=column, validator=validator,
        placeholder=placeholder, help_text=help)

STATUTORY_RULES = {
    "ZA": [
        _S("income_tax_no", "Income tax number", "SARS", column="income_tax_no"),
        _S("paye_no", "PAYE number", "SARS", column="paye_no"),
        _S("uif_no", "UIF number", "Dept. of Labour", column="uif_no"),
        _S("coida_no", "COIDA number", "Compensation Fund", column="coida_no",
           help="Workmen's compensation registration."),
        _S("coida_expiry", "COIDA letter expiry", field_type="date", column="coida_expiry"),
        _S("csd_supplier_no", "CSD supplier number", "National Treasury", column="csd_supplier_no"),
        _S("cidb_grading", "CIDB grading", "CIDB", column="cidb_grading", placeholder="3CE"),
        _S("bbbee_level", "B-BBEE level", column="bbbee_level", placeholder="Level 1",
           help="Optional — not every company has a rating."),
        _S("bbbee_expiry", "B-BBEE expiry", field_type="date", column="bbbee_expiry"),
    ],
    "US": [
        _S("ein", "EIN (Employer ID Number)", "IRS", validator=r"\d{2}-?\d{7}",
           placeholder="12-3456789"),
        _S("state_inc", "State of incorporation", placeholder="Delaware"),
        _S("state_reg", "State registration no."),
        _S("sales_tax", "Sales-tax permit no."),
    ],
    "GB": [
        _S("company_no", "Company number (Companies House)", "Companies House",
           validator=r"[A-Za-z0-9]{8}", placeholder="12345678"),
        _S("utr", "UTR (tax reference)", "HMRC", validator=r"\d{10}"),
        _S("paye_ref", "PAYE reference", "HMRC", placeholder="123/AB456"),
    ],
    "AU": [
        _S("abn", "ABN (Australian Business Number)", "ATO", placeholder="12 345 678 901"),
        _S("acn", "ACN (Company Number)", "ASIC"),
        _S("tfn", "Tax File Number", "ATO"),
    ],
    "CA": [
        _S("bn", "Business Number", "CRA", placeholder="123456789"),
        _S("gst", "GST/HST number", "CRA"),
    ],
    "KE": [
        _S("reg_no", "Company registration number", "Registrar of Companies"),
        _S("kra_pin", "KRA PIN", "KRA", validator=r"[A-Za-z]\d{9}[A-Za-z]",
           placeholder="A012345678Z", help="Personal/company tax PIN."),
        _S("vat_no", "VAT registration number", "KRA"),
        _S("nssf_no", "NSSF number", "NSSF"),
        _S("nhif_no", "NHIF number", "NHIF"),
    ],
    "NZ": [
        _S("nzbn", "NZBN (NZ Business Number)", "MBIE", validator=r"\d{13}",
           placeholder="9429000000000"),
        _S("company_no", "Company number", "Companies Office"),
        _S("ird", "IRD number", "Inland Revenue", validator=r"\d{2,3}[- ]?\d{3}[- ]?\d{3}",
           placeholder="012-345-678"),
        _S("gst", "GST number", "Inland Revenue"),
    ],
    "FR": [
        _S("siren", "SIREN", "INSEE", validator=r"\d{9}", placeholder="552100554"),
        _S("siret", "SIRET (establishment)", "INSEE", validator=r"\d{14}",
           placeholder="55210055400024"),
        _S("tva", "TVA intracommunautaire (VAT)", "DGFiP", placeholder="FR40552100554"),
        _S("rcs", "RCS registration", "Greffe du tribunal de commerce"),
        _S("ape", "Code APE / NAF", "INSEE", placeholder="4120A"),
    ],
}


class Command(BaseCommand):
    help = "Seed / refresh international reference data (countries, currencies, banks, rules)."

    def handle(self, *args, **opts):
        import pycountry
        import phonenumbers
        from apps.reference.models import (AddressFieldRule, Bank, BankingFieldRule,
                                           Country, CountryCurrency,
                                           CountryDocumentRule, Currency,
                                           StatutoryRegistrationRule)
        from apps.reference.services import _COUNTRY_DOCUMENTS, _DEFAULT_DOCUMENTS

        # 1) Currencies — every code we reference, named from pycountry.
        ccy_codes = set(CURRENCY_SYMBOLS) | set(COUNTRY_CCY.values())
        cur_objs = {}
        for code in sorted(ccy_codes):
            py = pycountry.currencies.get(alpha_3=code)
            name = py.name if py else code
            obj, _ = Currency.objects.update_or_create(
                code=code, defaults=dict(
                    name=name, symbol=CURRENCY_SYMBOLS.get(code, ""),
                    decimal_places=0 if code in ZERO_DECIMAL else 2, active=True))
            cur_objs[code] = obj

        # 2) Countries — all ISO 3166 alpha-2, calling code from libphonenumber.
        n_countries = 0
        for py in pycountry.countries:
            code = py.alpha_2
            cc = phonenumbers.country_code_for_region(code)
            flag = "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)
            region = "Africa" if code in _AFRICA else "Europe" if code in _EUROPE else ""
            priority = 0 if code == "ZA" else 10 if code in _AFRICA else 50 if code in _MAJOR else 100
            ccy = cur_objs.get(COUNTRY_CCY.get(code))
            Country.objects.update_or_create(
                code=code, defaults=dict(
                    name=getattr(py, "common_name", py.name),
                    calling_code=f"+{cc}" if cc else "",
                    default_currency=ccy, flag_emoji=flag, region=region,
                    sort_priority=priority, active=True))
            if ccy:
                CountryCurrency.objects.update_or_create(
                    country_id=code, currency=ccy, defaults=dict(is_default=True))
            n_countries += 1

        # 3) Banks
        n_banks = 0
        for country_code, rows in BANKS.items():
            for i, (name, short, bank_code, swift, aliases, major) in enumerate(rows):
                Bank.objects.update_or_create(
                    country_id=country_code, name=name, defaults=dict(
                        short_name=short, bank_code=bank_code, swift_bic=swift,
                        aliases=aliases, is_major=major,
                        sort_priority=i if major else 100 + i, active=True))
                n_banks += 1

        # 4) Banking + address rules
        for code, rule in BANKING_RULES.items():
            if Country.objects.filter(code=code).exists():
                BankingFieldRule.objects.update_or_create(country_id=code, defaults=rule)
        for code, rule in ADDRESS_RULES.items():
            if Country.objects.filter(code=code).exists():
                AddressFieldRule.objects.update_or_create(country_id=code, defaults=rule)

        # 5) Statutory registration rules
        n_stat = 0
        for code, rules in STATUTORY_RULES.items():
            if not Country.objects.filter(code=code).exists():
                continue
            for i, r in enumerate(rules):
                StatutoryRegistrationRule.objects.update_or_create(
                    country_id=code, key=r["key"],
                    defaults={**{k: v for k, v in r.items() if k != "key"},
                              "sort_priority": i})
                n_stat += 1

        # 6) Country document rules (from the curated names; expiry heuristic)
        n_docs = 0
        _EXP = ("clearance", "insurance", "certificate", "b-bbee", "licence", "license", "permit")
        for code, names in _COUNTRY_DOCUMENTS.items():
            if not Country.objects.filter(code=code).exists():
                continue
            for i, name in enumerate(names):
                key = name.lower().split("(")[0].strip().replace(" ", "_").replace("/", "_")[:48]
                CountryDocumentRule.objects.update_or_create(
                    country_id=code, key=key,
                    defaults={"name": name, "sort_priority": i,
                              "has_expiry": any(w in name.lower() for w in _EXP)})
                n_docs += 1

        self.stdout.write(self.style.SUCCESS(
            f"Reference seeded: {len(cur_objs)} currencies, {n_countries} countries, "
            f"{n_banks} banks, {len(BANKING_RULES)} banking rules, {len(ADDRESS_RULES)} address rules, "
            f"{n_stat} statutory rules, {n_docs} document rules."))
