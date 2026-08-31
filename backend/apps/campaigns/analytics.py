"""Marketing analytics & ROI (Phase 4).

The point of marketing is business, not messages sent — so this ties the funnel
back to money: revenue by lead source, email performance, and revenue attributed
to each campaign via its recipients (CampaignSend → the recipient's won
opportunities). No external services; everything reads the CRM + campaign data.
"""
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.customers.models import Lead, Opportunity, OpportunityStage

from .models import Campaign, CampaignChannel, CampaignSend, EmailSuppression

_ZERO = Decimal("0.00")


def _won_value(qs) -> Decimal:
    return qs.filter(stage=OpportunityStage.WON).aggregate(
        s=Sum("estimated_value"))["s"] or _ZERO


# ── The funnel (lead → qualified → opportunity → won → revenue) ───────────────

def marketing_funnel() -> dict:
    leads = Lead.objects.count()
    qualified = Lead.objects.filter(
        status__in=[Lead.Status.QUALIFIED, Lead.Status.CONVERTED]).count()
    opps = Opportunity.objects.count()
    won = Opportunity.objects.filter(stage=OpportunityStage.WON).count()
    revenue = _won_value(Opportunity.objects.all())
    return {
        "leads": leads, "qualified": qualified, "opportunities": opps, "won": won,
        "revenue": revenue,
        "lead_to_opp": round(100 * opps / leads) if leads else 0,
        "opp_to_won": round(100 * won / opps) if opps else 0,
    }


# ── Email performance across all email campaigns ─────────────────────────────

def email_performance() -> dict:
    camps = list(Campaign.objects.filter(channel=CampaignChannel.EMAIL))
    sent = sum(c.sent for c in camps)
    delivered = sum(c.delivered for c in camps)
    opened = sum(c.opened for c in camps)
    unsub = sum(c.unsubscribed for c in camps)
    return {
        "campaigns": len(camps),
        "sent": sent, "delivered": delivered, "opened": opened, "unsubscribed": unsub,
        "open_rate": round(100 * opened / sent) if sent else 0,
        "delivery_rate": round(100 * delivered / sent) if sent else 0,
        "suppressed": EmailSuppression.objects.count(),
    }


# ── Revenue by lead source ────────────────────────────────────────────────────

def source_roi() -> list[dict]:
    """Per source: leads, opportunities, won, and won revenue — where the money
    actually comes from, not just where the leads do."""
    from apps.customers.models import LEAD_SOURCES
    rows = []
    for src in LEAD_SOURCES + [""]:
        leads = Lead.objects.filter(source=src).count()
        src_opps = Opportunity.objects.filter(source=src)
        opps = src_opps.count()
        won = src_opps.filter(stage=OpportunityStage.WON).count()
        revenue = _won_value(src_opps)
        if leads or opps or won:
            rows.append({"source": src or "Unspecified", "leads": leads,
                         "opportunities": opps, "won": won, "revenue": revenue})
    rows.sort(key=lambda r: (-r["revenue"], -r["won"], -r["leads"]))
    return rows


# ── Per-campaign attribution ──────────────────────────────────────────────────

def campaign_attributed_revenue(campaign) -> dict:
    """Opportunities/revenue influenced by a campaign — the recipients it reached
    (CampaignSend → lead/customer) whose opportunities were created on/after the
    campaign went out. Won value is the attributed revenue; ROI uses campaign.cost.
    """
    sends = CampaignSend.objects.filter(campaign=campaign)
    lead_ids = [s.lead_id for s in sends if s.lead_id]
    cust_ids = [s.customer_id for s in sends if s.customer_id]
    # Recipient leads that later converted also carry their new customer.
    if lead_ids:
        cust_ids += list(Lead.objects.filter(id__in=lead_ids,
                                             converted_customer__isnull=False)
                         .values_list("converted_customer_id", flat=True))
    since = campaign.updated_at  # completed/sent time
    opp_qs = Opportunity.objects.filter(created_at__gte=since)
    from django.db.models import Q
    opp_qs = opp_qs.filter(Q(lead_id__in=lead_ids) | Q(customer_id__in=cust_ids))
    influenced = opp_qs.count()
    won = opp_qs.filter(stage=OpportunityStage.WON).count()
    revenue = _won_value(opp_qs)
    cost = campaign.cost or _ZERO
    roi = None
    if cost > 0:
        roi = round(float((revenue - cost) / cost) * 100)
    return {"influenced": influenced, "won": won, "revenue": revenue,
            "cost": cost, "roi": roi}


def campaign_performance() -> list[dict]:
    """Per-campaign row for the analytics table: engagement + attributed revenue."""
    rows = []
    for c in Campaign.objects.all()[:100]:
        attr = campaign_attributed_revenue(c)
        rows.append({
            "campaign": c,
            "sent": c.sent, "opened": c.opened,
            "open_rate": round(100 * c.opened / c.sent) if c.sent else 0,
            "influenced": attr["influenced"], "won": attr["won"],
            "revenue": attr["revenue"], "roi": attr["roi"],
        })
    rows.sort(key=lambda r: (-r["revenue"], -r["sent"]))
    return rows


def marketing_analytics() -> dict:
    return {
        "funnel": marketing_funnel(),
        "email": email_performance(),
        "sources": source_roi(),
        "campaigns": campaign_performance(),
    }
