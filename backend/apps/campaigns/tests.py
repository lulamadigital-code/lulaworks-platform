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


class EmailCampaignTests(APITestCase):
    """Send over the notifications pipe: per-recipient records + EmailLogs,
    unsubscribe suppression skips recipients, and the public unsubscribe/open
    endpoints work with no tenant in context."""

    def _setup(self):
        from apps.campaigns.models import Campaign, Segment
        c = _company()
        with tenant_scope(c.id):
            for i in range(3):
                Lead.objects.create(company=c, company_name=f"L{i}",
                                    contact_name=f"Sam {i}",
                                    email=f"lead{i}@example.com", source="Website",
                                    status=Lead.Status.NEW, industry="Mining")
            seg = Segment.objects.create(company=c, name="Mining", audience="leads",
                                         criteria={"industry": "Mining"})
            camp = Campaign.objects.create(company=c, name="Q4", channel="email",
                                           segment=seg, subject="Hi",
                                           content="Hello {{first_name}}")
        return c, seg, camp

    def test_send_creates_sends_and_logs(self):
        from apps.campaigns.email import send_campaign
        from apps.campaigns.models import CampaignSend, CampaignStatus
        c, seg, camp = self._setup()
        with tenant_scope(c.id):
            user = _user_with(c, ["customers.manage"], email="mgr@lula.co.za")
            res = send_campaign(camp, user, base_url="https://x.test")
            self.assertEqual(res["sent"], 3)
            self.assertEqual(CampaignSend.objects.filter(campaign=camp).count(), 3)
            self.assertEqual(CampaignSend.objects.filter(
                campaign=camp, email_log__isnull=False).count(), 3)
            camp.refresh_from_db()
            self.assertEqual(camp.sent, 3)
            self.assertEqual(camp.status, CampaignStatus.COMPLETED)

    def test_suppressed_recipient_is_skipped(self):
        from apps.campaigns.email import send_campaign, suppress
        from apps.campaigns.models import CampaignSend
        c, seg, camp = self._setup()
        with tenant_scope(c.id):
            user = _user_with(c, ["customers.manage"], email="m2@lula.co.za")
            suppress(c, "lead1@example.com")
            res = send_campaign(camp, user, base_url="https://x.test")
            self.assertEqual(res["sent"], 2)
            self.assertEqual(res["skipped"], 1)
            skipped = CampaignSend.objects.get(campaign=camp, email="lead1@example.com")
            self.assertEqual(skipped.status, CampaignSend.Status.SKIPPED)

    def test_public_unsubscribe_suppresses(self):
        from apps.campaigns.email import send_campaign
        from apps.campaigns.models import CampaignSend, EmailSuppression
        c, seg, camp = self._setup()
        with tenant_scope(c.id):
            user = _user_with(c, ["customers.manage"], email="m3@lula.co.za")
            send_campaign(camp, user, base_url="https://x.test")
            cs_id = CampaignSend.objects.filter(campaign=camp).first().id
            cs_email = CampaignSend.objects.get(id=cs_id).email
        resp = self.client.get(f"/m/u/{cs_id}/")           # public, no login
        self.assertEqual(resp.status_code, 200)
        with tenant_scope(c.id):
            self.assertTrue(EmailSuppression.objects.filter(company=c, email=cs_email).exists())

    def test_open_pixel_marks_opened(self):
        from apps.campaigns.email import send_campaign
        from apps.campaigns.models import CampaignSend
        c, seg, camp = self._setup()
        with tenant_scope(c.id):
            user = _user_with(c, ["customers.manage"], email="m4@lula.co.za")
            send_campaign(camp, user, base_url="https://x.test")
            cs_id = CampaignSend.objects.filter(campaign=camp).first().id
        resp = self.client.get(f"/m/o/{cs_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/gif")
        with tenant_scope(c.id):
            self.assertTrue(CampaignSend.objects.get(id=cs_id).opened)


class MarketingAnalyticsTests(APITestCase):
    """Revenue attribution: a campaign's recipients' won opportunities (created
    after it was sent) count as its revenue; ROI uses the campaign cost."""

    def test_revenue_and_roi_attributed_to_campaign(self):
        from decimal import Decimal

        from apps.campaigns.analytics import campaign_attributed_revenue, source_roi
        from apps.campaigns.models import Campaign, CampaignSend, Segment

        c = _company()
        with tenant_scope(c.id):
            lead = Lead.objects.create(company=c, company_name="Mine Co",
                                       email="buyer@mine.co", source="Website",
                                       status=Lead.Status.NEW, industry="Mining")
            seg = Segment.objects.create(company=c, name="Mining", audience="leads",
                                         criteria={"industry": "Mining"})
            camp = Campaign.objects.create(company=c, name="Q4", channel="email",
                                           segment=seg, subject="Hi",
                                           cost=Decimal("1000"), status="completed")
            CampaignSend.objects.create(company=c, campaign=camp, email="buyer@mine.co",
                                        lead=lead, status=CampaignSend.Status.SENT)
            camp.refresh_from_db()
            cust = Customer.objects.create(company=c, name="Mine Co")
            Opportunity.objects.create(company=c, customer=cust, lead=lead, title="Deal",
                                       source="Website", stage=OpportunityStage.WON,
                                       estimated_value=Decimal("50000"))
            attr = campaign_attributed_revenue(camp)
            src = {r["source"]: r for r in source_roi()}
        self.assertEqual(attr["won"], 1)
        self.assertEqual(attr["revenue"], Decimal("50000"))
        self.assertEqual(attr["roi"], 4900)              # (50000-1000)/1000 → 4900%
        self.assertEqual(src["Website"]["revenue"], Decimal("50000"))

    def test_analytics_page_renders(self):
        c = _company()
        u = _user_with(c, ["customers.manage"], email="an@lula.co.za")
        self.client.force_login(u)
        self.assertEqual(self.client.get("/marketing/analytics/").status_code, 200)


class WhatsAppCampaignTests(APITestCase):
    """WhatsApp sends from the tenant's own connected number; gated on a
    connection; the Meta HTTP call is mocked (no live call in tests)."""

    def _setup(self, connected=True):
        from apps.campaigns.models import Campaign, Segment, WhatsAppConnection
        c = _company()
        with tenant_scope(c.id):
            for i in range(2):
                Lead.objects.create(company=c, company_name=f"W{i}",
                                    contact_name=f"Sam {i}", mobile=f"+2761000000{i}",
                                    source="Website", status=Lead.Status.NEW,
                                    industry="Mining")
            seg = Segment.objects.create(company=c, name="Mining", audience="leads",
                                         criteria={"industry": "Mining"})
            camp = Campaign.objects.create(company=c, name="WA Q4", channel="whatsapp",
                                           segment=seg, content="Hi {{first_name}}")
            if connected:
                WhatsAppConnection.objects.create(company=c, phone_number_id="123",
                                                  access_token="tok", is_active=True)
        return c, camp

    def test_send_requires_a_connection(self):
        from apps.campaigns.whatsapp import send_whatsapp_campaign
        c, camp = self._setup(connected=False)
        with tenant_scope(c.id):
            user = _user_with(c, ["customers.manage"], email="w0@lula.co.za")
            with self.assertRaises(ValueError):
                send_whatsapp_campaign(camp, user)

    def test_send_creates_whatsapp_sends(self):
        from unittest.mock import patch

        from apps.campaigns.models import CampaignSend
        from apps.campaigns.whatsapp import send_whatsapp_campaign
        c, camp = self._setup(connected=True)
        with tenant_scope(c.id):
            user = _user_with(c, ["customers.manage"], email="w1@lula.co.za")
            with patch("apps.campaigns.whatsapp._post_message", return_value="wamid.X"):
                res = send_whatsapp_campaign(camp, user)
            self.assertEqual(res["sent"], 2)
            sends = CampaignSend.objects.filter(campaign=camp, channel="whatsapp")
            self.assertEqual(sends.count(), 2)
            self.assertTrue(all(s.wa_message_id == "wamid.X" for s in sends))
            camp.refresh_from_db()
            self.assertEqual(camp.sent, 2)
