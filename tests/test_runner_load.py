#!/usr/bin/env python3
"""
CP1 tests: test time limits stretch on a busy machine.   python3 tests/test_runner_load.py

Like tests/test_runner.py, this file drives the real tests/run_all.py (the script under test) as a subprocess against
throwaway folders of tiny fake "suite" scripts it writes itself. It never runs the real tests/test_*.py suites.

Machine load is never real. The run_all.py subprocess gets its "load" through two environment variables:
  RATCHET_FAKE_LOADAVG   a number (the one-minute load average), the word "unavailable", or "@<file>" where the
                         file holds a number and is re-read on every poll (so a case can change the load mid-run
                         by replacing the file atomically)
  RATCHET_FAKE_CPUS      the CPU count
While they are set, run_all.py uses them instead of os.getloadavg() / os.cpu_count().

Contract this file assumes of tests/run_all.py (the tests are the spec for it):
  - limit = --timeout * min(max(1, load / cpus), ceiling); the ceiling is --max-stretch (default 4, must be >= 1,
    anything else exits 2 with an error naming --max-stretch)
  - the deadline is re-read on every poll (0.5s or less) and follows the current reading, never below --timeout
  - a stretch is reported only when a suite ran past its configured limit or was stopped at a stretched limit,
    in one line of the end summary:
        '<suite>: time limit stretched 2.0x (3s to 6s) at load 8.0 on 4 CPUs'
The cases assert on the words 'stretched', the suite name, the factor, both limits, the load and the CPU count,
not on exact spacing.

The scenarios are independent, so they run at the same time in threads (each with its own folder and environment)
to keep the whole file near 10 seconds. Lower bounds on a stop time are firm; upper bounds carry about 4s of slack.
Throwaway names are neutral (rt-xq-, test_alpha.py, test_beta.py, reading.txt): run_all.py prints the folder path,
and a key word in it must not be able to satisfy an assertion.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP1-T01", "status": "passed"|"failed"|"skipped", "message": "..."}]}.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
from run_all import kill_tree  # noqa: E402

RUN_ALL = ROOT / "tests" / "run_all.py"
README = ROOT / "README.md"

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_CHECKS: dict = {}
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP1-[TX]\d+)\]")
_CHECK_LOCK = threading.Lock()


def check(name, cond, detail=""):
    global PASS, FAIL
    with _CHECK_LOCK:
        PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
        m = ID_RE.search(name)
        if m:
            cid = m.group(1)
            CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
            CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
            if not cond:
                CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


# ---- fake suite fixtures ---------------------------------------------------------------------------------------

SLOW = "import sys, time\ntime.sleep(4.5)\nsys.exit(0)\n"
HANG = "import time\ntime.sleep(600)\n"
QUICK = 'print("quick suite ran")\n'


def hang_with_child_body(pid_dir: Path) -> str:
    return textwrap.dedent(f"""\
        import os, subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(os.path.join({str(pid_dir)!r}, "child.pid"), "w") as f:
            f.write(str(child.pid))
        with open(os.path.join({str(pid_dir)!r}, "suite.pid"), "w") as f:
            f.write(str(os.getpid()))
        time.sleep(600)
        """)


def env_probe_body(out_dir: Path) -> str:
    return textwrap.dedent(f"""\
        import json, os, sys
        keys = ["RATCHET_RUN_ALL", "RATCHET_RUN_ALL_SUITES", "RATCHET_RUN_ALL_TAG", "TMPDIR"]
        with open(os.path.join({str(out_dir)!r}, "env.json"), "w") as f:
            json.dump({{k: os.environ.get(k) for k in keys}}, f)
        sys.exit(0)
        """)


def marker_body(marker: Path) -> str:
    return f"open({str(marker)!r}, 'w').write('ran')\n"


def new_dir(prefix: str) -> Path:
    return Path(_tmp.mkdtemp(prefix=f"rt-xq-{prefix}-"))


def write_suite(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


# ---- driving run_all.py --------------------------------------------------------------------------------------------


class Run:
    """What one run_all.py invocation did."""

    def __init__(self, rc, out, err, wall, exc=None):
        self.rc, self.out, self.err, self.wall, self.exc = rc, out, err, wall, exc

    @property
    def text(self) -> str:
        return (self.out or "") + (self.err or "")

    def duration(self, suite: str):
        """Seconds the end-of-run table shows for a suite, or None."""
        for ln in (self.out or "").splitlines():
            m = re.match(r"^\s*([\d.]+)s\s+(?:passed|FAILED)\s+(\S+)\s*$", ln)
            if m and m.group(2) == suite:
                return float(m.group(1))
        return None

    def reason_limit(self, suite: str):
        """The N in 'timed out after Ns' on the line that reports the suite, or None."""
        for ln in (self.out or "").splitlines():
            if suite in ln:
                m = re.search(r"timed out after (\d+)s", ln)
                if m:
                    return int(m.group(1))
        return None

    def suite_failed_timed_out(self, suite: str) -> bool:
        return any(suite in ln and "FAILED" in ln and "timed out" in ln for ln in (self.out or "").splitlines())

    def suite_passed(self, suite: str) -> bool:
        return any(re.match(r"^\[\d+/\d+\] passed\s+" + re.escape(suite) + r"\s*$", ln)
                   for ln in (self.out or "").splitlines())

    def stretch_lines(self, suite: str) -> list:
        return [ln for ln in (self.out or "").splitlines() if "stretched" in ln.lower() and suite in ln]

    def mentions_stretch(self) -> bool:
        return "stretched" in self.text.lower()


def base_env(xdg: Path, **fake) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("RATCHET_FAKE_") and k != "RATCHET_TEST_REPORT"}
    env["XDG_CACHE_HOME"] = str(xdg)  # keep run_all.py's remembered timings out of the real cache
    env.update({k: str(v) for k, v in fake.items()})
    return env


def run_runner(folder: Path, args=None, env=None, timeout=45, on_start=None) -> Run:
    """Invoke tests/run_all.py --dir <folder> [args] with env. Never raises: a stuck run is killed and returned
    with exc set. on_start(proc) runs right after the process starts (used to change the fake load mid-run)."""
    cmd = [sys.executable, str(RUN_ALL), "--dir", str(folder)] + list(args or [])
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True, env=env if env is not None else dict(os.environ))
    if on_start:
        on_start(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
        return Run(proc.returncode, out, err, time.monotonic() - t0)
    except subprocess.TimeoutExpired as e:
        kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return Run(None, out, err, time.monotonic() - t0, e)


def busy(xdg, load="8", cpus="4"):
    return base_env(xdg, RATCHET_FAKE_LOADAVG=load, RATCHET_FAKE_CPUS=cpus)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"  # a zombie is already dead
    except OSError:
        return False


# ---- the scenarios (each is one or more run_all.py runs; they all start together) -----------------------------


def sc_busy_slow():
    d, x = new_dir("a"), new_dir("x")
    write_suite(d, "test_alpha.py", SLOW)
    return run_runner(d, ["--timeout", "3"], busy(x))


def sc_idle_slow():
    d, x = new_dir("b"), new_dir("x")
    write_suite(d, "test_alpha.py", SLOW)
    return run_runner(d, ["--timeout", "3"], busy(x, "0.5", "4"))


def sc_busy_hang():
    d, x = new_dir("c"), new_dir("x")
    write_suite(d, "test_beta.py", HANG)
    return run_runner(d, ["--timeout", "2"], busy(x))


def sc_default_ceiling():
    d, x = new_dir("d"), new_dir("x")
    write_suite(d, "test_beta.py", HANG)
    return run_runner(d, ["--timeout", "1"], busy(x, "40", "4"))


def sc_ceiling_two():
    d, x = new_dir("e"), new_dir("x")
    write_suite(d, "test_beta.py", HANG)
    return run_runner(d, ["--timeout", "1", "--max-stretch", "2"], busy(x, "40", "4"))


def sc_ceiling_one():
    d, x = new_dir("f"), new_dir("x")
    write_suite(d, "test_alpha.py", SLOW)
    return run_runner(d, ["--timeout", "3", "--max-stretch", "1"], busy(x))


def sc_bad_ceiling():
    """Three refused values, then (positive control) a valid one."""
    x = new_dir("x")
    runs = {}
    for value in ("0.5", "0", "abc"):
        d, owned = new_dir("g"), new_dir("m")
        marker = owned / "marker.txt"
        write_suite(d, "test_alpha.py", marker_body(marker))
        r = run_runner(d, ["--max-stretch", value], busy(x, "1", "4"), timeout=30)
        runs[value] = (r, marker)
    d, owned = new_dir("g"), new_dir("m")
    marker = owned / "marker.txt"
    write_suite(d, "test_alpha.py", marker_body(marker))
    runs["valid"] = (run_runner(d, ["--max-stretch", "1"], busy(x, "1", "4"), timeout=30), marker)
    return runs


def sc_spike():
    d, x, r = new_dir("h"), new_dir("x"), new_dir("r")
    write_suite(d, "test_alpha.py", SLOW)
    reading = r / "reading.txt"
    reading.write_text("0.5\n")

    def spike(_proc):
        def later():
            time.sleep(1.5)
            tmp = r / "reading.tmp"
            tmp.write_text("8\n")
            os.replace(tmp, reading)
        threading.Thread(target=later, daemon=True).start()

    return run_runner(d, ["--timeout", "3"], busy(x, f"@{reading}", "4"), on_start=spike)


def sc_no_reading():
    x = new_dir("x")
    missing = new_dir("r") / "reading.txt"  # the file is never created
    runs = {}
    for label, value in (("unavailable", "unavailable"), ("garbage", "garbage here"), ("missing", f"@{missing}")):
        d = new_dir("i")
        write_suite(d, "test_alpha.py", SLOW)
        runs[label] = run_runner(d, ["--timeout", "3"], busy(x, value, "4"))
    return runs


def sc_not_overloaded():
    x = new_dir("x")
    runs = {}
    for label, (load, cpus) in (("equal", ("4", "4")), ("below", ("1", "8"))):
        d = new_dir("j")
        write_suite(d, "test_beta.py", HANG)
        runs[label] = run_runner(d, ["--timeout", "2"], busy(x, load, cpus))
    return runs


def sc_quick():
    d, x = new_dir("k"), new_dir("x")
    write_suite(d, "test_alpha.py", QUICK)
    return run_runner(d, [], busy(x, "40", "4"))


def sc_child():
    d, x, pids = new_dir("l"), new_dir("x"), new_dir("p")
    write_suite(d, "test_alpha.py", hang_with_child_body(pids))
    r = run_runner(d, ["--timeout", "2"], busy(x))
    return r, pids


def sc_two_suites():
    d, x = new_dir("n"), new_dir("x")
    write_suite(d, "test_alpha.py", SLOW)
    write_suite(d, "test_beta.py", HANG)
    return run_runner(d, ["--timeout", "3", "--workers", "2"], busy(x))


def sc_env_probe():
    d, x, out = new_dir("o"), new_dir("x"), new_dir("q")
    write_suite(d, "test_probe.py", env_probe_body(out))
    r = run_runner(d, ["--timeout", "3"], busy(x))
    return r, d, out


SCENARIOS = {
    "busy_slow": sc_busy_slow, "idle_slow": sc_idle_slow, "busy_hang": sc_busy_hang,
    "default_ceiling": sc_default_ceiling, "ceiling_two": sc_ceiling_two, "ceiling_one": sc_ceiling_one,
    "bad_ceiling": sc_bad_ceiling, "spike": sc_spike, "no_reading": sc_no_reading,
    "not_overloaded": sc_not_overloaded, "quick": sc_quick, "child": sc_child,
    "two_suites": sc_two_suites, "env_probe": sc_env_probe,
}
RESULTS: dict = {}


def start_scenarios():
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(SCENARIOS))
    futs = {k: pool.submit(fn) for k, fn in SCENARIOS.items()}
    return pool, futs


def result(key):
    """The scenario's result, or None (the case then reports the failure itself)."""
    return RESULTS.get(key)


def finished(cid, key, run) -> bool:
    ok = run is not None and run.exc is None
    check(f"[{cid}] setup: scenario '{key}' ran run_all.py to the end", ok,
          getattr(run, "exc", None) or "scenario crashed")
    return ok


STRETCH_WORDS = lambda ln, *bits: all(re.search(b, ln) for b in bits)  # noqa: E731


# ---- cases ---------------------------------------------------------------------------------------------------------


def t01_slow_passes_on_busy_machine():
    print("CP1-T01: a slow test file on a busy machine gets more time and passes")
    r = result("busy_slow")
    if not finished("CP1-T01", "busy_slow", r):
        return
    check("[CP1-T01] the slow suite is reported passed, not timed out", r.suite_passed("test_alpha.py"), r.out[-500:])
    check("[CP1-T01] run_all.py exits 0", r.rc == 0, (r.rc, r.out[-300:]))
    check("[CP1-T01] the summary says '1 passed, 0 failed'", "1 passed, 0 failed" in r.out, r.out[-300:])


def t02_idle_limit_unchanged():
    print("CP1-T02: on an idle machine the limit is exactly the configured one")
    r = result("idle_slow")
    if not finished("CP1-T02", "idle_slow", r):
        return
    check("[CP1-T02] the slow suite is reported timed out", r.suite_failed_timed_out("test_alpha.py"), r.out[-500:])
    d = r.duration("test_alpha.py")
    check(f"[CP1-T02] it was stopped no earlier than 2.9s (took {d})", d is not None and d >= 2.9, d)
    check(f"[CP1-T02] and well before the 4.5s the suite needs (took {d})", d is not None and d < 4.4, d)
    lim = r.reason_limit("test_alpha.py")
    check(f"[CP1-T02] the message says 'timed out after 3s' (plus or minus 1): {lim}", lim in (2, 3, 4), lim)
    check("[CP1-T02] run_all.py exits 1", r.rc == 1, r.rc)
    check("[CP1-T02] nothing says 'stretched'", not r.mentions_stretch(), r.text[-400:])


def t03_hang_stopped_at_stretched_limit():
    print("CP1-T03: a test file that really hangs is still stopped, at the stretched limit")
    r = result("busy_hang")
    if not finished("CP1-T03", "busy_hang", r):
        return
    d = r.duration("test_beta.py")
    check(f"[CP1-T03] the hang suite ran at least the stretched 4s (took {d})", d is not None and d >= 3.9, d)
    check(f"[CP1-T03] and was stopped well before it could finish on its own (took {d})",
          d is not None and d < 8.0, d)
    check("[CP1-T03] it is reported FAILED with 'timed out' in its reason",
          r.suite_failed_timed_out("test_beta.py"), r.out[-500:])
    lim = r.reason_limit("test_beta.py")
    check(f"[CP1-T03] the message says 'timed out after 4s' (plus or minus 1), not 2s: {lim}",
          lim in (3, 4, 5), lim)
    check("[CP1-T03] run_all.py exits 1", r.rc == 1, r.rc)


def t04_summary_says_stretch():
    print("CP1-T04: the summary says when a limit was stretched, by how much and at what load")
    r = result("busy_slow")
    if finished("CP1-T04", "busy_slow", r):
        lines = r.stretch_lines("test_alpha.py")
        check("[CP1-T04] one line names test_alpha.py and says 'stretched'", len(lines) == 1, r.out[-600:])
        ln = lines[0] if lines else ""
        for label, pat in (("the factor 2.0x", r"\b2\.0x\b"), ("the configured limit 3s", r"\b3s\b"),
                           ("the stretched limit 6s", r"\b6s\b"), ("the load 8.0", r"\b8\.0\b"),
                           ("the CPU count '4 CPUs'", r"\b4 CPUs\b")):
            check(f"[CP1-T04] busy slow suite: the line has {label}", re.search(pat, ln) is not None, ln or r.out[-300:])
    h = result("busy_hang")
    if finished("CP1-T04", "busy_hang", h):
        lines = h.stretch_lines("test_beta.py")
        check("[CP1-T04] a stretched hang that is stopped also gets a line naming test_beta.py",
              len(lines) == 1, h.out[-600:])
        ln = lines[0] if lines else ""
        for label, pat in (("the factor 2.0x", r"\b2\.0x\b"), ("the configured limit 2s", r"\b2s\b"),
                           ("the stretched limit 4s", r"\b4s\b"), ("the load 8.0", r"\b8\.0\b"),
                           ("the CPU count '4 CPUs'", r"\b4 CPUs\b")):
            check(f"[CP1-T04] stopped hang: the line has {label}", re.search(pat, ln) is not None, ln or h.out[-300:])


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def t05_docs_describe_stretch():
    print("CP1-T05: README and --help describe the stretch and its ceiling")
    h = subprocess.run([sys.executable, str(RUN_ALL), "--help"], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=30)
    helptext = _normalized(h.stdout + h.stderr)
    check("[CP1-T05] --help lists --max-stretch", "--max-stretch" in helptext, helptext[-500:])
    seg = helptext.split("--max-stretch", 1)[1] if "--max-stretch" in helptext else ""
    seg = re.split(r"\s--(?!max-stretch)[a-z]", seg)[0]  # only the text of this option
    check("[CP1-T05] --help states the default of 4", re.search(r"default\D{0,10}\b4\b", seg) is not None, seg[:300])
    check("[CP1-T05] --help says the time limit grows with machine load",
          re.search(r"\bload", seg, re.I) is not None and re.search(r"limit|time", seg, re.I) is not None, seg[:300])

    readme = README.read_text(encoding="utf-8")
    start = readme.find("### Running the tests")
    end = readme.find("Each test file is a standalone script", start)
    section = _normalized(readme[start:end]) if start >= 0 and end > start else ""
    check("[CP1-T05] setup: README's test section was found", bool(section), "section not found")
    check("[CP1-T05] README's test section mentions --max-stretch", "--max-stretch" in section, section[-600:])
    sents = [s for s in re.split(r"(?<=[.!?])\s+", section) if "--max-stretch" in s]
    check("[CP1-T05] README gives the default ceiling of 4",
          any(re.search(r"\b4\b|4x|four", s) for s in sents), sents)
    check("[CP1-T05] README says a stretch is reported in the summary",
          re.search(r"stretch\w*[^.]*summary|summary[^.]*stretch", section, re.I) is not None, section[-600:])
    check("[CP1-T05] README still mentions the 15-minute limit", "15 minutes" in section, section[-600:])
    check("[CP1-T05] README still mentions --timeout", "--timeout" in section, section[-600:])


def t06_default_ceiling():
    print("CP1-T06: the default ceiling stops a very busy machine stretching past 4x")
    r = result("default_ceiling")
    if not finished("CP1-T06", "default_ceiling", r):
        return
    d = r.duration("test_beta.py")
    check(f"[CP1-T06] the hang suite ran at least 3.9s (took {d})", d is not None and d >= 3.9, d)
    check(f"[CP1-T06] and was not held for the 10s a 10x stretch would give (took {d})",
          d is not None and d < 8.0, d)
    check("[CP1-T06] it is reported timed out", r.suite_failed_timed_out("test_beta.py"), r.out[-500:])
    lines = r.stretch_lines("test_beta.py")
    check("[CP1-T06] a stretch line names test_beta.py", len(lines) == 1, r.out[-500:])
    ln = lines[0] if lines else ""
    for label, pat in (("the factor 4.0x", r"\b4\.0x\b"), ("the configured limit 1s", r"\b1s\b"),
                       ("the stretched limit 4s", r"\b4s\b"), ("the load 40.0", r"\b40\.0\b"),
                       ("the CPU count '4 CPUs'", r"\b4 CPUs\b")):
        check(f"[CP1-T06] the line has {label}", re.search(pat, ln) is not None, ln or r.out[-300:])


def t07_max_stretch_lowers_ceiling():
    print("CP1-T07: --max-stretch lowers the ceiling")
    r = result("ceiling_two")
    if not finished("CP1-T07", "ceiling_two", r):
        return
    d = r.duration("test_beta.py")
    check(f"[CP1-T07] the hang suite ran at least 1.9s (took {d})", d is not None and d >= 1.9, d)
    check(f"[CP1-T07] and was stopped before the 3.9s the 4x default would allow (took {d})",
          d is not None and d < 3.9, d)
    check("[CP1-T07] it is reported timed out", r.suite_failed_timed_out("test_beta.py"), r.out[-500:])
    lines = r.stretch_lines("test_beta.py")
    ln = lines[0] if lines else ""
    check("[CP1-T07] the stretch line has the factor 2.0x and the limits 1s and 2s",
          re.search(r"\b2\.0x\b", ln) is not None and re.search(r"\b1s\b", ln) is not None
          and re.search(r"\b2s\b", ln) is not None, ln or r.out[-400:])


def t08_max_stretch_one_turns_it_off():
    print("CP1-T08: --max-stretch 1 turns the stretch off")
    r = result("ceiling_one")
    if not finished("CP1-T08", "ceiling_one", r):
        return
    check("[CP1-T08] the slow suite is reported timed out", r.suite_failed_timed_out("test_alpha.py"), r.out[-500:])
    d = r.duration("test_alpha.py")
    check(f"[CP1-T08] it was stopped at about 3s, not 4.5s (took {d})", d is not None and 2.9 <= d < 4.4, d)
    check("[CP1-T08] run_all.py exits 1", r.rc == 1, r.rc)
    check("[CP1-T08] nothing says 'stretched'", not r.mentions_stretch(), r.text[-400:])
    check("[CP1-T08] the option itself was accepted", "unrecognized" not in r.text and "invalid" not in r.text.lower(),
          r.text[-400:])


def t09_bad_ceiling_refused():
    print("CP1-T09: a --max-stretch below 1 or not a number is refused before any suite runs")
    runs = result("bad_ceiling")
    if runs is None:
        check("[CP1-T09] setup: scenario 'bad_ceiling' ran", False, "scenario crashed")
        return
    for value in ("0.5", "0", "abc"):
        r, marker = runs[value]
        if not finished("CP1-T09", f"bad_ceiling {value}", r):
            continue
        check(f"[CP1-T09] --max-stretch {value} exits 2", r.rc == 2, (r.rc, r.text[-300:]))
        check(f"[CP1-T09] --max-stretch {value}: the error text names --max-stretch", "--max-stretch" in r.err,
              r.err[-300:])
        check(f"[CP1-T09] --max-stretch {value}: the option is known, not 'unrecognized arguments'",
              "unrecognized arguments" not in r.err, r.err[-300:])
        check(f"[CP1-T09] --max-stretch {value}: no suite ran (no marker file)", not marker.exists(), "marker exists")
    r, marker = runs["valid"]
    if finished("CP1-T09", "bad_ceiling valid", r):
        check("[CP1-T09] control: --max-stretch 1 is accepted, exits 0 and runs the suite",
              r.rc == 0 and marker.exists(), (r.rc, r.err[-300:]))


def t10_spike_extends_limit():
    print("CP1-T10: a load spike that starts while a suite is running still extends its limit")
    r = result("spike")
    if not finished("CP1-T10", "spike", r):
        return
    check("[CP1-T10] the suite was not stopped at 3s: it is reported passed", r.suite_passed("test_alpha.py"),
          r.out[-500:])
    check("[CP1-T10] run_all.py exits 0", r.rc == 0, (r.rc, r.out[-300:]))
    lines = r.stretch_lines("test_alpha.py")
    ln = lines[0] if lines else ""
    check("[CP1-T10] the summary says the limit was stretched to 6s at load 8.0",
          re.search(r"\b6s\b", ln) is not None and re.search(r"\b8\.0\b", ln) is not None, ln or r.out[-400:])


def t11_no_usable_reading():
    print("CP1-T11: with no usable load reading the limit is unchanged and nothing crashes")
    runs = result("no_reading")
    if runs is None:
        check("[CP1-T11] setup: scenario 'no_reading' ran", False, "scenario crashed")
        return
    for label in ("unavailable", "garbage", "missing"):
        r = runs[label]
        if not finished("CP1-T11", f"no_reading {label}", r):
            continue
        check(f"[CP1-T11] {label}: the slow suite is reported timed out", r.suite_failed_timed_out("test_alpha.py"),
              r.out[-500:])
        d = r.duration("test_alpha.py")
        check(f"[CP1-T11] {label}: it was stopped at about 3s (took {d})", d is not None and 2.9 <= d < 4.4, d)
        check(f"[CP1-T11] {label}: no Python traceback", "Traceback" not in r.text, r.text[-500:])
        check(f"[CP1-T11] {label}: nothing says 'stretched'", not r.mentions_stretch(), r.text[-400:])


def t12_full_but_not_overloaded():
    print("CP1-T12: a machine that is full but not overloaded gets no extra time")
    runs = result("not_overloaded")
    if runs is None:
        check("[CP1-T12] setup: scenario 'not_overloaded' ran", False, "scenario crashed")
        return
    for label in ("equal", "below"):
        r = runs[label]
        if not finished("CP1-T12", f"not_overloaded {label}", r):
            continue
        d = r.duration("test_beta.py")
        check(f"[CP1-T12] load {label} to CPUs: the hang suite ran at least 1.9s (took {d})",
              d is not None and d >= 1.9, d)
        check(f"[CP1-T12] load {label} to CPUs: and was stopped before 3.9s (took {d})",
              d is not None and d < 3.9, d)
        check(f"[CP1-T12] load {label} to CPUs: reported timed out", r.suite_failed_timed_out("test_beta.py"),
              r.out[-500:])
        check(f"[CP1-T12] load {label} to CPUs: nothing says 'stretched'", not r.mentions_stretch(), r.text[-400:])


def t13_quick_suite_not_mentioned():
    print("CP1-T13: a quick suite on a busy machine is not mentioned and is not slowed by the polling")
    r = result("quick")
    if not finished("CP1-T13", "quick", r):
        return
    check("[CP1-T13] the suite passes and run_all.py exits 0", r.rc == 0 and r.suite_passed("test_alpha.py"),
          (r.rc, r.out[-400:]))
    check(f"[CP1-T13] it finished within 5s of wall time (took {r.wall:.2f}s)", r.wall < 5.0, r.wall)
    check("[CP1-T13] nothing says 'stretched'", not r.mentions_stretch(), r.text[-400:])
    check("[CP1-T13] the fake load reading did not break the run (no traceback)", "Traceback" not in r.text, r.text[-400:])


def t14_hang_leaves_no_process():
    print("CP1-T14: a hang stopped at the stretched limit leaves no process behind")
    res = result("child")
    if res is None:
        check("[CP1-T14] setup: scenario 'child' ran", False, "scenario crashed")
        return
    r, pids = res
    if not finished("CP1-T14", "child", r):
        return
    check("[CP1-T14] the suite is reported timed out", r.suite_failed_timed_out("test_alpha.py"), r.out[-500:])
    d = r.duration("test_alpha.py")
    check(f"[CP1-T14] it was stopped at the stretched 4s, not 2s (took {d})", d is not None and d >= 3.9, d)
    try:
        child = int((pids / "child.pid").read_text())
        suite = int((pids / "suite.pid").read_text())
    except (OSError, ValueError) as e:
        check("[CP1-T14] setup: the suite recorded its own pid and its child's pid", False, e)
        return
    time.sleep(0.3)
    check(f"[CP1-T14] the suite process {suite} is gone", not pid_alive(suite), suite)
    check(f"[CP1-T14] the detached child {child} is gone", not pid_alive(child), child)
    for pid in (child, suite):  # never leave a stray sleeper behind if the check above failed
        if pid_alive(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def t15_each_suite_own_deadline():
    print("CP1-T15: each suite gets its own stretched deadline when run together")
    r = result("two_suites")
    if not finished("CP1-T15", "two_suites", r):
        return
    check("[CP1-T15] test_alpha.py (4.5s) passes", r.suite_passed("test_alpha.py"), r.out[-600:])
    check("[CP1-T15] test_beta.py is reported timed out", r.suite_failed_timed_out("test_beta.py"), r.out[-600:])
    d = r.duration("test_beta.py")
    check(f"[CP1-T15] test_beta.py was stopped at the stretched 6s, not 3s (took {d})",
          d is not None and d >= 5.9, d)
    check("[CP1-T15] run_all.py exits 1", r.rc == 1, r.rc)
    check("[CP1-T15] the summary says '1 passed, 1 failed'", "1 passed, 1 failed" in r.out, r.out[-300:])
    for name in ("test_alpha.py", "test_beta.py"):
        check(f"[CP1-T15] the stretch summary names {name}", len(r.stretch_lines(name)) >= 1, r.out[-600:])


def t16_environment_unchanged():
    print("CP1-T16: the tag and run-all variables each suite sees are unchanged")
    res = result("env_probe")
    if res is None:
        check("[CP1-T16] setup: scenario 'env_probe' ran", False, "scenario crashed")
        return
    r, suite_dir, out_dir = res
    if not finished("CP1-T16", "env_probe", r):
        return
    check("[CP1-T16] the probe suite passes on the busy machine", r.rc == 0 and r.suite_passed("test_probe.py"),
          (r.rc, r.out[-400:]))
    try:
        env = json.loads((out_dir / "env.json").read_text())
    except (OSError, ValueError) as e:
        check("[CP1-T16] setup: the probe recorded its environment", False, e)
        return
    check("[CP1-T16] RATCHET_RUN_ALL is 1", env.get("RATCHET_RUN_ALL") == "1", env)
    check("[CP1-T16] RATCHET_RUN_ALL_SUITES lists the probe's own file name",
          "test_probe.py" in (env.get("RATCHET_RUN_ALL_SUITES") or "").split(","), env)
    tag = env.get("RATCHET_RUN_ALL_TAG") or ""
    check("[CP1-T16] RATCHET_RUN_ALL_TAG is non-empty and contains the suite's stem", "test_probe" in tag, env)
    tmpdir = env.get("TMPDIR") or ""
    check("[CP1-T16] TMPDIR is inside run_all.py's own temp area", "ratchet-run-all-" in tmpdir, env)
    check("[CP1-T16] TMPDIR is not inside the test's own folders",
          bool(tmpdir) and not tmpdir.startswith(str(suite_dir)) and not tmpdir.startswith(str(out_dir)), env)


TESTS = [
    ("CP1-T01", t01_slow_passes_on_busy_machine),
    ("CP1-T02", t02_idle_limit_unchanged),
    ("CP1-T03", t03_hang_stopped_at_stretched_limit),
    ("CP1-T04", t04_summary_says_stretch),
    ("CP1-T05", t05_docs_describe_stretch),
    ("CP1-T06", t06_default_ceiling),
    ("CP1-T07", t07_max_stretch_lowers_ceiling),
    ("CP1-T08", t08_max_stretch_one_turns_it_off),
    ("CP1-T09", t09_bad_ceiling_refused),
    ("CP1-T10", t10_spike_extends_limit),
    ("CP1-T11", t11_no_usable_reading),
    ("CP1-T12", t12_full_but_not_overloaded),
    ("CP1-T13", t13_quick_suite_not_mentioned),
    ("CP1-T14", t14_hang_leaves_no_process),
    ("CP1-T15", t15_each_suite_own_deadline),
    ("CP1-T16", t16_environment_unchanged),
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
    pool, futs = start_scenarios()
    for key, fut in futs.items():
        try:
            RESULTS[key] = fut.result()
        except Exception as e:  # a crashed scenario fails its cases, it does not crash the file
            print(f"  scenario {key} crashed: {e}")
            traceback.print_exc()
    pool.shutdown(wait=True)
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
