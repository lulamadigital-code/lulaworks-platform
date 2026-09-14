"""Shared server-side validation for untrusted text input.

Django already gives us the two big guarantees automatically — the ORM
parameterises queries (no SQL injection) and templates auto-escape output (no
stored XSS) — so these helpers focus on the rest: required fields, sane length
bounds (so an attacker can't flood a field or overflow a column), and format
checks (email). Use them wherever a view parses request.POST by hand,
especially on anonymous/public forms.
"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.core.validators import validate_email as _validate_email


class InputError(Exception):
    """A user-fixable input problem. The message is safe to show back verbatim."""


# Control characters (except tab/newline) should never appear in a stored field —
# they're invisible and only ever come from paste accidents or injection attempts.
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_str(value, *, field, max_length, required=False, min_length=0,
              collapse_ws=False):
    """Strip control chars, trim, optionally collapse internal whitespace, then
    enforce required / length bounds. Returns the cleaned value.

    Note: no character allow-listing — legitimate business names carry &, -, ',
    ., (, ) and accents. Output safety is handled by Django's auto-escaping, not
    by rejecting punctuation here."""
    v = _CTRL.sub("", value or "").strip()
    if collapse_ws:
        v = re.sub(r"\s{2,}", " ", v)
    if required and not v:
        raise InputError(f"{field} is required.")
    if v and len(v) < min_length:
        raise InputError(f"{field} must be at least {min_length} characters.")
    if len(v) > max_length:
        raise InputError(f"{field} is too long (maximum {max_length} characters).")
    return v


def clean_url(value, *, field="Website", required=False, max_length=255):
    """Validate + normalise a URL. Accepts 'example.com' / 'www.example.com' /
    'https://example.com' and canonicalises to an https:// URL. Rejects plain
    text like 'hello' or '123'."""
    v = (value or "").strip()
    if not v:
        if required:
            raise InputError(f"{field} is required.")
        return v
    if len(v) > max_length:
        raise InputError(f"{field} is too long (maximum {max_length} characters).")
    if not re.match(r"^https?://", v, re.IGNORECASE):
        v = "https://" + v
    try:
        URLValidator(schemes=["http", "https"])(v)
    except ValidationError:
        raise InputError(f"Enter a valid {field.lower()} (e.g. https://example.com).")
    return v


def clean_prefix(value, *, field="Reference prefix", max_length=4):
    """A short document-reference prefix: letters/digits only, upper-cased, no
    whitespace / HTML / control characters, capped length."""
    v = _CTRL.sub("", value or "").strip().upper()
    if not v:
        return v
    if not re.fullmatch(r"[A-Z0-9]{1," + str(max_length) + r"}", v):
        raise InputError(
            f"{field} must be {max_length} letters or digits (no spaces or symbols).")
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
