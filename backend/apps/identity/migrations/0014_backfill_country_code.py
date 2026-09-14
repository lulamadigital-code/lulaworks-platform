"""Non-destructive backfill: derive Company.country_code (ISO alpha-2) from the
existing free-text `country` name. Unmatched names are left blank (nothing is
overwritten or deleted); `currency` is untouched. Self-contained name map so it
never depends on the reference tables being seeded first."""
from django.db import migrations

_NAME_TO_CODE = {
    "south africa": "ZA", "za": "ZA", "rsa": "ZA", "republic of south africa": "ZA",
    "united states": "US", "usa": "US", "united states of america": "US",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "britain": "GB",
    "australia": "AU", "canada": "CA", "india": "IN", "germany": "DE",
    "france": "FR", "netherlands": "NL", "ireland": "IE", "nigeria": "NG",
    "kenya": "KE", "ghana": "GH", "botswana": "BW", "namibia": "NA",
    "zambia": "ZM", "zimbabwe": "ZW", "united arab emirates": "AE", "uae": "AE",
    "singapore": "SG", "new zealand": "NZ", "brazil": "BR", "japan": "JP",
}


def backfill(apps, schema_editor):
    Company = apps.get_model("identity", "Company")
    for c in Company.objects.all():
        if c.country_code:
            continue
        code = _NAME_TO_CODE.get((c.country or "").strip().lower())
        if code:
            c.country_code = code
            c.save(update_fields=["country_code"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0013_company_country_code_companybankaccount_bank_and_more"),
    ]
    operations = [migrations.RunPython(backfill, noop)]
