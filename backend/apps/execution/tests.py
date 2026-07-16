"""Execution tests: computed task readiness (predecessors + compliance gate +
materials), compliance-aware resource allocation (double-booking + expired
credential), the actuals loop that closes Module 7, project health, the
customer/internal report split (Golden Rule), plus API permissions + isolation."""

from datetime import date, timedelta
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.compliance.models import ComplianceRequirement
from apps.compliance.services import approve_item
from apps.core.context import tenant_scope
from apps.estimating.services import approve_estimate, create_estimate
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.procurement.models import Supplier
from apps.procurement.services import create_purchase_order
from apps.projects.services import award_quotation
from apps.quotes.models import Quotation

from .models import Resource, Task, TaskStatus
from .services import (
    AllocationError,
    allocate_resource,
    capture_project_actuals,
    complete_task,
    compute_task_readiness,
    daily_progress_report,
    project_health,
    start_task,
)


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("po", "PO"), ("estimate", "EST")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


def award_ready_project(company, work_type="pump_overhaul", compliant=True):
    """Award a project and (optionally) satisfy its mandatory compliance so the gate is open."""
    q = Quotation.objects.create(company=company, number="QT-1", client_name="Sasol")
    ComplianceRequirement.objects.create(company=company, code="SAFETY_FILE", name="Safety File",
                                         category="documentation", source="customer",
                                         is_mandatory=True, applies_when={})
    project = award_quotation(company, None, quotation=q, work_type=work_type)
    if compliant:
        for item in project.compliance_items.filter(is_mandatory=True):
            approve_item(item, None, expiry=date.today() + timedelta(days=365))
        project.refresh_from_db()
    return project


class TaskReadinessTests(APITestCase):
    def test_readiness_reflects_compliance_gate(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c, compliant=False)  # gate closed
            task = Task.objects.create(company=c, project=project, name="Strip pump",
                                       blocks_on_compliance=True)
            s0, r0 = compute_task_readiness(task)
            # open the gate
            for item in project.compliance_items.filter(is_mandatory=True):
                approve_item(item, None, expiry=date.today() + timedelta(days=365))
            s1, r1 = compute_task_readiness(task)
        self.assertEqual(s0, TaskStatus.BLOCKED)
        self.assertIn("compliance", r0)
        self.assertEqual(s1, TaskStatus.READY)   # gate opened → task now ready

    def test_predecessor_blocks_until_complete(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c)
            a = Task.objects.create(company=c, project=project, name="A", blocks_on_compliance=False)
            b = Task.objects.create(company=c, project=project, name="B", blocks_on_compliance=False)
            b.predecessors.add(a)
            s_blocked, reason = compute_task_readiness(b)
            complete_task(a, None)
            s_ready, _ = compute_task_readiness(b)
        self.assertEqual(s_blocked, TaskStatus.BLOCKED)
        self.assertIn("predecessor", reason)
        self.assertEqual(s_ready, TaskStatus.READY)

    def test_material_po_blocks_until_received(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c)
            supplier = Supplier.objects.create(company=c, name="NJR")
            po = create_purchase_order(c, None, supplier=supplier,
                                       lines=[{"description": "Seal kit", "qty": 5, "unit_price": 100}])
            task = Task.objects.create(company=c, project=project, name="Fit seals",
                                       blocks_on_compliance=False, material_po=po)
            s, reason = compute_task_readiness(task)
        self.assertEqual(s, TaskStatus.BLOCKED)
        self.assertIn("materials not delivered", reason)

    def test_start_enforced_by_readiness(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c, compliant=False)
            task = Task.objects.create(company=c, project=project, name="X")
            with self.assertRaises(ValueError):
                start_task(task, None)   # gate closed → cannot start


class ResourceAllocationTests(APITestCase):
    def test_double_booking_refused(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c)
            crane = Resource.objects.create(company=c, kind="equipment", name="Crane 25t")
            allocate_resource(c, None, resource=crane, project=project,
                              start_date=date(2026, 8, 1), end_date=date(2026, 8, 10))
            with self.assertRaises(AllocationError) as ctx:
                allocate_resource(c, None, resource=crane, project=project,
                                  start_date=date(2026, 8, 5), end_date=date(2026, 8, 15))
        self.assertTrue(any("already allocated" in w for w in ctx.exception.warnings))

    def test_expired_medical_refused_then_forced(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c)
            fitter = Resource.objects.create(company=c, kind="employee", name="J. Dlamini",
                                             medical_expiry=date(2026, 1, 1))  # expired
            with self.assertRaises(AllocationError) as ctx:
                allocate_resource(c, None, resource=fitter, project=project,
                                  start_date=date(2026, 8, 1), end_date=date(2026, 8, 2))
            # a manager may force past it, with a reason (audited)
            alloc = allocate_resource(c, None, resource=fitter, project=project,
                                      start_date=date(2026, 8, 1), end_date=date(2026, 8, 2),
                                      force=True, override_reason="medical renewal booked")
        self.assertTrue(any("medical expired" in w for w in ctx.exception.warnings))
        self.assertEqual(alloc.override_reason, "medical renewal booked")


class ActualsLoopTests(APITestCase):
    def test_capture_actuals_feeds_estimate(self):
        c = make_company()
        with tenant_scope(c.id):
            q = Quotation.objects.create(company=c, number="QT-2", client_name="Sasol")
            estimate = create_estimate(c, None, client_name="Sasol", work_type="pump_overhaul",
                                       quotation=q, sections=[
                {"category": "labour", "lines": [{"description": "Fitter", "qty": 100,
                                                  "unit": "hour", "unit_cost": 450}]},  # est 45000
            ])
            approve_estimate(estimate, None)
            ComplianceRequirement.objects.create(company=c, code="SF", name="Safety File",
                                                 category="documentation", source="customer",
                                                 is_mandatory=True, applies_when={})
            project = award_quotation(c, None, quotation=q, work_type="pump_overhaul")
            # log + approve a timesheet: 120h @ R450 = R54 000 actual labour (+20%)
            fitter = Resource.objects.create(company=c, kind="employee", name="Fitter",
                                             hourly_rate=Decimal("450"))
            task = Task.objects.create(company=c, project=project, name="Overhaul",
                                       blocks_on_compliance=False)
            task.timesheets.create(company=c, resource=fitter, date=date.today(),
                                   hours=Decimal("120"), approved=True)
            result = capture_project_actuals(project, None)
            estimate.refresh_from_db()
            labour_actual = estimate.actuals.get(category="labour")
        self.assertEqual(result["captured"]["labour"], Decimal("54000.00"))
        self.assertEqual(labour_actual.actual_cost, Decimal("54000.00"))
        self.assertEqual(labour_actual.variance, Decimal("9000.00"))   # +R9 000 vs estimate


class HealthAndReportTests(APITestCase):
    def test_customer_report_hides_cost_and_issues(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c)
            Task.objects.create(company=c, project=project, name="A", blocks_on_compliance=False,
                                status=TaskStatus.BLOCKED, blocked_reason="waiting")
            internal = daily_progress_report(project, audience="internal")
            customer = daily_progress_report(project, audience="customer")
        self.assertIn("actual_costs", internal)
        self.assertIn("blocked", internal)
        self.assertNotIn("actual_costs", customer)     # cost withheld
        self.assertNotIn("blocked", customer)          # internal issues withheld
        self.assertIn("progress_pct", customer)        # progress shown

    def test_health_composite(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c)
            Task.objects.create(company=c, project=project, name="A", blocks_on_compliance=False)
            health = project_health(project)   # no user → budget dimension omitted
        self.assertIn("overall", health)
        self.assertIn("compliance", health["dimensions"])
        self.assertNotIn("budget", health["dimensions"])   # Golden Rule: no finance perm


class ExecutionAPITests(APITestCase):
    def setUp(self):
        self.company = make_company()
        self.other = make_company("Rival")
        manage = Permission.objects.create(codename="execution.manage", module="execution", label="M")
        create = Permission.objects.create(codename="projects.create", module="projects", label="C")
        self.role = Role.objects.create(name="Ops", is_system=True)
        self.role.permissions.add(manage, create)
        self.ops = User.objects.create_user("ops@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.ops, company=self.company, role=self.role)
        self.viewer = User.objects.create_user("view@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(user=self.viewer, company=self.company,
                                  role=Role.objects.create(name="Viewer", is_system=True))
        with tenant_scope(self.company.id):
            self.project = award_ready_project(self.company)

    def test_create_task_requires_permission(self):
        self.client.force_authenticate(self.viewer)
        resp = self.client.post("/api/v1/tasks/", {"project": str(self.project.id), "name": "X"},
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.ops)
        resp = self.client.post("/api/v1/tasks/", {"project": str(self.project.id),
                                "name": "Strip pump", "blocks_on_compliance": False}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["readiness"]["status"], "ready")   # live computed

    def test_allocation_conflict_returns_409(self):
        self.client.force_authenticate(self.ops)
        with tenant_scope(self.company.id):
            r = Resource.objects.create(company=self.company, kind="equipment", name="Crane")
        base = {"resource": str(r.id), "project": str(self.project.id),
                "start_date": "2026-08-01", "end_date": "2026-08-10"}
        self.assertEqual(self.client.post("/api/v1/resource-allocations/", base,
                                          format="json").status_code, 201)
        clash = {**base, "start_date": "2026-08-05", "end_date": "2026-08-12"}
        resp = self.client.post("/api/v1/resource-allocations/", clash, format="json")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("warnings", resp.data["error"])

    def test_tenant_isolation(self):
        self.client.force_authenticate(self.ops)
        with tenant_scope(self.company.id):
            task = Task.objects.create(company=self.company, project=self.project, name="Secret")
        rival = User.objects.create_user("r@rival.co.za", "x", active_company=self.other)
        Membership.objects.create(user=rival, company=self.other, role=self.role)
        self.client.force_authenticate(rival)
        resp = self.client.get(f"/api/v1/tasks/{task.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
