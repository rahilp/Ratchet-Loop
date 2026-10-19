#!/usr/bin/env python3
"""
CP4 extra tests (fix round 1 of the done-means-proven run): one case per behaviour the fixes added.
    python3 tests/test_dm_finish_extra.py

Not planned cases (ids CP4-X10 ...): the planned cases live in tests/test_dm_finish.py, whose fixtures and
helpers this file reuses (_fin2, _approved, _build, _ask_yes_approve, _add_open_finding, _make_legacy, ...).
Covered here:
  X10 editing a test plan's `proves` links after approval cannot flip an unproven line: status names the changed
      file, ask human-approve prints no question, human-approve is refused; after the user confirms the change
      (approve-changes) the table reads the confirmed links and the approval works
  X11 the prompt hook never tells the agent to ask for or run human-approve while the finish is held, and a
      human-approve question asked before the line went unproven is withdrawn (status, hook, and the refusal
      clears it so a later human-approve without a fresh question is refused)
  X12 status's plain sentence never invites an approval while the finish is held (final review and the each-mode
      last-step pause); a proven run keeps the old sentence
  X13 the ways forward are worded per reason: a failed linked test in a done step is never sent to "plan a step";
      a line with no test linked is; an open finding gets mark-fixed
  X14 at the each-mode last-step pause status prints the table and status --json carries done_means_table next
      to finish_blockers; mid-run both are null
  X15 an older (legacy) each-mode run shows the table at the last step's pause with the plain note, and finishes
  X16 a none-mode run held by an open finding, once mark-fixed clears it, still needs the final approval and
      finishes through it
  X17 the docs state the none-mode rule, the per-reason ways forward and the each-mode table
  X18 plan-summary, brief and the adversarial how read the approved done-means lines once approved, and the live
      ones while the planner extends the plan
Fix round 2:
  X19 a marked run whose proves links were all removed (confirmed through approve-changes) is held naming every
      line; an older plan keeps the no-tests note and finishes
  X20 the finish hold checks only checkpoints.json and tests/CPn.json, never standards.md or the agent CLI, and
      never an older plan
  X21 while a test-plan edit holds the finish nothing invites the approval: status's sentence, the waiting
      question, the prompt hook; the refusal drops the pending question
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import test_gates as g               # noqa: E402
import test_dm_finish as fin         # noqa: E402  (fixtures and helpers)
import test_dm_table as dt           # noqa: E402  (mutate, _table, _case_result)
import test_receipt_proof as rp      # noqa: E402  (_write_report)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP4-X\d+)\]")

LINE1, LINE2, LINE3, REWORDED, DM2, DM3, YES = fin.LINE1, fin.LINE2, fin.LINE3, fin.REWORDED, fin.DM2, fin.DM3, fin.YES
FINISH_QUESTION, FIN2_STEPS, PASSED2, FAILED2 = fin.FINISH_QUESTION, fin.FIN2_STEPS, fin.PASSED2, fin.FAILED2
BLOCKED_RE, ASK_INSTRUCTION_RE, LEGACY_NOTE_RE = fin.BLOCKED_RE, fin.ASK_INSTRUCTION_RE, fin.LEGACY_NOTE_RE
_out, _state, _clean, _refused, _names = fin._out, fin._state, fin._clean, fin._refused, fin._names
INVITE_RE = re.compile(r"then approve|approve or tell me", re.I)
HELD_SENTENCE_RE = re.compile(r"cannot be approved as done yet", re.I)
PLAN_A_STEP_RE = re.compile(r"plan a step whose tests prove", re.I)
NO_NEW_STEP_RE = re.compile(r"new step cannot clear", re.I)


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


def _repoint_proves(d, t01, t02):
    """The agent 'tidies' tests/CP1.json after approval: CP1-T01 proves `t01`, CP1-T02 proves `t02`."""
    p = d / ".ratchet/runs/f/tests/CP1.json"
    tp = json.loads(p.read_text())
    for c in tp["cases"]:
        c["proves"] = list(t01 if c["id"] == "CP1-T01" else t02)
    p.write_text(json.dumps(tp))


def _approve_changes(d):
    ask = g.rx(d, "ask", "approve-changes")
    g.say(d, YES)
    return ask, g.rx(d, "approve-changes")


def _sjson(d):
    r = g.rx(d, "status", "--json")
    try:
        return r, json.loads(r.stdout)
    except Exception:
        return r, {}


def _held_by_finding(name, human_review="end"):
    """A run whose only block is the open finding S1 naming line 2 (every linked test passed): built with S1 as a
    deferred minor, S1 then names line 2 in state, the report says both cases passed, and the step completes."""
    d = fin._approved(name, human_review=human_review, extra_files={"docs/NOTES.md": "notes\n"})
    s1 = {"id": "S1", "severity": "minor", "kind": "docs-out-of-sync", "file": "docs/NOTES.md",
          "description": fin.FIND_DESC}
    fin._build(d, 1, FIN2_STEPS[0], findings=[s1], complete=False)
    # the fix and the clean round that confirms it happen before the step completes (as in CP4-T15), so
    # mark-fixed has a later round on the changed code to point at
    (d / "docs/NOTES.md").write_text("notes\nthe length message is documented now\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "document the message")
    rb = g.rx(d, "behavior", "CP1")
    assert rb.returncode == 0, _out(rb)
    rnd2 = g.open_round(d, "CP1")
    paths = [g.write_review(d, rnd2, rid, []) for rid in {rv["id"] for rv in rnd2["reviewers"]}]
    ra = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert ra.returncode == 0, _out(ra)
    rp._write_report(d, "CP1", PASSED2)

    def name_line(st):
        for f in st["checkpoints"]["CP1"].get("deferred") or []:
            if f.get("id") == "S1":
                f["done_means"] = [2]
    dt.mutate(d, name_line)
    r = g.rx(d, "complete", "CP1")
    return d, r


# ------------------------------------------------------------------------------------------------ cases

def cp4_x10_editing_proves_links_after_approval_cannot_flip_a_line():
    """[CP4-X10] Re-pointing `proves` in tests/CP1.json after approval cannot turn line 2 proven."""
    d, _ = fin._fin2("x10")
    _repoint_proves(d, [1, 2], [])
    sr = g.rx(d, "status")
    sout = _out(sr)
    check("[CP4-X10] status exits 0 with no traceback", _clean(sr), sout[-600:])
    check("[CP4-X10] status says the run cannot be marked done", BLOCKED_RE.search(sout) is not None, sout[-1200:])
    check("[CP4-X10] status names the changed test plan and approve-changes as the way to settle it",
          "tests/CP1.json" in sout and "approve-changes" in sout, sout[-1200:])
    check("[CP4-X10] status carries no ask-for-approval instruction", ASK_INSTRUCTION_RE.search(sout) is None, sout[-1200:])
    nout = _out(g.rx(d, "next"))
    check("[CP4-X10] next names the changed test plan and no ask instruction",
          "tests/CP1.json" in nout and ASK_INSTRUCTION_RE.search(nout) is None, nout[-900:])
    ask, ha = fin._ask_yes_approve(d)
    aout = _refused("[CP4-X10]", ask, "ask human-approve")
    check("[CP4-X10] ask human-approve prints no question", "ASK THE USER" not in aout, aout)
    hout = _refused("[CP4-X10]", ha, "human-approve")
    check("[CP4-X10] the refusal names the changed test plan", "tests/CP1.json" in hout, hout[-900:])
    fin._still_waiting("[CP4-X10]", d)
    # the user confirms the change: the table then reads the confirmed links, and the approval works
    ask2, ac = _approve_changes(d)
    check("[CP4-X10] ask approve-changes shows the changed test plan", _clean(ask2) and "tests/CP1.json" in ask2.stdout, _out(ask2))
    check("[CP4-X10] approve-changes succeeds", _clean(ac), _out(ac))
    tbl = dt._table(d)
    check("[CP4-X10] after approve-changes the table reads the confirmed links (line 2 proven by CP1-T01)",
          isinstance(tbl, list) and len(tbl) == 2 and tbl[1].get("verdict") == "proven"
          and dt._case_result(tbl[1], "CP1-T01") == "passed", tbl)
    ask3, ha3 = fin._ask_yes_approve(d)
    check("[CP4-X10] the approval then works", _clean(ask3) and FINISH_QUESTION in ask3.stdout and _clean(ha3), _out(ha3))
    fin._finished("[CP4-X10]", d)
    # the same edit on a run with nothing unproven is held too, until confirmed
    d2, _ = fin._fin2("x10b", failed=False)
    _repoint_proves(d2, [1], [1, 2])
    _, ha4 = fin._ask_yes_approve(d2)
    hout4 = _refused("[CP4-X10] (proven run)", ha4, "human-approve")
    check("[CP4-X10] (proven run) the refusal names the changed test plan", "tests/CP1.json" in hout4, hout4[-900:])
    fin._still_waiting("[CP4-X10] (proven run)", d2)


def cp4_x11_prompt_hook_never_steers_to_the_approval_while_held():
    """[CP4-X11] The prompt hook never tells the agent to ask for or run human-approve while the finish is held, and
    a question asked before the line went unproven is withdrawn."""
    d, _ = fin._fin2("x11")
    r = g.say(d, "looks good, ship it")
    hout = _out(r)
    check("[CP4-X11] (no question asked) the hook runs clean", "Traceback" not in hout, hout[-600:])
    check("[CP4-X11] (no question asked) the hook gives no ask-for-approval or human-approve instruction",
          ASK_INSTRUCTION_RE.search(hout) is None and "ask human-approve" not in hout, hout[-900:])
    check("[CP4-X11] (no question asked) the hook says the run cannot be marked done and points at status",
          BLOCKED_RE.search(hout) is not None and "status" in hout, hout[-900:])
    # a question asked while everything was proven, then a finding names line 2
    d2, _ = fin._fin2("x11b", failed=False)
    ask = g.rx(d2, "ask", "human-approve")
    assert _clean(ask) and "ASK THE USER" in ask.stdout, _out(ask)
    fin._add_open_finding(d2, "CP1", "S2", [2])
    r2 = g.say(d2, YES)
    hout2 = _out(r2)
    check("[CP4-X11] (question pending) the hook does not tell the agent to run human-approve",
          ASK_INSTRUCTION_RE.search(hout2) is None and re.search(r"Only if it is a listed yes", hout2) is None, hout2[-900:])
    check("[CP4-X11] (question pending) the hook says the reply does not approve the finish",
          re.search(r"does NOT approve|approves nothing|cannot be marked done", hout2, re.I) is not None, hout2[-900:])
    sout = _out(g.rx(d2, "status"))
    check("[CP4-X11] (question pending) status prints no waiting-for-the-user's-answer line",
          "Waiting for the user's answer" not in sout, sout[-1200:])
    ha = g.rx(d2, "human-approve")
    _refused("[CP4-X11] (question pending)", ha, "human-approve")
    check("[CP4-X11] the refusal withdraws the pending question",
          (_state(d2).get("pending") or {}).get("action") != "human-approve", _state(d2).get("pending"))
    def close_s2(st):  # the block clears (the fixture's finding stops naming the line)
        for f in st["checkpoints"]["CP1"].get("deferred") or []:
            if f.get("id") == "S2":
                f.pop("done_means", None)
    dt.mutate(d2, close_s2)
    check("[CP4-X11] fixture: the block cleared", BLOCKED_RE.search(_out(g.rx(d2, "status"))) is None)
    ha2 = g.rx(d2, "human-approve")
    check("[CP4-X11] after the block clears, the old yes does not finish the run: human-approve needs a fresh question",
          ha2.returncode != 0 and _state(d2).get("phase") == "awaiting_final_review", (_out(ha2)[-600:], _state(d2).get("phase")))
    ask3, ha3 = fin._ask_yes_approve(d2)
    check("[CP4-X11] a fresh question and yes finish it", _clean(ask3) and _clean(ha3), _out(ha3))
    fin._finished("[CP4-X11]", d2)


def cp4_x12_status_sentence_never_invites_approval_while_held():
    """[CP4-X12] status's plain sentence never invites an approval while the finish is held."""
    d, _ = fin._fin2("x12")
    sout = _out(g.rx(d, "status"))
    check("[CP4-X12] (final review, failed test) no 'then approve' / 'approve or tell me' sentence",
          INVITE_RE.search(sout) is None, sout[-1500:])
    check("[CP4-X12] (final review, failed test) the held sentence is printed", HELD_SENTENCE_RE.search(sout) is not None, sout[-1500:])
    d2, _ = _held_by_finding("x12b")
    sout2 = _out(g.rx(d2, "status"))
    check("[CP4-X12] (final review, open finding) no invitation to approve", INVITE_RE.search(sout2) is None, sout2[-1500:])
    check("[CP4-X12] (final review, open finding) the held sentence is printed", HELD_SENTENCE_RE.search(sout2) is not None, sout2[-1500:])
    # the each-mode last-step pause
    d3 = fin._approved("x12c", steps=fin.FIN3_STEPS, dm=DM3, human_review="each")
    fin._build(d3, 1, fin.FIN3_STEPS[0], report=[{"id": "CP1-T01", "status": "passed"}])
    _, ha = fin._ask_yes_approve(d3)
    assert _clean(ha) and _state(d3).get("current") == "CP2", _out(ha)
    fin._build(d3, 2, fin.FIN3_STEPS[1], report=[{"id": "CP2-T01", "status": "passed"}, {"id": "CP2-T02", "status": "failed"}])
    sout3 = _out(g.rx(d3, "status"))
    check("[CP4-X12] (each mode, last step) no invitation to approve", INVITE_RE.search(sout3) is None, sout3[-1500:])
    check("[CP4-X12] (each mode, last step) the held sentence is printed", HELD_SENTENCE_RE.search(sout3) is not None, sout3[-1500:])
    # a proven run keeps the old sentence
    d4, _ = fin._fin2("x12d", failed=False)
    sout4 = _out(g.rx(d4, "status"))
    check("[CP4-X12] (proven run) the old sentence still asks for the final review",
          INVITE_RE.search(sout4) is not None and HELD_SENTENCE_RE.search(sout4) is None, sout4[-1500:])


def cp4_x13_ways_forward_are_worded_per_reason():
    """[CP4-X13] The ways forward name a path that fits each reason, and never a dead end."""
    d, _ = fin._fin2("x13")
    for cmd in (("status",), ("next",)):
        out = _out(g.rx(d, *cmd))
        check(f"[CP4-X13] (failed test in a done step) {cmd[0]} never offers to plan a step that proves the line",
              PLAN_A_STEP_RE.search(out) is None, out[-1500:])
        check(f"[CP4-X13] (failed test in a done step) {cmd[0]} says plainly a new step cannot clear it",
              NO_NEW_STEP_RE.search(out) is not None, out[-1500:])
        check(f"[CP4-X13] (failed test in a done step) {cmd[0]} still names feedback for changes the user asks for",
              "feedback" in out, out[-1500:])
    _, ha = fin._ask_yes_approve(d)
    hout = _refused("[CP4-X13] (failed test)", ha, "human-approve")
    check("[CP4-X13] (failed test) the refusal says the same", NO_NEW_STEP_RE.search(hout) is not None
          and PLAN_A_STEP_RE.search(hout) is None, hout[-1200:])
    # an open finding: mark-fixed, and no 'new step cannot clear' talk
    d2, _ = _held_by_finding("x13b")
    out2 = _out(g.rx(d2, "status"))
    check("[CP4-X13] (open finding) status names mark-fixed and does not call the line stuck",
          "mark-fixed" in out2 and NO_NEW_STEP_RE.search(out2) is None and PLAN_A_STEP_RE.search(out2) is None, out2[-1500:])
    # no test linked (the user confirmed a test plan that unlinked line 2): a step through feedback proves it
    d3, _ = fin._fin2("x13c", failed=False)
    _repoint_proves(d3, [1], [1])
    _, ac = _approve_changes(d3)
    assert _clean(ac), _out(ac)
    out3 = _out(g.rx(d3, "status"))
    check("[CP4-X13] (no test linked) status offers feedback and a step whose tests prove the line",
          "no test linked" in out3 and PLAN_A_STEP_RE.search(out3) is not None and NO_NEW_STEP_RE.search(out3) is None,
          out3[-1500:])
    _names("[CP4-X13] (no test linked)", out3, 2, LINE2, "status")


def cp4_x14_each_mode_last_step_pause_prints_the_table_and_json_agrees():
    """[CP4-X14] At the each-mode last-step pause status prints the table and status --json carries done_means_table
    next to finish_blockers; mid-run both are null."""
    d = fin._approved("x14", steps=fin.FIN3_STEPS, dm=DM3, human_review="each")
    fin._build(d, 1, fin.FIN3_STEPS[0], report=[{"id": "CP1-T01", "status": "passed"}])
    sout = _out(g.rx(d, "status"))
    rj, j = _sjson(d)
    check("[CP4-X14] (mid-run pause) status --json parses", _clean(rj) and isinstance(j, dict), _out(rj)[-400:])
    check("[CP4-X14] (mid-run pause) done_means_table and finish_blockers are both null",
          j.get("done_means_table") is None and j.get("finish_blockers") is None,
          (j.get("done_means_table"), j.get("finish_blockers")))
    check("[CP4-X14] (mid-run pause) status prints no proven/unproven table",
          not re.search(r"\bunproven\b", sout, re.I), sout[-900:])
    _, ha = fin._ask_yes_approve(d)
    assert _clean(ha) and _state(d).get("current") == "CP2", _out(ha)
    fin._build(d, 2, fin.FIN3_STEPS[1], report=[{"id": "CP2-T01", "status": "passed"}, {"id": "CP2-T02", "status": "failed"}])
    st = _state(d)
    check("[CP4-X14] fixture: the last step waits for its review", st.get("phase") == "awaiting_checkpoint_review", st.get("phase"))
    sout2 = _out(g.rx(d, "status"))
    check("[CP4-X14] (last step's pause) status prints the table with every line",
          all(t in sout2 for t in DM3) and re.search(r"Done means \(", sout2) is not None, sout2[-1800:])
    check("[CP4-X14] (last step's pause) line 3 is shown unproven with CP2-T02 failed",
          re.search(r"CP2-T02[^\n]{0,80}fail", sout2, re.I) is not None, sout2[-1800:])
    rj2, j2 = _sjson(d)
    check("[CP4-X14] (last step's pause) status --json parses", _clean(rj2) and isinstance(j2, dict), _out(rj2)[-400:])
    tbl, fb = j2.get("done_means_table"), j2.get("finish_blockers")
    check("[CP4-X14] (last step's pause) done_means_table is the list and finish_blockers names line 3 from it",
          isinstance(tbl, list) and len(tbl) == 3 and isinstance(fb, list) and len(fb) == 1 and fb[0].get("number") == 3
          and tbl[2].get("verdict") == "unproven" and fb[0].get("reasons") == tbl[2].get("reasons"), (tbl, fb))


def cp4_x15_legacy_each_mode_run_shows_the_table_with_the_note_and_finishes():
    """[CP4-X15] An older each-mode run shows the table at the last step's pause with the plain note, and finishes."""
    d = fin._approved("x15", human_review="each")
    fin._make_legacy(d)
    fin._build(d, 1, FIN2_STEPS[0], report=FAILED2)
    st = _state(d)
    check("[CP4-X15] fixture: the last (only) step waits for its review", st.get("phase") == "awaiting_checkpoint_review", st.get("phase"))
    sr = g.rx(d, "status")
    sout = _out(sr)
    check("[CP4-X15] status exits 0 with no traceback", _clean(sr), sout[-600:])
    check("[CP4-X15] status shows the table (both lines, line 2 unproven)",
          LINE1 in sout and LINE2 in sout and re.search(r"\bunproven\b", sout) is not None, sout[-1200:])
    check("[CP4-X15] status prints the plain older-run note and no blocked wording",
          LEGACY_NOTE_RE.search(sout) is not None and fin.LEGACY_NO_STOP_RE.search(sout) is not None
          and BLOCKED_RE.search(sout) is None, sout[-1200:])
    check("[CP4-X15] status still invites the review", INVITE_RE.search(sout) is not None, sout[-1200:])
    ask, ha = fin._ask_yes_approve(d)
    check("[CP4-X15] the approval question is asked and the approval finishes the run",
          _clean(ask) and "ASK THE USER" in ask.stdout and _clean(ha), _out(ha))
    fin._finished("[CP4-X15]", d)


def cp4_x16_none_mode_run_needs_the_final_approval_once_the_block_clears():
    """[CP4-X16] A none-mode run held by an open finding still needs the final approval once mark-fixed clears it."""
    d, r = _held_by_finding("x16", human_review="none")
    out = _out(r)
    check("[CP4-X16] complete exits 0 and leaves the run at the final review",
          _clean(r) and _state(d).get("phase") == "awaiting_final_review", (out[-600:], _state(d).get("phase")))
    check("[CP4-X16] complete names the open finding S1 as the reason", re.search(r"(?<![A-Za-z0-9])S1(?![0-9])", out) is not None, out[-900:])
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="documented the message, confirmed by the clean round"')
    check("[CP4-X16] mark-fixed closes S1", _clean(mf), _out(mf))
    st = _state(d)
    check("[CP4-X16] the run is still at the final review, not done", st.get("phase") == "awaiting_final_review", st.get("phase"))
    sout = _out(g.rx(d, "status"))
    nout = _out(g.rx(d, "next"))
    check("[CP4-X16] status shows no blocked wording any more", BLOCKED_RE.search(sout) is None, sout[-1200:])
    check("[CP4-X16] next tells the agent to put the approval question", "ask human-approve" in nout, nout[-600:])
    ask, ha = fin._ask_yes_approve(d)
    check("[CP4-X16] the approval question is asked and the approval finishes the run",
          _clean(ask) and FINISH_QUESTION in ask.stdout and _clean(ha), _out(ha))
    fin._finished("[CP4-X16]", d)


def cp4_x17_docs_state_the_rules_the_fixes_added():
    """[CP4-X17] The docs state the none-mode rule, the per-reason ways forward and the each-mode table."""
    skill = (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    schemas = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text(encoding="utf-8")
    none_re = re.compile(r"finishes only (?:through|when)[^.]*approv", re.I)
    for name, text in (("SKILL.md", skill), ("README.md", readme), ("schemas.md", schemas)):
        check(f"[CP4-X17] {name} says a held none-mode run finishes only through the final approval",
              none_re.search(text) is not None)
        check(f"[CP4-X17] {name} says a new step cannot clear a test that already failed in a done step",
              re.search(r"cannot clear|no new step can clear", text) is not None)
    check("[CP4-X17] SKILL.md no longer says a failed test result is fixed through feedback",
          "a failed or missing test result is fixed through a change the user asks for" not in skill)
    check("[CP4-X17] schemas.md says done_means_table is present at the each-mode last-step pause too",
          re.search(r"done_means_table\` is null except at the pause whose approval would finish", schemas) is not None)
    check("[CP4-X17] SKILL.md and schemas.md say a test plan edited after approval is caught at the finish pause",
          "edited after approval is caught at this pause" in skill and "integrity_problems" in schemas)


def cp4_x18_briefs_read_the_approved_lines_once_approved():
    """[CP4-X18] plan-summary, brief and the adversarial how read the approved done-means lines once approved, and
    the live ones while the planner extends the plan."""
    d, _ = fin._fin2("x18", failed=False)
    run = d / ".ratchet/runs/f"
    fin._edit_done_means(run, [LINE1, REWORDED])
    ps = g.rx(d, "plan-summary")
    check("[CP4-X18] plan-summary exits 0", _clean(ps), _out(ps)[-400:])
    check("[CP4-X18] plan-summary shows the approved line 2, not the edited text",
          LINE2 in ps.stdout and REWORDED not in ps.stdout, ps.stdout[-900:])
    br = g.rx(d, "brief", "CP1", "--role", "reviewer")
    check("[CP4-X18] brief shows the approved line 2, not the edited text",
          _clean(br) and LINE2 in br.stdout and REWORDED not in br.stdout, _out(br)[-900:])
    # while the planner extends the plan, the new line must show
    d2, _ = fin._fin2("x18b", failed=False)
    rf = fin._feedback(d2)
    assert _clean(rf), _out(rf)
    fin._extend_plan(d2 / ".ratchet/runs/f", DM3, [fin._case("CP2-T01", [3])])
    ps2 = g.rx(d2, "plan-summary")
    check("[CP4-X18] (planning_feedback) plan-summary shows the appended line 3", _clean(ps2) and LINE3 in ps2.stdout, _out(ps2)[-900:])
    br2 = g.rx(d2, "brief", "CP2", "--role", "planner")
    check("[CP4-X18] (planning_feedback) brief shows the appended line 3", _clean(br2) and LINE3 in br2.stdout, _out(br2)[-900:])
    # the adversarial how, in a building step after approval, reads the approved lines
    d3 = fin._approved("x18c")
    fin._edit_done_means(d3 / ".ratchet/runs/f", [LINE1, REWORDED])
    fin._write_step_tests(d3, 1, FIN2_STEPS[0])
    for cmd in (("tests-written", "CP1"), ("red", "CP1")):
        r = g.rx(d3, *cmd)
        assert r.returncode == 0, _out(r)
    (d3 / "src/f.js").write_text("export const f = () => 1;\n")
    for cmd in (("implemented", "CP1"), ("behavior", "CP1")):
        r = g.rx(d3, *cmd)
        assert r.returncode == 0, _out(r)
    how = g.rx(d3, "reviewers", "CP1", "--json")  # opens the round and prints the reviewer prompt ("how")
    check("[CP4-X18] (building) the reviewer prompt shows the approved line 2, not the edited text",
          _clean(how) and LINE2 in _out(how) and REWORDED not in _out(how), _out(how)[-900:])


# ------------------------------------------------------------------------------------- fix round 2 (X19 ...)

def _unlink_all_proves(d):
    """The agent 'tidies' tests/CP1.json after approval so that no case links any line any more."""
    _repoint_proves(d, [], [])


def _edit_standards(d):
    """The user asks for a standard while reviewing: one bullet appended to the project's standards.md."""
    p = d / ".ratchet" / "standards.md"
    p.write_text((p.read_text() if p.exists() else "") + "\n- every message to the user is plain\n")


def _finishes(tag, d, env=None):
    """ask human-approve, the user's yes and human-approve all succeed and the run is done."""
    ask = g.rx(d, "ask", "human-approve", env=env)
    g.say(d, YES)
    ha = g.rx(d, "human-approve", env=env)
    check(f"{tag} ask human-approve prints the question", _clean(ask) and FINISH_QUESTION in ask.stdout, _out(ask))
    check(f"{tag} human-approve succeeds", _clean(ha), _out(ha)[-900:])
    fin._finished(tag, d)


def cp4_x19_removing_every_proves_link_holds_the_finish_on_every_line():
    """[CP4-X19] A marked run whose links were ALL removed (and confirmed through approve-changes) is held at the
    finish naming every approved line; the note path is for older plans only."""
    d, _ = fin._fin2("x19", failed=False)
    _unlink_all_proves(d)
    _, ac = _approve_changes(d)
    check("[CP4-X19] fixture: approve-changes confirmed the test-plan edit", _clean(ac), _out(ac))
    tbl = dt._table(d)
    check("[CP4-X19] the table is a list with every line unproven for 'no test linked', not a note",
          isinstance(tbl, list) and len(tbl) == 2 and all(e.get("verdict") == "unproven" and "no test linked" in
                                                             (e.get("reasons") or []) for e in tbl), tbl)
    sr = g.rx(d, "status")
    sout = _out(sr)
    check("[CP4-X19] status exits 0 and says the run cannot be marked done", _clean(sr) and BLOCKED_RE.search(sout) is not None, sout[-1200:])
    check("[CP4-X19] status names both lines as unproven", LINE1 in sout and LINE2 in sout
          and len(re.findall(r"^\s+- line \d+:", sout, re.M)) == 2, sout[-1500:])
    check("[CP4-X19] status does not print the older-plan note", LEGACY_NOTE_RE.search(sout) is None
          and "No tests are linked" not in sout, sout[-1500:])
    _, sj = _sjson(d)
    check("[CP4-X19] status --json carries two finish_blockers",
          len(sj.get("finish_blockers") or []) == 2 and {b.get("number") for b in sj["finish_blockers"]} == {1, 2}, sj.get("finish_blockers"))
    ask, ha = fin._ask_yes_approve(d)
    aout = _refused("[CP4-X19]", ask, "ask human-approve")
    check("[CP4-X19] ask human-approve prints no question", "ASK THE USER" not in aout, aout)
    hout = _refused("[CP4-X19]", ha, "human-approve")
    check("[CP4-X19] the refusal names every line", LINE1 in hout and LINE2 in hout, hout[-1200:])
    fin._still_waiting("[CP4-X19]", d)
    rc = g.rx(d, "receipt")
    rout = _out(rc)
    check("[CP4-X19] the receipt marks both lines unproven and prints no older-plan note",
          _clean(rc) and rout.count("- unproven") == 2 and "never linked" not in rout, rout[-1200:])
    # an older plan with no links keeps the note and finishes
    d2, _ = fin._fin2("x19b", failed=False)
    fin._make_legacy(d2)
    _unlink_all_proves(d2)
    _, ac2 = _approve_changes(d2)
    assert _clean(ac2), _out(ac2)
    sout2 = _out(g.rx(d2, "status"))
    check("[CP4-X19] (older plan) status prints the no-tests note and no block",
          "No tests are linked" in sout2 and BLOCKED_RE.search(sout2) is None, sout2[-1200:])
    _finishes("[CP4-X19] (older plan)", d2)


def cp4_x20_the_finish_hold_checks_only_the_files_that_decide_the_lines():
    """[CP4-X20] A standards.md edit or a different agent CLI never holds a fully proven run at the finish, on an
    older plan or a marked one; only a checkpoint or test plan edit does (X10), and never on an older plan."""
    d, _ = fin._fin2("x20", failed=False)
    fin._make_legacy(d)
    _edit_standards(d)
    sout = _out(g.rx(d, "status"))
    check("[CP4-X20] (older plan, standards.md edited) status does not say the run cannot be marked done",
          BLOCKED_RE.search(sout) is None and "standards.md" not in sout, sout[-1200:])
    check("[CP4-X20] (older plan, standards.md edited) status still invites the review", INVITE_RE.search(sout) is not None, sout[-1200:])
    _finishes("[CP4-X20] (older plan, standards.md edited)", d)
    d2, _ = fin._fin2("x20b", failed=False)
    _edit_standards(d2)
    sout2 = _out(g.rx(d2, "status"))
    check("[CP4-X20] (marked plan, standards.md edited) status does not hold the finish",
          BLOCKED_RE.search(sout2) is None and "standards.md" not in sout2 and INVITE_RE.search(sout2) is not None, sout2[-1200:])
    _finishes("[CP4-X20] (marked plan, standards.md edited)", d2)
    d3, _ = fin._fin2("x20c", failed=False)
    env = dict(g.ENV, RATCHET_HOST_PROVIDER="openai")
    sout3 = _out(g.rx(d3, "status", env=env))
    check("[CP4-X20] (marked plan, another agent CLI) status does not hold the finish",
          BLOCKED_RE.search(sout3) is None and "host provider" not in sout3 and INVITE_RE.search(sout3) is not None, sout3[-1200:])
    _finishes("[CP4-X20] (marked plan, another agent CLI)", d3, env=env)
    # an older plan is never held, even by a test-plan edit
    d4, _ = fin._fin2("x20d", failed=False)
    fin._make_legacy(d4)
    _repoint_proves(d4, [1], [1, 2])
    sout4 = _out(g.rx(d4, "status"))
    check("[CP4-X20] (older plan, test plan edited) status does not hold the finish", BLOCKED_RE.search(sout4) is None, sout4[-1200:])
    _finishes("[CP4-X20] (older plan, test plan edited)", d4)
    # a marked plan with a test-plan edit is held, and the refusal names only what it checks
    d5, _ = fin._fin2("x20e", failed=False)
    _repoint_proves(d5, [1], [1, 2])
    _edit_standards(d5)
    sout5 = _out(g.rx(d5, "status"))
    check("[CP4-X20] (marked plan, test plan edited) status holds the finish naming the test plan only",
          BLOCKED_RE.search(sout5) is not None and "tests/CP1.json" in sout5 and "standards.md" not in sout5, sout5[-1200:])
    check("[CP4-X20] (marked plan, test plan edited) the reason names the proves links",
          "proves" in sout5, sout5[-1200:])
    _, ha5 = fin._ask_yes_approve(d5)
    _refused("[CP4-X20] (marked plan, test plan edited)", ha5, "human-approve")
    fin._still_waiting("[CP4-X20] (marked plan, test plan edited)", d5)


def cp4_x21_no_output_invites_the_approval_while_a_test_plan_edit_holds_the_finish():
    """[CP4-X21] While a checkpoint or test plan edit holds the finish: status's sentence does not invite an
    approval, a human-approve question asked before the edit is not shown as waiting, the prompt hook never says to
    run human-approve, and the refusal drops the pending question."""
    d, _ = fin._fin2("x21", failed=False)
    ask = g.rx(d, "ask", "human-approve")
    assert _clean(ask) and "ASK THE USER" in ask.stdout, _out(ask)
    _repoint_proves(d, [1], [1, 2])
    sout = _out(g.rx(d, "status"))
    check("[CP4-X21] status's sentence does not invite an approval", INVITE_RE.search(sout) is None, sout[-1500:])
    check("[CP4-X21] status's sentence says the run cannot be approved yet", HELD_SENTENCE_RE.search(sout) is not None, sout[-1500:])
    check("[CP4-X21] status does not show the earlier question as waiting", "Waiting for the user's answer" not in sout, sout[-1500:])
    _, sj = _sjson(d)
    check("[CP4-X21] status --json shows no waiting question", not sj.get("waiting_question"), sj.get("waiting_question"))
    r = g.say(d, YES)
    hout = _out(r)
    check("[CP4-X21] the prompt hook does not tell the agent to run human-approve",
          ASK_INSTRUCTION_RE.search(hout) is None and re.search(r"Only if it is a listed yes", hout) is None, hout[-900:])
    check("[CP4-X21] the prompt hook says the reply does not approve the finish",
          re.search(r"does NOT approve|approves nothing|cannot be marked done", hout, re.I) is not None, hout[-900:])
    ha = g.rx(d, "human-approve")
    _refused("[CP4-X21]", ha, "human-approve")
    check("[CP4-X21] the refusal drops the pending question", (_state(d).get("pending") or {}).get("action") != "human-approve", _state(d).get("pending"))
    _, ac = _approve_changes(d)
    assert _clean(ac), _out(ac)
    ha2 = g.rx(d, "human-approve")
    check("[CP4-X21] after approve-changes the old yes does not finish the run without a fresh question",
          ha2.returncode != 0 and _state(d).get("phase") == "awaiting_final_review", (_out(ha2)[-600:], _state(d).get("phase")))
    _finishes("[CP4-X21]", d)


# ------------------------------------------------------------------------------------------------ runner

def main():
    global FAIL
    tests = sorted((m.group(1).upper().replace("_", "-"), fn) for name, fn in globals().items()
                   if callable(fn) and (m := re.match(r"(cp4_x\d+)_", name)))
    only = set(sys.argv[1:])
    for cid, fn in tests:
        if only and cid not in only:
            continue
        CASE_STATUS.setdefault(cid, True)
        print(f"[{cid}]")
        try:
            fn()
        except Exception as e:
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
