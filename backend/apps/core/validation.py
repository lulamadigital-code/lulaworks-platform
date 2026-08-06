"""Shared server-side validation for untrusted text input.

Django already gives us the two big guarantees automatically — the ORM
parameterises queries (no SQL injection) and templates auto-escape output (no
stored XSS) — so these helpers focus on the rest: required fields, sane length
bounds (so an attacker can't flood a field or overflow a column), and format
checks (email). Use them wherever a view parses request.POST by hand,
especially on anonymous/public forms.
"""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email as _validate_email


class InputError(Exception):
    """A user-fixable input problem. The message is safe to show back verbatim."""


def clean_str(value, *, field, max_length, required=False, min_length=0):
    """Strip, then enforce required / length bounds. Returns the cleaned value."""
    v = (value or "").strip()
    if required and not v:
        raise InputError(f"{field} is required.")
    if v and len(v) < min_length:
        raise InputError(f"{field} must be at least {min_length} characters.")
    if len(v) > max_length:
        raise InputError(f"{field} is too long (maximum {max_length} characters).")
    return v


def clean_email(value, *, required=True):
    """Validate + normalise an email address."""
    v = (value or "").strip().lower()
    if not v:
        if required:
            raise InputError("A valid email address is required.")
        return v
    if len(v) > 254:
        raise InputError("That email address is too long.")
    try:
        _validate_email(v)
    except ValidationError:
        raise InputError("Please enter a valid email address.")
    return v
