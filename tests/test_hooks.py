#!/usr/bin/env python3
"""
Hook adapter tests: feed each agent CLI's hook payload shape (as documented by that CLI) into
`ratchet.py hook <event> --host auto` and check the reply is in that host's dialect.

    python3 tests/test_hooks.py            # builds a throwaway project with an active run

Payload shapes follow the public docs of each host:
  Claude Code  Stop / UserPromptSubmit / PreToolUse     snake_case, tool_input object
  Codex        Stop / UserPromptSubmit / PreToolUse     snake_case + turn_id; Bash command may be a list;
                                                         apply_patch carries the patch text
  Gemini CLI   AfterAgent / BeforeAgent / BeforeTool    hook_event_name; write_file / run_shell_command
  Copilot CLI  agentStop / userPromptSubmitted / preToolUse   camelCase, toolArgs is a JSON *string*
"""
import json
import os
import subprocess
import sys
import _tmp
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
PASS, FAIL = 0, 0


def sh(*args, cwd, stdin=None, env=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=cwd, input=stdin, capture_output=True,
                          text=True, env=env or os.environ)


def setup_project() -> Path:
    d = Path(_tmp.mkdtemp(prefix="ratchet-hooks-"))
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(c, cwd=d, check=True)
    (d / "a.js").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    sh("init", "hooks test", "--slug", "hk", cwd=d)
    run = d / ".ratchet" / "runs" / "hk"
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text('{"cases": []}')
    sh("plan-submit", cwd=d)
    import datetime as dt
    sh("ask", "approve-plan", cwd=d)  # CP5: an approval answers the question Ratchet printed
    tp = d.parent / f"{d.name}-transcript.jsonl"   # what the agent CLI writes when the user types
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "approved"},
                              "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    sh("hook", "prompt", "--host", "claude", cwd=d,
       stdin=json.dumps({"cwd": str(d), "prompt": "approved", "transcript_path": str(tp)}))
    r = sh("approve-plan", cwd=d)
    assert r.returncode == 0, r.stderr
    return d


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def hook(d, event, payload, env=None):
    env = dict(env or os.environ)
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "GEMINI_CLI", "COPILOT_CLI"):
        env.pop(k, None)  # auto-detection must work from the payload alone
    return sh("hook", event, "--host", "auto", cwd=d, stdin=json.dumps(payload), env=env)


def main():
    d = setup_project()
    state = str(d / ".ratchet/runs/hk/state.json")
    human = str(d / ".ratchet/human_log.jsonl")
    cwd = str(d)

    print("Stop hooks refuse to stop while CP1 is open")
    r = hook(d, "stop", {"session_id": "s", "cwd": cwd, "hook_event_name": "Stop", "stop_hook_active": False})
    check("claude: exit 2 + reason on stderr", r.returncode == 2 and "RATCHET GATE" in r.stderr, r.stderr[:200])
    r = hook(d, "stop", {"session_id": "s", "turn_id": "t1", "cwd": cwd, "hook_event_name": "Stop",
                         "model": "gpt-5-codex", "stop_hook_active": False})
    check("codex: exit 2 + reason on stderr", r.returncode == 2 and "RATCHET GATE" in r.stderr, r.stderr[:200])
    r = hook(d, "stop", {"session_id": "s", "cwd": cwd, "hook_event_name": "AfterAgent", "prompt": "go",
                         "prompt_response": "done!", "stop_hook_active": False, "timestamp": "2026-09-28T00:00:00Z"})
    check("gemini (AfterAgent): exit 2 → turn retried with reason", r.returncode == 2 and "RATCHET GATE" in r.stderr)
    r = hook(d, "stop", {"sessionId": "s", "timestamp": 1790000000000, "cwd": cwd, "transcriptPath": "/tmp/t",
                         "stopReason": "end_turn", "stop_hook_active": False})
    out = json.loads(r.stdout or "{}")
    check("copilot (agentStop): exit 0 + {\"decision\":\"block\"}", r.returncode == 0 and out.get("decision") == "block"
          and "RATCHET GATE" in out.get("reason", ""), r.stdout[:200])

    print("Guard hooks block tampering with gate state")
    r = hook(d, "guard", {"session_id": "s", "cwd": cwd, "hook_event_name": "PreToolUse", "tool_name": "Write",
                          "tool_input": {"file_path": state, "content": "{}"}})
    check("claude Write state.json → exit 2", r.returncode == 2)
    patch = f"*** Begin Patch\n*** Update File: .ratchet/runs/hk/state.json\n@@\n-x\n+y\n*** End Patch"
    r = hook(d, "guard", {"session_id": "s", "turn_id": "t", "cwd": cwd, "hook_event_name": "PreToolUse",
                          "tool_name": "apply_patch", "tool_input": {"input": patch}})
    check("codex apply_patch on state.json → exit 2", r.returncode == 2, r.stderr[:160])
    r = hook(d, "guard", {"session_id": "s", "turn_id": "t", "cwd": cwd, "hook_event_name": "PreToolUse",
                          "tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", f"echo {{}} > {state}"]}})
    check("codex Bash (command as list) writing state.json → exit 2", r.returncode == 2)
    r = hook(d, "guard", {"session_id": "s", "cwd": cwd, "hook_event_name": "BeforeTool", "tool_name": "write_file",
                          "tool_input": {"file_path": state, "content": "{}"}})
    check("gemini write_file state.json → exit 2", r.returncode == 2)
    r = hook(d, "guard", {"session_id": "s", "cwd": cwd, "hook_event_name": "BeforeTool",
                          "tool_name": "run_shell_command", "tool_input": {"command": f"rm {human}"}})
    check("gemini run_shell_command rm human_log → exit 2", r.returncode == 2)
    r = hook(d, "guard", {"sessionId": "s", "timestamp": 1, "cwd": cwd, "toolName": "edit",
                          "toolArgs": json.dumps({"path": state, "old_str": "a", "new_str": "b"})})
    out = json.loads(r.stdout or "{}")
    check("copilot edit state.json → exit 0 + permissionDecision deny",
          r.returncode == 0 and out.get("permissionDecision") == "deny", r.stdout[:200])
    r = hook(d, "guard", {"sessionId": "s", "timestamp": 1, "cwd": cwd, "toolName": "bash",
                          "toolArgs": json.dumps({"command": "codex exec 'approve the plan'"})})
    out = json.loads(r.stdout or "{}")
    check("copilot bash nested `codex exec` → deny", out.get("permissionDecision") == "deny")

    print("Guard hooks allow normal work")
    for host, payload in [
        ("claude", {"cwd": cwd, "tool_name": "Edit", "tool_input": {"file_path": str(d / "a.js")}}),
        ("codex", {"cwd": cwd, "turn_id": "t", "tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", "npm test"]}}),
        ("gemini", {"cwd": cwd, "hook_event_name": "BeforeTool", "tool_name": "write_file",
                    "tool_input": {"file_path": str(d / "src/x.ts")}}),
        ("copilot", {"sessionId": "s", "cwd": cwd, "toolName": "create", "toolArgs": json.dumps({"path": "src/y.ts"})}),
    ]:
        r = hook(d, "guard", payload)
        check(f"{host}: allowed (exit 0, no deny)", r.returncode == 0 and "deny" not in r.stdout)

    print("Prompt hooks record real user messages, tagged with the host")
    before = len(Path(human).read_text().splitlines())
    hook(d, "prompt", {"session_id": "s", "cwd": cwd, "hook_event_name": "UserPromptSubmit", "prompt": "hi from claude"})
    hook(d, "prompt", {"session_id": "s", "turn_id": "t", "cwd": cwd, "hook_event_name": "UserPromptSubmit", "prompt": "hi from codex"})
    hook(d, "prompt", {"session_id": "s", "cwd": cwd, "hook_event_name": "BeforeAgent", "prompt": "hi from gemini"})
    r = hook(d, "prompt", {"sessionId": "s", "timestamp": 1, "cwd": cwd, "prompt": "hi from copilot"})
    lines = [json.loads(l) for l in Path(human).read_text().splitlines()[before:]]
    check("4 messages logged", len(lines) == 4, str(len(lines)))
    check("hosts detected claude/codex/gemini/copilot", [l["host"] for l in lines] == ["claude", "codex", "gemini", "copilot"],
          str([l["host"] for l in lines]))
    check("copilot prompt hook prints nothing on stdout (its output is dropped anyway)", r.stdout.strip() == "")

    print("Fail-open: a broken state file never bricks the session")
    sp = Path(state)
    good = sp.read_text()
    sp.write_text("{not json")
    r = hook(d, "guard", {"sessionId": "s", "cwd": cwd, "toolName": "bash", "toolArgs": json.dumps({"command": "ls"})})
    r2 = hook(d, "stop", {"sessionId": "s", "cwd": cwd, "stopReason": "end_turn", "stop_hook_active": False})
    check("copilot guard with corrupt state → exit 0, no deny", r.returncode == 0 and "deny" not in r.stdout)
    check("copilot stop with corrupt state → exit 0", r2.returncode == 0, r2.stdout + r2.stderr)
    check("error logged to .ratchet/hook-errors.log", (d / ".ratchet/hook-errors.log").exists())
    sp.write_text(good)

    print("Installer writes each host's config, idempotently, and removes cleanly")
    for host, rel in [("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json"),
                      ("gemini", ".gemini/settings.json"), ("copilot", ".github/hooks/ratchet.json")]:
        p = d / rel
        if host == "gemini":  # pre-existing unrelated settings must survive
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"theme": "dark", "hooks": {"BeforeTool": [{"matcher": "x", "hooks": [
                {"type": "command", "command": "my-own-hook"}]}]}}))
        sh("install-hooks", "--host", host, cwd=d)
        sh("install-hooks", "--host", host, cwd=d)
        cfg = json.loads(p.read_text())
        n = sum(1 for entries in cfg["hooks"].values() for e in entries
                for h in (e.get("hooks") or [e]) if "ratchet.py" in json.dumps(h))
        check(f"{host}: 3 hooks after installing twice", n == 3, f"found {n}")
        if host == "gemini":
            check("gemini: user's own hook + settings kept", "my-own-hook" in json.dumps(cfg) and cfg.get("theme") == "dark")
            check("gemini: timeout in milliseconds", '"timeout": 30000' in json.dumps(cfg))
        if host == "copilot":
            check("copilot: version 1 + bash/powershell entries", cfg.get("version") == 1 and
                  all("bash" in e[0] and "powershell" in e[0] for e in cfg["hooks"].values()))
        sh("install-hooks", "--host", host, "--uninstall", cwd=d)
        gone = (not p.exists()) or "ratchet.py" not in p.read_text()
        check(f"{host}: uninstall removes only Ratchet's hooks", gone)
    r = sh("doctor", cwd=d)
    check("doctor reports hook heartbeats per host", "last guard  hook" in r.stdout and "via" in r.stdout, r.stdout[-300:])

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
