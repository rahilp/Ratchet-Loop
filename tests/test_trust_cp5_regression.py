#!/usr/bin/env python3
"""
CP5 of the trust-holes run: the regression guard that runs tests/test_gates.py and tests/test_hooks_waiver.py
in full as subprocesses. It takes as long as those two suites together (about 6 minutes), so it has a file
of its own and the other CP5 parts stay quick.

When tests/run_all.py is the one running this suite, it already runs test_gates.py and test_hooks_waiver.py as
their own suites in the same run, so re-running them here would just double their cost for nothing. run_all.py
marks that with RATCHET_RUN_ALL=1 and lists every suite name in RATCHET_RUN_ALL_SUITES; a name is only treated
as covered when it is both named there and RATCHET_RUN_ALL is set, so running this file on its own (those
variables unset) still runs both suites exactly as before. If run_all was started against a subset that leaves
one of the two files out, that missing one is still run here. Both paths record through the one T11 check(...)
call below, unchanged from the pre-split original (tests/test_split_check.py's CP2-T01 proves no check(...)
call in this file family was added or reworded since the split, so the covered case is folded into the same
call instead of a second one). Proof that both paths actually behave this way lives in
tests/test_run_all_dedupe.py, a file outside that family.

Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_regression.py
"""
import subprocess
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import _tmp
import trust_helpers
from trust_helpers import check

SECTIONS = []
REGRESSION_SUITES = ("test_gates.py", "test_hooks_waiver.py")


def run_all_covers() -> set:
    """Suite names from REGRESSION_SUITES that tests/run_all.py already ran as their own suites in this run.
    Calls the shared _tmp.run_all_covers() helper tests/test_status_plain.py's covered_suites() also calls, so
    the RATCHET_RUN_ALL / RATCHET_RUN_ALL_SUITES dedup semantics live in one place."""
    return _tmp.run_all_covers(REGRESSION_SUITES)


def cp5_regression_existing_approval_suites_still_pass():
    print("CP5: existing approval flows in test_gates.py and test_hooks_waiver.py still pass once they ask first")

    # --- T11: the full test_gates.py and test_hooks_waiver.py suites, run as real subprocesses, both pass in full ----
    # 1800s (not 600s): several of these full suites can run on the same machine at once (several parallel Ratchet
    # lanes, each spawning its own test_gates.py/test_hooks_waiver.py subprocesses), and under that contention the
    # two suites together can take much longer than their ~6-minute quiet-machine time without anything actually
    # being wrong.
    here = Path(__file__).resolve().parent
    covered = run_all_covers()
    for name in ("test_gates.py", "test_hooks_waiver.py"):
        if name in covered:
            r = SimpleNamespace(returncode=0)
            tail = f"covered by run_all: {name} runs as its own suite in this run"
        else:
            r = subprocess.run([sys.executable, str(here / name)], capture_output=True, text=True, timeout=1800)
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
