"""Manager web tests: auth gate, dashboard renders, the Financial Golden Rule on
the HTML surface (money hidden from non-finance users), and tenant isolation."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
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
        # Editing needs quotes.create; the on-screen preview is open to a viewer;
        # taking a copy (download) needs quotes.download.
        user = user_with(c, ["projects.view", "finance.view_money", "quotes.create"])
        self.client.force_login(user)
        detail = self.client.get(f"/quotations/{q.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, q.number)
        self.assertContains(self.client.get(f"/quotations/{q.id}/?edit=1"), "Steel")
        # The inline preview is allowed without the download permission…
        self.assertEqual(self.client.get(f"/quotations/{q.id}/pdf/?inline=1").status_code, 200)
        # …but taking a copy is forbidden without it.
        self.assertEqual(self.client.get(f"/quotations/{q.id}/pdf/").status_code, 403)

        downloader = user_with(c, ["quotes.create", "quotes.download"],
                               email="dl@lulama.co.za")
        self.client.force_login(downloader)
        pdf = self.client.get(f"/quotations/{q.id}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn(f"{q.number}.pdf", pdf["Content-Disposition"])

    def test_edit_requires_permission_and_updates_lines(self):
        from apps.quotes.models import QuotationLine
        c = make_company()
        with tenant_scope(c.id):
            q = self._quote(c)
        no_perm = user_with(c, ["projects.view"], email="np@lulama.co.za")
        editor = user_with(c, ["projects.view", "quotes.create"], email="ed@lulama.co.za")

        # Editing opens the guided create page (edit == create). It needs
        # quotes.create: a viewer is bounced, an editor gets the prefilled page.
        self.client.force_login(no_perm)
        response = self.client.get(f"/quotations/{q.id}/edit/")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(str(q.id), response["Location"])     # sent to the list

        self.client.force_login(editor)
        response = self.client.get(f"/quotations/{q.id}/edit/")
        self.assertEqual(response.status_code, 200)            # the create-style editor
        self.assertContains(response, "Save changes")

        # Line-level costing (cost, markup) still lives on the HTMX builder
        # endpoints, which remain available.
        self.client.post(f"/quotations/{q.id}/lines/", {
            "description": "Bearing kit", "qty": "4", "unit": "set",
            "unit_cost": "800", "markup_pct": "50",
        })
        with tenant_scope(c.id):
            q.refresh_from_db()
            line = QuotationLine.objects.get(quotation=q, description="Bearing kit")
            self.assertEqual(line.qty, Decimal("4"))
            self.assertEqual(line.unit_cost, Decimal("800"))      # costing kept
            self.assertEqual(line.effective_unit_price, Decimal("1200.00"))



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


class EveryPageLoadsTests(TestCase):
    """Smoke-test every GET page in the manager web.

    Added after a NameError shipped on the main quotation-creation route: an
    import was removed while retiring a different view, and 321 passing tests
    said nothing because none of them opened that page. A missing import, a
    renamed field or a broken template is a 500, and this catches all three for
    the cost of one test.

    It asserts "not a server error" rather than "200" — a redirect is a
    legitimate answer for a permission gate or a retired route.
    """

    def setUp(self):
        from apps.customers.services import create_customer
        from apps.execution.services import create_work
        from apps.quotes.services import ensure_quotation_types

        self.company = make_company()
        # A user holding everything, so pages are exercised rather than bounced.
        self.user = user_with(self.company, [
            "projects.view", "projects.create", "quotes.create", "rfq.upload",
            "rfq.approve", "estimating.manage", "estimating.approve",
            "procurement.manage", "po.approve", "finance.view_money",
            "finance.manage", "execution.manage", "compliance.manage",
            "compliance.override", "ai.generate", "users.invite", "company.manage",
            "quotes.approve", "quotes.download",
        ], email="everything@lulama.co.za")

        with tenant_scope(self.company.id):
            ensure_quotation_types(self.company)
            self.customer = create_customer(self.company, self.user,
                                            name="Harmony Mining")
            self.contact = self.customer.contacts.create(
                company=self.company, full_name="Sarah Brown",
                email="sarah@harmony.co.za")
            self.quote = Quotation.objects.create(
                company=self.company, number="QT-SMOKE", client_name="Harmony Mining",
                customer=self.customer)
            self.quote.lines.create(company=self.company, position=1,
                                    description="A line", qty=1, unit_price=100)
            self.task = create_work(self.company, self.user, name="Smoke test work")
        self.membership = Membership.objects.get(company=self.company, user=self.user)
        self.client.force_login(self.user)

    def _urls(self):
        from django.urls import reverse
        simple = ["dashboard", "work", "work_new", "rfq", "quotations",
                  "quotation_new", "projects", "estimates", "suppliers",
                  "purchase_orders", "commercial", "lulama", "people",
                  "company_profile", "company_hours_page", "customers",
                  "notifications", "profile", "change_password"]
        urls = [(name, reverse(f"web:{name}")) for name in simple]

        with_pk = [
            ("quotation_detail", self.quote.id),
            ("quotation_edit", self.quote.id),        # retired → redirect
            ("quotation_suggest", self.quote.id),
            ("customer_detail", self.customer.id),
            ("customer_contact_detail", self.contact.id),
            ("work_detail", self.task.id),
            ("work_decompose", self.task.id),
            ("person_detail", self.membership.id),
        ]
        urls += [(name, reverse(f"web:{name}", args=[pk])) for name, pk in with_pk]
        return urls

    def test_no_page_raises_a_server_error(self):
        broken = []
        for name, url in self._urls():
            response = self.client.get(url)
            if response.status_code >= 500:
                broken.append(f"{name} ({url}) → {response.status_code}")
        self.assertEqual(broken, [], f"pages returning a server error: {broken}")

    def test_no_page_leaks_template_syntax(self):
        """A wrapped {# #} comment renders as literal text on the page.

        It happened twice — once in the sidebar, once on the quotation detail
        page — and both times a human had to spot it in a screenshot. Django
        gives no warning, so the only thing that catches it is looking at the
        output, which is what this does.
        """
        leaking = []
        for name, url in self._urls():
            response = self.client.get(url)
            if response.status_code != 200:
                continue
            body = response.content.decode()
            for token in ("{#", "#}", "{%", "%}", "{{"):
                if token in body:
                    leaking.append(f"{name} leaks {token!r}")
        self.assertEqual(leaking, [], f"unrendered template syntax: {leaking}")

    def test_creating_a_blank_quotation_works(self):
        """The exact route that broke: a blank quotation, end to end."""
        from apps.quotes.models import Quotation, QuotationType

        with tenant_scope(self.company.id):
            qtype = QuotationType.objects.get(key="plant_hire")
            before = Quotation.objects.count()

        response = self.client.post("/quotations/new/", {
            "method": "blank",
            "customer": str(self.customer.id),
            "quotation_type": str(qtype.id),
            "title": "Crane hire",
            "site": "Plant 1",
            "vat_mode": "exclusive",
        })
        self.assertEqual(response.status_code, 302)

        with tenant_scope(self.company.id):
            self.assertEqual(Quotation.objects.count(), before + 1)
            created = Quotation.objects.order_by("-created_at").first()
            self.assertEqual(created.customer_id, self.customer.id)
            self.assertEqual(created.quotation_type_id, qtype.id)
            # The type seeds its sections, so the estimator starts with a shape.
            self.assertTrue(created.sections.exists())

    def test_copying_a_quotation_works(self):
        from apps.quotes.models import Quotation

        with tenant_scope(self.company.id):
            before = Quotation.objects.count()
        response = self.client.post("/quotations/new/", {
            "method": "copy", "source": str(self.quote.id),
        })
        self.assertEqual(response.status_code, 302)
        with tenant_scope(self.company.id):
            self.assertEqual(Quotation.objects.count(), before + 1)


class QuotationCreationWorkflowTests(TestCase):
    """The guided create page (Module 5): the standard estimator workflow.

    Customer → contact → job type → basics → scope → items → attachments →
    create. These cover the parts a unit test can hold: the number format, the
    contact and vendor snapshot, pasted items, attached files, and that only an
    administrator may hand out a number by hand.
    """

    def setUp(self):
        from apps.customers.services import create_customer
        from apps.quotes.models import QuotationType
        from apps.quotes.services import ensure_quotation_types

        self.company = make_company(name="Harmony Works")
        self.user = user_with(self.company, ["quotes.create"],
                              email="estimator@harmony.co.za")
        with tenant_scope(self.company.id):
            ensure_quotation_types(self.company)
            self.customer = create_customer(self.company, self.user,
                                            name="Sasol", vendor_number="V-778")
            self.contact = self.customer.contacts.create(
                company=self.company, full_name="Thabo Nkosi",
                job_title="Buyer", email="thabo@sasol.com", telephone="011 555 0100")
            self.plant_hire = QuotationType.objects.get(company=self.company,
                                                        key="plant_hire")
        self.client.force_login(self.user)

    def _post(self, follow=False, **overrides):
        data = {
            "method": "blank",
            "customer": str(self.customer.id),
            "quotation_type": str(self.plant_hire.id),
            "title": "Crane hire",
            "site": "Plant 3",
            "vat_mode": "exclusive",
        }
        data.update(overrides)
        return self.client.post("/quotations/new/", data, follow=follow)

    def _latest(self):
        with tenant_scope(self.company.id):
            return Quotation.objects.order_by("-created_at").first()

    def test_number_is_two_letters_then_six_digits(self):
        self.assertEqual(self._post().status_code, 302)
        self.assertRegex(self._latest().number, r"^[A-Z]{2,4}\d{6}$")

    def test_contact_and_vendor_are_captured(self):
        self._post(contact=str(self.contact.id))
        quote = self._latest()
        self.assertEqual(quote.contact_id, self.contact.id)
        self.assertEqual(quote.vendor_number, "V-778")  # snapshot from the customer

    def test_pasted_items_become_lines(self):
        self._post(pasted_items="Supply crane\t1\tday\t18500\nRigging\t2\tshift\t4200")
        quote = self._latest()
        with tenant_scope(self.company.id):
            self.assertEqual(quote.lines.count(), 2)

    def test_attached_documents_are_saved(self):
        """One upload control takes the scope and any drawings/BOQ/photos."""
        self._post(documents=[
            SimpleUploadedFile("scope.txt", b"replace the rollers"),
            SimpleUploadedFile("drawing.pdf", b"%PDF-1.4 fake"),
        ])
        quote = self._latest()
        with tenant_scope(self.company.id):
            self.assertEqual(quote.documents.count(), 2)
            self.assertTrue(all(d.doc_type == "attachment"
                                for d in quote.documents.all()))

    def test_edit_uses_the_same_creation_page_prefilled(self):
        # Editing a quotation opens the SAME guided create page, prefilled.
        quote = self._post()  # returns 302; fetch the created one
        q = self._latest()
        resp = self.client.get(f"/quotations/{q.id}/edit/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"Edit {q.number}")     # same page, edit heading
        self.assertContains(resp, "Save changes")         # not "Create quotation"
        self.assertContains(resp, "Customer")             # the creation components
        self.assertContains(resp, "Job type")
        self.assertContains(resp, 'value="Crane hire"')   # title prefilled

    def test_saving_the_edit_updates_the_quotation_in_place(self):
        self._post(pasted_items="Old line\t1\tday\t100")
        q = self._latest()
        before = self._latest_count()
        resp = self.client.post(f"/quotations/{q.id}/edit/", {
            "method": "blank",
            "customer": str(self.customer.id),
            "quotation_type": str(self.plant_hire.id),
            "title": "Crane hire REVISED",
            "site": "Plant 9",
            "vat_mode": "exclusive",
            "pasted_items": "New line\t2\tday\t250",
        })
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            q.refresh_from_db()
            self.assertEqual(q.title, "Crane hire REVISED")   # updated in place
            self.assertEqual(q.lines.count(), 1)              # lines replaced
            self.assertEqual(q.lines.first().description, "New line")
        self.assertEqual(self._latest_count(), before)        # no new quotation

    def _latest_count(self):
        with tenant_scope(self.company.id):
            return Quotation.objects.count()

    def test_number_is_never_taken_from_the_form(self):
        """The number is system-allocated; a value posted by hand is ignored."""
        self._post(number="ZZ999999")
        created = self._latest()
        self.assertNotEqual(created.number, "ZZ999999")
        self.assertRegex(created.number, r"^[A-Z]{2,4}\d{6}$")

    def test_reminds_to_set_terms_when_none_are_configured(self):
        # No quotation terms on file → the estimator is nudged to add them.
        resp = self._post(follow=True)
        self.assertContains(resp, "No quotation terms &amp; conditions are set")

    def test_no_reminder_once_terms_are_configured(self):
        from apps.administration.models import CompanySettings
        with tenant_scope(self.company.id):
            row, _ = CompanySettings.objects.get_or_create(company=self.company)
            row.quotation_terms = "Prices valid for 30 days."
            row.save()
        resp = self._post(follow=True)
        self.assertNotContains(resp, "No quotation terms &amp; conditions are set")

    def test_page_offers_contacts_for_each_customer(self):
        resp = self.client.get("/quotations/new/")
        self.assertEqual(resp.status_code, 200)
        # The contact map is embedded so the page can filter without a round trip.
        self.assertContains(resp, "Thabo Nkosi")
        self.assertContains(resp, str(self.contact.id))

    def test_live_extraction_endpoint_returns_items_and_suggestions(self):
        """The create page reads the scope as the estimator types via this
        stateless endpoint — items to fill the grid, related items to suggest."""
        resp = self.client.post("/quotations/extract/", {
            "scope": "Supply and install 20 conveyor rollers\n40 bearings",
            "type_key": "supply",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        descs = [i["description"].lower() for i in data["items"]]
        self.assertIn("bearings", descs)
        self.assertIn("Installation labour", data["suggestions"])
        # Stateless — nothing was written.
        with tenant_scope(self.company.id):
            self.assertEqual(Quotation.objects.count(), 0)

    def test_live_extraction_requires_the_create_permission(self):
        outsider = user_with(self.company, ["projects.view"],
                             email="nope@harmony.co.za")
        self.client.force_login(outsider)
        resp = self.client.post("/quotations/extract/", {"scope": "10 gaskets"})
        self.assertEqual(resp.status_code, 403)


class QuotationReviewWorkflowTests(TestCase):
    """Module 5 details refactor: the detail page is a review workspace, the
    editor lives behind ?edit=1, finalize locks editing, and a PO is attached
    once the quotation is out with the customer."""

    def setUp(self):
        from apps.customers.services import create_customer
        from apps.quotes.services import create_quotation, ensure_quotation_types

        self.company = make_company(name="Harmony Works")
        self.user = user_with(self.company, [
            "quotes.create", "quotes.approve", "quotes.download",
            "finance.view_money", "company.manage",
            "projects.create"], email="mgr@harmony.co.za")
        with tenant_scope(self.company.id):
            ensure_quotation_types(self.company)
            self.customer = create_customer(self.company, self.user, name="Sasol",
                                            vendor_number="V-1")
            self.quote = create_quotation(self.company, self.user,
                                          client_name="Sasol", title="Crane hire")
            self.quote.customer = self.customer
            self.quote.site = "Plant 3"
            self.quote.save()
            self.quote.lines.create(company=self.company, position=1,
                                    description="Crane", qty=1, unit_price=1000)
        self.client.force_login(self.user)

    def _url(self, suffix=""):
        return f"/quotations/{self.quote.id}/{suffix}"

    def _set_status(self, status):
        with tenant_scope(self.company.id):
            self.quote.status = status
            self.quote.save(update_fields=["status"])

    def test_review_is_the_default_and_shows_actions_not_editors(self):
        resp = self.client.get(self._url())
        self.assertContains(resp, "Quotation preview")     # the PDF-style review
        self.assertContains(resp, "Approve")               # a lifecycle action
        self.assertNotContains(resp, "Add a line")         # editor lives elsewhere
        self.assertNotContains(resp, "Add many items at once")

    def test_edit_mode_shows_the_builder(self):
        resp = self.client.get(self._url("?edit=1"))
        self.assertContains(resp, "Editing")
        self.assertContains(resp, "Add a line")

    def test_create_only_user_cannot_approve(self):
        # Separation of duties: quotes.create prepares; approving needs
        # quotes.approve. A create-only user is refused and the quote stays draft.
        creator = user_with(self.company, ["quotes.create"], email="creator@harmony.co.za")
        self.client.force_login(creator)
        self.client.post(self._url("status/"), {"status": "approved"})
        with tenant_scope(self.company.id):
            self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, "draft")

    def test_approve_locks_editing(self):
        # Approve is the final step (no separate finalize): it locks the quote.
        self.client.post(self._url("status/"), {"status": "approved"})
        with tenant_scope(self.company.id):
            self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, "approved")
        self.assertTrue(self.quote.is_finalized)
        self.assertFalse(self.quote.is_editable)
        # ?edit=1 no longer yields the builder — it falls back to review.
        resp = self.client.get(self._url("?edit=1"))
        self.assertNotContains(resp, "Add a line")
        # And a direct edit POST is refused by the guard.
        self.client.post(self._url("lines/"), {"action": "add", "description": "Sneak"})
        with tenant_scope(self.company.id):
            self.assertEqual(self.quote.lines.count(), 1)   # unchanged

    def test_create_revision_leaves_the_original_untouched(self):
        self._set_status("issued")
        before = self._count_quotes()
        resp = self.client.post(self._url("revise/"), {"reason": "price change"})
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, "issued")       # original is as it was
        self.assertEqual(self._count_quotes(), before + 1)

    def _count_quotes(self):
        with tenant_scope(self.company.id):
            return Quotation.objects.count()

    def test_excel_export_returns_a_spreadsheet(self):
        resp = self.client.get(self._url("excel/"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
        self.assertTrue(resp["Content-Disposition"].endswith('.xlsx"'))

    def test_po_is_saved_by_extracting_fields_from_the_document(self):
        # The attach form is just a file + Save; the PO number/date/value/terms
        # are read off the uploaded document, nothing typed.
        self._set_status("approved")
        po_doc = SimpleUploadedFile(
            "po.txt",
            b"PURCHASE ORDER\nPO Number: 4500123456\nDate: 2026-07-01\n"
            b"Total: R1150.00\nPayment Terms: 30 days\n",
            content_type="text/plain")
        resp = self.client.post(self._url("po/"), {"document": po_doc})
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            po = self.quote.customer_pos.first()
        self.assertIsNotNone(po)
        self.assertEqual(po.po_number, "4500123456")     # read off the document
        self.assertTrue(po.document)                       # the file is stored

    def test_po_upload_auto_creates_and_opens_work(self):
        # The commercial → operational hand-off: a linked PO turns the quotation
        # into a project with phases and tasks, and opens the Work Details page.
        self._set_status("approved")
        po_doc = SimpleUploadedFile(
            "po.txt",
            b"PURCHASE ORDER\nPO Number: 4500123456\nDate: 2026-07-01\nTotal: R1000.00\n",
            content_type="text/plain")
        resp = self.client.post(self._url("po/"), {"document": po_doc})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/projects/", resp.url)              # opened Work Details
        with tenant_scope(self.company.id):
            project = self.quote.projects.first()
            self.assertIsNotNone(project)
            self.assertTrue(project.phases.exists())
            self.assertTrue(project.tasks.exists())

    def test_start_work_button_creates_work_without_a_po(self):
        self._set_status("approved")
        resp = self.client.post(self._url("start-work/"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/projects/", resp.url)
        with tenant_scope(self.company.id):
            self.assertTrue(self.quote.projects.exists())

    def test_a_document_that_is_not_a_po_is_rejected(self):
        # A valid-type file with no PO signal (no number/value/date/terms) must
        # not be recorded as a bogus purchase order.
        self._set_status("approved")
        not_a_po = SimpleUploadedFile(
            "holiday-photo-notes.txt",
            b"Just some notes about the weekend. Nothing procurement here.\n",
            content_type="text/plain")
        resp = self.client.post(self._url("po/"), {"document": not_a_po})
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            self.assertFalse(self.quote.customer_pos.exists())   # nothing saved

    def test_attach_po_requires_a_document(self):
        self._set_status("approved")
        resp = self.client.post(self._url("po/"), {})     # no file
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            self.assertFalse(self.quote.customer_pos.exists())

    def test_executable_po_upload_is_rejected(self):
        # The server rejects a disallowed file type regardless of the browser.
        self._set_status("approved")
        evil = SimpleUploadedFile("payload.exe", b"MZ...", content_type="application/octet-stream")
        resp = self.client.post(self._url("po/"), {"document": evil})
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            self.assertFalse(self.quote.customer_pos.exists())   # nothing saved

    def test_approval_and_download_are_audited_with_user_and_ip(self):
        from apps.administration.models import AuditLog
        # Approve, then download the PDF — both land in the immutable audit log.
        self.client.post(self._url("status/"), {"status": "approved"})
        self.client.get(self._url("pdf/"))                    # real download
        self.client.get(self._url("pdf/") + "?inline=1")      # preview — not logged
        with tenant_scope(self.company.id):
            actions = list(AuditLog.objects.filter(
                entity_id=self.quote.id).values_list("action", flat=True))
            row = AuditLog.objects.filter(action="quotation.approved").first()
        self.assertIn("quotation.approved", actions)
        self.assertIn("quotation.pdf_downloaded", actions)
        self.assertEqual(actions.count("quotation.pdf_downloaded"), 1)  # preview excluded
        self.assertEqual(row.user_id, self.user.id)
        self.assertEqual(row.company_id, self.company.id)
        self.assertTrue(row.ip_address)

    def test_upload_po_button_appears_on_the_action_bar_once_approved(self):
        # Hidden while draft; the "Upload purchase order" action appears once
        # approved, on the same line as Create tax invoice / delivery note.
        self.assertNotContains(self.client.get(self._url()), "Upload purchase order")
        self._set_status("approved")
        self.assertContains(self.client.get(self._url()), "Upload purchase order")

    def test_download_and_excel_hidden_until_approved(self):
        # Draft: Approve is offered, but no customer-facing outputs, and no
        # Finalize or Send (both removed — Approve is the final step).
        draft = self.client.get(self._url())
        self.assertNotContains(draft, "Download PDF")
        self.assertNotContains(draft, "Export Excel")
        self.assertContains(draft, "Approve")
        self.assertNotContains(draft, "Finalize")
        # Approved: the outputs appear; still no Finalize or Send.
        self._set_status("approved")
        final = self.client.get(self._url())
        self.assertContains(final, "Download PDF")
        self.assertContains(final, "Export Excel")
        self.assertNotContains(final, "Finalize")
        self.assertNotContains(final, "Send to customer")

    def test_commercial_timeline_is_shown_with_stages(self):
        resp = self.client.get(self._url())
        self.assertContains(resp, "Commercial timeline")
        self.assertContains(resp, "Quotation created")
        self.assertContains(resp, "Purchase order received")
        self.assertContains(resp, "Payment received")

    def test_invoice_appears_after_approval_delivery_only_after_invoice(self):
        # A PO is optional — approval is enough to raise the tax invoice. The
        # delivery note button stays hidden until the invoice exists.
        self.assertNotContains(self.client.get(self._url()), "Create tax invoice")
        self._set_status("approved")
        approved = self.client.get(self._url())
        self.assertContains(approved, "Create tax invoice")
        self.assertNotContains(approved, "Create delivery note")
        # Once the invoice is raised, the delivery note becomes available.
        self.client.post(self._url("invoice/"))
        after = self.client.get(self._url())
        self.assertContains(after, "Create delivery note")

    def test_creating_an_invoice_opens_its_detail_page(self):
        with tenant_scope(self.company.id):
            self.quote.lines.create(company=self.company, position=2,
                                    description="Bolt", qty=4, unit_price=25)
        self._set_status("approved")
        resp = self.client.post(self._url("invoice/"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/commercial-documents/", resp.url)
        detail = self.client.get(resp.url)
        self.assertContains(detail, "Tax invoice")
        self.assertContains(detail, "INV-")
        self.assertContains(detail, "Approve")             # lifecycle action
        self.assertNotContains(detail, "Finalize")         # removed
        self.assertContains(detail, "preview")             # PDF preview
        # No delivery note yet → the sibling is offered as Create.
        self.assertContains(detail, "Create delivery note")

    def test_delivery_note_page_links_to_its_invoice(self):
        # A delivery note always has an invoice (invoice precedes it), so its
        # sibling action is "View tax invoice", never "Create".
        with tenant_scope(self.company.id):
            self.quote.lines.create(company=self.company, position=2,
                                    description="Bolt", qty=4, unit_price=25)
        self._set_status("approved")
        self.client.post(self._url("invoice/"))            # invoice first
        resp = self.client.post(self._url("delivery-note/"))
        detail = self.client.get(resp.url)
        self.assertContains(detail, "Delivery note")
        self.assertContains(detail, "View tax invoice")
        self.assertNotContains(detail, "Create tax invoice")

    def test_existing_documents_show_view_not_create(self):
        # Once a document exists, its button becomes "View …" (on the quotation
        # and on the sibling document page).
        self._set_status("approved")
        self.client.post(self._url("invoice/"))
        self.client.post(self._url("delivery-note/"))
        review = self.client.get(self._url())
        self.assertContains(review, "View tax invoice")
        self.assertContains(review, "View delivery note")
        self.assertNotContains(review, "Create tax invoice")
        self.assertNotContains(review, "Create delivery note")
        # And the invoice page links to the existing delivery note, not Create.
        with tenant_scope(self.company.id):
            from apps.quotes.models import CommercialDocument
            inv = CommercialDocument.objects.get(quotation=self.quote, kind="invoice")
        detail = self.client.get(f"/commercial-documents/{inv.id}/")
        self.assertContains(detail, "View delivery note")
        self.assertNotContains(detail, "Create delivery note")

    def test_scope_of_work_shows_on_the_review_page(self):
        with tenant_scope(self.company.id):
            self.quote.scope_of_work = "Replace the head-pulley bearings."
            self.quote.save(update_fields=["scope_of_work"])
        resp = self.client.get(self._url())
        self.assertContains(resp, "Scope of Work")
        self.assertContains(resp, "head-pulley bearings")

    @NO_AI
    def test_po_extraction_reads_the_document_deterministically(self):
        po = SimpleUploadedFile(
            "po.txt", b"PO NUMBER: 4500123456\nDATE 2026-07-20\nTotal: R 12 000,00")
        resp = self.client.post(self._url("po/extract/"), {"document": po})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["po_number"], "4500123456")
        self.assertEqual(data["value"], "12000.00")

    def test_pdf_preview_may_be_framed(self):
        resp = self.client.get(self._url("pdf/?inline=1"))
        self.assertEqual(resp["Content-Disposition"][:6], "inline")
        # Not DENY, so the same-origin review page can iframe it.
        self.assertNotEqual(resp.get("X-Frame-Options"), "DENY")


class CompanyBrandingTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.admin = user_with(self.company, ["company.manage"], email="a@lulama.co.za")
        self.client.force_login(self.admin)

    def test_an_overlong_brand_colour_is_rejected_not_a_500(self):
        from apps.identity.models import Company
        resp = self.client.post("/company/", {
            "section": "branding",
            "brand_primary": "way-too-long-not-a-hex-colour-value",
        })
        self.assertEqual(resp.status_code, 302)        # redirect, not a DataError
        with tenant_scope(self.company.id):
            self.assertEqual(Company.objects.get(id=self.company.id).brand_primary, "")

    def test_a_valid_brand_colour_is_saved(self):
        from apps.identity.models import Company
        self.client.post("/company/", {"section": "branding", "brand_primary": "#A5127F"})
        with tenant_scope(self.company.id):
            self.assertEqual(Company.objects.get(id=self.company.id).brand_primary, "#a5127f")


class CustomerEditTests(TestCase):
    def setUp(self):
        from apps.customers.services import create_customer
        self.company = make_company()
        self.admin = user_with(self.company, ["projects.view", "projects.create"],
                               email="cm@lulama.co.za")
        with tenant_scope(self.company.id):
            self.customer = create_customer(self.company, self.admin, name="Harmony")
        self.client.force_login(self.admin)

    def test_edit_page_and_save(self):
        from apps.customers.models import Customer
        url = f"/customers/{self.customer.id}/edit/"
        self.assertContains(self.client.get(url), "Edit customer")
        resp = self.client.post(url, {
            "name": "Harmony Mining", "vendor_number": "TRL0086",
            "payment_terms_days": "60", "status": "active",
        })
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            c = Customer.objects.get(id=self.customer.id)
        self.assertEqual(c.name, "Harmony Mining")
        self.assertEqual(c.vendor_number, "TRL0086")
        self.assertEqual(c.payment_terms_days, 60)

    def test_edit_needs_permission(self):
        outsider = user_with(self.company, ["projects.view"], email="no@lulama.co.za")
        self.client.force_login(outsider)
        resp = self.client.post(f"/customers/{self.customer.id}/edit/", {"name": "Hacked"})
        self.assertEqual(resp.status_code, 302)
        with tenant_scope(self.company.id):
            from apps.customers.models import Customer
            self.assertEqual(Customer.objects.get(id=self.customer.id).name, "Harmony")

    def test_detail_shows_edit_and_add_person(self):
        resp = self.client.get(f"/customers/{self.customer.id}/")
        self.assertContains(resp, "Edit")
        self.assertContains(resp, "Add a person")
        self.assertContains(resp, "Responsibility coverage")


class QuotationListPaginationTests(TestCase):
    """Gate #17: the quotations list paginates (25 to a page) and renders a page
    of rows in a constant number of queries — adding quotations must never add a
    query per row (the customer / type FKs and line totals each row touches are
    select_related / prefetched)."""

    def _seed(self, company, count, start=0):
        """Create `count` quotations, each with a customer, a type and a line —
        the relations the table renders per row."""
        from apps.customers.models import Customer
        from apps.quotes.models import QuotationLine, QuotationType
        with tenant_scope(company.id):
            # get_or_create so seeding twice (short page, then a full one) reuses
            # the same customer/type rather than tripping their unique constraints.
            cust, _ = Customer.objects.get_or_create(
                company=company, code="HG", defaults={"name": "Harmony Gold"})
            qtype, _ = QuotationType.objects.get_or_create(
                company=company, key="general", defaults={"label": "General"})
            for i in range(start, start + count):
                q = Quotation.objects.create(company=company, number=f"QT-{i:04d}",
                                             client_name="Harmony", customer=cust,
                                             quotation_type=qtype)
                QuotationLine.objects.create(quotation=q, description="Steel",
                                             qty=Decimal("2"), unit_price=Decimal("485"))

    def test_page_is_bounded_and_second_page_holds_the_remainder(self):
        c = make_company()
        self._seed(c, 30)                                   # 25 + 5 across two pages
        user = user_with(c, ["projects.view", "quotes.create"])
        self.client.force_login(user)

        first = self.client.get("/quotations/")
        self.assertEqual(first.status_code, 200)
        page = first.context["quotations"]
        self.assertEqual(page.paginator.count, 30)
        self.assertEqual(page.paginator.num_pages, 2)
        self.assertEqual(len(page.object_list), 25)         # a page, not all 30

        second = self.client.get("/quotations/?page=2")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.context["quotations"].object_list), 5)

    def test_filters_survive_page_links(self):
        c = make_company()
        self._seed(c, 30)
        user = user_with(c, ["projects.view", "quotes.create"])
        self.client.force_login(user)
        resp = self.client.get("/quotations/?status=draft")
        # The Next link carries the active filter alongside the page cursor.
        self.assertContains(resp, "status=draft")
        self.assertContains(resp, "page=2")

    def test_rows_do_not_add_queries(self):
        """A full page of 25 rows costs the same number of queries as a short
        page of 5 — proof there is no per-row query."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        c = make_company()
        user = user_with(c, ["projects.view", "quotes.create", "finance.view_money"])
        self.client.force_login(user)

        self._seed(c, 5)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(self.client.get("/quotations/").status_code, 200)

        self._seed(c, 20, start=5)                          # now a full 25-row page
        with CaptureQueriesContext(connection) as full:
            resp = self.client.get("/quotations/")
        self.assertEqual(len(resp.context["quotations"].object_list), 25)
        self.assertEqual(len(full.captured_queries), len(small.captured_queries))
