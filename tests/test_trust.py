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
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

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
