"""
CP6 extra checks (CP6-X01 to CP6-X07), added after review round 1. They run as part of tests/test_merge_guard.py,
which imports this file just before it starts (the planned file's own lines are frozen, so its cases cannot be
extended in place) and uses its helpers and its case list. It is not named test_*.py so the full suite does not run
its cases twice.

CP6-X01: a plain `git merge main` that fast-forwards the lane (no merge commit) is flagged by next and tests-written,
         at a step boundary and at the very start of step 1, with the way out (`git reset --keep <old sha>`, then
         `git merge --no-ff`); following the hint leads to merged and on. A lane's own commit is not flagged.
CP6-X02: after a hand-resolved merge was accepted in a step, the guard refuses a second merge or pull with the
         finish-the-step message, and if one lands anyway next, tests-written and merged each name
         `git reset --keep <first parent>`.
CP6-X03: mid-step, `git merge --abort`, `--continue` and `--quit` are allowed.
CP6-X04: a merge commit older than the run is left alone by next and tests-written.
CP6-X05: a merge in which main only deleted a file is flagged like any other unaccepted merge.
CP6-X06: a merge that changes nothing but the run's own folder is not flagged because a git-ignored
         fingerprint_include file is missing from the merge commit.
CP6-X07: a fast-forward to a main tip committed before the run started (dated 2020) is flagged like any other.
CP6-X08: a fast-forward pull made before the run, plus an uncommitted file at plan approval, does not flag step 1
         (the lane never merged); a file only uncommitted at step start is never counted as removed by main.
"""
from __future__ import annotations

import os
import subprocess
import sys

_main = sys.modules.get("__main__")
base = _main if hasattr(_main, "TESTS") and hasattr(_main, "ask") else __import__("test_merge_guard")
mm = base.mm
import merge_main_cases as mmc  # noqa: E402  (its divergent_merge(): a hand-resolved conflict; it adds its own CP5 cases to test_merge_main's list)

check, need, ask, allowed, refused, finish_first = base.check, base.need, base.ask, base.allowed, base.refused, base.finish_first
git, rx, said, sj, tg = base.git, base.rx, base.said, base.sj, base.tg
next_action, mentions_merged, upstream = base.next_action, base.mentions_merged, base.upstream


def sha(d, rev):
    return git(d, "rev-parse", rev).stdout.strip()


def parents(d):
    return git(d, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()


def tests_written_says(d):
    (d / "tests/cp2.test.js").write_text(mm.CP2_TEST_JS)
    r = rx(d, "tests-written", "CP2")
    return r, mm.ours(r)


def x01_fast_forward_is_flagged():
    print("CP6-X01: a merge that fast-forwards the lane is flagged, with the way out")
    d = mm.build("gx01")
    old = sha(d, "HEAD")[:7]
    upstream(d)
    r = git(d, "merge", "main")
    need("[CP6-X01] (setup) a plain `git merge main` fast-forwards: no merge commit", r.returncode == 0
         and len(parents(d)) == 2 and sha(d, "HEAD") == sha(d, "main"), (r.returncode, (r.stdout + r.stderr)[-200:]))
    action = next_action(d)
    check("[CP6-X01] next names the old commit to reset to and a --no-ff redo",
          f"git reset --keep {old}" in action and "--no-ff" in action and "ratchet.py merged" in action, action[-400:])
    before = mm.snap(d)
    r, out = tests_written_says(d)
    check("[CP6-X01] tests-written is refused with the same way out",
          r.returncode != 0 and mm.known(r) and f"git reset --keep {old}" in out and "--no-ff" in out, (r.returncode, out[-400:]))
    check("[CP6-X01] the refusal changed nothing", mm.snap(d) == before, "")
    (d / "tests/cp2.test.js").unlink()
    r = git(d, "reset", "--keep", old)
    need("[CP6-X01] (setup) the named reset works", r.returncode == 0, r.stderr)
    action = next_action(d)
    check("[CP6-X01] after the reset next no longer mentions merged", action != "" and not mentions_merged(action), action[-300:])
    r = git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    need("[CP6-X01] (setup) the --no-ff merge makes a merge commit", r.returncode == 0 and len(parents(d)) == 3, r.stderr)
    check("[CP6-X01] next now says to run merged", "ratchet.py merged" in next_action(d), "")
    r = rx(d, "merged")
    need("[CP6-X01] merged accepts the redone merge", r.returncode == 0 and mm.known(r), mm.said(r)[-300:])
    r, out = tests_written_says(d)
    check("[CP6-X01] tests-written then goes ahead", r.returncode == 0, (r.returncode, out[-300:]))


def x01b_fast_forward_at_the_start_of_step_one():
    print("CP6-X01b: the same at the very start of the first step")
    d = tg.project("gx01b")
    tg.approve_plan(d)
    here = git(d, "branch", "--show-current").stdout.strip()
    old = sha(d, "HEAD")[:7]
    if here != "main":
        git(d, "checkout", "-q", "-b", "main")
        (d / "src/upstream.js").write_text("export const upstream = () => 1;\n")
        git(d, "add", "src/upstream.js")
        git(d, "commit", "-qm", "M1 from main")
        git(d, "checkout", "-q", here)
    ok, detail = allowed(d, "git merge main")
    check("[CP6-X01b] the guard still allows `git merge main`", ok, detail)
    r = git(d, "merge", "main")
    need("[CP6-X01b] (setup) the merge fast-forwards", r.returncode == 0 and len(parents(d)) == 2, r.stderr)
    action = next_action(d)
    check("[CP6-X01b] next names the way out", f"git reset --keep {old}" in action and "--no-ff" in action, action[-400:])
    r = rx(d, "tests-written", "CP1")
    out = mm.ours(r)
    check("[CP6-X01b] tests-written is refused with the way out",
          r.returncode != 0 and mm.known(r) and f"git reset --keep {old}" in out, (r.returncode, out[-400:]))


def x01c_own_commit_is_not_flagged():
    print("CP6-X01c: a lane's own commit in the middle of a step is not a fast-forward")
    d = mm.build("gx01c")
    (d / "src/own.js").write_text("export const own = 1;\n")
    mm.commit_all(d, "lane: own work")
    action = next_action(d)
    check("[CP6-X01c] next does not mention merged", action != "" and not mentions_merged(action), action[-300:])
    r = mm.cp2_tests_written(d)
    check("[CP6-X01c] tests-written goes ahead", r.returncode == 0, (r.returncode, said(r)[-300:]))


def x02_second_merge_after_a_hand_resolved_one():
    print("CP6-X02: a second merge after a hand-resolved one is refused, and a way out is named")
    d = mm.build("gx02")
    with (d / ".git/info/exclude").open("a") as f:   # the run's records stay out of main's commits, as in real projects
        f.write("/.ratchet/*\n")
    mmc.divergent_merge(d)
    r = rx(d, "merged")
    need("[CP6-X02] (setup) merged accepts the hand-resolved merge",
         r.returncode == 0 and mm.known(r) and sj(d)["history"][-1].get("by_hand"), mm.said(r)[-300:])
    git(d, "checkout", "-q", "main")
    (d / "src/m2.js").write_text("export const second = 2;\n")
    mm.commit_all(d, "M2 from main")
    need("[CP6-X02] (setup) back on the lane", git(d, "checkout", "-q", "lane").returncode == 0, "")
    for cmd in ("git merge main", "git merge --no-ff main", "git pull origin main"):
        ok, detail = finish_first(d, cmd)
        check(f"[CP6-X02] `{cmd}` is refused with the finish-the-step message", ok, detail)
    ok, detail = allowed(d, "git merge --abort")
    check("[CP6-X02] `git merge --abort` is still allowed", ok, detail)
    p1 = sha(d, "HEAD")[:7]
    r = git(d, "merge", "--no-ff", "-q", "-m", "Merge main again", "main")
    need("[CP6-X02] (setup) real git makes the second merge anyway", r.returncode == 0 and len(parents(d)) == 3, r.stderr)
    action = next_action(d)
    check("[CP6-X02] next names `git reset --keep <first parent>`", f"git reset --keep {p1}" in action, action[-400:])
    r, out = tests_written_says(d)
    check("[CP6-X02] tests-written names it too", r.returncode != 0 and f"git reset --keep {p1}" in out, out[-400:])
    r = rx(d, "merged")
    check("[CP6-X02] and so does merged", r.returncode != 0 and f"git reset --keep {p1}" in mm.ours(r), mm.ours(r)[-400:])
    (d / "tests/cp2.test.js").unlink()
    r = git(d, "reset", "--keep", p1)
    need("[CP6-X02] (setup) the named reset works", r.returncode == 0, r.stderr)
    action = next_action(d)
    check("[CP6-X02] after the reset next no longer mentions merged", action != "" and not mentions_merged(action), action[-300:])


def x03_abort_continue_quit_are_allowed_mid_step():
    print("CP6-X03: in the middle of a step, git merge --abort / --continue / --quit are allowed")
    d = mm.build("gx03")
    need("[CP6-X03] (setup) tests-written CP2 ran", mm.cp2_tests_written(d).returncode == 0, "")
    for point in ("after tests-written", "after red"):
        if point == "after red":
            need("[CP6-X03] (setup) red CP2 ran", rx(d, "red", "CP2").returncode == 0, "")
        for cmd in ("git merge --abort", "git merge --continue", "git merge --quit"):
            ok, detail = allowed(d, cmd)
            check(f"[CP6-X03] `{cmd}` is allowed {point}", ok, detail)
        ok, detail = finish_first(d, "git merge main")
        check(f"[CP6-X03] ... while `git merge main` is still refused {point}", ok, detail)


def x04_an_old_merge_commit_is_left_alone():
    print("CP6-X04: a merge commit from before the run is not flagged")
    d = mm.build("gx04")
    upstream(d)
    old = "2020-01-01T00:00:00"
    r = subprocess.run(["git", "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main"], cwd=d,
                       capture_output=True, text=True, env=dict(os.environ, GIT_COMMITTER_DATE=old, GIT_AUTHOR_DATE=old))
    need("[CP6-X04] (setup) an old merge commit is HEAD", r.returncode == 0 and len(parents(d)) == 3, r.stderr)
    action = next_action(d)
    check("[CP6-X04] next does not mention merged", action != "" and not mentions_merged(action), action[-300:])
    r, out = tests_written_says(d)
    check("[CP6-X04] tests-written goes ahead", r.returncode == 0, (r.returncode, out[-300:]))


def x05_deletion_only_merge_is_flagged():
    print("CP6-X05: a merge in which main only deleted a file is flagged")
    d = mm.build("gx05")
    git(d, "checkout", "-q", "-b", "main")
    (d / "src/a.js").unlink()
    mm.commit_all(d, "M1 from main: remove a.js")
    git(d, "checkout", "-q", "lane")
    mm.merge_main(d)
    need("[CP6-X05] (setup) the merge removed src/a.js", not (d / "src/a.js").exists(), "")
    action = next_action(d)
    check("[CP6-X05] next says to run merged", "ratchet.py merged" in action, action[-300:])
    r, out = tests_written_says(d)
    check("[CP6-X05] tests-written is refused and names merged",
          r.returncode != 0 and mm.known(r) and "ratchet.py merged" in out, (r.returncode, out[-300:]))


def x06_a_missing_ignored_include_file_is_not_a_deletion():
    print("CP6-X06: a git-ignored fingerprint_include file missing from a merge commit is not flagged")
    d = mmc.build_in_subfolder_with_include("gx06")
    need("[CP6-X06] (setup) git ignores the include file", git(d, "check-ignore", "-q", ".env.test").returncode == 0, "")
    mm.make_main(d, code=False, std=True)   # main changes only the run's own shared file
    mm.merge_main(d)
    action = next_action(d)
    check("[CP6-X06] next does not mention merged", action != "" and not mentions_merged(action), action[-300:])


def x07_fast_forward_to_a_tip_older_than_the_run():
    print("CP6-X07: a fast-forward to a main tip committed before the run started is flagged")
    d = mm.build("gx07")
    old = sha(d, "HEAD")[:7]
    saved = {k: os.environ.get(k) for k in ("GIT_COMMITTER_DATE", "GIT_AUTHOR_DATE")}
    os.environ["GIT_COMMITTER_DATE"] = os.environ["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00+0000"
    try:
        upstream(d)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    r = git(d, "merge", "main")
    need("[CP6-X07] (setup) a plain `git merge main` fast-forwards to a tip dated 2020",
         r.returncode == 0 and len(parents(d)) == 2 and sha(d, "HEAD") == sha(d, "main"), (r.returncode, (r.stdout + r.stderr)[-200:]))
    action = next_action(d)
    check("[CP6-X07] next names merged and the reset hint",
          f"git reset --keep {old}" in action and "--no-ff" in action and "ratchet.py merged" in action, action[-400:])
    r, out = tests_written_says(d)
    check("[CP6-X07] tests-written is refused with the same way out",
          r.returncode != 0 and mm.known(r) and f"git reset --keep {old}" in out, (r.returncode, out[-400:]))


def x08_a_pull_before_the_run_does_not_flag_step_one():
    print("CP6-X08: a fast-forward pull made before the run, plus an uncommitted file at plan approval, is not flagged")
    d = tg.project("gx08")
    here = git(d, "branch", "--show-current").stdout.strip()
    git(d, "checkout", "-q", "-b", "other")
    (d / "src/b.js").write_text("export const b = 1;\n")
    mm.commit_all(d, "other: b")
    git(d, "checkout", "-q", here)
    past = "2020-01-01T00:00:00+0000"   # the pull's own time: before the run was created
    r = subprocess.run(["git", "pull", "-q", ".", "other"], cwd=d, capture_output=True, text=True,
                       env=dict(os.environ, GIT_COMMITTER_DATE=past, GIT_AUTHOR_DATE=past))
    need("[CP6-X08] (setup) a plain pull fast-forwards the lane before the run's plan is approved",
         r.returncode == 0 and len(parents(d)) == 2 and sha(d, "HEAD") == sha(d, "other"), (r.returncode, (r.stdout + r.stderr)[-200:]))
    (d / "src").joinpath("wip.js").write_text("export const wip = 1;\n")   # uncommitted when the plan is approved
    tg.approve_plan(d)
    action = next_action(d)
    check("[CP6-X08] next does not mention merged", action != "" and not mentions_merged(action), action[-300:])
    (d / "tests/cp1.test.js").write_text(mm.CP1_TEST.replace("{marker!r}", repr(str(d / "marker"))))
    r = rx(d, "tests-written", "CP1")
    check("[CP6-X08] tests-written goes ahead", r.returncode == 0 and "fast-forwarded" not in mm.ours(r),
          (r.returncode, mm.ours(r)[-300:]))


base.TESTS += [("CP6-X01", x01_fast_forward_is_flagged), ("CP6-X01b", x01b_fast_forward_at_the_start_of_step_one),
               ("CP6-X01c", x01c_own_commit_is_not_flagged), ("CP6-X02", x02_second_merge_after_a_hand_resolved_one),
               ("CP6-X03", x03_abort_continue_quit_are_allowed_mid_step),
               ("CP6-X04", x04_an_old_merge_commit_is_left_alone), ("CP6-X05", x05_deletion_only_merge_is_flagged),
               ("CP6-X06", x06_a_missing_ignored_include_file_is_not_a_deletion),
               ("CP6-X07", x07_fast_forward_to_a_tip_older_than_the_run),
               ("CP6-X08", x08_a_pull_before_the_run_does_not_flag_step_one)]
