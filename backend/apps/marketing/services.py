"""Public-site services: self-service trial registration and lead capture.

Registration is the one place the public site writes into the core platform —
it creates a Company, its owner User, the owner Membership, and starts the
free trial (apps.billing.services.start_trial). Everything else the site does
is read-only marketing content.
"""

from django.db import transaction


class RegistrationError(Exception):
    """Human-readable problem with a trial sign-up (shown back on the form)."""


@transaction.atomic
def register_trial_company(*, company_name, full_name, email, password,
                           phone="", industry="", currency=None):
    """Create a new company on the free trial and return its owner user.

    The caller logs the returned user in. `currency` (auto-detected from the
    visitor's location) sets the company's billing currency. Raises
    RegistrationError for problems a visitor can fix (duplicate email, missing
    fields)."""
    from apps.billing.services import start_trial
    from apps.identity.models import Company, Membership, Role, User

    company_name = (company_name or "").strip()
    full_name = (full_name or "").strip()
    email = (email or "").strip().lower()
    if not (company_name and full_name and email and password):
        raise RegistrationError("Please complete all required fields.")
    if User.objects.filter(email=email).exists():
        raise RegistrationError(
            "An account with this email already exists — please log in instead."
        )

    first, _, last = full_name.partition(" ")
    company_kwargs = {"name": company_name, "industry": (industry or "").strip()}
    if currency:
        company_kwargs["currency"] = currency
    company = Company.objects.create(**company_kwargs)
    user = User.objects.create_user(
        email=email, password=password, first_name=first, last_name=last,
        mobile=(phone or "").strip(),
    )
    user.active_company = company
    user.save(update_fields=["active_company"])

    owner_role = Role.objects.filter(company=None, name="Company Owner").first()
    Membership.objects.create(
        company=company, user=user, role=owner_role, status="active",
    )
    # Free trial: 30 days of Professional features, capped (billing.services).
    start_trial(company, actor=user)
    return user
