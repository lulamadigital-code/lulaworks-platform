"""Document Designer services — resolving which look a document should wear.

The rule everywhere a PDF is generated:

    per-document override  →  company default for that type  →  plain layout

`effective_config` is what the PDF builders call; it always returns a full,
validated switch-set (never a partial dict), so a builder can read any key
without guarding. `clean_config` is the gate every write goes through, so a
form today — or an AI importer tomorrow — can never store an unknown key or an
unsafe value.
"""

import re

from django.db import transaction
from django.utils import timezone

from .models import (
    ALLOWED_FONTS,
    ALLOWED_LOGO_POSITIONS,
    BUILTIN_TEMPLATES,
    DEFAULT_CONFIG,
    BaseLayout,
    DocumentTemplate,
    DocumentTemplateVersion,
    TemplateEngine,
    TemplateOrigin,
)

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{6})$")
#: Keys whose value is a colour string ("" allowed → fall back to brand).
_COLOR_KEYS = {"accent_color", "secondary_color"}
#: Keys whose value is free text (length-capped).
_TEXT_KEYS = {"header_note", "footer_note", "watermark_text", "terms_override"}


class TemplateError(Exception):
    """A user-fixable problem with a template (bad colour, unknown font)."""


def clean_config(raw: dict) -> dict:
    """Validate + normalise an incoming config into the full switch-set.

    Unknown keys are dropped (not an error — forward-compatible). Every known
    key is coerced to the right type and range; bad colours/fonts raise so the
    user is told, rather than silently producing a broken document.
    """
    raw = raw or {}
    out = dict(DEFAULT_CONFIG)
    for key, default in DEFAULT_CONFIG.items():
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(default, bool):
            out[key] = _as_bool(val)
        elif key in _COLOR_KEYS:
            val = (val or "").strip()
            if val and not _HEX.match(val):
                raise TemplateError(f"“{val}” isn’t a valid colour (use #RRGGBB).")
            out[key] = val
        elif key == "font":
            val = (val or "").strip() or "Helvetica"
            if val not in ALLOWED_FONTS:
                raise TemplateError(f"Unsupported font “{val}”.")
            out[key] = val
        elif key == "logo_position":
            val = (val or "").strip() or "left"
            if val not in ALLOWED_LOGO_POSITIONS:
                raise TemplateError(f"Unknown logo position “{val}”.")
            out[key] = val
        elif key in _TEXT_KEYS:
            out[key] = (val or "").strip()[:2000]
        else:
            out[key] = val
    return out


def _as_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "on", "yes")


# ── Resolution ────────────────────────────────────────────────────────────────

def resolve_template(company, doc_type, *, override=None):
    """Which template a document should render with, or None for the plain
    layout. `override` is the document's own `template` FK (may be None).

    A per-document override is honoured only when its type matches — a quotation
    template must never render an invoice (spec: document-type validation)."""
    if override is not None and override.doc_type == doc_type:
        return override
    return (DocumentTemplate.objects.filter(company=company, doc_type=doc_type,
                                            is_default=True, archived_at__isnull=True).first()
            or DocumentTemplate.objects.filter(company=company, doc_type=doc_type,
                                               archived_at__isnull=True).first())


def effective_config(company, doc_type, *, override=None) -> dict:
    """The full switch-set the PDF builders read. Falls back to DEFAULT_CONFIG
    (i.e. today's output) when the company has no template for this type."""
    tpl = resolve_template(company, doc_type, override=override)
    cfg = tpl.merged_config() if tpl else dict(DEFAULT_CONFIG)
    cfg["_base_layout"] = tpl.base_layout if tpl else BaseLayout.CLASSIC
    return cfg


def _config_from_version(version) -> dict:
    """The switch-set frozen into a pinned template version — so a finalised
    document renders exactly as it did the day it was issued."""
    cfg = version.merged_config()
    cfg["_base_layout"] = version.base_layout
    return cfg


def effective_config_for(document, doc_type) -> dict:
    """Convenience for a Quotation / CommercialDocument that carries its own
    `template` and `company`.

    Immutability: if the document has a pinned `template_version` (set at
    finalise), render from that frozen snapshot; otherwise resolve the live
    template head, so an editable document always shows the latest design."""
    pinned = getattr(document, "template_version", None)
    if pinned is not None:
        return _config_from_version(pinned)
    return effective_config(document.company, doc_type,
                            override=getattr(document, "template", None))


# ── Versioning ────────────────────────────────────────────────────────────────

def _snapshot_version(template, actor, *, note=""):
    """Freeze the template's current fields into a new immutable version and point
    the head at it. Called on every create/edit; the resulting version is what a
    finalised document pins so a later edit can't rewrite an issued document."""
    last = template.versions.order_by("-version").first()
    number = (last.version + 1) if last else 1
    version = DocumentTemplateVersion.objects.create(
        company=template.company, template=template, version=number,
        engine=template.engine, base_layout=template.base_layout,
        config=dict(template.config or {}), note=(note or "")[:200],
        created_by=actor, updated_by=actor,
    )
    template.current_version = version
    template.updated_by = actor
    template.save(update_fields=["current_version", "updated_by", "updated_at"])
    return version


def pin_template_version(document, doc_type, actor=None):
    """Pin the resolved template's current version onto a document — call this at
    finalise so the issued document is frozen against later template edits.
    Idempotent: a no-op once pinned. Returns the version, or None if the company
    renders with no template (the plain layout, which never changes anyway)."""
    if getattr(document, "template_version_id", None):
        return document.template_version
    tpl = resolve_template(document.company, doc_type,
                           override=getattr(document, "template", None))
    if tpl is None:
        return None
    version = tpl.current_version or _snapshot_version(tpl, actor, note="auto-pin")
    document.template_version = version
    document.save(update_fields=["template_version"])
    return version


# ── Management ────────────────────────────────────────────────────────────────

@transaction.atomic
def seed_document_templates(company, actor=None) -> int:
    """Create the built-in library for a company that has none. Idempotent —
    returns how many were created (0 if they already exist)."""
    if DocumentTemplate.objects.filter(company=company).exists():
        return 0
    created = 0
    for doc_type, name, layout, is_default, cfg in BUILTIN_TEMPLATES:
        tpl = DocumentTemplate.objects.create(
            company=company, doc_type=doc_type, name=name, base_layout=layout,
            is_default=is_default, is_builtin=True, origin=TemplateOrigin.BUILTIN,
            engine=TemplateEngine.REPORTLAB, config=clean_config(cfg),
            created_by=actor, updated_by=actor,
        )
        _snapshot_version(tpl, actor, note="Built-in")
        created += 1
    return created


@transaction.atomic
def sync_builtin_templates(company, actor=None) -> int:
    """Top-up: add any built-in templates the company is missing, WITHOUT touching
    what it already has. Unlike seed_document_templates (which only runs for a
    company that has none), this reaches already-seeded companies when new
    built-ins ship — e.g. the house styles. Never changes an existing row, never
    moves the default flag. Matches on (doc_type, name). Returns how many added."""
    existing = {(t.doc_type, t.name)
                for t in DocumentTemplate.objects.filter(company=company)}
    created = 0
    for doc_type, name, layout, is_default, cfg in BUILTIN_TEMPLATES:
        if (doc_type, name) in existing:
            continue
        # Only seed a default when the company genuinely has none for that type,
        # so a top-up never overrides a chosen default.
        wants_default = is_default and not DocumentTemplate.objects.filter(
            company=company, doc_type=doc_type, is_default=True).exists()
        tpl = DocumentTemplate.objects.create(
            company=company, doc_type=doc_type, name=name, base_layout=layout,
            is_default=wants_default, is_builtin=True, origin=TemplateOrigin.BUILTIN,
            engine=TemplateEngine.REPORTLAB, config=clean_config(cfg),
            created_by=actor, updated_by=actor,
        )
        _snapshot_version(tpl, actor, note="Built-in")
        existing.add((doc_type, name))
        created += 1
    return created


@transaction.atomic
def create_template(company, actor, *, doc_type, name, base_layout, config=None,
                    description="", origin=TemplateOrigin.CUSTOM,
                    engine=TemplateEngine.REPORTLAB):
    name = (name or "").strip()
    if not name:
        raise TemplateError("A template needs a name.")
    if doc_type not in dict(DocumentTemplate._meta.get_field("doc_type").choices):
        raise TemplateError("Unknown document type.")
    tpl = DocumentTemplate.objects.create(
        company=company, doc_type=doc_type, name=name,
        description=(description or "")[:255], origin=origin, engine=engine,
        base_layout=base_layout or BaseLayout.CLASSIC,
        config=clean_config(config or {}), created_by=actor, updated_by=actor,
    )
    _snapshot_version(tpl, actor, note="Created")
    # First template of its type becomes the default automatically.
    if not DocumentTemplate.objects.filter(company=company, doc_type=doc_type,
                                           is_default=True, archived_at__isnull=True).exists():
        set_default_template(tpl)
    return tpl


@transaction.atomic
def update_template(template, actor, *, name=None, base_layout=None, config=None,
                    description=None, note=""):
    """Edit a template. Each save records a NEW immutable version — documents
    already finalised against an earlier version are unaffected."""
    if name is not None:
        name = name.strip()
        if not name:
            raise TemplateError("A template needs a name.")
        template.name = name
    if base_layout is not None:
        template.base_layout = base_layout
    if description is not None:
        template.description = description[:255]
    if config is not None:
        template.config = clean_config(config)
    template.updated_by = actor
    template.save()
    _snapshot_version(template, actor, note=note or "Edited")
    return template


@transaction.atomic
def duplicate_template(template, actor, *, new_name=None):
    """Copy a template into a fresh, editable one (never the default). Handy for
    'start from this look and tweak it'."""
    name = (new_name or f"{template.name} copy").strip()[:80]
    tpl = DocumentTemplate.objects.create(
        company=template.company, doc_type=template.doc_type, name=name,
        description=template.description, origin=TemplateOrigin.CUSTOM,
        engine=template.engine, base_layout=template.base_layout,
        is_default=False, is_builtin=False, config=dict(template.config or {}),
        created_by=actor, updated_by=actor,
    )
    _snapshot_version(tpl, actor, note=f"Duplicated from {template.name}")
    return tpl


@transaction.atomic
def archive_template(template, actor):
    """Hide a template from the picker (kept — a past document may reference it).
    The company default can't be archived; set another default first."""
    if template.is_default:
        raise TemplateError("This is the default — set another template as the "
                            "default before archiving it.")
    template.archived_at = timezone.now()
    template.updated_by = actor
    template.save(update_fields=["archived_at", "updated_by", "updated_at"])
    return template


@transaction.atomic
def restore_template(template, actor):
    template.archived_at = None
    template.updated_by = actor
    template.save(update_fields=["archived_at", "updated_by", "updated_at"])
    return template


@transaction.atomic
def set_default_template(template):
    """Make this the company's default for its type; unset the others. An archived
    template can't be a default."""
    if template.archived_at is not None:
        raise TemplateError("Restore this template before making it the default.")
    DocumentTemplate.objects.filter(
        company=template.company, doc_type=template.doc_type, is_default=True
    ).exclude(pk=template.pk).update(is_default=False)
    if not template.is_default:
        template.is_default = True
        template.save(update_fields=["is_default"])
    return template


def templates_for(company, doc_type, *, include_archived=False):
    qs = DocumentTemplate.objects.filter(company=company, doc_type=doc_type)
    if not include_archived:
        qs = qs.filter(archived_at__isnull=True)
    return list(qs)


def version_history(template):
    return list(template.versions.all())
