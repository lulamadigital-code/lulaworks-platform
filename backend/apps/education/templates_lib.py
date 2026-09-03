"""Free business templates — the Education Engine's template library.

Per the brief, templates are delivered *through* Lulaworks wherever possible
("Create your free professional quotation") rather than as static downloads, so
the template doubles as a product on-ramp. Genuinely text-based templates
(checklists, follow-up emails) show usable, copyable content on the page.

Declarative, like the free tools: add a template by adding a TemplateSpec.
Three kinds:
  * document  — a Lulaworks-generated document (quote/invoice/DN/RFQ/PO). We show
                what a good one contains and send the user to create it in-app.
  * checklist — a ready-to-use checklist (rendered as a copyable list).
  * email     — ready-to-send message templates (rendered copyable).
"""

from dataclasses import dataclass, field


@dataclass
class TemplateSpec:
    slug: str
    title: str
    summary: str
    kind: str                       # document | checklist | email
    category: str                   # ResourceCategory slug (grouping)
    related_feature: str
    icon: str
    problem: str                    # HTML — why it matters
    cta_label: str
    cta_url: str = "/start-free-trial/"
    includes: list = field(default_factory=list)   # document: what's on it
    items: list = field(default_factory=list)       # checklist: the items
    samples: list = field(default_factory=list)     # email: [{title, text}]


def published_template_specs():
    """Published templates from the DB, as TemplateSpecs; code-seed fallback when
    the table is empty so the public site works before it's seeded."""
    from .models import ContentStatus, Template
    rows = list(Template.objects.filter(status=ContentStatus.PUBLISHED))
    return [t.to_spec() for t in rows] if rows else list(TEMPLATES.values())


def template_spec(slug):
    """One published template as a TemplateSpec (DB first, code fallback) or None."""
    from .models import ContentStatus, Template
    row = Template.objects.filter(slug=slug, status=ContentStatus.PUBLISHED).first()
    return row.to_spec() if row else TEMPLATES.get(slug)


TEMPLATES = {
    "professional-quotation-template": TemplateSpec(
        slug="professional-quotation-template",
        title="Professional Quotation Template",
        summary="Everything a quote needs to look credible and win the work — built for you in Lulaworks.",
        kind="document", category="quoting", related_feature="quotations", icon="📝",
        problem="<p>A quotation is a sales document. The ones that win are branded, "
                "itemised, priced correctly and easy to accept. Lulaworks builds it for "
                "you from your items and turns an approved quote straight into a job.</p>",
        cta_label="Create your free professional quotation",
        includes=[
            "Your logo, company details and a unique quote number",
            "Customer, contact and a clear scope of work",
            "Itemised lines — labour, materials, transport",
            "Correct VAT and a clear total",
            "Validity date and payment terms",
            "Exclusions & assumptions (prevents scope-creep disputes)",
            "One-click acceptance and conversion to a job",
        ]),
    "tax-invoice-template": TemplateSpec(
        slug="tax-invoice-template",
        title="Tax Invoice Template",
        summary="A compliant tax invoice that gets you paid — generated from the job, VAT calculated.",
        kind="document", category="getting-paid", related_feature="invoices", icon="🧾",
        problem="<p>An unclear or late invoice is the top reason contractors get paid "
                "late. Lulaworks raises a compliant tax invoice that carries the job's "
                "number, calculates VAT, and tracks the outstanding balance.</p>",
        cta_label="Create your free tax invoice",
        includes=[
            "The words 'Tax Invoice', your VAT number and an invoice number",
            "Bill-to customer and their VAT number",
            "The customer's PO reference",
            "Itemised lines with VAT and totals",
            "Invoice date, due date and banking details",
            "Automatic outstanding-balance tracking",
        ]),
    "delivery-note-template": TemplateSpec(
        slug="delivery-note-template",
        title="Delivery Note Template",
        summary="Prove what was delivered and get it signed for — straight from the job.",
        kind="document", category="getting-paid", related_feature="jobs", icon="🚚",
        problem="<p>A signed delivery note is your proof of delivery when a customer "
                "disputes an invoice. Lulaworks generates a branded delivery note from "
                "the job — quantities only, never prices.</p>",
        cta_label="Create your free delivery note",
        includes=[
            "Your branding and a delivery-note number tied to the job",
            "Deliver-to site and date",
            "Item, quantity ordered, delivered and outstanding",
            "Driver and receiver fields",
            "A 'received in good order' sign-off",
        ]),
    "rfq-template": TemplateSpec(
        slug="rfq-template",
        title="RFQ Template (Request for Quotation)",
        summary="Ask suppliers for prices the professional way, so quotes are comparable.",
        kind="document", category="procurement", related_feature="rfq", icon="📨",
        problem="<p>Vague requests get vague prices you can't compare. A proper RFQ "
                "lists exactly what you need, so supplier quotes line up side by side. "
                "Lulaworks manages RFQs and reads supplier quotes back in.</p>",
        cta_label="Manage RFQs free in Lulaworks",
        includes=[
            "Your company and RFQ reference",
            "Itemised list of materials with quantities and units",
            "Required delivery date and site",
            "A closing date for quotes",
            "Clear contact and submission details",
        ]),
    "purchase-order-template": TemplateSpec(
        slug="purchase-order-template",
        title="Purchase Order Template",
        summary="Order from suppliers with a clear PO — and tie the spend to the job.",
        kind="document", category="procurement", related_feature="procurement", icon="🧾",
        problem="<p>Ordering by WhatsApp leads to wrong deliveries and untracked spend. "
                "A purchase order records exactly what you ordered, at what price, for "
                "which job. Lulaworks issues POs and tracks them against job cost.</p>",
        cta_label="Issue purchase orders free in Lulaworks",
        includes=[
            "Your details, the supplier and a PO number",
            "Itemised order with agreed prices",
            "Delivery site and date",
            "The job the spend belongs to",
            "Payment terms",
        ]),
    "site-handover-checklist": TemplateSpec(
        slug="site-handover-checklist",
        title="Site Handover Checklist",
        summary="A ready-to-use checklist so nothing is missed when you hand over a completed job.",
        kind="checklist", category="quoting", related_feature="jobs", icon="✅",
        problem="<p>A clean handover protects your final payment and your reputation. "
                "Use this checklist on completion; in Lulaworks you can attach it to the "
                "job with photos and a customer sign-off.</p>",
        cta_label="Track job checklists in Lulaworks",
        items=[
            "All scope items completed and matched to the quotation",
            "Site cleaned and waste removed",
            "Snags identified, logged and resolved",
            "Materials and equipment removed or accounted for",
            "As-built drawings / documentation handed over",
            "Safety file and compliance certificates provided",
            "Customer walkthrough completed",
            "Customer sign-off obtained (with date and name)",
            "Photos taken of completed work",
            "Final invoice raised",
        ]),
    "customer-onboarding-checklist": TemplateSpec(
        slug="customer-onboarding-checklist",
        title="New Customer Onboarding Checklist",
        summary="Capture the right details up front so quoting, invoicing and getting paid are smooth.",
        kind="checklist", category="getting-paid", related_feature="crm", icon="🤝",
        problem="<p>Missing customer details cause invoice disputes and payment delays. "
                "Capture these once, at the start. In Lulaworks this becomes the customer "
                "record every quote, job and invoice reuses.</p>",
        cta_label="Manage customers free in Lulaworks",
        items=[
            "Registered company name and trading name",
            "VAT number and company registration number",
            "Billing address and delivery/site address",
            "Primary contact: name, role, email, phone",
            "Accounts contact for invoices",
            "Purchase-order process and PO threshold",
            "Payment terms agreed (e.g. 30 days)",
            "Vendor/portal registration completed (Coupa, Ariba, etc.)",
            "Preferred document format and any specific requirements",
        ]),
    "payment-follow-up-templates": TemplateSpec(
        slug="payment-follow-up-templates",
        title="Payment Follow-up Email Templates",
        summary="Three ready-to-send reminders — polite to firm — that get invoices paid without burning the relationship.",
        kind="email", category="getting-paid", related_feature="invoices", icon="✉️",
        problem="<p>Most late invoices just need a nudge. These three escalating "
                "reminders do it professionally. Lulaworks shows you exactly which "
                "invoices are outstanding and by how long, so you know when to send "
                "which.</p>",
        cta_label="See outstanding invoices in Lulaworks",
        samples=[
            {"title": "1 · Friendly reminder (due date)",
             "text": ("Hi {name},\n\nJust a friendly reminder that invoice {number} "
                      "for {amount} is due today. If it's already in progress, thank "
                      "you — please ignore this note.\n\nBanking details are on the "
                      "invoice; happy to resend if useful.\n\nKind regards,\n{you}")},
            {"title": "2 · Follow-up (7 days overdue)",
             "text": ("Hi {name},\n\nInvoice {number} for {amount} was due on {due_date} "
                      "and is now 7 days overdue. Could you let me know when we can "
                      "expect payment, or if anything is holding it up?\n\nThank "
                      "you,\n{you}")},
            {"title": "3 · Firm request (30 days overdue)",
             "text": ("Hi {name},\n\nInvoice {number} for {amount} is now 30 days "
                      "overdue. Please arrange payment by {new_date}. If there is a "
                      "dispute or a problem, I'd like to resolve it — please call me on "
                      "{phone}.\n\nRegards,\n{you}")},
        ]),
}
