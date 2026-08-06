"""Task routing + failover — the orchestration layer decides which model.

What these protect: extraction prefers Gemini, reasoning prefers Claude,
generation prefers OpenAI; a task always falls back to any other available
provider; and a failing provider fails over to the next with an auditable trail
— never surfacing the failure to the caller while another provider can answer.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.identity.models import Company

from . import routing
from .gateway import (
    AIResponse,
    AllProvidersFailedError,
    allocate_credits,
    run_task,
)
from .models import AIUsageLog
from .routing import TaskType, route


ALL_KEYS = dict(GEMINI_API_KEY="g", ANTHROPIC_API_KEY="a", OPENAI_API_KEY="o",
                GEMINI_THINKING_BUDGET="0", AI_CREDITS_PER_EXTRACTION="1")


class RoutingTableTests(TestCase):
    @override_settings(**ALL_KEYS)
    def test_task_preferences(self):
        self.assertEqual(route(TaskType.EXTRACTION)[0], "gemini")
        self.assertEqual(route(TaskType.REASONING)[0], "claude")
        self.assertEqual(route(TaskType.GENERATION)[0], "openai")
        self.assertEqual(route(TaskType.IMAGE)[0], "gemini")
        self.assertEqual(route(TaskType.SUMMARY)[0], "openai")

    @override_settings(**ALL_KEYS)
    def test_feature_names_resolve_to_tasks(self):
        # A feature name routes like its category.
        self.assertEqual(route("rfq_extraction")[0], "gemini")
        self.assertEqual(route("risk_detection")[0], "claude")
        self.assertEqual(route("email_drafting")[0], "openai")

    @override_settings(GEMINI_API_KEY="", ANTHROPIC_API_KEY="a", OPENAI_API_KEY="")
    def test_chain_is_filtered_to_configured_providers(self):
        # Only Claude has a key: extraction still routes, just to what's available.
        self.assertEqual(route(TaskType.EXTRACTION), ["claude"])

    @override_settings(**ALL_KEYS, AI_DISABLED_PROVIDERS=["gemini"])
    def test_admin_can_disable_a_provider(self):
        # Gemini disabled → extraction falls to its next preference, Claude.
        chain = route(TaskType.EXTRACTION)
        self.assertNotIn("gemini", chain)
        self.assertEqual(chain[0], "claude")

    @override_settings(GEMINI_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="")
    def test_no_providers_means_empty_chain(self):
        self.assertEqual(route(TaskType.EXTRACTION), [])

    @override_settings(**ALL_KEYS, AI_TASK_ROUTES={"extraction": ["openai", "claude"]})
    def test_deployment_override_of_routes(self):
        self.assertEqual(route(TaskType.EXTRACTION)[0], "openai")


class _FakeProvider:
    """A provider stand-in that either answers or raises, to drive failover."""

    def __init__(self, name, *, fail=False):
        self.name = name
        self.fail = fail
        self.calls = 0

    def complete(self, prompt, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} is down")
        return AIResponse(text=f"ok from {self.name}", provider=self.name,
                          tokens_in=5, tokens_out=5, credits_used=Decimal("1"))


class FailoverTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="AI Co")
        allocate_credits(self.company, Decimal("100"))

    @override_settings(**ALL_KEYS)
    def test_primary_answers_no_failover(self):
        fake = _FakeProvider("gemini")
        with patch("apps.ai_platform.providers.get_provider", return_value=fake):
            resp = run_task(self.company, None, TaskType.EXTRACTION, "hi")
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(AIUsageLog.objects.filter(status="ok").count(), 1)
        self.assertEqual(AIUsageLog.objects.filter(status="failover").count(), 0)

    @override_settings(**ALL_KEYS)
    def test_fails_over_to_next_provider_and_logs_it(self):
        providers = {"gemini": _FakeProvider("gemini", fail=True),
                     "claude": _FakeProvider("claude")}
        with patch("apps.ai_platform.providers.get_provider",
                   side_effect=lambda n: providers[n]):
            # retries=0 so each provider is tried once → clean failover to Claude.
            resp = run_task(self.company, None, TaskType.EXTRACTION, "hi", retries=0)
        self.assertEqual(resp.provider, "claude")           # failed over
        logs = AIUsageLog.objects.all()
        self.assertEqual(logs.filter(status="failover", provider="gemini").count(), 1)
        self.assertEqual(logs.filter(status="ok", provider="claude").count(), 1)
        # One logical request → one shared request_id across both attempts.
        self.assertEqual(len({log.request_id for log in logs}), 1)

    @override_settings(**ALL_KEYS)
    def test_retries_once_before_failing_over(self):
        gemini = _FakeProvider("gemini", fail=True)
        claude = _FakeProvider("claude")
        with patch("apps.ai_platform.providers.get_provider",
                   side_effect=lambda n: {"gemini": gemini, "claude": claude}[n]):
            run_task(self.company, None, TaskType.EXTRACTION, "hi", retries=1)
        self.assertEqual(gemini.calls, 2)      # tried twice (1 + 1 retry)
        self.assertEqual(claude.calls, 1)

    @override_settings(**ALL_KEYS)
    def test_all_providers_fail_raises(self):
        allfail = _FakeProvider("x", fail=True)
        with patch("apps.ai_platform.providers.get_provider", return_value=allfail):
            with self.assertRaises(AllProvidersFailedError):
                run_task(self.company, None, TaskType.EXTRACTION, "hi", retries=0)

    @override_settings(GEMINI_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="")
    def test_no_provider_available_raises(self):
        with self.assertRaises(AllProvidersFailedError):
            run_task(self.company, None, TaskType.EXTRACTION, "hi")
