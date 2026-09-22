"""Signature-provider abstraction for Enterprise agreement acceptance.

The workflow never hardcodes one vendor: acceptance can be captured by online
click (the built-in V1), by uploading a counter-signed document, or — once real
credentials exist — by an e-signature provider such as DocuSign. Providers share
one interface so a real integration slots in without touching the lifecycle.

Honest gating: a provider that needs credentials reports `is_configured() == False`
until they are present (mirrors the payments gateway + SSO config surface). We do
NOT fake an envelope handshake.
"""
from django.conf import settings


class NotConfigured(Exception):
    pass


class SignatureProvider:
    name = "base"
    label = "Base"

    def is_configured(self) -> bool:
        return False

    def send_for_signature(self, agreement, *, signer_email, signer_name=""):
        """Kick off a signature request. Real providers create an envelope and
        return a reference; unconfigured ones raise NotConfigured."""
        raise NotConfigured(f"{self.label} is not configured.")


class ClickProvider(SignatureProvider):
    """The built-in online accept — the customer clicks Accept on the secure page.
    Always available; no external dependency."""
    name = "click"
    label = "Click-to-accept (online)"

    def is_configured(self) -> bool:
        return True


class UploadProvider(SignatureProvider):
    """Offline signature: an admin uploads the counter-signed agreement and marks
    it accepted. Always available; the uploaded document is the evidence."""
    name = "upload"
    label = "Signed document upload"

    def is_configured(self) -> bool:
        return True


class DocuSignProvider(SignatureProvider):
    """DocuSign e-signature. Reports not-configured until real credentials are set
    in the environment; the envelope flow is intentionally not faked."""
    name = "docusign"
    label = "DocuSign"

    def is_configured(self) -> bool:
        return bool(getattr(settings, "DOCUSIGN_INTEGRATION_KEY", "")
                    and getattr(settings, "DOCUSIGN_ACCOUNT_ID", ""))

    def send_for_signature(self, agreement, *, signer_email, signer_name=""):
        if not self.is_configured():
            raise NotConfigured(
                "DocuSign isn't configured. Add DOCUSIGN_INTEGRATION_KEY / "
                "DOCUSIGN_ACCOUNT_ID (and secret) to enable it.")
        # A real implementation would build and send a DocuSign envelope here and
        # return its id; completion arrives via a webhook that calls
        # billing.accept_agreement(..., method='docusign', ref=<envelope id>).
        raise NotConfigured("DocuSign envelope sending is not yet implemented.")


_PROVIDERS = {p.name: p for p in (ClickProvider(), UploadProvider(), DocuSignProvider())}


def get_provider(name) -> SignatureProvider | None:
    return _PROVIDERS.get(name)


def providers() -> list[SignatureProvider]:
    return list(_PROVIDERS.values())


def configured_providers() -> list[SignatureProvider]:
    return [p for p in _PROVIDERS.values() if p.is_configured()]
