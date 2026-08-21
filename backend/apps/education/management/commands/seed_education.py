"""Seed the Learning Centre with a starter set of genuinely useful, published
content so the Academy is not empty. Idempotent — safe to run repeatedly (keys
on slug). Lulaworks staff extend this in the admin over time."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.education.models import (
    ContentStatus,
    Difficulty,
    LearningPath,
    LearningPathStep,
    Resource,
    ResourceCategory,
    ResourceKind,
)

CATEGORIES = [
    ("Quoting", "quoting", "📝", "Win more work with quotes that look professional and price the job right.", 10),
    ("Procurement", "procurement", "🛒", "Buy smarter — manage suppliers, compare prices and control job costs.", 20),
    ("Profitability", "profitability", "📊", "Understand what actually makes (and loses) you money.", 30),
    ("Getting paid", "getting-paid", "💰", "Invoice faster and collect what you're owed.", 40),
]

RESOURCES = [
    {
        "slug": "how-to-create-a-professional-construction-quotation",
        "kind": ResourceKind.GUIDE, "category": "quoting", "featured": True,
        "title": "How to create a professional construction quotation",
        "summary": "The seven things every winning quote includes — and the mistakes that get quotes rejected.",
        "features": ["quotations", "rfq"],
        "cta_label": "Create your first quotation free",
        "cta_url": "/start-free-trial/",
        "body": (
            "<p>A quotation is a sales document, not a price list. Contractors lose "
            "work every day not because they were too expensive, but because the "
            "quote looked rushed, missed information, or never got followed up.</p>"
            "<h2>What a professional quotation includes</h2><ol>"
            "<li>Your company details, logo and a clear quote number</li>"
            "<li>The customer and the exact scope of work</li>"
            "<li>An itemised breakdown — labour, materials, transport</li>"
            "<li>Correct VAT and a clear total</li>"
            "<li>Validity date and payment terms</li>"
            "<li>Exclusions and assumptions (this prevents scope-creep disputes)</li>"
            "<li>A next step — how to accept</li></ol>"
            "<h2>Do it manually</h2><p>Build a branded template, keep a numbering "
            "system, and diarise a follow-up three days after sending. Most jobs are "
            "won on the follow-up, not the quote.</p>"
            "<h2>Or let Lulaworks do it</h2><p>Lulaworks builds branded, numbered "
            "quotations from your items, adds VAT automatically, and turns an approved "
            "quote into a job, invoice and delivery note with one click.</p>"
        ),
    },
    {
        "slug": "quotation-vs-invoice-whats-the-difference",
        "kind": ResourceKind.ARTICLE, "category": "quoting", "featured": False,
        "title": "Quotation vs invoice: what's the difference?",
        "summary": "When to send which document, and why sending the wrong one costs you money.",
        "features": ["quotations", "invoices"],
        "cta_label": "Quote and invoice in one place",
        "cta_url": "/start-free-trial/",
        "body": (
            "<p>A <strong>quotation</strong> is an offer to do work at a price, before "
            "the job. An <strong>invoice</strong> is a demand for payment, after (or "
            "during) the job. Mixing them up delays your cash.</p>"
            "<p>The clean flow is: quotation → approval → job → delivery note → tax "
            "invoice → payment. Each document should carry the same reference so the "
            "whole job is traceable.</p>"
            "<p>Lulaworks keeps that chain on one number automatically.</p>"
        ),
    },
    {
        "slug": "markup-vs-margin-contractor-profit",
        "kind": ResourceKind.GUIDE, "category": "profitability", "featured": True,
        "title": "Markup vs margin: the mistake that quietly kills contractor profit",
        "summary": "A 30% markup is NOT a 30% margin. Here's the difference, with the maths.",
        "features": ["quotations"],
        "cta_label": "Track real job profit automatically",
        "cta_url": "/start-free-trial/",
        "body": (
            "<p>Markup is added <em>on top of cost</em>. Margin is profit as a share of "
            "the <em>selling price</em>. They are not the same number.</p>"
            "<p>Cost R100, 30% markup → sell R130. But your margin is 30/130 = "
            "<strong>23%</strong>, not 30%. Contractors who price on markup and think "
            "in margin slowly bleed profit on every job.</p>"
            "<h2>Rule of thumb</h2><p>To hit a target margin, divide cost by (1 − "
            "margin). For a 30% margin on R100 cost: 100 ÷ 0.70 = R143.</p>"
            "<p>Lulaworks shows cost, markup and margin on every line as you quote, and "
            "the real profit on every job as it runs.</p>"
        ),
    },
    {
        "slug": "why-customers-pay-invoices-late",
        "kind": ResourceKind.GUIDE, "category": "getting-paid", "featured": False,
        "title": "Why customers pay invoices late (and how to get paid faster)",
        "summary": "Late payment is usually an admin problem, not a money problem. Fix the admin.",
        "features": ["invoices"],
        "cta_label": "Track outstanding invoices in Lulaworks",
        "cta_url": "/start-free-trial/",
        "body": (
            "<p>Most late payments come down to five fixable things: the invoice was "
            "late, it was unclear, it went to the wrong person, there was no PO "
            "reference, or nobody followed up.</p>"
            "<h2>Get paid faster</h2><ul><li>Invoice the day the work is signed off</li>"
            "<li>Put the customer's PO number on the invoice</li>"
            "<li>State the due date, not just 'net 30'</li>"
            "<li>Follow up at day 7, not day 31</li>"
            "<li>Track outstanding balances weekly</li></ul>"
            "<p>Lulaworks tracks each invoice's outstanding balance and ages your "
            "debtors (current / 30 / 60 / 90+) so nothing slips.</p>"
        ),
    },
    {
        "slug": "supplier-price-history-better-quotes",
        "kind": ResourceKind.GUIDE, "category": "procurement", "featured": False,
        "title": "How supplier price history helps you quote more accurately",
        "summary": "Every receipt is data. Use it to quote from real prices, not guesses.",
        "features": ["suppliers", "procurement"],
        "cta_label": "Manage suppliers in Lulaworks",
        "cta_url": "/start-free-trial/",
        "body": (
            "<p>If you quote from memory, you either pad the price and lose the job, or "
            "under-price and lose the margin. The fix is a price history.</p>"
            "<p>Record what you paid, to whom, for each material. Over time you can see "
            "who is cheapest, spot price creep, and quote the next job from real "
            "numbers.</p>"
            "<p>Lulaworks learns supplier prices from your purchases automatically and "
            "flags when a price looks high.</p>"
        ),
    },
]

PATH_STEPS = [
    ("Set up your company", "Add your logo, banking and document branding.", None),
    ("Add your customers", "Build your customer master — the base of every quote and invoice.", None),
    ("Add your suppliers", "Start a price history so you can quote from real numbers.", None),
    ("Create a professional quotation", "", "how-to-create-a-professional-construction-quotation"),
    ("Follow up and win the work", "Most jobs are won on the follow-up.", None),
    ("Convert the quotation into a job", "Turn the approved quote into an operational job.", None),
    ("Manage procurement", "Raise requests and purchase orders against the job.", None),
    ("Complete delivery", "Generate a branded delivery note from the job.", None),
    ("Create the invoice", "Raise a tax invoice that carries the job's number.", None),
    ("Track the payment", "Record payments and watch the outstanding balance.", None),
]


class Command(BaseCommand):
    help = "Seed the Learning Centre with starter published content (idempotent)."

    def handle(self, *args, **options):
        cats = {}
        for name, slug, icon, desc, order in CATEGORIES:
            c, _ = ResourceCategory.objects.get_or_create(
                slug=slug, defaults={"name": name, "icon": icon,
                                     "description": desc, "order": order})
            cats[slug] = c

        made = 0
        for r in RESOURCES:
            obj, created = Resource.objects.get_or_create(
                slug=r["slug"],
                defaults={
                    "kind": r["kind"], "title": r["title"], "summary": r["summary"],
                    "body": r["body"], "category": cats.get(r["category"]),
                    "difficulty": Difficulty.BEGINNER, "read_minutes": 4,
                    "status": ContentStatus.PUBLISHED, "is_featured": r["featured"],
                    "related_features": r["features"], "cta_label": r["cta_label"],
                    "cta_url": r["cta_url"], "published_at": timezone.now(),
                })
            made += int(created)

        path, _ = LearningPath.objects.get_or_create(
            slug="start-your-contractor-business",
            defaults={"title": "Start Your Contractor Business", "icon": "🚀",
                      "summary": "From company setup to your first paid invoice — the "
                      "whole Lulaworks flow in ten steps.",
                      "status": ContentStatus.PUBLISHED, "order": 10})
        if not path.steps.exists():
            for i, (title, desc, res_slug) in enumerate(PATH_STEPS, start=1):
                LearningPathStep.objects.create(
                    path=path, order=i, title=title, description=desc,
                    resource=Resource.objects.filter(slug=res_slug).first()
                    if res_slug else None)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded education: {len(cats)} categories, {made} new resource(s), "
            f"1 learning path."))
