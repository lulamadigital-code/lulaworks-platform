from django.test import TestCase

from apps.identity.models import Company

from .services import check_quota, register_upload


class QuotaTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama", storage_quota_bytes=1000,
                                              storage_used_bytes=0)

    def test_allows_within_quota(self):
        self.assertTrue(check_quota(self.company, 500).allowed)

    def test_warns_over_90_percent(self):
        self.company.storage_used_bytes = 850
        result = check_quota(self.company, 100)  # 950/1000
        self.assertTrue(result.allowed)
        self.assertTrue(result.warn)

    def test_blocks_over_quota(self):
        self.company.storage_used_bytes = 900
        result = check_quota(self.company, 200)  # 1100 > 1000
        self.assertFalse(result.allowed)
        self.assertIn("quota", result.reason.lower())

    def test_register_upload_increments_usage(self):
        register_upload(self.company, 300)
        self.company.refresh_from_db()
        self.assertEqual(self.company.storage_used_bytes, 300)
