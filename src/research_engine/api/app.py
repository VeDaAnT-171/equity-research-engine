"""Read-only HTTP API over a companies workspace, plus the dashboard that consumes it.

The API is deliberately thin. It holds no state, computes no analytics and caches nothing but
the lineage index, so anything it returns can be checked against the files in `output/`. It is
local analyst tooling: there is no authentication, and it should not be exposed to a network.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from ..errors import ResearchEngineError
from ..versioning import version_stamp
from . import repository as repo
from .repository import (
    Repository, StageNotRun, UnknownChart, UnknownCompany, UnknownNode,
)

STATIC_DIR = Path(__file__).parent / "static"


def _require_fastapi():
    try:
        import fastapi  # noqa: F401
    except ImportError:
        raise ResearchEngineError(
            "the dashboard needs FastAPI and uvicorn, which are optional: "
            'pip install -e ".[api]"'
        ) from None


def create_app(companies_root: Path):
    """Build the ASGI application for one companies directory."""
    _require_fastapi()
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

    store = Repository(Path(companies_root))
    app = FastAPI(
        title="Equity Research Engine",
        description="Read-only API over pipeline outputs. Every value is traceable to a filing.",
        version=version_stamp()["engine_version"],
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    def company(company_id: str):
        try:
            return store.get(company_id)
        except UnknownCompany as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def guarded(fn, *args, **kwargs) -> Any:
        """Missing stages are a 409, not a 500: the workspace is fine, the pipeline is behind."""
        try:
            return fn(*args, **kwargs)
        except StageNotRun as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc), "stage": exc.stage,
                                                         "company_id": exc.company_id}) from None
        except (UnknownChart, UnknownNode) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ResearchEngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    # ---- metadata -------------------------------------------------------------------------

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", **version_stamp(), "companies_root": str(store.root)}

    @app.get("/api/companies", tags=["companies"])
    def list_companies() -> list[dict]:
        return [
            {
                "company_id": ws.company_id, "name": ws.config.company.name,
                "ticker": ws.config.company.ticker, "exchange": ws.config.company.exchange,
                "sector": ws.config.company.sector, "stages": ws.stages(),
            }
            for ws in sorted(store, key=lambda w: w.company_id)
        ]

    @app.get("/api/companies/{company_id}", tags=["companies"])
    def overview(company_id: str) -> dict:
        return guarded(repo.overview_of, company(company_id))

    @app.get("/api/companies/{company_id}/documents", tags=["companies"])
    def documents(company_id: str) -> list[dict]:
        return guarded(repo.documents_of, company(company_id))

    # ---- analysis -------------------------------------------------------------------------

    @app.get("/api/companies/{company_id}/analytics", tags=["analysis"])
    def analytics(company_id: str) -> dict:
        return guarded(repo.analytics_of, company(company_id))

    @app.get("/api/companies/{company_id}/quality", tags=["analysis"])
    def quality(company_id: str) -> dict:
        return guarded(repo.quality_of, company(company_id))

    @app.get("/api/companies/{company_id}/charts", tags=["analysis"])
    def charts(company_id: str) -> list[dict]:
        return guarded(lambda ws: ws.chart_index(), company(company_id))

    @app.get("/api/companies/{company_id}/charts/{chart_id}.{fmt}", tags=["analysis"])
    def chart(company_id: str, chart_id: str, fmt: str):
        path = guarded(lambda ws: ws.chart_file(chart_id, fmt), company(company_id))
        media = "image/svg+xml" if fmt == "svg" else "image/png"
        return FileResponse(path, media_type=media)

    # ---- forecast -------------------------------------------------------------------------

    @app.get("/api/companies/{company_id}/forecast", tags=["forecast"])
    def forecast(company_id: str, scenario: Optional[str] = Query(default=None)) -> dict:
        data = guarded(repo.forecast_of, company(company_id))
        if scenario is not None:
            matched = [s for s in data["scenarios"] if s["id"] == scenario]
            if not matched:
                raise HTTPException(status_code=404, detail=f"no scenario {scenario!r}")
            data = {**data, "scenarios": matched}
        return data

    @app.get("/api/companies/{company_id}/assumptions", tags=["forecast"])
    def assumptions(company_id: str) -> dict:
        return guarded(repo.assumptions_of, company(company_id))

    # ---- lineage --------------------------------------------------------------------------

    @app.get("/api/companies/{company_id}/lineage/{node_id}", tags=["lineage"])
    def lineage(company_id: str, node_id: str) -> dict:
        return guarded(repo.trace, company(company_id), node_id)

    # ---- reports as written ---------------------------------------------------------------

    @app.get("/api/companies/{company_id}/reports/{name}", tags=["reports"],
             response_class=PlainTextResponse)
    def report(company_id: str, name: str) -> str:
        known = {
            "historical_analysis.md": "analyze",
            "forecast.md": "forecast",
            "extraction_report.md": "ingest",
            "sources.md": "ingest",
        }
        if name not in known:
            raise HTTPException(status_code=404, detail=f"no report {name!r}")
        return guarded(lambda ws: ws.text(name, known[name]), company(company_id))

    # ---- dashboard ------------------------------------------------------------------------

    if STATIC_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False, response_class=HTMLResponse)
        def index() -> str:
            return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return app


def serve(companies_root: Path, host: str = "127.0.0.1", port: int = 8000,
          reload: bool = False) -> None:
    _require_fastapi()
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - covered by _require_fastapi
        raise ResearchEngineError('the dashboard needs uvicorn: pip install -e ".[api]"') from None
    os.environ.setdefault("RESEARCH_ENGINE_COMPANIES", str(Path(companies_root).resolve()))
    uvicorn.run(create_app(companies_root), host=host, port=port, log_level="info")
