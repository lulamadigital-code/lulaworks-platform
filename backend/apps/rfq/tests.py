"""RFQ Intelligence tests: deterministic extraction, upload→review→approve
pipeline, Project DNA minting, tenant isolation, human-approval boundary."""

from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge.models import ProjectDNA
from apps.quotes.models import Quotation

from .extraction import extract_rfq, to_decimal
from .models import RFQDocument, RFQStatus

FIXTURE = Path(__file__).parent / "fixtures" / "sample_rfq.pdf"


class ExtractionTests(APITestCase):
    def test_sa_number_formats(self):
        self.assertEqual(to_decimal("486,00"), Decimal("486.00"))
        self.assertEqual(to_decimal("29 160,00"), Decimal("29160.00"))
        self.assertEqual(to_decimal("R 2 640,00"), Decimal("2640.00"))
        self.assertEqual(to_decimal("485.00"), Decimal("485.00"))

    def test_extracts_fields_and_lines(self):
        e = extract_rfq(str(FIXTURE))
        self.assertEqual(e.fields["po_number"].value, "5502446497")
        self.assertEqual(e.fields["po_number"].confidence, 1.0)
        self.assertEqual(e.fields["order_date"].value, "2026/07/10")
        self.assertEqual(len(e.lines), 3)
        self.assertEqual(e.lines[0].unit_price, Decimal("485.00"))

    def test_ocr_fallback_when_no_text_layer(self):
        """Scanned PDF (no text layer) → OCR fallback is used."""
        from apps.rfq import extraction as ex

        orig_pdf, orig_ocr = ex._pdfplumber_text, ex._ocr_text
        ex._pdfplumber_text = lambda src: ""          # simulate scanned (no text)
        ex._ocr_text = lambda data: "PO NUMBER 5502442801 scanned"
        try:
            text = ex.extract_text(str(FIXTURE))
        finally:
            ex._pdfplumber_text, ex._ocr_text = orig_pdf, orig_ocr
        self.assertIn("5502442801", text)  # came from the OCR path


class RFQPipelineTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        self.other = Company.objects.create(name="Rival")
        NumberingRule.objects.create(company=self.company, doc_type="quotation",
                                     prefix="QT", fmt="{prefix}-{yyyy}-{seq:06d}")
        upload = Permission.objects.create(codename="rfq.upload", module="rfq", label="Upload")
        approve = Permission.objects.create(codename="rfq.approve", module="rfq", label="Approve")
        self.full_role = Role.objects.create(name="Estimator", is_system=True)
        self.full_role.permissions.add(upload, approve)
        self.upload_only = Role.objects.create(name="Clerk", is_system=True)
        self.upload_only.permissions.add(upload)

        self.estimator = User.objects.create_user("est@lulama.co.za", "x",
                                                  active_company=self.company)
        Membership.objects.create(user=self.estimator, company=self.company, role=self.full_role)
        self.clerk = User.objects.create_user("clerk@lulama.co.za", "x",
                                              active_company=self.company)
        Membership.objects.create(user=self.clerk, company=self.company, role=self.upload_only)
        self.rival = User.objects.create_user("rival@rival.co.za", "x", active_company=self.other)
        Membership.objects.create(user=self.rival, company=self.other, role=self.full_role)

    def _upload(self, user):
        self.client.force_authenticate(user)
        with open(FIXTURE, "rb") as fh:
            data = fh.read()
        return self.client.post("/api/v1/rfqs/", {
            "file": SimpleUploadedFile("sample_rfq.pdf", data, content_type="application/pdf")
        }, format="multipart")

    def test_upload_extracts_and_enters_review(self):
        resp = self._upload(self.estimator)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["status"], RFQStatus.IN_REVIEW)
        keys = {f["key"] for f in resp.data["fields"]}
        self.assertIn("po_number", keys)
        self.assertEqual(len(resp.data["lines"]), 3)

    def test_upload_requires_permission(self):
        # a user with no rfq.upload
        nobody = User.objects.create_user("no@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=nobody, company=self.company,
                                  role=Role.objects.create(name="None", is_system=True))
        self.client.force_authenticate(nobody)
        with open(FIXTURE, "rb") as fh:
            resp = self.client.post("/api/v1/rfqs/", {
                "file": SimpleUploadedFile("x.pdf", fh.read())
            }, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_approve_creates_quotation_and_project_dna(self):
        rfq_id = self._upload(self.estimator).data["id"]
        resp = self.client.post(f"/api/v1/rfqs/{rfq_id}/approve/", {"client_name": "Sibanye"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["quotation"]["number"].startswith("QT-"))
        with tenant_scope(self.company.id):
            self.assertEqual(Quotation.objects.count(), 1)
            self.assertEqual(ProjectDNA.objects.count(), 1)
            dna = ProjectDNA.objects.first()
            self.assertEqual(dna.client_name, "Sibanye")
            self.assertEqual(len(dna.materials), 3)  # 3 line descriptions

    def test_approve_requires_rfq_approve_permission(self):
        rfq_id = self._upload(self.clerk).data["id"]  # clerk can upload
        resp = self.client.post(f"/api/v1/rfqs/{rfq_id}/approve/", {"client_name": "Sibanye"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # but not approve

    def test_no_auto_approval(self):
        rfq_id = self._upload(self.estimator).data["id"]
        with tenant_scope(self.company.id):
            rfq = RFQDocument.objects.get(id=rfq_id)
            self.assertEqual(rfq.status, RFQStatus.IN_REVIEW)  # never auto-approved
            self.assertEqual(Quotation.objects.count(), 0)  # no quote until human approves

    def test_tenant_isolation(self):
        rfq_id = self._upload(self.estimator).data["id"]
        self.client.force_authenticate(self.rival)
        resp = self.client.get(f"/api/v1/rfqs/{rfq_id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_review_edit_field(self):
        upload = self._upload(self.estimator)
        rfq_id = upload.data["id"]
        field_id = upload.data["fields"][0]["id"]
        resp = self.client.patch(f"/api/v1/rfqs/{rfq_id}/review/", {
            "fields": [{"id": field_id, "approved_value": "CORRECTED"}]
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        edited = next(f for f in resp.data["fields"] if f["id"] == field_id)
        self.assertEqual(edited["approved_value"], "CORRECTED")
        self.assertEqual(edited["review_status"], "edited")
