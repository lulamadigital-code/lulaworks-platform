"""Seed a starter Lulaworks Knowledge Base (idempotent by slug)."""
from django.core.management.base import BaseCommand

from apps.core.context import system_scope
from apps.support.models import KBArticle

ARTICLES = [
    ("Quotation isn't extracting items from my scope", "quotations",
     "The quotation needs a customer contact before items can be extracted.",
     "When a quotation won't generate line items from an uploaded scope, first make "
     "sure you've selected a customer contact — extraction is linked to the contact. "
     "Then re-upload the scope document (PDF or Excel) and click Extract. If items "
     "still don't appear, the document may be scanned; try a text-based file.",
     "quotation, extract, scope, items, contact, not working"),
    ("I can't log in", "account",
     "Reset your password from the login page, or ask your company admin to re-invite you.",
     "If you can't sign in, use 'Forgot password' on the login page to get a reset "
     "link. Accounts created by an admin must set a password via the activation link "
     "first — check your email (and spam). If your account was deactivated, your "
     "company administrator can reactivate it under People.",
     "login, password, sign in, reset, locked, access"),
    ("WhatsApp notifications aren't arriving", "whatsapp",
     "WhatsApp alerts require an opted-in mobile number and an active integration.",
     "WhatsApp notifications only send to team members who have a valid mobile number "
     "on their profile and have opted in. Check the number under your profile, and "
     "confirm your plan includes WhatsApp. Email notifications always work as a fallback.",
     "whatsapp, notification, sms, not receiving, alerts"),
    ("Running out of AI credits", "ai",
     "AI features use monthly credits from your plan; owners can top up or upgrade.",
     "LulaAI features (extraction, suggestions) consume AI credits included with your "
     "plan each month. When they run low, a company owner can top up credits or upgrade "
     "the plan. Deterministic features keep working without AI credits.",
     "ai, credits, lulaai, out of credits, top up, extraction"),
    ("How do I create an invoice from a job?", "invoices",
     "Open the job, then use Commercial to raise an invoice or progress claim.",
     "From a job or quotation, go to the Commercial section and choose Invoice (or "
     "Progress claim for staged billing). Line items and the customer carry over. "
     "Review the VAT and banking details, then generate the PDF.",
     "invoice, billing, job, commercial, progress claim, create"),
    ("How to set up Single Sign-On (SSO)", "account",
     "Enterprise admins can let their team sign in with the company identity "
     "provider (OpenID Connect / SAML).",
     "Single Sign-On (SSO) lets your team sign in with your company's identity "
     "provider — such as Google Workspace, Microsoft Entra ID (Azure AD) or Okta "
     "— instead of a separate Lulaworks password. SSO is an Enterprise feature. "
     "Password sign-in keeps working alongside it, and only people you've already "
     "invited to the workspace can sign in via SSO (no accounts are auto-created).\n\n"
     "Set it up in Settings > Security & Enterprise > Single Sign-On:\n\n"
     "1. In your identity provider, create an OpenID Connect (OIDC) app and set its "
     "redirect URI to the exact value shown on the SSO settings page — it ends in "
     "/sso/oidc/callback/.\n"
     "2. Copy the Issuer URL, Client ID and Client secret from your provider into the "
     "form, add your team's email domain(s), and click Save.\n"
     "3. Click Enable SSO. Then sign out and use 'Sign in with SSO' on the login page "
     "with your work email to test it.\n\n"
     "Provider notes:\n"
     "- Google Workspace: Issuer https://accounts.google.com. Create the client under "
     "Google Cloud Console > APIs & Services > Credentials > OAuth client ID (Web "
     "application), and add the redirect URI there.\n"
     "- Microsoft Entra ID: Issuer https://login.microsoftonline.com/<tenant-id>/v2.0. "
     "Register the app under Entra ID > App registrations; add the redirect URI under "
     "Authentication and create the secret under Certificates & secrets.\n"
     "- Okta: Issuer https://<your-org>.okta.com. Create an OIDC Web app and add the "
     "redirect URI to its Sign-in redirect URIs.\n\n"
     "Your client secret is stored encrypted and used only server-side — it is never "
     "shown again after you save it. If your provider only supports SAML, fill in the "
     "SAML fields instead and use 'Request activation'; the Lulaworks team completes "
     "that setup with you.",
     "sso, single sign-on, oidc, saml, okta, google workspace, azure, entra, login, identity provider, enterprise"),
    ("A document upload keeps failing", "technical",
     "Uploads must be under the size limit and a supported type (PDF, image, Excel).",
     "If a file won't upload, check it's a supported type (PDF, PNG/JPG, or Excel) and "
     "within the size limit. Very large scans can time out — try compressing the PDF. "
     "If it still fails, note the time and contact support with the file name.",
     "upload, file, failing, attachment, size, pdf, error"),
]


class Command(BaseCommand):
    help = "Seed starter Knowledge Base articles (idempotent)."

    def handle(self, *args, **opts):
        from django.utils.text import slugify
        created = 0
        with system_scope():
            for title, cat, summary, body, tags in ARTICLES:
                slug = slugify(title)[:200]
                _, was_new = KBArticle.objects.get_or_create(
                    slug=slug,
                    defaults=dict(title=title, category=cat, summary=summary,
                                  body=body, tags=tags, is_published=True))
                created += 1 if was_new else 0
        self.stdout.write(self.style.SUCCESS(f"KB seeded ({created} new, {len(ARTICLES)} total)."))
