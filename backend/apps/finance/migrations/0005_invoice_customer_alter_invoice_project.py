import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0001_initial"),
        ("projects", "0001_initial"),
        ("finance", "0004_invoice_reverse_charge_invoice_tax_inclusive_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="customer",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to="customers.customer",
            ),
        ),
        migrations.AlterField(
            model_name="invoice",
            name="project",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoices", to="projects.project",
            ),
        ),
    ]
