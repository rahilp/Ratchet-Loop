#!/usr/bin/env python3
"""
CP5 of the trust-holes run, part e: approvals answer the question Ratchet asked.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_e.py
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

# [CP2] A monotonic mark taken as close to process start as this file can manage, so CP2-T02 can bound the REAL
# top-level wall-clock of `python3 -I tests/test_trust_cp5_e.py` (what a user actually runs and waits on) instead
# of only the inner timer _cp2_run_once() puts around its own re-spawned child -- see t02_wall_clock_under_60s().
_CP2_PROCESS_START = time.monotonic()

# `-I` (isolated mode; this file's own checkpoint command runs it that way) implies safe_path, so Python no longer
# puts this script's own directory on sys.path the way a plain `python3 tests/test_trust_cp5_e.py` does -- without
# this, `import _tmp` below fails before a single check runs. [CP2] harness fix, not a behavior change.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _tmp
import trust_helpers
from trust_helpers import (
    ENGINE, PROVIDER_DISCOVERY, RECORDS, RUN_RECORDS, check, cp5v_until, cp5y_load_engine, engine_env, git,
    make_env, now_dt,
)

# ==================================================================================================================
# CP2 (speed-up checkpoint): the one in-process entry point every converted rx(...) call site below stands in for.
# ratchet.py recompiles its ~21k lines every time a subprocess spawns it (the real cost CP1's scout measured, not
# subprocess/git/flock overhead): trust_helpers.cp5y_load_engine() now memoizes, so every call below reuses the
# SAME loaded module object `m` instead of paying that cost again. _Cp2Call runs that module's own main(args) the
# way main() always runs, in-process: chdir to the project, swap os.environ for exactly the test's env (both
# restored after), optionally feed `stdin` text to sys.stdin, and capture stdout/stderr/exit code -- the same
# shape rx()'s subprocess.CompletedProcess gives check() bodies below, so none of them needed to change. Because
# `m` is reused for every call, a handful of its own module-level flags that a real subprocess would always start
# fresh (set once by a previous command and never reset by ratchet.py itself, since it never expected to be asked
# to run a second command in the same process) are put back to their pristine default both before AND after each
# call (see trust_helpers.InProcessCall, which _Cp2Call below is a thin constructor over).
_CP2_RESET_GLOBALS = {"_IN_HOOK": False, "_OVERRIDE_CONFIRMED": False, "_CHAIN_REBUILD": False,
                      "_HOOK_ENV_ADVISORY": False, "_STATE_LOCK_HELD": False, "_ENGINE_ARGV": None}

# A busy per-record lock (enrolled.json, active-runs.json, provider-status.json, unavailable-reviewers.json, ...)
# is waited for real (CPython's own time.sleep loop) before a command refuses; none of the check()s below assert
# a MINIMUM wait, only an upper bound ("took < 60") or just that the refusal happens and says the right thing, so
# capping how long _Cp2Call's own calls wait for one shortens the suite without weakening what any check proves --
# same reasoning as CP2-T08 applies to cp5v_until's timeout ceiling. Capped only inside _Cp2Call's own calls (never
# round 13's X70, which drives _RecordLock directly, outside any _Cp2Call, to prove its interleaved writers really
# wait on each other) so that race's own timing is untouched.
_CP2_LOCK_WAIT_CAP = 0.3


class _Cp2Call(trust_helpers.InProcessCall):
    """`with _Cp2Call(m, d, env) as r: m.main([...])` -- the in-process stand-in for one rx(...) subprocess call.
    `r.returncode` / `r.stdout` / `r.stderr` read the same way a subprocess.CompletedProcess's do.

    [Corrected, round 2] This class used to be a hand copy of its CP3 twin (tests/test_trust_cp5_d.py's
    `_Cp3Call`) that had already drifted from it by round 2 (host-alt-standards: duplicated-test-helper, raised
    twice). Both classes are now thin constructors over the one shared implementation,
    trust_helpers.InProcessCall -- see that class's own docstring for the full contract (the lock-wait cap, the
    entry-and-exit global reset, and the KeyboardInterrupt-propagation guard all live there now, identical for
    both suites). The ONE remaining difference between this class and `_Cp3Call`: this one passes
    `reset_unreadable=False` -- CP2's suite here has no check that asserts a SECOND damaged-record warning about
    the SAME path across two separate in-process calls (checked, this is not a bug in this file); `_Cp3Call`
    passes True because its own CP3 suite does (X64). If a future CP2 case ever needs that too, change the
    `False` below to `True`."""

    def __init__(self, m, d, env, stdin=None):
        super().__init__(m, d, env, _CP2_RESET_GLOBALS, _CP2_LOCK_WAIT_CAP, reset_unreadable=False, stdin=stdin)


# [CP2] cp2_project / cp2_guard_tool / cp2_director below are intentional hand-synced copies of
# trust_helpers.project() / cp4x_guard_tool() / cp5r12_director(), each rewritten to call the loaded engine
# in-process instead of spawning rx(...) subprocesses. trust_helpers' originals are left exactly as they were
# (every other trust-hole suite still uses them as real subprocesses), so nothing keeps these two recipes in sync
# automatically -- a later change to an original's setup steps must be applied here by hand too, or this suite
# starts proving things about a project state real projects no longer have. Converting the originals in place is
# a later checkpoint's job (done-means 2-3); when that happens, these mirrors should be deleted.
def cp2_project(m, env):
    """In-process mirror of trust_helpers.project(env) (approved=True, host claude): the same steps, each rx(...)
    subprocess call converted to one in-process m.main(...) call. trust_helpers.project() itself is left exactly
    as it was -- every other trust-hole suite still uses it as a real subprocess, and converting those is a later
    checkpoint's job, not this one's."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-p-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    first_prompt = json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                               "transcript_path": str(tp)})
    # replaces rx(d, env, "hook", "prompt", "--host", "claude", stdin=first_prompt)
    with _Cp2Call(m, d, env, stdin=first_prompt) as r:
        m.main(["hook", "prompt", "--host", "claude"])
    # replaces rx(d, env, "init", "feature", "--slug", "f")
    with _Cp2Call(m, d, env) as r:
        m.main(["init", "feature", "--slug", "f"])
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    # replaces rx(d, env, "config", "--set", "min_review_seconds=0")
    with _Cp2Call(m, d, env) as r:
        m.main(["config", "--set", "min_review_seconds=0"])
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    # replaces rx(d, env, "plan-submit")
    with _Cp2Call(m, d, env) as r:
        m.main(["plan-submit"])
    assert r.returncode == 0, r.stderr + r.stdout
    # replaces rx(d, env, "ask", "approve-plan")
    with _Cp2Call(m, d, env) as r:
        m.main(["ask", "approve-plan"])
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    second_prompt = json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                                "transcript_path": str(tp)})
    # replaces rx(d, env, "hook", "prompt", "--host", "claude", stdin=second_prompt)
    with _Cp2Call(m, d, env, stdin=second_prompt) as r:
        m.main(["hook", "prompt", "--host", "claude"])
    # replaces rx(d, env, "approve-plan")
    with _Cp2Call(m, d, env) as r:
        m.main(["approve-plan"])
    assert r.returncode == 0, r.stderr + r.stdout
    # replaces rx(d, env, "install-hooks", "--host", "claude", "--scope", "project")  (trust_helpers.install)
    with _Cp2Call(m, d, env) as r:
        m.main(["install-hooks", "--host", "claude", "--scope", "project"])
    assert r.returncode == 0, r.stderr + r.stdout
    stop_payload = json.dumps({"session_id": "s", "cwd": str(d), "hook_event_name": "Stop", "stop_hook_active": False})
    prompt_payload = json.dumps({"session_id": "s", "cwd": str(d), "hook_event_name": "UserPromptSubmit",
                                 "prompt": "hello"})
    # replaces rx(d, env, "hook", "prompt", "--host", "claude", stdin=prompt_payload)  (trust_helpers.fire_claude)
    with _Cp2Call(m, d, env, stdin=prompt_payload) as r:
        m.main(["hook", "prompt", "--host", "claude"])
    # replaces rx(d, env, "hook", "stop", "--host", "claude", stdin=stop_payload)  (trust_helpers.fire_claude)
    with _Cp2Call(m, d, env, stdin=stop_payload) as r:
        m.main(["hook", "stop", "--host", "claude"])
    return d


def cp2_guard_tool(m, d, env, tool, tool_input, session="s"):
    """In-process mirror of trust_helpers.cp4x_guard_tool."""
    payload = json.dumps({"session_id": session, "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": tool,
                          "tool_input": tool_input})
    # replaces rx(d, env, "hook", "guard", "--host", "claude", stdin=payload)  (trust_helpers.cp4x_guard_tool)
    with _Cp2Call(m, d, env, stdin=payload) as r:
        m.main(["hook", "guard", "--host", "claude"])
    return r


def cp2_director(m, home_dir, denv, lane, *args):
    """In-process mirror of trust_helpers.cp5r12_director."""
    cp2_guard_tool(m, home_dir, denv, "Bash", {"command": f"python3 {ENGINE} --root {lane} {' '.join(args)}"},
                  session="s-director")
    # replaces rx(home_dir, denv, "--root", str(lane), *args)  (trust_helpers.cp5r12_director)
    with _Cp2Call(m, home_dir, denv) as r:
        m.main(["--root", str(lane), *args])
    return r


SECTIONS = []


# ---- CP5 adversarial round 13 ----
# Every read-modify-write of a shared record (every record in RATCHET_HOME, and the project records that hooks or
# commands outside the state lock rewrite) goes through one helper, update_record, under a _BreakerLock-based lock
# for that file, so two writers never drop each other's entry. Each file keeps its own damaged-record rule.

_CP5R13_SHARED_PATHS = {"_markers_path", "_enrolled_path", "_director_folders_path", "_stop_errors_path",
                        "_chain_path", "_engine_notes_path", "_status_path", "_unavailable_path", "_heartbeat_path",
                        "_hook_env_path", "_health_path"}
_CP5R13_SHARED_NAMES = {"active-runs.json", "enrolled.json", "director-folders.json", "stop-errors.json",
                        "file-hashes.json", "engine-call-sessions.json", "provider-status.json",
                        "unavailable-reviewers.json", "hook-heartbeat.json", "hook-env.json",
                        "provider-health.json"}
_CP5R13_WRITE_OK = {
    "update_record": "the one read-modify-write of a shared record, under its lock",
    "write_json": "a facade over _write_record",
    "record_hook_env": "hook-env.json is rebuilt from the per-run records in hook-env/, never from its own content, "
                       "and checked again after each write (schemas.md section 10)",
    "_start_server": "viewer.json or the lanes viewer's state file, handed in by `view` / `lanes`: written whole "
                     "for the server it just started, never merged with what was there",
}


def cp5r13_shared_write_violations(src):
    """[(function, line, what)] for every write (_write_record / write_json) of a shared record outside
    update_record, and every write whose path is a parameter of its function (a helper that could hide such a
    write), apart from the allow-list above. A path counts as a shared record when it is built from one of the
    shared record path functions or file names, directly or through a local name assigned from one."""
    import ast
    tree = ast.parse(src)
    out = []

    def callee(n):
        f = n.func
        return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)

    def mentions(node, tainted):
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and callee(n) in _CP5R13_SHARED_PATHS:
                return True
            if isinstance(n, ast.Constant) and n.value in _CP5R13_SHARED_NAMES:
                return True
            if isinstance(n, ast.Name) and n.id in tainted:
                return True
        return False

    def scan(fn, qual):
        a = fn.args
        params = {x.arg for x in a.args + a.kwonlyargs + getattr(a, "posonlyargs", [])}
        tainted, changed = set(), True
        while changed:
            changed = False
            for n in ast.walk(fn):
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and mentions(n.value, tainted):
                    for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                        for x in ast.walk(t):
                            if isinstance(x, ast.Name) and x.id not in tainted:
                                tainted.add(x.id)
                                changed = True
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call) or not n.args or callee(n) not in ("_write_record", "write_json"):
                continue
            if qual in _CP5R13_WRITE_OK:
                continue
            arg = n.args[0]
            if mentions(arg, tainted):
                out.append((qual, n.lineno, "shared record"))
            elif isinstance(arg, ast.Name) and arg.id in params:
                out.append((qual, n.lineno, "path parameter"))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan(node, node.name)
        elif isinstance(node, ast.ClassDef):
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scan(b, f"{node.name}.{b.name}")
    return out


def cp5r13_interleave(m, record, calls):
    """Run each call in its own thread. Every time a worker reads `record`, it then waits (half a second at most) for
    the others to have read it too before it goes on to write: round 13's race, made certain. With a lock around
    the read-modify-write the others can't read until the first one has written, so the wait times out (the
    barrier breaks for good) and the writes take turns. Returns the errors the calls raised."""
    import threading
    real = m._read_record
    # _read_record's one real home is records.py since CP4; a writer defined there (record_chain, for the
    # file-hashes.json race below) resolves the bare name `_read_record` against records.py's own globals, not
    # m's -- patching m._read_record alone would never slow THAT writer down, so the race it's meant to force
    # would never actually happen. Patch whichever module(s) are really loaded.
    records_mod = sys.modules.get("records")
    barrier = threading.Barrier(len(calls), timeout=0.5)
    rec = Path(record).resolve()

    def slow(path, *a, **k):
        out = real(path, *a, **k)
        if threading.current_thread().name.startswith("cp5r13-") and Path(path).resolve() == rec:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return out
    errs = []

    def run(fn):
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - reported by the check
            errs.append(repr(e))
    m._read_record = slow
    if records_mod is not None:
        records_mod._read_record = slow
    try:
        ts = [threading.Thread(target=run, args=(c,), name=f"cp5r13-{i}") for i, c in enumerate(calls)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
    finally:
        m._read_record = real
        if records_mod is not None:
            records_mod._read_record = real
    return errs


def cp5r13_json(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


_CP5R13_CHILD = """
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5r13", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
record, start, ready = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
real = m._read_record

def slow(path, *a, **k):
    out = real(path, *a, **k)
    if Path(path).name == record:
        time.sleep(0.4)  # widen the window between this read and the write after it
    return out
m._read_record = slow
# update_record's own `_read_record` call (every shared-record writer goes through update_record) resolves
# against records.py's globals since CP4's split, not m's -- patch that module too, when it is loaded.
_records_mod = sys.modules.get("records")
if _records_mod is not None:
    _records_mod._read_record = slow
ready.write_text("1")
while not start.exists():
    time.sleep(0.002)
sys.argv = ["ratchet.py"] + sys.argv[5:]
try:
    m.main()
    rc = 0
except SystemExit as e:
    rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
sys.exit(rc)
"""


def cp5r13_race(script, record, jobs):
    """Run the engine command of each job ((cwd, env, argv)) in its own process, all released at once; each reads
    `record` slowly. Returns the finished processes' (returncode, stderr)."""
    gate = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-g-"))
    # CP2: kept as a REAL subprocess.Popen race -- it proves genuine cross-process contention for a shared
    # record's lock; one Python process calling in-process functions cannot reproduce two separate OS processes
    # racing to read-modify-write the same file.
    procs = [subprocess.Popen([sys.executable, str(script), str(ENGINE), record, str(gate / "go"),
                               str(gate / f"ready-{i}"), *argv], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
             for i, (cwd, env, argv) in enumerate(jobs)]
    ok_ready = cp5v_until(lambda: all((gate / f"ready-{i}").exists() for i in range(len(jobs))), timeout=120)
    (gate / "go").write_text("go")
    out = []
    for p in procs:
        try:
            _o, e = p.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()
            _o, e = p.communicate()
        out.append((p.returncode, e))
    shutil.rmtree(gate, ignore_errors=True)
    return ok_ready, out


def cp5r13_pre_init_project(env):
    """A project right before its first `init` (the same steps as cp4_unapproved_plan up to init)."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-p-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    m = cp5y_load_engine()
    payload = json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                          "transcript_path": str(tp)})
    # replaces rx(d, env, "hook", "prompt", "--host", "claude", stdin=payload)
    with _Cp2Call(m, d, env, stdin=payload) as r:
        m.main(["hook", "prompt", "--host", "claude"])
    return d


def cp5_adv_round13_shared_record_lock():
    print("CP5 adv round 13: every read-modify-write of a shared record holds that record's lock, so none is lost")
    import contextlib
    import io
    m = cp5y_load_engine()
    # [CP4] update_record/_read_record/_write_record/_RecordLock/_BreakerLock moved into records.py; the scans
    # below (and the AST lookup of update_record's own def just below) need both files' source combined.
    src = ENGINE.read_text(encoding="utf-8") + "\n" + RECORDS.read_text(encoding="utf-8") + "\n" + \
        PROVIDER_DISCOVERY.read_text(encoding="utf-8") + "\n" + RUN_RECORDS.read_text(encoding="utf-8")

    # --- X68: several `init`s in different projects at once: active-runs.json keeps every run ------------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    projects = [cp5r13_pre_init_project(env) for _ in range(4)]
    script = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-s-")) / "child.py"
    script.write_text(_CP5R13_CHILD)
    ready, res = cp5r13_race(script, "active-runs.json",
                             [(d, env, ["init", "feature", "--slug", "f"]) for d in projects[:3]])
    runs = cp5r13_json(rh / "active-runs.json") or {}
    want = {str(d.resolve()) for d in projects[:3]}
    check("[CP5-X68] three `init`s in three projects at once, each reading active-runs.json slowly: every one "
          "starts, and active-runs.json keeps all three runs (none drops another's entry)",
          ready and all(rc == 0 for rc, _e in res) and want <= set(runs)
          and all((runs.get(k) or {}).get("slug") == "f" for k in want),
          (ready, [(rc, e[-300:]) for rc, e in res if rc != 0], sorted(runs)))
    # replaces rx(projects[3], env, "init", "feature", "--slug", "f")
    with _Cp2Call(m, projects[3], env) as r:
        m.main(["init", "feature", "--slug", "f"])
    runs = cp5r13_json(rh / "active-runs.json") or {}
    check("[CP5-X68] ... and a later run keeps them too (the record is read again under the lock, never a stale copy)",
          r.returncode == 0 and set(runs) >= want | {str(projects[3].resolve())}, (r.returncode, sorted(runs)))

    # --- X69: several director-enrolls at once: enrolled.json keeps every lane ---------------------------------
    lanes = projects[:3]
    for d in projects:
        # replaces cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
        cp2_guard_tool(m, d, env, "Bash", {"command": "ls"})
    home_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-director-"))
    denv = dict(env, RATCHET_ROLE="director")
    # replaces cp5r12_director(home_dir, denv, lanes[0], "director-key")
    k = cp2_director(m, home_dir, denv, lanes[0], "director-key")
    for d in projects:
        # replaces cp4x_guard_tool(home_dir, denv, "Bash", {"command": ...}, session="s-director")
        cp2_guard_tool(m, home_dir, denv, "Bash", {"command": f"python3 {ENGINE} --root {d} director-enroll"},
                      session="s-director")
    ready, res = cp5r13_race(script, "enrolled.json",
                             [(home_dir, denv, ["--root", str(d), "director-enroll"]) for d in lanes])
    enrolled = cp5r13_json(rh / "enrolled.json") or {}
    want = {str(d.resolve()) for d in lanes}
    check("[CP5-X69] three director-enrolls for three lanes at once, each reading enrolled.json slowly: every one "
          "succeeds, every lane has its director.json, and enrolled.json keeps all three lanes",
          k.returncode == 0 and ready and all(rc == 0 for rc, _e in res) and set(enrolled) == want
          and all((d / ".ratchet" / "director.json").exists() for d in lanes),
          (k.returncode, ready, [(rc, e[-300:]) for rc, e in res if rc != 0], sorted(enrolled)))

    # --- X70: every shared record writer, two at once with the race forced: both entries survive ---------------
    lost = {}
    with engine_env(env):
        a = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-a-"))
        b = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-b-"))
        for x in (a, b):
            (x / ".ratchet").mkdir()
        ra, rb = str(a.resolve()), str(b.resolve())
        before = cp5r13_json(rh / "active-runs.json") or {}
        errs = cp5r13_interleave(m, m._markers_path(), [lambda: m.register_active(a, "fa"),
                                                        lambda: m.register_active(b, "fb")])
        got = cp5r13_json(rh / "active-runs.json") or {}
        lost["active-runs.json"] = (errs, not ((got.get(ra) or {}).get("slug") == "fa"
                                               and (got.get(rb) or {}).get("slug") == "fb"
                                               and set(before) <= set(got)))
        errs = cp5r13_interleave(m, m._director_folders_path(), [lambda: m.record_director_folder(a),
                                                                 lambda: m.record_director_folder(b)])
        got = cp5r13_json(m._director_folders_path()) or {}
        lost["director-folders.json"] = (errs, not {os.path.realpath(str(a)), os.path.realpath(str(b))} <= set(got))
        errs = cp5r13_interleave(m, m._stop_errors_path(), [lambda: m._stop_error(a, "t"),
                                                            lambda: m._stop_error(b, "t")])
        got = cp5r13_json(m._stop_errors_path()) or {}
        lost["stop-errors.json"] = (errs, not (got.get(ra) == 1 and got.get(rb) == 1))
        errs = cp5r13_interleave(m, a / ".ratchet" / "hook-heartbeat.json",
                                 [lambda: m._heartbeat(a, "guard", "claude"), lambda: m._heartbeat(a, "stop", "codex")])
        got = cp5r13_json(a / ".ratchet" / "hook-heartbeat.json") or {}
        lost["hook-heartbeat.json"] = (errs, not {"guard", "stop"} <= set(got))
        ctx = m.Ctx(a)
        errs = cp5r13_interleave(m, a / ".ratchet" / "unavailable-reviewers.json",
                                 [lambda: m.mark_unavailable(ctx, "x-correctness", "quota", hours=1),
                                  lambda: m.mark_unavailable(ctx, "y-correctness", "quota", hours=1)])
        got = cp5r13_json(a / ".ratchet" / "unavailable-reviewers.json") or {}
        lost["unavailable-reviewers.json"] = (errs, not {"x-correctness", "y-correctness"} <= set(got))
        errs = cp5r13_interleave(m, a / ".ratchet" / "provider-status.json",
                                 [lambda: m._update_provider_status(ctx, lambda d: d.__setitem__("x", {"ok": True})),
                                  lambda: m._update_provider_status(ctx, lambda d: d.__setitem__("y", {"ok": True}))])
        got = cp5r13_json(a / ".ratchet" / "provider-status.json") or {}
        lost["provider-status.json"] = (errs, not {"x", "y"} <= set(got))
        errs = cp5r13_interleave(m, m._health_path(),
                                 [lambda: m._update_health(lambda d: d.__setitem__("x", {"ok": True})),
                                  lambda: m._update_health(lambda d: d.__setitem__("y", {"ok": True}))])
        got = cp5r13_json(m._health_path()) or {}
        lost["provider-health.json"] = (errs, not {"x", "y"} <= set(got))
        fa, fb = a / "f.txt", b / "f.txt"
        fa.write_text("a")
        fb.write_text("b")
        errs = cp5r13_interleave(m, m._chain_path(), [lambda: m.record_chain(fa), lambda: m.record_chain(fb)])
        got = cp5r13_json(m._chain_path()) or {}
        lost["file-hashes.json"] = (errs, not {str(fa.resolve()), str(fb.resolve())} <= set(got))
        errs = cp5r13_interleave(m, m._engine_notes_path(),
                                 [lambda: m._note_engine_call(a, "$RATCHET ask choice --tag a", "s-a"),
                                  lambda: m._note_engine_call(b, "$RATCHET ask choice --tag b", "s-b")])
        got = cp5r13_json(m._engine_notes_path()) or {}
        lost["engine-call-sessions.json"] = (errs, not {f"{ra}|ask", f"{rb}|ask"} <= set(got))
    check("[CP5-X70] every shared record's writer, run by two workers at once with both reading before either "
          "writes (when nothing stops them): no entry is lost and nothing raises, in every per-user record and every "
          "project record more than one process updates",
          all(not e and not gone for e, gone in lost.values()),
          {k: v for k, v in lost.items() if v[0] or v[1]})

    # --- X71: structural: a shared record is only ever written through update_record --------------------------
    bad = cp5r13_shared_write_violations(src)
    check("[CP5-X71] ratchet.py writes a shared record (every record in RATCHET_HOME, and hook-heartbeat.json, "
          "provider-status.json and unavailable-reviewers.json) only inside update_record, and no helper writes a "
          "path it was handed, apart from a short allow-list", bad == [], bad[:10])
    probe = ("def register_active(root, slug):\n    p = _markers_path()\n    data, _ = _read_record(p)\n"
             "    data[str(root)] = slug\n    _write_record(p, data, pretty=True)\n"
             "def bump(root):\n    d = read_json(_stop_errors_path(), {}, record='x')\n"
             "    write_json(_stop_errors_path(), d)\n"
             "def beat(root):\n    q = root / RATCHET_DIR / 'hook-heartbeat.json'\n    q2 = q\n    _write_record(q2, {})\n"
             "def save(path, data):\n    _write_record(path, data)\n"
             "class L:\n    def enroll(self):\n        _write_record(_enrolled_path(), {})\n"
             "def fine(root):\n    update_record(_markers_path(), lambda d: d)\n    _write_record(root / 'state.json', {})\n")
    flagged = sorted({(fn, what) for fn, _l, what in cp5r13_shared_write_violations(probe)})
    check("[CP5-X71] ... and the scan really flags a hand-written read-modify-write of a shared record (through a "
          "local name, a copy of it, write_json or a method) and a helper that writes a path it is handed, and "
          "nothing that goes through update_record",
          flagged == [("L.enroll", "shared record"), ("beat", "shared record"), ("bump", "shared record"),
                      ("register_active", "shared record"), ("save", "path parameter")], flagged)
    import ast
    import re as _re
    home_names = set(_re.findall(r'ratchet_home\(\)\s*/\s*"([^"]+\.json)"', src))
    fns = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    ur = fns.get("update_record")
    calls = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in ast.walk(ur) if isinstance(c, ast.Call)} \
        if ur else set()
    check("[CP5-X71] every JSON record in RATCHET_HOME is on the scan's list of shared records, and update_record "
          "reads with _read_record and writes with _write_record inside a `with` over a _BreakerLock-based lock",
          home_names and home_names <= _CP5R13_SHARED_NAMES and ur is not None
          and any(isinstance(n, ast.With) for n in ast.walk(ur)) and {"_read_record", "_write_record"} <= calls
          and issubclass(m._RecordLock, m._BreakerLock),
          (sorted(home_names - _CP5R13_SHARED_NAMES), sorted(calls)))

    # --- X72: when the lock is held, nothing is written over it, and the person is told -----------------------
    lane = projects[3]
    lock = rh / "enrolled.lock"
    lock.write_text("12345 another-process")  # fresh: a live holder
    before = (rh / "enrolled.json").read_text()
    t0 = time.monotonic()
    # replaces cp5r12_director(home_dir, denv, lane, "director-enroll")
    e = cp2_director(m, home_dir, denv, lane, "director-enroll")
    took = time.monotonic() - t0
    # Wording pinned verbatim by tests/test_split_check.py (CP2-T01) against the pre-split tests/test_trust.py;
    # do not reword the string below. In this suite's in-process conversion, the real multi-second wait described
    # here is capped to _CP2_LOCK_WAIT_CAP=0.3s via _RecordLock, so `took` below measures the capped wait, not
    # the literal "few seconds" of the description.
    check("[CP5-X72] director-enroll while another process holds enrolled.json's lock waits a few seconds, then "
          "refuses plainly and changes nothing (enrolled.json as it was, no director.json in the lane)",
          e.returncode != 0 and "nothing was changed" in e.stderr and "enrolled.lock" in e.stderr
          and (rh / "enrolled.json").read_text() == before and not (lane / ".ratchet" / "director.json").exists()
          and lock.read_text() == "12345 another-process" and took < 60,
          (e.returncode, e.stderr[-400:], took))
    lock.unlink()
    # replaces cp5r12_director(home_dir, denv, lane, "director-enroll")
    e = cp2_director(m, home_dir, denv, lane, "director-enroll")
    enrolled = cp5r13_json(rh / "enrolled.json") or {}
    check("[CP5-X72] ... and once the lock is free it enrolls the lane and keeps every lane enrolled before",
          e.returncode == 0 and set(enrolled) == {str(d.resolve()) for d in projects}, (e.returncode, sorted(enrolled)))
    lock = rh / "active-runs.lock"
    lock.write_text("12345 another-process")
    before = (rh / "active-runs.json").read_text()
    c = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-c-"))
    with engine_env(env):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            m.register_active(c, "fc")
    check("[CP5-X72] a run that can't take active-runs.json's lock leaves the record as it is and says on stderr "
          "that it was not registered and how to register it again",
          (rh / "active-runs.json").read_text() == before and "was not registered" in err.getvalue()
          and "resume" in err.getvalue(), err.getvalue()[-400:])
    old = time.time() - 3600
    os.utime(lock, (old, old))  # left by a process that died
    with engine_env(env):
        m.register_active(c, "fc")
    got = cp5r13_json(rh / "active-runs.json") or {}
    check("[CP5-X72] ... while a lock left by a process that died (older than 30 seconds) is cleared, and the run is "
          "registered with every other run kept",
          (got.get(str(c.resolve())) or {}).get("slug") == "fc" and set(json.loads(before)) <= set(got)
          and not lock.exists(), sorted(got))


SECTIONS.append(("CP5", cp5_adv_round13_shared_record_lock))


# ---- CP5 adversarial round 14 ----
# `reviewer-status --reset` / `--mark` and `providers --add --no-check` must not claim the change was saved when
# the record's lock is held by another process: they die with a plain message instead, as `director-enroll` does.

def cp5_adv_round14_busy_update_dies():
    print("CP5 adv round 14: reviewer-status and providers --add --no-check die, not claim success, when busy")
    env = make_env("claude")
    m = cp5y_load_engine()
    # replaces d = project(env)
    d = cp2_project(m, env)
    rp = d / ".ratchet"

    # --- reviewer-status --reset while unavailable-reviewers.json's lock is held --------------------------------
    ulock = rp / "unavailable-reviewers.lock"
    ulock.write_text("12345 another-process")  # fresh: a live holder
    ufile = rp / "unavailable-reviewers.json"
    before = ufile.read_text() if ufile.exists() else None
    # replaces rx(d, env, "reviewer-status", "--reset", "codex-correctness")
    with _Cp2Call(m, d, env) as r:
        m.main(["reviewer-status", "--reset", "codex-correctness"])
    check("[CP5-X73] reviewer-status --reset while unavailable-reviewers.json's lock is held by another process "
          "dies plainly, names the lock, and does not claim codex-correctness will be tried again",
          r.returncode != 0 and "will be tried again" not in r.stdout
          and "unavailable-reviewers.lock" in r.stderr
          and (ufile.read_text() if ufile.exists() else None) == before,
          (r.returncode, r.stdout, r.stderr[-400:]))

    # --- reviewer-status --mark: same lock held, same outcome ------------------------------------------------------
    # replaces rx(d, env, "reviewer-status", "--mark", "codex-correctness", "--reason", "quota")
    with _Cp2Call(m, d, env) as r:
        m.main(["reviewer-status", "--mark", "codex-correctness", "--reason", "quota"])
    check("[CP5-X73] reviewer-status --mark while the same lock is held dies plainly and does not claim "
          "codex-correctness was skipped",
          r.returncode != 0 and "skipped until" not in r.stdout and "unavailable-reviewers.lock" in r.stderr
          and (ufile.read_text() if ufile.exists() else None) == before,
          (r.returncode, r.stdout, r.stderr[-400:]))
    ulock.unlink()
    # replaces rx(d, env, "reviewer-status", "--reset", "codex-correctness")
    with _Cp2Call(m, d, env) as r:
        m.main(["reviewer-status", "--reset", "codex-correctness"])
    check("[CP5-X73] ... and once the lock is free, --reset works and says so",
          r.returncode == 0 and "will be tried again" in r.stdout, (r.returncode, r.stdout, r.stderr[-300:]))

    # --- providers --add --no-check while provider-status.json's lock is held --------------------------------------
    plock = rp / "provider-status.lock"
    plock.write_text("12345 another-process")  # fresh: a live holder
    cfg_before = (rp / "config.json").read_text()
    # replaces rx(d, env, "providers", "--add", "cursor", "--preset", "cursor", "--company", "cursorco", "--no-check")
    with _Cp2Call(m, d, env) as r:
        m.main(["providers", "--add", "cursor", "--preset", "cursor", "--company", "cursorco", "--no-check"])
    check("[CP5-X73] providers --add --no-check while provider-status.json's lock is held dies plainly, names the "
          "lock, and does not add the provider or claim it can now review",
          r.returncode != 0 and "It can now review" not in r.stdout and "provider-status.lock" in r.stderr
          and (rp / "config.json").read_text() == cfg_before,
          (r.returncode, r.stdout, r.stderr[-400:]))
    plock.unlink()
    # replaces rx(d, env, "providers", "--add", "cursor", "--preset", "cursor", "--company", "cursorco", "--no-check")
    with _Cp2Call(m, d, env) as r:
        m.main(["providers", "--add", "cursor", "--preset", "cursor", "--company", "cursorco", "--no-check"])
    check("[CP5-X73] ... and once the lock is free, --no-check adds the provider and says so",
          r.returncode == 0 and "It can now review" in r.stdout, (r.returncode, r.stdout, r.stderr[-300:]))


SECTIONS.append(("CP5", cp5_adv_round14_busy_update_dies))


# ---- CP5 adversarial round 16 ----
# Round 14's fix covered `reviewer-status --reset/--mark` and `providers --add --no-check`, but `providers --add`
# checked (the default, no `--no-check`) still ignored probe_provider's own busy write of unavailable-reviewers.json
# and claimed the provider "can now review" anyway. It must refuse the same way. A structural scan backs this: every
# call to update_record must have its result consumed (assigned, returned or compared), never left as a bare
# statement whose "busy" outcome could be silently thrown away again.

def cp5r16_unconsumed_update_record_calls(src):
    """[line, ...] for every call to update_record left as a bare statement (its result thrown away): the
    structural half of round 16's fix, so a future caller can't repeat the mistake that let `providers --add`
    claim success after a busy write of unavailable-reviewers.json."""
    import ast
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Name) and f.id == "update_record":
                bad.append(node.lineno)
    return bad


def cp5_adv_round16_checked_add_busy_and_scan():
    print("CP5 adv round 16: providers --add (checked) dies when busy; every update_record result is consumed")
    # [CP4] update_record's own def (and any call site inside it) now lives in records.py; combine both files'
    # source so the bare-statement scan below still catches a violation wherever it is written.
    src = ENGINE.read_text(encoding="utf-8") + "\n" + RECORDS.read_text(encoding="utf-8") + "\n" + \
        PROVIDER_DISCOVERY.read_text(encoding="utf-8") + "\n" + RUN_RECORDS.read_text(encoding="utf-8")

    # --- structural: no update_record call site throws its result away ------------------------------------------
    bad = cp5r16_unconsumed_update_record_calls(src)
    check("[CP5-X75] every update_record call site's result is consumed (assigned, returned or compared), never "
          "left as a bare statement whose 'busy' outcome could be silently ignored", bad == [], bad)
    probe = ("def f():\n    update_record(p, fn)\n\n"
             "def g():\n    done = update_record(p, fn)\n    return done\n\n"
             "def h():\n    return update_record(p, fn) in ('written', 'kept')\n")
    flagged = cp5r16_unconsumed_update_record_calls(probe)
    check("[CP5-X75] ... and the scan really flags a bare update_record(...) statement, not one that's assigned, "
          "returned or compared", flagged == [2], flagged)

    # --- providers --add (checked, no --no-check) while unavailable-reviewers.json's lock is held -----------------
    env = make_env("claude")
    m = cp5y_load_engine()
    # replaces d = project(env)
    d = cp2_project(m, env)
    rp = d / ".ratchet"
    unavail = rp / "unavailable-reviewers.json"
    unavail.write_text(json.dumps({"mytool-correctness": {"reason": "test request failed: quota",
                                                           "since": "2026-01-01T00:00:00+00:00",
                                                           "until": "2099-01-01T00:00:00+00:00"}}))
    before = unavail.read_text()
    cfg_before = (rp / "config.json").read_text()
    ulock = rp / "unavailable-reviewers.lock"
    ulock.write_text("12345 another-process")  # fresh: a live holder
    # replaces rx(d, env, "providers", "--add", "mytool", "--command", '[...]', "--company", "otherco")
    with _Cp2Call(m, d, env) as r:
        m.main(["providers", "--add", "mytool", "--command", '["sh", "-c", "echo RATCHET-PROBE-OK"]',
               "--company", "otherco"])
    check("[CP5-X74] providers --add (checked) while unavailable-reviewers.json's lock is held dies plainly, names "
          "the lock, and does not claim the provider can now review",
          r.returncode != 0 and "It can now review" not in r.stdout and "unavailable-reviewers.lock" in r.stderr
          and unavail.read_text() == before and (rp / "config.json").read_text() == cfg_before,
          (r.returncode, r.stdout, r.stderr[-400:]))
    ulock.unlink()
    # replaces rx(d, env, "providers", "--add", "mytool", "--command", '[...]', "--company", "otherco")
    with _Cp2Call(m, d, env) as r:
        m.main(["providers", "--add", "mytool", "--command", '["sh", "-c", "echo RATCHET-PROBE-OK"]',
               "--company", "otherco"])
    check("[CP5-X74] ... and once the lock is free, the checked add works, says so, and clears the old benched "
          "entry",
          r.returncode == 0 and "It can now review" in r.stdout
          and "mytool-correctness" not in (json.loads(unavail.read_text()) if unavail.exists() else {}),
          (r.returncode, r.stdout, r.stderr[-300:]))


SECTIONS.append(("CP5", cp5_adv_round16_checked_add_busy_and_scan))


# ---- CP5 adversarial round 17 ----
# Round 16's fix still threw away one busy result: probe_provider left its own _update_provider_status(...) call as a
# bare statement, so a checked `providers --add` with provider-status.lock busy said "It can now review" while the
# probe result was never saved. And with unavailable-reviewers.lock busy it had already written provider-status.json
# before it refused, so "changes nothing" was false. [CP5-X75] only looked at bare update_record calls, so it passed.
# This round: the scan follows every function that hands update_record's result back (found by scanning, not a
# fixed list), a checked add takes both locks before it writes either record, and the busy refusal's retry command
# is the user's whole command, so running it as suggested works.

# A discard that is safe on purpose, by (the function it is in, the function it calls): why it may be thrown away.
# A new discard fails [CP5-X78] until it is added here, with its reason, on purpose.
_CP5R17_DISCARD_OK = {
    ("_record_chain_now", "update_record"):
        "need_lock=False, so never 'busy'; an unreadable hash record is reported on stderr by its damaged rule",
    ("_note_engine_call", "update_record"):
        "a note of an engine call: when it can't be saved the owner rules without a note apply (stricter)",
    ("_noted_session", "update_record"):
        "a read that also uses up a note: not used means the rules without a note apply (stricter)",
    ("_heartbeat", "update_record"): "liveness only: a skipped beat makes a hook look less healthy until its next run",
    ("_reset_stop_errors", "update_record"): "a counter reset: a missed reset only lets stop refuse a little longer",
    ("_stop_error", "update_record"): "a counter: when it can't be saved this refusal still counts as one more",
    ("probe_shared", "_update_health"):
        "ending the claim after the request: a busy lock only leaves the claim until it expires on its own, which is safe",
    ("cmd_providers", "probe_provider"):
        "--check/--discover only report the result, and probe_provider's own stderr note says when it wasn't saved",
}


def _cp5r17_callee(call):
    import ast
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)


def _cp5r17_own_nodes(fn):
    """Every node in fn's own body, not inside a nested def, lambda or class."""
    import ast
    out, stack = [], list(fn.body)
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(c for c in ast.iter_child_nodes(n)
                     if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)))
    return out


def _cp5r17_mentions(expr, tracked, names):
    import ast
    return any((isinstance(n, ast.Call) and _cp5r17_callee(n) in tracked)
               or (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in names) for n in ast.walk(expr))


def _cp5r17_bound(stmts):
    """Names a block sets: assigned, or changed in place through a method (busy.append(...), seen.add(...))."""
    import ast
    names = set()
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                names.add(n.id)
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                    and n.func.attr in ("append", "add", "extend", "update", "insert", "setdefault", "__setitem__")):
                names.add(n.func.value.id)
    return names


def _cp5r17_returns_result(fn, tracked):
    """True when fn hands back (all or part of) a tracked call's result: returns the call, a name assigned from it,
    or a name set under a condition on it (`if res == "busy": saved = False`), followed to a fixed point."""
    import ast
    nodes = _cp5r17_own_nodes(fn)
    names = set()
    while True:
        size = len(names)
        for n in nodes:
            if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and n.value is not None \
                    and _cp5r17_mentions(n.value, tracked, names):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    names |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
            if isinstance(n, (ast.If, ast.While)) and _cp5r17_mentions(n.test, tracked, names):
                names |= _cp5r17_bound(n.body + n.orelse)
        if len(names) == size:
            break
    return any(isinstance(n, ast.Return) and n.value is not None and _cp5r17_mentions(n.value, tracked, names)
               for n in nodes)


def cp5r17_result_wrappers(src):
    """update_record and every function that hands its result back, directly or through another such function,
    found by scanning ratchet.py (so a new wrapper is covered without editing a list)."""
    import ast
    fns = [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    tracked = {"update_record"}
    while True:
        size = len(tracked)
        tracked |= {f.name for f in fns if f.name not in tracked and _cp5r17_returns_result(f, tracked)}
        if len(tracked) == size:
            return tracked


def cp5r17_discarded_results(src, allow=None):
    """([(function, callee, line), ...], used) for every call to update_record or one of its result wrappers whose
    result is thrown away: a bare statement, a `_ = ...`, or an assignment some name of which is never read
    afterwards. A result is kept when it is returned, tested (if/while/assert), passed to another call, or assigned
    to names that are all read later in the same function. (function, callee) pairs on `allow` are deliberate
    discards; `used` is the set of allow-list pairs that were actually needed."""
    import ast
    allow = allow or {}
    tree = ast.parse(src)
    tracked = cp5r17_result_wrappers(src)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    bad, used = [], set()
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and _cp5r17_callee(call) in tracked):
            continue
        node = call
        while node in parents and not isinstance(node, (ast.stmt, ast.Lambda)):
            node = parents[node]
        if isinstance(node, ast.Lambda):
            continue  # a lambda hands its body's value back
        stmt, scope = node, parents.get(node)
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents.get(scope)
        where = scope.name if scope is not None else "<module>"
        kept = True
        if isinstance(stmt, ast.Expr):
            kept = stmt.value is not call and isinstance(parents.get(call), (ast.Call, ast.keyword))
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            tgts = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names = [x.id for t in tgts for x in ast.walk(t) if isinstance(x, ast.Name)]
            later = {x.id for x in ast.walk(scope if scope is not None else tree)
                     if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                     and (x.lineno, x.col_offset) > (stmt.end_lineno, stmt.end_col_offset)}
            kept = all(nm != "_" and nm in later for nm in names)
        if not kept:
            key = (where, _cp5r17_callee(call))
            if key in allow:
                used.add(key)
            else:
                bad.append((where, key[1], call.lineno))
    return bad, used


_CP5R17_PRE_FIX = '''
def update_record(path, fn, need_lock=True):
    return "written"

def _update_provider_status(ctx, change, note=True):
    res = update_record(p, fn)
    if res == "busy" and note:
        _note_record_busy(p, "this provider check")
    return res

def _update_unavailable(ctx, change, note=True):
    res = update_record(p, fn)
    return res

def mark_unavailable(ctx, rid, reason, note=True):
    res = _update_unavailable(ctx, lambda live: None, note=note)
    return None if res == "busy" else "until"

def probe_provider(ctx, cfg, pv, bench=True, note=True):
    good, detail = True, "ok"
    _update_provider_status(ctx, lambda data: data, note=note)
    saved = True
    if not good and bench:
        if mark_unavailable(ctx, "x", "r", note=note) is None:
            saved = False
    elif _update_unavailable(ctx, None, note=note) == "busy":
        saved = False
    return good, detail, saved

def cmd_providers(args):
    good, detail, saved = probe_provider(ctx, cfg, pv, bench=False, note=False)
    if not saved:
        die("busy: " + detail)
    print(good)

def best_effort(ctx):
    _ = _update_unavailable(ctx, None)

def never_read(ctx):
    until = mark_unavailable(ctx, "x", "r")

def read_later(ctx):
    until = mark_unavailable(ctx, "x", "r")
    return until
'''


def cp5_adv_round17_wrapper_scan_and_all_or_nothing_add():
    print("CP5 adv round 17: no wrapper of update_record has its result thrown away; checked add is all-or-nothing")
    import shlex
    # _record_chain_now (one of _CP5R17_DISCARD_OK's own allow-listed discards) moved into records.py in CP4;
    # merge it in so that allow-list entry still matches a real discard, not a stale permission.
    src = ENGINE.read_text(encoding="utf-8") + "\n" + RECORDS.read_text(encoding="utf-8") + "\n" + \
        PROVIDER_DISCOVERY.read_text(encoding="utf-8") + "\n" + RUN_RECORDS.read_text(encoding="utf-8")

    # --- structural: the wrapper scan, proven on the pre-fix shape first -----------------------------------------
    wr = cp5r17_result_wrappers(_CP5R17_PRE_FIX)
    flagged, _u = cp5r17_discarded_results(_CP5R17_PRE_FIX)
    lines = _CP5R17_PRE_FIX.splitlines()
    at = {k: next(i for i, s in enumerate(lines, 1) if s.strip().startswith(k)) for k in
          ("_update_provider_status(ctx, lambda", "_ = _update_unavailable", "until = mark_unavailable")}
    check("[CP5-X78] the wrapper scan finds every function that hands update_record's result back (through a "
          "return, a name assigned from it, or a flag set under a test of it) and flags round 16's bare "
          "_update_provider_status(...) in probe_provider, a `_ = ...` discard, and a result assigned but never read",
          {"update_record", "_update_provider_status", "_update_unavailable", "mark_unavailable",
           "probe_provider", "read_later"} <= wr and "cmd_providers" not in wr and "never_read" not in wr
          and sorted(flagged) == sorted([("probe_provider", "_update_provider_status",
                                          at["_update_provider_status(ctx, lambda"]),
                                         ("best_effort", "_update_unavailable", at["_ = _update_unavailable"]),
                                         ("never_read", "mark_unavailable", at["until = mark_unavailable"])]),
          (sorted(wr), flagged, at))
    allowed, used = cp5r17_discarded_results(_CP5R17_PRE_FIX, {("best_effort", "_update_unavailable"): "why"})
    check("[CP5-X78] ... and a discard is let through only when its (function, callee) pair is on the allow-list",
          [b for b in allowed if b[0] == "best_effort"] == [] and used == {("best_effort", "_update_unavailable")}
          and len(allowed) == 2, (allowed, used))
    real_wr = cp5r17_result_wrappers(src)
    bad, used = cp5r17_discarded_results(src, _CP5R17_DISCARD_OK)
    check("[CP5-X78] in ratchet.py, every call to update_record or to a function that hands its result back "
          "(found by scanning) has its result kept, except the commented best-effort allow-list",
          {"_update_provider_status", "_update_unavailable", "mark_unavailable", "probe_provider"} <= real_wr
          and bad == [], (bad, sorted(real_wr)))
    check("[CP5-X78] ... and every allow-list entry is still a real discard in ratchet.py (no stale permission)",
          used == set(_CP5R17_DISCARD_OK), sorted(set(_CP5R17_DISCARD_OK) - used))

    # --- checked providers --add with either lock busy: refuses and changes nothing ------------------------------
    env = make_env("claude")
    m = cp5y_load_engine()
    # replaces d = project(env)
    d = cp2_project(m, env)
    rp = d / ".ratchet"
    status, unavail, cfgp = rp / "provider-status.json", rp / "unavailable-reviewers.json", rp / "config.json"
    status.write_text(json.dumps({"mytool": {"ok": False, "at": "2026-01-01T00:00:00+00:00",
                                             "detail": "exit 1: old failure"}}, indent=2))
    unavail.write_text(json.dumps({"mytool-correctness": {"reason": "test request failed: quota",
                                                           "since": "2026-01-01T00:00:00+00:00",
                                                           "until": "2099-01-01T00:00:00+00:00"}}))
    add = ["providers", "--add", "mytool", "--command", '["sh", "-c", "echo RATCHET-PROBE-OK"]',
           "--company", "otherco", "--model", "other model 1"]

    def snap():
        return tuple(p.read_text() if p.exists() else None for p in (status, unavail, cfgp))

    def retry_of(r):
        m = re.search(r"again in a moment: `([^`]+)`", r.stderr)
        try:
            return shlex.split(m.group(1)) if m else []
        except ValueError:
            return []
    retry = []
    for lock in ("provider-status.lock", "unavailable-reviewers.lock"):
        before = snap()
        (rp / lock).write_text("12345 another-process")  # fresh: a live holder
        # replaces rx(d, env, *add)
        with _Cp2Call(m, d, env) as r:
            m.main(add)
        check(f"[CP5-X76] providers --add (checked) while {lock} is held exits non-zero, names that lock, does not "
              "claim the provider can now review, and leaves provider-status.json, unavailable-reviewers.json and "
              "config.json exactly as they were",
              r.returncode != 0 and "can now review" not in r.stdout and lock in r.stderr and snap() == before,
              (r.returncode, r.stdout, r.stderr[-400:]))
        retry = retry_of(r)
        check(f"[CP5-X77] ... and its suggested retry (with {lock} held) is the user's whole command, every flag "
              "kept (--command, --company, --model), runnable as written",
              len(retry) > len(add) and retry[-len(add):] == add and Path(retry[-len(add) - 1]).name == "ratchet.py",
              (retry, r.stderr[-400:]))
        (rp / lock).unlink()
    # CP2: kept as a REAL subprocess.run(retry, ...) on purpose -- this proves the exact string Ratchet prints as
    # its busy-retry suggestion is literally runnable, exactly as a user would copy-paste it into their shell; an
    # in-process call can't prove that (it never parses or execs the printed text at all).
    r = subprocess.run(retry, cwd=d, env=env, capture_output=True, text=True) if retry else None
    st_now = json.loads(status.read_text()) if status.exists() else {}
    check("[CP5-X77] ... running that suggested command exactly as written, once the lock is free, adds the "
          "provider, records its passing test and clears its old benched entry",
          r is not None and r.returncode == 0 and "It can now review" in r.stdout
          and (st_now.get("mytool") or {}).get("ok") is True
          and "mytool-correctness" not in (json.loads(unavail.read_text()) if unavail.exists() else {})
          and any(pv.get("id") == "mytool" and pv.get("model") == "other model 1"
                  for pv in json.loads(cfgp.read_text()).get("providers", [])),
          (r and (r.returncode, r.stdout, r.stderr[-300:])))

    # --- the reviewer status mark command: its retry keeps --reason ----------------------------------------------
    ulock = rp / "unavailable-reviewers.lock"
    ulock.write_text("12345 another-process")
    mark = ["reviewer-status", "--" + "mark", "codex-correctness", "--reason", "quota hit; back at 5"]
    # replaces rx(d, env, *mark)
    with _Cp2Call(m, d, env) as r:
        m.main(mark)
    retry = retry_of(r)
    check("[CP5-X77] reviewer-status --mark refused while busy suggests the whole command again, --reason included",
          r.returncode != 0 and retry[-len(mark):] == mark, (retry, r.stderr[-300:]))
    ulock.unlink()
    # CP2: kept as a REAL subprocess.run(retry, ...) on purpose -- this proves the printed retry string is
    # literally runnable as written, a copy-paste the user could really do; an in-process call can't prove that.
    r = subprocess.run(retry, cwd=d, env=env, capture_output=True, text=True) if retry else None
    got = json.loads(unavail.read_text()) if unavail.exists() else {}
    check("[CP5-X77] ... and running it as suggested marks the reviewer with the user's own reason",
          r is not None and r.returncode == 0 and (got.get("codex-correctness") or {}).get("reason") == mark[-1],
          (r and (r.returncode, r.stdout, r.stderr[-300:]), got))


SECTIONS.append(("CP5", cp5_adv_round17_wrapper_scan_and_all_or_nothing_add))


# ==================================================================================================================
# CP2 (speed-up checkpoint): this file's rx() subprocess call sites get converted to in-process engine calls
# (except round 13's real multi-process lock race and round 17's two literal retry-command subprocess.run calls,
# which stay real subprocesses on purpose). The conversion itself lives in the cp2_* call sites above (cp2_project,
# cp2_guard_tool, cp2_director and every `with _Cp2Call(...)` call site); these CP2-Tnn cases test it directly --
# T03 scans those call sites' comments, and T01/T02/T04/T05/T08/T09 run the converted suite for real and check its
# output. Appended after the existing SECTIONS/main() so none of round 13/14/16/17's own check() bodies above are
# touched.
#
# Convention CP2-Tnn holds the implementer to: an in-process call site that stands in for a converted rx(...) call
# is written as a direct call on the loaded engine module, `<name>.cmd_*(...)` or `<name>.main(...)`, with a
# comment in the two lines above (or on the same line) that says "replaces" and shows the rx(...) call it stands
# in for -- see cp2_uncommented_inprocess_calls()'s docstring below.
# ==================================================================================================================

import ast as _cp2_ast

_CP2_NO_RECURSE_VAR = "RATCHET_CP5E_NO_RECURSE"  # set on the child so it runs only SECTIONS, never recurses

# The pre-conversion baseline (captured solo, same command this checkpoint's test runner uses): 4 section
# announcements, 31 checks total (0 failed), 11 bracket ids with these per-id occurrence counts. Conversion must
# not change any of this -- only how fast it's produced.
BASELINE_SECTIONS = [
    "CP5 adv round 13: every read-modify-write of a shared record holds that record's lock, so none is lost",
    "CP5 adv round 14: reviewer-status and providers --add --no-check die, not claim success, when busy",
    "CP5 adv round 16: providers --add (checked) dies when busy; every update_record result is consumed",
    "CP5 adv round 17: no wrapper of update_record has its result thrown away; checked add is all-or-nothing",
]
BASELINE_TALLY = 31
BASELINE_ID_COUNTS = {
    "CP5-X68": 2, "CP5-X69": 1, "CP5-X70": 1, "CP5-X71": 3, "CP5-X72": 4, "CP5-X73": 5,
    "CP5-X74": 2, "CP5-X75": 2, "CP5-X76": 2, "CP5-X77": 5, "CP5-X78": 4,
}

CP2_PASS = CP2_FAIL = 0
CP2_STATUS: dict = {}
CP2_MESSAGES: dict = {}
_CP2_ID_RE = re.compile(r"\{(CP2-T\d+)\}")
# Built from trust_helpers.MARK_PASS/MARK_FAIL (not hand-copied glyphs) so a future change to check()'s own
# pass/fail marks can't silently desync this parsing -- see cp2_check() below, which prints with the same marks.
_CP2_RESULT_LINE_RE = re.compile(
    r"^  (%s|%s) \[(CP5-X\d+)\]" % (re.escape(trust_helpers.MARK_PASS), re.escape(trust_helpers.MARK_FAIL)), re.M)


def cp2_check(name, cond, detail=""):
    """Like trust_helpers.check(), but also files the result under its {CP2-Tnn} id for the JSON report."""
    global CP2_PASS, CP2_FAIL
    CP2_PASS, CP2_FAIL = (CP2_PASS + 1, CP2_FAIL) if cond else (CP2_PASS, CP2_FAIL + 1)
    m = _CP2_ID_RE.search(name)
    if m:
        cid = m.group(1)
        CP2_STATUS[cid] = CP2_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CP2_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {trust_helpers.MARK_PASS if cond else trust_helpers.MARK_FAIL} {name}"
          + ("" if cond else f"   {str(detail)[:400]}"))


def _cp2_id_lines(stdout):
    """{bracket_id: (pass_count, fail_count)} read from a run's captured stdout."""
    counts = {}
    for mark, cid in _CP2_RESULT_LINE_RE.findall(stdout):
        p, f = counts.get(cid, (0, 0))
        counts[cid] = (p + 1, f) if mark == trust_helpers.MARK_PASS else (p, f + 1)
    return counts


_cp2_once_cache: dict = {}


def _cp2_run_once():
    """Runs this exact file solo, the same way as the checkpoint's own command (`python3 -I
    tests/test_trust_cp5_e.py`), timed start-to-finish around the subprocess the same way CP1's baseline harness
    measures it. RATCHET_CP5E_NO_RECURSE tells the child to run only SECTIONS (today's and tomorrow's real checks)
    and skip these CP2-Tnn meta-cases -- without that guard the child would try to run them too, spawning another
    child, forever. Memoized per test process: every CP2-Tnn case that needs a real run reads the one result."""
    if "result" not in _cp2_once_cache:
        env = dict(os.environ)
        env[_CP2_NO_RECURSE_VAR] = "1"
        env.pop("RATCHET_TEST_REPORT", None)  # the child must never write over our own report file
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-I", str(HERE / "test_trust_cp5_e.py")], cwd=str(HERE.parent),
                           env=env, capture_output=True, text=True, timeout=280)
        elapsed = time.monotonic() - t0
        _cp2_once_cache["result"] = (r, elapsed)
    return _cp2_once_cache["result"]


def cp2_uncommented_inprocess_calls(src):
    """[line, ...] for every real (ast-parsed, never text inside a string literal) call to a loaded engine
    module's function directly -- `<name>.cmd_*(...)` or `<name>.main(...)`, the shape an in-process call site
    takes once a converted rx(...) subprocess call becomes an in-process one -- that has no comment in the two
    lines above it (or on its own line) saying what rx(...) call it replaces. Parsing with ast (not a text scan)
    means text that merely looks like such a call inside a string, such as _CP5R13_CHILD's own `m.main()` (which
    must run as a real child subprocess and is never converted), is never mistaken for a converted call site."""
    tree = _cp2_ast.parse(src)
    lines = src.splitlines()
    bad = []
    for node in _cp2_ast.walk(tree):
        if isinstance(node, _cp2_ast.Call) and isinstance(node.func, _cp2_ast.Attribute) \
                and (node.func.attr.startswith("cmd_") or node.func.attr == "main"):
            i = node.lineno
            window = "\n".join(lines[max(0, i - 3):i])  # the two lines above and the call's own line
            if not re.search(r"replaces\b[^\n]{0,200}\brx\(", window):
                bad.append(i)
    return bad


def t01_same_sections_and_tally():
    r, _elapsed = _cp2_run_once()
    cp2_check("{CP2-T01} running this file solo (`python3 -I tests/test_trust_cp5_e.py`) exits 0",
              r.returncode == 0, (r.returncode, r.stdout[-500:], r.stderr[-500:]))
    pos, order_ok = -1, True
    for section in BASELINE_SECTIONS:
        here = r.stdout.find(section)
        if here == -1 or here < pos:
            order_ok = False
            break
        pos = here
    cp2_check("{CP2-T01} it prints the same 4 section announcements (CP5 adv round 13, 14, 16, 17), in that order",
              order_ok, r.stdout[:3000])
    m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
    cp2_check(f"{{CP2-T01}} the final tally is '{BASELINE_TALLY} passed, 0 failed' -- the same N as the "
              "pre-conversion baseline (no check silently removed, and none newly failing)",
              bool(m) and int(m.group(1)) == BASELINE_TALLY and int(m.group(2)) == 0,
              m.group(0) if m else r.stdout[-300:])


def t02_wall_clock_under_60s():
    r, elapsed = _cp2_run_once()
    cp2_check("{CP2-T02} the re-spawned child's own inner wall-clock (a monotonic timer around that one subprocess "
              "invocation, the same way CP1's baseline harness measures it) is comfortably under 60s, down from "
              f"the ~115s solo baseline (took {elapsed:.1f}s, exit {r.returncode})",
              elapsed < 60, elapsed)
    # [CP2] The child-only number above is NOT what a user actually waits on: this very process also ran SECTIONS
    # directly before reaching here, so the REAL top-level invocation (`python3 -I tests/test_trust_cp5_e.py`,
    # what the checkpoint's "well under 120s" promise is about) is everything from this process's own start
    # (_CP2_PROCESS_START, set at the top of this file) to right now -- the same "outside timer around the one
    # real invocation" method CP1's baseline harness used, just measured from inside this process instead of a
    # second one wrapping it. By this point every CP2-Tnn case so far that needed a real run (T01) already
    # triggered and memoized the one re-spawned child, so this total already reflects the real, full cost.
    real_elapsed = time.monotonic() - _CP2_PROCESS_START
    cp2_check("{CP2-T02} the REAL top-level solo invocation's own wall-clock (measured end-to-end in this very "
              "process, from _CP2_PROCESS_START to here) is well under the 120s ceiling this checkpoint promises",
              real_elapsed < 100, real_elapsed)


def t03_converted_call_sites_name_what_they_replaced():
    good_probe = ("def f(m, d, env):\n"
                  "    # replaces rx(d, env, 'init', 'feature', '--slug', 'f')\n"
                  "    m.cmd_init(args)\n")
    bad_probe = "def f(m, d, env):\n    m.cmd_init(args)\n"
    cp2_check("{CP2-T03} the scan lets through a converted call site whose comment names the rx(...) call it "
              "replaces", cp2_uncommented_inprocess_calls(good_probe) == [],
              cp2_uncommented_inprocess_calls(good_probe))
    cp2_check("{CP2-T03} ... and flags one with no such comment",
              cp2_uncommented_inprocess_calls(bad_probe) == [2], cp2_uncommented_inprocess_calls(bad_probe))
    src = (HERE / "test_trust_cp5_e.py").read_text()
    bad = cp2_uncommented_inprocess_calls(src)
    cp2_check("{CP2-T03} every in-process engine call site in this file (every direct call to the loaded engine's "
              "cmd_*() or main(), which is what a converted rx(...) call becomes) carries a comment naming the "
              "real subprocess call it replaces", bad == [], bad)


def _cp2_callee(call):
    f = call.func
    return f.attr if isinstance(f, _cp2_ast.Attribute) else (f.id if isinstance(f, _cp2_ast.Name) else None)


def t04_round13_race_stays_real_subprocess():
    src = (HERE / "test_trust_cp5_e.py").read_text()
    tree = _cp2_ast.parse(src)
    fn = next((n for n in _cp2_ast.walk(tree) if isinstance(n, _cp2_ast.FunctionDef) and n.name == "cp5r13_race"),
              None)
    cp2_check("{CP2-T04} cp5r13_race is still defined in this file", fn is not None, "cp5r13_race not found")
    has_popen = fn is not None and any(isinstance(n, _cp2_ast.Call) and _cp2_callee(n) == "Popen"
                                      for n in _cp2_ast.walk(fn))
    cp2_check("{CP2-T04} cp5r13_race's subprocess.Popen call is still present (not replaced by an in-process "
              "stand-in)", has_popen, "")
    lines = src.splitlines()
    nearby = "\n".join(lines[max(0, (fn.lineno - 6) if fn else 0):(fn.end_lineno if fn else 0)])
    reason_ok = re.search(r"real\b[^\n]{0,160}(cross-process|one python process|in-process)", nearby, re.I) \
        is not None
    cp2_check("{CP2-T04} a comment next to cp5r13_race names the reason this boundary must stay a real "
              "multi-process race (genuine cross-process contention for a shared record's lock, which one Python "
              "process calling in-process functions cannot reproduce)", reason_ok, nearby[:600])
    r, _elapsed = _cp2_run_once()
    counts = _cp2_id_lines(r.stdout)
    for cid in ("CP5-X68", "CP5-X69", "CP5-X70"):
        passed, failed = counts.get(cid, (0, 0))
        cp2_check(f"{{CP2-T04}} {cid} still passes, driven by the real multi-process race", failed == 0 and passed > 0,
                  counts.get(cid))


def t05_round17_retry_subprocess_stays_real():
    src = (HERE / "test_trust_cp5_e.py").read_text()
    tree = _cp2_ast.parse(src)
    fn = next((n for n in _cp2_ast.walk(tree) if isinstance(n, _cp2_ast.FunctionDef)
              and n.name == "cp5_adv_round17_wrapper_scan_and_all_or_nothing_add"), None)
    cp2_check("{CP2-T05} round 17's section function is still defined in this file", fn is not None, "")
    retry_calls = [n for n in (_cp2_ast.walk(fn) if fn else [])
                   if isinstance(n, _cp2_ast.Call) and isinstance(n.func, _cp2_ast.Attribute)
                   and n.func.attr == "run" and isinstance(n.func.value, _cp2_ast.Name)
                   and n.func.value.id == "subprocess" and n.args and isinstance(n.args[0], _cp2_ast.Name)
                   and n.args[0].id == "retry"]
    cp2_check("{CP2-T05} both of round 17's subprocess.run(retry, ...) call sites are still present (not replaced "
              "by calling a command function in-process)", len(retry_calls) >= 2, len(retry_calls))
    lines = src.splitlines()
    named = sum(1 for call in retry_calls
                if re.search(r"(literally runnable|copy-paste|prove)[^\n]{0,200}(literally runnable|copy-paste|"
                             r"prove|real subprocess)",
                             "\n".join(lines[max(0, call.lineno - 4):call.lineno]), re.I))
    cp2_check("{CP2-T05} a comment next to each subprocess.run(retry, ...) names the reason it must stay a real "
              "subprocess (proving the string Ratchet prints is literally runnable, as a user would copy-paste "
              "it, which an in-process call cannot prove)",
              len(retry_calls) >= 2 and named == len(retry_calls), (named, len(retry_calls)))
    r, _elapsed = _cp2_run_once()
    counts = _cp2_id_lines(r.stdout)
    passed, failed = counts.get("CP5-X77", (0, 0))
    cp2_check("{CP2-T05} CP5-X77 still passes, by actually executing that printed string", failed == 0 and passed > 0,
              counts.get("CP5-X77"))


# [CP2 round-2 check-in] T06 used to diff ratchet.py against a pinned historical commit -- a one-time property of
# THIS checkpoint's own edits, not a lasting property of the code. Living in the PERMANENT suite, it would fail
# forever the moment any later, unrelated ratchet.py change (an ordinary feature, or lane M's modularization
# refactor) lands, since that diff would then include hunks outside any test-only seam. The before/after proof of
# "did CP2's own work touch ratchet.py outside its labeled test-only seam" is covered once at this checkpoint's
# gate by the adversarial review's own per-round diff check, never as a standing suite assertion. What T06 checks
# here, forever, needs no git history, no
# base commit, and no diff at all: ratchet.py has at most one function clearly labeled test-only (per this
# checkpoint's written convention: "a function or block clearly labeled test-only, by name or an adjacent comment
# saying so" -- checkpoints.json's CP2 entry), and no production code path in ratchet.py calls into it.
def _cp2_test_only_functions(tree, lines):
    """[FunctionDef/AsyncFunctionDef, ...] for every TOP-LEVEL (module-scope) function in ratchet.py whose name,
    adjacent comment, or docstring marks it test-only -- the shape a minimal, clearly-labeled test-only entry
    point takes. Only module-scope functions count as "entry points"; a label on a nested/inner function doesn't
    make the file itself expose one."""
    found = []
    for node in tree.body:
        if not isinstance(node, (_cp2_ast.FunctionDef, _cp2_ast.AsyncFunctionDef)):
            continue
        if re.search(r"test.?only", node.name, re.I):
            found.append(node)
            continue
        above = "\n".join(lines[max(0, node.lineno - 4):node.lineno - 1])
        if re.search(r"test.?only", above, re.I):
            found.append(node)
            continue
        doc = _cp2_ast.get_docstring(node) or ""
        if re.search(r"test.?only", doc, re.I):
            found.append(node)
    return found


def t06_test_only_seam_is_structurally_sound():
    src = (HERE.parent / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py").read_text()
    tree = _cp2_ast.parse(src)
    lines = src.splitlines()
    found = _cp2_test_only_functions(tree, lines)
    cp2_check("{CP2-T06} ratchet.py has at most one top-level function clearly labeled test-only (by name or an "
              "adjacent comment/docstring saying so)", len(found) <= 1, [n.name for n in found])
    if not found:
        return
    fn = found[0]
    cp2_check(f"{{CP2-T06}} its name ('{fn.name}') itself matches the written test-only naming convention",
              re.search(r"test.?only", fn.name, re.I) is not None, fn.name)
    callers = [node.lineno for node in _cp2_ast.walk(tree)
               if isinstance(node, _cp2_ast.Name) and node.id == fn.name
               and not (fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno))]
    cp2_check(f"{{CP2-T06}} no production code path in ratchet.py (no command dispatch, no CLI handler, no other "
              f"function body) calls '{fn.name}()' -- it has no callers anywhere else in the file",
              callers == [], callers)


def t07_loader_is_memoized_once_per_process():
    candidates = []
    for name, fn in (("cp5y_load_engine", cp5y_load_engine), ("engine", trust_helpers.engine)):
        try:
            m1 = fn()
            m2 = fn()
            candidates.append((name, m1 is m2))
        except Exception as e:
            candidates.append((name, False, repr(e)))
    cp2_check("{CP2-T07} calling trust_helpers' in-process engine loader (cp5y_load_engine or engine) twice in "
              "one process returns the identical module object both times -- proving ratchet.py's ~21k lines are "
              "compiled once per test process, not once per converted call",
              any(c[1] for c in candidates), candidates)


def t08_round13_wait_condition_unchanged():
    src = (HERE / "test_trust_cp5_e.py").read_text()
    tree = _cp2_ast.parse(src)
    fn = next((n for n in _cp2_ast.walk(tree) if isinstance(n, _cp2_ast.FunctionDef) and n.name == "cp5r13_race"),
              None)
    cp2_check("{CP2-T08} cp5r13_race is still defined in this file", fn is not None, "cp5r13_race not found")
    call = next((n for n in (_cp2_ast.walk(fn) if fn else [])
                if isinstance(n, _cp2_ast.Call) and _cp2_callee(n) == "cp5v_until"), None)
    cp2_check("{CP2-T08} cp5r13_race still waits with cp5v_until (a real polled condition), not a fixed sleep",
              call is not None, "")
    lam = call.args[0] if call and call.args else None
    is_lambda = isinstance(lam, _cp2_ast.Lambda)
    cp2_check("{CP2-T08} cp5v_until's condition is still a function, not a constant/fixed value", is_lambda, lam)
    # Structural check on the already-parsed AST node (no text rendering): ast.unparse needs Python 3.9+, which
    # breaks standards.md's 3.8+ stdlib-only requirement. ast.walk/ast.Constant/ast.JoinedStr are all 3.8-safe.
    body_nodes = list(_cp2_ast.walk(lam.body)) if is_lambda else []
    has_all_call = any(isinstance(n, _cp2_ast.Call) and isinstance(n.func, _cp2_ast.Name) and n.func.id == "all"
                        for n in body_nodes)
    has_exists_attr = any(isinstance(n, _cp2_ast.Attribute) and n.attr == "exists" for n in body_nodes)
    has_ready_literal = any(isinstance(n, _cp2_ast.JoinedStr) and any(
                                isinstance(part, _cp2_ast.Constant) and isinstance(part.value, str)
                                and "ready" in part.value
                                for part in n.values)
                            for n in body_nodes)
    condition_unchanged = is_lambda and has_all_call and has_exists_attr and has_ready_literal
    cp2_check("{CP2-T08} the condition itself still waits for every spawned job's own real ready file to exist "
              "(not a weaker check) -- only its timeout ceiling and/or poll interval may have shrunk",
              condition_unchanged, (has_all_call, has_exists_attr, has_ready_literal))
    r, _elapsed = _cp2_run_once()
    counts = _cp2_id_lines(r.stdout)
    for cid in ("CP5-X68", "CP5-X69"):
        passed, failed = counts.get(cid, (0, 0))
        cp2_check(f"{{CP2-T08}} {cid} still passes, driven by real separate processes reaching that condition",
                  failed == 0 and passed > 0, counts.get(cid))


def t09_no_check_silently_dropped():
    r, _elapsed = _cp2_run_once()
    counts = _cp2_id_lines(r.stdout)
    short = {cid: (base_n, counts.get(cid, (0, 0))) for cid, base_n in BASELINE_ID_COUNTS.items()
             if counts.get(cid, (0, 0))[0] < base_n}
    cp2_check("{CP2-T09} every one of the 11 bracket ids ([CP5-X68]..[CP5-X78]) appears in stdout at least as "
              "many times as it did before conversion -- none is missing, so a case can't be quietly weakened by "
              "deleting or relabeling its check() call while the overall pass count stays accidentally the same",
              not short, short)


TESTS = [
    ("CP2-T01", t01_same_sections_and_tally),
    ("CP2-T02", t02_wall_clock_under_60s),
    ("CP2-T03", t03_converted_call_sites_name_what_they_replaced),
    ("CP2-T04", t04_round13_race_stays_real_subprocess),
    ("CP2-T05", t05_round17_retry_subprocess_stays_real),
    ("CP2-T06", t06_test_only_seam_is_structurally_sound),
    ("CP2-T07", t07_loader_is_memoized_once_per_process),
    ("CP2-T08", t08_round13_wait_condition_unchanged),
    ("CP2-T09", t09_no_check_silently_dropped),
]


def cp2_write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CP2_STATUS.get(cid, False) else "failed",
              "message": "" if CP2_STATUS.get(cid, False) else "; ".join(CP2_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def run_cp2_cases():
    """Runs every CP2-Tnn case and writes the JSON report; returns True when any case failed."""
    for cid, fn in TESTS:
        CP2_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            CP2_STATUS[cid] = False
            CP2_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  {trust_helpers.MARK_FAIL} [{cid}] CRASHED: {e}")
            traceback.print_exc()
    cp2_write_report()
    ok = sum(1 for v in CP2_STATUS.values() if v)
    bad = sum(1 for v in CP2_STATUS.values() if not v)
    print(f"\n{ok} CP2 cases passed, {bad} CP2 cases failed")
    return bad > 0


def main():
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            trust_helpers.FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{trust_helpers.PASS} passed, {trust_helpers.FAIL} failed")
    exit_code = 1 if trust_helpers.FAIL else 0
    # CP2's own meta-cases run after the tally above (so that line's text never changes) and are skipped in the
    # child this file spawns of itself (see _cp2_run_once) so that child doesn't try to run them too, forever.
    if not os.environ.get(_CP2_NO_RECURSE_VAR):
        cp2_failed = run_cp2_cases()
        exit_code = 1 if (exit_code or cp2_failed) else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
