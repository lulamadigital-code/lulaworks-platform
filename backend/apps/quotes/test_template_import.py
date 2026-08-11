"""Method 3 — import an existing document → reconstructed template.

What matters: the analyser reads real structure from a PDF, degrades honestly for
inputs it can't read, the AI step is a no-op without credits (deterministic result
stands), and an approved import becomes an HTML template the engine can render.
"""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company

from . import document_templates as dt
from . import template_import as ti_mod
from .models import TemplateImport


def make_company(name="Lulama"):
    return Company.objects.create(name=name)


def _quotation_pdf(company):
    from .models import Quotation
    from .pdf import quotation_pdf_bytes
    q = Quotation.objects.create(company=company, number="QT-IMP", client_name="Sibanye",
                                 site="Plant 1")
    q.lines.create(company=company, position=1, description="Pump overhaul", qty=2,
                   unit="ea", unit_cost=1000)
    return quotation_pdf_bytes(q)


class AnalyserTests(TestCase):
    def test_analyses_structure_from_a_real_pdf(self):
        import os
        import tempfile
        c = make_company()
        with tenant_scope(c.id):
            pdf = _quotation_pdf(c)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(pdf)
        tmp.close()
        try:
            design, warnings, features = ti_mod.analyse_document(tmp.name, "quotation")
        finally:
            os.unlink(tmp.name)

        # A complete, valid design came back.
        self.assertIn("branding", design)
        self.assertIn("sections", design)
        self.assertTrue(design["columns"])
        # The classic quotation shows a totals block and a banking box → detected.
        present = set(features.get("sections_present", []))
        self.assertIn("banking", present)
        # An accent colour was picked up from the coloured table header.
        self.assertTrue(design["branding"]["accent_color"].startswith("#"))

    def test_detects_a_tagline_header_note(self):
        import os
        import tempfile
        from .models import Quotation
        from .pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            tpl = dt.create_template(c, None, doc_type="quotation", name="Branded",
                                     base_layout="modern",
                                     config={"header_note": "Engineering & Technical Services"})
            q = Quotation.objects.create(company=c, number="QT-HN", client_name="Anglo",
                                         site="Site", template=tpl)
            q.lines.create(company=c, position=1, description="Item", qty=1,
                           unit="ea", unit_cost=100)
            pdf = quotation_pdf_bytes(q)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(pdf)
        tmp.close()
        try:
            design, warnings, features = ti_mod.analyse_document(tmp.name, "quotation")
        finally:
            os.unlink(tmp.name)
        self.assertIn("Engineering", design.get("header_note", ""))
        self.assertTrue(any(w["field"] == "header_note" for w in warnings))

    def test_image_upload_degrades_with_a_warning(self):
        design, warnings, features = ti_mod.analyse_document("logo.png", "quotation")
        self.assertIn("branding", design)
        self.assertEqual(features["kind"], "image")
        self.assertTrue(warnings)          # tells the user to adjust in the builder

    def test_unreadable_pdf_does_not_crash(self):
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(b"not a real pdf")
        tmp.close()
        design, warnings, features = ti_mod.analyse_document(tmp.name, "quotation")
        self.assertIn("branding", design)   # fell back safely
        self.assertTrue(warnings)


class EnrichmentTests(TestCase):
    def test_enrich_is_a_noop_without_credits(self):
        c = make_company()
        with tenant_scope(c.id):
            design = dt.clean_design({})
            out, used, credits = ti_mod.enrich_design(c, None, {"kind": "pdf"}, design)
            self.assertFalse(used)                 # no provider/credits → skipped
            self.assertEqual(credits, Decimal("0"))
            self.assertEqual(out, design)          # deterministic result stands


class ImportFlowTests(TestCase):
    def test_import_then_save_produces_an_imported_html_template(self):
        c = make_company()
        with tenant_scope(c.id):
            pdf = _quotation_pdf(c)
            ti = TemplateImport.objects.create(
                company=c, doc_type="quotation", original_name="abc.pdf",
                source_file=SimpleUploadedFile("abc.pdf", pdf, content_type="application/pdf"))
            ti_mod.run_import(ti, None)
            ti.refresh_from_db()
            self.assertEqual(ti.status, TemplateImport.Status.READY)
            self.assertTrue(ti.design)

            tpl = ti_mod.save_as_template(ti, None, name="ABC Engineering")
            self.assertEqual(tpl.engine, "html")
            self.assertEqual(tpl.origin, "imported")
            self.assertTrue(tpl.current_version.design)
            ti.refresh_from_db()
            self.assertEqual(ti.status, TemplateImport.Status.SAVED)
            self.assertEqual(ti.saved_template_id, tpl.id)

    def test_reconstructed_design_previews_as_a_pdf(self):
        from .html_render import render_design_preview_pdf
        c = make_company()
        with tenant_scope(c.id):
            pdf = render_design_preview_pdf(c, "quotation", dt.clean_design({}))
            self.assertTrue(pdf.startswith(b"%PDF"))


class ImportIsolationTests(TestCase):
    def test_imports_never_leak_between_tenants(self):
        a, b = make_company(), make_company()
        with tenant_scope(b.id):
            TemplateImport.objects.create(company=b, doc_type="quotation",
                                          original_name="b.pdf",
                                          source_file=SimpleUploadedFile("b.pdf", b"x"))
        with tenant_scope(a.id):
            self.assertEqual(TemplateImport.objects.count(), 0)
