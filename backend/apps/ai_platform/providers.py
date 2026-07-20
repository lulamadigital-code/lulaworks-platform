"""Concrete AI provider adapters behind the gateway (AI_PLATFORM §2).

Provider-agnostic: every adapter implements AIProvider.complete() → AIResponse.
Vendor SDKs are lazy-imported *inside* complete(), so the platform has no hard
dependency on them and runs fully on the deterministic path when no key is set.
Enable a live provider by setting its API key (Secrets Manager in prod) and
installing its SDK (e.g. `pip install anthropic`).
"""

from decimal import Decimal

from django.conf import settings

from .gateway import AIProvider, AIResponse


class NotConfiguredError(RuntimeError):
    """Raised when a provider is selected but its API key/SDK is unavailable."""


def _key_for(provider: str) -> str:
    return {
        "claude": settings.ANTHROPIC_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
    }.get(provider, "")


def ai_configured(provider: str | None = None) -> bool:
    """True if the given provider (default: the active one) has a key."""
    return bool(_key_for(provider or settings.AI_PROVIDER))


def configured_provider_names() -> list[str]:
    """The active provider first, then the others that also have a key — the
    fallback order (AI_PLATFORM §2: provider down → next provider)."""
    active = settings.AI_PROVIDER
    ordered = [active] + [p for p in ("claude", "openai", "gemini") if p != active]
    return [p for p in ordered if _key_for(p)]


class ClaudeProvider(AIProvider):
    name = "claude"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2000,
                 json_mode: bool = False, **_) -> AIResponse:
        if not settings.ANTHROPIC_API_KEY:
            raise NotConfiguredError("ANTHROPIC_API_KEY is not set.")
        try:
            import anthropic  # lazy — only needed when live
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise NotConfiguredError(
                "anthropic SDK not installed (`pip install anthropic`)."
            ) from exc
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system or "You are a precise document-extraction assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(block, "text", "") for block in msg.content)
        tin = getattr(msg.usage, "input_tokens", 0)
        tout = getattr(msg.usage, "output_tokens", 0)
        return AIResponse(
            text=text, provider=self.name, tokens_in=tin, tokens_out=tout,
            credits_used=Decimal(settings.AI_CREDITS_PER_EXTRACTION),
        )


class OpenAIProvider(AIProvider):
    name = "openai"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2000,
                 json_mode: bool = False, **_) -> AIResponse:
        if not settings.OPENAI_API_KEY:
            raise NotConfiguredError("OPENAI_API_KEY is not set.")
        try:
            from openai import OpenAI  # lazy
        except ImportError as exc:  # pragma: no cover
            raise NotConfiguredError("openai SDK not installed.") from exc
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system or "Extract document data precisely."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
        )
        usage = resp.usage
        return AIResponse(
            text=resp.choices[0].message.content or "", provider=self.name,
            tokens_in=getattr(usage, "prompt_tokens", 0),
            tokens_out=getattr(usage, "completion_tokens", 0),
            credits_used=Decimal(settings.AI_CREDITS_PER_EXTRACTION),
        )


class GeminiProvider(AIProvider):
    """Google Gemini via the `google-genai` SDK.

    The model is configurable (GEMINI_MODEL) because Google ships new ones
    often; the default is a fast, inexpensive one, which suits this workload —
    short prompts returning small structured JSON, called per document.
    """

    name = "gemini"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2000,
                 json_mode: bool = False, **_) -> AIResponse:
        if not settings.GEMINI_API_KEY:
            raise NotConfiguredError("GEMINI_API_KEY is not set.")
        try:
            from google import genai              # lazy: no hard dependency
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - optional extra
            raise NotConfiguredError(
                "google-genai SDK not installed (pip install google-genai)."
            ) from exc

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        config = types.GenerateContentConfig(max_output_tokens=max_tokens)
        if system:
            config.system_instruction = system

        # Gemini 2.5 models "think" by default, and those tokens are drawn from
        # the SAME output budget as the answer. On a long extraction prompt the
        # thinking can consume the whole budget and return empty text. These are
        # structured extraction calls, not reasoning problems, so thinking is off
        # by default — set GEMINI_THINKING_BUDGET to re-enable it.
        budget = int(settings.GEMINI_THINKING_BUDGET)
        try:
            config.thinking_config = types.ThinkingConfig(thinking_budget=budget)
        except (AttributeError, TypeError):   # older SDK / model without thinking
            pass

        # Ask for JSON natively rather than parsing it out of a ```json fence.
        if json_mode:
            config.response_mime_type = "application/json"

        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL, contents=prompt, config=config,
        )

        usage = getattr(resp, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0
        return AIResponse(
            text=resp.text or "", provider=self.name,
            tokens_in=tokens_in, tokens_out=tokens_out,
            credits_used=Decimal(settings.AI_CREDITS_PER_EXTRACTION),
        )


_PROVIDERS = {"claude": ClaudeProvider, "openai": OpenAIProvider, "gemini": GeminiProvider}


def get_provider(name: str | None = None) -> AIProvider:
    """Factory for the active (or named) provider. Raises NotConfigured if the
    provider has no key."""
    name = name or settings.AI_PROVIDER
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise NotConfiguredError(f"Unknown AI provider '{name}'.")
    # Check the key for the provider being ASKED for — not the active one.
    # Getting this wrong made every fallback attempt fail whenever the primary
    # provider was unconfigured, which is exactly when fallback matters.
    if not ai_configured(name):
        raise NotConfiguredError(f"Provider '{name}' has no API key configured.")
    return cls()
