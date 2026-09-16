import sqlite3

import pytest

from research_engine.errors import RegistryError
from research_engine.registry import DocumentRegistry
from research_engine.schemas import DocumentStatus, DocumentType, SourceRef

URL = "https://example.com/data.json"


@pytest.fixture
def registry(tmp_path):
    return DocumentRegistry(tmp_path / "r.sqlite", tmp_path / "raw")


def test_changed_content_becomes_new_version(registry):
    v1 = registry.register("c", DocumentType.STRUCTURED_FILING, SourceRef(url=URL))
    registry.store_raw(v1.document_id, b"v1")
    v2 = registry.store_raw(v1.document_id, b"v2", on_change="new_version")
    assert v2.document_id != v1.document_id and v2.supersedes == v1.document_id
    assert registry.get(v1.document_id).status is DocumentStatus.SUPERSEDED
    assert registry.current_version(v1.document_id).document_id == v2.document_id
    assert [d.document_id for d in registry.list_documents()] == [v2.document_id]
    assert len(registry.list_documents(include_superseded=True)) == 2
    # both raw versions still exist
    assert open(registry.get(v1.document_id).raw_path, "rb").read() == b"v1"


def test_content_flip_flop_keeps_a_linear_chain(registry):
    v1 = registry.register("c", DocumentType.STRUCTURED_FILING, SourceRef(url=URL))
    registry.store_raw(v1.document_id, b"A")
    registry.store_raw(v1.document_id, b"B", on_change="new_version")
    registry.store_raw(v1.document_id, b"A", on_change="new_version")
    latest = registry.store_raw(v1.document_id, b"B", on_change="new_version")
    assert registry.current_version(v1.document_id).document_id == latest.document_id
    assert len(registry.list_documents(include_superseded=True)) == 4


def test_unchanged_content_on_current_version_is_noop(registry):
    v1 = registry.register("c", DocumentType.STRUCTURED_FILING, SourceRef(url=URL))
    registry.store_raw(v1.document_id, b"v1")
    v2 = registry.store_raw(v1.document_id, b"v2", on_change="new_version")
    assert registry.store_raw(v1.document_id, b"v2", on_change="new_version") == v2


def test_superseded_is_terminal(registry):
    v1 = registry.register("c", DocumentType.STRUCTURED_FILING, SourceRef(url=URL))
    registry.store_raw(v1.document_id, b"v1")
    registry.store_raw(v1.document_id, b"v2", on_change="new_version")
    with pytest.raises(RegistryError, match="invalid status transition"):
        registry.mark(v1.document_id, DocumentStatus.PARSED, parser_version="x")


def test_migrates_phase1_database(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE documents (document_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
        document_type TEXT NOT NULL, source_url TEXT, local_source_path TEXT, raw_path TEXT, publication_date TEXT,
        fiscal_period TEXT, retrieval_timestamp TEXT, file_hash TEXT, content_type TEXT, size_bytes INTEGER,
        status TEXT NOT NULL, parser_version TEXT, error TEXT, registered_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("INSERT INTO documents VALUES ('doc_aaaaaaaaaaaaaaaa','c','other','https://example.com/x',NULL,NULL,"
                 "NULL,NULL,NULL,NULL,NULL,NULL,'registered',NULL,NULL,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    registry = DocumentRegistry(db, tmp_path / "raw")
    assert registry.get("doc_aaaaaaaaaaaaaaaa").supersedes is None
