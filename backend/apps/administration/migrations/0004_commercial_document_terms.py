from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0003_companysettings_emergency_hours_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="companysettings",
            name="quotation_terms",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="companysettings",
            name="invoice_terms",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="companysettings",
            name="delivery_terms",
            field=models.TextField(blank=True),
        ),
    ]
