#!/usr/bin/env python3
"""
Trust-hole tests, shared by every checkpoint of the "close Ratchet's trust holes" run.
Each checkpoint owns one marked section below (`# ---- CPn ----`); every section can be read on its own, but
running this file always runs every section (see SECTIONS at the bottom), like tests/test_docs.py.

    python3 tests/test_trust.py

Everything runs in throwaway git projects with a private HOME / RATCHET_HOME. Agent-CLI env vars that could leak
host detection in from the real environment are scrubbed, and the PATH given to subprocesses only contains a
handful of basic tools (git, sh, coreutils) resolved from the real PATH by name - never claude/codex/gemini/
cursor-agent/opencode/copilot, even though this machine has all of those installed.
"""
import datetime as dt
import importlib.util
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
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


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def _safe_path():
    b = Path(tempfile.mkdtemp(prefix="ratchet-trust-bin-"))
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
    home = Path(tempfile.mkdtemp(prefix="ratchet-trust-home-"))
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
    d = Path(tempfile.mkdtemp(prefix="ratchet-trust-p-"))
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
        (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
        (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
        r = rx(d, env, "plan-submit")
        assert r.returncode == 0, r.stderr + r.stdout
        tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, build it"},
                                       "timestamp": now_dt().isoformat()}) + "\n")
        rx(d, env, "hook", "prompt", "--host", "claude",
           stdin=json.dumps({"cwd": str(d), "prompt": "yes, build it", "session_id": "s-owner",
                             "transcript_path": str(tp)}))
        r = rx(d, env, "approve-plan")
        assert r.returncode == 0, r.stderr + r.stdout
    install(d, env, "claude")
    fire_claude(d, env)
    return d


def engine():
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


# ---- CP1 ----

def cp1_hook_error_visibility():
    print("CP1: a crashed safety hook shows up in status, next and doctor")

    # --- T01 / T02 / T03: a recent guard crash surfaces in plain status, status --json and next -----------------
    env = make_env("claude")
    d = project(env)
    at = iso_ago(5 * 60)  # "a few minutes ago"
    log_error_at(d, "guard", "claude", at, "KeyError: 'phase'")

    plain = rx(d, env, "status").stdout
    check("[CP1-T01] plain status names a failed safety hook, not just the raw exception",
          re.search(r"(safety )?hook.*(fail|crash|broke)", plain, re.I) is not None
          and "KeyError: 'phase'" not in plain, plain)
    check("[CP1-T01] plain status says what to do about it (e.g. points at doctor, a fix, or telling the user)",
          re.search(r"doctor|tell the user|fix|restart", plain, re.I) is not None, plain)

    st = js(rx(d, env, "status", "--json"))
    errs = hook_errors_of(st)
    check("[CP1-T02] status --json carries the recent hook error (event/host/at) alongside hooks/hooks_warning",
          isinstance(errs, list) and len(errs) == 1 and errs[0].get("event") == "guard"
          and errs[0].get("host") == "claude" and close(errs[0].get("at"), at)
          and isinstance(st.get("hooks"), dict) and "hooks_warning" in st, (st and st.get("hook_errors"), st and st.get("hooks")))

    nx = js(rx(d, env, "next"))
    nerrs = hook_errors_of(nx)
    check("[CP1-T03] next names the same recent hook error without a separate doctor call",
          isinstance(nerrs, list) and len(nerrs) == 1 and nerrs[0].get("event") == "guard"
          and nerrs[0].get("host") == "claude" and close(nerrs[0].get("at"), at), nx and nx.get("hook_errors"))

    # --- T04: a crash in the same invocation as a fresh heartbeat still reads as not enforcing ------------------
    env = make_env("claude")
    d = project(env)
    hb_at = iso_ago(5)
    err_at = iso_ago(2)  # the crash happened after the heartbeat write, inside the same hook call
    set_heartbeat(d, "guard", "claude", hb_at)
    log_error_at(d, "guard", "claude", err_at, "KeyError: 'owner_session'")
    hooks = (js(rx(d, env, "doctor", "--json")) or {}).get("hooks") or {}
    check("[CP1-T04] a fresh guard heartbeat with a crash logged for that same call is not 'enforcing'",
          hooks.get("enforcing") is False, hooks)

    # --- T05: a later clean guard call clears the crash and restores working status ----------------------------
    r = fire_guard(d, env)
    assert r.returncode == 0, r.stderr + r.stdout
    hooks2 = (js(rx(d, env, "doctor", "--json")) or {}).get("hooks") or {}
    hooks2b = (js(rx(d, env, "status", "--json")) or {}).get("hooks") or {}
    check("[CP1-T05] a later clean guard run restores 'enforcing'; a past crash does not permanently wedge status",
          hooks2.get("enforcing") is True and hooks2b.get("enforcing") is True, (hooks2, hooks2b))

    # --- T06: errors older than 24h are not "recent" -------------------------------------------------------------
    env = make_env("claude")
    d = project(env)  # enforcing via the stop heartbeat from project()'s own fire_claude()
    stale_at = iso_ago(25 * 3600)
    log_error_at(d, "guard", "claude", stale_at, "KeyError: 'stale'")
    st = js(rx(d, env, "status", "--json"))
    nx = js(rx(d, env, "next"))
    plain = rx(d, env, "status").stdout
    hooks = (js(rx(d, env, "doctor", "--json")) or {}).get("hooks") or {}
    check("[CP1-T06] a >24h-old error: no hook-failure field/line, enforcing decided from the heartbeat alone",
          hook_errors_of(st) == [] and hook_errors_of(nx) == [] and "KeyError: 'stale'" not in plain
          and hooks.get("enforcing") is True, (st and st.get("hook_errors"), hooks))

    # --- T07: at most the 3 newest errors are shown --------------------------------------------------------------
    env = make_env("claude")
    d = project(env)
    stamps = [iso_ago(s) for s in (3000, 2400, 1800, 1200, 600)]  # oldest -> newest, all within 24h
    for i, at in enumerate(stamps):
        log_error_at(d, "guard", "claude", at, f"KeyError: 'oops-{i}'")
    newest3, oldest2 = stamps[-3:], stamps[:2]
    st = js(rx(d, env, "status", "--json"))
    nx = js(rx(d, env, "next"))
    for label, errs in (("status --json", hook_errors_of(st)), ("next", hook_errors_of(nx))):
        ats = [e.get("at") for e in (errs or [])]
        included = all(any(close(a, want) for a in ats) for want in newest3)
        excluded = not any(any(close(a, skip) for a in ats) for skip in oldest2)
        check(f"[CP1-T07] {label}: exactly the 3 newest errors are present, the other 2 are left out",
              isinstance(errs, list) and len(errs) == 3 and included and excluded, errs)

    # --- T08: a different host's error does not bleed into this host's status -------------------------------------
    env = make_env("claude")
    d = project(env)
    at = iso_ago(120)
    log_error_at(d, "guard", "codex", at, "KeyError: 'codex-only'")
    claude_errs = hook_errors_of(js(rx(d, env, "status", "--json")))
    env_codex = dict(env, RATCHET_HOST_PROVIDER="openai")
    codex_errs = hook_errors_of(js(rx(d, env_codex, "status", "--json")))
    check("[CP1-T08] a codex-logged error is absent from the claude view and present in the codex view",
          claude_errs == [] and isinstance(codex_errs, list) and len(codex_errs) == 1
          and codex_errs[0].get("host") == "codex" and close(codex_errs[0].get("at"), at),
          (claude_errs, codex_errs))

    # --- T09: no crashes ever: status/next unaffected, purely additive -----------------------------------------
    env = make_env("claude")
    d = project(env)
    st = js(rx(d, env, "status", "--json"))
    nx = js(rx(d, env, "next"))
    plain = rx(d, env, "status").stdout
    old_status = {"phase": str, "checkpoints": list, "hooks_warning": (str, type(None)), "hook_heartbeat": dict}
    check("[CP1-T09] a clean project: no hook-failure line/field anywhere, old keys/shapes unchanged",
          hook_errors_of(st) == [] and hook_errors_of(nx) == []
          and re.search(r"hook.*(fail|crash|broke)", plain, re.I) is None
          and all(isinstance(st.get(k), t) for k, t in old_status.items()), (st, plain[:200]))

    # --- T10: a corrupt hook-errors.log cannot crash status/next/doctor -----------------------------------------
    env = make_env("claude")
    d = project(env)
    errors_path(d).write_bytes(b"\xff\xfe\x00not utf8 or newline-delimited at all\x01\x02")
    outs = [rx(d, env, "status"), rx(d, env, "status", "--json"), rx(d, env, "next"), rx(d, env, "doctor", "--json")]
    st = js(outs[1])
    check("[CP1-T10] garbage bytes in hook-errors.log: every command exits 0, no traceback, falls back to no errors",
          all(r.returncode == 0 and "Traceback" not in r.stderr for r in outs) and hook_errors_of(st) == [],
          [(r.returncode, r.stderr[-150:]) for r in outs])
    errors_path(d).unlink()
    errors_path(d).mkdir()
    outs2 = [rx(d, env, "status"), rx(d, env, "status", "--json"), rx(d, env, "next"), rx(d, env, "doctor", "--json")]
    st2 = js(outs2[1])
    check("[CP1-T10] a directory in place of hook-errors.log: every command exits 0, no traceback",
          all(r.returncode == 0 and "Traceback" not in r.stderr for r in outs2) and hook_errors_of(st2) == [],
          [(r.returncode, r.stderr[-150:]) for r in outs2])

    # --- T11: the surfaced message is plain language, not a raw traceback ---------------------------------------
    env = make_env("claude")
    d = project(env)
    log_error_at(d, "guard", "claude", iso_ago(60), "KeyError: 'owner_session_super_specific_marker'")
    plain = rx(d, env, "status").stdout
    check("[CP1-T11] the plain status line is an English sentence about the safety hook, not a raw traceback dump",
          "owner_session_super_specific_marker" not in plain and "KeyError" not in plain
          and re.search(r"(safety )?(hook|check).*(fail|crash|broke|stopped working)", plain, re.I) is not None,
          plain)

    # --- T12 / T13: the Cursor preset runs headless and cannot edit files ---------------------------------------
    eng = engine()
    cmd = eng.PRESETS["cursor"]["command"]
    check("[CP1-T12] the cursor preset command includes --trust and --mode ask",
          "--trust" in cmd and "--mode" in cmd and cmd.index("--mode") + 1 < len(cmd)
          and cmd[cmd.index("--mode") + 1] == "ask", cmd)

    env = make_env("claude")
    d = project(env, approved=False)
    r = rx(d, env, "providers", "--add", "cursor", "--preset", "cursor", "--company", "cursorco", "--no-check")
    assert r.returncode == 0, r.stderr + r.stdout
    os.environ["RATCHET_HOME"] = env["RATCHET_HOME"]
    os.environ["RATCHET_HOST_PROVIDER"] = env["RATCHET_HOST_PROVIDER"]
    try:
        ctx = eng.Ctx(d)
        cfg = ctx.config()
        resolved_cmd, use_stdin = eng.reviewer_command(cfg, "cursor-both")
    finally:
        os.environ.pop("RATCHET_HOME", None)
        os.environ.pop("RATCHET_HOST_PROVIDER", None)
    check("[CP1-T13] a cursor-preset reviewer added via `providers --add` still resolves, with the new flags",
          "--trust" in resolved_cmd and "--mode" in resolved_cmd
          and resolved_cmd[resolved_cmd.index("--mode") + 1] == "ask", resolved_cmd)


def crash_payload(d):
    """A guard payload that makes the hook itself raise (tool_name must be a string), so it fails open for real."""
    return {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": 5,
            "tool_input": {"file_path": 5}}


def crash_guard(d, env, host="claude"):
    return rx(d, env, "hook", "guard", "--host", host, stdin=json.dumps(crash_payload(d)))


def age_error_log(d, seconds):
    """Move every hook-errors.log line `seconds` into the past, as if that much time had gone by."""
    lines = errors_path(d).read_text().splitlines()
    errors_path(d).write_text("".join(iso_ago(seconds) + " " + ln.split(" ", 1)[1] + "\n" for ln in lines))


def hooks_in(d, env):
    return (js(rx(d, env, "doctor", "--json")) or {}).get("hooks") or {}


def cp1_adv_round1_fixes():
    print("CP1 (adversarial round 1): crash state outlives the 24h window and is judged per host")

    # --- X01: a real guard crash that ages out of the 24h window still blocks enforcement until guard runs clean --
    env = make_env("claude")
    d = project(env)  # enforcing via the stop heartbeat
    r = crash_guard(d, env)
    check("[CP1-X01] a guard that raises fails open (exit 0) and logs the crash",
          r.returncode == 0 and errors_path(d).exists() and " claude guard: " in errors_path(d).read_text(),
          (r.returncode, r.stderr[-200:]))
    check("[CP1-X01] right after the crash, enforcing is false", hooks_in(d, env).get("enforcing") is False,
          hooks_in(d, env))
    age_error_log(d, 25 * 3600)
    fire_claude(d, env)  # the prompt and stop hooks keep running cleanly; guard does not run
    st = js(rx(d, env, "status", "--json")) or {}
    nx = js(rx(d, env, "next")) or {}
    plain = rx(d, env, "status").stdout
    hooks = hooks_in(d, env)
    check("[CP1-X01] the expired guard error leaves hook_errors, but enforcing stays false while stop runs clean",
          hook_errors_of(st) == [] and hook_errors_of(nx) == [] and hooks.get("enforcing") is False
          and (st.get("hooks") or {}).get("enforcing") is False, (hook_errors_of(st), hooks, st.get("hooks")))
    check("[CP1-X01] next/status still warn that a safety hook broke, and plain status says what to do",
          "broke" in str(st.get("hooks_warning")) and "broke" in str(nx.get("warning"))
          and re.search(r"hook failed.*not run cleanly since.*doctor", plain, re.I | re.S) is not None
          and "KeyError" not in plain and "AttributeError" not in plain, (st.get("hooks_warning"), nx.get("warning"), plain))
    doc = rx(d, env, "doctor").stdout
    check("[CP1-X01] plain doctor lists the unresolved older crash", "claude guard hook crashed" in doc, doc[-400:])
    r = fire_guard(d, env)
    assert r.returncode == 0, r.stderr + r.stdout
    plain2 = rx(d, env, "status").stdout
    check("[CP1-X01] a clean guard run on the same host restores enforcing and clears the status line",
          hooks_in(d, env).get("enforcing") is True
          and re.search(r"hook.*(fail|crash|broke)", plain2, re.I) is None, (hooks_in(d, env), plain2))

    # --- X02: unknown host: another host's crash or clean run never clears this host's crash ------------------------
    env = make_env("claude")
    d = project(env)
    env_u = dict(env, RATCHET_HOST_PROVIDER="weirdvendor")  # a host Ratchet cannot name: status is "unknown"
    crash_guard(d, env, "claude")
    crash_guard(d, env, "codex")
    r = rx(d, env, "hook", "guard", "--host", "codex", stdin=json.dumps(guard_payload(d)))
    assert r.returncode in (0, 2), r.stderr + r.stdout  # a clean codex guard decision (allow or block)
    hu = hooks_in(d, env_u)
    check("[CP1-X02] unknown host: claude guard crash, codex guard crash then a clean codex run: still not enforcing",
          hu.get("host") == "unknown" and hu.get("enforcing") is False, hu)
    check("[CP1-X02] the claude view is not enforcing either (its guard never ran cleanly)",
          hooks_in(d, env).get("enforcing") is False, hooks_in(d, env))
    r = fire_guard(d, env)
    assert r.returncode == 0, r.stderr + r.stdout
    check("[CP1-X02] once claude's guard runs cleanly, both the claude and unknown views are enforcing again",
          hooks_in(d, env).get("enforcing") is True and hooks_in(d, env_u).get("enforcing") is True,
          (hooks_in(d, env), hooks_in(d, env_u)))

    # --- X03: same, from the log alone: a codex heartbeat does not resolve an older claude guard error -----------
    env = make_env("claude")
    d = project(env)
    env_u = dict(env, RATCHET_HOST_PROVIDER="weirdvendor")
    log_error_at(d, "guard", "claude", iso_ago(300), "KeyError: 'claude-side'")
    log_error_at(d, "guard", "codex", iso_ago(200), "KeyError: 'codex-side'")
    set_heartbeat(d, "guard", "codex", iso_ago(100))
    hu = hooks_in(d, env_u)
    check("[CP1-X03] unknown host, log only: a newer codex error and codex heartbeat leave the claude error unresolved",
          hu.get("host") == "unknown" and hu.get("enforcing") is False, hu)


_CHILD_RECORD = (
    "import importlib.util, os, sys, time; from pathlib import Path\n"
    "s = importlib.util.spec_from_file_location('ratchet_child', sys.argv[1])\n"
    "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
    "if len(sys.argv) > 6:\n"
    "    Path(sys.argv[6]).touch()\n"
    "    while not os.path.exists(sys.argv[7]):\n"
    "        time.sleep(0.001)\n"
    "m._record_hook_run(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5] == '1')\n")


def record_in_child(d, env, host, event, crashed, ready=None, go=None):
    """Record a hook run from a separate process, as a concurrent hook process would (optionally after a barrier)."""
    args = [sys.executable, "-c", _CHILD_RECORD, str(ENGINE), str(d), host, event, "1" if crashed else "0"]
    args += [str(ready), str(go)] if ready else []
    return subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def crash_marks(d, host, event, kind):
    folder = Path(d) / ".ratchet" / "hook-crashes"
    return [n for n in (os.listdir(folder) if folder.is_dir() else []) if n.startswith(f"{host}.{event}.{kind}.")]


def run_with_interleaved_write(m, call, during):
    """Run `call()` (one _record_hook_run) and, right after its first read of the crash state, run `during()`: the
    exact interleaving of two hook processes racing. When `call` never reads the state, `during()` runs after it."""
    real, fired = m.read_hook_crashes, []

    def wrapped(root):
        out = real(root)
        if not fired:
            fired.append(1)
            during()
        return out
    m.read_hook_crashes = wrapped
    try:
        call()
    finally:
        m.read_hook_crashes = real
    if not fired:
        during()


def cp1_adv_round2_fixes():
    print("CP1 (adversarial round 2): concurrent hook processes never erase each other's crash records")
    m = engine()

    # --- X04: guard's clean run is in flight while stop crashes in another process; stop's crash must survive -----
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    m._record_hook_run(root, "claude", "guard", True)  # guard crashed earlier
    run_with_interleaved_write(m, lambda: m._record_hook_run(root, "claude", "guard", False),
                               lambda: record_in_child(d, env, "claude", "stop", True).wait(60))
    c = m.read_hook_crashes(root)
    check("[CP1-X04] a clean guard run racing a stop crash resolves guard but keeps stop's crash unresolved",
          ((c.get("claude") or {}).get("stop") or {}).get("unresolved") is True
          and ((c.get("claude") or {}).get("guard") or {}).get("unresolved") is False, c)
    log_error_at(d, "stop", "claude", iso_ago(25 * 3600))  # stop's log entry has aged out of the 24h window
    set_heartbeat(d, "guard", "claude", iso_ago(5))  # and guard keeps firing
    st = js(rx(d, env, "status", "--json")) or {}
    check("[CP1-X04] after stop's log entry expires, fresh guard heartbeats still do not make enforcing true",
          hook_errors_of(st) == [] and hooks_in(d, env).get("enforcing") is False
          and (st.get("hooks") or {}).get("enforcing") is False, (hooks_in(d, env), st.get("hooks")))
    fire_claude(d, env)  # a real clean stop run
    check("[CP1-X04] a clean stop run on the same host then restores enforcing",
          hooks_in(d, env).get("enforcing") is True, hooks_in(d, env))

    # --- X05: a crash of the same hook recorded while its clean run is in flight stays unresolved ---------------
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    m._record_hook_run(root, "claude", "guard", True)
    run_with_interleaved_write(m, lambda: m._record_hook_run(root, "claude", "guard", False),
                               lambda: record_in_child(d, env, "claude", "guard", True).wait(60))
    c = m.read_hook_crashes(root)
    check("[CP1-X05] a guard crash recorded while a clean guard run was in flight stays unresolved",
          ((c.get("claude") or {}).get("guard") or {}).get("unresolved") is True
          and hooks_in(d, env).get("enforcing") is False, (c, hooks_in(d, env)))

    # --- X06: many hook processes at once, different host/event pairs: every record survives -------------------
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    for h in ("claude", "codex"):
        m._record_hook_run(root, h, "prompt", True)
    bar = Path(tempfile.mkdtemp(prefix="ratchet-trust-bar-"))
    go = bar / "go"
    jobs = [(h, ev, True) for h in ("claude", "codex", "gemini", "copilot") for ev in ("guard", "stop")]
    jobs += [("claude", "prompt", False), ("codex", "prompt", False)]
    procs = [record_in_child(d, env, h, ev, cr, bar / f"ready-{i}", go) for i, (h, ev, cr) in enumerate(jobs)]
    deadline = time.time() + 60
    while time.time() < deadline and len(list(bar.glob("ready-*"))) < len(procs):
        time.sleep(0.01)
    go.touch()  # release every writer at once
    rcs = [p.wait(60) for p in procs]
    c = m.read_hook_crashes(root)
    lost = [(h, ev) for h, ev, cr in jobs if cr and ((c.get(h) or {}).get(ev) or {}).get("unresolved") is not True]
    check("[CP1-X06] ten concurrent hook processes: all 8 crashes survive and both clean runs resolve their own",
          rcs == [0] * len(procs) and lost == []
          and all(((c.get(h) or {}).get("prompt") or {}).get("unresolved") is False for h in ("claude", "codex")),
          (rcs, lost, c))
    shutil.rmtree(bar, ignore_errors=True)

    # --- X07: a hook that keeps crashing leaves one marker, not one per crash; a clean run leaves only its own -----
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    tick = [now_dt() - dt.timedelta(hours=1)]

    def clock():  # a controlled engine clock: every call is one second after the last, whatever the wall clock does
        tick[0] += dt.timedelta(seconds=1)
        return tick[0].isoformat(timespec="microseconds")
    real_now, m.now = m.now, clock
    try:
        for _ in range(5):
            m._record_hook_run(root, "claude", "guard", True)
        n_crash = len(crash_marks(d, "claude", "guard", "crash"))
        m._record_hook_run(root, "claude", "guard", False)
    finally:
        m.now = real_now
    check("[CP1-X07] five guard crashes keep one crash marker; a clean run then leaves one clean marker only",
          n_crash == 1 and crash_marks(d, "claude", "guard", "crash") == []
          and len(crash_marks(d, "claude", "guard", "clean")) == 1
          and ((m.read_hook_crashes(root).get("claude") or {}).get("guard") or {}).get("unresolved") is False,
          (n_crash, os.listdir(Path(d) / ".ratchet" / "hook-crashes")))


def unsaved_path(d):
    return Path(d) / ".ratchet" / "hook-crashes-unsaved.log"


def cp1_adv_round3_fixes():
    print("CP1 (adversarial round 3): crash state that cannot be saved or read fails safe; status names each broken hook")
    m = engine()

    # --- X08: hook-crashes/ cannot be created or read: the crash is kept elsewhere and never ages into "healthy" ----
    env = make_env("claude")
    d = project(env)
    folder = Path(d) / ".ratchet" / "hook-crashes"
    shutil.rmtree(folder, ignore_errors=True)
    folder.write_text("not a folder")  # crash markers can now be neither saved nor read, on every OS
    r = crash_guard(d, env)
    check("[CP1-X08] a guard crash whose marker cannot be saved fails open and is kept in the fallback file",
          r.returncode == 0 and " claude guard: " in errors_path(d).read_text()
          and unsaved_path(d).exists() and " claude guard" in unsaved_path(d).read_text(),
          (r.returncode, r.stderr[-200:], unsaved_path(d).exists() and unsaved_path(d).read_text()))
    fire_claude(d, env)
    r = fire_guard(d, env)  # a clean guard run that cannot save (or read) its record either
    assert r.returncode == 0, r.stderr + r.stdout
    st = js(rx(d, env, "status", "--json")) or {}
    nx = js(rx(d, env, "next")) or {}
    plain = rx(d, env, "status").stdout
    check("[CP1-X08] while the crash records cannot be read or saved, fresh clean runs do not make enforcing true",
          hooks_in(d, env).get("enforcing") is False and (st.get("hooks") or {}).get("enforcing") is False,
          (hooks_in(d, env), st.get("hooks")))
    check("[CP1-X08] status --json, next and plain status say the crash records cannot be read",
          "cannot read" in str(st.get("hooks_warning")) and "cannot read" in str(nx.get("warning"))
          and "cannot read its record of safety hook failures" in plain,
          (st.get("hooks_warning"), nx.get("warning"), plain))
    folder.unlink()  # the bad file is removed: hook-crashes/ reads as empty again
    age_error_log(d, 25 * 3600)  # and the guard's log line ages out of the 24h window
    fire_claude(d, env)  # stop and prompt keep running cleanly; guard does not run
    st = js(rx(d, env, "status", "--json")) or {}
    plain = rx(d, env, "status").stdout
    doc = rx(d, env, "doctor").stdout
    check("[CP1-X08] 24h later, the crash that could not be saved still keeps enforcing false, and status says so",
          hook_errors_of(st) == [] and hooks_in(d, env).get("enforcing") is False
          and re.search(r"failed .*before each edit or command.*not run cleanly since", plain) is not None
          and "could not be saved" in doc, (hooks_in(d, env), plain, doc[-500:]))
    r = fire_guard(d, env)
    assert r.returncode == 0, r.stderr + r.stdout
    plain = rx(d, env, "status").stdout
    check("[CP1-X08] once the folder works, a clean guard run on the same host restores enforcing",
          hooks_in(d, env).get("enforcing") is True and re.search(r"hook.*(fail|crash|broke)", plain, re.I) is None,
          (hooks_in(d, env), plain))

    # --- X09: hook-crashes/ readable but not writable (POSIX permissions): enforcing stays false until it can save --
    env = make_env("claude")
    d = project(env)
    folder = Path(d) / ".ratchet" / "hook-crashes"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o555)
    probe = folder / "probe"
    try:
        probe.touch()
        enforced = False  # permission bits are not enforced here (root, or not POSIX): X08 covers this OS
        probe.unlink()
    except OSError:
        enforced = True
    try:
        if not enforced:
            print("  - [CP1-X09] not run: this OS or user ignores folder permissions (X08 covers the same rule)")
        else:
            crash_guard(d, env)
            age_error_log(d, 25 * 3600)
            fire_claude(d, env)
            fire_guard(d, env)  # runs cleanly but cannot save that it did
            check("[CP1-X09] read-only hook-crashes/: the crash is kept, and a clean run that cannot be saved does "
                  "not restore enforcing", unsaved_path(d).exists() and hooks_in(d, env).get("enforcing") is False,
                  (unsaved_path(d).exists(), hooks_in(d, env)))
            os.chmod(folder, 0o755)
            fire_guard(d, env)
            check("[CP1-X09] once the folder is writable again, the next clean guard run restores enforcing",
                  hooks_in(d, env).get("enforcing") is True, hooks_in(d, env))
    finally:
        os.chmod(folder, 0o755)

    # --- X10: an old unresolved guard crash plus a newer, resolved prompt crash: each is described as itself -------
    env = make_env("claude")
    d = project(env)
    crash_guard(d, env)
    age_error_log(d, 25 * 3600)  # the guard crash leaves the 24h window but is still unresolved
    log_error_at(d, "prompt", "claude", iso_ago(60))  # a recent prompt crash...
    fire_claude(d, env)  # ...after which the prompt (and stop) hooks run cleanly
    plain = rx(d, env, "status").stdout
    check("[CP1-X10] plain status says the prompt hook has run cleanly since, and names the guard as still broken",
          re.search(r"failed .*records your replies.*\n.*It has run cleanly since then", plain) is not None
          and re.search(r"failed .*before each edit or command.*not run cleanly since.*not enforced", plain) is not None
          and "It has not run cleanly since" not in plain, plain)

    # --- X11: two unresolved older crashes of different hooks: each gets its own line and consequence ---------------
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    m._record_hook_run(root, "claude", "guard", True, started=iso_ago(7200))
    m._record_hook_run(root, "claude", "prompt", True, started=iso_ago(3600))
    plain = rx(d, env, "status").stdout
    replies = [ln for ln in plain.splitlines() if "records your replies" in ln]
    check("[CP1-X11] with no recent log lines, plain status names both broken hooks, each with its own consequence",
          len(replies) == 1 and "may not be recorded" in replies[0] and "not enforced" not in replies[0]
          and re.search(r"2 hours ago \(the check that runs before each edit or command\).*not enforced", plain)
          is not None, plain)


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


class deny_writes:
    """The OS refusing writes (still allowing reads) to some crash-state paths, on every OS: every crash-state write in
    the engine goes through its module-level open(), which this replaces for the duration. Applies to the hook and
    to readers in this process alike, as a real permission problem would."""

    def __init__(self, m, paths):
        self.m, self.paths = m, [Path(p).resolve() for p in paths]

    def __enter__(self):
        import builtins
        real = builtins.open

        def fake(file, mode="r", *a, **k):
            if isinstance(file, (str, os.PathLike)) and any(c in mode for c in "wax+"):
                p = Path(file).resolve()
                if any(p == d or d in p.parents for d in self.paths):
                    raise PermissionError(13, "Permission denied (test)", str(file))
            return real(file, mode, *a, **k)
        self.m.open = fake
        return self

    def __exit__(self, *exc):
        del self.m.open


def crash_in_process(m, d):
    """One real guard crash through run_hook, in this process (so deny_writes applies); returns (rc, stdout, stderr)."""
    import contextlib
    import io
    out, err, real_in = io.StringIO(), io.StringIO(), sys.stdin
    sys.stdin = io.StringIO(json.dumps(crash_payload(d)))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = m.run_hook("guard", "claude")
    finally:
        sys.stdin = real_in
    return rc, out.getvalue(), err.getvalue()


def cp1_adv_round4_fixes():
    print("CP1 (adversarial round 4): a hook counts as working only while Ratchet can save its crash state")
    m = engine()

    # --- X12: structural, every OS: each crash-state write path (and every combination) refusing writes ------------
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    rdir = root / ".ratchet"
    paths = {"crash marker": rdir / "hook-crashes", "unsaved log": unsaved_path(d), "error log": errors_path(d),
             "heartbeat": hb_path(d)}
    names = list(paths)
    combos = [[n for i, n in enumerate(names) if mask >> i & 1] for mask in range(1, 1 << len(names))]
    bad_denied, bad_later, bad_report, bad_restore, unrecordable = [], [], [], [], []
    for combo in combos:
        shutil.rmtree(paths["crash marker"], ignore_errors=True)
        paths["crash marker"].mkdir()
        for k in ("unsaved log", "error log"):
            if paths[k].exists():
                paths[k].unlink()
        fire_guard(d, env)
        fire_claude(d, env)
        if hooks_in(d, env).get("enforcing") is not True:
            bad_denied.append((combo, "precondition: not enforcing before the crash", hooks_in(d, env)))
            continue
        with engine_env(env), deny_writes(m, [paths[k] for k in combo]):
            rc, out, err = crash_in_process(m, d)
            denied_hs = m.hooks_status(root, {})
            problem = m.hook_crash_state_problem(root)
        lost = "crash marker" in combo and "unsaved log" in combo
        if rc != 0 or denied_hs.get("enforcing") is not False:
            bad_denied.append((combo, rc, denied_hs, problem))
        if lost and "could not save" not in out + err:
            bad_report.append((combo, out, err))
        if errors_path(d).exists():
            age_error_log(d, 25 * 3600)
        fire_claude(d, env)  # the writes work again; stop and prompt run cleanly; guard has not run since its crash
        later = hooks_in(d, env).get("enforcing")
        if lost:
            unrecordable.append((combo, later))  # no crash store took it: the documented limit (round 5)
        elif later is not False:
            bad_later.append((combo, hooks_in(d, env)))
        fire_guard(d, env)
        if hooks_in(d, env).get("enforcing") is not True:
            bad_restore.append((combo, hooks_in(d, env)))
    check(f"[CP1-X12] all {len(combos)} combinations of unwritable crash marker / unsaved log / error log / heartbeat: "
          "the crashing guard fails open and enforcing is false while the writes are refused", bad_denied == [],
          bad_denied)
    check("[CP1-X12] when neither crash store can take the crash, the hook says so plainly to the host",
          bad_report == [], bad_report)
    check("[CP1-X12] once writes work again, 24h later, after clean stop and prompt runs, the crash still keeps "
          "enforcing false wherever a crash store (marker or unsaved log) took it", bad_later == [], bad_later)
    check("[CP1-X12] in every combination, a clean guard run with writable stores restores enforcing",
          bad_restore == [], bad_restore)
    print(f"  - [CP1-X12] limit, by design: with crash marker and unsaved log both refusing writes, nothing "
          f"recorded the crash; enforcing stays false only while the writes are refused ({len(unrecordable)} "
          "combinations, checked above)")

    # --- X13: the findings' scenario with real permissions: both crash stores readable but not writable ------------
    env = make_env("claude")
    d = project(env)
    folder = Path(d) / ".ratchet" / "hook-crashes"
    folder.mkdir(parents=True, exist_ok=True)
    unsaved_path(d).write_text("")
    os.chmod(folder, 0o555)
    os.chmod(unsaved_path(d), 0o444)
    probe = folder / "probe"
    try:
        probe.touch()
        enforced = False
        probe.unlink()
    except OSError:
        enforced = True
    try:
        if not enforced:
            print("  - [CP1-X13] not run: this OS or user ignores permissions (X12 covers the same rule everywhere)")
        else:
            r = crash_guard(d, env)
            st = js(rx(d, env, "status", "--json")) or {}
            nx = js(rx(d, env, "next")) or {}
            plain = rx(d, env, "status").stdout
            doc = rx(d, env, "doctor").stdout
            check("[CP1-X13] read-only hook-crashes/ and unsaved log: the crash fails open and says it was not saved",
                  r.returncode == 0 and "could not save" in r.stderr and unsaved_path(d).read_text() == ""
                  and crash_marks(d, "claude", "guard", "crash") == [], (r.returncode, r.stderr[-300:]))
            check("[CP1-X13] enforcing is false and status --json, next, plain status and doctor say the crash "
                  "records cannot be saved",
                  hooks_in(d, env).get("enforcing") is False and "cannot save" in str(st.get("hooks_warning"))
                  and "cannot save" in str(nx.get("warning")) and "cannot save its record" in plain
                  and "cannot be written" in doc, (hooks_in(d, env), st.get("hooks_warning"), plain, doc[-400:]))
            age_error_log(d, 25 * 3600)
            fire_claude(d, env)
            check("[CP1-X13] 24h later, with clean stop and prompt runs, enforcing is still false",
                  hooks_in(d, env).get("enforcing") is False, hooks_in(d, env))
            os.chmod(folder, 0o755)
            os.chmod(unsaved_path(d), 0o644)
            fire_claude(d, env)
            plain = rx(d, env, "status").stdout
            check("[CP1-X13] the stores refusing writes is what kept hooks broken: once they are writable again the "
                  "'cannot save' warning is gone (documented limit: that crash left no lasting record)",
                  "cannot save its record" not in plain and "cannot save" not in str(
                      (js(rx(d, env, "status", "--json")) or {}).get("hooks_warning")), (hooks_in(d, env), plain))
            fire_guard(d, env)
            check("[CP1-X13] the next clean guard run restores enforcing", hooks_in(d, env).get("enforcing") is True,
                  hooks_in(d, env))
    finally:
        os.chmod(folder, 0o755)
        os.chmod(unsaved_path(d), 0o644)

    # --- X14 (replaced in round 5): no crash path ever rewrites the shared hook-heartbeat.json --------------------
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    (root / ".ratchet" / "hook-crashes").mkdir(parents=True, exist_ok=True)
    stores = {"crash marker": root / ".ratchet" / "hook-crashes", "unsaved log": unsaved_path(d),
              "error log": errors_path(d)}
    touched = []
    for mask in range(0, 1 << len(stores)):
        combo = [k for i, k in enumerate(stores) if mask >> i & 1]
        before = hb_path(d).read_bytes()
        with engine_env(env), deny_writes(m, [stores[k] for k in combo]):
            crash_in_process(m, d)
        if hb_path(d).read_bytes() != before:
            touched.append(combo)
    check("[CP1-X14] a crashing hook never rewrites hook-heartbeat.json, whichever crash stores refuse writes "
          "(crash state lives only in per-run markers and appended lines)",
          touched == [] and "crash_store_writable" not in hb_path(d).read_text(), touched)
    fire_guard(d, env)
    check("[CP1-X14] a clean claude guard run then restores enforcing", hooks_in(d, env).get("enforcing") is True,
          hooks_in(d, env))


def heartbeat_with_interleaved_crash(m, root, host, event, during):
    """Run one _heartbeat (a hook's read-modify-write of hook-heartbeat.json) and, right after it reads the file and
    before it writes its stale copy back, run `during()`: the exact race in round 5's finding."""
    real, fired, hb = m.read_json, [], (Path(root) / ".ratchet" / "hook-heartbeat.json").resolve()

    def wrapped(p, *a, **k):
        out = real(p, *a, **k)
        if not fired and Path(p).resolve() == hb:
            fired.append(1)
            during()
        return out
    m.read_json = wrapped
    try:
        m._heartbeat(Path(root), event, host)
    finally:
        m.read_json = real
    return bool(fired)


_CHILD_HOOK = (
    "import builtins, importlib.util, io, os, sys, time; from pathlib import Path\n"
    "s = importlib.util.spec_from_file_location('ratchet_child', sys.argv[1])\n"
    "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
    "event, host, payload, deny, ready, go = sys.argv[2:8]\n"
    "if deny:\n"
    "    real, dd = builtins.open, Path(deny).resolve()\n"
    "    def fake(file, mode='r', *a, **k):\n"
    "        if isinstance(file, (str, os.PathLike)) and any(c in mode for c in 'wax+') \\\n"
    "                and dd in Path(file).resolve().parents:\n"
    "            raise PermissionError(13, 'Permission denied (test)', str(file))\n"
    "        return real(file, mode, *a, **k)\n"
    "    m.open = fake\n"
    "sys.stdin = io.StringIO(payload)\n"
    "Path(ready).touch()\n"
    "while not os.path.exists(go):\n"
    "    time.sleep(0.001)\n"
    "sys.exit(m.run_hook(event, host))\n")


def hook_in_child(d, env, event, host, payload, deny, ready, go):
    """A real run_hook in its own process, released by a barrier; `deny` (a folder) refuses writes inside it."""
    args = [sys.executable, "-c", _CHILD_HOOK, str(ENGINE), event, host, json.dumps(payload), str(deny or ""),
            str(ready), str(go)]
    return subprocess.Popen(args, env=env, cwd=str(d), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def cp1_adv_round5_fixes():
    print("CP1 (adversarial round 5): no crash-state path rewrites a shared file, so no hook can erase another's crash")
    m = engine()

    # --- X15: the finding's interleaving: stop reads the heartbeat, guard crashes, stop writes its stale copy ------
    for label, deny in (("saved as a crash marker", False), ("kept in the unsaved log", True)):
        env = make_env("claude")
        d = project(env)
        root = Path(d)
        folder = root / ".ratchet" / "hook-crashes"
        folder.mkdir(parents=True, exist_ok=True)

        def guard_crashes():
            with engine_env(env), deny_writes(m, [folder] if deny else []):
                crash_in_process(m, d)
        fired = heartbeat_with_interleaved_crash(m, root, "claude", "stop", guard_crashes)
        rec = (m.read_hook_crashes(root).get("claude") or {}).get("guard") or {}
        kept = (len(crash_marks(d, "claude", "guard", "crash")) == 0 and "claude guard" in unsaved_path(d).read_text()
                if deny else len(crash_marks(d, "claude", "guard", "crash")) == 1)
        age_error_log(d, 25 * 3600)
        fire_claude(d, env)  # clean stop and prompt runs; guard has not run since its crash
        later = hooks_in(d, env).get("enforcing")
        fire_guard(d, env)
        check(f"[CP1-X15] stop rewriting a stale heartbeat around a guard crash ({label}) cannot erase it: enforcing "
              "stays false 24h later after clean stop and prompt runs, until a clean guard run",
              fired and kept and rec.get("unresolved") is True and later is False
              and hooks_in(d, env).get("enforcing") is True, (fired, kept, rec, later, hooks_in(d, env)))

    # --- X16: every remaining crash-state writer at once, in separate processes: no crash is erased --------------
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    folder = root / ".ratchet" / "hook-crashes"
    folder.mkdir(parents=True, exist_ok=True)
    for h in ("claude", "codex"):
        m._record_hook_run(root, h, "stop", True)  # resolved below by concurrent clean stop runs
    bar = Path(tempfile.mkdtemp(prefix="ratchet-trust-bar5-"))
    go = bar / "go"
    hosts = ("claude", "codex", "gemini", "copilot")
    jobs = [("guard", h, crash_payload(d), folder) for h in hosts]  # marker refused: the unsaved log takes it
    jobs += [("prompt", h, crash_payload(d), None) for h in hosts]  # crash markers
    jobs += [("stop", h, stop_payload(d), None) for h in ("claude", "codex")]  # clean runs: heartbeat writers
    procs = [hook_in_child(d, env, ev, h, p, deny, bar / f"ready-{i}", go) for i, (ev, h, p, deny) in enumerate(jobs)]
    deadline = time.time() + 60
    while time.time() < deadline and len(list(bar.glob("ready-*"))) < len(procs):
        time.sleep(0.01)
    go.touch()  # release every writer at once
    rcs = [p.wait(60) for p in procs]
    for p in procs:
        p.communicate()
    shutil.rmtree(bar, ignore_errors=True)
    c = m.read_hook_crashes(root)
    lost = [(h, ev) for ev, h, p, _ in jobs if p.get("tool_name") == 5
            and ((c.get(h) or {}).get(ev) or {}).get("unresolved") is not True]
    unsaved = unsaved_path(d).read_text().splitlines() if unsaved_path(d).exists() else []
    errs = errors_path(d).read_text().splitlines() if errors_path(d).exists() else []
    good_unsaved = sorted(ln.split(" ")[1] for ln in unsaved if re.fullmatch(r"\S+ [a-z]+ guard", ln))
    crash_errs = [ln for ln in errs if re.fullmatch(r"\S+ [a-z]+ (guard|prompt): \w+: .*", ln)]
    check("[CP1-X16] ten concurrent hook processes (4 crashes into the unsaved log, 4 crash markers, 2 clean stop "
          "runs rewriting the heartbeat): all 8 crashes survive, each unsaved and error line is whole, and the clean "
          "stop runs resolve only their own crashes",
          all(rc in (0, 2) for rc in rcs) and lost == [] and good_unsaved == sorted(hosts) and len(unsaved) == 4
          and len(crash_errs) == 8
          and all(((c.get(h) or {}).get("stop") or {}).get("unresolved") is False for h in ("claude", "codex"))
          and crash_marks(d, "claude", "guard", "crash") == [] and hooks_in(d, env).get("enforcing") is False,
          (rcs, lost, unsaved, errs, c))


def cp1_adv_round6_heartbeat_atomic():
    print("CP1 (adversarial round 6): a heartbeat rewrite is never visible half-written to a concurrent hook")
    m = engine()

    # --- X17: the X16 flake's root cause, made deterministic: at the moment _heartbeat opens a file to write, the
    # heartbeat another hook would read must still be whole JSON (open(p, "w") truncated it to empty, and a
    # concurrent stop hook's read of that empty file died and crashed it) -------------------------------------------
    import builtins
    env = make_env("claude")
    d = project(env)
    root = Path(d)
    with engine_env(env):
        m._heartbeat(root, "guard", "claude")
    real, seen = builtins.open, []

    def watching(file, mode="r", *a, **k):
        f = real(file, mode, *a, **k)
        if any(c in mode for c in "wax+"):
            try:
                seen.append(isinstance(json.loads(hb_path(d).read_text()), dict))
            except (OSError, ValueError):
                seen.append(False)
        return f
    m.open = watching
    try:
        with engine_env(env):
            m._heartbeat(root, "stop", "claude")
    finally:
        del m.open
    hb = json.loads(hb_path(d).read_text())
    leftovers = [n for n in os.listdir(root / ".ratchet") if n.startswith("hook-heartbeat.json.")]
    check("[CP1-X17] a hook rewriting hook-heartbeat.json never leaves it truncated or half-written for a concurrent "
          "reader (it writes a temp file and renames it into place), and leaves no temp file behind",
          seen != [] and all(seen) and set(hb) >= {"guard", "stop"} and leftovers == [], (seen, hb, leftovers))


# ---- end CP1 ----

# ---- CP3 ----
# Reviews only count from their round's own folder: a review written (or a symlink / `..`-path resolving) outside
# the open round's folder must be refused by adv-record/ui-record, while the normal in-folder flow keeps working.

def cp3_project(env, extra_cp=None):
    """A throwaway git project with one checkpoint, approved into building (mirrors tests/test_gates.py's project(),
    adapted to this file's env-carrying rx())."""
    d = Path(tempfile.mkdtemp(prefix="ratchet-trust-cp3-"))
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
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, build it"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "yes, build it", "session_id": "s-owner",
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


def cp3_state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def cp3_adv_folder_trust():
    print("CP3: adversarial reviews only count from their own round's folder")

    # --- T01: a review written outside the round's own folder is refused ---------------------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    outside = Path(tempfile.mkdtemp(prefix="ratchet-trust-outside-"))
    bad = cp3_write_review(outside / f"{rid}.json", rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", bad)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    evid = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial"
    check("[CP3-T01] a review outside the round's own folder is refused, names the folder, and records nothing",
          r.returncode != 0 and "reviews/CP1/adv-round-01" in (r.stderr + r.stdout)
          and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded")
          and (not evid.exists() or not any(evid.iterdir())), (r.returncode, r.stderr, cp1, evid.exists()))

    # --- T02: a review copied in from an earlier round's folder is refused even with edited fields ---------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd1 = cp3_open_round(d, env)
    rid = cp3_R0(rnd1)
    out_dir1 = Path(rnd1["output_dir"])
    (d / "src/f.js").write_text("export const f = () => 1; // tweak\n")  # new fingerprint, same test behavior
    rnd2 = cp3_open_round(d, env)
    check("[CP3-T02] setup: the code change opened round 2 with a new fingerprint",
          rnd2["round"] == rnd1["round"] + 1 and rnd2["fingerprint"] != rnd1["fingerprint"], (rnd1, rnd2))
    edited = out_dir1 / f"{rid}.json"
    cp3_write_review(edited, rnd1, rid)
    body = json.loads(edited.read_text())
    body["fingerprint"] = rnd2["fingerprint"]
    body["round_nonce"] = rnd2["round_nonce"]
    edited.write_text(json.dumps(body))  # physically still in adv-round-01/, now claiming round 2's identity
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(edited))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T02] refused for not being inside round 2's own folder, even though fingerprint/nonce now match",
          r.returncode != 0 and "reviews/CP1/adv-round-02" in (r.stderr + r.stdout)
          and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded"), (r.returncode, r.stderr, cp1))

    # --- T03: a review written straight into the round's own folder still passes (regression) --------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = cp3_write_review(Path(rnd["output_dir"]) / f"{rid}.json", rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", good)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T03] a review written straight into the round's own folder still passes (regression)",
          r.returncode == 0 and cp1["gates"]["adversarial"].get("status") == "passed",
          (r.returncode, r.stderr, cp1["gates"]["adversarial"]))

    # --- T04: a symlink inside the round's folder pointing outside it is refused ----------------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out_dir = Path(rnd["output_dir"])
    real = Path(tempfile.mkdtemp(prefix="ratchet-trust-sym-")) / "real.json"
    cp3_write_review(real, rnd, rid)
    link = out_dir / f"{rid}.json"
    link.symlink_to(real)
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(link))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T04] a symlink inside the round's folder pointing outside it is refused (resolved path checked)",
          r.returncode != 0 and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded"), (r.returncode, r.stderr, cp1))

    # --- T05: a `..`-laden path that only textually mentions the round folder is refused --------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out_dir = Path(rnd["output_dir"])
    real_dir = Path(tempfile.mkdtemp(prefix="ratchet-trust-dotdot-"))
    cp3_write_review(real_dir / f"{rid}.json", rnd, rid)
    rel = os.path.relpath(real_dir, start=out_dir)
    sneaky = os.path.join(str(out_dir), rel, f"{rid}.json")
    assert "adv-round-01" in sneaky  # sanity: the literal text still names the round folder
    r = rx(d, env, "adv-record", "CP1", "--reviews", sneaky)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T05] a `..`-laden path that only textually mentions the round folder is refused once resolved",
          r.returncode != 0 and cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded"), (r.returncode, r.stderr, cp1, sneaky))


def cp3_ui_folder_trust():
    print("CP3: the same rule applies to the combined UI review")

    # --- T06: a combined UI review living outside the round's own folder is refused -------------------------------
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    outside = Path(tempfile.mkdtemp(prefix="ratchet-trust-outside-"))
    bad = cp3_write_combined(outside / "combined.json", fp)
    r = rx(d, env, "ui-record", "CP1", "--review", bad)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T06] a combined review outside the round's own folder is refused, names the folder, and doesn't pass",
          r.returncode != 0 and "reviews/CP1/round-01" in (r.stderr + r.stdout)
          and cp1["gates"]["ui"].get("status") != "passed", (r.returncode, r.stderr, cp1["gates"]["ui"]))

    # --- T07: a combined review copied in from an earlier UI round's folder is refused, even with an edited fp ----
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp1 = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    compared = {"spec_sha256": "s1", "scope": "all"}
    diffs = [{"severity": "minor", "location": "x", "description": "issue", "fixable_in_code": True}]
    combined1 = cp3_write_combined(round1 / "combined.json", fp1, compared=compared, diffs=diffs)
    r1 = rx(d, env, "ui-record", "CP1", "--review", combined1)
    check("[CP3-T07] setup: round 1 fails and freezes the capture spec", r1.returncode == 2, (r1.returncode, r1.stderr))
    (d / "src/f.js").write_text("export const f = () => 1; // tweak\n")
    fp2 = cp3_fp(d, env)
    check("[CP3-T07] setup: the code change produced a new fingerprint", fp2 != fp1, (fp1, fp2))
    body = json.loads(Path(combined1).read_text())
    body["fingerprint"], body["differences"] = fp2, []  # would otherwise PASS round 2 outright
    Path(combined1).write_text(json.dumps(body))  # still physically in round-01/
    r2 = rx(d, env, "ui-record", "CP1", "--review", combined1)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T07] refused for not being inside round 2's own folder, even though the fingerprint now matches",
          r2.returncode != 0 and "reviews/CP1/round-02" in (r2.stderr + r2.stdout)
          and cp1["gates"]["ui"].get("status") != "passed", (r2.returncode, r2.stderr, cp1["gates"]["ui"]))

    # --- T08: a combined review written into the round's own folder still passes (regression) ---------------------
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    good = cp3_write_combined(round1 / "combined.json", fp)
    r = rx(d, env, "ui-record", "CP1", "--review", good)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T08] a combined review written into the round's own folder still passes (regression)",
          r.returncode == 0 and cp1["gates"]["ui"].get("status") == "passed", (r.returncode, r.stderr, cp1["gates"]["ui"]))

    # --- T09: a symlink inside the UI round's folder pointing outside it is refused --------------------------------
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    real = Path(tempfile.mkdtemp(prefix="ratchet-trust-uisym-")) / "real.json"
    cp3_write_combined(real, fp)
    link = round1 / "combined.json"
    link.symlink_to(real)
    r = rx(d, env, "ui-record", "CP1", "--review", str(link))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T09] a symlink inside the UI round's folder pointing outside it is refused (resolved path checked)",
          r.returncode != 0 and cp1["gates"]["ui"].get("status") != "passed", (r.returncode, r.stderr, cp1["gates"]["ui"]))


# ---- end CP3 ----

SECTIONS = [("CP1", cp1_hook_error_visibility)]
SECTIONS.append(("CP1", cp1_adv_round1_fixes))
SECTIONS.append(("CP1", cp1_adv_round2_fixes))
SECTIONS.append(("CP1", cp1_adv_round3_fixes))
SECTIONS.append(("CP1", cp1_adv_round4_fixes))
SECTIONS.append(("CP1", cp1_adv_round5_fixes))
SECTIONS.append(("CP1", cp1_adv_round6_heartbeat_atomic))
SECTIONS.append(("CP3", cp3_adv_folder_trust))
SECTIONS.append(("CP3", cp3_ui_folder_trust))


# ---- CP3 adversarial round 1 fixes ----
# The location is checked BEFORE a review is read, and the bytes read once from the opened, validated file are the
# only ones used afterwards (content checks and the evidence copy), so a swap after the check changes nothing.

def cp3x_in_process(m, d, env, *argv):
    """Runs one ratchet.py command in this process (so the test can interpose on it); returns (rc, stderr)."""
    import contextlib
    import io
    saved, cwd = dict(os.environ), os.getcwd()
    os.environ.clear()
    os.environ.update(env)
    os.environ["RATCHET_ROOT"] = str(d)
    out, err, rc = io.StringIO(), io.StringIO(), 0
    try:
        os.chdir(str(d))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                m.main(list(argv))
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(saved)
    return rc, err.getvalue() + out.getvalue()


def cp3x_adv_ready():
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    return env, d, rnd, cp3_R0(rnd)


def cp3x_ui_ready():
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    return env, d, cp3_fp(d, env)


def cp3x_adv_unrecorded(d):
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    return cp1["gates"]["adversarial"].get("status") != "passed" and not (cp1.get("adv_round") or {}).get("recorded")


def cp3x_swap_on_open(name, swap):
    """An os.open stand-in that runs `swap` just before the first open of a file called `name`: the moment between
    the location check and the open."""
    real_open = os.open
    state = {"done": False}

    def opener(path, flags, *a, **k):
        if not state["done"] and os.path.basename(str(path)) == name:
            state["done"] = True
            swap()
        return real_open(path, flags, *a, **k)
    return opener, real_open


def cp3_adv_round1_location_first():
    print("CP3 adversarial round 1: location is checked first, and only the bytes read from the checked file count")

    # --- wrong folder + malformed / stale: the folder message wins (adv-record) ------------------------------------
    env, d, rnd, rid = cp3x_adv_ready()
    outside = Path(tempfile.mkdtemp(prefix="ratchet-trust-x01-"))
    junk = outside / f"{rid}.json"
    junk.write_text("{ this is not json")
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(junk))
    check("[CP3-X01] adv-record: a malformed review outside the round's folder gets the folder message, not a JSON error",
          r.returncode != 0 and "reviews/CP1/adv-round-01" in r.stderr and "not valid JSON" not in r.stderr
          and cp3x_adv_unrecorded(d), (r.returncode, r.stderr))
    stale = cp3_write_review(outside / "stale.json", rnd, rid, fingerprint="0" * 40, round_nonce="old-nonce")
    r = rx(d, env, "adv-record", "CP1", "--reviews", stale)
    check("[CP3-X01] adv-record: a stale review outside the round's folder gets the folder message, not a fingerprint one",
          r.returncode != 0 and "reviews/CP1/adv-round-01" in r.stderr and "not in this round's folder" in r.stderr
          and "fingerprint" not in r.stderr and "does not carry" not in r.stderr and cp3x_adv_unrecorded(d),
          (r.returncode, r.stderr))
    notobj = Path(rnd["output_dir"]) / f"{rid}.json"
    notobj.write_text("[1, 2]")
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(notobj))
    check("[CP3-X01] adv-record: an in-folder review that is not a JSON object is refused with a clear message",
          r.returncode != 0 and "JSON object" in r.stderr and "Traceback" not in r.stderr and cp3x_adv_unrecorded(d),
          (r.returncode, r.stderr))

    # --- wrong folder + malformed / stale: the folder message wins (ui-record) -------------------------------------
    env, d, fp = cp3x_ui_ready()
    outside = Path(tempfile.mkdtemp(prefix="ratchet-trust-x02-"))
    junk = outside / "combined.json"
    junk.write_text("{ nope")
    r = rx(d, env, "ui-record", "CP1", "--review", str(junk))
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X02] ui-record: a malformed combined review outside the round's folder gets the folder message",
          r.returncode != 0 and "reviews/CP1/round-01" in r.stderr and "not valid JSON" not in r.stderr
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (r.returncode, r.stderr, ui))
    stale = cp3_write_combined(outside / "stale.json", "0" * 40)
    r = rx(d, env, "ui-record", "CP1", "--review", stale)
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X02] ui-record: a stale combined review outside the round's folder gets the folder message",
          r.returncode != 0 and "reviews/CP1/round-01" in r.stderr and "fingerprint" not in r.stderr
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (r.returncode, r.stderr, ui))

    # --- adv-record judges (and copies) the bytes it read after the check, even if the file is rewritten later -----
    m = engine()
    env, d, rnd, rid = cp3x_adv_ready()
    good = Path(cp3_write_review(Path(rnd["output_dir"]) / f"{rid}.json", rnd, rid))
    original = good.read_bytes()
    real_read = m._read_round_review

    def read_then_swap(*a, **k):
        res = real_read(*a, **k)
        good.write_text(json.dumps({"reviewer": rid, "fingerprint": "0" * 40, "round_nonce": "forged",
                                    "findings": [{"id": "F", "severity": "blocker", "file": "src/f.js"}]}))
        return res
    m._read_round_review = read_then_swap
    rc, err = cp3x_in_process(m, d, env, "adv-record", "CP1", "--reviews", str(good))
    ev = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial/round-01" / f"{rid}.json"
    check("[CP3-X03] adv-record: content checks use the bytes read after the location check (a later rewrite is ignored)",
          rc == 0 and cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"].get("status") == "passed", (rc, err))
    check("[CP3-X03] adv-record: the evidence copy holds the checked bytes, not the file re-read by name",
          ev.exists() and ev.read_bytes() == original, (ev.exists(), ev.read_bytes()[:200] if ev.exists() else None))

    # --- ui-record: same, the verdict and evidence come from the bytes read after the check ----------------------
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = Path(cp3_write_combined(cp3_ui_round_dir(d, 1) / "combined.json", fp))
    original = good.read_bytes()
    real_read = m._read_round_review

    def ui_read_then_swap(*a, **k):
        res = real_read(*a, **k)
        cp3_write_combined(good, "0" * 40, diffs=[{"severity": "blocker", "location": "x", "description": "forged"}])
        return res
    m._read_round_review = ui_read_then_swap
    rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    ev = Path(d) / ".ratchet/runs/f/evidence/CP1/ui/round-01/combined.json"
    check("[CP3-X03] ui-record: the verdict uses the bytes read after the location check (a later rewrite is ignored)",
          rc == 0 and cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"].get("status") == "passed", (rc, err))
    check("[CP3-X03] ui-record: the evidence copy holds the checked bytes, not the file re-read by name",
          ev.exists() and ev.read_bytes() == original, (ev.exists(), ev.read_bytes()[:200] if ev.exists() else None))

    # --- a swap between the location check and the open is refused (a link, or a different file) -----------------
    for label, make_link in (("a link to an outside file", True), ("a different file renamed over it", False)):
        m = engine()
        env, d, rnd, rid = cp3x_adv_ready()
        target = Path(rnd["output_dir"]) / f"{rid}.json"
        cp3_write_review(target, rnd, rid, findings=[{"id": "REAL", "severity": "blocker", "file": "src/f.js"}])
        evil = Path(cp3_write_review(Path(tempfile.mkdtemp(prefix="ratchet-trust-x04-")) / "evil.json", rnd, rid))

        def swap(target=target, evil=evil, make_link=make_link):
            tmp = target.with_name(target.name + ".swap")
            if make_link:
                tmp.symlink_to(evil)
            else:
                shutil.copyfile(str(evil), str(tmp))
            os.replace(str(tmp), str(target))
        opener, real_open = cp3x_swap_on_open(target.name, swap)
        os.open = opener
        try:
            rc, err = cp3x_in_process(m, d, env, "adv-record", "CP1", "--reviews", str(target))
        finally:
            os.open = real_open
        check(f"[CP3-X04] adv-record: {label} swapped in after the location check is refused, nothing recorded",
              rc != 0 and "changed while it was being checked" in err and cp3x_adv_unrecorded(d), (rc, err))

    m = engine()
    env, d, fp = cp3x_ui_ready()
    target = cp3_ui_round_dir(d, 1) / "combined.json"
    cp3_write_combined(target, fp, diffs=[{"severity": "blocker", "location": "x", "description": "real"}])
    evil = Path(cp3_write_combined(Path(tempfile.mkdtemp(prefix="ratchet-trust-x04ui-")) / "evil.json", fp))

    def ui_swap():
        tmp = target.with_name("combined.json.swap")
        tmp.symlink_to(evil)
        os.replace(str(tmp), str(target))
    opener, real_open = cp3x_swap_on_open("combined.json", ui_swap)
    os.open = opener
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(target))
    finally:
        os.open = real_open
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X04] ui-record: a link swapped in after the location check is refused, and the gate doesn't pass",
          rc != 0 and "changed while it was being checked" in err and ui.get("status") != "passed"
          and ui.get("attempts", 0) == 0, (rc, err, ui))


SECTIONS.append(("CP3", cp3_adv_round1_location_first))


# ---- CP3 adversarial round 2 fixes ----
# A review file's size is bounded, and an OSError raised while opening/fstat'ing/reading a review (or writing its
# evidence copy) becomes a plain die() message, never a bare traceback.

def cp3_adv_round2_size_and_io_errors():
    print("CP3 adversarial round 2: an oversized review is refused by size, and a read OSError is a plain message")

    # --- an oversized review, correctly located, is refused by size before being read into memory -----------------
    m = engine()
    env, d, rnd, rid = cp3x_adv_ready()
    big = Path(rnd["output_dir"]) / f"{rid}.json"
    big.write_bytes(b"x" * (m.MAX_REVIEW_BYTES + 1))
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(big))
    check("[CP3-X05] adv-record: a correctly-located review over the size limit is refused, names the limit, "
          "and isn't read into memory or left as a traceback",
          r.returncode != 0 and str(m.MAX_REVIEW_BYTES) in r.stderr and "Traceback" not in r.stderr
          and cp3x_adv_unrecorded(d), (r.returncode, r.stderr))

    # --- an OSError raised while reading an otherwise-valid, correctly-located review becomes a plain die message ---
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = Path(cp3_write_combined(cp3_ui_round_dir(d, 1) / "combined.json", fp))
    real_read = os.read
    state = {"raised": False}

    def failing_read(fd, n):
        if not state["raised"]:
            state["raised"] = True
            raise OSError(5, "Input/output error")
        return real_read(fd, n)
    os.read = failing_read
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        os.read = real_read
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X06] ui-record: an OSError raised while reading a correctly-located review becomes a plain die "
          "message (not a bare traceback), and the gate doesn't pass",
          rc != 0 and "Could not read" in err and "Input/output error" in err and "Traceback" not in err
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))


SECTIONS.append(("CP3", cp3_adv_round2_size_and_io_errors))


# ---- CP3 adversarial round 3 fixes ----
# Round 2 covered a read OSError ([CP3-X06]); these cover the other two OSError paths named by round 3: the
# fstat identity checks (dir-fd and file-fd) in _read_round_review, and the evidence write_bytes in
# _write_review_evidence. Each must become a plain die() message, never a bare traceback.

def cp3_adv_round3_fstat_and_write_errors():
    print("CP3 adversarial round 3: fstat and evidence-write OSErrors become plain die messages, not bare tracebacks")

    # --- an OSError from os.fstat(dfd), the dir-fd identity check, becomes the "changed" message --------------------
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = Path(cp3_write_combined(cp3_ui_round_dir(d, 1) / "combined.json", fp))
    real_fstat = os.fstat
    state = {"calls": 0}

    def failing_fstat_dir(fd):
        state["calls"] += 1
        if state["calls"] == 1:
            raise OSError(5, "Input/output error")
        return real_fstat(fd)
    os.fstat = failing_fstat_dir
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        os.fstat = real_fstat
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X07] ui-record: an OSError raised by the dir-fd fstat identity check becomes a plain die message "
          "(not a bare traceback), and the gate doesn't pass",
          rc != 0 and "changed while it was being checked" in err and "Input/output error" in err
          and "Traceback" not in err and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))

    # --- an OSError from os.fstat(fd), the opened file's own fstat, becomes "Could not inspect" ----------------------
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = Path(cp3_write_combined(cp3_ui_round_dir(d, 1) / "combined.json", fp))
    state = {"calls": 0}

    def failing_fstat_file(fd):
        state["calls"] += 1
        if state["calls"] == 2:
            raise OSError(5, "Input/output error")
        return real_fstat(fd)
    os.fstat = failing_fstat_file
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        os.fstat = real_fstat
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X07] ui-record: an OSError raised while inspecting the opened review file becomes a plain die "
          "message naming the operation (not a bare traceback), and the gate doesn't pass",
          rc != 0 and "Could not inspect" in err and "Input/output error" in err and "Traceback" not in err
          and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))

    # --- an OSError raised while writing the evidence copy (write_bytes) becomes a plain die message -----------------
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = Path(cp3_write_combined(cp3_ui_round_dir(d, 1) / "combined.json", fp))
    real_write_bytes = Path.write_bytes
    state = {"raised": False}

    def failing_write_bytes(self, data):
        if not state["raised"] and self.name == "combined.json" and "evidence" in self.parts:
            state["raised"] = True
            raise OSError(28, "No space left on device")
        return real_write_bytes(self, data)
    Path.write_bytes = failing_write_bytes
    try:
        rc, err = cp3x_in_process(m, d, env, "ui-record", "CP1", "--review", str(good))
    finally:
        Path.write_bytes = real_write_bytes
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP3-X07] ui-record: an OSError raised while writing the evidence copy becomes a plain die message "
          "naming the evidence path (not a bare traceback), and the gate doesn't pass",
          rc != 0 and "Could not write the evidence copy to" in err and "No space left on device" in err
          and "Traceback" not in err and ui.get("status") != "passed" and ui.get("attempts", 0) == 0, (rc, err, ui))


SECTIONS.append(("CP3", cp3_adv_round3_fstat_and_write_errors))


# ---- CP4 ----
# Agent-set overrides no longer bypass checks. RATCHET_HUMAN_OVERRIDE only works through a real controlling
# terminal where the person types back a short code Ratchet prints; RATCHET_ROLE / RATCHET_HOME /
# RATCHET_DIRECTOR_KEY_FILE cannot be smuggled past the guard by indirect shell tricks (declare -x, typeset, an
# indirect eval, or a python3 -c os.environ assignment); and even a value that slipped past the guard entirely must
# still be ignored by the engine itself. Pty-driven cases use Python's own os.forkpty (never the test runner's own
# tty, which is not a real terminal under CI) and skip, with a clear "not run" line, only on platforms with no pty
# (Windows) - they never silently pass.

CP4_CODE_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


def cp4_find_code(output):
    m = CP4_CODE_RE.search(output)
    return m.group(1) if m else None


def cp4_wrong_code(code):
    return code[:-1] + ("1" if code[-1] != "1" else "2")


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


def cp4_answer_wrong(master, output):
    code = cp4_find_code(output)
    if not code:
        return False
    os.write(master, (cp4_wrong_code(code) + "\n").encode())
    return True


def cp4_close_after_code(master, output):
    return "close" if cp4_find_code(output) else False


def cp4_awaiting_review(env):
    """One checkpoint, behavior and adversarial gates both passed, parked at the final human review (the same
    human-approve flow as a per-checkpoint review: cmd_human_approve treats awaiting_checkpoint_review and
    awaiting_final_review alike), built from this file's own CP3 fixtures."""
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = cp3_write_review(Path(rnd["output_dir"]) / f"{rid}.json", rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", good)
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, env, "complete", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    phase = cp3_state(d)["phase"]
    assert phase in ("awaiting_checkpoint_review", "awaiting_final_review"), phase
    return d


def cp4_tamper_fixture(env):
    """state.json hand-edited outside ratchet.py: the fixture accept-state exists for."""
    d = project(env)
    sp = Path(d) / ".ratchet/runs/f/state.json"
    st = json.loads(sp.read_text())
    st["tampered_marker"] = "edited by hand, not by ratchet.py"
    sp.write_text(json.dumps(st))
    return d


def cp4_unapproved_plan(env):
    """A submitted plan parked awaiting_plan_approval (mirrors cp3_project, minus the final approve-plan)."""
    d = Path(tempfile.mkdtemp(prefix="ratchet-trust-cp4-"))
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
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    return d, tp


def cp4_say_yes(d, tp, env, text="yes, build it", session="s-owner"):
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    return rx(d, env, "hook", "prompt", "--host", "claude",
              stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": session, "transcript_path": str(tp)}))


def cp4_guard_bash(d, env, command):
    payload = {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    return rx(d, env, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))


def cp4_human_log_lines(d):
    p = Path(d) / ".ratchet" / "human_log.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def cp4_override_needs_terminal():
    print("CP4: RATCHET_HUMAN_OVERRIDE only works through a real terminal, with the printed code typed back")

    # --- T01: no tty at all (plain pipes, as an agent's own Bash tool call would be): the bare override approves
    # and accepts nothing, for both human-approve and accept-state -------------------------------------------------
    env = make_env("claude")
    d = cp4_awaiting_review(env)
    phase_before = cp3_state(d)["phase"]
    r = rx(d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), "human-approve")
    phase_after = cp3_state(d)["phase"]
    check("[CP4-T01] human-approve: the bare override with no tty is refused and approves nothing",
          r.returncode != 0 and phase_after == phase_before
          and re.search(r"person.*(own )?terminal|terminal.*person", r.stderr + r.stdout, re.I) is not None,
          (r.returncode, r.stderr, phase_before, phase_after))

    env2 = make_env("claude")
    d2 = cp4_tamper_fixture(env2)
    r2 = rx(d2, dict(env2, RATCHET_HUMAN_OVERRIDE="1"), "accept-state")
    check("[CP4-T01] accept-state: the bare override with no tty is refused, nothing accepted as a new baseline",
          r2.returncode != 0
          and re.search(r"person.*(own )?terminal|terminal.*person", r2.stderr + r2.stdout, re.I) is not None,
          (r2.returncode, r2.stderr))
    r3 = rx(d2, env2, "status")
    check("[CP4-T01] accept-state: the hand-edited state is still refused afterwards (never silently accepted)",
          r3.returncode != 0, (r3.returncode, r3.stderr))

    # --- T02: a real pty, the exact printed code typed back: approves, records the override, and advances ----------
    if not HAVE_PTY:
        print("  - [CP4-T02] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        phase_before = cp3_state(d)["phase"]
        rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
        st = cp3_state(d)
        last_log = cp4_human_log_lines(d)[-1] if cp4_human_log_lines(d) else {}
        check("[CP4-T02] typing back the exact printed code approves, advances past the pause, and the record "
              "notes the override",
              rc == 0 and cp4_find_code(out) is not None and st["phase"] != phase_before
              and last_log.get("by") == "human" and "override" in json.dumps(last_log).lower(),
              (rc, out[-300:], phase_before, st.get("phase"), last_log))

    # --- T03: typing back a wrong code rejects the override: nothing approved, still waiting ------------------------
    if not HAVE_PTY:
        print("  - [CP4-T03] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        phase_before = cp3_state(d)["phase"]
        rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_wrong)
        st = cp3_state(d)
        check("[CP4-T03] a wrong code dies with a plain message; the run is still waiting, nothing approved",
              rc != 0 and cp4_find_code(out) is not None and st["phase"] == phase_before
              and "Traceback" not in out, (rc, out[-300:], phase_before, st.get("phase")))

    # --- T04: the terminal goes away (EOF) before any reply: never falls back to the bare env var as consent --------
    if not HAVE_PTY:
        print("  - [CP4-T04] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        phase_before = cp3_state(d)["phase"]
        rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_close_after_code)
        st = cp3_state(d)
        check("[CP4-T04] EOF before any reply approves nothing; the bare env var alone is never treated as consent",
              rc != 0 and st["phase"] == phase_before, (rc, out[-300:], phase_before, st.get("phase")))


SECTIONS.append(("CP4", cp4_override_needs_terminal))


def cp4_guard_indirect_env_tricks():
    print("CP4: the guard blocks indirect shell tricks that set a reserved env var, not just a literal VAR=")

    def table(commands, msg_re):
        env = make_env("claude")
        d = project(env, approved=False)
        bad, bad_msg = [], []
        for label, cmd in commands:
            r = cp4_guard_bash(d, env, cmd)
            if r.returncode != 2:
                bad.append((label, r.returncode, r.stderr))
            elif not re.search(msg_re, r.stderr + r.stdout, re.I):
                bad_msg.append((label, r.stderr))
        return bad, bad_msg

    # --- T05: RATCHET_HUMAN_OVERRIDE ---------------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve"),
        ("typeset", "typeset RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve"),
        ("eval indirect", 'V=RATCHET_HUMAN_OVERRIDE; eval "$V=1 ratchet.py human-approve"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_HUMAN_OVERRIDE']='1'; "
                               "subprocess.run(['python3','ratchet.py','human-approve'])\""),
    ], r"override.*(person|terminal)|terminal.*person")
    check("[CP4-T05] every indirect way of setting RATCHET_HUMAN_OVERRIDE is refused by the guard (exit 2)",
          bad == [], bad)
    check("[CP4-T05] each refusal names the override as reserved for a person at their own terminal",
          bad_msg == [], bad_msg)

    # --- T06: RATCHET_ROLE ----------------------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_ROLE=director; ratchet.py director-key"),
        ("typeset", "typeset RATCHET_ROLE=director; ratchet.py director-sign approve-plan"),
        ("eval indirect", 'V=RATCHET_ROLE; eval "$V=director ratchet.py director-key"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_ROLE']='director'; "
                               "subprocess.run(['python3','ratchet.py','director-key'])\""),
    ], r"director session|RATCHET_ROLE")
    check("[CP4-T06] every indirect way of setting RATCHET_ROLE is refused by the guard (exit 2)", bad == [], bad)
    check("[CP4-T06] each refusal names RATCHET_ROLE/director commands as reserved for the director session",
          bad_msg == [], bad_msg)

    # --- T07: RATCHET_HOME -----------------------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_HOME=./evil-home; ratchet.py status"),
        ("typeset", "typeset RATCHET_HOME=./evil-home; ratchet.py status"),
        ("eval indirect", 'V=RATCHET_HOME; eval "$V=./evil-home ratchet.py status"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_HOME']='./evil-home'; "
                               "subprocess.run(['python3','ratchet.py','status'])\""),
    ], r"per-user folder|RATCHET_HOME")
    check("[CP4-T07] every indirect way of setting RATCHET_HOME is refused by the guard (exit 2)", bad == [], bad)
    check("[CP4-T07] each refusal names Ratchet's per-user folder as off-limits to builder sessions",
          bad_msg == [], bad_msg)

    # --- T08: RATCHET_DIRECTOR_KEY_FILE ----------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan"),
        ("typeset", "typeset RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan"),
        ("eval indirect", 'V=RATCHET_DIRECTOR_KEY_FILE; eval "$V=./evil.key ratchet.py director-sign approve-plan"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_DIRECTOR_KEY_FILE']="
                               "'./evil.key'; subprocess.run(['python3','ratchet.py','director-sign','approve-plan'])\""),
    ], r"director key|RATCHET_DIRECTOR_KEY_FILE")
    check("[CP4-T08] every indirect way of setting RATCHET_DIRECTOR_KEY_FILE is refused by the guard (exit 2)",
          bad == [], bad)
    check("[CP4-T08] each refusal says the director key path cannot be redirected by a builder session",
          bad_msg == [], bad_msg)

    # --- T14: the existing literal-override guard case is not weakened ----------------------------------------------
    env = make_env("claude")
    d = project(env, approved=False)
    r = cp4_guard_bash(d, env, "RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan")
    check("[CP4-T14] the literal inline-override form is still refused exactly as before CP4 (additive, not weaker)",
          r.returncode == 2, (r.returncode, r.stderr))


SECTIONS.append(("CP4", cp4_guard_indirect_env_tricks))


def cp4_tampered_session_ignored():
    print("CP4: a tampered role/home that slipped past the guard is still ignored by the engine, and a disagreeing "
          "RATCHET_HOME is refused")

    # --- T09: a fresh RATCHET_HOME the test fully controls, no legitimate director key or hook history --------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    fake_home = Path(tempfile.mkdtemp(prefix="ratchet-trust-fakehome-"))
    fake_env = dict(env, RATCHET_ROLE="director", RATCHET_HOME=str(fake_home))
    rx(d, fake_env, "director-key")
    rx(d, fake_env, "director-enroll")
    sg = rx(d, fake_env, "director-sign", "approve-plan")
    forged = sg.stdout.strip().splitlines()[-1] if sg.returncode == 0 and sg.stdout.strip() else "forged-token"
    r = rx(d, env, "approve-plan", "--director-token", forged)
    phase_after_token = cp3_state(d)["phase"]
    check("[CP4-T09] a director token forged from a fresh, unrecorded RATCHET_HOME does not approve the plan",
          r.returncode != 0 and phase_after_token == "awaiting_plan_approval",
          (r.returncode, r.stderr, phase_after_token))
    r2 = cp4_say_yes(d, tp, env)
    assert r2.returncode == 0, r2.stderr + r2.stdout
    r3 = rx(d, env, "approve-plan")
    phase_after_chat = cp3_state(d)["phase"]
    check("[CP4-T09] the lane was not enrolled by the attempt: a normal chat yes from the real user still approves it",
          r3.returncode == 0 and phase_after_chat == "building", (r3.returncode, r3.stderr, phase_after_chat))

    # --- T10: a RATCHET_HOME that disagrees with what the hooks recorded is refused ---------------------------------
    env = make_env("claude")
    d = project(env)  # install + fire_claude already ran the hooks against this project with env's RATCHET_HOME (H1)
    h2 = Path(tempfile.mkdtemp(prefix="ratchet-trust-h2-"))
    env2 = dict(env, RATCHET_HOME=str(h2))
    r = rx(d, env2, "status")
    check("[CP4-T10] a later command under a different RATCHET_HOME (H2) is refused, not silently run against H2",
          r.returncode != 0
          and re.search(r"per-user folder|RATCHET_HOME", r.stderr + r.stdout, re.I) is not None
          and re.search(r"match|differ|disagree|mismatch", r.stderr + r.stdout, re.I) is not None,
          (r.returncode, r.stderr, env["RATCHET_HOME"], str(h2)))

    # --- T11: the SAME RATCHET_HOME the hooks recorded keeps working (no false positive) ------------------------------
    r2 = rx(d, env, "status")
    check("[CP4-T11] the same, legitimate RATCHET_HOME the hooks used keeps working exactly as before",
          r2.returncode == 0 and "Traceback" not in r2.stderr, (r2.returncode, r2.stderr))


SECTIONS.append(("CP4", cp4_tampered_session_ignored))


def cp4_director_and_override_regressions():
    print("CP4: a real director session still works, and a real person at a real tty can still use the override")

    # --- T12: a real director session enrolls a lane and signs an approval ------------------------------------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    denv = dict(env, RATCHET_ROLE="director")
    rk = rx(d, denv, "director-key")
    en = rx(d, denv, "director-enroll")
    sg = rx(d, denv, "director-sign", "approve-plan")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    check("[CP4-T12] a real director session creates the key, enrolls the lane, and signs a token",
          rk.returncode == 0 and en.returncode == 0 and sg.returncode == 0 and bool(tok),
          (rk.stderr, en.stderr, sg.stderr))
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-T12] the director-signed token is accepted by approve-plan and advances the run",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr, cp3_state(d)["phase"]))

    # --- T13: a person at their own terminal can still use the override for accept-state -----------------------------
    if not HAVE_PTY:
        print("  - [CP4-T13] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_tamper_fixture(env)
        rc, out = cp4_pty_run(["accept-state"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
        r = rx(d, env, "status")
        check("[CP4-T13] typing back the printed code accepts the hand-edited state as the new baseline",
              rc == 0 and cp4_find_code(out) is not None and r.returncode == 0,
              (rc, out[-300:], r.returncode, r.stderr))


SECTIONS.append(("CP4", cp4_director_and_override_regressions))


# ---- CP4 adversarial round 1: RATCHET_ROLE gets a structural backstop, one reserved-variable detector ----------

def cp4x_guard_tool(cwd, env, tool, tool_input, session="s"):
    payload = {"session_id": session, "cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": tool_input}
    return rx(cwd, env, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))


def cp4x_read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def cp4_adv_round1_role_backstop():
    print("CP4 adv round 1: a builder's script that sets RATCHET_ROLE gets nothing; a real director still works")

    # --- X01: the reviewer's exact bypass: write a script, then run it with a clean-looking command --------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    out = Path(tempfile.mkdtemp(prefix="ratchet-trust-x01-"))
    script = out / "x.sh"
    body = ("export RATCHET_ROLE=director\n"
            + "".join(f'"{sys.executable}" "{ENGINE}" {cmd} > "{out}/{name}.out" 2> "{out}/{name}.err"; '
                      f'echo $? > "{out}/{name}.rc"\n'
                      for name, cmd in (("key", "director-key"), ("enroll", "director-enroll"),
                                        ("sign", "director-sign approve-plan"))))
    w = cp4x_guard_tool(d, env, "Write", {"file_path": str(script), "content": body})
    script.write_text(body)  # what the Write tool would do once the guard let it through
    g = cp4x_guard_tool(d, env, "Bash", {"command": f"sh {script}"})
    check("[CP4-X01] the guard sees nothing in the outer commands (Write of the script, then `sh <script>`)",
          w.returncode == 0 and g.returncode == 0, (w.returncode, w.stderr, g.returncode, g.stderr))
    subprocess.run(["sh", str(script)], cwd=d, env=env, capture_output=True, text=True, timeout=120)
    rcs = {n: cp4x_read(out / f"{n}.rc") for n in ("key", "enroll", "sign")}
    errs = " ".join(cp4x_read(out / f"{n}.err") for n in ("key", "enroll", "sign"))
    check("[CP4-X01] director-key, director-enroll and director-sign run from the builder's script are all refused",
          all(rc not in ("", "0") for rc in rcs.values()), (rcs, errs[:300]))
    check("[CP4-X01] each refusal says plainly why (the hooks did not record a director session here)",
          errs.count("refused") >= 3 and re.search(r"not (started as|treated as) the director", errs) is not None,
          errs[:400])
    check("[CP4-X01] no director key was created and the lane was not enrolled by the script",
          not (Path(env["RATCHET_HOME"]) / "director.key").exists()
          and not (Path(d) / ".ratchet" / "director.json").exists())
    tok = (cp4x_read(out / "sign.out").splitlines() or ["forged-token"])[-1] or "forged-token"
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-X01] the follow-up `approve-plan --director-token` (plain env) is refused; the run still waits",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval",
          (r.returncode, r.stderr, cp3_state(d)["phase"]))

    # --- X02: the same script in Python, run from a folder with no .ratchet/ and aimed at the lane with --root ----
    elsewhere = Path(tempfile.mkdtemp(prefix="ratchet-trust-x02-"))
    py = elsewhere / "x.py"
    py_body = ("import os, subprocess, sys\n"
               "os.environ['RATCHET_ROLE'] = 'director'\n"
               f"for a in (['director-key'], ['director-enroll'], ['director-sign', 'approve-plan']):\n"
               f"    r = subprocess.run([sys.executable, {str(ENGINE)!r}, '--root', {str(d)!r}, *a],"
               " capture_output=True, text=True)\n"
               "    print(a[0], r.returncode)\n")
    py.write_text(py_body)
    g = cp4x_guard_tool(d, env, "Bash", {"command": f"cd {elsewhere} && python3 x.py"})
    pr = subprocess.run([sys.executable, str(py)], cwd=elsewhere, env=env, capture_output=True, text=True,
                        timeout=120)
    codes = dict(ln.split() for ln in pr.stdout.splitlines() if len(ln.split()) == 2)
    check("[CP4-X02] from a folder with no hook record, `--root <lane>` director commands in a script are refused",
          g.returncode == 0 and len(codes) == 3 and all(v != "0" for v in codes.values())
          and not (Path(d) / ".ratchet" / "director.json").exists(), (g.returncode, pr.stdout, pr.stderr[-300:]))
    r2 = cp4_say_yes(d, tp, env)
    r3 = rx(d, env, "approve-plan")
    check("[CP4-X02] the lane is still not enrolled: the real user's chat yes approves it as before",
          r2.returncode == 0 and r3.returncode == 0 and cp3_state(d)["phase"] == "building",
          (r3.returncode, r3.stderr))

    # --- X03: a real director session in its own folder (no .ratchet/), with hooks, still drives a lane via --root ---
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    b = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})  # the lane's builder has run commands in the lane
    home_dir = Path(tempfile.mkdtemp(prefix="ratchet-trust-director-"))
    denv = dict(env, RATCHET_ROLE="director")  # the director's agent CLI was started with it: its hooks have it
    steps = []
    for args in (["director-key"], ["director-enroll"], ["director-sign", "approve-plan"]):
        cmd = f"python3 {ENGINE} --root {d} {' '.join(args)}"
        g = cp4x_guard_tool(home_dir, denv, "Bash", {"command": cmd}, session="s-director")
        steps.append((g.returncode, rx(home_dir, denv, "--root", str(d), *args)))
    tok = steps[-1][1].stdout.strip().splitlines()[-1] if steps[-1][1].stdout.strip() else ""
    check("[CP4-X03] a real director session (own folder, hooks on) creates the key, enrolls the lane and signs",
          b.returncode == 0 and all(gr == 0 and s.returncode == 0 for gr, s in steps) and bool(tok),
          [(gr, s.returncode, s.stderr[-200:]) for gr, s in steps])
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-X03] its token is accepted by approve-plan in the lane and advances the run",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr))

    # --- X04: a director whose own folder is a Ratchet project (its guard recorded the director role there) --------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    dproj = Path(tempfile.mkdtemp(prefix="ratchet-trust-dproj-"))
    (dproj / ".ratchet").mkdir()
    denv = dict(env, RATCHET_ROLE="director")
    g = cp4x_guard_tool(dproj, denv, "Bash", {"command": f"python3 {ENGINE} --root {d} director-key"},
                        session="s-director")
    rec = json.loads((dproj / ".ratchet" / "hook-env.json").read_text())
    rk = rx(dproj, denv, "--root", str(d), "director-key")
    en = rx(dproj, denv, "--root", str(d), "director-enroll")
    sg = rx(dproj, denv, "--root", str(d), "director-sign", "approve-plan")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-X04] the director's own project records guard_role director, and --root lane commands work from it",
          g.returncode == 0 and rec.get("guard_role") == "director" and rk.returncode == 0 and en.returncode == 0
          and sg.returncode == 0 and r.returncode == 0 and cp3_state(d)["phase"] == "building",
          (rec, rk.stderr, en.stderr, sg.stderr, r.stderr))
    lane_rec = json.loads((Path(d) / ".ratchet" / "hook-env.json").read_text())
    check("[CP4-X04] the lane's record still shows its own builder session (the director never wrote it)",
          lane_rec.get("guard_role") == "builder", lane_rec)

    # --- X05: one reserved-variable detector; what the removed regexes covered is still refused -------------------
    env = make_env("claude")
    d = project(env)
    forms = ["export RATCHET_ROLE=director; python3 ratchet.py status",
             "env RATCHET_ROLE=director python3 ratchet.py status",
             "(RATCHET_ROLE=director python3 ratchet.py status)",
             "RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan",
             "export RATCHET_HUMAN_OVERRIDE=1; python3 ratchet.py human-approve",
             "env FOO=1 RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py human-approve"]
    res = [(f, cp4_guard_bash(d, env, f)) for f in forms]
    check("[CP4-X05] every form the old RATCHET_ROLE / RATCHET_HUMAN_OVERRIDE regexes caught is still refused",
          all(r.returncode == 2 for _f, r in res), [(f, r.returncode) for f, r in res if r.returncode != 2])
    m = engine()
    check("[CP4-X05] the guard has one reserved-variable detector (the old duplicate regexes are gone)",
          not hasattr(m, "ROLE_SET_RE") and not hasattr(m, "OVERRIDE_SET_RE") and hasattr(m, "_reserved_env_problem"))

    # --- X06: the schema doc matches the code ---------------------------------------------------------------------
    doc = (ENGINE.parents[1] / "references" / "schemas.md").read_text()
    tree = next((ln for ln in doc.splitlines() if ln.strip().startswith("human_log.jsonl")), "")
    check("[CP4-X06] schemas.md's file tree names both writers of human_log.jsonl (the prompt hook and log_override)",
          "log_override" in tree and "hook only" not in tree, tree)
    para = next((p for p in doc.split("\n\n") if p.startswith("**Variables a builder session may read")), "")
    check("[CP4-X06] schemas.md says how the engine itself distrusts RATCHET_ROLE (the hook-recorded role)",
          "guard_role" in para and "RATCHET_ROLE" in para, para[-400:])


SECTIONS.append(("CP4", cp4_adv_round1_role_backstop))


# ---- CP4 adversarial round 2: the hook-recorded role and folder fail closed; the Windows reader times out --------

class cp4y_deny_hook_env:
    """The OS refusing writes of .ratchet/hook-env.json (and its temp file) only, through the engine's module-level
    open(), as deny_writes does for the crash-state paths."""

    def __init__(self, m):
        self.m = m

    def __enter__(self):
        import builtins
        real = builtins.open

        def fake(file, mode="r", *a, **k):
            if isinstance(file, (str, os.PathLike)) and any(c in mode for c in "wax+") \
                    and Path(file).name.startswith("hook-env.json"):
                raise PermissionError(13, "Permission denied (test)", str(file))
            return real(file, mode, *a, **k)
        self.m.open = fake
        return self

    def __exit__(self, *exc):
        del self.m.open


def cp4y_guard_in_process(m, d, command):
    """One real builder guard run through run_hook, in this process (so the write fault applies)."""
    import contextlib
    import io
    payload = {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    out, err, real_in = io.StringIO(), io.StringIO(), sys.stdin
    saved_role = os.environ.pop("RATCHET_ROLE", None)
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = m.run_hook("guard", "claude")
    finally:
        sys.stdin = real_in
        if saved_role is not None:
            os.environ["RATCHET_ROLE"] = saved_role
    return rc, out.getvalue(), err.getvalue()


def cp4y_director_cmds(cwd, env, root=None):
    pre = ["--root", str(root)] if root else []
    return [rx(cwd, env, *pre, *a) for a in (["director-key"], ["director-enroll"], ["director-sign", "approve-plan"])]


def cp4_adv_round2_fail_closed():
    print("CP4 adv round 2: no hook record means no director (unless no hooks are installed at all); write "
          "failures are reported, never swallowed; the Windows terminal read times out")

    # --- X07: hooks installed; the builder's guard runs but cannot save its FIRST record -------------------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    rec = Path(d) / ".ratchet" / "hook-env.json"
    if rec.exists():
        rec.unlink()  # no record written yet: the guard's run below is the first one
    install(d, env, "claude")
    m = engine()
    with engine_env(env), cp4y_deny_hook_env(m):
        rc, out, err = cp4y_guard_in_process(m, d, "ls")
    with engine_env(env):
        unresolved = m.unresolved_hook_crashes(Path(d), "claude")
    log = errors_path(d).read_text() if errors_path(d).exists() else ""
    check("[CP4-X07] the guard still decides (ls allowed) and its record write really failed (no hook-env.json)",
          rc == 0 and not rec.exists(), (rc, out, err))
    check("[CP4-X07] the failed write is said where the host shows hook output, not swallowed",
          "could not save" in (out + err) and "hook-env.json" in (out + err), (out, err))
    check("[CP4-X07] the failed write is recorded like a hook crash (unresolved guard crash + hook-errors.log line)",
          any(c["event"] == "guard" for c in unresolved) and "HookRecordError" in log, (unresolved, log[-300:]))
    benv = dict(env, RATCHET_ROLE="director")  # what the builder's script sets
    res = cp4y_director_cmds(d, benv)
    check("[CP4-X07] with the record missing after a failed write, director-key/enroll/sign are all refused",
          all(r.returncode != 0 for r in res) and all("refused" in r.stderr for r in res),
          [(r.returncode, r.stderr[-200:]) for r in res])
    check("[CP4-X07] no director key was created and the lane was not enrolled",
          not (Path(env["RATCHET_HOME"]) / "director.key").exists()
          and not (Path(d) / ".ratchet" / "director.json").exists())
    fake_home = Path(tempfile.mkdtemp(prefix="ratchet-trust-x07-home-"))
    r = rx(d, dict(env, RATCHET_HOME=str(fake_home)), "status")
    check("[CP4-X07] the per-user folder check fails closed too: a made-up RATCHET_HOME is refused, not trusted",
          r.returncode != 0 and "hook-env.json" in r.stderr and "missing" in r.stderr, (r.returncode, r.stderr))
    g = fire_guard(d, env)  # the writes work again: the builder's next guard run saves the record
    rec_now = json.loads(rec.read_text()) if rec.exists() else {}
    r = rx(d, env, "status")
    res = cp4y_director_cmds(d, benv)
    check("[CP4-X07] once a hook saves the record the run works again, and the builder still gets no director role",
          g.returncode == 0 and rec_now.get("guard_role") == "builder" and r.returncode == 0
          and all(x.returncode != 0 for x in res), (g.stderr, rec_now, r.stderr, [x.stderr[-150:] for x in res]))

    # --- X08: hooks installed, no record at all (never written): RATCHET_ROLE=director is not taken at its word ---
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    rec = Path(d) / ".ratchet" / "hook-env.json"
    if rec.exists():
        rec.unlink()
    install(d, env, "claude")
    denv = dict(env, RATCHET_ROLE="director")
    res = cp4y_director_cmds(d, denv)
    check("[CP4-X08] hooks installed + no hook record: director-key/enroll/sign are refused",
          all(r.returncode != 0 for r in res) and not (Path(env["RATCHET_HOME"]) / "director.key").exists(),
          [(r.returncode, r.stderr[-200:]) for r in res])
    check("[CP4-X08] the refusal says plainly what to do (hooks are installed; restart the director's agent CLI)",
          "hooks are installed" in res[0].stderr and "Restart the director's agent CLI" in res[0].stderr,
          res[0].stderr)
    spoof = cp4y_director_cmds(d, dict(denv, RATCHET_HOST_PROVIDER="openai"))
    check("[CP4-X08] claiming another agent CLI (RATCHET_HOST_PROVIDER) does not make the installed hooks vanish",
          all(r.returncode != 0 for r in spoof), [(r.returncode, r.stderr[-150:]) for r in spoof])
    elsewhere = Path(tempfile.mkdtemp(prefix="ratchet-trust-x08-"))
    res = cp4y_director_cmds(elsewhere, denv, root=d)
    check("[CP4-X08] from a folder no director hook recorded, `--root <lane>` director commands are refused too",
          all(r.returncode != 0 for r in res) and not (Path(d) / ".ratchet" / "director.json").exists(),
          [(r.returncode, r.stderr[-150:]) for r in res])
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude", scope="user")  # installed for the user, in no project
    elsewhere = Path(tempfile.mkdtemp(prefix="ratchet-trust-x08u-"))
    res = cp4y_director_cmds(elsewhere, dict(env, RATCHET_ROLE="director"), root=d)
    check("[CP4-X08] user-scope hooks count as installed: with no director record the commands are refused",
          all(r.returncode != 0 for r in res), [(r.returncode, r.stderr[-150:]) for r in res])
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)  # hooks delivered without install-hooks config (a plugin): the guard has run
    cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    rec = Path(d) / ".ratchet" / "hook-env.json"
    if rec.exists():
        rec.unlink()
    res = cp4y_director_cmds(d, dict(env, RATCHET_ROLE="director"))
    check("[CP4-X08] hooks with no install config that have enforced here (a guard ran) count as in use: refused",
          all(r.returncode != 0 for r in res) and not (Path(env["RATCHET_HOME"]) / "director.key").exists(),
          [(r.returncode, r.stderr[-150:]) for r in res])
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)  # no hooks installed anywhere (CP4-T12's setup)
    rk = rx(d, dict(env, RATCHET_ROLE="director"), "director-key")
    check("[CP4-X08] with no hooks installed at all the variable is taken at its word, and it says so plainly",
          rk.returncode == 0 and "hooks are not installed" in rk.stderr and "taken at its word" in rk.stderr,
          (rk.returncode, rk.stderr))

    # --- X09: the Windows terminal read is bounded by OVERRIDE_TIMEOUT and refuses on expiry ----------------------
    import inspect
    import io
    import threading
    m = engine()
    rfd, wfd = os.pipe()
    blocking = os.fdopen(rfd, "r")  # nothing is ever written: readline() would wait forever
    t0 = time.time()
    got = m._read_line_with_timeout(blocking.readline, 0.3)
    check("[CP4-X09] the bounded read returns nothing once the timeout passes (no answer)",
          got is None and time.time() - t0 < 5, (got, time.time() - t0))
    check("[CP4-X09] a typed line is returned, and end of input counts as no answer",
          m._read_line_with_timeout(lambda: "123456\n", 5) == "123456\n"
          and m._read_line_with_timeout(lambda: "", 5) is None)
    src = inspect.getsource(m._open_terminal)
    check("[CP4-X09] the Windows branch reads the console through the bounded read, with OVERRIDE_TIMEOUT",
          "_read_line_with_timeout(fin.readline, OVERRIDE_TIMEOUT)" in src, src[:600])

    class NtOs:  # the engine's view of the OS, as on Windows; everything else is the real os module
        name = "nt"

        def __getattr__(self, k):
            return getattr(os, k)
    rfd2, wfd2 = os.pipe()
    conin, conout = os.fdopen(rfd2, "r"), io.StringIO()
    import builtins
    real_open = builtins.open

    def fake_open(file, mode="r", *a, **k):
        if file == "CONIN$":
            return conin
        if file == "CONOUT$":
            return conout
        return real_open(file, mode, *a, **k)
    box, err = [], io.StringIO()

    def run():
        import contextlib
        try:
            with contextlib.redirect_stderr(err):
                m.human_override("check the Windows timeout")
            box.append("approved")
        except SystemExit as e:
            box.append(("refused", e.code))
        except Exception as e:
            box.append(("error", repr(e)))
    saved_ov = os.environ.get("RATCHET_HUMAN_OVERRIDE")
    m.os, m.open, m.OVERRIDE_TIMEOUT = NtOs(), fake_open, 0.5
    os.environ["RATCHET_HUMAN_OVERRIDE"] = "1"
    try:
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(20)
    finally:
        m.os = os
        del m.open
        if saved_ov is None:
            os.environ.pop("RATCHET_HUMAN_OVERRIDE", None)
        else:
            os.environ["RATCHET_HUMAN_OVERRIDE"] = saved_ov
    check("[CP4-X09] on Windows an unanswered code is refused once OVERRIDE_TIMEOUT passes (it never waits forever)",
          not th.is_alive() and box and box[0][0] == "refused" and "No code was typed back" in err.getvalue()
          and not m._OVERRIDE_CONFIRMED, (th.is_alive(), box, err.getvalue()[-300:]))
    for fd in (wfd, wfd2):
        os.close(fd)


SECTIONS.append(("CP4", cp4_adv_round2_fail_closed))

# ---- end CP4 ----


def cp4_adv_round3_stale_record_and_recovery():
    print("CP4 adv round 3: an old director record is not trusted while a hook failure stands; status, next, "
          "doctor, view and install-hooks still run when the hook record can't be trusted")

    # --- X10: the reviewer's scenario: a director record exists, then a builder's guard can't replace it ---------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    rec = Path(d) / ".ratchet" / "hook-env.json"
    denv = dict(env, RATCHET_ROLE="director")
    benv = dict(env, RATCHET_ROLE="director")  # what the builder's script sets (its CLI was not started with it)
    g = cp4x_guard_tool(d, denv, "Bash", {"command": "ls"}, session="s-director")
    k = rx(d, denv, "director-key")
    check("[CP4-X10] setup: a real director guard recorded the director role here, and director-key works",
          g.returncode == 0 and json.loads(rec.read_text()).get("guard_role") == "director" and k.returncode == 0,
          (g.stderr, rec.read_text() if rec.exists() else None, k.returncode, k.stderr[-200:]))
    m = engine()
    with engine_env(env), cp4y_deny_hook_env(m):
        rc, out, err = cp4y_guard_in_process(m, d, "ls")  # the builder's guard: its builder record can't be saved
    stale = json.loads(rec.read_text()) if rec.exists() else {}
    check("[CP4-X10] the builder's guard write really failed and the old director record is still on disk",
          rc == 0 and stale.get("guard_role") == "director" and "could not save" in (out + err), (rc, stale, out, err))
    res = cp4y_director_cmds(d, benv)
    check("[CP4-X10] with the stale director record, director-key/enroll/sign from the builder are all refused",
          all(r.returncode != 0 for r in res) and all("refused" in r.stderr for r in res)
          and res[2].stdout.strip() == "" and not (Path(d) / ".ratchet" / "director.json").exists(),
          [(r.returncode, r.stdout[-80:], r.stderr[-200:]) for r in res])
    check("[CP4-X10] the refusal says why plainly (a hook failed, the record may be out of date, run doctor)",
          "out of date" in res[2].stderr and "doctor" in res[2].stderr and "guard hook" in res[2].stderr,
          res[2].stderr)
    f = rx(d, env, "fingerprint")
    check("[CP4-X10] the per-user folder / key-file check does not trust the stale record either (fingerprint refused)",
          f.returncode != 0 and "out of date" in f.stderr, (f.returncode, f.stderr[-300:]))
    s = rx(d, env, "status", "--json")
    check("[CP4-X10] status --json still runs and says the hook record is not confirmed",
          s.returncode == 0 and js(s) is not None and "not confirmed" in s.stderr, (s.returncode, s.stderr[-300:]))
    g = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})  # the builder's next guard run saves its record
    res = cp4y_director_cmds(d, benv)
    f = rx(d, env, "fingerprint")
    check("[CP4-X10] once the builder's guard saves its record, the run works again and the builder is no director",
          g.returncode == 0 and json.loads(rec.read_text()).get("guard_role") == "builder" and f.returncode == 0
          and all(r.returncode != 0 for r in res), (g.stderr, f.stderr[-200:], [r.stderr[-150:] for r in res]))
    g = cp4x_guard_tool(d, denv, "Bash", {"command": "ls"}, session="s-director")
    sg = rx(d, denv, "director-sign", "approve-plan")
    check("[CP4-X10] a real director guard run records the role again and director-sign works",
          g.returncode == 0 and sg.returncode == 0 and sg.stdout.strip() != "", (sg.returncode, sg.stderr[-300:]))

    # --- X10b: the director's own folder record is stale while a lane is reached with --root ---------------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    dproj = Path(tempfile.mkdtemp(prefix="ratchet-trust-x10b-"))
    (dproj / ".ratchet").mkdir()
    denv = dict(env, RATCHET_ROLE="director")
    cp4x_guard_tool(dproj, denv, "Bash", {"command": "ls"}, session="s-director")
    with engine_env(env), cp4y_deny_hook_env(m):
        cp4y_guard_in_process(m, dproj, "ls")  # a builder session in that folder; its record write fails
    r = rx(dproj, denv, "--root", str(d), "director-key")
    check("[CP4-X10] a stale director record in the own folder (a failed write there) is refused with --root too",
          r.returncode != 0 and "refused" in r.stderr and "out of date" in r.stderr
          and not (Path(env["RATCHET_HOME"]) / "director.key").exists(), (r.returncode, r.stderr[-300:]))

    # --- X11: .ratchet/ read-only: the record is missing and the hook can't even save the crash -----------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    rdir = Path(d) / ".ratchet"
    if (rdir / "hook-env.json").exists():
        (rdir / "hook-env.json").unlink()
    os.chmod(rdir, 0o555)
    try:
        probe_ok = False
        try:
            (rdir / "probe").write_text("x")
            (rdir / "probe").unlink()
            probe_ok = True
        except OSError:
            pass
        if probe_ok:  # root, or an OS whose folder permissions don't stop writes: the setup can't be made here
            print("  - [CP4-X11] not run: this user/OS can write to a read-only folder, so the setup can't be made")
        else:
            g = fire_guard(d, env)
            check("[CP4-X11] setup: the guard could not save the record, nor the crash (it says to run doctor)",
                  g.returncode == 0 and not (rdir / "hook-env.json").exists() and "doctor" in g.stderr, g.stderr)
            fp = rx(d, env, "fingerprint")
            check("[CP4-X11] other commands are still refused, saying the record is missing",
                  fp.returncode != 0 and "Refused" in fp.stderr and "missing" in fp.stderr, fp.stderr[-300:])
            doc = rx(d, env, "doctor")
            check("[CP4-X11] doctor still runs: it prints its report and says plainly what is wrong",
                  "Host running this command" in doc.stdout and "hook-env.json" in doc.stderr
                  and "missing" in doc.stderr and "Traceback" not in doc.stderr, (doc.returncode, doc.stderr[-300:]))
            dj = rx(d, env, "doctor", "--json")
            st, nx = rx(d, env, "status"), rx(d, env, "next")
            ok_json = True
            for r in (dj, nx):
                try:
                    json.loads(r.stdout)
                except ValueError:
                    ok_json = False
            check("[CP4-X11] doctor --json, next and status still run and print their output, with the problem said",
                  ok_json and "Phase" in st.stdout and all("missing" in r.stderr for r in (dj, nx, st))
                  and all("Traceback" not in r.stderr for r in (dj, nx, st)),
                  [(r.returncode, r.stdout[:80], r.stderr[-150:]) for r in (dj, nx, st)])
            check("[CP4-X11] the reads exit non-zero while the record is missing (what they show is unconfirmed)",
                  all(r.returncode != 0 for r in (doc, dj, nx, st)), [r.returncode for r in (doc, dj, nx, st)])
            v = rx(d, env, "view")
            check("[CP4-X11] view still runs (prints the viewer link) and says what is wrong",
                  "file://" in v.stdout and "missing" in v.stderr and "Traceback" not in v.stderr,
                  (v.returncode, v.stdout[:100], v.stderr[-200:]))
            ih = rx(d, env, "install-hooks", "--host", "claude")
            check("[CP4-X11] install-hooks still repairs the hooks (exit 0) and says what is wrong",
                  ih.returncode == 0 and "hooks written to" in ih.stdout and "missing" in ih.stderr,
                  (ih.returncode, ih.stdout, ih.stderr[-200:]))
    finally:
        os.chmod(rdir, 0o755)
    g = fire_guard(d, env)
    st = rx(d, env, "status")
    check("[CP4-X11] once .ratchet/ is writable, the next guard run saves the record and status runs cleanly",
          g.returncode == 0 and (rdir / "hook-env.json").exists() and st.returncode == 0
          and "not confirmed" not in st.stderr, (g.stderr[-200:], st.returncode, st.stderr[-200:]))


SECTIONS.append(("CP4", cp4_adv_round3_stale_record_and_recovery))


# ---- CP4 adversarial round 4: an override that can't be audited is refused; concurrent hooks lose no update ----

def cp4z_log_unwritable(d):
    """Make .ratchet/human_log.jsonl read-only (the run state stays writable). False when this user/OS can still
    append to it (root), so the setup can't be made."""
    p = Path(d) / ".ratchet" / "human_log.jsonl"
    if not p.exists():
        p.write_text("")
    os.chmod(p, 0o444)
    try:
        with open(p, "a"):
            pass
        os.chmod(p, 0o644)
        return False
    except OSError:
        return True


def cp4z_log_writable(d):
    os.chmod(Path(d) / ".ratchet" / "human_log.jsonl", 0o644)


_CP4Z_SLOW_HOOK = r"""
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location("ratchet_engine_slow_hook", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ready, real = pathlib.Path(sys.argv[2]), m.secrets
class Slow:  # the hook's first random name is made right before it writes its record: pause there
    n = 0
    def token_hex(self, k):
        if Slow.n == 0:
            ready.write_text("x")
            time.sleep(float(sys.argv[4]))
        Slow.n += 1
        return real.token_hex(k)
    def __getattr__(self, a):
        return getattr(real, a)
m.secrets = Slow()
sys.exit(m.run_hook(sys.argv[3], "claude"))
"""


def cp4z_slow_hook(d, env, event, payload, pause=2.0):
    """A real hook process that pauses right before it saves its hook-env record (after its start time is taken):
    the exact interleaving of a hook that started first but finishes last. Returns the process once it is paused
    (or after 15 s if it never reaches the write)."""
    ready = Path(tempfile.mkdtemp(prefix="ratchet-trust-x12-")) / "ready"
    p = subprocess.Popen([sys.executable, "-c", _CP4Z_SLOW_HOOK, str(ENGINE), str(ready), event, str(pause)],
                         cwd=str(d), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    t0 = time.time()
    while not ready.exists() and p.poll() is None and time.time() - t0 < 15:
        time.sleep(0.02)
    return p


def cp4z_bash_payload(d, command="ls", session="s"):
    return {"session_id": session, "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}}


def cp4z_hook_proc(d, env, event, payload):
    p = subprocess.Popen([sys.executable, str(ENGINE), "hook", event, "--host", "claude"], cwd=str(d), env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    return p


def cp4z_view(d):
    try:
        return json.loads((Path(d) / ".ratchet" / "hook-env.json").read_text())
    except (OSError, ValueError):
        return {}


def cp4_adv_round4_audit_and_concurrency():
    print("CP4 adv round 4: an override whose audit line can't be written is refused; concurrent hooks never "
          "restore an older role or drop a field")

    # --- X12: the override is confirmed, but human_log.jsonl can't take its audit line: refused, nothing changes --
    if not HAVE_PTY:
        print("  - [CP4-X12] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        sp = Path(d) / ".ratchet/runs/f/state.json"
        before = sp.read_text()
        if not cp4z_log_unwritable(d):
            print("  - [CP4-X12] not run: this user/OS can write to a read-only file, so the setup can't be made")
        else:
            try:
                rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"),
                                      cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            check("[CP4-X12] human-approve: a confirmed override whose audit line can't be written is refused, "
                  "with a plain message, and approves nothing (state.json unchanged)",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out and "nothing was approved" in out
                  and sp.read_text() == before and "Traceback" not in out, (rc, out[-400:]))
            check("[CP4-X12] human-approve: no override line was logged for the refused approval",
                  not any(ln.get("override") for ln in cp4_human_log_lines(d)), cp4_human_log_lines(d)[-2:])

        env = make_env("claude")
        d = cp4_tamper_fixture(env)
        if not cp4z_log_unwritable(d):
            print("  - [CP4-X12] not run: this user/OS can write to a read-only file, so the setup can't be made")
        else:
            try:
                rc, out = cp4_pty_run(["accept-state"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            r = rx(d, env, "status")
            check("[CP4-X12] accept-state: refused when its audit line can't be written; the hand-edited state is "
                  "still not accepted as a baseline",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out and r.returncode != 0
                  and "changed outside ratchet.py" in r.stderr, (rc, out[-300:], r.returncode, r.stderr[-200:]))

        env = make_env("claude")
        d = project(env)
        active = (Path(d) / ".ratchet" / "active.json").read_text()
        if not cp4z_log_unwritable(d):
            print("  - [CP4-X12] not run: this user/OS can write to a read-only file, so the setup can't be made")
        else:
            try:
                rc, out = cp4_pty_run(["init", "other feature", "--slug", "g", "--force"], d,
                                      dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            check("[CP4-X12] init --force: refused when its audit line can't be written; the active run is not "
                  "replaced",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out
                  and (Path(d) / ".ratchet" / "active.json").read_text() == active
                  and not (Path(d) / ".ratchet/runs/g").exists(), (rc, out[-300:]))

    # --- X13: a director guard that started first but saves last does not restore the director role -------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    g0 = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    denv = dict(env, RATCHET_ROLE="director")
    slow = cp4z_slow_hook(d, denv, "guard", cp4z_bash_payload(d, session="s-director"))
    b = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})  # the builder's guard: started later, finishes first
    slow_out = slow.communicate(timeout=60)
    view = cp4z_view(d)
    m = engine()
    with engine_env(env):
        role = m._recorded_guard_role(Path(d))
    check("[CP4-X13] setup: both guards ran cleanly, the director's one finishing last",
          g0.returncode == 0 and b.returncode == 0 and slow.returncode == 0, (b.stderr[-200:], slow_out))
    check("[CP4-X13] the newest guard (the builder's) wins: hook-env.json and the engine both say builder",
          view.get("guard_role") == "builder" and role == "builder", (view, role))
    res = cp4y_director_cmds(d, denv)  # what the builder's script sets
    check("[CP4-X13] so the builder's RATCHET_ROLE=director gets nothing: director-key/enroll/sign are refused",
          all(r.returncode != 0 for r in res) and not (Path(env["RATCHET_HOME"]) / "director.key").exists(),
          [(r.returncode, r.stderr[-150:]) for r in res])

    # --- X13: an older prompt hook (another folder, so it must save) never drops the newer guard's role ----------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    denv = dict(env, RATCHET_ROLE="director")
    g0 = cp4x_guard_tool(d, denv, "Bash", {"command": "ls"}, session="s-director")
    other = dict(env, RATCHET_HOME=str(Path(tempfile.mkdtemp(prefix="ratchet-trust-x13h-")) / "rh"))
    slow = cp4z_slow_hook(d, other, "prompt", prompt_payload(d))
    b = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    slow_out = slow.communicate(timeout=60)
    view = cp4z_view(d)
    with engine_env(env):
        role = m._recorded_guard_role(Path(d))
        home = m._current_hook_env()["home"]
    check("[CP4-X13] an older prompt hook finishing last keeps the newer guard's builder role and folder",
          g0.returncode == 0 and b.returncode == 0 and slow.returncode == 0 and view.get("guard_role") == "builder"
          and role == "builder" and view.get("home") == home, (view, role, home, slow_out))
    res = cp4y_director_cmds(d, denv)
    check("[CP4-X13] and the builder's script still gets no director role from it",
          all(r.returncode != 0 for r in res), [(r.returncode, r.stderr[-150:]) for r in res])

    # --- X13: many hook processes at once: the newest guard's role always wins and no field is lost --------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    denv = dict(env, RATCHET_ROLE="director")
    homes = {}
    with engine_env(env):
        homes[m._current_hook_env()["home"]] = 1
    with engine_env(other):
        homes[m._current_hook_env()["home"]] = 1

    def burst(guard_env, guards, session):
        procs = [cp4z_hook_proc(d, guard_env, "guard", cp4z_bash_payload(d, session=session)) for _ in range(guards)]
        procs += [cp4z_hook_proc(d, other, "prompt", prompt_payload(d)) for _ in range(6)]
        procs += [cp4z_hook_proc(d, env, "stop", stop_payload(d)) for _ in range(4)]
        return [(p.wait(timeout=120), p.stderr.read()) for p in procs]

    rounds = []
    for guard_env, want, n, session in ((denv, "director", 6, "s-director"), (env, "builder", 1, "s"),
                                        (denv, "director", 1, "s-director"), (env, "builder", 1, "s")):
        outs = burst(guard_env, n, session)
        view = cp4z_view(d)
        with engine_env(env):
            role = m._recorded_guard_role(Path(d))
        rounds.append((want, all(rc == 0 for rc, _e in outs), view, role,
                       [e[-150:] for rc, e in outs if "could not save" in e]))
    check("[CP4-X13] every concurrent hook ran cleanly and none failed to save its record",
          all(r[1] and not r[4] for r in rounds), [(r[0], r[1], r[4][:2]) for r in rounds])
    check("[CP4-X13] after each burst the role is the newest guard's (never an older one restored)",
          all(r[2].get("guard_role") == r[0] and r[3] == r[0] for r in rounds),
          [(r[0], r[2].get("guard_role"), r[3]) for r in rounds])
    check("[CP4-X13] no field is lost: hook-env.json always holds home, key_file and guard_role, home one of the "
          "folders the hooks really use",
          all(r[2].get("home") in homes and r[2].get("key_file") and r[2].get("guard_role") for r in rounds),
          [r[2] for r in rounds])
    log = errors_path(d).read_text() if errors_path(d).exists() else ""
    folder = Path(d) / ".ratchet" / "hook-env"
    left = sorted(os.listdir(folder)) if folder.is_dir() else []
    check("[CP4-X13] no hook logged a HookRecordError, and superseded records and temp files are cleaned up",
          "HookRecordError" not in log and not any(n.endswith(".tmp") for n in left) and len(left) <= 12
          and not any(n.endswith(".tmp") for n in os.listdir(Path(d) / ".ratchet")), (log[-300:], left))


SECTIONS.append(("CP4", cp4_adv_round4_audit_and_concurrency))


# ---- CP4 adversarial round 5: the audit line is part of the override itself; same-microsecond guard ties --------

def cp4w_choice_pending(env):
    """A building run with a `choice` question open and the user's reply "B" logged by the prompt hook, but with no
    transcript to check it against: without the override, `choice` refuses it; with it, `choice` records B."""
    d = project(env)
    r = rx(d, env, "ask", "choice", "--options", "A,B,C")
    assert r.returncode == 0, r.stderr + r.stdout
    time.sleep(0.01)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "B", "session_id": "s-owner"}))
    return d


def cp4w_snapshot(d, env):
    """Every file Ratchet keeps for this project (.ratchet/, except the human log the test makes read-only) and
    in the per-user folder (integrity records), as bytes: a refused command must leave all of them as they were."""
    out = {}
    for base in (Path(d) / ".ratchet", Path(env["RATCHET_HOME"])):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.name != "human_log.jsonl":
                try:
                    out[str(p)] = p.read_bytes()
                except OSError:
                    out[str(p)] = None
    return out


# Every command that accepts RATCHET_HUMAN_OVERRIDE, by the human_override call site it reaches in ratchet.py:
# _require_approval (human-approve, approve-plan), cmd_accept_state, cmd_init --force and cmd_choice.
CP4W_OVERRIDE_CMDS = [
    ("human-approve", lambda env: cp4_awaiting_review(env), ["human-approve"], "human-approve"),
    ("approve-plan", lambda env: cp4_unapproved_plan(env)[0], ["approve-plan"], "approve-plan"),
    ("accept-state", lambda env: cp4_tamper_fixture(env), ["accept-state"], "accept-state"),
    ("init --force", lambda env: project(env), ["init", "other feature", "--slug", "g", "--force"], "init --force"),
    ("choice", lambda env: cp4w_choice_pending(env), ["choice"], "choice"),
]


def cp4_adv_round5_override_audit_class():
    print("CP4 adv round 5: every command that takes the override writes its audit line, or is refused with nothing "
          "changed; on a same-microsecond tie the builder's guard record wins")
    import ast
    m = engine()

    # --- X14 (structural, code): every human_override call names what to audit; log_override has one caller ------
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    calls, log_callers = [], []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "human_override":
                    kw = {k.arg for k in node.keywords}
                    calls.append((fn.name, len(node.args) >= 3 or {"ctx", "action"} <= kw
                                  or (len(node.args) == 2 and "action" in kw)))
                elif node.func.id == "log_override":
                    log_callers.append(fn.name)
    sites = {f for f, _ok in calls}
    check("[CP4-X14] every human_override call site in ratchet.py passes the ctx and the action to audit",
          calls and all(ok for _f, ok in calls), calls)
    check("[CP4-X14] the call sites are the ones this test drives (approvals, accept-state, init, choice)",
          sites == {"_require_approval", "cmd_accept_state", "cmd_init", "cmd_choice"}, sites)
    check("[CP4-X14] the audit is part of the override: log_override is only called from human_override",
          log_callers == ["human_override"], log_callers)

    # --- X14 (in process): a confirmed override with no audit target, or an unwritable log, never returns True ----
    saved = (m._OVERRIDE_CONFIRMED, os.environ.get("RATCHET_HUMAN_OVERRIDE"))
    env = make_env("claude")
    d = project(env)
    results, unwritable = [], False
    try:
        m._OVERRIDE_CONFIRMED = True
        os.environ["RATCHET_HUMAN_OVERRIDE"] = "1"
        with engine_env(env):
            ctx = m.Ctx(Path(d))
            for args in ((), (ctx,), (ctx, "")):
                try:
                    results.append(("returned", m.human_override("test an unaudited use", *args)))
                except SystemExit:
                    results.append(("refused",))
            unwritable = cp4z_log_unwritable(d)
            try:
                if unwritable:
                    try:
                        results.append(("returned", m.human_override("test", ctx, "x14-unwritable")))
                    except SystemExit:
                        results.append(("refused",))
            finally:
                cp4z_log_writable(d)
            results.append(("returned", m.human_override("test", ctx, "x14-ok")))
    finally:
        m._OVERRIDE_CONFIRMED = saved[0]
        if saved[1] is None:
            os.environ.pop("RATCHET_HUMAN_OVERRIDE", None)
        else:
            os.environ["RATCHET_HUMAN_OVERRIDE"] = saved[1]
    acts = [ln.get("action") for ln in cp4_human_log_lines(d) if ln.get("override")]
    check("[CP4-X14] a confirmed override with no ctx or no action is refused, never used unaudited",
          results[:3] == [("refused",)] * 3, results)
    check("[CP4-X14] a confirmed override whose log can't be written is refused (in process)",
          not unwritable or results[3] == ("refused",), results)
    check("[CP4-X14] a confirmed, audited override returns True after writing exactly its own audit line",
          results[-1] == ("returned", True) and acts == ["x14-ok"], (results, acts))

    if not HAVE_PTY:
        print("  - [CP4-X14] command tests not run: no pty on this platform (Windows)")
    else:
        # --- X14: each command, used with a confirmed override, writes its audit line and succeeds ---------------
        for name, make, args, action in CP4W_OVERRIDE_CMDS:
            env = make_env("claude")
            d = make(env)
            n0 = len([ln for ln in cp4_human_log_lines(d) if ln.get("override")])
            rc, out = cp4_pty_run(args, d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            new = [ln for ln in cp4_human_log_lines(d) if ln.get("override")][n0:]
            check(f"[CP4-X14] {name}: a confirmed override succeeds and leaves exactly one audit line naming it",
                  rc == 0 and len(new) == 1 and str(new[0].get("action", "")).startswith(action)
                  and new[0].get("by") == "human", (rc, out[-300:], new))
            if name == "choice":
                st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
                check("[CP4-X14] choice: the override really skipped the transcript check and recorded option B",
                      "The user picked option B" in out and not st.get("pending"), (out[-300:], st.get("pending")))

        # --- X14: the same commands with an unwritable log are refused and change nothing --------------------------
        for name, make, args, action in CP4W_OVERRIDE_CMDS:
            env = make_env("claude")
            d = make(env)
            before = cp4w_snapshot(d, env)
            hl = Path(d) / ".ratchet" / "human_log.jsonl"
            log_before = hl.read_bytes() if hl.exists() else b""
            if not cp4z_log_unwritable(d):
                print(f"  - [CP4-X14] {name}: not run: this user/OS can write to a read-only file")
                continue
            try:
                rc, out = cp4_pty_run(args, d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            after = cp4w_snapshot(d, env)
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            check(f"[CP4-X14] {name}: refused with a plain message when its audit line can't be written, and "
                  "nothing Ratchet keeps changed",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out and "Traceback" not in out
                  and not changed and hl.read_bytes() == log_before, (rc, out[-300:], changed))

        env = make_env("claude")
        d = cp4w_choice_pending(env)
        r = rx(d, env, "choice")
        check("[CP4-X14] choice without the override still refuses a reply it can't match to the transcript",
              r.returncode != 0 and "Couldn't confirm" in (r.stderr + r.stdout), (r.returncode, r.stderr[-200:]))

    # --- D2: two guard records with the same start microsecond: the builder's wins, whatever the names say -------
    folder_root = Path(tempfile.mkdtemp(prefix="ratchet-trust-d2-"))
    folder = folder_root / ".ratchet" / "hook-env"
    folder.mkdir(parents=True)
    rec = json.dumps({"home": "/h", "key_file": "/k", "at": "x", "host": "claude"})
    outcomes = []
    for dtag, btag in (("zzzz", "0000"), ("0000", "zzzz")):
        for n in os.listdir(folder):
            os.unlink(folder / n)
        (folder / f"hook-env.json.guard.1000.director.1-{dtag}").write_text(rec)
        (folder / f"hook-env.json.guard.1000.builder.1-{btag}").write_text(rec)
        (folder / "hook-env.json.guard.999.director.1-aaaa").write_text(rec)
        (folder_root / ".ratchet" / "hook-env.json").write_text(json.dumps({"home": "/h", "key_file": "/k",
                                                                            "guard_role": "director"}))
        recs = m._hook_env_records(folder_root)
        outcomes.append((recs[-1][2], m._combined_hook_env(recs).get("guard_role"),
                         m._recorded_guard_role(folder_root)))
    check("[CP4-D2] same-microsecond director and builder guard records: the builder's is the newest, whichever "
          "random token each name holds",
          outcomes == [("builder", "builder", "builder")] * 2, outcomes)

    env = make_env("claude")
    roles = []
    for first, second in (("director", "builder"), ("builder", "director")):
        d = Path(tempfile.mkdtemp(prefix="ratchet-trust-d2p-"))
        (d / ".ratchet").mkdir()
        at = now_dt().isoformat(timespec="microseconds")
        saved_role = os.environ.get("RATCHET_ROLE")
        ok_all = True
        try:
            with engine_env(env):
                for role in (first, second):
                    if role == "director":
                        os.environ["RATCHET_ROLE"] = "director"
                    else:
                        os.environ.pop("RATCHET_ROLE", None)
                    ok_all = m.record_hook_env(d, "claude", "guard", started=at) and ok_all
                view = json.loads((d / ".ratchet" / "hook-env.json").read_text())
                roles.append((ok_all, view.get("guard_role"), m._recorded_guard_role(d)))
        finally:
            if saved_role is None:
                os.environ.pop("RATCHET_ROLE", None)
            else:
                os.environ["RATCHET_ROLE"] = saved_role
    check("[CP4-D2] record_hook_env: a director and a builder guard with the same start time leave the builder "
          "role, in either finishing order",
          roles == [(True, "builder", "builder")] * 2, roles)


SECTIONS.append(("CP4", cp4_adv_round5_override_audit_class))


def main():
    global FAIL
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
