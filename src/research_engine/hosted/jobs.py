"""Run the pipeline for a company in the background, one company at a time.

A single worker is deliberate. The SEC asks for no more than ten requests a second from one
client, a free-tier host has one small CPU and little memory, and matplotlib's pyplot is not safe
to use from several threads at once. Visitors queue; each sees their position and the stage their
company has reached.

A company is analysed once and then reused until its output is older than `fresh_for`, so the
tenth visitor to ask for the same company waits for nothing. Limits on how many new runs one
visitor may start, and on the queue length, keep a public endpoint from being turned into a way
to make this server fetch the whole SEC.
"""

from __future__ import annotations

import collections
import queue
import shutil
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..config import load_project_config
from ..errors import ResearchEngineError
from ..frameworks import FrameworkRegistry
from ..pipeline import run_ingestion
from ..pipeline.analysis import run_analysis_stage
from ..pipeline.forecast import run_forecast_stage
from ..pipeline.quality import run_quality_stage
from ..schemas.document import DocumentType
from .lookup import Fetcher, IndexEntry, write_config

STAGES = ("ingest", "quality", "analyze", "forecast")
STAGE_LABEL = {
    "ingest": "Fetching filings from SEC EDGAR",
    "quality": "Checking the reported figures",
    "analyze": "Calculating ratios and trends",
    "forecast": "Building estimates",
}


class Unavailable(ResearchEngineError):
    """A failure whose message is written for the visitor, not for whoever runs the server."""
State = Literal["queued", "running", "done", "failed"]


class RunRefused(ResearchEngineError):
    """A run was not queued: a limit was hit. `retry_after` is in seconds when waiting will help."""

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Job:
    job_id: str
    company_id: str
    name: str
    ticker: str
    state: State = "queued"
    stage: str | None = None
    stages_done: list[str] = field(default_factory=list)
    error: str | None = None
    cached: bool = False
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    refresh: bool = True                  # re-download SEC data; a document-only rerun uses the cache
    reason: str | None = None             # why it runs, when not a first analysis ("New document added")
    on_done: Callable[[Job], None] | None = field(default=None, repr=False)

    def public(self, position: int | None = None) -> dict:
        return {
            "job_id": self.job_id, "company_id": self.company_id, "name": self.name,
            "ticker": self.ticker, "state": self.state, "stage": self.stage,
            "stage_label": STAGE_LABEL.get(self.stage or ""),
            "stages": list(STAGES), "stages_done": list(self.stages_done),
            "error": self.error, "cached": self.cached, "queue_position": position, "reason": self.reason,
            "elapsed_seconds": round((self.finished or time.time()) - (self.started or self.created), 1),
        }


def _chain(first: Callable[[Job], None] | None, then: Callable[[Job], None]) -> Callable[[Job], None]:
    if first is None:
        return then

    def both(job: Job) -> None:
        first(job)
        then(job)
    return both


class JobRunner:
    def __init__(self, companies_root: Path, frameworks: FrameworkRegistry,
                 fetcher_factory: Callable[[], Fetcher], *,
                 fresh_for: float = 7 * 86400, max_queue: int = 20,
                 runs_per_requester: int = 6, window_seconds: float = 3600,
                 protected: frozenset[str] = frozenset()):
        self.root = Path(companies_root)
        self.frameworks = frameworks
        self.fetcher_factory = fetcher_factory
        self.fresh_for = fresh_for
        self.max_queue = max_queue
        self.runs_per_requester = runs_per_requester
        self.window = window_seconds
        self.protected = protected  # seeded companies: never deleted after a failed run
        self._jobs: dict[str, Job] = {}
        self._active: dict[str, str] = {}  # company_id -> job_id while queued or running
        self._queue: queue.Queue[str] = queue.Queue()
        self._order: collections.deque[str] = collections.deque()
        self._starts: dict[str, collections.deque[float]] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # ---- submitting ----------------------------------------------------------------------

    def _fresh(self, company_id: str) -> bool:
        marker = self.root / company_id / "output" / "forecast_manifest.json"
        return marker.is_file() and time.time() - marker.stat().st_mtime < self.fresh_for

    def submit(self, entry: IndexEntry, requester: str) -> Job:
        with self._lock:
            active = self._active.get(entry.company_id)
            if active:
                return self._jobs[active]  # someone already asked; share their run
            if self._fresh(entry.company_id):
                job = Job(uuid.uuid4().hex[:12], entry.company_id, entry.name, entry.ticker,
                          state="done", stages_done=list(STAGES), cached=True)
                job.started = job.finished = time.time()
                self._jobs[job.job_id] = job
                return job
            if requester != "boot":
                starts = self._starts[requester]
                now = time.time()
                while starts and now - starts[0] > self.window:
                    starts.popleft()
                if len(starts) >= self.runs_per_requester:
                    raise RunRefused(
                        f"You can add up to {self.runs_per_requester} new companies an hour. Companies "
                        "already prepared stay available.", retry_after=int(self.window - (now - starts[0])) + 1)
                starts.append(now)
            if len(self._order) >= self.max_queue:
                raise RunRefused("We're busy preparing other companies. Please try again in a few minutes.",
                                retry_after=120)
            write_config(entry, self.root)
            job = Job(uuid.uuid4().hex[:12], entry.company_id, entry.name, entry.ticker)
            self._jobs[job.job_id] = job
            self._active[entry.company_id] = job.job_id
            self._order.append(job.job_id)
            self._queue.put(job.job_id)
            self._ensure_worker()
            return job

    def resubmit(self, company_id: str, *, reason: str, on_done: Callable[[Job], None] | None = None) -> Job:
        """Analyse an already configured company again, e.g. because a document was added to it.

        A queued run for the company already includes the new document (the library is read when
        the run starts), so it is shared. A running one may have read the library already, so a
        new run is queued behind it.
        """
        config = load_project_config(self.root / company_id / "config.yaml")
        with self._lock:
            active = self._active.get(company_id)
            if active and self._jobs[active].state == "queued":
                job = self._jobs[active]
                if on_done:
                    job.on_done = _chain(job.on_done, on_done)
                return job
            if len(self._order) >= self.max_queue:
                raise RunRefused("We're busy preparing other companies. Please try again in a few minutes.",
                                 retry_after=120)
            job = Job(uuid.uuid4().hex[:12], company_id, config.company.name, config.company.ticker,
                      refresh=False, reason=reason, on_done=on_done)
            self._jobs[job.job_id] = job
            self._active[company_id] = job.job_id
            self._order.append(job.job_id)
            self._queue.put(job.job_id)
            self._ensure_worker()
            return job

    def get(self, job_id: str) -> tuple[Job, int | None] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            position = list(self._order).index(job_id) + 1 if job_id in self._order else None
            return job, position

    def active(self) -> list[tuple[Job, int | None]]:
        """Queued and running jobs, in the order they will finish."""
        with self._lock:
            ids = [jid for jid in self._jobs if self._jobs[jid].state in ("queued", "running")]
            order = list(self._order)
            ids.sort(key=lambda jid: order.index(jid) if jid in order else -1)
            return [(self._jobs[jid], order.index(jid) + 1 if jid in order else None) for jid in ids]

    # ---- running -------------------------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="pipeline-worker", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self._jobs[job_id]
                job.state, job.started = "running", time.time()
            try:
                self._run(job)
                job.state = "done"
                if job.on_done:
                    try:
                        job.on_done(job)
                    except Exception:  # a follow-up (saving to the repository) must not fail the run
                        traceback.print_exc()
            except Exception as exc:  # the worker must survive any one company's failure
                job.state = "failed"
                job.error = self._describe(job, exc)
                if not isinstance(exc, Unavailable):
                    traceback.print_exc()  # the detail belongs in the server log, not on the page
            finally:
                job.finished = time.time()
                with self._lock:
                    if self._active.get(job.company_id) == job_id:
                        self._active.pop(job.company_id, None)
                    if job_id in self._order:
                        self._order.remove(job_id)
                self._queue.task_done()

    def _describe(self, job: Job, exc: Exception) -> str:
        if isinstance(exc, Unavailable):
            text = str(exc)
        else:
            text = f"We couldn't prepare {job.name.rstrip('.')} just now. Please try again later."
        return text.replace(str(self.root.resolve()), "<workspace>").replace(str(self.root), "<workspace>")

    def _run(self, job: Job) -> None:
        workspace = self.root / job.company_id
        config = load_project_config(workspace / "config.yaml")

        job.stage = "ingest"
        result = run_ingestion(config, workspace=workspace, frameworks=self.frameworks,
                               fetcher=self.fetcher_factory(), refresh=job.refresh)
        extracted = result.extraction.facts_emitted if result.extraction else 0
        # A library document that cannot be read is reported with the document; only the SEC's
        # structured data is essential to an analysis.
        failures = [o for o in result.failures if o.record.document_type is DocumentType.STRUCTURED_FILING]
        if failures or not extracted:
            details = [o.detail or o.record.error or o.action for o in failures]
            if not failures or all("404" in d for d in details):
                # The SEC answers 404 for companyfacts when a filer has never tagged XBRL statements.
                message = (f"The SEC holds no structured financial statements (XBRL) for {job.name.rstrip('.')}. "
                           "This is common for funds, trusts, shell companies and some foreign issuers.")
            else:
                print(f"download failed for {job.name}: " + "; ".join(details), flush=True)
                message = (f"We couldn't download the filings for {job.name.rstrip('.')} from the SEC just now. "
                           "Please try again in a few minutes.")
            if job.company_id not in self.protected and job.reason is None:
                shutil.rmtree(workspace, ignore_errors=True)  # nothing to show; keep the list clean
            raise Unavailable(message)
        job.stages_done.append("ingest")

        for stage, run in (("quality", run_quality_stage), ("analyze", run_analysis_stage),
                           ("forecast", run_forecast_stage)):
            job.stage = stage
            run(config, workspace=workspace, frameworks=self.frameworks)
            job.stages_done.append(stage)
        job.stage = None
