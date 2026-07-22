"""Thin-slice end-to-end tests — the foundation proven on a real API resource:
ambient tenant isolation, numbering engine, Golden Rule, and the event bus."""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.core.models import DomainEvent
from apps.identity.models import Company, Membership, Permission, Role, User

from .models import Quotation


class QuotationSliceTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        self.other = Company.objects.create(name="Rival")
        NumberingRule.objects.create(
            company=self.company, doc_type="quotation", prefix="QT", fmt="{prefix}-{yyyy}-{seq:06d}"
        )
        self.money_perm = Permission.objects.create(
            codename="finance.view_money", module="finance", label="Money"
        )
        self.finance_role = Role.objects.create(name="Finance", is_system=True)
        self.finance_role.permissions.add(self.money_perm)
        self.worker_role = Role.objects.create(name="Worker", is_system=True)

        self.admin = User.objects.create_user("admin@lulama.co.za", "x",
                                              active_company=self.company)
        Membership.objects.create(user=self.admin, company=self.company, role=self.finance_role)
        self.worker = User.objects.create_user("thabo@lulama.co.za", "x",
                                               active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=self.worker_role)
        self.rival_admin = User.objects.create_user("boss@rival.co.za", "x",
                                                    active_company=self.other)
        Membership.objects.create(user=self.rival_admin, company=self.other, role=self.finance_role)

    def _create_quote(self):
        self.client.force_authenticate(self.admin)
        return self.client.post("/api/v1/quotations/", {
            "client_name": "Sibanye",
            "title": "Steel supply",
            "lines": [{"description": "Lip channel", "qty": "12", "unit_price": "485.00"}],
        }, format="json")

    def test_create_allocates_number_and_totals(self):
        resp = self._create_quote()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # Numbers are two letters then six digits (e.g. LU000001) — short enough
        # to quote over the phone, unique for the life of the company.
        self.assertRegex(resp.data["number"], r"^[A-Z]{2}\d{6}$")
        self.assertEqual(Decimal(resp.data["total"]), Decimal("6693.00"))  # 12*485 *1.15

    def test_create_emits_domain_event(self):
        self._create_quote()
        self.assertTrue(DomainEvent.objects.filter(type="QuotationCreated").exists())

    def test_tenant_isolation_via_api(self):
        self._create_quote()
        quote = Quotation.all_objects.get()
        # rival admin must not see another tenant's quotation → 404 (not 403)
        self.client.force_authenticate(self.rival_admin)
        resp = self.client.get(f"/api/v1/quotations/{quote.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_scoped_to_tenant(self):
        self._create_quote()
        self.client.force_authenticate(self.rival_admin)
        resp = self.client.get("/api/v1/quotations/")
        self.assertEqual(resp.data["count"], 0)  # rival sees none

    def test_golden_rule_finance_sees_money(self):
        self._create_quote()
        self.client.force_authenticate(self.admin)  # has finance.view_money
        resp = self.client.get("/api/v1/quotations/")
        self.assertIn("total", resp.data["results"][0])

    def test_golden_rule_worker_money_stripped(self):
        self._create_quote()
        self.client.force_authenticate(self.worker)  # no finance.view_money
        resp = self.client.get("/api/v1/quotations/")
        row = resp.data["results"][0]
        self.assertNotIn("total", row)
        self.assertNotIn("subtotal", row)
        self.assertIn("number", row)  # non-money fields still visible
        self.assertNotIn("unit_price", row["lines"][0])  # line money stripped too
