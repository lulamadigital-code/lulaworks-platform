import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum

from apps.core.events import publish

from .models import (
    CommercialDocument,
    Quotation,
    QuotationLine,
    QuotationSource,
)

#: A quotation reference is the company's document prefix (2–4 letters) then six
#: random digits, e.g. LPS845192. It is the parent reference every downstream
#: commercial document (invoice, delivery note, payment) is built from.
QUOTATION_NUMBER_RE = re.compile(r"^[A-Za-z]{2,4}\d{6}$")

#: Per-document-type prefixes, applied to the quotation reference so every child
#: document is visually identifiable but stays tied to the one commercial ref:
#: INV-LPS845192-01, DN-LPS845192-01, PAY-LPS845192-01, CN-LPS845192-01.
COMMERCIAL_DOC_PREFIXES = {
    "invoice": "INV", "delivery": "DN", "payment": "PAY",
    "credit": "CN", "purchase_order": "PO",
}


def quotation_prefix_for(company) -> str:
    """The company's document prefix — its own two-to-four letters that begin
    every reference. Uses the configured Company.document_prefix when set, else
    derives from the company name, falling back to QT."""
    configured = "".join(c for c in (getattr(company, "document_prefix", "") or "")
                         if c.isalpha()).upper()[:4]
    if len(configured) >= 2:
        return configured
    letters = [c for c in (company.name or "") if c.isalpha()]
    return ("".join(letters[:3]).upper() + "QT")[:max(2, len(letters[:3]))] or "QT"


def next_quotation_number(company) -> str:
    """Prefix + six RANDOM digits, guaranteed unique and never reused: a value
    already handed out stays in the table forever, so the uniqueness check that
    rejects a collision also prevents reuse. Random (not sequential) so a
    competitor cannot count how many quotations we issue."""
    import secrets

    prefix = quotation_prefix_for(company)
    for _ in range(50):
        number = f"{prefix}{secrets.randbelow(1_000_000):06d}"
        if not Quotation.all_objects.filter(company=company, number=number).exists():
            return number
    # Astronomically unlikely; fall back to a longer random tail rather than loop.
    return f"{prefix}{secrets.randbelow(1_000_000):06d}{secrets.randbelow(10)}"


def commercial_ref(quote, kind: str, seq: int = 1) -> str:
    """The reference for a document generated from this quotation — e.g.
    commercial_ref(q, "invoice") → "INV-LPS845192-01". Ties every child document
    to the single commercial reference while keeping it identifiable by type."""
    prefix = COMMERCIAL_DOC_PREFIXES.get(kind, kind.upper()[:3])
    return f"{prefix}-{quote.number}-{seq:02d}"


#: How close a PO value must be to count as "matching" the quotation (rounding).
_PO_MATCH_TOLERANCE = Decimal("1.00")


def matching_purchase_order(quote):
    """The customer PO whose value matches this quotation's price — either the
    net total or the VAT-inclusive invoice total, since a customer may raise a
    PO for either. None if no PO matches, which is what gates invoicing."""
    targets = {quote.total, quote.invoice_total}
    for po in quote.customer_pos.all():
        if po.value and any(abs(po.value - t) <= _PO_MATCH_TOLERANCE for t in targets):
            return po
    return None


def can_generate_documents(quote) -> bool:
    """A tax invoice or delivery note may be raised once the quotation is
    finalized. A purchase order is optional — many smaller contractors work from
    an approved quotation, an email or a phone instruction, so a PO must never
    block invoicing. When a PO IS on file it is linked automatically."""
    return quote.is_finalized


def _po_for_document(quote):
    """The PO to reference on a generated document, if any — prefer one whose
    value matches the quotation, else the first on file, else none."""
    return matching_purchase_order(quote) or quote.customer_pos.first()


def _guard_generatable(quote):
    """Shared preconditions for raising any document off a quotation: a customer
    that is on file and engageable, and at least one line item."""
    from apps.customers.models import CustomerStatus
    customer = quote.customer
    if customer is None:
        raise QuotationError("This quotation has no customer on file.")
    if customer.status in (CustomerStatus.ON_HOLD, CustomerStatus.DORMANT,
                           CustomerStatus.BLACKLISTED):
        raise QuotationError(
            f"{customer.display_name} is {customer.get_status_display().lower()} — "
            "documents cannot be raised for this customer.")
    if not quote.lines.exists():
        raise QuotationError("This quotation has no line items.")


#: How many times to re-allocate a sequence number before giving up. Five is far
#: beyond what any realistic contention needs — two callers racing collide once,
#: not five times — but it costs nothing to be generous.
_NUMBERING_ATTEMPTS = 5


def _create_numbered_document(quote, kind, user, **extra):
    """Allocate the next sequence for this kind of document and create it,
    retrying on the (company, number) collision two racing callers can hit.

    seq is derived from a count, so two callers can compute the same one and race
    to insert it; the unique constraint lets exactly one win. Each attempt runs in
    its own savepoint, so a loser's failed insert rolls back cleanly. The seq is
    recomputed every pass — the fresh count picks up the number a winner just
    committed, and the attempt offset guarantees forward progress past a number
    that is already taken rather than re-trying the same one."""
    last_error = None
    for attempt in range(_NUMBERING_ATTEMPTS):
        seq = quote.commercial_documents.filter(kind=kind).count() + 1 + attempt
        try:
            with transaction.atomic():
                return CommercialDocument.objects.create(
                    company=quote.company, quotation=quote, kind=kind,
                    number=commercial_ref(quote, kind, seq),
                    purchase_order=_po_for_document(quote),
                    created_by=user, updated_by=user, **extra)
        except IntegrityError as exc:
            last_error = exc          # someone took this number first — re-count
    raise last_error


@transaction.atomic
def create_invoice_document(quote, user):
    """Raise a tax invoice from the quotation — number inherited from the parent
    reference, any PO linked. The figures come from the quotation; nothing is
    typed."""
    if not can_generate_documents(quote):
        raise QuotationError("Approve the quotation before raising a tax invoice.")
    _guard_generatable(quote)
    if quote.invoice_total <= 0:
        raise QuotationError("The invoice total is zero — there is nothing to invoice.")
    doc = _create_numbered_document(quote, CommercialDocument.Kind.INVOICE, user)
    record_event(quote, verb="invoice_created", actor=user, note=doc.number)
    return doc


@transaction.atomic
def create_delivery_document(quote, user, **fields):
    """Raise a delivery note from the quotation. Carries operational quantities,
    never prices. A PO is optional."""
    if not can_generate_documents(quote):
        raise QuotationError("Approve the quotation before raising a delivery note.")
    _guard_generatable(quote)
    # A delivery note follows the tax invoice — it may only be raised once the
    # invoice for this quotation exists.
    if not quote.commercial_documents.filter(
            kind=CommercialDocument.Kind.INVOICE).exists():
        raise QuotationError("Create the tax invoice before the delivery note.")
    # A delivery note needs a destination: the address given now, the quotation's
    # customer site, or its free-text site.
    destination = (fields.get("delivery_address", "").strip()
                   or (str(quote.customer_site) if quote.customer_site_id else "")
                   or quote.site)
    if not destination:
        raise QuotationError("A delivery site is required for a delivery note.")
    doc = _create_numbered_document(
        quote, CommercialDocument.Kind.DELIVERY, user,
        delivery_date=fields.get("delivery_date") or None,
        delivery_address=fields.get("delivery_address", "").strip(),
        driver=fields.get("driver", "").strip(),
        receiver_name=fields.get("receiver_name", "").strip(),
        delivery_notes=fields.get("delivery_notes", "").strip())
    record_event(quote, verb="delivery_created", actor=user, note=doc.number)
    return doc


#: The lifecycle a generated document walks: draft → approved. Approval is the
#: final step and locks it — thereafter it is the read-only commercial record.
#: (There is no separate finalize or send step.) FINALIZED/SENT remain terminal
#: for any legacy rows.
_COMDOC_NEXT = {"draft": ["approved"], "approved": [],
                "finalized": [], "sent": []}


def commercial_document_next_statuses(doc) -> list:
    return _COMDOC_NEXT.get(doc.status, [])


def transition_commercial_document(doc, user, to_status):
    """Advance a tax invoice or delivery note through its lifecycle. Approving
    locks it — thereafter it is the read-only commercial record."""
    if to_status not in _COMDOC_NEXT.get(doc.status, []):
        raise QuotationError(
            f"A {doc.get_status_display().lower()} document cannot move to "
            f"'{to_status}'.")
    doc.status = to_status
    doc.updated_by = user
    doc.save(update_fields=["status", "updated_by"])
    return doc


def quotation_number_available(company, number: str, *, exclude=None) -> bool:
    """A number may be reused across revisions of the same quotation but never
    across two different ones."""
    qs = Quotation.objects.filter(company=company, number=number)
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    return not qs.exists()


def create_quotation(company, user, *, client_name, title="", site="", lines=None,
                     number=None, source=None) -> Quotation:
    """Create a draft quotation: allocate a number (configurable engine), stamp
    the tenant (ambient), and emit a domain event (outbox).

    `number` lets an authorised caller override the auto-allocated value (the
    view is responsible for the permission check and for validating format and
    uniqueness); when None the numbering engine allocates the next one."""
    fields = dict(
        company=company, number=number or next_quotation_number(company),
        client_name=client_name, title=title, site=site,
        created_by=user, updated_by=user,
    )
    if source is not None:
        fields["source"] = source
    quote = Quotation.objects.create(**fields)
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
    by_key = {t.key: t for t in QuotationType.objects.filter(company=company)}
    created = 0
    for position, (key, label, emphasis) in enumerate(DEFAULT_QUOTATION_TYPES):
        existing = by_key.get(key)
        if existing is None:
            QuotationType.objects.create(company=company, key=key, label=label,
                                         emphasis=emphasis, position=position)
            created += 1
        elif not existing.emphasis and emphasis:
            # Backfill a type seeded before emphasis existed, so its template
            # sections and the create-page hint start working — without
            # touching one an administrator has customised.
            existing.emphasis = emphasis
            existing.save(update_fields=["emphasis"])
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

    Called by every mutating path. A finalized quotation is the document the
    customer was sent; changing it silently would break the audit trail that the
    whole module exists to provide. Past finalize, a change is a new revision.
    """
    if quote.is_finalized:
        raise QuotationError(
            f"{quote.display_number} is {quote.get_status_display().lower()} and "
            "cannot be edited. Create a revision instead.")


# ── Lifecycle ────────────────────────────────────────────────────────────────

def next_statuses(quote) -> list:
    """The sensible next steps — what the UI offers as buttons."""
    status = quote.status
    # Approval is the final commercial version; from there the customer decides.
    if status in (QuotationStatus.APPROVED, QuotationStatus.SENT,
                  QuotationStatus.ISSUED):
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
    from .models import FINALIZED_STATUSES, LOCKED_STATUSES

    # Lock the row for the length of the transition so two concurrent approvals
    # (or any two racing transitions) cannot both proceed; re-read the status
    # under the lock so a duplicate approve is caught by the equality check below.
    Quotation.objects.select_for_update().filter(pk=quote.pk).exists()
    quote.refresh_from_db(fields=["status"])

    if quote.status == to_status:
        return quote
    if quote.status in LOCKED_STATUSES and to_status != QuotationStatus.AWARDED:
        raise QuotationError(
            f"{quote.display_number} is {quote.get_status_display().lower()} — "
            "its outcome is already recorded.")
    # A finalized (approved+) quotation cannot be moved backwards through the
    # approval chain — that would silently reopen a committed commercial
    # document. A change is a new revision, never a reversal.
    _BACKWARD = {QuotationStatus.DRAFT, QuotationStatus.REVIEW,
                 QuotationStatus.MANAGER_APPROVAL, QuotationStatus.COMMERCIAL_APPROVAL}
    if quote.status in FINALIZED_STATUSES and to_status in _BACKWARD:
        raise QuotationError(
            f"{quote.display_number} is {quote.get_status_display().lower()} and "
            "cannot be reopened. Create a revision instead.")
    if to_status in (QuotationStatus.APPROVED, QuotationStatus.ISSUED,
                     QuotationStatus.SENT) and not quote.lines.exists():
        raise QuotationError("This quotation has no line items yet.")

    previous = quote.status
    quote.status = to_status
    fields = ["status", "updated_at"]

    # Approval is the final commercial version, so it stamps the issued date the
    # validity period counts from (there is no separate issue/send step).
    if to_status in (QuotationStatus.APPROVED, QuotationStatus.ISSUED,
                     QuotationStatus.SENT):
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
    new = Quotation.objects.create(
        company=quote.company, number=number or next_quotation_number(quote.company),
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
    work is awarded in stages — but the same PO number is never attached twice."""
    number = po_number.strip()
    if not number:
        raise QuotationError("A PO number is required.")
    # Never create a duplicate: the same PO number cannot be attached to this
    # quotation more than once.
    if quote.customer_pos.filter(po_number__iexact=number).exists():
        raise QuotationError(
            f"PO {number} is already attached to {quote.display_number}.")
    po = CustomerPurchaseOrder.objects.create(
        company=quote.company, quotation=quote, po_number=number,
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


# ── Bulk line entry ──────────────────────────────────────────────────────────

def parse_pasted_lines(text: str) -> list[dict]:
    """Parse rows pasted from a spreadsheet or a BOQ.

    Accepts tab-separated (what you get pasting from Excel), comma-separated,
    or two-or-more spaces. Column order is the one estimators actually use:

        description | qty | unit | unit cost | markup%

    Anything after the description is optional, so a bare list of descriptions
    works too. Rows that are clearly a header are skipped rather than priced.
    """
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "\t" in line:
            cells = [c.strip() for c in line.split("\t")]
        elif "," in line and not re.match(r"^[^,]+,\d", line):
            cells = [c.strip() for c in line.split(",")]
        else:
            cells = [c.strip() for c in re.split(r"\s{2,}", line)]

        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        description = cells[0]
        # A header row prices nothing.
        if description.lower() in ("description", "item", "item description",
                                   "desc", "particulars"):
            continue

        def number(index, default="0"):
            if len(cells) <= index:
                return Decimal(default)
            cleaned = re.sub(r"[^\d.,-]", "", cells[index]).replace(" ", "")
            # SA formatting: "1 234,56" → comma is the decimal separator.
            if "," in cleaned and "." not in cleaned:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
            try:
                return Decimal(cleaned or default)
            except InvalidOperation:
                return Decimal(default)

        rows.append({
            "description": description[:500],
            "qty": number(1, "1") or Decimal("1"),
            "unit": (cells[2] if len(cells) > 2 and not cells[2][:1].isdigit()
                     else "each")[:32],
            "unit_cost": number(3),
            "markup_pct": number(4),
        })
    return rows


def parse_grid_lines(text: str) -> list[dict]:
    """Rows from the create-page item grid: description | qty | unit | unit price.

    Unlike parse_pasted_lines (which reads a cost + markup% BOQ), the fourth
    column here is the SELLING price the estimator typed, so it maps straight to
    unit_price — not unit_cost. Getting this wrong is why a line could show a
    zero unit price but a non-zero amount."""
    rows = []
    for raw in (text or "").splitlines():
        cells = [c.strip() for c in raw.split("\t")]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue

        def num(index):
            if len(cells) <= index:
                return Decimal("0")
            cleaned = re.sub(r"[^\d.,-]", "", cells[index]).replace(" ", "")
            if "," in cleaned and "." not in cleaned:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
            try:
                return Decimal(cleaned or "0")
            except InvalidOperation:
                return Decimal("0")

        rows.append({
            "description": cells[0][:500],
            "qty": num(1) or Decimal("1"),
            "unit": (cells[2] if len(cells) > 2 else "each")[:32],
            "unit_price": num(3),
        })
    return rows


@transaction.atomic
def add_lines_bulk(quote, user, rows, *, section=None) -> int:
    """Add many priced lines at once. Blank descriptions are skipped."""
    guard_editable(quote)
    position = quote.lines.count()
    created = 0
    for row in rows:
        description = (row.get("description") or "").strip()
        if not description:
            continue
        position += 1
        QuotationLine.objects.create(
            company=quote.company, quotation=quote, section=section,
            position=position, description=description[:500],
            category=row.get("category", "other"),
            qty=row.get("qty") or Decimal("1"),
            unit=row.get("unit") or "each",
            unit_cost=row.get("unit_cost") or Decimal("0"),
            markup_pct=row.get("markup_pct") or Decimal("0"),
            unit_price=row.get("unit_price") or Decimal("0"),
            created_by=user, updated_by=user,
        )
        created += 1
    return created
