"""Phase 2 pipeline: register -> retrieve (cached) -> identify -> classify -> extract -> write outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..calendar import FiscalCalendar
from ..classification import FrameworkSelection, select_framework
from ..documents.extract import Finding, Study, Verdict, study_document, verify
from ..documents.library import KIND_LABEL, DocumentLibrary, LibraryEntry, sec_archive_accession
from ..errors import ConfigError, ExtractionError, FetchError, RegistryError
from ..extraction import ExtractionReport, current_facts, extract_xbrl_facts
from ..frameworks import FrameworkRegistry, framework_fingerprint
from ..ingestion.http import Fetcher
from ..lineage import LineageGraph
from ..registry import DocumentRegistry
from ..schemas.company import ProjectConfig, SourceRef
from ..schemas.document import DocumentRecord, DocumentStatus, DocumentType
from ..schemas.financial import FinancialFact, Provenance, SourceLocation, make_fact_id
from ..sources.base import find_source
from ..sources.sec import (
    SEC_HOST,
    SEC_SOURCES,
    EntityProfile,
    SecCompanyFacts,
    SecSubmissions,
)
from ..versioning import PARSER_VERSION
from . import outputs


@dataclass
class DocumentOutcome:
    record: DocumentRecord
    action: str  # fetched | cached | new_version | failed | parsed | not_parsed
    detail: str = ""


@dataclass
class IngestionResult:
    company_id: str
    workspace: Path
    output_dir: Path
    outcomes: list[DocumentOutcome] = field(default_factory=list)
    profile: EntityProfile | None = None
    calendar: FiscalCalendar | None = None
    framework: FrameworkSelection | None = None
    facts: list[FinancialFact] = field(default_factory=list)
    extraction: ExtractionReport | None = None
    warnings: list[str] = field(default_factory=list)
    framework_sha256: str | None = None
    documents: list[dict] = field(default_factory=list)   # what each studied document contributed

    @property
    def failures(self) -> list[DocumentOutcome]:
        return [o for o in self.outcomes if o.action == "failed"]


def requires_sec_access(config: ProjectConfig) -> bool:
    return any(ref.url and urlparse(ref.url).hostname == SEC_HOST for _, ref in config.sources.iter_documents())


def preflight(config: ProjectConfig) -> None:
    """Catch wrong-company configuration before any network access."""
    cik = config.company.identifiers.cik
    for doc_type, ref in config.sources.iter_documents():
        if not ref.url:
            continue
        source = find_source(ref.url, SEC_SOURCES)
        if doc_type is DocumentType.STRUCTURED_FILING and ref.url and urlparse(ref.url).hostname == SEC_HOST and source is None:
            raise ConfigError(f"unsupported SEC endpoint {ref.url}; supported: companyfacts, submissions")
        if source is None:
            continue
        if doc_type is not DocumentType.STRUCTURED_FILING:
            raise ConfigError(f"{ref.url} is a structured data endpoint; list it under sources.structured_filings")
        url_cik = source.identifier_from_url(ref.url)
        if not cik:
            raise ConfigError(f"{ref.url} requires company.identifiers.cik to be set for identity checks")
        if url_cik != cik:
            raise ConfigError(f"CIK mismatch: config has {cik} but {ref.url} is for CIK {url_cik}")


def _retrieve(registry: DocumentRegistry, record: DocumentRecord, url: str | None, path: Path | None,
              fetcher: Fetcher | None, refresh: bool, offline: bool) -> DocumentOutcome:
    current = registry.current_version(record.document_id)
    cached = current.file_hash is not None and current.status is not DocumentStatus.FAILED
    if cached and (not refresh or offline):
        return DocumentOutcome(current, "cached")
    if offline and path is None:  # a local file needs no network
        return _fail(registry, current, "not in cache and --offline was given")
    try:
        if path is not None:
            content, content_type = path.read_bytes(), None
        elif fetcher is None:
            return _fail(registry, current, "no fetcher configured for network retrieval")
        else:
            result = fetcher.fetch(url)
            content, content_type = result.content, result.content_type
        if current.status is DocumentStatus.FAILED:
            current = registry.mark(current.document_id, DocumentStatus.REGISTERED, note="retry")
        stored = registry.store_raw(current.document_id, content, content_type=content_type, on_change="new_version")
    except (FetchError, OSError, RegistryError) as exc:
        if cached:  # refresh failed but a good copy exists: keep using it, but say so
            return DocumentOutcome(current, "cached", f"refresh failed, using cached copy: {exc}")
        return _fail(registry, current, str(exc))
    if stored.document_id != current.document_id:
        return DocumentOutcome(stored, "new_version", f"content changed; supersedes {current.document_id}")
    return DocumentOutcome(stored, "cached" if cached else "fetched")


def _fail(registry: DocumentRegistry, record: DocumentRecord, message: str) -> DocumentOutcome:
    if record.status is DocumentStatus.REGISTERED:
        record = registry.mark(record.document_id, DocumentStatus.FAILED, error=message)
    return DocumentOutcome(record, "failed", message)


def _mark_extracted(registry: DocumentRegistry, record: DocumentRecord) -> DocumentRecord:
    if record.status is DocumentStatus.EXTRACTED and record.parser_version == PARSER_VERSION:
        return record
    if record.status is DocumentStatus.RETRIEVED or record.status is DocumentStatus.EXTRACTED:
        record = registry.mark(record.document_id, DocumentStatus.PARSED, parser_version=PARSER_VERSION)
    return registry.mark(record.document_id, DocumentStatus.EXTRACTED)


def _read_raw(record: DocumentRecord) -> bytes:
    return Path(record.raw_path).read_bytes()


def _filing_index(submissions: DocumentRecord | None) -> dict[str, tuple[str, str]]:
    """Accession -> (form, filing date) from the SEC submissions document, when it was retrieved."""
    if submissions is None:
        return {}
    import json
    try:
        recent = json.loads(_read_raw(submissions))["filings"]["recent"]
        return {a: (f, d) for a, f, d in zip(recent["accessionNumber"], recent["form"], recent["filingDate"], strict=False)}
    except (ValueError, KeyError, TypeError):
        return {}


def _fact_from(finding: Finding, company_id: str, record: DocumentRecord, form: str | None,
               filed: str | None, accession: str | None) -> FinancialFact:
    from datetime import date
    is_table = finding.concept is None
    return FinancialFact(
        fact_id=make_fact_id(company_id, finding.metric_id, finding.period, Provenance.REPORTED,
                             f"{record.document_id}|{finding.where}|{finding.value}"),
        company_id=company_id, metric_id=finding.metric_id, value=finding.value, unit=finding.unit,
        currency=finding.currency, period=finding.period, provenance=Provenance.REPORTED,
        extraction_method=finding.method,
        source=SourceLocation(
            document_id=record.document_id, page=finding.page,
            table=finding.where if is_table else None, xbrl_concept=finding.concept,
            filing_accession=accession, filing_form=form,
            filed_date=date.fromisoformat(filed) if filed else None,
            source_text=finding.source_text,
        ),
        notes="from a document; used because the SEC's structured data does not have this figure",
    )


def _study_documents(config: ProjectConfig, framework, result: IngestionResult, registry: DocumentRegistry,
                     documents: list[tuple[str, DocumentType, str, DocumentRecord]],
                     library_of: dict[str, LibraryEntry], filings: dict[str, tuple[str, str]]) -> list[FinancialFact]:
    """Study every document, keep the verified ones, and use their figures only where the SEC data has none."""
    reported = [f for f in result.facts if f.provenance is Provenance.REPORTED]
    known = current_facts(reported)
    have = {(f.metric_id, f.period.period_type, f.period.label) for f in reported}
    candidates: dict[tuple, tuple[tuple, FinancialFact, str]] = {}
    reports: list[dict[str, Any]] = []
    for doc_id, _doc_type, location, record in documents:
        entry = library_of.get(doc_id)
        try:
            study = study_document(_read_raw(record), framework=framework, calendar=result.calendar,
                                   currency=config.company.reporting_currency,
                                   company_cik=config.company.identifiers.cik, known=known)
        except Exception as exc:  # a malformed upload must not stop the company's analysis
            study = Study(media_type="unknown", notes=[f"could not be read ({type(exc).__name__})"])
        verdict = verify(study, known) if study.findings else Verdict(
            "unverified", 0, 0, "No figures the framework uses were found in it.")
        accession = sec_archive_accession(location) if location.startswith("https://") else None
        form, filed = filings.get(accession or "", (study.form, None))
        rank = (bool(accession), filed or "", entry.added_at if entry else "")
        if verdict.status == "verified":
            for finding in study.findings:
                key = (finding.metric_id, finding.period.period_type, finding.period.label)
                if finding.check_only or key in have:
                    continue
                fact = _fact_from(finding, config.company_id, record, form or study.form, filed, accession)
                incumbent = candidates.get(key)
                if incumbent is None or rank > incumbent[0]:
                    candidates[key] = (rank, fact, doc_id)
        if study.media_type != "unknown":
            _mark_extracted(registry, registry.get(record.document_id))
        reports.append({
            "document_id": doc_id, "entry_id": entry.id if entry else None,
            "title": entry.title if entry else (record.document_type.value.replace("_", " ").capitalize()),
            "kind": entry.kind if entry else record.document_type.value,
            "kind_label": KIND_LABEL.get(entry.kind if entry else "", record.document_type.value.replace("_", " ").capitalize()),
            "added_by": entry.added_by if entry else "config", "added_at": entry.added_at if entry else None,
            "url": location if location.startswith("https://") else None,
            "media_type": study.media_type, "form": form or study.form, "filed": filed,
            "identity": study.identity, "status": verdict.status, "reason": verdict.reason,
            "checked": verdict.checked, "agreed": verdict.agreed, "disagreements": verdict.disagreements[:10],
            "found": len(study.findings), "ambiguous": len(study.ambiguous), "notes": study.notes[:10],
            "contributed": {},
        })
    added = []
    contributed: dict[str, dict[str, list[str]]] = {}
    for (metric_id, _ptype, label), (_rank, fact, doc_id) in sorted(candidates.items(), key=lambda kv: kv[0][2]):
        added.append(fact)
        contributed.setdefault(doc_id, {}).setdefault(metric_id, []).append(label)
    for report in reports:
        report["contributed"] = contributed.get(report["document_id"], {})
    result.documents = reports
    return added


def run_ingestion(
    config: ProjectConfig,
    *,
    workspace: Path,
    frameworks: FrameworkRegistry,
    fetcher: Fetcher | None,
    refresh: bool = False,
    offline: bool = False,
) -> IngestionResult:
    preflight(config)
    workspace = Path(workspace)
    registry = DocumentRegistry(workspace / "data" / "registry.sqlite", workspace / "data" / "raw")
    result = IngestionResult(config.company_id, workspace, workspace / "output")

    retrieved: dict[str, tuple[DocumentType, str, DocumentRecord]] = {}
    library_of: dict[str, LibraryEntry] = {}
    sources: list[tuple[DocumentType, SourceRef, LibraryEntry | None]] = [
        (doc_type, ref, None) for doc_type, ref in config.sources.iter_documents()]
    sources += [(doc_type, ref, entry) for entry, doc_type, ref in DocumentLibrary(workspace).source_refs()]
    for doc_type, ref, entry in sources:
        record = registry.register(config.company_id, doc_type, ref)
        # A library document that has not changed need not be fetched again: its content is pinned.
        outcome = _retrieve(registry, record, ref.url, ref.path, fetcher, refresh and entry is None, offline)
        result.outcomes.append(outcome)
        if outcome.action != "failed":
            retrieved[outcome.record.document_id] = (doc_type, ref.location, outcome.record)
            if entry is not None:
                library_of[outcome.record.document_id] = entry

    submissions = companyfacts = None
    for _doc_id, (doc_type, location, record) in retrieved.items():
        source = find_source(location, SEC_SOURCES) if doc_type is DocumentType.STRUCTURED_FILING else None
        if isinstance(source, SecSubmissions):
            submissions = record
        elif isinstance(source, SecCompanyFacts):
            companyfacts = record
        elif doc_type is DocumentType.STRUCTURED_FILING:
            result.warnings.append(f"no adapter for structured source {location}; retrieved but not parsed")

    # Identity and calendar
    if submissions is not None:
        profile = SecSubmissions().normalize(_read_raw(submissions), submissions.document_id)
        if profile.cik != config.company.identifiers.cik:
            raise ExtractionError(f"identity mismatch: submissions document is for CIK {profile.cik}")
        if profile.tickers and config.company.ticker not in profile.tickers:
            raise ExtractionError(
                f"identity mismatch: ticker {config.company.ticker} not in SEC tickers {list(profile.tickers)} "
                f"for {profile.name} (CIK {profile.cik})"
            )
        result.profile = profile
        _mark_extracted(registry, submissions)
    elif companyfacts is not None:
        result.warnings.append("SEC submissions not available: ticker identity, SIC and fiscal year end were not verified")

    fye_config = config.company.fiscal_year_end_month
    fye_sec = result.profile.fiscal_year_end_month if result.profile else None
    if fye_config and fye_sec and fye_config != fye_sec:
        raise ConfigError(f"fiscal_year_end_month is {fye_config} in config but SEC reports month {fye_sec}")
    fye = fye_config or fye_sec
    if fye is None and companyfacts is not None:
        raise ConfigError("fiscal year end unknown: set company.fiscal_year_end_month or add the SEC submissions source")
    if fye is not None:
        result.calendar = FiscalCalendar(fye, config.company.fiscal_year_convention)

    result.framework = select_framework(config, result.profile, frameworks)
    framework = frameworks.get(result.framework.name)
    result.framework_sha256 = framework_fingerprint(framework)

    # Facts
    if companyfacts is not None:
        parsed = SecCompanyFacts().normalize(_read_raw(companyfacts), companyfacts.document_id)
        if parsed.cik != config.company.identifiers.cik:
            raise ExtractionError(f"identity mismatch: companyfacts document is for CIK {parsed.cik}")
        facts, report = extract_xbrl_facts(
            parsed.observations,
            framework=framework,
            calendar=result.calendar,
            company_id=config.company_id,
            document_id=companyfacts.document_id,
            expected_currency=config.company.reporting_currency,
        )
        report.malformed_observations = parsed.malformed
        result.facts, result.extraction = facts, report
        _mark_extracted(registry, companyfacts)
    else:
        result.warnings.append("no XBRL companyfacts source retrieved: no financial facts extracted")

    documents_to_study: list[tuple[str, DocumentType, str, DocumentRecord]] = [(doc_id, doc_type, location, record) for doc_id, (doc_type, location, record)
                          in retrieved.items() if doc_type is not DocumentType.STRUCTURED_FILING]
    if documents_to_study:
        if result.calendar is None:
            result.warnings.append("documents were not studied: the fiscal calendar is unknown")
        else:
            filings = _filing_index(submissions)
            added = _study_documents(config, framework, result, registry, documents_to_study, library_of, filings)
            result.facts = [*result.facts, *added]

    documents = registry.list_documents(company_id=config.company_id, include_superseded=True)
    lineage = LineageGraph.from_records(documents, result.facts)
    broken = lineage.broken_nodes()
    if broken:
        raise ExtractionError(f"lineage check failed for {len(broken)} nodes, e.g. {broken[0].node_id}")
    result.outcomes = [DocumentOutcome(registry.get(o.record.document_id), o.action, o.detail) for o in result.outcomes]
    outputs.write_ingestion_outputs(result, config, documents, lineage)
    return result
