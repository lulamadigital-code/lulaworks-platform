"""Manager web tests: auth gate, dashboard renders, the Financial Golden Rule on
the HTML surface (money hidden from non-finance users), and tenant isolation."""

from datetime import date, timedelta

from django.test import TestCase

from apps.administration.models import NumberingRule
from apps.compliance.models import ComplianceRequirement
from apps.compliance.services import approve_item
from apps.core.context import tenant_scope
from apps.estimating.services import approve_estimate, create_estimate
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.projects.services import award_quotation
from apps.quotes.models import Quotation


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("estimate", "EST"),
                    ("invoice", "INV")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


def user_with(company, codenames, email="u@lulama.co.za"):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for code in codenames:
        p, _ = Permission.objects.get_or_create(codename=code,
                                                 defaults={"module": "x", "label": code})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


def awarded_project(company):
    q = Quotation.objects.create(company=company, number="QT-1", client_name="Sasol",
                                 site="Secunda")
    est = create_estimate(company, None, client_name="Sasol", work_type="pump_overhaul",
                          quotation=q, sections=[{"category": "labour",
                                                  "lines": [{"description": "Fitter", "qty": 100,
                                                             "unit": "hour", "unit_cost": 450}]}])
    approve_estimate(est, None)
    ComplianceRequirement.objects.create(company=company, code="SF", name="Safety File",
                                         category="documentation", source="customer",
                                         is_mandatory=True, applies_when={})
    project = award_quotation(company, None, quotation=q, work_type="pump_overhaul")
    for item in project.compliance_items.filter(is_mandatory=True):
        approve_item(item, None, expiry=date.today() + timedelta(days=365))
    project.refresh_from_db()
    return project


class AuthTests(TestCase):
    def test_dashboard_requires_login(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_login_and_dashboard(self):
        c = make_company()
        with tenant_scope(c.id):
            awarded_project(c)
        user = user_with(c, ["projects.view"])
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Operations dashboard")


class GoldenRuleTests(TestCase):
    def test_money_hidden_from_non_finance_user(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)
        broke = user_with(c, ["projects.view"], email="ops@lulama.co.za")
        rich = user_with(c, ["projects.view", "finance.view_money"], email="fin@lulama.co.za")

        # Dashboard: commercial panel only for the finance user.
        self.client.force_login(broke)
        self.assertNotContains(self.client.get("/"), "Portfolio margin")
        self.client.force_login(rich)
        self.assertContains(self.client.get("/"), "Portfolio margin")

        # Project detail: profitability only for the finance user.
        url = f"/projects/{project.id}/"
        self.client.force_login(broke)
        self.assertNotContains(self.client.get(url), "Gross profit")
        self.client.force_login(rich)
        detail = self.client.get(url)
        self.assertContains(detail, "Gross profit")
        self.assertContains(detail, "Profit forecast")


class TenantIsolationTests(TestCase):
    def test_cross_tenant_project_404(self):
        a = make_company("A")
        b = make_company("B")
        with tenant_scope(a.id):
            project = awarded_project(a)
        intruder = user_with(b, ["projects.view", "finance.view_money"], email="x@b.co.za")
        self.client.force_login(intruder)
        resp = self.client.get(f"/projects/{project.id}/")
        self.assertEqual(resp.status_code, 404)


class ReadinessPartialTests(TestCase):
    def test_htmx_readiness_partial_renders(self):
        c = make_company()
        with tenant_scope(c.id):
            project = awarded_project(c)
        user = user_with(c, ["projects.view"])
        self.client.force_login(user)
        resp = self.client.get(f"/projects/{project.id}/readiness/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ready for site")
