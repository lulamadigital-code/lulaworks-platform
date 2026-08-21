"""Migrate every company off the RETIRED document-template catalogue and onto the
twelve original Lulaworks families.

Background: the old catalogue mixed generic ReportLab presets (Classic / Modern /
Corporate …) with "house styles" that borrowed third-party product names. Those
names must never appear in a customer-facing catalogue, so this command:

  1. Seeds the twelve original families (Horizon, Elevate, Forge, …) for every
     company that is missing them.
  2. Removes the retired built-ins (every seeded template with no `family`):
       • deleted outright when nothing references it, OR
       • kept but ARCHIVED and RENAMED to a neutral label when a finalised document
         is pinned to one of its versions — so an already-issued quotation /
         invoice / DN never changes, yet no old catalogue name lingers in the data.
  3. Repairs the default for each document type, pointing it at the Horizon family
     when the retired default was removed.

Idempotent and safe to re-run — a company already on the families is a no-op.

    python manage.py refresh_document_templates                 # every company
    python manage.py refresh_document_templates --company <id>  # just one
    python manage.py refresh_document_templates --dry-run       # report only
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.context import tenant_scope
from apps.identity.models import Company
from apps.quotes.document_templates import (
    resync_builtin_family_designs,
    set_default_template,
    sync_builtin_templates,
)
from apps.quotes.models import (
    CommercialDocument,
    DEFAULT_FAMILY_KEY,
    RETIRED_TEMPLATE_LABEL,
    DocumentTemplate,
    DocumentType,
    Quotation,
)


class Command(BaseCommand):
    help = "Replace the retired document-template catalogue with the Lulaworks families."

    def add_arguments(self, parser):
        parser.add_argument("--company", help="Only this company UUID (default: all).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change without writing.")

    def handle(self, *args, **options):
        if options.get("company"):
            try:
                companies = [Company.objects.get(id=options["company"])]
            except Company.DoesNotExist as exc:
                raise CommandError("No such company.") from exc
        else:
            companies = list(Company.objects.all())

        dry = options.get("dry_run")
        seeded = deleted = archived = fixed = resynced = 0
        for company in companies:
            with tenant_scope(company.id):
                s, d, a, f, r = self._refresh_company(company, dry)
            seeded += s
            deleted += d
            archived += a
            fixed += f
            resynced += r
            if s or d or a or f or r:
                self.stdout.write(
                    f"  {company.name}: +{s} family, -{d} deleted, "
                    f"{a} archived, {f} default(s) repaired, {r} design(s) refreshed")

        verb = "Would apply" if dry else "Applied"
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: +{seeded} family template(s), {deleted} deleted, "
            f"{archived} archived, {fixed} default(s) repaired, {resynced} design(s) "
            f"refreshed across {len(companies)} company(ies)."))

    def _refresh_company(self, company, dry):
        # 1. Ensure the families exist (never touches existing rows).
        seeded = 0 if dry else sync_builtin_templates(company)

        # 2. Retired built-ins are the seeded templates that carry no family.
        retired = list(DocumentTemplate.objects.filter(
            company=company, is_builtin=True, family=""))
        deleted = archived = 0
        for tpl in retired:
            pinned = self._is_pinned(tpl)
            if pinned:
                # Keep the row (a finalised document renders from its version) but
                # hide it and give it a neutral label so no old catalogue name
                # lingers in the data.
                if not dry:
                    from django.utils import timezone
                    tpl.is_default = False
                    tpl.name = RETIRED_TEMPLATE_LABEL
                    tpl.archived_at = tpl.archived_at or timezone.now()
                    tpl.save(update_fields=["is_default", "name", "archived_at"])
                archived += 1
            else:
                if not dry:
                    tpl.delete()          # cascades its (unreferenced) versions
                deleted += 1

        # 3. Repair the default for any document type that now lacks one.
        fixed = 0 if dry else self._repair_defaults(company)

        # 4. Refresh pristine built-in family designs to the latest code (new
        #    section orders / footer layouts). Never touches user-customised ones.
        resynced = 0 if dry else resync_builtin_family_designs(company)
        return seeded, deleted, archived, fixed, resynced

    @staticmethod
    def _is_pinned(tpl) -> bool:
        """True if any finalised document is pinned to a version of this template
        (so deleting it would silently change an already-issued document)."""
        return (Quotation.objects.filter(template_version__template=tpl).exists()
                or CommercialDocument.objects.filter(
                    template_version__template=tpl).exists())

    @staticmethod
    def _repair_defaults(company) -> int:
        fixed = 0
        for doc_type, _label in DocumentType.choices:
            has_default = DocumentTemplate.objects.filter(
                company=company, doc_type=doc_type, is_default=True,
                archived_at__isnull=True).exists()
            if has_default:
                continue
            horizon = DocumentTemplate.objects.filter(
                company=company, doc_type=doc_type, family=DEFAULT_FAMILY_KEY,
                archived_at__isnull=True).first()
            target = horizon or DocumentTemplate.objects.filter(
                company=company, doc_type=doc_type, is_builtin=True,
                archived_at__isnull=True).first()
            if target:
                with transaction.atomic():
                    set_default_template(target)
                fixed += 1
        return fixed
