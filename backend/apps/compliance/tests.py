"""Compliance Intelligence tests: discovery composes a project-specific checklist,
the computed readiness gate (not-ready → ready), continuous validation re-blocks
on expiry, permits keep the gate closed, override opens it (audited), plus API
permission gating and tenant isolation."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import AuditLog
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.projects.models import ProjectStatus
from apps.projects.services import award_quotation
from apps.quotes.models import Quotation

from .models import ComplianceRequirement, ItemStatus
from .services import (
    approve_item,
    can_start,
    override,
    recompute_readiness,
    validate_expiries,
)


def make_company(name="Lulama"):
    return Company.objects.create(name=name)


def seed_requirements(company):
    reqs = [
        ("SAFETY_FILE", "Safety File", "documentation", "customer", True, {}),
        ("MEDICAL", "Medical Fitness", "medical", "mine", True, {}),
        ("HOTWORK", "Hot Work Permit", "permit", "work_type", True,
         {"work_types": ["pump_overhaul"]}),
        ("WAH", "Working at Heights", "training", "work_type", True,
         {"work_types": ["working_at_heights"]}),  # should NOT apply to pump_overhaul
        ("TOOLBOX", "Toolbox Talk", "training", "policy", False, {}),  # non-mandatory
    ]
    for code, name, cat, source, mand, aw in reqs:
        ComplianceRequirement.objects.create(
            company=company, code=code, name=name, category=cat, source=source,
            is_mandatory=mand, applies_when=aw,
        )


def award(company, work_type="pump_overhaul"):
    q = Quotation.objects.create(company=company, number="QT-1", client_name="Sasol",
                                 site="Secunda")
    return award_quotation(company, None, quotation=q, work_type=work_type,
                           mine="Sasol Mine", site="Secunda")


class DiscoveryTests(APITestCase):
    def test_checklist_is_project_specific(self):
        c = make_company()
        with tenant_scope(c.id):
            seed_requirements(c)
            project = award(c, "pump_overhaul")
            names = set(project.compliance_items.values_list("name", flat=True))
            mandatory = project.compliance_items.filter(is_mandatory=True).count()
        # SAFETY_FILE + MEDICAL + HOTWORK (matches) + TOOLBOX (all, non-mandatory); WAH excluded
        self.assertIn("Hot Work Permit", names)
        self.assertNotIn("Working at Heights", names)   # wrong work type
        self.assertEqual(mandatory, 3)                   # SAFETY_FILE, MEDICAL, HOTWORK


class ReadinessGateTests(APITestCase):
    def test_not_ready_until_all_mandatory_satisfied(self):
        c = make_company()
        with tenant_scope(c.id):
            seed_requirements(c)
            project = award(c)
            r0 = recompute_readiness(project)
            self.assertEqual(r0["gate_status"], "not_ready")
            self.assertFalse(can_start(project))

            for item in project.compliance_items.filter(is_mandatory=True):
                approve_item(item, None, expiry=timezone.localdate() + timedelta(days=180))
            project.refresh_from_db()
            r1 = recompute_readiness(project)
            started = can_start(project)

        self.assertEqual(r1["gate_status"], "ready")
        self.assertTrue(started)
        self.assertEqual(project.status, ProjectStatus.READY)   # gate synced onto project

    def test_permit_missing_keeps_gate_closed(self):
        c = make_company()
        with tenant_scope(c.id):
            seed_requirements(c)
            project = award(c)
            # approve everything EXCEPT the permit
            for item in project.compliance_items.filter(is_mandatory=True).exclude(category="permit"):
                approve_item(item, None)
            r = recompute_readiness(project)
        self.assertEqual(r["gate_status"], "not_ready")
        self.assertTrue(any(b["category"] == "permit" for b in r["blocking"]))


class ContinuousValidationTests(APITestCase):
    def test_expiry_sweep_reblocks_project(self):
        c = make_company()
        with tenant_scope(c.id):
            seed_requirements(c)
            project = award(c)
            future = timezone.localdate() + timedelta(days=180)
            for item in project.compliance_items.filter(is_mandatory=True):
                approve_item(item, None, expiry=future)
            project.refresh_from_db()
            self.assertEqual(project.status, ProjectStatus.READY)

            # A medical lapses: backdate its expiry, then run the scheduled sweep.
            med = project.compliance_items.get(category="medical")
            med.expiry = timezone.localdate() - timedelta(days=1)
            med.save(update_fields=["expiry"])

        result = validate_expiries()   # cross-tenant sweep (no scope)

        with tenant_scope(c.id):
            project.refresh_from_db()
            med.refresh_from_db()
        self.assertEqual(result["expired"], 1)
        self.assertEqual(med.status, ItemStatus.EXPIRED)
        self.assertEqual(project.status, ProjectStatus.PENDING_COMPLIANCE)  # re-blocked


class OverrideTests(APITestCase):
    def test_override_opens_gate_and_is_audited(self):
        c = make_company()
        with tenant_scope(c.id):
            seed_requirements(c)
            project = award(c)
            user = User.objects.create_user("safety@lulama.co.za", "x", active_company=c)
            self.assertFalse(can_start(project))
            override(project, user, reason="Client accepted risk; permit in progress")
            project.refresh_from_db()
            r = recompute_readiness(project)
            started = can_start(project)
        self.assertEqual(r["gate_status"], "overridden")
        self.assertTrue(started)
        self.assertEqual(project.status, ProjectStatus.READY)
        self.assertTrue(AuditLog.objects.filter(action="compliance.override").exists())

    def test_override_requires_reason(self):
        c = make_company()
        with tenant_scope(c.id):
            seed_requirements(c)
            project = award(c)
            with self.assertRaises(ValueError):
                override(project, None, reason="")


class ComplianceAPITests(APITestCase):
    def setUp(self):
        self.company = make_company()
        self.other = make_company("Rival")
        create = Permission.objects.create(codename="projects.create", module="projects", label="C")
        ov = Permission.objects.create(codename="compliance.override", module="compliance", label="O")
        self.ops_role = Role.objects.create(name="Ops", is_system=True)
        self.ops_role.permissions.add(create)
        self.safety_role = Role.objects.create(name="Safety", is_system=True)
        self.safety_role.permissions.add(create, ov)

        self.ops = User.objects.create_user("ops@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.ops, company=self.company, role=self.ops_role)
        self.safety = User.objects.create_user("saf@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.safety, company=self.company, role=self.safety_role)
        with tenant_scope(self.company.id):
            seed_requirements(self.company)
            self.quotation = Quotation.objects.create(
                company=self.company, number="QT-9", client_name="Sasol", site="Secunda"
            )

    def _award(self):
        self.client.force_authenticate(self.ops)
        return self.client.post("/api/v1/projects/", {
            "quotation": str(self.quotation.id), "work_type": "pump_overhaul",
            "mine": "Sasol Mine", "site": "Secunda",
        }, format="json")

    def test_award_creates_project_and_readiness(self):
        resp = self._award()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        pid = resp.data["id"]
        self.assertEqual(resp.data["status"], "pending_compliance")
        r = self.client.get(f"/api/v1/projects/{pid}/readiness/")
        self.assertEqual(r.data["gate_status"], "not_ready")
        self.assertGreater(len(r.data["blocking"]), 0)

    def test_override_requires_permission(self):
        pid = self._award().data["id"]
        # ops lacks compliance.override
        resp = self.client.post(f"/api/v1/projects/{pid}/override/", {"reason": "x"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        # safety can
        self.client.force_authenticate(self.safety)
        resp = self.client.post(f"/api/v1/projects/{pid}/override/",
                                {"reason": "accepted risk"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(resp.data["gate_status"], ("overridden", "ready"))

    def test_tenant_isolation(self):
        pid = self._award().data["id"]
        rival = User.objects.create_user("r@rival.co.za", "x", active_company=self.other)
        Membership.objects.create(user=rival, company=self.other, role=self.safety_role)
        self.client.force_authenticate(rival)
        resp = self.client.get(f"/api/v1/projects/{pid}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
