#!/usr/bin/env python3
"""
CP3 tests: `integrate <lane>` merges Ratchet's own shared files by Ratchet's rules.   python3 tests/test_integrate_files.py

The director (RATCHET_ROLE=director) merges a finished lane into the main checkout. When the lane and main both
changed Ratchet's four shared files (.ratchet/config.json, learnings.md, standards.md, .gitignore), integrate merges
them itself: config.json key by key against the merge base (one side changed takes that side, both changed keeps
main's, the lane-local reviewer and provider keys always keep main's, guardrail checks from both sides, every lane
setting not taken is listed in the output and the record), learnings.md and .gitignore as the lines of both sides,
standards.md as the changed side (both sides changed stops integrate). Main's uncommitted changes to those files are
carried across the merge, re-applied by the same rules and left uncommitted.

Fixture: a throwaway git project in a temp dir, following tests/test_integrate_merge.py: a main checkout on branch
main with a committed .ratchet/ (config.json, learnings.md with three lessons, standards.md with five rules,
.gitignore, plus notes.txt as a fifth tracked file under .ratchet/), src/feature.txt, src/shared.txt and src/notes.txt;
a lane worktree 'lane-a' (run 'lane-a-run', phase done) with its own commit; main one commit ahead (src/main_only.txt
and the case's own edits). The suite is a probe script outside the repo that passes unless the lane's src/feature.txt
says BREAK. Assertions are on git facts, file bytes, the printed message and .ratchet/integrations.jsonl.

Contract the cases pin for the implementer: the integrations.jsonl line of a merged attempt carries "not_taken", the
list of config.json keys (strings) the lane changed and main kept; it is [] when none. The printed output names every
such key too.

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

ENGINE = th.ENGINE
REPO = HERE.parent
PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP3-[TX]\d+[a-z]?)\]")
REAL_GIT = shutil.which("git")

SHARED = [".ratchet/config.json", ".ratchet/standards.md", ".ratchet/learnings.md", ".ratchet/.gitignore"]
LANE_LOCAL = ["review_depth", "reviewers", "providers", "host_provider", "host_models", "role_models", "ui_reviewers",
              "approver", "human_review", "max_adversarial_rounds", "max_adversarial_rounds_run",
              "unavailable_ttl_hours", "probe_providers", "probe_ttl_minutes", "probe_timeout_sec",
              "quota_retest_minutes", "provider_share"]
CONFLICT_RE = re.compile(rb"^(<{7}|={7}|>{7})", re.M)

LEARN_BASE = ("# Ratchet learnings\n\n- (2026-10-01) base lesson one\n- (2026-10-01) base lesson two\n"
              "- (2026-10-01) base lesson three\n")
LEARN_LESSONS = ["- (2026-10-01) base lesson one", "- (2026-10-01) base lesson two", "- (2026-10-01) base lesson three"]
STD_RULES = ["1. Keep functions short", "2. Name things plainly", "3. Test every rule", "4. No secrets in logs",
             "5. Docs match code"]
STD_BASE = "# Standards\n\n" + "".join(r + "\n" for r in STD_RULES)
GI_INIT = "*\n!.gitignore\n!config.json\n!standards.md\n!learnings.md\n"
FEATURE_BASE = "feature: base\n"
FEATURE_LANE = "feature: lane work\n"
FEATURE_BREAK = "feature: BREAK\n"
SHARED_BASE = "shared line 1\nshared line 2\n"
NOTES_BASE = "notes: base\n"
CHECK_A = {"kind": "a", "command": "python3 check_a.py", "title": "check A", "added": "2026-10-01"}
CHECK_B = {"kind": "b", "command": "python3 check_b.py", "title": "check B", "added": "2026-10-01"}
CHECK_MAIN = {"kind": "m", "command": "python3 check_main.py", "title": "check main", "added": "2026-10-02"}
CHECK_LANE = {"kind": "l", "command": "python3 check_lane.py", "title": "check lane", "added": "2026-10-02"}
DEPTH_A = {"low": ["any-both"], "normal": ["any-standards", "any-correctness"],
           "high": ["any-standards", "any-correctness", "any-correctness", "any-standards"]}
DEPTH_B = {"low": ["any-both"], "normal": ["any-correctness", "any-both"],
           "high": ["any-standards", "any-correctness", "any-correctness", "any-standards"]}
DEPTH_C = {"low": ["any-correctness"], "normal": ["any-both"], "high": ["any-both", "any-both"]}
PROVIDERS_LANE = [{"id": "codex", "preset": "codex", "paid_ok_at": "2026-10-02T10:00:00+00:00"},
                  {"id": "gemini", "preset": "gemini"}]


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


def raw(r) -> str:
    return (r.stdout or "") + "\n" + (r.stderr or "")


def said(r) -> str:
    """Everything a command printed, with absolute paths cut out (temp folders are named after the suite) and the
    engine's 'RATCHET: note:' lines (the hooks-not-installed note) left out."""
    kept = [ln for ln in raw(r).splitlines() if ln.strip() and not ln.startswith("RATCHET: note:")]
    return PATH_RE.sub("<path>", "\n".join(kept))


def known(r) -> bool:
    """The command exists: what came back is the engine's own message, not a usage error or a crash."""
    t = said(r)
    return "invalid choice" not in t and "usage:" not in t and "Traceback" not in t


def low(r) -> str:
    return said(r).lower() if known(r) else ""


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
    jwrite(p, st)


def parents(d, rev="HEAD") -> list:
    return g(d, "rev-list", "--parents", "-n", "1", rev).split()[1:]


def porcelain_lines(d) -> list:
    """git status --porcelain lines with the leading space kept (g() strips it)."""
    out = subprocess.run([REAL_GIT, "status", "--porcelain"], cwd=d, capture_output=True, text=True).stdout
    return out.splitlines()


def show(d, rev, rel) -> str:
    return subprocess.run([REAL_GIT, "show", f"{rev}:{rel}"], cwd=d, capture_output=True, text=True).stdout


def cfg_at(d, rev=None) -> dict:
    """config.json as settings: the working tree's, or the committed one at `rev`."""
    if rev is None:
        return jread(Path(d) / ".ratchet/config.json")
    return json.loads(show(d, rev, ".ratchet/config.json"))


def lines_of(text: str) -> list:
    return [ln for ln in text.splitlines() if ln.strip()]


def conflict_markers(d) -> list:
    out = []
    for f in g(d, "ls-files").splitlines():
        p = Path(d) / f
        if p.is_file() and CONFLICT_RE.search(p.read_bytes()):
            out.append(f)
    return out


# ------------------------------------------------------------------------------------------------ fixtures

SUITE_PY = r'''
import json, os, subprocess, sys
BASE = %(base)r
CTL = os.path.join(BASE, "ctl")


def g(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True).stdout.strip()


try:
    mode = open(os.path.join(CTL, "mode")).read().strip()
except OSError:
    mode = "pass"
cwd = os.getcwd()
feat = os.path.join(cwd, "src", "feature.txt")
text = open(feat).read() if os.path.exists(feat) else ""
rec = {"cwd": os.path.realpath(cwd), "head": g(cwd, "rev-parse", "HEAD"), "feature": text, "mode": mode}
with open(os.path.join(CTL, "log.jsonl"), "a") as f:
    f.write(json.dumps(rec) + "\n")
if mode == "fail" or "BREAK" in text:
    print("SUITE-BOOM-LINE")
    sys.exit(1)
sys.exit(0)
'''

PLAN_CPS = [{"id": "CP1", "title": "Price helper", "summary": "s", "done_when": ["works"],
             "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
             "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}]


class W:
    """A throwaway project: main checkout, lane worktrees beside it, a suite probe and a private HOME."""

    def __init__(self, base, main, env):
        self.base, self.main, self.env = base, main, env
        self.denv = dict(env, RATCHET_ROLE="director")
        self.ctl = base / "ctl"
        self.tmp = base / "tmp"
        self.lanes = {}
        self.remote = None

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

    def integrate(self, target="lane-a", *extra, timeout=150):
        return subprocess.run(self.argv(target, *extra), cwd=self.main, capture_output=True, text=True,
                              env=self.denv, timeout=timeout)

    def record_lines(self) -> list:
        if not self.record.exists():
            return []
        return [json.loads(x) for x in self.record.read_text().splitlines() if x.strip()]

    def last(self) -> dict:
        lines = self.record_lines()
        return lines[-1] if lines else {}


def edit_cfg(d, fn):
    """Change config.json in the checkout `d` with fn(cfg) (in place)."""
    p = Path(d) / ".ratchet/config.json"
    c = jread(p)
    fn(c)
    jwrite(p, c)


def set_cfg(d, **kv):
    edit_cfg(d, lambda c: c.update(kv))


def edit_text(d, rel, fn):
    p = Path(d) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text() if p.exists() else ""
    p.write_text(fn(old))


def append_line(d, rel, line):
    edit_text(d, rel, lambda t: t + ("" if not t or t.endswith("\n") else "\n") + line + "\n")


def reword(d, rel, old, new):
    def fn(t):
        assert old in t, (rel, old)
        return t.replace(old, new)
    edit_text(d, rel, fn)


def commit_all(d, msg):
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", msg)


def add_lane(w: W, name, edits, base_ref="HEAD", phase="done"):
    """A plain git worktree on its own branch with its own run ('<name>-run') and one commit of `edits` (a callable
    taking the lane folder, or a {path: text} dict)."""
    lane = w.base / name
    th.git(w.main, "worktree", "add", "-q", "-b", name, str(lane), base_ref)
    r = th.rx(lane, w.env, "init", f"{name} work", "--slug", f"{name}-run")
    assert r.returncode == 0, r.stdout + r.stderr
    if phase:
        set_phase(lane, f"{name}-run", phase)
    if callable(edits):
        edits(lane)
    else:
        for rel, text in (edits or {}).items():
            (lane / rel).parent.mkdir(parents=True, exist_ok=True)
            (lane / rel).write_text(text)
    commit_all(lane, f"{name}: work")
    w.lanes[name] = lane
    return lane


def build(main=None, lane=None, dirty=None, remote=False, no_gitignore=False, main_ahead=True, base_edit=None) -> W:
    """main / lane / dirty: callables that edit the main checkout (committed as main's own commit), the lane (its
    commit) and main's working tree (left uncommitted), in that order. base_edit: a callable that edits the base
    commit both sides branch from. The lane always changes src/feature.txt."""
    env = th.make_env()
    base = Path(os.path.realpath(_tmp.mkdtemp(prefix="ratchet-files-")))
    mainp = base / "main"
    (base / "ctl").mkdir()
    (base / "tmp").mkdir()
    (base / "transcripts").mkdir()
    env["TMPDIR"] = str(base / "tmp")
    (base / "suite.py").write_text(SUITE_PY % {"base": str(base)})
    mainp.mkdir()
    for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        th.git(mainp, *c)
    (mainp / "src").mkdir()
    (mainp / "src/feature.txt").write_text(FEATURE_BASE)
    (mainp / "src/shared.txt").write_text(SHARED_BASE)
    (mainp / "src/notes.txt").write_text(NOTES_BASE)
    (mainp / ".gitignore").write_text("node_modules/\n")
    r = th.rx(mainp, env, "init", "main work", "--slug", "main-run")
    assert r.returncode == 0, r.stdout + r.stderr
    (mainp / ".git/info/exclude").write_text(".ratchet/*\n!.ratchet/.gitignore\n!.ratchet/config.json\n"
                                             "!.ratchet/standards.md\n!.ratchet/learnings.md\n!.ratchet/notes.txt\n")
    set_cfg(mainp, regression_command=f"python3 {base / 'suite.py'}", test_timeout_sec=60, max_ui_rounds=3,
            ui_reviewers=["claude", "gemini"], review_depth=DEPTH_A, checks=[CHECK_A], commit_per_checkpoint=True)
    (mainp / ".ratchet/standards.md").write_text(STD_BASE)
    (mainp / ".ratchet/learnings.md").write_text(LEARN_BASE)
    (mainp / ".ratchet/notes.txt").write_text(NOTES_BASE)
    if no_gitignore:
        (mainp / ".ratchet/.gitignore").unlink()
    else:
        (mainp / ".ratchet/.gitignore").write_text(GI_INIT)
    if base_edit:
        base_edit(mainp)
    th.git(mainp, "add", "-A")
    th.git(mainp, "add", "-f", ".ratchet/notes.txt")
    th.git(mainp, "commit", "-qm", "initial commit")
    shutil.rmtree(mainp / ".ratchet/runs")
    (mainp / ".ratchet/active.json").unlink()
    w = W(base, mainp, env)
    if remote:
        rem = base / "origin.git"
        th.git(base, "init", "-q", "--bare", "-b", "main", str(rem))
        th.git(mainp, "remote", "add", "origin", str(rem))
        th.git(mainp, "push", "-q", "origin", "main")
        hook = rem / "hooks/pre-receive"
        hook.write_text(f"#!/bin/sh\ntouch '{base / 'pushed-marker'}'\n")
        hook.chmod(0o755)
        w.remote = rem

    def lane_edits(d):
        (d / "src/feature.txt").write_text(FEATURE_LANE)
        if lane:
            lane(d)

    add_lane(w, "lane-a", lane_edits)
    if main_ahead or main:
        (mainp / "src/main_only.txt").write_text("main only\n")
        if main:
            main(mainp)
        commit_all(mainp, "main: own work")
    if dirty:
        dirty(mainp)
    w.mode("pass")
    return w


def say(d, env, text, session="s-owner"):
    """What happens when the user types a message: the agent CLI writes it to its transcript and calls the prompt hook."""
    tp = Path(env["TMPDIR"]).parent / "transcripts" / f"{Path(d).name}-{session}.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    return subprocess.run([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=env,
                          input=json.dumps({"cwd": str(d), "prompt": text, "session_id": session,
                                            "transcript_path": str(tp)}))


def guard(d, env, command):
    """What the guard says about a Bash command in the checkout `d`: (exit code, output)."""
    r = subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "claude"], cwd=d, text=True,
                       capture_output=True, env=env,
                       input=json.dumps({"cwd": str(d), "tool_name": "Bash", "tool_input": {"command": command}}))
    return r.returncode, said(r)


def approved_run(d, env, slug):
    """Give the checkout `d` its own run with an approved one-step plan: it sits at CP1's step boundary (step
    'tests', clean tree), the one building state integrate and the merge guard allow."""
    r = th.rx(d, env, "init", f"{slug} feature", "--slug", slug)
    assert r.returncode == 0, r.stdout + r.stderr
    run = Path(d) / ".ratchet/runs" / slug
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": PLAN_CPS}))
    (run / "tests").mkdir(exist_ok=True)
    (run / "tests/CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    r = th.rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stdout + r.stderr
    r = th.rx(d, env, "ask", "approve-plan")
    assert r.returncode == 0, r.stdout + r.stderr
    say(d, env, "yes, go ahead")
    r = th.rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stdout + r.stderr
    st = jread(run / "state.json")
    assert st["phase"] == "building" and st["checkpoints"]["CP1"].get("step") == "tests", st
    assert g(d, "status", "--porcelain") == "", g(d, "status", "--porcelain")
    return run


def gates_of(run: Path) -> dict:
    st = jread(run / "state.json")
    return {cid: {k: v for k, v in cp.items() if k in ("gates", "status", "step", "passed")}
            for cid, cp in (st.get("checkpoints") or {}).items()}


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


def saved_copies(w: W, marker: str) -> list:
    """Files outside the working tree (under main's .git, the lanes' .git entries or the private TMPDIR) that hold the
    bytes of an uncommitted edit: a saved copy integrate left behind."""
    hits = []
    roots = [w.main / ".git", w.tmp]
    for root in roots:
        for r, dirs, files in os.walk(root):
            if Path(r).name == "objects":
                dirs[:] = []
                continue
            for f in files:
                p = Path(r) / f
                try:
                    if p.is_file() and marker.encode() in p.read_bytes():
                        hits.append(str(p))
                except OSError:
                    pass
    return hits


def no_scratch(w: W, cid, what="", lanes=("lane-a",)):
    exp = {os.path.realpath(w.main)} | {os.path.realpath(w.base / n) for n in lanes}
    check(f"[{cid}] {what}no scratch worktree remains: git worktree list shows only main and the lane",
          worktree_paths(w) == exp, sorted(worktree_paths(w) ^ exp))
    check(f"[{cid}] {what}no leftover scratch folder", not scratch_dirs(w), scratch_dirs(w))


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
         (r.returncode, said(r)[:400]))
    tip = g(w.main, "rev-parse", "HEAD")
    check(f"[{cid}] {what}: main's tip is a merge commit on top of the old tip",
          tip != old_head and len(parents(w.main)) == 2 and parents(w.main)[0] == old_head, parents(w.main))
    return tip


def only_the_merge(w: W, cid, old_head, lane="lane-a"):
    """Main gained the merge commit and the lane's own commits, nothing else (no commit of Ratchet's files)."""
    tip = g(w.main, "rev-parse", "HEAD")
    new = set(g(w.main, "rev-list", f"{old_head}..HEAD").split())
    lane_commits = set(g(w.main, "rev-list", f"{old_head}..{lane}").split())
    check(f"[{cid}] the only new commit on main is the merge (plus the lane's own commits): no commit of Ratchet's files",
          new == {tip} | lane_commits and g(w.main, "rev-list", "--first-parent", "--count", f"{old_head}..HEAD") == "1",
          (sorted(new), sorted(lane_commits)))


def not_taken(line: dict) -> list:
    v = line.get("not_taken")
    return [str(x) for x in v] if isinstance(v, list) else []


def listed(w: W, r, cid, key, what=""):
    """The lane setting `key` is named as not taken, in the output and in the newest record line."""
    line = w.last()
    check(f"[{cid}] {what}the output names '{key}' as a lane setting not taken", key in raw(r), said(r)[:600])
    check(f"[{cid}] {what}the record's not_taken list names '{key}'",
          line.get("result") == "merged" and any(key in x for x in not_taken(line)), line)


def remote_state(rem: Path) -> tuple:
    return (g(rem, "for-each-ref"), g(rem, "count-objects", "-v"))


# ------------------------------------------------------------------------------------------------ cases

def t01_config_conflict_merged_key_by_key():
    """[CP3-T01] A lane whose run config conflicts with main's integrates with no git command typed, merged key by key."""
    cid = "CP3-T01"
    w = build(main=lambda d: set_cfg(d, test_timeout_sec=600, max_ui_rounds=5, ui_reviewers=["gemini", "claude"]),
              lane=lambda d: set_cfg(d, test_timeout_sec=900, review_depth=DEPTH_B))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "config.json changed on both sides", old)
    check(f"[{cid}] the merge commit names the lane", "lane-a" in g(w.main, "log", "-1", "--format=%B"))
    c = cfg_at(w.main)
    check(f"[{cid}] test_timeout_sec is 600: both sides changed it, main's is kept", c.get("test_timeout_sec") == 600,
          c.get("test_timeout_sec"))
    check(f"[{cid}] max_ui_rounds is 5: a main-only change is kept", c.get("max_ui_rounds") == 5, c.get("max_ui_rounds"))
    check(f"[{cid}] ui_reviewers keeps main's order (lane-local key)", c.get("ui_reviewers") == ["gemini", "claude"],
          c.get("ui_reviewers"))
    check(f"[{cid}] review_depth keeps main's split (lane-local key)", c.get("review_depth") == DEPTH_A,
          c.get("review_depth"))
    check(f"[{cid}] the committed config.json on main's tip says the same", cfg_at(w.main, "HEAD") == c)
    check(f"[{cid}] the lane's src/feature.txt change is on main",
          (w.main / "src/feature.txt").read_text() == FEATURE_LANE and show(w.main, "HEAD", "src/feature.txt") == FEATURE_LANE)
    listed(w, r, cid, "test_timeout_sec")
    listed(w, r, cid, "review_depth")
    check(f"[{cid}] no conflict markers anywhere in main", conflict_markers(w.main) == [], conflict_markers(w.main))
    check(f"[{cid}] main's working tree is clean afterwards", porcelain_lines(w.main) == [], porcelain_lines(w.main))


def t02_lane_local_keys_changed_only_on_the_lane_keep_mains():
    """[CP3-T02] A lane-local reviewer or provider setting changed only on the lane keeps main's value and is listed."""
    cid = "CP3-T02"
    w = build(lane=lambda d: set_cfg(d, reviewers={"lane-only": {"kind": "agent", "lens": "both", "vendor": "host"}},
                                     providers=PROVIDERS_LANE, review_depth=DEPTH_C, probe_providers=False,
                                     quota_retest_minutes=7))
    old = g(w.main, "rev-parse", "HEAD")
    before = cfg_at(w.main)
    r = w.integrate()
    merged_ok(w, r, cid, "lane-only change to lane-local keys", old)
    c = cfg_at(w.main)
    for k in ("reviewers", "providers", "review_depth", "probe_providers", "quota_retest_minutes"):
        check(f"[{cid}] main's '{k}' is exactly as before", c.get(k, "<absent>") == before.get(k, "<absent>"),
              (c.get(k, "<absent>"), before.get(k, "<absent>")))
        listed(w, r, cid, k)
    check(f"[{cid}] no paid_ok_at consent stamp was added to main's config",
          "paid_ok_at" not in (w.main / ".ratchet/config.json").read_text()
          and "paid_ok_at" not in show(w.main, "HEAD", ".ratchet/config.json"))
    check(f"[{cid}] the lane's src/feature.txt change is on main", (w.main / "src/feature.txt").read_text() == FEATURE_LANE)


def t18_project_wide_setting_changed_only_in_the_lane_is_taken():
    """[CP3-T18] A project-wide setting changed only in the lane is taken."""
    cid = "CP3-T18"
    w = build(lane=lambda d: set_cfg(d, test_timeout_sec=900, commit_per_checkpoint=False, lane_note="from the lane"))
    old = g(w.main, "rev-parse", "HEAD")
    before = cfg_at(w.main)
    r = w.integrate()
    merged_ok(w, r, cid, "lane-only project-wide changes", old)
    c = cfg_at(w.main)
    check(f"[{cid}] test_timeout_sec is now 900", c.get("test_timeout_sec") == 900, c.get("test_timeout_sec"))
    check(f"[{cid}] commit_per_checkpoint is now false", c.get("commit_per_checkpoint") is False, c.get("commit_per_checkpoint"))
    check(f"[{cid}] the unknown key lane_note was taken (counts as project-wide)", c.get("lane_note") == "from the lane",
          c.get("lane_note"))
    taken = ("test_timeout_sec", "commit_per_checkpoint", "lane_note")
    others = {k: v for k, v in c.items() if k not in taken}
    before_others = {k: v for k, v in before.items() if k not in taken}
    check(f"[{cid}] every other key is as before", others == before_others and before.get("test_timeout_sec") == 60,
          diff_keys(others, before_others) if set(others) == set(before_others) else (sorted(others), sorted(before_others)))
    line = w.last()
    check(f"[{cid}] none of the three is listed as not taken (record)",
          line.get("result") == "merged" and not any(k in x for x in not_taken(line)
                                                     for k in ("test_timeout_sec", "commit_per_checkpoint", "lane_note")),
          line)
    out = raw(r)
    check(f"[{cid}] the output does not claim any of the three was left out",
          not re.search(r"not taken|left out|kept main|dropped", out, re.I)
          or not any(k in out for k in ("test_timeout_sec", "commit_per_checkpoint", "lane_note")), said(r)[:500])


def t19_guardrail_check_from_the_lane_survives_mains_additions():
    """[CP3-T19] A guardrail check the lane added survives even when main added checks of its own."""
    cid = "CP3-T19"
    w = build(main=lambda d: set_cfg(d, checks=[CHECK_A, CHECK_MAIN]),
              lane=lambda d: set_cfg(d, checks=[CHECK_A, CHECK_LANE]))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "checks added on both sides", old)
    cmds = [x.get("command") for x in cfg_at(w.main).get("checks") or []]
    check(f"[{cid}] main's checks hold all three commands, each once, main's first, the lane's kept",
          cmds == ["python3 check_a.py", "python3 check_main.py", "python3 check_lane.py"], cmds)
    check(f"[{cid}] the suite ran on the merged code", len(w.runs()) == 1, len(w.runs()))
    check(f"[{cid}] no conflict markers in main", conflict_markers(w.main) == [], conflict_markers(w.main))


def t20_guardrail_checks_matched_by_command():
    """[CP3-T20] Guardrail checks are matched by command, so none is duplicated or lost."""
    cid = "CP3-T20"
    a_main = dict(CHECK_A, title="check A (main wording)")
    a_lane = dict(CHECK_A, title="check A (lane wording)")
    # both sides branch from a base with two checks (A then B); main reorders and rewords A, the lane rewords A
    # differently and adds check_lane
    w = build(base_edit=lambda d: set_cfg(d, checks=[CHECK_A, CHECK_B]),
              main=lambda d: set_cfg(d, checks=[CHECK_B, a_main]),
              lane=lambda d: set_cfg(d, checks=[a_lane, CHECK_B, CHECK_LANE]))
    base_cmds = [x["command"] for x in cfg_at(w.main, g(w.main, "merge-base", "HEAD", "lane-a")).get("checks")]
    need(f"[{cid}] (setup) the merge base holds check_a and check_b; main and the lane each start from it",
         base_cmds == ["python3 check_a.py", "python3 check_b.py"], base_cmds)
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "checks reworded on both sides", old)
    checks = cfg_at(w.main).get("checks") or []
    cmds = [x.get("command") for x in checks]
    check(f"[{cid}] one entry for check_a, one for check_b, one for check_lane: none dropped, none duplicated",
          sorted(cmds) == ["python3 check_a.py", "python3 check_b.py", "python3 check_lane.py"], cmds)
    a = next((x for x in checks if x.get("command") == "python3 check_a.py"), {})
    check(f"[{cid}] check_a is main's version (main's wording)", a.get("title") == "check A (main wording)", a)
    check(f"[{cid}] main's entries come first, in main's order", cmds[:2] == ["python3 check_b.py", "python3 check_a.py"], cmds)
    line = w.last()
    check(f"[{cid}] the lane's differing wording for check_a is listed as not taken (record)",
          line.get("result") == "merged" and any("check_a" in x or "checks" in x for x in not_taken(line)), line)
    check(f"[{cid}] and named in the output", "check_a" in raw(r) or "checks" in raw(r), said(r)[:500])


def t21_every_lane_local_key_keeps_mains_value():
    """[CP3-T21] Every named lane-local key keeps main's value when only the lane changed it."""
    cid = "CP3-T21"
    lane_values = {"review_depth": DEPTH_C, "reviewers": {"lane-r": {"kind": "agent", "lens": "both", "vendor": "host"}},
                   "providers": PROVIDERS_LANE, "host_provider": "openai",
                   "host_models": {"openai": {"strong": "inherit", "standard": "o4", "light": "o4-mini", "alternates": []}},
                   "role_models": {"ratchet-fixer": "light"}, "ui_reviewers": ["lane-only"], "approver": "lane-approver",
                   "human_review": "none", "max_adversarial_rounds": 99, "max_adversarial_rounds_run": 98,
                   "unavailable_ttl_hours": 97, "probe_providers": False, "probe_ttl_minutes": 96,
                   "probe_timeout_sec": 95, "quota_retest_minutes": 94, "provider_share": {"codex": 1}}
    assert sorted(lane_values) == sorted(LANE_LOCAL)
    w = build(lane=lambda d: set_cfg(d, **lane_values))
    old = g(w.main, "rev-parse", "HEAD")
    before = cfg_at(w.main)
    for k in LANE_LOCAL:
        assert before.get(k, "<absent>") != lane_values[k], k
    r = w.integrate()
    merged_ok(w, r, cid, "every lane-local key changed on the lane only", old)
    c = cfg_at(w.main)
    for k in LANE_LOCAL:
        check(f"[{cid}] '{k}' keeps main's value", c.get(k, "<absent>") == before.get(k, "<absent>"),
              (c.get(k, "<absent>"), before.get(k, "<absent>")))
        listed(w, r, cid, k)
    check(f"[{cid}] the lane's src/feature.txt change is on main", (w.main / "src/feature.txt").read_text() == FEATURE_LANE)


def t03_no_differing_settings_reports_nothing_left_out():
    """[CP3-T03] When the lane's config has no differing settings, nothing is reported as left out."""
    cid = "CP3-T03"

    def reformat(d):
        p = d / ".ratchet/config.json"
        c = json.loads(p.read_text())
        p.write_text(json.dumps(dict(sorted(c.items(), reverse=True)), indent=4, sort_keys=False) + "\n")

    w = build(lane=reformat)
    old = g(w.main, "rev-parse", "HEAD")
    before = cfg_at(w.main)
    r = w.integrate()
    merged_ok(w, r, cid, "whitespace and key order only", old)
    check(f"[{cid}] (A) main's config.json settings are unchanged", cfg_at(w.main) == before, diff_keys(cfg_at(w.main), before))
    line = w.last()
    check(f"[{cid}] (A) the record's list of settings not taken is empty", line.get("result") == "merged" and not_taken(line) == [],
          line)
    check(f"[{cid}] (A) the output claims no setting was dropped",
          not re.search(r"not taken|left out|dropped", raw(r), re.I), said(r)[:500])

    w2 = build(main=lambda d: set_cfg(d, test_timeout_sec=600), lane=lambda d: set_cfg(d, test_timeout_sec=600))
    old2 = g(w2.main, "rev-parse", "HEAD")
    before2 = cfg_at(w2.main)
    r2 = w2.integrate()
    merged_ok(w2, r2, cid, "identical change on both sides", old2)
    check(f"[{cid}] (B) main's config.json settings are unchanged (test_timeout_sec 600)",
          cfg_at(w2.main) == before2 and before2["test_timeout_sec"] == 600, diff_keys(cfg_at(w2.main), before2))
    line2 = w2.last()
    check(f"[{cid}] (B) the record's list of settings not taken is empty",
          line2.get("result") == "merged" and not_taken(line2) == [], line2)
    check(f"[{cid}] (B) the output does not say test_timeout_sec was left out", "test_timeout_sec" not in raw(r2),
          said(r2)[:500])


def t04_learnings_from_both_sides_in_order_without_duplicates():
    """[CP3-T04] Learnings from main and from the lane are both kept, in order, without duplicates."""
    cid = "CP3-T04"
    w = build(main=lambda d: (append_line(d, ".ratchet/learnings.md", "- (2026-10-02) main lesson 1"),
                              append_line(d, ".ratchet/learnings.md", "- (2026-10-02) shared lesson")),
              lane=lambda d: (append_line(d, ".ratchet/learnings.md", "- (2026-10-02) lane lesson 1"),
                              append_line(d, ".ratchet/learnings.md", "- (2026-10-02) shared lesson")))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "learnings appended on both sides", old)
    text = (w.main / ".ratchet/learnings.md").read_text()
    got = [ln for ln in lines_of(text) if ln.startswith("- ")]
    want = LEARN_LESSONS + ["- (2026-10-02) main lesson 1", "- (2026-10-02) shared lesson", "- (2026-10-02) lane lesson 1"]
    check(f"[{cid}] the original three lessons once each, then main lesson 1, shared lesson once, lane lesson 1",
          got == want, got)
    check(f"[{cid}] the header line is still there once", text.count("# Ratchet learnings") == 1)
    check(f"[{cid}] no conflict markers", conflict_markers(w.main) == [], conflict_markers(w.main))
    check(f"[{cid}] the committed file on main's tip is the same", show(w.main, "HEAD", ".ratchet/learnings.md") == text)
    check(f"[{cid}] git status is clean afterwards", porcelain_lines(w.main) == [], porcelain_lines(w.main))


def t05_gitignore_keeps_every_line_from_both_sides():
    """[CP3-T05] The Ratchet .gitignore keeps every line from both sides, including when both created it."""
    cid = "CP3-T05"
    w = build(main=lambda d: append_line(d, ".ratchet/.gitignore", "*.log"),
              lane=lambda d: append_line(d, ".ratchet/.gitignore", ".ratchet/viewer-data/"))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "(A) .gitignore appended on both sides", old)
    got = lines_of((w.main / ".ratchet/.gitignore").read_text())
    want = lines_of(GI_INIT) + ["*.log", ".ratchet/viewer-data/"]
    check(f"[{cid}] (A) every line from both versions exactly once, nothing lost", sorted(got) == sorted(want)
          and len(got) == len(set(got)), got)
    check(f"[{cid}] (A) main's lines come first", got[:len(want) - 1] == want[:-1], got)
    check(f"[{cid}] (A) no conflict markers", conflict_markers(w.main) == [], conflict_markers(w.main))

    w2 = build(no_gitignore=True, main=lambda d: (d / ".ratchet/.gitignore").write_text("*.tmp\n"))
    need(f"[{cid}] (B setup) the lane created the init .gitignore in its own commit",
         show(w2.lanes["lane-a"], "HEAD", ".ratchet/.gitignore") == GI_INIT, show(w2.lanes["lane-a"], "HEAD", ".ratchet/.gitignore"))
    need(f"[{cid}] (B setup) main created a different one", show(w2.main, "HEAD", ".ratchet/.gitignore") == "*.tmp\n")
    old2 = g(w2.main, "rev-parse", "HEAD")
    r2 = w2.integrate()
    merged_ok(w2, r2, cid, "(B) .gitignore created on both sides", old2)
    got2 = lines_of((w2.main / ".ratchet/.gitignore").read_text())
    want2 = ["*.tmp"] + lines_of(GI_INIT)
    check(f"[{cid}] (B) every line from both versions exactly once, nothing lost", sorted(got2) == sorted(want2)
          and len(got2) == len(set(got2)), got2)
    check(f"[{cid}] (B) no conflict markers", conflict_markers(w2.main) == [], conflict_markers(w2.main))
    check(f"[{cid}] (B) git status is clean afterwards", porcelain_lines(w2.main) == [], porcelain_lines(w2.main))


def t06_standards_change_only_in_the_lane_is_taken():
    """[CP3-T06] A standards change made only in the lane is taken."""
    cid = "CP3-T06"
    w = build(lane=lambda d: append_line(d, ".ratchet/standards.md", "6. One assertion per rule"))
    old = g(w.main, "rev-parse", "HEAD")
    lane_std = show(w.lanes["lane-a"], "HEAD", ".ratchet/standards.md")
    r = w.integrate()
    merged_ok(w, r, cid, "standards changed on the lane only", old)
    check(f"[{cid}] main's standards.md equals the lane's version (with rule 6)",
          (w.main / ".ratchet/standards.md").read_text() == lane_std and "6. One assertion per rule" in lane_std)
    check(f"[{cid}] the committed file on main's tip is the lane's version", show(w.main, "HEAD", ".ratchet/standards.md") == lane_std)


def t07_standards_change_only_on_main_is_kept():
    """[CP3-T07] A standards change made only on main is kept while the lane's other work still lands."""
    cid = "CP3-T07"
    w = build(main=lambda d: reword(d, ".ratchet/standards.md", "2. Name things plainly", "2. Name things plainly and fully"),
              lane=lambda d: append_line(d, ".ratchet/learnings.md", "- (2026-10-02) lane lesson 1"))
    old = g(w.main, "rev-parse", "HEAD")
    main_std = show(w.main, "HEAD", ".ratchet/standards.md")
    r = w.integrate()
    merged_ok(w, r, cid, "standards changed on main only", old)
    check(f"[{cid}] main's standards.md is still main's reworded version",
          (w.main / ".ratchet/standards.md").read_text() == main_std and "plainly and fully" in main_std)
    check(f"[{cid}] the lane's src/feature.txt is on main", (w.main / "src/feature.txt").read_text() == FEATURE_LANE)
    check(f"[{cid}] the lane's learnings line is on main",
          "- (2026-10-02) lane lesson 1" in (w.main / ".ratchet/learnings.md").read_text())


def t08_standards_changed_on_both_sides_stops():
    """[CP3-T08] A standards change on both sides stops integrate with main untouched and a plain next step."""
    cid = "CP3-T08"
    w = build(main=lambda d: (reword(d, ".ratchet/standards.md", "3. Test every rule", "3. Test every rule on main"),
                              append_line(d, ".ratchet/learnings.md", "- (2026-10-02) main lesson 1")),
              lane=lambda d: (reword(d, ".ratchet/standards.md", "3. Test every rule", "3. Test every rule in the lane"),
                              append_line(d, ".ratchet/learnings.md", "- (2026-10-02) lane lesson 1")))
    before = snap(w)
    r = w.integrate()
    t = failed_run(r, cid, "standards changed both ways", ".ratchet/standards.md", ("both sides", "both changed", "on both"))
    check(f"[{cid}] it says what to do next: decide which wording to keep, change one side, then integrate again",
          ("decide" in t or "keep" in t or "choose" in t) and "integrate" in t and "again" in t, said(r)[:500])
    check(f"[{cid}] one plain message: no git conflict output and no traceback", "<<<<<<<" not in t and "traceback" not in t,
          said(r)[:500])
    unchanged(w, before, cid)
    check(f"[{cid}] main's learnings.md still has main's own line",
          "- (2026-10-02) main lesson 1" in (w.main / ".ratchet/learnings.md").read_text())
    check(f"[{cid}] no merge commit: main's tip is the old tip", g(w.main, "rev-parse", "HEAD") == before["head"])
    no_scratch(w, cid)
    line = w.last()
    check(f"[{cid}] the newest record says the attempt was stopped, names the lane and standards.md",
          line.get("result") in ("rolled-back", "refused") and line.get("lane") == "lane-a"
          and "standards.md" in str(line.get("detail", "")), line)


def t09_uncommitted_ratchet_files_carried_and_still_uncommitted():
    """[CP3-T09] Main's uncommitted Ratchet files are carried across the merge and stay uncommitted."""
    cid = "CP3-T09"
    w = build(lane=lambda d: append_line(d, ".ratchet/learnings.md", "- (2026-10-02) lane lesson 1"),
              dirty=lambda d: (append_line(d, ".ratchet/learnings.md", "- (2026-10-02) an uncommitted note on main"),
                               set_cfg(d, test_timeout_sec=600)))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "uncommitted learnings line and config change on main", old)
    only_the_merge(w, cid, old)
    c = cfg_at(w.main)
    check(f"[{cid}] config.json still has test_timeout_sec 600", c.get("test_timeout_sec") == 600, c.get("test_timeout_sec"))
    check(f"[{cid}] the committed config.json has 60: the uncommitted change was never committed",
          cfg_at(w.main, "HEAD").get("test_timeout_sec") == 60)
    text = (w.main / ".ratchet/learnings.md").read_text()
    check(f"[{cid}] learnings.md holds main's uncommitted line plus the lane's line",
          "- (2026-10-02) an uncommitted note on main" in text and "- (2026-10-02) lane lesson 1" in text
          and all(x in text for x in LEARN_LESSONS), text)
    check(f"[{cid}] the uncommitted line is in no commit", "an uncommitted note on main" not in show(w.main, "HEAD", ".ratchet/learnings.md"))
    check(f"[{cid}] git status shows exactly config.json and learnings.md modified and unstaged, nothing staged",
          sorted(porcelain_lines(w.main)) == [" M .ratchet/config.json", " M .ratchet/learnings.md"], porcelain_lines(w.main))
    check(f"[{cid}] the lane's src/feature.txt is on main", (w.main / "src/feature.txt").read_text() == FEATURE_LANE)


def t10_uncommitted_project_files_still_refused():
    """[CP3-T10] Uncommitted project files on main are still refused, and the uncommitted Ratchet files are left alone."""
    cid = "CP3-T10"
    marker = "an uncommitted note on main T10"
    w = build(dirty=lambda d: (append_line(d, ".ratchet/learnings.md", f"- (2026-10-02) {marker}"),
                               append_line(d, "src/notes.txt", "notes: uncommitted edit T10")))
    before = snap(w)
    learn, notes = (w.main / ".ratchet/learnings.md").read_bytes(), (w.main / "src/notes.txt").read_bytes()
    r = w.integrate()
    failed_run(r, cid, "uncommitted project file", "src/notes.txt", ("commit or stash", "stash"))
    check(f"[{cid}] no new commit exists on main", g(w.main, "rev-parse", "HEAD") == before["head"])
    check(f"[{cid}] learnings.md and src/notes.txt keep their uncommitted bytes",
          (w.main / ".ratchet/learnings.md").read_bytes() == learn and (w.main / "src/notes.txt").read_bytes() == notes)
    unchanged(w, before, cid)
    check(f"[{cid}] no saved copy of the uncommitted Ratchet file is left behind", saved_copies(w, marker) == [],
          saved_copies(w, marker))
    no_scratch(w, cid)


def t11_failing_suite_leaves_uncommitted_ratchet_files_exactly():
    """[CP3-T11] If the suite fails, main's uncommitted Ratchet files are exactly as they were."""
    cid = "CP3-T11"
    w = build(lane=lambda d: (d / "src/feature.txt").write_text(FEATURE_BREAK),
              dirty=lambda d: (append_line(d, ".ratchet/learnings.md", "- (2026-10-02) an uncommitted note on main"),
                               set_cfg(d, test_timeout_sec=600)))
    before = snap(w)
    learn, conf = (w.main / ".ratchet/learnings.md").read_bytes(), (w.main / ".ratchet/config.json").read_bytes()
    r = w.integrate()
    failed_run(r, cid, "suite fails on the merged code", "suite-boom-line")
    check(f"[{cid}] the suite did run once", len(w.runs()) == 1, len(w.runs()))
    check(f"[{cid}] the merge did not land: no merge commit, HEAD unchanged, the lane's feature.txt not on main",
          g(w.main, "rev-parse", "HEAD") == before["head"] and (w.main / "src/feature.txt").read_text() == FEATURE_BASE)
    check(f"[{cid}] learnings.md and config.json have exactly the bytes they had",
          (w.main / ".ratchet/learnings.md").read_bytes() == learn and (w.main / ".ratchet/config.json").read_bytes() == conf)
    check(f"[{cid}] git status still lists them as modified and unstaged",
          sorted(porcelain_lines(w.main)) == [" M .ratchet/config.json", " M .ratchet/learnings.md"], porcelain_lines(w.main))
    unchanged(w, before, cid)
    line = w.last()
    check(f"[{cid}] the record shows a rolled-back attempt", line.get("result") == "rolled-back" and line.get("suite") == "failed",
          line)
    no_scratch(w, cid)


def t22_uncommitted_review_depth_intact_lane_providers_kept_mains():
    """[CP3-T22] Main's uncommitted reviewer split stays intact while the lane's provider change is handled by the lane-local rule."""
    cid = "CP3-T22"
    w = build(lane=lambda d: set_cfg(d, providers=PROVIDERS_LANE),
              dirty=lambda d: set_cfg(d, review_depth=DEPTH_B))
    old = g(w.main, "rev-parse", "HEAD")
    main_providers = cfg_at(w.main, "HEAD").get("providers", "<absent>")
    r = w.integrate()
    merged_ok(w, r, cid, "uncommitted review_depth on main, providers changed in the lane", old)
    c = cfg_at(w.main)
    check(f"[{cid}] main's uncommitted review_depth is intact (DEPTH_B)", c.get("review_depth") == DEPTH_B, c.get("review_depth"))
    check(f"[{cid}] and still uncommitted: config.json modified and unstaged, the committed one has DEPTH_A",
          " M .ratchet/config.json" in porcelain_lines(w.main) and cfg_at(w.main, "HEAD").get("review_depth") == DEPTH_A,
          porcelain_lines(w.main))
    check(f"[{cid}] providers keep main's value, no paid_ok_at added",
          c.get("providers", "<absent>") == main_providers and "paid_ok_at" not in (w.main / ".ratchet/config.json").read_text()
          and "paid_ok_at" not in show(w.main, "HEAD", ".ratchet/config.json"), c.get("providers"))
    listed(w, r, cid, "providers")
    only_the_merge(w, cid, old)
    check(f"[{cid}] the lane's src/feature.txt is on main", (w.main / "src/feature.txt").read_text() == FEATURE_LANE)


def t23_uncommitted_value_wins_a_clash_and_is_listed():
    """[CP3-T23] When the uncommitted value and the lane both changed the same setting, the uncommitted value wins and is listed."""
    cid = "CP3-T23"
    w = build(lane=lambda d: set_cfg(d, test_timeout_sec=900, commit_per_checkpoint=False),
              dirty=lambda d: set_cfg(d, test_timeout_sec=600, max_ui_rounds=5))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "uncommitted test_timeout_sec and max_ui_rounds on main", old)
    c = cfg_at(w.main)
    check(f"[{cid}] config.json has test_timeout_sec 600 (the uncommitted value wins)", c.get("test_timeout_sec") == 600,
          c.get("test_timeout_sec"))
    check(f"[{cid}] max_ui_rounds 5 (uncommitted, untouched by the lane)", c.get("max_ui_rounds") == 5, c.get("max_ui_rounds"))
    check(f"[{cid}] commit_per_checkpoint false (taken from the lane)", c.get("commit_per_checkpoint") is False,
          c.get("commit_per_checkpoint"))
    check(f"[{cid}] those values are still uncommitted: config.json modified and unstaged, the committed file has the "
          f"lane's 900 and max_ui_rounds 3",
          " M .ratchet/config.json" in porcelain_lines(w.main) and cfg_at(w.main, "HEAD").get("test_timeout_sec") == 900
          and cfg_at(w.main, "HEAD").get("max_ui_rounds") == 3, (porcelain_lines(w.main), cfg_at(w.main, "HEAD")))
    listed(w, r, cid, "test_timeout_sec")
    only_the_merge(w, cid, old)


def t24_reapply_that_cannot_be_done_restores_and_rolls_back():
    """[CP3-T24] A re-apply that cannot be done restores main's saved bytes and rolls back."""
    cid = "CP3-T24"
    marker = "3. Test every rule, reworded on main but not committed"
    w = build(lane=lambda d: reword(d, ".ratchet/standards.md", "3. Test every rule", "3. Test every rule, the lane's wording"),
              dirty=lambda d: reword(d, ".ratchet/standards.md", "3. Test every rule", marker))
    before = snap(w)
    std = (w.main / ".ratchet/standards.md").read_bytes()
    r = w.integrate()
    failed_run(r, cid, "the uncommitted standards.md and the merged one clash", "standards.md")
    check(f"[{cid}] main's HEAD is back at (or never left) its old commit", g(w.main, "rev-parse", "HEAD") == before["head"],
          (g(w.main, "rev-parse", "HEAD"), before["head"]))
    check(f"[{cid}] the lane's changes are not on main",
          (w.main / "src/feature.txt").read_text() == FEATURE_BASE and not gok(w.main, "merge-base", "--is-ancestor", "lane-a", "HEAD"))
    check(f"[{cid}] the bytes of main's uncommitted standards.md are exactly what they were",
          (w.main / ".ratchet/standards.md").read_bytes() == std)
    check(f"[{cid}] git status is as before", g(w.main, "status", "--porcelain", "-uall") == before["status"],
          g(w.main, "status", "--porcelain", "-uall"))
    unchanged(w, before, cid)
    check(f"[{cid}] no saved copy needing restore is left", saved_copies(w, marker) == [], saved_copies(w, marker))
    line = w.last()
    check(f"[{cid}] the newest record says rolled-back", line.get("result") == "rolled-back", line)
    no_scratch(w, cid)


def t12_project_conflict_lists_only_the_project_file():
    """[CP3-T12] A project-file conflict still stops integrate and lists only the project file, even when Ratchet files also differ."""
    cid = "CP3-T12"

    def main_edits(d):
        reword(d, "src/shared.txt", "shared line 1", "shared line 1 main")
        append_line(d, ".ratchet/learnings.md", "- (2026-10-02) main lesson 1")
        append_line(d, ".ratchet/.gitignore", "*.log")
        set_cfg(d, test_timeout_sec=600)

    def lane_edits(d):
        reword(d, "src/shared.txt", "shared line 1", "shared line 1 lane")
        append_line(d, ".ratchet/learnings.md", "- (2026-10-02) lane lesson 1")
        append_line(d, ".ratchet/.gitignore", ".ratchet/viewer-data/")
        set_cfg(d, test_timeout_sec=900)

    w = build(main=main_edits, lane=lane_edits)
    before = snap(w)
    r = w.integrate()
    t = failed_run(r, cid, "conflict in src/shared.txt", "src/shared.txt")
    check(f"[{cid}] the message does not list the Ratchet files as conflicts",
          not any(f in t for f in SHARED), said(r)[:500])
    check(f"[{cid}] it points at merging main into the lane (merged) and integrating again",
          "merged" in t and "integrate" in t and "again" in t, said(r)[:500])
    unchanged(w, before, cid)
    check(f"[{cid}] the suite never ran", w.runs() == [], w.runs())
    no_scratch(w, cid)


def t13_mains_own_run_asks_no_approve_changes():
    """[CP3-T13] Main's own run does not ask for approve-changes because integrate merged the Ratchet files by rule."""
    cid = "CP3-T13"
    w = build(lane=lambda d: (append_line(d, ".ratchet/standards.md", "6. One assertion per rule"),
                              append_line(d, ".ratchet/learnings.md", "- (2026-10-02) lane lesson 1")))
    run = approved_run(w.main, w.env, "main-run")
    gates_before = gates_of(run)
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "main has its own run at a step boundary", old)
    check(f"[{cid}] main's standards.md now has the lane's rule 6", "6. One assertion per rule" in (w.main / ".ratchet/standards.md").read_text())
    st = th.rx(w.main, w.env, "status")
    nx = th.rx(w.main, w.env, "next")
    for name, rr in (("status", st), ("next", nx)):
        check(f"[{cid}] main's {name} runs and mentions no approve-changes and no unapproved change to rules",
              known(rr) and "approve-changes" not in raw(rr) and "changed after the user approved" not in raw(rr),
              (rr.returncode, said(rr)[-500:]))
    ask = th.rx(w.main, w.env, "ask", "approve-changes")
    check(f"[{cid}] the integrity check reports no problem: ask approve-changes says nothing has changed since approval",
          ask.returncode == 0 and "Nothing has changed since approval" in raw(ask), (ask.returncode, said(ask)[-500:]))
    check(f"[{cid}] main's own gates are unchanged", gates_of(run) == gates_before, (gates_of(run), gates_before))
    check(f"[{cid}] main's run is still building CP1 at its step boundary",
          jread(run / "state.json").get("phase") == "building" and jread(run / "state.json").get("current") == "CP1")


def t14_integrate_treats_exactly_fs_shared_files():
    """[CP3-T14] Integrate treats exactly the files lane F's merge guard treats as Ratchet's shared files."""
    cid = "CP3-T14"
    merge_shared = None
    try:
        spec = importlib.util.spec_from_file_location("ratchet_engine_for_cp3", str(ENGINE))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        merge_shared = set(getattr(mod, "MERGE_SHARED"))
    except Exception as e:  # noqa: BLE001
        merge_shared = {"<could not load the engine: %s>" % e}
    check(f"[{cid}] F's MERGE_SHARED holds exactly the four shared paths", merge_shared == set(SHARED), merge_shared)

    def both(tag):
        def fn(d):
            set_cfg(d, test_timeout_sec={"main": 600, "lane": 900}[tag])
            append_line(d, ".ratchet/learnings.md", f"- (2026-10-02) {tag} lesson")
            append_line(d, ".ratchet/.gitignore", f"*.{tag}")
            append_line(d, ".ratchet/standards.md", "6. One assertion per rule")   # the same change on both sides
        return fn

    w = build(main=both("main"), lane=both("lane"))
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "all four shared files changed on both sides", old)
    c = cfg_at(w.main)
    learn = (w.main / ".ratchet/learnings.md").read_text()
    gi = lines_of((w.main / ".ratchet/.gitignore").read_text())
    std = (w.main / ".ratchet/standards.md").read_text()
    check(f"[{cid}] config.json merged by rule: both changed test_timeout_sec, main's 600 kept", c.get("test_timeout_sec") == 600,
          c.get("test_timeout_sec"))
    check(f"[{cid}] learnings.md holds both sides' lessons", "main lesson" in learn and "lane lesson" in learn, learn)
    check(f"[{cid}] .gitignore holds both sides' lines once", gi.count("*.main") == 1 and gi.count("*.lane") == 1, gi)
    check(f"[{cid}] standards.md holds rule 6 once", std.count("6. One assertion per rule") == 1, std)
    check(f"[{cid}] no conflict markers in main", conflict_markers(w.main) == [], conflict_markers(w.main))
    listed(w, r, cid, "test_timeout_sec")

    w2 = build(main=lambda d: (d / ".ratchet/notes.txt").write_text("notes: main\n"),
               lane=lambda d: (d / ".ratchet/notes.txt").write_text("notes: lane\n"))
    before = snap(w2)
    r2 = w2.integrate()
    t2 = failed_run(r2, cid, "a fifth file under .ratchet/ changed on both sides is a project file", ".ratchet/notes.txt")
    check(f"[{cid}] the stop names it as a conflict", "conflict" in t2, said(r2)[:400])
    unchanged(w2, before, cid)
    check(f"[{cid}] the suite never ran for the fifth-file case", w2.runs() == [], w2.runs())


def t15_lane_taking_integrated_main_gets_no_approval_question():
    """[CP3-T15] A lane that takes an integrated main between steps gets no approval question for Ratchet's shared files."""
    cid = "CP3-T15"

    def both(tag):
        def fn(d):
            set_cfg(d, test_timeout_sec={"main": 600, "lane": 900}[tag])
            append_line(d, ".ratchet/learnings.md", f"- (2026-10-02) {tag} lesson")
            append_line(d, ".ratchet/.gitignore", f"*.{tag}")
            append_line(d, ".ratchet/standards.md", "6. One assertion per rule")
        return fn

    w = build(main=both("main"), lane=both("lane"))
    base = g(w.main, "merge-base", "HEAD", "lane-a")
    lane_y = w.base / "lane-y"
    th.git(w.main, "worktree", "add", "-q", "-b", "lane-y", str(lane_y), base)
    run_y = approved_run(lane_y, w.env, "lane-y-run")
    gates_before = gates_of(run_y)
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "(setup) lane-a integrated with all four shared files changed on both sides", old)
    main_std = show(w.main, "HEAD", ".ratchet/standards.md")
    main_cfg = show(w.main, "HEAD", ".ratchet/config.json")
    rc, out = guard(lane_y, w.env, "git merge --no-ff -m 'Merge main into lane-y' main")
    check(f"[{cid}] the merge guard lets the second lane merge main between steps", rc == 0 and "RATCHET GUARD" not in out,
          (rc, out[-300:]))
    m = subprocess.run([REAL_GIT, "merge", "--no-ff", "-q", "-m", "Merge main into lane-y", "main"], cwd=lane_y,
                       capture_output=True, text=True)
    need(f"[{cid}] (setup) plain git merges main into the lane cleanly", m.returncode == 0 and len(parents(lane_y)) == 2,
         m.stdout + m.stderr)
    mr = th.rx(lane_y, w.env, "merged")
    check(f"[{cid}] `merged` succeeds", known(mr) and mr.returncode == 0, (mr.returncode, said(mr)[-500:]))
    check(f"[{cid}] the lane holds main's committed standards.md and config.json exactly",
          (lane_y / ".ratchet/standards.md").read_text() == main_std and (lane_y / ".ratchet/config.json").read_text() == main_cfg)
    st_y = jread(run_y / "state.json")
    hist = [e for e in st_y.get("history") or [] if e.get("event") == "merged_main"]
    check(f"[{cid}] merged recorded the merge as accepted (merged_main, not by hand)",
          bool(hist) and not hist[-1].get("by_hand"), hist[-1:] )
    for name in ("next", "status"):
        rr = th.rx(lane_y, w.env, name)
        check(f"[{cid}] the lane's {name} mentions no approve-changes and no approval question for the shared files",
              known(rr) and "approve-changes" not in raw(rr) and "changed after the user approved" not in raw(rr),
              (rr.returncode, said(rr)[-500:]))
    ask = th.rx(lane_y, w.env, "ask", "approve-changes")
    check(f"[{cid}] the lane's integrity check reports no problem", ask.returncode == 0 and "Nothing has changed since approval" in raw(ask),
          (ask.returncode, said(ask)[-500:]))
    check(f"[{cid}] the lane's own gates are unchanged", gates_of(run_y) == gates_before, (gates_of(run_y), gates_before))


def t16_nothing_is_pushed_while_ratchet_files_are_merged():
    """[CP3-T16] Nothing is pushed while Ratchet's files are merged."""
    cid = "CP3-T16"
    w = build(remote=True, main=lambda d: (set_cfg(d, test_timeout_sec=600),
                                           append_line(d, ".ratchet/learnings.md", "- (2026-10-02) main lesson 1")),
              lane=lambda d: (set_cfg(d, test_timeout_sec=900),
                              append_line(d, ".ratchet/learnings.md", "- (2026-10-02) lane lesson 1")))
    rem_before = remote_state(w.remote)
    old = g(w.main, "rev-parse", "HEAD")
    r = w.integrate()
    merged_ok(w, r, cid, "merged outcome with config and learnings conflicts", old)
    check(f"[{cid}] (merged) the remote's refs and objects are identical to before, no push hook ran",
          remote_state(w.remote) == rem_before and not (w.base / "pushed-marker").exists(), (remote_state(w.remote), rem_before))

    w2 = build(remote=True, main=lambda d: reword(d, ".ratchet/standards.md", "3. Test every rule", "3. Test every rule on main"),
               lane=lambda d: reword(d, ".ratchet/standards.md", "3. Test every rule", "3. Test every rule in the lane"))
    rem_before2 = remote_state(w2.remote)
    r2 = w2.integrate()
    failed_run(r2, cid, "stopped on standards", "standards.md")
    check(f"[{cid}] (stopped) the remote's refs and objects are identical to before, no push hook ran",
          remote_state(w2.remote) == rem_before2 and not (w2.base / "pushed-marker").exists(),
          (remote_state(w2.remote), rem_before2))


def t17_docs_state_the_rules():
    """[CP3-T17] The docs state the rules for each Ratchet shared file."""
    cid = "CP3-T17"
    docs = {"README.md": REPO / "README.md", "SKILL.md": REPO / "skills/ratchet-orchestrator/SKILL.md",
            "schemas.md": REPO / "skills/ratchet-orchestrator/references/schemas.md"}
    groups = {
        "config.json merged key by key": (r"key by key|key-by-key|per key|each key|top-level key",),
        "a both-sides change keeps main's": (r"keeps? main'?s|main'?s (value |version )?(is )?(kept|wins)|main'?s (value|version) stays",),
        "lane-local reviewer and provider keys keep main's": (r"lane-local|lane local",),
        "guardrail checks from both sides": (r"checks?.{0,80}(both sides|from both|main lacks|the lane added)|guardrail.{0,80}(both|kept|lost)",),
        "lane settings not taken are listed": (r"not taken|left out|kept main'?s",),
        "learnings.md lines from both sides": (r"learnings\.md.{0,200}(both sides|from both|union)|(both sides|from both|union).{0,200}learnings\.md",),
        ".gitignore lines from both sides": (r"\.gitignore.{0,200}(both sides|from both|union)|(both sides|from both|union).{0,200}\.gitignore",),
        "standards.md: the changed side, stop if both": (r"standards\.md.{0,300}(stops?|refus|both sides|on both)|(stops?|refus).{0,300}standards\.md",),
        "uncommitted Ratchet files carried across and still uncommitted": (r"uncommitted.{0,300}(carried|re-?appl|still uncommitted|stay uncommitted|never committed)",),
        "the uncommitted value wins a clash and is listed": (r"uncommitted (value|copy|change).{0,120}(wins|kept|listed)",),
    }
    for name, path in docs.items():
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        text = " ".join(text.split())
        check(f"[{cid}] {name} describes integrate", "integrate" in text, name)
        for what, pats in groups.items():
            check(f"[{cid}] {name} says: {what}", any(re.search(p, text, re.I | re.S) for p in pats), name)


TESTS = [
    ("CP3-T01", t01_config_conflict_merged_key_by_key),
    ("CP3-T02", t02_lane_local_keys_changed_only_on_the_lane_keep_mains),
    ("CP3-T18", t18_project_wide_setting_changed_only_in_the_lane_is_taken),
    ("CP3-T19", t19_guardrail_check_from_the_lane_survives_mains_additions),
    ("CP3-T20", t20_guardrail_checks_matched_by_command),
    ("CP3-T21", t21_every_lane_local_key_keeps_mains_value),
    ("CP3-T03", t03_no_differing_settings_reports_nothing_left_out),
    ("CP3-T04", t04_learnings_from_both_sides_in_order_without_duplicates),
    ("CP3-T05", t05_gitignore_keeps_every_line_from_both_sides),
    ("CP3-T06", t06_standards_change_only_in_the_lane_is_taken),
    ("CP3-T07", t07_standards_change_only_on_main_is_kept),
    ("CP3-T08", t08_standards_changed_on_both_sides_stops),
    ("CP3-T09", t09_uncommitted_ratchet_files_carried_and_still_uncommitted),
    ("CP3-T10", t10_uncommitted_project_files_still_refused),
    ("CP3-T11", t11_failing_suite_leaves_uncommitted_ratchet_files_exactly),
    ("CP3-T22", t22_uncommitted_review_depth_intact_lane_providers_kept_mains),
    ("CP3-T23", t23_uncommitted_value_wins_a_clash_and_is_listed),
    ("CP3-T24", t24_reapply_that_cannot_be_done_restores_and_rolls_back),
    ("CP3-T12", t12_project_conflict_lists_only_the_project_file),
    ("CP3-T13", t13_mains_own_run_asks_no_approve_changes),
    ("CP3-T14", t14_integrate_treats_exactly_fs_shared_files),
    ("CP3-T15", t15_lane_taking_integrated_main_gets_no_approval_question),
    ("CP3-T16", t16_nothing_is_pushed_while_ratchet_files_are_merged),
    ("CP3-T17", t17_docs_state_the_rules),
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
