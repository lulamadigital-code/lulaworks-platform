"""Tax decision engine.

compute_tax(company, customer) decides the tax treatment for an invoice:
  • the rate + label (from the company's config, seeded from its jurisdiction),
  • whether prices are tax-inclusive,
  • and cross-border **reverse charge** (customer in another country and
    tax-registered, when the company enables it) → 0% with a note.

It returns only the DECISION (rate/label/flags). The Invoice model does the
money arithmetic, so there's one source of truth for amounts. Deliberately
conservative: cross-border sales are NOT silently zero-rated unless reverse
charge applies — the operator stays in control.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class TaxDecision:
    rate: Decimal            # percent, e.g. 15
    tax_name: str            # "VAT" / "GST" / "Sales Tax" / "Tax"
    inclusive: bool          # are line prices tax-inclusive?
    reverse_charge: bool     # cross-border B2B — recipient self-accounts
    note: str = ""           # human-readable note for the invoice (e.g. reverse-charge wording)


def jurisdiction_for(country: str):
    """The seeded TaxJurisdiction for a country name (case-insensitive), or None."""
    from .models import TaxJurisdiction
    if not country:
        return None
    return TaxJurisdiction.objects.filter(name__iexact=country.strip()).first()


def apply_company_jurisdiction_defaults(company) -> bool:
    """Populate a company's tax config from its country's standard treatment.
    Returns True if a jurisdiction was found and applied."""
    jur = jurisdiction_for(getattr(company, "country", ""))
    if jur is None:
        return False
    company.default_tax_rate = jur.rate
    company.tax_name = jur.tax_name
    company.prices_include_tax = jur.prices_include_tax
    company.save(update_fields=["default_tax_rate", "tax_name",
                                "prices_include_tax", "updated_at"])
    return True


def _same_region_reverse_charge(company_country, customer_country) -> bool:
    """True when both countries are in the same reverse-charge region (e.g. EU)."""
    a = jurisdiction_for(company_country)
    b = jurisdiction_for(customer_country)
    return bool(a and b and a.reverse_charge_region
                and a.reverse_charge_region == b.reverse_charge_region)


def compute_tax(company, customer=None) -> TaxDecision:
    """Decide the tax treatment for an invoice from `company` to `customer`."""
    rate = Decimal(company.default_tax_rate or 0)
    tax_name = (getattr(company, "tax_name", "") or "").strip()
    if not tax_name:
        jur = jurisdiction_for(getattr(company, "country", ""))
        tax_name = jur.tax_name if jur else "Tax"
    inclusive = bool(getattr(company, "prices_include_tax", False))

    if customer is not None:
        comp_country = (getattr(company, "country", "") or "").strip()
        cust_country = (getattr(customer, "country", "") or "").strip()
        cust_registered = bool((getattr(customer, "vat_no", "")
                                or getattr(customer, "tax_no", "")).strip())
        cross_border = comp_country and cust_country and \
            comp_country.lower() != cust_country.lower()
        if cross_border and cust_registered and getattr(company, "reverse_charge_enabled", False):
            # Recipient accounts for the tax (EU-style cross-border B2B).
            return TaxDecision(
                rate=Decimal("0"), tax_name=tax_name, inclusive=inclusive,
                reverse_charge=True,
                note=f"Reverse charge: {tax_name} to be accounted for by the recipient.",
            )

    return TaxDecision(rate=rate, tax_name=tax_name, inclusive=inclusive,
                       reverse_charge=False)
