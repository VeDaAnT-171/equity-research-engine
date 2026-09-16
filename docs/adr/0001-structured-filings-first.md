# ADR-0001: Structured filings first, document extraction second

**Status:** Accepted
**Date:** 2026-09-16

## Context
The original specification makes user-supplied PDFs the primary source of truth for all financial data, for any
listed company. Page-level lineage from arbitrary PDFs requires table detection, header/unit/scale inference, and
period alignment that varies by issuer, language and GAAP. This is the hardest component and would block every
downstream phase.

## Options considered

### A: PDF-first
| Dimension | Assessment |
|---|---|
| Complexity | High |
| Accuracy | Issuer-dependent; silent unit/scale errors likely |
| Coverage | Any company with a PDF |

### B: XBRL-first, PDF for gaps
| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Accuracy | High for tagged statement values; concept mapping still needed |
| Coverage | US SEC filers; EU/UK via ESEF; others fall back to A |

## Decision
Option B. Canonical facts record `extraction_method`, so XBRL and PDF facts share one schema and one lineage model.

## Consequences
- Easier: a working end-to-end pipeline for US filers early; reliable accounting identity checks.
- Harder: company-defined KPIs (ARR, backlog, CET1 detail) still need document extraction.
- Revisit: when adding non-XBRL markets.
