import re

from pydantic import BaseModel, ConfigDict

SLUG_PATTERN = r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$"
METRIC_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"
DOCUMENT_ID_PATTERN = r"^doc_[0-9a-f]{16}$"
FACT_ID_PATTERN = r"^fact_[0-9a-f]{16}$"
FISCAL_PERIOD_LABEL_PATTERN = r"^(FY\d{4}|(Q[1-4]|H[12])-\d{4})$"
XBRL_CONCEPT_PATTERN = r"^[A-Za-z][\w-]*:[A-Za-z]\w*$"

_NON_SLUG = re.compile(r"[^a-z0-9]+")


class StrictModel(BaseModel):
    """Immutable, typo-rejecting base model."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def slugify(*parts: str) -> str:
    slug = _NON_SLUG.sub("-", "-".join(parts).lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot build a slug from {parts!r}")
    return slug
