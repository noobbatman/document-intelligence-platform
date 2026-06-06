"""Compute edit-rate improvement before and after learned preferences."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(payload: dict) -> dict:
    before = payload.get("before", [])
    after = payload.get("after", [])

    def edit_rate(rows: list[dict]) -> float:
        if not rows:
            return 0.0
        edited = sum(1 for row in rows if row.get("edited"))
        return edited / len(rows)

    before_rate = edit_rate(before)
    after_rate = edit_rate(after)
    drop = before_rate - after_rate
    return {
        "before_edit_rate": round(before_rate, 4),
        "after_edit_rate": round(after_rate, 4),
        "absolute_drop": round(drop, 4),
        "relative_drop": round(drop / before_rate, 4) if before_rate else 0.0,
        "target_met": (drop / before_rate) >= 0.2 if before_rate else False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.payload.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
