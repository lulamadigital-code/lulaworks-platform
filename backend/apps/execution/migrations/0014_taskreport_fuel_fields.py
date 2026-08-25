from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0013_alter_task_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskreport",
            name="litres",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=8, null=True
            ),
        ),
        migrations.AddField(
            model_name="taskreport",
            name="odometer_km",
            field=models.DecimalField(
                blank=True, decimal_places=1, max_digits=10, null=True
            ),
        ),
        migrations.AddField(
            model_name="taskreport",
            name="vehicle",
            field=models.CharField(blank=True, default="", max_length=60),
            preserve_default=False,
        ),
    ]
