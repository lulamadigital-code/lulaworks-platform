"""Document-type correctness — the capability layer that keeps business meaning
independent of the visual template.

The guarantees these lock in:
  • a QUOTATION never shows a delivery acknowledgement or delivered/outstanding qty
  • a TAX INVOICE never shows a delivery acknowledgement
  • a DELIVERY NOTE never shows prices, and always shows ordered/delivered/outstanding
across EVERY template family, whatever the template's design happens to contain.
"""

from django.test import SimpleTestCase, TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company

from . import document_capabilities as caps
from . import document_templates as dt
from .html_render import design_to_html, sample_context
from .models import TEMPLATE_FAMILIES


def make_company(name="CapCo"):
    return Company.objects.create(name=name)


class CapabilityRulesTests(SimpleTestCase):
    def test_allowed_sections_per_type(self):
        self.assertIn("signature", caps.allowed_sections("quotation"))
        self.assertIn("signature", caps.allowed_sections("invoice"))  # compiled-by
        self.assertNotIn("totals", caps.allowed_sections("delivery"))    # no prices
        self.assertNotIn("banking", caps.allowed_sections("delivery"))

    def test_signoff_and_price_modes(self):
        # Quotation and invoice both carry a supplier "compiled by" sign-off; a
        # delivery note carries a delivery acknowledgement. None is a customer
        # counter-sign or, for invoice/quotation, a delivery receipt.
        self.assertEqual(caps.signoff_mode("quotation"), caps.SIGNOFF_COMPILED)
        self.assertEqual(caps.signoff_mode("invoice"), caps.SIGNOFF_COMPILED)
        self.assertEqual(caps.signoff_mode("delivery"), caps.SIGNOFF_DELIVERY)
        self.assertTrue(caps.allows_prices("quotation"))
        self.assertTrue(caps.allows_prices("invoice"))
        self.assertFalse(caps.allows_prices("delivery"))
        self.assertEqual(caps.item_mode("delivery"), caps.ITEMS_DELIVERY)

    def test_filter_sections_drops_forbidden(self):
        # A delivery note can never be given totals or banking, whatever a template
        # asks for (it's quantity-based and nothing is paid against it).
        dn = caps.filter_sections("delivery",
                                  ["letterhead", "items", "totals", "banking", "signature"])
        self.assertNotIn("totals", dn)
        self.assertNotIn("banking", dn)
        self.assertIn("signature", dn)      # delivery keeps its acknowledgement

    def test_validate_flags_missing_and_prohibited(self):
        ok = {"company": {"name": "X"}, "document": {"reference": "R1"},
              "customer": {"name": "C"}, "items": []}
        self.assertEqual(caps.validate_render_context(ok, "quotation"), [])
        missing = {"company": {"name": ""}, "document": {"reference": ""},
                   "customer": {"name": ""}, "items": []}
        self.assertTrue(caps.validate_render_context(missing, "quotation"))
        priced_delivery = {"company": {"name": "X"}, "document": {"reference": "R"},
                           "customer": {"name": "C"},
                           "items": [{"unit_price": "R10", "amount": "R10"}]}
        self.assertTrue(caps.validate_render_context(priced_delivery, "delivery"))


class RenderedContentTests(TestCase):
    """Render every family for every document type with sample data and assert the
    document-type rules hold in the actual output."""

    def test_no_family_leaks_wrong_content(self):
        c = make_company()
        with tenant_scope(c.id):
            for _key, name, _desc, _tags, design in TEMPLATE_FAMILIES:
                cd = dt.clean_design(design)
                q = design_to_html(cd, sample_context(c, "quotation")).lower()
                inv = design_to_html(cd, sample_context(c, "invoice")).lower()
                dn = design_to_html(cd, sample_context(c, "delivery")).lower()

                # Quotation: a supplier's offer — "compiled by" only. Never a
                # customer acceptance box, a delivery receipt, or delivered/outstanding.
                self.assertIn("compiled by", q, name)
                for bad in ("accepted by", "received in good order", "delivered by",
                            "outstanding", "proof of delivery"):
                    self.assertNotIn(bad, q, f"{name} quotation leaked {bad!r}")

                # Invoice: payment document — no delivery acknowledgement.
                for bad in ("received in good order", "delivered by"):
                    self.assertNotIn(bad, inv, f"{name} invoice leaked {bad!r}")

                # Delivery note: quantities, never prices.
                for token in ("ordered", "delivered", "outstanding",
                              "received in good order"):
                    self.assertIn(token, dn, f"{name} delivery missing {token!r}")
                for bad in ("unit price", "total due", "subtotal", "r18", "r78"):
                    self.assertNotIn(bad, dn, f"{name} delivery leaked price {bad!r}")


class JobTypeColumnTests(SimpleTestCase):
    def test_price_label_follows_job_type(self):
        self.assertEqual(caps.price_label("labour_hire"), "Rate")
        self.assertEqual(caps.price_label("plant_hire"), "Rate")
        self.assertEqual(caps.price_label("supply"), "Unit price")
        self.assertEqual(caps.price_label(None), "Unit price")


class MultiPageTests(TestCase):
    """A long quotation spans pages, repeats the item-table header, and numbers
    every page."""

    def test_long_quotation_paginates_with_repeated_header(self):
        import io

        import pdfplumber

        from .html_render import render_html_pdf
        from .models import FAMILY_BY_KEY, QuotationType, Quotation

        c = make_company("MP")
        with tenant_scope(c.id):
            qt = QuotationType.objects.create(company=c, key="labour_hire", label="Labour Hire")
            q = Quotation.objects.create(company=c, number="QT-MP", client_name="Big Co",
                                         site="S", quotation_type=qt)
            for i in range(1, 46):
                q.lines.create(company=c, position=i, description=f"Shift {i}",
                               qty=1, unit="shift", unit_cost=1500)
            pdf = render_html_pdf(q, "quotation", dt.clean_design(FAMILY_BY_KEY["horizon"][3]))
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            pages = [p.extract_text() or "" for p in doc.pages]
        self.assertGreater(len(pages), 1)                       # spilled to page 2
        # Column headers render uppercase (CSS text-transform), so the extracted
        # text is "RATE"/"DESCRIPTION".
        self.assertIn("RATE", pages[0])                         # job-type column label
        self.assertIn("DESCRIPTION", pages[1])                 # header repeated on p2
        self.assertIn("Page 1 of", "\n".join(pages))            # page numbering


class ReportLabSignoffTests(SimpleTestCase):
    """The shared ReportLab sign-off column omits the counter-sign line for a tax
    invoice (no acknowledgement) but keeps it for quotation/delivery."""

    def test_show_received_toggle(self):
        from reportlab.lib.styles import getSampleStyleSheet
        from .pdf import _signoff_column
        styles = getSampleStyleSheet()
        small, muted = styles["Normal"], styles["Normal"]
        with_recv = _signoff_column(small, muted, compiled_label="By:",
                                    prep_name="A B", today="01/01/2026")
        without = _signoff_column(small, muted, compiled_label="By:",
                                  prep_name="A B", today="01/01/2026",
                                  show_received=False)
        self.assertLess(len(without), len(with_recv))
