#!/usr/bin/env python3
"""CP4 fixture: the second of three tiny standalone 'report-writing suite' fixtures (see
test_fixture_a.py for the shared rationale). Used only as plain text inside a planned tests.command
string; never actually executed by the hazard check."""
import json
import os


def main() -> int:
    path = os.environ.get("RATCHET_TEST_REPORT")
    if path:
        with open(path, "w") as f:
            json.dump({"cases": [{"id": "FIXTURE-B", "status": "passed", "message": ""}]}, f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
