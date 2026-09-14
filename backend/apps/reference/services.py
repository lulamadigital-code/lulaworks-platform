"""Reusable country / currency / bank / phone / address / banking services.

One authoritative implementation of every rule, so web, API, Flutter (via the
reference API), document generation and LulaAI all validate the same way. Every
validator returns a `ValidationResult` with a stable machine `code` plus a
human `message`. Backend validation is always authoritative; the UI mirrors it
only for a nicer experience.

A note on honesty (spec §8/§16): these check FORMAT, never existence. A
syntactically valid IBAN or postal code is "format valid", not "verified" — real
verification needs an external banking / address provider and is never faked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (AddressFieldRule, Bank, BankingFieldRule, Country,
                     CountryCurrency, Currency)


@dataclass
class ValidationResult:
    valid: bool
    field: str = ""
    code: str = ""
    message: str = ""

    @classmethod
    def ok(cls, field=""):
        return cls(True, field=field)

    @classmethod
    def error(cls, field, code, message):
        return cls(False, field=field, code=code, message=message)


# ── Country ──────────────────────────────────────────────────────────────────
_ZA_ALIASES = {"south africa", "za", "rsa", "republic of south africa", ""}


class CountryService:
    @staticmethod
    def resolve(value: str) -> str | None:
        """Normalise a country given as an alpha-2 code OR a name to an alpha-2
        code. Returns None if it can't be resolved."""
        v = (value or "").strip()
        if not v:
            return None
        up = v.upper()
        if len(up) == 2 and Country.objects.filter(code=up).exists():
            return up
        hit = Country.objects.filter(name__iexact=v).first()
        if hit:
            return hit.code
        if v.lower() in _ZA_ALIASES:
            return "ZA"
        return None

    @staticmethod
    def get(code: str) -> Country | None:
        return Country.objects.filter(code=(code or "").upper()).first()

    @staticmethod
    def list_for_picker():
        """Active countries, SA/Africa first (via sort_priority) then by name."""
        return list(Country.objects.filter(active=True))

    @staticmethod
    def is_south_africa(code: str) -> bool:
        return (code or "").upper() == "ZA"


# ── Currency ─────────────────────────────────────────────────────────────────
class CurrencyService:
    @staticmethod
    def for_country(code: str) -> Currency | None:
        code = (code or "").upper()
        cc = (CountryCurrency.objects
              .filter(country_id=code, is_default=True)
              .select_related("currency").first())
        if cc:
            return cc.currency
        c = Country.objects.filter(code=code).select_related("default_currency").first()
        return c.default_currency if c else None

    @staticmethod
    def label(currency: Currency | None) -> str:
        if not currency:
            return ""
        return f"{currency.name} ({currency.code})"


# ── Supporting documents (country-aware recommendations) ─────────────────────
# Which documents a company is typically expected to hold, by country. Extend by
# adding a country key — the UI reads this, never a hardcoded per-country string.
_COUNTRY_DOCUMENTS = {
    "ZA": ["Company registration (CIPC) certificate", "Tax clearance / compliance",
           "VAT certificate (if registered)", "B-BBEE affidavit or certificate (if applicable)",
           "Bank confirmation letter"],
    "US": ["State registration / incorporation documents", "EIN confirmation (IRS)",
           "Sales-tax permit (if applicable)", "Certificate of good standing",
           "Bank verification letter"],
    "GB": ["Companies House registration", "VAT registration certificate (if registered)",
           "Business licence (if applicable)", "Insurance certificate", "Bank confirmation"],
    "AU": ["ASIC / ABN registration", "GST registration (if applicable)",
           "Workers' compensation certificate", "Bank confirmation"],
    "CA": ["Incorporation / registration documents", "Business number (CRA)",
           "GST/HST registration (if applicable)", "Bank confirmation"],
}
_DEFAULT_DOCUMENTS = ["Company registration document", "Tax registration document",
                      "Bank confirmation"]


class DocumentRulesService:
    @staticmethod
    def recommended(country_code: str) -> list:
        """Recommended supporting documents for a country (DB-driven; falls back
        to the curated dict when rules aren't seeded, e.g. in isolated tests)."""
        from .models import CountryDocumentRule
        code = (country_code or "").upper() or "ZA"
        rows = list(CountryDocumentRule.objects.filter(country_id=code))
        if rows:
            return [{"key": r.key, "name": r.name, "description": r.description,
                     "required": r.required, "has_expiry": r.has_expiry} for r in rows]
        names = _COUNTRY_DOCUMENTS.get(code, _DEFAULT_DOCUMENTS)
        return [{"key": "", "name": n, "description": "", "required": False,
                 "has_expiry": False} for n in names]


def recommended_documents(country_code: str) -> list:
    """Back-compat: just the document names for the country."""
    return [d["name"] for d in DocumentRulesService.recommended(country_code)]


# Generic statutory fallback for countries without seeded rules.
_GENERIC_STATUTORY = [
    {"key": "reg_no", "label": "Business / company registration no."},
    {"key": "tax_id", "label": "Tax ID / TIN"},
    {"key": "vat_no", "label": "VAT / GST number"},
    {"key": "employer_no", "label": "Employer registration no."},
]


class StatutoryService:
    @staticmethod
    def rules(country_code: str) -> list:
        """Ordered statutory rules for a country as plain dicts. Falls back to a
        generic set when the country has no seeded rules."""
        from .models import StatutoryRegistrationRule
        code = (country_code or "").upper() or "ZA"
        rows = list(StatutoryRegistrationRule.objects.filter(country_id=code))
        if not rows:
            return [{"key": g["key"], "label": g["label"], "help_text": "",
                     "authority": "", "required": False, "field_type": "text",
                     "column": "", "validator": "", "placeholder": ""}
                    for g in _GENERIC_STATUTORY]
        return [{"key": r.key, "label": r.label, "help_text": r.help_text,
                 "authority": r.authority, "required": r.required,
                 "field_type": r.field_type, "column": r.column,
                 "validator": r.validator, "placeholder": r.placeholder} for r in rows]

    @staticmethod
    def validate(country_code: str, data: dict) -> list:
        errs = []
        for r in StatutoryService.rules(country_code):
            val = (data.get(r["key"]) or "").strip()
            if r["required"] and not val:
                errs.append(ValidationResult.error(r["key"], "REQUIRED",
                                                   f"{r['label']} is required."))
            elif val and r["validator"]:
                if not re.fullmatch(r["validator"], val, re.IGNORECASE):
                    errs.append(ValidationResult.error(
                        r["key"], "INVALID_FORMAT", f"Enter a valid {r['label']}."))
        return errs


# ── Bank directory ───────────────────────────────────────────────────────────
class BankDirectoryService:
    @staticmethod
    def for_country(code: str, q: str | None = None):
        qs = Bank.objects.filter(country_id=(code or "").upper(), active=True)
        if q:
            ql = q.strip()
            qs = qs.filter(
                models_Q_name_or_alias(ql))
        return list(qs.order_by("-is_major", "sort_priority", "name"))

    @staticmethod
    def match(code: str, name: str) -> Bank | None:
        """Best-effort match a typed/legacy bank name to a directory row."""
        name = (name or "").strip()
        if not name:
            return None
        code = (code or "").upper()
        exact = Bank.objects.filter(country_id=code, name__iexact=name).first()
        if exact:
            return exact
        for b in Bank.objects.filter(country_id=code):
            if b.short_name and b.short_name.lower() == name.lower():
                return b
            if name.lower() in [a.lower() for a in (b.aliases or [])]:
                return b
        return None


def models_Q_name_or_alias(q: str):
    """A Q matching bank name / short_name (icontains). Aliases (JSON) are matched
    in Python by callers where DB JSON search isn't portable; for the picker the
    name/short_name match is enough."""
    from django.db.models import Q
    return Q(name__icontains=q) | Q(short_name__icontains=q) | Q(bank_code__icontains=q)


# ── Phone (E.164 via libphonenumber) ─────────────────────────────────────────
@dataclass
class PhoneResult:
    valid: bool
    e164: str = ""
    national: str = ""
    country_code: str = ""
    calling_code: str = ""
    number_type: str = "unknown"      # mobile | fixed_line | unknown
    message: str = ""


class PhoneValidationService:
    @staticmethod
    def validate(raw: str, country_code: str | None = None) -> PhoneResult:
        raw = (raw or "").strip()
        if not raw:
            return PhoneResult(False, message="Phone number is required.")
        import phonenumbers
        from phonenumbers import PhoneNumberType, NumberParseException
        region = (country_code or "").upper() or None
        try:
            num = phonenumbers.parse(raw, region)
        except NumberParseException:
            return PhoneResult(False, message="That phone number could not be understood.")
        if not phonenumbers.is_valid_number(num):
            return PhoneResult(False, message="Enter a valid phone number for the selected country.")
        t = phonenumbers.number_type(num)
        kind = ("mobile" if t in (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE)
                else "fixed_line" if t == PhoneNumberType.FIXED_LINE else "unknown")
        e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
        national = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)
        return PhoneResult(
            True, e164=e164, national=national,
            country_code=phonenumbers.region_code_for_number(num) or (region or ""),
            calling_code=f"+{num.country_code}", number_type=kind)


# ── Email (reuse core/validation) ────────────────────────────────────────────
class EmailValidationService:
    @staticmethod
    def validate(raw: str, *, required=True, field="email") -> ValidationResult:
        from apps.core.validation import clean_email, InputError
        try:
            clean_email(raw, required=required)
        except InputError as exc:
            return ValidationResult.error(field, "INVALID_EMAIL", str(exc))
        return ValidationResult.ok(field)

    @staticmethod
    def normalize(raw: str) -> str:
        return (raw or "").strip().lower()


# ── Address ──────────────────────────────────────────────────────────────────
class AddressValidationService:
    @staticmethod
    def rule(country_code: str) -> AddressFieldRule | None:
        return AddressFieldRule.objects.filter(country_id=(country_code or "").upper()).first()

    @staticmethod
    def verify(country_code: str, data: dict) -> dict:
        """Real address VERIFICATION (does this address physically exist?) needs an
        external geocoding/postal provider and is not wired yet. This honest stub
        always reports unverified so no caller ever implies an address was
        confirmed — only its FORMAT is checked by `validate()`."""
        return {"verified": False, "provider": None,
                "note": "Format checked only — not verified against a postal database."}

    @classmethod
    def validate_postal_code(cls, country_code: str, postal: str) -> ValidationResult | None:
        """Country-aware postal-code format check — the SAME rule used for the
        physical address, so postal and physical validate identically."""
        postal = (postal or "").strip()
        rule = cls.rule(country_code)
        if rule and rule.postal_regex and postal:
            if not re.fullmatch(rule.postal_regex, postal, re.IGNORECASE):
                return ValidationResult.error(
                    "postal_code", "INVALID_POSTAL",
                    rule.postal_hint or f"Enter a valid {rule.postal_label.lower()}.")
        return None

    @classmethod
    def validate(cls, country_code: str, data: dict) -> list[ValidationResult]:
        errs: list[ValidationResult] = []
        rule = cls.rule(country_code)
        fields = (rule.fields if rule else
                  [{"key": "street_address", "label": "Street address", "required": True},
                   {"key": "city", "label": "City", "required": True}])
        for f in fields:
            if f.get("required") and not (data.get(f["key"]) or "").strip():
                errs.append(ValidationResult.error(
                    f["key"], "REQUIRED", f"{f['label']} is required."))
        postal = (data.get("postal_code") or "").strip()
        if rule and rule.postal_regex and postal:
            if not re.fullmatch(rule.postal_regex, postal, re.IGNORECASE):
                errs.append(ValidationResult.error(
                    "postal_code", "INVALID_POSTAL",
                    rule.postal_hint or f"Enter a valid {rule.postal_label.lower()}."))
        return errs


# ── Banking ──────────────────────────────────────────────────────────────────
_IBAN_LEN = {  # country → expected IBAN length (subset; extend via seed later)
    "GB": 22, "DE": 22, "FR": 27, "NL": 18, "IE": 22, "ES": 24, "IT": 27,
    "PT": 25, "BE": 16, "AT": 20, "FI": 18,
}


class BankingValidationService:
    @staticmethod
    def rule(country_code: str) -> BankingFieldRule | None:
        return BankingFieldRule.objects.filter(country_id=(country_code or "").upper()).first()

    # -- individual format validators --
    @staticmethod
    def swift_bic(value: str) -> ValidationResult:
        v = re.sub(r"\s+", "", (value or "")).upper()
        if not v:
            return ValidationResult.ok("swift_bic")
        if re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", v):
            return ValidationResult.ok("swift_bic")
        return ValidationResult.error("swift_bic", "INVALID_SWIFT",
                                      "Enter a valid 8 or 11-character SWIFT/BIC code.")

    @classmethod
    def iban(cls, value: str) -> ValidationResult:
        v = re.sub(r"\s+", "", (value or "")).upper()
        if not v:
            return ValidationResult.ok("iban")
        if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}", v):
            return ValidationResult.error("iban", "INVALID_IBAN", "Enter a valid IBAN.")
        cc = v[:2]
        if cc in _IBAN_LEN and len(v) != _IBAN_LEN[cc]:
            return ValidationResult.error("iban", "INVALID_IBAN_LENGTH",
                                          f"An {cc} IBAN must be {_IBAN_LEN[cc]} characters.")
        # ISO 7064 mod-97: move first 4 chars to the end, letters→digits, %97 == 1
        rearranged = v[4:] + v[:4]
        digits = "".join(str(int(ch, 36)) for ch in rearranged)
        if int(digits) % 97 != 1:
            return ValidationResult.error("iban", "INVALID_IBAN_CHECKSUM",
                                          "That IBAN's checksum is not valid.")
        return ValidationResult.ok("iban")

    @staticmethod
    def _numeric_len(value, field, code, msg, lo, hi) -> ValidationResult:
        v = re.sub(r"\s|-", "", (value or ""))
        if not v:
            return ValidationResult.ok(field)
        if not (v.isdigit() and lo <= len(v) <= hi):
            return ValidationResult.error(field, code, msg)
        return ValidationResult.ok(field)

    @classmethod
    def validate_field(cls, validator: str, value: str) -> ValidationResult:
        if validator == "swift_bic":
            return cls.swift_bic(value)
        if validator == "iban":
            return cls.iban(value)
        if validator == "routing_number":
            return cls._numeric_len(value, "routing_number", "INVALID_ROUTING",
                                    "US routing number must be 9 digits.", 9, 9)
        if validator == "sort_code":
            return cls._numeric_len(value, "sort_code", "INVALID_SORT",
                                    "UK sort code must be 6 digits.", 6, 6)
        if validator == "bsb":
            return cls._numeric_len(value, "bsb", "INVALID_BSB",
                                    "BSB must be 6 digits.", 6, 6)
        if validator == "branch_code":
            return cls._numeric_len(value, "branch_code", "INVALID_BRANCH",
                                    "Branch code must be 5–6 digits.", 4, 8)
        if validator == "account_number":
            return cls._numeric_len(value, "account_number", "INVALID_ACCOUNT",
                                    "Enter a valid account number.", 4, 20)
        if validator == "ifsc":
            v = (value or "").strip().upper()
            if v and not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v):
                return ValidationResult.error("ifsc", "INVALID_IFSC",
                                              "Enter a valid 11-character IFSC code.")
        return ValidationResult.ok(validator)

    @classmethod
    def validate(cls, country_code: str, data: dict) -> list[ValidationResult]:
        """Validate a banking payload against the country's rule. Only fields in
        the rule are checked; required-but-empty and bad-format both reported."""
        errs: list[ValidationResult] = []
        if not (data.get("account_name") or "").strip():
            errs.append(ValidationResult.error("account_name", "REQUIRED",
                                               "Account holder name is required."))
        rule = cls.rule(country_code)
        fields = rule.fields if rule else [
            {"key": "account_number", "label": "Account number", "required": True,
             "validator": "account_number"},
            {"key": "branch_code", "label": "Branch code", "required": False,
             "validator": "branch_code"},
        ]
        for f in fields:
            val = (data.get(f["key"]) or "").strip()
            if f.get("required") and not val:
                errs.append(ValidationResult.error(f["key"], "REQUIRED",
                                                   f"{f['label']} is required."))
                continue
            if val:
                res = cls.validate_field(f.get("validator", "none"), val)
                if not res.valid:
                    errs.append(res)
        return errs
