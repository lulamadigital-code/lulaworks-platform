"""Marketing services — evaluate segments and report over the CRM.

Everything here reads the live CRM (tenant-scoped by the request), so a segment
or a report always reflects the current state, never a frozen copy.
"""
from datetime import timedelta

from django.utils import timezone

from apps.customers.models import (
    LEAD_SOURCES,
    Customer,
    Lead,
    Opportunity,
    OpportunityStage,
)

from .models import Campaign, CampaignStatus, Segment


# ── Segments — a saved filter over the CRM ────────────────────────────────────

def segment_queryset(segment):
    """Evaluate a segment's criteria into a live CRM queryset (Lead or Customer).

    Unknown/blank criteria are ignored, so a segment with no criteria is simply
    "everyone" in that audience. Text filters are case-insensitive contains."""
    crit = segment.criteria or {}

    if segment.audience == "customers":
        qs = Customer.objects.all()
        if crit.get("status"):
            qs = qs.filter(status=crit["status"])
        if crit.get("customer_type"):
            qs = qs.filter(customer_type=crit["customer_type"])
        if crit.get("industry"):
            qs = qs.filter(industry__icontains=crit["industry"])
        if crit.get("country"):
            qs = qs.filter(country__icontains=crit["country"])
        if crit.get("city"):
            qs = qs.filter(city__icontains=crit["city"])
        if crit.get("no_activity_days"):
            cutoff = timezone.now() - timedelta(days=int(crit["no_activity_days"]))
            qs = qs.filter(updated_at__lt=cutoff)
        return qs

    # Leads
    qs = Lead.objects.all()
    if crit.get("status"):
        qs = qs.filter(status=crit["status"])
    if crit.get("source"):
        qs = qs.filter(source=crit["source"])
    if crit.get("industry"):
        qs = qs.filter(industry__icontains=crit["industry"])
    if crit.get("country"):
        qs = qs.filter(country__icontains=crit["country"])
    if crit.get("city"):
        qs = qs.filter(city__icontains=crit["city"])
    if crit.get("uncontacted"):
        qs = qs.filter(status=Lead.Status.NEW)
    if crit.get("no_contact_days"):
        cutoff = timezone.now() - timedelta(days=int(crit["no_contact_days"]))
        qs = qs.filter(created_at__lt=cutoff,
                       status__in=[Lead.Status.NEW, Lead.Status.CONTACTED])
    return qs


def segment_count(segment) -> int:
    return segment_queryset(segment).count()


# ── Lead-source performance — where business actually comes from ──────────────

def lead_source_performance() -> list[dict]:
    """Per source: how many leads, how many opportunities, and how many were won.

    Opportunities carry their own ``source`` (stamped from the originating lead),
    so the funnel is source-attributable end to end."""
    opp = Opportunity.objects.all()
    rows = []
    for src in LEAD_SOURCES + [""]:
        leads = Lead.objects.filter(source=src).count()
        opps = opp.filter(source=src).count()
        won = opp.filter(source=src, stage=OpportunityStage.WON).count()
        if leads or opps or won:
            rows.append({
                "source": src or "Unspecified",
                "leads": leads,
                "opportunities": opps,
                "won": won,
                "win_rate": round(100 * won / opps) if opps else 0,
            })
    rows.sort(key=lambda r: (-r["won"], -r["opportunities"], -r["leads"]))
    return rows


# ── Marketing overview — the owner's at-a-glance ──────────────────────────────

def marketing_overview() -> dict:
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0,
                                          microsecond=0)
    total_opps = Opportunity.objects.count()
    won = Opportunity.objects.filter(stage=OpportunityStage.WON).count()
    perf = lead_source_performance()
    return {
        "active_campaigns": Campaign.objects.filter(
            status__in=[CampaignStatus.SCHEDULED, CampaignStatus.RUNNING]).count(),
        "total_campaigns": Campaign.objects.count(),
        "segments": Segment.objects.count(),
        "leads_this_month": Lead.objects.filter(created_at__gte=month_start).count(),
        "qualified_leads": Lead.objects.filter(status=Lead.Status.QUALIFIED).count(),
        "open_opportunities": Opportunity.objects.exclude(
            stage__in=[OpportunityStage.WON, OpportunityStage.LOST]).count(),
        "won_opportunities": won,
        "conversion_rate": round(100 * won / total_opps) if total_opps else 0,
        "best_source": perf[0]["source"] if perf else "—",
        "source_performance": perf[:5],
    }
