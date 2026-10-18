#!/usr/bin/env python3
"""
CP5 tests: a director signs and applies a routine approval in one command.   python3 tests/test_director_apply.py

`director-sign <action> [target] [--ids F1,F2] [--reason R] --apply` runs, inside one hold of the lane's state lock,
the question step the action needs (`ask ...`), the signature and the apply, for approve-changes, accept-finding
(target = the step), raise-limit (target = the step) and choice (target = the letter). It works from every phase the
action applies to (including a lane that paused itself for the director), refuses a builder session, a lane that is
not director-enrolled, the wrong phase and a bad request without changing anything, never applies twice, never
resets the run-wide review budget, and does nothing for the human-only actions (approve-plan, cancel, hooks-waive,
set-review, human-approve). In an enrolled lane next, status and refusals name the director's command instead of
telling the agent to ask the user. The old separate steps (ask, sign, apply with the token) still work.

Fixtures reuse tests/test_director_sign_more.py (director(), enroll(), enrolled_cap_lane(), state helpers; lanes on
tests/test_gates.py's throwaway git projects under one private RATCHET_HOME) and tests/test_accept_finding.py
(round_limit_project, write_round_review, F1, F2; lanes on tests/trust_helpers.py's per-project RATCHET_HOME). The
director runs from its own folder with RATCHET_ROLE=director and reaches a lane with --root <lane>; the lane itself
runs without the role. Every lane is enrolled only after its builder-side setup. No real reviewer CLI is called.

Every case is written so a missing or failing command is reported through check() as a clean fail, never an uncaught
crash.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                              # noqa: E402,F401
import test_gates as g                   # noqa: E402
import test_limits as tl                 # noqa: E402
import test_accept_finding as taf        # noqa: E402
import trust_helpers as th               # noqa: E402
import test_director_sign_more as m      # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-[TX]\d+[a-z]?)\]")

REASON = "edge case worth one more look"
ACCEPT_REASON = "known limit, tracked for later"
TOKEN_LINE = re.compile(r"^[0-9a-f]{64}$")
# what an enrolled lane's agent must never be told to do
ASK_USER = re.compile(r"ask the user|ask the developer|wait for the user|wait for their reply|waiting (on|for) the user|"
                      r"show (it |them |the changes |the question )?to the user|show the user|"
                      r"ask approve-plan|ask human-approve|ask approve-changes", re.I)


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    mm = ID_RE.search(name)
    if mm:
        cid = mm.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ helpers

out = m.out
state = m.state
adv = m.adv
events = m.events
pending = m.pending
learnings = m.learnings


def rx(d, env, *args):
    """A lane command (no director role): env=None means test_gates's shared env."""
    return g.rx(d, *args) if env is None else th.rx(d, env, *args)


def raw(d):
    return (Path(d) / ".ratchet/runs/f/state.json").read_bytes()


def ds(df, d, *args, env=None):
    """`director-sign <args> --apply` from the director's session."""
    return m.director(df, d, "director-sign", *args, "--apply", env=env)


def weaken(d):
    """Edit the planned test file after the red run, so integrity_problems lists it."""
    tf = Path(d) / "tests/cp1.test.js"
    tf.write_text(tf.read_text().replace("a.equal(f(), 1)", "a.ok(true)"))


def make_impl(d):
    (Path(d) / "src/f.js").write_text("export const f = () => 1;\n")


def to_red(d):
    g.write_tests(d)
    assert g.rx(d, "tests-written", "CP1").returncode == 0
    assert g.rx(d, "red", "CP1").returncode == 0


def changed_lane(name, enrolled=True, df=None):
    """A lane building CP1 past its red run, with the planned test file edited afterwards."""
    d = g.project(name)
    g.approve_plan(d)
    to_red(d)
    weaken(d)
    if enrolled:
        df = m.enroll(d, df)
    return d, df


def accept_lane(name):
    """Enrolled lane blocked at CP1's review limit (max_adversarial_rounds=1) with F1 (blocker) and F2 (major) open."""
    d, env, tp = taf.round_limit_project(name)
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = taf.write_round_review(d, env, rnd, rid, [taf.F1, taf.F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    assert r.returncode == 2 and state(d).get("phase") == "blocked", out(r)
    df = m.enroll(d, None, env=env)
    return d, env, df


def choice_lane(name):
    """A lettered question A/B pending (A approves changing tests/cp1.test.js), asked before enrolling."""
    d = g.project(name)
    g.approve_plan(d)
    to_red(d)
    r = g.rx(d, "ask", "choice", "--options", "A,B", "--locked", "A=tests/cp1.test.js")
    assert r.returncode == 0, out(r)
    df = m.enroll(d)
    return d, df


def plan_lane(name):
    d = g.project(name)
    df = m.enroll(d)
    assert state(d).get("phase") == "awaiting_plan_approval", state(d).get("phase")
    return d, df


def building_lane(name):
    d = g.project(name)
    g.approve_plan(d)
    df = m.enroll(d)
    return d, df


def review_lane(name):
    """Enrolled lane in awaiting_checkpoint_review (human_review=each), returns (d, env, df)."""
    env = th.make_env("claude")
    d, tp = th.cp5_awaiting_checkpoint_review(env)
    df = m.enroll(d, None, env=env)
    return d, env, df


def one_line(r):
    return (r.stderr or r.stdout or "").strip()


def last_line(r):
    lines = r.stdout.strip().splitlines()
    return lines[-1] if lines else ""


# ------------------------------------------------------------------------------------------------ T01

def cp5_t01_director_approves_changed_tests_in_one_command():
    d, df = changed_lane("t01")
    pre = g.rx(d, "implemented", "CP1")
    check("[CP5-T01] setup: the build refuses while the test file is changed",
          pre.returncode != 0 and re.search(r"changed after|approve-changes", out(pre)) is not None, out(pre))
    before = state(d)
    r = ds(df, d, "approve-changes")
    check("[CP5-T01] director-sign approve-changes --apply exits 0", r.returncode == 0, out(r))
    after = state(d)
    check("[CP5-T01] the approved baseline moved to the edited file", after.get("approved") != before.get("approved"),
          "approved unchanged")
    ev = events(d, "changes_approved")
    check("[CP5-T01] exactly one changes_approved event, by director", len(ev) == 1 and ev[0].get("by") == "director", ev)
    check("[CP5-T01] no question is left pending", not pending(d), pending(d))
    nothing = g.rx(d, "approve-changes")
    check("[CP5-T01] integrity is clean afterwards (nothing has changed since approval)",
          nothing.returncode == 0 and re.search(r"nothing[^.\n]*changed", out(nothing), re.I) is not None, out(nothing))
    make_impl(d)
    nxt = g.rx(d, "implemented", "CP1")
    check("[CP5-T01] the lane's next step command is no longer refused for the changed file", nxt.returncode == 0, out(nxt))


# ------------------------------------------------------------------------------------------------ T02

def cp5_t02_director_accepts_findings_in_one_command():
    d, env, df = accept_lane("t02")
    fp = (adv(d).get("fingerprint") or {}).get("id")
    r = ds(df, d, "accept-finding", "CP1", "--ids", "F1,F2", "--reason", ACCEPT_REASON, env=env)
    check("[CP5-T02] director-sign accept-finding CP1 --ids F1,F2 --reason --apply exits 0", r.returncode == 0, out(r))
    deferred = taf.deferred_findings(d, 1)
    for fid in ("F1", "F2"):
        e = taf.by_id(deferred, fid)
        check(f"[CP5-T02] {fid} is recorded accepted by the director with the director's reason",
              bool(e) and e.get("accepted") is True and e.get("accepted_by") == "director"
              and e.get("reason") == ACCEPT_REASON, e)
    lines = [ln for ln in learnings(d).splitlines() if ACCEPT_REASON in ln]
    check("[CP5-T02] learnings.md gets one line per finding (two lines)", len(lines) == 2, lines)
    check("[CP5-T02] one of those lines names F1 and the other F2",
          any("F1" in ln for ln in lines) and any("F2" in ln for ln in lines), lines)
    gate = adv(d)
    check("[CP5-T02] the adversarial gate passed for that round without another round",
          gate.get("status") == "passed" and len(gate.get("rounds") or []) == 1, gate)
    check("[CP5-T02] no question is left pending", not pending(d), pending(d))
    check("[CP5-T02] the run is no longer blocked", state(d).get("phase") != "blocked", state(d).get("phase"))
    _ = fp


# ------------------------------------------------------------------------------------------------ T03

def cp5_t03_director_raises_limit_in_one_command():
    d, df = m.enrolled_cap_lane("t03")
    r = ds(df, d, "raise-limit", "CP1", "--reason", REASON)
    check("[CP5-T03] director-sign raise-limit CP1 --reason --apply exits 0", r.returncode == 0, out(r))
    a = adv(d)
    check("[CP5-T03] cap_raised_to is the configured cap plus exactly one (3)", a.get("cap_raised_to") == 3, a)
    want = f"CP1's review-round limit was raised to 3 rounds: {REASON}"
    check("[CP5-T03] the reason is saved to learnings.md once", learnings(d).count(want) == 1, learnings(d)[-300:])
    check("[CP5-T03] the run is no longer blocked", state(d).get("phase") != "blocked", state(d).get("phase"))
    check("[CP5-T03] the earlier attempt count is kept (2), not reset", a.get("attempts") == 2, a)
    r2 = tl._reviewers_after_triage(d, "CP1")
    check("[CP5-T03] `reviewers CP1` opens exactly one more round (round 3)",
          r2.returncode == 0 and json.loads(r2.stdout).get("round") == 3, out(r2))
    check("[CP5-T03] no question is left pending", not pending(d), pending(d))


# ------------------------------------------------------------------------------------------------ T04

def cp5_t04_director_picks_a_letter_in_one_command():
    d, df = choice_lane("t04")
    r = ds(df, d, "choice", "A")
    check("[CP5-T04] director-sign choice A --apply exits 0", r.returncode == 0, out(r))
    ev = events(d, "choice_made")
    check("[CP5-T04] history has choice_made for letter A naming the file it unlocks",
          len(ev) == 1 and ev[0].get("letter") == "A" and ev[0].get("approves") == ["tests/cp1.test.js"], ev)
    allow = state(d).get("choice_allow") or {}
    check("[CP5-T04] choice_allow lists letter A and the file, for CP1",
          allow.get("letter") == "A" and allow.get("files") == ["tests/cp1.test.js"] and allow.get("cp") == "CP1", allow)
    check("[CP5-T04] the pending question is cleared", not pending(d), pending(d))
    weaken(d)
    ra = g.rx(d, "approve-changes")
    check("[CP5-T04] approve-changes for that file then works under the pick with no second signature",
          ra.returncode == 0 and "Accepted" in ra.stdout, out(ra))


# ------------------------------------------------------------------------------------------------ T05

def cp5_t05_paused_lane_carries_on():
    d, df = changed_lane("t05", enrolled=False)
    rp = g.rx(d, "pause", "--reason", "waiting for the director to approve test changes")
    df = m.enroll(d)
    check("[CP5-T05] setup: the lane paused itself from building", rp.returncode == 0
          and state(d).get("phase") == "paused" and state(d).get("paused_from") == "building", out(rp))
    r = ds(df, d, "approve-changes")
    check("[CP5-T05] the apply exits 0 even though the lane is paused", r.returncode == 0, out(r))
    check("[CP5-T05] one changes_approved event by director",
          [e.get("by") for e in events(d, "changes_approved")] == ["director"], events(d, "changes_approved"))
    rr = g.rx(d, "resume")
    check("[CP5-T05] resume works", rr.returncode == 0, out(rr))
    st = state(d)
    check("[CP5-T05] the phase is building on CP1 again", st.get("phase") == "building" and st.get("current") == "CP1",
          (st.get("phase"), st.get("current")))
    nx = g.rx(d, "next")
    check("[CP5-T05] next gives the normal build action (not a pause or a refusal)",
          nx.returncode == 0 and not re.search(r"paused|pause|director-sign|refus", nx.stdout, re.I), out(nx))
    make_impl(d)
    step = g.rx(d, "implemented", "CP1")
    check("[CP5-T05] that step's command runs without the changed-files refusal", step.returncode == 0, out(step))


# ------------------------------------------------------------------------------------------------ T06

def cp5_t06_approve_changes_from_every_phase():
    # blocked (at CP1's review limit), with the planned test file changed
    d, df = m.enrolled_cap_lane("t06a")
    weaken(d)
    r = ds(df, d, "approve-changes")
    ev = events(d, "changes_approved")
    check("[CP5-T06] blocked: apply exits 0", r.returncode == 0, out(r))
    check("[CP5-T06] blocked: one changes_approved by director and the phase is still blocked",
          len(ev) == 1 and ev[0].get("by") == "director" and state(d).get("phase") == "blocked",
          (ev, state(d).get("phase")))
    check("[CP5-T06] blocked: no question is left pending", not pending(d), pending(d))
    # awaiting_checkpoint_review, with the planned test file changed
    d2, env2, df2 = review_lane("t06b")
    weaken(d2)
    r2 = ds(df2, d2, "approve-changes", env=env2)
    ev2 = events(d2, "changes_approved")
    check("[CP5-T06] awaiting_checkpoint_review: apply exits 0", r2.returncode == 0, out(r2))
    check("[CP5-T06] awaiting_checkpoint_review: one changes_approved by director, phase unchanged",
          len(ev2) == 1 and ev2[0].get("by") == "director" and state(d2).get("phase") == "awaiting_checkpoint_review",
          (ev2, state(d2).get("phase")))
    check("[CP5-T06] awaiting_checkpoint_review: no question is left pending", not pending(d2), pending(d2))


# ------------------------------------------------------------------------------------------------ T07

def _next_texts(d, env=None):
    n = rx(d, env, "next")
    s = rx(d, env, "status")
    sj = rx(d, env, "status", "--json")
    try:
        nj = json.dumps(json.loads(sj.stdout).get("next"))
    except Exception:
        nj = sj.stdout
    return {"next": out(n), "status": out(s), "status --json next": nj}


def _names_director(label, texts):
    for what, txt in texts.items():
        check(f"[CP5-T07] {label}: {what} names the director and a director-sign command with --apply or a token",
              re.search(r"director", txt, re.I) is not None and "director-sign" in txt
              and re.search(r"--apply|--director-token", txt) is not None, txt[:300])
        bad = ASK_USER.search(txt)
        check(f"[CP5-T07] {label}: {what} never tells the agent to ask the user or wait for their reply",
              bad is None, bad.group(0) if bad else "")


def cp5_t07_enrolled_next_and_status_name_the_director():
    d, df = plan_lane("t07a")
    _names_director("awaiting_plan_approval", _next_texts(d))
    d2, env2, df2 = review_lane("t07b")
    _names_director("awaiting_checkpoint_review", _next_texts(d2, env2))
    d3, df3 = m.enrolled_cap_lane("t07c")
    _names_director("blocked at a review limit", _next_texts(d3))
    d4 = g.project("t07d")  # an ordinary lane: unchanged, still says to ask the user
    texts = _next_texts(d4)
    check("[CP5-T07] a non-enrolled lane's next still tells the agent to ask the user",
          re.search(r"ask|user", texts["next"], re.I) is not None and "director-sign" not in texts["next"],
          texts["next"][:300])
    check("[CP5-T07] a non-enrolled lane's status --json next names no director-sign command",
          "director-sign" not in texts["status --json next"], texts["status --json next"][:300])


# ------------------------------------------------------------------------------------------------ T08

def _refusal(label, d, r, before, action):
    o = out(r)
    check(f"[CP5-T08] {label}: refused (non-zero)", r.returncode != 0, o)
    check(f"[CP5-T08] {label}: nothing changed", raw(d) == before, "state.json changed")
    check(f"[CP5-T08] {label}: names `director-sign {action}` with --apply",
          f"director-sign {action}" in o and "--apply" in o, o[:300])
    bad = ASK_USER.search(o)
    check(f"[CP5-T08] {label}: does not say to show the user or wait for their reply", bad is None,
          bad.group(0) if bad else "")


def cp5_t08_refusals_point_at_the_directors_command():
    d, df = changed_lane("t08a")
    before = raw(d)
    _refusal("a gated step with a changed test file", d, g.rx(d, "implemented", "CP1"), before, "approve-changes")
    _refusal("approve-changes without a token", d, g.rx(d, "approve-changes"), before, "approve-changes")
    d2, df2 = m.enrolled_cap_lane("t08b")
    before2 = raw(d2)
    _refusal("accept-finding CP1 without a token or question", d2, g.rx(d2, "accept-finding", "CP1"), before2,
             "accept-finding")


# ------------------------------------------------------------------------------------------------ T09

def _section9(text):
    mm = re.search(r"^## 9\..*?(?=^## 10\.)", text, re.S | re.M)
    return mm.group(0) if mm else ""


def cp5_t09_docs_state_one_order():
    docs = {"README.md": (ROOT / "README.md").read_text(),
            "SKILL.md": (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text(),
            "schemas.md section 9": _section9((ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text())}
    for name, text in docs.items():
        check(f"[CP5-T09] {name}: has a director section", "director-sign" in text, "")
        check(f"[CP5-T09] {name}: shows the one-step form `director-sign <action> ... --apply`",
              re.search(r"director-sign <?[a-z-]+>?[^\n]{0,80}--apply", text) is not None, "")
        for act in ("approve-changes", "accept-finding", "raise-limit", "choice"):
            check(f"[CP5-T09] {name}: lists {act} as an --apply action",
                  re.search(rf"director-sign {act}[^\n]{{0,120}}--apply", text) is not None
                  or re.search(rf"--apply[^\n]{{0,300}}{act}", text) is not None, "")
        check(f"[CP5-T09] {name}: states the order (ask, sign, apply)",
              re.search(r"ask[^\n]{0,200}sign[^\n]{0,200}apply", text, re.I) is not None, "")
        check(f"[CP5-T09] {name}: says the old separate steps still work",
              re.search(r"(old|separate|three)[^\n]{0,160}still work|still work[^\n]{0,160}(separate|old|three)",
                        text, re.I) is not None, "")


# ------------------------------------------------------------------------------------------------ T10

def cp5_t10_old_separate_steps_still_work():
    d, df = changed_lane("t10")
    q = g.rx(d, "ask", "approve-changes")
    check("[CP5-T10] setup: ask approve-changes runs in the enrolled lane", q.returncode == 0, out(q))
    before = raw(d)
    sg = m.director(df, d, "director-sign", "approve-changes")
    tok = last_line(sg)
    check("[CP5-T10] director-sign approve-changes (no --apply) exits 0 and its last stdout line is the bare token",
          sg.returncode == 0 and TOKEN_LINE.match(tok) is not None, out(sg))
    check("[CP5-T10] sign alone changed no state", raw(d) == before, "state.json changed")
    r = g.rx(d, "approve-changes", "--director-token", tok or m.MADE_UP_TOKEN)
    ev = events(d, "changes_approved")
    check("[CP5-T10] the old three-step flow approves the change", r.returncode == 0, out(r))
    check("[CP5-T10] it records one changes_approved by director", len(ev) == 1 and ev[0].get("by") == "director", ev)
    # approve-plan token flow, unaffected
    dp, dfp = plan_lane("t10p")
    sp = m.director(dfp, dp, "director-sign", "approve-plan")
    tp = last_line(sp)
    check("[CP5-T10] director-sign approve-plan still prints only the token last",
          sp.returncode == 0 and TOKEN_LINE.match(tp) is not None, out(sp))
    rp = g.rx(dp, "approve-plan", "--director-token", tp or m.MADE_UP_TOKEN)
    check("[CP5-T10] approve-plan --director-token still approves the plan",
          rp.returncode == 0 and state(dp).get("phase") == "building", out(rp))
    # hooks-waive token flow, unaffected
    dh, dfh = building_lane("t10h")
    hb = raw(dh)
    sh = m.director(dfh, dh, "director-sign", "hooks-waive")
    check("[CP5-T10] director-sign hooks-waive still prints only the token last and changes no state",
          sh.returncode == 0 and TOKEN_LINE.match(last_line(sh)) is not None and raw(dh) == hb, out(sh))


# ------------------------------------------------------------------------------------------------ T11

def cp5_t11_builder_cannot_sign_and_apply():
    d, df = changed_lane("t11")
    before = raw(d)
    sa = m.not_director(df, d, "director-sign", "approve-changes", "--apply")
    check("[CP5-T11] a builder session's director-sign approve-changes --apply is refused with no token printed",
          sa.returncode != 0 and not TOKEN_LINE.search(sa.stdout), out(sa))
    check("[CP5-T11] the refusal says director-only (a real refusal, not a usage error)",
          "usage:" not in out(sa) and re.search(r"director", out(sa), re.I) is not None, out(sa))
    benv = {k: v for k, v in g.ENV.items() if k != "RATCHET_ROLE"}
    cmd = f"python3 {g.ENGINE} --root {d} director-sign approve-changes --apply"
    gr = th.cp4x_guard_tool(df, benv, "Bash", {"command": cmd}, session="s-builder")
    check("[CP5-T11] the guard hook blocks the same command", gr.returncode == 2, out(gr))
    check("[CP5-T11] the file stays unapproved, nothing is asked or pending, no history event was added",
          raw(d) == before and not pending(d) and not events(d, "changes_approved"), pending(d))
    nothing = g.rx(d, "implemented", "CP1")
    check("[CP5-T11] the changed file still blocks the build", nothing.returncode != 0, out(nothing))


# ------------------------------------------------------------------------------------------------ T12

def cp5_t12_apply_refuses_a_non_enrolled_lane():
    d, _ = changed_lane("t12", enrolled=False)
    df = m.director_folder()
    rk = m.director(df, d, "director-key")
    check("[CP5-T12] setup: the director has a key", rk.returncode == 0, out(rk))
    before = raw(d)
    r = ds(df, d, "approve-changes")
    check("[CP5-T12] the apply is refused", r.returncode != 0 and "usage:" not in out(r), out(r))
    check("[CP5-T12] the message says this lane takes the developer's approval",
          "usage:" not in out(r) and re.search(r"not (director[- ])?enrolled|developer|human", out(r), re.I) is not None, out(r))
    check("[CP5-T12] the file stays unapproved, no question is pending, state unchanged",
          raw(d) == before and not pending(d), pending(d))
    q = g.rx(d, "ask", "approve-changes")
    g.say(d, "yes")
    ra = g.rx(d, "approve-changes")
    check("[CP5-T12] the normal human ask + reply flow still works afterwards",
          q.returncode == 0 and ra.returncode == 0 and [e.get("by") for e in events(d, "changes_approved")] == ["human"],
          out(q) + out(ra))


# ------------------------------------------------------------------------------------------------ T13

def cp5_t13_apply_refuses_what_does_not_apply_now():
    lanes = []
    dp, dfp = plan_lane("t13a")
    lanes.append(("awaiting_plan_approval", dp, dfp, None))
    db, dfb = building_lane("t13b")
    lanes.append(("building and not blocked", db, dfb, None))
    # a done lane: one checkpoint through its reviews, then the director-signed final approval
    env = th.make_env("claude")
    dd = th.cp4_awaiting_review(env)
    dfd = m.enroll(dd, None, env=env)
    tok = last_line(m.director(dfd, dd, "director-sign", "human-approve", env=env))
    rh = th.rx(dd, env, "human-approve", "--director-token", tok or m.MADE_UP_TOKEN)
    check("[CP5-T13] setup: the third lane is done", rh.returncode == 0 and state(dd).get("phase") == "done",
          out(rh) + str(state(dd).get("phase")))
    lanes.append(("done", dd, dfd, env))
    reqs = [("accept-finding", ["accept-finding", "CP1", "--ids", "F1", "--reason", "r"]),
            ("raise-limit", ["raise-limit", "CP1", "--reason", "r"]),
            ("choice", ["choice", "A"])]
    for label, d, df, env_ in lanes:
        for name, args in reqs:
            before = raw(d)
            r = ds(df, d, *args, env=env_)
            o = out(r)
            check(f"[CP5-T13] {label}: {name} --apply is refused by the engine, not the argument parser",
                  r.returncode != 0 and "usage:" not in o, o)
            check(f"[CP5-T13] {label}: {name} says why in plain words (phase or missing condition)",
                  re.search(r"phase|not blocked|no open|no lettered|not waiting|plan|done|nothing to|isn't|is not",
                            o, re.I) is not None and "Traceback" not in o and "usage:" not in o, o[:300])
            check(f"[CP5-T13] {label}: {name} left state.json byte-identical", raw(d) == before and not pending(d),
                  "state.json changed")
    before = raw(dp)
    r = ds(dfp, dp, "approve-changes")
    check("[CP5-T13] awaiting_plan_approval: approve-changes --apply is refused naming the plan not yet approved",
          r.returncode != 0 and "usage:" not in out(r) and re.search(r"plan|phase|awaiting", out(r), re.I) is not None,
          out(r))
    check("[CP5-T13] awaiting_plan_approval: approve-changes left state.json byte-identical", raw(dp) == before, "")


# ------------------------------------------------------------------------------------------------ T14

def cp5_t14_bad_request_leaves_the_lane_as_it_was():
    d, env, df = accept_lane("t14a")
    b = raw(d)
    r1 = ds(df, d, "accept-finding", "CP1", "--ids", "F1,F2", env=env)
    check("[CP5-T14] accept-finding with no --reason is refused naming --reason",
          r1.returncode != 0 and "--reason" in out(r1), out(r1))
    check("[CP5-T14] accept-finding with no --reason left state.json byte-identical", raw(d) == b, "")
    r2 = ds(df, d, "accept-finding", "CP1", "--ids", "F9", "--reason", ACCEPT_REASON, env=env)
    check("[CP5-T14] accept-finding for F9 (not open) is refused naming F9",
          r2.returncode != 0 and "F9" in out(r2), out(r2))
    check("[CP5-T14] accept-finding for F9 left state.json byte-identical", raw(d) == b, "")
    r3 = ds(df, d, "accept-finding", "CP1", "--ids", "F1,F2", "--reason", ACCEPT_REASON, env=env)
    check("[CP5-T14] the corrected accept-finding succeeds on the first try", r3.returncode == 0, out(r3))
    d2, df2 = m.enrolled_cap_lane("t14b")
    b2 = raw(d2)
    r4 = ds(df2, d2, "raise-limit", "CP1")
    check("[CP5-T14] raise-limit with no --reason is refused naming --reason",
          r4.returncode != 0 and "--reason" in out(r4), out(r4))
    check("[CP5-T14] raise-limit with no --reason left state.json byte-identical", raw(d2) == b2, "")
    r5 = ds(df2, d2, "raise-limit", "CP1", "--reason", REASON)
    check("[CP5-T14] the corrected raise-limit succeeds on the first try",
          r5.returncode == 0 and adv(d2).get("cap_raised_to") == 3, out(r5))
    d3 = g.project("t14c")
    g.approve_plan(d3)
    g.rx(d3, "ask", "choice", "--options", "A,B")
    df3 = m.enroll(d3)
    b3 = raw(d3)
    r6 = ds(df3, d3, "choice", "C")
    check("[CP5-T14] choice C (not an option) is refused naming the offered letters",
          r6.returncode != 0 and re.search(r"A, B|A,B|offered", out(r6)) is not None, out(r6))
    check("[CP5-T14] choice C left state.json byte-identical", raw(d3) == b3, "")
    r7 = ds(df3, d3, "choice", "B")
    check("[CP5-T14] the corrected choice B succeeds on the first try",
          r7.returncode == 0 and [e.get("letter") for e in events(d3, "choice_made")] == ["B"], out(r7))


# ------------------------------------------------------------------------------------------------ T15

def cp5_t15_applying_twice_never_double_applies():
    d, env, df = accept_lane("t15a")
    args = ["accept-finding", "CP1", "--ids", "F1,F2", "--reason", ACCEPT_REASON]
    first = ds(df, d, *args, env=env)
    ev_n, l_n, h_n = len(state(d)["history"]), learnings(d), len(taf.deferred_findings(d, 1))
    again = ds(df, d, *args, env=env)
    check("[CP5-T15] setup: the first accept-finding --apply worked", first.returncode == 0, out(first))
    check("[CP5-T15] accept-finding again says the findings are already accepted",
          re.search(r"already", out(again), re.I) is not None, out(again))
    check("[CP5-T15] accept-finding again adds no history event, learnings line or deferred entry",
          len(state(d)["history"]) == ev_n and learnings(d) == l_n and len(taf.deferred_findings(d, 1)) == h_n,
          (ev_n, len(state(d)["history"])))
    d2, df2 = m.enrolled_cap_lane("t15b")
    first2 = ds(df2, d2, "raise-limit", "CP1", "--reason", REASON)
    n2, l2 = len(state(d2)["history"]), learnings(d2)
    again2 = ds(df2, d2, "raise-limit", "CP1", "--reason", REASON)
    check("[CP5-T15] setup: the first raise-limit --apply worked", first2.returncode == 0, out(first2))
    check("[CP5-T15] raise-limit again says the one raise is spent",
          re.search(r"already|spent|second time|once", out(again2), re.I) is not None, out(again2))
    check("[CP5-T15] raise-limit again adds no history event or learnings line, and the cap stays at 3",
          len(state(d2)["history"]) == n2 and learnings(d2) == l2 and adv(d2).get("cap_raised_to") == 3
          and len(events(d2, "round_limit_raised")) == 1, adv(d2))
    d3, df3 = changed_lane("t15c")
    first3 = ds(df3, d3, "approve-changes")
    n3 = len(state(d3)["history"])
    again3 = ds(df3, d3, "approve-changes")
    check("[CP5-T15] setup: the first approve-changes --apply worked", first3.returncode == 0, out(first3))
    check("[CP5-T15] approve-changes again says nothing has changed",
          re.search(r"nothing[^.\n]*changed", out(again3), re.I) is not None, out(again3))
    check("[CP5-T15] approve-changes again adds no second changes_approved event",
          len(events(d3, "changes_approved")) == 1 and len(state(d3)["history"]) == n3, events(d3, "changes_approved"))


# ------------------------------------------------------------------------------------------------ T16

def cp5_t16_apply_is_only_for_routine_approvals():
    keys = ("phase", "approved", "hooks_waived", "human_review", "current")

    def snap(d):
        st = state(d)
        return {k: st.get(k) for k in keys}

    dp, dfp = plan_lane("t16a")
    db, dfb = building_lane("t16b")
    for label, d, df in (("awaiting_plan_approval", dp, dfp), ("building", db, dfb)):
        for act in (["approve-plan"], ["cancel"], ["hooks-waive"], ["set-review", "end"], ["human-approve"]):
            before = snap(d)
            r = ds(df, d, *act)
            after = snap(d)
            check(f"[CP5-T16] {label}: `{' '.join(act)} --apply` changes no phase, approval, hooks or review mode",
                  before == after, (before, after))
            check(f"[CP5-T16] {label}: `{' '.join(act)} --apply` leaves no question pending and no cancelled event",
                  not pending(d) and not events(d, "cancelled") and not events(d, "approved"),
                  (r.returncode, pending(d)))
            if label == "awaiting_plan_approval" and act[0] == "approve-plan":
                check("[CP5-T16] approve-plan --apply never approves the plan (no plan_approved event)",
                      not events(d, "plan_approved"), events(d, "plan_approved"))


# ------------------------------------------------------------------------------------------------ T17

def _director_env():
    return dict(g.ENV, RATCHET_ROLE="director")


def _spawn(df, d, *args):
    return subprocess.Popen([sys.executable, str(g.ENGINE), "--root", str(d), *args], cwd=df, env=_director_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _prime_guard(df, d, *args):
    import shlex
    cmd = f"python3 {g.ENGINE} --root {d} {shlex.join(args)}"
    th.cp4x_guard_tool(df, _director_env(), "Bash", {"command": cmd}, session="s-director")


def cp5_t17_sign_and_apply_hold_one_lock():
    d, df = m.enrolled_cap_lane("t17a")
    lock = Path(d) / ".ratchet/state.lock"
    args = ["director-sign", "raise-limit", "CP1", "--reason", REASON, "--apply"]
    _prime_guard(df, d, *args)
    # plain sign (no --apply) still works while the lock is held: it needs a pending question first
    g.rx(d, "ask", "raise-limit", "CP1")
    lock.write_text("12345 held-by-test")
    t0 = time.time()
    plain = m.director(df, d, "director-sign", "raise-limit", "CP1", "--reason", REASON)
    check("[CP5-T17] plain `director-sign raise-limit` (no --apply) still works while the lock is held",
          plain.returncode == 0 and TOKEN_LINE.match(last_line(plain)) is not None and time.time() - t0 < 30, out(plain))
    proc = _spawn(df, d, *args)
    time.sleep(1.0)
    waited = proc.poll() is None and adv(d).get("cap_raised_to") is None
    lock.unlink()
    so, se = proc.communicate(timeout=120)
    check("[CP5-T17] the apply waits for the lock while another command holds it", waited, (proc.returncode, se))
    check("[CP5-T17] ... and then succeeds with its token not stale (cap 3, no stale-token refusal)",
          proc.returncode == 0 and adv(d).get("cap_raised_to") == 3 and "stale" not in (se + so).lower(), (so, se))
    check("[CP5-T17] ... and removes its own lock", not lock.exists(), "lock file left behind")
    # two at once
    d2, df2 = m.enrolled_cap_lane("t17b")
    _prime_guard(df2, d2, *args)
    procs = [_spawn(df2, d2, *args) for _ in range(2)]
    res = [(p.communicate(timeout=180), p.returncode) for p in procs]
    codes = sorted(rc for _, rc in res)
    check("[CP5-T17] of two concurrent applies exactly one succeeds and the other refuses",
          codes[0] == 0 and codes[1] != 0, [(rc, e[1][:200]) for e, rc in res])
    check("[CP5-T17] the cap is raised once (3) and learnings.md has one line",
          adv(d2).get("cap_raised_to") == 3 and learnings(d2).count(REASON) == 1, learnings(d2)[-300:])
    check("[CP5-T17] the loser says why in plain words (no traceback)",
          all("Traceback" not in e[1] for e, _ in res), [e[1][:200] for e, _ in res])
    check("[CP5-T17] no lock file is left behind", not (Path(d2) / ".ratchet/state.lock").exists(), "")


# ------------------------------------------------------------------------------------------------ T18

def cp5_t18_raise_never_resets_the_run_wide_budget():
    d, env, tp = taf.run_wide_cap_project("t18")
    th.cp3_pass_behavior(d, env, "CP1")
    rnd = th.cp3_open_round(d, env, "CP1")
    rid = th.cp3_R0(rnd)
    path = taf.write_round_review(d, env, rnd, rid, [taf.F1, taf.F2])
    th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    th.rx(d, env, "triage", "CP1", f"{rid}/F1=fix:known tradeoff", f"{rid}/F2=fix:known tradeoff")
    r2 = th.rx(d, env, "reviewers", "CP1", "--json")
    st0 = state(d)
    check("[CP5-T18] setup: blocked on the run-wide cap, not CP1's own",
          r2.returncode == 2 and "run-wide" in out(r2).lower() and st0.get("phase") == "blocked", out(r2))
    total, att = st0.get("adv_rounds_total"), adv(d).get("attempts")
    check("[CP5-T18] setup: attempts and adv_rounds_total are recorded", isinstance(total, int) and total > 0
          and isinstance(att, int), (total, att))
    df = m.enroll(d, None, env=env)
    r = ds(df, d, "raise-limit", "CP1", "--reason", REASON, env=env)
    st1 = state(d)
    check("[CP5-T18] the run stays blocked", st1.get("phase") == "blocked", st1.get("phase"))
    check("[CP5-T18] adv_rounds_total is exactly what it was", st1.get("adv_rounds_total") == total,
          (total, st1.get("adv_rounds_total")))
    check("[CP5-T18] CP1's attempts are exactly what they were", adv(d).get("attempts") == att, adv(d))
    check("[CP5-T18] the engine ran the request (not an argument-parser usage error)", "usage:" not in out(r), out(r))
    check("[CP5-T18] the output does not claim the run is unblocked",
          re.search(r"no longer blocked|unblocked", out(r), re.I) is None, out(r))
    check("[CP5-T18] CP1's gate is not passed by it", adv(d).get("status") != "passed", adv(d).get("status"))


# ------------------------------------------------------------------------------------------------ harness

TESTS = [
    ("CP5-T01", cp5_t01_director_approves_changed_tests_in_one_command),
    ("CP5-T02", cp5_t02_director_accepts_findings_in_one_command),
    ("CP5-T03", cp5_t03_director_raises_limit_in_one_command),
    ("CP5-T04", cp5_t04_director_picks_a_letter_in_one_command),
    ("CP5-T05", cp5_t05_paused_lane_carries_on),
    ("CP5-T06", cp5_t06_approve_changes_from_every_phase),
    ("CP5-T07", cp5_t07_enrolled_next_and_status_name_the_director),
    ("CP5-T08", cp5_t08_refusals_point_at_the_directors_command),
    ("CP5-T09", cp5_t09_docs_state_one_order),
    ("CP5-T10", cp5_t10_old_separate_steps_still_work),
    ("CP5-T11", cp5_t11_builder_cannot_sign_and_apply),
    ("CP5-T12", cp5_t12_apply_refuses_a_non_enrolled_lane),
    ("CP5-T13", cp5_t13_apply_refuses_what_does_not_apply_now),
    ("CP5-T14", cp5_t14_bad_request_leaves_the_lane_as_it_was),
    ("CP5-T15", cp5_t15_applying_twice_never_double_applies),
    ("CP5-T16", cp5_t16_apply_is_only_for_routine_approvals),
    ("CP5-T17", cp5_t17_sign_and_apply_hold_one_lock),
    ("CP5-T18", cp5_t18_raise_never_resets_the_run_wide_budget),
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
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        print(f"\n{cid}: {fn.__name__}")
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
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
