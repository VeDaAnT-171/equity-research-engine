# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions before 1.0.0 are pre-release: the engine has not been validated against a live SEC
filing, so no interface here should be treated as stable.

## [Unreleased]

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
