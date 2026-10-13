#!/usr/bin/env python3
"""
CP2 tests: `integrate <lane>` merges a finished lane into main, or rolls back.   python3 tests/test_integrate_merge.py

The director (RATCHET_ROLE=director) merges a finished lane into the main checkout. These tests drive the repo's own
ratchet.py as a subprocess against throwaway git projects: a main checkout on branch main with a committed .ratchet/
and a suite command, and plain `git worktree add` lanes (lane-x, run 'lane-x-run', phase done, one commit adding
src/lane.js). Main is one commit ahead of the lane's base (src/main_only.js), so a clean integrate is a real merge.

The suite command is a probe script kept outside the repo. Each run it appends one JSON line to a log (its working
folder, the commit checked out there, main's HEAD at that moment, which files it saw, its pid) and then does what a
control file says: pass, fail (prints SUITE-BOOM-LINE), hang, block (wait for a release file), commit-main or
edit-main (change the main checkout while it runs). Assertions are on git facts (HEAD, parents, tree, status, worktree
list, refs, a bare remote), on file bytes and on the record file, never on engine internals.

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

ENGINE = th.ENGINE
PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+[a-z]?)\]")
REAL_GIT = shutil.which("git")
A_BASE = "".join(f"line{i}\n" for i in range(1, 10))
B_BASE = "export const b = 1;\n"
LANE_JS = "export const lane = () => 'lane';\n"
MAIN_ONLY_JS = "export const mainOnly = () => 'main';\n"
RECORD_FIELDS = {"time", "lane", "run", "commits", "suite", "result", "detail"}


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:500]}"))


class Stop(Exception):
    """A case cannot go on after a failed check that was already recorded."""


def need(name, cond, detail=""):
    check(name, cond, detail)
    if not cond:
        raise Stop(name)


# ------------------------------------------------------------------------------------------------ text helpers

PATH_RE = re.compile(r"file://\S*|(?<![\w.\-/])/[^\s'\"),;>]+")


def said(r) -> str:
    """Everything a command printed, with absolute paths cut out (temp folders are named after the suite)."""
    return PATH_RE.sub("<path>", (r.stdout or "") + "\n" + (r.stderr or ""))


def raw(r) -> str:
    return (r.stdout or "") + "\n" + (r.stderr or "")


def known(r) -> bool:
    """The command exists: what came back is the engine's own message, not a usage error or a crash."""
    t = said(r)
    return "invalid choice" not in t and "usage:" not in t and "Traceback" not in t


def low(r) -> str:
    return said(r).lower() if known(r) else ""


def msg_lines(r) -> list:
    return [ln for ln in raw(r).splitlines() if ln.strip() and not ln.startswith("RATCHET: note:")]


# ------------------------------------------------------------------------------------------------ git helpers

def g(d, *a) -> str:
    return subprocess.run([REAL_GIT, *a], cwd=d, capture_output=True, text=True).stdout.strip()


def gok(d, *a) -> bool:
    return subprocess.run([REAL_GIT, *a], cwd=d, capture_output=True, text=True).returncode == 0


def jread(p):
    return json.loads(Path(p).read_text())


def jwrite(p, d):
    Path(p).write_text(json.dumps(d, indent=2) + "\n")


def set_phase(d, slug, phase):
    p = Path(d) / ".ratchet/runs" / slug / "state.json"
    st = jread(p)
    st["phase"] = phase
    if phase == "building":
        st["current"] = "CP1"
        st["checkpoints"] = {"CP1": {"status": "in_progress", "step": "implement"}}
    jwrite(p, st)


def parents(d, rev="HEAD") -> list:
    return g(d, "rev-list", "--parents", "-n", "1", rev).split()[1:]


def alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def wait_for(path, secs=40.0, proc=None) -> bool:
    """Wait for a marker file; give up early when the process that should write it has already exited."""
    end = time.time() + secs
    while time.time() < end:
        if Path(path).exists():
            return True
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.05)
    return Path(path).exists()


def porcelain_lines(d) -> list:
    """git status --porcelain lines with the leading space kept (g() strips it)."""
    out = subprocess.run([REAL_GIT, "status", "--porcelain"], cwd=d, capture_output=True, text=True).stdout
    return out.splitlines()


# ------------------------------------------------------------------------------------------------ fixtures

SUITE_PY = r'''
import json, os, subprocess, sys, time
BASE = %(base)r
MAIN = %(main)r
CTL = os.path.join(BASE, "ctl")


def g(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True).stdout.strip()


try:
    mode = open(os.path.join(CTL, "mode")).read().strip()
except OSError:
    mode = "pass"
cwd = os.getcwd()
nm = os.path.join(cwd, "node_modules")
rec = {"cwd": os.path.realpath(cwd), "head": g(cwd, "rev-parse", "HEAD"), "main_head": g(MAIN, "rev-parse", "HEAD"),
       "lane_js": os.path.exists(os.path.join(cwd, "src", "lane.js")),
       "main_only": os.path.exists(os.path.join(cwd, "src", "main_only.js")),
       "probe": os.path.exists(os.path.join(nm, "probe.txt")), "pid": os.getpid(), "mode": mode}
if os.path.isdir(nm):
    try:
        open(os.path.join(nm, "written.txt"), "w").write("x")
        rec["wrote"] = True
    except OSError:
        rec["wrote"] = False
with open(os.path.join(CTL, "log.jsonl"), "a") as f:
    f.write(json.dumps(rec) + "\n")
if mode == "fail":
    print("SUITE-BOOM-LINE")
    sys.exit(1)
if mode == "hang":
    time.sleep(40)
if mode == "block":
    open(os.path.join(CTL, "started"), "w").write("1")
    end = time.time() + 60
    while time.time() < end and not os.path.exists(os.path.join(CTL, "release")):
        time.sleep(0.05)
if mode == "commit-main":
    open(os.path.join(MAIN, "src", "other.js"), "w").write("export const other = 1;\n")
    subprocess.run(["git", "add", "src/other.js"], cwd=MAIN, check=True)
    subprocess.run(["git", "commit", "-qm", "other work"], cwd=MAIN, check=True)
if mode == "edit-main":
    p = os.path.join(MAIN, "src", "a.js")
    s = open(p).read()
    open(p, "w").write(s.replace("line9\n", "line9 dev edit\n"))
sys.exit(0)
'''


class W:
    """A throwaway project: main checkout, lane worktrees beside it, a suite probe and a private HOME."""

    def __init__(self, base, main, env):
        self.base, self.main, self.env = base, main, env
        self.denv = dict(env, RATCHET_ROLE="director")
        self.ctl = base / "ctl"
        self.tmp = base / "tmp"
        self.lanes = {}

    @property
    def record(self) -> Path:
        return self.main / ".ratchet/integrations.jsonl"

    def mode(self, m):
        (self.ctl / "mode").write_text(m)

    def runs(self) -> list:
        p = self.ctl / "log.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []

    def argv(self, target, *extra):
        return [sys.executable, str(ENGINE), "--root", str(self.main), "integrate", str(target), *extra]

    def integrate(self, target="lane-x", *extra, role="director", timeout=150):
        return subprocess.run(self.argv(target, *extra), cwd=self.main, capture_output=True, text=True,
                              env=self.denv if role == "director" else self.env, timeout=timeout)

    def start(self, target="lane-x", *extra):
        return subprocess.Popen(self.argv(target, *extra), cwd=self.main, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.denv)

    def record_lines(self) -> list:
        if not self.record.exists():
            return []
        return [json.loads(x) for x in self.record.read_text().splitlines() if x.strip()]

    def release(self):
        (self.ctl / "release").write_text("1")


def add_lane(w: W, name, edits, base_ref="HEAD", phase="done"):
    """A plain git worktree on its own branch with one commit of `edits` ({path: text}) and its own run, '<name>-run'."""
    lane = w.base / name
    th.git(w.main, "worktree", "add", "-q", "-b", name, str(lane), base_ref)
    for rel, text in edits.items():
        (lane / rel).parent.mkdir(parents=True, exist_ok=True)
        (lane / rel).write_text(text)
    th.git(lane, "add", "-A")
    th.git(lane, "commit", "-qm", f"{name}: work")
    if phase:
        r = th.rx(lane, w.env, "init", f"{name} work", "--slug", f"{name}-run")
        assert r.returncode == 0, r.stdout + r.stderr
        set_phase(lane, f"{name}-run", phase)
    w.lanes[name] = lane
    return lane


def build(lane_phase="done", regression=True, timeout=None, node_modules=False, remote=False, conflict=False,
          lane_edits_a=False, dirty_learnings=False, main_ahead=True) -> W:
    env = th.make_env()
    base = Path(os.path.realpath(_tmp.mkdtemp(prefix="ratchet-merge-")))
    main = base / "main"
    (base / "ctl").mkdir()
    (base / "tmp").mkdir()
    env["TMPDIR"] = str(base / "tmp")
    (base / "suite.py").write_text(SUITE_PY % {"base": str(base), "main": str(main)})
    main.mkdir()
    for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        th.git(main, *c)
    (main / "src").mkdir()
    (main / "tests").mkdir()
    (main / "src/a.js").write_text(A_BASE)
    (main / "src/b.js").write_text(B_BASE)
    (main / "tests/README.md").write_text("tests live here\n")
    (main / ".gitignore").write_text("node_modules/\n")
    r = th.rx(main, env, "init", "main work", "--slug", "main-run")
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = jread(main / ".ratchet/config.json")
    cfg["regression_command"] = f"python3 {base / 'suite.py'}" if regression else None
    if timeout:
        cfg["test_timeout_sec"] = timeout
    jwrite(main / ".ratchet/config.json", cfg)
    (main / ".ratchet/standards.md").write_text("# Standards\n\n1. Keep functions short\n")
    (main / ".ratchet/learnings.md").write_text("# Ratchet learnings\n\n- (2026-10-01) main learning one\n")
    th.git(main, "add", "-A")
    th.git(main, "commit", "-qm", "initial commit")
    shutil.rmtree(main / ".ratchet/runs")
    (main / ".ratchet/active.json").unlink()
    w = W(base, main, env)
    if remote:
        rem = base / "origin.git"
        th.git(base, "init", "-q", "--bare", "-b", "main", str(rem))
        th.git(main, "remote", "add", "origin", str(rem))
        th.git(main, "push", "-q", "origin", "main")
        hook = rem / "hooks/pre-receive"
        hook.write_text(f"#!/bin/sh\ntouch '{base / 'pushed-marker'}'\n")
        hook.chmod(0o755)
        w.remote = rem
    lane_edits = {"src/lane.js": LANE_JS}
    if conflict:
        lane_edits["src/a.js"] = A_BASE.replace("line1\n", "line1 lane\n")
        lane_edits["src/b.js"] = "export const b = 2;\n"
    elif lane_edits_a:
        lane_edits["src/a.js"] = A_BASE.replace("line1\n", "line1 lane\n")
    add_lane(w, "lane-x", lane_edits, phase=lane_phase)
    if main_ahead:
        (main / "src/main_only.js").write_text(MAIN_ONLY_JS)
        if conflict:
            (main / "src/a.js").write_text(A_BASE.replace("line1\n", "line1 main\n"))
            (main / "src/b.js").write_text("export const b = 3;\n")
        th.git(main, "add", "-A")
        th.git(main, "commit", "-qm", "main adds main_only")
    if node_modules:
        (main / "node_modules").mkdir()
        (main / "node_modules/probe.txt").write_text("probe\n")
    if dirty_learnings:
        with open(main / ".ratchet/learnings.md", "a") as f:
            f.write("- (2026-10-04) an uncommitted note on main\n")
    w.mode("pass")
    return w


def worktree_paths(w: W) -> set:
    out = set()
    for ln in subprocess.run([REAL_GIT, "worktree", "list", "--porcelain"], cwd=w.main, capture_output=True,
                             text=True).stdout.splitlines():
        if ln.startswith("worktree "):
            out.add(os.path.realpath(ln[9:]))
    return out


def scratch_dirs(w: W) -> list:
    """Worktree-looking folders (one with a .git entry) left in the private TMPDIR."""
    out = []
    for root, dirs, files in os.walk(w.tmp):
        depth = Path(root).relative_to(w.tmp).parts
        if len(depth) > 3:
            dirs[:] = []
            continue
        if ".git" in dirs or ".git" in files:
            out.append(root)
    return out


def admin_names(w: W) -> list:
    d = w.main / ".git/worktrees"
    return sorted(os.listdir(d)) if d.is_dir() else []


def no_scratch(w: W, cid, what="", lanes=("lane-x",)):
    exp = {os.path.realpath(w.main)} | {os.path.realpath(w.base / n) for n in lanes}
    check(f"[{cid}] {what}no scratch worktree remains in git worktree list", worktree_paths(w) == exp,
          sorted(worktree_paths(w) ^ exp))
    check(f"[{cid}] {what}no stale worktree admin entry and no leftover scratch folder",
          admin_names(w) == sorted(lanes) and not scratch_dirs(w), (admin_names(w), scratch_dirs(w)))


def snap(w: W) -> dict:
    """What a refused or rolled-back attempt must leave alone in the main checkout."""
    files = {f: (w.main / f).read_bytes() for f in g(w.main, "ls-files").splitlines() if (w.main / f).is_file()}
    return {"head": g(w.main, "rev-parse", "HEAD"), "branch": g(w.main, "rev-parse", "--abbrev-ref", "HEAD"),
            "refs": g(w.main, "for-each-ref"), "stash": g(w.main, "stash", "list"),
            "status": g(w.main, "status", "--porcelain", "-uall"),
            "merge_head": gok(w.main, "rev-parse", "-q", "--verify", "MERGE_HEAD"),
            "files": files, "worktrees": sorted(worktree_paths(w))}


def diff_keys(a, b):
    return [k for k in a if a[k] != b[k]]


def unchanged(w: W, before, cid, what="main is exactly as it was"):
    after = snap(w)
    d = diff_keys(before, after)
    check(f"[{cid}] {what} (tip, branch, refs, stash, status, files, worktrees)", not d, d)


def failed_run(r, cid, what, *needles):
    """Exit non-zero with the engine's own message (no traceback) holding every needle (a tuple means any of)."""
    t = low(r)
    need(f"[{cid}] {what}: a non-zero exit with the engine's own message", known(r) and r.returncode != 0 and t,
         (r.returncode, said(r)[:300]))
    for n in needles:
        alts = n if isinstance(n, tuple) else (n,)
        check(f"[{cid}] {what}: the message says {' / '.join(alts)}", any(a in t for a in alts), said(r)[:400])
    return t


def merged_ok(w: W, r, cid, what, old_head):
    need(f"[{cid}] {what}: exit 0 with the engine's own output", known(r) and r.returncode == 0,
         (r.returncode, said(r)[:300]))
    tip = g(w.main, "rev-parse", "HEAD")
    check(f"[{cid}] {what}: main's tip is a merge commit on top of the old tip",
          tip != old_head and len(parents(w.main)) == 2 and parents(w.main)[0] == old_head, parents(w.main))
    return tip


# ------------------------------------------------------------------------------------------------ cases

def t01_finished_lane_merges_as_one_merge_commit():
    """[CP2-T01] A finished lane merges into main as one merge commit naming the lane and its run."""
    cid = "CP2-T01"
    w = build()
    old = g(w.main, "rev-parse", "HEAD")
    lane_tip = g(w.lanes["lane-x"], "rev-parse", "HEAD")
    lane_status = g(w.lanes["lane-x"], "status", "--porcelain", "-uall")
    r = w.integrate()
    need(f"[{cid}] the command succeeds", known(r) and r.returncode == 0, (r.returncode, said(r)[:300]))
    tip = g(w.main, "rev-parse", "HEAD")
    check(f"[{cid}] main is still on branch 'main'", g(w.main, "rev-parse", "--abbrev-ref", "HEAD") == "main")
    check(f"[{cid}] main's tip is a merge commit: first parent the old tip, second parent the lane's tip",
          parents(w.main) == [old, lane_tip], parents(w.main))
    msg = g(w.main, "log", "-1", "--format=%B")
    check(f"[{cid}] the commit message names 'lane-x' and the run 'lane-x-run'", "lane-x" in msg and "lane-x-run" in msg,
          msg)
    check(f"[{cid}] main's folder has the lane's file with its exact contents",
          (w.main / "src/lane.js").is_file() and (w.main / "src/lane.js").read_text() == LANE_JS)
    check(f"[{cid}] main's folder still has main's own file with its exact contents",
          (w.main / "src/main_only.js").is_file() and (w.main / "src/main_only.js").read_text() == MAIN_ONLY_JS)
    check(f"[{cid}] main's working tree matches the new tip (clean status)",
          g(w.main, "status", "--porcelain", "-uall") == "", g(w.main, "status", "--porcelain", "-uall"))
    check(f"[{cid}] both files are in the committed tree",
          {"src/lane.js", "src/main_only.js"} <= set(g(w.main, "ls-tree", "-r", "--name-only", tip).splitlines()))
    check(f"[{cid}] the lane's branch and worktree are untouched",
          g(w.lanes["lane-x"], "rev-parse", "HEAD") == lane_tip
          and g(w.lanes["lane-x"], "status", "--porcelain", "-uall") == lane_status
          and g(w.main, "rev-parse", "lane-x") == lane_tip
          and os.path.realpath(w.lanes["lane-x"]) in worktree_paths(w))


def t02_suite_passed_on_the_commit_that_lands_in_a_copy():
    """[CP2-T02] The suite passed on exactly the commit that lands on main, in a copy, not in main's folder."""
    cid = "CP2-T02"
    w = build()
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    tip = merged_ok(w, r, cid, "clean integrate", old)
    runs = w.runs()
    need(f"[{cid}] the suite ran exactly once", len(runs) == 1, len(runs))
    run = runs[0]
    check(f"[{cid}] its working folder is not main's checkout folder", run["cwd"] != os.path.realpath(w.main), run["cwd"])
    check(f"[{cid}] the commit it saw is the same hash as main's new tip", run["head"] == tip, (run["head"], tip))
    check(f"[{cid}] it saw both src/lane.js and src/main_only.js", run["lane_js"] and run["main_only"], run)
    no_scratch(w, cid)


def t03_main_does_not_move_until_the_suite_finished_and_passed():
    """[CP2-T03] Main does not move until the suite has finished and passed."""
    cid = "CP2-T03"
    w = build()
    w.mode("block")
    old = g(w.main, "rev-parse", "HEAD")
    before = snap(w)
    p = w.start()
    try:
        need(f"[{cid}] the suite started and is paused", wait_for(w.ctl / "started", proc=p), p.poll())
        paused = snap(w)
        d = diff_keys(before, paused)
        check(f"[{cid}] while the suite is paused main's tip, branch, files and working tree are exactly the old ones",
              not d and not (w.main / "src/lane.js").exists(), d)
        check(f"[{cid}] the suite saw main's old tip while it ran", w.runs() and w.runs()[0]["main_head"] == old,
              w.runs())
        check(f"[{cid}] the command has not finished while the suite is paused", p.poll() is None)
    finally:
        w.release()
    out, err = p.communicate(timeout=120)
    check(f"[{cid}] after release and a pass the command succeeds", p.returncode == 0, (p.returncode, (out + err)[:300]))
    check(f"[{cid}] only then does main's tip become the merge commit",
          g(w.main, "rev-parse", "HEAD") != old and len(parents(w.main)) == 2 and parents(w.main)[0] == old,
          parents(w.main))


def t04_failing_suite_leaves_main_as_it_was():
    """[CP2-T04] A failing suite leaves main exactly as it was, and the message says what failed and what to do."""
    cid = "CP2-T04"
    w = build()
    w.mode("fail")
    before = snap(w)
    r = w.integrate()
    t = failed_run(r, cid, "suite fails", "suite-boom-line")
    check(f"[{cid}] a plain next step: fix the lane, finish it again, then integrate again",
          ("fix" in t or "finish" in t) and "integrate" in t and "again" in t, said(r)[:500])
    unchanged(w, before, cid)
    check(f"[{cid}] the suite did run once on the merged code", len(w.runs()) == 1, len(w.runs()))
    no_scratch(w, cid)


def t05_uncommitted_ratchet_file_untouched_by_rollback():
    """[CP2-T05] Main's uncommitted changes to Ratchet's own files survive a rolled-back attempt untouched."""
    cid = "CP2-T05"
    w = build(dirty_learnings=True)
    w.mode("fail")
    before = snap(w)
    content = (w.main / ".ratchet/learnings.md").read_bytes()
    r = w.integrate()
    failed_run(r, cid, "suite fails with learnings.md edited on main", "suite-boom-line")
    check(f"[{cid}] the suite did run on the merged code (so this was a real rollback)", len(w.runs()) == 1, len(w.runs()))
    check(f"[{cid}] learnings.md has exactly the bytes it had, including the developer's extra line",
          (w.main / ".ratchet/learnings.md").read_bytes() == content and b"an uncommitted note on main" in content)
    check(f"[{cid}] and git still shows it modified and uncommitted",
          " M .ratchet/learnings.md" in porcelain_lines(w.main), porcelain_lines(w.main))
    check(f"[{cid}] main's tip is the old tip: no commit of any kind was made", g(w.main, "rev-parse", "HEAD") ==
          before["head"])
    unchanged(w, before, cid)


def t06_uncommitted_ratchet_file_carried_through_success():
    """[CP2-T06] Main's uncommitted Ratchet-file changes are carried through a successful merge, still uncommitted."""
    cid = "CP2-T06"
    w = build(dirty_learnings=True)
    old = g(w.main, "rev-parse", "HEAD")
    content = (w.main / ".ratchet/learnings.md").read_bytes()
    r = w.integrate()
    need(f"[{cid}] the command succeeds", known(r) and r.returncode == 0, (r.returncode, said(r)[:300]))
    check(f"[{cid}] main gained exactly one commit on its own line: the merge, directly on the old tip",
          g(w.main, "rev-list", "--first-parent", "--count", f"{old}..HEAD") == "1" and parents(w.main)[0] == old
          and len(parents(w.main)) == 2, parents(w.main))
    check(f"[{cid}] the lane's file is in main", (w.main / "src/lane.js").is_file())
    check(f"[{cid}] learnings.md has exactly the bytes it had, including the developer's extra line",
          (w.main / ".ratchet/learnings.md").read_bytes() == content)
    check(f"[{cid}] and git still shows it modified and uncommitted",
          " M .ratchet/learnings.md" in porcelain_lines(w.main), porcelain_lines(w.main))
    check(f"[{cid}] the developer's line is not in any commit",
          "an uncommitted note on main" not in g(w.main, "show", "HEAD:.ratchet/learnings.md"))


def t07_conflict_stops_the_merge_and_lists_files():
    """[CP2-T07] A conflict in project files stops the merge, changes nothing and lists the files."""
    cid = "CP2-T07"
    w = build(conflict=True)
    before = snap(w)
    r = w.integrate()
    t = failed_run(r, cid, "conflict in src/a.js and src/b.js", "src/a.js", "src/b.js")
    check(f"[{cid}] the output does not list src/lane.js (it merges cleanly)", "src/lane.js" not in t, said(r)[:400])
    check(f"[{cid}] it tells the agent to merge main into the lane (the merged command) and integrate again",
          "merged" in t and "integrate" in t and "again" in t, said(r)[:500])
    unchanged(w, before, cid)
    check(f"[{cid}] the suite never ran", w.runs() == [], w.runs())
    check(f"[{cid}] no merge state is left in main", not gok(w.main, "rev-parse", "-q", "--verify", "MERGE_HEAD"))
    no_scratch(w, cid)


def t08_main_moved_while_the_suite_ran():
    """[CP2-T08] If main moved while the suite ran, nothing lands and the director is told to run it again."""
    cid = "CP2-T08"
    w = build()
    old = g(w.main, "rev-parse", "HEAD")
    w.mode("commit-main")
    r = w.integrate()
    t = failed_run(r, cid, "main got a commit during the suite", "again")
    check(f"[{cid}] it says to run integrate again", "integrate" in t, said(r)[:400])
    tip = g(w.main, "rev-parse", "HEAD")
    check(f"[{cid}] main's tip is the commit made during the suite: one parent, on the old tip, not a merge",
          tip != old and parents(w.main) == [old] and g(w.main, "log", "-1", "--format=%s") == "other work",
          parents(w.main))
    check(f"[{cid}] src/lane.js is not in main", "src/lane.js" not in g(w.main, "ls-files").splitlines()
          and not (w.main / "src/lane.js").exists())
    check(f"[{cid}] main's files and status are exactly what that commit left",
          g(w.main, "status", "--porcelain", "-uall") == "" and (w.main / "src/other.js").is_file())
    no_scratch(w, cid)
    w.mode("pass")
    r2 = w.integrate()
    merged_ok(w, r2, cid, "running integrate again", tip)
    check(f"[{cid}] the second run merges cleanly on top of the new tip: both files are there",
          (w.main / "src/lane.js").is_file() and (w.main / "src/other.js").is_file())


def t09_uncommitted_edit_made_during_the_suite_is_not_overwritten():
    """[CP2-T09] An uncommitted edit made in main while the suite ran is never overwritten."""
    cid = "CP2-T09"
    w = build(lane_edits_a=True)
    old = g(w.main, "rev-parse", "HEAD")
    w.mode("edit-main")
    r = w.integrate()
    t = failed_run(r, cid, "main's src/a.js was edited during the suite", "commit", ("set aside", "stash", "discard"),
                   "again")
    check(f"[{cid}] it says to integrate again", "integrate" in t, said(r)[:400])
    check(f"[{cid}] main's tip is unchanged (no fast-forward over the changed file)",
          g(w.main, "rev-parse", "HEAD") == old, g(w.main, "rev-parse", "HEAD"))
    check(f"[{cid}] src/a.js holds exactly the edit made during the suite, byte for byte",
          (w.main / "src/a.js").read_bytes() == A_BASE.replace("line9\n", "line9 dev edit\n").encode())
    check(f"[{cid}] the edit is still uncommitted", " M src/a.js" in porcelain_lines(w.main), porcelain_lines(w.main))
    check(f"[{cid}] the lane's file did not land", not (w.main / "src/lane.js").exists())
    no_scratch(w, cid)


def t10_hanging_suite_times_out_and_rolls_back():
    """[CP2-T10] A suite that hangs past the time limit counts as a failure and rolls back."""
    cid = "CP2-T10"
    w = build(timeout=3)
    w.mode("hang")
    before = snap(w)
    t0 = time.time()
    try:
        r = w.integrate(timeout=90)
        took = time.time() - t0
        t = failed_run(r, cid, "suite hangs past test_timeout_sec", ("timed out", "timeout", "time limit"))
        check(f"[{cid}] it came back in a bounded time", took < 30, took)
        unchanged(w, before, cid)
        runs = w.runs()
        need(f"[{cid}] the suite did start", len(runs) == 1, runs)
        pid = runs[0]["pid"]
        end = time.time() + 6
        while alive(pid) and time.time() < end:
            time.sleep(0.2)
        check(f"[{cid}] the suite process is not left running", not alive(pid), pid)
        no_scratch(w, cid)
    finally:
        for run in w.runs():
            if alive(run["pid"]):
                try:
                    os.kill(run["pid"], signal.SIGKILL)
                except OSError:
                    pass


def t11_no_suite_command_is_refused():
    """[CP2-T11] With no suite command configured, integrate refuses rather than merging untested code."""
    cid = "CP2-T11"
    w = build(regression=False)
    before = snap(w)
    r = w.integrate()
    failed_run(r, cid, "no regression_command", "regression_command", "set")
    unchanged(w, before, cid)
    check(f"[{cid}] the suite never ran", w.runs() == [], w.runs())
    lines = w.record_lines()
    check(f"[{cid}] a record line has result 'refused' and suite 'not-run'",
          bool(lines) and lines[-1].get("result") == "refused" and lines[-1].get("suite") == "not-run", lines)


def t12_unfinished_lane_is_refused_and_recorded():
    """[CP2-T12] A lane that is not finished is refused before any merge work, and the refusal is recorded."""
    cid = "CP2-T12"
    w = build(lane_phase="building")
    before = snap(w)
    r = w.integrate()
    failed_run(r, cid, "lane run still building", "building")
    check(f"[{cid}] exactly one plain line, no traceback", len(msg_lines(r)) == 1 and "Traceback" not in raw(r),
          msg_lines(r))
    unchanged(w, before, cid)
    check(f"[{cid}] the suite never ran", w.runs() == [], w.runs())
    no_scratch(w, cid)
    lines = w.record_lines()
    check(f"[{cid}] a record line exists with result 'refused' for lane-x",
          bool(lines) and lines[-1].get("result") == "refused" and lines[-1].get("lane") == "lane-x", lines)


def prefix_in(h, commits):
    return any(isinstance(c, str) and len(c) >= 7 and h.startswith(c) for c in commits)


def t13_every_attempt_is_one_record_line():
    """[CP2-T13] Every attempt, merged, rolled back or refused, is one record line with the facts."""
    cid = "CP2-T13"
    w = build(lane_phase="building")
    lane = w.lanes["lane-x"]
    w.integrate()                                            # 1: refused (lane unfinished)
    set_phase(lane, "lane-x-run", "done")
    w.mode("fail")
    w.integrate()                                            # 2: suite failure
    w.mode("pass")
    (w.main / "src/a.js").write_text(A_BASE.replace("line1\n", "line1 main\n"))
    th.git(w.main, "commit", "-qam", "main edits a")
    (lane / "src/a.js").write_text(A_BASE.replace("line1\n", "line1 lane\n"))
    th.git(lane, "commit", "-qam", "lane edits a")
    w.integrate()                                            # 3: conflict
    th.git(lane, "merge", "-q", "-X", "theirs", "-m", "take main", "main")
    lane_tip = g(lane, "rev-parse", "HEAD")
    r = w.integrate()                                        # 4: merged
    need(f"[{cid}] the fourth attempt merged", known(r) and r.returncode == 0, (r.returncode, said(r)[:300]))
    tip = g(w.main, "rev-parse", "HEAD")
    need(f"[{cid}] the record file exists in main's .ratchet", w.record.is_file())
    lines = w.record_lines()
    need(f"[{cid}] the file has exactly four lines", len(lines) == 4 and
         len([x for x in w.record.read_text().splitlines() if x.strip()]) == 4, len(lines))
    check(f"[{cid}] each line has exactly the fields time, lane, run, commits, suite, result, detail",
          all(set(x) == RECORD_FIELDS for x in lines), [sorted(x) for x in lines])
    stamps = []
    for x in lines:
        try:
            stamps.append(dt.datetime.fromisoformat(str(x.get("time")).replace("Z", "+00:00")))
        except ValueError:
            stamps.append(None)
    check(f"[{cid}] every time parses as a timestamp", all(s is not None for s in stamps), [x.get("time") for x in lines])
    check(f"[{cid}] lane is 'lane-x' and run is 'lane-x-run' on every line",
          all(x.get("lane") == "lane-x" and x.get("run") == "lane-x-run" for x in lines), lines)
    check(f"[{cid}] results in order: refused, rolled-back, rolled-back, merged",
          [x.get("result") for x in lines] == ["refused", "rolled-back", "rolled-back", "merged"],
          [x.get("result") for x in lines])
    check(f"[{cid}] suite values in order: not-run, failed, not-run, passed",
          [x.get("suite") for x in lines] == ["not-run", "failed", "not-run", "passed"], [x.get("suite") for x in lines])
    check(f"[{cid}] commits is a list and detail is non-empty plain text on every line",
          all(isinstance(x.get("commits"), list) and isinstance(x.get("detail"), str) and x["detail"].strip()
              for x in lines), lines)
    merged = lines[3] if len(lines) == 4 else {}
    cm = merged.get("commits") or []
    check(f"[{cid}] the merged line's commits include the merge commit that is main's tip", prefix_in(tip, cm), (tip, cm))
    check(f"[{cid}] and the lane's commit that came to main", prefix_in(lane_tip, cm), (lane_tip, cm))


def t14_record_is_append_only_and_out_of_git():
    """[CP2-T14] The record is append-only and stays out of git."""
    cid = "CP2-T14"
    w = build()
    first = json.dumps({"time": "2026-10-01T00:00:00+00:00", "lane": "older", "run": "older-run", "commits": [],
                        "suite": "not-run", "result": "refused", "detail": "an earlier attempt"}) + "\n"
    w.record.write_text(first)
    w.mode("fail")
    w.integrate()
    txt = w.record.read_text() if w.record.exists() else ""
    check(f"[{cid}] after a rolled-back attempt the earlier line is byte-identical and a new line follows it",
          txt.startswith(first) and len(txt.splitlines()) == 2, txt[:300])
    check(f"[{cid}] git status shows no integrations.jsonl", "integrations.jsonl" not in g(w.main, "status", "--porcelain",
                                                                                       "-uall"))
    w.mode("pass")
    r = w.integrate()
    txt2 = w.record.read_text() if w.record.exists() else ""
    check(f"[{cid}] after a merge the earlier lines are unchanged and a third line follows",
          txt2.startswith(txt) and len(txt2.splitlines()) == 3, txt2[:400])
    need(f"[{cid}] the merge succeeded", known(r) and r.returncode == 0, (r.returncode, said(r)[:300]))
    check(f"[{cid}] main is not dirty because of the record", g(w.main, "status", "--porcelain", "-uall") == "")
    check(f"[{cid}] the record is not in the merge commit",
          ".ratchet/integrations.jsonl" not in g(w.main, "ls-tree", "-r", "--name-only", "HEAD").splitlines())


def t15_second_integrate_is_refused_as_busy():
    """[CP2-T15] A second integrate while one is running is refused as busy and the first still finishes."""
    cid = "CP2-T15"
    w = build()
    add_lane(w, "lane-y", {"src/lane_y.js": "export const y = 1;\n"})
    w.mode("block")
    old = g(w.main, "rev-parse", "HEAD")
    p = w.start("lane-x")
    try:
        need(f"[{cid}] the first integrate is running with its suite paused", wait_for(w.ctl / "started", proc=p), p.poll())
        before = snap(w)
        for target in ("lane-x", "lane-y"):
            t0 = time.time()
            r = w.integrate(target, timeout=60)
            took = time.time() - t0
            t = failed_run(r, cid, f"second integrate on {target}", "busy", "already running")
            check(f"[{cid}] {target}: it answered quickly", took < 20, took)
            check(f"[{cid}] {target}: the busy message says to try again when it finishes", "again" in t, said(r)[:300])
            unchanged(w, before, cid, f"{target}: main is exactly as it was")
    finally:
        w.release()
    out, err = p.communicate(timeout=120)
    need(f"[{cid}] after release the first integrate succeeds", p.returncode == 0, (p.returncode, (out + err)[:300]))
    check(f"[{cid}] exactly one merge commit is on main",
          g(w.main, "rev-list", "--merges", "--count", f"{old}..HEAD") == "1", g(w.main, "log", "--oneline", "-5"))
    w.mode("pass")
    first_tip = g(w.main, "rev-parse", "HEAD")
    r = w.integrate("lane-y")
    check(f"[{cid}] an integrate run after that works normally (the lock was released)",
          known(r) and r.returncode == 0 and parents(w.main)[0] == first_tip and (w.main / "src/lane_y.js").is_file(),
          said(r)[:300])


def t16_killed_integrate_does_not_block_the_next():
    """[CP2-T16] A killed integrate leaves nothing that blocks the next one."""
    cid = "CP2-T16"
    w = build()
    w.mode("block")
    old = g(w.main, "rev-parse", "HEAD")
    p = subprocess.Popen(w.argv("lane-x"), cwd=w.main, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=w.denv)
    try:
        need(f"[{cid}] the first integrate is running with its suite paused", wait_for(w.ctl / "started", proc=p), p.poll())
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=30)
    finally:
        w.mode("pass")
        w.release()
        if p.poll() is None:
            p.kill()
    check(f"[{cid}] main was not touched by the killed run", g(w.main, "rev-parse", "HEAD") == old
          and not (w.main / "src/lane.js").exists())
    time.sleep(0.5)
    r = w.integrate()
    check(f"[{cid}] it does not report busy", "busy" not in low(r), said(r)[:300])
    need(f"[{cid}] it merges cleanly", known(r) and r.returncode == 0, (r.returncode, said(r)[:300]))
    no_scratch(w, cid)
    check(f"[{cid}] main's history has exactly one merge commit for the lane",
          g(w.main, "rev-list", "--merges", "--count", f"{old}..HEAD") == "1" and parents(w.main)[0] == old,
          g(w.main, "log", "--oneline", "-5"))


def t17_nothing_is_ever_pushed():
    """[CP2-T17] Nothing is ever pushed, on success or failure."""
    cid = "CP2-T17"
    w = build(remote=True)
    add_lane(w, "lane-f", {"src/lane_f.js": "export const f = 1;\n"})
    add_lane(w, "lane-c", {"src/a.js": A_BASE.replace("line1\n", "line1 lane c\n")})
    (w.main / "src/a.js").write_text(A_BASE.replace("line1\n", "line1 main moved\n"))
    th.git(w.main, "commit", "-qam", "main edits a after lane-c branched")
    marker = w.base / "pushed-marker"
    remote_before = g(w.remote, "for-each-ref")
    tracking = g(w.main, "rev-parse", "refs/remotes/origin/main")
    ahead = lambda: int(g(w.main, "rev-list", "--count", "origin/main..main") or 0)
    ahead0 = ahead()
    r1 = w.integrate("lane-x")
    check(f"[{cid}] the clean lane merged", known(r1) and r1.returncode == 0, (r1.returncode, said(r1)[:300]))
    ahead1 = ahead()
    check(f"[{cid}] local main is ahead of origin/main after the clean merge", ahead1 > ahead0, (ahead0, ahead1))
    w.mode("fail")
    r2 = w.integrate("lane-f")
    check(f"[{cid}] the failing-suite lane was rolled back", known(r2) and r2.returncode != 0, (r2.returncode, said(r2)[:300]))
    w.mode("pass")
    r3 = w.integrate("lane-c")
    check(f"[{cid}] the conflicting lane was refused", known(r3) and r3.returncode != 0, (r3.returncode, said(r3)[:300]))
    check(f"[{cid}] local main moved only for the clean merge", ahead() == ahead1, (ahead1, ahead()))
    check(f"[{cid}] the origin repository's branches and tags are identical to before", g(w.remote, "for-each-ref") ==
          remote_before)
    check(f"[{cid}] the origin's hook never ran", not marker.exists())
    check(f"[{cid}] origin/main in main's remote-tracking refs is unchanged",
          g(w.main, "rev-parse", "refs/remotes/origin/main") == tracking)


def t18_suite_in_the_copy_can_use_installed_dependencies():
    """[CP2-T18] The suite in the copy can use the project's installed dependencies without touching them."""
    cid = "CP2-T18"
    w = build(node_modules=True)
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "integrate with an ignored node_modules", old)
    runs = w.runs()
    need(f"[{cid}] the suite ran once", len(runs) == 1, runs)
    check(f"[{cid}] the suite saw node_modules/probe.txt", runs[0]["probe"] is True, runs[0])
    nm = w.main / "node_modules"
    check(f"[{cid}] main's real node_modules is still a real folder holding probe.txt",
          nm.is_dir() and not nm.is_symlink() and (nm / "probe.txt").read_text() == "probe\n")
    links = []
    for d in (w.main, w.base / "lane-x", w.tmp):
        for root, dirs, files in os.walk(d):
            if ".git" in dirs:
                dirs.remove(".git")
            links += [os.path.join(root, n) for n in dirs + files if os.path.islink(os.path.join(root, n))]
    check(f"[{cid}] no dangling link or scratch folder remains", not links and not scratch_dirs(w), (links, scratch_dirs(w)))
    check(f"[{cid}] the merge result does not contain node_modules",
          not any(x.startswith("node_modules") for x in g(w.main, "ls-tree", "-r", "--name-only", "HEAD").splitlines()))
    check(f"[{cid}] main is clean after the run", g(w.main, "status", "--porcelain", "-uall") == "",
          g(w.main, "status", "--porcelain", "-uall"))


def t19_builder_session_cannot_run_the_real_merge():
    """[CP2-T19] A builder session cannot run the real merge."""
    cid = "CP2-T19"
    w = build()
    before = snap(w)
    r = w.integrate(role="builder")
    t = said(r)
    check(f"[{cid}] non-zero exit", r.returncode != 0, r.returncode)
    check(f"[{cid}] refused as a director-only action", "integrate must be run from the director's session" in t, t[:300])
    unchanged(w, before, cid)
    check(f"[{cid}] the suite never ran", w.runs() == [], w.runs())
    check(f"[{cid}] nothing is recorded as a merge", all(x.get("result") != "merged" for x in w.record_lines()),
          w.record_lines())


def t20_lane_already_in_main_is_a_noop():
    """[CP2-T20] A lane that already is in main is a no-op, not a second merge commit."""
    cid = "CP2-T20"
    w = build()
    th.git(w.main, "merge", "-q", "--no-ff", "-m", "merge lane-x by hand", "lane-x")
    before = snap(w)
    count = g(w.main, "rev-list", "--count", "HEAD")
    w.mode("fail")      # a suite run would show up in the log (and would fail)
    r = w.integrate()
    t = low(r)
    check(f"[{cid}] exit 0 with the engine's own output", known(r) and r.returncode == 0, (r.returncode, said(r)[:300]))
    check(f"[{cid}] the message says 'already merged'", "already merged" in t, said(r)[:300])
    unchanged(w, before, cid, "main's tip is unchanged and nothing else moved")
    check(f"[{cid}] no new merge commit or empty commit", g(w.main, "rev-list", "--count", "HEAD") == count)
    check(f"[{cid}] the suite did not run", w.runs() == [], w.runs())
    lines = w.record_lines()
    check(f"[{cid}] a record line is appended with result 'no-op'", bool(lines) and lines[-1].get("result") == "no-op",
          lines)


def t21_dry_run_still_changes_nothing():
    """[CP2-T21] Guard for CP1: the dry run still changes nothing after the real merge exists."""
    cid = "CP2-T21"
    w = build()
    before = snap(w)
    rec_before = w.record.read_bytes() if w.record.exists() else None
    r = w.integrate("lane-x", "--dry-run")
    t = low(r)
    need(f"[{cid}] the dry run answers with exit 0", known(r) and r.returncode == 0, (r.returncode, said(r)[:300]))
    check(f"[{cid}] it says it is a dry run and nothing was changed", "dry run" in t and "nothing was changed" in t, t[:400])
    unchanged(w, before, cid, "main is exactly as it was")
    check(f"[{cid}] the record file is unchanged (not created, not appended to)",
          (w.record.read_bytes() if w.record.exists() else None) == rec_before)
    check(f"[{cid}] the suite never ran", w.runs() == [], w.runs())
    no_scratch(w, cid)


TESTS = [
    ("CP2-T01", t01_finished_lane_merges_as_one_merge_commit),
    ("CP2-T02", t02_suite_passed_on_the_commit_that_lands_in_a_copy),
    ("CP2-T03", t03_main_does_not_move_until_the_suite_finished_and_passed),
    ("CP2-T04", t04_failing_suite_leaves_main_as_it_was),
    ("CP2-T05", t05_uncommitted_ratchet_file_untouched_by_rollback),
    ("CP2-T06", t06_uncommitted_ratchet_file_carried_through_success),
    ("CP2-T07", t07_conflict_stops_the_merge_and_lists_files),
    ("CP2-T08", t08_main_moved_while_the_suite_ran),
    ("CP2-T09", t09_uncommitted_edit_made_during_the_suite_is_not_overwritten),
    ("CP2-T10", t10_hanging_suite_times_out_and_rolls_back),
    ("CP2-T11", t11_no_suite_command_is_refused),
    ("CP2-T12", t12_unfinished_lane_is_refused_and_recorded),
    ("CP2-T13", t13_every_attempt_is_one_record_line),
    ("CP2-T14", t14_record_is_append_only_and_out_of_git),
    ("CP2-T15", t15_second_integrate_is_refused_as_busy),
    ("CP2-T16", t16_killed_integrate_does_not_block_the_next),
    ("CP2-T17", t17_nothing_is_ever_pushed),
    ("CP2-T18", t18_suite_in_the_copy_can_use_installed_dependencies),
    ("CP2-T19", t19_builder_session_cannot_run_the_real_merge),
    ("CP2-T20", t20_lane_already_in_main_is_a_noop),
    ("CP2-T21", t21_dry_run_still_changes_nothing),
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
        print(f"{cid}")
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


if __name__ == "__main__":
    main()
