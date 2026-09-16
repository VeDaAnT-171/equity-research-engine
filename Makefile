PY ?= python3
CONFIG ?= companies/nyse-jpm/config.yaml
FRAMEWORKS ?= industry_frameworks

.PHONY: install test validate frameworks research

install:
	$(PY) -m pip install -e ".[dev]"

test:
	PYTHONPATH=src $(PY) -m pytest -q

validate:
	PYTHONPATH=src $(PY) -m research_engine validate --config $(CONFIG) --frameworks $(FRAMEWORKS)

frameworks:
	PYTHONPATH=src $(PY) -m research_engine frameworks --frameworks $(FRAMEWORKS)

# Fails loudly until the pipeline phases exist. Never silently no-ops.
research:
	PYTHONPATH=src $(PY) -m research_engine research --config $(CONFIG) --frameworks $(FRAMEWORKS)
