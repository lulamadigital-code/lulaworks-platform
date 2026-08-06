"""Document Designer — templates, resolution, and config validation.

The guarantees these protect: a company can hold several looks per document
type and pick a default; a document can override it; and the resolver always
falls back to today's plain layout so a company with no templates is unaffected.
"""

from django.test import TestCase

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
            modern = DocumentTemplate.objects.get(company=c, doc_type="quotation",
                                                  name="Modern")
            dt.set_default_template(modern)
            modern.refresh_from_db()
            self.assertTrue(modern.is_default)
            self.assertEqual(DocumentTemplate.objects.filter(
                company=c, doc_type="quotation", is_default=True).count(), 1)


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
            mining = DocumentTemplate.objects.get(company=c, doc_type="quotation",
                                                  name="Mining")
            cfg = dt.effective_config(c, "quotation", override=mining)
            self.assertEqual(cfg["accent_color"], "#B9711A")
            self.assertTrue(cfg["show_project_reference"])

    def test_default_used_when_no_override(self):
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            corp = DocumentTemplate.objects.get(company=c, doc_type="invoice",
                                                name="Corporate")
            dt.set_default_template(corp)
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

    def test_quotation_renders_with_a_preset_template(self):
        from .pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            dt.seed_document_templates(c)
            mining = DocumentTemplate.objects.get(company=c, doc_type="quotation",
                                                  name="Mining")
            pdf = quotation_pdf_bytes(self._quote(c, template=mining))
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
