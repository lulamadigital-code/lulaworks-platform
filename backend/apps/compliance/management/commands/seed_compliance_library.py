"""Seed a starter SA-mining compliance requirement library for a company.
Tenant-scoped (TenantBaseModel), so it runs inside the company's tenant scope.
Idempotent. Demo/bootstrap aid — real tenants curate their own library."""

from django.core.management.base import BaseCommand, CommandError

from apps.compliance.models import ComplianceRequirement
from apps.core.context import tenant_scope
from apps.identity.models import Company

# (code, name, category, source, mandatory, applies_when, valid_days)
LIBRARY = [
    ("SAFETY_FILE", "Approved Safety File", "documentation", "customer", True, {}, None),
    ("PUBLIC_LIABILITY", "Public Liability Insurance", "insurance", "policy", True, {}, 365),
    ("COIDA", "COIDA Letter of Good Standing", "documentation", "regulatory", True, {}, 365),
    ("SITE_INDUCTION", "Site Induction", "induction", "mine", True, {}, 180),
    ("MEDICAL_FITNESS", "Medical Certificate of Fitness", "medical", "mine", True, {}, 365),
    ("PPE_ISSUE", "PPE Issue Register", "ppe", "policy", True, {}, None),
    # Work-type-specific:
    ("WAH_CERT", "Working at Heights Certificate", "training", "work_type", True,
     {"work_types": ["working_at_heights", "conveyor_maintenance"]}, 730),
    ("HOTWORK_PERMIT", "Hot Work Permit", "permit", "work_type", True,
     {"work_types": ["hot_work", "shutdown", "pump_overhaul"]}, 7),
    ("CONFINED_SPACE", "Confined Space Entry Permit", "permit", "work_type", True,
     {"work_types": ["confined_space", "shutdown"]}, 1),
    ("LIFTING_INSPECTION", "Lifting Equipment Inspection", "equipment", "equipment", True,
     {"work_types": ["crane_lift", "shutdown"]}, 180),
]


class Command(BaseCommand):
    help = "Seed a starter compliance requirement library for a company."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Company UUID")

    def handle(self, *args, **options):
        try:
            company = Company.objects.get(id=options["company"])
        except Company.DoesNotExist as exc:
            raise CommandError("No such company.") from exc

        n = 0
        with tenant_scope(company.id):
            for code, name, cat, source, mand, aw, days in LIBRARY:
                _, created = ComplianceRequirement.objects.get_or_create(
                    company=company, code=code,
                    defaults={
                        "name": name, "category": cat, "source": source,
                        "is_mandatory": mand, "applies_when": aw, "default_valid_days": days,
                    },
                )
                n += int(created)
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {n} new requirement(s) for {company.name}."
        ))
