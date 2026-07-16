"""AI extraction layer tests — offline, with a stub provider. The live
Claude/OpenAI/Gemini path activates when an API key is configured."""

from decimal import Decimal

from django.test import TestCase, override_settings

from apps.ai_platform.gateway import AIProvider, AIResponse, allocate_credits, credit_balance
from apps.ai_platform.providers import NotConfiguredError, ai_configured, get_provider
from apps.core.context import tenant_scope
from apps.identity.models import Company

from .extraction import ExtractedValue, Extraction
from .intelligence import enrich_with_ai


class StubExtractor(AIProvider):
    name = "stub"

    def complete(self, prompt, **kwargs):
        return AIResponse(
            text='{"fields": {"work_type": {"value": "pump replacement", "confidence": 0.82}},'
                 ' "lines": [{"description": "Slurry pump", "qty": 2, "unit": "each",'
                 ' "unit_price": 15000}]}',
            provider="stub", tokens_in=200, tokens_out=40, credits_used=Decimal("1"),
        )


class ProviderConfigTests(TestCase):
    def test_not_configured_by_default(self):
        self.assertFalse(ai_configured())
        with self.assertRaises(NotConfiguredError):
            get_provider()

    @override_settings(AI_PROVIDER="claude", ANTHROPIC_API_KEY="test-key")
    def test_configured_returns_provider(self):
        self.assertTrue(ai_configured())
        self.assertEqual(get_provider().name, "claude")

    @override_settings(AI_PROVIDER="mystery")
    def test_unknown_provider_raises(self):
        with self.assertRaises(NotConfiguredError):
            get_provider("mystery")


class EnrichmentTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        allocate_credits(self.company, Decimal("100"))

    def _deterministic(self):
        e = Extraction(text="some rfq text")
        e.fields["po_number"] = ExtractedValue("5502442801", 1.0)  # deterministic present
        return e

    def test_ai_fills_gaps_and_meters(self):
        e = self._deterministic()
        with tenant_scope(self.company.id):
            enriched = enrich_with_ai(self.company, None, e, provider=StubExtractor(), force=True)
        # AI added a new field + lines; deterministic po_number preserved
        self.assertEqual(enriched.fields["po_number"].value, "5502442801")
        self.assertIn("work_type", enriched.fields)
        self.assertEqual(enriched.fields["work_type"].method, "ai_stub")
        self.assertEqual(len(enriched.lines), 1)
        # credits debited (100 - 1)
        self.assertEqual(credit_balance(self.company), Decimal("99"))

    def test_deterministic_value_not_overwritten(self):
        e = self._deterministic()
        e.fields["work_type"] = ExtractedValue("existing", 0.9)  # deterministic already has it
        with tenant_scope(self.company.id):
            enrich_with_ai(self.company, None, e, provider=StubExtractor(), force=True)
        self.assertEqual(e.fields["work_type"].value, "existing")  # AI did not overwrite

    def test_no_ai_when_unconfigured(self):
        # no provider passed, AI not configured → deterministic returned unchanged
        e = self._deterministic()
        enriched = enrich_with_ai(self.company, None, e)
        self.assertNotIn("work_type", enriched.fields)
        self.assertEqual(credit_balance(self.company), Decimal("100"))  # no spend
