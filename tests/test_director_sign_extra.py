#!/usr/bin/env python3
"""
CP4 fix-round tests (round 1): the holes the reviewers found around director-signed raise-limit and choice.
    python3 tests/test_director_sign_extra.py

Not a planned file: tests/test_director_sign_more.py is the planned, frozen one; this file reuses its helpers.
Cases: a raise token signed for reason R1 is refused with --reason R2 (and stays usable with R1); a director-signed
letter at a block never lets a tokenless `human-approve` resume the run or reset the run-wide review budget; a
tokenless `raise-limit` in an enrolled lane names the missing director token first (before --reason); the
signing hint in that refusal is the whole command that signs; `director-sign choice` says what the letter does;
and the prompt hook tells an enrolled lane that a chat letter picks nothing.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set.
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
import test_gates as g                   # noqa: E402
import test_limits as tl                 # noqa: E402
import test_accept_finding as taf        # noqa: E402
import trust_helpers as th               # noqa: E402
import test_director_sign_more as m      # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP4-F\d+[a-z]?)\]")


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


def f01_raise_token_bound_to_its_reason():
    d, df = m.enrolled_cap_lane("f01")
    g.rx(d, "ask", "raise-limit", "CP1")
    before = m.learnings(d)
    tok = m.token_of(m.sign_raise(df, d, reason="R1: the first reason"))
    check("[CP4-F01] setup: a raise token was signed for reason R1", bool(tok), "")
    r2 = m.apply_raise(d, tok, reason="R2: another reason")
    check("[CP4-F01] the token applied with a different --reason is refused", r2.returncode != 0, m.out(r2))
    check("[CP4-F01] the refusal says the token was signed for a different step, reason or letter",
          "different step, reason or letter" in m.out(r2), m.out(r2))
    check("[CP4-F01] cap_raised_to stays unset and the run stays blocked",
          m.adv(d).get("cap_raised_to") is None and m.state(d).get("phase") == "blocked", m.adv(d))
    check("[CP4-F01] the question is still pending and learnings.md is unchanged",
          m.pending(d).get("action") == "raise-limit" and m.learnings(d) == before, m.pending(d))
    r1 = m.apply_raise(d, tok, reason="R1: the first reason")
    check("[CP4-F01] the same token with the signed reason R1 then succeeds",
          r1.returncode == 0 and m.adv(d).get("cap_raised_to") == 3, m.out(r1))
    check("[CP4-F01] learnings.md holds R1 only",
          "R1: the first reason" in m.learnings(d) and "R2" not in m.learnings(d), m.learnings(d)[-300:])


def f02_signed_letter_never_resumes_or_resets_budget():
    # the run-wide cap (max_adversarial_rounds_run=1): the exact case the reviewer reproduced
    d, env, tp = taf.run_wide_cap_project("cp4f02")
    th.cp3_pass_behavior(d, env, "CP1")
    rnd = th.cp3_open_round(d, env, "CP1")
    rid = th.cp3_R0(rnd)
    path = taf.write_round_review(d, env, rnd, rid, [taf.F1, taf.F2])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    th.rx(d, env, "triage", "CP1", f"{rid}/F1=fix:known tradeoff", f"{rid}/F2=fix:known tradeoff")
    th.rx(d, env, "reviewers", "CP1", "--json")
    st0 = th.cp3_state(d)
    check("[CP4-F02] setup: blocked on the run-wide cap", r.returncode == 2 and st0.get("phase") == "blocked"
          and str(st0.get("blocked_reason", "")).startswith("Run-wide"), st0.get("blocked_reason"))
    total_before = st0.get("adv_rounds_total")
    df = m.director_folder("cp4f02")
    m.enroll(d, df, env=env)
    r_ask = th.rx(d, env, "ask", "choice", "--options", "A,B")
    check("[CP4-F02] setup: ask choice runs in the enrolled blocked lane", r_ask.returncode == 0, m.out(r_ask))
    sg = m.director(df, d, "director-sign", "choice", "A", env=env)
    tok = m.token_of(sg)
    check("[CP4-F02] director-sign choice says the run is blocked and that resuming needs director-sign human-approve",
          "blocked" in sg.stderr and "director-sign human-approve" in sg.stderr, m.out(sg))
    rc = th.rx(d, env, "choice", "A", "--director-token", tok or m.MADE_UP_TOKEN)
    check("[CP4-F02] the signed pick of A is recorded", rc.returncode == 0, m.out(rc))
    rh = th.rx(d, env, "human-approve")
    check("[CP4-F02] a TOKENLESS human-approve is refused, naming the director token",
          rh.returncode != 0 and "director-enrolled" in m.out(rh) and "director-sign human-approve" in m.out(rh), m.out(rh))
    st1 = th.cp3_state(d)
    check("[CP4-F02] the run is still blocked", st1.get("phase") == "blocked", st1.get("phase"))
    check("[CP4-F02] adv_rounds_total is exactly what it was (not reset)",
          isinstance(total_before, int) and total_before > 0 and st1.get("adv_rounds_total") == total_before,
          (total_before, st1.get("adv_rounds_total")))
    check("[CP4-F02] the pick itself is kept (choice_allow A, unblock true, by director)",
          (st1.get("choice_allow") or {}).get("letter") == "A" and (st1.get("choice_allow") or {}).get("by") == "director",
          st1.get("choice_allow"))
    tok_h = m.token_of(m.director(df, d, "director-sign", "human-approve", env=env))
    rh2 = th.rx(d, env, "human-approve", "--director-token", tok_h or m.MADE_UP_TOKEN)
    check("[CP4-F02] a director-signed human-approve then resumes the run", rh2.returncode == 0
          and th.cp3_state(d).get("phase") != "blocked", m.out(rh2))


def f03_tokenless_raise_names_the_token_first():
    d, df = m.enrolled_cap_lane("f03")
    g.rx(d, "ask", "raise-limit", "CP1")
    g.say(d, "raise: x")
    rc = g.rx(d, "raise-limit", "CP1")  # no token, no --reason
    check("[CP4-F03] a tokenless raise-limit in an enrolled lane is refused", rc.returncode != 0, m.out(rc))
    check("[CP4-F03] the first refusal names the enrolled lane and the director token, not --reason",
          "director-enrolled" in m.out(rc) and "director token" in m.out(rc) and "needs --reason" not in m.out(rc), m.out(rc))
    check("[CP4-F03] the signing hint is the whole command: director-sign raise-limit CP1 --reason",
          re.search(r'director-sign raise-limit CP1 --reason "<why>"', m.out(rc)) is not None, m.out(rc))
    rc2 = g.rx(d, "raise-limit", "CP1", "--reason", "x")  # no token, with --reason: same cause
    check("[CP4-F03] with --reason but no token the cause is still the director token",
          rc2.returncode != 0 and "director-enrolled" in m.out(rc2), m.out(rc2))
    check("[CP4-F03] nothing changed", m.adv(d).get("cap_raised_to") is None and m.state(d).get("phase") == "blocked"
          and m.pending(d).get("action") == "raise-limit", m.adv(d))


def f04_choice_hint_and_signing_note():
    d, df = m.building_lane("f04")
    m.ask_choice(d, "--locked", f"A={m.PROTO}")
    g.say(d, "A")
    rc = g.rx(d, "choice", "A")
    check("[CP4-F04] a tokenless choice in an enrolled lane names a whole signing command with the offered letters",
          rc.returncode != 0 and "director-sign choice <one of A, B, C>" in m.out(rc), m.out(rc))
    rc0 = g.rx(d, "choice")  # no letter at all: no dangling command either
    check("[CP4-F04] with no letter typed the hint is still whole (no trailing empty letter)",
          rc0.returncode != 0 and "director-sign choice <one of A, B, C>" in m.out(rc0), m.out(rc0))
    sa = m.sign_choice(df, d, "A")
    check("[CP4-F04] director-sign choice A says it approves changing the locked prototype",
          sa.returncode == 0 and m.PROTO in sa.stderr and "approves changing" in sa.stderr, m.out(sa))
    check("[CP4-F04] stdout is still only the token", len(sa.stdout.strip().splitlines()) == 1, sa.stdout)
    sb = m.sign_choice(df, d, "B")
    check("[CP4-F04] director-sign choice B says it unlocks nothing", "unlocks nothing" in sb.stderr, m.out(sb))
    d2, df2 = m.building_lane("f04g", proto=False)
    g.rx(d2, "ask", "choice", "--options", "A,B", "--guardrail", "B=magic-number")
    sg = m.sign_choice(df2, d2, "B")
    check("[CP4-F04] director-sign choice names the guardrail kind the letter adds",
          sg.returncode == 0 and "guardrail step for 'magic-number'" in sg.stderr, m.out(sg))


def f05_prompt_hook_is_director_aware():
    d, df = m.building_lane("f05", proto=False)
    g.rx(d, "ask", "choice", "--options", "A,B")
    hp = Path(d) / ".ratchet/human_log.jsonl"
    n_before = len(hp.read_text().splitlines()) if hp.exists() else 0
    r = g.say(d, "A")  # the prompt hook's reply to the agent is its stdout/stderr
    text = m.out(r)
    check("[CP4-F05] the prompt hook says a chat letter picks nothing in an enrolled lane",
          "picks nothing" in text and "director-sign choice <letter>" in text, text[:400])
    check("[CP4-F05] the hook does not point at a bare `choice` command", "choice` to record" not in text, text[:400])
    n_after = len(hp.read_text().splitlines()) if hp.exists() else 0
    check("[CP4-F05] the message is still recorded (the hook never blocks the user)", n_after == n_before + 1,
          (n_before, n_after))
    check("[CP4-F05] the question is still pending", m.pending(d).get("action") == "choice", m.pending(d))


TESTS = [
    ("CP4-F01", f01_raise_token_bound_to_its_reason),
    ("CP4-F02", f02_signed_letter_never_resumes_or_resets_budget),
    ("CP4-F03", f03_tokenless_raise_names_the_token_first),
    ("CP4-F04", f04_choice_hint_and_signing_note),
    ("CP4-F05", f05_prompt_hook_is_director_aware),
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
        except Exception as e:
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
