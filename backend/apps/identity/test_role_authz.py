"""Role-assignment authorization — no privilege escalation, and member invites
validate the email. Backend is authoritative."""
from django.test import TestCase, override_settings

from apps.identity.models import Company, Membership, Permission, Role, User
from apps.identity.services import (MemberError, can_assign_role, invite_member,
                                    selectable_roles)


def role_with(name, codenames):
    role = Role.objects.create(name=name, is_system=True)
    for code in codenames:
        perm, _ = Permission.objects.get_or_create(
            codename=code, defaults={"module": "x", "label": code})
        role.permissions.add(perm)
    return role


class RoleAuthorizationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        # An all-permissions owner, and a scoped manager who can invite but has no
        # finance rights.
        self.owner_role = role_with("Company Owner",
                                    ["users.invite", "finance.manage", "projects.view"])
        self.mgr_role = role_with("Ops Manager", ["users.invite", "projects.view"])
        self.finance_role = role_with("Finance Manager", ["finance.manage", "projects.view"])
        self.owner = self._member("owner@a.co", self.owner_role)
        self.mgr = self._member("mgr@a.co", self.mgr_role)

    def _member(self, email, role):
        u = User.objects.create_user(email, "x", active_company=self.company)
        Membership.objects.create(company=self.company, user=u, role=role, status="active")
        return u

    def test_owner_can_assign_any_role(self):
        self.assertTrue(can_assign_role(self.owner, self.finance_role))
        self.assertTrue(can_assign_role(self.owner, self.mgr_role))

    def test_manager_cannot_escalate_beyond_own_permissions(self):
        # Manager lacks finance.manage, so cannot grant the finance role.
        self.assertFalse(can_assign_role(self.mgr, self.finance_role))

    def test_manager_can_assign_subset_role(self):
        viewer = role_with("Viewer", ["projects.view"])
        self.assertTrue(can_assign_role(self.mgr, viewer))

    def test_selectable_roles_filtered_for_actor(self):
        names = {r.name for r in selectable_roles(self.mgr)}
        self.assertIn("Ops Manager", names)
        self.assertNotIn("Finance Manager", names)     # can't assign what they can't hold
        self.assertIn("Finance Manager", {r.name for r in selectable_roles(self.owner)})

    def test_superuser_unbounded(self):
        su = User.objects.create_user("root@a.co", "x", active_company=self.company,
                                      is_superuser=True)
        self.assertTrue(can_assign_role(su, self.finance_role))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InviteValidationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        self.owner_role = role_with("Company Owner", ["users.invite"])
        self.owner = User.objects.create_user("owner@a.co", "x", active_company=self.company)
        Membership.objects.create(company=self.company, user=self.owner,
                                  role=self.owner_role, status="active")

    def test_malformed_email_rejected(self):
        for bad in ("john@", "john example.com", "@a.co"):
            with self.assertRaises(MemberError):
                invite_member(self.company, self.owner, email=bad, role=self.owner_role)

    def test_email_normalised_on_invite(self):
        membership, token = invite_member(
            self.company, self.owner, email="  New.Person@Acme.CO ", role=self.owner_role)
        self.assertEqual(membership.user.email, "new.person@acme.co")
