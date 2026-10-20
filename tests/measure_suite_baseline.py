#!/usr/bin/env python3
"""
CP1: record today's baseline timings.   python3 -I tests/measure_suite_baseline.py --suites F1,F2,... [--out PATH]

This file is both the production timing harness Ratchet contributors run to time one or more
tests/test_*.py suites, and its own self-test. Run with --suites (a comma-separated list of suite
paths) and --out (where to record the measurement as JSON), it runs each given suite exactly once
as its own subprocess (`python3 <suite>`, same invocation tests/run_all.py uses internally), times
it with a real wall clock (each suite under its own explicit timeout, so a wedged suite is recorded
as a clean failure instead of hanging), and writes --out holding, for every suite, its elapsed
seconds and whether it passed, plus a reproducible command line that re-measures the same suites
(deliberately without --out, so replaying it never overwrites the evidence file it produced).

When $RATCHET_TEST_REPORT is set, invoking this file (with any --suites value; the checkpoint's own
fixed command always passes the three real target suites) instead runs its own self-tests
(CP1-T01..CP1-T08): most are fast, using small synthetic "fake suite" scripts built in a private
temp dir (tests/_tmp.py), each driven through this exact same --suites/--out CLI entry point (via a
nested subprocess call, so a harness that is not yet implemented fails that one case cleanly
instead of crashing the self-test process); the last case (CP1-T08) is the one genuinely slow case,
driving the same CLI entry point against the three real target suites named in the checkpoint's
command, and is what produces this run's real baseline_timings.json evidence. Every case's result is
written to $RATCHET_TEST_REPORT:
{"cases": [{"id": "CP1-T01", "status": "passed"|"failed", "message": "..."}]}.

Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARNESS = Path(__file__).resolve()

sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402  (tests/_tmp.py; every suite's temp folders go through this, never raw tempfile)

# The checkpoint's own fixed command names exactly these three real suites, and this run's own real
# evidence path -- used only by CP1-T08, never by the fast self-test cases above it.
REAL_SUITES = ["tests/test_trust_cp5_d.py", "tests/test_trust_cp5_e.py", "tests/test_quota_shared.py"]
REAL_OUT = ROOT / ".ratchet/runs/make-tests-test-trust-cp5-d-py-tests-test-trust/evidence/baseline_timings.json"

# The scout's already-measured solo figures: reference data only (printed for a human in CP1-T08), never a
# pass/fail gate on a real suite's elapsed time -- this plan's whole purpose (CP2-CP4) is to make these
# suites fast, so a correct measurement after conversion legitimately falls outside these old windows.
PLAUSIBLE_RANGES = {
    "tests/test_trust_cp5_e.py": (80.0, 170.0),
    "tests/test_trust_cp5_d.py": (380.0, 650.0),
    "tests/test_quota_shared.py": (250.0, 1200.0),  # "at least ~280s"; no firm upper bound was solo-measured
}

# Per-suite timeout for the production subprocess.run inside measure_suites (used for every real suite named
# in PLAUSIBLE_RANGES, including when CP1-T08 drives the three real target suites through it): generous enough
# to absorb this repo's own recorded ~1.8x load stretch, short enough to still catch a genuine hang. Derived
# from the scout's solo ceilings with a wide margin, not re-measured.
SUITE_TIMEOUT_SEC = {rel: hi * 2 + 300 for rel, (_lo, hi) in PLAUSIBLE_RANGES.items()}
# Fallback for a suite not in PLAUSIBLE_RANGES (a contributor's own suite, or a fake suite in the fast
# self-tests above, which finish in well under a second and never approach this).
DEFAULT_SUITE_TIMEOUT_SEC = 1800.0

# CP1-T08's own harness-level subprocess timeout must sit above the sum of every real suite's own timeout
# above, plus margin, so the harness can let each suite time out individually and exit cleanly on its own
# instead of being killed from outside (which would also orphan a still-running suite subprocess).
T08_HARNESS_TIMEOUT_SEC = sum(SUITE_TIMEOUT_SEC.values()) + 600.0

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}


def check(case_id: str, name: str, cond: bool, detail="") -> bool:
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    CASE_STATUS[case_id] = CASE_STATUS.get(case_id, True) and bool(cond)
    if not cond:
        CASE_MESSAGES.setdefault(case_id, []).append(f"{name}: {str(detail)[:400]}")
    print(f"  {'OK' if cond else 'XX'} [{case_id}] {name}" + ("" if cond else f"   {str(detail)[:400]}"))
    return bool(cond)


# ---------------------------------------------------------------------------------------------
# Fake suite fixtures (CP1-T01..CP1-T07 only; CP1-T08 uses the real target suites above).
# ---------------------------------------------------------------------------------------------

FAKE_PASS_FAST = """import sys, time
time.sleep(0.3)
sys.exit(0)
"""

FAKE_PASS_SLOW = """import sys, time
time.sleep(1.5)
sys.exit(0)
"""

FAKE_FAIL = """import sys, time
time.sleep(0.15)
sys.exit(1)
"""


def make_fake_suites(d) -> dict:
    """Writes the three fake suite scripts into temp dir `d`; returns {name: absolute path str}."""
    paths = {}
    for name, body in (("fake_pass_fast.py", FAKE_PASS_FAST), ("fake_pass_slow.py", FAKE_PASS_SLOW),
                        ("fake_fail.py", FAKE_FAIL)):
        p = Path(d) / name
        p.write_text(body, encoding="utf-8")
        paths[name] = str(p)
    return paths


def run_harness(suites_arg: str, out_path, timeout: float) -> tuple:
    """Invokes this same file's CLI entry point as a fresh subprocess (never recursing into self-test
    mode: $RATCHET_TEST_REPORT is stripped from its environment), the same way a real caller would.
    Returns (returncode, stdout, stderr, wall_clock_sec); never raises on a non-zero exit or on the
    harness not yet existing/being implemented -- a timeout or crash is turned into a clearly-failing
    result tuple instead, so a case that drives this can fail on an assertion, not crash."""
    env = dict(os.environ)
    env.pop("RATCHET_TEST_REPORT", None)
    cmd = [sys.executable, "-I", str(HARNESS), "--suites", suites_arg]
    if out_path is not None:
        cmd += ["--out", str(out_path)]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=timeout)
        wall = time.monotonic() - t0
        return r.returncode, r.stdout, r.stderr, wall
    except subprocess.TimeoutExpired as e:
        wall = time.monotonic() - t0
        return 124, (e.stdout or "") if isinstance(e.stdout, str) else "", \
            (e.stderr or "") if isinstance(e.stderr, str) else "", wall
    except Exception as e:  # the harness crashing to start at all is still a clean failed result, not a crash here
        wall = time.monotonic() - t0
        return 126, "", f"{type(e).__name__}: {e}", wall


def read_out(out_path):
    try:
        return json.loads(Path(out_path).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "no --out file was written"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------------------------
# CP1-T01..CP1-T08
# ---------------------------------------------------------------------------------------------


def cp1_t01():
    cid = "CP1-T01"
    with _tmp.mkdtemp(prefix="cp1-t01-") as d:
        fakes = make_fake_suites(d)
        suites_arg = f"{fakes['fake_pass_fast.py']},{fakes['fake_pass_slow.py']}"
        out_path = Path(d) / "out.json"
        rc, out, err, wall = run_harness(suites_arg, out_path, timeout=30)
        ok = check(cid, "exits 0 for two passing suites", rc == 0, (rc, out, err))
        for name, lo, hi in ((fakes["fake_pass_fast.py"], 0.05, 3.0), (fakes["fake_pass_slow.py"], 0.5, 5.0)):
            base = Path(name).name
            m = re.search(re.escape(base) + r".{0,40}?([0-9]+\.?[0-9]*)\s*s\b", out)
            found = float(m.group(1)) if m else None
            ok = check(cid, f"prints {base}'s elapsed time in seconds, within a generous tolerance",
                       found is not None and lo <= found <= hi, (out, found)) and ok


def cp1_t02():
    cid = "CP1-T02"
    with _tmp.mkdtemp(prefix="cp1-t02-") as d:
        fakes = make_fake_suites(d)
        fast, slow = fakes["fake_pass_fast.py"], fakes["fake_pass_slow.py"]
        suites_arg = f"{fast},{slow}"
        out_path = Path(d) / "out.json"
        rc, out, err, wall = run_harness(suites_arg, out_path, timeout=30)
        data, why = read_out(out_path)
        if not check(cid, "the --out file is valid JSON", data is not None, why or ""):
            return
        suites = data.get("suites") if isinstance(data, dict) else None
        check(cid, "holds an elapsed-seconds number for every suite given",
              isinstance(suites, dict) and all(
                  isinstance(suites.get(Path(p).name, suites.get(p)), dict)
                  and isinstance((suites.get(Path(p).name) or suites.get(p) or {}).get("elapsed_sec"), (int, float))
                  for p in (fast, slow)),
              suites)
        cmd_field = data.get("command") if isinstance(data, dict) else None
        reproducible = isinstance(cmd_field, str) and "--suites" in cmd_field and \
            Path(fast).name in cmd_field and Path(slow).name in cmd_field
        check(cid, "records the exact command used, complete enough to re-measure the same suites",
              reproducible, cmd_field)


def cp1_t03():
    cid = "CP1-T03"
    with _tmp.mkdtemp(prefix="cp1-t03-") as d:
        fakes = make_fake_suites(d)
        fast, fail = fakes["fake_pass_fast.py"], fakes["fake_fail.py"]
        suites_arg = f"{fast},{fail}"
        out_path = Path(d) / "out.json"
        rc, out, err, wall = run_harness(suites_arg, out_path, timeout=30)
        check(cid, "the harness's own exit code is non-zero when a suite fails", rc != 0, (rc, out, err))
        data, why = read_out(out_path)
        suites = (data or {}).get("suites") if isinstance(data, dict) else None
        fail_entry = None
        fast_entry = None
        if isinstance(suites, dict):
            fail_entry = suites.get(Path(fail).name) or suites.get(fail)
            fast_entry = suites.get(Path(fast).name) or suites.get(fast)
        check(cid, "the failing suite is clearly marked as failed, not a clean short-duration pass",
              isinstance(fail_entry, dict) and fail_entry.get("passed") is False, fail_entry)
        check(cid, "the passing suite's own number is still recorded correctly",
              isinstance(fast_entry, dict) and fast_entry.get("passed") is True
              and isinstance(fast_entry.get("elapsed_sec"), (int, float)) and fast_entry["elapsed_sec"] > 0,
              fast_entry)


def cp1_t04():
    cid = "CP1-T04"
    with _tmp.mkdtemp(prefix="cp1-t04-") as d:
        fakes = make_fake_suites(d)
        slow, fast = fakes["fake_pass_slow.py"], fakes["fake_pass_fast.py"]
        suites_arg = f"{slow},{fast}"
        out_path = Path(d) / "out.json"
        rc, out, err, wall = run_harness(suites_arg, out_path, timeout=30)
        data, why = read_out(out_path)
        suites = (data or {}).get("suites") if isinstance(data, dict) else None
        slow_e = (suites or {}).get(Path(slow).name) or (suites or {}).get(slow)
        fast_e = (suites or {}).get(Path(fast).name) or (suites or {}).get(fast)
        slow_t = slow_e.get("elapsed_sec") if isinstance(slow_e, dict) else None
        fast_t = fast_e.get("elapsed_sec") if isinstance(fast_e, dict) else None
        check(cid, "the slow suite's recorded elapsed time is measurably larger than the fast one's",
              isinstance(slow_t, (int, float)) and isinstance(fast_t, (int, float)) and slow_t > fast_t + 0.3,
              (slow_t, fast_t))
        check(cid, "the harness's own total wall-clock is at least approximately the sum of both real sleeps",
              wall >= 1.2, (wall, out, err))


def cp1_t05():
    cid = "CP1-T05"
    with _tmp.mkdtemp(prefix="cp1-t05-") as d:
        fakes = make_fake_suites(d)
        fast = fakes["fake_pass_fast.py"]
        missing = str(Path(d) / "does_not_exist.py")
        suites_arg = f"{fast},{missing}"
        out_path = Path(d) / "out.json"
        rc, out, err, wall = run_harness(suites_arg, out_path, timeout=30)
        check(cid, "exits non-zero for a suite path that does not exist", rc != 0, (rc, out, err))
        check(cid, "names the missing path in its message", missing in out or missing in err
              or Path(missing).name in out or Path(missing).name in err, (out, err))
        data, _why = read_out(out_path)
        suites = (data or {}).get("suites") if isinstance(data, dict) else None
        bogus = (suites or {}).get(Path(missing).name) or (suites or {}).get(missing) if suites else None
        check(cid, "writes no entry claiming the missing suite passed or ran in some number of seconds",
              bogus is None, bogus)


def cp1_t06():
    cid = "CP1-T06"
    with _tmp.mkdtemp(prefix="cp1-t06-") as d:
        fakes = make_fake_suites(d)
        fast, slow = fakes["fake_pass_fast.py"], fakes["fake_pass_slow.py"]
        suites_arg = f"{fast},{slow}"
        out_path = Path(d) / "out.json"
        stale = {"command": "stale old command from an earlier invocation",
                  "suites": {Path(fast).name: {"elapsed_sec": 999.0, "passed": True},
                             "stale_other_suite.py": {"elapsed_sec": 5.0, "passed": True}}}
        out_path.write_text(json.dumps(stale), encoding="utf-8")
        rc, out, err, wall = run_harness(suites_arg, out_path, timeout=30)
        data, why = read_out(out_path)
        if not check(cid, "the file is still valid JSON after a second run", data is not None, why or ""):
            return
        suites = data.get("suites") if isinstance(data, dict) else {}
        check(cid, "holds only this run's suites, not a stale extra entry from before",
              isinstance(suites, dict) and "stale_other_suite.py" not in suites, suites)
        fast_e = (suites or {}).get(Path(fast).name) or (suites or {}).get(fast)
        check(cid, "the stale number is replaced with this run's fresh measurement",
              isinstance(fast_e, dict) and isinstance(fast_e.get("elapsed_sec"), (int, float))
              and fast_e["elapsed_sec"] != 999.0, fast_e)
        check(cid, "still records the exact command used this time",
              isinstance(data.get("command"), str) and data["command"] != stale["command"]
              and "--suites" in data["command"], data.get("command"))


def _hash_real_suites() -> dict:
    out = {}
    for rel in REAL_SUITES:
        p = ROOT / rel
        try:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError as e:
            out[rel] = f"<unreadable: {e}>"
    return out


def cp1_t07(before: dict, after: dict):
    cid = "CP1-T07"
    check(cid, "no existing target suite file is modified by measuring it (hashes unchanged, byte for byte)",
          before == after and all(not v.startswith("<unreadable") for v in before.values()),
          {"before": before, "after": after})


def cp1_t08():
    cid = "CP1-T08"
    suites_arg = ",".join(REAL_SUITES)
    REAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    # Genuinely slow once implemented (roughly 12-34 minutes before CP2-CP4 convert these suites, well under
    # a minute each after): this is the one case allowed to take that long, and it is reached through the
    # exact same CLI entry point as every other case, never a separate code path. The harness-level timeout
    # (T08_HARNESS_TIMEOUT_SEC) sits above the sum of every suite's own internal timeout inside measure_suites,
    # so a single hung suite fails cleanly inside the harness instead of this wrapper being killed from
    # outside and orphaning the still-running suite subprocess.
    rc, out, err, wall = run_harness(suites_arg, REAL_OUT, timeout=T08_HARNESS_TIMEOUT_SEC)
    check(cid, "the real end-to-end run exits 0", rc == 0, (rc, out[-2000:], err[-2000:]))
    data, why = read_out(REAL_OUT)
    if not check(cid, "the recorded evidence file is valid JSON", data is not None, why or ""):
        return
    suites = data.get("suites") if isinstance(data, dict) else None
    check(cid, "holds three positive elapsed-seconds numbers", isinstance(suites, dict) and len(suites) == 3
          and all(isinstance((suites.get(Path(s).name) or suites.get(s) or {}).get("elapsed_sec"), (int, float))
                  and (suites.get(Path(s).name) or suites.get(s))["elapsed_sec"] > 0 for s in REAL_SUITES),
          suites)
    # Structural checks only: a positive, finite elapsed_sec and passed=True. NOT the scout's absolute windows
    # -- this plan's whole purpose (CP2-CP4) is to make these suites fast, so a correct measurement after
    # conversion legitimately falls outside the scout's one-time solo figures, which would otherwise make this
    # case fail forever on a correct, faster result. The scout's figures are printed below purely as reference
    # for a human reading the run, never as a pass/fail gate.
    for rel, (lo, hi) in PLAUSIBLE_RANGES.items():
        entry = (suites or {}).get(Path(rel).name) or (suites or {}).get(rel) if suites else None
        t = entry.get("elapsed_sec") if isinstance(entry, dict) else None
        is_finite_positive = isinstance(t, (int, float)) and t == t and t not in (float("inf"), float("-inf")) \
            and t > 0
        check(cid, f"{rel}'s elapsed time is a positive, finite number of seconds", is_finite_positive, t)
        check(cid, f"{rel} is reported as passed, matching its known all-green result today",
              isinstance(entry, dict) and entry.get("passed") is True, entry)
        print(f"  (reference only, not a gate) {rel}: scout's solo figure was {lo:.0f}-{hi:.0f}s; measured "
              f"{t if t is not None else 'n/a'}")
    check(cid, "records the exact command used", isinstance(data.get("command"), str)
          and "--suites" in data["command"], data.get("command"))


TESTS = [("CP1-T01", cp1_t01), ("CP1-T02", cp1_t02), ("CP1-T03", cp1_t03), ("CP1-T04", cp1_t04),
         ("CP1-T05", cp1_t05), ("CP1-T06", cp1_t06), ("CP1-T08", cp1_t08)]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    ids = [cid for cid, _ in TESTS] + ["CP1-T07"]
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid in ids]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cases": cases}, f)


def run_self_tests() -> int:
    before = _hash_real_suites()
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash in this self-test file still counts as a result for this case, not a
            global FAIL           # process crash: the report always gets a status for every planned case.
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  XX [{cid}] CRASHED: {e}")
            traceback.print_exc()
    after = _hash_real_suites()
    CASE_STATUS.setdefault("CP1-T07", True)
    try:
        cp1_t07(before, after)
    except Exception as e:
        FAIL += 1
        CASE_STATUS["CP1-T07"] = False
        CASE_MESSAGES.setdefault("CP1-T07", []).append(f"crashed: {e}")
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def _reproducible_command(suites_arg: str) -> str:
    """Builds the exact command line (quoted, re-runnable as-is) that reproduces this same measurement by
    re-running the same suites and printing to stdout. Deliberately omits --out: this string is itself
    written into the --out file's own "command" field, so including --out would mean replaying the recorded
    command silently overwrites whatever evidence file it produced (including this plan's own before-side
    baseline_timings.json)."""
    argv = [sys.executable, "-I", str(HARNESS), "--suites", suites_arg]
    return shlex.join(argv)


def measure_suites(suites_arg: str, out_path) -> int:
    """Runs every suite named in `suites_arg` (comma-separated paths) exactly once, each as its own real
    `python3 <suite>` subprocess, timed with a real wall clock (never mocked), each with its own explicit
    timeout (SUITE_TIMEOUT_SEC for a suite named in PLAUSIBLE_RANGES, else DEFAULT_SUITE_TIMEOUT_SEC) so a
    wedged suite is recorded as a clean failure instead of hanging the harness indefinitely. Writes `out_path`
    (if given) as JSON: {"command": <reproducible command line, without --out>, "suites": {<suite basename>:
    {"elapsed_sec": .., "passed": ..}}}, always overwriting any previous content at that path rather than
    merging with it. Returns non-zero if any named suite is missing on disk (checked up front, before running
    or timing anything, so no bogus measurement is ever recorded for it) or if any named suite's subprocess
    exits non-zero or times out."""
    suite_paths = [s.strip() for s in suites_arg.split(",") if s.strip()]
    if not suite_paths:
        sys.stderr.write("tests/measure_suite_baseline.py: --suites named no suite paths\n")
        return 1

    missing = [s for s in suite_paths if not Path(s).is_file()]
    if missing:
        for m in missing:
            sys.stderr.write(f"tests/measure_suite_baseline.py: suite path does not exist: {m}\n")
        return 1

    command = _reproducible_command(suites_arg)
    results: dict = {}
    any_failed = False
    for s in suite_paths:
        p = Path(s)
        suite_timeout = SUITE_TIMEOUT_SEC.get(s, SUITE_TIMEOUT_SEC.get(str(p), DEFAULT_SUITE_TIMEOUT_SEC))
        t0 = time.monotonic()
        try:
            r = subprocess.run([sys.executable, str(p)], cwd=str(ROOT), capture_output=True, text=True,
                                timeout=suite_timeout)
            passed = r.returncode == 0
        except subprocess.TimeoutExpired:
            passed = False
            sys.stderr.write(f"tests/measure_suite_baseline.py: {s} timed out after {suite_timeout:.0f}s\n")
        except OSError as e:
            passed = False
            sys.stderr.write(f"tests/measure_suite_baseline.py: could not start {s}: {e}\n")
        elapsed = time.monotonic() - t0
        any_failed = any_failed or not passed
        results[p.name] = {"elapsed_sec": elapsed, "passed": passed}
        print(f"  {p.name}: {elapsed:.2f}s  {'PASS' if passed else 'FAIL'}")

    if out_path is not None:
        data = {"command": command, "suites": results}
        Path(out_path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return 1 if any_failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suites", required=True, help="comma-separated list of suite paths to measure")
    ap.add_argument("--out", default=None, help="where to write the JSON measurement")
    args = ap.parse_args()

    if os.environ.get("RATCHET_TEST_REPORT"):
        return run_self_tests()

    return measure_suites(args.suites, args.out)


if __name__ == "__main__":
    sys.exit(main())
