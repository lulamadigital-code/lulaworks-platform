from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0006_commercialdocument_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="quotation",
            name="discount_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"),
                                      max_digits=12),
        ),
    ]
