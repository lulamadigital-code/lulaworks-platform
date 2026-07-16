"""Estimating tests: cost build-up + price derivation, cost engines off the
price ledger, risk scoring, approval gate, version control (never overwrite),
Golden-Rule-safe quotation generation, the pricing-intelligence variance loop,
Golden Rule + tenant isolation."""

from datetime import date
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.procurement.models import Supplier, SupplierPrice
from apps.quotes.models import Quotation

from .models import CostCategory, EstimateStatus
from .services import (
    approval_required,
    approve_estimate,
    capture_actuals,
    create_estimate,
    create_revision,
    generate_quotation,
    labour_calibration,
    propose_material_lines,
)


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    NumberingRule.objects.create(company=c, doc_type="estimate", prefix="EST",
                                 fmt="{prefix}-{yyyy}-{seq:05d}")
    NumberingRule.objects.create(company=c, doc_type="quotation", prefix="QT",
                                 fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


SECTIONS = [
    {"category": "labour", "lines": [{"description": "Fitter", "qty": 100, "unit": "hour",
                                      "unit_cost": 450}]},   # 45 000
    {"category": "material", "lines": [{"description": "Steel", "qty": 12, "unit_cost": 485}]},  # 5 820
]


class CostModelTests(APITestCase):
    def test_build_up_price_and_margin(self):
        c = make_company()
        with tenant_scope(c.id):
            est = create_estimate(c, None, client_name="Sasol", markup_pct=Decimal("25"),
                                  sections=SECTIONS)
            est.contingency_pct = Decimal("10")
            est.save()
            self.assertEqual(est.direct_cost, Decimal("50820.00"))       # 45 000 + 5 820
            self.assertEqual(est.contingency_amount, Decimal("5082.00"))  # 10%
            self.assertEqual(est.total_cost, Decimal("55902.00"))
            self.assertEqual(est.selling_price, Decimal("69877.50"))      # ×1.25
            # margin = profit / price
            self.assertEqual(est.margin_pct, Decimal("20.00"))


class CostEngineTests(APITestCase):
    def test_material_engine_prices_from_ledger(self):
        c = make_company()
        with tenant_scope(c.id):
            s = Supplier.objects.create(company=c, name="NJR")
            SupplierPrice.objects.create(company=c, supplier=s, item_key="steel lip channel",
                                         description="Steel lip channel", unit_price=Decimal("485"),
                                         date=date.today())
            proposals = propose_material_lines(c, [{"description": "Steel lip channel", "qty": 12}])
        self.assertEqual(proposals[0]["unit_cost"], Decimal("485"))
        self.assertEqual(proposals[0]["source"], "ledger")
        self.assertGreater(proposals[0]["confidence"], Decimal("0"))

    def test_material_engine_flags_missing_price(self):
        c = make_company()
        with tenant_scope(c.id):
            proposals = propose_material_lines(c, [{"description": "Unknown widget"}])
        self.assertEqual(proposals[0]["unit_cost"], Decimal("0.00"))
        self.assertEqual(proposals[0]["confidence"], Decimal("0.00"))


class RiskAndApprovalTests(APITestCase):
    def test_thin_margin_requires_approval(self):
        c = make_company()
        with tenant_scope(c.id):
            est = create_estimate(c, None, client_name="X", markup_pct=Decimal("5"),
                                  sections=SECTIONS)  # ~4.7% margin
            gate = approval_required(est)
        self.assertTrue(gate["required"])
        self.assertEqual(gate["perm"], "estimating.approve")

    def test_healthy_margin_no_approval(self):
        c = make_company()
        with tenant_scope(c.id):
            est = create_estimate(c, None, client_name="X", markup_pct=Decimal("40"),
                                  sections=SECTIONS)
            gate = approval_required(est)
        self.assertFalse(gate["required"])

    def test_risk_score_flags_unpriced_lines(self):
        c = make_company()
        with tenant_scope(c.id):
            est = create_estimate(c, None, client_name="X", markup_pct=Decimal("30"), sections=[
                {"category": "material", "lines": [{"description": "TBC", "qty": 1, "unit_cost": 0}]},
            ])
        self.assertGreater(est.risk_score, Decimal("0"))
        self.assertTrue(any("unpriced" in f for f in est.risk_flags))


class VersionControlTests(APITestCase):
    def test_revision_never_overwrites(self):
        c = make_company()
        with tenant_scope(c.id):
            est = create_estimate(c, None, client_name="X", sections=SECTIONS)
            rev = create_revision(est, None, reason="client cut scope")
            est.refresh_from_db()
            self.assertEqual(rev.version, 2)
            self.assertEqual(rev.number, est.number)               # same number, new version
            self.assertEqual(est.status, EstimateStatus.SUPERSEDED)  # prior kept, superseded
            self.assertEqual(rev.parent_id, est.id)
            self.assertEqual(rev.sections.count(), est.sections.count())  # deep-copied


class QuotationGenerationTests(APITestCase):
    def test_quotation_exposes_price_only_not_cost(self):
        c = make_company()
        with tenant_scope(c.id):
            est = create_estimate(c, None, client_name="Sasol", markup_pct=Decimal("25"),
                                  sections=SECTIONS)
            with self.assertRaises(ValueError):
                generate_quotation(est, None)   # not approved yet
            approve_estimate(est, None)
            quote = generate_quotation(est, None)
            self.assertIsInstance(quote, Quotation)
            # The external quotation total == the estimate's selling price…
            self.assertEqual(quote.subtotal, est.selling_price)
            # …and the Quotation model structurally has NO cost/markup/margin fields.
            for leaked in ("markup_pct", "total_cost", "margin_amount", "direct_cost"):
                self.assertFalse(hasattr(quote, leaked))


class PricingIntelligenceTests(APITestCase):
    def test_actuals_variance_and_labour_calibration(self):
        c = make_company()
        with tenant_scope(c.id):
            est = create_estimate(c, None, client_name="X", work_type="pump_overhaul",
                                  sections=SECTIONS)  # labour est 45 000
            rows = capture_actuals(est, None, [
                {"category": "labour", "actual_cost": "54000", "source": "timesheet"},  # +20%
                {"category": "material", "actual_cost": "5820", "source": "supplier_invoice"},
            ])
            labour_row = next(r for r in rows if r.category == CostCategory.LABOUR)
            self.assertEqual(labour_row.variance, Decimal("9000.00"))
            self.assertEqual(labour_row.variance_pct, Decimal("20.00"))
            factor, note = labour_calibration(c, "pump_overhaul")
        self.assertEqual(factor, Decimal("1.200"))   # historically 20% over → ×1.2
        self.assertIn("exceeded", note)


class EstimateAPITests(APITestCase):
    def setUp(self):
        self.company = make_company()
        self.other = make_company("Rival")
        manage = Permission.objects.create(codename="estimating.manage", module="estimating", label="M")
        approve = Permission.objects.create(codename="estimating.approve", module="estimating", label="A")
        money = Permission.objects.create(codename="finance.view_money", module="finance", label="$")
        self.est_role = Role.objects.create(name="Estimator", is_system=True)
        self.est_role.permissions.add(manage, money)
        self.approver_role = Role.objects.create(name="Approver", is_system=True)
        self.approver_role.permissions.add(approve, money)

        self.estimator = User.objects.create_user("est@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.estimator, company=self.company, role=self.est_role)
        self.approver = User.objects.create_user("app@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.approver, company=self.company, role=self.approver_role)

    def _create(self):
        self.client.force_authenticate(self.estimator)
        return self.client.post("/api/v1/estimates/", {
            "client_name": "Sasol", "work_type": "pump_overhaul", "markup_pct": "25",
            "sections": SECTIONS,
        }, format="json")

    def test_create_allocates_number_and_computes_price(self):
        resp = self._create()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        year = date.today().year
        self.assertEqual(resp.data["number"], f"EST-{year}-00001")
        self.assertEqual(Decimal(resp.data["selling_price"]), Decimal("63525.00"))  # 50 820×1.25

    def test_approve_requires_permission(self):
        eid = self._create().data["id"]
        # estimator lacks estimating.approve
        resp = self.client.post(f"/api/v1/estimates/{eid}/approve/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.approver)
        resp = self.client.post(f"/api/v1/estimates/{eid}/approve/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "approved")

    def test_golden_rule_hides_cost_and_margin(self):
        self._create()
        noperm = User.objects.create_user("np@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=noperm, company=self.company,
                                  role=Role.objects.create(name="Viewer", is_system=True))
        self.client.force_authenticate(noperm)
        row = self.client.get("/api/v1/estimates/").data["results"][0]
        for hidden in ("total_cost", "selling_price", "margin_pct", "markup_pct"):
            self.assertNotIn(hidden, row)
        self.assertIn("number", row)          # non-money fields still present
        self.assertIn("risk_score", row)

    def test_tenant_isolation(self):
        eid = self._create().data["id"]
        rival = User.objects.create_user("r@rival.co.za", "x", active_company=self.other)
        Membership.objects.create(user=rival, company=self.other, role=self.est_role)
        self.client.force_authenticate(rival)
        resp = self.client.get(f"/api/v1/estimates/{eid}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
