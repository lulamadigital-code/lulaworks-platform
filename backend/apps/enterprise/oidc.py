"""Minimal, dependency-light OpenID Connect (Authorization Code flow) client.

Built on `requests` + `pyjwt` — no extra packages. One SSOConfig per tenant acts
as a distinct OIDC client, so provider details are resolved per request rather
than from global settings. Every token is verified: signature via the issuer's
JWKS, plus issuer / audience / nonce / expiry.

Scope of trust: this module only *authenticates* an assertion (proves the IdP
vouches for an email). It does NOT decide who may log in — the view maps the
verified email onto an existing active membership of the pinned tenant.
"""
import time

import jwt
import requests
from jwt import PyJWKClient

DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
_HTTP_TIMEOUT = 8
_disco_cache: dict = {}   # issuer → (fetched_at, document)
_DISCO_TTL = 3600


class OIDCError(Exception):
    pass


def discover(issuer: str) -> dict:
    """Fetch (and briefly cache) the issuer's OIDC discovery document."""
    issuer = (issuer or "").rstrip("/")
    if not issuer:
        raise OIDCError("No issuer configured.")
    hit = _disco_cache.get(issuer)
    if hit and (time.time() - hit[0]) < _DISCO_TTL:
        return hit[1]
    url = issuer + DISCOVERY_SUFFIX
    try:
        resp = requests.get(url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        doc = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise OIDCError(f"Could not load provider metadata: {exc}") from exc
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if not doc.get(key):
            raise OIDCError(f"Provider metadata missing '{key}'.")
    _disco_cache[issuer] = (time.time(), doc)
    return doc


def authorization_url(config, redirect_uri: str, state: str, nonce: str) -> str:
    """Build the URL to send the browser to, to begin sign-in."""
    from urllib.parse import urlencode
    doc = discover(config.oidc_issuer)
    params = {
        "response_type": "code",
        "client_id": config.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    }
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{urlencode(params)}"


def exchange_code(config, code: str, redirect_uri: str) -> dict:
    """Swap the authorization code for tokens at the token endpoint (server-side,
    authenticated with the client secret)."""
    doc = discover(config.oidc_issuer)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.oidc_client_id,
        "client_secret": config.get_client_secret(),
    }
    try:
        resp = requests.post(doc["token_endpoint"], data=data, timeout=_HTTP_TIMEOUT,
                             headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise OIDCError(f"Token exchange failed: {exc}") from exc
    if resp.status_code != 200:
        raise OIDCError("The identity provider rejected the sign-in (token exchange).")
    tokens = resp.json()
    if not tokens.get("id_token"):
        raise OIDCError("No id_token returned by the provider.")
    return tokens


def verify_id_token(config, id_token: str, nonce: str) -> dict:
    """Verify the id_token signature (via JWKS) and its issuer / audience / nonce /
    expiry. Returns the validated claims."""
    doc = discover(config.oidc_issuer)
    try:
        signing_key = PyJWKClient(doc["jwks_uri"]).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, signing_key.key, algorithms=["RS256", "RS384", "RS512"],
            audience=config.oidc_client_id, issuer=doc["issuer"],
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError(f"Could not verify the sign-in token: {exc}") from exc
    if nonce and claims.get("nonce") != nonce:
        raise OIDCError("Sign-in token nonce mismatch — please try again.")
    return claims
