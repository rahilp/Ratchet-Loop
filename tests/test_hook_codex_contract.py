#!/usr/bin/env python3
"""
Codex hook reply contract: what `ratchet.py hook stop|guard --host codex` prints, byte for byte, plus the replies of
the other CLIs (claude, gemini, copilot), which must not change.

    python3 tests/test_hook_codex_contract.py      # builds throwaway projects with an active run

Sources the Codex rules were checked against (2026-10-31, release rust-v0.166.0):
  * docs   https://learn.chatgpt.com/docs/hooks (308 from https://developers.openai.com/codex/hooks)
  * source codex-rs/hooks/src/events/stop.rs at commit 75a4146 (the last commit to change stop.rs before release
    rust-v0.166.0, tag commit 6ff1e1c; stop.rs is identical there), and pre_tool_use.rs as read at that release
    (the PreToolUse block, where stderr is the block reason; the exit-2 rule is the same at 75a4146)
What they say, and what this file pins:
  * Stop: exit 0 with EMPTY stdout is a valid no-op (a quiet stop); exit 0 with non-JSON stdout makes the hook
    Failed; exit 2 with a non-blank stderr reason blocks the stop; exit 2 with a blank stderr makes the hook Failed.
  * PreToolUse: the same exit 2 + stderr reason blocks a tool call.
Unknown, and said so: older Codex versions are unverified (no primary evidence for or against empty stdout there).

Latent case, observed and reported, not fixed (out of scope here): emit_hook for host codex with block true and an
empty message would print a blank line to stderr and exit 2, which Codex marks as a Failed hook instead of a block.
It is unreachable today (every Codex block path builds a non-blank reason; CP1-T06 and CP1-X03 pin that) and is
reported, not fixed: this step changes no reply behavior.

Each case id appears in its check names as [CP1-Txx] / [CP1-Xxx].
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = ROOT / "README.md"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS_FILE = Path(__file__).resolve()
NEW_TEST_LINE = "python3 tests/test_hook_codex_contract.py"
PASS, FAIL = 0, 0

_STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "GEMINI_CLI", "COPILOT_CLI", "RATCHET_ROOT", "RATCHET_ROLE",
              "RATCHET_HOME", "RATCHET_HUMAN_OVERRIDE")


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
    """The child process environment: no CLI markers, a throwaway RATCHET_HOME; --host is always given by name."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_") and k not in _STRIP_ENV}
    env["RATCHET_HOME"] = str(home)
    return env


class Proj:
    def __init__(self, path, home):
        self.path, self.home = path, home
        self.cwd = str(path)
        self.state = str(path / ".ratchet/runs/hk/state.json")

    def sh(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(ENGINE), *args], cwd=self.path, input=stdin, capture_output=True,
                              text=True, env=child_env(self.home))

    def hook(self, event, host, payload):
        r = self.sh("hook", event, "--host", host, stdin=json.dumps(payload))
        return r.returncode, r.stdout, r.stderr

    def codex_stop(self, active=False, message="done"):
        return self.hook("stop", "codex", {"session_id": "s", "turn_id": "t1", "cwd": self.cwd,
                                           "hook_event_name": "Stop", "model": "gpt-5-codex",
                                           "stop_hook_active": active, "last_assistant_message": message})

    def codex_guard(self, tool_name, tool_input):
        return self.hook("guard", "codex", {"session_id": "s", "turn_id": "t1", "cwd": self.cwd,
                                            "hook_event_name": "PreToolUse", "model": "gpt-5-codex",
                                            "tool_name": tool_name, "tool_input": tool_input})


def new_project(active=True):
    """A git project; with active=True also `init` plus a one-step plan approved, so the run is active with CP1 open
    (the same setup as tests/test_hooks.py)."""
    import datetime as dt
    d = Path(_tmp.mkdtemp(prefix="ratchet-codex-contract-"))
    home = Path(_tmp.mkdtemp(prefix="ratchet-codex-home-"))
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(c, cwd=d, check=True)
    (d / "a.js").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    p = Proj(d, home)
    if not active:
        return p
    p.sh("init", "codex contract", "--slug", "hk")
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


# ---------------------------------------------------------------------------------- the reply checkers (with teeth)

def is_quiet_noop(rc, out, err):
    """A valid Codex Stop no-op: exit 0 and nothing printed at all."""
    return rc == 0 and out == "" and err == ""


def is_visible_block(rc, out, err):
    """A Codex block: exit 2, the reason on stderr and non-blank, nothing on stdout."""
    return rc == 2 and out == "" and err.strip() != ""


def is_exact_block(rc, out, err, reason):
    return is_visible_block(rc, out, err) and err == reason + "\n"


def is_copilot_json(rc, out, err, expected):
    """Copilot answers in JSON on stdout and always exits 0."""
    return rc == 0 and err == "" and out == json.dumps(expected) + "\n"


def load_engine():
    spec = importlib.util.spec_from_file_location("ratchet_codex_contract", str(ENGINE))
    m = importlib.util.module_from_spec(spec)
    sys.modules["ratchet_codex_contract"] = m
    spec.loader.exec_module(m)
    return m


def emit(fn, host, event, block, message):
    """Call a reply writer with stdout and stderr captured separately: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(host, event, block, message)
    return rc, out.getvalue(), err.getvalue()


_ENGINE_MOD = []


def real_emit():
    if not _ENGINE_MOD:
        _ENGINE_MOD.append(load_engine())
    return _ENGINE_MOD[0].emit_hook


# ------------------------------------------------------------------------------------------------------- the cases

def case_t01():
    print("[CP1-T01] a Codex stop in a folder with no Ratchet run ends quietly")
    p = new_project(active=False)
    rc, out, err = p.codex_stop()
    check("[CP1-T01] exit 0, stdout '' and stderr '' with no .ratchet at all",
          is_quiet_noop(rc, out, err), f"rc={rc} out={out!r} err={err!r}")
    check("[CP1-T01] the folder really has no .ratchet", not (p.path / ".ratchet").exists())


def case_t02():
    print("[CP1-T02] a Codex stop in a paused run ends quietly")
    p = new_project()
    r = p.sh("pause", "--reason", "user asked")
    check("[CP1-T02] setup: pause worked", r.returncode == 0, r.stderr[:200])
    rc, out, err = p.codex_stop()
    check("[CP1-T02] exit 0, stdout '' and stderr '' on a paused run", is_quiet_noop(rc, out, err),
          f"rc={rc} out={out!r} err={err!r}")


def case_t03_x01_t13():
    # one project for three stop checks: 3 refusals is far under the default limit of 40
    p = new_project()
    print("[CP1-T03] a Codex stop is blocked with a visible reason on stderr only")
    rc, out, err = p.codex_stop(active=False)
    check("[CP1-T03] exit 2, stdout '', stderr names RATCHET GATE and the run slug",
          is_visible_block(rc, out, err) and "RATCHET GATE" in err and "hk" in err, f"rc={rc} out={out!r} err={err!r}")
    print("[CP1-X01] a Codex stop that continues after an earlier block is still blocked, with a reason")
    rc, out, err = p.codex_stop(active=True)
    check("[CP1-X01] stop_hook_active true: exit 2, non-blank stderr with RATCHET GATE, stdout ''",
          is_visible_block(rc, out, err) and "RATCHET GATE" in err, f"rc={rc} out={out!r} err={err!r}")
    print("[CP1-T13] claude, gemini and copilot stops answer in their own dialect through the real command")
    case_t13_claude(p)
    case_t13_gemini(p)
    case_t13_copilot(p)
    return p


def case_t13_claude(p):
    rc, out, err = p.hook("stop", "claude", {"session_id": "s", "cwd": p.cwd, "hook_event_name": "Stop",
                                             "stop_hook_active": False})
    check("[CP1-T13] claude: exit 2, stderr has RATCHET GATE, stdout ''",
          rc == 2 and "RATCHET GATE" in err and out == "", f"rc={rc} out={out!r} err={err[:100]!r}")


def case_t13_gemini(p):
    rc, out, err = p.hook("stop", "gemini", {"session_id": "s", "cwd": p.cwd, "hook_event_name": "AfterAgent",
                                             "prompt": "go", "prompt_response": "done!", "stop_hook_active": False,
                                             "timestamp": "2026-09-28T00:00:00Z"})
    check("[CP1-T13] gemini (AfterAgent): exit 2, stderr has RATCHET GATE, stdout ''",
          rc == 2 and "RATCHET GATE" in err and out == "", f"rc={rc} out={out!r} err={err[:100]!r}")


def case_t13_copilot(p):
    rc, out, err = p.hook("stop", "copilot", {"sessionId": "s", "timestamp": 1790000000000, "cwd": p.cwd,
                                              "transcriptPath": "/tmp/t", "stopReason": "end_turn",
                                              "stop_hook_active": False})
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = {}
    check("[CP1-T13] copilot (agentStop): exit 0, stderr '', stdout JSON decision block with RATCHET GATE",
          rc == 0 and err == "" and isinstance(parsed, dict) and parsed.get("decision") == "block"
          and "RATCHET GATE" in str(parsed.get("reason", "")), f"rc={rc} out={out[:100]!r} err={err!r}")


def case_guards(p):
    print("[CP1-T04] a Codex apply_patch on a protected state file is blocked with a reason")
    patch = "*** Begin Patch\n*** Update File: .ratchet/runs/hk/state.json\n@@\n-x\n+y\n*** End Patch"
    rc, out, err = p.codex_guard("apply_patch", {"input": patch})
    check("[CP1-T04] exit 2, stderr non-blank with RATCHET GUARD and state.json, stdout ''",
          is_visible_block(rc, out, err) and "RATCHET GUARD" in err and "state.json" in err,
          f"rc={rc} out={out!r} err={err!r}")

    print("[CP1-T05] a Codex shell call (command as a list) overwriting a protected file is blocked with a reason")
    rc, out, err = p.codex_guard("Bash", {"command": ["bash", "-lc", f"echo {{}} > {p.state}"]})
    check("[CP1-T05] exit 2, stderr non-blank, stdout ''", is_visible_block(rc, out, err),
          f"rc={rc} out={out!r} err={err!r}")

    print("[CP1-T06] every other kind of Codex tool block also carries a visible reason")
    run = f"{p.cwd}/.ratchet/runs/hk"
    approvals = f"{run}/approvals.json"
    attempts = [
        ("rm human_log", f"rm {p.cwd}/.ratchet/human_log.jsonl"),
        ("redirect into evidence", f"echo ok > {run}/evidence/x.json"),
        ("nested agent approving", "codex exec 'approve the plan'"),
        ("write approvals.json", f"echo '{{}}' > {approvals}"),
    ]
    for label, cmd in attempts:
        rc, out, err = p.codex_guard("Bash", {"command": cmd})
        check(f"[CP1-T06] {label}: exit 2, stderr non-blank, stdout ''", is_visible_block(rc, out, err),
              f"rc={rc} out={out!r} err={err!r}")

    print("[CP1-T07] a Codex tool call Ratchet allows prints nothing and exits 0")
    rc, out, err = p.codex_guard("Bash", {"command": ["bash", "-lc", "npm test"]})
    check("[CP1-T07] Bash npm test: exit 0, stdout '' and stderr ''", is_quiet_noop(rc, out, err),
          f"rc={rc} out={out!r} err={err!r}")
    patch = "*** Begin Patch\n*** Update File: a.js\n@@\n-x\n+y\n*** End Patch"
    rc, out, err = p.codex_guard("apply_patch", {"input": patch})
    check("[CP1-T07] apply_patch on a.js: exit 0, stdout '' and stderr ''", is_quiet_noop(rc, out, err),
          f"rc={rc} out={out!r} err={err!r}")


def case_emit():
    print("[CP1-T08] the Codex reply writer gives a quiet no-op and a stderr-only block")
    e = real_emit()
    check("[CP1-T08] (stop, no block, '') returns 0 and prints nothing",
          is_quiet_noop(*emit(e, "codex", "stop", False, "")), repr(emit(e, "codex", "stop", False, "")))
    check("[CP1-T08] (guard, no block, '') returns 0 and prints nothing",
          is_quiet_noop(*emit(e, "codex", "guard", False, "")))
    check("[CP1-T08] (stop, block, 'why') returns 2, stderr exactly 'why\\n', stdout ''",
          emit(e, "codex", "stop", True, "why") == (2, "", "why\n"), repr(emit(e, "codex", "stop", True, "why")))
    check("[CP1-T08] (guard, block, 'why2') returns 2, stderr exactly 'why2\\n', stdout ''",
          emit(e, "codex", "guard", True, "why2") == (2, "", "why2\n"), repr(emit(e, "codex", "guard", True, "why2")))

    print("[CP1-T09] Claude Code replies are byte-identical to today's")
    check("[CP1-T09] (stop, block, 'm') -> 2, stderr 'm\\n', stdout ''", emit(e, "claude", "stop", True, "m") == (2, "", "m\n"))
    check("[CP1-T09] (guard, block, 'm') -> 2, stderr 'm\\n', stdout ''", emit(e, "claude", "guard", True, "m") == (2, "", "m\n"))
    check("[CP1-T09] (prompt, no block, 'guidance') -> 0, stdout 'guidance\\n', stderr ''",
          emit(e, "claude", "prompt", False, "guidance") == (0, "guidance\n", ""))
    check("[CP1-T09] (stop, no block, 'note') -> 0, stderr 'note\\n', stdout ''",
          emit(e, "claude", "stop", False, "note") == (0, "", "note\n"))
    check("[CP1-T09] (stop, no block, '') -> 0 and nothing printed", emit(e, "claude", "stop", False, "") == (0, "", ""))

    print("[CP1-T10] Gemini replies are byte-identical to today's")
    check("[CP1-T10] (stop, block, 'm') -> 2, stderr 'm\\n', stdout ''", emit(e, "gemini", "stop", True, "m") == (2, "", "m\n"))
    check("[CP1-T10] (guard, block, 'm') -> 2, stderr 'm\\n', stdout ''", emit(e, "gemini", "guard", True, "m") == (2, "", "m\n"))
    check("[CP1-T10] (prompt, no block, 'guidance') -> 0, stderr 'guidance\\n', stdout ''",
          emit(e, "gemini", "prompt", False, "guidance") == (0, "", "guidance\n"))
    check("[CP1-T10] (stop, no block, '') -> 0 and nothing printed", emit(e, "gemini", "stop", False, "") == (0, "", ""))

    print("[CP1-T11] Copilot replies are byte-identical to today's")
    check("[CP1-T11] (guard, block, 'm') -> 0, exact permissionDecision deny JSON",
          emit(e, "copilot", "guard", True, "m") == (0, '{"permissionDecision": "deny", "permissionDecisionReason": "m"}\n', ""))
    check("[CP1-T11] (stop, block, 'm') -> 0, exact decision block JSON",
          emit(e, "copilot", "stop", True, "m") == (0, '{"decision": "block", "reason": "m"}\n', ""))
    check("[CP1-T11] (prompt, block, 'm') -> 0 and nothing printed", emit(e, "copilot", "prompt", True, "m") == (0, "", ""))
    check("[CP1-T11] (prompt, no block, 'guidance') -> 0 and nothing printed",
          emit(e, "copilot", "prompt", False, "guidance") == (0, "", ""))
    check("[CP1-T11] (stop, no block, 'note') -> 0, stderr 'note\\n', stdout ''",
          emit(e, "copilot", "stop", False, "note") == (0, "", "note\n"))

    print("[CP1-T12] Codex replies are unchanged for all three events")
    for ev in ("stop", "guard", "prompt"):
        check(f"[CP1-T12] ({ev}, block, 'm') -> 2, stderr 'm\\n', stdout ''",
              emit(e, "codex", ev, True, "m") == (2, "", "m\n"))
    check("[CP1-T12] (prompt, no block, 'guidance') -> 0, stderr 'guidance\\n', stdout ''",
          emit(e, "codex", "prompt", False, "guidance") == (0, "", "guidance\n"))
    check("[CP1-T12] (stop, no block, 'note') -> 0, stderr 'note\\n', stdout ''",
          emit(e, "codex", "stop", False, "note") == (0, "", "note\n"))
    check("[CP1-T12] (stop, no block, '') -> 0 and nothing printed", emit(e, "codex", "stop", False, "") == (0, "", ""))


# ---------------------------------------------------------------------------------- the citation comment (T14, T15)

def hook_comment_span():
    """Lines from the one starting `# Ratchet needs three things` up to (not including) `HOOK_HOSTS = (`."""
    lines = ENGINE.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("# Ratchet needs three things")), None)
    end = next((i for i, ln in enumerate(lines) if ln.startswith("HOOK_HOSTS = (")), None)
    if start is None or end is None or end <= start:
        return None
    return lines[start:end]


def case_t14_t15():
    span = hook_comment_span()
    print("[CP1-T14] the checked Codex rules and their sources are written next to the hook replies")
    check("[CP1-T14] setup: the reply comment block was found above HOOK_HOSTS", span is not None)
    span = span or []
    text = re.sub(r"\s+", " ", " ".join(ln.lstrip("#") for ln in span)).lower()
    for label, needle in (("docs address", "learn.chatgpt.com/docs/hooks"), ("source file", "stop.rs"),
                          ("commit", "75a4146"), ("release", "rust-v0.166.0"), ("date", "2026-10-31")):
        check(f"[CP1-T14] the comment names the {label} ({needle})", needle in text, text[:200])
    check("[CP1-T14] the comment says exit 0 with empty stdout is a valid Stop no-op",
          "exit 0" in text and "empty stdout" in text and "no-op" in text)
    check("[CP1-T14] the comment says a block needs a non-blank stderr reason",
          "non-blank" in text and "stderr" in text)
    markers = ("unverified", "not verified", "no primary evidence")
    older = [ln.lower() for ln in span if "older" in ln.lower()]
    check("[CP1-T14] a line says older Codex versions are unverified",
          any(any(m in ln for m in markers) for ln in older), older)
    check("[CP1-T14] no line asserts as fact that older versions reject empty stdout",
          not any("reject" in ln and not any(m in ln for m in markers) for ln in older), older)

    print("[CP1-T15] the citation edit is a comment only, placed with the reply table")
    src = ENGINE.read_text(encoding="utf-8")
    try:
        ast.parse(src)
        parsed = True
    except SyntaxError as e:
        parsed = False
        print(f"    parse error: {e}")
    check("[CP1-T15] ast.parse of ratchet.py succeeds", parsed)
    check("[CP1-T15] every non-blank line in the span starts with '#'",
          bool(span) and all(ln.startswith("#") for ln in span if ln.strip()),
          [ln for ln in span if ln.strip() and not ln.startswith("#")][:3])
    codex_row = "codex    Stop           UserPromptSubmit      PreToolUse    exit 2 + stderr"
    check("[CP1-T15] the span still holds the codex table row as one line",
          any(ln.startswith("#") and codex_row in ln for ln in span))
    check("[CP1-T15] the span still holds the fail-open sentence",
          "Hooks fail OPEN on internal errors" in "\n".join(span)
          and "a crashing guard must never brick a session." in "\n".join(span))
    check("[CP1-T15] the new citation lines sit inside that same span",
          "learn.chatgpt.com/docs/hooks" in "\n".join(span) and "stop.rs" in "\n".join(span)
          and "75a4146" in "\n".join(span))


def case_t16():
    print("[CP1-T16] this file's docstring repeats the sources and the latent blank-reason case")
    doc = ast.get_docstring(ast.parse(THIS_FILE.read_text(encoding="utf-8"))) or ""
    low = re.sub(r"\s+", " ", doc.lower())
    check("[CP1-T16] docstring names the docs address", "learn.chatgpt.com/docs/hooks" in low)
    check("[CP1-T16] docstring names stop.rs at 75a4146", "stop.rs" in low and "75a4146" in low)
    check("[CP1-T16] docstring records the latent emit_hook(codex, block, '') blank line, unreachable, not fixed",
          "emit_hook" in low and "codex" in low and "blank line" in low and "unreachable" in low
          and "not fixed" in low)


# ------------------------------------------------------------------------------------------- registration (T17-T19)

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


def inversions(items):
    return [(a, b) for a, b in zip(items, items[1:]) if a > b]


def case_t17_t18():
    block = readme_test_block()
    print("[CP1-T17] the README test list names the new test file exactly once")
    check("[CP1-T17] the line appears exactly once in the 'Running the tests' block",
          block.count(NEW_TEST_LINE) == 1, block.count(NEW_TEST_LINE))
    check("[CP1-T17] the line appears nowhere else in the README",
          README.read_text(encoding="utf-8").splitlines().count(NEW_TEST_LINE) == 1)

    print("[CP1-T18] the README test list keeps its order")
    lst = [ln for ln in block if ln.startswith("python3 tests/")]
    check("[CP1-T18] setup: the new line is in the list", NEW_TEST_LINE in lst)
    if NEW_TEST_LINE in lst:
        i = lst.index(NEW_TEST_LINE)
        prev_ok = i == 0 or lst[i - 1] < NEW_TEST_LINE
        next_ok = i + 1 >= len(lst) or lst[i + 1] > NEW_TEST_LINE
        check("[CP1-T18] its previous neighbour in the list sorts before it", prev_ok, lst[max(0, i - 1):i + 2])
        check("[CP1-T18] its next neighbour in the list sorts after it", next_ok, lst[max(0, i - 1):i + 2])
        guard, listpay = "python3 tests/test_guardrails.py", "python3 tests/test_hook_list_payload.py"
        check("[CP1-T18] it sits after test_guardrails.py and before test_hook_list_payload.py",
              guard in lst and listpay in lst and lst.index(guard) < i < lst.index(listpay), lst[max(0, i - 2):i + 3])
        without = [x for x in lst if x != NEW_TEST_LINE]
        # the list already has a few older out-of-order pairs; adding the line must not add one
        check("[CP1-T18] adding the line adds no out-of-order pair to the list",
              inversions(lst) == inversions(without), inversions(lst))
    else:
        check("[CP1-T18] its previous neighbour in the list sorts before it", False, "line missing")
        check("[CP1-T18] its next neighbour in the list sorts after it", False, "line missing")
        check("[CP1-T18] it sits after test_guardrails.py and before test_hook_list_payload.py", False, "line missing")
        check("[CP1-T18] adding the line adds no out-of-order pair to the list", False, "line missing")


def case_t19():
    print("[CP1-T19] the test-inventory document names the new test file in Kind 3")
    text = INVENTORY.read_text(encoding="utf-8")
    name = "test_hook_codex_contract.py"
    m = re.search(r"^## Kind 3\b.*?(?=^## Kind 4\b)", text, re.S | re.M)
    kind3 = m.group(0) if m else ""
    check("[CP1-T19] setup: the Kind 3 section was found", bool(kind3))
    entry = re.search(r"^- `" + re.escape(name) + r"`[^\n]*(?:\n  [^\n]*)*", kind3, re.M)
    check("[CP1-T19] the file is named as a list entry in the Kind 3 (both) section", entry is not None)
    check("[CP1-T19] the entry says what it checks (more than the bare name)",
          entry is not None and len(entry.group(0)) > len(name) + 60, entry and len(entry.group(0)))
    check("[CP1-T19] this file really does both (module load and source text)",
          "spec_from_file_location" in THIS_FILE.read_text(encoding="utf-8")
          and "ENGINE.read_text" in THIS_FILE.read_text(encoding="utf-8"))
    others = [m2.start() for m2 in re.finditer(re.escape(name), text)]
    check("[CP1-T19] the file is not listed under a different kind as well",
          all(m.start() <= o < m.end() for o in others) if m else False)


def case_t20():
    print("[CP1-T20] the full suite stays green (opt-in run); by default the new file is one the suite runs")
    run_all = ROOT / "tests" / "run_all.py"
    run_all_src = run_all.read_text(encoding="utf-8")

    # tests/run_all.py finds suites inline in main() (no public discovery function), as
    # `sorted(p for p in folder.glob("test_*.py") if p.is_file())`. This reproduces that rule, and the check
    # below that run_all.py still contains that exact glob stops this copy from silently drifting.
    def discovered(folder):
        return sorted(p.resolve() for p in Path(folder).glob("test_*.py") if p.is_file())

    check("[CP1-T20] setup: run_all.py still finds suites with folder.glob(\"test_*.py\")",
          'folder.glob("test_*.py")' in run_all_src)
    check("[CP1-T20] run_all.py really discovers tests/test_hook_codex_contract.py",
          THIS_FILE in discovered(ROOT / "tests"), [p.name for p in discovered(ROOT / "tests")][:5])
    # teeth: the same rule must NOT pick up a wrongly named file or one in a subfolder
    with _tmp.mkdtemp() as d:
        d = Path(d)
        (d / "test_a.py").write_text("")
        (d / "contract_check.py").write_text("")
        (d / "sub").mkdir()
        (d / "sub" / "test_b.py").write_text("")
        found = [p.name for p in discovered(d)]
        check("[CP1-T20] teeth: a misnamed file or one in a subfolder is not discovered", found == ["test_a.py"], found)
    if os.environ.get("RATCHET_CP1_T20_FULL_SUITE") == "1":
        r = subprocess.run([sys.executable, str(run_all)], cwd=ROOT, capture_output=True, text=True)
        check("[CP1-T20] python3 tests/run_all.py exits 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    else:
        print("    NOT RUN here: the full suite itself (python3 tests/run_all.py) is enforced by `ratchet.py complete`"
              " and is not counted as a passing check above; set RATCHET_CP1_T20_FULL_SUITE=1 to run it here")


# ----------------------------------------------------------------------------------------------------- extra cases

def case_x02():
    print("[CP1-X02] when Ratchet gives up blocking, the Codex stop ends exit 0 with nothing on stdout")
    p = new_project()
    r = p.sh("config", "--set", "max_stop_blocks=2")
    check("[CP1-X02] setup: max_stop_blocks lowered to 2", r.returncode == 0, r.stderr[:200] + r.stdout[:200])
    for n in (1, 2):
        rc, out, err = p.codex_stop()
        check(f"[CP1-X02] call {n}: exit 2, stderr non-blank, stdout ''", is_visible_block(rc, out, err),
              f"rc={rc} out={out!r} err={err!r}")
    rc, out, err = p.codex_stop()
    check("[CP1-X02] call 3: exit 0 and stdout exactly ''", rc == 0 and out == "", f"rc={rc} out={out!r} err={err!r}")
    check("[CP1-X02] call 3: the safety valve message is on stderr only", "safety valve" in err, err)


def case_x03():
    print("[CP1-X03] a Codex stop with a damaged run state fails closed with a visible reason")
    p = new_project()
    Path(p.state).write_text("{not json")
    rc, out, err = p.codex_stop()
    check("[CP1-X03] exit 2, stderr non-blank and says the state could not be read, stdout ''",
          is_visible_block(rc, out, err) and "could not read" in err, f"rc={rc} out={out!r} err={err!r}")
    log = p.path / ".ratchet" / "hook-errors.log"
    text = log.read_text() if log.exists() else ""
    check("[CP1-X03] the failure is logged in .ratchet/hook-errors.log", "codex stop" in text and "StateUnreadable" in text,
          text[-200:])


def case_x04():
    print("[CP1-X04] the Codex contract checks reject wrong replies (teeth)")

    def noop_braces(host, event, block, msg):
        print("{}")
        return 0

    def block_exit0(host, event, block, msg):
        print(msg, file=sys.stderr)
        return 0

    def block_blank(host, event, block, msg):
        print("", file=sys.stderr)
        return 2

    def block_stdout(host, event, block, msg):
        print(msg)
        return 2

    def copilot_exit2(host, event, block, msg):
        print(json.dumps({"decision": "block", "reason": msg}))
        return 2

    rc, out, err = emit(noop_braces, "codex", "stop", False, "")
    check("[CP1-X04] a stop no-op that prints '{}' is rejected", not is_quiet_noop(rc, out, err))
    rc, out, err = emit(block_exit0, "codex", "stop", True, "why")
    check("[CP1-X04] a block that exits 0 is rejected", not is_exact_block(rc, out, err, "why"))
    rc, out, err = emit(block_blank, "codex", "stop", True, "why")
    check("[CP1-X04] a block with a blank stderr is rejected", not is_visible_block(rc, out, err))
    rc, out, err = emit(block_stdout, "codex", "stop", True, "why")
    check("[CP1-X04] a block with the reason on stdout is rejected", not is_visible_block(rc, out, err)
          and not is_exact_block(rc, out, err, "why"))
    rc, out, err = emit(copilot_exit2, "copilot", "stop", True, "m")
    check("[CP1-X04] a copilot reply that exits 2 is rejected",
          not is_copilot_json(rc, out, err, {"decision": "block", "reason": "m"}))
    e = real_emit()
    check("[CP1-X04] the real emit_hook is accepted: codex no-op", is_quiet_noop(*emit(e, "codex", "stop", False, "")))
    check("[CP1-X04] the real emit_hook is accepted: codex block",
          is_exact_block(*emit(e, "codex", "stop", True, "why"), "why"))
    check("[CP1-X04] the real emit_hook is accepted: copilot block",
          is_copilot_json(*emit(e, "copilot", "stop", True, "m"), {"decision": "block", "reason": "m"}))


def new_planned_project(submit):
    """A git project with `init` and a one-step plan written; with submit=True the plan is also submitted and its
    approval question printed, so the run is ACTIVE in phase awaiting_plan_approval, where the stop hook has nothing
    to block (stop_ok). With submit=False it stays in phase planning, where a stop is blocked (the contrast)."""
    p = new_project(active=False)
    p.sh("init", "codex contract", "--slug", "hk")
    run = p.path / ".ratchet" / "runs" / "hk"
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    if submit:
        assert p.sh("plan-submit").returncode == 0
        assert p.sh("ask", "approve-plan").returncode == 0
    return p


def case_x05():
    print("[CP1-X05] a Codex stop in an ACTIVE run with nothing to block ends quietly")
    p = new_planned_project(submit=True)
    phase = json.loads(Path(p.state).read_text()).get("phase")
    active = json.loads((p.path / ".ratchet" / "active.json").read_text()).get("slug")
    check("[CP1-X05] setup: the run is active (active.json names hk) and awaiting plan approval",
          active == "hk" and phase == "awaiting_plan_approval", f"active={active!r} phase={phase!r}")
    rc, out, err = p.codex_stop()
    check("[CP1-X05] exit 0, stdout '' and stderr '' on an active run with no gate to block",
          is_quiet_noop(rc, out, err), f"rc={rc} out={out!r} err={err!r}")
    # teeth: the same project one step earlier (plan not submitted, a gate still open) must block the stop
    q = new_planned_project(submit=False)
    qphase = json.loads(Path(q.state).read_text()).get("phase")
    rc, out, err = q.codex_stop()
    check("[CP1-X05] teeth: the same setup with the plan not submitted (phase planning) blocks: exit 2, reason on stderr",
          qphase == "planning" and is_visible_block(rc, out, err) and "RATCHET GATE" in err,
          f"phase={qphase!r} rc={rc} out={out!r} err={err!r}")


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
    guarded(case_t01)
    guarded(case_t02)
    p = guarded(case_t03_x01_t13)
    if p is not None:
        guarded(case_guards, p)
    guarded(case_emit)
    guarded(case_t14_t15)
    guarded(case_t16)
    guarded(case_t17_t18)
    guarded(case_t19)
    guarded(case_t20)
    guarded(case_x02)
    guarded(case_x03)
    guarded(case_x04)
    guarded(case_x05)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
