"""Historical Business Import — the "Bring Your Business History" pipeline.
Locks the guardrail: documents are classified and entities resolved, but
NOTHING enters the ERP until a person confirms."""
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.core.context import tenant_scope
from apps.customers.models import Customer, CustomerContact
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge import historical_import as imp
from apps.knowledge.classifier import classify_document
from apps.knowledge.models import ImportedDocument, StagedEntity
from apps.procurement.models import Supplier


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


_PO_TEXT = b"""PURCHASE ORDER
PO Number: PO-2024-0912
Customer: ABC Mining (Pty) Ltd
Supplier: Hydraulics SA
Contact: thabo@abcmining.co.za
VAT No: 4123456789
Total: R485 000
"""


class ClassifierTests(TestCase):
    def test_classifies_po(self):
        t, conf = classify_document("po_2024.pdf", _PO_TEXT.decode())
        self.assertEqual(t, ImportedDocument.DocType.CUSTOMER_PO)
        self.assertGreater(conf, 0.5)

    def test_unknown_is_other(self):
        t, conf = classify_document("notes.txt", "just some random words here")
        self.assertEqual(t, ImportedDocument.DocType.OTHER)
        self.assertEqual(conf, 0.0)


class ImportPipelineTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")
        self.viewer = _user(self.company, ["projects.view"], "v@a.co")
        # An existing customer the import should MATCH rather than duplicate.
        with tenant_scope(self.company.id):
            self.existing = Customer.objects.create(
                company=self.company, name="ABC Mining (Pty) Ltd")

    def test_ingest_classifies_extracts_and_resolves(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            doc = imp.ingest(batch, "po_2024.txt", _PO_TEXT, self.mgr)
            summary = imp.batch_summary(batch)

            self.assertEqual(doc.doc_type, ImportedDocument.DocType.CUSTOMER_PO)
            # The customer mention resolves to the existing record (no duplicate).
            cust = batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER).first()
            self.assertIsNotNone(cust)
            self.assertEqual(cust.verdict, StagedEntity.Verdict.MATCHED)
            self.assertEqual(cust.match_id, str(self.existing.id))
            # A supplier and a contact were also discovered.
            self.assertTrue(batch.entities.filter(kind=StagedEntity.Kind.SUPPLIER).exists())
            self.assertTrue(batch.entities.filter(kind=StagedEntity.Kind.CONTACT).exists())
        self.assertGreaterEqual(summary["documents"], 1)
        self.assertGreaterEqual(summary["auto_matched"], 1)

    def test_high_confidence_match_auto_links(self):
        # ABC Mining already exists → the historical mention should auto-link to
        # it (no manual click), connecting the customer from history.
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            cust = batch.entities.get(kind=StagedEntity.Kind.CUSTOMER)
            self.assertEqual(cust.review_status, StagedEntity.Review.LINKED)
            self.assertEqual(cust.resolved_id, str(self.existing.id))
            self.assertEqual(Customer.objects.count(), 1)    # linked, not duplicated

    def test_nothing_written_until_commit(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            # Ingest alone creates NO new customers/suppliers.
            self.assertEqual(Customer.objects.count(), 1)   # only the pre-existing
            self.assertEqual(Supplier.objects.count(), 0)

    def test_commit_link_and_create(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            cust = batch.entities.get(kind=StagedEntity.Kind.CUSTOMER)
            supp = batch.entities.get(kind=StagedEntity.Kind.SUPPLIER)

            r1 = imp.commit_entity(cust, self.mgr, decision="link")
            self.assertEqual(r1["resolved_id"], str(self.existing.id))
            self.assertEqual(Customer.objects.count(), 1)   # linked, not duplicated

            r2 = imp.commit_entity(supp, self.mgr, decision="create")
            self.assertTrue(r2["ok"])
            self.assertEqual(Supplier.objects.count(), 1)   # new supplier created

    def test_commit_requires_permission(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            cust = batch.entities.get(kind=StagedEntity.Kind.CUSTOMER)
            with self.assertRaises(imp.CommitError):
                imp.commit_entity(cust, self.viewer, decision="link")

    def test_supplier_invoice_feeds_price_history(self):
        from apps.procurement.models import SupplierPrice
        invoice = (
            b"SUPPLIER INVOICE\n"
            b"Invoice No: INV-2024-55\n"
            b"Supplier: Hydraulics SA\n"
            b"3 m  Hydraulic pipe  R173.00\n"
            b"2 each  Gasket kit  R42.50\n")
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="Prices")
            imp.ingest(batch, "inv.txt", invoice, self.mgr)
            supp = batch.entities.get(kind=StagedEntity.Kind.SUPPLIER)
            res = imp.commit_entity(supp, self.mgr, decision="create")
            # Committing the supplier captured the invoice's priced lines.
            self.assertGreaterEqual(res["prices_recorded"], 1)
            self.assertTrue(SupplierPrice.objects.filter(
                description__icontains="Hydraulic pipe").exists())

    def test_ai_enrichment_adds_missed_entities(self):
        # A document with no labelled customer line — the regex finds nothing,
        # but the (mocked) AI extractor surfaces the buyer. AI only ADDS.
        text = b"We completed the pump overhaul for Kumba Iron Ore last quarter.\n"
        ai_hits = [{"kind": "customer", "raw_name": "Kumba Iron Ore",
                    "email": "", "phone": "", "reference": ""}]
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="AI")
            with patch("apps.knowledge.document_intelligence.ai_extract_entities",
                       return_value=ai_hits):
                imp.ingest(batch, "note.txt", text, self.mgr)
            self.assertTrue(batch.entities.filter(
                kind=StagedEntity.Kind.CUSTOMER, raw_name="Kumba Iron Ore").exists())

    def test_ai_prices_merge_without_duplicates(self):
        from apps.knowledge.historical_import import _extract_priced_lines
        text = "5 m  Hydraulic pipe  R173.00\n"   # deterministic catches this
        ai_lines = [{"description": "Hydraulic pipe", "unit": "m", "unit_price": "173.00"},
                    {"description": "Gasket kit", "unit": "each", "unit_price": "42.50"}]
        with patch("apps.knowledge.document_intelligence.ai_extract_prices",
                   return_value=ai_lines):
            lines = _extract_priced_lines(text, company=self.company, user=self.mgr, use_ai=True)
        descs = sorted(l["description"] for l in lines)
        # Hydraulic pipe appears once (deduped), Gasket kit added by AI.
        self.assertEqual(descs, ["Gasket kit", "Hydraulic pipe"])

    def test_contact_create_needs_customer(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            contact = batch.entities.filter(kind=StagedEntity.Kind.CONTACT).first()
            with self.assertRaises(imp.CommitError):
                imp.commit_entity(contact, self.mgr, decision="create")
            imp.commit_entity(contact, self.mgr, decision="create",
                              customer_id=str(self.existing.id))
            self.assertEqual(CustomerContact.objects.count(), 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ImportArchitectureTests(TestCase):
    """The async/dedup/internal-email architecture fixes."""

    def setUp(self):
        self.company = Company.objects.create(name="Acme Civils", email="info@acmecivils.co.za")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@acmecivils.co.za")

    def test_duplicate_detection_by_hash(self):
        data = b"QUOTATION\nQuote No: Q-1\nCustomer: ABC Mining\n"
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            d1 = imp.queue_document(batch, "quote.pdf", data, self.mgr)
            # Same bytes, different name → duplicate of the first.
            d2 = imp.queue_document(batch, "RENAMED_quote.pdf", data, self.mgr)
        self.assertEqual(d1.status, ImportedDocument.Status.QUEUED)
        self.assertEqual(d2.status, ImportedDocument.Status.DUPLICATE)
        self.assertEqual(d2.duplicate_of_id, d1.id)
        self.assertEqual(d1.file_hash, d2.file_hash)

    def test_queue_then_process_status_flow(self):
        data = b"PURCHASE ORDER\nPO Number: PO-9\nCustomer: ABC Mining\nContact: joe@abcmining.co.za\n"
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            doc = imp.queue_document(batch, "po.txt", data, self.mgr)
            self.assertEqual(doc.status, ImportedDocument.Status.QUEUED)
            self.assertEqual(doc.entities.count(), 0)      # nothing staged yet
            imp.process_document(doc)
            doc.refresh_from_db()
            # Done-processing = COMPLETED, or NEEDS_REVIEW when entities await a human.
            self.assertIn(doc.status, [ImportedDocument.Status.COMPLETED,
                                       ImportedDocument.Status.NEEDS_REVIEW])
            self.assertIsNotNone(doc.completed_at)
            self.assertTrue(doc.entities.exists())

    def test_internal_email_not_a_contact(self):
        # info@acmecivils.co.za = internal domain; mgr@acmecivils.co.za = member.
        data = (b"QUOTATION\nPrepared by: admin@acmecivils.co.za\n"
                b"Customer contact: procurement@kumba.co.za\n")
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            doc = imp.ingest(batch, "q.txt", data, self.mgr)
            emails = set(doc.entities.filter(kind=StagedEntity.Kind.CONTACT)
                         .values_list("email", flat=True))
        self.assertIn("procurement@kumba.co.za", emails)       # external kept
        self.assertNotIn("admin@acmecivils.co.za", emails)     # internal excluded

    def test_reprocess_is_idempotent(self):
        data = b"INVOICE\nInvoice No: INV-1\nCustomer: ABC Mining\nContact: joe@abcmining.co.za\n"
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            doc = imp.ingest(batch, "inv.txt", data, self.mgr)
            n1 = doc.entities.count()
            imp.process_document(doc)                          # run again
            self.assertEqual(doc.entities.count(), n1)         # no duplication
