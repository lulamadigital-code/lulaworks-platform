"""Enterprise governance — audit trail + API-key auth & entitlement gating."""
from django.test import TestCase
from django.utils import timezone

from apps.core.context import system_scope
from apps.enterprise import services as gov
from apps.enterprise.authentication import ApiKeyAuthentication
from apps.enterprise.models import ApiKey, AuditAction, AuditEvent


def _rf(token=None):
    from django.test import RequestFactory
    headers = {}
    if token:
        headers["HTTP_AUTHORIZATION"] = f"Api-Key {token}"
    return RequestFactory().get("/api/v1/customers/", **headers)


class GovernanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.billing.models import Plan
        from apps.identity.models import Company, User
        with system_scope():
            cls.ent_plan = Plan.objects.create(
                code="ent_test", name="Ent", tier=4, price=0, annual_price=0,
                max_users=500,
                module_entitlements=["api_access", "audit_log", "sso"])
            cls.free_plan = Plan.objects.create(
                code="free_test", name="Free", tier=1, price=100, annual_price=1000,
                max_users=2, module_entitlements=["dashboards"])
            cls.company = Company.objects.create(name="Acme", currency="ZAR")
            cls.owner = User.objects.create_user(
                email="owner@acme.co", password="x", first_name="Ann", last_name="Owner")
            cls.owner.active_company = cls.company
            cls.owner.save()

    def _subscribe(self, plan):
        from apps.billing.models import Subscription, SubscriptionStatus
        with system_scope():
            sub, _ = Subscription.all_objects.get_or_create(
                company=self.company,
                defaults={"plan": plan, "currency": "ZAR",
                          "status": SubscriptionStatus.ACTIVE})
            sub.plan = plan
            sub.status = SubscriptionStatus.ACTIVE
            sub.save()

    # ── audit ────────────────────────────────────────────────────────────────
    def test_record_scopes_and_snapshots(self):
        with system_scope():
            ev = gov.record(AuditAction.LOGIN, company=self.company, actor=self.owner,
                            summary="Signed in")
            self.assertIsNotNone(ev)
            self.assertEqual(ev.company_id, self.company.id)
            self.assertIn("owner@acme.co", ev.actor_label)
            self.assertTrue(AuditEvent.all_objects.filter(pk=ev.pk).exists())

    def test_record_never_raises_without_company(self):
        self.assertIsNone(gov.record(AuditAction.LOGIN, summary="no tenant"))

    # ── api keys ─────────────────────────────────────────────────────────────
    def test_issue_and_resolve_key(self):
        with system_scope():
            key, token = gov.issue_api_key(self.company, "CI", actor=self.owner)
            self.assertTrue(token.startswith("lwk_"))
            self.assertEqual(gov.resolve_api_key(token).pk, key.pk)
            self.assertIsNone(gov.resolve_api_key("lwk_bogus"))
            gov.revoke_api_key(key)
            self.assertIsNone(gov.resolve_api_key(token))  # revoked → gone

    def test_auth_gated_on_entitlement(self):
        from rest_framework.exceptions import AuthenticationFailed
        with system_scope():
            _, token = gov.issue_api_key(self.company, "CI", actor=self.owner)
        auth = ApiKeyAuthentication()

        # No header → passes through (None), lets JWT try.
        self.assertIsNone(auth.authenticate(_rf()))

        # Entitled plan → authenticates, binds tenant to the key's company.
        self._subscribe(self.ent_plan)
        user, key = auth.authenticate(_rf(token))
        self.assertEqual(user.pk, self.owner.pk)
        self.assertEqual(key.company_id, self.company.id)

        # Downgraded plan (no api_access) → blocked, keys untouched.
        self._subscribe(self.free_plan)
        with self.assertRaises(AuthenticationFailed):
            auth.authenticate(_rf(token))

    # ── SSO config surface ───────────────────────────────────────────────────
    def test_sso_config_gated_and_persists(self):
        from apps.enterprise.models import SSOConfig, SSOStatus
        from django.test import Client
        self.owner.set_password("pw"); self.owner.save()
        # Grant company.manage so the surface is reachable.
        self.owner.is_superuser = True
        self.owner.save()
        c = Client(); c.force_login(self.owner)

        # Not entitled → upgrade prompt, no config written.
        self._subscribe(self.free_plan)
        r = c.get("/company/sso/")
        self.assertContains(r, "Enterprise feature")
        c.post("/company/sso/", {"action": "save", "protocol": "saml",
                                 "saml_idp_entity_id": "x"})
        with system_scope():
            self.assertFalse(SSOConfig.all_objects.filter(company=self.company).exists())

        # Entitled → save then request activation.
        self._subscribe(self.ent_plan)
        c.post("/company/sso/", {
            "action": "request", "protocol": "saml",
            "saml_idp_entity_id": "https://idp/meta",
            "saml_idp_sso_url": "https://idp/sso",
            "saml_idp_x509_cert": "CERT", "allowed_domains": "acme.com, x.co"})
        with system_scope():
            cfg = SSOConfig.all_objects.get(company=self.company)
        self.assertEqual(cfg.status, SSOStatus.ACTIVATION_REQUESTED)
        self.assertIsNotNone(cfg.requested_at)
        self.assertEqual(cfg.domain_list(), ["acme.com", "x.co"])

    def test_sso_request_needs_details(self):
        from apps.enterprise.models import SSOConfig, SSOStatus
        from django.test import Client
        self.owner.is_superuser = True; self.owner.save()
        self._subscribe(self.ent_plan)
        c = Client(); c.force_login(self.owner)
        # Request with no IdP details → stays un-requested.
        c.post("/company/sso/", {"action": "request", "protocol": "saml"})
        with system_scope():
            cfg = SSOConfig.all_objects.filter(company=self.company).first()
        self.assertTrue(cfg is None or cfg.status != SSOStatus.ACTIVATION_REQUESTED)

    def test_touch_throttled(self):
        with system_scope():
            key, token = gov.issue_api_key(self.company, "CI", actor=self.owner)
            gov.touch_api_key(key)
            first = ApiKey.all_objects.get(pk=key.pk).last_used_at
            self.assertIsNotNone(first)
            # Immediate second touch is throttled (no change within a minute).
            key.refresh_from_db()
            gov.touch_api_key(key)
            self.assertEqual(ApiKey.all_objects.get(pk=key.pk).last_used_at, first)
