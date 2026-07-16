"""Seed platform reference data: permission catalogue, default role templates,
subscription plans, and feature-flag definitions. Idempotent."""

from django.core.management.base import BaseCommand

from apps.administration.models import FeatureFlagDefinition
from apps.billing.models import Plan
from apps.identity.models import Permission, Role

PERMISSIONS = [
    ("company.manage", "company", "Manage company settings"),
    ("users.invite", "identity", "Invite / manage users"),
    ("billing.manage", "billing", "Manage subscription & billing"),
    ("finance.view_money", "finance", "View financial data (Golden Rule)"),
    ("projects.create", "projects", "Create projects"),
    ("projects.view", "projects", "View projects"),
    ("rfq.upload", "rfq", "Upload RFQs"),
    ("rfq.approve", "rfq", "Approve RFQ extractions"),
    ("quotes.create", "estimating", "Create quotations"),
    ("compliance.override", "compliance", "Override compliance gate"),
    ("invoices.approve", "finance", "Approve invoices"),
    ("ai.generate", "ai", "Use AI features"),
]

# role -> permission codenames ("*" = all)
ROLES = {
    "Company Owner": ["*"],
    "Company Administrator": ["*"],
    "Operations Manager": ["projects.create", "projects.view", "rfq.upload",
                            "rfq.approve", "quotes.create", "ai.generate"],
    "Finance Manager": ["finance.view_money", "invoices.approve", "billing.manage",
                        "projects.view"],
    "Safety Officer": ["compliance.override", "projects.view"],
    "Supervisor": ["projects.view", "ai.generate"],
    "Worker": ["projects.view"],
}

PLANS = [
    ("starter", "Starter", 299, 4, 1_073_741_824, 0, []),
    ("business", "Business", 799, 15, 5_368_709_120, 500,
     ["accounting", "dashboards", "compliance"]),
    ("growth", "Growth", 1999, 50, 10_737_418_240, 2000,
     ["accounting", "dashboards", "compliance", "procurement", "analytics"]),
]

FLAGS = [
    ("ai_quoting", "AI quote generation", False),
    ("compliance_engine", "Compliance Intelligence", True),
    ("procurement", "Procurement engine", True),
    ("executive_dashboard", "Executive dashboard", False),
]


class Command(BaseCommand):
    help = "Seed platform permissions, roles, plans, and feature flags"

    def handle(self, *args, **options):
        perms = {}
        for codename, module, label in PERMISSIONS:
            p, _ = Permission.objects.get_or_create(
                codename=codename, defaults={"module": module, "label": label}
            )
            perms[codename] = p
        self.stdout.write(f"Permissions: {len(perms)}")

        all_perms = list(perms.values())
        for name, codenames in ROLES.items():
            role, created = Role.objects.get_or_create(
                company=None, name=name, defaults={"is_system": True}
            )
            wanted = all_perms if codenames == ["*"] else [perms[c] for c in codenames]
            role.permissions.set(wanted)
        self.stdout.write(f"Role templates: {len(ROLES)}")

        for code, name, price, users, quota, credits, modules in PLANS:
            Plan.objects.get_or_create(
                code=code,
                defaults={
                    "name": name, "price": price, "max_users": users,
                    "storage_quota_bytes": quota, "monthly_ai_credits": credits,
                    "module_entitlements": modules,
                },
            )
        self.stdout.write(f"Plans: {len(PLANS)}")

        for key, desc, default in FLAGS:
            FeatureFlagDefinition.objects.get_or_create(
                key=key, defaults={"description": desc, "default_enabled": default}
            )
        self.stdout.write(self.style.SUCCESS("Platform seed complete."))
