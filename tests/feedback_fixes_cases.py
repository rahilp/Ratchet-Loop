"""
CP7 extra check (CP7-X01), added after adversarial review round 1. It runs as part of tests/test_feedback_fixes.py,
which imports this file just before it starts (the planned file's own lines are frozen, so its cases cannot be
extended in place) and uses its helpers and its case list. It is not named test_*.py so the full suite does not run
its cases twice.

CP7-X01: when a step finished without a commit and no reviewed tree was recorded for it (a run from before Ratchet
recorded one), the refusal from `merged` starts with the undo (git reset --keep and the merge's first parent) in every
phase, then names a step that can actually be taken: at a step review it says to get the review approved and merge once
the next step has started; at the final review (no next step exists) it says to ask for a step of its own through
feedback.

CP7-X02: with commit_per_checkpoint off, a stray untracked file that was in the working tree when the step finished is
refused with the delete advice, and once it is deleted `merged` is accepted (the file is not a lane commit), both while
a step review waits and while the next step is starting; a real unreviewed lane commit is still refused in both.

CP7-X03 (round 3): a path counts as an unreviewed lane change only if some commit since the step's starting commit
touched it. Reviewed work hand-committed and then removed by a lane commit (a file no test imports) is refused, with
commit_per_checkpoint off, while a step review waits and while the next step is starting. With the default setting, a
stray untracked file that was there when the next step started is accepted once deleted. A reset past the step's own
commit is refused.
"""
from __future__ import annotations

import json
import subprocess
import sys

_main = sys.modules.get("__main__")
base = _main if hasattr(_main, "TESTS") and hasattr(_main, "build_nocommit") else __import__("test_feedback_fixes")

check, said = base.check, base.said


_REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
               "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
               "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
               "m.record_chain(Path(sys.argv[2]))\n")


def _legacy(name, phase):
    """A no-commit run waiting at `phase`, whose finished step recorded no reviewed tree, with main merged in."""
    mm = base._mm()
    d = base.build_nocommit(name)
    mm.commit_all(d, "the reviewed work, by hand")
    base.merge_main_noff(d)
    p = d / ".ratchet/runs/f/state.json"
    st = json.loads(p.read_text())
    for e in st["history"]:
        if e.get("event") == "checkpoint_complete":
            e.pop("tree", None)
    st["phase"] = phase
    p.write_text(json.dumps(st))
    # re-baseline the engine's integrity chain with its own record_chain (what accept-state does), as
    # tests/test_brief_guard.py's _force_phase_blocked does, so the direct edit is not read as tampering
    r = subprocess.run([sys.executable, "-c", _REBASELINE, str(mm.tg.ENGINE), str(p)], cwd=d, env=mm.tg.ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    return mm, d


def x01_no_recorded_tree_refusal_names_a_step_that_can_be_taken():
    print("CP7-X01: the no-recorded-tree refusal does not advise a next step at the final review")
    mm, d = _legacy("x01a", "awaiting_checkpoint_review")
    r = mm.rx(d, "merged")
    out = said(r)
    p1a = mm.git(d, "rev-parse", "--short=7", "HEAD^1").stdout.strip()
    check("[CP7-X01] at a step review it is refused and tells the agent to merge once the next step has started",
          r.returncode != 0 and "no reviewed tree was recorded" in out and "once the next step has started" in out,
          (r.returncode, out[-500:]))
    check("[CP7-X01] ... and names the undo command with the merge's first parent, before the review is approved",
          f"git reset --keep {p1a}" in out and out.index(f"git reset --keep {p1a}") < out.index("approved"),
          (p1a, out[-500:]))
    mm, d = _legacy("x01b", "awaiting_final_review")
    p1 = mm.git(d, "rev-parse", "--short=7", "HEAD^1").stdout.strip()
    before = mm.snap(d)
    r = mm.rx(d, "merged")
    out = said(r)
    check("[CP7-X01] at the final review it is refused with the same cause", r.returncode != 0
          and "no reviewed tree was recorded" in out, (r.returncode, out[-500:]))
    check("[CP7-X01] ... without advising a merge once the next step has started",
          "once the next step has started" not in out, out[-500:])
    check("[CP7-X01] ... and tells the agent to ask for a step of its own through feedback",
          "through feedback" in out, out[-500:])
    check("[CP7-X01] ... without also telling it to finish the final review first",
          "finish the final review" not in out, out[-500:])
    check("[CP7-X01] ... and names the undo command with the merge's first parent",
          f"git reset --keep {p1}" in out, (p1, out[-500:]))
    check("[CP7-X01] nothing was changed", mm.snap(d) == before, (before, mm.snap(d)))


base.TESTS += [("CP7-X01", x01_no_recorded_tree_refusal_names_a_step_that_can_be_taken)]


def _stray_project(name, building):
    """A no-commit run whose working tree held an untracked notes.txt when CP1 finished; the reviewed work is
    committed by hand and main is merged in. With `building`, the step review is approved first, so CP2 is starting."""
    mm = base._mm()
    orig = mm.rx

    def rx(d, *a, **k):
        if a and a[0] == "init":
            (d / "notes.txt").write_text("my scratch notes\n")
        return orig(d, *a, **k)

    mm.rx = rx
    try:
        d = base.build_nocommit(name)
    finally:
        mm.rx = orig
    if building:
        assert mm.rx(d, "ask", "human-approve").returncode == 0
        mm.tg.say(d, "yes, go ahead")
        r = mm.rx(d, "human-approve")
        assert r.returncode == 0 and mm.sj(d)["phase"] == "building", said(r)[-400:]
    return mm, d


def _merge_main(mm, d):
    r = mm.git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    assert r.returncode == 0, r.stderr + r.stdout


def x02_a_deleted_stray_file_is_not_a_lane_commit():
    print("CP7-X02: with commit-per-checkpoint off, a deleted untracked file is not read as a lane commit")
    for building in (False, True):
        tag = "while the next step is starting" if building else "while a step review waits"
        mm, d = _stray_project(f"x02{int(building)}", building)
        mm.git(d, "add", "src/f.js", "tests/cp1.test.js")
        assert mm.git(d, "commit", "-q", "-m", "reviewed work").returncode == 0
        _merge_main(mm, d)
        r = mm.rx(d, "merged")
        out = said(r)
        check(f"[CP7-X02] {tag}: the untracked file is refused with the delete advice",
              r.returncode != 0 and "notes.txt" in out and "delete them" in out, (r.returncode, out[-400:]))
        (d / "notes.txt").unlink()
        r = mm.rx(d, "merged")
        out = said(r)
        check(f"[CP7-X02] {tag}: after deleting it merged is accepted, not refused for lane commits",
              r.returncode == 0 and base.UNREVIEWED not in out and len(mm.history(d, "merged_main")) == 1,
              (r.returncode, out[-400:]))
        # a real unreviewed lane commit is still refused in the same setup
        mm, d = _stray_project(f"x02r{int(building)}", building)
        (d / "src/a.js").write_text("export const a = 2;\n")
        (d / "notes.txt").unlink()
        mm.commit_all(d, "the reviewed work together with an edit no step reviewed")
        _merge_main(mm, d)
        r = mm.rx(d, "merged")
        out = said(r)
        line = next((ln for ln in out.splitlines() if base.UNREVIEWED in ln), "")
        check(f"[CP7-X02] {tag}: a real unreviewed lane commit is still refused",
              r.returncode != 0 and "src/a.js" in line, (r.returncode, out[-400:]))


base.TESTS += [("CP7-X02", x02_a_deleted_stray_file_is_not_a_lane_commit)]


def _reviewed_extra_project(name, building):
    """A no-commit run whose CP1 work includes src/g.js (written before `implemented`, so reviewed with the step),
    which no test imports. With `building`, the step review is approved first, so CP2 is starting."""
    mm = base._mm()
    orig = mm.rx

    def rx(d, *a, **k):
        if a and a[0] == "implemented":
            (d / "src/g.js").write_text("export const g = 2;\n")
        return orig(d, *a, **k)

    mm.rx = rx
    try:
        d = base.build_nocommit(name)
    finally:
        mm.rx = orig
    if building:
        assert mm.rx(d, "ask", "human-approve").returncode == 0
        mm.tg.say(d, "yes, go ahead")
        r = mm.rx(d, "human-approve")
        assert r.returncode == 0 and mm.sj(d)["phase"] == "building", said(r)[-400:]
    return mm, d


def x03_a_commit_that_removes_reviewed_work_is_refused_and_litter_is_not():
    print("CP7-X03: only a path some commit touched is a lane change; a reset past the step's commit is refused")
    for building in (False, True):
        tag = "while the next step is starting" if building else "while a step review waits"
        mm, d = _reviewed_extra_project(f"x03a{int(building)}", building)
        mm.git(d, "add", "src/f.js", "src/g.js", "tests/cp1.test.js")
        assert mm.git(d, "commit", "-q", "-m", "reviewed work by hand").returncode == 0
        assert mm.git(d, "rm", "-q", "src/g.js").returncode == 0
        assert mm.git(d, "commit", "-q", "-m", "tidy up").returncode == 0
        _merge_main(mm, d)
        r = mm.rx(d, "merged")
        out = said(r)
        line = next((ln for ln in out.splitlines() if base.UNREVIEWED in ln), "")
        check(f"[CP7-X03] {tag}: a lane commit that removed reviewed work is refused",
              r.returncode != 0 and "g.js" in line and not mm.history(d, "merged_main"), (r.returncode, out[-400:]))
    # the default setting (a commit per step), a stray untracked file there when the next step started
    mm = base._mm()
    d = mm.build("x03b", human_review="each")
    mm.make_main(d)
    (d / "notes.txt").write_text("scratch\n")
    assert mm.rx(d, "ask", "human-approve").returncode == 0
    mm.tg.say(d, "yes, go ahead")
    r = mm.rx(d, "human-approve")
    assert r.returncode == 0 and mm.sj(d)["phase"] == "building", said(r)[-400:]
    mm.merge_main(d)
    r = mm.rx(d, "merged")
    check("[CP7-X03] default setting: the untracked file is refused first", r.returncode != 0
          and not mm.history(d, "merged_main"), (r.returncode, said(r)[-300:]))
    (d / "notes.txt").unlink()
    r = mm.rx(d, "merged")
    out = said(r)
    check("[CP7-X03] default setting: once it is deleted merged is accepted, not refused for lane commits",
          r.returncode == 0 and base.UNREVIEWED not in out and len(mm.history(d, "merged_main")) == 1,
          (r.returncode, out[-400:]))
    # a reset past the step's own commit
    d = mm.build("x03c", human_review="each")
    mm.make_main(d)
    assert mm.rx(d, "ask", "human-approve").returncode == 0
    mm.tg.say(d, "yes, go ahead")
    r = mm.rx(d, "human-approve")
    assert r.returncode == 0 and mm.sj(d)["phase"] == "building", said(r)[-400:]
    assert mm.git(d, "reset", "-q", "--hard", "HEAD~1").returncode == 0
    mm.merge_main(d)
    before = mm.snap(d)
    r = mm.rx(d, "merged")
    check("[CP7-X03] a reset past the step's own commit is refused with nothing changed",
          r.returncode != 0 and mm.snap(d) == before and not mm.history(d, "merged_main"), (r.returncode, said(r)[-400:]))


base.TESTS += [("CP7-X03", x03_a_commit_that_removes_reviewed_work_is_refused_and_litter_is_not)]


"""
CP7-X04 (round 4): a hand-committed file whose name has a space or a non-ASCII letter is refused as an unreviewed lane
commit, with commit_per_checkpoint off and on (the default), while a step review waits and while the next step is
starting. Git's path lists are read NUL-separated, so the name is not split or quoted before it is looked up.
"""

_ODD_NAMES = ("src/my notes.js", "src/café.js")


def _approved(mm, d):
    assert mm.rx(d, "ask", "human-approve").returncode == 0
    mm.tg.say(d, "yes, go ahead")
    r = mm.rx(d, "human-approve")
    assert r.returncode == 0 and mm.sj(d)["phase"] == "building", said(r)[-400:]


def x04_files_with_odd_names_are_not_hidden_from_the_reviewers():
    print("CP7-X04: a hand-committed file with a space or a non-ASCII letter in its name is refused")
    for off in (True, False):
        for building in (False, True):
            for k, odd in enumerate(_ODD_NAMES):
                tag = (f"{'commit_per_checkpoint off' if off else 'default'}, "
                       f"{'next step starting' if building else 'review waiting'}, name {k}")
                nm = f"x04{int(off)}{int(building)}{k}"
                if off:
                    mm = base._mm()
                    d = base.build_nocommit(nm)
                    mm.git(d, "add", "src/f.js", "tests/cp1.test.js")
                else:
                    mm = base._mm()
                    d = mm.build(nm, human_review="each")
                    mm.make_main(d)
                if building:
                    _approved(mm, d)
                (d / odd).write_text("export const odd = 1;\n")
                mm.git(d, "add", "-A")
                assert mm.git(d, "commit", "-q", "-m", "a file by hand").returncode == 0
                _merge_main(mm, d)
                before = mm.snap(d)
                r = mm.rx(d, "merged")
                out = said(r)
                check(f"[CP7-X04] {tag}: refused as an unreviewed lane commit, nothing changed",
                      r.returncode != 0 and base.UNREVIEWED in out and not mm.history(d, "merged_main")
                      and mm.snap(d) == before, (r.returncode, out[-400:]))


base.TESTS += [("CP7-X04", x04_files_with_odd_names_are_not_hidden_from_the_reviewers)]
