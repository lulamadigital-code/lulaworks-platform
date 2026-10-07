"""The local, deterministic columnar-table parser — turns invoice/quote table
rows into priced line items with no AI, so price history builds on-device."""
from decimal import Decimal

from django.test import SimpleTestCase

from apps.rfq.extraction import parse_table_lines


class ParseTableLinesTests(SimpleTestCase):
    def _by_desc(self, lines):
        return {l.description.lower(): l for l in lines}

    def test_columnar_invoice_picks_unit_price_not_total(self):
        text = (
            "Qty  Item                 Unit price   Amount\n"
            "10   Steel pipe 50mm           1 500.00     15 000.00\n"
            "5    Angle iron 40x40            900.00      4 500.00\n"
            "Subtotal                                    19 500.00\n"
            "VAT 15%                                       2 925.00\n"
            "Total                                        22 425.00\n"
        )
        got = self._by_desc(parse_table_lines(text))
        self.assertIn("steel pipe 50mm", got)
        self.assertIn("angle iron 40x40", got)
        # unit price reconciles to the amount (1500*10 = 15000), not the total column
        self.assertEqual(got["steel pipe 50mm"].unit_price, Decimal("1500.00"))
        self.assertEqual(got["steel pipe 50mm"].qty, Decimal("10"))
        self.assertEqual(got["angle iron 40x40"].unit_price, Decimal("900.00"))
        # totals / tax / header rows are not line items
        self.assertNotIn("subtotal", got)
        self.assertNotIn("vat 15%", got)
        self.assertNotIn("total", got)

    def test_tab_delimited_spreadsheet_rows(self):
        text = "Cement 42.5N 50kg\t20\tbag\tR 95.00\tR 1 900.00\n"
        got = self._by_desc(parse_table_lines(text))
        self.assertIn("cement 42.5n 50kg", got)
        self.assertEqual(got["cement 42.5n 50kg"].unit_price, Decimal("95.00"))

    def test_single_money_requires_cents(self):
        # A single money column WITH cents is a real amount → captured.
        got = self._by_desc(parse_table_lines("Site supervision      R 1 250.00\n"))
        self.assertIn("site supervision", got)
        self.assertEqual(got["site supervision"].unit_price, Decimal("1250.00"))
        # A single bare number with no cents / currency is NOT a price → skipped,
        # so a stray count never becomes an invented price.
        self.assertEqual(parse_table_lines("Delivery note number      5\n"), [])

    def test_prose_is_not_captured(self):
        # Ordinary sentences have no money column → nothing captured.
        self.assertEqual(
            parse_table_lines("Please quote for the supply of mechanical seals.\n"), [])
