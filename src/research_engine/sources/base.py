"""Pluggable structured-data sources. Adding a provider (ESEF, FRED, a market-data vendor) means adding
an adapter class here; the pipeline discovers adapters by URL, never by company."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any


class StructuredSource(ABC):
    name: str = "abstract"
    #: human-readable description recorded in reports
    description: str = ""

    @abstractmethod
    def can_handle(self, url: str) -> bool: ...

    @abstractmethod
    def identifier_from_url(self, url: str) -> str | None:
        """Entity identifier embedded in the URL (e.g. CIK), used to catch config mistakes before fetching."""

    @abstractmethod
    def normalize(self, content: bytes, document_id: str) -> Any:
        """Parse and validate raw bytes into typed records. Raises ExtractionError."""

    def metadata(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


def find_source(url: str, sources: Sequence[StructuredSource]) -> StructuredSource | None:
    matches = [s for s in sources if s.can_handle(url)]
    return matches[0] if matches else None
