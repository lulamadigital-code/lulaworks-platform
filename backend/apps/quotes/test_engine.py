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
