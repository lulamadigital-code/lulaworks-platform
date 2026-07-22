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


class DocumentIntelligenceTests(TestCase):
    """The one shared extraction service the RFQ and Quotation modules both use.

    It reuses apps.rfq.extraction for the actual parsing; these tests pin the
    parts this module adds — reading text out of several file shapes, mapping
    parsed lines to plain item dicts, and suggesting related items.
    """

    def test_items_come_out_of_prose(self):
        from apps.knowledge.document_intelligence import extract_items

        text = "20 conveyor rollers\n40 bearings\n2 days installation labour"
        items = extract_items(text)
        descs = [i["description"].lower() for i in items]
        self.assertIn("conveyor rollers", descs)
        self.assertIn("bearings", descs)
        # A price is never invented — blank unless the text stated one.
        self.assertTrue(all(i["unit_price"] == "" for i in items))

    def test_type_key_defaults_the_unit(self):
        from apps.knowledge.document_intelligence import extract_items

        # "5 fitters" has no unit word; a labour-hire quote prices in hours.
        items = extract_items("5 fitters", type_key="labour_hire")
        self.assertEqual(items[0]["unit"], "hour")

    def test_related_items_are_suggested_and_exclude_what_you_have(self):
        from apps.knowledge.document_intelligence import suggest_related_items

        s = suggest_related_items("Supply and install conveyor rollers",
                                  existing=["bearings"])
        self.assertIn("Installation labour", s)
        self.assertNotIn("Bearings", s)        # already on the quotation

    def test_reads_text_files_and_zips(self):
        import io
        import zipfile

        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.knowledge.document_intelligence import (
            extract_text_from_upload,
        )

        txt = SimpleUploadedFile("scope.txt", b"10 gaskets\n2 pumps")
        self.assertIn("gaskets", extract_text_from_upload(txt))

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", "5 valves")
            zf.writestr("b.txt", "3 flanges")
        z = SimpleUploadedFile("pack.zip", buf.getvalue())
        body = extract_text_from_upload(z)
        self.assertIn("valves", body)
        self.assertIn("flanges", body)

    def test_a_broken_file_yields_no_text_not_an_error(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.knowledge.document_intelligence import extract_text_from_upload

        junk = SimpleUploadedFile("broken.pdf", b"\x00\x01not a pdf")
        self.assertEqual(extract_text_from_upload(junk), "")
