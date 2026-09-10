"""Attention Centre — cross-module exception detection, permission-gated."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.core.context import tenant_scope
from apps.execution.models import Task
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.quotes.models import (CustomerPurchaseOrder, Quotation,
                                QuotationStatus)


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class AttentionTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        with tenant_scope(self.c.id):
            Task.objects.create(company=self.c, name="Late job",
                                due_date=timezone.localdate() - timedelta(days=2))
            Quotation.objects.create(company=self.c, number="Q1", client_name="X",
                                     status=QuotationStatus.MANAGER_APPROVAL)
            self.u_mgr = _user(self.c, ["projects.view", "quotes.create"], "mgr@acme.co")
            CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-U", quotation=None,
                created_by=self.u_mgr, updated_by=self.u_mgr)

    def test_detects_exceptions(self):
        from apps.web.attention import attention_items
        with tenant_scope(self.c.id):
            data = attention_items(self.c, self.u_mgr)
        titles = [i["title"] for i in data["critical"] + data["warning"] + data["info"]]
        self.assertTrue(any("overdue" in t for t in titles))
        self.assertTrue(any("awaiting approval" in t for t in titles))
        self.assertTrue(any("unmatched" in t for t in titles))
        # overdue work is critical
        self.assertTrue(any("overdue" in i["title"] for i in data["critical"]))

    def test_money_exceptions_gated(self):
        from apps.web.attention import attention_items
        with tenant_scope(self.c.id):
            from apps.finance.models import Invoice, InvoiceStatus
            Invoice.objects.create(company=self.c, number="INV-1", client_name="X",
                                   status=InvoiceStatus.ISSUED)
            no_money = attention_items(self.c, self.u_mgr)
            money_user = _user(self.c, ["finance.view_money"], "fin@acme.co")
            with_money = attention_items(self.c, money_user)
        self.assertFalse(any("unpaid" in i["title"] for i in no_money["warning"]))
        self.assertTrue(any("unpaid" in i["title"] for i in with_money["warning"]))

    def test_page_renders(self):
        self.client.force_login(self.u_mgr)
        r = self.client.get("/attention/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Attention Centre")
        self.assertContains(r, "overdue")
