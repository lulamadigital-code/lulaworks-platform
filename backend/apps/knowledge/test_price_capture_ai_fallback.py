"""Importing a supplier invoice/quote must build price history even when the
document's layout defeats the deterministic line regex — the AI fallback keeps
the HistoricalLineItem ledger (which the price-history page reads) populated."""
from unittest.mock import patch

from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, User
from apps.knowledge.historical_import import _capture_document_lines
from apps.knowledge.models import HistoricalLineItem, ImportBatch, ImportedDocument

# A columnar invoice body with NO trailing "cents" price — the deterministic
# _PRICED_LINE regex matches nothing here, so without the fallback the ledger
# stays empty.
_UNPARSEABLE = """ACME STEEL SUPPLIES
Qty  Item                 Unit price   Amount
10   Steel pipe 50mm           1 500      15 000
5    Angle iron 40x40            900       4 500
"""


class PriceCaptureAiFallbackTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = User.objects.create_user("u@acme.co", "x", active_company=self.c)
        with tenant_scope(self.c.id):
            self.batch = ImportBatch.objects.create(company=self.c, created_by=self.u)
            self.doc = ImportedDocument.objects.create(
                company=self.c, batch=self.batch, created_by=self.u,
                filename="acme-invoice.pdf", text=_UNPARSEABLE,
                doc_type=ImportedDocument.DocType.SUPPLIER_INVOICE)

    def test_ai_fallback_populates_ledger_when_regex_finds_nothing(self):
        fake = [
            {"description": "Steel pipe 50mm", "unit": "each", "unit_price": "1500.00"},
            {"description": "Angle iron 40x40", "unit": "each", "unit_price": "900.00"},
        ]
        with tenant_scope(self.c.id), \
                patch("apps.knowledge.document_intelligence.ai_extract_prices",
                      return_value=fake) as ai:
            n = _capture_document_lines(self.doc, self.u)
        self.assertEqual(n, 2)
        ai.assert_called_once()  # fallback was used (regex found nothing)
        with tenant_scope(self.c.id):
            rows = HistoricalLineItem.objects.filter(document=self.doc)
            self.assertEqual(rows.count(), 2)
            self.assertTrue(all(
                r.direction == HistoricalLineItem.Direction.PURCHASE for r in rows))
            self.assertEqual(
                {str(r.unit_price) for r in rows}, {"1500.00", "900.00"})

    def test_regex_path_does_not_call_ai(self):
        # A clean, deterministic priced line — the fast path, no AI call.
        with tenant_scope(self.c.id):
            self.doc.text = "Steel pipe 50mm    R 1 500.00\n"
            self.doc.save(update_fields=["text"])
        with tenant_scope(self.c.id), \
                patch("apps.knowledge.document_intelligence.ai_extract_prices") as ai:
            n = _capture_document_lines(self.doc, self.u)
        self.assertEqual(n, 1)
        ai.assert_not_called()
