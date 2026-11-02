#!/usr/bin/env python3
"""
status_text.py -- plain-language status, next-action and blocked-options rendering for ratchet.py's engine.

plain_reason/blocked_options/plain_now/waiting_question/question_line/exit_code_only_line/exit_code_only_note
and the done-means-link helpers _lines_word/done_means_table/_finish_block_lines/_finish_blockers/_finish_held/
_finish_legacy_note/_finish_integrity_lines/_unproven_finish_lines/_accepted_lines/_apply_accepted_lines, moved
verbatim out of ratchet.py (see tests/test_modularize_cp8.py, CP8-T01) so this one responsibility -- the plain
English a non-engineer reads for what is happening now, what is blocked and why, what a waiting question is
about, and whether each done-means line is proven -- can be read and changed without reading or risking the
rest of the 21,000+ line engine. ratchet.py imports these names explicitly (`from status_text import ...`); it
never does a star import, and keeps no `def`/`class` of its own for any of them. cmd_status_text,
cmd_plan_summary and next_action (the CLI/next-action handlers) stay in ratchet.py, unchanged except that the
names they call now resolve through this module's explicit import, the same way plan_support.py's/
run_records.py's/records.py's/provider_discovery.py's own moved names are reached from their callers.

Twelve ratchet.py-resident names the moved functions call are NOT one of the moved names and stay in ratchet.py for
now (candidates for a later checkpoint's own module, not this one's responsibility): _finish_pending (plain_now,
_finish_held), _question_waiter/_question_answered/_pq (waiting_question), _plan_as_approved/_approved_after_dm/
_done_means_proof/_dm_none_note (done_means_table; _approved_after_dm is also read directly by _finish_blockers
and _finish_legacy_note), left_for_later/_finding_dm_lines/_finding_key/_approved_done_means
(_finish_block_lines). Every one of these is reached lazily, by file path, at call time only, through
_ratchet_module() below -- never a disk read or an AST parse of ratchet.py's text at THIS module's import
time, and never a module-level `import ratchet` that would create an import cycle (ratchet.py already imports
this module). This is the exact shape records.py's/provider_discovery.py's/run_records.py's/plan_support.py's
own wrappers use for their own forward dependency on ratchet.py. approval_snapshot (_finish_integrity_lines) is
not one of these: it already lives in run_records.py (moved there in CP6), so it is simply imported from there
the ordinary way, with no lazy wrapper and no cycle risk (run_records.py does not import this module or
ratchet.py at its own load time).

Seven more ratchet.py-resident names these functions read as a bare name, not a call, are given here too instead
of wrapped, because each is a small, fixed value used ONLY within the moved functions themselves (never a
forward dependency on anything that varies or is shared with code outside this move) -- the same "a related
helper placed directly, with a comment saying why" shape provider_discovery.py's/run_records.py's own
docstrings describe for their own small fixed literals: GATING_QUESTIONS/ALWAYS_GATING_QUESTIONS/GATING_PHASES
(waiting_question) and CARRY_ON_QUESTIONS (waiting_question) keep their exact original tuple shape (none is a
Dict/List/Set literal or a dict()/list()/set()/Counter()/defaultdict()/OrderedDict() call, so this move's own
mutable-literal scan does not flag any of them either way); DM_NO_TESTS_NOTE (done_means_table) is a plain
string, likewise unflagged. STEP_PLAIN (plain_now, and also step_stall below) and QUESTION_WORDS (question_line) were plain dict literals
in their original home; here they are each given in an equivalent but immutable shape instead
(types.MappingProxyType(...) wrapping the same literal) -- behaviourally identical for the only way the moved
bodies use them (".get()"), never mutated in their original home either, and this way the scan (which checks
the literal AST shape of the top-level value, not what it is used for: a Call to MappingProxyType is not one
of the banned dict()/list()/set()/... constructors) finds neither a new mutable registry, exactly as it
expects for this move -- the same device run_records.py's own docstring documents for its own
_HOOK_ENV_TIE_RANK.

The schedule helpers at the end of this file (schedule_numbers/step_stall and the _sched_* helpers) are new, not
moved. They are pure (no disk, git or subprocess access, no real clock) except for three more ratchet.py-resident
names, reached lazily through _ratchet_module() at call time like the twelve above: PROGRESS_EVENTS and
_plain_duration (step_stall) and _parse_iso (_sched_time). step_stall also reads STEP_PLAIN.

HELD_AGAIN_PREFIX is read as a bare name both inside the moved functions (_apply_accepted_lines,
_finish_block_lines) and by two call sites that stay in ratchet.py (the accept-line/finish-receipt commands
that strip a line's "held again" reason back out before showing it to the user); a plain string constant never
varies at runtime, so it is simply moved here with the functions that own its meaning and imported back into
ratchet.py by explicit name for those two remaining call sites, the same way ratchet.py already imports
NO_OBJECTIVE_NOTE back from plan_support.py.
"""
from __future__ import annotations

import re
import sys
import types
import datetime as _dt
from pathlib import Path

# A loader that reaches this file directly (spec_from_file_location, the way this project's own
# source-inspecting tests do, and the way ratchet.py never does -- ratchet.py always has its own directory on
# sys.path already) may never have put this file's own directory on sys.path, so the sibling import just
# below would fail; ratchet.py itself does the same insert before its own sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_records import approval_snapshot


def _ratchet_module():
    """The engine's own module, with every ratchet.py-resident name these functions still reach for (not yet
    moved anywhere; most look like candidates for a later checkpoint's own module) -- loaded by file path,
    right beside this module, never via a bare `import ratchet` that would depend on sys.path (and would be a
    real import cycle: ratchet.py already imports this module). Reuses an already-running ratchet.py
    (registered under "ratchet" in sys.modules -- including by ratchet.py itself when it runs as __main__)
    when there is one, so this never re-executes the file as a second, separate module instance in the normal
    case. Mirrors records.py's/provider_discovery.py's/run_records.py's/plan_support.py's own helper of the
    same name and shape."""
    mod = sys.modules.get("ratchet")
    if mod is not None:
        return mod
    import importlib.util as _ilu
    path = Path(__file__).resolve().with_name("ratchet.py")
    spec = _ilu.spec_from_file_location("ratchet", path)
    mod = _ilu.module_from_spec(spec)
    sys.modules["ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


def _finish_pending(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own finish-pause check -- see _ratchet_module()."""
    return _ratchet_module()._finish_pending(*args, **kwargs)


def _question_waiter(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own question-waiter lookup -- see _ratchet_module()."""
    return _ratchet_module()._question_waiter(*args, **kwargs)


def _question_answered(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own answered-question check -- see _ratchet_module()."""
    return _ratchet_module()._question_answered(*args, **kwargs)


def _pq(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own pending-question (mode, target) reader -- see
    _ratchet_module()."""
    return _ratchet_module()._pq(*args, **kwargs)


def _plan_as_approved(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own approved-plan-objective reader -- see
    _ratchet_module()."""
    return _ratchet_module()._plan_as_approved(*args, **kwargs)


def _approved_after_dm(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own done-means-rules approval marker check -- see
    _ratchet_module()."""
    return _ratchet_module()._approved_after_dm(*args, **kwargs)


def _done_means_proof(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own per-line done-means proof builder -- see
    _ratchet_module()."""
    return _ratchet_module()._done_means_proof(*args, **kwargs)


def _dm_none_note(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own no-objective/no-lines plain note -- see
    _ratchet_module()."""
    return _ratchet_module()._dm_none_note(*args, **kwargs)


def left_for_later(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own open-findings-left-for-later reader -- see
    _ratchet_module()."""
    return _ratchet_module().left_for_later(*args, **kwargs)


def _finding_dm_lines(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own finding-to-done-means-line resolver -- see
    _ratchet_module()."""
    return _ratchet_module()._finding_dm_lines(*args, **kwargs)


def _finding_key(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own finding-key builder -- see _ratchet_module()."""
    return _ratchet_module()._finding_key(*args, **kwargs)


def _approved_done_means(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own approved done-means-lines reader -- see
    _ratchet_module()."""
    return _ratchet_module()._approved_done_means(*args, **kwargs)


# A plain dict literal here would be flagged by this move's own new-mutable-registry scan; a MappingProxyType
# wrapping one is not (its own top-level value is a Call to MappingProxyType, not a Dict literal), and it is
# behaviourally identical for the only way plain_now/question_line/step_stall use these (".get()") -- see module
# docstring.
STEP_PLAIN = types.MappingProxyType({
    "tests": "writing the tests", "red": "checking the new tests fail before any code exists",
    "implement": "building it", "behavior": "running the tests", "ui": "checking how it looks",
    "adversarial": "getting a second opinion", "complete": "wrapping up"})


QUESTION_WORDS = types.MappingProxyType({
    "approve-plan": "the plan", "human-approve": "an approval", "approve-changes": "changes to the approved plan files",
    "cancel": "cancelling the run", "set-review": "how often to review", "claim": "continuing the run in this chat",
    "hooks-waive": "building without the hooks", "ui-respec": "a change to the UI check",
    "choice": "a choice between options", "accept-finding": "accepting a review finding as a known limit",
    "raise-limit": "raising the review limit by one round",
    "accept-line": "accepting an unproven done-means line"})


GATING_QUESTIONS = ("approve-plan", "human-approve", "choice")  # while the run is at a pause or blocked
ALWAYS_GATING_QUESTIONS = ("accept-finding", "raise-limit", "accept-line")  # a blocked review round, or the held
                                                                               # finish (CP5), waits on them
GATING_PHASES = ("awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review", "blocked",
                 "paused")
CARRY_ON_QUESTIONS = ("cancel", "set-review", "claim", "hooks-waive", "ui-respec", "choice")


DM_NO_TESTS_NOTE = ("No tests are linked to any of these lines (an older plan, from before tests could be linked "
                    "to a done-means line), so there is no result to show for them.")


HELD_AGAIN_PREFIX = "held again: "


def plain_reason(reason):
    """The block reason in plain words for the user. Unknown reasons pass through unchanged; the raw text stays
    in `status --json` (blocked_reason)."""
    if not reason:
        return reason
    m = re.match(r"(CP\d+): behavior gate failed (\d+) times", reason)
    if m:
        return f"The tests for {m.group(1)} still fail after {m.group(2)} tries."
    m = re.match(r"(CP\d+): UI gate failed (\d+) rounds", reason)
    if m:
        return f"The look-and-feel check for {m.group(1)} still is not passing after {m.group(2)} rounds."
    m = re.match(r"(CP\d+): adversarial review still has (\d+) finding\(s\) after (\d+) rounds", reason)
    if m:
        return (f"The reviewers still have {m.group(2)} open concern(s) about {m.group(1)} after "
                f"{m.group(3)} rounds of fixes, which is the limit.")
    m = re.match(r"(CP\d+): (\d+) adversarial rounds used without approval", reason)
    if m:
        return f"The reviewers have not signed off on {m.group(1)} after {m.group(2)} rounds, which is the limit."
    m = re.match(r"Run-wide adversarial round cap reached \((\d+)/(\d+)\)", reason)
    if m:
        return f"The reviewers have used all the review rounds allowed for this run ({m.group(1)} of {m.group(2)})."
    if reason.startswith("The agent tried to stop"):
        return "The work kept stopping without making progress, so it was halted for you to look at."
    return reason


def blocked_options(st) -> list:
    """2-4 lettered choices for the user, fitted to why the run is blocked. Offered by the agent, not typed.
    Only offers things that can work. Accepting an open finding is never one of these: it is never a lettered
    pick (so a pick followed by `human-approve` can never stand in for the user's own `accept: <reason>` reply).
    When that is a real option here, `next`/`status` add it as its own plain line instead (see
    `_blocked_accept_note`). At a step's own round-limit block the menu leads with simplify, then change the
    step, then stop; raising the limit (CP1: at most once per step, ever) comes last, and only while this step's
    own `cap_raised_to` (on the blocked checkpoint's adversarial gate) is still unset. The older, run-wide cap
    (a different mechanism: a config change plus `approve-changes`, not scoped to one step) keeps offering raise
    every time, just reordered the same way."""
    reason = st.get("blocked_reason")
    r = (reason or "").lower()
    cur = st.get("current")
    cps = (st.get("checkpoints") or {}).get(cur) if cur else None
    raise_used = bool(((cps or {}).get("gates") or {}).get("adversarial", {}).get("cap_raised_to")) if cps else False
    if "round cap" in r or "run-wide" in r:
        texts = ["Simplify the step so the reviewers can approve it in the rounds already allowed.",
                 "Stop here and keep what is already finished.",
                 "Raise the review limit so the reviewers can keep going."]
    elif "rounds used" in r or ("adversarial" in r and re.search(r"after \d+ rounds", r)):
        texts = ["Simplify the step, then let the reviewers look at it once more.",
                 "Change the step so the review notes no longer apply.",
                 "Stop here and keep what is already finished."]
        if not raise_used:
            texts.append("Raise the review limit so the reviewers can keep going.")
    elif "behavior gate failed" in r:
        texts = ["Try again with a different approach.", "Change or revise this step (its tests or its scope).",
                 "Stop here and keep what is already finished."]
    elif "adversarial" in r or "review" in r:
        texts = ["Change the step so the review notes no longer apply.",
                 "Stop here and keep what is already finished."]
    else:
        texts = ["Try again.", "Change the plan for this step.", "Stop here and keep what is already finished."]
    return [{"letter": "ABCD"[i], "text": t} for i, t in enumerate(texts)]


def plain_now(ctx: Ctx, st, plan=None) -> str:
    """One plain sentence about what is happening now (no phase ids, no commands). At the finish pause
    (`_finish_pending`) while the finish is held (`_finish_held`, CP4: an unproven line, or a checkpoint or test
    plan edited after approval) it says so instead of inviting an approval, so the sentence the agent relays never
    contradicts the block printed under it."""
    phase = st.get("phase")
    plan = plan if plan is not None else (ctx.plan() or {})
    if phase in ("awaiting_final_review", "awaiting_checkpoint_review") and _finish_pending(st):
        held = _finish_blockers(ctx, st)
        if held:
            n = len(held)
            return (f"Everything is built, but {n} done-means line{'s are' if n != 1 else ' is'} unproven, so the "
                    "run cannot be approved as done yet.")
        if _finish_integrity_lines(ctx, st):
            return ("Everything is built, but a checkpoint or test plan changed after the plan was approved, so the "
                    "run cannot be approved as done yet (the change must be confirmed or undone first).")
    # c.get("id", f"#{i}"): a checkpoint missing "id" must not crash `status`; validate_plan is what names it.
    titles = {c.get("id", f"#{i}"): c.get("title", c.get("id", f"#{i}"))
              for i, c in enumerate(plan.get("checkpoints", []))}
    fixed = {
        "planning": "Planning: splitting the feature into steps.",
        "awaiting_plan_approval": "The plan is ready and waiting for your approval. Say yes to start building, "
                                  "or tell me what to change.",
        "awaiting_final_review": "Everything is built. Please look it over, then approve or tell me what should change.",
        "planning_feedback": "Turning your feedback into new steps.",
        "paused": "The work is paused until you say to continue.",
        "cancelled": "This run was cancelled.",
        "done": "Finished.",
        "blocked": "The work is stuck and needs your decision.",
    }
    if phase in fixed:
        return fixed[phase]
    if phase == "awaiting_checkpoint_review":
        t = titles.get(st.get("review_target"), "the last step")
        return f"\"{t}\" is ready for you to review. Try it, then approve or tell me what should change."
    cur = st.get("current")
    cps = st.get("checkpoints") or {}
    if not cur or cur not in cps:
        return "Getting the next step ready."
    n = list(cps).index(cur) + 1
    return (f"Working on step {n} of {len(cps)}, \"{titles.get(cur, cur)}\": "
            + STEP_PLAIN.get(cps[cur].get("step"), "in progress") + ".")


def exit_code_only_line(cp):
    """A plain line when a step's tests are checked by exit code only: its plan says it writes no per-test report
    (`tests.report` none), or the plan does not say (no `report` key, e.g. a plan from before reports existed)."""
    t = (cp or {}).get("tests") or {}
    if not ((cp or {}).get("gates") or {}).get("behavior") or t.get("report") in ("junit", "json"):
        return None
    if t.get("report") == "none":
        return (f"{cp['id']} has no per-test report, so its tests are checked by exit code only. "
                f"Reason: {t.get('report_reason')}")
    return (f"{cp['id']}'s plan does not say how its tests report each result, so they are checked by exit code "
            "only.")


def exit_code_only_note(plan, st):
    """exit_code_only_line for the step being built now, or None."""
    if st.get("phase") != "building":
        return None
    return exit_code_only_line(next((c for c in (plan or {}).get("checkpoints") or []
                                     if c.get("id") == st.get("current")), None))


def waiting_question(ctx: Ctx, st):
    """The question that has been asked (`ratchet.py ask ...`) and not yet answered or withdrawn, or None. Read-only.
    A question asked in an earlier phase no longer applies (as in `_asked_reply`), so it is None. In a user lane a
    question the user has replied to is answered, so it is dropped even before the agent runs the command that
    clears `pending`; in a director lane a chat reply answers nothing, so the question shows until `pending`
    clears. `stops_run` says whether the run really waits for it: only the approval pauses, a blocked run's
    questions, the accept-a-finding / raise-the-limit questions of a blocked review round and the accept-a-line
    question of a held finish (CP5, `about` names its lines) do (True); the others
    (a review-frequency or cancel question, a building-phase choice...) leave a building run building (False);
    approve-changes and the like, and the carry-on kinds outside the building phase, are left unsaid (None)."""
    p = st.get("pending")
    if not isinstance(p, dict) or not p.get("action") or not p.get("asked_at"):
        return None
    if p.get("phase") and p["phase"] != st.get("phase"):
        return None
    if p.get("overtook"):  # printed as NOT ASKED: it never reached the user, and the question it replaced is gone too
        return None
    if p["action"] == "human-approve" and _finish_held(ctx, st):
        return None  # CP4: the finish is held, so a question asked before it was held no longer waits
    waiting_on = _question_waiter(ctx, p["action"] == "claim")
    if waiting_on == "user" and _question_answered(ctx, st, p):
        return None
    _m, _t = _pq(p)
    about = (_t or _m) if p["action"] in ("accept-finding", "raise-limit", "ui-respec") else None
    if p["action"] == "accept-line":
        about = _lines_word([int(x) for x in str(_t or "").split(",") if x.strip().isdigit()]) or None
    stops = None
    if p["action"] in ALWAYS_GATING_QUESTIONS or (p["action"] in GATING_QUESTIONS and st.get("phase") in GATING_PHASES):
        stops = True
    elif p["action"] in CARRY_ON_QUESTIONS and st.get("phase") == "building":
        stops = False
    return {"kind": p["action"], "since": p["asked_at"], "waiting_on": waiting_on, "about": about,
            "stops_run": stops}


def question_line(q):
    """The one plain-status line for a waiting question."""
    t = _dt.datetime.fromisoformat(q["since"]).astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    who = "the director's" if q["waiting_on"] == "director" else "the user's"
    about = f" for {q['about']}" if q.get("about") else ""
    tail = {True: " The run will not move until it is answered.",
            False: " The run can carry on meanwhile."}.get(q.get("stops_run"), "")
    return (f"Waiting for {who} answer: a question about {QUESTION_WORDS.get(q['kind'], q['kind'])}{about} "
            f"was asked at {t}.{tail}")


def _lines_word(nums) -> str:
    """'line 2' or 'lines 2, 3': the plain words for a list of 1-based done-means line numbers."""
    nums = list(nums)
    return "line" + ("s " if len(nums) != 1 else " ") + ", ".join(str(x) for x in nums)


def _accepted_lines(st) -> dict:
    """CP5: the done-means lines the user (or the director, in an enrolled lane) accepted as unproven with
    `accept-line`, by 1-based line number: {n: {"number", "text", "reason", "accepted_by", "accepted_at",
    "covered"}} ("covered": the line's `reasons` at the moment it was accepted, what the acceptance covered), read
    from state.json's `accepted_lines` (a dict keyed by the line number as a string, written by cmd_accept_line
    only). {} when none, or when the key has some other shape. Read by `_apply_accepted_lines` and
    `_ask_accept_line` (a line that reads "accepted" cannot be asked about again; one held again can)."""
    acc = st.get("accepted_lines")
    out = {}
    if not isinstance(acc, dict):
        return out
    for k, v in acc.items():
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict):
            out[n] = v
    return out


def _apply_accepted_lines(st, lines: list) -> list:
    """CP5: marks the accepted lines in a `_done_means_proof` lines list, in place, and returns it. An entry whose
    verdict is "unproven" and whose number the user accepted (`_accepted_lines`) gets verdict "accepted" and an
    "accepted" record {"reason", "accepted_by", "accepted_at", "covered"} ONLY while every one of its current
    `reasons` is in the record's "covered" list (the reasons at accept time): its `reasons` stay (what the
    acceptance covered, printed by `_accepted_line_text`). A line that is unproven for a reason the acceptance did
    not cover (a linked test that failed since, a NEW open finding naming the line, a link removed) is HELD AGAIN:
    it keeps verdict "unproven", gets an "accepted_earlier" record and one more plain reason string starting with
    HELD_AGAIN_PREFIX that names the earlier acceptance, what it covered and what is new, so status, status --json,
    the receipt, `_finish_blockers` and every refusal built on them show it, and `ask accept-line` may be run for
    it again. A record without "covered" (none is written any more) covers whatever holds the line. A line that is
    proven stays "proven" even when it was accepted earlier (the tests prove it now), and a line never accepted is
    untouched, so acceptance is per line and never a blanket flag. The one place the verdict "accepted" is made:
    `done_means_table` (status, status --json, `_finish_blockers`, which then no longer holds the finish on that
    line) and cmd_receipt both call it."""
    acc = _accepted_lines(st)
    for e in lines:
        rec = acc.get(e.get("number"))
        if not rec or e.get("verdict") != "unproven":
            continue
        record = {k: rec.get(k) for k in ("reason", "accepted_by", "accepted_at", "covered")}
        covered = rec.get("covered")
        reasons = e.get("reasons") or []
        new = [r for r in reasons if r not in covered] if isinstance(covered, list) else []
        if not new:
            e["verdict"] = "accepted"
            e["accepted"] = record
            continue
        who = "the director" if rec.get("accepted_by") == "director" else "the user"
        e["accepted_earlier"] = record
        why = " ".join(str(rec.get("reason") or "").split())
        e["reasons"] = list(reasons) + [
            f"{HELD_AGAIN_PREFIX}{who} accepted this line on {rec.get('accepted_at') or '?'} (reason: {why}) when "
            f"it was held by: {'; '.join(covered) or 'nothing'}; that acceptance does not cover what holds it now: "
            f"{'; '.join(new)}"]
    return lines


def done_means_table(ctx, plan, st):
    """The final review's done-means table, one source for plain `status`, `status --json`'s "done_means_table",
    the receipt and the finish check (`_finish_blockers`): the list of per-line entries from _done_means_proof
    ("number", "text", "verdict", "cases", "reasons"; verdict "accepted" with an "accepted" record for an unproven
    line the user accepted, `_apply_accepted_lines`, CP5), or one plain note string instead of a list when there is
    nothing to tabulate (no objective at all: NO_OBJECTIVE_NOTE; an objective with no done-means lines:
    DM_NO_LINES_NOTE; no test linked to any line on an OLDER plan: DM_NO_TESTS_NOTE). The lines are the
    APPROVED ones (`_plan_as_approved`), never a later edit of checkpoints.json. A plan approved under the
    done-means rules (`_approved_after_dm`) always gets the list: when no case links a line any more (every
    `proves` link removed after approval) each line is "unproven" with "no test linked", so the finish check
    (`_unproven_finish_lines`) holds on every line instead of reading a note as nothing to block."""
    plan = _plan_as_approved(plan, st)
    marked = _approved_after_dm(st)
    dm = _done_means_proof(ctx, plan, st.get("checkpoints") or {}, older_plan_note=not marked)
    if dm is None:
        return _dm_none_note(plan)
    return _apply_accepted_lines(st, dm["lines"]) if dm["any_proves"] or marked else DM_NO_TESTS_NOTE


def _unproven_finish_lines(ctx, st) -> list:
    """The final review table's entries whose verdict is "unproven" (a linked test failed or has no result, a
    browser case no UI gate checked, no test linked, or an open finding names the line), read from the approved
    lines (`done_means_table`); a line the user accepted reads "accepted" there (CP5), so it is not in this list,
    while a line held again after its acceptance (a reason the acceptance did not cover) is. [] when the table is
    a plain note instead (no objective, no done-means lines, or
    an OLDER plan whose tests link to no line; a plan under the done-means rules whose links were all removed
    gets every line as unproven instead, never a note) or every line is proven. Shared by `_finish_blockers` and
    `_finish_legacy_note`, so the block and the older-run note judge the same lines."""
    table = done_means_table(ctx, ctx.plan() or {"checkpoints": []}, st)
    if not isinstance(table, list):
        return []
    return [e for e in table if e.get("verdict") == "unproven"]


def _finish_blockers(ctx, st) -> list:
    """CP4: the done-means lines that hold the finish, one table entry each ("number", "text", "cases",
    "reasons"), or [] when the run may be marked done. Non-empty only for a plan approved under the done-means
    rules (`_approved_after_dm`) with at least one unproven line; a run approved before this change
    (no marker) never blocks here and gets `_finish_legacy_note` instead. A line the user accepted with
    `accept-line` (CP5, verdict "accepted") no longer holds the finish; every other unproven line still does,
    including an accepted line held again by a reason the acceptance did not cover (`_apply_accepted_lines`: its
    entry carries the "held again" reason). One verdict, read at the moment it
    matters and never cached: cmd_human_approve (a chat yes and a director token alike, before any counter, gate
    or round changes, and only where the approval would finish the run: `_finish_pending`; its one state change
    is dropping a pending human-approve question, so a yes given before the line went unproven never finishes
    the run later), `_approval_question` (so `ask human-approve` prints no question while blocked), next_action,
    cmd_status_text / status_json (the lines and the ways forward), `plain_now` (the held-finish sentence),
    `waiting_question` (a pending human-approve question no longer waits), the prompt hook (`decide_prompt`:
    never a hint to ask or run human-approve while held) and cmd_complete's last-step move in human_review `none`
    mode (which then stops at the final review instead of finishing). The review-limit unblock path (phase
    `blocked`) never reads it."""
    if not _approved_after_dm(st):
        return []
    return _unproven_finish_lines(ctx, st)


def _finish_integrity_lines(ctx, st) -> list[str]:
    """CP4: what the agent is told at the finish pause (`_finish_pending`) when a file that decides the done-means
    verdicts changed after the user approved the plan: a test plan (tests/CPn.json, whose `proves` links the
    table reads as they are on disk, so an edit after approval could re-point a line at a passed case) or a
    checkpoint in checkpoints.json (the step the cases belong to). Compared through the approval snapshot's own
    "cps" and "tests" hashes against `approval_snapshot`, and NOTHING else `integrity_problems` watches: an edit
    to standards.md or the design file, a config rule, a custom reviewer, the spec, the host agent CLI or the
    review mode cannot change a verdict, so none of them holds the finish (they were never checked at this pause
    before CP4, and the building-phase `verify_integrity` still catches them where they matter). Only for a plan
    approved under the done-means rules (`_approved_after_dm`): a run approved before this change is never held
    here, so it finishes as before (objective line 7). Until the user confirms the change (`approve-changes`,
    after which the table reads the links they confirmed) or it is undone, the run cannot be marked done and the
    approval question is not asked. [] when nothing relevant changed, or for a run with no approval yet. Printed by
    cmd_status_text and next_action; the same text is the refusal in cmd_human_approve and `_approval_question`;
    `_finish_held` folds it into the one held-finish verdict plain_now, waiting_question and the prompt hook
    read."""
    snap = st.get("approved")
    if not snap or not _approved_after_dm(st):
        return []
    now_ = approval_snapshot(ctx, st)
    probs = [f"checkpoint {cid} in checkpoints.json" for cid, h in snap.get("cps", {}).items()
             if now_["cps"].get(cid) != h]
    probs += [f"test plan tests/{cid}.json" for cid, h in snap.get("tests", {}).items()
              if now_["tests"].get(cid) != h]
    if not probs:
        return []
    return ["The run cannot be marked done yet: these changed after the user approved the plan: " + "; ".join(probs)
            + ". The done-means table reads each test plan's `proves` links and its step as they are now, so the "
            "approval question is not asked until the change is settled.",
            "Ways forward: undo the change, or show the user exactly what changed and run `ratchet.py ask "
            "approve-changes`; run `ratchet.py approve-changes` only if their first reply after it is a listed yes. "
            "Never ask them to approve the finish while this stands."]


def _finish_held(ctx, st) -> bool:
    """CP4: True when the finish pause (`_finish_pending`) is held for either reason: an unproven done-means line
    (`_finish_blockers`) or a checkpoint or test plan edited after approval (`_finish_integrity_lines`). The one
    verdict `plain_now` (the held sentence instead of an invitation to approve), `waiting_question` (a pending
    human-approve question no longer waits), the prompt hook (`decide_prompt`: never a hint to ask for or run the
    approval) and cmd_human_approve's pending-question drop read, so no output invites an approval that
    `human-approve` would refuse. False at every other pause."""
    return _finish_pending(st) and bool(_finish_blockers(ctx, st) or _finish_integrity_lines(ctx, st))


def _finish_legacy_note(ctx, st) -> str:
    """The plain note for a run approved before unproven lines blocked the finish: when its plan has no
    `done_means_rules` marker and at least one line is unproven, the one sentence `status` (at the finish
    pause) and `complete` (human_review `none`, last step) print instead of a block. "" for a plan under the
    done-means rules, or with nothing unproven."""
    if _approved_after_dm(st) or not _unproven_finish_lines(ctx, st):
        return ""
    return ("Note: this run's plan was approved before unproven done-means lines blocked the finish, so its "
            "unproven lines are shown but do not stop the run from being marked done.")


def _finish_block_lines(ctx, st, blockers: list) -> list[str]:
    """What the agent is told while `_finish_blockers` holds the finish (the human-approve and ask refusals,
    next_action, status and complete all print these same lines): that the run cannot be marked done yet, each
    blocking line by number and its approved text with its reasons (the table's own: "<id> failed", "<id> has no
    result", "open finding <key>: ..."), for each open finding the step it belongs to and the mark-fixed command
    that closes it once a clean review round confirms the fix, and the ways forward, one per kind of reason, each
    a path that really clears it: an open finding is closed with mark-fixed; a line with no test linked gets a
    step through feedback whose tests prove it; a linked test that failed, has no result, or is a browser case no
    UI gate checked sits in a step that is already done, whose tests are not re-run, so no new step can clear it
    (every test linked to a line must pass) and the agent is told to say so plainly instead of being sent down
    that path. Never an instruction to put the approval question to the user: that question is not asked while a
    line is unproven. CP5: also the one way the user themselves can release a line (`ask accept-line <n>`, their
    own `accept: <reason>` reply, or a director token in an enrolled lane) and, while that question is waiting
    (`waiting_question`), one line saying so, so `next` and `status` name what the run waits for; once the reply
    has landed (the question no longer waits, `pending` still accept-line), one line pointing at `accept-line` to
    record it, never back at `ask accept-line`."""
    n = len(blockers)
    out = [f"The run cannot be marked done yet: {n} done-means line{'s are' if n != 1 else ' is'} unproven, so the "
           "finish is held and the approval question is not asked."]
    q = waiting_question(ctx, st)
    p = st.get("pending") if isinstance(st.get("pending"), dict) else {}
    if q and q.get("kind") == "accept-line":
        out.append(f"A question to accept {q.get('about') or 'a line'} as unproven is waiting for the user's reply "
                   "(exactly `accept: <reason>`, their first reply after it); when it arrives, run `ratchet.py "
                   "accept-line`.")
    elif p.get("action") == "accept-line" and not p.get("overtook"):
        # the reply landed (so the question no longer "waits") but accept-line has not read it yet: keep pointing
        # at the command that records it, never back at `ask accept-line` (a re-ask would ask the user again)
        about = _lines_word([i for i in (p.get("ids") or []) if isinstance(i, int)]) or "a line"
        out.append(f"The user has replied to your question to accept {about} as unproven: run `ratchet.py "
                   "accept-line` now to record it (it accepts only a first reply in the exact form "
                   "`accept: <reason>`; any other first reply closes the question). Do not ask it again first.")
    cps = st.get("checkpoints") or {}
    dm_n = len(_approved_done_means(st)) or max((int(e.get("number") or 0) for e in blockers), default=0)
    unlinked, stuck = [], []
    for e in blockers:
        reasons = e.get("reasons") or []
        out.append(f"  - line {e['number']}: \"{e['text']}\": {'; '.join(reasons) or 'unproven'}")
        for cid, c in cps.items():
            for f in left_for_later(c):
                if e["number"] in _finding_dm_lines(f, dm_n):
                    key = _finding_key(f)
                    out.append(f"    finding {key} on {cid} is open: fix it, get a clean review round, then run "
                               f"`ratchet.py mark-fixed {cid} {key}=\"<how it was fixed>\"`.")
        if "no test linked" in reasons:
            unlinked.append(e["number"])
        if any(not r.startswith(("open finding ", HELD_AGAIN_PREFIX)) and r != "no test linked" for r in reasons):
            stuck.append(e["number"])

    _lines = _lines_word
    out.append("Ways forward: tell the user plainly which lines are unproven and why. An open finding is closed with "
               "mark-fixed as above. If the user asks for changes, `ratchet.py feedback` is still open. A line is "
               "proven only when every test linked to it passed and no open finding names it.")
    if unlinked:
        out.append(f"  {_lines(unlinked)}: no test is linked, so if the user asks for the work, run `ratchet.py "
                   "feedback` and plan a step whose tests prove the line.")
    if stuck:
        out.append(f"  {_lines(stuck)}: a linked test failed, has no result or is a browser test no UI gate checked, "
                   "in a step that is already done. A done step's tests are not re-run and every test linked to a "
                   "line must pass, so a new step cannot clear this: do not offer one for it. Say so plainly.")
    out.append("  If the user decides a line may stay unproven, run `ratchet.py ask accept-line <n>[,<n>]` and show the "
               "question it prints; only their own first reply `accept: <reason>` (in a director-enrolled lane, a "
               "director token with --reason) accepts it, and an accepted line no longer holds the finish. You "
               "cannot accept a line yourself.")
    return out


# --------------------------------------------------------------------------- schedule numbers
#
# schedule_numbers/step_stall work out, from what the run already records in state.json (and the config), how long
# the remaining steps will probably take, and whether the current step has gone too long with no gate progress.
# Pure: no disk, git or subprocess access, and no reading of the real clock -- `now` (a timezone-aware datetime) is
# always passed in. Everything is wall-clock time, waits inside a step included, in UTC. The projected finish, its range and the
# per-step average are never finer than 5 minutes; the stall warning states the real elapsed time (and its
# "since" time) to the minute.

STALL_BUDGET_DEFAULT = 90
SCHEDULE_ROUND_MINUTES = 5


def _sched_time(value):
    """A recorded timestamp as an aware UTC datetime; None when it is not text or not ISO. A text with no zone
    is read as UTC (the way the engine writes its own)."""
    if not isinstance(value, str):
        return None
    d = _ratchet_module()._parse_iso(value.strip())  # the engine's own reader: "Z" suffix, naive read as UTC
    return d.astimezone(_dt.timezone.utc) if d is not None else None


def _sched_round(d, now):
    """`d` rounded to the nearest 5-minute mark (zero seconds), never earlier than `now`."""
    step = SCHEDULE_ROUND_MINUTES * 60
    secs = d.timestamp()
    out = _dt.datetime.fromtimestamp(round(secs / step) * step, tz=_dt.timezone.utc)
    if out < now:
        out += _dt.timedelta(seconds=step)
    return out


def _sched_iso(d):
    return d.isoformat()


def _sched_clock(d):
    return d.strftime("%Y-%m-%d %H:%M UTC")


def _sched_dur(seconds):
    """A length of time as '1h 40m', rounded to 5 minutes, spelled by the engine's own _plain_duration (the one the
    STALL text uses), so the projected-finish line and the stall line never spell the same length two ways."""
    mins = int(round(max(0, seconds) / 60 / SCHEDULE_ROUND_MINUTES)) * SCHEDULE_ROUND_MINUTES
    return _ratchet_module()._plain_duration(mins * 60)


def _sched_num(x):
    return f"{x:.1f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def _sched_attempts(c):
    gates = c.get("gates") if isinstance(c.get("gates"), dict) else {}
    adv = gates.get("adversarial") if isinstance(gates.get("adversarial"), dict) else {}
    a = adv.get("attempts")
    return a if isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0 else 0


def _sched_int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def schedule_numbers(st, plan_ids, cfg, now) -> dict:
    """The projected finish and review-round numbers for a run, from state.json alone -- see the section note."""
    cps = st.get("checkpoints") if isinstance(st, dict) else None
    cps = cps if isinstance(cps, dict) else {}
    ids = [i for i in (plan_ids or [])]
    spans, rounds = [], []
    for cid in ids:
        c = cps.get(cid)
        if not isinstance(c, dict) or c.get("status") != "done":
            continue
        a, b = _sched_time(c.get("started_at")), _sched_time(c.get("completed_at"))
        if a is None or b is None or b < a:
            continue
        spans.append((b - a).total_seconds())
        rounds.append(_sched_attempts(c))
    finished = len(spans)
    open_ids = [i for i in ids if not (isinstance(cps.get(i), dict) and cps[i].get("status") == "done")]
    left = len(open_ids)
    cur = st.get("current") if isinstance(st, dict) else None
    cur_open = cur in open_ids
    cur_start = _sched_time(cps[cur].get("started_at")) if cur_open and isinstance(cps.get(cur), dict) else None
    sources = ["checkpoints[].status", "checkpoints[].started_at", "checkpoints[].completed_at"]
    if cur_open:
        sources.append("current")
    cfg = cfg if isinstance(cfg, dict) else {}
    limit = _sched_int(cfg.get("max_adversarial_rounds_run"))
    used = _sched_int(st.get("adv_rounds_total", 0)) if isinstance(st, dict) else None
    allowed = max(0, limit - (used or 0)) if limit is not None else None
    if limit is not None:
        sources += ["adv_rounds_total", "max_adversarial_rounds_run"]
    avg = expected = None
    if finished:
        sources.insert(3, "checkpoints[].gates.adversarial.attempts")
        avg = round(sum(rounds) / finished, 2)
        expected = round(avg * left, 2)
    label = "none" if not finished else ("rough" if finished < 3 else "estimate")
    finish = earliest = latest = None
    notes = []
    if finished and left:
        elapsed = (now - cur_start).total_seconds() if cur_start is not None else 0
        if cur_open and cur_start is None:
            notes.append("the current step's start time is unreadable, so it is counted as only just started")

        def at(span):
            rest = max(0, span - elapsed) if cur_open else 0
            n = left - 1 if cur_open else left
            return _sched_round(now + _dt.timedelta(seconds=rest + n * span), now)

        mean = sum(spans) / finished
        finish, earliest, latest = at(mean), at(min(spans)), at(max(spans))
    plural = "step" if finished == 1 else "steps"
    if not finished:
        text = f"no estimate yet: {finished} finished steps to work from"
    elif not left:
        text = f"nothing is left to build ({finished} finished {plural})"
    else:
        word = "rough estimate" if label == "rough" else "estimate"
        text = (f"{word} {_sched_clock(finish)} (between {_sched_clock(earliest)} and {_sched_clock(latest)}), "
                f"resting on {finished} finished {plural} that took about {_sched_dur(sum(spans) / finished)} each "
                "(wall-clock time, waits inside a step included)")
    if notes:
        text += "; " + "; ".join(notes)
    if avg is not None and left:
        er = _sched_num(float(expected))
        text += f". Review rounds: about {er} more expected ({_sched_num(float(avg))} per finished step x {left} left)"
        if allowed is not None:
            if expected > allowed:
                text += f", more than the {allowed} the run's review limit still allows"
            else:
                text += f", within the {allowed} the run's review limit still allows"
    return {"label": label, "finished": finished, "left": left,
            "finish": _sched_iso(finish) if finish else None,
            "earliest": _sched_iso(earliest) if earliest else None,
            "latest": _sched_iso(latest) if latest else None,
            "avg_rounds": avg, "expected_rounds_left": expected, "allowed_rounds_left": allowed,
            "sources": sources, "text": text}


def stall_budget(cfg):
    """(minutes, note): the stall budget (config stall_budget_minutes, 90 unless set) and, when the setting is there
    but not a positive number, a note saying it was ignored (else None). Lets a caller state the note even while the
    step is under budget, when step_stall itself returns None."""
    if not isinstance(cfg, dict) or "stall_budget_minutes" not in cfg:
        return STALL_BUDGET_DEFAULT, None
    raw = cfg["stall_budget_minutes"]
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return raw, None
    return STALL_BUDGET_DEFAULT, f"stall_budget_minutes ({raw!r}) was ignored, {STALL_BUDGET_DEFAULT} minutes used"


def step_stall(st, cfg, now):
    """None when the current step is not stalled, else a dict naming it (cp, step, elapsed_seconds, since,
    budget_minutes, note, text). Stalled = phase building and longer than the stall budget (config
    stall_budget_minutes, 90 unless set) since the latest of the step's start, the latest progress event for it in
    the history, and the latest run-level resumed/unblocked entry."""
    if not isinstance(st, dict) or st.get("phase") != "building":
        return None
    cur = st.get("current")
    cps = st.get("checkpoints") if isinstance(st.get("checkpoints"), dict) else {}
    c = cps.get(cur) if isinstance(cur, str) else None
    if not isinstance(c, dict):
        return None
    progress = _ratchet_module().PROGRESS_EVENTS
    marks = []
    t = _sched_time(c.get("started_at"))
    if t is not None:
        marks.append((t, "step start"))
    hist = st.get("history") if isinstance(st.get("history"), list) else []
    for e in hist:
        if not isinstance(e, dict):
            continue
        ev = e.get("event")
        if (ev in progress and e.get("cp") == cur) or ev in ("resumed", "unblocked"):
            t = _sched_time(e.get("at"))
            if t is not None:
                marks.append((t, ev))
    if not marks:
        return None
    since, since_event = marks[0]
    for t, ev in marks[1:]:
        if t >= since:  # the latest mark; on a tie the history entry, which says more than the step start
            since, since_event = t, ev
    budget, note = stall_budget(cfg)
    elapsed = int((now - since).total_seconds())
    if elapsed <= budget * 60:
        return None
    step = c.get("step")
    plain = STEP_PLAIN.get(step, step) if isinstance(step, str) else step
    text = (f"STALL: {cur} has been {plain} for {_ratchet_module()._plain_duration(elapsed)} with no gate progress "
            f"(budget {budget} minutes, since {_sched_clock(since)})")
    return {"cp": cur, "step": step, "elapsed_seconds": elapsed, "since": _sched_iso(since),
            "since_event": since_event, "budget_minutes": budget, "note": note, "text": text}


def stall_view(st, cfg, now):
    """The STALL warning as `status --json` (schedule.stall) and `next` (stall) carry it: None when nothing is stalled,
    else {checkpoint, step, elapsed_minutes, budget_minutes, since, since_event, message}. `message` is the one plain
    'STALL: ...' line `status` prints; it also names a declared wait (`ratchet.py wait`) and a budget setting that
    was ignored. Pure apart from reading the wait through the engine's live_wait."""
    r = step_stall(st, cfg, now)
    if r is None:
        return None
    text = r["text"]
    if r.get("note"):
        text += f"; {r['note']}"
    w = _ratchet_module().live_wait(st)
    if w:
        text += f"; a wait is declared: {w['reason']} (until {w['until'][:16].replace('T', ' ')} UTC)"
    return {"checkpoint": r["cp"], "step": r["step"], "elapsed_minutes": r["elapsed_seconds"] // 60,
            "budget_minutes": r["budget_minutes"], "since": r["since"], "since_event": r["since_event"], "message": text}


def path_numbers(st, plan_ids, now) -> dict:
    """The critical path (each step from its started_at to its completed_at; the open one to `now`), the time outside
    it (created_at to the first step's start, plus the waits between one step's finish and the next one's start) and
    the cost of one more review round (the mean gap between consecutive gates.adversarial.rounds[].at inside one
    step), from state.json alone. {critical_path, outside, review_round}; review_round.finish_if_one_more is None here
    (schedule_status fills it from the projected finish). A time that cannot be read is left out, never guessed,
    and no figure is negative. Time blocked stays inside its step: the record does not log when a block started."""
    st = st if isinstance(st, dict) else {}
    cps = st.get("checkpoints") if isinstance(st.get("checkpoints"), dict) else {}
    ids = list(plan_ids or [])
    live = st.get("phase") not in ("done", "cancelled")
    end_open = now if live else _sched_time(st.get("updated_at"))
    steps, spans, bad, total, gaps = [], {}, [], 0, []
    open_used = False  # an open step was closed at `now` (live run) or at updated_at (done/cancelled run)
    for cid in ids:
        c = cps.get(cid) if isinstance(cps.get(cid), dict) else {}
        done = c.get("status") == "done"
        if not (done or c.get("started_at") or c.get("status") == "in_progress"):
            steps.append({"id": cid, "seconds": 0, "finished": False, "started": False, "unreadable": False})
        else:
            a = _sched_time(c.get("started_at"))
            b = _sched_time(c.get("completed_at")) if done else end_open
            open_used = open_used or not done
            if a is None or b is None:
                bad.append(cid)
                steps.append({"id": cid, "seconds": None, "finished": done, "started": True, "unreadable": True})
            else:
                sec = int(max(0, (b - a).total_seconds()))
                total += sec
                spans[cid] = (a, b)
                steps.append({"id": cid, "seconds": sec, "finished": done, "started": True, "unreadable": False})
        adv = (c.get("gates") or {}).get("adversarial") if isinstance(c.get("gates"), dict) else None
        rounds = adv.get("rounds") if isinstance(adv, dict) and isinstance(adv.get("rounds"), list) else []
        at = [_sched_time(r.get("at")) if isinstance(r, dict) else None for r in rounds]
        gaps += [(y - x).total_seconds() for x, y in zip(at, at[1:]) if x is not None and y is not None and y >= x]
    created = _sched_time(st.get("created_at"))
    planning = None
    plan_open = False  # planning ran to `now` / updated_at because the first step never started
    if created is not None and steps:
        if steps[0]["id"] in spans:
            planning = int(max(0, (spans[steps[0]["id"]][0] - created).total_seconds()))
        elif not steps[0]["started"] and end_open is not None:
            planning = int(max(0, (end_open - created).total_seconds()))
            plan_open = True
    waits = 0
    for i in range(len(steps) - 1):
        if steps[i]["id"] not in spans:
            continue
        nxt = steps[i + 1]
        if nxt["id"] in spans:
            waits += int(max(0, (spans[nxt["id"]][0] - spans[steps[i]["id"]][1]).total_seconds()))
        elif not nxt["started"] and live:  # waiting between steps right now, e.g. a step review in `each` mode
            waits += int(max(0, (now - spans[steps[i]["id"]][1]).total_seconds()))
    mean = int(round(sum(gaps) / len(gaps))) if gaps else None
    cp_fields = ["checkpoints[].started_at", "checkpoints[].completed_at"]
    out_fields = ["created_at", "checkpoints[].started_at", "checkpoints[].completed_at"]
    if open_used:
        cp_fields.append("phase")
    out_fields.append("phase")  # whether a step is still waiting to start right now depends on the run's phase
    if not live and open_used:
        cp_fields.append("updated_at")
    if not live and plan_open:
        out_fields.append("updated_at")
    return {"critical_path": {"seconds": total, "steps": steps, "unreadable": bad, "fields": cp_fields},
            "outside": {"seconds": (planning or 0) + waits, "planning_seconds": planning, "waits_seconds": waits,
                        "fields": out_fields},
            "review_round": {"seconds": mean, "gaps": len(gaps), "enough_data": bool(gaps), "finish_if_one_more": None,
                             "fields": ["checkpoints[].gates.adversarial.rounds[].at"]}}


def path_lines(sched, phase) -> list:
    """The plain lines `status` prints for the critical path: the chain, the time outside it (with the note that a
    run records no parallel side-work) and what one more review round costs. Each starts 'Critical path:' or 'One
    more review round:'. [] when the schedule has no such numbers or the plan has no steps."""
    cp = sched.get("critical_path") if isinstance(sched, dict) else None
    ou, rr = sched.get("outside"), sched.get("review_round")
    if not (isinstance(cp, dict) and isinstance(ou, dict) and isinstance(rr, dict) and cp.get("steps")):
        return []
    dur = _ratchet_module()._plain_duration
    steps = cp["steps"]
    if not any(s["started"] for s in steps):
        chain = "Critical path: no step has started yet, so the chain of steps has no time to measure."
    else:
        chain = f"Critical path: {dur(cp['seconds'])} in the steps (each from its start to its finish"
        if any(s["started"] and not s["finished"] and not s["unreadable"] for s in steps):
            if phase in ("done", "cancelled"):  # the open step was closed at updated_at, not counted up to now
                chain += ", an unfinished step counted up to when the run stopped"
            else:
                chain += ", the current step counted so far"
        chain += ")"
        if cp.get("unreadable"):
            chain += f"; time unreadable, left out: {', '.join(cp['unreadable'])}"
        if phase == "blocked":
            chain += "; time blocked is counted inside the step, because the record does not log when a block started"
        chain += "."
    plan = "planning and approval unreadable" if ou["planning_seconds"] is None else \
        f"planning and approval {dur(ou['planning_seconds'])}"
    outside = (f"Critical path: {dur(ou['seconds'])} outside it ({plan}, waits between steps {dur(ou['waits_seconds'])}). "
               "This run records no parallel side-work; `lanes` shows work running in parallel.")
    if not rr["enough_data"]:
        more = ("One more review round: not enough data yet (a step needs at least 2 review rounds recorded to "
                "measure one).")
    else:
        n = rr["gaps"]
        more = (f"One more review round: about {dur(rr['seconds'])} (the average gap between a step's review rounds, "
                f"resting on {n} {'gap' if n == 1 else 'gaps'} in this run); ")
        if rr["finish_if_one_more"]:
            more += f"one more round would move the projected finish to {rr['finish_if_one_more']}."
        elif sched.get("steps_left") == 0:
            more += "no steps are left to build, so there is no finish to move."
        elif phase in ("done", "cancelled"):
            more += "the run has stopped, so there is no finish to move."
        else:
            more += "no estimate yet of the finish it would move."
    return [chain, outside, more]


def review_budget_line(plan, st, cfg) -> str:
    """The one plain 'Review budget:' line `plan-summary` prints: how many steps the plan has, how many review rounds
    each may use (config max_adversarial_rounds; a step's own gates.adversarial.cap_raised_to counted instead where
    it is set), the total, and the run-wide limit (max_adversarial_rounds_run). Says a round limit is not a time
    budget, and that the run-wide limit stops the run first when the total is bigger than it. Pure."""
    cps = plan.get("checkpoints") if isinstance(plan, dict) else None
    cps = [c for c in (cps if isinstance(cps, list) else []) if isinstance(c, dict)]
    cfg = cfg if isinstance(cfg, dict) else {}
    per = _sched_int(cfg.get("max_adversarial_rounds"))
    run = _sched_int(cfg.get("max_adversarial_rounds_run"))
    state = (st.get("checkpoints") if isinstance(st, dict) else None) or {}
    caps, raised = [], []
    for c in cps:
        sc = state.get(c.get("id")) if isinstance(state, dict) else None
        adv = ((sc or {}).get("gates") or {}).get("adversarial") if isinstance(sc, dict) else None
        r = _sched_int(adv.get("cap_raised_to")) if isinstance(adv, dict) else None
        if r:
            raised.append((c.get("id"), r))
        caps.append(r or per or 0)
    n = len(cps)
    total = sum(caps)
    head = f"Review budget: {n} {'step' if n == 1 else 'steps'}, each allowed up to {per} review rounds"
    if raised:
        head += "; " + ", ".join(f"{i} has a raised limit of {r}" for i, r in raised)
    head += f", {total} rounds in all; the whole run is allowed {run} rounds"
    tail = ""
    if run is not None and total > run:
        tail = " The run-wide limit is the one that stops the run first, because the total is bigger than it."
    return (head + "." + tail + " A round limit says how many rounds are allowed, not how long they take or how much "
            "time is left.")


def schedule_status(st, plan, cfg, now) -> dict:
    """What `status` and `status --json` show as the projected finish: {steps_left, eta, rounds, sources, line, stall,
    critical_path, outside, review_round, parallel_recorded} (stall: see stall_view; the last four: see path_numbers,
    and parallel_recorded is always False -- a single run records no parallel side-work).
    `line` is the one plain 'Projected finish:' line and `eta`/`rounds` carry the same numbers (eta: finish, earliest,
    latest as 'YYYY-MM-DD HH:MM UTC', label, based_on_steps; rounds: expected, used, run_limit, over_limit; both null
    until a step has finished). Everything is null (steps_left 0 or null, sources empty) when no step is left or the
    run is done or cancelled. Pure: built from schedule_numbers, `now` is passed in."""
    st = st if isinstance(st, dict) else {}
    cps = plan.get("checkpoints") if isinstance(plan, dict) else None
    ids = [c["id"] for c in (cps if isinstance(cps, list) else []) if isinstance(c, dict) and isinstance(c.get("id"), str)]
    r = schedule_numbers(st, ids, cfg, now)
    left = r["left"]
    stall = stall_view(st, cfg, now)
    path = path_numbers(st, ids, now)
    if not left or st.get("phase") in ("cancelled", "done"):
        return {"steps_left": 0 if not left else None, "eta": None, "rounds": None, "sources": [], "line": None,
                "stall": stall, **path, "parallel_recorded": False}
    eta = rounds = None
    if r["finish"]:
        eta = {"finish": _sched_clock(_sched_time(r["finish"])), "earliest": _sched_clock(_sched_time(r["earliest"])),
               "latest": _sched_clock(_sched_time(r["latest"])), "label": r["label"], "based_on_steps": r["finished"]}
    limit = _sched_int(cfg.get("max_adversarial_rounds_run")) if isinstance(cfg, dict) else None
    used = _sched_int(st.get("adv_rounds_total", 0)) or 0
    text = r["text"]
    if r["expected_rounds_left"] is not None:
        over = None if r["allowed_rounds_left"] is None else r["expected_rounds_left"] > r["allowed_rounds_left"]
        rounds = {"expected": r["expected_rounds_left"], "used": used, "run_limit": limit, "over_limit": over}
        if limit is not None:
            text += f" ({used} of the run's {limit} rounds used so far)"
    head = f"{left} {'step' if left == 1 else 'steps'} left; "
    line = f"Projected finish: {head}{text}. Worked out from these state.json fields: {', '.join(r['sources'])}"
    rr = path["review_round"]
    if r["finish"] and rr["seconds"] is not None:  # the projected finish moved by one round's cost, added once
        moved = _sched_round(_sched_time(r["finish"]) + _dt.timedelta(seconds=rr["seconds"]), now)
        rr["finish_if_one_more"] = _sched_clock(moved)
        rr["fields"] = rr["fields"] + [x for x in r["sources"] if x != "max_adversarial_rounds_run"]  # a setting, not a state field
    return {"steps_left": left, "eta": eta, "rounds": rounds, "sources": r["sources"], "line": line, "stall": stall,
            **path, "parallel_recorded": False}

