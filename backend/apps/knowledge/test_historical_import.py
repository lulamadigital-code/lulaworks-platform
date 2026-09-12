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

    def test_commit_all_adds_customers_to_crm(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            for nm in ["Sibanye Stillwater", "Anglo American", "Exxaro"]:
                StagedEntity.objects.create(batch=batch, company=self.company,
                    kind=StagedEntity.Kind.CUSTOMER, raw_name=nm,
                    verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
            before = Customer.objects.count()
            r = imp.commit_all(batch, self.mgr, kind=StagedEntity.Kind.CUSTOMER)
            self.assertEqual(r["created"], 3)
            self.assertEqual(Customer.objects.count(), before + 3)   # now in the CRM
            # All staged customers are resolved (none left pending).
            self.assertFalse(batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER,
                review_status=StagedEntity.Review.PENDING).exists())

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

    def test_prepared_by_is_internal_not_a_contact(self):
        # The brief's critical rule: "Prepared by" is an internal employee, never
        # a customer contact — even with no known company domain.
        from apps.knowledge.historical_import import _extract_entities
        text = ("Quotation\n"
                "Prepared by: Ronny Max ronny@mycontracting.co.za\n"
                "Customer: XYZ Mining\n"
                "Contact person: John Mokoena john@xyzmining.co.za\n")
        with tenant_scope(self.company.id):
            ents = _extract_entities(text, doc_type="quotation", company=self.company,
                                     user=self.mgr, use_ai=False)
        emails = {e["email"] for e in ents}
        names = {e["raw_name"] for e in ents}
        self.assertNotIn("ronny@mycontracting.co.za", emails)   # internal excluded
        self.assertIn("john@xyzmining.co.za", emails)           # customer contact kept
        self.assertIn("XYZ Mining", names)                      # customer captured
        # And the customer contact is a CONTACT, the company a CUSTOMER.
        by_email = {e["email"]: e["kind"] for e in ents}
        self.assertEqual(by_email["john@xyzmining.co.za"], "contact")

    def test_direction_supplier_doc_bill_to_is_internal(self):
        # On a SUPPLIER's invoice, "Bill To: <us>" is internal, and the issuer is
        # the supplier — don't create a customer from our own name.
        from apps.knowledge.historical_import import _extract_entities
        text = ("SUPPLIER INVOICE\n"
                "Supplier: Hydraulics SA\n"
                "Bill To: Acme Civils\n")
        with tenant_scope(self.company.id):
            ents = _extract_entities(text, doc_type="supplier_invoice",
                                     company=self.company, user=self.mgr, use_ai=False)
        kinds = {e["raw_name"]: e["kind"] for e in ents}
        self.assertEqual(kinds.get("Hydraulics SA"), "supplier")
        self.assertNotIn("Acme Civils", kinds)   # "bill to us" suppressed

    def test_reprocess_with_no_text_preserves_entities(self):
        # Regression: a retry where the file can't be read must NOT soft-delete
        # the entities already extracted (the prod data-loss bug).
        data = b"QUOTATION\nCustomer: ABC Mining\nContact: joe@abcmining.co.za\n"
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            doc = imp.ingest(batch, "q.txt", data, self.mgr)
            n1 = doc.entities.count()
            self.assertGreater(n1, 0)
            doc.file.delete(save=True)          # simulate the file going missing
            imp.process_document(doc)
            doc.refresh_from_db()
            # Entities are not wiped — either kept, or re-staged from the cached text.
            self.assertEqual(doc.entities.count(), n1)
            self.assertIn(doc.status, [ImportedDocument.Status.COMPLETED,
                                       ImportedDocument.Status.NEEDS_REVIEW])

    def test_reprocess_is_idempotent(self):
        data = b"INVOICE\nInvoice No: INV-1\nCustomer: ABC Mining\nContact: joe@abcmining.co.za\n"
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            doc = imp.ingest(batch, "inv.txt", data, self.mgr)
            n1 = doc.entities.count()
            imp.process_document(doc)                          # run again
            self.assertEqual(doc.entities.count(), n1)         # no duplication


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DedupAndCleaningTests(TestCase):
    """Cross-document de-dup + name cleaning — the same customer/contact seen in
    many documents collapses to one reviewable row."""

    def setUp(self):
        self.company = Company.objects.create(name="Acme Civils", email="info@acmecivils.co.za")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@acmecivils.co.za")

    def test_same_customer_across_docs_is_one_row(self):
        d1 = b"QUOTATION\nCustomer: Western Platinum (Pty) Ltd QUOTATION\n"
        d2 = b"TAX INVOICE\nCustomer: Western Platinum(Pty)Ltd TAX INVOICE\n"
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            imp.ingest(batch, "q.txt", d1, self.mgr)
            imp.ingest(batch, "i.txt", d2, self.mgr)
            custs = list(batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER))
        # Two docs, two name variants, same customer → ONE staged row.
        self.assertEqual(len(custs), 1)
        self.assertEqual(custs[0].raw_name, "Western Platinum (Pty) Ltd")  # cleaned
        self.assertEqual(custs[0].mentions, 2)                              # evidence count

    def test_same_contact_email_dedupes_prefers_real_name(self):
        # Doc 1: only the email (regex guesses "Luckymacheke89"). Doc 2: the AI
        # extractor supplies the real name for the same email. Dedup by email
        # collapses them to one, preferring the real name.
        d1 = b"QUOTATION\nCustomer: Kumba\nContact: luckymacheke89@gmail.com\n"
        d2 = b"INVOICE\nCustomer: Kumba\ncontact person on site\n"
        ai = [{"kind": "contact", "raw_name": "Lucky Macheke",
               "email": "luckymacheke89@gmail.com", "phone": "", "reference": ""}]
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            imp.ingest(batch, "q.txt", d1, self.mgr)
            with patch("apps.knowledge.document_intelligence.ai_extract_entities",
                       return_value=ai):
                imp.ingest(batch, "i.txt", d2, self.mgr)
            contacts = list(batch.entities.filter(kind=StagedEntity.Kind.CONTACT,
                                                  email="luckymacheke89@gmail.com"))
        self.assertEqual(len(contacts), 1)            # one person, not two
        self.assertEqual(contacts[0].raw_name, "Lucky Macheke")  # real name preferred
        self.assertEqual(contacts[0].mentions, 2)

    def test_consolidate_merges_site_and_doctype_variants(self):
        # All the same customer — site suffix + doc-type typo variants collapse.
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            for nm in ["Sibanye Stillwater K4 Shaft", "Sibanye Stillwater",
                       "Sibanye Stillwater K4 Shaft Qoutation"]:
                StagedEntity.objects.create(batch=batch, company=self.company,
                    kind=StagedEntity.Kind.CUSTOMER, raw_name=nm,
                    verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
            imp.consolidate_batch(batch)
            custs = list(batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER))
        self.assertEqual(len(custs), 1)
        self.assertEqual(custs[0].raw_name, "Sibanye Stillwater")
        self.assertEqual(custs[0].mentions, 3)

    def test_manual_merge_trading_and_registered_name(self):
        # Only a human knows Western Platinum (Pty) Ltd == Sibanye Stillwater.
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            a = StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="Sibanye Stillwater",
                mentions=5, verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
            b = StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="Western Platinum (Pty) Ltd",
                mentions=2, verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
            imp.consolidate_batch(batch)   # won't auto-merge these (different names)
            self.assertEqual(batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER).count(), 2)
            n = imp.merge_entities(batch, a.pk, [b.pk], self.mgr)
            custs = list(batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER))
        self.assertEqual(n, 1)
        self.assertEqual(len(custs), 1)
        self.assertEqual(custs[0].mentions, 7)       # 5 + 2

    def test_reject_removes_from_queue(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            e = StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="Junk Co",
                verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
            imp.commit_entity(e, self.mgr, decision="reject")
            # Gone from the live queue (soft-deleted).
            self.assertFalse(batch.entities.filter(pk=e.pk).exists())

    def test_consolidate_strips_owner_email(self):
        # info@acmecivils.co.za is the company's own domain → must not survive as
        # a customer contact.
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CONTACT, raw_name="Owner",
                email="admin@acmecivils.co.za", verdict=StagedEntity.Verdict.NEW,
                created_by=self.mgr)
            StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CONTACT, raw_name="Real Client",
                email="john@kumba.co.za", verdict=StagedEntity.Verdict.NEW,
                created_by=self.mgr)
            imp.consolidate_batch(batch)
            emails = set(batch.entities.filter(kind=StagedEntity.Kind.CONTACT)
                         .values_list("email", flat=True))
        self.assertIn("john@kumba.co.za", emails)
        self.assertNotIn("admin@acmecivils.co.za", emails)

    def test_consolidate_existing_duplicates(self):
        # Stage duplicates directly (simulating the pre-fix data), then consolidate.
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            for nm in ["Western Platinum(Pty)Ltd QUOTATION", "Western Platinum (Pty) Ltd",
                       "Western Platinum(Pty)Ltd TAX INVOICE"]:
                StagedEntity.objects.create(batch=batch, company=self.company,
                    kind=StagedEntity.Kind.CUSTOMER, raw_name=nm,
                    verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
            removed = imp.consolidate_batch(batch)
            custs = list(batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER))
        self.assertEqual(removed, 2)
        self.assertEqual(len(custs), 1)
        self.assertEqual(custs[0].raw_name, "Western Platinum (Pty) Ltd")
        self.assertEqual(custs[0].mentions, 3)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FileStorageGuaranteeTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")

    def test_queue_document_stores_file_on_disk(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            doc = imp.queue_document(batch, "po.pdf", b"%PDF-1.4 real bytes", self.mgr)
            doc.refresh_from_db()
        self.assertTrue(doc.file and doc.file.name)                 # name persisted
        self.assertTrue(doc.file.storage.exists(doc.file.name))     # actually on storage

    def test_unstorable_file_raises_not_silent(self):
        # If storage can't keep the file, queue_document must raise (no file-less row).
        from unittest.mock import patch
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="H")
            with patch("django.core.files.storage.FileSystemStorage.exists", return_value=False):
                with self.assertRaises(RuntimeError):
                    imp.queue_document(batch, "po.pdf", b"bytes", self.mgr)


class DocumentDateTests(TestCase):
    def test_extracts_various_formats(self):
        from datetime import date
        from apps.knowledge.historical_import import extract_document_date as ex
        self.assertEqual(ex("Quotation\nDate: 2023-05-12"), date(2023, 5, 12))
        self.assertEqual(ex("Invoice Date: 19/02/2024"), date(2024, 2, 19))  # SA D/M/Y
        self.assertEqual(ex("dated 12 August 2024"), date(2024, 8, 12))
        self.assertIsNone(ex("no date here"))
        self.assertIsNone(ex("VAT/Reg 2011/123456/07"))     # not a date

    def test_document_date_set_on_process(self):
        company = Company.objects.create(name="C2")
        mgr = _user(company, ["customers.manage"], "m2@c.co")
        import tempfile
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            with tenant_scope(company.id):
                batch = imp.create_batch(mgr, label="H")
                doc = imp.ingest(batch, "q.txt",
                                 b"QUOTATION\nDate: 2023-05-12\nCustomer: ABC Mining\n", mgr)
                doc.refresh_from_db()
        from datetime import date
        self.assertEqual(doc.document_date, date(2023, 5, 12))
