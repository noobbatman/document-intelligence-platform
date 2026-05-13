"""Small config loader for YAML-compatible project config files."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=64)
def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    raw = config_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(raw)
    except Exception:
        loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"Config {config_path} must contain a mapping at the top level.")
    return loaded
