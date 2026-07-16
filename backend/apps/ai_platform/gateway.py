"""AI Gateway — the ONLY path to an LLM (AI_PLATFORM §2). Provider-agnostic
interface + metered credit accounting. Deterministic work never touches this.

Phase-1 foundation: the interface, the metered wrapper, and the credit ledger.
Concrete Claude/OpenAI/Gemini adapters land in Phase 8.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from .models import AICreditLedger, AIUsageLog


class InsufficientCreditsError(RuntimeError):
    pass


@dataclass
class AIResponse:
    text: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Decimal = Decimal("0")
    credits_used: Decimal = Decimal("0")


class AIProvider:
    """Common interface every provider adapter implements."""

    name = "base"

    def complete(self, prompt: str, **kwargs) -> AIResponse:  # pragma: no cover - interface
        raise NotImplementedError


def credit_balance(company) -> Decimal:
    last = AICreditLedger.objects.filter(company=company).order_by("-created_at").first()
    return last.balance_after if last else Decimal("0")


@transaction.atomic
def _post_ledger(company, entry_type, credits: Decimal, source: str) -> Decimal:
    current = credit_balance(company)
    new_balance = current + credits
    AICreditLedger.objects.create(
        company=company, entry_type=entry_type, credits=credits,
        balance_after=new_balance, source=source,
    )
    return new_balance


def allocate_credits(company, amount: Decimal, source="monthly") -> Decimal:
    return _post_ledger(
        company, AICreditLedger.EntryType.ALLOCATION, Decimal(amount), source
    )


def topup_credits(company, amount: Decimal, source="purchase") -> Decimal:
    return _post_ledger(company, AICreditLedger.EntryType.TOPUP, Decimal(amount), source)


def run_metered(
    company, user, provider: AIProvider, prompt: str, *, agent="", **kwargs
) -> AIResponse:
    """Execute an AI call through the gateway: check balance → call → log usage
    → debit ledger. Fails closed if the tenant is out of credits."""
    if credit_balance(company) <= 0:
        raise InsufficientCreditsError("No AI credits remaining — top up to continue.")
    resp = provider.complete(prompt, **kwargs)
    AIUsageLog.objects.create(
        company=company, user=user, provider=resp.provider, agent=agent,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost=resp.cost,
        credits_used=resp.credits_used, status="ok",
    )
    if resp.credits_used:
        _post_ledger(
            company, AICreditLedger.EntryType.CONSUMPTION, -resp.credits_used,
            agent or resp.provider,
        )
    return resp
