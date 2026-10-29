#!/usr/bin/env python3
"""
CP4 tests: a director signs raise-limit and lettered choices.   python3 tests/test_director_sign_more.py

In a director-enrolled lane a chat reply approves nothing, so a lane stuck at its review-round limit, or waiting
on a lettered question, needs the director: `director-sign raise-limit CPn --reason "..."` gives a token that
`raise-limit CPn --director-token <t> --reason "..."` uses to raise that step's limit by exactly one round, once,
with the reason saved to learnings.md as a chat raise saves it; `ask choice` runs in an enrolled lane (today it is
refused there) and `director-sign choice <L>` gives a token that `choice <L> --director-token <t>` uses to record
that letter exactly as a developer's pick would, including what the option unlocks. A token for another lane,
another step, another letter, another action or an older pause is refused, a raise token without --reason is
refused and left usable, a builder session cannot sign, a chat `raise:` reply or a typed letter approves nothing
in an enrolled lane, and the run-wide review budget is never reset.

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
DIRECTOR_TOKEN_RE = re.compile(r"director[- ]token|director-sign|director-enrolled", re.I)


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
    cmd = f"python3 {g.ENGINE} --root {lane} {shlex.join(str(a) for a in args)}"
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


def sign_raise(dfolder, d, cp="CP1", reason=REASON):
    return director(dfolder, d, "director-sign", "raise-limit", cp, "--reason", reason)


def apply_raise(d, tok, cp="CP1", reason=REASON):
    args = ["raise-limit", cp, "--director-token", tok]
    if reason is not None:
        args += ["--reason", reason]
    return rx(d, *args)


def sign_choice(dfolder, d, letter):
    return director(dfolder, d, "director-sign", "choice", letter)


def apply_choice(d, letter, tok):
    return rx(d, "choice", letter, "--director-token", tok)


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
    sg = sign_raise(df, d)
    tok = token_of(sg)
    check("[CP4-T01] director-sign raise-limit CP1 --reason prints a token", sg.returncode == 0 and bool(tok), out(sg))
    r = apply_raise(d, tok)
    check("[CP4-T01] raise-limit CP1 --director-token --reason succeeds with no chat reply recorded",
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
    tok = token_of(sign_raise(df, d))
    r = apply_raise(d, tok)
    check("[CP4-T02] setup: the signed raise succeeds", r.returncode == 0 and bool(tok), out(r))
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
    tok = token_of(sign_raise(df, d))
    r = apply_raise(d, tok)
    check("[CP4-T03] setup: the signed raise succeeds", r.returncode == 0 and bool(tok), out(r))
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
    tok1 = token_of(sign_raise(df, d))
    r = apply_raise(d, tok1)
    check("[CP4-T04] setup: the first signed raise succeeds", r.returncode == 0 and bool(tok1), out(r))
    b = block_again(d)
    check("[CP4-T04] setup: the step blocks again at 3 rounds", b.returncode == 2 and state(d).get("phase") == "blocked"
          and adv(d).get("attempts") == 3, out(b))
    r_ask = rx(d, "ask", "raise-limit", "CP1")
    check("[CP4-T04] `ask raise-limit CP1` is refused as already raised",
          r_ask.returncode != 0 and re.search(r"already raised", out(r_ask), re.I) is not None, out(r_ask))
    r_replay = apply_raise(d, tok1)
    check("[CP4-T04] the replayed (already used) token fails", r_replay.returncode != 0, out(r_replay))
    sg2 = sign_raise(df, d, reason="one more, please")
    tok2 = token_of(sg2)
    r_new = apply_raise(d, tok2, reason="one more, please") if tok2 else None
    check("[CP4-T04] a new signature fails too (refused at signing, or its token refused)",
          sg2.returncode != 0 or (r_new is not None and r_new.returncode != 0), out(sg2) + (out(r_new) if r_new else ""))
    check("[CP4-T04] cap_raised_to stays 3", adv(d).get("cap_raised_to") == 3, adv(d))
    check("[CP4-T04] the run stays blocked", state(d).get("phase") == "blocked", state(d).get("phase"))
    check("[CP4-T04] learnings.md has the reason once", learnings(d).count(REASON) == 1
          and "one more, please" not in learnings(d), learnings(d)[-400:])


def t05_raise_token_without_reason_refused_not_spent():
    d, df = enrolled_cap_lane("t05")
    rx(d, "ask", "raise-limit", "CP1")
    before = learnings(d)
    tok = token_of(sign_raise(df, d, reason="needs one more pass"))
    check("[CP4-T05] setup: a raise-limit token was signed", bool(tok), "")
    r1 = apply_raise(d, tok, reason=None)
    check("[CP4-T05] raise-limit with a token but no --reason is refused", r1.returncode != 0, out(r1))
    check("[CP4-T05] the refusal names --reason as missing", "--reason" in out(r1), out(r1))
    check("[CP4-T05] cap_raised_to is unset", adv(d).get("cap_raised_to") is None, adv(d))
    check("[CP4-T05] the run is still blocked", state(d).get("phase") == "blocked", state(d).get("phase"))
    check("[CP4-T05] the pending raise question is still waiting",
          pending(d).get("action") == "raise-limit" and pending(d).get("mode") == "CP1", pending(d))
    check("[CP4-T05] learnings.md is unchanged", learnings(d) == before, learnings(d)[-300:])
    r2 = apply_raise(d, tok, reason="needs one more pass")
    check("[CP4-T05] the SAME token with --reason succeeds", r2.returncode == 0, out(r2))
    check("[CP4-T05] and the raise is then recorded", adv(d).get("cap_raised_to") == 3, adv(d))


def t06_raise_token_for_another_step_refused():
    d, df = enrolled_cap_lane("t06", cps=2)
    rx(d, "ask", "raise-limit", "CP1")
    sg = sign_raise(df, d, cp="CP2")
    tok = token_of(sg)
    r = apply_raise(d, tok) if tok else None
    check("[CP4-T06] signing for the unblocked CP2 is refused, or its token is refused on CP1",
          sg.returncode != 0 or (r is not None and r.returncode != 0), out(sg) + (out(r) if r else ""))
    check("[CP4-T06] CP1's cap_raised_to is unset", adv(d, "CP1").get("cap_raised_to") is None, adv(d, "CP1"))
    check("[CP4-T06] CP2's cap_raised_to is unset", adv(d, "CP2").get("cap_raised_to") is None, adv(d, "CP2"))
    check("[CP4-T06] the run is still blocked", state(d).get("phase") == "blocked", state(d).get("phase"))


def t07_raise_token_from_older_pause_refused():
    d, df = enrolled_cap_lane("t07")
    rx(d, "ask", "raise-limit", "CP1")
    old = token_of(sign_raise(df, d))
    check("[CP4-T07] setup: a token was signed for the first pause", bool(old), "")
    r_re = rx(d, "ask", "raise-limit", "CP1")
    check("[CP4-T07] setup: asking again moves the pause", r_re.returncode == 0, out(r_re))
    r_old = apply_raise(d, old)
    check("[CP4-T07] the old token is refused as stale", r_old.returncode != 0, out(r_old))
    check("[CP4-T07] no raise happened", adv(d).get("cap_raised_to") is None, adv(d))
    check("[CP4-T07] the run is still blocked", state(d).get("phase") == "blocked", state(d).get("phase"))
    new = token_of(sign_raise(df, d))
    r_new = apply_raise(d, new)
    check("[CP4-T07] a freshly signed token for the new pause works", r_new.returncode == 0 and adv(d).get("cap_raised_to") == 3,
          out(r_new))


def t08_raise_token_from_another_lane_refused():
    df = director_folder()
    da, _ = enrolled_cap_lane("t08a", dfolder=df)
    db, _ = enrolled_cap_lane("t08b", dfolder=df)
    rx(da, "ask", "raise-limit", "CP1")
    rx(db, "ask", "raise-limit", "CP1")
    tok_a = token_of(sign_raise(df, da))
    check("[CP4-T08] setup: lane A's token was signed", bool(tok_a), "")
    r_b = apply_raise(db, tok_a)
    check("[CP4-T08] lane B refuses lane A's token", r_b.returncode != 0, out(r_b))
    check("[CP4-T08] lane B stays blocked with cap_raised_to unset",
          state(db).get("phase") == "blocked" and adv(db).get("cap_raised_to") is None, (state(db).get("phase"), adv(db)))
    check("[CP4-T08] lane A is untouched until its own token is applied",
          state(da).get("phase") == "blocked" and adv(da).get("cap_raised_to") is None, (state(da).get("phase"), adv(da)))
    r_a = apply_raise(da, tok_a)
    check("[CP4-T08] lane A's own token still works in lane A", r_a.returncode == 0 and adv(da).get("cap_raised_to") == 3,
          out(r_a))


def t09_builder_cannot_sign_or_forge_and_chat_raises_nothing():
    d, df = enrolled_cap_lane("t09")
    rx(d, "ask", "raise-limit", "CP1")
    before = learnings(d)
    # (a) not the director's session
    sa = not_director(df, d, "director-sign", "raise-limit", "CP1", "--reason", "x")
    check("[CP4-T09] (a) director-sign from a non-director session is refused and prints no token",
          sa.returncode != 0 and not sa.stdout.strip(), out(sa))
    # (b) a made-up token, then a token signed with a different director key (the key rotated, token signed,
    # the original key put back, so the lane's enrollment is still valid but the token's key is not its key)
    rb1 = apply_raise(d, MADE_UP_TOKEN, reason="x")
    check("[CP4-T09] (b) a made-up token is refused", rb1.returncode != 0, out(rb1))
    key_path = Path(g.ENV["RATCHET_HOME"]) / "director.key"
    old_key = key_path.read_bytes()
    rot = director(df, d, "director-key", "--rotate")
    forged = token_of(sign_raise(df, d, reason="x"))
    key_path.write_bytes(old_key)
    check("[CP4-T09] (b) setup: the key was rotated, a token signed with it, and the original key restored",
          rot.returncode == 0 and key_path.read_bytes() == old_key, out(rot))
    rb2 = apply_raise(d, forged or MADE_UP_TOKEN, reason="x")
    check("[CP4-T09] (b) a token signed with a different director key is refused", rb2.returncode != 0, out(rb2))
    # (c) a chat reply
    say(d, "raise: x")
    rc = rx(d, "raise-limit", "CP1")
    check("[CP4-T09] (c) a chat `raise:` reply raises nothing in an enrolled lane", rc.returncode != 0, out(rc))
    check("[CP4-T09] (c) the refusal names the director token", DIRECTOR_TOKEN_RE.search(out(rc)) is not None, out(rc))
    check("[CP4-T09] cap_raised_to stays unset", adv(d).get("cap_raised_to") is None, adv(d))
    check("[CP4-T09] the run stays blocked", state(d).get("phase") == "blocked", state(d).get("phase"))
    check("[CP4-T09] learnings.md is unchanged", learnings(d) == before, learnings(d)[-300:])


def t10_tokens_do_not_cross_actions():
    d, df = enrolled_cap_lane("t10")
    rx(d, "ask", "raise-limit", "CP1")
    tok_h = token_of(director(df, d, "director-sign", "human-approve"))
    check("[CP4-T10] setup: a human-approve token was signed (blocked phase)", bool(tok_h), "")
    r1 = apply_raise(d, tok_h, reason="x")
    check("[CP4-T10] a human-approve token cannot raise a limit", r1.returncode != 0, out(r1))
    tok_r = token_of(sign_raise(df, d))
    check("[CP4-T10] setup: a raise-limit token was signed", bool(tok_r), "")
    r2 = rx(d, "human-approve", "--director-token", tok_r or MADE_UP_TOKEN)
    check("[CP4-T10] a raise token cannot human-approve", r2.returncode != 0, out(r2))
    plan_path = d / ".ratchet/runs/f/checkpoints.json"
    plan = json.loads(plan_path.read_text())
    plan["checkpoints"][0]["title"] = "Step 1 (renamed after approval)"  # a real change for approve-changes to see
    plan_path.write_text(json.dumps(plan))
    r3 = rx(d, "approve-changes", "--director-token", tok_r or MADE_UP_TOKEN)
    check("[CP4-T10] a raise token cannot approve-changes", r3.returncode != 0 and "Nothing has changed" not in out(r3), out(r3))
    plan["checkpoints"][0]["title"] = "Step 1"
    plan_path.write_text(json.dumps(plan))
    r4 = rx(d, "accept-finding", "CP1", "--director-token", tok_r or MADE_UP_TOKEN, "--reason", "x")
    check("[CP4-T10] a raise token cannot accept-finding", r4.returncode != 0, out(r4))
    check("[CP4-T10] cap_raised_to stays unset", adv(d).get("cap_raised_to") is None, adv(d))
    check("[CP4-T10] the run stays blocked (a raise token never unblocks it as a human-approve would)",
          state(d).get("phase") == "blocked", state(d).get("phase"))


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
    sg = director(df, d, "director-sign", "raise-limit", "CP1", "--reason", REASON, env=env)
    tok = token_of(sg)
    r3 = th.rx(d, env, "raise-limit", "CP1", "--director-token", tok or MADE_UP_TOKEN, "--reason", REASON)
    check("[CP4-T11] the signed raise is accepted", sg.returncode == 0 and r3.returncode == 0, out(sg) + out(r3))
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
    sg = sign_raise(df, d)
    tok = token_of(sg)
    r_apply = apply_raise(d, tok) if tok else None
    check("[CP4-T12] director-sign raise-limit is refused while CP1 is building (or its token is refused)",
          sg.returncode != 0 or (r_apply is not None and r_apply.returncode != 0), out(sg) + (out(r_apply) if r_apply else ""))
    check("[CP4-T12] the signing refusal says plainly that raise-limit applies only at the review limit",
          sg.returncode != 0 and re.search(r"blocked|review[- ]round limit|review limit", out(sg), re.I) is not None, out(sg))
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
    sg = sign_raise(df, d2)
    tok = token_of(sg)
    rb = apply_raise(d2, tok or MADE_UP_TOKEN)
    check("[CP4-T13] (b) the non-enrolled lane refuses a director-signed raise token", rb.returncode != 0, out(rb))
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
    sg = sign_choice(df, d, "A")
    tok = token_of(sg)
    check("[CP4-T14] director-sign choice A prints a token", sg.returncode == 0 and bool(tok), out(sg))
    r = apply_choice(d, "A", tok)
    check("[CP4-T14] choice A --director-token succeeds with no chat reply", r.returncode == 0, out(r))
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
    tok = token_of(sign_choice(df, d, "B"))
    r = apply_choice(d, "B", tok)
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
    tok = token_of(sign_choice(df, d, "B"))
    rc = apply_choice(d, "B", tok)
    check("[CP4-T16] the signed pick of B is recorded", rc.returncode == 0, out(rc))
    allow = state(d).get("choice_allow") or {}
    check("[CP4-T16] choice_allow records B with unblock true", allow.get("letter") == "B" and allow.get("unblock") is True, allow)
    check("[CP4-T16] the run is still blocked until human-approve", state(d).get("phase") == "blocked", state(d).get("phase"))
    tok_h = token_of(director(df, d, "director-sign", "human-approve"))
    rh = rx(d, "human-approve", "--director-token", tok_h or MADE_UP_TOKEN)
    check("[CP4-T16] the director-signed human-approve resumes the run", rh.returncode == 0 and unblocked_phase(d), out(rh))
    after = adv(d)
    check("[CP4-T16] CP1's attempts count is unchanged", after.get("attempts") == before.get("attempts"), (before, after))
    check("[CP4-T16] cap_raised_to is unchanged", after.get("cap_raised_to") == before.get("cap_raised_to"), (before, after))
    r5 = tl._reviewers_after_triage(d, "CP1")
    check("[CP4-T16] `reviewers CP1` refuses to open another round", r5.returncode != 0, out(r5))


def t17_choice_token_for_another_letter_refused():
    d, df = building_lane("t17")
    ask_choice(d, "--locked", f"A={PROTO}")
    tok = token_of(sign_choice(df, d, "A"))
    check("[CP4-T17] setup: a token for A was signed", bool(tok), "")
    rb = apply_choice(d, "B", tok or MADE_UP_TOKEN)
    rc = apply_choice(d, "C", tok or MADE_UP_TOKEN)
    check("[CP4-T17] the token for A is refused as `choice B`", rb.returncode != 0, out(rb))
    check("[CP4-T17] the token for A is refused as `choice C`", rc.returncode != 0, out(rc))
    check("[CP4-T17] nothing is recorded: no choice_made, no choice_allow, question still pending",
          not events(d, "choice_made") and "choice_allow" not in state(d) and pending(d).get("action") == "choice",
          (events(d, "choice_made"), state(d).get("choice_allow"), pending(d)))
    ra = apply_choice(d, "A", tok or MADE_UP_TOKEN)
    check("[CP4-T17] the same token with `choice A` then succeeds", ra.returncode == 0
          and [e.get("letter") for e in events(d, "choice_made")] == ["A"], out(ra))


def t18_pick_not_offered_or_no_question_cannot_be_signed():
    da, df = building_lane("t18a")
    ask_choice(da)
    sa = sign_choice(df, da, "D")
    check("[CP4-T18] (a) director-sign choice D is refused", sa.returncode != 0 and not token_of(sa), out(sa))
    check("[CP4-T18] (a) the refusal names the offered letters", re.search(r"A,\s?B,\s?C", out(sa)) is not None, out(sa))
    check("[CP4-T18] (a) no choice_allow and the question is still pending",
          "choice_allow" not in state(da) and pending(da).get("action") == "choice", pending(da))
    db, _ = building_lane("t18b", dfolder=df)
    sb = sign_choice(df, db, "A")
    tok = token_of(sb)
    rb = apply_choice(db, "A", tok) if tok else None
    refused = sb if sb.returncode != 0 else rb
    check("[CP4-T18] (b) with no question waiting, signing or applying is refused",
          refused is not None and refused.returncode != 0, out(sb) + (out(rb) if rb else ""))
    check("[CP4-T18] (b) the message says to run `ask choice` first",
          refused is not None and "ask choice" in out(refused), out(refused) if refused else "")
    check("[CP4-T18] (b) nothing is recorded in that lane",
          not events(db, "choice_made") and "choice_allow" not in state(db) and not pending(db),
          (events(db, "choice_made"), state(db).get("choice_allow"), pending(db)))


def t19_choice_token_from_older_question_or_used_refused():
    d, df = building_lane("t19")
    ask_choice(d)
    old = token_of(sign_choice(df, d, "A"))
    check("[CP4-T19] setup: a token for the first question was signed", bool(old), "")
    r_re = ask_choice(d)
    check("[CP4-T19] setup: the question was asked again (a new question)", r_re.returncode == 0, out(r_re))
    r_old = apply_choice(d, "A", old or MADE_UP_TOKEN)
    check("[CP4-T19] the stale token is refused", r_old.returncode != 0, out(r_old))
    check("[CP4-T19] the new question stays pending", pending(d).get("action") == "choice", pending(d))
    fresh = token_of(sign_choice(df, d, "A"))
    r_fresh = apply_choice(d, "A", fresh or MADE_UP_TOKEN)
    check("[CP4-T19] a freshly signed token works", r_fresh.returncode == 0, out(r_fresh))
    r_replay = apply_choice(d, "A", fresh or MADE_UP_TOKEN)
    check("[CP4-T19] the replay of the used token is refused (no question waiting)",
          r_replay.returncode != 0 and not pending(d), out(r_replay))
    check("[CP4-T19] the recorded pick is unchanged: exactly one choice_made, letter A",
          [e.get("letter") for e in events(d, "choice_made")] == ["A"], events(d, "choice_made"))


def t20_choice_token_from_another_lane_refused():
    df = director_folder()
    da, _ = building_lane("t20a", dfolder=df)
    db, _ = building_lane("t20b", dfolder=df)
    rx(da, "ask", "choice", "--options", "A,B")
    rx(db, "ask", "choice", "--options", "A,B")
    tok_a = token_of(sign_choice(df, da, "A"))
    check("[CP4-T20] setup: lane A's token was signed", bool(tok_a), "")
    rb = apply_choice(db, "A", tok_a or MADE_UP_TOKEN)
    check("[CP4-T20] lane B refuses lane A's token", rb.returncode != 0, out(rb))
    check("[CP4-T20] nothing recorded in lane B, its question still pending",
          not events(db, "choice_made") and "choice_allow" not in state(db) and pending(db).get("action") == "choice",
          (events(db, "choice_made"), pending(db)))
    ra = apply_choice(da, "A", tok_a or MADE_UP_TOKEN)
    check("[CP4-T20] lane A can still use its own token", ra.returncode == 0
          and [e.get("letter") for e in events(da, "choice_made")] == ["A"], out(ra))


def t21_builder_cannot_pick_and_chat_reply_picks_nothing():
    d, df = building_lane("t21")
    ask_choice(d)
    sa = not_director(df, d, "director-sign", "choice", "A")
    check("[CP4-T21] director-sign choice from a non-director session is refused with no token",
          sa.returncode != 0 and not sa.stdout.strip(), out(sa))
    rm = apply_choice(d, "A", MADE_UP_TOKEN)
    check("[CP4-T21] a made-up token is refused", rm.returncode != 0, out(rm))
    say(d, "A")
    rc = rx(d, "choice", "A")
    check("[CP4-T21] a chat reply plus a tokenless `choice A` is refused", rc.returncode != 0, out(rc))
    check("[CP4-T21] the refusal names the director token", DIRECTOR_TOKEN_RE.search(out(rc)) is not None, out(rc))
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
    check(f"[CP4-T22] {label}: names `director-sign raise-limit`", "director-sign raise-limit" in sec, sec[:300])
    check(f"[CP4-T22] {label}: the raise-limit signing takes --reason", "--reason" in sec, "")
    check(f"[CP4-T22] {label}: names `director-sign choice`", "director-sign choice" in sec, "")
    check(f"[CP4-T22] {label}: says `ask` runs before signing", re.search(r"`ask\b|\bask (raise-limit|choice)\b", sec) is not None, "")
    check(f"[CP4-T22] {label}: says a token signed before a re-ask is stale", re.search(r"\bstale\b", sec, re.I) is not None, "")
    check(f"[CP4-T22] {label}: still lists approve-plan", "approve-plan" in sec, "")
    check(f"[CP4-T22] {label}: still lists accept-finding", "accept-finding" in sec, "")


def t22_docs_list_both_actions():
    readme = (ROOT / "README.md").read_text()
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
    check("[CP4-T23] a chat reply plus a tokenless `choice A` is refused naming the director token",
          rc.returncode != 0 and DIRECTOR_TOKEN_RE.search(out(rc)) is not None, out(rc))
    check("[CP4-T23] nothing is recorded and the question is still pending",
          not events(d, "choice_made") and "choice_allow" not in state(d) and pending(d).get("action") == "choice",
          (events(d, "choice_made"), pending(d)))
    tok = token_of(sign_choice(df, d, "B"))
    rb = apply_choice(d, "B", tok or MADE_UP_TOKEN)
    check("[CP4-T23] a director token for B is then applied and recorded",
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
