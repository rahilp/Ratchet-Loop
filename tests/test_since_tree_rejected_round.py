#!/usr/bin/env python3
"""
CP1 tests: a review round adv-record REFUSED must not advance the baseline.   python3 tests/test_since_tree_rejected_round.py

Behavior under test: in `cmd_reviewers` (skills/ratchet-orchestrator/scripts/ratchet.py), when a new round opens
it sets the new round's `prev_round_tree` (the baseline a reviewer's diff is measured from) to the checkpoint's
own last RECORDED round's fingerprint (`gates.adversarial.rounds[-1]['fingerprint']`, or `None` when nothing is
recorded yet) -- never to the replaced round's own fingerprint, even when that round held a written review that
adv-record refused (omitted_files, too fast, an approval with no files listed) and never recorded. So a refused
or never-reviewed round never moves the baseline, and the real fix is shown again rather than hidden behind an
empty (or near-empty) diff, while a round adv-record actually records (approved or blocked) still moves the
baseline exactly as before.

Requirement-to-check map (planned case -> what it proves):
  CP1-T01  a recorded blocked round still measures the next fix round from itself (regression)
  CP1-T02  a recorded approval still measures the next small fix from itself (regression)
  CP1-T03  an omitted_files refusal does not advance the baseline; the fix is shown again
  CP1-T04  a too-fast refusal and an empty-files_reviewed refusal do not advance the baseline either
  CP1-T05  two refused rounds in a row still measure from the last recorded round
  CP1-T06  a round recorded after a refused one becomes the new baseline
  CP1-T07  a refused small fix after an approval shows that fix again next round
  CP1-T08  a refused first round leaves prev_round_tree null, not its own fingerprint
  CP1-T09  a refused round with a written review still costs one round; an unreviewed one is still free
  CP1-T10  running out of rounds after refusals stops with the visible needs-a-decision message
  CP1-T11  a stuck unreviewed round with a wrong baseline heals on the next (free) round
  CP1-T12  a stuck round holding a refused review and a wrong baseline heals on the next round
  CP1-T13  asking again with no code change keeps the same open round and its baseline
  CP1-T14  adv-record still refuses exactly what it refused before, each with its own message
  CP1-T15  only the code that sets a new round's baseline changed (structural)
  CP1-T16  the docs describe where the baseline comes from and the attempt-cost decision

Black-box: every case drives the real `ratchet.py` CLI in a throwaway one-checkpoint git project, reusing the
helpers of tests/test_fix_round.py (`_own_home`, `_open`, `_triage`, `_counters`, `_ids`, `_start`, `_pick`,
`_block`, `_approve`, `_record`, `_out`, `_gate`, `_state`) and tests/test_fix_round_diff.py (`_ext_start`,
`_ext_blocked_round`, `_ext_record_aged`, `_approved_ext`, `_edit`, `_finding`, `_src`, `_prompt`, `_since`,
`_added`, `_reply`, `_rewrite_cli`, `_age`, `_cp`, `_append_code`, `_ext_review`), and tests/test_fix_round_diff_fixes.py
(`_big_fix_round`), with their `check` pointed at this file's own `check`. The baseline is read two ways: the open
round's `prev_round_tree` in the project's state.json, and what the next reviewer is actually shown (the prompt
`external_review.py --dry-run` writes). Review times are set with `os.utime` (via `_age`), never by sleeping.
Refusals are produced the real way: a review the fake codex CLI signs with `omitted_files`, a review dated too
soon after its round opened, or an approval with an empty `files_reviewed` list. Only T11 and T12 write a wrong
`prev_round_tree` into the throwaway project's state.json, standing in for lane R CP4's already-stuck round.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                            # noqa: E402,F401
import test_gates as g                 # noqa: E402
import test_fix_round as tf            # noqa: E402
import test_fix_round_diff as t        # noqa: E402
import test_fix_round_diff_fixes as xf  # noqa: E402

PLANNED = [f"CP1-T{i:02d}" for i in range(1, 17)]
PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP1-[FT]\d+)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'OK ' if cond else 'FAIL'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# Every engine call in this file sees an idle machine, and the borrowed helpers record their assertions here, not
# into their own files' counters. Order matters: importing test_fix_round_diff_fixes re-points tf.check/t.check at
# ITS OWN check function as a side effect of its import, so this file's reassignment must come last.
g.ENV["RATCHET_FAKE_LOADAVG"] = "0"
tf.check = check
t.check = check
xf.check = check


ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
_REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
               "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
               "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
               "m.record_chain(Path(sys.argv[2]))\n")


def _corrupt_prev_round_tree(d, env, wrong_tree):
    """T11/T12 only: overwrites the throwaway project's open adv_round.prev_round_tree (standing in for lane R
    CP4's already-stuck round), then re-baselines the engine's own integrity chain with its own `record_chain`
    (what `accept-state` does for a person at a real terminal), so the next engine call still trusts state.json."""
    sp = Path(d) / ".ratchet/runs/f/state.json"
    state = json.loads(sp.read_text())
    state["checkpoints"]["CP1"]["adv_round"]["prev_round_tree"] = wrong_tree
    sp.write_text(json.dumps(state))
    r = subprocess.run([sys.executable, "-c", _REBASELINE, str(ENGINE), str(sp)], cwd=d, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout


def _blocked_aged(d, env, tag, age=10):
    """Round 1, recorded blocked by codex-standards' finding, with its review dated `age` seconds after the round
    opened (so a configured min_review_seconds > 0 does not itself refuse it). Returns round 1."""
    rnd1 = tf._open(d, env=env)
    check(f"[{tag}] (fixture) round 1 names codex-standards and host-correctness",
          sorted(tf._ids(rnd1)) == ["codex-standards", "host-correctness"], tf._ids(rnd1))
    t._ext_record_aged(d, rnd1, env, age, expect=2)
    return rnd1


# ------------------------------------------------------------------------------------------------ CP1-T01..T02

def t01_a_recorded_blocked_round_still_measures_the_next_fix_round_from_itself():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-A first problem", "FIX-A fix it")
        fb = t._finding("ZR-2", "src/bravo.js", 3, "DESC-R2 second problem", "FIX-R2 fix it")
        d, env, b = t._ext_start("c1t01", [fa], cfg=["checkin_round=99"])
        rnd1 = t._ext_blocked_round(d, env, "CP1-T01")
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP1-T01] round 2's open round has prev_round_tree == F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"],
              (t._cp(d)["adv_round"].get("prev_round_tree"), rnd1["fingerprint"]))
        t._rewrite_cli(b, "codex", t._reply(fb))
        r2 = tf._ext_record(d, rnd2, env)
        check("[CP1-T01] (fixture) round 2 is recorded blocked with a new finding", r2.returncode == 2, tf._out(r2))
        tf._triage(d, ["codex-standards/ZR-2"], env=env)
        t._edit(d, "src/bravo.js",
                t._src("bravo", t.BRAVO).replace("bravo_two = 2;", "bravo_two = 2; // ZQ-FIX-C"), env)
        rnd3 = tf._open(d, env=env)
        check("[CP1-T01] round 3's prev_round_tree == F2 exactly",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd2["fingerprint"],
              (t._cp(d)["adv_round"].get("prev_round_tree"), rnd2["fingerprint"]))
        p = t._prompt(d, env, rnd3["reviewers"][0])
        check("[CP1-T01] round 3's prompt shows the added ZQ-FIX-C line and finding ZR-2, not ZQ-FIX-A",
              t._added(p, "ZQ-FIX-C") and "ZR-2" in p and "ZQ-FIX-A" not in p, p[-1500:])
        check("[CP1-T01] it names F2's first 10 characters, not F1's",
              rnd2["fingerprint"][:10] in p and rnd1["fingerprint"][:10] not in p, "")
        check("[CP1-T01] counters are (3, 3)", tf._counters(d) == (3, 3), tf._counters(d))


def t02_a_recorded_approval_still_measures_the_next_small_fix_from_itself():
    with tf._own_home():
        d, env, rnd1 = t._approved_ext("c1t02")
        check("[CP1-T02] (fixture) round 1 is approved",
              t._cp(d)["gates"]["adversarial"].get("status") == "passed",
              t._cp(d)["gates"]["adversarial"].get("status"))
        t._append_code(d, "src/bravo.js", 2, "c1t02", env, first_marker="ZQ-FIX-B")
        rnd2 = tf._open(d, env=env)
        check("[CP1-T02] (fixture) round 2 opens as a one-reviewer small fix after approval",
              rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1, tf._ids(rnd2))
        check("[CP1-T02] round 2's prev_round_tree == F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        r2 = tf._ext_record(d, rnd2, env, expect=0)
        check("[CP1-T02] (fixture) round 2's one review approves and is recorded", r2.returncode == 0, tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t02b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T02] round 3's prev_round_tree == F2",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd2["fingerprint"], "")
        p = t._prompt(d, env, rnd3["reviewers"][0])
        check("[CP1-T02] round 3's prompt shows the ZQ-LATE line and not round 2's bravo edit",
              t._added(p, "ZQ-LATE") and "ZQ-FIX-B" not in p, p[-1200:])
        check("[CP1-T02] no open findings are listed",
              re.search(r"no (open )?(blocking )?findings", p, re.I) is not None, p[-800:])


# ------------------------------------------------------------------------------------------------ CP1-T03..T07

def t03_an_omitted_files_refusal_shows_the_fix_again_next_round():
    with tf._own_home():
        d, env, rnd2 = xf._big_fix_round("c1t03")
        r, paths = t._ext_record_aged(d, rnd2, env, 60, expect=1)
        check("[CP1-T03] (fixture) the oversized fix's review is refused for omitted_files",
              r.returncode == 1 and "never counts" in tf._out(r), tf._out(r))
        rnd1_fp = t._cp(d)["gates"]["adversarial"]["rounds"][0]["fingerprint"]
        t._append_code(d, "src/charlie.js", 1, "c1t03b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T03] round 3's prev_round_tree == F1 (round 1's fingerprint), not F2",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1_fp
              and t._cp(d)["adv_round"].get("prev_round_tree") != rnd2["fingerprint"],
              (t._cp(d)["adv_round"].get("prev_round_tree"), rnd1_fp, rnd2["fingerprint"]))
        check("[CP1-T03] round 3 is still a one-reviewer fix round",
              tf._ids(rnd3) == ["codex-standards"] and rnd3.get("small_fix") is True, tf._ids(rnd3))
        p = t._prompt(d, env, rnd3["reviewers"][0])
        check("[CP1-T03] round 3's prompt names F1's first 10 characters and not F2's",
              rnd1_fp[:10] in p and rnd2["fingerprint"][:10] not in p, "")
        check("[CP1-T03] it names src/bravo.js, the fix file no finding mentions (shown or listed as not shown)",
              "src/bravo.js" in p, p[-1500:])


def t04a_too_fast_also_shows_the_fix_again():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = t._ext_start("c1t04a", [fa], cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T04", age=10)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP1-T04] (too_fast) round 2 is the one-reviewer fix round",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True, tf._ids(rnd2))
        out = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out, 0)
        r2 = tf._record(d, rnd2, [out], env=env)
        check("[CP1-T04] (too_fast) adv-record refuses it as too fast",
              r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        check("[CP1-T04] (too_fast) the review file stays in round 2's folder", Path(out).exists(), out)
        t._append_code(d, "src/charlie.js", 1, "c1t04ab", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T04] (too_fast) round 3's prev_round_tree == F1, not F2",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"]
              and t._cp(d)["adv_round"].get("prev_round_tree") != rnd2["fingerprint"],
              (t._cp(d)["adv_round"].get("prev_round_tree"), rnd1["fingerprint"], rnd2["fingerprint"]))
        check("[CP1-T04] (too_fast) round 3 is still a one-reviewer fix round for codex-standards",
              tf._ids(rnd3) == ["codex-standards"], tf._ids(rnd3))
        p = t._prompt(d, env, rnd3["reviewers"][0])
        check("[CP1-T04] (too_fast) the prompt has an added line with ZQ-FIX-A and one with ZQ-LATE, "
              "and names F1 not F2",
              t._added(p, "ZQ-FIX-A") and t._added(p, "ZQ-LATE") and rnd1["fingerprint"][:10] in p
              and rnd2["fingerprint"][:10] not in p, p[-1500:])
        check("[CP1-T04] (too_fast) gates.adversarial.rounds still has exactly one entry (round 1)",
              len(t._cp(d)["gates"]["adversarial"].get("rounds") or []) == 1,
              t._cp(d)["gates"]["adversarial"].get("rounds"))


def t04b_an_approval_with_no_files_listed_also_shows_the_fix_again():
    # Blocked by the HOST reviewer this time (not the other-company one): a host review is written straight into
    # the round's own folder (through the real guard hook, as g.write_review does), so an approval with an empty
    # files_reviewed list can be produced the real way, without editing a script-signed review after the fact
    # (external_review.py always re-stamps files_reviewed from its own scope, so it can never carry an empty one).
    with tf._own_home():
        d, env, b = t._ext_start("c1t04b", cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = tf._open(d, env=env)
        check("[CP1-T04] (no_files) (fixture) round 1 names codex-standards and host-correctness",
              sorted(tf._ids(rnd1)) == ["codex-standards", "host-correctness"], tf._ids(rnd1))
        paths = [tf._ext_review(d, rnd1, rv["id"], env) if rv.get("kind") == "command"
                 else g.write_review(d, rnd1, rv["id"], [t._finding("ZH-1", "src/alpha.js", 2, "DESC-H bad",
                                                                    "FIX-H good")])
                 for rv in rnd1["reviewers"]]
        for p in paths:
            t._age(d, p, 10)
        r1 = tf._record(d, rnd1, paths, env=env)
        check("[CP1-T04] (no_files) (fixture) round 1 is recorded blocked by the host reviewer",
              r1.returncode == 2, tf._out(r1))
        tf._triage(d, ["host-correctness/ZH-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP1-T04] (no_files) round 2 is the one-reviewer fix round for host-correctness",
              tf._ids(rnd2) == ["host-correctness"] and rnd2.get("small_fix") is True, tf._ids(rnd2))
        out = g.write_review(d, rnd2, "host-correctness", [], files_reviewed=[])
        t._age(d, out, 10)
        r2 = tf._record(d, rnd2, [out], env=env)
        check("[CP1-T04] (no_files) adv-record refuses the approval for not listing the changed files",
              r2.returncode != 0 and "files_reviewed" in tf._out(r2), tf._out(r2))
        check("[CP1-T04] (no_files) the review file stays in round 2's folder", Path(out).exists(), out)
        t._append_code(d, "src/charlie.js", 1, "c1t04bb", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T04] (no_files) round 3's prev_round_tree == F1, not F2",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"]
              and t._cp(d)["adv_round"].get("prev_round_tree") != rnd2["fingerprint"],
              (t._cp(d)["adv_round"].get("prev_round_tree"), rnd1["fingerprint"], rnd2["fingerprint"]))
        check("[CP1-T04] (no_files) round 3 is still a one-reviewer fix round for host-correctness",
              tf._ids(rnd3) == ["host-correctness"], tf._ids(rnd3))
        txt = t._text_of_host_row(rnd3)
        check("[CP1-T04] (no_files) the host instruction names F1, not F2, and names both changed files",
              rnd1["fingerprint"][:10] in txt and rnd2["fingerprint"][:10] not in txt
              and "src/alpha.js" in txt and "src/charlie.js" in txt, txt[-1000:])
        check("[CP1-T04] (no_files) gates.adversarial.rounds still has exactly one entry (round 1)",
              len(t._cp(d)["gates"]["adversarial"].get("rounds") or []) == 1,
              t._cp(d)["gates"]["adversarial"].get("rounds"))


def t05_two_refused_rounds_in_a_row_still_measure_from_the_last_recorded_round():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = t._ext_start("c1t05", [fa], cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T05", age=10)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        out2 = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out2, 0)
        r2 = tf._record(d, rnd2, [out2], env=env)
        check("[CP1-T05] round 2's review is refused as too fast", r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t05b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T05] (fixture) round 3 is still a one-reviewer fix round for codex-standards",
              tf._ids(rnd3) == ["codex-standards"], tf._ids(rnd3))
        check("[CP1-T05] round 3's prev_round_tree is F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        out3 = tf._ext_review(d, rnd3, "codex-standards", env)
        t._age(d, out3, 0)
        r3 = tf._record(d, rnd3, [out3], env=env)
        check("[CP1-T05] round 3's review is also refused as too fast", r3.returncode != 0 and "too fast" in tf._out(r3), tf._out(r3))
        t._append_code(d, "src/bravo.js", 1, "c1t05c", env, first_marker="ZQ-FIX-C")
        rnd4 = tf._open(d, env=env)
        check("[CP1-T05] round 4's prev_round_tree is also F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        p = t._prompt(d, env, rnd4["reviewers"][0])
        check("[CP1-T05] round 4's prompt has added lines for ZQ-FIX-A, ZQ-LATE and ZQ-FIX-C",
              t._added(p, "ZQ-FIX-A") and t._added(p, "ZQ-LATE") and t._added(p, "ZQ-FIX-C"), p[-1800:])
        check("[CP1-T05] it names neither F2's nor F3's first 10 characters",
              rnd2["fingerprint"][:10] not in p and rnd3["fingerprint"][:10] not in p, "")


def t06_a_round_recorded_after_a_refused_one_becomes_the_new_baseline():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        fr2 = t._finding("ZR-2", "src/bravo.js", 3, "DESC-R2 second problem", "FIX-R2 fix it")
        d, env, b = t._ext_start("c1t06", [fa], cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T06", age=10)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        out2 = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out2, 0)
        r2 = tf._record(d, rnd2, [out2], env=env)
        check("[CP1-T06] round 2's review is refused as too fast", r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t06b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        t._rewrite_cli(b, "codex", t._reply(fr2))
        r3, _ = t._ext_record_aged(d, rnd3, env, 10, expect=2)
        check("[CP1-T06] (fixture) round 3 is recorded blocked with ZR-2", r3.returncode == 2, tf._out(r3))
        tf._triage(d, ["codex-standards/ZR-2"], env=env)
        t._append_code(d, "src/bravo.js", 1, "c1t06c", env, first_marker="ZQ-FIX-C")
        rnd4 = tf._open(d, env=env)
        check("[CP1-T06] round 4's prev_round_tree == F3 (round 3's fingerprint), not F1 and not F2",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd3["fingerprint"]
              and t._cp(d)["adv_round"].get("prev_round_tree") not in (rnd1["fingerprint"], rnd2["fingerprint"]),
              (t._cp(d)["adv_round"].get("prev_round_tree"), rnd1["fingerprint"], rnd2["fingerprint"],
               rnd3["fingerprint"]))
        p = t._prompt(d, env, rnd4["reviewers"][0])
        check("[CP1-T06] round 4's prompt shows ZQ-FIX-C and ZR-2 and not ZQ-FIX-A",
              t._added(p, "ZQ-FIX-C") and "ZR-2" in p and "ZQ-FIX-A" not in p, p[-1500:])


def t07_a_refused_small_fix_after_an_approval_shows_that_fix_again():
    with tf._own_home():
        d, env, rnd1 = t._approved_ext("c1t07")
        g.rx(d, "config", "--set", "min_review_seconds=5", env=env)
        g.rx(d, "ask", "approve-changes", env=env)
        g.say(d, "yes, go ahead")
        cr = g.rx(d, "approve-changes", env=env)
        assert cr.returncode == 0, tf._out(cr)
        t._append_code(d, "src/bravo.js", 2, "c1t07", env, first_marker="ZQ-FIX-B")
        rnd2 = tf._open(d, env=env)
        check("[CP1-T07] (fixture) round 2 is a one-reviewer small fix after approval",
              rnd2.get("small_fix") is True and tf._after_approval(rnd2), tf._kind(rnd2))
        row = rnd2["reviewers"][0]
        check("[CP1-T07] (fixture) the one reviewer is an other-company reviewer", row.get("kind") == "command", row)
        out = tf._ext_review(d, rnd2, row["id"], env)
        t._age(d, out, 0)
        r2 = tf._record(d, rnd2, [out], env=env)
        check("[CP1-T07] round 2's review is refused as too fast", r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t07b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T07] round 3's prev_round_tree == F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        p = t._prompt(d, env, rnd3["reviewers"][0])
        check("[CP1-T07] round 3's prompt has added lines for both ZQ-FIX-B and ZQ-LATE",
              t._added(p, "ZQ-FIX-B") and t._added(p, "ZQ-LATE"), p[-1500:])


# ------------------------------------------------------------------------------------------------ CP1-T08..T10

def t08_a_refused_first_round_leaves_no_baseline_at_all():
    d = tf._start("c1t08", config=["min_review_seconds=5", "checkin_round=99"])
    rnd1 = tf._open(d)
    check("[CP1-T08] (fixture) round 1 has no prev_round_tree yet",
          t._cp(d)["adv_round"].get("prev_round_tree") is None, t._cp(d)["adv_round"].get("prev_round_tree"))
    p = g.write_review(d, rnd1, rnd1["reviewers"][0]["id"], [])
    t._age(d, p, 0)
    r1 = tf._record(d, rnd1, [p])
    check("[CP1-T08] round 1's review is refused as too fast", r1.returncode != 0 and "too fast" in tf._out(r1), tf._out(r1))
    check("[CP1-T08] nothing is recorded yet: gates.adversarial.rounds is empty",
          not (t._cp(d)["gates"]["adversarial"].get("rounds") or []),
          t._cp(d)["gates"]["adversarial"].get("rounds"))
    t._edit(d, "src/f.js", "export const f = () => 1; // c1t08 edited\n")
    rnd2 = tf._open(d)
    check("[CP1-T08] round 2's prev_round_tree is null, not round 1's fingerprint",
          t._cp(d)["adv_round"].get("prev_round_tree") is None
          and rnd1["fingerprint"] is not None, t._cp(d)["adv_round"].get("prev_round_tree"))
    check("[CP1-T08] round 2 is a full round, not a small fix", rnd2.get("small_fix") is not True,
          rnd2.get("small_fix"))
    check("[CP1-T08] round 2 counts as attempt 2", tf._gate(d).get("attempts") == 2, tf._gate(d).get("attempts"))


def t09_a_refused_reviewed_round_costs_one_but_an_unreviewed_round_stays_free():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = t._ext_start("c1t09", [fa], cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T09", age=10)
        check("[CP1-T09] (fixture) counters are (1,1) after round 1 is recorded blocked",
              tf._counters(d) == (1, 1), tf._counters(d))
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP1-T09] (fixture) counters are (2,2) once round 2 opens", tf._counters(d) == (2, 2),
              tf._counters(d))
        out2 = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out2, 0)
        r2 = tf._record(d, rnd2, [out2], env=env)
        check("[CP1-T09] round 2's review is refused as too fast",
              r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t09b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T09] after round 3 opens the counters are (3,3): the refused round was charged, not refunded",
              tf._counters(d) == (3, 3), tf._counters(d))
        t._append_code(d, "src/bravo.js", 1, "c1t09c", env, first_marker="ZQ-FIX-C-UNREVIEWED")
        rnd4 = tf._open(d, env=env)
        check("[CP1-T09] round 4 replaces round 3 (never reviewed) for free: counters stay (3,3)",
              tf._counters(d) == (3, 3), tf._counters(d))
        check("[CP1-T09] round 4's prev_round_tree is still F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        p = t._prompt(d, env, rnd4["reviewers"][0])
        check("[CP1-T09] round 4's prompt shows ZQ-FIX-A", t._added(p, "ZQ-FIX-A"), p[-1500:])


def t10_running_out_of_rounds_after_refusals_stops_with_the_decision_message():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = t._ext_start("c1t10", [fa],
                                 cfg=["min_review_seconds=5", "max_adversarial_rounds=3", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T10", age=10)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        out2 = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out2, 0)
        r2 = tf._record(d, rnd2, [out2], env=env)
        check("[CP1-T10] round 2's review is refused as too fast", r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t10b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        check("[CP1-T10] (given) round 3's prev_round_tree is F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        p = t._prompt(d, env, rnd3["reviewers"][0])
        check("[CP1-T10] (given) round 3's prompt shows ZQ-FIX-A", t._added(p, "ZQ-FIX-A"), p[-1200:])
        out3 = tf._ext_review(d, rnd3, "codex-standards", env)
        t._age(d, out3, 0)
        r3 = tf._record(d, rnd3, [out3], env=env)
        check("[CP1-T10] round 3's review is also refused as too fast", r3.returncode != 0 and "too fast" in tf._out(r3), tf._out(r3))
        t._edit(d, "src/bravo.js", t._src("bravo", t.BRAVO) + "// c1t10 edited on disk\n", env)
        r4 = g.rx(d, "reviewers", "CP1", env=env)
        check("[CP1-T10] `reviewers` exits 2", r4.returncode == 2, tf._out(r4))
        st = tf._state(d)
        reason = st.get("blocked_reason") or ""
        check("[CP1-T10] the run is blocked with the usual escalation message naming the round count",
              st.get("phase") == "blocked" and "3 adversarial rounds used without approval" in reason
              and "human/director decision" in reason, (st.get("phase"), reason))
        check("[CP1-T10] attempts stays at 3", tf._gate(d).get("attempts") == 3, tf._gate(d).get("attempts"))
        rd = Path(d) / ".ratchet/runs/f/reviews/CP1/adv-round-04"
        check("[CP1-T10] no round-4 folder was created", not rd.exists(), str(rd))
        status_r = g.rx(d, "status", env=env)
        status_out = status_r.stdout or ""
        check("[CP1-T10] status shows the blocked reason (needs a decision, after 3 rounds, at the limit)",
              "needs your decision" in status_out and "3 rounds" in status_out and "limit" in status_out,
              status_out)


# ------------------------------------------------------------------------------------------------ CP1-T11..T13

def t11_a_stuck_unreviewed_round_with_a_wrong_baseline_heals_on_the_next_round():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = t._ext_start("c1t11", [fa], cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T11", age=10)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        out2 = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out2, 0)
        r2 = tf._record(d, rnd2, [out2], env=env)
        check("[CP1-T11] (fixture) round 2's review is refused as too fast",
              r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t11b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        before = tf._counters(d)
        _corrupt_prev_round_tree(d, env, rnd2["fingerprint"])
        check("[CP1-T11] (fixture) round 3 is open with no review written yet",
              t._cp(d)["adv_round"].get("recorded") in (None, False), t._cp(d)["adv_round"].get("recorded"))
        t._append_code(d, "src/bravo.js", 1, "c1t11c", env, first_marker="ZQ-FIX-C")
        rnd4 = tf._open(d, env=env)
        check("[CP1-T11] round 4's prev_round_tree == F1, not F2 (the wrong value left in state.json)",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"]
              and t._cp(d)["adv_round"].get("prev_round_tree") != rnd2["fingerprint"],
              (t._cp(d)["adv_round"].get("prev_round_tree"), rnd1["fingerprint"], rnd2["fingerprint"]))
        p = t._prompt(d, env, rnd4["reviewers"][0])
        check("[CP1-T11] round 4's prompt shows ZQ-FIX-A, ZQ-LATE and ZQ-FIX-C",
              t._added(p, "ZQ-FIX-A") and t._added(p, "ZQ-LATE") and t._added(p, "ZQ-FIX-C"), p[-1800:])
        check("[CP1-T11] the free replacement left the counters unchanged",
              tf._counters(d) == before, (before, tf._counters(d)))


def t12_a_stuck_round_with_a_refused_review_and_wrong_baseline_heals_on_next_round():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = t._ext_start("c1t12", [fa], cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T12", age=10)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        out2 = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out2, 0)
        r2 = tf._record(d, rnd2, [out2], env=env)
        check("[CP1-T12] (fixture) round 2's review is refused as too fast",
              r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        t._append_code(d, "src/charlie.js", 1, "c1t12b", env, first_marker="ZQ-LATE")
        rnd3 = tf._open(d, env=env)
        _corrupt_prev_round_tree(d, env, rnd2["fingerprint"])
        out3 = tf._ext_review(d, rnd3, "codex-standards", env)
        t._age(d, out3, 0)
        r3 = tf._record(d, rnd3, [out3], env=env)
        check("[CP1-T12] round 3's own review is also refused as too fast and stays in its folder",
              r3.returncode != 0 and "too fast" in tf._out(r3) and Path(out3).exists(), (tf._out(r3), out3))
        t._append_code(d, "src/bravo.js", 1, "c1t12c", env, first_marker="ZQ-FIX-C")
        rnd4 = tf._open(d, env=env)
        check("[CP1-T12] round 4's prev_round_tree == F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        p = t._prompt(d, env, rnd4["reviewers"][0])
        check("[CP1-T12] round 4's prompt shows ZQ-FIX-A, ZQ-LATE and ZQ-FIX-C",
              t._added(p, "ZQ-FIX-A") and t._added(p, "ZQ-LATE") and t._added(p, "ZQ-FIX-C"), p[-1800:])


def t13_asking_again_with_no_code_change_keeps_the_same_open_round():
    with tf._own_home():
        fa = t._finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = t._ext_start("c1t13", [fa], cfg=["min_review_seconds=5", "checkin_round=99"])
        rnd1 = _blocked_aged(d, env, "CP1-T13", age=10)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        t._edit(d, "src/alpha.js",
                t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        out2 = tf._ext_review(d, rnd2, "codex-standards", env)
        t._age(d, out2, 0)
        r2 = tf._record(d, rnd2, [out2], env=env)
        check("[CP1-T13] round 2's review is refused as too fast", r2.returncode != 0 and "too fast" in tf._out(r2), tf._out(r2))
        before = tf._counters(d)
        rnd2b = tf._open(d, env=env)
        check("[CP1-T13] the open round is still round 2 with the same fingerprint",
              rnd2b.get("round") == 2 and rnd2b.get("fingerprint") == rnd2["fingerprint"],
              (rnd2b.get("round"), rnd2b.get("fingerprint"), rnd2["fingerprint"]))
        check("[CP1-T13] round 2's prev_round_tree is still F1",
              t._cp(d)["adv_round"].get("prev_round_tree") == rnd1["fingerprint"], "")
        check("[CP1-T13] counters do not move", tf._counters(d) == before, (before, tf._counters(d)))


# ------------------------------------------------------------------------------------------------ CP1-T14

def t14_adv_record_still_refuses_what_it_refused_before():
    with tf._own_home():
        d, env, rnd2 = xf._big_fix_round("c1t14a")
        r, paths = t._ext_record_aged(d, rnd2, env, 60, expect=1)
        check("[CP1-T14] (omitted_files) adv-record refuses with its own 'never counts' message",
              r.returncode != 0 and "never counts" in tf._out(r), tf._out(r))
        check("[CP1-T14] (omitted_files) the round is not recorded; gates.adversarial.rounds gains no entry",
              not t._cp(d)["adv_round"].get("recorded")
              and len(t._cp(d)["gates"]["adversarial"].get("rounds") or []) == 1,
              (t._cp(d)["adv_round"].get("recorded"), t._cp(d)["gates"]["adversarial"].get("rounds")))
    with tf._own_home():
        fb = t._finding("ZB-1", "src/alpha.js", 2, "DESC-B bad", "FIX-B good")
        d, env, b = t._ext_start("c1t14b", [fb], cfg=["min_review_seconds=5", "checkin_round=99"])
        _blocked_aged(d, env, "CP1-T14", age=10)
        tf._triage(d, ["codex-standards/ZB-1"], env=env)
        t._edit(d, "src/alpha.js", t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22;"), env)
        rnd2b = tf._open(d, env=env)
        out = tf._ext_review(d, rnd2b, "codex-standards", env)
        t._age(d, out, 0)
        r = tf._record(d, rnd2b, [out], env=env)
        check("[CP1-T14] (too fast) adv-record refuses with its own 'too fast' message",
              r.returncode != 0 and "too fast to be a real review" in tf._out(r), tf._out(r))
        check("[CP1-T14] (too fast) the round is not recorded",
              not t._cp(d)["adv_round"].get("recorded")
              and len(t._cp(d)["gates"]["adversarial"].get("rounds") or []) == 1, "")
    with tf._own_home():
        # A host-blocked round this time: a host review is written straight into the round's own folder, so an
        # approval with an empty files_reviewed list is produced the real way (external_review.py always
        # re-stamps files_reviewed from its own scope, so a script-signed review can never carry an empty one).
        d, env, b = t._ext_start("c1t14c", cfg=["checkin_round=99"])
        rnd1c = tf._open(d, env=env)
        paths = [tf._ext_review(d, rnd1c, rv["id"], env) if rv.get("kind") == "command"
                 else g.write_review(d, rnd1c, rv["id"], [t._finding("ZC-1", "src/alpha.js", 2, "DESC-C bad",
                                                                     "FIX-C good")])
                 for rv in rnd1c["reviewers"]]
        rc1 = tf._record(d, rnd1c, paths, env=env)
        check("[CP1-T14] (no files listed) (fixture) round 1 is recorded blocked by the host reviewer",
              rc1.returncode == 2, tf._out(rc1))
        tf._triage(d, ["host-correctness/ZC-1"], env=env)
        t._edit(d, "src/alpha.js", t._src("alpha", t.ALPHA).replace("alpha_two = 2;", "alpha_two = 22;"), env)
        rnd2c = tf._open(d, env=env)
        check("[CP1-T14] (no files listed) (fixture) round 2 is the one-reviewer fix round for host-correctness",
              tf._ids(rnd2c) == ["host-correctness"], tf._ids(rnd2c))
        out = g.write_review(d, rnd2c, "host-correctness", [], files_reviewed=[])
        r = tf._record(d, rnd2c, [out], env=env)
        check("[CP1-T14] (no files listed) adv-record refuses with its own 'files_reviewed' message",
              r.returncode != 0 and "files_reviewed" in tf._out(r), tf._out(r))
        check("[CP1-T14] (no files listed) the round is not recorded",
              not t._cp(d)["adv_round"].get("recorded")
              and len(t._cp(d)["gates"]["adversarial"].get("rounds") or []) == 1, "")
    d = tf._start("c1t14d", config=["checkin_round=99"])
    rnd1 = tf._open(d)
    check("[CP1-T14] (fixture) round 1 names 2 reviewers", len(rnd1["reviewers"]) == 2, tf._ids(rnd1))
    only_one = [g.write_review(d, rnd1, rnd1["reviewers"][0]["id"], [])]
    r = tf._record(d, rnd1, only_one)
    check("[CP1-T14] (missing reviewer) adv-record refuses naming the missing one",
          r.returncode != 0 and "missing" in tf._out(r), tf._out(r))
    check("[CP1-T14] (missing reviewer) the round is not recorded; gates.adversarial.rounds stays empty",
          not t._cp(d)["adv_round"].get("recorded")
          and not (t._cp(d)["gates"]["adversarial"].get("rounds") or []), "")


# ------------------------------------------------------------------------------------------------ CP1-T15

def _git_show(tree, rel):
    r = subprocess.run(["git", "show", f"{tree}:{rel}"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _func_ast_dump_no_docstring(name, src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]
            return "\n".join(ast.dump(n, annotate_fields=False) for n in body)
    return None


def _refund_block(src):
    m = re.search(
        r"(free_replace = False\n(?:.*\n)*?\s*st\[\"adv_rounds_total\"\] = max\(0, int\(st\.get\(\"adv_rounds_total\", "
        r"0\)\) - 1\)\n)", src)
    return m.group(1) if m else None


# The lane's own starting commit (this branch's base, confirmed an ancestor of every commit built on it): a
# permanent git ref, unlike the run's own gitignored state.json, so it resolves on main, in a fresh clone, and
# once this run is archived, not only in the one checkout that built this fix.
_BASE_COMMIT = "3899764"

# This lane's own landing commit, found by its full, unique message prefix (never a shorter pattern: history
# already has other commits matching a shorter "ratchet(...): CP1 " prefix). While the lane is still being
# built, no commit has this prefix yet, so `_resolve_landing_commit` returns None and the "new side" of every
# comparison below reads the live working tree, exactly as before this fix. Once CP1 lands, the "new side"
# is pinned to that commit instead (the same precedent as tests/test_split_check.py's CP2_COMMIT), so a later,
# legitimate, unrelated edit to these same functions never fails this historical proof again.
_RUN_COMMIT_PREFIX = "ratchet(fix-a-review-round-adv-record-refused-never-reco): CP1 "


def _resolve_base_tree():
    """CP1-T15's base tree. Returns `_BASE_COMMIT` when it is a real ancestor of HEAD and its own blobs are
    readable, else None (a shallow clone, for example) -- a genuine edge case now, not the common path."""
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", _BASE_COMMIT, "HEAD"], cwd=ROOT,
                         capture_output=True)
    if anc.returncode != 0:
        return None
    return _BASE_COMMIT if _git_show(_BASE_COMMIT, "skills/ratchet-orchestrator/scripts/ratchet.py") else None


def _resolve_landing_commit():
    """The commit whose message starts with `_RUN_COMMIT_PREFIX`, or None when no such commit exists yet (the
    normal case while this lane is still being built)."""
    r = subprocess.run(["git", "log", "--format=%H %s"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        sha, _, subject = line.partition(" ")
        if subject.startswith(_RUN_COMMIT_PREFIX):
            return sha
    return None


def _test_file_already_committed():
    """True once this test file has at least one commit touching it (it is untracked, so this is False, while
    this lane is still being built)."""
    r = subprocess.run(["git", "log", "-1", "--format=%H", "--", str(Path(__file__).resolve())], cwd=ROOT,
                       capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def _is_old_key_assign(n):
    return (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "old_key")


def _is_rnd_dict_assign(n):
    return (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "rnd" and isinstance(n.value, ast.Dict)
            and any(isinstance(k, ast.Constant) and k.value == "prev_round_tree" for k in n.value.keys))


def _mask_span_between(fn_name, src, start_pred, end_pred):
    """The whole function (docstring removed), with every statement strictly between the one `start_pred`
    matches and the one `end_pred` matches -- wherever in the function's nested blocks that pair of statements
    sits, so long as both are direct children of the same `body`/`orelse`/`finalbody` list -- collapsed into a
    single placeholder, as one `ast.dump` string. `start_pred`/`end_pred` anchor on statements that are never
    part of the baseline computation itself (`old_key`'s own assignment, and the new round's own dict literal),
    so this masks exactly the span that sets `prev_round_tree`, however that span is shaped, and leaves every
    other line of the function, at any nesting depth, to compare exactly. None when the function, either anchor,
    or a single statement list holding both of them in order, can't be found."""
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == fn_name:
            owner = None
            for node in ast.walk(fn):
                for field in ("body", "orelse", "finalbody"):
                    lst = getattr(node, field, None)
                    if isinstance(lst, list) and lst and isinstance(lst[0], ast.stmt):
                        start_idx = next((i for i, n in enumerate(lst) if start_pred(n)), None)
                        end_idx = next((i for i, n in enumerate(lst) if end_pred(n)), None)
                        if start_idx is not None and end_idx is not None and end_idx > start_idx:
                            setattr(node, field, lst[:start_idx + 1] + [ast.Pass()] + lst[end_idx:])
                            owner = node
                            break
                if owner is not None:
                    break
            if owner is None:
                return None
            body = fn.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]
            return "\n".join(ast.dump(n, annotate_fields=False) for n in body)
    return None


def t15_only_the_baseline_setting_code_changed():
    base_tree = _resolve_base_tree()
    if base_tree is None:
        print(f"  SKIPPED [CP1-T15] base commit {_BASE_COMMIT} is unreadable in this checkout (for example a "
              "shallow clone); the scope check did not run")
        return

    landing_commit = _resolve_landing_commit()
    if landing_commit is None and _test_file_already_committed():
        print(f"  SKIPPED [CP1-T15] this lane's own landing commit (prefix {_RUN_COMMIT_PREFIX!r}) could not be "
              "found even though this test file is already committed (for example a squash or reword obscured "
              "the message); comparing against the live tree instead would reintroduce the exact drift bug "
              "this case guards against, so the scope check did not run")
        return

    def _new_side(rel):
        return _git_show(landing_commit, rel) if landing_commit is not None else (ROOT / rel).read_text()

    ext_rel = "skills/ratchet-adversarial-loop/scripts/external_review.py"
    base_ext = _git_show(base_tree, ext_rel)
    cur_ext = _new_side(ext_rel)
    check("[CP1-T15] external_review.py is byte-identical to the base tree", base_ext == cur_ext, "")

    rat_rel = "skills/ratchet-orchestrator/scripts/ratchet.py"
    base_rat = _git_show(base_tree, rat_rel)
    cur_rat = _new_side(rat_rel)

    for fn_name in ("_scope_for_round", "cmd_adv_record"):
        base_dump = _func_ast_dump_no_docstring(fn_name, base_rat)
        cur_dump = _func_ast_dump_no_docstring(fn_name, cur_rat)
        check(f"[CP1-T15] {fn_name}'s body is unchanged (AST, docstring removed)",
              base_dump is not None and base_dump == cur_dump, fn_name)

    base_refund = _refund_block(base_rat)
    cur_refund = _refund_block(cur_rat)
    check("[CP1-T15] cmd_reviewers' free_replace refund block is unchanged",
          base_refund is not None and base_refund == cur_refund, (bool(base_refund), bool(cur_refund)))

    base_masked = _mask_span_between("cmd_reviewers", base_rat, _is_old_key_assign, _is_rnd_dict_assign)
    cur_masked = _mask_span_between("cmd_reviewers", cur_rat, _is_old_key_assign, _is_rnd_dict_assign)
    check("[CP1-T15] cmd_reviewers is unchanged outside the span that sets prev_round_tree (AST, masked between "
          "the old_key assignment and the new round's own dict literal)",
          base_masked is not None and base_masked == cur_masked, "")


# ------------------------------------------------------------------------------------------------ CP1-T16

def t16_the_docs_say_where_the_baseline_comes_from_and_the_attempt_cost():
    schemas = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/ratchet-adversarial-loop/SKILL.md").read_text(encoding="utf-8")
    BASELINE = [r"prev_round_tree|since_tree",
                r"last round[^.\n]{0,80}(actually\s+)?record|adv-record[^.\n]{0,80}record",
                r"refus(ed|al|es)[^.\n]{0,160}(never|does not|doesn't|not)[^.\n]{0,60}(move|advance|advanc|change)"]
    check("[CP1-T16] schemas.md says the baseline is the last round adv-record recorded, and a refused round "
          "never moves it", t._some(schemas, *BASELINE), "")
    ATTEMPT = [r"refus(ed|al|es)", r"written review|review (was |is )?written",
               r"count[^.\n]{0,40}(one )?attempt|still counts|costs? (it |the step )?(one|an) (attempt|round)"]
    check("[CP1-T16] schemas.md or SKILL.md says a refused round with a written review still counts one attempt",
          t._some(schemas, *ATTEMPT) or t._some(skill, *ATTEMPT), "")
    old_phrase = "carried forward through a free replacement"
    occurrences = [para for para in t._paragraphs(schemas) if old_phrase in para]
    check("[CP1-T16] the old 'carried forward through a free replacement' sentence no longer stands alone as "
          "the whole rule for a refused round",
          not occurrences or all("refus" in para.lower() for para in occurrences), occurrences)
    before_fail = FAIL
    t.t22_the_docs_describe_what_a_fix_reviewer_sees_and_the_shorter_minimum()
    check("[CP1-T16] CP6-T22's doc phrases in tests/test_fix_round_diff.py still match",
          FAIL == before_fail, "CP6-T22 regressed after the schemas.md edit")


CASES = [
    ("CP1-T01", t01_a_recorded_blocked_round_still_measures_the_next_fix_round_from_itself),
    ("CP1-T02", t02_a_recorded_approval_still_measures_the_next_small_fix_from_itself),
    ("CP1-T03", t03_an_omitted_files_refusal_shows_the_fix_again_next_round),
    ("CP1-T04", t04a_too_fast_also_shows_the_fix_again),
    ("CP1-T04", t04b_an_approval_with_no_files_listed_also_shows_the_fix_again),
    ("CP1-T05", t05_two_refused_rounds_in_a_row_still_measure_from_the_last_recorded_round),
    ("CP1-T06", t06_a_round_recorded_after_a_refused_one_becomes_the_new_baseline),
    ("CP1-T07", t07_a_refused_small_fix_after_an_approval_shows_that_fix_again),
    ("CP1-T08", t08_a_refused_first_round_leaves_no_baseline_at_all),
    ("CP1-T09", t09_a_refused_reviewed_round_costs_one_but_an_unreviewed_round_stays_free),
    ("CP1-T10", t10_running_out_of_rounds_after_refusals_stops_with_the_decision_message),
    ("CP1-T11", t11_a_stuck_unreviewed_round_with_a_wrong_baseline_heals_on_the_next_round),
    ("CP1-T12", t12_a_stuck_round_with_a_refused_review_and_wrong_baseline_heals_on_next_round),
    ("CP1-T13", t13_asking_again_with_no_code_change_keeps_the_same_open_round),
    ("CP1-T14", t14_adv_record_still_refuses_what_it_refused_before),
    ("CP1-T15", t15_only_the_baseline_setting_code_changed),
    ("CP1-T16", t16_the_docs_say_where_the_baseline_comes_from_and_the_attempt_cost),
]


def main():
    for cid, fn in CASES:
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception:
            check(f"[{cid}] ran to the end without an unexpected error", False, traceback.format_exc())
    for cid in PLANNED:
        if cid not in CASE_STATUS:
            check(f"[{cid}] recorded at least one check", False, "no check ran for this case")
    print(f"\n{PASS} passed, {FAIL} failed")
    rep = os.environ.get("RATCHET_TEST_REPORT")
    if rep:
        cases = [{"id": c, "status": "passed" if ok else "failed",
                  "message": "; ".join(CASE_MESSAGES.get(c, []))[:2000]}
                 for c, ok in sorted(CASE_STATUS.items())]
        Path(rep).write_text(json.dumps({"cases": cases}, indent=1))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
