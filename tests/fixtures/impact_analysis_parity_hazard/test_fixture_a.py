#!/usr/bin/env python3
"""CP4 fixture: a tiny standalone 'report-writing suite' used only as plain text inside a planned
tests.command string for the hazard-check tests in tests/test_impact_analysis_parity_hazard.py. The
hazard check is a pure text-pattern analysis over the tests.command string -- it never executes this
file -- so this script's only job is to exist, under tests/fixtures/ (never under the real tests/
root, where tests/run_all.py's non-recursive test_*.py glob would otherwise pick it up as a real
suite), and to look like the kind of script the hazard check is watching for: one that writes a
per-test JSON report to RATCHET_TEST_REPORT when that env var is set."""
import json
import os


def main() -> int:
    path = os.environ.get("RATCHET_TEST_REPORT")
    if path:
        with open(path, "w") as f:
            json.dump({"cases": [{"id": "FIXTURE-A", "status": "passed", "message": ""}]}, f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
