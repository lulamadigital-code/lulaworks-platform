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
