"""Symmetric encryption for secrets at rest (the OIDC client secret).

The Fernet key is derived from Django's ``SECRET_KEY`` so there is no extra secret
to provision or rotate independently — if ``SECRET_KEY`` is compromised the whole
app is anyway, and if it is rotated a tenant simply re-enters its client secret
(the same trade-off the framework makes for signed sessions). We never log or
display the plaintext.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    # 32-byte key derived from SECRET_KEY, urlsafe-base64 encoded as Fernet wants.
    digest = hashlib.sha256(f"sso-secret:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
