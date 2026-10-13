#!/usr/bin/env python3
"""
CP2 safety tests for `integrate <lane>` (round 1 of the adversarial review).   python3 tests/test_integrate_safety.py

The merge commit is made in a scratch copy of main, and main only moves by a fast-forward after the suite passed. These
cases check that the scratch copy behaves like main (its local git settings and hooks), that nothing in main may change
between the last check and the fast-forward, that dependency folders linking back into main are refused, and that every
refusal is recorded. They reuse the throwaway projects and helpers of tests/test_integrate_merge.py and drive the real
ratchet.py as a subprocess. Case ids are [CP2-X01] and up.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import traceback
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_integrate_merge as tim  # noqa: E402
import trust_helpers as th  # noqa: E402

check, need, Stop, g = tim.check, tim.need, tim.Stop, tim.g
SUITE2 = r'''
import json, os, subprocess, sys
BASE, MAIN = %(base)r, %(main)r
mode = open(os.path.join(BASE, "ctl", "mode2")).read().strip()
with open(os.path.join(BASE, "ctl", "log2.jsonl"), "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "mode": mode}) + "\n")
if mode == "building":    # main's own run starts a step while the suite runs
    p = os.path.join(MAIN, ".ratchet/runs/main-run/state.json")
    st = json.load(open(p))
    st["phase"], st["current"] = "building", "CP1"
    st["checkpoints"] = {"CP1": {"status": "in_progress", "step": "implement"}}
    json.dump(st, open(p, "w"))
if mode == "edit-other":  # a tracked file the lane does not touch is edited
    open(os.path.join(MAIN, "src", "b.js"), "a").write("// dev edit\n")
if mode == "switch-branch":
    subprocess.run(["git", "checkout", "-q", "-b", "hotfix"], cwd=MAIN, check=True)
if mode == "untracked-lane-file":
    open(os.path.join(MAIN, "src", "lane.js"), "w").write("// dev scratch copy\n")
if mode == "edit-a":
    p = os.path.join(MAIN, "src", "a.js")
    text = open(p).read()
    open(p, "w").write(text.replace("line9\n", "line9 dev edit\n"))
if mode == "edit-tracked":  # a suite that rewrites a tracked file of the copy it runs in (a generator, a formatter)
    open(os.path.join(os.getcwd(), "src", "b.js"), "a").write("// generated before the tests\n")
if mode == "fail":
    sys.exit(3)
if mode == "hang":  # waits until the test writes ctl/release
    import time
    while not os.path.exists(os.path.join(BASE, "ctl", "release")):
        time.sleep(0.2)
sys.exit(0)
'''


def mk(**kw):
    """A project from test_integrate_merge.build whose suite is a script of this file (modes in ctl/mode2). The suite
    setting is committed on main, so main is clean when the case starts."""
    w = tim.build(**kw)
    (w.base / "suite2.py").write_text(SUITE2 % {"base": str(w.base), "main": str(w.main)})
    cfg = tim.jread(w.main / ".ratchet/config.json")
    cfg["regression_command"] = f"python3 {w.base / 'suite2.py'}"
    tim.jwrite(w.main / ".ratchet/config.json", cfg)
    th.git(w.main, "commit", "-qm", "suite for the safety cases", "--", ".ratchet/config.json")  # only the config: a
    # dirty learnings.md (dirty_learnings=True) must stay uncommitted
    mode2(w, "pass")
    return w


def mode2(w, m):
    (w.ctl / "mode2").write_text(m)


def runs2(w):
    p = w.ctl / "log2.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def last_record(w):
    lines = w.record_lines()
    return lines[-1] if lines else {}


def hook(path, body):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("#!/bin/sh\n" + body)
    Path(path).chmod(Path(path).stat().st_mode | stat.S_IXUSR)


def x01_scratch_copy_acts_like_main():
    """[CP2-X01] The merge commit is made with main's local identity, hooks and merge drivers, like a merge in main."""
    cid = "CP2-X01"
    w = mk()
    marker = w.base / "commit-msg-ran"
    other = w.base / "post-checkout-ran"
    hook(w.main / ".git/hooks/commit-msg", f"printf x >> '{marker}'\n")
    hook(w.main / ".git/hooks/post-checkout", f"printf x >> '{other}'\n")
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    tim.merged_ok(w, r, cid, "main has its own hooks in .git/hooks", old)
    check(f"[{cid}] main's commit-msg hook ran on the merge commit", marker.exists(), tim.said(r)[:300])
    check(f"[{cid}] no checkout hook ran for the scratch copy's own checkout", not other.exists())
    check(f"[{cid}] the merge commit carries main's local identity (not a made-up one)",
          g(w.main, "log", "-1", "--format=%an <%ae> / %cn <%ce>") == "t <t@t> / t <t@t>",
          g(w.main, "log", "-1", "--format=%an <%ae> / %cn <%ce>"))
    # a hooks folder set only in main's local config (how husky and similar tools install themselves), given as a
    # relative path, and a hook that rejects the commit: the merge is rolled back
    w2 = mk()
    hdir = w2.main / "myhooks"
    marker2 = w2.base / "path-hook-ran"
    default2 = w2.base / "default-hook-ran"
    hook(hdir / "commit-msg", f"printf x >> '{marker2}'\n")
    hook(w2.main / ".git/hooks/commit-msg", f"printf x >> '{default2}'\n")
    th.git(w2.main, "config", "core.hooksPath", "myhooks")
    old2 = g(w2.main, "rev-parse", "HEAD")
    r2 = w2.integrate()
    tim.merged_ok(w2, r2, cid, "main sets core.hooksPath locally (relative)", old2)
    check(f"[{cid}] the hook in main's core.hooksPath ran, not the default one", marker2.exists() and not default2.exists(),
          (marker2.exists(), default2.exists()))
    w3 = mk()
    hook(w3.main / ".git/hooks/commit-msg", "echo REJECTED-BY-HOOK >&2\nexit 1\n")
    before = tim.snap(w3)
    r3 = w3.integrate()
    t3 = tim.failed_run(r3, cid, "a commit-msg hook in main rejects the merge commit", "rejected-by-hook")
    tim.unchanged(w3, before, cid, "main is exactly as it was")
    check(f"[{cid}] the attempt is recorded as rolled back, suite not run",
          last_record(w3).get("result") == "rolled-back" and last_record(w3).get("suite") == "not-run", last_record(w3))
    # a merge driver defined only in main's local config and named by a committed .gitattributes
    w4 = mk()
    (w4.main / ".gitattributes").write_text("src/a.js merge=keepours\n")
    th.git(w4.main, "add", ".gitattributes")
    th.git(w4.main, "commit", "-qm", "attributes")
    tim.add_lane(w4, "lane-y", {"src/a.js": tim.A_BASE.replace("line1\n", "line1 lane\n")})
    (w4.main / "src/a.js").write_text(tim.A_BASE.replace("line1\n", "line1 main\n"))
    th.git(w4.main, "commit", "-qam", "main edits line 1")
    th.git(w4.main, "config", "merge.keepours.driver", "true")
    r4 = w4.integrate("lane-y")
    need(f"[{cid}] a merge driver from main's local config is used for the scratch merge",
         tim.known(r4) and r4.returncode == 0, tim.said(r4)[:400])
    check(f"[{cid}] the driver's result (main's version) landed",
          (w4.main / "src/a.js").read_text() == tim.A_BASE.replace("line1\n", "line1 main\n"))


def x02_main_is_rechecked_right_before_it_moves():
    """[CP2-X02] What changes in main while the suite runs (run phase, branch, project files) stops the fast-forward."""
    cid = "CP2-X02"
    w = mk()
    r0 = th.rx(w.main, w.env, "init", "main work", "--slug", "main-run")
    need(f"[{cid}] main gets a run of its own", r0.returncode == 0, r0.stdout + r0.stderr)
    tim.set_phase(w.main, "main-run", "done")
    need(f"[{cid}] main is clean with that run", g(w.main, "status", "--porcelain") == "", g(w.main, "status", "--porcelain"))
    old = g(w.main, "rev-parse", "HEAD")
    mode2(w, "building")
    before = tim.snap(w)
    r = w.integrate()
    tim.failed_run(r, cid, "main's run started a step while the suite ran", "middle of a step", "again")
    tim.unchanged(w, before, cid, "main did not move")
    check(f"[{cid}] it is recorded as rolled back with the suite passed",
          last_record(w).get("result") == "rolled-back" and last_record(w).get("suite") == "passed", last_record(w))
    tim.no_scratch(w, cid)
    # a tracked file the lane does not touch is edited while the suite runs
    w = mk()
    mode2(w, "edit-other")
    before = tim.snap(w)
    r = w.integrate()
    tim.failed_run(r, cid, "a project file was edited in main during the suite", "commit", ("stash",), "again")
    after = tim.snap(w)
    check(f"[{cid}] main did not move (tip, branch, refs, stash, worktrees)",
          all(after[k] == before[k] for k in ("head", "branch", "refs", "stash", "merge_head", "worktrees")),
          tim.diff_keys(before, after))
    check(f"[{cid}] the edit is still there, byte for byte, uncommitted",
          (w.main / "src/b.js").read_text() == tim.B_BASE + "// dev edit\n" and " M src/b.js" in tim.porcelain_lines(w.main),
          tim.porcelain_lines(w.main))
    tim.no_scratch(w, cid)
    # main's checkout is switched to a new branch at the same commit
    w = mk()
    mode2(w, "switch-branch")
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    tim.failed_run(r, cid, "main was switched to another branch during the suite", "hotfix", "again")
    check(f"[{cid}] neither branch moved", g(w.main, "rev-parse", "main") == old and g(w.main, "rev-parse", "hotfix") == old,
          (g(w.main, "rev-parse", "main"), g(w.main, "rev-parse", "hotfix")))
    check(f"[{cid}] main is still on the branch it was switched to", g(w.main, "rev-parse", "--abbrev-ref", "HEAD") == "hotfix")
    tim.no_scratch(w, cid)
    # the fast-forward happens while main's state lock is held
    w = mk()
    seen = w.base / "lock-state"
    hook(w.main / ".git/hooks/post-merge",
         f"if [ -e .ratchet/state.lock ]; then printf held > '{seen}'; else printf free > '{seen}'; fi\n")
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    tim.merged_ok(w, r, cid, "a plain integrate", old)
    check(f"[{cid}] main's state lock was held during the fast-forward", seen.exists() and seen.read_text() == "held",
          seen.read_text() if seen.exists() else None)
    check(f"[{cid}] and it is released afterwards", not (w.main / ".ratchet/state.lock").exists())


def x03_autostash_setting_cannot_hide_an_edit():
    """[CP2-X03] A user's merge.autoStash=true never turns an edit made in main during the suite into conflict markers."""
    cid = "CP2-X03"
    w = mk(lane_edits_a=True)
    Path(w.env["HOME"], ".gitconfig").write_text("[merge]\n\tautoStash = true\n")
    mode2(w, "edit-a")
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    tim.failed_run(r, cid, "src/a.js was edited during the suite", "again")
    check(f"[{cid}] main's tip is unchanged", g(w.main, "rev-parse", "HEAD") == old)
    check(f"[{cid}] src/a.js holds exactly the edit, no conflict markers",
          (w.main / "src/a.js").read_text() == tim.A_BASE.replace("line9\n", "line9 dev edit\n"))
    check(f"[{cid}] nothing was parked in the stash and the edit is still uncommitted",
          g(w.main, "stash", "list") == "" and " M src/a.js" in tim.porcelain_lines(w.main), tim.porcelain_lines(w.main))


def x04_dependency_folders_linking_into_main_are_refused():
    """[CP2-X04] Dependency folders that link back into main make the suite test main's old code: refused, plainly."""
    cid = "CP2-X04"
    # a workspace link in node_modules that resolves into main's own packages folder
    w = mk(node_modules=True)
    (w.main / "packages/a").mkdir(parents=True)
    (w.main / "packages/a/index.js").write_text("module.exports = 1;\n")
    (w.main / "node_modules/@org").mkdir()
    os.symlink("../../packages/a", w.main / "node_modules/@org/a")
    before = tim.snap(w)
    r = w.integrate()
    t = tim.failed_run(r, cid, "node_modules links to a workspace package inside main", "link", "main", "by hand")
    check(f"[{cid}] it names the link", "@org/a" in t, t[:300])
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the suite never ran", runs2(w) == [])
    check(f"[{cid}] refused and recorded, suite not run",
          last_record(w).get("result") == "refused" and last_record(w).get("suite") == "not-run", last_record(w))
    # a Python editable install: a .pth file in the virtual environment naming main's source folder
    w = mk()
    site = w.main / ".venv/lib/python3.11/site-packages"
    site.mkdir(parents=True)
    (site / "__editable__.pkg.pth").write_text(str(w.main / "src") + "\n")
    r = w.integrate()
    t = tim.failed_run(r, cid, "an editable install in .venv names main's source folder", "link", "main")
    check(f"[{cid}] it names the file", "__editable__.pkg.pth" in t, t[:300])
    check(f"[{cid}] the suite never ran", runs2(w) == [])
    # an editable install recorded in direct_url.json
    w = mk()
    di = w.main / ".venv/lib/python3.11/site-packages/pkg-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "direct_url.json").write_text(json.dumps({"url": "file://" + str(w.main), "dir_info": {"editable": True}}))
    r = w.integrate()
    tim.failed_run(r, cid, "an editable direct_url.json points at main", "link", "main")
    # controls: links that stay inside the dependency folder, and a .pth naming a path outside main, do not refuse
    w = mk(node_modules=True)
    (w.main / "node_modules/pkg").mkdir()
    (w.main / "node_modules/pkg/cli.js").write_text("x\n")
    os.makedirs(w.main / "node_modules/.bin")
    os.symlink("../pkg/cli.js", w.main / "node_modules/.bin/cli")
    site = w.main / ".venv/lib/python3.11/site-packages"
    site.mkdir(parents=True)
    (site / "other.pth").write_text(str(w.base / "elsewhere") + "\n")
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    tim.merged_ok(w, r, cid, "links inside node_modules and a .pth outside main", old)
    check(f"[{cid}] the suite ran once", len(runs2(w)) == 1, runs2(w))


def x05_every_refusal_is_recorded():
    """[CP2-X05] An unknown lane and a folder that is not a worktree each leave exactly one refused record line."""
    cid = "CP2-X05"
    w = mk()
    r = w.integrate("no-such-lane")
    tim.failed_run(r, cid, "an unknown lane name", "no-such-lane")
    lines = w.record_lines()
    check(f"[{cid}] exactly one record line: refused, the requested name, no run, not run, no commits",
          len(lines) == 1 and lines[0].get("result") == "refused" and lines[0].get("lane") == "no-such-lane"
          and lines[0].get("run") is None and lines[0].get("suite") == "not-run" and lines[0].get("commits") == [], lines)
    check(f"[{cid}] the record line has the documented fields", lines and set(lines[0]) == tim.RECORD_FIELDS, lines)
    folder = w.base / "plain-folder"
    folder.mkdir()
    r = w.integrate(str(folder))
    tim.failed_run(r, cid, "a folder that is not a worktree", "not a git worktree")
    lines = w.record_lines()
    check(f"[{cid}] a second line, refused, naming the folder", len(lines) == 2 and lines[1].get("result") == "refused"
          and lines[1].get("lane") == str(folder), lines)
    r = w.integrate("no-such-lane", "--dry-run")
    check(f"[{cid}] a dry run still writes no record line", len(w.record_lines()) == 2 and r.returncode != 0)


def x06_untracked_file_main_would_overwrite():
    """[CP2-X06] An untracked file on main at a path the lane adds gives a plain rollback, before or after the suite."""
    cid = "CP2-X06"
    w = mk()
    (w.main / "src/lane.js").write_text("// my own copy\n")
    before = tim.snap(w)
    r = w.integrate()
    t = tim.failed_run(r, cid, "main has an untracked src/lane.js the lane also adds", "src/lane.js", ("move", "delete"),
                       "again")
    check(f"[{cid}] it does not tell the developer to commit or stash tracked changes", "uncommitted changes" not in t, t[:300])
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the file is untouched", (w.main / "src/lane.js").read_text() == "// my own copy\n")
    check(f"[{cid}] the suite never ran (no wasted run)", runs2(w) == [], runs2(w))
    check(f"[{cid}] rolled back, suite not run", last_record(w).get("result") == "rolled-back"
          and last_record(w).get("suite") == "not-run", last_record(w))
    # the file shows up while the suite runs
    w = mk()
    mode2(w, "untracked-lane-file")
    before = tim.snap(w)
    r = w.integrate()
    tim.failed_run(r, cid, "an untracked src/lane.js appeared in main during the suite", "src/lane.js", ("move", "delete"))
    after = tim.snap(w)
    check(f"[{cid}] main did not move (tip, branch, refs, stash, worktrees)",
          all(after[k] == before[k] for k in ("head", "branch", "refs", "stash", "merge_head", "worktrees")),
          tim.diff_keys(before, after))
    check(f"[{cid}] the file is untouched and still untracked", (w.main / "src/lane.js").read_text() == "// dev scratch copy\n"
          and "?? src/lane.js" in tim.porcelain_lines(w.main), tim.porcelain_lines(w.main))
    check(f"[{cid}] rolled back with the suite passed", last_record(w).get("suite") == "passed", last_record(w))


def x07_record_holds_full_hashes():
    """[CP2-X07] A merged record names the merge commit and the lane's commits by full hashes."""
    cid = "CP2-X07"
    w = mk()
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    new = tim.merged_ok(w, r, cid, "a plain integrate", old)
    rec = last_record(w)
    check(f"[{cid}] commits is the merge commit, then the lane's commit, all full 40-character hashes",
          rec.get("commits") == [new, tip] and all(len(x) == 40 for x in rec.get("commits", [])), rec)


def x08_background_warning_is_right_for_integrate():
    """[CP2-X08] integrate warns about a foreground run, and stays quiet when the host noted it runs in the background."""
    cid = "CP2-X08"
    w = mk()
    r = w.integrate()
    check(f"[{cid}] with no note from the host, the warning is printed", "WARNING" in r.stderr and "run_in_background" in r.stderr,
          r.stderr[:300])
    w = mk()
    cmd = f"RATCHET_ROLE=director python3 {th.ENGINE} integrate lane-x"
    payload = {"cwd": str(w.main), "session_id": "s-director", "tool_name": "Bash",
               "tool_input": {"command": cmd, "run_in_background": True, "timeout": 120000}}
    gr = th.rx(w.main, w.denv, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))
    need(f"[{cid}] the guard lets the director's integrate call through", gr.returncode == 0, (gr.stdout, gr.stderr))
    r = subprocess.run([sys.executable, str(th.ENGINE), "integrate", "lane-x"], cwd=w.main, capture_output=True, text=True,
                       env=w.denv, timeout=150)
    need(f"[{cid}] the merge succeeds", r.returncode == 0, tim.said(r)[:300])
    check(f"[{cid}] no warning when the host noted the call runs in the background", "WARNING" not in r.stderr, r.stderr[:300])
    w = mk()
    payload["tool_input"]["run_in_background"] = False
    th.rx(w.main, w.denv, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))
    r = subprocess.run([sys.executable, str(th.ENGINE), "integrate", "lane-x"], cwd=w.main, capture_output=True, text=True,
                       env=w.denv, timeout=150)
    check(f"[{cid}] the warning is printed when the host noted a foreground call", "WARNING" in r.stderr, r.stderr[:300])


def x09_uncommitted_shared_file_the_merge_changes():
    """[CP2-X09] An uncommitted learnings.md on main that the lane also changes: rolled back before the suite, bytes kept."""
    cid = "CP2-X09"
    w = mk(dirty_learnings=True)
    tim.add_lane(w, "lane-y", {".ratchet/learnings.md": "# Ratchet learnings\n\n- (2026-10-01) main learning one\n"
                               "- (2026-10-05) a lane note\n"})
    mine = (w.main / ".ratchet/learnings.md").read_bytes()
    before = tim.snap(w)
    r = w.integrate("lane-y")
    tim.failed_run(r, cid, "the merge changes learnings.md, which is dirty on main", "learnings.md", "again")
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] learnings.md holds exactly the developer's bytes", (w.main / ".ratchet/learnings.md").read_bytes() == mine)
    check(f"[{cid}] the suite never ran", runs2(w) == [], runs2(w))
    check(f"[{cid}] rolled back, suite not run", last_record(w).get("result") == "rolled-back"
          and last_record(w).get("suite") == "not-run", last_record(w))


def x10_a_git_timeout_is_a_plain_rollback():
    """[CP2-X10] A git command that times out in the scratch copy is recorded as a rollback with a plain message."""
    cid = "CP2-X10"
    spec = importlib.util.spec_from_file_location("ratchet_engine_x10", str(th.ENGINE))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    w = mk()
    info = {"lane": "lane-x", "run": None, "commits": []}
    seen = []

    def finish(result, suite, msg, detail=None, commits=None):
        seen.append((result, suite, msg))
        raise SystemExit(1)
    f = eng._integrate_check(w.main, "lane-x", info, lambda m: finish("refused", "not-run", m))
    info["prefix"] = "ratchet-integrate-x10-"
    real = eng.subprocess.run

    def slow(args, *a, **kw):
        if isinstance(args, list) and "clone" in args:
            raise subprocess.TimeoutExpired(args, 120)
        return real(args, *a, **kw)
    eng.subprocess.run = slow
    try:
        try:
            eng._integrate_merge(w.main, f, info, finish)
        except SystemExit:
            pass
    finally:
        eng.subprocess.run = real
    check(f"[{cid}] it ends as rolled back, suite not run, with a plain message about the timeout",
          len(seen) == 1 and seen[0][0] == "rolled-back" and seen[0][1] == "not-run" and "timed out" in seen[0][2]
          and "run integrate again" in seen[0][2], seen)
    left = [p for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("ratchet-integrate-x10-")]
    check(f"[{cid}] no scratch folder is left behind", not left, left)


def x11_main_run_between_steps_starts_from_the_merge():
    """[CP2-X11] Main's own run building at step 'tests' (between steps) has its step's starting tree and commit moved
    to the merge, like `merged` does for a lane; a run waiting for a review is left alone."""
    cid = "CP2-X11"
    w = mk()
    r0 = th.rx(w.main, w.env, "init", "main work", "--slug", "main-run")
    need(f"[{cid}] main gets a run of its own", r0.returncode == 0, r0.stdout + r0.stderr)
    p = w.main / ".ratchet/runs/main-run/state.json"
    st = tim.jread(p)
    st["phase"], st["current"] = "building", "CP1"
    st["checkpoints"] = {"CP1": {"status": "in_progress", "step": "tests", "base_tree": "0" * 40, "base_head": "1" * 40}}
    tim.jwrite(p, st)
    need(f"[{cid}] main is clean with that run", g(w.main, "status", "--porcelain") == "", g(w.main, "status", "--porcelain"))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    new = tim.merged_ok(w, r, cid, "main's run is building at step tests with a clean tree", old)
    st = tim.jread(p)
    cp = st["checkpoints"]["CP1"]
    check(f"[{cid}] the step's starting commit is the merge commit", cp.get("base_head") == new, cp)
    check(f"[{cid}] the step's starting tree moved (and the run's base tree with it)",
          cp.get("base_tree") not in ("0" * 40, None) and st.get("base_tree") == cp.get("base_tree"), (cp, st.get("base_tree")))
    check(f"[{cid}] the step is still building at 'tests'", st.get("phase") == "building" and cp.get("step") == "tests", cp)
    ev = [e for e in st.get("history", []) if e.get("event") == "integrated_lane"]
    check(f"[{cid}] an integrated_lane event names the merge commit, the lane and its run",
          len(ev) == 1 and ev[0].get("commit") == new and ev[0].get("lane") == "lane-x" and ev[0].get("run") == "lane-x-run", ev)
    # a run waiting for a review: nothing in its state is written
    w = mk()
    r0 = th.rx(w.main, w.env, "init", "main work", "--slug", "main-run")
    need(f"[{cid}] main gets a run of its own", r0.returncode == 0, r0.stdout + r0.stderr)
    p = w.main / ".ratchet/runs/main-run/state.json"
    tim.set_phase(w.main, "main-run", "awaiting_checkpoint_review")
    before = p.read_bytes()
    old = g(w.main, "rev-parse", "HEAD")
    tim.merged_ok(w, w.integrate(), cid, "main's run waits for a review", old)
    check(f"[{cid}] a waiting run's state is untouched, byte for byte", p.read_bytes() == before)


def engine():
    """The engine loaded in-process (for cases that make a git call time out on purpose)."""
    spec = importlib.util.spec_from_file_location("ratchet_engine_safety", str(th.ENGINE))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    return eng


def main_run(w, phase, step="tests", **extra):
    """Give main a run of its own in `phase`, at CP1 step `step`, with stand-in base_tree/base_head and `extra` keys."""
    r0 = th.rx(w.main, w.env, "init", "main work", "--slug", "main-run")
    need("main gets a run of its own", r0.returncode == 0, r0.stdout + r0.stderr)
    p = w.main / ".ratchet/runs/main-run/state.json"
    st = tim.jread(p)
    st["phase"], st["current"] = phase, "CP1"
    st["checkpoints"] = {"CP1": {"status": "in_progress", "step": step, "base_tree": "0" * 40, "base_head": "1" * 40}}
    st.update(extra)
    tim.jwrite(p, st)
    return p


def x12_main_run_paused_mid_step_is_refused():
    """[CP2-X12] Main's own run paused from building (or blocked) is refused like a mid-step run; paused from a
    waiting phase is not."""
    cid = "CP2-X12"
    w = mk()
    p = main_run(w, "paused", paused_from="building")
    before = tim.snap(w)
    r = w.integrate()
    tim.failed_run(r, cid, "main's run is paused from building", "paused", "resume", "again")
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the suite never ran", runs2(w) == [], runs2(w))
    check(f"[{cid}] refused and recorded", last_record(w).get("result") == "refused", last_record(w))
    st = tim.jread(p)
    check(f"[{cid}] the paused run's starting commit is untouched", st["checkpoints"]["CP1"].get("base_head") == "1" * 40, st)
    w = mk()
    main_run(w, "paused", paused_from="blocked")
    tim.failed_run(w.integrate(), cid, "main's run is paused from blocked", "paused", "resume")
    w = mk()
    p = main_run(w, "paused", paused_from="awaiting_checkpoint_review")
    before = p.read_bytes()
    old = g(w.main, "rev-parse", "HEAD")
    tim.merged_ok(w, w.integrate(), cid, "main's run is paused from a waiting phase", old)
    check(f"[{cid}] that run's state is untouched, byte for byte", p.read_bytes() == before)


def x13_project_below_the_repository_top_is_refused():
    """[CP2-X13] A Ratchet project in a subfolder of its git repository is refused plainly (a known limit) and
    recorded, for the real run and the dry run alike."""
    cid = "CP2-X13"
    w = mk()
    outer = w.base / "outer"
    app = outer / "app"
    (app / ".ratchet").mkdir(parents=True)
    th.git(outer, "init", "-q", "-b", "main")
    th.git(outer, "config", "user.email", "t@t")
    th.git(outer, "config", "user.name", "t")
    (app / ".ratchet/config.json").write_bytes((w.main / ".ratchet/config.json").read_bytes())
    (app / "a.txt").write_text("a\n")
    th.git(outer, "add", "-A")
    th.git(outer, "commit", "-qm", "project in a subfolder")
    argv = [sys.executable, str(th.ENGINE), "--root", str(app), "integrate", "lane-x"]
    r = subprocess.run(argv, cwd=app, capture_output=True, text=True, env=w.denv, timeout=150)
    t = tim.failed_run(r, cid, "the project sits at app/ in its repository", "top of its git repository", "app/",
                       "by hand")
    rec = app / ".ratchet/integrations.jsonl"
    lines = [json.loads(x) for x in rec.read_text().splitlines() if x.strip()] if rec.exists() else []
    check(f"[{cid}] refused and recorded in the project's own folder", len(lines) == 1 and lines[0].get("result") == "refused"
          and lines[0].get("commits") == [], lines)
    check(f"[{cid}] the repository is untouched", g(outer, "status", "--porcelain") == "" or
          g(outer, "status", "--porcelain").strip() == "?? app/.ratchet/integrations.jsonl", g(outer, "status", "--porcelain"))
    r = subprocess.run(argv + ["--dry-run"], cwd=app, capture_output=True, text=True, env=w.denv, timeout=150)
    tim.failed_run(r, cid, "the dry run says the same", "top of its git repository", "by hand")
    check(f"[{cid}] the dry run wrote no record line", len(rec.read_text().splitlines()) == 1)


def x14_linked_dependencies_say_merge_by_hand():
    """[CP2-X14] The linked-dependency refusal says integrate cannot test this layout yet and to merge by hand; it
    never tells the developer to reinstall."""
    cid = "CP2-X14"
    w = mk(node_modules=True)
    (w.main / "packages/a").mkdir(parents=True)
    (w.main / "packages/a/index.js").write_text("module.exports = 1;\n")
    (w.main / "node_modules/@org").mkdir()
    os.symlink("../../packages/a", w.main / "node_modules/@org/a")
    r = w.integrate()
    t = tim.failed_run(r, cid, "a workspace link in node_modules", "cannot test this project layout yet", "by hand",
                       "workspace links or an editable install")
    msg = t.split("ratchet ✗")[-1]  # the refusal line itself (the hooks note on stderr mentions installing hooks)
    check(f"[{cid}] no reinstall advice", "non-editable" not in msg and "install them" not in msg, msg[:400])
    check(f"[{cid}] refused and recorded", last_record(w).get("result") == "refused", last_record(w))


def x15_ignored_file_on_main_is_never_overwritten():
    """[CP2-X15] An ignored file on main at a path the lane starts tracking is kept byte for byte: the attempt is
    rolled back before the suite, and the fast-forward itself would refuse to overwrite it."""
    cid = "CP2-X15"
    w = mk()
    (w.main / ".gitignore").write_text("local.cfg\n")
    th.git(w.main, "add", ".gitignore")
    th.git(w.main, "commit", "-qm", "ignore local.cfg")
    lane = tim.add_lane(w, "lane-y", {"src/y.js": "// y\n"})
    (lane / "local.cfg").write_text("lane tracks it now\n")
    th.git(lane, "add", "-f", "local.cfg")
    th.git(lane, "commit", "-qm", "track local.cfg")
    (w.main / "local.cfg").write_text("the developer's own local settings\n")
    need(f"[{cid}] main's local.cfg is ignored, not untracked", g(w.main, "status", "--porcelain") == "",
         g(w.main, "status", "--porcelain"))
    before = tim.snap(w)
    r = w.integrate("lane-y")
    t = tim.failed_run(r, cid, "main has an ignored local.cfg the lane starts tracking", "local.cfg", ("move", "delete"),
                       "again")
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the ignored file holds the developer's bytes",
          (w.main / "local.cfg").read_text() == "the developer's own local settings\n")
    check(f"[{cid}] the suite never ran", runs2(w) == [], runs2(w))
    check(f"[{cid}] rolled back, suite not run, the lane's tip recorded",
          last_record(w).get("result") == "rolled-back" and last_record(w).get("suite") == "not-run"
          and last_record(w).get("commits") == [g(lane, "rev-parse", "HEAD")], last_record(w))
    # the fast-forward's own backstop: the file appears only while the suite runs, so the pre-suite check misses it
    w = mk()
    (w.main / ".gitignore").write_text("local.cfg\n")
    th.git(w.main, "add", ".gitignore")
    th.git(w.main, "commit", "-qm", "ignore local.cfg")
    lane = tim.add_lane(w, "lane-y", {"src/y.js": "// y\n"})
    (lane / "local.cfg").write_text("lane tracks it now\n")
    th.git(lane, "add", "-f", "local.cfg")
    th.git(lane, "commit", "-qm", "track local.cfg")
    (w.base / "suite2.py").write_text((w.base / "suite2.py").read_text().replace(
        'if mode == "fail":', 'if mode == "late-ignored":\n    open(os.path.join(MAIN, "local.cfg"), "w").write("late local bytes\\n")\nif mode == "fail":'))
    mode2(w, "late-ignored")
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate("lane-y")
    tim.failed_run(r, cid, "an ignored local.cfg appeared during the suite", "local.cfg", ("move", "delete"))
    check(f"[{cid}] main did not move", g(w.main, "rev-parse", "HEAD") == old)
    check(f"[{cid}] the late ignored file holds its bytes", (w.main / "local.cfg").read_text() == "late local bytes\n")


def x16_suite_that_rewrites_tracked_files_is_rolled_back():
    """[CP2-X16] A suite that changes tracked files in the merged copy before passing tested something other than the
    merge commit: rolled back, main untouched."""
    cid = "CP2-X16"
    w = mk()
    mode2(w, "edit-tracked")
    before = tim.snap(w)
    r = w.integrate()
    tim.failed_run(r, cid, "the suite rewrote src/b.js in the merged copy", "changed tracked files", "src/b.js", "again")
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the suite did run once", len(runs2(w)) == 1, runs2(w))
    rec = last_record(w)
    check(f"[{cid}] rolled back with the suite passed and only the lane's tip recorded",
          rec.get("result") == "rolled-back" and rec.get("suite") == "passed"
          and rec.get("commits") == [g(w.base / "lane-x", "rev-parse", "HEAD")], rec)
    tim.no_scratch(w, cid)


def x17_fast_forward_hook_timeout_after_main_moved_is_merged():
    """[CP2-X17] When git stops answering during the fast-forward (a slow post-merge hook) but main is already at the
    merge commit, the attempt is merged, says so, and main's run base still moves."""
    cid = "CP2-X17"
    eng = engine()
    w = mk()
    p = main_run(w, "building")
    seen = []

    def finish(result, suite, msg, detail=None, commits=None):
        seen.append((result, suite, msg, commits))
        raise SystemExit(0 if result in ("merged", "no-op") else 1)
    real = eng.subprocess.run

    def slow(args, *a, **kw):
        r = real(args, *a, **kw)
        if isinstance(args, list) and "--ff-only" in args:
            raise subprocess.TimeoutExpired(args, kw.get("timeout", 120))
        return r
    with th.engine_env(w.denv):
        info = {"lane": "lane-x", "run": None, "commits": []}
        f = eng._integrate_check(w.main, "lane-x", info, lambda m: finish("refused", "not-run", m))
        info["prefix"] = "ratchet-integrate-x17-"
        eng.subprocess.run = slow
        try:
            try:
                eng._integrate_merge(w.main, f, info, finish)
            except SystemExit:
                pass
        finally:
            eng.subprocess.run = real
    new = g(w.main, "rev-parse", "HEAD")
    check(f"[{cid}] main took the merge commit", tim.parents(w.main)[0] == f["head"] and len(tim.parents(w.main)) == 2,
          tim.parents(w.main))
    check(f"[{cid}] recorded as merged, with a note about the hook", len(seen) == 1 and seen[0][0] == "merged"
          and seen[0][1] == "passed" and "still running" in seen[0][2] and (seen[0][3] or [])[:1] == [new], seen)
    st = tim.jread(p)
    check(f"[{cid}] main's run base moved to the merge", st["checkpoints"]["CP1"].get("base_head") == new, st["checkpoints"])
    check(f"[{cid}] no scratch folder is left behind",
          not [q for q in Path(tempfile.gettempdir()).iterdir() if q.name.startswith("ratchet-integrate-x17-")])


def x18_background_note_from_the_directors_own_folder():
    """[CP2-X18] A director that runs integrate from its own folder with --root <main>, in the background, gets no
    foreground warning: the note the guard took in that folder is found."""
    cid = "CP2-X18"
    w = mk()
    ddir = w.base / "director"
    ddir.mkdir()
    cmd = f"RATCHET_ROLE=director python3 {th.ENGINE} --root {w.main} integrate lane-x"
    payload = {"cwd": str(ddir), "session_id": "s-director", "tool_name": "Bash",
               "tool_input": {"command": cmd, "run_in_background": True, "timeout": 120000}}
    gr = th.rx(ddir, w.denv, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))
    need(f"[{cid}] the guard lets the director's call through from its own folder", gr.returncode == 0, (gr.stdout, gr.stderr))
    r = subprocess.run([sys.executable, str(th.ENGINE), "--root", str(w.main), "integrate", "lane-x"], cwd=ddir,
                       capture_output=True, text=True, env=w.denv, timeout=150)
    need(f"[{cid}] the merge succeeds", r.returncode == 0, tim.said(r)[:300])
    check(f"[{cid}] no warning: the background note under the director's folder counts", "WARNING" not in r.stderr,
          r.stderr[:300])
    w = mk()
    r = subprocess.run([sys.executable, str(th.ENGINE), "--root", str(w.main), "integrate", "lane-x"], cwd=ddir,
                       capture_output=True, text=True, env=w.denv, timeout=150)
    check(f"[{cid}] without a note the warning is printed", r.returncode == 0 and "WARNING" in r.stderr, r.stderr[:300])


def x19_records_of_attempts_that_did_not_merge_hold_the_lane_tip():
    """[CP2-X19] A failed suite, a clash and a no-op each record the lane's tip only, never the scratch merge commit."""
    cid = "CP2-X19"
    w = mk()
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    mode2(w, "fail")
    tim.failed_run(w.integrate(), cid, "the suite fails", "failed")
    rec = last_record(w)
    check(f"[{cid}] a failed suite records the lane's tip only", rec.get("suite") == "failed" and rec.get("commits") == [tip], rec)
    mode2(w, "pass")
    old = g(w.main, "rev-parse", "HEAD")
    new = tim.merged_ok(w, w.integrate(), cid, "then it merges", old)
    rec = last_record(w)
    check(f"[{cid}] the merged line holds the merge commit then the lane's commits", rec.get("commits") == [new, tip], rec)
    r = w.integrate()
    need(f"[{cid}] a second integrate is a no-op", r.returncode == 0 and "already merged" in tim.low(r), tim.said(r)[:300])
    rec = last_record(w)
    check(f"[{cid}] the no-op line holds the lane's tip", rec.get("result") == "no-op" and rec.get("commits") == [tip], rec)
    w = mk(dirty_learnings=True)
    lane = tim.add_lane(w, "lane-y", {".ratchet/learnings.md": "# Ratchet learnings\n\n- (2026-10-01) main learning one\n"
                                      "- (2026-10-05) a lane note\n"})
    tim.failed_run(w.integrate("lane-y"), cid, "a clash with a dirty learnings.md", "learnings.md")
    rec = last_record(w)
    check(f"[{cid}] the clash line holds the lane's tip only", rec.get("commits") == [g(lane, "rev-parse", "HEAD")], rec)


def x20_dry_run_git_timeout_is_a_plain_line():
    """[CP2-X20] A git command that times out during a dry run ends in one plain line, not a traceback."""
    cid = "CP2-X20"
    import io
    import types as _types
    eng = engine()
    w = mk()
    real = eng.subprocess.run

    def slow(args, *a, **kw):
        if isinstance(args, list) and "worktree" in args:
            raise subprocess.TimeoutExpired(args, 60)
        return real(args, *a, **kw)
    err, code = io.StringIO(), None
    saved = {k: os.environ.get(k) for k in ("RATCHET_ROOT", "RATCHET_ROLE")}
    old_err, old_cwd = sys.stderr, os.getcwd()
    with th.engine_env(w.denv):
        os.environ["RATCHET_ROOT"], os.environ["RATCHET_ROLE"] = str(w.main), "director"
        os.chdir(w.main)
        eng.subprocess.run, sys.stderr = slow, err
        try:
            try:
                eng.cmd_integrate(_types.SimpleNamespace(lane="lane-x", dry_run=True))
            except SystemExit as e:
                code = e.code
        finally:
            eng.subprocess.run, sys.stderr = real, old_err
            os.chdir(old_cwd)
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    check(f"[{cid}] exit 1 with the plain timeout line and no traceback", code == 1 and "timed out" in err.getvalue()
          and "Nothing was changed" in err.getvalue() and "Traceback" not in err.getvalue(), (code, err.getvalue()[-400:]))
    check(f"[{cid}] a dry run still writes no record line", w.record_lines() == [])


def x21_lane_that_changes_dependencies_is_refused():
    """[CP2-X21] A lane that changes a dependency manifest or lockfile (in any folder) is refused before the suite:
    the suite would run with main's installed dependencies."""
    cid = "CP2-X21"
    w = mk()
    lane = tim.add_lane(w, "lane-y", {"package.json": '{"name": "p", "dependencies": {"dep-b": "1"}}\n', "src/y.js": "// y\n"})
    before = tim.snap(w)
    r = w.integrate("lane-y")
    tim.failed_run(r, cid, "the lane changes package.json", "changes the project's dependencies", "package.json",
                   "main's installed dependencies", "by hand")
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the suite never ran", runs2(w) == [], runs2(w))
    rec = last_record(w)
    check(f"[{cid}] refused, suite not run, the lane's tip recorded", rec.get("result") == "refused"
          and rec.get("suite") == "not-run" and rec.get("commits") == [g(lane, "rev-parse", "HEAD")], rec)
    tim.add_lane(w, "lane-z", {"svc/requirements-dev.txt": "dep-c==1\n"})
    tim.failed_run(w.integrate("lane-z"), cid, "a requirements file in a subfolder", "svc/requirements-dev.txt", "by hand")
    old = g(w.main, "rev-parse", "HEAD")
    tim.merged_ok(w, w.integrate(), cid, "a lane that changes no manifest still merges", old)


def x22_composer_autoloader_in_vendor_is_refused():
    """[CP2-X22] A vendor/ holding Composer's autoloader resolves the project's classes into main through the link:
    refused like a workspace link; a vendor/ without it is not."""
    cid = "CP2-X22"
    w = mk()
    (w.main / "vendor/composer").mkdir(parents=True)
    (w.main / "vendor/composer/autoload_static.php").write_text(
        "<?php\n$baseDir = dirname(dirname(__DIR__));\n'App\\\\' => array($baseDir . '/src'),\n")
    before = tim.snap(w)
    r = w.integrate()
    t = tim.failed_run(r, cid, "vendor/composer/autoload_static.php exists in main", "cannot test this project layout yet",
                       "by hand")
    check(f"[{cid}] it names the autoloader", "autoload_static.php" in t, t[:400])
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the suite never ran", runs2(w) == [], runs2(w))
    check(f"[{cid}] refused and recorded", last_record(w).get("result") == "refused", last_record(w))
    w = mk()
    (w.main / "vendor").mkdir()
    (w.main / "vendor/autoload.php").write_text("<?php\nreturn require __DIR__ . '/composer/autoload_real.php';\n")
    tim.failed_run(w.integrate(), cid, "vendor/autoload.php alone", "autoload.php", "by hand")
    w = mk()
    (w.main / "vendor/lib").mkdir(parents=True)
    (w.main / "vendor/lib/helper.rb").write_text("# vendored\n")
    old = g(w.main, "rev-parse", "HEAD")
    tim.merged_ok(w, w.integrate(), cid, "a vendor/ without Composer's autoloader", old)


def x23_core_worktree_in_main_is_refused():
    """[CP2-X23] A main checkout whose git config sets core.worktree is refused before any clone (its config would
    point the scratch copy at main's files), dry run and real run alike."""
    cid = "CP2-X23"
    w = mk()
    th.git(w.main, "config", "core.worktree", str(w.main))
    before = tim.snap(w)
    r = w.integrate()
    tim.failed_run(r, cid, "main's config sets core.worktree", "core.worktree", "by hand")
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the suite never ran", runs2(w) == [], runs2(w))
    check(f"[{cid}] refused and recorded, no commits", last_record(w).get("result") == "refused"
          and last_record(w).get("commits") == [], last_record(w))
    tim.no_scratch(w, cid)
    tim.failed_run(w.integrate("lane-x", "--dry-run"), cid, "the dry run says the same", "core.worktree", "by hand")
    check(f"[{cid}] the dry run wrote no record line", len(w.record_lines()) == 1)


def x24_killed_during_the_suite_is_recorded_by_the_next_integrate():
    """[CP2-X24] An integrate killed while the suite runs leaves no unrecorded attempt: the next integrate records it
    as rolled back (the lane's tip, suite not run), then does its own work."""
    cid = "CP2-X24"
    import time
    w = mk()
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    old = g(w.main, "rev-parse", "HEAD")
    mode2(w, "hang")
    p = w.start()
    for _ in range(600):
        if runs2(w):
            break
        time.sleep(0.1)
    need(f"[{cid}] the suite started", bool(runs2(w)), runs2(w))
    p.kill()
    p.wait(timeout=30)
    w.release()
    mode2(w, "pass")
    journal = w.main / ".git/ratchet-integrate.journal"
    check(f"[{cid}] the killed attempt left its journal", journal.is_file())
    check(f"[{cid}] nothing was recorded by the killed attempt itself", w.record_lines() == [], w.record_lines())
    r = w.integrate()
    new = tim.merged_ok(w, r, cid, "the next integrate", old)
    lines = w.record_lines()
    check(f"[{cid}] the interrupted attempt is recorded first: rolled back, the lane and run, the lane's tip, suite not run",
          len(lines) == 2 and lines[0].get("result") == "rolled-back" and "interrupted" in lines[0].get("detail", "")
          and lines[0].get("lane") == "lane-x" and lines[0].get("run") == "lane-x-run" and lines[0].get("commits") == [tip]
          and lines[0].get("suite") == "not-run", lines)
    check(f"[{cid}] then the new attempt's own merged line", lines[-1].get("result") == "merged" and lines[-1].get("commits") == [new, tip],
          lines[-1])
    check(f"[{cid}] the journal is gone", not journal.exists())
    tim.no_scratch(w, cid)


def x25_killed_after_the_fast_forward_is_recorded_as_merged():
    """[CP2-X25] An integrate killed after main took the merge but before it recorded it: the next integrate records
    it as merged with a note (record only: main's run base is not moved, and the note says so) and treats the lane as
    already merged."""
    cid = "CP2-X25"
    eng = engine()
    w = mk()
    p = main_run(w, "building")
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    seen = []

    class Killed(Exception):
        pass

    def finish(result, suite, msg, detail=None, commits=None):
        seen.append((result, suite, msg))
        raise SystemExit(1)
    real = eng.subprocess.run

    def killer(args, *a, **kw):
        r = real(args, *a, **kw)
        if isinstance(args, list) and "--ff-only" in args:
            raise Killed()
        return r
    with th.engine_env(w.denv):
        info = {"lane": "lane-x", "run": None, "commits": []}
        f = eng._integrate_check(w.main, "lane-x", info, lambda m: finish("refused", "not-run", m))
        info["prefix"] = "ratchet-integrate-x25-"
        eng.subprocess.run = killer
        try:
            try:
                eng._integrate_merge(w.main, f, info, finish)
            except Killed:
                pass
        finally:
            eng.subprocess.run = real
    new = g(w.main, "rev-parse", "HEAD")
    need(f"[{cid}] main took the merge before the kill", len(tim.parents(w.main)) == 2 and tim.parents(w.main)[0] == f["head"],
         tim.parents(w.main))
    journal = w.main / ".git/ratchet-integrate.journal"
    check(f"[{cid}] nothing recorded, the journal names the merge commit", seen == [] and w.record_lines() == []
          and journal.is_file() and tim.jread(journal).get("sha") == new, (seen, w.record_lines()))
    check(f"[{cid}] main's run base was not moved yet", tim.jread(p)["checkpoints"]["CP1"].get("base_head") == "1" * 40)
    r = w.integrate()
    need(f"[{cid}] the next integrate exits 0 and finds the lane already merged", r.returncode == 0
         and "already merged" in tim.low(r), tim.said(r)[:400])
    check(f"[{cid}] it says the earlier attempt was interrupted after main took the merge, and that main's run was not changed",
          "interrupted" in tim.low(r) and "main's run was not changed" in tim.low(r), tim.said(r)[:600])
    lines = w.record_lines()
    check(f"[{cid}] the interrupted attempt is recorded as merged, with the merge commit then the lane's commit",
          len(lines) == 2 and lines[0].get("result") == "merged" and "interrupted" in lines[0].get("detail", "")
          and lines[0].get("commits") == [new, tip] and lines[0].get("lane") == "lane-x" and lines[0].get("run") == "lane-x-run",
          lines)
    check(f"[{cid}] then the no-op line", lines[-1].get("result") == "no-op", lines[-1])
    st = tim.jread(p)
    check(f"[{cid}] main's run base is not moved by the recovery (record only), no integrated_lane event",
          st["checkpoints"]["CP1"].get("base_head") == "1" * 40
          and not any(e.get("event") == "integrated_lane" for e in st.get("history", [])), st["checkpoints"])
    check(f"[{cid}] the recorded detail says main's run was not changed", "run was not changed" in lines[0].get("detail", ""),
          lines[0])
    check(f"[{cid}] the journal is gone", not journal.exists())
    check(f"[{cid}] main's tip is still the merge commit", g(w.main, "rev-parse", "HEAD") == new)


def x26_dry_run_reports_the_layout_refusals():
    """[CP2-X26] A dry run says the real run would refuse a lane that changes dependencies, or a project whose
    installed dependencies link back into main; it never says 'would merge' for those."""
    cid = "CP2-X26"
    w = mk()
    tim.add_lane(w, "lane-y", {"package.json": '{"name": "p"}\n'})
    r = w.integrate("lane-y", "--dry-run")
    t = tim.failed_run(r, cid, "dry run of a lane that changes package.json", "would refuse", "changes the project's dependencies",
                       "package.json", "by hand")
    check(f"[{cid}] it does not say it would merge", "would merge" not in t, t[:400])
    check(f"[{cid}] no record line for a dry run", w.record_lines() == [])
    w = mk()
    (w.main / "vendor/composer").mkdir(parents=True)
    (w.main / "vendor/composer/autoload_static.php").write_text("<?php\n")
    r = w.integrate("lane-x", "--dry-run")
    tim.failed_run(r, cid, "dry run with Composer's autoloader in vendor", "would refuse", "cannot test this project layout yet")
    check(f"[{cid}] no record line", w.record_lines() == [])
    old = g(w.main, "rev-parse", "HEAD")
    check(f"[{cid}] main did not move", g(w.main, "rev-parse", "HEAD") == old)


def x27_submodules_are_refused():
    """[CP2-X27] A main whose tree has a .gitmodules file is refused before any clone, dry run and real run alike,
    with a message that blames no lane."""
    cid = "CP2-X27"
    w = mk()
    (w.main / ".gitmodules").write_text('[submodule "lib"]\n\tpath = lib\n\turl = ../lib.git\n')
    th.git(w.main, "add", ".gitmodules")
    th.git(w.main, "commit", "-qm", "a submodule")
    before = tim.snap(w)
    r = w.integrate()
    t = tim.failed_run(r, cid, "main's tree has .gitmodules", "submodules", "by hand")
    check(f"[{cid}] the message does not blame the lane", "fix the failure in the lane" not in t, t[:400])
    tim.unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the suite never ran", runs2(w) == [], runs2(w))
    check(f"[{cid}] refused and recorded, no commits", last_record(w).get("result") == "refused"
          and last_record(w).get("commits") == [], last_record(w))
    tim.no_scratch(w, cid)
    tim.failed_run(w.integrate("lane-x", "--dry-run"), cid, "the dry run says the same", "submodules", "by hand")
    check(f"[{cid}] the dry run wrote no record line", len(w.record_lines()) == 1)


def x28_busy_refusal_leaves_the_live_journal_alone():
    """[CP2-X28] An integrate refused as busy while another runs never removes the live attempt's journal, so a later
    kill of the live run is still recorded."""
    cid = "CP2-X28"
    import time
    w = mk()
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    old = g(w.main, "rev-parse", "HEAD")
    mode2(w, "hang")
    p = w.start()
    for _ in range(600):
        if runs2(w):
            break
        time.sleep(0.1)
    need(f"[{cid}] the suite started", bool(runs2(w)), runs2(w))
    journal = w.main / ".git/ratchet-integrate.journal"
    need(f"[{cid}] the live attempt wrote its journal", journal.is_file())
    r = w.integrate()
    tim.failed_run(r, cid, "a second integrate while the first runs", "busy")
    check(f"[{cid}] the busy refusal is recorded", len(w.record_lines()) == 1 and w.record_lines()[0].get("result") == "refused",
          w.record_lines())
    check(f"[{cid}] the live attempt's journal is still there", journal.is_file())
    p.kill()
    p.wait(timeout=30)
    w.release()
    mode2(w, "pass")
    new = tim.merged_ok(w, w.integrate(), cid, "the next integrate", old)
    lines = w.record_lines()
    check(f"[{cid}] the killed attempt is recorded as rolled back after the busy refusal, then the merge",
          len(lines) == 3 and lines[1].get("result") == "rolled-back" and "interrupted" in lines[1].get("detail", "")
          and lines[1].get("commits") == [tip] and lines[2].get("result") == "merged" and lines[2].get("commits") == [new, tip],
          lines)
    check(f"[{cid}] the journal is gone", not journal.exists())


def x29_recovery_counts_a_merge_main_has_since_built_on():
    """[CP2-X29] A journaled merge commit that is an ancestor of main's HEAD (main moved on after the kill) is
    recorded as merged, not as rolled back."""
    cid = "CP2-X29"
    w = mk()
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    old = g(w.main, "rev-parse", "HEAD")
    new = tim.merged_ok(w, w.integrate(), cid, "a plain integrate", old)
    (w.main / "src/later.js").write_text("// after the merge\n")
    th.git(w.main, "add", "src/later.js")
    th.git(w.main, "commit", "-qm", "main moved on")
    head = g(w.main, "rev-parse", "HEAD")
    journal = w.main / ".git/ratchet-integrate.journal"
    journal.write_text(json.dumps({"lane": "lane-x", "run": "lane-x-run", "head": old, "lhead": tip, "target": "main",
                                   "sha": new, "suite": "passed", "started": "2026-10-13T00:00:00+00:00"}))
    r = w.integrate()
    need(f"[{cid}] the next integrate exits 0 as a no-op", r.returncode == 0 and "already merged" in tim.low(r), tim.said(r)[:400])
    lines = w.record_lines()
    check(f"[{cid}] the journaled attempt is recorded as merged (its commit is in main's history), then the no-op",
          len(lines) == 3 and lines[1].get("result") == "merged" and "interrupted" in lines[1].get("detail", "")
          and lines[1].get("commits") == [new, tip] and lines[2].get("result") == "no-op", lines)
    check(f"[{cid}] main did not move and the journal is gone", g(w.main, "rev-parse", "HEAD") == head and not journal.exists())


def kill_after_fast_forward(w, eng):
    """Run the merge in-process and 'kill' it right after main took the fast-forward (before the record line)."""
    class Killed(Exception):
        pass

    def finish(result, suite, msg, detail=None, commits=None):
        raise SystemExit(1)
    real = eng.subprocess.run

    def killer(args, *a, **kw):
        r = real(args, *a, **kw)
        if isinstance(args, list) and "--ff-only" in args:
            raise Killed()
        return r
    with th.engine_env(w.denv):
        info = {"lane": "lane-x", "run": None, "commits": []}
        f = eng._integrate_check(w.main, "lane-x", info, lambda m: finish("refused", "not-run", m))
        info["prefix"] = "ratchet-integrate-kill-"
        eng.subprocess.run = killer
        try:
            try:
                eng._integrate_merge(w.main, f, info, finish)
            except Killed:
                pass
        finally:
            eng.subprocess.run = real
    return f


def x30_recovery_on_a_main_whose_step_has_moved_on_changes_nothing():
    """[CP2-X30] Recovery after main's own run started writing its step's files: record only. The step's files are
    untouched and still its own changes, its base is not moved, and the new attempt is refused as mid-step."""
    cid = "CP2-X30"
    eng = engine()
    w = mk()
    p = main_run(w, "building")
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    f = kill_after_fast_forward(w, eng)
    new = g(w.main, "rev-parse", "HEAD")
    need(f"[{cid}] main took the merge before the kill", len(tim.parents(w.main)) == 2 and tim.parents(w.main)[0] == f["head"],
         tim.parents(w.main))
    (w.main / "tests/step_test.py").write_text("# the step's own test, written after the kill\n")
    st_before = p.read_bytes()
    r = w.integrate()
    tim.failed_run(r, cid, "the new attempt finds main's run in the middle of its step", "middle of a step")
    lines = w.record_lines()
    check(f"[{cid}] the interrupted attempt is recorded as merged with the note, then the refusal",
          len(lines) == 2 and lines[0].get("result") == "merged" and lines[0].get("commits") == [new, tip]
          and "run was not changed" in lines[0].get("detail", "") and lines[1].get("result") == "refused", lines)
    check(f"[{cid}] the step's file is untouched and still untracked", (w.main / "tests/step_test.py").read_text()
          == "# the step's own test, written after the kill\n" and "?? tests/step_test.py" in tim.porcelain_lines(w.main),
          tim.porcelain_lines(w.main))
    check(f"[{cid}] main's run state is untouched, byte for byte", p.read_bytes() == st_before)
    check(f"[{cid}] main's tip is still the merge commit", g(w.main, "rev-parse", "HEAD") == new)


def x31_a_record_that_cannot_be_written_is_kept_and_recorded_later():
    """[CP2-X31] When the record line cannot be appended, the attempt's outcome stays in the journal; recovery refuses
    plainly while writing still fails, and appends that very line once it works."""
    cid = "CP2-X31"
    w = mk()
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    old = g(w.main, "rev-parse", "HEAD")
    blocker = w.main / ".ratchet/integrations.jsonl"
    blocker.mkdir()  # a folder in the record's place: no line can be appended
    r = w.integrate()
    need(f"[{cid}] the merge itself still succeeds", tim.known(r) and r.returncode == 0, tim.said(r)[:400])
    new = g(w.main, "rev-parse", "HEAD")
    check(f"[{cid}] it warns that the outcome is kept for the next integrate", "could not write the integration record" in r.stderr
          and "next integrate records it" in r.stderr, r.stderr[-500:])
    journal = w.main / ".git/ratchet-integrate.journal"
    j = tim.jread(journal) if journal.is_file() else {}
    check(f"[{cid}] the journal holds the outcome line", j.get("outcome", {}).get("result") == "merged"
          and j["outcome"].get("commits") == [new, tip], j)
    r = w.integrate()
    tim.failed_run(r, cid, "recovery while the record still cannot be written", "could not be written",
                   "integrations.jsonl", "run integrate again")
    check(f"[{cid}] the journal is kept", journal.is_file() and tim.jread(journal).get("outcome", {}).get("result") == "merged")
    check(f"[{cid}] main did not move", g(w.main, "rev-parse", "HEAD") == new)
    blocker.rmdir()
    r = w.integrate()
    need(f"[{cid}] once writing works, the next integrate is a no-op", r.returncode == 0 and "already merged" in tim.low(r),
         tim.said(r)[:400])
    lines = w.record_lines()
    check(f"[{cid}] the journaled outcome line is written as it was, then the no-op",
          len(lines) == 2 and lines[0] == j["outcome"] and lines[1].get("result") == "no-op", lines)
    check(f"[{cid}] the journal is gone", not journal.exists())
    # the same for recovery itself: a journal from a kill, and a record that cannot be written
    eng = engine()
    w = mk()
    kill_after_fast_forward(w, eng)
    new = g(w.main, "rev-parse", "HEAD")
    (w.main / ".ratchet/integrations.jsonl").mkdir()
    r = w.integrate()
    tim.failed_run(r, cid, "recovery of a killed attempt while the record cannot be written", "could not be written")
    check(f"[{cid}] the kill's journal is kept", (w.main / ".git/ratchet-integrate.journal").is_file())
    (w.main / ".ratchet/integrations.jsonl").rmdir()
    r = w.integrate()
    need(f"[{cid}] then it is recorded", r.returncode == 0, tim.said(r)[:400])
    lines = w.record_lines()
    check(f"[{cid}] merged with the note, then the no-op", len(lines) == 2 and lines[0].get("result") == "merged"
          and "interrupted" in lines[0].get("detail", "") and lines[0].get("commits")[:1] == [new], lines)


def x32_killed_during_the_checks_is_recorded():
    """[CP2-X32] An integrate killed during its checks (before any scratch work) is recorded by the next integrate as
    rolled back, with the lane as given and no commits."""
    cid = "CP2-X32"
    import types as _types
    eng = engine()
    w = mk()
    real = eng.subprocess.run

    class Killed(Exception):
        pass

    def killer(args, *a, **kw):
        if isinstance(args, list) and "worktree" in args:
            raise Killed()
        return real(args, *a, **kw)
    saved = {k: os.environ.get(k) for k in ("RATCHET_ROOT", "RATCHET_ROLE")}
    old_cwd = os.getcwd()
    with th.engine_env(w.denv):
        os.environ["RATCHET_ROOT"], os.environ["RATCHET_ROLE"] = str(w.main), "director"
        os.chdir(w.main)
        eng.subprocess.run = killer
        try:
            try:
                eng.cmd_integrate(_types.SimpleNamespace(lane="lane-x", dry_run=False))
            except (Killed, SystemExit):
                pass
        finally:
            eng.subprocess.run = real
            os.chdir(old_cwd)
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    journal = w.main / ".git/ratchet-integrate.journal"
    check(f"[{cid}] the killed attempt left a starting journal and no record", journal.is_file()
          and tim.jread(journal).get("sha") is None and w.record_lines() == [], w.record_lines())
    old = g(w.main, "rev-parse", "HEAD")
    tip = g(w.base / "lane-x", "rev-parse", "HEAD")
    new = tim.merged_ok(w, w.integrate(), cid, "the next integrate", old)
    lines = w.record_lines()
    check(f"[{cid}] the killed attempt is recorded as rolled back (lane as given, no run, no commits), then the merge",
          len(lines) == 2 and lines[0].get("result") == "rolled-back" and "interrupted" in lines[0].get("detail", "")
          and lines[0].get("lane") == "lane-x" and lines[0].get("run") is None and lines[0].get("commits") == []
          and lines[1].get("result") == "merged" and lines[1].get("commits") == [new, tip], lines)
    check(f"[{cid}] the journal is gone", not journal.exists())


CASES = [x01_scratch_copy_acts_like_main, x02_main_is_rechecked_right_before_it_moves,
         x03_autostash_setting_cannot_hide_an_edit, x04_dependency_folders_linking_into_main_are_refused,
         x05_every_refusal_is_recorded, x06_untracked_file_main_would_overwrite, x07_record_holds_full_hashes,
         x08_background_warning_is_right_for_integrate, x09_uncommitted_shared_file_the_merge_changes,
         x10_a_git_timeout_is_a_plain_rollback, x11_main_run_between_steps_starts_from_the_merge,
         x12_main_run_paused_mid_step_is_refused, x13_project_below_the_repository_top_is_refused,
         x14_linked_dependencies_say_merge_by_hand, x15_ignored_file_on_main_is_never_overwritten,
         x16_suite_that_rewrites_tracked_files_is_rolled_back, x17_fast_forward_hook_timeout_after_main_moved_is_merged,
         x18_background_note_from_the_directors_own_folder, x19_records_of_attempts_that_did_not_merge_hold_the_lane_tip,
         x20_dry_run_git_timeout_is_a_plain_line, x21_lane_that_changes_dependencies_is_refused,
         x22_composer_autoloader_in_vendor_is_refused, x23_core_worktree_in_main_is_refused,
         x24_killed_during_the_suite_is_recorded_by_the_next_integrate, x25_killed_after_the_fast_forward_is_recorded_as_merged,
         x26_dry_run_reports_the_layout_refusals, x27_submodules_are_refused,
         x28_busy_refusal_leaves_the_live_journal_alone, x29_recovery_counts_a_merge_main_has_since_built_on,
         x30_recovery_on_a_main_whose_step_has_moved_on_changes_nothing, x31_a_record_that_cannot_be_written_is_kept_and_recorded_later,
         x32_killed_during_the_checks_is_recorded]


def main():
    only = [a.upper() for a in sys.argv[1:]]
    for fn in CASES:
        cid = "CP2-" + fn.__name__.split("_")[0].upper()
        if only and not any(cid.endswith(o) for o in only):
            continue
        print(cid)
        try:
            fn()
        except Stop:
            pass
        except Exception as e:  # a crash is a failed result, never a silent drop
            tim.FAIL += 1
            print(f"  ✗ [{cid}] stopped early: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{tim.PASS} passed, {tim.FAIL} failed")
    sys.exit(1 if tim.FAIL else 0)


if __name__ == "__main__":
    main()
