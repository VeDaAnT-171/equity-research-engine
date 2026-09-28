"""Enforces the core architectural requirement: companies are inputs, never code."""

import re
import textwrap

from research_engine.cli import main
from research_engine.config import load_project_config


def _company_configs(root):
    return sorted((root / "companies").glob("*/config.yaml")) + sorted((root / "tests" / "fixtures" / "companies").glob("*.yaml"))


def _brand(name: str) -> str | None:
    """The coined first word of a company name, if it has one — `JPMorgan`, `PayPal`, `BlackRock`.

    Matching the full legal name alone let "JPMorgan's" into engine comments unnoticed. A coined
    word (a capital after its first letter, or a digit) is distinctive enough to search for on its
    own; an ordinary first word ("Example", "General") is not, and searching for it would fail on
    every docstring. This still misses plain-word brands such as "Goldman"; it narrows the gap,
    it does not close it.
    """
    first = re.split(r"[\s,&.]+", name.strip())[0]
    return first if len(first) >= 4 and re.search(r".[A-Z0-9]", first) else None


def _identifiers(root):
    for path in _company_configs(root):
        cfg = load_project_config(path)
        ids = cfg.company.identifiers
        candidates = (cfg.company.ticker, cfg.company.name, _brand(cfg.company.name), ids.cik, ids.lei, ids.isin)
        yield path, [v for v in candidates if v]


def test_there_is_at_least_one_company_config(root):
    assert list((root / "companies").glob("*/config.yaml"))


def test_no_company_identifiers_in_engine_code_or_frameworks(root):
    engine_files = list((root / "src").rglob("*.py")) + list((root / "industry_frameworks").glob("*.yaml"))
    texts = {p: p.read_text(encoding="utf-8") for p in engine_files}
    offenders = []
    for cfg_path, identifiers in _identifiers(root):
        for ident in identifiers:
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(ident)}(?![A-Za-z0-9])", re.IGNORECASE)
            offenders += [f"{p.relative_to(root)} contains {ident!r} (from {cfg_path.name})"
                          for p, text in texts.items() if pattern.search(text)]
    assert not offenders, "\n".join(offenders)


def test_no_branching_on_company_identity(root):
    pattern = re.compile(r"\b(ticker|company_name|company\.name|cik)\s*(==|!=|in)\s*[\"'\[(]")
    hits = [f"{p.relative_to(root)}:{i}" for p in (root / "src").rglob("*.py")
            for i, line in enumerate(p.read_text().splitlines(), 1) if pattern.search(line)]
    assert not hits, hits


def test_new_company_needs_only_a_config(tmp_path, frameworks_dir, capsys):
    cfg = tmp_path / "brand_new.yaml"
    cfg.write_text(textwrap.dedent("""
        company:
          name: Brand New Holdings plc
          ticker: BNH
          exchange: LSE
          country: United Kingdom
          industry_framework: software
          identifiers: {isin: GB00B03MLX29}
        sources:
          annual_reports:
            - url: https://example.org/bnh-annual-report.pdf
    """))
    assert main(["validate", "--config", str(cfg), "--frameworks", str(frameworks_dir)]) == 0
    out = capsys.readouterr().out
    assert "lse-bnh" in out and "software (explicit override)" in out


def test_research_command_fails_loudly(root, frameworks_dir, capsys):
    cfg = next(iter(_company_configs(root)))
    assert main(["research", "--config", str(cfg), "--frameworks", str(frameworks_dir)]) == 2
    assert "not implemented" in capsys.readouterr().err
