"""Tool Registry (AI_PLATFORM §7) — least-privilege, tenant-scoped, audited.

THE AGENT SECURITY MODEL (the critical hardening): an AI agent executes strictly
within the invoking user's tenant context and RBAC permissions. Every tool call
is subject to the exact same company_id isolation and permission checks as if the
user made it directly — an agent can never read another tenant's data, and never
do what its user isn't allowed to do. No unrestricted database access. Every tool
call is audited. This closes the biggest risk in agentic systems: the AI as a
back door around security.
"""

from collections.abc import Callable
from dataclasses import dataclass

from apps.administration.services import record_audit


class ToolPermissionError(PermissionError):
    """Raised when the invoking user lacks the permission a tool requires."""


@dataclass
class Tool:
    name: str
    required_perm: str | None
    fn: Callable
    description: str = ""


_REGISTRY: dict[str, Tool] = {}


def register(name, required_perm=None, description=""):
    def deco(fn):
        _REGISTRY[name] = Tool(name=name, required_perm=required_perm, fn=fn,
                               description=description)
        return fn
    return deco


def available_tools(user) -> list[str]:
    """Tools the user is permitted to call (least-privilege view)."""
    return [t.name for t in _REGISTRY.values()
            if t.required_perm is None or user.has_perm_code(t.required_perm)]


def run_tool(name, user, **kwargs):
    """Execute a registered tool as the user. Enforces the user's RBAC (raises
    ToolPermissionError otherwise), relies on the ambient tenant for isolation,
    and audits the call. The tenant context must already be set by the caller
    (request or tenant_scope) — the tool reads only through tenant-scoped managers."""
    tool = _REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool '{name}'.")
    if tool.required_perm and not user.has_perm_code(tool.required_perm):
        record_audit(company=user.active_company, user=user, action="ai.tool_denied",
                     after={"tool": name, "required_perm": tool.required_perm})
        raise ToolPermissionError(
            f"Tool '{name}' requires '{tool.required_perm}', which the user lacks."
        )
    result = tool.fn(user, **kwargs)
    record_audit(company=user.active_company, user=user, action="ai.tool_call",
                 after={"tool": name})
    return result


# ── Registered tools (each reads only through tenant-scoped managers) ─────────

@register("search_suppliers", required_perm="procurement.manage",
          description="Top suppliers by performance score")
def _search_suppliers(user, *, limit=5):
    from apps.procurement.models import Supplier
    rows = Supplier.objects.all().order_by("-performance_score")[:limit]
    return [{"name": s.name, "performance_score": str(s.performance_score),
             "categories": s.categories} for s in rows]


@register("project_readiness", required_perm="projects.view",
          description="Compliance readiness for a project")
def _project_readiness(user, *, project):
    from apps.compliance.services import recompute_readiness
    return recompute_readiness(project)


@register("project_profitability", required_perm="finance.view_money",
          description="Live profitability for a project")
def _project_profitability(user, *, project):
    from apps.finance.services import profitability, rebuild_actuals_from_sources
    rebuild_actuals_from_sources(project, user)
    return profitability(project)


@register("project_profit_forecast", required_perm="finance.view_money",
          description="Project profit predictor")
def _project_profit_forecast(user, *, project):
    from apps.finance.services import profit_forecast, rebuild_actuals_from_sources
    rebuild_actuals_from_sources(project, user)
    return profit_forecast(project)


@register("commercial_dashboard", required_perm="finance.view_money",
          description="Portfolio commercial view")
def _commercial_dashboard(user):
    from apps.finance.services import commercial_dashboard
    return commercial_dashboard(user.active_company)


# ── LulaAI assistant read tools (Phase 1a) ────────────────────────────────────
# Each returns grounded data + a human "source" label the assistant cites. Money
# is included ONLY when the user holds finance.view_money (Golden Rule).

def _can_money(user) -> bool:
    return user.has_perm_code("finance.view_money")


@register("supplier_prices", required_perm="procurement.manage",
          description="Recorded supplier prices for an item — who we buy it from, latest price, when")
def _supplier_prices(user, *, item="", limit=8):
    from apps.procurement.models import SupplierPrice
    qs = SupplierPrice.objects.select_related("supplier").all()
    if item:
        qs = qs.filter(description__icontains=item)
    rows = list(qs.order_by("-date", "-created_at")[:limit])
    out = [{"supplier": getattr(p.supplier, "name", "—"), "item": p.description,
            "price": str(p.unit_price), "unit": getattr(p, "unit", "") or "",
            "date": p.date.isoformat() if getattr(p, "date", None) else "",
            "source": "Supplier purchase history"} for p in rows]
    return {"items": out, "source": "Supplier purchase history"}


@register("overdue_tasks", required_perm="projects.view",
          description="Open tasks that are past their due date")
def _overdue_tasks(user, *, limit=15):
    from apps.execution.models import Task
    rows = [t for t in Task.objects.select_related("project", "assignee").all()
            if t.is_overdue][:limit]
    out = [{"id": str(t.id), "name": t.name, "status": t.get_status_display(),
            "due": t.due_date.isoformat() if t.due_date else "",
            "job": getattr(t.project, "name", "") if t.project_id else "",
            "assignee": str(t.assignee) if t.assignee_id else "unassigned"} for t in rows]
    return {"items": out, "count": len(out), "source": "Tasks"}


@register("my_tasks", required_perm="projects.view",
          description="The current user's open/assigned tasks")
def _my_tasks(user, *, limit=15):
    from apps.execution.models import Task, TaskStatus
    open_statuses = [s for s in TaskStatus.values if s not in ("completed", "closed", "cancelled")]
    rows = Task.objects.select_related("project").filter(
        assignee=user, status__in=open_statuses).order_by("due_date")[:limit]
    out = [{"id": str(t.id), "name": t.name, "status": t.get_status_display(),
            "due": t.due_date.isoformat() if t.due_date else "",
            "job": getattr(t.project, "name", "") if t.project_id else ""} for t in rows]
    return {"items": out, "count": len(out), "source": "My tasks"}


@register("quotations_awaiting_approval", required_perm="projects.view",
          description="Quotations waiting for manager/commercial approval")
def _quotes_awaiting(user, *, limit=15):
    from apps.quotes.models import Quotation, QuotationStatus
    qs = Quotation.objects.filter(status__in=[QuotationStatus.MANAGER_APPROVAL,
                                              QuotationStatus.COMMERCIAL_APPROVAL])
    rows = list(qs.order_by("-created_at")[:limit])
    money = _can_money(user)
    out = []
    for q in rows:
        row = {"id": str(q.id), "number": q.number, "client": q.client_name,
               "status": q.get_status_display()}
        if money:
            try:
                row["value"] = str(getattr(q, "grand_total", None) or q.net_total)
            except Exception:                                  # noqa: BLE001
                pass
        out.append(row)
    return {"items": out, "count": len(out), "source": "Quotations"}


@register("unpaid_invoices", required_perm="finance.view_money",
          description="Customer invoices that are not fully paid")
def _unpaid_invoices(user, *, limit=15):
    from apps.finance.models import Invoice, InvoiceStatus
    paid = [InvoiceStatus.PAID]
    rows = list(Invoice.objects.exclude(status__in=paid)
                .exclude(status=InvoiceStatus.DRAFT).order_by("due_date")[:limit])
    out = []
    for inv in rows:
        row = {"id": str(inv.id), "number": inv.number, "client": inv.client_name,
               "status": inv.get_status_display(),
               "due": inv.due_date.isoformat() if getattr(inv, "due_date", None) else ""}
        try:
            row["total"] = str(getattr(inv, "total", None) or (inv.subtotal + inv.vat_amount))
        except Exception:                                      # noqa: BLE001
            pass
        out.append(row)
    return {"items": out, "count": len(out), "source": "Invoices"}


@register("uncontacted_customers", required_perm="customers.manage",
          description="Customers with no recent recorded activity")
def _uncontacted_customers(user, *, days=30, limit=15):
    from datetime import timedelta

    from django.utils import timezone

    from apps.customers.models import Customer
    cutoff = timezone.now() - timedelta(days=int(days))
    rows = list(Customer.objects.filter(updated_at__lt=cutoff).order_by("updated_at")[:limit])
    out = [{"id": str(c.id), "name": c.name, "industry": c.industry,
            "last_touch": c.updated_at.date().isoformat()} for c in rows]
    return {"items": out, "count": len(out), "days": int(days),
            "source": f"Customers with no activity in {int(days)} days"}


@register("job_summary", required_perm="projects.view",
          description="Operational summary of a job/task: progress, outstanding, money (if permitted)")
def _job_summary(user, *, task_id="", project_id=""):
    from apps.execution.models import Task
    from apps.execution.work_execution import task_operational_dashboard
    task = None
    if task_id:
        task = Task.objects.filter(pk=task_id).first()
    elif project_id:
        task = Task.objects.filter(project_id=project_id).order_by("created_at").first()
    if task is None:
        return {"found": False, "source": "Tasks"}
    dash = task_operational_dashboard(task, user)   # already Golden-Rule gated by user
    dash["found"] = True
    dash["source"] = f"Job · {task.name}"
    return dash


# ── LulaAI WRITE tools (Phase 1b) ─────────────────────────────────────────────
# High-risk: only ever invoked via an explicit user confirmation (assistant draft
# → confirm). Permission-checked + audited like every tool.

def _resolve_member(user, token):
    """Find a company member by first-name / email prefix (best-effort)."""
    if not token:
        return None
    from apps.identity.models import Membership
    token = token.strip().lower()
    for m in Membership.objects.filter(company=user.active_company).select_related("user"):
        u = m.user
        name = (u.get_full_name() or "").lower()
        if (name.startswith(token) or token in name.split()
                or (u.email or "").lower().startswith(token)):
            return u
    return None


@register("create_task", required_perm="work.create",
          description="Create a task (title, optional assignee/due/job)")
def _create_task(user, *, title, assignee="", assignee_id="", due="", project_id="",
                 notes=""):
    from datetime import date

    from apps.execution.models import Task
    due_val = None
    if due:
        try:
            due_val = date.fromisoformat(due)
        except ValueError:
            due_val = None
    # Task.assignee is a Resource, and person-assignment is a separate team step,
    # so we record the requested name in the description rather than mis-assign.
    desc = notes or ""
    if assignee:
        desc = (f"Requested assignee: {assignee}\n{desc}").strip()
    task = Task.objects.create(
        company=user.active_company, name=title.strip(), due_date=due_val,
        project_id=project_id or None, description=desc,
        created_by=user, updated_by=user)
    return {"ok": True, "id": str(task.id), "name": task.name,
            "assignee": assignee or "unassigned"}


@register("send_customer_email", required_perm="customers.manage",
          description="Send an email to a customer/contact (subject + body)")
def _send_customer_email(user, *, to, subject, body, customer_id=""):
    from apps.notifications.models import EmailCategory
    from apps.notifications.service import send_email
    if not (to and subject):
        raise ValueError("An email needs a recipient and a subject.")
    log = send_email(to=to.strip(), subject=subject.strip(), template="generic",
                     context={"body": body or "", "subject": subject.strip()},
                     company=user.active_company, category=EmailCategory.CRM,
                     sent_by=user)
    return {"ok": True, "to": to.strip(), "log_id": str(log.id)}


@register("send_whatsapp_text", required_perm="customers.manage",
          description="Send a WhatsApp text to one number via the company's connection")
def _send_whatsapp_text(user, *, phone, text):
    from apps.campaigns.whatsapp import _post_message, _wa_number, get_connection
    conn = get_connection(user.active_company)
    if not (conn and conn.is_connected):
        raise ValueError("WhatsApp isn't connected for this company yet.")
    if not phone:
        raise ValueError("A WhatsApp recipient number is required.")
    mid = _post_message(conn, _wa_number(phone), text=text or "")
    return {"ok": True, "phone": phone, "message_id": mid}


# ── One more read tool: customer relationship summary (§20) ───────────────────

@register("customer_summary", required_perm="customers.manage",
          description="A customer's relationship snapshot: status, opportunities, last touch")
def _customer_summary(user, *, customer_id="", name=""):
    from apps.customers.models import Customer, OpportunityStage
    cust = None
    if customer_id:
        cust = Customer.objects.filter(pk=customer_id).first()
    elif name:
        cust = Customer.objects.filter(name__icontains=name).first()
    if cust is None:
        return {"found": False, "source": "Customers"}
    opps = cust.opportunities.all()
    open_opps = [o for o in opps if o.stage not in (OpportunityStage.WON, OpportunityStage.LOST)]
    return {"found": True, "name": cust.name, "status": cust.get_status_display(),
            "industry": cust.industry, "open_opportunities": len(open_opps),
            "total_opportunities": opps.count(),
            "last_touch": cust.updated_at.date().isoformat(),
            "source": f"Customer · {cust.name}"}
