"""Quotation Management Engine.

These protect money and the audit trail: VAT arithmetic in both directions,
margin that refuses to flatter itself, a lifecycle that cannot be edited after
award, and revisions that supersede rather than overwrite.
"""

from decimal import Decimal

from django.test import TestCase

from apps.administration.models import NumberingRule
from apps.core.context import tenant_scope
from apps.identity.models import Company

from .models import (
    Quotation,
    QuotationLine,
    QuotationSection,
    QuotationStatus,
    VatMode,
)
from .services import (
    QuotationError,
    create_revision,
    duplicate,
    ensure_quotation_types,
    guard_editable,
    next_statuses,
    record_purchase_order,
    transition,
)


def make_company(name="Lulama"):
    company = Company.objects.create(name=name)
    for doc_type, prefix in [("quotation", "QT"), ("project", "PRJ")]:
        NumberingRule.objects.create(company=company, doc_type=doc_type,
                                     prefix=prefix, fmt="{prefix}-{yyyy}-{seq:05d}")
    return company


def make_quote(company, *, vat_mode=VatMode.EXCLUSIVE, status=QuotationStatus.DRAFT,
               number="QT-1"):
    return Quotation.objects.create(
        company=company, number=number, client_name="Harmony Mining",
        vat_mode=vat_mode, vat_rate=Decimal("15.00"), status=status)


def add_line(company, quote, *, qty=1, cost=0, markup=0, discount=0, price=0,
             description="Line", section=None):
    return QuotationLine.objects.create(
        company=company, quotation=quote, section=section, description=description,
        qty=Decimal(str(qty)), unit_cost=Decimal(str(cost)),
        markup_pct=Decimal(str(markup)), discount_pct=Decimal(str(discount)),
        unit_price=Decimal(str(price)))


class PricingTests(TestCase):
    """Cost + markup − discount → price, and margin from it."""

    def test_price_is_derived_from_cost_and_markup(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            line = add_line(c, quote, qty=10, cost=100, markup=25)
            self.assertEqual(line.computed_unit_price, Decimal("125.00"))
            self.assertEqual(line.line_total, Decimal("1250.00"))
            self.assertEqual(line.total_cost, Decimal("1000.00"))
            self.assertEqual(line.gross_profit, Decimal("250.00"))
            self.assertEqual(line.margin_pct, Decimal("20.00"))

    def test_discount_reduces_the_marked_up_price(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            line = add_line(c, quote, qty=1, cost=100, markup=50, discount=10)
            # 100 → 150 → less 10% → 135
            self.assertEqual(line.computed_unit_price, Decimal("135.00"))

    def test_an_explicit_price_wins_over_the_computed_one(self):
        """An estimator quoting a rate they were given must not have it
        silently recalculated."""
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            line = add_line(c, quote, qty=1, cost=100, markup=25, price=140)
            self.assertEqual(line.effective_unit_price, Decimal("140.00"))
            self.assertEqual(line.margin_pct, Decimal("28.57"))


class MarginHonestyTests(TestCase):
    """A margin that flatters itself is worse than no margin."""

    def test_a_line_with_no_cost_reports_unknown_not_a_hundred_percent(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            line = add_line(c, quote, qty=1, price=500)      # no cost captured
            self.assertFalse(line.has_cost)
            self.assertIsNone(line.margin_pct)

    def test_quotation_margin_is_unknown_while_any_line_is_uncosted(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, qty=1, cost=100, markup=20, description="Costed")
            add_line(c, quote, qty=1, price=500, description="Not costed")
            self.assertFalse(quote.has_costs)
            self.assertIsNone(quote.margin_pct)
            self.assertEqual([line.description for line in quote.uncosted_lines],
                             ["Not costed"])

    def test_margin_appears_once_every_line_is_costed(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, qty=1, cost=100, markup=25)
            add_line(c, quote, qty=1, cost=100, markup=25)
            self.assertTrue(quote.has_costs)
            self.assertEqual(quote.margin_pct, Decimal("20.00"))


class VatModeTests(TestCase):
    """VAT added on top, or extracted from within — same line prices."""

    def test_exclusive_adds_vat_on_top(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, vat_mode=VatMode.EXCLUSIVE)
            add_line(c, quote, qty=1, price=1000)
            self.assertEqual(quote.net_total, Decimal("1000.00"))
            self.assertEqual(quote.vat_amount, Decimal("150.00"))
            self.assertEqual(quote.total, Decimal("1150.00"))

    def test_inclusive_extracts_vat_from_within(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, vat_mode=VatMode.INCLUSIVE)
            add_line(c, quote, qty=1, price=1150)
            self.assertEqual(quote.total, Decimal("1150.00"))     # what they pay
            self.assertEqual(quote.vat_amount, Decimal("150.00"))  # inside it

    def test_profit_never_counts_vat_as_income(self):
        """VAT is never yours — an inclusive quote must not book it as margin."""
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, vat_mode=VatMode.INCLUSIVE)
            add_line(c, quote, qty=1, cost=500, price=1150)
            # Net of VAT the work is worth 1000; cost 500 → 50%.
            self.assertEqual(quote.gross_profit, Decimal("500.00"))
            self.assertEqual(quote.margin_pct, Decimal("50.00"))


class LifecycleTests(TestCase):
    def test_the_approval_chain_advances_one_step_at_a_time(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, price=100)
            self.assertEqual(next_statuses(quote), [QuotationStatus.REVIEW])
            transition(quote, None, to_status=QuotationStatus.REVIEW)
            self.assertEqual(next_statuses(quote),
                             [QuotationStatus.MANAGER_APPROVAL])

    def test_an_empty_quotation_cannot_be_issued(self):
        """An empty quotation reaching a customer is worse than a late one."""
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.APPROVED)
            with self.assertRaises(QuotationError):
                transition(quote, None, to_status=QuotationStatus.ISSUED)

    def test_every_transition_is_recorded(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, price=100)
            transition(quote, None, to_status=QuotationStatus.REVIEW, note="ready")
            event = quote.events.first()
            self.assertEqual(event.to_status, QuotationStatus.REVIEW)
            self.assertEqual(event.note, "ready")

    def test_a_decided_quotation_cannot_be_reopened(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.REJECTED)
            with self.assertRaises(QuotationError):
                transition(quote, None, to_status=QuotationStatus.DRAFT)

    def test_losing_records_the_reason(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.ISSUED)
            transition(quote, None, to_status=QuotationStatus.REJECTED,
                       note="Price too high")
            quote.refresh_from_db()
            self.assertEqual(quote.lost_reason, "Price too high")


class LockingTests(TestCase):
    """An awarded quotation is what was contracted."""

    def test_a_draft_is_editable(self):
        c = make_company()
        with tenant_scope(c.id):
            guard_editable(make_quote(c))       # does not raise

    def test_an_awarded_quotation_refuses_edits(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.AWARDED)
            self.assertTrue(quote.is_locked)
            with self.assertRaises(QuotationError) as ctx:
                guard_editable(quote)
            self.assertIn("revision", str(ctx.exception))


class RevisionTests(TestCase):
    def test_a_revision_supersedes_rather_than_overwrites(self):
        """The numbers that were sent must stay exactly as they were sent."""
        c = make_company()
        with tenant_scope(c.id):
            original = make_quote(c, status=QuotationStatus.ISSUED)
            section = QuotationSection.objects.create(company=c, quotation=original,
                                                      name="Labour")
            add_line(c, original, qty=2, cost=100, markup=20, section=section)
            original_total = original.net_total

            revised = create_revision(original, None, reason="Client cut scope")

            self.assertEqual(revised.revision, 1)
            self.assertEqual(revised.supersedes_id, original.id)
            self.assertEqual(revised.status, QuotationStatus.DRAFT)
            self.assertEqual(revised.lines.count(), 1)
            self.assertEqual(revised.sections.count(), 1)
            self.assertEqual(revised.net_total, original_total)
            original.refresh_from_db()
            self.assertEqual(original.status, QuotationStatus.ISSUED)  # untouched
            self.assertEqual(original.net_total, original_total)

    def test_duplicate_is_a_new_quotation_not_a_revision(self):
        c = make_company()
        with tenant_scope(c.id):
            ensure_quotation_types(c)
            original = make_quote(c)
            add_line(c, original, qty=1, cost=50, markup=10)
            copy = duplicate(original, None)
            self.assertEqual(copy.revision, 0)
            self.assertIsNone(copy.supersedes_id)
            self.assertNotEqual(copy.number, original.number)
            self.assertEqual(copy.lines.count(), 1)


class AwardTests(TestCase):
    def test_award_refuses_without_a_customer_purchase_order(self):
        """Work created with no PO cannot be invoiced against anything."""
        from .services import award_to_work
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.ACCEPTED)
            add_line(c, quote, price=1000)
            with self.assertRaises(QuotationError) as ctx:
                award_to_work(quote, None)
            self.assertIn("purchase order", str(ctx.exception))

    def test_recording_a_po_defaults_to_the_quoted_total(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.ACCEPTED)
            add_line(c, quote, price=1000)
            po = record_purchase_order(quote, None, po_number="4500123456")
            self.assertEqual(po.value, quote.total)
            self.assertEqual(quote.awarded_value, quote.total)

    def test_staged_awards_accumulate(self):
        """Large work arrives as several POs against one quotation."""
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.ACCEPTED)
            add_line(c, quote, price=1000)
            record_purchase_order(quote, None, po_number="PO-1",
                                  value=Decimal("400.00"))
            record_purchase_order(quote, None, po_number="PO-2",
                                  value=Decimal("600.00"))
            self.assertEqual(quote.customer_pos.count(), 2)
            self.assertEqual(quote.awarded_value, Decimal("1000.00"))

    def test_a_po_number_is_required(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            with self.assertRaises(QuotationError):
                record_purchase_order(quote, None, po_number="   ")


class TypeCatalogueTests(TestCase):
    def test_seeding_types_is_idempotent(self):
        c = make_company()
        with tenant_scope(c.id):
            first = ensure_quotation_types(c)
            second = ensure_quotation_types(c)
            self.assertEqual(first, 19)
            self.assertEqual(second, 0)


class PricingReviewTests(TestCase):
    """Deterministic, so the same quotation always raises the same questions."""

    def test_uncosted_lines_are_flagged_as_high(self):
        from .estimating_ai import pricing_review
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, price=500, description="No cost captured")
            findings = pricing_review(quote)["findings"]
            titles = " ".join(f["title"] for f in findings)
            self.assertIn("no cost", titles)
            self.assertTrue(any(f["severity"] == "high" for f in findings))

    def test_thin_margin_is_flagged(self):
        from .estimating_ai import pricing_review
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, qty=1, cost=100, markup=5)   # ~4.8% margin
            titles = " ".join(f["title"] for f in pricing_review(quote)["findings"])
            self.assertIn("Margin is", titles)

    def test_a_line_priced_below_cost_is_flagged_with_the_numbers(self):
        from .estimating_ai import pricing_review
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, qty=1, cost=1000, price=400, description="Loss maker")
            finding = next(f for f in pricing_review(quote)["findings"]
                           if "below cost" in f["title"])
            self.assertTrue(any("Loss maker" in item for item in finding["items"]))

    def test_a_healthy_quotation_raises_only_minor_questions(self):
        from .estimating_ai import pricing_review
        from datetime import date
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            quote.validity_date = date(2030, 1, 1)
            quote.save()
            add_line(c, quote, qty=1, cost=100, markup=40)
            findings = pricing_review(quote)["findings"]
            self.assertFalse([f for f in findings if f["severity"] == "high"])

    def test_an_empty_quotation_says_so_rather_than_passing(self):
        from .estimating_ai import pricing_review
        c = make_company()
        with tenant_scope(c.id):
            result = pricing_review(make_quote(c))
            self.assertFalse(result["ok"])
            self.assertIn("no lines", result["summary"])


class SuggestionTests(TestCase):
    """Grounded in the company's own quoting; proposes, never writes."""

    def test_suggestions_come_from_your_own_comparable_quotations(self):
        from .estimating_ai import suggest_lines
        c = make_company()
        with tenant_scope(c.id):
            past = make_quote(c, number="QT-OLD", status=QuotationStatus.AWARDED)
            past.title = "Conveyor gearbox replacement"
            past.save()
            add_line(c, past, qty=1, cost=500, markup=20,
                     description="Alignment with dial gauge")

            current = make_quote(c, number="QT-NEW")
            current.title = "Conveyor gearbox replacement CV-9"
            current.save()

            result = suggest_lines(current, use_ai=False)
            descriptions = [c["description"] for c in result["candidates"]]
            self.assertIn("Alignment with dial gauge", descriptions)
            self.assertTrue(result["grounded_in"])

    def test_suggesting_writes_nothing(self):
        from .estimating_ai import suggest_lines
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            quote.title = "Pump repair"
            quote.save()
            before = QuotationLine.objects.count()
            suggest_lines(quote, use_ai=False)
            self.assertEqual(QuotationLine.objects.count(), before)

    def test_lines_already_quoted_are_not_suggested_again(self):
        from .estimating_ai import suggest_lines
        c = make_company()
        with tenant_scope(c.id):
            past = make_quote(c, number="QT-OLD", status=QuotationStatus.AWARDED)
            past.title = "Pump seal replacement"
            past.save()
            add_line(c, past, description="Isolate and lock out")

            current = make_quote(c, number="QT-NEW")
            current.title = "Pump seal replacement P-204"
            current.save()
            add_line(c, current, description="Isolate and lock out")

            result = suggest_lines(current, use_ai=False)
            self.assertNotIn("Isolate and lock out",
                             [x["description"] for x in result["candidates"]])

    def test_applying_creates_only_ticked_lines(self):
        from .estimating_ai import apply_suggestions, suggest_lines
        c = make_company()
        with tenant_scope(c.id):
            past = make_quote(c, number="QT-OLD", status=QuotationStatus.AWARDED)
            past.title = "Gearbox job"
            past.save()
            add_line(c, past, description="Step one")
            add_line(c, past, description="Step two")

            current = make_quote(c, number="QT-NEW")
            current.title = "Gearbox job again"
            current.save()

            suggestion = suggest_lines(current, use_ai=False)
            created = apply_suggestions(current, None, suggestion, {0})
            self.assertEqual(created, 1)
            self.assertEqual(current.lines.count(), 1)

    def test_suggestions_cannot_be_applied_to_a_locked_quotation(self):
        from .estimating_ai import apply_suggestions
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.AWARDED)
            with self.assertRaises(QuotationError):
                apply_suggestions(quote, None, {"candidates": []}, {0})


class TypeTemplateTests(TestCase):
    def test_a_type_seeds_the_sections_that_kind_of_job_needs(self):
        from .models import QuotationType
        from .services import apply_type_template
        c = make_company()
        with tenant_scope(c.id):
            ensure_quotation_types(c)
            quote = make_quote(c)
            quote.quotation_type = QuotationType.objects.get(key="plant_hire")
            quote.save()
            created = apply_type_template(quote, None)
            names = set(quote.sections.values_list("name", flat=True))
            self.assertEqual(created, 4)
            self.assertIn("Mobilisation", names)
            self.assertIn("Standby", names)

    def test_applying_a_template_twice_adds_nothing(self):
        from .models import QuotationType
        from .services import apply_type_template
        c = make_company()
        with tenant_scope(c.id):
            ensure_quotation_types(c)
            quote = make_quote(c)
            quote.quotation_type = QuotationType.objects.get(key="labour_hire")
            quote.save()
            apply_type_template(quote, None)
            self.assertEqual(apply_type_template(quote, None), 0)


class LineOrderingTests(TestCase):
    def test_moving_a_line_up_swaps_it_with_its_neighbour(self):
        from .services import move_line
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            first = add_line(c, quote, description="First")
            second = add_line(c, quote, description="Second")
            first.position, second.position = 1, 2
            first.save(); second.save()

            move_line(second, direction="up")
            order = list(quote.lines.values_list("description", flat=True))
            self.assertEqual(order, ["Second", "First"])

    def test_moving_past_the_end_does_nothing(self):
        from .services import move_line
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            only = add_line(c, quote, description="Only")
            move_line(only, direction="up")
            move_line(only, direction="down")
            self.assertEqual(quote.lines.count(), 1)


class QuotedVsActualTests(TestCase):
    """Missing data must never read as a saving."""

    def test_uncaptured_costs_are_reported_as_gaps_not_zeros(self):
        from .services import quoted_vs_actual
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.AWARDED)
            add_line(c, quote, qty=10, cost=100, markup=20)
            result = quoted_vs_actual(quote)

            row = result["rows"][0]
            self.assertFalse(row["captured"])
            self.assertIsNone(row["variance"])       # not 0, not a saving
            self.assertIsNone(result["actual_margin_pct"])
            self.assertIn("floor", result["caveat"])

    def test_quoted_costs_are_grouped_by_category(self):
        from .services import quoted_vs_actual
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.AWARDED)
            labour = add_line(c, quote, qty=10, cost=100, markup=20)
            labour.category = "labour"
            labour.save()
            material = add_line(c, quote, qty=1, cost=5000, markup=15)
            material.category = "material"
            material.save()

            rows = {r["category"]: r for r in quoted_vs_actual(quote)["rows"]}
            self.assertEqual(rows["labour"]["quoted_cost"], Decimal("1000.00"))
            self.assertEqual(rows["material"]["quoted_cost"], Decimal("5000.00"))


class CostPreservationTests(TestCase):
    """Regression: the legacy editor silently destroyed costing.

    It rebuilt the whole line set from description/qty/unit/price, so saving it
    deleted every line and recreated it with no cost, markup, discount,
    category or section. Margin went from a real number to unknown, and nothing
    said so.
    """

    def _quote_with_costing(self, company):
        quote = make_quote(company)
        section = QuotationSection.objects.create(company=company, quotation=quote,
                                                  name="Labour")
        line = add_line(company, quote, qty=10, cost=100, markup=25,
                        description="Millwright", section=section)
        line.category = "labour"
        line.save()
        return quote, line

    def test_updating_lines_keeps_cost_markup_and_section(self):
        from .services import update_quotation
        c = make_company()
        with tenant_scope(c.id):
            quote, line = self._quote_with_costing(c)
            self.assertEqual(quote.margin_pct, Decimal("20.00"))

            update_quotation(quote, None, lines=[
                {"description": "Millwright", "qty": "12", "unit": "hour",
                 "unit_price": ""},
            ])

            line.refresh_from_db()
            self.assertEqual(line.unit_cost, Decimal("100.00"))   # survived
            self.assertEqual(line.markup_pct, Decimal("25.00"))   # survived
            self.assertEqual(line.category, "labour")             # survived
            self.assertIsNotNone(line.section_id)                 # survived
            self.assertEqual(line.qty, Decimal("12.00"))          # updated
            self.assertIsNotNone(quote.margin_pct)                # still knowable

    def test_a_line_the_caller_dropped_is_still_removed(self):
        from .services import update_quotation
        c = make_company()
        with tenant_scope(c.id):
            quote, _ = self._quote_with_costing(c)
            add_line(c, quote, qty=1, cost=50, markup=10, description="Rigger")
            self.assertEqual(quote.lines.count(), 2)

            update_quotation(quote, None, lines=[
                {"description": "Millwright", "qty": "10", "unit": "hour"},
            ])
            self.assertEqual(
                list(quote.lines.values_list("description", flat=True)), ["Millwright"])

    def test_a_new_line_is_still_added(self):
        from .services import update_quotation
        c = make_company()
        with tenant_scope(c.id):
            quote, _ = self._quote_with_costing(c)
            update_quotation(quote, None, lines=[
                {"description": "Millwright", "qty": "10", "unit": "hour"},
                {"description": "Brand new", "qty": "1", "unit": "each",
                 "unit_price": "500"},
            ])
            self.assertEqual(quote.lines.count(), 2)


class RetiredEditorTests(TestCase):
    def test_the_old_edit_url_redirects_to_the_builder(self):
        """Kept alive so old bookmarks land somewhere useful rather than 404."""
        from django.test import Client

        from apps.identity.models import Membership, Permission, Role, User
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
        role = Role.objects.create(name="R", is_system=True)
        perm, _ = Permission.objects.get_or_create(
            codename="quotes.create", defaults={"module": "x", "label": "x"})
        role.permissions.add(perm)
        user = User.objects.create_user("e@lulama.co.za", "x", active_company=c)
        Membership.objects.create(user=user, company=c, role=role)

        client = Client()
        client.force_login(user)
        response = client.get(f"/quotations/{quote.id}/edit/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(quote.id), response["Location"])


class PastedLineTests(TestCase):
    """Estimators live in spreadsheets. Meet them there."""

    def test_tab_separated_from_excel(self):
        from .services import parse_pasted_lines
        rows = parse_pasted_lines(
            "Lip channel 6m\t12\teach\t485\t25\n"
            "Bearing 6203-2RS\t40\teach\t62.50\t30")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["description"], "Lip channel 6m")
        self.assertEqual(rows[0]["qty"], Decimal("12"))
        self.assertEqual(rows[0]["unit_cost"], Decimal("485"))
        self.assertEqual(rows[1]["markup_pct"], Decimal("30"))

    def test_south_african_number_format(self):
        """"1 500,00" is fifteen hundred here, not one and a half."""
        from .services import parse_pasted_lines
        rows = parse_pasted_lines("Transportation\t1\teach\t1 500,00\t10")
        self.assertEqual(rows[0]["unit_cost"], Decimal("1500.00"))

    def test_a_header_row_is_not_priced(self):
        from .services import parse_pasted_lines
        rows = parse_pasted_lines(
            "Description\tQty\tUnit\tCost\nReal item\t2\teach\t100")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "Real item")

    def test_a_bare_list_of_descriptions_works(self):
        from .services import parse_pasted_lines
        rows = parse_pasted_lines("Strip gearbox\nReplace bearings")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["qty"], Decimal("1"))
        self.assertEqual(rows[0]["unit"], "each")

    def test_bulk_add_creates_the_lines_and_respects_locking(self):
        from .services import add_lines_bulk, parse_pasted_lines
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            rows = parse_pasted_lines("Item A\t2\teach\t100\t25\nItem B\t1\tset\t50\t10")
            self.assertEqual(add_lines_bulk(quote, None, rows), 2)
            self.assertEqual(quote.lines.count(), 2)
            first = quote.lines.first()
            self.assertEqual(first.unit_cost, Decimal("100"))
            self.assertEqual(first.effective_unit_price, Decimal("125.00"))

            locked = make_quote(c, number="QT-LOCKED",
                                status=QuotationStatus.AWARDED)
            with self.assertRaises(QuotationError):
                add_lines_bulk(locked, None, rows)


class VendorNumberTests(TestCase):
    """The code the CUSTOMER uses for US. Different for every client."""

    def test_it_lives_on_the_customer_because_it_is_per_relationship(self):
        from apps.customers.services import create_customer
        c = make_company()
        with tenant_scope(c.id):
            harmony = create_customer(c, None, name="Harmony Mining",
                                      seed_departments=False,
                                      vendor_number="TRL0086")
            sasol = create_customer(c, None, name="Sasol", seed_departments=False,
                                    vendor_number="SAS99120")
            self.assertNotEqual(harmony.vendor_number, sasol.vendor_number)

    def test_it_is_snapshotted_onto_the_quotation(self):
        """A quotation issued last year keeps the code it was issued under."""
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            quote.vendor_number = "TRL0086"
            quote.save()
            quote.refresh_from_db()
            self.assertEqual(quote.vendor_number, "TRL0086")

    def test_it_prints_on_the_quotation_pdf(self):
        import io

        import pdfplumber

        from apps.quotes.pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            quote.vendor_number = "TRL0086"
            quote.customer_reference = "REQ-88214"
            quote.save()
            add_line(c, quote, qty=1, price=100)
            pdf = quotation_pdf_bytes(quote)

        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            text = doc.pages[0].extract_text()
        self.assertIn("TRL0086", text)
        self.assertIn("REQ-88214", text)

    def test_pdf_carries_contact_scope_and_preparer(self):
        """§9–10: the document fills itself in — the recipient, the scope, and
        who prepared it — from data already on file, never re-typed."""
        import io

        import pdfplumber

        from apps.customers.services import create_customer
        from apps.identity.models import Membership, User
        from apps.quotes.pdf import quotation_pdf_bytes

        c = make_company()
        with tenant_scope(c.id):
            preparer = User.objects.create_user("sam@lulama.co.za", "x",
                                                first_name="Sam", last_name="Dlamini")
            Membership.objects.create(user=preparer, company=c, job_title="Estimator")
            customer = create_customer(c, preparer, name="Harmony Mining")
            contact = customer.contacts.create(company=c, full_name="Thabo Nkosi",
                                               job_title="Buyer")
            quote = make_quote(c)
            quote.contact = contact
            quote.scope_of_work = "Replace the head-pulley bearings on conveyor CV-102."
            quote.prepared_by = preparer
            quote.save()
            add_line(c, quote, qty=1, price=100)
            pdf = quotation_pdf_bytes(quote)

        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            text = doc.pages[0].extract_text()
        self.assertIn("Thabo Nkosi", text)               # contact person
        self.assertIn("head-pulley bearings", text)      # scope of work
        self.assertIn("Sam Dlamini", text)               # prepared by
        self.assertIn("Estimator", text)                 # their job title
        self.assertIn("Terms", text)                     # terms & conditions
