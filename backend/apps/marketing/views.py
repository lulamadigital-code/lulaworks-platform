"""Public marketing site — content pages, lead-capture forms, self-service
trial registration, and SEO endpoints (robots.txt / sitemap.xml).

Content pages are anonymous and cacheable. The only writes are the contact /
demo forms (lead capture) and trial registration (creates a company).
"""

from django.contrib import messages
from django.contrib.auth import login
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import ContactMessage, DemoRequest
from .services import RegistrationError, register_trial_company

# Pages exposed in the sitemap (name, changefreq, priority).
_SITEMAP = [
    ("marketing:home", "weekly", "1.0"),
    ("marketing:features", "monthly", "0.9"),
    ("marketing:pricing", "monthly", "0.9"),
    ("marketing:about", "monthly", "0.6"),
    ("marketing:contact", "monthly", "0.6"),
    ("marketing:demo", "monthly", "0.7"),
    ("marketing:faq", "monthly", "0.6"),
    ("marketing:trial", "monthly", "0.8"),
    ("marketing:privacy", "yearly", "0.3"),
    ("marketing:terms", "yearly", "0.3"),
    ("marketing:cookies", "yearly", "0.3"),
]


def _seo(title, description):
    return {"meta_title": title, "meta_description": description}


_FEATURE_CARDS = [
    ("📝", "Quotation Management", "Build accurate quotes fast and turn them into jobs in one click."),
    ("🔧", "Job Management", "Every job in one view — scope, team, progress, money and paperwork."),
    ("✅", "Task Management", "Break jobs into tasks with checklists, owners and live progress."),
    ("🛒", "Procurement", "Raise requests, compare supplier prices and issue purchase orders."),
    ("🧠", "Supplier Intelligence", "Every receipt teaches the system who sells what, and for how much."),
    ("📁", "Document Management", "POs, invoices, delivery notes and safety files, all in one place."),
    ("🤖", "AI Document Extraction", "Drop in an RFQ, PO or invoice — AI reads it and fills the details."),
    ("🚚", "Delivery Notes", "Generate branded delivery notes straight from the job."),
    ("💰", "Tax Invoices", "Compliant tax invoices and progress claims, with payments tracked."),
    ("📍", "GPS & Time Tracking", "Field check-ins are GPS-stamped so you know who was where, when."),
    ("👷", "Employee Management", "Unlimited employees — technicians, drivers, operators — on every plan."),
    ("📊", "Reporting & Analytics", "See profitability, cash flow and job health as the work happens."),
]

_TESTIMONIALS = [
    ("We quote in minutes now, and I can finally see which jobs actually make money.",
     "Thabo M.", "Managing Director, mechanical contractor"),
    ("The mobile check-ins ended the arguments about who was on site. It just works.",
     "Naledi K.", "Operations Manager, maintenance company"),
    ("Uploading a mine's PO and having it fill in the job automatically saves us hours.",
     "Riaan P.", "Owner, industrial services"),
]


def home(request):
    if request.user.is_authenticated:
        return redirect("web:dashboard")
    ctx = _seo(
        "LulaWorks — From Quotation to Payment. One Platform. Powered by AI.",
        "LulaWorks helps contractors manage quotations, jobs, procurement, teams, "
        "deliveries, invoices and payments from one intelligent, AI-powered platform.",
    )
    ctx["feature_cards"] = _FEATURE_CARDS
    ctx["testimonials"] = _TESTIMONIALS
    return render(request, "marketing/home.html", ctx)


_MODULES = [
    ("🏢", "Company Management", "Your business identity, entered once and reused everywhere.",
     ["Company profile, VAT & registration numbers, banking and branding",
      "Flows onto every quotation, invoice and delivery note automatically"]),
    ("🤝", "Customer Management", "Every client organisation, department and contact in one place.",
     ["Contacts, sites and a full communication timeline",
      "Documents routed to the right person by responsibility"]),
    ("📝", "Quotation Management", "Build accurate, professional quotes in minutes.",
     ["Per-type templates and spreadsheet-style bulk line entry",
      "Branded PDF output; award a quote to open a job in one click"]),
    ("🔧", "Jobs", "The operational heart — every job in a single view.",
     ["Scope, team, progress, money and paperwork together",
      "Raised from a quotation, a customer PO, or started standalone"]),
    ("📂", "Projects", "Group related jobs under one engagement.",
     ["Roll up commercial documents, budget, actuals and timeline",
      "Readiness gate keeps compliance in check before work starts"]),
    ("✅", "Tasks", "Break work down and track it to completion.",
     ["Checklists, owners, dependencies and live progress",
      "Field reports, GPS and evidence captured against each task"]),
    ("🛒", "Procurement", "Buy the right materials at the right price.",
     ["Raise procurement requests and compare supplier prices",
      "Issue purchase orders and reconcile against receipts"]),
    ("🏭", "Suppliers", "A supplier database that builds itself.",
     ["Every receipt records who you bought from and what you paid",
      "Manual entry and old-invoice upload to seed your history"]),
    ("📦", "Products", "Know the real price of everything you buy.",
     ["Price comparison across suppliers, trends and stale-price flags",
      "Product aliases so the same item is never counted twice"]),
    ("🧾", "Purchase Orders", "Turn approved buying into clean paperwork.",
     ["Generate and track POs against jobs and suppliers",
      "Extract incoming customer POs with AI"]),
    ("💰", "Invoices", "Get paid, and see it clearly.",
     ["Compliant tax invoices and progress claims with retention",
      "Payments and outstanding balances tracked per job"]),
    ("🚚", "Delivery Notes", "Prove delivery, on brand.",
     ["Generate delivery notes straight from the job",
      "Quantities and signatures captured for the record"]),
    ("📊", "Reports & Analytics", "Run the business on numbers, not guesswork.",
     ["Profitability, cash flow, aging and job health",
      "Field spend flows into live profitability automatically"]),
    ("📱", "Employee App", "The field team's tool, built mobile-first.",
     ["Capture time, materials, fuel and progress from site",
      "Works for technicians, drivers, welders, operators and labourers"]),
    ("📍", "GPS Tracking", "Know who was on site, and when.",
     ["Every field check-in is GPS-stamped and time-stamped",
      "Distance-from-site flags keep records honest"]),
    ("🤖", "AI Automation", "Let AI do the data entry.",
     ["Extract RFQs, purchase orders, supplier invoices and scopes of work",
      "AI quotation suggestions grounded in your own history"]),
    ("🔒", "Security", "Enterprise-grade protection for your data.",
     ["Role-based access control and full audit logging",
      "Tenant isolation so your data is only ever yours"]),
    ("☁️", "Cloud Platform", "Always on, everywhere.",
     ["Access from desktop, tablet and mobile",
      "Automatic backups and no software to install"]),
]


def features(request):
    ctx = _seo(
        "Features — LulaWorks",
        "Every module in LulaWorks: quotations, jobs, procurement, suppliers, "
        "invoices, delivery notes, the employee app, GPS tracking and AI automation.",
    )
    ctx["modules"] = _MODULES
    return render(request, "marketing/features.html", ctx)


def pricing(request):
    from apps.billing.models import CreditPack
    from apps.billing.services import priced_plans

    from .geo import detect_currency

    currency = detect_currency(request)   # auto: US → USD, UK → GBP, …
    ctx = _seo(
        "Pricing — LulaWorks",
        "Simple, transparent pricing for contractors, in your local currency. "
        "Unlimited employees on every plan.",
    )
    ctx["plans"] = priced_plans(currency)
    ctx["packs"] = list(CreditPack.objects.filter(is_active=True).order_by("price"))
    ctx["currency"] = currency
    return render(request, "marketing/pricing.html", ctx)


def about(request):
    return render(request, "marketing/about.html", _seo(
        "About LulaWorks",
        "Why we built LulaWorks — one platform to replace the spreadsheets, WhatsApp "
        "threads and disconnected tools contractors juggle every day.",
    ))


@require_http_methods(["GET", "POST"])
def contact(request):
    if request.method == "POST":
        from apps.core.validation import InputError, clean_email, clean_str
        try:
            msg = ContactMessage(
                name=clean_str(request.POST.get("name"), field="Name", max_length=200, required=True),
                email=clean_email(request.POST.get("email")),
                company=clean_str(request.POST.get("company"), field="Company", max_length=200),
                subject=clean_str(request.POST.get("subject"), field="Subject", max_length=200),
                message=clean_str(request.POST.get("message"), field="Message", max_length=5000, required=True),
            )
        except InputError as exc:
            messages.error(request, str(exc))
            return redirect("marketing:contact")
        msg.save()
        messages.success(request, "Thanks — we've received your message and will reply shortly.")
        return redirect("marketing:contact")
    return render(request, "marketing/contact.html", _seo(
        "Contact LulaWorks",
        "Get in touch with the LulaWorks team — sales, support and general enquiries.",
    ))


@require_http_methods(["GET", "POST"])
def demo(request):
    if request.method == "POST":
        from apps.core.validation import InputError, clean_email, clean_str
        raw_date = request.POST.get("preferred_date") or None
        try:
            demo_req = DemoRequest(
                company=clean_str(request.POST.get("company"), field="Company", max_length=200, required=True),
                name=clean_str(request.POST.get("name"), field="Name", max_length=200, required=True),
                email=clean_email(request.POST.get("email")),
                phone=clean_str(request.POST.get("phone"), field="Phone", max_length=40),
                industry=clean_str(request.POST.get("industry"), field="Industry", max_length=120),
                employees=clean_str(request.POST.get("employees"), field="Employees", max_length=40),
                preferred_date=raw_date or None,
                preferred_time=clean_str(request.POST.get("preferred_time"), field="Preferred time", max_length=40),
                notes=clean_str(request.POST.get("notes"), field="Notes", max_length=2000),
            )
        except InputError as exc:
            messages.error(request, str(exc))
            return redirect("marketing:demo")
        demo_req.save()
        return redirect("marketing:demo_thanks")
    return render(request, "marketing/demo.html", _seo(
        "Book a Demo — LulaWorks",
        "See LulaWorks in action. Book a personalised 30-minute demo with our team.",
    ))


def demo_thanks(request):
    return render(request, "marketing/demo_thanks.html", _seo(
        "Thank You — LulaWorks", "Your demo request has been received.",
    ))


def faq(request):
    return render(request, "marketing/faq.html", _seo(
        "FAQ — LulaWorks",
        "Answers about LulaWorks: pricing, security, AI, billing, the free trial, "
        "data ownership, migration and cancellation.",
    ))


@require_http_methods(["GET", "POST"])
def trial(request):
    if request.user.is_authenticated:
        return redirect("web:dashboard")
    if request.method == "POST":
        from .geo import detect_currency
        try:
            user = register_trial_company(
                company_name=request.POST.get("company", ""),
                full_name=request.POST.get("full_name", ""),
                email=request.POST.get("email", ""),
                password=request.POST.get("password", ""),
                phone=request.POST.get("phone", ""),
                industry=request.POST.get("industry", ""),
                currency=detect_currency(request),   # bill the new company in its local currency
            )
        except RegistrationError as exc:
            messages.error(request, str(exc))
            return render(request, "marketing/trial.html",
                          {**_seo("Start Free Trial — LulaWorks", ""),
                           "form": request.POST})
        login(request, user)
        messages.success(request, "Welcome to LulaWorks! Your 30-day free trial has started.")
        return redirect("web:dashboard")
    return render(request, "marketing/trial.html", _seo(
        "Start Your Free Trial — LulaWorks",
        "Start a 30-day free trial of LulaWorks. No credit card required. "
        "Professional features, 2 users, unlimited employees, 100 AI credits, 2 GB.",
    ))


def privacy(request):
    return render(request, "marketing/privacy.html", _seo(
        "Privacy Policy — LulaWorks",
        "How LulaWorks collects, uses, processes and protects your data (POPIA & GDPR).",
    ))


def terms(request):
    return render(request, "marketing/terms.html", _seo(
        "Terms & Conditions — LulaWorks",
        "The terms governing your use of LulaWorks, including subscriptions, billing, "
        "data ownership and acceptable use.",
    ))


def cookies(request):
    return render(request, "marketing/cookies.html", _seo(
        "Cookie Policy — LulaWorks",
        "The cookies LulaWorks uses — essential, authentication, preference and "
        "analytics — and how to control them.",
    ))


# ── SEO endpoints ─────────────────────────────────────────────────────────────

def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /dashboard/",
        "Disallow: /api/",
        "Disallow: /admin/",
        f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def sitemap_xml(request):
    today = timezone.localdate().isoformat()
    urls = []
    for name, freq, prio in _SITEMAP:
        loc = request.build_absolute_uri(reverse(name))
        urls.append(
            f"<url><loc>{loc}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>"
    )
    return HttpResponse(xml, content_type="application/xml")
