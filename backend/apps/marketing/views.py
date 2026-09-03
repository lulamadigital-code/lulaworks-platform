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
    ("marketing:learn", "weekly", "0.8"),
    ("marketing:tools", "monthly", "0.8"),
    ("marketing:templates", "monthly", "0.8"),
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
        "Lulaworks — From Quotation to Payment. One Platform. Powered by AI.",
        "Lulaworks helps contractors manage quotations, jobs, procurement, teams, "
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
        "Features — Lulaworks",
        "Every module in Lulaworks: quotations, jobs, procurement, suppliers, "
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
        "Pricing — Lulaworks",
        "Simple, transparent pricing for contractors, in your local currency. "
        "Unlimited employees on every plan.",
    )
    ctx["plans"] = priced_plans(currency)
    ctx["packs"] = list(CreditPack.objects.filter(is_active=True).order_by("price"))
    ctx["currency"] = currency
    return render(request, "marketing/pricing.html", ctx)


def about(request):
    return render(request, "marketing/about.html", _seo(
        "About Lulaworks",
        "Why we built Lulaworks — one platform to replace the spreadsheets, WhatsApp "
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
            # Re-render with what they typed so nothing is lost.
            return render(request, "marketing/contact.html", {
                **_seo("Contact Lulaworks", ""), "form": request.POST})
        msg.save()
        messages.success(request, "Thanks — we've received your message and will reply shortly.")
        return redirect("marketing:contact")
    return render(request, "marketing/contact.html", _seo(
        "Contact Lulaworks",
        "Get in touch with the Lulaworks team — sales, support and general enquiries.",
    ))


_DEMO_INDUSTRIES = ["General contracting", "Mechanical / engineering",
                    "Mining contractor", "Maintenance services",
                    "Industrial services", "Construction", "Other"]
_DEMO_EMPLOYEES = ["1–10", "11–50", "51–200", "200+"]


@require_http_methods(["GET", "POST"])
def demo(request):
    demo_ctx = {"industry_opts": _DEMO_INDUSTRIES, "employee_opts": _DEMO_EMPLOYEES}
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
            return render(request, "marketing/demo.html", {
                **_seo("Book a Demo — Lulaworks", ""), **demo_ctx,
                "form": request.POST})
        demo_req.save()
        return redirect("marketing:demo_thanks")
    return render(request, "marketing/demo.html", {
        **_seo("Book a Demo — Lulaworks",
               "See Lulaworks in action. Book a personalised 30-minute demo with our team."),
        **demo_ctx})


def demo_thanks(request):
    return render(request, "marketing/demo_thanks.html", _seo(
        "Thank You — Lulaworks", "Your demo request has been received.",
    ))


def faq(request):
    return render(request, "marketing/faq.html", _seo(
        "FAQ — Lulaworks",
        "Answers about Lulaworks: pricing, security, AI, billing, the free trial, "
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
                          {**_seo("Start Free Trial — Lulaworks", ""),
                           "form": request.POST})
        login(request, user)
        # Close the content → signup loop: credit the matching Education lead.
        from apps.education.leads import score_signup
        score_signup(user.email, request=request)
        messages.success(request, "Welcome to Lulaworks! Your 30-day free trial has started.")
        return redirect("web:dashboard")
    return render(request, "marketing/trial.html", _seo(
        "Start Your Free Trial — Lulaworks",
        "Start a 30-day free trial of Lulaworks. No credit card required. "
        "Professional features, 2 users, unlimited employees, 100 AI credits, 2 GB.",
    ))


def privacy(request):
    return render(request, "marketing/privacy.html", _seo(
        "Privacy Policy — Lulaworks",
        "How Lulaworks collects, uses, processes and protects your data (POPIA & GDPR).",
    ))


def terms(request):
    return render(request, "marketing/terms.html", _seo(
        "Terms & Conditions — Lulaworks",
        "The terms governing your use of Lulaworks, including subscriptions, billing, "
        "data ownership and acceptable use.",
    ))


def cookies(request):
    return render(request, "marketing/cookies.html", _seo(
        "Cookie Policy — Lulaworks",
        "The cookies Lulaworks uses — essential, authentication, preference and "
        "analytics — and how to control them.",
    ))


# ── Learning Centre (Education & Growth Engine) ───────────────────────────────

def learn(request):
    """The Lulaworks Academy home — featured content, categories and the guided
    learning paths. Public and inbound-first."""
    from apps.education.models import ResourceCategory
    from apps.education.services import (
        featured_resources,
        published_paths,
        published_resources,
    )
    cats = []
    for cat in ResourceCategory.objects.all():
        items = list(published_resources().filter(category=cat)[:6])
        if items:
            cats.append({"category": cat, "resources": items})
    ctx = _seo("Lulaworks Academy — Learn to win more work, quote better and get paid",
               "Free guides, tools and templates that help contractors and "
               "suppliers win more work, quote professionally, control procurement "
               "and get paid faster.")
    ctx.update({
        "featured": featured_resources(3),
        "categories": cats,
        "paths": list(published_paths()),
        "latest": list(published_resources()[:6]),
    })
    return render(request, "marketing/learn.html", ctx)


def learn_resource(request, slug):
    """A single guide / template / calculator, with its content→product CTA."""
    from django.shortcuts import get_object_or_404

    from apps.analytics.services import track
    from apps.education.models import Resource
    from apps.education.services import published_resources
    # Staff preview: platform staff can view an unpublished draft via ?preview=1
    # (so the console's Preview button works before publishing). Everyone else
    # only ever sees published content.
    is_preview = bool(request.GET.get("preview")) and \
        request.user.is_authenticated and request.user.can_platform("console")
    if is_preview:
        resource = get_object_or_404(Resource, slug=slug)
    else:
        resource = get_object_or_404(published_resources(), slug=slug)
        track("content_viewed", request=request, module="education",
              feature=resource.kind, source="learn",
              metadata={"slug": resource.slug, "category":
                        resource.category.slug if resource.category_id else ""})
    related = list(published_resources()
                   .filter(category=resource.category)
                   .exclude(pk=resource.pk)[:4]) if resource.category_id else []
    ctx = _seo(resource.seo_title or f"{resource.title} — Lulaworks Academy",
               resource.seo_description or resource.summary)
    ctx.update({"resource": resource, "related": related})
    return render(request, "marketing/learn_resource.html", ctx)


def learn_path(request, slug):
    """A guided learning path — an ordered set of lessons toward a business goal."""
    from django.shortcuts import get_object_or_404

    from apps.analytics.services import track
    from apps.education.services import published_paths
    path = get_object_or_404(
        published_paths().prefetch_related("steps", "steps__resource"), slug=slug)
    track("content_viewed", request=request, module="education", feature="path",
          source="learn", metadata={"slug": path.slug})
    ctx = _seo(f"{path.title} — Lulaworks Academy", path.summary)
    ctx.update({"path": path, "steps": list(path.steps.all())})
    return render(request, "marketing/learn_path.html", ctx)


# ── Free tools (calculators) — customer acquisition ───────────────────────────

def tools(request):
    """Index of the free calculators — each a genuinely useful tool that connects
    to a Lulaworks feature."""
    from apps.billing.models import currency_symbol
    from apps.education.tools import (localize_tax_spec, published_tool_specs,
                                      tax_for)
    from apps.marketing.geo import detect_currency
    tax_name, tax_rate = tax_for(detect_currency(request))
    tools_list = [localize_tax_spec(t, tax_name, tax_rate)
                  if t.maths_key == "vat-calculator" else t
                  for t in published_tool_specs()]
    ctx = _seo("Free tools for contractors — profit, markup, tax & break-even calculators",
               "Free calculators for contractors and suppliers: job profit, markup "
               "vs margin, tax and break-even. No signup required.")
    ctx["tools"] = tools_list
    return render(request, "marketing/tools.html", ctx)


def tool(request, slug):
    """A single calculator: problem → inputs → result → explanation → CTA. GET
    shows the form; POST computes server-side and shows the result."""
    from django.http import Http404

    from apps.analytics.services import track
    from apps.education.tools import compute, published_tool_specs, tool_spec
    # Staff preview: view an unpublished draft via ?preview=1 (console Preview btn).
    if request.GET.get("preview") and request.user.is_authenticated \
            and request.user.can_platform("console"):
        from apps.education.models import Tool
        row = Tool.objects.filter(slug=slug).first()
        spec = row.to_spec() if row else None
    else:
        spec = tool_spec(slug)
    if spec is None:
        raise Http404("Unknown tool")

    # Show amounts in the visitor's own currency (not always Rands) and localise
    # the VAT tool's tax name (VAT / GST / Sales Tax) to their region.
    from apps.billing.models import CURRENCY_SYMBOLS, currency_symbol
    from apps.education.tools import localize_tax_spec, tax_for
    from apps.marketing.geo import detect_currency
    ccy = detect_currency(request)
    sym = currency_symbol(ccy)
    tax_name, tax_rate = tax_for(ccy)
    maths_key = spec.maths_key
    if maths_key == "vat-calculator":
        spec = localize_tax_spec(spec, tax_name, tax_rate)

    results, values = None, {}
    if request.method == "POST":
        values = {f.name: request.POST.get(f.name, "") for f in spec.inputs}
        results = compute(maths_key, values, symbol=sym, tax_name=tax_name)
        track("tool_completed", request=request, module="education",
              feature=spec.related_feature, source="tools",
              metadata={"slug": slug, "currency": ccy})
    else:
        values = {f.name: f.default for f in spec.inputs}
        track("tool_started", request=request, module="education",
              feature=spec.related_feature, source="tools", metadata={"slug": slug})

    fields = [{"f": f, "value": values.get(f.name, "")} for f in spec.inputs]
    ctx = _seo(f"{spec.title} — free tool by Lulaworks", spec.summary)
    others = [localize_tax_spec(t, tax_name, tax_rate) if t.maths_key == "vat-calculator" else t
              for t in published_tool_specs() if t.slug != slug][:3]
    ctx.update({"tool": spec, "results": results, "fields": fields,
                "currency": ccy, "currency_symbol": sym,
                "currencies": list(CURRENCY_SYMBOLS.items()),
                "other_tools": others})
    return render(request, "marketing/tool.html", ctx)


# ── Lead capture (progressive, opt-in) ────────────────────────────────────────

@require_http_methods(["POST"])
def lead_capture(request):
    """Opt-in lead capture from the Academy / tools / templates. The content is
    always usable without this — it's a voluntary 'get more' / 'save' step."""
    from apps.education.leads import capture_lead
    email = request.POST.get("email", "")
    lead = capture_lead(
        email=email,
        event=request.POST.get("event", "opt_in"),
        request=request,
        detail=request.POST.get("source", ""),
        name=request.POST.get("name", ""),
        company=request.POST.get("company", ""),
        industry=request.POST.get("industry", ""),
        company_size=request.POST.get("company_size", ""),
        role=request.POST.get("role", ""),
        phone=request.POST.get("phone", ""),
        challenge=request.POST.get("challenge", ""),
    )
    if lead is None:
        messages.error(request, "Please enter a valid email address.")
        back = request.POST.get("next") or reverse("marketing:learn")
        return redirect(back)
    return redirect("marketing:lead_thanks")


def lead_thanks(request):
    """Progressive next step after opting in: nudge toward a free account, which
    is where the real value (and the strongest conversion) lives."""
    return render(request, "marketing/lead_thanks.html", _seo(
        "You're on the list — Lulaworks", "Thanks for joining."))


def unsubscribe(request, token):
    """One-click unsubscribe from Academy emails (no login). Honours the link in
    every marketing email so 'unsubscribe any time' is real."""
    from apps.education.leads import lead_from_token
    lead = lead_from_token(token)
    if lead is not None and lead.subscribed:
        lead.subscribed = False
        lead.save(update_fields=["subscribed", "updated_at"])
    return render(request, "marketing/unsubscribed.html",
                  {**_seo("Unsubscribed — Lulaworks", ""),
                   "ok": lead is not None, "email": lead.email if lead else ""})


# ── Templates library ─────────────────────────────────────────────────────────

def templates_lib(request):
    """Index of the free business templates — delivered through Lulaworks."""
    from apps.education.templates_lib import published_template_specs
    ctx = _seo("Free business templates for contractors — quotation, invoice, RFQ & more",
               "Free, professional templates for contractors and suppliers: "
               "quotation, tax invoice, delivery note, RFQ, purchase order, "
               "checklists and payment follow-up emails.")
    ctx["templates"] = published_template_specs()
    return render(request, "marketing/templates_lib.html", ctx)


def template_detail(request, slug):
    """A single template: what it's for, what it includes (or its usable content),
    and a CTA to create it in Lulaworks."""
    from django.http import Http404

    from apps.analytics.services import track
    from apps.education.templates_lib import published_template_specs, template_spec
    if request.GET.get("preview") and request.user.is_authenticated \
            and request.user.can_platform("console"):
        from apps.education.models import Template
        row = Template.objects.filter(slug=slug).first()
        spec = row.to_spec() if row else None
    else:
        spec = template_spec(slug)
    if spec is None:
        raise Http404("Unknown template")
    track("template_viewed", request=request, module="education",
          feature=spec.related_feature, source="templates", metadata={"slug": slug})
    ctx = _seo(f"{spec.title} — free template by Lulaworks", spec.summary)
    ctx.update({"tpl": spec,
                "others": [t for t in published_template_specs() if t.slug != slug][:3]})
    return render(request, "marketing/template_detail.html", ctx)


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

    def add(loc, lastmod=today, freq="monthly", prio="0.6"):
        urls.append(
            f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{prio}</priority></url>")

    for name, freq, prio in _SITEMAP:
        add(request.build_absolute_uri(reverse(name)), freq=freq, prio=prio)

    # Academy content — every published guide, learning path, free tool and
    # template, so Google can index them individually.
    try:
        from apps.education.services import published_paths, published_resources
        from apps.education.templates_lib import TEMPLATES
        from apps.education.tools import TOOLS
        for r in published_resources().only("slug", "updated_at"):
            add(request.build_absolute_uri(reverse("marketing:learn_resource", args=[r.slug])),
                lastmod=(r.updated_at.date().isoformat() if r.updated_at else today),
                freq="monthly", prio="0.7")
        for p in published_paths().only("slug"):
            add(request.build_absolute_uri(reverse("marketing:learn_path", args=[p.slug])),
                freq="monthly", prio="0.6")
        for slug in TOOLS:
            add(request.build_absolute_uri(reverse("marketing:tool", args=[slug])),
                freq="monthly", prio="0.7")
        for slug in TEMPLATES:
            add(request.build_absolute_uri(reverse("marketing:template_detail", args=[slug])),
                freq="monthly", prio="0.7")
    except Exception:                       # noqa: BLE001 - sitemap must never 500
        pass

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>"
    )
    return HttpResponse(xml, content_type="application/xml")
