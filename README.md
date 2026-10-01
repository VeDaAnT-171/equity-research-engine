# Equity Research Engine

[![CI](https://github.com/VeDaAnT-171/equity-research-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/VeDaAnT-171/equity-research-engine/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)

A company-agnostic engine that turns primary company disclosures into reproducible, auditable
equity research. **The company is an input — a YAML file. The engine is the product.**

Add a company by writing `companies/<exchange>-<ticker>/config.yaml`. There is no code to change,
and CI fails if any configured company's name, ticker or CIK appears anywhere in the engine.

**[Open the dashboard →](https://vedaant-171.github.io/equity-research-engine/)** Rebuilt every
week from the latest SEC filings (JPMorgan Chase, FY2007 onward): the whole pipeline re-runs on a
clean machine and republishes only if the tests and the data-quality gate pass. Every figure opens
its lineage back to the filing it came from; the page shows which run it was built from.

**[Search any company →](https://equity-research-engine-d7fs.onrender.com/)** The hosted app:
type a company name or ticker and it is analysed from its SEC filings on demand; add an annual
report or presentation in its Sources tab and it is checked and used. Free hosting: after 15
minutes idle the first visit takes about a minute to wake, and documents added there last until
the next restart.

```
SEC XBRL  →  canonical facts  →  quality checks  →  analytics  →  forecast  →  dashboard
             (every one cites      (missing is        (model        (assumptions   (every figure
              its source)           never a pass)      output)       are cited)     is traceable)
```

> ### Status: pre-1.0, phase 5 of 8
>
> **Built:** ingestion, data quality, historical analytics, driver-graph forecast with scenarios,
> and a read-only API and dashboard. Every projected figure traces to the assumption behind it and
> on to a filed document.
>
> **Not built:** valuation, the research report, and a second contrasting company taken end to
> end. `make research` fails loudly rather than producing a partial report.
>
> **Run against one live filer, not validated across many.** The full pipeline has been run on
> JPMorgan Chase's SEC filings (FY2007–FY2025). The revenue bridge, pre-tax bridge and EPS identity
> reconcile to the cent in the years checked; the balance-sheet identity misses in two annual
> periods, which the quality stage flags rather than hides. The run also found defects the
> synthetic fixtures could not reach — most now fixed, some still open (see the changelog). One
> bank is not a sample: no other company, and no non-bank framework, has been run on live data.
> See [DISCLAIMER.md](DISCLAIMER.md) before relying on any output.
>
> **The bank forecast is partly a trend.** Net interest income is modelled as earning assets times
> margin, and average earning assets is not in XBRL, so until document extraction exists NII falls
> back to its own three-year trend. Every figure that depends on it is marked `‡`.

## Why it is built this way

Most research tooling fails quietly: a missing value becomes a zero, a stale figure becomes a
trend, a check with no data looks like a check that passed. This engine is built so that the
failure modes are loud and the successes are checkable.

- **Nothing is computed twice.** The API serves what the pipeline wrote; the dashboard shows what
  the API served. One definition of every ratio, in one place.
- **Missing is never fine.** A check without data is "not evaluable". A metric the forecast cannot
  derive is refused with a counted reason. A pipeline stage that has not run returns a 409, not an
  empty page.
- **Provenance is structural, not documentary.** A reported fact that cites no source cannot be
  constructed. Management guidance without a cited document is rejected by the type system.
- **Industry knowledge is data.** Metrics, derivations, drivers, checks, charts and valuation
  policy live in YAML, so a bank never gets an EV/EBITDA and a software company never gets a P/TBV.

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
| Forecast charts | `forecast/charts.py` | Solid to the last reported year, dashed after it; scenarios separated by line style and an end label, never by hue alone; a metric with no projection is skipped rather than drawn flat |
| Dashboard | `api/static/` | Summary, financial statements, ratios, estimates and data checks, with any figure clickable through to the filing it came from. Keyboard-operable throughout, every chart has a data table, no hover-only information. No build step, no JS dependencies |
| Document library | `documents/`, `pipeline/ingest.py` | Reports, supplements and presentations added per company; inline XBRL and table reading; a document is used only after it agrees with the SEC on the figures both contain, and only to fill figures the SEC data lacks |
| Company-agnostic guard | `tests/test_company_agnostic.py` | CI fails if any configured company's ticker/name/CIK appears in engine code or frameworks |

## Quick start

```bash
make install-api     # engine + dashboard; `make install` omits the dashboard extras
make check           # tests, lint, types — the same gate CI runs
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

Five sections per company, written for a reader rather than for the pipeline: **Summary** (headline
figures with year-on-year change, key charts, the first years of the estimates), **Financials**
(income statement, balance sheet and cash flow statement in millions, as filed), **Ratios**
(grouped by growth, profitability, returns and so on, with charts and a plain-language list of what
could not be calculated and why), **Estimates** (projections, the assumptions behind them and what
was not estimated) and **Data checks** (each check in words, with every finding explained against
the figures involved).

Engine vocabulary stays out of the page. Metric ids, refusal codes and assumption keys are turned
into names and sentences by one module, `presentation.py`, which the charts, the API and the
dashboard all use — so "input_missing:loans" reads the same everywhere: *No figure for loans is
reported in those years.* The raw forms remain available from the API for anyone auditing it
(`/quality` is served verbatim; `/checks` is the same report in words).

Every figure is clickable and opens *Where this number comes from*: how it is calculated (in words),
the assumptions and earlier estimates it rests on, and each reported figure used, with the form,
filing date, XBRL tag and a link to the filing on EDGAR. The API computes
nothing: `GET /api/companies/<id>/analytics` returns exactly the rows in
`historical_analytics.parquet`, which is what makes the dashboard checkable against the files an
analyst would open directly. A stage that has not been run returns HTTP 409 naming the command to
run, because a dashboard reporting zero data-quality issues for a company that was never checked is
worse than one that shows nothing.

### Accessibility

The dashboard is built against WCAG 2.1 AA and verified with axe-core in CI-style browser runs
(zero violations across all five views), plus a keyboard-only walkthrough:

- Every control is a real button, link or select, reachable and operable from the keyboard. The
  tablist supports arrow keys, Home and End; the lineage dialog traps focus while open and returns
  it to the trigger on close.
- **No information lives only in a hover state.** The `*` and `†` provenance markers are buttons
  that reveal visible text in a status region, not tooltips.
- **Every chart has a data table beside it**, served from the same lineage ids the chart plotted,
  because a picture of a line tells a screen reader nothing.
- Nothing is encoded by colour alone: severity, projection method and assumption rung all carry
  text, and scenario lines are separated by dash pattern and a direct label.
- Loading reserves the space the content will occupy, so arriving data does not shift the layout;
  status changes are announced once through a single polite live region.
- Focus is never hidden under the sticky header, targets are at least 24x24 CSS px (44px where a
  finger is likely), text reflows at any width, and `prefers-reduced-motion` removes all motion.

The server has **no authentication** and binds to localhost. It is local analyst tooling; do not
expose it.

### Hosted app: analyse any SEC filer on demand

Live at **https://equity-research-engine-d7fs.onrender.com/**. To run your own:

```bash
SEC_USER_AGENT="Your Name you@example.com" research-engine app --data app-data
```

A public version of the dashboard with a search box. Type a company name or ticker; the app looks
it up in the SEC's own company index, writes a config for it, downloads its filings and runs
`ingest → quality → analyze → forecast` in the background, showing each stage as it completes.
A company already analysed opens at once; results are reused for a week.

It is a different deployment from `serve`, with different rules because it is public:

- **Configs come only from the SEC index, looked up by CIK.** A visitor picks a company; they never
  supply a URL, a path or a ticker the SEC does not know.
- **Every write stays under `--data`**, and every JSON response is scrubbed of local fields and of
  that directory's path, so there is nothing local to expose.
- **One pipeline at a time, a capped queue, and six new analyses per visitor per hour.** Reusing a
  finished company is never limited.
- **Coverage is what the SEC has as XBRL.** Funds, trusts and most foreign issuers have no tagged
  statements; the app says so instead of showing an empty dashboard.
- **Visitors can add documents** (see *Document library* below): five an hour each, 25 MB at most,
  HTML or PDF only, checked before anything runs.

`render.yaml` deploys it to Render's free plan (Blueprint → this repository; set `SEC_USER_AGENT`).
The free plan sleeps after 15 minutes idle and has no disk, so after waking it re-analyses its
showcase company first — the page shows the progress — and earlier results are recomputed when next
requested. Peak memory analysing a large bank's full history was about 265 MB, within the free
plan's 512 MB.

### Document library: add a report, and it is studied and kept

The SEC's structured data is the primary source, and it is not complete: figures a company tags
with its own concepts, figures qualified by a dimension (a regulatory approach), and figures that
are printed but not tagged at all never reach it. For a bank that is exactly the figures that
matter — CET1, risk-weighted assets, average interest-earning assets, tangible common equity.
A company's library holds the documents that fill that gap.

```bash
# owner: by EDGAR address (kept by reference) or as a file (kept in the repository)
research-engine add-document --config companies/nyse-jpm/config.yaml \
  --url https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm
research-engine add-document --config companies/nyse-jpm/config.yaml --file supplement.pdf --kind earnings_release
```

In the hosted app, anyone can add one from the company's **Sources** tab.

On the next `ingest`, each document is **studied**: its inline XBRL tags are read with their own
scale, sign, period and dimensions, and its tables are read row by row, a value taken only when
its row label matches the framework's wording for a metric and its column heading names the
fiscal year (period-end columns for balances, never the average ones; a stated unit — "in
millions" — is required). Then it is **verified**:

- a filing whose registrant CIK is another company's is rejected;
- where the document and the SEC data both have a figure, they must agree (within rounding) on at
  least 90% of at least three such figures, or the document is rejected;
- a document with too little in common to check is not used, whatever it contains.

A verified document **never replaces** an SEC figure; it only fills figures the SEC data does not
have, and each of those cites the document, the table or tag, and the printed row. The Sources tab
shows every document's result and exactly what it added; figures read from a document are marked
in the statements.

Run against JPMorgan's FY2025 10-K it agreed with the SEC data on 64 of 64 shared figures and
added average interest-earning assets (FY2023–FY2025), CET1 ratio and risk-weighted assets
(Standardized, FY2024–FY2025) and tangible common equity (FY2024–FY2025). The net interest income
driver then runs as declared — average earning assets × margin — instead of the trend stand-in.

**Kept for next time.** An owner's documents live in `companies/<id>/documents/`, versioned with
the repository. In the hosted app, a visitor's document is kept on the server and, when
`GITHUB_TOKEN` and `GITHUB_REPOSITORY` are set, proposed to the repository as a pull request once
it passes verification. Nothing reaches the permanent library without that check and your merge;
a company a visitor analysed for the first time comes with its config in the same pull request.

### Publishing

The public dashboard is static files on GitHub Pages; there is no server to secure.
`.github/workflows/pages.yml` rebuilds it weekly, on demand, and on every push to `main`: it takes
every configured company through `ingest → quality → analyze → forecast` on a clean runner, then
freezes the dashboard with `research-engine export-static`. Nothing is deployed unless the tests
pass, `quality --strict` finds no errors, every source downloads, and the export's leak check
passes — a failed run leaves the previous site up.

The export asks the API every question the dashboard can ask, in-process, and writes each answer
where the page will look for it, so the published figures are the API's own output rather than a
second implementation. Fields carrying local paths are dropped, and the export refuses to write
anything if a local path or e-mail address survives in any answer.

One-time setup:

```bash
gh secret set SEC_USER_AGENT                                    # "Your Name you@example.com"
gh api -X PUT repos/<owner>/<repo>/pages -f build_type=workflow # Pages source: GitHub Actions
```

To preview locally: `research-engine export-static --out site` then
`python -m http.server 8080 --directory site`. GitHub pauses scheduled workflows in repositories
with no activity for 60 days; re-enable it from the Actions tab if that happens.

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

Later: management guidance read from documents into the assumption registry, ESEF adapter, event studies.

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
- Tests use synthetic SEC-format fixtures, now alongside a documented live run against one bank. Other filers will tag things differently; the extraction report per run is the first place to look.
- The document registry stores raw-file paths as absolute paths, so a workspace does not survive being moved to another folder or machine; re-run `ingest` after moving one.
- Four frameworks ship (generic, banks, software, industrials). Insurance, asset managers, REITs, energy, etc. are not written.
- No consensus data source. Consensus will only appear if a user supplies a document for it.
- Forecast metrics with no driver formula and no derivation are projected by a trailing-median growth rate. That is
  the weakest link in any output here; `forecast.md` lists every metric produced this way. The fix is framework
  data — more derivations and driver formulas — not engine code.
- Forecasts are annual and deterministic. There is no interim forecast, no Monte Carlo, and no sensitivity grid;
  scenarios are discrete and analyst-declared.
- A driver formula cannot reference a prior-period value, so working-capital roll-forwards and balance-sheet
  closing identities (`equity[t] = equity[t-1] + net_income - dividends`) cannot yet be expressed as framework data.
- Documents are read for the figures a framework names, from tables and tags. Guidance and commentary in prose are
  not read; guidance must still be transcribed into `assumptions.yaml` by hand, citing the document.
- Table reading needs a year in the column heading and a stated unit. Quarterly columns ("three months ended") are
  skipped, so a 10-Q adds balance-sheet figures only at fiscal year end. PDF reading is line-based: a presentation
  whose figures sit in charts or images yields nothing.
- A document with fewer than three figures in common with the SEC data cannot be verified and is not used — so a
  document about a company the SEC does not cover cannot contribute yet.
- The dashboard is read-only and has no authentication. It cannot run the pipeline, edit assumptions or write
  anything; those are CLI operations, deliberately, so that every change to a company's data is a recorded command.
- The dashboard renders the charts the engine already produced rather than re-plotting in the browser, so provenance
  marks (hollow = derived, † = flagged) survive.
- Accessibility is verified automatically (axe-core) and by scripted keyboard walkthrough, not by manual audit with a
  screen reader. Automated tools catch roughly a third of real barriers; a genuine audit has not been done.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the invariants a change must not break, and
[CHANGELOG.md](CHANGELOG.md) for what changed when.

The most useful contribution is the least glamorous: run the pipeline against a real SEC filer and
report what breaks.

## Licence

[MIT](LICENSE).

## Disclaimer

**Research tooling, not investment advice.** Output beyond the extracted facts is model output —
an arithmetic consequence of stated assumptions, carrying no claim about what a company will do.
Publishing research or price projections about securities to other people is a regulated activity
in most jurisdictions. Read [DISCLAIMER.md](DISCLAIMER.md) before relying on or distributing
anything this produces.
