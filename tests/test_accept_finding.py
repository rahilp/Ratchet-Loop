#!/usr/bin/env python3
"""
CP3 tests: the user can accept open review findings.   python3 tests/test_accept_finding.py

When a review round blocks, the user can accept chosen open (blocking) findings with their own reason, so the
step finishes without another round. Only the user's own FIRST reply in the run's owning chat, in the exact
form `accept: <reason>`, accepts them; an agent cannot accept a finding by itself, and an unknown or
already-closed finding id is refused. Each accepted finding and the user's reason are written to
.ratchet/learnings.md and the step's deferred list, and marked accepted in that round's record. Accepting every
open finding of the round passes the adversarial gate without another round; accepting only some leaves the step
blocked; a later code change brings back the normal review rules.

Reuses tests/trust_helpers.py's CP3 fixtures (make_env, cp3_project, cp3_pass_behavior, cp3_open_round,
cp3_R0, cp3_write_review, cp3_reviewer_writes, cp3_state, cp3_fp, cp4_say_yes) which already drive a throwaway
git project through plan-submit -> approve-plan -> behavior -> an adversarial round, the real CLI subprocess way
(RATCHET_HOME in a private temp folder, no real reviewer CLI or network call). Replies go through the real
`ratchet.py hook prompt` hook, the same way tests/test_gates.py's say()/cp4_say_yes() do, including the
wrong-chat and reply-before-question cases this plan calls for.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
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
import importlib.util

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

ROOT = HERE.parent
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

# Two blocking findings (one blocker, one major), both carrying a user_impact line so CP2's no-user-impact
# exemption never removes them and they really block the round.
F1 = {"id": "F1", "severity": "blocker", "file": "src/f.js", "description": "crashes when the cart total is null",
      "user_impact": "a shopper's checkout crashes and they lose their cart"}
F2 = {"id": "F2", "severity": "major", "file": "src/f.js", "description": "logs the shopper's card number",
      "user_impact": "a shopper's card number is written to a log file other staff can read"}
# A minor finding with no user_impact: on the "low" risk this fixture builds at, minors never block
# (blocking_severities.low == ["blocker", "major"]), so F3 lands on the SAME round's deferred-findings.json
# without ever being open/blocking. CP3-T05 needs exactly this: a finding this flow must not reach.
F3 = {"id": "F3", "severity": "minor", "file": "src/f.js", "description": "variable name could be clearer"}


def write_round_review(d, env, rnd, rid, findings):
    """Writes an adversarial review the way the host's ratchet-adversary sub-agent writes it (the guard hook only
    counts a review written by that sub-agent) -- mirrors trust_helpers.cp3_reviewer_writes, but with findings."""
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    g = th.rx(d, env, "hook", "guard", "--host", "claude",
              stdin=json.dumps({"cwd": str(d), "tool_name": "Write", "tool_input": {"file_path": str(path)},
                                "agent_type": "ratchet-adversary", "agent_id": "a-ratchet-adversary"}))
    assert g.returncode == 0, g.stderr + g.stdout
    return th.cp3_write_review(path, rnd, rid, findings=findings)


def blocked_round(name):
    """A one-checkpoint project (CP1, low risk, built the way trust_helpers.cp3_project/cp3_pass_behavior build
    it) whose first adversarial round is recorded with F1 and F2 open (blocking) and F3 already deferred.
    Returns (d, env, tp, rnd, rid)."""
    env = th.make_env("claude")
    d = th.cp3_project(env)
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2, F3])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    assert r.returncode == 2, r.stdout + r.stderr  # fixture sanity: the round blocks on F1/F2
    tp = Path(d) / "t.jsonl"
    return d, env, tp, rnd, rid


def round_limit_project(name):
    """Mirrors trust_helpers.cp3_project, but with max_adversarial_rounds=1 set BEFORE the plan is approved (a
    config change made after approval needs the user's own approve-changes, which is not what CP3-T13 is
    about, so it can't be injected the way cp3_project's own fixed config call works)."""
    env = th.make_env("claude")
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp3af-rl-{name}-"))
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
    th.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", "max_adversarial_rounds=1")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
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
    return d, env, tp


def open_findings(d, round_no=1, cp="CP1", slug="f"):
    p = Path(d) / f".ratchet/runs/{slug}/evidence/{cp}/adversarial/round-{round_no:02d}/open-findings.json"
    return json.loads(p.read_text()) if p.exists() else []


def deferred_findings(d, round_no=1, cp="CP1", slug="f"):
    p = Path(d) / f".ratchet/runs/{slug}/evidence/{cp}/adversarial/round-{round_no:02d}/deferred-findings.json"
    return json.loads(p.read_text()) if p.exists() else []


def learnings_text(d):
    p = Path(d) / ".ratchet" / "learnings.md"
    return p.read_text() if p.exists() else ""


def step_deferred(d, cp="CP1"):
    return (th.cp3_state(d)["checkpoints"][cp].get("deferred")) or []


def by_id(entries, fid):
    return next((x for x in entries if x.get("id") == fid), None)


def reply(d, tp, env, text, session="s-owner"):
    return th.cp4_say_yes(d, tp, env, text=text, session=session)


# ------------------------------------------------------------------------------------------------ CP3-T01: the question

def t01_ask_prints_question_naming_findings_and_exact_reply():
    d, env, tp, rnd, rid = blocked_round("t01")
    r = th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    out = r.stdout + r.stderr
    check("[CP3-T01] `ask accept-finding CP1 F1,F2` is accepted and asks its question", r.returncode == 0, out)
    check("[CP3-T01] the question names finding F1", "F1" in out, out)
    check("[CP3-T01] the question names finding F2", "F2" in out, out)
    check("[CP3-T01] the question states the exact reply form 'accept: ' followed by the reason",
          "accept: " in out, out)
    check("[CP3-T01] the generic listed-yes words are not offered here as an equivalent reply",
          "yes, go ahead" not in out.lower() and "reply with just one of these" not in out.lower(), out)


# ------------------------------------------------------------------------------------------------ CP3-T02: round didn't block

def t02_cannot_ask_when_round_did_not_block():
    env = th.make_env("claude")
    d = th.cp3_project(env)
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    good = th.cp3_reviewer_writes(d, env, rnd, rid)
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", good)
    assert r.returncode == 0, r.stdout + r.stderr  # fixture sanity: gate passed, no open findings
    before = json.dumps(th.cp3_state(d), sort_keys=True)
    r = th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    out = r.stdout + r.stderr
    check("[CP3-T02] the command is refused when the round did not block", r.returncode != 0, out)
    check("[CP3-T02] the refusal says there is nothing open to accept",
          bool(re.search(r"no open|not blocked|nothing (is )?waiting|already (passed|approved)", out, re.I)), out)
    after = json.dumps(th.cp3_state(d), sort_keys=True)
    check("[CP3-T02] state is unchanged", before == after, (before, after))


# ------------------------------------------------------------------------------------------------ CP3-T03: unknown id

def t03_unknown_finding_id_refused():
    d, env, tp, rnd, rid = blocked_round("t03")
    before_open = open_findings(d)
    r = th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F9")
    out = r.stdout + r.stderr
    check("[CP3-T03] asking to accept an unknown finding id is refused", r.returncode != 0, out)
    check("[CP3-T03] the refusal names the unknown id F9 as unknown",
          "F9" in out and bool(re.search(r"unknown|doesn'?t exist|not (a |an )?(open |known )?finding", out, re.I)),
          out)
    after_open = open_findings(d)
    check("[CP3-T03] F1 is not accepted either", by_id(after_open, "F1") is not None, after_open)
    check("[CP3-T03] open-findings.json is unchanged", after_open == before_open, (before_open, after_open))


# ------------------------------------------------------------------------------------------------ CP3-T04: double accept

def t04_accepting_same_finding_twice_refused_second_time():
    d, env, tp, rnd, rid = blocked_round("t04")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: good enough for now")
    r1 = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-T04] (setup) the first acceptance of F1 succeeds", r1.returncode == 0, r1.stdout + r1.stderr)
    if r1.returncode != 0:
        return
    learnings_before = learnings_text(d)
    deferred_before = deferred_findings(d)
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: still fine")
    r2 = th.rx(d, env, "accept-finding", "CP1")
    out = r2.stdout + r2.stderr
    check("[CP3-T04] accepting the same finding again is refused", r2.returncode != 0, out)
    check("[CP3-T04] the refusal says it is already closed/accepted",
          bool(re.search(r"already (closed|accepted)", out, re.I)), out)
    check("[CP3-T04] learnings.md gains no duplicate entry for F1",
          learnings_text(d) == learnings_before, (learnings_before, learnings_text(d)))
    check("[CP3-T04] the deferred list gains no duplicate entry for F1",
          deferred_findings(d) == deferred_before, (deferred_before, deferred_findings(d)))


# ------------------------------------------------------------------------------------------------ CP3-T05: minor already deferred

def t05_deferred_minor_not_reachable_via_accept_finding():
    d, env, tp, rnd, rid = blocked_round("t05")
    deferred_before = deferred_findings(d)
    r = th.rx(d, env, "ask", "accept-finding", "CP1", "F3")
    out = r.stdout + r.stderr
    check("[CP3-T05] asking to accept an already-deferred minor finding is refused", r.returncode != 0, out)
    check("[CP3-T05] the refusal says F3 is not an open blocking finding of the round",
          "F3" in out and bool(re.search(r"not (an? )?open|not blocking|already deferred", out, re.I)), out)
    check("[CP3-T05] F3's existing deferred record is untouched",
          deferred_findings(d) == deferred_before, (deferred_before, deferred_findings(d)))


# ------------------------------------------------------------------------------------------------ CP3-T06: no reply yet

def t06_nothing_accepted_before_reply():
    d, env, tp, rnd, rid = blocked_round("t06")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    r = th.rx(d, env, "accept-finding", "CP1")
    out = r.stdout + r.stderr
    check("[CP3-T06] accept-finding with no reply yet is refused", r.returncode != 0, out)
    check("[CP3-T06] the refusal explains there is no reply to the question yet",
          bool(re.search(r"repl(y|ies)|wait for (the|their) (user|reply)", out, re.I)), out)
    ids = sorted(x.get("id") for x in open_findings(d))
    check("[CP3-T06] F1 and F2 remain open", ids == ["F1", "F2"], ids)
    gate = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-T06] the step remains blocked (gate not passed)", gate.get("status") != "passed", gate.get("status"))


# ------------------------------------------------------------------------------------------------ CP3-T07: wrong chat

def t07_reply_in_different_chat_does_not_accept():
    d, env, tp, rnd, rid = blocked_round("t07")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: because it's fine", session="s-other")
    r = th.rx(d, env, "accept-finding", "CP1")
    out = r.stdout + r.stderr
    check("[CP3-T07] a reply typed in a different chat does not accept the findings", r.returncode != 0, out)
    check("[CP3-T07] the refusal says no reply has come from the owning chat yet",
          bool(re.search(r"repl(y|ies)|another chat|owning chat|owner", out, re.I)), out)
    ids = sorted(x.get("id") for x in open_findings(d))
    check("[CP3-T07] F1 and F2 remain open", ids == ["F1", "F2"], ids)


# ------------------------------------------------------------------------------------------------ CP3-T08: genuine accept

def t08_owners_first_reply_accepts_with_reason():
    d, env, tp, rnd, rid = blocked_round("t08")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: we'll live with this for the beta")
    r = th.rx(d, env, "accept-finding", "CP1")
    out = r.stdout + r.stderr
    check("[CP3-T08] the command succeeds", r.returncode == 0, out)
    deferred = deferred_findings(d)
    e1, e2 = by_id(deferred, "F1"), by_id(deferred, "F2")
    check("[CP3-T08] F1 is recorded accepted with the user's reason",
          bool(e1) and e1.get("accepted") is True and e1.get("reason") == "we'll live with this for the beta", e1)
    check("[CP3-T08] F2 is recorded accepted with the user's reason",
          bool(e2) and e2.get("accepted") is True and e2.get("reason") == "we'll live with this for the beta", e2)
    check("[CP3-T08] the reason is appended to .ratchet/learnings.md",
          "we'll live with this for the beta" in learnings_text(d), learnings_text(d))
    step_def = step_deferred(d)
    check("[CP3-T08] F1 and F2 are added to the step's deferred list",
          by_id(step_def, "F1") is not None and by_id(step_def, "F2") is not None, step_def)
    opened = open_findings(d)
    check("[CP3-T08] F1 and F2 are no longer open", by_id(opened, "F1") is None and by_id(opened, "F2") is None,
          opened)


# ------------------------------------------------------------------------------------------------ CP3-T09: all accepted -> passes

def t09_accepting_all_passes_without_new_round():
    d, env, tp, rnd, rid = blocked_round("t09")
    fp_at_block = th.cp3_fp(d, env)
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: shipping with this known limit")
    r = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-T09] (setup) accept-finding succeeds for F1,F2", r.returncode == 0, r.stdout + r.stderr)
    if r.returncode != 0:
        return
    gate = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-T09] the adversarial gate is marked passed", gate.get("status") == "passed", gate.get("status"))
    check("[CP3-T09] passed for this round's fingerprint",
          (gate.get("fingerprint") or {}).get("id") == fp_at_block, gate.get("fingerprint"))
    check("[CP3-T09] no new review round was opened (still only round 1 on record)",
          len(gate.get("rounds") or []) == 1, gate.get("rounds"))
    r2 = th.rx(d, env, "complete", "CP1")
    check("[CP3-T09] `complete`'s re-check accepts it", r2.returncode == 0, r2.stdout + r2.stderr)


# ------------------------------------------------------------------------------------------------ CP3-T10: partial accept -> still blocked

def t10_accepting_only_some_leaves_blocked():
    d, env, tp, rnd, rid = blocked_round("t10")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: known, will fix separately")
    r = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-T10] (setup) accept-finding succeeds for F1", r.returncode == 0, r.stdout + r.stderr)
    if r.returncode != 0:
        return
    deferred = deferred_findings(d)
    e1 = by_id(deferred, "F1")
    check("[CP3-T10] F1 is recorded accepted", bool(e1) and e1.get("accepted") is True, e1)
    open_ids = [x.get("id") for x in open_findings(d)]
    check("[CP3-T10] F2 is still open", open_ids == ["F2"], open_ids)
    gate = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-T10] the adversarial gate is still not passed", gate.get("status") != "passed", gate.get("status"))
    r2 = th.rx(d, env, "complete", "CP1")
    check("[CP3-T10] the step is still blocked at `complete`", r2.returncode != 0, r2.stdout + r2.stderr)


# ------------------------------------------------------------------------------------------------ CP3-T11: code change resets rules

def t11_code_change_after_acceptance_resets_rules():
    d, env, tp, rnd, rid = blocked_round("t11")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: known tradeoff")
    r = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-T11] (setup) accept-finding succeeds for F1,F2", r.returncode == 0, r.stdout + r.stderr)
    if r.returncode != 0:
        return
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // changed again\n")
    br = th.rx(d, env, "behavior", "CP1")
    check("[CP3-T11] (setup) behavior passes again after the code change", br.returncode == 0,
          br.stdout + br.stderr)
    if br.returncode != 0:
        return
    rnd2 = th.cp3_open_round(d, env)
    rid2 = th.cp3_R0(rnd2)
    reborn = dict(F1)  # the same mistake, reintroduced by the new round
    path = write_round_review(d, env, rnd2, rid2, [reborn])
    r2 = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-T11] a finding reintroduced by the new round blocks normally again", r2.returncode == 2,
          r2.stdout + r2.stderr)
    opened2 = open_findings(d, round_no=2)
    check("[CP3-T11] it appears on round 2's own open-findings.json",
          [x.get("id") for x in opened2] == ["F1"], opened2)
    gate = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-T11] the earlier acceptance does not exempt the new round (gate not passed)",
          gate.get("status") != "passed", gate.get("status"))


# ------------------------------------------------------------------------------------------------ CP3-T12: listings

def t12_status_and_summary_list_accepted_findings_severity_order():
    d, env, tp, rnd, rid = blocked_round("t12")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: known tradeoff for this release")
    r = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-T12] (setup) accept-finding succeeds for F1,F2", r.returncode == 0, r.stdout + r.stderr)
    if r.returncode != 0:
        return
    r = th.rx(d, env, "complete", "CP1")
    check("[CP3-T12] (setup) complete succeeds", r.returncode == 0, r.stdout + r.stderr)
    if r.returncode != 0:
        return
    status_out = th.rx(d, env, "status").stdout
    summary_out = th.rx(d, env, "plan-summary").stdout
    for label, out in (("status", status_out), ("plan-summary", summary_out)):
        check(f"[CP3-T12] {label} shows F1 with its accepted reason",
              "F1" in out and "known tradeoff for this release" in out, out)
        check(f"[CP3-T12] {label} shows F2 with its accepted reason",
              "F2" in out and "known tradeoff for this release" in out, out)
        f1_idx, f2_idx, f3_idx = out.find("F1"), out.find("F2"), out.find("F3")
        check(f"[CP3-T12] {label} orders the accepted blocker/major before the plain deferred minor F3",
              f1_idx != -1 and f2_idx != -1
              and (f3_idx == -1 or (f1_idx < f3_idx and f2_idx < f3_idx)), out)
    phase = th.cp3_state(d)["phase"]
    check("[CP3-T12] the run reached the final review", phase == "awaiting_final_review", phase)


# ------------------------------------------------------------------------------------------------ CP3-T13: round-limit option

def t13_round_limit_offers_accept_finding_as_lettered_option():
    d, env, tp = round_limit_project("t13")
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-T13] (fixture sanity) the round blocks", r.returncode == 2, r.stdout + r.stderr)
    st = th.cp3_state(d)
    check("[CP3-T13] (fixture sanity) the run is now blocked at the round limit", st.get("phase") == "blocked",
          st.get("phase"))
    out = th.rx(d, env, "status").stdout
    check("[CP3-T13] a lettered option offers accepting the open findings",
          bool(re.search(r"[A-Z]\)[^\n]*accept", out, re.I)) or "accept-finding" in out.lower(), out)


# ------------------------------------------------------------------------------------------------ CP3-T14: docs

README_MD = ROOT / "README.md"
ORCH_SKILL_MD = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS_MD = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"


def t14_docs_describe_accept_finding_flow():
    for name, path in (("README.md", README_MD), ("skills/ratchet-orchestrator/SKILL.md", ORCH_SKILL_MD),
                       ("skills/ratchet-orchestrator/references/schemas.md", SCHEMAS_MD)):
        try:
            text = path.read_text()
        except OSError as e:
            check(f"[CP3-T14] {name} can be read", False, str(e))
            continue
        low = text.lower()
        check(f"[CP3-T14] {name} describes a user accepting open review findings",
              "accept" in low and "finding" in low, name)
        check(f"[CP3-T14] {name} states the exact reply form `accept: <reason>`",
              bool(re.search(r"accept:\s*<?\s*(your )?reason|\"accept: ", text, re.I)), name)


# ------------------------------------------------------------------------------------------------ CP3-X01: ambiguous bare id across reviewers

def x01_ambiguous_bare_id_refused_qualified_accept_removes_only_that_one():
    env = th.make_env("claude")
    d = th.cp3_project(env, extra_cp={"risk": "normal"})  # normal risk: two reviewers review every round
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rids = sorted(r["id"] for r in rnd["reviewers"])
    check("[CP3-X01] (setup) this round has two independent reviewers", len(rids) == 2, rids)
    a1_std = {"id": "A1", "severity": "blocker", "file": "src/f.js", "description": "standards finding",
              "user_impact": "a user is hurt by the standards issue"}
    a1_corr = {"id": "A1", "severity": "major", "file": "src/f.js", "description": "correctness finding",
               "user_impact": "a user is hurt by the correctness issue"}
    p1 = write_round_review(d, env, rnd, rids[0], [a1_std])
    p2 = write_round_review(d, env, rnd, rids[1], [a1_corr])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", p1, p2)
    check("[CP3-X01] (setup) the round blocks with both reviewers' A1 open", r.returncode == 2, r.stdout + r.stderr)
    opened = open_findings(d)
    check("[CP3-X01] (setup) both reviewers' own A1 are open", sorted(x.get("reviewer") for x in opened) == rids,
          opened)
    tp = Path(d) / "t.jsonl"
    r_bare = th.rx(d, env, "ask", "accept-finding", "CP1", "A1")
    out = r_bare.stdout + r_bare.stderr
    check("[CP3-X01] a bare id naming two reviewers' findings is refused", r_bare.returncode != 0, out)
    check("[CP3-X01] the refusal names both colliding reviewers", all(rid in out for rid in rids), out)
    check("[CP3-X01] nothing was asked: open-findings.json is unchanged", open_findings(d) == opened, open_findings(d))
    qualified = f"{rids[0]}/A1"
    r_q = th.rx(d, env, "ask", "accept-finding", "CP1", qualified)
    check("[CP3-X01] the qualified 'reviewer/id' form is accepted and asks its own question",
          r_q.returncode == 0 and qualified in r_q.stdout, r_q.stdout + r_q.stderr)
    reply(d, tp, env, "accept: fine for now, the other reviewer's A1 still needs a look")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X01] accepting the qualified finding succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    still_open = open_findings(d)
    check("[CP3-X01] only that one reviewer's A1 is removed; the other reviewer's A1 (not shown or agreed to) "
          "remains open", len(still_open) == 1 and still_open[0].get("reviewer") == rids[1]
          and still_open[0].get("id") == "A1", still_open)


# ------------------------------------------------------------------------------------------------ CP3-X02: a write failure leaves no partial accept

_CP3X02_FAIL_SAVE_STATE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ratchet_cp3x02", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def failing_save(self, st):
    raise OSError("simulated disk failure recording the accept")
m.Ctx.save_state = failing_save
m.main(sys.argv[2:])
"""


def x02_write_failure_leaves_no_partial_accept_and_retry_completes_once():
    d, env, tp, rnd, rid = blocked_round("x02")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: known limit for now")
    state_path = Path(d) / ".ratchet/runs/f/state.json"
    before_state = state_path.read_bytes()
    before_open = open_findings(d)
    before_deferred = deferred_findings(d)
    before_learnings = learnings_text(d)
    r_fail = subprocess.run([sys.executable, "-c", _CP3X02_FAIL_SAVE_STATE, str(th.ENGINE), "accept-finding", "CP1"],
                           cwd=d, env=env, capture_output=True, text=True)
    check("[CP3-X02] the simulated write failure crashes the command (it does not report success)",
          r_fail.returncode != 0, r_fail.stdout + r_fail.stderr)
    check("[CP3-X02] state.json is byte-for-byte unchanged by the failed write", state_path.read_bytes() == before_state,
          "state.json changed")
    check("[CP3-X02] open-findings.json still lists F1 as open: nothing claims it was accepted",
          open_findings(d) == before_open, (before_open, open_findings(d)))
    # [CP3-X02 adaptation, round-04 fix: the write order is now learnings.md, then deferred-findings.json, THEN
    # the state commit (never the old state-first order the "heal" machinery assumed) -- so a failure in the
    # state commit itself can leave these two evidence files already written: an extra note and an uncommitted
    # accept, which the new design accepts as harmless (state.json, the one authoritative record, is the thing
    # that is still untouched below, so nothing claims F1 is really accepted; the retry below still finishes it
    # exactly once, never twice).]
    check("[CP3-X02] deferred-findings.json may already carry F1's entry, written before the failed state commit",
          by_id(deferred_findings(d), "F1") is not None, deferred_findings(d))
    check("[CP3-X02] but state.json (the authoritative record) was never committed: F1 is not really accepted yet",
          by_id(step_deferred(d), "F1") is None, step_deferred(d))
    check("[CP3-X02] learnings.md already has exactly one note for F1 too, written before the failed commit",
          len(re.findall(r"Accepted finding \S*F1\b", learnings_text(d))) == 1, learnings_text(d))
    r_retry = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X02] a plain retry (same pending question, same reply) completes the accept",
          r_retry.returncode == 0, r_retry.stdout + r_retry.stderr)
    deferred = deferred_findings(d)
    matches = [x for x in deferred if x.get("id") == "F1" and x.get("accepted")]
    check("[CP3-X02] F1 is recorded accepted exactly once, not left half-done by the earlier failure",
          len(matches) == 1, deferred)
    check("[CP3-X02] F1 is no longer open", by_id(open_findings(d), "F1") is None, open_findings(d))
    check("[CP3-X02] the retry did not duplicate F1's learnings.md note",
          len(re.findall(r"Accepted finding \S*F1\b", learnings_text(d))) == 1, learnings_text(d))


# ------------------------------------------------------------------------------------------------ CP3-X03: director mode needs a director token

def _blocked_round_unconfirmed(name):
    """Like blocked_round(), but on a host whose hook doesn't name sub-agents (codex), so the review file written
    straight to disk still counts as an (unconfirmed) review, with no `hook guard` call. CP3-X03 needs this: it
    enrolls this very project as a director lane afterward, and `director-enroll` correctly refuses once a guard
    hook has already recorded a (non-director) role here, same as it would for any real project a builder's guard
    hook has already touched."""
    env = th.make_env("codex")
    d = th.cp3_project(env)
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    th.cp3_write_review(path, rnd, rid, findings=[F1, F2, F3])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", str(path))
    assert r.returncode == 2, r.stdout + r.stderr  # fixture sanity: the round blocks on F1/F2
    tp = Path(d) / "t.jsonl"
    return d, env, tp, rnd, rid


def x03_director_mode_refuses_chat_accept_needs_director_token():
    d, env, tp, rnd, rid = _blocked_round_unconfirmed("x03")
    denv = dict(env, RATCHET_ROLE="director")
    rk = th.rx(d, denv, "director-key")
    en = th.rx(d, denv, "director-enroll")
    check("[CP3-X03] (setup) director-key and director-enroll succeed", rk.returncode == 0 and en.returncode == 0,
          (rk.stderr, en.stderr))
    r_ask = th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    check("[CP3-X03] (setup) ask accept-finding still works in a director-enrolled lane", r_ask.returncode == 0,
          r_ask.stdout + r_ask.stderr)
    reply(d, tp, env, "accept: a plain chat reply, not a director token")
    r_chat = th.rx(d, env, "accept-finding", "CP1")
    out = r_chat.stdout + r_chat.stderr
    check("[CP3-X03] a chat 'accept: <reason>' reply is refused in a director-enrolled lane",
          r_chat.returncode != 0, out)
    check("[CP3-X03] the refusal says a director token is needed",
          bool(re.search(r"director[- ]enrolled|director token|director-sign", out, re.I)), out)
    check("[CP3-X03] F1 is still open: the chat reply accepted nothing", by_id(open_findings(d), "F1") is not None,
          open_findings(d))
    sg = th.rx(d, denv, "director-sign", "accept-finding")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    check("[CP3-X03] (setup) director-sign accept-finding produces a token", sg.returncode == 0 and tok,
          sg.stdout + sg.stderr)
    r_tok = th.rx(d, env, "accept-finding", "CP1", "--director-token", tok, "--reason", "director accepted this limit")
    check("[CP3-X03] the director-signed token accepts the finding with the director's own reason",
          r_tok.returncode == 0, r_tok.stdout + r_tok.stderr)
    e1 = by_id(deferred_findings(d), "F1")
    check("[CP3-X03] F1 is recorded accepted by the director, with the director's own reason",
          bool(e1) and e1.get("accepted") is True and e1.get("accepted_by") == "director"
          and e1.get("reason") == "director accepted this limit", e1)


# ------------------------------------------------------------------------------------------------ CP3-X04: duplicate ids are deduped

def x04_duplicate_ids_deduped_to_one_acceptance():
    d, env, tp, rnd, rid = blocked_round("x04")
    key = f"{rid}/F1"  # the canonical identity: F1's own reviewer, qualified (standards: identify by reviewer+id)
    r_ask = th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F1,F1")
    check("[CP3-X04] ask accept-finding dedupes a repeated id", r_ask.returncode == 0, r_ask.stdout + r_ask.stderr)
    pending_ids = (th.cp3_state(d).get("pending") or {}).get("ids")
    check("[CP3-X04] only one id is recorded pending, not three", pending_ids == [key], pending_ids)
    reply(d, tp, env, "accept: only once please")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X04] accept-finding succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    deferred = deferred_findings(d)
    matches = [x for x in deferred if x.get("id") == "F1" and x.get("accepted")]
    check("[CP3-X04] F1 is recorded accepted exactly once in deferred-findings.json, not three times",
          len(matches) == 1, deferred)
    step_matches = [x for x in step_deferred(d) if x.get("id") == "F1" and x.get("accepted")]
    check("[CP3-X04] the step's own deferred list also has exactly one accepted entry for F1", len(step_matches) == 1,
          step_matches)
    # [CP3-X04 adaptation, round-04 fix: the marker now carries the run slug/checkpoint/round too, not just the
    # bare reviewer/id, so a later acceptance of the same reviewer/id in a different round or checkpoint is never
    # silently dropped (see CP3-X11); the substring anchor below moves with it, the assertion itself does not.]
    learn_count = learnings_text(d).count(f"/{key} (")
    check("[CP3-X04] learnings.md gains exactly one entry for F1, not three", learn_count == 1, learnings_text(d))


# ------------------------------------------------------------------------------------------------ CP3-X05/X06: a stale open-findings.json (an
# interruption AFTER the state commit, not before) never outranks state on a retry

_CP3X05_FAIL_OPEN_FINDINGS_WRITE = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp3x05", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
orig_write_json = m.write_json
def failing_write_json(path, data):
    if Path(path).name == "open-findings.json":
        raise OSError("simulated disk failure writing open-findings.json")
    return orig_write_json(path, data)
m.write_json = failing_write_json
m.main(sys.argv[2:])
"""


def _accept_f1_with_interrupted_open_findings_write(name):
    """blocked_round(name), then accept F1 (F2 stays open) through a run whose open-findings.json write fails
    AFTER state.json is already committed (deferred-findings.json and learnings.md, written before it in
    cmd_accept_finding's own order, still succeed) -- the exact partial-write window CP3-X02 doesn't cover.
    Returns (d, env, tp, rnd, rid, r_fail)."""
    d, env, tp, rnd, rid = blocked_round(name)
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: known limit for now, F2 still needs a look")
    r_fail = subprocess.run([sys.executable, "-c", _CP3X05_FAIL_OPEN_FINDINGS_WRITE, str(th.ENGINE),
                             "accept-finding", "CP1"], cwd=d, env=env, capture_output=True, text=True)
    return d, env, tp, rnd, rid, r_fail


def x05_interrupted_write_after_state_commit_refuses_duplicate_on_retry():
    d, env, tp, rnd, rid, r_fail = _accept_f1_with_interrupted_open_findings_write("x05")
    check("[CP3-X05] the simulated failure writing open-findings.json crashes the command (no success reported)",
          r_fail.returncode != 0, r_fail.stdout + r_fail.stderr)
    e1_state = by_id(step_deferred(d), "F1")
    check("[CP3-X05] F1 is committed to state as accepted despite the later write failure",
          bool(e1_state) and e1_state.get("accepted") is True, e1_state)
    check("[CP3-X05] deferred-findings.json already has F1's accepted entry (written before the failure point)",
          by_id(deferred_findings(d), "F1") is not None, deferred_findings(d))
    stale = open_findings(d)
    check("[CP3-X05] (setup) open-findings.json is left stale, still listing F1 as open",
          by_id(stale, "F1") is not None, stale)
    r_ask2 = th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    check("[CP3-X05] (retry) asking to accept F1 again is still accepted (lets accept-finding itself refuse it)",
          r_ask2.returncode == 0, r_ask2.stdout + r_ask2.stderr)
    reply(d, tp, env, "accept: trying again after the crash")
    r_acc2 = th.rx(d, env, "accept-finding", "CP1")
    out2 = r_acc2.stdout + r_acc2.stderr
    check("[CP3-X05] (retry) accept-finding refuses F1 as already accepted, not recording a second acceptance",
          r_acc2.returncode != 0 and bool(re.search(r"already (closed|accepted)", out2, re.I)), out2)
    matches = [x for x in deferred_findings(d) if x.get("id") == "F1" and x.get("accepted")]
    check("[CP3-X05] exactly one accepted entry for F1 remains in deferred-findings.json, not two",
          len(matches) == 1, deferred_findings(d))
    step_matches = [x for x in step_deferred(d) if x.get("id") == "F1" and x.get("accepted")]
    check("[CP3-X05] exactly one accepted entry for F1 remains in the step's own deferred list, not two",
          len(step_matches) == 1, step_matches)
    f1_learn_count = len(re.findall(r"Accepted finding \S*F1\b", learnings_text(d)))
    check("[CP3-X05] learnings.md gains exactly one entry for F1, not two", f1_learn_count == 1, learnings_text(d))


def x06_other_still_open_finding_survives_the_interruption_and_gate_stays_blocked():
    d, env, tp, rnd, rid, r_fail = _accept_f1_with_interrupted_open_findings_write("x06")
    check("[CP3-X06] (setup) the simulated failure crashes the command", r_fail.returncode != 0,
          r_fail.stdout + r_fail.stderr)
    gate = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-X06] F2 is still blocking: the gate did not pass despite the stale evidence file",
          gate.get("status") != "passed", gate.get("status"))
    r_ask = th.rx(d, env, "ask", "accept-finding", "CP1", "F2")
    check("[CP3-X06] F2, the genuinely still-open finding, can still be asked about normally",
          r_ask.returncode == 0, r_ask.stdout + r_ask.stderr)
    reply(d, tp, env, "accept: F2 too, let's ship")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X06] accepting F2 succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    gate2 = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-X06] once F2 is accepted too, the gate passes without another round",
          gate2.get("status") == "passed", gate2.get("status"))
    check("[CP3-X06] open-findings.json is healed back to empty once both findings are accounted for",
          open_findings(d) == [], open_findings(d))


# ------------------------------------------------------------------------------------------------ CP3-X07: accepting at
# the round limit must actually unblock the run (not just pass the gate)

def x07_accepting_at_round_limit_unblocks_and_complete_works():
    d, env, tp = round_limit_project("x07")
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X07] (setup) the round blocks at the round limit", r.returncode == 2, r.stdout + r.stderr)
    st0 = th.cp3_state(d)
    check("[CP3-X07] (setup) the run is blocked", st0.get("phase") == "blocked", st0.get("phase"))
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: known tradeoff, fine")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X07] accept-finding succeeds at the round limit", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    out = r_acc.stdout + r_acc.stderr
    check("[CP3-X07] the reply says the run is no longer blocked",
          bool(re.search(r"no longer blocked", out, re.I)), out)
    st1 = th.cp3_state(d)
    check("[CP3-X07] the run is no longer blocked (phase moved on)", st1.get("phase") != "blocked", st1.get("phase"))
    gate = st1["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-X07] the adversarial gate is passed", gate.get("status") == "passed", gate)
    status_out = th.rx(d, env, "status").stdout
    check("[CP3-X07] status no longer reports open concerns for CP1",
          "open concern" not in status_out.lower(), status_out)
    check("[CP3-X07] status no longer offers accepting the open findings (nothing left to accept)",
          not re.search(r"[A-Z]\)[^\n]*accept the open finding", status_out, re.I), status_out)
    next_out = th.rx(d, env, "next").stdout
    check("[CP3-X07] `next` no longer reports the run blocked",
          '"phase": "blocked"' not in next_out, next_out)
    r_complete = th.rx(d, env, "complete", "CP1")
    check("[CP3-X07] `complete` now works (the run was truly unblocked, not just the gate)",
          r_complete.returncode == 0, r_complete.stdout + r_complete.stderr)


# ------------------------------------------------------------------------------------------------ CP3-X08: the final
# review's own command (`deferred`) shows accepted findings in their own section

def x08_deferred_lists_accepted_findings_in_their_own_section():
    d, env, tp, rnd, rid = blocked_round("x08")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: known tradeoff for this release")
    r = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X08] (setup) accept-finding succeeds for F1,F2", r.returncode == 0, r.stdout + r.stderr)
    if r.returncode != 0:
        return
    out = th.rx(d, env, "deferred").stdout
    check("[CP3-X08] `deferred` shows F1 with its accepted reason",
          "F1" in out and "known tradeoff for this release" in out, out)
    check("[CP3-X08] `deferred` shows F2 with its accepted reason",
          "F2" in out and "known tradeoff for this release" in out, out)
    check("[CP3-X08] `deferred` names who accepted them", "human" in out.lower(), out)
    check("[CP3-X08] F3 (a plain minor that never blocked) is listed under its own 'did not block' header",
          "did not block" in out and "F3" in out.split("did not block", 1)[1], out)
    after_block = out.split("did not block", 1)[1] if "did not block" in out else ""
    check("[CP3-X08] F1 is NOT listed under the 'did not block' header (it was accepted, not merely non-blocking)",
          "F1" not in after_block, out)
    check("[CP3-X08] F2 is NOT listed under the 'did not block' header either",
          "F2" not in after_block, out)
    f1_idx, f2_idx, f3_idx = out.find("F1"), out.find("F2"), out.find("F3")
    check("[CP3-X08] the accepted blocker/major (F1, F2) appear before the plain minor F3",
          f1_idx != -1 and f2_idx != -1 and f3_idx != -1 and f1_idx < f3_idx and f2_idx < f3_idx, out)


# ------------------------------------------------------------------------------------------------ CP3-X09 [round-04
# adaptation: the "heal from state" design this test covered (deferred-findings.json/learnings.md self-repairing
# from a LATER command after a crash right after the state commit) was removed by the orchestrator's round-04
# simplification. The new order writes learnings.md (a), then deferred-findings.json (b), THEN commits state (c),
# so a failure in either (a) or (b) now happens BEFORE anything is committed: at most a harmless extra note is
# left on disk, never a committed accept, and there is nothing left to "heal" (status/deferred write nothing any
# more). This test now covers that: a crash in either earlier write commits nothing to state, and a plain retry
# still completes the accept exactly once, with no duplicate note.]

_CP3X09_FAIL_DEFERRED_WRITE = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp3x09a", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
orig_write_json = m.write_json
def failing_write_json(path, data):
    if Path(path).name == "deferred-findings.json":
        raise OSError("simulated disk failure writing deferred-findings.json")
    return orig_write_json(path, data)
m.write_json = failing_write_json
m.main(sys.argv[2:])
"""

_CP3X09_FAIL_LEARNINGS_APPEND = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ratchet_cp3x09b", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def failing_append(ctx, text, source):
    raise OSError("simulated disk failure appending to learnings.md")
m._append_learning = failing_append
m.main(sys.argv[2:])
"""


def x09_crash_before_the_state_commit_commits_nothing_and_a_retry_completes_once():
    # Part A: the deferred-findings.json write (b) fails AFTER the learnings.md write (a) already succeeded: the
    # one "extra note, nothing committed" case the new order accepts as harmless.
    d, env, tp, rnd, rid = blocked_round("x09a")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: known limit, not yet committed (A)")
    r_fail = subprocess.run([sys.executable, "-c", _CP3X09_FAIL_DEFERRED_WRITE, str(th.ENGINE),
                             "accept-finding", "CP1"], cwd=d, env=env, capture_output=True, text=True)
    check("[CP3-X09] (A) the simulated failure writing deferred-findings.json crashes the command",
          r_fail.returncode != 0, r_fail.stdout + r_fail.stderr)
    check("[CP3-X09] (A) state.json was never committed: F1 is not accepted",
          by_id(step_deferred(d), "F1") is None, step_deferred(d))
    check("[CP3-X09] (A) deferred-findings.json was never written (the failure happened on that very write)",
          by_id(deferred_findings(d), "F1") is None, deferred_findings(d))
    check("[CP3-X09] (A) learnings.md already has F1's note though: the one harmless extra note, written before "
          "the state commit that never happened",
          len(re.findall(r"Accepted finding \S*F1\b", learnings_text(d))) == 1, learnings_text(d))
    check("[CP3-X09] (A) F1 is still open: nothing claims it was accepted", by_id(open_findings(d), "F1") is not None,
          open_findings(d))
    r_status = th.rx(d, env, "status")
    check("[CP3-X09] (A) status succeeds and writes nothing (no heal any more)", r_status.returncode == 0,
          r_status.stdout + r_status.stderr)
    check("[CP3-X09] (A) status did not write deferred-findings.json", by_id(deferred_findings(d), "F1") is None,
          deferred_findings(d))
    r_retry = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X09] (A) a plain retry (same pending question, same reply) completes the accept",
          r_retry.returncode == 0, r_retry.stdout + r_retry.stderr)
    check("[CP3-X09] (A) the retry's learnings.md append is skipped as already-present: still exactly one entry",
          len(re.findall(r"Accepted finding \S*F1\b", learnings_text(d))) == 1, learnings_text(d))
    check("[CP3-X09] (A) deferred-findings.json now has F1's entry, written once by the completed retry",
          len([x for x in deferred_findings(d) if x.get("id") == "F1" and x.get("accepted")]) == 1,
          deferred_findings(d))
    check("[CP3-X09] (A) F1 is no longer open", by_id(open_findings(d), "F1") is None, open_findings(d))

    # Part B: the learnings.md append (a) itself fails -- the very first write, so nothing at all reaches disk yet.
    d2, env2, tp2, rnd2, rid2 = blocked_round("x09b")
    th.rx(d2, env2, "ask", "accept-finding", "CP1", "F2")
    reply(d2, tp2, env2, "accept: known limit, not yet committed (B)")
    r_fail2 = subprocess.run([sys.executable, "-c", _CP3X09_FAIL_LEARNINGS_APPEND, str(th.ENGINE),
                             "accept-finding", "CP1"], cwd=d2, env=env2, capture_output=True, text=True)
    check("[CP3-X09] (B) the simulated failure appending to learnings.md crashes the command",
          r_fail2.returncode != 0, r_fail2.stdout + r_fail2.stderr)
    check("[CP3-X09] (B) state.json was never committed: F2 is not accepted",
          by_id(step_deferred(d2), "F2") is None, step_deferred(d2))
    check("[CP3-X09] (B) learnings.md gained no entry at all: it is the very first write, and it failed",
          not re.search(r"Accepted finding \S*F2\b", learnings_text(d2)), learnings_text(d2))
    check("[CP3-X09] (B) deferred-findings.json was never reached either (it comes after the failed write)",
          by_id(deferred_findings(d2), "F2") is None, deferred_findings(d2))
    r_retry2 = th.rx(d2, env2, "accept-finding", "CP1")
    check("[CP3-X09] (B) a plain retry completes the accept", r_retry2.returncode == 0,
          r_retry2.stdout + r_retry2.stderr)
    check("[CP3-X09] (B) learnings.md now has exactly one entry for F2",
          len(re.findall(r"Accepted finding \S*F2\b", learnings_text(d2))) == 1, learnings_text(d2))
    check("[CP3-X09] (B) deferred-findings.json has exactly one accepted entry for F2",
          len([x for x in deferred_findings(d2) if x.get("id") == "F2" and x.get("accepted")]) == 1,
          deferred_findings(d2))


# ------------------------------------------------------------------------------------------------ CP3-X10: the round's
# own record is found by its stamped round number, not by list position, after a round was replaced before recording

def x10_round_record_found_by_number_after_a_replaced_round():
    env = th.make_env("claude")
    d = th.cp3_project(env)
    th.cp3_pass_behavior(d, env)
    rnd1 = th.cp3_open_round(d, env)  # round 1 opened, never recorded
    check("[CP3-X10] (setup) round 1 opened", rnd1.get("round") == 1, rnd1)
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // changed before round 1 was recorded\n")
    br = th.rx(d, env, "behavior", "CP1")
    check("[CP3-X10] (setup) behavior passes again after the code change", br.returncode == 0, br.stdout + br.stderr)
    rnd2 = th.cp3_open_round(d, env)  # round 1 is replaced by round 2 (fingerprint changed before it was recorded)
    check("[CP3-X10] (setup) round 1 was replaced by round 2 without ever being recorded", rnd2.get("round") == 2,
          rnd2)
    rid2 = th.cp3_R0(rnd2)
    path = write_round_review(d, env, rnd2, rid2, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X10] (setup) round 2 blocks with F1,F2 open", r.returncode == 2, r.stdout + r.stderr)
    gate_before = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-X10] (setup) only one round is on record (round 1 was replaced, never recorded)",
          len(gate_before.get("rounds") or []) == 1, gate_before.get("rounds"))
    tp = Path(d) / "t.jsonl"
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: known tradeoff after a replaced round")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X10] accept-finding succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    gate_after = th.cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    rounds = gate_after.get("rounds") or []
    check("[CP3-X10] the round record on disk is stamped with its own round number (2), not left to position",
          len(rounds) == 1 and rounds[0].get("round") == 2, rounds)
    accepted_ids = sorted(str(a.get("id", "")).rsplit("/", 1)[-1] for a in (rounds[0].get("accepted") or []))
    check("[CP3-X10] the acceptance IS marked in that round's own record, found by round number not list position",
          accepted_ids == ["F1", "F2"], rounds[0])


# ------------------------------------------------------------------------------------------------ CP3-X11: the same
# reviewer/id, accepted in two different rounds of the same checkpoint, reaches learnings.md both times, each with
# its own reason (round-04 fix: the marker is unique per run/checkpoint/round, not just reviewer/id)

def x11_same_reviewer_id_accepted_in_two_rounds_both_reach_learnings_with_their_own_reasons():
    d, env, tp, rnd, rid = blocked_round("x11")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: round one's own reason")
    r1 = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X11] (setup) round 1's F1 is accepted (F2 stays open)", r1.returncode == 0, r1.stdout + r1.stderr)
    tr = th.rx(d, env, "triage", "CP1", f"{rid}/F2=fix:redact the card number from logs")
    assert tr.returncode == 0, tr.stdout + tr.stderr  # F2 is still open and needs its own decision before round 2
    r_open = th.rx(d, env, "reviewers", "CP1", "--json")
    check("[CP3-X11] (setup) a second round opens for CP1", r_open.returncode == 0, r_open.stdout + r_open.stderr)
    rnd2 = json.loads(r_open.stdout)
    check("[CP3-X11] (setup) this really is round 2", rnd2.get("round") == 2, rnd2)
    path2 = write_round_review(d, env, rnd2, rid, [F1])  # same reviewer, same bare id "F1": a NEW, different finding
    r2 = th.rx(d, env, "adv-record", "CP1", "--reviews", path2)
    check("[CP3-X11] (setup) round 2 blocks with its own F1 open", r2.returncode == 2, r2.stdout + r2.stderr)
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: round two's own, different reason")
    r3 = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X11] round 2's F1 is also accepted", r3.returncode == 0, r3.stdout + r3.stderr)
    text = learnings_text(d)
    check("[CP3-X11] learnings.md carries round 1's own reason", "round one's own reason" in text, text)
    check("[CP3-X11] learnings.md ALSO carries round 2's own, different reason (not silently dropped)",
          "round two's own, different reason" in text, text)
    check("[CP3-X11] two distinct lines were written for this repeated reviewer/id, not deduped into one",
          len(re.findall(r"Accepted finding \S*F1\b", text)) == 2, text)


# ------------------------------------------------------------------------------------------------ CP3-X12: accepting
# at the run-wide round cap resets it, so the very next `reviewers` call (a fresh checkpoint) still works

def run_wide_cap_project(name):
    """Two checkpoints (CP1, CP2, both low risk, adversarial required), max_adversarial_rounds_run=1 set BEFORE
    the plan is approved, max_adversarial_rounds left at its default (high) so only the RUN-WIDE cap ever blocks,
    never CP1's own per-checkpoint one -- the exact scenario the run-wide-cap reset bug needs. Returns (d, env, tp)."""
    env = th.make_env("claude")
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp3af-rw-{name}-"))
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
    th.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", "max_adversarial_rounds_run=1")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s2", "done_when": ["works too"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp2.test.js"], "command": "test -f src/g.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp1, cp2]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))
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
    return d, env, tp


def _cp2_pass_behavior(d, env):
    """Mirrors trust_helpers.cp3_pass_behavior, but for CP2 of run_wide_cap_project: that fixture's CP2 checks for
    src/g.js (CP1 already created src/f.js, so re-using that file would start CP2 already green, never red)."""
    th.cp3_write_tests(d, "CP2")
    assert th.rx(d, env, "tests-written", "CP2").returncode == 0
    assert th.rx(d, env, "red", "CP2").returncode == 0
    (Path(d) / "src/g.js").write_text("export const g = () => 1;\n")
    assert th.rx(d, env, "implemented", "CP2").returncode == 0
    r = th.rx(d, env, "behavior", "CP2")
    assert r.returncode == 0, r.stderr + r.stdout


def x12_accepting_at_the_run_wide_cap_resets_it_so_a_fresh_checkpoint_can_still_open_a_round():
    d, env, tp = run_wide_cap_project("x12")
    th.cp3_pass_behavior(d, env, "CP1")
    rnd = th.cp3_open_round(d, env, "CP1")  # uses the run's one allowed round
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X12] (setup) CP1's round blocks with F1,F2 open", r.returncode == 2, r.stdout + r.stderr)
    th.rx(d, env, "triage", "CP1", f"{rid}/F1=fix:known tradeoff", f"{rid}/F2=fix:known tradeoff")
    r2 = th.rx(d, env, "reviewers", "CP1", "--json")
    out2 = r2.stdout + r2.stderr
    check("[CP3-X12] (setup) opening a second round for CP1 hits the RUN-WIDE cap, not a per-checkpoint one",
          r2.returncode == 2 and "run-wide" in out2.lower(), out2)
    st0 = th.cp3_state(d)
    check("[CP3-X12] (setup) the run is blocked", st0.get("phase") == "blocked", st0.get("phase"))
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: known tradeoff, ship CP1")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X12] accept-finding succeeds at the run-wide cap", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    st1 = th.cp3_state(d)
    check("[CP3-X12] the run-wide round budget is reset back to 0", st1.get("adv_rounds_total", -1) == 0,
          st1.get("adv_rounds_total"))
    check("[CP3-X12] the run is no longer blocked", st1.get("phase") != "blocked", st1.get("phase"))
    r_complete = th.rx(d, env, "complete", "CP1")
    check("[CP3-X12] CP1 completes (the run was truly unblocked, not just the gate)", r_complete.returncode == 0,
          r_complete.stdout + r_complete.stderr)
    _cp2_pass_behavior(d, env)
    r3 = th.rx(d, env, "reviewers", "CP2", "--json")
    check("[CP3-X12] the very next `reviewers` call (CP2, a fresh checkpoint) opens a round instead of "
          "immediately re-blocking on the same run-wide cap", r3.returncode == 0, r3.stdout + r3.stderr)


# ------------------------------------------------------------------------------------------------ CP3-X13: the
# stored block choice (picked from a lettered option, e.g. "stop here") is consumed by accept-finding: a LATER
# block still needs its own, fresh answer. Accepting itself is never one of those lettered options; it only ever
# goes through the plain accept_note line (`ask accept-finding` + the user's own `accept: <reason>` reply), for
# either block kind, the per-checkpoint round limit or the run-wide cap.

def x13_stored_block_choice_is_consumed_a_later_block_needs_a_new_answer():
    d, env, tp = round_limit_project("x13")
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X13] (setup) the round blocks at the round limit", r.returncode == 2, r.stdout + r.stderr)
    th.rx(d, env, "ask", "choice", "--options", "A,B,C")
    reply(d, tp, env, "B")
    r_choice = th.rx(d, env, "choice")
    check("[CP3-X13] (setup) `choice` records the user's pick", r_choice.returncode == 0,
          r_choice.stdout + r_choice.stderr)
    allow0 = th.cp3_state(d).get("choice_allow") or {}
    check("[CP3-X13] (setup) choice_allow['unblock'] is now set", allow0.get("unblock") is True, allow0)
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    reply(d, tp, env, "accept: fine, ship it")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X13] accept-finding succeeds and unblocks the run", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    allow1 = th.cp3_state(d).get("choice_allow")
    check("[CP3-X13] accept-finding consumed the stored block choice: no stale 'unblock' left for a later block",
          not (allow1 or {}).get("unblock"), allow1)
    r_reblock = th.rx(d, env, "reviewers", "CP1")
    check("[CP3-X13] (setup) CP1 blocks again at the same round limit (attempts were not reset by the accept)",
          r_reblock.returncode == 2, r_reblock.stdout + r_reblock.stderr)
    st2 = th.cp3_state(d)
    check("[CP3-X13] (setup) the run is blocked again", st2.get("phase") == "blocked", st2.get("phase"))
    r_bare_approve = th.rx(d, env, "human-approve")
    out2 = r_bare_approve.stdout + r_bare_approve.stderr
    check("[CP3-X13] a bare human-approve, with no fresh question, is refused: the earlier choice cannot unblock "
          "this new block", r_bare_approve.returncode != 0, out2)
    check("[CP3-X13] the run is still blocked", th.cp3_state(d).get("phase") == "blocked",
          th.cp3_state(d).get("phase"))


# ------------------------------------------------------------------------------------------------ CP3-X14: accepting
# is refused once the code changed since the reviewed round, with the new, plain message

def x14_accept_refused_after_a_code_change_since_the_round():
    d, env, tp, rnd, rid = blocked_round("x14")
    r_ask = th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F2")
    check("[CP3-X14] (setup) ask accept-finding succeeds before any code change", r_ask.returncode == 0,
          r_ask.stdout + r_ask.stderr)
    reply(d, tp, env, "accept: fine, ship it")
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // changed after the round, before accept\n")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    out = r_acc.stdout + r_acc.stderr
    check("[CP3-X14] accept-finding refuses once the code changed since the round", r_acc.returncode != 0, out)
    check("[CP3-X14] the refusal says plainly the code changed and a new round is needed",
          "the code changed since this review round" in out.lower() and "new review round" in out.lower(), out)
    check("[CP3-X14] nothing was accepted: F1 is still open", by_id(open_findings(d), "F1") is not None,
          open_findings(d))
    r_ask2 = th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    out2 = r_ask2.stdout + r_ask2.stderr
    check("[CP3-X14] a fresh `ask accept-finding` is refused too, for the same reason",
          r_ask2.returncode != 0 and "the code changed since this review round" in out2.lower(), out2)


# ------------------------------------------------------------------------------------------------ CP3-X15: status
# and deferred never write files any more (the heal machinery is gone): every file is left byte-identical

def x15_status_and_deferred_leave_every_file_byte_identical():
    d, env, tp, rnd, rid = blocked_round("x15")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    reply(d, tp, env, "accept: known limit for now")
    r = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X15] (setup) accept-finding succeeds", r.returncode == 0, r.stdout + r.stderr)
    state_path = Path(d) / ".ratchet/runs/f/state.json"
    deferred_path = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial/round-01/deferred-findings.json"
    open_path = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial/round-01/open-findings.json"
    learnings_path = Path(d) / ".ratchet/learnings.md"
    paths = [p for p in (state_path, deferred_path, open_path, learnings_path) if p.exists()]
    before = {str(p): p.read_bytes() for p in paths}
    r_s1 = th.rx(d, env, "status")
    r_s2 = th.rx(d, env, "status", "--json")
    r_d1 = th.rx(d, env, "deferred")
    r_d2 = th.rx(d, env, "deferred", "--json")
    check("[CP3-X15] (setup) all four read-only commands succeed",
          all(r.returncode == 0 for r in (r_s1, r_s2, r_d1, r_d2)),
          (r_s1.stderr, r_s2.stderr, r_d1.stderr, r_d2.stderr))
    after = {str(p): p.read_bytes() for p in paths}
    check("[CP3-X15] status/status --json/deferred/deferred --json write nothing: every file is byte-identical",
          before == after, {k: (len(v), len(after.get(k, b""))) for k, v in before.items()})


# ------------------------------------------------------------------------------------------------ CP3-X16: one
# reviewer's own two "A1" findings get distinct ids, and accepting one leaves the other open

def x16_duplicate_ids_from_one_reviewer_get_their_own_open_ids():
    env = th.make_env("claude")
    d = th.cp3_project(env)
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    a1a = {"id": "A1", "severity": "blocker", "file": "src/f.js", "description": "first A1: crashes on a null cart",
           "user_impact": "a shopper's checkout crashes and they lose their cart"}
    a1b = {"id": "A1", "severity": "major", "file": "src/f.js", "description": "second A1: logs the card number",
           "user_impact": "a shopper's card number is written to a log file other staff can read"}
    path = write_round_review(d, env, rnd, rid, [a1a, a1b])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X16] (setup) the round blocks on the one reviewer's two 'A1' findings", r.returncode == 2,
          r.stdout + r.stderr)
    ids = sorted(f.get("id") for f in open_findings(d))
    check("[CP3-X16] open-findings.json lists two distinct ids, not two copies of 'A1'",
          ids == ["A1", "A1-dup2"], ids)
    tp = Path(d) / "t.jsonl"
    th.rx(d, env, "ask", "accept-finding", "CP1", "A1")
    reply(d, tp, env, "accept: fine for now, the first one only")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X16] accepting 'A1' succeeds", r_acc.returncode == 0, r_acc.stdout + r_acc.stderr)
    remaining = sorted(f.get("id") for f in open_findings(d))
    check("[CP3-X16] 'A1-dup2' (the reviewer's second, different finding) is still open",
          remaining == ["A1-dup2"], remaining)


# ------------------------------------------------------------------------------------------------ CP3-X17:
# a director token with no --reason is refused and left usable (not spent) for the same token plus --reason

def x17_director_token_without_reason_is_refused_and_not_consumed():
    d, env, tp, rnd, rid = _blocked_round_unconfirmed("x17")
    denv = dict(env, RATCHET_ROLE="director")
    rk = th.rx(d, denv, "director-key")
    en = th.rx(d, denv, "director-enroll")
    check("[CP3-X17] (setup) director-key and director-enroll succeed", rk.returncode == 0 and en.returncode == 0,
          (rk.stderr, en.stderr))
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    sg = th.rx(d, denv, "director-sign", "accept-finding")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    check("[CP3-X17] (setup) director-sign accept-finding produces a token", sg.returncode == 0 and tok,
          sg.stdout + sg.stderr)
    r_no_reason = th.rx(d, env, "accept-finding", "CP1", "--director-token", tok)
    out = r_no_reason.stdout + r_no_reason.stderr
    check("[CP3-X17] accept-finding with a director token but no --reason is refused",
          r_no_reason.returncode != 0, out)
    check("[CP3-X17] the refusal names --reason as what is missing", "--reason" in out, out)
    check("[CP3-X17] nothing was accepted: F1 is still open", by_id(open_findings(d), "F1") is not None,
          open_findings(d))
    st = th.cp3_state(d)
    check("[CP3-X17] the question is still pending: the forgotten --reason did not spend the token",
          (st.get("pending") or {}).get("action") == "accept-finding", st.get("pending"))
    r_tok = th.rx(d, env, "accept-finding", "CP1", "--director-token", tok, "--reason", "director accepted this limit")
    check("[CP3-X17] the SAME token plus --reason now succeeds", r_tok.returncode == 0,
          r_tok.stdout + r_tok.stderr)
    e1 = by_id(deferred_findings(d), "F1")
    check("[CP3-X17] F1 is recorded accepted by the director, with the director's own reason",
          bool(e1) and e1.get("accepted") is True and e1.get("accepted_by") == "director", e1)


# ------------------------------------------------------------------------------------------------ CP3-X18:
# a lettered block option never offers accepting a finding, at either block cause

def x18_lettered_block_options_never_offer_accepting_a_finding():
    d, env, tp = round_limit_project("x18")
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X18] (setup) the round blocks at the per-checkpoint round limit", r.returncode == 2,
          r.stdout + r.stderr)
    nj = json.loads(th.rx(d, env, "next").stdout)
    opts = nj.get("options") or []
    check("[CP3-X18] per-checkpoint block: no lettered option mentions accepting",
          bool(opts) and all("accept" not in o.get("text", "").lower() for o in opts), opts)

    d2, env2, tp2 = run_wide_cap_project("x18b")
    th.cp3_pass_behavior(d2, env2, "CP1")
    rnd2 = th.cp3_open_round(d2, env2, "CP1")
    rid2 = th.cp3_R0(rnd2)
    path2 = write_round_review(d2, env2, rnd2, rid2, [F1, F2])
    th.rx(d2, env2, "adv-record", "CP1", "--reviews", path2)
    th.rx(d2, env2, "triage", "CP1", f"{rid2}/F1=fix:known tradeoff", f"{rid2}/F2=fix:known tradeoff")
    th.rx(d2, env2, "reviewers", "CP1", "--json")
    st2 = th.cp3_state(d2)
    check("[CP3-X18] (setup) run-wide cap blocked the run", st2.get("phase") == "blocked", st2.get("phase"))
    nj2 = json.loads(th.rx(d2, env2, "next").stdout)
    opts2 = nj2.get("options") or []
    check("[CP3-X18] run-wide cap block: no lettered option mentions accepting",
          bool(opts2) and all("accept" not in o.get("text", "").lower() for o in opts2), opts2)


# ------------------------------------------------------------------------------------------------ CP3-X19:
# picking a letter, then human-approve, never accepts a finding

def x19_picking_a_letter_then_human_approve_never_accepts_a_finding():
    d, env, tp = round_limit_project("x19")
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X19] (setup) the round blocks", r.returncode == 2, r.stdout + r.stderr)
    before_learnings = learnings_text(d)
    th.rx(d, env, "ask", "choice", "--options", "A,B")
    reply(d, tp, env, "A")
    r_choice = th.rx(d, env, "choice")
    check("[CP3-X19] (setup) choice records the pick", r_choice.returncode == 0, r_choice.stdout + r_choice.stderr)
    r_ha = th.rx(d, env, "human-approve")
    check("[CP3-X19] human-approve resumes the run on the stored pick", r_ha.returncode == 0,
          r_ha.stdout + r_ha.stderr)
    st = th.cp3_state(d)
    check("[CP3-X19] the run is no longer blocked", st.get("phase") != "blocked", st.get("phase"))
    check("[CP3-X19] F1 and F2 are still open: picking a letter accepted nothing",
          by_id(open_findings(d), "F1") is not None and by_id(open_findings(d), "F2") is not None,
          open_findings(d))
    check("[CP3-X19] nothing is recorded accepted in the step's deferred list",
          not any(f.get("accepted") for f in step_deferred(d)), step_deferred(d))
    check("[CP3-X19] learnings.md gained no 'Accepted finding' line",
          learnings_text(d).count("Accepted finding") == before_learnings.count("Accepted finding"),
          learnings_text(d))


# ------------------------------------------------------------------------------------------------ CP3-X20: the
# plain accept line (never a letter) names `ask accept-finding` when open findings exist for the current code

def x20_plain_accept_line_shown_when_open_findings_exist_for_the_current_code():
    d, env, tp = run_wide_cap_project("x20")
    th.cp3_pass_behavior(d, env, "CP1")
    rnd = th.cp3_open_round(d, env, "CP1")
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    th.rx(d, env, "triage", "CP1", f"{rid}/F1=fix:known tradeoff", f"{rid}/F2=fix:known tradeoff")
    th.rx(d, env, "reviewers", "CP1", "--json")
    st = th.cp3_state(d)
    check("[CP3-X20] (setup) the run is blocked at the run-wide cap", st.get("phase") == "blocked", st.get("phase"))
    nj = json.loads(th.rx(d, env, "next").stdout)
    note = nj.get("accept_note") or ""
    check("[CP3-X20] next names the real way to accept them: ask accept-finding plus accept: <reason>",
          "ask accept-finding" in note.lower() and "accept: <reason>" in note, note)
    check("[CP3-X20] it is not offered as a letter", not re.match(r"[A-Z]\)", note), note)
    out = th.rx(d, env, "status").stdout
    check("[CP3-X20] status prints the same plain line",
          "ask accept-finding" in out.lower() and "accept: <reason>" in out, out)


# ------------------------------------------------------------------------------------------------ CP3-X21: the
# fresh-look line (not the accept line) is shown once the code changed since the blocked round

def x21_fresh_look_line_shown_once_the_code_changed_since_the_round():
    d, env, tp = round_limit_project("x21")
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [F1, F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X21] (setup) the round blocks at the per-checkpoint limit", r.returncode == 2,
          r.stdout + r.stderr)
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // changed after the round\n")
    nj = json.loads(th.rx(d, env, "next").stdout)
    note = nj.get("accept_note") or ""
    check("[CP3-X21] next points at raising the limit for a fresh round, not at accepting",
          "fresh look" in note.lower() and "raise the limit" in note.lower() and "accept: <reason>" not in note,
          note)
    out = th.rx(d, env, "status").stdout
    check("[CP3-X21] status shows the same fresh-look line", "fresh look" in out.lower(), out)


# ------------------------------------------------------------------------------------------------ CP3-X22: a
# multi-line accept reason is collapsed to one learnings.md line; the full text stays in state/deferred

def x22_multi_line_reason_becomes_one_learnings_line():
    d, env, tp, rnd, rid = blocked_round("x22")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    multi = "accept: fine for the beta because\nonly admins hit it\nand it is fixed in v2"
    reply(d, tp, env, multi)
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X22] accept-finding succeeds with a multi-line reason", r_acc.returncode == 0,
          r_acc.stdout + r_acc.stderr)
    learn = learnings_text(d)
    lines_with_marker = [ln for ln in learn.splitlines() if "Accepted finding" in ln and "F1" in ln]
    check("[CP3-X22] exactly one learnings.md line for this acceptance", len(lines_with_marker) == 1, learn)
    check("[CP3-X22] that line carries the reason text on one line, whitespace collapsed",
          bool(lines_with_marker) and "only admins hit it" in lines_with_marker[0]
          and "fixed in v2" in lines_with_marker[0], lines_with_marker)
    e1 = by_id(deferred_findings(d), "F1")
    check("[CP3-X22] the full, original multi-line reason is kept verbatim in deferred-findings.json",
          e1 is not None
          and e1.get("reason") == "fine for the beta because\nonly admins hit it\nand it is fixed in v2", e1)


# ------------------------------------------------------------------------------------------------ runner

TESTS = [
    ("CP3-T01", t01_ask_prints_question_naming_findings_and_exact_reply),
    ("CP3-T02", t02_cannot_ask_when_round_did_not_block),
    ("CP3-T03", t03_unknown_finding_id_refused),
    ("CP3-T04", t04_accepting_same_finding_twice_refused_second_time),
    ("CP3-T05", t05_deferred_minor_not_reachable_via_accept_finding),
    ("CP3-T06", t06_nothing_accepted_before_reply),
    ("CP3-T07", t07_reply_in_different_chat_does_not_accept),
    ("CP3-T08", t08_owners_first_reply_accepts_with_reason),
    ("CP3-T09", t09_accepting_all_passes_without_new_round),
    ("CP3-T10", t10_accepting_only_some_leaves_blocked),
    ("CP3-T11", t11_code_change_after_acceptance_resets_rules),
    ("CP3-T12", t12_status_and_summary_list_accepted_findings_severity_order),
    ("CP3-T13", t13_round_limit_offers_accept_finding_as_lettered_option),
    ("CP3-T14", t14_docs_describe_accept_finding_flow),
    ("CP3-X01", x01_ambiguous_bare_id_refused_qualified_accept_removes_only_that_one),
    ("CP3-X02", x02_write_failure_leaves_no_partial_accept_and_retry_completes_once),
    ("CP3-X03", x03_director_mode_refuses_chat_accept_needs_director_token),
    ("CP3-X04", x04_duplicate_ids_deduped_to_one_acceptance),
    ("CP3-X05", x05_interrupted_write_after_state_commit_refuses_duplicate_on_retry),
    ("CP3-X06", x06_other_still_open_finding_survives_the_interruption_and_gate_stays_blocked),
    ("CP3-X07", x07_accepting_at_round_limit_unblocks_and_complete_works),
    ("CP3-X08", x08_deferred_lists_accepted_findings_in_their_own_section),
    ("CP3-X09", x09_crash_before_the_state_commit_commits_nothing_and_a_retry_completes_once),
    ("CP3-X10", x10_round_record_found_by_number_after_a_replaced_round),
    ("CP3-X11", x11_same_reviewer_id_accepted_in_two_rounds_both_reach_learnings_with_their_own_reasons),
    ("CP3-X12", x12_accepting_at_the_run_wide_cap_resets_it_so_a_fresh_checkpoint_can_still_open_a_round),
    ("CP3-X13", x13_stored_block_choice_is_consumed_a_later_block_needs_a_new_answer),
    ("CP3-X14", x14_accept_refused_after_a_code_change_since_the_round),
    ("CP3-X15", x15_status_and_deferred_leave_every_file_byte_identical),
    ("CP3-X16", x16_duplicate_ids_from_one_reviewer_get_their_own_open_ids),
    ("CP3-X17", x17_director_token_without_reason_is_refused_and_not_consumed),
    ("CP3-X18", x18_lettered_block_options_never_offer_accepting_a_finding),
    ("CP3-X19", x19_picking_a_letter_then_human_approve_never_accepts_a_finding),
    ("CP3-X20", x20_plain_accept_line_shown_when_open_findings_exist_for_the_current_code),
    ("CP3-X21", x21_fresh_look_line_shown_once_the_code_changed_since_the_round),
    ("CP3-X22", x22_multi_line_reason_becomes_one_learnings_line),
]


# ------------------------------------------------------------------------------------------------ CP3-X23
# (adv-round-06 fix): a blank id's synthetic default can never collide with an explicit id the same reviewer
# wrote, even when that explicit id is itself a "-dupN" the synthetic numbering would otherwise land on

def x23_blank_id_synthetic_default_never_collides_with_an_explicit_id():
    env = th.make_env("claude")
    d = th.cp3_project(env)
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    explicit_f = {"id": "F", "severity": "blocker", "file": "src/f.js", "description": "explicit F: null pointer",
                  "user_impact": "a shopper's checkout crashes and they lose their cart"}
    blank_id = {"severity": "major", "file": "src/f.js", "description": "blank id: logs the card number",
                "user_impact": "a shopper's card number is written to a log file other staff can read"}
    path = write_round_review(d, env, rnd, rid, [explicit_f, blank_id])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X23] (setup) the round blocks on the explicit 'F' and the blank-id finding", r.returncode == 2,
          r.stdout + r.stderr)
    ids = sorted(f.get("id") for f in open_findings(d))
    check("[CP3-X23] the blank id's synthetic default does not collide with the explicit 'F'",
          len(ids) == 2 and len(set(ids)) == 2 and "F" in ids, ids)
    synthetic_id = [i for i in ids if i != "F"][0]
    tp = Path(d) / "t.jsonl"
    th.rx(d, env, "ask", "accept-finding", "CP1", "F")
    reply(d, tp, env, "accept: the explicit F finding only")
    r_acc = th.rx(d, env, "accept-finding", "CP1")
    check("[CP3-X23] accepting the explicit bare id 'F' succeeds", r_acc.returncode == 0,
          r_acc.stdout + r_acc.stderr)
    remaining = sorted(f.get("id") for f in open_findings(d))
    check("[CP3-X23] only the blank-id finding (its own synthetic id) is still open: the right one was accepted",
          remaining == [synthetic_id], remaining)
    accepted_descs = [e.get("description") for e in deferred_findings(d) if e.get("accepted")]
    check("[CP3-X23] the accepted entry is really the explicit F finding, not the blank-id one",
          accepted_descs == ["explicit F: null pointer"], accepted_descs)


def x23b_two_blank_ids_next_to_explicit_f_and_f_dup2_all_get_distinct_ids():
    spec = importlib.util.spec_from_file_location("ratchet_cp3x23b", th.ENGINE)
    _eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_eng)
    findings = [
        {"id": "F", "description": "explicit F"},
        {"description": "first blank id"},
        {"description": "second blank id"},
        {"id": "F-dup2", "description": "explicit F-dup2"},
    ]
    out = _eng._dedupe_finding_ids(findings)
    ids = [f.get("id") for f in out]
    check("[CP3-X23b] all four ids come out distinct even though two were blank and two explicit "
          "ids already sit in the synthetic numbering's path", len(ids) == len(set(ids)), ids)
    check("[CP3-X23b] the explicit ids are left exactly as written",
          out[0]["id"] == "F" and out[3]["id"] == "F-dup2", ids)


# ------------------------------------------------------------------------------------------------ CP3-X24
# (adv-round-06 fix): _blocked_accept_note never claims "the last fix changed the code" when there is nothing
# open to accept in the first place (e.g. an approved round, then an unrelated code change, then a run-wide cap
# block raised by a LATER checkpoint)

def x24_no_fresh_look_line_when_the_blocked_rounds_own_findings_are_not_open():
    d, env, tp = run_wide_cap_project("x24")
    th.cp3_pass_behavior(d, env, "CP1")
    rnd = th.cp3_open_round(d, env, "CP1")
    rid = th.cp3_R0(rnd)
    path = write_round_review(d, env, rnd, rid, [])  # approves: nothing open for CP1's round
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP3-X24] (setup) CP1's round approves with no findings", r.returncode == 0, r.stdout + r.stderr)
    (Path(d) / "src/f.js").write_text("export const f = () => 2; // changed after CP1 approved\n")
    r2 = th.rx(d, env, "reviewers", "CP1", "--json")
    check("[CP3-X24] (setup) the run-wide cap blocks the run", r2.returncode == 2, r2.stdout + r2.stderr)
    st = th.cp3_state(d)
    check("[CP3-X24] (setup) the run is blocked at the run-wide cap", st.get("phase") == "blocked", st.get("phase"))
    nj = json.loads(th.rx(d, env, "next").stdout)
    note = nj.get("accept_note")
    check("[CP3-X24] next prints no accept_note: CP1's recorded round has no open findings to accept or refresh",
          note is None, note)
    out = th.rx(d, env, "status").stdout
    check("[CP3-X24] status prints no 'fresh look' or 'accept open findings' line either",
          "fresh look" not in out.lower() and "accept open findings" not in out.lower(), out)


TESTS.extend([
    ("CP3-X23", x23_blank_id_synthetic_default_never_collides_with_an_explicit_id),
    ("CP3-X23b", x23b_two_blank_ids_next_to_explicit_f_and_f_dup2_all_get_distinct_ids),
    ("CP3-X24", x24_no_fresh_look_line_when_the_blocked_rounds_own_findings_are_not_open),
])


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
