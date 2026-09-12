"""Customer dossier (LulaAI tool) + the 'Historical data' badge provenance."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.models import ImportBatch, ImportedDocument, StagedEntity


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class DossierToolTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["customers.manage"], "mgr@a.co")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")

    def test_dossier_tool_returns_customer(self):
        from apps.ai_platform.tools import run_tool
        with tenant_scope(self.c.id):
            out = run_tool("customer_dossier", self.mgr, customer_name="ABC")
        self.assertTrue(out["found"])
        self.assertEqual(out["name"], "ABC Mining")
        self.assertIn("quotations", out)

    def test_dossier_unknown(self):
        from apps.ai_platform.tools import run_tool
        with tenant_scope(self.c.id):
            out = run_tool("customer_dossier", self.mgr, customer_name="Nope Ltd")
        self.assertFalse(out["found"])


class HistoryBadgeTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["customers.manage", "projects.create"], "mgr@a.co")
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")
            batch = ImportBatch.objects.create(company=self.c, label="H", created_by=self.mgr)
            doc = ImportedDocument.objects.create(batch=batch, company=self.c,
                filename="abc_po.pdf", created_by=self.mgr)
            StagedEntity.objects.create(batch=batch, company=self.c, document=doc,
                kind=StagedEntity.Kind.CUSTOMER, raw_name="ABC Mining",
                verdict=StagedEntity.Verdict.NEW, review_status=StagedEntity.Review.CREATED,
                resolved_id=str(self.cust.pk), created_by=self.mgr)

    def test_badge_shows_with_source_doc(self):
        self.client.force_login(self.mgr)
        r = self.client.get(f"/customers/{self.cust.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Historical data")
        self.assertContains(r, "abc_po.pdf")
