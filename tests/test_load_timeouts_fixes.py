#!/usr/bin/env python3
"""
CP2 fix-round tests: a stretched limit never shrinks, and a stopped ratchet.py stops its command.
    python3 tests/test_load_timeouts_fixes.py

Black-box like tests/test_load_timeouts.py (whose helpers it reuses): every case drives the real ratchet.py in a
throwaway git project with the load faked through RATCHET_FAKE_LOADAVG / RATCHET_FAKE_CPUS, set only in the
engine's environment and never in this process's.
  - [CP2-X01] a load drop after the command passed test_timeout_sec does not take back the time already granted
              (the mirror of CP2-T06): the command still passes and the log carries the stretch note
  - [CP2-X02] a SIGTERM to ratchet.py while a step's command runs kills the command's whole process group too
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_load_timeouts as lt  # noqa: E402
import trust_helpers as tt  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


def x01_load_drop_keeps_the_granted_limit():
    print("CP2-X01: a load drop after the configured limit passed does not shorten the limit in force")
    w = lt.work("drop")
    started = w / "started"
    reading = w / "reading.txt"
    reading.write_text("12")  # on 4 CPUs: factor 3, limit 2s -> 6s
    d, env = lt.staged(lt.sleep_script(w, 2.8, started=started), 2)
    r0 = tt.rx(d, env, "red", "CP1")
    assert r0.returncode == 0, r0.stderr + r0.stdout
    lt.implement(d, env)
    stop = threading.Event()

    def drop():
        t_end = time.monotonic() + 60
        while not stop.is_set() and time.monotonic() < t_end:
            if started.exists():
                time.sleep(2.2)  # past the configured 2s, before the command ends at 2.8s
                tmp = w / "reading.tmp"
                tmp.write_text("1")
                os.replace(tmp, reading)
                return
            time.sleep(0.02)

    th = threading.Thread(target=drop, daemon=True)
    th.start()
    r = lt.rx(d, lt.fake(env, f"@{reading}", "4"), "behavior", "CP1", cap=90)
    stop.set()
    th.join(5)
    log = lt.read(lt.evidence(d) / "behavior-01.log")
    line = lt.stretch_line(log)
    check("[CP2-X01] behavior exits 0 (the granted limit stayed in force after the load fell)",
          r.rc == 0, (r.rc, r.text[-300:]))
    check("[CP2-X01] the behavior gate is passed", lt.gate(d).get("status") == "passed", lt.gate(d))
    check("[CP2-X01] the log does not contain TIMEOUT", "TIMEOUT" not in log, log[:300])
    check("[CP2-X01] the log says the limit was stretched", bool(line), log[:300])
    check("[CP2-X01] the note names the highest load 12, not the later reading", lt.has_num(line, 12), line)
    check("[CP2-X01] the note names 2s and 6s (3.0x)", lt.has_num(line, 2) and lt.has_num(line, 6), line)


def x02_sigterm_to_ratchet_stops_the_command_tree():
    print("CP2-X02: a SIGTERM to ratchet.py while its command runs kills the command's whole process group")
    w = lt.work("term")
    d, env = lt.staged(lt.hang_script(w), 30)
    (d / "src/f1.js").write_text("export const f = () => 1;\n")  # so the script reaches its hang
    proc = subprocess.Popen([sys.executable, str(tt.ENGINE), "red", "CP1"], cwd=str(d), env=lt.idle(env),
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    script = child = 0
    t_end = time.monotonic() + 30
    while time.monotonic() < t_end and not (script and child):
        try:
            script = int(lt.read(w / "script.pid").strip() or 0)
            child = int(lt.read(w / "child.pid").strip() or 0)
        except ValueError:
            pass
        time.sleep(0.05)
    time.sleep(0.3)
    check("[CP2-X02] the hang script started and recorded its pids", script > 0 and child > 0, (script, child))
    check("[CP2-X02] both are alive before ratchet.py is stopped",
          lt.pid_alive(script) and lt.pid_alive(child), (script, child))
    os.kill(proc.pid, signal.SIGTERM)  # ratchet.py alone, not its group
    try:
        out, err = proc.communicate(timeout=30)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        rc = None
    time.sleep(0.5)
    script_alive, child_alive = lt.pid_alive(script), lt.pid_alive(child)
    lt.reap(w)
    check("[CP2-X02] ratchet.py exited on the SIGTERM", rc is not None and rc != 0, (rc, (out or "")[-200:], (err or "")[-200:]))
    check("[CP2-X02] the hang script is no longer alive", script > 0 and not script_alive, script)
    check("[CP2-X02] its background child is no longer alive", child > 0 and not child_alive, child)


def start_hang_red(w, env, key="sig", preexec_fn=None):
    """ratchet.py `red` running the hang script, as a Popen; returns (proc, script pid, child pid, project)."""
    d, env = lt.staged(lt.hang_script(w), 30)
    (d / "src/f1.js").write_text("export const f = () => 1;\n")  # so the script reaches its hang
    proc = subprocess.Popen([sys.executable, str(tt.ENGINE), "red", "CP1"], cwd=str(d), env=lt.idle(env),
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            start_new_session=True, preexec_fn=preexec_fn)
    script = child = 0
    t_end = time.monotonic() + 30
    while time.monotonic() < t_end and not (script and child):
        try:
            script = int(lt.read(w / "script.pid").strip() or 0)
            child = int(lt.read(w / "child.pid").strip() or 0)
        except ValueError:
            pass
        time.sleep(0.05)
    time.sleep(0.3)
    return proc, script, child, d


def x03_sigint_and_sighup_stop_the_command_tree():
    print("CP2-X03: a SIGINT or SIGHUP to ratchet.py while its command runs kills the command's whole process group")
    for name in ("SIGINT", "SIGHUP"):
        w = lt.work("sig")
        sig = getattr(signal, name)
        # the child starts with the signal at its default, whatever this process inherited (a nohup'd parent
        # ignores SIGHUP, and ratchet.py would rightly keep ignoring it): each case is independent of the parent
        proc, script, child, _ = start_hang_red(w, None, preexec_fn=lambda: signal.signal(sig, signal.SIG_DFL))
        check(f"[CP2-X03] {name}: the hang script started and recorded its pids", script > 0 and child > 0,
              (script, child))
        check(f"[CP2-X03] {name}: both are alive before ratchet.py is stopped",
              lt.pid_alive(script) and lt.pid_alive(child), (script, child))
        os.kill(proc.pid, sig)  # ratchet.py alone, not its group
        try:
            out, err = proc.communicate(timeout=30)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            rc = None
        time.sleep(0.5)
        script_alive, child_alive = lt.pid_alive(script), lt.pid_alive(child)
        lt.reap(w)
        check(f"[CP2-X03] {name}: ratchet.py exited on the signal", rc is not None and rc != 0,
              (rc, (out or "")[-200:], (err or "")[-200:]))
        check(f"[CP2-X03] {name}: the hang script is no longer alive", script > 0 and not script_alive, script)
        check(f"[CP2-X03] {name}: its background child is no longer alive", child > 0 and not child_alive, child)


def x04_ignored_sighup_stays_ignored():
    print("CP2-X04: a SIGHUP that ratchet.py already ignores (nohup) is still ignored: the command runs on")
    w = lt.work("hup")
    started = w / "started"
    d, env = lt.staged(lt.sleep_script(w, 2.5, started=started), 30)
    r0 = tt.rx(d, env, "red", "CP1")
    assert r0.returncode == 0, r0.stderr + r0.stdout
    lt.implement(d, env)
    t0 = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(tt.ENGINE), "behavior", "CP1"], cwd=str(d), env=lt.idle(env),
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            start_new_session=True, preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    t_end = time.monotonic() + 30
    while time.monotonic() < t_end and not started.exists():
        time.sleep(0.02)
    check("[CP2-X04] the step's command started", started.exists())
    time.sleep(0.3)
    os.kill(proc.pid, signal.SIGHUP)
    try:
        out, err = proc.communicate(timeout=60)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        rc = None
    took = time.monotonic() - t0
    log = lt.read(lt.evidence(d) / "behavior-01.log")
    check("[CP2-X04] ratchet.py finished normally (exit 0) after the ignored SIGHUP", rc == 0,
          (rc, (out or "")[-200:], (err or "")[-200:]))
    check("[CP2-X04] the behavior gate passed", lt.gate(d).get("status") == "passed", lt.gate(d))
    check("[CP2-X04] the command ran to its end (the run took at least 2.5s)", took >= 2.5, took)
    check("[CP2-X04] the log shows exit 0 and no TIMEOUT", "# exit=0 " in log and "TIMEOUT" not in log, log[:200])


def x05_load_stretch_max_is_locked_after_approval():
    print("CP2-X05: load_stretch_max is a plan rule: changing it after approval stops the gates")
    w = lt.work("lock")
    d, env = lt.staged(lt.sleep_script(w, 0), 30)
    r = tt.rx(d, env, "config", "--set", "load_stretch_max=2")
    check("[CP2-X05] config --set itself is accepted", r.returncode == 0, r.stderr + r.stdout)
    r = tt.rx(d, env, "red", "CP1")
    check("[CP2-X05] red is refused after the change", r.returncode != 0, r.stdout[-300:])
    check("[CP2-X05] the refusal names load_stretch_max and the approval",
          "load_stretch_max" in r.stderr and "approved" in r.stderr, r.stderr[-400:])


def main():
    global FAIL
    for fn in (x01_load_drop_keeps_the_granted_limit, x02_sigterm_to_ratchet_stops_the_command_tree,
               x03_sigint_and_sighup_stop_the_command_tree, x04_ignored_sighup_stays_ignored,
               x05_load_stretch_max_is_locked_after_approval):
        try:
            fn()
        except Exception as e:  # a crashed case fails; it never takes the other case down
            FAIL += 1
            print(f"  ✗ CRASHED: {type(e).__name__}: {e}")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
