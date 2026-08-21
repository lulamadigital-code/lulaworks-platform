"""Method 3 — reconstruct an uploaded document's visual structure into a reusable
HTML template `design`.

Deterministic-first, like the rest of the platform: a PDF is read with pdfplumber
into layout FEATURES (logo position, brand colour, header style, which sections
are present, item columns), which become a `design` proposal + `warnings` for the
low-confidence findings. The AI orchestration layer then OPTIONALLY refines that
proposal — reasoning over the extracted features (never raw pixels, never business
data), metered against AI credits, and a no-op when credits/providers are absent
so the deterministic result always stands.

The AI identifies layout only. It never invents company, customer or financial
data — those are filled from real records at render time.
"""

from __future__ import annotations

from decimal import Decimal

from .document_templates import clean_design
from .models import DEFAULT_DESIGN, TEMPLATE_SECTION_KEYS

#: What the review screen quotes as the likely cost before running the AI step.
IMPORT_CREDIT_ESTIMATE = 5

#: Sections we always keep visible — the spine of any commercial document — so a
#: keyword miss never drops the letterhead or the items table.
_CORE_SECTIONS = {"letterhead", "document_meta", "parties", "items", "totals", "footer"}


# ── Deterministic analysis ─────────────────────────────────────────────────────

def analyse_document(file_path: str, doc_type: str):
    """Read a document's visual structure into (design, warnings, features).
    Never raises for a readable file — an image or unreadable upload degrades to a
    sensible default design plus a warning telling the user to adjust it."""
    lower = (file_path or "").lower()
    if lower.endswith(".pdf"):
        try:
            return _analyse_pdf(file_path, doc_type)
        except Exception as exc:       # noqa: BLE001 - never fail the import on a quirky PDF
            return (clean_design({}),
                    [_warn("layout", f"Couldn’t fully read this PDF ({exc}). A standard "
                           "layout was applied — adjust it in the builder.")],
                    {"kind": "pdf", "error": str(exc)[:200]})
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return (clean_design({}),
                [_warn("layout", "You uploaded an image, which carries no layout data. "
                       "LulaAI applied a standard layout — set your colours, logo and "
                       "sections in the builder.")],
                {"kind": "image"})
    return (clean_design({}),
            [_warn("layout", "This file type gives no layout information. A standard "
                   "layout was applied — adjust it in the builder.")],
            {"kind": "other"})


def _analyse_pdf(path: str, doc_type: str):
    import pdfplumber

    warnings, features = [], {}
    branding = dict(DEFAULT_DESIGN["branding"])

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        width, height = float(page.width), float(page.height)

        # Logo — the largest image sitting in the top third.
        top_images = [im for im in (page.images or []) if float(im.get("top", 0)) < height * 0.34]
        if top_images:
            biggest = max(top_images, key=lambda im: (float(im["x1"]) - float(im["x0"]))
                          * (float(im["bottom"]) - float(im["top"])))
            centre = (float(biggest["x0"]) + float(biggest["x1"])) / 2
            branding["logo_position"] = ("left" if centre < width * 0.4
                                         else "right" if centre > width * 0.6 else "center")
            features["logo_position"] = branding["logo_position"]
        else:
            warnings.append(_warn("logo", "Couldn’t find a logo in the document — "
                                  "add one in Company Profile or place it in the builder."))

        # Brand colour — the most saturated fill among the page's rectangles.
        accent = _dominant_colour(page.rects or [])
        if accent:
            branding["accent_color"] = accent
            features["accent_color"] = accent
        else:
            warnings.append(_warn("accent_color", "Couldn’t detect a brand colour — "
                                  "using a neutral default. Set yours in the builder."))

        # Header style — a wide filled bar across the top reads as a colour band.
        band = any(_rect_is_top_band(r, width, height) for r in (page.rects or []))
        branding["header_style"] = "band" if band else "plain"
        features["header_style"] = branding["header_style"]

        text = (page.extract_text() or "").lower()
        features["text_chars"] = len(text)
        present = _sections_from_text(text)
        columns = _columns_from_text(text, doc_type)

        # Tagline / header note — a short line near the top under the company name.
        header_note = _detect_header_note(page, width, height)

    for key in ("banking", "terms", "scope", "signature"):
        if key not in present:
            warnings.append(_warn(key, f"Couldn’t confirm a “{key}” section — it’s left "
                                  "off; switch it on in the builder if the document has one."))
    if header_note:
        features["header_note"] = header_note
        warnings.append(_warn("header_note", f"Detected a possible tagline — “{header_note}”. "
                              "It’s been added under the letterhead; remove it in the builder "
                              "if it isn’t yours."))

    sections = [{"key": k, "visible": (k in _CORE_SECTIONS or k in present)}
                for k in TEMPLATE_SECTION_KEYS]
    features["sections_present"] = sorted(present)
    design = clean_design({"branding": branding, "sections": sections, "columns": columns,
                           "header_note": header_note})
    return design, warnings, features


def _warn(field: str, message: str) -> dict:
    return {"field": field, "message": message}


#: Substrings that mark a top-region line as address/contact/statutory/title —
#: never a tagline. Conservative on purpose: better to miss a tagline than to lift
#: the customer's name or a reference into the template.
_NOT_TAGLINE = (
    "@", "tel", "cell", "fax", "phone", "vat", "reg", "www", ".co", "http",
    "p.o", "po box", "street", "road", " ave", "avenue", "suite", "floor",
    "quotation", "tax invoice", "invoice", "delivery note", "date", "no:",
    "number", "client", "bill to", "customer", "attention", "ref",
)


def _detect_header_note(page, width, height) -> str:
    """A conservative guess at the company tagline — a short alphabetic line in the
    top band that isn't the name (line 0), an address, contact, statutory line or
    the document title. Empty when nothing clearly qualifies; the caller warns the
    user to confirm whatever is found, and the AI step can refine it."""
    try:
        cropped = page.crop((0, 0, float(width), float(height) * 0.26))
        top = cropped.extract_text() or ""
    except Exception:                # noqa: BLE001 - a crop quirk must not fail the import
        return ""
    lines = [ln.strip() for ln in top.splitlines() if ln.strip()]
    for line in lines[1:6]:          # skip the first line (usually the company name)
        low = line.lower()
        if not (4 <= len(line) <= 60):
            continue
        if any(bad in low for bad in _NOT_TAGLINE):
            continue
        if sum(ch.isdigit() for ch in line) > len(line) * 0.25:
            continue
        if not any(ch.isalpha() for ch in line):
            continue
        return line
    return ""


def _rect_is_top_band(rect, width, height) -> bool:
    try:
        wide = (float(rect["x1"]) - float(rect["x0"])) > width * 0.6
        high = float(rect.get("top", height)) < height * 0.2
        return bool(wide and high and rect.get("non_stroking_color") is not None)
    except (KeyError, TypeError, ValueError):
        return False


def _dominant_colour(rects) -> str | None:
    """The most saturated, non-grey rectangle fill on the page → hex. None when
    nothing colourful is found (all black/white/grey)."""
    best, best_sat = None, 0.0
    for rect in rects:
        rgb = _to_rgb(rect.get("non_stroking_color"))
        if rgb is None:
            continue
        hi, lo = max(rgb), min(rgb)
        sat = hi - lo
        if hi < 0.12 or lo > 0.9 or sat < 0.12:      # black / white / grey → skip
            continue
        if sat > best_sat:
            best, best_sat = rgb, sat
    if best is None:
        return None
    return "#{:02X}{:02X}{:02X}".format(*(int(round(c * 255)) for c in best))


def _to_rgb(color):
    """pdfplumber colours come as a scalar (grey), a 3-tuple (RGB) or a 4-tuple
    (CMYK), each 0–1 or 0–255. Normalise to an (r,g,b) 0–1 tuple, or None."""
    if color is None:
        return None
    if isinstance(color, (int, float)):
        v = _norm(color)
        return (v, v, v)
    if isinstance(color, (list, tuple)) and color:
        vals = [_norm(c) for c in color]
        if len(vals) == 1:
            return (vals[0],) * 3
        if len(vals) == 3:
            return tuple(vals)
        if len(vals) == 4:
            c, m, y, k = vals
            return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _norm(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, x / 255 if x > 1 else x))


_SECTION_KEYWORDS = {
    "banking": ("bank", "account no", "account number", "branch code", "swift"),
    "terms": ("terms and conditions", "terms & conditions", "t&c"),
    "scope": ("scope of work", "scope:"),
    "signature": ("signature", "received in good", "signed", "authorised by"),
    "parties": ("bill to", "invoice to", "customer", "client"),
}


def _sections_from_text(text: str) -> set:
    present = set()
    for key, needles in _SECTION_KEYWORDS.items():
        if any(n in text for n in needles):
            present.add(key)
    return present


def _columns_from_text(text: str, doc_type: str) -> list:
    cols = ["item_no", "description"]
    if any(w in text for w in ("qty", "quantity")):
        cols.append("qty")
    if "unit" in text:
        cols.append("unit")
    if doc_type != "delivery":       # delivery notes never carry prices
        if any(w in text for w in ("unit price", "rate", "price")):
            cols.append("unit_price")
        if any(w in text for w in ("amount", "total", "line total")):
            cols.append("amount")
    return cols


# ── Optional AI enrichment (metered, graceful) ─────────────────────────────────

def enrich_design(company, user, features: dict, design: dict):
    """Refine the deterministic design through the AI orchestration layer, reasoning
    over the extracted FEATURES only. Returns (design, ai_used, credits_used).
    Any failure — no credits, no provider, bad output — returns the deterministic
    design unchanged with ai_used=False, so the import never depends on the AI."""
    import json

    from apps.ai_platform.gateway import (
        AllProvidersFailedError,
        InsufficientCreditsError,
        run_task,
    )

    prompt = _enrich_prompt(features, design)
    try:
        resp = run_task(company, user, task="template_layout_import", prompt=prompt,
                        prompt_name="template_layout_import")
    except (InsufficientCreditsError, AllProvidersFailedError):
        return design, False, Decimal("0")
    except Exception:                # noqa: BLE001 - AI is best-effort, never fatal
        return design, False, Decimal("0")

    refined = _parse_design(resp.text)
    if refined is None:
        return design, False, getattr(resp, "credits_used", Decimal("0"))
    try:
        cleaned = clean_design({**design, **refined})
    except Exception:                # noqa: BLE001 - reject unsafe AI output
        return design, False, getattr(resp, "credits_used", Decimal("0"))
    return cleaned, True, getattr(resp, "credits_used", Decimal("0"))


def _enrich_prompt(features: dict, design: dict) -> str:
    import json
    return (
        "You refine the LAYOUT of a business document template. You are given "
        "features extracted from a company's existing document and a draft design. "
        "Return ONLY a JSON object with the same shape as the draft "
        "(keys: branding{accent_color,secondary_color,font_family,logo_position,"
        "header_style}, sections[{key,visible}], columns[]). Correct the branding "
        "and which sections are visible to best match the features. Do NOT invent "
        "any company, customer, or financial data — layout only. If unsure, keep "
        "the draft value.\n\n"
        f"FEATURES:\n{json.dumps(features)[:2000]}\n\n"
        f"DRAFT DESIGN:\n{json.dumps(design)[:2000]}\n\n"
        "JSON:"
    )


def _parse_design(text: str):
    """Pull the first JSON object out of the model's reply. None on failure."""
    import json
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


# ── Vision enrichment: let a multimodal model SEE the page ─────────────────────

def _page_image_bytes(path: str, original_name: str) -> bytes | None:
    """Render the first page of the upload to PNG bytes for a vision model — a PDF
    via pdf2image (poppler), an uploaded image used directly. Downscaled to keep the
    request small. None when it can't be rendered (e.g. DOCX), so the caller falls
    back to the text-feature path."""
    low = (original_name or path or "").lower()
    try:
        if low.endswith(".pdf"):
            from pdf2image import convert_from_path
            pages = convert_from_path(path, dpi=110, first_page=1, last_page=1)
            if not pages:
                return None
            img = pages[0]
        elif low.endswith((".png", ".jpg", ".jpeg", ".webp")):
            from PIL import Image
            img = Image.open(path)
        else:
            return None
        import io
        img = img.convert("RGB")
        img.thumbnail((1000, 1400))           # cap the payload
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception:                # noqa: BLE001 - vision is best-effort
        return None


def _vision_prompt(doc_type: str) -> str:
    return (
        f"You are shown an image of a company's existing {doc_type} document. "
        "Pick the Lulaworks template settings whose VISUAL STYLE best matches it — "
        "the brand colour, header treatment, logo placement, and table/totals look. "
        "Identify STYLE ONLY. Never copy or output any company, customer, address, "
        "reference, or money value from the image.\n\n"
        "Return ONLY this JSON object:\n"
        "{\n"
        '  "branding": {\n'
        '    "accent_color": "#RRGGBB (the dominant brand colour)",\n'
        '    "secondary_color": "#RRGGBB or empty string",\n'
        '    "font_family": one of ["Helvetica","Arial","Times New Roman","Georgia","Courier New","Verdana","Trebuchet MS"],\n'
        '    "logo_position": "left" | "center" | "right",\n'
        '    "logo_size": "small" | "medium" | "large" | "xlarge",\n'
        '    "header_style": "band" | "plain" | "minimal" | "centered" | "split" | "sidebar" | "hero" | "ledger"\n'
        "  },\n"
        '  "table_style": "lines" | "striped" | "bordered" | "plain",\n'
        '  "totals_style": "plain" | "boxed" | "highlighted",\n'
        '  "section_title_style": "plain" | "bar" | "underline",\n'
        '  "footer_layout": "stacked" | "split"\n'
        "}\n\n"
        "How to choose:\n"
        "- header_style: a solid colour bar across the whole top = band; a coloured "
        "vertical column down the left = sidebar; a big centred document title = hero "
        "(or centered for a smaller centred one); a two-tone header = split; a thin "
        "rule under the letterhead = plain; almost no colour = minimal; a boxed "
        "reference panel = ledger.\n"
        "- table_style: zebra/alternating rows = striped; full borders around every "
        "cell = bordered; only horizontal rules = lines; no lines = plain.\n"
        "- totals_style: the grand total in a filled colour block = highlighted; in a "
        "bordered box = boxed; just a rule above it = plain.\n"
        "- section_title_style: headings in filled colour bars = bar; underlined "
        "headings = underline; plain coloured headings = plain.\n"
        "- logo_position/size: match where the logo sits and how large it is.\n"
        "JSON:"
    )


def _merge_design(design: dict, refined: dict) -> dict:
    """Layer the AI's style choices over the deterministic draft — branding merged
    key-by-key, top-level style knobs overridden, sections/columns kept from the
    deterministic pass (structure) unless the AI clearly returned them."""
    out = dict(design)
    b = dict(design.get("branding") or {})
    b.update({k: v for k, v in (refined.get("branding") or {}).items() if v not in (None, "")})
    out["branding"] = b
    for key in ("table_style", "totals_style", "section_title_style", "footer_layout"):
        if refined.get(key):
            out[key] = refined[key]
    return out


def vision_enrich(company, user, image_bytes: bytes, design: dict, doc_type: str):
    """Refine the design by having a multimodal model LOOK at the page. Same
    fail-open contract as enrich_design — any problem returns the deterministic
    design unchanged with ai_used=False."""
    from apps.ai_platform.gateway import (
        AllProvidersFailedError,
        InsufficientCreditsError,
        run_task,
    )
    try:
        resp = run_task(company, user, task="template_layout_import",
                        prompt=_vision_prompt(doc_type),
                        prompt_name="template_layout_vision",
                        images=[image_bytes], json_mode=True)
    except (InsufficientCreditsError, AllProvidersFailedError):
        return design, False, Decimal("0")
    except Exception:                # noqa: BLE001 - AI is best-effort, never fatal
        return design, False, Decimal("0")

    refined = _parse_design(resp.text)
    if refined is None:
        return design, False, getattr(resp, "credits_used", Decimal("0"))
    try:
        cleaned = clean_design(_merge_design(design, refined))
    except Exception:                # noqa: BLE001 - reject unsafe AI output
        return design, False, getattr(resp, "credits_used", Decimal("0"))
    return cleaned, True, getattr(resp, "credits_used", Decimal("0"))


# ── Faithful recreation: the model writes an HTML/CSS template of the page ──────

_TOKENS_HELP = (
    "Use ONLY these placeholders for DATA — never copy real text, names, numbers or "
    "money values:\n"
    "{{company.name}} {{company.address}} {{company.email}} {{company.phone}} "
    "{{company.website}} {{company.vat}} {{company.reg}} {{logo}} {{customer.name}} "
    "{{customer.address}} {{customer.vat}} {{contact.name}} {{contact.email}} "
    "{{contact.phone}} {{document.title}} {{document.reference}} {{document.date}} "
    "{{document.prepared_by}} {{document.po}} {{document.quotation_ref}} "
    "{{document.valid_until}} {{job.scope}} {{terms}} {{totals.subtotal}} "
    "{{totals.discount}} {{totals.vat_label}} {{totals.vat}} {{totals.total}} "
    "{{banking.bank_name}} {{banking.account_name}} {{banking.account_number}} "
    "{{banking.branch_code}}\n"
    "For item rows wrap EXACTLY ONE row in {{#items}} … {{/items}}. {{document.title}} "
    "already resolves to QUOTATION / TAX INVOICE / DELIVERY NOTE. {{logo}} becomes the "
    "company logo image.\n")
_LAYOUT_RULES = (
    "Rules: NO <script>, <img>, <link>, <style>, external URLs or fonts (styling goes "
    "in css; inline styles are fine). Placeholders only. Use NORMAL flow — flexbox and "
    "tables only, NEVER position absolute/fixed or negative margins. Keep within a "
    "~186mm-wide page, logo at most ~200px, every label and value clearly spaced, one "
    "A4 page.\n")


def _primary_prompt(doc_type: str) -> str:
    price = ("{{item.ordered}} {{item.delivered}} {{item.outstanding}} — NO prices"
             if doc_type == "delivery" else "{{item.unit_price}} {{item.amount}}")
    return (
        f"You are shown an image of a company's existing {doc_type}. Recreate its "
        "LAYOUT as an HTML+CSS template so a generated document looks like the "
        "original — reproduce the letterhead, logo placement, colours, the company and "
        "customer blocks, the item table columns and styling, totals, banking, terms "
        "and any sign-off.\n\n" + _TOKENS_HELP +
        f"Inside the item row use {{item.no}} {{item.description}} {{item.qty}} {{item.unit}} {price}.\n\n"
        + _LAYOUT_RULES +
        '\nReturn ONLY JSON: {"html":"<body html>","css":"<css>"}'
    )


def _run_ai(company, user, prompt, name, *, images=None, max_tokens=6000):
    from django.conf import settings

    from apps.ai_platform.gateway import (
        AllProvidersFailedError,
        InsufficientCreditsError,
        run_task,
    )
    try:
        resp = run_task(company, user, task="template_layout_import", prompt=prompt,
                        prompt_name=name, images=images, json_mode=True,
                        max_tokens=max_tokens, model=settings.AI_IMPORT_MODEL or "")
    except (InsufficientCreditsError, AllProvidersFailedError):
        return None, Decimal("0")
    except Exception:                # noqa: BLE001 - AI is best-effort, never fatal
        return None, Decimal("0")
    return resp, getattr(resp, "credits_used", Decimal("0"))


def _usable_html(v):
    return v[:60000] if (isinstance(v, str) and "{{" in v and len(v.strip()) >= 40) else None


def _gen_one_type(company, user, image_bytes, doc_type):
    """Recreate ONE document type from the uploaded image. Returns (doc_type, html,
    css, credits)."""
    resp, credits = _run_ai(company, user, _primary_prompt(doc_type),
                            f"template_html_{doc_type}", images=[image_bytes], max_tokens=5000)
    data = _parse_design(resp.text) if resp else None
    html = _usable_html((data or {}).get("html"))
    css = (data.get("css") or "")[:40000] if (data and isinstance(data.get("css"), str)) else ""
    return doc_type, html, css, credits


def vision_generate_all(company, user, image_bytes: bytes, primary_doc_type: str):
    """Recreate the uploaded page as a MATCHING SET — quotation, invoice, delivery
    note. Runs the three type-specific recreations IN PARALLEL (each anchored to the
    same source image, so they share the look) to roughly halve the wall-clock time
    vs generating them one after another. Returns (by_type, css_by_type, ai_used,
    credits); fails open to ({}, {}, False, …) when the uploaded type can't be made."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda dt: _gen_one_type(company, user, image_bytes, dt),
            ("quotation", "invoice", "delivery")))

    by_type, css_by_type, credits = {}, {}, Decimal("0")
    for dt, html, css, c in results:
        credits += c
        if html:
            by_type[dt] = html
            css_by_type[dt] = css
    if primary_doc_type not in by_type:
        return {}, {}, False, credits
    return by_type, css_by_type, True, credits


def _sibling_prompt(primary_type: str, needed: list, primary_html: str) -> str:
    want = ", ".join(f'"{t}_html"' for t in needed)
    return (
        f"Below is an approved HTML template for a {primary_type} (using "
        "{{placeholders}}). Produce the matching versions for the other document "
        "types so the company has a consistent SET. Keep the SAME visual style, CSS "
        "class names and structure — change ONLY what each type requires:\n"
        "- quotation & tax invoice: keep the price columns {{item.unit_price}} "
        "{{item.amount}} and the totals block; sign-off reads 'Quotation compiled by' "
        "or 'Invoice compiled by'.\n"
        "- delivery note: REMOVE every price — the item row becomes {{item.no}} "
        "{{item.description}} {{item.ordered}} {{item.delivered}} {{item.outstanding}} "
        "{{item.unit}}; REMOVE the totals block and banking; change the sign-off to a "
        "'Received in good order by' acknowledgement.\n"
        "{{document.title}} already gives the correct heading. Same placeholder and "
        "layout rules (no script/img/link, normal flow, single page).\n\n"
        f"Return ONLY JSON mapping each needed type to its html body: {{{want}}}.\n\n"
        f"INPUT TEMPLATE ({primary_type}):\n{primary_html}"
    )


def create_siblings_from_template(template, user):
    """After the company APPROVES an imported template for one document type, create
    the matching templates for the OTHER two types by adapting the approved HTML —
    same CSS/style — and set each as its type's default. Returns how many were made.
    Runs in the background (see tasks.generate_import_siblings_task)."""
    import re

    from .document_templates import create_html_template, set_default_template
    from .models import DocumentTemplate, TemplateOrigin

    ver = template.current_version
    if not (ver and ver.html):
        return 0
    primary_type = template.doc_type
    needed = [t for t in ("quotation", "invoice", "delivery")
              if t != primary_type
              and not DocumentTemplate.objects.filter(
                  company=template.company, doc_type=t, is_default=True,
                  origin=TemplateOrigin.IMPORTED, archived_at__isnull=True).exists()]
    if not needed:
        return 0
    resp, _ = _run_ai(template.company, user,
                      _sibling_prompt(primary_type, needed, ver.html),
                      "template_html_siblings", max_tokens=9000)
    data = _parse_design(resp.text) if resp else None
    if not data:
        return 0
    base = re.sub(r"\s·\s(Quotation|Tax Invoice|Delivery Note)$", "",
                  template.name).strip() or template.name
    made = 0
    for t in needed:
        h = _usable_html(data.get(f"{t}_html") or data.get(t))
        if not h:
            continue
        tpl = create_html_template(
            template.company, user, doc_type=t,
            name=f"{base} · {_TYPE_LABELS[t]}"[:80], design={}, html=h,
            css=ver.css or "", description="Matching set from an imported document",
            origin=TemplateOrigin.IMPORTED)
        set_default_template(tpl)
        made += 1
    return made


# ── Orchestration: analyse an upload, then save the approved design ─────────────

def _local_path(template_import) -> str:
    """A filesystem path pdfplumber can open. Uses the storage path when local,
    else streams the upload to a temp file."""
    import tempfile
    field = template_import.source_file
    try:
        return field.path
    except (NotImplementedError, ValueError):
        suffix = "." + (template_import.original_name.rsplit(".", 1)[-1] or "pdf")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        field.open("rb")
        tmp.write(field.read())
        tmp.close()
        return tmp.name


def run_import(template_import, user):
    """Analyse the uploaded document (deterministic + optional AI) and store the
    proposed design, warnings and features on the import, ready for review."""
    ti = template_import
    by_type, css_by_type = {}, {}
    try:
        path = _local_path(ti)
        design, warnings, features = analyse_document(path, ti.doc_type)
        image = _page_image_bytes(path, ti.original_name)
        if image is not None:
            # Recreate ONLY the uploaded document type now (one AI call → fast
            # review). The matching quotation/invoice/delivery note are created
            # afterwards, once the user approves this one (see save_as_template).
            _, html, css, credits = _gen_one_type(ti.company, user, image, ti.doc_type)
            ai_used = bool(html)
            if html:
                by_type = {ti.doc_type: html}
                css_by_type = {ti.doc_type: css}
            else:
                # Recreation unavailable → match the closest structured design.
                design, ai_used, credits = vision_enrich(
                    ti.company, user, image, design, ti.doc_type)
        else:
            design, ai_used, credits = enrich_design(ti.company, user, features, design)
    except Exception as exc:          # noqa: BLE001 - surface, never crash the request
        ti.status = ti.Status.FAILED
        ti.error = str(exc)[:255]
        ti.updated_by = user
        ti.save(update_fields=["status", "error", "updated_by", "updated_at"])
        return ti

    # Carry the recreated set (html + its own css per type) on `features` (no schema
    # change needed); the structured `design` remains as an editable fallback.
    features["_raw_by_type"] = by_type
    features["_raw_css_by_type"] = css_by_type

    # When the AI set the branding, the deterministic "couldn't detect colour/logo"
    # notes are stale — drop them. Also drop the deterministic tagline guess: the
    # vision model handles branding and the text-scan can lift a stray line (a
    # letterhead word, or nothing) that isn't really a tagline.
    if ai_used:
        warnings = [w for w in warnings
                    if w.get("field") not in ("accent_color", "logo", "header_note")]
        design = {**design, "header_note": ""}

    ti.design = design
    ti.warnings = warnings
    ti.features = features
    ti.ai_used = ai_used
    ti.credits_used = credits
    ti.status = ti.Status.READY
    ti.error = ""
    ti.updated_by = user
    ti.save()
    return ti


#: Human labels for the per-type templates a set creates.
_TYPE_LABELS = {"quotation": "Quotation", "invoice": "Tax Invoice", "delivery": "Delivery Note"}


def save_as_template(template_import, user, *, name="", design=None):
    """Turn an approved import into company HTML templates (origin=imported), and set
    them as the company's defaults. When the AI produced a matching SET, ALL three
    document types (quotation / invoice / delivery note) are created in one shared
    style; otherwise a single template for the uploaded type. Returns the template
    for the uploaded document type."""
    from .document_templates import create_html_template, set_default_template
    from .models import TemplateOrigin

    ti = template_import
    feats = ti.features or {}
    by_type = feats.get("_raw_by_type") or {}
    css_by_type = feats.get("_raw_css_by_type") or {}
    base = (name or "").strip() or "Imported"
    primary = None

    if by_type:
        for dt in ("quotation", "invoice", "delivery"):
            html = by_type.get(dt)
            if not html:
                continue
            tpl = create_html_template(
                ti.company, user, doc_type=dt, name=f"{base} · {_TYPE_LABELS[dt]}"[:80],
                design={}, html=html, css=css_by_type.get(dt, ""),
                description="Recreated from an uploaded document",
                origin=TemplateOrigin.IMPORTED)
            set_default_template(tpl)                 # make it the default for its type
            if dt == ti.doc_type:
                primary = tpl
        primary = primary or tpl
    else:
        tpl = create_html_template(
            ti.company, user, doc_type=ti.doc_type,
            name=f"{base} · {_TYPE_LABELS.get(ti.doc_type, 'Document')}"[:80],
            design=design if design is not None else ti.design,
            description="Reconstructed from an uploaded document",
            origin=TemplateOrigin.IMPORTED)
        set_default_template(tpl)
        primary = tpl

    ti.saved_template = primary
    ti.status = ti.Status.SAVED
    ti.updated_by = user
    ti.save(update_fields=["saved_template", "status", "updated_by", "updated_at"])
    return primary
