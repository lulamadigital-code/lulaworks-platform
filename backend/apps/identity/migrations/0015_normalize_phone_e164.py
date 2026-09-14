"""Normalise existing phone numbers to E.164 (the canonical storage now that the
UI has a country-code picker). Non-destructive: a number is rewritten only when
it parses to a VALID number for the company's country (fallback ZA); anything
unparseable is left exactly as it was."""
from django.db import migrations


def _norm(raw, region):
    v = (raw or "").strip()
    if not v:
        return v
    try:
        import phonenumbers
        num = phonenumbers.parse(v, None if v.startswith("+") else region)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except Exception:      # noqa: BLE001 — leave legacy/garbage untouched
        pass
    return v


def forward(apps, schema_editor):
    Company = apps.get_model("identity", "Company")
    Contact = apps.get_model("identity", "CompanyContact")
    for c in Company.objects.all():
        region = (c.country_code or "ZA") or "ZA"
        changed = []
        for f in ("phone", "phone_secondary", "mobile", "emergency_phone", "whatsapp"):
            new = _norm(getattr(c, f, ""), region)
            if new != getattr(c, f, ""):
                setattr(c, f, new)
                changed.append(f)
        if changed:
            c.save(update_fields=changed)
    for ct in Contact.objects.all():
        region = "ZA"
        comp = Company.objects.filter(pk=ct.company_id).first()
        if comp and comp.country_code:
            region = comp.country_code
        changed = []
        for f in ("phone", "mobile"):
            new = _norm(getattr(ct, f, ""), region)
            if new != getattr(ct, f, ""):
                setattr(ct, f, new)
                changed.append(f)
        if changed:
            ct.save(update_fields=changed)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("identity", "0014_backfill_country_code")]
    operations = [migrations.RunPython(forward, noop)]
