"""Free calculators — customer-acquisition tools for the Education Engine.

Each tool is a small, genuinely useful calculator that answers a real contractor
question, then connects to the Lulaworks feature that does it automatically for
every job. Compute is server-side (one source of truth, fully testable); the
pages work without JavaScript and are SEO-friendly.

A tool is defined declaratively (inputs + copy) and computed by `compute(slug,
values)`. Add a tool by adding a ToolSpec and a branch in `compute`.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

TWO = Decimal("0.01")


@dataclass
class Field:
    name: str
    label: str
    kind: str = "money"            # money | percent | number | choice
    default: str = ""
    help: str = ""
    choices: tuple = ()            # for kind == "choice": ((value, label), ...)


@dataclass
class ToolSpec:
    slug: str
    title: str
    summary: str
    category: str                  # ResourceCategory slug (for grouping)
    related_feature: str           # Lulaworks feature this connects to
    cta_label: str
    cta_url: str
    icon: str
    problem: str                   # HTML — the business problem
    explainer: str                 # HTML — teach the concept
    inputs: list = field(default_factory=list)


def _dec(v, default="0"):
    try:
        return Decimal(str(v).replace(",", "").strip() or default)
    except (InvalidOperation, TypeError, AttributeError):
        return Decimal(default)


def _money(v):
    return "R" + f"{v.quantize(TWO):,.2f}"


def _pct(v):
    return f"{v.quantize(Decimal('0.1'))}%"


TOOLS = {
    "job-profit-calculator": ToolSpec(
        slug="job-profit-calculator",
        title="Job Profit Calculator",
        summary="See the real gross profit, margin and markup on a job before you commit.",
        category="profitability", related_feature="jobs", icon="📊",
        cta_label="Track this automatically for every job",
        cta_url="/start-free-trial/",
        problem=(
            "<p>A job can look profitable and still lose money. Contractors quote on "
            "a headline price, then materials, labour, transport and subcontractors "
            "eat the margin quietly. This tool shows the real profit on a job before "
            "you commit.</p>"),
        explainer=(
            "<p><strong>Gross profit</strong> is the contract value less the direct "
            "cost of doing the work. <strong>Margin</strong> is that profit as a share "
            "of the price; <strong>markup</strong> is it as a share of the cost. Aim "
            "for a margin that also covers your overheads — not just break-even on the "
            "job.</p>"),
        inputs=[
            Field("contract", "Contract value", "money", "0"),
            Field("materials", "Materials", "money", "0"),
            Field("labour", "Labour", "money", "0"),
            Field("transport", "Transport", "money", "0"),
            Field("subcontractors", "Subcontractors", "money", "0"),
            Field("other", "Other costs", "money", "0"),
        ]),
    "markup-margin-calculator": ToolSpec(
        slug="markup-margin-calculator",
        title="Markup & Margin Calculator",
        summary="A 30% markup is not a 30% margin. Enter cost and price to see both.",
        category="profitability", related_feature="quotations", icon="🧮",
        cta_label="See margin on every quote line in Lulaworks",
        cta_url="/start-free-trial/",
        problem=(
            "<p>Pricing on markup but thinking in margin is one of the most common "
            "ways contractors under-price. They are not the same number.</p>"),
        explainer=(
            "<p><strong>Markup</strong> = profit ÷ cost. <strong>Margin</strong> = "
            "profit ÷ selling price. A R100 cost sold at R130 is a 30% markup but only "
            "a 23% margin. To hit a target margin, divide cost by (1 − margin).</p>"),
        inputs=[
            Field("cost", "Cost", "money", "0"),
            Field("sell", "Selling price", "money", "0"),
        ]),
    "vat-calculator": ToolSpec(
        slug="vat-calculator",
        title="VAT Calculator",
        summary="Add or extract South African VAT (15%) from any amount.",
        category="getting-paid", related_feature="invoices", icon="🧾",
        cta_label="Invoices calculate VAT for you",
        cta_url="/start-free-trial/",
        problem=(
            "<p>Getting VAT wrong on a quote or invoice means you either short-change "
            "yourself or over-charge the customer. This works both ways — adding VAT "
            "to a net amount, or extracting it from a VAT-inclusive total.</p>"),
        explainer=(
            "<p>On a <strong>VAT-exclusive</strong> amount, VAT = amount × rate. On a "
            "<strong>VAT-inclusive</strong> total, the net = total ÷ (1 + rate), and "
            "VAT is the difference. South Africa's standard rate is 15%.</p>"),
        inputs=[
            Field("amount", "Amount", "money", "0"),
            Field("rate", "VAT rate", "percent", "15"),
            Field("mode", "This amount is", "choice", "exclusive",
                  choices=(("exclusive", "VAT-exclusive (add VAT)"),
                           ("inclusive", "VAT-inclusive (extract VAT)"))),
        ]),
    "break-even-calculator": ToolSpec(
        slug="break-even-calculator",
        title="Break-even Calculator",
        summary="How many units or jobs you must sell to cover your fixed costs.",
        category="profitability", related_feature="reporting", icon="⚖️",
        cta_label="See profitability live with Lulaworks",
        cta_url="/start-free-trial/",
        problem=(
            "<p>If you don't know your break-even point, you don't know whether you're "
            "actually making money this month. It's the sales level where you cover "
            "your fixed costs — everything after it is profit.</p>"),
        explainer=(
            "<p>The <strong>contribution</strong> per unit = price − variable cost. "
            "Break-even units = fixed costs ÷ contribution. If the contribution is "
            "zero or negative, you can never break even at that price.</p>"),
        inputs=[
            Field("fixed", "Fixed costs (per month)", "money", "0"),
            Field("price", "Price per unit / job", "money", "0"),
            Field("variable", "Variable cost per unit / job", "money", "0"),
        ]),
}


def compute(slug, values):
    """Return a list of {label, value, emph} result rows for the given tool, or
    an {error} note when the inputs can't produce a sensible answer."""
    if slug == "job-profit-calculator":
        contract = _dec(values.get("contract"))
        cost = sum(_dec(values.get(k)) for k in
                   ("materials", "labour", "transport", "subcontractors", "other"))
        gross = contract - cost
        margin = (gross / contract * 100) if contract > 0 else Decimal("0")
        markup = (gross / cost * 100) if cost > 0 else Decimal("0")
        return [
            {"label": "Total cost", "value": _money(cost)},
            {"label": "Gross profit", "value": _money(gross), "emph": True},
            {"label": "Profit margin", "value": _pct(margin)},
            {"label": "Markup", "value": _pct(markup)},
        ]

    if slug == "markup-margin-calculator":
        cost = _dec(values.get("cost"))
        sell = _dec(values.get("sell"))
        profit = sell - cost
        markup = (profit / cost * 100) if cost > 0 else Decimal("0")
        margin = (profit / sell * 100) if sell > 0 else Decimal("0")
        return [
            {"label": "Profit", "value": _money(profit), "emph": True},
            {"label": "Markup", "value": _pct(markup)},
            {"label": "Margin", "value": _pct(margin)},
        ]

    if slug == "vat-calculator":
        amount = _dec(values.get("amount"))
        rate = _dec(values.get("rate"), "15")
        if values.get("mode") == "inclusive":
            net = amount / (1 + rate / 100) if rate >= 0 else amount
            vat = amount - net
            total = amount
        else:
            net = amount
            vat = amount * rate / 100
            total = amount + vat
        return [
            {"label": "Net (excl. VAT)", "value": _money(net)},
            {"label": f"VAT ({_pct(rate)})", "value": _money(vat)},
            {"label": "Total (incl. VAT)", "value": _money(total), "emph": True},
        ]

    if slug == "break-even-calculator":
        fixed = _dec(values.get("fixed"))
        price = _dec(values.get("price"))
        variable = _dec(values.get("variable"))
        contribution = price - variable
        if contribution <= 0:
            return [{"error": "The price must be higher than the variable cost — "
                     "otherwise you can never break even."}]
        import math
        units = math.ceil(fixed / contribution)
        revenue = Decimal(units) * price
        return [
            {"label": "Break-even units / jobs", "value": f"{units:,}", "emph": True},
            {"label": "Break-even revenue", "value": _money(revenue)},
            {"label": "Contribution per unit", "value": _money(contribution)},
        ]

    return []
