"""LulaAI decomposition tests.

The invariants worth protecting: proposing writes NOTHING, applying creates only
what a human ticked, and the company's own completed work outranks the library.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.execution.models import ChecklistItem, Task, TaskStatus
from apps.execution.services import add_checklist_item, create_work
from apps.identity.models import Company, Membership, Permission, Role, User

from .decomposition import (
    Decomposition,
    apply_decomposition,
    propose_decomposition,
    record_proposal,
)
from .models import AIInteraction, ApprovalStatus


def make_company(name="Lulama"):
    return Company.objects.create(name=name)


def user_with(company, codenames, email=None):
    email = email or f"u{Role.objects.count()}@lulama.co.za"
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for code in codenames:
        perm, _ = Permission.objects.get_or_create(
            codename=code, defaults={"module": "x", "label": code})
        role.permissions.add(perm)
    user = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=user, company=company, role=role)
    return user


def _grounded_user(company):
    """A user permitted to run AI and manage work."""
    return user_with(company, ["ai.generate", "execution.manage"])


class ProposeIsReadOnlyTests(TestCase):
    """The human-approval boundary, applied to planning."""

    def test_propose_creates_nothing(self):
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            task = create_work(c, user, name="Replace pump seal on P-101")
            before_tasks = Task.objects.count()
            before_items = ChecklistItem.objects.count()

            draft = propose_decomposition(c, user, name=task.name, enrich=False)

            self.assertTrue(draft.checklist)              # it proposed something
            self.assertTrue(draft.requires_approval)
            self.assertFalse(draft.executed_by_ai)
            self.assertEqual(Task.objects.count(), before_tasks)
            self.assertEqual(ChecklistItem.objects.count(), before_items)

    def test_apply_with_no_selection_creates_nothing(self):
        """Refusing to guess is the point — an empty tick list means an empty result."""
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            task = create_work(c, user, name="Conveyor belt inspection")
            draft = propose_decomposition(c, user, name=task.name, enrich=False)

            applied = apply_decomposition(task, user, draft)

            self.assertEqual(applied["checklist"], 0)
            self.assertEqual(task.checklist_items.count(), 0)

    def test_apply_creates_only_selected_items(self):
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            task = create_work(c, user, name="Gearbox replacement on CV-102")
            draft = propose_decomposition(c, user, name=task.name, enrich=False)
            self.assertGreater(len(draft.checklist), 3)

            applied = apply_decomposition(task, user, draft, checklist_indexes={0, 2})

            labels = list(task.checklist_items.values_list("label", flat=True))
            self.assertEqual(applied["checklist"], 2)
            self.assertEqual(len(labels), 2)
            self.assertIn(draft.checklist[0], labels)
            self.assertIn(draft.checklist[2], labels)
            self.assertNotIn(draft.checklist[1], labels)

    def test_apply_hours_only_when_asked(self):
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            task = create_work(c, user, name="Pump overhaul")
            draft = propose_decomposition(c, user, name=task.name, enrich=False)

            apply_decomposition(task, user, draft)
            task.refresh_from_db()
            self.assertEqual(task.estimated_hours, Decimal("0"))

            apply_decomposition(task, user, draft, apply_hours=True)
            task.refresh_from_db()
            self.assertEqual(task.estimated_hours, Decimal(str(draft.estimated_hours)))


class GroundingTests(TestCase):
    """Grounded before generated — a contractor's own history wins."""

    def test_library_pattern_matches_work_type(self):
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            draft = propose_decomposition(
                c, user, name="Replace conveyor idlers on CV-7", enrich=False)

            self.assertEqual(draft.source, "library")
            self.assertIn("Conveyor", draft.work_type)
            joined = " ".join(draft.checklist).lower()
            self.assertIn("lock out", joined)

    def test_unmatched_work_falls_back_to_generic_with_low_confidence(self):
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            draft = propose_decomposition(c, user, name="Xyzzy plugh", enrich=False)

            self.assertEqual(draft.source, "generic")
            self.assertLess(draft.confidence, 0.6)

    def test_own_history_outranks_the_library(self):
        """Two past gearbox jobs sharing a step should drive the proposal, and
        their ACTUAL hours should become the estimate."""
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            for n in range(2):
                past = create_work(c, user, name=f"Gearbox swap on CV-{n}")
                add_checklist_item(past, user, label="Drain oil into bunded container")
                add_checklist_item(past, user, label="Record gearbox serial number")
                past.status = TaskStatus.COMPLETED
                past.actual_hours = Decimal("12.00")
                past.save()

            draft = propose_decomposition(
                c, user, name="Gearbox swap on CV-9", enrich=False)

            self.assertEqual(draft.source, "history")
            self.assertIn("Drain oil into bunded container", draft.checklist)
            self.assertEqual(draft.estimated_hours, 12.0)
            self.assertGreater(draft.confidence, 0.6)

    def test_history_never_leaks_across_tenants(self):
        """Company B's jobs must not inform Company A's proposal."""
        a, b = make_company(), make_company()
        with tenant_scope(b.id):
            user_b = _grounded_user(b)
            past = create_work(b, user_b, name="Gearbox swap on CV-1")
            add_checklist_item(past, user_b, label="COMPANY-B-SECRET-STEP")
            past.status = TaskStatus.COMPLETED
            past.save()

        with tenant_scope(a.id):
            user_a = _grounded_user(a)
            draft = propose_decomposition(
                a, user_a, name="Gearbox swap on CV-2", enrich=False)

        self.assertNotIn("COMPANY-B-SECRET-STEP", draft.checklist)
        self.assertEqual(draft.source, "library")


class EnrichmentTests(TestCase):
    """The LLM may only add to a grounded plan, and its failure must be harmless."""

    def test_provider_failure_keeps_the_grounded_draft(self):
        from apps.ai_platform.gateway import AllProvidersFailedError
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            with patch("apps.ai_platform.decomposition.run_task",
                       side_effect=AllProvidersFailedError("provider down")):
                draft = propose_decomposition(c, user, name="Pump seal replacement")

            self.assertTrue(draft.checklist)     # deterministic plan survived
            self.assertEqual(draft.provider, "")

    def test_llm_suggestions_are_appended_not_substituted(self):
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            baseline = propose_decomposition(
                c, user, name="Pump seal replacement", enrich=False)

            class _Resp:
                provider = "claude"
                text = ('{"extra_checklist": ["Fit new gasket set"], '
                        '"extra_risks": ["Wrong seal size on site"], '
                        '"briefing": "Standard seal job."}')

            with patch("apps.ai_platform.decomposition.run_task",
                       return_value=_Resp()):
                draft = propose_decomposition(c, user, name="Pump seal replacement")

            for step in baseline.checklist:
                self.assertIn(step, draft.checklist)      # nothing was replaced
            self.assertIn("Fit new gasket set", draft.checklist)
            self.assertIn("Wrong seal size on site", draft.risks)
            # Branded: users see "LulaAI", never the underlying vendor.
            self.assertEqual(draft.provider, "lulaai")

    def test_enrichment_requires_ai_permission(self):
        c = make_company()
        with tenant_scope(c.id):
            user = user_with(c, ["execution.manage"])          # no ai.generate
            with patch("apps.ai_platform.decomposition.run_task") as routed:
                draft = propose_decomposition(c, user, name="Pump seal replacement")
            routed.assert_not_called()
            self.assertTrue(draft.checklist)   # deterministic plan still offered


class AuditTests(TestCase):
    def test_proposal_is_a_draft_until_applied(self):
        c = make_company()
        with tenant_scope(c.id):
            user = _grounded_user(c)
            task = create_work(c, user, name="Pump overhaul")
            draft = propose_decomposition(c, user, name=task.name, enrich=False)

            proposed = record_proposal(c, user, task, draft)
            self.assertEqual(proposed.approval_status, ApprovalStatus.DRAFT)
            self.assertIsNone(proposed.decided_by)

            applied = apply_decomposition(task, user, draft, checklist_indexes={0})
            accepted = record_proposal(c, user, task, draft, applied=applied)
            self.assertEqual(accepted.approval_status, ApprovalStatus.APPROVED)
            self.assertEqual(accepted.decided_by, user)
            self.assertEqual(AIInteraction.objects.filter(
                agent="lulaai_decompose").count(), 2)
