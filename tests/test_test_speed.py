#!/usr/bin/env python3
"""
Lane T guards: required waits must not spin on a fixed 0.25s poll when stretch cannot grow,
and stop-on-edit / timeout outcomes stay the same.
    python3 tests/test_test_speed.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import trust_helpers as th  # noqa: E402

ENGINE = th.ENGINE
PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(T-T\d+)\]")


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


def load_engine():
    spec = importlib.util.spec_from_file_location("ratchet_engine_tspeed", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class CountingStretch:
    """Wraps m._stretch_factor and counts calls."""

    def __init__(self, m):
        self.m = m
        self.calls = 0
        self._real = m._stretch_factor

    def __call__(self, max_stretch):
        self.calls += 1
        return self._real(max_stretch)

    def install(self):
        self.m._stretch_factor = self

    def restore(self):
        self.m._stretch_factor = self._real


def sleep_proc(seconds: float):
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def t01_no_stretch_spin_while_hanging():
    """With max_stretch=1, a ~1.2s hang must not call _stretch_factor every RUN_POLL tick."""
    print("T-T01: max_stretch=1 hang does not poll stretch every 0.25s")
    m = load_engine()
    ctr = CountingStretch(m)
    ctr.install()
    try:
        proc = sleep_proc(1.2)
        t0 = time.monotonic()
        timed_out, limit, note, changed = m._wait_stretched(proc, timeout=60.0, max_stretch=1.0, watch=None)
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5)
    finally:
        ctr.restore()
    check("[T-T01] wait returned without timeout", timed_out is False, (timed_out, note))
    check("[T-T01] no watch changes", changed == [], changed)
    check("[T-T01] wall time is about the sleep (~1.2s), not the full timeout",
          0.9 <= elapsed <= 3.0, elapsed)
    # Current engine calls _stretch_factor every ~0.25s → ~4–6 calls in 1.2s.
    # After T1: at most 1 call (or 0 if stretch is skipped entirely when max_stretch<=1).
    check("[T-T01] stretch was not polled every RUN_POLL tick (≤2 calls for a ~1.2s hang)",
          ctr.calls <= 2, f"calls={ctr.calls} RUN_POLL={getattr(m, 'RUN_POLL_SECONDS', '?')}")


def t02_watch_every_does_not_force_stretch_spin():
    print("T-T02: watch.every=30 with max_stretch=1 does not force 0.25s stretch spin")
    m = load_engine()
    ctr = CountingStretch(m)
    ctr.install()
    # Minimal watch stand-in: never sees a change; every=30 so next look is far away.
    watch = SimpleNamespace(every=30.0, found_at_end=False, changed=lambda final=False: [])
    try:
        proc = sleep_proc(1.2)
        timed_out, limit, note, changed = m._wait_stretched(proc, timeout=60.0, max_stretch=1.0, watch=watch)
        proc.wait(timeout=5)
    finally:
        ctr.restore()
    check("[T-T02] wait returned without timeout", timed_out is False, (timed_out, note))
    check("[T-T02] watch reported no changes", changed == [], changed)
    check("[T-T02] stretch was not polled every RUN_POLL tick (≤2 calls)",
          ctr.calls <= 2, f"calls={ctr.calls}")


def t03_timeout_still_kills():
    print("T-T03: timeout still kills when the deadline passes (max_stretch=1)")
    m = load_engine()
    proc = sleep_proc(30.0)
    t0 = time.monotonic()
    timed_out, limit, note, changed = m._wait_stretched(proc, timeout=0.4, max_stretch=1.0, watch=None)
    elapsed = time.monotonic() - t0
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        m._kill_group(proc)
    check("[T-T03] timed_out is True", timed_out is True, timed_out)
    check("[T-T03] finished near the timeout (not the 30s sleep)", 0.3 <= elapsed <= 2.5, elapsed)
    check("[T-T03] process group is gone", proc.poll() is not None, proc.poll())


def t04_stop_on_edit_still_fires():
    """Edit under a watched run still stops (product invariant). Uses a real throwaway git project."""
    print("T-T04: stop-on-edit still fires with a real _Watch")
    m = load_engine()
    parent = Path(_tmp.mkdtemp(prefix="ratchet-tspeed-watch-"))
    d = parent / "app"
    d.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True, capture_output=True)
    (d / "README").write_text("x\n")
    (d / ".ratchet").mkdir()
    (d / ".ratchet" / "config.json").write_text("{}\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-m", "init"],
                   cwd=d, check=True, capture_output=True)

    ctx = m.Ctx(d)
    fp = m.fingerprint(d)
    watch = m._Watch(ctx, fp, every=0.15, again="behavior CP1")
    proc = sleep_proc(5.0)
    time.sleep(0.05)
    (d / "README").write_text("edited\n")
    timed_out, limit, note, changed = m._wait_stretched(proc, timeout=10.0, max_stretch=1.0, watch=watch)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        m._kill_group(proc)
    check("[T-T04] not a timeout (stop-on-edit path)", timed_out is False, (timed_out, note, changed))
    check("[T-T04] watch reported a change", bool(changed), changed)
    check("[T-T04] README is among the changed files (or listed)",
          any("README" in str(x) for x in changed), changed)


TESTS = [
    ("T-T01", t01_no_stretch_spin_while_hanging),
    ("T-T02", t02_watch_every_does_not_force_stretch_spin),
    ("T-T03", t03_timeout_still_kills),
    ("T-T04", t04_stop_on_edit_still_fires),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid, _fn in TESTS:
        ok = CASE_STATUS.get(cid, True)
        cases.append({"id": cid, "status": "passed" if ok else "failed",
                      "message": "; ".join(CASE_MESSAGES.get(cid, []))})
    Path(path).write_text(json.dumps({"cases": cases}, indent=2) + "\n")


def main():
    for cid, fn in TESTS:
        try:
            fn()
        except Exception:
            check(f"[{cid}] case raised", False, traceback.format_exc()[-500:])
    print(f"\n{PASS} passed, {FAIL} failed")
    write_report()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
