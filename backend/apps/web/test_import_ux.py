"""Historical-import UX: viewable reconstructed-job detail, date-based import
names, discard of an empty import, and per-type labelled document numbers."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.models import HistoricalJob, ImportBatch, ImportedDocument


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ImportUXTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, ["customers.manage"], "mgr@acme.co")
        self.client.force_login(self.u)

    # 1 — a reconstructed job is viewable in detail (not just the batch list)
    def test_job_detail_page_renders_with_labelled_doc_numbers(self):
        with tenant_scope(self.c.id):
            batch = ImportBatch.objects.create(company=self.c)
            job = HistoricalJob.objects.create(company=self.c, batch=batch,
                                               title="Pump overhaul", customer_name="ABC Mining")
            ImportedDocument.objects.create(
                company=self.c, batch=batch, job=job, doc_type="customer_po",
                filename="po.pdf", text="Customer Purchase Order Number: PO-2291")
        r = self.client.get(f"/import/job/{job.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ABC Mining")
        self.assertContains(r, "PO number")          # labelled by type
        self.assertContains(r, "PO-2291")            # the extracted number

    # 4 — each document number is labelled for what it is
    def test_reference_label_and_extraction_by_type(self):
        with tenant_scope(self.c.id):
            batch = ImportBatch.objects.create(company=self.c)
            po = ImportedDocument.objects.create(company=self.c, batch=batch,
                    doc_type="customer_po", filename="po.pdf",
                    text="Purchase Order No PO-2291 dated ...")
            inv = ImportedDocument.objects.create(company=self.c, batch=batch,
                    doc_type="invoice", filename="inv.pdf",
                    text="Tax Invoice Number: INV-5567")
            dn = ImportedDocument.objects.create(company=self.c, batch=batch,
                    doc_type="delivery_note", filename="dn.pdf",
                    text="Delivery Note: DN-88")
        self.assertEqual(po.reference_label, "PO number")
        self.assertEqual(po.extracted_reference, "PO-2291")
        self.assertEqual(inv.reference_label, "Invoice number")
        self.assertEqual(inv.extracted_reference, "INV-5567")
        self.assertEqual(dn.reference_label, "Delivery note no")
        self.assertEqual(dn.extracted_reference, "DN-88")

    # 1b — the import centre lists reconstructed jobs, each linking to its detail
    def test_import_centre_lists_clickable_jobs(self):
        with tenant_scope(self.c.id):
            batch = ImportBatch.objects.create(company=self.c)
            job = HistoricalJob.objects.create(company=self.c, batch=batch,
                                               title="Pump overhaul", customer_name="ABC Mining")
        r = self.client.get("/import/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Reconstructed jobs")
        self.assertContains(r, f"/import/job/{job.pk}/")   # clickable to detail

    # 2 — an unnamed import shows a date-based name, not "Untitled import"
    def test_import_uses_date_name_not_untitled(self):
        with tenant_scope(self.c.id):
            batch = ImportBatch.objects.create(company=self.c)
        self.assertTrue(batch.display_label.startswith("Import "))
        r = self.client.get("/import/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Untitled import")
        self.assertContains(r, batch.display_label)

    # 3 — a user can discard an EMPTY import; a non-empty one is protected
    def test_discard_empty_import_only(self):
        with tenant_scope(self.c.id):
            empty = ImportBatch.objects.create(company=self.c)
            full = ImportBatch.objects.create(company=self.c)
            ImportedDocument.objects.create(company=self.c, batch=full,
                                            doc_type="invoice", filename="x.pdf")
        r = self.client.post(f"/import/{empty.pk}/delete/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ImportBatch.all_objects.get(pk=empty.pk).is_deleted)

        self.client.post(f"/import/{full.pk}/delete/")
        self.assertFalse(ImportBatch.all_objects.get(pk=full.pk).is_deleted)   # protected
