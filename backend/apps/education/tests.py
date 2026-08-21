from django.core.management import call_command
from django.test import TestCase

from apps.education.models import ContentStatus, Resource, ResourceKind
from apps.education.services import prompt_for


class EducationSeedTests(TestCase):
    def test_seed_creates_published_content(self):
        call_command("seed_education")
        pub = Resource.objects.filter(status=ContentStatus.PUBLISHED)
        self.assertGreaterEqual(pub.count(), 3)
        # Slugs are auto-filled and unique.
        self.assertTrue(all(r.slug for r in pub))

    def test_seed_is_idempotent(self):
        call_command("seed_education")
        n = Resource.objects.count()
        call_command("seed_education")
        self.assertEqual(Resource.objects.count(), n)


class LearningCentreTests(TestCase):
    def setUp(self):
        self.r = Resource.objects.create(
            kind=ResourceKind.GUIDE, title="How to quote well",
            summary="Do it right.", body="<p>Body</p>",
            status=ContentStatus.PUBLISHED, related_features=["quotations"])
        self.draft = Resource.objects.create(
            kind=ResourceKind.ARTICLE, title="Secret draft",
            status=ContentStatus.DRAFT)

    def test_learn_index_lists_published(self):
        resp = self.client.get("/learn/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "How to quote well")
        self.assertNotContains(resp, "Secret draft")

    def test_resource_detail_renders(self):
        resp = self.client.get(f"/learn/{self.r.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Body")
        self.assertContains(resp, "Start Free with Lulaworks")

    def test_draft_resource_404s(self):
        resp = self.client.get(f"/learn/{self.draft.slug}/")
        self.assertEqual(resp.status_code, 404)

    def test_prompt_for_feature(self):
        self.assertEqual(prompt_for("quotations"), self.r)
        self.assertIsNone(prompt_for("nonexistent"))
        self.assertIsNone(prompt_for(""))


class FreeToolsTests(TestCase):
    def _row(self, results, label):
        return next(r["value"] for r in results if r.get("label") == label)

    def test_job_profit_compute(self):
        from apps.education.tools import compute
        res = compute("job-profit-calculator", {
            "contract": "10000", "materials": "3000", "labour": "2000",
            "transport": "500", "subcontractors": "0", "other": "500"})
        self.assertEqual(self._row(res, "Total cost"), "R6,000.00")
        self.assertEqual(self._row(res, "Gross profit"), "R4,000.00")
        self.assertEqual(self._row(res, "Profit margin"), "40.0%")

    def test_markup_vs_margin_are_different(self):
        from apps.education.tools import compute
        res = compute("markup-margin-calculator", {"cost": "100", "sell": "130"})
        self.assertEqual(self._row(res, "Profit"), "R30.00")
        self.assertEqual(self._row(res, "Markup"), "30.0%")
        self.assertEqual(self._row(res, "Margin"), "23.1%")   # not 30%!

    def test_vat_inclusive_and_exclusive(self):
        from apps.education.tools import compute
        excl = compute("vat-calculator", {"amount": "100", "rate": "15", "mode": "exclusive"})
        self.assertEqual(self._row(excl, "Total (incl. VAT)"), "R115.00")
        incl = compute("vat-calculator", {"amount": "115", "rate": "15", "mode": "inclusive"})
        self.assertEqual(self._row(incl, "Net (excl. VAT)"), "R100.00")

    def test_break_even_guards_bad_price(self):
        from apps.education.tools import compute
        ok = compute("break-even-calculator", {"fixed": "1000", "price": "100", "variable": "60"})
        self.assertEqual(self._row(ok, "Break-even units / jobs"), "25")
        bad = compute("break-even-calculator", {"fixed": "1000", "price": "50", "variable": "60"})
        self.assertIn("error", bad[0])

    def test_tools_index_and_tool_pages(self):
        idx = self.client.get("/tools/")
        self.assertEqual(idx.status_code, 200)
        self.assertContains(idx, "Job Profit Calculator")
        page = self.client.get("/tools/vat-calculator/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Start Free with Lulaworks")
        posted = self.client.post("/tools/vat-calculator/",
                                  {"amount": "100", "rate": "15", "mode": "exclusive"})
        self.assertContains(posted, "R115.00")
        self.assertEqual(self.client.get("/tools/does-not-exist/").status_code, 404)


class TemplatesLibraryTests(TestCase):
    def test_index_lists_templates(self):
        resp = self.client.get("/templates/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Professional Quotation Template")
        self.assertContains(resp, "Payment Follow-up Email Templates")

    def test_document_template_shows_includes_and_cta(self):
        resp = self.client.get("/templates/professional-quotation-template/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "What a good one includes")
        self.assertContains(resp, "Create your free professional quotation")
        self.assertContains(resp, "Start Free with Lulaworks")

    def test_checklist_template_renders_items(self):
        resp = self.client.get("/templates/site-handover-checklist/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Customer sign-off obtained")

    def test_email_template_renders_samples(self):
        resp = self.client.get("/templates/payment-follow-up-templates/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Friendly reminder")

    def test_unknown_template_404s(self):
        self.assertEqual(self.client.get("/templates/nope/").status_code, 404)


class LeadCaptureTests(TestCase):
    def test_capture_creates_and_scores_lead(self):
        from apps.education.leads import capture_lead
        from apps.education.models import EducationLead
        lead = capture_lead(email="Bob@Acme.CO.ZA", event="tool_used",
                            detail="vat-calculator", name="Bob", company="Acme")
        self.assertIsNotNone(lead)
        self.assertEqual(lead.email, "bob@acme.co.za")     # normalised
        self.assertEqual(lead.score, 3)                    # tool_used = 3
        self.assertEqual(lead.name, "Bob")
        # A second action on the same email accrues, doesn't duplicate the lead.
        capture_lead(email="bob@acme.co.za", event="template_used", detail="rfq")
        self.assertEqual(EducationLead.objects.count(), 1)
        lead.refresh_from_db()
        self.assertEqual(lead.score, 5)                    # 3 + 2
        self.assertEqual(lead.events.count(), 2)

    def test_capture_validates_email(self):
        from apps.education.leads import capture_lead
        for bad in ["not-an-email", "", "   ", "x@", "@y.com",
                    "user name@x.com", "two@@at.com", "trailing@dot."]:
            self.assertIsNone(capture_lead(email=bad), f"should reject {bad!r}")
        # A proper address is accepted (and normalised).
        self.assertIsNotNone(capture_lead(email="Valid.User@Example.CO.ZA"))

    def test_capture_endpoint_rejects_bad_email(self):
        from apps.education.models import EducationLead
        resp = self.client.post("/grow/", {"email": "nope", "event": "opt_in"},
                                follow=True)
        self.assertContains(resp, "valid email")          # error surfaced
        self.assertFalse(EducationLead.objects.filter(email="nope").exists())

    def test_does_not_overwrite_profile_with_blanks(self):
        from apps.education.leads import capture_lead
        capture_lead(email="c@c.co", event="opt_in", company="First Co")
        lead = capture_lead(email="c@c.co", event="opt_in", company="")
        self.assertEqual(lead.company, "First Co")

    def test_capture_endpoint_redirects_to_thanks(self):
        from apps.education.models import EducationLead
        resp = self.client.post("/grow/", {"email": "lead@co.za", "event": "opt_in",
                                           "source": "academy"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/grow/thanks/", resp.url)
        self.assertTrue(EducationLead.objects.filter(email="lead@co.za").exists())

    def test_signup_scores_existing_lead(self):
        from apps.education.leads import capture_lead, score_signup
        capture_lead(email="signup@co.za", event="tool_used", detail="x")
        score_signup("signup@co.za")
        from apps.education.models import EducationLead
        lead = EducationLead.objects.get(email="signup@co.za")
        self.assertTrue(lead.has_account)
        self.assertEqual(lead.score, 8)                    # 3 (tool) + 5 (account)


class WelcomeAndUnsubscribeTests(TestCase):
    def test_new_lead_gets_one_welcome_email(self):
        from apps.education.leads import capture_lead
        from apps.education.models import EducationLead
        from apps.notifications.models import EmailLog
        capture_lead(email="new@co.za", event="opt_in", name="Jane Doe")
        logs = EmailLog.objects.filter(to_email="new@co.za",
                                       template="academy_welcome")
        self.assertEqual(logs.count(), 1)
        lead = EducationLead.objects.get(email="new@co.za")
        self.assertIsNotNone(lead.welcomed_at)
        # A second action doesn't send another welcome.
        capture_lead(email="new@co.za", event="tool_used", detail="vat")
        self.assertEqual(EmailLog.objects.filter(
            to_email="new@co.za", template="academy_welcome").count(), 1)

    def test_unsubscribe_token_roundtrip(self):
        from apps.education.leads import (
            capture_lead,
            lead_from_token,
            unsubscribe_token,
        )
        lead = capture_lead(email="bye@co.za", event="opt_in")
        token = unsubscribe_token(lead)
        self.assertEqual(lead_from_token(token), lead)
        self.assertIsNone(lead_from_token("garbage.token.value"))

    def test_unsubscribe_view_marks_lead(self):
        from apps.education.leads import capture_lead, unsubscribe_token
        from apps.education.models import EducationLead
        lead = capture_lead(email="stop@co.za", event="opt_in")
        resp = self.client.get(f"/grow/unsubscribe/{unsubscribe_token(lead)}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "unsubscribed")
        self.assertFalse(EducationLead.objects.get(email="stop@co.za").subscribed)

    def test_bad_unsubscribe_token_is_handled(self):
        resp = self.client.get("/grow/unsubscribe/not-a-real-token/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "invalid")


class CrmBridgeTests(TestCase):
    def _sales_company(self):
        from apps.identity.models import Company
        c = Company.objects.create(name="Lulaworks Sales")
        c.receives_education_leads = True
        c.save()
        return c

    def test_no_sales_company_is_noop(self):
        from apps.education.leads import capture_lead
        # score >= threshold but no company marked → bridge does nothing, no error
        lead = capture_lead(email="a@a.co", event="account_created")   # +5
        self.assertIsNotNone(lead)

    def test_hot_lead_syncs_into_crm(self):
        from apps.core.context import tenant_scope
        from apps.customers.models import Lead as CrmLead
        from apps.education.leads import capture_lead
        company = self._sales_company()
        capture_lead(email="hot@build.co.za", event="account_created",  # +5 → hot
                     name="Sipho", company="Build It", industry="Construction")
        with tenant_scope(company.id):
            crm = CrmLead.objects.get(email="hot@build.co.za")
            self.assertEqual(crm.company_name, "Build It")
            self.assertEqual(crm.contact_name, "Sipho")
            self.assertEqual(crm.source, "Lulaworks Academy")

    def test_cold_lead_not_synced(self):
        from apps.core.context import tenant_scope
        from apps.customers.models import Lead as CrmLead
        from apps.education.leads import capture_lead
        company = self._sales_company()
        capture_lead(email="cold@x.co", event="content_read")           # +1, below 5
        with tenant_scope(company.id):
            self.assertFalse(CrmLead.objects.filter(email="cold@x.co").exists())

    def test_sync_is_idempotent(self):
        from apps.core.context import tenant_scope
        from apps.customers.models import Lead as CrmLead
        from apps.education.leads import capture_lead, sync_lead_to_crm
        from apps.education.models import EducationLead
        company = self._sales_company()
        capture_lead(email="dup@x.co", event="account_created")
        sync_lead_to_crm(EducationLead.objects.get(email="dup@x.co"))   # again
        with tenant_scope(company.id):
            self.assertEqual(CrmLead.objects.filter(email="dup@x.co").count(), 1)
