"""Seed a demo FIELD / groundfloor employee on the primary tenant company, so
the role-based mobile navigation can be experienced from the employee's side.

Idempotent — safe to re-run. Creates:
  • a user  field@lulama.co.za  (password Lulaworks123!)
  • a Worker-role membership on the tenant company
  • EXECUTOR assignments on a handful of the company's tasks so "My Work" has
    something in it

The Worker role (projects.view, work.edit, work.files) carries no money, CRM,
procurement or approval permissions, so in the app it resolves to the EMPLOYEE
persona: bottom bar Home · My Work · Jobs · More, a personal Home scorecard
(My tasks / Due today / In progress / Completed), and no company money — the
Golden Rule holds by construction.
"""
from collections import Counter

from django.contrib.auth import get_user_model

from apps.core.context import tenant_scope
from apps.execution.models import Assignment, Task
from apps.identity.models import Company, Membership, Role

U = get_user_model()

EMAIL = "field@lulama.co.za"
PASSWORD = "Lulaworks123!"

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

# ── The field/groundfloor role (template set).
role = (Role.objects.filter(company=company, name="Worker").first()
        or Role.objects.filter(company__isnull=True, name="Worker").first())
assert role, "No 'Worker' role found — run: manage.py seed_platform first."
print("Role:", role.name,
      "| perms:", sorted(role.permissions.values_list("codename", flat=True)))

# ── The user.
user, created = U.objects.get_or_create(
    email=EMAIL,
    defaults=dict(first_name="Sipho", last_name="Ndlovu", is_active=True,
                  active_company=company),
)
user.first_name = user.first_name or "Sipho"
user.last_name = user.last_name or "Ndlovu"
user.is_active = True
user.active_company = company
user.set_password(PASSWORD)
user.save()
print(("Created" if created else "Updated"), "user:", user.email)

# ── The membership (Worker on the tenant company).
membership, m_created = Membership.objects.get_or_create(
    user=user, company=company,
    defaults=dict(role=role, job_title="Field Technician", status="active"),
)
if not m_created:
    membership.role = role
    membership.status = "active"
    membership.job_title = membership.job_title or "Field Technician"
    membership.save()
print(("Created" if m_created else "Updated"),
      f"membership @ {company.name} as {role.name}")

# ── Populate "My Work": assign as EXECUTOR to a few of the company's tasks.
with tenant_scope(company.id):
    tasks = list(Task.objects.order_by("-created_at")[:5])
    new_assignments = 0
    for t in tasks:
        _, a_created = Assignment.objects.get_or_create(
            task=t, user=user, role=Assignment.Role.EXECUTOR)
        new_assignments += 1 if a_created else 0
print(f"Tasks now visible under My Work: {len(tasks)} "
      f"(new assignments: {new_assignments})")

print(f"\n  Log in on the app →  {EMAIL}  /  {PASSWORD}")
print("  Expected: bottom bar = Home · My Work · Jobs · More, personal Home,"
      " no money.")
