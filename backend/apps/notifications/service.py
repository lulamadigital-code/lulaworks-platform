"""The Email Service — the ONE path every module uses to send email.

    module → send_email(...) → EmailLog (queued) → Celery → provider → recipient

No module builds an EmailMessage or talks to a provider. They call send_email
with a template name and context; this renders the branded HTML, records the
audit row, and hands delivery to the background worker (with retry). The
provider is Django's EMAIL_BACKEND — SMTP today, SendGrid/SES/Mailgun/Postmark/
Resend later by changing one env var, no module change.
"""

from datetime import datetime, timezone

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import EmailCategory, EmailLog, EmailStatus

#: Default retry budget for a failed send (Celery re-queues up to this many times).
MAX_ATTEMPTS = 3


def _branding(company) -> dict:
    """Everything the branded shell needs about the sender, from the company
    profile (reused from document generation) with safe platform fallbacks."""
    ctx = {
        "brand_color": "#0E6E6E",
        "company_name": "LulaWorks",
        "company_address_lines": [],
        "support_email": getattr(settings, "SUPPORT_EMAIL", "") or "",
        "website": "",
        "logo_url": "",
        "year": datetime.now(timezone.utc).year,
    }
    if company is not None:
        try:
            from apps.identity.profile import document_header
            h = document_header(company, kind="email")
            ctx.update({
                "company_name": h["display_name"] or "LulaWorks",
                "company_address_lines": h.get("address_lines") or [],
                "support_email": h.get("email") or ctx["support_email"],
                "website": h.get("website") or "",
            })
            brand = (getattr(company, "brand_primary", "") or "").strip()
            if brand.startswith("#") and len(brand) in (7, 9):
                ctx["brand_color"] = brand[:7]
            # A logo only renders in email from an absolute URL; use one if the
            # deployment set SITE_URL and the company has a logo file.
            site = getattr(settings, "SITE_URL", "").rstrip("/")
            logo = h.get("logo")
            if site and logo and getattr(logo, "url", ""):
                ctx["logo_url"] = site + logo.url
        except Exception:                       # branding must never block a send
            pass
    return ctx


def render_email(template: str, context: dict, company=None) -> tuple[str, str]:
    """Render (html, text) for a template. `template` is a name under emails/,
    with or without the .html suffix. The branded shell is applied automatically
    for the generic template; specific templates extend base.html themselves."""
    name = template if template.endswith(".html") else f"emails/{template}.html"
    full = {**_branding(company), **context}
    html = render_to_string(name, full)
    text = context.get("text_body") or strip_tags(
        render_to_string(name, full)).strip()
    return html, text


def send_email(*, to, subject, template="generic", context=None, company=None,
               to_name="", category=EmailCategory.SYSTEM, sent_by=None,
               related=None, cc=None, reply_to="", attachment_specs=None,
               now=False) -> EmailLog:
    """Queue a branded email and record it. Returns the EmailLog.

    `related` is any model instance the email concerns (stamped as entity_type/
    id for the history). `attachment_specs` is a list of {kind,id,name} the
    worker rebuilds into files at delivery (see notifications.attachments).
    `now=True` sends synchronously (tests / management commands); normally
    delivery runs on the Celery worker so the request never blocks on SMTP.
    """
    context = dict(context or {})
    context.setdefault("subject", subject)
    html, text = render_email(template, context, company)

    # "Sent on behalf of" the tenant: platform mail all goes out from the one
    # authenticated address (DEFAULT_FROM_EMAIL), but a recipient who replies
    # should reach the tenant, not the platform. So when a company sends and no
    # explicit reply-to is given, default it to the company's own email.
    if not reply_to and company is not None and getattr(company, "email", ""):
        reply_to = company.email

    specs = list(attachment_specs or [])
    log = EmailLog.objects.create(
        company=company, to_email=(to or "").strip().lower(), to_name=to_name,
        cc=list(cc or []), reply_to=reply_to, subject=subject, template=template,
        category=category, html_body=html, text_body=text,
        attachment_spec=specs, attachment_names=[s.get("name", "") for s in specs],
        entity_type=related.__class__.__name__ if related is not None else "",
        entity_id=getattr(related, "id", None) if related is not None else None,
        sent_by=sent_by, status=EmailStatus.QUEUED,
    )

    if now or getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        deliver_now(log.id)
    else:
        from .tasks import deliver_email
        try:
            deliver_email.delay(str(log.id))
        except Exception:                       # broker down → send inline
            deliver_now(log.id)
    log.refresh_from_db()
    return log


def deliver_now(log_id) -> EmailLog:
    """Actually send one queued email through Django's configured backend and
    update its log. Idempotent-ish: a SENT log is not re-sent."""
    from django.core.mail import EmailMultiAlternatives, get_connection

    log = EmailLog.objects.get(id=log_id)
    if log.status == EmailStatus.SENT:
        return log

    log.status = EmailStatus.SENDING
    log.attempts += 1
    log.save(update_fields=["status", "attempts", "updated_at"])

    from_email = settings.DEFAULT_FROM_EMAIL
    if log.company_id and log.company:
        from_email = f'{log.company.name} <{settings.DEFAULT_FROM_EMAIL}>'
    try:
        connection = get_connection()
        msg = EmailMultiAlternatives(
            subject=log.subject, body=log.text_body, from_email=from_email,
            to=[log.to_email], cc=log.cc or None,
            reply_to=[log.reply_to] if log.reply_to else None,
            connection=connection)
        msg.attach_alternative(log.html_body, "text/html")
        # Rebuild attachments from their specs (the PDF is regenerated here, in
        # the worker, from the live source record).
        from .attachments import build_attachments
        for filename, data, mimetype in build_attachments(log.attachment_spec):
            msg.attach(filename, data, mimetype)
        msg.send()
    except Exception as exc:                     # noqa: BLE001 - recorded, retried by task
        log.error = str(exc)[:1000]
        log.status = EmailStatus.FAILED
        log.provider = settings.EMAIL_BACKEND
        log.save(update_fields=["error", "status", "provider", "updated_at"])
        raise
    from django.utils import timezone as djtz
    log.status = EmailStatus.SENT
    log.sent_at = djtz.now()
    log.error = ""
    log.provider = settings.EMAIL_BACKEND
    log.save(update_fields=["status", "sent_at", "error", "provider", "updated_at"])
    return log


def resend(log: EmailLog, sent_by=None) -> EmailLog:
    """Manually re-send a failed (or any) email — a fresh log entry so the
    history keeps both attempts."""
    return send_email(
        to=log.to_email, subject=log.subject, template=log.template,
        context={"text_body": log.text_body}, company=log.company,
        to_name=log.to_name, category=log.category, sent_by=sent_by or log.sent_by,
        cc=log.cc, reply_to=log.reply_to,
        # Re-render fresh isn't possible without the original context, so reuse
        # the stored bodies directly.
    )
