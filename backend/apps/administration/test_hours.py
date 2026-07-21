"""Working hours — the company calendar.

Worth testing carefully because the failure mode is silent: a wrong holiday
list does not crash anything, it just promises a client a date your gates are
shut, and nobody notices until the day.
"""

from datetime import date, datetime, time

from django.test import TestCase

from apps.administration.hours import (
    DEFAULT_WEEK,
    add_holiday,
    add_working_days,
    daily_hours,
    ensure_statutory_holidays,
    get_week,
    holidays,
    is_open,
    is_working_day,
    next_open,
    sa_public_holidays,
    save_week,
    weekly_hours,
    working_days_between,
)
from apps.identity.models import Company


def make_company():
    return Company.objects.create(name="Lulama Projects")


class PublicHolidayTests(TestCase):
    """These are computed, not typed — so the computation must be right."""

    def test_easter_dependent_holidays_move_with_easter(self):
        # Easter Sunday 2026 is 5 April; 2027 is 28 March.
        h2026 = {h["name"]: h["date"] for h in sa_public_holidays(2026)}
        self.assertEqual(h2026["Good Friday"], "2026-04-03")
        self.assertEqual(h2026["Family Day"], "2026-04-06")

        h2027 = {h["name"]: h["date"] for h in sa_public_holidays(2027)}
        self.assertEqual(h2027["Good Friday"], "2027-03-26")
        self.assertEqual(h2027["Family Day"], "2027-03-29")

    def test_fixed_holidays_are_present(self):
        names = {h["name"] for h in sa_public_holidays(2026)}
        for expected in ("New Year's Day", "Human Rights Day", "Freedom Day",
                         "Workers' Day", "Youth Day", "National Women's Day",
                         "Heritage Day", "Day of Reconciliation", "Christmas Day",
                         "Day of Goodwill"):
            self.assertIn(expected, names)

    def test_a_sunday_holiday_moves_to_the_monday(self):
        """Public Holidays Act. 2027-12-26 (Day of Goodwill) is a Sunday, so the
        27th is a holiday too — miss this and you promise delivery on a closed day."""
        rows = {h["date"]: h["name"] for h in sa_public_holidays(2027)}
        self.assertEqual(date(2027, 12, 26).weekday(), 6)
        self.assertIn("2027-12-27", rows)
        self.assertIn("observed", rows["2027-12-27"])

    def test_weekday_holidays_are_not_duplicated(self):
        dates = [h["date"] for h in sa_public_holidays(2026)]
        self.assertEqual(len(dates), len(set(dates)))


class HolidayStorageTests(TestCase):
    def test_seeding_is_idempotent_and_keeps_company_closures(self):
        company = make_company()
        add_holiday(company, day=date(2026, 12, 21), name="December shutdown")

        first = ensure_statutory_holidays(company, 2026)
        second = ensure_statutory_holidays(company, 2026)

        self.assertGreater(first, 0)
        self.assertEqual(second, 0)          # nothing added twice
        names = {h["name"] for h in holidays(company, 2026)}
        self.assertIn("December shutdown", names)   # company entry survived
        self.assertIn("Christmas Day", names)


class OpenNowTests(TestCase):
    def setUp(self):
        self.company = make_company()
        save_week(self.company, {k: dict(v) for k, v in DEFAULT_WEEK.items()})

    def test_open_during_business_hours(self):
        # Wednesday 2026-07-22 at 09:00
        status = is_open(self.company, datetime(2026, 7, 22, 9, 0))
        self.assertTrue(status["open"])

    def test_closed_before_opening_says_when_it_opens(self):
        status = is_open(self.company, datetime(2026, 7, 22, 6, 0))
        self.assertFalse(status["open"])
        self.assertIn("07:00", status["reason"])

    def test_lunch_counts_as_closed(self):
        status = is_open(self.company, datetime(2026, 7, 22, 13, 10))
        self.assertFalse(status["open"])
        self.assertIn("Lunch", status["reason"])

    def test_closed_on_sunday_with_a_reason(self):
        status = is_open(self.company, datetime(2026, 7, 26, 10, 0))  # Sunday
        self.assertFalse(status["open"])
        self.assertIn("Sunday", status["reason"])

    def test_public_holiday_closes_the_business(self):
        ensure_statutory_holidays(self.company, 2026)
        status = is_open(self.company, datetime(2026, 12, 25, 10, 0))
        self.assertFalse(status["open"])
        self.assertIn("Christmas", status["reason"])

    def test_next_open_skips_the_weekend(self):
        # Saturday 12:30 — Saturday closes at 12:00, Sunday is closed.
        when = next_open(self.company, datetime(2026, 7, 25, 12, 30))
        self.assertEqual(when.date(), date(2026, 7, 27))    # Monday
        self.assertEqual(when.time(), time(7, 0))


class WorkingDayMathTests(TestCase):
    """The part other modules depend on."""

    def setUp(self):
        self.company = make_company()
        week = {k: dict(v) for k, v in DEFAULT_WEEK.items()}
        week["sat"]["closed"] = True          # a Mon–Fri company
        save_week(self.company, week)

    def test_add_working_days_skips_the_weekend(self):
        # Thursday 2026-07-23 + 3 working days → Tuesday 2026-07-28
        self.assertEqual(add_working_days(self.company, date(2026, 7, 23), 3),
                         date(2026, 7, 28))

    def test_add_working_days_skips_public_holidays(self):
        ensure_statutory_holidays(self.company, 2026)
        # 2026-12-24 Thu + 2 working days: 25th (Christmas) and 26th/28th
        # (Goodwill, observed Monday) are skipped → Tuesday 29 December.
        self.assertEqual(add_working_days(self.company, date(2026, 12, 24), 2),
                         date(2026, 12, 29))

    def test_zero_days_stays_put(self):
        self.assertEqual(add_working_days(self.company, date(2026, 7, 23), 0),
                         date(2026, 7, 23))

    def test_working_days_between_excludes_the_end_date(self):
        # Mon 20 → Mon 27 July 2026: five working days (20,21,22,23,24).
        self.assertEqual(
            working_days_between(self.company, date(2026, 7, 20), date(2026, 7, 27)), 5)

    def test_working_days_between_is_zero_when_reversed(self):
        self.assertEqual(
            working_days_between(self.company, date(2026, 7, 27), date(2026, 7, 20)), 0)

    def test_is_working_day_respects_closures(self):
        ensure_statutory_holidays(self.company, 2026)
        self.assertTrue(is_working_day(self.company, date(2026, 7, 22)))   # Wed
        self.assertFalse(is_working_day(self.company, date(2026, 7, 25)))  # Sat, closed
        self.assertFalse(is_working_day(self.company, date(2026, 12, 25)))  # Christmas


class CapacityTests(TestCase):
    def test_daily_hours_removes_lunch(self):
        company = make_company()
        save_week(company, {k: dict(v) for k, v in DEFAULT_WEEK.items()})
        # Wednesday 07:00–17:00 with a 30-minute lunch = 9.5 productive hours.
        self.assertEqual(daily_hours(company, date(2026, 7, 22)), 9.5)

    def test_closed_days_yield_no_hours(self):
        company = make_company()
        save_week(company, {k: dict(v) for k, v in DEFAULT_WEEK.items()})
        self.assertEqual(daily_hours(company, date(2026, 7, 26)), 0.0)   # Sunday

    def test_weekly_hours_sums_the_configured_week(self):
        company = make_company()
        save_week(company, {k: dict(v) for k, v in DEFAULT_WEEK.items()})
        # Mon–Thu 9.5 each, Fri 8.5, Sat 5.0, Sun closed.
        self.assertEqual(weekly_hours(company), 51.5)


class DefaultsTests(TestCase):
    def test_a_partially_configured_week_is_filled_from_the_default(self):
        """A company that set only Monday must not blow up on Tuesday."""
        company = make_company()
        from apps.administration.models import CompanySettings
        row, _ = CompanySettings.objects.get_or_create(company=company)
        row.business_hours = {"mon": {"open": "06:00"}}
        row.save()

        week = get_week(company)
        self.assertEqual(week["mon"]["open"], "06:00")
        self.assertEqual(week["tue"]["open"], DEFAULT_WEEK["tue"]["open"])
        self.assertTrue(week["sun"]["closed"])


class YearBoundaryRegressionTests(TestCase):
    """Seeding is per-year, but arithmetic crosses years.

    Relying only on STORED holidays meant an unseeded year was silently fully
    workable — a five-day job from 24 December landed its due date on New
    Year's Day. Statutory holidays are now computed as a fallback.
    """

    def setUp(self):
        self.company = make_company()
        week = {k: dict(v) for k, v in DEFAULT_WEEK.items()}
        week["sat"]["closed"] = True
        save_week(self.company, week)

    def test_holidays_resolve_for_years_that_were_never_seeded(self):
        self.assertEqual(holidays(self.company, 2029), [])      # nothing stored
        found = is_working_day(self.company, date(2029, 1, 1))
        self.assertFalse(found)                                  # still a holiday

    def test_a_due_date_never_lands_on_new_years_day(self):
        ensure_statutory_holidays(self.company, 2026)            # 2026 only
        due = add_working_days(self.company, date(2026, 12, 24), 5)
        self.assertNotEqual(due, date(2027, 1, 1))
        self.assertTrue(is_working_day(self.company, due))

    def test_company_closures_still_win_over_the_computed_list(self):
        """A stored entry is authoritative — a company may rename or remove one."""
        add_holiday(self.company, day=date(2027, 6, 16), name="Youth Day — plant open")
        found = holidays(self.company, 2027)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], "company")
