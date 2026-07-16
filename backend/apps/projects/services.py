"""Project services: the award transition (Quotation → Project) that opens the
compliance gate (BUSINESS_WORKFLOW §1)."""

from django.db import transaction
from django.utils import timezone

from apps.administration.services import next_number
from apps.core.events import publish
from apps.quotes.models import QuotationStatus

from .models import Project


@transaction.atomic
def award_quotation(company, user, *, quotation, work_type="", mine="", site="") -> Project:
    """Award a quotation and create its Project. Fires `ProjectCreated`, which the
    compliance engine consumes to auto-compose the readiness checklist. The
    project starts `pending_compliance` — the hard execution gate is closed until
    Work Readiness passes or an authorised override is recorded."""
    quotation.status = QuotationStatus.AWARDED
    quotation.updated_by = user
    quotation.save(update_fields=["status", "updated_by", "updated_at"])

    project = Project.objects.create(
        company=company, number=next_number(company, "project"),
        quotation=quotation, title=quotation.title, client_name=quotation.client_name,
        site=site or quotation.site, mine=mine, work_type=work_type,
        awarded_at=timezone.now(), created_by=user, updated_by=user,
    )
    publish("ProjectCreated", company=company, subject=project, actor=user,
            payload={"number": project.number, "quotation": quotation.number,
                     "work_type": work_type})

    # Compose the compliance checklist for the new project (Module 8 §5).
    from apps.compliance.services import discover_requirements
    discover_requirements(project, user)
    return project
