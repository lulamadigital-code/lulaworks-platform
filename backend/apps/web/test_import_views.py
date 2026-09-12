"""Bring Your Business History — the console flow renders and enforces its
guardrail through the web views (upload → review → confirm)."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.models import ImportBatch, StagedEntity

_PO = b"""PURCHASE ORDER
PO Number: PO-2024-0912
Customer: ABC Mining (Pty) Ltd
Supplier: Hydraulics SA
Contact: thabo@abcmining.co.za
"""


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ImportViewsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")
        self.viewer = _user(self.company, ["projects.view"], "v@a.co")
        with tenant_scope(self.company.id):
            self.existing = Customer.objects.create(
                company=self.company, name="ABC Mining (Pty) Ltd")

    def test_centre_renders_and_start_creates_batch(self):
        self.client.force_login(self.mgr)
        self.assertEqual(self.client.get("/import/").status_code, 200)
        r = self.client.post("/import/start/", {"label": "My history"})
        self.assertEqual(r.status_code, 302)
        with tenant_scope(self.company.id):
            self.assertEqual(ImportBatch.objects.count(), 1)

    def test_upload_process_and_commit_link(self):
        self.client.force_login(self.mgr)
        with tenant_scope(self.company.id):
            batch = ImportBatch.objects.create(company=self.company, label="H",
                                               created_by=self.mgr)
        f = SimpleUploadedFile("po.txt", _PO, content_type="text/plain")
        r = self.client.post(f"/import/{batch.pk}/", {"documents": f})
        self.assertEqual(r.status_code, 302)
        # Batch page renders with the staged, auto-matched customer.
        page = self.client.get(f"/import/{batch.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "ABC Mining")
        with tenant_scope(self.company.id):
            cust = batch.entities.get(kind=StagedEntity.Kind.CUSTOMER)
            self.assertEqual(cust.verdict, StagedEntity.Verdict.MATCHED)
        # Confirm the link — no duplicate customer created.
        self.client.post(f"/import/{batch.pk}/entity/{cust.pk}/commit/", {"decision": "link"})
        with tenant_scope(self.company.id):
            cust.refresh_from_db()
            self.assertEqual(cust.review_status, StagedEntity.Review.LINKED)
            self.assertEqual(Customer.objects.count(), 1)

    def test_commit_via_ajax_returns_json(self):
        self.client.force_login(self.mgr)
        with tenant_scope(self.company.id):
            batch = ImportBatch.objects.create(company=self.company, label="H",
                                               created_by=self.mgr)
            e = StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="Reject Me",
                verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
        r = self.client.post(f"/import/{batch.pk}/entity/{e.pk}/commit/",
                             {"decision": "reject"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/json")
        self.assertTrue(r.json()["ok"])
        with tenant_scope(self.company.id):
            self.assertFalse(batch.entities.filter(pk=e.pk).exists())   # rejected = gone

    def test_bulk_commit_all_via_ajax_returns_json(self):
        self.client.force_login(self.mgr)
        with tenant_scope(self.company.id):
            batch = ImportBatch.objects.create(company=self.company, label="H",
                                               created_by=self.mgr)
            StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="Kumba",
                verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
        r = self.client.post(f"/import/{batch.pk}/commit-all/", {"kind": "customer"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/json")
        self.assertTrue(r.json()["ok"])
        with tenant_scope(self.company.id):
            self.assertTrue(Customer.objects.filter(name="Kumba").exists())  # in CRM now

    def test_viewer_blocked(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get("/import/").status_code, 302)  # redirected away


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class HistoryWorkspaceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")

    def test_overview_and_customers_discovered_render(self):
        from apps.knowledge.models import ImportBatch, StagedEntity
        self.client.force_login(self.mgr)
        with tenant_scope(self.company.id):
            batch = ImportBatch.objects.create(company=self.company, label="H", created_by=self.mgr)
            StagedEntity.objects.create(batch=batch, company=self.company,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="ABC Mining", mentions=12,
                verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
        centre = self.client.get("/import/")
        self.assertEqual(centre.status_code, 200)
        self.assertContains(centre, "Customers")
        disc = self.client.get("/import/customers/")
        self.assertEqual(disc.status_code, 200)
        self.assertContains(disc, "ABC Mining")
        self.assertContains(disc, "12")        # mentions


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class DocumentsExplorerTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")

    def test_explorer_and_detail(self):
        from apps.knowledge.models import ImportBatch, ImportedDocument, StagedEntity
        self.client.force_login(self.mgr)
        with tenant_scope(self.company.id):
            batch = ImportBatch.objects.create(company=self.company, label="H", created_by=self.mgr)
            doc = ImportedDocument.objects.create(batch=batch, company=self.company,
                filename="abc_quote.pdf", doc_type=ImportedDocument.DocType.QUOTATION,
                status=ImportedDocument.Status.COMPLETED, text="Customer: ABC Mining",
                text_chars=20, created_by=self.mgr)
            StagedEntity.objects.create(batch=batch, company=self.company, document=doc,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="ABC Mining",
                verdict=StagedEntity.Verdict.NEW, created_by=self.mgr)
        lst = self.client.get("/import/documents/")
        self.assertEqual(lst.status_code, 200)
        self.assertContains(lst, "abc_quote.pdf")
        # Type filter
        self.assertContains(self.client.get("/import/documents/?type=quotation"), "abc_quote.pdf")
        detail = self.client.get(f"/import/documents/{doc.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "abc_quote.pdf")     # the document itself
        self.assertNotContains(detail, "Extracted from this document")  # view-only now


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class RelationshipsViewTests(TestCase):
    def test_relationships_renders(self):
        from apps.knowledge.models import ImportBatch, ImportedDocument, HistoricalJob
        company = Company.objects.create(name="C")
        mgr = _user(company, ["customers.manage"], "m@c.co")
        self.client.force_login(mgr)
        with tenant_scope(company.id):
            batch = ImportBatch.objects.create(company=company, label="H", created_by=mgr)
            job = HistoricalJob.objects.create(company=company, batch=batch,
                title="Job QT-1", customer_name="ABC Mining", reference="qt1",
                confidence=0.8, created_by=mgr)
            ImportedDocument.objects.create(batch=batch, company=company, job=job,
                filename="q.pdf", doc_type=ImportedDocument.DocType.QUOTATION, created_by=mgr)
        r = self.client.get("/import/relationships/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ABC Mining")
        self.assertContains(r, "q.pdf")


import tempfile
@override_settings(CELERY_TASK_ALWAYS_EAGER=True, MEDIA_ROOT=tempfile.mkdtemp())
class DocumentPreviewTests(TestCase):
    def test_pdf_document_previews_inline(self):
        from django.core.files.base import ContentFile
        from apps.knowledge.models import ImportBatch, ImportedDocument
        company = Company.objects.create(name="C")
        mgr = _user(company, ["customers.manage"], "m@c.co")
        self.client.force_login(mgr)
        with tenant_scope(company.id):
            batch = ImportBatch.objects.create(company=company, label="H", created_by=mgr)
            doc = ImportedDocument.objects.create(batch=batch, company=company,
                filename="abc_quote.pdf", status=ImportedDocument.Status.COMPLETED, created_by=mgr)
            doc.file.save("abc_quote.pdf", ContentFile(b"%PDF-1.4 fake"), save=True)
        r = self.client.get(f"/import/documents/{doc.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "<iframe")                 # inline preview
        self.assertContains(r, "/media/")                 # absolute media URL


import io
import tempfile as _tempfile
import zipfile


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, MEDIA_ROOT=_tempfile.mkdtemp())
class ZipUploadTests(TestCase):
    def test_zip_is_unpacked_into_documents(self):
        from apps.knowledge.models import ImportBatch, ImportedDocument
        company = Company.objects.create(name="C")
        mgr = _user(company, ["customers.manage"], "m@c.co")
        self.client.force_login(mgr)
        with tenant_scope(company.id):
            batch = ImportBatch.objects.create(company=company, label="H", created_by=mgr)
        # A zip with two real docs, a nested-folder doc, and junk that must be skipped.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("po1.txt", _PO)
            zf.writestr("history/invoice2.txt", b"INVOICE\nCustomer: ABC Mining (Pty) Ltd\n")
            zf.writestr("__MACOSX/._po1.txt", b"junk")
            zf.writestr(".DS_Store", b"junk")
        buf.seek(0)
        z = SimpleUploadedFile("history.zip", buf.read(), content_type="application/zip")
        r = self.client.post(f"/import/{batch.pk}/", {"documents": z})
        self.assertEqual(r.status_code, 302)
        with tenant_scope(company.id):
            names = set(ImportedDocument.objects.filter(batch=batch).values_list("filename", flat=True))
            self.assertEqual(names, {"po1.txt", "invoice2.txt"})   # junk + __MACOSX skipped


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class DuplicateDocsTests(TestCase):
    def test_duplicates_hidden_and_removable(self):
        from apps.knowledge.models import ImportBatch, ImportedDocument
        company = Company.objects.create(name="C")
        mgr = _user(company, ["customers.manage"], "m@c.co")
        self.client.force_login(mgr)
        with tenant_scope(company.id):
            batch = ImportBatch.objects.create(company=company, label="H", created_by=mgr)
            orig = ImportedDocument.objects.create(batch=batch, company=company,
                filename="po.pdf", status=ImportedDocument.Status.COMPLETED, created_by=mgr)
            dup = ImportedDocument.objects.create(batch=batch, company=company,
                filename="po.pdf", status=ImportedDocument.Status.DUPLICATE,
                duplicate_of=orig, created_by=mgr)
        page = self.client.get(f"/import/{batch.pk}/")
        self.assertContains(page, "1 duplicate")              # count shown
        # Remove duplicates → the dup is gone, the original stays.
        self.client.post(f"/import/{batch.pk}/remove-duplicates/")
        with tenant_scope(company.id):
            self.assertFalse(ImportedDocument.objects.filter(pk=dup.pk).exists())
            self.assertTrue(ImportedDocument.objects.filter(pk=orig.pk).exists())
