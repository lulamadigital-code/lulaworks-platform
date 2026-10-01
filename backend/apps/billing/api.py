"""Read-only billing summary for the mobile client.

Surfaces the company's plan, status, renewal, credit balance, seat and storage
usage — everything the app's Billing screen shows. It is READ-ONLY by design:
plan changes and payments happen on the web/PayFast flow (and Enterprise is
contact-sales), so there is nothing here that charges or mutates a subscription.
Gated on company.manage — billing is owner/admin information.
"""
from rest_framework.response import Response
from rest_framework.views import APIView


class BillingSummaryView(APIView):
    """GET /api/v1/billing/summary/ → the company's plan + usage, read-only."""

    def get(self, request):
        from apps.core.middleware import set_tenant_from_request
        set_tenant_from_request(request)
        user = request.user
        if not user.has_perm_code("company.manage"):
            return Response(
                {"error": {"code": "forbidden",
                           "message": "Billing is available to company admins."}},
                status=403)

        company = user.active_company
        sub = getattr(company, "subscription", None)
        plan = sub.plan if sub else None

        from apps.identity.models import Membership
        seats_used = (Membership.objects.filter(company=company, status="active")
                      .count())

        data = {
            "status": sub.status if sub else (company.subscription_status or "trial"),
            "currency": (sub.currency if sub else None) or company.currency or "ZAR",
            "billing_cycle": sub.billing_cycle if sub else None,
            "current_period_end": sub.current_period_end if sub else None,
            "cancel_at_period_end": bool(sub.cancel_at_period_end) if sub else False,
            "plan": None,
            "credits": {
                "balance": str(company.ai_credit_balance),
                "monthly": str(plan.monthly_ai_credits) if plan else None,
            },
            "seats": {
                "used": seats_used,
                "included": (sub.seats if sub and sub.seats else
                             (plan.max_users if plan else company.max_users)),
            },
            "storage": {
                "used_bytes": company.storage_used_bytes,
                "quota_bytes": company.storage_quota_bytes,
            },
        }
        if plan:
            cycle = (sub.billing_cycle if sub else "monthly") or "monthly"
            try:
                price = plan.price_in(data["currency"], cycle)
            except Exception:  # noqa: BLE001 - price table edge cases never 500 the view
                price = plan.price
            data["plan"] = {
                "code": plan.code,
                "name": plan.name,
                "tier": plan.tier,
                "price": str(price),
                "features": plan.features or [],
                "api_access": plan.api_access,
                "support_level": plan.support_level,
            }
        return Response(data)
