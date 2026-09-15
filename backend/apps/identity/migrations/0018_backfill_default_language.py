"""Non-destructive backfill: seed Company.default_language from the existing
CompanySettings.language where present. Nothing is overwritten — only blank
default_language fields are filled; the resolver already falls back to
CompanySettings.language when this is blank, so this is a convenience, not a
requirement. English remains the final system fallback."""
from django.db import migrations


def backfill(apps, schema_editor):
    Company = apps.get_model("identity", "Company")
    CompanySettings = apps.get_model("administration", "CompanySettings")
    settings_by_company = {s.company_id: s.language
                           for s in CompanySettings.objects.all()}
    for c in Company.objects.all():
        if c.default_language:
            continue
        lang = settings_by_company.get(c.id)
        if lang:
            c.default_language = lang
            c.save(update_fields=["default_language"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0017_company_default_language_company_default_locale_and_more"),
        ("administration", "0001_initial"),
    ]
    operations = [migrations.RunPython(backfill, noop)]
