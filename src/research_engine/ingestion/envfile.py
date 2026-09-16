from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path, *, override: bool = False) -> list[str]:
    """Minimal KEY=VALUE loader (no shell expansion). Returns the keys it set.
    Existing environment variables win unless override=True."""
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            raise ValueError(f"{path}:{lineno}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key.isidentifier():
            raise ValueError(f"{path}:{lineno}: invalid variable name {key!r}")
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
