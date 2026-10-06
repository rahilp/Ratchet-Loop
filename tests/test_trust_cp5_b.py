#!/usr/bin/env python3
"""
CP5 of the trust-holes run, part b: approvals answer the question Ratchet asked.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_b.py
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import trust_helpers
from trust_helpers import (
    ENGINE, check, cp3_fp, cp3_signed_combined, cp3_state, cp5_awaiting_checkpoint_review, cp5_plan, cp5_say,
    cp5w_lock, cp5x_frozen_ui_project, cp5x_hint, cp5x_history, cp5y_first_run_plan, cp5y_guard_bash,
    cp5y_load_engine, cp5y_repo, cp5z_notes, cp5z_project_notes, iso_ago, make_env, now_dt, project, rx,
)

SECTIONS = []


# ---- CP5 adversarial round 2 ----
# Fixes for CP5's second adversarial round: a question can only be put once a chat owns the run (the chat that ran
# init, or the first chat the prompt hook records for it), so on a repo's first run another chat's yes can't answer
# or move it; a question waiting in another chat, and the hooks question, are never silently replaced; and the
# engine's own hints say to show the printed question word for word.











def cp5y_set_state(d, env, code):
    """Change state.json through the engine's own Ctx.save_state (the way an older run's state would look)."""
    prog = ("import importlib.util, sys\nfrom pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('r', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\nctx = m.Ctx(Path(sys.argv[2])); st = ctx.state()\n" + code +
            "\nctx.save_state(st)\n")
    r = subprocess.run([sys.executable, "-c", prog, str(ENGINE), str(d)], cwd=d, env=env, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr + r.stdout


def cp5_adv_round2_owner_before_questions():
    print("CP5 adv round 2: a question needs an owner chat; another chat's yes never answers or moves the run")

    # --- X13: first run, no owner yet: the chat that runs `ask` (seen by the guard hook) owns the run -------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    check("[CP5-X13] a repo's first run starts with no owner chat when nothing could be logged before init",
          cp3_state(d).get("owner_session") is None, cp3_state(d).get("owner_session"))
    r = rx(d, env, "ask", "claim")
    check("[CP5-X13] `ask claim` with no owner chat is refused (nothing to take over) and stores nothing",
          r.returncode != 0 and "nothing to take over" in r.stderr and not cp3_state(d).get("pending"),
          (r.returncode, r.stderr))
    g = cp5y_guard_bash(d, env, f'python3 "{ENGINE}" ask approve-plan', "s-A")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    rec = cp5x_history(d, "owner_recorded")
    check("[CP5-X13] with no owner, the chat whose `ask` the guard saw becomes the owner and the question's chat",
          g.returncode == 0 and r.returncode == 0 and st.get("owner_session") == "s-A"
          and (st.get("pending") or {}).get("session") == "s-A" and bool(rec) and rec[-1].get("via") == "ask",
          (g.stderr, r.returncode, r.stderr, st.get("owner_session"), st.get("pending"), rec))
    hint = cp5x_hint(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X13] the user's yes in another chat (the first message after the question) approves nothing",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and "another chat" in hint
          and st.get("owner_session") == "s-A" and (st.get("pending") or {}).get("action") == "approve-plan",
          (r.returncode, r.stderr, hint, st.get("owner_session")))
    cp5_say(d, tp, env, "yes", session="s-A")
    r = rx(d, env, "approve-plan")
    check("[CP5-X13] the asking chat's yes then approves, and the owner stays the asking chat",
          r.returncode == 0 and cp3_state(d)["phase"] == "building" and cp3_state(d).get("owner_session") == "s-A",
          (r.returncode, r.stderr))
    # with no guard note (a CLI whose guard doesn't run), the question waits unbound: the first chat that types owns it
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X13] without a guard note the question is still asked, bound to no chat yet",
          r.returncode == 0 and "ASK THE USER" in r.stdout and st.get("owner_session") is None
          and (st.get("pending") or {}).get("session") is None, (r.returncode, r.stderr, st.get("pending")))
    cp5_say(d, tp, env, "wait, not yet", session="s-A")
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X13] ... the first chat to reply owns the run; a later yes in another chat neither approves nor "
          "moves it", r.returncode != 0 and "wait, not yet" in r.stderr and st.get("owner_session") == "s-A"
          and st["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr, st.get("owner_session")))

    # --- X14: two chats, no owner yet: the first chat the prompt hook records owns the run ---------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5_say(d, tp, env, "ok, show me the plan", session="s-A")
    st = cp3_state(d)
    rec = cp5x_history(d, "owner_recorded")
    check("[CP5-X14] the prompt hook alone never binds the owner into state.json or logs it (no command has read "
          "its record yet): the hook keeps its own record, not state.json",
          st.get("owner_session") is None and not rec, (st.get("owner_session"), rec))
    hint = cp5x_hint(d, tp, env, "what's for lunch?", session="s-B")
    check("[CP5-X14] a second chat is told the run was started in another chat, from the hook's own record alone "
          "(state.json still names no owner)",
          "another chat" in hint and cp3_state(d).get("owner_session") is None, hint)
    r = rx(d, env, "ask", "approve-plan")
    pend = cp3_state(d).get("pending") or {}
    check("[CP5-X14] `ask approve-plan` now binds the question to the owner chat, not the chat that typed last",
          r.returncode == 0 and pend.get("session") == "s-A", (r.returncode, r.stderr, pend))
    cp5_say(d, tp, env, "yes", session="s-B")
    cp5_say(d, tp, env, "wait, not yet", session="s-A")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X14] the other chat's yes does not approve: the owner chat's first reply ('wait, not yet') decides",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and "wait, not yet" in r.stderr,
          (r.returncode, r.stderr))
    check("[CP5-X14] ... and the run did not move to the other chat", st.get("owner_session") == "s-A",
          st.get("owner_session"))

    # the same two chats, where the other chat's yes is the only reply: still nothing approved
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5_say(d, tp, env, "go on", session="s-A")
    cp5_say(d, tp, env, "something else", session="s-B")
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "approve-plan")
    st = cp3_state(d)
    check("[CP5-X14] a yes typed only in the other chat approves nothing and leaves the owner as it was",
          r.returncode != 0 and st["phase"] == "awaiting_plan_approval" and st.get("owner_session") == "s-A"
          and (st.get("pending") or {}).get("action") == "approve-plan",
          (r.returncode, r.stderr, st.get("owner_session")))

    # --- X15: a question with no chat recorded and no owner (an older run): no reply from any chat counts --------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_set_state(d, env, "st['pending'] = {'action': 'approve-plan', 'mode': None, 'asked_at': m.now(), "
                           "'prev_awaiting': st.get('awaiting_since'), 'phase': st['phase']}")
    cp5_say(d, tp, env, "yes", session="s-B")
    cp5y_set_state(d, env, "st['owner_session'] = None")  # as an older run's state: no owner recorded
    prog = ("import importlib.util, json, sys\nfrom pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('r', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\nctx = m.Ctx(Path(sys.argv[2])); none = {'owner_session': None}\n"
            "print(json.dumps({'replies': m._replies(ctx, none, '0', {'action': 'approve-plan'}),\n"
            "  'ask': m._asking_session(ctx, none, 'approve-plan'), 'claim': m._asking_session(ctx, none, 'claim')}))\n")
    u = subprocess.run([sys.executable, "-c", prog, str(ENGINE), str(d)], cwd=d, env=env, capture_output=True,
                       text=True)
    out = json.loads(u.stdout) if u.returncode == 0 and u.stdout.strip() else {}
    check("[CP5-X15] with no chat known for the question, _replies counts no message at all (no unfiltered fallback)",
          out.get("replies") == [], (u.returncode, u.stdout, u.stderr))
    check("[CP5-X15] with no owner, _asking_session names no chat (it never guesses the chat that typed last)",
          bool(out) and out.get("ask") is None and out.get("claim") is None, out)
    r = rx(d, env, "approve-plan")
    check("[CP5-X15] approve-plan on such a question is refused while no chat owns the run",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval", (r.returncode, r.stderr))

    # --- X16: the chat that runs `init` owns the run, even on a first run and even if another chat typed last -----
    env = make_env("claude")
    d = cp5y_repo()
    g = cp5y_guard_bash(d, env, f'python3 "{ENGINE}" init "feature" --slug f', "s-A")
    r = rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X16] the guard notes the chat that runs `init`, and init makes it the owner (first run, empty log)",
          g.returncode == 0 and r.returncode == 0 and cp3_state(d).get("owner_session") == "s-A",
          (g.returncode, g.stderr, r.stderr, cp3_state(d).get("owner_session")))
    tp = d / "t.jsonl"
    hint = cp5x_hint(d, tp, env, "yes", session="s-B")
    check("[CP5-X16] another chat's first message then gets the 'another chat' notice and does not take the run",
          "another chat" in hint and cp3_state(d).get("owner_session") == "s-A", hint)
    env = make_env("claude")
    d = cp5y_repo()
    (d / ".ratchet").mkdir()
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "unrelated question", session="s-B")
    cp5y_guard_bash(d, env, "$RATCHET init 'feature' --slug f", "s-A")
    rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X16] the chat that ran init owns the run even when another chat typed last",
          cp3_state(d).get("owner_session") == "s-A", cp3_state(d).get("owner_session"))
    env = make_env("claude")
    d = cp5y_repo()
    (d / ".ratchet").mkdir()
    cp5_say(d, d / "t.jsonl", env, "please build it", session="s-B")
    notes = Path(env["RATCHET_HOME"]) / "engine-call-sessions.json"
    notes.parent.mkdir(parents=True, exist_ok=True)
    old = (now_dt() - dt.timedelta(minutes=30)).isoformat()
    key = f"{d.resolve()}|init"
    notes.write_text(json.dumps({key: {"session": "s-stale", "at": old}}))
    rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X16] a stale init note (older than a few minutes) is ignored and removed",
          cp3_state(d).get("owner_session") == "s-B"
          and key not in json.loads(notes.read_text() or "{}"), cp3_state(d).get("owner_session"))


SECTIONS.append(("CP5", cp5_adv_round2_owner_before_questions))


def cp5_adv_round2_no_silent_overtake():
    print("CP5 adv round 2: another chat's question, and the hooks question, are never silently replaced")

    # --- X17: another chat's `ask claim` must not drop the run chat's waiting question ------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    r = rx(d, env, "ask", "claim")
    st = cp3_state(d)
    check("[CP5-X17] `ask claim` from another chat is refused while the run chat's question waits",
          r.returncode != 0 and "another chat" in r.stderr and "ASK THE USER" not in r.stdout
          and "NOT ASKED" not in r.stdout, (r.returncode, r.stdout, r.stderr))
    check("[CP5-X17] ... the run chat's plan question is kept, and nothing is logged as overtaken",
          (st.get("pending") or {}).get("action") == "approve-plan"
          and (st.get("pending") or {}).get("session") == "s-owner"
          and not cp5x_history(d, "question_overtaken"), (st.get("pending"), cp5x_history(d, "question_overtaken")))
    hint = cp5x_hint(d, tp, env, "yes")
    check("[CP5-X17] the owner chat's prompt hook still treats its reply as the answer to the plan question",
          "answers your waiting question" in hint and "another chat" not in hint, hint)
    r = rx(d, env, "approve-plan")
    check("[CP5-X17] the owner's yes then approves the plan",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr))
    # the other way round: the claim waits in the other chat, and the owner chat's question must not drop it
    env = make_env("claude")
    d = project(env)
    tp = d / "t.jsonl"
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    rx(d, env, "ask", "claim")
    r = rx(d, env, "ask", "cancel")
    st = cp3_state(d)
    check("[CP5-X17] the owner chat's `ask cancel` is refused while the claim question waits in the other chat",
          r.returncode != 0 and "another chat" in r.stderr and (st.get("pending") or {}).get("action") == "claim",
          (r.returncode, r.stderr, st.get("pending")))
    cp5_say(d, tp, env, "yes", session="s-other")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X17] ... and the claiming chat's yes still moves the run there",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-other", (r.returncode, r.stderr))
    # once the waiting question had its reply, another chat's claim may replace it
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "hmm, let me think")
    cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X17] a question that already had its reply can be replaced by another chat's claim",
          r.returncode == 0 and (cp3_state(d).get("pending") or {}).get("action") == "claim",
          (r.returncode, r.stderr))
    # if the user says the run's chat is gone, an explicit (logged) withdraw lets the claim be asked
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "that old chat is gone, carry on here", session="s-other")
    rx(d, env, "ask", "withdraw")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X17] after an explicit `ask withdraw` (logged), the claim can be asked",
          r.returncode == 0 and "ASK THE USER" in r.stdout and bool(cp5x_history(d, "question_withdrawn")),
          (r.returncode, r.stderr))

    # --- X18: a pending hooks-waive question is never replaced, so its reply can't be hidden -------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "hooks-waive")
    for action in (["cancel"], ["set-review", "each"], ["approve-plan"], ["choice", "--options", "A,B"]):
        r = rx(d, env, "ask", *action)
        st = cp3_state(d)
        check(f"[CP5-X18] `ask {' '.join(action)}` over a waiting hooks question is refused and keeps it",
              r.returncode != 0 and "hooks" in r.stderr and "ask withdraw" in r.stderr
              and (st.get("pending") or {}).get("action") == "hooks-waive"
              and not cp5x_history(d, "question_overtaken"), (r.returncode, r.stdout, r.stderr, st.get("pending")))
    eng = cp5y_load_engine()
    cp5_say(d, tp, env, eng.HOOKS_WAIVE_REPLY)
    r = rx(d, env, "ask", "cancel")
    check("[CP5-X18] after the user answered the hooks question, `ask cancel` is still refused (the reply stays "
          "with the hooks question)", r.returncode != 0 and "hooks-waive" in r.stderr
          and (cp3_state(d).get("pending") or {}).get("action") == "hooks-waive", (r.returncode, r.stderr))
    r = rx(d, env, "hooks-waive")
    check("[CP5-X18] `hooks-waive` then reads the user's reply to its own question (it was not hidden)",
          r.returncode == 0 and "No human message" not in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X18] once the hooks question is settled, the plan question can be asked",
          r.returncode == 0 and "ASK THE USER" in r.stdout, (r.returncode, r.stderr))
    # a first reply that is not the waiver phrase is a no: nothing to hide, so a new question may replace it (logged)
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "hooks-waive")
    cp5_say(d, tp, env, "no, keep the hooks")
    r = rx(d, env, "ask", "approve-plan")
    over = cp5x_history(d, "question_overtaken")
    check("[CP5-X18] after a 'no' to the hooks question, the plan question replaces it and the swap is logged",
          r.returncode == 0 and "ASK THE USER" in r.stdout and bool(over) and over[-1].get("action") == "hooks-waive"
          and over[-1].get("answered") is True, (r.returncode, r.stderr, over))


SECTIONS.append(("CP5", cp5_adv_round2_no_silent_overtake))


def cp5_adv_round2_engine_hints():
    print("CP5 adv round 2: the engine's own hints say to show the printed question word for word")

    # --- X19: ui-record's frozen-spec refusal ----------------------------------------------------------------
    # (CP7: the combined review must be script-signed with real provenance to reach the frozen-spec check at all;
    # cp3_signed_combined's spec_sha256 override produces one that still mismatches round 1's frozen spec)
    env = make_env("claude")
    d = cp5x_frozen_ui_project(env)
    fp = cp3_fp(d, env)
    combined = str(cp3_signed_combined(d, env, fp, n=2, spec_sha256="s2"))
    r = rx(d, env, "ui-record", "CP1", "--review", combined)
    check("[CP5-X19] ui-record's frozen-spec refusal names `ask ui-respec`, says to show the question word for word, "
          "and that only a listed yes as the first reply counts", r.returncode != 0 and "ask ui-respec CP1" in r.stderr
          and "word for word" in r.stderr and "first reply" in r.stderr and "clear yes" not in r.stderr,
          (r.returncode, r.stderr))

    # --- X20: the prompt hook's claim hint (another chat) and cancel hint ---------------------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    hint = cp5x_hint(d, tp, env, "can we pick this up here?", session="s-other")
    check("[CP5-X20] the 'another chat' hint says to show the claim question word for word; first reply, listed yes",
          "ask claim" in hint and "word for word" in hint and "first reply" in hint and "clear yes" not in hint, hint)
    hint = cp5x_hint(d, tp, env, "please cancel this run")
    check("[CP5-X20] the cancel hint says to show the cancel question word for word; first reply, listed yes",
          "ask cancel" in hint and "word for word" in hint and "first reply" in hint and "clear yes" not in hint, hint)

    # --- X21: next_action's plan-approval text is built from PLAN_ASK_NEXT ----------------------------------------
    eng = cp5y_load_engine()
    r = rx(d, env, "next")
    act = json.loads(r.stdout).get("action", "")
    body = eng.PLAN_ASK_NEXT[len("NEXT: "):]
    check("[CP5-X21] `next` while the plan waits (no question yet) says what PLAN_ASK_NEXT says, word for word",
          act.startswith("Waiting for the user to approve the plan. ") and (body[:1].upper() + body[1:]) in act, act)


SECTIONS.append(("CP5", cp5_adv_round2_engine_hints))


# ---- CP5 adversarial round 3 ----
# The guard's "this chat runs ratchet.py init/ask" note is planted only for a real ratchet.py call (parsed shell
# words, its exact arguments), never for text that merely mentions one; the engine takes a note only when the
# arguments equal its own, it is a few seconds old at most, and only once.
# (Correction: "a few seconds" above is loose; the limit is ENGINE_NOTE_SECONDS, 60 seconds.)





def cp5_adv_round3_engine_note_real_call():
    print("CP5 adv round 3: only a real ratchet.py init/ask call, with matching arguments, fresh and unused, names "
          "the chat")
    m = cp5y_load_engine()

    # --- X22: the parser tells a real call from a mention ------------------------------------------------------
    decoys = ['echo "python3 ratchet.py ask approve-plan"', "echo python3 ratchet.py ask approve-plan",
              "git commit -m 'next: $RATCHET ask approve-plan'", 'grep -n "ratchet.py ask" README.md',
              "cat <<EOF\n$RATCHET ask approve-plan\nEOF", "# $RATCHET ask approve-plan",
              "true # ; $RATCHET ask approve-plan", 'echo a#" x\n$RATCHET ask approve-plan"',
              'echo "&&" $RATCHET ask approve-plan', "echo \\; $RATCHET ask approve-plan",
              "printf '%s\\n' \"$RATCHET init feature --slug f\"", "echo `$RATCHET ask approve-plan`"]
    bad = [c for c in decoys if m._engine_invocations(c)]
    check("[CP5-X22] text that only mentions ratchet.py init/ask (echo, commit message, grep, heredoc, comment, "
          "quoted separators) is never read as a call", not bad, bad)
    real = {f'python3 "{ENGINE}" ask approve-plan': [["ask", "approve-plan"]],
            '$RATCHET ask approve-plan 2>&1 | tail -5': [["ask", "approve-plan"]],
            "cd /p && $RATCHET init 'my feature' --slug f; echo done": [["init", "my feature", "--slug", "f"]],
            "X=1 python3 -u ./ratchet.py --root /p ask claim": [["--root", "/p", "ask", "claim"]]}
    got = {c: m._engine_invocations(c) for c in real}
    check("[CP5-X22] a real call is read with its exact arguments", got == real, got)

    # --- X23: mentions in another chat plant nothing and don't change who owns a first run ------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    for c in decoys:
        cp5y_guard_bash(d, env, c, "s-B")
    check("[CP5-X23] the guard notes nothing for those mentions", cp5z_project_notes(d, env) == {},
          cp5z_project_notes(d, env))
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X23] the real `ask` (its own guard note missing) does not hand the run to the chat that mentioned it",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None,
          (r.returncode, r.stderr, st.get("owner_session"), st.get("pending")))
    cp5_say(d, tp, env, "yes", session="s-A")
    r = rx(d, env, "approve-plan")
    check("[CP5-X23] ... the existing rule applies: the first chat to reply (s-A) owns it, and its yes approves",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-A", (r.returncode, r.stderr))
    env = make_env("claude")
    d = cp5y_repo()
    (d / ".ratchet").mkdir()
    cp5_say(d, d / "t.jsonl", env, "please build the feature", session="s-A")
    cp5y_guard_bash(d, env, 'git commit -m "then run $RATCHET init feature --slug f"', "s-B")
    cp5y_guard_bash(d, env, f'echo python3 "{ENGINE}" init feature --slug f', "s-B")
    rx(d, env, "init", "feature", "--slug", "f")
    check("[CP5-X23] an `init` mentioned in another chat's echo/commit message does not make that chat the owner",
          cp3_state(d).get("owner_session") == "s-A", cp3_state(d).get("owner_session"))

    # --- X24: the real call does name the chat; the note is used once ---------------------------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_guard_bash(d, env, f'cd "{d}" && python3 "{ENGINE}" ask approve-plan 2>&1 | tail -5', "s-B")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X24] the chat whose guard saw the real `ask approve-plan` call owns the run",
          r.returncode == 0 and st.get("owner_session") == "s-B" and (st.get("pending") or {}).get("session") == "s-B",
          (r.returncode, r.stderr, st.get("owner_session")))
    check("[CP5-X24] the note is used up", cp5z_project_notes(d, env) == {}, cp5z_project_notes(d, env))
    cp5y_set_state(d, env, "st['owner_session'] = None; st.pop('pending', None)")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X24] the same call run again without a new guard note names no chat (a note works once)",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None,
          (r.returncode, r.stderr, st.get("owner_session")))

    # --- X25: a note for different arguments, an expired note, or two chats noting the same call: ignored -----
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_guard_bash(d, env, "$RATCHET ask cancel", "s-B")
    rx(d, env, "ask", "approve-plan")
    check("[CP5-X25] a note for another call (`ask cancel`) does not name the chat running `ask approve-plan`",
          cp3_state(d).get("owner_session") is None, cp3_state(d).get("owner_session"))
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    key = f"{d.resolve()}|ask"
    p = Path(env["RATCHET_HOME"]) / "engine-call-sessions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({key: [{"session": "s-B", "argv": ["ask", "approve-plan"],
                                    "at": iso_ago(m.ENGINE_NOTE_SECONDS + 30)}]}))
    rx(d, env, "ask", "approve-plan")
    check("[CP5-X25] an expired note (older than ENGINE_NOTE_SECONDS, seconds not minutes) is ignored and removed",
          m.ENGINE_NOTE_SECONDS <= 120 and cp3_state(d).get("owner_session") is None
          and key not in cp5z_notes(env), (cp3_state(d).get("owner_session"), cp5z_notes(env)))
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-A")
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-B")
    rx(d, env, "ask", "approve-plan")
    check("[CP5-X25] when two chats noted the same call, neither is guessed to be the owner",
          cp3_state(d).get("owner_session") is None and cp5z_project_notes(d, env) == {},
          (cp3_state(d).get("owner_session"), cp5z_project_notes(d, env)))


SECTIONS.append(("CP5", cp5_adv_round3_engine_note_real_call))


# ---- CP5 adversarial round 4 ----
# A claim question belongs to the chat that ran `ask claim` (the guard's note for that exact call), never to the chat
# the user typed in last; another chat's same question never replaces a waiting one; bash $'...' quoting is never
# read as a call; and two approval commands running at once can't both spend the same answer.

def cp5w_claim_project(env):
    d = project(env)
    return d, d / "t.jsonl"




def cp5_adv_round4_claim_chat_and_lock():
    print("CP5 adv round 4: the claim belongs to the chat that asked; one approval command at a time")
    m = cp5y_load_engine()

    # --- X26: three chats: the claim binds to the chat that ran `ask claim`, not the one that typed last ---------
    env = make_env("claude")
    d, tp = cp5w_claim_project(env)
    cp5_say(d, tp, env, "carry on with that run here", session="s-B")
    cp5_say(d, tp, env, "what does this function do?", session="s-C")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    r = rx(d, env, "ask", "claim")
    st = cp3_state(d)
    check("[CP5-X26] the claim question belongs to the chat that ran `ask claim` (s-B), not the one typed in last",
          r.returncode == 0 and (st.get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr,
                                                                                   st.get("pending")))
    hint = cp5x_hint(d, tp, env, "yes", session="s-C")
    check("[CP5-X26] the third chat's prompt hook says the run is in another chat (its yes is not the answer)",
          "another chat" in hint and "answers your waiting question" not in hint, hint)
    r = rx(d, env, "resume", "--claim")
    st = cp3_state(d)
    check("[CP5-X26] a yes in the third chat does not answer the claim or move the run",
          r.returncode != 0 and st.get("owner_session") == "s-owner"
          and (st.get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr, st.get("owner_session")))
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X26] the asking chat's own yes then moves the run there",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-B", (r.returncode, r.stderr))
    # the note wins even when the asking chat typed last of all but another chat's note is for a different call
    env = make_env("claude")
    d, tp = cp5w_claim_project(env)
    cp5_say(d, tp, env, "what does this function do?", session="s-C")
    cp5_say(d, tp, env, "carry on with that run here", session="s-B")
    cp5y_guard_bash(d, env, "$RATCHET ask cancel", "s-C")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X26] a note for another call from a third chat doesn't take the claim",
          r.returncode == 0 and (cp3_state(d).get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr))

    # --- X27: another chat's same question never replaces the waiting one -------------------------------------
    env = make_env("claude")
    d, tp = cp5w_claim_project(env)
    cp5_say(d, tp, env, "carry on with that run here", session="s-B")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    rx(d, env, "ask", "claim")
    cp5_say(d, tp, env, "no, carry on with it here instead", session="s-C")
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-C")
    r = rx(d, env, "ask", "claim")
    st = cp3_state(d)
    check("[CP5-X27] the same `ask claim` from another chat is refused while s-B's claim waits; nothing overtaken",
          r.returncode != 0 and "another chat" in r.stderr and (st.get("pending") or {}).get("session") == "s-B"
          and not cp5x_history(d, "question_overtaken"), (r.returncode, r.stderr, st.get("pending")))
    cp5y_guard_bash(d, env, "$RATCHET ask claim", "s-B")
    r = rx(d, env, "ask", "claim")
    check("[CP5-X27] the asking chat itself can still print its question again",
          r.returncode == 0 and (cp3_state(d).get("pending") or {}).get("session") == "s-B", (r.returncode, r.stderr))
    cp5_say(d, tp, env, "yes", session="s-B")
    r = rx(d, env, "resume", "--claim")
    check("[CP5-X27] ... and s-B's yes still moves the run to s-B",
          r.returncode == 0 and cp3_state(d).get("owner_session") == "s-B", (r.returncode, r.stderr))

    # --- X22 (more decoys): bash $'...' quoting is a mention, never a call --------------------------------------
    # each is ONE bash string: inside $'...' a \' is a quote character, not the end of the string
    ansi = ["echo $'\\' ; $RATCHET ask approve-plan #'", "echo $'it\\'s && python3 ratchet.py ask approve-plan'",
            "printf $'%s\\' ; $RATCHET init feature --slug f\\n'"]
    bad = [c for c in ansi if m._engine_invocations(c)]
    check("[CP5-X22] a ratchet.py call inside (or after) bash $'...' quoting is never read as a call", not bad, bad)
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    for c in ansi:
        cp5y_guard_bash(d, env, c, "s-B")
    check("[CP5-X22] the guard notes nothing for them", cp5z_project_notes(d, env) == {}, cp5z_project_notes(d, env))
    check("[CP5-X22] a plain call is still read", m._engine_invocations("$RATCHET ask approve-plan") ==
          [["ask", "approve-plan"]], m._engine_invocations("$RATCHET ask approve-plan"))
    check("[CP5-X22] the unused _SHELL_SEPARATORS constant is gone", not hasattr(m, "_SHELL_SEPARATORS"))
    # Note (round 5): the check above names a private constant, not behavior; the line is locked, so it stays. The
    # behavior check that replaces it is the round-5 "[CP5-X22] calls after ; && || | ..." check at the end.

    # --- X28: a held lock makes an approval wait; a stale one is cleared -----------------------------------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    lock = cp5w_lock(d)
    lock.write_text("12345 held-by-test")
    proc = subprocess.Popen([sys.executable, str(ENGINE), "approve-plan"], cwd=d, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    waited = proc.poll() is None and cp3_state(d)["phase"] == "awaiting_plan_approval"
    lock.unlink()
    out, err = proc.communicate(timeout=60)
    check("[CP5-X28] approve-plan waits while another command holds the state lock",
          waited, (proc.returncode, err))
    check("[CP5-X28] ... and approves once it is released, removing its own lock",
          proc.returncode == 0 and cp3_state(d)["phase"] == "building" and not lock.exists(), (proc.returncode, err))
    lock.write_text("12345 crashed")
    old = time.time() - m.STATE_LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    r = rx(d, env, "ask", "withdraw")
    check("[CP5-X28] a stale lock (left by a crashed command) is removed and the command runs",
          r.returncode == 0 and not lock.exists(), (r.returncode, r.stderr))

    # --- X29: two approvals at once (double submit): exactly one spends the answer ------------------------------
    for cmd in ("approve-plan", "human-approve"):
        env = make_env("claude")
        d, tp = cp5_plan(env) if cmd == "approve-plan" else cp5_awaiting_checkpoint_review(env)
        r = rx(d, env, "ask", cmd)
        cp5_say(d, tp, env, "yes")
        events_before = len(cp5x_history(d))
        procs = [subprocess.Popen([sys.executable, str(ENGINE), cmd], cwd=d, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
        res = [(p.communicate(timeout=120), p.returncode) for p in procs]
        codes = sorted(rc for _, rc in res)
        hist = cp5x_history(d)[events_before:]
        kind = "plan_approved" if cmd == "approve-plan" else "approved"
        check(f"[CP5-X29] two `{cmd}` at once: exactly one succeeds, the other refuses plainly",
              codes == [0, 1] and all("Traceback" not in e for (_, e), _ in res), (r.stderr, res))
        check(f"[CP5-X29] ... and the approval is recorded once", len([e for e in hist if e.get("event") == kind]) == 1,
              [e.get("event") for e in hist])
        check(f"[CP5-X29] ... and no lock file is left behind", not cp5w_lock(d).exists())


SECTIONS.append(("CP5", cp5_adv_round4_claim_chat_and_lock))


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
