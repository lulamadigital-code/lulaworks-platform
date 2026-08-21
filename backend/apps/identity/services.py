"""Company membership services — who works here, and what they may do.

Two rules shape this module:

1. **Invite by secure link — never email a password.** A manager enters a name,
   email and role; Lulaworks creates the account with NO usable password and
   emails a time-limited activation link. The recipient follows it and sets their
   own password. Nothing is ever transmitted that could be used as a credential.
   (`add_member` with a one-time password remains for API/back-compat, but the
   web flow uses `invite_member`.)

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


# ══════════════════════════════════════════════════════════════════════════════
# Invitations & account tokens — the secure, link-based account lifecycle.
#
# Lulaworks never emails a password. A manager invites; the person activates via
# a single-use, time-limited link and sets their own password. Password reset
# and email verification use the same token mechanism.
# ══════════════════════════════════════════════════════════════════════════════

INVITE_TTL_DAYS = 7
RESET_TTL_HOURS = 1


def _new_token() -> str:
    import secrets as _s
    return _s.token_urlsafe(32)


def _mint_token(*, purpose, email, user=None, company=None, role=None,
                invited_by=None, ttl):
    from django.utils import timezone
    from .models import AccountToken
    return AccountToken.objects.create(
        purpose=purpose, token=_new_token(), email=email.strip().lower(),
        user=user, company=company, role=role, invited_by=invited_by,
        expires_at=timezone.now() + ttl,
    )


def _activation_url(token) -> str:
    from django.conf import settings
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    path = {"invite": "/activate/", "reset": "/reset/",
            "verify": "/verify/"}.get(token.purpose, "/activate/")
    return f"{base}{path}{token.token}/"


@transaction.atomic
def invite_member(company, actor, *, email, role, first_name="", last_name="",
                  job_title="", mobile=""):
    """Invite a person to the company by secure activation link.

    Creates the account with NO usable password (so it cannot be logged into
    until they set one), the membership, an invite token, and sends the branded
    invitation email. Returns (membership, token). An email that already exists
    on the platform gains a membership and is emailed a plain "you've been added"
    note instead of an activation link (they already have a password).
    """
    from .models import AccountToken

    email = (email or "").strip().lower()
    if not email:
        raise MemberError("An email address is required.")

    user = User.objects.filter(email__iexact=email).first()
    is_new = user is None

    if is_new:
        user = User(email=email, first_name=first_name.strip(),
                    last_name=last_name.strip(), mobile=mobile.strip(),
                    active_company=company, must_change_password=True)
        user.set_unusable_password()          # activation link sets the real one
        user.save()
    else:
        existing = Membership.objects.filter(company=company, user=user).first()
        if existing is not None:
            if existing.status == "active":
                raise MemberError(f"{email} is already a member of this company.")
            existing.status = "active"
            existing.role = role
            existing.save(update_fields=["status", "role"])
            return existing, None

    membership = Membership.objects.create(
        company=company, user=user, role=role, job_title=job_title.strip(),
        invited_by=actor, status="active")

    from datetime import timedelta
    token = None
    if is_new:
        token = _mint_token(
            purpose=AccountToken.Purpose.INVITE, email=email, user=user,
            company=company, role=role, invited_by=actor,
            ttl=timedelta(days=INVITE_TTL_DAYS))
        _send_invite_email(company, actor, user, token)
    else:
        _send_added_email(company, user)
    return membership, token


def _send_invite_email(company, actor, user, token):
    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email
    inviter = (actor.get_full_name() or actor.email) if actor else company.name
    send_email(
        to=user.email, subject=f"You're invited to join {company.name} on Lulaworks",
        template="invitation", company=company, sent_by=actor,
        to_name=(user.get_full_name() or "").strip(), category=EmailCategory.ACCOUNT,
        context={
            "heading": f"Join {company.name} on Lulaworks",
            "inviter": inviter, "company_name_display": company.name,
            "role": token.role.name if token.role else "",
            "cta_url": _activation_url(token), "cta_label": "Set your password",
            "cta_note": f"This invitation expires in {INVITE_TTL_DAYS} days. "
                        "You'll choose your own password — we never send one.",
        })


def _send_added_email(company, user):
    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email
    from django.conf import settings
    send_email(
        to=user.email, subject=f"You've been added to {company.name} on Lulaworks",
        template="generic", company=company, category=EmailCategory.ACCOUNT,
        to_name=(user.get_full_name() or "").strip(),
        context={
            "heading": f"You've been added to {company.name}",
            "body": "You already have a Lulaworks account, so just sign in with "
                    "your existing password to access this company.",
            "cta_url": (getattr(settings, "SITE_URL", "").rstrip("/") + "/login/"),
            "cta_label": "Sign in",
        })


class PlatformStaffError(Exception):
    """Raised for platform-team management problems (bad email, role, etc.)."""


# Which Django flags each platform access level carries. Owners are full Django
# superusers; admin/support reach the Console through `platform_role` alone, with
# no superuser powers and no Django-admin access.
_PLATFORM_ROLE_FLAGS = {
    "owner": {"is_superuser": True, "is_staff": True},
    "admin": {"is_superuser": False, "is_staff": False},
    "finance": {"is_superuser": False, "is_staff": False},
    "hr": {"is_superuser": False, "is_staff": False},
    "support": {"is_superuser": False, "is_staff": False},
}


@transaction.atomic
def invite_platform_staff(actor, *, email, role, first_name="", last_name=""):
    """Add a Lulaworks platform-team member by secure activation link.

    Creates the account with NO usable password (they set it via the link),
    stamps the platform role + matching Django flags, mints an invite token and
    emails it. Returns (user, token). Superuser owners keep Django-admin access;
    admin/support are Console-only.
    """
    from .models import AccountToken

    email = (email or "").strip().lower()
    if not email:
        raise PlatformStaffError("An email address is required.")
    if role not in _PLATFORM_ROLE_FLAGS:
        raise PlatformStaffError("Choose a valid role.")
    if User.objects.filter(email__iexact=email).exists():
        raise PlatformStaffError("A user with that email already exists.")

    user = User(
        email=email, first_name=first_name.strip(), last_name=last_name.strip(),
        platform_role=role, is_active=True, must_change_password=True,
        **_PLATFORM_ROLE_FLAGS[role])
    user.set_unusable_password()                # activation link sets the real one
    user.save()

    from datetime import timedelta
    token = _mint_token(
        purpose=AccountToken.Purpose.INVITE, email=email, user=user,
        company=None, role=None, invited_by=actor,
        ttl=timedelta(days=INVITE_TTL_DAYS))
    _send_platform_invite(actor, user, token)
    return user, token


def _send_platform_invite(actor, user, token):
    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email
    inviter = (actor.get_full_name() or actor.email) if actor else "Lulaworks"
    role_label = dict(User.PlatformRole.choices).get(user.platform_role, "team member")
    send_email(
        to=user.email, subject="You're invited to the Lulaworks platform team",
        template="invitation", company=None, sent_by=actor,
        to_name=(user.get_full_name() or "").strip(), category=EmailCategory.ACCOUNT,
        context={
            "heading": "Join the Lulaworks platform team",
            "inviter": inviter, "company_name_display": "Lulaworks",
            "role": role_label,
            "cta_url": _activation_url(token), "cta_label": "Set your password",
            "cta_note": f"This invitation expires in {INVITE_TTL_DAYS} days. "
                        "You'll choose your own password — we never send one.",
        })


def set_platform_role(user, role) -> User:
    """Change a platform-team member's access level (and sync Django flags)."""
    if role not in _PLATFORM_ROLE_FLAGS:
        raise PlatformStaffError("Choose a valid role.")
    user.platform_role = role
    for field, value in _PLATFORM_ROLE_FLAGS[role].items():
        setattr(user, field, value)
    user.save(update_fields=["platform_role", "is_superuser", "is_staff"])
    return user


def revoke_platform_staff(user) -> User:
    """Remove platform-team access — clears the role and superuser/staff flags.
    The account stays (they may still be a tenant member); it just loses the
    Console. Deactivating entirely is a separate action."""
    user.platform_role = ""
    user.is_superuser = False
    user.is_staff = False
    user.save(update_fields=["platform_role", "is_superuser", "is_staff"])
    return user


@transaction.atomic
def accept_invitation(token_str, *, password):
    """Complete an invitation: validate the token, set the user's chosen
    password, activate the account, and consume the token. Returns the user."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from django.utils import timezone
    from .models import AccountToken

    token = AccountToken.objects.select_related("user").filter(
        token=token_str, purpose=AccountToken.Purpose.INVITE).first()
    if token is None or not token.is_valid:
        raise MemberError("This invitation link is invalid or has expired.")
    try:
        validate_password(password, token.user)
    except ValidationError as exc:
        raise MemberError(" ".join(exc.messages))

    user = token.user
    user.set_password(password)
    user.must_change_password = False
    user.is_active = True
    user.save(update_fields=["password", "must_change_password", "is_active"])

    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    _send_welcome_email(token.company, user)
    return user


def _send_welcome_email(company, user):
    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email
    from django.conf import settings
    send_email(
        to=user.email, subject="Welcome to Lulaworks", template="generic",
        company=company, category=EmailCategory.ACCOUNT,
        to_name=(user.get_full_name() or "").strip(),
        context={
            "heading": f"Welcome{', ' + user.first_name if user.first_name else ''}!",
            "body": "Your account is active. You can sign in any time.",
            "cta_url": (getattr(settings, "SITE_URL", "").rstrip("/") + "/login/"),
            "cta_label": "Open Lulaworks",
        })


def request_password_reset(email) -> bool:
    """Start a password reset. Sends a reset link if an active account exists —
    but ALWAYS behaves the same to the caller (returns True) so the reset page
    can't be used to discover which emails have accounts (no user enumeration)."""
    from datetime import timedelta
    from .models import AccountToken

    email = (email or "").strip().lower()
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user is not None:
        token = _mint_token(purpose=AccountToken.Purpose.RESET, email=email,
                            user=user, ttl=timedelta(hours=RESET_TTL_HOURS))
        _send_reset_email(user, token)
    return True


def _send_reset_email(user, token):
    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email
    company = user.active_company
    send_email(
        to=user.email, subject="Reset your Lulaworks password",
        template="password_reset", company=company, category=EmailCategory.SECURITY,
        to_name=(user.get_full_name() or "").strip(),
        context={
            "heading": "Reset your password",
            "cta_url": _activation_url(token), "cta_label": "Choose a new password",
            "cta_note": f"This link expires in {RESET_TTL_HOURS} hour. If you "
                        "didn't request this, you can safely ignore this email.",
        })


@transaction.atomic
def reset_password(token_str, *, password):
    """Complete a password reset: validate the token, set the new password,
    consume the token. Returns the user."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from django.utils import timezone
    from .models import AccountToken

    token = AccountToken.objects.select_related("user").filter(
        token=token_str, purpose=AccountToken.Purpose.RESET).first()
    if token is None or not token.is_valid:
        raise MemberError("This reset link is invalid or has expired.")
    try:
        validate_password(password, token.user)
    except ValidationError as exc:
        raise MemberError(" ".join(exc.messages))

    user = token.user
    user.set_password(password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    notify_password_changed(user)
    return user


def notify_password_changed(user):
    """Security email: confirm a password change (a heads-up if it wasn't them).
    Called after a reset or a self-service change. Never raises."""
    try:
        from apps.notifications.models import EmailCategory
        from apps.notifications.service import send_email
        send_email(
            to=user.email, subject="Your Lulaworks password was changed",
            template="generic", company=user.active_company,
            category=EmailCategory.SECURITY,
            to_name=(user.get_full_name() or "").strip(),
            context={
                "heading": "Your password was changed",
                "body": "This is a confirmation that your Lulaworks password was "
                        "just changed. If this wasn't you, reset your password "
                        "immediately and contact support.",
            })
    except Exception:  # noqa: BLE001 - security notice must not break the change
        pass
