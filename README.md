# Equity Research Engine

A company-agnostic engine that turns primary company disclosures into reproducible, auditable equity research.
The company is an **input** (a YAML file). The engine is the product.

> **Status: Phase 5 of 8 — forecast.** SEC XBRL data is retrieved, identity-checked, completed with derived
> facts, tested against accounting identities, turned into framework-defined analytics and charts, and now
> projected forward through the framework's driver graph under an assumption registry and named scenarios.
> Every projected figure traces to the assumption behind it and on to a filed document, and a read-only
> dashboard makes that trace clickable. Valuation and report rendering are **not built yet**.
> `make research` fails loudly until they are.
>
> All tests run on **synthetic** SEC-format data. The engine has not yet been run against a live filing.

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
| Historical analytics | `analysis/engine.py`, `industry_frameworks/*.yaml` | Growth, margins, returns, leverage, efficiency, operating leverage defined as data; balance-sheet denominators use an explicit basis (average / opening); facts failing error checks are excluded, warnings carried as flags |
| Charts and summaries | `analysis/charts.py`, `analysis/summary.py` | Framework-defined SVG/PNG charts marking derived and flagged points; descriptive statistics labelled as model output |
| Driver graph | `forecast/drivers.py`, `industry_frameworks/*.yaml` | Framework drivers resolved into an evaluation plan; derivations re-applied forward; derivation/driver cycles broken by demotion, never silently |
| Assumption registry | `forecast/assumptions.py`, `schemas/assumption.py` | Engine seeds `historical` assumptions from verified facts with lineage; analyst supplies guidance/consensus/judgement; guidance needs a cited document and analysts cannot claim `historical` |
| Scenario forecast | `forecast/engine.py` | Fixed precedence ladder, per-year overrides, independent scenario projections; unset assumptions refuse to project rather than fall back |
| Read-only API | `api/repository.py`, `api/app.py` | Serves only what the pipeline wrote; a stage that has not run returns 409 with the command to run, never an empty result that looks like a clean bill of health |
| Dashboard | `api/static/` | Overview, analytics, scenario forecast, assumption ladder and quality report, with any figure clickable through to the filing it came from. No build step, no JS dependencies |
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
make analyze CONFIG=companies/nyse-jpm/config.yaml                  # historical analytics, charts, Parquet
make forecast CONFIG=companies/nyse-jpm/config.yaml                 # driver graph, assumptions, scenarios
make install-api && make serve                                      # dashboard at http://127.0.0.1:8000
make data CONFIG=companies/nyse-jpm/config.yaml                     # ingest + quality + analyze + forecast
```

Assumptions are optional. With no `companies/<id>/assumptions.yaml`, the forecast runs on history the engine
seeded itself and says so. To supply guidance, consensus, your own judgement or scenarios, copy
`companies/nyse-jpm/assumptions.example.yaml` to `assumptions.yaml` and edit it; run `make forecast` once first
and read `output/assumptions.json` to see exactly which assumption keys the framework asks for.

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

`make analyze` adds:

| File | Contents |
|---|---|
| `historical_financials.parquet` | Current reported and derived facts, long format, with lineage columns |
| `historical_analytics.parquet`, `historical_analytics.csv` | Analytic values by fiscal year with basis, formula, quality flags and input ids |
| `historical_summary.json` | Descriptive statistics per analytic, counted reasons for every value not computed, chart records |
| `historical_analysis.md` | Tables by category (growth, profitability, returns, ...), labelled MODEL OUTPUT, no interpretation |
| `charts/*.svg`, `charts/*.png`, `charts/index.json` | Framework-defined charts; hollow markers / hatched bars = derived, † = flagged input |
| `analysis_manifest.json` | Versions, framework hash, hashes of the ingestion and quality manifests used |

`make forecast` adds:

| File | Contents |
|---|---|
| `forecast.parquet`, `forecast.csv` | Projected values by scenario, metric and fiscal year, with the method, the assumption ids and types behind each figure, and lineage to prior values and base-year facts |
| `assumptions.json` | Every assumption in force with its rung and provenance, the full projection plan, assumptions history could not seed, demoted derivations, and every metric-year not projected with its reason |
| `forecast.md` | Projection plan, scenario tables, assumptions in force, and everything refused — labelled MODEL OUTPUT, no interpretation |
| `forecast_manifest.json` | Versions, framework hash, hash of the analysis manifest and of the assumptions file used |

## Dashboard

```bash
make install-api          # FastAPI and uvicorn are optional extras; the pipeline does not need them
make serve                # http://127.0.0.1:8000, reads every company under companies/
make serve COMPANIES=/path/to/companies PORT=8100
```

Five views over whatever the pipeline has written: **Overview** (entity, framework and the evidence
that chose it, document hashes), **Historical** (analytics by category with the framework's charts),
**Forecast** (per-scenario projections with the method and assumption rung behind each figure),
**Assumptions** (the resolution ladder and each value's provenance) and **Data quality** (checks,
issues, and what was not evaluable).

Every number carrying a lineage id is clickable and opens its full chain — model output to fact to
document to source URL, with XBRL concept, filing accession and filed date. The API computes
nothing: `GET /api/companies/<id>/analytics` returns exactly the rows in
`historical_analytics.parquet`, which is what makes the dashboard checkable against the files an
analyst would open directly. A stage that has not been run returns HTTP 409 naming the command to
run, because a dashboard reporting zero data-quality issues for a company that was never checked is
worse than one that shows nothing.

The server has **no authentication** and binds to localhost. It is local analyst tooling; do not
expose it.

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
4. **Historical analysis** — framework-defined analytics with explicit balance bases, quality gating, descriptive statistics, charts, Parquet ✅
5. **Forecast** — driver graph evaluation, assumption registry, scenarios ✅
6. **Valuation + reverse valuation** — DCF/FCFF, residual income, multiples; solve for market-implied drivers
7. **Research output** — HTML/PDF report; thesis, catalysts, falsifiers from analyst YAML, quantified by the engine
8. **Second contrasting company end to end** (proves agnosticism with output, not just tests)

Dashboard: built (see below), read-only over whatever the pipeline has written.

Later: PDF/HTML KPI extraction, ESEF adapter, event studies.

## Limitations

- Structured ingestion covers SEC filers only (US domestic plus 20-F/40-F filers that tag in XBRL). ESEF is not implemented.
- Balance-sheet identity failures are warnings, not errors: equity tags excluding non-controlling interests break it legitimately. Adding an NCI metric would let the check separate real errors from that pattern.
- The rounding tolerance infers presentation units from trailing zeros. Filers that tag unrounded values get a tight tolerance; that is conservative but can flag trivially small differences.
- Analysis is annual only. Quarterly and trailing-twelve-month analytics are not implemented.
- Segment analysis is not available: SEC companyfacts has no segment dimensions; it needs XBRL instance documents.
- Cyclicality is described by the company's own growth volatility and drawdowns, not measured against macro data (no FRED adapter yet).
- ROIC depends on `total_debt`, whose XBRL tag coverage varies widely by filer.
- Outlier and scale rules need history: fewer than six fiscal years makes the outlier check "not evaluable".
- Framework XBRL concept lists are candidates. Each run's extraction report shows which tagged metrics had no data for that filer.
- Tests use synthetic SEC-format fixtures. The format follows SEC documentation, but a live run against a real filer is the first check against production payloads.
- Four frameworks ship (generic, banks, software, industrials). Insurance, asset managers, REITs, energy, etc. are not written.
- No consensus data source. Consensus will only appear if a user supplies a document for it.
- Forecast metrics with no driver formula and no derivation are projected by a trailing-median growth rate. That is
  the weakest link in any output here; `forecast.md` lists every metric produced this way. The fix is framework
  data — more derivations and driver formulas — not engine code.
- Forecasts are annual and deterministic. There is no interim forecast, no Monte Carlo, and no sensitivity grid;
  scenarios are discrete and analyst-declared.
- A driver formula cannot reference a prior-period value, so working-capital roll-forwards and balance-sheet
  closing identities (`equity[t] = equity[t-1] + net_income - dividends`) cannot yet be expressed as framework data.
- Guidance must be transcribed by hand from a registered document, because PDF extraction is not implemented. The
  document id makes the citation checkable; it does not make the number automatic.
- The dashboard is read-only and has no authentication. It cannot run the pipeline, edit assumptions or write
  anything; those are CLI operations, deliberately, so that every change to a company's data is a recorded command.
- The dashboard renders the charts the engine already produced rather than re-plotting in the browser, so provenance
  marks (hollow = derived, † = flagged) survive. Forecast scenarios have no charts yet, only tables.

## Disclaimer

Research tooling, not investment advice.
