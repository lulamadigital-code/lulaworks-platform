"""Seed realistic demo data for the primary tenant company:
customers + contacts, projects + tasks (some assigned to the actor so they show
under "My tasks"), and quotations with line items.

The company is resolved automatically (the one the most members belong to), and
restored if it was soft-deleted, so the seeded data is actually visible. The
script is idempotent by name — safe to re-run; it skips anything already present.
"""
from collections import Counter

from django.contrib.auth import get_user_model

from apps.core.context import tenant_scope
from apps.customers.models import Customer, CustomerContact
from apps.customers.services import create_customer
from apps.execution.models import Assignment, Task
from apps.identity.models import Company
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.quotes.models import Quotation
from apps.quotes.services import create_quotation

U = get_user_model()

# ── Resolve the tenant company (most members) and an actor, tolerating a
# soft-deleted company (FK access + all_objects both bypass the delete filter).
counts = Counter(
    u.active_company_id for u in U.objects.filter(active_company__isnull=False))
if counts:
    company = Company.all_objects.get(id=counts.most_common(1)[0][0])
else:
    company = Company.all_objects.first()
actor = (U.objects.filter(active_company=company).order_by("-is_superuser").first()
         or U.objects.filter(is_superuser=True).first())
assert company and actor, "Could not resolve a tenant company or an actor user"
print("Company:", company.name, "| Actor:", actor)

# Restore the company if it was soft-deleted, so the data is visible in the app.
if getattr(company, "is_deleted", False):
    company.is_deleted = False
    company.deleted_at = None
    company.save(update_fields=["is_deleted", "deleted_at"])
    print("Company was soft-deleted — restored.")

CUSTOMERS = [
    dict(name="Harmony Gold Mining", customer_type="mine", city="Welkom",
         province="Free State", email="procurement@harmony.co.za",
         telephone="057 391 0000",
         contacts=[("Thabo Nkosi", "Maintenance Planner", "thabo.nkosi@harmony.co.za", "082 111 2233"),
                   ("Lerato Dlamini", "Procurement Officer", "lerato.dlamini@harmony.co.za", "083 444 5566")]),
    dict(name="Sibanye-Stillwater", customer_type="mine", city="Westonaria",
         province="Gauteng", email="buyers@sibanyestillwater.com",
         telephone="011 278 9600",
         contacts=[("Sipho Zulu", "Engineering Manager", "sipho.zulu@sibanye.com", "082 777 8899")]),
    dict(name="Sasol", customer_type="industrial", city="Secunda",
         province="Mpumalanga", email="procurement@sasol.com",
         telephone="017 610 1000",
         contacts=[("Anele Khumalo", "Buyer", "anele.khumalo@sasol.com", "084 222 3344")]),
    dict(name="ArcelorMittal South Africa", customer_type="industrial",
         city="Vanderbijlpark", province="Gauteng", email="vendors@arcelormittal.co.za",
         telephone="016 889 9111",
         contacts=[("Riaan Botha", "Project Engineer", "riaan.botha@arcelormittal.co.za", "082 555 6677")]),
]

PROJECTS = [
    dict(customer="Harmony Gold Mining", title="Pump Station 4 Overhaul",
         site="Welkom Shaft 2", work_type="pump_overhaul",
         tasks=["Strip and inspect pumps", "Replace mechanical seals & bearings",
                "Reassemble, align and test run"]),
    dict(customer="Sibanye-Stillwater", title="Conveyor CV-12 Maintenance",
         site="Westonaria Plant", work_type="maintenance",
         tasks=["Inspect belt rollers and idlers", "Replace worn idlers",
                "Tension and commission belt"]),
    dict(customer="Sasol", title="Walkway Steel Fabrication",
         site="Secunda West", work_type="fabrication",
         tasks=["Cut and prep structural steel", "Weld walkway assemblies",
                "Surface treatment and delivery"]),
]

QUOTES = [
    dict(client="Harmony Gold Mining", title="Pump overhaul — 3 units",
         site="Welkom Shaft 2",
         lines=[("Strip, inspect & report (per pump)", 3, "unit", "4500.00"),
                ("Mechanical seal kit", 3, "set", "3800.00"),
                ("Bearing set", 6, "each", "1250.00"),
                ("Site labour & supervision", 40, "hour", "650.00")]),
    dict(client="Sibanye-Stillwater", title="Conveyor idler replacement",
         site="Westonaria Plant",
         lines=[("Troughing idler 152mm", 24, "each", "890.00"),
                ("Return idler 152mm", 12, "each", "760.00"),
                ("Installation labour", 32, "hour", "580.00")]),
    dict(client="Sasol", title="Access walkway fabrication & install",
         site="Secunda West",
         lines=[("Grating 1m x 1m hot-dip galvanised", 18, "panel", "1650.00"),
                ("Handrail assembly (per meter)", 36, "m", "420.00"),
                ("Fabrication labour", 60, "hour", "620.00"),
                ("Delivery & rigging", 1, "lot", "8500.00")]),
]

created = {"customers": 0, "contacts": 0, "projects": 0, "tasks": 0, "quotes": 0}

with tenant_scope(company.id):
    by_name = {}
    for cd in CUSTOMERS:
        contacts = cd.pop("contacts", [])
        cust = Customer.objects.filter(name=cd["name"]).first()
        if not cust:
            cust = create_customer(company, actor, seed_departments=True, **cd)
            created["customers"] += 1
        by_name[cd["name"]] = cust
        for (nm, title, email, mob) in contacts:
            if not CustomerContact.objects.filter(customer=cust, full_name=nm).exists():
                CustomerContact.objects.create(
                    company=company, customer=cust, full_name=nm, job_title=title,
                    email=email, mobile=mob, created_by=actor, updated_by=actor)
                created["contacts"] += 1

    for pd in PROJECTS:
        proj = Project.objects.filter(title=pd["title"]).first()
        if not proj:
            proj = create_project(
                company, actor, title=pd["title"],
                client_name=pd["customer"], customer=by_name.get(pd["customer"]),
                site=pd["site"], work_type=pd["work_type"])
            created["projects"] += 1
        for i, tname in enumerate(pd["tasks"]):
            if Task.objects.filter(project=proj, name=tname).exists():
                continue
            t = Task.objects.create(company=company, project=proj, name=tname,
                                    blocks_on_compliance=False)
            created["tasks"] += 1
            if i < 2:  # first two tasks per project → the actor's "My tasks"
                Assignment.objects.get_or_create(company=company, task=t, user=actor)

    for qd in QUOTES:
        if Quotation.objects.filter(title=qd["title"]).exists():
            continue
        lines = [dict(description=d, qty=q, unit=u, unit_price=p)
                 for (d, q, u, p) in qd["lines"]]
        create_quotation(company, actor, client_name=qd["client"], title=qd["title"],
                         site=qd["site"], lines=lines)
        created["quotes"] += 1

print("SEEDED:", created)
