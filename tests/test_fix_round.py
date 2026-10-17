#!/usr/bin/env python3
"""
Lane H CP5 tests: a fix round names exactly one reviewer.   python3 tests/test_fix_round.py

After a review round BLOCKS and the fixer changes the code, the next round is a fix round: `reviewers` names
exactly one reviewer, the one who raised the finding(s) triaged `fix`, says it is a fix round (`fix_round`,
`fix_for` in --json and state.json, a "Fix round:" line in its text output), whatever the fix's size and whatever
the step's risk. A first round, and a round that does not follow a block, keep their normal reviewer set. When the
findings being fixed came from more than one reviewer, no single reviewer is tied to the fix: the round keeps the
existing narrowing rules and says why it is not a fix round.

  - [CP5-F01] normal risk, a fix over 20 changed lines: one reviewer, the standards reviewer that raised it
  - [CP5-F02] normal risk, the correctness reviewer raised it: that reviewer is the one named
  - [CP5-F03] high risk, a small fix: one reviewer, the one that raised it
  - [CP5-F04] high risk, a fix over 20 changed lines: one reviewer, the one that raised it
  - [CP5-F05] a first round keeps its full set (normal and high risk) and is not a fix round
  - [CP5-F06] rounds that do not follow a block keep their normal set: a round replaced before anyone reviewed
              it, a fix over 20 lines after an approval, and a blocked round whose findings were all rebutted
              with no code change
  - [CP5-F07] findings being fixed from two reviewers: not a fix round, the reason is printed, existing
              narrowing rules apply
  - [CP5-F09] a fix round replaced before it was reviewed (the fixer changed the code again) stays a fix round
              for the same reviewer and findings
  - [CP5-F08] the fix round's approval names the one reviewer, and the existing converge rule still holds: a
              fix round that blocks again brings back the full set, not another fix round

Black-box: every case drives the real ratchet.py CLI in a throwaway git project through tests/test_gates.py's
helpers (`project`, `approve_plan`, `to_adversarial`, `write_review`, `rx`), with RATCHET_HOME in a temp folder and
no other-provider CLI configured (providers=[]), so only the host's own reviewer ids are used.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-F01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_gates as g  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-F\d+)\]")


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

def _finding(fid, severity="major"):
    return {"id": fid, "severity": severity, "file": "src/f.js", "description": "bug",
            "user_impact": "a user hits a crash"}


def _lens(rid: str) -> str:
    return rid.rsplit("-", 1)[-1] if rid.endswith(("-standards", "-correctness")) else rid


def _state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def _reviewers(d, *extra):
    return g.rx(d, "reviewers", "CP1", *extra)


def _open(d):
    r = _reviewers(d, "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _fix(d, lines, tag):
    """The fixer's change: src/f.js rewritten so `lines` code lines differ (f() still returns 1), then behavior."""
    body = "".join(f"export const pad_{tag}_{i} = {i};\n" for i in range(lines - 1))
    (d / "src/f.js").write_text(body + f"export const f = () => 1; // {tag}\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr


def _lines_since(d, rnd, path="src/f.js"):
    """Changed lines in `path` between the tree round `rnd` reviewed and the working tree (added + removed)."""
    old = g.git(d, "show", f"{rnd['fingerprint']}:{path}").stdout.splitlines()
    new = (Path(d) / path).read_text().splitlines()
    return sum(1 for ln in difflib.ndiff(old, new) if ln[:2] in ("+ ", "- "))


def _block(d, rnd, by: dict, decision="fix"):
    """Records `rnd` with reviewer id -> findings from `by` (every other reviewer approves), checks it blocks,
    and triages every finding `decision`. Returns the qualified finding keys."""
    paths = [g.write_review(d, rnd, rv["id"], by.get(rv["id"], [])) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    keys = [f"{rid}/{f['id']}" for rid, fs in by.items() for f in fs]
    for k in keys:
        t = g.rx(d, "triage", "CP1", f'{k}={decision}:"real for the users"')
        assert t.returncode == 0, t.stdout + t.stderr
    return keys


def _start(name, risk="normal", config=None):
    d = g.project(name, extra_cp={"risk": risk}, config=config)
    g.approve_plan(d)
    g.to_adversarial(d)
    return d


def _pick(rnd, lens):
    return next(rv["id"] for rv in rnd["reviewers"] if _lens(rv["id"]) == lens)


def _is_fix_round(case, d, rnd, reviewer, keys, what):
    check(f"[{case}] {what}: exactly one reviewer is named", len(rnd["reviewers"]) == 1, rnd["reviewers"])
    check(f"[{case}] {what}: it is {reviewer}, the reviewer that raised the finding being fixed",
          [rv["id"] for rv in rnd["reviewers"]] == [reviewer], rnd["reviewers"])
    check(f"[{case}] {what}: --json says it is a fix round", rnd.get("fix_round") is True, rnd)
    ff = rnd.get("fix_for") or {}
    check(f"[{case}] {what}: fix_for names the blocked round, its reviewer and the findings being fixed",
          ff.get("round") == rnd["round"] - 1 and ff.get("reviewer") == reviewer
          and sorted(ff.get("findings") or []) == sorted(keys), ff)
    check(f"[{case}] {what}: it is neither a small-fix nor a narrowed round",
          rnd.get("small_fix") is False and rnd.get("narrowed") is False, rnd)
    adv = _state(d)["checkpoints"]["CP1"]["adv_round"]
    check(f"[{case}] {what}: state.json's open round records fix_round and its one reviewer",
          adv.get("fix_round") is True and adv.get("reviewers") == [reviewer], adv)
    txt = _reviewers(d)
    check(f"[{case}] {what}: the text output identifies the fix round and its reviewer",
          txt.returncode == 0 and "Fix round: one reviewer" in txt.stdout and reviewer in txt.stdout,
          txt.stdout + txt.stderr)


def _not_fix_round(case, rnd, what):
    check(f"[{case}] {what}: not labelled a fix round",
          rnd.get("fix_round") is False and rnd.get("fix_for") is None, rnd)


# ------------------------------------------------------------------------------------------------ cases

def f01_normal_risk_large_fix():
    d = _start("f01")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    keys = _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 30, "f01")
    n = _lines_since(d, rnd1)
    check("[CP5-F01] (fixture sanity) the fix changes more than 20 lines since round 1", n > 20, n)
    _is_fix_round("CP5-F01", d, _open(d), rid, keys, "normal risk, a 30-line fix")


def f02_reviewer_tied_to_the_finding():
    d = _start("f02")
    rnd1 = _open(d)
    rid = _pick(rnd1, "correctness")
    keys = _block(d, rnd1, {rid: [_finding("C1"), _finding("C2", "blocker")]})
    _fix(d, 3, "f02")
    _is_fix_round("CP5-F02", d, _open(d), rid, keys, "normal risk, the correctness reviewer's two findings")


def f03_high_risk_small_fix():
    d = _start("f03", risk="high")
    rnd1 = _open(d)
    check("[CP5-F03] (fixture sanity) a high-risk first round names more than one reviewer",
          len(rnd1["reviewers"]) > 1, rnd1["reviewers"])
    rid = rnd1["reviewers"][-1]["id"]
    keys = _block(d, rnd1, {rid: [_finding("H1", "blocker")]})
    _fix(d, 2, "f03")
    _is_fix_round("CP5-F03", d, _open(d), rid, keys, "high risk, a 2-line fix")


def f04_high_risk_large_fix():
    d = _start("f04", risk="high")
    rnd1 = _open(d)
    rid = rnd1["reviewers"][0]["id"]
    keys = _block(d, rnd1, {rid: [_finding("H1")]})
    _fix(d, 40, "f04")
    n = _lines_since(d, rnd1)
    check("[CP5-F04] (fixture sanity) the fix changes more than 20 lines since round 1", n > 20, n)
    _is_fix_round("CP5-F04", d, _open(d), rid, keys, "high risk, a 40-line fix")


def f05_first_round_keeps_full_set():
    for risk, want in (("normal", 2), ("high", None)):
        d = _start(f"f05-{risk}", risk=risk)
        rnd1 = _open(d)
        n = len(json.loads(g.rx(d, "config").stdout)["review_depth"][risk]) if want is None else want
        check(f"[CP5-F05] {risk} risk: round 1 names its full reviewer set (more than one reviewer)",
              len(rnd1["reviewers"]) > 1 and len(rnd1["reviewers"]) <= n, rnd1["reviewers"])
        _not_fix_round("CP5-F05", rnd1, f"{risk} risk round 1")
        check(f"[CP5-F05] {risk} risk: round 1's text output has no fix-round line",
              "Fix round" not in _reviewers(d).stdout)


def f06_rounds_not_following_a_block():
    # a round replaced before anyone reviewed it: the next round keeps the full set
    d = _start("f06a")
    rnd1 = _open(d)
    _fix(d, 30, "f06a")
    rnd2 = _open(d)
    check("[CP5-F06] a round replacing an unreviewed one keeps the full set",
          sorted(rv["id"] for rv in rnd2["reviewers"]) == sorted(rv["id"] for rv in rnd1["reviewers"]),
          rnd2["reviewers"])
    _not_fix_round("CP5-F06", rnd2, "replacing an unreviewed round")
    # an approved round, then a fix over 20 lines: the normal full set, not a fix round
    d = _start("f06b")
    rnd1 = _open(d)
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    assert g.rx(d, "adv-record", "CP1", "--reviews", *paths).returncode == 0
    _fix(d, 30, "f06b")
    rnd2 = _open(d)
    check("[CP5-F06] after an approval, a 30-line change opens the full set",
          sorted(rv["id"] for rv in rnd2["reviewers"]) == sorted(rv["id"] for rv in rnd1["reviewers"]),
          rnd2["reviewers"])
    _not_fix_round("CP5-F06", rnd2, "after an approval")
    # a blocked round whose only finding was rebutted, code unchanged: no fixer ran, so not a fix round
    d = _start("f06c")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, decision="rebut")
    rnd2 = _open(d)
    _not_fix_round("CP5-F06", rnd2, "after a block that was only rebutted")


def f07_two_reviewers_being_fixed_is_ambiguous():
    d = _start("f07")
    rnd1 = _open(d)
    by = {rv["id"]: [_finding("A1")] for rv in rnd1["reviewers"]}
    _block(d, rnd1, by)
    _fix(d, 3, "f07")
    rnd2 = _open(d)
    _not_fix_round("CP5-F07", rnd2, "findings fixed from two reviewers")
    check("[CP5-F07] --json records why it is not a fix round (two reviewers)",
          "2 reviewers" in str(rnd2.get("fix_round_skipped") or ""), rnd2.get("fix_round_skipped"))
    check("[CP5-F07] the existing rules still apply: both lenses with an open major are asked again",
          sorted(_lens(rv["id"]) for rv in rnd2["reviewers"]) == ["correctness", "standards"], rnd2["reviewers"])
    txt = _reviewers(d)
    check("[CP5-F07] the text output says it is not a fix round and why",
          "Not a fix round" in txt.stdout and "2 reviewers" in txt.stdout, txt.stdout + txt.stderr)


def f08_fix_round_approval_and_converge_rule():
    d = _start("f08", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 25, "f08")
    rnd2 = _open(d)
    check("[CP5-F08] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    _block(d, rnd2, {rid: [_finding("M1")]})
    _fix(d, 26, "f08b")
    rnd3 = _open(d)
    check("[CP5-F08] a fix round that blocked again brings back the full set (existing converge rule)",
          sorted(_lens(rv["id"]) for rv in rnd3["reviewers"]) == ["correctness", "standards"], rnd3["reviewers"])
    _not_fix_round("CP5-F08", rnd3, "round 3 after a blocked fix round")
    # round 3 blocks on the standards reviewer again: round 4 is a fix round again, and its approval names it
    _block(d, rnd3, {_pick(rnd3, "standards"): [_finding("M1")]})
    _fix(d, 27, "f08c")
    rnd4 = _open(d)
    check("[CP5-F08] round 4 after a blocked full round is a fix round with one reviewer",
          rnd4.get("fix_round") is True and len(rnd4["reviewers"]) == 1, rnd4)
    path = g.write_review(d, rnd4, rnd4["reviewers"][0]["id"], [])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    check("[CP5-F08] the fix round's clean review approves the gate, naming one reviewer",
          r.returncode == 0 and "all 1 reviewer(s)" in r.stdout, r.stdout + r.stderr)


def f09_replaced_fix_round_stays_a_fix_round():
    d = _start("f09")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    keys = _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 4, "f09")
    rnd2 = _open(d)
    check("[CP5-F09] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    _fix(d, 30, "f09b")  # the fixer changes the code again before anyone reviews round 2
    rnd3 = _open(d)
    check("[CP5-F09] the round replacing an unreviewed fix round is still a fix round with one reviewer",
          rnd3.get("fix_round") is True and [rv["id"] for rv in rnd3["reviewers"]] == [rid], rnd3)
    check("[CP5-F09] its fix_for still names round 1's reviewer and findings",
          (rnd3.get("fix_for") or {}).get("round") == 1 and (rnd3.get("fix_for") or {}).get("reviewer") == rid
          and sorted((rnd3.get("fix_for") or {}).get("findings") or []) == sorted(keys), rnd3.get("fix_for"))


CASES = [("CP5-F01", f01_normal_risk_large_fix), ("CP5-F02", f02_reviewer_tied_to_the_finding),
         ("CP5-F03", f03_high_risk_small_fix), ("CP5-F04", f04_high_risk_large_fix),
         ("CP5-F05", f05_first_round_keeps_full_set), ("CP5-F06", f06_rounds_not_following_a_block),
         ("CP5-F07", f07_two_reviewers_being_fixed_is_ambiguous),
         ("CP5-F08", f08_fix_round_approval_and_converge_rule),
         ("CP5-F09", f09_replaced_fix_round_stays_a_fix_round)]


def main():
    for cid, fn in CASES:
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception:
            check(f"[{cid}] ran without an exception", False, traceback.format_exc())
    print(f"\n{PASS} passed, {FAIL} failed")
    rep = os.environ.get("RATCHET_TEST_REPORT")
    if rep:
        cases = [{"id": c, "status": "passed" if ok_ else "failed",
                  "message": "; ".join(CASE_MESSAGES.get(c, []))} for c, ok_ in sorted(CASE_STATUS.items())]
        Path(rep).write_text(json.dumps({"cases": cases}))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
