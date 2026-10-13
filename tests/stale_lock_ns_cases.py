"""
CP2 extra checks (CP2-X01 to CP2-X03), added after review rounds. They run as part of tests/test_stale_lock.py,
which imports this file (it is not named test_*.py so the full suite does not run its cases twice) just before it starts (the planned file's own lines are frozen, so its cases cannot be extended
in place) and uses its helpers and its case list.

CP2-X01: an agent's Linux sandbox has its own pid namespace but the same machine name, so a process id in a lock means
nothing to a checker in another namespace (or after a reboot). The lock text now carries the pid namespace and the boot
id, and a lock whose namespace or boot id differs, or is missing, is not judged gone (the timed rule).
CP2-X02: a live python process that is not running ratchet.py (python -c, python -m, another script) is not judged
gone; a live non-python process (a reused id) still is.
CP2-X03: a zombie is gone; an empty command line that is not a zombie, and a /proc that is another pid namespace's, are
not judged gone.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

import _tmp

if __name__ == "__main__":  # run on its own: python3 tests/stale_lock_ns_cases.py runs just the CP2-X cases
    import test_stale_lock  # its last lines import this file again, which adds the cases to its list
    test_stale_lock.TESTS[:] = [t for t in test_stale_lock.TESTS if t[0].startswith("CP2-X")]
    test_stale_lock.main()

_main = sys.modules.get("__main__")
base = _main if hasattr(_main, "TESTS") and hasattr(_main, "dead_owner") else __import__("test_stale_lock")
check, case = base.check, base.case


def cmdline_of(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read()
    except OSError:
        return b""


def state_of(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rpartition(b")")[2].split()[0].decode()
    except (OSError, IndexError):
        return None


def verdict(m, pid, needle, seconds=5.0):
    """_pid_is_ratchet(pid) once the child shows the arguments it was started with (between fork and exec its command
    line is still its parent's, and it can be empty for a moment); polls up to `seconds`."""
    deadline = time.monotonic() + seconds
    while needle not in cmdline_of(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return m._pid_is_ratchet(pid)


@case("CP2-X01")
def x01():
    """A lock from another pid namespace or another boot is respected; the same lock from this one is cleared."""
    if not base.LINUX:
        return base.windows_skip("CP2-X01")
    dead = base.dead_owner()
    real = dead["text"]
    check("[CP2-X01] setup: the real lock text records the pid namespace and the boot id",
          re.search(r"pidns=pid:\[\d+\]", real) is not None and re.search(r"boot=[0-9a-f-]{36}", real) is not None, real)
    check("[CP2-X01] ... and still has the owner's pid as a standalone number and the machine name verbatim",
          base.swap_word(real, dead["pid"], "0") != real and f"@{base.THIS_HOST}" in real, real)
    root = base.bare_root(real)
    why, took, _lk = base.acquire(root, 0.3)
    check("[CP2-X01] control: the same lock with the dead owner and this namespace and boot is cleared at once",
          why is None and took < 5, why)
    variants = {
        "another pid namespace": re.sub(r"pidns=\S+", "pidns=pid:[4026539999]", real),
        "another boot": re.sub(r"boot=\S+", "boot=00000000-0000-0000-0000-000000000000", real),
        "no pid namespace field": re.sub(r"\s+pidns=\S+", "", real),
        "no boot id field": re.sub(r"\s+boot=\S+", "", real),
    }
    for label, text in variants.items():
        check(f"[CP2-X01] setup: the {label} variant differs from the real text", text != real, text)
        r = base.bare_root(text)
        why2, _t, _l = base.acquire(r, 0.3)
        lock = r / ".ratchet" / "state.lock"
        check(f"[CP2-X01] a fresh lock with a dead pid but {label} is respected (busy), not cleared at once",
              "still changing this run" in str(why2) and lock.exists() and lock.read_text() == text, why2)
        r2 = base.bare_root(text, age=base.engine()._lock_stale_seconds(r) + 60)
        why3, _t3, lk3 = base.acquire(r2, 0.3)
        check(f"[CP2-X01] ... and the same lock past the timed limit is cleared by the timed rule", why3 is None, why3)
        lk3.release()


@case("CP2-X02")
def x02():
    """A python -c holder is respected; a live process of some other program on a reused id is still gone."""
    if not base.LINUX:
        return base.windows_skip("CP2-X02")
    dead = base.dead_owner()
    real = dead["text"]
    m = base.engine()
    env = os.environ.copy()
    holder = base.spawn([sys.executable, "-c", "import time; time.sleep(120)"], None, env)
    got = verdict(m, holder.pid, b"time.sleep(120)")
    check("[CP2-X02] a live python -c process is unknown (not gone)", got is None, got)
    text = base.swap_word(real, dead["pid"], holder.pid)
    root = base.bare_root(text)
    why, _t, _lk = base.acquire(root, 0.3)
    lock = root / ".ratchet" / "state.lock"
    check("[CP2-X02] a fresh lock held by a python -c process is respected (busy), not cleared at once",
          "still changing this run" in str(why) and lock.exists() and lock.read_text() == text, why)
    modrun = base.spawn([sys.executable, "-m", "http.server", "--bind", "127.0.0.1", "0"], None, env)
    got = verdict(m, modrun.pid, b"http.server")
    check("[CP2-X02] a live python -m process is unknown (not gone)", got is None, got)
    folder = _tmp.mkdtemp(prefix="ratchet-stale-x02-")
    script = os.path.join(folder, "other_tool.py")
    with open(script, "w") as f:
        f.write("import time\ntime.sleep(120)\n")
    other = base.spawn([sys.executable, script], None, env)
    got = verdict(m, other.pid, b"other_tool.py")
    check("[CP2-X02] a live python process running some other script is unknown (not gone)", got is None, got)
    sleeper = base.spawn(["sleep", "120"], None, env)
    got = verdict(m, sleeper.pid, b"sleep\x00120")
    check("[CP2-X02] a live non-python process on a reused id is still gone", got is False, got)


@case("CP2-X03")
def x03():
    """A zombie is gone; an empty command line that is not a zombie and another namespace's /proc are not judged."""
    if not base.LINUX:
        return base.windows_skip("CP2-X03")
    m = base.engine()
    z = base.spawn(["true"], None, os.environ.copy())  # not waited for: it stays a zombie
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and state_of(z.pid) != "Z":
        time.sleep(0.02)
    check("[CP2-X03] setup: the finished child is a zombie with an empty command line", state_of(z.pid) == "Z",
          state_of(z.pid))
    got = m._pid_is_ratchet(z.pid)
    check("[CP2-X03] a zombie is gone (False)", got is False, got)
    kthread = None
    for name in os.listdir("/proc"):
        if name.isdigit() and int(name) > 1 and state_of(int(name)) not in (None, "Z") and not cmdline_of(int(name)):
            kthread = int(name)
            break
    if kthread is None:
        print("  - [CP2-X03] no kernel thread to try an empty command line on; that check is skipped")
    else:
        got = m._pid_is_ratchet(kthread)
        check("[CP2-X03] a live process with an empty command line is unknown (not gone)", got is None, got)
    code = ("import importlib.util as u,sys;s=u.spec_from_file_location('r',sys.argv[1]);m=u.module_from_spec(s);"
            "s.loader.exec_module(m);print('verdict',m._pid_is_ratchet(2))")
    try:
        r = subprocess.run(["unshare", "-Urpf", sys.executable, "-c", code, str(base.ENGINE)], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is None or r.returncode != 0 or "verdict" not in r.stdout:
        print("  - [CP2-X03] no new pid namespace could be made here; the other-/proc check is skipped")
    else:
        check("[CP2-X03] where the mounted /proc is the host's, not this namespace's, no process is judged gone (None)",
              r.stdout.split("verdict", 1)[1].strip() == "None", r.stdout + r.stderr)
