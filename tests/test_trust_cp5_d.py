#!/usr/bin/env python3
"""
CP5 of the trust-holes run, part d: approvals answer the question Ratchet asked.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_d.py
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

# [CP3] A monotonic mark taken as close to process start as this file can manage, so CP3-T02's real top-level
# check (now in run_cp3_cases(), AFTER every CP3-Tnn case including T11 has run -- see that function below) can
# bound the REAL top-level wall-clock of `python3 -I tests/test_trust_cp5_d.py` (what a user actually runs and
# waits on) instead of only the inner timer _cp3_run_once()/_cp3_run_fresh() put around their own re-spawned
# child. Same reasoning as test_trust_cp5_e.py's CP2 block's _CP2_PROCESS_START.
_CP3_PROCESS_START = time.monotonic()

# [CP3] Set once, at the very end of main()'s own in-process SECTIONS loop (see main() below), to this process's
# own elapsed cold-start-plus-SECTIONS cost on THIS box at THIS moment. CP3-T02's child-level budget reads this
# instead of a load-average/cpu-count stretch factor: stretch_factor() stays 1.0 for any load below the raw CPU
# count -- exactly the "two agent lanes sharing one box" contention band the objective names as real usage -- so
# it never actually scales the budget there. Measuring this same process's own same-box, same-moment cost does.
_CP3_PARENT_SECTIONS_ELAPSED = None

# `-I` (isolated mode; this file's own checkpoint command runs it that way) implies safe_path, so Python no longer
# puts this script's own directory on sys.path the way a plain `python3 tests/test_trust_cp5_d.py` does -- without
# this, `import _tmp` below fails before a single check runs. [CP3] harness fix (same one CP2 already applied to
# tests/test_trust_cp5_e.py), not a behavior change.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _tmp
import trust_helpers
from trust_helpers import (
    CP4_CODE_RE, ENGINE, HAVE_PTY, check, cp3_state, cp4_answer_correct, cp4_pty_run,
    cp4_unapproved_plan, cp5r8_sha, cp5v_until,
    cp5y_load_engine, cp5y_repo, cp5z_notes, cp5z_project_notes, engine_env, git, js, make_env,
    stop_payload,
)

# ==================================================================================================================
# CP3 (speed-up checkpoint): this file's rx() subprocess call sites (rounds 10/11/12/flaky-X61 below) get converted
# to in-process engine calls wherever the subprocess boundary is not itself under test, following the pattern
# tests/test_trust_cp5_e.py's CP2 checkpoint established (_Cp2Call / cp2_project / cp2_guard_tool / cp2_director).
# _Cp3Call is the SAME in-process stand-in, renamed for this file -- as of round 2, a thin constructor over the
# one shared implementation both suites use, trust_helpers.InProcessCall: it runs the shared, memoized engine
# module's own main(args) in-process (chdir to the project, swap os.environ for exactly the test's env, capture
# stdout/stderr/exit code, and reset a handful of the module's own per-call globals both before AND after each
# call, since the module is now reused across many calls instead of a fresh subprocess every time). See
# trust_helpers.cp5y_load_engine()'s own docstring for why this reuse is safe and the real cost it avoids
# (recompiling ratchet.py's ~21k lines every subprocess spawn -- CP1's scout finding, not subprocess/git/flock
# overhead), and trust_helpers.InProcessCall's own docstring for the full shared contract and _Cp3Call's one
# remaining difference from its CP2 twin.
#
# The cp3_* mirror functions below are hand-synced in-process copies of the trust_helpers helpers this file calls
# heavily in loops (cp4_unapproved_plan, cp5y_first_run_plan/cp5y_submit_plan, cp4x_guard_tool, cp5y_guard_bash,
# cp4_say_yes, cp5r12_director, install): trust_helpers' originals are left exactly as they were (every other
# trust-hole suite still uses them as real subprocesses). Converting the originals in place is a later checkpoint's
# job, mirroring test_trust_cp5_e.py's own note on cp2_project/cp2_guard_tool/cp2_director: "when that happens,
# these mirrors should be deleted."
# ==================================================================================================================

_CP3_RESET_GLOBALS = {"_IN_HOOK": False, "_OVERRIDE_CONFIRMED": False, "_CHAIN_REBUILD": False,
                      "_HOOK_ENV_ADVISORY": False, "_STATE_LOCK_HELD": False, "_ENGINE_ARGV": None}

# Same reasoning as test_trust_cp5_e.py's CP2 block's _CP2_LOCK_WAIT_CAP: none of this file's check()s assert a
# MINIMUM wait on a busy per-user lock, only an upper bound or a plain refusal, so capping how long _Cp3Call's own
# calls wait for one shortens the suite without weakening what any check proves.
_CP3_LOCK_WAIT_CAP = 0.3


class _Cp3Call(trust_helpers.InProcessCall):
    """`with _Cp3Call(m, d, env) as r: m.main([...])` -- the in-process stand-in for one rx(...) subprocess call in
    this file, the same shape test_trust_cp5_e.py's _Cp2Call gives its own converted call sites: `r.returncode` /
    `r.stdout` / `r.stderr` read the same way a subprocess.CompletedProcess's do.

    [Corrected, round 2] This class used to be a hand copy of its CP2 twin (test_trust_cp5_e.py's `_Cp2Call`)
    that had already drifted from it by round 2 (host-alt-standards: duplicated-test-helper, raised twice). Both
    classes are now thin constructors over the one shared implementation, trust_helpers.InProcessCall -- see
    that class's own docstring for the full contract (the lock-wait cap, the entry-and-exit global reset, and
    the KeyboardInterrupt-propagation guard all live there now, identical for both suites). The ONE remaining
    difference between this class and `_Cp2Call`: this one passes `reset_unreadable=True` -- this file's own
    X64 check needs a damaged-record warning about one path to fire again on a later call that re-damages the
    same path, which clearing `m._NOTED_UNREADABLE` on entry and exit makes possible; test_trust_cp5_e.py's CP2
    suite has no check that needs this (checked: nothing there asserts a SECOND warning about the SAME damaged
    path across two separate in-process calls), so `_Cp2Call` passes False.

    ONE DOCUMENTED EXCEPTION: X53's record_chain probe (search this file for "w.stdout.strip() == \"done\"") calls
    `m.record_chain(sp)` plus a `print("done")`, not `m.main([...])`, because record_chain is a plain module
    function with no CLI subcommand of its own to dispatch through -- the ORIGINAL site it replaces was never an
    rx(...) call either (it was a standalone `python3 -c <script>` subprocess that loaded ratchet.py fresh and
    called record_chain(state.json) directly, then printed "done"; see that site's own comment). That is the only
    call site in this file allowed to break the one-`m.main(...)`-line contract above."""

    def __init__(self, m, d, env, stdin=None):
        super().__init__(m, d, env, _CP3_RESET_GLOBALS, _CP3_LOCK_WAIT_CAP, reset_unreadable=True, stdin=stdin)


def cp3_unapproved_plan(env):
    """In-process mirror of trust_helpers.cp4_unapproved_plan (same return shape: (d, tp)). trust_helpers'
    original is left exactly as it was -- every other trust-hole suite still uses it as a real subprocess, and
    test_trust_cp5_d.py's own CP3-T08 deliberately still calls the real one too (see t08's own comment)."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp4-"))
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
                              "timestamp": trust_helpers.now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    m = cp5y_load_engine()
    first_prompt = json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                               "transcript_path": str(tp)})
    # replaces rx(d, env, "hook", "prompt", "--host", "claude", stdin=first_prompt)  (trust_helpers.cp4_unapproved_plan)
    with _Cp3Call(m, d, env, stdin=first_prompt) as r:
        m.main(["hook", "prompt", "--host", "claude"])
    # replaces rx(d, env, "init", "feature", "--slug", "f")  (trust_helpers.cp4_unapproved_plan)
    with _Cp3Call(m, d, env) as r:
        m.main(["init", "feature", "--slug", "f"])
    assert r.returncode == 0, r.stderr + r.stdout
    # replaces rx(d, env, "config", "--set", "min_review_seconds=0")  (trust_helpers.cp4_unapproved_plan)
    with _Cp3Call(m, d, env) as r:
        m.main(["config", "--set", "min_review_seconds=0"])
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    # replaces rx(d, env, "plan-submit")  (trust_helpers.cp4_unapproved_plan)
    with _Cp3Call(m, d, env) as r:
        m.main(["plan-submit"])
    assert r.returncode == 0, r.stderr + r.stdout
    return d, tp


def cp3_say_yes(d, tp, env, text="yes, build it", session="s-owner"):
    """In-process mirror of trust_helpers.cp4_say_yes."""
    m = cp5y_load_engine()
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                   "timestamp": trust_helpers.now_dt().isoformat()}) + "\n")
    payload = json.dumps({"cwd": str(d), "prompt": text, "session_id": session, "transcript_path": str(tp)})
    # replaces rx(d, env, "hook", "prompt", "--host", "claude", stdin=payload)  (trust_helpers.cp4_say_yes)
    with _Cp3Call(m, d, env, stdin=payload) as r:
        m.main(["hook", "prompt", "--host", "claude"])
    return r


def cp3_guard_tool(cwd, env, tool, tool_input, session="s"):
    """In-process mirror of trust_helpers.cp4x_guard_tool."""
    m = cp5y_load_engine()
    payload = json.dumps({"session_id": session, "cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": tool,
                          "tool_input": tool_input})
    # replaces rx(cwd, env, "hook", "guard", "--host", "claude", stdin=payload)  (trust_helpers.cp4x_guard_tool)
    with _Cp3Call(m, cwd, env, stdin=payload) as r:
        m.main(["hook", "guard", "--host", "claude"])
    return r


def cp3_guard_bash(d, env, command, session):
    """In-process mirror of trust_helpers.cp5y_guard_bash."""
    m = cp5y_load_engine()
    payload = json.dumps({"session_id": session, "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": command}})
    # replaces rx(d, env, "hook", "guard", "--host", "claude", stdin=payload)  (trust_helpers.cp5y_guard_bash)
    with _Cp3Call(m, d, env, stdin=payload) as r:
        m.main(["hook", "guard", "--host", "claude"])
    return r


def cp3_install(d, env, host="claude", scope="project"):
    """In-process mirror of trust_helpers.install."""
    m = cp5y_load_engine()
    # replaces rx(d, env, "install-hooks", "--host", host, "--scope", scope)  (trust_helpers.install)
    with _Cp3Call(m, d, env) as r:
        m.main(["install-hooks", "--host", host, "--scope", scope])
    assert r.returncode == 0, r.stderr + r.stdout
    return r


def cp3_submit_plan(d, env):
    """In-process mirror of trust_helpers.cp5y_submit_plan."""
    m = cp5y_load_engine()
    # replaces rx(d, env, "config", "--set", "min_review_seconds=0")  (trust_helpers.cp5y_submit_plan)
    with _Cp3Call(m, d, env) as r:
        m.main(["config", "--set", "min_review_seconds=0"])
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    # replaces rx(d, env, "plan-submit")  (trust_helpers.cp5y_submit_plan)
    with _Cp3Call(m, d, env) as r:
        m.main(["plan-submit"])
    assert r.returncode == 0, r.stderr + r.stdout


def cp3_first_run_plan(env):
    """In-process mirror of trust_helpers.cp5y_first_run_plan."""
    m = cp5y_load_engine()
    d = cp5y_repo()
    # replaces rx(d, env, "init", "feature", "--slug", "f")  (trust_helpers.cp5y_first_run_plan)
    with _Cp3Call(m, d, env) as r:
        m.main(["init", "feature", "--slug", "f"])
    assert r.returncode == 0, r.stderr + r.stdout
    cp3_submit_plan(d, env)
    return d, d / "t.jsonl"


def cp3_director(home_dir, denv, lane, *args):
    """In-process mirror of trust_helpers.cp5r12_director."""
    m = cp5y_load_engine()
    cp3_guard_tool(home_dir, denv, "Bash", {"command": f"python3 {ENGINE} --root {lane} {' '.join(args)}"},
                  session="s-director")
    # replaces rx(home_dir, denv, "--root", str(lane), *args)  (trust_helpers.cp5r12_director)
    with _Cp3Call(m, home_dir, denv) as r:
        m.main(["--root", str(lane), *args])
    return r


SECTIONS = []


# ---- CP5 adversarial round 10 ----
# A damaged per-user record never crashes a hook or wedges a run: an unreadable engine-call-sessions.json holds no
# note and is replaced by the next note; an unreadable director-folders.json names no director folder; an unreadable
# file-hashes.json vouches for nothing (checks refuse plainly, writes leave it alone, accept-state rebuilds it).
# The per-user short locks clear a stale lock only under a breaker file, and every write uses its own temp name.

_CP5R10_BREAKER = """
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5r10", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
role, marks = sys.argv[2], Path(sys.argv[3])
other = "fast" if role == "slow" else "slow"
lock_path = str(m._EngineNotesLock().path)
if role == "slow":  # judges the aged lock stale, then stalls right before removing it until `fast` has had its try
    real_unlink = os.unlink
    first = [True]
    def unlink(p, *a, **k):
        if first[0] and str(p) == lock_path:
            first[0] = False
            (marks / "slow-judged").write_text("1")
            end = time.monotonic() + 60
            while not ((marks / "fast-held").exists() or (marks / "fast-done").exists()) and time.monotonic() < end:
                time.sleep(0.01)
        return real_unlink(p, *a, **k)
    os.unlink = unlink
with m._EngineNotesLock() as lock:
    held = lock.held
    if held:
        (marks / (role + "-held")).write_text("1")
        end = time.monotonic() + 3
        while not (marks / (other + "-done")).exists() and time.monotonic() < end:
            time.sleep(0.01)
(marks / (role + "-done")).write_text("1")
print("held" if held else "not-held")
"""

_CP5R10_TMPNAMES = """
import importlib.util, json, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5r10t", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
root, f = Path(sys.argv[2]), Path(sys.argv[3])
names = []
real_replace = os.replace
def replace(src, dst, *a, **k):
    names.append((Path(src).name, Path(dst).name))
    return real_replace(src, dst, *a, **k)
os.replace = replace
m._note_engine_call(root, "$RATCHET ask choice --tag t1", "s-t1")
m._note_engine_call(root, "$RATCHET ask choice --tag t2", "s-t2")
m._ENGINE_ARGV = ["ask", "choice", "--tag", "t1"]
got = m._noted_session(root, "ask")
m.record_chain(f)
print(json.dumps({"pid": os.getpid(), "names": names, "got": got}))
"""


def cp5r10_hook_crashes(d):
    p = Path(d) / ".ratchet" / "hook-errors.log"
    return p.read_text() if p.exists() else ""


def cp5r10_breaker_proc(script, marks, env, role):
    # [CP3] kept as a real subprocess: X54 proves a genuine cross-process race over one stale lock file between
    # two separate OS processes acquiring/removing it, which one Python process calling in-process functions
    # cannot reproduce.
    return subprocess.Popen([sys.executable, str(script), str(ENGINE), role, str(marks)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def cp5_adv_round10_damaged_records_and_breaker():
    print("CP5 adv round 10: damaged per-user records fail safe; a stale short lock is cleared by one process only")
    m = cp5y_load_engine()
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r10-"))
    corrupt = '{"a": [1,'

    # --- X51: a corrupt engine-call-sessions.json: the guard notes over it, `ask` uses no note, nothing crashes ----
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp3_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    notes_p = rh / "engine-call-sessions.json"
    notes_p.write_text(corrupt)
    g = cp3_guard_bash(d, env, "$RATCHET ask approve-plan", "s-D")
    check("[CP5-X51] a guard hook over a corrupt engine-call-sessions.json allows the call, records no hook crash "
          "and says plainly on stderr that the file could not be read",
          g.returncode == 0 and "deny" not in g.stdout and "SystemExit" not in cp5r10_hook_crashes(d)
          and "Traceback" not in g.stderr and "engine-call-sessions.json could not be read" in g.stderr,
          (g.returncode, g.stdout[-200:], g.stderr[-300:], cp5r10_hook_crashes(d)[-300:]))
    check("[CP5-X51] ... and its note replaces the corrupt file, which is valid JSON again",
          [x.get("session") for x in (cp5z_project_notes(d, env).get(f"{d.resolve()}|ask") or [])] == ["s-D"],
          notes_p.read_text()[:300])
    # replaces rx(d, env, "ask", "approve-plan")
    with _Cp3Call(m, d, env) as r:
        m.main(["ask", "approve-plan"])
    st = cp3_state(d)
    check("[CP5-X51] the real `ask approve-plan` then uses that note: its chat owns the run and is the asker",
          r.returncode == 0 and st.get("owner_session") == "s-D" and (st.get("pending") or {}).get("session") == "s-D",
          (r.returncode, r.stderr[-300:], st.get("owner_session")))
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp3_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    notes_p = rh / "engine-call-sessions.json"
    notes_p.write_text(corrupt)
    # replaces rx(d, env, "ask", "approve-plan")
    with _Cp3Call(m, d, env) as r:
        m.main(["ask", "approve-plan"])
    st = cp3_state(d)
    check("[CP5-X51] `ask approve-plan` over a corrupt notes file exits 0 with the no-note rules (no owner, no "
          "asker), says so plainly, and never prints `is not valid JSON` or a traceback",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None
          and "engine-call-sessions.json could not be read" in r.stderr and "is not valid JSON" not in r.stderr
          and "Traceback" not in r.stderr, (r.returncode, r.stderr[-400:], st.get("owner_session")))
    g = cp3_guard_bash(d, env, "$RATCHET ask human-approve", "s-E")
    check("[CP5-X51] ... and the next note the guard takes replaces the corrupt file",
          g.returncode == 0 and isinstance(cp5z_notes(env), dict)
          and [x.get("session") for x in (cp5z_project_notes(d, env).get(f"{d.resolve()}|ask") or [])] == ["s-E"],
          (g.returncode, notes_p.read_text()[:300]))

    # --- X52: a corrupt director-folders.json grants nothing and never crashes the director's guard --------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    folder = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r10-dir-"))
    df = rh / "director-folders.json"
    df.write_text(corrupt)
    payload = {"session_id": "s-dir", "cwd": str(folder), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "ls"}}
    denv = dict(env, RATCHET_ROLE="director")
    # replaces rx(folder, dict(env, RATCHET_ROLE="director"), "hook", "guard", "--host", "claude", stdin=json.dumps(payload))
    with _Cp3Call(m, folder, denv, stdin=json.dumps(payload)) as g:
        m.main(["hook", "guard", "--host", "claude"])
    try:
        rec = json.loads(df.read_text())
    except ValueError:
        rec = None
    check("[CP5-X52] a director session's guard over a corrupt director-folders.json does not crash or report a "
          "failed save: it says so on stderr and replaces the file with a record of its own folder only",
          g.returncode == 0 and "Traceback" not in g.stderr and "could not save" not in g.stdout
          and "director-folders.json could not be read" in g.stderr
          and isinstance(rec, dict) and list(rec) == [os.path.realpath(str(folder))],
          (g.returncode, g.stdout[-300:], g.stderr[-300:], df.read_text()[:200]))

    # --- X53: a corrupt file-hashes.json vouches for nothing; a write leaves it; accept-state rebuilds it -------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp3_first_run_plan(env)
    ch = rh / "file-hashes.json"
    for bad in (corrupt, "[]"):
        ch.write_text(bad)
        # replaces rx(d, env, "ask", "approve-plan")
        with _Cp3Call(m, d, env) as r:
            m.main(["ask", "approve-plan"])
        check(f"[CP5-X53] with file-hashes.json damaged ({bad!r}), a command that reads state.json refuses with a "
              "plain message naming the hash record and accept-state, and no traceback",
              r.returncode != 0 and "record of file hashes" in r.stderr and "accept-state" in r.stderr
              and "Traceback" not in r.stderr and (cp3_state(d).get("pending") or {}) == {},
              (r.returncode, r.stderr[-400:]))
        sp = d / ".ratchet/runs/f/state.json"
        # replaces subprocess.run([sys.executable, "-c", prog, str(ENGINE), str(sp)], cwd=d, env=env, ...) -- that
        # standalone script just loaded ratchet.py fresh and called record_chain(state.json) then printed "done";
        # m.record_chain is the SAME function on the shared cached module, so this reuses it in-process instead.
        # [CP3] deliberate, documented exception to _Cp3Call's own one-`m.main(...)`-line contract (see the class's
        # own docstring, "ONE DOCUMENTED EXCEPTION"): record_chain is a plain module function, not a CLI subcommand
        # main() can dispatch to, and the call it replaces was never rx(...)/m.main(...) either -- a standalone
        # `python3 -c` script calling record_chain directly, same as this.
        with _Cp3Call(m, d, env) as w:
            m.record_chain(sp)
            print("done")
        check(f"[CP5-X53] ... and a write of a new hash ({bad!r}) does not exit and leaves the damaged record as it "
              "is (a fresh record would drop every other file's hash), saying so on stderr",
              w.returncode == 0 and w.stdout.strip() == "done" and ch.read_text() == bad
              and "file-hashes.json could not be read" in w.stderr, (w.returncode, w.stderr[-300:], ch.read_text()))
    if not HAVE_PTY:
        print("  - [CP5-X53] accept-state rebuild not run: no pty on this platform (Windows)")
    else:
        # [CP3] kept as a real pty/forkpty run: proves a genuine controlling terminal accepting the override code,
        # which no in-process call can reproduce.
        rc, out = cp4_pty_run(["accept-state"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp5_answer_prompt_code)
        try:
            rec = json.loads(ch.read_text())
        except ValueError:
            rec = None
        sp = (d / ".ratchet/runs/f/state.json").resolve()
        sha_then = cp5r8_sha(sp)
        # replaces rx(d, env, "ask", "approve-plan")
        with _Cp3Call(m, d, env) as r:
            m.main(["ask", "approve-plan"])
        check("[CP5-X53] accept-state, confirmed by a person at their terminal, rebuilds the damaged record, and "
              "commands run again",
              rc == 0 and isinstance(rec, dict) and rec.get(str(sp)) == sha_then and r.returncode == 0,
              (rc, out[-300:], ch.read_text()[:200], r.returncode, r.stderr[-300:]))

    # --- X54: two processes clearing one stale lock: only one proceeds -----------------------------------------
    script = tmp / "breaker.py"
    script.write_text(_CP5R10_BREAKER)
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    lock = rh / "engine-call-sessions.lock"
    lock.write_text("")
    aged = time.time() - 120
    os.utime(lock, (aged, aged))
    marks = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r10-m-"))
    slow = cp5r10_breaker_proc(script, marks, env, "slow")
    judged = cp5v_until(lambda: (marks / "slow-judged").exists(), timeout=60)
    fast = cp5r10_breaker_proc(script, marks, env, "fast")
    so, se = slow.communicate(timeout=120)
    fo, fe = fast.communicate(timeout=120)
    check("[CP5-X54] two processes clearing the same stale engine-call-sessions.lock: only one of them holds it "
          "(the one that judged it stale first and removed it; the other never removes that one's fresh lock)",
          judged and slow.returncode == 0 and fast.returncode == 0 and so.strip() == "held"
          and fo.strip() == "not-held", (judged, so.strip(), fo.strip(), se[-300:], fe[-300:]))
    left = sorted(x.name for x in rh.iterdir() if x.name.startswith("engine-call-sessions.lock"))
    check("[CP5-X54] ... and neither the lock nor its .break file is left behind", left == [], left)

    # --- X55: every write of these per-user records goes through a temp file of its own -----------------------
    script = tmp / "tmpnames.py"
    script.write_text(_CP5R10_TMPNAMES)
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d = cp5y_repo()
    f = d / "src/a.js"
    # [CP3] kept as a real subprocess: X55 proves each writer's temp file name is scoped to its OWN process id
    # (os.getpid()), which needs a genuinely separate process id to prove.
    r = subprocess.run([sys.executable, str(script), str(ENGINE), str(d), str(f)], cwd=d, env=env,
                       capture_output=True, text=True)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {}
    names = out.get("names") or []
    srcs = [s for s, _ in names]
    dsts = sorted({t for _, t in names})
    check("[CP5-X55] the notes and hash-record writes each use a temp file named with the process id and a random "
          "token (never the shared `.tmp` name), a different one every time",
          r.returncode == 0 and out.get("got") == "s-t1" and len(srcs) == 4 and len(set(srcs)) == 4
          and dsts == ["engine-call-sessions.json", "file-hashes.json"]
          and all(s.endswith(".tmp") and f".{out.get('pid')}-" in s for s in srcs)
          and "engine-call-sessions.json.tmp" not in srcs,
          (r.returncode, r.stderr[-300:], names))
    left = sorted(x.name for x in rh.iterdir() if x.name.endswith(".tmp"))
    check("[CP5-X55] ... and no temp file is left behind", left == [], left)


SECTIONS.append(("CP5", cp5_adv_round10_damaged_records_and_breaker))


# ---- CP5 adversarial round 11 ----
# The whole class of damaged records: every record Ratchet keeps is read through one reader that never exits
# (_read_record) and rewritten through one atomic writer (_write_record). A damaged record reads as "nothing
# recorded" (a record that grants trust grants nothing), no hook crashes on one, the repair Ratchet names
# (accept-state) runs even while a hook failure is unresolved, and the two lock kinds share one breaker protocol.

_CP5R11_CORRUPT = '{"a": [1,'

# Functions that may read JSON strictly (read_json without `record=`): files a person or an agent writes, never
# Ratchet's own records. A damaged one stops the command with a plain message naming it.
_CP5R11_STRICT_READS = {
    "read_json": "the facade itself (it reads through _read_record)",
    "Ctx.config": "config.json, the user's settings",
    "Ctx.plan": "checkpoints.json, written by the planner agent",
    "fingerprint": "config.json (fingerprint_include)",
    "changed_files": "fingerprint manifests, a cache named by its own hash",
    "integrity_problems": "config.json",
    "test_plan": "tests/CPn.json, written by the test planner",
    "_write_providers": "config.json",
    "cmd_spec": "config.json",
    "_finish_guardrail": "config.json",
    "cmd_config": "config.json",
    "spec_source": "reference/spec-source.json, in the agent-writable reference folder",
    "cmd_install_hooks": "an agent CLI's own settings file",
}
# Functions that may parse JSON themselves: input that is not a file Ratchet keeps.
_CP5R11_JSON_PARSE = {
    "_read_record": "the one reader",
    "_transcript_ok": "the agent CLI's own transcript",
    "_parse_review": "a review file's bytes, already read by the round reader",
    "_hook_input": "the hook payload on stdin",
    "normalize_hook": "a field of the hook payload",
    "cmd_config": "a value given on the command line",
    "cmd_providers": "a value given on the command line",
    "event_stream": "a line of an agent CLI's --format json output, not a record",
}
# Functions that may write files without the writer or appender: outputs and inputs that are not Ratchet's records.
_CP5R11_WRITES = {
    "_write_record": "the one atomic writer",
    "_append_record": "the one appender",
    "_BreakerLock._create": "a lock file's own token, right after its exclusive (O_CREAT|O_EXCL) create",
    "write_json": "a facade over _write_record",
    "fingerprint": "the fingerprint manifest cache",
    "_open_terminal": "the console",
    "_write_providers": "config.json",
    "cmd_init": "config.json, learnings.md and .gitignore, files people edit",
    "probe_provider": "a scratch prompt file",
    "cmd_director_key": "the director's secret key text file",
    "_run_cmd": "an evidence log",
    "_snapshot_tests": "an evidence copy of the tests",
    "_write_review_evidence": "an evidence copy of a review",
    "cmd_adv_record": "evidence findings",
    "cmd_accept_finding": "evidence findings (the round's open/deferred findings, same as cmd_adv_record)",
    "cmd_triage": "evidence findings (the round's triage decisions and the fixer's fix-list)",
    "_insert_guardrail_step": "the plan and a test plan",
    "_run_check_in": "an evidence log",
    "_finish_guardrail": "config.json",
    "cmd_spec": "the spec reference, the plan and config.json",
    "write_living_spec": "the living spec markdown",
    "_append_learning": "learnings.md",
    "cmd_config": "config.json",
    "build_viewer": "the viewer page and its data",
    "_spawn_background": "a server log",
    "cmd_install_hooks": "an agent CLI's own settings file",
    "_crash_store_unwritable": "an empty probe file, removed at once",
    "_record_hook_run": "an empty crash marker whose name is the record (exclusive create: nothing to half-write)",
}


def cp5r11_io_violations(src):
    """[(function, line, what)] for every direct JSON read, JSON parse or file write in `src` outside the reader, the
    writer, the appender and the allow-lists above."""
    import ast
    tree = ast.parse(src)
    out = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_FunctionDef(self, n):
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, n):
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        def visit_Call(self, n):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            fn = ".".join(self.stack) or "<module>"
            what = None
            if name == "read_json" and not any(k.arg == "record" for k in n.keywords):
                what = "read_json" if fn not in _CP5R11_STRICT_READS else None
            elif name in ("loads", "load") and isinstance(f, ast.Attribute) and getattr(f.value, "id", None) == "json":
                copy = n.args and isinstance(n.args[0], ast.Call) and getattr(n.args[0].func, "attr", None) == "dumps"
                what = f"json.{name}" if not copy and fn not in _CP5R11_JSON_PARSE else None
            elif name in ("open", "fdopen") and not (name == "open" and isinstance(f, ast.Attribute)
                                                     and getattr(f.value, "id", None) == "os"):  # os.open: a lock
                # open(path, mode) and os.fdopen(fd, mode) take the mode second; path.open(mode) takes it first
                margs = n.args[1:2] if isinstance(f, ast.Name) or name == "fdopen" else n.args[0:1]
                mode = next((a.value for a in margs if isinstance(a, ast.Constant)), "r")
                mode = next((k.value.value for k in n.keywords if k.arg == "mode"
                             and isinstance(k.value, ast.Constant)), mode)
                if isinstance(mode, str) and any(c in mode for c in "wax+") and fn not in _CP5R11_WRITES:
                    what = f"{name}({mode})"
            elif name in ("write_text", "write_bytes", "write_json") or (
                    name == "replace" and isinstance(f, ast.Attribute) and getattr(f.value, "id", None) == "os"):
                what = name if fn not in _CP5R11_WRITES else None
            if what:
                out.append((fn, n.lineno, what))
            self.generic_visit(n)

    V().visit(tree)
    return out


def cp5r11_project(env):
    """A plan awaiting approval in a project whose hooks are installed and firing (so they enforce)."""
    d, tp = cp3_unapproved_plan(env)
    cp3_install(d, env, "claude")
    cp5r11_prompt(d, tp, env, "hello")
    m = cp5y_load_engine()
    # replaces rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(dict(stop_payload(d), session_id="s-owner")))
    with _Cp3Call(m, d, env, stdin=json.dumps(dict(stop_payload(d), session_id="s-owner"))) as r:
        m.main(["hook", "stop", "--host", "claude"])
    cp3_guard_bash(d, env, "ls", "s-owner")
    return d, tp


def cp5r11_prompt(d, tp, env, text):
    return cp3_say_yes(d, tp, env, text=text, session="s-owner")


def cp5r11_errlog(d):
    p = Path(d) / ".ratchet" / "hook-errors.log"
    return p.read_text().splitlines() if p.exists() else []


# The code reader that types back the code from Ratchet's own prompt line only (never the first number anywhere
# in the output - a damaged record makes the engine print a warning naming its path before the prompt, and a temp
# folder name can hold a run of digits, e.g. ratchet-trust-cp4-8871g3bi) is shared with test_trust_cp4.py and
# test_trust_cp5_c.py: trust_helpers.CP4_CODE_RE/cp4_find_code/cp4_answer_correct, fixed at exactly 6 digits (the
# engine always prints exactly 6) rather than a 4-8 digit range, so the same shared cause is fixed once, not
# copied. Kept as aliases, not copies, so there is exactly one implementation.
CP5_PROMPT_CODE_RE = CP4_CODE_RE
cp5_answer_prompt_code = cp4_answer_correct


def cp5r11_accept(d, env):
    if not HAVE_PTY:
        return None, ""
    # [CP3] kept as a real pty/forkpty run: proves a genuine controlling terminal accepting the override code,
    # which no in-process call can reproduce.
    return cp4_pty_run(["accept-state"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp5_answer_prompt_code)


def cp5r11_damage(path, how):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".jsonl":
        with path.open("ab") as f:
            f.write(b"\xff\xfe not json\n" if how == _CP5R11_CORRUPT else b"[]\n")
    else:
        path.write_text(how)


def cp5_adv_round11_damaged_record_class():
    print("CP5 adv round 11: every damaged record fails safe: no hook crashes, recovery always runs, no run is stuck")
    m = cp5y_load_engine()
    src = ENGINE.read_text(encoding="utf-8")

    # --- X56: a damaged file-hashes.json never crashes the prompt hook; accept-state then repairs it -----------
    for label, crashed in (("after the real prompt hook", False), ("while an older prompt-hook crash is unresolved",
                                                                    True)):
        env = make_env("claude")
        rh = Path(env["RATCHET_HOME"])
        d, tp = cp3_first_run_plan(env)
        ch = rh / "file-hashes.json"
        ch.write_text(_CP5R11_CORRUPT)
        log = d / ".ratchet" / "human_log.jsonl"
        before = log.read_text() if log.exists() else ""
        # replaces rx(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps({"session_id": "s-A", ...}))
        with _Cp3Call(m, d, env, stdin=json.dumps({"session_id": "s-A", "cwd": str(d), "prompt": "yes"})) as r:
            m.main(["hook", "prompt", "--host", "claude"])
        check(f"[CP5-X56] ({label}) the prompt hook over a damaged file-hashes.json exits 0 without a crash: it says "
              "the message was not recorded and approves nothing, names accept-state, and records no hook failure",
              r.returncode == 0 and "Traceback" not in r.stderr and "did not record this message" in r.stdout
              and "accept-state" in r.stdout and cp5r11_errlog(d) == []
              and (log.read_text() if log.exists() else "") == before,
              (r.returncode, r.stdout[-300:], r.stderr[-300:], cp5r11_errlog(d)))
        if crashed:
            with engine_env(env):
                m._record_hook_run(d, "claude", "prompt", True)
        if not HAVE_PTY:
            print(f"  - [CP5-X56] ({label}) accept-state through a terminal not run: no pty on this platform")
            continue
        rc, out = cp5r11_accept(d, env)  # kept real: see cp5r11_accept's own comment (a real pty/forkpty run)
        try:
            rec = json.loads(ch.read_text())
        except ValueError:
            rec = None
        sp = (d / ".ratchet/runs/f/state.json").resolve()
        check(f"[CP5-X56] ({label}) accept-state, confirmed at a real terminal, runs (rc 0) and rebuilds the record "
              "with this run's files",
              rc == 0 and isinstance(rec, dict) and rec.get(str(sp)) == cp5r8_sha(sp)
              and rec.get(str(log.resolve())) == cp5r8_sha(log), (rc, out[-500:], ch.read_text()[:200]))
        check(f"[CP5-X56] ({label}) accept-state never tells the person running it that the record waits until the "
              "user runs accept-state",
              "until the user runs accept-state" not in out and "accept-state replaces it with a new record" in out,
              out[-500:])
        cp5r11_prompt(d, tp, env, "please carry on")
        # replaces rx(d, env, "ask", "approve-plan")
        with _Cp3Call(m, d, env) as r:
            m.main(["ask", "approve-plan"])
        check(f"[CP5-X56] ({label}) after a fresh message, `ask approve-plan` works again",
              r.returncode == 0 and "ASK THE USER" in r.stdout and "Refused" not in r.stderr,
              (r.returncode, r.stderr[-400:]))

    # --- X57: damaged per-user stop-hook records: the stop hook never crashes and no command is refused ---------
    for name in ("stop-errors.json", "active-runs.json"):
        for bad in (_CP5R11_CORRUPT, "[]"):
            env = make_env("claude")
            rh = Path(env["RATCHET_HOME"])
            d, tp = cp3_first_run_plan(env)
            rh.mkdir(parents=True, exist_ok=True)
            (rh / name).write_text(bad)
            # replaces s = rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(d)))
            with _Cp3Call(m, d, env, stdin=json.dumps(stop_payload(d))) as s:
                m.main(["hook", "stop", "--host", "claude"])
            # replaces s2 = rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(d)))
            with _Cp3Call(m, d, env, stdin=json.dumps(stop_payload(d))) as s2:
                m.main(["hook", "stop", "--host", "claude"])
            # replaces r = rx(d, env, "ask", "approve-plan")
            with _Cp3Call(m, d, env) as r:
                m.main(["ask", "approve-plan"])
            check(f"[CP5-X57] a damaged {name} ({bad!r}): the real stop hook records no crash and says so plainly, "
                  "and `ask approve-plan` is not refused",
                  s.returncode in (0, 2) and s2.returncode in (0, 2) and cp5r11_errlog(d) == []
                  and "Traceback" not in s.stderr + s2.stderr and f"{name} could not be read" in s.stderr
                  and r.returncode == 0 and "until that hook runs cleanly" not in r.stderr,
                  (s.returncode, s.stderr[-300:], cp5r11_errlog(d), r.returncode, r.stderr[-300:]))
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp3_first_run_plan(env)
    with engine_env(env):
        for _ in range(2):
            m._stop_error(d, "test")
    (rh / "stop-errors.json").write_text("[]")
    with engine_env(env):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            blocked, _msg = m._stop_error(d, "test")
            m._reset_stop_errors(d)
    try:
        after = json.loads((rh / "stop-errors.json").read_text())
    except ValueError:
        after = None
    check("[CP5-X57] a damaged stop-errors.json counts as no count (the stop is still refused once) and is replaced "
          "with a valid record",
          blocked is True and isinstance(after, dict) and str(d.resolve()) not in after, (blocked, after))

    # --- X58: a director-folders.json that is valid JSON but not an object is replaced, never a failed save ------
    for bad in ("[]", "null", "1"):
        env = make_env("claude")
        rh = Path(env["RATCHET_HOME"])
        rh.mkdir(parents=True, exist_ok=True)
        folder = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r11-dir-"))
        df = rh / "director-folders.json"
        df.write_text(bad)
        payload = {"session_id": "s-dir", "cwd": str(folder), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                   "tool_input": {"command": "ls"}}
        denv = dict(env, RATCHET_ROLE="director")
        # replaces rx(folder, dict(env, RATCHET_ROLE="director"), "hook", "guard", "--host", "claude", stdin=json.dumps(payload))
        with _Cp3Call(m, folder, denv, stdin=json.dumps(payload)) as g:
            m.main(["hook", "guard", "--host", "claude"])
        try:
            rec = json.loads(df.read_text())
        except ValueError:
            rec = None
        left = sorted(x.name for x in rh.iterdir() if x.name.endswith(".tmp"))
        check(f"[CP5-X58] a director's guard over a director-folders.json holding {bad} replaces it with its own "
              "folder: no failed save is reported, it says so on stderr, and no temp file is left",
              g.returncode == 0 and "could not save" not in g.stdout + g.stderr and "Traceback" not in g.stderr
              and "director-folders.json could not be read" in g.stderr and isinstance(rec, dict)
              and list(rec) == [os.path.realpath(str(folder))] and left == [],
              (g.returncode, g.stdout[-300:], g.stderr[-300:], df.read_text()[:200], left))

    # --- X59: one breaker protocol for both lock kinds --------------------------------------------------------
    shared = ("_create", "_take_breaker", "_drop_breaker", "_clear_stale", "_release_own", "_is_stale", "breaker")
    check("[CP5-X59] the state lock and the per-user locks share one breaker protocol (_BreakerLock): neither "
          "defines its own copy",
          issubclass(m._StateLock, m._BreakerLock) and issubclass(m._ChainLock, m._BreakerLock)
          and issubclass(m._EngineNotesLock, m._BreakerLock)
          and not any(k in vars(c) for c in (m._StateLock, m._ChainLock, m._EngineNotesLock) for k in shared),
          [(c.__name__, [k for k in shared if k in vars(c)]) for c in (m._StateLock, m._ChainLock)])
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r11-"))
    (root / ".ratchet").mkdir()
    lockf, brk = root / ".ratchet" / "state.lock", root / ".ratchet" / "state.lock.break"
    old = time.time() - 3600
    lockf.write_text("12345 crashed")
    brk.write_text("")
    os.utime(lockf, (old, old))
    os.utime(brk, (time.time() - 120, time.time() - 120))
    lk = m._StateLock(root, wait=2)
    why = lk.acquire()
    held = lockf.exists() and lockf.read_text() == lk.token
    lk.release()
    check("[CP5-X59] a state.lock.break left by a process that died (older than 30 seconds) no longer blocks the "
          "state lock: a stale lock is cleared and taken, as the per-user locks already did",
          why is None and held and not lockf.exists() and not brk.exists(), (why, held))
    lockf.write_text("12345 crashed")
    brk.write_text("")
    os.utime(lockf, (old, old))
    why = m._StateLock(root, wait=0.3).acquire()
    check("[CP5-X59] ... while a fresh breaker file still keeps a stale lock from being cleared (it is judged only "
          "under the breaker)", why is not None and lockf.read_text() == "12345 crashed" and brk.exists(), why)
    brk.unlink()
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    (rh / "file-hashes.lock").mkdir()
    os.utime(rh / "file-hashes.lock", (old, old))
    with engine_env(env):
        t0 = time.monotonic()
        with m._ChainLock(wait=0.3) as cl:
            got = cl.held
        took = time.monotonic() - t0
    check("[CP5-X59] the per-user lock, like the state lock, never judges a folder in its place stale or removes it",
          got is False and (rh / "file-hashes.lock").is_dir() and took < 5, (got, took))

    # --- X60: structural: no record is read or written around the one reader and the one writer ----------------
    # fingerprint/changed_files live in fingerprint.py since the modularize-engine run's CP3; merge its source
    # into the scan's input text so this check keeps its actual invariant -- each still reads/writes only the
    # manifest cache the allow-lists above already name for them -- regardless of which file currently holds
    # them. Mirrors the engine_utils.py merge done for the facade check just below.
    fp_src = ENGINE.with_name("fingerprint.py").read_text(encoding="utf-8")
    # _read_record/_write_record/_append_record/update_record and their locks live in records.py since the
    # modularize-engine run's CP4; merge it in too, for the same reason fp_src is merged in above.
    rec_src = ENGINE.with_name("records.py").read_text(encoding="utf-8")
    # probe_provider, event_stream and the rest of provider discovery/quota live in provider_discovery.py since
    # the modularize-engine run's CP5; merge it in too, for the same reason fp_src and rec_src are merged in above.
    pd_src = ENGINE.with_name("provider_discovery.py").read_text(encoding="utf-8")
    bad = cp5r11_io_violations(src + "\n" + fp_src + "\n" + rec_src + "\n" + pd_src)
    check("[CP5-X60] ratchet.py reads JSON files only through _read_record (read_json without `record=` only for "
          "files people or agents write) and writes files only through _write_record / _append_record, apart from "
          "a short allow-list of files that are not Ratchet's records",
          bad == [], bad[:10])
    # mutation-proof: a disguised unguarded write planted inside changed_files (allow-listed only for the read it
    # already does, not for a write) in a throwaway copy of fingerprint.py's text must still be caught -- proving
    # the merge above really walks into fingerprint.py's function bodies, not just ratchet.py's.
    _marker = '    b = read_json(mdir / f"{fp_b[\'id\']}.json", {}) or {}\n'
    assert _marker in fp_src, "fingerprint.py's changed_files no longer matches the expected source; update the probe"
    fp_src_planted = fp_src.replace(
        _marker,
        _marker + '    (mdir / "planted.json").write_text(json.dumps(b))  # [CP5-X60] planted mutation\n')
    planted_flagged = {(fn, what) for fn, _l, what in cp5r11_io_violations(fp_src_planted)}
    check("[CP5-X60] ... and the merged scan really walks into fingerprint.py's own functions: a disguised "
          "unguarded write planted inside changed_files is still flagged",
          ("changed_files", "write_text") in planted_flagged, planted_flagged)
    probe = ("def _stop_error(root):\n    data = read_json(p, {})\n    write_json(p, data)\n"
             "def record_director_folder(f):\n    with open(tmp, 'w') as fh:\n        fh.write('x')\n"
             "    os.replace(tmp, p)\n    json.loads(p.read_text())\n"
             "def fine():\n    read_json(p, {}, record='x')\n    json.loads(json.dumps({}))\n    open(p)\n")
    flagged = sorted({(fn, what) for fn, _l, what in cp5r11_io_violations(probe)})
    check("[CP5-X60] ... and the scan really flags a direct read, a hand-written write and a hand-written temp-file "
          "rename on a record (and nothing in a function that uses the reader)",
          flagged == [("_stop_error", "read_json"), ("_stop_error", "write_json"),
                      ("record_director_folder", "json.loads"), ("record_director_folder", "open(w)"),
                      ("record_director_folder", "replace")], flagged)
    import ast
    # read_json/write_json live in engine_utils.py since the modularize-engine run's CP2 (_read_record/
    # _write_record stay in ratchet.py, `src`, until CP4's records.py); merge both files' top-level defs so this
    # check keeps its actual invariant -- each is still a thin facade over the one reader/writer -- regardless of
    # which file currently holds which name.
    eu_src = ENGINE.with_name("engine_utils.py").read_text(encoding="utf-8")
    fns = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    fns.update({n.name: n for n in ast.walk(ast.parse(eu_src)) if isinstance(n, ast.FunctionDef)})
    rr = fns.get("_read_record")
    calls = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in ast.walk(rr) if isinstance(c, ast.Call)} \
        if rr else set()
    raises = [n for n in ast.walk(rr) if isinstance(n, ast.Raise)] if rr else [1]
    uses = lambda name, callee: any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == callee  # noqa: E731
                                    for c in ast.walk(fns[name])) if name in fns else False
    check("[CP5-X60] the reader never exits or raises, and read_json / write_json are only facades over the reader "
          "and the writer",
          rr is not None and not ({"die", "exit"} & calls) and raises == [] and uses("read_json", "_read_record")
          and uses("write_json", "_write_record"), (sorted(calls), len(raises)))
    with engine_env(make_env("claude")):
        tmpd = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r11-rr-"))
        cases = {"corrupt": _CP5R11_CORRUPT, "list": "[]", "null": "null", "bytes": None, "folder": None}
        got = {}
        for k, v in cases.items():
            p = tmpd / f"{k}.json"
            if k == "bytes":
                p.write_bytes(b"\xff\xfe\x00")
            elif k == "folder":
                p.mkdir()
            else:
                p.write_text(v)
            try:
                got[k] = m._read_record(p)
            except BaseException as e:  # noqa: BLE001 - the point: it must not raise or exit at all
                got[k] = ("raised", type(e).__name__)
        got["missing"] = m._read_record(tmpd / "none.json")
    check("[CP5-X60] _read_record reads a corrupt, non-object, non-UTF-8 or folder record as (None, why), and a "
          "missing one as an empty record, without raising or exiting",
          all(v[0] is None and isinstance(v[1], str) for k, v in got.items() if k != "missing")
          and got["missing"] == ({}, None), got)

    # --- X61: corrupt each record in turn; the hooks, status, next, doctor and accept-state never crash -----------
    home_records = ["file-hashes.json", "engine-call-sessions.json", "director-folders.json", "active-runs.json",
                    "stop-errors.json", "enrolled.json"]
    project_records = ["active.json", "hook-heartbeat.json", "hook-env.json", "director.json", "provider-status.json",
                       "unavailable-reviewers.json", "viewer.json", "human_log.jsonl", "runs/f/state.json",
                       "runs/f/prompt-chats.jsonl", "runs/f/stop-blocks.jsonl"]
    targets = [("home", n) for n in home_records] + [("project", n) for n in project_records]
    for where, name in targets:
        for bad in (_CP5R11_CORRUPT, "[]"):
            env = make_env("claude")
            d, tp = cp5r11_project(env)
            path = (Path(env["RATCHET_HOME"]) if where == "home" else d / ".ratchet") / name
            errs_before = cp5r11_errlog(d)
            cp5r11_damage(path, bad)
            outs = []
            p1 = cp5r11_prompt(d, tp, env, "hello")
            # replaces p2 = rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(dict(stop_payload(d), session_id="s-owner")))
            with _Cp3Call(m, d, env, stdin=json.dumps(dict(stop_payload(d), session_id="s-owner"))) as p2:
                m.main(["hook", "stop", "--host", "claude"])
            p3 = cp3_guard_bash(d, env, "ls", "s-owner")
            hooks_ok = all(h.returncode in (0, 2) for h in (p1, p2, p3))
            for args in (["status"], ["next"], ["doctor"]):
                # replaces r = rx(d, env, *args)
                with _Cp3Call(m, d, env) as r:
                    m.main(list(args))
                outs.append((args[0], r.returncode, r.stdout + r.stderr))
            outs.extend((h, x.returncode, x.stdout + x.stderr) for h, x in (("prompt", p1), ("stop", p2),
                                                                             ("guard", p3)))
            rc, acc = cp5r11_accept(d, env)
            crashed = [o[0] for o in outs if "Traceback" in o[2]] + (["accept-state"] if "Traceback" in acc else [])
            new_errs = cp5r11_errlog(d)[len(errs_before):]
            if name == "runs/f/state.json":  # the stop hook logs (never as a crash) that it could not check the run
                new_errs = [e for e in new_errs if " stop: StateUnreadable: " not in e]
            with engine_env(env):
                unresolved = m.unresolved_hook_crashes(d, "unknown")
            check(f"[CP5-X61] {where} {name} damaged ({bad!r}): the prompt, stop and guard hooks, status, next, "
                  "doctor and accept-state never crash, and no hook failure is recorded (a damaged state.json is "
                  "logged by the stop hook, but never as a crash)",
                  hooks_ok and crashed == [] and new_errs == [] and unresolved == [] and dict(
                      (o[0], o[1]) for o in outs).get("doctor") == 0,
                  (crashed, new_errs, unresolved, [(o[0], o[1], o[2][-200:]) for o in outs]))
            if rc is None:
                print(f"  - [CP5-X61] {name}: accept-state through a terminal not run: no pty on this platform")
                continue
            if name == "runs/f/state.json":
                check(f"[CP5-X61] {name} damaged ({bad!r}): the stop hook logs that it could not read the run state, "
                      "and accept-state refuses plainly (a damaged state is no baseline) and names the way out",
                      rc == 1 and "can't be accepted" in acc and "init --force" in acc
                      and any(" stop: StateUnreadable: " in e for e in cp5r11_errlog(d)), (rc, acc[-400:]))
                # [CP3] kept as a real pty/forkpty run: proves a genuine controlling terminal accepting the
                # override code, which no in-process call can reproduce.
                rc2, out2 = cp4_pty_run(["init", "feature again", "--slug", "f2", "--force"], d,
                                        dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp5_answer_prompt_code)
                # replaces s = rx(d, env, "status", "--json")
                with _Cp3Call(m, d, env) as s:
                    m.main(["status", "--json"])
                check(f"[CP5-X61] {name} damaged ({bad!r}): the run is not stuck: `init --force`, confirmed at a "
                      "terminal, starts the feature over and status works",
                      rc2 == 0 and s.returncode == 0 and (js(s) or {}).get("slug") == "f2",
                      (rc2, out2[-400:], s.returncode, s.stderr[-300:]))
                continue
            cp5r11_prompt(d, tp, env, "please carry on")
            # replaces r = rx(d, env, "ask", "approve-plan")
            with _Cp3Call(m, d, env) as r:
                m.main(["ask", "approve-plan"])
            # replaces s = rx(d, env, "status", "--json")
            with _Cp3Call(m, d, env) as s:
                m.main(["status", "--json"])
            if name == "director.json":
                check(f"[CP5-X61] {name} damaged ({bad!r}): accept-state runs, and the damaged enrollment still "
                      "refuses chat approvals (the lane stays a director lane)",
                      rc == 0 and s.returncode == 0 and (js(s) or {}).get("approval_mode") == "director",
                      (rc, acc[-300:], s.returncode, s.stdout[-300:], s.stderr[-300:]))
                continue
            check(f"[CP5-X61] {name} damaged ({bad!r}): accept-state runs (rc 0), and after a fresh message the run "
                  "is not stuck: `ask approve-plan` and status work",
                  rc == 0 and r.returncode == 0 and "ASK THE USER" in r.stdout and s.returncode == 0,
                  (rc, acc[-300:], r.returncode, r.stderr[-300:], s.returncode, s.stderr[-300:]))
    env = make_env("claude")
    d, tp = cp5r11_project(env)
    (d / ".ratchet" / "active.json").write_text(_CP5R11_CORRUPT)
    # replaces s = rx(d, env, "status", "--json")
    with _Cp3Call(m, d, env) as s:
        m.main(["status", "--json"])
    rc, acc = cp5r11_accept(d, env)
    try:
        act = json.loads((d / ".ratchet" / "active.json").read_text())
    except ValueError:
        act = None
    check("[CP5-X61] a damaged active.json names the run registered in the per-user folder (status works and says so "
          "on stderr), and accept-state writes it again",
          s.returncode == 0 and (js(s) or {}).get("slug") == "f" and "active.json could not be read" in s.stderr
          and (rc is None or (rc == 0 and act == {"slug": "f"})), (s.returncode, s.stderr[-300:], rc, act))


SECTIONS.append(("CP5", cp5_adv_round11_damaged_record_class))


# ---- CP5 adversarial round 12 ----
# A damaged per-user record that holds a restriction (enrolled.json, active-runs.json) is never written over, and a
# damaged enrolled.json keeps restricting; a stale breaker is removed only when it is the file judged stale, so two
# waiters can't both hold it; accept-state refuses a damaged state.json before it asks for the code.



class _Cp5r12OsProxy:
    """The engine module's `os`, with a hook called after every lstat(), so a test can interleave two threads."""

    def __init__(self, real, hook):
        self._real, self._hook = real, hook

    def __getattr__(self, name):
        return getattr(self._real, name)

    def lstat(self, path, *a, **k):
        st = self._real.lstat(path, *a, **k)
        self._hook(path)
        return st


def cp5_adv_round12_restrictions_breaker_and_order():
    print("CP5 adv round 12: restriction records are never written over, one stale-breaker winner, accept-state order")
    import threading
    m = cp5y_load_engine()
    # CP3-T10 isolates and re-executes JUST this one assignment (ENGINE monkeypatched to a scratch file with no
    # records.py sibling) to prove it reads live; it must keep reading ENGINE alone, so X65's own need for
    # records.py's text (_BreakerLock/_remove_stale_breaker moved there in CP4) is merged separately, below.
    src = ENGINE.read_text(encoding="utf-8")
    # _BreakerLock (and _remove_stale_breaker, X65 below) lives in records.py since the modularize-engine run's
    # CP4; merge it in (as its own variable, not `src` above) so X65's text check still sees it.
    src_with_records = src + "\n" + ENGINE.with_name("records.py").read_text(encoding="utf-8")

    # --- X63: a damaged enrolled.json is never replaced by a later enrollment, and keeps restricting ------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    lanes = []
    for _ in range(3):
        d, tp = cp3_unapproved_plan(env)
        cp3_guard_tool(d, env, "Bash", {"command": "ls"})
        lanes.append((d, tp))
    (a, tpa), (b, _tpb), (c, _tpc) = lanes
    home_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r12-director-"))
    denv = dict(env, RATCHET_ROLE="director")
    k = cp3_director(home_dir, denv, a, "director-key")
    ea = cp3_director(home_dir, denv, a, "director-enroll")
    eb = cp3_director(home_dir, denv, b, "director-enroll")
    enrolled = rh / "enrolled.json"
    try:
        names = set(json.loads(enrolled.read_text()))
    except (OSError, ValueError):
        names = set()
    setup = (k.returncode == 0 and ea.returncode == 0 and eb.returncode == 0
             and names == {str(a.resolve()), str(b.resolve())})
    enrolled.write_text(_CP5R11_CORRUPT)
    ec = cp3_director(home_dir, denv, c, "director-enroll")
    check("[CP5-X63] director-enroll over a damaged enrolled.json refuses and changes nothing (the record stays as it "
          "is and the new lane is not enrolled), naming the file and how to repair it",
          setup and ec.returncode != 0 and enrolled.read_text() == _CP5R11_CORRUPT
          and not (c / ".ratchet" / "director.json").exists() and "enrolled.json" in ec.stderr
          and "won't write" in ec.stderr and "director-enroll again" in ec.stderr and "Traceback" not in ec.stderr,
          (setup, k.stderr[-200:], ea.stderr[-200:], ec.returncode, ec.stderr[-400:]))
    (a / ".ratchet" / "director.json").unlink()
    # replaces s = rx(a, env, "status", "--json")
    with _Cp3Call(m, a, env) as s:
        m.main(["status", "--json"])
    check("[CP5-X63] a lane whose director.json is then removed stays in director mode: the damaged backstop keeps "
          "its restriction while a director key exists, and says so on stderr",
          (js(s) or {}).get("approval_mode") == "director" and "enrolled.json could not be read" in s.stderr,
          (s.returncode, s.stdout[-300:], s.stderr[-300:]))
    # replaces rx(a, env, "ask", "approve-plan")
    with _Cp3Call(m, a, env):
        m.main(["ask", "approve-plan"])
    cp3_say_yes(a, tpa, env, text="yes, go ahead")
    # replaces r = rx(a, env, "approve-plan")
    with _Cp3Call(m, a, env) as r:
        m.main(["approve-plan"])
    check("[CP5-X63] ... so a chat yes in that lane approves nothing", r.returncode != 0
          and cp3_state(a)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr[-300:]))
    # replaces dr = rx(home_dir, env, "doctor")
    with _Cp3Call(m, home_dir, env) as dr:
        m.main(["doctor"])
    check("[CP5-X63] doctor names the damaged enrolled.json and how to repair it",
          "enrolled.json can't be read" in dr.stdout and "director-enroll again" in dr.stdout, dr.stdout[-600:])
    enrolled.rename(enrolled.with_name("enrolled.json.damaged"))
    ec2 = cp3_director(home_dir, denv, c, "director-enroll")
    try:
        names = set(json.loads(enrolled.read_text()))
    except (OSError, ValueError):
        names = set()
    check("[CP5-X63] the repair it names works: once the damaged file is moved aside, director-enroll writes it again",
          ec2.returncode == 0 and str(c.resolve()) in names, (ec2.returncode, ec2.stderr[-300:], names))

    # --- X64: a damaged active-runs.json is never replaced by another project's run -----------------------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    p1, _tp1 = cp3_unapproved_plan(env)
    markers = rh / "active-runs.json"
    try:
        had = str(p1.resolve()) in json.loads(markers.read_text())
    except (OSError, ValueError):
        had = False
    markers.write_text(_CP5R11_CORRUPT)
    p2, _tp2 = cp3_unapproved_plan(env)
    with engine_env(env):
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            m.register_active(p2, "f")
            m.register_active(p2, None)
    # replaces dr = rx(p2, env, "doctor")
    with _Cp3Call(m, p2, env) as dr:
        m.main(["doctor"])
    check("[CP5-X64] a damaged active-runs.json is left as it is when another project's run starts or ends (it holds "
          "other projects' runs), and stderr and doctor say how to repair it",
          had and markers.read_text() == _CP5R11_CORRUPT and (p2 / ".ratchet" / "runs" / "f" / "state.json").exists()
          and "did not write over it" in err.getvalue() and "To repair it" in err.getvalue()
          and "active-runs.json can't be read" in dr.stdout and dr.returncode == 0,
          (had, markers.read_text()[:80], err.getvalue()[-400:], dr.stdout[-400:]))
    shutil.rmtree(p1 / ".ratchet")
    # replaces st = rx(p1, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(p1)))
    with _Cp3Call(m, p1, env, stdin=json.dumps(stop_payload(p1))) as st:
        m.main(["hook", "stop", "--host", "claude"])
    check("[CP5-X64] the stop hook still never crashes on it", st.returncode in (0, 2)
          and "Traceback" not in st.stderr, (st.returncode, st.stderr[-300:]))

    # --- X65: two waiters behind one stale breaker: only one holds it -------------------------------------------
    class Lk(m._BreakerLock):
        def _stale_after(self):
            return 30

    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r12-brk-"))
    lockf = root / "x.lock"
    p1l, p2l = Lk(lockf, "tok-P1"), Lk(lockf, "tok-P2")
    brk = p1l.breaker
    brk.write_text("dead")
    old = time.time() - 120
    os.utime(brk, (old, old))
    seen, barrier, p1_done = set(), threading.Barrier(2), threading.Event()

    def hook(path):
        name = threading.current_thread().name
        if str(path) != str(brk) or name not in ("P1", "P2") or name in seen:
            return
        seen.add(name)
        try:
            barrier.wait(5)  # both judged the old breaker stale
        except threading.BrokenBarrierError:
            pass
        if name == "P2":
            p1_done.wait(5)  # P2 acts on its old judgement only after P1 holds a fresh breaker

    got = {}

    def run(name, lk, wait):
        try:
            got[name] = lk._take_breaker(wait)
        except OSError as e:
            got[name] = e
        if name == "P1":
            p1_done.set()

    # _BreakerLock._take_breaker's own `os.lstat` resolves against whichever module it was DEFINED in (records.py
    # since CP4's split, not ratchet.py), regardless of which module's attribute you reach the class through --
    # so the proxy has to replace THAT module's `os`, or the hook below is never called at all.
    os_owner = sys.modules.get("records", m)
    real_os = os_owner.os
    os_owner.os = _Cp5r12OsProxy(real_os, hook)
    try:
        t1 = threading.Thread(target=run, args=("P1", p1l, 2.0), name="P1")
        t2 = threading.Thread(target=run, args=("P2", p2l, 0.5), name="P2")
        t1.start()
        t2.start()
        t1.join(15)
        t2.join(15)
    finally:
        os_owner.os = real_os
    content = brk.read_text() if brk.exists() else None
    check("[CP5-X65] two waiters behind one stale breaker, both judging it stale before either acts: only one holds "
          "the breaker, and the fresh one stays in place",
          sorted(v is True for v in got.values()) == [False, True] and got.get("P1") is True
          and content == "tok-P1" and p1l._holds_breaker() and not p2l._holds_breaker(),
          (got, content))
    leftovers = [x.name for x in root.iterdir() if x.name.endswith(".stale")]
    check("[CP5-X65] no renamed breaker file is left behind", leftovers == [], leftovers)
    p1l._drop_breaker()
    check("[CP5-X65] the holder drops its own breaker", not brk.exists())
    p1l._take_breaker(0)
    brk.unlink()  # moved away by a waiter, and another process took a new one
    p2l._take_breaker(0)
    p1l._drop_breaker()
    check("[CP5-X65] a holder whose breaker was moved away never drops the breaker another process holds now",
          brk.exists() and brk.read_text() == "tok-P2" and p2l._holds_breaker(), brk.exists())
    p2l._drop_breaker()
    check("[CP5-X65] the stale-breaker removal checks identity (inode and modification time) after a rename, never "
          "unlinking the breaker by path",
          "def _remove_stale_breaker" in src_with_records
          and "os.unlink(self.breaker)\n                        continue" not in src_with_records)

    # --- X66: accept-state refuses a damaged state.json before it asks for the code ---------------------------
    if not HAVE_PTY:
        print("  - [CP5-X66] accept-state through a terminal not run: no pty on this platform")
    else:
        env = make_env("claude")
        d, _tp = cp5r11_project(env)
        rc_ok, out_ok = cp5r11_accept(d, env)
        check("[CP5-X66] accept-state on a sound run asks for the code with its own purpose and accepts",
              rc_ok == 0 and "type this code" in out_ok
              and "accept the current state.json and human_log.jsonl as the new baseline" in out_ok,
              (rc_ok, out_ok[-400:]))
        (d / ".ratchet" / "runs" / "f" / "state.json").write_text(_CP5R11_CORRUPT)
        rc, out = cp5r11_accept(d, env)
        check("[CP5-X66] with a damaged state.json, accept-state refuses plainly before asking for the code (the "
              "person never types a code for a command that can't run)",
              rc == 1 and "can't be accepted" in out and "init --force" in out and "type this code" not in out,
              (rc, out[-400:]))

    # --- X67: the active-runs note and docstring describe the record as it is --------------------------------
    doc = m._active_marker.__doc__ or ""
    check("[CP5-X67] register_active no longer says other projects' entries come back when their runs end, and "
          "_active_marker's docstring says the record also names the active run when active.json is damaged",
          "runs start or end" not in src and "Ctx.active_slug" in doc and "active.json is damaged" in doc, doc)


SECTIONS.append(("CP5", cp5_adv_round12_restrictions_breaker_and_order))


# ---- CP5 adversarial round 12: flaky X61 ----
# The pty checks after the red snapshot read the override code from Ratchet's prompt line, never from the first
# number in the output: a damaged record's warning names a temp path that can hold a run of digits.

# The bare digit-hunting pattern CP4_CODE_RE replaced (see trust_helpers.py's comment on CP4_CODE_RE): kept here,
# inert, only so this test can still show that a naive "any run of digits" reader gets the wrong answer in a
# folder whose name holds digits, now that the real reader (cp4_find_code) no longer makes that mistake either.
_OLD_NAIVE_CODE_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


def cp5_flaky_x61_prompt_code():
    print("CP5 adv round 12: the override code is read from Ratchet's prompt, never from digits in a printed path")
    r, w = os.pipe()
    sample = ("ratchet: /tmp/ratchet-trust-cp4-8871g3bi/.ratchet/active.json could not be read\n"
              "RATCHET: RATCHET_HUMAN_OVERRIDE is set, to accept.\n"
              "To confirm you are a person at this terminal, type this code and press Enter: 022660\n> ")
    try:
        early = cp5_answer_prompt_code(w, sample.split("To confirm")[0])
        answered = cp5_answer_prompt_code(w, sample)
        os.close(w)
        w = None
        typed = os.read(r, 64).decode()
    finally:
        os.close(r)
        if w is not None:
            os.close(w)
    check("[CP5-X61] the code reader waits for Ratchet's prompt line and types back its code, not digits from a path "
          "printed before it", early is False and answered is True and typed == "022660\n", (early, answered, typed))
    if not HAVE_PTY:
        print("  - [CP5-X61] accept-state in a folder whose name holds digits not run: no pty on this platform")
        return
    real_mkdtemp = tempfile.mkdtemp

    def digits_in_name(*a, **k):
        if str(k.get("prefix", "")).startswith("ratchet-trust-cp4-"):
            k["suffix"] = "-4321x"
        return real_mkdtemp(*a, **k)
    tempfile.mkdtemp = digits_in_name
    try:
        env = make_env("claude")
        d, _tp = cp5r11_project(env)
    finally:
        tempfile.mkdtemp = real_mkdtemp
    (d / ".ratchet" / "active.json").write_text(_CP5R11_CORRUPT)
    rc, acc = cp5r11_accept(d, env)
    try:
        act = json.loads((d / ".ratchet" / "active.json").read_text())
    except ValueError:
        act = None
    # mkdtemp's random part can hold its own run of digits (e.g. "zs71019a"), so the first number in the output is
    # some number from the path, not always "4321": check that, and that the path comes before Ratchet's prompt.
    prompt = CP5_PROMPT_CODE_RE.search(acc)
    naive_m = _OLD_NAIVE_CODE_RE.search(acc)
    naive = naive_m.group(1) if naive_m else None
    warned_at = acc.find(str(d / ".ratchet" / "active.json"))
    check("[CP5-X61] in a project folder whose name holds a run of digits, accept-state over a damaged active.json "
          "(its warning names that path before the code) still gets the right code and writes active.json again",
          "4321x" in str(d) and prompt is not None and 0 <= warned_at < prompt.start() and naive is not None
          and naive in str(d) and naive != prompt.group(1) and rc == 0 and act == {"slug": "f"},
          (str(d), rc, acc[:700], act))


SECTIONS.append(("CP5", cp5_flaky_x61_prompt_code))


# ==================================================================================================================
# CP3 (speed-up checkpoint): this file's rx() subprocess call sites get converted to in-process engine calls
# (through the shared, memoized tests/trust_helpers.py:cp5y_load_engine()), except the per-user lock-breaker
# scripts (X54/X55, real subprocess.Popen/subprocess.run races) and every pty/forkpty interactive accept-state run
# (cp4_pty_run/os.forkpty), which stay real subprocesses on purpose. These CP3-Tnn cases test that conversion
# directly -- T04/T05/T06 scan this file's own source for the labeling convention and an exact enumeration; T01/
# T02/T03 read the one memoized, freshly-interpreted child _cp3_run_once() spawns and check its output and
# timing; T11 compares THIS process's own in-process tally (trust_helpers.PASS/FAIL) against that same memoized
# child's, instead of spawning any cold run of its own; T07/T08 probe the shared cached engine module itself;
# T09/T10 are history-free structural checks; T12 checks this file's own report writer.
# Appended after the existing SECTIONS/main() so none of round 10/11/12/flaky-X61's own check() bodies above are
# touched. Tag format: curly braces `{CP3-Tnn}`, never square brackets -- square-bracket tags collide with
# tests/test_split_check.py's own "[CP{digit}-[TX]{digit}]" scan for mixed historical CP-groups in a split file.
# ==================================================================================================================

import ast as _cp3_ast  # noqa: E402 -- deliberately imported here, at the start of the appended CP3 block, not at
import io  # noqa: E402            -- the top of the file with the rest, so the block's OWN new imports stay
                                   # visible as one unit a future merge or speed-up checkpoint can find at a
                                   # glance, the same way test_trust_cp5_e.py's own CP2 block does it. [Corrected,
                                   # this checkpoint] `io` is NOT solely introduced by this block: it is already
                                   # imported at the top of the file (line 8; _Cp3Call's own io.StringIO use now
                                   # lives in trust_helpers.InProcessCall, which it subclasses, but this file still
                                   # has its own direct uses below -- X64's `err = io.StringIO()`, `_cp3_inprocess_
                                   # call`'s `out = io.StringIO()`, round 12's stdin feeding) -- this line is a
                                   # harmless duplicate, kept so this block still reads as self-contained, not this
                                   # block's only source of `io`.
                                   # `contextlib` was dropped from here: nothing in this CP3-Tnn block ever used
                                   # it -- every real use already has its own local `import contextlib` right
                                   # where it's used (see X57's and X64's own bodies above).
from run_all import read_load  # noqa: E402 -- [Corrected, round 6] reuses run_all.py's own guarded load read
                                # (catches AttributeError/OSError around os.getloadavg(), see its docstring)
                                # instead of CP3-T02's end-to-end check calling os.getloadavg() directly.
_CP3_NO_RECURSE_VAR = "RATCHET_CP5D_NO_RECURSE"  # set on the child so it runs only SECTIONS, never recurses

# The pre-conversion baseline (captured solo, same command this checkpoint's test runner uses): 4 section
# announcements, 127 checks total (0 failed) when HAVE_PTY, 16 bracket ids ([CP5-X51]..[CP5-X67], X62 was never
# used) with these per-id occurrence counts. Conversion must not change any of this -- only how fast it's produced.
# [Corrected, this checkpoint] The original SECTIONS already skip their pty/forkpty-only checks when HAVE_PTY is
# false (see the `if not HAVE_PTY:` branches throughout this file, e.g. around X53/X56/X61/X66) -- a platform
# without a pty (trust_helpers.HAVE_PTY = hasattr(os, "forkpty"), false on Windows) legitimately runs fewer checks,
# and the expected totals below must agree, not assume a pty is always there. The HAVE_PTY=False totals (81 total;
# X53 4, X56 2, X61 36, X66 0, all others unchanged) were captured the same way as the HAVE_PTY=True ones: running
# this file's own SECTIONS solo with HAVE_PTY forced false (`del os.forkpty` before import) and reading the result.
# [Corrected, modularize-engine run's CP3] X60 gained one genuinely new check() (a mutation-proof regression
# proving the X60 scan, re-pointed at fingerprint.py after CP3's move, really walks into fingerprint.py's own
# function bodies) -- not a dropped or relabeled case, so the totals below are bumped by exactly that one check,
# same way, on both the HAVE_PTY and non-PTY counts (the new check does not itself depend on HAVE_PTY).
BASELINE_SECTIONS = [
    "CP5 adv round 10: damaged per-user records fail safe; a stale short lock is cleared by one process only",
    "CP5 adv round 11: every damaged record fails safe: no hook crashes, recovery always runs, no run is stuck",
    "CP5 adv round 12: restriction records are never written over, one stale-breaker winner, accept-state order",
    "CP5 adv round 12: the override code is read from Ratchet's prompt, never from digits in a printed path",
]
BASELINE_TALLY = 128 if HAVE_PTY else 82
BASELINE_ID_COUNTS = {
    "CP5-X51": 5, "CP5-X52": 1, "CP5-X53": 5 if HAVE_PTY else 4, "CP5-X54": 2, "CP5-X55": 2,
    "CP5-X56": 8 if HAVE_PTY else 2, "CP5-X57": 5, "CP5-X58": 3, "CP5-X59": 4, "CP5-X60": 5,
    "CP5-X61": 73 if HAVE_PTY else 36, "CP5-X63": 5, "CP5-X64": 2, "CP5-X65": 5, "CP5-X66": 2 if HAVE_PTY else 0,
    "CP5-X67": 1,
}
# [CP3-T06 baseline, corrected] A committed, CI-portable snapshot of every direct rx(...) call site this file had
# BEFORE this checkpoint's conversion -- re-derived (not trusted blindly) straight from `git show
# HEAD:tests/test_trust_cp5_d.py` (HEAD at the time this baseline was written was CP2's completion commit, i.e.
# this file exactly as it stood before this checkpoint's test-writer or implementer touched it), by AST-walking
# that source for every `rx(...)` call the same way _cp3_inprocess_engine_calls()/_cp3_commented_inprocess_calls()
# below walk today's. One entry per original call site (duplicates kept -- e.g. the 7 original identical
# 'rx(d, env, "ask", "approve-plan")' sites are 7 separate entries), each a normalized (whitespace-collapsed), exact
# substring of that call's own source text -- NOT a line number (line numbers shift the moment a single line is
# inserted anywhere above them, which is the opposite of stable). One entry (the "hook"/"prompt"/"s-A" site) is
# deliberately truncated before its stdin dict's later keys, since the real conversion's own comment legitimately
# abbreviates a long nested literal with "..." -- the truncation point is still long enough to be unique among all
# 24 entries. This baseline is used ONLY by CP3-T06 below: T04/T05 do their own independent AST scans of today's
# file and are untouched by this correction.
_CP3_ORIGINAL_RX_CALL_SITES = [
    'rx(d, env, "ask", "approve-plan")',                                                              # orig L122
    'rx(d, env, "ask", "approve-plan")',                                                               # orig L133
    'rx(folder, dict(env, RATCHET_ROLE="director"), "hook", "guard", "--host", "claude", '
    'stdin=json.dumps(payload))',                                                                      # orig L155
    'rx(d, env, "ask", "approve-plan")',                                                               # orig L175
    'rx(d, env, "ask", "approve-plan")',                                                               # orig L200
    'rx(d, env, "hook", "stop", "--host", "claude", '
    'stdin=json.dumps(dict(stop_payload(d), session_id="s-owner")))',                                  # orig L386
    'rx(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps({"session_id": "s-A",',          # orig L440
    'rx(d, env, "ask", "approve-plan")',                                                               # orig L469
    'rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(d)))',                # orig L482
    'rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(d)))',                # orig L483
    'rx(d, env, "ask", "approve-plan")',                                                               # orig L484
    'rx(folder, dict(env, RATCHET_ROLE="director"), "hook", "guard", "--host", "claude", '
    'stdin=json.dumps(payload))',                                                                      # orig L522
    'rx(d, env, "hook", "stop", "--host", "claude", '
    'stdin=json.dumps(dict(stop_payload(d), session_id="s-owner")))',                                  # orig L645
    'rx(d, env, *args)',                                                                               # orig L650
    'rx(d, env, "status", "--json")',                                                                  # orig L677
    'rx(d, env, "ask", "approve-plan")',                                                               # orig L684
    'rx(d, env, "status", "--json")',                                                                  # orig L685
    'rx(d, env, "status", "--json")',                                                                  # orig L699
    'rx(a, env, "status", "--json")',                                                                  # orig L772
    'rx(a, env, "ask", "approve-plan")',                                                               # orig L777
    'rx(a, env, "approve-plan")',                                                                      # orig L779
    'rx(home_dir, env, "doctor")',                                                                     # orig L782
    'rx(p2, env, "doctor")',                                                                            # orig L812
    'rx(p1, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(p1)))',              # orig L820
]
# Label words the conversion's own comment convention uses right next to a call-site needle above: "replaces ..."
# for a converted site (see _cp3_uncommented_inprocess_calls()'s own regex), "stays real ..."/"kept as a real ..."
# for a site kept as a genuine subprocess/pty call (see _CP3_KEPT_REASON_RE below, reused by T05).
_CP3_ACCOUNTED_FOR_RE = re.compile(r"\b(replaces|stays real|kept as a real)\b", re.I)

CP3_PASS = CP3_FAIL = 0
CP3_STATUS: dict = {}
CP3_MESSAGES: dict = {}
_CP3_ID_RE = re.compile(r"\{(CP3-T\d+)\}")
# Built from trust_helpers.MARK_PASS/MARK_FAIL (not hand-copied glyphs) so a future change to check()'s own
# pass/fail marks can't silently desync this parsing -- see cp3_check() below, which prints with the same marks.
_CP3_RESULT_LINE_RE = re.compile(
    r"^  (%s|%s) \[(CP5-X\d+)\]" % (re.escape(trust_helpers.MARK_PASS), re.escape(trust_helpers.MARK_FAIL)), re.M)


def cp3_check(name, cond, detail=""):
    """Like trust_helpers.check(), but also files the result under its {CP3-Tnn} id for the JSON report."""
    global CP3_PASS, CP3_FAIL
    CP3_PASS, CP3_FAIL = (CP3_PASS + 1, CP3_FAIL) if cond else (CP3_PASS, CP3_FAIL + 1)
    m = _CP3_ID_RE.search(name)
    if m:
        cid = m.group(1)
        CP3_STATUS[cid] = CP3_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CP3_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {trust_helpers.MARK_PASS if cond else trust_helpers.MARK_FAIL} {name}"
          + ("" if cond else f"   {str(detail)[:400]}"))


def _cp3_id_lines(stdout):
    """{bracket_id: (pass_count, fail_count)} read from a run's captured stdout."""
    counts = {}
    for mark, cid in _CP3_RESULT_LINE_RE.findall(stdout):
        p, f = counts.get(cid, (0, 0))
        counts[cid] = (p + 1, f) if mark == trust_helpers.MARK_PASS else (p, f + 1)
    return counts


_cp3_once_cache: dict = {}


def _cp3_run_fresh():
    """Always spawns a brand-new `python3 -I tests/test_trust_cp5_d.py` subprocess (never memoized itself --
    the memoizing is _cp3_run_once()'s job, its one caller). Mirrors test_trust_cp5_e.py's _cp2_run_once(),
    timed start-to-finish the same way CP1's baseline harness measures it. RATCHET_CP5D_NO_RECURSE tells the
    child to run only SECTIONS and skip these CP3-Tnn meta-cases -- without that guard the child would try to
    run them too, spawning another child, forever."""
    env = dict(os.environ)
    env[_CP3_NO_RECURSE_VAR] = "1"
    env.pop("RATCHET_TEST_REPORT", None)  # the child must never write over our own report file
    t0 = time.monotonic()
    # [CP3] kept as a real separate process: T01/T02/T03/T11 all need one freshly interpreted `python3 -I` child
    # process of this whole file (a genuine cold run, not a repeat within the one already-running process), and
    # _cp3_run_once() memoizes exactly that one child for all of them to share.
    r = subprocess.run([sys.executable, "-I", str(HERE / "test_trust_cp5_d.py")], cwd=str(HERE.parent),
                       env=env, capture_output=True, text=True, timeout=700)
    elapsed = time.monotonic() - t0
    return r, elapsed


def _cp3_run_once():
    """Runs this exact file solo exactly once, the same way as the checkpoint's own command (`python3 -I
    tests/test_trust_cp5_d.py`), and memoizes the result: every CP3-Tnn case that needs a real freshly
    interpreted run (T01, T02, T03, T11) reads the same cached result instead of paying for the real
    ~515s-baseline run again."""
    if "result" not in _cp3_once_cache:
        _cp3_once_cache["result"] = _cp3_run_fresh()
    return _cp3_once_cache["result"]


def _cp3_inprocess_call(m, d, env, args, stdin_text=None):
    """Minimal test-only in-process call wrapper, used ONLY by these CP3-Tnn meta-cases to probe the shared cached
    engine module directly (CP3-T08): chdir to `d`, swap os.environ for exactly `env` (both restored after), run
    m.main(args) and capture stdout and the exit code. [Corrected, this checkpoint] This is a probe helper only,
    separate from `_Cp3Call` -- the real conversion of this file's own rx(...) call sites (done-means 2-3) is this
    same diff's own work, not later work; it is done above, via `_Cp3Call`, not through this function."""
    cwd0 = os.getcwd()
    environ0 = dict(os.environ)
    streams0 = (sys.stdout, sys.stderr, sys.stdin)
    out = io.StringIO()
    code = 0
    try:
        os.chdir(d)
        os.environ.clear()
        os.environ.update(env)
        sys.stdout = out
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        try:
            # replaces what a direct rx(d, env, *args) subprocess call would be; used only by CP3-T08 to probe the
            # shared cached engine module directly, never a stand-in for one specific section's call site
            m.main(list(args))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception:
        code = 1
    finally:
        os.chdir(cwd0)
        os.environ.clear()
        os.environ.update(environ0)
        sys.stdout, sys.stderr, sys.stdin = streams0
    return code, out.getvalue()


def _cp3_inprocess_engine_calls(src):
    """[Call, ...] ast nodes for every direct call to a loaded engine module's cmd_*()/main() -- the shape a
    converted rx(...) call site takes once it becomes an in-process call."""
    tree = _cp3_ast.parse(src)
    return [n for n in _cp3_ast.walk(tree)
            if isinstance(n, _cp3_ast.Call) and isinstance(n.func, _cp3_ast.Attribute)
            and (n.func.attr.startswith("cmd_") or n.func.attr == "main")]


def _cp3_uncommented_inprocess_calls(src):
    """[line, ...] for every in-process engine call site (see _cp3_inprocess_engine_calls) with no comment in the
    two lines above it (or on its own line) naming the rx(...) call it replaces."""
    lines = src.splitlines()
    bad = []
    for node in _cp3_inprocess_engine_calls(src):
        i = node.lineno
        window = "\n".join(lines[max(0, i - 3):i])
        if not re.search(r"replaces\b[^\n]{0,200}\brx\(", window):
            bad.append(i)
    return bad


def _cp3_commented_inprocess_calls(src):
    """[line, ...] for every in-process engine call site that DOES carry that "replaces rx(...)" comment -- the
    converted-call count CP3-T04/T06 print and reconcile."""
    uncommented = set(_cp3_uncommented_inprocess_calls(src))
    return [node.lineno for node in _cp3_inprocess_engine_calls(src) if node.lineno not in uncommented]


_CP3_KEPT_REASON_RE = re.compile(
    r"real\b[^\n]{0,200}(cross-process|subprocess|separate process|one python process|in-process|terminal|"
    r"controlling tty|pty|lock race)", re.I)

# [CP3-T06, corrected] A real converted or kept call site, as text -- the thing a "replaces .../stays real ..."
# label comment must actually sit next to for its needle to count. Reused so a probe helper's own illustrative
# comment (which sits next to no such call) can never be mistaken for a real site's label.
_CP3_REAL_CALL_SITE_RE = re.compile(r"\bwith _Cp3Call\(|\bsubprocess\.(?:Popen|run)\(|\bcp4_pty_run\(")


def _cp3_meta_block_start_line(src):
    """1-based line number of the LAST `SECTIONS.append(...)` call in `src`. Everything after this line is this
    checkpoint's own CP3-Tnn test infrastructure (the meta-cases and their helpers/probes, including CP3-T08's
    `_cp3_inprocess_call` probe) -- never an original SECTIONS call site. _cp3_accounted_needle_counts never scans
    past it, so that probe's own lookalike comment can't be mistaken for a label on a real call site."""
    last = None
    for i, line in enumerate(src.splitlines(), start=1):
        if re.match(r"^SECTIONS\.append\(", line):
            last = i
    assert last is not None, "no SECTIONS.append(...) found in src -- update this boundary marker"
    return last


_CP3_KWARG_START_RE = re.compile(r"^[A-Za-z_]\w*\s*=(?!=)")


def _cp3_split_balanced(text, close_char):
    """Top-level, comma-separated substrings of `text` (the text right after a call's own opening delimiter), up
    to (not including) the first unmatched `close_char` (')' for a call's args, ']' for a list literal's
    elements) -- or, when `text` runs out first (an abbreviated "..." placeholder in a hand-written comment, for
    example), up to the end of `text`. Nested (), [], {} and quoted strings are tracked, so a comma or close char
    inside one of them is never mistaken for the top-level one."""
    items, depth, buf, quote, i, n = [], 0, "", None, 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            buf += c
            if c == "\\" and i + 1 < n:
                i += 1
                buf += text[i]
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            buf += c
        elif c in "([{":
            depth += 1
            buf += c
        elif c in ")]}":
            if c == close_char and depth == 0:
                break
            depth -= 1
            buf += c
        elif c == "," and depth == 0:
            items.append(buf)
            buf = ""
        else:
            buf += c
        i += 1
    if buf.strip():
        items.append(buf)
    return [s.strip() for s in items]


def _cp3_comment_rx_args(comment_text):
    """The rx(...) call's own positional args named in a '# replaces rx(cwd, env, ...)' comment, AFTER the first
    two (cwd, env), with any trailing keyword arg (stdin=... or any other `name=value`) dropped -- or None when
    `comment_text` names no rx(...) call at all (a "stays real .../kept as a real ..." comment names no specific
    call to compare against)."""
    m = re.search(r"\brx\(", comment_text)
    if not m:
        return None
    args = []
    for a in _cp3_split_balanced(comment_text[m.end():], ")"):
        if _CP3_KWARG_START_RE.match(a):
            break
        args.append(a)
    return args[2:]


_CP3_MAIN_LIST_OF_RE = re.compile(r"\bm\.main\(list\((\w+)\)\)")


def _cp3_main_list_args(window_text):
    """The elements of the `m.main([...])` list literal in `window_text` (the real call site a converted
    "# replaces rx(...)" comment sits next to) -- or None when `window_text` has no such call (not every
    accounted-for site is a converted one; a kept real subprocess/pty site has no m.main(...) call at all).
    `m.main(list(NAME))` (round 11's own X61 site, looping `for args in (["status"], ["next"], ["doctor"]):`)
    is the one call site in this file that passes a variable instead of a literal list -- its own "replaces r =
    rx(d, env, *args)" comment names it the same way, as `*args`, so this reads it that way too instead of
    requiring a literal list everywhere."""
    m = _CP3_MAIN_LIST_OF_RE.search(window_text)
    if m:
        return [f"*{m.group(1)}"]
    m = re.search(r"\bm\.main\(\[", window_text)
    if not m:
        return None
    return _cp3_split_balanced(window_text[m.end():], "]")


def _cp3_accounted_needle_counts(src):
    """{needle: count} -- how many times each distinct baseline call-site needle (see _CP3_ORIGINAL_RX_CALL_SITES)
    appears inside a comment that both (a) carries the conversion's own labeling convention (a "replaces ..." or a
    "stays real ..."/"kept as a real ..." comment) AND (b) sits on, or within a few lines above, an ACTUAL
    converted or kept call site (see _CP3_REAL_CALL_SITE_RE) -- never just any matching text anywhere else in the
    file. [Corrected, this checkpoint] Also never looks past _cp3_meta_block_start_line(src): CP3-T08's own
    `_cp3_inprocess_call` probe helper carries an illustrative "# replaces what a direct rx(d, env, *args)
    subprocess call would be" comment that labels no real call site at all, and used to satisfy the
    'rx(d, env, *args)' baseline needle even when the real X61 site's own label comment was missing.

    [Corrected, round 2] A "replaces rx(...)" comment next to a converted (`with _Cp3Call(`) site only counts
    when the comment's own named args (after the first two, cwd/env, and ignoring stdin=/other kwargs) equal the
    `m.main([...])` list literal on the call line beneath it -- so a converted site that silently runs a
    different command than its label claims is never counted as accounted for (host-alt-correctness round-2
    A2). A "stays real .../kept as a real ..." comment (no rx(...) call named at all) is unaffected by this: there
    is no m.main(...) call there to compare against."""
    boundary = _cp3_meta_block_start_line(src)
    lines = src.splitlines()
    counts = {}
    for i, line in enumerate(lines[:boundary], start=1):
        pre, sep, post = line.partition("#")
        if not sep or not _CP3_ACCOUNTED_FOR_RE.search(post):
            continue
        window = "\n".join(lines[i - 1:min(i + 4, boundary)])
        if not _CP3_REAL_CALL_SITE_RE.search(window):
            continue
        comment_args = _cp3_comment_rx_args(post)
        if comment_args is not None and "with _Cp3Call(" in window:
            main_args = _cp3_main_list_args(window)
            if main_args is None or comment_args != main_args:
                continue  # labeled "replaces rx(...)" but the real call site doesn't run what it claims
        for needle in set(_CP3_ORIGINAL_RX_CALL_SITES):
            n = post.count(needle)
            if n:
                counts[needle] = counts.get(needle, 0) + n
    return counts


def _cp3_unaccounted_original_sites(src):
    """[needle, ...] -- one entry per original call site (from the committed _CP3_ORIGINAL_RX_CALL_SITES baseline)
    that today's `src` does not account for. Reconciles as a multiset, not a simple presence check: if the baseline
    had 7 identical 'rx(d, env, "ask", "approve-plan")' entries and today's file only labels 5 of them (converted
    or kept, doesn't matter which), the other 2 show up here -- a partial reconciliation is a real, visible
    shortfall, never hidden by an aggregate pass/fail or by the mere presence of at least one matching comment."""
    need = {}
    for needle in _CP3_ORIGINAL_RX_CALL_SITES:
        need[needle] = need.get(needle, 0) + 1
    have = _cp3_accounted_needle_counts(src)
    out = []
    for needle, n in need.items():
        deficit = n - have.get(needle, 0)
        out.extend([needle] * max(0, deficit))
    return out


def t01_same_sections_and_tally():
    r, _elapsed = _cp3_run_once()
    cp3_check("{CP3-T01} running this file solo (`python3 -I tests/test_trust_cp5_d.py`) exits 0",
              r.returncode == 0, (r.returncode, r.stdout[-500:], r.stderr[-500:]))
    pos, order_ok = -1, True
    for section in BASELINE_SECTIONS:
        here = r.stdout.find(section)
        if here == -1 or here < pos:
            order_ok = False
            break
        pos = here
    cp3_check("{CP3-T01} it prints the same 4 section announcements (round 10, 11, 12, flaky X61), in that order",
              order_ok, r.stdout[:3000])
    m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
    cp3_check(f"{{CP3-T01}} the final tally is '{BASELINE_TALLY} passed, 0 failed' -- the same N as the "
              "pre-conversion baseline (no check silently removed, and none newly failing)",
              bool(m) and int(m.group(1)) == BASELINE_TALLY and int(m.group(2)) == 0,
              m.group(0) if m else r.stdout[-300:])


def t02_wall_clock_well_under_120s():
    """[Corrected, this checkpoint] This case now only asserts the re-spawned child's OWN inner wall-clock. The
    REAL top-level, end-to-end wall-clock assertion that used to live here (reading time.monotonic() -
    _CP3_PROCESS_START at this point) sampled too early: at this point in the run, T03-T12 had not executed yet,
    so it missed their cost and called itself "end-to-end" when it was not. That assertion -- still filed under
    this same {CP3-T02} id (cp3_check merges every call for one id: an id only passes if every cp3_check tagged
    with it does) -- now runs at the TRUE end of run_cp3_cases(), after every other CP3-Tnn case including T11
    has finished, right before the final report is written. See run_cp3_cases() below.

    [Corrected, round 3] The budget below is no longer stretched by run_all.py's own load-average/cpu-count
    stretch_factor(): that factor stays 1.0 for any load below the raw CPU count, which is exactly the
    "two agent lanes sharing one box" contention band the objective names as real usage, so it never actually
    scaled the budget there. Instead the budget is measured directly from this same process's own in-process
    SECTIONS run (_CP3_PARENT_SECTIONS_ELAPSED, set once at the end of main()'s SECTIONS loop, same box, same
    moment), scaled by 1.5x plus 15s slack, capped at a 110s absolute ceiling -- comfortably above a healthy
    run but still well below the 514.7s solo baseline. This 110s ceiling only bounds the SECTIONS body's own
    per-process cost: the child this case times runs with RATCHET_CP5D_NO_RECURSE set and never executes any
    CP3-Tnn meta-case, T11 included (see main() below), so a regression that re-adds T11's old four-cold-runs
    extra spawns would leave this child's own elapsed time completely unchanged and this check would still
    pass. Only the end-to-end check in run_cp3_cases() (below) can see that kind of regression."""
    r, elapsed = _cp3_run_once()
    parent_sections = _CP3_PARENT_SECTIONS_ELAPSED
    ratio_budget = 1.5 * parent_sections + 15.0
    absolute_ceiling = 110.0
    budget = min(ratio_budget, absolute_ceiling)
    which_bound = "ratio" if ratio_budget <= absolute_ceiling else "ceiling"
    cp3_check("{CP3-T02} the re-spawned child's own inner wall-clock (a monotonic timer around that one subprocess "
              "invocation, the same way CP1's baseline harness measures it) is comfortably under its budget "
              f"(min(1.5x this process's own in-process SECTIONS run [{parent_sections:.1f}s] + 15s slack = "
              f"{ratio_budget:.1f}s, a {absolute_ceiling:.0f}s absolute ceiling) = {budget:.1f}s, {which_bound} "
              f"bound applied), down from the 514.7s solo baseline (took {elapsed:.1f}s, exit {r.returncode})",
              elapsed < budget, (elapsed, budget, parent_sections))


def t03_no_existing_check_dropped():
    r, _elapsed = _cp3_run_once()
    counts = _cp3_id_lines(r.stdout)
    short = {cid: (base_n, counts.get(cid, (0, 0))) for cid, base_n in BASELINE_ID_COUNTS.items()
             if counts.get(cid, (0, 0))[0] < base_n}
    cp3_check("{CP3-T03} every one of the 16 bracket ids ([CP5-X51]..[CP5-X67], X62 was never used) appears in "
              "stdout at least as many times as it did before conversion -- none is missing, so a case can't be "
              "quietly weakened by deleting or relabeling its check() call while the overall pass count stays "
              "accidentally the same", not short, short)


def t04_converted_call_sites_name_what_they_replace():
    good_probe = ("def f(m, d, env):\n"
                  "    # replaces rx(d, env, 'init', 'feature', '--slug', 'f')\n"
                  "    m.cmd_init(args)\n")
    bad_probe = "def f(m, d, env):\n    m.cmd_init(args)\n"
    cp3_check("{CP3-T04} the scan lets through a converted call site whose comment names the rx(...) call it "
              "replaces", _cp3_uncommented_inprocess_calls(good_probe) == [],
              _cp3_uncommented_inprocess_calls(good_probe))
    cp3_check("{CP3-T04} ... and flags one with no such comment",
              _cp3_uncommented_inprocess_calls(bad_probe) == [2], _cp3_uncommented_inprocess_calls(bad_probe))
    src = (HERE / "test_trust_cp5_d.py").read_text()
    bad = _cp3_uncommented_inprocess_calls(src)
    cp3_check("{CP3-T04} every in-process engine call site in this file (every direct call to the loaded engine's "
              "cmd_*() or main(), which is what a converted rx(...) call becomes) carries a comment naming the "
              "real rx(...) subprocess call it replaces", bad == [], bad)
    converted = _cp3_commented_inprocess_calls(src)
    cp3_check(f"{{CP3-T04}} a concrete, printed converted-call count (not just aggregate pass/fail) is greater "
              f"than zero, so a reviewer or a future merge can diff the exact set (today: {len(converted)})",
              len(converted) > 0, converted[:10])
    # [CP3-T04, added] The scan above only recognizes m.cmd_*()/m.main(...) calls -- by design, since that is the
    # shape every converted rx(...) call site takes. X53's `m.record_chain(sp)` probe is the ONE site _Cp3Call's
    # own docstring documents as a deliberate exception to the one-`m.main(...)`-line contract (it replaces a
    # standalone subprocess.run(...) script, never an rx(...) call), so it is invisible to the scan above and would
    # otherwise be checked by nothing at all. Give it its own explicit, narrow check instead of broadening the
    # general cmd_*/main scan (which would risk letting an unrelated, uncommented cmd_*/main site slip through).
    tree = _cp3_ast.parse(src)
    record_chain_lines = [n.lineno for n in _cp3_ast.walk(tree) if isinstance(n, _cp3_ast.Call)
                          and isinstance(n.func, _cp3_ast.Attribute) and n.func.attr == "record_chain"]
    lines = src.splitlines()
    unlabeled_rc = [i for i in record_chain_lines
                    if not re.search(r"replaces\b[^\n]{0,300}subprocess\.run",
                                      "\n".join(lines[max(0, i - 10):i]))]
    cp3_check("{CP3-T04} the one documented exception to the m.main(...)-only contract (X53's m.record_chain(...) "
              "probe) still carries a nearby comment naming the real subprocess.run(...) call it replaces",
              record_chain_lines != [] and unlabeled_rc == [], (record_chain_lines, unlabeled_rc))


def t05_kept_real_subprocess_sites_name_the_reason():
    src = (HERE / "test_trust_cp5_d.py").read_text()
    lines = src.splitlines()
    tree = _cp3_ast.parse(src)
    popen_run_calls = [n for n in _cp3_ast.walk(tree) if isinstance(n, _cp3_ast.Call)
                       and isinstance(n.func, _cp3_ast.Attribute) and isinstance(n.func.value, _cp3_ast.Name)
                       and n.func.value.id == "subprocess" and n.func.attr in ("Popen", "run")]
    cp3_check(f"{{CP3-T05}} the per-user lock-breaker scripts (X54/X55) still launch real subprocess.Popen/"
              f"subprocess.run calls (not replaced by an in-process or threaded stand-in) -- found "
              f"{len(popen_run_calls)}", len(popen_run_calls) >= 3, len(popen_run_calls))
    pty_calls = [n for n in _cp3_ast.walk(tree) if isinstance(n, _cp3_ast.Call)
                and ((isinstance(n.func, _cp3_ast.Name) and n.func.id == "cp4_pty_run")
                     or (isinstance(n.func, _cp3_ast.Attribute) and n.func.attr == "forkpty"))]
    cp3_check(f"{{CP3-T05}} every pty/forkpty interactive accept-state call site (cp4_pty_run/os.forkpty) is still "
              f"present (not converted to an in-process or threaded stand-in) -- found {len(pty_calls)}",
              len(pty_calls) >= 3, len(pty_calls))
    unlabeled = []
    for call in list(popen_run_calls) + list(pty_calls):
        i = call.lineno
        nearby = "\n".join(lines[max(0, i - 6):i])
        if not _CP3_KEPT_REASON_RE.search(nearby):
            unlabeled.append(i)
    cp3_check("{CP3-T05} each kept subprocess.Popen/subprocess.run/cp4_pty_run call site carries a nearby comment "
              "naming the specific reason that boundary must stay a real subprocess (a genuine cross-process lock "
              "race, or a real controlling terminal) -- not just that it happens to still be one",
              unlabeled == [], unlabeled)


def t06_converted_and_kept_counts_reconcile():
    # [CP3-T06, corrected] Per-site reconciliation against the committed, CI-portable _CP3_ORIGINAL_RX_CALL_SITES
    # baseline (re-derived from `git show HEAD:tests/test_trust_cp5_d.py`, the pre-conversion source) -- replaces
    # the original design's two assertions that couldn't both hold once real conversion happened: (a) "this file
    # still has exactly 24 literal rx(...) call sites" (impossible by definition once any are converted away) and
    # (b) "converted + kept == 24" (wrong once the implementer legitimately converted MORE than 24 direct sites,
    # e.g. shared trust_helpers functions called repeatedly in a loop -- a GOOD thing the old arithmetic couldn't
    # express and would instead punish). This case asserts ONLY one thing: nothing from the ORIGINAL list was
    # silently dropped -- every one of those sites is, today, either converted-with-a-"replaces rx(...)"-comment or
    # kept-with-a-"stays real .../kept as a real ..."-comment. It does NOT re-assert a 24-site ceiling and does NOT
    # care how many extra (non-original) sites got converted too -- CP3-T04 already separately counts/asserts the
    # full converted-call set broadly.
    src = (HERE / "test_trust_cp5_d.py").read_text()
    missing = _cp3_unaccounted_original_sites(src)
    counted = {}
    for needle in missing:
        counted[needle] = counted.get(needle, 0) + 1
    cp3_check(f"{{CP3-T06}} every one of the {len(_CP3_ORIGINAL_RX_CALL_SITES)} original rx(...) call sites from "
              "the committed pre-conversion baseline is, in today's file, either converted (a 'replaces rx(...)' "
              "comment naming it) or kept as a real subprocess/pty call (a 'stays real .../kept as a real ...' "
              "comment naming it) -- none silently dropped or forgotten "
              f"(unaccounted: {counted if counted else 'none'})",
              missing == [], missing)


def t07_loader_is_memoized_once_per_process():
    candidates = []
    for name, fn in (("cp5y_load_engine", cp5y_load_engine), ("engine", trust_helpers.engine)):
        try:
            m1 = fn()
            m2 = fn()
            candidates.append((name, m1 is m2))
        except Exception as e:
            candidates.append((name, False, repr(e)))
    cp3_check("{CP3-T07} calling the shared in-process engine loader (cp5y_load_engine) twice in one process "
              "returns the identical module object both times -- proving ratchet.py's ~21k lines are compiled "
              "once per test process, not once per converted call, the specific cost app-scout found a naive "
              "per-call conversion would still pay",
              any(c[1] for c in candidates if c[0] == "cp5y_load_engine"), candidates)


def t08_cache_never_leaks_state_between_projects():
    m = cp5y_load_engine()
    env_a, env_b = make_env("claude"), make_env("claude")
    d_a, _tp_a = cp4_unapproved_plan(env_a)
    d_b, _tp_b = cp4_unapproved_plan(env_b)
    code_a, out_a = _cp3_inprocess_call(m, d_a, env_a, ["status", "--json"])
    reset_after_a = {k: getattr(m, k, "MISSING") for k in ("_STATE_LOCK_HELD", "_IN_HOOK", "_ENGINE_ARGV")}
    code_b, out_b = _cp3_inprocess_call(m, d_b, env_b, ["status", "--json"])
    try:
        js_a, js_b = json.loads(out_a), json.loads(out_b)
    except ValueError:
        js_a = js_b = None
    # [Corrected, round 2] Both projects share the same --slug ("f", cp4_unapproved_plan's fixed value), so
    # comparing js_a/js_b's slug to that shared constant can't distinguish broken isolation (a cwd/env swap that
    # silently failed, leaving both calls reading project A, would satisfy the old check just as well). `status
    # --json`'s own "root" field (str(ctx.root), ratchet.py's cmd_status) is a path, not a shared constant: it
    # names the actual project each call resolved to, so comparing it against each project's OWN resolved folder
    # -- and asserting the two differ from each other -- actually fails if isolation breaks (host-alt-correctness
    # round-2 A3).
    cp3_check("{CP3-T08} two in-process `status --json` calls for two different throwaway projects, back to "
              "back through the one shared cached engine module, both exit 0 and each reports its OWN project's "
              "root folder, never the other's",
              code_a == 0 and code_b == 0 and js_a is not None and js_b is not None
              and js_a.get("root") == str(d_a.resolve()) and js_b.get("root") == str(d_b.resolve())
              and js_a.get("root") != js_b.get("root"),
              (code_a, code_b, out_a[-200:], out_b[-200:]))
    cp3_check("{CP3-T08} after project A's in-process call, the shared module's own per-call flags "
              "(_STATE_LOCK_HELD/_IN_HOOK/_ENGINE_ARGV) are back to their pristine default before project B's "
              "call ever runs -- nothing project A set lingers into project B's call",
              reset_after_a.get("_STATE_LOCK_HELD") in (False, "MISSING")
              and reset_after_a.get("_IN_HOOK") in (False, "MISSING"), reset_after_a)
    # [Corrected, round 2] Dropped: a third assertion here used to read project B's own engine-call-sessions.json
    # and check it never named project A's folder, but that file is written only by the guard hook, which neither
    # of this case's two calls (cp4_unapproved_plan, `status --json`) ever runs -- raw_b was always the "{}"
    # default, so `str(d_a.resolve()) not in raw_b` could never fail (host-alt-correctness round-2 A3, second
    # half). Giving this meta-case a real call path that writes that file is a later checkpoint's work; the two
    # checks above prove cwd/project-root isolation only. HOME/RATCHET_HOME isolation between the two in-process
    # calls is NOT covered by this meta-case: `root` is resolved from cwd, not from HOME, so nothing here would
    # catch an env/RATCHET_HOME swap failure.


# [CP3] T09 is a permanent, HISTORY-FREE structural check: it needs no git diff and no base commit at all, unlike
# an earlier approach (rejected: see this checkpoint's own learnings) that diffed ratchet.py against a pinned base
# commit, which would break forever the next time anyone makes a completely unrelated, legitimate change to
# ratchet.py (for example lane M's own concurrent CLI-modularization refactor). What this checks, forever: ratchet
# .py has at most one function clearly labeled test-only (per this checkpoint's written convention: "a function or
# block clearly labeled test-only, by name or an adjacent comment saying so" -- checkpoints.json's CP3 entry), and
# no production code path in ratchet.py calls into it. The before/after proof of "did this checkpoint's own work
# touch ratchet.py outside its labeled test-only seam" is covered once, per round, by the adversarial review's own
# diff check -- never as a standing suite assertion. Mirrors test_trust_cp5_e.py's own CP2-T06
# (t06_test_only_seam_is_structurally_sound), renamed for this checkpoint.
def _cp3_test_only_functions(tree, lines):
    """[FunctionDef/AsyncFunctionDef, ...] for every TOP-LEVEL (module-scope) function in ratchet.py whose name,
    adjacent comment, or docstring marks it test-only. Only module-scope functions count as "entry points"; a
    label on a nested/inner function doesn't make the file itself expose one."""
    found = []
    for node in tree.body:
        if not isinstance(node, (_cp3_ast.FunctionDef, _cp3_ast.AsyncFunctionDef)):
            continue
        if re.search(r"test.?only", node.name, re.I):
            found.append(node)
            continue
        above = "\n".join(lines[max(0, node.lineno - 4):node.lineno - 1])
        if re.search(r"test.?only", above, re.I):
            found.append(node)
            continue
        doc = _cp3_ast.get_docstring(node) or ""
        if re.search(r"test.?only", doc, re.I):
            found.append(node)
    return found


def t09_test_only_seam_is_structurally_sound():
    src = ENGINE.read_text(encoding="utf-8")
    tree = _cp3_ast.parse(src)
    lines = src.splitlines()
    found = _cp3_test_only_functions(tree, lines)
    cp3_check("{CP3-T09} ratchet.py has at most one top-level function clearly labeled test-only (by name or an "
              "adjacent comment/docstring saying so)", len(found) <= 1, [n.name for n in found])
    if not found:
        return
    fn = found[0]
    cp3_check(f"{{CP3-T09}} its name ('{fn.name}') itself matches the written test-only naming convention",
              re.search(r"test.?only", fn.name, re.I) is not None, fn.name)
    callers = [node.lineno for node in _cp3_ast.walk(tree)
               if isinstance(node, _cp3_ast.Name) and node.id == fn.name
               and not (fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno))]
    cp3_check(f"{{CP3-T09}} no production code path in ratchet.py (no command dispatch, no CLI handler, no other "
              f"function body) calls '{fn.name}()' -- it has no callers anywhere else in the file",
              callers == [], callers)


def _cp3_source_read_assign(fn):
    """[CP3-T10, corrected -- loosened from an AST pin to a structural probe] The Assign statement in `fn`'s own
    top-level body that reads ratchet.py's source through the shared ENGINE pointer into a local variable (today:
    `src = ENGINE.read_text(encoding="utf-8")`) -- found structurally (a single-Name-target assignment whose
    expression mentions the global ENGINE), not pinned to one specific literal method-call spelling. A legitimate
    re-point of HOW the source is read (e.g. lane M's modularization reading split files through ENGINE some other
    way) does not trip this on the exact API shape, only on no longer reading live content through ENGINE at all.
    Returns None if no such statement exists."""
    for stmt in fn.body:
        if (isinstance(stmt, _cp3_ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], _cp3_ast.Name)
                and any(isinstance(n, _cp3_ast.Name) and n.id == "ENGINE" for n in _cp3_ast.walk(stmt.value))):
            return stmt
    return None


def _cp3_exec_source_read(stmt, engine_path):
    """Executes ONLY that one isolated assignment statement -- never the rest of the function, which would re-run
    its own check()s against trust_helpers' shared PASS/FAIL tally a second time (T11 depends on that tally
    matching BASELINE_TALLY exactly) -- with the global ENGINE monkeypatched to `engine_path`, and returns whatever
    its target local variable ends up holding."""
    mod = _cp3_ast.Module(body=[stmt], type_ignores=[])
    _cp3_ast.fix_missing_locations(mod)
    code = compile(mod, "<cp3-t10-live-read-probe>", "exec")
    g = dict(sys.modules[__name__].__dict__)
    g["ENGINE"] = engine_path
    ns = {}
    exec(code, g, ns)
    return ns[stmt.targets[0].id]


def t10_source_inspection_reads_live_file():
    """CP3-T10: lane M has unmerged, re-pointed source-inspection checks in THIS file (X59's _BreakerLock subclass
    check, X60's AST scan, X67's docstring check). This protects that merge seam: confirms these checks still read
    ratchet.py's own current source/module live off disk every time they run, inside the function that uses it,
    never from a frozen snapshot or a stale module reference captured once at import time. Keep this test clearly
    commented so a future merge can tell at a glance this protection is intentional.

    [Corrected, this checkpoint] The live-read check below is now BEHAVIORAL, not an AST pin on one literal API
    spelling: it mutates a scratch file ENGINE is monkeypatched to point at, between two isolated executions of
    just the function's own source-reading statement, and confirms the read result actually changes to match --
    proving a fresh, live read every call -- instead of asserting the statement is spelled exactly
    `ENGINE.read_text(...)`, which a legitimate lane-M re-point could change without weakening the property."""
    src_d = (HERE / "test_trust_cp5_d.py").read_text()
    tree_d = _cp3_ast.parse(src_d)
    for fn_name in ("cp5_adv_round11_damaged_record_class", "cp5_adv_round12_restrictions_breaker_and_order"):
        fn = next((n for n in _cp3_ast.walk(tree_d) if isinstance(n, _cp3_ast.FunctionDef) and n.name == fn_name),
                  None)
        cp3_check(f"{{CP3-T10}} {fn_name} is still defined in this file", fn is not None, fn_name)
        read_stmt = fn is not None and _cp3_source_read_assign(fn)
        cp3_check(f"{{CP3-T10}} {fn_name} still has a statement in its own body reading ratchet.py's source "
                  "through the shared ENGINE pointer into a local variable (today's shape -- "
                  "`src = ENGINE.read_text(...)` -- is one example, not a pinned requirement)",
                  bool(read_stmt), fn_name)
        if read_stmt:
            scratch = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp3-t10-")) / "scratch_ratchet.py"
            scratch.write_text("# scratch content A\n")
            val_a = _cp3_exec_source_read(read_stmt, scratch)
            scratch.write_text("# scratch content B, now different\n")
            val_b = _cp3_exec_source_read(read_stmt, scratch)
            cp3_check(f"{{CP3-T10}} {fn_name} reads ratchet.py's source live, fresh, every call -- when the file "
                      "ENGINE names changes between two calls, its own read result changes to match, proving it "
                      "is never a frozen snapshot taken once (whether at import time or on a first call)",
                      val_a == "# scratch content A\n" and val_b == "# scratch content B, now different\n",
                      (val_a, val_b))
        loads_engine = fn is not None and any(
            isinstance(n, _cp3_ast.Call) and (
                (isinstance(n.func, _cp3_ast.Name) and n.func.id == "cp5y_load_engine")
                or (isinstance(n.func, _cp3_ast.Attribute) and n.func.attr == "cp5y_load_engine"))
            for n in _cp3_ast.walk(fn))
        cp3_check(f"{{CP3-T10}} {fn_name} loads the engine module through the shared, live loader "
                  "(cp5y_load_engine), not a frozen reference taken elsewhere", loads_engine, fn_name)
    frozen = {t.id for node in tree_d.body if isinstance(node, _cp3_ast.Assign) for t in node.targets
              if isinstance(t, _cp3_ast.Name) and re.search(r"(_src\b|_SRC\b|_engine\b|_ENGINE_MODULE\b)", t.id)}
    cp3_check("{CP3-T10} no module-level variable in this file holds a frozen copy of ratchet.py's source or a "
              "pre-loaded engine module (every read goes through ENGINE.read_text()/cp5y_load_engine() inside the "
              "function that needs it, each time it runs)", frozen == set(), frozen)
    cp3_check("{CP3-T10} ENGINE itself still names the real, live production file on disk, and reading it now "
              "matches reading the real path directly -- never a cached/snapshotted copy",
              ENGINE.exists() and ENGINE.read_text(encoding="utf-8") == (HERE.parent / "skills" /
              "ratchet-orchestrator" / "scripts" / "ratchet.py").read_text(encoding="utf-8"), str(ENGINE))


def t11_two_cold_runs_agree():
    """[Reworked, this checkpoint] "Two independent runs of the converted SECTIONS agree, no flakiness from the
    conversion" used to mean spawning two MORE fresh `python3 -I` child processes here, on top of the one T01/T02/
    T03 already pay for -- making every ordinary invocation of this file run the converted SECTIONS body FOUR
    times (this process's own in-process run, the one memoized child, and these two extra ones), well over the
    120s budget. The two independent runs this case needs were already being paid for and just not compared:
    this very process's own in-process execution of SECTIONS (main() runs it directly, before any CP3-Tnn
    meta-case including this one ever starts -- trust_helpers.PASS/FAIL already holds that result) and the one
    separately-spawned, freshly-interpreted child _cp3_run_once() memoizes (a genuine independent cold
    `python3 -I` process, the same one T01/T02/T03 read). Comparing those two tallies proves the same thing the
    old two-extra-cold-runs design proved -- the conversion behaves identically in-process and freshly spawned,
    no flakiness -- without spawning anything new."""
    r, _elapsed = _cp3_run_once()
    m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
    child_ok = (r.returncode == 0 and bool(m) and int(m.group(1)) == BASELINE_TALLY and int(m.group(2)) == 0)
    parent_ok = (trust_helpers.PASS == BASELINE_TALLY and trust_helpers.FAIL == 0)
    cp3_check(f"{{CP3-T11}} two independent executions of the converted SECTIONS body -- this process's own "
              "in-process run (already performed, before any CP3-Tnn meta-case, as part of main()'s normal flow) "
              "and a separately-spawned, freshly-interpreted `python3 -I` child process (the same one T01/T02/T03 "
              f"use) -- both agree: '{BASELINE_TALLY} passed, 0 failed', no flakiness introduced by the in-process "
              "caching/memoization the conversion added",
              child_ok and parent_ok,
              (r.returncode, m.group(0) if m else r.stdout[-300:], trust_helpers.PASS, trust_helpers.FAIL))


def t12_report_names_every_case():
    ids = [cid for cid, _ in TESTS]
    expected = [f"CP3-T{i:02d}" for i in range(1, 14)]
    cp3_check("{CP3-T12} this file's own TESTS table defines all 13 planned CP3 cases, CP3-T01 through CP3-T13, "
              "none missing and none duplicated", ids == expected, ids)
    tmp_report = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp3-report-")) / "report.json"
    old = os.environ.get("RATCHET_TEST_REPORT")
    os.environ["RATCHET_TEST_REPORT"] = str(tmp_report)
    try:
        cp3_write_report()
    finally:
        if old is None:
            os.environ.pop("RATCHET_TEST_REPORT", None)
        else:
            os.environ["RATCHET_TEST_REPORT"] = old
    try:
        report = json.loads(tmp_report.read_text())
    except (OSError, ValueError):
        report = None
    got_ids = sorted(c.get("id") for c in (report or {}).get("cases", []))
    cp3_check("{CP3-T12} calling this file's own report writer produces a JSON file naming every CP3 case id, none "
              "missing and none crashed/skipped",
              report is not None and got_ids == sorted(expected), (report, got_ids))


def t13_cp3_t02_budget_formula():
    """{CP3-T13} Direct unit test of _cp3_t02_budget(), the pure arithmetic extracted from run_cp3_cases()'s own
    end-to-end {CP3-T02} check. No subprocess, no environment variables, no fake-env plumbing: this calls the
    function with raw numbers and checks the result."""
    idle_budget = _cp3_t02_budget(0.8, 12)
    cp3_check("{CP3-T13} at or below the idle baseline (load_avg 0.8 <= idle_load_avg 1.5), the budget is "
              "exactly the flat ceiling, no allowance", idle_budget == 90.0, idle_budget)

    heavy_budget = _cp3_t02_budget(12.29, 12)
    cp3_check("{CP3-T13} this box's own measured heavy-load point (load_avg 12.29, 12 cpus) reproduces the "
              "~133s budget measured earlier this round under genuine heavy load, within a couple seconds",
              abs(heavy_budget - 133.0) <= 2.0, heavy_budget)

    small_box_budget = _cp3_t02_budget(5.0, 4)
    this_box_budget = _cp3_t02_budget(5.0, 12)
    cp3_check("{CP3-T13} CPU-normalization works: the same load average (5.0) gives a LARGER allowance on a "
              "small, 4-thread box than on this 12-thread box",
              (small_box_budget - 90.0) > (this_box_budget - 90.0), (small_box_budget, this_box_budget))
    cp3_check("{CP3-T13} that small-box allowance is big enough that a real_elapsed around 110-120s on a "
              "4-thread box at load_avg 5.0 would still pass (budget comfortably >= 115s)",
              small_box_budget >= 115.0, small_box_budget)


TESTS = [
    ("CP3-T01", t01_same_sections_and_tally),
    ("CP3-T02", t02_wall_clock_well_under_120s),
    ("CP3-T03", t03_no_existing_check_dropped),
    ("CP3-T04", t04_converted_call_sites_name_what_they_replace),
    ("CP3-T05", t05_kept_real_subprocess_sites_name_the_reason),
    ("CP3-T06", t06_converted_and_kept_counts_reconcile),
    ("CP3-T07", t07_loader_is_memoized_once_per_process),
    ("CP3-T08", t08_cache_never_leaks_state_between_projects),
    ("CP3-T09", t09_test_only_seam_is_structurally_sound),
    ("CP3-T10", t10_source_inspection_reads_live_file),
    ("CP3-T11", t11_two_cold_runs_agree),
    ("CP3-T12", t12_report_names_every_case),
    ("CP3-T13", t13_cp3_t02_budget_formula),
]


def cp3_write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CP3_STATUS.get(cid, False) else "failed",
              "message": "" if CP3_STATUS.get(cid, False) else "; ".join(CP3_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def _cp3_t02_budget(load_avg, cpus):
    """Pure arithmetic: the CP3-T02 end-to-end budget for a given load average and cpu count. Extracted
    from run_cp3_cases() so the formula can be unit-tested (CP3-T13) without running the whole suite or faking
    the environment. The constants here must stay in lockstep with run_cp3_cases()'s own comments explaining
    them (idle_load_avg, base_slope_per_load_point_at_12_threads, base_ceiling)."""
    idle_load_avg = 1.5
    base_slope_per_load_point_at_12_threads = 4.0
    load_allowance_per_point = base_slope_per_load_point_at_12_threads * (12.0 / (cpus or 12))
    base_ceiling = 90.0
    load_allowance = load_allowance_per_point * max(0.0, load_avg - idle_load_avg)
    return base_ceiling + load_allowance


def run_cp3_cases():
    """Runs every CP3-Tnn case and writes the JSON report; returns True when any case failed."""
    for cid, fn in TESTS:
        CP3_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            CP3_STATUS[cid] = False
            CP3_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  {trust_helpers.MARK_FAIL} [{cid}] CRASHED: {e}")
            traceback.print_exc()
    # [CP3, corrected] The REAL top-level wall-clock check, moved here from inside t02_wall_clock_well_under_120s()
    # (see that function's own docstring): this is the true end of the run a user or run_all.py waits on -- AFTER
    # every other CP3-Tnn case has finished -- so it bounds the complete cost of `python3 -I
    # tests/test_trust_cp5_d.py`, not a snapshot taken partway through. Still filed under {CP3-T02}: cp3_check
    # merges every call tagged with one id, so this id only passes if every cp3_check call for it does.
    #
    # [Corrected, round 5] Rounds 2-4's budget here (min(2.2x the measured child's own elapsed + 30s, a 300s
    # ceiling)) could never fail on a real regression: it scales WITH the same child/parent SECTIONS measurement
    # it is bounding, so a uniform slowdown (every SECTIONS run, parent's in-process run and the child's fresh
    # run alike, getting slower by the same factor) inflates the budget right along with real_elapsed (codex-
    # correctness round-4 X1; host-alt-correctness round-4 A1). The 300s ceiling was also far looser than
    # done-means 2's "well under 120s".
    #
    # Replaced with ONE mechanism: a flat ceiling plus an allowance tied to the box's own RAW load average
    # (os.getloadavg()[0], sampled fresh right here -- not the load/cpu-count ratio tests/run_all.py's
    # stretch_factor() uses, which stays 1.0 below the raw CPU count and misses exactly the shared-box
    # contention band this objective cares about), not to anything this process itself measured. A code
    # regression inside this one process (a stray sleep, a couple of extra subprocess spawns) does not move
    # system-wide load average enough to matter; genuine external contention (another lane's processes) does,
    # including below the raw CPU count. So the raw load average can tell "the box is busy" apart from "the
    # code got slower" in a way neither the old ratio term nor stretch_factor() could.
    #
    # Measured this round (flock'd, otherwise-idle 12-core box unless noted):
    #   - idle, unmodified code: real_elapsed 63.8s against budget 90.0s (load_avg ~0.9, below idle_load_avg,
    #     so load_allowance is 0) -- comfortable headroom, well under 120s.
    #   - idle, with a planted 30s time.sleep() at the top of cp5_adv_round10_damaged_records_and_breaker (one
    #     SECTIONS function, so both the parent's own in-process SECTIONS run and the child's fresh SECTIONS run
    #     pay the extra 30s): real_elapsed exceeded the same 90.0s base_ceiling (load_allowance still 0 at idle)
    #     and {CP3-T02} failed, exactly the uniform-regression case rounds 2-4 could never catch. Sleep removed
    #     afterward.
    #   - heavy load (11 busy CPU processes, load avg ~10.5-12.5 on this 12-core box, matching rounds 2-4's own
    #     load-simulation): real_elapsed 118.4s against a budget that grows with that same raw load_avg -- PASS,
    #     no false red from contention alone.
    _r, elapsed = _cp3_run_once()
    real_elapsed = time.monotonic() - _CP3_PROCESS_START
    idle_load_avg = 1.5  # this box's own observed idle 1-min load sits well under this; a little background
                          # noise does not trigger any allowance
    # [Corrected, round 6] read_load() (tests/run_all.py) already guards this exact os.getloadavg()[0] read
    # against AttributeError (no os.getloadavg on Windows) and OSError (e.g. an unreadable /proc/loadavg in
    # some sandboxes) and returns None instead of raising. Calling os.getloadavg() directly here, outside the
    # per-case try/except above, let either of those escape run_cp3_cases() entirely and skip
    # cp3_write_report() (codex-correctness/codex-standards round-5 X1). A missing/unreadable reading now
    # degrades to idle_load_avg -- zero allowance, never a crash -- and this check still runs.
    _load_reading = read_load()
    load_avg = _load_reading[0] if _load_reading is not None else idle_load_avg
    # [Corrected, round 6] This slope was fitted on THIS box's 12 threads; the same raw load average is
    # proportionally more contention per CPU on a box with fewer threads (host-alt-correctness round-5 A2).
    # Scale it by this box's own thread count relative to the 12 it was measured on, smoothly in both
    # directions (fewer threads -> steeper slope, more threads -> shallower) -- no floor/ceiling clamp, unlike
    # stretch_factor()'s own min(max(1.0, ...), max_stretch), whose floor is exactly what misses this same
    # under-cpu-count contention band.
    # {CP3-T13} The arithmetic itself now lives in _cp3_t02_budget() (same constants: base_ceiling 90.0,
    # base_slope_per_load_point_at_12_threads 4.0, idle_load_avg 1.5), so CP3-T13 can unit-test the formula
    # directly without faking the environment or running this whole check.
    cpus = os.cpu_count()
    budget = _cp3_t02_budget(load_avg, cpus)
    load_allowance_per_point = 4.0 * (12.0 / (cpus or 12))
    load_allowance = budget - 90.0
    base_ceiling = 90.0  # comfortably above the idle real run, comfortably below the planted-regression run
    cp3_check("{CP3-T02} the REAL top-level solo invocation's own wall-clock (measured end-to-end in this very "
              "process, from _CP3_PROCESS_START to right before the final report, after every CP3-Tnn case has "
              f"run) is under its budget (a flat {base_ceiling:.0f}s ceiling plus {load_allowance_per_point:.1f}s "
              f"per point this box's own raw load average (read_load()[0] = {load_avg:.2f}) sits above an "
              f"idle baseline of {idle_load_avg:.1f} = {load_allowance:.1f}s allowance) = {budget:.1f}s)",
              real_elapsed < budget, (real_elapsed, budget, load_avg, elapsed))
    cp3_write_report()
    ok = sum(1 for v in CP3_STATUS.values() if v)
    bad = sum(1 for v in CP3_STATUS.values() if not v)
    print(f"\n{ok} CP3 cases passed, {bad} CP3 cases failed")
    return bad > 0


def main():
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            trust_helpers.FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    global _CP3_PARENT_SECTIONS_ELAPSED
    _CP3_PARENT_SECTIONS_ELAPSED = time.monotonic() - _CP3_PROCESS_START
    print(f"\n{trust_helpers.PASS} passed, {trust_helpers.FAIL} failed")
    exit_code = 1 if trust_helpers.FAIL else 0
    # CP3's own meta-cases run after the tally above (so that line's text never changes) and are skipped in the
    # child this file spawns of itself (see _cp3_run_fresh/_cp3_run_once) so that child doesn't try to run them
    # too, forever.
    if not os.environ.get(_CP3_NO_RECURSE_VAR):
        cp3_failed = run_cp3_cases()
        exit_code = 1 if (exit_code or cp3_failed) else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
