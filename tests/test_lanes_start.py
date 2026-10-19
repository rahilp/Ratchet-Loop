#!/usr/bin/env python3
"""
CP7 tests: `lanes-start` turns one request into several ready lanes.   python3 tests/test_lanes_start.py

The director (RATCHET_ROLE=director, after its guard hook fired) runs
  lanes-start --request "<text>" --lane NAME=FEATURE [--lane NAME=FEATURE ...] [--name N] [--json]
with --root pointing at the main checkout. These tests drive the repo's own ratchet.py as a subprocess against throwaway
git projects: a main checkout (branch main, Ratchet's four shared files committed, a bare 'origin' remote) in a parent
temp folder so the lanes' sibling worktrees land there, a private RATCHET_HOME with a director key, and a PATH that has
no agent CLI and no paseo binary. Assertions read git itself (branches, worktree list, status, HEAD), the files Ratchet
writes and what the command printed.

Contract the tests pin (the build must match):
  - the branch of a lane is the lane name; its worktree is a sibling folder of the main checkout whose name contains
    the lane name; main's HEAD branch is what the lane branches start from
  - --json prints {"request": name, "lanes": [{"name", "branch", "worktree", "brief", "start"}]}; "start" is the
    shell-quoted (shlex.join) command from lane_start_command, or plain words when none is set
  - the request name is a slug (lowercase letters, digits, hyphens) of the request text unless --name is given; the
    record is .ratchet/requests/<name>.json in the main checkout with "request" (the text), a time key
    (time | created_at | started_at | at), and "lanes": [{name, feature, branch, worktree, brief}] in the order given

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP7-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shlex
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
ID_RE = re.compile(r"\[(CP7-[TX]\d+[a-z]?)\]")

REQUEST = "Add search and export"
SEARCH = "Search box on the list page"
EXPORT = "CSV export button"
TWO = [f"search={SEARCH}", f"export={EXPORT}"]


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
    """Everything a command printed, with absolute paths cut out (temp folders may carry any word)."""
    return PATH_RE.sub("<path>", (r.stdout or "") + "\n" + (r.stderr or ""))


def raw_said(r) -> str:
    return (r.stdout or "") + "\n" + (r.stderr or "")


def known(r) -> bool:
    t = said(r)
    return "invalid choice" not in t and "unrecognized arguments" not in t and "Traceback" not in t


def low(r) -> str:
    return said(r).lower() if known(r) else ""


def jread(p):
    return json.loads(Path(p).read_text())


def jwrite(p, d):
    Path(p).write_text(json.dumps(d, indent=2) + "\n")


def gitx(d, *a):
    """git without the assert of th.git (a failing command is data here)."""
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True, env=dict(os.environ, PATH=th.SAFE_PATH))


def gout(d, *a) -> str:
    return gitx(d, *a).stdout


# ------------------------------------------------------------------------------------------------ fixtures

class World:
    """A throwaway main checkout + bare remote + director folder, with a private HOME / RATCHET_HOME."""

    def __init__(self, base, main, remote, env, dfolder):
        self.base, self.main, self.remote, self.env, self.dfolder = base, main, remote, env, dfolder
        self.denv = dict(env, RATCHET_ROLE="director")

    def rh(self) -> Path:
        return Path(self.env["RATCHET_HOME"])

    def enrolled_bytes(self):
        p = self.rh() / "enrolled.json"
        return p.read_bytes() if p.exists() else None

    def start(self, request=REQUEST, lanes=TWO, name=None, js=False, env=None, hook=True, extra=()):
        args = ["lanes-start", "--request", request]
        for ln in lanes:
            args += ["--lane", ln]
        if name:
            args += ["--name", name]
        if js:
            args.append("--json")
        args += list(extra)
        e = env or self.denv
        if hook:   # the director's own guard hook sees the command first
            cmd = f"python3 {ENGINE} --root {shlex.quote(str(self.main))} {shlex.join(args)}"
            th.cp4x_guard_tool(self.dfolder, e, "Bash", {"command": cmd}, session="s-director")
        return th.rx(self.dfolder, e, "--root", str(self.main), *args)


def _approved_run(main, env):
    """Main's own run, taken to phase building with real Ratchet commands (plan submitted, then approved by a
    chat reply in the owner session), never by editing state.json."""
    tp = main.parent / "main-transcript.jsonl"

    def say(text):
        with tp.open("a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                "timestamp": th.now_dt().isoformat()}) + "\n")
        th.rx(main, env, "hook", "prompt", "--host", "claude",
              stdin=json.dumps({"cwd": str(main), "prompt": text, "session_id": "s-owner", "transcript_path": str(tp)}))
    run = main / ".ratchet/runs/main-run"
    th.rx(main, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp]}))
    (run / "tests").mkdir(exist_ok=True)
    (run / "tests/CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    say("please build the feature")
    r = th.rx(main, env, "plan-submit")
    assert r.returncode == 0, r.stdout + r.stderr
    th.rx(main, env, "ask", "approve-plan")
    say("yes, go ahead")
    r = th.rx(main, env, "approve-plan")
    assert r.returncode == 0, r.stdout + r.stderr


def build(name="main", cfg=None, key=True, with_run=False, path_extra=None) -> World:
    env = th.make_env()
    if path_extra:
        env["PATH"] = path_extra + os.pathsep + env["PATH"]
    base = Path(os.path.realpath(_tmp.mkdtemp(prefix="ratchet-lanes-start-")))
    remote, main = base / "origin.git", base / name
    th.git(base, "init", "-q", "--bare", "-b", "main", str(remote))
    main.mkdir()
    for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        th.git(main, *c)
    (main / "src").mkdir()
    (main / "tests").mkdir()
    (main / "src/a.js").write_text("export const a = 1;\n")
    (main / "tests/a.test.js").write_text("// tests\n")
    r = th.rx(main, env, "init", "main work", "--slug", "main-run")
    assert r.returncode == 0, r.stdout + r.stderr
    if with_run:
        _approved_run(main, env)
    c = jread(main / ".ratchet/config.json")
    c["regression_command"] = "true"
    c.update(cfg or {})
    jwrite(main / ".ratchet/config.json", c)
    (main / ".ratchet/standards.md").write_text("# Standards\n\n1. Keep functions short\n")
    (main / ".ratchet/learnings.md").write_text("# Ratchet learnings\n\n- (2026-10-01) main learning one\n")
    th.git(main, "add", "-A")
    th.git(main, "commit", "-qm", "initial commit")
    th.git(main, "remote", "add", "origin", str(remote))
    th.git(main, "push", "-q", "origin", "main")
    if not with_run:
        import shutil
        shutil.rmtree(main / ".ratchet/runs")
        (main / ".ratchet/active.json").unlink()
    dfolder = Path(_tmp.mkdtemp(prefix="ratchet-lanes-start-dir-"))
    w = World(base, main, remote, env, dfolder)
    if key:
        r = th.cp5r12_director(dfolder, w.denv, main, "director-key")
        assert r.returncode == 0, r.stdout + r.stderr
    return w


def fake_cli(w_dir, name):
    """A script `name` in w_dir that appends its arguments to <name>.marker when run. Returns (dir, marker)."""
    d = Path(w_dir)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / f"{name}.marker"
    s = d / name
    s.write_text(f"#!/bin/sh\necho \"$@\" >> '{marker}'\n")
    s.chmod(s.stat().st_mode | stat.S_IXUSR)
    return d, marker


def tree_bytes(d, skip=()):
    out = {}
    d = Path(d)
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if ".git" in rel.parts or "__pycache__" in rel.parts or p.name.endswith((".lock", ".lock.break")):
            continue
        if any(str(rel).startswith(s) for s in skip):
            continue
        out[str(rel)] = p.read_bytes() if p.is_file() and not p.is_symlink() else ("<dir>" if p.is_dir() else "<link>")
    return out


def state_of(w: World) -> dict:
    """What a refused request must leave exactly as it was."""
    return {
        "heads": gout(w.main, "for-each-ref", "refs/heads"),
        "main_head": gout(w.main, "rev-parse", "HEAD"),
        "worktrees": gout(w.main, "worktree", "list", "--porcelain"),
        "base": sorted(p.name for p in w.base.iterdir()),
        "enrolled": w.enrolled_bytes(),
        "requests": sorted(p.name for p in (w.main / ".ratchet/requests").glob("*")) if (w.main / ".ratchet/requests").exists() else [],
        "remote": gout(w.remote, "for-each-ref"),
        "main_tree": tree_bytes(w.main, skip=(".ratchet/requests",)),
    }


def diff_keys(a, b):
    return [k for k in a if a[k] != b[k]]


def nothing_created(w: World, before: dict, cid: str, what: str):
    after = state_of(w)
    d = diff_keys(before, after)
    check(f"[{cid}] {what}: no branch, worktree, folder, enrollment, request record or file in main changed", not d, d)


def refused(r, cid, what, *needles):
    t = low(r)
    need(f"[{cid}] {what}: a non-zero exit with the engine's own message", known(r) and r.returncode != 0 and t,
         (r.returncode, said(r)[:300]))
    check(f"[{cid}] {what}: no traceback", "traceback" not in t, t[:200])
    for n in needles:
        alts = n if isinstance(n, tuple) else (n,)
        check(f"[{cid}] {what}: the message says {' / '.join(alts)}", any(a in t for a in alts), said(r)[:300])


def ok_json(r, cid, what) -> dict:
    need(f"[{cid}] {what}: exit 0 with the engine's own output", known(r) and r.returncode == 0,
         (r.returncode, said(r)[:300]))
    try:
        return json.loads(r.stdout)
    except Exception as e:
        need(f"[{cid}] {what}: --json prints one JSON document", False, f"{e}: {r.stdout[:200]}")


def worktrees(w: World) -> dict:
    """{branch: path} for every worktree git lists, main included."""
    out, cur = {}, None
    for ln in gout(w.main, "worktree", "list", "--porcelain").splitlines():
        if ln.startswith("worktree "):
            cur = os.path.realpath(ln[9:])
        elif ln.startswith("branch ") and cur:
            out[ln[7:].replace("refs/heads/", "", 1)] = cur
    return out


def lane_of(doc, name):
    ls = [x for x in doc.get("lanes", []) if x.get("name") == name]
    return ls[0] if ls else {}


# ------------------------------------------------------------------------------------------------ cases

def t01_branch_and_plain_worktree_per_lane():
    cid = "CP7-T01"
    w = build()
    head = gout(w.main, "rev-parse", "HEAD").strip()
    tree = gout(w.main, "rev-parse", "HEAD^{tree}").strip()
    r = w.start(js=True)
    doc = ok_json(r, cid, "two-lane request")
    wts = worktrees(w)
    for lane in ("search", "export"):
        check(f"[{cid}] branch {lane} exists and points at main's HEAD", gout(w.main, "rev-parse", lane).strip() == head,
              gout(w.main, "for-each-ref", "refs/heads"))
        p = wts.get(lane)
        check(f"[{cid}] git lists a worktree on branch {lane}", bool(p), wts)
        if p:
            check(f"[{cid}] the {lane} worktree is a sibling folder of the main checkout named for the lane",
                  Path(p).parent == w.main.parent and lane in Path(p).name and Path(p) != w.main, p)
            check(f"[{cid}] the {lane} worktree's file tree equals main's",
                  gout(p, "rev-parse", "HEAD^{tree}").strip() == tree, p)
            check(f"[{cid}] --json names the same worktree and branch for {lane}",
                  os.path.realpath(lane_of(doc, lane).get("worktree", "")) == p and lane_of(doc, lane).get("branch") == lane,
                  doc)
    check(f"[{cid}] main's branch and HEAD are unchanged",
          gout(w.main, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main" and gout(w.main, "rev-parse", "HEAD").strip() == head,
          "")
    check(f"[{cid}] exactly three worktrees now (main and two lanes)", len(wts) == 3, wts)


def t02_lanes_enrolled_without_a_run():
    cid = "CP7-T02"
    w = build()
    before_main = tree_bytes(w.main, skip=(".ratchet/requests",))
    r = w.start(js=True)
    ok_json(r, cid, "two-lane request")
    wts = worktrees(w)
    enrolled = (w.rh() / "enrolled.json").read_text() if (w.rh() / "enrolled.json").exists() else ""
    for lane in ("search", "export"):
        p = wts.get(lane)
        need(f"[{cid}] worktree {lane} exists", bool(p), wts)
        dj = Path(p) / ".ratchet/director.json"
        sig = jread(dj).get("sig") if dj.exists() else None
        check(f"[{cid}] {lane} has .ratchet/director.json with a signature", bool(sig) and isinstance(sig, str), dj)
        check(f"[{cid}] enrolled.json lists the {lane} worktree", p in enrolled, enrolled[:300])
        check(f"[{cid}] {lane} has no active run", not (Path(p) / ".ratchet/active.json").exists(), "")
        runs = Path(p) / ".ratchet/runs"
        check(f"[{cid}] {lane} has no .ratchet/runs entry", not runs.exists() or not any(runs.iterdir()), "")
    after_main = tree_bytes(w.main, skip=(".ratchet/requests",))
    check(f"[{cid}] main's own files are not changed", before_main == after_main,
          [k for k in set(before_main) | set(after_main) if before_main.get(k) != after_main.get(k)])


def t03_lane_agent_runs_init_itself():
    cid = "CP7-T03"
    w = build()
    r = w.start(js=True)
    ok_json(r, cid, "two-lane request")
    p = worktrees(w).get("search")
    need(f"[{cid}] the search worktree exists", bool(p), "")
    tp = Path(p) / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "build the search box"},
                              "timestamp": th.now_dt().isoformat()}) + "\n")
    th.rx(p, w.env, "hook", "prompt", "--host", "claude",
          stdin=json.dumps({"cwd": str(p), "prompt": "build the search box", "session_id": "s-lane-search",
                            "transcript_path": str(tp)}))
    ri = th.rx(p, w.env, "init", "search", "--slug", "search")
    check(f"[{cid}] init succeeds in the lane with no refusal about enrollment",
          ri.returncode == 0 and "enroll" not in said(ri).lower(), said(ri)[:300])
    rs = th.rx(p, w.env, "status", "--json")
    try:
        mode = json.loads(rs.stdout).get("approval_mode")
    except Exception:
        mode = None
    check(f"[{cid}] status --json shows approval_mode director", mode == "director", (mode, said(rs)[:200]))
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    th.rx(p, w.env, "hook", "prompt", "--host", "claude",
          stdin=json.dumps({"cwd": str(p), "prompt": "yes, go ahead", "session_id": "s-lane-search",
                            "transcript_path": str(tp)}))
    ra = th.rx(p, w.env, "approve-plan")
    t = said(ra).lower()
    check(f"[{cid}] a chat reply does not approve the plan: approve-plan is refused", ra.returncode != 0, said(ra)[:300])
    check(f"[{cid}] the refusal asks for a director token", "director" in t, t[:300])
    st = th.rx(p, w.env, "status", "--json")
    try:
        phase = json.loads(st.stdout).get("phase")
    except Exception:
        phase = None
    check(f"[{cid}] the plan is still not approved (phase is not building)", phase is not None and phase != "building", phase)


def t04_each_lane_gets_its_own_brief():
    cid = "CP7-T04"
    w = build()
    r = w.start(js=True)
    doc = ok_json(r, cid, "two-lane request")
    for lane, mine, other in (("search", SEARCH, EXPORT), ("export", EXPORT, SEARCH)):
        e = lane_of(doc, lane)
        b = Path(e.get("brief", "") or "/nonexistent")
        need(f"[{cid}] {lane} has a brief file that exists", b.is_file(), e)
        txt = b.read_text()
        check(f"[{cid}] {lane}'s brief contains the whole request text", REQUEST in txt, txt[:200])
        check(f"[{cid}] {lane}'s brief contains the lane name and its own feature", lane in txt and mine in txt, txt[:300])
        check(f"[{cid}] {lane}'s brief does not contain the other lane's feature", other not in txt, txt[:300])
        wt = e.get("worktree")
        check(f"[{cid}] {lane}'s worktree stays clean (the brief is not a tracked or untracked git change)",
              gout(wt, "status", "--porcelain").strip() == "", gout(wt, "status", "--porcelain"))
    bs = {lane_of(doc, n).get("brief") for n in ("search", "export")}
    check(f"[{cid}] the two lanes have two different brief files", len(bs) == 2, bs)


TEMPLATE = ["mycli", "--cwd", "{worktree}", "--brief", "{brief}", "--name", "{name}"]


def _check_start_cmds(cid, lines_or_starts, w, what):
    """Each lane's command is exactly the template with that lane's own values."""
    seen = {}
    for lane in ("search", "export"):
        wt = worktrees(w).get(lane)
        cmds = [c for c in lines_or_starts if wt and wt in c]
        check(f"[{cid}] {what}: one start command for {lane}", len(cmds) == 1, lines_or_starts)
        if len(cmds) != 1:
            continue
        parts = shlex.split(cmds[0].strip())
        ok = (len(parts) == 7 and parts[0] == "mycli" and parts[1] == "--cwd" and parts[3] == "--brief"
              and parts[5] == "--name" and os.path.realpath(parts[2]) == wt and parts[6] == lane
              and Path(parts[4]).is_file())
        check(f"[{cid}] {what}: {lane}'s command is the template with its own worktree, brief and name", ok, parts)
        seen[lane] = parts
    if len(seen) == 2:
        a, b = seen["search"], seen["export"]
        check(f"[{cid}] {what}: the two commands differ in worktree, brief and name",
              a[2] != b[2] and a[4] != b[4] and a[6] != b[6], seen)


def t05_start_command_from_the_developers_template():
    cid = "CP7-T05"
    bindir = Path(_tmp.mkdtemp(prefix="ratchet-lanes-start-bin-"))
    _, marker = fake_cli(bindir, "mycli")
    w = build(cfg={"lane_start_command": TEMPLATE}, path_extra=str(bindir))
    r = w.start(js=True)
    doc = ok_json(r, cid, "--json with a template")
    _check_start_cmds(cid, [lane_of(doc, n).get("start", "") for n in ("search", "export")], w, "json start field")
    check(f"[{cid}] the fake mycli was never run", not marker.exists(), marker)
    w2 = build(cfg={"lane_start_command": TEMPLATE}, path_extra=str(bindir))
    r2 = w2.start()
    need(f"[{cid}] text mode succeeds", known(r2) and r2.returncode == 0, said(r2)[:300])
    cmds = [ln for ln in r2.stdout.splitlines() if "mycli" in ln]
    _check_start_cmds(cid, cmds, w2, "text output")
    check(f"[{cid}] the fake mycli was never run (text mode)", not marker.exists(), marker)


def t06_plain_words_without_a_template():
    cid = "CP7-T06"
    w = build()
    r = w.start()
    need(f"[{cid}] the command succeeds", known(r) and r.returncode == 0, said(r)[:300])
    out = r.stdout
    t = out.lower()
    wts = worktrees(w)
    for lane in ("search", "export"):
        check(f"[{cid}] the output names the {lane} worktree path", wts.get(lane, "<none>") in out, out[:400])
    files = {m for m in re.findall(r"(?<![\w.\-/])/[^\s'\"),;>]+", out) if Path(m).is_file()}
    check(f"[{cid}] the output names two brief files", len(files) >= 2, files)
    check(f"[{cid}] it says to open the agent CLI in that folder and give it the brief",
          "agent" in t and "brief" in t and ("open" in t or "start" in t), t[:400])
    for bad in ("claude -p", "claude --", "codex exec", "gemini -p", "opencode run", "cursor-agent", "copilot -p"):
        check(f"[{cid}] no made-up launch command '{bad}'", bad not in t, t[:300])


def t07_nothing_needs_paseo():
    cid = "CP7-T07"
    bindir = Path(_tmp.mkdtemp(prefix="ratchet-lanes-start-elsewhere-"))
    _, marker = fake_cli(bindir, "paseo")
    for label, cfg, js, lanes in (("plain, no template", None, False, ["a1=one", "a2=two"]),
                                  ("json, no template", None, True, ["b1=one", "b2=two"]),
                                  ("plain, template", {"lane_start_command": ["mycli", "{worktree}"]}, False, ["c1=one"]),
                                  ("json, template", {"lane_start_command": ["mycli", "{worktree}"]}, True, ["d1=one"])):
        w = build(cfg=cfg)
        r = w.start(lanes=lanes, js=js)
        need(f"[{cid}] {label}: succeeds", known(r) and r.returncode == 0, said(r)[:300])
        check(f"[{cid}] {label}: neither stdout nor stderr says paseo", "paseo" not in said(r).lower(), said(r)[:300])
    check(f"[{cid}] the fake paseo was never run", not marker.exists(), marker)


def t08_space_in_path_stays_one_argument():
    cid = "CP7-T08"
    w = build(name="my main", cfg={"lane_start_command": ["mycli", "--cwd", "{worktree}"]})
    r = w.start(js=True, lanes=[f"search={SEARCH}"])
    doc = ok_json(r, cid, "main with a space in its folder name")
    e = lane_of(doc, "search")
    parts = shlex.split(e.get("start", ""))
    wt = worktrees(w).get("search")
    check(f"[{cid}] the worktree exists", bool(wt) and Path(wt).is_dir(), wt)
    check(f"[{cid}] split like a shell the command is mycli --cwd <one path argument>",
          len(parts) == 3 and parts[:2] == ["mycli", "--cwd"] and os.path.realpath(parts[2]) == wt, parts)
    check(f"[{cid}] the main checkout's folder name really has a space", wt is not None and " " in wt, wt)


def _bad_template_case(cid, value, label):
    w = build(cfg={"lane_start_command": value})
    before = state_of(w)
    r = w.start()
    refused(r, cid, label, "lane_start_command", ("list", "array", "["))
    nothing_created(w, before, cid, label)


def t09_unusable_template_refused_before_anything_is_created():
    cid = "CP7-T09"
    _bad_template_case(cid, "mycli --cwd {worktree}", "a plain string")
    _bad_template_case(cid, ["mycli", "--cwd", "{nope}"], "an unknown placeholder")
    _bad_template_case(cid, [], "an empty list")


def t10_mains_uncommitted_ratchet_settings_are_copied():
    cid = "CP7-T10"
    w = build()
    cfg = jread(w.main / ".ratchet/config.json")
    cfg["ui_reviewers"]["behavior"] = ["codex"]
    jwrite(w.main / ".ratchet/config.json", cfg)
    with open(w.main / ".ratchet/learnings.md", "a") as f:
        f.write("- (2026-10-04) an uncommitted note on main\n")
    head = gout(w.main, "rev-parse", "HEAD").strip()
    cfg_b = (w.main / ".ratchet/config.json").read_bytes()
    lrn_b = (w.main / ".ratchet/learnings.md").read_bytes()
    r = w.start(js=True)
    ok_json(r, cid, "main with uncommitted Ratchet settings")
    check(f"[{cid}] main gains no commit", gout(w.main, "rev-parse", "HEAD").strip() == head, "")
    check(f"[{cid}] main's config.json and learnings.md are byte for byte as before",
          (w.main / ".ratchet/config.json").read_bytes() == cfg_b and (w.main / ".ratchet/learnings.md").read_bytes() == lrn_b, "")
    want = [" M .ratchet/config.json", " M .ratchet/learnings.md"]
    check(f"[{cid}] main still lists exactly those two paths as modified",
          sorted(gout(w.main, "status", "--porcelain").splitlines()) == want, gout(w.main, "status", "--porcelain"))
    wts = worktrees(w)
    for lane in ("search", "export"):
        p = wts.get(lane)
        need(f"[{cid}] {lane} worktree exists", bool(p), wts)
        check(f"[{cid}] {lane}'s config.json equals main's, byte for byte", (Path(p) / ".ratchet/config.json").read_bytes() == cfg_b, "")
        check(f"[{cid}] {lane}'s learnings.md equals main's, byte for byte", (Path(p) / ".ratchet/learnings.md").read_bytes() == lrn_b, "")
        check(f"[{cid}] {lane}'s git status lists those paths as uncommitted changes",
              sorted(gout(p, "status", "--porcelain").splitlines()) == want, gout(p, "status", "--porcelain"))
        check(f"[{cid}] the {lane} branch HEAD is still main's HEAD", gout(p, "rev-parse", "HEAD").strip() == head, "")


def t11_main_with_uncommitted_project_changes_refused():
    cid = "CP7-T11"
    w = build()

    def dirty_shared():
        cfg = jread(w.main / ".ratchet/config.json")
        cfg["max_behavior_attempts"] = 9
        jwrite(w.main / ".ratchet/config.json", cfg)
        with open(w.main / ".ratchet/learnings.md", "a") as f:
            f.write("- (2026-10-04) an uncommitted note on main\n")
    dirty_shared()
    (w.main / "src/a.js").write_text("export const a = 42; // developer's own work\n")
    before = state_of(w)
    r = w.start()
    refused(r, cid, "a modified tracked file", "src/a.js", ("commit", "set aside", "stash"))
    nothing_created(w, before, cid, "a modified tracked file")
    gitx(w.main, "checkout", "--", "src/a.js")
    (w.main / "src/b.js").write_text("export const b = 1;\n")
    th.git(w.main, "add", "src/b.js")
    before = state_of(w)
    staged = gout(w.main, "diff", "--cached")
    r = w.start()
    refused(r, cid, "a staged new file", "src/b.js", ("commit", "set aside", "stash"))
    nothing_created(w, before, cid, "a staged new file")
    check(f"[{cid}] the staged file is still staged, byte for byte", gout(w.main, "diff", "--cached") == staged, "")
    check(f"[{cid}] no worktree got a copy of Ratchet's shared files", len(worktrees(w)) == 1, worktrees(w))


def t12_existing_lane_branch_refused_even_for_a_later_lane():
    cid = "CP7-T12"
    w = build()
    th.git(w.main, "branch", "export")
    export_sha = gout(w.main, "rev-parse", "export")
    before = state_of(w)
    r = w.start(lanes=[f"search={SEARCH}", f"export={EXPORT}"])
    refused(r, cid, "an existing branch", "export", ("another", "different"), "name")
    nothing_created(w, before, cid, "an existing branch")
    check(f"[{cid}] no search branch was created", gitx(w.main, "rev-parse", "--verify", "-q", "search").returncode != 0, "")
    check(f"[{cid}] the existing export branch is untouched", gout(w.main, "rev-parse", "export") == export_sha, "")


def t13_existing_worktree_folder_refused_and_left_alone():
    cid = "CP7-T13"
    w = build()
    cands = [w.base / "export", w.base / f"{w.main.name}-export", w.base / f"{w.main.name}.export",
             w.base / f"{w.main.name}_export"]
    for c in cands:
        c.mkdir()
        (c / "notes.txt").write_text("keep me\n")
    before = state_of(w)
    r = w.start()
    need(f"[{cid}] the run exits non-zero", known(r) and r.returncode != 0, said(r)[:300])
    t = raw_said(r)
    check(f"[{cid}] the message names the existing folder", any(str(c) in t for c in cands), t[:300])
    check(f"[{cid}] notes.txt is unchanged everywhere", all((c / "notes.txt").read_text() == "keep me\n" for c in cands), "")
    nothing_created(w, before, cid, "an existing worktree folder")


def t14_single_lane_request():
    cid = "CP7-T14"
    w = build()
    r = w.start(request="Add search", lanes=[f"search={SEARCH}"], js=True)
    doc = ok_json(r, cid, "one lane, --json")
    check(f"[{cid}] --json has a lanes list of length one", len(doc.get("lanes", [])) == 1, doc)
    wt = worktrees(w).get("search")
    check(f"[{cid}] one branch and one sibling worktree for search", bool(wt) and Path(wt).parent == w.main.parent
          and len(worktrees(w)) == 2, worktrees(w))
    if wt:
        check(f"[{cid}] the lane is enrolled with the director", (Path(wt) / ".ratchet/director.json").exists(), "")
    e = lane_of(doc, "search")
    check(f"[{cid}] it has a brief and a start field", Path(e.get("brief", "") or "/nonexistent").is_file() and bool(e.get("start")), e)
    recs = list((w.main / ".ratchet/requests").glob("*.json"))
    need(f"[{cid}] one request record exists", len(recs) == 1, recs)
    rec = jread(recs[0])
    check(f"[{cid}] the record lists exactly that one lane", [x.get("name") for x in rec.get("lanes", [])] == ["search"], rec)
    w2 = build()
    r2 = w2.start(request="Add search", lanes=[f"search={SEARCH}"])
    t2 = r2.stdout
    wt2 = worktrees(w2).get("search")
    check(f"[{cid}] text mode succeeds and names the worktree", known(r2) and r2.returncode == 0 and bool(wt2) and wt2 in t2,
          said(r2)[:300])


def t15_request_recorded_and_main_stays_clean():
    cid = "CP7-T15"
    w = build()
    r = w.start(js=True)
    doc = ok_json(r, cid, "two-lane request")
    f = w.main / ".ratchet/requests" / f"{doc.get('request')}.json"
    need(f"[{cid}] the record .ratchet/requests/<request>.json exists in main", f.is_file(), doc)
    rec = jread(f)
    check(f"[{cid}] the record holds the request text", rec.get("request") == REQUEST or REQUEST in json.dumps(rec), rec)
    check(f"[{cid}] the record holds a time", any(isinstance(rec.get(k), str) and rec.get(k) for k in
          ("time", "created_at", "started_at", "at")), list(rec))
    lanes = rec.get("lanes", [])
    check(f"[{cid}] the record lists the lanes in the order given", [x.get("name") for x in lanes] == ["search", "export"], lanes)
    wts = worktrees(w)
    for x, feat in zip(lanes, (SEARCH, EXPORT)):
        n = x.get("name")
        check(f"[{cid}] {n}: feature, branch, worktree and brief match what git and --json show",
              x.get("feature") == feat and x.get("branch") == n and os.path.realpath(x.get("worktree", "")) == wts.get(n)
              and x.get("brief") == lane_of(doc, n).get("brief") and Path(x.get("brief", "/x")).is_file(), x)
    check(f"[{cid}] git status in main is empty afterward", gout(w.main, "status", "--porcelain").strip() == "",
          gout(w.main, "status", "--porcelain"))


def _no_state(w, cid, what):
    check(f"[{cid}] {what}: no lane branch", gout(w.main, "for-each-ref", "refs/heads").count("\n") == 1, gout(w.main, "for-each-ref", "refs/heads"))
    check(f"[{cid}] {what}: only the main worktree", len(worktrees(w)) == 1, worktrees(w))
    check(f"[{cid}] {what}: no request record", not list((w.main / ".ratchet/requests").glob("*")) if (w.main / ".ratchet/requests").exists() else True, "")


def t16_only_the_director_can_start_lanes():
    cid = "CP7-T16"
    w = build()
    cmd = f"python3 {ENGINE} --root {w.main} lanes-start --request x --lane a=b"
    g = th.cp4x_guard_tool(w.main, w.env, "Bash", {"command": cmd}, session="s-builder")
    before = state_of(w)   # the guard hook itself writes its own records into main, so the snapshot comes after it
    m = (g.stderr + g.stdout).lower()
    check(f"[{cid}] the guard blocks the builder's tool call with exit 2", g.returncode == 2, (g.returncode, m[:200]))
    check(f"[{cid}] the block says director commands are reserved for the director session",
          "director" in m and "reserved" in m and "session" in m, m[:300])
    r = w.start(request="x", lanes=["a=b"], env=w.env, hook=False)
    t = low(r)
    need(f"[{cid}] a direct run without the director role exits non-zero", known(r) and r.returncode != 0 and t, said(r)[:300])
    check(f"[{cid}] it says only the director session can start lanes",
          "director" in t and "session" in t and "lane" in t, t[:300])
    nothing_created(w, before, cid, "a non-director run")


def t17_without_a_director_key_nothing_is_half_created():
    cid = "CP7-T17"
    w = build(key=False)
    check(f"[{cid}] fixture: no director key", not (w.rh() / "director.key").exists(), "")
    before = state_of(w)
    r = w.start()
    refused(r, cid, "no director key", "director-key")
    nothing_created(w, before, cid, "no director key")


def t18_failed_enrollment_leaves_no_lane_behind():
    cid = "CP7-T18"
    w = build()
    ep = w.rh() / "enrolled.json"
    ep.write_text("{ this is not json")
    before = state_of(w)
    r = w.start()
    refused(r, cid, "a damaged enrolled.json", ("enrolled", "enroll"), ("can't be read", "cannot be read", "damaged", "unreadable", "not valid"))
    check(f"[{cid}] the damaged file is not overwritten", ep.read_text() == "{ this is not json", ep.read_text()[:100])
    nothing_created(w, before, cid, "a damaged enrolled.json")
    _no_state(w, cid, "a damaged enrolled.json")


def t19_lanes_start_from_the_checked_out_branch():
    cid = "CP7-T19"
    w = build()
    main_sha = gout(w.main, "rev-parse", "main").strip()
    th.git(w.main, "checkout", "-q", "-b", "develop")
    (w.main / "src/dev.js").write_text("export const dev = 1;\n")
    th.git(w.main, "add", "-A")
    th.git(w.main, "commit", "-qm", "develop work")
    dev = gout(w.main, "rev-parse", "develop").strip()
    r = w.start(js=True)
    ok_json(r, cid, "main checkout on develop")
    for lane in ("search", "export"):
        check(f"[{cid}] the {lane} branch points at develop's HEAD", gout(w.main, "rev-parse", lane).strip() == dev,
              gout(w.main, "for-each-ref", "refs/heads"))
    check(f"[{cid}] the branch named main is untouched", gout(w.main, "rev-parse", "main").strip() == main_sha, "")


def t20_nothing_else_is_touched():
    cid = "CP7-T20"
    w = build(with_run=True)
    cfg = jread(w.main / ".ratchet/config.json")
    cfg["max_behavior_attempts"] = 9
    jwrite(w.main / ".ratchet/config.json", cfg)
    cfg_b = (w.main / ".ratchet/config.json").read_bytes()
    head = gout(w.main, "rev-parse", "HEAD").strip()
    remote = gout(w.remote, "for-each-ref")

    def view():
        s = th.rx(w.main, w.env, "status", "--json")
        try:
            d = json.loads(s.stdout)
        except Exception:
            return {"raw": s.stdout[:200] + s.stderr[:200]}
        return {k: d.get(k) for k in ("phase", "current", "step", "next_action", "next") if k in d}
    v0 = view()
    r = w.start(js=True)
    ok_json(r, cid, "main with its own run mid-step")
    check(f"[{cid}] the bare remote's refs are identical", gout(w.remote, "for-each-ref") == remote, "")
    for lane in ("search", "export"):
        p = worktrees(w).get(lane)
        need(f"[{cid}] {lane} worktree exists", bool(p), "")
        extra = [d for d in (".claude", ".codex", ".gemini", ".opencode", ".cursor") if (Path(p) / d).exists()]
        check(f"[{cid}] {lane} holds no agent CLI folder", not extra, extra)
        check(f"[{cid}] {lane}'s git status lists only the copied .ratchet/config.json",
              gout(p, "status", "--porcelain").splitlines() == [" M .ratchet/config.json"], gout(p, "status", "--porcelain"))
    v1 = view()
    check(f"[{cid}] main's own run has the same phase, step and next action", v0 == v1 and "raw" not in v0, (v0, v1))
    check(f"[{cid}] main's HEAD and config.json bytes are unchanged",
          gout(w.main, "rev-parse", "HEAD").strip() == head and (w.main / ".ratchet/config.json").read_bytes() == cfg_b, "")
    st = jread(w.main / ".ratchet/runs/main-run/state.json")
    check(f"[{cid}] no integrity or approve-changes demand appeared in main's run",
          not st.get("integrity") and "approve-changes" not in json.dumps(st.get("awaiting", "")), st.get("phase"))


def t21_docs_name_the_command():
    cid = "CP7-T21"
    docs = {"README.md": ROOT / "README.md", "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
            "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md"}
    txt = {k: p.read_text() for k, p in docs.items()}
    for k, t in txt.items():
        check(f"[{cid}] {k} mentions lanes-start", "lanes-start" in t, "")
    s = txt["schemas.md"]
    check(f"[{cid}] schemas.md lists lane_start_command with its three placeholders",
          "lane_start_command" in s and all(x in s for x in ("{worktree}", "{brief}", "{name}")), "")
    check(f"[{cid}] schemas.md describes the request record under .ratchet/requests/", ".ratchet/requests/" in s, "")
    sk = txt["SKILL.md"]
    i = sk.find("lanes-start")
    near = sk[max(0, i - 700): i + 900].lower() if i >= 0 else ""
    check(f"[{cid}] SKILL.md's director section says the lane agent runs init itself and Ratchet only prints the start command",
          "init" in near and ("prints" in near or "only print" in near) and "itself" in near, near[:300])
    for k, t in txt.items():
        for para in re.split(r"\n\s*\n", t):
            if "paseo" in para.lower():
                pl = para.lower()
                check(f"[{cid}] {k}: a Paseo mention presents it as optional, never required",
                      "optional" in pl and not re.search(r"(requires|requires? the|needs|must use) paseo", pl), para[:200])
    eng = th.cp5y_load_engine()
    check(f"[{cid}] DIRECTOR_CALL_RE matches lanes-start",
          bool(eng.DIRECTOR_CALL_RE.search("python3 ratchet.py --root /x lanes-start --request a --lane b=c")), "")
    check(f"[{cid}] DIRECTOR_CALL_RE still matches integrate and does not match plain lanes",
          bool(eng.DIRECTOR_CALL_RE.search("python3 ratchet.py integrate x"))
          and not eng.DIRECTOR_CALL_RE.search("python3 ratchet.py --root /x lanes --json"), "")


def t22_bad_lane_lists_refused():
    cid = "CP7-T22"
    cases = [("a repeated lane name", [f"search={SEARCH}", "search=other"], ("distinct", "twice", "repeat", "duplicate", "same")),
             ("an unsafe lane name", ["../escape=Something"], ("name",)),
             ("an empty feature", ["search="], ("feature",)),
             ("no lanes at all", [], ("lane",))]
    for label, lanes, words in cases:
        w = build()
        before = state_of(w)
        outside = sorted(p.name for p in w.base.parent.iterdir()) if False else None
        r = w.start(lanes=lanes)
        refused(r, cid, label, words)
        nothing_created(w, before, cid, label)
        check(f"[{cid}] {label}: nothing escaped to the folder above the sibling worktrees",
              not (w.base.parent / "escape").exists(), "")


def t23_request_naming_and_refused_leaves_no_record():
    cid = "CP7-T23"
    w = build()
    r1 = w.start(js=True)
    d1 = ok_json(r1, cid, "no --name")
    rd = w.main / ".ratchet/requests"
    f1 = sorted(p.name for p in rd.glob("*.json")) if rd.exists() else []
    check(f"[{cid}] the first record is named from a slug of the request text",
          len(f1) == 1 and re.fullmatch(r"[a-z0-9-]+", f1[0][:-5] or "!") is not None and all(x in f1[0] for x in ("add", "search", "export")), f1)
    check(f"[{cid}] --json's request field equals that name", f1 == [f"{d1.get('request')}.json"], (f1, d1.get("request")))
    r2 = w.start(request="Nightly chores", lanes=["lint=Run the linter", "docs=Update the docs"], name="nightly", js=True)
    d2 = ok_json(r2, cid, "--name nightly")
    check(f"[{cid}] the second record is .ratchet/requests/nightly.json and --json says nightly",
          (rd / "nightly.json").is_file() and d2.get("request") == "nightly", (d2.get("request"), list(rd.glob("*"))))
    before = state_of(w)
    r3 = w.start(request="Third thing", lanes=["search=again"])
    refused(r3, cid, "an existing branch", "search")
    after = sorted(p.name for p in rd.glob("*.json"))
    check(f"[{cid}] after the refused third run the folder holds exactly the two earlier records",
          after == sorted(f1 + ["nightly.json"]), after)
    nothing_created(w, before, cid, "the refused third request")


TESTS = [
    ("CP7-T01", t01_branch_and_plain_worktree_per_lane),
    ("CP7-T02", t02_lanes_enrolled_without_a_run),
    ("CP7-T03", t03_lane_agent_runs_init_itself),
    ("CP7-T04", t04_each_lane_gets_its_own_brief),
    ("CP7-T05", t05_start_command_from_the_developers_template),
    ("CP7-T06", t06_plain_words_without_a_template),
    ("CP7-T07", t07_nothing_needs_paseo),
    ("CP7-T08", t08_space_in_path_stays_one_argument),
    ("CP7-T09", t09_unusable_template_refused_before_anything_is_created),
    ("CP7-T10", t10_mains_uncommitted_ratchet_settings_are_copied),
    ("CP7-T11", t11_main_with_uncommitted_project_changes_refused),
    ("CP7-T12", t12_existing_lane_branch_refused_even_for_a_later_lane),
    ("CP7-T13", t13_existing_worktree_folder_refused_and_left_alone),
    ("CP7-T14", t14_single_lane_request),
    ("CP7-T15", t15_request_recorded_and_main_stays_clean),
    ("CP7-T16", t16_only_the_director_can_start_lanes),
    ("CP7-T17", t17_without_a_director_key_nothing_is_half_created),
    ("CP7-T18", t18_failed_enrollment_leaves_no_lane_behind),
    ("CP7-T19", t19_lanes_start_from_the_checked_out_branch),
    ("CP7-T20", t20_nothing_else_is_touched),
    ("CP7-T21", t21_docs_name_the_command),
    ("CP7-T22", t22_bad_lane_lists_refused),
    ("CP7-T23", t23_request_naming_and_refused_leaves_no_record),
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
