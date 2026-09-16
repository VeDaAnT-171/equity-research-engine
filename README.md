# Equity Research Engine

A company-agnostic engine that turns primary company disclosures into reproducible, auditable equity research.
The company is an **input** (a YAML file). The engine is the product.

> **Status: Phase 1 of 8 — foundation.** Configuration, canonical schemas, industry frameworks, document registry
> and lineage are implemented and tested. Extraction, forecasting, valuation and reporting are **not built yet**.
> `make research` fails loudly until they are.

## What works today

| Capability | Where | Guarantee |
|---|---|---|
| Company config validation | `schemas/company.py`, `config/loader.py` | Typos rejected, https-only URLs, SSRF guard, blank placeholders tolerated |
| Canonical financial facts | `schemas/financial.py` | Reported facts **must** cite a page/table/XBRL concept; derived facts **must** cite input facts and a formula |
| Assumption registry schema | `schemas/assumption.py` | Consensus and guidance cannot exist without a cited document |
| Industry frameworks as data | `industry_frameworks/*.yaml` | Inheritance, removals, reference and cycle checks, valuation-method compatibility |
| Document registry | `registry/document_registry.py` | Content-addressed, read-only raw store; never overwrites; enforced status machine |
| Lineage graph | `lineage/graph.py` | Report element → chart → model → fact → document → source URL; broken lineage detectable |
| Company-agnostic guard | `tests/test_company_agnostic.py` | CI fails if any configured company's ticker/name/CIK appears in engine code or frameworks |

## Quick start

```bash
make install
make test
make frameworks
make validate CONFIG=companies/nyse-jpm/config.yaml
```

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
2. **Structured ingestion** — SEC companyfacts/submissions adapter, downloader with caching, XBRL → canonical facts
3. **Classification + data quality** — SIC-based candidates, accounting identity checks, `data_quality_report`
4. **Historical analysis** — framework-driven ratios, charts, reconciliation flags
5. **Forecast** — driver graph evaluation, assumption registry, scenarios
6. **Valuation + reverse valuation** — DCF/FCFF, residual income, multiples; solve for market-implied drivers
7. **Research output** — HTML/PDF report; thesis, catalysts, falsifiers from analyst YAML, quantified by the engine
8. **Second contrasting company end to end** (proves agnosticism with output, not just tests)

Later: PDF/HTML KPI extraction, ESEF adapter, dashboard, event studies.

## Limitations

- No data is fetched or extracted yet.
- Framework XBRL concepts are candidate lists; filer coverage is unverified until Phase 2.
- Four frameworks ship (generic, banks, software, industrials). Insurance, asset managers, REITs, energy, etc. are not written.
- No consensus data source. Consensus will only appear if a user supplies a document for it.

## Disclaimer

Research tooling, not investment advice.
