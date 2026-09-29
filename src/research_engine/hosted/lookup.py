"""Find a company by name or ticker in the SEC's own company index, and write its config.

The index is the SEC's `company_tickers_exchange.json`: every company with a ticker that files with
the SEC, with its CIK and exchange. Resolving through it — rather than accepting a ticker and
exchange from a visitor — means a config is only ever built for a company the SEC says exists, and
the CIK the pipeline fetches is the SEC's, not whatever was typed.

One company is one CIK. A CIK often has several tickers (common stock plus preferred series); the
index lists the primary one first, and every match on a secondary ticker resolves to that primary,
so searching two tickers of the same company never creates two workspaces for it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from ..config import load_project_config
from ..errors import ResearchEngineError
from ..schemas.common import slugify

INDEX_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
INDEX_MAX_AGE_SECONDS = 24 * 3600
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


class Fetcher(Protocol):
    def fetch(self, url: str): ...


class IndexUnavailable(ResearchEngineError):
    """The SEC company index could not be loaded and there is no earlier copy to fall back on."""


@dataclass(frozen=True)
class IndexEntry:
    cik: str          # zero-padded to 10 digits, as the SEC's own URLs use it
    name: str
    ticker: str       # the primary ticker for this CIK
    exchange: str | None

    @property
    def company_id(self) -> str:
        return slugify(self.exchange or "sec", self.ticker)

    def public(self) -> dict:
        return {"cik": self.cik, "name": self.name, "ticker": self.ticker,
                "exchange": self.exchange, "company_id": self.company_id}


def parse_index(raw: bytes) -> tuple[list[IndexEntry], dict[str, IndexEntry]]:
    """Primary entries (one per CIK, in SEC order) and a map from every ticker to its CIK's primary."""
    doc = json.loads(raw)
    fields = doc.get("fields") or []
    try:
        at = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
    except ValueError:
        raise IndexUnavailable(f"SEC company index has unexpected fields {fields!r}") from None
    primaries: dict[str, IndexEntry] = {}
    by_ticker: dict[str, IndexEntry] = {}
    for row in doc.get("data") or []:
        cik, name, ticker = row[at["cik"]], row[at["name"]], row[at["ticker"]]
        if cik is None or not name or not ticker:
            continue
        cik = str(int(cik)).zfill(10)
        ticker = str(ticker).strip().upper()
        entry = primaries.get(cik)
        if entry is None:
            entry = primaries[cik] = IndexEntry(cik, str(name).strip(), ticker, row[at["exchange"]] or None)
        by_ticker.setdefault(ticker, entry)
    return list(primaries.values()), by_ticker


class CompanyIndex:
    """The SEC index, cached on disk and refreshed daily; a failed refresh keeps the last good copy."""

    def __init__(self, cache_path: Path, fetcher: Fetcher, *, max_age: float = INDEX_MAX_AGE_SECONDS):
        self.cache_path = Path(cache_path)
        self.fetcher = fetcher
        self.max_age = max_age
        self._entries: list[IndexEntry] = []
        self._by_ticker: dict[str, IndexEntry] = {}
        self._by_cik: dict[str, IndexEntry] = {}
        self._loaded_mtime: float | None = None

    def _load(self) -> None:
        stale = (not self.cache_path.is_file()
                 or time.time() - self.cache_path.stat().st_mtime > self.max_age)
        if stale:
            try:
                raw = self.fetcher.fetch(INDEX_URL).content
                parse_index(raw)  # never replace a good cache with something unparseable
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.cache_path.with_suffix(".incoming")
                tmp.write_bytes(raw)
                os.replace(tmp, self.cache_path)
            except ResearchEngineError as exc:
                if not self.cache_path.is_file():
                    raise IndexUnavailable(f"cannot load the SEC company index: {exc}") from None
        mtime = self.cache_path.stat().st_mtime
        if mtime != self._loaded_mtime:
            self._entries, self._by_ticker = parse_index(self.cache_path.read_bytes())
            self._by_cik = {e.cik: e for e in self._entries}
            self._loaded_mtime = mtime

    def search(self, query: str, limit: int = 8) -> list[IndexEntry]:
        """Exact ticker first, then names that start with the query, then names that contain it."""
        q = " ".join(query.split())
        if len(q) < 1:
            return []
        self._load()
        results: list[IndexEntry] = []

        def add(entry: IndexEntry) -> None:
            if entry not in results:
                results.append(entry)

        exact = self._by_ticker.get(q.upper())
        if exact:
            add(exact)
        low = q.lower()
        for entry in self._entries:
            if len(results) >= limit:
                break
            if entry.name.lower().startswith(low) or entry.ticker.lower().startswith(low):
                add(entry)
        for entry in self._entries:
            if len(results) >= limit:
                break
            if low in entry.name.lower():
                add(entry)
        return results[:limit]

    def get(self, cik: str) -> IndexEntry | None:
        self._load()
        try:
            return self._by_cik.get(str(int(cik)).zfill(10))
        except ValueError:
            return None


def write_config(entry: IndexEntry, companies_root: Path) -> Path:
    """Write `companies/<id>/config.yaml` for an index entry, validated before it is kept."""
    company_dir = Path(companies_root) / entry.company_id
    path = company_dir / "config.yaml"
    if path.is_file():
        return path
    config = {
        "schema_version": "1",
        "company": {
            "name": entry.name,
            "ticker": entry.ticker,
            "exchange": entry.exchange or "SEC",
            # The index does not carry a country and the config requires one. Saying so beats
            # guessing "United States" for a foreign private issuer.
            "country": "Not stated (SEC filer)",
            "identifiers": {"cik": entry.cik},
            "company_id": entry.company_id,
        },
        "sources": {
            "structured_filings": [
                {"url": COMPANYFACTS_URL.format(cik=entry.cik), "label": "SEC XBRL company facts"},
                {"url": SUBMISSIONS_URL.format(cik=entry.cik), "label": "SEC submissions index"},
            ],
        },
    }
    company_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".incoming")
    tmp.write_text("# Generated by the hosted app from the SEC company index.\n"
                   + yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    load_project_config(tmp)  # an invalid config never becomes visible to the dashboard
    os.replace(tmp, path)
    return path
