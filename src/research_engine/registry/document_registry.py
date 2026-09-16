"""SQLite-backed document registry with an immutable, content-addressed raw store."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional
from urllib.parse import urlparse

from pydantic import ValidationError

from ..errors import RegistryError, format_validation_error
from ..schemas.company import SourceRef
from ..schemas.document import ALLOWED_TRANSITIONS, DocumentRecord, DocumentStatus, DocumentType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source_url TEXT,
    local_source_path TEXT,
    raw_path TEXT,
    publication_date TEXT,
    fiscal_period TEXT,
    retrieval_timestamp TEXT,
    file_hash TEXT,
    content_type TEXT,
    size_bytes INTEGER,
    status TEXT NOT NULL,
    parser_version TEXT,
    error TEXT,
    registered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_company ON documents(company_id);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    at TEXT NOT NULL,
    note TEXT
);
"""

_COLUMNS = [
    "document_id", "company_id", "document_type", "source_url", "local_source_path", "raw_path",
    "publication_date", "fiscal_period", "retrieval_timestamp", "file_hash", "content_type", "size_bytes",
    "status", "parser_version", "error", "registered_at", "updated_at",
]

_EXT_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/json": ".json",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
_KNOWN_EXT = set(_EXT_BY_CONTENT_TYPE.values()) | {".htm", ".xls", ".xbrl", ".zip"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_document_id(company_id: str, document_type: DocumentType, source_key: str) -> str:
    """Deterministic: registering the same source twice is idempotent (enables caching)."""
    raw = f"{company_id}|{document_type.value}|{source_key}"
    return "doc_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


class DocumentRegistry:
    def __init__(self, db_path: str | Path, raw_root: str | Path):
        self.db_path = Path(db_path)
        self.raw_root = Path(raw_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- reads -------------------------------------------------------------------------
    def _row_to_record(self, row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord.model_validate(dict(row))

    def find(self, document_id: str) -> Optional[DocumentRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, document_id: str) -> DocumentRecord:
        record = self.find(document_id)
        if record is None:
            raise RegistryError(f"unknown document {document_id!r}")
        return record

    def list_documents(
        self,
        *,
        company_id: Optional[str] = None,
        status: Optional[DocumentStatus] = None,
        document_type: Optional[DocumentType] = None,
    ) -> list[DocumentRecord]:
        clauses, params = [], []
        for column, value in (("company_id", company_id), ("status", status), ("document_type", document_type)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value.value if hasattr(value, "value") else value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM documents {where} ORDER BY registered_at, document_id", params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def find_by_hash(self, file_hash: str) -> list[DocumentRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM documents WHERE file_hash = ? ORDER BY document_id", (file_hash,)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def history(self, document_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT from_status, to_status, at, note FROM status_history WHERE document_id = ? ORDER BY id",
                (document_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- writes ------------------------------------------------------------------------
    def _save(self, record: DocumentRecord, from_status: Optional[DocumentStatus], note: Optional[str]) -> None:
        payload = record.model_dump(mode="json")
        placeholders = ", ".join(f":{c}" for c in _COLUMNS)
        with self._connect() as conn:
            conn.execute(f"INSERT OR REPLACE INTO documents ({', '.join(_COLUMNS)}) VALUES ({placeholders})", payload)
            conn.execute(
                "INSERT INTO status_history (document_id, from_status, to_status, at, note) VALUES (?, ?, ?, ?, ?)",
                (record.document_id, from_status.value if from_status else None, record.status.value,
                 record.updated_at.isoformat(), note),
            )

    @staticmethod
    def _rebuild(record: DocumentRecord, **updates) -> DocumentRecord:
        try:
            return DocumentRecord.model_validate({**record.model_dump(), **updates})
        except ValidationError as exc:
            raise RegistryError(f"{record.document_id}: invalid document state\n{format_validation_error(exc)}") from None

    def register(self, company_id: str, document_type: DocumentType, source: SourceRef) -> DocumentRecord:
        source_key = source.url if source.url is not None else str(Path(source.path).expanduser().resolve())
        document_id = make_document_id(company_id, document_type, source_key)
        existing = self.find(document_id)
        if existing is not None:
            return existing
        now = _utcnow()
        try:
            record = DocumentRecord(
                document_id=document_id,
                company_id=company_id,
                document_type=document_type,
                source_url=source.url,
                local_source_path=None if source.url is not None else source_key,
                publication_date=source.publication_date,
                fiscal_period=source.fiscal_period,
                status=DocumentStatus.REGISTERED,
                registered_at=now,
                updated_at=now,
            )
        except ValidationError as exc:
            raise RegistryError(f"cannot register {source_key}\n{format_validation_error(exc)}") from None
        self._save(record, None, source.label)
        return record

    def _extension(self, record: DocumentRecord, content_type: Optional[str]) -> str:
        if content_type:
            ext = _EXT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
            if ext:
                return ext
        location = urlparse(record.source_url).path if record.source_url else record.local_source_path
        suffix = PurePosixPath(location or "").suffix.lower()
        return suffix if suffix in _KNOWN_EXT else ".bin"

    def store_raw(
        self,
        document_id: str,
        content: bytes,
        *,
        content_type: Optional[str] = None,
        retrieved_at: Optional[datetime] = None,
    ) -> DocumentRecord:
        record = self.get(document_id)
        if not content:
            raise RegistryError(f"{document_id}: refusing to store empty content")
        digest = hashlib.sha256(content).hexdigest()
        if record.file_hash is not None:
            if record.file_hash == digest:
                return record  # unchanged document: cache hit, nothing to do
            raise RegistryError(
                f"{document_id}: source content changed (stored {record.file_hash[:12]}, new {digest[:12]}). "
                "Raw documents are never overwritten; register the new version as a separate document."
            )
        if record.status is not DocumentStatus.REGISTERED:
            raise RegistryError(f"{document_id}: cannot store content in status '{record.status.value}'")

        target = self.raw_root / record.company_id / digest[:2] / f"{digest}{self._extension(record, content_type)}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RegistryError(f"raw store corruption: {target} does not match its content hash")
        else:
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, target)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
            target.chmod(0o444)

        updated = self._rebuild(
            record,
            status=DocumentStatus.RETRIEVED,
            file_hash=digest,
            raw_path=str(target),
            size_bytes=len(content),
            content_type=content_type,
            retrieval_timestamp=retrieved_at or _utcnow(),
            updated_at=_utcnow(),
            error=None,
        )
        self._save(updated, record.status, None)
        return updated

    def mark(
        self,
        document_id: str,
        status: DocumentStatus,
        *,
        parser_version: Optional[str] = None,
        error: Optional[str] = None,
        note: Optional[str] = None,
    ) -> DocumentRecord:
        record = self.get(document_id)
        allowed = ALLOWED_TRANSITIONS[record.status]
        if status not in allowed:
            raise RegistryError(
                f"{document_id}: invalid status transition {record.status.value} -> {status.value}; "
                f"allowed: {sorted(s.value for s in allowed)}"
            )
        updates: dict = {"status": status, "updated_at": _utcnow()}
        if parser_version is not None:
            updates["parser_version"] = parser_version
        updates["error"] = error if status is DocumentStatus.FAILED else None
        updated = self._rebuild(record, **updates)
        self._save(updated, record.status, note or error)
        return updated
