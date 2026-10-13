#!/usr/bin/env python3
"""
CP1 fix-round tests: the summary line reports the limit that was really in force.   python3 tests/test_runner_load_fixes.py

Drives Runner.wait() from tests/run_all.py directly, with the load faked through RATCHET_FAKE_LOADAVG.
  - [CP1-X2] a suite that ends within one poll of passing --timeout, saved only by the stretch, still gets a line
  - [CP1-A3] a load drop after --timeout passed does not take back the stretched limit: the suite is stopped at the
             limit it was granted, and the line reports that limit (CP2 round 2: the limit in force never shrinks)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import run_all  # noqa: E402

PASS = FAIL = 0
FAKE_VARS = ("RATCHET_FAKE_LOADAVG", "RATCHET_FAKE_CPUS")


def set_fake_load(load, cpus):
    """Fake the machine load for this process; returns what to hand to restore_env()."""
    saved = {k: os.environ.get(k) for k in FAKE_VARS}
    os.environ.update(RATCHET_FAKE_LOADAVG=load, RATCHET_FAKE_CPUS=cpus)
    return saved


def restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"), flush=True)


class QuickEnd:
    """A stand-in for a suite process that ends 0.12s into its first wait(), past a 0.1s --timeout."""
    pid = 0

    def wait(self, timeout=None):
        time.sleep(0.12)
        return 0

    def poll(self):
        return 0


def x2_exit_right_after_timeout_is_reported():
    print("CP1-X2: a suite that ends within one poll of passing --timeout, saved by the stretch, gets a line")
    saved = set_fake_load("8", "4")
    try:
        runner = run_all.Runner(Path(tempfile.gettempdir()), 0.1, 10, "", 4.0)
        res = run_all.Result("test_alpha.py")
        runner.wait(QuickEnd(), res, time.monotonic())
    finally:
        restore_env(saved)
    check("[CP1-X2] it passed", res.exit_code == 0 and not res.timed_out, (res.exit_code, res.timed_out))
    check("[CP1-X2] the line says stretched 2.0x with both limits", "stretched 2.0x (0s to 0s)" in res.stretch
          and "load 8.0 on 4 CPUs" in res.stretch, res.stretch)


def a3_falling_load_keeps_the_granted_limit():
    print("CP1-A3: a load drop after --timeout passed keeps the stretched limit, and the line reports that limit")
    # timeout 1s at 2x: the stretched deadline is 2s. The load falls at 1.5s, half a second after --timeout passed
    # and half a second before the stretched deadline: the suite is still given its 2s, and stopped only then.
    reading = Path(_tmp.mkdtemp(prefix="rt-fix-")) / "load.txt"
    reading.write_text("8\n")
    saved = set_fake_load(f"@{reading}", "4")

    def drop():
        time.sleep(1.5)
        tmp = reading.with_name("load.tmp")
        tmp.write_text("1\n")
        os.replace(tmp, reading)

    try:
        threading.Thread(target=drop, daemon=True).start()
        runner = run_all.Runner(Path(tempfile.gettempdir()), 1.0, 10, "", 4.0)
        res = run_all.Result("test_beta.py")
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        t0 = time.monotonic()
        try:
            runner.wait(proc, res, t0)
        finally:
            run_all.kill_tree(proc)
        took = time.monotonic() - t0
    finally:
        restore_env(saved)
    check("[CP1-A3] it was stopped as timed out", res.timed_out, res.exit_code)
    check("[CP1-A3] it was stopped at the granted 2s, not cut at 1s when the load fell", 1.9 <= took < 2.6, took)
    check("[CP1-A3] the line reports the limit that was applied: stretched 2.0x (1s to 2s) at load 8",
          "stretched 2.0x (1s to 2s)" in res.stretch and "load 8.0 on 4 CPUs" in res.stretch, res.stretch)


if __name__ == "__main__":
    x2_exit_right_after_timeout_is_reported()
    a3_falling_load_keeps_the_granted_limit()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
