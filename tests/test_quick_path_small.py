#!/usr/bin/env python3
"""
CP3 tests: small fixes get a one-reviewer check.   python3 tests/test_quick_path_small.py

After a checkpoint's adversarial gate has a clean, approved round 1, a small code fix (about 20 changed lines
or fewer, `git diff --numstat` added+removed, the same measurement `ratchet.py changes CPn --json` already
reports as `since_approval.code_lines_changed`) made in a step that is not high risk opens its next review
round with exactly one reviewer, told to look only at the file(s) changed since round 1 -- instead of the
step's normal full reviewer set. A fix over that limit, or any fix in a high-risk step, still opens the normal
full round. The step's own tests and the full regression suite still run before the step completes, and the
small-fix round still counts toward the step's round limit like any other round. A doc-only edit after
approval is unaffected: it keeps CP2's zero-reviewer skip, never a downgraded one-reviewer round.

Reuses tests/test_gates.py (`project`, `approve_plan`, `to_adversarial`, `open_round`, `write_review`, `rx`,
`git`, `say`, `ENGINE`, `ENV`) for the throwaway git project and the real-CLI-subprocess way every suite in
this repo drives the engine. Drives the real `ratchet.py` CLI against a throwaway one-checkpoint git project;
no command or app server is started, this is pure CLI/state behavior. Only tiny fake test/regression commands
this file fully controls are ever run (never tests/run_all.py or any real suite).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, open_round, write_review, rx, git, say)

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


def _out(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


# ------------------------------------------------------------------------------------------------ fixtures

# A tiny fake regression command that always fails (CP3-T07 proves it was actually invoked, not silently
# skipped as a docs-only completion), never the real suite.
_FAIL_SRC = "import sys\nsys.exit(1)\n"
_FAIL_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp3-failcmd-")) / "always_fail.py"
_FAIL_SCRIPT.write_text(_FAIL_SRC)


def _always_fail_command() -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(_FAIL_SCRIPT))}"


# A tiny fake doc_test_command that always passes (CP3-T10's doc-only-after-approval route), never the real
# suite.
_DOC_OK_SRC = "import sys\nsys.exit(0)\n"
_DOC_OK_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp3-doccmd-")) / "doc_ok.py"
_DOC_OK_SCRIPT.write_text(_DOC_OK_SRC)


def _doc_ok_command() -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(_DOC_OK_SCRIPT))} {{files}}"


def _project(name, risk="normal", extra_cp=None, config=None) -> Path:
    """A one-checkpoint project (risk configurable per case) with a README present before `approve_plan()`
    (so it's part of CP1's recorded base_tree), for the doc-mixed (CP3-T06) and doc-only (CP3-T10) cases.
    `extra_cp={"risk": "high"}` gets its `risk_reason` filled in automatically by `g.project()`."""
    extra = {"risk": risk}
    if extra_cp:
        extra.update(extra_cp)
    d = g.project(name, cps=1, extra_cp=extra, config=config or [])
    (d / "README.md").write_text("# Title\n\nThe original sentence.\n")
    g.approve_plan(d)
    return d


def _append_lines(path: Path, n: int, tag: str):
    """Appends exactly `n` new line-comments to `path` (0 removed), so `git diff --numstat`-style measurement
    since the approved tree is exactly `n` added lines -- used to hit 5, 20, 21, 25-ish and 3 lines precisely."""
    with open(path, "a") as f:
        for i in range(n):
            f.write(f"// filler-{tag}-{i}\n")


def _approve_round1(d, cp="CP1"):
    """Drives `cp` through tests-written/red/implemented/behavior and one clean adversarial round (no
    findings, every reviewer the round names), so the gate is genuinely 'approved', not just 'recorded'.
    Returns the round-1 JSON (`reviewers` opened it with) for later comparison."""
    g.to_adversarial(d, cp)
    rnd = g.open_round(d, cp)
    paths = [g.write_review(d, rnd, rv["id"], findings=[]) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", cp, "--reviews", *paths)
    assert r.returncode == 0, _out(r)
    return rnd


def _open_and_approve_round(d, cp, rnd):
    """Writes a clean review (no findings) from every reviewer a round names, then records it. Works for any
    reviewer count (1 for a small-fix round, 2 or 4 for a full round)."""
    paths = [g.write_review(d, rnd, rv["id"], findings=[]) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", cp, "--reviews", *paths)
    assert r.returncode == 0, _out(r)
    return r


def _since_approval_lines(d, cp="CP1"):
    """`ratchet.py changes CPn --json`'s own `since_approval.code_lines_changed` -- the exact same measurement
    the engine uses, read back read-only, as a fixture sanity check before testing `reviewers`."""
    r = g.rx(d, "changes", cp, "--json")
    data = json.loads(r.stdout) if r.stdout.strip() else {}
    return (data.get("since_approval") or {}), data


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _gate(d, cp="CP1", gate="adversarial", slug="f"):
    return _state(d, slug)["checkpoints"][cp]["gates"][gate]


def _commit_message(d):
    return g.git(d, "log", "-1", "--format=%B").stdout


def _head(d):
    return g.git(d, "rev-parse", "HEAD").stdout.strip()


_SMALL_FIX_RE = re.compile(r"small[\s-]*fix", re.I)
_ONE_REVIEWER_RE = re.compile(r"\b(one|1|single)\b[^.\n]{0,24}review", re.I)


def _names_small_fix_review(text, reviewer_id):
    """True when `text` names `reviewer_id`, calls the review a 'small fix' (in some plain-English way) and
    says it was done by one reviewer -- the visibility CP3-T08 requires, matched loosely since the exact
    wording is the implementation's call, not this file's."""
    if reviewer_id not in text:
        return False
    return bool(_SMALL_FIX_RE.search(text)) and bool(_ONE_REVIEWER_RE.search(text))


# ------------------------------------------------------------------------------------------------ cases

def t01_small_fix_opens_a_one_reviewer_round_scoped_to_the_fix():
    d = _project("cp3-t01", risk="normal")
    rnd1 = _approve_round1(d)
    check("[CP3-T01] (fixture sanity) round 1 is the normal-risk full set (two reviewers)",
          len(rnd1["reviewers"]) == 2, rnd1["reviewers"])
    _append_lines(d / "src" / "f.js", 5, "t01")
    sa, data = _since_approval_lines(d)
    check("[CP3-T01] (fixture sanity) exactly 5 code lines changed since approval",
          sa.get("code_lines_changed") == 5, data)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T01] round 2 opens naming exactly one reviewer, not the normal-risk step's usual two",
          len(rnd2["reviewers"]) == 1, rnd2["reviewers"])
    check("[CP3-T01] round 2 is round number 2", rnd2.get("round") == 2, rnd2.get("round"))
    check("[CP3-T01] round 2's own focus names only the file(s) changed since round 1, not the whole checkpoint",
          set(rnd2.get("focus_files") or []) == {"src/f.js"}, rnd2.get("focus_files"))


def t02_fix_over_the_limit_opens_the_normal_full_round():
    d = _project("cp3-t02", risk="normal")
    rnd1 = _approve_round1(d)
    ids1 = {r["id"] for r in rnd1["reviewers"]}
    _append_lines(d / "src" / "f.js", 25, "t02")
    sa, data = _since_approval_lines(d)
    check("[CP3-T02] (fixture sanity) exactly 25 code lines changed since approval",
          sa.get("code_lines_changed") == 25, data)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T02] round 2 opens naming the full normal-risk reviewer set (two reviewers)",
          len(rnd2["reviewers"]) == 2, rnd2["reviewers"])
    check("[CP3-T02] round 2 names the same two reviewer ids as round 1's full set, not a single small-fix one",
          {r["id"] for r in rnd2["reviewers"]} == ids1, ({r["id"] for r in rnd2["reviewers"]}, ids1))


def t03_any_fix_in_a_high_risk_step_gets_the_full_round():
    d = _project("cp3-t03", risk="high")
    rnd1 = _approve_round1(d)
    ids1 = {r["id"] for r in rnd1["reviewers"]}
    check("[CP3-T03] (fixture sanity) round 1 is the high-risk full set (four reviewers)",
          len(rnd1["reviewers"]) == 4, rnd1["reviewers"])
    _append_lines(d / "src" / "f.js", 3, "t03")
    sa, data = _since_approval_lines(d)
    check("[CP3-T03] (fixture sanity) exactly 3 code lines changed since approval",
          sa.get("code_lines_changed") == 3, data)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T03] round 2 opens naming every reviewer the high-risk depth gives (four reviewers)",
          len(rnd2["reviewers"]) == 4, rnd2["reviewers"])
    check("[CP3-T03] round 2 never collapses to the single small-fix reviewer, however small the fix",
          {r["id"] for r in rnd2["reviewers"]} == ids1, ({r["id"] for r in rnd2["reviewers"]}, ids1))


def t04_a_fix_of_exactly_the_limit_still_counts_as_small():
    d = _project("cp3-t04", risk="normal")
    _approve_round1(d)
    _append_lines(d / "src" / "f.js", 20, "t04")
    sa, data = _since_approval_lines(d)
    check("[CP3-T04] (fixture sanity) exactly 20 code lines changed since approval",
          sa.get("code_lines_changed") == 20, data)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T04] round 2 opens naming exactly one reviewer at exactly the small-fix limit",
          len(rnd2["reviewers"]) == 1, rnd2["reviewers"])


def t05_one_line_over_the_limit_is_a_full_fix():
    d = _project("cp3-t05", risk="normal")
    _approve_round1(d)
    _append_lines(d / "src" / "f.js", 21, "t05")
    sa, data = _since_approval_lines(d)
    check("[CP3-T05] (fixture sanity) exactly 21 code lines changed since approval",
          sa.get("code_lines_changed") == 21, data)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T05] round 2 opens naming the full normal-risk reviewer set, not one reviewer",
          len(rnd2["reviewers"]) == 2, rnd2["reviewers"])


def t06_small_code_fix_mixed_with_a_doc_edit_still_counts_only_its_code_lines():
    d = _project("cp3-t06", risk="normal")
    _approve_round1(d)
    _append_lines(d / "src" / "f.js", 5, "t06code")
    _append_lines(d / "README.md", 50, "t06doc")
    sa, data = _since_approval_lines(d)
    check("[CP3-T06] (fixture sanity) exactly 5 code lines changed since approval (the doc edit doesn't count)",
          sa.get("code_lines_changed") == 5, data)
    check("[CP3-T06] (fixture sanity) README.md is reported as a doc file changed since approval",
          "README.md" in (sa.get("doc_files") or []), data)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T06] round 2 still opens naming exactly one reviewer: the doc file's lines don't count toward "
          "the small-fix limit", len(rnd2["reviewers"]) == 1, rnd2["reviewers"])


def t07_small_fix_rounds_tests_and_full_suite_still_run_before_complete():
    d = _project("cp3-t07", risk="normal", config=[f"regression_command={_always_fail_command()}"])
    _approve_round1(d)
    _append_lines(d / "src" / "f.js", 5, "t07")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T07] (fixture sanity) round 2 is a one-reviewer small-fix round",
          len(rnd2["reviewers"]) == 1, rnd2["reviewers"])
    _open_and_approve_round(d, "CP1", rnd2)
    r = g.rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T07] `complete CP1` fails on the always-failing regression command",
          r.returncode != 0, out)
    check("[CP3-T07] the failure names the regression suite, proving it actually ran (not a silently-skipped "
          "docs-only completion)", bool(re.search(r"regression", out, re.I)), out)


def t08_status_and_commit_say_a_fix_was_reviewed_as_a_small_fix_by_one_reviewer():
    d = _project("cp3-t08", risk="normal")
    _approve_round1(d)
    _append_lines(d / "src" / "f.js", 5, "t08")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T08] (fixture sanity) round 2 is a one-reviewer small-fix round",
          len(rnd2["reviewers"]) == 1, rnd2["reviewers"])
    rid2 = rnd2["reviewers"][0]["id"]
    _open_and_approve_round(d, "CP1", rnd2)
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    msg = _commit_message(d)
    check("[CP3-T08] the commit message says the fix was reviewed as a small fix by one reviewer, naming it",
          _names_small_fix_review(msg, rid2), msg)
    status = g.rx(d, "status", "--json")
    sout = json.dumps(json.loads(status.stdout)) if status.stdout.strip() else _out(status)
    check("[CP3-T08] `ratchet.py status --json` says the same: a small-fix review by one reviewer, naming it",
          _names_small_fix_review(sout, rid2), sout)


def t09_a_small_fix_round_counts_toward_the_round_limit_like_any_other_round():
    d = _project("cp3-t09", risk="normal")
    _approve_round1(d)
    gate1 = dict(_gate(d))
    st1 = _state(d)
    attempts1, run_rounds1 = gate1["attempts"], int(st1.get("adv_rounds_total", 0))
    _append_lines(d / "src" / "f.js", 5, "t09")
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T09] (fixture sanity) round 2 is a one-reviewer small-fix round",
          len(rnd2["reviewers"]) == 1, rnd2["reviewers"])
    _open_and_approve_round(d, "CP1", rnd2)
    gate2 = _gate(d)
    st2 = _state(d)
    check("[CP3-T09] the gate's own attempts count increased by exactly one, same as an ordinary round",
          gate2["attempts"] == attempts1 + 1, (gate2["attempts"], attempts1))
    check("[CP3-T09] the run-wide adversarial round count increased by exactly one",
          int(st2.get("adv_rounds_total", 0)) == run_rounds1 + 1,
          (st2.get("adv_rounds_total"), run_rounds1))


def t10_doc_only_edit_after_approval_is_still_a_zero_reviewer_skip():
    d = _project("cp3-t10", risk="normal", config=[f"doc_test_command={_doc_ok_command()}"])
    _approve_round1(d)
    gate_before = dict(_gate(d))
    head_before = _head(d)
    (d / "README.md").write_text("# Title\n\nThe edited sentence, doc only, after approval.\n")
    sa, data = _since_approval_lines(d)
    check("[CP3-T10] (fixture sanity) zero code lines changed since approval (doc only)",
          sa.get("code_lines_changed") == 0, data)
    r = g.rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T10] `complete CP1` succeeds via the doc-only path (CP2), no review round needed for a doc-only "
          "edit", r.returncode == 0, out)
    check("[CP3-T10] no new adversarial round was opened: attempts are unchanged from round 1",
          _gate(d)["attempts"] == gate_before["attempts"], (_gate(d), gate_before))
    round2_dir = Path(d) / ".ratchet/runs/f/reviews/CP1/adv-round-02"
    check("[CP3-T10] no small-fix round-2 folder was ever created for this zero-code-line edit",
          not round2_dir.exists(), str(round2_dir))
    check("[CP3-T10] a new commit was created (the doc-only edit still completed)",
          _head(d) != head_before, (_head(d), head_before))


TESTS = [
    ("CP3-T01", t01_small_fix_opens_a_one_reviewer_round_scoped_to_the_fix),
    ("CP3-T02", t02_fix_over_the_limit_opens_the_normal_full_round),
    ("CP3-T03", t03_any_fix_in_a_high_risk_step_gets_the_full_round),
    ("CP3-T04", t04_a_fix_of_exactly_the_limit_still_counts_as_small),
    ("CP3-T05", t05_one_line_over_the_limit_is_a_full_fix),
    ("CP3-T06", t06_small_code_fix_mixed_with_a_doc_edit_still_counts_only_its_code_lines),
    ("CP3-T07", t07_small_fix_rounds_tests_and_full_suite_still_run_before_complete),
    ("CP3-T08", t08_status_and_commit_say_a_fix_was_reviewed_as_a_small_fix_by_one_reviewer),
    ("CP3-T09", t09_a_small_fix_round_counts_toward_the_round_limit_like_any_other_round),
    ("CP3-T10", t10_doc_only_edit_after_approval_is_still_a_zero_reviewer_skip),
]

# Later fix-round cases are appended here (after the first red run this file is append-only); the report
# writer below already folds this list in, so a later append only needs to add to FIX_TESTS and call its
# case function before `main()` runs, the same inline pattern test_quick_path.py/test_quick_path_docs.py use.
FIX_TESTS: list[tuple[str, object]] = []


# ------------------------------------------------------------------------------------ CP3 adversarial round 1 fix-round cases
# A small fix's one reviewer must still cover both lenses (CP3-T12), and several small fixes in a row must add
# up against the checkpoint's last FULL review, not just the previous approval (CP3-T11).

def t11_two_small_fixes_summing_over_the_limit_get_the_full_round():
    d = _project("cp3-t11", risk="normal")
    rnd1 = _approve_round1(d)
    ids1 = {r["id"] for r in rnd1["reviewers"]}
    _append_lines(d / "src" / "f.js", 15, "t11a")
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T11] (fixture sanity) the first 15-line fix opens a one-reviewer small-fix round",
          len(rnd2["reviewers"]) == 1, rnd2["reviewers"])
    _open_and_approve_round(d, "CP1", rnd2)
    _append_lines(d / "src" / "f.js", 15, "t11b")
    sa, data = _since_approval_lines(d)
    check("[CP3-T11] (fixture sanity) only 15 code lines changed since round 2's own approval",
          sa.get("code_lines_changed") == 15, data)
    rnd3 = g.open_round(d, "CP1")
    check("[CP3-T11] round 3 opens naming the full normal-risk reviewer set: the two small fixes together "
          "(30 lines) exceed small_edit_lines measured since round 1's last FULL approval",
          len(rnd3["reviewers"]) == 2, rnd3["reviewers"])
    check("[CP3-T11] round 3 names the same two reviewer ids as round 1's full set, not a single small-fix one",
          {r["id"] for r in rnd3["reviewers"]} == ids1, ({r["id"] for r in rnd3["reviewers"]}, ids1))


def t12_small_fix_reviewer_covers_both_lenses_on_a_normal_risk_step():
    d = _project("cp3-t12", risk="normal")
    rnd1 = _approve_round1(d)
    check("[CP3-T12] (fixture sanity) round 1's reviewers are single-lens (standards, correctness)",
          {r.get("lens") for r in rnd1["reviewers"]} == {"standards", "correctness"}, rnd1["reviewers"])
    _append_lines(d / "src" / "f.js", 5, "t12")
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T12] round 2 opens naming exactly one reviewer", len(rnd2["reviewers"]) == 1, rnd2["reviewers"])
    check("[CP3-T12] the small-fix round's one reviewer covers both lenses, not whichever review_depth slot "
          "happened to come first", rnd2["reviewers"][0].get("lens") == "both", rnd2["reviewers"])


FIX_TESTS.extend([
    ("CP3-T11", t11_two_small_fixes_summing_over_the_limit_get_the_full_round),
    ("CP3-T12", t12_small_fix_reviewer_covers_both_lenses_on_a_normal_risk_step),
])


# ------------------------------------------------------------------------------------ CP3 adversarial round 2 fix-round cases
# A checkpoint whose own `reviewers` override (or a customized review_depth["low"]) can't collapse to exactly
# one both-lens reviewer must never be mislabeled a small-fix round: it keeps the normal full round, and
# `small_fix` on that round, plus the status/commit small-fix line, both stay off (CP3-T13).

def t13_small_fix_label_only_when_exactly_one_both_lens_reviewer_actually_runs():
    cases = [
        ("a", {"extra_cp": {"reviewers": ["host-standards"]}, "config": None},
         "a single single-lens `reviewers` override"),
        ("b", {"extra_cp": {"reviewers": ["host-standards", "host-correctness"]}, "config": None},
         "a two-reviewer `reviewers` override"),
        ("c", {"extra_cp": None, "config": ['review_depth.low=["host-standards", "host-correctness"]']},
         "a customized review_depth[\"low\"] with two slots"),
        ("control", {"extra_cp": None, "config": None}, "the default config"),
    ]
    for key, spec, label in cases:
        d = _project(f"cp3-t13-{key}", risk="normal", extra_cp=spec["extra_cp"], config=spec["config"])
        rnd1 = _approve_round1(d)
        _append_lines(d / "src" / "f.js", 5, f"t13{key}")
        b = g.rx(d, "behavior", "CP1")
        assert b.returncode == 0, _out(b)
        rnd2 = g.open_round(d, "CP1")
        if key == "control":
            check(f"[CP3-T13] ({label}) round 2's small_fix flag is True",
                  rnd2.get("small_fix") is True, rnd2)
            check(f"[CP3-T13] ({label}) round 2 names exactly one both-lens reviewer",
                  len(rnd2["reviewers"]) == 1 and rnd2["reviewers"][0].get("lens") == "both", rnd2["reviewers"])
        else:
            check(f"[CP3-T13] ({label}) round 2's small_fix flag is False",
                  rnd2.get("small_fix") is False, rnd2)
            check(f"[CP3-T13] ({label}) round 2 names the full normal reviewer set, not one reviewer",
                  {r["id"] for r in rnd2["reviewers"]} == {r["id"] for r in rnd1["reviewers"]},
                  (rnd2["reviewers"], rnd1["reviewers"]))
        _open_and_approve_round(d, "CP1", rnd2)
        r = g.rx(d, "complete", "CP1")
        assert r.returncode == 0, _out(r)
        msg = _commit_message(d)
        status = g.rx(d, "status", "--json")
        sout = json.dumps(json.loads(status.stdout)) if status.stdout.strip() else _out(status)
        if key == "control":
            rid2 = rnd2["reviewers"][0]["id"]
            check(f"[CP3-T13] ({label}) the commit message names the single small-fix reviewer",
                  _names_small_fix_review(msg, rid2), msg)
            check(f"[CP3-T13] ({label}) status --json says the same",
                  _names_small_fix_review(sout, rid2), sout)
        else:
            check(f"[CP3-T13] ({label}) the commit message never claims a small-fix one-reviewer review",
                  not _SMALL_FIX_RE.search(msg), msg)
            check(f"[CP3-T13] ({label}) status --json never claims a small-fix one-reviewer review",
                  not _SMALL_FIX_RE.search(sout), sout)


FIX_TESTS.append(("CP3-T13", t13_small_fix_label_only_when_exactly_one_both_lens_reviewer_actually_runs))


# ------------------------------------------------------------------------------------ CP3 adversarial round 3 fix-round cases
# A small fix must still be recognized through Ratchet's own real post-approval sequence: `complete` refuses and
# marks the adversarial gate stale (not "passed" anymore), `behavior` is re-run, and only then does `reviewers`
# open the next round (CP3-T14) -- not only via the shortcut of calling `reviewers` straight after the fix.

def t14_small_fix_recognized_through_the_real_complete_then_behavior_then_reviewers_sequence():
    d = _project("cp3-t14", risk="normal")
    _approve_round1(d)
    _append_lines(d / "src" / "f.js", 5, "t14")
    r = g.rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T14] `complete CP1` refuses: code changed since the adversarial approval",
          r.returncode != 0, out)
    check("[CP3-T14] `complete` marks the adversarial gate stale, not left at 'passed'",
          _gate(d)["status"] == "stale", _gate(d))
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-T14] after the real complete-then-behavior sequence, `reviewers` still opens a one-reviewer "
          "small-fix round, not a full round", rnd2.get("small_fix") is True, rnd2)
    check("[CP3-T14] round 2 names exactly one reviewer", len(rnd2["reviewers"]) == 1, rnd2["reviewers"])


FIX_TESTS.append(("CP3-T14", t14_small_fix_recognized_through_the_real_complete_then_behavior_then_reviewers_sequence))


# ------------------------------------------------------------------------------------ CP3 round-3 fix: user accept
# must not count as a FULL review approval. Only `cmd_adv_record`'s own full-round approval sets
# `last_full_review_fingerprint`; a user accept-finding only sets the gate's `fingerprint` (it never touches
# `last_full_review_fingerprint`). A fix made after an accept-finding pass must therefore always open a normal
# full round -- there is no recorded full-review baseline to measure a "small" fix against.

_ACCEPT_BLOCKER = [{"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "unsafe write",
                    "user_impact": "a user's saved data could be silently corrupted"}]


def t_x01_a_user_accept_never_counts_as_the_full_review_baseline_for_a_later_small_fix():
    d = _project("cp3-x01", risk="normal")
    g.to_adversarial(d, "CP1")
    rnd1 = g.open_round(d, "CP1")
    check("[CP3-X01] (fixture sanity) round 1 names the full two-reviewer normal-risk set",
          len(rnd1["reviewers"]) == 2, rnd1["reviewers"])
    paths = [g.write_review(d, rnd1, rnd1["reviewers"][0]["id"], _ACCEPT_BLOCKER),
             g.write_review(d, rnd1, rnd1["reviewers"][1]["id"], findings=[])]
    adv = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("[CP3-X01] (fixture sanity) round 1 blocks on the open finding", adv.returncode == 2, _out(adv))
    g.rx(d, "ask", "accept-finding", "CP1", "B1")
    g.say(d, "accept: known limit for this fixture")
    racc = g.rx(d, "accept-finding", "CP1")
    check("[CP3-X01] (fixture sanity) accept-finding passes the gate", racc.returncode == 0, _out(racc))
    cps = _state(d)["checkpoints"]["CP1"]
    check("[CP3-X01] accept-finding never sets last_full_review_fingerprint",
          cps.get("last_full_review_fingerprint") is None, cps)
    check("[CP3-X01] (fixture sanity) the gate itself is passed", _gate(d)["status"] == "passed", _gate(d))
    _append_lines(d / "src" / "f.js", 5, "x01")
    r = g.rx(d, "complete", "CP1")
    check("[CP3-X01] `complete CP1` refuses: code changed since the accept-finding approval",
          r.returncode != 0, _out(r))
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = g.open_round(d, "CP1")
    check("[CP3-X01] with no recorded FULL-review approval, the next round is a normal full round, never a "
          "one-reviewer small-fix round", rnd2.get("small_fix") is not True, rnd2)
    check("[CP3-X01] round 2 names the full two-reviewer set, not one reviewer",
          len(rnd2["reviewers"]) == 2, rnd2["reviewers"])


FIX_TESTS.append(("CP3-X01", t_x01_a_user_accept_never_counts_as_the_full_review_baseline_for_a_later_small_fix))


# -------------------------------------------------------------------------- CP3 round-04 fix: host-alt-standards/A1
# A small-fix round's one reviewer must always cover both lenses, even when it goes unavailable mid-round (a CLI
# timeout/quota/auth failure) and the agent re-runs `ratchet.py reviewers CPn` as instructed. Reproduces
# host-alt-standards/A1's repro (/tmp/repro_cp3_swap.py): a fake external `codex` CLI stands in for the "other
# provider" roster so there is a command-kind both-lens reviewer (codex-both) whose availability `reviewer-status
# --mark` can actually flip (an "agent"-kind host reviewer is always available, never affected by that mark).

_X02_EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"


def _x02_use_fake_codex_env():
    """Swaps the shared g.ENV for one whose PATH has only a fake `codex` CLI, the same `cp7_host_project`-style
    swap test_gates.py and test_flaky.py already use so every existing project()/approve_plan()/to_adversarial()/
    open_round() helper (none of which take an env= override) runs against it. Returns the saved ENV to restore."""
    import test_providers as tp
    saved = dict(g.ENV)
    g.ENV.clear()
    g.ENV.update(tp.env_with(tp.fake_bin("codex")))
    return saved


def _x02_run_external(d, rid, out):
    """Signs a command-kind reviewer's review the real way (external_review.py itself, backed by the fake
    codex CLI), the way test_providers.py/test_flaky.py drive command-kind reviewers -- a hand-written review
    (g.write_review's generic agent-sub-agent signature) does not count for a command-kind id like codex-both."""
    import subprocess
    r = subprocess.run([sys.executable, str(_X02_EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out)],
                       cwd=d, capture_output=True, text=True, env=g.ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    return str(out)


def _x02_write_review(d, rnd, rv):
    """Writes a clean review for reviewer row `rv` the way its own kind requires: a command-kind id (codex-*)
    through external_review.py (real signing), an agent-kind id (host-*) through g.write_review's sub-agent
    guard signature -- a mid-round swap can land on either kind depending on what is still available."""
    if rv.get("kind") == "command":
        return _x02_run_external(d, rv["id"], Path(rnd["output_dir"]) / f"{rv['id']}.json")
    return g.write_review(d, rnd, rv["id"], findings=[])


def t_x02_mid_round_unavailable_never_swaps_a_small_fix_rounds_both_lens_reviewer_for_a_single_lens_one():
    saved = _x02_use_fake_codex_env()
    try:
        d = g.project("cp3-x02", cps=1, extra_cp={"risk": "normal"})
        cfg = g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', "probe_ttl_minutes=0")
        assert cfg.returncode == 0, _out(cfg)
        g.approve_plan(d)
        g.to_adversarial(d, "CP1")
        rnd1 = g.open_round(d, "CP1")
        check("[CP3-X02] (fixture sanity) round 1 is the normal two-reviewer set",
              len(rnd1["reviewers"]) == 2, rnd1["reviewers"])
        paths = [_x02_write_review(d, rnd1, rv) for rv in rnd1["reviewers"]]
        r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
        assert r.returncode == 0, _out(r)
        _append_lines(d / "src" / "f.js", 5, "x02")
        b = g.rx(d, "behavior", "CP1")
        assert b.returncode == 0, _out(b)
        rnd2 = g.open_round(d, "CP1")
        check("[CP3-X02] (fixture sanity) the fix opens a one-reviewer, both-lens small-fix round",
              rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1
              and rnd2["reviewers"][0]["id"].endswith("-both"), rnd2)
        rid2 = rnd2["reviewers"][0]["id"]
        mark = g.rx(d, "reviewer-status", "--mark", rid2, "--reason", "quota exceeded")
        assert mark.returncode == 0, _out(mark)
        # the agent re-runs `reviewers` as external_review.py's own unavailable() path instructs
        rnd2b = g.open_round(d, "CP1")
        all_both = bool(rnd2b["reviewers"]) and all(rv["id"].endswith("-both") for rv in rnd2b["reviewers"])
        single_lens_small_fix = rnd2b.get("small_fix") is True and not all_both
        check("[CP3-X02] after the small-fix round's own reviewer goes unavailable mid-round and `reviewers` is "
              "re-run, the round never stays labeled small_fix with a reviewer that isn't both-lens",
              not single_lens_small_fix, rnd2b)
        if rnd2b.get("small_fix") is True:
            check("[CP3-X02] still a small-fix round: still exactly one both-lens reviewer",
                  len(rnd2b["reviewers"]) == 1 and all_both, rnd2b["reviewers"])
        else:
            check("[CP3-X02] demoted instead to a normal full round: names the step's full reviewer set",
                  len(rnd2b["reviewers"]) >= 2, rnd2b["reviewers"])
        paths2 = [_x02_write_review(d, rnd2b, rv) for rv in rnd2b["reviewers"]]
        r2 = g.rx(d, "adv-record", "CP1", "--reviews", *paths2)
        check("[CP3-X02] the re-resolved round still records cleanly", r2.returncode == 0, _out(r2))
        comp = g.rx(d, "complete", "CP1")
        check("[CP3-X02] the checkpoint still completes", comp.returncode == 0, _out(comp))
        msg = _commit_message(d)
        claims_single_lens_small_fix = bool(_SMALL_FIX_RE.search(msg)) and bool(_ONE_REVIEWER_RE.search(msg)) \
            and not all_both
        check("[CP3-X02] the commit message never claims a small fix by one reviewer unless that reviewer "
              "covers both lenses", not claims_single_lens_small_fix, msg)
    finally:
        g.ENV.clear()
        g.ENV.update(saved)


FIX_TESTS.append(("CP3-X02",
                   t_x02_mid_round_unavailable_never_swaps_a_small_fix_rounds_both_lens_reviewer_for_a_single_lens_one))


def write_report():
    import os
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in [*TESTS, *FIX_TESTS]]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in [*TESTS, *FIX_TESTS]:
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
