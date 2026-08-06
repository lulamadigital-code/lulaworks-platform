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
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    from apps.billing.services import start_trial
    from apps.core.validation import InputError, clean_email, clean_str
    from apps.identity.models import Company, Membership, Role, User

    # Validate untrusted, anonymous input: bounds + format + password strength.
    try:
        company_name = clean_str(company_name, field="Company name", max_length=255, required=True)
        full_name = clean_str(full_name, field="Full name", max_length=200, required=True)
        email = clean_email(email)
        phone = clean_str(phone, field="Phone", max_length=32)
        industry = clean_str(industry, field="Industry", max_length=64)
        if not password:
            raise InputError("A password is required.")
        validate_password(password)   # runs AUTH_PASSWORD_VALIDATORS (length, common, numeric…)
    except InputError as exc:
        raise RegistrationError(str(exc))
    except ValidationError as exc:
        raise RegistrationError(" ".join(exc.messages))

    if User.objects.filter(email=email).exists():
        raise RegistrationError(
            "An account with this email already exists — please log in instead."
        )

    first, _, last = full_name.partition(" ")
    company_kwargs = {"name": company_name, "industry": industry}
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
