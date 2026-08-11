"""Event-triggered notifications — task, billing and security emails.

These prove the platform fires the right email on the right event, honouring
preferences and login access, and that the scheduled sweep reminds trials and
overdue work.
"""

from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.administration.models import NotificationPreference
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User

EAGER = override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CELERY_TASK_ALWAYS_EAGER=True, SITE_URL="https://app.lulaworks.com")


def admin_user(company, email="admin@co.io"):
    role = Role.objects.create(name=f"Admin-{email}", is_system=True)
    perm, _ = Permission.objects.get_or_create(
        codename="company.manage", defaults={"module": "company", "label": "Manage"})
    role.permissions.add(perm)
    u = User.objects.create_user(email, "pass12345", active_company=company)
    Membership.objects.create(company=company, user=u, role=role, status="active")
    return u


@EAGER
class TaskNotificationTests(TestCase):
    def test_assignment_emails_the_assignee(self):
        from apps.execution.models import Assignment, Task
        company = Company.objects.create(name="Co")
        worker = User.objects.create_user("worker@co.io", "x", active_company=company)
        with tenant_scope(company.id):
            task = Task.objects.create(company=company, name="Replace seal")
            from apps.execution.services import add_member
            add_member(task, worker, Assignment.Role.EXECUTOR)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Replace seal", mail.outbox[0].subject)

    def test_opted_out_worker_is_not_emailed(self):
        from apps.execution.models import Assignment, Task
        company = Company.objects.create(name="Co")
        worker = User.objects.create_user("w@co.io", "x", active_company=company)
        NotificationPreference.objects.create(user=worker, email=False)
        with tenant_scope(company.id):
            task = Task.objects.create(company=company, name="X")
            from apps.execution.services import add_member
            add_member(task, worker, Assignment.Role.EXECUTOR)
        self.assertEqual(len(mail.outbox), 0)

    def test_overdue_sweep_emails_assignee(self):
        from apps.execution.models import Assignment, Task
        from apps.execution.services import run_overdue_reminders
        company = Company.objects.create(name="Co")
        worker = User.objects.create_user("late@co.io", "x", active_company=company)
        with tenant_scope(company.id):
            task = Task.objects.create(
                company=company, name="Overdue job",
                due_date=timezone.localdate() - timedelta(days=2), status="in_progress")
            Assignment.objects.create(company=company, task=task, user=worker,
                                      role=Assignment.Role.EXECUTOR)
        mail.outbox.clear()
        emailed = run_overdue_reminders()
        self.assertEqual(emailed, 1)
        self.assertIn("overdue", mail.outbox[0].subject.lower())


@EAGER
class BillingNotificationTests(TestCase):
    def _plan(self, code="professional", tier=2):
        from apps.billing.models import Plan
        from apps.billing.services import GB
        return Plan.objects.create(code=code, name=code.title(), tier=tier,
                                   price=Decimal("1299"), annual_price=Decimal("12990"),
                                   max_users=10, storage_quota_bytes=50 * GB,
                                   monthly_ai_credits=Decimal("2000"))

    def test_credit_pack_receipt_emails_admin(self):
        from apps.billing.models import CreditPack
        from apps.billing.services import purchase_credit_pack
        company = Company.objects.create(name="Co")
        admin_user(company)
        CreditPack.objects.create(code="p100", name="100 credits",
                                  credits=Decimal("100"), price=Decimal("199"))
        with tenant_scope(company.id):
            purchase_credit_pack(company, "p100")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Receipt", mail.outbox[0].subject)

    def test_plan_change_emails_admin(self):
        from apps.billing.services import change_plan
        company = Company.objects.create(name="Co")
        admin_user(company)
        self._plan()
        with tenant_scope(company.id):
            change_plan(company, "professional")
        self.assertTrue(any("plan" in m.subject.lower() for m in mail.outbox))

    def test_trial_reminder_sweep(self):
        from apps.billing.models import BillingCycle, Subscription, SubscriptionStatus
        from apps.billing.services import run_trial_reminders
        company = Company.objects.create(name="Trialco")
        admin_user(company)
        Subscription.objects.create(
            company=company, plan=self._plan(), status=SubscriptionStatus.TRIAL,
            billing_cycle=BillingCycle.MONTHLY, currency="ZAR",
            current_period_end=timezone.localdate() + timedelta(days=3))
        mail.outbox.clear()
        counts = run_trial_reminders()
        self.assertEqual(counts["reminded"], 1)
        self.assertIn("trial ends soon", mail.outbox[0].subject.lower())


@EAGER
class SecurityNotificationTests(TestCase):
    def test_password_reset_sends_changed_confirmation(self):
        from apps.identity.services import request_password_reset, reset_password
        from apps.identity.models import AccountToken
        company = Company.objects.create(name="Co")
        user = User.objects.create_user("sam@co.io", "oldpass12!", active_company=company)
        request_password_reset("sam@co.io")            # reset-link email
        mail.outbox.clear()
        token = AccountToken.objects.get(purpose=AccountToken.Purpose.RESET)
        reset_password(token.token, password="brandNew99!")
        self.assertTrue(any("password was changed" in m.subject.lower()
                            for m in mail.outbox))
