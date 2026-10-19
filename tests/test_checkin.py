#!/usr/bin/env python3
"""
CP4 tests: check in with the objective when reviews drag.   python3 tests/test_checkin.py

When a step's second review round in a row still blocks (or, separately, a second round in a row brings a
previously-unseen finding kind into a file an earlier round already flagged), Ratchet pauses before opening
another round: `ratchet.py check-in CPn` prints the objective's goal, every open finding's user_impact, how many
rounds have run and the finding kinds grouped by file, and no new round opens until the check-in is answered
(simplify is recorded straight from the orchestrator's own reasoning, accept always needs CP3's own accept-finding
flow, stop hands it to the user). Separately, once a round blocks, no further round opens until every one of
that round's blocking findings has a fix-or-rebut decision from `ratchet.py triage`; the fixer is handed only the
fix items, and the next round's reviewers see the rebuttal text. The final review (status at
awaiting_final_review) opens with the objective and reports each done_means line against what was actually built.

Reuses tests/trust_helpers.py's make_env/rx/cp3_open_round/cp3_R0/cp3_state/cp4_say_yes and _tmp.OBJECTIVE_MIN's
shape (a plan's objective: users/goal/done_means/out_of_scope), the same real-CLI-subprocess way test_gates.py
and test_accept_finding.py drive a throwaway git project (private RATCHET_HOME, no real reviewer CLI, no network
call). A fake reviewer is never spawned: reviews are written directly to the round's output_dir and confirmed
through the guard hook the way the real ratchet-adversary sub-agent's write would be, exactly as
test_accept_finding.py's write_round_review() does.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

ROOT = HERE.parent
PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP4-[TX]\d+[a-z]?)\]")


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

OBJECTIVE = {
    "users": "shoppers checking out on the storefront",
    "goal": "ship a safe login without losing a shopper's cart",
    "done_means": ["CP1 works", "CP2 login works", "no regressions"],
    "out_of_scope": ["payment processing"],
}

# Two blocking findings in src/login.js, each with its own `kind` and a user_impact line (so CP2's no-user-impact
# exemption never removes them and they really block their round).
MAJOR1 = {"id": "F1", "severity": "major", "kind": "token-rotation-bug", "file": "src/login.js",
          "description": "session token not rotated on login",
          "user_impact": "a signed-in user's session token can be reused by an attacker"}
MAJOR2 = {"id": "F2", "severity": "major", "kind": "rate-limit-bug", "file": "src/login.js",
          "description": "no rate limiting on login attempts",
          "user_impact": "a user's account can be brute-forced by repeated login attempts"}


def write_round_review(d, env, rnd, rid, findings):
    """Writes an adversarial review the way the host's ratchet-adversary sub-agent writes it (the guard hook only
    counts a review written by that sub-agent) -- mirrors test_accept_finding.py's helper of the same name."""
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    g = th.rx(d, env, "hook", "guard", "--host", "claude",
              stdin=json.dumps({"cwd": str(d), "tool_name": "Write", "tool_input": {"file_path": str(path)},
                                "agent_type": "ratchet-adversary", "agent_id": "a-ratchet-adversary"}))
    assert g.returncode == 0, g.stderr + g.stdout
    return th.cp3_write_review(path, rnd, rid, findings=findings)


def build_project(name, checkin_round=None, cp2_risk="low"):
    """A throwaway git project with two checkpoints (CP1 trivial, CP2 the one under test), approved into building.
    Mirrors tests/trust_helpers.py's cp3_project(), widened to two checkpoints and a full CP1 objective block.
    Returns (d, env, tp, objective)."""
    env = th.make_env("claude")
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp4-{name}-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": th.now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    th.rx(d, env, "hook", "prompt", "--host", "claude",
          stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                            "transcript_path": str(tp)}))
    r = th.rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    extra = [f"checkin_round={checkin_round}"] if checkin_round is not None else []
    th.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", *extra)
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    cp2 = {"id": "CP2", "title": "Step 2: login", "summary": "login", "done_when": ["login works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": cp2_risk,
           "depends_on": ["CP1"],
           "tests": {"files": ["tests/cp2.test.js"], "command": "test -f src/login.js"}}
    body = {"feature": "x", "objective": OBJECTIVE, "checkpoints": [cp1, cp2]}
    (run / "checkpoints.json").write_text(json.dumps(body))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code", "proves": [2, 3]}]}))
    r = th.rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    th.rx(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    th.rx(d, env, "hook", "prompt", "--host", "claude",
          stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                            "transcript_path": str(tp)}))
    r = th.rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d, env, tp, OBJECTIVE


def legacy_two_cp_project(name):
    """A run approved before CP1's objective field existed: like build_project(), but checkpoints.json has no
    'objective' key at all, and the run reaches 'awaiting_plan_approval' the way the pre-objective engine's
    plan-submit did. Today's plan-submit now refuses a plan with no objective (CP1), so that shape can't be
    produced through the CLI any more -- mirrors tests/test_objective.py's _legacy_project(): the same init/config
    steps, then the exact state change cmd_plan_submit makes, called straight from the engine module in-process
    (never by hand-editing state.json's fields), skipping only the objective check that did not exist yet.
    Returns (d, env, tp)."""
    env = th.make_env("claude")
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp4-legacy-{name}-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": th.now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    th.rx(d, env, "hook", "prompt", "--host", "claude",
          stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                            "transcript_path": str(tp)}))
    r = th.rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    th.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    cp2 = {"id": "CP2", "title": "Step 2: login", "summary": "login", "done_when": ["login works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "depends_on": ["CP1"],
           "tests": {"files": ["tests/cp2.test.js"], "command": "test -f src/login.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp1, cp2]}))  # no 'objective'
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))
    with th.engine_env(env):
        m = th.engine()
        ctx = m.Ctx(d)
        st = ctx.state()
        plan = ctx.plan()
        st["phase"] = "awaiting_plan_approval"
        st["awaiting_since"] = m.now()
        st["plan_hash"] = m._plan_hash(plan, ctx)
        st["checkpoints"] = {c["id"]: st["checkpoints"].get(c["id"]) or m.new_cp_state(c) for c in plan["checkpoints"]}
        ctx.log(st, "plan_submitted", hash=st["plan_hash"], count=len(plan["checkpoints"]))
        ctx.save_state(st)
    th.rx(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    th.rx(d, env, "hook", "prompt", "--host", "claude",
          stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                            "transcript_path": str(tp)}))
    r = th.rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d, env, tp


def finish_cp1_clean(d, env):
    (d / "tests/cp1.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f.js';\n"
        "t('[CP1-T01] works', () => a.equal(f(), 1));\n")
    assert th.rx(d, env, "tests-written", "CP1").returncode == 0
    assert th.rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert th.rx(d, env, "implemented", "CP1").returncode == 0
    assert th.rx(d, env, "behavior", "CP1").returncode == 0
    rnd = th.cp3_open_round(d, env, "CP1")
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    assert r.returncode == 0, r.stdout + r.stderr
    r = th.rx(d, env, "complete", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr


def cp2_pass_behavior(d, env):
    (d / "tests/cp2.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { login } from '../src/login.js';\n"
        "t('[CP2-T01] works', () => a.equal(login(), 1));\n")
    assert th.rx(d, env, "tests-written", "CP2").returncode == 0
    assert th.rx(d, env, "red", "CP2").returncode == 0
    (d / "src/login.js").write_text("export const login = () => 1;\n")
    assert th.rx(d, env, "implemented", "CP2").returncode == 0
    r = th.rx(d, env, "behavior", "CP2")
    assert r.returncode == 0, r.stdout + r.stderr


def block_two_rounds(name):
    """CP2 blocked at round 1 on MAJOR1, "fixed" (a code change that makes the next round's fingerprint differ),
    then round 2 blocks again on the different MAJOR2. Returns (d, env, tp, objective, r2) where r2 is adv-record's
    own CompletedProcess for round 2."""
    d, env, tp, objective = build_project(name)
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd1 = th.cp3_open_round(d, env, "CP2")
    rid1 = th.cp3_R0(rnd1)
    path1 = write_round_review(d, env, rnd1, rid1, [MAJOR1])
    r1 = th.rx(d, env, "adv-record", "CP2", "--reviews", path1)
    assert r1.returncode == 2, r1.stdout + r1.stderr  # fixture sanity: round 1 blocks
    tr1 = th.rx(d, env, "triage", "CP2", f"{rid1}/F1=fix:rotate the token")
    assert tr1.returncode == 0, tr1.stdout + tr1.stderr  # every open finding needs a decision before round 2 opens
    (d / "src/login.js").write_text("export const login = () => 1; // rotate the token (fix 1)\n")
    br = th.rx(d, env, "behavior", "CP2")
    assert br.returncode == 0, br.stdout + br.stderr
    rnd2 = th.cp3_open_round(d, env, "CP2")
    rid2 = th.cp3_R0(rnd2)
    path2 = write_round_review(d, env, rnd2, rid2, [MAJOR2])
    r2 = th.rx(d, env, "adv-record", "CP2", "--reviews", path2)
    return d, env, tp, objective, r2


def one_round_two_findings(name):
    """CP2, normal risk (two reviewers), round 1 blocks with reviewer[0]'s F1 and reviewer[1]'s F2. Returns
    (d, env, tp, objective, rids) with rids sorted so rids[0]/F1 and rids[1]/F2 are the two qualified ids."""
    d, env, tp, objective = build_project(name, cp2_risk="normal")
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env, "CP2")
    rids = sorted(r["id"] for r in rnd["reviewers"])
    assert len(rids) == 2, rids  # fixture sanity: normal risk reviews with two independent reviewers
    fa = {"id": "F1", "severity": "major", "kind": "null-check", "file": "src/login.js",
          "description": "null check missing on an empty cart",
          "user_impact": "a signed-in user's session crashes on an empty cart"}
    fb = {"id": "F2", "severity": "major", "kind": "disputed-path", "file": "src/login.js",
          "description": "a path that may not be reachable for a signed-in user",
          "user_impact": "a signed-in user is logged out mid-session"}
    p1 = write_round_review(d, env, rnd, rids[0], [fa])
    p2 = write_round_review(d, env, rnd, rids[1], [fb])
    r = th.rx(d, env, "adv-record", "CP2", "--reviews", p1, p2)
    assert r.returncode == 2, r.stdout + r.stderr  # fixture sanity: round 1 blocks on both F1 and F2
    return d, env, tp, objective, rids


def status_and_checkin(d, env, cp="CP2"):
    ci = th.rx(d, env, "check-in", cp)
    return th.rx(d, env, "status").stdout + "\n" + ci.stdout + ci.stderr


def _latest_round_reviewer(d, cp="CP2"):
    """The reviewer id `reviewers` resolved for the checkpoint's current adv_round, so a test can build a
    `reviewer/id` triage ref for that round's own finding without re-deriving it from `cp3_open_round`'s return
    value (which isn't kept around everywhere a later round needs to triage a prior one's finding)."""
    return th.cp3_state(d)["checkpoints"][cp]["adv_round"]["reviewers"][0]


# ------------------------------------------------------------------------------------------------ CP4-T01

def t01_second_blocking_round_pauses_for_checkin():
    d, env, tp, objective, r2 = block_two_rounds("t01")
    check("[CP4-T01] (fixture sanity) round 2 of CP2 blocks", r2.returncode == 2, r2.stdout + r2.stderr)
    combined = status_and_checkin(d, env)
    check("[CP4-T01] the run is paused on a check-in rather than silently waiting for round 3",
          "check-in" in combined.lower(), combined)
    check("[CP4-T01] the plan's objective goal is printed", OBJECTIVE["goal"] in combined, combined)
    check("[CP4-T01] F1's user_impact is not printed: round 1's finding was addressed before round 2 ran, so the "
          "latest round no longer carries it", MAJOR1["user_impact"] not in combined, combined)
    check("[CP4-T01] F2's user_impact is printed", MAJOR2["user_impact"] in combined, combined)
    check("[CP4-T01] three lettered options cover simplify, accept and stop",
          bool(re.search(r"[A-Z]\)[^\n]*simplify", combined, re.I))
          and bool(re.search(r"[A-Z]\)[^\n]*accept", combined, re.I))
          and bool(re.search(r"[A-Z]\)[^\n]*stop", combined, re.I)), combined)


# ------------------------------------------------------------------------------------------------ CP4-T02

def t02_new_round_refused_while_checkin_open():
    d, env, tp, objective, r2 = block_two_rounds("t02")
    before = (th.cp3_state(d)["checkpoints"]["CP2"]["gates"]["adversarial"].get("rounds") or [])
    r = th.rx(d, env, "reviewers", "CP2")
    out = r.stdout + r.stderr
    check("[CP4-T02] opening round 3 while a check-in is open is refused", r.returncode != 0, out)
    check("[CP4-T02] the refusal names the open check-in and the command to resolve it",
          "check-in" in out.lower(), out)
    after = (th.cp3_state(d)["checkpoints"]["CP2"]["gates"]["adversarial"].get("rounds") or [])
    check("[CP4-T02] no round 3 was opened", len(after) == len(before), (before, after))


# ------------------------------------------------------------------------------------------------ CP4-T03

def t03_checkin_with_no_decision_prints_brief_only():
    d, env, tp, objective, r2 = block_two_rounds("t03")
    phase_before = th.cp3_state(d).get("phase")
    r = th.rx(d, env, "check-in", "CP2")
    out = r.stdout + r.stderr
    check("[CP4-T03] `check-in CP2` with no flags succeeds", r.returncode == 0, out)
    check("[CP4-T03] the brief shows the objective's goal", OBJECTIVE["goal"] in out, out)
    check("[CP4-T03] the brief does not show round 1's resolved F1 user_impact", MAJOR1["user_impact"] not in out,
          out)
    check("[CP4-T03] the brief shows F2's user_impact", MAJOR2["user_impact"] in out, out)
    check("[CP4-T03] the brief shows how many rounds this step has used (2)",
          bool(re.search(r"\b2\b", out)) and "round" in out.lower(), out)
    rj = th.rx(d, env, "check-in", "CP2", "--json")
    try:
        data = json.loads(rj.stdout)
    except Exception:
        data = None
    check("[CP4-T03] `--json` returns parseable JSON", isinstance(data, dict), rj.stdout + rj.stderr)
    dumped = json.dumps(data) if isinstance(data, dict) else ""
    check("[CP4-T03] the JSON carries the objective goal", OBJECTIVE["goal"] in dumped, dumped)
    check("[CP4-T03] the JSON carries only the latest round's finding's user_impact",
          MAJOR1["user_impact"] not in dumped and MAJOR2["user_impact"] in dumped, dumped)
    check("[CP4-T03] the JSON groups finding kinds by the file they are in (src/login.js)",
          "src/login.js" in dumped, dumped)
    check("[CP4-T03] the phase is unchanged: nothing was decided",
          th.cp3_state(d).get("phase") == phase_before, (phase_before, th.cp3_state(d).get("phase")))


# ------------------------------------------------------------------------------------------------ CP4-T04

def t04_simplify_recorded_without_user_and_unblocks_next_round():
    d, env, tp, objective, r2 = block_two_rounds("t04")
    r = th.rx(d, env, "check-in", "CP2", "--decision", "simplify", "--why",
              "dropping the optional retry path keeps the fix small")
    out = r.stdout + r.stderr
    check("[CP4-T04] `check-in --decision simplify --why ...` succeeds immediately, no user asked",
          r.returncode == 0, out)
    pending = th.cp3_state(d).get("pending") or {}
    check("[CP4-T04] no pending user question was created", not pending, pending)
    later = th.rx(d, env, "status").stdout + th.rx(d, env, "plan-summary").stdout
    check("[CP4-T04] the simplify reasoning is recorded against this check-in",
          "dropping the optional retry path keeps the fix small" in later, later)
    tr2 = th.rx(d, env, "triage", "CP2", f"{_latest_round_reviewer(d)}/F2=fix:add rate limiting")
    assert tr2.returncode == 0, tr2.stdout + tr2.stderr  # round 2's own finding still needs its decision
    r_rev = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-T04] round 3 can now be opened", r_rev.returncode == 0, r_rev.stdout + r_rev.stderr)


# ------------------------------------------------------------------------------------------------ CP4-T05

def t05_round_count_checkin_fires_once_per_step():
    d, env, tp, objective, r2 = block_two_rounds("t05")
    rs = th.rx(d, env, "check-in", "CP2", "--decision", "simplify", "--why", "keep the fix minimal")
    check("[CP4-T05] (setup) simplify is recorded for round 2's check-in", rs.returncode == 0, rs.stdout + rs.stderr)
    if rs.returncode != 0:
        return
    tr2 = th.rx(d, env, "triage", "CP2", f"{_latest_round_reviewer(d)}/F2=fix:add rate limiting")
    assert tr2.returncode == 0, tr2.stdout + tr2.stderr  # round 2's own finding still needs its decision
    r_open = th.rx(d, env, "reviewers", "CP2", "--json")
    check("[CP4-T05] (setup) round 3 opens after simplify", r_open.returncode == 0, r_open.stdout + r_open.stderr)
    if r_open.returncode != 0:
        return
    try:
        rnd3 = json.loads(r_open.stdout)
    except Exception:
        rnd3 = None
    check("[CP4-T05] (setup) round 3's --json output is readable", isinstance(rnd3, dict), r_open.stdout)
    if not isinstance(rnd3, dict):
        return
    rid3 = th.cp3_R0(rnd3)
    major3 = {"id": "F3", "severity": "major", "kind": MAJOR2["kind"], "file": "src/login.js",
              "description": "rate limiting still missing after the fix", "user_impact": MAJOR2["user_impact"]}
    path3 = write_round_review(d, env, rnd3, rid3, [major3])
    r3 = th.rx(d, env, "adv-record", "CP2", "--reviews", path3)
    check("[CP4-T05] (fixture sanity) round 3 blocks again, with no new finding kind",
          r3.returncode == 2, r3.stdout + r3.stderr)
    out = th.rx(d, env, "status").stdout
    check("[CP4-T05] round 3 blocking alone does not reopen a second round-count check-in",
          "check-in" not in out.lower(), out)
    tr3 = th.rx(d, env, "triage", "CP2", f"{rid3}/F3=fix:add rate limiting after the earlier fix")
    assert tr3.returncode == 0, tr3.stdout + tr3.stderr  # round 3's own finding still needs its decision
    r_rev = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-T05] round 4 opens under the normal round-limit rules, with no check-in blocking it",
          r_rev.returncode == 0, r_rev.stdout + r_rev.stderr)


# ------------------------------------------------------------------------------------------------ CP4-T06

def t06_two_new_kinds_in_an_already_flagged_file_trigger_a_checkin():
    # checkin_round is set high here to isolate the kind-repetition trigger from the round-count trigger.
    d, env, tp, objective = build_project("t06", checkin_round=10)
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    kind_a = {"id": "A", "severity": "major", "kind": "kind-a", "file": "src/login.js",
              "description": "round 1 finding", "user_impact": "a signed-in user is hurt by kind a"}
    rnd1 = th.cp3_open_round(d, env, "CP2")
    rid1 = th.cp3_R0(rnd1)
    path1 = write_round_review(d, env, rnd1, rid1, [kind_a])
    r1 = th.rx(d, env, "adv-record", "CP2", "--reviews", path1)
    check("[CP4-T06] (fixture sanity) round 1 blocks", r1.returncode == 2, r1.stdout + r1.stderr)
    tr1 = th.rx(d, env, "triage", "CP2", f"{rid1}/A=fix:address kind a")
    assert tr1.returncode == 0, tr1.stdout + tr1.stderr  # every open finding needs a decision before round 2 opens
    (d / "src/login.js").write_text("export const login = () => 1; // fix 1\n")
    assert th.rx(d, env, "behavior", "CP2").returncode == 0
    kind_b = {"id": "B", "severity": "major", "kind": "kind-b", "file": "src/login.js",
              "description": "round 2 finding, a kind not seen before", "user_impact": "a signed-in user is hurt by kind b"}
    rnd2 = th.cp3_open_round(d, env, "CP2")
    rid2 = th.cp3_R0(rnd2)
    path2 = write_round_review(d, env, rnd2, rid2, [kind_b])
    r2 = th.rx(d, env, "adv-record", "CP2", "--reviews", path2)
    check("[CP4-T06] (fixture sanity) round 2 blocks with a new finding kind", r2.returncode == 2, r2.stdout + r2.stderr)
    out2 = th.rx(d, env, "status").stdout
    check("[CP4-T06] round 2's own new kind (the first repeat) does not by itself open a check-in",
          "check-in" not in out2.lower(), out2)
    tr2 = th.rx(d, env, "triage", "CP2", f"{rid2}/B=fix:address kind b")
    assert tr2.returncode == 0, tr2.stdout + tr2.stderr  # every open finding needs a decision before round 3 opens
    (d / "src/login.js").write_text("export const login = () => 1; // fix 2\n")
    assert th.rx(d, env, "behavior", "CP2").returncode == 0
    kind_c = {"id": "C", "severity": "major", "kind": "kind-c", "file": "src/login.js",
              "description": "round 3 finding, yet another kind not seen before",
              "user_impact": "a signed-in user is hurt by kind c"}
    rnd3 = th.cp3_open_round(d, env, "CP2")
    rid3 = th.cp3_R0(rnd3)
    path3 = write_round_review(d, env, rnd3, rid3, [kind_c])
    r3 = th.rx(d, env, "adv-record", "CP2", "--reviews", path3)
    check("[CP4-T06] (fixture sanity) round 3 blocks with yet another new finding kind",
          r3.returncode == 2, r3.stdout + r3.stderr)
    out3 = th.rx(d, env, "status").stdout
    check("[CP4-T06] a check-in opens at round 3: the second round a new kind shows up in a file an earlier "
          "round already blocked", "check-in" in out3.lower(), out3)


# ------------------------------------------------------------------------------------------------ CP4-T07

def t07_checkin_cannot_itself_accept_a_finding():
    d, env, tp, objective, r2 = block_two_rounds("t07")
    open_path = Path(d) / ".ratchet/runs/f/evidence/CP2/adversarial/round-02/open-findings.json"
    before_open = json.loads(open_path.read_text()) if open_path.exists() else []
    r = th.rx(d, env, "check-in", "CP2", "--decision", "accept")
    out = r.stdout + r.stderr
    check("[CP4-T07] `check-in --decision accept` is refused: a cooperative agent cannot wave its own findings "
          "through via check-in", r.returncode != 0, out)
    check("[CP4-T07] the refusal points at the accept-finding flow, which needs the user's own reply",
          "accept-finding" in out.lower(), out)
    after_open = json.loads(open_path.read_text()) if open_path.exists() else []
    check("[CP4-T07] nothing was accepted: the open findings are unchanged",
          after_open == before_open, (before_open, after_open))


# ------------------------------------------------------------------------------------------------ CP4-T08

def t08_stop_hands_the_decision_to_the_user():
    d, env, tp, objective, r2 = block_two_rounds("t08")
    r = th.rx(d, env, "check-in", "CP2", "--decision", "stop")
    out = r.stdout + r.stderr
    check("[CP4-T08] `check-in --decision stop` succeeds", r.returncode == 0, out)
    st = th.cp3_state(d)
    check("[CP4-T08] the run moves into the existing blocked/ask flow for the user to decide how to wrap up",
          st.get("phase") == "blocked", st.get("phase"))
    r_rev = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-T08] no new review round opens", r_rev.returncode != 0, r_rev.stdout + r_rev.stderr)


# ------------------------------------------------------------------------------------------------ CP4-T09

def t09_checkin_round_is_configurable():
    d, env, tp, objective = build_project("t09", checkin_round=3)
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd1 = th.cp3_open_round(d, env, "CP2")
    rid1 = th.cp3_R0(rnd1)
    path1 = write_round_review(d, env, rnd1, rid1, [MAJOR1])
    r1 = th.rx(d, env, "adv-record", "CP2", "--reviews", path1)
    check("[CP4-T09] (fixture sanity) round 1 blocks", r1.returncode == 2, r1.stdout + r1.stderr)
    tr1 = th.rx(d, env, "triage", "CP2", f"{rid1}/F1=fix:rotate the token")
    assert tr1.returncode == 0, tr1.stdout + tr1.stderr  # every open finding needs a decision before round 2 opens
    (d / "src/login.js").write_text("export const login = () => 1; // fix\n")
    assert th.rx(d, env, "behavior", "CP2").returncode == 0
    rnd2 = th.cp3_open_round(d, env, "CP2")
    rid2 = th.cp3_R0(rnd2)
    path2 = write_round_review(d, env, rnd2, rid2, [MAJOR2])
    r2 = th.rx(d, env, "adv-record", "CP2", "--reviews", path2)
    check("[CP4-T09] (fixture sanity) round 2 blocks too", r2.returncode == 2, r2.stdout + r2.stderr)
    out = th.rx(d, env, "status").stdout
    check("[CP4-T09] no check-in opens yet: round 2 is below the configured checkin_round of 3",
          "check-in" not in out.lower(), out)
    tr2 = th.rx(d, env, "triage", "CP2", f"{rid2}/F2=fix:add rate limiting")
    assert tr2.returncode == 0, tr2.stdout + tr2.stderr  # every open finding needs a decision before round 3 opens
    r_rev = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-T09] round 3 opens normally", r_rev.returncode == 0, r_rev.stdout + r_rev.stderr)


# ------------------------------------------------------------------------------------------------ CP4-T10

def t10_final_review_reports_against_the_objective():
    d, env, tp, objective = build_project("t10")
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env, "CP2")
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [MAJOR1])
    r = th.rx(d, env, "adv-record", "CP2", "--reviews", path)
    check("[CP4-T10] (fixture sanity) CP2's round blocks on F1", r.returncode == 2, r.stdout + r.stderr)
    th.rx(d, env, "ask", "accept-finding", "CP2", "F1")
    th.cp4_say_yes(d, tp, env, text="accept: known limit for the beta, tracked separately")
    r_acc = th.rx(d, env, "accept-finding", "CP2")
    check("[CP4-T10] (fixture sanity) accept-finding succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    r_complete = th.rx(d, env, "complete", "CP2")
    check("[CP4-T10] (fixture sanity) CP2 completes", r_complete.returncode == 0, r_complete.stdout + r_complete.stderr)
    st = th.cp3_state(d)
    check("[CP4-T10] (fixture sanity) the run reached the final review",
          st.get("phase") == "awaiting_final_review", st.get("phase"))
    out = th.rx(d, env, "status").stdout
    check("[CP4-T10] status opens with the objective's goal (near the top of the output)",
          OBJECTIVE["goal"] in out[:500], out[:500])
    check("[CP4-T10] status names the objective's users", OBJECTIVE["users"] in out, out)
    for dm in OBJECTIVE["done_means"]:
        check(f"[CP4-T10] status lists the done_means line {dm!r}, met or not", dm in out, out)
    check("[CP4-T10] status shows the finding accepted along the way, with its reason",
          "F1" in out and "known limit for the beta" in out, out)
    jout = th.rx(d, env, "status", "--json").stdout
    try:
        data = json.loads(jout)
    except Exception:
        data = None
    dumped = json.dumps(data) if isinstance(data, dict) else ""
    check("[CP4-T10] status --json also carries the objective's goal and users",
          OBJECTIVE["goal"] in dumped and OBJECTIVE["users"] in dumped, dumped)


# ------------------------------------------------------------------------------------------------ CP4-T11

def t11_no_objective_still_reaches_checkin_and_final_review():
    d, env, tp = legacy_two_cp_project("t11")
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd1 = th.cp3_open_round(d, env, "CP2")
    rid1 = th.cp3_R0(rnd1)
    path1 = write_round_review(d, env, rnd1, rid1, [MAJOR1])
    r1 = th.rx(d, env, "adv-record", "CP2", "--reviews", path1)
    check("[CP4-T11] (fixture sanity) round 1 blocks", r1.returncode == 2, r1.stdout + r1.stderr)
    tr1 = th.rx(d, env, "triage", "CP2", f"{rid1}/F1=fix:rotate the token")
    assert tr1.returncode == 0, tr1.stdout + tr1.stderr  # every open finding needs a decision before round 2 opens
    (d / "src/login.js").write_text("export const login = () => 1; // fix\n")
    assert th.rx(d, env, "behavior", "CP2").returncode == 0
    rnd2 = th.cp3_open_round(d, env, "CP2")
    rid2 = th.cp3_R0(rnd2)
    path2 = write_round_review(d, env, rnd2, rid2, [MAJOR2])
    r2 = th.rx(d, env, "adv-record", "CP2", "--reviews", path2)
    check("[CP4-T11] (fixture sanity) round 2 blocks too", r2.returncode == 2, r2.stdout + r2.stderr)
    out = status_and_checkin(d, env)
    check("[CP4-T11] the check-in still opens with the latest round's open findings and does not crash with no "
          "objective stated", "check-in" in out.lower() and MAJOR2["user_impact"] in out, out)
    check("[CP4-T11] it plainly notes no objective was stated instead of raising an error",
          bool(re.search(r"no objective", out, re.I)), out)
    r_simplify = th.rx(d, env, "check-in", "CP2", "--decision", "simplify", "--why", "cut the retry path")
    check("[CP4-T11] simplify is still recorded cleanly with no objective present",
          r_simplify.returncode == 0, r_simplify.stdout + r_simplify.stderr)
    th.rx(d, env, "ask", "accept-finding", "CP2", "F2")
    th.cp4_say_yes(d, tp, env, text="accept: ship as-is")
    r_acc = th.rx(d, env, "accept-finding", "CP2")
    check("[CP4-T11] (fixture sanity) accepting both remaining findings succeeds",
          r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    r_complete = th.rx(d, env, "complete", "CP2")
    check("[CP4-T11] (fixture sanity) CP2 completes", r_complete.returncode == 0, r_complete.stdout + r_complete.stderr)
    final_out = th.rx(d, env, "status").stdout
    check("[CP4-T11] the final review still prints, noting no objective was stated",
          bool(re.search(r"no objective", final_out, re.I)), final_out)


# ------------------------------------------------------------------------------------------------ CP4-T12

def t12_open_checkin_does_not_let_the_step_complete():
    d, env, tp, objective, r2 = block_two_rounds("t12")
    r = th.rx(d, env, "complete", "CP2")
    out = r.stdout + r.stderr
    check("[CP4-T12] `complete CP2` is refused while the check-in is open", r.returncode != 0, out)
    check("[CP4-T12] the adversarial gate for CP2 is still not marked passed",
          th.cp3_state(d)["checkpoints"]["CP2"]["gates"]["adversarial"].get("status") != "passed",
          th.cp3_state(d)["checkpoints"]["CP2"]["gates"]["adversarial"].get("status"))


# ------------------------------------------------------------------------------------------------ CP4-T13

def t13_next_round_refused_until_every_blocking_finding_is_triaged():
    d, env, tp, objective, rids = one_round_two_findings("t13")
    r1 = th.rx(d, env, "triage", "CP2", f"{rids[0]}/F1=fix:straightforward null check")
    check("[CP4-T13] triaging F1 as fix is accepted", r1.returncode == 0, r1.stdout + r1.stderr)
    r_rev = th.rx(d, env, "reviewers", "CP2")
    out = r_rev.stdout + r_rev.stderr
    check("[CP4-T13] opening round 2 is refused while F2 still has no decision", r_rev.returncode != 0, out)
    check("[CP4-T13] the refusal names F2 as the undecided finding", "F2" in out, out)
    r2 = th.rx(d, env, "triage", "CP2", f"{rids[1]}/F2=rebut:this path never runs for a signed-in user")
    check("[CP4-T13] triaging F2 as rebut is accepted", r2.returncode == 0, r2.stdout + r2.stderr)
    r_rev2 = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-T13] round 2 now opens normally", r_rev2.returncode == 0, r_rev2.stdout + r_rev2.stderr)


# ------------------------------------------------------------------------------------------------ CP4-T14

def t14_triage_decisions_restricted_to_fix_or_rebut_and_rebut_needs_a_reason():
    d, env, tp, objective, rids = one_round_two_findings("t14")
    key = f"{rids[0]}/F1"
    r_empty = th.rx(d, env, "triage", "CP2", f"{key}=rebut:")
    out1 = r_empty.stdout + r_empty.stderr
    check("[CP4-T14] an empty rebut reason is refused, naming the missing reason",
          r_empty.returncode != 0 and bool(re.search(r"reason", out1, re.I)), out1)
    r_accept = th.rx(d, env, "triage", "CP2", f"{key}=accept:looks fine")
    out2 = r_accept.stdout + r_accept.stderr
    check("[CP4-T14] a triage decision of accept is refused, pointing at accept-finding",
          r_accept.returncode != 0 and "accept-finding" in out2.lower(), out2)
    r_defer = th.rx(d, env, "triage", "CP2", f"{key}=defer:later")
    out3 = r_defer.stdout + r_defer.stderr
    check("[CP4-T14] a triage decision of defer is refused, pointing at accept-finding",
          r_defer.returncode != 0 and "accept-finding" in out3.lower(), out3)
    r_check = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-T14] F1 still has no recorded decision afterward: round 2 is still refused",
          r_check.returncode != 0, r_check.stdout + r_check.stderr)


# ------------------------------------------------------------------------------------------------ CP4-T15

def t15_fixer_gets_only_fix_items_and_next_round_sees_the_rebuttal():
    d, env, tp, objective, rids = one_round_two_findings("t15")
    rebuttal_text = "F2's scenario can't occur for a signed-in user, per the objective"
    r = th.rx(d, env, "triage", "CP2", f"{rids[0]}/F1=fix:add the null check", f"{rids[1]}/F2=rebut:{rebuttal_text}")
    check("[CP4-T15] (setup) both triage decisions are recorded in one call", r.returncode == 0, r.stdout + r.stderr)
    fix_list_path = Path(d) / ".ratchet/runs/f/evidence/CP2/adversarial/round-01/fix-list.json"
    fix_list = json.loads(fix_list_path.read_text()) if fix_list_path.exists() else []
    ids_in_fixlist = sorted(f.get("id") for f in fix_list)
    check("[CP4-T15] the fixer's fix-list.json lists only F1, not F2",
          ids_in_fixlist == ["F1"], (str(fix_list_path), fix_list))
    r_rev = th.rx(d, env, "reviewers", "CP2")
    out = r_rev.stdout + r_rev.stderr
    check("[CP4-T15] round 2 opens now that both findings have a decision", r_rev.returncode == 0, out)
    check("[CP4-T15] round 2's reviewer brief includes F2's rebuttal text so reviewers can withdraw or re-raise it",
          rebuttal_text in out, out)


TESTS = [
    ("CP4-T01", t01_second_blocking_round_pauses_for_checkin),
    ("CP4-T02", t02_new_round_refused_while_checkin_open),
    ("CP4-T03", t03_checkin_with_no_decision_prints_brief_only),
    ("CP4-T04", t04_simplify_recorded_without_user_and_unblocks_next_round),
    ("CP4-T05", t05_round_count_checkin_fires_once_per_step),
    ("CP4-T06", t06_two_new_kinds_in_an_already_flagged_file_trigger_a_checkin),
    ("CP4-T07", t07_checkin_cannot_itself_accept_a_finding),
    ("CP4-T08", t08_stop_hands_the_decision_to_the_user),
    ("CP4-T09", t09_checkin_round_is_configurable),
    ("CP4-T10", t10_final_review_reports_against_the_objective),
    ("CP4-T11", t11_no_objective_still_reaches_checkin_and_final_review),
    ("CP4-T12", t12_open_checkin_does_not_let_the_step_complete),
    ("CP4-T13", t13_next_round_refused_until_every_blocking_finding_is_triaged),
    ("CP4-T14", t14_triage_decisions_restricted_to_fix_or_rebut_and_rebut_needs_a_reason),
    ("CP4-T15", t15_fixer_gets_only_fix_items_and_next_round_sees_the_rebuttal),
]


# ------------------------------------------------------------------------------------------------ CP4-T16

def t16_single_remaining_finding_still_needs_a_triage_decision():
    """[CP4-T16] A round blocking on exactly one finding (not two) still needs its own fix-or-rebut decision
    before the next round opens; the len(open_findings) >= 2 exemption is gone. Covers both an initially single
    finding and a single finding left after partial triage of two."""
    d, env, tp, objective = build_project("t16")
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd1 = th.cp3_open_round(d, env, "CP2")
    rid1 = th.cp3_R0(rnd1)
    path1 = write_round_review(d, env, rnd1, rid1, [MAJOR1])
    r1 = th.rx(d, env, "adv-record", "CP2", "--reviews", path1)
    check("[CP4-T16] (fixture sanity) round 1 blocks on a single finding", r1.returncode == 2, r1.stdout + r1.stderr)
    r_rev = th.rx(d, env, "reviewers", "CP2")
    out = r_rev.stdout + r_rev.stderr
    check("[CP4-T16] opening round 2 is refused: the single open finding still has no fix-or-rebut decision",
          r_rev.returncode != 0, out)
    check("[CP4-T16] the refusal names F1", "F1" in out, out)
    r_triage = th.rx(d, env, "triage", "CP2", f"{rid1}/F1=fix:add the null check")
    check("[CP4-T16] triaging the single finding is accepted", r_triage.returncode == 0,
          r_triage.stdout + r_triage.stderr)
    r_rev2 = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-T16] round 2 now opens, the lone finding decided", r_rev2.returncode == 0,
          r_rev2.stdout + r_rev2.stderr)


# ------------------------------------------------------------------------------------------------ CP4-T17

def t17_final_review_never_claims_done_means_is_met():
    """[CP4-T17] The final review lists each done_means line plainly for the user to judge, never claiming it was
    met just because the checkpoints finished; the accepted finding (which leaves "no regressions" unproven) is
    shown separately, not folded into a false [met] claim."""
    d, env, tp, objective = build_project("t17")
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env, "CP2")
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [MAJOR1])
    r = th.rx(d, env, "adv-record", "CP2", "--reviews", path)
    check("[CP4-T17] (fixture sanity) CP2's round blocks on F1", r.returncode == 2, r.stdout + r.stderr)
    th.rx(d, env, "ask", "accept-finding", "CP2", "F1")
    th.cp4_say_yes(d, tp, env, text="accept: known limit for the beta, tracked separately")
    r_acc = th.rx(d, env, "accept-finding", "CP2")
    check("[CP4-T17] (fixture sanity) accept-finding succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    r_complete = th.rx(d, env, "complete", "CP2")
    check("[CP4-T17] (fixture sanity) CP2 completes", r_complete.returncode == 0,
          r_complete.stdout + r_complete.stderr)
    out = th.rx(d, env, "status").stdout
    check("[CP4-T17] no done_means line is marked [met]", "[met]" not in out, out)
    for dm in OBJECTIVE["done_means"]:
        check(f"[CP4-T17] done_means line {dm!r} is printed plainly for the user to judge", dm in out, out)
    check("[CP4-T17] the accepted finding is shown separately from the done_means lines, with its reason",
          "F1" in out and "known limit for the beta" in out, out)


# ------------------------------------------------------------------------------------------------ CP4-T18

def t18_checkin_shows_only_the_latest_rounds_open_findings():
    """[CP4-T18] The check-in brief reflects only the gate's latest recorded round, not a finding from an earlier
    round that the current code no longer carries (round 1's F1, addressed before round 2 ran): an old, resolved
    finding must never be shown as if it were still the current blocker."""
    d, env, tp, objective, r2 = block_two_rounds("t18")
    check("[CP4-T18] (fixture sanity) round 2 of CP2 blocks", r2.returncode == 2, r2.stdout + r2.stderr)
    out = th.rx(d, env, "check-in", "CP2").stdout
    check("[CP4-T18] the brief shows the latest round's own finding (F2)", MAJOR2["user_impact"] in out, out)
    check("[CP4-T18] the brief does not resurrect round 1's resolved finding (F1)",
          MAJOR1["user_impact"] not in out, out)


TESTS.append(("CP4-T16", t16_single_remaining_finding_still_needs_a_triage_decision))
TESTS.append(("CP4-T17", t17_final_review_never_claims_done_means_is_met))
TESTS.append(("CP4-T18", t18_checkin_shows_only_the_latest_rounds_open_findings))


# ------------------------------------------------------------------------------------------------ CP4-X01

def t_x01_triage_resolves_a_bare_unambiguous_id_the_same_way_accept_finding_does():
    """[CP4-X01] `ratchet.py triage CPn F1=fix:...` (a bare id, no `reviewer/` prefix) must resolve the same way
    `accept-finding` resolves a ref (`_resolve_finding_ref`): every open finding is keyed 'reviewer/id' internally,
    but a bare id that names exactly one of them still works directly, and the decision is recorded (and
    fix-list.json built) under the finding's own qualified key."""
    d, env, tp, objective = build_project("x01")
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd1 = th.cp3_open_round(d, env, "CP2")
    rid1 = th.cp3_R0(rnd1)
    path1 = write_round_review(d, env, rnd1, rid1, [MAJOR1])
    r1 = th.rx(d, env, "adv-record", "CP2", "--reviews", path1)
    check("[CP4-X01] (fixture sanity) round 1 blocks on a single finding", r1.returncode == 2, r1.stdout + r1.stderr)
    r_triage = th.rx(d, env, "triage", "CP2", "F1=fix:add the null check")
    check("[CP4-X01] triaging the bare unambiguous id F1 is accepted (not refused as 'not open')",
          r_triage.returncode == 0, r_triage.stdout + r_triage.stderr)
    fix_list_path = Path(d) / ".ratchet/runs/f/evidence/CP2/adversarial/round-01/fix-list.json"
    fix_list = json.loads(fix_list_path.read_text()) if fix_list_path.exists() else []
    check("[CP4-X01] the finding reaches fix-list.json (triage was recorded against the real open finding, not "
          "silently dropped)", [f.get("id") for f in fix_list] == ["F1"], (str(fix_list_path), fix_list))
    triage_path = Path(d) / ".ratchet/runs/f/evidence/CP2/adversarial/round-01/triage.json"
    triage = json.loads(triage_path.read_text()) if triage_path.exists() else {}
    check("[CP4-X01] triage.json keys the decision by the finding's own qualified 'reviewer/id', not the bare ref",
          list(triage.keys()) == [f"{rid1}/F1"], triage)
    r_rev2 = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-X01] round 2 now opens, the lone finding decided via its bare id", r_rev2.returncode == 0,
          r_rev2.stdout + r_rev2.stderr)


# ------------------------------------------------------------------------------------------------ CP4-X02

def t_x02_accept_finding_clears_an_open_checkin():
    """[CP4-X02] Accepting the last open finding of a round must also resolve an open check-in for that step: a
    check-in only ever pauses the loop because a blocking finding is still open, so once accept-finding leaves none
    open, nothing is left for it to pause on. A later code change still brings back the normal review rules (a
    fresh round, not silently skipped) instead of the check-in sitting open forever."""
    d, env, tp, objective, r2 = block_two_rounds("x02")
    before = th.cp3_state(d)["checkpoints"]["CP2"].get("checkin") or {}
    check("[CP4-X02] (fixture sanity) round 2 blocks and a check-in is open", r2.returncode == 2 and
          bool(before.get("open")), before)
    r_ask = th.rx(d, env, "ask", "accept-finding", "CP2", "F2")
    check("[CP4-X02] (setup) asking to accept the round's only remaining open finding succeeds",
          r_ask.returncode == 0, r_ask.stdout + r_ask.stderr)
    th.cp4_say_yes(d, tp, env, text="accept: known limit, tracked separately")
    r_acc = th.rx(d, env, "accept-finding", "CP2")
    check("[CP4-X02] (setup) accept-finding succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    after = th.cp3_state(d)["checkpoints"]["CP2"].get("checkin") or {}
    check("[CP4-X02] the open check-in is cleared once no open finding remains for the step",
          after.get("open") is None, after)
    r_status = th.rx(d, env, "status")
    check("[CP4-X02] status no longer reports a check-in waiting to be answered for CP2",
          "check-in" not in r_status.stdout.lower(), r_status.stdout)
    (d / "src/login.js").write_text("export const login = () => 1; // post-accept change\n")
    r_complete = th.rx(d, env, "complete", "CP2")
    check("[CP4-X02] (setup) `complete` catches the post-accept code change and sends CP2 back for a fresh review",
          r_complete.returncode != 0 and "adversarial" in (r_complete.stdout + r_complete.stderr).lower(),
          r_complete.stdout + r_complete.stderr)
    br = th.rx(d, env, "behavior", "CP2")
    check("[CP4-X02] (setup) behavior re-passes on the changed code", br.returncode == 0, br.stdout + br.stderr)
    r_rev3 = th.rx(d, env, "reviewers", "CP2")
    check("[CP4-X02] a fresh review round opens normally for the changed code: the old, already-resolved "
          "check-in never refuses it", r_rev3.returncode == 0, r_rev3.stdout + r_rev3.stderr)


TESTS.append(("CP4-X01", t_x01_triage_resolves_a_bare_unambiguous_id_the_same_way_accept_finding_does))
TESTS.append(("CP4-X02", t_x02_accept_finding_clears_an_open_checkin))


# ------------------------------------------------------------------------------------------------ CP4-X03

def t_x03_an_approved_round_resets_the_consecutive_blocking_count():
    """[CP4-X03] The round-count check-in trigger counts CONSECUTIVE blocking rounds, not every recorded round: an
    approved round resets the count. Round 1 approves (no findings), the code changes, round 2 blocks -- with
    `checkin_round` at its default of 2, that is only one blocking round in a row, so no check-in opens yet."""
    d, env, tp, objective = build_project("x03")
    finish_cp1_clean(d, env)
    cp2_pass_behavior(d, env)
    rnd1 = th.cp3_open_round(d, env, "CP2")
    rid1 = th.cp3_R0(rnd1)
    path1 = Path(rnd1["output_dir"]) / f"{rid1}.json"
    g1 = th.rx(d, env, "hook", "guard", "--host", "claude",
               stdin=json.dumps({"cwd": str(d), "tool_name": "Write", "tool_input": {"file_path": str(path1)},
                                 "agent_type": "ratchet-adversary", "agent_id": "a-ratchet-adversary"}))
    assert g1.returncode == 0, g1.stderr + g1.stdout
    # an approving review must list every changed file it reviewed; the shared write_round_review() helper defaults
    # files_reviewed to CP1's src/f.js, so round 1's approval here is written directly with CP2's own changed files.
    th.cp3_write_review(path1, rnd1, rid1, findings=[], files_reviewed=["src/login.js", "tests/cp2.test.js"])
    r1 = th.rx(d, env, "adv-record", "CP2", "--reviews", str(path1))
    check("[CP4-X03] (fixture sanity) round 1 approves with no findings", r1.returncode == 0, r1.stdout + r1.stderr)
    (d / "src/login.js").write_text("export const login = () => 1; // post-approval change\n")
    br = th.rx(d, env, "behavior", "CP2")
    assert br.returncode == 0, br.stdout + br.stderr
    rnd2 = th.cp3_open_round(d, env, "CP2")
    rid2 = th.cp3_R0(rnd2)
    path2 = write_round_review(d, env, rnd2, rid2, [MAJOR1])
    r2 = th.rx(d, env, "adv-record", "CP2", "--reviews", path2)
    check("[CP4-X03] (fixture sanity) round 2 blocks on F1", r2.returncode == 2, r2.stdout + r2.stderr)
    out = th.rx(d, env, "status").stdout
    check("[CP4-X03] no check-in opens yet: round 1's approval resets the consecutive-blocking count, so round 2 "
          "is only the first blocking round in a row", "check-in" not in out.lower(), out)


TESTS.append(("CP4-X03", t_x03_an_approved_round_resets_the_consecutive_blocking_count))


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
