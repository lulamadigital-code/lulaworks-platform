from django.db import migrations, models

import apps.quotes.models


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0007_quotation_discount_amount"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customerpurchaseorder",
            name="document",
            field=models.FileField(
                blank=True, null=True,
                upload_to=apps.quotes.models.po_upload_path),
        ),
        migrations.AlterField(
            model_name="quotationdocument",
            name="file",
            field=models.FileField(
                upload_to=apps.quotes.models.quotation_doc_upload_path),
        ),
    ]
