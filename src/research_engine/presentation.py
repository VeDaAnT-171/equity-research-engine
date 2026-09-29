"""Words for people: labels, sector names and plain-language explanations.

The engine records everything in stable machine terms — metric ids, `growth.deposits`,
`input_missing:loans` — because that is what makes it auditable and testable. Readers of the
dashboard should never have to decode those. This module is the single place that turns them into
language, so the API, the charts and the dashboard say the same thing the same way.

Nothing here computes a figure. It only names things and explains refusals the engine already made.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .schemas.framework import IndustryFramework

SECTOR_LABEL = {
    "banks": "Bank",
    "software": "Software",
    "industrials": "Industrial",
    "generic": "General corporate",
}

STATEMENT_LABEL = {
    "income_statement": "Income statement",
    "balance_sheet": "Balance sheet",
    "cash_flow": "Cash flow statement",
    "operating": "Operating metrics",
    "regulatory": "Regulatory capital",
}

CATEGORY_LABEL = {
    "growth": "Growth", "profitability": "Profitability", "returns": "Returns",
    "per_share": "Per share", "efficiency": "Efficiency", "leverage": "Leverage",
    "liquidity": "Liquidity", "capital": "Capital", "credit": "Credit",
    "capital_intensity": "Capital intensity", "operating": "Operating",
}

CHECK_LABEL = {
    "balance_sheet_identity": "Balance sheet balances",
    "cash_flow_statement_sum": "Cash-flow statement adds up",
    "cash_roll_forward": "Cash rolls forward year to year",
    "missing_years": "No missing years",
    "scale_break": "No unit or scale errors",
    "sign": "Values have the expected sign",
    "unusual_change": "No unusual year-on-year moves",
    "restatement": "Restated figures",
    "history_length": "Length of history",
    "concept_switch": "Reporting line renamed",
}

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")

# Metrics no company tags in the SEC's structured data; they live in the annual report text.
_DOCUMENT_ONLY = "isn't disclosed in the SEC's structured financial data"


def labels(framework: IndustryFramework) -> dict[str, str]:
    """Display names for every metric, analytic and assumption key the framework can produce."""
    out: dict[str, str] = {}
    growth_names = {a.metric: a.name for a in framework.analytics if a.kind == "growth" and a.metric}
    for m in framework.metrics:
        out[m.id] = m.name
        base = m.name.removeprefix("Weighted average ").removesuffix(", net")
        out[f"growth.{m.id}"] = growth_names.get(m.id) or f"{base[:1].upper()}{base[1:]} growth"
        out[f"level.{m.id}"] = m.name
    for a in framework.analytics:
        out[a.id] = a.name
        out[f"rate.{a.id}"] = a.name
    return out


def _name(item: str, names: Mapping[str, str]) -> str:
    return names.get(item) or item.replace("_", " ").capitalize()


def in_sentence(name: str) -> str:
    """`Net interest margin` -> `net interest margin` mid-sentence; `CET1 ratio` stays as it is."""
    name = name.removesuffix(", net")
    return name[:1].lower() + name[1:] if name[1:2].islower() else name


def explain(code: str, names: Mapping[str, str], *, document_only: frozenset[str] = frozenset()) -> str:
    """One sentence for a refusal code such as `input_missing:loans` or `growth_sign_change:x`."""
    kind, _, subject = code.partition(":")
    metric = subject.rpartition(".")[2]  # assumption keys carry a namespace: `level.cet1_ratio`
    if metric in document_only:
        return f"{_name(metric, names)} {_DOCUMENT_ONLY}."
    what = in_sentence(_name(subject, names)) if subject else ""
    What = what[:1].upper() + what[1:]
    sentences = {
        "input_missing": f"No figure for {what} is reported in those years.",
        "input_failed_quality": f"The reported figure for {what} failed a data check, so it isn't used.",
        "input_not_computed": f"It depends on {what}, which could not be calculated.",
        "opening_balance_missing": f"No prior-year figure for {what}, so an average can't be taken.",
        "prior_year_missing": f"No prior-year figure for {what}.",
        "start_of_history": "It needs a prior year, and this is the first year on record.",
        # Both growth refusals read the same way: a reader needs to know the rate is meaningless
        # when either end is zero or negative, not which of the two ends it was.
        "growth_base_not_positive": (f"{What} was zero or negative in one of the years compared, so a "
                                     "growth rate would not be meaningful."),
        "growth_sign_change": (f"{What} was zero or negative in one of the years compared, so a "
                               "growth rate would not be meaningful."),
        "denominator_not_positive": "The divisor is zero or negative, so the ratio would not be meaningful.",
        "division_by_zero": "The calculation would divide by zero.",
        "reference_growth_zero": "The comparison figure did not change, so the ratio is undefined.",
        "mixed_currency": "Its inputs are reported in different currencies.",
        "no_currency_input": "Its inputs carry no currency.",
        "base_year_actual_missing": f"No figure is reported for {what} in the latest year, so there is nothing to project from.",
        "prior_year_not_projected": f"The previous year of {what} could not be estimated.",
        "input_not_projected": f"It depends on {what}, which could not be estimated.",
        "assumption_missing": f"No estimate is available for {what}.",
        "assumption_unset": f"The estimate for {what} has been left blank.",
        "formula_not_evaluable": "The calculation could not be evaluated.",
    }
    return sentences.get(kind, code.replace("_", " ").replace(":", ": "))


def document_only_metrics(framework: IndustryFramework) -> frozenset[str]:
    """Metrics the framework declares but that have no XBRL tag: they need the report itself."""
    return frozenset(m.id for m in framework.metrics if not m.xbrl_concepts and not m.derivation)


def split_gap_key(key: str) -> tuple[str, str]:
    """`credit_loss_rate:input_missing:loans` -> (`credit_loss_rate`, `input_missing:loans`)."""
    item, _, code = key.partition(":")
    return item, code


# Reasons that only say "an upstream step failed". When an item has a root cause as well, the
# root cause is what a reader needs; these follow from it.
_KNOCK_ON = ("prior_year_not_projected", "input_not_projected", "input_not_computed", "start_of_history")


def gaps(counter: Mapping[str, int], names: Mapping[str, str],
         document_only: frozenset[str] = frozenset()) -> list[dict]:
    """A reason counter (`item:code:subject` -> count) as one readable row per item, largest first."""
    by_item: dict[str, list[tuple[str, str, int]]] = {}
    for key, count in counter.items():
        item, code = split_gap_key(key)
        by_item.setdefault(item, []).append((key, code, count))
    rows = []
    for item, entries in by_item.items():
        roots = [e for e in entries if not e[1].startswith(_KNOCK_ON)] or entries
        roots.sort(key=lambda e: -e[2])
        reasons = list(dict.fromkeys(explain(code, names, document_only=document_only) for _, code, _ in roots))
        rows.append({"key": roots[0][0], "item": item, "label": _name(item, names),
                     "reason": " ".join(reasons[:2]), "count": sum(e[2] for e in entries)})
    return sorted(rows, key=lambda r: (-r["count"], r["label"]))


def unseeded_text(key: str, names: Mapping[str, str], document_only: frozenset[str] = frozenset()) -> str:
    """Why an assumption could not be measured from history, for a key like `growth.loans`."""
    _, _, metric = key.partition(".")
    what = _name(metric, names)
    if metric in document_only:
        return f"{what} {_DOCUMENT_ONLY}."
    return f"Not enough recent history to measure a trend for {what}."


def year_ranges(years) -> str:
    """[2016, 2017, 2018, 2021] -> `FY2016–FY2018 and FY2021`."""
    spans: list[list[int]] = []
    for y in sorted(years):
        if spans and y == spans[-1][1] + 1:
            spans[-1][1] = y
        else:
            spans.append([y, y])
    parts = [f"FY{a}" if a == b else f"FY{a}–FY{b}" for a, b in spans]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def fiscal_year_end(month: int | None) -> str | None:
    return MONTHS[month - 1] if month and 1 <= month <= 12 else None


def glossary(framework: IndustryFramework) -> dict:
    """Everything the dashboard needs to name and group what a framework produces."""
    return {
        "framework": framework.name,
        "sector": SECTOR_LABEL.get(framework.name, framework.name.replace("_", " ").capitalize()),
        "model_name": framework.display_name,
        "labels": labels(framework),
        "metric_order": [m.id for m in framework.metrics],
        "statements": {m.id: m.statement for m in framework.metrics},
        "units": {m.id: m.unit_kind for m in framework.metrics},
        "analytic_order": [a.id for a in framework.analytics],
        "categories": {a.id: a.category for a in framework.analytics},
        "document_only": sorted(document_only_metrics(framework)),
        "statement_labels": STATEMENT_LABEL,
        "category_labels": CATEGORY_LABEL,
        "check_labels": CHECK_LABEL,
    }


# ---- data checks ------------------------------------------------------------------------------

CHECK_DESCRIPTION = {
    "balance_sheet_identity": "Total assets equal total liabilities plus total equity.",
    "cash_flow_statement_sum": ("Operating, investing and financing cash flows, plus the effect of "
                                "exchange rates, add up to the net change in cash."),
    "cash_roll_forward": "Opening cash plus the net change in cash equals closing cash.",
    "missing_years": "Every year inside a line item's reported range has a figure.",
    "scale_break": "No year-on-year jump that looks like a thousands-versus-millions mix-up.",
    "sign": "Figures that cannot be negative are not negative.",
    "unusual_change": "Year-on-year changes are within the usual range for that line item.",
}

# Why an identity can fail without anything being wrong, in words a reader can act on.
CHECK_EXPLANATION = {
    "balance_sheet_identity": ("Often legitimate: reported equity can exclude non-controlling interests "
                               "and temporary equity, which some companies report on separate lines."),
}

# The two sides of each accounting identity, as a sentence can name them.
IDENTITY_SIDES = {
    "balance_sheet_identity": ("Total assets", "total liabilities plus total equity"),
    "cash_flow_statement_sum": ("Operating, investing and financing cash flows plus the exchange-rate effect",
                                "the net change in cash"),
    "cash_roll_forward": ("Opening cash plus the net change in cash", "closing cash"),
}

SEVERITY_LABEL = {"error": "Excluded", "warning": "Review", "info": "Note"}

_ID = re.compile(r"[a-z][a-z0-9_]*")


def phrase(expression: str, names: Mapping[str, str]) -> str:
    """`pre_tax_income - income_tax_expense` -> `pre-tax income minus income tax expense`."""
    words = _ID.sub(lambda m: in_sentence(names[m.group(0)]) if m.group(0) in names else m.group(0), expression)
    for op, word in ((" + ", " plus "), (" - ", " minus "), (" * ", " times "), (" / ", " divided by ")):
        words = words.replace(op, word)
    return words


def money(value: float, currency: str | None, extra: int = 0) -> str:
    """A reported amount at the scale a reader expects: `$2,414.9bn`, `$810m`, `$4.35`."""
    symbol = "$" if currency in (None, "USD") else f"{currency} "
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e9:
        return f"{sign}{symbol}{v / 1e9:,.{1 + extra}f}bn"
    if v >= 1e6:
        return f"{sign}{symbol}{v / 1e6:,.{extra}f}m"
    return f"{sign}{symbol}{v:,.{2 + extra}f}"


def period_label(period: str | None) -> str:
    """`FY2013` stays; `9M-2014` -> `9M FY2014`; `Q3-2014` -> `Q3 FY2014`."""
    if not period:
        return ""
    head, _, year = period.partition("-")
    return f"{head} FY{year}" if year else period


def filed_on(date: str) -> str:
    """`2026-02-13` -> `13 Feb 2026`."""
    try:
        year, month, day = (int(p) for p in date.split("-"))
        return f"{day} {MONTHS[month - 1][:3]} {year}"
    except (ValueError, IndexError):
        return date


def amount(value: float, unit_kind: str | None, currency: str | None, extra: int = 0) -> str:
    if unit_kind in ("currency", "currency_per_share"):
        return money(value, currency, extra)
    if unit_kind == "ratio":
        return f"{value:.{1 + extra}%}"
    if abs(value) >= 1e6:
        return f"{value / 1e6:,.{1 + extra}f}m"
    return f"{value:,.{2 + extra}f}"


def distinct_amounts(values: list[float], unit_kind: str | None, currency: str | None) -> list[str]:
    """Format values so that different numbers never print the same (a small revision stays visible)."""
    for extra in range(4):
        out = [amount(v, unit_kind, currency, extra) for v in values]
        if len(set(out)) == len(set(values)):
            return out
    return out


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def issue_text(issue: Mapping, names: Mapping[str, str], currency: str | None,
               checks: Mapping[str, Mapping] | None = None, units: Mapping[str, str | None] | None = None) -> str:
    """One plain sentence (or two) for a data-check finding, built from its structured details."""
    check = issue.get("check", "")
    metric = issue.get("metric_id")
    what = _name(metric, names) if metric else ""
    details = issue.get("details") or {}
    message = issue.get("message", "")
    period = period_label(issue.get("period"))

    if check.startswith("derivation:"):
        reported, computed = _number(details.get("reported")), _number(details.get("computed"))
        description = ((checks or {}).get(check) or {}).get("description", "")
        formula = description.partition(" = ")[2].partition(" (")[0]
        if reported is not None and computed is not None:
            return (f"Reported {in_sentence(what)} ({money(reported, currency)}) differs from "
                    f"{phrase(formula, names) or 'its components'} ({money(computed, currency)}) by "
                    f"{money(abs(reported - computed), currency)}. The company may define this line differently.")
    if "left" in details and "right" in details:
        left, right = _number(details.get("left")), _number(details.get("right"))
        lhs, rhs = IDENTITY_SIDES.get(check, ("One side", "the other"))
        if left is not None and right is not None:
            text = (f"{lhs} ({money(left, currency)}) doesn't equal {rhs} ({money(right, currency)}); "
                    f"the difference is {money(abs(left - right), currency)}.")
            extra = CHECK_EXPLANATION.get(check)
            return f"{text} {extra}" if extra else text
    if check == "missing_years":
        missing = details.get("missing") or []
        if missing:
            return f"{what} has no annual figure for {year_ranges(missing)}."
    if check == "unusual_change":
        found = re.search(r"change of ([+-]?[\d.]+%)", message)
        if found:
            return (f"{what} changed by {found.group(1)} in {period}, well outside its usual range. "
                    "Worth checking before relying on it.")
    if check == "scale_break":
        return f"{what} changed by an unusually large factor in {period}, which can indicate a units error."
    if check == "restatement":
        found = re.search(r"was restated: (.*?); current views", message)
        if found:
            unit = (units or {}).get(metric or "")
            parsed = re.findall(r"(-?[\d.]+(?:[eE][-+]?\d+)?) \(([^)]+?) filed ([\d-]+)\)", found.group(1))
            shown = distinct_amounts([float(v) for v, _, _ in parsed], unit, currency)
            versions = [f"{text} in the {form} filed {filed_on(d)}"
                        for text, (_, form, d) in zip(shown, parsed, strict=True)]
            if versions:
                return (f"{what} for {period} was revised in a later filing: {', then '.join(versions)}. "
                        "The latest figure is used.")
    if check == "concept_switch":
        return (f"{what} is reported under different line-item definitions over time, so years may not "
                "be fully comparable.")
    if check == "history_length":
        found = re.search(r"fewer than the configured (\d+) fiscal years: (.*)$", message)
        if found:
            items = ", ".join(in_sentence(_name(m.strip(), names)) for m in found.group(2).split(","))
            return f"Fewer than {found.group(1)} years of history for {items}."
    if check == "sign":
        return f"{what} is negative in {period}, which it cannot be."
    if check == "currency":
        return f"Some {in_sentence(what)} figures are not in the reporting currency."
    return message


def check_label(check: str, names: Mapping[str, str]) -> str:
    if check.startswith("derivation:"):
        return f"{_name(check.partition(':')[2], names)} adds up"
    return CHECK_LABEL.get(check, check.replace("_", " ").capitalize())


def check_description(check: str, raw: str, names: Mapping[str, str]) -> str:
    if check.startswith("derivation:"):
        target, _, formula = raw.partition(" = ")
        formula = formula.partition(" (")[0]
        return (f"Where the company reports {in_sentence(_name(target, names))} itself, it equals "
                f"{phrase(formula, names)}.")
    return CHECK_DESCRIPTION.get(check, raw)


def checks_view(report: Mapping, names: Mapping[str, str], currency: str | None,
                units: Mapping[str, str | None] | None = None) -> dict:
    """The data-quality report as a reader sees it: named checks and one sentence per finding."""
    raw_checks = report.get("checks") or {}
    checks = []
    for cid, c in sorted(raw_checks.items(), key=lambda kv: check_label(kv[0], names)):
        if not (c.get("passed") or c.get("failed") or c.get("not_evaluable")):
            continue  # a check with nothing to look at says nothing a reader can use
        checks.append({"id": cid, "label": check_label(cid, names),
                       "description": check_description(cid, c.get("description", ""), names),
                       "passed": c.get("passed", 0), "failed": c.get("failed", 0),
                       "not_evaluable": c.get("not_evaluable", 0)})
    order = {"error": 0, "warning": 1, "info": 2}
    issues = []
    for i in report.get("issues") or []:
        issues.append({
            "severity": i.get("severity"), "severity_label": SEVERITY_LABEL.get(i.get("severity"), i.get("severity")),
            "check": i.get("check"), "check_label": check_label(i.get("check", ""), names),
            "item": _name(i["metric_id"], names) if i.get("metric_id") else "",
            "period": period_label(i.get("period")), "text": issue_text(i, names, currency, raw_checks, units),
            "fact_ids": list(i.get("fact_ids") or []),
        })
    issues.sort(key=lambda r: (order.get(r["severity"], 9), r["check_label"], r["item"], r["period"]))
    counts = report.get("counts") or {}
    return {"counts": {"excluded": counts.get("error", 0), "review": counts.get("warning", 0),
                       "notes": counts.get("info", 0)},
            "checks": checks, "issues": issues}
