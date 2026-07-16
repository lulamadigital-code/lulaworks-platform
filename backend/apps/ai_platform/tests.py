from decimal import Decimal

from django.test import TestCase

from apps.identity.models import Company

from .gateway import (
    AIProvider,
    AIResponse,
    InsufficientCreditsError,
    allocate_credits,
    credit_balance,
    run_metered,
)
from .models import AICreditLedger, AIUsageLog


class StubProvider(AIProvider):
    name = "stub"

    def complete(self, prompt, **kwargs):
        return AIResponse(text="ok", provider="stub", tokens_in=10, tokens_out=5,
                          cost=Decimal("0.01"), credits_used=Decimal("2"))


class CreditLedgerTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")

    def test_allocation_sets_balance(self):
        allocate_credits(self.company, Decimal("100"))
        self.assertEqual(credit_balance(self.company), Decimal("100"))

    def test_metered_call_debits_and_logs(self):
        allocate_credits(self.company, Decimal("100"))
        resp = run_metered(self.company, None, StubProvider(), "hi", agent="rfq")
        self.assertEqual(resp.text, "ok")
        self.assertEqual(credit_balance(self.company), Decimal("98"))  # 100 - 2
        self.assertEqual(AIUsageLog.objects.count(), 1)
        self.assertEqual(AICreditLedger.objects.count(), 2)  # allocation + consumption

    def test_fails_closed_without_credits(self):
        with self.assertRaises(InsufficientCreditsError):
            run_metered(self.company, None, StubProvider(), "hi")
        self.assertEqual(AIUsageLog.objects.count(), 0)
