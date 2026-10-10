#!/usr/bin/env python3
"""Make a consistent copy of the local statutes SQLite database for deployment."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "statutes.sqlite3"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Destination outside the live data directory")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output == SOURCE.resolve():
        parser.error("output must differ from the live database")
    if output.exists():
        parser.error("output already exists; choose a new filename")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True) as src:
            with sqlite3.connect(output) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("snapshot integrity check failed")
                count = dst.execute("SELECT COUNT(*) FROM statutes").fetchone()[0]
                if count == 0:
                    raise RuntimeError("snapshot has no statutes")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    print(f"Snapshot ready: {output} ({count} statutes)")


if __name__ == "__main__":
    main()
