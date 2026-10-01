"""Inline XBRL (iXBRL) reader for filed HTML documents.

A 10-K or 10-Q filed with the SEC since 2019 is an HTML page whose financial figures are tagged
in place. The SEC's companyfacts API republishes only the plain, undimensioned facts under
standard taxonomies. Figures a filer tags with its own extension concepts, and figures qualified
by a dimension (a regulatory approach, a segment), are only in the filing itself. This module
reads them from there, with the tag's own scale and sign, so no number is re-typed or guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

_IX = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"


@dataclass(frozen=True)
class IxFact:
    concept: str               # prefix:LocalName as written in the filing
    value: Decimal
    unit: str | None           # e.g. "USD", "pure", "USD/shares"
    start: date | None
    end: date
    dimensions: tuple[tuple[str, str], ...]   # (axis local name, member local name)
    element_id: str | None
    decimals: str | None

    @property
    def local_name(self) -> str:
        return self.concept.split(":", 1)[-1]

    @property
    def prefix(self) -> str:
        return self.concept.split(":", 1)[0] if ":" in self.concept else ""


@dataclass
class IxDocument:
    facts: list[IxFact] = field(default_factory=list)
    dei: dict[str, str] = field(default_factory=dict)
    skipped: int = 0

    @property
    def cik(self) -> str | None:
        raw = self.dei.get("EntityCentralIndexKey")
        return raw.strip().zfill(10) if raw and raw.strip().isdigit() else None


def _local(name: str | None) -> str:
    return (name or "").split(":", 1)[-1]


def _date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


_DIGITS = re.compile(r"[^0-9.,]")


def parse_number(text: str, fmt: str | None) -> Decimal | None:
    """A displayed number as the iXBRL transformation registry defines it."""
    fmt_local = _local(fmt).lower()
    raw = (text or "").strip()
    if fmt_local in ("fixed-zero", "fixedzero") or raw in ("-", "—", "–"):
        return Decimal(0)
    if "numword" in fmt_local or not raw:
        return None
    cleaned = _DIGITS.sub("", raw)
    if "comma-decimal" in fmt_local or "numcommadecimal" in fmt_local:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_tree(content: bytes):
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError:
        root = None
    return root


def read_inline_xbrl(content: bytes) -> IxDocument | None:
    """Every numeric fact in an inline XBRL document, or None when the document is not iXBRL."""
    root = _parse_tree(content)
    if root is None or root.find(f".//{{{_IX}}}nonFraction") is None:
        return None
    doc = IxDocument()

    contexts: dict[str, tuple[date | None, date | None, tuple[tuple[str, str], ...]]] = {}
    for ctx in root.iter(f"{{{_XBRLI}}}context"):
        period = ctx.find(f"{{{_XBRLI}}}period")
        if period is None:
            continue
        instant = _date(period.findtext(f"{{{_XBRLI}}}instant"))
        start = _date(period.findtext(f"{{{_XBRLI}}}startDate"))
        end = _date(period.findtext(f"{{{_XBRLI}}}endDate")) or instant
        dims = tuple(sorted((_local(m.get("dimension")), _local((m.text or "").strip()))
                            for m in ctx.iter(f"{{{_XBRLDI}}}explicitMember")))
        contexts[ctx.get("id", "")] = (None if instant else start, end, dims)

    units: dict[str, str] = {}
    for unit in root.iter(f"{{{_XBRLI}}}unit"):
        measures = [_local((m.text or "").strip()).upper() for m in unit.iter(f"{{{_XBRLI}}}measure")]
        if unit.find(f"{{{_XBRLI}}}divide") is not None and len(measures) == 2:
            units[unit.get("id", "")] = f"{measures[0]}/{measures[1].lower()}"
        elif measures:
            units[unit.get("id", "")] = "pure" if measures[0] == "PURE" else measures[0]

    for el in root.iter(f"{{{_IX}}}nonNumeric"):
        name = _local(el.get("name"))
        if el.get("name", "").startswith("dei:") and name not in doc.dei:
            doc.dei[name] = "".join(el.itertext()).strip()

    for el in root.iter(f"{{{_IX}}}nonFraction"):
        ctx = contexts.get(el.get("contextRef", ""))
        if ctx is None or ctx[1] is None:
            doc.skipped += 1
            continue
        if el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        value = parse_number("".join(el.itertext()), el.get("format"))
        if value is None:
            doc.skipped += 1
            continue
        scale = int(el.get("scale") or 0)
        value = value.scaleb(scale)
        if el.get("sign") == "-":
            value = -value
        start, end, dims = ctx
        doc.facts.append(IxFact(
            concept=el.get("name", ""), value=value, unit=units.get(el.get("unitRef", "")),
            start=start, end=end, dimensions=dims, element_id=el.get("id"), decimals=el.get("decimals"),
        ))
    return doc
