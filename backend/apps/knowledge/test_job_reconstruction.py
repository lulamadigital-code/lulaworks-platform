"""Historical Job Reconstruction — cluster imported documents that share
references into one proposed past job, for human confirmation."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge import historical_import as imp
from apps.knowledge import job_reconstruction as jr
from apps.knowledge.models import HistoricalJob


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


# A quote, the PO that accepted it, and the invoice — all sharing QT-2024-0100.
_QUOTE = b"QUOTATION\nQuote No: QT-2024-0100\nCustomer: ABC Mining\nPump overhaul\n"
_PO = b"PURCHASE ORDER\nPO Number: PO-556\nYour quote QT-2024-0100 refers\nCustomer: ABC Mining\n"
_INV = b"TAX INVOICE\nInvoice No: INV-99\nAgainst quote QT-2024-0100\nAmount due R120000\n"
_STRAY = b"DELIVERY NOTE\nWaybill WB-777\nSome unrelated delivery\n"


class JobReconstructionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")

    def _batch_with_docs(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "quote.txt", _QUOTE, self.mgr)
            imp.ingest(batch, "po.txt", _PO, self.mgr)
            imp.ingest(batch, "invoice.txt", _INV, self.mgr)
            imp.ingest(batch, "delivery.txt", _STRAY, self.mgr)
        return batch

    def test_clusters_shared_reference_into_one_job(self):
        batch = self._batch_with_docs()
        with tenant_scope(self.company.id):
            jobs = jr.reconstruct_jobs(batch, self.mgr)
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            # The three QT-2024-0100 documents grouped; the stray delivery did not.
            self.assertEqual(job.documents.count(), 3)
            self.assertIn("qt20240100", job.reference)
            self.assertGreater(job.confidence, 0.6)
            self.assertEqual(job.status, HistoricalJob.Status.PROPOSED)

    def test_reconstruct_is_idempotent(self):
        batch = self._batch_with_docs()
        with tenant_scope(self.company.id):
            jr.reconstruct_jobs(batch, self.mgr)
            jr.reconstruct_jobs(batch, self.mgr)
            self.assertEqual(
                HistoricalJob.objects.filter(batch=batch,
                                             status=HistoricalJob.Status.PROPOSED).count(), 1)

    def test_confirm_and_dismiss(self):
        batch = self._batch_with_docs()
        with tenant_scope(self.company.id):
            job = jr.reconstruct_jobs(batch, self.mgr)[0]
            jr.confirm_job(job, self.mgr, decision="confirm")
            job.refresh_from_db()
            self.assertEqual(job.status, HistoricalJob.Status.CONFIRMED)
            # A confirmed job is NOT rebuilt away by a re-run.
            jr.reconstruct_jobs(batch, self.mgr)
            self.assertTrue(HistoricalJob.objects.filter(
                pk=job.pk, status=HistoricalJob.Status.CONFIRMED).exists())
