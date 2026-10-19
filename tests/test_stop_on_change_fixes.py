#!/usr/bin/env python3
"""
CP4 fix-round tests (adversarial round 1): the code watch under a running suite.
    python3 tests/test_stop_on_change_fixes.py

Black-box like tests/test_stop_on_change.py (whose project helpers, engine runner and fake-suite harness it
reuses; that file is frozen, so every new case lives here), plus a few direct calls into the engine for the parts
that have no black-box trigger. Temp folders only (_tmp.mkdtemp), every process started here is killed at the end
of its case, the environment is restored, children get explicit signal dispositions, the engine sees an idle
machine (RATCHET_FAKE_LOADAVG=0).
  - [CP4-A1]  a change found only when the command ended (it ran to its end on its own) is a refusal, not a stop:
              it names both causes (the command wrote the file, or it was edited while the command ran), gives
              the right guidance per file (an edit of your own: keep it; tracked and written by the command: put
              it back from the tree the run started on, the step's uncommitted work included, never HEAD, then fix
              the command or take the file out of the fingerprint; a file untracked at HEAD the same way, since
              the start tree holds it; nothing is ever deleted), counts nothing however often it repeats, and `complete` refuses the
              same way without a stale-gate detour; a helper the ended command left in its group is stopped too;
              a second mid-run stop on the same file adds the writer hint but stays uncounted
  - [CP4-A2]  a look git cannot take while the suite runs is skipped and noted, the suite keeps running and a
              later edit still stops it with nothing left behind; a failure on the last look ends the call with
              nothing running and no lock; whatever escapes the wait kills the command's group first; the
              skipped-look note is per command, never carried into the next command's log
  - [CP4-A3]  a failed diff-tree between two differing trees is never 'nothing changed'
  - [CP4-A4]  a deleted file stops the run; a generated file that only appeared (a snapshot: untracked, not
              ignored, not project source) does not; a suite_watch_seconds change after plan approval is refused
              as a locked rule
  - [CP4-A5]  the test-runner agent knows the stopped outcome
  - [CP4-C3]  the stop's grace covers the whole group, not only the shell leader
  - [CP4-C4]  a project below the repository top: a chat line in its transcript never stops a run, an edit does
  - [CP4-C5]  a periodic look reuses one kept index (the same tree id as the full fingerprint)
  - [CP4-A6]  a new project source file added while the suite runs stops it within the watch interval (behavior
              and complete), naming the file, freeing the lock and counting nothing; generated build, cache,
              report, snapshot and scratch output written all through a run (not gitignored) never stops it; a
              quick suite that itself writes a new source file is refused when it ends, with remove-or-ignore
              guidance for that file, uncounted; a `bin` folder holds source (Rust's src/bin/main.rs is a change)
              unless it is .NET's bin/Debug or bin/Release output
  - [CP4-A7]  any added file git would commit is new source: an HTML template and a module in a `reports/`
              folder added while the suite runs stop it, naming the file and counting nothing; build output such
              as build/out.js and dist/app.min.js that appears during the run does not

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
import traceback
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                        # noqa: E402
import trust_helpers as tt         # noqa: E402
import test_stop_on_change as q4   # noqa: E402  (Fake, project, run_then_edit, rx, gate, tagged, reap, ...)

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_ERRORS: dict = {}
ID_RE = re.compile(r"\[(CP4-[A-Z]\d+)\]")
PY = shlex.quote(sys.executable)
SLOW = q4.SLOW_SECONDS


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:160]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


# ---- a second fake suite with the modes these cases need ----------------------------------------------------------------

FAKE2 = Path(_tmp.mkdtemp(prefix="ratchet-q4f-scripts-")) / "q4_suite_fixes.py"
FAKE2.write_text(f"SLOW = {SLOW}\n" + r'''
import json, os, signal, stat, subprocess, sys, time
from pathlib import Path

for name in ("SIGTERM", "SIGINT", "SIGHUP"):
    sig = getattr(signal, name, None)
    if sig is not None:
        signal.signal(sig, signal.SIG_DFL)
work = Path(sys.argv[1])
modes = json.loads((work / "modes.json").read_text())
n = int((work / "n").read_text().strip() or "0") + 1 if (work / "n").exists() else 1
(work / "n").write_text(str(n))
mode = modes[min(n, len(modes)) - 1]
(work / f"call-{n}.started").write_text(str(time.time()))
root = Path(os.getcwd())

def ready():
    (work / f"call-{n}.pids").write_text(json.dumps([os.getpid()]))
    (work / f"call-{n}.ready").write_text("ready")

def nap(sec):
    end = time.time() + sec
    while time.time() < end:
        time.sleep(0.1)

if mode.startswith("exit:"):
    sys.exit(int(mode[5:]))
if mode.startswith("sleep:"):
    nap(float(mode[6:]))
    sys.exit(0)
if mode == "rewrite":            # the command rewrites a tracked file it started with, then ends on its own
    with (root / "src/a.js").open("a") as f:
        f.write("// refreshed %f\n" % time.time())
    sys.exit(0)
if mode == "rewrite-untracked":  # the command rewrites an untracked (not ignored) file it started with, then ends
    with (root / "local.txt").open("a") as f:
        f.write("refreshed %f\n" % time.time())
    sys.exit(0)
if mode == "helper-exit":        # a helper in the command's own group outlives the command, which ends on its own
    helper = subprocess.Popen([sys.executable, "-c", "import time\nfor _ in range(%d): time.sleep(0.1)" % (SLOW * 10)],
                              stdin=subprocess.DEVNULL)
    (work / f"call-{n}.pids").write_text(json.dumps([os.getpid(), helper.pid]))
    (work / f"call-{n}.ready").write_text("ready")
    nap(2.5)
    sys.exit(0)
if mode == "unreadable-fail":    # looks are skipped for a while, then the command fails on its own
    p = root / "secret.txt"
    p.write_text("x")
    p.chmod(0)
    nap(2.5)
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    p.unlink()
    sys.exit(1)
if mode == "appear":             # the command adds a file that is neither tracked nor ignored, then ends
    (root / "tests/__snapshots__").mkdir(parents=True, exist_ok=True)
    (root / "tests/__snapshots__/x.snap").write_text("snap %f\n" % time.time())
    nap(2.0)
    sys.exit(0)
if mode == "slow":
    ready()
    nap(SLOW)
    sys.exit(0)
if mode == "unreadable":         # a file git cannot open sits in the tree for a while, then goes away
    p = root / "secret.txt"
    p.write_text("x")
    p.chmod(0)
    ready()
    nap(2.5)
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    p.unlink()
    nap(SLOW)
    sys.exit(0)
if mode == "unreadable-exit":    # a file git cannot open is left behind as the command ends
    p = root / "secret.txt"
    p.write_text("x")
    p.chmod(0)
    sys.exit(0)
if mode == "gen-output":         # for ~3 s the command keeps writing generated output, none of it gitignored
    targets = ["build/gen.js", "dist/app.js", "out/main.py", "coverage/lcov-report/x.js", "htmlcov/index.py",
               ".cache/mod.py", ".pytest_cache/v/x.py", "node_modules/pkg/index.js", "__pycache__/m.py",
               "tests/__snapshots__/a.test.js.snap", "src/__snapshots__/b.js", "reports/junit.xml",
               "test-results/r.json", "tmp/scratch.py", "scratch/note.js", "logs/run.log", "src/app.min.js",
               "src/app.js.map", "results.xml", "notes.txt"]
    end = time.time() + 3.0
    i = 0
    while time.time() < end:
        i += 1
        for t in targets:
            (root / t).parent.mkdir(parents=True, exist_ok=True)
            (root / t).write_text("// gen %d at %f\n" % (i, time.time()))
        time.sleep(0.3)
    sys.exit(0)
if mode == "write-source":       # the command itself adds a new source file to the project, then ends at once
    (root / "src/made_by_suite.js").write_text("export const m = %f;\n" % time.time())
    sys.exit(0)
if mode == "slow-cleanup":       # on SIGTERM: half a second of cleanup, a marker, then exit
    def on_term(signum, frame):
        nap(0.5)
        (work / f"call-{n}.cleaned").write_text("cleaned")
        sys.exit(143)
    signal.signal(signal.SIGTERM, on_term)
    ready()
    nap(SLOW)
    sys.exit(0)
print("unknown mode " + mode)
sys.exit(3)
''')


class Fake2(q4.Fake):
    @property
    def cmd(self) -> str:
        return f"{PY} {shlex.quote(str(FAKE2))} {shlex.quote(str(self.work))}"

    def cleaned(self, n) -> bool:
        return (self.work / f"call-{n}.cleaned").exists()


def behavior_project2(modes, config=(), **kw):
    env = q4.make_env()
    fk = Fake2(modes, "t")
    d = q4.project(env, fk.cmd, config, **kw)
    q4.to_implemented(d, env)
    return d, env, fk


def stop_record(d) -> dict:
    try:
        return json.loads((q4.evidence(d) / "stopped-runs.json").read_text())
    except (OSError, ValueError):
        return {}


def _engine():
    spec = importlib.util.spec_from_file_location("ratchet_engine_cp4_fixes", str(tt.ENGINE))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- CP4-A1: a command that rewrites a file it started with ------------------------------------------------------------

RESTORE_RE = re.compile(r"git checkout ([0-9a-f]{40}) -- src/a\.js")   # restore from the tree the run started on


def says_both_causes(text) -> bool:
    return re.search(r"found (only )?when it ended", text) is not None and "command itself wrote" in text \
        and "edited while it ran" in text


def a1_behavior():
    d, env, fk = behavior_project2(["exit:1", "rewrite", "rewrite", "sleep:1"], ["suite_watch_seconds=30"])
    before = q4.gate(d)
    r1 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A1] first run: a change found only when the command ended is a refusal (exit 2) that says the code changed",
          r1.rc == 2 and q4.says_changed(r1.text) and "src/a.js" in r1.text, (r1.rc, r1.text[-600:]))
    check("[CP4-A1] first run: the message names both possible writers (the command itself, or an edit while it ran)",
          says_both_causes(r1.text), r1.text[-600:])
    check("[CP4-A1] first run: a tracked file gets the restore command and the take-out-of-git advice, then run again",
          RESTORE_RE.search(r1.text) and "git rm --cached src/a.js" in r1.text and ".gitignore" in r1.text
          and "keep your edit" in r1.text
          and "behavior CP1" in r1.text and q4.says_again(r1.text), r1.text[-600:])
    check("[CP4-A1] first run: it is not called a stop, and not the old 'changed files in the project' refusal",
          not re.search(r"\bstopped\b", r1.text, re.I) and "changed files in the project" not in r1.text, r1.text[-600:])
    check("[CP4-A1] first run: nothing was counted and no stop was recorded (the command was not stopped)",
          q4.no_runs(q4.gate(d)) == q4.no_runs(before) and fk.cmd not in stop_record(d), (q4.gate(d), stop_record(d)))
    logs = q4.behavior_logs(d)
    text = q4.read(logs[-1]) if logs else ""
    check("[CP4-A1] first run: the log keeps the command's exit and says the change was found when it ended",
          "# exit=0" in text and "found when it ended" in text and "src/a.js" in text, text[:400])
    r2 = q4.rx(d, env, "behavior", "CP1", cap=60)
    g = q4.gate(d)
    check("[CP4-A1] second run without restoring: the same refusal, still uncounted (never a failed attempt, never blocked)",
          r2.rc == 2 and says_both_causes(r2.text) and g.get("attempts") == 0 and g.get("status") != "failed"
          and not q4.history(d, "behavior_failed") and q4.state(d).get("phase") != "blocked", (r2.rc, g, r2.text[-600:]))
    check("[CP4-A1] no lock is left and nothing of the command is running", q4.locks(d) == [] and not q4.tagged(env[q4.TAG_VAR]),
          (q4.locks(d), q4.tagged(env[q4.TAG_VAR])))
    tt.git(d, "checkout", "--", "src/a.js")
    r3 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A1] restored, and the command no longer writes: the next run passes on its first counted attempt",
          r3.rc == 0 and q4.gate(d).get("status") == "passed" and q4.gate(d).get("attempts") == 1, (r3.rc, q4.gate(d)))


def a1_keeps_own_work():
    d, env, fk = behavior_project2(["exit:1", "rewrite", "sleep:1"], ["suite_watch_seconds=30"])
    (d / "src/a.js").write_text("export const a = 1;\nexport const mine = 2;\n")  # the step's own uncommitted edit
    r1 = q4.rx(d, env, "behavior", "CP1", cap=60)
    m = RESTORE_RE.search(r1.text)
    check("[CP4-A1] the refusal names the restore from the tree the run started on, and says it keeps uncommitted work",
          r1.rc == 2 and m is not None and "uncommitted work" in r1.text and "not HEAD" in r1.text, r1.text[-700:])
    check("[CP4-A1] and says an edit of your own is kept, not restored", "keep your edit" in r1.text, r1.text[-700:])
    if m:
        rr = subprocess.run(["git", "checkout", m.group(1), "--", "src/a.js"], cwd=str(d), capture_output=True, text=True)
        text = q4.read(d / "src/a.js")
        check("[CP4-A1] following the guidance puts the file back as it was when the run began: the step's edit survives, the rewrite is gone",
              rr.returncode == 0 and "mine = 2" in text and "refreshed" not in text, (rr.returncode, rr.stderr, text))
        check("[CP4-A1] HEAD's version was not what came back", "mine = 2" not in tt.git(d, "show", "HEAD:src/a.js").stdout)
    r2 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A1] the next run passes with the edit in place", r2.rc == 0 and q4.gate(d).get("status") == "passed"
          and "mine = 2" in q4.read(d / "src/a.js"), (r2.rc, r2.text[-300:]))


def a1_untracked():
    d, env, fk = behavior_project2(["exit:1", "rewrite-untracked", "sleep:1"], ["suite_watch_seconds=30"])
    (d / "local.txt").write_text("local\n")  # the step's own new file: untracked at HEAD, there before the run
    r1 = q4.rx(d, env, "behavior", "CP1", cap=60)
    m = re.search(r"git checkout ([0-9a-f]{40}) -- local\.txt", r1.text)
    check("[CP4-A1] a file untracked at HEAD that the command rewrote gets the same restore-from-the-start-tree guidance, never 'delete'",
          r1.rc == 2 and says_both_causes(r1.text) and m is not None and "keep your edit" in r1.text
          and ".gitignore" in r1.text and not re.search(r"\bdelete\b(?! a file the run started with)", r1.text, re.I),
          (r1.rc, r1.text[-700:]))
    if m:
        rr = subprocess.run(["git", "checkout", m.group(1), "--", "local.txt"], cwd=str(d), capture_output=True, text=True)
        check("[CP4-A1] following it restores the new file as it was when the run began (the start tree holds it), not deleted",
              rr.returncode == 0 and (d / "local.txt").exists() and q4.read(d / "local.txt") == "local\n",
              (rr.returncode, rr.stderr, q4.read(d / "local.txt")))
        check("[CP4-A1] HEAD never had the file", "local.txt" not in tt.git(d, "ls-tree", "-r", "--name-only", "HEAD").stdout)
    r2 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A1] with the file restored and the command quiet, the next run passes and the file is still there",
          r2.rc == 0 and q4.gate(d).get("status") == "passed" and q4.read(d / "local.txt") == "local\n", (r2.rc, r2.text[-400:]))


def a1_complete():
    env = q4.make_env()
    fk_t = q4.Fake(["exit:1", "exit:0"], "t")
    fk_r = Fake2(["rewrite", "rewrite", "sleep:1"], "r")
    d = q4.project(env, fk_t.cmd, ["suite_watch_seconds=30", f"regression_command={fk_r.cmd}"])
    q4.to_implemented(d, env)
    q4.to_complete(d, env)
    h = q4.head(d)
    r1 = q4.rx(d, env, "complete", "CP1", cap=90)
    check("[CP4-A1] complete, first run: the suite's rewrite found when it ended is refused saying the code changed, both causes",
          r1.rc == 2 and q4.says_changed(r1.text) and says_both_causes(r1.text), (r1.rc, r1.text[-600:]))
    check("[CP4-A1] complete: the restore command comes first, then run complete again; never 'changed files in the project'",
          RESTORE_RE.search(r1.text) and "complete CP1" in r1.text and q4.says_again(r1.text)
          and "changed files in the project" not in r1.text, r1.text[-600:])
    check("[CP4-A1] complete: the gates are not marked stale by the refusal itself",
          q4.gate(d).get("status") == "passed" and q4.gate(d, "adversarial").get("status") == "passed", q4.cps(d)["gates"])
    tt.git(d, "checkout", "--", "src/a.js")  # the agent follows the message: restore, then run again
    r2 = q4.rx(d, env, "complete", "CP1", cap=90)
    check("[CP4-A1] complete, second run after restoring: the same refusal, no stale-gate detour, no review round asked",
          r2.rc == 2 and says_both_causes(r2.text) and "cannot complete" not in r2.text and "stale" not in r2.text
          and "adversaries must review" not in r2.text, (r2.rc, r2.text[-600:]))
    st = q4.state(d)
    check("[CP4-A1] complete: nothing committed, the step not done, the flaky allowance unspent, no lock",
          q4.head(d) == h and q4.cps(d).get("status") != "done" and not st.get("regression_flaky_used")
          and q4.locks(d) == [], (q4.head(d) == h, q4.cps(d).get("status"), st.get("regression_flaky_used"), q4.locks(d)))
    tt.git(d, "checkout", "--", "src/a.js")
    r3 = q4.rx(d, env, "complete", "CP1", cap=90)
    check("[CP4-A1] complete: once the suite no longer writes, the step completes",
          r3.rc == 0 and q4.cps(d).get("status") == "done", (r3.rc, r3.text[-400:]))


def a1_midrun_hint():
    d, env, fk = behavior_project2(["exit:1", "slow", "slow"], ["suite_watch_seconds=0.3"])
    try:
        r1 = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        q4.reap(fk.work, env[q4.TAG_VAR])
        check("[CP4-A1] mid-run, first stop: the plain stop, without the writer hint",
              r1.ready and r1.rc == 2 and q4.says_changed(r1.text) and "if you did not edit" not in r1.text, r1.text[-500:])
        rec = stop_record(d)
        check("[CP4-A1] mid-run: the stop is recorded next to the log, keyed by the command, naming the file",
              rec.get(fk.cmd, {}).get("files") == ["src/a.js"], rec)
        r2 = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 3)
        q4.reap(fk.work, env[q4.TAG_VAR])
        check("[CP4-A1] mid-run, second stop on the same file: still a stop, with the hint that the command may be the writer",
              r2.ready and r2.rc == 2 and q4.says_changed(r2.text) and "if you did not edit" in r2.text
              and RESTORE_RE.search(r2.text) and ".gitignore" in r2.text, (r2.rc, r2.text[-600:]))
        g = q4.gate(d)
        check("[CP4-A1] mid-run stops never count: attempts 0, not failed, no behavior_failed entry",
              g.get("attempts") == 0 and g.get("status") != "failed" and not q4.history(d, "behavior_failed"), g)
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


def a1_helper_after_end():
    d, env, fk = behavior_project2(["exit:1", "helper-exit"], ["suite_watch_seconds=30"])
    try:
        r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2, delay=0.3, cap_after=60)
        check("[CP4-A1] an edit found only when the command ended is refused (the command ended on its own)",
              r.ready and r.rc == 2 and q4.says_changed(r.text) and says_both_causes(r.text), (r.rc, r.text[-500:]))
        dead = q4.wait_dead(fk.pids(2))
        left = q4.wait_untagged(env[q4.TAG_VAR])
        check("[CP4-A1] the helper the command left in its group is gone too, without the harness killing it",
              not dead and not left, (dead, left))
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


# ---- CP4-A2: a look git cannot take ------------------------------------------------------------------------------------

def a2_skipped_look():
    d, env, fk = behavior_project2(["exit:1", "unreadable"], ["suite_watch_seconds=0.3"])
    try:
        before = q4.gate(d)
        r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2, delay=3.5)
        check("[CP4-A2] the suite kept running through the looks git could not take (the edit landed 3.5 s in)",
              r.ready and fk.started(2), (r.ready, r.text[-400:]))
        check("[CP4-A2] the edit after the unreadable file went away still stopped the run within the bound",
              r.rc == 2 and q4.says_changed(r.text) and r.after is not None and r.after < q4.STOP_BOUND, (r.rc, r.after, r.text[-500:]))
        check("[CP4-A2] it did not end with a bare git error", "fingerprint failed" not in r.text, r.text[-500:])
        logs = q4.behavior_logs(d)
        text = q4.read(logs[-1]) if logs else ""
        check("[CP4-A2] the log notes that looks were skipped and why", re.search(r"look\(s\).*skipped", text)
              and re.search(r"secret\.txt|Permission denied", text), text[:600])
        left = q4.wait_untagged(env[q4.TAG_VAR])
        check("[CP4-A2] nothing of the run is alive and no lock is left", not left and q4.locks(d) == [], (left, q4.locks(d)))
        check("[CP4-A2] the gate is as before the call", q4.no_runs(q4.gate(d)) == q4.no_runs(before), q4.gate(d))
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


def a2_last_look_fails():
    d, env, fk = behavior_project2(["exit:1", "unreadable-exit"], ["suite_watch_seconds=30"])
    try:
        before = q4.gate(d)
        r = q4.rx(d, env, "behavior", "CP1", cap=60)
        check("[CP4-A2] a last look git cannot take ends the call non-zero, naming the git failure, never a pass",
              r.rc not in (None, 0) and re.search(r"git add|fingerprint", r.text) and q4.gate(d).get("status") != "passed",
              (r.rc, r.text[-500:]))
        left = q4.wait_untagged(env[q4.TAG_VAR])
        check("[CP4-A2] nothing of the run is alive, no lock is left, the gate is as before",
              not left and q4.locks(d) == [] and q4.no_runs(q4.gate(d)) == q4.no_runs(before), (left, q4.locks(d), q4.gate(d)))
    finally:
        try:
            (d / "secret.txt").chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        q4.reap(fk.work, env[q4.TAG_VAR])


def a2_exception_kills_group():
    m = _engine()
    d = Path(_tmp.mkdtemp(prefix="ratchet-q4f-x-"))
    tag = q4.make_env()[q4.TAG_VAR]
    ctx = types.SimpleNamespace(root=d, config=lambda: {})

    class Boom:
        every, again, skipped, found_at_end = 0.2, "x", [], False

        def changed(self, final=False):
            raise RuntimeError("the look blew up")

    saved = os.environ.get(q4.TAG_VAR)
    os.environ[q4.TAG_VAR] = tag
    os.environ["RATCHET_FAKE_LOADAVG"] = "0"
    raised = None
    try:
        m._run_cmd(ctx, f"{PY} -c 'import time\nfor _ in range(600): time.sleep(0.1)'", d / "x.log", 60, watch=Boom())
    except RuntimeError as e:
        raised = e
    finally:
        if saved is None:
            os.environ.pop(q4.TAG_VAR, None)
        else:
            os.environ[q4.TAG_VAR] = saved
        os.environ.pop("RATCHET_FAKE_LOADAVG", None)
    check("[CP4-A2] an exception from a look leaves _run_cmd as that exception", isinstance(raised, RuntimeError), raised)
    left = q4.wait_untagged(tag)
    check("[CP4-A2] and the command's group was killed before it left", not left, left)
    q4.reap(tag=tag)


def a2_skipped_per_command():
    d, env, fk = behavior_project2(["exit:1", "unreadable-fail", "sleep:1"], ["suite_watch_seconds=0.3"])
    try:
        r = q4.rx(d, env, "behavior", "CP1", cap=60)
        check("[CP4-A2] (setup) the first run failed on its own after its skipped looks and the retry passed",
              r.rc == 0 and q4.gate(d).get("status") == "passed" and q4.gate(d).get("flaky") is True, (r.rc, q4.gate(d)))
        logs = q4.behavior_logs(d)
        first = q4.read(logs[-2]) if len(logs) >= 2 else ""
        retry = q4.read(logs[-1]) if logs else ""
        check("[CP4-A2] the first run's log notes its skipped looks", re.search(r"look\(s\).*skipped", first), first[:400])
        check("[CP4-A2] the retry's log does not repeat them (the count is per command)",
              logs and logs[-1].name.endswith("b.log") and not re.search(r"look\(s\).*skipped", retry), (logs, retry[:400]))
    finally:
        try:
            (d / "secret.txt").chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        q4.reap(fk.work, env[q4.TAG_VAR])


# ---- CP4-A3: a failed diff-tree is never 'nothing changed' ----------------------------------------------------------------

def a3_difftree():
    m = _engine()
    d = Path(_tmp.mkdtemp(prefix="ratchet-q4f-d-"))
    tt.git(d, "init", "-q")
    (d / "a.txt").write_text("a\n")
    tt.git(d, "add", "-A")
    tt.git(d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    fp = m.fingerprint(d)
    bogus = {"kind": "git-tree", "id": "1" * 40}
    raised = None
    try:
        out = m._edited_or_deleted(d, bogus, fp)
    except Exception as e:  # noqa: BLE001
        raised, out = e, None
    check("[CP4-A3] _edited_or_deleted raises on a failed diff-tree between two git trees instead of returning []",
          raised is not None and type(raised).__name__ == "_LookFailed", (raised, out))
    ctx = types.SimpleNamespace(root=d, ratchet=d / ".ratchet")
    w = m._Watch(ctx, bogus, 30.0, "x")
    files = w.changed(final=True)
    check("[CP4-A3] the last look reports the change with a marker when the files cannot be listed, never nothing",
          files and files[0].startswith("(") and "could not be listed" in files[0], files)
    w2 = m._Watch(ctx, bogus, 30.0, "x")
    check("[CP4-A3] a periodic look that fails the same way is skipped and noted, never a pass claim",
          w2.changed() == [] and w2.skipped and "diff-tree" in w2.skipped[0], w2.skipped)


# ---- CP4-A4: the deleted-file, appeared-file and locked-key rules ----------------------------------------------------------

def a4_deleted():
    d, env, fk = behavior_project2(["exit:1", "slow"], ["suite_watch_seconds=0.3"])
    try:
        r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2, edit=lambda dd: (dd / "src/a.js").unlink())
        check("[CP4-A4] deleting a file the run started with stops the run, naming the file",
              r.ready and r.rc == 2 and q4.says_changed(r.text) and "src/a.js" in r.text
              and r.after is not None and r.after < q4.STOP_BOUND, (r.rc, r.after, r.text[-500:]))
        check("[CP4-A4] nothing counted", q4.gate(d).get("attempts") == 0 and q4.gate(d).get("status") != "failed", q4.gate(d))
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


def a4_appeared():
    d, env, fk = behavior_project2(["exit:1", "appear"], ["suite_watch_seconds=0.3"])
    r = q4.rx(d, env, "behavior", "CP1", cap=60)
    g = q4.gate(d)
    check("[CP4-A4] a generated file that only appeared (untracked, not ignored, not source) during the run is not a change: the run passes",
          r.rc == 0 and g.get("status") == "passed" and not q4.says_changed(r.text), (r.rc, g, r.text[-400:]))
    check("[CP4-A4] (setup) the file really is there, untracked and not ignored",
          (d / "tests/__snapshots__/x.snap").exists()
          and "tests/__snapshots__/x.snap" in tt.git(d, "status", "--porcelain", "--untracked-files=all").stdout,
          tt.git(d, "status", "--porcelain", "--untracked-files=all").stdout)


def a4_locked_key():
    d, env, fk = behavior_project2(["exit:1", "exit:0"], ["suite_watch_seconds=0.3"])
    r = tt.rx(d, env, "config", "--set", "suite_watch_seconds=5")
    calls = fk.calls()
    r2 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A4] a suite_watch_seconds change after plan approval is refused as a locked rule (approve-changes needed)",
          r2.rc != 0 and "suite_watch_seconds" in r2.text and "approve-changes" in r2.text, (r.returncode, r2.rc, r2.text[-500:]))
    check("[CP4-A4] and the tests were not run", fk.calls() == calls and q4.gate(d).get("status") != "passed",
          (fk.calls(), calls, q4.gate(d)))


# ---- CP4-A5: the test-runner agent ------------------------------------------------------------------------------------------

def a5_agent_doc():
    text = q4.read(ROOT / "agents/ratchet-test-runner.md")
    check("[CP4-A5] the test-runner agent names the stopped outcome (code changed, exit 2) as neither pass nor failure",
          re.search(r"STOPPED", text) and re.search(r"code changed", text, re.I) and re.search(r"exit 2", text)
          and re.search(r"not a failure|neither a pass nor a failure", text, re.I), text[-800:])
    check("[CP4-A5] it says to run behavior again and that no diagnosis is needed",
          re.search(r"again", text) and re.search(r"no diagnosis|needs no diagnosis", text, re.I), text[-800:])
    check("[CP4-A5] it covers the change found only when the command ended: both causes, the restore guidance, no diagnosis",
          re.search(r"found (only )?when it ended", text) and re.search(r"command itself", text)
          and re.search(r"edited while it ran", text) and re.search(r"restore", text, re.I), text[-1200:])
    check("[CP4-A5] every outcome is named: passed, failed, stopped, and the change found when the command ended",
          re.search(r"PASSED", text) and re.search(r"FAILED", text) and re.search(r"STOPPED", text)
          and re.search(r"CHANGED", text), text[-1200:])


# ---- CP4-C3: the grace covers the whole group ----------------------------------------------------------------------------------

def c3_group_grace():
    env = q4.make_env()
    fk = Fake2(["exit:1", "slow-cleanup"], "t")
    d = q4.project(env, f"{fk.cmd} && true", ["suite_watch_seconds=0.3"])  # a compound command: the shell stays the leader
    q4.to_implemented(d, env)
    try:
        r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2)
        check("[CP4-C3] the run under a compound command was stopped by the edit",
              r.ready and r.rc == 2 and q4.says_changed(r.text), (r.rc, r.text[-400:]))
        check("[CP4-C3] the runner got its grace to clean up after SIGTERM although the shell leader died at once",
              fk.cleaned(2), sorted(p.name for p in fk.work.iterdir()))
        left = q4.wait_untagged(env[q4.TAG_VAR])
        dead = q4.wait_dead(fk.pids(2))
        check("[CP4-C3] nothing of the run is alive afterwards", not left and not dead, (left, dead))
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


# ---- CP4-C4: a project below the repository top ---------------------------------------------------------------------------------

def subproject(env, cmd, config=()):
    """Like q4.project, but the git repository's top is one folder above the project (the transcript inside it)."""
    top = Path(_tmp.mkdtemp(prefix="ratchet-q4f-top-"))
    tt.git(top, "init", "-q")
    tt.git(top, "config", "user.email", "t@t")
    tt.git(top, "config", "user.name", "t")
    (top / "README.md").write_text("top\n")
    d = top / "app"
    (d / "src").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    tt.git(top, "add", "-A")
    tt.git(top, "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    q4.say(d, env, "please build the feature")
    r = tt.rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    r = tt.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", *config)
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": cmd}}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    r = tt.rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    tt.rx(d, env, "ask", "approve-plan")
    q4.say(d, env, "yes, go ahead")
    r = tt.rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def c4_subfolder():
    env = q4.make_env()
    fk = Fake2(["exit:1", "sleep:3", "slow"], "t")
    d = subproject(env, fk.cmd, ["suite_watch_seconds=0.3"])
    q4.to_implemented(d, env)
    check("[CP4-C4] (setup) the project sits below the repository top and its transcript is inside it",
          tt.git(d, "rev-parse", "--show-prefix").stdout.strip() == "app/" and (d / "t.jsonl").exists(),
          tt.git(d, "rev-parse", "--show-prefix").stdout)
    try:
        r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2, wait="started", edit=lambda dd: q4.say(dd, env, "how is it going?"),
                             delay=0.3, cap_after=60)
        check("[CP4-C4] a chat line in the project's transcript does not stop the run of a subfolder project",
              r.ready and r.rc == 0 and q4.gate(d).get("status") == "passed" and not q4.says_changed(r.text),
              (r.rc, q4.gate(d), r.text[-400:]))
        r2 = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 3)
        check("[CP4-C4] an edit still stops it, naming the file relative to the project",
              r2.ready and r2.rc == 2 and q4.says_changed(r2.text) and re.search(r"\(src/a\.js", r2.text), (r2.rc, r2.text[-400:]))
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


# ---- CP4-C5: periodic looks reuse one kept index ------------------------------------------------------------------------------------

def c5_kept_index():
    m = _engine()
    d = Path(_tmp.mkdtemp(prefix="ratchet-q4f-i-"))
    tt.git(d, "init", "-q")
    (d / "a.txt").write_text("a\n")
    (d / "b.txt").write_text("b\n")
    tt.git(d, "add", "-A")
    tt.git(d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    ctx = types.SimpleNamespace(root=d, ratchet=d / ".ratchet")
    w = m._Watch(ctx, m.fingerprint(d), 30.0, "x")
    seeds = []          # the index file of every `git read-tree` run meanwhile: the kept index is seeded exactly once
    real_run = m.subprocess.run

    def counting_run(args, *a, **kw):
        if isinstance(args, (list, tuple)) and list(args[:2]) == ["git", "read-tree"]:
            seeds.append((kw.get("env") or {}).get("GIT_INDEX_FILE", ""))
        return real_run(args, *a, **kw)

    m.subprocess.run = counting_run
    try:
        check("[CP4-C5] a periodic look gives the full fingerprint's tree id", w._look_fp()["id"] == m.fingerprint(d)["id"]
              and w.changed() == [], w.skipped)
        kept = w._index
        (d / "b.txt").write_text("changed\n")
        found = w.changed()
        check("[CP4-C5] an edit is found through the kept index", found == ["b.txt"], (found, w.skipped))
        check("[CP4-C5] a look through the kept index still equals a fresh fingerprint after the edit",
              w._look_fp()["id"] == m.fingerprint(d)["id"])
        (d / "c.txt").write_text("new\n")
        check("[CP4-C5] a new non-source file that only appeared is still not a change through the kept index",
              w.changed() == ["b.txt"], w.changed())
        check("[CP4-C5] the index was kept across all of those looks: one folder, seeded by one read-tree, never rebuilt",
              w._index is kept and (Path(kept.name) / "index").exists()
              and sum(1 for f in seeds if f.startswith(kept.name)) == 1, seeds)
    finally:
        m.subprocess.run = real_run


# ---- CP4-A6: a new source file stops the run; generated output never does ---------------------------------------------------

def add_source(dd):
    (dd / "src/b.js").write_text("export const b = 2;\n")  # the "agent edit": a brand-new project source file


def a6_new_source_behavior():
    d, env, fk = behavior_project2(["exit:1", "slow", "exit:0"], ["suite_watch_seconds=0.3"])
    try:
        before = q4.gate(d)
        st0 = q4.state(d)
        r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2, edit=add_source)
        check("[CP4-A6] (setup) src/b.js was not in the tree the run started on",
              "src/b.js" not in tt.git(d, "ls-tree", "-r", "--name-only", "HEAD").stdout)
        check("[CP4-A6] a new source file added while the tests run stops them within the watch interval, naming it",
              r.ready and r.rc == 2 and q4.says_stopped_for_change(r.text) and "src/b.js" in r.text
              and r.after is not None and r.after < q4.STOP_BOUND, (r.rc, r.after, r.text[-500:]))
        check("[CP4-A6] it says to run `behavior CP1` again", "behavior CP1" in r.text and q4.says_again(r.text), r.text[-500:])
        g = q4.gate(d)
        check("[CP4-A6] nothing counted: the gate (status, attempts, flaky allowance) is exactly as before the call",
              q4.no_runs(g) == q4.no_runs(before) and g.get("attempts") == 0 and not g.get("flaky_used")
              and not q4.history(d, "behavior_failed"), (before, g))
        check("[CP4-A6] the step and phase are unchanged", q4.state(d).get("phase") == st0.get("phase"),
              (st0.get("phase"), q4.state(d).get("phase")))
        check("[CP4-A6] no lock is left and nothing of the stopped tests is running",
              q4.locks(d) == [] and not q4.wait_untagged(env[q4.TAG_VAR]) and not q4.wait_dead(fk.pids(2)),
              (q4.locks(d), q4.tagged(env[q4.TAG_VAR])))
        logs = q4.behavior_logs(d)
        text = q4.read(logs[-1]) if logs else ""
        check("[CP4-A6] the log says the run was stopped because the code changed, naming the new file",
              q4.says_stopped_for_change(text) and "src/b.js" in text, text[:400])
        check("[CP4-A6] the new file is left where the agent put it", q4.read(d / "src/b.js") == "export const b = 2;\n")
        r2 = q4.rx(d, env, "behavior", "CP1", cap=60)
        check("[CP4-A6] the next run starts at once and passes on its first counted attempt, with the new file in place",
              r2.rc == 0 and q4.gate(d).get("status") == "passed" and q4.gate(d).get("attempts") == 1,
              (r2.rc, q4.gate(d), r2.text[-300:]))
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


def a6_new_source_complete():
    d, env, fk_r, _ = q4.complete_project(["slow"], ["suite_watch_seconds=0.3"])
    try:
        h = q4.head(d)
        r = q4.run_then_edit(d, env, ["complete", "CP1"], fk_r, 1, edit=add_source)
        check("[CP4-A6] complete: a new source file added while the full suite runs stops it within the watch interval",
              r.ready and r.rc not in (None, 0) and q4.says_changed(r.text) and "src/b.js" in r.text
              and r.after is not None and r.after < q4.STOP_BOUND, (r.rc, r.after, r.text[-500:]))
        st = q4.state(d)
        check("[CP4-A6] complete: nothing committed, the step not done, no flaky allowance spent, no lock left",
              q4.head(d) == h and q4.cps(d).get("status") != "done" and not st.get("regression_flaky_used")
              and not q4.cps(d).get("regression_flaky") and q4.locks(d) == [],
              (q4.head(d), h, q4.cps(d).get("status"), st.get("regression_flaky_used"), q4.locks(d)))
        check("[CP4-A6] complete: the gates are left as they were (behavior and adversarial still passed)",
              q4.gate(d).get("status") == "passed" and q4.gate(d, "adversarial").get("status") == "passed", q4.cps(d)["gates"])
    finally:
        q4.reap(fk_r.work, env[q4.TAG_VAR])


def a6_generated_output():
    d, env, fk = behavior_project2(["exit:1", "gen-output"], ["suite_watch_seconds=0.3"])
    r = q4.rx(d, env, "behavior", "CP1", cap=60)
    g = q4.gate(d)
    check("[CP4-A6] generated build, cache, report, snapshot and scratch output written all through the run never stops it: it passes",
          r.rc == 0 and g.get("status") == "passed" and g.get("attempts") == 1 and not q4.says_changed(r.text),
          (r.rc, g, r.text[-400:]))
    status = tt.git(d, "status", "--porcelain", "--untracked-files=all").stdout
    check("[CP4-A6] (setup) the generated files really are there, untracked and not ignored, in source-looking places too",
          all(x in status for x in ("dist/app.js", "tmp/scratch.py", "src/__snapshots__/b.js", "src/app.min.js",
                                    "reports/junit.xml", "scratch/note.js")), status)
    logs = q4.behavior_logs(d)
    text = q4.read(logs[-1]) if logs else ""
    # A look git cannot take while the suite rewrites its own output (reports/junit.xml mid-write) is skipped and
    # noted, which the watch allows; any other header line would be a change, a stop or a failure.
    head = [l for l in text.split("\n\n", 1)[0].splitlines() if l.startswith("#")]
    extra = [l for l in head if not l.startswith("# exit=")
             and not re.match(r"# \d+ look\(s\) at the code could not be taken and were skipped \(last reason: ", l)]
    check("[CP4-A6] the log is an ordinary pass with no stop (a skipped look, if any, only in its documented note)",
          "# exit=0" in text and not extra and not q4.says_stopped_for_change(text) and "found when it ended" not in text,
          (extra, text[:400]))
    check("[CP4-A6] no lock is left and nothing of the run is still running",
          q4.locks(d) == [] and not q4.wait_untagged(env[q4.TAG_VAR]), (q4.locks(d), q4.tagged(env[q4.TAG_VAR])))


def a6_suite_writes_source():
    d, env, fk = behavior_project2(["exit:1", "write-source", "write-source", "sleep:1"], ["suite_watch_seconds=30"])
    before = q4.gate(d)
    r1 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A6] a quick suite that itself adds a new source file is refused when it ends, naming the file, both causes",
          r1.rc == 2 and q4.says_changed(r1.text) and "src/made_by_suite.js" in r1.text and says_both_causes(r1.text),
          (r1.rc, r1.text[-600:]))
    check("[CP4-A6] the guidance for a new file: it did not exist when the run began; keep it if you added it, else remove or ignore it",
          "did not exist when the run began" in r1.text and "keep them" in r1.text
          and "rm src/made_by_suite.js" in r1.text and ".gitignore" in r1.text
          and not re.search(r"git checkout [0-9a-f]{40} -- src/made_by_suite\.js", r1.text), r1.text[-600:])
    check("[CP4-A6] nothing counted and no lock left",
          q4.no_runs(q4.gate(d)) == q4.no_runs(before) and q4.locks(d) == [], (q4.gate(d), q4.locks(d)))
    (d / "src/made_by_suite.js").unlink()  # the agent follows the guidance: the command wrote it
    r2 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A6] the same refusal while the command keeps writing it, still uncounted",
          r2.rc == 2 and "src/made_by_suite.js" in r2.text and q4.gate(d).get("attempts") == 0, (r2.rc, r2.text[-400:]))
    (d / "src/made_by_suite.js").unlink()
    r3 = q4.rx(d, env, "behavior", "CP1", cap=60)
    check("[CP4-A6] once the command stops writing source, the next run passes on its first counted attempt",
          r3.rc == 0 and q4.gate(d).get("status") == "passed" and q4.gate(d).get("attempts") == 1, (r3.rc, r3.text[-300:]))


def a6_engine_rule():
    m = _engine()
    src = ["src/b.js", "lib/x.py", "app/models/user.rb", "cmd/main.go", "tests/test_new.py", "styles/site.scss", "b.ts",
           "src/bin/main.rs", "src/bin/tool/main.rs", "bin/cli.js", "tools/bin/run.sh", "src/bin/release.rs",
           "templates/index.html", "public/page.htm", "static/site.css", "src/App.vue", "src/App.svelte",
           "src/App.jsx", "src/App.tsx", "reports/summary.py", "app/reports/views.py", "report/model.rb",
           "config/app.yaml", "package.json", "pom.xml", "Makefile", "Dockerfile", "src/schema.graphql"]
    gen = ["build/x.js", "dist/x.js", "out/x.py", "coverage/x.js", "htmlcov/x.py", ".cache/x.py", "node_modules/p/i.js",
           "__pycache__/m.py", "src/__snapshots__/a.js", "tests/__snapshots__/a.snap", "reports/junit.xml",
           "test-results/r.json", "tmp/s.py", "scratch/s.js", "logs/run.log", "src/app.min.js", "src/app.js.map",
           "results.xml", "notes.txt", "src/.#a.js", "src/a.js~", "target/x.rs", "bin/Debug/net8.0/Gen.cs",
           "App/bin/Release/net8.0/x.js", "obj/Debug/net8.0/App.AssemblyInfo.cs", "target/debug/build/out.rs",
           "coverage.xml", ".coverage", "lcov.info.lcov", "test-results.xml", "README.md", "docs/guide.rst",
           "vendor/lib/x.js", "src/a.js.orig", "src/a.py.bak", "dist/app.min.js", "build/out.js"]
    check("[CP4-A6] the engine's rule: project source files count as new source",
          all(m._new_source(f) for f in src), [f for f in src if not m._new_source(f)])
    check("[CP4-A6] the engine's rule: generated build/cache/report/snapshot/scratch files do not",
          not any(m._new_source(f) for f in gen), [f for f in gen if m._new_source(f)])


def _add_files(spec: dict):
    def edit(dd):
        for rel, body in spec.items():
            (dd / rel).parent.mkdir(parents=True, exist_ok=True)
            (dd / rel).write_text(body)
    return edit


def _stops_on_new_file(label: str, rel: str, body: str):
    d, env, fk = behavior_project2(["exit:1", "slow", "exit:0"], ["suite_watch_seconds=0.3"])
    try:
        before = q4.gate(d)
        r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2, edit=_add_files({rel: body}))
        check(f"[CP4-A7] a new {label} ({rel}) added while the suite runs stops it within the watch interval, naming it",
              r.ready and r.rc == 2 and q4.says_stopped_for_change(r.text) and rel in r.text
              and r.after is not None and r.after < q4.STOP_BOUND, (r.rc, r.after, r.text[-500:]))
        check(f"[CP4-A7] {rel}: nothing counted, no lock left, nothing of the suite still running",
              q4.no_runs(q4.gate(d)) == q4.no_runs(before) and q4.locks(d) == []
              and not q4.wait_untagged(env[q4.TAG_VAR]) and not q4.wait_dead(fk.pids(2)), (q4.gate(d), q4.locks(d)))
    finally:
        q4.reap(fk.work, env[q4.TAG_VAR])


def a7_html_template_stops():
    _stops_on_new_file("HTML template", "templates/x.html", "<h1>hello</h1>\n")


def a7_reports_module_stops():
    _stops_on_new_file("module under an application reports/ folder", "reports/summary.py", "def summary():\n    return 1\n")


def a7_build_output_does_not_stop():
    d, env, fk = behavior_project2(["exit:1", "sleep:3"], ["suite_watch_seconds=0.3"])
    r = q4.run_then_edit(d, env, ["behavior", "CP1"], fk, 2, wait="started", delay=0.3, cap_after=60,
                         edit=_add_files({"build/out.js": "// built\n", "dist/app.min.js": "// minified\n"}))
    g = q4.gate(d)
    check("[CP4-A7] build/out.js and dist/app.min.js appearing during the run are generated output: the run passes",
          r.ready and r.rc == 0 and g.get("status") == "passed" and not q4.says_changed(r.text), (r.rc, g, r.text[-400:]))
    check("[CP4-A7] (setup) they are really there, untracked and not ignored",
          all(x in tt.git(d, "status", "--porcelain", "--untracked-files=all").stdout for x in ("build/out.js", "dist/app.min.js")),
          tt.git(d, "status", "--porcelain", "--untracked-files=all").stdout)


def a6_rust_bin_watch():
    """Regression (local review): src/bin/ is a standard Rust source folder, never generated output, so a new file
    there is a change to the running watch; .NET's bin/Debug output beside it still is not."""
    m = _engine()
    d = Path(_tmp.mkdtemp(prefix="ratchet-q4f-rs-"))
    tt.git(d, "init", "-q")
    (d / "src").mkdir()
    (d / "src/lib.rs").write_text("pub fn a() {}\n")
    tt.git(d, "add", "-A")
    tt.git(d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    ctx = types.SimpleNamespace(root=d, ratchet=d / ".ratchet")
    w = m._Watch(ctx, m.fingerprint(d), 30.0, "x")
    (d / "bin/Debug/net8.0").mkdir(parents=True)
    (d / "bin/Debug/net8.0/Gen.cs").write_text("// generated\n")
    check("[CP4-A6] .NET build output (bin/Debug/...) that appears is not a change", w.changed() == [], (w.changed(), w.skipped))
    (d / "src/bin").mkdir()
    (d / "src/bin/main.rs").write_text("fn main() {}\n")
    found = w.changed()
    check("[CP4-A6] a new Rust binary source file (src/bin/main.rs) is a change to the running watch, named alone",
          found == ["src/bin/main.rs"] and w.added == ["src/bin/main.rs"], (found, w.skipped))
    check("[CP4-A6] the last look agrees", w.changed(final=True) == ["src/bin/main.rs"])


TESTS = [
    ("CP4-A1", a1_behavior), ("CP4-A1", a1_keeps_own_work), ("CP4-A1", a1_untracked), ("CP4-A1", a1_complete), ("CP4-A1", a1_midrun_hint),
    ("CP4-A1", a1_helper_after_end),
    ("CP4-A2", a2_skipped_look), ("CP4-A2", a2_last_look_fails), ("CP4-A2", a2_exception_kills_group),
    ("CP4-A2", a2_skipped_per_command),
    ("CP4-A3", a3_difftree),
    ("CP4-A4", a4_deleted), ("CP4-A4", a4_appeared), ("CP4-A4", a4_locked_key),
    ("CP4-A5", a5_agent_doc),
    ("CP4-C3", c3_group_grace),
    ("CP4-C4", c4_subfolder),
    ("CP4-C5", c5_kept_index),
    ("CP4-A6", a6_new_source_behavior), ("CP4-A6", a6_new_source_complete), ("CP4-A6", a6_generated_output),
    ("CP4-A6", a6_suite_writes_source), ("CP4-A6", a6_engine_rule), ("CP4-A6", a6_rust_bin_watch),
    ("CP4-A7", a7_html_template_stops), ("CP4-A7", a7_reports_module_stops), ("CP4-A7", a7_build_output_does_not_stop),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid in dict.fromkeys(c for c, _ in TESTS):
        if cid in CASE_ERRORS:
            cases.append({"id": cid, "status": "error", "message": CASE_ERRORS[cid][:300]})
        elif CASE_STATUS.get(cid, False):
            cases.append({"id": cid, "status": "passed", "message": ""})
        else:
            cases.append({"id": cid, "status": "failed", "message": "; ".join(CASE_MESSAGES.get(cid, []))[:300]})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    only = set(sys.argv[1:])
    for cid, fn in TESTS:
        if only and cid not in only and fn.__name__ not in only:
            continue
        CASE_STATUS.setdefault(cid, True)
        print(f"{cid} {fn.__name__}", flush=True)
        t0 = time.monotonic()
        try:
            fn()
        except Exception as e:  # a crash is reported as an error for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_ERRORS[cid] = f"crashed: {e}"
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        print(f"  ({time.monotonic() - t0:.1f}s)", flush=True)
    q4._reap_all()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
