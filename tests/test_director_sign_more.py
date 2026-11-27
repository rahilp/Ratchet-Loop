#!/usr/bin/env python3
"""
CP4 tests: a director signs raise-limit and lettered choices.   python3 tests/test_director_sign_more.py

In a director-enrolled lane a chat reply approves nothing, so a lane stuck at its review-round limit, or waiting
on a lettered question, needs the director: `director-sign raise-limit CPn --reason "..." --apply` raises that step's
limit by exactly one round, once, with the reason saved to learnings.md as a chat raise saves it; `ask choice` runs
in an enrolled lane and `director-sign choice <L> --apply` records that letter exactly as a developer's pick would,
including what the option unlocks. Pasteable `--director-token` on the lane CLI is refused; plain `director-sign`
without `--apply` refuses and names `--apply`. A raise for another step, another letter, another action or an
already-spent raise is refused; a builder session cannot sign or apply; a chat `raise:` reply or a typed letter
approves nothing in an enrolled lane; and the run-wide review budget is never reset.

Fixtures reuse tests/test_limits.py (_project_to_cap, _reviewers_after_triage, _drive_to_cap_safe, _raise,
BLOCKER, all on tests/test_gates.py's throwaway git projects under one private RATCHET_HOME, so every lane built
here shares one director key) and tests/test_accept_finding.py (run_wide_cap_project, write_round_review, reply,
on tests/trust_helpers.py's per-project RATCHET_HOME). The director runs from its own folder (no .ratchet/) with
RATCHET_ROLE=director, after its guard hook recorded that folder, and reaches a lane with --root <lane>, the same
way tests/test_trust_cp5_d.py's cp5r12_director does; the lane itself runs without the role. Every lane is
enrolled only after its builder-side setup (the guard hook records a builder role in the lane while reviews are
written, which is exactly why the director works from its own folder). No real reviewer CLI is ever called.

Every case that depends on a not-yet-built command is written so a missing or failing command is reported
through `check()` as a clean fail, never an uncaught crash.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text

import json
import os
import re
import shlex
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                              # noqa: E402
import test_gates as g                   # noqa: E402  (project, approve_plan, say, rx, write_review, R0, ENV, ENGINE)
import test_limits as tl                 # noqa: E402  (_project_to_cap, _reviewers_after_triage, _raise, BLOCKER)
import test_accept_finding as taf        # noqa: E402  (run_wide_cap_project, write_round_review, reply, F1, F2)
import trust_helpers as th               # noqa: E402  (rx, cp4x_guard_tool, cp3_*)

# [perf, standard #30] test_gates.rx/say/project/approve_plan already carry an opt-in in-process fast path (a
# serialized lock around one shared, memoized engine module -- the same mechanism test_quota_shared.py's
# conversion uses). Every call this suite drives through them is the lane's own command logic; the director side
# runs through trust_helpers.rx (director() / not_director()), left untouched, and tl.*/taf.* helpers keep their
# own real-subprocess calls. No case here turns on a genuine subprocess boundary for a test_gates call, so these
# thin wrappers default fast=True. fast-path replaces the per-call `python3 ratchet.py ...` subprocess; a call
# needing a real subprocess would pass fast=False.
def rx(d, *a, **k):
    k.setdefault("fast", True)
    return g.rx(d, *a, **k)


def say(d, *a, **k):
    k.setdefault("fast", True)
    return g.say(d, *a, **k)


def project(*a, **k):
    k.setdefault("fast", True)
    return g.project(*a, **k)


def approve_plan(*a, **k):
    k.setdefault("fast", True)
    return g.approve_plan(*a, **k)


PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP4-[TX]\d+[a-z]?)\]")

REASON = "edge case worth one more look"
PROTO = "reference/prototype.html"
MADE_UP_TOKEN = "0123456789abcdef" * 4
DIRECTOR_HARNESS_RE = re.compile(r"director-sign[^\n]{0,120}--apply|director-enrolled", re.I)
NEEDS_APPLY_RE = re.compile(r"needs --apply|Pasteable director tokens", re.I)


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


# ------------------------------------------------------------------------------------------------ helpers

def state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def adv(d, cp="CP1"):
    return ((state(d).get("checkpoints") or {}).get(cp) or {}).get("gates", {}).get("adversarial") or {}


def events(d, name):
    return [e for e in state(d).get("history") or [] if e.get("event") == name]


def pending(d):
    return state(d).get("pending") or {}


def learnings(d):
    p = Path(d) / ".ratchet/learnings.md"
    return p.read_text() if p.exists() else ""


def out(r):
    return (r.stdout or "") + (r.stderr or "")


def token_of(r):
    """The token a successful director-sign printed (its last stdout line), or '' when it refused."""
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ""


def director_folder(prefix="cp4-director"):
    return Path(_tmp.mkdtemp(prefix=f"ratchet-{prefix}-"))


def director(dfolder, lane, *args, env=None, session="s-director"):
    """Runs a director command the way the director's own agent CLI would: its guard hook sees the command first
    (and records the director's folder, which has no .ratchet/), then the command runs from that folder with
    --root <lane>. `env` is the lane's env plus RATCHET_ROLE=director (default: test_gates's shared env)."""
    denv = dict(env or g.ENV, RATCHET_ROLE="director")
    cmd = f"python3 {g.LAUNCHER} --root {lane} {shlex.join(str(a) for a in args)}"
    th.cp4x_guard_tool(dfolder, denv, "Bash", {"command": cmd}, session=session)
    return th.rx(dfolder, denv, "--root", str(lane), *[str(a) for a in args])


def not_director(dfolder, lane, *args, env=None):
    """The same command from a session that is NOT the director's (no RATCHET_ROLE)."""
    benv = {k: v for k, v in (env or g.ENV).items() if k != "RATCHET_ROLE"}
    return th.rx(dfolder, benv, "--root", str(lane), *[str(a) for a in args])


def enroll(d, dfolder=None, env=None):
    """Creates the director key (once per RATCHET_HOME) and enrolls lane `d`. Returns the director folder."""
    dfolder = dfolder or director_folder()
    rk = director(dfolder, d, "director-key", env=env)
    assert rk.returncode == 0, out(rk)  # existing behaviour (CP5-T09, CP3-X03)
    en = director(dfolder, d, "director-enroll", env=env)
    assert en.returncode == 0, out(en)
    assert (Path(d) / ".ratchet/director.json").exists()
    return dfolder


def enrolled_cap_lane(name, max_rounds=2, cps=1, dfolder=None):
    """An enrolled lane blocked on CP1's OWN round limit (max_adversarial_rounds=2, two blocking rounds)."""
    d, r = tl._project_to_cap(name, max_rounds=max_rounds, cps=cps)
    assert r.returncode != 0 and state(d).get("phase") == "blocked", out(r)
    dfolder = enroll(d, dfolder)
    return d, dfolder


def building_lane(name, enrolled=True, proto=True, dfolder=None):
    """A lane building CP1 (plan approved, nothing reviewed yet); with `proto`, reference/prototype.html exists
    before approval, so it is locked by the approved snapshot."""
    d = project(name)
    if proto:
        p = d / ".ratchet/runs/f" / PROTO
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<p aria-live='polite'>approved prototype</p>")
    approve_plan(d)
    if enrolled:
        dfolder = enroll(d, dfolder)
    return d, dfolder


def ask_choice(d, *extra):
    return rx(d, "ask", "choice", "--options", "A,B,C", *extra)


def sign_raise(dfolder, d, cp="CP1", reason=REASON, env=None):
    return director(dfolder, d, "director-sign", "raise-limit", cp, "--reason", reason, env=env)


def apply_raise(dfolder, d, cp="CP1", reason=REASON, env=None):
    return director(dfolder, d, "director-sign", "raise-limit", cp, "--reason", reason, "--apply", env=env)


def apply_raise_token(d, tok, cp="CP1", reason=REASON):
    """Legacy lane paste path (must refuse in enrolled lanes)."""
    args = ["raise-limit", cp, "--director-token", tok]
    if reason is not None:
        args += ["--reason", reason]
    return rx(d, *args)


def sign_choice(dfolder, d, letter, env=None):
    return director(dfolder, d, "director-sign", "choice", letter, env=env)


def apply_choice(dfolder, d, letter, env=None):
    return director(dfolder, d, "director-sign", "choice", letter, "--apply", env=env)


def block_again(d, cp="CP1"):
    """After a raise: open the one promised extra round, record it blocking, so the step blocks again."""
    r2 = tl._reviewers_after_triage(d, cp)
    if r2.returncode != 0:
        return r2
    rnd = json.loads(r2.stdout)
    p = g.write_review(d, rnd, g.R0(rnd), tl.BLOCKER)
    return rx(d, "adv-record", cp, "--reviews", p)


def unblocked_phase(d):
    return state(d).get("phase") not in ("blocked",)


# ------------------------------------------------------------------------------------------------ raise-limit

def t01_director_raises_limit_with_signed_token():
    d, df = enrolled_cap_lane("t01")
    r_ask = rx(d, "ask", "raise-limit", "CP1")
    check("[CP4-T01] setup: ask raise-limit still runs in the enrolled lane", r_ask.returncode == 0, out(r_ask))
    r = apply_raise(df, d)
    check("[CP4-T01] director-sign raise-limit CP1 --reason --apply succeeds with no chat reply recorded",
          r.returncode == 0, out(r))
    a = adv(d)
    check("[CP4-T01] CP1's cap_raised_to is 3 (configured 2 plus exactly one)", a.get("cap_raised_to") == 3, a)
    check("[CP4-T01] the run is no longer blocked", unblocked_phase(d), state(d).get("phase"))
    check("[CP4-T01] CP1's attempts count is still 2 (not reset)", a.get("attempts") == 2, a)
    check("[CP4-T01] the message says the limit was raised and names the reason",
          re.search(r"raised", r.stdout, re.I) is not None and REASON in r.stdout, out(r))
    check("[CP4-T01] the raise is recorded as the director's", any(e.get("by") == "director" for e in events(d, "unblocked")),
          events(d, "unblocked"))


def t02_reason_saved_exactly_as_chat_raise():
    d, df = enrolled_cap_lane("t02a")
    rx(d, "ask", "raise-limit", "CP1")
    r = apply_raise(df, d)
    check("[CP4-T02] setup: director-sign --apply raise succeeds", r.returncode == 0, out(r))
    d2, _r2 = tl._project_to_cap("t02b")
    rr = tl._raise(d2, "CP1", reason_text=f"raise: {REASON}")
    check("[CP4-T02] setup: the non-enrolled twin's chat raise succeeds", rr.returncode == 0, out(rr))
    want = f"CP1's review-round limit was raised to 3 rounds: {REASON}"
    l1, l2 = learnings(d), learnings(d2)
    check("[CP4-T02] the enrolled lane's learnings.md has the exact raise line once", l1.count(want) == 1, l1[-400:])
    check("[CP4-T02] the chat-raised lane's learnings.md has the same line once", l2.count(want) == 1, l2[-400:])
    line1 = [ln for ln in l1.splitlines() if want in ln]
    line2 = [ln for ln in l2.splitlines() if want in ln]
    tail = lambda ln: ln.split(") ", 1)[-1]  # noqa: E731  (after the "(date, source) " prefix)
    check("[CP4-T02] the text after the date/source prefix is identical in both files",
          bool(line1) and bool(line2) and tail(line1[0]) == tail(line2[0]), (line1, line2))
    ev = [e for e in events(d, "round_limit_raised") if e.get("cp") == "CP1"]
    check("[CP4-T02] the enrolled lane's log has round_limit_raised with cp CP1, cap 3 and the reason",
          len(ev) == 1 and ev[0].get("cap") == 3 and ev[0].get("reason") == REASON, ev)


def t03_signed_raise_grants_exactly_one_round():
    d, df = enrolled_cap_lane("t03")
    rx(d, "ask", "raise-limit", "CP1")
    r = apply_raise(df, d)
    check("[CP4-T03] setup: director-sign --apply raise succeeds", r.returncode == 0, out(r))
    r2 = tl._reviewers_after_triage(d, "CP1")
    check("[CP4-T03] exactly one extra round opens (round 3)",
          r2.returncode == 0 and (json.loads(r2.stdout).get("round") == 3 if r2.returncode == 0 else False), out(r2))
    if r2.returncode != 0:
        check("[CP4-T03] (not reached) the extra round records blocking", False, out(r2))
        return
    rnd = json.loads(r2.stdout)
    p = g.write_review(d, rnd, g.R0(rnd), tl.BLOCKER)
    a3 = rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP4-T03] after round 3 records blocking the run is blocked again", a3.returncode == 2
          and state(d).get("phase") == "blocked", out(a3) + state(d).get("phase", ""))
    check("[CP4-T03] attempts are 3 at the raised cap", adv(d).get("attempts") == 3, adv(d))
    r4 = tl._reviewers_after_triage(d, "CP1")
    check("[CP4-T03] a fourth `reviewers CP1` is refused", r4.returncode != 0, out(r4))


def t04_raise_once_signed_or_not():
    d, df = enrolled_cap_lane("t04")
    rx(d, "ask", "raise-limit", "CP1")
    r = apply_raise(df, d)
    check("[CP4-T04] setup: the first --apply raise succeeds", r.returncode == 0, out(r))
    b = block_again(d)
    check("[CP4-T04] setup: the step blocks again at 3 rounds", b.returncode == 2 and state(d).get("phase") == "blocked"
          and adv(d).get("attempts") == 3, out(b))
    r_ask = rx(d, "ask", "raise-limit", "CP1")
    check("[CP4-T04] `ask raise-limit CP1` is refused as already raised",
          r_ask.returncode != 0 and re.search(r"already raised", out(r_ask), re.I) is not None, out(r_ask))
    r_replay = apply_raise(df, d)
    check("[CP4-T04] a second --apply raise fails (already spent)", r_replay.returncode != 0, out(r_replay))
    r_new = apply_raise(df, d, reason="one more, please")
    check("[CP4-T04] another --apply with a new reason fails too", r_new.returncode != 0, out(r_new))
    check("[CP4-T04] cap_raised_to stays 3", adv(d).get("cap_raised_to") == 3, adv(d))
    check("[CP4-T04] the run stays blocked", state(d).get("phase") == "blocked", state(d).get("phase"))
    check("[CP4-T04] learnings.md has the reason once", learnings(d).count(REASON) == 1
          and "one more, please" not in learnings(d), learnings(d)[-400:])


def t05_raise_token_without_reason_refused_not_spent():
    d, df = enrolled_cap_lane("t05")
    rx(d, "ask", "raise-limit", "CP1")
    before = learnings(d)
    sg = director(df, d, "director-sign", "raise-limit", "CP1", "--apply")
    check("[CP4-T05] director-sign raise-limit --apply with no --reason is refused", sg.returncode != 0, out(sg))
    check("[CP4-T05] the refusal names --reason as missing", "--reason" in out(sg), out(sg))
    check("[CP4-T05] cap_raised_to is unset", adv(d).get("cap_raised_to") is None, adv(d))
    check("[CP4-T05] the run is still blocked", state(d).get("phase") == "blocked", state(d).get("phase"))
    check("[CP4-T05] the pending raise question is still waiting",
          pending(d).get("action") == "raise-limit" and pending(d).get("mode") == "CP1", pending(d))
    check("[CP4-T05] learnings.md is unchanged", learnings(d) == before, learnings(d)[-300:])
    r2 = apply_raise(df, d, reason="needs one more pass")
    check("[CP4-T05] director-sign --apply with --reason then succeeds", r2.returncode == 0, out(r2))
    check("[CP4-T05] and the raise is then recorded", adv(d).get("cap_raised_to") == 3, adv(d))


def t06_raise_token_for_another_step_refused():
    d, df = enrolled_cap_lane("t06", cps=2)
    rx(d, "ask", "raise-limit", "CP1")
    r = apply_raise(df, d, cp="CP2")
    check("[CP4-T06] director-sign --apply raise-limit CP2 is refused while CP1 is blocked",
          r.returncode != 0, out(r))
    check("[CP4-T06] CP1's cap_raised_to is unset", adv(d, "CP1").get("cap_raised_to") is None, adv(d, "CP1"))
    check("[CP4-T06] CP2's cap_raised_to is unset", adv(d, "CP2").get("cap_raised_to") is None, adv(d, "CP2"))
    check("[CP4-T06] the run is still blocked", state(d).get("phase") == "blocked", state(d).get("phase"))


def t07_raise_token_from_older_pause_refused():
    d, df = enrolled_cap_lane("t07")
    rx(d, "ask", "raise-limit", "CP1")
    r0 = apply_raise(df, d)
    check("[CP4-T07] setup: first --apply raise succeeds", r0.returncode == 0 and adv(d).get("cap_raised_to") == 3, out(r0))
    b = block_again(d)
    check("[CP4-T07] setup: the step blocks again at the raised cap", b.returncode == 2 and state(d).get("phase") == "blocked", out(b))
    r_re = rx(d, "ask", "raise-limit", "CP1")
    check("[CP4-T07] setup: asking again after the spent raise is refused", r_re.returncode != 0, out(r_re))
    r_old = apply_raise(df, d)
    check("[CP4-T07] a second --apply is refused (raise already spent)", r_old.returncode != 0, out(r_old))
    check("[CP4-T07] cap_raised_to stays 3", adv(d).get("cap_raised_to") == 3, adv(d))
    check("[CP4-T07] the run is still blocked", state(d).get("phase") == "blocked", state(d).get("phase"))


def t08_raise_token_from_another_lane_refused():
    df = director_folder()
    da, _ = enrolled_cap_lane("t08a", dfolder=df)
    db, _ = enrolled_cap_lane("t08b", dfolder=df)
    rx(da, "ask", "raise-limit", "CP1")
    rx(db, "ask", "raise-limit", "CP1")
    r_b = apply_raise(df, db)
    check("[CP4-T08] lane B's --apply succeeds only in lane B", r_b.returncode == 0 and adv(db).get("cap_raised_to") == 3,
          out(r_b))
    check("[CP4-T08] lane A stays blocked with cap_raised_to unset until its own --apply",
          state(da).get("phase") == "blocked" and adv(da).get("cap_raised_to") is None, (state(da).get("phase"), adv(da)))
    r_a = apply_raise(df, da)
    check("[CP4-T08] lane A's own --apply works in lane A", r_a.returncode == 0 and adv(da).get("cap_raised_to") == 3,
          out(r_a))


def t09_builder_cannot_sign_or_forge_and_chat_raises_nothing():
    d, df = enrolled_cap_lane("t09")
    rx(d, "ask", "raise-limit", "CP1")
    before = learnings(d)
    # (a) not the director's session
    sa = not_director(df, d, "director-sign", "raise-limit", "CP1", "--reason", "x", "--apply")
    check("[CP4-T09] (a) director-sign --apply from a non-director session is refused",
          sa.returncode != 0, out(sa))
    # (b) pasteable --director-token on the lane CLI is gone
    rb1 = apply_raise_token(d, MADE_UP_TOKEN, reason="x")
    check("[CP4-T09] (b) a made-up --director-token is refused", rb1.returncode != 0, out(rb1))
    check("[CP4-T09] (b) the refusal names director-sign --apply",
          DIRECTOR_HARNESS_RE.search(out(rb1)) is not None or "no longer accepted" in out(rb1), out(rb1))
    # (c) a chat reply
    say(d, "raise: x")
    rc = rx(d, "raise-limit", "CP1")
    check("[CP4-T09] (c) a chat `raise:` reply raises nothing in an enrolled lane", rc.returncode != 0, out(rc))
    check("[CP4-T09] (c) the refusal names the director harness", DIRECTOR_HARNESS_RE.search(out(rc)) is not None, out(rc))
    check("[CP4-T09] cap_raised_to stays unset", adv(d).get("cap_raised_to") is None, adv(d))
    check("[CP4-T09] the run stays blocked", state(d).get("phase") == "blocked", state(d).get("phase"))
    check("[CP4-T09] learnings.md is unchanged", learnings(d) == before, learnings(d)[-300:])


def t10_tokens_do_not_cross_actions():
    d, df = enrolled_cap_lane("t10")
    assert state(d).get("phase") == "blocked", state(d).get("phase")
    rx(d, "ask", "raise-limit", "CP1")
    plain_h = director(df, d, "director-sign", "human-approve")
    check("[CP4-T10] human-approve without --apply refuses", plain_h.returncode != 0, out(plain_h))
    apply_raise(df, d)
    check("[CP4-T10] setup: raise-limit --apply succeeded", adv(d).get("cap_raised_to") == 3, adv(d))
    r2 = rx(d, "human-approve", "--director-token", MADE_UP_TOKEN)
    check("[CP4-T10] lane human-approve with --director-token is refused", r2.returncode != 0, out(r2))
    plan_path = d / ".ratchet/runs/f/checkpoints.json"
    plan = json.loads(plan_path.read_text())
    plan["checkpoints"][0]["title"] = "Step 1 (renamed after approval)"  # a real change for approve-changes to see
    plan_path.write_text(json.dumps(plan))
    r3 = rx(d, "approve-changes", "--director-token", MADE_UP_TOKEN)
    check("[CP4-T10] approve-changes --director-token is refused", r3.returncode != 0 and "Nothing has changed" not in out(r3), out(r3))
    plan["checkpoints"][0]["title"] = "Step 1"
    plan_path.write_text(json.dumps(plan))
    r4 = rx(d, "accept-finding", "CP1", "--director-token", MADE_UP_TOKEN, "--reason", "x")
    check("[CP4-T10] accept-finding --director-token is refused", r4.returncode != 0, out(r4))
    check("[CP4-T10] cap_raised_to is set only by the raise --apply", adv(d).get("cap_raised_to") == 3, adv(d))
    check("[CP4-T10] pasteable tokens did not approve-changes or accept-finding",
          not events(d, "changes_approved") and not events(d, "finding_accepted"), events(d, "changes_approved"))


def t11_signed_raise_at_run_wide_cap_never_resets_budget():
    d, env, tp = taf.run_wide_cap_project("cp4t11")
    th.cp3_pass_behavior(d, env, "CP1")
    rnd = th.cp3_open_round(d, env, "CP1")
    rid = th.cp3_R0(rnd)
    path = taf.write_round_review(d, env, rnd, rid, [taf.F1, taf.F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    th.rx(d, env, "triage", "CP1", f"{rid}/F1=fix:known tradeoff", f"{rid}/F2=fix:known tradeoff")
    r2 = th.rx(d, env, "reviewers", "CP1", "--json")
    st0 = th.cp3_state(d)
    check("[CP4-T11] setup: the run is blocked on the RUN-WIDE cap after CP1's first blocking round",
          r.returncode == 2 and r2.returncode != 0 and st0.get("phase") == "blocked"
          and str(st0.get("blocked_reason", "")).startswith("Run-wide"), (out(r2), st0.get("blocked_reason")))
    total_before = st0.get("adv_rounds_total")
    df = director_folder("cp4t11")
    enroll(d, df, env=env)
    r_ask = th.rx(d, env, "ask", "raise-limit", "CP1")
    check("[CP4-T11] setup: ask raise-limit runs at the run-wide cap", r_ask.returncode == 0, out(r_ask))
    r3 = director(df, d, "director-sign", "raise-limit", "CP1", "--reason", REASON, "--apply", env=env)
    check("[CP4-T11] director-sign raise-limit --apply is accepted at the run-wide cap", r3.returncode == 0, out(r3))
    a = (th.cp3_state(d).get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {})
    check("[CP4-T11] CP1's cap_raised_to is set", isinstance(a.get("cap_raised_to"), int), a)
    check("[CP4-T11] the output does not say the run is unblocked", "no longer blocked" not in out(r3).lower(), out(r3))
    st1 = th.cp3_state(d)
    check("[CP4-T11] the phase is still blocked", st1.get("phase") == "blocked", st1.get("phase"))
    check("[CP4-T11] adv_rounds_total is exactly what it was (not reset)",
          isinstance(total_before, int) and total_before > 0 and st1.get("adv_rounds_total") == total_before,
          (total_before, st1.get("adv_rounds_total")))


def t12_raise_refused_when_not_blocked():
    d, df = building_lane("t12", proto=False)
    r_apply = apply_raise(df, d)
    check("[CP4-T12] director-sign raise-limit --apply is refused while CP1 is building",
          r_apply.returncode != 0, out(r_apply))
    check("[CP4-T12] the refusal says plainly that raise-limit applies only at the review limit",
          re.search(r"blocked|review[- ]round limit|review limit", out(r_apply), re.I) is not None, out(r_apply))
    r_ask = rx(d, "ask", "raise-limit", "CP1")
    check("[CP4-T12] `ask raise-limit CP1` is refused too, saying it applies only when blocked at the limit",
          r_ask.returncode != 0 and re.search(r"not blocked|only applies", out(r_ask), re.I) is not None, out(r_ask))
    check("[CP4-T12] cap_raised_to stays unset", adv(d).get("cap_raised_to") is None, adv(d))
    check("[CP4-T12] the run is still building", state(d).get("phase") == "building", state(d).get("phase"))


def t13_without_director_nothing_changes_for_chat_raise():
    df = director_folder()
    d0 = project("t13key")  # any lane just to create the key on this machine (shared RATCHET_HOME)
    rk = director(df, d0, "director-key")
    check("[CP4-T13] setup: a director key exists on the machine",
          rk.returncode == 0 and (Path(g.ENV["RATCHET_HOME"]) / "director.key").exists(), out(rk))
    # (a) a non-enrolled lane: the chat raise works exactly as before
    d, r = tl._project_to_cap("t13a")
    check("[CP4-T13] (a) setup: the non-enrolled lane is blocked at its round limit", r.returncode != 0, out(r))
    rr = tl._raise(d, "CP1", reason_text="raise: need one more look")
    check("[CP4-T13] (a) the chat raise succeeds with no token", rr.returncode == 0, out(rr))
    check("[CP4-T13] (a) cap_raised_to is 3", adv(d).get("cap_raised_to") == 3, adv(d))
    check("[CP4-T13] (a) the reason is in learnings.md", "need one more look" in learnings(d), learnings(d)[-300:])
    check("[CP4-T13] (a) the run is unblocked", unblocked_phase(d), state(d).get("phase"))
    # (b) a second non-enrolled lane: a director-signed token is refused
    d2, r2 = tl._project_to_cap("t13b")
    rx(d2, "ask", "raise-limit", "CP1")
    rb = apply_raise_token(d2, MADE_UP_TOKEN)
    check("[CP4-T13] (b) the non-enrolled lane refuses --director-token paste", rb.returncode != 0, out(rb))
    check("[CP4-T13] (b) the refusal says the lane only accepts human approvals",
          re.search(r"human approvals", out(rb), re.I) is not None, out(rb))
    check("[CP4-T13] (b) cap_raised_to stays unset and the lane stays blocked",
          adv(d2).get("cap_raised_to") is None and state(d2).get("phase") == "blocked", (adv(d2), state(d2).get("phase")))


# ------------------------------------------------------------------------------------------------ choice

def t14_director_pick_unlocks_file_like_human_pick():
    d, df = building_lane("t14")
    r_ask = ask_choice(d, "--locked", f"A={PROTO}")
    check("[CP4-T14] `ask choice` is allowed in the enrolled lane", r_ask.returncode == 0
          and "Put the options to the director" not in out(r_ask), out(r_ask))
    check("[CP4-T14] the ask prints the options", all(re.search(rf"\b{L}\b", r_ask.stdout) for L in "ABC"), r_ask.stdout)
    # the non-enrolled twin, picked by a chat reply
    d2, _ = building_lane("t14twin", enrolled=False)
    rx(d2, "ask", "choice", "--options", "A,B,C", "--locked", f"A={PROTO}")
    say(d2, "A")
    r_twin = rx(d2, "choice")
    check("[CP4-T14] setup: the twin's chat pick of A is recorded", r_twin.returncode == 0, out(r_twin))
    r = apply_choice(df, d, "A")
    check("[CP4-T14] director-sign choice A --apply succeeds with no chat reply", r.returncode == 0, out(r))
    check("[CP4-T14] the message says option A was picked, approves changing the prototype, and to run approve-changes",
          "picked option A" in r.stdout and f"approves changing {PROTO}" in r.stdout and "approve-changes" in r.stdout, out(r))
    check("[CP4-T14] the pending question is gone", not pending(d), pending(d))
    ev = events(d, "choice_made")
    check("[CP4-T14] a choice_made event names letter A and the file",
          len(ev) == 1 and ev[0].get("letter") == "A" and ev[0].get("approves") == [PROTO], ev)
    keys = ("letter", "files", "cp", "unblock", "guardrail")
    mine = {k: (state(d).get("choice_allow") or {}).get(k) for k in keys}
    twin = {k: (state(d2).get("choice_allow") or {}).get(k) for k in keys}
    check("[CP4-T14] state.choice_allow matches the twin's (letter, files, cp, unblock, guardrail)",
          state(d).get("choice_allow") is not None and mine == twin and mine["letter"] == "A" and mine["files"] == [PROTO],
          (mine, twin))
    (d / ".ratchet/runs/f" / PROTO).write_text("<p aria-live='polite'>changed</p>")
    ra = rx(d, "approve-changes")
    check("[CP4-T14] approve-changes then accepts the prototype change on the strength of the pick",
          ra.returncode == 0 and "prototype" in ra.stdout, out(ra))


def t15_pick_that_unlocks_nothing_records_letter_only():
    d, df = building_lane("t15")
    ask_choice(d, "--locked", f"A={PROTO}")
    r = apply_choice(df, d, "B")
    check("[CP4-T15] the signed pick of B is recorded", r.returncode == 0 and "picked option B" in r.stdout, out(r))
    ev = events(d, "choice_made")
    check("[CP4-T15] choice_made names letter B", len(ev) == 1 and ev[0].get("letter") == "B", ev)
    check("[CP4-T15] the pending question is cleared", not pending(d), pending(d))
    check("[CP4-T15] no choice_allow in state", "choice_allow" not in state(d), state(d).get("choice_allow"))
    (d / ".ratchet/runs/f" / PROTO).write_text("<p>changed</p>")
    rt = rx(d, "tests-written", "CP1")
    check("[CP4-T15] changing the prototype still fails the lock check", rt.returncode != 0 and "prototype" in out(rt), out(rt))
    ra = rx(d, "approve-changes")
    check("[CP4-T15] approve-changes still refuses it", ra.returncode != 0, out(ra))


def t16_signed_pick_at_review_limit_block_gives_no_extra_round():
    # the raise is spent BEFORE enrollment (a plain chat raise, as test_limits CP1-X02 does), then the step blocks
    # again at the raised cap and the lane is enrolled: what is under test is the signed pick plus human-approve
    d, r = tl._project_to_cap("t16", max_rounds=2)
    rr = tl._raise(d, "CP1")
    b = block_again(d)
    check("[CP4-T16] setup: the one raise is spent and the step blocks again at the raised cap",
          rr.returncode == 0 and b.returncode == 2 and state(d).get("phase") == "blocked", out(rr) + out(b))
    df = enroll(d)
    before = adv(d)
    check("[CP4-T16] setup: attempts sit at the raised cap", before.get("attempts") == before.get("cap_raised_to") == 3, before)
    r_ask = ask_choice(d)
    check("[CP4-T16] setup: ask choice runs in the enrolled blocked lane", r_ask.returncode == 0, out(r_ask))
    rc = apply_choice(df, d, "B")
    check("[CP4-T16] the signed pick of B is recorded", rc.returncode == 0, out(rc))
    allow = state(d).get("choice_allow") or {}
    check("[CP4-T16] choice_allow records B with unblock true", allow.get("letter") == "B" and allow.get("unblock") is True, allow)
    check("[CP4-T16] the run is still blocked until human-approve", state(d).get("phase") == "blocked", state(d).get("phase"))
    rh = director(df, d, "director-sign", "human-approve", "--apply")
    check("[CP4-T16] director-sign human-approve --apply resumes the run", rh.returncode == 0 and unblocked_phase(d), out(rh))
    after = adv(d)
    check("[CP4-T16] CP1's attempts count is unchanged", after.get("attempts") == before.get("attempts"), (before, after))
    check("[CP4-T16] cap_raised_to is unchanged", after.get("cap_raised_to") == before.get("cap_raised_to"), (before, after))
    r5 = tl._reviewers_after_triage(d, "CP1")
    check("[CP4-T16] `reviewers CP1` refuses to open another round", r5.returncode != 0, out(r5))


def t17_choice_token_for_another_letter_refused():
    d, df = building_lane("t17")
    ask_choice(d, "--locked", f"A={PROTO}")
    ra = apply_choice(df, d, "A")
    check("[CP4-T17] choice A --apply succeeds",
          ra.returncode == 0 and [e.get("letter") for e in events(d, "choice_made")] == ["A"], out(ra))
    rb = apply_choice(df, d, "B")
    check("[CP4-T17] a second choice --apply is refused (no question waiting)", rb.returncode != 0, out(rb))
    check("[CP4-T17] nothing else was recorded",
          len(events(d, "choice_made")) == 1, events(d, "choice_made"))


def t18_pick_not_offered_or_no_question_cannot_be_signed():
    da, df = building_lane("t18a")
    ask_choice(da)
    sa = sign_choice(df, da, "D")
    check("[CP4-T18] (a) director-sign choice D (no --apply) is refused", sa.returncode != 0 and NEEDS_APPLY_RE.search(out(sa)), out(sa))
    check("[CP4-T18] (a) the refusal names the offered letters or needs --apply",
          re.search(r"A,\s?B,\s?C", out(sa)) is not None or NEEDS_APPLY_RE.search(out(sa)), out(sa))
    check("[CP4-T18] (a) no choice_allow and the question is still pending",
          "choice_allow" not in state(da) and pending(da).get("action") == "choice", pending(da))
    db, _ = building_lane("t18b", dfolder=df)
    rb = apply_choice(df, db, "A")
    check("[CP4-T18] (b) with no question waiting, --apply is refused", rb.returncode != 0, out(rb))
    check("[CP4-T18] (b) the message says to run `ask choice` first",
          "ask choice" in out(rb), out(rb))
    check("[CP4-T18] (b) nothing is recorded in that lane",
          not events(db, "choice_made") and "choice_allow" not in state(db) and not pending(db),
          (events(db, "choice_made"), state(db).get("choice_allow"), pending(db)))


def t19_choice_token_from_older_question_or_used_refused():
    d, df = building_lane("t19")
    ask_choice(d)
    apply_choice(df, d, "A")
    check("[CP4-T19] setup: the first pick of A was recorded", [e.get("letter") for e in events(d, "choice_made")] == ["A"],
          events(d, "choice_made"))
    r_re = ask_choice(d)
    check("[CP4-T19] setup: the question was asked again (a new question)", r_re.returncode == 0, out(r_re))
    r_fresh = apply_choice(df, d, "A")
    check("[CP4-T19] a fresh --apply for the new question works", r_fresh.returncode == 0, out(r_fresh))
    r_replay = apply_choice(df, d, "A")
    check("[CP4-T19] a replay --apply is refused (no question waiting)",
          r_replay.returncode != 0 and not pending(d), out(r_replay))
    check("[CP4-T19] the recorded picks are both letter A",
          events(d, "choice_made") and all(e.get("letter") == "A" for e in events(d, "choice_made")),
          events(d, "choice_made"))


def t20_choice_token_from_another_lane_refused():
    df = director_folder()
    da, _ = building_lane("t20a", dfolder=df)
    db, _ = building_lane("t20b", dfolder=df)
    rx(da, "ask", "choice", "--options", "A,B")
    rx(db, "ask", "choice", "--options", "A,B")
    rb = apply_choice(df, db, "A")
    check("[CP4-T20] lane B records its own --apply pick", rb.returncode == 0
          and [e.get("letter") for e in events(db, "choice_made")] == ["A"], out(rb))
    check("[CP4-T20] lane A still has no pick recorded",
          not events(da, "choice_made") and pending(da).get("action") == "choice", pending(da))
    ra = apply_choice(df, da, "A")
    check("[CP4-T20] lane A can still apply its own pick", ra.returncode == 0
          and [e.get("letter") for e in events(da, "choice_made")] == ["A"], out(ra))


def t21_builder_cannot_pick_and_chat_reply_picks_nothing():
    d, df = building_lane("t21")
    ask_choice(d)
    sa = not_director(df, d, "director-sign", "choice", "A")
    check("[CP4-T21] director-sign choice from a non-director session is refused",
          sa.returncode != 0, out(sa))
    rm = rx(d, "choice", "A", "--director-token", MADE_UP_TOKEN)
    check("[CP4-T21] a made-up token is refused", rm.returncode != 0, out(rm))
    say(d, "A")
    rc = rx(d, "choice", "A")
    check("[CP4-T21] a chat reply plus a tokenless `choice A` is refused", rc.returncode != 0, out(rc))
    check("[CP4-T21] the refusal names the director harness", DIRECTOR_HARNESS_RE.search(out(rc)) is not None, out(rc))
    check("[CP4-T21] no choice_made, no choice_allow, question still pending",
          not events(d, "choice_made") and "choice_allow" not in state(d) and pending(d).get("action") == "choice",
          (events(d, "choice_made"), state(d).get("choice_allow"), pending(d)))


def _section(text, start_re, end_re):
    m = re.search(start_re, text)
    if not m:
        return ""
    rest = text[m.end():]
    e = re.search(end_re, rest)
    return rest[:e.start()] if e else rest


def _director_doc_checks(label, sec):
    check(f"[CP4-T22] {label}: has a director section", bool(sec.strip()), "")
    check(f"[CP4-T22] {label}: names director-sign for raise-limit",
          "director-sign" in sec and "raise-limit" in sec, sec[:300])
    check(f"[CP4-T22] {label}: the raise-limit path takes --reason", "--reason" in sec, "")
    check(f"[CP4-T22] {label}: names director-sign for choice",
          "director-sign" in sec and "choice" in sec, sec[:300])
    check(f"[CP4-T22] {label}: says `ask` runs before applying", re.search(r"`ask\b|\bask (raise-limit|choice)\b", sec) is not None, "")
    check(f"[CP4-T22] {label}: documents --apply as the harness path",
          "--apply" in sec or "one step" in sec.lower(), sec[:300])
    check(f"[CP4-T22] {label}: still lists approve-plan", "approve-plan" in sec, "")
    check(f"[CP4-T22] {label}: still lists accept-finding", "accept-finding" in sec, "")


def t22_docs_list_both_actions():
    readme = README_DOCS.read_text()
    para = next((p for p in readme.split("\n\n") if "Director-signed approvals" in p), "")
    _director_doc_checks("README director paragraph", para)
    skill = (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text()
    _director_doc_checks("SKILL.md 'Running under a director'",
                         _section(skill, r"(?m)^## Running under a director.*$", r"(?m)^## "))
    schemas = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text()
    _director_doc_checks("schemas.md section 9", _section(schemas, r"(?m)^## 9\..*$", r"(?m)^## 10\."))


def t23_lettered_question_waits_for_director_in_enrolled_lane():
    d, df = building_lane("t23", proto=False)
    r_ask = rx(d, "ask", "choice", "--options", "A,B")
    check("[CP4-T23] `ask choice` succeeds in the enrolled lane (no 'put the options to the director' refusal)",
          r_ask.returncode == 0 and "Put the options to the director" not in out(r_ask), out(r_ask))
    p = pending(d)
    check("[CP4-T23] the question is recorded pending with options A and B and an asked-at time",
          p.get("action") == "choice" and p.get("options") == ["A", "B"] and bool(p.get("asked_at")), p)
    say(d, "A")
    rc = rx(d, "choice", "A")
    check("[CP4-T23] a chat reply plus a tokenless `choice A` is refused naming director-sign --apply",
          rc.returncode != 0 and DIRECTOR_HARNESS_RE.search(out(rc)) is not None, out(rc))
    check("[CP4-T23] nothing is recorded and the question is still pending",
          not events(d, "choice_made") and "choice_allow" not in state(d) and pending(d).get("action") == "choice",
          (events(d, "choice_made"), pending(d)))
    rb = apply_choice(df, d, "B")
    check("[CP4-T23] director-sign choice B --apply is then recorded",
          rb.returncode == 0 and [e.get("letter") for e in events(d, "choice_made")] == ["B"] and not pending(d), out(rb))


# ------------------------------------------------------------------------------------------------ runner

TESTS = [
    ("CP4-T01", t01_director_raises_limit_with_signed_token),
    ("CP4-T02", t02_reason_saved_exactly_as_chat_raise),
    ("CP4-T03", t03_signed_raise_grants_exactly_one_round),
    ("CP4-T04", t04_raise_once_signed_or_not),
    ("CP4-T05", t05_raise_token_without_reason_refused_not_spent),
    ("CP4-T06", t06_raise_token_for_another_step_refused),
    ("CP4-T07", t07_raise_token_from_older_pause_refused),
    ("CP4-T08", t08_raise_token_from_another_lane_refused),
    ("CP4-T09", t09_builder_cannot_sign_or_forge_and_chat_raises_nothing),
    ("CP4-T10", t10_tokens_do_not_cross_actions),
    ("CP4-T11", t11_signed_raise_at_run_wide_cap_never_resets_budget),
    ("CP4-T12", t12_raise_refused_when_not_blocked),
    ("CP4-T13", t13_without_director_nothing_changes_for_chat_raise),
    ("CP4-T14", t14_director_pick_unlocks_file_like_human_pick),
    ("CP4-T15", t15_pick_that_unlocks_nothing_records_letter_only),
    ("CP4-T16", t16_signed_pick_at_review_limit_block_gives_no_extra_round),
    ("CP4-T17", t17_choice_token_for_another_letter_refused),
    ("CP4-T18", t18_pick_not_offered_or_no_question_cannot_be_signed),
    ("CP4-T19", t19_choice_token_from_older_question_or_used_refused),
    ("CP4-T20", t20_choice_token_from_another_lane_refused),
    ("CP4-T21", t21_builder_cannot_pick_and_chat_reply_picks_nothing),
    ("CP4-T22", t22_docs_list_both_actions),
    ("CP4-T23", t23_lettered_question_waits_for_director_in_enrolled_lane),
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
