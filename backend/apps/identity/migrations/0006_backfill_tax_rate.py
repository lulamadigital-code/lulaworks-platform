"""Preserve existing tenants' tax behaviour.

New companies now default to 0% tax (neutral, set per company). Every company
that already existed was created under the old 15% VAT default, so backfill them
to 15% to keep their invoices unchanged. New companies are unaffected — this
runs once.
"""

from decimal import Decimal

from django.db import migrations


def preserve_existing_tax(apps, schema_editor):
    Company = apps.get_model("identity", "Company")
    Company.objects.filter(default_tax_rate=0).update(default_tax_rate=Decimal("15.00"))


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0005_company_default_tax_rate_alter_company_country_and_more"),
    ]

    operations = [
        migrations.RunPython(preserve_existing_tax, migrations.RunPython.noop),
    ]
