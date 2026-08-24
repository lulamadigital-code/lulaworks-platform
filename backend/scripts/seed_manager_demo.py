"""Seed a demo MANAGER (Operations Manager) on the primary tenant company, so
the role-based mobile navigation can be experienced from the manager's side.

Idempotent — safe to re-run. Creates:
  • a user  manager@lulama.co.za  (password Lulaworks123!)
  • an Operations Manager membership on the tenant company
  • OWNER assignments on a handful of the company's tasks

The Operations Manager role runs work and approvals (quotes.approve,
rfq.approve, procurement.manage, execution.manage, …) but has NO
finance.view_money and NO company.manage. In the app it resolves to the
MANAGER persona: bottom bar Home · CRM · Jobs · Purchasing · More, an
operations Home, and — because money is not permitted — no financial figures
and no company administration (Golden Rule holds).
"""
from collections import Counter

from django.contrib.auth import get_user_model

from apps.core.context import tenant_scope
from apps.execution.models import Assignment, Task
from apps.identity.models import Company, Membership, Role

U = get_user_model()

EMAIL = "manager@lulama.co.za"
PASSWORD = "Lulaworks123!"
ROLE_NAME = "Operations Manager"

# ── Resolve the tenant company (most members), tolerating a soft-deleted one.
counts = Counter(
    u.active_company_id for u in U.objects.filter(active_company__isnull=False))
company = (Company.all_objects.get(id=counts.most_common(1)[0][0]) if counts
           else Company.all_objects.first())
assert company, "Could not resolve a tenant company"
if getattr(company, "is_deleted", False):
    company.is_deleted = False
    company.deleted_at = None
    company.save(update_fields=["is_deleted", "deleted_at"])
    print("Company was soft-deleted — restored.")
print("Company:", company.name)

# ── The manager role (template set).
role = (Role.objects.filter(company=company, name=ROLE_NAME).first()
        or Role.objects.filter(company__isnull=True, name=ROLE_NAME).first())
assert role, f"No '{ROLE_NAME}' role found — run: manage.py seed_platform first."
print("Role:", role.name,
      "| perms:", sorted(role.permissions.values_list("codename", flat=True)))

# ── The user.
user, created = U.objects.get_or_create(
    email=EMAIL,
    defaults=dict(first_name="Lerato", last_name="Molefe", is_active=True,
                  active_company=company),
)
user.first_name = user.first_name or "Lerato"
user.last_name = user.last_name or "Molefe"
user.is_active = True
user.active_company = company
user.set_password(PASSWORD)
user.save()
print(("Created" if created else "Updated"), "user:", user.email)

# ── The membership (Operations Manager on the tenant company).
membership, m_created = Membership.objects.get_or_create(
    user=user, company=company,
    defaults=dict(role=role, job_title="Operations Manager", status="active"),
)
if not m_created:
    membership.role = role
    membership.status = "active"
    membership.job_title = membership.job_title or "Operations Manager"
    membership.save()
print(("Created" if m_created else "Updated"),
      f"membership @ {company.name} as {role.name}")

# ── Give the manager a few tasks they own (shows under their own work).
with tenant_scope(company.id):
    tasks = list(Task.objects.order_by("-created_at")[:4])
    new_assignments = 0
    for t in tasks:
        _, a_created = Assignment.objects.get_or_create(
            task=t, user=user, role=Assignment.Role.OWNER)
        new_assignments += 1 if a_created else 0
print(f"Tasks owned by the manager: {len(tasks)} "
      f"(new assignments: {new_assignments})")

print(f"\n  Log in on the app →  {EMAIL}  /  {PASSWORD}")
print("  Expected: bar = Home · CRM · Jobs · Purchasing · More, operations"
      " Home, NO money (no finance.view_money).")
