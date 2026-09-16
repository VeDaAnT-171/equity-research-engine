from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FRAMEWORKS = ROOT / "industry_frameworks"


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def frameworks_dir() -> Path:
    return FRAMEWORKS


@pytest.fixture
def bank_config_path() -> Path:
    return FIXTURES / "companies" / "example_bank.yaml"


@pytest.fixture
def industrial_config_path() -> Path:
    return FIXTURES / "companies" / "example_industrial.yaml"
