"""Provider selection and the Gemini adapter.

The regression that matters: `get_provider(name)` used to validate the ACTIVE
provider's key rather than the one being asked for, so every fallback attempt
failed exactly when fallback was needed.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from .providers import (
    GeminiProvider,
    NotConfiguredError,
    ai_configured,
    configured_provider_names,
    get_provider,
)


class ProviderSelectionTests(TestCase):
    @override_settings(AI_PROVIDER="claude", ANTHROPIC_API_KEY="", GEMINI_API_KEY="k",
                       OPENAI_API_KEY="")
    def test_named_provider_is_usable_even_when_the_active_one_has_no_key(self):
        """The regression: Gemini is configured, Claude is not, and Claude is
        the nominal default — asking for Gemini must still work."""
        self.assertEqual(configured_provider_names(), ["gemini"])
        provider = get_provider("gemini")
        self.assertEqual(provider.name, "gemini")

    @override_settings(AI_PROVIDER="gemini", GEMINI_API_KEY="k", ANTHROPIC_API_KEY="",
                       OPENAI_API_KEY="")
    def test_active_provider_leads_the_fallback_order(self):
        self.assertEqual(configured_provider_names(), ["gemini"])
        self.assertTrue(ai_configured())

    @override_settings(AI_PROVIDER="gemini", GEMINI_API_KEY="k", ANTHROPIC_API_KEY="a",
                       OPENAI_API_KEY="")
    def test_other_keyed_providers_follow_as_fallback(self):
        self.assertEqual(configured_provider_names(), ["gemini", "claude"])

    @override_settings(AI_PROVIDER="gemini", GEMINI_API_KEY="", ANTHROPIC_API_KEY="",
                       OPENAI_API_KEY="")
    def test_no_keys_means_no_providers_and_the_deterministic_path_stands(self):
        self.assertEqual(configured_provider_names(), [])
        self.assertFalse(ai_configured())
        with self.assertRaises(NotConfiguredError):
            get_provider("gemini")


class GeminiAdapterTests(TestCase):
    @override_settings(GEMINI_API_KEY="", AI_PROVIDER="gemini")
    def test_missing_key_raises_not_configured(self):
        with self.assertRaises(NotConfiguredError):
            GeminiProvider().complete("hello")

    @override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="gemini-2.5-flash",
                       AI_CREDITS_PER_EXTRACTION="1")
    def test_completion_maps_text_and_token_usage(self):
        fake_response = MagicMock()
        fake_response.text = '{"ok": true}'
        fake_response.usage_metadata.prompt_token_count = 120
        fake_response.usage_metadata.candidates_token_count = 34

        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response
        genai_module = MagicMock()
        genai_module.Client.return_value = fake_client

        with patch.dict("sys.modules", {
            "google": MagicMock(genai=genai_module),
            "google.genai": genai_module,
            "google.genai.types": MagicMock(),
        }):
            resp = GeminiProvider().complete("extract this", system="be terse")

        self.assertEqual(resp.text, '{"ok": true}')
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(resp.tokens_in, 120)
        self.assertEqual(resp.tokens_out, 34)
        self.assertEqual(resp.credits_used, Decimal("1"))
        # The configured model must actually be the one requested.
        _, kwargs = fake_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-2.5-flash")


def _fake_gemini(text='{"ok": true}'):
    """Stand-in for the google-genai client that captures the config we send."""
    response = MagicMock()
    response.text = text
    response.usage_metadata.prompt_token_count = 10
    response.usage_metadata.candidates_token_count = 5

    client = MagicMock()
    client.models.generate_content.return_value = response
    genai_module = MagicMock()
    genai_module.Client.return_value = client

    class _Config:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    types_module = MagicMock()
    types_module.GenerateContentConfig = _Config
    types_module.ThinkingConfig = lambda **kw: kw
    # `from google.genai import types` resolves `types` as an ATTRIBUTE of the
    # google.genai module, so patching sys.modules alone is not enough.
    genai_module.types = types_module
    return client, {
        "google": MagicMock(genai=genai_module),
        "google.genai": genai_module,
        "google.genai.types": types_module,
    }


class GeminiConfigTests(TestCase):
    """The two settings that decide whether Gemini returns anything usable."""

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="gemini-2.5-flash",
                       GEMINI_THINKING_BUDGET="0", AI_CREDITS_PER_EXTRACTION="1")
    def test_thinking_is_disabled_so_it_cannot_starve_the_answer(self):
        """Gemini 2.5 draws thinking tokens from the OUTPUT budget. Left on, a
        long extraction prompt spends the budget thinking and returns empty
        text — which is exactly what happened before this was set."""
        client, modules = _fake_gemini()
        with patch.dict("sys.modules", modules):
            GeminiProvider().complete("extract this")
        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.thinking_config, {"thinking_budget": 0})

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="gemini-2.5-flash",
                       GEMINI_THINKING_BUDGET="0", AI_CREDITS_PER_EXTRACTION="1")
    def test_json_mode_asks_for_native_json(self):
        client, modules = _fake_gemini()
        with patch.dict("sys.modules", modules):
            GeminiProvider().complete("extract", json_mode=True)
        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.response_mime_type, "application/json")

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="gemini-2.5-flash",
                       GEMINI_THINKING_BUDGET="0", AI_CREDITS_PER_EXTRACTION="1")
    def test_prose_calls_do_not_force_json(self):
        client, modules = _fake_gemini()
        with patch.dict("sys.modules", modules):
            GeminiProvider().complete("write a briefing")
        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertFalse(hasattr(config, "response_mime_type"))

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="gemini-2.5-flash",
                       GEMINI_THINKING_BUDGET="0", AI_CREDITS_PER_EXTRACTION="1")
    def test_unknown_kwargs_are_tolerated(self):
        """A provider-specific option must never break a call that fell back to
        a different provider."""
        client, modules = _fake_gemini()
        with patch.dict("sys.modules", modules):
            resp = GeminiProvider().complete("hi", some_future_option=True)
        self.assertEqual(resp.provider, "gemini")
