"""Support SLA engine — tier targets, breach/on-time snapshots, routing order."""
from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.core.context import system_scope
from apps.support import sla
from apps.support.models import TicketStatus


def _ticket(*, priority="normal", status=TicketStatus.OPEN, age_hours=0,
            first_response_after=None, resolved_after=None, company_id="c1"):
    now = timezone.now()
    created = now - timedelta(hours=age_hours)
    return SimpleNamespace(
        priority=priority, status=status, created_at=created, company=None,
        company_id=company_id,
        first_response_at=(created + timedelta(hours=first_response_after))
        if first_response_after is not None else None,
        resolved_at=(created + timedelta(hours=resolved_after))
        if resolved_after is not None else None,
    )


class SLAPureTests(SimpleTestCase):
    def test_normalize_tier(self):
        self.assertEqual(sla.normalize_tier("standard"), "email")
        self.assertEqual(sla.normalize_tier(""), "email")
        self.assertEqual(sla.normalize_tier(None), "email")
        self.assertEqual(sla.normalize_tier("DEDICATED"), "dedicated")
        self.assertEqual(sla.normalize_tier("nonsense"), "email")

    def test_targets_scale_by_tier(self):
        self.assertEqual(sla.response_target_hours("dedicated", "urgent"), 1)
        self.assertEqual(sla.response_target_hours("email", "urgent"), 8)
        self.assertEqual(sla.response_target_hours("email", "low"), 72)
        # Dedicated is always at least as fast as entry tier.
        for p in ("urgent", "high", "normal", "low"):
            self.assertLessEqual(sla.response_target_hours("dedicated", p),
                                 sla.response_target_hours("email", p))

    def test_on_time_response(self):
        # dedicated/urgent target 1h; responded in 0.5h.
        s = sla.snapshot(_ticket(priority="urgent", first_response_after=0.5),
                         tier="dedicated")
        self.assertTrue(s.response_met)
        self.assertTrue(s.response_on_time)
        self.assertFalse(s.response_breached)
        self.assertFalse(s.breached)

    def test_breached_when_open_past_target(self):
        # dedicated/urgent target 1h; open, no response, 10h old.
        s = sla.snapshot(_ticket(priority="urgent", age_hours=10), tier="dedicated")
        self.assertFalse(s.response_met)
        self.assertTrue(s.response_breached)
        self.assertTrue(s.breached)
        self.assertLess(s.hours_to_response_due, 0)   # overdue
        self.assertEqual(s.badge, "breached")

    def test_response_met_but_late(self):
        # target 1h, responded at 5h → met but not on time.
        s = sla.snapshot(_ticket(priority="urgent", age_hours=6,
                                 first_response_after=5), tier="dedicated")
        self.assertTrue(s.response_met)
        self.assertFalse(s.response_on_time)
        self.assertFalse(s.response_breached)   # a response exists

    def test_resolution_breach(self):
        # dedicated normal: response 4h, resolution 4*3=12h. Open, 20h old,
        # responded early so only resolution breaches.
        s = sla.snapshot(_ticket(priority="normal", age_hours=20,
                                 first_response_after=1), tier="dedicated")
        self.assertFalse(s.response_breached)
        self.assertTrue(s.resolution_breached)
        self.assertTrue(s.breached)

    def test_closed_ticket_never_breaches(self):
        s = sla.snapshot(_ticket(priority="urgent", age_hours=100,
                                 status=TicketStatus.CLOSED), tier="dedicated")
        self.assertFalse(s.breached)
        self.assertFalse(s.is_open)
        self.assertEqual(s.badge, "closed")

    def test_route_order_breached_first_then_closed_last(self):
        breached = _ticket(priority="urgent", age_hours=10)
        ontrack = _ticket(priority="low", age_hours=0)
        closed = _ticket(priority="urgent", age_hours=100, status=TicketStatus.CLOSED)
        items = [ontrack, closed, breached]
        for t in items:
            t.sla = sla.snapshot(t, tier="dedicated")
        items.sort(key=lambda t: sla.route_key(t, t.sla))
        self.assertIs(items[0], breached)   # breached open first
        self.assertIs(items[1], ontrack)    # then on-track open
        self.assertIs(items[2], closed)     # closed last


class SLADBTests(TestCase):
    def test_company_tier_and_map(self):
        from apps.billing.models import Plan, Subscription, SubscriptionStatus
        from apps.identity.models import Company
        with system_scope():
            plan = Plan.objects.create(code="ent_sla", name="Ent", tier=4, price=0,
                                       annual_price=0, support_level="dedicated")
            co = Company.objects.create(name="Acme SLA", currency="ZAR")
            Subscription.objects.create(company=co, plan=plan, currency="ZAR",
                                        status=SubscriptionStatus.ACTIVE)
            co.refresh_from_db()
            self.assertEqual(sla.company_tier(co), "dedicated")
            self.assertEqual(sla.tier_map([co]), {co.id: "dedicated"})
            # A company with no subscription falls back to the entry tier.
            bare = Company.objects.create(name="Bare", currency="ZAR")
            self.assertEqual(sla.company_tier(bare), "email")
