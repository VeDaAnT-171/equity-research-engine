# ADR-0002: Versioned source documents and date-based fiscal periods

**Status:** Accepted
**Date:** 2026-09-16

## Context
Two assumptions from Phase 1 failed against real SEC data:
1. The registry refused changed content at the same URL. `companyfacts` changes after every filing, so a refresh would always fail.
2. The obvious way to label periods is SEC's `fy`/`fp` fields. Those fields belong to the filing, not the value:
   a 10-K for FY2025 reports FY2024 and FY2023 comparatives tagged `fy=2025, fp=FY`.

## Decision
- Changed content is stored as a new document version (`supersedes` link, old version marked `superseded`).
  Raw bytes of every version remain; facts keep pointing at the version they came from.
- Periods are classified from `start`/`end` dates against the fiscal year end (config, or SEC `fiscalYearEnd`),
  with duration buckets and a 7-day spillover rule for 52/53-week years. `fy`/`fp` are ignored.

## Options considered
| Option | Why rejected |
|---|---|
| Overwrite raw documents on refresh | Breaks lineage for facts already extracted and violates the immutability requirement |
| Date the source key (one document per retrieval day) | Creates duplicates when content has not changed; hash-based versions do not |
| Use `fy`/`fp` with a de-duplication pass | Still mislabels comparatives in edge cases (amended filings, transition periods) |

## Consequences
- Easier: repeatable refreshes; a restated period is visible as two facts from two filings.
- Harder: consumers must use the `current_facts` view rather than assume one fact per period.
- Revisit: transition periods (10-KT) produce durations outside the buckets and are skipped with a counted reason.
