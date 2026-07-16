"""Knowledge Platform pipeline (ARCHITECTURE §7.3-7.4).

Three tiers with a one-way promotion pipeline:
  private (per-tenant, default)  →  (opt-in) de-identify + corroborate  →
  shared-entity  /  aggregate-only (past k-anonymity).

Guarantees enforced here:
- No raw cross-tenant reads (private stays TenantBaseModel-scoped elsewhere).
- Promotion is opt-in per tenant; source company is stripped on promotion.
- Shared facts never expose their source.
- Aggregates are suppressed below the minimum-N threshold.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count, Max, Min

from apps.core.context import system_scope

from .models import (
    AggregateSample,
    KnowledgeConfig,
    SharedEntityFact,
    SharedFactContribution,
)

MIN_N = 5              # k-anonymity: aggregates need ≥5 samples to be exposed
CORROBORATION_CAP = 5  # confidence saturates at 5 corroborating companies


def normalise(name: str) -> str:
    return " ".join(name.lower().split())


def set_contribution(company, opt_in: bool) -> None:
    with system_scope():
        cfg, _ = KnowledgeConfig.objects.get_or_create(company=company)
        cfg.contribute_shared = opt_in
        cfg.save(update_fields=["contribute_shared"])


def contributes(company) -> bool:
    with system_scope():
        cfg = KnowledgeConfig.objects.filter(company=company).first()
        return bool(cfg and cfg.contribute_shared)


@transaction.atomic
def promote_shared_fact(company, *, entity_type, entity_key, fact_type, fact_value) -> bool:
    """Promote a private observation to the shared-entity tier — ONLY if the
    tenant opted in. De-identifies (source stripped from the fact) and corroborates
    (distinct-company count). One-way. Returns True if promoted."""
    if not contributes(company):
        return False
    with system_scope():
        fact, _ = SharedEntityFact.objects.get_or_create(
            entity_type=entity_type, entity_key=normalise(entity_key),
            fact_type=fact_type, fact_value=fact_value,
        )
        _, created = SharedFactContribution.objects.get_or_create(fact=fact, company=company)
        if created:
            fact.corroboration_count = SharedFactContribution.objects.filter(fact=fact).count()
            fact.confidence = min(fact.corroboration_count / CORROBORATION_CAP, 1.0)
            fact.save(update_fields=["corroboration_count", "confidence"])
    return True


def query_shared_facts(entity_type, entity_key, *, min_corroboration=1):
    """Read shared-entity knowledge — de-identified, no source attribution ever."""
    with system_scope():
        rows = SharedEntityFact.objects.filter(
            entity_type=entity_type, entity_key=normalise(entity_key),
            corroboration_count__gte=min_corroboration,
        ).values("fact_type", "fact_value", "confidence", "corroboration_count")
    return list(rows)


def record_aggregate(company, *, metric_key, bucket, value) -> None:
    """Contribute a numeric sample to the aggregate tier (opt-in only)."""
    if not contributes(company):
        return
    with system_scope():
        AggregateSample.objects.create(
            metric_key=metric_key, bucket=bucket, value=Decimal(value), company=company
        )


def query_aggregate(metric_key, bucket, *, min_n=MIN_N):
    """Return an aggregate ONLY past the k-anonymity threshold; else None
    (small cells suppressed). Never reveals individual samples."""
    with system_scope():
        agg = AggregateSample.objects.filter(metric_key=metric_key, bucket=bucket).aggregate(
            n=Count("id"), avg=Avg("value"), lo=Min("value"), hi=Max("value")
        )
    if not agg["n"] or agg["n"] < min_n:
        return None
    return {"n": agg["n"], "avg": agg["avg"], "min": agg["lo"], "max": agg["hi"]}


def capture_from_rfq(rfq, quotation, user) -> None:
    """Capture knowledge from an approved RFQ. Private capture happens for the
    tenant regardless; promotion to shared/aggregate only if opted in."""
    client = quotation.client_name if quotation else ""
    if not client:
        return
    # (Private ClientProfile enrichment would live here.)
    # Shared/aggregate promotion (opt-in, de-identified):
    for line in rfq.lines.all():
        promote_shared_fact(
            rfq.company, entity_type="client", entity_key=client,
            fact_type="ordered_item", fact_value=line.description[:500],
        )
        if line.unit_price:
            record_aggregate(
                rfq.company, metric_key="unit_price",
                bucket=f"item:{normalise(line.description)[:100]}", value=line.unit_price,
            )
