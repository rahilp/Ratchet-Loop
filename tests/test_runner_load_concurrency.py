#!/usr/bin/env python3
"""
CP3 tests: run_all.py caps how many suites it starts at once when the machine is already loaded.
    python3 tests/test_runner_load_concurrency.py

Like tests/test_runner_load.py (whose run_runner()/busy()/new_dir()/write_suite()/base_env()/QUICK fixtures this
file imports and reuses, rather than growing that file further), this drives the real tests/run_all.py as a
subprocess against throwaway folders of tiny quick-exit fake suites. Machine load is never real: every run sets
RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS so the worker cap is read from a deterministic fake reading, never from the
real host's load or core count.

Every case reads the worker count off the line main() prints:
    "Running N suites from DIR, W at a time, slowest first (timeout ...)."
through a small regex against the real subprocess's stdout -- never off wall-clock timing, and never a sleep.

This file does NOT touch tests/run_all.py's run_with_stretched_timeout() (a separate, already-existing per-call
timeout helper from CP1/CP2); it only exercises default_workers()/parse_args()'s worker-COUNT selection.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP3-T01", "status": "passed"|"failed"|"skipped", "message": "..."}]}.
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
import test_runner_load as trl  # noqa: E402  (reused sibling harness: run_runner/busy/new_dir/write_suite/base_env/QUICK)
import run_all  # noqa: E402  (default_workers() only -- never run_with_stretched_timeout())

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_CHECKS: dict = {}
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP3-[TX]\d+)\]")

WORKERS_RE = re.compile(r"Running (\d+) suites from .*, (\d+) at a time")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


def write_quick_suites(folder: Path, n: int) -> None:
    """n trivial quick-exit suites (print('quick'); exit 0), mirroring test_runner_load.QUICK."""
    for i in range(1, n + 1):
        trl.write_suite(folder, f"test_s{i:02d}.py", trl.QUICK)


def workers_started(run: "trl.Run"):
    """(suites, workers) parsed from the 'Running N suites from DIR, W at a time' line, or (None, None)."""
    m = WORKERS_RE.search(run.text)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


# ---- cases -----------------------------------------------------------------------------------------------------


def t01_explicit_workers_idle_unchanged():
    print("CP3-T01: explicit --workers is unchanged on an idle machine")
    d, x = trl.new_dir("cc-a"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "6"], trl.busy(x, "1", "8"))
    suites, workers = workers_started(run)
    check("[CP3-T01] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T01] 8 suites were found", suites == 8, suites)
    check(f"[CP3-T01] --workers 6 on an idle machine starts exactly 6 at a time (got {workers})", workers == 6,
          run.text[-300:])


def t02_explicit_workers_unreadable_unchanged():
    print("CP3-T02: explicit --workers is unchanged when load is unreadable")
    d, x = trl.new_dir("cc-b"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "6"], trl.busy(x, "unavailable", "4"))
    suites, workers = workers_started(run)
    check("[CP3-T02] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T02] --workers 6 with no usable load reading starts exactly 6 at a time (got {workers})",
          workers == 6, run.text[-300:])


def t03_default_workers_idle_unchanged():
    print("CP3-T03: default worker count is unchanged on an idle machine")
    n = run_all.default_workers() + 5
    d, x = trl.new_dir("cc-c"), trl.new_dir("cc-x")
    write_quick_suites(d, n)
    run = trl.run_runner(d, [], trl.busy(x, "0.3", "8"))
    suites, workers = workers_started(run)
    check("[CP3-T03] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T03] {n} suites were found", suites == n, suites)
    check(f"[CP3-T03] with no --workers on an idle machine the count equals default_workers()={run_all.default_workers()} "
          f"(got {workers})", workers == run_all.default_workers(), run.text[-300:])


def t04_default_workers_unreadable_unchanged():
    print("CP3-T04: default worker count is unchanged when load is unreadable")
    n = run_all.default_workers() + 5
    d, x = trl.new_dir("cc-d"), trl.new_dir("cc-x")
    write_quick_suites(d, n)
    run = trl.run_runner(d, [], trl.busy(x, "unavailable", "4"))
    suites, workers = workers_started(run)
    check("[CP3-T04] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T04] with no --workers and no usable load reading the count equals "
          f"default_workers()={run_all.default_workers()} (got {workers})", workers == run_all.default_workers(),
          run.text[-300:])


def t05_high_load_starts_fewer():
    print("CP3-T05: high simulated load starts fewer suites at once than requested")
    d, x = trl.new_dir("cc-e"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "8"], trl.busy(x, "32", "4"))  # load/cpus = 8
    suites, workers = workers_started(run)
    check("[CP3-T05] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T05] under heavy load (load/cpus=8) fewer than the requested 8 are started at once (got {workers})",
          workers < 8, run.text[-300:])
    check(f"[CP3-T05] but at least 2 are still started at once (got {workers})", workers >= 2, run.text[-300:])


def t06_floor_of_two():
    print("CP3-T06: the cap never drops below the floor of 2 even under extreme load")
    d, x = trl.new_dir("cc-f"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "8"], trl.busy(x, "1000", "4"))  # load/cpus = 250
    suites, workers = workers_started(run)
    check("[CP3-T06] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T06] under extreme simulated load (load/cpus=250) the count is exactly 2, never 0 or 1 (got {workers})",
          workers == 2, run.text[-300:])


def t07_monotonic_with_load():
    print("CP3-T07: the cap is a monotonic function of load, not a constant")
    d, x = trl.new_dir("cc-g"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    readings = (4, 16, 64)  # load/cpus = 1, 4, 16 with cpus fixed at 4
    workers_by_load = {}
    for load in readings:
        run = trl.run_runner(d, ["--workers", "8"], trl.busy(x, str(load), "4"))
        suites, workers = workers_started(run)
        check(f"[CP3-T07] setup: at load {load} the 'Running N suites ... W at a time' line was printed",
              workers is not None, run.text[-500:])
        if workers is None:
            return
        workers_by_load[load] = workers
    ordered = [workers_by_load[load] for load in readings]
    check(f"[CP3-T07] the worker count is non-increasing as load rises: {ordered}",
          all(ordered[i] >= ordered[i + 1] for i in range(len(ordered) - 1)), ordered)
    check(f"[CP3-T07] the count at the highest load is strictly lower than at the lowest: {ordered}",
          ordered[-1] < ordered[0], ordered)


def t08_cap_never_raises_the_count():
    print("CP3-T08: the cap never raises the count above what was requested, even at the zero-load edge")
    d, x = trl.new_dir("cc-h"), trl.new_dir("cc-x")
    write_quick_suites(d, 6)
    run = trl.run_runner(d, ["--workers", "4"], trl.busy(x, "0", "8"))  # load/cpus = 0
    suites, workers = workers_started(run)
    check("[CP3-T08] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T08] at the zero-load edge the count is exactly the requested 4, never more (got {workers})",
          workers == 4, run.text[-300:])


def t09_intermediate_factor_is_exact():
    print("CP3-T09: an intermediate, non-clamped stretch factor divides the count exactly, not a binary drop")
    d, x = trl.new_dir("cc-i"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "8"], trl.busy(x, "8", "4"))  # load/cpus = 2, well under max-stretch 4
    suites, workers = workers_started(run)
    check("[CP3-T09] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T09] at an intermediate factor of 2 (not clamped) 8 requested workers becomes exactly 4, "
          f"not a binary drop to the floor of 2 (got {workers})", workers == 4, run.text[-300:])
    check("[CP3-T09] the diagnostic line names the exact before/after/load/cpus "
          "('Worker count capped from 8 to 4 (load 8.0 on 4 CPUs).')",
          "Worker count capped from 8 to 4 (load 8.0 on 4 CPUs)." in run.text, run.text[-300:])


def t10_default_workers_capped_under_high_load():
    print("CP3-T10: the computed default worker count (no --workers) is also capped under high simulated load")
    default = run_all.default_workers()
    n = default + 5
    d, x = trl.new_dir("cc-j"), trl.new_dir("cc-x")
    write_quick_suites(d, n)
    run = trl.run_runner(d, [], trl.busy(x, "8", "4"))  # load/cpus = 2
    suites, workers = workers_started(run)
    check("[CP3-T10] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    expected = max(2, int(default / 2))
    check(f"[CP3-T10] with no --workers flag, under load the default count {default} is still capped to exactly "
          f"{expected} (got {workers})", workers == expected, run.text[-300:])
    if default > 2:
        check(f"[CP3-T10] the capped count is below the uncapped default {default} (got {workers})",
              workers < default, run.text[-300:])
    else:
        check(f"[CP3-T10] on a machine where default_workers() is already the floor ({default}), the capped "
              f"count holds at the floor of 2 (got {workers})", workers == 2, run.text[-300:])
    check(f"[CP3-T10] but still at least 2 (got {workers})", workers >= 2, run.text[-300:])


def t11_load_equals_cpus_is_intentional_noop():
    print("CP3-T11: at load == cpus exactly, the cap is a documented, intentional no-op (not an untested accident)")
    d, x = trl.new_dir("cc-k"), trl.new_dir("cc-x")
    write_quick_suites(d, 6)
    run = trl.run_runner(d, ["--workers", "6"], trl.busy(x, "12", "12"))  # load == cpus exactly
    suites, workers = workers_started(run)
    check("[CP3-T11] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T11] at load == cpus exactly, stretch_factor()'s floor of 1.0 means the count is left "
          f"unchanged at 6 by design, not reduced (got {workers})", workers == 6, run.text[-300:])
    check("[CP3-T11] the cap-triggered print line does not appear at load == cpus (no reduction happened)",
          "Worker count capped" not in run.text, run.text[-300:])


def t12_max_stretch_one_disables_cap():
    print("CP3-T12: --max-stretch 1 turns the worker cap off too, exactly as documented")
    d, x = trl.new_dir("cc-l"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "8", "--max-stretch", "1"], trl.busy(x, "32", "4"))  # load/cpus = 8
    suites, workers = workers_started(run)
    check("[CP3-T12] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T12] with --max-stretch 1, even under heavy load (load/cpus=8) the requested 8 is left "
          f"unchanged (got {workers})", workers == 8, run.text[-300:])
    check("[CP3-T12] the cap-triggered print line does not appear (the cap never fired)",
          "Worker count capped" not in run.text, run.text[-300:])


def t13_max_stretch_clamps_the_cap_factor():
    print("CP3-T13: --max-stretch also clamps the factor the worker cap divides by, not just the timeout stretch")
    d, x = trl.new_dir("cc-m"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "8", "--max-stretch", "2"], trl.busy(x, "16", "4"))  # load/cpus = 4
    suites, workers = workers_started(run)
    check("[CP3-T13] setup: the 'Running N suites ... W at a time' line was printed", workers is not None,
          run.text[-500:])
    if workers is None:
        return
    check(f"[CP3-T13] with --max-stretch 2 at load/cpus=4, the cap divides by the clamped factor 2 (not the "
          f"unclamped 4), so 8 becomes exactly 4 (got {workers})", workers == 4, run.text[-300:])


def t14_rounding_mode_is_truncation():
    print("CP3-T14: the cap's division truncates (int()), it does not round or ceil")
    d, x = trl.new_dir("cc-n"), trl.new_dir("cc-x")
    write_quick_suites(d, 8)
    run = trl.run_runner(d, ["--workers", "8"], trl.busy(x, "6", "4"))  # load/cpus = 1.5, factor 1.5
    suites, workers = workers_started(run)
    check("[CP3-T14] setup: at load/cpus=1.5 the 'Running N suites ... W at a time' line was printed",
          workers is not None, run.text[-500:])
    if workers is not None:
        check(f"[CP3-T14] 8 / 1.5 truncates to exactly 5, not 6 (which ceil would give) (got {workers})",
              workers == 5, run.text[-300:])

    run2 = trl.run_runner(d, ["--workers", "8"], trl.busy(x, "12", "4"))  # load/cpus = 3, factor 3
    suites2, workers2 = workers_started(run2)
    check("[CP3-T14] setup: at load/cpus=3 the 'Running N suites ... W at a time' line was printed",
          workers2 is not None, run2.text[-500:])
    if workers2 is not None:
        check(f"[CP3-T14] 8 / 3 truncates to exactly 2, not 3 (which round or ceil would give) (got {workers2})",
              workers2 == 2, run2.text[-300:])


def t15_workers_one_never_raised_and_len_bound_respected():
    print("CP3-T15: an explicit --workers 1 is never raised by the cap; a len-bounded count stays len-bounded")
    d1, x = trl.new_dir("cc-o"), trl.new_dir("cc-x")
    write_quick_suites(d1, 6)
    run = trl.run_runner(d1, ["--workers", "1"], trl.busy(x, "32", "4"))  # load/cpus = 8
    suites, workers = workers_started(run)
    check("[CP3-T15] setup: the 'Running N suites ... W at a time' line was printed (--workers 1)",
          workers is not None, run.text[-500:])
    if workers is not None:
        check(f"[CP3-T15] an explicit --workers 1 stays exactly 1 under heavy load, never raised to the floor "
              f"of 2 (got {workers})", workers == 1, run.text[-300:])

    d2 = trl.new_dir("cc-p")
    write_quick_suites(d2, 3)  # fewer suites than --workers requests
    run2 = trl.run_runner(d2, ["--workers", "8"], trl.busy(x, "8", "4"))  # load/cpus = 2
    suites2, workers2 = workers_started(run2)
    check("[CP3-T15] setup: the 'Running N suites ... W at a time' line was printed (3 suites, --workers 8)",
          workers2 is not None, run2.text[-500:])
    if workers2 is not None:
        check(f"[CP3-T15] 3 suites: {suites2} were found", suites2 == 3, suites2)
        check(f"[CP3-T15] with only 3 suites, the count is already len-bounded to 3 before the cap divides by "
              f"the factor 2, giving exactly 2 -- not 4 (which dividing the unbounded --workers 8 by the same "
              f"factor would give) (got {workers2})", workers2 == 2, run2.text[-300:])


TESTS = [
    ("CP3-T01", t01_explicit_workers_idle_unchanged),
    ("CP3-T02", t02_explicit_workers_unreadable_unchanged),
    ("CP3-T03", t03_default_workers_idle_unchanged),
    ("CP3-T04", t04_default_workers_unreadable_unchanged),
    ("CP3-T05", t05_high_load_starts_fewer),
    ("CP3-T06", t06_floor_of_two),
    ("CP3-T07", t07_monotonic_with_load),
    ("CP3-T08", t08_cap_never_raises_the_count),
    ("CP3-T09", t09_intermediate_factor_is_exact),
    ("CP3-T10", t10_default_workers_capped_under_high_load),
    ("CP3-T11", t11_load_equals_cpus_is_intentional_noop),
    ("CP3-T12", t12_max_stretch_one_disables_cap),
    ("CP3-T13", t13_max_stretch_clamps_the_cap_factor),
    ("CP3-T14", t14_rounding_mode_is_truncation),
    ("CP3-T15", t15_workers_one_never_raised_and_len_bound_respected),
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
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
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
