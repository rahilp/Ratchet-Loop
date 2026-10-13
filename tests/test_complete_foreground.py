#!/usr/bin/env python3
"""
CP3 tests: a foreground `complete` warns first that a tool timeout could cut it off.   python3 tests/test_complete_foreground.py

An agent that runs `ratchet.py complete CP1` as an ordinary foreground call can have it cut off by the host's tool
timeout (complete runs the whole regression suite). The guard hook notes each `complete` call with the tool input's
run_in_background and timeout (the way it already notes init / ask / wait), and `complete` then prints a plain warning
on its error stream, flushed, before the regression suite starts. The warning never changes what complete does.

The host is played the way a real one behaves: first the guard hook gets the Bash tool call a real agent would make
(`hook guard --host claude`, with a session id and tool_input {command, run_in_background, timeout}), then the exact
same `complete CP1` arguments run. Fixtures reuse tests/test_signing.py's project2()/pass_behavior2()/open_round()/
record_host_review() and tests/test_flaky.py's set_regression_command_confirmed()/flaky_cmd_from_outcomes(). No real
agent CLI, no network.

Matching rule used throughout: output is matched line by line after dropping every token that holds a "/" (a path),
because the full suite runs each suite in a TMPDIR named after the suite, and a path could hold words like "complete".

Writes a per-test JSON report to RATCHET_TEST_REPORT when set:
  {"cases": [{"id": "CP3-T01", "status": "passed"|"failed", "message": "..."}]}
"""
from __future__ import annotations

import json
import os
import re
import select
import shlex
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import test_signing as ts  # noqa: E402  (project2, pass_behavior2, open_round, record_host_review, guard, rx)
import test_flaky as tf  # noqa: E402  (set_regression_command_confirmed, flaky_cmd_from_outcomes)
import trust_helpers as tt  # noqa: E402  (ENGINE)

ENGINE = tt.ENGINE
README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP3-[TX]\d+)\]")


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


# ---- reading output -------------------------------------------------------------------------------------------

CUT_RE = re.compile(r"time-?\s?out|timed out|cut off|cut-off", re.I)
BG_RE = re.compile(r"background", re.I)
DUR_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:min(?:ute)?s?|sec(?:ond)?s?|hours?|[mhs])\b", re.I)


def plain_lines(text):
    """Non-empty lines of `text` with every token that holds a "/" removed (paths never take part in a match)."""
    out = []
    for ln in (text or "").splitlines():
        s = " ".join(t for t in ln.split() if "/" not in t)
        if s.strip():
            out.append(s)
    return out


def warns(text):
    """True when `text` (outside paths) says a timeout could cut it off AND mentions the background."""
    body = "\n".join(plain_lines(text))
    return bool(CUT_RE.search(body)) and bool(BG_RE.search(body))


def mentions_either(text):
    body = "\n".join(plain_lines(text))
    return bool(CUT_RE.search(body)) or bool(BG_RE.search(body))


def cut_lines(text):
    return [ln for ln in plain_lines(text) if CUT_RE.search(ln)]


def norm(text, d):
    """Output with this project's folder and any commit id made comparable between two projects."""
    t = (text or "").replace(str(d), "<D>")
    return re.sub(r"\b[0-9a-f]{7,40}\b", "<H>", t)


# ---- the host: the guard hook sees the Bash call, then the call runs ------------------------------------------

def complete_cmd():
    return f"python3 {shlex.quote(str(ENGINE))} complete CP1"


def guard_bash(d, env, command, session="s-builder", **extra):
    """The Bash tool call a host sends the guard hook before running `command`. extra = run_in_background /
    timeout. Returns the hook's CompletedProcess."""
    ti = {"command": command}
    ti.update(extra)
    payload = {"cwd": str(d), "session_id": session, "tool_name": "Bash", "tool_input": ti}
    return ts.rx(d, env, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))


def note_complete(d, env, **extra):
    g = guard_bash(d, env, complete_cmd(), **extra)
    assert g.returncode == 0, (g.returncode, g.stdout, g.stderr)
    return g


# ---- fixtures -------------------------------------------------------------------------------------------------

_SCRIPTS = Path(_tmp.mkdtemp(prefix="ratchet-cp3-scripts-"))
_COUNT_SRC = ("import sys\n"
              "from pathlib import Path\n"
              "with open(sys.argv[1], 'a') as f:\n"
              "    f.write('x\\n')\n")
_COUNT = _SCRIPTS / "count_ok.py"
_COUNT.write_text(_COUNT_SRC)
_HOLD_SRC = ("import sys, time\n"
             "from pathlib import Path\n"
             "started, release = Path(sys.argv[1]), Path(sys.argv[2])\n"
             "started.write_text('1')\n"
             "end = time.time() + 20\n"
             "while time.time() < end and not release.exists():\n"
             "    time.sleep(0.05)\n"
             "sys.exit(0)\n")
_HOLD = _SCRIPTS / "hold.py"
_HOLD.write_text(_HOLD_SRC)


def counting_cmd():
    """A regression command that exits 0 at once and appends one line to its own counter file per run."""
    ctr = Path(_tmp.mkdtemp(prefix="ratchet-cp3-ctr-")) / "runs"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(_COUNT))} {shlex.quote(str(ctr))}", ctr


def runs_of(ctr):
    return len(Path(ctr).read_text().splitlines()) if Path(ctr).exists() else 0


def build_finished():
    """A one-step project with every gate passed, so the only thing left is `complete CP1` (no regression
    command yet). Independent of every other project, so several can be built at the same time."""
    env = ts.make_env("claude")
    d = ts.project2(env)
    ts.pass_behavior2(d, env, "CP1", 1)
    rnd = ts.open_round(d, env, "CP1")
    _, r0 = ts.record_host_review(d, env, rnd, findings=[], cp="CP1", files_reviewed=["src/f1.js"])
    assert r0.returncode == 0, r0.stderr + r0.stdout
    return d, env


_POOL = []  # futures of projects being built ahead of time (see main)


def prebuild(n):
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(max_workers=4)
    for _ in range(n):
        _POOL.append(ex.submit(build_finished))
    ex.shutdown(wait=False)


def finished_project(regression=None):
    """A finished project (see build_finished) with `regression` as its regression command (default: one that
    exits 0 at once, with a counter). Returns (d, env, counter_path)."""
    d, env = _POOL.pop(0).result() if _POOL else build_finished()
    ctr = None
    if regression is None:
        regression, ctr = counting_cmd()
    tf.set_regression_command_confirmed(d, env, regression)
    return d, env, ctr


def state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def cp1_done(d, env):
    r = ts.rx(d, env, "status", "--json")
    try:
        sj = json.loads(r.stdout)
    except Exception:
        return False
    cps = sj.get("checkpoints") or []
    row = next((c for c in cps if isinstance(c, dict) and c.get("id") == "CP1"), {})
    return row.get("status") == "done" and state(d)["checkpoints"]["CP1"].get("status") == "done"


def commit_of(d):
    c = state(d)["checkpoints"]["CP1"].get("commit")
    if not c:
        return None
    msg = ts.git(d, "log", "-1", "--format=%B", c).stdout
    files = sorted(ts.git(d, "show", "--name-only", "--format=", c).stdout.split())
    return msg, files


_REF = {}


def foreground_reference():
    """One foreground complete, run once and kept: what the warning looks like in a plain run."""
    if "r" not in _REF:
        d, env, ctr = finished_project()
        note_complete(d, env, run_in_background=False, timeout=120000)
        _REF["r"] = (d, env, ctr, ts.rx(d, env, "complete", "CP1"))
    return _REF["r"]


# ---- CP3-T01 .. T08 ------------------------------------------------------------------------------------------

def t01_foreground_warns_first():
    print("CP3-T01: a foreground complete warns first that a tool timeout could cut it off")
    d, env, ctr, r = foreground_reference()
    err_lines = plain_lines(r.stderr)
    check("[CP3-T01] complete itself still succeeds", r.returncode == 0, (r.returncode, r.stdout[-300:], r.stderr[-300:]))
    check("[CP3-T01] the first thing printed on stderr says a tool timeout could cut complete off",
          bool(err_lines) and bool(CUT_RE.search(err_lines[0])), err_lines[:3])
    check("[CP3-T01] the warning says to run complete in the background instead",
          bool(err_lines) and warns(r.stderr) and "complete" in " ".join(err_lines).lower(), err_lines[:4])
    head = " ".join(err_lines[:4])
    check("[CP3-T01] the warning gives a how-long figure with a time unit", bool(DUR_RE.search(head)), head)
    check("[CP3-T01] stdout has no trace of the warning", not warns(r.stdout) and not cut_lines(r.stdout),
          plain_lines(r.stdout)[:6])


def t02_warning_before_regression():
    print("CP3-T02: the warning appears before the regression suite starts")
    hold_dir = Path(_tmp.mkdtemp(prefix="ratchet-cp3-hold-"))
    started, release = hold_dir / "started", hold_dir / "release"
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(_HOLD))} {shlex.quote(str(started))} {shlex.quote(str(release))}"
    d, env, _ = finished_project(regression=cmd)
    note_complete(d, env, run_in_background=False, timeout=120000)
    proc = subprocess.Popen([sys.executable, str(ENGINE), "complete", "CP1"], cwd=d, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    first = b""
    still_running = False
    ready = False
    try:
        end = time.time() + 40
        while time.time() < end and not started.exists() and proc.poll() is None:
            time.sleep(0.05)
        ready = started.exists()  # the regression command is running now: anything complete prints first is out
        if ready:
            rl, _, _ = select.select([proc.stderr], [], [], 8)
            if rl:
                first = os.read(proc.stderr.fileno(), 65536)
            still_running = proc.poll() is None and not release.exists()
        release.write_text("go")
        try:
            out, err_rest = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err_rest = proc.communicate()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        release.write_text("go")
    text = first.decode("utf-8", "replace")
    check("[CP3-T02] the regression command really started (precondition)", ready, "regression command never started")
    check("[CP3-T02] the warning was already readable on stderr while the regression suite was still blocked",
          ready and still_running and warns(text) and bool(CUT_RE.search(plain_lines(text)[0] if plain_lines(text) else "")),
          (still_running, text[:300]))
    check("[CP3-T02] complete then finishes with exit status 0 once the release file exists",
          proc.returncode == 0, (proc.returncode, err_rest[-300:] if err_rest else b""))
    check("[CP3-T02] the step is done", cp1_done(d, env))


def t03_warning_changes_nothing():
    print("CP3-T03: the warning never stops or changes complete")
    results = []
    for fg in (True, False):
        d, env, ctr = finished_project()
        note_complete(d, env, run_in_background=not fg, timeout=120000)
        r = ts.rx(d, env, "complete", "CP1")
        results.append((fg, d, env, ctr, r))
    (_, d1, e1, c1, r1), (_, d2, e2, c2, r2) = results
    check("[CP3-T03] both runs exit 0", r1.returncode == 0 and r2.returncode == 0,
          (r1.returncode, r2.returncode, r1.stderr[-200:], r2.stderr[-200:]))
    check("[CP3-T03] both mark CP1 done in status --json", cp1_done(d1, e1) and cp1_done(d2, e2))
    k1, k2 = commit_of(d1), commit_of(d2)
    check("[CP3-T03] both make a step commit with the same message and the same files",
          k1 is not None and k1 == k2, (k1, k2))
    check("[CP3-T03] the regression command ran exactly once in each", runs_of(c1) == 1 and runs_of(c2) == 1,
          (runs_of(c1), runs_of(c2)))
    check("[CP3-T03] the warned run carries the warning on stderr, the unwarned run carries none",
          warns(r1.stderr) and not mentions_either(r2.stderr), (plain_lines(r1.stderr)[:3], plain_lines(r2.stderr)[:3]))
    check("[CP3-T03] stdout is the same in both runs (the warning is the only difference)",
          norm(r1.stdout, d1) == norm(r2.stdout, d2), (norm(r1.stdout, d1)[-200:], norm(r2.stdout, d2)[-200:]))


def t04_background_no_warning():
    print("CP3-T04: no warning when the host says the call runs in the background")
    d, env, ctr = finished_project()
    note_complete(d, env, run_in_background=True, timeout=120000)
    r = ts.rx(d, env, "complete", "CP1")
    check("[CP3-T04] the step is completed", r.returncode == 0 and cp1_done(d, env), (r.returncode, r.stderr[-300:]))
    check("[CP3-T04] nothing about timeouts or the background on stderr", not mentions_either(r.stderr),
          plain_lines(r.stderr)[:4])
    check("[CP3-T04] nothing about timeouts or the background on stdout", not mentions_either(r.stdout),
          [ln for ln in plain_lines(r.stdout) if mentions_either(ln)][:4])


def t05_no_signal_still_warns():
    print("CP3-T05: no guard hook ran for this call, so the warning is still printed")
    d, env, ctr = finished_project()  # no guard call at all: nothing noted
    r = ts.rx(d, env, "complete", "CP1")
    err_lines = plain_lines(r.stderr)
    check("[CP3-T05] the same warning is printed first",
          bool(err_lines) and bool(CUT_RE.search(err_lines[0])) and warns(r.stderr), err_lines[:3])
    check("[CP3-T05] complete still finishes with exit status 0", r.returncode == 0 and cp1_done(d, env),
          (r.returncode, r.stderr[-300:]))


def t06_unknown_is_foreground():
    print("CP3-T06: a host that does not say either way is treated as foreground")
    for label, extra in (("a command only", {}), ("a timeout but no background flag", {"timeout": 600000})):
        d, env, ctr = finished_project()
        note_complete(d, env, **extra)
        r = ts.rx(d, env, "complete", "CP1")
        check(f"[CP3-T06] the warning is printed for {label}", warns(r.stderr), plain_lines(r.stderr)[:3])
        check(f"[CP3-T06] complete still finishes for {label}", r.returncode == 0 and cp1_done(d, env),
              (r.returncode, r.stderr[-300:]))


def t07_rerun_does_not_repeat():
    print("CP3-T07: a regression re-run does not repeat the warning")
    _, _, _, ref = foreground_reference()
    ref_count = len(cut_lines(ref.stderr + "\n" + ref.stdout))
    cmd, _ = tf.flaky_cmd_from_outcomes([1, 0])
    d, env, _ = finished_project(regression=cmd)
    note_complete(d, env, run_in_background=False, timeout=120000)
    r = ts.rx(d, env, "complete", "CP1")
    count = len(cut_lines(r.stderr + "\n" + r.stdout))
    check("[CP3-T07] complete succeeds after the automatic re-run", r.returncode == 0 and cp1_done(d, env),
          (r.returncode, r.stderr[-300:]))
    check("[CP3-T07] the regression result is marked flaky as before",
          state(d)["checkpoints"]["CP1"].get("regression_flaky") is True, state(d)["checkpoints"]["CP1"])
    check("[CP3-T07] the warning appears exactly as often as in a run with no re-run, and at least once",
          count >= 1 and count == ref_count, (count, ref_count))


def t08_refused_and_other_commands():
    print("CP3-T08: a refused complete keeps its message and status; other commands print no warning")
    outs = []
    for noted in (True, False):
        env = ts.make_env("claude")
        d = ts.project2(env)
        if noted:
            note_complete(d, env, run_in_background=False, timeout=120000)
        r = ts.rx(d, env, "complete", "CP1")
        outs.append((d, env, r))
    (d1, e1, r1), (d2, e2, r2) = outs
    check("[CP3-T08] complete is refused with a non-zero status", r1.returncode != 0 and r2.returncode != 0,
          (r1.returncode, r2.returncode))
    check("[CP3-T08] the same status as without any note", r1.returncode == r2.returncode, (r1.returncode, r2.returncode))
    check("[CP3-T08] no traceback", "Traceback" not in r1.stderr + r1.stdout, r1.stderr[-300:])

    def reason(r, d):  # the refusal text, one entry per non-empty line
        return [ln for ln in norm(r.stderr + r.stdout, d).splitlines() if ln.strip()]
    check("[CP3-T08] the same plain reason as without any note: every line it had is still there, unchanged",
          bool(reason(r2, d2)) and all(ln in reason(r1, d1) for ln in reason(r2, d2)),
          (reason(r1, d1)[:4], reason(r2, d2)[:4]))
    for args in (("status",), ("status", "--json"), ("next",)):
        r = ts.rx(d1, e1, *args)
        check(f"[CP3-T08] `{' '.join(args)}` never mentions a timeout cut-off or running in the background",
              r.returncode == 0 and not mentions_either(r.stdout + "\n" + r.stderr),
              (r.returncode, plain_lines(r.stdout + r.stderr)[:3]))
        if args == ("status", "--json"):
            try:
                ok = isinstance(json.loads(r.stdout), dict)
            except Exception:
                ok = False
            check("[CP3-T08] `status --json` is still valid JSON", ok, r.stdout[:200])


# ---- CP3-T09: the guard ---------------------------------------------------------------------------------------

def t09_guard_lets_complete_through():
    print("CP3-T09: the guard still lets a foreground complete through")
    env = ts.make_env("claude")
    d = ts.project2(env)
    g = guard_bash(d, env, complete_cmd(), run_in_background=False, timeout=120000)
    check("[CP3-T09] the guard exits 0 for a foreground complete in a project with an active run", g.returncode == 0,
          (g.returncode, g.stdout[-200:], g.stderr[-200:]))
    check("[CP3-T09] the guard prints no refusal and no warning itself",
          not warns(g.stdout + "\n" + g.stderr) and not mentions_either(g.stdout + "\n" + g.stderr),
          (g.stdout[:200], g.stderr[:200]))
    for label, make in (("damaged", lambda p: p.write_text("{ this is not json")),
                        ("unwritable", lambda p: p.mkdir())):
        d2, env2, ctr = finished_project()
        p = Path(env2["RATCHET_HOME"]) / "engine-call-sessions.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
        make(p)
        g2 = guard_bash(d2, env2, complete_cmd(), run_in_background=False, timeout=120000)
        check(f"[CP3-T09] the guard still lets the call through when the notes file is {label}", g2.returncode == 0,
              (g2.returncode, g2.stdout[-200:], g2.stderr[-200:]))
        r = ts.rx(d2, env2, "complete", "CP1")
        check(f"[CP3-T09] complete still runs and finishes when the notes file is {label}",
              r.returncode == 0 and cp1_done(d2, env2), (r.returncode, r.stdout[-200:], r.stderr[-300:]))


# ---- CP3-T10: docs --------------------------------------------------------------------------------------------

COMPLETE_RE = re.compile(r"(`complete`|ratchet\.py complete|RATCHET\}? complete|\bcomplete CP)", re.I)
REASON_RE = re.compile(r"time-?\s?out|timed out|cut off", re.I)


def doc_names_complete_in_background(text):
    """True when two neighbouring sentences name `complete`, running it in the background and a timeout."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]
    for i in range(len(sents)):
        win = " ".join(sents[i:i + 2])
        if COMPLETE_RE.search(win) and BG_RE.search(win) and REASON_RE.search(win):
            return True
    return False


def t10_docs():
    print("CP3-T10: README and the orchestrator skill tell agents to run complete in the background")
    readme, skill = README.read_text(), SKILL.read_text()
    check("[CP3-T10] README names `complete` with running it in the background and the tool timeout reason",
          doc_names_complete_in_background(readme))
    check("[CP3-T10] the orchestrator SKILL.md names `complete` with running it in the background and the timeout reason",
          doc_names_complete_in_background(skill))
    check("[CP3-T10] README's list of test files names tests/test_complete_foreground.py",
          "tests/test_complete_foreground.py" in readme)
    check("[CP3-T10] this test file exists", Path(__file__).is_file())


TESTS = [
    ("CP3-T01", t01_foreground_warns_first),
    ("CP3-T02", t02_warning_before_regression),
    ("CP3-T03", t03_warning_changes_nothing),
    ("CP3-T04", t04_background_no_warning),
    ("CP3-T05", t05_no_signal_still_warns),
    ("CP3-T06", t06_unknown_is_foreground),
    ("CP3-T07", t07_rerun_does_not_repeat),
    ("CP3-T08", t08_refused_and_other_commands),
    ("CP3-T09", t09_guard_lets_complete_through),
    ("CP3-T10", t10_docs),
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
    prebuild(11)  # the finished projects the cases below use, built a few at a time
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
