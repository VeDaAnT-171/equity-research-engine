# ADR-0004: Historical analytics as framework data with an explicit balance basis

**Status:** Accepted
**Date:** 2026-09-16

## Context
Phase 3 frameworks defined ROE, ROTCE, credit-loss rate, revenue per customer and backlog conversion as metric
derivations such as `net_income / total_equity`. The fact index resolved the balance at period end, so a year of
income was divided by one day's equity. For a company that raised or returned capital during the year, this misstates
the ratio, and the error is invisible in the output.

## Decision
- A metric derivation may only combine terms of the same period type (flows with flows, balances with balances).
  Framework validation rejects anything else and points to analytics.
- Engine-computed ratios live in `analytics:` with a kind (`level`, `growth`, `ratio`, `expression`, `elasticity`)
  and, for ratios, a `denominator_basis` of `period_end`, `average` or `opening`.
- Analytics are annual, computed in dependency order, and never estimated: every missing value records a reason.
- Values built on facts that failed error-severity checks are not computed; warning flags propagate.

## Options considered
| Option | Why rejected |
|---|---|
| Keep period-end derivations and document the bias | The bias is largest exactly when capital changes, which is when analysts look hardest |
| Add averaging syntax to the expression language (`avg(x)`) | Mixes time semantics into arithmetic; harder to validate and explain in reports |
| Hardcode average-based ratios in Python | Industry-specific; would reintroduce code changes per industry |

## Consequences
- Easier: every ratio in the report states its basis; the same analytic definition works for any company in the framework.
- Harder: the first year of history cannot have average-based ratios.
- Frameworks changed, so outputs from Phase 3 are rejected by hash and must be regenerated.
