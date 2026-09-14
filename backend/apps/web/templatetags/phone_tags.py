"""Template helpers for the reusable phone widget: split a stored E.164 number
back into its region (for the country-code picker) and national part (for the
number input). Falls back gracefully for legacy / unparseable values."""
from django import template

register = template.Library()


@register.filter
def phone_region(value, default="ZA"):
    """ISO alpha-2 region for a stored number (from E.164), else `default`."""
    v = (value or "").strip()
    if not v:
        return default
    try:
        import phonenumbers
        num = phonenumbers.parse(v, None if v.startswith("+") else default)
        return phonenumbers.region_code_for_number(num) or default
    except Exception:                       # noqa: BLE001 — legacy/garbage value
        return default


@register.filter
def phone_national(value, default="ZA"):
    """National (local) portion of a stored number for display in the input.
    Legacy values that aren't E.164 are shown as-is."""
    v = (value or "").strip()
    if not v:
        return ""
    try:
        import phonenumbers
        num = phonenumbers.parse(v, None if v.startswith("+") else default)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(
                num, phonenumbers.PhoneNumberFormat.NATIONAL)
    except Exception:                       # noqa: BLE001
        pass
    return v
