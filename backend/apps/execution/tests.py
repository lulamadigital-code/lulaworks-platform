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

from .models import (
    Assignment,
    AutomationRule,
    Notification,
    Resource,
    Task,
    TaskDependency,
    TaskStatus,
    WorkOrigin,
)
from .services import (
    AllocationError,
    add_checklist_item,
    add_subtask,
    allocate_resource,
    can_modify,
    capture_project_actuals,
    complete_task,
    compute_task_readiness,
    create_work,
    daily_progress_report,
    ensure_default_phases,
    link_tasks,
    next_statuses,
    portfolio_report,
    project_health,
    refresh_task_status,
    start_task,
    toggle_checklist_item,
    transition,
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
            link_tasks(a, b)
            s_blocked, reason = compute_task_readiness(b)
            complete_task(a, None)
            s_ready, _ = compute_task_readiness(b)
        self.assertEqual(s_blocked, TaskStatus.BLOCKED)
        self.assertIn("waiting for A to finish", reason)
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


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 8 — Work Management Engine
# ══════════════════════════════════════════════════════════════════════════════

class WorkEngineTests(APITestCase):
    """One engine for every origin: hierarchy roll-up, typed dependencies, the
    team model, the lifecycle, notifications and automations."""

    def _user(self, company, email="lead@lulama.co.za"):
        user = User.objects.create_user(email, "x", active_company=company)
        Membership.objects.create(user=user, company=company,
                                  role=Role.objects.create(name=email, is_system=True))
        return user

    def test_standalone_work_needs_no_project_or_compliance(self):
        """A two-person shop logging "replace a valve" must not be gated by a
        project compliance file it will never have."""
        c = make_company()
        with tenant_scope(c.id):
            task = create_work(c, None, name="Replace valve", origin=WorkOrigin.BREAKDOWN)
            status_, reason = compute_task_readiness(task)
            self.assertTrue(task.is_standalone)
            self.assertFalse(task.blocks_on_compliance)
            self.assertEqual(status_, TaskStatus.READY)
            self.assertEqual(reason, "")

    def test_every_origin_flows_through_one_engine(self):
        c = make_company()
        with tenant_scope(c.id):
            for origin, _label in WorkOrigin.choices:
                task = create_work(c, None, name=f"Work via {origin}", origin=origin)
                self.assertEqual(task.origin, origin)
                # Same lifecycle, same computed readiness, regardless of the door.
                self.assertIn(task.status, (TaskStatus.READY, TaskStatus.BLOCKED))
            self.assertEqual(Task.objects.count(), len(WorkOrigin.choices))

    def test_checklist_ticks_roll_up_to_task_progress(self):
        """Progress is DERIVED from what the crew ticked off — never typed in."""
        c = make_company()
        with tenant_scope(c.id):
            task = create_work(c, None, name="Service gearbox")
            a = add_checklist_item(task, None, label="Drain oil")
            add_checklist_item(task, None, label="Replace seals")
            self.assertEqual(task.progress_pct, 0)

            toggle_checklist_item(a, None, done=True)
            task.refresh_from_db()
            self.assertEqual(task.progress_pct, 50)

    def test_subtask_completes_when_its_checklist_is_done(self):
        c = make_company()
        with tenant_scope(c.id):
            task = create_work(c, None, name="Install pump")
            sub = add_subtask(task, None, name="Mechanical")
            one = add_checklist_item(task, None, label="Align", subtask=sub)
            two = add_checklist_item(task, None, label="Grout", subtask=sub)

            toggle_checklist_item(one, None, done=True)
            sub.refresh_from_db()
            self.assertFalse(sub.is_done)

            toggle_checklist_item(two, None, done=True)
            sub.refresh_from_db()
            self.assertTrue(sub.is_done)

    def test_typed_dependency_explains_itself(self):
        """The blocked reason must name the real-world wait, not just "blocked"."""
        c = make_company()
        with tenant_scope(c.id):
            a = create_work(c, None, name="Supplier delivery")
            b = create_work(c, None, name="Install")
            link_tasks(a, b, kind=TaskDependency.Kind.WAITING_DELIVERY)

            status_, reason = compute_task_readiness(b)
            self.assertEqual(status_, TaskStatus.BLOCKED)
            self.assertIn("waiting for delivery on Supplier delivery", reason)

            complete_task(a, None)
            status_after, _ = compute_task_readiness(b)
            self.assertEqual(status_after, TaskStatus.READY)

    def test_start_to_start_dependency_clears_once_predecessor_starts(self):
        c = make_company()
        with tenant_scope(c.id):
            a = create_work(c, None, name="Scaffold")
            b = create_work(c, None, name="Paint")
            link_tasks(a, b, kind=TaskDependency.Kind.START_TO_START)
            blocked, _ = compute_task_readiness(b)

            start_task(a, None)
            ready, _ = compute_task_readiness(b)
            self.assertEqual(blocked, TaskStatus.BLOCKED)
            self.assertEqual(ready, TaskStatus.READY)

    def test_circular_dependency_is_refused(self):
        c = make_company()
        with tenant_scope(c.id):
            a = create_work(c, None, name="A")
            b = create_work(c, None, name="B")
            link_tasks(a, b)
            with self.assertRaises(ValueError):
                link_tasks(b, a)

    def test_work_carries_a_team_not_one_assignee(self):
        c = make_company()
        with tenant_scope(c.id):
            owner = self._user(c, "owner@lulama.co.za")
            hand = self._user(c, "hand@lulama.co.za")
            watcher = self._user(c, "watch@lulama.co.za")
            task = create_work(c, owner, name="Shutdown prep", owner=owner,
                               executors=[hand], watchers=[watcher])

            self.assertEqual(task.owner, owner)
            self.assertIn(hand, task.team(Assignment.Role.EXECUTOR))
            self.assertIn(watcher, task.team(Assignment.Role.WATCHER))
            # Watchers are read-only; the execution team is not.
            self.assertFalse(can_modify(task, watcher))
            self.assertTrue(can_modify(task, hand))

    def test_team_is_notified_but_the_actor_is_not(self):
        c = make_company()
        with tenant_scope(c.id):
            owner = self._user(c, "o@lulama.co.za")
            hand = self._user(c, "h@lulama.co.za")
            task = create_work(c, owner, name="Callout", owner=owner, executors=[hand])
            Notification.objects.all().delete()

            complete_task(task, owner)
            recipients = set(Notification.objects.values_list("user_id", flat=True))
            self.assertIn(hand.id, recipients)
            self.assertNotIn(owner.id, recipients)   # you don't notify yourself

    def test_lifecycle_transition_and_next_steps(self):
        c = make_company()
        with tenant_scope(c.id):
            task = create_work(c, None, name="Weld repair")
            self.assertEqual(task.status, TaskStatus.READY)

            transition(task, None, to_status=TaskStatus.QUALITY_CHECK)
            task.refresh_from_db()
            self.assertEqual(task.status, TaskStatus.QUALITY_CHECK)
            # A human deliberately parked it here — the engine must not overrule.
            refresh_task_status(task)
            task.refresh_from_db()
            self.assertEqual(task.status, TaskStatus.QUALITY_CHECK)
            self.assertIn(TaskStatus.CLIENT_SIGNOFF, next_statuses(task))

    def test_automation_notifies_approvers_on_completion(self):
        """Automations move information — they never approve anything."""
        c = make_company()
        with tenant_scope(c.id):
            approver = self._user(c, "app@lulama.co.za")
            task = create_work(c, None, name="Panel build", approvers=[approver])
            AutomationRule.objects.create(
                company=c, name="Tell the approvers",
                trigger=AutomationRule.Trigger.TASK_COMPLETED,
                action=AutomationRule.Action.NOTIFY_APPROVERS,
            )
            Notification.objects.all().delete()

            complete_task(task, None)
            rows = Notification.objects.filter(user=approver)
            self.assertTrue(rows.exists())
            # Completion still required a human — nothing self-approved.
            self.assertEqual(rows.first().verb, "approval_required")

    def test_project_work_still_honours_the_compliance_gate(self):
        """The unified engine must not weaken the hard gate for project work."""
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c, compliant=False)
            task = create_work(c, None, name="Mobilise crew", project=project)
            status_, reason = compute_task_readiness(task)
            self.assertEqual(status_, TaskStatus.BLOCKED)
            self.assertIn("compliance", reason)

    def test_default_phases_seed_once(self):
        c = make_company()
        with tenant_scope(c.id):
            project = award_ready_project(c)
            first = ensure_default_phases(project, None)
            again = ensure_default_phases(project, None)
            self.assertEqual(len(first), 7)
            self.assertEqual(len(again), 7)
            self.assertEqual(project.phases.count(), 7)

    def test_portfolio_report_counts_blocked_and_overdue(self):
        c = make_company()
        with tenant_scope(c.id):
            done = create_work(c, None, name="Done")
            complete_task(done, None)
            late = create_work(c, None, name="Late",
                               due_date=date.today() - timedelta(days=3))
            report = portfolio_report()
            self.assertEqual(report["total"], 2)
            self.assertEqual(report["completed"], 1)
            self.assertEqual(report["completion_pct"], 50)
            self.assertIn(late, report["overdue"])
