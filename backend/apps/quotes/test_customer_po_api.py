"""Customer PO JSON API — capture, match, convert, and the Golden-Rule gate.
Thin API over the same services the web workspace uses (apps.web.views_po)."""
from rest_framework.test import APIClient, APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.quotes.models import CustomerPurchaseOrder, Quotation


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


def _api(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


class CustomerPOApiTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.editor = _user(self.c, ["quotes.create", "finance.view_money"], "ed@acme.co")
        self.nomoney = _user(self.c, ["quotes.create"], "nm@acme.co")
        self.viewer = _user(self.c, ["projects.view"], "vw@acme.co")
        self.outsider = _user(self.c, ["customers.view"], "out@acme.co")

    def test_create_requires_quotes_create(self):
        r = _api(self.viewer).post("/api/v1/customer-pos/",
                                   {"po_number": "PO-1"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_create_typed_po(self):
        r = _api(self.editor).post("/api/v1/customer-pos/", {
            "po_number": "PO-1001", "client_name": "ABC Mining", "value": "5000",
        }, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["po_number"], "PO-1001")
        self.assertFalse(r.data["is_matched"])
        self.assertEqual(r.data["value"], "5000")

    def test_create_rejects_empty(self):
        r = _api(self.editor).post("/api/v1/customer-pos/", {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_value_withheld_without_finance(self):
        with tenant_scope(self.c.id):
            po = CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-2", value=9000)
        r = _api(self.nomoney).get(f"/api/v1/customer-pos/{po.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["value"])

    def test_retrieve_offers_suggestions_when_unmatched(self):
        with tenant_scope(self.c.id):
            Quotation.objects.create(company=self.c, number="QTN-9",
                                     client_name="ABC Mining")
            po = CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-3", client_name="ABC Mining")
        r = _api(self.editor).get(f"/api/v1/customer-pos/{po.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("suggestions", r.data)
        self.assertTrue(any(s["number"] == "QTN-9" for s in r.data["suggestions"]))

    def test_link_to_quotation(self):
        with tenant_scope(self.c.id):
            q = Quotation.objects.create(company=self.c, number="QTN-7",
                                         client_name="ABC")
            po = CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-4", client_name="ABC")
        r = _api(self.editor).post(f"/api/v1/customer-pos/{po.pk}/link/",
                                   {"quotation": str(q.pk)}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_matched"])
        self.assertEqual(r.data["status"], "acknowledged")

    def test_create_job_requires_match_first(self):
        with tenant_scope(self.c.id):
            po = CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-5")
        creator = _user(self.c, ["quotes.create", "projects.create"], "cr@acme.co")
        r = _api(creator).post(f"/api/v1/customer-pos/{po.pk}/create-job/")
        self.assertEqual(r.status_code, 400)

    def test_set_status(self):
        with tenant_scope(self.c.id):
            po = CustomerPurchaseOrder.objects.create(
                company=self.c, po_number="PO-6")
        r = _api(self.editor).post(f"/api/v1/customer-pos/{po.pk}/set-status/",
                                   {"status": "cancelled"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "cancelled")

    def test_list_forbidden_without_access(self):
        r = _api(self.outsider).get("/api/v1/customer-pos/")
        self.assertEqual(r.status_code, 403)
