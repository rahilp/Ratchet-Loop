#!/usr/bin/env python3
"""
CP4 tests: Ratchet stops a test run when the code changes under it.   python3 tests/test_stop_on_change.py

When the code changes while Ratchet is running a step's tests (`behavior`: the step's own tests and the automatic
checks; `complete`: the related pass, the full suite and its one automatic re-run), Ratchet stops that run at once,
says the code changed and names the command to run again, frees its lock, and leaves every counter as it was: a
stopped run is never a pass, never a failed attempt, never a spent flaky allowance. The check interval is the config
key `suite_watch_seconds` (default 30, a positive number); a run that ends before the next look is still compared
once more at its end, so a quick suite can never stamp a gate with a tree it never ran against.

Black-box: every case drives the real ratchet.py in a throwaway git project built the way tests/test_load_timeouts.py
builds its projects (tests/trust_helpers.py's hermetic env, the chat transcript inside the project as t.jsonl). The
step command (and the regression, related and check commands) is one small fake suite script this file writes to a
scratch folder OUTSIDE the project. Each call of the fake takes its behaviour from a per-fixture modes list (exit at
once, sleep, write a marker, or "slow": record its pids, start grandchildren and sleep up to two minutes), writes a
`call-N.started` marker, and for the slow modes a `call-N.ready` marker once its pids are written. The harness starts
the ratchet command in its own process group, waits for the ready marker, appends a line to src/a.js as the "agent
edit", then measures how long the ratchet command takes to end. Processes are found afterwards by a unique tag in
their environment (/proc scan) and by the pids the fake wrote. Every process started here is killed at the end of
its case and again at exit. Scratch folders use the neutral prefix ratchet-q4-. Engine calls get
RATCHET_FAKE_LOADAVG=0 so CP2's load stretch never changes a limit. Timing bounds keep a wide margin: a stop is
asserted within 15 s of the edit against a suite that would otherwise run 120 s.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP4-T01", "status": "passed"|"failed"|"error", "message": "..."}]}.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import trust_helpers as tt  # noqa: E402  (ENGINE, make_env, rx, git, now_dt, guard)
import test_signing as ts  # noqa: E402  (open_round, record_host_review, write_tests2)

README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_ERRORS: dict = {}
ID_RE = re.compile(r"\[(CP4-T\d+)\]")
PY = shlex.quote(sys.executable)
TAG_VAR = "Q4_SUITE_TAG"
STOP_BOUND = 15.0       # a stop must end the ratchet command within this many seconds of the edit
SLOW_SECONDS = 120      # how long the fake's slow modes would run if nobody stopped them
_WORKS: list = []       # every scratch folder a fake suite wrote pids into (reaped at exit)
_TAGS: list = []        # every tag used (swept at exit)


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:160]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


# ---- processes: liveness, tag sweep, reaping ----------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """True for a running process; a zombie (dead, awaiting reaping) does not count."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def tagged(tag: str) -> list:
    """Pids of live processes (not zombies, not this one) whose environment carries TAG_VAR=tag. [] without /proc."""
    out = []
    needle = f"{TAG_VAR}={tag}".encode()
    proc = Path("/proc")
    if not proc.is_dir():
        return out
    for p in proc.iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            env = (p / "environ").read_bytes()
        except OSError:
            continue
        if needle in env.split(b"\0") and pid_alive(int(p.name)):
            out.append(int(p.name))
    return out


def recorded_pids(work: Path) -> list:
    pids = []
    for f in Path(work).glob("call-*.pids"):
        try:
            pids.extend(int(x) for x in json.loads(f.read_text()))
        except (ValueError, OSError):
            pass
    return pids


def wait_dead(pids, timeout=5.0) -> list:
    """Pids still alive after `timeout` seconds (polling)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(pid_alive(p) for p in pids):
            return []
        time.sleep(0.1)
    return [p for p in pids if pid_alive(p)]


def wait_untagged(tag, timeout=5.0) -> list:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not tagged(tag):
            return []
        time.sleep(0.1)
    return tagged(tag)


def reap(work=None, tag=None):
    """Kill anything a fake suite left behind: its recorded pids and every process carrying its tag."""
    for pid in (recorded_pids(work) if work else []) + (tagged(tag) if tag else []):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _reap_all():
    for w in _WORKS:
        reap(work=w)
    for t in _TAGS:
        reap(tag=t)


atexit.register(_reap_all)


def _dfl():
    """Default dispositions in the child, whatever the parent inherited (nohup leaves SIGHUP ignored)."""
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.SIG_DFL)


# ---- the fake suite -------------------------------------------------------------------------------------------------

SCRIPTS = Path(_tmp.mkdtemp(prefix="ratchet-q4-scripts-"))
FAKE = SCRIPTS / "q4_suite.py"
FAKE.write_text(f"SLOW = {SLOW_SECONDS}\n" + r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path

def dfl():
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.SIG_DFL)

dfl()
work = Path(sys.argv[1])
modes = json.loads((work / "modes.json").read_text())
n = int((work / "n").read_text().strip() or "0") + 1 if (work / "n").exists() else 1
(work / "n").write_text(str(n))
mode = modes[min(n, len(modes)) - 1]
(work / f"call-{n}.started").write_text(str(time.time()))
kids = []
SLEEP = "import time\nfor _ in range(%d): time.sleep(0.1)\n" % (SLOW * 10)
IGNORE = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nfor _ in range(%d): time.sleep(0.1)\n" % (SLOW * 10)

def child(code, session=False):
    p = subprocess.Popen([sys.executable, "-c", code], start_new_session=session, preexec_fn=dfl,
                         stdin=subprocess.DEVNULL)
    kids.append(p)
    return p

def ready():
    (work / f"call-{n}.pids").write_text(json.dumps([os.getpid()] + [p.pid for p in kids]))
    (work / f"call-{n}.ready").write_text("ready")

def nap(sec):
    end = time.time() + sec
    while time.time() < end:
        time.sleep(0.1)

if mode.startswith("exit:"):
    sys.exit(int(mode[5:]))
if mode.startswith("sleep:"):
    nap(float(mode[6:]))
    sys.exit(0)
if mode == "marker":
    (work / f"call-{n}.marker").write_text("ran")
    sys.exit(0)
if mode == "slow":
    child(SLEEP)
    child(SLEEP)
    ready()
    nap(SLOW)
    sys.exit(0)
if mode == "slow-trap":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child(IGNORE)
    ready()
    nap(SLOW)
    sys.exit(0)
if mode == "slow-session":
    helper = child(SLEEP, session=True)
    def on_term(signum, frame):
        try:
            os.killpg(helper.pid, signal.SIGKILL)
        except OSError:
            pass
        sys.exit(143)
    signal.signal(signal.SIGTERM, on_term)
    ready()
    nap(SLOW)
    sys.exit(0)
if mode == "slow-write":
    root = Path(os.getcwd())
    report = os.environ.get("RATCHET_TEST_REPORT") or str(root / ".ratchet/runs/f/evidence/CP1/test-report.json")
    targets = [root / "build/out.txt", root / "__pycache__/mod.cpython.pyc", Path(report), work / "scratch.txt"]
    end = time.time() + 3.0
    i = 0
    while time.time() < end:
        i += 1
        for t in targets:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text("write %d at %f\n" % (i, time.time()))
        time.sleep(0.3)
    sys.exit(0)
print("unknown mode " + mode)
sys.exit(3)
''')


class Fake:
    """One fake command: its scratch folder (outside the project), its modes list and its call markers."""

    def __init__(self, modes, name="w"):
        self.work = Path(_tmp.mkdtemp(prefix=f"ratchet-q4-{name}-"))
        _WORKS.append(self.work)
        self.set_modes(modes)

    def set_modes(self, modes):
        """Replace the modes and start counting calls from one again."""
        (self.work / "modes.json").write_text(json.dumps(list(modes)))
        (self.work / "n").write_text("0")

    @property
    def cmd(self) -> str:
        return f"{PY} {shlex.quote(str(FAKE))} {shlex.quote(str(self.work))}"

    def calls(self) -> int:
        try:
            return int((self.work / "n").read_text().strip() or "0")
        except (OSError, ValueError):
            return 0

    def started(self, n) -> bool:
        return (self.work / f"call-{n}.started").exists()

    def marker(self, n) -> bool:
        return (self.work / f"call-{n}.marker").exists()

    def wait_started(self, n, timeout=60.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.started(n):
                return True
            time.sleep(0.05)
        return False

    def wait_ready(self, n, timeout=60.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if (self.work / f"call-{n}.ready").exists():
                return True
            time.sleep(0.05)
        return False

    def pids(self, n) -> list:
        try:
            return [int(x) for x in json.loads((self.work / f"call-{n}.pids").read_text())]
        except (OSError, ValueError):
            return []


# ---- running the engine ----------------------------------------------------------------------------------------------

class Run:
    def __init__(self, rc, out, err, wall, after=None, ready=True):
        self.rc, self.out, self.err, self.wall, self.after, self.ready = rc, out or "", err or "", wall, after, ready

    @property
    def text(self):
        return self.out + "\n" + self.err


def start(d, env, *args):
    proc = subprocess.Popen([sys.executable, str(tt.ENGINE), *args], cwd=str(d), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            start_new_session=True, preexec_fn=_dfl)
    _tmp.track(proc)
    return proc


def finish(proc, cap, t0) -> Run:
    """Wait up to `cap` seconds for the engine; a run that exceeds it is killed (rc None) with its process group."""
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


def rx(d, env, *args, cap=120) -> Run:
    t0 = time.monotonic()
    return finish(start(d, env, *args), cap, t0)


def edit_source(d):
    with (d / "src/a.js").open("a") as f:
        f.write("export const edited = 1;\n")


def run_then_edit(d, env, args, fake: Fake, n, wait="ready", edit=edit_source, delay=0.0, cap_after=20.0) -> Run:
    """Start `ratchet.py <args>`, wait for the fake's call `n` to be ready (or started), make the edit, then wait up to
    `cap_after` seconds for the engine to end. `after` is the time from the edit to the end (None when killed)."""
    t0 = time.monotonic()
    proc = start(d, env, *args)
    ok_ = fake.wait_ready(n) if wait == "ready" else fake.wait_started(n)
    if not ok_:
        r = finish(proc, 1.0, t0)
        r.ready = False
        return r
    if delay:
        time.sleep(delay)
    edit(d)
    t_edit = time.monotonic()
    r = finish(proc, cap_after, t0)
    r.after = time.monotonic() - t_edit if r.rc is not None else None
    return r


# ---- throwaway projects ---------------------------------------------------------------------------------------------

def make_env():
    tag = uuid.uuid4().hex
    _TAGS.append(tag)
    env = tt.make_env("claude")
    env.update(RATCHET_FAKE_LOADAVG="0", **{TAG_VAR: tag})
    return env


def say(d, env, text, transcript=None):
    """The user types `text`: a line in the chat transcript (inside the project unless `transcript` says otherwise)
    plus the prompt hook."""
    tp = Path(transcript) if transcript else Path(d) / "t.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": tt.now_dt().isoformat()}) + "\n")
    return tt.rx(d, env, "hook", "prompt", "--host", "claude",
                 stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner",
                                   "transcript_path": str(tp)}))


def project(env, cmd, config=(), files=None, transcript_inside=True, approve=True) -> Path:
    """A one-step git project whose step command is `cmd`; `config` is set before the plan is approved (so a locked
    rule key such as suite_watch_seconds needs no approve-changes)."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-q4-p-"))
    tt.git(d, "init", "-q")
    tt.git(d, "config", "user.email", "t@t")
    tt.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    for rel, text in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    tt.git(d, "add", "-A")
    tt.git(d, "commit", "-qm", "init")
    transcript = None if transcript_inside else str(Path(_tmp.mkdtemp(prefix="ratchet-q4-chat-")) / "t.jsonl")
    (d / ".ratchet").mkdir(exist_ok=True)
    say(d, env, "please build the feature", transcript)
    r = tt.rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    r = tt.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", *config)
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": cmd}}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    r = tt.rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    if approve:
        tt.rx(d, env, "ask", "approve-plan")
        say(d, env, "yes, go ahead", transcript)
        r = tt.rx(d, env, "approve-plan")
        assert r.returncode == 0, r.stderr + r.stdout
    return d


def to_implemented(d, env):
    """tests-written, red (the fake's first call must exit non-zero), src/f1.js, implemented."""
    ts.write_tests2(d, "CP1", 1)
    r = tt.rx(d, env, "tests-written", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    r = tt.rx(d, env, "red", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    (d / "src/f1.js").write_text("export const f = () => 1;\n")
    r = tt.rx(d, env, "implemented", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout


def behavior_project(modes, config=(), **kw):
    """A project at the behavior step whose step command is a fake with `modes` (modes[0] is spent by red)."""
    env = make_env()
    fk = Fake(modes, "t")
    d = project(env, fk.cmd, config, **kw)
    to_implemented(d, env)
    return d, env, fk


def to_complete(d, env):
    """behavior passes, one clean adversarial round is recorded: `complete` is next."""
    r = tt.rx(d, env, "behavior", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    rnd = ts.open_round(d, env, "CP1")
    _, r0 = ts.record_host_review(d, env, rnd, findings=[], cp="CP1", files_reviewed=["src/f1.js"])
    assert r0.returncode == 0, r0.stderr + r0.stdout


def complete_project(regression_modes, config=(), related_modes=None, **kw):
    """A project at the complete step: the step's own tests pass quickly; regression_command (and optionally
    related_test_command) is a fake with the given modes. Returns (d, env, regression fake, related fake)."""
    env = make_env()
    fk_t = Fake(["exit:1", "exit:0"], "t")
    fk_r = Fake(regression_modes, "r")
    cfg = [f"regression_command={fk_r.cmd}", *config]
    fk_rel = None
    if related_modes is not None:
        fk_rel = Fake(related_modes, "rel")
        cfg.append(f"related_test_command={fk_rel.cmd} {{files}}")
    d = project(env, fk_t.cmd, cfg, **kw)
    to_implemented(d, env)
    to_complete(d, env)
    return d, env, fk_r, fk_rel


# ---- reading the project -----------------------------------------------------------------------------------------------

def state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def cps(d):
    return state(d)["checkpoints"]["CP1"]


def gate(d, name="behavior"):
    return cps(d)["gates"][name]


def evidence(d) -> Path:
    return Path(d) / ".ratchet/runs/f/evidence/CP1"


def read(p) -> str:
    try:
        return Path(p).read_text(errors="replace")
    except OSError:
        return ""


def head(d) -> str:
    return tt.git(d, "rev-parse", "HEAD").stdout.strip()


def history(d, event):
    return [h for h in state(d).get("history", []) if h.get("event") == event]


def locks(d) -> list:
    return [n for n in ("state.lock", "state.lock.break") if (Path(d) / ".ratchet" / n).exists()]


def says_changed(text) -> bool:
    return re.search(r"code changed", text, re.I) is not None


def says_again(text) -> bool:
    return re.search(r"\bagain\b|re-?run", text, re.I) is not None


def says_stopped_for_change(text) -> bool:
    return re.search(r"stopp", text, re.I) is not None and says_changed(text)


def behavior_logs(d) -> list:
    return sorted(evidence(d).glob("behavior-*.log"))


def no_runs(g: dict) -> dict:
    return {k: v for k, v in g.items() if k != "runs"}


# ---- CP4-T01..T03, T06: one stopped behavior run, with a bystander ------------------------------------------------------

_SHARED = {}


def shared_stop():
    if "r" not in _SHARED:
        d, env, fk = behavior_project(["exit:1", "slow"], ["suite_watch_seconds=0.3"])
        clean_env = {k: v for k, v in env.items() if k != TAG_VAR}
        bystander = subprocess.Popen([sys.executable, "-c", "import time\nfor _ in range(1200): time.sleep(0.1)"],
                                     cwd=str(d), env=clean_env, start_new_session=True, preexec_fn=_dfl,
                                     stdin=subprocess.DEVNULL)
        _tmp.track(bystander)
        r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        _SHARED.update(d=d, env=env, fk=fk, r=r, bystander=bystander, tag=env[TAG_VAR])
    return _SHARED


def t01():
    s = shared_stop()
    r = s["r"]
    check("[CP4-T01] the fake tests had started (pids and ready marker written) before the edit", r.ready, r.text[-400:])
    check("[CP4-T01] the behavior command ended within a few seconds of the edit, not after the tests' two minutes",
          r.after is not None and r.after < STOP_BOUND, (r.rc, r.after, r.wall))
    check("[CP4-T01] it exited non-zero", r.rc not in (None, 0), r.rc)
    check("[CP4-T01] its output says the code changed and the run was stopped for that reason",
          says_stopped_for_change(r.text), r.text[-600:])
    check("[CP4-T01] its output says to run `behavior CP1` again", "behavior CP1" in r.text and says_again(r.text),
          r.text[-600:])


def t02():
    s = shared_stop()
    logs = behavior_logs(s["d"])
    text = read(logs[-1]) if logs else ""
    check("[CP4-T02] a behavior log for the stopped run exists", bool(logs), [str(p) for p in logs])
    check("[CP4-T02] the log says the run was stopped because the code changed", says_stopped_for_change(text),
          text[:600])
    check("[CP4-T02] the log does not read like a timeout", "TIMEOUT" not in text, text[:600])
    check("[CP4-T02] the log does not read like a test failure", not re.search(r"fail", text, re.I), text[:600])


def t03():
    s = shared_stop()
    pids = s["fk"].pids(2)
    alive = wait_dead(pids)
    check("[CP4-T03] the fake recorded its own pid and two grandchildren", len(pids) == 3, pids)
    check("[CP4-T03] none of the recorded pids is alive a few seconds after the stop", pids and not alive, alive)
    left = wait_untagged(s["tag"])
    check("[CP4-T03] no process anywhere carries the run's tag", not left, left)
    if not Path("/proc").is_dir():
        print("  (tag sweep not run: no /proc on this system)")


def t06():
    s = shared_stop()
    by = s["bystander"]
    check("[CP4-T06] the bystander in its own process group is still alive", by.poll() is None, by.poll())
    check("[CP4-T06] the ratchet command exited itself with its own exit code, not killed by a signal",
          s["r"].rc is not None and s["r"].rc > 0, s["r"].rc)
    check("[CP4-T06] it printed its message before exiting", says_changed(s["r"].text), s["r"].text[-400:])
    by.kill()
    by.wait()
    reap(s["fk"].work, s["tag"])


# ---- CP4-T04, T05: stubborn and detached test processes -----------------------------------------------------------------

def t04():
    d, env, fk = behavior_project(["exit:1", "slow-trap"], ["suite_watch_seconds=0.3"])
    try:
        r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        check("[CP4-T04] the fake tests (which ignore the terminate signal) had started", r.ready, r.text[-400:])
        check("[CP4-T04] the ratchet command still ends within about ten seconds of the edit",
              r.after is not None and r.after < STOP_BOUND, (r.rc, r.after))
        check("[CP4-T04] it says the code changed", says_changed(r.text), r.text[-400:])
        left = wait_untagged(env[TAG_VAR])
        dead = wait_dead(fk.pids(2))
        check("[CP4-T04] no process carrying the tag is alive afterwards, nor any recorded pid", not left and not dead,
              (left, dead))
    finally:
        reap(fk.work, env[TAG_VAR])


def t05():
    d, env, fk = behavior_project(["exit:1", "slow-session"], ["suite_watch_seconds=0.3"])
    try:
        r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        pids = fk.pids(2)
        check("[CP4-T05] the fake tests and their detached helper had started", r.ready and len(pids) == 2, (r.ready, pids))
        check("[CP4-T05] the ratchet command exited normally with its own code",
              r.rc is not None and r.rc > 0 and r.after is not None and r.after < STOP_BOUND, (r.rc, r.after))
        check("[CP4-T05] it says the code changed", says_changed(r.text), r.text[-400:])
        alive = wait_dead(pids)
        check("[CP4-T05] neither the fake tests nor the detached helper is alive afterwards", pids and not alive, alive)
    finally:
        reap(fk.work, env[TAG_VAR])


# ---- CP4-T07..T11: locks and counters after a stop ----------------------------------------------------------------------

def t07():
    d, env, fk = behavior_project(["exit:1", "slow", "sleep:1"], ["suite_watch_seconds=0.3"])
    try:
        r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        check("[CP4-T07] the first behavior run was stopped by the edit",
              r.ready and r.rc not in (None, 0) and says_changed(r.text), (r.rc, r.text[-300:]))
        check("[CP4-T07] right after the stop there is no state.lock and no state.lock.break", locks(d) == [], locks(d))
        reap(fk.work, env[TAG_VAR])
        t0 = time.monotonic()
        proc = start(d, env, "behavior", "CP1")
        started = fk.wait_started(3, timeout=30)
        t_start = time.monotonic() - t0
        r2 = finish(proc, 60, t0)
        check("[CP4-T07] the next behavior command is not refused as busy", r2.rc == 0 and "busy" not in r2.text.lower(),
              (r2.rc, r2.text[-300:]))
        check("[CP4-T07] it started its tests within a couple of seconds (no lock to wait out)",
              started and t_start < 6.0, (started, t_start))
    finally:
        reap(fk.work, env[TAG_VAR])


def t08():
    d, env, fk = behavior_project(["exit:1", "slow"], ["suite_watch_seconds=0.3"])
    try:
        before_gate = gate(d)
        before_step = cps(d).get("step")
        before_failed = len(history(d, "behavior_failed"))
        r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        check("[CP4-T08] the run was stopped by the edit", r.ready and r.rc not in (None, 0) and says_changed(r.text),
              (r.rc, r.text[-300:]))
        g = gate(d)
        check("[CP4-T08] the behavior gate is not passed and holds no pass fingerprint",
              g.get("status") != "passed" and not g.get("fingerprint"), g)
        check("[CP4-T08] attempts, flaky_used and `last` are exactly as before the call (only `runs` may differ)",
              no_runs(g) == no_runs(before_gate), (before_gate, g))
        check("[CP4-T08] the step has not moved forward", cps(d).get("step") == before_step, (before_step, cps(d).get("step")))
        check("[CP4-T08] the history has no behavior_failed entry", len(history(d, "behavior_failed")) == before_failed,
              history(d, "behavior_failed"))
    finally:
        reap(fk.work, env[TAG_VAR])


def t09():
    d, env, fk = behavior_project(["exit:1", "slow", "slow", "slow", "sleep:1"],
                                  ["suite_watch_seconds=0.3", "max_behavior_attempts=2"])
    try:
        for n in (2, 3, 4):
            r = run_then_edit(d, env, ["behavior", "CP1"], fk, n)
            reap(fk.work, env[TAG_VAR])
            st = state(d)
            check(f"[CP4-T09] stop {n - 1} of 3: the run was stopped, not failed",
                  r.ready and r.rc not in (None, 0) and says_changed(r.text), (r.rc, r.text[-300:]))
            check(f"[CP4-T09] stop {n - 1} of 3: the step is not blocked and attempts is still 0",
                  st.get("phase") != "blocked" and st.get("blocked_reason") is None and gate(d).get("attempts") == 0,
                  (st.get("phase"), st.get("blocked_reason"), gate(d)))
        r = rx(d, env, "behavior", "CP1", cap=60)
        g = gate(d)
        check("[CP4-T09] the following uninterrupted run passes the gate on its first counted attempt",
              r.rc == 0 and g.get("status") == "passed" and g.get("attempts") == 1, (r.rc, g, r.text[-300:]))
    finally:
        reap(fk.work, env[TAG_VAR])


def t10():
    d, env, fk = behavior_project(["exit:1", "slow", "exit:1", "exit:0"], ["suite_watch_seconds=0.3"])
    try:
        r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        reap(fk.work, env[TAG_VAR])
        check("[CP4-T10] the first run was stopped by the edit", r.ready and r.rc not in (None, 0) and says_changed(r.text),
              (r.rc, r.text[-300:]))
        check("[CP4-T10] flaky_used is still unset after the stop", not gate(d).get("flaky_used"), gate(d))
        r2 = rx(d, env, "behavior", "CP1", cap=60)
        g = gate(d)
        check("[CP4-T10] the next run (fails once, passes on the automatic re-run) passes marked flaky",
              r2.rc == 0 and g.get("status") == "passed" and g.get("flaky") is True, (r2.rc, g, r2.text[-300:]))
        check("[CP4-T10] attempts is still 0: neither the stop nor the flaky pass spent one", g.get("attempts") == 0, g)
    finally:
        reap(fk.work, env[TAG_VAR])


def t11():
    d, env, fk = behavior_project(["exit:1", "exit:1", "slow"], ["suite_watch_seconds=0.3"])
    try:
        before = gate(d)
        r = run_then_edit(d, env, ["behavior", "CP1"], fk, 3)
        check("[CP4-T11] the automatic re-run (the fake's third call) was the one stopped", r.ready and fk.calls() == 3,
              (r.ready, fk.calls()))
        check("[CP4-T11] the command ends as a stop, not as a failure",
              r.rc not in (None, 0) and says_changed(r.text) and "Behavior gate FAILED" not in r.text, (r.rc, r.text[-400:]))
        g = gate(d)
        check("[CP4-T11] flaky_used is unset and attempts is what it was before the call",
              not g.get("flaky_used") and g.get("attempts") == before.get("attempts"), (before, g))
        check("[CP4-T11] the gate is not failed and the history has no behavior_failed entry",
              g.get("status") != "failed" and not history(d, "behavior_failed"), (g, history(d, "behavior_failed")))
    finally:
        reap(fk.work, env[TAG_VAR])


# ---- CP4-T12..T15: complete -------------------------------------------------------------------------------------------------

_T12 = {}


def shared_complete_stop():
    if "r" not in _T12:
        d, env, fk_r, _ = complete_project(["slow"], ["suite_watch_seconds=0.3"])
        h = head(d)
        r = run_then_edit(d, env, ["complete", "CP1"], fk_r, 1)
        reap(fk_r.work, env[TAG_VAR])
        _T12.update(d=d, env=env, fk=fk_r, r=r, head=h)
    return _T12


def t12():
    s = shared_complete_stop()
    d, r = s["d"], s["r"]
    check("[CP4-T12] the full suite had started before the edit", r.ready, r.text[-400:])
    check("[CP4-T12] complete ended within a few seconds of the edit with a non-zero exit",
          r.rc not in (None, 0) and r.after is not None and r.after < STOP_BOUND, (r.rc, r.after))
    check("[CP4-T12] it says the code changed and the step must be run again", says_changed(r.text) and says_again(r.text),
          r.text[-600:])
    check("[CP4-T12] no checkpoint commit was made", head(d) == s["head"], (head(d), s["head"]))
    check("[CP4-T12] the step is not done", cps(d).get("status") != "done", cps(d).get("status"))
    st = state(d)
    check("[CP4-T12] regression_flaky_used and regression_flaky are unset",
          not st.get("regression_flaky_used") and not cps(d).get("regression_flaky"),
          (st.get("regression_flaky_used"), cps(d).get("regression_flaky")))
    check("[CP4-T12] no state.lock is left", locks(d) == [], locks(d))
    log = read(evidence(d) / "regression.log")
    check("[CP4-T12] the regression log says the run was stopped because the code changed", says_stopped_for_change(log),
          log[:600])


def t13():
    d, env, fk_r, _ = complete_project(["exit:1", "slow"], ["suite_watch_seconds=0.3"])
    try:
        h = head(d)
        r = run_then_edit(d, env, ["complete", "CP1"], fk_r, 2)
        check("[CP4-T13] the automatic re-run (the fake's second call) was the one stopped", r.ready and fk_r.calls() == 2,
              (r.ready, fk_r.calls()))
        check("[CP4-T13] the stop message is shown, not a regression failure",
              r.rc not in (None, 0) and says_changed(r.text) and "Regression suite failed" not in r.text, (r.rc, r.text[-400:]))
        st = state(d)
        check("[CP4-T13] regression_flaky_used is unset", not st.get("regression_flaky_used"), st.get("regression_flaky_used"))
        check("[CP4-T13] nothing is marked as a failed regression and nothing was committed",
              not cps(d).get("regression_flaky") and cps(d).get("status") != "done" and head(d) == h,
              (cps(d).get("regression_flaky"), cps(d).get("status")))
        left = wait_untagged(env[TAG_VAR])
        dead = wait_dead(fk_r.pids(2))
        check("[CP4-T13] no process of the stopped re-run is alive", not left and not dead, (left, dead))
    finally:
        reap(fk_r.work, env[TAG_VAR])


def t14():
    s = shared_complete_stop()
    d, env, fk_r = s["d"], s["env"], s["fk"]
    check("[CP4-T14] (setup) the earlier complete run was stopped by the edit", s["r"].rc not in (None, 0) and s["r"].ready,
          s["r"].rc)
    tt.git(d, "checkout", "--", "src/a.js")
    fk_r.set_modes(["sleep:1"])
    r = rx(d, env, "complete", "CP1", cap=90)
    check("[CP4-T14] after the edit is undone, complete succeeds", r.rc == 0, (r.rc, r.text[-400:]))
    check("[CP4-T14] the step is done and the checkpoint commit was made",
          cps(d).get("status") == "done" and head(d) != s["head"], (cps(d).get("status"), head(d), s["head"]))
    st = state(d)
    check("[CP4-T14] the regression run counted as a normal first run (flaky allowance still available)",
          not st.get("regression_flaky_used") and not cps(d).get("regression_flaky") and fk_r.calls() == 1,
          (st.get("regression_flaky_used"), cps(d).get("regression_flaky"), fk_r.calls()))


def t15():
    d, env, fk_r, fk_rel = complete_project(["marker"], ["suite_watch_seconds=0.3"], related_modes=["slow"])
    try:
        before = {n: dict(gate(d, n)) for n in ("behavior", "adversarial")}
        r = run_then_edit(d, env, ["complete", "CP1"], fk_rel, 1)
        check("[CP4-T15] the related pass had started (CP3's related_test_command is in force)",
              r.ready and fk_rel.started(1) and "related_test_command is not configured" not in r.text, (r.ready, r.text[-400:]))
        check("[CP4-T15] complete ends with the code-changed message", r.rc not in (None, 0) and says_changed(r.text),
              (r.rc, r.text[-400:]))
        failed_line = [l for l in r.text.splitlines() if re.search(r"fail", l, re.I) and "tests/cp1.test.js" in l]
        check("[CP4-T15] not with a 'related tests failed' message naming a test file", not failed_line, failed_line)
        check("[CP4-T15] the full suite was never started (no regression marker)",
              not fk_r.marker(1) and fk_r.calls() == 0, (fk_r.marker(1), fk_r.calls()))
        after = {n: dict(gate(d, n)) for n in ("behavior", "adversarial")}
        check("[CP4-T15] the run's counters are unchanged",
              after == before and not state(d).get("regression_flaky_used") and cps(d).get("status") != "done",
              (before, after, state(d).get("regression_flaky_used")))
    finally:
        reap(fk_rel.work, env[TAG_VAR])


# ---- CP4-T16: automatic checks ------------------------------------------------------------------------------------------

def t16():
    env = make_env()
    fk_t = Fake(["exit:1", "exit:0"], "t")
    fk_c = Fake(["slow"], "c")
    checks = json.dumps([{"kind": "lint", "command": fk_c.cmd}])
    d = project(env, fk_t.cmd, ["suite_watch_seconds=0.3", f"checks={checks}"])
    try:
        to_implemented(d, env)
        before = gate(d)
        r = run_then_edit(d, env, ["behavior", "CP1"], fk_c, 1)
        check("[CP4-T16] the automatic check had started after the step's tests passed", r.ready and fk_t.calls() == 2,
              (r.ready, fk_t.calls(), r.text[-400:]))
        check("[CP4-T16] the command ends with the code-changed message", r.rc not in (None, 0) and says_changed(r.text),
              (r.rc, r.text[-400:]))
        check("[CP4-T16] not with 'automatic check failed'",
              not (re.search(r"automatic check", r.text, re.I) and re.search(r"fail", r.text, re.I)), r.text[-400:])
        g = gate(d)
        check("[CP4-T16] attempts, gate status and `last` are as before the call", no_runs(g) == no_runs(before), (before, g))
        left = wait_untagged(env[TAG_VAR])
        dead = wait_dead(fk_c.pids(1))
        check("[CP4-T16] no process of the check is alive", not left and not dead, (left, dead))
    finally:
        reap(fk_c.work, env[TAG_VAR])


# ---- CP4-T17, T18: a quick run that ends before the next look -----------------------------------------------------------

def t17():
    d, env, fk = behavior_project(["exit:1", "sleep:2.5"], ["suite_watch_seconds=30"])
    before = gate(d)
    r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2, wait="started", delay=0.3, cap_after=60)
    check("[CP4-T17] the edit landed while the quick tests were running", r.ready, r.text[-300:])
    g = gate(d)
    check("[CP4-T17] the behavior gate does not become passed", r.rc != 0 and g.get("status") != "passed", (r.rc, g))
    check("[CP4-T17] no pass fingerprint covering the edited code is saved", not g.get("fingerprint"), g)
    check("[CP4-T17] attempts is unchanged", g.get("attempts") == before.get("attempts"), (before, g))
    check("[CP4-T17] the output says the code changed during the run and to run the step again",
          says_changed(r.text) and "behavior CP1" in r.text and says_again(r.text), r.text[-500:])


def t18():
    d, env, fk_r, _ = complete_project(["sleep:2.5"], ["suite_watch_seconds=30"])
    h = head(d)
    r = run_then_edit(d, env, ["complete", "CP1"], fk_r, 1, wait="started", delay=0.3, cap_after=60)
    check("[CP4-T18] the edit landed while the quick suite was running", r.ready, r.text[-300:])
    check("[CP4-T18] complete does not commit and does not finish the step",
          r.rc != 0 and head(d) == h and cps(d).get("status") != "done", (r.rc, head(d) == h, cps(d).get("status")))
    check("[CP4-T18] it says the code changed during the run", says_changed(r.text), r.text[-500:])
    check("[CP4-T18] not that the suite itself wrote files", "changed files in the project" not in r.text, r.text[-500:])
    check("[CP4-T18] the flaky allowance is unset", not state(d).get("regression_flaky_used") and not cps(d).get("regression_flaky"),
          (state(d).get("regression_flaky_used"), cps(d).get("regression_flaky")))


# ---- CP4-T19..T22: runs that must not be stopped -------------------------------------------------------------------------

def _normal(text, d):
    return re.sub(r"\d+(\.\d+)?s\b", "<t>", text.replace(str(d), "<d>"))


def t19():
    d, env, fk = behavior_project(["exit:1", "sleep:2"], ["suite_watch_seconds=0.3"])
    r = rx(d, env, "behavior", "CP1", cap=60)
    g = gate(d)
    check("[CP4-T19] the gate passes with attempts 1", r.rc == 0 and g.get("status") == "passed" and g.get("attempts") == 1,
          (r.rc, g, r.text[-300:]))
    logs = behavior_logs(d)
    text = read(logs[-1]) if logs else ""
    check("[CP4-T19] the log shows the tests' output with exit 0 and no mention of a stop",
          "exit=0" in text and "stop" not in text.split("\n", 1)[-1].lower(), text[:400])
    check("[CP4-T19] flaky is not set", not g.get("flaky") and not g.get("flaky_used"), g)
    d2, env2, _ = behavior_project(["exit:1", "sleep:2"])
    r2 = rx(d2, env2, "behavior", "CP1", cap=60)
    check("[CP4-T19] the exit code and message are the same as with suite_watch_seconds at its default",
          r.rc == r2.rc == 0 and _normal(r.out, d) == _normal(r2.out, d2), (r.rc, r2.rc, r.out[-300:], r2.out[-300:]))


def t20():
    d, env, fk_r, _ = complete_project(["sleep:2"], ["suite_watch_seconds=0.3"])
    h = head(d)
    r = rx(d, env, "complete", "CP1", cap=90)
    check("[CP4-T20] the step completes", r.rc == 0 and cps(d).get("status") == "done", (r.rc, cps(d).get("status"), r.text[-300:]))
    subject = tt.git(d, "log", "-1", "--format=%s").stdout.strip()
    count = tt.git(d, "rev-list", "--count", f"{h}..HEAD").stdout.strip()
    check("[CP4-T20] one checkpoint commit was made with the usual message",
          count == "1" and subject == "ratchet(f): CP1 Step 1", (count, subject))
    log = read(evidence(d) / "regression.log")
    check("[CP4-T20] regression.log has no mention of a stop", "exit=0" in log and "stop" not in log.split("\n", 1)[-1].lower(), log[:400])


def t21():
    files = {".gitignore": "build/\n"}
    env = make_env()
    fk_t = Fake(["exit:1", "slow-write"], "t")
    fk_r = Fake(["slow-write"], "r")
    d = project(env, fk_t.cmd, ["suite_watch_seconds=0.3", f"regression_command={fk_r.cmd}"], files=files,
                transcript_inside=False)
    to_implemented(d, env)
    r = rx(d, env, "behavior", "CP1", cap=60)
    g = gate(d)
    check("[CP4-T21] the behavior run is not stopped by the files the tests write; it passes",
          r.rc == 0 and g.get("status") == "passed" and g.get("attempts") == 1 and not says_changed(r.text),
          (r.rc, g, r.text[-300:]))
    check("[CP4-T21] the tests did write into build/, __pycache__/, the report file and the scratch folder",
          (d / "build/out.txt").exists() and (d / "__pycache__/mod.cpython.pyc").exists()
          and (evidence(d) / "test-report.json").exists() and (fk_t.work / "scratch.txt").exists(),
          sorted(str(p) for p in d.rglob("*") if p.is_file() and ".git/" not in str(p))[:30])
    rnd = ts.open_round(d, env, "CP1")
    _, r0 = ts.record_host_review(d, env, rnd, findings=[], cp="CP1", files_reviewed=["src/f1.js"])
    check("[CP4-T21] (setup) the adversarial round is recorded", r0.returncode == 0, r0.stderr + r0.stdout)
    h = head(d)
    r2 = rx(d, env, "complete", "CP1", cap=90)
    check("[CP4-T21] the complete run is not stopped either; it completes and commits",
          r2.rc == 0 and cps(d).get("status") == "done" and head(d) != h and not says_changed(r2.text),
          (r2.rc, cps(d).get("status"), r2.text[-300:]))
    # a twin project with the same tree whose fake only sleeps: the saved gate fingerprint must be the same
    env2 = make_env()
    fk2 = Fake(["exit:1", "sleep:3"], "t")
    d2 = project(env2, fk2.cmd, ["suite_watch_seconds=0.3"], files=files, transcript_inside=False)
    to_implemented(d2, env2)
    r3 = rx(d2, env2, "behavior", "CP1", cap=60)
    fp1, fp2 = (g.get("fingerprint") or {}).get("id"), (gate(d2).get("fingerprint") or {}).get("id")
    check("[CP4-T21] the gate's saved fingerprint equals the one a run with no such writes gets",
          r3.rc == 0 and fp1 and fp1 == fp2, (fp1, fp2, r3.text[-200:]))


def t22():
    d, env, fk = behavior_project(["exit:1", "sleep:3"], ["suite_watch_seconds=0.3"])
    check("[CP4-T22] (setup) the chat transcript lives inside the project and the hook recorded it",
          (d / "t.jsonl").exists() and any(e.get("transcript_path", "").endswith("t.jsonl")
                                           for e in (json.loads(l) for l in read(d / ".ratchet/human_log.jsonl").splitlines() if l.strip())),
          read(d / ".ratchet/human_log.jsonl")[:300])

    def chat(dd):
        say(dd, env, "how is it going?")

    r = run_then_edit(d, env, ["behavior", "CP1"], fk, 2, wait="started", edit=chat, delay=0.3, cap_after=60)
    g = gate(d)
    check("[CP4-T22] a new chat line in the project's transcript does not stop the run",
          r.ready and r.rc == 0 and g.get("status") == "passed" and not says_changed(r.text), (r.rc, g, r.text[-400:]))


# ---- CP4-T23: a real hang still times out --------------------------------------------------------------------------------

def t23():
    d, env, fk = behavior_project(["exit:1", "slow"], ["suite_watch_seconds=0.3", "test_timeout_sec=2"])
    try:
        before = gate(d)
        r = rx(d, env, "behavior", "CP1", cap=60)
        logs = behavior_logs(d)
        text = read(logs[-1]) if logs else ""
        check("[CP4-T23] the run is stopped by the time limit and says it timed out",
              r.rc not in (None, 0) and "TIMEOUT" in r.text and "TIMEOUT" in text, (r.rc, r.text[-400:]))
        check("[CP4-T23] it does not say the code changed", not says_changed(r.text) and not says_changed(text), r.text[-400:])
        g = gate(d)
        check("[CP4-T23] it counts as one failed attempt exactly as before this step",
              g.get("status") == "failed" and g.get("attempts") == before.get("attempts", 0) + 1
              and len(history(d, "behavior_failed")) == 1, (before, g, history(d, "behavior_failed")))
        left = wait_untagged(env[TAG_VAR])
        check("[CP4-T23] nothing of the timed-out run is left running", not left, left)
    finally:
        reap(fk.work, env[TAG_VAR])


# ---- CP4-T24: the config key -------------------------------------------------------------------------------------------------

def _cfg_value(d, env, key):
    r = tt.rx(d, env, "config")
    try:
        return json.loads(r.stdout).get(key, "<absent>")
    except json.JSONDecodeError:
        return f"<unreadable: {r.stdout[:100]}>"


def t24():
    env = make_env()
    d = Path(_tmp.mkdtemp(prefix="ratchet-q4-c-"))
    tt.git(d, "init", "-q")
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    tt.git(d, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    tt.git(d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    say(d, env, "please build the feature")
    r = tt.rx(d, env, "init", "feature", "--slug", "f")
    check("[CP4-T24] (setup) a fresh project", r.returncode == 0, r.stderr + r.stdout)
    check("[CP4-T24] the default shown is 30", _cfg_value(d, env, "suite_watch_seconds") == 30, _cfg_value(d, env, "suite_watch_seconds"))
    r = tt.rx(d, env, "config", "--set", "suite_watch_seconds=0.3")
    check("[CP4-T24] 0.3 is accepted and used", r.returncode == 0 and _cfg_value(d, env, "suite_watch_seconds") == 0.3,
          (r.returncode, r.stdout, r.stderr, _cfg_value(d, env, "suite_watch_seconds")))
    for bad in ("0", "-5", "abc"):
        r = tt.rx(d, env, "config", "--set", f"suite_watch_seconds={bad}")
        check(f"[CP4-T24] {bad} is refused with a plain message and the stored value stays 0.3",
              r.returncode != 0 and "suite_watch_seconds" in (r.stdout + r.stderr)
              and _cfg_value(d, env, "suite_watch_seconds") == 0.3,
              (r.returncode, r.stdout[-200:], r.stderr[-200:], _cfg_value(d, env, "suite_watch_seconds")))


# ---- CP4-T25: the docs ------------------------------------------------------------------------------------------------------

def _paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if "suite_watch_seconds" in p]


def t25():
    for label, path in (("README", README), ("SKILL.md", SKILL), ("schemas.md", SCHEMAS)):
        text = read(path)
        paras = _paragraphs(text)
        joined = "\n\n".join(paras)
        check(f"[CP4-T25] {label} names suite_watch_seconds", bool(paras), str(path))
        check(f"[CP4-T25] {label} says a suite is stopped when the code changes",
              re.search(r"stop", joined, re.I) and re.search(r"code changes|code changed|changes while|changed while", joined, re.I),
              joined[:300])
        check(f"[CP4-T25] {label} says a stopped run is not a pass and not a failed attempt",
              re.search(r"not (a |count(ed)? as a )?pass|never (a |count(s|ed)? as a )?pass", joined, re.I)
              and re.search(r"attempt", joined, re.I), joined[:400])
        check(f"[CP4-T25] {label} says a stopped run does not use the flaky retry", re.search(r"flaky", joined, re.I), joined[:400])
        check(f"[CP4-T25] {label} says the default check interval is 30 seconds",
              re.search(r"\b30\b", joined) and re.search(r"second", joined, re.I), joined[:400])
    schemas = read(SCHEMAS)
    m = re.search(r'"suite_watch_seconds":\s*30\b', schemas)
    check("[CP4-T25] the schemas config table lists suite_watch_seconds with its default 30", m is not None,
          [l for l in schemas.splitlines() if "suite_watch_seconds" in l][:5])


TESTS = [(f"CP4-T{i:02d}", fn) for i, fn in enumerate(
    [t01, t02, t03, t04, t05, t06, t07, t08, t09, t10, t11, t12, t13, t14, t15, t16, t17, t18, t19, t20, t21, t22,
     t23, t24, t25], 1)]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid, _ in TESTS:
        if cid in CASE_ERRORS:
            cases.append({"id": cid, "status": "error", "message": CASE_ERRORS[cid][:300]})
        elif CASE_STATUS.get(cid, False):
            cases.append({"id": cid, "status": "passed", "message": ""})
        else:
            cases.append({"id": cid, "status": "failed", "message": "; ".join(CASE_MESSAGES.get(cid, []))[:300]})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    only = set(sys.argv[1:])
    for cid, fn in TESTS:
        if only and cid not in only and cid[-2:].lstrip("0") not in only:
            continue
        CASE_STATUS.setdefault(cid, True)
        print(cid, flush=True)
        t0 = time.monotonic()
        try:
            fn()
        except Exception as e:  # a crash is reported as an error for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_ERRORS[cid] = f"crashed: {e}"
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        print(f"  ({time.monotonic() - t0:.1f}s)", flush=True)
    _reap_all()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
