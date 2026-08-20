from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0012_documenttemplate_family"),
    ]

    operations = [
        migrations.AddField(
            model_name="quotation",
            name="is_direct_invoice",
            field=models.BooleanField(default=False),
        ),
    ]
