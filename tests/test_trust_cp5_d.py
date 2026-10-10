#!/usr/bin/env python3
"""
CP5 of the trust-holes run, part d: approvals answer the question Ratchet asked.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_d.py
"""
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

import _tmp
import trust_helpers
from trust_helpers import (
    ENGINE, HAVE_PTY, check, cp3_state, cp4_find_code, cp4_pty_run, cp4_say_yes, cp4_unapproved_plan,
    cp4x_guard_tool, cp5r12_director, cp5r8_sha, cp5v_until, cp5y_first_run_plan, cp5y_guard_bash, cp5y_load_engine,
    cp5y_repo, cp5z_notes, cp5z_project_notes, engine_env, install, js, make_env, rx, stop_payload,
)

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
    return subprocess.Popen([sys.executable, str(script), str(ENGINE), role, str(marks)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def cp5_adv_round10_damaged_records_and_breaker():
    print("CP5 adv round 10: damaged per-user records fail safe; a stale short lock is cleared by one process only")
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r10-"))
    corrupt = '{"a": [1,'

    # --- X51: a corrupt engine-call-sessions.json: the guard notes over it, `ask` uses no note, nothing crashes ----
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp5y_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    notes_p = rh / "engine-call-sessions.json"
    notes_p.write_text(corrupt)
    g = cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-D")
    check("[CP5-X51] a guard hook over a corrupt engine-call-sessions.json allows the call, records no hook crash "
          "and says plainly on stderr that the file could not be read",
          g.returncode == 0 and "deny" not in g.stdout and "SystemExit" not in cp5r10_hook_crashes(d)
          and "Traceback" not in g.stderr and "engine-call-sessions.json could not be read" in g.stderr,
          (g.returncode, g.stdout[-200:], g.stderr[-300:], cp5r10_hook_crashes(d)[-300:]))
    check("[CP5-X51] ... and its note replaces the corrupt file, which is valid JSON again",
          [x.get("session") for x in (cp5z_project_notes(d, env).get(f"{d.resolve()}|ask") or [])] == ["s-D"],
          notes_p.read_text()[:300])
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X51] the real `ask approve-plan` then uses that note: its chat owns the run and is the asker",
          r.returncode == 0 and st.get("owner_session") == "s-D" and (st.get("pending") or {}).get("session") == "s-D",
          (r.returncode, r.stderr[-300:], st.get("owner_session")))
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp5y_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    notes_p = rh / "engine-call-sessions.json"
    notes_p.write_text(corrupt)
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X51] `ask approve-plan` over a corrupt notes file exits 0 with the no-note rules (no owner, no "
          "asker), says so plainly, and never prints `is not valid JSON` or a traceback",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None
          and "engine-call-sessions.json could not be read" in r.stderr and "is not valid JSON" not in r.stderr
          and "Traceback" not in r.stderr, (r.returncode, r.stderr[-400:], st.get("owner_session")))
    g = cp5y_guard_bash(d, env, "$RATCHET ask human-approve", "s-E")
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
    g = rx(folder, dict(env, RATCHET_ROLE="director"), "hook", "guard", "--host", "claude",
           stdin=json.dumps(payload))
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
    d, tp = cp5y_first_run_plan(env)
    ch = rh / "file-hashes.json"
    for bad in (corrupt, "[]"):
        ch.write_text(bad)
        r = rx(d, env, "ask", "approve-plan")
        check(f"[CP5-X53] with file-hashes.json damaged ({bad!r}), a command that reads state.json refuses with a "
              "plain message naming the hash record and accept-state, and no traceback",
              r.returncode != 0 and "record of file hashes" in r.stderr and "accept-state" in r.stderr
              and "Traceback" not in r.stderr and (cp3_state(d).get("pending") or {}) == {},
              (r.returncode, r.stderr[-400:]))
        prog = ("import importlib.util, sys\nfrom pathlib import Path\n"
                "spec = importlib.util.spec_from_file_location('r', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(m)\nm.record_chain(Path(sys.argv[2]))\nprint('done')\n")
        w = subprocess.run([sys.executable, "-c", prog, str(ENGINE), str(d / ".ratchet/runs/f/state.json")], cwd=d,
                           env=env, capture_output=True, text=True)
        check(f"[CP5-X53] ... and a write of a new hash ({bad!r}) does not exit and leaves the damaged record as it "
              "is (a fresh record would drop every other file's hash), saying so on stderr",
              w.returncode == 0 and w.stdout.strip() == "done" and ch.read_text() == bad
              and "file-hashes.json could not be read" in w.stderr, (w.returncode, w.stderr[-300:], ch.read_text()))
    if not HAVE_PTY:
        print("  - [CP5-X53] accept-state rebuild not run: no pty on this platform (Windows)")
    else:
        rc, out = cp4_pty_run(["accept-state"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp5_answer_prompt_code)
        try:
            rec = json.loads(ch.read_text())
        except ValueError:
            rec = None
        sp = (d / ".ratchet/runs/f/state.json").resolve()
        sha_then = cp5r8_sha(sp)
        r = rx(d, env, "ask", "approve-plan")
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
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    cp5r11_prompt(d, tp, env, "hello")
    rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(dict(stop_payload(d), session_id="s-owner")))
    cp5y_guard_bash(d, env, "ls", "s-owner")
    return d, tp


def cp5r11_prompt(d, tp, env, text):
    return cp4_say_yes(d, tp, env, text=text, session="s-owner")


def cp5r11_errlog(d):
    p = Path(d) / ".ratchet" / "hook-errors.log"
    return p.read_text().splitlines() if p.exists() else []


CP5_PROMPT_CODE_RE = re.compile(r"type this code and press Enter: (\d{4,8})(?!\d)")


def cp5_answer_prompt_code(master, output):
    """Types back the code from Ratchet's own prompt line only. A damaged record makes the engine print a warning
    naming its path before the prompt, and a temp folder name can hold a run of digits (for example
    ratchet-trust-cp4-8871g3bi): reading the first number in the output would type that back instead."""
    m = CP5_PROMPT_CODE_RE.search(output)
    if not m:
        return False
    os.write(master, (m.group(1) + "\n").encode())
    return True


def cp5r11_accept(d, env):
    if not HAVE_PTY:
        return None, ""
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
        d, tp = cp5y_first_run_plan(env)
        ch = rh / "file-hashes.json"
        ch.write_text(_CP5R11_CORRUPT)
        log = d / ".ratchet" / "human_log.jsonl"
        before = log.read_text() if log.exists() else ""
        r = rx(d, env, "hook", "prompt", "--host", "claude",
               stdin=json.dumps({"session_id": "s-A", "cwd": str(d), "prompt": "yes"}))
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
        rc, out = cp5r11_accept(d, env)
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
        r = rx(d, env, "ask", "approve-plan")
        check(f"[CP5-X56] ({label}) after a fresh message, `ask approve-plan` works again",
              r.returncode == 0 and "ASK THE USER" in r.stdout and "Refused" not in r.stderr,
              (r.returncode, r.stderr[-400:]))

    # --- X57: damaged per-user stop-hook records: the stop hook never crashes and no command is refused ---------
    for name in ("stop-errors.json", "active-runs.json"):
        for bad in (_CP5R11_CORRUPT, "[]"):
            env = make_env("claude")
            rh = Path(env["RATCHET_HOME"])
            d, tp = cp5y_first_run_plan(env)
            rh.mkdir(parents=True, exist_ok=True)
            (rh / name).write_text(bad)
            s = rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(d)))
            s2 = rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(d)))
            r = rx(d, env, "ask", "approve-plan")
            check(f"[CP5-X57] a damaged {name} ({bad!r}): the real stop hook records no crash and says so plainly, "
                  "and `ask approve-plan` is not refused",
                  s.returncode in (0, 2) and s2.returncode in (0, 2) and cp5r11_errlog(d) == []
                  and "Traceback" not in s.stderr + s2.stderr and f"{name} could not be read" in s.stderr
                  and r.returncode == 0 and "until that hook runs cleanly" not in r.stderr,
                  (s.returncode, s.stderr[-300:], cp5r11_errlog(d), r.returncode, r.stderr[-300:]))
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp5y_first_run_plan(env)
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
        g = rx(folder, dict(env, RATCHET_ROLE="director"), "hook", "guard", "--host", "claude",
               stdin=json.dumps(payload))
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
    bad = cp5r11_io_violations(src)
    check("[CP5-X60] ratchet.py reads JSON files only through _read_record (read_json without `record=` only for "
          "files people or agents write) and writes files only through _write_record / _append_record, apart from "
          "a short allow-list of files that are not Ratchet's records",
          bad == [], bad[:10])
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
    fns = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
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
            p2 = rx(d, env, "hook", "stop", "--host", "claude",
                    stdin=json.dumps(dict(stop_payload(d), session_id="s-owner")))
            p3 = cp5y_guard_bash(d, env, "ls", "s-owner")
            hooks_ok = all(h.returncode in (0, 2) for h in (p1, p2, p3))
            for args in (["status"], ["next"], ["doctor"]):
                r = rx(d, env, *args)
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
                rc2, out2 = cp4_pty_run(["init", "feature again", "--slug", "f2", "--force"], d,
                                        dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp5_answer_prompt_code)
                s = rx(d, env, "status", "--json")
                check(f"[CP5-X61] {name} damaged ({bad!r}): the run is not stuck: `init --force`, confirmed at a "
                      "terminal, starts the feature over and status works",
                      rc2 == 0 and s.returncode == 0 and (js(s) or {}).get("slug") == "f2",
                      (rc2, out2[-400:], s.returncode, s.stderr[-300:]))
                continue
            cp5r11_prompt(d, tp, env, "please carry on")
            r = rx(d, env, "ask", "approve-plan")
            s = rx(d, env, "status", "--json")
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
    s = rx(d, env, "status", "--json")
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
    src = ENGINE.read_text(encoding="utf-8")

    # --- X63: a damaged enrolled.json is never replaced by a later enrollment, and keeps restricting ------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    lanes = []
    for _ in range(3):
        d, tp = cp4_unapproved_plan(env)
        cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
        lanes.append((d, tp))
    (a, tpa), (b, _tpb), (c, _tpc) = lanes
    home_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r12-director-"))
    denv = dict(env, RATCHET_ROLE="director")
    k = cp5r12_director(home_dir, denv, a, "director-key")
    ea = cp5r12_director(home_dir, denv, a, "director-enroll")
    eb = cp5r12_director(home_dir, denv, b, "director-enroll")
    enrolled = rh / "enrolled.json"
    try:
        names = set(json.loads(enrolled.read_text()))
    except (OSError, ValueError):
        names = set()
    setup = (k.returncode == 0 and ea.returncode == 0 and eb.returncode == 0
             and names == {str(a.resolve()), str(b.resolve())})
    enrolled.write_text(_CP5R11_CORRUPT)
    ec = cp5r12_director(home_dir, denv, c, "director-enroll")
    check("[CP5-X63] director-enroll over a damaged enrolled.json refuses and changes nothing (the record stays as it "
          "is and the new lane is not enrolled), naming the file and how to repair it",
          setup and ec.returncode != 0 and enrolled.read_text() == _CP5R11_CORRUPT
          and not (c / ".ratchet" / "director.json").exists() and "enrolled.json" in ec.stderr
          and "won't write" in ec.stderr and "director-enroll again" in ec.stderr and "Traceback" not in ec.stderr,
          (setup, k.stderr[-200:], ea.stderr[-200:], ec.returncode, ec.stderr[-400:]))
    (a / ".ratchet" / "director.json").unlink()
    s = rx(a, env, "status", "--json")
    check("[CP5-X63] a lane whose director.json is then removed stays in director mode: the damaged backstop keeps "
          "its restriction while a director key exists, and says so on stderr",
          (js(s) or {}).get("approval_mode") == "director" and "enrolled.json could not be read" in s.stderr,
          (s.returncode, s.stdout[-300:], s.stderr[-300:]))
    rx(a, env, "ask", "approve-plan")
    cp4_say_yes(a, tpa, env, text="yes, go ahead")
    r = rx(a, env, "approve-plan")
    check("[CP5-X63] ... so a chat yes in that lane approves nothing", r.returncode != 0
          and cp3_state(a)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr[-300:]))
    dr = rx(home_dir, env, "doctor")
    check("[CP5-X63] doctor names the damaged enrolled.json and how to repair it",
          "enrolled.json can't be read" in dr.stdout and "director-enroll again" in dr.stdout, dr.stdout[-600:])
    enrolled.rename(enrolled.with_name("enrolled.json.damaged"))
    ec2 = cp5r12_director(home_dir, denv, c, "director-enroll")
    try:
        names = set(json.loads(enrolled.read_text()))
    except (OSError, ValueError):
        names = set()
    check("[CP5-X63] the repair it names works: once the damaged file is moved aside, director-enroll writes it again",
          ec2.returncode == 0 and str(c.resolve()) in names, (ec2.returncode, ec2.stderr[-300:], names))

    # --- X64: a damaged active-runs.json is never replaced by another project's run -----------------------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    p1, _tp1 = cp4_unapproved_plan(env)
    markers = rh / "active-runs.json"
    try:
        had = str(p1.resolve()) in json.loads(markers.read_text())
    except (OSError, ValueError):
        had = False
    markers.write_text(_CP5R11_CORRUPT)
    p2, _tp2 = cp4_unapproved_plan(env)
    with engine_env(env):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            m.register_active(p2, "f")
            m.register_active(p2, None)
    dr = rx(p2, env, "doctor")
    check("[CP5-X64] a damaged active-runs.json is left as it is when another project's run starts or ends (it holds "
          "other projects' runs), and stderr and doctor say how to repair it",
          had and markers.read_text() == _CP5R11_CORRUPT and (p2 / ".ratchet" / "runs" / "f" / "state.json").exists()
          and "did not write over it" in err.getvalue() and "To repair it" in err.getvalue()
          and "active-runs.json can't be read" in dr.stdout and dr.returncode == 0,
          (had, markers.read_text()[:80], err.getvalue()[-400:], dr.stdout[-400:]))
    shutil.rmtree(p1 / ".ratchet")
    st = rx(p1, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(p1)))
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

    real_os = m.os
    m.os = _Cp5r12OsProxy(real_os, hook)
    try:
        t1 = threading.Thread(target=run, args=("P1", p1l, 2.0), name="P1")
        t2 = threading.Thread(target=run, args=("P2", p2l, 0.5), name="P2")
        t1.start()
        t2.start()
        t1.join(15)
        t2.join(15)
    finally:
        m.os = real_os
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
          "def _remove_stale_breaker" in src and "os.unlink(self.breaker)\n                        continue" not in src)

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
    naive = cp4_find_code(acc)
    warned_at = acc.find(str(d / ".ratchet" / "active.json"))
    check("[CP5-X61] in a project folder whose name holds a run of digits, accept-state over a damaged active.json "
          "(its warning names that path before the code) still gets the right code and writes active.json again",
          "4321x" in str(d) and prompt is not None and 0 <= warned_at < prompt.start() and naive is not None
          and naive in str(d) and naive != prompt.group(1) and rc == 0 and act == {"slug": "f"},
          (str(d), rc, acc[:700], act))


SECTIONS.append(("CP5", cp5_flaky_x61_prompt_code))


def main():
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            trust_helpers.FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{trust_helpers.PASS} passed, {trust_helpers.FAIL} failed")
    sys.exit(1 if trust_helpers.FAIL else 0)


if __name__ == "__main__":
    main()
