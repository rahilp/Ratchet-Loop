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


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


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
        (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
        (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
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
    bar = Path(_tmp.mkdtemp(prefix="ratchet-trust-bar-"))
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
    bar = Path(_tmp.mkdtemp(prefix="ratchet-trust-bar5-"))
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
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
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


def cp3_adv_folder_trust():
    print("CP3: adversarial reviews only count from their own round's folder")

    # --- T01: a review written outside the round's own folder is refused ---------------------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-outside-"))
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

    # --- T03: a script-signed review written straight into the round's own folder still passes (regression) ------
    # (CP7, user decision: reviews must be signed, so this regression now uses external_review.py against a fake CLI)
    import test_signing as ts
    env = ts.with_fake_provider(make_env("claude"), json.dumps({"findings": []}))
    d = cp3_project(env)
    ts.use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = Path(rnd["output_dir"]) / f"{rid}.json"
    rp = ts.run_external(d, env, rid, good)
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(good))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T03] a script-signed review written straight into the round's own folder still passes (regression)",
          rp.returncode == 0 and r.returncode == 0 and cp1["gates"]["adversarial"].get("status") == "passed",
          (rp.returncode, rp.stderr, r.returncode, r.stderr, cp1["gates"]["adversarial"]))

    # --- T04: a symlink inside the round's folder pointing outside it is refused ----------------------------------
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out_dir = Path(rnd["output_dir"])
    real = Path(_tmp.mkdtemp(prefix="ratchet-trust-sym-")) / "real.json"
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
    real_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-dotdot-"))
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
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-outside-"))
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
    # CP7: round 1's combined review must be script-signed to be accepted at all (a hand-typed one would be
    # refused as unsigned, never reaching the "fails and freezes" behavior this test is about).
    diffs = [{"severity": "minor", "location": "x", "description": "issue", "fixable_in_code": True}]
    combined1 = str(cp3_signed_combined(d, env, fp1, diffs=diffs))
    r1 = rx(d, env, "ui-record", "CP1", "--review", combined1)
    check("[CP3-T07] setup: round 1 fails and freezes the capture spec", r1.returncode == 2, (r1.returncode, r1.stderr))
    (d / "src/f.js").write_text("export const f = () => 1; // tweak\n")
    fp2 = cp3_fp(d, env)
    check("[CP3-T07] setup: the code change produced a new fingerprint", fp2 != fp1, (fp1, fp2))
    body = json.loads(Path(combined1).read_text())
    body["fingerprint"], body["differences"] = fp2, []  # would otherwise PASS round 2 outright
    Path(combined1).write_text(json.dumps(body))  # still physically in round-01/, and no longer validly signed
    r2 = rx(d, env, "ui-record", "CP1", "--review", combined1)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T07] refused for not being inside round 2's own folder, even though the fingerprint now matches",
          r2.returncode != 0 and "reviews/CP1/round-02" in (r2.stderr + r2.stdout)
          and cp1["gates"]["ui"].get("status") != "passed", (r2.returncode, r2.stderr, cp1["gates"]["ui"]))

    # --- T08: a script-signed combined review written into the round's own folder still passes (regression) -------
    # (CP7, user decision: reviews must be signed, so this regression now uses ui_review.py's combine step, with
    # per-role files given real provenance the same way cp3_signed_combined builds them for every other test)
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    good = cp3_signed_combined(d, env, fp)
    r = rx(d, env, "ui-record", "CP1", "--review", str(good))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP3-T08] a script-signed combined review written into the round's own folder still passes (regression)",
          r.returncode == 0 and cp1["gates"]["ui"].get("status") == "passed", (r.returncode, r.stderr, cp1["gates"]["ui"]))

    # --- T09: a symlink inside the UI round's folder pointing outside it is refused --------------------------------
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    real = Path(_tmp.mkdtemp(prefix="ratchet-trust-uisym-")) / "real.json"
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
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-x01-"))
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
    outside = Path(_tmp.mkdtemp(prefix="ratchet-trust-x02-"))
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
    # CP7: `good` must be accepted outright (rc == 0, gate passed) for this to prove anything, so it is confirmed as
    # a host sub-agent's write (the same mechanism test_signing.py's record_host_review drives end to end) before
    # being written; the forged rewrite below stays hand-typed, since it must never be read back anyway.
    import test_signing as ts
    m = engine()
    env, d, rnd, rid = cp3x_adv_ready()
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    gr = ts.guard(d, env, "Write", {"file_path": str(path)}, agent="ratchet-adversary")
    assert gr.returncode == 0, (gr.returncode, gr.stdout, gr.stderr)
    good = Path(cp3_write_review(path, rnd, rid))
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
    # CP7: `good` must be accepted outright (rc == 0, gate passed), so it is signed the way ui_review.py's combine
    # step signs it; the forged rewrite below stays hand-typed, since it must never be read back anyway.
    m = engine()
    env, d, fp = cp3x_ui_ready()
    good = cp3_signed_combined(d, env, fp)
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
        evil = Path(cp3_write_review(Path(_tmp.mkdtemp(prefix="ratchet-trust-x04-")) / "evil.json", rnd, rid))

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
    evil = Path(cp3_write_combined(Path(_tmp.mkdtemp(prefix="ratchet-trust-x04ui-")) / "evil.json", fp))

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
    good = cp3_signed_combined(d, env, fp)  # CP7: must be accepted outright to exercise the later fstat/write step
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
    good = cp3_signed_combined(d, env, fp)  # CP7: must be accepted outright to exercise the later fstat/write step
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
    good = cp3_signed_combined(d, env, fp)  # CP7: must be accepted outright to exercise the later fstat/write step
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
    good = cp3_reviewer_writes(d, env, rnd, rid)
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
    fake_home = Path(_tmp.mkdtemp(prefix="ratchet-trust-fakehome-"))
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
    rx(d, env, "ask", "approve-plan")
    r2 = cp4_say_yes(d, tp, env, text="yes, go ahead")
    assert r2.returncode == 0, r2.stderr + r2.stdout
    r3 = rx(d, env, "approve-plan")
    phase_after_chat = cp3_state(d)["phase"]
    check("[CP4-T09] the lane was not enrolled by the attempt: a normal chat yes from the real user still approves it",
          r3.returncode == 0 and phase_after_chat == "building", (r3.returncode, r3.stderr, phase_after_chat))

    # --- T10: a RATCHET_HOME that disagrees with what the hooks recorded is refused ---------------------------------
    env = make_env("claude")
    d = project(env)  # install + fire_claude already ran the hooks against this project with env's RATCHET_HOME (H1)
    h2 = Path(_tmp.mkdtemp(prefix="ratchet-trust-h2-"))
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
    out = Path(_tmp.mkdtemp(prefix="ratchet-trust-x01-"))
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
    elsewhere = Path(_tmp.mkdtemp(prefix="ratchet-trust-x02-"))
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
    rx(d, env, "ask", "approve-plan")
    r2 = cp4_say_yes(d, tp, env, text="yes, go ahead")
    r3 = rx(d, env, "approve-plan")
    check("[CP4-X02] the lane is still not enrolled: the real user's chat yes approves it as before",
          r2.returncode == 0 and r3.returncode == 0 and cp3_state(d)["phase"] == "building",
          (r3.returncode, r3.stderr))

    # --- X03: a real director session in its own folder (no .ratchet/), with hooks, still drives a lane via --root ---
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    b = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})  # the lane's builder has run commands in the lane
    home_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-director-"))
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
    dproj = Path(_tmp.mkdtemp(prefix="ratchet-trust-dproj-"))
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
    fake_home = Path(_tmp.mkdtemp(prefix="ratchet-trust-x07-home-"))
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
    elsewhere = Path(_tmp.mkdtemp(prefix="ratchet-trust-x08-"))
    res = cp4y_director_cmds(elsewhere, denv, root=d)
    check("[CP4-X08] from a folder no director hook recorded, `--root <lane>` director commands are refused too",
          all(r.returncode != 0 for r in res) and not (Path(d) / ".ratchet" / "director.json").exists(),
          [(r.returncode, r.stderr[-150:]) for r in res])
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude", scope="user")  # installed for the user, in no project
    elsewhere = Path(_tmp.mkdtemp(prefix="ratchet-trust-x08u-"))
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
    dproj = Path(_tmp.mkdtemp(prefix="ratchet-trust-x10b-"))
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
    ready = Path(_tmp.mkdtemp(prefix="ratchet-trust-x12-")) / "ready"
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
    other = dict(env, RATCHET_HOME=str(Path(_tmp.mkdtemp(prefix="ratchet-trust-x13h-")) / "rh"))
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
    folder_root = Path(_tmp.mkdtemp(prefix="ratchet-trust-d2-"))
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
        d = Path(_tmp.mkdtemp(prefix="ratchet-trust-d2p-"))
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


# ---- CP5 ----
# Approvals answer the question Ratchet asked: a plan or step approval only counts when the user's FIRST reply
# after Ratchet printed that exact question is one of the listed yes replies (DECIDED: yes / yes, go ahead /
# go ahead / approved - case and trailing punctuation ignored, nothing else attached). CP4 landed: the human
# override now needs a real terminal and writes an audit line, so nothing here relies on RATCHET_HUMAN_OVERRIDE=1
# without a pty; director-signed approvals (which need no chat reply at all) are used instead where a fixture
# needs an approval without driving the new ask-first flow.

CP5_YES_REPLIES = ["yes", "yes, go ahead", "go ahead", "approved"]


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
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
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


def cp5_ask_prints_listed_yes_replies():
    print("CP5: asking for plan approval prints the question and the exact replies that count as yes")

    # --- T01: `ask approve-plan` prints the question and every listed yes reply, and approves nothing yet -----------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    r = rx(d, env, "ask", "approve-plan")
    out = r.stdout
    present = [p for p in CP5_YES_REPLIES if re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", out, re.I)]
    check("[CP5-T01] `ask approve-plan` succeeds and prints an ASK THE USER line naming the plan approval",
          r.returncode == 0 and out.strip().startswith("ASK THE USER") and re.search(r"plan", out, re.I) is not None,
          (r.returncode, out, r.stderr))
    check("[CP5-T01] the printed question lists every one of the exact replies that count as yes",
          present == CP5_YES_REPLIES, (present, out))
    check("[CP5-T01] asking the question alone approves nothing: the plan is still awaiting approval",
          cp3_state(d)["phase"] == "awaiting_plan_approval", cp3_state(d)["phase"])


SECTIONS.append(("CP5", cp5_ask_prints_listed_yes_replies))


def cp5_listed_yes_after_question_approves():
    print("CP5: a listed yes sent after the question approves the plan")

    # --- T02: ask approve-plan, then a listed yes, then approve-plan succeeds and the run leaves awaiting_plan_approval
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")  # the question this case assumes was just printed
    cp5_say(d, tp, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    check("[CP5-T02] approve-plan exits 0 once a listed yes follows the printed question",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))
    check("[CP5-T02] the run leaves awaiting_plan_approval and moves to building",
          phase == "building", phase)


SECTIONS.append(("CP5", cp5_listed_yes_after_question_approves))


def cp5_yes_before_question_does_not_approve():
    print("CP5: a yes typed before the question was printed does not approve the plan")

    # --- T03: the user already said yes in chat; the agent then asks afterward; no further reply follows ------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    cp5_say(d, tp, env, "yes, build it")
    rx(d, env, "ask", "approve-plan")  # printed only after the user's earlier yes
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    check("[CP5-T03] approve-plan is refused: the user's yes predates the printed question",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    check("[CP5-T03] the plan is still awaiting approval", phase == "awaiting_plan_approval", phase)


SECTIONS.append(("CP5", cp5_yes_before_question_does_not_approve))


def cp5_yes_to_unrelated_chat_question_does_not_approve():
    print("CP5: a yes to an unrelated question the agent asked does not approve the plan")

    # --- T04: ask approve-plan, then ordinary chat text answering a DIFFERENT, unregistered question -----------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "sure thing")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    msg = r.stderr + r.stdout
    check("[CP5-T04] approve-plan is refused: the first reply after the question was not a clear, listed yes",
          r.returncode != 0 and re.search(r"not (a )?(clear|listed)( yes)?|not one of", msg, re.I) is not None,
          (r.returncode, msg))
    check("[CP5-T04] the refusal tells the agent to print the question again",
          re.search(r"ask (them )?again|print the question again|ask .*again", msg, re.I) is not None, msg)
    check("[CP5-T04] the plan stays unapproved", phase == "awaiting_plan_approval", phase)


SECTIONS.append(("CP5", cp5_yes_to_unrelated_chat_question_does_not_approve))


def cp5_yes_to_different_pending_question_does_not_approve():
    print("CP5: a yes to a different pending Ratchet question does not approve the plan")

    # --- T05: ask approve-plan, then ask set-review (a different registered question) before any reply --------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    r2 = rx(d, env, "ask", "set-review", "each")
    assert r2.returncode == 0, r2.stderr + r2.stdout
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    msg = r.stderr + r.stdout
    check("[CP5-T05] approve-plan is refused and names the question that is actually pending (set-review)",
          r.returncode != 0 and "set-review" in msg, (r.returncode, msg))
    check("[CP5-T05] the plan's approval state did not change", phase == "awaiting_plan_approval", phase)


SECTIONS.append(("CP5", cp5_yes_to_different_pending_question_does_not_approve))


def cp5_non_yes_first_reply_spends_question():
    print("CP5: a non-yes first reply spends the question; a later yes does not retroactively approve")

    # --- T06: ask approve-plan, "ok" (not listed) first, then a listed yes: the FIRST reply is the one that counts --
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "ok")
    cp5_say(d, tp, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    check("[CP5-T06] approve-plan is refused: the FIRST reply after the question ('ok') was not a listed yes",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    check("[CP5-T06] the plan is still unapproved despite the later, stray listed yes",
          phase == "awaiting_plan_approval", phase)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "approved")
    r3 = rx(d, env, "approve-plan")
    check("[CP5-T06] once the question is printed again and the next FIRST reply is a listed yes, it succeeds",
          r3.returncode == 0 and cp3_state(d)["phase"] == "building",
          (r3.returncode, r3.stderr, cp3_state(d)["phase"]))


SECTIONS.append(("CP5", cp5_non_yes_first_reply_spends_question))


def cp5_human_approve_requires_question_too():
    print("CP5: step review (human-approve) also requires the printed question first")

    # --- T07: human-approve without an `ask human-approve` first is refused; after ask + a listed yes it succeeds ---
    env = make_env("claude")
    d, tp = cp5_awaiting_checkpoint_review(env)
    phase_before = cp3_state(d)["phase"]
    cp5_say(d, tp, env, "yes, go ahead")  # a listed yes is already in the log, but the question was never asked
    r = rx(d, env, "human-approve")
    check("[CP5-T07] human-approve without `ask human-approve` first is refused, nothing approved",
          r.returncode != 0 and cp3_state(d)["phase"] == phase_before, (r.returncode, r.stdout, r.stderr))
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "yes, go ahead")
    r3 = rx(d, env, "human-approve")
    check("[CP5-T07] after `ask human-approve` and a listed yes, human-approve succeeds",
          r3.returncode == 0, (r3.returncode, r3.stderr, r3.stdout))
    gate = cp3_state(d)["checkpoints"]["CP1"]["gates"]["human"]
    check("[CP5-T07] the checkpoint's human gate is marked passed",
          gate.get("status") == "passed", gate)


SECTIONS.append(("CP5", cp5_human_approve_requires_question_too))


def cp5_pre_upgrade_run_can_continue():
    print("CP5: a pre-upgrade run already waiting for approval can carry on once the question is printed once more")

    # --- T08: a run submitted before this change (awaiting_plan_approval, awaiting_since set, no `pending` entry) ----
    env = make_env("claude")
    d, tp = cp5_plan(env)
    st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    check("[CP5-T08] precondition: a freshly submitted plan has no `pending` entry yet (models a pre-upgrade run)",
          "pending" not in st and st["phase"] == "awaiting_plan_approval" and bool(st.get("awaiting_since")), st)
    cp5_say(d, tp, env, "yes")  # a human message already sitting in the log, from before the question was ever printed
    r = rx(d, env, "approve-plan")
    check("[CP5-T08] the stale pre-upgrade message alone does not approve the plan",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stdout, r.stderr))
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "approved")
    r3 = rx(d, env, "approve-plan")
    check("[CP5-T08] once the question is printed fresh and a listed yes follows, approve-plan succeeds",
          r3.returncode == 0 and cp3_state(d)["phase"] == "building",
          (r3.returncode, r3.stderr, cp3_state(d)["phase"]))


SECTIONS.append(("CP5", cp5_pre_upgrade_run_can_continue))


def cp5_director_signed_approval_skips_chat_question():
    print("CP5: a director-signed approval still works without any chat question being printed")

    # --- T09: a director token approves the plan with no `ask` step and no chat reply at all -------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    denv = dict(env, RATCHET_ROLE="director")
    rk = rx(d, denv, "director-key")
    en = rx(d, denv, "director-enroll")
    sg = rx(d, denv, "director-sign", "approve-plan")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    assert rk.returncode == 0 and en.returncode == 0 and sg.returncode == 0 and tok, (rk.stderr, en.stderr, sg.stderr)
    st_before = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    check("[CP5-T09] precondition: no question was ever asked and no chat reply was ever recorded for this plan",
          "pending" not in st_before, st_before)
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP5-T09] the director-signed token approves the plan with no ask step and no chat reply required",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr, cp3_state(d)["phase"]))


SECTIONS.append(("CP5", cp5_director_signed_approval_skips_chat_question))


def cp5_feedback_still_accepts_freeform_text():
    print("CP5: feedback at a review point still accepts freeform text, not just a listed yes")

    # --- T10: ordinary feedback text, not one of the approval phrases, is still recorded as feedback -----------------
    env = make_env("claude")
    d = cp4_awaiting_review(env)
    assert cp3_state(d)["phase"] == "awaiting_final_review", cp3_state(d)["phase"]
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "actually, can the button be blue?")
    r = rx(d, env, "feedback", "--text", "blue button")
    check("[CP5-T10] feedback is recorded from ordinary text; CP5's yes/no allow-list does not block it",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))


SECTIONS.append(("CP5", cp5_feedback_still_accepts_freeform_text))


def cp5_regression_existing_approval_suites_still_pass():
    print("CP5: existing approval flows in test_gates.py and test_hooks_waiver.py still pass once they ask first")

    # --- T11: the full test_gates.py and test_hooks_waiver.py suites, run as real subprocesses, both pass in full ----
    here = Path(__file__).resolve().parent
    for name in ("test_gates.py", "test_hooks_waiver.py"):
        r = subprocess.run([sys.executable, str(here / name)], capture_output=True, text=True, timeout=600)
        tail = (r.stdout[-1500:] + r.stderr[-1500:])
        check(f"[CP5-T11] {name} passes in full (regression guard: CP5 must not break CP1-CP4, hooks-waiver or "
              "gate-integrity behavior)", r.returncode == 0, tail)


SECTIONS.append(("CP5", cp5_regression_existing_approval_suites_still_pass))


# ---- CP5 adversarial round 1 ----
# Fixes for CP5's first adversarial round: one question at a time (a new `ask` never lets one reply answer two
# questions), replies only count from the question's own chat, feedback is bound to the reply that answered the
# review question, the deliberate narrowing of cancel / set-review / approve-changes / claim / ui-respec to the
# listed yes replies, the phase rule, the prompt hook's guidance, and the docs that describe all of it.

def cp5x_history(d, event=None):
    h = cp3_state(d).get("history", [])
    return [e for e in h if event is None or e.get("event") == event]


def cp5x_hint(d, tp, env, text, session="s-owner"):
    """Say `text` in `session` and return what the prompt hook told the agent."""
    r = cp5_say(d, tp, env, text, session=session)
    return r.stdout + r.stderr


def cp5x_gate_blocks_run(d, env, why="a gate needs a human decision"):
    """Block the run the way a failing gate does (the engine's own _set_blocked + save_state), without a human."""
    code = ("import importlib.util, sys\nfrom pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('r', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\nctx = m.Ctx(Path(sys.argv[2])); st = ctx.state()\n"
            "m._set_blocked(st, sys.argv[3]); ctx.save_state(st)\n")
    r = subprocess.run([sys.executable, "-c", code, str(ENGINE), str(d), why], cwd=d, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout


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


def cp5_adv_round1_one_question_at_a_time():
    print("CP5 adv round 1: a new question never lets one reply answer two questions")

    # --- X01: plan approval can't be asked over a different question still waiting for its first reply ----------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "set-review", "each")
    r = rx(d, env, "ask", "approve-plan")
    pend = cp3_state(d).get("pending") or {}
    check("[CP5-X01] `ask approve-plan` is refused while the set-review question waits, and says to answer it or "
          "`ask withdraw` first", r.returncode != 0 and "different question" in r.stderr and "ask withdraw" in r.stderr
          and "ASK THE USER" not in r.stdout, (r.returncode, r.stdout, r.stderr))
    check("[CP5-X01] the waiting set-review question is left as it was", pend.get("action") == "set-review", pend)
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    check("[CP5-X01] the user's yes (to the set-review question) does not approve the plan",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X02: any other question asked over a waiting one is NOT ASKED, logged, and no reply approves it ---------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    r = rx(d, env, "ask", "cancel")
    over = cp5x_history(d, "question_overtaken")
    check("[CP5-X02] `ask cancel` over the waiting plan question prints NOT ASKED, not a question for the user",
          r.returncode == 0 and r.stdout.strip().startswith("NOT ASKED") and "ASK THE USER" not in r.stdout, r.stdout)
    check("[CP5-X02] the replaced plan question is logged as question_overtaken (audit trail)",
          bool(over) and over[-1].get("action") == "approve-plan" and over[-1].get("by") == "cancel", over)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X02] the prompt hook says this reply approves nothing", "approves nothing" in hint, hint)
    r = rx(d, env, "cancel")
    check("[CP5-X02] the user's yes does not cancel the run (it may have been meant for the plan)",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))
    r = rx(d, env, "approve-plan")
    check("[CP5-X02] ... and it does not approve the plan either",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X03: a question that already had its first reply can be followed by a new one; withdraw is logged -------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "set-review", "each")
    cp5_say(d, tp, env, "no thanks, keep it as it is")
    r = rx(d, env, "ask", "approve-plan")
    over = cp5x_history(d, "question_overtaken")
    check("[CP5-X03] once the waiting question had its reply, `ask approve-plan` asks normally",
          r.returncode == 0 and "ASK THE USER" in r.stdout, (r.returncode, r.stdout, r.stderr))
    check("[CP5-X03] the replaced, already-answered question is still logged",
          bool(over) and over[-1].get("action") == "set-review" and over[-1].get("answered") is True, over)
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    check("[CP5-X03] a listed yes after it approves the plan", r.returncode == 0, (r.returncode, r.stderr))
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "set-review", "each")
    r = rx(d, env, "ask", "withdraw")
    wd = cp5x_history(d, "question_withdrawn")
    check("[CP5-X03] `ask withdraw` is logged as question_withdrawn",
          r.returncode == 0 and bool(wd) and wd[-1].get("action") == "set-review", (r.stdout, wd))


SECTIONS.append(("CP5", cp5_adv_round1_one_question_at_a_time))


def cp5_adv_round1_replies_from_this_chat_only():
    print("CP5 adv round 1: only a reply in the question's own chat answers it")

    # --- X04: a yes in another chat neither answers nor spends the plan question, nor changes the owner ----------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes", session="s-other")
    cp5_say(d, tp, env, "wait, change CP1 first")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X04] another chat's yes doesn't count: the owner chat's first reply (not a yes) decides",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and "wait, change CP1" in r.stderr,
          (r.returncode, r.stderr))
    check("[CP5-X04] the run still belongs to the chat it was started in", st.get("owner_session") == "s-owner", st)

    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "approve-plan")
    check("[CP5-X04] a yes typed only in another chat approves nothing",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval" and "another chat" in r.stderr,
          (r.returncode, r.stderr))
    check("[CP5-X04] ... and does not spend the question",
          (cp3_state(d).get("pending") or {}).get("action") == "approve-plan", cp3_state(d).get("pending"))
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X04] the owner chat's own yes then approves, and the owner is unchanged",
          r.returncode == 0 and st["phase"] == "building" and st.get("owner_session") == "s-owner",
          (r.returncode, r.stderr, st.get("owner_session")))

    # --- X05: `resume --claim` is answered only in the chat that asked to take the run over ----------------------
    env = make_env("claude")
    d = project(env)
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    rx(d, env, "ask", "claim")
    cp5_say(d, tp, env, "yes")  # typed in the owner chat, answering something else there
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X05] a yes in the owner chat does not answer the claim question asked in the other chat",
          r.returncode != 0 and cp3_state(d).get("owner_session") == "s-owner", (r.returncode, r.stderr))
    cp5_say(d, tp, env, "sounds good", session="s-other")
    cp5_say(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X05] claim: 'sounds good' (formerly accepted) as the first reply is refused, and the later yes "
          "does not count", r.returncode != 0 and "sounds good" in r.stderr
          and cp3_state(d).get("owner_session") == "s-owner", (r.returncode, r.stderr))
    cp5_say(d, tp, env, "please continue it here", session="s-other")
    rx(d, env, "ask", "claim")
    hint = cp5x_hint(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X05] the claim chat's prompt hook treats its reply as the answer to the claim question",
          "listed yes" in hint, hint)
    check("[CP5-X05] a listed yes in the claiming chat moves the run there (resume --claim is the only way)",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-other", (r.returncode, r.stderr))


SECTIONS.append(("CP5", cp5_adv_round1_replies_from_this_chat_only))


def cp5_adv_round1_first_listed_reply_only():
    print("CP5 adv round 1: cancel, set-review, approve-changes and ui-respec take only a listed yes, first reply only")
    # Deliberate narrowing (CP5 DECIDED): these used to accept any approving phrase ("sounds good"); now only the
    # first reply after the question counts, and only a listed yes. Each case: "sounds good" first, then "yes".

    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "cancel")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "cancel")
    check("[CP5-X06] cancel: 'sounds good' first is refused and the later yes does not count",
          r.returncode != 0 and "sounds good" in r.stderr and cp3_state(d)["phase"] == "awaiting_plan_approval",
          (r.returncode, r.stderr))

    rx(d, env, "ask", "set-review", "each")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "set-review", "each")
    check("[CP5-X06] set-review: 'sounds good' first is refused and the later yes does not count",
          r.returncode != 0 and "sounds good" in r.stderr and cp3_state(d).get("human_review") != "each",
          (r.returncode, r.stderr, cp3_state(d).get("human_review")))

    env = make_env("claude")
    d = project(env)
    tp = d / "t.jsonl"
    cpj = d / ".ratchet/runs/f/checkpoints.json"
    plan = json.loads(cpj.read_text())
    plan["checkpoints"][0]["summary"] = "changed after approval"
    cpj.write_text(json.dumps(plan))
    snap = cp3_state(d).get("approved")
    q = rx(d, env, "ask", "approve-changes")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-changes")
    check("[CP5-X06] approve-changes: 'sounds good' first is refused and the later yes does not count",
          "ASK THE USER" in q.stdout and r.returncode != 0 and "sounds good" in r.stderr
          and cp3_state(d).get("approved") == snap, (q.stdout, r.returncode, r.stderr))

    env = make_env("claude")
    d = cp5x_frozen_ui_project(env)
    tp = d / "t.jsonl"
    rx(d, env, "ask", "ui-respec", "CP1")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "ui-respec", "CP1")
    check("[CP5-X06] ui-respec: 'sounds good' first is refused and the later yes does not count",
          r.returncode != 0 and "sounds good" in r.stderr
          and cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"].get("frozen"), (r.returncode, r.stderr))


SECTIONS.append(("CP5", cp5_adv_round1_first_listed_reply_only))


def cp5_adv_round1_reply_variants_and_phase():
    print("CP5 adv round 1: which replies count as yes, and a question only counts at the pause it was asked at")

    # --- X07: reply variants through human-approve (case and trailing . or ! ignored; nothing else attached) -----
    env = make_env("claude")
    d, tp = cp5_awaiting_checkpoint_review(env)
    for txt in ["yes, but change the header", "yes?", "ok", "sounds good", "lgtm", "yes please"]:
        rx(d, env, "ask", "human-approve")
        cp5_say(d, tp, env, txt)
        r = rx(d, env, "human-approve")
        check(f"[CP5-X07] {txt!r} is not a listed yes: human-approve is refused",
              r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_checkpoint_review", (r.returncode, r.stderr))
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "Yes!")
    r = rx(d, env, "human-approve")
    st = cp3_state(d)
    check("[CP5-X07] 'Yes!' is a listed yes (case and trailing ! ignored): human-approve succeeds",
          r.returncode == 0 and st["checkpoints"]["CP1"]["gates"]["human"].get("status") == "passed",
          (r.returncode, r.stderr))
    check("[CP5-X07] human-approve closes the question it answered", "pending" not in st, st.get("pending"))
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "APPROVED.")
    r = rx(d, env, "approve-plan")
    check("[CP5-X07] 'APPROVED.' is a listed yes (case and trailing . ignored): approve-plan succeeds",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr))

    # --- X08: a review question asked at one pause no longer applies once the run moved to another --------------
    env = make_env("claude")
    d, tp = cp5_awaiting_checkpoint_review(env)
    rx(d, env, "ask", "human-approve")
    cp5x_gate_blocks_run(d, env)
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "human-approve")
    st = cp3_state(d)
    check("[CP5-X08] the yes to a question asked at the step review does not unblock the run",
          r.returncode != 0 and "no longer applies" in r.stderr and st["phase"] == "blocked", (r.returncode, r.stderr))
    check("[CP5-X08] the question is closed and logged as question_not_approved",
          "pending" not in st and bool(cp5x_history(d, "question_not_approved")), st.get("pending"))


SECTIONS.append(("CP5", cp5_adv_round1_reply_variants_and_phase))


def cp5_adv_round1_resubmit_and_feedback():
    print("CP5 adv round 1: resubmitting the plan and giving feedback close the waiting approval question")

    # --- X09: plan-submit drops an approve-plan question about the earlier plan --------------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    r = rx(d, env, "plan-submit")
    check("[CP5-X09] plan-submit again prints a NEXT: line naming `ask approve-plan`, as the only question",
          r.returncode == 0 and "NEXT:" in r.stdout and "ask approve-plan" in r.stdout and "only question" in r.stdout,
          r.stdout)
    check("[CP5-X09] the question about the earlier plan is gone", "pending" not in cp3_state(d), cp3_state(d).get("pending"))
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    check("[CP5-X09] a yes after the resubmit does not approve the new plan without a fresh question",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X10: feedback records the reply that answered the review question, not a later message ----------------
    env = make_env("claude")
    d = cp4_awaiting_review(env)
    tp = d / "t.jsonl"
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "make the button blue")
    cp5_say(d, tp, env, "yes")  # a later message, answering something else
    r = rx(d, env, "feedback")
    st = cp3_state(d)
    check("[CP5-X10] feedback records the first reply after the review question, not the later 'yes'",
          r.returncode == 0 and (st.get("feedback") or [{}])[-1].get("text") == "make the button blue",
          (r.returncode, r.stderr, st.get("feedback")))
    check("[CP5-X10] feedback closes the waiting review question", "pending" not in st, st.get("pending"))
    r = rx(d, env, "human-approve")
    check("[CP5-X10] the later yes can't approve afterwards", r.returncode != 0 and "pending" not in cp3_state(d),
          (r.returncode, r.stderr))

    env = make_env("claude")
    d = cp4_awaiting_review(env)
    tp = d / "t.jsonl"
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "change the header copy")
    r1 = rx(d, env, "human-approve")  # spends the question: the first reply was not a yes
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "feedback")
    st = cp3_state(d)
    check("[CP5-X10] after the question was spent, feedback still records the reply that spent it",
          r1.returncode != 0 and r.returncode == 0
          and (st.get("feedback") or [{}])[-1].get("text") == "change the header copy", (r1.stderr, r.stderr, st.get("feedback")))


SECTIONS.append(("CP5", cp5_adv_round1_resubmit_and_feedback))


def cp5_adv_round1_prompt_hook_guidance():
    print("CP5 adv round 1: the prompt hook tells the agent whether a reply answers the question")

    env = make_env("claude")
    d, tp = cp5_plan(env)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X11] no question yet: the hook says Ratchet has not asked and names `ask approve-plan`",
          "has not asked" in hint and "ask approve-plan" in hint and "approves nothing" in hint, hint)
    q = rx(d, env, "ask", "approve-plan")
    check("[CP5-X11] `ask` tells the agent to show the question as the only one in its message",
          "only question" in q.stdout and "can't see" in q.stdout, q.stdout)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X11] the first reply: the hook says it answers the waiting question and is a listed yes",
          "answers your waiting question" in hint and "It is one of the listed yes replies" in hint, hint)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X11] a second reply: the hook says the question was already answered and this does NOT approve",
          "already answered" in hint and "does NOT" in hint and "ask approve-plan" in hint, hint)
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    hint = cp5x_hint(d, tp, env, "ok")
    check("[CP5-X11] a first reply that is not listed: the hook says it approves nothing",
          "NOT one of the listed yes replies" in hint, hint)


SECTIONS.append(("CP5", cp5_adv_round1_prompt_hook_guidance))


def cp5_adv_round1_docs_match():
    print("CP5 adv round 1: the skills describe the approval question as it works")
    here = Path(__file__).resolve().parents[1]
    orch = (here / "skills/ratchet-orchestrator/SKILL.md").read_text()
    uic = (here / "skills/ratchet-ui-compare/SKILL.md").read_text()
    review = orch[orch.index("4. Run `$RATCHET ask human-approve`"):]
    review = review[:review.index("## Modes")]
    yes_i, wrap_i = review.index("listed yes replies →"), review.index("Give a one-line wrap-up")
    other_i = review.index("Anything else as their first reply")
    check("[CP5-X12] review step: the wrap-up follows only the approving reply, and the non-yes case is its own bullet",
          yes_i < wrap_i < other_i and "\n   - Anything else" in review, review[:400])
    cancel = next(l for l in orch.splitlines() if l.startswith('- **"Cancel"'))
    check("[CP5-X12] cancel shows the printed question word for word", "word for word" in cancel, cancel)
    respec = next(l for l in uic.splitlines() if "ask ui-respec" in l)
    check("[CP5-X12] ui-respec shows the printed question word for word; only the first reply counts",
          "word for word" in respec and "first reply" in respec, respec)
    check("[CP5-X12] one question per message: the plan question is asked alone, after the blockers are settled",
          "only question" in orch and "before step 11" in orch, "")


SECTIONS.append(("CP5", cp5_adv_round1_docs_match))


# ---- CP5 adversarial round 2 ----
# Fixes for CP5's second adversarial round: a question can only be put once a chat owns the run (the chat that ran
# init, or the first chat the prompt hook records for it), so on a repo's first run another chat's yes can't answer
# or move it; a question waiting in another chat, and the hooks question, are never silently replaced; and the
# engine's own hints say to show the printed question word for word.

def cp5y_load_engine():
    spec = importlib.util.spec_from_file_location("ratchet_cp5_round2", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout


def cp5y_first_run_plan(env):
    """The first run in a repo, started before any prompt hook could log a message: no chat owns it yet."""
    d = cp5y_repo()
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    cp5y_submit_plan(d, env)
    return d, d / "t.jsonl"


def cp5y_set_state(d, env, code):
    """Change state.json through the engine's own Ctx.save_state (the way an older run's state would look)."""
    prog = ("import importlib.util, sys\nfrom pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('r', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\nctx = m.Ctx(Path(sys.argv[2])); st = ctx.state()\n" + code +
            "\nctx.save_state(st)\n")
    r = subprocess.run([sys.executable, "-c", prog, str(ENGINE), str(d)], cwd=d, env=env, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr + r.stdout


def cp5_adv_round2_owner_before_questions():
    print("CP5 adv round 2: a question needs an owner chat; another chat's yes never answers or moves the run")

    # --- X13: first run, no owner yet: the chat that runs `ask` (seen by the guard hook) owns the run -------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    check("[CP5-X13] a repo's first run starts with no owner chat when nothing could be logged before init",
          cp3_state(d).get("owner_session") is None, cp3_state(d).get("owner_session"))
    r = rx(d, env, "ask", "claim")
    check("[CP5-X13] `ask claim` with no owner chat is refused (nothing to take over) and stores nothing",
          r.returncode != 0 and "nothing to take over" in r.stderr and not cp3_state(d).get("pending"),
          (r.returncode, r.stderr))
    g = cp5y_guard_bash(d, env, f'python3 "{ENGINE}" ask approve-plan', "s-A")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    rec = cp5x_history(d, "owner_recorded")
    check("[CP5-X13] with no owner, the chat whose `ask` the guard saw becomes the owner and the question's chat",
          g.returncode == 0 and r.returncode == 0 and st.get("owner_session") == "s-A"
          and (st.get("pending") or {}).get("session") == "s-A" and bool(rec) and rec[-1].get("via") == "ask",
          (g.stderr, r.returncode, r.stderr, st.get("owner_session"), st.get("pending"), rec))
    hint = cp5x_hint(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X13] the user's yes in another chat (the first message after the question) approves nothing",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and "another chat" in hint
          and st.get("owner_session") == "s-A" and (st.get("pending") or {}).get("action") == "approve-plan",
          (r.returncode, r.stderr, hint, st.get("owner_session")))
    cp5_say(d, tp, env, "yes", session="s-A")
    r = rx(d, env, "approve-plan")
    check("[CP5-X13] the asking chat's yes then approves, and the owner stays the asking chat",
          r.returncode == 0 and cp3_state(d)["phase"] == "building" and cp3_state(d).get("owner_session") == "s-A",
          (r.returncode, r.stderr))
    # with no guard note (a CLI whose guard doesn't run), the question waits unbound: the first chat that types owns it
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X13] without a guard note the question is still asked, bound to no chat yet",
          r.returncode == 0 and "ASK THE USER" in r.stdout and st.get("owner_session") is None
          and (st.get("pending") or {}).get("session") is None, (r.returncode, r.stderr, st.get("pending")))
    cp5_say(d, tp, env, "wait, not yet", session="s-A")
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X13] ... the first chat to reply owns the run; a later yes in another chat neither approves nor "
          "moves it", r.returncode != 0 and "wait, not yet" in r.stderr and st.get("owner_session") == "s-A"
          and st["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr, st.get("owner_session")))

    # --- X14: two chats, no owner yet: the first chat the prompt hook records owns the run ---------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5_say(d, tp, env, "ok, show me the plan", session="s-A")
    st = cp3_state(d)
    rec = cp5x_history(d, "owner_recorded")
    check("[CP5-X14] the prompt hook alone never binds the owner into state.json or logs it (no command has read "
          "its record yet): the hook keeps its own record, not state.json",
          st.get("owner_session") is None and not rec, (st.get("owner_session"), rec))
    hint = cp5x_hint(d, tp, env, "what's for lunch?", session="s-B")
    check("[CP5-X14] a second chat is told the run was started in another chat, from the hook's own record alone "
          "(state.json still names no owner)",
          "another chat" in hint and cp3_state(d).get("owner_session") is None, hint)
    r = rx(d, env, "ask", "approve-plan")
    pend = cp3_state(d).get("pending") or {}
    check("[CP5-X14] `ask approve-plan` now binds the question to the owner chat, not the chat that typed last",
          r.returncode == 0 and pend.get("session") == "s-A", (r.returncode, r.stderr, pend))
    cp5_say(d, tp, env, "yes", session="s-B")
    cp5_say(d, tp, env, "wait, not yet", session="s-A")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X14] the other chat's yes does not approve: the owner chat's first reply ('wait, not yet') decides",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and "wait, not yet" in r.stderr,
          (r.returncode, r.stderr))
    check("[CP5-X14] ... and the run did not move to the other chat", st.get("owner_session") == "s-A",
          st.get("owner_session"))

    # the same two chats, where the other chat's yes is the only reply: still nothing approved
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5_say(d, tp, env, "go on", session="s-A")
    cp5_say(d, tp, env, "something else", session="s-B")
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X14] a yes typed only in the other chat approves nothing and leaves the owner as it was",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and st.get("owner_session") == "s-A"
          and (st.get("pending") or {}).get("action") == "approve-plan",
          (r.returncode, r.stderr, st.get("owner_session")))

    # --- X15: a question with no chat recorded and no owner (an older run): no reply from any chat counts --------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_set_state(d, env, "st['pending'] = {'action': 'approve-plan', 'mode': None, 'asked_at': m.now(), "
                           "'prev_awaiting': st.get('awaiting_since'), 'phase': st['phase']}")
    cp5_say(d, tp, env, "yes", session="s-B")
    cp5y_set_state(d, env, "st['owner_session'] = None")  # as an older run's state: no owner recorded
    prog = ("import importlib.util, json, sys\nfrom pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('r', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\nctx = m.Ctx(Path(sys.argv[2])); none = {'owner_session': None}\n"
            "print(json.dumps({'replies': m._replies(ctx, none, '0', {'action': 'approve-plan'}),\n"
            "  'ask': m._asking_session(ctx, none, 'approve-plan'), 'claim': m._asking_session(ctx, none, 'claim')}))\n")
    u = subprocess.run([sys.executable, "-c", prog, str(ENGINE), str(d)], cwd=d, env=env, capture_output=True,
                       text=True)
    out = json.loads(u.stdout) if u.returncode == 0 and u.stdout.strip() else {}
    check("[CP5-X15] with no chat known for the question, _replies counts no message at all (no unfiltered fallback)",
          out.get("replies") == [], (u.returncode, u.stdout, u.stderr))
    check("[CP5-X15] with no owner, _asking_session names no chat (it never guesses the chat that typed last)",
          bool(out) and out.get("ask") is None and out.get("claim") is None, out)
    r = rx(d, env, "approve-plan")
    check("[CP5-X15] approve-plan on such a question is refused while no chat owns the run",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X16: the chat that runs `init` owns the run, even on a first run and even if another chat typed last -----
    env = make_env("claude")
    d = cp5y_repo()
    g = cp5y_guard_bash(d, env, f'python3 "{ENGINE}" init "feature" --slug f', "s-A")
    r = rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X16] the guard notes the chat that runs `init`, and init makes it the owner (first run, empty log)",
          g.returncode == 0 and r.returncode == 0 and cp3_state(d).get("owner_session") == "s-A",
          (g.returncode, g.stderr, r.stderr, cp3_state(d).get("owner_session")))
    tp = d / "t.jsonl"
    hint = cp5x_hint(d, tp, env, "yes", session="s-B")
    check("[CP5-X16] another chat's first message then gets the 'another chat' notice and does not take the run",
          "another chat" in hint and cp3_state(d).get("owner_session") == "s-A", hint)
    env = make_env("claude")
    d = cp5y_repo()
    (d / ".ratchet").mkdir()
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "unrelated question", session="s-B")
    cp5y_guard_bash(d, env, "$RATCHET init 'feature' --slug f", "s-A")
    rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X16] the chat that ran init owns the run even when another chat typed last",
          cp3_state(d).get("owner_session") == "s-A", cp3_state(d).get("owner_session"))
    env = make_env("claude")
    d = cp5y_repo()
    (d / ".ratchet").mkdir()
    cp5_say(d, d / "t.jsonl", env, "please build it", session="s-B")
    notes = Path(env["RATCHET_HOME"]) / "engine-call-sessions.json"
    notes.parent.mkdir(parents=True, exist_ok=True)
    old = (now_dt() - dt.timedelta(minutes=30)).isoformat()
    key = f"{d.resolve()}|init"
    notes.write_text(json.dumps({key: {"session": "s-stale", "at": old}}))
    rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X16] a stale init note (older than a few minutes) is ignored and removed",
          cp3_state(d).get("owner_session") == "s-B"
          and key not in json.loads(notes.read_text() or "{}"), cp3_state(d).get("owner_session"))


SECTIONS.append(("CP5", cp5_adv_round2_owner_before_questions))


def cp5_adv_round2_no_silent_overtake():
    print("CP5 adv round 2: another chat's question, and the hooks question, are never silently replaced")

    # --- X17: another chat's `ask claim` must not drop the run chat's waiting question ------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    r = rx(d, env, "ask", "claim")
    st = cp3_state(d)
    check("[CP5-X17] `ask claim` from another chat is refused while the run chat's question waits",
          r.returncode != 0 and "another chat" in r.stderr and "ASK THE USER" not in r.stdout
          and "NOT ASKED" not in r.stdout, (r.returncode, r.stdout, r.stderr))
    check("[CP5-X17] ... the run chat's plan question is kept, and nothing is logged as overtaken",
          (st.get("pending") or {}).get("action") == "approve-plan"
          and (st.get("pending") or {}).get("session") == "s-owner"
          and not cp5x_history(d, "question_overtaken"), (st.get("pending"), cp5x_history(d, "question_overtaken")))
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X17] the owner chat's prompt hook still treats its reply as the answer to the plan question",
          "answers your waiting question" in hint and "another chat" not in hint, hint)
    r = rx(d, env, "approve-plan")
    check("[CP5-X17] the owner's yes then approves the plan",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr))
    # the other way round: the claim waits in the other chat, and the owner chat's question must not drop it
    env = make_env("claude")
    d = project(env)
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    rx(d, env, "ask", "claim")
    r = rx(d, env, "ask", "cancel")
    st = cp3_state(d)
    check("[CP5-X17] the owner chat's `ask cancel` is refused while the claim question waits in the other chat",
          r.returncode != 0 and "another chat" in r.stderr and (st.get("pending") or {}).get("action") == "claim",
          (r.returncode, r.stderr, st.get("pending")))
    cp5_say(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X17] ... and the claiming chat's yes still moves the run there",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-other", (r.returncode, r.stderr))
    # once the waiting question had its reply, another chat's claim may replace it
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "hmm, let me think")
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X17] a question that already had its reply can be replaced by another chat's claim",
          r.returncode == 0 and (cp3_state(d).get("pending") or {}).get("action") == "claim",
          (r.returncode, r.stderr))
    # if the user says the run's chat is gone, an explicit (logged) withdraw lets the claim be asked
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "that old chat is gone, carry on here", session="s-other")
    rx(d, env, "ask", "withdraw")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X17] after an explicit `ask withdraw` (logged), the claim can be asked",
          r.returncode == 0 and "ASK THE USER" in r.stdout and bool(cp5x_history(d, "question_withdrawn")),
          (r.returncode, r.stderr))

    # --- X18: a pending hooks-waive question is never replaced, so its reply can't be hidden -------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "hooks-waive")
    for action in (["cancel"], ["set-review", "each"], ["approve-plan"], ["choice", "--options", "A,B"]):
        r = rx(d, env, "ask", *action)
        st = cp3_state(d)
        check(f"[CP5-X18] `ask {' '.join(action)}` over a waiting hooks question is refused and keeps it",
              r.returncode != 0 and "hooks" in r.stderr and "ask withdraw" in r.stderr
              and (st.get("pending") or {}).get("action") == "hooks-waive"
              and not cp5x_history(d, "question_overtaken"), (r.returncode, r.stdout, r.stderr, st.get("pending")))
    eng = cp5y_load_engine()
    cp5_say(d, tp, env, eng.HOOKS_WAIVE_REPLY)
    r = rx(d, env, "ask", "cancel")
    check("[CP5-X18] after the user answered the hooks question, `ask cancel` is still refused (the reply stays "
          "with the hooks question)", r.returncode != 0 and "hooks-waive" in r.stderr
          and (cp3_state(d).get("pending") or {}).get("action") == "hooks-waive", (r.returncode, r.stderr))
    r = rx(d, env, "hooks-waive")
    check("[CP5-X18] `hooks-waive` then reads the user's reply to its own question (it was not hidden)",
          r.returncode == 0 and "No human message" not in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X18] once the hooks question is settled, the plan question can be asked",
          r.returncode == 0 and "ASK THE USER" in r.stdout, (r.returncode, r.stderr))
    # a first reply that is not the waiver phrase is a no: nothing to hide, so a new question may replace it (logged)
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "hooks-waive")
    cp5_say(d, tp, env, "no, keep the hooks")
    r = rx(d, env, "ask", "approve-plan")
    over = cp5x_history(d, "question_overtaken")
    check("[CP5-X18] after a 'no' to the hooks question, the plan question replaces it and the swap is logged",
          r.returncode == 0 and "ASK THE USER" in r.stdout and bool(over) and over[-1].get("action") == "hooks-waive"
          and over[-1].get("answered") is True, (r.returncode, r.stderr, over))


SECTIONS.append(("CP5", cp5_adv_round2_no_silent_overtake))


def cp5_adv_round2_engine_hints():
    print("CP5 adv round 2: the engine's own hints say to show the printed question word for word")

    # --- X19: ui-record's frozen-spec refusal ----------------------------------------------------------------
    # (CP7: the combined review must be script-signed with real provenance to reach the frozen-spec check at all;
    # cp3_signed_combined's spec_sha256 override produces one that still mismatches round 1's frozen spec)
    env = make_env("claude")
    d = cp5x_frozen_ui_project(env)
    fp = cp3_fp(d, env)
    combined = str(cp3_signed_combined(d, env, fp, n=2, spec_sha256="s2"))
    r = rx(d, env, "ui-record", "CP1", "--review", combined)
    check("[CP5-X19] ui-record's frozen-spec refusal names `ask ui-respec`, says to show the question word for word, "
          "and that only a listed yes as the first reply counts", r.returncode != 0 and "ask ui-respec CP1" in r.stderr
          and "word for word" in r.stderr and "first reply" in r.stderr and "clear yes" not in r.stderr,
          (r.returncode, r.stderr))

    # --- X20: the prompt hook's claim hint (another chat) and cancel hint ---------------------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    hint = cp5x_hint(d, tp, env, "can we pick this up here?", session="s-other")
    check("[CP5-X20] the 'another chat' hint says to show the claim question word for word; first reply, listed yes",
          "ask claim" in hint and "word for word" in hint and "first reply" in hint and "clear yes" not in hint, hint)
    hint = cp5x_hint(d, tp, env, "please cancel this run")
    check("[CP5-X20] the cancel hint says to show the cancel question word for word; first reply, listed yes",
          "ask cancel" in hint and "word for word" in hint and "first reply" in hint and "clear yes" not in hint, hint)

    # --- X21: next_action's plan-approval text is built from PLAN_ASK_NEXT ----------------------------------------
    eng = cp5y_load_engine()
    r = rx(d, env, "next")
    act = json.loads(r.stdout).get("action", "")
    body = eng.PLAN_ASK_NEXT[len("NEXT: "):]
    check("[CP5-X21] `next` while the plan waits (no question yet) says what PLAN_ASK_NEXT says, word for word",
          act.startswith("Waiting for the user to approve the plan. ") and (body[:1].upper() + body[1:]) in act, act)


SECTIONS.append(("CP5", cp5_adv_round2_engine_hints))


# ---- CP5 adversarial round 3 ----
# The guard's "this chat runs ratchet.py init/ask" note is planted only for a real ratchet.py call (parsed shell
# words, its exact arguments), never for text that merely mentions one; the engine takes a note only when the
# arguments equal its own, it is a few seconds old at most, and only once.
# (Correction: "a few seconds" above is loose; the limit is ENGINE_NOTE_SECONDS, 60 seconds.)

def cp5z_notes(env):
    p = Path(env["RATCHET_HOME"]) / "engine-call-sessions.json"
    try:
        return json.loads(p.read_text() or "{}")
    except (OSError, ValueError):
        return {}


def cp5z_project_notes(d, env):
    return {k: v for k, v in cp5z_notes(env).items() if k.startswith(f"{d.resolve()}|")}


def cp5_adv_round3_engine_note_real_call():
    print("CP5 adv round 3: only a real ratchet.py init/ask call, with matching arguments, fresh and unused, names "
          "the chat")
    m = cp5y_load_engine()

    # --- X22: the parser tells a real call from a mention ------------------------------------------------------
    decoys = ['echo "python3 ratchet.py ask approve-plan"', "echo python3 ratchet.py ask approve-plan",
              "git commit -m 'next: $RATCHET ask approve-plan'", 'grep -n "ratchet.py ask" README.md',
              "cat <<EOF\n$RATCHET ask approve-plan\nEOF", "# $RATCHET ask approve-plan",
              "true # ; $RATCHET ask approve-plan", 'echo a#" x\n$RATCHET ask approve-plan"',
              'echo "&&" $RATCHET ask approve-plan', "echo \\; $RATCHET ask approve-plan",
              "printf '%s\\n' \"$RATCHET init feature --slug f\"", "echo `$RATCHET ask approve-plan`"]
    bad = [c for c in decoys if m._engine_invocations(c)]
    check("[CP5-X22] text that only mentions ratchet.py init/ask (echo, commit message, grep, heredoc, comment, "
          "quoted separators) is never read as a call", not bad, bad)
    real = {f'python3 "{ENGINE}" ask approve-plan': [["ask", "approve-plan"]],
            '$RATCHET ask approve-plan 2>&1 | tail -5': [["ask", "approve-plan"]],
            "cd /p && $RATCHET init 'my feature' --slug f; echo done": [["init", "my feature", "--slug", "f"]],
            "X=1 python3 -u ./ratchet.py --root /p ask claim": [["--root", "/p", "ask", "claim"]]}
    got = {c: m._engine_invocations(c) for c in real}
    check("[CP5-X22] a real call is read with its exact arguments", got == real, got)

    # --- X23: mentions in another chat plant nothing and don't change who owns a first run ------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    for c in decoys:
        cp5y_guard_bash(d, env, c, "s-B")
    check("[CP5-X23] the guard notes nothing for those mentions", cp5z_project_notes(d, env) == {},
          cp5z_project_notes(d, env))
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X23] the real `ask` (its own guard note missing) does not hand the run to the chat that mentioned it",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None,
          (r.returncode, r.stderr, st.get("owner_session"), st.get("pending")))
    cp5_say(d, tp, env, "yes", session="s-A")
    r = rx(d, env, "approve-plan")
    check("[CP5-X23] ... the existing rule applies: the first chat to reply (s-A) owns it, and its yes approves",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-A", (r.returncode, r.stderr))
    env = make_env("claude")
    d = cp5y_repo()
    (d / ".ratchet").mkdir()
    cp5_say(d, d / "t.jsonl", env, "please build the feature", session="s-A")
    cp5y_guard_bash(d, env, 'git commit -m "then run $RATCHET init feature --slug f"', "s-B")
    cp5y_guard_bash(d, env, f'echo python3 "{ENGINE}" init feature --slug f', "s-B")
    rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X23] an `init` mentioned in another chat's echo/commit message does not make that chat the owner",
          cp3_state(d).get("owner_session") == "s-A", cp3_state(d).get("owner_session"))

    # --- X24: the real call does name the chat; the note is used once ---------------------------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_guard_bash(d, env, f'cd "{d}" && python3 "{ENGINE}" ask approve-plan 2>&1 | tail -5', "s-B")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X24] the chat whose guard saw the real `ask approve-plan` call owns the run",
          r.returncode == 0 and st.get("owner_session") == "s-B" and (st.get("pending") or {}).get("session") == "s-B",
          (r.returncode, r.stderr, st.get("owner_session")))
    check("[CP5-X24] the note is used up", cp5z_project_notes(d, env) == {}, cp5z_project_notes(d, env))
    cp5y_set_state(d, env, "st['owner_session'] = None; st.pop('pending', None)")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X24] the same call run again without a new guard note names no chat (a note works once)",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None,
          (r.returncode, r.stderr, st.get("owner_session")))

    # --- X25: a note for different arguments, an expired note, or two chats noting the same call: ignored -----
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_guard_bash(d, env, "$RATCHET ask cancel", "s-B")
    rx(d, env, "ask", "approve-plan")
    check("[CP5-X25] a note for another call (`ask cancel`) does not name the chat running `ask approve-plan`",
          cp3_state(d).get("owner_session") is None, cp3_state(d).get("owner_session"))
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    key = f"{d.resolve()}|ask"
    p = Path(env["RATCHET_HOME"]) / "engine-call-sessions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({key: [{"session": "s-B", "argv": ["ask", "approve-plan"],
                                    "at": iso_ago(m.ENGINE_NOTE_SECONDS + 30)}]}))
    rx(d, env, "ask", "approve-plan")
    check("[CP5-X25] an expired note (older than ENGINE_NOTE_SECONDS, seconds not minutes) is ignored and removed",
          m.ENGINE_NOTE_SECONDS <= 120 and cp3_state(d).get("owner_session") is None
          and key not in cp5z_notes(env), (cp3_state(d).get("owner_session"), cp5z_notes(env)))
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-A")
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-B")
    rx(d, env, "ask", "approve-plan")
    check("[CP5-X25] when two chats noted the same call, neither is guessed to be the owner",
          cp3_state(d).get("owner_session") is None and cp5z_project_notes(d, env) == {},
          (cp3_state(d).get("owner_session"), cp5z_project_notes(d, env)))


SECTIONS.append(("CP5", cp5_adv_round3_engine_note_real_call))


# ---- CP5 adversarial round 4 ----
# A claim question belongs to the chat that ran `ask claim` (the guard's note for that exact call), never to the chat
# the user typed in last; another chat's same question never replaces a waiting one; bash $'...' quoting is never
# read as a call; and two approval commands running at once can't both spend the same answer.

def cp5w_claim_project(env):
    d = project(env)
    return d, d / "t.jsonl"


def cp5w_lock(d):
    return Path(d) / ".ratchet" / "state.lock"


def cp5_adv_round4_claim_chat_and_lock():
    print("CP5 adv round 4: the claim belongs to the chat that asked; one approval command at a time")
    m = cp5y_load_engine()

    # --- X26: three chats: the claim binds to the chat that ran `ask claim`, not the one that typed last ---------
    env = make_env("claude")
    d, tp = cp5w_claim_project(env)
    cp5_say(d, tp, env, "carry on with that run here", session="s-B")
    cp5_say(d, tp, env, "what does this function do?", session="s-C")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    r = rx(d, env, "ask", "claim")
    st = cp3_state(d)
    check("[CP5-X26] the claim question belongs to the chat that ran `ask claim` (s-B), not the one typed in last",
          r.returncode == 0 and (st.get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr,
                                                                                   st.get("pending")))
    hint = cp5x_hint(d, tp, env, "yes", session="s-C")
    check("[CP5-X26] the third chat's prompt hook says the run is in another chat (its yes is not the answer)",
          "another chat" in hint and "answers your waiting question" not in hint, hint)
    r = rx(d, env, "resume", "--claim")
    st = cp3_state(d)
    check("[CP5-X26] a yes in the third chat does not answer the claim or move the run",
          r.returncode != 0 and st.get("owner_session") == "s-owner"
          and (st.get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr, st.get("owner_session")))
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X26] the asking chat's own yes then moves the run there",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-B", (r.returncode, r.stderr))
    # the note wins even when the asking chat typed last of all but another chat's note is for a different call
    env = make_env("claude")
    d, tp = cp5w_claim_project(env)
    cp5_say(d, tp, env, "what does this function do?", session="s-C")
    cp5_say(d, tp, env, "carry on with that run here", session="s-B")
    cp5y_guard_bash(d, env, "$RATCHET ask cancel", "s-C")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X26] a note for another call from a third chat doesn't take the claim",
          r.returncode == 0 and (cp3_state(d).get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr))

    # --- X27: another chat's same question never replaces the waiting one -------------------------------------
    env = make_env("claude")
    d, tp = cp5w_claim_project(env)
    cp5_say(d, tp, env, "carry on with that run here", session="s-B")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    rx(d, env, "ask", "claim")
    cp5_say(d, tp, env, "no, carry on with it here instead", session="s-C")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-C")
    r = rx(d, env, "ask", "claim")
    st = cp3_state(d)
    check("[CP5-X27] the same `ask claim` from another chat is refused while s-B's claim waits; nothing overtaken",
          r.returncode != 0 and "another chat" in r.stderr and (st.get("pending") or {}).get("session") == "s-B"
          and not cp5x_history(d, "question_overtaken"), (r.returncode, r.stderr, st.get("pending")))
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X27] the asking chat itself can still print its question again",
          r.returncode == 0 and (cp3_state(d).get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr))
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X27] ... and s-B's yes still moves the run to s-B",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-B", (r.returncode, r.stderr))

    # --- X22 (more decoys): bash $'...' quoting is a mention, never a call --------------------------------------
    # each is ONE bash string: inside $'...' a \' is a quote character, not the end of the string
    ansi = ["echo $'\\' ; $RATCHET ask approve-plan #'", "echo $'it\\'s && python3 ratchet.py ask approve-plan'",
            "printf $'%s\\' ; $RATCHET init feature --slug f\\n'"]
    bad = [c for c in ansi if m._engine_invocations(c)]
    check("[CP5-X22] a ratchet.py call inside (or after) bash $'...' quoting is never read as a call", not bad, bad)
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    for c in ansi:
        cp5y_guard_bash(d, env, c, "s-B")
    check("[CP5-X22] the guard notes nothing for them", cp5z_project_notes(d, env) == {}, cp5z_project_notes(d, env))
    check("[CP5-X22] a plain call is still read", m._engine_invocations("$RATCHET ask approve-plan") ==
          [["ask", "approve-plan"]], m._engine_invocations("$RATCHET ask approve-plan"))
    check("[CP5-X22] the unused _SHELL_SEPARATORS constant is gone", not hasattr(m, "_SHELL_SEPARATORS"))
    # Note (round 5): the check above names a private constant, not behavior; the line is locked, so it stays. The
    # behavior check that replaces it is the round-5 "[CP5-X22] calls after ; && || | ..." check at the end.

    # --- X28: a held lock makes an approval wait; a stale one is cleared -----------------------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    lock = cp5w_lock(d)
    lock.write_text("12345 held-by-test")
    proc = subprocess.Popen([sys.executable, str(ENGINE), "approve-plan"], cwd=d, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    waited = proc.poll() is None and cp3_state(d)["phase"] == "awaiting_plan_approval"
    lock.unlink()
    out, err = proc.communicate(timeout=60)
    check("[CP5-X28] approve-plan waits while another command holds the state lock",
          waited, (proc.returncode, err))
    check("[CP5-X28] ... and approves once it is released, removing its own lock",
          proc.returncode == 0 and cp3_state(d)["phase"] == "building" and not lock.exists(), (proc.returncode, err))
    lock.write_text("12345 crashed")
    old = time.time() - m.STATE_LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    r = rx(d, env, "ask", "withdraw")
    check("[CP5-X28] a stale lock (left by a crashed command) is removed and the command runs",
          r.returncode == 0 and not lock.exists(), (r.returncode, r.stderr))

    # --- X29: two approvals at once (double submit): exactly one spends the answer ------------------------------
    for cmd in ("approve-plan", "human-approve"):
        env = make_env("claude")
        d, tp = cp5_plan(env) if cmd == "approve-plan" else cp5_awaiting_checkpoint_review(env)
        r = rx(d, env, "ask", cmd)
        cp5_say(d, tp, env, "yes")
        events_before = len(cp5x_history(d))
        procs = [subprocess.Popen([sys.executable, str(ENGINE), cmd], cwd=d, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
        res = [(p.communicate(timeout=120), p.returncode) for p in procs]
        codes = sorted(rc for _, rc in res)
        hist = cp5x_history(d)[events_before:]
        kind = "plan_approved" if cmd == "approve-plan" else "approved"
        check(f"[CP5-X29] two `{cmd}` at once: exactly one succeeds, the other refuses plainly",
              codes == [0, 1] and all("Traceback" not in e for (_, e), _ in res), (r.stderr, res))
        check(f"[CP5-X29] ... and the approval is recorded once", len([e for e in hist if e.get("event") == kind]) == 1,
              [e.get("event") for e in hist])
        check(f"[CP5-X29] ... and no lock file is left behind", not cp5w_lock(d).exists())


SECTIONS.append(("CP5", cp5_adv_round4_claim_chat_and_lock))


# ---- CP5 adversarial round 5 ----
# The state lock fails safe: every failure to take it (a read-only folder, a dangling link or a folder in its place)
# waits with a pause, then refuses plainly; a stale lock is cleared by exactly one waiter; the override code is typed
# before the lock is taken; the prompt and stop hooks take the same lock; and the guard blocks only a real
# `ratchet.py hook ...` call, not text that mentions one.

# Runs the engine's own main() with a few observation points: a line in the marker file when the lock wait pauses
# ("waiting") and at every state save ("saved-locked" / "saved-unlocked": whether the lock file existed then).
# With CP5V_GATE set, each save first waits until that file exists (the test holds a command inside its lock).
_CP5V_WRAP = r"""
import importlib.util, os, pathlib, sys, time
spec = importlib.util.spec_from_file_location("ratchet_cp5_round5", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
marker = pathlib.Path(sys.argv[2])
m.STATE_LOCK_WAIT_SECONDS = float(sys.argv[3])
m.STATE_LOCK_HOOK_WAIT_SECONDS = float(sys.argv[3])
gate = os.environ.get("CP5V_GATE")
pause = m._lock_pause
def noting_pause():
    with marker.open("a") as f:
        f.write("waiting\n")
    pause()
m._lock_pause = noting_pause
save = m.Ctx.save_state
def noting_save(self, st):
    with marker.open("a") as f:
        f.write("in-save\n")
    while gate and not pathlib.Path(gate).exists():
        time.sleep(0.02)
    with marker.open("a") as f:
        f.write("saved-locked\n" if (self.ratchet / "state.lock").exists() else "saved-unlocked\n")
    save(self, st)
m.Ctx.save_state = noting_save
m.main(sys.argv[4:])
"""


def cp5v_wrap(d):
    p = Path(d) / "cp5v_wrap.py"
    p.write_text(_CP5V_WRAP)
    return p


def cp5v_start(d, env, marker, wait, *args, gate=None):
    e = dict(env, CP5V_GATE=str(gate)) if gate else env
    return subprocess.Popen([sys.executable, str(cp5v_wrap(d)), str(ENGINE), str(marker), str(wait), *args], cwd=d,
                            env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def cp5v_until(cond, timeout=60):
    """Wait for a condition the child process makes true (never a fixed sleep standing in for an assertion)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def cp5v_lines(marker):
    return Path(marker).read_text().split() if Path(marker).exists() else []


def cp5v_refusal(m, root, wait=0.5):
    """Take the lock in process with a short wait. Returns (outcome, message, seconds, pauses, opens)."""
    import contextlib
    import io
    counts = {"pause": 0, "open": 0}
    pause, real_open = m._lock_pause, os.open

    def counting_pause():
        counts["pause"] += 1
        pause()

    def counting_open(*a, **k):
        counts["open"] += 1
        return real_open(*a, **k)

    saved = m.STATE_LOCK_WAIT_SECONDS
    m.STATE_LOCK_WAIT_SECONDS, m._lock_pause, os.open = wait, counting_pause, counting_open
    err, outcome, t0 = io.StringIO(), "entered", time.monotonic()
    try:
        with contextlib.redirect_stderr(err):
            with m._StateLock(Path(root)):
                pass
    except SystemExit as e:
        outcome = f"refused {e.code}"
    except Exception:
        outcome = "crashed " + traceback.format_exc()
    finally:
        m.STATE_LOCK_WAIT_SECONDS, m._lock_pause, os.open = saved, pause, real_open
    return outcome, err.getvalue(), time.monotonic() - t0, counts["pause"], counts["open"]


def cp5_adv_round5_lock_fails_safe():
    print("CP5 adv round 5: the state lock fails safe, the hooks take it, and the guard blocks only real hook calls")
    m = cp5y_load_engine()

    # --- X30: every failure to create the lock waits with a pause, then refuses plainly within the wait ----------
    cases = []
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet").mkdir()
    os.chmod(root / ".ratchet", 0o555)
    try:
        writable = os.access(root / ".ratchet", os.W_OK)
        if writable:
            print("  - [CP5-X30] read-only folder: not run: this user/OS can write to a read-only folder")
        else:
            cases.append(("a read-only Ratchet folder", cp5v_refusal(m, root)))
    finally:
        os.chmod(root / ".ratchet", 0o755)
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet").mkdir()
    os.symlink(str(root / "nowhere"), str(root / ".ratchet" / "state.lock"))
    cases.append(("a dangling link where the lock goes", cp5v_refusal(m, root)))
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet" / "state.lock").mkdir(parents=True)
    cases.append(("a folder where the lock goes", cp5v_refusal(m, root)))
    old = time.time() - 3600
    os.utime(root / ".ratchet" / "state.lock", (old, old))
    cases.append(("an old folder where the lock goes (never treated as a stale lock)", cp5v_refusal(m, root)))
    for label, (outcome, msg, secs, pauses, opens) in cases:
        check(f"[CP5-X30] {label}: refused (exit 1) with a plain message naming the lock file, no traceback",
              outcome == "refused 1" and msg.startswith("RATCHET ✗") and "state.lock" in msg
              and "Traceback" not in msg, (outcome, msg))
        check(f"[CP5-X30] {label}: refused within the wait, pausing between tries (no spin)",
              0.4 <= secs < 5 and pauses >= 1 and opens <= pauses + 1, (secs, pauses, opens))
    check("[CP5-X30] the folder in the lock's place is left alone", (root / ".ratchet" / "state.lock").is_dir())
    # the same through a real command: a dangling link where the lock goes makes approve-plan refuse plainly, not
    # hang (a read-only Ratchet folder is refused even earlier, by the hook-record check, before any lock)
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    os.symlink(str(Path(d) / "nowhere"), str(cp5w_lock(d)))
    proc = cp5v_start(d, env, marker, 1, "approve-plan")
    out, err = proc.communicate(timeout=60)
    check("[CP5-X30] approve-plan with a dangling link where the lock goes refuses plainly (exit 1, no traceback)",
          proc.returncode == 1 and "lock file" in err and "Traceback" not in err and "waiting" in cp5v_lines(marker)
          and cp3_state(d)["phase"] == "awaiting_plan_approval", (proc.returncode, err[-400:]))
    os.unlink(str(cp5w_lock(d)))

    # --- X31: two waiters on one stale lock: exactly one takes it; a live lock is never cleared ------------------
    import threading
    both = []
    for _trial in range(10):
        root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
        (root / ".ratchet").mkdir()
        lockf = root / ".ratchet" / "state.lock"
        lockf.write_text("12345 crashed")
        os.utime(lockf, (old, old))
        locks = [m._StateLock(root, wait=0.3) for _ in range(2)]
        start = threading.Barrier(2)
        got = {}

        def take(i):
            start.wait()
            got[i] = locks[i].acquire()

        threads = [threading.Thread(target=take, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        winners = [i for i in range(2) if got.get(i) is None]
        ok_trial = (len(winners) == 1 and lockf.read_text() == locks[winners[0]].token
                    and "still changing" in str(got.get(1 - winners[0]))
                    and not (root / ".ratchet" / "state.lock.break").exists())
        if winners:
            locks[winners[0]].release()
        both.append((ok_trial, winners, got, lockf.exists()))
    check("[CP5-X31] two waiters on the same stale lock: only one takes it, the other refuses (10 races)",
          all(t[0] for t in both), [t for t in both if not t[0]][:2])
    check("[CP5-X31] ... and the one that took it removes its own lock when it is done", not any(t[3] for t in both),
          both)
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet").mkdir()
    (root / ".ratchet" / "state.lock").write_text("999 live")
    why = m._StateLock(root, wait=0.3).acquire()
    check("[CP5-X31] a live (fresh) lock is waited on and refused, never cleared",
          "still changing" in str(why) and (root / ".ratchet" / "state.lock").read_text() == "999 live", why)

    # --- X32 (X28 without a sleep): approve-plan waits on a held lock, then takes and removes its own ------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    lock = cp5w_lock(d)
    lock.write_text("12345 held-by-test")
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    proc = cp5v_start(d, env, marker, 30, "approve-plan")
    seen = cp5v_until(lambda: "waiting" in cp5v_lines(marker))
    waiting = seen and proc.poll() is None and cp3_state(d)["phase"] == "awaiting_plan_approval"
    lock.unlink()
    out, err = proc.communicate(timeout=60)
    check("[CP5-X32] approve-plan is seen waiting on a held lock (its own wait marker), approving nothing meanwhile",
          waiting, (seen, proc.returncode, err[-300:]))
    lines = cp5v_lines(marker)
    check("[CP5-X32] ... once released it approves while holding its own lock, then removes that lock",
          proc.returncode == 0 and cp3_state(d)["phase"] == "building" and "saved-locked" in lines
          and "saved-unlocked" not in lines and not lock.exists(), (proc.returncode, err[-300:], lines))

    # --- X33: the override code is typed back before the lock is taken -------------------------------------------
    if not HAVE_PTY:
        print("  - [CP5-X33] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d, tp = cp5_plan(env)
        held = []

        def answer(master, output):
            if cp4_find_code(output) and not held:
                held.append(cp5w_lock(d).exists())
            return cp4_answer_correct(master, output)

        rc, out = cp4_pty_run(["approve-plan"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), answer)
        check("[CP5-X33] approve-plan with the override holds no lock while it waits for the code",
              held == [False], (held, out[-300:]))
        check("[CP5-X33] ... and still approves once the code is typed back, leaving no lock",
              rc == 0 and cp3_state(d)["phase"] == "building" and not cp5w_lock(d).exists(), (rc, out[-300:]))

    # --- X34: the prompt hook never waits on, or writes, the state lock; the stop hook refuses while it's held -------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    state_file = Path(d) / ".ratchet" / "runs" / "f" / "state.json"
    before, log_before = state_file.read_bytes(), len(cp4_human_log_lines(d))
    cp5w_lock(d).write_text("12345 held-by-test")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    t0 = time.monotonic()
    proc = cp5v_start(d, env, marker, 0.5, "hook", "prompt", "--host", "claude")
    out, err = proc.communicate(json.dumps({"cwd": str(d), "prompt": "yes", "session_id": "s-owner",
                                            "transcript_path": str(tp)}), timeout=60)
    took = time.monotonic() - t0
    check("[CP5-X34] the prompt hook records the reply at once even with the state lock held, never waiting on it, "
          "and leaves state.json (which only the command holding the lock may write) untouched",
          proc.returncode == 0 and took < 10 and "waiting" not in cp5v_lines(marker)
          and len(cp4_human_log_lines(d)) == log_before + 1 and cp4_human_log_lines(d)[-1].get("prompt") == "yes"
          and state_file.read_bytes() == before, (proc.returncode, took, cp5v_lines(marker), err[-300:]))
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    proc = cp5v_start(d, env, marker, 0.5, "hook", "stop", "--host", "claude")
    out, err = proc.communicate(json.dumps({"session_id": "s-owner", "cwd": str(d), "hook_event_name": "Stop",
                                            "stop_hook_active": False}), timeout=60)
    check("[CP5-X34] the stop hook can't take a held lock: it changes nothing and does not let the agent stop "
          "silently", "could not be checked" in out + err and state_file.read_bytes() == before, (out, err[-300:]))
    cp5w_lock(d).unlink()
    r = rx(d, env, "approve-plan")
    check("[CP5-X34] ... so the yes the prompt hook recorded while the lock was held still approves it, once the "
          "lock is gone: nothing it recorded was ever lost", r.returncode == 0
          and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr[-200:]))
    # a message arriving while approve-plan holds the lock is recorded at once; it never waits, and is never lost
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    gate = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-g-")) / "go"
    m1 = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    m2 = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    approver = cp5v_start(d, env, m1, 30, "approve-plan", gate=gate)
    in_save = cp5v_until(lambda: "in-save" in cp5v_lines(m1))
    t0 = time.monotonic()
    hook = cp5v_start(d, env, m2, 30, "hook", "prompt", "--host", "claude")
    h_out, h_err = hook.communicate(json.dumps({"cwd": str(d), "prompt": "thanks, carry on", "session_id": "s-owner"}),
                                    timeout=60)
    hook_took = time.monotonic() - t0
    gate.write_text("go")
    a_out, a_err = approver.communicate(timeout=60)
    st = cp3_state(d)
    check("[CP5-X34] a prompt arriving while approve-plan holds the lock returns at once, never waiting on it",
          in_save and hook.returncode == 0 and hook_took < 10 and "waiting" not in cp5v_lines(m2),
          (in_save, hook_took, cp5v_lines(m2), h_err[-300:]))
    check("[CP5-X34] ... and nothing approve-plan wrote is lost; the message is still recorded",
          approver.returncode == 0 and st["phase"] == "building" and not st.get("pending")
          and len(cp5x_history(d, "plan_approved")) == 1
          and cp4_human_log_lines(d)[-1].get("prompt") == "thanks, carry on" and not cp5w_lock(d).exists(),
          (approver.returncode, a_err[-200:], st.get("phase"), st.get("pending")))

    # --- X35: the guard blocks a real `ratchet.py hook ...` call, never text that only mentions one --------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    mentions = ['grep -n "hook_prompt\\|hook-prompt" skills/ratchet-orchestrator/scripts/ratchet.py',
                'echo "the agent CLI runs ratchet.py hook prompt for you"',
                'git commit -m "say what hook-stop does"',
                "grep -rn 'hook stop' README.md  # the hook-guard section too",
                "cat SKILL.md | grep hook-guard"]
    wrongly = [(c, r.stderr[-200:]) for c in mentions for r in [cp5y_guard_bash(d, env, c, "s-owner")]
               if r.returncode != 0]
    check("[CP5-X35] grep / echo / a commit message / a comment that mention a hook are not blocked", not wrongly,
          wrongly)
    calls = ["python3 x/ratchet.py hook prompt", "$RATCHET hook-prompt --host claude",
             "env FOO=1 python3 /x/ratchet.py hook stop", "bash -c 'python3 ratchet.py hook prompt'",
             "timeout 5 python3 -u ratchet.py --root . hook guard", "$R hook prompt",
             "echo $(date) | python3 ratchet.py hook prompt", "eval 'python3 ratchet.py hook-stop'",
             "cat p.json | ./ratchet.py hook prompt --host claude"]
    missed = [(c, r.returncode) for c in calls for r in [cp5y_guard_bash(d, env, c, "s-owner")]
              if r.returncode != 2 or "called by your agent CLI" not in r.stderr]
    check("[CP5-X35] a real call of the hook command (direct, $VAR, env, bash -c, timeout, eval, or a command the "
          "guard can't read) is still blocked", not missed, missed)

    # --- X22 (behavior, in place of the private-name check above): ; && || | and newlines separate calls --------
    got = m._engine_invocations("echo hi; $RATCHET ask approve-plan && $RATCHET ask cancel || true | cat\n"
                                "$RATCHET init x")
    check("[CP5-X22] calls after ; && || | and a newline are each read as their own call",
          got == [["ask", "approve-plan"], ["ask", "cancel"], ["init", "x"]], got)


SECTIONS.append(("CP5", cp5_adv_round5_lock_fails_safe))


# ---- CP5 adversarial round 6 ----
# A long engine command (behavior runs the project's tests, for up to test_timeout_sec) holds the state lock for its
# whole run (the user's round-7 decision: serialise, don't merge — see learnings.md). So an approval made while it
# runs is never lost, but it is not applied either: it is refused at once as busy, and goes through once behavior has
# finished, on the yes the prompt hook already recorded (the hook itself never waits on the lock or writes state.json).

def cp5_adv_round6_long_command_keeps_approval():
    print("CP5 adv round 6: an approval made while behavior runs is refused as busy, then applied once it finishes")
    env = make_env("claude")
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5u-"))
    hold, running, go = tmp / "hold", tmp / "running", tmp / "go"
    waiter = tmp / "waiter.py"  # no `sleep` on the hermetic PATH: the test command waits through Python
    waiter.write_text("import os, sys, time\nhold, running, go = sys.argv[1:4]\nif os.path.exists(hold):\n"
                      "    open(running, 'w').close()\n    while not os.path.exists(go):\n        time.sleep(0.05)\n")
    command = f"test -f src/f.js || exit 1; '{sys.executable}' '{waiter}' '{hold}' '{running}' '{go}'"
    d = cp3_project(env, extra_cp={"tests": {"files": ["tests/cp1.test.js"], "command": command}})
    tp = d / "t.jsonl"
    cp3_write_tests(d)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    assert rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r_ask = rx(d, env, "ask", "set-review", "each")
    hold.write_text("1")
    proc = subprocess.Popen([sys.executable, str(ENGINE), "behavior", "CP1"], cwd=d, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    said, r_set, set_s, mid = None, None, 99, {}
    try:
        started = cp5v_until(lambda: running.exists())
        lock_text = cp5w_lock(d).read_text() if cp5w_lock(d).exists() else ""
        said = cp5_say(d, tp, env, "yes")
        r_set, set_s = cp5r7_timed(d, env, "set-review", "each")
        mid = cp3_state(d)
    finally:
        go.write_text("go")
    out, err = proc.communicate(timeout=120)
    st = cp3_state(d)
    check("[CP5-X36] behavior holds the state lock through its whole test run, and the lock file names it",
          r_ask.returncode == 0 and started and "behavior" in lock_text.split(),
          (r_ask.stderr[-200:], started, lock_text))
    check("[CP5-X36] the user's yes typed while behavior runs is recorded at once by the prompt hook, which neither "
          "waits for the lock nor writes state.json",
          said is not None and said.returncode == 0 and cp4_human_log_lines(d)[-1].get("prompt") == "yes",
          said and said.stderr[-200:])
    check("[CP5-X36] a set-review approved while behavior runs is refused quickly as busy, and changes nothing",
          r_set is not None and cp5r7_busy(r_set, "behavior") and set_s < 15
          and mid.get("human_review") != "each" and (mid.get("pending") or {}).get("action") == "set-review",
          (set_s, r_set and r_set.stderr[-300:], mid.get("human_review"), mid.get("pending")))
    r_after = rx(d, env, "set-review", "each")
    check("[CP5-X36] once behavior has finished, that same approval goes through on the yes already recorded "
          "(nothing was ever lost), and behavior's own result is saved too, leaving no lock",
          proc.returncode == 0 and r_after.returncode == 0 and cp3_state(d).get("human_review") == "each"
          and st["checkpoints"]["CP1"]["gates"]["behavior"]["status"] == "passed"
          and len(cp5x_history(d, "behavior_passed")) == 1 and not cp5w_lock(d).exists(),
          (proc.returncode, err[-200:], r_after.stderr[-300:], st["checkpoints"]["CP1"]["gates"]["behavior"]))


SECTIONS.append(("CP5", cp5_adv_round6_long_command_keeps_approval))


# ---- CP5 adversarial round 7 ----
# Ratchet serialises instead of merging (the user's decision, learnings.md, CP5 round 7): every command that changes a
# run holds the state lock for its whole run, its test runs and regression suite included. A command that would wait
# behind a long one refuses at once as busy and changes nothing. The prompt hook never takes the lock and never
# writes state.json; the stop hook keeps its count of refused stops in its own file.

_CP5R7_WAITER = ("import os, sys, time\nhold, running, go = sys.argv[1:4]\nif os.path.exists(hold):\n"
                 "    open(running, 'w').close()\n    while not os.path.exists(go):\n        time.sleep(0.05)\n")


def cp5r7_gate(tmp, name):
    """A shell command that, while `<name>-hold` exists, notes it started and waits for `<name>-go` (no `sleep` on
    the hermetic PATH). Returns (hold, running, go, command)."""
    waiter = tmp / "waiter.py"
    waiter.write_text(_CP5R7_WAITER)
    hold, running, go = tmp / f"{name}-hold", tmp / f"{name}-running", tmp / f"{name}-go"
    return hold, running, go, f"'{sys.executable}' '{waiter}' '{hold}' '{running}' '{go}'"


def cp5r7_project(env):
    """cp3_project, with a behavior test command and a regression suite that each wait on marker files when held.
    CP1 is implemented and waiting for its behavior gate. Returns (d, tp, {"behavior": .., "regression": ..})."""
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-g-"))
    beh, reg = cp5r7_gate(tmp, "behavior"), cp5r7_gate(tmp, "regression")
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = tmp / "t.jsonl"  # outside the project: a message typed after a gate passed must not change the code
    tp.write_text("")
    (d / ".ratchet").mkdir(exist_ok=True)
    cp5_say(d, tp, env, "please build the feature")
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", f"regression_command={reg[3]}")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": f"test -f src/f.js || exit 1; {beh[3]}"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    cp3_write_tests(d)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    assert rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert rx(d, env, "implemented", "CP1").returncode == 0
    return d, tp, {"behavior": beh[:3], "regression": reg[:3]}


def cp5r7_at_complete(d, env):
    """Behavior and adversarial gates passed: CP1 waits for `complete`."""
    r = rx(d, env, "behavior", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = cp3_reviewer_writes(d, env, rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", good)
    assert r.returncode == 0, r.stderr + r.stdout


def cp5r7_start(d, env, *args):
    return subprocess.Popen([sys.executable, str(ENGINE), *args], cwd=d, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def cp5r7_timed(d, env, *args):
    t0 = time.monotonic()
    r = rx(d, env, *args)
    return r, time.monotonic() - t0


def cp5r7_busy(r, holder):
    return (r.returncode != 0 and f"Ratchet is busy running {holder}" in r.stderr
            and "try again when it finishes" in r.stderr)


def cp5r7_commits(d):
    return int(git(d, "rev-list", "--count", "HEAD").stdout.strip())


_CP5R7_FAIL_AFTER_COMMIT = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ratchet_cp5r7", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def failing_save(self, st):
    m.die("simulated failure after the checkpoint commit")
m.Ctx.save_state = failing_save
m.main(sys.argv[2:])
"""


def cp5_adv_round7_one_command_at_a_time():
    print("CP5 adv round 7: a command that changes a run holds the state lock for its whole run; hooks never wait")
    m = cp5y_load_engine()

    # --- X38: a cancel approved while behavior's tests run is refused as busy, and works once behavior is done -----
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    hold, running, go = gates["behavior"]
    r_ask = rx(d, env, "ask", "cancel")
    hold.write_text("1")
    proc = cp5r7_start(d, env, "behavior", "CP1")
    lock_text, said, said_s, r_cancel, cancel_s, r_second, second_s, mid = "", None, 99, None, 99, None, 99, {}
    try:
        started = cp5v_until(lambda: running.exists())
        lock_text = cp5w_lock(d).read_text() if cp5w_lock(d).exists() else ""
        t0 = time.monotonic()
        said = cp5_say(d, tp, env, "yes")
        said_s = time.monotonic() - t0
        r_cancel, cancel_s = cp5r7_timed(d, env, "cancel")
        r_second, second_s = cp5r7_timed(d, env, "behavior", "CP1")
        mid = cp3_state(d)
    finally:
        go.write_text("go")
    out, err = proc.communicate(timeout=120)
    st = cp3_state(d)
    check("[CP5-X38] behavior holds the state lock through its whole test run, and the lock file names it",
          r_ask.returncode == 0 and started and "behavior" in lock_text.split(), (r_ask.stderr[-200:], lock_text))
    check("[CP5-X38] the user's yes typed meanwhile is recorded at once (the prompt hook neither waits nor refuses)",
          said is not None and said.returncode == 0 and said_s < 10
          and cp4_human_log_lines(d)[-1].get("prompt") == "yes", (said_s, said and said.stderr[-200:]))
    check("[CP5-X38] a cancel approved while behavior runs is refused quickly as busy, telling the agent to run it "
          "again when behavior finishes, and changes nothing",
          r_cancel is not None and cp5r7_busy(r_cancel, "behavior") and cancel_s < 15
          and mid.get("phase") == "building" and (mid.get("pending") or {}).get("action") == "cancel",
          (cancel_s, r_cancel and r_cancel.stderr[-300:], mid.get("phase")))
    r_after = rx(d, env, "cancel")
    check("[CP5-X38] once behavior has finished, the same cancel works on the yes already recorded",
          proc.returncode == 0 and r_after.returncode == 0 and cp3_state(d)["phase"] == "cancelled"
          and not cp5w_lock(d).exists(), (proc.returncode, err[-200:], r_after.stderr[-300:]))

    # --- X39: two behavior runs at once: the second refuses as busy; only the first one's result counts ---------
    g = st["checkpoints"]["CP1"]["gates"]["behavior"]
    ev = d / ".ratchet/runs/f/evidence/CP1"
    check("[CP5-X39] a second behavior run while the first is still running is refused quickly as busy",
          r_second is not None and cp5r7_busy(r_second, "behavior") and second_s < 15,
          (second_s, r_second and r_second.stderr[-300:]))
    check("[CP5-X39] ... so the gate, its history and its evidence agree: one attempt, one pass, one log",
          g.get("status") == "passed" and g.get("attempts") == 1 and len(cp5x_history(d, "behavior_passed")) == 1
          and (ev / "behavior-01.log").exists() and not (ev / "behavior-02.log").exists(),
          (g, [e.get("event") for e in st.get("history", [])][-6:]))

    # --- X40: the prompt hook never writes state.json and never waits on the lock -------------------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)  # no chat owns this run yet: the hook used to write the owner into state.json
    sp = d / ".ratchet/runs/f/state.json"
    before = sp.read_bytes()
    cp5w_lock(d).write_text(f"{os.getpid()} cp5r7-token behavior")  # a live, long command holds the lock
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-m-")) / "marker"
    t0 = time.monotonic()
    hook = cp5v_start(d, env, marker, 30, "hook", "prompt", "--host", "claude")
    h_out, h_err = hook.communicate(json.dumps({"cwd": str(d), "prompt": "ok, show me the plan", "session_id": "s-A",
                                                "transcript_path": str(tp)}), timeout=60)
    took = time.monotonic() - t0
    check("[CP5-X40] while a long command holds the lock, the prompt hook returns at once, never waiting on it",
          hook.returncode == 0 and took < 10 and "waiting" not in cp5v_lines(marker),
          (hook.returncode, took, cp5v_lines(marker), h_err[-300:]))
    check("[CP5-X40] ... it records the message and leaves state.json byte for byte unchanged, even as the first "
          "chat of a run no chat owns", sp.read_bytes() == before
          and cp4_human_log_lines(d)[-1].get("prompt") == "ok, show me the plan" and cp5w_lock(d).exists(),
          (cp4_human_log_lines(d)[-1:], h_err[-200:]))
    t0 = time.monotonic()
    said = cp5_say(d, tp, env, "and the plan's second step?", session="s-A")
    check("[CP5-X40] a second message, with the lock still held, is recorded the same way, state.json untouched",
          said.returncode == 0 and time.monotonic() - t0 < 10 and sp.read_bytes() == before
          and cp4_human_log_lines(d)[-1].get("prompt") == "and the plan's second step?", said.stderr[-200:])
    cp5w_lock(d).unlink()
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    rec = cp5x_history(d, "owner_recorded")
    check("[CP5-X40] the next command, under its own lock, makes that first chat the run's owner from the hook's "
          "record", r.returncode == 0 and st.get("owner_session") == "s-A" and bool(rec)
          and rec[-1].get("via") == "prompt" and (st.get("pending") or {}).get("session") == "s-A",
          (r.returncode, r.stderr[-200:], st.get("owner_session"), rec))

    # --- X41: the stop hook counts refused stops in its own file, never in state.json ---------------------------
    env = make_env("claude")
    d = cp3_project(env)
    sp = d / ".ratchet/runs/f/state.json"
    before = sp.read_bytes()
    stop = json.dumps({"session_id": "s-owner", "cwd": str(d), "hook_event_name": "Stop", "stop_hook_active": False})
    r1 = rx(d, env, "hook", "stop", "--host", "claude", stdin=stop)
    r2 = rx(d, env, "hook", "stop", "--host", "claude", stdin=stop)
    blocks = d / ".ratchet/runs/f/stop-blocks.jsonl"
    recs = [json.loads(ln) for ln in blocks.read_text().splitlines() if ln.strip()] if blocks.exists() else []
    check("[CP5-X41] the stop hook refuses a stop mid-build, counts it in its own record file, and leaves "
          "state.json byte for byte unchanged", "RATCHET GATE" in r1.stdout + r1.stderr
          and "RATCHET GATE" in r2.stdout + r2.stderr and sp.read_bytes() == before
          and [x.get("phase") for x in recs] == ["building", "building"], (r1.stdout[-200:], recs))

    # --- X42: complete holds the lock through its regression suite --------------------------------------------
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    cp5r7_at_complete(d, env)
    hold, running, go = gates["regression"]
    r_ask = rx(d, env, "ask", "set-review", "each")
    cp5_say(d, tp, env, "yes")
    hold.write_text("1")
    proc = cp5r7_start(d, env, "complete", "CP1")
    lock_text, r_set, set_s, mid = "", None, 99, {}
    try:
        started = cp5v_until(lambda: running.exists())
        lock_text = cp5w_lock(d).read_text() if cp5w_lock(d).exists() else ""
        r_set, set_s = cp5r7_timed(d, env, "set-review", "each")
        mid = cp3_state(d)
    finally:
        go.write_text("go")
    out, err = proc.communicate(timeout=120)
    st = cp3_state(d)
    check("[CP5-X42] complete holds the state lock through its regression suite, and the lock file names it",
          r_ask.returncode == 0 and started and "complete" in lock_text.split(), (r_ask.stderr[-200:], lock_text))
    check("[CP5-X42] a review-mode change approved meanwhile is refused quickly as busy and changes nothing",
          r_set is not None and cp5r7_busy(r_set, "complete") and set_s < 15 and mid.get("human_review") == "end"
          and mid["checkpoints"]["CP1"]["status"] != "done", (set_s, r_set and r_set.stderr[-300:]))
    check("[CP5-X42] ... complete itself finishes and leaves no lock",
          proc.returncode == 0 and st["checkpoints"]["CP1"]["status"] == "done" and not cp5w_lock(d).exists(),
          (proc.returncode, err[-300:]))
    r_set = rx(d, env, "set-review", "each")
    check("[CP5-X42] ... after which the same change goes through on the yes already recorded",
          r_set.returncode == 0 and cp3_state(d).get("human_review") == "each", r_set.stderr[-300:])
    r_again = rx(d, env, "set-review", "none")
    check("[CP5-X42] ... and that answered question stays spent: nothing brings it back for a second use",
          r_again.returncode != 0 and not cp3_state(d).get("pending") and cp3_state(d).get("human_review") == "each"
          and len(cp5x_history(d, "review_mode_changed")) == 1, (r_again.returncode, r_again.stderr[-200:]))

    # --- X43: running complete again after it failed past its commit makes no second commit ---------------------
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    cp5r7_at_complete(d, env)
    base = cp5r7_commits(d)
    fail = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-f-")) / "fail_after_commit.py"
    fail.write_text(_CP5R7_FAIL_AFTER_COMMIT)
    r1 = subprocess.run([sys.executable, str(fail), str(ENGINE), "complete", "CP1"], cwd=d, env=env,
                        capture_output=True, text=True)
    head1 = git(d, "rev-parse", "HEAD").stdout.strip()[:7]
    check("[CP5-X43] (setup) complete made the checkpoint commit, then failed before saving, leaving no lock",
          r1.returncode != 0 and "simulated failure" in r1.stderr and cp5r7_commits(d) == base + 1
          and cp3_state(d)["checkpoints"]["CP1"]["status"] != "done" and not cp5w_lock(d).exists(),
          (r1.returncode, r1.stderr[-300:], cp5r7_commits(d), base))
    r2 = rx(d, env, "complete", "CP1")
    st = cp3_state(d)
    check("[CP5-X43] running complete again makes no second checkpoint commit and records the first one",
          r2.returncode == 0 and cp5r7_commits(d) == base + 1 and st["checkpoints"]["CP1"].get("commit") == head1
          and st["checkpoints"]["CP1"]["status"] == "done" and len(cp5x_history(d, "checkpoint_complete")) == 1,
          (r2.returncode, r2.stderr[-300:], cp5r7_commits(d), st["checkpoints"]["CP1"].get("commit"), head1))

    # --- X44: a live command's lock is never taken for a crashed one's while a test run may still be going ------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    lock = cp5w_lock(d)
    lock.write_text("12345 cp5r7-token behavior")
    t = m.DEFAULT_CONFIG["test_timeout_sec"]
    aged = time.time() - (t + 60)  # older than the longest test run, still within the margin
    os.utime(lock, (aged, aged))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X44] a lock last refreshed longer ago than test_timeout_sec, but within the margin, still counts "
          "as held: the command refuses as busy and the lock stays",
          cp5r7_busy(r, "behavior") and lock.exists() and not cp3_state(d).get("pending"), r.stderr[-300:])
    rx(d, env, "config", "--set", f"test_timeout_sec={t * 4}")
    aged = time.time() - (t + m.STATE_LOCK_STALE_MARGIN_SECONDS + 60)  # stale by the default, live by this config
    os.utime(lock, (aged, aged))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X44] a project's longer test_timeout_sec lengthens the wait before a lock counts as stale",
          cp5r7_busy(r, "behavior") and lock.exists(), r.stderr[-300:])
    aged = time.time() - (t * 4 + m.STATE_LOCK_STALE_MARGIN_SECONDS + 60)
    os.utime(lock, (aged, aged))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X44] a lock older than test_timeout_sec plus the margin is left from a crash: cleared, and the "
          "command runs", r.returncode == 0 and not lock.exists()
          and (cp3_state(d).get("pending") or {}).get("action") == "approve-plan", r.stderr[-300:])


SECTIONS.append(("CP5", cp5_adv_round7_one_command_at_a_time))


# ---- CP5 adversarial round 8 ----
# init takes the state lock even in a project with no Ratchet folder yet (it makes the folder first), and the
# per-user hash record's own lock, file-hashes.lock, keeps every hash when hooks append while a locked command runs.

_CP5R8_APPENDER = """
import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5r8", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
target, start, n = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
while not start.exists():
    time.sleep(0.002)
for i in range(n):
    m.append_recorded(target, '{"i": %d}\\n' % i)
"""


def cp5r8_sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def cp5r8_hashes(env):
    p = Path(env["RATCHET_HOME"]) / "file-hashes.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def cp5r8_jsonl_ok(p):
    """(every line parses as JSON, the parsed lines)."""
    try:
        return True, [json.loads(ln) for ln in Path(p).read_text().splitlines() if ln.strip()]
    except Exception:
        return False, []


def cp5r8_prompt_proc(d, env, tp, text):
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    p = subprocess.Popen([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner", "transcript_path": str(tp)}))
    p.stdin.close()
    return p


def cp5_adv_round8_init_lock_and_hash_record_lock():
    print("CP5 adv round 8: first inits are serialised; the hash record's lock keeps every hash under a race")

    # --- X45: concurrent first-run inits in a project with no Ratchet folder: exactly one runs -----------------
    env = make_env("claude")
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r8-i-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "a.txt").write_text("x\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    had_folder = (d / ".ratchet").exists()
    procs = [subprocess.Popen([sys.executable, str(ENGINE), "init", f"feature {i}", "--slug", f"s{i}"], cwd=d,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
    results = [(p,) + p.communicate(timeout=120) for p in procs]
    won = [i for i, (p, _, _) in enumerate(results) if p.returncode == 0]
    lost = [results[i] for i in range(4) if i not in won]
    runs = sorted(x.name for x in (d / ".ratchet/runs").iterdir()) if (d / ".ratchet/runs").is_dir() else []
    active = (json.loads((d / ".ratchet/active.json").read_text()) or {}).get("slug") \
        if (d / ".ratchet/active.json").exists() else None
    check("[CP5-X45] four inits started at once in a project with no Ratchet folder: exactly one starts a run",
          not had_folder and len(won) == 1 and runs == [f"s{won[0]}" if won else None] and active == runs[0],
          (won, runs, active, [(p.returncode, e[-200:]) for p, _, e in results]))
    check("[CP5-X45] ... every other init refuses plainly, naming the run already active, with no traceback",
          len(lost) == 3 and all(p.returncode != 0 and "is still active" in e and "Traceback" not in e
                                 for p, _, e in lost), [(p.returncode, e[-300:]) for p, _, e in lost])
    check("[CP5-X45] ... and no lock is left behind", not cp5w_lock(d).exists()
          and not (d / ".ratchet/state.lock.break").exists())
    r = rx(d, env, "init", "another", "--slug", "s9")
    check("[CP5-X45] a later init still refuses while that run is active (the lock changed nothing else)",
          r.returncode != 0 and "is still active" in r.stderr and not (d / ".ratchet/runs/s9").exists(),
          r.stderr[-300:])

    # --- X46: hooks and other writers race on the hash record while a locked command runs and saves ------------
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    hold, running, go = gates["behavior"]
    rh = Path(env["RATCHET_HOME"])
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r8-a-"))
    script = tmp / "appender.py"
    script.write_text(_CP5R8_APPENDER)
    start = tmp / "start"
    n_app, n_lines, n_prompts, n_stops = 3, 25, 8, 3
    targets = [d / f".ratchet/runs/f/cp5r8-race-{i}.jsonl" for i in range(n_app)]
    hold.write_text("1")
    beh = cp5r7_start(d, env, "behavior", "CP1")
    started = cp5v_until(lambda: running.exists())
    appenders = [subprocess.Popen([sys.executable, str(script), str(ENGINE), str(t), str(start), str(n_lines)],
                                  cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for t in targets]
    texts = [f"cp5r8 message {i}" for i in range(n_prompts)]
    prompts = [cp5r8_prompt_proc(d, env, tp, t) for t in texts]
    stop = json.dumps({"session_id": "s-owner", "cwd": str(d), "hook_event_name": "Stop", "stop_hook_active": False})
    stops = []
    for _ in range(n_stops):
        sp_ = subprocess.Popen([sys.executable, str(ENGINE), "hook", "stop", "--host", "claude"], cwd=d, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        sp_.stdin.write(stop)
        sp_.stdin.close()
        stops.append(sp_)
    start.write_text("go")
    go.write_text("go")  # behavior saves state.json (and its hash) while the hooks and appenders still run
    p_res = [(p.wait(timeout=120), p.stderr.read()) for p in prompts]
    s_res = [(p.wait(timeout=120), p.stderr.read()) for p in stops]
    a_res = [(p.wait(timeout=120), p.stderr.read()) for p in appenders]
    b_out, b_err = beh.communicate(timeout=120)
    log_ok, log = cp5r8_jsonl_ok(d / ".ratchet/human_log.jsonl")
    said = [x.get("prompt") for x in log]
    check("[CP5-X46] (setup) behavior held the state lock while every hook and appender ran, and all finished "
          "(the stop hooks refusing the stop mid-build, exit 2, as a stop hook does)",
          started and beh.returncode == 0 and all(c == 0 for c, _ in p_res + a_res)
          and all(c == 2 and "RATCHET" in e for c, e in s_res),
          (started, beh.returncode, b_err[-200:], [(c, e[-200:]) for c, e in p_res + a_res if c != 0],
           [c for c, _ in s_res]))
    check("[CP5-X46] every message typed during the race is in human_log.jsonl exactly once, every line intact",
          log_ok and all(said.count(t) == 1 for t in texts), (log_ok, said[-10:]))
    app_ok = []
    for t in targets:
        ok_, recs = cp5r8_jsonl_ok(t)
        app_ok.append(ok_ and [x.get("i") for x in recs] == list(range(n_lines)))
    st_ok, blocks = cp5r8_jsonl_ok(d / ".ratchet/runs/f/stop-blocks.jsonl")
    check("[CP5-X46] every concurrent append_recorded line landed, in order, and the stop hooks' own record file "
          "has only whole lines", all(app_ok) and st_ok, (app_ok, st_ok, blocks[-5:]))
    rec = cp5r8_hashes(env)
    files = [d / ".ratchet/human_log.jsonl", d / ".ratchet/runs/f/state.json", *targets]
    want = {str(f.resolve()): cp5r8_sha(f) for f in files}
    got = {k: (rec or {}).get(k) for k in want}
    check("[CP5-X46] file-hashes.json is whole and holds the current hash of every file written in the race: "
          "no writer dropped another's hash", rec is not None and got == want,
          [Path(k).name for k in want if got.get(k) != want[k]])
    leftovers = sorted(x.name for x in rh.iterdir() if x.name == "file-hashes.lock" or x.name.endswith(".tmp"))
    check("[CP5-X46] ... and neither file-hashes.lock nor a half-written record file is left behind",
          leftovers == [], leftovers)
    r_status = rx(d, env, "status")
    after = cp4_say_yes(d, tp, env, text="cp5r8 after the race")
    check("[CP5-X46] the chain verifies afterwards: a command reading state.json and a prompt hook checking "
          "human_log.jsonl both go on, with no tampering reported",
          r_status.returncode == 0 and after.returncode == 0 and "outside ratchet.py" not in
          r_status.stderr + after.stderr + after.stdout and cp4_human_log_lines(d)[-1].get("prompt")
          == "cp5r8 after the race", (r_status.stderr[-300:], after.stderr[-300:]))

    # --- X47: the hash record's lock is really honoured, and a stale one is cleared ----------------------------
    lock = rh / "file-hashes.lock"
    lock.write_text("")
    t0 = time.monotonic()
    p = cp5r8_prompt_proc(d, env, tp, "cp5r8 held")
    time.sleep(2.0)  # the lock waits up to 5 seconds before going on without it
    waited = p.poll() is None and "cp5r8 held" not in [x.get("prompt") for x in cp4_human_log_lines(d)]
    lock.unlink()
    rc = p.wait(timeout=60)
    took = time.monotonic() - t0
    hl = d / ".ratchet/human_log.jsonl"
    check("[CP5-X47] while file-hashes.lock is held, the prompt hook waits and appends nothing; once it is "
          "released the message is recorded with its hash",
          waited and rc == 0 and took >= 2.0 and cp4_human_log_lines(d)[-1].get("prompt") == "cp5r8 held"
          and (cp5r8_hashes(env) or {}).get(str(hl.resolve())) == cp5r8_sha(hl), (waited, rc, took))
    lock.write_text("")
    aged = time.time() - 120
    os.utime(lock, (aged, aged))
    t0 = time.monotonic()
    r = cp4_say_yes(d, tp, env, text="cp5r8 stale")
    took = time.monotonic() - t0
    check("[CP5-X47] a file-hashes.lock left by a process that died (older than 30 seconds) is cleared at once",
          r.returncode == 0 and took < 4.5 and not lock.exists()
          and cp4_human_log_lines(d)[-1].get("prompt") == "cp5r8 stale"
          and (cp5r8_hashes(env) or {}).get(str(hl.resolve())) == cp5r8_sha(hl), (r.stderr[-300:], took))


SECTIONS.append(("CP5", cp5_adv_round8_init_lock_and_hash_record_lock))



# ---- CP5 adversarial round 9 ----
# The per-user engine-call-sessions.json (which chat ran a real `init` / `ask`) is read-modify-written by every guard
# hook and by the `init` / `ask` that use a note up. Its own lock, engine-call-sessions.lock, keeps every note when
# several write at once; when the lock can't be had, nothing is recorded and the no-note owner rules apply.

_CP5R9_NOTER = """
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5r9", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
root, start, w, n, mode = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], int(sys.argv[5]), sys.argv[6]
(start.parent / ("ready-%s" % w)).write_text("1")  # the engine is loaded: this worker now only waits on start
while not start.exists():
    time.sleep(0.002)
got = []
for i in range(n):
    if mode == "note":
        m._note_engine_call(root, "$RATCHET ask choice --tag w%s-%d" % (w, i), "s-w%s-%d" % (w, i))
    else:
        m._ENGINE_ARGV = ["ask", "choice", "--tag", "w%s-%d" % (w, i)]
        got.append(m._noted_session(root, "ask"))
print(json.dumps(got))
"""


def cp5r9_race(script, root, env, workers, n, mode):
    start = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r9-s-")) / "start"
    procs = [subprocess.Popen([sys.executable, str(script), str(ENGINE), str(root), str(start), str(w), str(n), mode],
                              cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for w in range(workers)]
    ready = cp5v_until(lambda: all((start.parent / f"ready-{w}").exists() for w in range(workers)), timeout=120)
    assert ready, "not every race worker loaded the engine"
    start.write_text("go")
    out = []
    for p in procs:
        o, e = p.communicate(timeout=120)
        out.append((p.returncode, o, e))
    return out


def cp5r9_guard_proc(d, env, command, session):
    payload = {"session_id": session, "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    p = subprocess.Popen([sys.executable, str(ENGINE), "hook", "guard", "--host", "claude"], cwd=d, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    return p


def cp5_adv_round9_engine_notes_lock():
    print("CP5 adv round 9: the engine-call notes have their own lock; concurrent guard hooks never drop a note")
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r9-"))
    script = tmp / "noter.py"
    script.write_text(_CP5R9_NOTER)

    # --- X48: many guard-hook writers note calls at once; every note survives and is used once by its own call --
    env = make_env("claude")
    d = cp5y_repo()
    rh = Path(env["RATCHET_HOME"])
    workers, n = 6, 15
    res = cp5r9_race(script, d, env, workers, n, "note")
    key = f"{d.resolve()}|ask"
    notes = cp5z_notes(env).get(key) or []
    want = {(f"s-w{w}-{i}", ("ask", "choice", "--tag", f"w{w}-{i}")) for w in range(workers) for i in range(n)}
    got = [(x.get("session"), tuple(x.get("argv") or ())) for x in notes if isinstance(x, dict)]
    check("[CP5-X48] six processes noting 15 engine calls each at once: all 90 notes are in "
          "engine-call-sessions.json, each exactly once, the file whole",
          all(r[0] == 0 and "Traceback" not in r[2] for r in res) and len(got) == len(want)
          and set(got) == want, (len(got), len(want), [r[2][-200:] for r in res if r[2]]))
    leftovers = sorted(x.name for x in rh.iterdir() if x.name == "engine-call-sessions.lock" or x.name.endswith(".tmp"))
    check("[CP5-X48] ... and neither engine-call-sessions.lock nor a half-written notes file is left behind",
          leftovers == [], leftovers)
    res = cp5r9_race(script, d, env, workers, n, "use")
    outs = []
    for r in res:
        try:
            outs.append(json.loads(r[1].strip().splitlines()[-1]))
        except (ValueError, IndexError):
            outs.append(None)
    check("[CP5-X48] six processes using those notes up at once: each call gets its own chat, none gets nothing",
          outs == [[f"s-w{w}-{i}" for i in range(n)] for w in range(workers)],
          [(o or [])[:3] for o in outs])
    check("[CP5-X48] ... and every note is gone afterwards: no consumer wrote back a note another had used up",
          cp5z_project_notes(d, env) == {}, {k: len(v) for k, v in cp5z_project_notes(d, env).items()})

    # --- X49: real guard hooks in three first-run projects fire at once; each real `ask` binds its own chat -----
    env = make_env("claude")
    projects = [cp5y_first_run_plan(env)[0] for _ in range(3)]
    procs = [cp5r9_guard_proc(pd, env, f'cd "{pd}" && $RATCHET ask approve-plan', f"s-P{i}")
             for i, pd in enumerate(projects)]
    g_res = [(p.wait(timeout=60), p.stdout.read(), p.stderr.read()) for p in procs]
    check("[CP5-X49] three guard hooks noting real `ask approve-plan` calls at once all allow the call",
          all(c == 0 and "deny" not in o for c, o, _ in g_res), [(c, o[-200:], e[-200:]) for c, o, e in g_res])
    noted = {i: cp5z_project_notes(pd, env) for i, pd in enumerate(projects)}
    check("[CP5-X49] ... and all three notes survive, each naming its own chat",
          all([x.get("session") for x in (noted[i].get(f"{pd.resolve()}|ask") or [])] == [f"s-P{i}"]
              for i, pd in enumerate(projects)), noted)
    owners = []
    for i, pd in enumerate(projects):
        r = rx(pd, env, "ask", "approve-plan")
        st = cp3_state(pd)
        owners.append((r.returncode, st.get("owner_session"), (st.get("pending") or {}).get("session")))
    check("[CP5-X49] each project's real `ask approve-plan` then makes its own chat the owner and the asker",
          owners == [(0, f"s-P{i}", f"s-P{i}") for i in range(3)], owners)
    check("[CP5-X49] ... using each note up", all(cp5z_project_notes(pd, env) == {} for pd in projects),
          [cp5z_project_notes(pd, env) for pd in projects])

    # --- X50: the lock fails safe: held, nothing is noted or used up; a stale one is cleared ------------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp5y_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    lock = rh / "engine-call-sessions.lock"
    lock.write_text("")
    t0 = time.monotonic()
    r = cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-B")
    took = time.monotonic() - t0
    check("[CP5-X50] while engine-call-sessions.lock is held, the guard waits briefly, allows the call and notes "
          "nothing", r.returncode == 0 and "deny" not in r.stdout and took >= 1.5
          and cp5z_project_notes(d, env) == {}, (r.returncode, r.stdout[-200:], took, cp5z_project_notes(d, env)))
    lock.unlink()
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-B")
    had = cp5z_project_notes(d, env)
    lock.write_text("")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X50] with the lock held, `ask` uses no note and changes none: no chat is made owner or asker, the "
          "no-note rules apply, and the note is left as it was",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None
          and had and cp5z_project_notes(d, env) == had,
          (r.returncode, r.stderr[-300:], st.get("owner_session"), had, cp5z_project_notes(d, env)))
    lock.unlink()
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp5y_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    lock = rh / "engine-call-sessions.lock"
    lock.write_text("")
    aged = time.time() - 120
    os.utime(lock, (aged, aged))
    t0 = time.monotonic()
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-C")
    took = time.monotonic() - t0
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X50] an engine-call-sessions.lock left by a process that died (older than 30 seconds) is cleared at "
          "once: the note is taken and the real `ask` binds its chat",
          not lock.exists() and r.returncode == 0 and cp3_state(d).get("owner_session") == "s-C",
          (took, lock.exists(), r.stderr[-300:], cp3_state(d).get("owner_session")))


SECTIONS.append(("CP5", cp5_adv_round9_engine_notes_lock))



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

def cp5r12_director(home_dir, denv, lane, *args):
    cp4x_guard_tool(home_dir, denv, "Bash", {"command": f"python3 {ENGINE} --root {lane} {' '.join(args)}"},
                    session="s-director")
    return rx(home_dir, denv, "--root", str(lane), *args)


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


# ---- CP5 adversarial round 13 ----
# Every read-modify-write of a shared record (every record in RATCHET_HOME, and the project records that hooks or
# commands outside the state lock rewrite) goes through one helper, update_record, under a _BreakerLock-based lock
# for that file, so two writers never drop each other's entry. Each file keeps its own damaged-record rule.

_CP5R13_SHARED_PATHS = {"_markers_path", "_enrolled_path", "_director_folders_path", "_stop_errors_path",
                        "_chain_path", "_engine_notes_path", "_status_path", "_unavailable_path", "_heartbeat_path",
                        "_hook_env_path"}
_CP5R13_SHARED_NAMES = {"active-runs.json", "enrolled.json", "director-folders.json", "stop-errors.json",
                        "file-hashes.json", "engine-call-sessions.json", "provider-status.json",
                        "unavailable-reviewers.json", "hook-heartbeat.json", "hook-env.json"}
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
    ("cmd_providers", "probe_provider"):
        "--check/--discover only report the result, and probe_provider's own stderr note says when it wasn't saved",
    ("cmd_reviewers", "probe_provider"):
        "picks reviewers from what was saved; probe_provider's own stderr note says when a result wasn't saved",
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
