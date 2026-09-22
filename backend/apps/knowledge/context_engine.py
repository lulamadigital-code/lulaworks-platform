"""AI Context Engine — "what does LulaAI know at THIS record?".

Given a user and the record they are looking at (a quotation, PO, job,
invoice or customer), this assembles ONE permission-scoped context bundle:
the record's own key facts, the related-records chain around it (reusing the
business graph), and a compact text serialisation ready to ground a prompt.

This is the single place LulaAI turns "where the user is" into "what LulaAI
may know here" (LULAWORKS AI OS §3, §4, §19). It is:
* TENANT-SCOPED — the subject is fetched through tenant-scoped managers, so the
  caller must hold tenant context; there is no cross-tenant path here.
* PERMISSION-SCOPED — money facts are withheld from users without
  finance.view_money (the Golden Rule), exactly as the ERP does.
* READ-ONLY and grounded — it reports only what the ERP holds; it invents
  nothing. A caller that finds no context says so rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.web.relations import related_records, resolve_subject

# The record kinds the engine can ground on today (keys of relations.resolve_subject).
KINDS = ("quotation", "customer_po", "job", "commercial_document", "customer",
         "account")


@dataclass
class Context:
    kind: str
    id: str
    title: str
    subtitle: str
    fields: dict                       # label -> value (money already gated)
    related: list                      # [{title, items:[{label,sub,type,id,url}]}]
    money_visible: bool
    history: dict = field(default_factory=dict)   # imported-archive intelligence
    scope: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "id": self.id, "title": self.title,
            "subtitle": self.subtitle, "fields": self.fields,
            "related": self.related, "money_visible": self.money_visible,
            "history": self.history, "scope": self.scope,
        }

    def as_prompt_text(self) -> str:
        """A compact grounding block for LulaAI — facts only, no invention."""
        lines = [f"# {self.title}", ""]
        if self.subtitle:
            lines.append(self.subtitle)
            lines.append("")
        for label, value in self.fields.items():
            if value not in (None, "", []):
                lines.append(f"- {label}: {value}")
        if not self.money_visible:
            lines.append("- (monetary values withheld — user lacks finance access)")
        for section in self.related:
            names = ", ".join(i["label"] for i in section["items"][:8])
            if names:
                lines.append(f"- {section['title']}: {names}")
        lines.extend(_history_prompt_lines(self.history))
        return "\n".join(lines)


def _history_prompt_lines(history: dict) -> list[str]:
    """Serialise the imported-archive intelligence for the grounding prompt.

    Clearly fenced and labelled as HISTORICAL/ADVISORY so LulaAI never presents
    it as a live ERP record and never lets it drive a live quote/PO/job. Money
    figures inside are already gated by the intelligence service."""
    if not history:
        return []
    out: list[str] = []
    cust = history.get("customer")
    if cust and cust.get("found"):
        out += ["", "## Historical activity (imported business history — ADVISORY, not live records)"]
        out.append(f"- Previous jobs on record: {cust.get('job_count', 0)}")
        if cust.get("money_visible") and cust.get("total_value"):
            out.append(f"- Historical value (advisory): {cust['total_value']}")
        lj = cust.get("last_job")
        if lj:
            bits = [lj.get("title") or "job"]
            if lj.get("work_type"):
                bits.append(lj["work_type"])
            if lj.get("occurred_on"):
                bits.append(lj["occurred_on"])
            if cust.get("money_visible") and lj.get("value"):
                bits.append(str(lj["value"]))
            out.append(f"- Last job: {' · '.join(bits)}")
        charged = cust.get("charged_items") or []
        for it in charged[:5]:
            price = it.get("unit_price")
            out.append(f"  · charged “{it.get('description', '')}”"
                       + (f" @ {price}" if price else ""))
    sims = history.get("similar_jobs")
    if sims:
        out += ["", "## Similar past jobs (imported business history — ADVISORY)"]
        for j in sims[:5]:
            bits = [j.get("title") or "job"]
            if j.get("work_type"):
                bits.append(j["work_type"])
            if j.get("occurred_on"):
                bits.append(j["occurred_on"])
            if j.get("value"):
                bits.append(str(j["value"]))
            out.append(f"- {' · '.join(bits)}")
    if out:
        out += ["", "(Historical items above are advisory context from imported "
                "documents — never treat them as live records or let them change "
                "a live quotation, price or job on their own.)"]
    return out


def _money_ok(user) -> bool:
    try:
        return bool(user.has_perm_code("finance.view_money"))
    except Exception:                                # noqa: BLE001
        return False


def _g(obj, *names):
    """First non-empty attribute among names (defensive across model variants)."""
    for n in names:
        v = getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return None


def _facts(kind, obj, can_money) -> tuple[str, str, dict]:
    """(title, subtitle, fields) for the subject, with money fields gated."""
    if kind == "customer":
        title = _g(obj, "display_name", "name") or "Customer"
        fields = {
            "Status": _g(obj, "status"),
            "Type": _g(obj, "customer_type"),
            "City": _g(obj, "city"),
            "Email": _g(obj, "email"),
            "Phone": _g(obj, "mobile", "telephone"),
            "VAT": _g(obj, "vat_no"),
        }
        return title, "Customer", fields

    if kind == "quotation":
        title = f"Quotation {_g(obj, 'number') or ''}".strip()
        fields = {
            "Customer": _g(getattr(obj, "customer", None) or object(), "display_name", "name"),
            "Client": _g(obj, "client_name"),
            "Status": _g(obj, "status"),
            "Date": _g(obj, "created_at"),
        }
        if can_money:
            fields["Total"] = _g(obj, "total", "grand_total", "total_incl")
        return title, _g(obj, "title", "client_name") or "Quotation", fields

    if kind == "customer_po":
        title = f"Customer PO {_g(obj, 'po_number') or ''}".strip()
        fields = {
            "Client": _g(obj, "client_name", "customer_display"),
            "Site": _g(obj, "site"),
            "Matched": _g(obj, "is_matched"),
        }
        if can_money:
            fields["Value"] = _g(obj, "value", "total")
        return title, "Customer PO", fields

    if kind == "job":
        title = f"Job {_g(obj, 'number') or ''}".strip()
        fields = {
            "Title": _g(obj, "title"),
            "Status": _g(obj, "status"),
            "Start": _g(obj, "start_date", "created_at"),
        }
        if can_money:
            fields["Value"] = _g(obj, "value", "contract_value")
        return title, _g(obj, "title") or "Job", fields

    if kind == "commercial_document":
        disp = getattr(obj, "get_kind_display", lambda: "Document")()
        title = f"{disp} {_g(obj, 'number') or ''}".strip()
        fields = {"Status": _g(obj, "status"), "Date": _g(obj, "created_at")}
        if can_money:
            fields["Amount"] = _g(obj, "total", "amount", "grand_total")
        return title, disp, fields

    return (_g(obj, "number", "name") or kind.title()), "", {}


def _history_for(kind, obj, user) -> dict:
    """Historical-archive intelligence relevant to this subject, from the ONE
    shared retrieval service (knowledge.intelligence) — the same source the
    module Intelligence panels and LulaAI's history tools use. Fail-safe: any
    error yields an empty bundle rather than breaking grounding. Read-only,
    evidence-backed, money-gated inside the service."""
    try:
        from . import intelligence
    except Exception:                                # noqa: BLE001
        return {}
    try:
        if kind == "customer":
            intel = intelligence.customer_intelligence(obj, user)
            return {"customer": intel} if intel.get("found") else {}
        if kind == "job":
            sims = intelligence.similar_jobs(
                user, work_type=getattr(obj, "work_type", "") or "",
                keywords=getattr(obj, "title", "") or "",
                customer_id=str(getattr(obj, "customer_id", "") or ""))
            return {"similar_jobs": sims} if sims else {}
        if kind == "quotation":
            sims = intelligence.similar_jobs(
                user, keywords=getattr(obj, "title", "") or "",
                customer_id=str(getattr(obj, "customer_id", "") or ""))
            return {"similar_jobs": sims} if sims else {}
    except Exception:                                # noqa: BLE001
        return {}
    return {}


def _account_context(user, pk) -> Context | None:
    """Grounding for the tenant's own Enterprise account — plan, contract dates,
    usage (contracted/used/remaining) and factual signals. Read-only facts; money
    figures are gated on finance access, and only the user's OWN company resolves
    (tenant isolation)."""
    from apps.identity.models import Company
    own = getattr(user, "active_company_id", None)
    if own is None or str(pk) != str(own):
        return None
    company = Company.objects.filter(pk=own).first()
    if company is None:
        return None
    can_money = _money_ok(user)

    from apps.billing import services as billing

    def _gb(v):
        return f"{float(v):.0f} GB" if v is not None else "—"

    fields = {}
    sub = getattr(company, "subscription", None)
    plan = getattr(getattr(sub, "plan", None), "name", None)
    if plan:
        fields["Plan"] = plan
    try:
        st = billing.enterprise_state(company)
        fields["Account status"] = st.get("account")
        fields["Agreement"] = st.get("agreement")
    except Exception:                                # noqa: BLE001
        pass
    ov = (sub.overrides if sub else None) or {}
    if ov.get("contract_term_months"):
        fields["Contract term"] = f"{ov['contract_term_months']} months"
    if ov.get("contract_end"):
        fields["Renews / ends"] = ov["contract_end"]
    if can_money and ov.get("contract_price"):
        fields["Contract price"] = ov["contract_price"]
    try:
        u = billing.enterprise_usage(company)
        fields["Seats"] = (f"{u['seats']['used']:.0f} used / "
                           f"{u['seats']['contracted']:.0f} contracted "
                           f"({u['seats']['remaining']:.0f} remaining)")
        fields["Storage"] = (f"{_gb(u['storage']['used'])} used / "
                             f"{_gb(u['storage']['contracted'])} contracted")
        fields["AI credits"] = (f"{u['credits']['used']:.0f} used / "
                                f"{u['credits']['contracted']:.0f} this cycle "
                                f"({u['credits']['remaining']:.0f} remaining)")
    except Exception:                                # noqa: BLE001
        pass
    try:
        sigs = billing.enterprise_signals(company)
        if not can_money:
            sigs = [s for s in sigs if "invoice" not in s["text"].lower()]
        if sigs:
            fields["Alerts"] = " · ".join(s["text"] for s in sigs)
    except Exception:                                # noqa: BLE001
        pass

    return Context(
        kind="account", id=str(pk), title=f"{company.name} — account",
        subtitle="Enterprise account facts (read-only; never changes anything).",
        fields={k: v for k, v in fields.items() if v not in (None, "")},
        related=[], money_visible=can_money, history={},
        scope={"user": str(getattr(user, "id", "")), "money_visible": can_money})


def context_for(user, kind, pk) -> Context | None:
    """Build the grounded context bundle for the record `kind`/`pk` as seen by
    `user`. Returns None if the kind is unknown or the record is not visible in
    the current tenant. Caller must hold tenant context.

    Grounding draws on the LIVE ERP record + its related-records chain AND, where
    relevant, ADVISORY intelligence reconstructed from the imported business
    history (customer relationship, similar past jobs) — kept clearly separate so
    LulaAI never confuses historical context with a live record."""
    if kind not in KINDS:
        return None
    if kind == "account":
        return _account_context(user, pk)
    obj = resolve_subject(kind, pk)
    if obj is None:
        return None

    can_money = _money_ok(user)
    title, subtitle, fields = _facts(kind, obj, can_money)
    fields = {k: v for k, v in fields.items() if v not in (None, "")}

    related = []
    try:
        related = related_records(obj, user)
    except Exception:                                # noqa: BLE001
        related = []

    return Context(
        kind=kind, id=str(pk), title=title or kind.title(), subtitle=subtitle or "",
        fields=fields, related=related, money_visible=can_money,
        history=_history_for(kind, obj, user),
        scope={"user": str(getattr(user, "id", "")), "money_visible": can_money},
    )
