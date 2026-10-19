"""Write the API's OpenAPI document to the Android app's snapshot, or check it.

The Android app's client is generated from ``android/api/openapi.json``, which
is committed, so that the app builds without a backend and a change to the
contract shows up as a diff in review. This keeps that file honest: it is the
schema ``app.main`` would serve at ``/api/v1/openapi.json``, built in process,
with no server and no database.

Usage::

    uv run python -m scripts.openapi            # rewrite the snapshot
    uv run python -m scripts.openapi --check    # fail if it is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.main import app

SNAPSHOT = Path(__file__).resolve().parents[2] / "android" / "api" / "openapi.json"


def render(schema: dict[str, Any]) -> str:
    """The document as it is committed: stable, diffable, one trailing newline."""
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the snapshot is out of date")
    args = parser.parse_args(argv)

    current = render(app.openapi())
    relative = SNAPSHOT.relative_to(SNAPSHOT.parents[2])

    if args.check:
        committed = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
        if committed != current:
            print(
                f"{relative} is out of date: the API's contract changed and the "
                "Android client is generated from that file.\n"
                "Run `make openapi` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{relative} matches the API.")
        return 0

    SNAPSHOT.write_text(current, encoding="utf-8")
    print(f"Wrote {relative}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
