#!/usr/bin/env python3
"""
CP3 extra tests (fix rounds 1 and 2 of the done-means-proven run): one case per behaviour the fixes added.
    python3 tests/test_dm_findings_extra.py

Not planned cases (ids CP3-X01 ...): the planned cases live in tests/test_dm_findings.py, whose fixtures and
helpers this file reuses (named, _in_review, _record, _blocks_with_open, _deferred_with, ...). Covered here:
  X01 review_rules: the exception text on a new plan (low-risk round 1 and a round 2), absent on an older one,
      which instead says plainly that the plan predates the rule
  X02 the reviewer role brief text (`brief CPn --role reviewer`)
  X03 names_done_means: the accepted shapes ("3, 4", "line 3", 3.0, a bare number) block; the rejected ones
      (false, "none", "N/A", [null], [""], []) follow the ordinary rules
  X04 the legacy note: an older plan with EVERY line linked still keeps the older rules and prints the note
  X05 the line shown in the accept-finding question and the check-in brief
  X06 the docs-only skip refuses while a line-naming finding is open
  X07 the narrowing path: an older plan's lens behind a line-naming minor is not kept; a new plan's is
  X08 the done_means_rules marker: written at approve-plan, kept across approve-changes, plan-extend and merged,
      absent on a run approved through the older path (and such a run keeps the older rules after that)
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
import _tmp                          # noqa: E402
import test_gates as g               # noqa: E402
import test_dm_findings as df        # noqa: E402  (fixtures and the shared then-helpers)
import test_dm_plan as dp            # noqa: E402  (_project, _write_plan, _submit, _case, _cp, DM)
import test_dm_plan_extend as pe     # noqa: E402  (_finish_cp1, _append_step, LINE4)
import test_receipt_proof as rp      # noqa: E402  (_legacy_approve, _no_objective_project)
import test_quick_path_docs_step as qd  # noqa: E402  (_approved_docs_project, _to_adversarial_docs)
import test_merge_main as mm         # noqa: E402  (build, make_main, merge_main)

sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import ratchet as rt                 # noqa: E402  (DM_LEGACY_FINDINGS_NOTE, the one note text)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP3-X\d+)\]")

named, MINOR, MAJOR, LOW_REASON, LATER_REASON, DM_LINE = (df.named, df.MINOR, df.MAJOR, df.LOW_REASON,
                                                         df.LATER_REASON, df.DM_LINE)
_out, _state, _gate, _open, _deferred, _by_id, _record, _in_review = (df._out, df._state, df._gate, df._open,
                                                                       df._deferred, df._by_id, df._record,
                                                                       df._in_review)
EXCEPTION = "unless one names a done-means line"
STANDALONE = "always blocks, at any severity, round or scope"


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


def _marker(d, slug="f"):
    return (_state(d, slug).get("approved") or {}).get("done_means_rules")


def _legacy_linked_project(name, risk="low"):
    """An older run (approved through the legacy path, no marker) whose plan has an objective with EVERY
    done-means line linked to a case: the plan a proof-receipt-era test planner produced. (d, run)."""
    d, run = dp._project(name)
    dp._write_plan(run, [[dp._case("CP1-T01", [1, 2, 3])]], cps=[dict(dp._cp(1), risk=risk)])
    rp._legacy_approve(d)
    return d, run


def _rules_text(rnd) -> str:
    return " ".join(rnd.get("review_rules") or [])


# ------------------------------------------------------------------------------------------------ cases

def cp3_x01_review_rules_carry_the_exception_on_a_new_plan_and_not_on_an_older_one():
    """[CP3-X01] review_rules: exception + standalone rule on a new plan (low-risk round 1 and round 2); an older
    plan gets neither and is told plainly that it predates the rule."""
    d, rnd = _in_review("x01")
    rules = _rules_text(rnd)
    check("[CP3-X01] low-risk round 1: the minor-is-set-aside sentence carries the exception", EXCEPTION in rules, rules)
    check("[CP3-X01] low-risk round 1: the standalone always-blocks rule is present", STANDALONE in rules, rules)
    r = _record(d, "CP1", rnd, [named(MINOR)])
    assert r.returncode == 2, _out(r)
    rnd2 = df._fix_and_reopen(d, "x01-fix")
    rules2 = _rules_text(rnd2)
    check("[CP3-X01] round 2: the later-round scope sentence carries the exception", EXCEPTION in rules2, rules2)
    check("[CP3-X01] round 2: the standalone always-blocks rule is present", STANDALONE in rules2, rules2)
    d2 = rp._no_objective_project("x01-noobj")
    g.to_adversarial(d2)
    rules3 = _rules_text(g.open_round(d2))
    check("[CP3-X01] older plan with no objective: no exception clause", EXCEPTION not in rules3, rules3)
    check("[CP3-X01] older plan with no objective: no standalone rule, nothing about done_means",
          STANDALONE not in rules3 and "done_means" not in rules3, rules3)
    d3, _ = _legacy_linked_project("x01-legacy")
    g.to_adversarial(d3)
    rules4 = _rules_text(g.open_round(d3))
    check("[CP3-X01] older plan with an objective (every line linked): no exception clause and no standalone rule",
          EXCEPTION not in rules4 and STANDALONE not in rules4, rules4)
    check("[CP3-X01] ...and one rule says plainly that this plan was approved before the rule",
          "approved before" in rules4 and rt.DM_LEGACY_FINDINGS_NOTE in rules4, rules4)


def cp3_x02_reviewer_role_brief_states_the_exception_and_the_older_plan_qualifier():
    """[CP3-X02] `brief CP1 --role reviewer` carries the exception and says an older plan keeps the old rule."""
    d, _rnd = _in_review("x02")
    r = g.rx(d, "brief", "CP1", "--role", "reviewer")
    out = _out(r)
    check("[CP3-X02] the reviewer brief prints", r.returncode == 0, out)
    check("[CP3-X02] it says a finding naming a done-means line blocks at any severity and any round",
          re.search(r"done_means\).{0,80}any severity and any round", out, re.S) is not None, out[-1200:])
    check("[CP3-X02] it says to name a line only when that line is not met in normal use",
          df._names_only_when_not_met(out), out[-1200:])
    check("[CP3-X02] it says a plan approved before that rule keeps the older rules, per the round's review rules",
          "approved before that rule" in out and "review rules" in out, out[-1200:])
    check("[CP3-X02] it is the same text ROLE_BRIEFS['reviewer'] holds", rt.ROLE_BRIEFS["reviewer"] in out, out[-1200:])


def cp3_x03_names_done_means_reads_common_shapes_and_rejects_no_line_values():
    """[CP3-X03] "1, 2", "line 1", 1.0 and "#1" name a line (block on a low-risk step); false, "none", "N/A",
    [null], [""] and [] name none (deferred with the low-risk reason)."""
    accepted = [("A1", "1, 2"), ("A2", "line 1"), ("A3", 1.0), ("A4", ["#1"]), ("A5", "1")]
    rejected = [("R1", False), ("R2", "none"), ("R3", "N/A"), ("R4", [None]), ("R5", [""]), ("R6", [])]
    d, rnd = _in_review("x03")
    findings = [named(dict(MINOR, id=fid), dm=val) for fid, val in accepted + rejected]
    r = _record(d, "CP1", rnd, findings)
    check("[CP3-X03] adv-record exits 2 cleanly", df._clean_block(r), (r.returncode, _out(r)))
    opened, deferred = _by_id(_open(d, "CP1", 1)), _by_id(_deferred(d, "CP1", 1))
    for fid, val in accepted:
        check(f"[CP3-X03] done_means {val!r} names a line: {fid} is open, kept as written",
              fid in opened and opened[fid].get("done_means") == val and fid not in deferred, opened.get(fid))
    for fid, val in rejected:
        check(f"[CP3-X03] done_means {val!r} names no line: {fid} is deferred with the low-risk reason",
              fid in deferred and deferred[fid].get("deferred_because") == LOW_REASON and fid not in opened,
              deferred.get(fid))
    ask = _out(g.rx(d, "ask", "accept-finding", "CP1", "A1,A2,A3"))
    check("[CP3-X03] the question renders \"1, 2\" as lines 1 and 2 (line 1 with its text, 2 as written)",
          f'1: "{DM_LINE}"' in ask and "2 (not a line of this plan, shown as written)" in ask, ask)
    check("[CP3-X03] ...and \"line 1\" and 1.0 as line 1 with its text", ask.count(f'1: "{DM_LINE}"') >= 3, ask)
    d2, rnd2 = _in_review("x03c")
    r2 = _record(d2, "CP1", rnd2, [named(MINOR, dm="none")])
    df._deferred_with("[CP3-X03]", d2, r2, "N1", LOW_REASON)


def cp3_x04_older_plan_with_every_line_linked_keeps_the_older_rules_with_the_note():
    """[CP3-X04] An older run whose plan links every done-means line is still an older run: a line-naming minor on
    a low-risk step is deferred and adv-record prints the plain note; so does the no-objective older run."""
    d, _run = _legacy_linked_project("x04")
    check("[CP3-X04] fixture: the older run has no done_means_rules marker", _marker(d) is None, _marker(d))
    g.to_adversarial(d)
    rnd = g.open_round(d)
    r = _record(d, "CP1", rnd, [named(MINOR), dict(MINOR, id="N2", description="plain")])
    df._deferred_with("[CP3-X04]", d, r, "N1", LOW_REASON)
    check("[CP3-X04] adv-record prints the plain legacy note", rt.DM_LEGACY_FINDINGS_NOTE in _out(r), _out(r))
    check("[CP3-X04] the gate is passed; nothing blocks", _gate(d).get("status") == "passed", _gate(d))
    d2 = rp._no_objective_project("x04-noobj")
    g.to_adversarial(d2)
    r2 = _record(d2, "CP1", g.open_round(d2), [named(MINOR)])
    check("[CP3-X04] the no-objective older run also prints the plain note",
          r2.returncode == 0 and rt.DM_LEGACY_FINDINGS_NOTE in _out(r2), _out(r2))
    d3, rnd3 = _in_review("x04c")
    r3 = _record(d3, "CP1", rnd3, [named(MINOR)])
    check("[CP3-X04] CONTROL: a new plan blocks and prints no legacy note",
          r3.returncode == 2 and rt.DM_LEGACY_FINDINGS_NOTE not in _out(r3), _out(r3))


def cp3_x05_decision_points_show_the_line_a_finding_names():
    """[CP3-X05] `ask accept-finding` and the check-in brief show the line's number and text."""
    d, rnd = _in_review("x05", config=["checkin_round=1"])
    r = _record(d, "CP1", rnd, [named(MINOR), dict(MINOR, id="N2", description="plain")])
    check("[CP3-X05] setup: the round blocks", df._clean_block(r), _out(r))
    check("[CP3-X05] adv-record's NOT-approved message counts the line-naming finding",
          "1 of them naming a done-means line" in _out(r), _out(r))
    ask = _out(g.rx(d, "ask", "accept-finding", "CP1", "N1"))
    check("[CP3-X05] the accept-finding question shows the line's number and text",
          f'contradicts done-means line 1: "{DM_LINE}"' in ask, ask)
    ci = g.rx(d, "check-in", "CP1", "--json")
    brief = json.loads(ci.stdout or "{}") if ci.returncode == 0 else {}
    entry = next((f for f in brief.get("findings", []) if str(f.get("id", "")).endswith("N1")), None)  # key: reviewer/id
    check("[CP3-X05] the check-in brief JSON carries done_means [1] on N1", entry is not None
          and entry.get("done_means") == [1], (ci.returncode, _out(ci)[:600]))
    plain = _out(g.rx(d, "check-in", "CP1"))
    check("[CP3-X05] the plain check-in brief prints the line under N1",
          f'contradicts done-means line 1: "{DM_LINE}"' in plain, plain)


def cp3_x06_docs_only_skip_refuses_while_a_line_naming_finding_is_open():
    """[CP3-X06] A docs-only diff after a round blocked on a line-naming finding still opens a normal round."""
    d, _log = qd._approved_docs_project("x06", risk="low")
    qd._to_adversarial_docs(d)
    (d / "src" / "a.js").write_text("export const a = 2;\n")   # code too: round 1 is a normal round
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, _out(r)
    rnd = g.open_round(d)
    check("[CP3-X06] fixture: round 1 is a normal round (code changed)", rnd.get("reviewers") and not rnd.get("docs_only_skip"), rnd)
    r = _record(d, "CP1", rnd, [named(dict(MINOR, file="README.md"))])
    check("[CP3-X06] round 1 blocks on the line-naming minor", df._clean_block(r), _out(r))
    (d / "src" / "a.js").write_text("export const a = 1;\n")   # the diff since base_tree is docs only again
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, _out(r)
    g.rx(d, "triage", "CP1", 'N1=fix:"will fix"')
    r2 = g.rx(d, "reviewers", "CP1", "--json")
    rnd2 = json.loads(r2.stdout or "{}") if r2.returncode == 0 else {}
    check("[CP3-X06] `reviewers CP1` opens round 2 instead of recording a docs-only skip",
          r2.returncode == 0 and rnd2.get("round") == 2 and "docs_only_skip" not in rnd2, (r2.returncode, _out(r2)[:600]))
    check("[CP3-X06] the gate is not passed", _gate(d).get("status") != "passed", _gate(d))
    # round 3 fix round: round 2 is open but unrecorded (cps.adv_round now points at it); another docs-only
    # edit before that review finishes, then `reviewers` again, replaces the pending round. Round 1's
    # line-naming finding is still open, so the docs-only skip must still refuse.
    readme = d / "README.md"
    readme.write_text(readme.read_text() + "\nanother docs correction while review is pending.\n")
    r3 = g.rx(d, "reviewers", "CP1", "--json")
    rnd3 = json.loads(r3.stdout or "{}") if r3.returncode == 0 else {}
    check("[CP3-X06] re-running `reviewers CP1` over the pending round opens a new round, not a docs-only skip",
          r3.returncode == 0 and rnd3.get("round") == 3 and "docs_only_skip" not in rnd3, (r3.returncode, _out(r3)[:600]))
    check("[CP3-X06] ...and the gate is still not passed", _gate(d).get("status") != "passed", _gate(d))
    check("[CP3-X06] ...and no skip was recorded", "docs_only_review_skip" not in _state(d)["checkpoints"]["CP1"],
          _state(d)["checkpoints"]["CP1"].get("docs_only_review_skip"))


def _legacy_round2_lenses(name, correctness_finding):
    d, _run = _legacy_linked_project(name, risk="normal")
    g.to_adversarial(d)
    rnd = g.open_round(d)
    assert len(rnd["reviewers"]) == 2, rnd["reviewers"]
    r = _record(d, "CP1", rnd, None, by_lens={"standards": [dict(MAJOR)], "correctness": [correctness_finding]})
    assert r.returncode == 2, _out(r)
    rnd2 = df._fix_and_reopen(d, f"{name}-fix")
    return sorted(rv["lens"] for rv in rnd2["reviewers"])


def cp3_x07_narrowing_leaves_an_older_plans_line_naming_minor_lens_out():
    """[CP3-X07] Round 2 narrowing: on an older plan the lens behind an open line-naming minor is not kept (older
    rules); on a new plan it is (CP3-T10)."""
    lenses = _legacy_round2_lenses("x07", named(MINOR))
    check("[CP3-X07] older plan: round 2 lists only the standards lens (the line-naming minor keeps no lens)",
          lenses == ["standards"], lenses)
    lenses2 = df._t10_round2_lenses("x07c", named(MINOR))
    check("[CP3-X07] CONTROL: new plan: round 2 lists both lenses", lenses2 == ["correctness", "standards"], lenses2)


def cp3_x08_marker_written_at_approval_kept_by_later_snapshots_absent_on_older_runs():
    """[CP3-X08] done_means_rules: written by approve-plan, kept across approve-changes, plan-extend and merged,
    never added to an older run."""
    d = g.project("x08")
    check("[CP3-X08] before approval there is no marker", _marker(d) is None, _state(d).get("approved"))
    g.approve_plan(d)
    check("[CP3-X08] approve-plan writes done_means_rules = 1", _marker(d) == 1, _state(d).get("approved"))
    # approve-changes: the user approves a test-plan change that drops line 1's only "proves"
    tp = d / ".ratchet/runs/f/tests/CP1.json"
    tp.write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    g.rx(d, "ask", "approve-changes")
    g.say(d, "yes, go ahead")
    r = g.rx(d, "approve-changes")
    check("[CP3-X08] approve-changes accepts the test-plan change", r.returncode == 0, _out(r))
    check("[CP3-X08] ...and keeps the marker", _marker(d) == 1, _state(d).get("approved"))
    g.to_adversarial(d)
    rnd = g.open_round(d)
    r = _record(d, "CP1", rnd, [named(MINOR)])
    df._blocks_with_open("[CP3-X08]", d, r, "N1", dm=[1])
    check("[CP3-X08] the new plan's line-naming minor still blocks after the unlinking change, with no legacy note",
          rt.DM_LEGACY_FINDINGS_NOTE not in _out(r), _out(r))
    # plan-extend keeps it
    d2, run2, r2 = dp._submit("x08e", [[dp._case("CP1-T01", [1, 2, 3])]])
    assert r2.returncode == 0, _out(r2)
    g.approve_plan(d2)
    pe._finish_cp1(d2)
    g.say(d2, "also add logout")
    assert g.rx(d2, "feedback", "--text", "also add logout").returncode == 0
    pe._append_step(run2, pe.LINE4, [dp._case("CP2-T01", [4])])
    re_ = g.rx(d2, "plan-extend")
    check("[CP3-X08] plan-extend succeeds", re_.returncode == 0, _out(re_))
    check("[CP3-X08] ...and keeps the marker", _marker(d2) == 1, _state(d2).get("approved"))
    # merged keeps it
    d3 = mm.build("x08m")
    check("[CP3-X08] fixture: the lane's approved run carries the marker", _marker(d3) == 1, _state(d3).get("approved"))
    mm.make_main(d3)
    mm.merge_main(d3)
    rm = mm.rx(d3, "merged")
    check("[CP3-X08] merged accepts the merge", rm.returncode == 0, mm.said(rm)[-400:])
    check("[CP3-X08] ...and keeps the marker", _marker(d3) == 1, _state(d3).get("approved"))
    # absent on older runs, and plan-extend on one adds none
    d4, run4 = dp._legacy_project("x08l", [[dp._case("CP1-T01", [1])]])
    check("[CP3-X08] an older run (legacy approval) has no marker", _marker(d4) is None, _state(d4).get("approved"))
    pe._finish_cp1(d4)
    g.say(d4, "also add logout")
    assert g.rx(d4, "feedback", "--text", "also add logout").returncode == 0
    pe._append_step(run4, None, [dp._case("CP2-T01", [1])])
    re4 = g.rx(d4, "plan-extend")
    check("[CP3-X08] plan-extend on the older run succeeds and adds no marker",
          re4.returncode == 0 and _marker(d4) is None, (_out(re4), _state(d4).get("approved")))
    check("[CP3-X08] the no-objective older run has no marker either",
          _marker(rp._no_objective_project("x08n")) is None, "")


# ------------------------------------------------------------------------------------------------ runner

def main():
    global FAIL
    tests = sorted((m.group(1).upper().replace("_", "-"), fn) for name, fn in globals().items()
                   if callable(fn) and (m := re.match(r"(cp3_x\d+)_", name)))
    for cid, fn in tests:
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
