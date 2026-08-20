from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0010_alter_user_platform_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="receives_education_leads",
            field=models.BooleanField(default=False),
        ),
    ]
