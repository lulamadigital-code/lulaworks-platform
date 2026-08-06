"""Public marketing site tests: pages render, SEO endpoints, lead capture, and
the self-service trial registration that creates a company on the free trial."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.billing.services import GB
from apps.identity.models import Company, Membership, Role, User

from .models import ContactMessage, DemoRequest

PUBLIC_PAGES = [
    "home", "features", "pricing", "about", "contact", "demo",
    "demo_thanks", "faq", "trial", "privacy", "terms", "cookies",
]


class PublicPagesTests(TestCase):
    def setUp(self):
        # Pricing reads live plans; trial needs the Professional plan + owner role.
        Plan.objects.create(code="starter", name="Starter", tier=1, price=Decimal("299"),
                            annual_price=Decimal("2990"), max_users=2,
                            storage_quota_bytes=5 * GB, monthly_ai_credits=Decimal("300"))
        Plan.objects.create(code="professional", name="Professional", tier=2, is_popular=True,
                            price=Decimal("1299"), annual_price=Decimal("12990"), max_users=10,
                            storage_quota_bytes=50 * GB, monthly_ai_credits=Decimal("2000"))
        Role.objects.create(company=None, name="Company Owner", is_system=True)

    def test_all_public_pages_render(self):
        for name in PUBLIC_PAGES:
            resp = self.client.get(reverse(f"marketing:{name}"))
            self.assertEqual(resp.status_code, 200, f"{name} did not return 200")

    def test_home_has_hero_headline(self):
        resp = self.client.get(reverse("marketing:home"))
        self.assertContains(resp, "One Platform")
        self.assertContains(resp, "Start Free Trial")

    def test_pricing_lists_plans(self):
        resp = self.client.get(reverse("marketing:pricing"))
        self.assertContains(resp, "Professional")
        self.assertContains(resp, "R1299")

    def test_robots_and_sitemap(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Sitemap:", r.content.decode())
        s = self.client.get("/sitemap.xml")
        self.assertEqual(s.status_code, 200)
        self.assertIn("<urlset", s.content.decode())

    def test_contact_stores_message(self):
        self.client.post(reverse("marketing:contact"), {
            "name": "Sipho", "email": "s@co.za", "message": "Hello"})
        self.assertEqual(ContactMessage.objects.count(), 1)

    def test_demo_stores_request_and_redirects(self):
        resp = self.client.post(reverse("marketing:demo"), {
            "company": "Acme", "name": "Lerato", "email": "l@acme.co.za"})
        self.assertRedirects(resp, reverse("marketing:demo_thanks"))
        self.assertEqual(DemoRequest.objects.count(), 1)


class TrialRegistrationTests(TestCase):
    def setUp(self):
        Plan.objects.create(code="professional", name="Professional", tier=2,
                            price=Decimal("1299"), annual_price=Decimal("12990"), max_users=10,
                            storage_quota_bytes=50 * GB, monthly_ai_credits=Decimal("2000"))
        Role.objects.create(company=None, name="Company Owner", is_system=True)

    def test_signup_creates_company_on_trial_and_logs_in(self):
        resp = self.client.post(reverse("marketing:trial"), {
            "company": "Bright Contracting", "full_name": "Ada Owner",
            "email": "ada@bright.co.za", "password": "s3curePass!", "industry": "Construction",
        })
        self.assertRedirects(resp, reverse("web:dashboard"), fetch_redirect_response=False)
        company = Company.objects.get(name="Bright Contracting")
        user = User.objects.get(email="ada@bright.co.za")
        self.assertEqual(user.active_company_id, company.id)
        self.assertTrue(Membership.objects.filter(company=company, user=user, status="active").exists())
        sub = Subscription.objects.get(company=company)
        self.assertEqual(sub.status, SubscriptionStatus.TRIAL)
        # Logged in — session established.
        self.assertIn("_auth_user_id", self.client.session)

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user(email="dup@co.za", password="x")
        resp = self.client.post(reverse("marketing:trial"), {
            "company": "X", "full_name": "Y", "email": "dup@co.za", "password": "s3cure!!"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.assertEqual(Company.objects.count(), 0)

    def test_invalid_email_is_rejected(self):
        resp = self.client.post(reverse("marketing:trial"), {
            "company": "X", "full_name": "Y", "email": "not-an-email", "password": "s3curePass!"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "valid email")
        self.assertEqual(Company.objects.count(), 0)

    def test_weak_password_is_rejected(self):
        # Django's AUTH_PASSWORD_VALIDATORS must run server-side (not just client).
        resp = self.client.post(reverse("marketing:trial"), {
            "company": "X", "full_name": "Y", "email": "new@co.za", "password": "123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Company.objects.count(), 0)
        self.assertFalse(User.objects.filter(email="new@co.za").exists())

    def test_overlong_company_name_is_rejected(self):
        resp = self.client.post(reverse("marketing:trial"), {
            "company": "A" * 5000, "full_name": "Y", "email": "big@co.za",
            "password": "s3curePass!"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "too long")
        self.assertEqual(Company.objects.count(), 0)


class InputValidationTests(TestCase):
    """Public forms reject malformed / oversized anonymous input server-side."""

    def test_contact_rejects_invalid_email(self):
        self.client.post(reverse("marketing:contact"), {
            "name": "Sipho", "email": "nope", "message": "Hi"})
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_contact_rejects_missing_message(self):
        self.client.post(reverse("marketing:contact"), {
            "name": "Sipho", "email": "s@co.za", "message": ""})
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_demo_rejects_invalid_email(self):
        resp = self.client.post(reverse("marketing:demo"), {
            "company": "Acme", "name": "Lerato", "email": "bad"})
        self.assertRedirects(resp, reverse("marketing:demo"))
        self.assertEqual(DemoRequest.objects.count(), 0)


class ApiDocsAccessTests(TestCase):
    """The OpenAPI schema + Swagger UI expose the whole API surface — they must
    NOT be reachable by anonymous visitors."""

    def test_anonymous_schema_is_denied(self):
        r = self.client.get(reverse("schema"))
        self.assertIn(r.status_code, (401, 403))

    def test_anonymous_docs_is_denied(self):
        r = self.client.get(reverse("docs"))
        self.assertIn(r.status_code, (401, 403))

    def test_staff_can_read_schema(self):
        # DRF authenticates with JWT (not the session), so exercise the real path.
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import AccessToken
        staff = User.objects.create_superuser(email="root@lw.io", password="s3curePass!")
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(staff)}")
        r = api.get(reverse("schema"))
        self.assertEqual(r.status_code, 200)


class CurrencyDetectionTests(TestCase):
    def setUp(self):
        from apps.billing.models import PlanPrice
        Plan.objects.create(code="starter", name="Starter", tier=1, price=Decimal("299"),
                            annual_price=Decimal("2990"), max_users=2,
                            storage_quota_bytes=5 * GB, monthly_ai_credits=Decimal("300"))
        pro = Plan.objects.create(code="professional", name="Professional", tier=2,
                                  is_popular=True, price=Decimal("1299"),
                                  annual_price=Decimal("12990"), max_users=10,
                                  storage_quota_bytes=50 * GB, monthly_ai_credits=Decimal("2000"))
        for ccy, m, a in [("USD", 79, 790), ("GBP", 65, 650), ("EUR", 75, 750)]:
            PlanPrice.objects.create(plan=pro, currency=ccy, monthly=m, annual=a)
        Role.objects.create(company=None, name="Company Owner", is_system=True)

    def test_us_visitor_sees_usd_via_geo_header(self):
        r = self.client.get(reverse("marketing:pricing"), HTTP_CF_IPCOUNTRY="US")
        self.assertContains(r, "$79")

    def test_uk_visitor_sees_gbp_via_geo_header(self):
        r = self.client.get(reverse("marketing:pricing"), HTTP_CF_IPCOUNTRY="GB")
        self.assertContains(r, "£65")

    def test_browser_language_does_not_set_currency(self):
        # A SA visitor whose browser is set to en-GB must NOT be shown pounds —
        # currency comes from location, not language. No geo header → default.
        r = self.client.get(reverse("marketing:pricing"),
                            HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.9")
        self.assertContains(r, "R1299")   # ZAR default, not GBP
        self.assertNotContains(r, "£65")

    def test_unknown_location_falls_back_to_default(self):
        r = self.client.get(reverse("marketing:pricing"))
        self.assertContains(r, "R1299")   # ZAR base price

    def test_no_currency_selector_list_rendered(self):
        r = self.client.get(reverse("marketing:pricing"), HTTP_CF_IPCOUNTRY="US")
        # The old manual currency pills built links like ?cycle=...&currency=...
        self.assertNotContains(r, "&currency=")

    def test_signup_sets_company_currency_from_location(self):
        self.client.post(reverse("marketing:trial"), {
            "company": "Yankee Build", "full_name": "Sam Owner",
            "email": "sam@yankee.us", "password": "s3curePass!",
        }, HTTP_CF_IPCOUNTRY="US")
        company = Company.objects.get(name="Yankee Build")
        self.assertEqual(company.currency, "USD")
        self.assertEqual(company.subscription.currency, "USD")
