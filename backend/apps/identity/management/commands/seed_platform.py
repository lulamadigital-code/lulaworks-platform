"""Seed platform reference data: permission catalogue, default role templates,
subscription plans, and feature-flag definitions. Idempotent."""

from django.core.management.base import BaseCommand

from apps.administration.models import FeatureFlagDefinition
from apps.ai_platform.models import PromptTemplate
from apps.billing.models import Plan
from apps.identity.models import Permission, Role

PERMISSIONS = [
    ("company.manage", "company", "Manage company settings"),
    ("users.invite", "identity", "Invite / manage users"),
    ("billing.manage", "billing", "Manage subscription & billing"),
    ("finance.view_money", "finance", "View financial data (Golden Rule)"),
    ("finance.manage", "finance", "Manage budgets, invoices, cost entries"),
    ("projects.create", "projects", "Create projects"),
    ("projects.view", "projects", "View projects"),
    ("rfq.upload", "rfq", "Upload RFQs"),
    ("rfq.approve", "rfq", "Approve RFQ extractions"),
    ("quotes.create", "estimating", "Create & edit draft quotations"),
    ("quotes.approve", "estimating", "Approve quotations & commercial documents"),
    ("quotes.download", "estimating", "Download / export quotation documents"),
    ("estimating.manage", "estimating", "Build & edit estimates"),
    ("estimating.approve", "estimating", "Approve estimates (margin/discount gate)"),
    ("compliance.manage", "compliance", "Manage compliance library & items"),
    ("compliance.override", "compliance", "Override compliance gate"),
    ("invoices.approve", "finance", "Approve invoices"),
    ("procurement.manage", "procurement", "Manage suppliers & POs"),
    ("po.approve", "procurement", "Approve purchase orders"),
    ("ai.generate", "ai", "Use AI features"),
    ("execution.manage", "execution", "Manage tasks, resources & allocations"),
    ("timesheet.approve", "execution", "Approve timesheets"),
    # Work Management Engine (Module 8) — granular, on top of the execution.manage
    # umbrella. Holding execution.manage implies all of these.
    ("work.create", "work", "Create work"),
    ("work.edit", "work", "Edit work"),
    ("work.delete", "work", "Delete work"),
    ("work.assign", "work", "Assign work & manage the team"),
    ("work.approve", "work", "Approve work (quality / sign-off)"),
    ("work.close", "work", "Close work"),
    ("work.files", "work", "Upload & manage work files"),
]

# role -> permission codenames ("*" = all)
ROLES = {
    "Company Owner": ["*"],
    "Company Administrator": ["*"],
    "Operations Manager": ["projects.create", "projects.view", "rfq.upload",
                            "rfq.approve", "quotes.create", "quotes.approve",
                            "quotes.download", "procurement.manage",
                            "estimating.manage", "compliance.manage", "execution.manage",
                            "timesheet.approve", "ai.generate", "work.create", "work.edit",
                            "work.delete", "work.assign", "work.approve", "work.close",
                            "work.files"],
    "Finance Manager": ["finance.view_money", "finance.manage", "invoices.approve",
                        "billing.manage", "po.approve", "estimating.approve",
                        "quotes.download", "projects.view"],
    # An estimator prepares and downloads quotations, but approval is a separate,
    # authorised step (separation of duties).
    "Estimator": ["estimating.manage", "finance.view_money", "quotes.create",
                  "quotes.download", "projects.view", "ai.generate"],
    "Procurement Officer": ["procurement.manage", "finance.view_money", "projects.view"],
    "Safety Officer": ["compliance.manage", "compliance.override", "projects.view"],
    "Supervisor": ["projects.view", "execution.manage", "timesheet.approve", "ai.generate",
                   "work.create", "work.edit", "work.assign", "work.approve",
                   "work.close", "work.files"],
    "Worker": ["projects.view", "work.edit", "work.files"],
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

        PromptTemplate.objects.get_or_create(
            agent="rfq_extraction", version="v1",
            defaults={
                "key": "rfq_extraction",
                "content": (
                    "Extract from this RFQ/PO text as strict JSON with keys "
                    '"fields" (key -> {value, confidence}) and "lines" '
                    "([{description, qty, unit, unit_price}]). Find: po_number, "
                    "order_date, client, site, contact, scope, work_type.\n\n{text}"
                ),
            },
        )
        PromptTemplate.objects.get_or_create(
            agent="lulama_orchestrator", version="v1",
            defaults={
                "key": "lulama_orchestrator",
                "content": (
                    "You are Lulama, the AI Operations Director for a contracting "
                    "business. Decompose the user's request, coordinate the "
                    "specialised agents, and present ONE consolidated draft for human "
                    "review. Never approve, award, send, pay or delete — always "
                    "propose and let a human decide.\n\n{request}"
                ),
            },
        )
        self.stdout.write(self.style.SUCCESS("Platform seed complete."))
