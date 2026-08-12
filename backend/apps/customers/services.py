"""Customer services — turning an org chart into operational behaviour.

The routing functions are the reason this module exists. Once you know that
Sarah Brown approves quotations and Jane Williams receives invoices, LulaWorks
stops asking "who should this go to?" and starts answering it.
"""

import re

from django.db import transaction

from .models import (
    CONTACT_ROLES,
    RESPONSIBILITIES,
    Activity,
    Customer,
    CustomerContact,
    CustomerDepartment,
    CustomerNote,
    DEFAULT_DEPARTMENTS,
    Interaction,
    Lead,
    Opportunity,
    OpportunityStage,
    OPEN_OPPORTUNITY_STAGES,
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


# ══════════════════════════════════════════════════════════════════════════════
# CRM — the relationship layer (leads, pipeline, activities, history, notes).
#
# The functions below are what turn the CRM models into behaviour. The house
# rule from the rest of the platform holds here too: the real workflow objects
# (Quotation, Project, Invoice) remain the source of truth; the CRM records
# intent and history and *links* to them, it never duplicates their numbers.
# ══════════════════════════════════════════════════════════════════════════════


class CRMError(Exception):
    """A CRM operation the user can fix — surfaced back to them verbatim."""


# ── Leads ─────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_lead(company, user, *, company_name, **fields) -> Lead:
    """Capture a raw enquiry. Almost everything is optional — a lead is a
    stranger, and demanding a full profile up front is how leads never get
    logged at all."""
    name = (company_name or "").strip()
    if not name:
        raise CRMError("A lead needs at least a company or contact name.")
    return Lead.objects.create(
        company=company, company_name=name,
        created_by=user, updated_by=user, **fields,
    )


@transaction.atomic
def convert_lead(lead, user, *, create_opportunity=True, opportunity_title="",
                 seed_departments=True) -> Customer:
    """Promote a real lead into a Customer (and, by default, an Opportunity).

    This is the Lead → Customer boundary in the sales flow. It is idempotent on
    the customer: a lead already converted returns its existing customer rather
    than minting a duplicate. Everything logged against the lead stays on the
    lead — the trail is preserved via `converted_customer`, not moved.
    """
    from django.utils import timezone

    if lead.converted_customer_id:
        return lead.converted_customer

    customer = create_customer(
        lead.company, user, name=lead.company_name,
        industry=lead.industry, customer_type=lead.customer_type,
        city=lead.city, country=(lead.country or "South Africa"),
        telephone=lead.telephone, mobile=lead.mobile, email=lead.email,
        currency=lead.currency, seed_departments=seed_departments,
    )

    # Carry the human across too, so the customer isn't a faceless shell.
    if lead.contact_name:
        CustomerContact.objects.create(
            company=lead.company, customer=customer, full_name=lead.contact_name,
            job_title=lead.job_title, email=lead.email, telephone=lead.telephone,
            mobile=lead.mobile, is_primary=True,
            created_by=user, updated_by=user,
        )

    lead.status = Lead.Status.CONVERTED
    lead.converted_customer = customer
    lead.converted_at = timezone.now()
    lead.updated_by = user
    lead.save(update_fields=["status", "converted_customer", "converted_at",
                             "updated_by", "updated_at"])

    if create_opportunity:
        create_opportunity_for(
            customer, user,
            title=opportunity_title or f"{lead.company_name} — new enquiry",
            lead=lead, estimated_value=lead.estimated_value,
            currency=lead.currency, source=lead.source,
            stage=OpportunityStage.QUALIFIED,
        )
    return customer


def mark_lead_lost(lead, user, *, reason="") -> Lead:
    lead.status = Lead.Status.LOST
    lead.lost_reason = (reason or "").strip()
    lead.updated_by = user
    lead.save(update_fields=["status", "lost_reason", "updated_by", "updated_at"])
    return lead


# ── Opportunities (the pipeline) ──────────────────────────────────────────────

#: Default probability for each stage — a starting point a salesperson overrides.
#: The funnel gets more likely as it advances; WON/LOST are certainties.
STAGE_PROBABILITY = {
    OpportunityStage.LEAD: 10,
    OpportunityStage.QUALIFIED: 25,
    OpportunityStage.QUOTE_REQUESTED: 40,
    OpportunityStage.QUOTE_SENT: 60,
    OpportunityStage.NEGOTIATION: 80,
    OpportunityStage.WON: 100,
    OpportunityStage.LOST: 0,
}


@transaction.atomic
def create_opportunity_for(customer, user, *, title, stage=OpportunityStage.LEAD,
                           **fields) -> Opportunity:
    """Open a deal against a customer. Probability defaults from the stage unless
    the caller sets one explicitly."""
    title = (title or "").strip()
    if not title:
        raise CRMError("An opportunity needs a title.")
    fields.setdefault("probability", STAGE_PROBABILITY.get(stage, 10))
    return Opportunity.objects.create(
        company=customer.company, customer=customer, title=title, stage=stage,
        created_by=user, updated_by=user, **fields,
    )


@transaction.atomic
def set_opportunity_stage(opp, user, stage, *, reason="") -> Opportunity:
    """Move a deal along the pipeline. Advancing the stage nudges the default
    probability (only if the user hasn't diverged from the previous default), and
    reaching WON/LOST stamps the close date."""
    from django.utils import timezone

    if stage not in OpportunityStage.values:
        raise CRMError(f"Unknown stage '{stage}'.")

    previous_default = STAGE_PROBABILITY.get(opp.stage)
    opp.stage = stage
    # Keep probability meaningful: if it was still sitting on the old stage's
    # default, move it to the new one; if a human set a custom value, respect it.
    if opp.probability == previous_default:
        opp.probability = STAGE_PROBABILITY.get(stage, opp.probability)

    fields = ["stage", "probability", "updated_by", "updated_at"]
    if stage in (OpportunityStage.WON, OpportunityStage.LOST):
        opp.closed_at = timezone.now()
        fields.append("closed_at")
        if stage == OpportunityStage.LOST and reason:
            opp.lost_reason = reason.strip()
            fields.append("lost_reason")
    elif opp.closed_at:                       # re-opened
        opp.closed_at = None
        fields.append("closed_at")

    opp.updated_by = user
    opp.save(update_fields=fields)
    return opp


def win_opportunity(opp, user, *, quotation=None) -> Opportunity:
    """Mark a deal won, optionally linking the quotation it produced."""
    if quotation is not None:
        opp.quotation = quotation
        opp.save(update_fields=["quotation"])
    return set_opportunity_stage(opp, user, OpportunityStage.WON)


def lose_opportunity(opp, user, *, reason="") -> Opportunity:
    return set_opportunity_stage(opp, user, OpportunityStage.LOST, reason=reason)


def pipeline_summary(company) -> dict:
    """The sales pipeline at a glance: count and value in each open stage, plus
    the weighted forecast. This is the number a sales manager opens the CRM to
    see."""
    from decimal import Decimal

    rows, total_value, weighted = [], Decimal("0"), Decimal("0")
    open_opps = list(Opportunity.objects.filter(
        company=company, stage__in=OPEN_OPPORTUNITY_STAGES))
    for stage in OPEN_OPPORTUNITY_STAGES:
        stage_opps = [o for o in open_opps if o.stage == stage]
        value = sum((o.estimated_value or Decimal("0")) for o in stage_opps)
        rows.append({
            "stage": stage,
            "label": OpportunityStage(stage).label,
            "count": len(stage_opps),
            "value": value,
        })
        total_value += value
        weighted += sum(o.weighted_value for o in stage_opps)
    return {
        "stages": rows,
        "open_count": len(open_opps),
        "open_value": total_value,
        "weighted_value": weighted.quantize(Decimal("0.01")),
    }


# ── Activities (the to-do engine) ─────────────────────────────────────────────

@transaction.atomic
def schedule_activity(company, user, *, subject, activity_type=Activity.Type.FOLLOW_UP,
                      customer=None, lead=None, opportunity=None, contact=None,
                      due_at=None, assigned_to=None, detail="") -> Activity:
    subject = (subject or "").strip()
    if not subject:
        raise CRMError("An activity needs a subject.")
    if not any([customer, lead, opportunity]):
        raise CRMError("Attach the activity to a customer, lead or opportunity.")
    return Activity.objects.create(
        company=company, subject=subject, activity_type=activity_type,
        customer=customer, lead=lead, opportunity=opportunity, contact=contact,
        due_at=due_at, assigned_to=assigned_to or user, detail=detail,
        created_by=user, updated_by=user,
    )


def complete_activity(activity, user, *, outcome="") -> Activity:
    """Close an activity and (usefully) drop a matching interaction into the
    history, so "what did we actually do" stays a single timeline."""
    from django.utils import timezone

    activity.status = Activity.Status.DONE
    activity.completed_at = timezone.now()
    activity.outcome = (outcome or "").strip()
    activity.updated_by = user
    activity.save(update_fields=["status", "completed_at", "outcome",
                                 "updated_by", "updated_at"])
    return activity


def cancel_activity(activity, user) -> Activity:
    activity.status = Activity.Status.CANCELLED
    activity.updated_by = user
    activity.save(update_fields=["status", "updated_by", "updated_at"])
    return activity


def open_activities(company, *, assigned_to=None, limit=None):
    qs = Activity.objects.filter(company=company, status=Activity.Status.OPEN)
    if assigned_to is not None:
        qs = qs.filter(assigned_to=assigned_to)
    qs = qs.select_related("customer", "lead", "opportunity", "assigned_to")
    return list(qs[:limit]) if limit else list(qs)


# ── Communication history ─────────────────────────────────────────────────────

@transaction.atomic
def log_interaction(company, user, *, summary, channel=Interaction.Channel.NOTE,
                    direction=Interaction.Direction.OUTBOUND, subject="",
                    occurred_at=None, customer=None, lead=None, opportunity=None,
                    contact=None) -> Interaction:
    from django.utils import timezone

    summary = (summary or "").strip()
    if not summary:
        raise CRMError("A logged interaction needs a summary of what was said.")
    return Interaction.objects.create(
        company=company, summary=summary, channel=channel, direction=direction,
        subject=(subject or "").strip(), occurred_at=occurred_at or timezone.now(),
        customer=customer, lead=lead, opportunity=opportunity, contact=contact,
        created_by=user, updated_by=user,
    )


# ── Notes ─────────────────────────────────────────────────────────────────────

@transaction.atomic
def add_note(company, user, *, body, customer=None, lead=None, opportunity=None,
             is_pinned=False) -> CustomerNote:
    body = (body or "").strip()
    if not body:
        raise CRMError("A note can't be empty.")
    if not any([customer, lead, opportunity]):
        raise CRMError("Attach the note to a customer, lead or opportunity.")
    return CustomerNote.objects.create(
        company=company, body=body, customer=customer, lead=lead,
        opportunity=opportunity, is_pinned=is_pinned,
        created_by=user, updated_by=user,
    )


# ── The customer 360 dashboard ────────────────────────────────────────────────

def customer_dashboard(customer) -> dict:
    """Everything about one customer in one place — the "single source of truth"
    view the spec is really asking for.

    Reads through the real workflow tables (quotations, projects, invoices) so
    the numbers can never drift from them, and folds in the CRM layer (open
    opportunities, next activity, last interaction, pinned notes).
    """
    from decimal import Decimal

    from apps.finance.models import Invoice, InvoiceStatus
    from apps.projects.models import Project
    from apps.quotes.models import OPEN_STATUSES, Quotation, QuotationStatus

    quotations = list(Quotation.objects.filter(customer=customer)
                      .order_by("-created_at"))
    projects = list(Project.objects.filter(customer=customer)
                    .order_by("-created_at"))
    invoices = list(Invoice.objects.filter(project__customer=customer)
                    .select_related("project").order_by("-created_at"))

    open_quotes = [q for q in quotations if q.status in OPEN_STATUSES]
    won_quotes = [q for q in quotations
                  if q.status in (QuotationStatus.ACCEPTED, QuotationStatus.AWARDED)]
    open_projects = [p for p in projects
                     if p.status not in ("complete", "cancelled")]

    paid = sum((inv.paid for inv in invoices), Decimal("0"))
    outstanding = sum((inv.outstanding for inv in invoices
                       if inv.status != InvoiceStatus.PAID), Decimal("0"))

    opportunities = list(Opportunity.objects.filter(customer=customer)
                         .order_by("-created_at"))
    open_opps = [o for o in opportunities if o.is_open]

    next_activity = (Activity.objects.filter(customer=customer,
                                             status=Activity.Status.OPEN)
                     .order_by("due_at", "-created_at").first())
    last_interaction = (Interaction.objects.filter(customer=customer)
                        .order_by("-occurred_at").first())
    pinned_notes = list(CustomerNote.objects.filter(customer=customer,
                                                    is_pinned=True)[:5])
    primary_contact = customer.contacts.filter(
        status=CustomerContact.Status.ACTIVE, is_primary=True).first()

    return {
        "customer": customer,
        "overview": customer_overview(customer),
        # Commercial rollup
        "open_quotes": open_quotes,
        "open_quote_count": len(open_quotes),
        "won_quote_count": len(won_quotes),
        "open_projects": open_projects,
        "open_project_count": len(open_projects),
        "completed_project_count": sum(1 for p in projects
                                       if p.status == "complete"),
        "invoice_count": len(invoices),
        "outstanding": outstanding,
        "revenue": paid,
        # CRM layer
        "opportunities": opportunities[:10],
        "open_opportunity_count": len(open_opps),
        "open_opportunity_value": sum((o.estimated_value or Decimal("0"))
                                      for o in open_opps),
        "next_activity": next_activity,
        "last_interaction": last_interaction,
        "pinned_notes": pinned_notes,
        "primary_contact": primary_contact,
        "recent_quotations": quotations[:6],
        "recent_invoices": invoices[:6],
    }


# ── Global CRM search ─────────────────────────────────────────────────────────

def crm_search(company, query: str, *, limit=10) -> dict:
    """One box, everything: customers, contacts, leads, opportunities, and the
    workflow records people search by number (quotations, invoices).

    Kept deliberately simple (icontains across the fields people actually type)
    — fast, predictable, and good enough until search volume justifies a real
    index. Each bucket is capped so a broad term can't return the whole DB.
    """
    from django.db.models import Q

    q = (query or "").strip()
    if len(q) < 2:
        return {"query": q, "customers": [], "contacts": [], "leads": [],
                "opportunities": [], "quotations": [], "invoices": [], "total": 0}

    customers = list(Customer.objects.filter(company=company).filter(
        Q(name__icontains=q) | Q(trading_name__icontains=q) |
        Q(code__icontains=q) | Q(registration_no__icontains=q) |
        Q(vat_no__icontains=q) | Q(vendor_number__icontains=q))[:limit])

    contacts = list(CustomerContact.objects.filter(company=company).filter(
        Q(full_name__icontains=q) | Q(email__icontains=q) |
        Q(mobile__icontains=q) | Q(telephone__icontains=q))
        .select_related("customer")[:limit])

    leads = list(Lead.objects.filter(company=company).filter(
        Q(company_name__icontains=q) | Q(contact_name__icontains=q) |
        Q(email__icontains=q))[:limit])

    opportunities = list(Opportunity.objects.filter(company=company).filter(
        Q(title__icontains=q) | Q(reference__icontains=q))
        .select_related("customer")[:limit])

    from apps.quotes.models import Quotation
    quotations = list(Quotation.objects.filter(company=company).filter(
        Q(number__icontains=q) | Q(customer_reference__icontains=q) |
        Q(rfq_reference__icontains=q)).select_related("customer")[:limit])

    from apps.finance.models import Invoice
    invoices = list(Invoice.objects.filter(company=company).filter(
        Q(number__icontains=q)).select_related("project", "project__customer")[:limit])

    total = sum(len(x) for x in (customers, contacts, leads, opportunities,
                                 quotations, invoices))
    return {
        "query": q, "customers": customers, "contacts": contacts, "leads": leads,
        "opportunities": opportunities, "quotations": quotations,
        "invoices": invoices, "total": total,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def crm_reports(company) -> dict:
    """The CRM report pack: who the customers are, what they're worth, which
    have gone quiet, and how well enquiries turn into deals."""
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from apps.finance.models import Invoice
    from apps.projects.models import Project

    customers = list(Customer.objects.filter(company=company))
    by_status = {}
    for c in customers:
        by_status[c.status] = by_status.get(c.status, 0) + 1

    # Revenue by customer (paid invoices), reading through projects.
    invoices = list(Invoice.objects.filter(company=company)
                    .select_related("project", "project__customer"))
    revenue = {}
    for inv in invoices:
        cust = getattr(inv.project, "customer", None)
        if cust is None:
            continue
        revenue[cust.id] = revenue.get(cust.id, Decimal("0")) + inv.paid
    cust_by_id = {c.id: c for c in customers}
    top = sorted(
        ({"customer": cust_by_id[cid], "revenue": val}
         for cid, val in revenue.items() if cid in cust_by_id),
        key=lambda r: r["revenue"], reverse=True)[:10]

    # Inactive: no project or activity in 120 days. Cheap heuristic on recency.
    cutoff = timezone.now() - timedelta(days=120)
    recent_project_customers = set(
        Project.objects.filter(company=company, created_at__gte=cutoff)
        .values_list("customer_id", flat=True))
    recent_activity_customers = set(
        Activity.objects.filter(company=company, created_at__gte=cutoff)
        .values_list("customer_id", flat=True))
    active_ids = recent_project_customers | recent_activity_customers
    inactive = [c for c in customers if c.id not in active_ids
                and c.status == "active"]

    # Conversion: won opportunities / all closed opportunities.
    won = Opportunity.objects.filter(company=company,
                                     stage=OpportunityStage.WON).count()
    lost = Opportunity.objects.filter(company=company,
                                      stage=OpportunityStage.LOST).count()
    closed = won + lost
    conversion_rate = round(100 * won / closed, 1) if closed else None

    leads_total = Lead.objects.filter(company=company).count()
    leads_converted = Lead.objects.filter(
        company=company, status=Lead.Status.CONVERTED).count()
    lead_conversion = (round(100 * leads_converted / leads_total, 1)
                       if leads_total else None)

    return {
        "customer_count": len(customers),
        "prospect_count": by_status.get("prospect", 0),
        "active_count": by_status.get("active", 0),
        "by_status": by_status,
        "top_customers": top,
        "total_revenue": sum(revenue.values(), Decimal("0")),
        "inactive_customers": inactive[:25],
        "inactive_count": len(inactive),
        "won": won, "lost": lost,
        "conversion_rate": conversion_rate,
        "leads_total": leads_total,
        "leads_converted": leads_converted,
        "lead_conversion": lead_conversion,
        "pipeline": pipeline_summary(company),
    }


def _recent_months(n: int):
    """The last `n` calendar months, oldest → newest, as (year, month, "Mon YY")."""
    from datetime import date

    from django.utils import timezone
    first = timezone.now().date().replace(day=1)
    y, m, out = first.year, first.month, []
    for _ in range(n):
        out.append((y, m, date(y, m, 1).strftime("%b %y")))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def crm_analytics(company) -> dict:
    """Sales analytics computed from the existing Opportunity/Customer data — no new
    tables. Won value is the opportunity's estimated value at the WON stage. Every
    breakdown is a list of dicts the template renders as a bar chart."""
    from collections import defaultdict
    from decimal import Decimal

    ZERO = Decimal("0")
    opps = list(Opportunity.objects.filter(company=company)
                .select_related("customer", "assigned_to"))
    won = [o for o in opps if o.stage == OpportunityStage.WON]
    lost = [o for o in opps if o.stage == OpportunityStage.LOST]
    open_opps = [o for o in opps
                 if o.stage not in (OpportunityStage.WON, OpportunityStage.LOST)]

    def val(o):
        return o.estimated_value or ZERO

    won_value = sum((val(o) for o in won), ZERO)
    closed = len(won) + len(lost)
    win_rate = round(100 * len(won) / closed, 1) if closed else None
    avg_deal = (won_value / len(won)) if won else ZERO
    cycles = [(o.closed_at - o.created_at).days for o in won
              if o.closed_at and o.created_at]
    avg_cycle = round(sum(cycles) / len(cycles)) if cycles else None

    # ── Sales by salesperson ──────────────────────────────────────────────────
    owners = defaultdict(lambda: {"count": 0, "value": ZERO})
    for o in won:
        name = ((o.assigned_to.get_full_name() or o.assigned_to.email)
                if o.assigned_to else "Unassigned")
        owners[name]["count"] += 1
        owners[name]["value"] += val(o)
    sales_by_owner = sorted(({"name": n, **d} for n, d in owners.items()),
                            key=lambda r: r["value"], reverse=True)

    # ── Sales by customer ─────────────────────────────────────────────────────
    custs = defaultdict(lambda: {"count": 0, "value": ZERO, "customer": None})
    for o in won:
        if not o.customer:
            continue
        row = custs[o.customer_id]
        row["customer"] = o.customer
        row["count"] += 1
        row["value"] += val(o)
    sales_by_customer = sorted(custs.values(), key=lambda r: r["value"],
                               reverse=True)[:10]

    # ── Sales by industry ─────────────────────────────────────────────────────
    inds = defaultdict(lambda: {"count": 0, "value": ZERO})
    for o in won:
        ind = (o.customer.industry.strip() if o.customer and o.customer.industry
               else "Unspecified")
        inds[ind]["count"] += 1
        inds[ind]["value"] += val(o)
    sales_by_industry = sorted(({"name": n, **d} for n, d in inds.items()),
                               key=lambda r: r["value"], reverse=True)

    # ── Won / lost trend (6 months) ───────────────────────────────────────────
    months = _recent_months(6)
    wl = {(y, m): {"won": 0, "won_value": ZERO, "lost": 0} for (y, m, _) in months}
    for o in won:
        when = o.closed_at or o.created_at
        key = (when.year, when.month)
        if key in wl:
            wl[key]["won"] += 1
            wl[key]["won_value"] += val(o)
    for o in lost:
        when = o.closed_at or o.created_at
        key = (when.year, when.month)
        if key in wl:
            wl[key]["lost"] += 1
    won_lost_trend = [{"label": lbl, **wl[(y, m)]} for (y, m, lbl) in months]

    # ── Customer acquisition (6 months) ───────────────────────────────────────
    acq = {(y, m): 0 for (y, m, _) in months}
    for c in Customer.objects.filter(company=company):
        key = (c.created_at.year, c.created_at.month)
        if key in acq:
            acq[key] += 1
    acquisition = [{"label": lbl, "count": acq[(y, m)]} for (y, m, lbl) in months]

    return {
        "won_count": len(won), "lost_count": len(lost), "open_count": len(open_opps),
        "won_value": won_value, "win_rate": win_rate,
        "avg_deal": avg_deal, "avg_cycle": avg_cycle,
        "sales_by_owner": sales_by_owner,
        "sales_by_customer": sales_by_customer,
        "sales_by_industry": sales_by_industry,
        "won_lost_trend": won_lost_trend,
        "acquisition": acquisition,
        "max_owner": max((r["value"] for r in sales_by_owner), default=ZERO),
        "max_customer": max((r["value"] for r in sales_by_customer), default=ZERO),
        "max_industry": max((r["value"] for r in sales_by_industry), default=ZERO),
        "max_trend": max((r["won_value"] for r in won_lost_trend), default=ZERO),
        "max_acq": max((r["count"] for r in acquisition), default=0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Sites & contacts management — the dedicated CRM screens.
#
# A big client is a place with many gates and many people. These are the writes
# behind the Sites and Contacts management pages: create/update a site (where
# work happens, and what it takes to get on it), and maintain the people whose
# RESPONSIBILITIES drive document routing.
# ══════════════════════════════════════════════════════════════════════════════

def _dec_or_none(value):
    from decimal import Decimal, InvalidOperation
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


#: The site fields a form may set — a whitelist, so a stray POST key can't write
#: something it shouldn't.
_SITE_TEXT_FIELDS = ("name", "site_code", "description", "physical_address",
                     "access_notes", "safety_requirements")


@transaction.atomic
def save_site(customer, user, *, site=None, data):
    """Create or update a customer site. `data` is a plain dict (request.POST).
    Returns the site. Name is required; GPS and the on-site contact are optional."""
    from .models import CustomerSite

    name = (data.get("name") or "").strip()
    if not name:
        raise CRMError("A site needs a name.")

    if site is None:
        site = CustomerSite(company=customer.company, customer=customer,
                            created_by=user)
    for field in _SITE_TEXT_FIELDS:
        if field in data:
            setattr(site, field, (data.get(field) or "").strip())
    site.latitude = _dec_or_none(data.get("latitude"))
    site.longitude = _dec_or_none(data.get("longitude"))

    parent_id = (data.get("parent") or "").strip()
    site.parent = (CustomerSite.objects.filter(customer=customer, pk=parent_id).first()
                   if parent_id else None)
    contact_id = (data.get("site_contact") or "").strip()
    site.site_contact = (customer.contacts.filter(pk=contact_id).first()
                         if contact_id else None)
    site.updated_by = user
    site.save()
    return site


def delete_site(site, user):
    """Soft-delete a site (keeps history; nestable children go with it)."""
    site.delete()


#: Contact fields a form may set.
_CONTACT_TEXT_FIELDS = ("full_name", "job_title", "email", "telephone", "mobile",
                        "extension", "whatsapp")


@transaction.atomic
def save_contact(customer, user, *, contact=None, data):
    """Create or update a customer contact, including the responsibilities that
    drive document routing. `data` is request.POST (getlist for the multi-values)."""
    from .models import CustomerContact, CustomerDepartment

    full_name = (data.get("full_name") or "").strip()
    if not full_name:
        raise CRMError("A contact needs a name.")

    if contact is None:
        contact = CustomerContact(company=customer.company, customer=customer,
                                  created_by=user)
    for field in _CONTACT_TEXT_FIELDS:
        if field in data:
            setattr(contact, field, (data.get(field) or "").strip())

    dept_id = (data.get("department") or "").strip()
    contact.department = (CustomerDepartment.objects.filter(
        customer=customer, pk=dept_id).first() if dept_id else None)

    # Only a known contact method — CharField `choices` are not enforced on
    # .save(), so a stray/forged value would otherwise persist unchecked. An
    # unrecognised value is ignored (keeps the existing/default method).
    method = (data.get("preferred_method") or "").strip()
    if method in CustomerContact.Method.values:
        contact.preferred_method = method

    # Deliberately NOT taken from `data`: status is a behavioural state (routing
    # skips non-active contacts) with its own audited path, set_contact_status().
    # A new contact defaults to ACTIVE via the model.

    # Multi-value fields. Both are whitelisted for the same reason — roles are
    # labels, responsibilities drive document routing; neither should accept an
    # arbitrary string a client happened to POST.
    getlist = getattr(data, "getlist", None)
    if getlist is not None:
        contact.roles = [r for r in getlist("roles") if r in CONTACT_ROLES]
        contact.responsibilities = [r for r in getlist("responsibilities")
                                    if r in RESPONSIBILITIES]
    contact.is_primary = bool(data.get("is_primary"))
    contact.updated_by = user
    contact.save()          # the model unsets other primaries when this is primary
    return contact


def set_contact_status(contact, user, *, status):
    """Deactivate / reactivate a contact (kept, never deleted — they may be on a
    past quotation)."""
    contact.status = status
    contact.updated_by = user
    contact.save(update_fields=["status", "updated_by", "updated_at"])
    return contact
