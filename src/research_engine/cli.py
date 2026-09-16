from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from .errors import ResearchEngineError
from .config import load_project_config
from .frameworks import FrameworkRegistry
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


def _cmd_research(args: argparse.Namespace) -> int:
    print(
        "error: the research pipeline is not implemented yet. Phase 1 provides configuration, schemas, "
        "industry frameworks, the document registry and lineage. Use `validate`.",
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

    p_research = sub.add_parser("research", help="run the full pipeline (not yet implemented)")
    p_research.add_argument("--config", required=True, type=Path)
    p_research.add_argument("--frameworks", type=Path, default=_default_frameworks())
    p_research.set_defaults(func=_cmd_research)

    args = parser.parse_args(argv)
    return args.func(args)
