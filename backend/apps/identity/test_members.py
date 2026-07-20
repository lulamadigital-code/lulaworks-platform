"""Company membership tests.

The two rules that were asked for, plus the lockout guards that make them safe:
add directly with a one-time password, and deactivate rather than delete.
"""

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.execution.models import Assignment
from apps.execution.services import add_member as assign_to_work
from apps.execution.services import create_work

from .models import Company, Membership, Permission, Role, User
from .services import (
    MemberError,
    add_member,
    assignable_users,
    generate_temp_password,
    set_member_status,
    set_password,
)


def role_with(name, codenames):
    role = Role.objects.create(name=name, is_system=True)
    for code in codenames:
        perm, _ = Permission.objects.get_or_create(
            codename=code, defaults={"module": "x", "label": code})
        role.permissions.add(perm)
    return role


class AddMemberTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        self.admin_role = role_with("Owner", ["users.invite", "execution.manage"])
        self.worker_role = role_with("Worker", ["projects.view"])
        self.admin = User.objects.create_user(
            "boss@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(company=self.company, user=self.admin,
                                  role=self.admin_role, status="active")

    def test_add_creates_user_with_one_time_password(self):
        membership, temp = add_member(
            self.company, self.admin, email="thabo@lulama.co.za",
            role=self.worker_role, first_name="Thabo")

        self.assertIsNotNone(temp)
        self.assertEqual(membership.user.email, "thabo@lulama.co.za")
        self.assertEqual(membership.invited_by, self.admin)
        # The generated password works, but the account is gated until replaced.
        self.assertTrue(membership.user.check_password(temp))
        self.assertTrue(membership.user.must_change_password)

    def test_choosing_a_password_clears_the_gate(self):
        membership, temp = add_member(self.company, self.admin,
                                      email="thabo@lulama.co.za", role=self.worker_role)
        set_password(membership.user, "a-much-better-secret")
        membership.user.refresh_from_db()

        self.assertFalse(membership.user.must_change_password)
        self.assertTrue(membership.user.check_password("a-much-better-secret"))
        self.assertFalse(membership.user.check_password(temp))

    def test_duplicate_membership_is_refused(self):
        add_member(self.company, self.admin, email="thabo@lulama.co.za",
                   role=self.worker_role)
        with self.assertRaises(MemberError):
            add_member(self.company, self.admin, email="thabo@lulama.co.za",
                       role=self.worker_role)

    def test_existing_platform_user_joins_without_a_new_password(self):
        """Multi-company: an email that already exists gains a second membership
        and keeps the password it already had."""
        other = Company.objects.create(name="Other Contractor")
        existing = User.objects.create_user("shared@lulama.co.za", "their-own-secret",
                                            active_company=other)
        Membership.objects.create(company=other, user=existing, role=self.worker_role)

        membership, temp = add_member(self.company, self.admin,
                                      email="shared@lulama.co.za", role=self.worker_role)

        self.assertIsNone(temp)
        self.assertEqual(membership.user_id, existing.id)
        self.assertTrue(existing.check_password("their-own-secret"))
        self.assertEqual(Membership.objects.filter(user=existing).count(), 2)

    def test_temp_passwords_are_unambiguous_and_varied(self):
        for _ in range(30):
            pw = generate_temp_password()
            self.assertEqual(len(pw), 12)
            self.assertFalse(set(pw) & set("O0l1I"))     # readable over a phone
            self.assertTrue(any(c.isupper() for c in pw))
            self.assertTrue(any(c.islower() for c in pw))
            self.assertTrue(any(c.isdigit() for c in pw))


class DeactivationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Lulama")
        self.admin_role = role_with("Owner", ["users.invite", "execution.manage"])
        self.worker_role = role_with("Worker", ["projects.view"])
        self.admin = User.objects.create_user(
            "boss@lulama.co.za", "x", active_company=self.company)
        Membership.objects.create(company=self.company, user=self.admin,
                                  role=self.admin_role, status="active")
        self.membership, _ = add_member(self.company, self.admin,
                                        email="thabo@lulama.co.za", role=self.worker_role)
        self.worker = self.membership.user

    def test_deactivation_revokes_every_permission_immediately(self):
        self.assertTrue(self.worker.has_perm_code("projects.view"))
        set_member_status(self.membership, self.admin, active=False)
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.has_perm_code("projects.view"))

    def test_deactivated_member_leaves_the_assignment_pickers(self):
        self.assertIn(self.worker, assignable_users(self.company))
        set_member_status(self.membership, self.admin, active=False)
        self.assertNotIn(self.worker, assignable_users(self.company))

    def test_deactivation_keeps_their_name_on_past_work(self):
        """The whole point of not deleting: the audit trail must stay true."""
        with tenant_scope(self.company.id):
            task = create_work(self.company, self.admin, name="Replace pump seal")
            assign_to_work(task, self.worker, Assignment.Role.EXECUTOR)

            set_member_status(self.membership, self.admin, active=False)

            task.refresh_from_db()
            self.assertIn(self.worker, task.team(Assignment.Role.EXECUTOR))
            self.assertTrue(User.objects.filter(pk=self.worker.pk).exists())

    def test_cannot_deactivate_yourself(self):
        own = Membership.objects.get(company=self.company, user=self.admin)
        with self.assertRaises(MemberError):
            set_member_status(own, self.admin, active=False)

    def test_cannot_strand_the_company_without_an_administrator(self):
        """Deactivating the last user-manager would lock everyone out forever."""
        second_admin = User.objects.create_user("second@lulama.co.za", "x",
                                                active_company=self.company)
        m2 = Membership.objects.create(company=self.company, user=second_admin,
                                       role=self.admin_role, status="active")
        # Two admins: removing one is fine.
        set_member_status(m2, self.admin, active=False)
        # Now only `self.admin` can manage users — and they cannot remove themselves,
        # so try from a third party's perspective.
        third = User.objects.create_user("third@lulama.co.za", "x",
                                         active_company=self.company)
        Membership.objects.create(company=self.company, user=third,
                                  role=self.worker_role, status="active")
        own = Membership.objects.get(company=self.company, user=self.admin)
        with self.assertRaises(MemberError):
            set_member_status(own, third, active=False)

    def test_reactivation_restores_access(self):
        set_member_status(self.membership, self.admin, active=False)
        set_member_status(self.membership, self.admin, active=True)
        self.worker.refresh_from_db()
        self.assertTrue(self.worker.has_perm_code("projects.view"))
        self.assertIn(self.worker, assignable_users(self.company))

    def test_rejoining_reuses_the_membership_row(self):
        set_member_status(self.membership, self.admin, active=False)
        membership, temp = add_member(self.company, self.admin,
                                      email="thabo@lulama.co.za", role=self.worker_role)
        self.assertEqual(membership.pk, self.membership.pk)
        self.assertEqual(membership.status, "active")
        self.assertIsNone(temp)          # they keep the password they had
        self.assertEqual(
            Membership.objects.filter(company=self.company, user=self.worker).count(), 1)
