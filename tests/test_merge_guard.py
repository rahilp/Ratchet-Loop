#!/usr/bin/env python3
"""
CP6 tests: steer merges to happen only between steps.   python3 tests/test_merge_guard.py

Between steps (a step is complete and the next one has not started its own work) the guard lets an agent merge main
into the lane and resolve a conflict in Ratchet's four shared tracked files (.ratchet/config.json, standards.md,
learnings.md, .gitignore) with plain git commands. In the middle of a step, `git merge` and `git pull` are refused with
a plain message to finish the step first (run it through `complete`). After a merge landed, `next` tells the agent to
run `ratchet.py merged`, and the step's first command (`tests-written`) refuses until it has. Every other guard rule
behaves exactly as before.

Every case drives the repo's own ratchet.py: `ratchet.py hook guard --host claude` with a Bash tool call as JSON on
stdin (as tests/test_gates.py's guard() does), plus `next`, `merged` and `tests-written` as commands, in throwaway git
projects built with tests/test_merge_main.py's fixtures (imported, not edited): a two-step plan, CP1 complete, CP2
current. Real git (outside the guard) makes the merges, conflicts and merge commits, so MERGE_HEAD and the conflicted
files are genuine. The tests assert only on what an agent sees (exit 0 or 2, and the words of a refusal), never on how
the guard parses a command. Words are matched on output with absolute paths cut out (said()).

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
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
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402,F401  (temp folders are cleaned up when the suite exits)
import test_gates as tg  # noqa: E402
import test_merge_main as mm  # noqa: E402  (its fixtures: build(), make_main(), cp2_tests_written(), said(), ...)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP6-[TX]\d+[a-z]?)\]")
SHARED = [".ratchet/config.json", ".ratchet/standards.md", ".ratchet/learnings.md", ".ratchet/.gitignore"]
GENERIC = "could change Ratchet's run state"   # the guard's existing text for a command that touches run state


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


class Stop(Exception):
    """A case cannot go on after a failed check that was already recorded."""


def need(name, cond, detail=""):
    check(name, cond, detail)
    if not cond:
        raise Stop(name)


# ------------------------------------------------------------------------------------------------ helpers

said, git, rx, sj = mm.said, mm.git, mm.rx, mm.sj


def ask(d, cmd):
    """What the guard says about a Bash command: (exit code, what it printed with absolute paths cut out)."""
    r = tg.guard(d, "Bash", {"command": cmd})
    return r.returncode, said(r).strip()


def allowed(d, cmd):
    rc, out = ask(d, cmd)
    return rc == 0 and "RATCHET GUARD" not in out, (cmd, rc, out[-200:])


def refused(d, cmd, generic=None):
    """Refused (exit 2) with the guard's own line. generic=True: the existing 'could change run state' text;
    generic=False: some other message; None: either."""
    rc, out = ask(d, cmd)
    ok = rc == 2 and "RATCHET GUARD" in out
    if generic is True:
        ok = ok and GENERIC in out
    if generic is False:
        ok = ok and GENERIC not in out
    return ok, (cmd, rc, out[-200:])


def finish_first(d, cmd):
    """Refused with a plain message that says to finish the step first and names `complete`; not the generic text."""
    rc, out = ask(d, cmd)
    ok = (rc == 2 and "RATCHET GUARD" in out and re.search(r"finish", out, re.I) is not None
          and re.search(r"\bcomplete\b", out) is not None and GENERIC not in out)
    return ok, (cmd, rc, out[-300:])


def head_and_status(d):
    return git(d, "rev-parse", "HEAD").stdout.strip(), git(d, "branch", "--show-current").stdout.strip(), \
        git(d, "status", "--porcelain").stdout


def upstream(d):
    """Main forks from the lane's current commit and gets one commit that adds src/upstream.js; back on the lane."""
    r = git(d, "checkout", "-q", "-b", "main")
    assert r.returncode == 0, r.stderr
    (d / "src/upstream.js").write_text("export const upstream = () => 1;\n")
    mm.commit_all(d, "M1 from main: upstream.js")
    r = git(d, "checkout", "-q", "lane")
    assert r.returncode == 0, r.stderr


def edit_shared(d, tag):
    """Changes one line at the end of each of the four shared files, differently for each tag."""
    for f in SHARED:
        p = d / f
        if f.endswith("config.json"):
            c = json.loads(p.read_text())
            c[f"zz_{tag}_note"] = tag
            p.write_text(json.dumps(c, indent=2) + "\n")
        else:
            text = p.read_text()
            p.write_text(text + ("" if text.endswith("\n") or not text else "\n") + f"{tag} line\n")


def prepare_conflict(d):
    """Main and the lane each change the four shared files (main also adds src/upstream.js). Merge not run yet."""
    r = git(d, "checkout", "-q", "-b", "main")
    assert r.returncode == 0, r.stderr
    edit_shared(d, "main")
    (d / "src/upstream.js").write_text("export const upstream = () => 1;\n")
    mm.commit_all(d, "M1 from main: shared files and upstream.js")
    r = git(d, "checkout", "-q", "lane")
    assert r.returncode == 0, r.stderr
    edit_shared(d, "lane")
    mm.commit_all(d, "lane: own edit of the shared files")


def start_conflicted_merge(d, case):
    r = git(d, "merge", "--no-ff", "-m", "Merge main into lane", "main")
    bad = sorted(git(d, "diff", "--name-only", "--diff-filter=U").stdout.split())
    need(f"[{case}] (setup) git merge main stops with MERGE_HEAD and the four shared files conflicted",
         r.returncode != 0 and git(d, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0
         and bad == sorted(SHARED), (r.returncode, bad, (r.stdout + r.stderr)[-200:]))


def next_action(d):
    r = rx(d, "next")
    try:
        return json.loads(r.stdout).get("action") or ""
    except ValueError:
        return said(r)


def mentions_merged(text):
    return re.search(r"\bmerged\b", text) is not None


# ------------------------------------------------------------------------------------------------ cases

def t01_merge_or_pull_between_steps():
    print("CP6-T01: between steps, an agent may merge or pull main")
    d = mm.build("g01")
    upstream(d)
    before = head_and_status(d)
    for cmd in ("git merge main", "git pull origin main", "git merge --no-ff main"):
        ok, detail = allowed(d, cmd)
        check(f"[CP6-T01] the guard allows `{cmd}` between steps", ok, detail)
        rc, out = ask(d, cmd)
        check(f"[CP6-T01] ... and says nothing refusing (`{cmd}`)", rc == 0 and out.strip() == "", (rc, out[-200:]))
    check("[CP6-T01] (setup) asking the guard changed neither the branch nor the tree", head_and_status(d) == before, "")
    r = git(d, "merge", "main")
    check("[CP6-T01] the real merge succeeds and src/upstream.js is in the lane",
          r.returncode == 0 and (d / "src/upstream.js").exists(), (r.returncode, (r.stdout + r.stderr)[-200:]))


def t02_merge_at_the_very_start():
    print("CP6-T02: at the very start of the first step, a merge is also allowed")
    d = tg.project("g02")
    tg.approve_plan(d)
    st = sj(d)
    need("[CP6-T02] (setup) the plan is approved and CP1 is current with its tests not written",
         st["phase"] == "building" and st["current"] == "CP1" and st["checkpoints"]["CP1"]["step"] == "tests",
         (st["phase"], st["current"]))
    here = git(d, "branch", "--show-current").stdout.strip()
    if here != "main":   # main one commit ahead, when the project's own branch is not called main
        git(d, "checkout", "-q", "-b", "main")
        (d / "src/upstream.js").write_text("export const upstream = () => 1;\n")
        git(d, "add", "src/upstream.js")
        git(d, "commit", "-qm", "M1 from main")
        git(d, "checkout", "-q", here)
    ok, detail = allowed(d, "git merge main")
    check("[CP6-T02] the guard allows `git merge main` before the step has any work of its own", ok, detail)
    ok, detail = allowed(d, "git pull origin main")
    check("[CP6-T02] ... and `git pull origin main`", ok, detail)


def t03_resolve_conflicts_between_steps():
    print("CP6-T03: between steps, a conflict in Ratchet's shared files can be resolved with git")
    d = mm.build("g03")
    prepare_conflict(d)
    start_conflicted_merge(d, "CP6-T03")
    cmds = ["git checkout --theirs .ratchet/config.json", "git checkout --ours .ratchet/learnings.md",
            "git checkout main -- .ratchet/standards.md",
            "git add .ratchet/config.json .ratchet/standards.md .ratchet/learnings.md .ratchet/.gitignore",
            "git commit --no-edit", "git merge --continue", "git merge --abort"]
    for cmd in cmds:
        ok, detail = allowed(d, cmd)
        check(f"[CP6-T03] the guard allows `{cmd}` during the conflicted merge", ok, detail)
    rcs = [git(d, "checkout", "--theirs", ".ratchet/config.json").returncode,
           git(d, "checkout", "--ours", ".ratchet/learnings.md").returncode,
           git(d, "checkout", "main", "--", ".ratchet/standards.md").returncode,
           git(d, "checkout", "--ours", ".ratchet/.gitignore").returncode]
    need("[CP6-T03] (setup) git resolves the four files", rcs == [0, 0, 0, 0], rcs)
    r = git(d, "add", *SHARED)
    need("[CP6-T03] (setup) git add of the four files works", r.returncode == 0, r.stderr)
    r = git(d, "commit", "--no-edit")
    parents = git(d, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    check("[CP6-T03] the real merge ends as a merge commit (two parents) and MERGE_HEAD is gone",
          r.returncode == 0 and len(parents) == 3
          and git(d, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0, (r.returncode, r.stderr[-200:]))
    left = [f for f in SHARED if re.search(r"^(<<<<<<<|=======|>>>>>>>)", (d / f).read_text(), re.M)]
    check("[CP6-T03] no conflict markers are left in the four files", not left, left)
    check("[CP6-T03] config.json holds main's version (it was taken with --theirs)",
          json.loads((d / ".ratchet/config.json").read_text()).get("zz_main_note") == "main", "")


def t04_mid_step_merge_is_refused():
    print("CP6-T04: in the middle of a step, merge and pull are refused with a plain message")
    d = mm.build("g04")
    upstream(d)
    need("[CP6-T04] (setup) tests-written CP2 ran", mm.cp2_tests_written(d).returncode == 0, "")
    for point in ("after tests-written", "after red"):
        if point == "after red":
            need("[CP6-T04] (setup) red CP2 ran", rx(d, "red", "CP2").returncode == 0, "")
        before, state = head_and_status(d), mm.snap(d)
        for cmd in ("git merge main", "git pull origin main"):
            ok, detail = finish_first(d, cmd)
            check(f"[CP6-T04] `{cmd}` is refused {point}, telling the agent to finish the step with complete", ok, detail)
        check(f"[CP6-T04] {point}: the branch, the tree and the run's state are untouched",
              head_and_status(d) == before and mm.snap(d) == state, "")


def t05_written_tests_count_as_mid_step():
    print("CP6-T05: test files already written for the step count as mid-step")
    d = mm.build("g05")
    upstream(d)
    (d / "tests/cp2.test.js").write_text(mm.CP2_TEST_JS)
    need("[CP6-T05] (setup) tests-written has not been run: CP2 is still at its first step",
         sj(d)["checkpoints"]["CP2"]["step"] == "tests", sj(d)["checkpoints"]["CP2"]["step"])
    before = head_and_status(d)
    ok, detail = finish_first(d, "git merge main")
    check("[CP6-T05] `git merge main` is refused with the finish-the-step message", ok, detail)
    check("[CP6-T05] the branch and the tree are untouched", head_and_status(d) == before, "")


def t06_final_review_allows_a_merge():
    print("CP6-T06: while the run waits for the final review, a merge is allowed")
    d = mm.build("g06")
    mm.cp2_tests_written(d, to_complete=True)
    rnd = tg.open_round(d, "CP2")
    r = rx(d, "adv-record", "CP2", "--reviews", tg.write_review(d, rnd, tg.R0(rnd)))
    need("[CP6-T06] (setup) the CP2 review is recorded", r.returncode == 0, said(r)[-300:])
    r = rx(d, "complete", "CP2")
    need("[CP6-T06] (setup) the last step completes and the run waits for the final review",
         r.returncode == 0 and sj(d)["phase"] == "awaiting_final_review", (r.returncode, sj(d)["phase"], said(r)[-300:]))
    ok, detail = allowed(d, "git merge main")
    check("[CP6-T06] the guard allows `git merge main` while the final review waits", ok, detail)
    prepare_conflict(d)
    start_conflicted_merge(d, "CP6-T06")
    ok, detail = allowed(d, "git checkout --theirs .ratchet/standards.md")
    check("[CP6-T06] ... and `git checkout --theirs .ratchet/standards.md` during the conflicted merge", ok, detail)


def t07_unchecked_merge_blocks_tests_written():
    print("CP6-T07: a merge Ratchet has not checked yet is pointed out by next and blocks tests-written")
    d = mm.build("g07")
    upstream(d)
    mm.merge_main(d)
    action = next_action(d)
    check("[CP6-T07] next tells the agent to run `ratchet.py merged`", "ratchet.py merged" in action, action[-300:])
    (d / "tests/cp2.test.js").write_text(mm.CP2_TEST_JS)
    before = mm.snap(d)
    r = rx(d, "tests-written", "CP2")
    out = mm.ours(r)
    check("[CP6-T07] tests-written exits non-zero with the engine's own message", r.returncode != 0 and mm.known(r),
          (r.returncode, out[-300:]))
    check("[CP6-T07] the message names `merged` as the command to run first",
          "ratchet.py merged" in out or mentions_merged(out), out[-300:])
    st = sj(d)
    check("[CP6-T07] the step's recorded state is unchanged: CP2 still building, tests not recorded",
          mm.snap(d) == before and st["phase"] == "building" and st["checkpoints"]["CP2"]["step"] == "tests",
          (st["phase"], st["checkpoints"]["CP2"]["step"]))


def t08_after_merged_the_step_carries_on():
    print("CP6-T08: once merged has passed, next and tests-written carry on as normal")
    d = mm.build("g08")
    upstream(d)
    mm.merge_main(d)
    r = rx(d, "merged")
    need("[CP6-T08] merged succeeds", r.returncode == 0 and mm.known(r), (r.returncode, said(r)[-300:]))
    action = next_action(d)
    check("[CP6-T08] next no longer mentions merged", action != "" and not mentions_merged(action), action[-300:])
    r = mm.cp2_tests_written(d)
    check("[CP6-T08] tests-written CP2 succeeds", r.returncode == 0, (r.returncode, said(r)[-300:]))
    step = sj(d)["checkpoints"]["CP2"]["step"]
    check("[CP6-T08] the step proceeds past its first command", step != "tests", step)


def t09_the_allowance_is_narrow():
    print("CP6-T09: the merge allowance is narrow: run state and everything else stay protected")
    d = mm.build("g09")
    prepare_conflict(d)
    others = ["git stash -u", "sed -i s/a/b/ .ratchet/standards.md",
              "git checkout --theirs .ratchet/runs/f/checkpoints.json"]
    # (a) no merge in progress: the shared config file is still guarded
    ok, detail = refused(d, "git add .ratchet/config.json", generic=True)
    check("[CP6-T09] (a) without a merge in progress `git add .ratchet/config.json` is refused (existing message)",
          ok, detail)
    ok, detail = refused(d, "git checkout --theirs .ratchet/config.json", generic=True)
    check("[CP6-T09] (a) ... and so is `git checkout --theirs .ratchet/config.json`", ok, detail)
    baseline = {cmd: ask(d, cmd) for cmd in others}   # how the guard treats these today, with no merge in progress
    start_conflicted_merge(d, "CP6-T09")
    # (b) a conflicted merge between steps
    ok, detail = allowed(d, "git add .ratchet/config.json")
    check("[CP6-T09] (b) control: during the conflicted merge `git add .ratchet/config.json` is the newly allowed case",
          ok, detail)
    for cmd in ("git checkout main -- .ratchet/runs/f/state.json", "git add .ratchet/runs/f/state.json"):
        ok, detail = refused(d, cmd, generic=True)
        check(f"[CP6-T09] (b) `{cmd}` is still refused (existing message)", ok, detail)
    ok, detail = refused(d, "echo x > .ratchet/config.json", generic=True)
    check("[CP6-T09] (b) `echo x > .ratchet/config.json` is still refused (existing message)", ok, detail)
    rc, out = ask(d, "git clean -fdx")
    check("[CP6-T09] (b) `git clean -fdx` is still refused", rc == 2 and "RATCHET GUARD" in out, (rc, out[-200:]))
    for cmd in others:   # refused or allowed, the guard answers exactly as it did with no merge in progress
        check(f"[CP6-T09] (b) `{cmd}` gets exactly the answer it got before the merge started",
              ask(d, cmd) == baseline[cmd], (baseline[cmd], ask(d, cmd)))


def t10_a_lane_that_never_merges_sees_no_change():
    print("CP6-T10: a lane that never merges sees no change")
    d = mm.build("g10")
    need("[CP6-T10] (setup) tests-written CP2 ran", mm.cp2_tests_written(d).returncode == 0, "")
    for cmd in ("git status", "git log --oneline", "git diff", "git checkout -b scratch", "git commit -m x"):
        rc, out = ask(d, cmd)
        check(f"[CP6-T10] mid-step `{cmd}` is allowed with no message", rc == 0 and out == "", (rc, out[-200:]))
        check(f"[CP6-T10] mid-step `{cmd}` gets no message about merging", "merg" not in out.lower(), out[-200:])
    action = next_action(d)
    check("[CP6-T10] mid-step next never mentions merged", action != "" and not mentions_merged(action), action[-300:])
    d = mm.build("g10b")
    action = next_action(d)
    check("[CP6-T10] at the boundary next never mentions merged", action != "" and not mentions_merged(action),
          action[-300:])
    rc, out = ask(d, "git status")
    check("[CP6-T10] at the boundary `git status` is allowed with no message", rc == 0 and out == "", (rc, out[-200:]))


# ------------------------------------------------------------------------------------------------ driver

TESTS = [
    ("CP6-T01", t01_merge_or_pull_between_steps),
    ("CP6-T02", t02_merge_at_the_very_start),
    ("CP6-T03", t03_resolve_conflicts_between_steps),
    ("CP6-T04", t04_mid_step_merge_is_refused),
    ("CP6-T05", t05_written_tests_count_as_mid_step),
    ("CP6-T06", t06_final_review_allows_a_merge),
    ("CP6-T07", t07_unchecked_merge_blocks_tests_written),
    ("CP6-T08", t08_after_merged_the_step_carries_on),
    ("CP6-T09", t09_the_allowance_is_narrow),
    ("CP6-T10", t10_a_lane_that_never_merges_sees_no_change),
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
    only = [a.upper() for a in sys.argv[1:]]   # optional: case ids to run while working on one ("T05 T08")
    for cid, fn in TESTS:
        if only and not any(cid.endswith(o) for o in only):
            continue
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Stop:
            pass    # the failed check that stopped the case is already recorded
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"[{cid}] stopped early: {type(e).__name__}: {e}"[:200])
            print(f"  ✗ [{cid}] stopped early: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


import merge_guard_cases  # noqa: E402,F401  (adds the CP6-X cases after the planned ones)


if __name__ == "__main__":
    main()
