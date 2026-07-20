"""Manager web tests: auth gate, dashboard renders, the Financial Golden Rule on
the HTML surface (money hidden from non-finance users), and tenant isolation."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings

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


#: These exercise the pipeline's plumbing, not the AI. Pinning the provider off
#: keeps them fast, deterministic, and free — without it they reach a live API
#: the moment a developer configures a key.
NO_AI = override_settings(AI_PROVIDER="claude", ANTHROPIC_API_KEY="",
                          OPENAI_API_KEY="", GEMINI_API_KEY="")


@NO_AI
class RFQTests(TestCase):
    """The RFQ front door: upload → extract → review → approve → quotation."""

    def test_upload_requires_permission(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.rfq.models import RFQDocument
        c = make_company()
        pdf = SimpleUploadedFile("rfq.pdf", b"%PDF-1.4 not-a-real-pdf",
                                 content_type="application/pdf")
        no_perm = user_with(c, ["projects.view"], email="np@lulama.co.za")
        self.client.force_login(no_perm)
        self.client.post("/rfq/upload/", {"file": pdf})
        with tenant_scope(c.id):
            self.assertEqual(RFQDocument.objects.count(), 0)

        pdf2 = SimpleUploadedFile("rfq.pdf", b"%PDF-1.4 not-a-real-pdf",
                                  content_type="application/pdf")
        uploader = user_with(c, ["projects.view", "rfq.upload"], email="up@lulama.co.za")
        self.client.force_login(uploader)
        self.client.post("/rfq/upload/", {"file": pdf2})
        with tenant_scope(c.id):
            self.assertEqual(RFQDocument.objects.count(), 1)  # resilient extraction, no 500

    def test_approve_creates_quotation(self):
        from apps.rfq.models import RFQDocument, RFQStatus
        c = make_company()
        with tenant_scope(c.id):
            rfq = RFQDocument.objects.create(company=c, original_name="Coupa PO",
                                             status=RFQStatus.IN_REVIEW)
            rfq.lines.create(company=c, position=1, description="Steel", qty=12, unit_price=485)
        no_perm = user_with(c, ["projects.view"], email="np@lulama.co.za")
        approver = user_with(c, ["projects.view", "rfq.approve"], email="ap@lulama.co.za")

        self.client.force_login(no_perm)
        self.client.post(f"/rfq/{rfq.id}/approve/", {"client_name": "Sasol"})
        with tenant_scope(c.id):
            self.assertIsNone(RFQDocument.objects.get(id=rfq.id).quotation_id)

        self.client.force_login(approver)
        self.client.post(f"/rfq/{rfq.id}/approve/", {"client_name": "Sasol"})
        with tenant_scope(c.id):
            rfq.refresh_from_db()
            self.assertIsNotNone(rfq.quotation_id)
            self.assertEqual(rfq.status, RFQStatus.APPROVED)


class QuotationTests(TestCase):
    """View, review, edit and download a quotation from the manager web."""

    def _quote(self, company):
        from apps.quotes.services import create_quotation
        return create_quotation(company, None, client_name="Sasol", title="Pump overhaul",
                                lines=[{"description": "Steel", "qty": 12, "unit_price": 485}])

    def test_detail_and_pdf_download(self):
        c = make_company()
        with tenant_scope(c.id):
            q = self._quote(c)
        user = user_with(c, ["projects.view", "finance.view_money"])
        self.client.force_login(user)
        detail = self.client.get(f"/quotations/{q.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, q.number)
        self.assertContains(detail, "Steel")
        # PDF download
        pdf = self.client.get(f"/quotations/{q.id}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn(f"{q.number}.pdf", pdf["Content-Disposition"])

    def test_edit_requires_permission_and_updates_lines(self):
        from apps.quotes.models import QuotationLine
        c = make_company()
        with tenant_scope(c.id):
            q = self._quote(c)
        no_perm = user_with(c, ["projects.view"], email="np@lulama.co.za")
        editor = user_with(c, ["projects.view", "quotes.create"], email="ed@lulama.co.za")

        # no permission → edit page redirects, no change
        self.client.force_login(no_perm)
        self.assertEqual(self.client.get(f"/quotations/{q.id}/edit/").status_code, 302)

        # editor → change a line + add one
        self.client.force_login(editor)
        self.assertEqual(self.client.get(f"/quotations/{q.id}/edit/").status_code, 200)
        self.client.post(f"/quotations/{q.id}/edit/", {
            "client_name": "Sasol Secunda", "title": "Pump overhaul", "site": "Secunda",
            "vat_rate": "15", "notes": "",
            "description": ["Steel lip channel", "Bearing kit"],
            "qty": ["10", "4"], "unit": ["m", "set"], "unit_price": ["500", "1200"],
        })
        with tenant_scope(c.id):
            q.refresh_from_db()
            self.assertEqual(q.client_name, "Sasol Secunda")
            lines = list(QuotationLine.objects.filter(quotation=q).order_by("position"))
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0].description, "Steel lip channel")
            self.assertEqual(lines[1].unit_price, Decimal("1200"))



class UnifiedWorkTests(TestCase):
    """The unified Work engine: standalone work (no project, no compliance gate)
    flows through the same lifecycle as project work — Example 2 (the electrician)."""

    def test_standalone_work_create_start_complete(self):
        from apps.execution.models import Task, TaskStatus
        c = make_company()
        mgr = user_with(c, ["projects.view", "execution.manage"], email="mgr@lulama.co.za")
        self.client.force_login(mgr)

        # New standalone work — no project, no RFQ, no quotation
        resp = self.client.post("/work/new/", {
            "name": "Replace faulty DB board", "origin": "manual",
            "project": "", "is_billable": "on", "client_name": "Corner Cafe",
        })
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(c.id):
            task = Task.objects.get(name="Replace faulty DB board")
            self.assertIsNone(task.project_id)          # standalone
            self.assertTrue(task.is_billable)
            self.assertFalse(task.blocks_on_compliance)  # no project → no gate
            self.assertEqual(task.status, TaskStatus.READY)  # ready immediately

        self.client.post(f"/work/{task.id}/start/")
        with tenant_scope(c.id):
            self.assertEqual(Task.objects.get(id=task.id).status, TaskStatus.IN_PROGRESS)
        self.client.post(f"/work/{task.id}/complete/", {"actual_hours": "2"})
        with tenant_scope(c.id):
            self.assertEqual(Task.objects.get(id=task.id).status, TaskStatus.COMPLETED)

    def test_work_new_requires_permission(self):
        c = make_company()
        viewer = user_with(c, ["projects.view"], email="v@lulama.co.za")
        self.client.force_login(viewer)
        resp = self.client.post("/work/new/", {"name": "X"})
        self.assertEqual(resp.status_code, 302)  # redirected, not created
        from apps.execution.models import Task
        with tenant_scope(c.id):
            self.assertFalse(Task.objects.filter(name="X").exists())

    def test_work_list_shows_all_origins(self):
        c = make_company()
        mgr = user_with(c, ["projects.view", "execution.manage"], email="m2@lulama.co.za")
        with tenant_scope(c.id):
            from apps.execution.services import create_work
            create_work(c, mgr, name="Standalone job", origin="manual")
        self.client.force_login(mgr)
        self.assertContains(self.client.get("/work/"), "Standalone job")
