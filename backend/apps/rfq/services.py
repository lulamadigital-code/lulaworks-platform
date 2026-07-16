"""RFQ pipeline services (RFQ_INTELLIGENCE §3-4, §7, §11).

Ingest → deterministic extract → review → approve. Nothing is auto-approved
(locked human-approval boundary). On approval the RFQ becomes a Quotation and a
Project DNA record is minted from the human-verified data.
"""

from decimal import Decimal

from django.db import transaction

from apps.core.events import publish
from apps.knowledge.models import ProjectDNA
from apps.quotes.services import create_quotation
from apps.storage.models import StorageFile

from .extraction import extract_rfq
from .intelligence import enrich_with_ai
from .models import ExtractedField, RFQDocument, RFQLineItem, RFQStatus

CONFIDENCE_THRESHOLD = 0.85  # below → needs_review (RFQ_INTELLIGENCE decision 16)


@transaction.atomic
def ingest_rfq(company, user, *, uploaded_file, original_name: str) -> RFQDocument:
    """Store the original immutably, run deterministic extraction, and populate
    the review record with per-field confidence."""
    stored = StorageFile.objects.create(
        company=company, module="rfq", document_type="rfq",
        original_name=original_name, storage_path="", file_size=uploaded_file.size,
        mime_type=getattr(uploaded_file, "content_type", ""), uploaded_by=user,
    )
    stored.storage_path = f"rfq/{stored.id}_{original_name}"
    # Persist the blob (FileSystemStorage in dev, S3 in prod via STORAGES).
    from django.core.files.storage import default_storage

    saved_path = default_storage.save(stored.storage_path, uploaded_file)
    stored.storage_path = saved_path
    stored.checksum = ""
    stored.save(update_fields=["storage_path"])

    rfq = RFQDocument.objects.create(
        company=company, source_file=stored, original_name=original_name,
        status=RFQStatus.UPLOADED, created_by=user, updated_by=user,
    )

    with default_storage.open(saved_path, "rb") as fh:
        extraction = extract_rfq(fh)

    # Deterministic-first; AI fills gaps only if a provider is configured (§4).
    extraction = enrich_with_ai(company, user, extraction)

    rfq.warnings = extraction.warnings
    rfq.extracted_text = extraction.text[:20000]
    rfq.status = RFQStatus.IN_REVIEW
    rfq.save(update_fields=["warnings", "extracted_text", "status"])

    for key, ev in extraction.fields.items():
        ExtractedField.objects.create(
            company=company, rfq=rfq, key=key, value=ev.value, confidence=ev.confidence,
            method=ev.method, source_text=ev.source_text,
            review_status="auto" if ev.confidence >= CONFIDENCE_THRESHOLD else "needs_review",
        )
    for pos, line in enumerate(extraction.lines, start=1):
        RFQLineItem.objects.create(
            company=company, rfq=rfq, position=pos, description=line.description,
            qty=line.qty, unit=line.unit, unit_price=line.unit_price, confidence=0.9,
        )
    publish("RFQExtracted", company=company, subject=rfq, actor=user,
            payload={"fields": len(extraction.fields), "lines": len(extraction.lines)})
    return rfq


@transaction.atomic
def approve_rfq(rfq: RFQDocument, user, *, client_name: str) -> RFQDocument:
    """Human approval (never automatic): create the Quotation from the reviewed
    lines and mint Project DNA from the approved data."""
    lines = [
        {"description": line.description, "qty": line.qty, "unit": line.unit,
         "unit_price": line.unit_price or Decimal("0")}
        for line in rfq.lines.all()
    ]
    quote = create_quotation(
        rfq.company, user, client_name=client_name,
        title=rfq.original_name or "RFQ", lines=lines,
    )
    dna = ProjectDNA.objects.create(
        company=rfq.company, quotation=quote, client_name=client_name,
        site=_field(rfq, "ship_to"), scope=rfq.extracted_text[:2000],
        materials=[line.description for line in rfq.lines.all()],
        estimated_value=quote.subtotal, created_by=user, updated_by=user,
    )
    rfq.quotation = quote
    rfq.status = RFQStatus.APPROVED
    rfq.approved_by = user
    rfq.save(update_fields=["quotation", "status", "approved_by"])
    for f in rfq.fields.all():
        f.review_status = "approved"
        f.approved_value = f.approved_value or f.value
        f.save(update_fields=["review_status", "approved_value"])
    publish("RFQApproved", company=rfq.company, subject=rfq, actor=user,
            payload={"quotation": quote.number, "project_dna": str(dna.id)})
    return rfq


def _field(rfq, key: str) -> str:
    f = rfq.fields.filter(key=key).first()
    return (f.approved_value or f.value) if f else ""
