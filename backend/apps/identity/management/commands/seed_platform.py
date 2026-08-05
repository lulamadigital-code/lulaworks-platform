"""Seed platform reference data: permission catalogue, default role templates,
subscription plans, and feature-flag definitions. Idempotent."""

from django.core.management.base import BaseCommand

from apps.administration.models import FeatureFlagDefinition
from apps.ai_platform.models import PromptTemplate
from apps.billing.models import CreditPack, Plan
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

GB = 1024 ** 3

# The V1 pricing model (spec). Plans are DATA — an Enterprise tier can be added
# later as another row with a higher `tier`, no code change. `module_entitlements`
# are the gating keys; `features` are the human-readable bullets for the UI.
_STARTER_ENTITLEMENTS = ["basic_procurement", "basic_dashboard", "pdf_export", "excel_export"]
_PRO_ENTITLEMENTS = _STARTER_ENTITLEMENTS + [
    "ai_extraction", "rfq_extraction", "po_extraction", "invoice_extraction",
    "scope_extraction", "supplier_intelligence", "price_history", "gps_checkin",
    "time_tracking", "team_management", "advanced_dashboard",
]
_BUSINESS_ENTITLEMENTS = _PRO_ENTITLEMENTS + [
    "approval_workflows", "compliance_management", "procurement_analytics",
    "advanced_reporting", "multi_team",
]

PLANS = [
    {
        "code": "starter", "name": "Starter", "tier": 1, "is_popular": False,
        "price": 299, "annual_price": 2990, "max_users": 2,
        "storage_quota_bytes": 5 * GB, "monthly_ai_credits": 300,
        "support_level": "email", "module_entitlements": _STARTER_ENTITLEMENTS,
        "features": [
            "2 users", "Unlimited employees", "Unlimited customers & suppliers",
            "Unlimited jobs, tasks & quotations", "Unlimited tax invoices & delivery notes",
            "Basic procurement", "Basic dashboard", "PDF & Excel export",
            "300 AI credits / month", "5 GB storage", "Email support",
        ],
    },
    {
        "code": "professional", "name": "Professional", "tier": 2, "is_popular": True,
        "price": 1299, "annual_price": 12990, "max_users": 10,
        "storage_quota_bytes": 50 * GB, "monthly_ai_credits": 2000,
        "support_level": "priority", "module_entitlements": _PRO_ENTITLEMENTS,
        "features": [
            "Everything in Starter, plus:", "10 users", "2,000 AI credits / month",
            "50 GB storage", "AI document extraction (RFQ, PO, invoice, scope)",
            "Supplier intelligence & product price history",
            "GPS employee check-ins & time tracking", "Team management",
            "Advanced dashboards", "Priority support",
        ],
    },
    {
        "code": "business", "name": "Business", "tier": 3, "is_popular": False,
        "price": 3999, "annual_price": 39990, "max_users": 50,
        "storage_quota_bytes": 200 * GB, "monthly_ai_credits": 8000,
        "support_level": "highest", "module_entitlements": _BUSINESS_ENTITLEMENTS,
        "features": [
            "Everything in Professional, plus:", "50 users", "8,000 AI credits / month",
            "200 GB storage", "Advanced approval workflows", "Compliance management",
            "Advanced procurement analytics", "Advanced reporting",
            "Multi-team management", "Highest-priority support",
        ],
    },
]

# Optional one-off AI credit top-ups (spec).
CREDIT_PACKS = [
    ("pack_500", "500 AI Credits", 500, 199),
    ("pack_2000", "2,000 AI Credits", 2000, 699),
    ("pack_10000", "10,000 AI Credits", 10000, 2999),
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

        # update_or_create so re-seeding REFRESHES prices/limits/features on any
        # already-seeded plan (e.g. a running deployment) — not just new rows.
        for spec in PLANS:
            Plan.objects.update_or_create(
                code=spec["code"],
                defaults={**{k: v for k, v in spec.items() if k != "code"}, "is_active": True},
            )
        # Retire any legacy plans not in the V1 set (kept for existing subs, hidden).
        Plan.objects.exclude(code__in=[p["code"] for p in PLANS]).update(is_active=False)
        self.stdout.write(f"Plans: {len(PLANS)} (legacy plans deactivated)")

        for code, name, credits, price in CREDIT_PACKS:
            CreditPack.objects.update_or_create(
                code=code,
                defaults={"name": name, "credits": credits, "price": price, "is_active": True},
            )
        self.stdout.write(f"Credit packs: {len(CREDIT_PACKS)}")

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
