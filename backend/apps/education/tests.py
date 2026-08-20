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
