#!/usr/bin/env python3
"""
CP2 tests: Ratchet writes every sub-agent brief header.   python3 tests/test_brief.py

`ratchet.py brief CPn --role <role>` is the one source every sub-agent's opening text comes from: the plan's
objective, word for word as `plan-summary` prints it (or the plain no-objective note for a legacy plan with no
`objective` key at all), followed by that role's own standing rules (implementer, fixer, test-writer, reviewer,
scout, planner each get their own, never a copy of another role's). CPn is optional: scout and planner can be
briefed before any checkpoint exists. An unknown role, or a CPn not in the plan, is refused with a plain message
naming the problem; a known CPn is named plainly at the top of its own brief.

Reuses tests/test_gates.py (`project`, `approve_plan`, `rx`, `say`, `git`, `ENV`, `ENGINE`) for the throwaway git
projects and the real-CLI-subprocess, private-RATCHET_HOME way every suite in this repo drives the engine, and
tests/test_limits.py's `_legacy_approve` (the engine's own pure helper functions, `_plan_hash`/`new_cp_state`/
`approval_snapshot`, used as a library) to fabricate the one history the CLI itself can't produce today: a plan
approved with no `objective` key at all (a run from before CP1's objective rule existed).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, rx, say, git, ENV, ENGINE)
import test_limits as tl        # noqa: E402  (_legacy_approve: fabricates a pre-objective-rule approved plan)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+[a-z]?)\]")

ROLES = ["implementer", "fixer", "test-writer", "reviewer", "scout", "planner"]


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

_LABELS = ("Who it's for:", "What they want:", "Done means:", "Out of scope:")


def _objective_block(text):
    """Every contiguous line, starting from the first "Who it's for:" line, that is either one of the four
    known objective labels or a "- ..." bullet under one of them. Stops at the first line that is neither (a
    blank line, or the start of a role's own rules / the per-step summary), so this works unchanged on both
    `plan-summary`'s output and `brief`'s, which share the same four labels."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("Who it's for:"))
    except StopIteration:
        return []
    out = []
    for l in lines[start:]:
        s = l.strip()
        if s == "":
            break
        if s.startswith(_LABELS) or s.startswith("- "):
            out.append(s)
            continue
        break
    return out


def _rules_text(output, after_objective=True):
    """Everything brief prints after its objective block (or the whole output, for a role whose brief has no
    objective block at all) -- the role-specific rules section a case inspects for its own keywords."""
    lines = output.splitlines()
    obj = _objective_block(output) if after_objective else []
    if not obj:
        return output
    try:
        last_idx = next(i for i in range(len(lines) - 1, -1, -1) if lines[i].strip() == obj[-1])
    except StopIteration:
        return output
    return "\n".join(lines[last_idx + 1:])


_PROJECT = g.project("brief", cps=2)
g.approve_plan(_PROJECT)


def _plan_summary_objective(d=_PROJECT):
    r = g.rx(d, "plan-summary")
    assert r.returncode == 0, r.stdout + r.stderr  # existing, stable behaviour
    return _objective_block(r.stdout)


def _bare_no_objective_project(name):
    """A fresh project whose plan.json has no top-level 'objective' key at all, built directly (not through
    plan-submit, which would refuse a plan missing today's required objective) and fabricated into an approved,
    building run with test_limits._legacy_approve -- the engine's own pure helpers, skipping only the objective
    rule itself, the one history a legacy (pre-rule) approved run can have but the CLI can no longer produce."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-brief-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr  # init itself is existing, unrelated behaviour
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    plan = {"feature": "x", "checkpoints": [cp]}  # deliberately: no "objective" key at all
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    tl._legacy_approve(d)
    return d


# ------------------------------------------------------------------------------------------------ cases

def t01_implementer_objective_matches_plan_summary_then_rules():
    r = g.rx(_PROJECT, "brief", "CP1", "--role", "implementer")
    check("[CP2-T01] `brief CP1 --role implementer` exits 0", r.returncode == 0, r.stdout + r.stderr)
    brief_obj = _objective_block(r.stdout)
    summary_obj = _plan_summary_objective()
    check("[CP2-T01] the objective lines are non-empty", bool(brief_obj) and bool(summary_obj), (brief_obj, summary_obj))
    check("[CP2-T01] the objective lines match plan-summary's, word for word",
          brief_obj == summary_obj, (brief_obj, summary_obj))
    rules = _rules_text(r.stdout)
    check("[CP2-T01] the implementer is told to make the smallest change",
          re.search(r"smallest change", rules, re.I) is not None, rules)
    check("[CP2-T01] the implementer is told to add no new mechanism or file unless needed",
          re.search(r"new mechanism", rules, re.I) is not None, rules)
    check("[CP2-T01] the implementer is told not to run the full test suite unless asked",
          re.search(r"full (?:test )?suite", rules, re.I) is not None
          and re.search(r"unless asked", rules, re.I) is not None, rules)


def t02_fixer_gets_smallest_fix_rule():
    r = g.rx(_PROJECT, "brief", "CP1", "--role", "fixer")
    check("[CP2-T02] `brief CP1 --role fixer` exits 0", r.returncode == 0, r.stdout + r.stderr)
    brief_obj = _objective_block(r.stdout)
    check("[CP2-T02] the objective lines match plan-summary's, word for word",
          brief_obj == _plan_summary_objective(), (brief_obj, _plan_summary_objective()))
    rules = _rules_text(r.stdout)
    check("[CP2-T02] the fixer is told to make the smallest change that resolves each finding",
          re.search(r"smallest change", rules, re.I) is not None, rules)
    check("[CP2-T02] the fixer is told to add no new mechanism or file unless the finding needs it",
          re.search(r"new mechanism", rules, re.I) is not None, rules)
    check("[CP2-T02] the fixer is told to stop and say so when a proper fix needs a redesign",
          re.search(r"redesign", rules, re.I) is not None, rules)
    fixer_md = (ROOT / "agents" / "ratchet-fixer.md").read_text()
    check("[CP2-T02] the fixer's brief wording is consistent with agents/ratchet-fixer.md's own smallest-fix text",
          re.search(r"smallest change", fixer_md, re.I) is not None, "")


def t03_reviewer_states_realistic_use_bar_and_named_harm():
    r = g.rx(_PROJECT, "brief", "CP1", "--role", "reviewer")
    check("[CP2-T03] `brief CP1 --role reviewer` exits 0", r.returncode == 0, r.stdout + r.stderr)
    brief_obj = _objective_block(r.stdout)
    check("[CP2-T03] the objective lines match plan-summary's, word for word",
          brief_obj == _plan_summary_objective(), (brief_obj, _plan_summary_objective()))
    rules = _rules_text(r.stdout)
    check("[CP2-T03] the reviewer is told to judge findings against a realistic-use bar",
          re.search(r"realistic[- ]use", rules, re.I) is not None, rules)
    check("[CP2-T03] the reviewer is told the user runs a cooperative agent, not an attacker",
          re.search(r"cooperative", rules, re.I) is not None, rules)
    check("[CP2-T03] the reviewer is told every blocking finding must say who is hurt and how",
          re.search(r"who", rules, re.I) is not None and re.search(r"hurt|harm", rules, re.I) is not None, rules)


def t04_each_roles_rules_are_its_own_text():
    texts = {}
    for role in ROLES:
        r = g.rx(_PROJECT, "brief", "CP1", "--role", role)
        check(f"[CP2-T04] briefing role '{role}' exits 0", r.returncode == 0, r.stdout + r.stderr)
        texts[role] = _rules_text(r.stdout).strip()
    check("[CP2-T04] every role got some non-empty rules text",
          all(texts.values()), texts)
    check("[CP2-T04] every role's rules text is distinct from every other role's",
          len(set(texts.values())) == len(ROLES), texts)


def t05_scout_and_planner_brief_with_no_checkpoint():
    for role in ("scout", "planner"):
        r = g.rx(_PROJECT, "brief", "--role", role)
        check(f"[CP2-T05] `brief --role {role}` (no CPn) exits 0", r.returncode == 0, r.stdout + r.stderr)
        obj = _objective_block(r.stdout)
        check(f"[CP2-T05] `brief --role {role}` prints the plan's objective",
              bool(obj), r.stdout + r.stderr)
        rules = _rules_text(r.stdout)
        check(f"[CP2-T05] `brief --role {role}` prints that role's own rules after the objective",
              bool(rules.strip()), r.stdout + r.stderr)


def t06_unknown_role_refused_with_valid_list():
    r = g.rx(_PROJECT, "brief", "CP1", "--role", "wizard")
    out = r.stdout + r.stderr
    check("[CP2-T06] an unknown role exits non-zero", r.returncode != 0, out)
    check("[CP2-T06] the message names 'wizard' as not a valid role", "wizard" in out, out)
    for role in ROLES:
        check(f"[CP2-T06] the message lists valid role '{role}'", role in out, out)


def t07_plan_with_no_objective_gets_plain_note():
    d = _bare_no_objective_project("t07")
    r = g.rx(d, "brief", "CP1", "--role", "implementer")
    out = r.stdout + r.stderr
    check("[CP2-T07] briefing a plan with no objective key does not crash or exit non-zero for that reason",
          r.returncode == 0, out)
    check("[CP2-T07] the output carries the plain no-objective note",
          re.search(r"no objective", out, re.I) is not None, out)
    rules = _rules_text(out, after_objective=False)
    check("[CP2-T07] the implementer's rules still follow the no-objective note",
          re.search(r"smallest change", rules, re.I) is not None, rules)


def t08_unknown_cpn_refused_valid_cpn_named():
    r = g.rx(_PROJECT, "brief", "CP9", "--role", "implementer")
    out = r.stdout + r.stderr
    check("[CP2-T08] a CPn not in the plan exits non-zero", r.returncode != 0, out)
    check("[CP2-T08] the message names CP9 as not part of this plan", "CP9" in out, out)
    r1 = g.rx(_PROJECT, "brief", "CP1", "--role", "implementer")
    check("[CP2-T08] a valid CPn (CP1) is named plainly at the top of its own brief",
          r1.returncode == 0 and re.search(r"\bCP1\b", r1.stdout.splitlines()[0] if r1.stdout.splitlines() else ""),
          r1.stdout[:200])
    r2 = g.rx(_PROJECT, "brief", "CP2", "--role", "implementer")
    check("[CP2-T08] a valid CPn (CP2) is named plainly at the top of its own brief",
          r2.returncode == 0 and re.search(r"\bCP2\b", r2.stdout.splitlines()[0] if r2.stdout.splitlines() else ""),
          r2.stdout[:200])


def t09_docs_describe_the_brief_command():
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    readme = (ROOT / "README.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP2-T09] the orchestrator skill tells the orchestrator to start every spawn prompt with "
          "`ratchet.py brief CPn --role <role>`'s output instead of writing the objective by hand",
          re.search(r"brief\s+CPn\s+--role", skill) is not None, "")
    check("[CP2-T09] the orchestrator skill no longer tells the orchestrator to write the objective by hand",
          re.search(r"every spawn prompt includes the plan's objective", skill, re.I) is None, "")
    check("[CP2-T09] README mentions the `brief` command and what it prints",
          re.search(r"ratchet\.py brief\b", readme) is not None, "")
    check("[CP2-T09] schemas.md mentions the `brief` command",
          re.search(r"ratchet\.py brief\b", schemas) is not None, "")


# ------------------------------------------------------------------------------------------------ CP2-X01
# (adversarial round 01 fix): `brief --role scout`/`--role planner` must work right after `init`, before
# checkpoints.json exists at all -- not merely before a CPn is named against an already-approved plan (t05's
# case). This reproduces the real first-brief-of-a-run case the fix list named.

def _fresh_init_only_project(name):
    """A project right after `init`, with no checkpoints.json written at all -- the exact state the orchestrator
    is in when SKILL.md tells it to spawn the scout and the planner in parallel, before either has run."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-brief-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    return d


def x01_brief_scout_and_planner_right_after_init():
    d = _fresh_init_only_project("x01")
    checkpoints_path = d / ".ratchet" / "runs" / "f" / "checkpoints.json"
    check("[CP2-X01] (fixture sanity) checkpoints.json does not exist yet, right after init",
          not checkpoints_path.exists(), str(checkpoints_path))
    for role in ("scout", "planner"):
        r = g.rx(d, "brief", "--role", role)
        check(f"[CP2-X01] `brief --role {role}` right after init (no checkpoints.json at all) exits 0",
              r.returncode == 0, r.stdout + r.stderr)
        rules = _rules_text(r.stdout)
        check(f"[CP2-X01] `brief --role {role}` right after init still prints that role's own rules",
              bool(rules.strip()), r.stdout + r.stderr)


# ------------------------------------------------------------------------------------------------ CP2-X02
# (adversarial round 03 fix, copilot-correctness/C1): every agent the orchestrator actually spawns (agents/*.md)
# must get a `--role` token `brief` accepts, not just the original 6 role words -- SKILL.md tells the orchestrator
# to start every spawn with `brief`'s output, and it spawns ratchet-test-planner, ratchet-test-runner,
# ratchet-design-system, ratchet-prototyper, ratchet-variant-writer and the two UI reviewers on top of those 6.

def x02_every_agents_dir_file_name_accepted_by_brief():
    agent_names = sorted(p.stem for p in (ROOT / "agents").glob("*.md"))
    check("[CP2-X02] (fixture sanity) agents/ holds more than the 6 original role names",
          len(agent_names) > len(ROLES), agent_names)
    for name in agent_names:
        r = g.rx(_PROJECT, "brief", "CP1", "--role", name)
        check(f"[CP2-X02] `brief CP1 --role {name}` (the agents/{name}.md file name) exits 0",
              r.returncode == 0, r.stdout + r.stderr)
        obj = _objective_block(r.stdout)
        check(f"[CP2-X02] `brief CP1 --role {name}` prints the plan's objective", bool(obj), r.stdout + r.stderr)
        rules = _rules_text(r.stdout)
        check(f"[CP2-X02] `brief CP1 --role {name}` prints some rules text after the objective",
              bool(rules.strip()), r.stdout + r.stderr)


TESTS = [
    ("CP2-T01", t01_implementer_objective_matches_plan_summary_then_rules),
    ("CP2-T02", t02_fixer_gets_smallest_fix_rule),
    ("CP2-T03", t03_reviewer_states_realistic_use_bar_and_named_harm),
    ("CP2-T04", t04_each_roles_rules_are_its_own_text),
    ("CP2-T05", t05_scout_and_planner_brief_with_no_checkpoint),
    ("CP2-T06", t06_unknown_role_refused_with_valid_list),
    ("CP2-T07", t07_plan_with_no_objective_gets_plain_note),
    ("CP2-T08", t08_unknown_cpn_refused_valid_cpn_named),
    ("CP2-T09", t09_docs_describe_the_brief_command),
    ("CP2-X01", x01_brief_scout_and_planner_right_after_init),
    ("CP2-X02", x02_every_agents_dir_file_name_accepted_by_brief),
]


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
