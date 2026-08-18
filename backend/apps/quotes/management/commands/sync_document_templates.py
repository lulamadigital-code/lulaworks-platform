"""Top-up the built-in document-template library for existing companies.

seed_document_templates only runs for a company that has NONE, so when new
built-in template families ship, already-seeded companies never receive them.
This command adds only the missing ones, touching nothing a company already has
and never moving a chosen default. Idempotent — safe to run on every deploy.

(To also RETIRE the old catalogue and repair defaults, use the one-shot
`refresh_document_templates` command instead.)

    python manage.py sync_document_templates                # every company
    python manage.py sync_document_templates --company <id>  # just one
"""

from django.core.management.base import BaseCommand, CommandError

from apps.core.context import tenant_scope
from apps.identity.models import Company
from apps.quotes.document_templates import sync_builtin_templates


class Command(BaseCommand):
    help = "Add any missing built-in document templates to existing companies."

    def add_arguments(self, parser):
        parser.add_argument("--company", help="Only this company UUID (default: all).")

    def handle(self, *args, **options):
        if options.get("company"):
            try:
                companies = [Company.objects.get(id=options["company"])]
            except Company.DoesNotExist as exc:
                raise CommandError("No such company.") from exc
        else:
            companies = list(Company.objects.all())

        total = 0
        for company in companies:
            with tenant_scope(company.id):
                added = sync_builtin_templates(company)
            total += added
            if added:
                self.stdout.write(f"  {company.name}: +{added} template(s)")
        self.stdout.write(self.style.SUCCESS(
            f"Done. Added {total} template(s) across {len(companies)} company(ies)."
        ))
