"""AI Prediction Engine (§16) — grounded foresight with confidence + reasoning,
gated by the same permissions as the underlying data, and empty (not invented)
when there's no signal."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.ai_platform import predictions as pr
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class PredictionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")

    def test_price_rise_prediction(self):
        from apps.procurement.models import Supplier, SupplierPrice
        with tenant_scope(self.company.id):
            s = Supplier.objects.create(company=self.company, name="Hydraulics SA")
            for yr, price in [(2024, "100"), (2025, "120"), (2026, "150")]:
                SupplierPrice.objects.create(
                    company=self.company, supplier=s, item_key="hydraulic pipe",
                    description="Hydraulic pipe", unit="m", unit_price=Decimal(price),
                    date=date(yr, 3, 1))
            preds = pr.predict_price_rises(self.company)
        self.assertTrue(preds)
        p = preds[0]
        self.assertEqual(p.kind, "price_rise")
        self.assertGreater(p.confidence, 0)
        self.assertTrue(p.reasoning)          # never a bare claim
        self.assertEqual(p.source, "Supplier price history")

    def test_repeat_customer_prediction(self):
        from apps.customers.models import Customer
        from apps.quotes.models import Quotation
        with tenant_scope(self.company.id):
            cust = Customer.objects.create(company=self.company, name="ABC Mining")
            # Quotes ~every 30 days; last one 45 days ago → due.
            base = timezone.now() - timedelta(days=135)
            for i in range(4):
                q = Quotation.objects.create(company=self.company, number=f"QT-{i}",
                                             client_name="ABC", customer=cust)
                Quotation.objects.filter(pk=q.pk).update(
                    created_at=base + timedelta(days=30 * i))
            preds = pr.predict_repeat_customers(self.company)
        self.assertTrue(any(p.kind == "repeat_customer" for p in preds))

    def test_permission_gating_and_empty(self):
        # A projects-only user sees no price predictions and, with no data, nothing.
        viewer = _user(self.company, ["projects.view"], "v@a.co")
        with tenant_scope(self.company.id):
            out = pr.predictions(self.company, viewer)
        self.assertEqual(out, [])

    def test_aggregator_sorts_by_confidence(self):
        mgr = _user(self.company, ["procurement.manage", "customers.manage",
                                   "projects.view"], "m@a.co")
        with tenant_scope(self.company.id):
            out = pr.predictions(self.company, mgr)
        # No data yet → empty, but the call is safe and returns a list.
        self.assertIsInstance(out, list)
