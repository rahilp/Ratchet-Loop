#!/usr/bin/env python3
"""
CP3 tests: one command runs every test suite at the same time.   python3 tests/test_runner.py

This suite never runs the real tests/test_*.py suites (that would take 35+ minutes, per app-scout.json).
Instead it drives tests/run_all.py (the script under test) against throwaway folders of tiny, fast, fully
deterministic fake "suite" scripts this file writes itself: pass, fail-cleanly, crash-uncaught, sleep-a-known-
amount, leak-a-temp-file, clean-up-fully, race-on-a-fixed-basename, and hang-forever. Each fixture is a
standalone script with no third-party dependencies, generated as a string and written into a private temp
folder per case, so the whole suite stays fast (seconds) and hermetic.

Contract this file assumes of tests/run_all.py (not yet written -- these tests are the spec for it):
  - invoked as `python3 tests/run_all.py --dir <folder> [--workers N] [--timeout SECONDS]`
  - discovers every `test_*.py` file directly under <folder> and runs each as its own suite
  - gives each suite its own private TMPDIR and reports it as failed if that folder is non-empty afterwards
  - runs suites concurrently (default worker count from the CPU count, overridable with --workers)
  - kills a suite that outlives --timeout and reports it as a timeout failure
  - prints each suite's name next to a pass/fail indication and a measured duration, plus a final
    "N passed, M failed" style summary line counting suites (not individual checks)
  - exits 0 only when every suite passed and none left temp files behind

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP3-T01", "status": "passed"|"failed"|"skipped", "message": "..."}]}. A case that returned
without making a single check (X01 to X03 where there is no /proc, say) is "skipped", never "passed".
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
from run_all import kill_tree  # noqa: E402  (the one process-tree kill, shared with run_all.py)

RUN_ALL = ROOT / "tests" / "run_all.py"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
CASE_CHECKS: dict[str, int] = {}  # how many checks each case made; a case that made none did not run
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP3-[TX]\d+)\]")


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
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ---- fake suite fixtures ---------------------------------------------------------------------------------


def suite_dir(prefix: str) -> Path:
    return Path(_tmp.mkdtemp(prefix=f"ratchet-cp3-{prefix}-"))


def write_suite(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def pass_fast_body() -> str:
    return "import sys\nsys.exit(0)\n"


def sleep_body(seconds) -> str:
    return f"import sys, time\ntime.sleep({seconds})\nsys.exit(0)\n"


def fail_body(marker: str) -> str:
    return f'import sys\nprint({marker!r})\nprint("0 passed, 1 failed")\nsys.exit(1)\n'


def crash_body(message: str) -> str:
    return f'print("about to crash")\nraise RuntimeError({message!r})\n'


def leaky_body(leftover_name: str) -> str:
    return (
        "import os, sys, tempfile\n"
        f"p = os.path.join(tempfile.gettempdir(), {leftover_name!r})\n"
        'with open(p, "w") as f:\n'
        '    f.write("leftover")\n'
        "sys.exit(0)\n"
    )


def clean_body() -> str:
    return (
        "import os, sys, tempfile\n"
        "d = tempfile.mkdtemp()\n"
        'fp = os.path.join(d, "f.txt")\n'
        'open(fp, "w").close()\n'
        "os.remove(fp)\n"
        "os.rmdir(d)\n"
        "sys.exit(0)\n"
    )


def clash_body() -> str:
    return (
        "import os, sys, tempfile, time, uuid\n"
        "token = uuid.uuid4().hex\n"
        'p = os.path.join(tempfile.gettempdir(), "clash_marker.txt")\n'
        'with open(p, "w") as f:\n'
        "    f.write(token)\n"
        "time.sleep(0.6)\n"
        "with open(p) as f:\n"
        "    seen = f.read()\n"
        "if seen != token:\n"
        '    print("COLLISION: temp dir was shared with another suite")\n'
        "    sys.exit(1)\n"
        "os.remove(p)\n"
        "sys.exit(0)\n"
    )


def hang_body() -> str:
    return "import time\ntime.sleep(600)\n"


# ---- driving run_all.py -----------------------------------------------------------------------------------


class _GroupHandle:
    """Adapts a Popen so _tmp.track()'s cleanup (poll/terminate/wait) kills its whole process tree (run_all.py's
    kill_tree()) instead of just that single pid -- a safety net alongside run_runner()'s own TimeoutExpired
    handling."""

    def __init__(self, proc):
        self._proc = proc

    def poll(self):
        return self._proc.poll()

    def wait(self, timeout=None):
        return self._proc.wait(timeout=timeout)

    def terminate(self):
        kill_tree(self._proc)

    def kill(self):
        kill_tree(self._proc)


def run_runner(dir_path, extra_args=None, timeout=30):
    """Invoke tests/run_all.py --dir <dir_path> [extra_args]. Never raises: a timeout is returned as
    (None, partial_stdout, partial_stderr, the_exception) so a case that proves "it finishes instead of
    hanging" can assert on that directly instead of crashing the whole test file.

    run_all.py runs in its own process group (start_new_session=True) so a forced timeout here can kill that
    whole group; kill_tree() (imported from run_all.py) additionally walks /proc for any descendant still alive
    (a fake suite run_all.py spawned, possibly already detached into its own session) so no child can outlive a
    timed-out case. The Popen is also registered with _tmp.track() as a second safety net."""
    cmd = [sys.executable, str(RUN_ALL), "--dir", str(dir_path)]
    if extra_args:
        cmd += list(extra_args)
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
    _tmp.track(_GroupHandle(proc))
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err, None
    except subprocess.TimeoutExpired as e:
        kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return None, out, err, e


# ---- CP3-T01: all suites pass -----------------------------------------------------------------------------


def t01_all_suites_pass():
    print("CP3-T01: all suites pass")
    d = suite_dir("t01")
    names = [f"test_p{i}.py" for i in range(1, 6)]
    for n in names:
        write_suite(d, n, pass_fast_body())
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T01] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T01] run_all.py exits 0 when every suite passes", rc == 0, (rc, out[-300:], err[-300:]))
    m = re.search(r"(\d+)\s+passed,\s+(\d+)\s+failed", out)
    check("[CP3-T01] setup: the final summary reports a 'N passed, M failed' style line", m is not None, out[-300:])
    if m:
        check("[CP3-T01] the summary counts all 5 suites as passed and none failed",
              int(m.group(1)) == 5 and int(m.group(2)) == 0, m.group(0))
    missing = [n for n in names if n not in out]
    check("[CP3-T01] every suite is named in the output", not missing, missing)


# ---- CP3-T02: suites run concurrently ----------------------------------------------------------------------


def t02_concurrent():
    print("CP3-T02: suites run at the same time, not one after another")
    d = suite_dir("t02")
    names = [f"test_s{i}.py" for i in range(1, 5)]
    for n in names:
        write_suite(d, n, sleep_body(2))
    t0 = time.time()
    rc, out, err, exc = run_runner(d, timeout=30)
    elapsed = time.time() - t0
    check("[CP3-T02] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T02] run_all.py exits 0 when all slow suites pass", rc == 0, (rc, out[-300:], err[-300:]))
    check(f"[CP3-T02] 4 suites that each sleep 2s finish in well under 8s (ran concurrently): took {elapsed:.2f}s",
          elapsed < 7.5, elapsed)


# ---- CP3-T03: a failing suite is named with the end of its output -------------------------------------------


def t03_failing_named():
    print("CP3-T03: a failing suite is named with the end of its output")
    d = suite_dir("t03")
    marker = "CP3_T03_DISTINCT_MARKER_7f1a9"
    for i in range(1, 4):
        write_suite(d, f"test_ok{i}.py", pass_fast_body())
    write_suite(d, "test_bad.py", fail_body(marker))
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T03] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T03] run_all.py exits non-zero when one suite fails", rc != 0, (rc, out[-300:]))
    bad_lines = [ln for ln in out.splitlines() if "test_bad.py" in ln]
    check("[CP3-T03] the failing suite test_bad.py is named in the output", bool(bad_lines), out[-400:])
    check("[CP3-T03] the failing suite is flagged as failed, not passed",
          any(re.search(r"fail", ln, re.I) for ln in bad_lines)
          and not any(re.search(r"\bpass(ed)?\b", ln, re.I) for ln in bad_lines), bad_lines)
    check("[CP3-T03] the distinctive marker line from the failing suite's printed output is shown",
          marker in out, out[-600:])


# ---- CP3-T04: a crashing suite is also named, not swallowed --------------------------------------------------


def t04_crash_named():
    print("CP3-T04: a suite that crashes outright is also named, not swallowed")
    d = suite_dir("t04")
    for i in range(1, 4):
        write_suite(d, f"test_ok{i}.py", pass_fast_body())
    write_suite(d, "test_crash.py", crash_body("CP3_T04_boom"))
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T04] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T04] run_all.py exits non-zero when one suite crashes outright", rc != 0, (rc, out[-300:]))
    crash_lines = [ln for ln in out.splitlines() if "test_crash.py" in ln]
    check("[CP3-T04] the crashing suite test_crash.py is named in the output", bool(crash_lines), out[-400:])
    check("[CP3-T04] the crashing suite is flagged failed, same as an ordinary assertion failure",
          any(re.search(r"fail", ln, re.I) for ln in crash_lines), crash_lines)
    check("[CP3-T04] the end of the crash's traceback output (its exception type and message) is shown",
          "RuntimeError" in out and "CP3_T04_boom" in out, out[-600:])


# ---- CP3-T05: each suite gets its own private temp folder ---------------------------------------------------


def t05_private_temp():
    print("CP3-T05: each suite gets its own private temp folder")
    d = suite_dir("t05")
    write_suite(d, "test_clash_a.py", clash_body())
    write_suite(d, "test_clash_b.py", clash_body())
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T05] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T05] both suites pass when each is given its own private temp folder (no collision)",
          rc == 0, (rc, out[-400:], err[-400:]))
    check("[CP3-T05] neither suite reports a collision from a shared temp folder", "COLLISION" not in out, out[-400:])


# ---- CP3-T06: a suite that leaves files behind is reported as a failure -------------------------------------


def t06_leftover_fails():
    print("CP3-T06: a suite that leaves files behind is reported as a failure")
    d = suite_dir("t06")
    write_suite(d, "test_ok1.py", pass_fast_body())
    write_suite(d, "test_ok2.py", pass_fast_body())
    write_suite(d, "test_leaky.py", leaky_body("cp3_t06_leftover.txt"))
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T06] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T06] run_all.py exits non-zero because a suite left temp files behind", rc != 0, (rc, out[-400:]))
    leaky_lines = [ln for ln in out.splitlines() if "test_leaky.py" in ln]
    check("[CP3-T06] the leaky suite is named and flagged failed even though its own exit code was 0",
          bool(leaky_lines) and any(re.search(r"fail", ln, re.I) for ln in leaky_lines), leaky_lines)
    check("[CP3-T06] the failure reason mentions leftover/stray temp files, not a check failure",
          bool(re.search(r"left.?over|leaves?.*temp|temp.*behind|stray", out, re.I)), out[-400:])


# ---- CP3-T07: a suite that cleans up fully is not flagged ----------------------------------------------------


def t07_clean_not_flagged():
    print("CP3-T07: a suite that cleans up fully is not flagged")
    d = suite_dir("t07")
    write_suite(d, "test_ok1.py", pass_fast_body())
    write_suite(d, "test_clean.py", clean_body())
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T07] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T07] run_all.py exits 0 when a suite cleans up fully after itself",
          rc == 0, (rc, out[-400:], err[-400:]))
    clean_lines = [ln for ln in out.splitlines() if "test_clean.py" in ln]
    check("[CP3-T07] the cleaned-up suite is named and reported as passed, not flagged for leftovers",
          bool(clean_lines) and not any(re.search(r"fail|left.?over", ln, re.I) for ln in clean_lines), clean_lines)


# ---- CP3-T08: README points to run_all.py for the full regression run ---------------------------------------


def t08_readme_points_to_run_all():
    print("CP3-T08: README points to run_all.py for the full regression run")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("[CP3-T08] README.md tells a developer to run `python3 tests/run_all.py`",
          "python3 tests/run_all.py" in readme, "")
    idx = readme.find("python3 tests/run_all.py")
    nearby = readme[max(0, idx - 300):idx + 300] if idx != -1 else ""
    check("[CP3-T08] that instruction sits near wording about the full/regression test run",
          idx != -1 and bool(re.search(r"regress|full suite|all test", nearby, re.I)), nearby)


# ---- CP3-T09: an empty suite folder is handled gracefully -----------------------------------------------------


def t09_empty_dir_handled():
    print("CP3-T09: an empty suite folder is handled gracefully")
    d = suite_dir("t09")
    rc, out, err, exc = run_runner(d, timeout=20)
    check("[CP3-T09] setup: run_all.py was invoked without hanging on an empty folder", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T09] run_all.py does not crash with an uncaught traceback on an empty suite folder",
          "Traceback (most recent call last)" not in err, err[-400:])
    check("[CP3-T09] run_all.py prints a plain 'no suites found' style message",
          bool(re.search(r"no .*suites? found", out + err, re.I)), (out[-300:], err[-300:]))
    check("[CP3-T09] run_all.py exits non-zero on an empty suite folder rather than silently claiming success",
          rc is not None and rc != 0, rc)


# ---- CP3-T10: worker count can be overridden -------------------------------------------------------------------


def t10_workers_override():
    print("CP3-T10: worker count can be overridden")
    bodies = [sleep_body(1) for _ in range(4)]
    names = [f"test_w{i}.py" for i in range(1, 5)]

    d1 = suite_dir("t10-default")
    for n, b in zip(names, bodies):
        write_suite(d1, n, b)
    t0 = time.time()
    rc1, out1, err1, exc1 = run_runner(d1, timeout=30)
    elapsed_default = time.time() - t0

    d2 = suite_dir("t10-w1")
    for n, b in zip(names, bodies):
        write_suite(d2, n, b)
    t0 = time.time()
    rc2, out2, err2, exc2 = run_runner(d2, ["--workers", "1"], timeout=30)
    elapsed_w1 = time.time() - t0

    check("[CP3-T10] setup: both runs (default workers and --workers 1) completed without hanging",
          exc1 is None and exc2 is None, (str(exc1), str(exc2)))
    if exc1 is not None or exc2 is not None:
        return
    check("[CP3-T10] both runs pass (4 suites that each sleep ~1s)", rc1 == 0 and rc2 == 0, (rc1, rc2))
    check(f"[CP3-T10] --workers 1 takes measurably longer than the default worker count "
          f"(default {elapsed_default:.2f}s vs --workers 1 {elapsed_w1:.2f}s), proving the flag controls concurrency",
          elapsed_w1 > elapsed_default * 1.5 and elapsed_w1 > 3.0, (elapsed_default, elapsed_w1))


# ---- CP3-T11: a hung suite is killed at its timeout, the rest still finish -------------------------------------


def t11_timeout_kills_hung():
    print("CP3-T11: a hung suite is killed at its timeout and the rest still finish")
    d = suite_dir("t11")
    for i in range(1, 4):
        write_suite(d, f"test_ok{i}.py", pass_fast_body())
    write_suite(d, "test_hangs.py", hang_body())
    t0 = time.time()
    rc, out, err, exc = run_runner(d, ["--timeout", "2"], timeout=30)
    elapsed = time.time() - t0
    check("[CP3-T11] setup: run_all.py terminated on its own within 30s instead of hanging like the stuck suite",
          exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T11] the whole run finishes quickly (well under the hung suite's 600s sleep) "
          "because it is killed at its timeout", elapsed < 25.0, elapsed)
    check("[CP3-T11] run_all.py exits non-zero because one suite timed out", rc != 0, rc)
    hang_lines = [ln for ln in out.splitlines() if "test_hangs.py" in ln]
    check("[CP3-T11] the hung suite is named and flagged failed/timed out",
          bool(hang_lines) and any(re.search(r"fail|time.?out", ln, re.I) for ln in hang_lines), hang_lines)
    ok_lines = [ln for ln in out.splitlines() if re.search(r"test_ok[123]\.py", ln)]
    check("[CP3-T11] the other (fast, passing) suites still completed and report correctly",
          len(ok_lines) >= 3 and not any(re.search(r"fail", ln, re.I) for ln in ok_lines), ok_lines)


# ---- CP3-T12: final summary table names every suite with its duration -------------------------------------------


def t12_summary_table_durations():
    print("CP3-T12: final summary table names every suite with its duration")
    durations = [0.3, 0.9, 1.5, 2.1, 2.7]
    names = [f"test_d{i}.py" for i in range(len(durations))]
    d = suite_dir("t12")
    for n, secs in zip(names, durations):
        write_suite(d, n, sleep_body(secs))
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T12] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T12] run_all.py exits 0 when every suite passes", rc == 0, (rc, out[-300:]))
    missing = [n for n in names if n not in out]
    check("[CP3-T12] the final table lists every one of the 5 suites by name", not missing, missing)
    order = []
    lines_with_number = set()
    for ln in out.splitlines():
        for n in names:
            if n in ln and re.search(r"\d+(\.\d+)?\s*s\b", ln):
                lines_with_number.add(n)
                if n not in order:
                    order.append(n)
    check("[CP3-T12] each suite's line in the table shows a measured duration",
          lines_with_number == set(names), sorted(set(names) - lines_with_number))
    is_ascending = order == names
    is_descending = order == list(reversed(names))
    check("[CP3-T12] the table's suites are ordered by their measured duration (not scrambled)",
          len(order) == len(names) and (is_ascending or is_descending), order)


# ---- CP3-T13: one failing suite does not hide or change the others' results --------------------------------------


def t13_one_failure_isolated():
    print("CP3-T13: one failing suite does not hide or change the others' results")
    d = suite_dir("t13")
    for i in range(1, 4):
        write_suite(d, f"test_ok{i}.py", pass_fast_body())
    write_suite(d, "test_bad.py", fail_body("CP3_T13_MARKER"))
    rc, out, err, exc = run_runner(d, timeout=30)
    check("[CP3-T13] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    if exc is not None:
        return
    check("[CP3-T13] the overall process exit code is non-zero", rc != 0, rc)
    bad_lines = [ln for ln in out.splitlines() if "test_bad.py" in ln]
    check("[CP3-T13] only the failing suite test_bad.py is flagged as failed",
          bool(bad_lines) and any(re.search(r"fail", ln, re.I) for ln in bad_lines), bad_lines)
    for i in range(1, 4):
        n = f"test_ok{i}.py"
        ok_lines = [ln for ln in out.splitlines() if n in ln]
        check(f"[CP3-T13] passing suite {n} is still marked passed, unaffected by the other suite's failure",
              bool(ok_lines) and any(re.search(r"\bpass(ed)?\b", ln, re.I) for ln in ok_lines)
              and not any(re.search(r"\bfail", ln, re.I) for ln in ok_lines), ok_lines)


# ---- CP3-X01: run_runner()'s own forced timeout kills run_all.py and every fixture process, including a
#              grandchild that detached into its own session with setsid() -----------------------------------


def hang_detach_body(tag: str) -> str:
    """A suite that hangs forever and also spawns a grandchild that detaches into its own session via
    os.setsid() before hanging too. The suite's own argv (its file path, which sits inside its unique suite
    temp folder) and the grandchild's argv both carry `tag`, so a /proc cmdline scan can find either one."""
    child_src = "import os, time\nos.setsid()\ntime.sleep(600)\n"
    return (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_src!r}, {tag!r}])\n"
        "time.sleep(600)\n"
    )


def _procs_with_text(text: str) -> list:
    """pids whose /proc/<pid>/cmdline contains `text` right now (Linux only, best effort)."""
    hits: list = []
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return hits
    needle = text.encode("utf-8", "replace")
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if needle in raw:
            hits.append(pid)
    return hits


def x01_forced_timeout_kills_everything():
    print("CP3-X01: run_runner()'s own forced timeout kills run_all.py and every fixture process, "
          "including a setsid-detached grandchild")
    if not os.path.isdir("/proc"):
        print("  - [CP3-X01] not run: no /proc on this platform, so a process left running can't be found")
        return
    d = suite_dir("x01")
    write_suite(d, "test_hangs.py", hang_detach_body(str(d)))
    # run_all.py's own --timeout is left generous (120s) so run_runner()'s short outer timeout (3s) is what
    # actually fires first, driving run_runner() itself into its TimeoutExpired/kill_tree path.
    t0 = time.time()
    rc, out, err, exc = run_runner(d, ["--timeout", "120"], timeout=3)
    elapsed = time.time() - t0
    check("[CP3-X01] run_runner() itself raised TimeoutExpired (its own short outer timeout fired, not "
          "run_all.py's much longer --timeout)", isinstance(exc, subprocess.TimeoutExpired), str(exc))
    check("[CP3-X01] run_runner() returned quickly (within a few seconds of its own outer timeout), "
          f"not after the 600s hang or the 120s --timeout: took {elapsed:.2f}s", elapsed < 20.0, elapsed)
    check("[CP3-X01] run_runner() reports no return code on a forced timeout", rc is None, rc)
    survivors: list = []
    for _ in range(20):
        survivors = _procs_with_text(str(d))
        if not survivors:
            break
        time.sleep(0.25)
    check("[CP3-X01] no run_all.py process, fake-suite process or setsid-detached grandchild survives "
          "the forced timeout", not survivors, survivors)


# ---- CP3-X02: run_all.py's own --timeout stops a hung suite and the grandchild it detached with setsid() ------


def x02_run_all_timeout_kills_detached_grandchild():
    print("CP3-X02: run_all.py's own --timeout stops a hung suite and the setsid-detached grandchild it started")
    if not os.path.isdir("/proc"):
        print("  - [CP3-X02] not run: no /proc on this platform, so a process left running can't be found")
        return
    d = suite_dir("x02")
    write_suite(d, "test_hangs.py", hang_detach_body(str(d)))
    # run_all.py's own --timeout (2s) fires long before run_runner()'s outer timeout (60s), so this drives
    # run_all.py's kill path (stop() and kill_tree()), not the test harness's supervision of run_all.py.
    rc, out, err, exc = run_runner(d, ["--timeout", "2", "--workers", "1"], timeout=60)
    check("[CP3-X02] run_all.py finished on its own: its --timeout fired, not run_runner()'s outer timeout",
          exc is None, str(exc))
    hang_lines = [ln for ln in (out or "").splitlines() if "test_hangs.py" in ln]
    check("[CP3-X02] run_all.py exits non-zero and reports the hung suite as timed out",
          rc not in (0, None) and any(re.search(r"timed? ?out", ln, re.I) for ln in hang_lines), (rc, hang_lines))
    survivors: list = []
    for _ in range(8):
        survivors = _procs_with_text(str(d))
        if not survivors:
            break
        time.sleep(0.25)
    check("[CP3-X02] no fixture process (the hung suite or its setsid-detached grandchild) survives after "
          "run_all.py exits", not survivors, survivors)
    for pid in survivors:  # never leave a leaked fixture process running, even when the check failed
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            pass


# ---- CP3-X03: a setsid-detached child of a suite that already exited 0 is stopped through the suite's tag -------


def detach_and_exit_body(tag_file: str, tag: str) -> str:
    """A suite that records its RATCHET_RUN_ALL_TAG value in `tag_file`, starts a sleeper in a new session
    (start_new_session=True, so it has left the suite's process group before Popen returns; its argv carries
    `tag`), and exits 0 at once. Once it exits the sleeper is handed to another parent, so only the tag it
    inherited links it to the suite."""
    return (
        "import os, subprocess, sys\n"
        f"with open({tag_file!r}, 'w') as f:\n"
        "    f.write(os.environ.get('RATCHET_RUN_ALL_TAG', ''))\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)', {tag!r}],\n"
        "                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "                 start_new_session=True)\n"
        "sys.exit(0)\n"
    )


def _procs_with_env_value(name: str, value: str) -> list:
    """pids whose /proc/<pid>/environ sets `name` to exactly `value` right now (Linux only, best effort)."""
    hits: list = []
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return hits
    want = f"{name}={value}".encode("utf-8", "replace")
    for pid in pids:
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if want in raw.split(b"\0"):
            hits.append(pid)
    return hits


def x03_detached_child_of_exited_suite_is_stopped():
    print("CP3-X03: a setsid-detached sleeper left by a suite that already exited 0 does not survive run_all.py")
    if not os.path.isdir("/proc"):
        print("  - [CP3-X03] not run: no /proc on this platform, so a process left running can't be found")
        return
    d = suite_dir("x03")
    tag_file = d / "seen_tag.txt"
    write_suite(d, "test_detach.py", detach_and_exit_body(str(tag_file), str(d)))
    rc, out, err, exc = run_runner(d, ["--workers", "1"], timeout=60)
    check("[CP3-X03] setup: run_all.py was invoked without hanging", exc is None, str(exc))
    try:
        tag = tag_file.read_text(encoding="utf-8").strip()
    except OSError:
        tag = ""
    check("[CP3-X03] the suite ran with a RATCHET_RUN_ALL_TAG set by run_all.py", bool(tag), tag)
    lines = [ln for ln in (out or "").splitlines() if "test_detach.py" in ln]
    check("[CP3-X03] run_all.py exits 0 and reports the suite as passed",
          rc == 0 and bool(lines) and any(re.search(r"\bpassed\b", ln) for ln in lines)
          and not any(re.search(r"fail", ln, re.I) for ln in lines), (rc, lines, (err or "")[-300:]))
    survivors: list = []
    if tag:
        for _ in range(8):
            survivors = _procs_with_env_value("RATCHET_RUN_ALL_TAG", tag)
            if not survivors:
                break
            time.sleep(0.25)
    check("[CP3-X03] no process carrying that run's tag (the detached sleeper) survives after run_all.py exits",
          bool(tag) and not survivors, (tag, survivors))
    for pid in set(survivors) | set(_procs_with_text(str(d))):  # never leave a leaked sleeper running
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            pass


TESTS = [
    ("CP3-T01", t01_all_suites_pass),
    ("CP3-T02", t02_concurrent),
    ("CP3-T03", t03_failing_named),
    ("CP3-T04", t04_crash_named),
    ("CP3-T05", t05_private_temp),
    ("CP3-T06", t06_leftover_fails),
    ("CP3-T07", t07_clean_not_flagged),
    ("CP3-T08", t08_readme_points_to_run_all),
    ("CP3-T09", t09_empty_dir_handled),
    ("CP3-T10", t10_workers_override),
    ("CP3-T11", t11_timeout_kills_hung),
    ("CP3-T12", t12_summary_table_durations),
    ("CP3-T13", t13_one_failure_isolated),
    ("CP3-X01", x01_forced_timeout_kills_everything),
    ("CP3-X02", x02_run_all_timeout_kills_detached_grandchild),
    ("CP3-X03", x03_detached_child_of_exited_suite_is_stopped),
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
              "message": "not run: it made no checks on this platform" if status(cid) == "skipped"
              else "" if status(cid) == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash in one case still counts as a result for that case, not a crash of the file
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        else:
            if not CASE_CHECKS.get(cid):  # returned without a single check (a "not run" case): not a pass
                CASE_SKIPPED.add(cid)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
