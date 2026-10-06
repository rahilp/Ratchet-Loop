#!/usr/bin/env python3
"""
CP5 of the trust-holes run, part a: approvals answer the question Ratchet asked.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_a.py
"""
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

import trust_helpers
from trust_helpers import (
    ENGINE, check, cp3_state, cp4_awaiting_review, cp5_awaiting_checkpoint_review, cp5_plan, cp5_say,
    cp5x_frozen_ui_project, cp5x_hint, cp5x_history, make_env, project, rx,
)

SECTIONS = []


# ---- CP5 ----
# Approvals answer the question Ratchet asked: a plan or step approval only counts when the user's FIRST reply
# after Ratchet printed that exact question is one of the listed yes replies (DECIDED: yes / yes, go ahead /
# go ahead / approved - case and trailing punctuation ignored, nothing else attached). CP4 landed: the human
# override now needs a real terminal and writes an audit line, so nothing here relies on RATCHET_HUMAN_OVERRIDE=1
# without a pty; director-signed approvals (which need no chat reply at all) are used instead where a fixture
# needs an approval without driving the new ask-first flow.

CP5_YES_REPLIES = ["yes", "yes, go ahead", "go ahead", "approved"]










def cp5_ask_prints_listed_yes_replies():
    print("CP5: asking for plan approval prints the question and the exact replies that count as yes")

    # --- T01: `ask approve-plan` prints the question and every listed yes reply, and approves nothing yet -----------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    r = rx(d, env, "ask", "approve-plan")
    out = r.stdout
    present = [p for p in CP5_YES_REPLIES if re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", out, re.I)]
    check("[CP5-T01] `ask approve-plan` succeeds and prints an ASK THE USER line naming the plan approval",
          r.returncode == 0 and out.strip().startswith("ASK THE USER") and re.search(r"plan", out, re.I) is not None,
          (r.returncode, out, r.stderr))
    check("[CP5-T01] the printed question lists every one of the exact replies that count as yes",
          present == CP5_YES_REPLIES, (present, out))
    check("[CP5-T01] asking the question alone approves nothing: the plan is still awaiting approval",
          cp3_state(d)["phase"] == "awaiting_plan_approval", cp3_state(d)["phase"])


SECTIONS.append(("CP5", cp5_ask_prints_listed_yes_replies))


def cp5_listed_yes_after_question_approves():
    print("CP5: a listed yes sent after the question approves the plan")

    # --- T02: ask approve-plan, then a listed yes, then approve-plan succeeds and the run leaves awaiting_plan_approval
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")  # the question this case assumes was just printed
    cp5_say(d, tp, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    check("[CP5-T02] approve-plan exits 0 once a listed yes follows the printed question",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))
    check("[CP5-T02] the run leaves awaiting_plan_approval and moves to building",
          phase == "building", phase)


SECTIONS.append(("CP5", cp5_listed_yes_after_question_approves))


def cp5_yes_before_question_does_not_approve():
    print("CP5: a yes typed before the question was printed does not approve the plan")

    # --- T03: the user already said yes in chat; the agent then asks afterward; no further reply follows ------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    cp5_say(d, tp, env, "yes, build it")
    rx(d, env, "ask", "approve-plan")  # printed only after the user's earlier yes
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    check("[CP5-T03] approve-plan is refused: the user's yes predates the printed question",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    check("[CP5-T03] the plan is still awaiting approval", phase == "awaiting_plan_approval", phase)


SECTIONS.append(("CP5", cp5_yes_before_question_does_not_approve))


def cp5_yes_to_unrelated_chat_question_does_not_approve():
    print("CP5: a yes to an unrelated question the agent asked does not approve the plan")

    # --- T04: ask approve-plan, then ordinary chat text answering a DIFFERENT, unregistered question -----------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "sure thing")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    msg = r.stderr + r.stdout
    check("[CP5-T04] approve-plan is refused: the first reply after the question was not a clear, listed yes",
          r.returncode != 0 and re.search(r"not (a )?(clear|listed)( yes)?|not one of", msg, re.I) is not None,
          (r.returncode, msg))
    check("[CP5-T04] the refusal tells the agent to print the question again",
          re.search(r"ask (them )?again|print the question again|ask .*again", msg, re.I) is not None, msg)
    check("[CP5-T04] the plan stays unapproved", phase == "awaiting_plan_approval", phase)


SECTIONS.append(("CP5", cp5_yes_to_unrelated_chat_question_does_not_approve))


def cp5_yes_to_different_pending_question_does_not_approve():
    print("CP5: a yes to a different pending Ratchet question does not approve the plan")

    # --- T05: ask approve-plan, then ask set-review (a different registered question) before any reply --------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    r2 = rx(d, env, "ask", "set-review", "each")
    assert r2.returncode == 0, r2.stderr + r2.stdout
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    msg = r.stderr + r.stdout
    check("[CP5-T05] approve-plan is refused and names the question that is actually pending (set-review)",
          r.returncode != 0 and "set-review" in msg, (r.returncode, msg))
    check("[CP5-T05] the plan's approval state did not change", phase == "awaiting_plan_approval", phase)


SECTIONS.append(("CP5", cp5_yes_to_different_pending_question_does_not_approve))


def cp5_non_yes_first_reply_spends_question():
    print("CP5: a non-yes first reply spends the question; a later yes does not retroactively approve")

    # --- T06: ask approve-plan, "ok" (not listed) first, then a listed yes: the FIRST reply is the one that counts --
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "ok")
    cp5_say(d, tp, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    phase = cp3_state(d)["phase"]
    check("[CP5-T06] approve-plan is refused: the FIRST reply after the question ('ok') was not a listed yes",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    check("[CP5-T06] the plan is still unapproved despite the later, stray listed yes",
          phase == "awaiting_plan_approval", phase)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "approved")
    r3 = rx(d, env, "approve-plan")
    check("[CP5-T06] once the question is printed again and the next FIRST reply is a listed yes, it succeeds",
          r3.returncode == 0 and cp3_state(d)["phase"] == "building",
          (r3.returncode, r3.stderr, cp3_state(d)["phase"]))


SECTIONS.append(("CP5", cp5_non_yes_first_reply_spends_question))


def cp5_human_approve_requires_question_too():
    print("CP5: step review (human-approve) also requires the printed question first")

    # --- T07: human-approve without an `ask human-approve` first is refused; after ask + a listed yes it succeeds ---
    env = make_env("claude")
    d, tp = cp5_awaiting_checkpoint_review(env)
    phase_before = cp3_state(d)["phase"]
    cp5_say(d, tp, env, "yes, go ahead")  # a listed yes is already in the log, but the question was never asked
    r = rx(d, env, "human-approve")
    check("[CP5-T07] human-approve without `ask human-approve` first is refused, nothing approved",
          r.returncode != 0 and cp3_state(d)["phase"] == phase_before, (r.returncode, r.stdout, r.stderr))
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "yes, go ahead")
    r3 = rx(d, env, "human-approve")
    check("[CP5-T07] after `ask human-approve` and a listed yes, human-approve succeeds",
          r3.returncode == 0, (r3.returncode, r3.stderr, r3.stdout))
    gate = cp3_state(d)["checkpoints"]["CP1"]["gates"]["human"]
    check("[CP5-T07] the checkpoint's human gate is marked passed",
          gate.get("status") == "passed", gate)


SECTIONS.append(("CP5", cp5_human_approve_requires_question_too))


def cp5_pre_upgrade_run_can_continue():
    print("CP5: a pre-upgrade run already waiting for approval can carry on once the question is printed once more")

    # --- T08: a run submitted before this change (awaiting_plan_approval, awaiting_since set, no `pending` entry) ----
    env = make_env("claude")
    d, tp = cp5_plan(env)
    st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    check("[CP5-T08] precondition: a freshly submitted plan has no `pending` entry yet (models a pre-upgrade run)",
          "pending" not in st and st["phase"] == "awaiting_plan_approval" and bool(st.get("awaiting_since")), st)
    cp5_say(d, tp, env, "yes")  # a human message already sitting in the log, from before the question was ever printed
    r = rx(d, env, "approve-plan")
    check("[CP5-T08] the stale pre-upgrade message alone does not approve the plan",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stdout, r.stderr))
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "approved")
    r3 = rx(d, env, "approve-plan")
    check("[CP5-T08] once the question is printed fresh and a listed yes follows, approve-plan succeeds",
          r3.returncode == 0 and cp3_state(d)["phase"] == "building",
          (r3.returncode, r3.stderr, cp3_state(d)["phase"]))


SECTIONS.append(("CP5", cp5_pre_upgrade_run_can_continue))


def cp5_director_signed_approval_skips_chat_question():
    print("CP5: a director-signed approval still works without any chat question being printed")

    # --- T09: a director token approves the plan with no `ask` step and no chat reply at all -------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    denv = dict(env, RATCHET_ROLE="director")
    rk = rx(d, denv, "director-key")
    en = rx(d, denv, "director-enroll")
    sg = rx(d, denv, "director-sign", "approve-plan")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    assert rk.returncode == 0 and en.returncode == 0 and sg.returncode == 0 and tok, (rk.stderr, en.stderr, sg.stderr)
    st_before = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    check("[CP5-T09] precondition: no question was ever asked and no chat reply was ever recorded for this plan",
          "pending" not in st_before, st_before)
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP5-T09] the director-signed token approves the plan with no ask step and no chat reply required",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr, cp3_state(d)["phase"]))


SECTIONS.append(("CP5", cp5_director_signed_approval_skips_chat_question))


def cp5_feedback_still_accepts_freeform_text():
    print("CP5: feedback at a review point still accepts freeform text, not just a listed yes")

    # --- T10: ordinary feedback text, not one of the approval phrases, is still recorded as feedback -----------------
    env = make_env("claude")
    d = cp4_awaiting_review(env)
    assert cp3_state(d)["phase"] == "awaiting_final_review", cp3_state(d)["phase"]
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "actually, can the button be blue?")
    r = rx(d, env, "feedback", "--text", "blue button")
    check("[CP5-T10] feedback is recorded from ordinary text; CP5's yes/no allow-list does not block it",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))


SECTIONS.append(("CP5", cp5_feedback_still_accepts_freeform_text))




# ---- CP5 adversarial round 1 ----
# Fixes for CP5's first adversarial round: one question at a time (a new `ask` never lets one reply answer two
# questions), replies only count from the question's own chat, feedback is bound to the reply that answered the
# review question, the deliberate narrowing of cancel / set-review / approve-changes / claim / ui-respec to the
# listed yes replies, the phase rule, the prompt hook's guidance, and the docs that describe all of it.





def cp5x_gate_blocks_run(d, env, why="a gate needs a human decision"):
    """Block the run the way a failing gate does (the engine's own _set_blocked + save_state), without a human."""
    code = ("import importlib.util, sys\nfrom pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('r', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\nctx = m.Ctx(Path(sys.argv[2])); st = ctx.state()\n"
            "m._set_blocked(st, sys.argv[3]); ctx.save_state(st)\n")
    r = subprocess.run([sys.executable, "-c", code, str(ENGINE), str(d), why], cwd=d, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout




def cp5_adv_round1_one_question_at_a_time():
    print("CP5 adv round 1: a new question never lets one reply answer two questions")

    # --- X01: plan approval can't be asked over a different question still waiting for its first reply ----------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "set-review", "each")
    r = rx(d, env, "ask", "approve-plan")
    pend = cp3_state(d).get("pending") or {}
    check("[CP5-X01] `ask approve-plan` is refused while the set-review question waits, and says to answer it or "
          "`ask withdraw` first", r.returncode != 0 and "different question" in r.stderr and "ask withdraw" in r.stderr
          and "ASK THE USER" not in r.stdout, (r.returncode, r.stdout, r.stderr))
    check("[CP5-X01] the waiting set-review question is left as it was", pend.get("action") == "set-review", pend)
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    check("[CP5-X01] the user's yes (to the set-review question) does not approve the plan",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X02: any other question asked over a waiting one is NOT ASKED, logged, and no reply approves it ---------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    r = rx(d, env, "ask", "cancel")
    over = cp5x_history(d, "question_overtaken")
    check("[CP5-X02] `ask cancel` over the waiting plan question prints NOT ASKED, not a question for the user",
          r.returncode == 0 and r.stdout.strip().startswith("NOT ASKED") and "ASK THE USER" not in r.stdout, r.stdout)
    check("[CP5-X02] the replaced plan question is logged as question_overtaken (audit trail)",
          bool(over) and over[-1].get("action") == "approve-plan" and over[-1].get("by") == "cancel", over)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X02] the prompt hook says this reply approves nothing", "approves nothing" in hint, hint)
    r = rx(d, env, "cancel")
    check("[CP5-X02] the user's yes does not cancel the run (it may have been meant for the plan)",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))
    r = rx(d, env, "approve-plan")
    check("[CP5-X02] ... and it does not approve the plan either",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X03: a question that already had its first reply can be followed by a new one; withdraw is logged -------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "set-review", "each")
    cp5_say(d, tp, env, "no thanks, keep it as it is")
    r = rx(d, env, "ask", "approve-plan")
    over = cp5x_history(d, "question_overtaken")
    check("[CP5-X03] once the waiting question had its reply, `ask approve-plan` asks normally",
          r.returncode == 0 and "ASK THE USER" in r.stdout, (r.returncode, r.stdout, r.stderr))
    check("[CP5-X03] the replaced, already-answered question is still logged",
          bool(over) and over[-1].get("action") == "set-review" and over[-1].get("answered") is True, over)
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    check("[CP5-X03] a listed yes after it approves the plan", r.returncode == 0, (r.returncode, r.stderr))
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "set-review", "each")
    r = rx(d, env, "ask", "withdraw")
    wd = cp5x_history(d, "question_withdrawn")
    check("[CP5-X03] `ask withdraw` is logged as question_withdrawn",
          r.returncode == 0 and bool(wd) and wd[-1].get("action") == "set-review", (r.stdout, wd))


SECTIONS.append(("CP5", cp5_adv_round1_one_question_at_a_time))


def cp5_adv_round1_replies_from_this_chat_only():
    print("CP5 adv round 1: only a reply in the question's own chat answers it")

    # --- X04: a yes in another chat neither answers nor spends the plan question, nor changes the owner ----------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes", session="s-other")
    cp5_say(d, tp, env, "wait, change CP1 first")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X04] another chat's yes doesn't count: the owner chat's first reply (not a yes) decides",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and "wait, change CP1" in r.stderr,
          (r.returncode, r.stderr))
    check("[CP5-X04] the run still belongs to the chat it was started in", st.get("owner_session") == "s-owner", st)

    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "approve-plan")
    check("[CP5-X04] a yes typed only in another chat approves nothing",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval" and "another chat" in r.stderr,
          (r.returncode, r.stderr))
    check("[CP5-X04] ... and does not spend the question",
          (cp3_state(d).get("pending") or {}).get("action") == "approve-plan", cp3_state(d).get("pending"))
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X04] the owner chat's own yes then approves, and the owner is unchanged",
          r.returncode == 0 and st["phase"] == "building" and st.get("owner_session") == "s-owner",
          (r.returncode, r.stderr, st.get("owner_session")))

    # --- X05: `resume --claim` is answered only in the chat that asked to take the run over ----------------------
    env = make_env("claude")
    d = project(env)
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    rx(d, env, "ask", "claim")
    cp5_say(d, tp, env, "yes")  # typed in the owner chat, answering something else there
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X05] a yes in the owner chat does not answer the claim question asked in the other chat",
          r.returncode != 0 and cp3_state(d).get("owner_session") == "s-owner", (r.returncode, r.stderr))
    cp5_say(d, tp, env, "sounds good", session="s-other")
    cp5_say(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X05] claim: 'sounds good' (formerly accepted) as the first reply is refused, and the later yes "
          "does not count", r.returncode != 0 and "sounds good" in r.stderr
          and cp3_state(d).get("owner_session") == "s-owner", (r.returncode, r.stderr))
    cp5_say(d, tp, env, "please continue it here", session="s-other")
    rx(d, env, "ask", "claim")
    hint = cp5x_hint(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X05] the claim chat's prompt hook treats its reply as the answer to the claim question",
          "listed yes" in hint, hint)
    check("[CP5-X05] a listed yes in the claiming chat moves the run there (resume --claim is the only way)",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-other", (r.returncode, r.stderr))


SECTIONS.append(("CP5", cp5_adv_round1_replies_from_this_chat_only))


def cp5_adv_round1_first_listed_reply_only():
    print("CP5 adv round 1: cancel, set-review, approve-changes and ui-respec take only a listed yes, first reply only")
    # Deliberate narrowing (CP5 DECIDED): these used to accept any approving phrase ("sounds good"); now only the
    # first reply after the question counts, and only a listed yes. Each case: "sounds good" first, then "yes".

    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "cancel")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "cancel")
    check("[CP5-X06] cancel: 'sounds good' first is refused and the later yes does not count",
          r.returncode != 0 and "sounds good" in r.stderr and cp3_state(d)["phase"] == "awaiting_plan_approval",
          (r.returncode, r.stderr))

    rx(d, env, "ask", "set-review", "each")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "set-review", "each")
    check("[CP5-X06] set-review: 'sounds good' first is refused and the later yes does not count",
          r.returncode != 0 and "sounds good" in r.stderr and cp3_state(d).get("human_review") != "each",
          (r.returncode, r.stderr, cp3_state(d).get("human_review")))

    env = make_env("claude")
    d = project(env)
    tp = d / "t.jsonl"
    cpj = d / ".ratchet/runs/f/checkpoints.json"
    plan = json.loads(cpj.read_text())
    plan["checkpoints"][0]["summary"] = "changed after approval"
    cpj.write_text(json.dumps(plan))
    snap = cp3_state(d).get("approved")
    q = rx(d, env, "ask", "approve-changes")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-changes")
    check("[CP5-X06] approve-changes: 'sounds good' first is refused and the later yes does not count",
          "ASK THE USER" in q.stdout and r.returncode != 0 and "sounds good" in r.stderr
          and cp3_state(d).get("approved") == snap, (q.stdout, r.returncode, r.stderr))

    env = make_env("claude")
    d = cp5x_frozen_ui_project(env)
    tp = d / "t.jsonl"
    rx(d, env, "ask", "ui-respec", "CP1")
    cp5_say(d, tp, env, "sounds good")
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "ui-respec", "CP1")
    check("[CP5-X06] ui-respec: 'sounds good' first is refused and the later yes does not count",
          r.returncode != 0 and "sounds good" in r.stderr
          and cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"].get("frozen"), (r.returncode, r.stderr))


SECTIONS.append(("CP5", cp5_adv_round1_first_listed_reply_only))


def cp5_adv_round1_reply_variants_and_phase():
    print("CP5 adv round 1: which replies count as yes, and a question only counts at the pause it was asked at")

    # --- X07: reply variants through human-approve (case and trailing . or ! ignored; nothing else attached) -----
    env = make_env("claude")
    d, tp = cp5_awaiting_checkpoint_review(env)
    for txt in ["yes, but change the header", "yes?", "ok", "sounds good", "lgtm", "yes please"]:
        rx(d, env, "ask", "human-approve")
        cp5_say(d, tp, env, txt)
        r = rx(d, env, "human-approve")
        check(f"[CP5-X07] {txt!r} is not a listed yes: human-approve is refused",
              r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_checkpoint_review", (r.returncode, r.stderr))
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "Yes!")
    r = rx(d, env, "human-approve")
    st = cp3_state(d)
    check("[CP5-X07] 'Yes!' is a listed yes (case and trailing ! ignored): human-approve succeeds",
          r.returncode == 0 and st["checkpoints"]["CP1"]["gates"]["human"].get("status") == "passed",
          (r.returncode, r.stderr))
    check("[CP5-X07] human-approve closes the question it answered", "pending" not in st, st.get("pending"))
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "APPROVED.")
    r = rx(d, env, "approve-plan")
    check("[CP5-X07] 'APPROVED.' is a listed yes (case and trailing . ignored): approve-plan succeeds",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr))

    # --- X08: a review question asked at one pause no longer applies once the run moved to another --------------
    env = make_env("claude")
    d, tp = cp5_awaiting_checkpoint_review(env)
    rx(d, env, "ask", "human-approve")
    cp5x_gate_blocks_run(d, env)
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "human-approve")
    st = cp3_state(d)
    check("[CP5-X08] the yes to a question asked at the step review does not unblock the run",
          r.returncode != 0 and "no longer applies" in r.stderr and st["phase"] == "blocked", (r.returncode, r.stderr))
    check("[CP5-X08] the question is closed and logged as question_not_approved",
          "pending" not in st and bool(cp5x_history(d, "question_not_approved")), st.get("pending"))


SECTIONS.append(("CP5", cp5_adv_round1_reply_variants_and_phase))


def cp5_adv_round1_resubmit_and_feedback():
    print("CP5 adv round 1: resubmitting the plan and giving feedback close the waiting approval question")

    # --- X09: plan-submit drops an approve-plan question about the earlier plan --------------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    r = rx(d, env, "plan-submit")
    check("[CP5-X09] plan-submit again prints a NEXT: line naming `ask approve-plan`, as the only question",
          r.returncode == 0 and "NEXT:" in r.stdout and "ask approve-plan" in r.stdout and "only question" in r.stdout,
          r.stdout)
    check("[CP5-X09] the question about the earlier plan is gone", "pending" not in cp3_state(d), cp3_state(d).get("pending"))
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "approve-plan")
    check("[CP5-X09] a yes after the resubmit does not approve the new plan without a fresh question",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X10: feedback records the reply that answered the review question, not a later message ----------------
    env = make_env("claude")
    d = cp4_awaiting_review(env)
    tp = d / "t.jsonl"
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "make the button blue")
    cp5_say(d, tp, env, "yes")  # a later message, answering something else
    r = rx(d, env, "feedback")
    st = cp3_state(d)
    check("[CP5-X10] feedback records the first reply after the review question, not the later 'yes'",
          r.returncode == 0 and (st.get("feedback") or [{}])[-1].get("text") == "make the button blue",
          (r.returncode, r.stderr, st.get("feedback")))
    check("[CP5-X10] feedback closes the waiting review question", "pending" not in st, st.get("pending"))
    r = rx(d, env, "human-approve")
    check("[CP5-X10] the later yes can't approve afterwards", r.returncode != 0 and "pending" not in cp3_state(d),
          (r.returncode, r.stderr))

    env = make_env("claude")
    d = cp4_awaiting_review(env)
    tp = d / "t.jsonl"
    rx(d, env, "ask", "human-approve")
    cp5_say(d, tp, env, "change the header copy")
    r1 = rx(d, env, "human-approve")  # spends the question: the first reply was not a yes
    cp5_say(d, tp, env, "yes")
    r = rx(d, env, "feedback")
    st = cp3_state(d)
    check("[CP5-X10] after the question was spent, feedback still records the reply that spent it",
          r1.returncode != 0 and r.returncode == 0
          and (st.get("feedback") or [{}])[-1].get("text") == "change the header copy", (r1.stderr, r.stderr, st.get("feedback")))


SECTIONS.append(("CP5", cp5_adv_round1_resubmit_and_feedback))


def cp5_adv_round1_prompt_hook_guidance():
    print("CP5 adv round 1: the prompt hook tells the agent whether a reply answers the question")

    env = make_env("claude")
    d, tp = cp5_plan(env)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X11] no question yet: the hook says Ratchet has not asked and names `ask approve-plan`",
          "has not asked" in hint and "ask approve-plan" in hint and "approves nothing" in hint, hint)
    q = rx(d, env, "ask", "approve-plan")
    check("[CP5-X11] `ask` tells the agent to show the question as the only one in its message",
          "only question" in q.stdout and "can't see" in q.stdout, q.stdout)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X11] the first reply: the hook says it answers the waiting question and is a listed yes",
          "answers your waiting question" in hint and "It is one of the listed yes replies" in hint, hint)
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X11] a second reply: the hook says the question was already answered and this does NOT approve",
          "already answered" in hint and "does NOT" in hint and "ask approve-plan" in hint, hint)
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    hint = cp5x_hint(d, tp, env, "ok")
    check("[CP5-X11] a first reply that is not listed: the hook says it approves nothing",
          "NOT one of the listed yes replies" in hint, hint)


SECTIONS.append(("CP5", cp5_adv_round1_prompt_hook_guidance))


def cp5_adv_round1_docs_match():
    print("CP5 adv round 1: the skills describe the approval question as it works")
    here = Path(__file__).resolve().parents[1]
    orch = (here / "skills/ratchet-orchestrator/SKILL.md").read_text()
    uic = (here / "skills/ratchet-ui-compare/SKILL.md").read_text()
    review = orch[orch.index("4. Run `$RATCHET ask human-approve`"):]
    review = review[:review.index("## Modes")]
    yes_i, wrap_i = review.index("listed yes replies →"), review.index("Give a one-line wrap-up")
    other_i = review.index("Anything else as their first reply")
    check("[CP5-X12] review step: the wrap-up follows only the approving reply, and the non-yes case is its own bullet",
          yes_i < wrap_i < other_i and "\n   - Anything else" in review, review[:400])
    cancel = next(l for l in orch.splitlines() if l.startswith('- **"Cancel"'))
    check("[CP5-X12] cancel shows the printed question word for word", "word for word" in cancel, cancel)
    respec = next(l for l in uic.splitlines() if "ask ui-respec" in l)
    check("[CP5-X12] ui-respec shows the printed question word for word; only the first reply counts",
          "word for word" in respec and "first reply" in respec, respec)
    check("[CP5-X12] one question per message: the plan question is asked alone, after the blockers are settled",
          "only question" in orch and "before step 11" in orch, "")


SECTIONS.append(("CP5", cp5_adv_round1_docs_match))


def main():
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            trust_helpers.FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{trust_helpers.PASS} passed, {trust_helpers.FAIL} failed")
    sys.exit(1 if trust_helpers.FAIL else 0)


if __name__ == "__main__":
    main()
