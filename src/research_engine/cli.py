from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from .config import load_project_config
from .errors import ResearchEngineError
from .frameworks import FrameworkRegistry
from .ingestion import HttpFetcher, load_env_file
from .ingestion.http import require_contact_user_agent
from .pipeline import run_ingestion
from .pipeline.ingest import requires_sec_access
from .pipeline.analysis import run_analysis_stage
from .pipeline.forecast import run_forecast_stage
from .pipeline.quality import run_quality_stage
from .versioning import version_stamp


def _default_frameworks() -> Path:
    return Path(os.environ.get("RESEARCH_ENGINE_FRAMEWORKS", "industry_frameworks"))


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.config)
        registry = FrameworkRegistry(args.frameworks)
        resolved = registry.validate_all()
    except ResearchEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    company = config.company
    print(f"config OK: {args.config}")
    print(f"  company_id:  {config.company_id}")
    print(f"  company:     {company.name} ({company.ticker}, {company.exchange}, {company.country})")
    print("  sources:")
    for doc_type, count in sorted(Counter(t.value for t, _ in config.sources.iter_documents()).items()):
        print(f"    {doc_type}: {count}")
    print(f"  frameworks:  {len(resolved)} valid ({', '.join(sorted(resolved))})")

    if company.industry_framework:
        framework = resolved.get(company.industry_framework)
        if framework is None:
            print(f"error: industry_framework {company.industry_framework!r} not found", file=sys.stderr)
            return 1
        methods = framework.valuation.methods_for(config.research.valuation_methods)
        print(f"  framework:   {framework.name} (explicit override)")
        print(f"  valuation:   {', '.join(m.value for m in methods) or 'none for requested families'}")
    else:
        print("  framework:   not set; will be classified from filing evidence")

    warnings = []
    if not (config.sources.structured_filings or config.sources.annual_reports):
        warnings.append("no structured filings or annual reports; historical coverage will be thin")
    if not (company.identifiers.cik or company.identifiers.lei):
        warnings.append("no CIK or LEI; structured regulatory data cannot be located automatically")
    for w in warnings:
        print(f"  warning:     {w}")
    return 0


def _cmd_frameworks(args: argparse.Namespace) -> int:
    try:
        registry = FrameworkRegistry(args.frameworks)
        resolved = registry.validate_all()
    except ResearchEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for name, fw in resolved.items():
        parent = registry.raw(name).extends or "-"
        methods = ", ".join(m.value for m in fw.valuation.preferred)
        print(f"{name:<14} extends={parent:<10} metrics={len(fw.metrics):<3} drivers={len(fw.drivers):<2} valuation=[{methods}]")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    try:
        if args.env_file:
            load_env_file(args.env_file)
        config = load_project_config(args.config)
        registry = FrameworkRegistry(args.frameworks)
        fetcher = None
        if not args.offline:
            ua = os.environ.get("SEC_USER_AGENT")
            if requires_sec_access(config):
                ua = require_contact_user_agent(ua)
            fetcher = HttpFetcher(ua or "research-engine")
        workspace = args.workspace or args.config.resolve().parent
        result = run_ingestion(config, workspace=workspace, frameworks=registry, fetcher=fetcher,
                               refresh=args.refresh, offline=args.offline)
    except ResearchEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"ingested {result.company_id} -> {result.output_dir}")
    for o in result.outcomes:
        detail = f" ({o.detail})" if o.detail else ""
        print(f"  [{o.action:<11}] {o.record.document_type.value:<24} {o.record.status.value:<10}{detail}")
    if result.profile:
        print(f"  entity:     {result.profile.name} CIK {result.profile.cik} SIC {result.profile.sic}")
    if result.framework:
        print(f"  framework:  {result.framework.name} via {result.framework.method} ({result.framework.evidence})")
    if result.extraction:
        r = result.extraction
        print(f"  facts:      {r.facts_emitted} from {r.observations_mapped}/{r.observations_total} mapped observations")
        print(f"  gaps:       {len(r.metrics_without_data)} tagged metrics without data, "
              f"{len(r.metrics_requiring_documents)} need document extraction")
    for w in result.warnings:
        print(f"  warning:    {w}")
    return 3 if result.failures else 0


def _cmd_quality(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.config)
        registry = FrameworkRegistry(args.frameworks)
        workspace = args.workspace or args.config.resolve().parent
        result = run_quality_stage(config, workspace=workspace, frameworks=registry)
    except ResearchEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    counts = result.report.counts()
    print(f"data quality for {config.company_id} -> {result.output_dir / 'data_quality_report.html'}")
    print(f"  facts:   {len(result.current)} current reported, {len(result.derived)} derived")
    print(f"  issues:  {counts['error']} errors, {counts['warning']} warnings, {counts['info']} info")
    for c in sorted(result.report.checks.values(), key=lambda c: c.check):
        if c.failed:
            print(f"  failed:  {c.check} ({c.failed} of {c.passed + c.failed} evaluable; {c.not_evaluable} not evaluable)")
    return 4 if args.strict and counts["error"] else 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.config)
        registry = FrameworkRegistry(args.frameworks)
        workspace = args.workspace or args.config.resolve().parent
        result = run_analysis_stage(config, workspace=workspace, frameworks=registry)
    except ResearchEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    rendered = [c for c in result.charts if not c.skipped]
    print(f"historical analysis for {config.company_id} -> {result.output_dir / 'historical_analysis.md'}")
    print(f"  analytics: {len(result.analysis.values)} values across {len(result.summaries)} analytics, "
          f"FY{result.analysis.fiscal_years[0] if result.analysis.fiscal_years else '-'}"
          f"-FY{result.analysis.fiscal_years[-1] if result.analysis.fiscal_years else '-'}")
    print(f"  not computed: {sum(result.analysis.not_computed.values())} analytic-years (reasons in historical_summary.json)")
    print(f"  charts:    {len(rendered)} rendered, {len(result.charts) - len(rendered)} skipped")
    return 0


def _cmd_forecast(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.config)
        registry = FrameworkRegistry(args.frameworks)
        workspace = args.workspace or args.config.resolve().parent
        result = run_forecast_stage(config, workspace=workspace, frameworks=registry)
    except ResearchEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    f = result.forecast
    years = list(f.forecast_years)
    print(f"forecast for {config.company_id} -> {result.output_dir / 'forecast.md'}")
    print(f"  horizon:   base FY{f.base_year}, projecting FY{years[0]}-FY{years[-1]}" if years else "  horizon:   none")
    print(f"  scenarios: {', '.join(f.by_scenario)}")
    print(f"  plan:      {len(f.graph.rules)} metrics; "
          f"{sum(1 for r in f.graph.rules.values() if r.is_exogenous)} exogenous, "
          f"{sum(1 for r in f.graph.rules.values() if not r.is_exogenous)} computed")
    seeded, analyst = len(f.seeded), len(f.assumptions.all) - len(f.seeded)
    print(f"  assumptions: {seeded} seeded from history, {analyst} analyst-supplied, {len(f.unseeded)} unseeded")
    if not result.assumptions_file_present:
        print(f"  note:      no {result.assumptions_path.name}; forecasting on seeded history alone "
              "(no guidance, consensus or scenarios)")
    print(f"  values:    {len(f.values)} projected; "
          f"{sum(f.not_projected.values())} metric-years not projected (reasons in assumptions.json)")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .api import serve
    try:
        companies = args.companies.resolve()
        if not companies.is_dir():
            raise ResearchEngineError(f"{companies} is not a directory")
        print(f"serving {companies} on http://{args.host}:{args.port}")
        print("  read-only dashboard over pipeline outputs; no authentication, do not expose it")
        serve(companies, host=args.host, port=args.port)
    except ResearchEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    print(
        "error: the full research pipeline is not implemented yet. Implemented stages: `validate`, `ingest`, "
        "`quality`, `analyze`, `forecast`. Valuation and report rendering are not built.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-engine", description="Company-agnostic equity research engine")
    parser.add_argument("--version", action="version", version=str(version_stamp()))
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a company config and all industry frameworks")
    p_validate.add_argument("--config", required=True, type=Path)
    p_validate.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_validate.set_defaults(func=_cmd_validate)

    p_fw = sub.add_parser("frameworks", help="list and validate industry frameworks")
    p_fw.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_fw.set_defaults(func=_cmd_frameworks)

    p_ingest = sub.add_parser("ingest", help="retrieve sources, verify identity, extract XBRL facts with lineage")
    p_ingest.add_argument("--config", required=True, type=Path)
    p_ingest.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_ingest.add_argument("--workspace", type=Path, default=None, help="defaults to the config file's directory")
    p_ingest.add_argument("--refresh", action="store_true", help="re-download sources; changed content becomes a new version")
    p_ingest.add_argument("--offline", action="store_true", help="use cached documents only")
    p_ingest.add_argument("--env-file", type=Path, default=Path(".env"))
    p_ingest.set_defaults(func=_cmd_ingest)

    p_quality = sub.add_parser("quality", help="derive interim/framework metrics and run data-quality checks")
    p_quality.add_argument("--config", required=True, type=Path)
    p_quality.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_quality.add_argument("--workspace", type=Path, default=None, help="defaults to the config file's directory")
    p_quality.add_argument("--strict", action="store_true", help="exit with status 4 if any error-severity issue is found")
    p_quality.set_defaults(func=_cmd_quality)

    p_analyze = sub.add_parser("analyze", help="historical analytics, summaries, charts and Parquet datasets")
    p_analyze.add_argument("--config", required=True, type=Path)
    p_analyze.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_analyze.add_argument("--workspace", type=Path, default=None, help="defaults to the config file's directory")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_forecast = sub.add_parser("forecast", help="project the driver graph over scenarios from the assumption registry")
    p_forecast.add_argument("--config", required=True, type=Path)
    p_forecast.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_forecast.add_argument("--workspace", type=Path, default=None, help="defaults to the config file's directory")
    p_forecast.set_defaults(func=_cmd_forecast)

    p_serve = sub.add_parser("serve", help="read-only dashboard and API over pipeline outputs")
    p_serve.add_argument("--companies", type=Path, default=Path("companies"),
                         help="directory containing <company>/config.yaml (default: companies)")
    p_serve.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1; local tooling only")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=_cmd_serve)

    p_research = sub.add_parser("research", help="run the full pipeline (not yet implemented)")
    p_research.add_argument("--config", required=True, type=Path)
    p_research.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_research.set_defaults(func=_cmd_research)

    args = parser.parse_args(argv)
    return args.func(args)
