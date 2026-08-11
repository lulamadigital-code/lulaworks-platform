"""Invitations & password reset — the secure, link-based account lifecycle.

The guarantees these protect: no password is ever created for or emailed to a
new user; the invite link is single-use and time-limited; a real user sets their
own password to activate; reset never reveals whether an email has an account;
and expired/used tokens are refused.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.context import tenant_scope

from .models import AccountToken, Company, Membership, Role, User
from .services import (
    MemberError,
    accept_invitation,
    invite_member,
    request_password_reset,
    reset_password,
)

EAGER = override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CELERY_TASK_ALWAYS_EAGER=True, SITE_URL="https://app.lulaworks.com")


@EAGER
class InvitationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Bright Contracting")
        self.role = Role.objects.create(name="Worker", is_system=True)
        self.admin = User.objects.create_user("admin@bright.co", "pass12345",
                                              active_company=self.company)

    def test_invite_creates_account_without_a_usable_password(self):
        from django.core import mail
        with tenant_scope(self.company.id):
            membership, token = invite_member(
                self.company, self.admin, email="new@bright.co", role=self.role,
                first_name="Thabo")
        user = membership.user
        self.assertFalse(user.has_usable_password())   # never emailed a password
        self.assertTrue(user.must_change_password)
        self.assertIsNotNone(token)
        self.assertEqual(token.purpose, AccountToken.Purpose.INVITE)
        # The invitation email carries the activation LINK, not a credential.
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn(f"/activate/{token.token}/", html)
        self.assertNotIn("password:", html.lower())

    def test_accept_sets_password_activates_and_consumes_token(self):
        with tenant_scope(self.company.id):
            _, token = invite_member(self.company, self.admin,
                                     email="new@bright.co", role=self.role)
            user = accept_invitation(token.token, password="s3curePass!")
        user.refresh_from_db()
        self.assertTrue(user.check_password("s3curePass!"))
        self.assertFalse(user.must_change_password)
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)          # single-use
        # A second use is refused.
        with self.assertRaises(MemberError):
            accept_invitation(token.token, password="anotherPass1!")

    def test_weak_password_is_rejected_on_activation(self):
        with tenant_scope(self.company.id):
            _, token = invite_member(self.company, self.admin,
                                     email="new@bright.co", role=self.role)
            with self.assertRaises(MemberError):
                accept_invitation(token.token, password="123")

    def test_expired_invite_is_refused(self):
        with tenant_scope(self.company.id):
            _, token = invite_member(self.company, self.admin,
                                     email="new@bright.co", role=self.role)
        token.expires_at = timezone.now() - timedelta(minutes=1)
        token.save(update_fields=["expires_at"])
        with self.assertRaises(MemberError):
            accept_invitation(token.token, password="s3curePass!")

    def test_existing_user_is_added_without_a_token(self):
        User.objects.create_user("already@bright.co", "existing1!")
        with tenant_scope(self.company.id):
            membership, token = invite_member(self.company, self.admin,
                                              email="already@bright.co", role=self.role)
        self.assertIsNone(token)                     # no activation link needed
        self.assertTrue(Membership.objects.filter(
            company=self.company, user__email="already@bright.co").exists())


@EAGER
class PasswordResetTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Co")
        self.user = User.objects.create_user("sam@co.za", "oldpass12!",
                                            active_company=self.company)

    def test_reset_sends_link_and_sets_new_password(self):
        from django.core import mail
        self.assertTrue(request_password_reset("sam@co.za"))
        self.assertEqual(len(mail.outbox), 1)
        token = AccountToken.objects.get(purpose=AccountToken.Purpose.RESET)
        reset_password(token.token, password="brandNew99!")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brandNew99!"))
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)

    def test_no_user_enumeration(self):
        from django.core import mail
        # Unknown address: same return, no token, no email.
        self.assertTrue(request_password_reset("nobody@nowhere.co"))
        self.assertEqual(AccountToken.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_used_reset_token_is_refused(self):
        request_password_reset("sam@co.za")
        token = AccountToken.objects.get(purpose=AccountToken.Purpose.RESET)
        reset_password(token.token, password="brandNew99!")
        with self.assertRaises(MemberError):
            reset_password(token.token, password="another123!")
