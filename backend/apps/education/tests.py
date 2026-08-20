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
        self.assertContains(resp, "Start Free with LulaWorks")

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
        self.assertContains(page, "Start Free with LulaWorks")
        posted = self.client.post("/tools/vat-calculator/",
                                  {"amount": "100", "rate": "15", "mode": "exclusive"})
        self.assertContains(posted, "R115.00")
        self.assertEqual(self.client.get("/tools/does-not-exist/").status_code, 404)
