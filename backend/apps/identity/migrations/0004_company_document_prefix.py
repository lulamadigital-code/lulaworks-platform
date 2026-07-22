from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0003_company_company_type_company_description_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="document_prefix",
            field=models.CharField(blank=True, max_length=4),
        ),
    ]
