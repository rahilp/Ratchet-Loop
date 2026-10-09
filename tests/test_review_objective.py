#!/usr/bin/env python3
"""
CP2 tests: reviewers and builders work toward the objective.   python3 tests/test_review_objective.py

Every reviewer, builder and fixer must be told the plan's objective first; reviewers must be told the
realistic-use bar; and a blocker or major (or unknown-severity) finding that does not say who is hurt and
how (`user_impact`) is recorded for the final review and never blocks, at any round and any risk. A run
whose plan carries no objective (a run approved before this change) still reviews normally, with a plain
note that none was stated.

Reuses tests/test_gates.py's project()/rx()/write_review() CLI-level fixtures (throwaway git repos,
RATCHET_HOME in a temp dir, providers=[] so no real agent CLI runs by default) and tests/test_providers.py's
fake-agent-CLI-on-a-private-PATH helpers for the other-company reviewer case.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402
import test_gates as g  # noqa: E402
import test_providers as tp  # noqa: E402
import ratchet as rt  # noqa: E402

os.environ["RATCHET_HOME"] = str(g.HOME)  # this file's direct (non-subprocess) engine calls share test_gates.py's
                                           # sandboxed per-user folder

ROOT = HERE.parent
PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+)\]")


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

def ids(rnd):
    return [r["id"] for r in rnd["reviewers"]]


def record(d, cp, rnd, findings_by_id):
    """Writes a review for every reviewer this round requires (empty unless findings_by_id names it), then
    calls adv-record. Returns the adv-record subprocess result."""
    paths = [g.write_review(d, rnd, rid, findings=findings_by_id.get(rid, [])) for rid in ids(rnd)]
    return g.rx(d, "adv-record", cp, "--reviews", *paths)


def deferred_file(d, cp, round_no, slug="f"):
    p = Path(d) / f".ratchet/runs/{slug}/evidence/{cp}/adversarial/round-{round_no:02d}/deferred-findings.json"
    return json.loads(p.read_text()) if p.exists() else []


def open_findings_file(d, cp, round_no, slug="f"):
    p = Path(d) / f".ratchet/runs/{slug}/evidence/{cp}/adversarial/round-{round_no:02d}/open-findings.json"
    return json.loads(p.read_text()) if p.exists() else []


def codex_project(name):
    """A project with a single working other-company reviewer ('codex', a fake CLI on a private PATH, as
    tests/test_providers.py builds it) registered as a provider. Returns (dir, env)."""
    b = tp.fake_bin("codex")
    env = tp.env_with(b)
    d = g.project(name)
    r = g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', env=env)
    assert r.returncode == 0, r.stderr
    return d, env


def _codex_dry_run_prompt(name):
    """Opens a round with the fake 'codex' reviewer and runs external_review.py --dry-run for it, returning
    (the dry-run result, the kept prompt file's text)."""
    d, env = codex_project(name)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    rid = g.R0(rnd)
    out = str(Path(d) / "dryrun-review.json")
    r = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", rid, "--cp", "CP1", "--out", out, "--dry-run"],
                        cwd=d, capture_output=True, text=True, env=env)
    m = re.search(r"prompt written: (\S+)", r.stdout)
    prompt = Path(m.group(1)).read_text() if m else ""
    return r, prompt


ADVERSARY_MARKER = "You are an **adversarial reviewer**"


# ------------------------------------------------------------------------------------------------ CP2-T01..T02: the other-company reviewer's prompt

def t01_prompt_opens_with_objective():
    r, prompt = _codex_dry_run_prompt("t01")
    check("[CP2-T01] external_review.py --dry-run runs and keeps the prompt file", r.returncode == 0 and bool(prompt),
          r.stdout + r.stderr)
    users_idx = prompt.find(_tmp.OBJECTIVE_MIN["users"])
    goal_idx = prompt.find(_tmp.OBJECTIVE_MIN["goal"])
    marker_idx = prompt.find(ADVERSARY_MARKER)
    check("[CP2-T01] the prompt contains the objective's users text", users_idx != -1, prompt[:600])
    check("[CP2-T01] the prompt contains the objective's goal text", goal_idx != -1, prompt[:600])
    check("[CP2-T01] the prompt contains the adversary review instructions", marker_idx != -1, prompt[:600])
    check("[CP2-T01] the objective's users text appears before the adversary review instructions",
          users_idx != -1 and marker_idx != -1 and users_idx < marker_idx, (users_idx, marker_idx))
    check("[CP2-T01] the objective's goal text appears before the adversary review instructions",
          goal_idx != -1 and marker_idx != -1 and goal_idx < marker_idx, (goal_idx, marker_idx))


def t02_reply_shape_asks_for_user_impact():
    r, prompt = _codex_dry_run_prompt("t02")
    assert r.returncode == 0, r.stdout + r.stderr
    idx = prompt.find('"findings":[{')
    chunk = prompt[idx:idx + 400] if idx != -1 else ""
    check("[CP2-T02] the reply shape's findings example is present in the prompt", idx != -1, prompt[-600:])
    check("[CP2-T02] that findings example still asks for severity", "severity" in chunk, chunk)
    check("[CP2-T02] that findings example also asks for user_impact", "user_impact" in chunk, chunk)


# ------------------------------------------------------------------------------------------------ CP2-T03..T04: ratchet.py reviewers

def t03_reviewers_prints_objective_first():
    d = g.project("t03")
    g.approve_plan(d)
    g.to_adversarial(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    check("[CP2-T03] --json carries an 'objective' field with the plan's full objective",
          j.get("objective") == _tmp.OBJECTIVE_MIN, j.get("objective"))
    plain = g.rx(d, "reviewers", "CP1").stdout
    goal_idx = plain.find(_tmp.OBJECTIVE_MIN["goal"])
    bullet_idx = plain.find("\n  - ")
    check("[CP2-T03] the plain-text output prints the objective's goal text", goal_idx != -1, plain)
    check("[CP2-T03] the plain-text output prints at least one per-reviewer row", bullet_idx != -1, plain)
    check("[CP2-T03] the objective is printed before the per-reviewer rows",
          goal_idx != -1 and bullet_idx != -1 and goal_idx < bullet_idx, (goal_idx, bullet_idx))


def t04_host_subagent_brief_states_objective():
    d = g.project("t04")  # providers=[] (test_gates.project() default): host sub-agent reviewers only
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    check("[CP2-T04] (fixture sanity) every reviewer this round is a host sub-agent",
          all(r.get("vendor") == "host" for r in rnd["reviewers"]), rnd["reviewers"])
    hows = " ".join(r.get("how", "") for r in rnd["reviewers"])
    check("[CP2-T04] the host sub-agent's launch instructions name the objective's users",
          _tmp.OBJECTIVE_MIN["users"] in hows, hows)
    check("[CP2-T04] the host sub-agent's launch instructions name the objective's goal",
          _tmp.OBJECTIVE_MIN["goal"] in hows, hows)


# ------------------------------------------------------------------------------------------------ CP2-T05..T09: the no-user-impact exemption

def t05_blocker_without_user_impact_never_blocks_even_high_risk():
    d = g.project("t05", extra_cp={"risk": "high"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    blocker_no_impact = {"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "crashes on null input"}
    r = record(d, "CP1", rnd, {ids(rnd)[0]: [blocker_no_impact]})
    check("[CP2-T05] high-risk step: a blocker finding with no user_impact does not block (gate approved)",
          r.returncode == 0, r.stdout + r.stderr)
    deferred = deferred_file(d, "CP1", 1)
    entry = next((x for x in deferred if x.get("id") == "B1"), None)
    check("[CP2-T05] the finding is written to the round's deferred list", entry is not None, deferred)
    check("[CP2-T05] its deferred_because is 'no user impact stated'",
          (entry or {}).get("deferred_because") == "no user impact stated", entry)


def t06_major_and_unknown_severity_without_impact_defer_the_same_way():
    d = g.project("t06", extra_cp={"risk": "high"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    rid0, rid1 = ids(rnd)[0], ids(rnd)[1]
    major_no_impact = {"id": "M1", "severity": "major", "file": "src/f.js", "description": "leaks a secret in logs"}
    unknown_no_impact = {"id": "U1", "severity": "critical", "file": "src/f.js", "description": "undefined behaviour"}
    r = record(d, "CP1", rnd, {rid0: [major_no_impact], rid1: [unknown_no_impact]})
    check("[CP2-T06] both a major and an unknown-severity finding with no user_impact pass the gate",
          r.returncode == 0, r.stdout + r.stderr)
    deferred = deferred_file(d, "CP1", 1)
    by_id = {x.get("id"): x for x in deferred}
    check("[CP2-T06] the major finding is deferred with 'no user impact stated'",
          by_id.get("M1", {}).get("deferred_because") == "no user impact stated", by_id.get("M1"))
    check("[CP2-T06] the unknown-severity finding is deferred with the same reason",
          by_id.get("U1", {}).get("deferred_because") == "no user impact stated", by_id.get("U1"))


def t07_no_impact_rule_holds_in_a_later_round():
    d = g.project("t07", extra_cp={"risk": "high"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    rid0 = ids(rnd)[0]
    blocker_with_impact = {"id": "B2", "severity": "blocker", "file": "src/f.js", "description": "sql injection",
                            "user_impact": "an attacker can read any other user's data"}
    blocker_no_impact = {"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "crashes on null input"}
    r1 = record(d, "CP1", rnd, {rid0: [blocker_with_impact, blocker_no_impact]})
    check("[CP2-T07] round 1 still blocks because of the finding that states a user impact",
          r1.returncode == 2, r1.stdout + r1.stderr)
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // fixed sqli\n")
    br = g.rx(d, "behavior", "CP1")
    assert br.returncode == 0, br.stderr
    rnd2 = g.open_round(d)
    r2 = record(d, "CP1", rnd2, {ids(rnd2)[0]: [blocker_no_impact]})
    check("[CP2-T07] round 2: the same no-impact blocker is deferred again and the gate passes",
          r2.returncode == 0, r2.stdout + r2.stderr)
    deferred = deferred_file(d, "CP1", 2)
    entry = next((x for x in deferred if x.get("id") == "B1"), None)
    check("[CP2-T07] its deferred_because is the same no-impact reason in round 2",
          (entry or {}).get("deferred_because") == "no user impact stated", entry)


def t08_blocker_with_user_impact_still_blocks():
    d = g.project("t08", extra_cp={"risk": "normal"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    rid0 = ids(rnd)[0]
    blocker_with_impact = {"id": "B3", "severity": "blocker", "file": "src/f.js", "description": "cart total overflow",
                            "user_impact": "a shopper's cart total is wrong and they are overcharged"}
    blocker_no_impact = {"id": "B4", "severity": "blocker", "file": "src/f.js", "description": "logs secrets"}
    r = record(d, "CP1", rnd, {rid0: [blocker_with_impact, blocker_no_impact]})
    check("[CP2-T08] the gate fails because of the finding that states a user impact", r.returncode == 2,
          r.stdout + r.stderr)
    open_findings = open_findings_file(d, "CP1", 1)
    check("[CP2-T08] exactly one finding blocks: the one with user_impact",
          [x.get("id") for x in open_findings] == ["B3"], open_findings)
    deferred = deferred_file(d, "CP1", 1)
    entry = next((x for x in deferred if x.get("id") == "B4"), None)
    check("[CP2-T08] the other (no user_impact) finding is deferred with 'no user impact stated'",
          (entry or {}).get("deferred_because") == "no user impact stated", entry)


def t09_minor_without_impact_keeps_existing_minor_rules():
    d = g.project("t09", extra_cp={"risk": "high"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    minor_no_impact = {"id": "N1", "severity": "minor", "file": "src/f.js", "description": "name could be clearer"}
    r = record(d, "CP1", rnd, {ids(rnd)[0]: [minor_no_impact]})
    check("[CP2-T09] a minor finding with no user_impact still blocks on a high-risk step (existing rule)",
          r.returncode == 2, r.stdout + r.stderr)
    open_findings = open_findings_file(d, "CP1", 1)
    check("[CP2-T09] it is the blocking finding", [x.get("id") for x in open_findings] == ["N1"], open_findings)
    deferred = deferred_file(d, "CP1", 1)
    entry = next((x for x in deferred if x.get("id") == "N1"), None)
    check("[CP2-T09] it is not exempted through the no-user-impact deferral (not on the deferred list)",
          entry is None, entry)


# ------------------------------------------------------------------------------------------------ CP2-T10: `ratchet.py deferred`

def t10_deferred_lists_the_no_impact_reason():
    d = g.project("t10", extra_cp={"risk": "high"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    blocker_no_impact = {"id": "B5", "severity": "blocker", "file": "src/f.js",
                          "description": "race condition on double-click"}
    r = record(d, "CP1", rnd, {ids(rnd)[0]: [blocker_no_impact]})
    assert r.returncode == 0, r.stdout + r.stderr
    plain = g.rx(d, "deferred").stdout
    check("[CP2-T10] `deferred` lists the finding in plain words", "race condition on double-click" in plain, plain)
    check("[CP2-T10] `deferred` shows the plain reason 'no user impact stated'", "no user impact stated" in plain,
          plain)
    j = json.loads(g.rx(d, "deferred", "--json").stdout)
    entry = next((x for x in j if x.get("id") == "B5"), None)
    check("[CP2-T10] `deferred --json` carries the same reason",
          (entry or {}).get("deferred_because") == "no user impact stated", entry)


# ------------------------------------------------------------------------------------------------ CP2-T11: a run with no stated objective

def legacy_no_objective_project(name):
    """A run approved before this feature existed: its plan has no 'objective' field at all, and it reached
    'awaiting_plan_approval' the way the old engine's plan-submit did (before CP1's objective_problems()
    existed). Built directly through the engine module (not the CLI, which now refuses a plan with no
    objective at CP1), the same way tests/test_objective.py's _legacy_project() does, never by hand-editing
    state.json's fields beyond what cmd_plan_submit itself used to write."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp2-legacy-{name}-"))
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
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "x", "checkpoints": [{
        "id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
        "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "normal",
        "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"},
    }]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    ctx = rt.Ctx(d)
    st = ctx.state()
    loaded_plan = ctx.plan()
    st["phase"] = "awaiting_plan_approval"
    st["awaiting_since"] = rt.now()
    st["plan_hash"] = rt._plan_hash(loaded_plan, ctx)
    st["checkpoints"] = {cp["id"]: st["checkpoints"].get(cp["id"]) or rt.new_cp_state(cp)
                         for cp in loaded_plan["checkpoints"]}
    ctx.log(st, "plan_submitted", hash=st["plan_hash"], count=len(loaded_plan["checkpoints"]))
    ctx.save_state(st)
    return d


def t11_run_with_no_objective_still_reviews_normally():
    d = legacy_no_objective_project("t11")
    g.approve_plan(d)
    g.to_adversarial(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    check("[CP2-T11] opening a review round for a plan with no objective does not crash",
          r.returncode == 0, r.stdout + r.stderr)
    combined = r.stdout + r.stderr
    check("[CP2-T11] the output carries a plain note that no objective was stated for this run",
          bool(re.search(r"no objective (was )?stated", combined, re.I)), combined)
    rnd = json.loads(r.stdout) if r.returncode == 0 else {}
    paths = [g.write_review(d, rnd, rid, findings=[]) for rid in [x["id"] for x in rnd.get("reviewers", [])]]
    r2 = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("[CP2-T11] the round still records and approves normally", r2.returncode == 0, r2.stdout + r2.stderr)


# ------------------------------------------------------------------------------------------------ CP2-T12: the realistic-use bar in the docs

ADV_MD = ROOT / "agents" / "ratchet-adversary.md"
ADV_LOOP_SKILL = ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md"


def t12_adversary_instructions_state_the_realistic_use_bar():
    texts = {}
    for name, path in (("agents/ratchet-adversary.md", ADV_MD),
                       ("skills/ratchet-adversarial-loop/SKILL.md", ADV_LOOP_SKILL)):
        try:
            texts[name] = path.read_text()
        except OSError as e:
            texts[name] = ""
            check(f"[CP2-T12] {name} can be read", False, str(e))
            continue
    for name, text in texts.items():
        low = text.lower()
        check(f"[CP2-T12] {name} says, in generic terms, who the project's users are",
              bool(re.search(r"(the|this) project'?s? users|users of (the|this|whatever) project", low)), name)
        check(f"[CP2-T12] {name} says severity follows real harm to users in normal use",
              "normal use" in low and bool(re.search(r"real (user )?harm", low)), name)
        check(f"[CP2-T12] {name} says deliberate same-user evasion is out of scope",
              "evasion" in low and bool(re.search(r"out of scope|not in scope|accepted limit", low)), name)


# ------------------------------------------------------------------------------------------------ CP2-T13: the objective at the top of every brief

BRIEF_FILES = {
    "agents/ratchet-implementer.md": ROOT / "agents" / "ratchet-implementer.md",
    "agents/ratchet-fixer.md": ROOT / "agents" / "ratchet-fixer.md",
    "agents/ratchet-test-writer.md": ROOT / "agents" / "ratchet-test-writer.md",
}
ORCH_SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"


def t13_every_subagent_brief_carries_the_objective():
    for name, path in BRIEF_FILES.items():
        text = path.read_text()
        check(f"[CP2-T13] {name} expects the plan's objective among its inputs",
              bool(re.search(r"objective", text, re.I)), name)
    orch = ORCH_SKILL.read_text()
    m = re.search(r"Every spawn prompt includes[^\n]*", orch)
    check("[CP2-T13] (fixture sanity) the orchestrator skill's generic spawn-prompt rule can be found",
          bool(m), orch[:200])
    check("[CP2-T13] that generic spawn-prompt rule includes the plan's objective",
          bool(m) and bool(re.search(r"objective", m.group(0), re.I)), m.group(0) if m else "")


# ------------------------------------------------------------------------------------------------ CP2-X01 (adversarial
# round 1, finding X1): the shipped reviewer guidance must not hard-code who the reviewed project's users are, and
# must send reviewers to the plan's objective for users, goal and out-of-scope instead.

HARDCODED_AUDIENCE_RX = re.compile(
    r"(this|the)\s+project'?s?\s+users\s+are\s+developers\s+running\s+it\s+with\s+a\s+cooperative\s+coding\s+agent",
    re.I)
TAKES_USERS_FROM_OBJECTIVE_RX = re.compile(
    r"objective['’]?s?\s+(own\s+)?`?out_of_scope`?|out[\s-]of[\s-]scope\s+comes\s+from\s+the\s+objective", re.I)


def x01_reviewer_guidance_does_not_hardcode_developer_audience():
    for name, path in (("agents/ratchet-adversary.md", ADV_MD),
                       ("skills/ratchet-adversarial-loop/SKILL.md", ADV_LOOP_SKILL)):
        text = path.read_text()
        check(f"[CP2-X01] {name} does not hard-code 'cooperative coding agent' developers as this project's users",
              HARDCODED_AUDIENCE_RX.search(text) is None, name)
        check(f"[CP2-X01] {name} tells reviewers to judge findings against the plan's objective's users",
              bool(re.search(r"objective", text, re.I)) and bool(re.search(r"users", text, re.I)), name)
        check(f"[CP2-X01] {name} says what is out of scope comes from the objective, not a fixed rule",
              TAKES_USERS_FROM_OBJECTIVE_RX.search(text) is not None, name)
        check(f"[CP2-X01] {name} still says deliberate same-user evasion is out of scope for Ratchet's own plans "
              "specifically (not every project's)",
              bool(re.search(r"ratchet'?s?\s+own", text, re.I)), name)
        check(f"[CP2-X01] {name} says a public-facing or malicious-user case can be in scope elsewhere",
              bool(re.search(r"public|malicious", text, re.I)), name)


# ------------------------------------------------------------------------------------------------ runner

TESTS = [
    ("CP2-T01", t01_prompt_opens_with_objective),
    ("CP2-T02", t02_reply_shape_asks_for_user_impact),
    ("CP2-T03", t03_reviewers_prints_objective_first),
    ("CP2-T04", t04_host_subagent_brief_states_objective),
    ("CP2-T05", t05_blocker_without_user_impact_never_blocks_even_high_risk),
    ("CP2-T06", t06_major_and_unknown_severity_without_impact_defer_the_same_way),
    ("CP2-T07", t07_no_impact_rule_holds_in_a_later_round),
    ("CP2-T08", t08_blocker_with_user_impact_still_blocks),
    ("CP2-T09", t09_minor_without_impact_keeps_existing_minor_rules),
    ("CP2-T10", t10_deferred_lists_the_no_impact_reason),
    ("CP2-T11", t11_run_with_no_objective_still_reviews_normally),
    ("CP2-T12", t12_adversary_instructions_state_the_realistic_use_bar),
    ("CP2-T13", t13_every_subagent_brief_carries_the_objective),
    ("CP2-X01", x01_reviewer_guidance_does_not_hardcode_developer_audience),
]


def write_report():
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
        print(f"\n{cid}: {fn.__name__}")
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, not a silent drop
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
