# Equity Research Engine

A company-agnostic engine that turns primary company disclosures into reproducible, auditable equity research.
The company is an **input** (a YAML file). The engine is the product.

> **Status: Phase 3 of 8 — data quality.** SEC XBRL data is retrieved, identity-checked, mapped onto industry
> frameworks, completed with derived periods and metrics (each with lineage), and tested against accounting
> identities and time-series rules. Historical analysis, forecasting, valuation and reporting are **not built yet**.
> `make research` fails loudly until they are.

## What works today

| Capability | Where | Guarantee |
|---|---|---|
| Company config validation | `schemas/company.py`, `config/loader.py` | Typos rejected, https-only URLs, SSRF guard, blank placeholders tolerated |
| Canonical financial facts | `schemas/financial.py` | Reported facts **must** cite a page/table/XBRL concept; derived facts **must** cite input facts and a formula |
| Assumption registry schema | `schemas/assumption.py` | Consensus and guidance cannot exist without a cited document |
| Industry frameworks as data | `industry_frameworks/*.yaml` | Inheritance, removals, reference and cycle checks, valuation-method compatibility |
| Document registry | `registry/document_registry.py` | Content-addressed, read-only raw store; never overwrites; enforced status machine |
| Lineage graph | `lineage/graph.py` | Report element → chart → model → fact → document → source URL; broken lineage detectable |
| SEC ingestion | `sources/sec.py`, `ingestion/http.py`, `pipeline/ingest.py` | Cached, versioned downloads; CIK/ticker/fiscal-year-end checked against SEC before any fact is trusted |
| Fiscal calendar | `calendar.py` | Periods derived from start/end dates (never SEC's filing-level `fy`/`fp`); 52/53-week years handled |
| XBRL extraction | `extraction/xbrl.py` | Framework decides concepts; comparatives deduplicated; restatements kept side by side; every skip counted |
| Derived facts | `quality/derive.py` | Q2/Q3/Q4/H2 from year-to-date values; framework formulas fill gaps; reported values always win and are cross-checked |
| Data-quality checks | `quality/checks.py`, `industry_frameworks/*.yaml` | Accounting identities as framework data; sign, scale-break, outlier and gap rules with stated thresholds; missing inputs reported as "not evaluable", never as passes |
| Company-agnostic guard | `tests/test_company_agnostic.py` | CI fails if any configured company's ticker/name/CIK appears in engine code or frameworks |

## Quick start

```bash
make install
make test
make frameworks
make validate CONFIG=companies/nyse-jpm/config.yaml
cp .env.example .env   # set SEC_USER_AGENT="Your Name you@example.com"
make ingest CONFIG=companies/nyse-jpm/config.yaml
make ingest CONFIG=companies/nyse-jpm/config.yaml ARGS=--refresh   # re-download; changed filings become new versions
make quality CONFIG=companies/nyse-jpm/config.yaml                  # QARGS=--strict to fail on error-severity issues
make data CONFIG=companies/nyse-jpm/config.yaml                     # ingest + quality
```

`make ingest` writes to `companies/<id>/output/`:

| File | Contents |
|---|---|
| `facts.jsonl` | Every extracted fact version with full lineage (concept, accession, form, filed date, document id) |
| `facts_current.csv` | One value per metric/period: the latest filed disclosure, with lineage columns |
| `historical_annual.csv`, `historical_interim.csv` | Wide views for inspection |
| `extraction_report.md/.json` | Coverage by metric, restatements, concept switches, gaps, skip reasons |
| `lineage.json` | Fact → document → source URL graph |
| `sources.md`, `manifest.json` | Document hashes and versions; engine/parser/config versions for reproducibility |

`make quality` adds:

| File | Contents |
|---|---|
| `data_quality_report.html/.json` | Issues by severity, check pass/fail/not-evaluable counts, annual coverage matrix (reported vs derived), methodology |
| `facts_derived.jsonl` | Derived facts with formula and input fact ids |
| `financials_long.csv` | Current reported + derived values with provenance, concept or formula, and lineage ids |
| `financials_annual.csv`, `financials_interim.csv` | Wide views including derived periods and metrics |
| `quality_manifest.json` | Versions, issue counts, hash of the ingestion manifest it was built from |

Add a company: create `companies/<exchange>-<ticker>/config.yaml`. No code changes.

## Design in one paragraph

Numbers come from **structured filings first** (SEC XBRL for US filers, ESEF/iXBRL for EU/UK), with PDF/HTML
extraction reserved for operating KPIs that have no standard tag. Every value is an immutable fact with lineage;
restatements coexist rather than overwrite. Industry frameworks declare which metrics matter, how they are derived,
which drivers generate them, and which valuation methods are valid, so a bank never gets an EV/EBITDA and a
software company never gets a P/TBV. The engine structures and quantifies an investment thesis
(market-implied assumptions vs. model assumptions, catalysts, falsifiers); it does not invent one.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/adr/](docs/adr/).

## Roadmap

1. **Foundation** — config, schemas, frameworks, registry, lineage ✅
2. **Structured ingestion** — SEC companyfacts/submissions adapters, cached downloader, SIC framework selection, XBRL → canonical facts ✅
3. **Data quality** — interim and framework derivations with lineage, accounting identities, sign/scale/outlier/gap checks, `data_quality_report.html` ✅
4. **Historical analysis** — framework-driven ratios, charts, reconciliation flags
5. **Forecast** — driver graph evaluation, assumption registry, scenarios
6. **Valuation + reverse valuation** — DCF/FCFF, residual income, multiples; solve for market-implied drivers
7. **Research output** — HTML/PDF report; thesis, catalysts, falsifiers from analyst YAML, quantified by the engine
8. **Second contrasting company end to end** (proves agnosticism with output, not just tests)

Later: PDF/HTML KPI extraction, ESEF adapter, dashboard, event studies.

## Limitations

- Structured ingestion covers SEC filers only (US domestic plus 20-F/40-F filers that tag in XBRL). ESEF is not implemented.
- Balance-sheet identity failures are warnings, not errors: equity tags excluding non-controlling interests break it legitimately. Adding an NCI metric would let the check separate real errors from that pattern.
- The rounding tolerance infers presentation units from trailing zeros. Filers that tag unrounded values get a tight tolerance; that is conservative but can flag trivially small differences.
- Derived ratios use period-end balances, not averages (e.g. ROE = net income / closing equity). Averaging belongs to historical analysis.
- Outlier and scale rules need history: fewer than six fiscal years makes the outlier check "not evaluable".
- Framework XBRL concept lists are candidates. Each run's extraction report shows which tagged metrics had no data for that filer.
- Tests use synthetic SEC-format fixtures. The format follows SEC documentation, but a live run against a real filer is the first check against production payloads.
- Four frameworks ship (generic, banks, software, industrials). Insurance, asset managers, REITs, energy, etc. are not written.
- No consensus data source. Consensus will only appear if a user supplies a document for it.

## Disclaimer

Research tooling, not investment advice.
