PY ?= python3
CONFIG ?= companies/nyse-jpm/config.yaml
FRAMEWORKS ?= industry_frameworks

.PHONY: install install-api test lint types check validate frameworks ingest quality analyze forecast data research serve

install:
	$(PY) -m pip install -e ".[dev]"

install-api:
	$(PY) -m pip install -e ".[dev,api]"

test:
	PYTHONPATH=src $(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests

types:
	$(PY) -m mypy

# What CI runs. If this passes locally it should pass there.
check: test lint types

validate:
	PYTHONPATH=src $(PY) -m research_engine validate --config $(CONFIG) --frameworks $(FRAMEWORKS)

frameworks:
	PYTHONPATH=src $(PY) -m research_engine frameworks --frameworks $(FRAMEWORKS)

ingest:
	PYTHONPATH=src $(PY) -m research_engine ingest --config $(CONFIG) --frameworks $(FRAMEWORKS) $(ARGS)

quality:
	PYTHONPATH=src $(PY) -m research_engine quality --config $(CONFIG) --frameworks $(FRAMEWORKS) $(QARGS)

analyze:
	PYTHONPATH=src $(PY) -m research_engine analyze --config $(CONFIG) --frameworks $(FRAMEWORKS)

forecast:
	PYTHONPATH=src $(PY) -m research_engine forecast --config $(CONFIG) --frameworks $(FRAMEWORKS)

data: ingest quality analyze forecast

# Read-only dashboard over whatever the pipeline has written. Local tooling: do not expose it.
COMPANIES ?= companies
PORT ?= 8000
serve:
	PYTHONPATH=src $(PY) -m research_engine serve --companies $(COMPANIES) --port $(PORT)

# Fails loudly until the pipeline phases exist. Never silently no-ops.
research:
	PYTHONPATH=src $(PY) -m research_engine research --config $(CONFIG) --frameworks $(FRAMEWORKS)
