#!/usr/bin/env python3
"""
CP2 tests: Ratchet's own test runs get more time on a busy machine.   python3 tests/test_load_timeouts.py

Black-box: every case drives the real ratchet.py (red, behavior, complete, config) in a throwaway git project whose
step command is a tiny python script this file writes (it sleeps or hangs for a known time). Nothing here imports
the engine, and no real agent CLI is reachable (the projects use tests/trust_helpers.py's hermetic env).

Machine load is never real. The engine process gets its "load" through two environment variables set per
subprocess (never left in os.environ):
  RATCHET_FAKE_LOADAVG   a number (the one-minute load average), the word "unavailable", or "@<file>" where the
                         file holds a number and is re-read on every poll (so a case can change the load while
                         the command runs)
  RATCHET_FAKE_CPUS      the CPU count
These are the same two names tests/run_all.py reads (CP1).

Contract this file assumes of ratchet.py (the tests are the spec for it):
  - limit = test_timeout_sec * min(max(1, load / cpus), load_stretch_max); load_stretch_max is a config key,
    default 4; the limit follows the current reading while the command runs and is never below test_timeout_sec
  - it applies to every command Ratchet runs for a step: the step's tests (red, behavior), automatic checks and
    the full regression command at complete
  - when a command ran with a limit above the configured one, its saved log gets a line saying the limit was
    'stretched', with the configured limit, the limit used, the load and the CPU count, for example
        time limit stretched 2.0x (2s to 4s) at load 8.0 on 4 CPUs
    The cases assert on those words and numbers, not on exact spacing. On an idle machine (or with no load reading)
    the log has no such line.
  - a command that really hangs is stopped at the stretched limit with 'TIMEOUT after <limit>s' in its log, and its
    whole process group is killed

Throwaway folder names are neutral (ratchet-p-*), never holding the key words the cases look for. Timing margins
are at least 0.8s on both sides of every bound so a busy machine does not flip a case.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP2-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import trust_helpers as tt  # noqa: E402  (rx, git, make_env, now_dt)
import test_signing as ts  # noqa: E402  (open_round, record_host_review, write_tests2)

README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_CHECKS: dict = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+)\]")
PY = shlex.quote(sys.executable)
_PIDFILES: list = []


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:160]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


# ---- fake machine readings ----------------------------------------------------------------------------------------

def fake(env, load, cpus):
    return dict(env, RATCHET_FAKE_LOADAVG=str(load), RATCHET_FAKE_CPUS=str(cpus))


def busy(env):
    return fake(env, "8", "4")      # factor 2


def idle(env):
    return fake(env, "0.5", "4")    # factor 1


# ---- running the engine with a hard cap, so a hung implementation fails a case instead of hanging the file --------

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


def reap(*dirs):
    """Kill anything a hang script left behind (an implementation that does not kill the tree leaks it)."""
    for d in dirs:
        for p in Path(d).glob("*.pid"):
            try:
                os.kill(int(p.read_text().strip()), signal.SIGKILL)
            except (ValueError, OSError):
                pass


def _reap_all():
    reap(*_PIDFILES)


import atexit  # noqa: E402
atexit.register(_reap_all)


class Run:
    def __init__(self, rc, out, err, wall):
        self.rc, self.out, self.err, self.wall = rc, out or "", err or "", wall

    @property
    def text(self):
        return self.out + "\n" + self.err


def rx(d, env, *args, cap=120):
    """ratchet.py <args> in d. A run that exceeds `cap` seconds is killed (rc None)."""
    t0 = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(tt.ENGINE), *args], cwd=str(d), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=cap)
        return Run(proc.returncode, out, err, time.monotonic() - t0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate()
        return Run(None, out, err, time.monotonic() - t0)


# ---- throwaway projects (the shape of tests/test_signing.py's project2, with our own step command) -----------------

def work(name="w") -> Path:
    w = Path(_tmp.mkdtemp(prefix=f"ratchet-p-{name}-"))
    _PIDFILES.append(w)
    return w


def project_n(env, n, cmd, config=(), report=None) -> Path:
    d = Path(_tmp.mkdtemp(prefix="ratchet-p-"))
    tt.git(d, "init", "-q")
    tt.git(d, "config", "user.email", "t@t")
    tt.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    tt.git(d, "add", "-A")
    tt.git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"

    def say(text):
        with tp.open("a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                "timestamp": tt.now_dt().isoformat()}) + "\n")
        tt.rx(d, env, "hook", "prompt", "--host", "claude",
              stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner", "transcript_path": str(tp)}))

    (d / ".ratchet").mkdir(exist_ok=True)
    say("please build the feature")
    r = tt.rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    r = tt.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", *config)
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cps = []
    for i in range(1, n + 1):
        tests = {"files": [f"tests/cp{i}.test.js"], "command": cmd}
        if report:
            tests["report"] = report
        cps.append({"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
                    "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low", "tests": tests})
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": cps}))
    r = tt.rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    tt.rx(d, env, "ask", "approve-plan")
    say("yes, go ahead")
    r = tt.rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def gate(d, cp="CP1", name="behavior"):
    return state(d)["checkpoints"][cp]["gates"][name]


def evidence(d, cp="CP1") -> Path:
    return Path(d) / ".ratchet/runs/f/evidence" / cp


def read(p) -> str:
    try:
        return Path(p).read_text(errors="replace")
    except OSError:
        return ""


def stretch_line(text: str) -> str:
    for ln in text.splitlines():
        if "stretched" in ln.lower():
            return ln
    return ""


def duration_of(log_text: str):
    m = re.search(r"# exit=\S+ duration=([\d.]+)s", log_text)
    return float(m.group(1)) if m else None


def has_num(line: str, n: int) -> bool:
    return re.search(rf"(?<![\d.]){n}(?:\.0+)?(?![\d.])", line) is not None


# ---- step scripts -------------------------------------------------------------------------------------------------
# Each is written to a work folder outside the project; `cmd` is the command string a step runs.

def write_script(w: Path, body: str, name="cmd.py") -> str:
    p = w / name
    p.write_text(body)
    return f"{PY} {shlex.quote(str(p))}"


def script_need(need: str, body: str) -> str:
    """Exits 1 right away while `need` (a project file) does not exist, then runs `body`."""
    return ("import os, sys, time, subprocess\n"
            f"if not os.path.exists({need!r}):\n    print('not implemented yet')\n    sys.exit(1)\n" + body)


def sleep_script(w, secs, need="src/f1.js", started=None) -> str:
    mark = f"open({str(started)!r}, 'w').write('1')\n" if started else ""
    return write_script(w, script_need(need, mark + f"time.sleep({secs})\nsys.exit(0)\n"))


def hang_script(w, need="src/f1.js") -> str:
    """Starts a background child (same process group) and records both pids, then sleeps 300 seconds."""
    return write_script(w, script_need(need, (
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],\n"
        "                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(w / 'child.pid')!r}, 'w').write(str(child.pid))\n"
        f"open({str(w / 'script.pid')!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(300)\n")))


def switch_script(w) -> str:
    """Behaves as W/mode.json says: {"need": file, "mode": "fast"|"slow"|"flaky"}."""
    return write_script(w, (
        "import json, os, sys, time\n"
        f"W = {str(w)!r}\n"
        "m = json.load(open(os.path.join(W, 'mode.json')))\n"
        "if not os.path.exists(m['need']):\n    print('not implemented yet')\n    sys.exit(1)\n"
        "if m['mode'] == 'slow':\n    time.sleep(2.8)\n    sys.exit(0)\n"
        "if m['mode'] == 'flaky':\n"
        "    p = os.path.join(W, 'counter')\n"
        "    n = int(open(p).read() or '0') + 1 if os.path.exists(p) else 1\n"
        "    open(p, 'w').write(str(n))\n"
        "    sys.exit(1 if n == 1 else 0)\n"
        "sys.exit(0)\n"))


def set_mode(w, need, mode):
    (w / "mode.json").write_text(json.dumps({"need": need, "mode": mode}))


def to_red(d, env, create_f1_first=False):
    """tests-written + red (the red run is fast unless the script hangs). Returns red's Run."""
    ts.write_tests2(d, "CP1", 1)
    r = tt.rx(d, env, "tests-written", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    if create_f1_first:
        (d / "src/f1.js").write_text("export const f = () => 1;\n")
    return None


def implement(d, env):
    (d / "src/f1.js").write_text("export const f = () => 1;\n")
    r = tt.rx(d, env, "implemented", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout


def staged(cmd, timeout, config=(), report=None, n=1):
    """A project at the red step: tests written, nothing run yet."""
    env = tt.make_env("claude")
    d = project_n(env, n, cmd, [f"test_timeout_sec={timeout}", *config], report)
    to_red(d, env)
    return d, env


def busy_pass_scenario(config=(), cmd_fn=None, timeout=2):
    """Red fails fast, src/f1.js appears, then behavior runs on a busy machine."""
    w = work("s")
    cmd = cmd_fn(w) if cmd_fn else sleep_script(w, 2.8)
    d, env = staged(cmd, timeout, config)
    r = tt.rx(d, env, "red", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    implement(d, env)
    return d, env, rx(d, busy(env), "behavior", "CP1")


_CACHE: dict = {}


def once(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


# ---- CP2-T01 / T02: a busy machine gives a slow test more time, and the log says so -----------------------------------

def t01():
    print("CP2-T01: slow only because the machine is busy: not failed")
    d, env, r = once("busy_pass", busy_pass_scenario)
    log = read(evidence(d) / "behavior-01.log")
    g = gate(d)
    check("[CP2-T01] behavior exits 0 on a busy machine (limit 2s stretched to 4s, the tests need 2.8s)",
          r.rc == 0, (r.rc, r.text[-300:]))
    check("[CP2-T01] the behavior gate is passed", g.get("status") == "passed", g)
    check("[CP2-T01] the command ran once: no behavior-01b.log", not (evidence(d) / "behavior-01b.log").exists(),
          sorted(p.name for p in evidence(d).iterdir()))
    check("[CP2-T01] the gate is not marked flaky and the attempt count is still 1",
          not g.get("flaky") and g.get("attempts") == 1, g)
    check("[CP2-T01] the log does not contain TIMEOUT", "TIMEOUT" not in log and "TIMEOUT" not in r.text, log[:300])


def t02():
    print("CP2-T02: the saved log says the limit was stretched")
    d, env, r = once("busy_pass", busy_pass_scenario)
    log = read(evidence(d) / "behavior-01.log")
    line = stretch_line(log)
    head = "\n".join(log.splitlines()[:6])
    check("[CP2-T02] the log has a line saying the limit was stretched", bool(line), head)
    check("[CP2-T02] it names the configured limit 2s and the limit used 4s",
          re.search(r"(?<!\d)2s\b", line) is not None and re.search(r"(?<!\d)4s\b", line) is not None, line)
    check("[CP2-T02] it names the load reading 8", has_num(line, 8), line)
    check("[CP2-T02] it names the CPU count 4", re.search(r"(?<![\d.])4 CPUs?\b", line, re.I) is not None, line)
    lines = log.splitlines()
    check("[CP2-T02] the first line is still `$ <command>`", bool(lines) and lines[0].startswith("$ "), lines[:1])
    check("[CP2-T02] the `# exit=0 duration=` line is still there",
          re.search(r"^# exit=0 duration=[\d.]+s", log, re.M) is not None, head)


# ---- CP2-T03: an idle machine keeps the configured limit exactly -----------------------------------------------------

def hang_red(load, cpus, timeout=1, config=(), key="hang"):
    w = work(key)
    d, env = staged(hang_script(w), timeout, config)
    (d / "src/f1.js").write_text("export const f = () => 1;\n")  # so the script reaches its hang
    r = rx(d, fake(env, load, cpus), "red", "CP1", cap=60)
    time.sleep(0.2)
    return d, env, w, r


def t03():
    print("CP2-T03: on an idle machine the limit is exactly the configured one")
    for load, cpus in (("0.5", "4"), ("4", "4"), ("0", "1")):
        d, env, w, r = hang_red(load, cpus)
        log = read(evidence(d) / "red.log")
        label = f"load {load} on {cpus} CPUs"
        reap(w)
        check(f"[CP2-T03] {label}: red.log says TIMEOUT after 1s", "TIMEOUT after 1s" in log, log[:300])
        check(f"[CP2-T03] {label}: no 'stretched' wording in the log or the output",
              "stretched" not in (log + r.text).lower(), stretch_line(log + r.text))
        check(f"[CP2-T03] {label}: the run took at least 1s and under 3s", 1.0 <= r.wall < 3.0, r.wall)


# ---- CP2-T04: a real hang is stopped at the stretched limit ---------------------------------------------------------

def t04():
    print("CP2-T04: a command that really hangs is stopped at the stretched limit")
    w = work("hang")
    d, env = staged(hang_script(w, need="src/f1.js"), 1)
    r0 = tt.rx(d, env, "red", "CP1")
    assert r0.returncode == 0, r0.stderr + r0.stdout
    implement(d, env)
    r = rx(d, busy(env), "behavior", "CP1", cap=90)
    reap(w)
    log = read(evidence(d) / "behavior-01.log")
    g = gate(d)
    check("[CP2-T04] behavior exits non-zero with 'Behavior gate FAILED'",
          r.rc not in (0, None) and "Behavior gate FAILED" in r.text, (r.rc, r.text[-300:]))
    check("[CP2-T04] the output shows TIMEOUT after 2s", "TIMEOUT after 2s" in r.text, r.text[-300:])
    check("[CP2-T04] the gate is failed with attempts 1", g.get("status") == "failed" and g.get("attempts") == 1, g)
    check("[CP2-T04] behavior-01.log holds the TIMEOUT line and the stretch note",
          "TIMEOUT after 2s" in log and bool(stretch_line(log)), log[:300])
    dur = duration_of(log)
    check("[CP2-T04] the first run lasted at least 2 seconds (it was not cut at 1)",
          dur is not None and dur >= 1.95, dur)
    check("[CP2-T04] the call returned well before 300 seconds", r.rc is not None and r.wall < 60, r.wall)


# ---- CP2-T05: the whole process tree is killed ----------------------------------------------------------------------

def t05():
    print("CP2-T05: a timed-out command's whole process tree is killed")
    d, env, w, r = hang_red("8", "4")
    try:
        child = int(read(w / "child.pid").strip() or 0)
        script = int(read(w / "script.pid").strip() or 0)
        check("[CP2-T05] the hang script started and recorded its pids", child > 0 and script > 0,
              (child, script, read(evidence(d) / "red.log")[:200]))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and (pid_alive(child) or pid_alive(script)):
            time.sleep(0.1)
        check("[CP2-T05] the background child is no longer alive", child > 0 and not pid_alive(child), child)
        check("[CP2-T05] the script itself is no longer alive", script > 0 and not pid_alive(script), script)
        check("[CP2-T05] the run stopped at the stretched limit (2s), long before 300s",
              r.rc is not None and r.wall < 30, r.wall)
    finally:
        reap(w)


# ---- CP2-T06: a spike in the middle of the run still extends the limit ---------------------------------------------

def t06():
    print("CP2-T06: a load spike in the middle of a run extends the limit")
    w = work("spike")
    started = w / "started"
    reading = w / "reading.txt"
    reading.write_text("1")
    d, env = staged(sleep_script(w, 2.8, started=started), 2)
    r0 = tt.rx(d, env, "red", "CP1")
    assert r0.returncode == 0, r0.stderr + r0.stdout
    implement(d, env)
    stop = threading.Event()

    def spike():
        t_end = time.monotonic() + 60
        while not stop.is_set() and time.monotonic() < t_end:
            if started.exists():
                time.sleep(0.5)
                tmp = w / "reading.tmp"
                tmp.write_text("12")
                os.replace(tmp, reading)
                return
            time.sleep(0.02)

    th = threading.Thread(target=spike, daemon=True)
    th.start()
    r = rx(d, fake(env, f"@{reading}", "4"), "behavior", "CP1", cap=90)
    stop.set()
    th.join(5)
    log = read(evidence(d) / "behavior-01.log")
    line = stretch_line(log)
    check("[CP2-T06] behavior exits 0 (the limit was recomputed from the new reading while the command ran)",
          r.rc == 0, (r.rc, r.text[-300:]))
    check("[CP2-T06] the log says the limit was stretched", bool(line), log[:300])
    check("[CP2-T06] it names the higher load 12", has_num(line, 12), line)
    check("[CP2-T06] the log does not contain TIMEOUT", "TIMEOUT" not in log, log[:300])


# ---- CP2-T07: the ceiling, and its default ------------------------------------------------------------------------------

def t07():
    print("CP2-T07: the stretch has a configurable ceiling")
    d, env, w, r = hang_red("100", "4", config=["load_stretch_max=2"], key="ceil")
    reap(w)
    log = read(evidence(d) / "red.log")
    check("[CP2-T07] red.log says TIMEOUT after 2s (the ceiling), not 25s", "TIMEOUT after 2s" in log, log[:300])
    check("[CP2-T07] the run took under 5 seconds", r.rc is not None and r.wall < 5.0, r.wall)
    base = Path(_tmp.mkdtemp(prefix="ratchet-p-cfg-"))
    benv = tt.make_env("claude")
    tt.git(base, "init", "-q")
    pr = tt.rx(base, benv, "config")
    try:
        cfg = json.loads(pr.stdout)
    except ValueError:
        cfg = {}
    check("[CP2-T07] the printed default config has load_stretch_max equal to 4",
          pr.returncode == 0 and cfg.get("load_stretch_max") == 4, (pr.returncode, pr.stdout[-200:], pr.stderr[-200:]))


# ---- CP2-T08: no load reading ---------------------------------------------------------------------------------------------

def t08():
    print("CP2-T08: a machine with no load reading keeps the configured limit and does not crash")
    d, env, w, r = hang_red("unavailable", "4", key="noread")
    reap(w)
    log = read(evidence(d) / "red.log")
    check("[CP2-T08] red.log says TIMEOUT after 1s", "TIMEOUT after 1s" in log, log[:300])
    check("[CP2-T08] no stretch wording", "stretched" not in (log + r.text).lower(), stretch_line(log + r.text))
    check("[CP2-T08] neither stdout nor stderr holds a Python traceback", "Traceback" not in r.text, r.text[-400:])
    check("[CP2-T08] red finished (exit 0) instead of crashing", r.rc == 0, (r.rc, r.text[-200:]))


# ---- two-step builder used by T09 and T13 ----------------------------------------------------------------------------------

def two_step(config):
    w = work("two")
    env = tt.make_env("claude")
    set_mode(w, "src/f1.js", "fast")
    d = project_n(env, 2, switch_script(w), ["test_timeout_sec=2", *config])
    return d, env, w


def pass_first_step(d, env, w, mode="fast", behavior_env=None):
    ts.write_tests2(d, "CP1", 1)
    assert tt.rx(d, env, "tests-written", "CP1").returncode == 0
    assert tt.rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f1.js").write_text("export const f = () => 1;\n")
    assert tt.rx(d, env, "implemented", "CP1").returncode == 0
    set_mode(w, "src/f1.js", mode)
    return rx(d, behavior_env or env, "behavior", "CP1", cap=90)


def review_first_step(d, env):
    rnd = ts.open_round(d, env, "CP1")
    _, r0 = ts.record_host_review(d, env, rnd, findings=[], cp="CP1", files_reviewed=["src/f1.js"])
    assert r0.returncode == 0, r0.stderr + r0.stdout


# ---- CP2-T09: the full suite at complete ---------------------------------------------------------------------------------

def t09():
    print("CP2-T09: the full suite at the end of a step gets more time on a busy machine")
    w2 = work("reg")
    reg = write_script(w2, "import sys, time\ntime.sleep(2.8)\nsys.exit(0)\n", name="reg.py")
    d, env, w = two_step([f"regression_command={json.dumps(reg)}"])
    rb = pass_first_step(d, env, w, mode="fast")
    assert rb.rc == 0, rb.text[-300:]
    review_first_step(d, env)
    r = rx(d, busy(env), "complete", "CP1", cap=90)
    log = read(evidence(d) / "regression.log")
    line = stretch_line(log)
    check("[CP2-T09] complete exits 0 on a busy machine", r.rc == 0, (r.rc, r.text[-300:]))
    check("[CP2-T09] regression.log has the stretch note naming 2s, 4s and load 8",
          bool(line) and re.search(r"(?<!\d)2s\b", line) is not None and re.search(r"(?<!\d)4s\b", line) is not None
          and has_num(line, 8), log[:300])
    check("[CP2-T09] regression.log has no TIMEOUT", bool(log) and "TIMEOUT" not in log, log[:300])
    check("[CP2-T09] no regression-retry.log exists", not (evidence(d) / "regression-retry.log").exists(),
          sorted(p.name for p in evidence(d).iterdir()))
    check("[CP2-T09] regression_flaky_used is still unset", not state(d).get("regression_flaky_used"),
          state(d).get("regression_flaky_used"))


# ---- CP2-T10: automatic checks ---------------------------------------------------------------------------------------------

def t10():
    print("CP2-T10: an automatic check run after the tests is stretched too")
    w2 = work("chk")
    chk_cmd = write_script(w2, "import sys, time\ntime.sleep(2.8)\nsys.exit(0)\n", name="chk.py")
    check1 = {"kind": "lint-no-console", "title": "No console.log left in", "command": chk_cmd,
              "added": "2026-01-01", "from": "x/CP1"}

    def fast(w):
        return write_script(w, script_need("src/f1.js", "sys.exit(0)\n"))

    d, env, r = busy_pass_scenario(config=[f"checks={json.dumps([check1])}"], cmd_fn=fast)
    logs = sorted(evidence(d).glob("check-*-01.log"))
    log = read(logs[0]) if logs else ""
    check("[CP2-T10] behavior exits 0 on a busy machine", r.rc == 0, (r.rc, r.text[-300:]))
    check("[CP2-T10] the check wrote its own log (check-*-01.log)", bool(logs), sorted(p.name for p in evidence(d).iterdir()))
    check("[CP2-T10] the check's log carries the stretch note", bool(stretch_line(log)), log[:300])
    check("[CP2-T10] the check's log has no TIMEOUT", bool(log) and "TIMEOUT" not in log, log[:300])


# ---- CP2-T11: a lot of output ------------------------------------------------------------------------------------------------

def t11():
    print("CP2-T11: a test command that prints a lot of output does not stall or time out")
    body = ("line = 'x' * 99 + '\\n'\n"
            "sys.stdout.write(line * 6000 + 'STDOUT-END-91c\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stderr.write(line * 6000 + 'STDERR-END-5d2\\n')\n"
            "sys.stderr.flush()\n"
            "sys.exit(0)\n")
    w = work("big")
    d, env = staged(write_script(w, script_need("src/f1.js", body)), 5)
    assert tt.rx(d, env, "red", "CP1").returncode == 0
    implement(d, env)
    r = rx(d, idle(env), "behavior", "CP1", cap=90)
    log = read(evidence(d) / "behavior-01.log")
    dur = duration_of(log)
    check("[CP2-T11] behavior exits 0", r.rc == 0, (r.rc, r.text[-300:]))
    check("[CP2-T11] the command finished well under the 5s limit (no full-pipe stall)",
          dur is not None and dur < 3.5, dur)
    check("[CP2-T11] the log keeps the first command line", log.startswith("$ "), log[:80])
    check("[CP2-T11] the log keeps the distinctive last line of stdout and of stderr",
          "STDOUT-END-91c" in log and "STDERR-END-5d2" in log, (len(log), log[-120:]))
    check("[CP2-T11] no TIMEOUT", "TIMEOUT" not in log, log[:120])


# ---- CP2-T12: environment, folder, exit code, log header ------------------------------------------------------------

PROBE = ("print('cwd=' + os.getcwd())\n"
         "print('CI=' + str(os.environ.get('CI')))\n"
         "print('FORCE_COLOR=' + str(os.environ.get('FORCE_COLOR')))\n"
         "print('REPORT=' + ('unset' if 'RATCHET_TEST_REPORT' not in os.environ else os.environ['RATCHET_TEST_REPORT']))\n"
         "sys.exit(3)\n")


def t12():
    print("CP2-T12: running a command still behaves as before")
    w = work("probe")
    d, env = staged(write_script(w, script_need("src/f1.js", PROBE)), 5)
    (d / "src/f1.js").write_text("export const f = () => 1;\n")
    # an outer report variable must never reach the step's command when the step has no report
    penv = dict(idle(env), RATCHET_TEST_REPORT="/nonexistent/outer-report.json")
    r = rx(d, penv, "red", "CP1", cap=60)
    red_log = read(evidence(d) / "red.log")
    check("[CP2-T12] red records the run (exit 0 from ratchet.py)", r.rc == 0, (r.rc, r.text[-300:]))
    check("[CP2-T12] red.log has the `# exit=3` header", re.search(r"^# exit=3 duration=", red_log, re.M) is not None,
          red_log[:200])
    check("[CP2-T12] the first line of red.log is `$ <command>`", red_log.startswith("$ "), red_log[:60])
    assert tt.rx(d, env, "implemented", "CP1").returncode == 0
    rb = rx(d, penv, "behavior", "CP1", cap=60)
    blog = read(evidence(d) / "behavior-01.log")
    check("[CP2-T12] behavior fails with 'exit 3' in its message", rb.rc not in (0, None) and "exit 3" in rb.text,
          (rb.rc, rb.text[-300:]))
    check("[CP2-T12] the command ran in the project folder", f"cwd={d.resolve()}" in blog, blog[:300])
    check("[CP2-T12] the command saw CI=1 and FORCE_COLOR=0", "CI=1" in blog and "FORCE_COLOR=0" in blog, blog[:300])
    check("[CP2-T12] the command saw no RATCHET_TEST_REPORT when the step has no report", "REPORT=unset" in blog,
          blog[:300])
    # a step with a json report and an old report file already there
    w2 = work("probe2")
    probe_file = w2 / "seen.json"
    body = ("import json\n"
            "p = os.environ.get('RATCHET_TEST_REPORT')\n"
            f"json.dump({{'report': p, 'old_existed': bool(p and os.path.exists(p))}}, open({str(probe_file)!r}, 'w'))\n"
            "sys.exit(3)\n")
    d2, env2 = staged(write_script(w2, script_need("src/f1.js", body)), 5, report="json")
    (d2 / "src/f1.js").write_text("export const f = () => 1;\n")
    ev = evidence(d2)
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "test-report.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "status": "passed"}]}))
    rx(d2, idle(env2), "red", "CP1", cap=60)
    seen = json.loads(read(probe_file) or "{}")
    rep = seen.get("report")
    check("[CP2-T12] with report json the command sees RATCHET_TEST_REPORT inside evidence/CP1",
          bool(rep) and Path(rep).resolve().parent == ev.resolve(), seen)
    check("[CP2-T12] the old report file was gone before the run", seen.get("old_existed") is False, seen)


# ---- CP2-T13: a stretched pass does not spend the flaky allowance --------------------------------------------------------

def t13():
    print("CP2-T13: a stretched pass does not use up the flaky-retry allowance")
    d, env, w = two_step([])
    ts.write_tests2(d, "CP1", 1)
    assert tt.rx(d, env, "tests-written", "CP1").returncode == 0
    assert tt.rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f1.js").write_text("export const f = () => 1;\n")
    assert tt.rx(d, env, "implemented", "CP1").returncode == 0
    set_mode(w, "src/f1.js", "slow")
    r = rx(d, busy(env), "behavior", "CP1", cap=90)
    g = gate(d)
    check("[CP2-T13] the busy slow step passes", r.rc == 0 and g.get("status") == "passed", (r.rc, r.text[-300:]))
    check("[CP2-T13] after the stretched pass flaky_used is unset on that step", not g.get("flaky_used"), g)
    if g.get("status") != "passed":
        return  # nothing to build the second step on; the checks above already failed this case
    review_first_step(d, env)
    rc = tt.rx(d, env, "complete", "CP1")
    assert rc.returncode == 0, rc.stderr + rc.stdout
    ts.write_tests2(d, "CP2", 2)
    assert tt.rx(d, env, "tests-written", "CP2").returncode == 0
    assert tt.rx(d, idle(env), "red", "CP2").returncode == 0
    (d / "src/f2.js").write_text("export const f = () => 1;\n")
    assert tt.rx(d, env, "implemented", "CP2").returncode == 0
    set_mode(w, "src/f2.js", "flaky")
    r2 = rx(d, idle(env), "behavior", "CP2", cap=90)
    g2 = gate(d, "CP2")
    check("[CP2-T13] the other step's one-time retry still works: it passes", r2.rc == 0 and g2.get("status") == "passed",
          (r2.rc, r2.text[-300:]))
    check("[CP2-T13] that step is marked flaky and its allowance is spent", g2.get("flaky") is True and g2.get("flaky_used") is True,
          g2)


# ---- CP2-T14: docs ------------------------------------------------------------------------------------------------------------

def mentions_stretch(text: str) -> bool:
    return re.search(r"stretch\w*[^.\n]{0,200}\bload\b|\bload\b[^.\n]{0,200}stretch", text, re.I) is not None


def t14():
    print("CP2-T14: README, SKILL.md and schemas.md describe the stretch and its ceiling")
    for label, path in (("README.md", README), ("the orchestrator SKILL.md", SKILL), ("schemas.md", SCHEMAS)):
        text = path.read_text(encoding="utf-8")
        check(f"[CP2-T14] {label} names load_stretch_max", "load_stretch_max" in text)
        check(f"[CP2-T14] {label} says the time limit stretches with machine load", mentions_stretch(text))
    s = SCHEMAS.read_text(encoding="utf-8")
    lines = s.splitlines()
    idx = [i for i, ln in enumerate(lines) if "load_stretch_max" in ln]
    para = "\n".join(lines[max(0, idx[0] - 4): idx[0] + 8]) if idx else ""
    check("[CP2-T14] schemas.md lists load_stretch_max with default 4",
          bool(idx) and re.search(r"load_stretch_max[^\n]*(?<![\d.])4(?![\d.])", para) is not None, para[:300])
    check("[CP2-T14] schemas.md says an idle machine keeps the configured limit",
          bool(para) and re.search(r"idle", para, re.I) is not None and "configured" in para.lower(), para[:400])
    check("[CP2-T14] schemas.md says the log notes a stretch",
          bool(para) and re.search(r"\blog\b", para, re.I) is not None and "stretch" in para.lower(), para[:400])


TESTS = [("CP2-T01", t01), ("CP2-T02", t02), ("CP2-T03", t03), ("CP2-T04", t04), ("CP2-T05", t05),
         ("CP2-T06", t06), ("CP2-T07", t07), ("CP2-T08", t08), ("CP2-T09", t09), ("CP2-T10", t10),
         ("CP2-T11", t11), ("CP2-T12", t12), ("CP2-T13", t13), ("CP2-T14", t14)]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return

    def status(cid):
        return "passed" if CASE_STATUS.get(cid, False) and CASE_CHECKS.get(cid) else "failed"

    cases = []
    for cid, _ in TESTS:
        st = status(cid)
        msg = "" if st == "passed" else ("; ".join(CASE_MESSAGES.get(cid, []))[:300] or "no checks ran")
        cases.append({"id": cid, "status": st, "message": msg})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crashed case fails; it never takes the other cases down
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(e).__name__}: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
