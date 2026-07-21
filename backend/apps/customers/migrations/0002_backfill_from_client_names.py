"""Turn the free-text `client_name` strings into real Customer organisations.

Existing quotations and projects carry a client as typed text — the live data
already contains 'Sasol Secunda', 'sibanye' and 'ns', which is exactly the mess
this module exists to end. Matching is case-insensitive so 'sibanye' and
'Sibanye' become one organisation rather than two.

`client_name` is deliberately LEFT IN PLACE. A quotation sent last year should
keep showing the name it was sent under even if the customer is later renamed,
and keeping it means nothing breaks while the codebase converts over.
"""

import re

from django.db import migrations


def _code_for(name, taken):
    base = (re.sub(r"[^A-Za-z]", "", name or "").upper()[:6]) or "CUST"
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = f"{base[:4]}{n:02d}"
        if candidate not in taken:
            return candidate
    return base


def forwards(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    Quotation = apps.get_model("quotes", "Quotation")
    Project = apps.get_model("projects", "Project")

    # company_id -> {lowercased name: customer}
    index: dict = {}
    taken: dict = {}

    for model in (Quotation, Project):
        for row in model.objects.filter(customer__isnull=True).exclude(client_name=""):
            key = (row.company_id, row.client_name.strip().lower())
            if not key[1]:
                continue
            customer = index.get(key)
            if customer is None:
                customer = Customer.objects.filter(
                    company_id=row.company_id, name__iexact=row.client_name.strip()
                ).first()
            if customer is None:
                company_taken = taken.setdefault(
                    row.company_id,
                    set(Customer.objects.filter(company_id=row.company_id)
                        .values_list("code", flat=True)))
                code = _code_for(row.client_name, company_taken)
                company_taken.add(code)
                customer = Customer.objects.create(
                    company_id=row.company_id, name=row.client_name.strip(),
                    code=code, status="active",
                )
            index[key] = customer
            row.customer = customer
            row.save(update_fields=["customer"])


def backwards(apps, schema_editor):
    """Unlink, but keep the customers — deleting organisations someone may have
    since filled in with contacts would lose real work."""
    for label in (("quotes", "Quotation"), ("projects", "Project")):
        model = apps.get_model(*label)
        model.objects.update(customer=None)


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0001_initial"),
        ("quotes", "0002_quotation_customer"),
        ("projects", "0002_project_customer"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
