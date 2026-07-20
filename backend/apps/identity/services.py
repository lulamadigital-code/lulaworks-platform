"""Company membership services — who works here, and what they may do.

Two rules shape this module:

1. **Add directly, hand over a one-time password.** There is no invite email yet
   (no SMTP), so a manager creates the account and passes on a generated
   temporary password. That password is shown to the manager exactly once, is
   never stored in plain text, and the account cannot be used for anything until
   the person has chosen their own — `must_change_password` gates every page.

2. **Deactivate, never delete.** A person who leaves must stay attached to the
   jobs, timesheets and sign-offs they touched, or the audit trail becomes a lie.
   Deactivation flips `Membership.status`, which `User.active_membership()`
   already filters on — so an inactive member instantly fails every
   `has_perm_code()` check without any extra enforcement.
"""

import secrets
import string

from django.db import transaction

from .models import Membership, Role, User

#: Unambiguous alphabet — no O/0 or l/1, because these get read out over a phone.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def generate_temp_password(length: int = 12) -> str:
    """A temporary password a manager can dictate over the phone without
    ambiguity. It is single-use in practice: the holder must replace it at
    first sign-in."""
    while True:
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        # Guarantee some variety so it survives basic password validators.
        if (any(c.islower() for c in candidate) and any(c.isupper() for c in candidate)
                and any(c in string.digits for c in candidate)):
            return candidate


class MemberError(ValueError):
    """Raised for the human-meaningful refusals (duplicate member, self-lockout)."""


def company_members(company, *, include_inactive=True):
    qs = Membership.objects.filter(company=company).select_related("user", "role")
    if not include_inactive:
        qs = qs.filter(status="active")
    return qs.order_by("user__email")


def assignable_users(company):
    """The people a work item may be assigned to: ACTIVE members only. Someone
    who has left stops appearing in pickers but keeps their name on past work."""
    return User.objects.filter(
        memberships__company=company, memberships__status="active"
    ).distinct().order_by("email")


@transaction.atomic
def add_member(company, actor, *, email, role, first_name="", last_name="",
               job_title="", mobile="", password=None) -> tuple[Membership, str | None]:
    """Add a person to the company. Returns (membership, temp_password) where the
    password is None if the user already existed on the platform.

    Multi-company is supported from day one, so an email that already exists is
    NOT an error — that person gains a second membership and keeps their existing
    password. Only a duplicate membership *in this company* is refused.
    """
    email = (email or "").strip().lower()
    if not email:
        raise MemberError("An email address is required.")

    user = User.objects.filter(email__iexact=email).first()
    temp_password = None

    if user is None:
        temp_password = generate_temp_password()
        user = User.objects.create_user(
            email, temp_password,
            first_name=first_name.strip(), last_name=last_name.strip(),
            mobile=mobile.strip(), active_company=company,
        )
        # Admin-set credential: useless until the holder replaces it.
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
    else:
        existing = Membership.objects.filter(company=company, user=user).first()
        if existing is not None:
            if existing.status == "active":
                raise MemberError(f"{email} is already a member of this company.")
            # Re-joining: reactivate rather than create a second row.
            existing.status = "active"
            existing.role = role
            existing.save(update_fields=["status", "role"])
            return existing, None

    membership = Membership.objects.create(
        company=company, user=user, role=role,
        job_title=job_title.strip(), invited_by=actor, status="active",
    )
    return membership, temp_password


def set_member_role(membership, role) -> Membership:
    membership.role = role
    membership.save(update_fields=["role"])
    return membership


def set_member_status(membership, actor, *, active: bool) -> Membership:
    """Deactivate or restore a member. Refuses to deactivate the actor (nobody
    should be able to lock themselves out) or the last active member holding
    user-management rights (which would strand the company with no administrator).
    """
    if not active:
        if membership.user_id == actor.id:
            raise MemberError("You cannot deactivate your own account.")
        if _is_last_administrator(membership):
            raise MemberError(
                "This is the last active member who can manage users — promote "
                "someone else first, or the company would be left locked out."
            )
    membership.status = "active" if active else "inactive"
    membership.save(update_fields=["status"])
    return membership


def _is_last_administrator(membership) -> bool:
    """True if deactivating this membership would leave nobody able to manage users."""
    if membership.role_id is None:
        return False
    if not membership.role.permissions.filter(codename="users.invite").exists():
        return False
    others = (Membership.objects
              .filter(company_id=membership.company_id, status="active",
                      role__permissions__codename="users.invite")
              .exclude(pk=membership.pk))
    return not others.exists()


def set_password(user, raw_password) -> User:
    """Set a chosen password and clear the forced-change gate."""
    user.set_password(raw_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    return user


def selectable_roles():
    """Role templates a manager can assign. Roles are platform-level templates
    shared by every company (see seed_platform)."""
    return Role.objects.all().order_by("name")


# ── What a person actually works on ───────────────────────────────────────────

def member_work(user, company):
    """Everything this person is attached to, split into live and finished.

    Reads through `Assignment`, so it reflects the four real roles rather than a
    single "assignee" field — someone can be the approver on one job and on the
    execution team of another, and both show up.
    """
    from apps.execution.models import Assignment, TaskStatus

    rows = (Assignment.objects
            .filter(user=user, company=company)
            .select_related("task", "task__project", "task__phase")
            .order_by("-task__created_at"))

    done_states = {TaskStatus.COMPLETED, TaskStatus.CLOSED, TaskStatus.CANCELLED}
    current, past, seen = [], [], {}

    for row in rows:
        task = row.task
        # One line per task, collecting every role they hold on it.
        entry = seen.get(task.id)
        if entry is None:
            entry = {"task": task, "roles": []}
            seen[task.id] = entry
            (past if task.status in done_states else current).append(entry)
        entry["roles"].append(row.get_role_display())

    projects = {}
    for entry in current + past:
        project = entry["task"].project
        if project is not None:
            projects.setdefault(project.id, {"project": project, "count": 0})
            projects[project.id]["count"] += 1

    open_tasks = [e["task"] for e in current]
    return {
        "current": current,
        "past": past,
        "projects": sorted(projects.values(), key=lambda p: -p["count"]),
        "open_count": len(open_tasks),
        "past_count": len(past),
        "overdue_count": sum(1 for t in open_tasks if t.is_overdue),
        "blocked_count": sum(1 for t in open_tasks if t.status == "blocked"),
        "estimated_hours": sum((t.estimated_hours or 0) for t in open_tasks),
    }
