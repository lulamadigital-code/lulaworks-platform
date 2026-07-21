"""Customer services — turning an org chart into operational behaviour.

The routing functions are the reason this module exists. Once you know that
Sarah Brown approves quotations and Jane Williams receives invoices, LulaWorks
stops asking "who should this go to?" and starts answering it.
"""

import re

from django.db import transaction

from .models import (
    RESPONSIBILITIES,
    Customer,
    CustomerContact,
    CustomerDepartment,
    DEFAULT_DEPARTMENTS,
)


# ── Customer codes ────────────────────────────────────────────────────────────

def _slug_code(name: str) -> str:
    """A short human code from the name: "Harmony Mining" → "HARMON"."""
    letters = re.sub(r"[^A-Za-z]", "", name or "").upper()
    return (letters[:6] or "CUST")


def next_customer_code(company, name: str) -> str:
    """Short, stable, and unique within the company — something people can quote
    on paperwork without reading out a UUID."""
    base = _slug_code(name)
    existing = set(Customer.objects.filter(company=company)
                   .values_list("code", flat=True))
    if base not in existing:
        return base
    for n in range(2, 1000):
        candidate = f"{base[:4]}{n:02d}"
        if candidate not in existing:
            return candidate
    return base


@transaction.atomic
def create_customer(company, user, *, name, seed_departments=True, **fields) -> Customer:
    """Create a client organisation. Departments are seeded by default because
    an empty customer is useless — you cannot file a contact without one."""
    customer = Customer.objects.create(
        company=company, name=name.strip(),
        code=fields.pop("code", "") or next_customer_code(company, name),
        created_by=user, updated_by=user, **fields,
    )
    if seed_departments:
        for dept in DEFAULT_DEPARTMENTS:
            CustomerDepartment.objects.create(company=company, customer=customer,
                                              name=dept, created_by=user,
                                              updated_by=user)
    return customer


def get_or_create_by_name(company, user, name: str):
    """Resolve a free-text client name to a real Customer.

    Used to migrate the old `client_name` strings and to keep older code paths
    working while they are converted. Matching is case-insensitive because the
    live data already contains 'sibanye' and 'Sasol Secunda' typed by hand.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    existing = Customer.objects.filter(company=company, name__iexact=cleaned).first()
    if existing:
        return existing
    trading = Customer.objects.filter(company=company,
                                      trading_name__iexact=cleaned).first()
    if trading:
        return trading
    return create_customer(company, user, name=cleaned, seed_departments=False)


# ── Who does what: the routing engine ────────────────────────────────────────

def contacts_with(customer, responsibility: str, *, department=None):
    """Active contacts empowered to do something. Inactive people are excluded —
    sending a quotation to someone who left is how deals go quiet."""
    qs = customer.contacts.filter(status=CustomerContact.Status.ACTIVE)
    if department is not None:
        qs = qs.filter(department=department)
    return [c for c in qs if c.can(responsibility)]


#: Who a document goes TO, and who is copied. Expressed as responsibilities
#: rather than job titles, so it keeps working at a customer whose Finance
#: Manager is called something else.
DOCUMENT_ROUTING = {
    "quotation": {"to": ["approve_quotation"],
                  "cc": ["release_rfq", "approve_po"]},
    "invoice": {"to": ["receive_invoice"],
                "cc": ["approve_invoice", "receive_reports"]},
    "progress_report": {"to": ["receive_reports"], "cc": ["approve_quotation"]},
    "safety_file": {"to": ["receive_safety_file"], "cc": ["authorise_site_access"]},
    "completion_certificate": {"to": ["sign_completion"], "cc": ["receive_reports"]},
    "variation": {"to": ["approve_variation"], "cc": ["approve_quotation"]},
    "rfq_response": {"to": ["release_rfq"], "cc": ["approve_quotation"]},
}


def route_document(customer, kind: str, *, department=None) -> dict:
    """Who should receive this document, and who should be copied.

    Returns contacts, never bare addresses, so the caller can show a human the
    names before anything is sent. Nothing here sends anything — routing is a
    suggestion a person confirms, in line with the approval boundary everywhere
    else in the platform.
    """
    rules = DOCUMENT_ROUTING.get(kind)
    if rules is None:
        raise KeyError(f"No routing defined for '{kind}'.")

    to, cc, seen = [], [], set()
    for responsibility in rules["to"]:
        for contact in contacts_with(customer, responsibility, department=department):
            if contact.pk not in seen and contact.reach:
                seen.add(contact.pk)
                to.append(contact)
    for responsibility in rules["cc"]:
        for contact in contacts_with(customer, responsibility, department=department):
            if contact.pk not in seen and contact.reach:
                seen.add(contact.pk)
                cc.append(contact)

    # No named recipient: fall back to the primary contact, then the company
    # switchboard — and SAY that is what happened, so nobody assumes the
    # routing worked when it merely defaulted.
    fallback = None
    if not to:
        primary = customer.contacts.filter(
            status=CustomerContact.Status.ACTIVE, is_primary=True).first()
        if primary and primary.reach:
            to = [primary]
            fallback = "No contact holds that responsibility — using the primary contact."
        elif customer.email:
            fallback = f"No contact holds that responsibility — using {customer.email}."
        else:
            fallback = "Nobody at this customer can receive this document yet."

    return {
        "kind": kind,
        "to": to,
        "cc": cc,
        "fallback": fallback,
        "customer_email": customer.email,
    }


def responsibility_matrix(customer) -> list[dict]:
    """Every responsibility and who holds it — the gaps are the useful part.
    An unassigned "approves invoices" is why a payment is sitting unactioned."""
    rows = []
    for key, label in RESPONSIBILITIES.items():
        holders = contacts_with(customer, key)
        rows.append({"key": key, "label": label, "contacts": holders,
                     "covered": bool(holders)})
    return rows


# ── Contact history ──────────────────────────────────────────────────────────

def contact_timeline(contact) -> dict:
    """What this person has actually been involved in.

    Deliberately reads through the real workflow records rather than a separate
    activity log — a CRM note saying "sent quotation" that disagrees with the
    quotations table is worse than no note.
    """
    from apps.projects.models import Project
    from apps.quotes.models import Quotation
    from apps.rfq.models import RFQDocument

    customer = contact.customer
    rfqs = list(RFQDocument.objects.filter(released_by=contact)
                .order_by("-created_at")[:50])
    quotations = list(Quotation.objects.filter(customer=customer)
                      .order_by("-created_at")[:50])
    projects = list(Project.objects.filter(customer=customer)
                    .order_by("-created_at")[:50])

    return {
        "contact": contact,
        "rfqs": rfqs,
        "quotations": quotations,
        "projects": projects,
        "rfq_count": len(rfqs),
        "responsibilities": contact.responsibility_labels(),
    }


def customer_overview(customer) -> dict:
    """The numbers a manager wants on the customer page."""
    from apps.finance.models import Invoice
    from apps.projects.models import Project
    from apps.quotes.models import Quotation

    quotations = Quotation.objects.filter(customer=customer)
    projects = Project.objects.filter(customer=customer)
    invoices = Invoice.objects.filter(project__customer=customer)

    return {
        "contacts": customer.contacts.filter(
            status=CustomerContact.Status.ACTIVE).count(),
        "departments": customer.departments.count(),
        "sites": customer.sites.count(),
        "branches": customer.branches.count(),
        "quotations": quotations.count(),
        "projects": projects.count(),
        "open_projects": projects.exclude(status__in=["complete", "cancelled"]).count(),
        "invoices": invoices.count(),
        "contracts": customer.contracts.filter(status="active").count(),
    }
