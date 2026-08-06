"""AI Platform (Phase 8) tests: the Lulama orchestrator produces one grounded
consolidated draft; the AGENT SECURITY MODEL (agents/tools run within the
invoking user's RBAC + tenant); GOVERNANCE (the AI proposes but never executes
side-effects); confidence + sources; prompt versioning; the deterministic path
consumes no credits; rejected-suggestion logging; and tenant isolation."""

from datetime import date, timedelta
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import AuditLog, NumberingRule
from apps.compliance.models import ComplianceRequirement
from apps.compliance.services import approve_item
from apps.core.context import tenant_scope
from apps.estimating.services import approve_estimate, create_estimate
from apps.finance.models import InvoiceStatus
from apps.finance.services import create_invoice
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.projects.services import award_quotation
from apps.quotes.models import Quotation

from django.test import override_settings

from .gateway import allocate_credits, credit_balance

#: Pins "no provider configured" so these tests never depend on a developer's
#: .env — and never reach a live API.
NO_AI = override_settings(AI_PROVIDER="claude", ANTHROPIC_API_KEY="",
                          OPENAI_API_KEY="", GEMINI_API_KEY="")
from .models import ApprovalStatus
from .orchestrator import orchestrate, record_decision
from .tools import ToolPermissionError, available_tools, run_tool


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("estimate", "EST"),
                    ("invoice", "INV")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


PERMS = ["ai.generate", "projects.view", "projects.create", "procurement.manage",
         "finance.view_money", "estimating.manage", "compliance.override"]


def user_with(company, codenames, email="u@lulama.co.za"):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for code in codenames:
        p, _ = Permission.objects.get_or_create(codename=code,
                                                 defaults={"module": "x", "label": code})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


def compliant_project(company, work_type="pump_overhaul"):
    q = Quotation.objects.create(company=company, number="QT-1", client_name="Sasol",
                                 site="Secunda")
    est = create_estimate(company, None, client_name="Sasol", work_type=work_type, quotation=q,
                          sections=[{"category": "labour",
                                     "lines": [{"description": "Fitter", "qty": 100,
                                                "unit": "hour", "unit_cost": 450}]}])
    approve_estimate(est, None)
    ComplianceRequirement.objects.create(company=company, code="SF", name="Safety File",
                                         category="documentation", source="customer",
                                         is_mandatory=True, applies_when={})
    project = award_quotation(company, None, quotation=q, work_type=work_type)
    for item in project.compliance_items.filter(is_mandatory=True):
        approve_item(item, None, expiry=date.today() + timedelta(days=365))
    project.refresh_from_db()
    return project


class OrchestratorTests(APITestCase):
    def test_prepare_project_produces_consolidated_draft(self):
        c = make_company()
        with tenant_scope(c.id):
            project = compliant_project(c)
            user = user_with(c, PERMS)
            interaction = orchestrate(c, user, "Prepare this shutdown project", project=project)
            result = interaction.result
        self.assertEqual(interaction.agent, "lulama")
        self.assertEqual(interaction.approval_status, ApprovalStatus.DRAFT)
        agents = {a["agent"] for a in result["agents"]}
        # the full "prepare" chain ran, grounded in real modules
        self.assertTrue({"compliance", "commercial", "procurement"} <= agents)
        # every agent result carries confidence + sources (Confidence Engine)
        for a in result["agents"]:
            self.assertIn("confidence", a)
            self.assertTrue(a["sources"])

    def test_confidence_aggregated(self):
        c = make_company()
        with tenant_scope(c.id):
            project = compliant_project(c)
            user = user_with(c, PERMS)
            interaction = orchestrate(c, user, "compliance readiness?", project=project)
        self.assertGreater(interaction.confidence, Decimal("0.5"))


class AgentSecurityTests(APITestCase):
    def test_agent_skipped_when_user_lacks_permission(self):
        """The commercial agent needs finance.view_money — an ops user without it
        must NOT get commercial output (no privilege escalation via the AI)."""
        c = make_company()
        with tenant_scope(c.id):
            project = compliant_project(c)
            ops = user_with(c, ["ai.generate", "projects.view", "procurement.manage"],
                            email="ops@lulama.co.za")
            interaction = orchestrate(c, ops, "Prepare this project", project=project)
            agents = {a["agent"] for a in interaction.result["agents"]}
            omitted = {o["agent"] for o in interaction.result["omitted_agents"]}
        self.assertNotIn("commercial", agents)     # money agent withheld
        self.assertIn("commercial", omitted)       # and transparently reported

    def test_tool_refuses_without_permission(self):
        c = make_company()
        with tenant_scope(c.id):
            project = compliant_project(c)
            viewer = user_with(c, ["projects.view"], email="v@lulama.co.za")
            with self.assertRaises(ToolPermissionError):
                run_tool("project_profitability", viewer, project=project)
            # the denial is audited
            self.assertTrue(AuditLog.objects.filter(action="ai.tool_denied").exists())

    def test_available_tools_is_least_privilege(self):
        c = make_company()
        with tenant_scope(c.id):
            viewer = user_with(c, ["projects.view"], email="v2@lulama.co.za")
            money = user_with(c, ["projects.view", "finance.view_money"], email="m@lulama.co.za")
            self.assertNotIn("project_profitability", available_tools(viewer))
            self.assertIn("project_profitability", available_tools(money))


class GovernanceTests(APITestCase):
    def test_ai_proposes_but_never_sends_invoice(self):
        c = make_company()
        with tenant_scope(c.id):
            project = compliant_project(c)
            user = user_with(c, PERMS)
            invoice = create_invoice(project, user,
                                     lines=[{"description": "x", "qty": 1, "unit_price": 1000}])
            interaction = orchestrate(c, user, "Send the invoice to the customer now",
                                      project=project)
            invoice.refresh_from_db()
            proposals = interaction.result["proposed_actions"]
        # a forbidden action is surfaced as a human-approval proposal…
        send = [p for p in proposals if p["action"] == "send_invoice"]
        self.assertTrue(send)
        self.assertTrue(send[0]["requires_approval"])
        self.assertFalse(send[0]["executed_by_ai"])
        self.assertTrue(interaction.result["requires_human_approval"])
        # …and nothing was actually sent — the invoice is untouched
        self.assertEqual(invoice.status, InvoiceStatus.DRAFT)

    def test_decision_records_without_executing(self):
        c = make_company()
        with tenant_scope(c.id):
            project = compliant_project(c)
            user = user_with(c, PERMS)
            interaction = orchestrate(c, user, "Prepare this project", project=project)
            rejected = record_decision(interaction, user, approved=False)
        self.assertEqual(rejected.approval_status, ApprovalStatus.REJECTED)  # learning-loop log
        self.assertIsNotNone(rejected.decided_at)


class PromptVersionTests(APITestCase):
    def test_interaction_records_prompt_version(self):
        c = make_company()
        with tenant_scope(c.id):
            project = compliant_project(c)
            user = user_with(c, PERMS)
            interaction = orchestrate(c, user, "status?", project=project)
        self.assertTrue(interaction.prompt_version)     # versioned, never hardcoded blank
        self.assertEqual(interaction.provider, "deterministic")


class MeteringTests(APITestCase):
    @NO_AI
    def test_deterministic_path_consumes_no_credits(self):
        c = make_company()
        with tenant_scope(c.id):
            allocate_credits(c, Decimal("100"))
            project = compliant_project(c)
            user = user_with(c, PERMS)
            orchestrate(c, user, "Prepare this project", project=project)
            balance = credit_balance(c)
        self.assertEqual(balance, Decimal("100"))   # grounded/deterministic = free


class AIAPITests(APITestCase):
    def setUp(self):
        self.company = make_company()
        self.other = make_company("Rival")
        with tenant_scope(self.company.id):
            self.project = compliant_project(self.company)
            self.user = user_with(self.company, PERMS)

    def test_ask_and_decision_flow(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            "/api/v1/ai/interactions/ask/",
            {"request": "Prepare this project", "project": str(self.project.id)},
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        iid = resp.data["id"]
        self.assertEqual(resp.data["agent"], "lulama")
        resp = self.client.post(f"/api/v1/ai/interactions/{iid}/decision/",
                                {"approved": True}, format="json")
        self.assertEqual(resp.data["approval_status"], "approved")

    def test_ask_requires_ai_permission(self):
        noai = user_with(self.company, ["projects.view"], email="noai@lulama.co.za")
        self.client.force_authenticate(noai)
        resp = self.client.post("/api/v1/ai/interactions/ask/",
                                {"request": "status?", "project": str(self.project.id)},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_tenant_isolation(self):
        with tenant_scope(self.company.id):
            interaction = orchestrate(self.company, self.user, "status?", project=self.project)
        rival = user_with(self.other, PERMS, email="r@rival.co.za")
        self.client.force_authenticate(rival)
        resp = self.client.get(f"/api/v1/ai/interactions/{interaction.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ── Live-LLM enrichment (grounded, metered, with fallback) ────────────────────

from unittest.mock import patch  # noqa: E402

from .gateway import AIResponse  # noqa: E402
from .providers import AIProvider  # noqa: E402


class _StubProvider(AIProvider):
    name = "claude"

    def __init__(self, *, fail=False):
        self.fail = fail

    def complete(self, prompt, *, system="", max_tokens=2000):
        if self.fail:
            raise RuntimeError("provider timeout")
        # The stub echoes that it only saw grounded facts — never invents.
        return AIResponse(text="Executive briefing (grounded).", provider="claude",
                          tokens_in=50, tokens_out=20, credits_used=Decimal("1"))


class EnrichmentTests(APITestCase):
    def _setup(self):
        c = make_company()
        with tenant_scope(c.id):
            allocate_credits(c, Decimal("10"))
            project = compliant_project(c)
            user = user_with(c, PERMS)
        return c, project, user

    @override_settings(ANTHROPIC_API_KEY="a", OPENAI_API_KEY="", GEMINI_API_KEY="")
    def test_enrich_adds_briefing_and_meters_credits(self):
        c, project, user = self._setup()
        # Only Claude configured → the router (reasoning→Claude) picks the stub.
        with tenant_scope(c.id), \
                patch("apps.ai_platform.providers.get_provider",
                      return_value=_StubProvider()):
            interaction = orchestrate(c, user, "Prepare this project", project=project,
                                      enrich=True)
            balance = credit_balance(c)
        self.assertEqual(interaction.provider, "claude")
        self.assertIn("executive_briefing", interaction.result)
        self.assertEqual(balance, Decimal("9"))   # 10 − 1 credit metered

    @override_settings(ANTHROPIC_API_KEY="a", OPENAI_API_KEY="", GEMINI_API_KEY="")
    def test_provider_failure_falls_back_to_deterministic(self):
        c, project, user = self._setup()
        with tenant_scope(c.id), \
                patch("apps.ai_platform.providers.get_provider",
                      return_value=_StubProvider(fail=True)):
            interaction = orchestrate(c, user, "Prepare this project", project=project,
                                      enrich=True)
            balance = credit_balance(c)
        self.assertEqual(interaction.provider, "deterministic")   # graceful fallback
        self.assertNotIn("executive_briefing", interaction.result)
        self.assertEqual(balance, Decimal("10"))   # nothing debited on failure

    @NO_AI
    def test_default_path_stays_deterministic_without_key(self):
        c, project, user = self._setup()
        with tenant_scope(c.id):
            interaction = orchestrate(c, user, "Prepare this project", project=project)
        self.assertEqual(interaction.provider, "deterministic")   # enrich=None, no key
        self.assertEqual(credit_balance(c), Decimal("10"))
