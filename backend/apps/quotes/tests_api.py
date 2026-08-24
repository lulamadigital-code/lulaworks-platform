"""API tests for the quotation workflow — detail with lines/totals, next-status
workflow, permission-gated transition, and the PDF endpoint."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User

from .services import create_quotation


class QuotationWorkflowAPITests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        codes = ["quotes.create", "quotes.approve", "quotes.download",
                 "finance.view_money"]
        perms = [Permission.objects.create(codename=c, module=c.split(".")[0], label=c)
                 for c in codes]
        commercial = Role.objects.create(name="Commercial", is_system=True)
        commercial.permissions.add(*perms)
        worker_role = Role.objects.create(name="Worker", is_system=True)

        self.user = User.objects.create_user(
            "sales@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.user, company=self.company, role=commercial)
        self.worker = User.objects.create_user(
            "hand@lulama.co.za", "pass12345", active_company=self.company)
        Membership.objects.create(user=self.worker, company=self.company, role=worker_role)

        with tenant_scope(self.company.id):
            self.quote = create_quotation(
                self.company, self.user, client_name="Sasol Secunda",
                title="Pump overhaul",
                lines=[{"description": "Strip & inspect", "qty": 1, "unit": "job",
                        "unit_price": "1000.00"}])

    def _auth(self, u):
        self.client.force_authenticate(user=u)

    def test_detail_has_lines_and_totals(self):
        self._auth(self.user)
        r = self.client.get(f"/api/v1/quotations/{self.quote.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.data["lines"]), 1)
        self.assertIn("total", r.data)  # money user sees totals

    def test_workflow_lists_next_statuses(self):
        self._auth(self.user)
        r = self.client.get(f"/api/v1/quotations/{self.quote.id}/workflow/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["next"], "a draft should have a next step")

    def test_transition_moves_status(self):
        self._auth(self.user)
        nxt = self.client.get(
            f"/api/v1/quotations/{self.quote.id}/workflow/").data["next"][0]["value"]
        r = self.client.post(f"/api/v1/quotations/{self.quote.id}/transition/",
                             {"to_status": nxt}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], nxt)

    def test_worker_cannot_transition(self):
        self._auth(self.worker)
        r = self.client.post(f"/api/v1/quotations/{self.quote.id}/transition/",
                             {"to_status": "review"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_pdf_requires_download_permission(self):
        self._auth(self.worker)  # no quotes.download
        self.assertEqual(
            self.client.get(f"/api/v1/quotations/{self.quote.id}/pdf/").status_code, 403)
        self._auth(self.user)   # has quotes.download
        ok = self.client.get(f"/api/v1/quotations/{self.quote.id}/pdf/")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok["Content-Type"], "application/pdf")
