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
               number="QT-1", site="K4 Shaft"):
    # A real quotation always has a customer (the create flow sets it); attach an
    # active one so document-generation guards see valid data.
    import uuid

    from apps.customers.models import Customer
    customer = Customer.objects.create(
        company=company, name="Harmony Mining", code=f"C-{uuid.uuid4().hex[:8]}")
    return Quotation.objects.create(
        company=company, number=number, client_name=customer.name,
        customer=customer, site=site,
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

    def test_exclusive_defers_vat_to_the_invoice(self):
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, vat_mode=VatMode.EXCLUSIVE)
            add_line(c, quote, qty=1, price=1000)
            self.assertEqual(quote.net_total, Decimal("1000.00"))
            self.assertEqual(quote.vat_amount, Decimal("150.00"))    # memo
            self.assertEqual(quote.total, Decimal("1000.00"))        # VAT not added here
            self.assertEqual(quote.invoice_total, Decimal("1150.00"))

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

    def test_an_approved_quotation_cannot_be_moved_backwards(self):
        # Approved is the finalized commercial record — it cannot be reopened to
        # draft/review; a change is a revision, not a reversal.
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c, status=QuotationStatus.APPROVED)
            add_line(c, quote, price=100)
            for back in (QuotationStatus.DRAFT, QuotationStatus.REVIEW,
                         QuotationStatus.MANAGER_APPROVAL):
                with self.assertRaises(QuotationError):
                    transition(quote, None, to_status=back)

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
    def test_the_edit_url_opens_the_creation_page(self):
        """Editing reuses the guided creation page (edit == create)."""
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
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save changes")


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
        self.assertIn("head-pulley", text)               # scope of work
        self.assertIn("Sam Dlamini", text)               # prepared by
        self.assertIn("Compiled By", text)               # supplier sign-off only
        # A quotation is the supplier's OFFER: no customer counter-sign, and never
        # a delivery acknowledgement ("received in good order" is a delivery note).
        self.assertNotIn("Accepted By", text)
        self.assertNotIn("Received in Good Order", text)


class QuotationPdfLayoutTests(TestCase):
    """The PDF matches the format contractors issue: the customer's supplier
    number (even when only on the customer record), the ship-to site, and the
    company's brand colour rather than a crash on a bad value."""

    def test_supplier_number_falls_back_to_the_customer(self):
        import io

        import pdfplumber

        from apps.customers.services import create_customer
        from apps.quotes.pdf import quotation_pdf_bytes

        c = make_company()
        with tenant_scope(c.id):
            customer = create_customer(c, None, name="Harmony", vendor_number="TRL0086")
            quote = make_quote(c)
            quote.customer = customer
            quote.vendor_number = ""            # snapshot was empty at creation
            quote.site = "K4 Shaft, Plant 1"
            quote.save()
            add_line(c, quote, qty=1, price=100)
            pdf = quotation_pdf_bytes(quote)
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            text = doc.pages[0].extract_text()
        self.assertIn("TRL0086", text)                 # supplier no, from the customer
        self.assertIn("Supplier No", text)
        self.assertIn("K4 Shaft", text)                # ship to / site

    def test_brand_colour_is_honoured_with_a_teal_fallback(self):
        # The accent uses the company's own brand colour; a bad or empty value
        # falls back to the LulaWorks teal rather than breaking the PDF.
        from apps.quotes.pdf import _brand_color
        c = make_company()
        c.brand_primary = "this is not a colour"       # what the crash came from
        self.assertEqual(_brand_color(c).hexval(), "0x0e6e6e")   # default teal
        c.brand_primary = "#a5127f"
        self.assertEqual(_brand_color(c).hexval(), "0xa5127f")   # honoured


class GridLineParsingTests(TestCase):
    """The create-page grid's fourth column is a selling price, not a cost —
    the bug behind a line that showed a zero unit price but a real amount."""

    def test_grid_price_maps_to_unit_price_not_cost(self):
        from apps.quotes.services import add_lines_bulk, parse_grid_lines
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            rows = parse_grid_lines("30t crane\t2\tday\t18500")
            self.assertEqual(rows[0]["unit_price"], Decimal("18500"))
            self.assertNotIn("unit_cost", rows[0])
            add_lines_bulk(quote, None, rows)
            line = quote.lines.first()
            self.assertEqual(line.unit_price, Decimal("18500"))
            self.assertEqual(line.unit_cost, Decimal("0"))
            self.assertEqual(line.line_total, Decimal("37000.00"))

    def test_pdf_unit_price_column_agrees_with_the_amount(self):
        """Even a cost+markup line prints a unit price that matches its amount."""
        import io

        import pdfplumber

        from apps.quotes.pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            add_line(c, quote, qty=2, cost=17845, markup=0)   # price via cost
            pdf = quotation_pdf_bytes(quote)
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            text = doc.pages[0].extract_text()
        self.assertIn("R17,845.00", text)     # unit price column, not R0.00
        self.assertIn("R35,690.00", text)     # amount


class CommercialNumberingTests(TestCase):
    """The quotation reference is the parent from which child documents inherit."""

    def test_number_uses_the_company_document_prefix(self):
        from apps.quotes.services import next_quotation_number
        c = make_company()
        c.document_prefix = "LPS"
        c.save(update_fields=["document_prefix"])
        with tenant_scope(c.id):
            n = next_quotation_number(c)
        self.assertRegex(n, r"^LPS\d{6}$")

    def test_two_quotations_get_different_numbers(self):
        from apps.quotes.services import create_quotation
        c = make_company()
        c.document_prefix = "ENG"; c.save(update_fields=["document_prefix"])
        with tenant_scope(c.id):
            a = create_quotation(c, None, client_name="A")
            b = create_quotation(c, None, client_name="B")
        self.assertNotEqual(a.number, b.number)
        self.assertTrue(a.number.startswith("ENG") and b.number.startswith("ENG"))

    def test_child_document_references_inherit_the_quotation(self):
        from apps.quotes.services import commercial_ref
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS845192")
        self.assertEqual(commercial_ref(q, "invoice"), "INV-LPS845192-01")
        self.assertEqual(commercial_ref(q, "delivery"), "DN-LPS845192-01")
        self.assertEqual(commercial_ref(q, "payment", 2), "PAY-LPS845192-02")
        self.assertEqual(commercial_ref(q, "credit"), "CN-LPS845192-01")


class VatDeferralTests(TestCase):
    """A VAT-exclusive quotation does not add VAT to its total — VAT is applied
    on the tax invoice."""

    def test_exclusive_total_excludes_vat(self):
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, vat_mode=VatMode.EXCLUSIVE)
            add_line(c, q, qty=1, price=1000)
            self.assertEqual(q.total, Decimal("1000.00"))          # no VAT on the quote
            self.assertEqual(q.vat_amount, Decimal("150.00"))      # memo
            self.assertEqual(q.invoice_total, Decimal("1150.00"))  # VAT added on invoice

    def test_inclusive_total_unchanged(self):
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, vat_mode=VatMode.INCLUSIVE)
            add_line(c, q, qty=1, price=1150)
            self.assertEqual(q.total, Decimal("1150.00"))
            self.assertEqual(q.invoice_total, Decimal("1150.00"))


class CommercialDocumentTests(TestCase):
    """Invoice and delivery note are raised from a FINALIZED quotation — a PO is
    optional (linked when present) — and inherit the quotation reference."""

    def _finalized_quote(self, c, price=1000):
        q = make_quote(c, number="LPS845192", vat_mode=VatMode.EXCLUSIVE,
                       status=QuotationStatus.ISSUED)
        add_line(c, q, qty=2, price=price)
        return q

    def test_cannot_generate_before_finalize(self):
        from apps.quotes.services import (
            QuotationError, can_generate_documents, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, status=QuotationStatus.DRAFT)
            add_line(c, q, qty=1, price=100)
            self.assertFalse(can_generate_documents(q))
            with self.assertRaises(QuotationError):
                create_invoice_document(q, None)

    def test_the_same_po_number_cannot_be_attached_twice(self):
        from apps.quotes.services import QuotationError, record_purchase_order
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            record_purchase_order(q, None, po_number="PO900", value=q.total)
            with self.assertRaises(QuotationError):
                record_purchase_order(q, None, po_number="po900", value=q.total)

    def test_invoice_rejected_when_customer_is_blacklisted(self):
        from apps.customers.models import CustomerStatus
        from apps.quotes.services import QuotationError, create_invoice_document
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            q.customer.status = CustomerStatus.BLACKLISTED
            q.customer.save()
            with self.assertRaises(QuotationError):
                create_invoice_document(q, None)

    def test_delivery_rejected_without_a_site(self):
        from apps.quotes.services import (
            QuotationError, create_delivery_document, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            q.site = ""
            q.save()
            create_invoice_document(q, None)          # invoice first (allowed)
            with self.assertRaises(QuotationError):
                create_delivery_document(q, None)     # no destination

    def test_delivery_note_requires_the_invoice_first(self):
        # Ordering: approve → invoice → delivery note. A delivery note cannot be
        # raised before the tax invoice exists.
        from apps.quotes.services import (
            QuotationError, create_delivery_document, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            with self.assertRaises(QuotationError):
                create_delivery_document(q, None)          # no invoice yet
            create_invoice_document(q, None)
            dn = create_delivery_document(q, None)         # now allowed
        self.assertEqual(dn.kind, "delivery")

    def test_generate_without_a_po(self):
        """A PO is optional — a finalized quotation goes straight to documents."""
        from apps.quotes.services import (
            can_generate_documents, create_delivery_document, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            self.assertTrue(can_generate_documents(q))
            self.assertFalse(q.customer_pos.exists())
            inv = create_invoice_document(q, None)
            dn = create_delivery_document(q, None, delivery_address="K4 Shaft")
        self.assertEqual(inv.number, "INV-LPS845192-01")
        self.assertEqual(dn.number, "DN-LPS845192-01")
        self.assertIsNone(inv.purchase_order)      # none on file, so none linked

    def test_links_po_when_one_is_on_file(self):
        from apps.quotes.services import create_invoice_document, record_purchase_order
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            record_purchase_order(q, None, po_number="PO45821", value=q.invoice_total)
            inv = create_invoice_document(q, None)
        self.assertEqual(inv.purchase_order.po_number, "PO45821")

    def test_approving_a_document_locks_it(self):
        # Approve is the final step (no finalize/send): it locks the document.
        from apps.quotes.services import (
            QuotationError, create_invoice_document, transition_commercial_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            inv = create_invoice_document(q, None)
            self.assertFalse(inv.is_finalized)
            transition_commercial_document(inv, None, "approved")
            self.assertTrue(inv.is_finalized)
            with self.assertRaises(QuotationError):     # cannot go back to draft
                transition_commercial_document(inv, None, "draft")

    def test_invoice_and_delivery_pdfs_render(self):
        import io

        import pdfplumber

        from apps.quotes.pdf import delivery_note_pdf_bytes, invoice_pdf_bytes
        from apps.quotes.services import (
            create_delivery_document, create_invoice_document, record_purchase_order,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            record_purchase_order(q, None, po_number="PO45821", value=q.invoice_total)
            inv = create_invoice_document(q, None)
            dn = create_delivery_document(q, None)
            inv_pdf = invoice_pdf_bytes(inv)
            dn_pdf = delivery_note_pdf_bytes(dn)
        with pdfplumber.open(io.BytesIO(inv_pdf)) as d:
            itext = d.pages[0].extract_text()
        with pdfplumber.open(io.BytesIO(dn_pdf)) as d:
            dtext = d.pages[0].extract_text()
        self.assertIn("TAX INVOICE", itext)
        self.assertIn("LPS845192", itext)          # quotation ref inherited
        self.assertIn("R2,300.00", itext)          # 2000 net + 15% VAT
        self.assertIn("Bill to:", itext)           # not "Client :"
        self.assertIn("PO45821", itext)            # the submitted PO number
        self.assertIn("Invoice Compiled By:", itext)
        self.assertIn("BANKING DETAILS", itext)
        self.assertIn("DELIVERY NOTE", dtext)
        self.assertIn("Outstanding", dtext)        # operational columns
        self.assertNotIn("R2,000", dtext)          # a delivery note shows no prices
        # A delivery note carries no banking details and no Unit column.
        self.assertNotIn("BANKING DETAILS", dtext)
        self.assertNotIn("Unit", dtext)


class CommercialDocumentContentTests(TestCase):
    """V4 document consistency: standard terms auto-inserted from the company
    profile, a Prepared By block, and delivery quantities that default to a full
    delivery — across quotation, invoice and delivery note."""

    def _text(self, pdf_bytes):
        import io

        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as d:
            return "\n".join(p.extract_text() or "" for p in d.pages)

    def _prepared_quote(self, c):
        from apps.identity.models import Membership, User
        u = User.objects.create_user("estimator@lula.co", "x",
                                     first_name="Ronny", last_name="Maluleke",
                                     mobile="082 555 1234")
        Membership.objects.create(user=u, company=c, job_title="Senior Estimator")
        q = make_quote(c, number="LPS845192", vat_mode=VatMode.EXCLUSIVE,
                       status=QuotationStatus.ISSUED)
        q.prepared_by = u
        q.scope_of_work = "Supply and install conveyor idlers at K4 shaft."
        q.save()
        add_line(c, q, qty=20, price=100)
        return q

    def _set_terms(self, c):
        from apps.administration.models import CompanySettings
        row, _ = CompanySettings.objects.get_or_create(company=c)
        row.quotation_terms = "Prices valid for 30 days.\n50% deposit required."
        row.invoice_terms = "Payment due within 30 days. E&OE."
        row.delivery_terms = "Goods received in good order unless noted."
        row.save()

    def test_prepared_by_block_shows_name_and_position_only(self):
        from apps.quotes.pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            q = self._prepared_quote(c)
            text = self._text(quotation_pdf_bytes(q))
        self.assertIn("Prepared By:", text)
        self.assertIn("Ronny Maluleke", text)
        self.assertIn("Senior Estimator", text)      # position from membership
        # Cell and email are deliberately omitted from the Prepared By block.
        self.assertNotIn("082 555 1234", text)
        self.assertNotIn("Email: estimator@lula.co", text)

    def test_terms_auto_inserted_from_company_settings(self):
        from apps.quotes.pdf import (
            delivery_note_pdf_bytes, invoice_pdf_bytes, quotation_pdf_bytes,
        )
        from apps.quotes.services import (
            create_delivery_document, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            self._set_terms(c)
            q = self._prepared_quote(c)
            qt = self._text(quotation_pdf_bytes(q))
            inv = self._text(invoice_pdf_bytes(create_invoice_document(q, None)))
            dn = self._text(delivery_note_pdf_bytes(create_delivery_document(q, None)))
        self.assertIn("Terms & Conditions", qt)
        self.assertIn("50% deposit required.", qt)
        self.assertIn("Payment due within 30 days.", inv)
        self.assertIn("Goods received in good order", dn)

    def test_documents_are_forced_onto_a_single_page(self):
        # Even a long quotation (and its invoice and delivery note) stays on one
        # page — the content is shrunk to fit rather than spilling over.
        import io

        import pdfplumber

        from apps.quotes.pdf import (
            delivery_note_pdf_bytes, invoice_pdf_bytes, quotation_pdf_bytes,
        )
        from apps.quotes.services import (
            create_delivery_document, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS654321", vat_mode=VatMode.EXCLUSIVE,
                           status=QuotationStatus.ISSUED)
            q.scope_of_work = "A long job. " * 40
            q.save()
            for i in range(40):
                add_line(c, q, qty=i + 1, price=100 + i)
            pdfs = [quotation_pdf_bytes(q),
                    invoice_pdf_bytes(create_invoice_document(q, None)),
                    delivery_note_pdf_bytes(create_delivery_document(q, None))]
        for b in pdfs:
            with pdfplumber.open(io.BytesIO(b)) as d:
                self.assertEqual(len(d.pages), 1)

    def test_contact_tel_and_email_appear_on_invoice_and_delivery(self):
        # The contact person's Tel and Email show on the invoice and delivery
        # note, just like on the quotation.
        from apps.customers.models import Customer, CustomerContact
        from apps.quotes.pdf import delivery_note_pdf_bytes, invoice_pdf_bytes
        from apps.quotes.services import (
            create_delivery_document, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            cust = Customer.objects.create(company=c, name="Harmony Mining")
            contact = CustomerContact.objects.create(
                company=c, customer=cust, full_name="Thabo Nkosi",
                telephone="011 999 1234", email="thabo@harmony.co")
            q = make_quote(c, number="LPS900111", vat_mode=VatMode.EXCLUSIVE,
                           status=QuotationStatus.ISSUED)
            q.customer = cust
            q.contact = contact
            q.save()
            add_line(c, q, qty=1, price=500)
            inv = self._text(invoice_pdf_bytes(create_invoice_document(q, None)))
            dn = self._text(delivery_note_pdf_bytes(create_delivery_document(q, None)))
        for text in (inv, dn):
            self.assertIn("Thabo Nkosi", text)
            self.assertIn("011 999 1234", text)
            self.assertIn("thabo@harmony.co", text)

    def test_scope_of_work_falls_back_to_the_quotation_title(self):
        # The quotation title IS its scope of work — used when the dedicated
        # scope field is left empty.
        from apps.quotes.pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS777888", status=QuotationStatus.ISSUED)
            q.title = "3 in 1 Mediloo Including bucket"
            q.scope_of_work = ""
            q.save()
            add_line(c, q, qty=1, price=500)
            text = self._text(quotation_pdf_bytes(q))
        self.assertRegex(text, r"Scope of Work:\s*3 in 1 Mediloo Including bucket")

    def test_delivery_quantities_default_to_full_delivery(self):
        from apps.quotes.pdf import delivery_note_pdf_bytes
        from apps.quotes.services import (
            create_delivery_document, create_invoice_document,
        )
        c = make_company()
        with tenant_scope(c.id):
            q = self._prepared_quote(c)          # a line of qty 20
            create_invoice_document(q, None)     # invoice precedes the delivery note
            dn = create_delivery_document(q, None)
            text = self._text(delivery_note_pdf_bytes(dn))
        # Ordered 20, Delivered 20, Outstanding 0 — the normal full delivery.
        self.assertIn("Outstanding", text)
        self.assertRegex(text, r"20(?:\.00)?\s+20(?:\.00)?\s+0\b")


class DiscountTests(TestCase):
    """An overall quotation discount comes off the subtotal before VAT."""

    def test_overall_discount_reduces_net_and_total(self):
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, vat_mode=VatMode.EXCLUSIVE)
            add_line(c, q, qty=1, price=1000)
            q.discount_amount = Decimal("150")
            q.save()
            self.assertEqual(q.subtotal, Decimal("1000.00"))    # lines, pre-discount
            self.assertEqual(q.net_total, Decimal("850.00"))    # after discount
            self.assertEqual(q.total, Decimal("850.00"))
            self.assertEqual(q.invoice_total, Decimal("977.50"))  # 850 + 15% VAT

    def test_discount_never_pushes_the_total_below_zero(self):
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, vat_mode=VatMode.EXCLUSIVE)
            add_line(c, q, qty=1, price=500)
            q.discount_amount = Decimal("900")     # more than the lines
            q.save()
            self.assertEqual(q.net_total, Decimal("0.00"))

    def test_a_negative_discount_cannot_inflate_the_total(self):
        # A crafted negative discount must not become a surcharge.
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, vat_mode=VatMode.EXCLUSIVE)
            add_line(c, q, qty=1, price=500)
            q.discount_amount = Decimal("-200")
            q.save()
            self.assertEqual(q.net_total, Decimal("500.00"))   # discount ignored
            self.assertEqual(q.total, Decimal("500.00"))

    def test_discount_prints_on_the_quotation_pdf(self):
        import io

        import pdfplumber

        from apps.quotes.pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS314159", vat_mode=VatMode.EXCLUSIVE,
                           status=QuotationStatus.ISSUED)
            add_line(c, q, qty=1, price=1000)
            q.discount_amount = Decimal("150")
            q.save()
            with pdfplumber.open(io.BytesIO(quotation_pdf_bytes(q))) as d:
                text = d.pages[0].extract_text()
        self.assertIn("DISCOUNT", text)
        self.assertIn("150.00", text)
        self.assertIn("850.00", text)          # the discounted total


class AwardAndTraceabilityTests(TestCase):
    """Award hands the quotation to execution; traceability is the query that
    proves everything downstream leads back to it."""

    def test_award_to_work_creates_standalone_work_and_locks_the_quote(self):
        from apps.quotes.services import award_to_work, record_purchase_order
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS500001", status=QuotationStatus.ISSUED)
            q.title = "Conveyor overhaul"
            q.save()
            add_line(c, q, qty=2, price=1000)
            record_purchase_order(q, None, po_number="PO7", value=q.total)
            result = award_to_work(q, None, create_project=False)
            q.refresh_from_db()
        self.assertIsNone(result["project"])
        self.assertIsNotNone(result["task"])
        self.assertEqual(q.status, QuotationStatus.AWARDED)

    def test_award_to_work_with_a_project_links_the_customer(self):
        from apps.quotes.services import award_to_work, record_purchase_order
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS500002", status=QuotationStatus.ISSUED)
            q.title = "Shutdown"
            q.save()
            add_line(c, q, qty=1, price=500)
            record_purchase_order(q, None, po_number="PO8", value=q.total)
            result = award_to_work(q, None, create_project=True)
        self.assertIsNotNone(result["project"])
        self.assertEqual(result["project"].quotation_id, q.id)
        self.assertEqual(result["project"].customer_id, q.customer_id)

    def test_award_requires_a_purchase_order(self):
        from apps.quotes.services import award_to_work
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, status=QuotationStatus.ISSUED)
            add_line(c, q, qty=1, price=100)
            with self.assertRaises(QuotationError):
                award_to_work(q, None, create_project=False)

    def test_traceability_returns_the_chain(self):
        from apps.quotes.services import record_purchase_order, traceability
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, status=QuotationStatus.ISSUED)
            add_line(c, q, qty=1, price=100)
            record_purchase_order(q, None, po_number="PO9", value=q.total)
            trace = traceability(q)
        self.assertEqual(trace["quotation"], q)
        self.assertEqual(len(trace["customer_pos"]), 1)
        self.assertEqual(trace["projects"], [])
        self.assertEqual(trace["invoiced"], Decimal("0"))

    def test_award_summary_lists_what_would_be_handed_over(self):
        from apps.quotes.services import award_summary
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, status=QuotationStatus.ISSUED)
            add_line(c, q, qty=1, cost=100, markup=20, description="Fitter")
            summary = award_summary(q)
        self.assertEqual(summary["quotation"], q)
        self.assertEqual(summary["customer"], q.customer)
        self.assertIn("site", summary)

    def test_next_statuses_for_the_post_approval_branches(self):
        from apps.quotes.services import next_statuses
        c = make_company()
        with tenant_scope(c.id):
            accepted = make_quote(c, number="LPS600001",
                                  status=QuotationStatus.ACCEPTED)
            self.assertEqual(next_statuses(accepted), [QuotationStatus.AWARDED])
            revision = make_quote(c, number="LPS600002",
                                  status=QuotationStatus.REVISION_REQUESTED)
            self.assertEqual(next_statuses(revision), [QuotationStatus.DRAFT])
            awarded = make_quote(c, number="LPS600003",
                                 status=QuotationStatus.AWARDED)
            self.assertEqual(next_statuses(awarded), [])   # outcome recorded


class MoveLineTests(TestCase):
    """Reorder a line by swapping with its neighbour; the ends are no-ops."""

    def test_moving_down_swaps_and_the_top_edge_is_a_no_op(self):
        from apps.quotes.services import move_line
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c)
            a = add_line(c, q, description="A")
            b = add_line(c, q, description="B")
            a.position, b.position = 1, 2
            a.save(update_fields=["position"])
            b.save(update_fields=["position"])
            move_line(a, direction="down")          # A and B swap
            a.refresh_from_db()
            b.refresh_from_db()
            self.assertEqual((a.position, b.position), (2, 1))
            move_line(b, direction="up")            # B now at the top → no-op
            b.refresh_from_db()
            self.assertEqual(b.position, 1)


class DocumentGuardBranchTests(TestCase):
    """The shared _guard_generatable preconditions on their own."""

    def test_rejects_a_quotation_with_no_customer(self):
        from apps.quotes.services import create_invoice_document
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, status=QuotationStatus.ISSUED)
            add_line(c, q, qty=1, price=100)
            q.customer = None
            q.save()
            with self.assertRaises(QuotationError):
                create_invoice_document(q, None)

    def test_rejects_a_quotation_with_no_line_items(self):
        from apps.quotes.services import create_invoice_document
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, status=QuotationStatus.ISSUED)   # no lines
            with self.assertRaises(QuotationError):
                create_invoice_document(q, None)


class LineValidationTests(TestCase):
    """A quotation line may not carry a negative quantity or price — bad data
    that would silently corrupt every total summed from the lines."""

    def test_negative_qty_is_rejected(self):
        from django.core.exceptions import ValidationError
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            with self.assertRaises(ValidationError):
                add_line(c, quote, qty=-1, price=100)

    def test_negative_unit_price_is_rejected(self):
        from django.core.exceptions import ValidationError
        c = make_company()
        with tenant_scope(c.id):
            quote = make_quote(c)
            with self.assertRaises(ValidationError):
                add_line(c, quote, qty=1, price=-5)


class CommercialNumberingConcurrencyTests(TestCase):
    """Two callers racing for the next document number must not 500 on the
    unique-constraint collision — the loser re-allocates and gets a fresh one."""

    def _finalized_quote(self, c):
        q = make_quote(c, number="LPS845192", vat_mode=VatMode.EXCLUSIVE,
                       status=QuotationStatus.ISSUED)
        add_line(c, q, qty=2, price=1000)
        return q

    def test_invoice_number_collision_retries_to_a_fresh_number(self):
        from apps.quotes.models import CommercialDocument
        from apps.quotes.services import commercial_ref, create_invoice_document
        c = make_company()
        with tenant_scope(c.id):
            q = self._finalized_quote(c)
            # Occupy the number the first invoice seq would produce, so the
            # creator's first insert collides on (company, number).
            taken = commercial_ref(q, "invoice", 1)          # INV-LPS845192-01
            CommercialDocument.objects.create(
                company=c, quotation=q, kind=CommercialDocument.Kind.DELIVERY,
                number=taken)
            doc = create_invoice_document(q, None)            # must not raise
            self.assertNotEqual(doc.number, taken)
            self.assertEqual(doc.number, commercial_ref(q, "invoice", 2))
            self.assertEqual(
                CommercialDocument.objects.filter(
                    company=c, number=doc.number).count(), 1)


class TenantUploadPathTests(TestCase):
    """Uploaded files are namespaced under the owning company, not a shared date
    bucket — defence in depth against cross-tenant path traversal."""

    def test_po_document_path_is_company_scoped(self):
        from apps.quotes.models import po_upload_path
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c)
            po = record_purchase_order(q, None, po_number="PO900")
        path = po_upload_path(po, "order.pdf")
        self.assertTrue(path.startswith(f"c/{c.id}/"))
        self.assertEqual(path, f"c/{c.id}/customer_pos/order.pdf")

    def test_quotation_document_path_is_company_scoped(self):
        from apps.quotes.models import QuotationDocument, quotation_doc_upload_path
        c = make_company()
        instance = QuotationDocument(company_id=c.id)
        path = quotation_doc_upload_path(instance, "boq.pdf")
        self.assertTrue(path.startswith(f"c/{c.id}/"))
        self.assertEqual(path, f"c/{c.id}/quotation_docs/boq.pdf")


class NameCapitalisationTests(TestCase):
    """Names typed in lower case print capitalised on the documents; acronyms
    and codes are preserved."""

    def test_name_helper(self):
        from apps.quotes.pdf import _name
        self.assertEqual(_name("harmony mining"), "Harmony Mining")
        self.assertEqual(_name("BHP billiton"), "BHP Billiton")   # acronym kept
        self.assertEqual(_name("k4 shaft"), "K4 Shaft")
        self.assertEqual(_name(""), "")

    def test_lowercase_client_name_prints_capitalised(self):
        import io

        import pdfplumber

        from apps.quotes.pdf import quotation_pdf_bytes
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS424242", status=QuotationStatus.ISSUED)
            q.client_name = "harmony mining"
            q.site = "k4 shaft"
            q.save()
            add_line(c, q, qty=1, price=100)
            with pdfplumber.open(io.BytesIO(quotation_pdf_bytes(q))) as d:
                text = d.pages[0].extract_text()
        self.assertIn("Harmony Mining", text)
        self.assertNotIn("harmony mining", text)
        self.assertIn("K4 Shaft", text)


class WorkInitiationTests(TestCase):
    """An approved quotation becomes operational work — project, phases and the
    tasks a job of this type needs — with nothing re-entered."""

    def test_initiate_creates_project_phases_and_type_tasks(self):
        from apps.quotes.models import QuotationType
        from apps.quotes.services import (
            WORK_PHASES, ensure_quotation_types, initiate_work_from_quotation,
        )
        c = make_company()
        with tenant_scope(c.id):
            ensure_quotation_types(c)
            supply = QuotationType.objects.get(company=c, key="supply")
            q = make_quote(c, number="LPS700700", status=QuotationStatus.APPROVED)
            q.quotation_type = supply
            q.scope_of_work = "Supply conveyor rollers"
            q.save()
            add_line(c, q, qty=1, price=1000)
            project = initiate_work_from_quotation(q, None)
            self.assertEqual(project.quotation_id, q.id)
            self.assertEqual(project.customer_id, q.customer_id)
            self.assertEqual(project.phases.count(), len(WORK_PHASES))
            self.assertEqual(project.tasks.count(), 6 + 4)   # supply + universal
            names = set(project.tasks.values_list("name", flat=True))
            self.assertIn("Purchase materials", names)
            self.assertIn("Customer sign-off", names)
            self.assertIn("Issue tax invoice", names)

    def test_repair_type_uses_repair_template(self):
        from apps.quotes.models import QuotationType
        from apps.quotes.services import (
            ensure_quotation_types, initiate_work_from_quotation, resource_hints_for,
        )
        c = make_company()
        with tenant_scope(c.id):
            ensure_quotation_types(c)
            repair = QuotationType.objects.get(company=c, key="mechanical_repair")
            q = make_quote(c, number="LPS700702", status=QuotationStatus.APPROVED)
            q.quotation_type = repair
            q.save()
            add_line(c, q, qty=1, price=500)
            project = initiate_work_from_quotation(q, None)
            names = set(project.tasks.values_list("name", flat=True))
            self.assertIn("Diagnose fault", names)
            self.assertIn("Return to service", names)
            self.assertEqual(resource_hints_for("mechanical_repair"),
                             ["purchase_budget", "transport"])

    def test_initiation_is_idempotent(self):
        from apps.quotes.services import initiate_work_from_quotation
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, number="LPS700701", status=QuotationStatus.APPROVED)
            add_line(c, q, qty=1, price=100)
            p1 = initiate_work_from_quotation(q, None)
            p2 = initiate_work_from_quotation(q, None)
            self.assertEqual(p1.id, p2.id)
            self.assertEqual(q.projects.count(), 1)

    def test_cannot_start_work_before_approval(self):
        from apps.quotes.services import QuotationError, initiate_work_from_quotation
        c = make_company()
        with tenant_scope(c.id):
            q = make_quote(c, status=QuotationStatus.DRAFT)
            add_line(c, q, qty=1, price=100)
            with self.assertRaises(QuotationError):
                initiate_work_from_quotation(q, None)
