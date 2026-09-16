# ADR-0003: Data-quality thresholds are derived rules, not tuned constants

**Status:** Accepted
**Date:** 2026-09-16

## Context
Data-quality checks need thresholds: how far apart two numbers can be before an identity "fails", how large a change is
"unusual". Tuning constants against one company's data would bake that company into the engine and violate the
no-arbitrary-thresholds requirement.

## Decision
| Check | Rule | Source of the number |
|---|---|---|
| Identities and additive derivations | abs(difference) ≤ n × u / 2 | Each of n terms is rounded to presentation unit u; u inferred from trailing zeros, capped at 10^6 |
| Scale break | abs(log10(ratio)) within 0.2 of 3, 6 or 9 | Unit errors are powers of 1000; 0.2 in log10 allows −37%..+58% genuine change on top |
| Unusual change | modified z-score of log growth > 3.5, n ≥ 5 | Iglewicz & Hoaglin (1993) convention for robust outlier labelling |
| Sign | negative value for a metric declared `non_negative` | Framework metadata, not data |

Severity reflects what a failure usually means: cash-flow identities are `error` (the statement itself must tie);
the balance-sheet identity is `warning` because equity tags that exclude non-controlling interests break it legitimately.

## Consequences
- Every threshold can be explained in one sentence in the report's methodology section.
- The first live runs will show whether the presentation-unit inference is too tight for filers that tag unrounded values.
  If so, reading XBRL `decimals` from the filing instance documents is the principled fix, not widening the constant.
