"""OIDC SSO — secret crypto, tenant/member resolution, token verification, and
the sign-in view flow (existing-members-only)."""
import time
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase
from django.utils import timezone

from apps.core.context import system_scope
from apps.enterprise import crypto, oidc
from apps.enterprise import services as gov
from apps.enterprise.models import SSOConfig, SSOProtocol, SSOStatus

ISSUER = "https://idp.example.com"
CLIENT_ID = "lula-client"


def _rsa():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = key.public_key()
    return priv, pub


def _id_token(priv, *, aud=CLIENT_ID, iss=ISSUER, nonce="n", email="u@acme.co",
              email_verified=True, exp_delta=300):
    now = int(time.time())
    payload = {"iss": iss, "aud": aud, "iat": now, "exp": now + exp_delta,
               "nonce": nonce, "email": email, "email_verified": email_verified}
    return jwt.encode(payload, priv, algorithm="RS256")


class CryptoTests(TestCase):
    def test_roundtrip(self):
        enc = crypto.encrypt("s3cr3t")
        self.assertNotEqual(enc, "s3cr3t")
        self.assertEqual(crypto.decrypt(enc), "s3cr3t")
        self.assertEqual(crypto.encrypt(""), "")
        self.assertEqual(crypto.decrypt("garbage"), "")   # never raises


class ResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.billing.models import Plan, Subscription, SubscriptionStatus
        from apps.identity.models import Company, Membership, Role, User
        with system_scope():
            cls.co = Company.objects.create(name="Acme", currency="ZAR")
            cls.cfg = SSOConfig.objects.create(
                company=cls.co, protocol=SSOProtocol.OIDC, status=SSOStatus.ACTIVE,
                oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID,
                allowed_domains="acme.co, acme.com")
            cls.cfg.set_client_secret("shhh"); cls.cfg.save()
            cls.role = Role.objects.create(name="Member", company=cls.co)
            cls.member = User.objects.create_user("u@acme.co", "x")
            Membership.objects.create(company=cls.co, user=cls.member, role=cls.role,
                                      status="active")

    def test_active_sso_for_email(self):
        self.assertEqual(gov.active_sso_for_email("u@acme.co").pk, self.cfg.pk)
        self.assertEqual(gov.active_sso_for_email("x@ACME.COM").pk, self.cfg.pk)
        self.assertIsNone(gov.active_sso_for_email("x@other.io"))
        self.assertIsNone(gov.active_sso_for_email("bad-email"))

    def test_active_requires_active_status_and_secret(self):
        with system_scope():
            self.cfg.status = SSOStatus.CONFIGURED; self.cfg.save()
        self.assertIsNone(gov.active_sso_for_email("u@acme.co"))
        with system_scope():
            self.cfg.status = SSOStatus.ACTIVE
            self.cfg.oidc_client_secret_enc = ""; self.cfg.save()   # no secret
        self.assertIsNone(gov.active_sso_for_email("u@acme.co"))

    def test_member_lookup(self):
        self.assertIsNotNone(gov.sso_member_for(self.co, "U@Acme.co"))
        self.assertIsNone(gov.sso_member_for(self.co, "ghost@acme.co"))


class TokenVerifyTests(TestCase):
    def test_verify_and_reject(self):
        priv, pub = _rsa()
        cfg = SSOConfig(oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID)
        fake_key = mock.Mock(key=pub)
        with mock.patch.object(oidc, "discover",
                               return_value={"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks",
                                             "authorization_endpoint": ISSUER + "/auth",
                                             "token_endpoint": ISSUER + "/token"}), \
             mock.patch.object(oidc, "PyJWKClient") as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value = fake_key
            claims = oidc.verify_id_token(cfg, _id_token(priv, nonce="n"), "n")
            self.assertEqual(claims["email"], "u@acme.co")
            # wrong nonce
            with self.assertRaises(oidc.OIDCError):
                oidc.verify_id_token(cfg, _id_token(priv, nonce="n"), "different")
            # wrong audience
            with self.assertRaises(oidc.OIDCError):
                oidc.verify_id_token(cfg, _id_token(priv, aud="someone-else"), "n")


class SSOFlowViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.identity.models import Company, Membership, Role, User
        with system_scope():
            cls.co = Company.objects.create(name="Acme", currency="ZAR")
            cls.cfg = SSOConfig.objects.create(
                company=cls.co, protocol=SSOProtocol.OIDC, status=SSOStatus.ACTIVE,
                oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID, allowed_domains="acme.co")
            cls.cfg.set_client_secret("shhh"); cls.cfg.save()
            role = Role.objects.create(name="Member", company=cls.co)
            cls.member = User.objects.create_user("u@acme.co", "x")
            Membership.objects.create(company=cls.co, user=cls.member, role=role,
                                      status="active")

    def test_login_redirects_to_idp_and_sets_state(self):
        from django.test import Client
        c = Client()
        with mock.patch.object(oidc, "authorization_url",
                               return_value=ISSUER + "/auth?x=1") as au:
            r = c.post("/sso/login/", {"email": "u@acme.co"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith(ISSUER))
        self.assertIn("sso_state", c.session)
        self.assertEqual(c.session["sso_company"], str(self.co.id))
        au.assert_called_once()

    def test_login_unknown_domain(self):
        from django.test import Client
        r = Client().post("/sso/login/", {"email": "x@nope.io"})
        self.assertContains(r, "isn&#x27;t set up", status_code=200)

    def _prime(self, c):
        s = c.session
        s["sso_state"] = "st"; s["sso_nonce"] = "no"; s["sso_company"] = str(self.co.id)
        s.save()

    def test_callback_signs_in_member(self):
        from django.test import Client
        c = Client(); self._prime(c)
        with mock.patch.object(oidc, "exchange_code", return_value={"id_token": "tok"}), \
             mock.patch.object(oidc, "verify_id_token",
                               return_value={"email": "u@acme.co", "email_verified": True}):
            r = c.get("/sso/oidc/callback/", {"state": "st", "code": "abc"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("_auth_user_id", c.session)     # logged in
        self.assertEqual(c.session["_auth_user_id"], str(self.member.pk))

    def test_callback_denies_non_member(self):
        from django.test import Client
        from apps.enterprise.models import AuditEvent
        c = Client(); self._prime(c)
        with mock.patch.object(oidc, "exchange_code", return_value={"id_token": "tok"}), \
             mock.patch.object(oidc, "verify_id_token",
                               return_value={"email": "stranger@acme.co", "email_verified": True}):
            r = c.get("/sso/oidc/callback/", {"state": "st", "code": "abc"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("_auth_user_id", c.session)   # NOT logged in
        with system_scope():
            self.assertTrue(AuditEvent.all_objects.filter(
                company=self.co, action="login_failed").exists())

    def test_callback_rejects_bad_state(self):
        from django.test import Client
        c = Client(); self._prime(c)
        r = c.get("/sso/oidc/callback/", {"state": "WRONG", "code": "abc"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("_auth_user_id", c.session)
