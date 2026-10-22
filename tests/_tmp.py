"""
Shared temp-folder helper for every tests/test_*.py suite. Also exports `set_fake_load`/`restore_env`, the
shared RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS helpers every suite that simulates machine load uses.

Every temp folder a suite needs comes from `mkdtemp()` here, never from tempfile.mkdtemp/mkstemp
directly (tests/test_cleanup.py scans for that). Each folder is removed:
  - when a `with _tmp.mkdtemp(...) as d:` block is left (normally, by sys.exit or by an exception),
  - or, for folders never used as a context manager, when the process exits (atexit), which also
    covers a failing check followed by sys.exit(1), a direct sys.exit() and an uncaught exception.
A SIGTERM sent to the suite is turned into a normal exit so the same cleanup runs.

A background process (a server, a slow child) can be tied to a folder with `d.track(proc)`; it is
terminated before the folder is removed. `d.on_cleanup(fn)` runs `fn()` at that same moment, for
servers that are stopped by a command rather than by killing a known process. Files a suite writes
next to the folder and names after it ("<folder>-transcript.jsonl") are removed with the folder.

Programs the suite starts get TMPDIR pointed at one folder the suite owns (SESSION), so temp
folders they create and leave behind are removed with it.

The object returned by `mkdtemp()` is a `str` holding the folder's path, so it drops in wherever the
old tempfile.mkdtemp() result (a str) or a TemporaryDirectory's `.name` was used. It also has
`.name`, `.path` and `.cleanup()` like tempfile.TemporaryDirectory.

Stdlib only.
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# CP1: every plan needs a top-level 'objective' (who it is for, what they want, what done means, what is out
# of scope). Test fixtures that write checkpoints.json directly (not through the real planner) use this minimal
# one, shared here so every suite's setup data stays in one place.
OBJECTIVE_MIN = {
    "users": "the people who use this test project",
    "goal": "get the checkpoint under test built",
    "done_means": ["the checkpoint's own done_when criteria are met"],
    "out_of_scope": ["anything not listed in the checkpoint's plan"],
}

# CP1: a 'high'-risk checkpoint needs risk_reason naming who is harmed and how, or plan-submit/plan-extend refuse
# it. Fixtures across suites that build a high-risk checkpoint (not testing that refusal itself) use this one
# shared reason so they don't all have to say it separately.
RISK_REASON_MIN = ("a wrong check here would let a corner-cutting agent ship broken or unsafe behavior straight "
                   "past review, hurting whoever uses the resulting feature")

# Load-aware-timeout suites fake the machine load through these two env vars (read_load() in run_all.py), so
# more than one suite needs to set and restore them; shared here so each one doesn't carry its own copy.
FAKE_LOAD_VARS = ("RATCHET_FAKE_LOADAVG", "RATCHET_FAKE_CPUS")


def set_fake_load(load, cpus):
    """Fakes RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS for this process (either may be None to leave that one
    variable unset); returns what to hand to restore_env()."""
    saved = {k: os.environ.get(k) for k in FAKE_LOAD_VARS}
    if load is None:
        os.environ.pop("RATCHET_FAKE_LOADAVG", None)
    else:
        os.environ["RATCHET_FAKE_LOADAVG"] = str(load)
    if cpus is None:
        os.environ.pop("RATCHET_FAKE_CPUS", None)
    else:
        os.environ["RATCHET_FAKE_CPUS"] = str(cpus)
    return saved


def restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


_LOCK = threading.RLock()
_LIVE: list = []          # TmpDir objects not cleaned up yet, oldest first
_PROCS: list = []         # processes tracked without a folder (see track())
_OWNER = os.getpid()      # a forked child must never clean up its parent's folders


def _stop(proc) -> None:
    """Terminate a Popen-like object if it is still running; kill it if it will not stop."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except Exception:  # cleanup must never raise over the suite's own result
        pass
    for stream in (getattr(proc, "stdin", None), getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass


def _make_writable(p: str) -> None:
    try:
        st = os.lstat(p)
        if not stat.S_ISLNK(st.st_mode):
            os.chmod(p, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
    except OSError:
        pass


def _remove_tree(path: str) -> None:
    """shutil.rmtree that also gets through folders a test made read-only or unreadable."""
    def retry(_func, p, _exc):
        _make_writable(os.path.dirname(p))
        _make_writable(p)
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.unlink(p)
        except OSError:
            pass

    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)
    except OSError:
        pass
    if os.path.lexists(path):
        _make_writable(path)
        shutil.rmtree(path, ignore_errors=True)


class TmpDir(str):
    """A temp folder's path (as a str) that removes itself, and anything tracked on it, on cleanup."""

    def __new__(cls, path: str):
        obj = super().__new__(cls, path)
        obj._procs = []
        obj._callbacks = []
        obj._done = False
        obj._pid = os.getpid()
        return obj

    @property
    def name(self) -> str:
        return str.__str__(self)

    @property
    def path(self) -> Path:
        return Path(str.__str__(self))

    def track(self, proc):
        """Terminate `proc` (anything with poll/terminate/wait) when this folder is cleaned up."""
        with _LOCK:
            self._procs.append(proc)
        return proc

    def on_cleanup(self, fn):
        """Call `fn()` when this folder is cleaned up, before the folder is removed."""
        with _LOCK:
            self._callbacks.append(fn)
        return fn

    def cleanup(self) -> None:
        with _LOCK:
            if self._done or os.getpid() != self._pid:
                return
            self._done = True
            procs, self._procs = self._procs[::-1], []
            callbacks, self._callbacks = self._callbacks[::-1], []
            try:
                _LIVE.remove(self)
            except ValueError:
                pass
        for proc in procs:
            _stop(proc)
        for fn in callbacks:
            try:
                fn()
            except Exception:
                pass
        own = str.__str__(self)
        _remove_tree(own)
        # Files a suite writes next to its folder, named after it ("<folder>-transcript.jsonl", "<folder>-cap"),
        # belong to the folder too. The folder's random name makes these siblings unique to it.
        parent, base = os.path.split(own)
        try:
            siblings = [e for e in os.listdir(parent) if e.startswith(base + "-") or e.startswith(base + ".")]
        except OSError:
            siblings = []
        for e in siblings:
            _remove_tree(os.path.join(parent, e))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()
        return False

    def __reduce__(self):  # copies and pickles are plain paths, never a second owner of the folder
        return (str, (str.__str__(self),))


def mkdtemp(suffix: str | None = None, prefix: str | None = None, dir=None) -> TmpDir:
    """Like tempfile.mkdtemp (honours $TMPDIR), but the folder is removed on with-exit or at process exit."""
    d = TmpDir(tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir))
    with _LOCK:
        _LIVE.append(d)
    return d


def adopt(path) -> TmpDir:
    """Take ownership of a folder some other code made (say the engine, loaded in this process, returned a
    throwaway folder to its caller), so it is removed like one made by mkdtemp()."""
    d = TmpDir(os.fspath(path))
    with _LOCK:
        _LIVE.append(d)
    return d


def track(proc):
    """Terminate `proc` at process exit, for a background process not tied to one folder."""
    with _LOCK:
        _PROCS.append(proc)
    return proc


def cleanup_all() -> None:
    """Stop every tracked process and remove every folder still alive, newest first."""
    if os.getpid() != _OWNER:
        return
    with _LOCK:
        procs, _PROCS[:] = _PROCS[::-1], []
        live = _LIVE[::-1]
    for proc in procs:
        _stop(proc)
    for d in live:
        d.cleanup()


atexit.register(cleanup_all)

# Programs a suite runs (the engine, review scripts, probes) make temp folders of their own and do not always
# remove them. Every child inherits TMPDIR, so point it at one folder this suite owns: whatever the children
# leave there goes when the suite ends. The suite's own folders still use the original location, which
# tempfile has already cached by the time TMPDIR changes.
SESSION = mkdtemp(prefix="ratchet-tests-")
os.environ["TMPDIR"] = str.__str__(SESSION)


def _on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


try:  # only take over SIGTERM when nobody else did, and only from the main thread
    if threading.current_thread() is threading.main_thread() and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
        signal.signal(signal.SIGTERM, _on_sigterm)
except (ValueError, OSError, AttributeError):
    pass
