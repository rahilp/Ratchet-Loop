#!/usr/bin/env python3
"""
CP1 tests: `integrate <lane> --dry-run` checks a lane and changes nothing.   python3 tests/test_integrate_check.py

The director (RATCHET_ROLE=director) asks Ratchet what merging a finished lane into main would do. These tests drive
the repo's own ratchet.py as a subprocess against throwaway git projects: a main checkout with a committed .ratchet/
(config.json, learnings.md, standards.md, .gitignore), a bare remote 'origin' that main pushes to, and a plain
`git worktree add` lane on branch lane-a, two commits ahead of main, whose own run 'feature-a' is in phase done.
Variants (lane still building, lane or main with uncommitted work, merges in progress, main's own run mid-step, a lane
with nothing new, ...) are built from that.

Every command is run between two snapshots (both checkouts' files byte for byte, branches and HEADs, status, the
worktree list, stash, the bare remote's refs). A refusal and a success are both proven to change nothing. A `git`
shim first on PATH logs every git call so one case can assert that no git command that writes or pushes ran.

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

ROOT = HERE.parent
ENGINE = th.ENGINE
PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP1-[TX]\d+[a-z]?)\]")
REAL_GIT = shutil.which("git")
SUITE_CMD = "python3 tests/run_all.py"
SUBJECTS = ("add feature a", "add tests for a")


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
    """Everything a command printed, with absolute paths cut out (the suite's temp folders are named after the
    suite, so a word match must never run over a path)."""
    return PATH_RE.sub("<path>", (r.stdout or "") + "\n" + (r.stderr or ""))


def known(r) -> bool:
    """The command exists: what came back is the engine's own message, not an argparse usage error."""
    t = said(r)
    return "invalid choice" not in t and "usage:" not in t and "Traceback" not in t


def msg_lines(r) -> list:
    """The non-empty lines a command printed, without the director-session note about missing hooks."""
    return [ln for ln in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines()
            if ln.strip() and not ln.startswith("RATCHET: note:")]


def low(r) -> str:
    return said(r).lower() if known(r) else ""


# ------------------------------------------------------------------------------------------------ fixtures

def jread(p):
    return json.loads(Path(p).read_text())


def jwrite(p, d):
    Path(p).write_text(json.dumps(d, indent=2) + "\n")


def set_phase(d, slug, phase, step="implement"):
    """Put a run's state.json in a phase (the run was started with the real `init`)."""
    p = Path(d) / ".ratchet/runs" / slug / "state.json"
    st = jread(p)
    st["phase"] = phase
    if phase == "building":
        st["current"] = "CP1"
        st["checkpoints"] = {"CP1": {"status": "in_progress", "step": step}}
    jwrite(p, st)


class World:
    """A throwaway main checkout + bare remote + lane worktree, with a private HOME / RATCHET_HOME."""

    def __init__(self, base, main, lane, remote, env):
        self.base, self.main, self.lane, self.remote, self.env = base, main, lane, remote, env
        self.denv = dict(env, RATCHET_ROLE="director")

    def git(self, d, *a):
        return th.git(d, *a)

    def run(self, target, *extra, role="director", env=None, cwd=None, dry=True):
        e = env or (self.denv if role == "director" else self.env)
        args = ["--root", str(self.main), "integrate", str(target), *(["--dry-run"] if dry else []), *extra]
        return subprocess.run([sys.executable, str(ENGINE), *args], cwd=cwd or self.main, capture_output=True,
                              text=True, env=e)


def build(lane_commits="two", lane_phase="done", lane_dirty=None, lane_merge=False, main_dirty=None,
          main_merge=False, main_run=None, main_std_commit=False, regression=SUITE_CMD) -> World:
    """lane_commits: 'two' (project + shared files), 'project' (project files only), 'none' (zero ahead).
    lane_phase: 'done', 'building' or None (the lane has no run). lane_dirty: 'project' or 'ratchet'.
    main_dirty: 'project', 'learnings', 'config' or 'config+project'. main_run: None, 'done', 'building', 'blocked'."""
    env = th.make_env()
    base = Path(_tmp.mkdtemp(prefix="ratchet-integrate-"))
    base = Path(os.path.realpath(base))
    remote, main, lane = base / "origin.git", base / "main", base / "lane-a"
    th.git(base, "init", "-q", "--bare", "-b", "main", str(remote))
    main.mkdir()
    for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        th.git(main, *c)
    (main / "src").mkdir()
    (main / "src/a.js").write_text("export const a = 1;\n")
    (main / "src/b.js").write_text("export const b = 1;\n")
    r = th.rx(main, env, "init", "main work", "--slug", "main-run")
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = jread(main / ".ratchet/config.json")
    cfg["regression_command"] = regression
    jwrite(main / ".ratchet/config.json", cfg)
    (main / ".ratchet/standards.md").write_text("# Standards\n\n1. Keep functions short\n")
    (main / ".ratchet/learnings.md").write_text("# Ratchet learnings\n\n- (2026-10-01) main learning one\n")
    th.git(main, "add", "-A")
    th.git(main, "commit", "-qm", "initial commit")
    th.git(main, "remote", "add", "origin", str(remote))
    th.git(main, "push", "-q", "origin", "main")
    # main's own run: none, or a state in the wanted phase
    if main_run is None:
        shutil.rmtree(main / ".ratchet/runs")
        (main / ".ratchet/active.json").unlink()
    else:
        set_phase(main, "main-run", {"done": "done", "building": "building", "blocked": "blocked"}[main_run])
    # the lane: a plain worktree on its own branch
    th.git(main, "worktree", "add", "-q", "-b", "lane-a", str(lane))
    th.git(main, "branch", "lane-orphan")   # a branch with no worktree
    if main_std_commit:
        (main / ".ratchet/standards.md").write_text("# Standards\n\n1. Keep functions short\n2. No magic numbers\n")
        th.git(main, "add", ".ratchet/standards.md")
        th.git(main, "commit", "-qm", "main adds a standard")
    if lane_commits in ("two", "project"):
        (lane / "src/feature_a.js").write_text("export const featureA = () => 'a';\n")
        if lane_commits == "two":
            with open(lane / ".ratchet/learnings.md", "a") as f:
                f.write("- (2026-10-02) lane learning from feature a\n")
        th.git(lane, "add", "-A")
        th.git(lane, "commit", "-qm", "add feature a")
        (lane / "tests").mkdir(exist_ok=True)
        (lane / "tests/a.test.js").write_text("// tests for feature a\n")
        if lane_commits == "two":
            lc = jread(lane / ".ratchet/config.json")
            lc["max_behavior_attempts"] = 5
            jwrite(lane / ".ratchet/config.json", lc)
        th.git(lane, "add", "-A")
        th.git(lane, "commit", "-qm", "add tests for a")
    if lane_phase:
        r = th.rx(lane, env, "init", "feature a", "--slug", "feature-a")
        assert r.returncode == 0, r.stdout + r.stderr
        set_phase(lane, "feature-a", lane_phase)
    if lane_dirty == "project":
        (lane / "src/b.js").write_text("export const b = 2;\n")
    elif lane_dirty == "ratchet":
        with open(lane / ".ratchet/learnings.md", "a") as f:
            f.write("- (2026-10-03) an uncommitted lane note\n")
        ev = lane / ".ratchet/runs/feature-a/evidence"
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "note.json").write_text("{}\n")
    if lane_merge:
        gd = Path(th.git(lane, "rev-parse", "--absolute-git-dir").stdout.strip())
        (gd / "MERGE_HEAD").write_text(th.git(main, "rev-parse", "HEAD").stdout.strip() + "\n")
    if main_dirty in ("project", "config+project"):
        (main / "src/a.js").write_text("export const a = 42; // developer's own work\n")
    if main_dirty in ("learnings",):
        with open(main / ".ratchet/learnings.md", "a") as f:
            f.write("- (2026-10-04) an uncommitted note on main\n")
    if main_dirty in ("config", "config+project"):
        mc = jread(main / ".ratchet/config.json")
        mc["max_behavior_attempts"] = 9
        jwrite(main / ".ratchet/config.json", mc)
    if main_merge:
        gd = Path(th.git(main, "rev-parse", "--absolute-git-dir").stdout.strip())
        (gd / "MERGE_HEAD").write_text(th.git(lane, "rev-parse", "HEAD").stdout.strip() + "\n")
    return World(base, main, lane, remote, env)


def tree_bytes(d):
    out = {}
    d = Path(d)
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if ".git" in rel.parts or "__pycache__" in rel.parts or p.name.endswith((".lock", ".lock.break")):
            continue
        out[str(rel)] = p.read_bytes() if p.is_file() and not p.is_symlink() else ("<dir>" if p.is_dir() else "<link>")
    return out


def snap(w: World) -> dict:
    """Everything integrate --dry-run must leave alone."""
    g = lambda d, *a: subprocess.run([REAL_GIT, *a], cwd=d, capture_output=True, text=True).stdout
    gdir = lambda d: Path(g(d, "rev-parse", "--absolute-git-dir").strip())
    return {
        "main_head": g(w.main, "rev-parse", "HEAD"),
        "main_refs": g(w.main, "for-each-ref"),
        "main_commits": g(w.main, "rev-list", "--all", "--count"),
        "main_status": g(w.main, "status", "--porcelain", "-uall"),
        "main_stash": g(w.main, "stash", "list"),
        "main_merge_head": (gdir(w.main) / "MERGE_HEAD").exists(),
        "main_tree": tree_bytes(w.main),
        "lane_head": g(w.lane, "rev-parse", "HEAD"),
        "lane_status": g(w.lane, "status", "--porcelain", "-uall"),
        "lane_merge_head": (gdir(w.lane) / "MERGE_HEAD").exists(),
        "lane_tree": tree_bytes(w.lane),
        "worktrees": g(w.main, "worktree", "list", "--porcelain"),
        "remote_refs": g(w.remote, "for-each-ref"),
        "base_entries": sorted(p.name for p in w.base.iterdir()),
    }


def diff_keys(a, b):
    ks = [k for k in a if a[k] != b[k]]
    out = []
    for k in ks:
        if isinstance(a[k], dict):
            out.append(k + ":" + ",".join(sorted(x for x in set(a[k]) | set(b[k]) if a[k].get(x) != b[k].get(x)))[:200])
        else:
            out.append(k)
    return out


def go(w: World, target, cid, **kw):
    """Run integrate --dry-run between two snapshots; check (under case cid) that nothing changed."""
    before = snap(w)
    r = w.run(target, **kw)
    after = snap(w)
    d = diff_keys(before, after)
    check(f"[{cid}] nothing in main, the lane, the worktree list or the remote changed", not d, d)
    return r


def refused(r, cid, what, *needles):
    """The command exists, exited non-zero with exactly one plain line (no traceback) that has every needle."""
    t = low(r)
    need(f"[{cid}] {what}: a non-zero exit with the engine's own message", known(r) and r.returncode != 0 and t,
         (r.returncode, said(r)[:300]))
    check(f"[{cid}] {what}: exactly one plain line, no traceback", len(msg_lines(r)) == 1 and "traceback" not in t,
          msg_lines(r))
    for n in needles:
        alts = n if isinstance(n, tuple) else (n,)
        check(f"[{cid}] {what}: the line says {' / '.join(alts)}", any(a in t for a in alts), said(r)[:300])


def plan_ok(r, cid, what):
    need(f"[{cid}] {what}: exit 0 with the engine's own output", known(r) and r.returncode == 0,
         (r.returncode, said(r)[:300]))
    return low(r)


def lines_with(t, *words):
    return [ln for ln in t.splitlines() if all(w in ln for w in words)]


# ------------------------------------------------------------------------------------------------ cases

def t01_dry_run_says_what_integrate_would_do():
    cid = "CP1-T01"
    w = build()
    r = go(w, w.lane, cid)
    t = plan_ok(r, cid, "dry run on a finished lane")
    check(f"[{cid}] names the lane's run 'feature-a'", "feature-a" in t, t[:300])
    check(f"[{cid}] lists both commit subjects", all(s in t for s in SUBJECTS), t[:400])
    check(f"[{cid}] names the suite command it would run", SUITE_CMD in t, t[:400])
    check(f"[{cid}] says plainly it is a dry run and nothing was changed",
          "dry run" in t and re.search(r"nothing (was |has been )?changed|no changes|changed nothing|not changed", t),
          t[:400])


def t02_lane_named_by_branch():
    cid = "CP1-T02"
    w = build()
    r = go(w, "lane-a", cid)
    t = plan_ok(r, cid, "lane named by its branch")
    check(f"[{cid}] finds the worktree: names run 'feature-a'", "feature-a" in t, t[:300])
    check(f"[{cid}] lists both commit subjects", all(s in t for s in SUBJECTS), t[:400])
    r2 = go(w, w.lane, cid)
    t2 = low(r2)
    check(f"[{cid}] the result is the same as naming the folder (same subjects and run)",
          known(r2) and all(s in t2 for s in SUBJECTS) and "feature-a" in t2 and r2.returncode == r.returncode,
          t2[:300])


def t03_lists_which_shared_files_differ():
    cid = "CP1-T03"
    w = build()
    r = go(w, w.lane, cid)
    t = plan_ok(r, cid, "lane changed learnings.md and config.json")
    check(f"[{cid}] names learnings.md", "learnings.md" in t, t[:400])
    check(f"[{cid}] names config.json", "config.json" in t, t[:400])
    check(f"[{cid}] does not name standards.md (nobody changed it)", "standards.md" not in t, t[:400])
    check(f"[{cid}] does not name .gitignore (nobody changed it)", ".gitignore" not in t, t[:400])
    w2 = build(lane_commits="project")
    r2 = go(w2, w2.lane, cid)
    t2 = plan_ok(r2, cid, "lane changed none of the four shared files")
    check(f"[{cid}] a lane that changed none shows a line saying so",
          any(re.search(r"\b(none|no|nothing)\b", ln) and re.search(r"differ|changed", ln) for ln in t2.splitlines()),
          t2[:400])
    check(f"[{cid}] and names none of the four files as differing",
          not any(n in t2 for n in ("learnings.md", "config.json", "standards.md", ".gitignore")), t2[:400])


def t04_building_lane_is_refused():
    cid = "CP1-T04"
    w = build(lane_phase="building")
    r = go(w, w.lane, cid)
    refused(r, cid, "lane run still building", "building", ("not finished", "isn't finished", "unfinished"),
            "finish", "agent")


def t05_no_run_or_not_a_lane_is_refused():
    cid = "CP1-T05"
    w = build(lane_phase=None)
    r = go(w, w.lane, cid)
    refused(r, cid, "A) worktree with no active run", ("no run", "no active run", "has no run"),
            "folder", "branch")
    r = go(w, "lane-orphan", cid)
    refused(r, cid, "B) branch with no worktree", "lane-orphan", "worktree", "folder", "branch")
    plain = w.base / "plain-folder"
    plain.mkdir()
    (plain / "x.txt").write_text("not a lane\n")
    r = go(w, plain, cid)
    refused(r, cid, "C) plain folder that is not a worktree of this repo", "worktree", "folder", "branch")


def t06_lane_with_uncommitted_project_changes_is_refused():
    cid = "CP1-T06"
    w = build(lane_dirty="project")
    r = go(w, w.lane, cid)
    refused(r, cid, "lane has src/b.js edited and not committed", "src/b.js", "commit", "discard", "agent")


def t07_uncommitted_ratchet_files_in_the_lane_do_not_block():
    cid = "CP1-T07"
    w = build(lane_dirty="ratchet")
    r = go(w, w.lane, cid)
    t = plan_ok(r, cid, "only .ratchet/ is dirty in the lane")
    check(f"[{cid}] the normal plan is shown: run and both commits", "feature-a" in t and all(s in t for s in SUBJECTS),
          t[:400])


def t08_lane_with_merge_in_progress_is_refused():
    cid = "CP1-T08"
    w = build(lane_merge=True)
    r = go(w, w.lane, cid)
    refused(r, cid, "lane has a merge in progress", "merge", "in progress", ("finish", "abort"), "lane")


def t09_main_with_uncommitted_project_work_is_refused():
    cid = "CP1-T09"
    w = build(main_dirty="project")
    r = go(w, w.lane, cid)
    refused(r, cid, "main has src/a.js edited and not committed", "src/a.js", "commit", "stash", "developer")


def t10_main_with_only_ratchet_file_edits_is_carried():
    cid = "CP1-T10"
    w = build(main_dirty="learnings")
    head = th.git(w.main, "rev-parse", "HEAD").stdout
    content = (w.main / ".ratchet/learnings.md").read_bytes()
    r = go(w, w.lane, cid)
    t = plan_ok(r, cid, "main has only learnings.md edited, uncommitted")
    carried = [ln for ln in t.splitlines() if "carr" in ln]
    check(f"[{cid}] lists learnings.md as carried across", any("learnings.md" in ln for ln in carried), t[:500])
    check(f"[{cid}] says it is never committed", re.search(r"never commit|not commit|uncommitted|without commit", t),
          t[:500])
    check(f"[{cid}] main's HEAD is unchanged", th.git(w.main, "rev-parse", "HEAD").stdout == head)
    check(f"[{cid}] the file's bytes are unchanged and it is still uncommitted",
          (w.main / ".ratchet/learnings.md").read_bytes() == content
          and ".ratchet/learnings.md" in th.git(w.main, "status", "--porcelain").stdout)


def t11_main_with_merge_in_progress_is_refused():
    cid = "CP1-T11"
    w = build(main_merge=True)
    r = go(w, w.lane, cid)
    refused(r, cid, "main has a merge in progress", "main", "merge", "in progress", ("finish", "abort"))


def t12_main_whose_own_run_is_mid_step_is_refused():
    cid = "CP1-T12"
    for phase in ("building", "blocked"):
        w = build(main_run=phase)
        before = jread(w.main / ".ratchet/runs/main-run/state.json")
        r = go(w, w.lane, cid)
        refused(r, cid, f"main's own run is {phase}", "main-run", phase, "wait")
        check(f"[{cid}] main's run state is unchanged ({phase})",
              jread(w.main / ".ratchet/runs/main-run/state.json") == before)
    for label, run in (("done", "done"), ("no run at all", None)):
        w = build(main_run=run)
        r = go(w, w.lane, cid)
        t = plan_ok(r, cid, f"main's run is {label}")
        check(f"[{cid}] the plan is shown when main's run is {label}", "feature-a" in t, t[:300])


def t13_lane_with_nothing_new_is_reported():
    cid = "CP1-T13"
    w = build(lane_commits="none")
    r = go(w, w.lane, cid)
    t = low(r)
    need(f"[{cid}] the engine answers (not a usage error)", known(r), said(r)[:300])
    check(f"[{cid}] says there is nothing to bring to main",
          re.search(r"nothing (new )?to (bring|merge|integrate)|no new commits|nothing new|zero commits", t), t[:400])
    check(f"[{cid}] does not pretend a commit list", not any(s in t for s in SUBJECTS), t[:300])


def t14_integrate_only_runs_from_the_directors_session():
    cid = "CP1-T14"
    w = build()
    r = go(w, w.lane, cid, role="builder")
    t = said(r)
    check(f"[{cid}] non-zero exit", r.returncode != 0, r.returncode)
    check(f"[{cid}] says integrate must be run from the director's session",
          "integrate must be run from the director's session" in t, t[:300])
    check(f"[{cid}] no plan is printed", "feature-a" not in t and not any(s in t for s in SUBJECTS), t[:300])


def t15_guard_blocks_a_builder_from_running_integrate():
    cid = "CP1-T15"
    w = build()
    p = w.main

    def guard(cmd):
        payload = {"cwd": str(p), "tool_name": "Bash", "tool_input": {"command": cmd}}
        return subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "claude"], cwd=p, text=True,
                              capture_output=True, env=w.env, input=json.dumps(payload))

    for cmd in ("python3 ratchet.py integrate ../lane-a --dry-run",
                "python3 ratchet.py --root /x integrate lane-a --dry-run"):
        r = guard(cmd)
        check(f"[{cid}] blocked with exit 2: {cmd}", r.returncode == 2, (r.returncode, r.stderr[:200]))
        m = (r.stderr + r.stdout).lower()
        check(f"[{cid}] message says director commands are reserved for the director session: {cmd}",
              "director" in m and "reserved" in m and "session" in m, m[:300])
    for cmd in ('git commit -m "integrate the lane work"',
                'python3 ratchet.py pause --reason "integrate the lane work later"'):
        r = guard(cmd)
        check(f"[{cid}] a command that only mentions integrate in prose is allowed: {cmd}", r.returncode == 0,
              (r.returncode, r.stderr[:200]))


def t16_nothing_changes_for_success_and_every_refusal():
    cid = "CP1-T16"
    scenarios = [
        ("finished lane (dry run succeeds)", {}, 0),
        ("lane still building", {"lane_phase": "building"}, 1),
        ("lane with no run", {"lane_phase": None}, 1),
        ("lane with uncommitted project file", {"lane_dirty": "project"}, 1),
        ("lane with uncommitted .ratchet file", {"lane_dirty": "ratchet"}, 0),
        ("lane with a merge in progress", {"lane_merge": True}, 1),
        ("main with uncommitted project file", {"main_dirty": "project"}, 1),
        ("main with uncommitted learnings.md", {"main_dirty": "learnings"}, 0),
        ("main with uncommitted config.json", {"main_dirty": "config"}, 0),
        ("main with a merge in progress", {"main_merge": True}, 1),
        ("main's run building", {"main_run": "building"}, 1),
        ("main's run blocked", {"main_run": "blocked"}, 1),
        ("lane with nothing new", {"lane_commits": "none"}, 0),
    ]
    for label, kw, bad in scenarios:
        w = build(**kw)
        before = snap(w)
        r = w.run(w.lane)
        after = snap(w)
        d = diff_keys(before, after)
        check(f"[{cid}] {label}: the command ran and answered", known(r) and (r.returncode != 0) == bool(bad),
              (r.returncode, said(r)[:200]))
        check(f"[{cid}] {label}: commits, branches, status, files, worktrees, run state and remote refs are identical",
              not d, d)
        check(f"[{cid}] {label}: no new scratch worktree left behind",
              before["worktrees"] == after["worktrees"] and before["base_entries"] == after["base_entries"])
        check(f"[{cid}] {label}: the remote's refs are untouched", before["remote_refs"] == after["remote_refs"])
    # the other lane is named by branch and by folder: same promise
    w = build()
    before = snap(w)
    r = w.run("lane-a")
    check(f"[{cid}] naming the lane by branch: answered and nothing changed",
          known(r) and r.returncode == 0 and snap(w) == before, said(r)[:200])


WRITERS = {"add", "commit", "merge", "checkout", "switch", "reset", "update-ref", "fetch", "pull", "push", "stash",
           "apply", "clean", "gc", "rebase", "cherry-pick", "restore", "rm", "mv", "tag", "am", "revert", "clone",
           "init", "write-tree", "commit-tree", "read-tree", "update-index", "prune", "repack", "checkout-index",
           "mktag", "merge-tree", "symbolic-ref", "replace", "notes", "bisect", "submodule", "filter-branch"}
GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
BRANCH_READ = {"--list", "-l", "--show-current", "-a", "-r", "-v", "-vv", "--contains", "--merged", "--no-merged",
               "--points-at", "--format"}


def git_sub(argv):
    """(subcommand, its args) from a git argv with the global options skipped."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in GLOBAL_WITH_ARG:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return a, argv[i + 1:]
    return "", []


def writes(argv):
    sub, rest = git_sub(argv)
    if sub in WRITERS:
        return True
    if sub == "branch":
        return not (not rest or all(x.startswith("-") or x in BRANCH_READ for x in rest[:1]))
    if sub == "worktree":
        return not (rest and rest[0] == "list")
    if sub == "config":
        return not (rest and rest[0] in ("--get", "--get-all", "--get-regexp", "--list", "-l"))
    if sub == "remote":
        return bool(rest) and rest[0] not in ("-v", "show", "get-url")
    if sub in ("hash-object",):
        return "-w" in rest
    return False


def t17_no_git_command_that_writes_or_pushes_is_run():
    cid = "CP1-T17"
    w = build()
    shim = Path(_tmp.mkdtemp(prefix="ratchet-gitshim-"))
    log = shim / "git.log"
    sh = shim / "git"
    sh.write_text(f'#!/bin/sh\nprintf \'%s\\t\' "$@" >> "{log}"\nprintf \'\\n\' >> "{log}"\nexec "{REAL_GIT}" "$@"\n')
    sh.chmod(sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(w.denv, PATH=str(shim) + os.pathsep + w.env["PATH"])
    before = snap(w)
    r = w.run(w.lane, env=env)
    calls = [ln.rstrip("\t").split("\t") for ln in (log.read_text().splitlines() if log.exists() else [])]
    need(f"[{cid}] the dry run on a finished lane succeeds", known(r) and r.returncode == 0,
         (r.returncode, said(r)[:300]))
    check(f"[{cid}] the shim saw git being called (read-only calls were made)", len(calls) > 0, calls[:3])
    bad = [c for c in calls if writes(c)]
    check(f"[{cid}] no add, commit, merge, checkout, switch, reset, update-ref, branch with a name, worktree "
          "add/remove, fetch, pull, push, stash, apply, clean or gc was run", not bad, bad[:3])
    check(f"[{cid}] nothing changed on disk", snap(w) == before)


def t18_docs_describe_the_integrate_command():
    cid = "CP1-T18"
    docs = {"README.md": ROOT / "README.md",
            "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
            "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md"}
    for name, p in docs.items():
        lines = p.read_text().splitlines()
        anchors = [i for i, ln in enumerate(lines) if "director-enroll" in ln]
        hit = False
        for i in anchors:
            win = "\n".join(lines[max(0, i - 6): i + 7])
            if re.search(r"\bintegrate\b", win) and "--dry-run" in win and "director" in win:
                hit = True
                break
        check(f"[{cid}] {name} names `integrate` and `--dry-run` as a director command next to director-enroll", hit,
              f"{len(anchors)} director-enroll lines")


def t19_shared_file_changed_only_on_main_is_listed():
    cid = "CP1-T19"
    w = build(lane_commits="project", main_std_commit=True)
    head = th.git(w.main, "rev-parse", "HEAD").stdout
    content = (w.main / ".ratchet/standards.md").read_bytes()
    r = go(w, w.lane, cid)
    t = plan_ok(r, cid, "only main changed standards.md since the lane branched")
    differ = [ln for ln in t.splitlines() if "standards.md" in ln]
    check(f"[{cid}] names standards.md as differing", bool(differ), t[:400])
    check(f"[{cid}] does not name learnings.md, config.json or .gitignore",
          not any(n in t for n in ("learnings.md", "config.json", ".gitignore")), t[:400])
    check(f"[{cid}] main's HEAD and the file are unchanged",
          th.git(w.main, "rev-parse", "HEAD").stdout == head and (w.main / ".ratchet/standards.md").read_bytes() == content)


def t20_mains_uncommitted_config_is_carried_not_refused():
    cid = "CP1-T20"
    w = build(main_dirty="config")
    head = th.git(w.main, "rev-parse", "HEAD").stdout
    count = th.git(w.main, "rev-list", "--count", "HEAD").stdout
    content = (w.main / ".ratchet/config.json").read_bytes()
    r = go(w, w.lane, cid)
    t = plan_ok(r, cid, "main has only config.json edited, uncommitted")
    carried = [ln for ln in t.splitlines() if "carr" in ln]
    check(f"[{cid}] lists .ratchet/config.json as carried across", any("config.json" in ln for ln in carried), t[:500])
    check(f"[{cid}] says it is never committed", re.search(r"never commit|not commit|uncommitted|without commit", t),
          t[:500])
    check(f"[{cid}] main's HEAD and commit count are unchanged",
          th.git(w.main, "rev-parse", "HEAD").stdout == head and th.git(w.main, "rev-list", "--count", "HEAD").stdout == count)
    check(f"[{cid}] config.json still holds the uncommitted value byte for byte, still uncommitted",
          (w.main / ".ratchet/config.json").read_bytes() == content
          and json.loads(content)["max_behavior_attempts"] == 9
          and ".ratchet/config.json" in th.git(w.main, "status", "--porcelain").stdout)
    w2 = build(main_dirty="config+project")
    r2 = go(w2, w2.lane, cid)
    refused(r2, cid, "config.json and a project file both edited on main", "src/a.js")
    check(f"[{cid}] the refusal does not name config.json as the reason", "config.json" not in low(r2), said(r2)[:300])


TESTS = [
    ("CP1-T01", t01_dry_run_says_what_integrate_would_do),
    ("CP1-T02", t02_lane_named_by_branch),
    ("CP1-T03", t03_lists_which_shared_files_differ),
    ("CP1-T04", t04_building_lane_is_refused),
    ("CP1-T05", t05_no_run_or_not_a_lane_is_refused),
    ("CP1-T06", t06_lane_with_uncommitted_project_changes_is_refused),
    ("CP1-T07", t07_uncommitted_ratchet_files_in_the_lane_do_not_block),
    ("CP1-T08", t08_lane_with_merge_in_progress_is_refused),
    ("CP1-T09", t09_main_with_uncommitted_project_work_is_refused),
    ("CP1-T10", t10_main_with_only_ratchet_file_edits_is_carried),
    ("CP1-T11", t11_main_with_merge_in_progress_is_refused),
    ("CP1-T12", t12_main_whose_own_run_is_mid_step_is_refused),
    ("CP1-T13", t13_lane_with_nothing_new_is_reported),
    ("CP1-T14", t14_integrate_only_runs_from_the_directors_session),
    ("CP1-T15", t15_guard_blocks_a_builder_from_running_integrate),
    ("CP1-T16", t16_nothing_changes_for_success_and_every_refusal),
    ("CP1-T17", t17_no_git_command_that_writes_or_pushes_is_run),
    ("CP1-T18", t18_docs_describe_the_integrate_command),
    ("CP1-T19", t19_shared_file_changed_only_on_main_is_listed),
    ("CP1-T20", t20_mains_uncommitted_config_is_carried_not_refused),
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


if __name__ == "__main__":
    main()
