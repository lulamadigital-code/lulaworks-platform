"""Generate the sanitised RFQ test fixture (real Sibanye/Western Platinum Coupa
layout with placeholder PII). Run with reportlab available; the PDF is committed
so tests need no reportlab dependency."""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "sample_rfq.pdf"
ROWS = [
    "Western Platinum (Pty) Ltd", "PURCHASE ORDER",
    "Lulama Projects and Services ( - TRL0086",
    "PO NUMBER 5502446497", "Attn: Jane Placeholder", "DATE 2026/07/10",
    "CONTACT Sam Buyer", "Ship To Bill To",
    "K4 Shaft Western Platinum (Pty) Ltd",
    "Line Description Need By Date Qty Unit Price Total",
    "1 Lip channel 100x50x20x2mm galvanized 6m 12 each 485,00 5 820,00",
    "2 Bearing 6203-2RS SKF 40 each 62,50 2 500,00",
    "3 Transportation 1 each 1 500,00 1 500,00",
    "Total Nett Value excl.VAT 9 820,00",
]
c = canvas.Canvas(str(OUT), pagesize=A4)
y = A4[1] - 20 * mm
c.setFont("Helvetica", 10)
for r in ROWS:
    c.drawString(20 * mm, y, r); y -= 7 * mm
c.save()
print("wrote", OUT)
