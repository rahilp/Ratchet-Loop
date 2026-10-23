#!/usr/bin/env python3
"""
CP4: no test in this suite reads the real machine's load or CPU count.
    python3 tests/test_load_determinism.py

tests/test_status_plain.py's CP2-T01/CP2-T02/CP2-T10 and tests/test_install.py's CP1-T01/CP1-T02 are supposed to
give the same exact floor timeout (120s for an engine CLI call, 590s for a nested test_gates.py/test_hooks.py
run) on any machine, however busy that machine genuinely is, for their "unset"/"idle"/default-load sub-case.
Running the whole, slow test_status_plain.py suite under genuine machine load to prove that is not practical as a
narrow, fast command (it also re-runs test_gates.py/test_hooks.py in full as CP3-T21). Instead, this file imports
tests/test_status_plain.py and tests/test_install.py as sibling modules (both guard everything heavy behind
`if __name__ == "__main__":`, the same convention tests/test_runner_load_fixes.py already relies on for
tests/run_all.py) and calls their individual CP2-T01/T02/T10 and CP1-T01/T02 case functions directly, each under
`unittest.mock.patch` on `os.getloadavg`/`os.cpu_count` (NEVER the RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS
environment variables, which are explicitly popped for the duration of each call) set to a fixed, heavily-loaded
reading (load 64.0 on 4 CPUs -- a 16x ratio that saturates run_all.DEFAULT_MAX_STRETCH=4.0). That is the one
condition that exposes the defect this checkpoint fixes: on the code as it stood before this checkpoint, the
default/"unset"/"idle" sub-case reads the real host through that same os-level fallback and genuinely stretches
past the floor (480s/2360s instead of 120s/590s) under this mock, exactly as it would on a genuinely busy real
machine -- this file makes that reproducible on demand, deterministically, with no real busy machine required.

Each targeted case function still runs its OWN check(...) calls (bookkeeping PASS/FAIL and CASE_STATUS in that
sibling module, unaffected by this file), but a dynamically-computed "expected" value inside a sibling case (as
CP2-T10's "idle" sub-case computes from a live run_all.stretch_factor() reading) would, under this same ambient
os-mock, match whatever the (possibly still-buggy) actual reading is and so could pass even before the fix --
self-referential, not a real proof. So every case here instead wraps the sibling module's `check` function for the
duration of the call, captures the literal `detail` value passed for the one relevant check (the real
`r.timeout_limit`/`result.timeout_limit` number that case observed), and asserts THAT literal number equals the
exact floor (120.0 or 590.0) itself -- never trusting the sibling's own embedded pass/fail judgment for that one
case, the same way a human reviewer would re-derive the answer instead of trusting a self-checking computation.

CP4-T04 proves test_install.py's CP1-T01/CP1-T02 are unaffected by the identical ambient os-mock (they already
pass an explicit, non-None RATCHET_FAKE_LOADAVG, so os.getloadavg/os.cpu_count are never consulted there) --
a regression guard against a later, unneeded "fix" to a file that was never broken. CP4-T05 is a static,
AST-based guard (reusing tests/test_status_plain.py's own `collect_check_pairs()`, the same helper its CP2-T11
case uses) pinning every check(...) call site in both tests/test_status_plain.py and tests/test_install.py to
their content at this checkpoint's own starting commit, so a fix cannot silently reword, weaken, or delete an
existing check while adding the new idle-mock setup around it. CP4-T06 checks README.md's "### Running the
tests" section names both of this lane's new load-aware suites, and that tests/test_docs.py's own
cp6_tests_documented() check (its CP6-T05 proof) agrees for every tests/test_*.py file really on disk.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP4-T01", "status": "passed"|"failed"|"skipped", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from collections import Counter
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import run_all  # noqa: E402
import test_status_plain  # noqa: E402  (guards everything heavy behind `if __name__ == "__main__":`)
import test_install  # noqa: E402  (same)
import test_docs  # noqa: E402  (same)
import cp4_t05_base_pairs  # noqa: E402  (checked-in baseline for CP4-T05, generated once from BASE_COMMIT)

# The commit this checkpoint (CP4) started from -- right after CP3's own commit landed on this branch. CP4-T05
# compares against tests/cp4_t05_base_pairs.py's checked-in PAIRS_BY_FILE (generated once from this commit via
# `git show`) instead of shelling out to git history at test time, the same fix tests/cp2_t11_base_pairs.py
# already applies for CP2-T11: a shallow CI clone (fetch-depth 1, actions/checkout's default) has no access to
# this commit once a later commit is checked out, and a historical `git show` would otherwise make the guard
# permanently and unrelatedly red (codex-standards/X1, codex-correctness/X1, CP4 adversarial round 1).
CP4_BASE_COMMIT = "0d3fd10acdf24f3c9f1d2dfecd3c559f03c7b9cf"

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_CHECKS: dict = {}
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP4-T\d+)\]")

HIGH_LOAD = (64.0, 64.0, 64.0)  # os.getloadavg()'s 3-tuple; only [0] (the one-minute average) is read
HIGH_CPUS = 4  # load/cpus = 16, saturating run_all.DEFAULT_MAX_STRETCH (4.0)

PROJECT = None  # an approved throwaway plan project, built once in main(), reused by every rx()-calling case


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"), flush=True)


def _capture_matching_checks(module, prefix):
    """Temporarily wraps `module.check` so every call whose name starts with `prefix` is ALSO recorded as
    (name, cond, detail) in the returned list, while still forwarding to the module's real check() underneath (so
    that module's own PASS/FAIL/CASE_STATUS bookkeeping for its own report is unaffected by this file calling its
    case functions directly). Returns (captured, original); restore with `module.check = original` when done,
    even if the call being wrapped raises."""
    original = module.check
    captured = []

    def wrapper(name, cond, detail=""):
        if name.startswith(prefix):
            captured.append((name, cond, detail))
        return original(name, cond, detail)

    module.check = wrapper
    return captured, original


def _high_load_mock():
    """The one ambient fake-busy-host context every CP4-T01..T04 case runs its sibling call under: a fixed,
    heavily loaded os.getloadavg()/os.cpu_count() reading (never RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS, which
    stay popped so this mock -- the real os-level fallback read_load() falls back to -- is what is actually
    exercised)."""
    return mock.patch("os.getloadavg", return_value=HIGH_LOAD), mock.patch("os.cpu_count", return_value=HIGH_CPUS)


def cp4_t01():
    print("CP4-T01: CP2-T01's default ('unset') sub-case still gives rx() exactly 120s when the real host looks "
          "heavily loaded")
    captured, original = _capture_matching_checks(test_status_plain, "[CP2-T01] unset:")
    saved_fake = _tmp.set_fake_load(None, None)
    load_mock, cpus_mock = _high_load_mock()
    try:
        with load_mock, cpus_mock:
            test_status_plain.cp2_t01(PROJECT)
    finally:
        test_status_plain.check = original
        _tmp.restore_env(saved_fake)
    hits = [(n, c, d) for n, c, d in captured if "subprocess timeout is exactly 120" in n]
    check("[CP4-T01] setup: CP2-T01's 'unset' sub-case made its own timeout check under the mocked host",
          len(hits) == 1, captured)
    detail = hits[0][2] if hits else None
    check(f"[CP4-T01] rx()'s effective timeout for the 'unset' sub-case is exactly 120 (never the stretched 480 "
          f"a load-64-on-4-cpus host would otherwise give it): got {detail}",
          detail == 120, detail)


def cp4_t02():
    print("CP4-T02: CP2-T02's default ('unset') sub-case still gives CP3-T21's test_hooks.py run exactly 590s "
          "when the real host looks heavily loaded")
    captured, original = _capture_matching_checks(test_status_plain, "[CP2-T02] unset:")
    saved_fake = _tmp.set_fake_load(None, None)
    saved_run_all = {k: os.environ.get(k) for k in ("RATCHET_RUN_ALL", "RATCHET_RUN_ALL_SUITES")}
    os.environ.pop("RATCHET_RUN_ALL", None)  # the suite must actually run (stubbed), never be skipped as "covered"
    os.environ.pop("RATCHET_RUN_ALL_SUITES", None)
    load_mock, cpus_mock = _high_load_mock()
    try:
        with load_mock, cpus_mock:
            test_status_plain.cp2_t02()
    finally:
        test_status_plain.check = original
        _tmp.restore_env(saved_fake)
        _tmp.restore_env(saved_run_all)
    hits = [(n, c, d) for n, c, d in captured if "timeout used for test_hooks.py is exactly 590" in n]
    check("[CP4-T02] setup: CP2-T02's 'unset' sub-case made its own timeout check under the mocked host",
          len(hits) == 1, captured)
    detail = hits[0][2] if hits else None
    check(f"[CP4-T02] the timeout used for test_hooks.py in the 'unset' sub-case is exactly 590 (never the "
          f"stretched 2360 a load-64-on-4-cpus host would otherwise give it): got {detail}",
          detail == 590, detail)


def cp4_t03():
    print("CP4-T03: CP2-T10's 'idle' sub-case still gives both rx() and CP3-T21 their exact floor timeouts "
          "(120/590) when the real host looks heavily loaded")
    captured, original = _capture_matching_checks(test_status_plain, "[CP2-T10] idle:")
    saved_fake = _tmp.set_fake_load(None, None)
    load_mock, cpus_mock = _high_load_mock()
    try:
        with load_mock, cpus_mock:
            test_status_plain.cp2_t10(PROJECT)
    finally:
        test_status_plain.check = original
        _tmp.restore_env(saved_fake)
    rx_hits = [(n, c, d) for n, c, d in captured if "rx()'s effective timeout equals exactly" in n]
    cp3_hits = [(n, c, d) for n, c, d in captured
                if "CP3-T21's effective timeout (for a suite that actually runs) equals exactly" in n]
    check("[CP4-T03] setup: CP2-T10's 'idle' sub-case made both its own effective-timeout checks under the "
          "mocked host", len(rx_hits) == 1 and len(cp3_hits) == 1, captured)
    rx_detail = rx_hits[0][2] if rx_hits else None
    cp3_detail = cp3_hits[0][2] if cp3_hits else None
    # CP2-T10's own "expected" value for this label is computed live from run_all.stretch_factor() -- under this
    # same ambient mock, that self-computed expectation would match whatever the (possibly still-buggy) actual
    # reading is, so CP2-T10's own cond cannot be trusted as this checkpoint's proof. The literal detail value
    # (the real r.timeout_limit/result.timeout_limit observed) is compared against the fixed floor directly here.
    check(f"[CP4-T03] rx()'s own effective timeout for the 'idle' sub-case is exactly 120.0 (never the stretched "
          f"480.0 a load-64-on-4-cpus host would otherwise give it): got {rx_detail}",
          rx_detail == 120.0, rx_detail)
    check(f"[CP4-T03] CP3-T21's own effective timeout for the 'idle' sub-case is exactly 590.0 (never the "
          f"stretched 2360.0 a load-64-on-4-cpus host would otherwise give it): got {cp3_detail}",
          cp3_detail == 590.0, cp3_detail)


def cp4_t04():
    print("CP4-T04: test_install.py's CP1-T01 and CP1-T02 stay exactly 120s when the real host looks heavily "
          "loaded -- no fix needed there")
    load_mock, cpus_mock = _high_load_mock()
    captured1, original1 = _capture_matching_checks(test_install, "[CP1-T01]")
    try:
        with load_mock, cpus_mock:
            test_install.cp1_t01()
    finally:
        test_install.check = original1
    load_mock, cpus_mock = _high_load_mock()
    captured2, original2 = _capture_matching_checks(test_install, "[CP1-T02]")
    try:
        with load_mock, cpus_mock:
            test_install.cp1_t02()
    finally:
        test_install.check = original2

    timeout_hits1 = [(n, c, d) for n, c, d in captured1 if "subprocess timeout at exactly 120" in n]
    timeout_hits2 = [(n, c, d) for n, c, d in captured2 if "subprocess timeout at exactly 120" in n]
    check("[CP4-T04] setup: CP1-T01 and CP1-T02 each made their own timeout check under the mocked host",
          len(timeout_hits1) == 1 and len(timeout_hits2) == 1, (captured1, captured2))
    d1 = timeout_hits1[0][2] if timeout_hits1 else None
    d2 = timeout_hits2[0][2] if timeout_hits2 else None
    check(f"[CP4-T04] CP1-T01 (explicit RATCHET_FAKE_LOADAVG=1, RATCHET_FAKE_CPUS=8) keeps its timeout at exactly "
          f"120 under the mocked host, which it never consults: got {d1}", d1 == 120, d1)
    check(f"[CP4-T04] CP1-T02 (explicit RATCHET_FAKE_LOADAVG=unavailable) keeps its timeout at exactly 120 under "
          f"the mocked host, which it never consults: got {d2}", d2 == 120, d2)


def _pinned_pairs(relpath):
    return Counter(tuple(p) for p in cp4_t05_base_pairs.PAIRS_BY_FILE[relpath])


def cp4_t05():
    print("CP4-T05: static guard -- the fix changes no existing check(...) call's name or condition in "
          "tests/test_status_plain.py or tests/test_install.py")
    check("[CP4-T05] setup: the checked-in base fixture (tests/cp4_t05_base_pairs.py) pins the exact base "
          "commit this guard freezes",
          cp4_t05_base_pairs.BASE_COMMIT == CP4_BASE_COMMIT,
          (cp4_t05_base_pairs.BASE_COMMIT, CP4_BASE_COMMIT))
    for relpath in ("tests/test_status_plain.py", "tests/test_install.py"):
        pinned_pairs = _pinned_pairs(relpath)
        check(f"[CP4-T05] setup: {relpath}'s pinned base commit contains at least one check(...) call",
              sum(pinned_pairs.values()) > 0, sum(pinned_pairs.values()))
        current_src = (ROOT / relpath).read_text()
        current_pairs = Counter(test_status_plain.collect_check_pairs(current_src, str(ROOT / relpath)))
        missing = pinned_pairs - current_pairs
        check(f"[CP4-T05] every check(...) call from {relpath} at this checkpoint's starting commit is still "
              "present, unreworded, with its original condition (exact multiset match; new calls may be ADDED "
              "for the idle-mock fix, but none existing may be removed, reworded, or narrowed)",
              not missing, list(missing.items())[:5])


def cp4_t06():
    print("CP4-T06: README documents both of this lane's load-aware test files, and test_docs.py's own doc "
          "check passes for every tests/test_*.py file on disk")
    readme = (ROOT / "README.md").read_text()
    files = test_docs.cp6_tests_on_disk()
    check("[CP4-T06] setup: tests/test_load_determinism.py (this file) is itself among the files test_docs.py "
          "found on disk", "test_load_determinism.py" in files, files)
    documented = test_docs.cp6_tests_documented(readme, files)
    check("[CP4-T06] test_docs.cp6_tests_documented(README, every tests/test_*.py on disk) returns true",
          documented is True, [f for f in files if f not in readme])
    for fname in ("test_runner_load_concurrency.py", "test_load_determinism.py"):
        found = re.search(r"(?m)^python3?\s+tests/" + re.escape(fname) + r"\s*$", readme) is not None
        check(f"[CP4-T06] the 'Running the tests' command list names python3 tests/{fname} on its own line",
              found, fname)


TESTS = [
    ("CP4-T01", cp4_t01),
    ("CP4-T02", cp4_t02),
    ("CP4-T03", cp4_t03),
    ("CP4-T04", cp4_t04),
    ("CP4-T05", cp4_t05),
    ("CP4-T06", cp4_t06),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return

    def status(cid):
        if cid in CASE_SKIPPED:
            return "skipped"
        return "passed" if CASE_STATUS.get(cid, False) else "failed"

    cases = [{"id": cid, "status": status(cid),
              "message": "not run: it made no checks" if status(cid) == "skipped"
              else "" if status(cid) == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL, PROJECT
    PROJECT = test_status_plain.plan_project(True)
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(e).__name__}: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        else:
            if not CASE_CHECKS.get(cid):
                CASE_SKIPPED.add(cid)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
