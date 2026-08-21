"""Document Designer — templates, resolution, and config validation.

The guarantees these protect: a company can hold several looks per document
type and pick a default; a document can override it; and the resolver always
falls back to today's plain layout so a company with no templates is unaffected.
"""

from django.test import SimpleTestCase, TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company

from . import document_templates as dt
from .models import DEFAULT_CONFIG, DocumentTemplate


def make_company(name="Lulama"):
    return Company.objects.create(name=name)


class SeedAndDefaultTests(TestCase):
    def test_seed_creates_builtins_once(self):
        c = make_company()
        with tenant_scope(c.id):
            n = dt.seed_document_templates(c)
            self.assertGreater(n, 0)
            # Idempotent — a second call adds nothing.
            self.assertEqual(dt.seed_document_templates(c), 0)
            # One default per document type.
            for doc_type in ("quotation", "invoice", "delivery"):
                defaults = DocumentTemplate.objects.filter(
                    company=c, doc_type=doc_type, is_default=True)
                self.assertEqual(defaults.count(), 1, doc_type)

    def test_set_default_moves_the_flag(self):
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            elevate = DocumentTemplate.objects.get(company=c, doc_type="quotation",
                                                   name="Elevate")
            dt.set_default_template(elevate)
            elevate.refresh_from_db()
            self.assertTrue(elevate.is_default)
            self.assertEqual(DocumentTemplate.objects.filter(
                company=c, doc_type="quotation", is_default=True).count(), 1)

    def test_default_family_is_horizon(self):
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            for doc_type in ("quotation", "invoice", "delivery"):
                d = DocumentTemplate.objects.get(company=c, doc_type=doc_type,
                                                 is_default=True)
                self.assertEqual((d.name, d.family), ("Horizon", "horizon"))


class ResolutionTests(TestCase):
    def test_effective_config_is_full_even_with_no_templates(self):
        """A company with no templates must render exactly as before — the
        resolver returns the complete default switch-set, not a partial dict."""
        c = make_company()
        with tenant_scope(c.id):
            cfg = dt.effective_config(c, "quotation")
            for key in DEFAULT_CONFIG:
                self.assertIn(key, cfg)
            self.assertEqual(cfg["_base_layout"], "classic")

    def test_override_beats_company_default(self):
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            override = dt.create_template(
                c, None, doc_type="quotation", name="Bespoke override",
                base_layout="modern",
                config={"accent_color": "#B9711A", "show_project_reference": True})
            cfg = dt.effective_config(c, "quotation", override=override)
            self.assertEqual(cfg["accent_color"], "#B9711A")
            self.assertTrue(cfg["show_project_reference"])

    def test_default_used_when_no_override(self):
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            serif = dt.create_template(
                c, None, doc_type="invoice", name="Bespoke serif",
                base_layout="classic", config={"font": "Times-Roman"})
            dt.set_default_template(serif)
            cfg = dt.effective_config(c, "invoice")   # no override
            self.assertEqual(cfg["font"], "Times-Roman")


class ConfigValidationTests(TestCase):
    def test_bad_colour_is_rejected(self):
        with self.assertRaises(dt.TemplateError):
            dt.clean_config({"accent_color": "red"})

    def test_unknown_font_is_rejected(self):
        with self.assertRaises(dt.TemplateError):
            dt.clean_config({"font": "Comic Sans"})

    def test_unknown_keys_are_dropped_not_stored(self):
        cleaned = dt.clean_config({"evil": "x", "show_banking": "0"})
        self.assertNotIn("evil", cleaned)
        self.assertFalse(cleaned["show_banking"])   # coerced "0" → False

    def test_booleans_are_coerced_from_form_strings(self):
        cleaned = dt.clean_config({"show_qr": "on", "page_numbering": "false"})
        self.assertTrue(cleaned["show_qr"])
        self.assertFalse(cleaned["page_numbering"])

    def test_create_and_update_go_through_validation(self):
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_template(c, None, doc_type="quotation",
                                     name="House Style", base_layout="modern",
                                     config={"accent_color": "#123456"})
            self.assertTrue(tpl.is_default)   # first of its type
            self.assertEqual(tpl.config["accent_color"], "#123456")
            with self.assertRaises(dt.TemplateError):
                dt.update_template(tpl, None, config={"accent_color": "nope"})


class PdfRenderingTests(TestCase):
    """The payoff: the PDF builders honour a template, and a company with no
    template still renders exactly as before (a valid PDF, no exceptions)."""

    def _quote(self, company, **extra):
        from .models import Quotation
        return Quotation.objects.create(company=company, number="QT-1",
                                        client_name="Harmony", site="Welkom",
                                        **extra)

    def test_quotation_renders_without_a_template(self):
        from .pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            pdf = quotation_pdf_bytes(self._quote(c))
            self.assertTrue(pdf.startswith(b"%PDF"))

    def test_quotation_renders_with_a_family_template(self):
        from .pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            forge = DocumentTemplate.objects.get(company=c, doc_type="quotation",
                                                 name="Forge")
            pdf = quotation_pdf_bytes(self._quote(c, template=forge))
            self.assertTrue(pdf.startswith(b"%PDF"))

    def test_template_toggles_and_watermark_render(self):
        """A template that hides banking + signature and stamps a watermark must
        still produce a valid document."""
        from .pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_template(
                c, None, doc_type="quotation", name="Draft look",
                base_layout="compact",
                config={"show_banking": False, "show_signature": False,
                        "show_watermark": True, "watermark_text": "DRAFT",
                        "footer_note": "Confidential", "font": "Times-Roman"})
            pdf = quotation_pdf_bytes(self._quote(c, template=tpl))
            self.assertTrue(pdf.startswith(b"%PDF"))


class LetterheadLogoPositionTests(SimpleTestCase):
    """logo_position must mean what it says. Asserts the letterhead layout so a
    future refactor can't silently invert it again (the bug this fixes: 'left'
    used to put the logo on the RIGHT)."""

    def _table(self, position):
        from .pdf import _letterhead_table
        ident = ["IDENT-SENTINEL"]                 # identity by object identity
        table = _letterhead_table({"logo": None}, ident, {"logo_position": position})
        return table, ident

    def test_left_puts_the_logo_on_the_left(self):
        table, ident = self._table("left")
        # One row, two columns; identity is the RIGHT cell → logo sits left.
        self.assertIs(table._cellvalues[0][1], ident)

    def test_right_puts_the_logo_on_the_right(self):
        table, ident = self._table("right")
        # Identity is the LEFT cell → logo sits right (the classic default).
        self.assertIs(table._cellvalues[0][0], ident)

    def test_center_stacks_logo_above_identity(self):
        table, ident = self._table("center")
        self.assertEqual(len(table._cellvalues), 2)   # logo row, then identity row
        self.assertIs(table._cellvalues[1][0], ident)

    def test_default_when_unset_is_logo_right(self):
        from .pdf import _letterhead_table
        ident = ["IDENT-SENTINEL"]
        table = _letterhead_table({"logo": None}, ident, {})   # no logo_position
        self.assertIs(table._cellvalues[0][0], ident)          # identity left


class FamilyTests(TestCase):
    """The twelve original Lulaworks families: every family is seeded across all
    three document types as an HTML-engine template, they render valid documents,
    and the built-in name guard is an allowlist (no borrowed product names)."""

    def test_every_family_is_seeded_for_all_doc_types(self):
        from .models import TEMPLATE_FAMILIES
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            for key, name, _desc, _tags, _design in TEMPLATE_FAMILIES:
                for doc_type in ("quotation", "invoice", "delivery"):
                    tpl = DocumentTemplate.objects.filter(
                        company=c, doc_type=doc_type, name=name, family=key).first()
                    self.assertIsNotNone(tpl, f"{name}/{doc_type}")
                    self.assertEqual(tpl.engine, "html")
                    self.assertTrue(tpl.is_builtin)

    def test_name_guard_is_an_allowlist_for_builtins(self):
        # Any name that is not one of the twelve families is refused for a built-in
        # (this makes it impossible to ship a third-party product name), but a
        # customer may still use any name on their OWN custom template.
        for bad in ("Anything Else", "My Own Style", "Nonsense"):
            with self.assertRaises(dt.TemplateError):
                dt.assert_allowed_template_name(bad, is_builtin=True)
        dt.assert_allowed_template_name("Anything Else", is_builtin=False)  # no raise
        for family in ("Horizon", "Elevate", "Forge", "Canvas"):
            dt.assert_allowed_template_name(family, is_builtin=True)  # no raise


class LetterheadBandTests(SimpleTestCase):
    """The 'modern' base layout wraps the identity in a coloured band; classic
    and compact do not — that structural difference is the whole point."""

    def test_modern_wraps_identity_in_a_band(self):
        from reportlab.lib import colors
        from reportlab.platypus import Table
        from .pdf import _letterhead_table
        band = _letterhead_table({"logo": None}, ["IDENT"],
                                 {"_base_layout": "modern", "logo_position": "left"},
                                 brand=colors.HexColor("#13B5EA"))
        # A single wrapper cell holding the inner identity table = the band.
        self.assertEqual(len(band._cellvalues), 1)
        self.assertIsInstance(band._cellvalues[0][0], Table)

    def test_classic_has_no_band_wrapper(self):
        from .pdf import _letterhead_table
        ident = ["IDENT"]
        tbl = _letterhead_table({"logo": None}, ident,
                                {"_base_layout": "classic", "logo_position": "right"})
        self.assertIs(tbl._cellvalues[0][0], ident)   # identity sits directly


class SyncBuiltinsTests(TestCase):
    """sync_builtin_templates tops up an already-seeded company with newly shipped
    built-ins, without duplicating or disturbing what it already has."""

    def test_sync_adds_only_the_missing_and_keeps_the_default(self):
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            # Simulate a company seeded before a family shipped.
            DocumentTemplate.objects.filter(name="Vector").delete()
            default_before = DocumentTemplate.objects.get(
                company=c, doc_type="quotation", is_default=True).name

            added = dt.sync_builtin_templates(c)
            self.assertEqual(added, 3)   # Vector for all 3 doc types
            self.assertEqual(dt.sync_builtin_templates(c), 0)   # idempotent

            # The company's chosen default is untouched, still exactly one per type.
            self.assertEqual(DocumentTemplate.objects.get(
                company=c, doc_type="quotation", is_default=True).name, default_before)
            for doc_type in ("quotation", "invoice", "delivery"):
                self.assertEqual(DocumentTemplate.objects.filter(
                    company=c, doc_type=doc_type, is_default=True).count(), 1)


class TemplateVersioningTests(TestCase):
    """Editing records immutable versions; a finalised document is pinned to the
    version it was issued with; archive/restore; and document-type validation."""

    def test_edit_records_a_new_version(self):
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_template(c, None, doc_type="quotation", name="House",
                                     base_layout="modern",
                                     config={"accent_color": "#111111"})
            self.assertEqual(tpl.versions.count(), 1)
            first = tpl.current_version_id
            dt.update_template(tpl, None, config={"accent_color": "#222222"})
            tpl.refresh_from_db()
            self.assertEqual(tpl.versions.count(), 2)
            self.assertNotEqual(tpl.current_version_id, first)

    def test_finalised_document_is_frozen_against_later_edits(self):
        from apps.quotes.models import Quotation
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_template(c, None, doc_type="quotation", name="House",
                                     base_layout="modern",
                                     config={"accent_color": "#111111"})
            dt.set_default_template(tpl)
            q = Quotation.objects.create(company=c, number="QT-1",
                                         client_name="Sibanye", template=tpl)
            dt.pin_template_version(q, "quotation")
            q.refresh_from_db()
            self.assertIsNotNone(q.template_version_id)

            # The template's colour changes after the quotation was issued.
            dt.update_template(tpl, None, config={"accent_color": "#999999"})

            # The issued quotation still renders with the ORIGINAL colour…
            self.assertEqual(dt.effective_config_for(q, "quotation")["accent_color"],
                             "#111111")
            # …while a brand-new quotation picks up the new one.
            self.assertEqual(dt.effective_config(c, "quotation")["accent_color"],
                             "#999999")

    def test_archive_hides_from_the_picker_and_restore_brings_it_back(self):
        c = make_company()
        with tenant_scope(c.id):
            dt.create_template(c, None, doc_type="quotation", name="Keep",
                               base_layout="classic")            # becomes default
            extra = dt.create_template(c, None, doc_type="quotation", name="Extra",
                                       base_layout="modern")
            dt.archive_template(extra, None)
            self.assertNotIn("Extra", [t.name for t in dt.templates_for(c, "quotation")])
            self.assertIn("Extra", [t.name for t in
                                    dt.templates_for(c, "quotation", include_archived=True)])
            dt.restore_template(extra, None)
            self.assertIn("Extra", [t.name for t in dt.templates_for(c, "quotation")])

    def test_the_default_cannot_be_archived(self):
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_template(c, None, doc_type="quotation", name="Only",
                                     base_layout="classic")
            with self.assertRaises(dt.TemplateError):
                dt.archive_template(tpl, None)

    def test_a_wrong_type_override_is_ignored(self):
        from apps.quotes.models import Quotation
        c = make_company()
        with tenant_scope(c.id):
            inv = dt.create_template(c, None, doc_type="invoice", name="Inv",
                                     base_layout="modern",
                                     config={"accent_color": "#abcdef"})
            q = Quotation.objects.create(company=c, number="QT-2",
                                         client_name="X", template=inv)
            # An invoice template must never render a quotation.
            self.assertIsNone(dt.resolve_template(c, "quotation", override=q.template))

    def test_duplicate_is_a_non_default_custom_copy(self):
        c = make_company()
        with tenant_scope(c.id):
            src = dt.create_template(c, None, doc_type="quotation", name="Src",
                                     base_layout="modern")
            copy = dt.duplicate_template(src, None)
            self.assertFalse(copy.is_default)
            self.assertEqual(copy.origin, "custom")
            self.assertEqual(copy.versions.count(), 1)


class HtmlEngineTests(TestCase):
    """The HTML engine (Method 2): design validation, section show/hide/reorder,
    dispatch from the shared PDF entry points, and per-version immutability."""

    def _quote(self, c, template=None):
        from .models import Quotation
        q = Quotation.objects.create(company=c, number="QT-H1", client_name="Sibanye",
                                     site="Plant 1", template=template)
        q.lines.create(company=c, position=1, description="Pump overhaul", qty=1,
                       unit="ea", unit_cost=1000)
        return q

    def test_clean_design_validates_and_completes(self):
        from .models import TEMPLATE_SECTION_KEYS
        d = dt.clean_design({
            "branding": {"accent_color": "#123456", "font_family": "Arial"},
            "sections": [{"key": "items", "visible": False}, {"key": "bogus"}],
            "columns": ["description", "qty", "evil"]})
        self.assertEqual(d["branding"]["accent_color"], "#123456")
        # Unknown section dropped; every known section present exactly once.
        keys = [s["key"] for s in d["sections"]]
        self.assertEqual(sorted(keys), sorted(TEMPLATE_SECTION_KEYS))
        self.assertEqual(len(keys), len(set(keys)))
        items = next(s for s in d["sections"] if s["key"] == "items")
        self.assertFalse(items["visible"])
        self.assertEqual(d["columns"], ["description", "qty"])   # 'evil' dropped

    def test_clean_design_rejects_bad_colour_and_font(self):
        with self.assertRaises(dt.TemplateError):
            dt.clean_design({"branding": {"accent_color": "red"}})
        with self.assertRaises(dt.TemplateError):
            dt.clean_design({"branding": {"font_family": "Comic Sans"}})

    def test_design_hides_a_section(self):
        from .html_render import build_context, design_to_html
        c = make_company()
        with tenant_scope(c.id):
            ctx = build_context(self._quote(c), "quotation")
            shown = design_to_html(dt.clean_design(
                {"sections": [{"key": "document_meta", "visible": True}]}), ctx)
            hidden = design_to_html(dt.clean_design(
                {"sections": [{"key": "document_meta", "visible": False}]}), ctx)
            self.assertIn("QUOTATION", shown)          # the title block…
            self.assertNotIn("QUOTATION", hidden)      # …gone when hidden

    def test_html_template_dispatches_and_renders_a_pdf(self):
        from .pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_html_template(c, None, doc_type="quotation", name="Mine",
                                          design={"branding": {"accent_color": "#0A6ED1"}})
            dt.set_default_template(tpl)
            pdf = quotation_pdf_bytes(self._quote(c))   # engine=html → WeasyPrint
            self.assertTrue(pdf.startswith(b"%PDF"))

    def test_create_html_template_is_html_engine_with_a_design(self):
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_html_template(c, None, doc_type="invoice", name="Inv")
            self.assertEqual(tpl.engine, "html")
            self.assertEqual(tpl.origin, "custom")
            self.assertTrue(tpl.current_version.design)   # design captured on v1

    def test_pinned_html_version_is_immutable(self):
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_html_template(c, None, doc_type="quotation", name="Mine",
                                          design={"branding": {"accent_color": "#111111"}})
            dt.set_default_template(tpl)
            q = self._quote(c, template=tpl)
            dt.pin_template_version(q, "quotation")
            q.refresh_from_db()
            dt.update_html_design(tpl, None, design={"branding": {"accent_color": "#999999"}})
            engine, spec = dt.resolve_render(q, "quotation")
            self.assertEqual(engine, "html")
            # resolve_render now returns a render spec {design, html, css}.
            self.assertEqual(spec["design"]["branding"]["accent_color"], "#111111")  # frozen


class StructuralLooksTests(TestCase):
    """The distinct HTML 'looks' are genuinely different structures, and each
    structural knob renders a valid document."""

    def _quote(self, company):
        from .models import Quotation
        q = Quotation.objects.create(company=company, number="QT-L", client_name="X", site="S")
        q.lines.create(company=company, position=1, description="Item", qty=2,
                       unit="ea", unit_cost=500)
        return q

    def test_all_families_are_seeded_as_html_templates(self):
        from .models import TEMPLATE_FAMILIES, DocumentTemplate
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            for _key, name, _desc, _tags, _design in TEMPLATE_FAMILIES:
                for doc_type in ("quotation", "invoice", "delivery"):
                    tpl = DocumentTemplate.objects.filter(
                        company=c, doc_type=doc_type, name=name).first()
                    self.assertIsNotNone(tpl, f"{name}/{doc_type}")
                    self.assertEqual(tpl.engine, "html")
                    self.assertTrue(tpl.current_version.design)

    def test_each_family_renders_a_valid_pdf(self):
        from .models import TEMPLATE_FAMILIES, DocumentTemplate
        from .pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            q = self._quote(c)
            for _key, name, _desc, _tags, _design in TEMPLATE_FAMILIES:
                tpl = DocumentTemplate.objects.get(company=c, doc_type="quotation", name=name)
                q.template = tpl
                pdf = quotation_pdf_bytes(q)
                self.assertTrue(pdf.startswith(b"%PDF"), name)

    def test_sidebar_produces_a_two_column_layout(self):
        from .html_render import design_to_html, sample_context
        c = make_company()
        with tenant_scope(c.id):
            html = design_to_html(
                dt.clean_design({"branding": {"header_style": "sidebar"}}),
                sample_context(c, "quotation"))
        self.assertIn("sidebar-layout", html)
        self.assertIn("class='rail'", html)

    def test_clean_design_rejects_unknown_style_knobs(self):
        with self.assertRaises(dt.TemplateError):
            dt.clean_design({"table_style": "rainbow"})
        with self.assertRaises(dt.TemplateError):
            dt.clean_design({"totals_style": "sparkle"})
