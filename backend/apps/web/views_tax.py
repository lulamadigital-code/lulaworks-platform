"""Company tax settings — configure the tax engine (apps.tax) for this company.

Owners set their rate, label, tax number, whether prices are tax-inclusive, and
whether cross-border reverse charge applies — or apply their country's standard
treatment in one click. These values drive every new invoice's tax.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


def _dec(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


@login_required
def company_tax(request):
    from apps.tax.services import apply_company_jurisdiction_defaults, jurisdiction_for
    company = request.user.active_company
    can_manage = request.user.has_perm_code("company.manage")

    if request.method == "POST":
        if not can_manage:
            messages.error(request, "You do not have permission to change tax settings.")
            return redirect("web:company_tax")

        if request.POST.get("action") == "apply_default":
            if apply_company_jurisdiction_defaults(company):
                messages.success(request, "Applied your country's standard tax treatment.")
            else:
                messages.error(
                    request,
                    "No standard tax profile for your country yet — set it manually below.",
                )
            return redirect("web:company_tax")

        company.default_tax_rate = _dec(request.POST.get("rate"))
        company.tax_name = (request.POST.get("tax_name") or "").strip()
        tax_number = (request.POST.get("tax_number") or "").strip()
        if tax_number:
            company.vat_no = tax_number
        company.prices_include_tax = bool(request.POST.get("prices_include_tax"))
        company.reverse_charge_enabled = bool(request.POST.get("reverse_charge_enabled"))
        company.save(update_fields=[
            "default_tax_rate", "tax_name", "vat_no",
            "prices_include_tax", "reverse_charge_enabled", "updated_at",
        ])
        messages.success(request, "Tax settings saved — they apply to new invoices.")
        return redirect("web:company_tax")

    return render(request, "web/company_tax.html", {
        "company": company,
        "can_manage": can_manage,
        "jurisdiction": jurisdiction_for(company.country),
    })
