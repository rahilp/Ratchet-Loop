#!/usr/bin/env python3
"""
Shared helpers for the trust-hole suites (tests/test_trust_cp1.py, test_trust_cp3.py, test_trust_cp4.py
and test_trust_cp5_*.py) and for tests/test_results.py and tests/test_signing.py. Not a suite itself.

Moved verbatim from the old tests/test_trust.py: everything runs in throwaway git projects with a private
HOME / RATCHET_HOME. Agent-CLI env vars that could leak host detection in from the real environment are
scrubbed, and the PATH given to subprocesses only contains a handful of basic tools (git, sh, coreutils)
resolved from the real PATH by name - never claude/codex/gemini/cursor-agent/opencode/copilot, even though
this machine has all of those installed.
"""
import datetime as dt
import functools
import importlib.util
import io
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import _tmp
import time
import traceback
from pathlib import Path

try:  # pty / os.forkpty give a test a REAL controlling terminal; absent on Windows
    import pty as _pty_mod  # noqa: F401  (imported only to prove the module exists)
    HAVE_PTY = hasattr(os, "forkpty")
except ImportError:
    HAVE_PTY = False

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
PASS = FAIL = 0
VENDOR = {"claude": "anthropic", "codex": "openai", "gemini": "google", "copilot": "github"}

# Agent-CLI env vars an agent's own shell may set; a hermetic test must not inherit them.
_DROP_PREFIXES = ("RATCHET_", "CODEX_", "COPILOT_")
_DROP_EXACT = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "GEMINI_CLI", "GITHUB_COPILOT_CLI"}

# Tools a throwaway git project and the engine itself need; deliberately excludes every agent CLI binary name
# (claude, codex, gemini, cursor-agent, opencode, copilot) so they stay unreachable regardless of the real PATH.
_SAFE_TOOLS = ("git", "python3", "sh", "env", "cat", "mkdir", "rm", "ls", "dirname", "basename", "true", "false",
               "uname", "chmod", "mv", "cp", "sed", "grep", "find", "touch", "date", "sort", "head", "tail", "wc",
               "readlink", "printf", "echo")


# The two glyphs check() prints a result line with. [CP2] A second copy of this format (CP2's own cp2_check() in
# test_trust_cp5_e.py, and the regex it uses to tally bracket ids out of a captured run's stdout) reads these same
# two constants instead of hand-copying the glyphs, so a future change here can't silently desync that parsing.
MARK_PASS, MARK_FAIL = "✓", "✗"


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {MARK_PASS if cond else MARK_FAIL} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def _safe_path():
    b = Path(_tmp.mkdtemp(prefix="ratchet-trust-bin-"))
    for n in _SAFE_TOOLS:
        p = shutil.which(n)
        if p:
            try:
                (b / n).symlink_to(p)
            except OSError:
                pass
    return str(b)


SAFE_PATH = _safe_path()


def scrub(env):
    return {k: v for k, v in env.items() if not k.startswith(_DROP_PREFIXES) and k not in _DROP_EXACT}


def make_env(host="claude"):
    home = Path(_tmp.mkdtemp(prefix="ratchet-trust-home-"))
    env = scrub(os.environ)
    env.update(HOME=str(home), RATCHET_HOME=str(home / "rh"), RATCHET_HOST_PROVIDER=VENDOR[host], PATH=SAFE_PATH)
    return env


def rx(d, env, *args, stdin=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True,
                          env=env, input=stdin)


def git(d, *a):
    r = subprocess.run(["git", *a], cwd=d, capture_output=True, text=True,
                       env=dict(os.environ, PATH=SAFE_PATH))
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def js(r):
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def errors_path(d):
    return Path(d) / ".ratchet" / "hook-errors.log"


def hb_path(d):
    return Path(d) / ".ratchet" / "hook-heartbeat.json"


def now_dt():
    return dt.datetime.now(dt.timezone.utc)


def iso_ago(seconds):
    return (now_dt() - dt.timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def log_error_at(d, event, host, at_iso, detail="KeyError: 'missing'"):
    with errors_path(d).open("a") as f:
        f.write(f"{at_iso} {host} {event}: {detail}\n")


def set_heartbeat(d, event, host, at_iso):
    hb = json.loads(hb_path(d).read_text()) if hb_path(d).exists() else {}
    hb[event] = {"at": at_iso, "host": host}
    hb_path(d).write_text(json.dumps(hb))


def prompt_payload(d):
    return {"session_id": "s", "cwd": str(d), "hook_event_name": "UserPromptSubmit", "prompt": "hello"}


def stop_payload(d):
    return {"session_id": "s", "cwd": str(d), "hook_event_name": "Stop", "stop_hook_active": False}


def guard_payload(d, path=None):
    return {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(path or (d / "src/a.js"))}}


def fire_claude(d, env, prompt_only=False):
    r = rx(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps(prompt_payload(d)))
    if not prompt_only:
        r = rx(d, env, "hook", "stop", "--host", "claude", stdin=json.dumps(stop_payload(d)))
    return r


def fire_guard(d, env, path=None):
    return rx(d, env, "hook", "guard", "--host", "claude", stdin=json.dumps(guard_payload(d, path)))


def install(d, env, host="claude", scope="project"):
    r = rx(d, env, "install-hooks", "--host", host, "--scope", scope)
    assert r.returncode == 0, r.stderr + r.stdout
    return r


def project(env, approved=True):
    """A throwaway git project, approved into the building phase (CP1 the only checkpoint), hooks installed and
    firing for host claude - mirrors tests/test_hooks_status.py's project()."""
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
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    if approved:
        run = d / ".ratchet/runs/f"
        rx(d, env, "config", "--set", "min_review_seconds=0")
        (run / "app-scout.json").write_text("{}")
        cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
              "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
              "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
        (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
        (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
        r = rx(d, env, "plan-submit")
        assert r.returncode == 0, r.stderr + r.stdout
        rx(d, env, "ask", "approve-plan")
        tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                       "timestamp": now_dt().isoformat()}) + "\n")
        rx(d, env, "hook", "prompt", "--host", "claude",
           stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                             "transcript_path": str(tp)}))
        r = rx(d, env, "approve-plan")
        assert r.returncode == 0, r.stderr + r.stdout
    install(d, env, "claude")
    fire_claude(d, env)
    return d


def engine():
    """A freshly loaded engine module, one new module object per call. [CP2] Deliberately NOT memoized: unlike
    cp5y_load_engine() below, this loader is called by test_trust_cp3.py's CP3-X03/X04 cases, which monkeypatch
    a module-level function (e.g. m._read_round_review) mid-section and rely on the NEXT engine() call starting
    from a pristine module to undo it -- caching this one reintroduces the patched function into later sections
    (confirmed: with lru_cache here, test_trust_cp3.py crashes with RecursionError from the stacked monkeypatches).
    cp5y_load_engine()'s callers were all audited and every one restores any monkeypatch itself (try/finally,
    including test_lanes_start_extra.py's write_fails(), which restores both _write_record and
    require_director_session), so only that loader is safe to memoize."""
    spec = importlib.util.spec_from_file_location("ratchet_engine_trust", str(ENGINE))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def close(iso_a, iso_b, tol=2.0):
    """True when two ISO timestamps are within `tol` seconds (tolerant of reformatting)."""
    try:
        a = dt.datetime.fromisoformat(str(iso_a).replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(str(iso_b).replace("Z", "+00:00"))
        return abs((a - b).total_seconds()) <= tol
    except Exception:
        return False


def hook_errors_of(obj):
    return obj.get("hook_errors") if isinstance(obj, dict) else None


# ---- helpers from the CP sections that more than one test file uses (moved unchanged, in their old order) ----

class engine_env:
    """Run in-process engine code with the test project's HOME / RATCHET_HOME / host, never the real ones."""

    def __init__(self, env):
        self.env, self.saved = env, {}

    def __enter__(self):
        for k in ("HOME", "RATCHET_HOME", "RATCHET_HOST_PROVIDER"):
            self.saved[k] = os.environ.get(k)
            os.environ[k] = self.env[k]
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def cp3_project(env, extra_cp=None):
    """A throwaway git project with one checkpoint, approved into building (mirrors tests/test_gates.py's project(),
    adapted to this file's env-carrying rx())."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp3-"))
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
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          # No node/npm on the hermetic PATH: "red" fails (no src/f.js yet) and "behavior" passes once
          # cp3_pass_behavior() creates it, without needing a real JS test runner.
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    if extra_cp:
        cp.update(extra_cp)
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def cp3_ui_project(env):
    return cp3_project(env, extra_cp={"gates": {"behavior": True, "ui": True, "adversarial": True},
                                      "ui_scope": "all", "ui_states": [{"name": "empty"}]})


def cp3_write_tests(d, cp="CP1"):
    (d / f"tests/{cp.lower()}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f.js';\n"
        f"t('[{cp}-T01] works', () => a.equal(f(), 1));\n")


def cp3_pass_behavior(d, env, cp="CP1"):
    """Drives one checkpoint through tests-written / red / implemented / behavior, landing on the earliest open
    step after behavior (ui or adversarial, depending on the checkpoint's gates)."""
    cp3_write_tests(d, cp)
    assert rx(d, env, "tests-written", cp).returncode == 0
    assert rx(d, env, "red", cp).returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert rx(d, env, "implemented", cp).returncode == 0
    r = rx(d, env, "behavior", cp)
    assert r.returncode == 0, r.stderr + r.stdout


def cp3_open_round(d, env, cp="CP1"):
    r = rx(d, env, "reviewers", cp, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def cp3_R0(rnd):
    return rnd["reviewers"][0]["id"]


def cp3_write_review(path, rnd, rid, findings=None, **over):
    body = {"reviewer": rid, "checkpoint": rnd["checkpoint"], "fingerprint": rnd["fingerprint"],
            "round_nonce": rnd["round_nonce"], "findings": findings or [], "files_reviewed": ["src/f.js"]}
    body.update(over)
    Path(path).write_text(json.dumps(body))
    return str(path)


def cp3_reviewer_writes(d, env, rnd, rid):
    """A host review written the way the host's ratchet-adversary sub-agent writes it (CP7: on Claude Code a review
    counts only when the guard saw that sub-agent write it), for helpers that need an accepted review to go on."""
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    g = rx(d, env, "hook", "guard", "--host", "claude",
           stdin=json.dumps({"cwd": str(d), "tool_name": "Write", "tool_input": {"file_path": str(path)},
                             "agent_type": "ratchet-adversary", "agent_id": "a-ratchet-adversary"}))
    assert g.returncode == 0, g.stderr + g.stdout
    return cp3_write_review(path, rnd, rid)


def cp3_fp(d, env):
    return json.loads(rx(d, env, "fingerprint").stdout)["id"]


def cp3_ui_round_dir(d, n, cp="CP1"):
    p = Path(d) / ".ratchet/runs/f/reviews" / cp / f"round-{n:02d}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cp3_write_combined(path, fp, cp="CP1", compared=None, diffs=None):
    body = {"checkpoint": cp, "fingerprint": fp, "reviewers": ["visual", "behavior"],
            "compared": compared or {"spec_sha256": "s1", "scope": "all"}, "differences": diffs or []}
    Path(path).write_text(json.dumps(body))
    return str(path)


def cp3_signed_combined(d, env, fp, cp="CP1", n=1, diffs=None, spec_sha256="s1"):
    """A combined.json for round `n`, signed the way ui_review.py's `combine` step signs it (CP7: a hand-typed
    combined review no longer counts). Used wherever a test needs an *accepted* combined review to reach the
    behavior under test, not the signature check itself (that's CP7's own tests, in test_signing.py)."""
    import test_signing as ts  # safe here: by the time this runs, either module is already fully imported
    round_dir = cp3_ui_round_dir(d, n, cp)
    # CP7 round 2: the capture manifest starts as capture.mjs writes it (checkpoint, spec hash, states; no
    # fingerprint), and `stage` stamps it with the code version and scope and signs it for this round: combine
    # refuses a manifest that ui_review.py did not sign.
    manifest = Path(d) / ".ratchet/runs/f/reviews" / cp / "capture" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"checkpoint": cp, "spec_sha256": spec_sha256, "states": []}))
    # CP7 round 1: combine only signs per-role reviews whose origin checks out, so the two files are written the
    # way the host's reviewer sub-agents write them: `stage` opens the round, and the guard sees each one written.
    st = subprocess.run([sys.executable, str(ts.UI_REVIEW), "stage", "--manifest", str(manifest), "--scope", "all"],
                        cwd=d, capture_output=True, text=True, env=env)
    assert st.returncode == 0, st.stderr + st.stdout
    for tmp in json.loads(st.stdout).values():
        shutil.rmtree(tmp, ignore_errors=True)
    stamped = json.loads(manifest.read_text())
    assert stamped.get("fingerprint") == fp and isinstance(stamped.get("signature"), dict), stamped
    visual = round_dir / "visual.json"
    behavior = round_dir / "behavior.json"
    for path, agent in ((visual, "ratchet-visual-reviewer"), (behavior, "ratchet-behavior-reviewer")):
        g = ts.guard(d, env, "Write", {"file_path": str(path)}, host="claude", agent=agent)
        assert g.returncode == 0, g.stderr + g.stdout
    visual.write_text(json.dumps({"reviewer": "visual", "verdict": "PASS" if not diffs else "FAIL",
                                  "differences": diffs or []}))
    behavior.write_text(json.dumps({"reviewer": "behavior", "verdict": "PASS", "differences": []}))
    out = round_dir / "combined.json"
    rp = ts.run_combine(d, env, [visual, behavior], out, cp, manifest)
    assert rp.returncode == 0, rp.stderr + rp.stdout
    return out


def cp3_state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


# The engine always prints the override code on its own prompt line, as exactly 6 digits (ratchet.py:
# f"{secrets.randbelow(10 ** 6):06d}"). Anchoring to that literal prompt text and fixing the length to 6 - rather
# than hunting for any 4-8 digit run anywhere in the captured pty output - avoids two failure modes that only show
# up when several of these pty tests run at once under load: (1) a false match on unrelated digits printed earlier
# (a PID, a temp-folder name that happens to hold a run of digits); and (2) a false *miss* when the test's own
# echoed answer lands, under scheduling delay, before the rest of the engine's prompt write has been delivered,
# concatenating the printed code and its echo into one long digit run that a variable-length pattern can no longer
# match at all (confirmed by reproduction: captured output like "...Enter: 104774104774\r\n\r\n> RATCHET ✓ ...",
# the real code immediately followed by its own echoed answer, with the old (?<!\d)(\d{4,8})(?!\d) pattern matching
# nothing in that 12-digit run).
CP4_CODE_RE = re.compile(r"type this code and press Enter: (\d{6})")


def cp4_find_code(output):
    m = CP4_CODE_RE.search(output)
    return m.group(1) if m else None


def cp4_pty_run(args, cwd, env, on_output, timeout=15):
    """Runs `ratchet.py <args>` attached to a REAL controlling terminal (os.forkpty - the same mechanism a real
    shell uses), never the test runner's own tty. `on_output(master_fd, output_so_far)` is called after every chunk
    read until it returns a truthy value: True (answered; keep draining to EOF/exit) or "close" (answered by
    closing the master fd itself, modelling the terminal going away before any reply). Returns (returncode, the
    full captured text)."""
    pid, master = os.forkpty()
    if pid == 0:
        try:
            os.chdir(str(cwd))
            os.environ.clear()
            os.environ.update(env)
            os.execvp(sys.executable, [sys.executable, str(ENGINE), *args])
        except Exception:
            pass
        os._exit(127)
    output, done, closed, exited, status = b"", False, False, False, 0
    deadline = time.time() + timeout
    while time.time() < deadline and not exited:
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
            exited = wpid == pid
        except OSError:
            pass
        if closed:
            if not exited:
                time.sleep(0.05)
            continue
        try:
            r, _, _ = select.select([master], [], [], 0.2)
        except OSError:
            closed = True
            continue
        if not r:
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            closed = True
            continue
        output += chunk
        if not done:
            action = on_output(master, output.decode("utf-8", "replace"))
            if action:
                done = True
                if action == "close":
                    try:
                        os.close(master)
                    except OSError:
                        pass
                    closed = True
    if not exited:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        _, status = os.waitpid(pid, 0)
    if not closed:
        try:
            os.close(master)
        except OSError:
            pass
    rc = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
    return rc, output.decode("utf-8", "replace")


def cp4_answer_correct(master, output):
    code = cp4_find_code(output)
    if not code:
        return False
    os.write(master, (code + "\n").encode())
    return True


def cp4_awaiting_review(env):
    """One checkpoint, behavior and adversarial gates both passed, parked at the final human review (the same
    human-approve flow as a per-checkpoint review: cmd_human_approve treats awaiting_checkpoint_review and
    awaiting_final_review alike), built from this file's own CP3 fixtures."""
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = cp3_reviewer_writes(d, env, rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", good)
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, env, "complete", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    phase = cp3_state(d)["phase"]
    assert phase in ("awaiting_checkpoint_review", "awaiting_final_review"), phase
    return d


def cp4_unapproved_plan(env):
    """A submitted plan parked awaiting_plan_approval (mirrors cp3_project, minus the final approve-plan)."""
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
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "config", "--set", "min_review_seconds=0")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    return d, tp


def cp4_say_yes(d, tp, env, text="yes, build it", session="s-owner"):
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    return rx(d, env, "hook", "prompt", "--host", "claude",
              stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": session, "transcript_path": str(tp)}))


def cp4_human_log_lines(d):
    p = Path(d) / ".ratchet" / "human_log.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def cp4x_guard_tool(cwd, env, tool, tool_input, session="s"):
    payload = {"session_id": session, "cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": tool_input}
    return rx(cwd, env, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))


def cp5_plan(env):
    """An unapproved plan parked in awaiting_plan_approval (reuses CP4's fixture)."""
    return cp4_unapproved_plan(env)


def cp5_say(d, tp, env, text, session="s-owner"):
    return cp4_say_yes(d, tp, env, text=text, session=session)


def cp5_project_each(env):
    """A one-checkpoint project with human_review=each, approved into building (mirrors cp3_project, but with the
    per-step review mode CP5-T07 needs: completing the only checkpoint still pauses for a step review)."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5-"))
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
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f", "--human-review", "each")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d, tp


def cp5_awaiting_checkpoint_review(env):
    """CP1 built and both gates passed, human_review=each: completing it lands in awaiting_checkpoint_review."""
    d, tp = cp5_project_each(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = cp3_reviewer_writes(d, env, rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", good)
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, env, "complete", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    phase = cp3_state(d)["phase"]
    assert phase == "awaiting_checkpoint_review", phase
    return d, tp


def cp5x_history(d, event=None):
    h = cp3_state(d).get("history", [])
    return [e for e in h if event is None or e.get("event") == event]


def cp5x_hint(d, tp, env, text, session="s-owner"):
    """Say `text` in `session` and return what the prompt hook told the agent."""
    r = cp5_say(d, tp, env, text, session=session)
    return r.stdout + r.stderr


def cp5x_frozen_ui_project(env):
    """CP1 with a UI gate whose first round failed, so its capture spec is frozen (ui-respec applies)."""
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    diffs = [{"severity": "minor", "location": "x", "description": "issue", "fixable_in_code": True}]
    combined = str(cp3_signed_combined(d, env, fp, diffs=diffs))
    r = rx(d, env, "ui-record", "CP1", "--review", combined)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"].get("frozen"), cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    return d


# [CP4 fix round 1] The one canonical copy of the engine-global reset list and lock-wait cap every in-process
# caller needs (see InProcessCall's own `reset_globals`/`lock_wait_cap` docstring below for why they exist).
# test_trust_cp5_e.py's `_CP2_RESET_GLOBALS`/`_CP2_LOCK_WAIT_CAP` and test_trust_cp5_d.py's
# `_CP3_RESET_GLOBALS`/`_CP3_LOCK_WAIT_CAP` are each checkpoint's own already-committed hand-copy of this same
# dict and value (left as-is: they are other checkpoints' files, out of scope for this fix); test_gates.py's
# CP4 block imports THESE instead of adding a third independent hand-copy that could drift from the other two
# unnoticed when ratchet.py gains or renames a module global (omp-glm-standards/X4, omp-glm-correctness/A5).
DEFAULT_RESET_GLOBALS = {"_IN_HOOK": False, "_OVERRIDE_CONFIRMED": False, "_CHAIN_REBUILD": False,
                         "_HOOK_ENV_ADVISORY": False, "_STATE_LOCK_HELD": False, "_ENGINE_ARGV": None}
DEFAULT_LOCK_WAIT_CAP = 0.3  # none of the fast-path callers' checks assert a MINIMUM wait on a busy per-record lock


@functools.lru_cache(maxsize=None)
def cp5y_load_engine():
    """The engine module, loaded once per test process (memoized) -- see engine()'s docstring: the real cost is
    recompiling ratchet.py's ~21k lines, so every call after the first, from any caller, gets back the identical
    module object (`is`, not just `==`), letting a converted in-process call reuse it instead of re-importing."""
    spec = importlib.util.spec_from_file_location("ratchet_cp5_round2", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class InProcessCall:
    """`with InProcessCall(m, d, env, reset_globals, lock_wait_cap) as r: m.main([...])` -- the in-process
    stand-in for one rx(...) subprocess call that test_trust_cp5_e.py's `_Cp2Call` and test_trust_cp5_d.py's
    `_Cp3Call` are both thin subclasses of (they only supply their own suite's `reset_globals`/`lock_wait_cap`/
    `reset_unreadable`; see cp5y_load_engine()'s own docstring for why reusing the loaded module this way is
    safe and what real cost it avoids paying again). `r.returncode` / `r.stdout` / `r.stderr` read the same way
    a subprocess.CompletedProcess's do.

    This class used to be two independent, hand-copied classes, one per suite; round-1 and round-2 adversarial
    review both found the copies had drifted (host-alt-standards: duplicated-test-helper). Moving the shared
    logic here, with the two suites' own differences passed in as constructor arguments instead of hand-copied
    code, means there is now exactly one place this logic can drift from itself.

    CONTRACT (not just an example): the with-body must contain exactly one `m.main([...])` call (or, for the one
    documented exception test_trust_cp5_d.py's own `_Cp3Call` docstring names, a plain module-function call plus
    a print) and nothing else. __exit__ converts any exception raised anywhere in the body into `returncode = 1`
    (the same way a real subprocess crashing looks to its caller) for ordinary Exception subclasses only --
    KeyboardInterrupt and any other non-Exception BaseException (e.g. a future SystemExit subclass misuse)
    propagate past this call site instead, because a real subprocess dying to Ctrl-C would stop the whole suite,
    not just fail one command.

    `reset_globals`: a {name: default} dict of the module's own per-call globals, put back to that default both
    before AND after the call -- not just before: a command that raises partway through (die()/SystemExit
    before its own cleanup runs) can leave one of these set, and the NEXT caller might not go through this class
    at all (e.g. test_trust_cp5_d.py's own CP3-T08 probe), so resetting on the way out too keeps the module
    pristine for whatever runs next.

    `reset_unreadable`: when True, also clears `m._NOTED_UNREADABLE` (a damaged-record warning the module prints
    "once per file and cause in a process") on entry and exit, so a prior call's warning about path X never
    silently suppresses a later call's real warning about that SAME path. Only test_trust_cp5_d.py's CP3 suite
    currently has a check that needs this (X64: an in-process call and an explicit register_active() call
    sharing one damaged active-runs.json); test_trust_cp5_e.py's CP2 suite has no equivalent check (checked:
    nothing there asserts a SECOND warning about the SAME damaged path across two separate in-process calls), so
    it passes False.

    `lock_wait_cap`: none of either suite's check()s assert a MINIMUM wait on a busy per-record lock, only an
    upper bound or a plain refusal, so capping how long this class's own calls wait for one shortens the suite
    without weakening what any check proves."""

    def __init__(self, m, d, env, reset_globals, lock_wait_cap, reset_unreadable=False, stdin=None):
        self.m, self.d, self.env, self.stdin = m, Path(d), env, stdin
        self.reset_globals, self.lock_wait_cap, self.reset_unreadable = reset_globals, lock_wait_cap, reset_unreadable
        self.returncode, self.stdout, self.stderr = 0, "", ""

    def __enter__(self):
        self._cwd = os.getcwd()
        self._environ = dict(os.environ)
        self._streams = (sys.stdout, sys.stderr, sys.stdin)
        missing = [k for k in self.reset_globals if not hasattr(self.m, k)]
        assert not missing, f"ratchet.py no longer has module global(s) {missing!r} -- update the reset list"
        for k, v in self.reset_globals.items():
            setattr(self.m, k, v)
        if self.reset_unreadable:
            self.m._NOTED_UNREADABLE.clear()
        self._record_lock_init = self.m._RecordLock.__init__

        def _fast_record_lock_init(rl_self, record, wait=5.0):
            self._record_lock_init(rl_self, record, min(wait, self.lock_wait_cap))
        self.m._RecordLock.__init__ = _fast_record_lock_init
        os.chdir(self.d)
        os.environ.clear()
        os.environ.update(self.env)
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        if self.stdin is not None:
            sys.stdin = io.StringIO(self.stdin)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stdout, self.stderr = sys.stdout.getvalue(), sys.stderr.getvalue()
        sys.stdout, sys.stderr, sys.stdin = self._streams
        os.chdir(self._cwd)
        os.environ.clear()
        os.environ.update(self._environ)
        self.m._RecordLock.__init__ = self._record_lock_init
        for k, v in self.reset_globals.items():
            setattr(self.m, k, v)
        if self.reset_unreadable:
            self.m._NOTED_UNREADABLE.clear()
        if exc_type is SystemExit:
            code = exc.code
            self.returncode = code if isinstance(code, int) else (0 if code is None else 1)
            return True  # like a subprocess's exit: never propagates past the call site
        if exc_type is not None and not issubclass(exc_type, Exception):
            return False
        if exc_type is not None:  # an unhandled crash inside main() would also just exit non-zero as a subprocess
            self.returncode = 1
            self.stderr += "".join(traceback.format_exception(exc_type, exc, tb))
            return True
        return False


def cp5y_guard_bash(d, env, command, session):
    payload = {"session_id": session, "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    return rx(d, env, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))


def cp5y_repo():
    """A git repo with no Ratchet folder yet: a project's very first Ratchet run."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5y-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    return d


def cp5y_submit_plan(d, env):
    rx(d, env, "config", "--set", "min_review_seconds=0")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout


def cp5y_first_run_plan(env):
    """The first run in a repo, started before any prompt hook could log a message: no chat owns it yet."""
    d = cp5y_repo()
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    cp5y_submit_plan(d, env)
    return d, d / "t.jsonl"


def cp5z_notes(env):
    p = Path(env["RATCHET_HOME"]) / "engine-call-sessions.json"
    try:
        return json.loads(p.read_text() or "{}")
    except (OSError, ValueError):
        return {}


def cp5z_project_notes(d, env):
    return {k: v for k, v in cp5z_notes(env).items() if k.startswith(f"{d.resolve()}|")}


def cp5w_lock(d):
    return Path(d) / ".ratchet" / "state.lock"


def cp5v_until(cond, timeout=60):
    """Wait for a condition the child process makes true (never a fixed sleep standing in for an assertion)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def cp5r8_sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def cp5r12_director(home_dir, denv, lane, *args):
    cp4x_guard_tool(home_dir, denv, "Bash", {"command": f"python3 {ENGINE} --root {lane} {' '.join(args)}"},
                    session="s-director")
    return rx(home_dir, denv, "--root", str(lane), *args)
