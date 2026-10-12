#!/usr/bin/env python3
"""
CP5 of the trust-holes run, part e: approvals answer the question Ratchet asked.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_e.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import _tmp
import trust_helpers
from trust_helpers import (
    ENGINE, check, cp4x_guard_tool, cp5r12_director, cp5v_until, cp5y_load_engine, engine_env, git, make_env,
    now_dt, project, rx,
)

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
    try:
        ts = [threading.Thread(target=run, args=(c,), name=f"cp5r13-{i}") for i, c in enumerate(calls)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
    finally:
        m._read_record = real
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
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    return d


def cp5_adv_round13_shared_record_lock():
    print("CP5 adv round 13: every read-modify-write of a shared record holds that record's lock, so none is lost")
    import contextlib
    import io
    m = cp5y_load_engine()
    src = ENGINE.read_text(encoding="utf-8")

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
    r = rx(projects[3], env, "init", "feature", "--slug", "f")
    runs = cp5r13_json(rh / "active-runs.json") or {}
    check("[CP5-X68] ... and a later run keeps them too (the record is read again under the lock, never a stale copy)",
          r.returncode == 0 and set(runs) >= want | {str(projects[3].resolve())}, (r.returncode, sorted(runs)))

    # --- X69: several director-enrolls at once: enrolled.json keeps every lane ---------------------------------
    lanes = projects[:3]
    for d in projects:
        cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    home_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r13-director-"))
    denv = dict(env, RATCHET_ROLE="director")
    k = cp5r12_director(home_dir, denv, lanes[0], "director-key")
    for d in projects:
        cp4x_guard_tool(home_dir, denv, "Bash", {"command": f"python3 {ENGINE} --root {d} director-enroll"},
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
    e = cp5r12_director(home_dir, denv, lane, "director-enroll")
    took = time.monotonic() - t0
    check("[CP5-X72] director-enroll while another process holds enrolled.json's lock waits a few seconds, then "
          "refuses plainly and changes nothing (enrolled.json as it was, no director.json in the lane)",
          e.returncode != 0 and "nothing was changed" in e.stderr and "enrolled.lock" in e.stderr
          and (rh / "enrolled.json").read_text() == before and not (lane / ".ratchet" / "director.json").exists()
          and lock.read_text() == "12345 another-process" and took < 60,
          (e.returncode, e.stderr[-400:], took))
    lock.unlink()
    e = cp5r12_director(home_dir, denv, lane, "director-enroll")
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
    d = project(env)
    rp = d / ".ratchet"

    # --- reviewer-status --reset while unavailable-reviewers.json's lock is held --------------------------------
    ulock = rp / "unavailable-reviewers.lock"
    ulock.write_text("12345 another-process")  # fresh: a live holder
    ufile = rp / "unavailable-reviewers.json"
    before = ufile.read_text() if ufile.exists() else None
    r = rx(d, env, "reviewer-status", "--reset", "codex-correctness")
    check("[CP5-X73] reviewer-status --reset while unavailable-reviewers.json's lock is held by another process "
          "dies plainly, names the lock, and does not claim codex-correctness will be tried again",
          r.returncode != 0 and "will be tried again" not in r.stdout
          and "unavailable-reviewers.lock" in r.stderr
          and (ufile.read_text() if ufile.exists() else None) == before,
          (r.returncode, r.stdout, r.stderr[-400:]))

    # --- reviewer-status --mark: same lock held, same outcome ------------------------------------------------------
    r = rx(d, env, "reviewer-status", "--mark", "codex-correctness", "--reason", "quota")
    check("[CP5-X73] reviewer-status --mark while the same lock is held dies plainly and does not claim "
          "codex-correctness was skipped",
          r.returncode != 0 and "skipped until" not in r.stdout and "unavailable-reviewers.lock" in r.stderr
          and (ufile.read_text() if ufile.exists() else None) == before,
          (r.returncode, r.stdout, r.stderr[-400:]))
    ulock.unlink()
    r = rx(d, env, "reviewer-status", "--reset", "codex-correctness")
    check("[CP5-X73] ... and once the lock is free, --reset works and says so",
          r.returncode == 0 and "will be tried again" in r.stdout, (r.returncode, r.stdout, r.stderr[-300:]))

    # --- providers --add --no-check while provider-status.json's lock is held --------------------------------------
    plock = rp / "provider-status.lock"
    plock.write_text("12345 another-process")  # fresh: a live holder
    cfg_before = (rp / "config.json").read_text()
    r = rx(d, env, "providers", "--add", "cursor", "--preset", "cursor", "--company", "cursorco", "--no-check")
    check("[CP5-X73] providers --add --no-check while provider-status.json's lock is held dies plainly, names the "
          "lock, and does not add the provider or claim it can now review",
          r.returncode != 0 and "It can now review" not in r.stdout and "provider-status.lock" in r.stderr
          and (rp / "config.json").read_text() == cfg_before,
          (r.returncode, r.stdout, r.stderr[-400:]))
    plock.unlink()
    r = rx(d, env, "providers", "--add", "cursor", "--preset", "cursor", "--company", "cursorco", "--no-check")
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
    src = ENGINE.read_text(encoding="utf-8")

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
    d = project(env)
    rp = d / ".ratchet"
    unavail = rp / "unavailable-reviewers.json"
    unavail.write_text(json.dumps({"mytool-correctness": {"reason": "test request failed: quota",
                                                           "since": "2026-01-01T00:00:00+00:00",
                                                           "until": "2099-01-01T00:00:00+00:00"}}))
    before = unavail.read_text()
    cfg_before = (rp / "config.json").read_text()
    ulock = rp / "unavailable-reviewers.lock"
    ulock.write_text("12345 another-process")  # fresh: a live holder
    r = rx(d, env, "providers", "--add", "mytool", "--command", '["sh", "-c", "echo RATCHET-PROBE-OK"]',
           "--company", "otherco")
    check("[CP5-X74] providers --add (checked) while unavailable-reviewers.json's lock is held dies plainly, names "
          "the lock, and does not claim the provider can now review",
          r.returncode != 0 and "It can now review" not in r.stdout and "unavailable-reviewers.lock" in r.stderr
          and unavail.read_text() == before and (rp / "config.json").read_text() == cfg_before,
          (r.returncode, r.stdout, r.stderr[-400:]))
    ulock.unlink()
    r = rx(d, env, "providers", "--add", "mytool", "--command", '["sh", "-c", "echo RATCHET-PROBE-OK"]',
           "--company", "otherco")
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
    src = ENGINE.read_text(encoding="utf-8")

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
    d = project(env)
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
        r = rx(d, env, *add)
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
    r = rx(d, env, *mark)
    retry = retry_of(r)
    check("[CP5-X77] reviewer-status --mark refused while busy suggests the whole command again, --reason included",
          r.returncode != 0 and retry[-len(mark):] == mark, (retry, r.stderr[-300:]))
    ulock.unlink()
    r = subprocess.run(retry, cwd=d, env=env, capture_output=True, text=True) if retry else None
    got = json.loads(unavail.read_text()) if unavail.exists() else {}
    check("[CP5-X77] ... and running it as suggested marks the reviewer with the user's own reason",
          r is not None and r.returncode == 0 and (got.get("codex-correctness") or {}).get("reason") == mark[-1],
          (r and (r.returncode, r.stdout, r.stderr[-300:]), got))


SECTIONS.append(("CP5", cp5_adv_round17_wrapper_scan_and_all_or_nothing_add))


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
