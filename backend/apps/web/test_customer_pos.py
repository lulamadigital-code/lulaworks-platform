"""Customer Purchase Orders workspace — capture, match to a quotation, convert."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.quotes.models import CustomerPurchaseOrder, Quotation


def _user(company, codes, email="po@acme.co"):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class CustomerPOTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = _user(self.c, ["quotes.create", "quotes.approve", "projects.view",
                                "projects.create"])
        self.client.force_login(self.u)

    def test_add_manual_creates_unmatched_po(self):
        r = self.client.post("/customer-pos/add/", {
            "po_number": "PO-45821", "client_name": "ABC Mining", "value": "485000"})
        self.assertEqual(r.status_code, 302)
        with tenant_scope(self.c.id):
            po = CustomerPurchaseOrder.objects.get(po_number="PO-45821")
            self.assertFalse(po.is_matched)
            self.assertEqual(po.client_name, "ABC Mining")

    def test_workspace_lists_and_counts(self):
        with tenant_scope(self.c.id):
            CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-1", client_name="ABC Mining",
                created_by=self.u, updated_by=self.u)
        r = self.client.get("/customer-pos/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "PO-1")
        self.assertContains(r, "Unmatched")

    def test_suggest_and_link_to_quotation(self):
        with tenant_scope(self.c.id):
            quote = Quotation.objects.create(company=self.c, number="QTN-7",
                                             client_name="ABC Mining", site="Rustenburg")
            po = CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-9", client_name="ABC Mining",
                created_by=self.u, updated_by=self.u)
        # detail suggests the matching quotation (customer name)
        r = self.client.get(f"/customer-pos/{po.pk}/")
        self.assertContains(r, "QTN-7")
        # linking sets the match
        self.client.post(f"/customer-pos/{po.pk}/link/", {"quotation": str(quote.pk)})
        po.refresh_from_db()
        self.assertEqual(po.quotation_id, quote.pk)
        self.assertTrue(po.is_matched)

    def test_create_job_requires_a_match(self):
        with tenant_scope(self.c.id):
            po = CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-2", client_name="X",
                created_by=self.u, updated_by=self.u)
        r = self.client.post(f"/customer-pos/{po.pk}/create-job/", follow=True)
        self.assertContains(r, "Match the PO to a quotation first")
        po.refresh_from_db()
        self.assertFalse(po.is_matched)

    def test_add_requires_permission(self):
        viewer = _user(self.c, ["projects.view"], email="viewer@acme.co")
        self.client.force_login(viewer)
        r = self.client.get("/customer-pos/add/", follow=True)
        self.assertNotContains(r, "Upload PO document")   # bounced, not on the form
