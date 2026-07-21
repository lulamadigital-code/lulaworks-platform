"""Working hours — the company calendar every scheduling decision reads.

Hours are only worth storing if something uses them. This module exists so that
"due in 5 days" means five days the crew will actually be on site — not five
calendar days that quietly land a deadline on Christmas Day.

Three things it answers:

  * Are we open right now, and if not, when do we next open?
  * How many working days are there between two dates?
  * What date is N working days from here?  (due dates, SLAs, lead times)

South African public holidays are COMPUTED rather than typed in, including the
Easter-dependent ones and the Public Holidays Act rule that a holiday falling on
a Sunday moves to the Monday. A contractor should not have to maintain that list
by hand every year, and getting it wrong means promising a client a date your
gate is locked.
"""

from datetime import date, datetime, time, timedelta

from django.utils import timezone

#: Weekday keys in Python's weekday() order — Monday is 0.
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
              "thu": "Thursday", "fri": "Friday", "sat": "Saturday",
              "sun": "Sunday"}

#: A contracting default: full week, half-day Saturday, closed Sunday.
DEFAULT_WEEK = {
    "mon": {"closed": False, "open": "07:00", "close": "17:00",
            "lunch_start": "13:00", "lunch_end": "13:30"},
    "tue": {"closed": False, "open": "07:00", "close": "17:00",
            "lunch_start": "13:00", "lunch_end": "13:30"},
    "wed": {"closed": False, "open": "07:00", "close": "17:00",
            "lunch_start": "13:00", "lunch_end": "13:30"},
    "thu": {"closed": False, "open": "07:00", "close": "17:00",
            "lunch_start": "13:00", "lunch_end": "13:30"},
    "fri": {"closed": False, "open": "07:00", "close": "16:00",
            "lunch_start": "13:00", "lunch_end": "13:30"},
    "sat": {"closed": False, "open": "07:00", "close": "12:00",
            "lunch_start": "", "lunch_end": ""},
    "sun": {"closed": True, "open": "", "close": "",
            "lunch_start": "", "lunch_end": ""},
}


def _settings(company):
    from .models import CompanySettings
    row, _ = CompanySettings.objects.get_or_create(company=company)
    return row


def get_week(company) -> dict:
    """The configured week, filled out from the default so a partially
    configured company never hits a missing key."""
    stored = _settings(company).business_hours or {}
    week = {}
    for key in DAYS:
        base = dict(DEFAULT_WEEK[key])
        base.update(stored.get(key) or {})
        week[key] = base
    return week


def save_week(company, week: dict) -> dict:
    row = _settings(company)
    row.business_hours = week
    row.working_days = [k for k in DAYS if not week[k].get("closed")]
    row.save(update_fields=["business_hours", "working_days"])
    return week


def _parse_time(value, fallback=None):
    try:
        hour, minute = str(value).split(":")[:2]
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return fallback


# ── South African public holidays ────────────────────────────────────────────

def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm. Good Friday and Family Day move with it,
    so they cannot be hard-coded."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's own name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def sa_public_holidays(year: int) -> list[dict]:
    """The statutory list, with the Sunday rule applied.

    Public Holidays Act: when a holiday falls on a Sunday, the following Monday
    is also a public holiday. Missing that is how a company promises delivery on
    a day its gates are shut.
    """
    easter = _easter_sunday(year)
    fixed = [
        (date(year, 1, 1), "New Year's Day"),
        (date(year, 3, 21), "Human Rights Day"),
        (easter - timedelta(days=2), "Good Friday"),
        (easter + timedelta(days=1), "Family Day"),
        (date(year, 4, 27), "Freedom Day"),
        (date(year, 5, 1), "Workers' Day"),
        (date(year, 6, 16), "Youth Day"),
        (date(year, 8, 9), "National Women's Day"),
        (date(year, 9, 24), "Heritage Day"),
        (date(year, 12, 16), "Day of Reconciliation"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 26), "Day of Goodwill"),
    ]
    out, seen = [], set()
    for day, name in sorted(fixed):
        out.append({"date": day.isoformat(), "name": name, "source": "statutory"})
        seen.add(day)
        if day.weekday() == 6:            # Sunday → the Monday is a holiday too
            observed = day + timedelta(days=1)
            if observed not in seen:
                out.append({"date": observed.isoformat(),
                            "name": f"{name} (observed)", "source": "statutory"})
                seen.add(observed)
    return sorted(out, key=lambda h: h["date"])


def holidays(company, year=None) -> list[dict]:
    """Stored holidays for the company. Company-specific closures (shutdown
    weeks, founder's day) sit alongside the statutory ones."""
    rows = _settings(company).public_holidays or []
    if year is not None:
        rows = [h for h in rows if str(h.get("date", "")).startswith(str(year))]
    return sorted(rows, key=lambda h: h.get("date", ""))


def ensure_statutory_holidays(company, year: int) -> int:
    """Add any missing statutory holidays for the year. Idempotent, and never
    removes a company's own entries."""
    row = _settings(company)
    existing = {h.get("date") for h in (row.public_holidays or [])}
    added = [h for h in sa_public_holidays(year) if h["date"] not in existing]
    if added:
        row.public_holidays = sorted((row.public_holidays or []) + added,
                                     key=lambda h: h["date"])
        row.save(update_fields=["public_holidays"])
    return len(added)


def add_holiday(company, *, day, name) -> dict:
    row = _settings(company)
    entry = {"date": day.isoformat() if hasattr(day, "isoformat") else str(day),
             "name": name, "source": "company"}
    rows = [h for h in (row.public_holidays or []) if h.get("date") != entry["date"]]
    rows.append(entry)
    row.public_holidays = sorted(rows, key=lambda h: h["date"])
    row.save(update_fields=["public_holidays"])
    return entry


def remove_holiday(company, day) -> None:
    row = _settings(company)
    target = day.isoformat() if hasattr(day, "isoformat") else str(day)
    row.public_holidays = [h for h in (row.public_holidays or [])
                           if h.get("date") != target]
    row.save(update_fields=["public_holidays"])


#: Statutory holidays computed per year, cached for the life of the process.
#: Pure and cheap, so recomputation is never a correctness risk.
_STATUTORY_CACHE: dict[int, dict[str, str]] = {}


def _statutory_index(year: int) -> dict[str, str]:
    if year not in _STATUTORY_CACHE:
        _STATUTORY_CACHE[year] = {h["date"]: h["name"]
                                  for h in sa_public_holidays(year)}
    return _STATUTORY_CACHE[year]


def holiday_on(company, day) -> dict | None:
    """Is this day a closure?

    Stored holidays are checked first (they include the company's own shutdowns
    and any edits), then the COMPUTED statutory list for that day's year.

    That fallback matters: seeding is per-year, and a deadline five days out
    from 24 December crosses into January. Relying only on stored rows meant
    an unseeded year silently became fully workable — the calculation happily
    landed a due date on New Year's Day.
    """
    target = day.isoformat()
    stored = next((h for h in (_settings(company).public_holidays or [])
                   if h.get("date") == target), None)
    if stored is not None:
        return stored
    name = _statutory_index(day.year).get(target)
    return {"date": target, "name": name, "source": "statutory"} if name else None


# ── The questions the rest of the platform asks ──────────────────────────────

def is_working_day(company, day, *, week=None) -> bool:
    """A day the crew is on site: an open weekday that is not a holiday."""
    week = week or get_week(company)
    if week[DAYS[day.weekday()]].get("closed"):
        return False
    return holiday_on(company, day) is None


def is_open(company, at=None) -> dict:
    """Open right now? Returns the answer AND why, because "closed" without a
    reason is useless to whoever is looking at it."""
    at = at or timezone.localtime()
    day = at.date()
    week = get_week(company)
    spec = week[DAYS[day.weekday()]]

    holiday = holiday_on(company, day)
    if holiday:
        return {"open": False, "reason": f"Closed — {holiday['name']}",
                "next_open": next_open(company, at)}
    if spec.get("closed"):
        return {"open": False, "reason": f"Closed on {DAY_LABELS[DAYS[day.weekday()]]}s",
                "next_open": next_open(company, at)}

    opens = _parse_time(spec.get("open"), time(7, 0))
    closes = _parse_time(spec.get("close"), time(17, 0))
    now = at.time()
    if now < opens:
        return {"open": False, "reason": f"Opens at {opens:%H:%M}",
                "next_open": next_open(company, at)}
    if now >= closes:
        return {"open": False, "reason": f"Closed at {closes:%H:%M}",
                "next_open": next_open(company, at)}

    lunch_start = _parse_time(spec.get("lunch_start"))
    lunch_end = _parse_time(spec.get("lunch_end"))
    if lunch_start and lunch_end and lunch_start <= now < lunch_end:
        return {"open": False, "reason": f"Lunch until {lunch_end:%H:%M}",
                "next_open": datetime.combine(day, lunch_end)}

    return {"open": True, "reason": f"Open until {closes:%H:%M}", "next_open": None}


def next_open(company, at=None, *, horizon_days=30):
    """When the company is next open. Bounded, so a company that has closed
    every day returns None instead of looping forever."""
    at = at or timezone.localtime()
    week = get_week(company)
    day = at.date()

    for offset in range(horizon_days + 1):
        candidate = day + timedelta(days=offset)
        if not is_working_day(company, candidate, week=week):
            continue
        spec = week[DAYS[candidate.weekday()]]
        opens = _parse_time(spec.get("open"), time(7, 0))
        closes = _parse_time(spec.get("close"), time(17, 0))
        if offset == 0:
            if at.time() < opens:
                return datetime.combine(candidate, opens)
            if at.time() < closes:
                return at.replace(second=0, microsecond=0)
            continue        # today is finished — try tomorrow
        return datetime.combine(candidate, opens)
    return None


def add_working_days(company, start, days: int):
    """The date `days` WORKING days from `start` — the honest way to turn "this
    takes 5 days" into a deadline. Skips closed days and public holidays."""
    week = get_week(company)
    current = start
    remaining = max(0, int(days))
    guard = 0
    while remaining > 0 and guard < 3650:
        current += timedelta(days=1)
        guard += 1
        if is_working_day(company, current, week=week):
            remaining -= 1
    return current


def working_days_between(company, start, end) -> int:
    """Working days in [start, end) — used for turnaround and SLA reporting."""
    if end <= start:
        return 0
    week = get_week(company)
    count, current = 0, start
    while current < end:
        if is_working_day(company, current, week=week):
            count += 1
        current += timedelta(days=1)
    return count


def daily_hours(company, day) -> float:
    """Productive hours on a given day, lunch removed — the basis for capacity
    planning rather than a guess of eight."""
    week = get_week(company)
    spec = week[DAYS[day.weekday()]]
    if spec.get("closed") or holiday_on(company, day):
        return 0.0
    opens = _parse_time(spec.get("open"), time(7, 0))
    closes = _parse_time(spec.get("close"), time(17, 0))
    total = (closes.hour * 60 + closes.minute) - (opens.hour * 60 + opens.minute)
    lunch_start = _parse_time(spec.get("lunch_start"))
    lunch_end = _parse_time(spec.get("lunch_end"))
    if lunch_start and lunch_end:
        total -= (lunch_end.hour * 60 + lunch_end.minute) - (
            lunch_start.hour * 60 + lunch_start.minute)
    return round(max(0, total) / 60, 2)


def weekly_hours(company) -> float:
    """Contracted hours a full week yields — capacity before any absence."""
    monday = date(2026, 1, 5)          # a plain week; only weekday shape matters
    week = get_week(company)
    total = 0.0
    for offset in range(7):
        day = monday + timedelta(days=offset)
        spec = week[DAYS[day.weekday()]]
        if spec.get("closed"):
            continue
        opens = _parse_time(spec.get("open"), time(7, 0))
        closes = _parse_time(spec.get("close"), time(17, 0))
        minutes = (closes.hour * 60 + closes.minute) - (opens.hour * 60 + opens.minute)
        lunch_start = _parse_time(spec.get("lunch_start"))
        lunch_end = _parse_time(spec.get("lunch_end"))
        if lunch_start and lunch_end:
            minutes -= (lunch_end.hour * 60 + lunch_end.minute) - (
                lunch_start.hour * 60 + lunch_start.minute)
        total += max(0, minutes) / 60
    return round(total, 2)
