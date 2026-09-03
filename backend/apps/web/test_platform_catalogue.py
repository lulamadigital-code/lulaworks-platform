"""Free tools + Templates are now DB-backed and editable in the platform console
(owner/admin only); the public pages render from the DB, and calculator maths
stays in code keyed by compute_key."""
from django.test import TestCase

from apps.education.models import ContentStatus, Template, Tool
from apps.identity.models import User


class CatalogueSeedTests(TestCase):
    def test_migration_seeded_the_catalogues(self):
        # data migration 0005 loads the code specs into the DB
        self.assertTrue(Tool.objects.filter(slug="job-profit-calculator").exists())
        self.assertGreaterEqual(Template.objects.count(), 5)
        vat = Tool.objects.get(slug="vat-calculator")
        self.assertEqual(vat.compute_key, "vat-calculator")
        self.assertTrue(vat.inputs)                    # inputs migrated


class PublicRendersFromDbTests(TestCase):
    def test_tools_index_and_detail_from_db(self):
        Tool.objects.filter(slug="job-profit-calculator").update(title="DB Profit Tool")
        idx = self.client.get("/tools/")
        self.assertEqual(idx.status_code, 200)
        self.assertContains(idx, "DB Profit Tool")
        detail = self.client.get("/tools/job-profit-calculator/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "DB Profit Tool")

    def test_calculator_maths_still_runs(self):
        r = self.client.post("/tools/job-profit-calculator/",
                             {"contract": "1000", "materials": "200", "labour": "100",
                              "transport": "0", "subcontractors": "0", "other": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Gross profit")        # compute() ran via compute_key

    def test_templates_index_and_detail_from_db(self):
        Template.objects.filter(slug="tax-invoice-template").update(title="DB Invoice Tpl")
        idx = self.client.get("/templates/")
        self.assertEqual(idx.status_code, 200)
        self.assertContains(idx, "DB Invoice Tpl")
        self.assertEqual(
            self.client.get("/templates/tax-invoice-template/").status_code, 200)


class ConsoleEditingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@lulaworks.com", "x")
        self.owner.is_superuser = True
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)

    def test_edit_tool_presentation(self):
        t = Tool.objects.get(slug="markup-margin-calculator")
        self.client.post("/platform/tools/", {
            "action": "save", "id": str(t.pk), "title": "Markup Helper",
            "summary": "new copy", "category": "profitability",
            "related_feature": "quotations", "icon": "🧮",
            "problem": "<p>p</p>", "explainer": "<p>e</p>",
            "inputs": '[{"name":"cost","label":"Cost","kind":"money","default":"0"}]',
            "compute_key": "markup-margin-calculator", "cta_label": "Go",
            "cta_url": "/start-free-trial/", "order": "20", "is_published": "on"})
        t.refresh_from_db()
        self.assertEqual(t.title, "Markup Helper")
        self.assertEqual(t.inputs[0]["name"], "cost")

    def test_bad_inputs_json_rejected(self):
        t = Tool.objects.get(slug="vat-calculator")
        r = self.client.post("/platform/tools/", {
            "action": "save", "id": str(t.pk), "title": "VAT",
            "inputs": "{not json"}, follow=True)
        self.assertContains(r, "valid JSON")

    def test_create_template(self):
        self.client.post("/platform/content-templates/", {
            "action": "save", "title": "Warranty Letter", "kind": "document",
            "category": "getting-paid", "icon": "📄", "summary": "s",
            "includes": "Line one\nLine two", "is_published": "on"})
        t = Template.objects.get(title="Warranty Letter")
        self.assertEqual(t.includes, ["Line one", "Line two"])
        self.assertEqual(t.status, ContentStatus.PUBLISHED)

    def test_draft_tool_preview_gated(self):
        d = Tool.objects.create(title="Secret calc", status=ContentStatus.DRAFT)
        self.client.logout()
        self.assertEqual(self.client.get(f"/tools/{d.slug}/").status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(f"/tools/{d.slug}/?preview=1").status_code, 200)

    def test_preview_embeds_in_console_not_marketing(self):
        # Preview for a tool renders INSIDE the console (sidebar present) and
        # embeds the public page in an iframe — it never navigates to /tools/.
        r = self.client.get("/platform/preview/tool/job-profit-calculator/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "<iframe")
        self.assertContains(r, "/tools/job-profit-calculator/?preview=1")
        self.assertContains(r, "Learning centre")          # console shell, still in admin

    def test_preview_template_and_learn(self):
        self.assertEqual(
            self.client.get("/platform/preview/template/tax-invoice-template/").status_code, 200)
        from apps.education.models import Resource
        res = Resource.objects.create(title="Guide X", author=self.owner)
        r = self.client.get(f"/platform/preview/learn/{res.slug}/")
        self.assertContains(r, f"/learn/{res.slug}/?preview=1")

    def test_preview_missing_item_redirects_to_editor(self):
        r = self.client.get("/platform/preview/tool/does-not-exist/", follow=True)
        self.assertContains(r, "no longer exists")

    def test_only_owner_admin_can_edit(self):
        support = User.objects.create_user("support@lulaworks.com", "x")
        support.platform_role = "support"          # console+support, NOT settings
        support.save()
        self.client.force_login(support)
        self.assertEqual(self.client.get("/platform/tools/").status_code, 200)
        self.client.post("/platform/tools/",
                         {"action": "save", "title": "Nope"})
        self.assertFalse(Tool.objects.filter(title="Nope").exists())
