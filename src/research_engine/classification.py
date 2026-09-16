"""Framework selection from evidence. Order: explicit config override > unique SIC match > generic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .errors import FrameworkError
from .frameworks import FrameworkRegistry
from .schemas.company import ProjectConfig
from .sources.sec import EntityProfile

FALLBACK = "generic"


@dataclass(frozen=True)
class FrameworkSelection:
    name: str
    method: Literal["config_override", "sic_match", "fallback"]
    evidence: str


def select_framework(config: ProjectConfig, profile: Optional[EntityProfile], registry: FrameworkRegistry) -> FrameworkSelection:
    override = config.company.industry_framework
    if override:
        registry.get(override)
        detail = f"industry_framework: {override} in config"
        if profile and profile.sic:
            candidates = registry.candidates_for_sic(profile.sic)
            if candidates and override not in candidates:
                detail += f" (note: SIC {profile.sic} suggests {', '.join(candidates)})"
        return FrameworkSelection(override, "config_override", detail)
    if profile and profile.sic is not None:
        candidates = registry.candidates_for_sic(profile.sic)
        label = f"SIC {profile.sic}" + (f" ({profile.sic_description})" if profile.sic_description else "")
        if len(candidates) == 1:
            return FrameworkSelection(candidates[0], "sic_match", label)
        if len(candidates) > 1:
            raise FrameworkError(f"{label} matches several frameworks ({', '.join(candidates)}); set company.industry_framework")
        registry.get(FALLBACK)
        return FrameworkSelection(FALLBACK, "fallback", f"{label} matches no specific framework")
    registry.get(FALLBACK)
    return FrameworkSelection(FALLBACK, "fallback", "no SIC code available")
