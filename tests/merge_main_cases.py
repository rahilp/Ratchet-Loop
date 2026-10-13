"""
CP5 extra checks (CP5-X01 and CP5-X02), added after review round 1. They run as part of tests/test_merge_main.py,
which imports this file just before it starts (the planned file's own lines are frozen, so its cases cannot be
extended in place) and uses its helpers and its case list. It is not named test_*.py so the full suite does not run
its cases twice.

CP5-X01: a lane commit that only deletes a file between the step and the merge is refused like an added one.
CP5-X02: an untracked, non-ignored file outside the run folder blocks `merged`; a git-ignored one does not.
CP5-X03..X05 (round 2): a conflict resolved by hand with new code in the merge commit is not main's or the lane's
reviewed work, so it stays in the next step's review diff; a clean merge keeps the starting tree at the merge;
interpreter litter (__pycache__) does not block `merged`.
CP5-X04, X06..X08 (round 3): "resolved by hand" means different from git's own clean merge of the two parents. Such a
merge is accepted only while a step is starting to build (the base takes main's version of those files); while a step
review or the final review waits it is refused and state.json is left alone. A file both sides edited in different
places that git merged on its own is not hand-resolved, in any phase.
"""
from __future__ import annotations

import sys
from pathlib import Path

_main = sys.modules.get("__main__")
base = _main if hasattr(_main, "TESTS") and hasattr(_main, "build") else __import__("test_merge_main")

check, build, make_main, merge_main = base.check, base.build, base.make_main, base.merge_main
rx, git, snap, ours, known, commit_all = base.rx, base.git, base.snap, base.ours, base.known, base.commit_all


def x01_deletion_only_lane_commit_is_refused():
    print("CP5-X01: a lane commit that only deletes a file before the merge is refused")
    d = build("x01")
    (d / "src/a.js").unlink()
    commit_all(d, "cleanup: remove a.js")
    make_main(d)
    merge_main(d)
    before = snap(d)
    r = rx(d, "merged")
    check("[CP5-X01] merged is refused with the engine's own message", r.returncode != 0 and known(r),
          (r.returncode, ours(r)[-300:]))
    check("[CP5-X01] neither baseline moved and no merge was logged", snap(d) == before, (before, snap(d)))


def x02_untracked_files_block_merged_ignored_ones_do_not():
    print("CP5-X02: an untracked file blocks merged; a git-ignored one does not")
    d = build("x02", human_review="each")   # waiting for the step review: any untracked file counts
    make_main(d)
    merge_main(d)
    (d / "scratch.local").write_text("notes\n")
    before = snap(d)
    r = rx(d, "merged")
    check("[CP5-X02] an untracked, non-ignored file makes merged refuse", r.returncode != 0 and known(r),
          (r.returncode, ours(r)[-300:]))
    check("[CP5-X02] the refusal changed nothing", snap(d) == before, (before, snap(d)))
    (d / ".git/info/exclude").write_text("*.local\n")   # now git ignores it (no commit, so HEAD stays the merge)
    r = rx(d, "merged")
    check("[CP5-X02] a git-ignored file does not block merged", r.returncode == 0 and known(r),
          (r.returncode, ours(r)[-300:]))





RESOLVED = "export const f = () => 1 + 0;\nexport const g = () => 5;\n"   # new logic, written while resolving


def divergent_merge(d):
    """Main forks from the first commit (before the lane's CP1 commit) and adds its own src/f.js, so merging it into
    the lane conflicts; the agent resolves the conflict with new code that still passes CP1's test."""
    first = git(d, "rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
    git(d, "checkout", "-q", "-b", "main", first)
    (d / "src").mkdir(exist_ok=True)
    (d / "src/f.js").write_text("export const f = () => 2;\n")
    (d / "src/m.js").write_text("export const tax = () => 0.2;\n")
    commit_all(d, "M1 from main")
    git(d, "checkout", "-q", "lane")
    r = git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    assert r.returncode != 0 and "src/f.js" in git(d, "diff", "--name-only", "--diff-filter=U").stdout, r.stdout
    (d / "src/f.js").write_text(RESOLVED)
    git(d, "add", "src/f.js")
    r = git(d, "commit", "-qm", "Merge main into lane")
    assert r.returncode == 0, r.stderr + r.stdout
    assert len(git(d, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) == 3


def x03_hand_resolved_conflict_is_reviewed_with_the_next_step():
    print("CP5-X03: a conflict resolved by hand with new code stays in the next step's review")
    d = build("x03")
    divergent_merge(d)
    base.merged_ok(d, "CP5-X03")
    st = base.sj(d)
    got = base.changed_since(d, st["checkpoints"]["CP2"]["base_tree"])
    check("[CP5-X03] the resolved file is in the next step's own changes from the start", got == ["src/f.js"], got)
    base.cp2_tests_written(d, to_complete=True)
    got = base.changed_since(d, base.sj(d)["checkpoints"]["CP2"]["base_tree"])
    check("[CP5-X03] CP2's changes are its two files plus the resolved file, not main's other file",
          got == ["src/cart.js", "src/f.js", "tests/cp2.test.js"], got)
    rnd = base.tg.open_round(d, "CP2")
    honest = sorted(base.tg._default_files_reviewed(d, rnd))
    check("[CP5-X03] an honest default review lists the resolved file", honest == got, honest)
    r = rx(d, "adv-record", "CP2", "--reviews", base.tg.write_review(d, rnd, base.tg.R0(rnd)))
    base.need("[CP5-X03] (setup) the CP2 review is recorded", r.returncode == 0, base.said(r)[-300:])
    r = rx(d, "complete", "CP2")
    check("[CP5-X03] CP2 completes with the resolved file among its changes", r.returncode == 0, base.said(r)[-300:])


def refusal_leaves_state_alone(d, case, what):
    """Runs `merged` on a hand-resolved merge while a review waits: refused, naming the commit to undo it to, and
    state.json is byte for byte what it was."""
    sp = d / ".ratchet/runs/f/state.json"
    before_bytes, before = sp.read_bytes(), snap(d)
    p1 = git(d, "rev-parse", "HEAD^1").stdout.strip()
    r = rx(d, "merged")
    check(f"[{case}] {what}: merged is refused with the engine's own message", r.returncode != 0 and known(r),
          (r.returncode, ours(r)[-300:]))
    check(f"[{case}] {what}: the refusal names the commit to undo the merge to", p1[:7] in ours(r), ours(r)[-300:])
    check(f"[{case}] {what}: state.json is untouched", sp.read_bytes() == before_bytes and snap(d) == before,
          (before, snap(d)))
    return p1


def x04_hand_resolved_conflict_while_a_review_waits():
    print("CP5-X04: a hand-resolved merge is refused while the step review waits; accepted once the next step starts")
    d = build("x04", human_review="each")
    with (d / ".git/info/exclude").open("a") as f:   # the run's records stay out of main's commit, as in real projects
        f.write("/.ratchet/*\n")
    divergent_merge(d)
    p1 = refusal_leaves_state_alone(d, "CP5-X04", "(step review)")
    r = git(d, "reset", "--hard", p1[:7])
    base.need("[CP5-X04] (setup) undoing the merge with the named commit works", r.returncode == 0, r.stderr)
    base.need("[CP5-X04] (setup) asking for the review works", rx(d, "ask", "human-approve").returncode == 0, "")
    base.tg.say(d, "yes, go ahead")
    r = rx(d, "human-approve")
    st = base.sj(d)
    base.need("[CP5-X04] (setup) the user's yes moves the run on to CP2",
              r.returncode == 0 and st["phase"] == "building" and st["current"] == "CP2", base.said(r)[-300:])
    r = git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    base.need("[CP5-X04] (setup) merging again conflicts as before", r.returncode != 0, r.stdout)
    (d / "src/f.js").write_text(RESOLVED)
    git(d, "add", "src/f.js")
    r = git(d, "commit", "-qm", "Merge main into lane")
    base.need("[CP5-X04] (setup) the merge is finished", r.returncode == 0, r.stderr + r.stdout)
    base.merged_ok(d, "CP5-X04")
    got = base.changed_since(d, base.sj(d)["checkpoints"]["CP2"]["base_tree"])
    check("[CP5-X04] once the next step has started the merge is accepted and the resolved file is CP2's own change",
          got == ["src/f.js"], got)


def x06_hand_resolved_conflict_in_building_puts_mains_version_in_the_base():
    print("CP5-X06: building at the start of a step, the base holds main's version of a hand-resolved file")
    d = build("x06")
    divergent_merge(d)
    base.merged_ok(d, "CP5-X06")
    st = base.sj(d)
    b = st["checkpoints"]["CP2"]["base_tree"]
    tree = git(d, "rev-parse", "HEAD^{tree}").stdout.strip()
    check("[CP5-X06] the base is not the merge's tree (the resolved file is held back)", b != tree, b)
    got = git(d, "show", f"{b}:src/f.js").stdout
    check("[CP5-X06] the base holds main's version of the resolved file", got == "export const f = () => 2;\n", got)
    got = git(d, "show", f"{b}:src/m.js").stdout
    check("[CP5-X06] the base holds the merge's version of main's other file",
          got == "export const tax = () => 0.2;\n", got)
    check("[CP5-X06] the run's own base_tree is the same tree", st.get("base_tree") == b, (st.get("base_tree"), b))


def x07_hand_resolved_conflict_at_the_final_review_is_refused():
    print("CP5-X07: a hand-resolved merge is refused while the final review waits")
    d = build("x07")
    base.cp2_tests_written(d, to_complete=True)
    rnd = base.tg.open_round(d, "CP2")
    r = rx(d, "adv-record", "CP2", "--reviews", base.tg.write_review(d, rnd, base.tg.R0(rnd)))
    base.need("[CP5-X07] (setup) the CP2 review is recorded", r.returncode == 0, base.said(r)[-300:])
    r = rx(d, "complete", "CP2")
    st = base.sj(d)
    base.need("[CP5-X07] (setup) CP2 completes and the final review waits",
              r.returncode == 0 and st["phase"] == "awaiting_final_review", (r.returncode, st["phase"], base.said(r)[-300:]))
    divergent_merge(d)
    refusal_leaves_state_alone(d, "CP5-X07", "(final review)")


SHARED_TAIL = "# lane: a note added at the end of the runner\n"


def x08_same_file_different_regions_is_a_clean_merge():
    print("CP5-X08: a file both sides edited in different places, merged by git on its own, is not hand-resolved")
    for phase in ("building", "waiting"):
        # The lane edits the end of tests/py_runner.py inside step 1; main (forked earlier) edits its first line.
        orig_rx = base.rx

        def lane_edit_then_run(dd, *a, _orig=orig_rx):
            if a[:2] == ("tests-written", "CP1"):
                with (dd / "tests/py_runner.py").open("a") as f:
                    f.write(SHARED_TAIL)
            return _orig(dd, *a)

        base.rx = lane_edit_then_run
        try:
            d = build(f"x08{phase[0]}", human_review="each" if phase == "waiting" else "end")
        finally:
            base.rx = orig_rx
        first = git(d, "rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
        git(d, "checkout", "-q", "-b", "main", first)
        pr = d / "tests/py_runner.py"
        pr.write_text(pr.read_text().replace("import sys\n", "import sys  # main\n", 1))
        commit_all(d, "M1 from main")
        git(d, "checkout", "-q", "lane")
        r = git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
        base.need(f"[CP5-X08] ({phase}) (setup) git merges the shared file on its own",
                  r.returncode == 0 and len(git(d, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) == 3,
                  r.stdout + r.stderr)
        merged_text = pr.read_text()
        base.need(f"[CP5-X08] ({phase}) (setup) the file holds both edits",
                  "# main" in merged_text and SHARED_TAIL in merged_text, merged_text)
        base.merged_ok(d, f"CP5-X08 {phase}")
        tree = git(d, "rev-parse", "HEAD^{tree}").stdout.strip()
        st = base.sj(d)
        check(f"[CP5-X08] ({phase}) the run's starting tree is the merge's tree", st.get("base_tree") == tree,
              (st.get("base_tree"), tree))
        if phase == "building":
            check("[CP5-X08] (building) the next step's base is the merge's tree, so it holds none of the lane's "
                  "earlier edits", st["checkpoints"]["CP2"]["base_tree"] == tree and base.changed_since(d, tree) == [],
                  st["checkpoints"]["CP2"]["base_tree"])
        else:
            check("[CP5-X08] (waiting) the run still waits for the review", st["phase"] == "awaiting_checkpoint_review",
                  st["phase"])
            base.need("[CP5-X08] (setup) asking for the review works", rx(d, "ask", "human-approve").returncode == 0, "")
            base.tg.say(d, "yes, go ahead")
            r = rx(d, "human-approve")
            st = base.sj(d)
            base.need("[CP5-X08] (setup) the user's yes moves the run on to CP2",
                      r.returncode == 0 and st["current"] == "CP2", base.said(r)[-300:])
            got = base.changed_since(d, st["checkpoints"]["CP2"]["base_tree"])
            check("[CP5-X08] (waiting) CP2 starts with none of the merged files among its own changes", got == [], got)


def x05_clean_merge_keeps_the_base_and_litter_does_not_block():
    print("CP5-X05: a clean merge keeps the base at the merge; __pycache__ litter does not block merged")
    for phase in ("building", "waiting"):
        d = build(f"x05{phase[0]}", human_review="each" if phase == "waiting" else "end")
        make_main(d)
        merge_main(d)
        (d / "src/__pycache__").mkdir()
        (d / "src/__pycache__/f.cpython-311.pyc").write_bytes(b"\0\1\2")
        r = rx(d, "merged")
        check(f"[CP5-X05] ({phase}) interpreter litter does not block merged", r.returncode == 0 and known(r),
              (r.returncode, ours(r)[-300:]))
        tree = git(d, "rev-parse", "HEAD^{tree}").stdout.strip()
        st = base.sj(d)
        check(f"[CP5-X05] ({phase}) the run's starting tree is the merge's tree", st.get("base_tree") == tree,
              (st.get("base_tree"), tree))
        if phase == "building":
            check("[CP5-X05] (building) the next step's starting tree is the merge's tree",
                  st["checkpoints"]["CP2"]["base_tree"] == tree, st["checkpoints"]["CP2"]["base_tree"])


base.TESTS += [("CP5-X01", x01_deletion_only_lane_commit_is_refused),
               ("CP5-X02", x02_untracked_files_block_merged_ignored_ones_do_not),
               ("CP5-X03", x03_hand_resolved_conflict_is_reviewed_with_the_next_step),
               ("CP5-X04", x04_hand_resolved_conflict_while_a_review_waits),
               ("CP5-X05", x05_clean_merge_keeps_the_base_and_litter_does_not_block),
               ("CP5-X06", x06_hand_resolved_conflict_in_building_puts_mains_version_in_the_base),
               ("CP5-X07", x07_hand_resolved_conflict_at_the_final_review_is_refused),
               ("CP5-X08", x08_same_file_different_regions_is_a_clean_merge)]


# ------------------------------------------------------------------------------------------------ round 4
# CP5-X09..X11 (round 4): a git-ignored file named in fingerprint_include is not a lane commit; a second merge in the
# same step after a hand-resolved one is accepted; the undo the messages suggest is `git reset --keep`, never --hard.

def build_with_include(name, **kw):
    """Like build(), but the project lists a git-ignored file in fingerprint_include from before the plan is
    submitted, so the starting tree of every step is a fingerprint tree that holds that file."""
    orig_rx = base.rx

    def with_include(dd, *a, _orig=orig_rx):
        if a[:1] == ("plan-submit",):
            (dd / ".env.test").write_text("TOKEN=fake\n")
            with (dd / ".git/info/exclude").open("a") as f:
                f.write("/.env.test\n")
            r = _orig(dd, "config", "--set", 'fingerprint_include=[".env.test"]')
            assert r.returncode == 0, r.stderr + r.stdout
        return _orig(dd, *a)

    base.rx = with_include
    try:
        return build(name, **kw)
    finally:
        base.rx = orig_rx


def x09_fingerprint_include_file_is_not_a_lane_commit():
    print("CP5-X09: a git-ignored file named in fingerprint_include does not make merged refuse a clean merge")
    d = build_with_include("x09")
    base.need("[CP5-X09] (setup) git ignores the file", git(d, "check-ignore", "-q", ".env.test").returncode == 0, "")
    make_main(d)
    merge_main(d)
    r = rx(d, "merged")
    check("[CP5-X09] a clean merge at the start of a step is accepted", r.returncode == 0 and known(r),
          (r.returncode, ours(r)[-300:]))
    d = build_with_include("x09b")
    (d / "src/a.js").unlink()
    commit_all(d, "cleanup: remove a.js")
    make_main(d)
    merge_main(d)
    before = snap(d)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-X09] a real unreviewed lane commit is still refused",
          r.returncode != 0 and known(r) and "src/a.js" in out, (r.returncode, out[-300:]))
    check("[CP5-X09] the refusal does not name the ignored file", ".env.test" not in out, out[-300:])
    check("[CP5-X09] the refusal changed nothing", snap(d) == before, (before, snap(d)))


def x10_second_merge_after_a_clean_one_is_accepted():
    print("CP5-X10: a second merge in the same step, after a clean one, is accepted")
    d = build("x10")
    make_main(d)
    merge_main(d)
    base.merged_ok(d, "CP5-X10")
    git(d, "checkout", "-q", "main")
    (d / "src/m2.js").write_text("export const fee = () => 1;\n")
    commit_all(d, "M2 from main")
    git(d, "checkout", "-q", "lane")
    merge_main(d)
    r = rx(d, "merged")
    check("[CP5-X10] the second merge is accepted", r.returncode == 0 and known(r), (r.returncode, ours(r)[-300:]))
    check("[CP5-X10] both merges are in the run log", len(base.history(d, "merged_main")) == 2,
          base.history(d, "merged_main"))


def x11_undo_advice_keeps_uncommitted_edits():
    print("CP5-X11: the undo the messages suggest is git reset --keep, never --hard, and keeps run-file edits")
    d = build("x11", human_review="each")
    divergent_merge(d)
    p1 = git(d, "rev-parse", "HEAD^1").stdout.strip()
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-X11] the refusal names git reset --keep with the first parent's commit",
          "git reset --keep" in out and p1[:7] in out, out[-400:])
    check("[CP5-X11] the refusal does not suggest --hard", "--hard" not in out, out[-400:])
    lp = d / ".ratchet/learnings.md"
    lp.write_text((lp.read_text() if lp.exists() else "") + "- a note added after the merge\n")
    r = git(d, "reset", "--keep", p1[:7])
    check("[CP5-X11] the suggested undo works and the uncommitted learnings edit survives",
          r.returncode == 0 and git(d, "rev-parse", "HEAD").stdout.strip() == p1
          and "a note added after the merge" in lp.read_text(), (r.returncode, r.stderr))
    d = build("x11b")
    make_main(d, brk=True)
    merge_main(d)
    p1 = git(d, "rev-parse", "HEAD^1").stdout.strip()
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-X11] the failed re-check names git reset --keep with the first parent's commit",
          r.returncode != 0 and "git reset --keep" in out and p1[:7] in out, out[-500:])
    check("[CP5-X11] the failed re-check does not suggest --hard or ORIG_HEAD",
          "--hard" not in out and "ORIG_HEAD" not in out, out[-500:])


base.TESTS += [("CP5-X09", x09_fingerprint_include_file_is_not_a_lane_commit),
               ("CP5-X10", x10_second_merge_after_a_clean_one_is_accepted),
               ("CP5-X11", x11_undo_advice_keeps_uncommitted_edits)]


# ------------------------------------------------------------------------------------------------ round 5
# CP5-X12, X13 (round 5): a second merge in the same step after a hand-resolved one is refused (the resolution is
# never folded into the base); the base after a merge still holds the git-ignored fingerprint_include files, so they
# are not the step's own change and its commit does not hold them.

def x12_second_merge_after_a_hand_resolved_one_is_refused():
    print("CP5-X12: a second merge in the same step, after a hand-resolved one, is refused")
    d = build("x12")
    divergent_merge(d)
    base.merged_ok(d, "CP5-X12")
    git(d, "checkout", "-q", "main")
    (d / "src/m2.js").write_text("export const fee = () => 1;\n")
    commit_all(d, "M2 from main")
    git(d, "checkout", "-q", "lane")
    merge_main(d)
    before = snap(d)
    r = rx(d, "merged")
    check("[CP5-X12] the second merge is refused with a known message",
          r.returncode != 0 and known(r), (r.returncode, ours(r)[-300:]))
    check("[CP5-X12] the refusal changed nothing", snap(d) == before, (before, snap(d)))
    got = base.changed_since(d, base.sj(d)["checkpoints"]["CP2"]["base_tree"])
    check("[CP5-X12] the resolved file is still among the step's own changes", "src/f.js" in got, got)


def x13_include_file_stays_out_of_the_steps_changes_after_a_merge():
    print("CP5-X13: a git-ignored fingerprint_include file is not the step's change or commit after a merge")
    d = build_with_include("x13")
    make_main(d)
    merge_main(d)
    base.merged_ok(d, "CP5-X13")
    got = base.changed_since(d, base.sj(d)["checkpoints"]["CP2"]["base_tree"])
    check("[CP5-X13] right after merged the step has no changes of its own", got == [], got)
    base.cp2_tests_written(d, to_complete=True)
    got = base.changed_since(d, base.sj(d)["checkpoints"]["CP2"]["base_tree"])
    check("[CP5-X13] the step's changes are its own two files, not the ignored file",
          got == ["src/cart.js", "tests/cp2.test.js"], got)
    rnd = base.tg.open_round(d, "CP2")
    r = rx(d, "adv-record", "CP2", "--reviews", base.tg.write_review(d, rnd, base.tg.R0(rnd)))
    base.need("[CP5-X13] (setup) the CP2 review is recorded", r.returncode == 0, base.said(r)[-300:])
    r = rx(d, "complete", "CP2")
    base.need("[CP5-X13] (setup) CP2 completes", r.returncode == 0, base.said(r)[-300:])
    files = git(d, "show", "--name-only", "--format=", "HEAD").stdout.split()
    check("[CP5-X13] the step's commit holds only its own files", sorted(files) == ["src/cart.js", "tests/cp2.test.js"],
          files)
    d = build_with_include("x13b")
    divergent_merge(d)
    base.merged_ok(d, "CP5-X13")
    got = base.changed_since(d, base.sj(d)["checkpoints"]["CP2"]["base_tree"])
    check("[CP5-X13] with a hand-resolved file too, only that file is the step's change", got == ["src/f.js"], got)


base.TESTS += [("CP5-X12", x12_second_merge_after_a_hand_resolved_one_is_refused),
               ("CP5-X13", x13_include_file_stays_out_of_the_steps_changes_after_a_merge)]


def build_in_subfolder(name):
    """Like build(), but the Ratchet project is the folder app/ inside a git repository whose root is its parent."""
    top = Path(base._tmp.mkdtemp(prefix=f"ratchet-mm-{name}-"))
    for c in (["init", "-q", "-b", "lane"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(top, *c)
    (top / "app").mkdir()
    real_git, real_mk = base.git, base._tmp.mkdtemp
    base.git = lambda d, *a: real_git(d, *a) if a[0] != "init" else None   # the repository already exists
    base._tmp.mkdtemp = lambda **kw: str(top / "app")
    try:
        return build(name)
    finally:
        base.git, base._tmp.mkdtemp = real_git, real_mk


def x14_project_below_the_git_root_keeps_a_hand_resolved_file_in_review():
    print("CP5-X14: a project in a subfolder of the git repo: the hand-resolved file stays in the next step's changes")
    d = build_in_subfolder("x14")
    divergent_merge(d)
    base.merged_ok(d, "CP5-X14")
    b = base.sj(d)["checkpoints"]["CP2"]["base_tree"]
    got = base.changed_since(d, b)
    check("[CP5-X14] the resolved file is in the next step's own changes", got == ["app/src/f.js"], got)
    names = git(d, "ls-tree", "-r", "--full-tree", "--name-only", b).stdout.split()
    check("[CP5-X14] the base has no entry outside the project folder", names and all(n.startswith("app/") for n in names),
          names)


base.TESTS += [("CP5-X14", x14_project_below_the_git_root_keeps_a_hand_resolved_file_in_review)]


# CP5-X15 (round 7): a project in a subfolder of the git repo that also lists a git-ignored file in fingerprint_include,
# ignored by a rule anchored at the repo root, still gets a clean merge accepted and a real lane commit refused.

def build_in_subfolder_with_include(name):
    orig_rx = base.rx

    def with_include(dd, *a, _orig=orig_rx):
        if a[:1] == ("plan-submit",):
            (dd / ".env.test").write_text("TOKEN=fake\n")
            with (dd.parent / ".git/info/exclude").open("a") as f:
                f.write("/app/.env.test\n")
            r = _orig(dd, "config", "--set", 'fingerprint_include=[".env.test"]')
            assert r.returncode == 0, r.stderr + r.stdout
        return _orig(dd, *a)

    base.rx = with_include
    try:
        return build_in_subfolder(name)
    finally:
        base.rx = orig_rx


def x15_subfolder_project_with_an_anchored_ignored_include_file():
    print("CP5-X15: a project below the git root with an anchored-ignored fingerprint_include file")
    d = build_in_subfolder_with_include("x15")
    base.need("[CP5-X15] (setup) git ignores the file", git(d, "check-ignore", "-q", ".env.test").returncode == 0, "")
    make_main(d)
    merge_main(d)
    r = rx(d, "merged")
    check("[CP5-X15] a clean merge at the start of a step is accepted", r.returncode == 0 and known(r),
          (r.returncode, ours(r)[-300:]))
    d = build_in_subfolder_with_include("x15b")
    (d / "src/a.js").unlink()
    commit_all(d, "cleanup: remove a.js")
    make_main(d)
    merge_main(d)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-X15] a real unreviewed lane commit is still refused",
          r.returncode != 0 and known(r) and "src/a.js" in out and ".env.test" not in out, (r.returncode, out[-300:]))


base.TESTS += [("CP5-X15", x15_subfolder_project_with_an_anchored_ignored_include_file)]
