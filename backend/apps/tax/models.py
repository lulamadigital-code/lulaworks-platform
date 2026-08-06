"""Tax engine — jurisdictions & rules.

A reference table of standard tax treatment per country (VAT/GST/sales tax rate,
inclusive-vs-exclusive norm, and reverse-charge grouping). Companies get correct
defaults for their country, and the compute_tax service (services.py) turns a
company + customer into the right tax DECISION for an invoice — including
cross-border reverse charge. Kept deliberately configurable so tax is never a
single-country assumption.
"""

from django.db import models

from apps.core.models import PlatformBaseModel


class TaxJurisdiction(PlatformBaseModel):
    """Standard tax treatment for a country (platform reference data)."""

    name = models.CharField(max_length=64, unique=True)   # matches Company.country, e.g. "South Africa"
    code = models.CharField(max_length=2, blank=True)     # ISO2, e.g. "ZA"
    tax_name = models.CharField(max_length=24, default="VAT")   # VAT / GST / Sales Tax
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Regional norm for how prices are quoted (EU consumer = inclusive; US = exclusive).
    prices_include_tax = models.BooleanField(default=False)
    # Countries sharing a region (e.g. "EU") support cross-border B2B reverse charge.
    reverse_charge_region = models.CharField(max_length=16, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} · {self.tax_name} {self.rate}%"
