# Contributing

## Getting set up

```bash
git clone https://github.com/VeDaAnT-171/equity-research-engine
cd equity-research-engine
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
make install-api        # or `make install` without the dashboard extras
make check              # tests, lint, types
```

`make check` is what CI runs. If it passes locally it should pass there.

## The invariants

Most of this project's value is in what it refuses to do. A change that breaks one of these is
not a trade-off to discuss; it is a defect. Each is enforced by a test, and the tests are the
specification.

**The company is an input, never code.** No configured company's ticker, name or identifiers may
appear anywhere in `src/` or `industry_frameworks/`, and no code may branch on company identity.
Adding a company is a YAML file and nothing else. Enforced by `tests/test_company_agnostic.py`.

**Every number carries its lineage.** A reported fact must cite a page, table or XBRL concept. A
derived fact must cite its input facts and a formula. An analytic must cite the facts or analytics
it was computed from. A forecast value must cite the assumption that supplied its exogenous input.
If you add a value type, it joins the lineage graph, and a test proves it traces to a source URL.

**Guidance and consensus cannot exist without a cited document.** This is a type-level rule in
`schemas/assumption.py`, not a convention. Equally, an analyst may not author a `historical`
assumption: only the engine can cite the fact ids that make one checkable.

**Missing is not the same as fine.** A check with no data is "not evaluable", never a pass. A
metric the engine cannot project is refused with a counted reason, never extrapolated. A pipeline
stage that has not run returns a 409 from the API, never an empty result. If your change can
produce a plausible-looking blank, it is wrong.

**The engine computes; it does not opine.** Outputs are labelled MODEL OUTPUT and carry no
interpretation. Adding language that tells a reader what a number means belongs in an analyst's
own commentary, not in the engine.

**Industry knowledge is data.** Metrics, derivations, drivers, checks, analytics, charts and
valuation policy live in `industry_frameworks/*.yaml`. If you find yourself writing a Python
branch for how banks differ from software companies, the framework schema is the place to put it
instead — extend the schema rather than special-casing in code.

## Adding an industry framework

Frameworks inherit from `generic` and may add, override or remove. Start from the closest
existing one:

```bash
cp industry_frameworks/industrials.yaml industry_frameworks/insurance.yaml
make frameworks     # validates every framework: references, cycles, valuation compatibility
```

The validator will reject dangling references, circular derivations, ids used as both metric and
driver, analytics colliding with metric names, and a framework that resolves to no valuation
methods. Removals must name something the parent actually defines.

## Adding a company

```bash
mkdir -p companies/<exchange>-<ticker>
$EDITOR companies/<exchange>-<ticker>/config.yaml
make validate CONFIG=companies/<exchange>-<ticker>/config.yaml
```

No code changes. If a company needs code changes, that is a bug in the engine.

## Tests

Tests use synthetic SEC-format fixtures under `tests/fixtures/`. Please do not add fixtures
copied from a real filer's data.

A change to a guarantee should come with a test that fails without it. Several existing tests are
written as mutation checks — loosening a tolerance, disabling the quality gate or allowing a
negative denominator each makes a test fail on purpose. That pattern is welcome.

## Style

`ruff` and `mypy` are configured in `pyproject.toml` and run in CI. Lines go to 120 characters.

Comments should explain *why*, especially where the code refuses to do something obvious — that
refusal is usually the design. Comments restating what the line does are noise.

## What is most useful

The roadmap's remaining phases are valuation, the research report, and a second contrasting
company taken end to end. Before any of those, the highest-value contribution is the least
glamorous one: **run the pipeline against a real SEC filer and report what breaks.** Every test in
this repository runs on synthetic data, so the first live run is the first real test.
