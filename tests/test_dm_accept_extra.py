#!/usr/bin/env python3
"""
CP5 extra tests (fix round 1 of the done-means-proven run): one case per behaviour the fixes added.
    python3 tests/test_dm_accept_extra.py

Not planned cases (ids CP5-X21 ...): the planned cases live in tests/test_dm_accept.py, whose fixtures and helpers
this file reuses (_project_a, _project_d, _accept_flow, _entry, _blocker_numbers, ...). Covered here:
  X21 an acceptance covers only what held the line when it was given: a review finding filed AFTER the acceptance
      that names the same line holds it again (status, status --json, the receipt, finish_blockers and the
      human-approve refusal all say "held again" next to the earlier acceptance), the record keeps what it covered,
      and the user can accept the line again with a new reason (its own learnings line), after which the run finishes
  X22 the accepted-line text names what really held the line (an open finding, never "its tests failed" when every
      linked test passed) in status and the receipt; a linked test that fails afterwards is a new reason and holds
      the line again
  X23 accept-line's closing line never says the finish is free while a test plan edited after approval still holds
      it (CP4's integrity hold): it points at status / approve-changes, and ask human-approve prints no question
  X24 the docs tie the reply form and the "accepted" verdict to accept-line itself (each assertion fails on the
      pre-change docs) and describe the held-again rule and the record's `covered` list
Fix round 2:
  X25 a second `ask accept-line` about a different line overtakes the waiting one (NOT ASKED, `overtook` marker),
      so the user's single reply releases nothing; asking again on its own works; the same line twice is no overtake
  X26 a reason that appears between the question and the reply is never accepted unseen: accept-line clears the
      question and accepts nothing; the fresh question shows the new reason and the record covers it
  X27 the receipt strips a leading case id on a space boundary, so overlapping ids (CP1-T1, CP1-T10) never leave
      stray digits in the accepted text
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
import test_dm_finish as fin         # noqa: E402  (fixtures and helpers of the CP4 finish hold)
import test_dm_table as dt           # noqa: E402  (_segs, _verdict, _has_id)
import test_dm_accept as acc         # noqa: E402  (Projects A and D, the accept flow, table readers)
import test_receipt_proof as rp      # noqa: E402  (_write_report)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-X\d+)\]")

DM, LINE2, REASON, FIND_DESC, L2_FAIL = acc.DM, acc.LINE2, acc.REASON, acc.FIND_DESC, acc.L2_FAIL
FINISH_QUESTION = acc.FINISH_QUESTION
_out, _state, _clean, _refused, _names = fin._out, fin._state, fin._clean, fin._refused, fin._names
HELD_AGAIN_RE = re.compile(r"held again", re.I)
TESTS_FAILED_RE = re.compile(r"did not prove|tests? (?:did not|didn't|never) pass|\bfailed\b", re.I)
NEW_REASON = "the new finding is a wording nit, ships with the docs pass"
NEW_DESC = "the lockout message still says attempts in English only"


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


def _seg(d, n):
    sr = g.rx(d, "status")
    sout = _out(sr)
    return sr, sout, dt._segs(sout, dm=DM)[n - 1]


def _receipt_seg(d, n):
    rt = g.rx(d, "receipt")
    tout = _out(rt)
    return rt, tout, dt._segs(tout, dm=DM)[n - 1]


def _held_again_entry(tag, d, n, where):
    """The table (status --json) reads line n as unproven again, naming the earlier acceptance and a held-again reason."""
    e = acc._entry(d, n)
    check(f"[{tag}] {where}: status --json verdict for line {n} is 'unproven' again", e.get("verdict") == "unproven", e)
    check(f"[{tag}] {where}: status --json carries the earlier acceptance under accepted_earlier with its reason",
          isinstance(e.get("accepted_earlier"), dict) and e["accepted_earlier"].get("reason") == REASON, e)
    check(f"[{tag}] {where}: status --json reasons for line {n} say it is held again",
          any(HELD_AGAIN_RE.search(str(r)) for r in e.get("reasons") or []), e.get("reasons"))
    nums = acc._blocker_numbers(d)
    check(f"[{tag}] {where}: line {n} holds the finish again (finish_blockers)", nums is not None and n in nums, nums)
    return e


def cp5_x21_a_finding_filed_after_the_acceptance_holds_the_line_again():
    """[CP5-X21] An acceptance covers only what held the line when it was given; a later finding holds it again."""
    d = acc._project_a("x21")
    _, ac = acc._accept_flow(d, "2", REASON)
    check("[CP5-X21] setup: line 2 accepted", _clean(ac), _out(ac)[-600:])
    rec = acc._accepted(_state(d), 2) or {}
    check("[CP5-X21] the record keeps what the acceptance covered (CP1-T02 failed)",
          isinstance(rec.get("covered"), list) and any("CP1-T02" in str(r) for r in rec["covered"]), rec)
    check("[CP5-X21] before the new finding, line 2 reads accepted and nothing holds the finish",
          acc._verdict(d, 2) == "accepted" and acc._blockers(d) == [], acc._table(d))
    # a reviewer on a later step files a finding that names line 2; it is left open
    fin._add_open_finding(d, "CP1", "S2", [2], desc=NEW_DESC)
    e = _held_again_entry("CP5-X21", d, 2, "after the new finding")
    check("[CP5-X21] the held-again reason names the new finding S2 and the earlier reason",
          any(dt._has_id(str(r), "S2") for r in e.get("reasons") or []) and REASON in json.dumps(e.get("reasons")), e.get("reasons"))
    sr, sout, seg = _seg(d, 2)
    check("[CP5-X21] status exits 0 with no traceback", _clean(sr), sout[-600:])
    check("[CP5-X21] plain status reads line 2 as unproven, held again, naming S2 and the earlier acceptance",
          dt._verdict(seg) == "unproven" and HELD_AGAIN_RE.search(seg) is not None and dt._has_id(seg, "S2")
          and REASON in seg, seg)
    check("[CP5-X21] status says the run cannot be marked done", fin.BLOCKED_RE.search(sout) is not None, sout[-1500:])
    rt, tout, rseg = _receipt_seg(d, 2)
    check("[CP5-X21] the receipt reads line 2 as unproven and held again, naming S2",
          _clean(rt) and dt._verdict(rseg) == "unproven" and HELD_AGAIN_RE.search(rseg) is not None
          and dt._has_id(rseg, "S2"), rseg)
    _, ha = fin._ask_yes_approve(d)
    hout = _refused("[CP5-X21]", ha, "human-approve")
    _names("[CP5-X21]", hout, 2, LINE2, "the refusal")
    check("[CP5-X21] the refusal says line 2 is held again", HELD_AGAIN_RE.search(hout) is not None, hout[-900:])
    fin._still_waiting("[CP5-X21]", d)
    # the user can accept the line again, with a new reason that covers the new finding
    ask, ac2 = acc._accept_flow(d, "2", NEW_REASON)
    aout = _out(ask)
    check("[CP5-X21] `ask accept-line 2` is allowed again and names the earlier acceptance",
          _clean(ask) and "ASK THE USER" in aout and HELD_AGAIN_RE.search(aout) is not None, aout[-900:])
    check("[CP5-X21] accept-line succeeds the second time", _clean(ac2), _out(ac2)[-900:])
    rec2 = acc._accepted(_state(d), 2) or {}
    check("[CP5-X21] one record for line 2, with the new reason, covering the new finding",
          acc._accepted_count(_state(d), 2) == 1 and rec2.get("reason") == NEW_REASON
          and any(dt._has_id(str(r), "S2") for r in rec2.get("covered") or []), rec2)
    check("[CP5-X21] the re-accept has its own learnings line (two for line 2, the second with the new reason)",
          len(acc._learn_lines(d, "Accepted done-means line 2 of run f")) == 2
          and len(acc._learn_lines(d, f"Reason: {NEW_REASON}")) == 1, acc._learn(d))
    check("[CP5-X21] line 2 reads accepted again and nothing holds the finish",
          acc._verdict(d, 2) == "accepted" and acc._blockers(d) == [], acc._table(d))
    _, _, seg2 = _seg(d, 2)
    check("[CP5-X21] plain status shows line 2 accepted with what held it (S2) and the new reason, not unproven",
          "accepted" in seg2.lower() and dt._has_id(seg2, "S2") and NEW_REASON in seg2 and "unproven" not in seg2.lower(), seg2)
    ask2, ha2 = fin._ask_yes_approve(d)
    check("[CP5-X21] the finish then works", _clean(ask2) and _clean(ha2), _out(ha2)[-900:])
    fin._finished("[CP5-X21]", d)


def cp5_x22_the_accepted_text_names_what_really_held_the_line():
    """[CP5-X22] The accepted line's text names the open finding that held it, never a failed test; a test that
    fails afterwards is a new reason."""
    d = acc._project_d("x22")
    _, ac = acc._accept_flow(d, "2", REASON)
    check("[CP5-X22] setup: line 2 accepted (held by the finding S1 alone, every test passed)", _clean(ac), _out(ac)[-600:])
    sr, sout, seg = _seg(d, 2)
    check("[CP5-X22] status exits 0 with no traceback", _clean(sr), sout[-600:])
    check("[CP5-X22] plain status names the finding S1 as what held the accepted line", dt._has_id(seg, "S1") and FIND_DESC in seg, seg)
    check("[CP5-X22] plain status never claims the linked tests failed", TESTS_FAILED_RE.search(seg) is None, seg)
    check("[CP5-X22] plain status shows the reason and not the word unproven", REASON in seg and "unproven" not in seg.lower(), seg)
    rt, tout, rseg = _receipt_seg(d, 2)
    check("[CP5-X22] the receipt names the finding S1 as what held the accepted line",
          _clean(rt) and dt._has_id(rseg, "S1") and FIND_DESC in rseg, rseg)
    check("[CP5-X22] the receipt never claims the linked tests failed", TESTS_FAILED_RE.search(rseg) is None, rseg)
    # a later run of the tests leaves CP1-T02 failed: a reason the acceptance did not cover
    rp._write_report(d, "CP1", L2_FAIL)
    e = _held_again_entry("CP5-X22", d, 2, "after CP1-T02 failed")
    check("[CP5-X22] the held-again reason names CP1-T02 as the new cause",
          any(HELD_AGAIN_RE.search(str(r)) and "CP1-T02" in str(r) for r in e.get("reasons") or []), e.get("reasons"))
    _, sout2, seg2 = _seg(d, 2)
    check("[CP5-X22] plain status reads line 2 unproven again and names CP1-T02 as failed",
          dt._verdict(seg2) == "unproven" and dt._near(seg2, "CP1-T02", "fail"), seg2)
    check("[CP5-X22] status --json: only line 2 holds the finish", acc._blocker_numbers(d) == [2], acc._blockers(d))


def cp5_x23_accept_line_never_says_the_finish_is_free_while_a_test_plan_edit_holds_it():
    """[CP5-X23] accept-line's closing line reads the whole finish verdict, including CP4's integrity hold."""
    d = acc._project_a("x23")
    p = d / ".ratchet/runs/f/tests/CP1.json"
    tp = json.loads(p.read_text())
    tp["cases"][0]["title"] = tp["cases"][0].get("title", "") + " (tidied after approval)"
    p.write_text(json.dumps(tp))
    _, j = acc._sjson(d)
    check("[CP5-X23] fixture: line 2 holds the finish and the edited test plan is named", acc._blocker_numbers(d) == [2], j.get("finish_blockers"))
    _, ac = acc._accept_flow(d, "2", REASON)
    aout = _out(ac)
    check("[CP5-X23] accept-line succeeds", _clean(ac), aout[-900:])
    check("[CP5-X23] line 2 is accepted and no line holds the finish", acc._verdict(d, 2) == "accepted" and acc._blockers(d) == [], acc._table(d))
    check("[CP5-X23] the closing line does not say the finish is no longer held", "no longer held" not in aout, aout[-900:])
    check("[CP5-X23] the closing line does not tell the agent to ask for the final approval",
          fin.ASK_INSTRUCTION_RE.search(aout) is None, aout[-900:])
    check("[CP5-X23] the closing line says the finish is still held and points at the change (status / approve-changes)",
          re.search(r"still held", aout, re.I) is not None and "approve-changes" in aout, aout[-900:])
    ask = g.rx(d, "ask", "human-approve")
    check("[CP5-X23] ask human-approve prints no approval question", FINISH_QUESTION not in _out(ask), _out(ask)[-600:])
    g.say(d, acc.YES)
    ha = g.rx(d, "human-approve")
    _refused("[CP5-X23]", ha, "human-approve")
    fin._still_waiting("[CP5-X23]", d)


DOC_CHECKS = {
    # each regex is anchored to accept-line itself, so the accept-FINDING text alone can never satisfy it
    "README.md": [
        ("the reply form `accept: <reason>` is stated for ask accept-line",
         r"ask accept-line[\s\S]{0,1500}accept: <your reason>"),
        ("accepted done-means lines show as accepted in the table and the receipt",
         r"accept-line[\s\S]{0,3000}final-review table and the proof receipt show that line as \*\*accepted\*\*"),
        ("the acceptance covers only what held the line at that moment", r"acceptance covers only what held the line"),
        ("a line that goes unproven for a new reason is shown held again with the earlier acceptance",
         r"marked `held again` with your earlier acceptance and the new cause"),
    ],
    "skills/ratchet-orchestrator/SKILL.md": [
        ("the reply form is stated for ask accept-line",
         r"ask accept-line[\s\S]{0,1200}exactly `accept: <reason>`, accepts those lines"),
        ("an accepted line shows as accepted in the table and the receipt",
         r"accept-line[\s\S]{0,2500}An accepted line shows as `accepted` in the table \(`status`, `status --json`\) and in the receipt"),
        ("the acceptance covers only what held the line when it was given", r"acceptance covers only what held the line"),
        ("a new reason holds the line again with a held again reason", r"`held again:` reason"),
    ],
    "skills/ratchet-orchestrator/references/schemas.md": [
        ("the reply form is stated for ask accept-line",
         r"ask accept-line[\s\S]{0,3000}exactly `accept: <reason>` as the user's own FIRST reply"),
        ("the table and the receipt read the line as accepted",
         r"accept-line[\s\S]{0,6000}reads that line as `verdict: \"accepted\"`"),
        ("the accepted_lines record carries covered", r"\"covered\": \[\"<reason>\""),
        ("an acceptance covers only what held the line when it was given", r"An acceptance covers only what held the line when it was given"),
        ("a held-again line reads unproven with accepted_earlier and a held again reason",
         r"held again: the user accepted this line on <iso>"),
        ("accept-line and merged are in the STATE_LOCKED_CMDS list", r"`accept-finding`, `accept-line`, `raise-limit`, `approve-changes`, `merged`"),
        ("the receipt's done_means lines carry accepted_earlier", r"accepted_earlier` with the same keys"),
    ],
}


def _doc_failures(name, text):
    """The labels of DOC_CHECKS[name] whose regex does not match `text` (used both for the live docs and, by hand,
    against `git show HEAD:<file>` to prove each assertion fails on the pre-change docs)."""
    return [label for label, rx in DOC_CHECKS[name] if re.search(rx, text) is None]


def cp5_x24_docs_tie_the_accept_line_claims_to_accept_line_and_state_the_held_again_rule():
    """[CP5-X24] The docs' accept-line claims are anchored to accept-line and describe the held-again rule."""
    for name in DOC_CHECKS:
        try:
            text = (ROOT / name).read_text()
        except OSError as e:
            check(f"[CP5-X24] {name} can be read", False, str(e))
            continue
        failures = _doc_failures(name, text)
        for label, _ in DOC_CHECKS[name]:
            check(f"[CP5-X24] {name}: {label}", label not in failures, name)
    skill = (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text()
    check("[CP5-X24] SKILL.md's waiting-question paragraph says `about` carries the line numbers for accept-line",
          re.search(r"`about` is[^\n]{0,200}line numbers[^\n]{0,80}accept-line", skill) is not None, "SKILL.md")
    check("[CP5-X24] SKILL.md's waiting-question paragraph lists accept-line among the stops_run questions",
          re.search(r"`stops_run` is true[^\n]{0,200}accept-line", skill) is not None, "SKILL.md")



# ------------------------------------------------------------------------------------------------ fix round 2

NOT_ASKED_RE = re.compile(r"NOT ASKED", re.I)
OVERTOOK_RE = re.compile(r"another (?:question|one)|still waiting|could be meant for either", re.I)
STEPS_PREFIX_IDS = [[fin._case("CP1-T01", [1]), fin._case("CP1-T1", [2]), fin._case("CP1-T10", [2]), fin._case("CP1-T03", [3])]]
PREFIX_IDS_FAIL = [{"id": "CP1-T01", "status": "passed"}, {"id": "CP1-T1", "status": "failed"},
                   {"id": "CP1-T10", "status": "failed"}, {"id": "CP1-T03", "status": "passed"}]


def cp5_x25_a_second_accept_line_question_about_another_line_overtakes_the_first():
    """[CP5-X25] `ask accept-line 3` while a question about line 2 waits is NOT ASKED and overtakes it; the user's
    one `accept:` reply then releases nothing, and asking line 3 again on its own works."""
    d = acc._project_b("x25")
    check("[CP5-X25] fixture: lines 2 and 3 hold the finish", acc._blocker_numbers(d) == [2, 3], acc._blockers(d))
    a2 = g.rx(d, "ask", "accept-line", "2")
    check("[CP5-X25] the first question (line 2) is asked", _clean(a2) and "ASK THE USER" in _out(a2), _out(a2)[-600:])
    first = acc._pending(d)
    a3 = g.rx(d, "ask", "accept-line", "3")
    out3 = _out(a3)
    check("[CP5-X25] the second question (line 3) prints NOT ASKED instead of a question",
          NOT_ASKED_RE.search(out3) is not None and "ASK THE USER" not in out3, out3[-900:])
    check("[CP5-X25] the NOT ASKED text names the waiting accept-line question's line (2)",
          re.search(r"line 2\b", out3) is not None, out3[-900:])
    p = acc._pending(d)
    check("[CP5-X25] pending carries the overtook marker and is the line-3 question",
          p.get("overtook") == "accept-line" and acc._ids(p) == [3], p)
    check("[CP5-X25] the line-2 question is gone (asked_at moved)", p.get("asked_at") != first.get("asked_at"), (first, p))
    learn_before = acc._learn(d)
    g.say(d, f"accept: {REASON}")
    r = g.rx(d, "accept-line")
    out = _refused("[CP5-X25]", r, "accept-line after an overtaking question")
    check("[CP5-X25] the refusal says the question was printed over another one and nothing was accepted",
          OVERTOOK_RE.search(out) is not None and re.search(r"nothing was accepted", out, re.I) is not None, out[-900:])
    check("[CP5-X25] no accepted record for any line", not _state(d).get("accepted_lines"), _state(d).get("accepted_lines"))
    check("[CP5-X25] no learnings line was written", acc._learn(d) == learn_before, acc._learn(d))
    check("[CP5-X25] the pending question is cleared", acc._pending(d).get("action") != "accept-line", acc._pending(d))
    check("[CP5-X25] lines 2 and 3 still hold the finish", acc._blocker_numbers(d) == [2, 3], acc._blockers(d))
    check("[CP5-X25] both lines still read unproven", acc._verdict(d, 2) == "unproven" and acc._verdict(d, 3) == "unproven", acc._table(d))
    ask3, ac3 = acc._accept_flow(d, "3", REASON)
    check("[CP5-X25] asking line 3 again on its own prints the question", _clean(ask3) and "ASK THE USER" in _out(ask3), _out(ask3)[-600:])
    check("[CP5-X25] accept-line then accepts line 3", _clean(ac3), _out(ac3)[-900:])
    check("[CP5-X25] only line 3 is accepted; line 2 still holds the finish",
          acc._verdict(d, 3) == "accepted" and acc._verdict(d, 2) == "unproven" and acc._blocker_numbers(d) == [2], acc._table(d))
    # the same question printed again (same line) is not an overtake
    d2 = acc._project_b("x25b")
    g.rx(d2, "ask", "accept-line", "2")
    again = g.rx(d2, "ask", "accept-line", "2")
    check("[CP5-X25] re-printing the same question (line 2 twice) is not NOT ASKED and carries no overtook marker",
          _clean(again) and NOT_ASKED_RE.search(_out(again)) is None and not acc._pending(d2).get("overtook"),
          (_out(again)[-600:], acc._pending(d2)))


def cp5_x26_a_reason_that_appears_after_the_question_is_not_accepted_unseen():
    """[CP5-X26] A new reason that holds the line between the question and the reply is never covered by that
    reply: accept-line clears the question and accepts nothing; a fresh question shows the new reason."""
    d = acc._project_a("x26")
    a = g.rx(d, "ask", "accept-line", "2")
    aout = _out(a)
    check("[CP5-X26] setup: the question is asked, naming CP1-T02 and not S2", _clean(a) and "CP1-T02" in aout and not dt._has_id(aout, "S2"), aout[-600:])
    p = acc._pending(d)
    check("[CP5-X26] pending records what the question showed for line 2 (CP1-T02 failed)",
          "CP1-T02" in json.dumps(p.get("covered") or p.get("shown") or p.get("reasons") or {}), p)
    # a reviewer files a finding naming line 2 after the question was shown, before the reply
    fin._add_open_finding(d, "CP1", "S2", [2], desc=NEW_DESC)
    learn_before = acc._learn(d)
    g.say(d, f"accept: {REASON}")
    r = g.rx(d, "accept-line")
    out = _refused("[CP5-X26]", r, "accept-line after a new reason appeared")
    check("[CP5-X26] the refusal names the new finding S2 and says nothing was accepted",
          dt._has_id(out, "S2") and re.search(r"nothing was accepted", out, re.I) is not None, out[-900:])
    check("[CP5-X26] the refusal tells the agent to ask again", ASK_AGAIN_RE.search(out) is not None, out[-900:])
    check("[CP5-X26] no accepted record was written", acc._accepted(_state(d), 2) is None, _state(d).get("accepted_lines"))
    check("[CP5-X26] no learnings line was written", acc._learn(d) == learn_before, acc._learn(d))
    check("[CP5-X26] the pending question is cleared", acc._pending(d).get("action") != "accept-line", acc._pending(d))
    check("[CP5-X26] line 2 still reads unproven and holds the finish", acc._verdict(d, 2) == "unproven" and acc._blocker_numbers(d) == [2], acc._table(d))
    ask2, ac2 = acc._accept_flow(d, "2", REASON)
    check("[CP5-X26] the fresh question shows the new finding S2", _clean(ask2) and dt._has_id(_out(ask2), "S2"), _out(ask2)[-900:])
    check("[CP5-X26] accept-line then accepts line 2", _clean(ac2), _out(ac2)[-900:])
    rec = acc._accepted(_state(d), 2) or {}
    check("[CP5-X26] the record covers both CP1-T02 and S2",
          any("CP1-T02" in str(x) for x in rec.get("covered") or []) and any(dt._has_id(str(x), "S2") for x in rec.get("covered") or []), rec)
    check("[CP5-X26] line 2 reads accepted and nothing holds the finish", acc._verdict(d, 2) == "accepted" and acc._blockers(d) == [], acc._table(d))


ASK_AGAIN_RE = re.compile(r"ask accept-line", re.I)


def cp5_x27_the_receipt_strips_overlapping_case_ids_cleanly():
    """[CP5-X27] With linked case ids CP1-T1 and CP1-T10 on one line, the receipt's accepted text reads
    "a linked test failed" twice, with no leftover digits."""
    d = fin._approved("x27", steps=STEPS_PREFIX_IDS, dm=DM)
    r = fin._build(d, 1, STEPS_PREFIX_IDS[0], report=PREFIX_IDS_FAIL)
    check("[CP5-X27] fixture: at the final review with line 2 unproven", _clean(r) and acc._verdict(d, 2) == "unproven", _out(r)[-600:])
    _, ac = acc._accept_flow(d, "2", REASON)
    check("[CP5-X27] accept-line succeeds", _clean(ac), _out(ac)[-600:])
    rt, tout, rseg = _receipt_seg(d, 2)
    check("[CP5-X27] receipt exits 0", _clean(rt), tout[-600:])
    check("[CP5-X27] the receipt's accepted line carries no leftover digits after 'a linked test'",
          re.search(r"a linked test\d", rseg) is None, rseg)
    check("[CP5-X27] the receipt names neither case id", "CP1-T1" not in rseg and "CP1-T10" not in rseg, rseg)
    check("[CP5-X27] the receipt says a linked test failed, twice", len(re.findall(r"a linked test failed", rseg)) == 2, rseg)
    check("[CP5-X27] the receipt shows the line as accepted with the reason", "accepted" in rseg.lower() and REASON in rseg, rseg)
    _, sout, seg = _seg(d, 2)
    check("[CP5-X27] plain status keeps both case ids", "CP1-T1 " in seg + " " and "CP1-T10" in seg, seg)

# ------------------------------------------------------------------------------------------------ fix round 3
# X28 the prompt hook, on the user's `accept: <reason>` reply to a waiting accept-line question, names a command
#     that runs (bare `accept-line`), never calls that reply a non-yes that approves nothing, never points at
#     `ask withdraw`, and running exactly the command it names accepts the line
# X29 `next` keeps pointing at `accept-line` once the reply has landed (never back at `ask accept-line`)
# X30 `ask accept-line 3,2` re-prints the same question as `2,3` (no overtake); `ask accept-line 2 3` is refused
#     instead of silently dropping the 3

HOOK_CMD_RE = re.compile(r"`([^`]*ratchet\.py\"? accept-line\b[^`]*)`")  # the follow-up, not the `ask accept-line` re-ask


def _hook_text(h) -> str:
    return (h.stdout or "") + (h.stderr or "")


def cp5_x28_the_prompt_hook_names_a_runnable_accept_line_command_on_the_accept_reply():
    """[CP5-X28] With an accept-line question waiting, the real prompt hook on `accept: <reason>` names bare
    `accept-line`, does not call the reply a non-yes, does not say withdraw, and the named command accepts."""
    d = acc._project_a("x28")
    r_ask = g.rx(d, "ask", "accept-line", "2")
    check("[CP5-X28] setup: the question is asked", _clean(r_ask) and "ASK THE USER" in _out(r_ask), _out(r_ask)[-600:])
    h = g.say(d, f"accept: {REASON}")
    text = _hook_text(h)
    check("[CP5-X28] the hook answers about the waiting accept-line question", "waiting question" in text, text[-1200:])
    cmds = HOOK_CMD_RE.findall(text)
    check("[CP5-X28] the hook names an accept-line command", bool(cmds), text[-1200:])
    bad = [c for c in cmds if re.search(r"\baccept-line\s+\S", c)]
    check("[CP5-X28] the accept-line command it names takes no argument (bare `accept-line`)", cmds and not bad, cmds)
    check("[CP5-X28] the hook does not call the exact accept reply a non-yes that approves nothing",
          re.search(r"NOT one of the listed yes|approves nothing|accepts nothing", text) is None, text[-1200:])
    check("[CP5-X28] the hook does not tell the agent to withdraw the question",
          re.search(r"otherwise run `[^`]*ask withdraw`", text) is None, text[-1200:])
    check("[CP5-X28] the hook says the reply is in the exact accept form", re.search(r"exact form", text) is not None, text[-1200:])
    check("[CP5-X28] the hook still says the reply does not approve the finish", "does NOT approve" in text, text[-1200:])
    # run exactly the command the hook named (the engine path it printed, then the words after ratchet.py)
    named = cmds[0] if cmds else ""
    words = named.split()
    eng_i = next((i for i, w in enumerate(words) if w.endswith('ratchet.py"') or w.endswith("ratchet.py")), -1)
    args = [w for w in words[eng_i + 1:]] if eng_i >= 0 else ["accept-line"]
    r = g.rx(d, *args)
    check("[CP5-X28] running exactly the command the hook named exits 0 (no argparse usage dump)",
          _clean(r) and "unrecognized arguments" not in _out(r), _out(r)[-900:])
    check("[CP5-X28] the line is accepted with the user's reason", acc._verdict(d, 2) == "accepted"
          and (acc._accepted(_state(d), 2) or {}).get("reason") == REASON, acc._table(d))
    # a reply in the wrong form: the hook says so and still names the bare command (which closes the question)
    d2 = acc._project_a("x28b")
    g.rx(d2, "ask", "accept-line", "2")
    h2 = g.say(d2, "yes")
    text2 = _hook_text(h2)
    check("[CP5-X28] a plain yes is reported as NOT the exact form", re.search(r"NOT in the exact form", text2) is not None, text2[-1200:])
    cmds2 = HOOK_CMD_RE.findall(text2)
    check("[CP5-X28] the command named for a wrong-form reply is still bare accept-line",
          cmds2 and not [c for c in cmds2 if re.search(r"\baccept-line\s+\S", c)], cmds2)


def cp5_x29_next_points_at_accept_line_once_the_reply_has_landed():
    """[CP5-X29] After the user's `accept:` reply is recorded and before accept-line runs, `next` and `status`
    still say to run `accept-line` and do not send the agent back to `ask accept-line`."""
    d = acc._project_a("x29")
    g.rx(d, "ask", "accept-line", "2")
    g.say(d, f"accept: {REASON}")
    for cmd in ("next", "status"):
        r = g.rx(d, cmd)
        out = _out(r)
        check(f"[CP5-X29] {cmd} exits 0", _clean(r), out[-900:])
        check(f"[CP5-X29] {cmd} says the reply has arrived and names `ratchet.py accept-line` to record it",
              re.search(r"replied[^\n]*`ratchet\.py accept-line`", out) is not None, out[-1500:])
        sentence = (re.search(r"The user has replied[^.]*\.", out) or re.search(r"$", out)).group(0)
        check(f"[CP5-X29] {cmd} does not point back at `ask accept-line` as the way forward for this reply",
              sentence and "ask accept-line" not in sentence, sentence or out[-1500:])
    r = g.rx(d, "accept-line")
    check("[CP5-X29] accept-line then records the acceptance", _clean(r) and acc._verdict(d, 2) == "accepted", _out(r)[-900:])
    r2 = g.rx(d, "next")
    check("[CP5-X29] after it is recorded, next no longer says a reply is waiting to be recorded",
          "The user has replied" not in _out(r2), _out(r2)[-1200:])


def cp5_x30_line_order_and_a_second_space_separated_value():
    """[CP5-X30] `ask accept-line 3,2` after `2,3` is the same question (no overtake, mode sorted); `ask
    accept-line 2 3` is refused naming the comma form and asks nothing."""
    d = acc._project_b("x30")
    r1 = g.rx(d, "ask", "accept-line", "2,3")
    check("[CP5-X30] setup: the question about lines 2,3 is asked", _clean(r1) and "ASK THE USER" in _out(r1), _out(r1)[-600:])
    r2 = g.rx(d, "ask", "accept-line", "3,2")
    p = acc._pending(d)
    check("[CP5-X30] re-printing it as 3,2 is the same question: no NOT ASKED, no overtook marker",
          _clean(r2) and NOT_ASKED_RE.search(_out(r2)) is None and not p.get("overtook"), (_out(r2)[-600:], p))
    check("[CP5-X30] the stored target is the sorted form", p.get("target") == "2,3" and acc._ids(p) == [2, 3], p)
    d2 = acc._project_b("x30b")
    r3 = g.rx(d2, "ask", "accept-line", "2", "3")
    out3 = _refused("[CP5-X30]", r3, "ask accept-line 2 3")
    check("[CP5-X30] the refusal names the comma form", "accept-line 2,3" in out3, out3[-600:])
    check("[CP5-X30] nothing was asked", acc._pending(d2).get("action") != "accept-line" and "Nothing was asked" in out3, acc._pending(d2))


# ------------------------------------------------------------------------------------------------ target field
# X31 the pending question keeps what it is about in `target` and leaves `mode` (the command's argument) empty for
#     accept-line; two questions about different lines overtake each other through `target`; a hint that prints the
#     question again names a command that runs; a pending written before `target` existed (line numbers in `mode`)
#     still works end to end

def _write_pending(d, **fields):
    def fn(st):
        merged = {**st["pending"], **fields}
        st["pending"] = {k: v for k, v in merged.items() if v != "__drop__"}
    dt.mutate(d, fn)


def cp5_x31_the_pending_question_keeps_its_subject_in_target():
    """[CP5-X31] target holds the line numbers, mode stays empty; overtake and hints read target; an older pending
    with the numbers in `mode` and no `target` behaves the same."""
    d = acc._project_b("x31")
    g.rx(d, "ask", "accept-line", "2")
    p = acc._pending(d)
    check("[CP5-X31] pending keeps the line numbers in target and no mode", p.get("target") == "2" and not p.get("mode"), p)
    a3 = g.rx(d, "ask", "accept-line", "3")
    out3 = _out(a3)
    check("[CP5-X31] a question about another line overtakes it (NOT ASKED names line 2)",
          NOT_ASKED_RE.search(out3) is not None and re.search(r"line 2\b", out3) is not None, out3[-900:])
    p = acc._pending(d)
    check("[CP5-X31] the overtook marker carries the earlier target", p.get("overtook_target") == "2", p)
    # the same lines in another order are the same question
    d2 = acc._project_b("x31b")
    g.rx(d2, "ask", "accept-line", "2,3")
    g.rx(d2, "ask", "accept-line", "3,2")
    check("[CP5-X31] the same lines in another order are the same question", not acc._pending(d2).get("overtook"), acc._pending(d2))
    # accept-line with no question waiting names an ask command that runs
    d3 = acc._project_b("x31c")
    r = g.rx(d3, "accept-line")
    out = _out(r)
    m = re.search(r"ratchet\.py\"? (ask accept-line [\d,]+)", out)
    check("[CP5-X31] with no question waiting, the hint names `ask accept-line <numbers>`, not a bare one",
          m is not None and not re.search(r"ask accept-line`", out), out[-700:])
    if m:
        rr = g.rx(d3, *m.group(1).split())
        check("[CP5-X31] the command the hint named runs and asks the question", _clean(rr) and "ASK THE USER" in _out(rr), _out(rr)[-600:])
    # an older pending: numbers in mode, no target
    d4 = acc._project_a("x31d")
    g.rx(d4, "ask", "accept-line", "2")
    _write_pending(d4, mode="2", target="__drop__")
    check("[CP5-X31] fixture: the pending has only mode", acc._pending(d4).get("mode") == "2" and "target" not in acc._pending(d4), acc._pending(d4))
    h = g.say(d4, f"accept: {REASON}")
    text = _hook_text(h)
    cmds = HOOK_CMD_RE.findall(text)
    check("[CP5-X31] older pending: the hook still names bare accept-line and the line",
          cmds and not [c for c in cmds if re.search(r"\baccept-line\s+\S", c)] and "line 2" in text, text[-1000:])
    ra = g.rx(d4, "accept-line")
    check("[CP5-X31] older pending: accept-line still accepts the line", _clean(ra) and acc._verdict(d4, 2) == "accepted", _out(ra)[-900:])
    d5 = acc._project_b("x31e")
    g.rx(d5, "ask", "accept-line", "2")
    _write_pending(d5, mode="2", target="__drop__")
    same = g.rx(d5, "ask", "accept-line", "2")
    check("[CP5-X31] older pending: the same question again is not an overtake", _clean(same) and not acc._pending(d5).get("overtook"), (_out(same)[-500:], acc._pending(d5)))
    other = g.rx(d5, "ask", "accept-line", "3")
    check("[CP5-X31] older pending: another line still overtakes it", NOT_ASKED_RE.search(_out(other)) is not None, _out(other)[-600:])


def cp5_x32_ask_from_another_chat_is_refused_plainly_not_with_a_traceback():
    import trust_helpers as th
    env = th.make_env("claude")
    d, tp = th.cp5_plan(env)
    th.rx(d, env, "ask", "approve-plan")
    th.cp5_say(d, tp, env, "carry on with that run here", session="s-other")
    for args in (("claim",),):
        r = th.rx(d, env, "ask", *args)
        check(f"[CP5-X32] ask {args[0]} from a second chat: plain refusal naming the waiting question, no traceback",
              r.returncode != 0 and "Traceback" not in r.stderr and "UnboundLocalError" not in r.stderr
              and "A question is waiting for the user's first reply in another chat (shall I " in r.stderr
              and "ASK THE USER" not in r.stdout, (r.returncode, r.stdout[-300:], r.stderr[-600:]))


# ------------------------------------------------------------------------------------------------ runner

def main():
    global FAIL
    tests = sorted((m.group(1).upper().replace("_", "-"), fn) for name, fn in globals().items()
                   if callable(fn) and (m := re.match(r"(cp5_x\d+)_", name)))
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
