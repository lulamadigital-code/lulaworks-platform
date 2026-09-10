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
KINDS = ("quotation", "customer_po", "job", "commercial_document", "customer")


@dataclass
class Context:
    kind: str
    id: str
    title: str
    subtitle: str
    fields: dict                       # label -> value (money already gated)
    related: list                      # [{title, items:[{label,sub,type,id,url}]}]
    money_visible: bool
    scope: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "id": self.id, "title": self.title,
            "subtitle": self.subtitle, "fields": self.fields,
            "related": self.related, "money_visible": self.money_visible,
            "scope": self.scope,
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
        return "\n".join(lines)


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


def context_for(user, kind, pk) -> Context | None:
    """Build the grounded context bundle for the record `kind`/`pk` as seen by
    `user`. Returns None if the kind is unknown or the record is not visible in
    the current tenant. Caller must hold tenant context."""
    if kind not in KINDS:
        return None
    obj = resolve_subject(kind, pk)
    if obj is None:
        return None

    can_money = _money_ok(user)
    title, subtitle, fields = _facts(kind, obj, can_money)
    fields = {k: v for k, v in fields.items() if v not in (None, "")}

    related = []
    try:
        related = related_records(obj)
    except Exception:                                # noqa: BLE001
        related = []

    return Context(
        kind=kind, id=str(pk), title=title or kind.title(), subtitle=subtitle or "",
        fields=fields, related=related, money_visible=can_money,
        scope={"user": str(getattr(user, "id", "")), "money_visible": can_money},
    )
