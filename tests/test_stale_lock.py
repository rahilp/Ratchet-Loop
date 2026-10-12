#!/usr/bin/env python3
"""
CP2 tests: a state.lock left behind by a Ratchet command that is provably gone clears itself at once, while a lock
held by a live command is still respected and a lock whose owner can't be checked keeps the timed rule.
python3 tests/test_stale_lock.py

Driven the way an agent meets it. A throwaway git project (tests/test_gates.py project()) has a plan whose step test
command is a small slow script (tests/slow_test.py inside the project). A real `ratchet.py red CP1` is started in its
own process group; it holds .ratchet/state.lock for its whole run, test run included. Killing that group with SIGKILL
leaves a REAL lock file behind whose owner is provably dead; leaving it running gives a live owner. The next real
ratchet.py command is then run against the same project. Lock text is never invented where a real one can be taken:
the dead owner's real state.lock is read, and where a case needs another machine or another process id only that
machine name or that process id inside the real text is swapped. Only the legacy case writes a lock by hand, in
today's '<pid> <token> <command>' format (like tests/test_trust_cp5_c.py). The short waits that would cost 30 s
through the CLI are proven in process by loading ratchet.py with importlib and calling _StateLock(root, wait=0.3).

Assumption (CP2-T05 relies on it): the new lock format stores this machine's name verbatim, exactly as
socket.gethostname() returns it. If the name can't be found in the real lock text, that case fails and says so.
Another assumption: the lock text keeps the owner's process id as a standalone number (CP2-T06 swaps it).

Process-liveness cases need POSIX signals (and /proc for the reused-id case, which runs on Linux only). On Windows the
timed rule stays, so those cases print a 'not run' line and are reported passed with a skip message.

Everything a case starts (ratchet commands, the slow test, a 'sleep 120') is killed in a finally block and at exit;
every temp folder comes from tests/_tmp.py so nothing is left behind.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import re
import signal
import socket
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

ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+[a-z]?)\]")
TESTS: list = []
POSIX = os.name == "posix"
LINUX = sys.platform.startswith("linux") and os.path.isdir("/proc")
THIS_HOST = socket.gethostname()
OTHER_HOST = "build-box-2.invalid"


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name} {str(detail)[:160]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def case(cid):
    def deco(fn):
        TESTS.append((cid, fn))
        return fn
    return deco


# ------------------------------------------------------------------------------------------------ process control

PROCS: list = []  # every Popen this suite started, each in its own session/process group


def _kill_group(proc):
    try:
        if POSIX:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def kill_everything():
    for p in PROCS:
        if p.poll() is None:
            _kill_group(p)
    for pid in list(SLOW_PIDS):  # slow tests a killed ratchet may have left behind (they also give up after 120 s)
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError, AttributeError):
            pass


SLOW_PIDS: set = set()
atexit.register(kill_everything)


def spawn(args, cwd, env, **kw):
    p = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=POSIX, **kw)
    PROCS.append(p)
    return p


def alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:  # a zombie is gone for every purpose here
        if LINUX:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False
    return True


def wait_for(pred, timeout=30.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


# ------------------------------------------------------------------------------------------------ project helpers

def _g():
    import test_gates as g  # lazily: a harness problem shows up as a crash of the one case that needs it
    return g


def rx(d, *args):
    g = _g()
    return g.rx(d, *args, env=g.ENV)


def out_of(r):
    return r.stdout + r.stderr


def lock_path(d):
    return Path(d) / ".ratchet" / "state.lock"


def breaker_path(d):
    return Path(d) / ".ratchet" / "state.lock.break"


def state_path(d):
    return Path(d) / ".ratchet" / "runs" / "f" / "state.json"


def lock_leftovers(d):
    return sorted(p.name for p in (Path(d) / ".ratchet").iterdir() if p.name.startswith("state.lock"))


SLOW_TEST = '''\
# [CP1-T01] slow test for the stale-lock suite: writes its pid, waits for a go file, then fails (red wants a failure)
import os, sys, time
open({marker!r}, "w").write(str(os.getpid()))
t0 = time.time()
while not os.path.exists({go!r}) and time.time() - t0 < 120:
    time.sleep(0.05)
sys.exit(1)
'''


class Lane:
    """A project whose approved step runs tests/slow_test.py, plus the control folder holding its marker and go files."""

    def __init__(self):
        g = _g()
        self.ctl = _tmp.mkdtemp(prefix="ratchet-stale-ctl-")
        self.marker = Path(self.ctl) / "marker"
        self.go = Path(self.ctl) / "go"
        self.d = g.project("stalelane", extra_cp={"tests": {"files": ["tests/slow_test.py"],
                                                            "command": "python3 tests/slow_test.py"}})
        g.approve_plan(self.d)
        (self.d / "tests" / "slow_test.py").write_text(SLOW_TEST.format(marker=str(self.marker), go=str(self.go)))
        r = rx(self.d, "tests-written", "CP1")
        assert r.returncode == 0, out_of(r)

    def start_red(self):
        """Start `ratchet.py red CP1` in its own process group and wait until its test is running (so the command
        is known to hold state.lock)."""
        g = _g()
        if self.marker.exists():
            self.marker.unlink()
        p = spawn([sys.executable, str(ENGINE), "red", "CP1"], self.d, g.ENV)
        up = wait_for(lambda: self.marker.exists() or p.poll() is not None, 60)
        assert up and p.poll() is None and self.marker.exists(), \
            f"red did not reach its slow test: rc={p.poll()} {p.stdout.read() if p.poll() is not None else ''}"
        try:
            SLOW_PIDS.add(int(self.marker.read_text()))
        except (OSError, ValueError):
            pass
        assert wait_for(lambda: lock_path(self.d).exists(), 10), "red is running but holds no state.lock"
        return p

    def release_slow_test(self):
        self.go.write_text("go")


def plan_only_project(name="stalep"):
    """A cheap project whose plan awaits approval: `ask approve-plan` succeeds there and changes state."""
    return _g().project(name)


DEAD: dict = {}  # the real leftover of one killed red: {"pid", "text"}; built by CP2-T01's setup, reused by later cases


def kill_a_red():
    """Run red, SIGKILL its whole process group and return (lane, red_pid, real_lock_text)."""
    lane = Lane()
    p = lane.start_red()
    text = lock_path(lane.d).read_text()
    _kill_group(p)
    return lane, p.pid, text


def dead_owner():
    if not DEAD:
        lane, pid, text = kill_a_red()
        DEAD.update(pid=pid, text=text, lane=lane)
    return DEAD


def swap_word(text, old, new):
    """Replace `old` where it stands alone (not inside a longer word or number) in `text`."""
    return re.sub(r"(?<![0-9A-Za-z])" + re.escape(str(old)) + r"(?![0-9A-Za-z])", str(new), text)


_MOD = None


def engine():
    """ratchet.py loaded in this process (for _StateLock only), the way tests/test_trust_cp5_c.py loads it."""
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("ratchet_cp2_stale", str(ENGINE))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MOD = m
    return _MOD


def bare_root(text=None, age=None):
    """A project root holding only a .ratchet folder, with state.lock = text (when given) written just now or `age`
    seconds ago."""
    root = Path(_tmp.mkdtemp(prefix="ratchet-stale-root-"))
    (root / ".ratchet").mkdir()
    lock = root / ".ratchet" / "state.lock"
    if text is not None:
        lock.write_text(text)
        if age:
            t = time.time() - age
            os.utime(lock, (t, t))
    return root


def acquire(root, wait):
    """(reason-or-None, seconds waited, the lock object)."""
    lk = engine()._StateLock(root, wait=wait)
    t0 = time.monotonic()
    why = lk.acquire()
    return why, time.monotonic() - t0, lk


def windows_skip(cid):
    print(f"  - [{cid}] not run on this platform (process liveness is checked on POSIX; Windows keeps the timed rule)")
    check(f"[{cid}] skipped: needs POSIX signals", True)


# ------------------------------------------------------------------------------------------------ the cases

@case("CP2-T01")
def t01():
    """A command that was killed leaves no hour-long wait behind."""
    if not sys.platform.startswith("linux"):
        return windows_skip("CP2-T01")
    lane, pid, text = kill_a_red()
    DEAD.update(pid=pid, text=text, lane=lane)
    check("[CP2-T01] setup: the killed red left its state.lock on disk, written moments ago",
          lock_path(lane.d).exists() and time.time() - lock_path(lane.d).stat().st_mtime < 20, text)
    check("[CP2-T01] setup: the killed command's process is really gone", not alive(pid), pid)
    lane.release_slow_test()
    t0 = time.monotonic()
    r = rx(lane.d, "red", "CP1")
    took = time.monotonic() - t0
    out = out_of(r)
    check("[CP2-T01] the second red exits 0 (it was not refused by the dead owner's lock)", r.returncode == 0, out[-400:])
    check("[CP2-T01] ... in well under the 30 s lock wait", took < 15, f"{took:.1f}s")
    check("[CP2-T01] ... without saying Ratchet is busy", not re.search(r"busy|still changing", out, re.I), out[-400:])
    check("[CP2-T01] ... and without telling the agent to wait minutes for the lock",
          not re.search(r"\b\d+ minutes\b", out), out[-400:])
    check("[CP2-T01] the step's red is recorded (Ratchet says so, and state.json holds the red snapshot)",
          "red run recorded" in out and '"red_tests"' in state_path(lane.d).read_text(), out[-300:])
    check("[CP2-T01] no state.lock or state.lock.break is left behind", lock_leftovers(lane.d) == [],
          lock_leftovers(lane.d))


@case("CP2-T02")
def t02():
    """A command that is still running keeps its lock."""
    if not POSIX:
        return windows_skip("CP2-T02")
    lane = Lane()
    p = lane.start_red()
    lock_before = lock_path(lane.d).read_text()
    state_before = state_path(lane.d).read_bytes()
    for label, args in (("a second red", ("red", "CP1")), ("ask approve-plan", ("ask", "approve-plan"))):
        t0 = time.monotonic()
        r = rx(lane.d, *args)
        took = time.monotonic() - t0
        out = out_of(r)
        check(f"[CP2-T02] {label} is refused with Ratchet busy running red", r.returncode != 0
              and "busy running red" in out and "try again when it finishes" in out, out[-300:])
        check(f"[CP2-T02] {label} is refused within a few seconds", took < 10, f"{took:.1f}s")
    check("[CP2-T02] the lock file text is unchanged", lock_path(lane.d).exists()
          and lock_path(lane.d).read_text() == lock_before, lock_before)
    check("[CP2-T02] state.json is byte-identical", state_path(lane.d).read_bytes() == state_before)
    check("[CP2-T02] the first command is still running", p.poll() is None)
    lane.release_slow_test()
    try:
        p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        _kill_group(p)
    check("[CP2-T02] once the go file is written the first command finishes (exit 0)", p.returncode == 0, p.returncode)
    check("[CP2-T02] ... and state.lock is gone", lock_leftovers(lane.d) == [], lock_leftovers(lane.d))


@case("CP2-T03")
def t03():
    """A short command waits for a live lock and takes it the moment it is released."""
    live = f"{os.getpid()} tokenfortest held-by-test"  # today's format, naming this (live) test process
    root = bare_root(live)
    lock = root / ".ratchet" / "state.lock"
    engine(); threading.Timer(0.4, lambda: lock.unlink() if lock.exists() else None).start()
    why, took, lk = acquire(root, 3)
    check("[CP2-T03] the first call waits for the live lock (at least 0.3 s)", took >= 0.3, f"{took:.2f}s")
    check("[CP2-T03] ... then takes it (returns None)", why is None, why)
    check("[CP2-T03] ... and its own lock file replaced the removed one",
          lock.exists() and lock.read_text() != live and str(os.getpid()) in lock.read_text(), lock.exists())
    lk.release()
    root2 = bare_root(live)
    lock2 = root2 / ".ratchet" / "state.lock"
    why2, took2, _lk2 = acquire(root2, 0.3)
    check("[CP2-T03] the second call returns the 'still changing this run' busy text", "still changing this run" in str(why2),
          why2)
    check("[CP2-T03] ... after waiting about its full 0.3 s", 0.25 <= took2 < 2.5, f"{took2:.2f}s")
    check("[CP2-T03] ... and the held lock's text is unchanged", lock2.exists() and lock2.read_text() == live)


@case("CP2-T04")
def t04():
    """An old-style lock whose owner is gone is still cleared only by age."""
    if not POSIX:
        return windows_skip("CP2-T04")
    dead = dead_owner()
    old_style = f"{dead['pid']} held-by-test"
    m = engine()
    root = bare_root(old_style)
    lock = root / ".ratchet" / "state.lock"
    why, _t, _lk = acquire(root, 0.3)
    check("[CP2-T04] the fresh old-style lock is respected (busy text returned)", "still changing this run" in str(why), why)
    check("[CP2-T04] ... and its file is untouched", lock.exists() and lock.read_text() == old_style)
    limit = m._lock_stale_seconds(root)
    root2 = bare_root(old_style, age=limit + 60)
    lock2 = root2 / ".ratchet" / "state.lock"
    why2, _t2, lk2 = acquire(root2, 0.3)
    check("[CP2-T04] the copy older than the timed limit is cleared by the timed rule and the lock acquired",
          why2 is None and lock2.exists() and lock2.read_text() != old_style, why2)
    lk2.release()


@case("CP2-T05")
def t05():
    """A lock written on another machine is not judged by this machine's processes."""
    if not POSIX:
        return windows_skip("CP2-T05")
    dead = dead_owner()
    real = dead["text"]
    found = re.search(r"(?<![0-9A-Za-z.-])" + re.escape(THIS_HOST) + r"(?![0-9A-Za-z.-])", real) is not None
    check("[CP2-T05] this machine's name is stored verbatim in the real lock text", found,
          f"the lock text {real!r} does not contain this machine's name {THIS_HOST!r}")
    foreign = re.sub(r"(?<![0-9A-Za-z.-])" + re.escape(THIS_HOST) + r"(?![0-9A-Za-z.-])", OTHER_HOST, real)
    if not found:
        foreign = real + f" {OTHER_HOST}"  # keep going so the respect/timed-rule checks below still run
    m = engine()
    root = bare_root(foreign)
    lock = root / ".ratchet" / "state.lock"
    why, _t, _lk = acquire(root, 0.3)
    check("[CP2-T05] the fresh foreign-machine lock is respected (busy text returned) though its pid is not running here",
          "still changing this run" in str(why) and not alive(dead["pid"]), why)
    check("[CP2-T05] ... and its file is untouched", lock.exists() and lock.read_text() == foreign)
    root2 = bare_root(foreign, age=m._lock_stale_seconds(root) + 60)
    why2, _t2, lk2 = acquire(root2, 0.3)
    check("[CP2-T05] the foreign-machine lock older than the timed limit is cleared by the timed rule", why2 is None, why2)
    lk2.release()


@case("CP2-T06")
def t06():
    """A reused process id that is not a Ratchet command does not hold the lock."""
    if not LINUX:
        return windows_skip("CP2-T06")
    dead = dead_owner()
    real = dead["text"]
    check("[CP2-T06] setup: the real lock text holds the owner's pid as a standalone number",
          swap_word(real, dead["pid"], "0") != real, real)
    sleeper = spawn(["sleep", "120"], None, os.environ.copy())
    d = plan_only_project("stale6")
    lock = lock_path(d)
    lock.write_text(swap_word(real, dead["pid"], sleeper.pid))
    t0 = time.monotonic()
    r = rx(d, "ask", "approve-plan")
    took = time.monotonic() - t0
    out = out_of(r)
    check("[CP2-T06] the lock held by a reused id (a live sleep) is cleared and the command runs (exit 0)",
          r.returncode == 0 and not re.search(r"busy|still changing", out, re.I), out[-300:])
    check("[CP2-T06] ... at once, with no wait", took < 10, f"{took:.1f}s")
    check("[CP2-T06] ... leaving no lock file behind", lock_leftovers(d) == [], lock_leftovers(d))
    check("[CP2-T06] the sleep process is untouched and still running", sleeper.poll() is None and alive(sleeper.pid))
    # companion: the id of a live ratchet.py command is respected
    lane = Lane()
    red = lane.start_red()
    live_text = swap_word(real, dead["pid"], red.pid)
    d2 = plan_only_project("stale6b")
    lock2 = lock_path(d2)
    lock2.write_text(live_text)
    r2 = rx(d2, "ask", "approve-plan")
    out2 = out_of(r2)
    check("[CP2-T06] companion: a lock naming a live ratchet.py command is respected as busy",
          r2.returncode != 0 and "busy running" in out2, out2[-300:])
    check("[CP2-T06] ... and its file is untouched", lock2.exists() and lock2.read_text() == live_text)
    lane.release_slow_test()
    red.communicate(timeout=60)


@case("CP2-T07")
def t07():
    """Two commands started at once behind a dead owner's lock both run, one after the other."""
    if not sys.platform.startswith("linux"):
        return windows_skip("CP2-T07")
    dead = dead_owner()
    d = plan_only_project("stale7")
    g = _g()
    ok_all = True
    for rnd in range(1, 6):
        lock_path(d).write_text(dead["text"])
        procs = [spawn([sys.executable, str(ENGINE), "ask", "approve-plan"], d, g.ENV) for _ in range(2)]
        outs = []
        for p in procs:
            try:
                o, e = p.communicate(timeout=90)
            except subprocess.TimeoutExpired:
                _kill_group(p)
                o, e = p.communicate()
            outs.append((p.returncode, o + e))
        try:
            json.loads(state_path(d).read_text())
            valid = True
        except (OSError, ValueError):
            valid = False
        no_busy = all(not re.search(r"busy|still changing|lock file", o, re.I) for _rc, o in outs)
        ran = all(rc == 0 for rc, _o in outs)
        clean = lock_leftovers(d) == []
        good = no_busy and ran and valid and clean
        check(f"[CP2-T07] round {rnd}: both commands ran, no busy or lock-file error, state.json valid, no lock files left",
              good, (outs, valid, lock_leftovers(d)))
        if not good:
            ok_all = False
            break  # a failing round already says what is wrong; do not spend 30 s waits on the rest
    check("[CP2-T07] all 5 rounds behind a fresh dead lock passed", ok_all)


@case("CP2-T08")
def t08():
    """When a dead owner's lock cannot be cleared the message does not say to wait 65 minutes."""
    if not sys.platform.startswith("linux"):
        return windows_skip("CP2-T08")
    dead = dead_owner()
    root = bare_root(dead["text"])
    lock = root / ".ratchet" / "state.lock"
    brk = root / ".ratchet" / "state.lock.break"
    brk.write_text("held-by-someone-else")
    why, _t, _lk = acquire(root, 0.3)
    check("[CP2-T08] the leftover lock cannot be cleared right now: a refusal text is returned", isinstance(why, str) and bool(why),
          why)
    check("[CP2-T08] ... it changes nothing (both files still on disk, lock text unchanged)",
          lock.exists() and brk.exists() and lock.read_text() == dead["text"] and brk.read_text() == "held-by-someone-else")
    check("[CP2-T08] ... and does not say 65 minutes or tell the agent to wait for the lock to clear itself after minutes",
          not re.search(r"\b\d+ minutes\b|clears itself", str(why)), why)
    # the live holder's busy text (T02) still names the command and says to try again
    live_root = bare_root(f"{os.getpid()} tok red")
    why2, _t2, _lk2 = acquire(live_root, 0.3)
    check("[CP2-T08] a live holder's busy text still names the command and says to try again when it finishes",
          "busy running red" in str(why2) and "try again when it finishes" in str(why2), why2)


@case("CP2-T09")
def t09():
    """A normal command removes its own lock and leaves no breaker file."""
    if not sys.platform.startswith("linux"):
        return windows_skip("CP2-T09")
    d = plan_only_project("stale9")
    for n in (1, 2):
        r = rx(d, "ask", "approve-plan")
        out = out_of(r)
        check(f"[CP2-T09] normal command {n} exits 0 and is never told Ratchet is busy",
              r.returncode == 0 and not re.search(r"busy|still changing", out, re.I), out[-300:])
        check(f"[CP2-T09] ... and state.lock and state.lock.break do not exist after it", lock_leftovers(d) == [],
              lock_leftovers(d))
    lane = Lane()
    red = lane.start_red()
    live_text = lock_path(lane.d).read_text()
    rx(lane.d, "status")  # a command with another process id that starts and exits meanwhile
    r2 = rx(lane.d, "ask", "approve-plan")  # refused as busy, and exits
    check("[CP2-T09] an earlier command with another pid exiting does not clear a live command's lock",
          lock_path(lane.d).exists() and lock_path(lane.d).read_text() == live_text and red.poll() is None
          and "busy running" in out_of(r2), out_of(r2)[-300:])
    r3 = rx(lane.d, "ask", "approve-plan")
    check("[CP2-T09] ... and a later command is still refused as busy while the live command runs",
          r3.returncode != 0 and "busy running" in out_of(r3), out_of(r3)[-300:])
    lane.release_slow_test()
    red.communicate(timeout=60)
    check("[CP2-T09] the live command finishes and removes its own lock", lock_leftovers(lane.d) == [],
          lock_leftovers(lane.d))


def lock_sentences(path):
    """Sentences of the doc that talk about the lock, each with the sentence after it."""
    text = path.read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text)
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z`*(\"'])", flat)
    keep = []
    for i, s in enumerate(sents):
        if re.search(r"\block\b|state\.lock|Ratchet is busy", s) and re.search(r"clear|stale|left over|leftover|crash|stopp|killed|gone", s, re.I):
            keep.append(s + " " + (sents[i + 1] if i + 1 < len(sents) else ""))
    return " ".join(keep)


def schemas_state_lock_window():
    text = re.sub(r"\s+", " ", SCHEMAS.read_text(encoding="utf-8"))
    i = text.find("`.ratchet/state.lock`")
    if i < 0:
        return ""
    j = text.find("share this one breaker protocol", i)
    return text[i: j + 40 if j > 0 else i + 6000]


@case("CP2-T10")
def t10():
    """The README, the orchestrator skill and schemas.md say how a leftover lock is cleared."""
    docs = {"README.md": lock_sentences(README), "SKILL.md": lock_sentences(SKILL),
            "schemas.md": schemas_state_lock_window() + " " + lock_sentences(SCHEMAS)}
    for name, t in docs.items():
        check(f"[CP2-T10] {name} talks about a leftover lock", bool(t.strip()), "no sentence about clearing a lock found")
        check(f"[CP2-T10] {name} says a lock left by a command whose process is no longer running is cleared at once",
              bool(re.search(r"no longer running|not running|isn't running|is gone|has gone|has stopped|was stopped|"
                             r"was killed|been killed|process (?:is )?(?:dead|gone)|has exited", t, re.I))
              and bool(re.search(r"\bat once\b|immediately|right away", t, re.I)), t[:200])
        check(f"[CP2-T10] {name} says a lock it cannot check (older lock file, another machine, Windows) still clears by the timed rule",
              bool(re.search(r"another machine|other machine|different machine", t, re.I))
              and bool(re.search(r"windows", t, re.I)) and bool(re.search(r"older|old lock|old-format|legacy", t, re.I))
              and bool(re.search(r"timed|by age|minutes|after a (?:fixed )?(?:time|period)", t, re.I)), t[:200])
        check(f"[CP2-T10] {name} no longer says a stopped command's lock always waits the full timed period",
              not re.search(r"\b65 minutes\b|always waits?", t), t[:200])
    s = docs["schemas.md"]
    check("[CP2-T10] schemas.md says a lock held by a live command is respected",
          bool(re.search(r"live[^.]{0,100}(?:respect|left alone|never (?:cleared|removed)|not (?:cleared|removed))|"
                         r"(?:respect|left alone)[^.]{0,100}live", s, re.I)), s[:200])


# ------------------------------------------------------------------------------------------------ report + main

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
    global FAIL
    try:
        for cid, fn in TESTS:
            CASE_STATUS.setdefault(cid, True)
            print(f"\n[{cid}] {fn.__doc__ or ''}")
            try:
                fn()
            except Exception as e:  # a crash is still a result for this case, never a silent drop
                FAIL += 1
                CASE_STATUS[cid] = False
                CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(e).__name__}: {e}"[:200])
                print(f"  ✗ [{cid}] CRASHED: {e}")
                traceback.print_exc()
    finally:
        kill_everything()
        write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


import stale_lock_ns_cases  # noqa: E402,F401  (adds the CP2-X cases after the planned ones)


if __name__ == "__main__":
    main()
