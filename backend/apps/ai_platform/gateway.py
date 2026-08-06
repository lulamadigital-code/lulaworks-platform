"""AI Gateway — the ONLY path to an LLM (AI_PLATFORM §2). Provider-agnostic
interface + metered credit accounting. Deterministic work never touches this.

Phase-1 foundation: the interface, the metered wrapper, and the credit ledger.
Concrete Claude/OpenAI/Gemini adapters land in Phase 8.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from .models import AICreditLedger, AIUsageLog

logger = logging.getLogger(__name__)


class InsufficientCreditsError(RuntimeError):
    pass


class AllProvidersFailedError(RuntimeError):
    """Every provider in a task's routing chain failed (or none was available).
    Callers fall back to their deterministic result."""


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
        """Adapters accept `system`, `max_tokens`, `json_mode`, and tolerate
        unknown kwargs so a provider-specific option never breaks a call that
        falls back to a different provider."""
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
    company, user, provider: AIProvider, prompt: str, *, agent="", task="",
    prompt_name="", request_id=None, **kwargs
) -> AIResponse:
    """Execute one AI call through the gateway: check balance → call → log usage
    (with timing) → debit ledger. Fails closed if the tenant is out of credits.

    This is the single-provider primitive. `run_task` wraps it with the routing
    chain, retry and failover. Direct callers still get metering + logging."""
    if credit_balance(company) <= 0:
        raise InsufficientCreditsError("No AI credits remaining — top up to continue.")
    started = time.monotonic()
    resp = provider.complete(prompt, **kwargs)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    AIUsageLog.objects.create(
        company=company, user=user, provider=resp.provider, agent=agent,
        task=task, prompt_name=prompt_name, request_id=request_id,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost=resp.cost,
        credits_used=resp.credits_used, execution_ms=elapsed_ms, status="ok",
    )
    if resp.credits_used:
        _post_ledger(
            company, AICreditLedger.EntryType.CONSUMPTION, -resp.credits_used,
            agent or resp.provider,
        )
    return resp


def run_task(company, user, task: str, prompt: str, *, agent="", prompt_name="",
             retries=1, **kwargs) -> AIResponse:
    """The orchestration entry point: route `task` to the best provider, and on
    failure retry once then fail over to the next provider in the chain.

    Every module calls THIS (or run_metered) — never a provider SDK directly.
    Out of credits fails closed immediately (no failover — credits are global,
    not a provider problem). Each attempt is logged with a shared request_id, so
    a failover leaves an auditable trail; if the whole chain fails,
    AllProvidersFailedError is raised and the caller keeps its deterministic
    result.
    """
    from .routing import route, task_for_feature

    if credit_balance(company) <= 0:
        raise InsufficientCreditsError("No AI credits remaining — top up to continue.")

    from .providers import get_provider  # local: avoids import cycle at load

    request_id = uuid.uuid4()
    category = task_for_feature(task)
    chain = route(task)
    if not chain:
        raise AllProvidersFailedError(f"No AI provider available for '{task}'.")

    last_exc = None
    for name in chain:
        for attempt in range(retries + 1):
            started = time.monotonic()
            try:
                provider = get_provider(name)
                return run_metered(
                    company, user, provider, prompt, agent=agent,
                    task=category, prompt_name=prompt_name or task,
                    request_id=request_id, **kwargs)
            except InsufficientCreditsError:
                raise                       # global — never fail over on this
            except Exception as exc:        # noqa: BLE001 - resilient failover
                last_exc = exc
                elapsed_ms = int((time.monotonic() - started) * 1000)
                final_attempt = attempt == retries
                # Record the failed attempt so failover is auditable.
                AIUsageLog.objects.create(
                    company=company, user=user, provider=name, agent=agent,
                    task=category, prompt_name=prompt_name or task,
                    request_id=request_id, execution_ms=elapsed_ms,
                    status="failover", error=str(exc)[:500],
                )
                logger.warning(
                    "AI task '%s' via %s failed (attempt %d/%d): %s%s",
                    category, name, attempt + 1, retries + 1, exc,
                    "" if not final_attempt else " — failing over.")
                if final_attempt:
                    break                   # move to the next provider
    raise AllProvidersFailedError(
        f"All providers failed for task '{category}': {last_exc}")
