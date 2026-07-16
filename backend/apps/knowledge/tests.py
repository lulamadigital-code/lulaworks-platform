"""Knowledge Platform confidentiality tests — the moat's guarantees:
private stays private, promotion is opt-in + de-identified, aggregates are
k-anonymised."""

from decimal import Decimal

from django.test import TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company

from .models import ClientProfile, SharedEntityFact, SharedFactContribution
from .services import (
    MIN_N,
    contributes,
    promote_shared_fact,
    query_aggregate,
    query_shared_facts,
    record_aggregate,
    set_contribution,
)


class PrivateTierTests(TestCase):
    def test_client_profile_is_tenant_isolated(self):
        a = Company.objects.create(name="Contractor A")
        b = Company.objects.create(name="Contractor B")
        with tenant_scope(a.id):
            ClientProfile.objects.create(company=a, name="Sibanye")
        with tenant_scope(b.id):
            self.assertEqual(ClientProfile.objects.count(), 0)  # B never sees A's private data
        with tenant_scope(a.id):
            self.assertEqual(ClientProfile.objects.count(), 1)


class OptInPromotionTests(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="Contractor A")
        self.b = Company.objects.create(name="Contractor B")

    def test_default_is_private_no_promotion(self):
        self.assertFalse(contributes(self.a))
        promoted = promote_shared_fact(
            self.a, entity_type="mine", entity_key="Mine Alpha",
            fact_type="required_induction", fact_value="Working at Heights",
        )
        self.assertFalse(promoted)
        self.assertEqual(SharedEntityFact.objects.count(), 0)  # nothing promoted

    def test_opt_in_promotes_and_strips_source(self):
        set_contribution(self.a, True)
        self.assertTrue(promote_shared_fact(
            self.a, entity_type="mine", entity_key="Mine Alpha",
            fact_type="required_induction", fact_value="Working at Heights",
        ))
        facts = query_shared_facts("mine", "mine alpha")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_value"], "Working at Heights")
        # the shared read exposes NO source company
        self.assertNotIn("company", facts[0])
        self.assertNotIn("source", facts[0])

    def test_corroboration_across_companies(self):
        set_contribution(self.a, True)
        set_contribution(self.b, True)
        for co in (self.a, self.b):
            promote_shared_fact(co, entity_type="mine", entity_key="Mine Alpha",
                                fact_type="required_induction", fact_value="Medical")
        fact = SharedEntityFact.objects.get()
        self.assertEqual(fact.corroboration_count, 2)  # two distinct companies
        self.assertGreater(fact.confidence, 0)
        # same company twice does not double-count
        promote_shared_fact(self.a, entity_type="mine", entity_key="Mine Alpha",
                            fact_type="required_induction", fact_value="Medical")
        fact.refresh_from_db()
        self.assertEqual(fact.corroboration_count, 2)
        self.assertEqual(SharedFactContribution.objects.filter(fact=fact).count(), 2)

    def test_opt_out_after_never_leaks_new(self):
        set_contribution(self.a, False)
        promote_shared_fact(self.a, entity_type="client", entity_key="X",
                            fact_type="t", fact_value="v")
        self.assertEqual(SharedEntityFact.objects.count(), 0)


class AggregateKAnonymityTests(TestCase):
    def test_aggregate_suppressed_below_min_n(self):
        companies = [Company.objects.create(name=f"C{i}") for i in range(MIN_N + 1)]
        for c in companies:
            set_contribution(c, True)
        # fewer than MIN_N samples → suppressed
        for c in companies[: MIN_N - 1]:
            record_aggregate(c, metric_key="labour_hours",
                             bucket="work_type:pump", value=Decimal("8"))
        self.assertIsNone(query_aggregate("labour_hours", "work_type:pump"))
        # reaching MIN_N → exposed as an aggregate only
        record_aggregate(companies[MIN_N - 1], metric_key="labour_hours",
                         bucket="work_type:pump", value=Decimal("10"))
        agg = query_aggregate("labour_hours", "work_type:pump")
        self.assertIsNotNone(agg)
        self.assertEqual(agg["n"], MIN_N)
        self.assertEqual(agg["min"], Decimal("8"))
        self.assertEqual(agg["max"], Decimal("10"))

    def test_opt_out_not_sampled(self):
        c = Company.objects.create(name="Private Co")
        set_contribution(c, False)
        record_aggregate(c, metric_key="m", bucket="b", value=Decimal("5"))
        self.assertIsNone(query_aggregate("m", "b", min_n=1))  # no sample recorded
