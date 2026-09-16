# 0005 — Forecast: driver graph, assumption precedence, and cycle breaking

Status: accepted (Phase 5)

## Context

Phase 4 ends with verified annual analytics. A forecast has to answer three questions the earlier
phases deliberately avoided: which metrics are *assumed* rather than computed, where each assumed
number comes from, and what happens when the answer is "nothing credible".

Three constraints shaped the design.

**The engine must stay company-agnostic.** Drivers are already framework data (`DriverSpec.affects`
and an optional `formula`). The forecast had to be an interpreter for that data, not a model with
company logic in it.

**Fabrication must remain structurally hard.** The `Assumption` schema already refuses
`management_guidance` and `consensus` without a cited document. That guarantee is worth little if
the engine can quietly substitute a trend when guidance is absent, or if an analyst can hand-write
an assumption labelled `historical` and attach fact ids that were never checked.

**Historical derivations and forecast drivers run in opposite directions.** The banks framework
derives `net_interest_margin = net_interest_income / average_interest_earning_assets` — how you
measure a margin from actuals — and drives `net_interest_income = average_interest_earning_assets *
net_interest_margin` — how you forecast income. Both are correct. Applying both inside one
projected period is circular.

## Decision

### Projection precedence

For each metric in the forecast, in order: a driver formula that affects it; failing that, its own
framework derivation re-applied to projected inputs; failing that, exogenous. Exogenous metrics take
a growth rate, except ratio-valued metrics, which take the level — growing a margin is meaningless.

### Cycle breaking by demotion

The graph is built with derivations preferred, then checked for cycles. When a cycle is found, the
metric in it that another driver's formula models *with* is demoted to exogenous, preferring a
ratio-valued one. Forward, the margin is the assumption and the income line is the output. The
demotion, and the derivation it replaced, are recorded on the rule and printed in the report. A
cycle containing no derivation to demote is a framework error and raises.

We considered hard-coding "ratios are always exogenous". Rejected: `cet1_ratio` is ratio-valued and
exogenous for a different reason (nothing computes it), while `effective_tax_rate` is an analytic
that a driver consumes. The cycle is the actual signal; unit kind is only a tie-breaker.

### Assumption precedence

    scenario > analyst_assumption > management_guidance > consensus > historical/derived

Within one rung, an assumption pinned to a fiscal year beats one applying to every year.

The analyst outranks management deliberately. Deciding whether to believe guidance is the analyst's
job, and a model that cannot disagree with the company cannot produce a variant perception — which
is the only thing a research product sells. Consensus sits below guidance because it is a summary of
other people's models, useful mainly as the thing to differ from; it ranks above raw history because
a trailing median is the weakest input on the ladder.

Every projected value records the assumption id *and* the rung it resolved to, so
`assumption_types` on a forecast row shows at a glance how much of a model is judgement and how much
is extrapolation.

### Seeding, and who may write what

The engine seeds one `historical` assumption per exogenous input from the trailing three fiscal
years — median growth for levels, median level for rates — resolving each analytic's lineage back to
the fact ids underneath it. Three years is a stated convention, not a discovery: one year is noise,
a long window buries regime changes. It is overridable at every rung above it.

The analyst file may declare only `management_guidance`, `consensus`, `analyst_assumption` and
`scenario`. `historical` and `derived` are engine-computed, because an analyst cannot legitimately
cite the fact ids that make them checkable, and permitting the label would let invented provenance
in through the front door.

### Refusal over extrapolation

A metric whose rule needs a missing input, or an assumption declared without a value, is *not
projected*. The reason is counted per metric-year and reported. A forecast that quietly invents a
number it could not compute is worse than one that is visibly incomplete, because only the second
kind can be reviewed.

## Consequences

- Adding a driver, a forecast target or a scenario is a YAML edit. No engine change.
- A framework whose derivations and drivers disagree gets a documented demotion rather than a crash
  or, worse, a silently wrong number.
- Sparse history produces a mostly-empty forecast with an explicit list of what is missing and why.
  This is the intended outcome and the reason `output/assumptions.json` carries `unseeded`,
  `not_projected` and `demoted_derivations` alongside the values.
- Trend-driven metrics are the weakest part of any output here. The remedy is framework data — more
  derivations and driver formulas — not engine code, and the projection plan in the report is
  designed to make those gaps obvious.
