#!/usr/bin/env python3
"""
Proof that tests/test_trust_cp5_regression.py's CP5-T11 check skips test_gates.py/test_hooks_waiver.py only
when tests/run_all.py says it already ran them, and still runs them itself in every other case.   python3 tests/test_run_all_dedupe.py

run_all.py runs every tests/test_*.py suite as its own subprocess, including test_gates.py and
test_hooks_waiver.py. test_trust_cp5_regression.py's CP5-T11 check also runs those same two files in full, as
a regression guard, which means every run_all.py run paid for them twice. run_all.py now sets RATCHET_RUN_ALL=1
and RATCHET_RUN_ALL_SUITES (every suite name in that run) on each suite's environment; CP5-T11 skips a file
only when it is named there and RATCHET_RUN_ALL is set.

This file lives outside the test_trust_cp* family on purpose: tests/test_split_check.py's CP2-T01 proves no
check(...) call was added to or rewarded in that family since the old tests/test_trust.py was split, so the
skip had to be folded into the one pre-existing T11 check(...) call instead of a new one, and its proof
belongs in its own file rather than a file that check guards.

Drives test_trust_cp5_regression.cp5_regression_existing_approval_suites_still_pass() directly with
subprocess.run stubbed out (never the real ~6-minute test_gates.py/test_hooks_waiver.py subprocesses), so this
stays fast. Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_trust_cp5_regression as cp5r  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(T\d+)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


class _StubResult:
    returncode = 0
    stdout = ""
    stderr = ""


def _run_guarded(env):
    """Runs the real T11 function with subprocess.run stubbed (records every command instead of running it)
    and the given RATCHET_RUN_ALL / RATCHET_RUN_ALL_SUITES, restoring both the function's view of the
    environment and subprocess.run no matter what."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _StubResult()

    saved_env = {k: os.environ.get(k) for k in ("RATCHET_RUN_ALL", "RATCHET_RUN_ALL_SUITES")}
    real_run = cp5r.subprocess.run
    cp5r.subprocess.run = fake_run
    try:
        for k in ("RATCHET_RUN_ALL", "RATCHET_RUN_ALL_SUITES"):
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in env.items() if v is not None})
        cp5r.PASS = cp5r.FAIL = 0  # trust_helpers.check() tallies into the module cp5r imported it from
        cp5r.trust_helpers.PASS = cp5r.trust_helpers.FAIL = 0
        cp5r.cp5_regression_existing_approval_suites_still_pass()
    finally:
        cp5r.subprocess.run = real_run
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return calls


def t01_unset_runs_both_as_before():
    print("T01: RATCHET_RUN_ALL unset behaves exactly as today -- both suites run as real subprocesses")
    calls = _run_guarded({})
    names = [Path(c[-1]).name for c in calls]
    check("[T01] both test_gates.py and test_hooks_waiver.py are still run as subprocesses",
          sorted(names) == sorted(cp5r.REGRESSION_SUITES), names)


def t02_both_covered_skips_both():
    print("T02: RATCHET_RUN_ALL set and both files named -- no subprocess re-run happens")
    calls = _run_guarded({"RATCHET_RUN_ALL": "1",
                          "RATCHET_RUN_ALL_SUITES": "test_gates.py,test_hooks_waiver.py,test_other.py"})
    check("[T02] no subprocess re-run happens", calls == [], calls)
    check("[T02] CP5-T11 still records both as passed", cp5r.trust_helpers.FAIL == 0 and cp5r.trust_helpers.PASS == 2,
          (cp5r.trust_helpers.PASS, cp5r.trust_helpers.FAIL))


def t03_subset_still_runs_the_missing_file():
    print("T03: RATCHET_RUN_ALL set but the run only named one of the two files -- the other still runs")
    calls = _run_guarded({"RATCHET_RUN_ALL": "1", "RATCHET_RUN_ALL_SUITES": "test_gates.py"})
    names = [Path(c[-1]).name for c in calls]
    check("[T03] only the uncovered file (test_hooks_waiver.py) is re-run", names == ["test_hooks_waiver.py"],
          names)


def t04_variable_unset_ignores_suites_list():
    print("T04: RATCHET_RUN_ALL_SUITES alone, without RATCHET_RUN_ALL, changes nothing")
    calls = _run_guarded({"RATCHET_RUN_ALL_SUITES": "test_gates.py,test_hooks_waiver.py"})
    names = [Path(c[-1]).name for c in calls]
    check("[T04] both suites still run (RATCHET_RUN_ALL itself was never set)",
          sorted(names) == sorted(cp5r.REGRESSION_SUITES), names)


TESTS = [
    ("T01", t01_unset_runs_both_as_before),
    ("T02", t02_both_covered_skips_both),
    ("T03", t03_subset_still_runs_the_missing_file),
    ("T04", t04_variable_unset_ignores_suites_list),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
