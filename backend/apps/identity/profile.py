"""Company profile — the identity of the business inside LulaWorks.

The rule this module exists to enforce: **company information is entered once**.
Quotations, invoices, purchase orders, RFQs, delivery notes, safety files and
every PDF header read from here. No other module stores a VAT number, a bank
account or a letterhead, so changing it here changes it everywhere — which is
the whole point, and also why `document_header()` returns a snapshot rather than
letting each caller reach in and pick fields at random.

`completeness()` exists because a half-filled profile fails quietly and late:
you discover the missing VAT number when a client rejects the invoice. It scores
each section so the gap is visible before it costs anything.
"""

from django.db import transaction

from .models import (
    Company,
    CompanyBankAccount,
    CompanyBranding,
    CompanyCompliance,
    CompanyContact,
)


def get_profile(company: Company) -> Company:
    """The company with its profile relations ready. Creates the one-to-one
    rows on first access so callers never handle a missing side."""
    CompanyCompliance.objects.get_or_create(company=company)
    CompanyBranding.objects.get_or_create(company=company)
    from apps.administration.models import CompanySettings
    CompanySettings.objects.get_or_create(company=company)
    return company


def default_bank_account(company):
    """The account documents quote for payment: the explicit default, else the
    only one, else nothing (and the document simply omits banking)."""
    accounts = list(company.bank_accounts.all())
    if not accounts:
        return None
    return next((a for a in accounts if a.is_default), accounts[0])


def primary_contact(company):
    contacts = list(company.contacts.all())
    if not contacts:
        return None
    return next((c for c in contacts if c.is_primary), contacts[0])


def physical_address_lines(company) -> list[str]:
    parts = [company.street_address, company.suburb, company.city,
             company.province, company.postal_code, company.country]
    return [p for p in parts if p]


def postal_address_lines(company) -> list[str]:
    """Falls back to the physical address when they are the same — the checkbox
    means "don't make me type it twice", so nothing is stored twice either."""
    if company.postal_same_as_physical:
        return physical_address_lines(company)
    parts = [company.postal_address, company.postal_city,
             company.postal_code_postal, company.postal_country or company.country]
    return [p for p in parts if p]


def document_header(company, *, kind="invoice") -> dict:
    """Everything a generated business document needs about *us*, in one call.

    Every document generator uses this instead of reading Company fields
    directly, so adding a field to letterheads is one change here rather than
    one per document type.
    """
    profile = get_profile(company)
    bank = default_bank_account(company)
    contact = primary_contact(company)
    compliance = profile.compliance

    return {
        "name": company.name,
        "trading_name": company.trading_name,
        "display_name": company.trading_name or company.name,
        "registration_no": company.registration_no,
        "vat_no": company.vat_no,
        "tax_reference_no": company.tax_reference_no,
        "is_vat_registered": bool(company.vat_no) or compliance.vat_registered,
        "address_lines": physical_address_lines(company),
        "email": company.email,
        "phone": company.phone,
        "mobile": company.mobile,
        "website": company.website,
        "logo": profile.branding.for_document(kind),
        "contact": ({"name": contact.full_name, "title": contact.job_title,
                     "email": contact.email or company.email,
                     "phone": contact.phone or contact.mobile or company.phone}
                    if contact else None),
        "bank": ({"bank_name": bank.bank_name, "account_name": bank.account_name,
                  "account_number": bank.account_number,
                  "branch_code": bank.branch_code, "branch_name": bank.branch_name,
                  "account_type": bank.get_account_type_display(),
                  "swift_code": bank.swift_code, "currency": bank.currency}
                 if bank else None),
        "csd_supplier_no": compliance.csd_supplier_no,
        "bbbee_level": compliance.bbbee_level,
    }


# ── Completeness ──────────────────────────────────────────────────────────────
#
# Weighted by consequence, not by field count. A missing VAT number stops you
# invoicing; a missing Twitter handle stops nothing.

_SECTIONS = [
    ("identity", "Company identity", 25),
    ("contact", "Contact details", 15),
    ("address", "Physical address", 15),
    ("banking", "Banking", 20),
    ("compliance", "Statutory registrations", 15),
    ("branding", "Branding", 5),
    ("business", "Business profile", 5),
]


def _section_state(company) -> dict:
    profile = get_profile(company)
    compliance = profile.compliance
    return {
        "identity": [
            ("Registered name", bool(company.name)),
            ("Registration number", bool(company.registration_no)),
            ("Tax reference number", bool(company.tax_reference_no)),
            ("Company type", bool(company.company_type)),
            ("Industry", bool(company.industry)),
        ],
        "contact": [
            ("Primary email", bool(company.email)),
            ("Primary phone", bool(company.phone)),
        ],
        "address": [
            ("Street address", bool(company.street_address)),
            ("City", bool(company.city)),
            ("Province", bool(company.province)),
            ("Postal code", bool(company.postal_code)),
        ],
        "banking": [
            ("At least one bank account", company.bank_accounts.exists()),
            ("A default account is set", company.bank_accounts.filter(
                is_default=True).exists() or company.bank_accounts.count() == 1),
        ],
        "compliance": [
            ("Income tax number", bool(compliance.income_tax_no)),
            ("VAT number (if registered)",
             bool(company.vat_no) or not compliance.vat_registered),
            ("CSD supplier number", bool(compliance.csd_supplier_no)),
        ],
        "branding": [("Company logo", bool(company.logo))],
        "business": [
            ("Business description", bool(company.description)),
            ("Services offered", bool(company.services_offered)),
        ],
    }


def completeness(company) -> dict:
    """Per-section scoring plus an overall percentage and the specific things
    still missing — a number alone tells nobody what to do next."""
    state = _section_state(company)
    sections, earned, possible, missing = [], 0, 0, []

    for key, label, weight in _SECTIONS:
        checks = state[key]
        done = sum(1 for _, ok in checks if ok)
        pct = round(100 * done / len(checks)) if checks else 100
        earned += weight * pct / 100
        possible += weight
        gaps = [name for name, ok in checks if not ok]
        missing.extend(gaps)
        sections.append({"key": key, "label": label, "pct": pct,
                         "done": done, "total": len(checks), "missing": gaps})

    overall = round(100 * earned / possible) if possible else 0
    return {
        "overall": overall,
        "sections": sections,
        "missing": missing,
        # What a person cannot do until they fix it — concrete, not a nag.
        "blocks": _blocked_actions(company),
    }


def _blocked_actions(company) -> list[str]:
    profile = get_profile(company)
    blocked = []
    if not company.bank_accounts.exists():
        blocked.append("Invoices will go out with no payment details.")
    if profile.compliance.vat_registered and not company.vat_no:
        blocked.append(
            "You are marked VAT-registered but have no VAT number — a tax "
            "invoice is not valid without it.")
    if not company.registration_no:
        blocked.append("Tenders and vendor onboarding require a registration number.")
    if not (company.street_address and company.city):
        blocked.append("Documents will print without a company address.")
    return blocked


# ── Mutations ─────────────────────────────────────────────────────────────────

@transaction.atomic
def add_bank_account(company, **fields) -> CompanyBankAccount:
    """The first account added becomes the default automatically — a company
    with one account should never have to think about the flag."""
    if not company.bank_accounts.exists():
        fields["is_default"] = True
    return CompanyBankAccount.objects.create(company=company, **fields)


def set_default_bank_account(account) -> CompanyBankAccount:
    account.is_default = True
    account.save()          # the model clears the others
    return account


@transaction.atomic
def add_contact(company, **fields) -> CompanyContact:
    if not company.contacts.exists():
        fields["is_primary"] = True
    return CompanyContact.objects.create(company=company, **fields)
