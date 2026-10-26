#!/usr/bin/env python3
"""
CP5: run_all.run_with_stretched_timeout() must stop the child's process tree before an interrupt
(KeyboardInterrupt, or _tmp.py's SIGTERM-to-SystemExit conversion) propagates out of it, exactly like it
already does on the ordinary timeout-expiry path -- and the success and timeout-expiry paths themselves stay
byte-for-byte unchanged by that fix.
    python3 tests/test_stretched_timeout_cleanup.py

Every case drives run_with_stretched_timeout() directly with an injected `popen=`/`monotonic=` pair (the same
seam CP1-T06/CP1-T07 in tests/test_install.py and CP2-T10 in tests/test_status_plain.py use), so the whole call
is deterministic: no real subprocess, no real sleep, no real signal. tests/run_all.py's own `kill_tree` is spied
on by swapping the module attribute for the duration of each case and restored in `finally`, so the real
OS-level kill_tree never runs against a fake proc/pid.
"""
import json
import os
import re
import subprocess
import sys
import traceback

import _tmp
import run_all

set_fake_load = _tmp.set_fake_load
restore_env = _tmp.restore_env

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP\d+-T\d+)\]")


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


# ---- fake process stand-ins ---------------------------------------------------------------------------------


class _FakeInterruptProc:
    """Deterministic stand-in for subprocess.Popen: `.communicate()` raises `exc_cls()` on its very first call
    (standing in for a real KeyboardInterrupt, or _tmp.py's SIGTERM-to-SystemExit conversion, arriving while the
    real call is blocked in communicate()). `.pid`/`.poll()`/`.wait()` behave plausibly without ever starting a
    real process."""

    def __init__(self, argv, exc_cls):
        self.argv = argv
        self.pid = 5001
        self.returncode = None
        self.exc_cls = exc_cls

    def communicate(self, timeout=None):
        raise self.exc_cls()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = -9
        return self.returncode


class _FakeSuccessProc:
    """Deterministic stand-in whose `.communicate()` returns a normal (stdout, stderr) pair on its first call,
    exercising the ordinary success path with no exception anywhere."""

    def __init__(self, argv):
        self.argv = argv
        self.pid = 5101
        self.returncode = 0

    def communicate(self, timeout=None):
        return "out-data", "err-data"

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _FakeHangingProc:
    """Deterministic stand-in whose `.communicate()` always raises subprocess.TimeoutExpired (a real hang never
    finishes), so the only way run_with_stretched_timeout() can leave its poll loop is its own `remaining <= 0`
    check, driven by the injected fake monotonic clock -- never a real sleep or a real wall-clock read. Matches
    tests/test_install.py's CP1-T06 fixture exactly."""

    def __init__(self, argv):
        self.argv = argv
        self.pid = 5201
        self.returncode = None

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = -9
        return self.returncode


# ---- CP5-T01/T02: an interrupt still stops the child before it propagates -----------------------------------


def _interrupt_case(cid, exc_cls):
    saved_load = set_fake_load("1", "8")
    kill_calls = []
    real_kill_tree = run_all.kill_tree
    made_procs = []

    def spy_kill_tree(proc, *a, **kw):
        kill_calls.append(proc)
        return []

    def fake_popen(argv, **kw):
        p = _FakeInterruptProc(argv, exc_cls)
        made_procs.append(p)
        return p

    def fake_monotonic():
        return 0.0  # the clock never advances; the body raises before any remaining<=0 check could fire

    caught = False
    run_all.kill_tree = spy_kill_tree
    try:
        try:
            run_all.run_with_stretched_timeout(["bash", "-c", "true"], 5.0, cwd=None, env=None,
                                                popen=fake_popen, monotonic=fake_monotonic)
        except exc_cls:
            caught = True
    finally:
        run_all.kill_tree = real_kill_tree
        restore_env(saved_load)

    check(f"[{cid}] run_all.kill_tree is called exactly once, with the exact process object the interrupted "
          f"call started",
          len(made_procs) == 1 and kill_calls == made_procs, (kill_calls, made_procs))
    check(f"[{cid}] the {exc_cls.__name__} is not swallowed inside run_with_stretched_timeout(): it still "
          f"reaches the caller",
          caught is True, caught)


def cp5_t01():
    print("CP5-T01: a KeyboardInterrupt while waiting on the command still stops its process tree before it "
          "propagates")
    _interrupt_case("CP5-T01", KeyboardInterrupt)


def cp5_t02():
    print("CP5-T02: a SystemExit while waiting on the command still stops its process tree before it "
          "propagates")
    _interrupt_case("CP5-T02", SystemExit)


# ---- CP5-T03: the success path is untouched -------------------------------------------------------------


def cp5_t03():
    print("CP5-T03: a command that finishes normally is not touched by the interrupt fix")
    saved_load = set_fake_load("1", "8")
    kill_calls = []
    real_kill_tree = run_all.kill_tree
    made_procs = []

    def spy_kill_tree(proc, *a, **kw):
        kill_calls.append(proc)
        return []

    def fake_popen(argv, **kw):
        p = _FakeSuccessProc(argv)
        made_procs.append(p)
        return p

    clock = iter([0.0, 0.01])

    def fake_monotonic():
        return next(clock, 0.01)

    try:
        expected_factor, _, _ = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
        run_all.kill_tree = spy_kill_tree
        result = run_all.run_with_stretched_timeout(["bash", "-c", "true"], 10.0, cwd=None, env=None,
                                                      popen=fake_popen, monotonic=fake_monotonic)
    finally:
        run_all.kill_tree = real_kill_tree
        restore_env(saved_load)

    expected_timeout = 10.0 * expected_factor
    check("[CP5-T03] the result is a CompletedProcess with the exact returncode/stdout/stderr the fake "
          "process returned",
          result.returncode == 0 and result.stdout == "out-data" and result.stderr == "err-data",
          (result.returncode, result.stdout, result.stderr))
    check("[CP5-T03] .timeout_limit equals base (10.0) times run_all.stretch_factor() computed fresh under "
          "the same fixed fake load (load 1 on 8 CPUs -> factor 1.0), never a hardcoded number",
          result.timeout_limit == expected_timeout, (result.timeout_limit, expected_timeout))
    check("[CP5-T03] run_all.kill_tree is called zero times on the success path -- it is not newly signalled "
          "by the interrupt fix",
          kill_calls == [], kill_calls)


# ---- CP5-T04: the ordinary timeout-expiry path is stopped exactly once, not twice --------------------------


def cp5_t04():
    print("CP5-T04: a command that runs out of time is still stopped exactly once, not twice")
    saved_load = set_fake_load("8", "4")
    try:
        expected_factor, _, _ = run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)
        expected_timeout = 0.1 * expected_factor

        kill_calls = []
        real_kill_tree = run_all.kill_tree
        made_procs = []

        def spy_kill_tree(proc, *a, **kw):
            kill_calls.append(proc)
            return []

        def fake_popen(argv, **kw):
            p = _FakeHangingProc(argv)
            made_procs.append(p)
            return p

        # monotonic ticks: t0, then one tick per poll; the first poll stays under the stretched limit (so
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

    check("[CP5-T04] subprocess.TimeoutExpired is raised with the same .timeout as before this fix (base * "
          "stretch factor, the limit in force)",
          exc is not None and exc.timeout == expected_timeout, (getattr(exc, "timeout", None), expected_timeout))
    check("[CP5-T04] run_all.kill_tree is called exactly once, not twice -- the existing expiry-path call is "
          "not duplicated by the new exception handling",
          len(made_procs) == 1 and kill_calls == made_procs, (kill_calls, made_procs))


CP5_CASES = ("CP5-T01", "CP5-T02", "CP5-T03", "CP5-T04")
CP5_TESTS = [("CP5-T01", cp5_t01), ("CP5-T02", cp5_t02), ("CP5-T03", cp5_t03), ("CP5-T04", cp5_t04)]


# ---- unplanned extra coverage: an interrupt during a SUBSEQUENT poll's load/clock read (not just
# communicate()) still stops the child exactly once, before it propagates ------------------------------------
#
# CP5-T01..T04 above are frozen planned cases and are left untouched. CP5-T05/T06 below are new, unplanned
# cases added to close a narrower leak: the loop's own stretch_factor()/monotonic() calls -- which compute the
# next stretched limit -- sit outside the try/except that CP5-T01..T04 exercise (that one only guards
# proc.communicate()). An interrupt landing there would still escape without kill_tree(). Both cases force a
# real TimeoutExpired poll cycle first (via a hanging fake proc), then inject the exception on the *second*
# poll's load or clock read, proving the fix covers the whole loop body, not just its first iteration or its
# communicate() call.


def _loop_read_interrupt_case(cid, exc_cls, via):
    saved_load = set_fake_load("1", "8")
    kill_calls = []
    real_kill_tree = run_all.kill_tree
    real_stretch_factor = run_all.stretch_factor
    made_procs = []

    def spy_kill_tree(proc, *a, **kw):
        kill_calls.append(proc)
        return []

    def fake_popen(argv, **kw):
        p = _FakeHangingProc(argv)
        made_procs.append(p)
        return p

    monotonic_calls = [0]

    def fake_monotonic():
        monotonic_calls[0] += 1
        # call 1 is t0; call 2 is the first poll's remaining calc (communicate() then really raises
        # TimeoutExpired on a hanging proc); call 3 is the second poll's remaining calc -- where the injected
        # interrupt lands, when `via` is "monotonic". The clock never advances, so remaining stays positive
        # on every non-raising call.
        if via == "monotonic" and monotonic_calls[0] == 3:
            raise exc_cls()
        return 0.0

    stretch_calls = [0]

    def fake_stretch_factor(max_stretch):
        stretch_calls[0] += 1
        # call 1 is the first poll's load read (normal, lets the loop reach communicate() and really raise
        # TimeoutExpired); call 2 is the second poll's load read -- where the injected interrupt lands, when
        # `via` is "stretch_factor".
        if via == "stretch_factor" and stretch_calls[0] == 2:
            raise exc_cls()
        return real_stretch_factor(max_stretch)

    caught = False
    run_all.kill_tree = spy_kill_tree
    run_all.stretch_factor = fake_stretch_factor
    try:
        try:
            run_all.run_with_stretched_timeout(["bash", "-c", "true"], 5.0, cwd=None, env=None,
                                                popen=fake_popen, monotonic=fake_monotonic)
        except exc_cls:
            caught = True
    finally:
        run_all.kill_tree = real_kill_tree
        run_all.stretch_factor = real_stretch_factor
        restore_env(saved_load)

    check(f"[{cid}] the interrupt landed on the SECOND poll, after the first poll already made it as far as "
          f"communicate() (proving this exercises a later iteration, not just the first one)",
          (stretch_calls[0] >= 2 if via == "stretch_factor" else monotonic_calls[0] >= 3),
          (via, stretch_calls[0], monotonic_calls[0]))
    check(f"[{cid}] run_all.kill_tree is called exactly once, with the exact process object the interrupted "
          f"call started",
          len(made_procs) == 1 and kill_calls == made_procs, (kill_calls, made_procs))
    check(f"[{cid}] the {exc_cls.__name__} is not swallowed inside run_with_stretched_timeout(): it still "
          f"reaches the caller",
          caught is True, caught)


def cp5_t05():
    print("CP5-T05: a KeyboardInterrupt raised while re-reading the clock on a later poll (after a real "
          "TimeoutExpired cycle already ran) still stops the process tree exactly once before it propagates")
    _loop_read_interrupt_case("CP5-T05", KeyboardInterrupt, via="monotonic")


def cp5_t06():
    print("CP5-T06: a SystemExit raised while re-reading the load on a later poll (after a real TimeoutExpired "
          "cycle already ran) still stops the process tree exactly once before it propagates")
    _loop_read_interrupt_case("CP5-T06", SystemExit, via="stretch_factor")


CP5_CASES = CP5_CASES + ("CP5-T05", "CP5-T06")
CP5_TESTS = CP5_TESTS + [("CP5-T05", cp5_t05), ("CP5-T06", cp5_t06)]


def write_report():
    """Writes {"cases": [{"id","status","message"}]} to $RATCHET_TEST_REPORT when that env var is set, one
    entry per planned CP5-Txx case (a case whose check() never ran is reported as "error", never silently
    dropped)."""
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid in CP5_CASES:
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
    for cid, fn in CP5_TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as exc:  # a crashed case fails; it never takes the other cases down
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
