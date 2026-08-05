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
