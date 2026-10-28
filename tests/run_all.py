#!/usr/bin/env python3
"""
Run every test suite at the same time.   python3 tests/run_all.py [--dir DIR] [--workers N] [--timeout SECONDS] [--max-stretch X] [FILE ...]

Finds every test_*.py file directly under DIR (default: this tests/ folder) and runs each one as its own
process, several at once. With FILE arguments (paths relative to the working folder, or names inside DIR) it
runs only those suites, in the same way; a FILE that does not exist is refused before anything starts, so a
partly wrong list never runs half. Ratchet's own `related_test_command` ("python3 tests/run_all.py {files}")
calls it this way to run the test files related to a step before the full suite. Suites run several at once
(half the CPU cores by default, at least 2; fewer on a busy machine, see below). Each suite gets its own empty
temp folder (TMPDIR, TEMP and TMP point at it); a suite that leaves anything in that folder is reported as
failed even when its own checks passed. A suite still running after --timeout seconds (more on a busy machine,
see below) is stopped and reported as timed out.

The time limit grows with machine load: on every check it is --timeout times the one-minute load average divided by
the CPU count (never less than --timeout, never more than --timeout times --max-stretch, default 4; a load drop never
shortens a limit already granted), so a suite that is only slow because the machine is busy is given more time. The
number of suites started at once is also reduced on a busy machine (the one-minute load average strictly above the
CPU count, read once at start): the requested or computed count is cut by the same load/CPU share, down to a floor of 2
(never lower unless fewer than 2 were requested or there are fewer than 2 suites to run, in which case that smaller
number is left alone -- there is no floor to raise it to). --max-stretch's clamp also bounds how far this cut can go,
so --max-stretch 1 disables it along with the timeout stretch. The end summary has one line for each suite that ran
past its --timeout or was stopped at a stretched limit. RATCHET_FAKE_LOADAVG (a number, "unavailable", or @FILE
holding a number) and RATCHET_FAKE_CPUS replace the real readings; the tests use them.

Each suite also gets RATCHET_RUN_ALL=1 and RATCHET_RUN_ALL_SUITES (a comma-separated list of every suite name in
this run), so a suite that also runs other suites as subprocesses (test_trust_cp5_regression.py runs
test_gates.py and test_hooks_waiver.py) can skip ones already covered by this run instead of running them twice.

Each suite also gets a unique RATCHET_RUN_ALL_TAG in its environment, which every process it starts inherits.
When a suite ends (passed, failed or timed out), every live process whose /proc/<pid>/environ carries that tag
is stopped, so a background process the suite detached with setsid() and left behind does not survive it, even
after it was handed to another parent. Where there is no /proc this search is not possible; only the suite's
process group is stopped, and the summary says so.

The slowest suites start first, so a long suite does not start last and set the finish time. The order comes
from how long each suite took on earlier runs on this machine, kept in a small file in your user cache folder
(not in the repository). A suite with no recorded time is estimated from its file size.

At the end it prints the last lines of output of every failed suite, a table of every suite and how long it
took (slowest first), and a "N passed, M failed" line that counts suites. Exit code: 0 when every suite passed,
1 when any failed, 2 when no suites were found or the arguments are wrong, 130 when interrupted.

Stdlib only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TIMEOUT = 900.0
DEFAULT_TAIL = 40
DEFAULT_MAX_STRETCH = 4.0
POLL_SECONDS = 0.25
TAG_VAR = "RATCHET_RUN_ALL_TAG"
RUN_ALL_VAR = "RATCHET_RUN_ALL"
SUITES_VAR = "RATCHET_RUN_ALL_SUITES"


# ---- recorded durations ------------------------------------------------------------------------------------


def timings_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "ratchet-loop" / "run_all_timings.json"


def load_timings() -> dict:
    try:
        data = json.loads(timings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_timings(folder: Path, durations: dict) -> None:
    """Best effort: merge this run's durations into the file; drop folders that no longer exist."""
    path = timings_path()
    data = load_timings()
    data = {k: v for k, v in data.items() if isinstance(v, dict) and os.path.isdir(k)}
    entry = data.get(str(folder), {})
    entry.update({name: round(secs, 2) for name, secs in durations.items()})
    data[str(folder)] = entry
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        pass


def start_order(suites: list, history: dict) -> list:
    """Longest expected suite first. Expected time is the last recorded one; a suite with none is estimated
    from its file size, scaled by the seconds-per-byte of the suites that have a record."""
    sizes = {}
    for s in suites:
        try:
            sizes[s] = s.stat().st_size
        except OSError:
            sizes[s] = 0
    known = {s: float(history[s.name]) for s in suites
             if isinstance(history.get(s.name), (int, float))}
    known_bytes = sum(sizes[s] for s in known)
    rate = (sum(known.values()) / known_bytes) if known and known_bytes else 1.0

    def expected(s):
        return known[s] if s in known else sizes[s] * rate

    return sorted(suites, key=lambda s: (-expected(s), s.name))


# ---- machine load ------------------------------------------------------------------------------------------


def read_load():
    """(one-minute load average, CPU count) right now, or None when there is no usable reading. The environment
    variables RATCHET_FAKE_LOADAVG ("unavailable", a number, or "@file" holding a number) and RATCHET_FAKE_CPUS
    replace the real values (the tests use them). Never raises."""
    fake = os.environ.get("RATCHET_FAKE_LOADAVG")
    try:
        if fake is not None:
            fake = fake.strip()
            if fake.startswith("@"):
                with open(fake[1:], "r", encoding="utf-8") as f:
                    fake = f.read().strip()
            load = float(fake)
        else:
            load = os.getloadavg()[0]
        cpus = int(os.environ.get("RATCHET_FAKE_CPUS") or 0) or (os.cpu_count() or 1)
    except (OSError, ValueError, AttributeError):
        return None
    if not (load == load and load < float("inf")) or load < 0 or cpus < 1:
        return None
    return load, cpus


def stretch_factor(max_stretch: float):
    """(factor, load, cpus): how many times longer than --timeout a suite may run right now. The factor is
    load / cpus, at least 1 and at most max_stretch; 1 when there is no usable reading."""
    reading = read_load()
    if reading is None:
        return 1.0, None, None
    load, cpus = reading
    return min(max(1.0, load / cpus), max_stretch), load, cpus


def run_with_stretched_timeout(argv, base: float, max_stretch: float = DEFAULT_MAX_STRETCH, cwd=None, env=None,
                                capture_output: bool = True, text: bool = True, popen=subprocess.Popen,
                                monotonic=time.monotonic):
    """Runs argv in its own process group and waits for it to finish, the way subprocess.run() would, except the
    time limit follows stretch_factor() exactly like Runner.wait() does: it is re-read on every poll, so it is
    `base` times the highest stretch_factor(max_stretch) reading seen since the call started (a load spike while
    it runs still extends it, a load drop never takes back time already granted, and it is never less than
    `base`). On expiry the whole process tree is stopped with kill_tree() and subprocess.TimeoutExpired is
    raised (its .timeout is the limit that was in force). If a KeyboardInterrupt, SystemExit, or any other
    exception arrives anywhere in the poll loop -- while re-reading the load/clock for the next stretched limit,
    or while waiting in proc.communicate() -- the process tree is stopped with kill_tree() the same way, before
    the exception is re-raised unchanged -- the interrupt is never swallowed, and it is never stopped twice. On
    success, returns a subprocess.CompletedProcess like
    subprocess.run() would, with one extra attribute, `.timeout_limit`: the limit in force when the call
    finished, so a caller (or a test) can see what was actually used without ever waiting one out in real time.
    `popen` and `monotonic` default to subprocess.Popen/time.monotonic (every real call site leaves them alone);
    a test may inject stand-ins to make the whole call deterministic, with no real sleep or wall-clock read."""
    proc = popen(argv, stdout=subprocess.PIPE if capture_output else None,
                 stderr=subprocess.PIPE if capture_output else None, stdin=subprocess.DEVNULL,
                 cwd=cwd, env=env, text=text, start_new_session=True)
    t0 = monotonic()
    limit = base
    out = err = None
    while True:
        try:
            factor, _, _ = stretch_factor(max_stretch)
            limit = max(limit, base * factor)
            remaining = limit - (monotonic() - t0)
        except BaseException:
            kill_tree(proc)
            raise
        if remaining <= 0:
            kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise subprocess.TimeoutExpired(argv, limit)
        try:
            out, err = proc.communicate(timeout=min(POLL_SECONDS, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
        except BaseException:
            kill_tree(proc)
            raise
    result = subprocess.CompletedProcess(argv, proc.returncode, out, err)
    result.timeout_limit = limit
    return result


# ---- running one suite -------------------------------------------------------------------------------------


class Result:
    def __init__(self, name: str):
        self.name = name
        self.exit_code = None
        self.timed_out = False
        self.leftovers: list = []
        self.duration = 0.0
        self.tail: list = []
        self.error = ""
        self.stretch = ""  # a summary line when this suite ran past its --timeout or was stopped at a stretched one
        self.report_text = None  # this suite's own isolated $RATCHET_TEST_REPORT content, if any (see Runner.run)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.leftovers and not self.error

    def reason(self) -> str:
        parts = []
        if self.error:
            parts.append(self.error)
        if self.timed_out:
            parts.append(f"timed out after {self.duration:.0f}s and was stopped")
        elif self.exit_code not in (0, None):
            parts.append(f"exit {self.exit_code}")
        if self.leftovers:
            shown = ", ".join(self.leftovers[:5])
            more = f" (+{len(self.leftovers) - 5} more)" if len(self.leftovers) > 5 else ""
            parts.append(f"left temp files behind: {shown}{more}")
        return "; ".join(parts)


def _ppid_map() -> dict:
    """pid -> parent pid for every process /proc shows right now. Empty where there is no /proc (best effort)."""
    out = {}
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                raw = f.read()
        except OSError:
            continue
        # the 2nd field (the command name) is in parentheses and may hold ')'; the parent pid follows the last one
        after_name = raw.rsplit(")", 1)[-1].split()
        try:
            out[pid] = int(after_name[1])
        except (IndexError, ValueError):
            continue
    return out


def _descendant_pids(root_pid: int, ppid_of: dict) -> list:
    """Every live process descended from root_pid, found through the parent links in /proc. This also finds a
    process that left root_pid's process group with setsid() (a background server started with
    start_new_session=True, say): setsid() changes the session and group but not the parent. `ppid_of` is a
    _ppid_map() result."""
    found: list = []
    frontier = {root_pid}
    while frontier:
        children = {pid for pid, ppid in ppid_of.items() if ppid in frontier} - frontier - set(found)
        if not children:
            break
        found.extend(children)
        frontier = children
    return found


def kill_tree(proc, sig=None, also=()) -> list:
    """Send `sig` (default SIGKILL) to the process group of `proc` (a Popen started with start_new_session=True,
    so its group holds it and every child that did not detach), to every live descendant of it and to each
    descendant's own process group, so a child that detached into its own session with setsid() is stopped too.
    The descendants are found before anything is signalled: once `proc` exits they are handed to another parent
    and can no longer be traced back to it. So when `proc` has already exited, only its group and `also` are
    signalled (its pid may belong to another process by then). `also` holds pids from an earlier call (a SIGTERM
    before this SIGKILL, say) that are signalled again if still alive. Returns the pids it signalled. Never
    raises. Where there is no /proc the descendant search finds nothing and only the process groups are
    signalled; os.killpg/os.getpgid and SIGKILL are POSIX-only, so they are guarded."""
    if sig is None:
        sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    exited = proc.poll() is not None
    alive = _ppid_map()
    tree = [] if exited else _descendant_pids(proc.pid, alive)
    tree += [pid for pid in also if pid not in tree and pid in alive]
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, sig)
        elif not exited:
            os.kill(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    for pid in tree:
        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return tree


def _tagged_pids(tag: str) -> list:
    """Every live process whose /proc/<pid>/environ has TAG_VAR set to a value holding `tag` as one of its
    space-separated words (a run_all.py started inside another one's suite adds its own tag to the inherited
    value, so both runs can find the process). Empty where there is no /proc, or for a process whose environ
    can't be read (another user's, say). The environ file shows the environment the process started with."""
    found: list = []
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return found
    prefix = (TAG_VAR + "=").encode()
    for pid in pids:
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        for entry in raw.split(b"\0"):
            if entry.startswith(prefix):
                if tag in entry[len(prefix):].decode("utf-8", "replace").split():
                    found.append(pid)
                break
    return found


def sweep_tagged(proc, tag: str) -> list:
    """After a suite has ended, stop every process still alive that carries its tag (see _tagged_pids), through
    kill_tree(). Repeats until none is found (at most 10 passes), so a process forked during a pass is caught by
    the next. Returns every pid it signalled. Never raises; does nothing where there is no /proc."""
    signalled: list = []
    for _ in range(10):
        pids = [pid for pid in _tagged_pids(tag) if pid != os.getpid()]
        if not pids:
            break
        signalled += kill_tree(proc, also=pids)
        time.sleep(0.05)
    return signalled


def stop(proc) -> None:
    """Stop a suite and everything it started: SIGTERM first, then SIGKILL for whatever is still running."""
    tree = kill_tree(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    kill_tree(proc, also=tree)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def read_tail(log: Path, lines: int) -> list:
    try:
        with open(log, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


class Runner:
    def __init__(self, root: Path, timeout: float, tail: int, suite_names: str = "",
                 max_stretch: float = DEFAULT_MAX_STRETCH):
        self.root = root
        self.timeout = timeout
        self.max_stretch = max_stretch
        self.tail = tail
        self.suite_names = suite_names
        self.lock = threading.Lock()
        self.running: dict = {}
        self.stopping = False

    def run(self, suite: Path) -> Result:
        res = Result(suite.name)
        tmp = self.root / "tmp" / suite.stem
        log = self.root / "logs" / f"{suite.stem}.log"
        tmp.mkdir(parents=True)
        tag = f"{os.getpid()}-{suite.stem}-{secrets.token_hex(8)}"
        outer = os.environ.get(TAG_VAR, "").strip()
        # When the parent process (this run_all.py invocation) itself was given a $RATCHET_TEST_REPORT, do not
        # hand that SAME shared path to every suite unchanged: each suite's own report-writing helper does a
        # destructive overwrite, so whichever suite finishes last would destroy every other suite's results.
        # Instead give each suite its own isolated report path inside its own tmp folder; main() merges every
        # suite's isolated report back into the one real path after all suites finish. When the parent process
        # has no $RATCHET_TEST_REPORT at all, this is a no-op: no isolated path is set, nothing new is created.
        orig_report = os.environ.get("RATCHET_TEST_REPORT")
        isolated_report = (tmp / "report.json") if orig_report else None
        env = dict(os.environ, TMPDIR=str(tmp), TEMP=str(tmp), TMP=str(tmp), PYTHONUNBUFFERED="1",
                   **{TAG_VAR: f"{outer} {tag}".strip(), RUN_ALL_VAR: "1", SUITES_VAR: self.suite_names})
        if isolated_report is not None:
            env["RATCHET_TEST_REPORT"] = str(isolated_report)
        t0 = time.monotonic()
        try:
            with open(log, "wb") as out:
                with self.lock:
                    if self.stopping:
                        res.error = "not run (interrupted)"
                        return res
                    proc = subprocess.Popen([sys.executable, str(suite)], stdout=out, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, env=env, start_new_session=True)
                    self.running[suite.name] = proc
                try:
                    self.wait(proc, res, t0)
                finally:
                    with self.lock:
                        self.running.pop(suite.name, None)
                # Anything the suite started and left running would keep writing into its temp folder.
                kill_tree(proc)
                # A child that detached with setsid() was handed to another parent once the suite exited, so
                # neither the group nor the descendant walk finds it; its inherited tag still does.
                sweep_tagged(proc, tag)
        except OSError as e:
            res.error = f"could not start: {e}"
        res.duration = time.monotonic() - t0
        if isolated_report is not None and isolated_report.exists():
            try:
                res.report_text = isolated_report.read_text(encoding="utf-8")
            except OSError:
                res.report_text = None
            try:
                isolated_report.unlink()  # read before the leftover check below, so it is never reported as one
            except OSError:
                pass
        try:
            res.leftovers = sorted(os.listdir(tmp))
        except OSError:
            res.leftovers = []
        shutil.rmtree(tmp, ignore_errors=True)
        if not res.passed:
            res.tail = read_tail(log, self.tail)
        return res

    def wait(self, proc, res: Result, t0: float) -> None:
        """Wait for a suite, polling. The deadline is --timeout times the highest stretch factor read while the
        suite runs, so a load spike while it runs still extends it and a load drop never takes back time already
        granted; it is never earlier than --timeout. Stops the suite when the deadline passes. The summary line
        reports the limit in force when the suite ended (the highest reached, or a fresh reading when the suite
        ended within one poll of passing --timeout); a suite that ran within the plain --timeout gets no line."""
        peak = None  # the reading that set the highest limit so far: the limit in force never shrinks
        limit = self.timeout
        while True:
            factor, load, cpus = stretch_factor(self.max_stretch)
            if self.timeout * factor > limit:
                limit = self.timeout * factor
                peak = (factor, load, cpus)
            elapsed = time.monotonic() - t0
            if elapsed >= limit:
                res.timed_out = True
                stop(proc)
                res.exit_code = proc.returncode
                break
            try:
                res.exit_code = proc.wait(timeout=min(POLL_SECONDS, limit - elapsed))
                break
            except subprocess.TimeoutExpired:
                continue
        ran_past = res.timed_out or time.monotonic() - t0 >= self.timeout
        if ran_past and not res.timed_out:
            fresh = stretch_factor(self.max_stretch)  # ended within one poll of passing --timeout
            if self.timeout * fresh[0] > limit:
                limit = self.timeout * fresh[0]
                peak = fresh
        if peak and ran_past:
            factor, load, cpus = peak
            res.stretch = (f"{res.name}: time limit stretched {factor:.1f}x ({self.timeout:.0f}s to "
                           f"{limit:.0f}s) at load {load:.1f} on {cpus} CPUs")

    def stop_all(self) -> None:
        with self.lock:
            self.stopping = True
            procs = list(self.running.values())
        for p in procs:
            stop(p)


# ---- main --------------------------------------------------------------------------------------------------


def default_workers() -> int:
    """Half the CPU cores, at least 2. Every suite starts engine subprocesses of its own, so one suite per core
    oversubscribes the machine: on a 12-core machine 6 at a time finished the full run in about 540 to 560s,
    8 in about 570s and 12 in about 590 to 670s."""
    return max(2, (os.cpu_count() or 2) // 2)


def parse_args(argv):
    p = argparse.ArgumentParser(usage="%(prog)s [options] [FILE ...]",
                                description="Run every tests/test_*.py suite at the same time.")
    p.add_argument("--dir", default=str(HERE), help="folder holding the test_*.py suites (default: tests/)")
    p.add_argument("--workers", type=int, default=default_workers(),
                   help=f"how many suites run at once, at most (default {default_workers()}: half the CPU cores, at "
                        "least 2; each suite starts its own subprocesses, so one suite per core overloads the machine "
                        "and was slower when measured); on a busy machine (load strictly above the CPU count) fewer "
                        "start at once, down to a floor of 2 unless this itself asked for fewer")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                   help="seconds before a suite is stopped and reported as timed out; on a busy machine the limit grows, "
                        f"up to --max-stretch times this (default {DEFAULT_TIMEOUT:.0f})")
    p.add_argument("--max-stretch", type=float, default=DEFAULT_MAX_STRETCH,
                   help="the time limit grows with machine load (timeout x load / CPUs); this is the most times "
                        f"the timeout it can grow to (default {DEFAULT_MAX_STRETCH:.0f}; 1 turns the stretch off). "
                        "It also bounds how far the worker-concurrency cap can cut the number of suites running at "
                        "once on a busy machine, so --max-stretch 1 turns that cap off too")
    p.add_argument("--tail", type=int, default=DEFAULT_TAIL,
                   help=f"lines of output shown for each failed suite (default {DEFAULT_TAIL})")
    p.add_argument("files", nargs="*", metavar="FILE",
                   help="run only these suites: paths relative to the working folder, or names inside --dir "
                        "(default: every test_*.py in --dir)")
    args = p.parse_args(argv)
    if args.workers < 1:
        p.error("--workers must be at least 1")
    if args.timeout <= 0:
        p.error("--timeout must be more than 0")
    if not (1.0 <= args.max_stretch < float("inf")):
        p.error("--max-stretch must be a number, 1 or more")
    if args.tail < 0:
        p.error("--tail must be 0 or more")
    return args


def merge_reports(results: list) -> None:
    """After every suite has finished, union every suite's own isolated $RATCHET_TEST_REPORT (see Runner.run)
    into the ONE real path named by this process's own $RATCHET_TEST_REPORT, if that was set at all. Merge
    means: every suite's "cases" list, concatenated in `results` order (ids are not deduplicated: different
    suites use disjoint id namespaces in practice). A suite with no isolated report (most suites: they never
    looked at $RATCHET_TEST_REPORT), or one whose isolated report is missing or not valid JSON, contributes
    nothing. When $RATCHET_TEST_REPORT is not set, or no suite contributed any cases, this does nothing: no
    file is written and none of this run's behavior changes (the common case: a developer running this
    directly, with no report path set at all)."""
    orig_report = os.environ.get("RATCHET_TEST_REPORT")
    if not orig_report:
        return
    merged_cases: list = []
    for r in results:
        if not r.report_text:
            continue
        try:
            data = json.loads(r.report_text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("cases"), list):
            merged_cases.extend(data["cases"])
    if not merged_cases:
        return
    try:
        with open(orig_report, "w", encoding="utf-8") as f:
            json.dump({"cases": merged_cases}, f)
    except OSError:
        pass


def _interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main(argv=None) -> int:
    args = parse_args(argv)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _interrupt)  # stopped from outside: still stop every suite it started
    folder = Path(args.dir).resolve()
    if not folder.is_dir():
        print(f"No test suites found: {folder} is not a folder.", file=sys.stderr)
        return 2
    if args.files:
        suites, missing = [], []
        for f in args.files:
            cand = Path(f)
            if not cand.is_file():
                cand = folder / Path(f).name
            if not cand.is_file():
                missing.append(f)
            elif cand.resolve() not in {s.resolve() for s in suites}:
                suites.append(cand.resolve())
        if missing:
            print("No suite was run: " + ", ".join(f"{m} does not exist (not a file relative to the working folder, "
                                                   f"and not a file inside {folder})" for m in missing),
                  file=sys.stderr)
            return 2
    else:
        suites = sorted(p for p in folder.glob("test_*.py") if p.is_file())
    if not suites:
        print(f"No test suites found in {folder} (looked for test_*.py files).", file=sys.stderr)
        return 2

    history = load_timings().get(str(folder), {})
    order = start_order(suites, history if isinstance(history, dict) else {})
    workers = min(args.workers, len(order))
    # Load-aware cap: on a busy machine, start fewer suites at once than requested/computed, using the exact
    # same stretch_factor()/read_load() reading and magnitude (the share to reduce by) that
    # run_with_stretched_timeout() uses for the timeout stretch -- reused as-is, not a second, divergent
    # formula. This is a backstop against genuine, measurable oversubscription: it only reduces `workers` once
    # the one-minute load average is actually above the CPU count (factor > 1.0), the same point past which
    # stretch_factor() itself starts growing the timeout. At exactly load == cpus, stretch_factor() returns its
    # floor of 1.0 ("never shrink a timeout below the requested one"), so int(workers / 1.0) == workers and this
    # leaves the count unchanged -- intentional, not a bug: the cap reacts to measurable oversubscription, not to
    # the exact mathematical saturation boundary. On an idle or unreadable-load machine this never triggers and
    # `workers` is untouched -- byte for byte what it is today. It only ever reduces `workers`, never raises it;
    # it only imposes a floor of 2 when it actually reduces a count that was 2 or more to begin with (a
    # requested/computed count already below 2, such as --workers 1 or a single-suite run, is left alone --
    # there is no floor to enforce there).
    factor, load, cpus = stretch_factor(args.max_stretch)
    if load is not None and cpus and factor > 1.0:
        capped = min(workers, max(2, int(workers / factor)))
        if capped < workers:
            print(f"Worker count capped from {workers} to {capped} (load {load:.1f} on {cpus} CPUs).", flush=True)
        workers = capped
    print(f"Running {len(order)} suites from {folder}, {workers} at a time, slowest first "
          f"(timeout {args.timeout:.0f}s each).", flush=True)

    root = Path(tempfile.mkdtemp(prefix="ratchet-run-all-"))
    (root / "tmp").mkdir()
    (root / "logs").mkdir()
    suite_names = ",".join(s.name for s in order)
    runner = Runner(root, args.timeout, args.tail, suite_names, args.max_stretch)
    results: list = []
    started = time.monotonic()
    interrupted = False
    futures: list = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(runner.run, s) for s in order]
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            results.append(res)
            label = "passed" if res.passed else f"FAILED ({res.reason()})"
            print(f"[{len(results)}/{len(order)}] {label}  {res.name}", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        for f in futures:
            f.cancel()
        runner.stop_all()
    finally:
        pool.shutdown(wait=True)
        shutil.rmtree(root, ignore_errors=True)
    wall = time.monotonic() - started

    if interrupted:
        print(f"\nInterrupted after {wall:.1f}s; {len(results)} of {len(order)} suites had finished.", flush=True)
        return 130

    merge_reports(results)

    save_timings(folder, {r.name: r.duration for r in results if not r.error})

    failed = [r for r in results if not r.passed]
    for r in sorted(failed, key=lambda r: r.name):
        print(f"\n=== {r.name} failed: {r.reason()}")
        if r.tail:
            print(f"--- last {len(r.tail)} lines of its output ---")
            for ln in r.tail:
                print(f"    {ln}")
        else:
            print("--- it printed nothing ---")

    print("\nSuite times (slowest first):")
    width = max(len(r.name) for r in results)
    for r in sorted(results, key=lambda r: (-r.duration, r.name)):
        status = "passed" if r.passed else "FAILED"
        print(f"  {r.duration:7.1f}s  {status:<6}  {r.name:<{width}}")
    stretched = sorted(r.stretch for r in results if r.stretch)
    if stretched:
        print()
        for ln in stretched:
            print(ln)
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed (suites) in {wall:.1f}s "
          f"with {workers} workers", flush=True)
    if failed:
        print("Failed: " + ", ".join(sorted(r.name for r in failed)))
    if not os.path.isdir("/proc"):
        print("Note: no /proc on this platform, so a process a suite detached and left running after it ended "
              "could not be found; only each suite's process group was stopped.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
