#!/usr/bin/env python3
"""
CP7 tests: three gaps found in director QA.   python3 tests/test_feedback_fixes.py

  1. A test that reached a planned test file from main stays frozen after the red run, also when the merge (or the
     fast-forward) was made before the plan was approved, while a test this lane's own fixer added stays free
     (CP7-T01 to CP7-T05; the fixtures of tests/test_lane_added_tests.py and tests/test_merge_main.py by import).
  2. With commit_per_checkpoint off, `merged` between steps refuses lane commits that no step reviewed, and still
     accepts a hand commit of exactly the reviewed work (CP7-T06, CP7-T07).
  3. A question printed as NOT ASKED (it replaced one still waiting for its first reply) is not reported as waiting
     by status, status --json or next, a real question still is, and no reply approves the NOT ASKED one
     (CP7-T08 to CP7-T10; the fixtures of tests/test_question_waiting.py by import).

Every case drives the repo's own ratchet.py through real commands in throwaway git projects; state.json is only
ever read. The checks match the engine's refusal lines after cutting absolute paths out of what a command printed
(the suite's temp folders are named after the suite), never a word over a path, and there are no wall-clock bounds.

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP7-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP7-[TX]\d+[a-z]?)\]")
FREEZE = "These changed after the user approved the plan"
UNREVIEWED = "The lane has commits since the last step finished that never went through the gates"
NOT_ASKED_OVER = "This question was printed while another one (shall I change how often they review"
PATH_RE = re.compile(r"file://\S*|(?<![\w.\-/])/[^\s'\"),;>]+")
MAIN_T = "def test_from_main():\n    assert 3 + 3 == 6\n"


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


def said(r) -> str:
    """Everything a command printed, with absolute paths cut out."""
    return PATH_RE.sub("<path>", (r.stdout or "") + "\n" + (r.stderr or ""))


# Fixture modules are imported inside the cases, so a harness problem in one of them is reported as a failed
# case, never as a crash of the whole file.
def _lat():
    import test_lane_added_tests as lat
    return lat


def _th():
    import trust_helpers as th
    return th


def _mm():
    import test_merge_main as mm
    return mm


def _qw():
    import test_question_waiting as qw
    return qw


# ------------------------------------------------------------------------------------------------ group 1 fixtures

def build_merged(mode):
    """The lane-added project of tests/test_lane_added_tests.py, with main's test_from_main in tests/test_cart.py
    brought in after `init` and before the plan is approved. mode 'merge': the lane makes its own commit, runs a
    real `git merge --no-ff main`, then makes one more commit (so HEAD is not the merge itself). mode 'ff': the lane
    has no commit of its own and fast-forwards to main."""
    lat, th = _lat(), _th()
    orig = th.rx
    notes = []

    def rx(d, env, *a, **k):
        if a[:1] == ("plan-submit",):
            br = th.git(d, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            if br == "main":
                th.git(d, "branch", "-m", "lane")
                br = "lane"
            th.git(d, "checkout", "-q", "-b", "main")
            (d / lat.TF).write_text(lat.LEGACY + "\n\n" + MAIN_T)
            lat.commit_all(d, "main adds a test")
            th.git(d, "checkout", "-q", br)
            if mode == "merge":
                (d / "src/b.js").write_text("export const b = 1;\n")
                lat.commit_all(d, "lane own commit")
            th.git(d, "merge", "--no-ff" if mode == "merge" else "--ff-only", "-q", "-m", "merge main", "main")
            if mode == "merge":
                (d / "src/c.js").write_text("export const c = 1;\n")
                lat.commit_all(d, "lane commit after the merge")
            notes.append("merged")
        return orig(d, env, *a, **k)

    th.rx = rx
    try:
        p = lat.build()
    finally:
        th.rx = orig
    assert notes == ["merged"], "fixture bug: the merge step did not run"
    assert "def test_from_main" in p.read(), "fixture bug: main's test is not in the lane's file"
    return p


def merged_built(mode):
    """build_merged taken to a passing first behavior run, with main's test kept in the test writer's file."""
    lat = _lat()
    p = build_merged(mode)
    p.write(lat.HEADER_SETUP + lat.LEGACY + "\n\n" + MAIN_T + lat.HELPERS + lat.PLANNED_T01 + lat.PLANNED_T02_SETUP)
    lat.must(p.rx("tests-written", "CP1"), "tests-written")
    lat.must(p.rx("red", "CP1"), "red")
    (p.d / "src/cart.py").write_text(lat.SRC_CART)
    lat.must(p.rx("implemented", "CP1"), "implemented")
    lat.must(p.behavior(), "the first behavior run")
    return p


def refused(p, r, label, case, before_step, before_log):
    """behavior was refused with the freeze line naming the test file and the approve-changes route, the step did
    not move and nothing was added to the human log."""
    lat = _lat()
    ok, det = lat.asks(r)
    check(f"[{case}] {label}: behavior exits non-zero with the freeze line naming tests/test_cart.py and approve-changes",
          ok, det)
    check(f"[{case}] {label}: the step did not move on", p.step() == before_step, (before_step, p.step()))
    check(f"[{case}] {label}: no approval is recorded (human log unchanged)", p.human_log_len() == before_log,
          (before_log, p.human_log_len()))


# ------------------------------------------------------------------------------------------------ CP7-T01 .. T04

def t01_test_from_main_before_the_plan_stays_frozen():
    print("CP7-T01: a test that came from main before the plan was approved stays frozen")
    lat = _lat()
    p = merged_built("merge")
    base, step, log = p.read(), p.step(), p.human_log_len()
    p.write(lat.sub(base, "assert 3 + 3 == 6", "assert 3 + 3 >= 6"))
    r = p.behavior()
    refused(p, r, "test_from_main's assertion changed", "CP7-T01", step, log)
    check("[CP7-T01] the file on disk is the fixer's edit (Ratchet did not touch it)",
          "assert 3 + 3 >= 6" in p.read(), p.read()[-200:])
    p.write(base)
    r = p.behavior()
    check("[CP7-T01] control: the file as it was goes on", r.returncode == 0, said(r)[-300:])


def t02_removing_a_test_from_main_is_asked_about():
    print("CP7-T02: removing a test that came from main is also asked about")
    lat = _lat()
    p = merged_built("merge")
    base, step, log = p.read(), p.step(), p.human_log_len()
    gone = lat.sub(base, "def test_from_main():\n    assert 3 + 3 == 6\n\n\n", "")
    p.write(gone)
    r = p.behavior()
    refused(p, r, "test_from_main deleted", "CP7-T02", step, log)
    check("[CP7-T02] the file on disk is still the fixer's version (Ratchet did not touch it)",
          p.read() == gone, p.read()[-200:])
    check("[CP7-T02] CP1 stays at its step", p.step() == step, (step, p.step()))


def t03_fast_forward_before_the_plan_is_frozen():
    print("CP7-T03: a fast-forward of main before the plan was approved is also frozen")
    lat, th = _lat(), _th()
    p = merged_built("ff")
    check("[CP7-T03] (setup) the lane really fast-forwarded: HEAD is a single-parent commit that is main's",
          len(th.git(p.d, "rev-list", "--parents", "-n", "1", "main").stdout.split()) == 2
          and th.git(p.d, "merge-base", "--is-ancestor", "main", "HEAD").returncode == 0)
    base, step, log = p.read(), p.step(), p.human_log_len()
    p.write(lat.sub(base, "assert 3 + 3 == 6", "assert 3 + 3 >= 6"))
    r = p.behavior()
    refused(p, r, "test_from_main's assertion changed after a fast-forward", "CP7-T03", step, log)


def t04_a_merge_does_not_lock_the_lanes_own_tests():
    print("CP7-T04: a merge does not lock the lane's own added tests, and planned cases stay locked")
    lat = _lat()
    p = merged_built("merge")
    base = p.read()
    p.write(base + lat.APPENDED)
    r = p.behavior()
    ok, det = lat.goes_on(p, r, p.human_log_len())
    check("[CP7-T04] (setup) appending a helper and a new test after red goes on", ok, det)
    full = p.read()
    log = p.human_log_len()
    p.write(lat.sub(full, "assert make_bad_cart().total() == 0", "assert make_bad_cart().total() + 1 == 1"))
    r = p.behavior()
    ok, det = lat.goes_on(p, r, log)
    check("[CP7-T04] changing the added test's assertion: exit 0, no approve-changes question, human log unchanged",
          ok, det)
    p.write(base)
    r = p.behavior()
    ok, det = lat.goes_on(p, r, log)
    check("[CP7-T04] removing the added test and its helper: exit 0, no approve-changes question, human log unchanged",
          ok, det)
    step = p.step()
    p.write(lat.sub(base, "    assert c.total() == 5\n", "    assert c.total() >= 5\n"))
    r = p.behavior()
    refused(p, r, "a planned case (CP1-T01) edited", "CP7-T04", step, log)
    check("[CP7-T04] the refusal names the planned case's test", "test_cp1_t01" in said(r), said(r)[-400:])


# ------------------------------------------------------------------------------------------------ CP7-T05

def t05_test_brought_in_by_merged_is_frozen():
    print("CP7-T05: a test brought in by `merged` between steps is frozen once the next step's tests are red")
    mm = _mm()
    d = mm.build("cp7t05", cp2="py")
    mm.make_main(d, py_test=True)
    mm.merge_main(d)
    r = mm.rx(d, "merged")
    check("[CP7-T05] (setup) merged accepts the merge", r.returncode == 0 and mm.known(r), said(r)[-300:])
    tf = d / "tests/test_cart.py"
    tf.write_text(mm.MAIN_PY_TEST + mm.PY_CP2_TEST)
    ok = True
    for step in ("tests-written", "red"):
        r = mm.rx(d, step, "CP2")
        ok = ok and r.returncode == 0
        check(f"[CP7-T05] (setup) {step} CP2", r.returncode == 0, said(r)[-300:])
    (d / "src/cart.py").write_text(mm.CART_PY)
    for step in ("implemented", "behavior"):
        r = mm.rx(d, step, "CP2")
        ok = ok and r.returncode == 0
        check(f"[CP7-T05] (setup) {step} CP2", r.returncode == 0, said(r)[-300:])
    if not ok:
        return
    log = mm.human_log_len(d)
    good = tf.read_text()
    tf.write_text(good.replace("assert 1 + 1 == 2", "assert 1 + 1 >= 2"))
    r = mm.rx(d, "behavior", "CP2")
    out = said(r)
    check("[CP7-T05] changing test_from_main's assertion: behavior exits non-zero with the freeze line naming "
          "tests/test_cart.py", r.returncode != 0 and FREEZE in out and "tests/test_cart.py" in out
          and "approve-changes" in out, (r.returncode, out[-400:]))
    check("[CP7-T05] no approval is recorded (human log unchanged)", mm.human_log_len(d) == log,
          (log, mm.human_log_len(d)))


# ------------------------------------------------------------------------------------------------ CP7-T06, T07

def build_nocommit(name):
    """tests/test_merge_main.py's build() flow with config commit_per_checkpoint=false and human_review each, minus
    its clean-tree assertion: CP1 goes through every gate and `complete` commits nothing, so the run waits for the
    step review with CP1's work uncommitted. Main is forked from the first commit and gets src/m.js before the lane
    does any work."""
    mm = _mm()
    tg, git, rx = mm.tg, mm.git, mm.rx
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp7-{name}-"))
    marker2 = mm.AUX / f"{d.name}.cp1runs"
    for c in (["init", "-q", "-b", "lane"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / ".ratchet").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    (d / ".ratchet/standards.md").write_text("Keep functions short\n")
    (d / "tests/py_runner.py").write_text(mm.PY_RUNNER)
    mm.commit_all(d, "init")
    git(d, "checkout", "-q", "-b", "main")
    (d / "src/m.js").write_text("export const tax = () => 0.2;\n")
    mm.commit_all(d, "M1 from main")
    git(d, "checkout", "-q", "lane")
    tg.say(d, "please build the feature")
    r = rx(d, "init", "feature", "--slug", "f", "--human-review", "each")
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, "config", "--set", "min_review_seconds=0", "providers=[]", "commit_per_checkpoint=false")
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cps = [
        {"id": "CP1", "title": "Price helper", "summary": "s", "done_when": ["works"],
         "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
         "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}},
        {"id": "CP2", "title": "Cart total", "summary": "s", "done_when": ["a cart adds its lines"],
         "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
         "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}]
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": cps}))
    for cid in ("CP1", "CP2"):
        (run / "tests" / f"{cid}.json").write_text(json.dumps({"cases": [{"id": f"{cid}-T01", "layer": "code", "proves": [1]}]}))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    tg.approve_plan(d)
    (d / "tests/cp1.test.js").write_text(mm.CP1_TEST.replace("{marker!r}", repr(str(marker2))))
    for step in ("tests-written", "red"):
        r = rx(d, step, "CP1")
        assert r.returncode == 0, step + (r.stderr + r.stdout)[-400:]
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    for step in ("implemented", "behavior"):
        r = rx(d, step, "CP1")
        assert r.returncode == 0, step + (r.stderr + r.stdout)[-400:]
    rnd = tg.open_round(d, "CP1")
    r = rx(d, "adv-record", "CP1", "--reviews", tg.write_review(d, rnd, tg.R0(rnd)))
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    assert mm.sj(d)["phase"] == "awaiting_checkpoint_review", mm.sj(d)["phase"]
    assert mm.sj(d)["checkpoints"]["CP1"].get("commit") is None, mm.sj(d)["checkpoints"]["CP1"]
    return d


def merge_main_noff(d):
    mm = _mm()
    r = mm.git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    assert r.returncode == 0, r.stderr + r.stdout
    assert len(mm.git(d, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) == 3
    assert not mm.git(d, "status", "--porcelain").stdout.strip(), mm.git(d, "status", "--porcelain").stdout


def t06_unreviewed_lane_commits_refused_without_a_commit_per_step():
    print("CP7-T06: with commit-per-checkpoint off, merged refuses lane commits no step reviewed")
    mm = _mm()
    d = build_nocommit("t06")
    (d / "src/a.js").write_text("export const a = 2;\n")
    (d / "src/extra.js").write_text("export const extra = 1;\n")
    mm.commit_all(d, "the reviewed work together with edits no step reviewed")
    merge_main_noff(d)
    before = mm.snap(d)
    check("[CP7-T06] (setup) the run waits for the step review", before["phase"] == "awaiting_checkpoint_review",
          before["phase"])
    r = mm.rx(d, "merged")
    out = said(r)
    check("[CP7-T06] merged exits non-zero", r.returncode != 0, (r.returncode, out[-400:]))
    line = next((ln for ln in out.splitlines() if UNREVIEWED in ln), "")
    check("[CP7-T06] it prints the engine's 'lane has commits since the last step finished that never went through "
          "the gates' line", bool(line), out[-500:])
    check("[CP7-T06] that line names src/a.js and src/extra.js", "src/a.js" in line and "src/extra.js" in line, line[:500])
    check("[CP7-T06] ... and does not name the reviewed src/f.js or tests/cp1.test.js",
          bool(line) and "src/f.js" not in line and "tests/cp1.test.js" not in line, line[:500])
    after = mm.snap(d)
    check("[CP7-T06] state.json is unchanged (phase, base trees, no merged_main event)",
          after == before and after["phase"] == "awaiting_checkpoint_review" and not after["merged"], (before, after))


def t07_committing_exactly_the_reviewed_work_is_accepted():
    print("CP7-T07: with commit-per-checkpoint off, committing exactly the reviewed work is still accepted")
    mm = _mm()
    d = build_nocommit("t07")
    mm.commit_all(d, "the reviewed work, by hand")
    merge_main_noff(d)
    r = mm.rx(d, "merged")
    out = said(r)
    st = mm.sj(d)
    check("[CP7-T07] merged exits 0", r.returncode == 0 and mm.known(r), (r.returncode, out[-400:]))
    check("[CP7-T07] exactly one merged_main event is logged", len(mm.history(d, "merged_main")) == 1,
          mm.history(d, "merged_main"))
    check("[CP7-T07] the run stays in awaiting_checkpoint_review", st["phase"] == "awaiting_checkpoint_review", st["phase"])
    check("[CP7-T07] no question is pending", not st.get("pending"), st.get("pending"))


# ------------------------------------------------------------------------------------------------ CP7-T08 .. T10

def not_asked_project(name):
    """A building project where `ask set-review each` is waiting for its first reply and `ask cancel` was then
    printed as NOT ASKED. Returns (d, baseline status lines, first question's asked_at)."""
    qw = _qw()
    d = qw.building_project(name)
    base = qw.status_lines(d)
    r = qw.rx(d, "ask", "set-review", "each")
    assert r.returncode == 0 and "ASK THE USER" in r.stdout, qw.out_of(r)
    first = qw.asked_at(d)
    r = qw.rx(d, "ask", "cancel")
    assert r.returncode == 0 and r.stdout.strip().startswith("NOT ASKED"), qw.out_of(r)
    return d, base, first


def t08_not_asked_question_is_not_reported_as_waiting():
    print("CP7-T08: a question printed as NOT ASKED is not reported as waiting")
    qw = _qw()
    d, base, _ = not_asked_project("cp7t08")
    p = qw.pending(d) or {}
    check("[CP7-T08] (setup) state.json holds the overtaking cancel entry", p.get("action") == "cancel"
          and bool(p.get("overtook")), p)
    new = [ln for ln in qw.status_lines(d) if ln not in base]
    check("[CP7-T08] status prints no line saying the run waits for an answer", not qw.waiting_lines(new), new)
    check("[CP7-T08] status says nothing about a question or an answer at all", not qw.mentions_question_at_all(new), new)
    sj, nx = qw.status_json(d), qw.next_json(d)
    check("[CP7-T08] status --json has no question entry (null or absent)", qw.question_of(sj) is None, qw.question_of(sj))
    check("[CP7-T08] next has no question entry (null or absent)", qw.question_of(nx) is None, qw.question_of(nx))
    p2 = qw.pending(d) or {}
    check("[CP7-T08] state.json still holds the overtaking cancel entry, so no reply can approve it",
          p2.get("action") == "cancel" and bool(p2.get("overtook")), p2)


def _since_check(case, label, d, kind, base, expected_since):
    qw = _qw()
    new = [ln for ln in qw.status_lines(d) if ln not in base]
    wl = qw.waiting_lines(new)
    check(f"[{case}] {label}: status has exactly one waiting line", len(wl) == 1, new)
    sj, nx = qw.status_json(d), qw.next_json(d)
    q_s, q_n = qw.question_of(sj), qw.question_of(nx)
    check(f"[{case}] {label}: status --json question has kind '{kind}', waiting_on 'user' and since = asked_at",
          isinstance(q_s, dict) and q_s.get("kind") == kind and q_s.get("waiting_on") == "user"
          and q_s.get("since") == expected_since and bool(expected_since), (q_s, expected_since))
    check(f"[{case}] {label}: next carries the same question entry", isinstance(q_n, dict) and q_n == q_s, (q_n, q_s))


def t09_a_real_question_is_still_reported_also_after_not_asked():
    print("CP7-T09: a real waiting question is still reported, including one asked again after NOT ASKED")
    qw = _qw()
    d = qw.building_project("cp7t09")
    base = qw.status_lines(d)
    r = qw.rx(d, "ask", "set-review", "each")
    check("[CP7-T09] (setup) `ask set-review each` is accepted", r.returncode == 0 and "ASK THE USER" in r.stdout,
          qw.out_of(r))
    first = qw.asked_at(d)
    _since_check("CP7-T09", "set-review waiting alone", d, "set-review", base, first)
    r = qw.rx(d, "ask", "cancel")
    check("[CP7-T09] (setup) `ask cancel` over it prints NOT ASKED", r.stdout.strip().startswith("NOT ASKED"),
          qw.out_of(r))
    r = qw.rx(d, "ask", "cancel")
    check("[CP7-T09] (setup) asking cancel again prints the real question",
          r.returncode == 0 and "ASK THE USER" in r.stdout and not r.stdout.strip().startswith("NOT ASKED"),
          qw.out_of(r))
    second = qw.asked_at(d)
    p = qw.pending(d) or {}
    check("[CP7-T09] (setup) the new cancel entry is not an overtaker", p.get("action") == "cancel"
          and not p.get("overtook"), p)
    _since_check("CP7-T09", "cancel asked again", d, "cancel", base, second)
    t1, t2 = dt.datetime.fromisoformat(first), dt.datetime.fromisoformat(second)
    check("[CP7-T09] the second 'since' is not earlier than the first, and differs from it",
          t2 >= t1 and second != first, (first, second))


def t10_a_reply_cannot_approve_a_not_asked_question():
    print("CP7-T10: a reply cannot approve a question that was printed as NOT ASKED")
    qw = _qw()
    d, base, _ = not_asked_project("cp7t10")
    qw.reply_yes(d, "yes, go ahead")
    r = qw.rx(d, "cancel")
    out = said(r)
    st = json.loads(qw.state_path(d).read_text())
    check("[CP7-T10] cancel exits non-zero", r.returncode != 0, (r.returncode, out[-400:]))
    check("[CP7-T10] the refusal says the question was printed while another one (change how often they review) "
          "was waiting", NOT_ASKED_OVER in out, out[-500:])
    check("[CP7-T10] it tells the agent to print the question again on its own", "Print it again on its own" in out,
          out[-500:])
    check("[CP7-T10] the run is not cancelled: the phase is still building", st.get("phase") == "building",
          st.get("phase"))


# ------------------------------------------------------------------------------------------------ driver

TESTS = [
    ("CP7-T01", t01_test_from_main_before_the_plan_stays_frozen),
    ("CP7-T02", t02_removing_a_test_from_main_is_asked_about),
    ("CP7-T03", t03_fast_forward_before_the_plan_is_frozen),
    ("CP7-T04", t04_a_merge_does_not_lock_the_lanes_own_tests),
    ("CP7-T05", t05_test_brought_in_by_merged_is_frozen),
    ("CP7-T06", t06_unreviewed_lane_commits_refused_without_a_commit_per_step),
    ("CP7-T07", t07_committing_exactly_the_reviewed_work_is_accepted),
    ("CP7-T08", t08_not_asked_question_is_not_reported_as_waiting),
    ("CP7-T09", t09_a_real_question_is_still_reported_also_after_not_asked),
    ("CP7-T10", t10_a_reply_cannot_approve_a_not_asked_question),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    only = [a.upper() for a in sys.argv[1:]]   # optional: case ids to run while working on one ("T06 T08")
    for cid, fn in TESTS:
        if only and not any(cid.endswith(o) for o in only):
            continue
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"[{cid}] stopped early: {type(e).__name__}: {e}"[:200])
            print(f"  ✗ [{cid}] stopped early: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


import feedback_fixes_cases  # noqa: E402,F401  (adds the CP7-X cases after the planned ones)


if __name__ == "__main__":
    main()
