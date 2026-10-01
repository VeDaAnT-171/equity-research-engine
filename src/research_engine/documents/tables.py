"""Financial tables in HTML and PDF documents, reduced to labelled rows of year-keyed values.

A reader finds a figure by its row label and its column heading: "Average interest-earning assets"
under "2025". This module turns a table into exactly that — for every value, the row label, the
full heading above its column (every header row that spans it, joined), the fiscal year that
heading names and the scale the table states ("in millions"). Nothing is interpreted beyond that;
deciding which row is which metric belongs to the extractor, which has the framework.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

_WS = re.compile(r"\s+")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_FOOTNOTE = re.compile(r"(\s*\([a-z]{1,2}\))+$|(\s*\(\d\))+$")
_NUMBER = re.compile(r"^\(?-?\$?\s*[\d,]*\.?\d+\s*%?\)?$")
_SCALES = (("thousand", Decimal(10) ** 3), ("million", Decimal(10) ** 6), ("billion", Decimal(10) ** 9))
_MONTH_DAY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}\b", re.I)


def clean(text: str) -> str:
    return _WS.sub(" ", (text or "").replace("\xa0", " ")).strip()


def normalize_label(text: str) -> str:
    """`Average interest-earning assets(a)` -> `average interest-earning assets`."""
    text = clean(text).replace("’", "'").replace("–", "-").replace("—", "-")
    text = _FOOTNOTE.sub("", text)
    return text.rstrip(":").strip().lower()


def parse_cell_number(text: str) -> Decimal | None:
    raw = clean(text).replace("$", "").replace(" ", "")
    if not raw or not _NUMBER.match(raw):
        return None
    negative = raw.startswith("(") and raw.endswith(")") or raw.startswith("-")
    digits = raw.strip("()%").replace(",", "").lstrip("-")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def stated_scale(text: str) -> Decimal | None:
    lowered = text.lower()
    for word, scale in _SCALES:
        if re.search(rf"\bin {word}s?\b", lowered):
            return scale
    return None


@dataclass(frozen=True)
class Cell:
    value: Decimal
    raw: str
    heading: str          # every header text spanning this column, joined
    year: int | None
    percent: bool


@dataclass
class Row:
    label: str            # as printed, footnote markers removed
    cells: list[Cell]

    @property
    def key(self) -> str:
        return normalize_label(self.label)


@dataclass
class Table:
    index: int            # position in the document, counting from 1
    rows: list[Row]
    scale: Decimal | None
    context: str          # stated units and title text, for the source note
    page: int | None = None
    headings: list[str] = field(default_factory=list)


# ---- HTML -----------------------------------------------------------------------------------------

def _span(td, attr: str) -> int:
    raw = (td.get(attr) or "1").strip()
    return max(1, min(int(raw), 200)) if raw.isdigit() else 1


def _grids(trs) -> list[list[tuple[int, int, str]]]:
    """(first column, last column + 1, text) per cell and row, with row- and colspans resolved.

    A heading cell that spans two rows occupies its columns in the second row too; without that,
    every cell to its right in the second row slides left and lands under the wrong year.
    """
    carried: dict[int, int] = {}   # column -> rows it is still occupied for
    out = []
    for tr in trs:
        row, col = [], 0
        for td in tr.xpath("./td|./th"):
            while carried.get(col, 0) > 0:
                col += 1
            span, down = _span(td, "colspan"), _span(td, "rowspan")
            row.append((col, col + span, clean("".join(td.itertext()))))
            if down > 1:
                for c in range(col, col + span):
                    carried[c] = max(carried.get(c, 0), down)
            col += span
        carried = {c: n - 1 for c, n in carried.items() if n - 1 > 0}
        out.append(row)
    return out


def _is_header(cells: list[tuple[int, int, str]]) -> bool:
    label = cells[0][2] if cells else ""
    values = [t for c0, _, t in cells[1:] if t and t not in ("$", "%")]
    numeric = [t for t in values if parse_cell_number(t) is not None and not _YEAR.fullmatch(t)]
    return not numeric or (not label and all(_YEAR.search(t) for t in values))


def _preceding_text(table, limit: int = 3) -> str:
    texts, node = [], table
    for _ in range(12):
        node = node.getprevious()
        if node is None:
            break
        text = clean("".join(node.itertext()))
        if text:
            texts.append(text[:300])
        if len(texts) >= limit:
            break
    return " ".join(reversed(texts))


def html_tables(content: bytes) -> Iterator[Table]:
    from lxml import html

    root = html.fromstring(content)
    for number, table in enumerate(root.iter("table"), 1):
        grids = _grids(table.xpath(".//tr"))
        grids = [g for g in grids if any(t for _, _, t in g)]
        if len(grids) < 2:
            continue
        header_rows: list[list[tuple[int, int, str]]] = []
        body: list[list[tuple[int, int, str]]] = []
        for g in grids:
            if not body and _is_header(g):
                header_rows.append(g)
            else:
                body.append(g)
        if not body:
            continue

        def heading(c0: int, c1: int, _headers=header_rows) -> str:
            parts = [t for h in _headers for (a, b, t) in h if t and a < c1 and c0 < b and a > 0]
            return " ".join(dict.fromkeys(parts))

        header_text = " ".join(t for h in header_rows for _, _, t in h if t)
        context = f"{_preceding_text(table)} {header_text}".strip()
        scale = stated_scale(header_text) or stated_scale(_preceding_text(table))
        rows = []
        for g in body:
            label = g[0][2] if g else ""
            if not label:
                continue
            cells = []
            for i, (c0, c1, text) in enumerate(g[1:], 1):
                value = parse_cell_number(text)
                if value is None:
                    continue
                percent = "%" in text or (i + 1 < len(g) and g[i + 1][2] == "%")
                head = heading(c0, c1)
                years = _YEAR.findall(head)
                cells.append(Cell(value, text, head, int(years[-1]) if years else None, percent))
            if cells:
                rows.append(Row(_FOOTNOTE.sub("", label), cells))
        if rows:
            yield Table(number, rows, scale, context[:600], None,
                        [" ".join(t for _, _, t in h if t) for h in header_rows])


# ---- PDF ------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"\(?-?\$?\s?[\d,]*\.?\d+%?\)?")


def _split_line(line: str) -> tuple[str, list[str]]:
    """`Average interest-earning assets $ 3,834,359 $ 3,537,567` -> label and value tokens."""
    tokens = line.split()
    values: list[str] = []
    while tokens and (parse_cell_number(tokens[-1]) is not None or tokens[-1] in ("$", "%")):
        tok = tokens.pop()
        if tok not in ("$", "%"):
            values.append(tok)
    return " ".join(tokens), list(reversed(values))


def pdf_tables(content: bytes) -> Iterator[Table]:
    """Line-based reading of a PDF's text: a header line of years, then labelled value lines.

    Presentations and supplements rarely have ruled tables that a table finder can see, but their
    text keeps each row on one line. A block starts at a line naming two or more years and runs
    until a line with a different count of values ends it.
    """
    import io

    import pdfplumber

    tables: list[Table] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            lines = [clean(line) for line in text.splitlines() if clean(line)]
            tables += _page_tables(lines, page_no, stated_scale(text), len(tables))
    yield from tables


def _page_tables(lines: list[str], page_no: int, scale: Decimal | None, numbered: int) -> list[Table]:
    out: list[Table] = []
    years: list[int] = []
    heading = ""
    rows: list[Row] = []

    def close() -> None:
        if rows:
            out.append(Table(numbered + len(out) + 1, list(rows), scale, heading[:600], page_no, [heading]))
            rows.clear()

    for line in lines:
        found = [int(y) for y in _YEAR.findall(line)]
        label, values = _split_line(line)
        numeric = [parse_cell_number(v) for v in values]
        is_heading = len(found) >= 2 and len(set(found)) == len(found) and all(
            n is not None and 1900 <= n <= 2200 for n in numeric)
        if is_heading:
            close()
            years, heading = found, line
            continue
        if years and label and values and len(values) == len(years):
            rows.append(Row(_FOOTNOTE.sub("", label), [
                Cell(n, v, heading, y, v.endswith("%"))
                for v, n, y in zip(values, numeric, years, strict=True) if n is not None]))
    close()
    return out
