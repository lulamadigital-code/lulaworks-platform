from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("education", "0002_educationlead_leadevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="educationlead",
            name="subscribed",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="educationlead",
            name="welcomed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
