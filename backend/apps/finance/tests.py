"""Finance tests: budget baseline from the approved estimate, actuals convergence
from execution + procurement, live profitability, the explainable profit
predictor, retention + progress claims, variation budget update, the commercial
dashboard, Golden Rule, and tenant isolation."""

from datetime import date
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.compliance.models import ComplianceRequirement
from apps.core.context import tenant_scope
from apps.estimating.services import approve_estimate, create_estimate
from apps.execution.models import Resource, Task
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.projects.services import award_quotation
from apps.quotes.models import Quotation

from .models import Variation, VariationStatus
from .services import (
    approve_variation,
    budget_vs_actual,
    commercial_dashboard,
    create_invoice,
    create_progress_claim,
    profit_forecast,
    profitability,
    rebuild_actuals_from_sources,
    record_payment,
)

SECTIONS = [
    {"category": "labour", "lines": [{"description": "Fitter", "qty": 100, "unit": "hour",
                                      "unit_cost": 450}]},   # 45 000
    {"category": "material", "lines": [{"description": "Steel", "qty": 12, "unit_cost": 485}]},  # 5 820
]


def make_company(name="Lulama"):
    # An SA contractor with 15% VAT configured (tax is now per-company; the
    # platform default for a new company is 0%).
    c = Company.objects.create(name=name, default_tax_rate=Decimal("15.00"))
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("estimate", "EST"),
                    ("invoice", "INV"), ("variation", "VO"), ("po", "PO")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


def awarded_project_with_budget(company, markup="25"):
    """Award a project whose approved estimate becomes the budget baseline."""
    q = Quotation.objects.create(company=company, number="QT-1", client_name="Sasol")
    est = create_estimate(company, None, client_name="Sasol", work_type="pump_overhaul",
                          quotation=q, markup_pct=Decimal(markup), sections=SECTIONS)
    approve_estimate(est, None)
    ComplianceRequirement.objects.create(company=company, code="SF", name="Safety File",
                                         category="documentation", source="customer",
                                         is_mandatory=True, applies_when={})
    project = award_quotation(company, None, quotation=q, work_type="pump_overhaul")
    return project, est


class BudgetTests(APITestCase):
    def test_budget_created_from_estimate_on_award(self):
        c = make_company()
        with tenant_scope(c.id):
            project, est = awarded_project_with_budget(c)
            budget = project.budget  # auto-created on award
            cats = {line.category: line.amount for line in budget.lines.all()}
            total_budget = budget.total_cost_budget
            self.assertEqual(budget.revenue, est.selling_price)     # 50 820 × 1.25 = 63 525
        self.assertEqual(cats["labour"], Decimal("45000.00"))
        self.assertEqual(cats["material"], Decimal("5820.00"))
        self.assertEqual(total_budget, Decimal("50820.00"))


class ConvergenceTests(APITestCase):
    def test_actuals_converge_from_execution_and_procurement(self):
        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            # labour actual via approved timesheet: 120h @ R450 = R54 000
            fitter = Resource.objects.create(company=c, kind="employee", name="Fitter",
                                             hourly_rate=Decimal("450"))
            task = Task.objects.create(company=c, project=project, name="Overhaul",
                                       blocks_on_compliance=False)
            task.timesheets.create(company=c, resource=fitter, date=date.today(),
                                   hours=Decimal("120"), approved=True)
            rebuild_actuals_from_sources(project)
            bva = {r["category"]: r for r in budget_vs_actual(project)["lines"]}
        self.assertEqual(bva["labour"]["actual"], "54000.00")
        self.assertEqual(bva["labour"]["variance"], "9000.00")   # R9k over the R45k budget


class FieldSpendConvergenceTests(APITestCase):
    """The money loop closes: cash/card spend the crew captures in the field
    (WES task reports) converges into the cost ledger and hits profitability,
    alongside — never overwriting — procurement's supplier-invoice material."""

    def test_field_reports_land_in_actuals_and_profitability(self):
        from apps.execution.work_execution import create_task_report
        from apps.execution.models import ReportKind
        from .services import actual_cost

        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            task = Task.objects.create(company=c, project=project, name="Overhaul",
                                       blocks_on_compliance=False)
            # Crew buys steel cash on site (R2 000) and fuel (R800), plus a misc expense.
            create_task_report(task, None, kind=ReportKind.MATERIAL,
                               title="Steel offcuts", supplier="Steel & Pipe",
                               amount=Decimal("2000"))
            create_task_report(task, None, kind=ReportKind.FUEL,
                               title="Diesel", supplier="Engen", amount=Decimal("800"))
            create_task_report(task, None, kind=ReportKind.EXPENSE,
                               title="Toll gate", amount=Decimal("150"))
            result = rebuild_actuals_from_sources(project)
            bva = {r["category"]: r for r in budget_vs_actual(project)["lines"]}
            total = actual_cost(project)
        # Field spend grouped by finance category, no procurement invoices in play.
        self.assertEqual(result["field"], {"material": Decimal("2000"),
                                           "equipment": Decimal("800"),
                                           "other": Decimal("150")})
        self.assertEqual(bva["material"]["actual"], "2000.00")   # field material shows through
        self.assertEqual(bva["equipment"]["actual"], "800.00")   # fuel → equipment
        self.assertEqual(bva["other"]["actual"], "150.00")
        self.assertEqual(total, Decimal("2950.00"))              # all field spend in the ledger

    def test_resync_is_idempotent(self):
        """Running convergence twice must not double-count field spend."""
        from apps.execution.work_execution import create_task_report
        from apps.execution.models import ReportKind
        from .services import actual_cost

        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            task = Task.objects.create(company=c, project=project, name="T",
                                       blocks_on_compliance=False)
            create_task_report(task, None, kind=ReportKind.MATERIAL,
                               title="Bolts", supplier="Fastenal", amount=Decimal("500"))
            rebuild_actuals_from_sources(project)
            rebuild_actuals_from_sources(project)   # re-sync
            total = actual_cost(project)
        self.assertEqual(total, Decimal("500.00"))

    def test_removed_receipt_self_corrects(self):
        """Deleting a field receipt and re-syncing clears its ledger row — the
        actual must fall back, not leave a stale cost behind."""
        from apps.execution.work_execution import create_task_report
        from apps.execution.models import ReportKind
        from .services import actual_cost

        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            task = Task.objects.create(company=c, project=project, name="T",
                                       blocks_on_compliance=False)
            r = create_task_report(task, None, kind=ReportKind.MATERIAL,
                                   title="Gaskets", supplier="Acme", amount=Decimal("900"))
            rebuild_actuals_from_sources(project)
            self.assertEqual(actual_cost(project), Decimal("900.00"))
            r.delete()                              # receipt reversed
            rebuild_actuals_from_sources(project)   # re-sync
            total = actual_cost(project)
        self.assertEqual(total, Decimal("0.00"))


class ProfitabilityTests(APITestCase):
    def test_live_profitability(self):
        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            fitter = Resource.objects.create(company=c, kind="employee", name="F",
                                             hourly_rate=Decimal("450"))
            task = Task.objects.create(company=c, project=project, name="T",
                                       blocks_on_compliance=False)
            task.timesheets.create(company=c, resource=fitter, date=date.today(),
                                   hours=Decimal("100"), approved=True)  # R45 000
            rebuild_actuals_from_sources(project)
            p = profitability(project)
        self.assertEqual(p["revenue"], "63525.00")
        self.assertEqual(p["actual_cost"], "45000.00")
        self.assertEqual(p["gross_profit"], "18525.00")


class ProfitPredictorTests(APITestCase):
    def test_forecast_flags_overrun(self):
        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            fitter = Resource.objects.create(company=c, kind="employee", name="F",
                                             hourly_rate=Decimal("450"))
            # 50% complete but already spent R45k of a R50.8k budget → trending well over
            t1 = Task.objects.create(company=c, project=project, name="A",
                                     blocks_on_compliance=False, progress_pct=100)
            Task.objects.create(company=c, project=project, name="B",
                                blocks_on_compliance=False, progress_pct=0)
            t1.timesheets.create(company=c, resource=fitter, date=date.today(),
                                 hours=Decimal("100"), approved=True)  # R45 000
            rebuild_actuals_from_sources(project)
            fc = profit_forecast(project)
        self.assertEqual(fc["completion_pct"], 50)
        # projected final cost = 45 000 / 0.5 = 90 000 → way over the 50 820 budget
        self.assertEqual(fc["projected_final_cost"], "90000.00")
        self.assertEqual(fc["verdict"], "at risk")
        self.assertTrue(any(ct["category"] == "labour" for ct in fc["contributors"]))


class InvoicingTests(APITestCase):
    def test_retention_and_payment_lifecycle(self):
        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            inv = create_invoice(project, None, retention_pct=Decimal("10"),
                                 lines=[{"description": "Works", "qty": 1, "unit_price": 100000}])
            # subtotal 100 000; VAT 15 000; retention 10 000 → payable now 105 000
            self.assertEqual(inv.subtotal, Decimal("100000.00"))
            self.assertEqual(inv.retention_amount, Decimal("10000.00"))
            self.assertEqual(inv.total, Decimal("105000.00"))
            record_payment(inv, None, amount=Decimal("105000"))
            inv.refresh_from_db()
            outstanding = inv.outstanding
        self.assertEqual(inv.status, "paid")
        self.assertEqual(outstanding, Decimal("0.00"))

    def test_progress_claim_bills_delta(self):
        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)  # contract revenue 63 525
            claim1 = create_progress_claim(project, None, percent_complete=40)   # 40% → 25 410
            claim2 = create_progress_claim(project, None, percent_complete=70)   # +30% → 19 057.50
            sub1, sub2 = claim1.subtotal, claim2.subtotal
        self.assertEqual(sub1, Decimal("25410.00"))
        self.assertEqual(sub2, Decimal("19057.50"))


class VariationTests(APITestCase):
    def test_approval_updates_budget(self):
        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            before = project.budget.revenue
            v = Variation.objects.create(company=c, project=project, number="VO-1",
                                         description="Extra pump", category="material",
                                         estimated_cost=Decimal("8000"),
                                         revenue_impact=Decimal("12000"),
                                         status=VariationStatus.PENDING_CUSTOMER)
            approve_variation(v, None)
            project.budget.refresh_from_db()
            after = project.budget.revenue
            new_total = project.budget.total_cost_budget
        self.assertEqual(after - before, Decimal("12000.00"))       # revenue grew
        self.assertEqual(new_total, Decimal("58820.00"))            # +R8k cost line


class DashboardTests(APITestCase):
    def test_commercial_dashboard_flags_loss_maker(self):
        c = make_company()
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            # blow the cost past revenue via a manual timesheet
            fitter = Resource.objects.create(company=c, kind="employee", name="F",
                                             hourly_rate=Decimal("1000"))
            task = Task.objects.create(company=c, project=project, name="T",
                                       blocks_on_compliance=False)
            task.timesheets.create(company=c, resource=fitter, date=date.today(),
                                   hours=Decimal("100"), approved=True)  # R100 000 > R63 525
            rebuild_actuals_from_sources(project)
            dash = commercial_dashboard(c)
        self.assertEqual(len(dash["loss_making_projects"]), 1)
        self.assertEqual(dash["loss_making_projects"][0]["project"], project.number)


class FinanceAPITests(APITestCase):
    def setUp(self):
        self.company = make_company()
        self.other = make_company("Rival")
        manage = Permission.objects.create(codename="finance.manage", module="finance", label="M")
        money = Permission.objects.create(codename="finance.view_money", module="finance", label="$")
        self.fin_role = Role.objects.create(name="Finance", is_system=True)
        self.fin_role.permissions.add(manage, money)
        self.fin = User.objects.create_user("fin@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.fin, company=self.company, role=self.fin_role)
        self.worker = User.objects.create_user("w@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company,
                                  role=Role.objects.create(name="Worker", is_system=True))
        with tenant_scope(self.company.id):
            self.project, _ = awarded_project_with_budget(self.company)

    def test_profitability_endpoint_golden_rule(self):
        # worker (no finance.view_money) is refused the money endpoint
        self.client.force_authenticate(self.worker)
        resp = self.client.get(f"/api/v1/projects/{self.project.id}/profitability/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        # finance sees it
        self.client.force_authenticate(self.fin)
        resp = self.client.get(f"/api/v1/projects/{self.project.id}/profitability/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["revenue"], "63525.00")

    def test_invoice_money_hidden_from_worker(self):
        with tenant_scope(self.company.id):
            create_invoice(self.project, self.fin, lines=[{"description": "x", "qty": 1,
                                                           "unit_price": 1000}])
        self.client.force_authenticate(self.worker)
        row = self.client.get(f"/api/v1/invoices/?project={self.project.id}").data["results"][0]
        self.assertNotIn("total", row)          # money stripped
        self.assertNotIn("subtotal", row)
        self.assertIn("number", row)            # identity still visible

    def test_tenant_isolation(self):
        with tenant_scope(self.company.id):
            inv = create_invoice(self.project, self.fin, lines=[])
        rival = User.objects.create_user("r@rival.co.za", "x", active_company=self.other)
        Membership.objects.create(user=rival, company=self.other, role=self.fin_role)
        self.client.force_authenticate(rival)
        resp = self.client.get(f"/api/v1/invoices/{inv.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class InternationalDefaultsTests(APITestCase):
    """Tax is per-company, not a single-country assumption."""

    def test_new_company_has_no_tax_by_default(self):
        c = Company.objects.create(name="US Contractor")   # platform default
        self.assertEqual(c.default_tax_rate, Decimal("0"))

    def test_invoice_uses_company_tax_rate(self):
        c = make_company()                 # SA company, 15%
        c.default_tax_rate = Decimal("0")  # switch to a no-tax jurisdiction
        c.save(update_fields=["default_tax_rate"])
        with tenant_scope(c.id):
            project, _ = awarded_project_with_budget(c)
            inv = create_invoice(project, None,
                                 lines=[{"description": "Works", "qty": 1, "unit_price": 1000}])
            self.assertEqual(inv.vat_rate, Decimal("0"))
            self.assertEqual(inv.vat_amount, Decimal("0.00"))
