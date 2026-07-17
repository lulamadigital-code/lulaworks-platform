"""Manager web tests: auth gate, dashboard renders, the Financial Golden Rule on
the HTML surface (money hidden from non-finance users), and tenant isolation."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.administration.models import NumberingRule
from apps.compliance.models import ComplianceItem, ComplianceRequirement, ItemStatus
from apps.compliance.services import approve_item
from apps.core.context import tenant_scope
from apps.estimating.models import Estimate, EstimateStatus
from apps.estimating.services import approve_estimate, create_estimate
from apps.finance.models import Invoice
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.procurement.models import GRN, Supplier
from apps.procurement.services import create_purchase_order, three_way_match
from apps.projects.services import award_quotation
from apps.quotes.models import Quotation


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("estimate", "EST"),
                    ("invoice", "INV")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


def user_with(company, codenames, email="u@lulama.co.za"):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for code in codenames:
        p, _ = Permission.objects.get_or_create(codename=code,
                                                 defaults={"module": "x", "label": code})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


def awarded_project(company):
    q = Quotation.objects.create(company=company, number="QT-1", client_name="Sasol",
                                 site="Secunda")
    est = create_estimate(company, None, client_name="Sasol", work_type="pump_overhaul",
                          quotation=q, sections=[{"category": "labour",
                                                  "lines": [{"description": "Fitter", "qty": 100,
                                                             "unit": "hour", "unit_cost": 450}]}])
    approve_estimate(est, None)
    ComplianceRequirement.objects.create(company=company, code="SF", name="Safety File",
                                         category="documentation", source="customer",
                                         is_mandatory=True, applies_when={})
    project = award_quotation(company, None, quotation=q, work_type="pump_overhaul")
    for item in project.compliance_items.filter(is_mandatory=True):
        approve_item(item, None, expiry=date.today() + timedelta(days=365))
    project.refresh_from_db()
    return project


class AuthTests(TestCase):
    def test_dashboard_requires_login(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_login_and_dashboard(self):
        c = make_company()
        with tenant_scope(c.id):
            awarded_project(c)
        user = user_with(c, ["projects.view"])
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Operations dashboard")


class GoldenRuleTests(TestCase):
    def test_money_hidden_from_non_finance_user(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)
        broke = user_with(c, ["projects.view"], email="ops@lulama.co.za")
        rich = user_with(c, ["projects.view", "finance.view_money"], email="fin@lulama.co.za")

        # Dashboard: commercial panel only for the finance user.
        self.client.force_login(broke)
        self.assertNotContains(self.client.get("/"), "Portfolio margin")
        self.client.force_login(rich)
        self.assertContains(self.client.get("/"), "Portfolio margin")

        # Project detail: profitability only for the finance user.
        url = f"/projects/{project.id}/"
        self.client.force_login(broke)
        self.assertNotContains(self.client.get(url), "Gross profit")
        self.client.force_login(rich)
        detail = self.client.get(url)
        self.assertContains(detail, "Gross profit")
        self.assertContains(detail, "Profit forecast")


class TenantIsolationTests(TestCase):
    def test_cross_tenant_project_404(self):
        a = make_company("A")
        b = make_company("B")
        with tenant_scope(a.id):
            project = awarded_project(a)
        intruder = user_with(b, ["projects.view", "finance.view_money"], email="x@b.co.za")
        self.client.force_login(intruder)
        resp = self.client.get(f"/projects/{project.id}/")
        self.assertEqual(resp.status_code, 404)


class ReadinessPartialTests(TestCase):
    def test_htmx_readiness_partial_renders(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)
        user = user_with(c, ["projects.view"])
        self.client.force_login(user)
        resp = self.client.get(f"/projects/{project.id}/readiness/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ready for site")


class EstimatesTests(TestCase):
    def test_list_and_detail_money_gated(self):
        c = make_company()
        with tenant_scope(c.id):
            awarded_project(c)
            estimate = Estimate.objects.first()
        broke = user_with(c, ["projects.view"], email="ops@lulama.co.za")
        rich = user_with(c, ["projects.view", "finance.view_money"], email="fin@lulama.co.za")

        self.client.force_login(rich)
        self.assertContains(self.client.get("/estimates/"), estimate.number)
        self.assertContains(self.client.get(f"/estimates/{estimate.id}/"), "Selling price")
        self.client.force_login(broke)
        self.assertNotContains(self.client.get(f"/estimates/{estimate.id}/"), "Selling price")

    def test_approve_requires_permission(self):
        c = make_company()
        with tenant_scope(c.id):
            awarded_project(c)
            est = create_estimate(c, None, client_name="X", sections=[
                {"category": "labour", "lines": [{"description": "L", "qty": 1, "unit_cost": 10}]}])
            est.status = EstimateStatus.AWAITING_APPROVAL
            est.save(update_fields=["status"])
        no_perm = user_with(c, ["projects.view"], email="np@lulama.co.za")
        approver = user_with(c, ["estimating.approve"], email="ap@lulama.co.za")

        self.client.force_login(no_perm)
        self.client.post(f"/estimates/{est.id}/approve/")
        with tenant_scope(c.id):
            self.assertEqual(Estimate.objects.get(id=est.id).status, EstimateStatus.AWAITING_APPROVAL)
        self.client.force_login(approver)
        self.client.post(f"/estimates/{est.id}/approve/")
        with tenant_scope(c.id):
            self.assertEqual(Estimate.objects.get(id=est.id).status, EstimateStatus.APPROVED)


class ProcurementTests(TestCase):
    def test_suppliers_and_po_pages(self):
        c = make_company()
        with tenant_scope(c.id):
            s = Supplier.objects.create(company=c, name="NJR Steel", performance_score=80)
            po = create_purchase_order(c, None, supplier=s,
                                       lines=[{"description": "Steel", "qty": 12, "unit_price": 485}])
        user = user_with(c, ["projects.view", "finance.view_money"])
        self.client.force_login(user)
        self.assertContains(self.client.get("/suppliers/"), "NJR Steel")
        detail = self.client.get(f"/purchase-orders/{po.id}/")
        self.assertContains(detail, "3-way match")
        self.assertContains(detail, po.number)


class CommercialAndLulamaTests(TestCase):
    def test_commercial_requires_finance(self):
        c = make_company()
        with tenant_scope(c.id):
            awarded_project(c)
        broke = user_with(c, ["projects.view"], email="ops@lulama.co.za")
        rich = user_with(c, ["projects.view", "finance.view_money"], email="fin@lulama.co.za")
        self.client.force_login(broke)
        self.assertEqual(self.client.get("/commercial/").status_code, 302)  # bounced
        self.client.force_login(rich)
        self.assertContains(self.client.get("/commercial/"), "Aging")

    def test_lulama_ask_renders_draft(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)
        user = user_with(c, ["projects.view", "ai.generate"])
        self.client.force_login(user)
        self.assertEqual(self.client.get("/lulama/").status_code, 200)
        resp = self.client.post("/lulama/", {"request": "Prepare this project",
                                             "project": str(project.id)})
        self.assertContains(resp, "Consolidated draft")
        self.assertContains(resp, "confidence")

    def test_lulama_requires_ai_permission(self):
        c = make_company()
        user = user_with(c, ["projects.view"])
        self.client.force_login(user)
        self.assertEqual(self.client.get("/lulama/").status_code, 302)  # bounced


class ActionsTests(TestCase):
    """The manager web is an operating tool: clear the gate, receive goods, bill,
    revise — each action permission-gated and reflected in the domain."""

    def test_compliance_item_approve_opens_gate(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)
            item = project.compliance_items.first()
            item.status = ItemStatus.MISSING
            item.expiry = None
            item.save(update_fields=["status", "expiry"])
        no_perm = user_with(c, ["projects.view"], email="np@lulama.co.za")
        approver = user_with(c, ["projects.view", "compliance.override"], email="so@lulama.co.za")

        self.client.force_login(no_perm)
        self.client.post(f"/compliance-items/{item.id}/approve/", {"expiry": "2027-06-30"})
        with tenant_scope(c.id):
            self.assertEqual(ComplianceItem.objects.get(id=item.id).status, ItemStatus.MISSING)

        self.client.force_login(approver)
        self.client.post(f"/compliance-items/{item.id}/approve/", {"expiry": "2027-06-30"})
        with tenant_scope(c.id):
            self.assertEqual(ComplianceItem.objects.get(id=item.id).status, ItemStatus.APPROVED)

    def test_override_requires_reason_and_permission(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)
            project.compliance_items.update(status=ItemStatus.MISSING, expiry=None)
        approver = user_with(c, ["projects.view", "compliance.override"], email="so@lulama.co.za")
        self.client.force_login(approver)
        # no reason → no override
        self.client.post(f"/projects/{project.id}/override/", {"reason": ""})
        with tenant_scope(c.id):
            self.assertFalse(project.compliance_overrides.exists())
        # with reason → overridden
        self.client.post(f"/projects/{project.id}/override/", {"reason": "client accepted risk"})
        with tenant_scope(c.id):
            self.assertTrue(project.compliance_overrides.exists())

    def test_po_receive_creates_grn_and_updates_match(self):
        c = make_company()
        with tenant_scope(c.id):
            s = Supplier.objects.create(company=c, name="NJR")
            po = create_purchase_order(c, None, supplier=s,
                                       lines=[{"description": "Steel", "qty": 12, "unit_price": 485}])
            line = po.lines.first()
        user = user_with(c, ["projects.view", "procurement.manage"], email="buy@lulama.co.za")
        self.client.force_login(user)
        self.client.post(f"/purchase-orders/{po.id}/receive/", {f"qty_{line.id}": "12"})
        with tenant_scope(c.id):
            self.assertTrue(GRN.objects.filter(purchase_order=po).exists())
            self.assertEqual(po.lines.first().qty_received, Decimal("12"))
            # quantity variance is now cleared in the 3-way match
            match = three_way_match(po)
            self.assertFalse(any(v["type"] == "quantity" for v in match["variances"]))

    def test_estimate_revise_creates_new_version(self):
        c = make_company()
        with tenant_scope(c.id):
            awarded_project(c)
            est = Estimate.objects.first()
        user = user_with(c, ["estimating.manage"], email="est@lulama.co.za")
        self.client.force_login(user)
        self.client.post(f"/estimates/{est.id}/revise/", {"reason": "scope cut"})
        with tenant_scope(c.id):
            self.assertEqual(Estimate.objects.filter(number=est.number).count(), 2)

    def test_progress_claim_and_payment(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)  # budget auto-created from approved estimate
        finance = user_with(c, ["projects.view", "finance.view_money", "finance.manage"],
                            email="fin@lulama.co.za")
        self.client.force_login(finance)
        self.client.post(f"/projects/{project.id}/progress-claim/",
                         {"percent_complete": "40", "retention": "10"})
        with tenant_scope(c.id):
            inv = Invoice.objects.filter(project=project, is_progress_claim=True).first()
            self.assertIsNotNone(inv)
            outstanding = inv.outstanding
        self.client.post(f"/invoices/{inv.id}/payment/", {"amount": str(outstanding)})
        with tenant_scope(c.id):
            inv.refresh_from_db()
            self.assertEqual(inv.status, "paid")
