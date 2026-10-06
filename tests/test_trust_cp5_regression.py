#!/usr/bin/env python3
"""
CP5 of the trust-holes run: the regression guard that runs tests/test_gates.py and tests/test_hooks_waiver.py
in full as subprocesses. It takes as long as those two suites together (about 6 minutes), so it has a file
of its own and the other CP5 parts stay quick.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_regression.py
"""
import subprocess
import sys
import traceback
from pathlib import Path

import trust_helpers
from trust_helpers import check

SECTIONS = []


def cp5_regression_existing_approval_suites_still_pass():
    print("CP5: existing approval flows in test_gates.py and test_hooks_waiver.py still pass once they ask first")

    # --- T11: the full test_gates.py and test_hooks_waiver.py suites, run as real subprocesses, both pass in full ----
    here = Path(__file__).resolve().parent
    for name in ("test_gates.py", "test_hooks_waiver.py"):
        r = subprocess.run([sys.executable, str(here / name)], capture_output=True, text=True, timeout=600)
        tail = (r.stdout[-1500:] + r.stderr[-1500:])
        check(f"[CP5-T11] {name} passes in full (regression guard: CP5 must not break CP1-CP4, hooks-waiver or "
              "gate-integrity behavior)", r.returncode == 0, tail)


SECTIONS.append(("CP5", cp5_regression_existing_approval_suites_still_pass))


def main():
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            trust_helpers.FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{trust_helpers.PASS} passed, {trust_helpers.FAIL} failed")
    sys.exit(1 if trust_helpers.FAIL else 0)


if __name__ == "__main__":
    main()
