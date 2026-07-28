"""Server-side upload validation.

The browser's ``accept=`` attribute is advisory only — a real request can carry
anything. Every uploaded file is validated here before it is stored or read:
an extension allowlist (so executables and scripts are rejected outright) and a
size cap. This is the single gate every upload path calls, so the policy lives
in one place.
"""

from django.core.exceptions import ValidationError

#: The document types the commercial module accepts — quotations, POs, drawings,
#: BOQs, photos, emails. Anything not on this list is rejected, so no executable
#: or script extension can slip through (allowlist, not blocklist).
ALLOWED_UPLOAD_EXTENSIONS = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx", "txt", "csv",
    "png", "jpg", "jpeg", "tif", "tiff", "gif", "webp",
    "zip", "eml", "msg",
})

MAX_UPLOAD_MB = 20


def _extension(name: str) -> str:
    name = (name or "").strip().lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def validate_upload(f, *, allowed=ALLOWED_UPLOAD_EXTENSIONS, max_mb=MAX_UPLOAD_MB):
    """Raise ``ValidationError`` unless ``f`` is an accepted type within the size
    cap. A missing file passes (callers decide whether one is required)."""
    if not f:
        return
    ext = _extension(getattr(f, "name", ""))
    if ext not in allowed:
        raise ValidationError(
            f"“{f.name}” is not an accepted file type. Allowed: "
            f"{', '.join(sorted(allowed))}.")
    size = getattr(f, "size", 0) or 0
    if size > max_mb * 1024 * 1024:
        raise ValidationError(f"“{f.name}” is larger than the {max_mb} MB limit.")


def clean_uploads(files, **kwargs):
    """Split an iterable of uploads into (accepted, rejected-reasons). Used where
    several files arrive together and the valid ones should still be kept."""
    accepted, rejected = [], []
    for f in files:
        try:
            validate_upload(f, **kwargs)
            accepted.append(f)
        except ValidationError as exc:
            rejected.append(f"{f.name}: {exc.messages[0]}")
    return accepted, rejected
