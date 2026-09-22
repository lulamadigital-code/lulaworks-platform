"""Public Enterprise-agreement confirmation page.

No login required — the signed token in the URL is the authorisation and also
carries the agreed-terms snapshot, so the page shows exactly what was emailed.
The customer accepts or requests a change; both notify sales.
"""
from django.shortcuts import render
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET", "POST"])
def agreement_review(request, token):
    from apps.billing.services import read_agreement_token, record_agreement_response
    from apps.identity.models import Company

    snap = read_agreement_token(token)
    company = Company.objects.filter(pk=snap.get("c")).first() if snap else None
    if not snap or company is None:
        return render(request, "web/agreement.html", {"invalid": True})

    done = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "accept":
            record_agreement_response(company, accepted=True, snapshot=snap)
            done = "accepted"
        elif action == "change":
            record_agreement_response(
                company, accepted=False, message=request.POST.get("message", ""))
            done = "change"

    def _gb(v):
        try:
            return f"{int(v) / (1024 ** 3):.0f} GB" if v else ""
        except (TypeError, ValueError):
            return ""

    def _comma(v):
        try:
            return f"{int(v):,}"
        except (TypeError, ValueError):
            return v or ""

    sub = getattr(company, "subscription", None)
    already = bool((sub.overrides or {}).get("agreement_accepted_at")) if sub else False

    return render(request, "web/agreement.html", {
        "company": company, "snap": snap, "storage": _gb(snap.get("storage")),
        "credits": _comma(snap.get("credits")), "do": request.GET.get("do", ""),
        "done": done, "already": already,
    })
