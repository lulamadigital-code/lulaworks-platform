"""SMS channel — provider abstraction, opt-in gate, and task-assigned wiring.

Protects: SMS is off until a provider is configured; it is OPT-IN (mobile +
preference) so it never surprises a user or spends money uninvited; sends are
logged and failures recorded; and assigning a task texts an opted-in field
worker.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.administration.models import NotificationPreference
from apps.core.context import tenant_scope
from apps.identity.models import Company, User

from . import sms
from .models import EmailStatus, SmsLog

TWILIO = dict(SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="sid",
              TWILIO_AUTH_TOKEN="tok", TWILIO_FROM_NUMBER="+150000000",
              CELERY_TASK_ALWAYS_EAGER=True, SITE_URL="https://app.lulaworks.com")


class _FakeSms(sms.SmsProvider):
    name = "twilio"

    def __init__(self):
        self.sent = []

    def send(self, to, body):
        self.sent.append((to, body))
        return "SM-fake-id"


class ConfigTests(TestCase):
    @override_settings(SMS_PROVIDER="", TWILIO_ACCOUNT_SID="")
    def test_sms_off_without_a_provider(self):
        self.assertFalse(sms.sms_configured())

    @override_settings(**TWILIO)
    def test_sms_configured_with_twilio_keys(self):
        self.assertTrue(sms.sms_configured())


class OptInTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Co")

    def test_no_mobile_no_sms(self):
        u = User.objects.create_user("a@co.io", "x", active_company=self.company)
        NotificationPreference.objects.create(user=u, sms=True)   # opted in, but…
        self.assertFalse(sms.sms_allowed(u))                       # …no mobile

    def test_opt_in_required_even_with_mobile(self):
        u = User.objects.create_user("b@co.io", "x", active_company=self.company,
                                     mobile="+27610000000")
        # No prefs row → SMS stays off (opt-in default).
        self.assertFalse(sms.sms_allowed(u))
        NotificationPreference.objects.create(user=u, sms=True)
        self.assertTrue(sms.sms_allowed(u))


@override_settings(**TWILIO)
class SendTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Co")

    def test_send_logs_and_delivers(self):
        fake = _FakeSms()
        with patch("apps.notifications.sms.get_sms_provider", return_value=fake):
            log = sms.send_sms(to="+27611111111", body="New job today",
                               company=self.company)
        self.assertEqual(log.status, EmailStatus.SENT)
        self.assertEqual(log.provider_message_id, "SM-fake-id")
        self.assertEqual(fake.sent, [("+27611111111", "New job today")])

    def test_failed_send_is_recorded(self):
        class _Boom(sms.SmsProvider):
            def send(self, to, body):
                raise RuntimeError("carrier down")
        with patch("apps.notifications.sms.get_sms_provider", return_value=_Boom()):
            with self.assertRaises(Exception):
                sms.send_sms(to="+27612222222", body="x", company=self.company, now=True)
        log = SmsLog.objects.get(to_number="+27612222222")
        self.assertEqual(log.status, EmailStatus.FAILED)
        self.assertTrue(log.error)


@override_settings(**TWILIO)
class TaskAssignedSmsTests(TestCase):
    def test_assignment_texts_an_opted_in_worker(self):
        from apps.execution.models import Assignment, Task
        from apps.execution.services import add_member
        company = Company.objects.create(name="Co")
        worker = User.objects.create_user("field@co.io", "x", active_company=company,
                                          mobile="+27613333333")
        NotificationPreference.objects.create(user=worker, sms=True, email=False)
        fake = _FakeSms()
        with patch("apps.notifications.sms.get_sms_provider", return_value=fake), \
                tenant_scope(company.id):
            task = Task.objects.create(company=company, name="Replace seal")
            add_member(task, worker, Assignment.Role.EXECUTOR)
        self.assertEqual(SmsLog.objects.filter(company=company).count(), 1)
        self.assertIn("Replace seal", fake.sent[0][1])

    def test_worker_without_opt_in_is_not_texted(self):
        from apps.execution.models import Assignment, Task
        from apps.execution.services import add_member
        company = Company.objects.create(name="Co")
        worker = User.objects.create_user("f2@co.io", "x", active_company=company,
                                          mobile="+27614444444")
        # No prefs / not opted in.
        fake = _FakeSms()
        with patch("apps.notifications.sms.get_sms_provider", return_value=fake), \
                tenant_scope(company.id):
            task = Task.objects.create(company=company, name="X")
            add_member(task, worker, Assignment.Role.EXECUTOR)
        self.assertEqual(SmsLog.objects.count(), 0)
