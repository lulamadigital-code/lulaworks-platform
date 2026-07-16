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
