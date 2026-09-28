# Disclaimer

**This software is research tooling. It is not investment advice, and its output is not a
recommendation to buy, sell or hold any security.**

## What the output is

Everything this engine produces beyond the reported facts it extracts is **model output**: a
number computed from assumptions, by formulas defined in configuration files, starting from
figures the operator chose to ingest. The forecast in particular is an arithmetic consequence of
assumptions that a person supplied or that the engine seeded from a short window of history. It
carries no claim about what a company will actually do.

The engine is deliberately built to make this checkable rather than to be trusted: every figure
records the formula, inputs and source document behind it, and refuses to produce a value it
cannot derive. That design reduces the chance of a *silently* wrong number. It does not make the
numbers right.

## Known limitations that bear on reliance

- **The engine has not been validated against a live filing.** Every automated test runs on
  synthetic SEC-format fixtures. Behaviour on real filings is untested.
- Forecast metrics with no driver formula are projected by a trailing-median growth rate, which
  is the weakest possible forecasting method and is labelled as such in every output.
- Data-quality checks are heuristics with stated thresholds, not audit procedures.
- No valuation is implemented, so nothing here produces a price, a target or a fair value.

## Regulatory note

Publishing research, recommendations or price projections about securities to other people is a
**regulated activity** in most jurisdictions — including India, where it falls under SEBI's
Research Analyst Regulations, and the United States, where it may implicate the Investment
Advisers Act. Running this software privately on your own machine for your own analysis is an
ordinary use of a tool. Using its output to advise others, or hosting it as a service that gives
third parties equity projections, may carry registration and compliance obligations.

This is not legal advice. If you intend to publish or distribute anything derived from this
engine, take advice from someone qualified in your jurisdiction first.

## Warranty

None. See [LICENSE](LICENSE). The software is provided "as is", and the authors accept no
liability for any loss arising from its use.
