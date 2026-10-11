#!/usr/bin/env python3
"""
CP3 tests: Sub-agents launched without the objective are refused.   python3 tests/test_brief_guard.py

On Claude Code, launching a Ratchet sub-agent (subagent_type `ratchet-<role>`, with or without the plugin
`ratchet-loop:` prefix) during a build (phase `building` or `blocked`) with a prompt that does not carry the
plan's objective -- the exact text `ratchet.py brief CPn --role <role>` prints -- is refused (exit 2) with a plain
message naming the `brief` command to fix the prompt with. A launch whose prompt does carry that text, re-wrapped
or not, goes through. A non-Ratchet sub-agent, any sub-agent while the run isn't building, or any sub-agent on a
non-Claude-Code host (Codex, Gemini, Copilot) is never refused for this. A guard crash (an unreadable plan) fails
open (exit 0) but is still recorded like any other hook crash, never a silent, unrecorded pass.

Reuses tests/test_gates.py (`project`, `approve_plan`, `rx`, `ENGINE`, `ENV`) for the throwaway git projects and
the real-CLI-subprocess way every suite in this repo drives the engine, and tests/test_status_plain.py's
`mutate`/record_chain re-baseline pattern (copied here, not imported, since that module isn't a shared helper)
to write state.json's phase to 'blocked' directly, as CP3-T07's own fixture asks for, without breaking the
engine's own integrity chain.

The guard hook is exercised via `ratchet.py hook guard --host <host>` with a JSON payload on stdin, carrying
`tool_name` "Task" and `tool_input` {"subagent_type", "prompt"} -- the shape Claude Code's PreToolUse hook sends
for a sub-agent launch -- exactly as tests/test_gates.py's own `guard()` helper does for other tool calls.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, rx, ENGINE, ENV)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP3-[TX]\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

MISSING_OBJECTIVE_PROMPT = ("Please implement checkpoint CP1: add the new validation function described in the "
                             "plan and wire it into the existing handler.")
NO_OBJECTIVE_AT_ALL_PROMPT = "Explore the repository and summarize the module layout you find."


def guard_host(d, host, tool_name, tool_input, extra_payload=None):
    """One `hook guard` call on host `host`, with a Claude-Code-shaped tool_name/tool_input pair (tests/test_gates
    .py's own `guard()` helper is --host claude only; CP3-T08 needs a Codex-shaped call too)."""
    payload = {"cwd": str(d), "tool_name": tool_name, "tool_input": tool_input}
    if extra_payload:
        payload.update(extra_payload)
    return subprocess.run([sys.executable, str(g.ENGINE), "hook", "guard", "--host", host], cwd=d, text=True,
                          capture_output=True, env=g.ENV, input=json.dumps(payload))


def task_guard(d, subagent_type, prompt, host="claude", extra_payload=None):
    """A Claude-Code-style `Task` tool call launching a sub-agent: tool_input carries `subagent_type` and the
    sub-agent's own opening `prompt`, the two fields this guard check inspects."""
    return guard_host(d, host, "Task", {"subagent_type": subagent_type, "prompt": prompt}, extra_payload)


def _building_project(name):
    d = g.project(name, cps=1)
    g.approve_plan(d)  # phase -> building
    return d


# A shared, never-mutated building project for every case that only *reads* through the guard hook (T01-T05, T08).
# T06 (not building), T07 (forced to 'blocked') and T09 (checkpoints.json corrupted) each get their own project so
# mutating their fixtures can never leak into another case.
_PROJECT = _building_project("brief-guard-shared")


_REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
               "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
               "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
               "m.record_chain(Path(sys.argv[2]))\n")


def _force_phase_blocked(d):
    """CP3-T07's own fixture: write state.json's phase to 'blocked' directly -- not through check-in or triage --
    then re-baseline the engine's own integrity chain with its own record_chain (what accept-state does), the same
    way tests/test_status_plain.py's `mutate` helper does, so the direct edit doesn't make later reads of this
    run's state look tampered with."""
    p = Path(d) / ".ratchet" / "runs" / "f" / "state.json"
    st = json.loads(p.read_text())
    st["blocked_from"] = st.get("phase")
    st["phase"] = "blocked"
    p.write_text(json.dumps(st))
    r = subprocess.run([sys.executable, "-c", _REBASELINE, str(g.ENGINE), str(p)], cwd=d, env=g.ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout


_LABELS = ("Who it's for:", "What they want:", "Done means:", "Out of scope:")


def _objective_range(output):
    """(start, end) line indices of `brief`'s objective block in its own output (the contiguous run of lines from
    the first "Who it's for:" line through the last bullet/label line under it), or None if there isn't one."""
    lines = output.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("Who it's for:"))
    except StopIteration:
        return None
    end = start
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if s == "":
            break
        if s.startswith(_LABELS) or s.startswith("- "):
            end = i
            continue
        break
    return start, end


def _noisy_whitespace(text):
    """The same words, in the same order, as `text`, but threaded together with a rotating mix of doubled spaces,
    tabs, a tab/space mix, and newlines at different points than the original -- re-wrapped at a different width
    with whitespace noise introduced, never adding, dropping or reordering a word. Collapsing runs of whitespace
    to single spaces gives back the exact same normalized text as collapsing `text` itself."""
    words = text.split()
    seps = ["  ", "\t", " \t ", "   ", "\n", "\n  ", " "]
    out = []
    for i, w in enumerate(words):
        out.append(w)
        if i != len(words) - 1:
            out.append(seps[i % len(seps)])
    return "".join(out)


def _noisy_brief(output):
    """`output` (brief's own stdout) with only its objective block re-wrapped by `_noisy_whitespace`; the header
    line and the role's rules that follow are left exactly as brief printed them."""
    rng = _objective_range(output)
    assert rng, output
    start, end = rng
    lines = output.splitlines()
    noisy_block = _noisy_whitespace("\n".join(lines[start:end + 1]))
    return "\n".join(lines[:start] + [noisy_block] + lines[end + 1:])


# ------------------------------------------------------------------------------------------------ cases

def t01_implementer_launch_without_objective_is_refused():
    r = task_guard(_PROJECT, "ratchet-implementer", MISSING_OBJECTIVE_PROMPT)
    out = r.stdout + r.stderr
    check("[CP3-T01] launching ratchet-implementer with a prompt lacking the objective is refused (exit 2)",
          r.returncode == 2, out)
    check("[CP3-T01] stderr names the `brief CP1 --role implementer` command to fix the prompt with",
          re.search(r"brief\s+CP1\s+--role\s+implementer", out) is not None, out)


def t02_prefixed_subagent_type_recognized_same_way():
    r = task_guard(_PROJECT, "ratchet-loop:ratchet-fixer", MISSING_OBJECTIVE_PROMPT)
    out = r.stdout + r.stderr
    check("[CP3-T02] a plugin-prefixed subagent_type ('ratchet-loop:ratchet-fixer') is refused exactly like the "
          "unprefixed name (exit 2)", r.returncode == 2, out)
    check("[CP3-T02] stderr names the `brief CP1 --role fixer` command to fix the prompt with",
          re.search(r"brief\s+CP1\s+--role\s+fixer", out) is not None, out)


def t03_correct_launch_with_briefs_output_never_refused():
    br = g.rx(_PROJECT, "brief", "CP1", "--role", "implementer")
    check("[CP3-T03] (fixture sanity) `brief CP1 --role implementer` exits 0 and prints something",
          br.returncode == 0 and bool(br.stdout.strip()), br.stdout + br.stderr)
    prompt = br.stdout + "\n\nGo implement checkpoint CP1 now."
    r = task_guard(_PROJECT, "ratchet-implementer", prompt)
    check("[CP3-T03] a launch whose prompt starts with brief's own output is never refused (exit 0)",
          r.returncode == 0, r.stdout + r.stderr)


def t04_whitespace_noise_in_objective_never_refuses():
    br = g.rx(_PROJECT, "brief", "CP1", "--role", "implementer")
    check("[CP3-T04] (fixture sanity) `brief CP1 --role implementer` exits 0", br.returncode == 0, br.stderr)
    noisy = _noisy_brief(br.stdout)
    check("[CP3-T04] (fixture sanity) the noisy objective text differs from brief's own formatting",
          noisy != br.stdout, (noisy, br.stdout))
    check("[CP3-T04] (fixture sanity) the noisy text carries the exact same words as brief's output, just "
          "rewrapped with doubled spaces and a tab/space mix",
          re.sub(r"\s+", " ", noisy).strip() == re.sub(r"\s+", " ", br.stdout).strip(), (noisy, br.stdout))
    prompt = noisy + "\n\nGo implement checkpoint CP1 now."
    r = task_guard(_PROJECT, "ratchet-implementer", prompt)
    check("[CP3-T04] whitespace/line-wrap noise in the objective header never causes a refusal (exit 0)",
          r.returncode == 0, r.stdout + r.stderr)


def t05_non_ratchet_subagent_never_refused():
    for subagent in ("general-purpose", "Explore"):
        r = task_guard(_PROJECT, subagent, NO_OBJECTIVE_AT_ALL_PROMPT)
        check(f"[CP3-T05] a non-Ratchet sub-agent ('{subagent}') with no objective text at all is never refused "
              "(exit 0)", r.returncode == 0, r.stdout + r.stderr)


def t06_no_refusal_while_not_building():
    d = g.project("brief-guard-t06", cps=1)  # plan-submit only: phase is awaiting_plan_approval, not building
    r = task_guard(d, "ratchet-implementer", MISSING_OBJECTIVE_PROMPT)
    check("[CP3-T06] the same missing-objective launch is never refused while the run isn't building (exit 0)",
          r.returncode == 0, r.stdout + r.stderr)


def t07_enforced_while_blocked_too():
    d = _building_project("brief-guard-t07")
    _force_phase_blocked(d)
    r = task_guard(d, "ratchet-implementer", MISSING_OBJECTIVE_PROMPT)
    check("[CP3-T07] the check still blocks (exit 2) while the run is 'blocked', the same as while 'building'",
          r.returncode == 2, r.stdout + r.stderr)


def t08_codex_host_never_refused():
    r = task_guard(_PROJECT, "ratchet-implementer", MISSING_OBJECTIVE_PROMPT, host="codex",
                   extra_payload={"turn_id": "turn-1"})
    check("[CP3-T08] the same missing-objective launch on a Codex-shaped payload (turn_id present, --host codex) "
          "is never refused (exit 0): this guard check is Claude-Code-only", r.returncode == 0,
          r.stdout + r.stderr)


def t09_guard_crash_fails_open_and_is_recorded():
    d = _building_project("brief-guard-t09")
    cp_path = Path(d) / ".ratchet" / "runs" / "f" / "checkpoints.json"
    cp_path.write_text("{not valid json at all, this is garbage")
    r = task_guard(d, "ratchet-implementer", MISSING_OBJECTIVE_PROMPT)
    check("[CP3-T09] an unreadable plan fails the guard open (exit 0): the launch is still allowed",
          r.returncode == 0, r.stdout + r.stderr)
    errlog = Path(d) / ".ratchet" / "hook-errors.log"
    crashdir = Path(d) / ".ratchet" / "hook-crashes"
    crash_evidence = (errlog.exists() and errlog.read_text().strip() != "") or \
                     (crashdir.is_dir() and any(crashdir.iterdir()))
    check("[CP3-T09] the crash is recorded the same way any other hook crash is (hook-errors.log or a "
          "hook-crashes/ marker), never a silent, unrecorded pass", crash_evidence, "")


def _doc_windows(text, cli):
    """Every markdown table row naming `cli` (lines starting with '|', the whole row kept as one window), plus a
    local window of text around every other appearance of `cli` -- the two natural shapes a per-CLI enforcement
    note could take in these docs, whichever one CP3's writer picked. A character-radius window (not a
    blank-line-separated paragraph) is used for prose so one long, blank-line-free block of a doc (some of
    SKILL.md's numbered steps run on for pages with no blank line) can't make an unrelated mention of `cli`
    thousands of characters away from the real enforcement note look like a match."""
    windows = [l for l in text.splitlines() if l.strip().startswith("|") and cli in l]
    radius = 300
    for m in re.finditer(re.escape(cli), text):
        windows.append(text[max(0, m.start() - radius):m.end() + radius])
    return windows


def _cli_says(text, cli, enforced):
    for w in _doc_windows(text, cli):
        if not re.search(r"objective", w, re.I):
            continue
        if enforced and re.search(r"\b(enforced|yes)\b", w, re.I) and not re.search(r"not enforced", w, re.I):
            return True
        if not enforced and re.search(r"\b(not enforced|no)\b", w, re.I):
            return True
    return False


def t10_docs_state_which_clis_enforce_this():
    readme = (ROOT / "README.md").read_text()
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    for label, text in (("README.md", readme), ("SKILL.md", skill), ("schemas.md", schemas)):
        check(f"[CP3-T10] {label} says Claude Code enforces the no-objective sub-agent launch refusal",
              _cli_says(text, "Claude Code", enforced=True), "")
        for cli in ("Codex", "Gemini", "Copilot"):
            check(f"[CP3-T10] {label} says {cli} does not enforce it",
                  _cli_says(text, cli, enforced=False), "")


TESTS = [
    ("CP3-T01", t01_implementer_launch_without_objective_is_refused),
    ("CP3-T02", t02_prefixed_subagent_type_recognized_same_way),
    ("CP3-T03", t03_correct_launch_with_briefs_output_never_refused),
    ("CP3-T04", t04_whitespace_noise_in_objective_never_refuses),
    ("CP3-T05", t05_non_ratchet_subagent_never_refused),
    ("CP3-T06", t06_no_refusal_while_not_building),
    ("CP3-T07", t07_enforced_while_blocked_too),
    ("CP3-T08", t08_codex_host_never_refused),
    ("CP3-T09", t09_guard_crash_fails_open_and_is_recorded),
    ("CP3-T10", t10_docs_state_which_clis_enforce_this),
]


def tx01_agent_tool_name_launch_without_objective_is_refused():
    """[CP3-X01] Current Claude Code builds send PreToolUse tool_name "Agent" (not "Task") for a sub-agent launch;
    this must be refused exactly like the "Task" dialect t01 covers, and allowed once the prompt carries brief's
    objective."""
    r = guard_host(_PROJECT, "claude", "Agent",
                   {"subagent_type": "ratchet-implementer", "prompt": MISSING_OBJECTIVE_PROMPT})
    out = r.stdout + r.stderr
    check("[CP3-X01] launching ratchet-implementer via tool_name 'Agent' with a prompt lacking the objective is "
          "refused (exit 2)", r.returncode == 2, out)
    br = g.rx(_PROJECT, "brief", "CP1", "--role", "implementer")
    check("[CP3-X01] (fixture sanity) `brief CP1 --role implementer` exits 0", br.returncode == 0, br.stdout + br.stderr)
    prompt = br.stdout + "\n\nGo implement checkpoint CP1 now."
    r2 = guard_host(_PROJECT, "claude", "Agent", {"subagent_type": "ratchet-implementer", "prompt": prompt})
    check("[CP3-X01] the same tool_name 'Agent' launch with brief's own output in the prompt is never refused "
          "(exit 0)", r2.returncode == 0, r2.stdout + r2.stderr)


TESTS.append(("CP3-X01", tx01_agent_tool_name_launch_without_objective_is_refused))


def tx02_refusal_names_dollar_ratchet_not_bare_script():
    """[CP3-X02] The refusal must paste as a runnable command: it names `$RATCHET brief CPn --role <role>` (the
    form SKILL.md and the checkpoint notes use), never a bare `ratchet.py brief ...` call, which is not on PATH."""
    r = task_guard(_PROJECT, "ratchet-implementer", MISSING_OBJECTIVE_PROMPT)
    out = r.stdout + r.stderr
    check("[CP3-X02] the refusal names `$RATCHET brief CP1 --role implementer`",
          re.search(r"\$RATCHET\s+brief\s+CP1\s+--role\s+implementer", out) is not None, out)
    check("[CP3-X02] the refusal never names a bare `ratchet.py` call, which is not on PATH",
          "ratchet.py" not in out, out)


TESTS.append(("CP3-X02", tx02_refusal_names_dollar_ratchet_not_bare_script))


def tx03_readme_table_row_states_enforcement():
    """[CP3-X03] CP3-T10 accepts a prose mention anywhere near the CLI's name, so a table with no enforcement
    column would still pass it. The done_when asks for this in the per-CLI table itself: check the table row,
    not prose, naming each CLI."""
    readme = (ROOT / "README.md").read_text()
    rows = [l for l in readme.splitlines() if l.strip().startswith("|")]
    claude_rows = [l for l in rows if "Claude Code" in l]
    check("[CP3-X03] README's per-CLI table has a row for Claude Code that itself says the objective check is "
          "enforced", any(re.search(r"objective", l, re.I) and re.search(r"\b(enforced|yes)\b", l, re.I) and
                           not re.search(r"not enforced", l, re.I) for l in claude_rows), "\n".join(claude_rows))
    for cli in ("Codex", "Gemini", "Copilot"):
        cli_rows = [l for l in rows if cli in l]
        check(f"[CP3-X03] README's per-CLI table has a row for {cli} that itself says the objective check is not "
              "enforced", any(re.search(r"objective", l, re.I) and re.search(r"\b(not enforced|no)\b", l, re.I)
                               for l in cli_rows), "\n".join(cli_rows))


TESTS.append(("CP3-X03", tx03_readme_table_row_states_enforcement))


def write_report():
    import os
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
