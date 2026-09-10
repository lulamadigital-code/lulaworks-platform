"""Historical Import REST surface — end-to-end through the API: create a batch,
upload a document, read discovery counts, and commit a staged entity."""
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer
from apps.identity.models import Company, Membership, Permission, Role, User

_PO = b"""PURCHASE ORDER
PO Number: PO-2024-0912
Customer: ABC Mining (Pty) Ltd
Supplier: Hydraulics SA
Contact: thabo@abcmining.co.za
"""


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class ImportApiTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")
        with tenant_scope(self.company.id):
            self.existing = Customer.objects.create(
                company=self.company, name="ABC Mining (Pty) Ltd")

    def test_full_flow(self):
        self.client.force_authenticate(self.mgr)
        # 1. create batch
        r = self.client.post("/api/v1/import-batches/", {"label": "My history"})
        self.assertEqual(r.status_code, 201)
        bid = r.data["id"]
        # 2. upload a document
        f = SimpleUploadedFile("po.txt", _PO, content_type="text/plain")
        up = self.client.post(f"/api/v1/import-batches/{bid}/upload/", {"file": f},
                              format="multipart")
        self.assertEqual(up.status_code, 201)
        self.assertEqual(up.data["doc_type"], "customer_po")
        self.assertGreaterEqual(up.data["summary"]["documents"], 1)
        # 3. staged entities include the auto-matched customer
        ents = self.client.get(f"/api/v1/import-batches/{bid}/entities/?kind=customer")
        self.assertEqual(ents.status_code, 200)
        cust = ents.data[0]
        self.assertEqual(cust["verdict"], "matched")
        self.assertEqual(cust["match_id"], str(self.existing.id))
        # 4. commit: link to the existing customer (no duplicate created)
        cm = self.client.post(
            f"/api/v1/import-batches/{bid}/entities/{cust['id']}/commit/",
            {"decision": "link"})
        self.assertEqual(cm.status_code, 200)
        self.assertEqual(cm.data["resolved_id"], str(self.existing.id))
        with tenant_scope(self.company.id):
            self.assertEqual(Customer.objects.count(), 1)

    def test_upload_requires_permission(self):
        viewer = _user(self.company, ["projects.view"], "v@a.co")
        self.client.force_authenticate(self.mgr)
        bid = self.client.post("/api/v1/import-batches/", {"label": "x"}).data["id"]
        self.client.force_authenticate(viewer)
        f = SimpleUploadedFile("po.txt", _PO, content_type="text/plain")
        up = self.client.post(f"/api/v1/import-batches/{bid}/upload/", {"file": f},
                              format="multipart")
        self.assertEqual(up.status_code, 403)
