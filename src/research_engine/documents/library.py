"""A company's document library: the reports, supplements and presentations added for it.

The library lives beside the company's config, in `documents/`, so it is versioned with the
repository and travels to every place the company is analysed (the published site, the hosted
app). SEC filings are kept by reference — their EDGAR address and content hash — because the
SEC is the durable copy; anything else is kept as the file itself.

Adding a document never changes a figure by itself. It is studied on the next analysis run and
used only if it passes verification (see `extract.verify`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..errors import ConfigError
from ..schemas.company import SourceRef
from ..schemas.document import DocumentType
from .extract import sniff

LIBRARY_DIR = "documents"
INDEX_FILE = "index.json"
MAX_BYTES = 25 * 1024 * 1024

KINDS: dict[str, DocumentType] = {
    "annual_report": DocumentType.ANNUAL_REPORT,
    "quarterly_report": DocumentType.QUARTERLY_REPORT,
    "earnings_release": DocumentType.EARNINGS_RELEASE,
    "investor_presentation": DocumentType.INVESTOR_PRESENTATION,
    "other": DocumentType.OTHER,
}
KIND_LABEL = {
    "annual_report": "Annual report", "quarterly_report": "Quarterly report",
    "earnings_release": "Earnings release", "investor_presentation": "Investor presentation",
    "other": "Other document",
}

_SEC_ARCHIVE = re.compile(r"^/Archives/edgar/data/\d{1,10}/(\d{18})/[\w.\-]+\.(htm|html|pdf)$", re.I)


def sec_archive_accession(url: str) -> str | None:
    """`.../000162828026008131/x.htm` -> `0001628280-26-008131` for an EDGAR archive URL, else None."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "www.sec.gov":
        return None
    match = _SEC_ARCHIVE.match(parsed.path)
    if not match:
        return None
    raw = match.group(1)
    return f"{raw[:10]}-{raw[10:12]}-{raw[12:]}"


@dataclass
class LibraryEntry:
    id: str
    kind: str
    title: str
    added_by: str                   # owner | visitor
    added_at: str
    media_type: str | None = None   # html | pdf (known once the content has been seen)
    file: str | None = None         # stored file name, for uploaded documents
    url: str | None = None          # EDGAR address, for SEC filings kept by reference
    sha256: str | None = None
    bytes: int | None = None
    original_name: str | None = None
    saved: str | None = None        # the pull request that proposes it for the permanent library

    @property
    def document_type(self) -> DocumentType:
        return KINDS.get(self.kind, DocumentType.OTHER)


class DocumentLibrary:
    def __init__(self, workspace: Path):
        self.root = Path(workspace) / LIBRARY_DIR
        self.index_path = self.root / INDEX_FILE

    # ---- reading ----------------------------------------------------------------------------------

    def entries(self) -> list[LibraryEntry]:
        if not self.index_path.is_file():
            return []
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(f"{self.index_path} is not valid JSON: {exc}") from None
        fields = set(LibraryEntry.__dataclass_fields__)
        return [LibraryEntry(**{k: v for k, v in item.items() if k in fields}) for item in raw.get("documents", [])]

    def get(self, entry_id: str) -> LibraryEntry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)

    def path_of(self, entry: LibraryEntry) -> Path | None:
        if not entry.file:
            return None
        path = (self.root / entry.file).resolve()
        return path if path.parent == self.root.resolve() and path.is_file() else None

    def source_refs(self) -> list[tuple[LibraryEntry, DocumentType, SourceRef]]:
        out = []
        for entry in self.entries():
            if entry.url:
                ref = SourceRef(url=entry.url, label=entry.title)
            else:
                path = self.path_of(entry)
                if path is None:
                    continue
                ref = SourceRef(path=path, label=entry.title)
            out.append((entry, entry.document_type, ref))
        return out

    def mark_saved(self, entry_id: str, where: str) -> None:
        entries = self.entries()
        for entry in entries:
            if entry.id == entry_id:
                entry.saved = where
        self._write_index(entries)

    # ---- adding -----------------------------------------------------------------------------------

    def _write_index(self, entries: list[LibraryEntry]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"documents": [asdict(e) for e in entries]}, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".index-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, self.index_path)

    @staticmethod
    def _title(title: str | None, fallback: str) -> str:
        title = re.sub(r"\s+", " ", (title or "").strip())[:120]
        return title or fallback

    def add_file(self, content: bytes, *, original_name: str, kind: str, title: str | None = None,
                 added_by: str = "owner") -> tuple[LibraryEntry, bool]:
        """Store an uploaded document. Returns the entry and whether it was new."""
        if kind not in KINDS:
            raise ConfigError(f"unknown document kind {kind!r}; expected one of {', '.join(KINDS)}")
        if len(content) > MAX_BYTES:
            raise ConfigError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB")
        media = sniff(content)
        if media is None:
            raise ConfigError("only HTML and PDF documents can be added")
        digest = hashlib.sha256(content).hexdigest()
        entries = self.entries()
        existing = next((e for e in entries if e.sha256 == digest), None)
        if existing:
            return existing, False
        entry_id = digest[:16]
        name = f"{entry_id}.{'pdf' if media == 'pdf' else 'htm'}"
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".upload-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        os.replace(tmp, target)
        clean_name = Path(original_name or "").name[:120] or name
        entry = LibraryEntry(
            id=entry_id, kind=kind, title=self._title(title, Path(clean_name).stem.replace("_", " ")),
            added_by=added_by, added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            media_type=media, file=name, sha256=digest, bytes=len(content), original_name=clean_name,
        )
        self._write_index([*entries, entry])
        return entry, True

    def add_sec_filing(self, url: str, *, kind: str, title: str | None = None,
                       added_by: str = "owner") -> tuple[LibraryEntry, bool]:
        """Keep an EDGAR filing by reference; it is fetched (and its hash pinned) when analysed."""
        if kind not in KINDS:
            raise ConfigError(f"unknown document kind {kind!r}; expected one of {', '.join(KINDS)}")
        accession = sec_archive_accession(url)
        if accession is None:
            raise ConfigError("only documents in the SEC's EDGAR archive can be added by address "
                              "(https://www.sec.gov/Archives/edgar/data/...); upload anything else as a file")
        entries = self.entries()
        existing = next((e for e in entries if e.url == url), None)
        if existing:
            return existing, False
        entry = LibraryEntry(
            id=hashlib.sha256(url.encode()).hexdigest()[:16], kind=kind,
            title=self._title(title, f"SEC filing {accession}"), added_by=added_by,
            added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), url=url,
        )
        self._write_index([*entries, entry])
        return entry, True
