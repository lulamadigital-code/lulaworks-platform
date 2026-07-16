from django.test import TestCase

from apps.identity.models import Company

from .models import AuditLog, FeatureFlagDefinition, FeatureFlagOverride, NumberingRule
from .services import feature_enabled, next_number, record_audit


class NumberingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")

    def test_configurable_format_and_increment(self):
        NumberingRule.objects.create(
            company=self.company, doc_type="quotation", prefix="QT", fmt="{prefix}-{yyyy}-{seq:06d}"
        )
        year = __import__("datetime").date.today().year
        n1 = next_number(self.company, "quotation")
        n2 = next_number(self.company, "quotation")
        self.assertEqual(n1, f"QT-{year}-000001")
        self.assertEqual(n2, f"QT-{year}-000002")

    def test_default_rule_when_none_configured(self):
        n = next_number(self.company, "invoice")
        self.assertTrue(n.startswith("INV-"))

    def test_sequences_isolated_per_company(self):
        other = Company.objects.create(name="Other")
        next_number(self.company, "project")
        n = next_number(other, "project")
        self.assertTrue(n.endswith("000001"))


class FeatureFlagTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")

    def test_resolution_override_then_default(self):
        FeatureFlagDefinition.objects.create(key="ai_quoting", default_enabled=False)
        self.assertFalse(feature_enabled(self.company, "ai_quoting"))
        FeatureFlagOverride.objects.create(company=self.company, key="ai_quoting", enabled=True)
        self.assertTrue(feature_enabled(self.company, "ai_quoting"))

    def test_unknown_flag_defaults_false(self):
        self.assertFalse(feature_enabled(self.company, "nope"))


class AuditTests(TestCase):
    def test_record_audit(self):
        company = Company.objects.create(name="Lulama")
        log = record_audit(company=company, action="company.created", entity=company)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(log.entity_type, "Company")
        self.assertEqual(log.entity_id, company.id)
