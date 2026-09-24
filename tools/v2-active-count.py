"""In số job V2 đang active; helper nhỏ cho deferred safe restart."""

from __future__ import annotations

import sqlite3
import sys


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    with sqlite3.connect(sys.argv[1]) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN "
            "('queued','transcribing','recapping')"
        ).fetchone()
    print(int(row[0]) if row else 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
