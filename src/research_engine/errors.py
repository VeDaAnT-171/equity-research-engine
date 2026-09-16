"""Exception hierarchy. Every failure surfaces as one of these with an actionable message."""

from pydantic import ValidationError


class ResearchEngineError(Exception):
    """Base class for all engine errors."""


class ConfigError(ResearchEngineError):
    """Invalid or missing company/project configuration."""


class FrameworkError(ResearchEngineError):
    """Invalid, missing or inconsistent industry framework."""


class RegistryError(ResearchEngineError):
    """Document registry integrity or state-transition violation."""


class LineageError(ResearchEngineError):
    """Broken or cyclic data lineage."""


class FetchError(ResearchEngineError):
    """A document could not be retrieved."""


class ExtractionError(ResearchEngineError):
    """A retrieved document could not be parsed, or contradicts the configuration."""


def format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        msg = err["msg"].removeprefix("Value error, ")
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)
