"""Entity Resolution + Context Engine — the keystone services of the Company
Knowledge Engine. These lock the two guarantees the whole AI-OS vision rests
on: we don't create duplicate entities, and we never leak or fabricate."""
from types import SimpleNamespace

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer, CustomerContact
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge import context_engine as ctx
from apps.knowledge import entity_resolution as er
from apps.procurement.models import Supplier


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class EntityResolutionTests(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="Contractor A")
        self.b = Company.objects.create(name="Contractor B")
        with tenant_scope(self.a.id):
            self.abc = Customer.objects.create(
                company=self.a, name="ABC Mining (Pty) Ltd",
                email="accounts@abcmining.co.za", telephone="011 555 1234",
                registration_no="2011/123456/07")
            self.supplier = Supplier.objects.create(
                company=self.a, name="Hydraulics SA", email="sales@hydraulics.co.za")
        with tenant_scope(self.b.id):
            # A DIFFERENT company's customer that happens to share the name.
            Customer.objects.create(company=self.b, name="ABC Mining")

    def test_exact_name_variant_matches(self):
        with tenant_scope(self.a.id):
            r = er.resolve_company("ABC Mining Pty Ltd")
        self.assertEqual(r.verdict, "matched")
        self.assertEqual(r.best.id, str(self.abc.id))

    def test_email_identifies_even_with_odd_name(self):
        with tenant_scope(self.a.id):
            r = er.resolve_company("A.B.C.", email="accounts@abcmining.co.za")
        self.assertEqual(r.verdict, "matched")
        self.assertIn("same email", r.best.reasons)

    def test_similar_name_is_review_not_auto(self):
        with tenant_scope(self.a.id):
            r = er.resolve_company("ABC Mine")
        self.assertEqual(r.verdict, "review")
        self.assertEqual(r.best.id, str(self.abc.id))

    def test_unknown_company_is_new(self):
        with tenant_scope(self.a.id):
            r = er.resolve_company("Zzz Nonexistent Trading")
        self.assertEqual(r.verdict, "new")
        self.assertIsNone(r.best)

    def test_tenant_isolation_supplier(self):
        # The supplier exists only in A — B must never resolve to it.
        with tenant_scope(self.b.id):
            r = er.resolve_company("Hydraulics SA", kind="supplier")
        self.assertEqual(r.verdict, "new")
        with tenant_scope(self.a.id):
            r2 = er.resolve_company("Hydraulics SA", kind="supplier")
        self.assertEqual(r2.verdict, "matched")

    def test_contact_resolution(self):
        with tenant_scope(self.a.id):
            person = CustomerContact.objects.create(
                company=self.a, customer=self.abc, full_name="Thabo Molefe",
                email="thabo@abcmining.co.za", job_title="Procurement")
            r = er.resolve_contact("Thabo  Molefe")
        self.assertEqual(r.verdict, "matched")
        self.assertEqual(r.best.id, str(person.id))


class ContextEngineTests(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="Contractor A")
        self.b = Company.objects.create(name="Contractor B")
        self.money = _user(self.a, ["finance.view_money"], "cfo@a.co")
        self.nomoney = _user(self.a, ["projects.view"], "field@a.co")
        with tenant_scope(self.a.id):
            self.cust = Customer.objects.create(company=self.a, name="ABC Mining")

    def test_context_for_customer(self):
        with tenant_scope(self.a.id):
            c = ctx.context_for(self.nomoney, "customer", self.cust.id)
        self.assertIsNotNone(c)
        self.assertEqual(c.kind, "customer")
        self.assertIn("ABC Mining", c.title)
        self.assertIsInstance(c.related, list)
        self.assertIn("ABC Mining", c.as_prompt_text())

    def test_unknown_kind_returns_none(self):
        with tenant_scope(self.a.id):
            self.assertIsNone(ctx.context_for(self.money, "spaceship", self.cust.id))

    def test_money_gating(self):
        quote = SimpleNamespace(number="Q-100", client_name="ABC",
                                customer=SimpleNamespace(display_name="ABC Mining", name="ABC"),
                                status="draft", created_at="2026-01-01", title="Pump job",
                                total="500000")
        _, _, hidden = ctx._facts("quotation", quote, can_money=False)
        _, _, shown = ctx._facts("quotation", quote, can_money=True)
        self.assertNotIn("Total", hidden)
        self.assertEqual(shown["Total"], "500000")


class EntityResolutionToolTests(TestCase):
    """The resolver is registered in the AI tool registry so agents can dedup —
    behind customers.manage, tenant-scoped like every other tool."""

    def setUp(self):
        self.a = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.a, ["customers.manage"], "mgr@a.co")
        self.viewer = _user(self.a, ["projects.view"], "v@a.co")
        with tenant_scope(self.a.id):
            Customer.objects.create(company=self.a, name="ABC Mining (Pty) Ltd")

    def test_tool_runs_for_permitted_user(self):
        from apps.ai_platform.tools import run_tool
        with tenant_scope(self.a.id):
            out = run_tool("resolve_company", self.mgr, company_name="ABC Mining Pty Ltd")
        self.assertEqual(out["verdict"], "matched")

    def test_tool_denied_without_permission(self):
        from apps.ai_platform.tools import ToolPermissionError, run_tool
        with tenant_scope(self.a.id):
            with self.assertRaises(ToolPermissionError):
                run_tool("resolve_company", self.viewer, company_name="ABC Mining")
