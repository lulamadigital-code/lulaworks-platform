"""Procurement tests: price ledger + anomaly, supplier performance, 3-way match,
PO lifecycle + numbering, Golden Rule, tenant isolation."""

from datetime import date
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User

from .models import (
    GRN,
    GRNLine,
    Supplier,
    SupplierInvoice,
    SupplierPrice,
    SupplierRFQ,
    SupplierRFQStatus,
)
from .services import (
    create_purchase_order,
    price_anomaly,
    recompute_performance,
    three_way_match,
)


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    NumberingRule.objects.create(company=c, doc_type="po", prefix="PO", fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


class PriceLedgerTests(APITestCase):
    def setUp(self):
        self.company = make_company()

    def test_anomaly_detection_vs_history(self):
        with tenant_scope(self.company.id):
            s = Supplier.objects.create(company=self.company, name="NJR Steel")
            for price in ("100", "110", "105"):
                SupplierPrice.objects.create(
                    company=self.company, supplier=s, item_key="bearing 6203",
                    description="Bearing 6203", unit_price=Decimal(price), date=date.today(),
                )
            normal = price_anomaly(self.company, "Bearing 6203", Decimal("108"))
            spike = price_anomaly(self.company, "Bearing 6203", Decimal("160"))
        self.assertFalse(normal["anomaly"])
        self.assertTrue(spike["anomaly"])  # ~52% above avg
        self.assertEqual(spike["avg"], Decimal("105"))


class PerformanceTests(APITestCase):
    def test_score_from_delivery_and_quality(self):
        c = make_company()
        with tenant_scope(c.id):
            s = Supplier.objects.create(company=c, name="NJR")
            SupplierRFQ.objects.create(company=c, quotation=None, supplier=s,
                                       number="R1", status=SupplierRFQStatus.RESPONDED)
            po = create_purchase_order(c, None, supplier=s,
                                       lines=[{"description": "Item", "qty": 10, "unit_price": 5}])
            grn = GRN.objects.create(company=c, purchase_order=po, seq=1, date=date.today())
            GRNLine.objects.create(company=c, grn=grn, po_line=po.lines.first(),
                                   description="Item", qty_received=Decimal("10"), condition="good")
            score = recompute_performance(s)
        self.assertEqual(score, Decimal("100.00"))  # responded + full delivery + good quality


class ThreeWayMatchTests(APITestCase):
    def setUp(self):
        self.company = make_company()

    def _po(self):
        with tenant_scope(self.company.id):
            s = Supplier.objects.create(company=self.company, name="NJR")
            return create_purchase_order(
                self.company, None, supplier=s,
                lines=[{"description": "Steel", "qty": 12, "unit_price": 485}],
            ), s

    def test_matched_when_received_and_invoiced_agree(self):
        po, s = self._po()
        with tenant_scope(self.company.id):
            grn = GRN.objects.create(company=self.company, purchase_order=po, seq=1, date=date.today())
            GRNLine.objects.create(company=self.company, grn=grn, po_line=po.lines.first(),
                                   description="Steel", qty_received=Decimal("12"))
            SupplierInvoice.objects.create(company=self.company, supplier=s, purchase_order=po,
                                          invoice_no="INV1", date=date.today(),
                                          total_excl=Decimal("5820"))  # 12*485
            result = three_way_match(po)
        self.assertTrue(result["matched"])
        self.assertEqual(result["variances"], [])

    def test_quantity_and_price_variance_flagged(self):
        po, s = self._po()
        with tenant_scope(self.company.id):
            grn = GRN.objects.create(company=self.company, purchase_order=po, seq=1, date=date.today())
            GRNLine.objects.create(company=self.company, grn=grn, po_line=po.lines.first(),
                                   description="Steel", qty_received=Decimal("10"))  # short 2
            SupplierInvoice.objects.create(company=self.company, supplier=s, purchase_order=po,
                                          invoice_no="INV1", date=date.today(),
                                          total_excl=Decimal("6000"))  # ≠ 5820
            result = three_way_match(po)
        self.assertFalse(result["matched"])
        types = {v["type"] for v in result["variances"]}
        self.assertEqual(types, {"quantity", "price"})


class PurchaseOrderAPITests(APITestCase):
    def setUp(self):
        self.company = make_company()
        self.other = make_company("Rival")
        manage = Permission.objects.create(codename="procurement.manage", module="procurement", label="M")
        approve = Permission.objects.create(codename="po.approve", module="procurement", label="A")
        money = Permission.objects.create(codename="finance.view_money", module="finance", label="$")
        self.buyer_role = Role.objects.create(name="Buyer", is_system=True)
        self.buyer_role.permissions.add(manage, money)
        self.finance_role = Role.objects.create(name="Finance", is_system=True)
        self.finance_role.permissions.add(approve, money)

        self.buyer = User.objects.create_user("buyer@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.buyer, company=self.company, role=self.buyer_role)
        self.finance = User.objects.create_user("fin@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.finance, company=self.company, role=self.finance_role)
        with tenant_scope(self.company.id):
            self.supplier = Supplier.objects.create(company=self.company, name="NJR")

    def test_create_po_allocates_number_and_total(self):
        self.client.force_authenticate(self.buyer)
        resp = self.client.post("/api/v1/purchase-orders/", {
            "supplier": str(self.supplier.id),
            "lines": [{"description": "Steel", "qty": "12", "unit_price": "485.00"}],
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        year = date.today().year
        self.assertEqual(resp.data["number"], f"PO-{year}-00001")
        self.assertEqual(Decimal(resp.data["total"]), Decimal("5820.00"))

    def test_approve_requires_permission(self):
        with tenant_scope(self.company.id):
            po = create_purchase_order(self.company, self.buyer, supplier=self.supplier,
                                       lines=[{"description": "x", "qty": 1, "unit_price": 1}])
        self.client.force_authenticate(self.buyer)  # buyer lacks po.approve
        resp = self.client.post(f"/api/v1/purchase-orders/{po.id}/approve/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        # finance can
        self.client.force_authenticate(self.finance)
        resp = self.client.post(f"/api/v1/purchase-orders/{po.id}/approve/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "approved")

    def test_tenant_isolation(self):
        with tenant_scope(self.company.id):
            po = create_purchase_order(self.company, self.buyer, supplier=self.supplier,
                                       lines=[{"description": "x", "qty": 1, "unit_price": 1}])
        rival = User.objects.create_user("r@rival.co.za", "x", active_company=self.other)
        Membership.objects.create(user=rival, company=self.other, role=self.buyer_role)
        self.client.force_authenticate(rival)
        resp = self.client.get(f"/api/v1/purchase-orders/{po.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_golden_rule_hides_po_money(self):
        with tenant_scope(self.company.id):
            create_purchase_order(self.company, self.buyer, supplier=self.supplier,
                                  lines=[{"description": "Steel", "qty": 12, "unit_price": 485}])
        noperm = User.objects.create_user("np@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=noperm, company=self.company,
                                  role=Role.objects.create(name="Viewer", is_system=True))
        self.client.force_authenticate(noperm)
        resp = self.client.get("/api/v1/purchase-orders/")
        row = resp.data["results"][0]
        self.assertNotIn("total", row)  # money stripped
        self.assertNotIn("unit_price", row["lines"][0])
        self.assertIn("number", row)
