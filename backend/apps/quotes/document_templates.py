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
    ALLOWED_FONT_FAMILIES,
    ALLOWED_FONTS,
    ALLOWED_FOOTER_LAYOUTS,
    ALLOWED_HEADER_STYLES,
    ALLOWED_LOGO_POSITIONS,
    ALLOWED_LOGO_SIZES,
    ALLOWED_SECTION_STYLES,
    ALLOWED_TABLE_STYLES,
    ALLOWED_TEMPLATE_FAMILY_NAMES,
    ALLOWED_TOTALS_STYLES,
    LOGO_HEIGHT_MAX,
    LOGO_HEIGHT_MIN,
    DEFAULT_CONFIG,
    DEFAULT_DESIGN,
    DEFAULT_FAMILY_KEY,
    TEMPLATE_FAMILIES,
    TEMPLATE_ITEM_COLUMN_KEYS,
    TEMPLATE_SECTION_KEYS,
    BaseLayout,
    DocumentTemplate,
    DocumentTemplateVersion,
    TemplateEngine,
    TemplateOrigin,
)


def assert_allowed_template_name(name: str, *, is_builtin: bool) -> None:
    """Allowlist gate for BUILT-IN template names: a LulaWorks-shipped template must
    carry one of the twelve original family names. This makes it impossible for any
    third-party product name to enter the shipped catalogue — without the code ever
    having to enumerate competitor names. Customers may still name their own custom
    templates freely (this is a no-op unless `is_builtin`)."""
    if is_builtin and (name or "").strip() not in ALLOWED_TEMPLATE_FAMILY_NAMES:
        raise TemplateError(
            f"“{name}” isn’t a LulaWorks design family — built-in templates must "
            f"use one of the original family names.")

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


def clean_design(raw: dict) -> dict:
    """Validate an HTML-engine `design` into the full, safe schema. Unknown section
    keys / columns are dropped; every known section is preserved in the given order
    (missing ones appended visible); colours/fonts are checked. This is the single
    write gate the visual builder AND a future AI import both pass through."""
    raw = raw or {}
    b_in = raw.get("branding") or {}
    branding = dict(DEFAULT_DESIGN["branding"])
    for key in ("accent_color", "secondary_color"):
        if key in b_in:
            val = (b_in.get(key) or "").strip()
            if val and not _HEX.match(val):
                raise TemplateError(f"“{val}” isn’t a valid colour (use #RRGGBB).")
            branding[key] = val
    if "font_family" in b_in:
        font = (b_in.get("font_family") or "").strip() or "Helvetica"
        if font not in ALLOWED_FONT_FAMILIES:
            raise TemplateError(f"Unsupported font “{font}”.")
        branding["font_family"] = font
    if "logo_position" in b_in:
        lp = (b_in.get("logo_position") or "").strip() or "left"
        if lp not in ALLOWED_LOGO_POSITIONS:
            raise TemplateError(f"Unknown logo position “{lp}”.")
        branding["logo_position"] = lp
    if "logo_size" in b_in:
        ls = (b_in.get("logo_size") or "").strip() or "medium"
        if ls not in ALLOWED_LOGO_SIZES:
            raise TemplateError(f"Unknown logo size “{ls}”.")
        branding["logo_size"] = ls
    if "logo_height" in b_in:
        try:
            h = int(float(b_in.get("logo_height") or 0))
        except (TypeError, ValueError):
            h = 0
        # 0 = use the named preset; otherwise clamp to a printable range.
        branding["logo_height"] = 0 if h <= 0 else max(LOGO_HEIGHT_MIN, min(LOGO_HEIGHT_MAX, h))
    if "header_style" in b_in:
        hs = (b_in.get("header_style") or "").strip() or "band"
        if hs not in ALLOWED_HEADER_STYLES:
            raise TemplateError(f"Unknown header style “{hs}”.")
        branding["header_style"] = hs

    sections, seen = [], set()
    for entry in (raw.get("sections") or []):
        key = entry.get("key")
        if key in TEMPLATE_SECTION_KEYS and key not in seen:
            sections.append({"key": key, "visible": _as_bool(entry.get("visible", True))})
            seen.add(key)
    for key in TEMPLATE_SECTION_KEYS:       # append any not mentioned, visible
        if key not in seen:
            sections.append({"key": key, "visible": True})

    columns = [c for c in (raw.get("columns") or []) if c in TEMPLATE_ITEM_COLUMN_KEYS]
    if not columns:
        columns = list(TEMPLATE_ITEM_COLUMN_KEYS)

    def _choice(key, allowed):
        val = (raw.get(key) or DEFAULT_DESIGN[key])
        if key in raw and val not in allowed:
            raise TemplateError(f"Unknown {key.replace('_', ' ')} “{val}”.")
        return val if val in allowed else DEFAULT_DESIGN[key]

    return {
        "branding": branding, "sections": sections, "columns": columns,
        "table_style": _choice("table_style", ALLOWED_TABLE_STYLES),
        "totals_style": _choice("totals_style", ALLOWED_TOTALS_STYLES),
        "section_title_style": _choice("section_title_style", ALLOWED_SECTION_STYLES),
        "footer_layout": _choice("footer_layout", ALLOWED_FOOTER_LAYOUTS),
        "header_note": (raw.get("header_note") or "").strip()[:200],
        "footer_note": (raw.get("footer_note") or "").strip()[:200],
    }


def current_design(template) -> dict:
    """The design the builder edits — the current version's, or a clean default."""
    version = template.current_version
    if version and version.design:
        return version.design
    return {k: v for k, v in DEFAULT_DESIGN.items()}  # shallow copy is fine here


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


def _html_spec(version):
    """The HTML-engine render spec from a version: its structured `design` plus any
    AI-recreated raw `html`/`css` (when present, the raw template is rendered)."""
    return {"design": (version.design or {}) if version else {},
            "html": (version.html or "") if version else "",
            "css": (version.css or "") if version else ""}


def resolve_render(document, doc_type):
    """Which engine renders this document, and the HTML render spec when it's the
    HTML engine. Honours a pinned version (immutability) first, else the live
    template head. Returns (engine, spec); spec is {} for the ReportLab engine and
    {design, html, css} for the HTML engine."""
    from .models import TemplateEngine

    pinned = getattr(document, "template_version", None)
    if pinned is not None:
        return pinned.engine, (_html_spec(pinned)
                               if pinned.engine == TemplateEngine.HTML else {})
    tpl = resolve_template(document.company, doc_type,
                           override=getattr(document, "template", None))
    if tpl is None:
        return TemplateEngine.REPORTLAB, {}
    if tpl.engine == TemplateEngine.HTML:
        return TemplateEngine.HTML, _html_spec(tpl.current_version)
    return TemplateEngine.REPORTLAB, {}


# ── Versioning ────────────────────────────────────────────────────────────────

def _snapshot_version(template, actor, *, note="", design=None, html=None, css=None):
    """Freeze the template's current fields into a new immutable version and point
    the head at it. Called on every create/edit; the resulting version is what a
    finalised document pins so a later edit can't rewrite an issued document.
    `design` carries the HTML-engine payload; `html`/`css` carry an AI-recreated raw
    template. All three are inherited from the previous version when not re-supplied,
    so a config-only edit keeps the design and any raw template."""
    last = template.versions.order_by("-version").first()
    number = (last.version + 1) if last else 1
    if design is None:
        design = dict(last.design) if (last and last.design) else {}
    if html is None:
        html = (last.html if last else "") or ""
    if css is None:
        css = (last.css if last else "") or ""
    version = DocumentTemplateVersion.objects.create(
        company=template.company, template=template, version=number,
        engine=template.engine, base_layout=template.base_layout,
        config=dict(template.config or {}), design=design, html=html, css=css,
        note=(note or "")[:200], created_by=actor, updated_by=actor,
    )
    template.current_version = version
    template.updated_by = actor
    template.save(update_fields=["current_version", "updated_by", "updated_at"])
    return version


@transaction.atomic
def create_html_template(company, actor, *, doc_type, name, design=None,
                         html="", css="", description="", origin=TemplateOrigin.CUSTOM):
    """A company's own (or AI-imported) HTML-engine template. Same lifecycle as a
    ReportLab template — versioned, default-able. Rendered from a structured
    `design`, or, when `html` is supplied, from an AI-recreated raw HTML/CSS
    template that reproduces an uploaded document's layout."""
    name = (name or "").strip()
    if not name:
        raise TemplateError("A template needs a name.")
    assert_allowed_template_name(name, is_builtin=(origin == TemplateOrigin.BUILTIN))
    if doc_type not in dict(DocumentTemplate._meta.get_field("doc_type").choices):
        raise TemplateError("Unknown document type.")
    tpl = DocumentTemplate.objects.create(
        company=company, doc_type=doc_type, name=name,
        description=(description or "")[:255], origin=origin,
        engine=TemplateEngine.HTML, config={},
        created_by=actor, updated_by=actor,
    )
    _snapshot_version(tpl, actor, note="Created", design=clean_design(design or {}),
                      html=html or "", css=css or "")
    if not DocumentTemplate.objects.filter(company=company, doc_type=doc_type,
                                           is_default=True, archived_at__isnull=True).exists():
        set_default_template(tpl)
    return tpl


@transaction.atomic
def update_html_design(template, actor, *, design, name=None, description=None, note=""):
    """Save an edit to an HTML template's design — a new immutable version."""
    if name is not None:
        name = name.strip()
        if not name:
            raise TemplateError("A template needs a name.")
        template.name = name
    if description is not None:
        template.description = description[:255]
    template.updated_by = actor
    template.save()
    _snapshot_version(template, actor, note=note or "Edited",
                      design=clean_design(design or {}))
    return template


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

def _seed_families(company, actor, existing) -> int:
    """Create the built-in LulaWorks template FAMILIES the company is missing —
    each family expanded across the three document types, sharing one visual
    identity (HTML engine). `existing` is a set of (doc_type, name) already present
    and is updated in place. The default family (Horizon) is made the company
    default for a document type ONLY when that type has no default yet, so a
    top-up never overrides a chosen default. Returns how many were created."""
    created = 0
    for key, name, description, _tags, design in TEMPLATE_FAMILIES:
        clean = clean_design(design)
        for doc_type in ("quotation", "invoice", "delivery"):
            if (doc_type, name) in existing:
                continue
            wants_default = (
                key == DEFAULT_FAMILY_KEY
                and not DocumentTemplate.objects.filter(
                    company=company, doc_type=doc_type, is_default=True,
                    archived_at__isnull=True).exists())
            tpl = DocumentTemplate.objects.create(
                company=company, doc_type=doc_type, name=name, family=key,
                description=description, is_default=wants_default, is_builtin=True,
                origin=TemplateOrigin.BUILTIN, engine=TemplateEngine.HTML,
                config={}, created_by=actor, updated_by=actor,
            )
            _snapshot_version(tpl, actor, note="Family", design=clean)
            existing.add((doc_type, name))
            created += 1
    return created


@transaction.atomic
def seed_document_templates(company, actor=None) -> int:
    """Create the built-in family library for a company that has none. Idempotent —
    returns how many were created (0 if they already exist)."""
    if DocumentTemplate.objects.filter(company=company).exists():
        return 0
    return _seed_families(company, actor, set())


@transaction.atomic
def sync_builtin_templates(company, actor=None) -> int:
    """Top-up: add any built-in family variants the company is missing, WITHOUT
    touching what it already has. Reaches already-seeded companies when new
    families ship. Never changes an existing row; only sets a default where a
    document type has none. Matches on (doc_type, name). Returns how many added."""
    existing = {(t.doc_type, t.name)
                for t in DocumentTemplate.objects.filter(company=company)}
    return _seed_families(company, actor, existing)


@transaction.atomic
def resync_builtin_family_designs(company, actor=None) -> int:
    """Refresh each built-in FAMILY template's design to the latest code definition
    — so shipped improvements (new section orders, footer layouts, larger logo
    controls) reach companies seeded earlier. SAFE: a family the user has customised
    in the builder is left untouched, and finalised documents keep their pinned
    version. Returns how many templates were refreshed."""
    from .models import FAMILY_BY_KEY
    updated = 0
    qs = (DocumentTemplate.objects.filter(company=company, is_builtin=True,
                                          engine=TemplateEngine.HTML)
          .exclude(family=""))
    for tpl in qs:
        meta = FAMILY_BY_KEY.get(tpl.family)
        if not meta:
            continue
        # A pristine built-in family only carries seed ("Family") / auto-pin notes;
        # any other note means the user edited it — leave those alone.
        notes = set(tpl.versions.values_list("note", flat=True))
        if notes - {"Family", "auto-pin"}:
            continue
        new_design = clean_design(meta[3])
        if current_design(tpl) == new_design:
            continue      # already current
        _snapshot_version(tpl, actor, note="Family", design=new_design)
        updated += 1
    return updated


@transaction.atomic
def create_template(company, actor, *, doc_type, name, base_layout, config=None,
                    description="", origin=TemplateOrigin.CUSTOM,
                    engine=TemplateEngine.REPORTLAB):
    name = (name or "").strip()
    if not name:
        raise TemplateError("A template needs a name.")
    assert_allowed_template_name(name, is_builtin=(origin == TemplateOrigin.BUILTIN))
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
    src = template.current_version
    design = dict(src.design) if (src and src.design) else {}
    _snapshot_version(tpl, actor, note=f"Duplicated from {template.name}", design=design)
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


#: Human labels for the per-type templates a default set creates.
_SET_LABELS = {"quotation": "Quotation", "invoice": "Tax Invoice", "delivery": "Delivery Note"}


@transaction.atomic
def apply_as_default_set(template, actor):
    """Make this design the company's default across ALL THREE document types: set it
    as the default for its own type, and create a matching template (same structured
    design) for each other type, each set as its default. One shared look renders
    correctly per type — the capability layer adapts content (a delivery note shows
    quantities not prices, etc.). Returns how many templates the set now has."""
    ver = template.current_version
    design = dict(ver.design) if (ver and ver.design) else {}
    set_default_template(template)
    base = re.sub(r"\s·\s(Quotation|Tax Invoice|Delivery Note)$", "",
                  template.name).strip() or template.name
    count = 1
    for dt in ("quotation", "invoice", "delivery"):
        if dt == template.doc_type:
            continue
        tpl = create_html_template(
            template.company, actor, doc_type=dt,
            name=f"{base} · {_SET_LABELS[dt]}"[:80], design=design,
            description=template.description or "", origin=TemplateOrigin.CUSTOM)
        set_default_template(tpl)
        count += 1
    return count


def _is_pinned(template) -> bool:
    """True if any finalised document is pinned to a version of this template —
    deleting it would delete that version and silently change an issued document."""
    from .models import CommercialDocument, Quotation
    return (Quotation.objects.filter(template_version__template=template).exists()
            or CommercialDocument.objects.filter(template_version__template=template).exists())


@transaction.atomic
def delete_template(template, actor):
    """Permanently remove a template the company CREATED — one built in the visual
    builder or reconstructed from an upload. The shipped LulaWorks built-ins can't
    be deleted (archive to hide them). Also refused for the company default (set
    another first) and for any template an issued document is pinned to (archive
    instead) — so deleting can never break a finalised document."""
    if template.is_builtin:
        raise TemplateError("This is a built-in LulaWorks template — it can't be "
                            "deleted, but you can archive it to hide it.")
    if template.is_default:
        raise TemplateError("This is the default — set another template as the "
                            "default before deleting it.")
    if _is_pinned(template):
        raise TemplateError("An already-issued document uses this template, so it "
                            "can't be deleted — archive it instead.")
    template.delete()      # cascades its versions (none are pinned)


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
