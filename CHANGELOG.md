# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions before 1.0.0 are pre-release: the engine has not been validated against a live SEC
filing, so no interface here should be treated as stable.

## [Unreleased]

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
