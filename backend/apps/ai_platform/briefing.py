"""LulaAI daily briefing (§24/§25).

"Here's what needs attention today" — permission-driven, so it's inherently
role-appropriate: an owner sees money + operations, a manager sees jobs, and
nobody is shown anything their role can't access. Each item drills into the
assistant via its own question, reusing the Phase 1 tools.
"""
from datetime import timedelta

from django.utils import timezone


def _overdue_count():
    from apps.execution.models import Task
    return sum(1 for t in Task.objects.only("due_date", "status").all() if t.is_overdue)


def _due_today_count():
    from apps.execution.models import Task
    terminal = ["completed", "closed", "cancelled"]   # match tools.my_tasks
    return (Task.objects.filter(due_date=timezone.localdate())
            .exclude(status__in=terminal).count())


def _quotes_awaiting_count():
    from apps.quotes.models import Quotation, QuotationStatus
    return Quotation.objects.filter(status__in=[QuotationStatus.MANAGER_APPROVAL,
                                                QuotationStatus.COMMERCIAL_APPROVAL]).count()


def _unpaid_count():
    from apps.finance.models import Invoice, InvoiceStatus
    return (Invoice.objects.exclude(status__in=[InvoiceStatus.PAID, InvoiceStatus.DRAFT])
            .count())


def _uncontacted_count(days=30):
    from apps.customers.models import Customer
    cutoff = timezone.now() - timedelta(days=days)
    return Customer.objects.filter(updated_at__lt=cutoff).count()


def daily_brief(company, user) -> dict:
    """A role-appropriate attention list. Every item is permission-gated, so the
    brief only ever shows what the user is allowed to see."""
    now = timezone.localtime()
    greet = ("Good morning" if now.hour < 12
             else "Good afternoon" if now.hour < 17 else "Good evening")
    full = (user.get_full_name() or "").strip()
    first = full.split(" ")[0] if full else ""

    items = []

    def add(perm, count, label, query, severity="info"):
        if count and user.has_perm_code(perm):
            items.append({"count": count, "label": label, "query": query,
                          "severity": severity})

    if user.has_perm_code("projects.view"):
        add("projects.view", _overdue_count(),
            "overdue", "Show me overdue tasks", "bad")
        add("projects.view", _due_today_count(),
            "due today", "What tasks are due today?", "warn")
        add("projects.view", _quotes_awaiting_count(),
            "awaiting approval", "Which quotations are awaiting approval?", "warn")
    if user.has_perm_code("finance.view_money"):
        add("finance.view_money", _unpaid_count(),
            "unpaid", "Which invoices are unpaid?", "warn")
    if user.has_perm_code("customers.manage"):
        add("customers.manage", _uncontacted_count(30),
            "not contacted in 30 days", "Which customers haven't been contacted in 30 days?")

    return {
        "greeting": f"{greet}{', ' + first if first else ''}.",
        "items": items,
        "has_attention": bool(items),
        "line": _line(items),
    }


_NOUNS = {
    "overdue": ("task is overdue", "tasks are overdue"),
    "due today": ("task is due today", "tasks are due today"),
    "awaiting approval": ("quotation is awaiting approval", "quotations are awaiting approval"),
    "unpaid": ("invoice is unpaid", "invoices are unpaid"),
    "not contacted in 30 days": ("customer hasn't been contacted in 30 days",
                                 "customers haven't been contacted in 30 days"),
}


def _phrase(item):
    one, many = _NOUNS.get(item["label"], (item["label"], item["label"]))
    return f"{item['count']} {one if item['count'] == 1 else many}"


def _line(items):
    if not items:
        return "You're all caught up — nothing needs your attention right now."
    return "Here's what needs attention: " + "; ".join(_phrase(i) for i in items) + "."
