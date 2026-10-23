#!/usr/bin/env python3
"""
Plain-language status tests (CP3): `status` (text by default, JSON behind --json), `plan-summary`, blocked-run
options, jargon-free user-visible text, and the hooks-skipped line.
    python3 tests/test_status_plain.py

Assumed interface (the implementation must provide it):
  * `status`            plain text: first line "Step N of M done: <title of last finished step>" (no flag)
  * `status --json`     the old machine output, unchanged
  * `plan-summary`      new command: one block per step with its title, first done-when line, a "UI review: yes|no"
                        line, "Risk: low|normal|high" and the reviewer ids for that risk; a note when hooks are skipped
  * blocked `status`    the reason in quotes plus 2-3 options on lines starting "A)", "B)", ("C)")
  * blocked `next`      still JSON (phase, action, stop_ok) plus "options": [{"letter": "A", "text": "..."}]
Everything runs in throwaway git projects with a temp HOME / RATCHET_HOME; the repo's own .ratchet is never touched.
"""
import ast
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import _tmp
import cp2_t11_base_pairs
import run_all
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
SCRUB = ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT", "RATCHET_HOST_PROVIDER", "CLAUDECODE",
         "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_SANDBOX_NETWORK_DISABLED",
         "GEMINI_CLI", "COPILOT_CLI", "GITHUB_COPILOT_CLI", "CODEX_HOME", "COPILOT_HOME", "RATCHET_HOOKS_WAIVE")
HOME = Path(_tmp.mkdtemp(prefix="ratchet-sp-home-"))


def path_without_agent_clis() -> str:
    """The real PATH minus every agent CLI the engine knows, so a machine with Codex or Gemini installed
    sees the same 'no other provider' plan as one without them."""
    spec = importlib.util.spec_from_file_location("ratchet_engine_sp", ENGINE)
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    agents = {pr["bin"].lower() for pr in eng.PRESETS.values()}

    def is_agent(name):  # on Windows, codex.cmd or gemini.exe is still codex or gemini
        return (os.path.splitext(name)[0] if os.name == "nt" else name).lower() in agents

    folders = []
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        names = os.listdir(folder) if os.path.isdir(folder) else []
        if not any(is_agent(n) for n in names):
            folders.append(folder)  # most folders hold no agent CLI and are kept as they are
            continue
        # a folder that holds one (a mise or npm bin, say) is replaced by a copy of its other tools
        shadow = Path(_tmp.mkdtemp(prefix="ratchet-sp-bin-"))
        for name in names:
            src = Path(folder) / name
            if not is_agent(name) and src.is_file():
                try:
                    (shadow / name).symlink_to(src)
                except OSError:  # no symlinks allowed (stock Windows): that one tool is left out
                    pass
        folders.append(str(shadow))
    return os.pathsep.join(folders)


ENV = {k: v for k, v in os.environ.items() if k not in SCRUB}
ENV.update(HOME=str(HOME), RATCHET_HOME=str(HOME / "rh"), CODEX_HOME=str(HOME / "codexhome"),
           COPILOT_HOME=str(HOME / "copilothome"), RATCHET_HOST_PROVIDER="anthropic", PATH=path_without_agent_clis())
TRANSCRIPTS = Path(_tmp.mkdtemp(prefix="ratchet-sp-transcripts-"))
PASS = FAIL = 0
# CP2: generic "[CPn-Txx]" bracket-id extraction, the same pattern tests/test_install.py (CP1) and
# tests/test_run_all_dedupe.py already use, so this file's own per-test JSON report (RATCHET_TEST_REPORT)
# can tally pass/fail per planned case id without changing any existing check(...) call's text or condition.
ID_RE = re.compile(r"\[(CP\d+-T\d+)\]")
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CHECK_NAME_COUNTS: Counter = Counter()  # exact rendered `name` text -> how many times check() was called with it
GO = "yes, go ahead without hooks"
JARGON = re.compile(r"\b(fingerprint|lens|slot|nonce|adversarial|director|approve-changes|gate|stop_blocks|round cap)\b", re.I)
RAW_PHASE = re.compile(r"\b(awaiting|planning)_[a-z_]+\b")
TRACE = "Traceback"

TITLES = ["Sign-up form", "Login page", "Password reset", "Profile page", "Email alerts", "Admin report"]
DONE_WHEN = ["Visitors can create an account", "Members can sign in", "A forgotten password can be reset",
             "Members can edit their profile", "Members get an email when something changes",
             "Admins can download a monthly report"]
RISKS = ["low", "normal", "normal", "high", "low", "normal"]
UI = [False, True, False, False, True, False]
KEEP = []


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    CHECK_NAME_COUNTS[name] += 1
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


def rx(d, *args, env=None, popen=subprocess.Popen, monotonic=time.monotonic):
    # CP2: delegates to run_all.run_with_stretched_timeout() (the same helper tests/run_all.py's own Runner
    # and tests/test_install.py's Env.run() use), so the limit follows run_all.stretch_factor() re-read on
    # every poll -- a flat 120s on a normal, idle, or unreadable-load machine (factor 1.0, same as before
    # this checkpoint), stretched when the machine is genuinely busy, and a load spike mid-call still raises
    # it (never a one-time stretch_factor() snapshot taken before the call). `popen`/`monotonic` are optional
    # seams (defaulting to the real subprocess.Popen/time.monotonic, untouched at every real call site) a
    # test may inject to make the call fully deterministic, reusing tests/test_install.py's CP1-T06/T07
    # pattern.
    return run_all.run_with_stretched_timeout([sys.executable, str(ENGINE), *args], 120.0, cwd=d,
                                               env=env or ENV, popen=popen, monotonic=monotonic)


def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def say(d, text, session="s-owner"):
    tp = TRANSCRIPTS / f"{Path(d).name}-{session}.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    return subprocess.run([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(d), "prompt": text, "session_id": session,
                                            "transcript_path": str(tp)}))


def clear_hb(d):
    p = Path(d) / ".ratchet" / "hook-heartbeat.json"
    if p.exists():
        hb = json.loads(p.read_text())
        for k in ("guard", "stop"):
            hb.pop(k, None)
        p.write_text(json.dumps(hb))


def new_git_dir(prefix) -> Path:
    d = Path(_tmp.mkdtemp(prefix=prefix))
    KEEP.append(d)
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    return d


def plan_project(approve: bool) -> Path:
    """A 6-step plan (mixed risk, two UI steps). Submitted; approved when `approve`."""
    d = new_git_dir("ratchet-sp-p-")
    say(d, "please build the feature")
    r = rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, "config", "--set", "min_review_seconds=0")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "Accounts", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": []}
    for i in range(6):
        cp = {"id": f"CP{i + 1}", "title": TITLES[i], "summary": "s", "done_when": [DONE_WHEN[i], "second line"],
              "gates": {"behavior": True, "ui": UI[i], "adversarial": True}, "risk": RISKS[i],
              "tests": {"files": [f"tests/cp{i + 1}.test.js"], "command": f"node --test tests/cp{i + 1}.test.js"}}
        if RISKS[i] == "high":
            cp["risk_reason"] = _tmp.RISK_REASON_MIN  # CP1: plan-submit refuses high risk with no risk_reason
        if UI[i]:
            cp.update(ui_scope="the page", ui_states=[{"name": "default", "how": "open it"}])
        plan["checkpoints"].append(cp)
        (run / "tests" / f"CP{i + 1}.json").write_text(json.dumps({"cases": [{"id": f"CP{i + 1}-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    if approve:
        rx(d, "ask", "approve-plan")  # CP5: an approval answers the question Ratchet printed
        say(d, "yes, go ahead")
        r = rx(d, "approve-plan")
        assert r.returncode == 0, r.stderr + r.stdout
    clear_hb(d)
    return d


def clone(src: Path) -> Path:
    dst = Path(_tmp.mkdtemp(prefix="ratchet-sp-c-"))
    KEEP.append(dst)
    shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return dst


def spath(d):
    return Path(d) / ".ratchet/runs/f/state.json"


def mutate(d, fn):
    """Edit the throwaway project's state.json (fixture only) and re-baseline its integrity record."""
    p = spath(d)
    st = json.loads(p.read_text())
    fn(st)
    p.write_text(json.dumps(st))
    # Re-baseline with the engine's own record_chain (what accept-state does), in this suite's RATCHET_HOME.
    # accept-state itself now needs a person typing a code at a real terminal, which a fixture cannot be.
    r = subprocess.run([sys.executable, "-c", REBASELINE, str(ENGINE), str(p)], cwd=d, env=ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    return d


REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
              "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
              "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
              "m.record_chain(Path(sys.argv[2]))\n")


def finish(st, n):
    for i in range(1, 7):
        c = st["checkpoints"]["CP%d" % i]
        c["status"] = "done" if i <= n else "pending"
        c["step"] = "complete" if i <= n else "tests"
        for g in c["gates"].values():
            if g.get("required") or g is c["gates"].get("human"):
                g["status"] = "passed" if i <= n else "pending"
    st["current"] = "CP%d" % (n + 1) if n < 6 else None


def blocked(reason):
    def f(st):
        st["blocked_from"] = st["phase"]
        st["phase"] = "blocked"
        st["blocked_reason"] = reason
    return f


def phase(name, **extra):
    def f(st):
        st["phase"] = name
        st.update(extra)
    return f


def status_text(d):
    return rx(d, "status")


def opts_in(text):
    return re.findall(r"^\s*([A-Z])\)\s+(\S.*)$", text, re.M)


def quoted(text, s):
    return any(q1 + s + q2 in text for q1, q2 in (('"', '"'), ("“", "”"), ("'", "'")))


def blocks_by_title(text):
    """Split plan-summary text into one block per step: from the line naming its title to the next title's line."""
    lines = text.splitlines()
    idx = []
    for i, t in enumerate(TITLES):
        hits = [n for n, l in enumerate(lines) if t in l]
        idx.append(hits[0] if len(hits) == 1 else None)
    if None in idx or idx != sorted(idx):
        return None, [sum(1 for l in lines if t in l) for t in TITLES]
    idx.append(len(lines))
    return ["\n".join(lines[idx[i]:idx[i + 1]]) for i in range(6)], None


# =================================================================================================================
# CP2: dedup and load-aware timeout in this very suite's own engine-CLI calls and nested-suite re-runs.
#
# This checkpoint's "production code" is this file's own rx() helper and the CP3-T21 block (the `for t in
# ("test_gates.py", "test_hooks.py"): ... check(...)` loop a few hundred lines below). Before this checkpoint
# both were flat: rx() always waited 120s and CP3-T21 always waited 590s and always re-ran both suites, even
# when tests/run_all.py already ran them as their own suites in this same run (doubling their ~6-minute cost
# for nothing). This checkpoint adds, to this file, the standing contract the functions below must keep:
#   - a top-level `covered_suites()` function, shaped exactly like
#     tests/test_trust_cp5_regression.py's `run_all_covers()` (same RATCHET_RUN_ALL / RATCHET_RUN_ALL_SUITES
#     semantics, via the shared `_tmp.run_all_covers()` helper both files call), but over the universe
#     {"test_gates.py", "test_hooks.py"} (CP2-T09 proves the match).
#   - rx() computing its subprocess.run(..., timeout=...) as
#     max(120, 120 * run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0]) instead of a flat 120.
#   - the CP3-T21 loop's body (everything above its existing, unchanged `check(f"[CP3-T21] {t} exits 0", ...)`
#     call) pulled out into two top-level functions so this checkpoint's cases can drive it directly instead
#     of running the whole file recursively:
#       * `cp3_t21_run_one(name)`: for one suite filename, returns the SimpleNamespace/CompletedProcess-like
#         result (skipping the subprocess call with a canned pass when `name` is in covered_suites(), else
#         running it for real with a timeout of max(590, 590 * run_all.stretch_factor(...)[0])).
#       * `cp3_t21_engine_suites()`: loops over both suite names, calling cp3_t21_run_one() for each and then
#         the SAME existing `check(f"[CP3-T21] {t} exits 0", ...)` call (CP2-T11/T12 pin that this call's text
#         and condition never change, and that there is still exactly one such call site), called once from
#         main() in CP3-T21's current place.
#
# A case below that calls covered_suites()/cp3_t21_run_one()/cp3_t21_engine_suites() still goes through
# _try_call() so a future regression in one of those names reports a clean check() failure rather than an
# uncaught crash.
#
# CP2's own test-writing brief explicitly allows monkeypatching subprocess.run to capture the *requested*
# timeout value (never driving a real kill-on-timeout path) for every case here that only needs to prove the
# computed VALUE, not that a hang is actually stopped (that proof already lives in tests/test_install.py's
# CP1-T06/T07, reusing run_all.run_with_stretched_timeout()'s injectable popen/monotonic seams). CP2-T03 needs
# to prove a suite genuinely still runs and passes, so it uses a real, fast call instead; CP2-T04's own real
# proof for test_hooks.py comes from CP3-T21's own real run earlier in this same process (see cp2_t04()), so
# it never launches a second, independent real test_hooks.py subprocess just to re-prove the same thing.

set_fake_load = _tmp.set_fake_load
restore_env = _tmp.restore_env

# The commit this checkpoint's test-writing started from (the tip of the branch before this file's CP2
# section was added). CP2-T11 pins the ORIGINAL check(...) call sites against this exact commit, so a later
# edit here can only ADD new ones, never remove, reword or narrow one that already existed.
STATUS_PLAIN_BASE_COMMIT = "699da723422e890bac8f3eebbc9bbc0e63bd549d"

CP3_T21_SUITES = ("test_gates.py", "test_hooks.py")

# The most recent cp3_t21_run_one() result per suite name, keyed by suite filename. main() always runs
# cp3_t21_engine_suites() (the "[CP3-T21] existing engine suites stay green" block) once, for real, before
# run_cp2_cases() -- this lets cp2_t04() confirm that prior real result for test_hooks.py instead of launching
# a second, independent real subprocess just to re-prove the same thing (see cp2_t04()).
CP3_T21_RESULTS: dict = {}

# [CP2 round 3, adv-round-02 fix X1] The result main()'s own CP3-T21 block recorded for test_hooks.py,
# snapshotted exactly once by main() right after cp3_t21_engine_suites() returns and before any CP2-Txx case
# runs. cp2_t04()'s real-call sub-check reads this instead of CP3_T21_RESULTS directly, because
# cp2_t02()'s stubbed call (which runs before cp2_t04() in run_cp2_cases()'s call order) overwrites
# CP3_T21_RESULTS["test_hooks.py"] with its own canned result.
CP2_T04_REAL_RESULT = None


def covered_suites() -> set:
    """Suite names from {"test_gates.py", "test_hooks.py"} that tests/run_all.py already ran as their own
    suites in this run. Calls the same parameterized _tmp.run_all_covers() helper tests/test_trust_cp5_regression.py's
    run_all_covers() calls (same RATCHET_RUN_ALL / RATCHET_RUN_ALL_SUITES semantics), just over this file's
    own universe (CP2-T09 proves the two functions agree combo by combo on the same universe)."""
    return _tmp.run_all_covers(CP3_T21_SUITES)


def cp3_t21_run_one(name, popen=subprocess.Popen, monotonic=time.monotonic):
    """Runs one CP3-T21 engine suite (test_gates.py/test_hooks.py) as a real subprocess via
    run_all.run_with_stretched_timeout() (floor 590s, re-reading run_all.stretch_factor() on every poll
    exactly like rx()'s own timeout -- never a one-time snapshot before the call) and returns the result --
    unless `name` is already covered_suites() (tests/run_all.py's own run of it already proved it in this
    run), in which case the real subprocess is skipped and a canned passing result is returned instead, the
    same skip-when-already-covered pattern tests/test_trust_cp5_regression.py's run_all_covers() uses. Also
    records the result in CP3_T21_RESULTS under `name`, so a later, cheaper check (cp2_t04()'s real-call
    sub-check) can read it back instead of running the suite again. `popen`/`monotonic` are the same optional
    seams rx() takes, threaded straight through to run_with_stretched_timeout()."""
    if name in covered_suites():
        r = SimpleNamespace(returncode=0, stdout="", stderr="",
                            tail=f"covered by run_all: {name} runs as its own suite in this run")
    else:
        r = run_all.run_with_stretched_timeout([sys.executable, str(ROOT / "tests" / name)], 590.0,
                                                popen=popen, monotonic=monotonic)
    CP3_T21_RESULTS[name] = r
    return r


def cp3_t21_engine_suites(popen=subprocess.Popen, monotonic=time.monotonic):
    """The CP3-T21 loop's entry point: for each engine suite, runs (or skips, per cp3_t21_run_one()) it and
    records the result through the one pre-existing check(...) call site, unchanged from before this
    checkpoint except that a skipped suite's detail now says why (the tail text cp3_t21_run_one() computed for
    it) instead of silently looking identical to a real run. `popen`/`monotonic` are threaded straight through
    to cp3_t21_run_one()."""
    for t in CP3_T21_SUITES:
        r = cp3_t21_run_one(t, popen=popen, monotonic=monotonic)
        tail = getattr(r, "tail", "")
        detail = (tail + " " if tail else "") + r.stdout[-300:] + r.stderr[-200:]
        check(f"[CP3-T21] {t} exits 0", r.returncode == 0, detail)


def _try_call(fn):
    """Calls fn() and returns (True, value); if a name this checkpoint's fix is expected to add doesn't exist
    yet, returns (False, exc) instead of letting the case crash, so a planned CP2 case reports a clean
    check() failure rather than an uncaught crash while the fix is still missing."""
    try:
        return True, fn()
    except (NameError, AttributeError, TypeError) as e:
        return False, e


class _FakeQuickProc:
    """Stand-in for subprocess.Popen that finishes immediately with returncode 0 and empty output, so a
    heavy real suite (test_gates.py, test_hooks.py) is never actually started just to read off the timeout
    value run_all.run_with_stretched_timeout() computed for it."""

    def __init__(self, argv):
        self.argv = argv
        self.pid = 9999
        self.returncode = 0

    def communicate(self, timeout=None):
        return "", ""

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _patch_run(real):
    """Returns (calls, fake_popen): a stand-in for the `popen` seam run_all.run_with_stretched_timeout()
    takes (the call site rx()/cp3_t21_run_one() now delegate to), recording the argv of every call. When
    real is True it still starts the real process (used only for rx(), which is fast); when False it returns
    a _FakeQuickProc that finishes immediately, so a heavy real suite (test_gates.py, test_hooks.py) is never
    actually started just to read off the timeout value that would have been used."""
    calls = []
    real_popen = subprocess.Popen

    def fake_popen(argv, **kw):
        calls.append({"args": argv, **kw})
        if real:
            return real_popen(argv, **kw)
        return _FakeQuickProc(argv)
    return calls, fake_popen


def _set_run_all_env(env):
    """Sets RATCHET_RUN_ALL/RATCHET_RUN_ALL_SUITES exactly to `env` (both cleared first), returning what to
    hand to restore_env()."""
    saved = {k: os.environ.get(k) for k in ("RATCHET_RUN_ALL", "RATCHET_RUN_ALL_SUITES")}
    os.environ.pop("RATCHET_RUN_ALL", None)
    os.environ.pop("RATCHET_RUN_ALL_SUITES", None)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    return saved


def _run_cp3_t21_guarded(env, real=False):
    """Calls cp3_t21_engine_suites() directly with its `popen` seam stubbed (recording the argv of every
    call instead of actually starting the process, unless real=True) and the given RATCHET_RUN_ALL/
    RATCHET_RUN_ALL_SUITES, mirroring tests/test_run_all_dedupe.py's _run_guarded() against
    test_trust_cp5_regression.py. Returns (argvs, (ok, result), status_after): the list of argv lists the
    `popen` seam was actually called with, the _try_call() outcome of calling cp3_t21_engine_suites() itself,
    and CASE_STATUS["CP3-T21"] right after the call (None if the function doesn't exist yet, or if it exists
    but recorded nothing)."""
    calls, fake_popen = _patch_run(real)
    saved_env = _set_run_all_env(env)
    CASE_STATUS.pop("CP3-T21", None)
    try:
        ok, result = _try_call(lambda: cp3_t21_engine_suites(popen=fake_popen))
    finally:
        restore_env(saved_env)
    argvs = [kw.get("args") for kw in calls]
    return argvs, (ok, result), CASE_STATUS.get("CP3-T21")


def collect_check_pairs(source, filename="<src>"):
    """Every check(...) call site in `source` as a (name-source-text, condition-source-text) pair, read from
    the AST so renaming a loop variable or re-wrapping a call is caught, not just a changed literal string."""
    tree = ast.parse(source, filename=filename)
    pairs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check" and len(node.args) >= 2:
            name_src = ast.get_source_segment(source, node.args[0])
            cond_src = ast.get_source_segment(source, node.args[1])
            if name_src is not None and cond_src is not None:
                pairs.append((name_src, cond_src))
    return pairs


def cp2_t01(project):
    print("CP2-T01: rx()'s engine-CLI timeout is unchanged on a normal, idle, or unreadable-load machine")
    # CP2 round 2: rx() now delegates to run_all.run_with_stretched_timeout() (it re-reads stretch_factor() on
    # every poll instead of a one-time snapshot before the call, so a mid-call load spike still extends it --
    # see cp2_t10()'s added spike checks for that proof). subprocess.run is no longer the call site (Popen is,
    # inside run_with_stretched_timeout()), so the effective limit is read off the real, fast, non-hanging
    # call's own `.timeout_limit` attribute, exactly like tests/test_install.py's CP1-T01..T05 do for Env.run().
    for label, load, cpus in (("unset", None, None), ("low-load", "1", "8"), ("unavailable", "unavailable", None)):
        saved = set_fake_load(load, cpus)
        try:
            r = rx(project, "status")
        finally:
            restore_env(saved)
        check(f"[CP2-T01] {label}: rx()'s subprocess timeout is exactly 120, byte for byte the same as before "
              "this checkpoint",
              r.timeout_limit == 120, r.timeout_limit)
        check(f"[CP2-T01] {label}: the status call still completes normally (exit 0)",
              r.returncode == 0, r.stderr[:200] + r.stdout[:200])


def cp2_t02():
    print("CP2-T02: CP3-T21's own subprocess timeout is unchanged when a suite actually runs on a normal, "
          "idle, or unreadable-load machine")
    # CP2 round 2: cp3_t21_run_one() now delegates to run_all.run_with_stretched_timeout() too; its `popen`
    # seam is stubbed via _patch_run(real=False) (an immediate-success fake process, so the heavy real suite
    # is never actually started) and the effective limit is read off the returned `.timeout_limit`.
    for label, load, cpus in (("unset", None, None), ("low-load", "1", "8"), ("unavailable", "unavailable", None)):
        saved = set_fake_load(load, cpus)
        _, fake_popen = _patch_run(real=False)
        saved_env = _set_run_all_env({})  # RATCHET_RUN_ALL unset: the suite actually runs, never skipped
        try:
            ok, result = _try_call(lambda: cp3_t21_run_one("test_hooks.py", popen=fake_popen))
        finally:
            restore_env(saved_env)
            restore_env(saved)
        check(f"[CP2-T02] {label}: the timeout used for test_hooks.py is exactly 590, unchanged from before "
              "this checkpoint",
              ok and getattr(result, "timeout_limit", None) == 590,
              result if not ok else getattr(result, "timeout_limit", None))


def cp2_t03(project):
    print("CP2-T03: rx()'s timeout stretches under simulated high load, matching run_all.stretch_factor() exactly")
    # CP2 round 2: reads the real call's own `.timeout_limit` (run_all.run_with_stretched_timeout()'s own
    # return value) instead of a captured subprocess.run(timeout=...) kwarg -- see cp2_t01() above.
    for load, cpus in (("16", "4"), ("40", "4")):
        saved = set_fake_load(load, cpus)
        try:
            expected_factor, _, _ = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
            expected = max(120.0, 120.0 * expected_factor)
            r = rx(project, "status")
        finally:
            restore_env(saved)
        check(f"[CP2-T03] load {load} on {cpus} CPUs: rx()'s subprocess timeout equals exactly "
              f"120*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected}), never less than 120",
              r.timeout_limit == expected, r.timeout_limit)
        check(f"[CP2-T03] load {load} on {cpus} CPUs: the real status call still completes and passes inside "
              "that stretched budget",
              r.returncode == 0, r.stderr[:200] + r.stdout[:200])


def cp2_t04():
    print("CP2-T04: CP3-T21's nested-suite timeout stretches under simulated high load, matching "
          "run_all.stretch_factor() exactly")
    # [CP2 round 3, adv-round-02 fix X1] main() already snapshotted CP3_T21_RESULTS["test_hooks.py"] into the
    # module-level CP2_T04_REAL_RESULT global right after CP3-T21's own block ran/skipped it, before any
    # CP2-Txx case (including cp2_t02()'s stubbed call, which runs earlier in run_cp2_cases()'s call order)
    # could overwrite CP3_T21_RESULTS with a canned result. Read that snapshot here instead of reading
    # CP3_T21_RESULTS directly, which by now holds cp2_t02()'s stubbed result, not the genuine one.
    real_result = CP2_T04_REAL_RESULT
    # Guard: confirm the snapshot genuinely predates cp2_t02()'s overwrite. By the time this runs,
    # CP3_T21_RESULTS["test_hooks.py"] holds cp2_t02()'s own stubbed object; the snapshot above must be a
    # different object, not that same (later) one.
    check("[CP2-T04] the snapshot read here is the genuine pre-CP2 result, not a later overwrite -- a "
          "different object than what CP3_T21_RESULTS now holds after cp2_t02()'s stubbed call",
          real_result is not None and real_result is not CP3_T21_RESULTS.get("test_hooks.py"),
          "no snapshot captured" if real_result is None else "snapshot is identical to the current dict entry")
    # CP2 round 2: reads the stubbed call's own `.timeout_limit` (run_all.run_with_stretched_timeout()'s own
    # return value, via the immediate-success `popen` seam _patch_run(real=False) injects) instead of a
    # captured subprocess.run(timeout=...) kwarg -- see cp2_t01()/cp2_t02() above.
    for load, cpus in (("16", "4"), ("40", "4")):
        saved = set_fake_load(load, cpus)
        try:
            expected_factor, _, _ = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
            expected = max(590.0, 590.0 * expected_factor)
            _, fake_popen = _patch_run(real=False)
            saved_env = _set_run_all_env({})
            try:
                ok, result = _try_call(lambda: cp3_t21_run_one("test_hooks.py", popen=fake_popen))
            finally:
                restore_env(saved_env)
        finally:
            restore_env(saved)
        check(f"[CP2-T04] load {load} on {cpus} CPUs: the timeout used for test_hooks.py equals exactly "
              f"590*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected}), never less than 590",
              ok and getattr(result, "timeout_limit", None) == expected,
              result if not ok else getattr(result, "timeout_limit", None))

    # Confirms the suite genuinely still runs and passes, without launching a second, independent real
    # test_hooks.py subprocess here: CP3-T21's own block (main(), run once before run_cp2_cases()) already
    # ran it for real (or skipped it, when run_all.py already covered it in this run -- the same
    # skip-when-already-covered dedup this checkpoint exists to apply everywhere), and that is the result
    # snapshotted above as `real_result`. A second, independent real invocation here would recreate exactly
    # the doubled-suite-cost bug done-means 2 exists to eliminate.
    check("[CP2-T04] the test_hooks.py result CP3-T21 already produced earlier in this run (real run, or "
          "skip when already covered by run_all.py) finished and passed, with no second real invocation here",
          real_result is not None and getattr(real_result, "returncode", None) == 0,
          "no CP3-T21 result recorded for test_hooks.py" if real_result is None else
          getattr(real_result, "stderr", "")[-300:] + getattr(real_result, "stdout", "")[-200:])


def cp2_t05():
    print("CP2-T05: RATCHET_RUN_ALL unset -- CP3-T21 runs both suites as real subprocesses regardless of "
          "RATCHET_RUN_ALL_SUITES")
    for label, suites_env in (("unset", None), ("empty", ""), ("naming both", "test_gates.py,test_hooks.py")):
        env = {} if suites_env is None else {"RATCHET_RUN_ALL_SUITES": suites_env}
        argvs, (ok, result), status_after = _run_cp3_t21_guarded(env)
        names = sorted(Path(a[-1]).name for a in argvs if a)
        check(f"[CP2-T05] RATCHET_RUN_ALL_SUITES {label}: both test_gates.py and test_hooks.py are run as "
              "real subprocess calls (never skipped)",
              ok and names == ["test_gates.py", "test_hooks.py"], result if not ok else names)
        check(f"[CP2-T05] RATCHET_RUN_ALL_SUITES {label}: the block's check(...) call records a pass for each, "
              "exactly as it did before this checkpoint",
              ok and status_after is True, status_after if ok else result)


def cp2_t06():
    print("CP2-T06: RATCHET_RUN_ALL=1 and RATCHET_RUN_ALL_SUITES names both suites -- CP3-T21 skips the "
          "subprocess re-run for both, through its one existing check(...) call")
    env = {"RATCHET_RUN_ALL": "1", "RATCHET_RUN_ALL_SUITES": "test_gates.py,test_hooks.py,test_other.py"}
    argvs, (ok, result), status_after = _run_cp3_t21_guarded(env)
    check("[CP2-T06] no subprocess re-run of either suite happens", ok and argvs == [], result if not ok else argvs)
    check("[CP2-T06] the SAME pre-existing check(...) call per suite still fires and records a pass for each "
          "-- not a second check() call added for the skip path",
          ok and status_after is True, status_after if ok else result)


def cp2_t07():
    print("CP2-T07: RATCHET_RUN_ALL=1 but RATCHET_RUN_ALL_SUITES names only test_gates.py -- the unnamed one "
          "still runs in full")
    env = {"RATCHET_RUN_ALL": "1", "RATCHET_RUN_ALL_SUITES": "test_gates.py"}
    argvs, (ok, result), status_after = _run_cp3_t21_guarded(env)
    names = sorted(Path(a[-1]).name for a in argvs if a)
    check("[CP2-T07] test_gates.py is skipped (no subprocess call) but test_hooks.py is still run as a real "
          "subprocess", ok and names == ["test_hooks.py"], result if not ok else names)
    check("[CP2-T07] both suites are still proven pass/fail by the block's check(...) call",
          ok and status_after is True, status_after if ok else result)


def cp2_t08():
    print("CP2-T08: RATCHET_RUN_ALL_SUITES alone, without RATCHET_RUN_ALL, changes nothing")
    env = {"RATCHET_RUN_ALL_SUITES": "test_gates.py,test_hooks.py"}
    argvs, (ok, result), status_after = _run_cp3_t21_guarded(env)
    names = sorted(Path(a[-1]).name for a in argvs if a)
    check("[CP2-T08] both suites still run as real subprocesses -- the suites list alone, without the flag, "
          "never triggers a skip",
          ok and names == ["test_gates.py", "test_hooks.py"], result if not ok else names)


def cp2_t09():
    print("CP2-T09: the new dedup helper (covered_suites) has the exact same shape and behavior as "
          "test_trust_cp5_regression.run_all_covers()")
    import test_trust_cp5_regression as cp5r
    saved_universe = cp5r.REGRESSION_SUITES
    # Compared on the SAME universe as CP2's own dedup ({"test_gates.py", "test_hooks.py"}, not CP5's
    # {"test_gates.py", "test_hooks_waiver.py"}): this proves the two functions apply the identical dedup
    # pattern to the identical inputs, not that they happen to agree on two different universes. Restored
    # below no matter what.
    cp5r.REGRESSION_SUITES = ("test_gates.py", "test_hooks.py")
    matrix = [
        {},
        {"RATCHET_RUN_ALL": "1", "RATCHET_RUN_ALL_SUITES": "test_gates.py,test_hooks.py"},
        {"RATCHET_RUN_ALL": "1", "RATCHET_RUN_ALL_SUITES": "test_gates.py"},
        {"RATCHET_RUN_ALL": "1", "RATCHET_RUN_ALL_SUITES": "test_gates.py,test_hooks.py,test_other.py"},
        {"RATCHET_RUN_ALL_SUITES": "test_gates.py,test_hooks.py"},
        {"RATCHET_RUN_ALL": "1", "RATCHET_RUN_ALL_SUITES": ""},
    ]
    try:
        for i, env in enumerate(matrix):
            saved_env = _set_run_all_env(env)
            try:
                ok, mine = _try_call(lambda: covered_suites())
                theirs = cp5r.run_all_covers()
            finally:
                restore_env(saved_env)
            check(f"[CP2-T09] combo {i} ({env}): covered_suites() matches run_all_covers() computed on the "
                  "same universe and environment",
                  ok and mine == theirs, (env, mine if ok else mine, theirs))
    finally:
        cp5r.REGRESSION_SUITES = saved_universe


class _ScriptedSpikeProc:
    """Deterministic stand-in for subprocess.Popen used only to prove re-sampling (CP2-T10): raises
    subprocess.TimeoutExpired on its first two polls (like a still-running call) while the caller spikes the
    fake load file after the first poll and drops it back after the second, then finishes normally on the
    third poll -- all without any real sleep. The same pattern tests/test_install.py's CP1-T07 uses directly
    against run_all.run_with_stretched_timeout(); reused here against rx()/cp3_t21_run_one()'s own injected
    `popen` seam so the SAME re-sampling is proven for both of this checkpoint's call sites."""

    def __init__(self, argv, load_file):
        self.argv = argv
        self.pid = 5151
        self.returncode = None
        self._n = 0
        self._load_file = load_file

    def communicate(self, timeout=None):
        self._n += 1
        if self._n == 1:
            self._load_file.write_text("32")  # spike: load/cpus=8 -> factor hits DEFAULT_MAX_STRETCH (4.0)
            raise subprocess.TimeoutExpired(self.argv, timeout)
        if self._n == 2:
            self._load_file.write_text("1")  # falls back: the limit already granted must not shrink
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.returncode = 0
        return "", ""

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def cp2_t10(project):
    print("CP2-T10: the load-aware timeout formula used by rx() and CP3-T21 is exactly run_all.stretch_factor(), "
          "not a separate formula")
    # CP2 round 2: reads the real/stubbed call's own `.timeout_limit` instead of a captured
    # subprocess.run(timeout=...) kwarg -- see cp2_t01()/cp2_t02() above.
    for label, load, cpus in (("idle", None, None), ("borderline", "6", "4"), ("high", "16", "4"),
                               ("unavailable", "unavailable", None)):
        saved = set_fake_load(load, cpus)
        try:
            expected_factor, _, _ = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
            expected_rx = max(120.0, 120.0 * expected_factor)
            expected_cp3 = max(590.0, 590.0 * expected_factor)

            r = rx(project, "status")

            _, fake_popen = _patch_run(real=False)
            saved_env = _set_run_all_env({})
            try:
                ok, result = _try_call(lambda: cp3_t21_run_one("test_hooks.py", popen=fake_popen))
            finally:
                restore_env(saved_env)
        finally:
            restore_env(saved)
        check(f"[CP2-T10] {label}: rx()'s effective timeout equals exactly "
              f"120*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected_rx})",
              r.timeout_limit == expected_rx, r.timeout_limit)
        check(f"[CP2-T10] {label}: CP3-T21's effective timeout (for a suite that actually runs) equals exactly "
              f"590*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected_cp3})",
              ok and getattr(result, "timeout_limit", None) == expected_cp3,
              result if not ok else getattr(result, "timeout_limit", None))

    # CP2 round 2 (director-authorized addition, codex-correctness/X2): the property that distinguishes
    # run_with_stretched_timeout() from a one-time stretch_factor() snapshot taken before the call -- the real
    # flakiness vector this checkpoint exists to kill -- is that it is RE-SAMPLED on every poll, so a load
    # spike mid-call still raises the effective limit, and a later drop never takes it back. Proven
    # deterministically for both call sites, reusing tests/test_install.py's CP1-T07 pattern directly against
    # rx()/cp3_t21_run_one()'s own injected popen/monotonic seams (no real sleep, no real clock read, no real
    # hang).
    for label, base, make_call, run_all_env in (
        ("rx()", 120.0, lambda popen, monotonic: rx(project, "status", popen=popen, monotonic=monotonic), None),
        ("CP3-T21", 590.0,
         lambda popen, monotonic: cp3_t21_run_one("test_hooks.py", popen=popen, monotonic=monotonic), {}),
    ):
        load_file = Path(_tmp.mkdtemp(prefix="cp2-t10-spike-")) / "load.txt"
        load_file.write_text("1")
        saved = set_fake_load(f"@{load_file}", "4")
        saved_env = _set_run_all_env(run_all_env) if run_all_env is not None else None
        try:
            expected_peak = base * run_all.DEFAULT_MAX_STRETCH

            def fake_popen(argv, **kw):
                return _ScriptedSpikeProc(argv, load_file)

            clock = iter([0.0, 0.01, 0.02, 0.03])

            def fake_monotonic():
                return next(clock, 0.03)

            ok, result = _try_call(lambda: make_call(fake_popen, fake_monotonic))
        finally:
            if saved_env is not None:
                restore_env(saved_env)
            restore_env(saved)
        check(f"[CP2-T10] {label} spike: a load spike mid-call raises the effective timeout to the peak "
              f"stretch_factor() reading reached ({base}*DEFAULT_MAX_STRETCH={expected_peak}), re-sampled on "
              "every poll, never a one-time snapshot taken before the call and never a later lower reading "
              "taken after the spike drops",
              ok and getattr(result, "timeout_limit", None) == expected_peak,
              result if not ok else getattr(result, "timeout_limit", None))
        check(f"[CP2-T10] {label} spike: the call still finishes and reports success once the scripted "
              "process exits",
              ok and getattr(result, "returncode", None) == 0,
              result if not ok else getattr(result, "returncode", None))


def cp2_t11():
    print("CP2-T11: static guard -- every check(...) call present before this checkpoint's fix is still "
          "present, unreworded, with its original condition")
    # CP2 round 2 (director-authorized, omp-glm-standards/X3): the pin now comes from a checked-in fixture
    # (tests/cp2_t11_base_pairs.py's PAIRS, generated once from `git show STATUS_PLAIN_BASE_COMMIT:...`) instead
    # of shelling out to git history at test time -- a shallow CI clone (fetch-depth 1, actions/checkout's
    # default) has no access to that commit and would otherwise make this guard permanently and unrelatedly
    # red. The multiset comparison logic and refusal on removal/reword/narrowing are unchanged.
    check("[CP2-T11] setup: the checked-in base fixture (tests/cp2_t11_base_pairs.py) pins the exact base "
          "commit this guard freezes",
          cp2_t11_base_pairs.BASE_COMMIT == STATUS_PLAIN_BASE_COMMIT,
          (cp2_t11_base_pairs.BASE_COMMIT, STATUS_PLAIN_BASE_COMMIT))
    pinned_pairs = Counter(tuple(p) for p in cp2_t11_base_pairs.PAIRS)
    check("[CP2-T11] setup: the pinned base file contains at least one check(...) call",
          sum(pinned_pairs.values()) > 0, sum(pinned_pairs.values()))
    current_src = Path(__file__).read_text()
    current_pairs = Counter(collect_check_pairs(current_src, str(Path(__file__))))
    missing = pinned_pairs - current_pairs
    check("[CP2-T11] every check(...) call from before this checkpoint is still present, unreworded, with its "
          "original condition (exact multiset match; new calls may be ADDED for this checkpoint's own new "
          "cases, but none existing may be removed, reworded, or have its condition narrowed)",
          not missing, list(missing.items())[:5])


def cp2_t12():
    print("CP2-T12: the CP3-T21 block still records through exactly one check(...) call site per suite name "
          "after the fix")
    current_src = Path(__file__).read_text()
    tree = ast.parse(current_src, filename=str(Path(__file__)))
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check" and node.args:
            name_src = ast.get_source_segment(current_src, node.args[0]) or ""
            if "[CP3-T21]" in name_src:  # the exact bracket tag, never a loose substring match (a CP2 case's
                                         # own name text may mention "CP3-T21" in prose without that tag)
                sites.append(name_src)
    check("[CP2-T12] exactly one check(...) call site in the source names CP3-T21 (folding the skip into the "
          "pre-existing call, matching the test_trust_cp5_regression.py precedent, never a second call site "
          "added)",
          len(sites) == 1, sites)

    before_gates = CHECK_NAME_COUNTS["[CP3-T21] test_gates.py exits 0"]
    before_hooks = CHECK_NAME_COUNTS["[CP3-T21] test_hooks.py exits 0"]
    argvs, (ok, result), status_after = _run_cp3_t21_guarded({})
    after_gates = CHECK_NAME_COUNTS["[CP3-T21] test_gates.py exits 0"] - before_gates
    after_hooks = CHECK_NAME_COUNTS["[CP3-T21] test_hooks.py exits 0"] - before_hooks
    check("[CP2-T12] a fresh, uncovered CP3-T21 run fires that one call site exactly once for test_gates.py's "
          "result and exactly once for test_hooks.py's result",
          ok and after_gates == 1 and after_hooks == 1,
          (result if not ok else None, after_gates, after_hooks))


CP2_CASES = ("CP2-T01", "CP2-T02", "CP2-T03", "CP2-T04", "CP2-T05", "CP2-T06", "CP2-T07", "CP2-T08", "CP2-T09",
             "CP2-T10", "CP2-T11", "CP2-T12")


def run_cp2_cases(base_project):
    """Runs every CP2-Txx case, each isolated so one crashing (a name the fix hasn't added yet, outside what
    _try_call() already guards against) still lets every other case run and still reports a result instead of
    silently dropping it."""
    global FAIL
    for cid, fn in (("CP2-T01", lambda: cp2_t01(base_project)), ("CP2-T02", cp2_t02),
                    ("CP2-T03", lambda: cp2_t03(base_project)), ("CP2-T04", cp2_t04), ("CP2-T05", cp2_t05),
                    ("CP2-T06", cp2_t06), ("CP2-T07", cp2_t07), ("CP2-T08", cp2_t08), ("CP2-T09", cp2_t09),
                    ("CP2-T10", lambda: cp2_t10(base_project)), ("CP2-T11", cp2_t11), ("CP2-T12", cp2_t12)):
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as exc:  # pragma: no cover -- a crash here still counts as a result, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(exc).__name__}: {exc}")
            print(f"  ✗ [{cid}] CRASHED: {exc}")
            traceback.print_exc()


def write_cp2_report():
    """Writes {"cases": [{"id","status","message"}]} to $RATCHET_TEST_REPORT when that env var is set, one
    entry per planned CP2-Txx case (a case whose check() never ran is reported as "error", never silently
    dropped). Older bracketed ids already in this file (e.g. CP3-T21, from an earlier run) are harmless
    extras this same mechanism also happens to tally, but only CP2-T* ids are required to resolve here."""
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid in CP2_CASES:
        if cid in CASE_STATUS:
            status = "passed" if CASE_STATUS[cid] else "failed"
            msg = "" if status == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]
        else:
            status, msg = "error", "no check() call tagged with this id ran"
        cases.append({"id": cid, "status": status, "message": msg})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    base = plan_project(True)
    pre = plan_project(False)

    # ------------------------------------------------------------ T01 / T02 / T03
    building = clone(base)
    mutate(building, lambda st: finish(st, 2))
    print("[CP3-T01] status is a short plain summary by default")
    r = status_text(building)
    out = r.stdout
    try:
        json.loads(out)
        is_json = True
    except ValueError:
        is_json = False
    first = out.strip().splitlines()[0] if out.strip() else ""
    check("[CP3-T01] exit 0 and not JSON", r.returncode == 0 and not is_json, r.stderr + out[:100])
    check("[CP3-T01] first line is 'Step 2 of 6 done: <title of the last finished step>'",
          first.startswith("Step 2 of 6 done:") and TITLES[1] in first, first)
    check("[CP3-T01] at most 12 lines", 0 < len(out.strip().splitlines()) <= 12, len(out.splitlines()))
    check("[CP3-T01] says what is happening now, by the current step's title", TITLES[2] in out, out)

    print("[CP3-T02] status --json keeps the machine output")
    r = rx(building, "status", "--json")
    try:
        j = json.loads(r.stdout)
    except ValueError:
        j = None
    want = {"phase": str, "current": str, "next": str, "stop_ok": bool, "checkpoints": list, "viewer_url": str,
            "root": str, "slug": str, "feature": str, "done": int, "total": int, "hooks": dict}
    check("[CP3-T02] valid JSON object", isinstance(j, dict), r.stdout[:100] + r.stderr)
    j = j or {}
    check("[CP3-T02] pre-existing keys keep their types", all(isinstance(j.get(k), t) for k, t in want.items()),
          {k: type(j.get(k)).__name__ for k in want})
    check("[CP3-T02] hooks_warning key is still there", "hooks_warning" in j)
    check("[CP3-T02] values are unchanged (phase building, current CP3, 2 of 6)",
          j.get("phase") == "building" and j.get("current") == "CP3" and (j.get("done"), j.get("total")) == (2, 6), j)

    print("[CP3-T03] progress count at the boundaries")
    zero = clone(base)
    one = clone(base)
    mutate(one, lambda st: finish(st, 1))
    alldone = clone(base)
    mutate(alldone, lambda st: (finish(st, 6), st.update(phase="done")))
    z, o, a = status_text(zero).stdout, status_text(one).stdout, status_text(alldone).stdout
    check("[CP3-T03] 0 done: 'Step 0 of 6 done' or 'Not started'",
          "Step 0 of 6 done" in z or "not started" in z.lower(), z)
    check("[CP3-T03] 1 done: first line 'Step 1 of 6 done: <title>'",
          o.strip().splitlines()[0].startswith("Step 1 of 6 done:") and TITLES[0] in o.splitlines()[0], o)
    check("[CP3-T03] all done: says all 6 steps are done, never 'Step 7 of 6'",
          re.search(r"all 6 steps", a, re.I) is not None and "Step 7" not in a, a)

    # ------------------------------------------------------------ T04
    print("[CP3-T04] status says the right thing for every phase")
    variants = {
        "planning": (clone(base), phase("planning")),
        "awaiting_plan_approval": (clone(pre), None),
        "awaiting_checkpoint_review": (clone(base), lambda st: (finish(st, 2), st.update(phase="awaiting_checkpoint_review", review_target="CP2"))),
        "paused": (clone(base), lambda st: st.update(paused_from="building", phase="paused", blocked_reason="paused")),
        "cancelled": (clone(base), lambda st: st.update(phase="cancelled", blocked_reason="cancelled by the user")),
        "done": (clone(base), lambda st: (finish(st, 6), st.update(phase="done"))),
    }
    outs = {}
    for name, (d, fn) in variants.items():
        if fn:
            mutate(d, fn)
        r = status_text(d)
        outs[name] = r.stdout
        check(f"[CP3-T04] {name}: exit 0, no traceback, no raw phase identifier",
              r.returncode == 0 and TRACE not in r.stderr and not RAW_PHASE.search(r.stdout), r.stderr + r.stdout)
    check("[CP3-T04] every phase prints something different", len(set(outs.values())) == len(outs), list(outs))
    check("[CP3-T04] plan approval waits for the user's yes",
          re.search(r"plan", outs["awaiting_plan_approval"], re.I) and re.search(r"approv|yes|your", outs["awaiting_plan_approval"], re.I),
          outs["awaiting_plan_approval"])
    check("[CP3-T04] paused says paused", "paused" in outs["paused"].lower(), outs["paused"])
    check("[CP3-T04] cancelled says cancelled", "cancel" in outs["cancelled"].lower(), outs["cancelled"])
    check("[CP3-T04] review phase asks the user to look at the step",
          re.search(r"review|look|try", outs["awaiting_checkpoint_review"], re.I) is not None, outs["awaiting_checkpoint_review"])

    # ------------------------------------------------------------ T05
    print("[CP3-T05] no plan yet, and outside a run")
    fresh = new_git_dir("ratchet-sp-fresh-")
    say(fresh, "please build the feature")
    rx(fresh, "init", "feature", "--slug", "f")
    r = status_text(fresh)
    check("[CP3-T05] fresh project: exit 0, no traceback, plain 'no plan yet' line",
          r.returncode == 0 and TRACE not in r.stderr and re.search(r"no plan (yet)?", r.stdout, re.I) is not None,
          r.stderr + r.stdout)
    bare = new_git_dir("ratchet-sp-bare-")
    r = status_text(bare)
    check("[CP3-T05] never-initialised dir: non-zero, actionable message, no traceback",
          r.returncode != 0 and TRACE not in r.stderr and TRACE not in r.stdout and (r.stderr + r.stdout).strip() != "",
          r.stderr + r.stdout)

    # ------------------------------------------------------------ T06 / T07 / T08
    print("[CP3-T06] plan summary lists every step")
    r = rx(pre, "plan-summary")
    check("[CP3-T06] plan-summary exits 0", r.returncode == 0, r.stderr + r.stdout)
    blks, counts = blocks_by_title(r.stdout)
    check("[CP3-T06] each of the six titles appears on exactly one line, in plan order", blks is not None, counts)
    blks = blks or [""] * 6
    check("[CP3-T06] each step shows its first done-when line", all(DONE_WHEN[i] in blks[i] for i in range(6)),
          [DONE_WHEN[i] in blks[i] for i in range(6)])
    ui_marks = [re.search(r"UI review\W+(yes|no)\b", b, re.I) for b in blks]
    check("[CP3-T06] each step states 'UI review: yes|no' matching the plan",
          all(m and (m.group(1).lower() == "yes") == UI[i] for i, m in enumerate(ui_marks)),
          [m.group(1) if m else None for m in ui_marks])
    check("[CP3-T06] each step states its risk in words",
          all(re.search(r"risk\W+" + RISKS[i] + r"\b", blks[i], re.I) for i in range(6)), [b[:120] for b in blks])

    print("[CP3-T07] plan summary reflects per-step differences")
    depth = json.loads(rx(pre, "config").stdout)["review_depth"]

    def reviewed_by(block):
        m = re.search(r"Reviewed by: (.*)", block)
        return [x.strip() for x in m.group(1).split(";")] if m else []
    low, normal, high = reviewed_by(blks[0]), reviewed_by(blks[1]), reviewed_by(blks[3])
    check("[CP3-T07] low-risk step names its one reviewer in plain words",
          len(low) == 1 and "(code standards and bugs)" in low[0], low)
    check("[CP3-T07] high-risk step lists more reviewers than the low-risk one, incl. one on bugs",
          len(high) == len(depth["high"]) > len(low) and any("(bugs and security)" in h for h in high), high)
    check("[CP3-T07] normal-risk step differs from both", normal not in (low, high) and len(normal) == len(depth["normal"]),
          normal)
    check("[CP3-T07] no reviewer ids (host-both, codex-correctness) or arrows in the summary",
          not re.search(r"host-|-correctness|-standards|any-|→", "\n".join(blks)), blks[3])
    check("[CP3-T07] the summary names the models and warns when no other provider can review",
          "Models:" in r.stdout and "no other provider is available" in r.stdout, r.stdout[-600:])
    check("[CP3-T07] UI review says yes for exactly the two UI steps",
          [bool(m and m.group(1).lower() == "yes") for m in ui_marks] == UI, UI)

    print("[CP3-T08] plan summary edge cases")
    r = rx(fresh, "plan-summary")
    check("[CP3-T08] no plan: non-zero, message, no traceback",
          r.returncode != 0 and TRACE not in r.stderr and (r.stderr + r.stdout).strip() != "", r.stderr + r.stdout)
    waived = clone(base)
    rx(waived, "ask", "hooks-waive")
    say(waived, GO)
    rx(waived, "hooks-waive")
    clear_hb(waived)
    r = rx(waived, "plan-summary")
    b2, _ = blocks_by_title(r.stdout)
    check("[CP3-T08] waived: still lists all six steps", r.returncode == 0 and b2 is not None, r.stderr + r.stdout[:200])
    check("[CP3-T08] waived: one line notes that hooks were skipped",
          len([l for l in r.stdout.splitlines() if re.search(r"hooks", l, re.I) and re.search(r"skipped", l, re.I)]) == 1,
          r.stdout)
    r = rx(pre, "plan-summary")
    check("[CP3-T08] not waived: no 'skipped' note", "skipped" not in r.stdout.lower(), r.stdout)

    # ------------------------------------------------------------ T09 / T10 / T11 / T12
    print("[CP3-T09] a blocked run quotes the finding and offers lettered options")
    R_BEH = "CP3: behavior gate failed 3 times"
    P_BEH = "The tests for CP3 still fail after 3 tries."
    P_ADV = "The reviewers still have 2 open concern(s) about CP3 after 6 rounds of fixes, which is the limit."
    P_CAP = "The reviewers have used all the review rounds allowed for this run (30 of 30)."
    R_ADV = "CP3: adversarial review still has 2 finding(s) after 6 rounds - needs a human decision."
    R_CAP = "Run-wide adversarial round cap reached (30/30) - needs a human decision."
    variants = {}
    for key, reason in (("beh", R_BEH), ("adv", R_ADV), ("cap", R_CAP)):
        d = clone(base)
        mutate(d, lambda st, reason=reason: (finish(st, 2), blocked(reason)(st)))
        variants[key] = (d, reason)
    d, reason = variants["beh"]
    r = status_text(d)
    o = opts_in(r.stdout)
    check("[CP3-T09] status quotes the reason in plain words", r.returncode == 0 and quoted(r.stdout, P_BEH), r.stdout)
    check("[CP3-T09] status offers 2 or 3 options lettered A), B)[, C)]",
          2 <= len(o) <= 3 and [x[0] for x in o] == ["A", "B", "C"][:len(o)], r.stdout)
    n = rx(d, "next")
    try:
        nj = json.loads(n.stdout)
    except ValueError:
        nj = {}
    nopts = nj.get("options") if isinstance(nj, dict) else None
    check("[CP3-T09] next also carries 2 or 3 lettered options",
          isinstance(nopts, list) and 2 <= len(nopts) <= 3 and [x.get("letter") for x in nopts] == ["A", "B", "C"][:len(nopts)],
          n.stdout)
    check("[CP3-T09] next keeps the raw reason and adds a plain one", R_BEH in json.dumps(nj.get("action", "")) and nj.get("reason_plain") == P_BEH, nj)

    print("[CP3-T10] options fit the kind of block")
    texts = {}
    for key, (d, reason) in variants.items():
        rr = rx(d, "next")
        try:
            jj = json.loads(rr.stdout)
        except ValueError:
            jj = {}
        texts[key] = " ".join(x.get("text", "") for x in (jj.get("options") or []) if isinstance(x, dict)).lower()
        check(f"[CP3-T10] {key}: status quotes its own reason in plain words",
              quoted(status_text(d).stdout, {"beh": P_BEH, "adv": P_ADV, "cap": P_CAP}[key]), status_text(d).stdout)
    check("[CP3-T10] option sets differ by cause", len(set(texts.values())) == 3 and "" not in texts.values(), texts)
    check("[CP3-T10] round cap offers raising the limit or stopping",
          re.search(r"raise|increase|allow more|higher", texts["cap"]) and "stop" in texts["cap"], texts["cap"])
    check("[CP3-T10] test failure offers a retry or revising the step",
          re.search(r"again|retry|different approach", texts["beh"]) and re.search(r"change|revise|rewrite|adjust", texts["beh"]),
          texts["beh"])

    print("[CP3-T11] block output stays machine-readable")
    d, reason = variants["beh"]
    n = rx(d, "next")
    try:
        nj = json.loads(n.stdout)
    except ValueError:
        nj = None
    check("[CP3-T11] next is JSON with phase=blocked, stop_ok=true, action",
          isinstance(nj, dict) and nj.get("phase") == "blocked" and nj.get("stop_ok") is True and isinstance(nj.get("action"), str),
          n.stdout[:200])
    nj = nj or {}
    check("[CP3-T11] options are a list of {letter, text}",
          isinstance(nj.get("options"), list) and all(isinstance(x, dict) and set(("letter", "text")) <= set(x)
                                                      and re.fullmatch(r"[A-C]", str(x["letter"])) and str(x["text"]).strip()
                                                      for x in nj["options"]) and len(nj["options"]) >= 2, nj.get("options"))
    check("[CP3-T11] hooks object still present", isinstance(nj.get("hooks"), dict), nj.keys())

    print("[CP3-T12] odd reasons still render")
    d = clone(base)
    mutate(d, lambda st: (finish(st, 2), blocked("")(st)))
    r = status_text(d)
    check("[CP3-T12] empty reason: no traceback, generic plain line",
          r.returncode == 0 and TRACE not in r.stderr and re.search(r"block|stuck|stopped|need", r.stdout, re.I) is not None, r.stderr + r.stdout)
    odd = 'He said "hi"\nsecond line — café ✓'
    d = clone(base)
    mutate(d, lambda st: (finish(st, 2), blocked(odd)(st)))
    r = status_text(d)
    check("[CP3-T12] odd reason: no traceback and text kept intact",
          r.returncode == 0 and TRACE not in r.stderr and 'He said "hi"' in r.stdout and "café" in r.stdout and "second line" in r.stdout,
          r.stderr + r.stdout)
    check("[CP3-T12] odd reason still gets 2-3 options", 2 <= len(opts_in(r.stdout)) <= 3, r.stdout)
    pd = clone(base)
    mutate(pd, lambda st: st.update(paused_from="building", phase="paused", blocked_reason="paused"))
    r = status_text(pd)
    check("[CP3-T12] paused: 'paused' wording and no failure options",
          r.returncode == 0 and "paused" in r.stdout.lower() and not opts_in(r.stdout), r.stdout)

    # ------------------------------------------------------------ T14
    print("[CP3-T14] a stale gate is described plainly")
    stale = clone(base)

    def go_stale(st):
        finish(st, 2)
        st["checkpoints"]["CP3"]["gates"]["behavior"]["status"] = "stale"
        st["checkpoints"]["CP3"]["step"] = "behavior"
    mutate(stale, go_stale)
    r = status_text(stale)
    check("[CP3-T14] says the check must be re-run because the code changed",
          re.search(r"re-?run|run (it|them|the checks?) again|check again", r.stdout, re.I) and re.search(r"code .*changed|changed", r.stdout, re.I),
          r.stdout)
    check("[CP3-T14] no 'stale' and no fingerprint", not re.search(r"\bstale\b|fingerprint", r.stdout, re.I), r.stdout)

    # ------------------------------------------------------------ T15 / T16
    print("[CP3-T15] warnings and doctor talk to the agent")
    nowaive = clone(base)
    mutate(nowaive, lambda st: finish(st, 2))
    n = rx(nowaive, "next")
    try:
        warn = json.loads(n.stdout).get("warning") or ""
    except ValueError:
        warn = ""
    dr = rx(nowaive, "doctor")
    for label, txt in (("warning", warn), ("doctor", dr.stdout)):
        check(f"[CP3-T15] {label}: addressed to the agent (offer to the user, ask first)",
              re.search(r"offer", txt, re.I) and re.search(r"\bask\b", txt, re.I), txt)
        check(f"[CP3-T15] {label}: never tells the user to type a ratchet.py command", "ratchet.py" not in txt, txt)

    print("[CP3-T16] hooks skipped shows in status text")
    r = status_text(waived)
    lines = [l for l in r.stdout.splitlines() if re.search(r"hooks", l, re.I) and re.search(r"skipped", l, re.I)]
    check("[CP3-T16] waived: a plain line says hooks were skipped at the user's request",
          len(lines) == 1 and re.search(r"request|choice|chose|asked", lines[0], re.I) is not None, r.stdout)
    r = status_text(nowaive)
    check("[CP3-T16] hooks missing but not waived: no 'skipped' line", "skipped" not in r.stdout.lower(), r.stdout)
    inst = rx(waived, "install-hooks", "--host", "claude", "--scope", "project")
    r = status_text(waived)
    check("[CP3-T16] installing hooks removes the line", inst.returncode == 0 and "skipped" not in r.stdout.lower(),
          inst.stderr + r.stdout)

    # ------------------------------------------------------------ T13
    print("[CP3-T13] nothing the user sees says fingerprint, lens or slot")
    seen_text = {}
    for name, d in [("building", building), ("stale", stale), ("pre", pre), ("waived", waived), ("nowaive", nowaive),
                    ("beh", variants["beh"][0]), ("adv", variants["adv"][0]), ("cap", variants["cap"][0]),
                    ("done", alldone)]:
        seen_text[f"{name} status"] = status_text(d).stdout
        seen_text[f"{name} view"] = rx(d, "view").stdout
    for name, d in [("pre", pre), ("nowaive", nowaive)]:
        seen_text[f"{name} plan-summary"] = rx(d, "plan-summary").stdout
    for k in ("beh", "adv", "cap"):
        try:
            nj = json.loads(rx(variants[k][0], "next").stdout)
            seen_text[f"{k} next action"] = str(nj.get("reason_plain", "")) + " " + " ".join(
                str(x.get("text", "")) for x in nj.get("options", []) if isinstance(x, dict))
        except ValueError:
            seen_text[f"{k} next action"] = ""
    seen_text["hooks warning"] = warn
    seen_text["doctor"] = dr.stdout
    bad = {k: (JARGON.findall(v) or RAW_PHASE.findall(v)) for k, v in seen_text.items() if JARGON.search(v) or RAW_PHASE.search(v)}
    check("[CP3-T13] no jargon word or raw phase id in any user-facing output", not bad, bad)
    check("[CP3-T13] the outputs were actually produced", all(v.strip() for k, v in seen_text.items() if "warning" not in k),
          [k for k, v in seen_text.items() if not v.strip()])

    # ------------------------------------------------------------ T21
    print("[CP3-T22] options never offer something that re-blocks at once")
    REAL = {
        "per-cp cap (adv-record)": "CP3: adversarial review still has 2 finding(s) after 6 rounds \u2014 needs a human/director decision.",
        "per-cp cap (reviewers)": "CP3: 6 adversarial rounds used without approval \u2014 needs a human/director decision.",
        "run cap": "Run-wide adversarial round cap reached (30/30). Raising it is the user's call (config change + approve-changes).",
    }
    for label, reason in REAL.items():
        d = clone(base)
        mutate(d, lambda st, reason=reason: (finish(st, 2), blocked(reason)(st)))
        out = status_text(d).stdout
        low = out.lower()
        opts = opts_in(out)
        texts_low = [t.lower() for _, t in opts]
        check(f"[CP3-T22] {label}: no 'another round' option, offers raising the limit",
              "another round" not in low and re.search(r"raise the review limit", low) is not None, out)
        expected = 3 if label == "run cap" else 4
        check(f"[CP3-T22] {label}: {expected} options (CP1 order), simplify first and raise last",
              len(opts) == expected and bool(texts_low) and "simplify" in texts_low[0]
              and "raise" in texts_low[-1], out)

    print("[CP3-T23] block reasons are shown in plain words; raw text stays in status --json")
    REAL["behavior"] = "CP3: behavior gate failed 3 times \u2014 needs a human decision."
    REAL["ui"] = "CP3: UI gate failed 3 rounds \u2014 needs a human decision."
    REAL["stop valve"] = "The agent tried to stop 9 times without any gate progress \u2014 loop halted for the user to look at."
    for label, reason in REAL.items():
        d = clone(base)
        mutate(d, lambda st, reason=reason: (finish(st, 2), blocked(reason)(st)))
        out = status_text(d).stdout
        check(f"[CP3-T23] {label}: status text has no jargon", not JARGON.search(out), out)
        view = rx(d, "view").stdout
        check(f"[CP3-T23] {label}: view output has no jargon", not JARGON.search(view), view)
        js = rx(d, "status", "--json")
        try:
            sj = json.loads(js.stdout)
        except ValueError:
            sj = {}
        check(f"[CP3-T23] {label}: --json keeps the raw reason and adds a plain one",
              sj.get("blocked_reason") == reason and sj.get("blocked_reason_plain") and not JARGON.search(sj["blocked_reason_plain"]), sj)

    print("[CP3-T21] existing engine suites stay green")
    cp3_t21_engine_suites()

    # [CP2 round 3, adv-round-02 fix X1] Snapshot CP3_T21_RESULTS["test_hooks.py"] now, once, before any
    # CP2-Txx case runs -- cp2_t02()'s stubbed call (below, via run_cp2_cases()) would otherwise overwrite
    # CP3_T21_RESULTS with a canned result before cp2_t04() gets a chance to read the genuine one. See
    # CP2_T04_REAL_RESULT's own comment above.
    global CP2_T04_REAL_RESULT
    CP2_T04_REAL_RESULT = CP3_T21_RESULTS.get("test_hooks.py")

    # ------------------------------------------------------------ T24 (adv-round-06 fix: the per-checkpoint
    # round-limit branch must still offer a "change the step" option, alongside raise/stop, not just those two)
    print("[CP3-T24] per-checkpoint round limit still offers raise, change-the-step, and stop")
    d, reason = variants["adv"]
    out = status_text(d).stdout
    opts = opts_in(out)
    texts_low = [t.lower() for _, t in opts]
    check("[CP3-T24] per-checkpoint limit offers exactly 4 options (CP1 order: simplify, change, stop, raise)",
          len(opts) == 4, out)
    check("[CP3-T24] option 1 simplifies the step", len(texts_low) > 0 and "simplify" in texts_low[0], out)
    check("[CP3-T24] option 2 changes the step so the review notes no longer apply",
          len(texts_low) > 1 and "change the step" in texts_low[1], out)
    check("[CP3-T24] option 3 stops and keeps what is finished", len(texts_low) > 2 and "stop" in texts_low[2], out)
    check("[CP3-T24] option 4 (last) raises the review limit",
          len(texts_low) > 3 and "raise the review limit" in texts_low[3], out)
    cap_d, cap_reason = variants["cap"]
    cap_opts = opts_in(status_text(cap_d).stdout)
    cap_texts_low = [t.lower() for _, t in cap_opts]
    check("[CP3-T24] the run-wide cap branch stays distinct (3 options: simplify, stop, raise; no change-the-step)",
          len(cap_opts) == 3 and "change the step" not in " ".join(cap_texts_low), cap_opts)
    check("[CP3-T24] the run-wide cap branch still leads with simplify and ends with raise",
          bool(cap_texts_low) and "simplify" in cap_texts_low[0] and "raise" in cap_texts_low[-1], cap_opts)

    # ------------------------------------------------------------ T25 (adv-round-06 fix): _blocked_accept_note
    # must never claim "the last fix changed the code, these findings need a fresh look" when the blocked round's
    # own record has nothing open to begin with -- only the stale-file shortcut used to say that regardless.
    print("[CP3-T25] no stale 'fresh look'/accept line when the recorded round has nothing open")
    d25 = clone(base)
    old_fp = json.loads(rx(d25, "fingerprint").stdout)["id"]
    (Path(d25) / "src/a.js").write_text("export const a = 2; // changed after CP3's round was recorded\n")

    def _inject_approved_round_no_findings(st):
        finish(st, 2)
        st["checkpoints"]["CP3"]["adv_round"] = {
            "round": 1, "recorded": "2026-01-01T00:00:00+00:00", "opened_at": "2026-01-01T00:00:00+00:00",
            "fingerprint": old_fp, "nonce": "n1", "reviewers": ["host-both"]}
        blocked("Run-wide adversarial round cap reached (30/30) - needs a human decision.")(st)

    mutate(d25, _inject_approved_round_no_findings)
    nj = json.loads(rx(d25, "next").stdout)
    check("[CP3-T25] next prints no accept_note: the recorded round has no open findings",
          nj.get("accept_note") is None, nj)
    out25 = rx(d25, "status").stdout
    check("[CP3-T25] status prints no 'fresh look' or accept-finding line either",
          "fresh look" not in out25.lower() and "accept-finding" not in out25.lower(), out25)

    # ------------------------------------------------------------ CP2-T01..T12
    run_cp2_cases(base)

    for d in KEEP:
        shutil.rmtree(d, ignore_errors=True)
    write_cp2_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
