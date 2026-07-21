from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum

from apps.administration.services import next_number
from apps.core.events import publish

from .models import Quotation, QuotationLine, QuotationSource


def create_quotation(company, user, *, client_name, title="", site="", lines=None) -> Quotation:
    """Create a draft quotation: allocate a number (configurable engine), stamp
    the tenant (ambient), and emit a domain event (outbox)."""
    quote = Quotation.objects.create(
        company=company, number=next_number(company, "quotation"),
        client_name=client_name, title=title, site=site,
        created_by=user, updated_by=user,
    )
    for position, line in enumerate(lines or [], start=1):
        quote.lines.create(
            company=company, position=position,
            description=line["description"], qty=line.get("qty", 1),
            unit=line.get("unit", "each"), unit_price=line.get("unit_price", 0),
        )
    publish("QuotationCreated", company=company, subject=quote, actor=user,
            payload={"number": quote.number, "client": client_name})
    return quote


def _dec(raw, default="0"):
    try:
        return Decimal(str(raw).strip() or default)
    except (InvalidOperation, TypeError, AttributeError):
        return Decimal(default)


@transaction.atomic
def update_quotation(quote, user, *, title=None, client_name=None, site=None,
                     vat_rate=None, validity_date=None, notes=None, lines=None) -> Quotation:
    """Edit a draft quotation: header fields and a full replacement of the line
    set (the manager edits rows on the page). Lines with a blank description are
    dropped, so removing a line = clearing its description."""
    if title is not None:
        quote.title = title
    if client_name:
        quote.client_name = client_name
    if site is not None:
        quote.site = site
    if vat_rate is not None:
        quote.vat_rate = _dec(vat_rate, "15")
    if validity_date is not None:
        quote.validity_date = validity_date or None
    if notes is not None:
        quote.notes = notes
    quote.updated_by = user
    quote.save()

    if lines is not None:
        # Wholesale replacement PRESERVES costing. This used to delete every
        # line and recreate it from description/qty/unit/price alone, which
        # silently discarded cost, markup, discount, category and section — a
        # quotation with a real margin came back with an unknown one. Existing
        # lines are matched by description and updated in place instead.
        existing = {line.description.strip().lower(): line
                    for line in quote.lines.all()}
        keep, pos = set(), 0
        for row in lines:
            desc = (row.get("description") or "").strip()
            if not desc:
                continue
            pos += 1
            line = existing.get(desc.lower())
            if line is not None:
                line.position = pos
                line.qty = _dec(row.get("qty"), "1")
                line.unit = row.get("unit") or "each"
                line.unit_price = _dec(row.get("unit_price"), "0")
                line.save(update_fields=["position", "qty", "unit", "unit_price",
                                         "updated_at"])
                keep.add(line.pk)
            else:
                created = quote.lines.create(
                    company=quote.company, position=pos, description=desc,
                    qty=_dec(row.get("qty"), "1"), unit=row.get("unit") or "each",
                    unit_price=_dec(row.get("unit_price"), "0"),
                )
                keep.add(created.pk)
        # Only lines the caller actually dropped are removed.
        quote.lines.exclude(pk__in=keep).delete()
    publish("QuotationUpdated", company=quote.company, subject=quote, actor=user,
            payload={"number": quote.number})
    return quote


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 5 — the commercial gateway
#
# The quotation is the contract. Once awarded it is the single source of truth
# execution traces back to, which means two rules hold everywhere below:
#   * a locked quotation cannot be edited — only revised
#   * every state change is recorded, so "who approved this?" always answers
# ══════════════════════════════════════════════════════════════════════════════

from django.utils import timezone as _tz

#: Money rounding, matching the models.
TWO = Decimal("0.01")

from .models import (
    APPROVAL_CHAIN,
    DEFAULT_QUOTATION_TYPES,
    CustomerPurchaseOrder,
    LineCategory,
    QuotationEvent,
    QuotationSection,
    QuotationStatus,
    QuotationType,
    VatMode,
)


class QuotationError(ValueError):
    """A refusal with a reason a person can act on."""


def ensure_quotation_types(company) -> int:
    """Seed the default type catalogue. Idempotent; never removes custom types."""
    existing = set(QuotationType.objects.filter(company=company)
                   .values_list("key", flat=True))
    created = 0
    for position, (key, label, emphasis) in enumerate(DEFAULT_QUOTATION_TYPES):
        if key not in existing:
            QuotationType.objects.create(company=company, key=key, label=label,
                                         emphasis=emphasis, position=position)
            created += 1
    return created


def apply_type_template(quote, user) -> int:
    """Create the sections a job of this type is normally priced in.

    A blank quotation is a blank page; the type knows the shape the estimator
    is about to build. Existing sections are never duplicated, and nothing is
    removed — the template is a starting point, not a rule.
    """
    if not quote.quotation_type_id or not quote.quotation_type.emphasis:
        return 0
    existing = {s.name.lower() for s in quote.sections.all()}
    created = 0
    for position, name in enumerate(quote.quotation_type.emphasis,
                                    start=quote.sections.count() + 1):
        if name.lower() in existing:
            continue
        QuotationSection.objects.create(company=quote.company, quotation=quote,
                                        name=name, position=position,
                                        created_by=user, updated_by=user)
        created += 1
    return created


def move_line(line, *, direction) -> None:
    """Reorder by swapping with the neighbour. No drag-and-drop, no JavaScript,
    and it works on a phone in a workshop."""
    siblings = list(line.quotation.lines.all())
    index = next((i for i, row in enumerate(siblings) if row.pk == line.pk), None)
    if index is None:
        return
    target = index - 1 if direction == "up" else index + 1
    if not 0 <= target < len(siblings):
        return
    other = siblings[target]
    line.position, other.position = other.position, line.position
    # Positions can collide on legacy rows; renumber the pair definitively.
    if line.position == other.position:
        line.position, other.position = target + 1, index + 1
    line.save(update_fields=["position"])
    other.save(update_fields=["position"])


def record_event(quote, *, verb, note="", actor=None, from_status="", to_status="",
                 customer_contact=None) -> QuotationEvent:
    return QuotationEvent.objects.create(
        company=quote.company, quotation=quote, verb=verb, note=note[:500],
        actor=actor, from_status=from_status, to_status=to_status,
        customer_contact=customer_contact,
    )


def guard_editable(quote) -> None:
    """Raise unless the quotation may still be changed.

    Called by every mutating path. An awarded quotation is what was contracted;
    changing it silently would break the audit trail that the whole module
    exists to provide.
    """
    if quote.is_locked:
        raise QuotationError(
            f"{quote.display_number} is {quote.get_status_display().lower()} and "
            "cannot be edited. Create a revision instead.")


# ── Lifecycle ────────────────────────────────────────────────────────────────

def next_statuses(quote) -> list:
    """The sensible next steps — what the UI offers as buttons."""
    status = quote.status
    if status in (QuotationStatus.SENT, QuotationStatus.ISSUED):
        return [QuotationStatus.ACCEPTED, QuotationStatus.REVISION_REQUESTED,
                QuotationStatus.REJECTED, QuotationStatus.EXPIRED]
    if status == QuotationStatus.ACCEPTED:
        return [QuotationStatus.AWARDED]
    if status == QuotationStatus.REVISION_REQUESTED:
        return [QuotationStatus.DRAFT]
    from .models import LOCKED_STATUSES
    if status in LOCKED_STATUSES:
        return []                        # the outcome is already recorded
    try:
        index = APPROVAL_CHAIN.index(status)
    except ValueError:
        return []
    return [APPROVAL_CHAIN[index + 1]] if index + 1 < len(APPROVAL_CHAIN) else []


@transaction.atomic
def transition(quote, user, *, to_status, note="", customer_contact=None):
    """Move the quotation through its lifecycle, recording why.

    Refuses to issue a quotation with no lines — an empty quotation reaching a
    customer is worse than a late one.
    """
    from .models import LOCKED_STATUSES

    if quote.status == to_status:
        return quote
    if quote.status in LOCKED_STATUSES and to_status != QuotationStatus.AWARDED:
        raise QuotationError(
            f"{quote.display_number} is {quote.get_status_display().lower()} — "
            "its outcome is already recorded.")
    if to_status in (QuotationStatus.ISSUED, QuotationStatus.SENT) \
            and not quote.lines.exists():
        raise QuotationError("This quotation has no line items yet.")

    previous = quote.status
    quote.status = to_status
    fields = ["status", "updated_at"]

    if to_status in (QuotationStatus.ISSUED, QuotationStatus.SENT):
        quote.issued_at = _tz.now()
        fields.append("issued_at")
    if to_status in (QuotationStatus.ACCEPTED, QuotationStatus.REJECTED,
                     QuotationStatus.LOST, QuotationStatus.AWARDED):
        quote.decided_at = _tz.now()
        fields.append("decided_at")
    if to_status in (QuotationStatus.REJECTED, QuotationStatus.LOST) and note:
        quote.lost_reason = note[:255]
        fields.append("lost_reason")
    if user is not None:
        quote.updated_by = user
        fields.append("updated_by")

    quote.save(update_fields=fields)
    record_event(quote, verb="status_changed", note=note, actor=user,
                 from_status=previous, to_status=to_status,
                 customer_contact=customer_contact)
    publish("QuotationStatusChanged", company=quote.company, subject=quote, actor=user,
            payload={"quotation": quote.number, "from": previous, "to": to_status})
    return quote


@transaction.atomic
def create_revision(quote, user, *, reason=""):
    """A new version that SUPERSEDES rather than overwrites.

    The customer asked for a change; the previously issued numbers must remain
    exactly as they were sent, so the revision is a new row pointing back.
    """
    new = Quotation.objects.create(
        company=quote.company, number=quote.number, revision=quote.revision + 1,
        supersedes=quote, title=quote.title, client_name=quote.client_name,
        customer=quote.customer, branch=quote.branch, customer_site=quote.customer_site,
        department=quote.department, contact=quote.contact, site=quote.site,
        quotation_type=quote.quotation_type, source=quote.source,
        source_rfq=quote.source_rfq, scope_of_work=quote.scope_of_work,
        vat_mode=quote.vat_mode, vat_rate=quote.vat_rate, currency=quote.currency,
        validity_date=quote.validity_date, payment_terms_days=quote.payment_terms_days,
        customer_reference=quote.customer_reference, rfq_reference=quote.rfq_reference,
        project_reference=quote.project_reference, prepared_by=user,
        exclusions=quote.exclusions, assumptions=quote.assumptions, notes=quote.notes,
        status=QuotationStatus.DRAFT, created_by=user, updated_by=user,
    )
    _copy_contents(quote, new, user)
    record_event(new, verb="revised", note=reason, actor=user,
                 from_status=quote.status, to_status=QuotationStatus.DRAFT)
    record_event(quote, verb="superseded", note=f"Revision {new.revision} created",
                 actor=user)
    return new


@transaction.atomic
def duplicate(quote, user, *, number=None, customer=None):
    """Copy an existing quotation for recurring work — a fresh quotation, not a
    revision, because it is a different job."""
    from apps.administration.services import next_number

    new = Quotation.objects.create(
        company=quote.company, number=number or next_number(quote.company, "quotation"),
        title=quote.title, client_name=(customer.name if customer else quote.client_name),
        customer=customer or quote.customer,
        branch=None if customer else quote.branch,
        customer_site=None if customer else quote.customer_site,
        department=None if customer else quote.department,
        contact=None if customer else quote.contact,
        site=quote.site, quotation_type=quote.quotation_type,
        source=QuotationSource.COPY, scope_of_work=quote.scope_of_work,
        vat_mode=quote.vat_mode, vat_rate=quote.vat_rate, currency=quote.currency,
        exclusions=quote.exclusions, assumptions=quote.assumptions,
        prepared_by=user, status=QuotationStatus.DRAFT,
        created_by=user, updated_by=user,
    )
    _copy_contents(quote, new, user)
    record_event(new, verb="copied", note=f"Copied from {quote.display_number}",
                 actor=user)
    return new


def _copy_contents(source, target, user):
    """Sections and lines, preserving grouping and order."""
    section_map = {}
    for section in source.sections.all():
        section_map[section.id] = QuotationSection.objects.create(
            company=target.company, quotation=target, name=section.name,
            position=section.position, notes=section.notes,
            created_by=user, updated_by=user,
        )
    for line in source.lines.all():
        QuotationLine.objects.create(
            company=target.company, quotation=target,
            section=section_map.get(line.section_id),
            position=line.position, item_no=line.item_no, description=line.description,
            category=line.category, qty=line.qty, unit=line.unit,
            unit_cost=line.unit_cost, markup_pct=line.markup_pct,
            discount_pct=line.discount_pct, unit_price=line.unit_price,
            supplier=line.supplier, labour_category=line.labour_category,
            equipment=line.equipment, notes=line.notes,
            created_by=user, updated_by=user,
        )


# ── The award: where a quotation becomes work ────────────────────────────────

@transaction.atomic
def record_purchase_order(quote, user, *, po_number, value=None, po_date=None,
                          issued_by=None, department=None, document=None, notes=""):
    """Capture the customer's PO. Several may arrive against one quotation when
    work is awarded in stages."""
    if not po_number.strip():
        raise QuotationError("A PO number is required.")
    po = CustomerPurchaseOrder.objects.create(
        company=quote.company, quotation=quote, po_number=po_number.strip(),
        value=value if value is not None else quote.total, po_date=po_date,
        issued_by=issued_by or quote.contact, department=department or quote.department,
        document=document, notes=notes, created_by=user, updated_by=user,
    )
    record_event(quote, verb="po_received", actor=user,
                 note=f"PO {po.po_number} for {po.value}")
    publish("CustomerPOReceived", company=quote.company, subject=quote, actor=user,
            payload={"quotation": quote.number, "po": po.po_number,
                     "value": str(po.value)})
    return po


def award_summary(quote) -> dict:
    """What awarding this quotation would hand over to execution — shown to a
    human BEFORE anything is created, because creating work is not reversible."""
    lines = list(quote.lines.all())
    by_category = {}
    for line in lines:
        by_category.setdefault(line.get_category_display(), []).append(line)
    return {
        "quotation": quote,
        "customer": quote.customer,
        "site": quote.customer_site or quote.site,
        "contact": quote.contact,
        "line_count": len(lines),
        "by_category": by_category,
        "value": quote.total,
        "cost": quote.total_cost,
        "margin_pct": quote.margin_pct,
        "customer_pos": list(quote.customer_pos.all()),
        "has_po": quote.customer_pos.exists(),
        "scope_of_work": quote.scope_of_work,
    }


@transaction.atomic
def award_to_work(quote, user, *, create_project=True, work_name=""):
    """Turn an awarded quotation into executable work.

    This is the handover the module exists for: customer, site, contact, scope
    and the priced items all move across in one step, so nobody retypes them and
    everything downstream traces back to this quotation.

    The quotation LOCKS on award. From here changes are revisions or variations.
    """
    from apps.execution.services import create_work
    from apps.projects.services import award_quotation

    if not quote.customer_pos.exists():
        raise QuotationError(
            "Record the customer's purchase order first — work created without "
            "one cannot be invoiced against anything.")

    project = None
    if create_project:
        project = award_quotation(
            quote.company, user, quotation=quote,
            work_type=(quote.quotation_type.label if quote.quotation_type else ""),
            site=str(quote.customer_site) if quote.customer_site else quote.site,
        )
        if quote.customer_id and not project.customer_id:
            project.customer = quote.customer
            project.save(update_fields=["customer"])

    task = create_work(
        quote.company, user,
        name=work_name or quote.title or f"Work for {quote.display_number}",
        description=quote.scope_of_work,
        origin="rfq" if quote.source_rfq_id else "project",
        project=project,
        is_billable=True,
        client_name=quote.client_name,
        site=str(quote.customer_site) if quote.customer_site else quote.site,
        owner=user,
    )

    if quote.status != QuotationStatus.AWARDED:
        transition(quote, user, to_status=QuotationStatus.AWARDED,
                   note="Awarded and handed to execution")
    record_event(quote, verb="work_created", actor=user,
                 note=f"Work '{task.name}' created"
                      + (f" under {project.number}" if project else ""))
    return {"project": project, "task": task, "quotation": quote}


def traceability(quote) -> dict:
    """Everything that traces back to this quotation.

    The architectural claim of the module is that after award, every task,
    material, hour, invoice and payment leads back here. This is the query that
    makes the claim checkable rather than aspirational.
    """
    from apps.execution.models import Task, Timesheet
    from apps.finance.models import Invoice
    from apps.procurement.models import PurchaseOrder
    from apps.projects.models import Project

    projects = list(Project.objects.filter(quotation=quote))
    tasks = list(Task.objects.filter(project__in=projects)) if projects else []
    supplier_pos = list(PurchaseOrder.objects.filter(quotation=quote))
    invoices = list(Invoice.objects.filter(project__in=projects)) if projects else []
    timesheets = list(Timesheet.objects.filter(task__in=tasks)) if tasks else []

    labour_hours = sum((t.total_hours for t in timesheets), Decimal("0"))
    invoiced = sum((i.total for i in invoices), Decimal("0"))

    return {
        "quotation": quote,
        "customer_pos": list(quote.customer_pos.all()),
        "projects": projects,
        "tasks": tasks,
        "supplier_pos": supplier_pos,
        "invoices": invoices,
        "labour_hours": labour_hours,
        "invoiced": invoiced,
        "quoted_value": quote.total,
        "quoted_cost": quote.total_cost,
        # Quoted vs actual — the post-project question every contractor asks.
        "variance": (invoiced - quote.total).quantize(TWO) if invoices else None,
    }


def pipeline(company=None) -> dict:
    """The quotation dashboard: what is open, what was won, and how often."""
    quotes = list(Quotation.objects.all().prefetch_related("lines"))
    won = [q for q in quotes if q.status == QuotationStatus.AWARDED]
    lost = [q for q in quotes if q.status in (QuotationStatus.REJECTED,
                                              QuotationStatus.LOST)]
    open_quotes = [q for q in quotes if q.is_open]
    decided = len(won) + len(lost)

    awaiting = [q for q in quotes if q.status in
                (QuotationStatus.ISSUED, QuotationStatus.SENT)]
    approval = [q for q in quotes if q.status in
                (QuotationStatus.REVIEW, QuotationStatus.MANAGER_APPROVAL,
                 QuotationStatus.COMMERCIAL_APPROVAL)]

    return {
        "total": len(quotes),
        "open": open_quotes,
        "open_value": sum((q.total for q in open_quotes), Decimal("0")).quantize(TWO),
        "won": won,
        "won_value": sum((q.total for q in won), Decimal("0")).quantize(TWO),
        "lost": lost,
        "awaiting_customer": awaiting,
        "awaiting_approval": approval,
        "expired": [q for q in quotes if q.is_expired],
        "win_rate": round(100 * len(won) / decided) if decided else None,
    }


# ── Quoted vs actual: the post-project question ──────────────────────────────

#: Which quotation line categories each actual source answers for.
_ACTUAL_SOURCES = {
    "labour": "Approved timesheets",
    "material": "Supplier invoices",
    "consumable": "Supplier invoices",
    "equipment": "Supplier invoices",
    "subcontractor": "Supplier invoices",
}


def quoted_vs_actual(quote) -> dict:
    """What we said it would cost, against what it did.

    Deliberately honest about coverage: labour actuals come from APPROVED
    timesheets and material actuals from supplier invoices, so anything not yet
    captured shows as a gap rather than as a saving. A variance report that
    counts missing data as profit is worse than none.
    """
    from apps.execution.models import Timesheet
    from apps.procurement.models import SupplierInvoice
    from apps.projects.models import Project

    projects = list(Project.objects.filter(quotation=quote))

    quoted = {}
    for line in quote.lines.all():
        row = quoted.setdefault(line.category, {"cost": Decimal("0"),
                                                "price": Decimal("0")})
        row["cost"] += line.total_cost
        row["price"] += line.line_total

    # ── Actual labour: approved timesheets only. Unapproved hours are claims,
    # not costs, and counting them would move the number every time someone
    # keys a sheet in.
    labour_cost, labour_hours, unapproved_hours = Decimal("0"), Decimal("0"), Decimal("0")
    if projects:
        for sheet in Timesheet.objects.filter(task__project__in=projects):
            if sheet.approved:
                labour_cost += sheet.labour_cost
                labour_hours += sheet.total_hours
            else:
                unapproved_hours += sheet.total_hours

    # ── Actual materials/equipment: what suppliers have actually invoiced.
    material_cost = Decimal("0")
    if quote.pk:
        material_cost = SupplierInvoice.objects.filter(
            purchase_order__quotation=quote
        ).aggregate(t=Sum("total_excl"))["t"] or Decimal("0")

    rows = []
    for category, values in sorted(quoted.items()):
        if category == "labour":
            actual = labour_cost
            source = _ACTUAL_SOURCES["labour"]
            captured = bool(labour_cost) or bool(labour_hours)
        elif category in ("material", "consumable", "equipment", "subcontractor"):
            # Supplier invoices are not split by our quotation categories, so
            # they are reported once against materials rather than guessed apart.
            actual = material_cost if category == "material" else Decimal("0")
            source = _ACTUAL_SOURCES.get(category, "")
            captured = bool(actual)
        else:
            actual, source, captured = Decimal("0"), "not tracked", False

        rows.append({
            "category": category,
            "label": dict(LineCategory.choices).get(category, category.title()),
            "quoted_cost": values["cost"].quantize(TWO),
            "quoted_price": values["price"].quantize(TWO),
            "actual_cost": Decimal(actual).quantize(TWO),
            "variance": (Decimal(actual) - values["cost"]).quantize(TWO)
                        if captured else None,
            "source": source,
            "captured": captured,
        })

    total_actual = labour_cost + material_cost
    fully_captured = all(row["captured"] for row in rows) if rows else False

    return {
        "quotation": quote,
        "rows": rows,
        "quoted_cost": quote.total_cost,
        "quoted_price": quote.total,
        "actual_cost": total_actual.quantize(TWO),
        "labour_hours": labour_hours,
        "unapproved_hours": unapproved_hours,
        # None while anything is uncaptured — see the docstring.
        "actual_margin_pct": (
            ((quote.total - quote.vat_amount - total_actual)
             / (quote.total - quote.vat_amount) * 100).quantize(TWO)
            if fully_captured and quote.total > quote.vat_amount else None),
        "fully_captured": fully_captured,
        "caveat": None if fully_captured else (
            "Some costs are not captured yet, so the actual figure is a floor, "
            "not a total."),
    }
