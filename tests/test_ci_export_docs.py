#!/usr/bin/env python3
"""
CP3 tests: suggest the export at run end; document it.   python3 tests/test_ci_export_docs.py

Two kinds of case in one file.

(1) Run-end hint (T01, T02, T04, T05): drives a minimal one-checkpoint project through to its final review
using the same real-CLI-subprocess helpers tests/test_gates.py uses (`project`, `approve_plan`, `to_adversarial`,
`open_round`, `write_review`, `R0`, `say`, `rx`), copied here in a minimal form so this file stays independent
(the same choice tests/test_ci_export_write.py already made relative to tests/test_ci_export.py). Config sets
regression_command so there is something to export, and gates behavior/adversarial True so the real flow
(tests-written, red, implemented, behavior, adv-record, complete, human-approve) is exercised once; human_review
is "end" or "none" per case.

(2) Docs (T06-T09): README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/
schemas.md are read directly from the repo (no project fixture). README checks are scoped to the CI-export
section only (found by its own heading, sliced to the next heading of equal or higher level), since other lanes
edit unrelated parts of these same files. Checks look for meaning-bearing phrases (regex on words/ideas), never
a pinned exact sentence.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP3-T01", "status": "passed"|"failed"|"error"|"skipped", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
ENV = {k: v for k, v in os.environ.items() if k not in ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT")}
ENV["RATCHET_HOME"] = str(_tmp.mkdtemp(prefix="ratchet-ci-docs-home-"))
ENV["RATCHET_HOST_PROVIDER"] = "anthropic"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
CASE_CHECKS: dict[str, int] = {}
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP3-T\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ run-driving fixtures
# Minimal copies of the tests/test_gates.py helpers this file needs (project/rx/say/guard/approve_plan/
# to_adversarial/open_round/R0/write_review), kept independent rather than imported.

def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def rx(d, *args, env=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True,
                          env=env or ENV)


TRANSCRIPTS = Path(_tmp.mkdtemp(prefix="ratchet-ci-docs-transcripts-"))


def say(d, text, session="s-owner"):
    tp = TRANSCRIPTS / f"{Path(d).name}-{session}.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    return subprocess.run([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(d), "prompt": text, "session_id": session,
                                            "transcript_path": str(tp)}))


def guard(d, tool, tool_input, cwd=None, agent=None):
    payload = {"cwd": str(cwd or d), "tool_name": tool, "tool_input": tool_input}
    if agent:
        payload.update(agent_type=agent, agent_id="a-" + agent)
    return subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV, input=json.dumps(payload))


def set_cfg(key, value) -> str:
    return f"{key}={json.dumps(value)}"


def project(name, config=None, human_review=None) -> Path:
    """`human_review`, when given, is passed to `init --human-review` itself (a later `config --set
    human_review=...` would not change it: cmd_complete/cmd_human_approve read it from state.json, which
    snapshots config['human_review'] only once, at init time -- ratchet.py:3321)."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-ci-docs-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    say(d, "please build the feature")
    init_args = ["init", "feature", "--slug", "f"]
    if human_review:
        init_args += ["--human-review", human_review]
    r = rx(d, *init_args)
    assert r.returncode == 0, r.stderr
    rx(d, "config", "--set", "min_review_seconds=0", "providers=[]", *(config or []))
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": []}
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    plan["checkpoints"].append(cp)
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def approve_plan(d):
    rx(d, "ask", "approve-plan")
    say(d, "yes, go ahead")
    r = rx(d, "approve-plan")
    assert r.returncode == 0, r.stderr
    return r


def write_tests(d, cp="CP1"):
    (d / f"tests/{cp.lower()}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f.js';\n"
        f"t('[{cp}-T01] works', () => a.equal(f(), 1));\n")


def to_adversarial(d, cp="CP1"):
    write_tests(d, cp)
    assert rx(d, "tests-written", cp).returncode == 0
    assert rx(d, "red", cp).returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert rx(d, "implemented", cp).returncode == 0
    r = rx(d, "behavior", cp)
    assert r.returncode == 0, r.stderr


def open_round(d, cp="CP1"):
    r = rx(d, "reviewers", cp, "--json")
    if r.returncode != 0 and "no fix-or-rebut" in r.stderr:
        m = re.search(r"no fix-or-rebut decision: (.+?)\. Run", r.stderr)
        for ref in (m.group(1).split(", ") if m else []):
            rx(d, "triage", cp, f'{ref}=fix:"test fixture"')
        r = rx(d, "reviewers", cp, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def R0(rnd):
    return rnd["reviewers"][0]["id"]


def _default_files_reviewed(d, rnd, slug="f"):
    try:
        st = json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())
        base = (st["checkpoints"].get(rnd["checkpoint"]) or {}).get("base_tree")
        if not base:
            return ["src/f.js"]
        fp = json.loads(rx(d, "fingerprint").stdout)["id"]
        r = git(d, "diff-tree", "-r", "--no-renames", "--name-only", base, fp)
        files = [l for l in r.stdout.splitlines() if l]
        return files or ["src/f.js"]
    except Exception:
        return ["src/f.js"]


def write_review(d, rnd, rid, findings=None, **over):
    p = Path(rnd["output_dir"]) / f"{rid}.json"
    g = guard(d, "Write", {"file_path": str(p)}, agent="ratchet-adversary")
    assert g.returncode == 0, g.stdout + g.stderr
    body = {"reviewer": rid, "checkpoint": rnd["checkpoint"], "fingerprint": rnd["fingerprint"],
            "round_nonce": rnd["round_nonce"], "findings": findings or [],
            "files_reviewed": _default_files_reviewed(d, rnd)}
    body.update(over)
    p.write_text(json.dumps(body))
    return str(p)


def drive_to_end(d, cp="CP1", human_review="end"):
    """Drives a project built by `project()` through approve-plan, tests-written/red/implemented/behavior,
    one adversarial round, and `complete` for its only checkpoint. With human_review="end" this also drives
    the final `human-approve` that finishes the run; its result is returned. With "none", `complete` alone
    finishes the run and its own result is returned."""
    approve_plan(d)
    to_adversarial(d, cp)
    rnd = open_round(d, cp)
    rr = rx(d, "adv-record", cp, "--reviews", write_review(d, rnd, R0(rnd)))
    assert rr.returncode == 0, rr.stderr + rr.stdout
    rc = rx(d, "complete", cp)
    assert rc.returncode == 0, rc.stderr + rc.stdout
    if human_review == "none":
        return rc
    rx(d, "ask", "human-approve")
    say(d, "approved")
    rf = rx(d, "human-approve")
    assert rf.returncode == 0, rf.stderr + rf.stdout
    return rf


def _ci_export_lines(stdout):
    return [ln for ln in stdout.splitlines() if "ci-export" in ln]


# ------------------------------------------------------------------------------------------------ T01, T02, T04, T05

def t01_final_human_approve_suggests_ci_export():
    d = project("t01", human_review="end",
                config=[set_cfg("regression_command", "node --test tests/cp1.test.js")])
    final = drive_to_end(d, human_review="end")
    lines = _ci_export_lines(final.stdout)
    check("[CP3-T01] the final human-approve call exits 0", final.returncode == 0, final.stderr)
    check("[CP3-T01] its own stdout contains exactly one line naming ci-export", len(lines) == 1, final.stdout)
    check("[CP3-T01] that line names ratchet.py ci-export, not a bare word",
          bool(lines) and "ratchet.py" in lines[0] and "ci-export" in lines[0], lines)


def t02_hint_on_completing_call_when_human_review_none():
    d = project("t02", human_review="none",
                config=[set_cfg("regression_command", "node --test tests/cp1.test.js")])
    final = drive_to_end(d, human_review="none")
    lines = _ci_export_lines(final.stdout)
    check("[CP3-T02] the completing `complete` call exits 0", final.returncode == 0, final.stderr)
    check("[CP3-T02] that call's own stdout contains the ci-export suggestion line", len(lines) == 1, final.stdout)
    st = json.loads(rx(d, "status", "--json").stdout)
    check("[CP3-T02] the run finished (phase done) on `complete` alone, with no human-approve call made",
          st.get("phase") == "done", st.get("phase"))


def t04_hint_asks_nothing_new():
    d = project("t04", human_review="end",
                config=[set_cfg("regression_command", "node --test tests/cp1.test.js")])
    drive_to_end(d, human_review="end")
    state = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    st = json.loads(rx(d, "status", "--json").stdout)
    check("[CP3-T04] state.json's pending is empty right after the hint was printed",
          not state.get("pending"), state.get("pending"))
    check("[CP3-T04] status --json reports no one waiting on a reply", not st.get("waiting_on"), st.get("waiting_on"))
    check("[CP3-T04] status --json's reported next action does not itself open a ci-export question",
          "ci-export" not in str(st.get("next", "")).lower(), st.get("next"))


def t05_hint_not_repeated_on_later_status():
    d = project("t05", human_review="end",
                config=[set_cfg("regression_command", "node --test tests/cp1.test.js")])
    final = drive_to_end(d, human_review="end")
    check("[CP3-T05] the final human-approve call did print the hint once",
          len(_ci_export_lines(final.stdout)) == 1, final.stdout)
    rstatus = rx(d, "status", "--json")
    rnext = rx(d, "next")
    check("[CP3-T05] a later `status --json` read does not mention ci-export again",
          "ci-export" not in rstatus.stdout.lower(), rstatus.stdout)
    check("[CP3-T05] a later `next` read does not mention ci-export again",
          "ci-export" not in rnext.stdout.lower(), rnext.stdout)


# ------------------------------------------------------------------------------------------------ doc fixtures (T06-T09)

CI_HEADING_RE = re.compile(r"ci[\s-]?export", re.I)


def doc_section(text, heading_re=CI_HEADING_RE):
    """The markdown section whose heading mentions `heading_re` (e.g. 'CI export'), sliced from that heading
    to the next heading of equal or higher level. Returns "" if no such heading exists."""
    lines = text.splitlines(keepends=True)
    start = level = None
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m and heading_re.search(m.group(2)):
            start, level = i, len(m.group(1))
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    return "".join(lines[start:end])


README_TEXT = (ROOT / "README.md").read_text(encoding="utf-8")
SKILL_TEXT = (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text(encoding="utf-8")
SCHEMAS_TEXT = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text(encoding="utf-8")
README_CI_SECTION = doc_section(README_TEXT)


def t06_readme_says_what_runs_in_ci():
    sec = README_CI_SECTION
    low = sec.lower()
    check("[CP3-T06] README has a CI-export section at all", bool(sec), README_TEXT[:200])
    check("[CP3-T06] it tells the developer to ask their agent for CI",
          re.search(r"ask\s+(your|the)\s+agent", low) is not None, low[:600])
    check("[CP3-T06] it says the workflow runs the project's regression/test command",
          ("regression" in low or "test command" in low or "tests" in low), low[:600])
    check("[CP3-T06] it says the workflow runs the automatic checks saved from guardrail steps",
          ("automatic check" in low or ("guardrail" in low and "check" in low) or "saved check" in low), low[:600])
    check("[CP3-T06] it says this runs on push and on pull requests",
          "push" in low and "pull request" in low, low[:600])
    check("[CP3-T06] the section gives the developer no ratchet.py command line to type",
          "ratchet.py" not in sec, sec)


def t07_readme_explains_ai_gates_and_hand_written_protection():
    sec = README_CI_SECTION
    low = sec.lower()
    check("[CP3-T07] it names adversarial review", "adversarial review" in low, low[:800])
    check("[CP3-T07] it names UI review", "ui review" in low, low[:800])
    check("[CP3-T07] it names human review", "human review" in low, low[:800])
    check("[CP3-T07] it says those gates do not run in CI",
          re.search(r"(doesn.?t|does not|aren.?t|are not|not run)\b.{0,40}\bci\b", low) is not None
          or re.search(r"\bci\b.{0,40}(doesn.?t|does not|can.?t|cannot)", low) is not None, low[:800])
    check("[CP3-T07] it says why: CI machines lack the agent CLIs/credentials those gates need",
          "credential" in low and ("cli" in low or "agent" in low), low[:800])
    check("[CP3-T07] it says a hand-written workflow already at that path is left alone (never replaced)",
          ("hand-written" in low or "hand written" in low)
          and re.search(r"never replac|not replac|won.?t replace|left alone|leaves? it alone|leaves? .{0,20}unchanged",
                        low) is not None, low[:800])
    check("[CP3-T07] it says the agent can show the file instead",
          re.search(r"\b(show|print)\b.{0,40}\b(file|workflow)\b", low) is not None, low[:800])


def t08_skill_and_schemas_mention_ci_export_and_stdout():
    check("[CP3-T08] SKILL.md mentions the ci-export command", "ci-export" in SKILL_TEXT, SKILL_TEXT[:200])
    check("[CP3-T08] SKILL.md mentions --stdout", "--stdout" in SKILL_TEXT, SKILL_TEXT[:200])
    check("[CP3-T08] schemas.md mentions the ci-export command", "ci-export" in SCHEMAS_TEXT, SCHEMAS_TEXT[:200])
    check("[CP3-T08] schemas.md mentions --stdout", "--stdout" in SCHEMAS_TEXT, SCHEMAS_TEXT[:200])

    def mentions_telling_developer(text):
        for m in re.finditer(r"ci[\s-]?export", text, re.I):
            window = text[max(0, m.start() - 300):m.end() + 300]
            if re.search(r"developer", window, re.I) and re.search(r"mention|tell|say", window, re.I):
                return True
        return False

    check("[CP3-T08] the skill tells the agent to mention CI export to the developer in plain words, near "
          "the end of a run", mentions_telling_developer(SKILL_TEXT), SKILL_TEXT[:200])


def t09_workflow_header_says_to_regenerate_via_agent():
    d = project("t09", config=[set_cfg("regression_command", "true # regression")])
    r = rx(d, "ci-export", "--stdout")
    check("[CP3-T09] ci-export --stdout exits 0", r.returncode == 0, r.stderr + r.stdout)
    header = r.stdout.split("\non:")[0] if "\non:" in r.stdout else r.stdout
    check("[CP3-T09] the header says to regenerate the file",
          re.search(r"regenerat", header, re.I) is not None, header)
    check("[CP3-T09] the header says to do that by asking the agent",
          re.search(r"regenerat", header, re.I) is not None and re.search(r"\bagent\b", header, re.I) is not None,
          header)
    check("[CP3-T09] the header does not instruct a person to run a ratchet.py command",
          re.search(r"\brun\b[^\n]{0,30}ratchet\.py", header, re.I) is None, header)


TESTS = [
    ("CP3-T01", t01_final_human_approve_suggests_ci_export),
    ("CP3-T02", t02_hint_on_completing_call_when_human_review_none),
    ("CP3-T04", t04_hint_asks_nothing_new),
    ("CP3-T05", t05_hint_not_repeated_on_later_status),
    ("CP3-T06", t06_readme_says_what_runs_in_ci),
    ("CP3-T07", t07_readme_explains_ai_gates_and_hand_written_protection),
    ("CP3-T08", t08_skill_and_schemas_mention_ci_export_and_stdout),
    ("CP3-T09", t09_workflow_header_says_to_regenerate_via_agent),
]


def t03_hint_absent_once_workflow_up_to_date():
    d = project("t03", human_review="end",
                config=[set_cfg("regression_command", "node --test tests/cp1.test.js")])
    rpre = rx(d, "ci-export")
    check("[CP3-T03] ci-export, run once before driving the run through review, exits 0", rpre.returncode == 0,
          rpre.stderr + rpre.stdout)
    final = drive_to_end(d, human_review="end")
    check("[CP3-T03] the final human-approve call still exits 0", final.returncode == 0, final.stderr)
    check("[CP3-T03] its stdout does not mention ci-export (the workflow was already up to date)",
          "ci-export" not in final.stdout.lower(), final.stdout)
    check("[CP3-T03] its stdout does not suggest running an export (no bare 'export' hint either)",
          not re.search(r"\bexport\s+ci\b|\bset\s*up\s*ci\b", final.stdout, re.I), final.stdout)


TESTS.append(("CP3-T03", t03_hint_absent_once_workflow_up_to_date))


def f01_hint_on_complete_without_regression_command_or_saved_checks():
    """[CP3-F01] human_review=none, no regression_command, no saved guardrail checks, one checkpoint with a
    tests.command: the completing `complete` call must still see that checkpoint's command (now persisted by
    the time the hint runs) and print the hint exactly once."""
    d = project("f01", human_review="none")
    final = drive_to_end(d, human_review="none")
    lines = _ci_export_lines(final.stdout)
    check("[CP3-F01] the completing `complete` call exits 0", final.returncode == 0, final.stderr)
    check("[CP3-F01] its own stdout contains the ci-export suggestion line exactly once", len(lines) == 1,
          final.stdout)


TESTS.append(("CP3-F01", f01_hint_on_complete_without_regression_command_or_saved_checks))

# check() reads the module-global ID_RE at call time, so widen it here to also record CP3-F ids.
ID_RE = re.compile(r"\[(CP3-[TF]\d+[a-z]?)\]")


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return

    def status(cid):
        if cid in CASE_SKIPPED:
            return "skipped"
        return "passed" if CASE_STATUS.get(cid, False) else "failed"

    cases = [{"id": cid, "status": status(cid),
              "message": "not run: it made no checks" if status(cid) == "skipped"
              else "" if status(cid) == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        else:
            if not CASE_CHECKS.get(cid):
                CASE_SKIPPED.add(cid)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
