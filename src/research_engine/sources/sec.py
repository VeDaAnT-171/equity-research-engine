"""SEC EDGAR adapters: submissions (entity metadata) and companyfacts (all XBRL facts for an entity)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..calendar import effective_month
from ..errors import ExtractionError
from .base import StructuredSource

SEC_HOST = "data.sec.gov"
_COMPANYFACTS = re.compile(r"^https://data\.sec\.gov/api/xbrl/companyfacts/CIK(\d{10})\.json$")
_SUBMISSIONS = re.compile(r"^https://data\.sec\.gov/submissions/CIK(\d{10})\.json$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def _load_json(content: bytes, what: str) -> dict:
    try:
        data = json.loads(content, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"{what}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ExtractionError(f"{what}: expected a JSON object")
    return data


def _cik(value, what: str) -> str:
    s = str(value).strip()
    if not s.isdigit():
        raise ExtractionError(f"{what}: invalid CIK {value!r}")
    return s.zfill(10)


@dataclass(frozen=True)
class EntityProfile:
    cik: str
    name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    sic: Optional[int]
    sic_description: Optional[str]
    fiscal_year_end_month: Optional[int]
    source_document_id: str


@dataclass(frozen=True)
class XbrlObservation:
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    start: Optional[date]
    end: date
    accession: str
    form: str
    filed: date
    frame: Optional[str]

    @property
    def qname(self) -> str:
        return f"{self.taxonomy}:{self.concept}"


@dataclass(frozen=True)
class CompanyFacts:
    cik: str
    entity_name: str
    observations: tuple[XbrlObservation, ...]
    malformed: int  # observations dropped because required fields were missing or invalid


class SecSubmissions(StructuredSource):
    name = "sec_submissions"
    description = "SEC EDGAR submissions API (entity metadata, SIC, fiscal year end)"

    def can_handle(self, url: str) -> bool:
        return bool(_SUBMISSIONS.match(url))

    def identifier_from_url(self, url: str) -> Optional[str]:
        m = _SUBMISSIONS.match(url)
        return m.group(1) if m else None

    def normalize(self, content: bytes, document_id: str) -> EntityProfile:
        data = _load_json(content, "SEC submissions")
        for key in ("cik", "name"):
            if key not in data:
                raise ExtractionError(f"SEC submissions: missing field {key!r}")
        sic_raw = str(data.get("sic") or "").strip()
        fye_raw = str(data.get("fiscalYearEnd") or "").strip()
        fye_month = None
        if fye_raw:
            if not re.fullmatch(r"\d{4}", fye_raw):
                raise ExtractionError(f"SEC submissions: invalid fiscalYearEnd {fye_raw!r}")
            try:
                # MMDD; a 52/53-week year ending e.g. 0103 belongs to December
                _, fye_month = effective_month(date(2001, int(fye_raw[:2]), int(fye_raw[2:])))
            except ValueError:
                raise ExtractionError(f"SEC submissions: invalid fiscalYearEnd {fye_raw!r}") from None
        return EntityProfile(
            cik=_cik(data["cik"], "SEC submissions"),
            name=str(data["name"]),
            tickers=tuple(str(t).upper() for t in data.get("tickers") or ()),
            exchanges=tuple(str(e) for e in data.get("exchanges") or () if e),
            sic=int(sic_raw) if sic_raw.isdigit() else None,
            sic_description=data.get("sicDescription") or None,
            fiscal_year_end_month=fye_month,
            source_document_id=document_id,
        )


def normalize_unit(unit: str) -> str:
    """SEC uses both 'USD/shares' and 'USD-per-shares' for ratio units."""
    return unit.replace("-per-", "/")


class SecCompanyFacts(StructuredSource):
    name = "sec_companyfacts"
    description = "SEC EDGAR XBRL companyfacts API (every tagged fact across an entity's filings)"

    def can_handle(self, url: str) -> bool:
        return bool(_COMPANYFACTS.match(url))

    def identifier_from_url(self, url: str) -> Optional[str]:
        m = _COMPANYFACTS.match(url)
        return m.group(1) if m else None

    def normalize(self, content: bytes, document_id: str) -> CompanyFacts:
        data = _load_json(content, "SEC companyfacts")
        if "cik" not in data or not isinstance(data.get("facts"), dict):
            raise ExtractionError("SEC companyfacts: missing 'cik' or 'facts'")
        observations: list[XbrlObservation] = []
        malformed = 0
        for taxonomy, concepts in data["facts"].items():
            if not isinstance(concepts, dict):
                raise ExtractionError(f"SEC companyfacts: taxonomy {taxonomy!r} is not an object")
            for concept, body in concepts.items():
                units = body.get("units") if isinstance(body, dict) else None
                if not isinstance(units, dict):
                    malformed += 1
                    continue
                for unit, rows in units.items():
                    for row in rows if isinstance(rows, list) else ():
                        obs = self._observation(taxonomy, concept, normalize_unit(unit), row)
                        if obs is None:
                            malformed += 1
                        else:
                            observations.append(obs)
        return CompanyFacts(
            cik=_cik(data["cik"], "SEC companyfacts"),
            entity_name=str(data.get("entityName", "")),
            observations=tuple(observations),
            malformed=malformed,
        )

    @staticmethod
    def _observation(taxonomy: str, concept: str, unit: str, row) -> Optional[XbrlObservation]:
        if not isinstance(row, dict):
            return None
        try:
            value = row["val"] if isinstance(row["val"], Decimal) else Decimal(str(row["val"]))
            end = date.fromisoformat(row["end"])
            start = date.fromisoformat(row["start"]) if row.get("start") else None
            filed = date.fromisoformat(row["filed"])
            accession = str(row["accn"])
            form = str(row["form"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None
        if not value.is_finite() or not _ACCESSION.match(accession):
            return None
        return XbrlObservation(taxonomy, concept, unit, value, start, end, accession, form, filed, row.get("frame"))


SEC_SOURCES: tuple[StructuredSource, ...] = (SecSubmissions(), SecCompanyFacts())
