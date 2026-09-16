import hashlib
import os
import stat

import pytest

from research_engine.errors import RegistryError
from research_engine.registry import DocumentRegistry
from research_engine.schemas import DocumentStatus, DocumentType, SourceRef

PDF = b"%PDF-1.7 fake annual report"


@pytest.fixture
def registry(tmp_path):
    return DocumentRegistry(tmp_path / "db" / "registry.sqlite", tmp_path / "raw")


@pytest.fixture
def doc(registry):
    return registry.register("nyse-tst", DocumentType.ANNUAL_REPORT,
                             SourceRef(url="https://example.com/ar-2025.pdf", fiscal_period="FY2025"))


def test_register_is_idempotent(registry, doc):
    again = registry.register("nyse-tst", DocumentType.ANNUAL_REPORT, SourceRef(url="https://example.com/ar-2025.pdf"))
    assert again.document_id == doc.document_id
    assert len(registry.list_documents()) == 1
    assert doc.status is DocumentStatus.REGISTERED and doc.fiscal_period == "FY2025"


def test_same_url_different_company_is_distinct(registry, doc):
    other = registry.register("nyse-oth", DocumentType.ANNUAL_REPORT, SourceRef(url="https://example.com/ar-2025.pdf"))
    assert other.document_id != doc.document_id


def test_store_raw_is_content_addressed_and_read_only(registry, doc):
    rec = registry.store_raw(doc.document_id, PDF, content_type="application/pdf")
    digest = hashlib.sha256(PDF).hexdigest()
    assert rec.status is DocumentStatus.RETRIEVED and rec.file_hash == digest and rec.size_bytes == len(PDF)
    assert rec.raw_path.endswith(f"{digest}.pdf")
    mode = os.stat(rec.raw_path).st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert registry.find_by_hash(digest)[0].document_id == doc.document_id


def test_unchanged_content_is_a_cache_hit(registry, doc):
    first = registry.store_raw(doc.document_id, PDF)
    second = registry.store_raw(doc.document_id, PDF)
    assert first == second


def test_changed_content_never_overwrites(registry, doc):
    rec = registry.store_raw(doc.document_id, PDF)
    with pytest.raises(RegistryError, match="never overwritten"):
        registry.store_raw(doc.document_id, PDF + b" restated")
    with open(rec.raw_path, "rb") as fh:
        assert fh.read() == PDF


def test_empty_content_rejected(registry, doc):
    with pytest.raises(RegistryError, match="empty"):
        registry.store_raw(doc.document_id, b"")


def test_status_transitions(registry, doc):
    with pytest.raises(RegistryError, match="invalid status transition"):
        registry.mark(doc.document_id, DocumentStatus.PARSED)
    registry.store_raw(doc.document_id, PDF)
    with pytest.raises(RegistryError, match="parser_version"):
        registry.mark(doc.document_id, DocumentStatus.PARSED)
    registry.mark(doc.document_id, DocumentStatus.PARSED, parser_version="0.1.0")
    done = registry.mark(doc.document_id, DocumentStatus.EXTRACTED)
    assert done.parser_version == "0.1.0"
    reparsed = registry.mark(doc.document_id, DocumentStatus.PARSED, parser_version="0.2.0")
    assert reparsed.parser_version == "0.2.0"
    history = [h["to_status"] for h in registry.history(doc.document_id)]
    assert history == ["registered", "retrieved", "parsed", "extracted", "parsed"]


def test_failure_requires_error_and_can_retry(registry, doc):
    with pytest.raises(RegistryError, match="error message"):
        registry.mark(doc.document_id, DocumentStatus.FAILED)
    failed = registry.mark(doc.document_id, DocumentStatus.FAILED, error="HTTP 404")
    assert failed.error == "HTTP 404"
    retried = registry.mark(doc.document_id, DocumentStatus.REGISTERED)
    assert retried.error is None


def test_persistence_and_filters(tmp_path, registry, doc):
    registry.register("nyse-tst", DocumentType.EARNINGS_TRANSCRIPT, SourceRef(url="https://example.com/t.pdf"))
    reopened = DocumentRegistry(tmp_path / "db" / "registry.sqlite", tmp_path / "raw")
    assert len(reopened.list_documents(company_id="nyse-tst")) == 2
    assert len(reopened.list_documents(document_type=DocumentType.EARNINGS_TRANSCRIPT)) == 1
    assert reopened.list_documents(status=DocumentStatus.RETRIEVED) == []


def test_local_file_source(registry, tmp_path):
    local = tmp_path / "10k.html"
    local.write_text("<html></html>")
    rec = registry.register("nyse-tst", DocumentType.REGULATORY_FILING, SourceRef(path=local))
    assert rec.local_source_path == str(local.resolve()) and rec.source_url is None
    stored = registry.store_raw(rec.document_id, local.read_bytes())
    assert stored.raw_path.endswith(".html")


def test_unknown_document(registry):
    with pytest.raises(RegistryError, match="unknown document"):
        registry.get("doc_ffffffffffffffff")
