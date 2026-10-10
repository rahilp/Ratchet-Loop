#!/usr/bin/env python3
"""
CP5 tests: later review rounds cost less.   python3 tests/test_converge.py

After round 1 only a lens with an open blocker/major is asked again; two slots that resolve to the same
reviewer (same company, model, lens) run once and the approval message names the true distinct count; a round
replaced before anyone wrote a review into its folder is free (does not count toward either round cap) while a
round where a review was already written still counts; and from a configurable round (default: round 3,
`converge_after_round` + 1) a minor finding no longer blocks, even in code that just changed, and is listed in
`ratchet.py deferred`.

Reuses tests/test_gates.py (`project`, `approve_plan`, `write_tests`, `to_adversarial`, `open_round`, `write_review`,
`rx`, `R0`) for the throwaway git projects, the same real-CLI-subprocess, private-RATCHET_HOME way every suite in
this repo drives the engine, and tests/test_providers.py (`fake_bin`, `env_with`, `EXT`, `open_round`) for the one
case (CP5-T04) that needs two independently-registered command-kind reviewers backed by working fake CLIs. No
real reviewer CLI, pytest or network call is ever used; CP5-T11/T12 only read repository text files.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import test_gates as g        # noqa: E402  (project, approve_plan, write_tests, to_adversarial, open_round, ...)
import test_providers as tp   # noqa: E402  (fake_bin, env_with, EXT, open_round for command-kind providers)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-[TX]\d+[a-z]?)\]")


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

MAJOR = [{"id": "M1", "severity": "major", "file": "src/f.js", "description": "bug",
          "user_impact": "a user hits a crash"}]
MINOR_COMMENT = [{"id": "N1", "severity": "minor", "file": "src/f.js", "description": "needs a comment"}]


def _lens_ids(rnd) -> dict:
    """lens -> reviewer id, for whatever reviewers the round actually lists (1 or 2 of them)."""
    return {r["lens"]: r["id"] for r in rnd["reviewers"]}


def _round1_block(name, config=None):
    """Normal-risk CP1, round 1: the standards-lens reviewer reports one major finding on src/f.js, the
    correctness-lens reviewer approves with no findings. The finding still blocks, so the fixer edits src/f.js
    and `behavior CP1` passes again. Shared setup for CP5-T01/T02/T03."""
    d = g.project(name, extra_cp={"risk": "normal"}, config=config)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    assert len(rnd["reviewers"]) == 2, rnd["reviewers"]
    lens = _lens_ids(rnd)
    paths = [g.write_review(d, rnd, lens["standards"], MAJOR), g.write_review(d, rnd, lens["correctness"], [])]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    (d / "src/f.js").write_text("export const f = () => 1; // fixed\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    return d


def _carry_to_round3(name, third_severity):
    """Normal-risk CP1 carried through round 1 and round 2 with the same minor finding ('needs a comment')
    reported each round by one lens on src/f.js, which is edited again before each round (so it stays in scope
    and round 2 still blocks, matching the existing rule). The file is edited once more before round 3, which
    reports a finding of `third_severity` on it. Returns (project dir, round-3 adv-record result).
    checkin_round=99 disables CP4's drag check-in (out of scope here): two blocking rounds in a row would
    otherwise stop round 3 from opening before this test gets to look at it."""
    d = g.project(name, extra_cp={"risk": "normal"}, config=["checkin_round=99"])
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    for n, tag in ((1, "// r2"), (2, "// r3")):
        lens = _lens_ids(rnd)
        paths = [g.write_review(d, rnd, rid, MINOR_COMMENT if ln == "standards" else [])
                 for ln, rid in lens.items()]
        r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
        assert r.returncode == 2, f"round {n} should still block: {r.stdout}{r.stderr}"
        (d / "src/f.js").write_text(f"export const f = () => 1; {tag}\n")
        r = g.rx(d, "behavior", "CP1")
        assert r.returncode == 0, r.stdout + r.stderr
        rnd = g.open_round(d)
    finding = [dict(MINOR_COMMENT[0], severity=third_severity)]
    if third_severity != "minor":
        finding[0]["user_impact"] = "a confused reader files a support ticket over it"
    lens = _lens_ids(rnd)
    paths = [g.write_review(d, rnd, rid, finding if ln == "standards" else []) for ln, rid in lens.items()]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    return d, r


# ------------------------------------------------------------------------------------------------ cases

def t01_approved_lens_not_rerun():
    d = _round1_block("t01")
    rnd2 = g.open_round(d)
    check("[CP5-T01] a lens that approved last round (correctness) is not asked again",
          not any(r["lens"] == "correctness" for r in rnd2["reviewers"]), rnd2["reviewers"])


def t02_open_major_lens_rerun_alone():
    d = _round1_block("t02")
    rnd2 = g.open_round(d)
    check("[CP5-T02] a lens with an open major (standards) is asked again, and round 2 lists only it",
          len(rnd2["reviewers"]) == 1 and rnd2["reviewers"][0]["lens"] == "standards", rnd2["reviewers"])


def t03_unresolved_blocker_restores_full_set():
    # two blocking rounds in a row would otherwise trip CP4's check-in before round 3 can open; that check-in
    # itself is out of scope here, so disable it (checkin_round=99) and focus purely on reviewer selection.
    d = _round1_block("t03", config=["checkin_round=99"])
    rnd2 = g.open_round(d)
    lens2 = _lens_ids(rnd2)
    paths = [g.write_review(d, rnd2, rid, MAJOR if ln == "standards" else []) for ln, rid in lens2.items()]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr  # the same major is still open
    (d / "src/f.js").write_text("export const f = () => 1; // fixed again\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd3 = g.open_round(d)
    check("[CP5-T03] a fix that leaves the blocker open brings back the full reviewer set in round 3",
          sorted(r["lens"] for r in rnd3["reviewers"]) == ["correctness", "standards"], rnd3["reviewers"])


def t04_duplicate_slot_runs_once():
    d = g.project("t04", extra_cp={"risk": "normal", "reviewers": ["idA-standards", "idB-standards"]})
    b = tp.fake_bin("idA", "idB")
    env = tp.env_with(b)
    for pid in ("idA", "idB"):
        r = g.rx(d, "providers", "--add", pid, "--command", json.dumps([str(b / pid)]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env=env)
    check("[CP5-T04] two slots that resolve to the same reviewer (same company/model/lens) run as one",
          len(rnd["reviewers"]) == 1, rnd["reviewers"])
    # run every reviewer the round actually lists (robust to the pre-fix behaviour of not collapsing the
    # duplicate slot, so this still produces a real pass/fail instead of an unrelated "missing review" crash)
    outs = []
    for rv in rnd["reviewers"]:
        p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", rv["id"], "--cp", "CP1",
                            "--out", rv["output"]], cwd=d, capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr
        outs.append(rv["output"])
    r = g.rx(d, "adv-record", "CP1", "--reviews", *outs, env=env)
    check("[CP5-T04] the approval message names the true distinct count: 'all 1 reviewer(s)'",
          r.returncode == 0 and "all 1 reviewer(s)" in r.stdout, r.stdout + r.stderr)


def t05_free_replacement_before_any_review():
    d = g.project("t05")
    g.approve_plan(d)
    g.to_adversarial(d)
    g.open_round(d)                                        # round 1, nobody reviews it
    (d / "src/a.js").write_text("export const a = 2; // t05\n")
    rnd2 = g.open_round(d)                                 # round 1 is replaced before any review was written
    path = g.write_review(d, rnd2, g.R0(rnd2), [])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    assert r.returncode == 0, r.stdout + r.stderr
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    adv = st["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T05] a round replaced before any review was written costs nothing: attempts == 1",
          adv.get("attempts") == 1, adv.get("attempts"))
    check("[CP5-T05] ...and adv_rounds_total == 1", st.get("adv_rounds_total") == 1, st.get("adv_rounds_total"))


def t06_replacement_after_a_review_still_counts():
    d = g.project("t06", extra_cp={"risk": "normal"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = g.open_round(d)
    lens1 = _lens_ids(rnd1)
    g.write_review(d, rnd1, next(iter(lens1.values())), [])  # a review is written, but adv-record is never run
    (d / "src/a.js").write_text("export const a = 3; // t06\n")
    rnd2 = g.open_round(d)                                   # round 1 is replaced, but it already had a review
    lens2 = _lens_ids(rnd2)
    paths = [g.write_review(d, rnd2, rid, []) for rid in lens2.values()]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    adv = st["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T06] a round where a review was already written still counts: attempts == 2",
          adv.get("attempts") == 2, adv.get("attempts"))
    check("[CP5-T06] ...and adv_rounds_total == 2", st.get("adv_rounds_total") == 2, st.get("adv_rounds_total"))


def t07_minor_stops_blocking_from_round3():
    d, r = _carry_to_round3("t07", "minor")
    check("[CP5-T07] from round 3 a minor finding no longer blocks the gate, even in just-changed code",
          r.returncode == 0, r.stdout + r.stderr)
    rd = g.rx(d, "deferred")
    check("[CP5-T07] `ratchet.py deferred` lists the minor finding for CP1",
          "needs a comment" in rd.stdout and "CP1" in rd.stdout, rd.stdout)


def t08_major_still_blocks_from_round3():
    _d, r = _carry_to_round3("t08", "major")
    check("[CP5-T08] from round 3 a major finding still blocks (exits non-zero)",
          r.returncode != 0, r.stdout + r.stderr)


def t09_converge_after_round_is_configurable():
    d = g.project("t09", extra_cp={"risk": "normal"}, config=["converge_after_round=1"])
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = g.open_round(d)
    lens1 = _lens_ids(rnd1)
    paths = [g.write_review(d, rnd1, lens1["standards"], MAJOR), g.write_review(d, rnd1, lens1["correctness"], [])]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    (d / "src/f.js").write_text("export const f = () => 1; // t09 fix\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = g.open_round(d)
    lens2 = _lens_ids(rnd2)
    fresh_minor = [{"id": "N2", "severity": "minor", "file": "src/f.js", "description": "needs a tweak"}]
    paths2 = [g.write_review(d, rnd2, rid, fresh_minor if ln == "standards" else []) for ln, rid in lens2.items()]
    r2 = g.rx(d, "adv-record", "CP1", "--reviews", *paths2)
    check("[CP5-T09] converge_after_round=1 makes round 2 (not round 3) already pass on a fresh minor finding",
          r2.returncode == 0, r2.stdout + r2.stderr)


def t10_refund_never_goes_below_zero():
    d = g.project("t10")
    g.approve_plan(d)
    g.to_adversarial(d)
    g.open_round(d)                                        # round 1, no review
    (d / "src/a.js").write_text("export const a = 4; // t10a\n")
    g.open_round(d)                                        # free replacement #1 -> round 2, still no review
    (d / "src/a.js").write_text("export const a = 5; // t10b\n")
    rnd3 = g.open_round(d)                                 # free replacement #2 -> round 3
    path = g.write_review(d, rnd3, g.R0(rnd3), [])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    assert r.returncode == 0, r.stdout + r.stderr
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    adv = st["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T10] two free replacements in a row never inflate or go negative: attempts == 1",
          adv.get("attempts") == 1, adv.get("attempts"))
    check("[CP5-T10] ...and adv_rounds_total == 1", st.get("adv_rounds_total") == 1, st.get("adv_rounds_total"))


def t11_rules_documented_in_plain_words():
    skill = (ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP5-T11] SKILL.md no longer claims every reviewer, including ones that approved, reviews every round",
          re.search(r"including the ones that approved", skill, re.I) is None, "")
    check("[CP5-T11] SKILL.md states only a lens with an open blocker/major is re-run",
          re.search(r"only a lens with an open (?:blocker|major)", skill, re.I) is not None, "")
    check("[CP5-T11] SKILL.md states a round replaced before any review was written is free",
          re.search(r"replaced before any review.{0,60}free", skill, re.I) is not None
          or re.search(r"free.{0,60}no review.{0,30}(?:was )?written", skill, re.I) is not None, "")
    check("[CP5-T11] SKILL.md states a duplicate reviewer slot runs once, with the real count shown",
          re.search(r"runs? once", skill, re.I) is not None
          and re.search(r"real (?:count|number)", skill, re.I) is not None, "")
    check("[CP5-T11] SKILL.md states minor findings stop blocking from a configurable round",
          "converge_after_round" in skill, "")
    check("[CP5-T11] schemas.md documents the converge_after_round config key",
          "converge_after_round" in schemas, "")


def t12_fixer_told_to_fix_listed_findings_only():
    fixer = (ROOT / "agents" / "ratchet-fixer.md").read_text()
    check("[CP5-T12] the fixer is told to address every finding it was handed (fix or rebut)",
          re.search(r"every\*{0,2} finding", fixer) is not None, "")
    check("[CP5-T12] the fixer is told to touch only what the findings require",
          "Touch only what the findings require" in fixer, "")
    check("[CP5-T12] the fixer is not told to invent or redesign the round/reviewer-selection mechanism itself",
          re.search(r"redesign.{0,30}(?:round|reviewer)|invent.{0,30}mechanism", fixer, re.I) is None, "")


TESTS = [
    ("CP5-T01", t01_approved_lens_not_rerun),
    ("CP5-T02", t02_open_major_lens_rerun_alone),
    ("CP5-T03", t03_unresolved_blocker_restores_full_set),
    ("CP5-T04", t04_duplicate_slot_runs_once),
    ("CP5-T05", t05_free_replacement_before_any_review),
    ("CP5-T06", t06_replacement_after_a_review_still_counts),
    ("CP5-T07", t07_minor_stops_blocking_from_round3),
    ("CP5-T08", t08_major_still_blocks_from_round3),
    ("CP5-T09", t09_converge_after_round_is_configurable),
    ("CP5-T10", t10_refund_never_goes_below_zero),
    ("CP5-T11", t11_rules_documented_in_plain_words),
    ("CP5-T12", t12_fixer_told_to_fix_listed_findings_only),
]


def write_report():
    import os
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
