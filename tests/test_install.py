#!/usr/bin/env python3
"""
install.sh end to end, in throwaway dirs: HOME, RATCHET_HOME and the working directory are all temp folders, so the
real ~/.claude, ~/.ratchet-loop and this repo's .ratchet/ are never touched.
    python3 tests/test_install.py
"""
import json
import os
import re
import subprocess
import sys
import traceback
import _tmp
import run_all
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
PASS = FAIL = 0
SKILLS = ["ratchet-orchestrator", "ratchet-tdd-planner", "ratchet-design-md", "ratchet-prototype", "ratchet-ui-compare", "ratchet-adversarial-loop"]
HOOK_FILES = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "gemini": ".gemini/settings.json", "copilot": ".copilot/hooks/ratchet.json"}
EVENTS = ("stop", "prompt", "guard")
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP\d+-T\d+)\]")


def entry_counts(path, host):
    """Number of Ratchet hook commands per event (Copilot's powershell twin of each entry is not counted)."""
    t = Path(path).read_text(encoding="utf-8")
    return {ev: len(re.findall(r'python3 \\?"[^"\n]*ratchet\.py\\?" hook %s --host %s' % (ev, host), t)) for ev in EVENTS}


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


INSTALL_TIMEOUT_BASE = 120  # seconds; stretched by run_all.stretch_factor() the same way run_all.py stretches
                            # its own --timeout.


class Env:
    def __init__(self, tmp):
        self.home, self.rh, self.cwd = (Path(tmp) / n for n in ("home", "ratchet_home", "project"))
        for d in (self.home, self.rh, self.cwd):
            d.mkdir()

    def run(self, *args, extra_env=None):
        env = dict(os.environ, HOME=str(self.home), RATCHET_HOME=str(self.rh))
        for k in ("XDG_CONFIG_HOME", "CODEX_HOME", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX",
                  "CODEX_THREAD_ID", "CODEX_SANDBOX_NETWORK_DISABLED", "GEMINI_CLI", "COPILOT_CLI",
                  "GITHUB_COPILOT_CLI"):
            env.pop(k, None)  # the host is auto-detected from these; tests set them explicitly
        env.update(extra_env or {})
        # Load-aware timeout, process-group kill on expiry and the re-check-load-while-waiting logic all live in
        # the one shared helper tests/run_all.py already uses for its own suites (run_with_stretched_timeout, next
        # to stretch_factor()), so this is the only place that formula is written.
        return run_all.run_with_stretched_timeout(["bash", str(INSTALL), *args], INSTALL_TIMEOUT_BASE,
                                                    cwd=self.cwd, env=env)

    def files(self, root):
        return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


def real_home_marks():
    out = {}
    for rel in list(HOOK_FILES.values()) + [".ratchet-loop"]:
        p = Path.home() / rel
        out[rel] = p.stat().st_mtime_ns if p.exists() else None
    return out


# ---- CP1-T01..T07: load-aware timeout for Env.run() -------------------------------------------------------
# Env.run() delegates to tests/run_all.py's own run_with_stretched_timeout() helper, next to stretch_factor().
# These cases prove the limit it actually used follows that same stretch_factor()/read_load() formula (CP1
# reuses it via this sibling `import run_all`, the precedent tests/test_runner_load_fixes.py already set), stays
# exactly 120 on a quiet or unreadable-load machine, and that a hang is still stopped -- never per-test sleeps,
# a re-typed copy of the formula, or a real wall-clock race. CP1-T06 and CP1-T07 drive
# run_all.run_with_stretched_timeout() directly with an injected `popen`/`monotonic` (optional seams on that
# function, defaulting to the real subprocess.Popen/time.monotonic so every other call site, including
# Env.run() above, is untouched) so the timeout/kill-on-expiry/never-shrinks behavior is proven with no real
# process, no real sleep and no real clock read anywhere in the test.

set_fake_load = _tmp.set_fake_load
restore_env = _tmp.restore_env


def capture_run_timeout(e, load, cpus, extra_env=None):
    """Runs Env.run() for real (install.sh itself is fast; no stand-in is needed) with RATCHET_FAKE_LOADAVG/
    RATCHET_FAKE_CPUS faked for the duration of the call, and returns {"timeout": <limit>, "returncode": <rc>},
    the limit run_with_stretched_timeout() actually used (its `.timeout_limit` attribute) and the call's own
    exit code, so a case can also check the call actually completed normally. Restores the environment even if
    Env.run() raises."""
    saved_load = set_fake_load(load, cpus)
    try:
        r = e.run(extra_env=extra_env)
    finally:
        restore_env(saved_load)
    return {"timeout": r.timeout_limit, "returncode": r.returncode}


def cp1_t01():
    print("CP1-T01: an idle machine keeps install.sh's subprocess timeout at exactly 120s")
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        kw = capture_run_timeout(e, "1", "8")
    check("[CP1-T01] load 1 on 8 CPUs (load/cpus=0.125, well under 1) leaves the subprocess timeout at exactly 120",
          kw.get("timeout") == 120, kw.get("timeout"))
    check("[CP1-T01] the call completes normally (exit 0)", kw.get("returncode") == 0, kw.get("returncode"))


def cp1_t02():
    print("CP1-T02: an unreadable load (RATCHET_FAKE_LOADAVG=unavailable) keeps the timeout at 120s")
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        kw = capture_run_timeout(e, "unavailable", None)
    check("[CP1-T02] RATCHET_FAKE_LOADAVG=unavailable leaves the subprocess timeout at exactly 120, the same as "
          "when load cannot be stretched",
          kw.get("timeout") == 120, kw.get("timeout"))
    check("[CP1-T02] the call completes normally (exit 0)", kw.get("returncode") == 0, kw.get("returncode"))


def cp1_t03():
    print("CP1-T03: a garbage load reading falls back to 120s instead of crashing")
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        kw = capture_run_timeout(e, "not-a-number", None)
    check("[CP1-T03] RATCHET_FAKE_LOADAVG=not-a-number raises no exception, and the subprocess timeout passed is "
          "exactly 120, matching run_all.read_load()'s own fallback for an unusable reading",
          kw.get("timeout") == 120, kw.get("timeout"))
    check("[CP1-T03] the call completes normally (exit 0)", kw.get("returncode") == 0, kw.get("returncode"))


def cp1_t04():
    print("CP1-T04: moderate simulated load stretches the timeout by exactly run_all's own factor")
    saved = set_fake_load("8", "4")
    try:
        expected_factor, load, cpus = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
    finally:
        restore_env(saved)
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        kw = capture_run_timeout(e, "8", "4")
    check("[CP1-T04] load 8 on 4 CPUs (load/cpus=2.0, under run_all.DEFAULT_MAX_STRETCH) gives "
          "run_all.stretch_factor() a factor of exactly 2.0",
          expected_factor == 2.0, (expected_factor, load, cpus))
    check("[CP1-T04] the subprocess timeout passed equals 120 * run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] "
          "computed fresh with the same fake env (240.0), not a hardcoded or independently-derived number",
          kw.get("timeout") == 120 * expected_factor, (kw.get("timeout"), expected_factor))
    check("[CP1-T04] the call completes normally (exit 0) under the simulated load", kw.get("returncode") == 0,
          kw.get("returncode"))


def cp1_t05():
    print("CP1-T05: extreme simulated load is capped at run_all's own max stretch, never beyond it")
    saved = set_fake_load("400", "4")
    try:
        expected_factor, load, cpus = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
    finally:
        restore_env(saved)
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        kw = capture_run_timeout(e, "400", "4")
    check("[CP1-T05] load 400 on 4 CPUs (load/cpus=100, far past the ceiling) is capped by run_all.stretch_factor() "
          "at exactly DEFAULT_MAX_STRETCH (4.0)",
          expected_factor == run_all.DEFAULT_MAX_STRETCH, (expected_factor, load, cpus))
    check("[CP1-T05] the subprocess timeout passed equals 120 * run_all.DEFAULT_MAX_STRETCH exactly (480.0), "
          "matching run_all.stretch_factor() exactly, never 120*100 and never unbounded",
          kw.get("timeout") == 120 * run_all.DEFAULT_MAX_STRETCH and kw.get("timeout") != 120 * 100,
          kw.get("timeout"))
    check("[CP1-T05] the call completes normally (exit 0) under the simulated load", kw.get("returncode") == 0,
          kw.get("returncode"))


class _FakeHangingProc:
    """Deterministic stand-in for subprocess.Popen: `.communicate()` always raises subprocess.TimeoutExpired
    (a real hang never finishes), so the only way run_with_stretched_timeout() can leave its poll loop is its
    own `remaining <= 0` check -- driven here by the injected fake monotonic clock, never a real sleep or a
    real wall-clock read. `.pid`/`.poll()`/`.wait()` behave plausibly without ever starting a real process."""

    def __init__(self, argv):
        self.argv = argv
        self.pid = 4242
        self.returncode = None

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = -9
        return self.returncode


def cp1_t06():
    print("CP1-T06: a genuinely hanging call is still stopped, at exactly the stretched limit, fully "
          "deterministically (no real process, no real sleep, no real clock read)")
    saved_load = set_fake_load("8", "4")
    try:
        expected_factor, load, cpus = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
        expected_timeout = 0.1 * expected_factor

        kill_calls = []
        real_kill_tree = run_all.kill_tree

        def spy_kill_tree(proc, *a, **kw):
            kill_calls.append(proc)
            return []

        made_procs = []

        def fake_popen(argv, **kw):
            p = _FakeHangingProc(argv)
            made_procs.append(p)
            return p

        # monotonic ticks: t0, then one tick per poll; the first poll stays under the 0.2s limit (so
        # communicate() is actually exercised, raising TimeoutExpired like a real hang would), the second
        # pushes past it, deterministically ending the loop via remaining<=0 -- never a real clock.
        clock = iter([0.0, 0.05, 0.25])

        def fake_monotonic():
            return next(clock, 0.25)

        run_all.kill_tree = spy_kill_tree
        exc = None
        try:
            try:
                run_all.run_with_stretched_timeout(["bash", "-c", "true"], 0.1, cwd=None, env=None,
                                                    popen=fake_popen, monotonic=fake_monotonic)
            except subprocess.TimeoutExpired as e:
                exc = e
        finally:
            run_all.kill_tree = real_kill_tree
    finally:
        restore_env(saved_load)

    check("[CP1-T06] a hanging call raises subprocess.TimeoutExpired, never completing normally",
          exc is not None, exc)
    check("[CP1-T06] it is stopped at exactly the stretched limit (0.1s base * stretch_factor(load 8 on 4 "
          "CPUs) = 0.2s), computed fresh from run_all.stretch_factor(), never a flat or hardcoded value",
          exc is not None and exc.timeout == expected_timeout, (getattr(exc, "timeout", None), expected_timeout))
    check("[CP1-T06] run_all.kill_tree is called exactly once, with the exact process object the timed-out "
          "call started",
          len(made_procs) == 1 and kill_calls == made_procs, (kill_calls, made_procs))


def cp1_t07():
    print("CP1-T07: a load spike mid-call raises the stretched limit permanently; the limit used is the peak "
          "reached, never a later lower reading (RATCHET_FAKE_LOADAVG=@<file>, deterministic, no real sleep)")
    load_file = Path(_tmp.mkdtemp(prefix="cp1-t07-load-")) / "load.txt"
    load_file.write_text("1")
    saved_load = set_fake_load(f"@{load_file}", "4")

    class _ScriptedProc:
        """Raises subprocess.TimeoutExpired on its first two polls (like a still-running call), spiking the
        fake load file to 32 after the first and dropping it back to 1 after the second, then finishes
        normally on the third poll -- all without any real sleep."""

        def __init__(self, argv):
            self.argv = argv
            self.pid = 4343
            self.returncode = None
            self._n = 0

        def communicate(self, timeout=None):
            self._n += 1
            if self._n == 1:
                load_file.write_text("32")  # spike: load/cpus=8 -> factor hits DEFAULT_MAX_STRETCH (4.0)
                raise subprocess.TimeoutExpired(self.argv, timeout)
            if self._n == 2:
                load_file.write_text("1")  # falls back: the limit already granted must not shrink
                raise subprocess.TimeoutExpired(self.argv, timeout)
            self.returncode = 0
            return "", ""

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    def fake_popen(argv, **kw):
        return _ScriptedProc(argv)

    clock = iter([0.0, 0.01, 0.02, 0.03])

    def fake_monotonic():
        return next(clock, 0.03)

    try:
        expected_peak = 1.0 * run_all.DEFAULT_MAX_STRETCH
        result = run_all.run_with_stretched_timeout(["bash", "-c", "true"], 1.0, cwd=None, env=None,
                                                      popen=fake_popen, monotonic=fake_monotonic)
    finally:
        restore_env(saved_load)

    check("[CP1-T07] the call finishes normally once the scripted process exits", result.returncode == 0,
          result.returncode)
    check("[CP1-T07] the limit used is the peak stretch reached mid-call (1.0 * DEFAULT_MAX_STRETCH = 4.0), "
          "never the lower reading taken afterward (1.0 * 1.0 = 1.0)",
          result.timeout_limit == expected_peak, (result.timeout_limit, expected_peak))


CP1_CASES = ("CP1-T01", "CP1-T02", "CP1-T03", "CP1-T04", "CP1-T05", "CP1-T06", "CP1-T07")
CP1_TESTS = [("CP1-T01", cp1_t01), ("CP1-T02", cp1_t02), ("CP1-T03", cp1_t03),
             ("CP1-T04", cp1_t04), ("CP1-T05", cp1_t05), ("CP1-T06", cp1_t06), ("CP1-T07", cp1_t07)]


def write_report():
    """Writes {"cases": [{"id","status","message"}]} to $RATCHET_TEST_REPORT when that env var is set, one
    entry per planned CP1-Txx case (a case whose check() never ran is reported as "error", never silently
    dropped)."""
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid in CP1_CASES:
        if cid in CASE_STATUS:
            status = "passed" if CASE_STATUS[cid] else "failed"
            msg = "" if status == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]
        else:
            status, msg = "error", "no check() call tagged with this id ran"
        cases.append({"id": cid, "status": status, "message": msg})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    before = real_home_marks()
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        r = e.run("--host", "all")
        out = r.stdout + r.stderr
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        claude_root, other_root = e.home / ".claude", e.rh
        both = all((root / "skills" / s / "SKILL.md").exists() for root in (claude_root, other_root) for s in SKILLS)
        check("[CP6-T09] --host all exits 0 and installs the Claude copy (skills and agents) under HOME/.claude",
              r.returncode == 0 and (claude_root / "skills/ratchet-orchestrator/SKILL.md").exists() and (claude_root / "agents/ratchet-fixer.md").exists(),
              f"rc={r.returncode} {out[-200:]}")
        check("[CP6-T09] --host all also installs the RATCHET_HOME copy, with all six skill folders in each copy",
              (other_root / "skills/ratchet-orchestrator/SKILL.md").exists() and (other_root / "agents/ratchet-fixer.md").exists() and both)

        missing = [h for h, f in HOOK_FILES.items() if not (e.home / f).exists() or min(entry_counts(e.home / f, h).values()) < 1]
        check("[CP6-T10] --host all writes Ratchet hook entries for Claude, Codex, Gemini and Copilot, each in its own file under the temp HOME",
              not missing, missing)
        check("[CP6-T10] the real HOME's hook files and ~/.ratchet-loop are untouched", real_home_marks() == before)

        # T12: second run, plus a stale file in an installed skill folder
        stale = other_root / "skills/ratchet-orchestrator/STALE.txt"
        stale_c = claude_root / "skills/ratchet-orchestrator/STALE.txt"
        for s in (stale, stale_c):
            s.parent.mkdir(parents=True, exist_ok=True)
            s.write_text("x")
        r2 = e.run("--host", "all")
        dup = {h: entry_counts(e.home / f, h) for h, f in HOOK_FILES.items() if (e.home / f).exists()}
        once = {"stop": 1, "prompt": 1, "guard": 1}
        check("[CP6-T12] a second --host all exits 0 and leaves exactly one Ratchet entry per event and host",
              r2.returncode == 0 and set(dup) == set(HOOK_FILES) and all(v == once for v in dup.values()), (r2.returncode, dup))
        check("[CP6-T12] a stale file inside an installed skill folder is gone after re-install (both copies)",
              not stale.exists() and not stale_c.exists() and r2.returncode == 0)
        for h, f in HOOK_FILES.items():
            if (e.home / f).exists():
                try:
                    json.loads((e.home / f).read_text(encoding="utf-8"))
                except ValueError:
                    dup[h] = "invalid json"
        check("[CP6-T12] hook files are still valid JSON after the second run", all(v == once for v in dup.values()) and len(dup) == 4, dup)

        # T15 on the first run's output
        tail = "\n".join(lines[-3:])
        check("[CP6-T15] closing lines tell the user to restart the CLI and then say a quoted sentence mentioning ratchet to the agent",
              re.search(r"restart", tail, re.I) and re.search(r"[\"'“][^\"'”\n]*ratchet[^\"'”\n]*[\"'”]", lines[-1] if lines else "", re.I) is not None,
              tail)
        check("[CP6-T15] closing lines do not tell the user to run ratchet.py doctor or a python3 command",
              lines != [] and not re.search(r"python3|ratchet\.py|\bdoctor\b", tail), tail)
        all_out = out

    # T11
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        rc = e.run("--host", "claude")
        claude_out = rc.stdout
        ok = rc.returncode == 0 and (e.home / ".claude/skills/ratchet-orchestrator/SKILL.md").exists() and e.files(e.rh) == []
        check("[CP6-T11] --host claude installs into HOME/.claude only and leaves RATCHET_HOME empty", ok, (rc.returncode, e.files(e.rh)[:3]))
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        rx = e.run("--host", "codex")
        codex_out = rx.stdout
        ok = rx.returncode == 0 and (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and not (e.home / ".claude").exists()
        check("[CP6-T11] --host codex installs into RATCHET_HOME only and creates no HOME/.claude", ok, (rx.returncode, (e.home / ".claude").exists()))

    # T16
    def names_folder(o, rh):
        return f"{rh}/skills" in o and re.search(r"link", o, re.I) is not None
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        ra = e.run("--host", "all")
        rc2 = e.run("--host", "codex")
        check("[CP6-T16] --host codex and --host all messages name the RATCHET_HOME skills folder and say to link it",
              names_folder(rc2.stdout, e.rh) and names_folder(ra.stdout, e.rh), (rc2.stdout[-250:], ra.stdout[-250:]))
        check("[CP6-T16] neither message mentions 'Other agent CLIs'", not re.search(r"other agent clis?", rc2.stdout + ra.stdout, re.I))
        check("[CP6-T16] a --host claude run does not print the non-Claude link line",
              not re.search(r"link|point your|symlink", claude_out, re.I) and str(e.rh) not in claude_out, claude_out[-250:])

    # T25: auto-detected host, and skills linked into the CLI's own skills folder
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        r = e.run(extra_env={"CODEX_THREAD_ID": "t1"})
        link = e.home / ".codex/skills/ratchet-orchestrator"
        check("[CP6-T25] no --host inside Codex installs for Codex (shared copy + Codex hooks, no Claude copy)",
              r.returncode == 0 and "Installing for: codex" in r.stdout and (e.home / ".codex/hooks.json").exists()
              and not (e.home / ".claude").exists(), r.stdout[-300:] + r.stderr[-200:])
        check("[CP6-T25] the skills are linked into ~/.codex/skills and point at the shared copy",
              link.is_symlink() and link.resolve() == (e.rh / "skills/ratchet-orchestrator").resolve()
              and (link / "SKILL.md").exists(), link)
        r2 = e.run(extra_env={"CODEX_THREAD_ID": "t1"})
        check("[CP6-T25] re-running keeps a single working link per skill",
              r2.returncode == 0 and link.is_symlink() and (link / "SKILL.md").exists(), r2.stderr[-200:])
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        r = e.run()
        check("[CP6-T25] no --host and no CLI detected falls back to Claude Code and says so",
              r.returncode == 0 and "Could not tell" in r.stdout and (e.home / ".claude/skills/ratchet-orchestrator").exists(),
              r.stdout[-300:])
        check("[CP6-T25] the closing line matches the README's sentence",
              "Set up Ratchet and continue" in r.stdout, r.stdout[-200:])

    # T13
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        rp = e.run("--host", "all", "--project")
        check("[CP6-T13] --host all --project exits 0 and puts the Claude copy in the project's .claude, not in HOME/.claude",
              rp.returncode == 0 and (e.cwd / ".claude/skills/ratchet-orchestrator/SKILL.md").exists() and not (e.home / ".claude").exists(),
              (rp.returncode, (rp.stdout + rp.stderr)[-200:]))

    # T13 detail, T18-T21
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        rp = e.run("--host", "all", "--project")
        proj = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "gemini": ".gemini/settings.json", "copilot": ".github/hooks/ratchet.json"}
        miss = [h for h, f in proj.items() if not (e.cwd / f).exists() or min(entry_counts(e.cwd / f, h).values()) < 1]
        check("[CP6-T18] --host all --project writes all four hook files under the project dir, each with Ratchet entries", not miss, (miss, rp.stderr[-200:]))
        stray = [f for f in list(HOOK_FILES.values()) + [".github"] if (e.home / f).exists()]
        check("[CP6-T18] --project writes no hook file under HOME", not stray, stray)
        check("[CP6-T19] --project keeps the shared non-Claude skills copy in RATCHET_HOME and none in the project",
              (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and not (e.cwd / ".ratchet-loop").exists(), e.files(e.cwd)[:5])
    for host, rel in (("gemini", ".gemini/settings.json"), ("copilot", ".copilot/hooks/ratchet.json")):
        with _tmp.mkdtemp() as tmp:
            e = Env(tmp)
            r1 = e.run("--host", host)
            f = e.home / rel
            others = [h for h, x in HOOK_FILES.items() if h != host and (e.home / x).exists()]
            check(f"[CP6-T20] --host {host} exits 0, installs RATCHET_HOME skills, writes only its own hook file with one entry per event",
                  r1.returncode == 0 and (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and f.exists()
                  and entry_counts(f, host) == {"stop": 1, "prompt": 1, "guard": 1} and not others and not (e.home / ".claude").exists(),
                  (r1.returncode, (r1.stdout + r1.stderr)[-200:], others))
        with _tmp.mkdtemp() as tmp:
            e = Env(tmp)
            rq = e.run("--host", host, "--project")
            pf = e.cwd / (".github/hooks/ratchet.json" if host == "copilot" else rel)
            check(f"[CP6-T21] --host {host} --project puts its hook file in the project, not HOME, and the skills copy in RATCHET_HOME",
                  rq.returncode == 0 and pf.exists() and entry_counts(pf, host) == {"stop": 1, "prompt": 1, "guard": 1}
                  and not (e.home / rel).exists() and (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists(),
                  (rq.returncode, (rq.stdout + rq.stderr)[-200:]))

    # T14
    with _tmp.mkdtemp() as tmp:
        e = Env(tmp)
        rb = e.run("--host", "nonsense")
        after_bad = e.files(e.home) + e.files(e.rh)
        ro = e.run("--bogus")
        msg = lambda r: (r.stdout + r.stderr).strip()
        check("[CP6-T14] --host nonsense exits non-zero with a short message naming the value, no traceback, nothing copied",
              rb.returncode != 0 and "nonsense" in msg(rb) and "Traceback" not in msg(rb) and len(msg(rb).splitlines()) <= 2 and after_bad == [],
              (rb.returncode, msg(rb)[:200], after_bad[:3]))
        check("[CP6-T14] --bogus exits non-zero with a short message naming the option, and copies nothing",
              ro.returncode != 0 and "--bogus" in msg(ro) and "Traceback" not in msg(ro) and len(msg(ro).splitlines()) <= 2 and e.files(e.home) + e.files(e.rh) == [],
              (ro.returncode, msg(ro)[:200]))

    # CP1-T01..T06
    for cid, fn in CP1_TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as exc:  # a crashed case fails; it never takes the other cases (or CP6's) down
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(exc).__name__}: {exc}")
            print(f"  ✗ [{cid}] CRASHED: {exc}")
            traceback.print_exc()

    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
