from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from ..errors import ConfigError, format_validation_error
from ..schemas.company import ProjectConfig


def _resolve_local_paths(data: dict, base: Path) -> None:
    """Relative `path:` sources are resolved against the config file's directory,
    so a project behaves the same regardless of the working directory."""
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return
    for value in sources.values():
        refs = value if isinstance(value, list) else [value]
        for ref in refs:
            if isinstance(ref, dict) and ref.get("path"):
                p = Path(str(ref["path"])).expanduser()
                ref["path"] = str(p if p.is_absolute() else (base / p).resolve())


def load_project_config(path: str | Path) -> ProjectConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    _resolve_local_paths(data, path.parent.resolve())
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: invalid configuration\n{format_validation_error(exc)}") from None
