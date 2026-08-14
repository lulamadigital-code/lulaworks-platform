"""Email & Notification platform — the core service.

Guarantees protected: every send is logged and rendered branded; delivery is
recorded and retriable; failures are captured not raised at the call site of the
notification service; and the channel dispatch honours user preferences and
login access.
"""

from django.core import mail
from django.test import TestCase, override_settings

from apps.identity.models import Company, User

from . import service
from .dispatch import notify
from .models import EmailCategory, EmailLog, EmailStatus

EAGER = override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CELERY_TASK_ALWAYS_EAGER=True)


@EAGER
class SendEmailTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Bright Contracting")

    def test_send_logs_and_delivers(self):
        log = service.send_email(
            to="sam@client.co.za", subject="Welcome to LulaWorks",
            template="generic", context={"heading": "Welcome", "body": "You're in."},
            company=self.company, category=EmailCategory.ACCOUNT)
        self.assertEqual(log.status, EmailStatus.SENT)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["sam@client.co.za"])
        self.assertIn("Welcome", msg.subject)
        # The company name (branding) is in the HTML alternative.
        html = msg.alternatives[0][0]
        self.assertIn("Bright Contracting", html)

    def test_body_is_branded_and_recorded(self):
        log = service.send_email(
            to="a@b.co", subject="Hi", context={"heading": "H", "body": "B"},
            company=self.company)
        self.assertIn("Bright Contracting", log.html_body)   # footer branding
        self.assertIn("B", log.text_body)                    # plain-text version
        self.assertEqual(log.provider, "django.core.mail.backends.locmem.EmailBackend")

    def test_platform_email_without_company(self):
        # Auth emails have no tenant — must still send + log.
        log = service.send_email(to="reset@x.co", subject="Reset", context={"body": "x"})
        self.assertEqual(log.status, EmailStatus.SENT)
        self.assertIsNone(log.company_id)

    def test_tenant_mail_replies_to_the_tenant(self):
        # "Sent on behalf of": platform address sends, but a reply reaches the
        # tenant — so a customer answering Lulama's invoice reaches Lulama.
        tenant = Company.objects.create(name="Lulama", email="info@lulama.co.za")
        service.send_email(to="client@example.com", subject="Invoice",
                           template="generic", context={"body": "See attached."},
                           company=tenant)
        msg = mail.outbox[-1]
        self.assertEqual(msg.reply_to, ["info@lulama.co.za"])   # replies → tenant
        self.assertIn("Lulama", msg.from_email)                 # branded From name

    def test_explicit_reply_to_wins(self):
        tenant = Company.objects.create(name="Lulama", email="info@lulama.co.za")
        service.send_email(to="c@example.com", subject="Q", template="generic",
                           context={"body": "x"}, company=tenant,
                           reply_to="sales@lulama.co.za")
        self.assertEqual(mail.outbox[-1].reply_to, ["sales@lulama.co.za"])

    def test_no_reply_to_when_tenant_has_no_email(self):
        tenant = Company.objects.create(name="NoEmail Co")   # email blank
        service.send_email(to="c@example.com", subject="Q", template="generic",
                           context={"body": "x"}, company=tenant)
        self.assertEqual(mail.outbox[-1].reply_to, [])

    def test_failed_send_is_recorded(self):
        with override_settings(
                EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                EMAIL_HOST="256.256.256.256", EMAIL_PORT=2, EMAIL_TIMEOUT=1):
            with self.assertRaises(Exception):
                service.send_email(to="x@y.co", subject="S", context={"body": "b"},
                                   company=self.company, now=True)
        log = EmailLog.objects.filter(to_email="x@y.co").first()
        self.assertEqual(log.status, EmailStatus.FAILED)
        self.assertTrue(log.error)

    def test_resend_creates_a_new_log(self):
        log = service.send_email(to="a@b.co", subject="Hi", context={"body": "B"},
                                 company=self.company)
        again = service.resend(log)
        self.assertNotEqual(log.id, again.id)
        self.assertEqual(EmailLog.objects.filter(to_email="a@b.co").count(), 2)


@EAGER
class DispatchTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Co")

    def test_notify_writes_inapp_and_email_for_a_user(self):
        user = User.objects.create_user("worker@co.za", "x", active_company=self.company)
        res = notify(self.company, user, title="Task assigned",
                     body="Replace the seal", category=EmailCategory.TASK)
        self.assertIsNotNone(res["email"])
        self.assertEqual(res["email"].status, EmailStatus.SENT)
        self.assertEqual(len(mail.outbox), 1)
        # And the in-app inbox got it too (tenant bound explicitly by dispatch).
        self.assertIsNotNone(res["notification"])

    def test_no_email_without_an_address(self):
        # A user with no email (e.g. field-only employee) is never emailed.
        user = User.objects.create_user("noaddr@co.za", "x", active_company=self.company)
        user.email = ""
        user.save(update_fields=["email"])
        res = notify(self.company, user, title="X", category=EmailCategory.TASK)
        self.assertIsNone(res["email"])

    def test_opt_out_suppresses_email(self):
        from apps.administration.models import NotificationPreference
        user = User.objects.create_user("optout@co.za", "x", active_company=self.company)
        NotificationPreference.objects.create(user=user, email=False)
        res = notify(self.company, user, title="X", category=EmailCategory.TASK)
        self.assertIsNone(res["email"])
        self.assertEqual(len(mail.outbox), 0)
