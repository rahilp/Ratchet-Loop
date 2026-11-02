#!/usr/bin/env python3
"""
Codex hook messages: what `ratchet.py hook stop|guard|prompt` does with the messages Codex really sends (turn_id,
stop_hook_active, last_assistant_message, tool_input, a command given as a list, a bare JSON list) and how it tells
Codex apart from the other CLIs.

    python3 tests/test_hook_codex_payloads.py      # builds throwaway projects with an active run

Every case runs the real `ratchet.py hook <event> --host auto|codex` as a subprocess. Which CLI Ratchet decided it
was talking to is read from what the hook records (the host in .ratchet/hook-heartbeat.json, and the host field on
.ratchet/human_log.jsonl lines), never by loading ratchet.py, so this file needs no test-inventory entry.

Message fields follow the Codex docs (https://learn.chatgpt.com/docs/hooks): session_id, turn_id, cwd,
hook_event_name, model, permission_mode, transcript_path, stop_hook_active, last_assistant_message, tool_name,
tool_input, tool_use_id; Stop and the other events send one JSON object on stdin.

Each case id appears in its check names as [CP2-Txx] (extra cases: [CP2-Xnn]).

The regression half of CP2-T16 (the older hook suites still pass) is enforced by tests/run_all.py, which runs
test_hooks.py, test_hook_list_payload.py and test_hooks_status.py on their own, and by `ratchet.py complete`; it is
not repeated here (re-running them inside this file cost about 95 seconds). T16 only checks they exist and that
run_all.py would pick them up.
"""
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text
import json
import os
import re
import subprocess
import sys
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = README_DOCS
THIS_FILE = Path(__file__).resolve()
NEW_TEST_LINE = "python3 tests/test_hook_codex_payloads.py"
PASS, FAIL = 0, 0

# Markers of other CLIs, and of Codex, removed so detection works from the message alone.
_STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "GEMINI_CLI", "COPILOT_CLI",
              "GITHUB_COPILOT_CLI", "RATCHET_HOST_PROVIDER", "RATCHET_ROOT", "RATCHET_ROLE", "RATCHET_HOME", "RATCHET_HUMAN_OVERRIDE", "RATCHET_NESTED_REVIEW")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {str(detail)[:400]}")


# ---------------------------------------------------------------------------------------------- the throwaway project

def child_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_") and k not in _STRIP_ENV}
    env["RATCHET_HOME"] = str(home)
    return env


class Proj:
    def __init__(self, path, home):
        self.path, self.home = path, home
        self.cwd = str(path)
        self.state = str(path / ".ratchet/runs/hk/state.json")
        self.human = str(path / ".ratchet/human_log.jsonl")
        self.heartbeat = path / ".ratchet" / "hook-heartbeat.json"
        self.log = path / ".ratchet" / "human_log.jsonl"

    def sh(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(ENGINE), *args], cwd=self.path, input=stdin, capture_output=True,
                              text=True, env=child_env(self.home))

    def run(self, event, host, text):
        """Send `text` on stdin to `hook <event> --host <host>`: (exit code, stdout, stderr, host the hook recorded).
        The old heartbeat is deleted first, so a host read back is the one this very call recorded (None: none)."""
        try:
            self.heartbeat.unlink()
        except OSError:
            pass
        r = self.sh("hook", event, "--host", host, stdin=text)
        return r.returncode, r.stdout, r.stderr, self.beat_host(event)

    def hook(self, event, host, payload):
        return self.run(event, host, json.dumps(payload))

    def beat_host(self, event):
        try:
            hb = json.loads(self.heartbeat.read_text())
            entry = hb.get(event)
            return entry.get("host") if isinstance(entry, dict) else None
        except (OSError, ValueError, AttributeError):
            return None

    def log_lines(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines() if x.strip()]

    # ---- Codex-shaped messages (the fields the Codex docs name)
    def stop_msg(self, **kw):
        m = {"session_id": "s", "turn_id": "t1", "cwd": self.cwd, "hook_event_name": "Stop", "model": "gpt-5-codex",
             "permission_mode": "default", "transcript_path": None, "stop_hook_active": False,
             "last_assistant_message": "All done."}
        m.update(kw)
        return m

    def tool_msg(self, tool_name, tool_input, **kw):
        m = {"session_id": "s", "turn_id": "t1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
             "model": "gpt-5-codex", "permission_mode": "default", "transcript_path": None, "tool_name": tool_name,
             "tool_input": tool_input, "tool_use_id": "call_1"}
        m.update(kw)
        return m

    def prompt_msg(self, prompt, **kw):
        m = {"session_id": "s", "turn_id": "t1", "cwd": self.cwd, "hook_event_name": "UserPromptSubmit",
             "model": "gpt-5-codex", "permission_mode": "default", "transcript_path": None, "prompt": prompt}
        m.update(kw)
        return m


def git_project():
    d = Path(_tmp.mkdtemp(prefix="ratchet-codex-payloads-"))
    home = Path(_tmp.mkdtemp(prefix="ratchet-codex-payloads-home-"))
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(c, cwd=d, check=True)
    (d / "a.js").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    return Proj(d, home)


def project_a():
    """Project A: an active run, plan approved, CP1 still open (the same setup as tests/test_hooks.py)."""
    import datetime as dt
    p = git_project()
    d = p.path
    p.sh("init", "codex payloads", "--slug", "hk")
    run = d / ".ratchet" / "runs" / "hk"
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    p.sh("plan-submit")
    p.sh("ask", "approve-plan")
    tp = d.parent / f"{d.name}-transcript.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "approved"},
                              "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    p.sh("hook", "prompt", "--host", "claude",
         stdin=json.dumps({"cwd": str(d), "prompt": "approved", "transcript_path": str(tp)}))
    r = p.sh("approve-plan")
    assert r.returncode == 0, r.stderr
    return p


def project_b():
    """Project B: a .ratchet/ folder is there (Ratchet was set up here) but no run is active, so a stop has nothing
    to block."""
    p = git_project()
    (p.path / ".ratchet").mkdir()
    return p


def project_c():
    """Project C: a git repo with no .ratchet/ at all."""
    return git_project()


# ---------------------------------------------------------------------------------- the reply checkers (with teeth)

def quiet(rc, out, err):
    return rc == 0 and out == "" and err == ""


def visible_block(rc, out, err):
    return rc == 2 and out == "" and err.strip() != ""


def no_crash(out, err):
    blob = out + err
    return "Traceback" not in blob and "AttributeError" not in blob


# ------------------------------------------------------------------------------------------------------- the cases

def case_t01():
    print("[CP2-T01] a full Codex stop message is read as Codex and answered in Codex's way")
    p = project_a()
    rc, out, err, host = p.hook("stop", "auto", p.stop_msg())
    check("[CP2-T01] the hook recorded host 'codex'", host == "codex", host)
    check("[CP2-T01] exit 2, stdout '', stderr non-blank with RATCHET GATE", visible_block(rc, out, err)
          and "RATCHET GATE" in err, f"rc={rc} out={out!r} err={err[:200]!r}")
    check("[CP2-T01] no Traceback anywhere", no_crash(out, err), err[:300])


def case_t02():
    print("[CP2-T02] a Codex stop message with nothing to block ends quietly")
    p = project_b()
    rc, out, err, host = p.hook("stop", "auto", p.stop_msg())
    check("[CP2-T02] exit 0, stdout '' and stderr '' (a quiet no-op)", quiet(rc, out, err),
          f"rc={rc} out={out!r} err={err!r}")
    check("[CP2-T02] setup: Ratchet is set up here but no run is active",
          (p.path / ".ratchet").is_dir() and not (p.path / ".ratchet" / "active.json").exists())


def case_t03():
    print("[CP2-T03] a Codex stop sent again after a refused stop (stop_hook_active true) is handled")
    p = project_a()
    rc, out, err, host = p.hook("stop", "auto", p.stop_msg(stop_hook_active=True, last_assistant_message="Still working"))
    check("[CP2-T03] recorded host is 'codex'", host == "codex", host)
    check("[CP2-T03] no Traceback", no_crash(out, err), err[:300])
    check("[CP2-T03] exit code is 0 or 2; if 2, stderr is non-blank; stdout empty either way",
          rc in (0, 2) and out == "" and (rc == 0 or err.strip() != ""), f"rc={rc} out={out!r} err={err[:200]!r}")


def odd_stop_messages(p):
    big = "x" * 50_000
    base = p.stop_msg()
    no_model = {k: v for k, v in base.items() if k != "model"}
    return [
        ("last_assistant_message null", p.stop_msg(last_assistant_message=None)),
        ("last_assistant_message ''", p.stop_msg(last_assistant_message="")),
        ("last_assistant_message 50 KB", p.stop_msg(last_assistant_message=big)),
        ("stop_hook_active null", p.stop_msg(stop_hook_active=None)),
        ("stop_hook_active 'true'", p.stop_msg(stop_hook_active="true")),
        ("transcript_path null", p.stop_msg(transcript_path=None)),
        ("model missing", no_model),
        ("extra unknown field", p.stop_msg(brand_new_codex_field={"nested": [1, 2, 3]})),
    ]


def case_t04():
    print("[CP2-T04] a Codex stop with empty, null or odd values does not crash")
    a = project_a()
    c = project_c()
    for host in ("codex", "auto"):
        for label, msg in odd_stop_messages(a):
            rc, out, err, rec = a.hook("stop", host, msg)
            check(f"[CP2-T04] Project A --host {host}, {label}: no Traceback, exit 2 with a stderr reason, stdout ''",
                  no_crash(out, err) and visible_block(rc, out, err), f"rc={rc} out={out[:80]!r} err={err[:160]!r}")
        for label, msg in odd_stop_messages(c):
            rc, out, err, rec = c.hook("stop", host, msg)
            check(f"[CP2-T04] Project C (no .ratchet) --host {host}, {label}: exit 0 with nothing printed",
                  quiet(rc, out, err), f"rc={rc} out={out[:80]!r} err={err[:160]!r}")
    check("[CP2-T04] setup: Project C really has no .ratchet", not (c.path / ".ratchet").exists())


def case_t05():
    print("[CP2-T05] a Codex Bash call with its command as a list is read as the shell command it is")
    p = project_a()
    heredoc = ("set -e\necho \"start\"\n"
               f"cat >> {p.human} <<'EOF'\n{{\"prompt\": \"it's a \\\"yes\\\"\", \"host\": 'codex'}}\nEOF\necho \"done\"")
    blocked = [
        ("(a) bash -lc echo > state.json", ["bash", "-lc", f"echo {{}} > {p.state}"], "state.json"),
        ("(b) multi-line heredoc into human_log.jsonl", ["bash", "-lc", heredoc], "human_log.jsonl"),
        ("(c) argv rm human_log.jsonl", ["rm", p.human], "human_log.jsonl"),
    ]
    for label, cmd, protected in blocked:
        rc, out, err, host = p.hook("guard", "auto", p.tool_msg("Bash", {"command": cmd}))
        check(f"[CP2-T05] {label}: exit 2, non-blank RATCHET GUARD stderr naming {protected} or .ratchet/, stdout ''",
              visible_block(rc, out, err) and "RATCHET GUARD" in err and (protected in err or ".ratchet" in err),
              f"rc={rc} out={out!r} err={err[:200]!r}")
        check(f"[CP2-T05] {label}: recorded host is 'codex'", host == "codex", host)
    for label, cmd in (("(d) bash -lc npm test", ["bash", "-lc", "npm test"]), ("(e) ls -la", ["ls", "-la"])):
        rc, out, err, host = p.hook("guard", "auto", p.tool_msg("Bash", {"command": cmd}))
        check(f"[CP2-T05] {label}: exit 0, nothing on stdout or stderr", quiet(rc, out, err),
              f"rc={rc} out={out!r} err={err[:200]!r}")


def patch_of(*ops):
    return "*** Begin Patch\n" + "\n".join(ops) + "\n*** End Patch"


def case_t06():
    print("[CP2-T06] a Codex apply_patch call is read for every file it touches")
    p = project_a()
    cases = [
        ("(a) adds a file under evidence/", patch_of("*** Add File: .ratchet/runs/hk/evidence/new.json", "+{}")),
        ("(b) moves a project file onto state.json",
         patch_of("*** Update File: src/app.js", "*** Move to: .ratchet/runs/hk/state.json", "@@", "-x", "+y")),
        ("(c) deletes human_log.jsonl", patch_of("*** Delete File: .ratchet/human_log.jsonl")),
    ]
    for label, patch in cases:
        rc, out, err, host = p.hook("guard", "auto", p.tool_msg("apply_patch", {"input": patch}))
        check(f"[CP2-T06] {label}: exit 2, non-blank stderr, stdout ''", visible_block(rc, out, err),
              f"rc={rc} out={out!r} err={err[:200]!r}")
    rc, out, err, host = p.hook("guard", "auto", p.tool_msg("apply_patch", {
        "input": patch_of("*** Update File: src/app.js", "@@", "-x", "+y")}))
    check("[CP2-T06] (d) only updates src/app.js: exit 0, nothing printed", quiet(rc, out, err),
          f"rc={rc} out={out!r} err={err[:200]!r}")


def case_t07():
    print("[CP2-T07] a Codex tool message with a strange tool_input never crashes or blocks real work")
    p = project_a()
    shapes = [
        ("tool_input is a list", p.tool_msg("Bash", ["rm", "x"])),
        ("tool_input null", p.tool_msg("Bash", None)),
        ("tool_input a bare string", p.tool_msg("Bash", "just words")),
        ("tool_input a number", p.tool_msg("Bash", 42)),
        ("tool_input {}", p.tool_msg("Bash", {})),
        ("tool_input missing", {k: v for k, v in p.tool_msg("Bash", {}).items() if k != "tool_input"}),
        ("command is an empty list", p.tool_msg("Bash", {"command": []})),
        ("command is a list of numbers", p.tool_msg("Bash", {"command": [1, 2, 3]})),
        ("tool_name missing", {k: v for k, v in p.tool_msg("Bash", {"command": ["ls"]}).items() if k != "tool_name"}),
    ]
    for label, msg in shapes:
        rc, out, err, host = p.hook("guard", "auto", msg)
        check(f"[CP2-T07] {label}: no Traceback, exit 0, nothing blocked (stdout '', no RATCHET GUARD)",
              no_crash(out, err) and rc == 0 and out == "" and "RATCHET GUARD" not in err,
              f"rc={rc} out={out[:80]!r} err={err[:200]!r}")


LIST_TEXTS = ['["a","b"]', '[{"turn_id":"t"}]', "[]"]


def case_t08():
    print("[CP2-T08] a bare JSON list sent instead of a message does not crash any Codex hook")
    a = project_a()
    c = project_c()
    for host in ("codex", "auto"):
        empty = a.run("stop", host, "{}")[:3]
        for text in LIST_TEXTS:
            for label, p in (("A", a), ("C", c)):
                rc, out, err, _h = p.run("guard", host, text)
                check(f"[CP2-T08] guard --host {host} {text} in {label}: no Traceback, exit 0",
                      no_crash(out, err) and rc == 0, f"rc={rc} err={err[:200]!r}")
                before = len(p.log_lines())
                rc, out, err, beat = p.run("prompt", host, text)
                check(f"[CP2-T08] prompt --host {host} {text} in {label}: no Traceback, exit 0",
                      no_crash(out, err) and rc == 0, f"rc={rc} err={err[:200]!r}")
                if label == "A":  # C has no ratchet folder, so it keeps no heartbeat; a bare list has no CLI hint under auto
                    check(f"[CP2-T08] prompt --host {host} {text} in A: the hook still recorded a host"
                          + (" ('codex')" if host == "codex" else ""),
                          beat == "codex" if host == "codex" else beat is not None, beat)
                check(f"[CP2-T08] prompt --host {host} {text} in {label}: nothing logged to human_log.jsonl",
                      len(p.log_lines()) == before, f"{before} -> {len(p.log_lines())}")
                rc, out, err, _h = p.run("stop", host, text)
                if label == "A":
                    check(f"[CP2-T08] stop --host {host} {text} in A: same exit code and text as '{{}}'",
                          (rc, out, err) == empty, f"list={(rc, out, err[:120])!r} empty={empty[0], empty[1], empty[2][:120]!r}")
                    check(f"[CP2-T08] stop --host {host} {text} in A: no Traceback", no_crash(out, err), err[:300])
                else:
                    check(f"[CP2-T08] stop --host {host} {text} in C: no Traceback, exit 0, nothing printed",
                          no_crash(out, err) and quiet(rc, out, err), f"rc={rc} out={out!r} err={err[:200]!r}")
    check("[CP2-T08] teeth: the '{}' stop in A is a real block (exit 2), so the equality above compares real replies",
          empty[0] == 2 and "RATCHET GATE" in empty[2], f"rc={empty[0]}")


def case_t09():
    print("[CP2-T09] unreadable input is also safe on Codex")
    p = project_a()
    empty = p.run("stop", "codex", "{}")[:3]
    for text in ("", "{not json", "42", '"hello"', "null"):
        for ev in ("guard", "prompt"):
            rc, out, err, _h = p.run(ev, "codex", text)
            check(f"[CP2-T09] {ev} stdin {text!r}: no Traceback, exit 0, nothing blocked",
                  no_crash(out, err) and rc == 0 and out == "" and "RATCHET GUARD" not in err,
                  f"rc={rc} out={out[:80]!r} err={err[:200]!r}")
        rc, out, err, _h = p.run("stop", "codex", text)
        check(f"[CP2-T09] stop stdin {text!r}: no Traceback and the same reply as '{{}}'",
              no_crash(out, err) and (rc, out, err) == empty, f"got={(rc, out, err[:100])!r}")
    check("[CP2-T09] teeth: the '{}' stop is a real block (exit 2), so the equality compares real replies",
          empty[0] == 2 and "RATCHET GATE" in empty[2], f"rc={empty[0]}")


def case_t10():
    print("[CP2-T10] every Codex message shape is recorded as Codex, never as Gemini or Copilot")
    p = project_a()
    extra = {"model": "gpt-5-codex", "permission_mode": "default", "transcript_path": None, "session_id": "s"}
    rc, out, err, host = p.hook("stop", "auto", p.stop_msg(**extra))
    check("[CP2-T10] stop: heartbeat host is 'codex'", host == "codex", host)
    for label, msg in (
            ("Bash with a list command", p.tool_msg("Bash", {"command": ["bash", "-lc", "npm test"]}, **extra)),
            ("apply_patch", p.tool_msg("apply_patch", {"input": patch_of("*** Update File: a.js", "@@", "-x", "+y")}, **extra))):
        rc, out, err, host = p.hook("guard", "auto", msg)
        check(f"[CP2-T10] guard ({label}): heartbeat host is 'codex'", host == "codex", host)
    before = len(p.log_lines())
    rc, out, err, host = p.hook("prompt", "auto", p.prompt_msg("typed by a person in codex", **extra))
    check("[CP2-T10] prompt: heartbeat host is 'codex'", host == "codex", host)
    new = p.log_lines()[before:]
    check("[CP2-T10] prompt: one line appended to human_log.jsonl with host 'codex' and the typed text",
          len(new) == 1 and new[0].get("host") == "codex" and new[0].get("prompt") == "typed by a person in codex",
          new)
    check("[CP2-T10] none of the recorded hosts is gemini or copilot",
          all(h not in ("gemini", "copilot") for h in [new[0].get("host") if new else None, host]), new)


def case_t11():
    print("[CP2-T11] with --host codex given, the message is Codex whatever it holds")
    p = project_a()
    gem = {"session_id": "s", "cwd": p.cwd, "hook_event_name": "BeforeTool", "tool_name": "write_file",
           "tool_input": {"file_path": p.state, "content": "{}"}}
    rc, out, err, host = p.hook("guard", "codex", gem)
    check("[CP2-T11] (a) gemini-shaped guard: recorded host 'codex'", host == "codex", host)
    check("[CP2-T11] (a) gemini-shaped guard: exit 2, reason on stderr, stdout ''", visible_block(rc, out, err),
          f"rc={rc} out={out!r} err={err[:160]!r}")
    cop = {"sessionId": "s", "timestamp": 1, "cwd": p.cwd, "toolName": "edit",
           "toolArgs": json.dumps({"path": p.state, "old_str": "a", "new_str": "b"})}
    rc, out, err, host = p.hook("guard", "codex", cop)
    check("[CP2-T11] (b) copilot-shaped guard: recorded host 'codex'", host == "codex", host)
    check("[CP2-T11] (b) copilot-shaped guard: exit 2, reason on stderr, stdout '' (no permissionDecision JSON)",
          visible_block(rc, out, err) and "permissionDecision" not in out, f"rc={rc} out={out!r} err={err[:160]!r}")
    copstop = {"sessionId": "s", "timestamp": 1790000000000, "cwd": p.cwd, "transcriptPath": "/tmp/t",
               "stopReason": "end_turn", "stop_hook_active": False}
    rc, out, err, host = p.hook("stop", "codex", copstop)
    check("[CP2-T11] (b) copilot-shaped stop: recorded host 'codex'", host == "codex", host)
    check("[CP2-T11] (b) copilot-shaped stop: exit 2, reason on stderr, stdout '' (no decision block JSON)",
          visible_block(rc, out, err) and '"decision"' not in out, f"rc={rc} out={out!r} err={err[:160]!r}")
    claude = {"session_id": "s", "cwd": p.cwd, "hook_event_name": "Stop", "stop_hook_active": False}
    rc, out, err, host = p.hook("stop", "codex", claude)
    check("[CP2-T11] (c) claude-shaped stop without turn_id: recorded host 'codex'", host == "codex", host)
    check("[CP2-T11] (c) claude-shaped stop: exit 2 with a stderr reason", visible_block(rc, out, err),
          f"rc={rc} out={out!r} err={err[:160]!r}")
    claude_g = {"session_id": "s", "cwd": p.cwd, "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": p.state, "content": "{}"}}
    rc, out, err, host = p.hook("guard", "codex", claude_g)
    check("[CP2-T11] (c) claude-shaped guard: recorded host 'codex', exit 2 with a stderr reason",
          host == "codex" and visible_block(rc, out, err), f"host={host} rc={rc} out={out!r}")
    rc, out, err, host = p.hook("stop", "codex", {})
    check("[CP2-T11] (d) empty message stop: recorded host 'codex'", host == "codex", host)
    check("[CP2-T11] (d) empty message stop: exit 2 with a stderr reason", visible_block(rc, out, err),
          f"rc={rc} out={out!r} err={err[:160]!r}")
    rc, out, err, host = p.hook("guard", "codex", {})
    check("[CP2-T11] (d) empty message guard: recorded host 'codex' and nothing blocked", host == "codex" and quiet(rc, out, err),
          f"host={host} rc={rc} out={out!r} err={err[:100]!r}")


def case_t12():
    print("[CP2-T12] Codex-like snake_case fields without a turn_id are not guessed to be Gemini or Copilot")
    p = project_a()
    cases = (("prompt", "UserPromptSubmit", {"prompt": "hello"}),
             ("guard", "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": ["ls"]}}))
    for event, hook_name, extra in cases:
        msg = {"session_id": "s", "cwd": p.cwd, "hook_event_name": hook_name, **extra}
        rc, out, err, host = p.hook(event, "auto", msg)
        check(f"[CP2-T12] {hook_name} without turn_id: a host was recorded, and it is not gemini or copilot",
              host is not None and host not in ("gemini", "copilot"), host)
        check(f"[CP2-T12] {hook_name} without turn_id: it is not guessed to be codex either (that needs a turn_id)",
              host != "codex", host)
        rc, out, err, host = p.hook(event, "auto", {**msg, "turn_id": "t9"})
        check(f"[CP2-T12] {hook_name} with turn_id added: host becomes 'codex'", host == "codex", host)


def case_t13():
    """PINNED FINDING (2026-10-31): where Codex's prompt guidance goes today.

    Today the prompt hook's guidance for Codex is printed on STDERR with exit 0 (emit_hook: only Claude gets it on
    stdout). The Codex docs (https://learn.chatgpt.com/docs/hooks, UserPromptSubmit) say: plain text on stdout with
    exit 0 is added as developer context; exit 2 with stderr blocks the prompt. The docs alone do not say outright that stderr on exit 0 is ignored; the
    source does: codex-rs/hooks/src/events/user_prompt_submit.rs (parse_completed reads only stdout on exit 0). So
    Codex does not show the agent
    this guidance, and the claim 'Codex shows the agent Ratchet's guidance' in README's Codex row and in
    HOST_ENFORCEMENT['codex']['guidance'] is doubtful. It is reported, not changed here. This test pins today's
    behaviour so the director's later fix changes it on purpose (and then updates this test).
    """
    print("[CP2-T13] PINNED FINDING: Codex prompt guidance goes to stderr on exit 0 today")
    p = project_a()
    phase = json.loads(Path(p.state).read_text()).get("phase")
    check("[CP2-T13] setup: the run is in phase building (the prompt hook has guidance for 'stop')", phase == "building", phase)
    rc, out, err, host = p.hook("prompt", "auto", p.prompt_msg("stop, hold on"))
    check("[CP2-T13] recorded host is 'codex'", host == "codex", host)
    check("[CP2-T13] exit code 0", rc == 0, rc)
    check("[CP2-T13] stdout is empty (so Codex adds no developer context from it)", out == "", repr(out))
    check("[CP2-T13] stderr holds the '[ratchet] ...' guidance", err.startswith("[ratchet] ") and "pause" in err, repr(err[:200]))
    doc = case_t13.__doc__ or ""
    check("[CP2-T13] the docstring is labelled PINNED FINDING (2026-10-31) and quotes the Codex docs",
          "PINNED FINDING (2026-10-31)" in doc and "https://learn.chatgpt.com/docs/hooks" in doc
          and "developer context" in doc and "exit 2" in doc and "stderr blocks the prompt" in doc, doc[:100])


def case_t14():
    print("[CP2-T14] the pinned Codex guidance differs from Claude's, and the README Codex row is left alone")
    p = project_a()
    rc, out, err, host = p.hook("prompt", "auto", p.prompt_msg("stop, hold on"))
    claude = {"session_id": "s", "cwd": p.cwd, "hook_event_name": "UserPromptSubmit", "prompt": "stop, hold on"}
    crc, cout, cerr, chost = p.hook("prompt", "auto", claude)
    check("[CP2-T14] the Claude prompt in the same state is read as claude", chost == "claude", chost)
    check("[CP2-T14] Claude's guidance is on stdout with exit 0 and stderr ''",
          crc == 0 and cout.startswith("[ratchet] ") and cerr == "", f"rc={crc} out={cout[:80]!r} err={cerr[:80]!r}")
    check("[CP2-T14] so Codex's stderr-only result is a real difference, not a quirk of the setup",
          host == "codex" and out == "" and err.startswith("[ratchet] ") and cout.strip() == err.strip(),
          f"codex out={out!r} err={err[:60]!r}")
    # a Codex block is exit 2 with its reason on stderr (a prompt hook never blocks today, so the tool guard stands in)
    rc, out, err, host = p.hook("guard", "auto", p.tool_msg("Bash", {"command": ["bash", "-lc", f"rm {p.human}"]}))
    check("[CP2-T14] a Codex block still exits 2 with its reason on stderr and nothing on stdout",
          host == "codex" and visible_block(rc, out, err), f"rc={rc} out={out!r} err={err[:100]!r}")
    row = next((ln for ln in README.read_text(encoding="utf-8").splitlines() if ln.startswith("| Codex |")), "")
    check("[CP2-T14] the README Codex row still says Ratchet 'shows Ratchet's guidance back to the agent'",
          "shows Ratchet's guidance back to the agent" in row, row[:200])


def case_t15():
    print("[CP2-T15] Claude Code, Gemini and Copilot messages are still read and answered as before")
    p = project_a()
    state, human = p.state, p.human
    cwd = p.cwd
    # stops
    rc, out, err, host = p.hook("stop", "auto", {"session_id": "s", "cwd": cwd, "hook_event_name": "Stop", "stop_hook_active": False})
    check("[CP2-T15] claude stop: host claude, exit 2, stderr reason, stdout ''",
          host == "claude" and visible_block(rc, out, err) and "RATCHET GATE" in err, f"host={host} rc={rc} out={out!r}")
    rc, out, err, host = p.hook("stop", "auto", {"session_id": "s", "cwd": cwd, "hook_event_name": "AfterAgent", "prompt": "go",
                                                 "prompt_response": "done!", "stop_hook_active": False,
                                                 "timestamp": "2026-09-28T00:00:00Z"})
    check("[CP2-T15] gemini stop (AfterAgent): host gemini, exit 2, stderr reason, stdout ''",
          host == "gemini" and visible_block(rc, out, err) and "RATCHET GATE" in err, f"host={host} rc={rc} out={out!r}")
    rc, out, err, host = p.hook("stop", "auto", {"sessionId": "s", "timestamp": 1790000000000, "cwd": cwd,
                                                 "transcriptPath": "/tmp/t", "stopReason": "end_turn",
                                                 "stop_hook_active": False})
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = {}
    check("[CP2-T15] copilot stop (agentStop): host copilot, exit 0, stdout is a decision block JSON, stderr ''",
          host == "copilot" and rc == 0 and err == "" and parsed.get("decision") == "block"
          and "RATCHET GATE" in str(parsed.get("reason")), f"host={host} rc={rc} out={out[:100]!r}")
    # guards
    rc, out, err, host = p.hook("guard", "auto", {"session_id": "s", "cwd": cwd, "hook_event_name": "PreToolUse",
                                                  "tool_name": "Write", "tool_input": {"file_path": state, "content": "{}"}})
    check("[CP2-T15] claude guard: host claude, exit 2 with a stderr reason", host == "claude" and visible_block(rc, out, err),
          f"host={host} rc={rc}")
    rc, out, err, host = p.hook("guard", "auto", {"session_id": "s", "cwd": cwd, "hook_event_name": "BeforeTool",
                                                  "tool_name": "run_shell_command", "tool_input": {"command": f"rm {human}"}})
    check("[CP2-T15] gemini guard (BeforeTool): host gemini, exit 2 with a stderr reason",
          host == "gemini" and visible_block(rc, out, err), f"host={host} rc={rc}")
    rc, out, err, host = p.hook("guard", "auto", {"sessionId": "s", "timestamp": 1, "cwd": cwd, "toolName": "edit",
                                                  "toolArgs": json.dumps({"path": state, "old_str": "a", "new_str": "b"})})
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = {}
    check("[CP2-T15] copilot guard (preToolUse): host copilot, exit 0, stdout permissionDecision deny",
          host == "copilot" and rc == 0 and parsed.get("permissionDecision") == "deny", f"host={host} rc={rc} out={out[:100]!r}")
    # prompts: the host recorded on the human_log line
    for label, msg, want in (
            ("claude", {"session_id": "s", "cwd": cwd, "hook_event_name": "UserPromptSubmit", "prompt": "hi from claude"}, "claude"),
            ("gemini (BeforeAgent)", {"session_id": "s", "cwd": cwd, "hook_event_name": "BeforeAgent", "prompt": "hi from gemini"}, "gemini"),
            ("copilot (userPromptSubmitted)", {"sessionId": "s", "timestamp": 1, "cwd": cwd, "prompt": "hi from copilot"}, "copilot")):
        before = len(p.log_lines())
        rc, out, err, host = p.hook("prompt", "auto", msg)
        new = p.log_lines()[before:]
        check(f"[CP2-T15] {label} prompt: heartbeat host and the human_log line host are '{want}'",
              host == want and len(new) == 1 and new[0].get("host") == want, f"host={host} new={new}")
        check(f"[CP2-T15] {label} prompt: exit 0", rc == 0, rc)


def case_t16():
    print("[CP2-T16] the existing hook test suites are present and run_all.py picks them up")
    tests_dir = ROOT / "tests"
    found = sorted(p.name for p in tests_dir.glob("test_*.py") if p.is_file())  # run_all.py: folder.glob("test_*.py")
    runner = (tests_dir / "run_all.py").read_text(encoding="utf-8") if (tests_dir / "run_all.py").is_file() else ""
    check("[CP2-T16] run_all.py discovers suites with folder.glob(\"test_*.py\")", 'folder.glob("test_*.py")' in runner)
    for name in ("test_hooks.py", "test_hook_list_payload.py", "test_hooks_status.py"):
        check(f"[CP2-T16] {name} exists and is found by run_all.py's test_*.py discovery",
              (tests_dir / name).is_file() and name in found, name)
    print("    NOT RUN here: the old hook suites and the full suite (python3 tests/run_all.py) are proved by run_all.py "
          "and `ratchet.py complete`; running them from inside a tests/test_*.py file would repeat them")


FORBIDDEN_PARTS = (("spec_from", "_file_location"), ("ENGINE.", "read_text"), ("ast.", "parse"), ("ast.", "walk"),
                   ("get", "source"))


def inventory_hits(text):
    return [a + b for a, b in FORBIDDEN_PARTS if a + b in text]


def inversions(items):
    return [(a, b) for a, b in zip(items, items[1:]) if a > b]


def readme_test_block():
    text = README.read_text(encoding="utf-8")
    if "### Running the tests" not in text:
        return []
    after = text.split("### Running the tests", 1)[1].splitlines()
    block, fenced = [], False
    for ln in after:
        if ln.startswith("```"):
            fenced = not fenced
        elif not fenced and ln.startswith("#"):
            break
        block.append(ln)
    return block


def case_t17():
    print("[CP2-T17] the new test file is listed in the README and needs no inventory entry")
    block = readme_test_block()
    lst = [ln for ln in block if ln.startswith("python3 tests/")]
    check("[CP2-T17] the README 'Running the tests' list holds the exact line exactly once",
          block.count(NEW_TEST_LINE) == 1, block.count(NEW_TEST_LINE))
    names = ("its previous neighbour in the list sorts before it", "its next neighbour in the list sorts after it",
             "it sits after test_hook_codex_contract.py and before test_hook_list_payload.py",
             "adding the line adds no out-of-order pair to the list")
    if NEW_TEST_LINE in lst:
        i = lst.index(NEW_TEST_LINE)
        ctx = lst[max(0, i - 1):i + 2]
        check(f"[CP2-T17] {names[0]}", i == 0 or lst[i - 1] < NEW_TEST_LINE, ctx)
        check(f"[CP2-T17] {names[1]}", i + 1 >= len(lst) or lst[i + 1] > NEW_TEST_LINE, ctx)
        before, after = "python3 tests/test_hook_codex_contract.py", "python3 tests/test_hook_list_payload.py"
        check(f"[CP2-T17] {names[2]}",
              before in lst and after in lst and lst.index(before) < i < lst.index(after), lst[max(0, i - 2):i + 3])
        without = [x for x in lst if x != NEW_TEST_LINE]
        # older out-of-order pairs may exist; adding the line must not add one
        check(f"[CP2-T17] {names[3]}", inversions(lst) == inversions(without), inversions(lst))
    else:
        for n in names:
            check(f"[CP2-T17] {n}", False, "line missing")
    text = THIS_FILE.read_text(encoding="utf-8")
    check("[CP2-T17] this file holds none of the strings the test inventory looks for", inventory_hits(text) == [],
          inventory_hits(text))
    check("[CP2-T17] teeth: the inventory-string finder does spot them in a sample that loads ratchet.py",
          inventory_hits("import importlib.util\nimportlib.util.spec_from" + "_file_location('x', p)") != [])
    check("[CP2-T17] the file is a tests/test_*.py script that run_all.py picks up",
          THIS_FILE in sorted(p.resolve() for p in (ROOT / "tests").glob("test_*.py") if p.is_file())
          and 'folder.glob("test_*.py")' in (ROOT / "tests" / "run_all.py").read_text(encoding="utf-8"))


def case_x01():
    print("[CP2-X01] an empty, whitespace-only or non-string prompt is not logged, but the hook still records its host")
    p = project_a()
    for label, prompt in (("whitespace-only '   '", "   "), ("empty ''", ""), ("a list ['x']", ["x"]),
                          ("a dict {'a': 1}", {"a": 1}), ("a number 7", 7), ("null", None)):
        before = len(p.log_lines())
        rc, out, err, host = p.hook("prompt", "auto", p.prompt_msg(prompt))
        check(f"[CP2-X01] prompt {label}: no Traceback, exit 0, nothing blocked",
              no_crash(out, err) and rc == 0 and "RATCHET GUARD" not in err, f"rc={rc} out={out[:80]!r} err={err[:160]!r}")
        check(f"[CP2-X01] prompt {label}: nothing logged to human_log.jsonl", len(p.log_lines()) == before,
              f"{before} -> {len(p.log_lines())}")
        check(f"[CP2-X01] prompt {label}: the heartbeat still recorded host 'codex'", host == "codex", host)
    before = len(p.log_lines())
    rc, out, err, host = p.hook("prompt", "auto", p.prompt_msg("a real typed reply"))
    new = p.log_lines()[before:]
    check("[CP2-X01] teeth: a real prompt in the same project is logged (one line, host 'codex')",
          host == "codex" and len(new) == 1 and new[0].get("prompt") == "a real typed reply", new)


def guarded(fn, *args):
    """A case that crashes is a failed check, not a crashed script, so every other case still runs."""
    global FAIL
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        FAIL += 1
        print(f"  ✗ {fn.__name__} crashed: {type(e).__name__}: {e}")
        return None


def main():
    for fn in (case_t01, case_t02, case_t03, case_t04, case_t05, case_t06, case_t07, case_t08, case_t09, case_t10,
               case_t11, case_t12, case_t13, case_t14, case_t15, case_t16, case_t17, case_x01):
        guarded(fn)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
