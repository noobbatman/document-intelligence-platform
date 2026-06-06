#!/usr/bin/env python
"""Initialise the database schema and optionally seed demo data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.base import Base
from app.db.session import get_engine


def main() -> None:
    print("Creating database tables…")
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    print("Done.")


if __name__ == "__main__":
    main()
