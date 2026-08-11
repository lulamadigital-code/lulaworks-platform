"""Attachment builders — how the email worker turns an attachment SPEC into bytes.

A document email doesn't ship a PDF through the task queue; it ships a tiny spec
like {"kind": "quotation_pdf", "id": "...", "name": "Quotation QT-1.pdf"}. At
delivery time the worker calls the registered builder for that kind, which
regenerates the document from its source record. Benefits: no large payloads in
the broker, and the attachment is always the current version.

Modules register their builders at startup (e.g. quotes in its AppConfig.ready),
so the notifications app never imports a business module — the dependency points
the right way (modules → notifications).
"""

import logging

logger = logging.getLogger(__name__)

#: kind → callable(entity_id) -> bytes
_BUILDERS: dict[str, callable] = {}


def register_attachment_builder(kind: str, builder) -> None:
    """Register a builder for an attachment kind. `builder(entity_id)` returns
    the file bytes (typically a PDF)."""
    _BUILDERS[kind] = builder


def build_attachments(specs) -> list[tuple[str, bytes, str]]:
    """Turn a list of specs into (filename, bytes, mimetype) tuples ready to
    attach. A builder that fails is skipped and logged — a missing attachment
    must never sink the whole email."""
    out = []
    for spec in specs or []:
        kind = spec.get("kind")
        builder = _BUILDERS.get(kind)
        if builder is None:
            logger.warning("No attachment builder for kind '%s'.", kind)
            continue
        try:
            data = builder(spec.get("id"))
        except Exception as exc:  # noqa: BLE001 - one bad attachment ≠ failed email
            logger.warning("Attachment '%s' (%s) failed to build: %s",
                           kind, spec.get("id"), exc)
            continue
        if not data:
            continue
        name = spec.get("name") or f"{kind}.pdf"
        out.append((name, data, spec.get("mimetype", "application/pdf")))
    return out
