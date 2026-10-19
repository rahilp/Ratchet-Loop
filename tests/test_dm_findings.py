#!/usr/bin/env python3
"""
CP3 tests: findings against a done-means line always block.   python3 tests/test_dm_findings.py

A review finding may name the done-means line(s) it contradicts in an optional `"done_means": [n]` field. Such a
finding blocks its step at any severity and any round: it is never set aside for being minor on a low-risk step,
for arriving after the round where minor findings stop blocking, for being outside the code changed since the last
round, for being of a kind an automatic check covers, or for carrying no `user_impact`. A number that is not a
real line still blocks and is recorded exactly as written. Findings that name no line follow exactly today's rules
(every case pairs the line-naming finding with a control twin without the field). The user can still accept such
a finding with their own `accept: <reason>` reply; a legacy plan with no objective records one without crashing;
round limits are untouched; and the reviewer prompt, the host sub-agent's file, the review loop's SKILL.md and
schemas.md all describe the field.

Every case drives the real CLI in a throwaway git project built with tests/_tmp.py's mkdtemp and the helpers of
tests/test_gates.py (project, approve_plan, to_adversarial, open_round, write_review, rx, say), the same pattern as
tests/test_dm_table.py and tests/test_dm_plan.py; tests/test_receipt_proof.py's _no_objective_project builds the
one older-plan case, and tests/test_review_objective.py's _codex_dry_run_prompt keeps the other-company reviewer's
prompt. Every assertion goes through check(), so the missing feature fails cleanly instead of crashing.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                         # noqa: E402
import test_gates as g              # noqa: E402  (project, approve_plan, to_adversarial, open_round, write_review, rx, say)
import test_receipt_proof as rp     # noqa: E402  (_no_objective_project: a legacy plan with no objective at all)
import test_review_objective as ro  # noqa: E402  (_codex_dry_run_prompt: the other-company reviewer's prompt)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP3-[TX]\d+[a-z]?)\]")


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


# ------------------------------------------------------------------------------------------------ fixtures

DM_LINE = _tmp.OBJECTIVE_MIN["done_means"][0]   # the one done-means line every fixture plan has: line 1
LOW_REASON = "low-risk step: only blocker and major findings block"
LATER_REASON = "later round: not in code changed since the previous round"
NO_IMPACT_REASON = "no user impact stated"
COVERED_REASON = "an automatic check already covers 'docs-out-of-sync'"
DOCS_CHECK = 'checks=[{"kind":"docs-out-of-sync","command":"node -e 0"}]'

MINOR = {"id": "N1", "severity": "minor", "file": "src/f.js", "description": "needs a comment"}
MAJOR = {"id": "M1", "severity": "major", "file": "src/f.js", "description": "bug",
         "user_impact": "a user hits a crash"}


def named(f, dm=(1,), **over):
    """`f` with a done_means field (a list by default; pass a bare value with dm=99 for the non-list shape)."""
    x = dict(f, done_means=list(dm) if isinstance(dm, (list, tuple)) else dm)
    x.update(over)
    return x


def _out(r):
    return r.stdout + r.stderr


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _gate(d, cp="CP1"):
    return _state(d)["checkpoints"][cp]["gates"]["adversarial"]


def _open(d, cp, round_no, slug="f"):
    p = Path(d) / f".ratchet/runs/{slug}/evidence/{cp}/adversarial/round-{round_no:02d}/open-findings.json"
    return json.loads(p.read_text()) if p.exists() else []


def _deferred(d, cp, round_no, slug="f"):
    p = Path(d) / f".ratchet/runs/{slug}/evidence/{cp}/adversarial/round-{round_no:02d}/deferred-findings.json"
    return json.loads(p.read_text()) if p.exists() else []


def _by_id(entries):
    return {e.get("id"): e for e in entries}


def _record(d, cp, rnd, findings, by_lens=None):
    """Writes `findings` into the round's FIRST required reviewer's file and an empty approval for every other
    reviewer the round lists (1 for a low-risk or small-fix round, 2 for a normal-risk round 1), then runs
    adv-record. `by_lens` ({lens: findings}) spreads findings by lens instead. Returns the adv-record result."""
    paths = []
    for i, rv in enumerate(rnd["reviewers"]):
        if by_lens is not None:
            fs = by_lens.get(rv.get("lens"), [])
        else:
            fs = findings if i == 0 else []
        paths.append(g.write_review(d, rnd, rv["id"], findings=fs))
    return g.rx(d, "adv-record", cp, "--reviews", *paths)


def _in_review(name, risk="low", config=None):
    """A one-step project of the given risk, approved and driven to its adversarial step, with round 1 open."""
    d = g.project(name, extra_cp={"risk": risk}, config=config)
    g.approve_plan(d)
    g.to_adversarial(d)
    return d, g.open_round(d)


def _fix_and_reopen(d, tag):
    """The fixer edits src/f.js, the behavior gate passes again, and the next round opens (open_round records a
    fix decision for every still-open finding first, as every suite in this repo does)."""
    (d / "src/f.js").write_text(f"export const f = () => 1; // {tag}\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, _out(r)
    return g.open_round(d)


def _triage_all_fix(d, cp="CP1"):
    for f in _open(d, cp, _state(d)["checkpoints"][cp]["adv_round"]["round"]):
        g.rx(d, "triage", cp, f'{f["id"]}=fix:"test fixture"')


def _rounds(d, cp="CP1"):
    return _gate(d, cp).get("rounds") or []


def _clean_block(r):
    return r.returncode == 2 and "Traceback" not in _out(r)


def _blocks_with_open(tag, d, r, fid, round_no=1, dm=None):
    """The shared 'then' of a blocking case: adv-record exits 2 cleanly, `fid` is in open-findings.json with its
    done_means exactly as written, and it is not in deferred-findings.json."""
    check(f"{tag} adv-record exits 2 (not approved), no traceback", _clean_block(r), (r.returncode, _out(r)))
    opened = _by_id(_open(d, "CP1", round_no))
    check(f"{tag} {fid} is listed in open-findings.json", fid in opened, opened)
    check(f"{tag} {fid}'s done_means is kept exactly as written: {dm!r}",
          fid in opened and opened[fid].get("done_means") == dm, opened.get(fid))
    check(f"{tag} {fid} is not in deferred-findings.json", fid not in _by_id(_deferred(d, "CP1", round_no)),
          _deferred(d, "CP1", round_no))


def _deferred_with(tag, d, r, fid, reason, round_no=1):
    """The shared 'then' of a control: adv-record exits 0 and `fid` is deferred with exactly `reason`."""
    check(f"{tag} CONTROL: adv-record exits 0 (approved)", r.returncode == 0, (r.returncode, _out(r)))
    entry = _by_id(_deferred(d, "CP1", round_no)).get(fid)
    check(f"{tag} CONTROL: {fid} is deferred with exactly '{reason}'",
          entry is not None and entry.get("deferred_because") == reason, entry)
    check(f"{tag} CONTROL: {fid} is not in open-findings.json", fid not in _by_id(_open(d, "CP1", round_no)),
          _open(d, "CP1", round_no))


# ------------------------------------------------------------------------------------------------ cases

def cp3_t01_minor_naming_a_line_blocks_on_a_low_risk_step():
    """[CP3-T01] A minor finding that names a done-means line blocks even on a low-risk step."""
    d, rnd = _in_review("t01")
    r = _record(d, "CP1", rnd, [named(MINOR)])
    _blocks_with_open("[CP3-T01]", d, r, "N1", dm=[1])
    rd = g.rx(d, "deferred")
    check("[CP3-T01] `ratchet.py deferred` does not list the line-naming finding",
          "needs a comment" not in rd.stdout, rd.stdout)
    d2, rnd2 = _in_review("t01c")
    r2 = _record(d2, "CP1", rnd2, [dict(MINOR)])
    _deferred_with("[CP3-T01]", d2, r2, "N1", LOW_REASON)


def _carry_to_round3(name, third):
    """Normal-risk CP1 through rounds 1 and 2, each blocked on a plain minor on src/f.js (edited again before
    each round so it stays in scope), the same setup as tests/test_converge.py. Round 3 reports `third`."""
    d = g.project(name, extra_cp={"risk": "normal"}, config=["checkin_round=99"])
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    for n, tag in ((1, "r2"), (2, "r3")):
        r = _record(d, "CP1", rnd, [dict(MINOR)])
        assert r.returncode == 2, f"round {n} should block: {_out(r)}"
        rnd = _fix_and_reopen(d, tag)
    assert rnd.get("round") == 3, rnd
    return d, _record(d, "CP1", rnd, [third])


def cp3_t02_minor_naming_a_line_blocks_past_the_converge_round():
    """[CP3-T02] A minor finding that names a line still blocks after the round where minor findings stop
    blocking."""
    d, r = _carry_to_round3("t02", named(MINOR))
    _blocks_with_open("[CP3-T02]", d, r, "N1", round_no=3, dm=[1])
    d2, r2 = _carry_to_round3("t02c", dict(MINOR))
    check("[CP3-T02] CONTROL: the same round-3 minor without done_means is approved (exit 0)",
          r2.returncode == 0, _out(r2))
    rd = g.rx(d2, "deferred")
    check("[CP3-T02] CONTROL: `ratchet.py deferred` lists it for CP1",
          "needs a comment" in rd.stdout and "CP1" in rd.stdout, rd.stdout)


def _round2_on_unchanged_file(name, finding):
    """Normal-risk CP1: round 1 blocks on a major (with user_impact) in src/f.js, the fixer edits it, and round 2
    reports `finding` on src/a.js, a file nobody changed since round 1 and not among last round's blocking files."""
    d, rnd = _in_review(name, risk="normal")
    r = _record(d, "CP1", rnd, [dict(MAJOR)], by_lens={"standards": [dict(MAJOR)]})
    assert r.returncode == 2, _out(r)
    rnd2 = _fix_and_reopen(d, "fixed")
    return d, _record(d, "CP1", rnd2, [finding])


def cp3_t03_minor_naming_a_line_in_unchanged_code_still_blocks_later():
    """[CP3-T03] A line-naming minor finding in code nobody changed still blocks in a later round."""
    on_a = dict(MINOR, file="src/a.js")
    d, r = _round2_on_unchanged_file("t03", named(on_a))
    _blocks_with_open("[CP3-T03]", d, r, "N1", round_no=2, dm=[1])
    d2, r2 = _round2_on_unchanged_file("t03c", on_a)
    _deferred_with("[CP3-T03]", d2, r2, "N1", LATER_REASON, round_no=2)


def cp3_t04_minor_of_a_covered_kind_naming_a_line_still_blocks():
    """[CP3-T04] A line-naming minor finding of a kind an automatic check covers still blocks."""
    covered = {"id": "N1", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
               "description": "README does not mention the new flag"}
    d, rnd = _in_review("t04", risk="normal", config=[DOCS_CHECK])
    r = _record(d, "CP1", rnd, [named(covered)])
    _blocks_with_open("[CP3-T04]", d, r, "N1", dm=[1])
    d2, rnd2 = _in_review("t04c", risk="normal", config=[DOCS_CHECK])
    r2 = _record(d2, "CP1", rnd2, [dict(covered)])
    _deferred_with("[CP3-T04]", d2, r2, "N1", COVERED_REASON)


def cp3_t05_line_naming_finding_needs_no_user_impact():
    """[CP3-T05] A finding that names a line needs no separate user-impact line to block."""
    m1 = {"id": "M1", "severity": "major", "file": "src/f.js", "description": "bug with no impact line"}
    d, rnd = _in_review("t05", risk="normal")
    r = _record(d, "CP1", rnd, [named(m1)])
    _blocks_with_open("[CP3-T05]", d, r, "M1", dm=[1])
    entry = _by_id(_deferred(d, "CP1", 1)).get("M1")
    check("[CP3-T05] M1 is not deferred as 'no user impact stated'",
          entry is None or entry.get("deferred_because") != NO_IMPACT_REASON, entry)
    d2, rnd2 = _in_review("t05c", risk="normal")
    r2 = _record(d2, "CP1", rnd2, [dict(m1)])
    _deferred_with("[CP3-T05]", d2, r2, "M1", NO_IMPACT_REASON)
    n1 = {"id": "N1", "severity": "minor", "file": "src/f.js", "description": "needs a comment"}
    d3, rnd3 = _in_review("t05m")   # low risk: only the named line can make this minor block
    r3 = _record(d3, "CP1", rnd3, [named(n1)])
    _blocks_with_open("[CP3-T05] (minor N1, no user_impact, low-risk step)", d3, r3, "N1", dm=[1])


def cp3_t06_number_that_is_not_a_line_still_blocks_and_is_shown_as_written():
    """[CP3-T06] A finding naming a number that is not a real line still blocks and is shown as written."""
    fs = [named(dict(MINOR, id="N99"), dm=[99]), named(dict(MINOR, id="N0b"), dm=99),
          named(dict(MINOR, id="N0"), dm=[0])]
    d, rnd = _in_review("t06")
    r = _record(d, "CP1", rnd, fs)
    check("[CP3-T06] adv-record exits 2 without a crash or traceback", _clean_block(r), (r.returncode, _out(r)))
    opened = _by_id(_open(d, "CP1", 1))
    deferred = _by_id(_deferred(d, "CP1", 1))
    for fid, dm in (("N99", [99]), ("N0b", 99), ("N0", [0])):
        check(f"[CP3-T06] {fid} is open with done_means exactly {dm!r}",
              fid in opened and opened[fid].get("done_means") == dm, opened.get(fid))
        check(f"[CP3-T06] {fid} is not deferred", fid not in deferred, deferred.get(fid))


def _t07_findings(with_keys=True):
    n1 = dict(MINOR)
    n2 = dict(MINOR, id="N2", description="needs a second comment")
    n3 = dict(MINOR, id="N3", description="needs a third comment")
    m1 = dict(MAJOR)
    m2 = {"id": "M2", "severity": "major", "file": "src/f.js", "description": "bug with no impact line"}
    if with_keys:
        n2["done_means"] = []
        n3["done_means"] = None
    return [n1, n2, n3, m1, m2]


def cp3_t07_findings_naming_no_line_follow_todays_rules():
    """[CP3-T07] Findings that name no line follow exactly today's rules."""
    d, rnd = _in_review("t07")
    r = _record(d, "CP1", rnd, _t07_findings(True))
    check("[CP3-T07] adv-record exits 2, no traceback", _clean_block(r), (r.returncode, _out(r)))
    check("[CP3-T07] open-findings.json lists exactly M1", sorted(_by_id(_open(d, "CP1", 1))) == ["M1"],
          _open(d, "CP1", 1))
    deferred = _by_id(_deferred(d, "CP1", 1))
    for fid in ("N1", "N2", "N3"):
        check(f"[CP3-T07] {fid} is deferred with the low-risk reason",
              deferred.get(fid, {}).get("deferred_because") == LOW_REASON, deferred.get(fid))
    check("[CP3-T07] M2 is deferred with 'no user impact stated'",
          deferred.get("M2", {}).get("deferred_because") == NO_IMPACT_REASON, deferred.get("M2"))
    rounds = _rounds(d)
    entry = rounds[-1] if rounds else {}
    check("[CP3-T07] the recorded round counts findings 1 and deferred 4",
          entry.get("findings") == 1 and entry.get("deferred") == 4, entry)
    d2, rnd2 = _in_review("t07c")
    r2 = _record(d2, "CP1", rnd2, _t07_findings(False))
    rounds2 = _rounds(d2)
    entry2 = rounds2[-1] if rounds2 else {}
    keys = ("findings", "deferred", "raw_findings", "blocking_files")
    check("[CP3-T07] the round entry's counts are identical to the same round with the done_means keys deleted",
          r2.returncode == 2 and all(entry.get(k) == entry2.get(k) for k in keys),
          ({k: entry.get(k) for k in keys}, {k: entry2.get(k) for k in keys}))
    check("[CP3-T07] the twin's open and deferred lists match (same ids, same reasons)",
          sorted(_by_id(_open(d2, "CP1", 1))) == ["M1"]
          and {k: v.get("deferred_because") for k, v in _by_id(_deferred(d2, "CP1", 1)).items()}
          == {k: v.get("deferred_because") for k, v in deferred.items()}, _deferred(d2, "CP1", 1))


def cp3_t08_only_the_line_naming_finding_blocks_its_neighbour_is_set_aside():
    """[CP3-T08] Only the finding that names a line blocks; its neighbours are still set aside."""
    n2 = dict(MINOR, id="N2", description="needs a second comment")
    d, rnd = _in_review("t08")
    r = _record(d, "CP1", rnd, [named(MINOR), n2])
    check("[CP3-T08] adv-record exits 2, no traceback", _clean_block(r), (r.returncode, _out(r)))
    check("[CP3-T08] open-findings.json lists only N1", sorted(_by_id(_open(d, "CP1", 1))) == ["N1"],
          _open(d, "CP1", 1))
    deferred = _by_id(_deferred(d, "CP1", 1))
    check("[CP3-T08] deferred-findings.json lists only N2, with the low-risk reason",
          sorted(deferred) == ["N2"] and deferred["N2"].get("deferred_because") == LOW_REASON, deferred)
    rounds = _rounds(d)
    entry = rounds[-1] if rounds else {}
    check("[CP3-T08] the round entry shows findings 1 and deferred 1",
          entry.get("findings") == 1 and entry.get("deferred") == 1, entry)


def cp3_t09_line_naming_findings_stay_inside_the_round_limits():
    """[CP3-T09] Line-naming findings stay inside the round limits and never reset a budget."""
    d, rnd = _in_review("t09", risk="normal", config=["max_adversarial_rounds=2", "checkin_round=99"])
    r1 = _record(d, "CP1", rnd, [named(MINOR)])
    check("[CP3-T09] round 1 blocks on the line-naming minor", _clean_block(r1), (r1.returncode, _out(r1)))
    rnd2 = _fix_and_reopen(d, "t09-r2")
    r2 = _record(d, "CP1", rnd2, [named(MINOR)])
    check("[CP3-T09] round 2 blocks on it too", _clean_block(r2), (r2.returncode, _out(r2)))
    st = _state(d)
    gate = st["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP3-T09] after round 2 the run is blocked on the existing review-round limit (phase 'blocked')",
          st.get("phase") == "blocked", (st.get("phase"), st.get("blocked_reason")))
    check("[CP3-T09] gates.adversarial.attempts == 2", gate.get("attempts") == 2, gate.get("attempts"))
    check("[CP3-T09] adv_rounds_total == 2", st.get("adv_rounds_total") == 2, st.get("adv_rounds_total"))
    check("[CP3-T09] cap_raised_to is unset (nothing raised a limit)", not gate.get("cap_raised_to"), gate)
    _triage_all_fix(d)
    (d / "src/f.js").write_text("export const f = () => 1; // t09-r3\n")
    g.rx(d, "behavior", "CP1")
    r3 = g.rx(d, "reviewers", "CP1", "--json")
    # Review finding A4 (round 1): a bare nonzero exit let any refusal or a crash pass. The refusal must be the
    # existing round-limit block (adv-record's "still has N finding(s) after 2 rounds" reason, kept in state, and
    # the "phase blocked" refusal reviewers prints for it), never a traceback or another refusal.
    check("[CP3-T09] `reviewers CP1` refuses to open round 3 because the run is blocked, with no traceback",
          r3.returncode != 0 and "phase blocked" in _out(r3) and "Traceback" not in _out(r3), _out(r3))
    st3 = _state(d)
    check("[CP3-T09] the block's reason is the existing round-limit wording: 'still has 1 finding(s) after 2 rounds'",
          st3.get("phase") == "blocked"
          and re.search(r"CP1: adversarial review still has 1 finding\(s\) after 2 rounds",
                        str(st3.get("blocked_reason"))) is not None,
          (st3.get("phase"), st3.get("blocked_reason")))


def _t10_round2_lenses(name, correctness_finding):
    d, rnd = _in_review(name, risk="normal")
    assert len(rnd["reviewers"]) == 2, rnd["reviewers"]
    r = _record(d, "CP1", rnd, None, by_lens={"standards": [dict(MAJOR)], "correctness": [correctness_finding]})
    assert r.returncode == 2, _out(r)
    rnd2 = _fix_and_reopen(d, "t10-fix")
    return sorted(rv["lens"] for rv in rnd2["reviewers"])


def cp3_t10_reviewer_behind_an_open_line_naming_finding_is_asked_again():
    """[CP3-T10] The reviewer behind an open line-naming finding is asked again next round."""
    lenses = _t10_round2_lenses("t10", named(MINOR))
    check("[CP3-T10] round 2 lists BOTH lenses (standards and correctness)",
          lenses == ["correctness", "standards"], lenses)
    lenses2 = _t10_round2_lenses("t10c", dict(MINOR))
    check("[CP3-T10] CONTROL: with a plain minor from the correctness lens, round 2 lists only standards",
          lenses2 == ["standards"], lenses2)


def cp3_t11_user_can_still_accept_a_blocking_line_naming_finding():
    """[CP3-T11] The user can still accept a blocking line-naming finding with their own reply."""
    d, rnd = _in_review("t11")
    r = _record(d, "CP1", rnd, [named(MINOR)])
    check("[CP3-T11] setup: the line-naming minor blocks the low-risk step", _clean_block(r), _out(r))
    ask = g.rx(d, "ask", "accept-finding", "CP1", "N1")
    check("[CP3-T11] `ask accept-finding CP1 N1` is not refused (the finding is open, not deferred)",
          ask.returncode == 0 and "Nothing was asked" not in _out(ask) and "deferred" not in _out(ask), _out(ask))
    early = g.rx(d, "accept-finding", "CP1")
    check("[CP3-T11] before the user's reply, `accept-finding` is refused (the agent cannot accept it itself)",
          early.returncode != 0, _out(early))
    tf = g.rx(d, "triage", "CP1", 'N1=fix:"will add the comment"')
    check("[CP3-T11] `triage CP1 N1=fix:...` works as for any open finding", tf.returncode == 0, _out(tf))
    tr = g.rx(d, "triage", "CP1", 'N1=rebut:"the line is covered by the docs"')
    check("[CP3-T11] `triage CP1 N1=rebut:...` works as for any open finding", tr.returncode == 0, _out(tr))
    ta = g.rx(d, "triage", "CP1", 'N1=accept:"shortcut"')
    check("[CP3-T11] `triage CP1 N1=accept` is still refused", ta.returncode != 0, _out(ta))
    g.say(d, "accept: shipping it, the line is covered elsewhere")
    acc = g.rx(d, "accept-finding", "CP1")
    check("[CP3-T11] after the user's `accept: <reason>` reply, accept-finding works", acc.returncode == 0, _out(acc))
    check("[CP3-T11] the step's adversarial gate is passed", _gate(d).get("status") == "passed", _gate(d))
    rows = json.loads(g.rx(d, "deferred", "--json").stdout or "[]")
    entry = next((x for x in rows if x.get("id") == "N1" and x.get("accepted")), None)
    check("[CP3-T11] `deferred --json` lists N1 as accepted, still carrying done_means [1] and the user's reason",
          entry is not None and entry.get("done_means") == [1]
          and entry.get("reason") == "shipping it, the line is covered elsewhere", entry)


def cp3_t12_legacy_plan_with_no_objective_records_a_line_naming_finding():
    """[CP3-T12] A run with an older plan that has no objective still records a line-naming finding without
    crashing: approved before this change, it keeps the old rules (the minor is left for later, never blocked)."""
    d = rp._no_objective_project("dmfind-t12")
    g.to_adversarial(d)
    rnd = g.open_round(d)
    n2 = dict(MINOR, id="N2", description="needs a second comment")
    r = _record(d, "CP1", rnd, [named(MINOR), n2])
    # Done-means line 7 (review finding X1, round 1): "Runs and plans approved before this change keep working,
    # with a plain note instead of a refusal or a block". This plan was approved before the rule existed (the
    # older-plan path: _legacy_approve, no objective at all), so CP3's blocking rule does NOT apply to it. The
    # line-naming minor follows the OLD rules: on a low-risk step it is recorded for later exactly as N2 is,
    # with its done_means kept as written, and nothing blocks or crashes.
    check("[CP3-T12] adv-record exits 0 (approved, no block) with no traceback",
          r.returncode == 0 and "Traceback" not in _out(r), (r.returncode, _out(r)))
    # Review finding A3 (round 1): the old `or` here could never fail; every check below can.
    check("[CP3-T12] no finding is left open: open-findings.json lists nothing",
          _by_id(_open(d, "CP1", 1)) == {}, _open(d, "CP1", 1))
    deferred = _by_id(_deferred(d, "CP1", 1))
    check("[CP3-T12] N1 (names line 1) is recorded for later by today's low-risk rule, as before this change",
          deferred.get("N1") is not None and deferred["N1"].get("deferred_because") == LOW_REASON, deferred.get("N1"))
    check("[CP3-T12] N1's done_means is kept exactly as written on the deferred entry: [1]",
          deferred.get("N1") is not None and deferred["N1"].get("done_means") == [1], deferred.get("N1"))
    check("[CP3-T12] N2 (no line) is deferred by today's low-risk rule",
          deferred.get("N2") is not None and deferred["N2"].get("deferred_because") == LOW_REASON, deferred.get("N2"))
    st = _state(d)
    check("[CP3-T12] the step's adversarial gate is passed and the run is not blocked",
          _gate(d).get("status") == "passed" and st.get("phase") != "blocked",
          (_gate(d).get("status"), st.get("phase"), st.get("blocked_reason")))
    rd = g.rx(d, "deferred", "--json")
    rows = json.loads(rd.stdout or "[]") if rd.returncode == 0 else []
    check("[CP3-T12] `deferred --json` lists both N1 and N2 as left for later, N1 still carrying done_means [1]",
          rd.returncode == 0 and sorted(x.get("id") for x in rows if x.get("id") in ("N1", "N2")) == ["N1", "N2"]
          and next((x for x in rows if x.get("id") == "N1"), {}).get("done_means") == [1], _out(rd))


def _numbered(text, line_no, line_text):
    """`text` lists the done-means line `line_text` with the 1-based number `line_no` in front of it."""
    return re.search(rf"(?<!\d){line_no}[.)]\s*{re.escape(line_text)}", text) is not None


def _names_only_when_not_met(text):
    """`text` says to name a done-means line (done_means) only when that line is not met in normal use."""
    win = re.search(r"done_means.{0,700}", text, re.S)
    chunk = win.group(0) if win else ""
    return re.search(r"only (?:when|if)", chunk, re.I) is not None and "normal use" in chunk \
        and re.search(r"not met|isn't met|is not met|fails|contradict", chunk, re.I) is not None


def cp3_t13_other_company_reviewer_is_asked_for_the_line_with_numbered_lines():
    """[CP3-T13] The other-company reviewer is asked for the line, with numbered lines to pick from."""
    r, prompt = ro._codex_dry_run_prompt("dmfind-t13")
    check("[CP3-T13] external_review.py --dry-run runs and keeps the prompt", r.returncode == 0 and bool(prompt),
          _out(r))
    check("[CP3-T13] the Done means line in the objective section is numbered 1", _numbered(prompt, 1, DM_LINE),
          prompt[:900])
    idx = prompt.find('"findings":[{')
    chunk = prompt[idx:idx + 600] if idx != -1 else ""
    check("[CP3-T13] the reply shape's findings example contains a done_means key", "done_means" in chunk, chunk)
    check("[CP3-T13] that findings example keeps its severity and user_impact keys",
          "severity" in chunk and "user_impact" in chunk, chunk)
    check("[CP3-T13] the prompt says to name a line only when that line is not met in normal use",
          _names_only_when_not_met(prompt), prompt[-1500:])
    check("[CP3-T13] ...and to leave the field out otherwise",
          re.search(r"done_means.{0,700}(leave it out|leave .{0,30} out|omit it|omit the field|otherwise)",
                    prompt, re.S | re.I) is not None, prompt[-1500:])


def cp3_t14_host_reviewer_sub_agent_and_the_review_loop_are_told_the_same_thing():
    """[CP3-T14] The host reviewer sub-agent and the review loop are told the same thing."""
    adv = (ROOT / "agents" / "ratchet-adversary.md").read_text()
    skill = (ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md").read_text()
    example = next((ln for ln in adv.splitlines() if '"findings":[{' in ln), "")
    check("[CP3-T14] agents/ratchet-adversary.md's JSON output example has a done_means key",
          "done_means" in example, example)
    check("[CP3-T14] agents/ratchet-adversary.md says to name a line only when it is not met in normal use",
          _names_only_when_not_met(adv), "")
    check("[CP3-T14] agents/ratchet-adversary.md says such a finding always blocks",
          re.search(r"done_means.{0,600}always block", adv, re.S | re.I) is not None, "")
    check("[CP3-T14] agents/ratchet-adversary.md still describes the severity rules for a finding without a line",
          "only blocker and major findings block" in adv, "")
    para = next((p for p in skill.split("\n\n") if "Every reviewer's prompt opens with the plan's objective" in p), "")
    check("[CP3-T14] SKILL.md's objective paragraph names done_means", "done_means" in para, para[:300])
    check("[CP3-T14] SKILL.md's objective paragraph says a finding naming a line always blocks",
          re.search(r"done_means.{0,600}always block", para, re.S | re.I) is not None, para[-400:])
    check("[CP3-T14] SKILL.md's objective paragraph says to name one only when the line is not met in normal use",
          _names_only_when_not_met(para), para[-400:])
    d, rnd = _in_review("t14")   # providers=[]: host sub-agent reviewers only
    hows = " ".join(rv.get("how", "") for rv in rnd["reviewers"])
    check("[CP3-T14] (fixture sanity) every reviewer this round is a host sub-agent",
          all(rv.get("vendor") == "host" for rv in rnd["reviewers"]), rnd["reviewers"])
    check("[CP3-T14] the host launch instructions (or the objective carried in `reviewers --json`) number the "
          "Done means line", _numbered(hows, 1, DM_LINE) or _numbered(json.dumps(rnd), 1, DM_LINE), hows)


def _section(text, start, end):
    i, j = text.find(start), text.find(end)
    return text[i:j] if i != -1 and j != -1 and j > i else ""


def cp3_t15_docs_describe_the_done_means_finding_field_everywhere():
    """[CP3-T15] The docs describe the done_means finding field in every place the rule shows up."""
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    readme = (ROOT / "README.md").read_text()
    oskill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    s7 = _section(schemas, "## 7. Adversarial review JSON", "## 8.")
    check("[CP3-T15] schemas.md section 7 documents the done_means finding field", "done_means" in s7, s7[:200])
    win = re.search(r"done_means.{0,1500}", s7, re.S)
    chunk = win.group(0) if win else ""
    check("[CP3-T15] ...as a list of 1-based line numbers", re.search(r"1-based", chunk) is not None, chunk[:400])
    check("[CP3-T15] ...that blocks at any severity and round",
          re.search(r"(any|every|whatever its) severity", chunk, re.I) is not None
          and re.search(r"(any|every) round", chunk, re.I) is not None, chunk[:600])
    check("[CP3-T15] ...and is never deferred for scope, a covering check or a missing user_impact",
          re.search(r"never (deferred|set aside|written to .deferred)|not deferred", chunk, re.I) is not None
          and "user_impact" in chunk and re.search(r"scope|changed since", chunk, re.I) is not None
          and re.search(r"check", chunk, re.I) is not None, chunk[:900])
    check("[CP3-T15] ...and that a number that is not a real line still blocks and is shown as written",
          re.search(r"not a (real )?line.{0,200}(still blocks|blocks)", chunk, re.S | re.I) is not None
          and re.search(r"as written|as the reviewer wrote|exactly as", chunk, re.I) is not None, chunk[:900])
    ui_para = next((p for p in schemas.split("\n\n") if p.startswith("`user_impact`")), "")
    check("[CP3-T15] schemas.md's user_impact paragraph names the done_means exception (a major without "
          "user_impact that names a line is not set aside)", "done_means" in ui_para, ui_para[:300])
    check("[CP3-T15] README says a finding naming a done-means line always blocks",
          re.search(r"done.means line[^\n]{0,160}always block", readme, re.I) is not None, "")
    check("[CP3-T15] the orchestrator SKILL.md mentions that a finding naming a done-means line blocks",
          re.search(r"finding[^\n]{0,200}done.means[^\n]{0,200}block", oskill, re.I) is not None, "")


def _small_fix_round2(name):
    """Normal-risk CP1 whose round 1 was fully approved, then a 5-line fix to src/f.js opens round 2 as a
    one-reviewer small-fix round (tests/test_quick_path_small.py CP3-T01's setup)."""
    d, rnd = _in_review(name, risk="normal")
    r = _record(d, "CP1", rnd, [])
    assert r.returncode == 0, _out(r)
    with open(d / "src" / "f.js", "a") as f:
        for i in range(5):
            f.write(f"// filler-{name}-{i}\n")
    rnd2 = g.open_round(d)
    return d, rnd2


def cp3_t16_line_naming_minor_still_blocks_in_a_one_reviewer_small_fix_round():
    """[CP3-T16] A line-naming minor finding still blocks in a one-reviewer small-fix round."""
    on_a = dict(MINOR, file="src/a.js")
    d, rnd2 = _small_fix_round2("t16")
    check("[CP3-T16] (fixture sanity) round 2 is a one-reviewer small-fix round",
          rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1, rnd2)
    r = _record(d, "CP1", rnd2, [named(on_a)])
    _blocks_with_open("[CP3-T16]", d, r, "N1", round_no=2, dm=[1])
    cps = _state(d)["checkpoints"]["CP1"]
    check("[CP3-T16] the gate is not passed", cps["gates"]["adversarial"].get("status") != "passed",
          cps["gates"]["adversarial"])
    check("[CP3-T16] no small_fix_review is recorded on the step (a blocked small-fix round is not an approved one)",
          not cps.get("small_fix_review"), cps.get("small_fix_review"))
    check("[CP3-T16] the small-fix round still counts toward the step's round limit (attempts == 2)",
          cps["gates"]["adversarial"].get("attempts") == 2, cps["gates"]["adversarial"].get("attempts"))
    d2, rnd2b = _small_fix_round2("t16c")
    check("[CP3-T16] CONTROL (fixture sanity): the twin's round 2 is a one-reviewer small-fix round",
          rnd2b.get("small_fix") is True and len(rnd2b["reviewers"]) == 1, rnd2b)
    r2 = _record(d2, "CP1", rnd2b, [on_a])
    _deferred_with("[CP3-T16]", d2, r2, "N1", LATER_REASON, round_no=2)
    cps2 = _state(d2)["checkpoints"]["CP1"]
    check("[CP3-T16] CONTROL: the small-fix review is recorded on the step (round 2)",
          (cps2.get("small_fix_review") or {}).get("round") == 2, cps2.get("small_fix_review"))
    check("[CP3-T16] CONTROL: the small-fix round counts toward the round limit too (attempts == 2)",
          cps2["gates"]["adversarial"].get("attempts") == 2, cps2["gates"]["adversarial"].get("attempts"))


# ------------------------------------------------------------------------------------------------ runner

def write_report(ids):
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid in ids]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    tests = sorted((m.group(1).upper().replace("_", "-"), fn) for name, fn in globals().items()
                   if callable(fn) and (m := re.match(r"(cp3_t\d+)_", name)))
    for cid, fn in tests:
        CASE_STATUS.setdefault(cid, True)
        print(f"[{cid}]")
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report([cid for cid, _ in tests])
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
