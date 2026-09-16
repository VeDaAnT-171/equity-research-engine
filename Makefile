PY ?= python3
CONFIG ?= companies/nyse-jpm/config.yaml
FRAMEWORKS ?= industry_frameworks

.PHONY: install test validate frameworks ingest quality analyze forecast data research

install:
	$(PY) -m pip install -e ".[dev]"

test:
	PYTHONPATH=src $(PY) -m pytest -q

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

# Fails loudly until the pipeline phases exist. Never silently no-ops.
research:
	PYTHONPATH=src $(PY) -m research_engine research --config $(CONFIG) --frameworks $(FRAMEWORKS)
