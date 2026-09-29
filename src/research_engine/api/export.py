"""Freeze the dashboard into static files, so it can be hosted where no Python runs.

The dashboard is a client of the read-only API. A static host such as GitHub Pages can serve files
but cannot run the API, so this module asks the API every question the dashboard can ask —
through the application itself, in-process — and writes each answer to the path the dashboard will
look for it at. The snapshot is therefore the API's own output rather than a second implementation
of it: a figure cannot differ between the live dashboard and the published one.

Three rules shape what is written:

- **Nothing local leaves the machine.** The live API is loopback-only precisely because it exposes
  workspace paths. Those fields are removed, and the export then refuses to finish if any absolute
  local path or e-mail address survives anywhere in the output. A refusal is recoverable; a
  published home directory is not.
- **A snapshot says it is one.** The page carries the time and engine version it was frozen at and
  tells the reader it will not update. A frozen page that looks live is a stale page that lies.
- **Refusals are exported too.** A stage that was not run answers 409 in the live API. Static files
  have no status codes, so the answer is written with its status inside it and the dashboard
  raises the same "stage not run" it would have raised live, instead of a generic fetch error.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import ResearchEngineError
from ..versioning import version_stamp
from .app import STATIC_DIR, create_app

# Fields that carry a location on the exporting machine. Removed rather than rewritten: a
# placeholder path would still invite the reader to look for a file that is not there.
LOCAL_FIELDS = frozenset({"companies_root", "raw_path", "local_source_path", "local_path", "workspace"})

_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:\\\\?(?:Users|Documents and Settings)\\|/home/|/Users/|/root/)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class ExportRefused(ResearchEngineError):
    """The snapshot would have published something from the local machine."""


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in LOCAL_FIELDS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _file_for(api_path: str, out: Path) -> Path:
    """`/api/companies/x/forecast` -> `<out>/api/companies/x/forecast.json`; images keep their name."""
    relative = api_path.lstrip("/")
    if relative.endswith((".svg", ".png")):
        return out / relative
    return out / f"{relative}.json"


class _Exporter:
    def __init__(self, companies_root: Path, out: Path):
        from fastapi.testclient import TestClient

        self.client = TestClient(create_app(companies_root))
        self.out = out
        self.written: list[Path] = []
        self.skipped: list[str] = []

    def json(self, api_path: str, *, required: bool = True) -> Any:
        response = self.client.get(api_path)
        if response.status_code == 200:
            body = _scrub(response.json())
        elif response.status_code == 409:
            body = {"__status": 409, "detail": _scrub(response.json().get("detail"))}
        elif not required:
            self.skipped.append(f"{api_path} ({response.status_code})")
            return None
        else:
            raise ResearchEngineError(f"export: {api_path} answered {response.status_code}: {response.text[:200]}")
        path = _file_for(api_path, self.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        self.written.append(path)
        return body if response.status_code == 200 else None

    def image(self, api_path: str) -> None:
        response = self.client.get(api_path)
        if response.status_code != 200:
            self.skipped.append(f"{api_path} ({response.status_code})")
            return
        path = _file_for(api_path, self.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        self.written.append(path)


def _traceable_ids(analytics: dict | None, forecast: dict | None, financials: dict | None = None) -> list[str]:
    """Exactly the values the dashboard renders as trace buttons — no more, so the site stays small."""
    ids: list[str] = []
    for statement in (financials or {}).get("statements", []):
        for row in statement.get("rows", []):
            ids += [v["fact_id"] for v in row.get("values", {}).values() if v.get("fact_id")]
    for series in (analytics or {}).get("series", []):
        ids += [p["value_id"] for p in series.get("points", []) if p.get("value_id")]
    for scenario in (forecast or {}).get("scenarios", []):
        for metric in scenario.get("metrics", []):
            ids += [p["value_id"] for p in metric.get("points", []) if p.get("value_id")]
    return list(dict.fromkeys(ids))


def _snapshot_index(generated_at: str, engine_version: str) -> str:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # Relative asset paths: a project site is served under /<repo>/, where "/static/…" would miss.
    html = html.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    marker = (f'<meta name="research-engine-snapshot" content="{generated_at}" '
              f'data-engine="{engine_version}">')
    return html.replace("</head>", f"{marker}\n</head>", 1)


def export_static(companies_root: Path, out: Path) -> dict[str, Any]:
    """Write a self-contained, read-only copy of the dashboard for every company to `out`."""
    companies_root = Path(companies_root).resolve()
    out = Path(out)
    staging = out.with_name(out.name + ".incoming")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    exporter = _Exporter(companies_root, staging)
    exporter.json("/api/health")
    companies = exporter.json("/api/companies") or []
    traces = 0
    for company in companies:
        base = f"/api/companies/{company['company_id']}"
        exporter.json(base)
        exporter.json(f"{base}/documents")
        exporter.json(f"{base}/glossary")
        financials = exporter.json(f"{base}/financials", required=False)
        analytics = exporter.json(f"{base}/analytics")
        exporter.json(f"{base}/quality")
        exporter.json(f"{base}/checks")
        exporter.json(f"{base}/assumptions")
        forecast = exporter.json(f"{base}/forecast")
        for chart in exporter.json(f"{base}/charts") or []:
            if chart.get("skipped"):
                continue
            exporter.json(f"{base}/charts/{chart['chart_id']}/data")
            exporter.image(f"{base}/charts/{chart['chart_id']}.svg")
        for chart in exporter.json(f"{base}/forecast/charts") or []:
            if chart.get("skipped"):
                continue
            exporter.json(f"{base}/forecast/charts/{chart['chart_id']}/data")
            exporter.image(f"{base}/forecast/charts/{chart['chart_id']}.svg")
        for node_id in _traceable_ids(analytics, forecast, financials):
            if exporter.json(f"{base}/lineage/{node_id}", required=False) is not None:
                traces += 1

    # Refuse before anything reaches `out`: a leaked path is only recoverable while it is local.
    # The workspace's own location is checked exactly, in both raw and JSON-escaped form, so the
    # guard does not depend on the home-directory patterns matching this machine's layout.
    root_forms = {str(companies_root), json.dumps(str(companies_root))[1:-1]}
    leaks = []
    for path in exporter.written:
        if path.suffix != ".json":
            continue
        text = path.read_text(encoding="utf-8")
        hit = next((f"the workspace path ({form!r})" for form in root_forms if form in text), None)
        if hit is None:
            for pattern, what in ((_ABSOLUTE_PATH, "a local file path"), (_EMAIL, "an e-mail address")):
                found = pattern.search(text)
                if found:
                    hit = f"{what} ({found.group(0)!r})"
                    break
        if hit:
            leaks.append(f"{path.relative_to(staging)}: {hit}")
    if leaks:
        shutil.rmtree(staging)
        raise ExportRefused("export refused, nothing was written — the snapshot would publish:\n  "
                            + "\n  ".join(leaks[:10]))

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    engine_version = version_stamp()["engine_version"]
    shutil.copytree(STATIC_DIR, staging / "static", ignore=shutil.ignore_patterns("index.html"))
    (staging / "index.html").write_text(_snapshot_index(generated_at, engine_version), encoding="utf-8")
    (staging / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is, no site generator

    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)
    files = [p for p in out.rglob("*") if p.is_file()]
    return {
        "out": str(out), "generated_at": generated_at, "engine_version": engine_version,
        "companies": [c["company_id"] for c in companies], "files": len(files),
        "bytes": sum(p.stat().st_size for p in files), "lineage_traces": traces,
        "skipped": exporter.skipped,
    }
