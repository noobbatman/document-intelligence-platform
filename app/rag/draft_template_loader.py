"""Draft template loading for config-driven drafting."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.utils.config_loader import load_config

TEMPLATE_DIR = Path(__file__).with_name("draft_templates")


@lru_cache(maxsize=32)
def load_draft_template(draft_type: str) -> dict[str, Any] | None:
    path = TEMPLATE_DIR / f"{draft_type}.yaml"
    if not path.exists():
        return None
    return load_config(str(path))
