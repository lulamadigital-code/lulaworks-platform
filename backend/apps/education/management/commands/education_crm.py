"""Configure and run the Education → CRM bridge.

  education_crm                      # show status
  education_crm --set "Lulama"       # mark that company to receive Academy leads
  education_crm --sync               # backfill hot leads into the CRM now
"""

from django.core.management.base import BaseCommand, CommandError

from apps.education.leads import CRM_SYNC_THRESHOLD, crm_company, sync_lead_to_crm
from apps.education.models import EducationLead
from apps.identity.models import Company


class Command(BaseCommand):
    help = "Configure/run the Education Engine → CRM lead bridge."

    def add_arguments(self, parser):
        parser.add_argument("--set", dest="set", default="",
                            help="Company name or id to receive Academy leads.")
        parser.add_argument("--sync", action="store_true",
                            help="Backfill hot Education leads into the CRM now.")

    def handle(self, *args, **opts):
        if opts["set"]:
            key = opts["set"].strip()
            company = (Company.objects.filter(id=key).first()
                       if "-" in key else None) or \
                Company.objects.filter(name__icontains=key).first()
            if company is None:
                raise CommandError(f"No company matches '{key}'.")
            Company.objects.filter(receives_education_leads=True).exclude(
                id=company.id).update(receives_education_leads=False)
            company.receives_education_leads = True
            company.save(update_fields=["receives_education_leads"])
            self.stdout.write(self.style.SUCCESS(
                f"'{company.name}' now receives Education Engine leads."))

        target = crm_company()
        self.stdout.write(f"Sales company: {target.name if target else '— none set (bridge is off) —'}")

        if opts["sync"]:
            if target is None:
                raise CommandError("Set a sales company first: --set \"<name>\".")
            hot = EducationLead.objects.filter(score__gte=CRM_SYNC_THRESHOLD)
            n = 0
            for lead in hot:
                if sync_lead_to_crm(lead) is not None:
                    n += 1
            self.stdout.write(self.style.SUCCESS(
                f"Synced {n}/{hot.count()} hot lead(s) into the CRM."))
        else:
            self.stdout.write(
                f"Hot leads (score ≥ {CRM_SYNC_THRESHOLD}): "
                f"{EducationLead.objects.filter(score__gte=CRM_SYNC_THRESHOLD).count()}"
                f"  ·  run with --sync to push them to the CRM.")
