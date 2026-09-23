"""The reconstructed-relationships page renders its cards as a wrapping CSS grid
(not a single crushed flex row)."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.models import HistoricalJob, ImportBatch


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ImportRelationshipsPageTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, ["customers.manage"], "mgr@acme.co")
        with tenant_scope(self.c.id):
            batch = ImportBatch.objects.create(company=self.c)
            for i in range(3):
                HistoricalJob.objects.create(company=self.c, batch=batch,
                                             title=f"Job {i}", customer_name="ABC Mining",
                                             confidence=0.9)
        self.client.force_login(self.u)

    def test_page_renders_cards_as_wrapping_grid(self):
        r = self.client.get("/import/relationships/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ABC Mining")
        # The container is a real CSS grid (auto-fill wrap), not the flex .grid
        # class that would crush every card into one non-wrapping row.
        self.assertContains(r, "display:grid;grid-template-columns:repeat(auto-fill")

    def test_requires_permission(self):
        other = _user(self.c, ["projects.view"], "worker@acme.co")
        self.client.force_login(other)
        r = self.client.get("/import/relationships/")
        self.assertEqual(r.status_code, 302)          # redirected away
