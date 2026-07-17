from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.administration.services import next_number
from apps.core.events import publish

from .models import Quotation


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
        quote.lines.all().delete()
        pos = 0
        for line in lines:
            desc = (line.get("description") or "").strip()
            if not desc:
                continue
            pos += 1
            quote.lines.create(
                company=quote.company, position=pos, description=desc,
                qty=_dec(line.get("qty"), "1"), unit=line.get("unit") or "each",
                unit_price=_dec(line.get("unit_price"), "0"),
            )
    publish("QuotationUpdated", company=quote.company, subject=quote, actor=user,
            payload={"number": quote.number})
    return quote
