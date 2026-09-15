"""International reference data — the normalized foundation the whole platform
reads country / currency / bank / validation rules from.

These are PLATFORM tables (not tenant-scoped): the same ISO country and currency
list, and the same maintained bank directory, serve every company. Company rows
point at these by stable codes (ISO 3166-1 alpha-2, ISO 4217) rather than storing
free text — so currency, banking rules and validation all follow the one country
choice, and new countries/banks drop in as data, not code.
"""
from django.db import models


class Currency(models.Model):
    """An ISO 4217 currency. `code` is the canonical value stored elsewhere."""
    code = models.CharField(max_length=3, primary_key=True)        # ISO 4217, e.g. ZAR
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8, blank=True)            # R, $, €, £
    decimal_places = models.PositiveSmallIntegerField(default=2)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return f"{self.code} — {self.name}"


class Country(models.Model):
    """An ISO 3166-1 country. `code` (alpha-2) is the source of truth a Company
    stores; everything country-aware resolves from it."""
    code = models.CharField(max_length=2, primary_key=True)        # ISO 3166-1 alpha-2, e.g. ZA
    name = models.CharField(max_length=96)
    calling_code = models.CharField(max_length=8, blank=True)      # E.164 country code, e.g. +27
    default_currency = models.ForeignKey(
        Currency, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="default_for_countries")
    flag_emoji = models.CharField(max_length=8, blank=True)
    region = models.CharField(max_length=32, blank=True)           # Africa, Europe, …
    # Lower sorts first. SA and the rest of Africa are boosted so pickers lead
    # with the home market; the bulk default to a neutral mid value.
    sort_priority = models.IntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_priority", "name"]
        verbose_name_plural = "countries"

    def __str__(self):
        return f"{self.code} — {self.name}"


class CountryCurrency(models.Model):
    """Country → currency mapping. Modelled as its own table (not a single FK) so
    exceptional / historical cases are configurable — a country is not assumed to
    have exactly one currency forever."""
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="currencies")
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="countries")
    is_default = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["country", "-is_default"]
        constraints = [
            models.UniqueConstraint(fields=["country", "currency"],
                                    name="uniq_country_currency"),
        ]

    def __str__(self):
        return f"{self.country_id} → {self.currency_id}"


class Bank(models.Model):
    """A real bank in the maintained directory — a proper reference entity (tied
    to a country + SWIFT/BIC), the foundation for future payment integrations and
    invoice bank details, not just a name in a dropdown."""
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="banks")
    name = models.CharField(max_length=120)
    short_name = models.CharField(max_length=48, blank=True)
    bank_code = models.CharField(max_length=24, blank=True)        # universal branch code / sort / routing prefix
    swift_bic = models.CharField(max_length=11, blank=True)
    aliases = models.JSONField(default=list, blank=True)           # ["FNB", "First National Bank"]
    is_major = models.BooleanField(default=False)
    sort_priority = models.IntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country", "sort_priority", "name"]
        constraints = [
            models.UniqueConstraint(fields=["country", "name"], name="uniq_bank_country_name"),
        ]

    def __str__(self):
        return f"{self.name} ({self.country_id})"


class BankingFieldRule(models.Model):
    """Which banking fields a country's banking system uses and how they validate.

    Keyed by country (a shared system like SEPA is seeded per member country for
    simplicity in this phase). `fields` is an ordered list of dicts:
        {"key","label","required","validator","hint"}
    where `validator` names a check in services.BankingValidationService
    (account_number | branch_code | routing_number | sort_code | iban | bsb |
     ifsc | swift_bic | transit | institution | none).
    """
    country = models.OneToOneField(Country, on_delete=models.CASCADE,
                                   related_name="banking_rule")
    branch_label = models.CharField(max_length=48, default="Branch code")
    branch_hint = models.CharField(max_length=160, blank=True)
    show_swift = models.BooleanField(default=False)                # local transfers don't need SWIFT
    fields = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"Banking rule · {self.country_id}"


class AddressFieldRule(models.Model):
    """Country-aware address structure + postal-code validation. `fields` is an
    ordered list of {"key","label","required"}; `postal_regex` validates the
    postal/ZIP/postcode where practical (empty = no format rule)."""
    country = models.OneToOneField(Country, on_delete=models.CASCADE,
                                   related_name="address_rule")
    fields = models.JSONField(default=list, blank=True)
    postal_label = models.CharField(max_length=48, default="Postal code")
    postal_regex = models.CharField(max_length=200, blank=True)
    postal_hint = models.CharField(max_length=160, blank=True)

    def __str__(self):
        return f"Address rule · {self.country_id}"


class StatutoryRegistrationRule(models.Model):
    """One statutory / regulatory registration a company in a country may hold
    (SA CIPC/CIDB/B-BBEE, US EIN, UK Companies House number, …). Data-driven so a
    new jurisdiction is added by seeding rows, not editing a form.

    `column` names a real `CompanyCompliance` field to store into (SA keeps its
    typed columns, so PDF/expiry/completeness keep working); blank = store the
    value in `CompanyCompliance.other_registrations` JSON under `key`.
    `field_type` = text|date. `validator` = optional regex (format-only, never
    claims official verification)."""

    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        DATE = "date", "Date"

    country = models.ForeignKey(Country, on_delete=models.CASCADE,
                                related_name="statutory_rules")
    key = models.CharField(max_length=48)
    label = models.CharField(max_length=96)
    help_text = models.CharField(max_length=200, blank=True)
    authority = models.CharField(max_length=96, blank=True)     # CIPC, IRS, Companies House…
    required = models.BooleanField(default=False)               # B-BBEE etc. never mandatory
    field_type = models.CharField(max_length=8, choices=FieldType.choices,
                                  default=FieldType.TEXT)
    column = models.CharField(max_length=48, blank=True)        # CompanyCompliance column, or ""→JSON
    validator = models.CharField(max_length=200, blank=True)    # optional regex
    placeholder = models.CharField(max_length=64, blank=True)
    sort_priority = models.IntegerField(default=100)

    class Meta:
        ordering = ["country", "sort_priority", "key"]
        constraints = [
            models.UniqueConstraint(fields=["country", "key"], name="uniq_statutory_country_key"),
        ]

    def __str__(self):
        return f"{self.country_id} · {self.key}"


class Language(models.Model):
    """A language Lulaworks can localize into — reference data, so new languages
    are activated by adding a row, never by code. `code` is the ISO 639-1 / BCP-47
    primary subtag. Country and language are SEPARATE concepts."""

    class Direction(models.TextChoices):
        LTR = "ltr", "Left-to-right"
        RTL = "rtl", "Right-to-left"

    code = models.CharField(max_length=8, primary_key=True)        # en, fr, ar, sw, zu
    name = models.CharField(max_length=64)                         # English name
    native_name = models.CharField(max_length=64)                 # endonym, e.g. isiZulu
    direction = models.CharField(max_length=3, choices=Direction.choices,
                                 default=Direction.LTR)
    active = models.BooleanField(default=True)
    sort_priority = models.IntegerField(default=100)

    class Meta:
        ordering = ["sort_priority", "name"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Locale(models.Model):
    """A language + region, driving regional FORMATTING (dates, numbers). Distinct
    from Language: en-ZA, en-US and en-GB share a language but format differently."""
    code = models.CharField(max_length=12, primary_key=True)       # en-ZA, fr-CA, pt-BR
    language = models.ForeignKey(Language, on_delete=models.CASCADE, related_name="locales")
    region = models.CharField(max_length=2, blank=True)            # country alpha-2
    date_format = models.CharField(max_length=32, default="d/m/Y")  # Django date format
    number_decimal = models.CharField(max_length=1, default=".")
    number_group = models.CharField(max_length=1, default=",")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class CountryLanguage(models.Model):
    """Which languages are used in a country, and which is the default. A country
    has MANY languages; a language spans MANY countries. This is the only place
    country and language meet — never in forms or business logic."""
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="languages")
    language = models.ForeignKey(Language, on_delete=models.CASCADE, related_name="countries")
    is_default = models.BooleanField(default=False)
    sort_priority = models.IntegerField(default=100)

    class Meta:
        ordering = ["country", "-is_default", "sort_priority"]
        constraints = [
            models.UniqueConstraint(fields=["country", "language"],
                                    name="uniq_country_language"),
        ]

    def __str__(self):
        return f"{self.country_id} · {self.language_id}{' (default)' if self.is_default else ''}"


class CountryDocumentRule(models.Model):
    """A supporting document a company in a country is typically expected to hold.
    Drives the 'recommended documents' list per country — no hardcoded per-country
    strings in templates."""
    country = models.ForeignKey(Country, on_delete=models.CASCADE,
                                related_name="document_rules")
    key = models.CharField(max_length=48)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=200, blank=True)
    authority = models.CharField(max_length=96, blank=True)
    required = models.BooleanField(default=False)
    has_expiry = models.BooleanField(default=False)
    sort_priority = models.IntegerField(default=100)

    class Meta:
        ordering = ["country", "sort_priority", "key"]
        constraints = [
            models.UniqueConstraint(fields=["country", "key"], name="uniq_document_country_key"),
        ]

    def __str__(self):
        return f"{self.country_id} · {self.key}"
