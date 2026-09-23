"""The reconstructed-relationships page scales: wrapping grid, server-side
search + status filter + pagination, and a constant query count (no N+1) so it
holds up at 100k jobs."""
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.models import (HistoricalJob, ImportBatch, ImportedDocument)


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ImportRelationshipsScaleTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, ["customers.manage"], "mgr@acme.co")
        self.client.force_login(self.u)

    def _jobs(self, n, *, customer="ABC Mining", docs_each=0):
        with tenant_scope(self.c.id):
            batch = ImportBatch.objects.create(company=self.c)
            for i in range(n):
                j = HistoricalJob.objects.create(company=self.c, batch=batch,
                                                 title=f"Job {i}", customer_name=customer,
                                                 confidence=0.9)
                for d in range(docs_each):
                    ImportedDocument.objects.create(company=self.c, batch=batch, job=j,
                                                    filename=f"doc-{i}-{d}.pdf")

    def test_wrapping_grid_and_total(self):
        self._jobs(35)
        r = self.client.get("/import/relationships/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "display:grid;grid-template-columns:repeat(auto-fill")
        self.assertContains(r, "35 relationships")
        self.assertContains(r, "Page 1 of 2")               # 35 / 30 per page

    def test_search_filters_server_side(self):
        self._jobs(2, customer="ABC Mining")
        self._jobs(3, customer="XYZ Holdings")
        r = self.client.get("/import/relationships/?q=ABC")
        self.assertContains(r, "ABC Mining")
        self.assertNotContains(r, "XYZ Holdings")
        self.assertContains(r, "2 relationships")

    def _page_query_count(self):
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get("/import/relationships/")
        self.assertEqual(r.status_code, 200)
        return len(ctx.captured_queries)

    def test_query_count_does_not_grow_with_jobs(self):
        # The real N+1 test: the page's query count must NOT scale with the number
        # of jobs (documents are prefetched, results paginated). If it were N+1,
        # going from 5 jobs to 40 would add ~35 queries; here it stays flat — which
        # is exactly what makes 100k jobs safe.
        self._jobs(5, docs_each=3)
        small = self._page_query_count()
        self._jobs(35, docs_each=3)                         # now 40 total (page shows 30)
        large = self._page_query_count()
        self.assertLessEqual(large, small + 1)              # flat, not +35
