# Hand-written: point file/image uploads at tenant-scoped callables so every
# customer's files land under c/<company_id>/… (gate #15 defence in depth).

from django.db import migrations, models

import apps.customers.models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0003_customer_vendor_note_customer_vendor_number_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customer',
            name='logo',
            field=models.ImageField(
                blank=True, null=True,
                upload_to=apps.customers.models.customer_logo_upload_path),
        ),
        migrations.AlterField(
            model_name='customerdocument',
            name='file',
            field=models.FileField(
                upload_to=apps.customers.models.customer_doc_upload_path),
        ),
    ]
