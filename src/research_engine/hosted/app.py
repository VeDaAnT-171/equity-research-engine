"""The public app: the read-only dashboard API plus company search and on-demand analysis.

Built on `api.app.create_app`, so every figure is served by exactly the code the local dashboard
uses. What this module adds is the ability to *start* work, and the rules that make that safe on
a public address:

- configs are written only from the SEC's company index, looked up by CIK — a visitor chooses a
  company, never a URL, a path or a ticker the SEC does not know;
- everything is written under one data directory;
- every JSON response is scrubbed of local fields and of the data directory's path, so the
  loopback-only rule `serve` enforces is replaced by never having anything local to expose;
- starting runs is rate-limited per visitor and the queue is capped (see `jobs`).
"""

from __future__ import annotations

import collections
import contextlib
import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Imported at module level, not inside the factory: FastAPI resolves route annotations against
# module globals, so a `Request` imported locally is read as a query parameter named "request".
from fastapi import Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..documents.library import INDEX_FILE, LIBRARY_DIR, MAX_BYTES, DocumentLibrary
from ..errors import ResearchEngineError
from ..frameworks import FrameworkRegistry
from .github import GitHubPersister, PersistError
from .jobs import STAGES, Job, JobRunner, RunRefused
from .lookup import CompanyIndex, Fetcher, IndexUnavailable

SEARCH_DOWN = "Company search is temporarily unavailable. Please try again shortly."
LOCAL_FIELDS = frozenset({"companies_root", "raw_path", "local_source_path", "local_path", "workspace"})


def _scrub(value: Any, root_forms: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v, root_forms) for k, v in value.items() if k not in LOCAL_FIELDS}
    if isinstance(value, list):
        return [_scrub(v, root_forms) for v in value]
    if isinstance(value, str):
        for form in root_forms:
            value = value.replace(form, "<workspace>")
    return value


def _merge_index(source: Path, dest: Path) -> None:
    if not source.is_file():
        return
    theirs = json.loads(source.read_text(encoding="utf-8")).get("documents", [])
    ours = json.loads(dest.read_text(encoding="utf-8")).get("documents", []) if dest.is_file() else []
    known = {d.get("id") for d in ours}
    merged = ours + [d for d in theirs if d.get("id") not in known]
    dest.write_text(json.dumps({"documents": merged}, indent=2) + "\n", encoding="utf-8")


def seed(companies_root: Path, seed_dir: Path | None) -> frozenset[str]:
    """Copy configured companies (the repo's `companies/`) into the data directory once."""
    seeded: set[str] = set()
    if seed_dir is None or not Path(seed_dir).is_dir():
        return frozenset()
    for config in sorted(Path(seed_dir).glob("*/config.yaml")):
        target = companies_root / config.parent.name / "config.yaml"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(config, target)
        # The company's document library travels with it; documents added here since are kept.
        library = config.parent / LIBRARY_DIR
        if library.is_dir():
            dest = target.parent / LIBRARY_DIR
            dest.mkdir(exist_ok=True)
            for item in library.iterdir():
                if item.is_file() and item.name != INDEX_FILE and not (dest / item.name).exists():
                    shutil.copyfile(item, dest / item.name)
            _merge_index(library / INDEX_FILE, dest / INDEX_FILE)
        seeded.add(config.parent.name)
    return frozenset(seeded)


def create_hosted_app(data_dir: Path, frameworks_dir: Path, fetcher_factory: Callable[[], Fetcher], *,
                      seed_dir: Path | None = None, runner_options: dict | None = None,
                      start_seeds: bool = True, persister: GitHubPersister | None = None,
                      uploads_per_hour: int = 5, max_documents: int = 40):
    from ..api.app import create_app

    data_dir = Path(data_dir).resolve()
    companies_root = data_dir / "companies"
    companies_root.mkdir(parents=True, exist_ok=True)
    protected = seed(companies_root, seed_dir)
    frameworks = FrameworkRegistry(frameworks_dir)
    index = CompanyIndex(data_dir / "sec_company_index.json", fetcher_factory())
    runner = JobRunner(companies_root, frameworks, fetcher_factory, protected=protected,
                       **(runner_options or {}))

    app = create_app(companies_root)
    app.title = "Equity Research Engine (hosted)"
    root_forms = tuple(sorted({str(data_dir), json.dumps(str(data_dir))[1:-1]}, key=len, reverse=True))

    @app.middleware("http")
    async def scrub_local(request: Request, call_next):
        response = await call_next(request)
        if not response.headers.get("content-type", "").startswith("application/json"):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            data = json.loads(body)
        except ValueError:
            data = body.decode("utf-8", "replace")
        headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-type")}
        return JSONResponse(_scrub(data, root_forms), status_code=response.status_code, headers=headers)

    def requester(request: Request) -> str:
        # Behind a hosting proxy the right-most X-Forwarded-For entry is the one the proxy added;
        # entries to its left are whatever the client chose to send.
        forwarded = request.headers.get("x-forwarded-for", "")
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        return hops[-1] if hops else (request.client.host if request.client else "unknown")

    persister = persister if persister is not None else GitHubPersister.from_env()
    uploads: dict[str, collections.deque[float]] = collections.defaultdict(collections.deque)

    @app.get("/api/app", tags=["hosted"])
    def app_info() -> dict:
        return {"hosted": True, "stages": list(STAGES),
                "coverage": "Companies that file financial statements with the U.S. SEC",
                "documents": True, "max_document_mb": MAX_BYTES // (1024 * 1024),
                "documents_saved": persister is not None}

    def _upload_allowed(who: str) -> None:
        window = uploads[who]
        now = time.time()
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= uploads_per_hour:
            raise HTTPException(status_code=429, detail=f"You can add up to {uploads_per_hour} documents an hour.",
                                headers={"Retry-After": str(int(3600 - (now - window[0])) + 1)})
        window.append(now)

    def _workspace(company_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", company_id):
            raise HTTPException(status_code=404, detail="unknown company")
        workspace = companies_root / company_id
        if not (workspace / "output" / "forecast_manifest.json").is_file():
            raise HTTPException(status_code=409, detail="This company has to finish its first analysis before documents can be added.")
        return workspace

    def _after(company_id: str, workspace: Path, entry_id: str) -> Callable[[Job], None]:
        def done(_job: Job) -> None:
            if persister is None:
                return
            report = next((d for d in json.loads((workspace / "output" / "documents.json").read_text(encoding="utf-8")).get("documents", [])
                           if d.get("entry_id") == entry_id), None)
            library = DocumentLibrary(workspace)
            entry = library.get(entry_id)
            if report is None or report.get("status") != "verified" or entry is None or entry.saved:
                return
            try:
                url = persister.propose(company_id, workspace, entry, summary=report.get("reason", ""))
            except PersistError as exc:
                print(f"could not save {entry_id} for {company_id}: {exc}", flush=True)
                return
            library.mark_saved(entry_id, url)
        return done

    def _queue_study(company_id: str, workspace: Path, entry_id: str) -> dict:
        try:
            job = runner.resubmit(company_id, reason="New document added", on_done=_after(company_id, workspace, entry_id))
        except RunRefused as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None
        found = runner.get(job.job_id)
        return job.public(found[1] if found else None)

    @app.put("/api/companies/{company_id}/library", tags=["hosted"], status_code=202,
             summary="Add a document (the request body is the HTML or PDF file)")
    async def add_document(company_id: str, request: Request,
                           kind: str = Query("annual_report", max_length=40),
                           title: str | None = Query(None, max_length=120),
                           filename: str = Query("document", max_length=200)) -> JSONResponse:
        workspace = _workspace(company_id)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        _upload_allowed(requester(request))
        content = await request.body()
        if len(content) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        library = DocumentLibrary(workspace)
        if len(library.entries()) >= max_documents:
            raise HTTPException(status_code=409, detail="This company's library is full.")
        try:
            entry, new = library.add_file(content, original_name=filename, kind=kind, title=title, added_by="visitor")
        except ResearchEngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        job = _queue_study(company_id, workspace, entry.id) if new else None
        return JSONResponse({"document": entry.__dict__, "new": new, "run": job}, status_code=202 if new else 200)

    @app.post("/api/companies/{company_id}/library", tags=["hosted"], status_code=202,
              summary="Add an SEC filing by its EDGAR address")
    def add_sec_document(company_id: str, request: Request, url: str = Body(embed=True, max_length=300),
                         kind: str = Body("annual_report", embed=True, max_length=40),
                         title: str | None = Body(None, embed=True, max_length=120)) -> JSONResponse:
        workspace = _workspace(company_id)
        _upload_allowed(requester(request))
        library = DocumentLibrary(workspace)
        if len(library.entries()) >= max_documents:
            raise HTTPException(status_code=409, detail="This company's library is full.")
        try:
            entry, new = library.add_sec_filing(url.strip(), kind=kind, title=title, added_by="visitor")
        except ResearchEngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        job = _queue_study(company_id, workspace, entry.id) if new else None
        return JSONResponse({"document": entry.__dict__, "new": new, "run": job}, status_code=202 if new else 200)

    @app.get("/api/search", tags=["hosted"], summary="Find a company in the SEC company index")
    def search(q: str = Query(min_length=1, max_length=80)) -> list[dict]:
        try:
            matches = index.search(q)
        except IndexUnavailable as exc:
            print(f"company index unavailable: {exc}", flush=True)
            raise HTTPException(status_code=503, detail=SEARCH_DOWN) from None
        out = []
        for entry in matches:
            info = entry.public()
            info["analysed"] = (companies_root / entry.company_id / "output" / "forecast_manifest.json").is_file()
            out.append(info)
        return out

    @app.post("/api/runs", tags=["hosted"], summary="Analyse a company (by SEC CIK)")
    def start_run(request: Request, cik: str = Body(embed=True, min_length=1, max_length=10)) -> JSONResponse:
        try:
            entry = index.get(cik)
        except IndexUnavailable as exc:
            print(f"company index unavailable: {exc}", flush=True)
            raise HTTPException(status_code=503, detail=SEARCH_DOWN) from None
        if entry is None:
            raise HTTPException(status_code=404, detail="That company isn't in the SEC's list of filers.")
        try:
            job = runner.submit(entry, requester(request))
        except RunRefused as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(status_code=429, detail=str(exc), headers=headers) from None
        except ResearchEngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        found = runner.get(job.job_id)
        position = found[1] if found else None
        return JSONResponse(job.public(position), status_code=200 if job.state == "done" else 202)

    @app.get("/api/runs", tags=["hosted"], summary="Analyses queued or running now")
    def active_runs() -> list[dict]:
        return [job.public(position) for job, position in runner.active()]

    @app.get("/api/runs/{job_id}", tags=["hosted"])
    def run_status(job_id: str) -> dict:
        found = runner.get(job_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown run")
        job, position = found
        return job.public(position)

    if start_seeds:
        from ..config import load_project_config
        from .lookup import IndexEntry
        for company_id in sorted(protected):
            config = load_project_config(companies_root / company_id / "config.yaml")
            c = config.company
            entry = IndexEntry(str(c.identifiers.cik or ""), c.name, c.ticker, c.exchange)
            if entry.company_id != company_id or not c.identifiers.cik:
                continue  # a seed whose id does not follow the index convention is left as configured
            # a seed that cannot be queued (limits, a bad config) must not stop the app starting
            with contextlib.suppress(ResearchEngineError):
                runner.submit(entry, "boot")

    app.state.runner = runner
    app.state.index = index
    return app
