# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions before 1.0.0 are pre-release: the engine has been run against one live filer and no
more, so no interface here should be treated as stable.

## [Unreleased]

### Added — document library

- **Company document library** (`documents/`): annual reports, quarterly reports, earnings
  releases and presentations added per company, as a file (HTML or PDF) or by EDGAR address.
  `research-engine add-document` for the owner; the hosted app's **Sources** tab for anyone.
- **Inline XBRL reader.** Reads every tagged figure in a filed 10-K/10-Q with its scale, sign,
  period and dimensions, recovering figures the companyfacts API omits: filer-specific concepts and
  dimensioned values (CET1 and risk-weighted assets by regulatory approach).
- **Table reader** for HTML and PDF. Resolves row- and colspans so every value knows its full
  column heading; takes a value only under a fiscal-year heading, with a stated unit, and for a
  balance never from an "average" column. Frameworks name the wording (`document:` hints on a
  metric), never a company.
- **Verification before use.** Registrant CIK must match; figures shared with the SEC data must
  agree on ≥ 90% of ≥ 3; otherwise the document is rejected or left unused. A verified document
  only fills gaps and never replaces an SEC figure. `output/documents.json` records each
  document's result and contribution.
- **Hosted uploads** with per-visitor limits, type sniffing, a 25 MB cap and a per-company cap;
  uploaded HTML is only ever served as a download. Verified documents are proposed to the
  repository as pull requests when `GITHUB_TOKEN`/`GITHUB_REPOSITORY` are set.
- **Dashboard:** Sources tab (primary source, library with status and what each document added,
  upload form in the hosted app); figures read from documents marked in the statements; the source
  panel names the document, table and printed row. New statements appear when documents supply
  them (operating metrics, regulatory capital).
- API: `/library`, `/library/{id}/file`; `PUT`/`POST /library` in the hosted app.

### Changed

- JPMorgan's FY2025 10-K is in its library by EDGAR reference. With it, net interest income is
  projected by the declared driver (average interest-earning assets × margin); the trend fallback
  is no longer in force for it.
- Published outputs and lineage name a local file by its file name only.
- `lxml` and `pdfplumber` are now dependencies.

### Changed

- **The dashboard is rebuilt as a reader's product.** Sections are now Summary, Financials, Ratios,
  Estimates and Data checks. A company header gives exchange, ticker, sector, fiscal year end,
  history and the data date; headline tiles show the latest year with year-on-year change; the
  financial statements are shown in millions with negatives in parentheses; estimates are headed
  `FY2026E` and described as projections, not company guidance. Pipeline stages, engine and schema
  versions, framework ids, metric ids and refusal codes no longer appear on the page. Verified in a
  browser against the live filer's outputs, locally, as a static snapshot and in the hosted app,
  with zero axe-core WCAG 2.1 AA violations across every section and the source panel.
- **Refusals and data-check findings are explained in words.** `presentation.py` turns metric ids,
  refusal codes and assumption keys into names and sentences, and describes each data-check finding
  from its structured details (amounts formatted, revisions listed with their filings). Chart notes
  and captions use the same wording and name the SEC as the source instead of the engine.
- Hosted-app messages are written for visitors: failures say what happened in one sentence and keep
  the technical detail in the server log.

### Added

- API: `/glossary` (display names and vocabulary, written at analyze time so the API stays
  framework-free), `/financials` (full-year statements, every value citing its fact) and `/checks`
  (the data-quality report in words). The static export includes all three and the lineage trace
  behind every statement cell.

- **Hosted app (`research-engine app`): search any SEC filer and analyse it on demand.** Company
  lookup against the SEC's own ticker index (one CIK is one company, whichever ticker is typed),
  config generation from the index only, a single background worker running the four stages with
  per-stage progress, a week of result reuse, per-visitor limits on new runs and a capped queue.
  Every JSON response is scrubbed of local fields and the data directory's path; a test sweeps
  every endpoint for it. The dashboard gains an accessible search form, run progress with
  announced stage transitions, a warm-up view while the showcase company is analysed at start-up,
  and a plain explanation when the SEC has no XBRL for a company. `render.yaml` deploys it to
  Render's free plan. Built and verified in a browser against the live filer's real filings served
  from disk, since this build environment cannot reach the SEC.

- **`export-static`: the dashboard as static files.** GitHub Pages serves files and runs nothing,
  so the dashboard, which reads a live API, showed only the rendered README there. The export
  drives the API in-process, writes every answer the dashboard can request — including the lineage
  trace behind each value it renders as traceable — and marks the page as a frozen snapshot with
  its time and engine version. Local paths are removed, and the export refuses to write anything if
  the workspace path, any home-directory path, or an e-mail address survives in any answer. A stage
  that was not run is exported as the same refusal the live API gives, not as a missing file.
- **The public dashboard rebuilds itself.** `.github/workflows/pages.yml` runs weekly, on demand and
  on every push to `main`: every configured company goes through the whole pipeline on a clean
  runner against the live SEC API, and the result is exported and deployed. It publishes only if
  the tests pass, `quality --strict` finds no errors, every source downloads and the export's leak
  check passes; otherwise the previous site stays up. `site/` is build output and is not committed.

- **Declared fallback drivers.** A driver may declare `fallback: trend`. When the company has no
  base-year value for a metric the driver's formula needs, the metrics it affects are projected on
  their own history instead, and every forecast value that depends on one — however far downstream
  — carries `fallback_for`. The substitution is recorded in `assumptions.json`, stated above the
  tables in `forecast.md`, and shown in the dashboard as a note plus a `‡` on each affected figure.
  The bank framework declares it for net interest income, whose model (earning assets times margin)
  needs average interest-earning assets, which has no us-gaap element. Before this, no bank read
  from XBRL alone had a projected revenue line, net income or EPS.

### Fixed (continued from the first live run)

- **Bank loans stopped at FY2015.** The framework declared only the oldest of the three elements
  the net-loans line has been tagged with; the two successors are now declared, ranked, and
  stitched with a `concept_switch` warning. This restored credit costs, loan-to-deposit and the
  provision driver, and gave `missing_years` its first real finding: one year between renames that
  no declared element covers.
- **A skipped chart could still be served.** A projection image from an earlier run survived after
  the metric stopped being projected; the index marked the chart skipped and the image endpoint
  served the file anyway. Skipped charts are no longer served, and their old images are deleted
  when the stage runs.
- **Lines bridged missing years.** A single polyline drew a smooth path through years with no data.
  Lines now break at gaps, and the chart notes name the missing years and why.
- **X-axis labels collided** past a dozen years; every other year is labelled beyond that.
- **The company-agnostic check missed a leak.** Engine comments written during this work named the
  live filer. They are removed, and the invariant test now also searches for a company's coined
  brand word, which is how the leak got past a check that only matched full legal names.

### Still open from the live run

Found, understood, and deliberately not fixed yet — listed so that "fixed" above means what it says.

- `missing_years` checks only for gaps *inside* a series' own span, so a metric that stops years
  before its siblings passes. Charts now say where a series stops; the quality check still does not.
- The `balance_sheet_identity` warning suggests non-controlling interests as the likely cause. For
  this filer liabilities plus equity *exceed* assets, which that explanation cannot produce; the
  real cause is not yet known.
- `derivation:diluted_eps` was never evaluable on this filer (0 of 111), so the EPS identity was
  confirmed by hand rather than by the engine.
- The document registry stores raw-file paths as absolute paths, so a workspace cannot be moved.
- The forecast seeds every rate from the three years to the base year with no mean reversion. On
  this filer that window includes a bank acquisition, so loans compound at roughly 11% a year while
  deposits grow under 3%. Nothing is wrong with the arithmetic; the assumption is the weak part,
  which is what `assumptions.yaml` exists to override.

### Fixed

Both of these were found by the first run against live SEC data (JPMorgan Chase, CIK 0000019617,
FY2007–FY2025). Neither was reachable with the synthetic fixtures the test suite had used until
now, because both need a real filer's irregularities to appear.

- **Year-on-year growth is refused whenever the series crosses zero, not only when the base is
  non-positive.** The old test caught `-100 -> -50` and let `+100 -> -50` through, which computes
  to -150%: a figure that reads as a rate and is not one. JPMorgan's operating cash flow swings
  either side of zero, so the historical table carried entries of -2052.8% and -423.8% and a
  median over them; the forecast seeder applied the same one-sided test, took that median, and
  compounded it five years into a decay toward zero presented exactly like every other projection.
  The predicate now lives in `research_engine.growth` and both stages call it, so they cannot
  disagree again. A refusal names its case — `growth_base_not_positive` or `growth_sign_change`.
- **A growth projection is refused when the base-year value is not positive.** Removing the
  sign-crossing pairs from the *seed* was not enough: the seeder stepped back over the refused
  years, stitched FY2019, FY2022 and FY2023 into what it described as three observations, and the
  projection carried on decaying. No rate makes `-147.8bn x (1 + r)` mean anything, so the refusal
  now sits at the starting point, where it holds regardless of how the rate was measured.
- **Historical assumptions are seeded from the window ending at the base year**, rather than from
  a metric's last three observations wherever they fall. The old rule reached across every gap:
  JPMorgan's loans series ended in FY2015 and still produced a growth assumption measured over
  FY2013–FY2015, described without qualification, eleven years stale. How many of the window's
  years must be usable is deliberately not a rule — a company with two years of history has a
  fragile but real growth rate, and refusing it would be a judgement about sample size rather than
  about whether the measurement means anything.
- **Charts state where a series stops.** The x-axis was drawn to the last year with data, so a
  metric that died mid-history produced a chart that looked complete. JPMorgan stopped using the
  loans concept the bank framework declares after FY2015, and the credit-cost chart ran 2009–2015
  in silence while the analytics table beside it correctly showed dashes through FY2025 — the
  record was right and the picture, which is what gets read, was not. The axis now runs to the end
  of the company's reporting span, the reason each series ended travels to the chart, its data
  table and the dashboard, and the rendered image carries it in the footnote. The axis is never
  extended backwards: a series that starts late started when its tagging did.

### Not yet built

- Valuation and reverse valuation (roadmap phase 6)
- Research report output (phase 7)
- A second contrasting company taken end to end (phase 8)

## [0.7.0] — 2026-09-28

### Added

- **Forecast scenario charts**, rendered server-side. Solid to the last reported year and dashed
  after it; scenarios separated by line style and a direct end label rather than by hue; filled
  markers for reported values, hollow for projected. A metric no scenario projects is skipped
  rather than drawn as a flat continuation of its last actual.
- **Typed API responses**, so the OpenAPI document describes real shapes rather than bare
  dictionaries, and clients can see which fields may be absent.
- Endpoints for forecast charts, the annual coverage matrix, and a data-table representation of
  every chart, built by joining the lineage ids the chart plotted.
- Project documents: licence, changelog, contributing guide, and a disclaimer proportionate to
  financial output.
- Continuous integration across Python 3.10–3.12, with linting, type checking and coverage.

### Changed

- The dashboard was rebuilt against WCAG 2.1 AA. Keyboard-operable throughout, no information
  available only on hover, a data table beside every chart, nothing encoded by colour alone, and
  loading that reserves the space content will occupy. Verified with axe-core (zero violations on
  every view) and a scripted keyboard walkthrough.
- The `serve` command now refuses a non-loopback bind without an explicit acknowledgement, since
  the API has no authentication.

### Fixed

- The forecast stage rewrote `lineage.json` from documents and facts only, silently dropping the
  chart and analytic chains the analysis stage had written. Covered by a regression test.
- `history.replaceState` threw in sandboxed and `srcdoc` contexts, taking the whole dashboard
  down with it. Deep linking is now attempted and ignored on failure.
- The dashboard's startup error handler reported "could not reach the server" for every failure,
  including ones with no network involved.
- `quality/runner.py` contained two byte-identical definitions of `_carry_extraction_findings`;
  the first was dead code that would have diverged on the next edit.
- `ForecastStageResult.charts` was a non-optional field defaulting to `None`.
- Loop variables shadowed across record types in the forecast pipeline, which defeated type
  checking in exactly the region where the duplicate definition above had hidden.

## [0.6.0] — 2026-09-17

### Added

- **Read-only HTTP API and dashboard** over pipeline outputs. The API computes nothing: every
  value it returns was written by a pipeline stage, so anything shown can be checked against the
  files in a company's `output/` directory.
- A stage that has not been run returns HTTP 409 naming the command to run, rather than an empty
  result that reads as a clean bill of health.
- Lineage drill-down from any figure to the filing it rests on.

## [0.5.0] — 2026-09-16

### Added

- **Forecast** (roadmap phase 5): driver-graph evaluation, an assumption registry and scenarios.
- Assumption precedence: scenario, then analyst, then management guidance, then consensus, then
  history seeded by the engine. Guidance and consensus cannot exist without a cited document, and
  the analyst file may not declare engine-seeded history.
- Cycle breaking by demotion, for frameworks whose historical derivations and forecast drivers
  legitimately run in opposite directions.
- `forecast_targets` in the framework schema, so a framework can demand metrics no driver names.
- Framework additions so the modelled chain articulates end to end: tax and pre-tax bridge
  drivers, net income and EPS derivations, and bank-specific pre-tax and attribution drivers.

## [0.4.0] — 2026-09-16

### Added

- **Historical analysis** (phase 4): framework-defined analytics with explicit balance bases,
  quality gating, descriptive statistics, charts and Parquet datasets.
- Lineage from a chart through its analytic values and facts to a source URL.

## [0.3.0] — 2026-09-16

### Added

- **Data quality** (phase 3): interim and framework derivations with lineage, accounting
  identities as framework data, and sign, scale, outlier and gap checks with stated thresholds.
- `data_quality_report.html`, where a check with no data is reported as "not evaluable" rather
  than as a pass.

## [0.2.0] — 2026-09-16

### Added

- **Structured ingestion** (phase 2): SEC companyfacts and submissions adapters behind a cached,
  versioned downloader; identity verification before any fact is trusted; framework selection
  from filing evidence; XBRL mapped to canonical facts with every skip counted under a reason.

## [0.1.0] — 2026-09-16

### Added

- **Foundation** (phase 1): company configuration as validated data, a canonical fact schema with
  lineage enforced by construction, industry frameworks as data with single inheritance, a
  content-addressed document registry, and a lineage DAG.
- The invariant the project exists to hold: no configured company's identifiers may appear in
  engine code or frameworks, enforced in CI.

[Unreleased]: https://github.com/VeDaAnT-171/equity-research-engine/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/VeDaAnT-171/equity-research-engine/releases/tag/v0.7.0
[0.6.0]: https://github.com/VeDaAnT-171/equity-research-engine/releases/tag/v0.6.0
[0.5.0]: https://github.com/VeDaAnT-171/equity-research-engine/releases/tag/v0.5.0
[0.4.0]: https://github.com/VeDaAnT-171/equity-research-engine/releases/tag/v0.4.0
[0.3.0]: https://github.com/VeDaAnT-171/equity-research-engine/releases/tag/v0.3.0
[0.2.0]: https://github.com/VeDaAnT-171/equity-research-engine/releases/tag/v0.2.0
[0.1.0]: https://github.com/VeDaAnT-171/equity-research-engine/releases/tag/v0.1.0
