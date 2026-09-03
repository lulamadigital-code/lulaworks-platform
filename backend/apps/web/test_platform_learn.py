"""Platform-console Learning-centre manager — create/edit content without ever
touching Django admin. Editing is gated on the `settings` platform capability."""
from django.test import TestCase

from apps.education.models import ContentStatus, Resource, ResourceCategory
from apps.identity.models import User


class PlatformLearnTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@lulaworks.com", "x")
        self.owner.is_superuser = True          # → platform_level "owner"
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)

    def test_page_loads(self):
        r = self.client.get("/platform/learn/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Learning centre")

    def test_add_category_then_article(self):
        r = self.client.post("/platform/learn/", {
            "action": "save_category", "name": "Quoting", "icon": "📈", "order": "10"})
        self.assertRedirects(r, "/platform/learn/")
        cat = ResourceCategory.objects.get(name="Quoting")
        self.assertEqual(cat.slug, "quoting")

        self.client.post("/platform/learn/", {
            "action": "save", "title": "How to win more quotes",
            "kind": "guide", "category": str(cat.pk), "difficulty": "beginner",
            "read_minutes": "6", "summary": "Practical quoting tips",
            "body": "# Body", "is_published": "on"})
        res = Resource.objects.get(title="How to win more quotes")
        self.assertEqual(res.category_id, cat.pk)
        self.assertEqual(res.status, ContentStatus.PUBLISHED)
        self.assertIsNotNone(res.published_at)     # set on publish
        self.assertEqual(res.author, self.owner)
        self.assertEqual(res.slug, "how-to-win-more-quotes")

    def test_draft_when_unpublished(self):
        self.client.post("/platform/learn/", {
            "action": "save", "title": "Draft piece", "kind": "article",
            "difficulty": "beginner", "read_minutes": "4"})
        res = Resource.objects.get(title="Draft piece")
        self.assertEqual(res.status, ContentStatus.DRAFT)
        self.assertIsNone(res.published_at)

    def test_edit_and_delete(self):
        res = Resource.objects.create(title="Temp", author=self.owner)
        edit = self.client.get(f"/platform/learn/?edit={res.pk}")
        self.assertEqual(edit.status_code, 200)
        self.assertContains(edit, "Temp")
        self.client.post("/platform/learn/",
                         {"action": "delete", "id": str(res.pk)})
        self.assertFalse(Resource.objects.filter(pk=res.pk).exists())

    def test_non_staff_cannot_reach_it(self):
        plain = User.objects.create_user("nobody@x.co", "x")   # no platform role
        self.client.force_login(plain)
        r = self.client.get("/platform/learn/", follow=True)
        self.assertNotContains(r, "Learning centre", status_code=200)

    def test_viewer_without_settings_cannot_edit(self):
        from apps.identity.models import User as U
        support = U.objects.create_user("support@lulaworks.com", "x")
        support.platform_role = "support"      # console+support, NOT settings
        support.save()
        self.client.force_login(support)
        self.assertEqual(self.client.get("/platform/learn/").status_code, 200)
        self.client.post("/platform/learn/",
                         {"action": "save", "title": "Should not save"})
        self.assertFalse(Resource.objects.filter(title="Should not save").exists())
