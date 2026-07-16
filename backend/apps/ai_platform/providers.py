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


def ai_configured() -> bool:
    """True if the active provider has an API key configured."""
    provider = settings.AI_PROVIDER
    return bool(
        (provider == "claude" and settings.ANTHROPIC_API_KEY)
        or (provider == "openai" and settings.OPENAI_API_KEY)
        or (provider == "gemini" and settings.GEMINI_API_KEY)
    )


class ClaudeProvider(AIProvider):
    name = "claude"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> AIResponse:
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

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> AIResponse:
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
    name = "gemini"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> AIResponse:
        if not settings.GEMINI_API_KEY:
            raise NotConfiguredError("GEMINI_API_KEY is not set.")
        try:
            import google.generativeai as genai  # lazy
        except ImportError as exc:  # pragma: no cover
            raise NotConfiguredError("google-generativeai SDK not installed.") from exc
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-pro")
        resp = model.generate_content((system + "\n\n" + prompt).strip())
        return AIResponse(
            text=resp.text, provider=self.name,
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
    if not ai_configured():
        raise NotConfiguredError(f"Provider '{name}' has no API key configured.")
    return cls()
