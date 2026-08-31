"""Marketing (Phase 1) tests: segments evaluate over the CRM, lead-source
performance aggregates the funnel, and the web pages render + create."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.customers.models import (
    Customer,
    Lead,
    Opportunity,
    OpportunityStage,
)
from apps.identity.models import Company, Membership, Permission, Role, User


def _company(name="Lula Marketing"):
    return Company.objects.create(name=name)


def _user_with(company, codenames, email="mkt@lula.co.za"):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for code in codenames:
        p, _ = Permission.objects.get_or_create(
            codename=code, defaults={"module": "x", "label": code})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class SegmentAndSourceTests(APITestCase):
    def test_segment_membership_and_source_report(self):
        from apps.campaigns.models import Segment
        from apps.campaigns.services import lead_source_performance, segment_count

        c = _company()
        with tenant_scope(c.id):
            Lead.objects.create(company=c, company_name="A", source="Website",
                                status=Lead.Status.NEW, industry="Mining")
            Lead.objects.create(company=c, company_name="B", source="Website",
                                status=Lead.Status.QUALIFIED, industry="Mining")
            Lead.objects.create(company=c, company_name="D", source="Referral",
                                status=Lead.Status.NEW, industry="Retail")

            mining = Segment.objects.create(company=c, name="Mining leads",
                                            audience="leads", criteria={"industry": "Mining"})
            self.assertEqual(segment_count(mining), 2)

            new_web = Segment.objects.create(company=c, name="Website — never contacted",
                                             audience="leads",
                                             criteria={"source": "Website", "uncontacted": True})
            self.assertEqual(segment_count(new_web), 1)   # only A (B is qualified)

            cust = Customer.objects.create(company=c, name="Cust")
            Opportunity.objects.create(company=c, customer=cust, title="O1",
                                       source="Website", stage=OpportunityStage.WON)
            Opportunity.objects.create(company=c, customer=cust, title="O2",
                                       source="Website", stage=OpportunityStage.LEAD)
            rows = {r["source"]: r for r in lead_source_performance()}
        self.assertEqual(rows["Website"]["leads"], 2)
        self.assertEqual(rows["Website"]["opportunities"], 2)
        self.assertEqual(rows["Website"]["won"], 1)
        self.assertEqual(rows["Website"]["win_rate"], 50)


class MarketingWebTests(APITestCase):
    def test_pages_render_and_can_create(self):
        from apps.campaigns.models import Campaign, Segment

        c = _company()
        u = _user_with(c, ["customers.manage"])
        self.client.force_login(u)
        for path in ("/marketing/", "/marketing/campaigns/", "/marketing/campaigns/new/",
                     "/marketing/segments/", "/marketing/segments/new/",
                     "/marketing/lead-sources/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

        self.client.post("/marketing/segments/new/",
                         {"name": "Mining", "audience": "leads", "industry": "Mining"})
        self.client.post("/marketing/campaigns/new/", {"name": "Q4", "channel": "email"})
        with tenant_scope(c.id):
            self.assertTrue(Segment.objects.filter(name="Mining").exists())
            self.assertTrue(Campaign.objects.filter(name="Q4").exists())

    def test_gated_without_permission(self):
        c = _company()
        u = _user_with(c, ["projects.view"], email="np@lula.co.za")
        self.client.force_login(u)
        self.assertEqual(self.client.get("/marketing/").status_code, 302)  # bounced
