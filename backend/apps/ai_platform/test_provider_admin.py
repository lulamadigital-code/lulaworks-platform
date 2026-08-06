"""Provider management — the admin-tunable, key-free settings, and their effect
on routing. Keys are never stored here; only enable/priority/model overrides."""

from django.test import TestCase, override_settings

from . import provider_admin as pa
from .models import AIProviderSetting
from .routing import TaskType, route

ALL_KEYS = dict(GEMINI_API_KEY="g", ANTHROPIC_API_KEY="a", OPENAI_API_KEY="o")


class ProviderSettingTests(TestCase):
    def test_ensure_creates_all_known_providers(self):
        pa.ensure_provider_settings()
        self.assertEqual(
            set(AIProviderSetting.objects.values_list("provider", flat=True)),
            {"gemini", "claude", "openai"})
        # Idempotent.
        pa.ensure_provider_settings()
        self.assertEqual(AIProviderSetting.objects.count(), 3)

    def test_enabled_by_default(self):
        self.assertTrue(pa.is_enabled("gemini"))   # no row yet → on

    @override_settings(**ALL_KEYS)
    def test_disabling_a_provider_removes_it_from_routing(self):
        pa.set_enabled("gemini", False)
        chain = route(TaskType.EXTRACTION)         # extraction prefers Gemini
        self.assertNotIn("gemini", chain)
        self.assertEqual(chain[0], "claude")       # fell to next preference
        # Re-enabling brings it back to the front.
        pa.set_enabled("gemini", True)
        self.assertEqual(route(TaskType.EXTRACTION)[0], "gemini")

    def test_no_key_stored_anywhere_on_the_model(self):
        # Guard against a future field creeping in that would hold a secret.
        field_names = {f.name for f in AIProviderSetting._meta.get_fields()}
        self.assertFalse(any("key" in n or "secret" in n for n in field_names))


class ProviderStatusTests(TestCase):
    @override_settings(GEMINI_API_KEY="g", ANTHROPIC_API_KEY="", OPENAI_API_KEY="")
    def test_status_reports_configured_and_state(self):
        rows = {r["provider"]: r for r in pa.provider_status()}
        self.assertTrue(rows["gemini"]["configured"])
        self.assertEqual(rows["gemini"]["state"], "Ready")
        self.assertFalse(rows["claude"]["configured"])
        self.assertEqual(rows["claude"]["state"], "No API key set")

    @override_settings(GEMINI_API_KEY="g")
    def test_disabled_provider_shows_disabled_state(self):
        pa.set_enabled("gemini", False)
        rows = {r["provider"]: r for r in pa.provider_status()}
        self.assertEqual(rows["gemini"]["state"], "Disabled by admin")
        self.assertFalse(rows["gemini"]["ready"])

    @override_settings(GEMINI_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="")
    def test_test_connection_reports_no_key_without_calling(self):
        result = pa.test_connection("gemini")
        self.assertFalse(result["ok"])
        self.assertIn("No API key", result["detail"])
