"""Study a document: find the framework's metrics in it, and check it against what is already known.

Two readers, in order of reliability:

1. **Inline XBRL.** A filed 10-K or 10-Q tags its figures in place. Tags carry their own scale,
   sign, period and dimensions, so a tagged value is taken exactly as the filer stated it. This
   is how figures that the SEC's companyfacts API leaves out (filer-specific concepts, and values
   qualified by a dimension such as a regulatory approach) are recovered.
2. **Tables.** For figures that are printed but not tagged (an average-balance table, a non-GAAP
   reconciliation, a PDF supplement), a row is matched by its label and a value by the year in
   its column heading. The table's stated scale ("in millions") is required: a currency figure
   from a table that does not state its scale is not used.

Every finding records where it came from — tag and element, or table number, row label and
column heading — and nothing here decides whether a document is trustworthy. That is
`verify()`, which compares a document's figures with the SEC data on every figure both contain.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..calendar import FiscalCalendar
from ..schemas.financial import ExtractionMethod, FinancialFact, FiscalPeriodCode, Period, PeriodType
from ..schemas.framework import IndustryFramework, MetricSpec
from .ixbrl import IxDocument, IxFact, read_inline_xbrl
from .tables import Cell, Table, html_tables, normalize_label, pdf_tables

_SUBPERIOD = re.compile(r"\b(three|six|nine) months\b|\bquarter\b|\bQ[1-4]\b", re.I)
_AVERAGE = re.compile(r"\baverage\b", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MONTH_WORD = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\b", re.I)

AGREEMENT_TOLERANCE = Decimal("0.005")   # relative, for values printed at full precision
MIN_CHECKS = 3                           # fewer overlapping figures than this proves nothing
MIN_AGREEMENT = 0.9


@dataclass(frozen=True)
class Finding:
    metric_id: str
    period: Period
    value: Decimal
    unit: str
    currency: str | None
    method: ExtractionMethod
    where: str                     # "Table 100" / "page 4, table 2" / the tagged concept
    source_text: str               # row label and printed value, or the tag and its context
    concept: str | None = None
    page: int | None = None
    tolerance: Decimal = Decimal(0)  # rounding in the printed figure (half a unit of its scale)
    check_only: bool = False       # matched by name only: used to verify, never to fill a gap


@dataclass
class Study:
    media_type: str
    identity: str = "not_stated"   # match | mismatch | not_stated
    identity_note: str = ""
    form: str | None = None
    period_end: date | None = None
    findings: list[Finding] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---- periods ----------------------------------------------------------------------------------------

def _month_end(year: int, month: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


class PeriodResolver:
    """Fiscal-year periods for values a table labels only by year.

    Dates are borrowed from the company's own structured facts for that fiscal year when there are
    any, so a figure from a document lands on exactly the period the SEC data uses (52/53-week
    years included). Without them the calendar's nominal year end is used.
    """

    def __init__(self, calendar: FiscalCalendar, known: list[FinancialFact]):
        self.calendar = calendar
        self.known: dict[tuple[PeriodType, int], Period] = {}
        for fact in known:
            if fact.period.fiscal_period is FiscalPeriodCode.FY:
                self.known.setdefault((fact.period.period_type, fact.period.fiscal_year), fact.period)

    def fiscal_year(self, period_type: PeriodType, year: int) -> Period:
        found = self.known.get((period_type, year))
        if found:
            return found
        fye = self.calendar.fiscal_year_end_month
        end_year = year if self.calendar.convention == "end_year" or fye == 12 else year + 1
        end = _month_end(end_year, fye)
        start = None if period_type is PeriodType.INSTANT else end.replace(year=end.year - 1) + timedelta(days=1)
        return Period(period_type=period_type, start=start, end=end, fiscal_year=year,
                      fiscal_period=FiscalPeriodCode.FY)


# ---- units ------------------------------------------------------------------------------------------

def _unit_ok(metric: MetricSpec, unit: str | None, currency: str | None) -> bool:
    if unit is None:
        return False
    if metric.unit_kind == "currency":
        return bool(re.fullmatch(r"[A-Z]{3}", unit)) and (currency is None or unit == currency)
    if metric.unit_kind == "currency_per_share":
        return "/" in unit and unit.split("/")[1].lower().startswith("share")
    if metric.unit_kind == "ratio":
        return unit.lower() == "pure"
    return unit.upper() in ("SHARES", "PURE")


def _unit_for(metric: MetricSpec, currency: str | None) -> tuple[str, str | None]:
    if metric.unit_kind == "currency":
        return currency or "USD", currency or "USD"
    if metric.unit_kind == "currency_per_share":
        return f"{currency or 'USD'}/share", currency or "USD"
    if metric.unit_kind == "ratio":
        return "pure", None
    return "shares", None


# ---- inline XBRL ------------------------------------------------------------------------------------

def _dims_allowed(metric: MetricSpec, fact: IxFact) -> bool:
    if not fact.dimensions:
        return True
    allowed = metric.document.dimensions if metric.document else {}
    for axis, member in fact.dimensions:
        rule = next((m for a, m in allowed.items() if re.fullmatch(a, axis, re.I)), None)
        if rule is None or not re.fullmatch(rule, member, re.I):
            return False
    return True


def _concept_match(metric: MetricSpec, fact: IxFact) -> str | None:
    """'standard' for an undimensioned framework concept, 'document' for a hint match, else None."""
    if fact.concept in metric.xbrl_concepts and not fact.dimensions:
        return "standard"
    if metric.document and any(re.fullmatch(p, fact.local_name, re.I) for p in metric.document.concepts):
        return "document" if _dims_allowed(metric, fact) else None
    return None


def _from_inline(ix: IxDocument, framework: IndustryFramework, calendar: FiscalCalendar,
                 currency: str | None, study: Study) -> None:
    grouped: dict[tuple[str, str], list[tuple[IxFact, Period, str]]] = defaultdict(list)
    for fact in ix.facts:
        for metric in framework.metrics:
            how = _concept_match(metric, fact)
            if how is None or not _unit_ok(metric, fact.unit, currency):
                continue
            is_instant = fact.start is None
            if is_instant != (metric.period_type is PeriodType.INSTANT):
                continue
            period = calendar.classify(fact.start, fact.end).period
            if period is None:
                continue
            grouped[(metric.id, period.label)].append((fact, period, how))
    for (metric_id, label), candidates in grouped.items():
        metric = framework.metric(metric_id)
        # A standard undimensioned tag outranks a hint match; among hint matches, the one that
        # names the most of the framework's axes is the most specific statement of the figure.
        candidates.sort(key=lambda c: (c[2] != "standard", -len(c[0].dimensions)))
        best = candidates[0]
        rivals = {c[0].value for c in candidates if c[2] == best[2] and len(c[0].dimensions) == len(best[0].dimensions)}
        if len(rivals) > 1:
            study.ambiguous.append(f"{metric_id} {label}: {len(rivals)} different tagged values")
            continue
        fact, period, _ = best
        unit, cur = _unit_for(metric, currency)
        dims = ", ".join(f"{a}={m}" for a, m in fact.dimensions)
        study.findings.append(Finding(
            metric_id=metric_id, period=period, value=fact.value, unit=unit, currency=cur,
            method=ExtractionMethod.XBRL, where=fact.concept,
            source_text=f"{fact.concept} = {fact.value}" + (f" [{dims}]" if dims else ""),
            concept=fact.concept if re.fullmatch(r"^[A-Za-z][\w-]*:[A-Za-z]\w*$", fact.concept) else None,
        ))


# ---- tables -----------------------------------------------------------------------------------------

def _label_rules(framework: IndustryFramework) -> list[tuple[MetricSpec, re.Pattern, bool]]:
    rules = []
    for metric in framework.metrics:
        for pattern in (metric.document.labels if metric.document else ()):
            rules.append((metric, re.compile(pattern, re.I), False))
        if metric.xbrl_concepts:
            # Matched by the metric's own name, exactly: good enough to compare with a figure the
            # SEC already published, never to supply one.
            rules.append((metric, re.compile(re.escape(normalize_label(metric.name)), re.I), True))
    return rules


def _usable_cells(metric: MetricSpec, cells: list[Cell], fye_month: int) -> dict[int, list[Cell]]:
    by_year: dict[int, list[Cell]] = defaultdict(list)
    wants_average = "average" in metric.name.lower()
    for cell in cells:
        if cell.year is None or _SUBPERIOD.search(cell.heading):
            continue
        month = _MONTH_WORD.search(cell.heading)
        if month and _MONTHS[month.group(1).lower()[:3]] != fye_month:
            continue  # a quarter-end column
        if metric.period_type is PeriodType.INSTANT and _AVERAGE.search(cell.heading):
            continue
        if metric.period_type is PeriodType.DURATION and not wants_average and _AVERAGE.search(cell.heading):
            continue
        by_year[cell.year].append(cell)
    if metric.document and metric.document.column:
        column = re.compile(metric.document.column, re.I)
        for year, found in list(by_year.items()):
            if len(found) > 1:
                narrowed = [c for c in found if column.search(c.heading.replace(" ", " "))]
                by_year[year] = narrowed or found
    return by_year


def _from_tables(tables: list[Table], framework: IndustryFramework, resolver: PeriodResolver,
                 currency: str | None, method: ExtractionMethod, study: Study) -> None:
    rules = _label_rules(framework)
    seen: dict[tuple[str, int, bool], Finding | None] = {}
    for table in tables:
        for row in table.rows:
            key = row.key
            for metric, pattern, check_only in rules:
                if not pattern.fullmatch(key):
                    continue
                if metric.unit_kind in ("currency", "count") and table.scale is None:
                    study.notes.append(f"{row.label} in table {table.index}: the table does not state its units")
                    continue
                for year, cells in _usable_cells(metric, row.cells, resolver.calendar.fiscal_year_end_month).items():
                    values = {c.value for c in cells}
                    if len(values) != 1:
                        study.ambiguous.append(f"{metric.id} FY{year}: {len(values)} values in table {table.index}")
                        continue
                    cell = cells[0]
                    value, tolerance = cell.value, Decimal(0)
                    if metric.unit_kind in ("currency", "count"):
                        value, tolerance = value * table.scale, table.scale / 2
                    elif metric.unit_kind == "ratio":
                        value, tolerance = value / 100, Decimal("0.00005")
                    unit, cur = _unit_for(metric, currency)
                    where = f"page {table.page}, table {table.index}" if table.page else f"table {table.index}"
                    finding = Finding(
                        metric_id=metric.id, period=resolver.fiscal_year(metric.period_type, year),
                        value=value, unit=unit, currency=cur, method=method, where=where.capitalize(),
                        source_text=f"{row.label}: {cell.raw} ({cell.heading})"[:900],
                        page=table.page, tolerance=tolerance, check_only=check_only,
                    )
                    slot = (metric.id, year, check_only)
                    if slot not in seen:
                        seen[slot] = finding
                        continue
                    earlier = seen[slot]
                    if earlier is None:
                        continue
                    if abs(earlier.value - finding.value) > max(earlier.tolerance, finding.tolerance):
                        study.ambiguous.append(f"{metric.id} FY{year}: tables disagree ({earlier.where}, {finding.where})")
                        seen[slot] = None
    study.findings.extend(f for f in seen.values() if f is not None)


# ---- entry point ------------------------------------------------------------------------------------

def sniff(content: bytes) -> str | None:
    head = content[:4096].lstrip().lower()
    if head.startswith(b"%pdf-"):
        return "pdf"
    if b"<html" in head or head.startswith(b"<!doctype html") or (head.startswith(b"<?xml") and b"<html" in content[:20000].lower()):
        return "html"
    return None


def study_document(content: bytes, *, framework: IndustryFramework, calendar: FiscalCalendar,
                   currency: str | None, company_cik: str | None, known: list[FinancialFact]) -> Study:
    media = sniff(content)
    study = Study(media_type=media or "unknown")
    if media is None:
        study.notes.append("not an HTML or PDF document")
        return study
    resolver = PeriodResolver(calendar, known)
    if media == "html":
        ix = read_inline_xbrl(content)
        if ix is not None:
            study.form = ix.dei.get("DocumentType")
            if ix.cik and company_cik:
                study.identity = "match" if ix.cik == company_cik else "mismatch"
                study.identity_note = f"filed by CIK {ix.cik}"
            _from_inline(ix, framework, calendar, currency, study)
        tagged = {(f.metric_id, f.period.label) for f in study.findings}
        table_study = Study(media_type=media)
        _from_tables(list(html_tables(content)), framework, resolver, currency, ExtractionMethod.HTML_TABLE, table_study)
        # A tagged figure is the filer's own statement of it; a table reading of the same figure
        # only adds a second opinion, so it is kept for checking and never supplies the value.
        for f in table_study.findings:
            if (f.metric_id, f.period.label) in tagged:
                continue
            study.findings.append(f)
        study.ambiguous += table_study.ambiguous
        study.notes += table_study.notes
    else:
        _from_tables(list(pdf_tables(content)), framework, resolver, currency, ExtractionMethod.PDF_TABLE, study)
    return study


# ---- verification -----------------------------------------------------------------------------------

@dataclass
class Verdict:
    status: str            # verified | rejected | unverified
    checked: int
    agreed: int
    reason: str
    disagreements: list[str] = field(default_factory=list)


def verify(study: Study, known: list[FinancialFact]) -> Verdict:
    """Is this document about this company, and do its figures agree with the SEC's where both exist?"""
    index = {(f.metric_id, f.period.period_type, f.period.fiscal_year, f.period.fiscal_period): f for f in known}
    checked = agreed = 0
    disagreements: list[str] = []
    for finding in study.findings:
        p = finding.period
        known_fact = index.get((finding.metric_id, p.period_type, p.fiscal_year, p.fiscal_period))
        if known_fact is None:
            continue
        checked += 1
        tolerance = max(abs(known_fact.value) * AGREEMENT_TOLERANCE, finding.tolerance)
        if abs(known_fact.value - finding.value) <= tolerance:
            agreed += 1
        else:
            disagreements.append(f"{finding.metric_id} {p.label}: document {finding.value}, SEC {known_fact.value}")
    if study.identity == "mismatch":
        return Verdict("rejected", checked, agreed, f"It was filed by a different company ({study.identity_note}).",
                       disagreements)
    if checked >= MIN_CHECKS:
        if agreed / checked >= MIN_AGREEMENT:
            return Verdict("verified", checked, agreed, f"Agrees with the SEC figures on {agreed} of {checked} shared figures.")
        return Verdict("rejected", checked, agreed,
                       f"Disagrees with the SEC figures on {checked - agreed} of {checked} shared figures.", disagreements)
    if study.identity == "match":
        return Verdict("verified", checked, agreed, "Filed with the SEC by this company.")
    return Verdict("unverified", checked, agreed,
                   "Too few figures in common with the SEC data to check it, so its numbers are not used.")
