#!/usr/bin/env python3
"""
CP7 fix-round tests: a failed `git worktree add` leaves nothing behind.   python3 tests/test_lanes_start_extra.py

A project's post-checkout hook that exits 1 makes git create the branch and the worktree and then exit non-zero.
lanes-start must refuse plainly and remove what that attempt made (branch, worktree, enrollment, brief, request record).
"""
from __future__ import annotations

import argparse
import json
import contextlib
import io
import os
import signal
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_lanes_start as t  # noqa: E402

FAILED = 0


def check(name, cond, detail=""):
    global FAILED
    FAILED += 0 if cond else 1
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def main():
    w = t.build()
    hook = w.main / ".git/hooks/post-checkout"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    before = t.state_of(w)
    r = w.start(js=True)
    low = t.low(r)
    check("[CP7-X01] lanes-start is refused with a plain message naming the hook", t.known(r) and r.returncode != 0
          and "hook" in low and "traceback" not in low, t.said(r)[:300])
    after = t.state_of(w)
    d = t.diff_keys(before, after)
    check("[CP7-X01] no branch, worktree, folder, enrollment, request record or file in main changed", not d, d)
    check("[CP7-X01] only the main worktree is listed", len(t.worktrees(w)) == 1, t.worktrees(w))
    check("[CP7-X01] no lane branch is left", t.gout(w.main, "for-each-ref", "refs/heads").count("\n") == 1,
          t.gout(w.main, "for-each-ref", "refs/heads"))
    check("[CP7-X01] no brief folder or request record is left", not (w.main / ".ratchet/requests").exists()
          or not any((w.main / ".ratchet/requests").iterdir()), "")
    subfolder_project()
    lock_cases()
    write_fails()
    sys.exit(1 if FAILED else 0)


def subfolder_project():
    """A Ratchet project below the top of its git repository is refused, with nothing created."""
    w = t.build()
    top = w.main.parent / "repo-top"
    sub = top / "apps" / "service"
    t.th.git(w.base, "init", "-q", "-b", "main", str(top))
    for c in (["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        t.th.git(top, *c)
    import shutil
    shutil.copytree(w.main / ".ratchet", sub / ".ratchet")
    (sub / "a.txt").write_text("x\n")
    t.th.git(top, "add", "-A")
    t.th.git(top, "commit", "-qm", "init")
    w.main = sub
    before = {"heads": t.gout(top, "for-each-ref", "refs/heads"), "wt": t.gout(top, "worktree", "list", "--porcelain"),
              "base": sorted(p.name for p in w.base.iterdir()), "enrolled": w.enrolled_bytes()}
    r = w.start(js=True)
    low = t.low(r)
    check("[CP7-X03] a project in a subfolder is refused, naming the top-of-repository limit",
          t.known(r) and r.returncode != 0 and "top of its git repository" in low and "by hand" in low, t.said(r)[:300])
    after = {"heads": t.gout(top, "for-each-ref", "refs/heads"), "wt": t.gout(top, "worktree", "list", "--porcelain"),
             "base": sorted(p.name for p in w.base.iterdir()), "enrolled": w.enrolled_bytes()}
    check("[CP7-X03] no branch, worktree, folder or enrollment was created", before == after, (before, after))
    check("[CP7-X03] no brief folder or request record", not (sub / ".ratchet/requests").exists(), "")


HOLDER = """
import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("eng", sys.argv[1])
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)
main = Path(sys.argv[2])
lk = eng._IntegrateLock(main, main / ".git", "integrate somelane")
print("ready" if lk.acquire() is None else "failed", flush=True)
time.sleep(120)
"""


def hold_lock(w):
    p = subprocess.Popen([sys.executable, "-c", HOLDER, str(t.ENGINE), str(w.main)], stdout=subprocess.PIPE, text=True,
                         env=w.env)
    return p, p.stdout.readline().strip()


def lock_cases():
    """lanes-start shares integrate's per-repository lock: a live holder refuses it, a dead one is cleared."""
    w = t.build()
    before = t.state_of(w)
    p, said = hold_lock(w)
    try:
        check("[CP7-X04] fixture: a live process holds the lock", said == "ready", said)
        r = w.start(lanes=["a=x"])
        check("[CP7-X04] a live holder: refused as busy, nothing changed", t.known(r) and r.returncode != 0
              and "busy" in t.low(r) and not t.diff_keys(before, t.state_of(w)), t.said(r)[:300])
        check("[CP7-X04] no branch a, no folder, no request record",
              t.gitx(w.main, "rev-parse", "--verify", "-q", "a").returncode != 0
              and not (w.main.parent / f"{w.main.name}-a").exists()
              and not (w.main / ".ratchet/requests").exists(), "")
    finally:
        p.kill()
        p.wait()
    # (c) the holder was killed: its lock file is left behind with a dead pid, and lanes-start clears it and goes on
    check("[CP7-X05] fixture: the killed holder left its lock file", (w.main / ".git/ratchet-integrate.lock").exists(), "")
    r = w.start(lanes=["a=x"], js=True)
    check("[CP7-X05] a lock left by a dead process: lanes-start goes ahead", t.known(r) and r.returncode == 0, t.said(r)[:300])
    # (b) a second call for the same lane name is refused at preflight and leaves the first lane alone
    enrolled = w.enrolled_bytes()
    wts = t.worktrees(w)
    head = t.gout(w.main, "rev-parse", "a")
    r2 = w.start(request="Another", lanes=["a=y"])
    check("[CP7-X06] the same lane name again: refused as already existing", t.known(r2) and r2.returncode != 0
          and "already exists" in t.low(r2), t.said(r2)[:300])
    check("[CP7-X06] the first lane's branch, worktree and enrollment are untouched",
          t.worktrees(w) == wts and t.gout(w.main, "rev-parse", "a") == head and w.enrolled_bytes() == enrolled
          and "a" in wts, "")
    check("[CP7-X06] the lock is released after each call", not (w.main / ".git/ratchet-integrate.lock").exists(), "")


def _empty(ep):
    """True when enrolled.json is absent or holds a record with no lane entries."""
    return not ep.exists() or json.loads(ep.read_text() or "{}") == {}


def write_fails():
    """Structural: whichever record write fails (the Nth _write_record call), the run is rolled back completely."""
    eng = t.th.cp5y_load_engine()
    real = eng._write_record
    n = 0
    while True:
        n += 1
        w = t.build()
        ep = w.rh() / "enrolled.json"
        before = t.state_of(w)
        status = t.gout(w.main, "status", "--porcelain")
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == n:
                raise OSError("disk full (injected)")
            return real(*a, **kw)
        eng._write_record = flaky
        eng.require_director_session = lambda what: None
        args = argparse.Namespace(request=t.REQUEST, lane=list(t.TWO), name=None, json=True)
        old_env, old_cwd = dict(os.environ), os.getcwd()
        os.environ.update(w.env)
        os.chdir(w.main)
        failed = False
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    eng.cmd_lanes_start(args)
                except (OSError, SystemExit):
                    failed = True
        finally:
            eng._write_record = real
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
        if not failed:
            check(f"[CP7-X02] a run with no failing write succeeds (checked {n - 1} failing writes)", n > 3, n)
            return
        after = t.state_of(w)
        absent = before["enrolled"] is None   # then an empty record (no lane entries) may be left, never an entry
        d = [k for k in t.diff_keys(before, after) if not (k == "enrolled" and absent and _empty(ep))]
        rd = w.main / ".ratchet/requests"
        check(f"[CP7-X02] write #{n} fails: branches, worktrees, folders, enrolled.json, main files all as before", not d, d)
        check(f"[CP7-X02] write #{n} fails: no request record or brief folder", not rd.exists() or not list(rd.iterdir()), "")
        check(f"[CP7-X02] write #{n} fails: main's git status is unchanged",
              t.gout(w.main, "status", "--porcelain") == status, "")
        check(f"[CP7-X02] write #{n} fails: enrolled.json is byte for byte as before (or, if it did not exist, "
              "absent or an empty record)", (ep.read_bytes() if ep.exists() else None) == before["enrolled"]
              or (absent and _empty(ep)), "")
        if n > 40:
            check("[CP7-X02] the loop ends", False, n)
            return


if __name__ == "__main__":
    main()
