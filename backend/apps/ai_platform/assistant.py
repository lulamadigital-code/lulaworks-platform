"""LulaAI assistant (redesign Phase 1a) — the intelligence layer.

Not a chatbot: a message is turned into QUESTION → DATA → REASONING → ANSWER over
the company's real data, never free-text invention. The flow is deterministic-
first (so it's correct, testable and free of AI cost) with optional AI phrasing:

    message → intent (deterministic) → permission-checked TOOL → grounded data
            → structured answer (+ cited sources, suggested actions)
            → optional AI phrasing of ONLY those facts

Permissions and tenancy are enforced by the tool layer (apps.ai_platform.tools);
the assistant never bypasses them. If a tool the user can't access is needed, the
answer says so. If no data is found, it says so — it never invents.
"""
import re

from .tools import ToolPermissionError, run_tool

_STOP = "i couldn't find that in LulaWorks."


def _item_after(msg, *keywords):
    """Rough entity extraction: text after 'for/of/buy …'."""
    m = re.search(r"(?:for|of|buy|buying)\s+(.+?)(?:\?|\.|$)", msg)
    return m.group(1).strip() if m else ""


def _days(msg, default=30):
    m = re.search(r"(\d+)\s*day", msg)
    return int(m.group(1)) if m else default


def classify(message: str, context: dict | None = None) -> tuple[str, dict]:
    """Map a message (+ optional page context) to an intent + tool params.
    Deterministic — no AI, so it's fast, free and testable."""
    m = (message or "").lower().strip()
    ctx = context or {}

    # "Summarise this" while on a task/job page.
    if any(w in m for w in ("summar", "overview", "tell me about")) and \
            (ctx.get("type") in ("task", "job") or "job" in m or "task" in m):
        return "job_summary", {"task_id": ctx.get("id", "") if ctx.get("type") in ("task", "job") else ""}

    if "overdue" in m:
        return "overdue_tasks", {}
    if any(w in m for w in ("my task", "my work", "what must i", "what do i", "today")):
        return "my_tasks", {}
    if ("quotation" in m or "quote" in m) and any(
            w in m for w in ("approv", "await", "waiting", "pending", "sign off")):
        return "quotations_awaiting_approval", {}
    if "invoice" in m and any(w in m for w in ("unpaid", "outstanding", "owed", "not paid", "due")):
        return "unpaid_invoices", {}
    if "customer" in m and any(
            w in m for w in ("not contact", "haven't", "havent", "no contact", "not been contact", "quiet")):
        return "uncontacted_customers", {"days": _days(m)}
    # Write intents (prepared as a draft, never executed here).
    if any(w in m for w in ("create a task", "add a task", "make a task", "new task")) \
            or re.search(r"task for \w+", m):
        return "create_task", _parse_task(message)
    if ("email" in m or "e-mail" in m) and any(
            w in m for w in ("draft", "write", "send", "compose", "reply")):
        return "draft_email", _parse_email(message)
    if "whatsapp" in m and any(w in m for w in ("draft", "send", "message", "write")):
        return "draft_whatsapp", _parse_whatsapp(message, context)
    if "customer" in m and any(
            w in m for w in ("summar", "relationship", "about", "history")):
        return "customer_summary", {"name": _customer_name(message)}

    if any(w in m for w in ("supplier", "buy", "price", "cheapest", "where do we")):
        return "supplier_prices", {"item": _item_after(m)}
    return "unknown", {}


def _due_from(msg):
    from datetime import date, timedelta
    ml = msg.lower()
    if "tomorrow" in ml:
        return (date.today() + timedelta(days=1)).isoformat()
    if "today" in ml:
        return date.today().isoformat()
    d = re.search(r"(\d{4}-\d{2}-\d{2})", msg)
    return d.group(1) if d else ""


def _parse_task(msg):
    assignee = ""
    a = re.search(r"\bfor\s+([A-Za-z][\w.]*)", msg)
    if a:
        assignee = a.group(1)
    title = ""
    t = re.search(r"\bto\s+(.+?)(?:\s+tomorrow\b|\s+today\b|\s+by\b|$)", msg, re.I)
    if t:
        title = t.group(1).strip()
    if not title:
        title = re.sub(r"(?i)\b(create|add|make|new|a|task|for\s+\w+)\b", "",
                       msg).strip() or "New task"
    return {"title": title, "assignee": assignee, "due": _due_from(msg)}


def _parse_email(msg):
    r = re.search(r"\bto\s+(.+?)(?:\s+asking|\s+about|\s+regarding|\s+for\b|$)", msg, re.I)
    recipient = r.group(1).strip() if r else ""
    p = re.search(r"(?:asking for|about|regarding|to request|for)\s+(.+?)(?:\.|$)",
                  msg, re.I)
    return {"recipient_name": recipient, "purpose": p.group(1).strip() if p else ""}


def _parse_whatsapp(msg, context):
    ctx = context or {}
    return {"customer_id": ctx.get("id", "") if ctx.get("type") == "customer" else ""}


def _customer_name(msg):
    r = re.search(r"(?:customer|with|for)\s+([A-Z][\w &]+)", msg)
    return r.group(1).strip() if r else ""


# intent → (tool name, human title, suggested action route name)
_INTENTS = {
    "overdue_tasks": ("overdue_tasks", "Overdue tasks", "web:work"),
    "my_tasks": ("my_tasks", "Your open tasks", "web:work"),
    "quotations_awaiting_approval": ("quotations_awaiting_approval",
                                     "Quotations awaiting approval", "web:quotations"),
    "unpaid_invoices": ("unpaid_invoices", "Unpaid invoices", "web:invoices"),
    "uncontacted_customers": ("uncontacted_customers", "Customers to follow up", "web:crm_hub"),
    "supplier_prices": ("supplier_prices", "Supplier prices", "web:suppliers"),
    "job_summary": ("job_summary", "Job summary", None),
}


def ask(company, user, message: str, *, context: dict | None = None) -> dict:
    """Answer a LulaAI question from company data. Returns a structured result the
    UI renders (answer + items + sources + suggested actions)."""
    intent, params = classify(message, context)
    if intent == "unknown":
        return _capabilities(user)
    if intent in _WRITE_INTENTS:
        return _build_draft(intent, params, company, user, context)
    if intent == "customer_summary":
        return _customer_answer(user, params)

    tool_name, title, action_route = _INTENTS[intent]
    try:
        result = run_tool(tool_name, user, **params)
    except ToolPermissionError:
        return {"answer": "I can't provide that with your current access.",
                "intent": intent, "items": [], "sources": [], "actions": [],
                "denied": True, "confidence": "high"}
    except Exception:                                          # noqa: BLE001
        return {"answer": "LulaAI couldn't complete that request right now.",
                "intent": intent, "items": [], "sources": [], "actions": [],
                "error": True, "confidence": "low"}

    return _shape(intent, title, action_route, result, message, company, user)


def _shape(intent, title, action_route, result, message, company, user) -> dict:
    items = result.get("items", []) if isinstance(result, dict) else []
    source = result.get("source", title) if isinstance(result, dict) else title

    if intent == "job_summary":
        if not result.get("found"):
            return {"answer": _STOP, "intent": intent, "items": [], "sources": [],
                    "actions": [], "confidence": "high"}
        answer = _job_answer(result)
        return {"answer": answer, "intent": intent, "items": [], "sources": [source],
                "actions": [], "summary": result, "confidence": "high"}

    if not items:
        return {"answer": f"No {title.lower()} — {_STOP}", "intent": intent,
                "items": [], "sources": [source], "actions": [], "confidence": "high"}

    answer = _deterministic_answer(intent, title, items)
    ai = _maybe_phrase(company, user, title, items)
    actions = _actions(action_route)
    return {"answer": ai or answer, "intent": intent, "items": items,
            "sources": [source], "actions": actions,
            "confidence": "high", "ai_phrased": bool(ai)}


def _deterministic_answer(intent, title, items) -> str:
    n = len(items)
    if intent == "supplier_prices":
        top = items[0]
        return (f"Found {n} recorded price{'s' if n != 1 else ''}. Most recent: "
                f"{top['supplier']} — R{top['price']}"
                + (f"/{top['unit']}" if top.get('unit') else "")
                + (f" on {top['date']}." if top.get('date') else "."))
    if intent == "uncontacted_customers":
        return f"{n} customer{'s' if n != 1 else ''} have no recent recorded activity."
    return f"You have {n} {title.lower()}."


def _job_answer(dash) -> str:
    bits = []
    outstanding = dash.get("outstanding") or []
    if outstanding:
        bits.append(f"{len(outstanding)} item(s) outstanding")
    if dash.get("over_budget"):
        bits.append("over budget")
    fin = dash.get("financials") or {}
    if fin.get("allocated") is not None:
        bits.append(f"R{fin.get('spent', 0)} spent of R{fin.get('allocated', 0)}")
    head = f"{dash.get('source', 'This job')}."
    return head + (" " + "; ".join(bits) + "." if bits else " No outstanding items.")


def _maybe_phrase(company, user, title, items):
    """Optional: have the model phrase ONLY these grounded facts. Best-effort —
    any failure (no provider, no credits) falls back to the deterministic answer."""
    if not user.has_perm_code("ai.generate"):
        return None
    try:
        import json

        from .gateway import run_task
        from .providers import ai_configured
        from .routing import TaskType
        if not ai_configured():
            return None
        system = ("You are LulaAI, an assistant inside the LulaWorks business "
                  "system. Summarise ONLY the facts in the data below in 1-2 short "
                  "sentences. Do not invent anything not present. No preamble.")
        prompt = f"{title}:\n{json.dumps(items[:15], default=str)}"
        resp = run_task(company, user, TaskType.REASONING, prompt,
                        agent="lulaai", prompt_name="lulaai_answer", system=system)
        return (resp.text or "").strip() or None
    except Exception:                                          # noqa: BLE001
        return None


def _actions(route_name):
    if not route_name:
        return []
    try:
        from django.urls import reverse
        return [{"label": "Open", "url": reverse(route_name)}]
    except Exception:                                          # noqa: BLE001
        return []


# ── Write drafts + confirmed execution (§9, §10, §22, §23) ────────────────────

_WRITE_INTENTS = {"create_task", "draft_email", "draft_whatsapp"}

# intent → (action tool, required perm, high-risk?)
_WRITE_META = {
    "create_task": ("create_task", "work.create", False),
    "draft_email": ("send_customer_email", "customers.manage", True),
    "draft_whatsapp": ("send_whatsapp_text", "customers.manage", True),
}


def _denied(intent):
    return {"answer": "I can't do that with your current access.", "intent": intent,
            "items": [], "sources": [], "actions": [], "denied": True,
            "confidence": "high"}


def _build_draft(intent, params, company, user, context) -> dict:
    action, perm, high_risk = _WRITE_META[intent]
    if not user.has_perm_code(perm):
        return _denied(intent)

    if intent == "create_task":
        draft = {"title": params.get("title", "New task"),
                 "assignee": params.get("assignee", ""),
                 "due": params.get("due", ""), "notes": ""}
        fields = [("title", "Title", "text"), ("assignee", "Assign to", "text"),
                  ("due", "Due (YYYY-MM-DD)", "text"), ("notes", "Notes", "textarea")]
        answer = "Here's a task draft — review and confirm."
    elif intent == "draft_email":
        to, cid = _resolve_email(user, params.get("recipient_name", ""))
        subject, body = _draft_email_text(company, user,
                                          params.get("recipient_name", ""),
                                          params.get("purpose", ""))
        draft = {"to": to, "subject": subject, "body": body, "customer_id": cid}
        fields = [("to", "To", "text"), ("subject", "Subject", "text"),
                  ("body", "Message", "textarea")]
        answer = "Here's an email draft — edit anything, then send."
    else:  # draft_whatsapp
        phone = ""
        if params.get("customer_id"):
            phone = _customer_phone(params["customer_id"])
        draft = {"phone": phone, "text": ""}
        fields = [("phone", "To (number)", "text"), ("text", "Message", "textarea")]
        answer = "Here's a WhatsApp draft — add the message, then send."

    return {"kind": "draft", "action": action, "intent": intent, "answer": answer,
            "draft": draft,
            "fields": [{"name": n, "label": l, "type": t, "value": draft.get(n, "")}
                       for (n, l, t) in fields],
            "high_risk": high_risk, "needs_confirm": True, "confidence": "high"}


def execute(company, user, action, params) -> dict:
    """Run a confirmed write action through the permission-checked tool layer."""
    try:
        result = run_tool(action, user, **params)
    except ToolPermissionError:
        return {"kind": "result", "ok": False, "denied": True,
                "answer": "I can't do that with your current access."}
    except ValueError as exc:
        return {"kind": "result", "ok": False, "error": True, "answer": str(exc)}
    except Exception:                                          # noqa: BLE001
        return {"kind": "result", "ok": False, "error": True,
                "answer": "LulaAI couldn't complete that action right now."}
    return _result_answer(action, result)


def _result_answer(action, result) -> dict:
    if action == "create_task":
        msg = f"Task created: “{result['name']}” · {result['assignee']}."
    elif action == "send_customer_email":
        msg = f"Email sent to {result['to']}."
    elif action == "send_whatsapp_text":
        msg = f"WhatsApp sent to {result['phone']}."
    else:
        msg = "Done."
    return {"kind": "result", "ok": True, "answer": msg, "result": result}


def _resolve_email(user, name):
    """Best-effort: a customer name → (email, customer_id) for the draft."""
    if not name:
        return "", ""
    from apps.customers.models import Customer
    c = Customer.objects.filter(name__icontains=name).first()
    if not c:
        return "", ""
    email = (getattr(c, "email", "") or "").strip()
    if not email:
        contact = c.contacts.filter(is_primary=True).first() or c.contacts.first()
        if contact:
            email = (contact.email or "").strip()
    return email, str(c.id)


def _customer_phone(customer_id):
    from apps.customers.models import Customer
    c = Customer.objects.filter(pk=customer_id).first()
    if not c:
        return ""
    return (getattr(c, "mobile", "") or getattr(c, "telephone", "") or "").strip()


def _draft_email_text(company, user, recipient, purpose):
    p = (purpose or "").lower()
    if "po" in p or "purchase order" in p:
        subject = "Purchase order request"
        line = "Please could you send us the purchase order so we can proceed."
    elif purpose:
        subject = purpose[:1].upper() + purpose[1:]
        line = f"I'm following up regarding {purpose}."
    else:
        subject = "Following up"
        line = "I'm following up on our recent correspondence."
    body = (f"Dear {recipient or 'there'},\n\n{line}\n\n"
            f"Kind regards,\n{user.get_full_name() or user.email}\n{company.name}")
    return subject, body


def _customer_answer(user, params):
    try:
        result = run_tool("customer_summary", user, **params)
    except ToolPermissionError:
        return _denied("customer_summary")
    except Exception:                                          # noqa: BLE001
        return {"answer": "LulaAI couldn't complete that request right now.",
                "intent": "customer_summary", "items": [], "sources": [],
                "actions": [], "confidence": "low"}
    if not result.get("found"):
        return {"answer": _STOP, "intent": "customer_summary", "items": [],
                "sources": ["Customers"], "actions": [], "confidence": "high"}
    answer = (f"{result['name']} ({result['status']}) — {result['open_opportunities']} "
              f"open opportunit{'y' if result['open_opportunities'] == 1 else 'ies'}, "
              f"last touched {result['last_touch']}.")
    return {"answer": answer, "intent": "customer_summary", "items": [],
            "sources": [result["source"]], "actions": [], "summary": result,
            "confidence": "high"}


def _capabilities(user) -> dict:
    caps = []
    if user.has_perm_code("projects.view"):
        caps += ["find overdue tasks", "summarise a job", "show your tasks"]
    if user.has_perm_code("procurement.manage"):
        caps += ["find supplier prices"]
    if user.has_perm_code("finance.view_money"):
        caps += ["list unpaid invoices"]
    if user.has_perm_code("customers.manage"):
        caps += ["find customers to follow up"]
    caps = caps or ["see your work"]
    return {"answer": "I can help you with your LulaWorks work — try: "
            + "; ".join(caps) + ".",
            "intent": "unknown", "items": [], "sources": [], "actions": [],
            "confidence": "high"}
