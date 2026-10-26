#!/usr/bin/env python3
"""
records.py -- tamper-evidence and record-locking for ratchet.py's engine.

_chain_path/_read_record/_write_record/_append_record/_note_unreadable/_BreakerLock/_ChainLock/_RecordLock/
_record_lock_path/update_record/_chain_record/_record_chain_now/record_chain/append_recorded/chain_problem/
verify_chain, moved verbatim out of ratchet.py (see tests/test_modularize_cp4.py, CP4-01) so this one
responsibility -- every shared record's read-modify-write lock, and the hash chain that catches a file changed
outside ratchet.py -- can be read and changed without reading or risking the rest of the 21,000+ line engine.
ratchet.py imports these sixteen names explicitly (`from records import ...`); it never does a star import, and
keeps no `def`/`class` of its own for any of them. KEEP (update_record's "leave it as it is" sentinel) and
ACCEPT_STATE_CMD (chain_problem's recovery-command text) move here too, since nothing outside these sixteen
functions used either one; ratchet.py imports both by name as well, rather than keeping a second `object()` (an
`is KEEP` check across the two modules must compare the SAME object) or a hand-synced copy of the string.

_NOTED_UNREADABLE and _CHAIN_REBUILD are the two pre-existing module-level registries these functions already
used before the move (CP4-03 checks that no new one is added to make the move easier): _NOTED_UNREADABLE is
used only inside _note_unreadable, unchanged; _CHAIN_REBUILD is used only inside _record_chain_now, but it is
also SET from outside these sixteen functions, by cmd_accept_state (which stays in ratchet.py -- a person
confirming an override at their own terminal is not part of this move). Since this module is now its one real
home (the bare name `_CHAIN_REBUILD` inside _record_chain_now's body resolves against THIS module's globals,
never ratchet.py's), cmd_accept_state sets it through the `records` module object it already imports
(`records._CHAIN_REBUILD = True`) instead of a local `global` assignment -- the single-source-of-truth shape
CP4-03 expects, not a second, unsynced copy.

Three names these functions call that stay in ratchet.py for now (ratchet_home, require_hook_env -- CP6's
run_records.py owns them) are reached lazily, by file path, at call time only, through _ratchet_module() below
-- never a disk read or an AST parse of ratchet.py's text at THIS module's import time, and never a module-level
`import ratchet` that would create an import cycle (ratchet.py already imports this module). This is the exact
shape engine_utils.py's own _read_record/_write_record/_note_unreadable wrappers use for their own forward
dependency on this module (see that module's docstring); CP4-02's dynamic check (importing this module alone,
with ratchet.py never previously imported) proves the import itself never pays for either call.

die/read_json/RATCHET_DIR are the only engine_utils.py names these functions call; all three are imported
explicitly below, the same direction fingerprint.py already imports them in, so there is no cycle risk from
this module's side (engine_utils.py does not import this module at its own load time either).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import sys
import time
from pathlib import Path

# A loader that reaches this file directly (spec_from_file_location, the way this project's own
# source-inspecting tests do, and the way ratchet.py never does -- ratchet.py always has its own directory on
# sys.path already) may never have put this file's own directory on sys.path, so the sibling import just
# below would fail; ratchet.py itself does the same insert before its own sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_utils import die, read_json, RATCHET_DIR


def _ratchet_module():
    """The engine's own module, with its ratchet_home/require_hook_env (not yet moved here; CP6's
    run_records.py owns them) -- loaded by file path, right beside this module, never via a bare `import
    ratchet` that would depend on sys.path (and would be a real import cycle: ratchet.py already imports this
    module). Reuses an already-running ratchet.py (registered under "ratchet" in sys.modules -- including by
    ratchet.py itself when it runs as __main__) when there is one, so this never re-executes the file as a
    second, separate module instance in the normal case. Mirrors engine_utils.py's own helper of the same
    name and shape."""
    mod = sys.modules.get("ratchet")
    if mod is not None:
        return mod
    import importlib.util as _ilu
    path = Path(__file__).resolve().with_name("ratchet.py")
    spec = _ilu.spec_from_file_location("ratchet", path)
    mod = _ilu.module_from_spec(spec)
    sys.modules["ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


def ratchet_home() -> Path:
    """Deferred, call-time-only reach into ratchet.py's per-user folder path (not yet moved here; CP6's
    run_records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module().ratchet_home()


def require_hook_env(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's hook-env check (not yet moved here; CP6's
    run_records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module().require_hook_env(*args, **kwargs)


# --------------------------------------------------------------------------- tamper evidence
# ratchet.py records a hash of every file it owns after each write, in RATCHET_HOME (outside the repo and off-limits
# to agents). A write by any other route — a helper script, an editor, a "quick fix" — is detected on the next read.

def _chain_path() -> Path:
    return ratchet_home() / "file-hashes.json"


_FRESH = object()  # _read_record's default for `missing`: a new empty record ({}, or [] for lines)


def _read_record(path: Path, kind=dict, missing=_FRESH, lines: bool = False, tail: int | None = None, data=None):
    """The one reader of Ratchet's records (every file under .ratchet/ and in RATCHET_HOME that the hooks or the
    engine read for state, approvals, crashes, notes, hashes, director folders, stop errors or active runs).
    Returns (data, None), or (None, why) when the file can't be read, is not valid JSON, or is not a `kind` (a JSON
    object by default; None accepts any JSON value). A file that does not exist yet returns (`missing`, None): a new
    empty record unless the caller passes its own marker. With `lines`, a JSON-lines file: (the JSON objects on its
    lines, None), skipping lines that are not one; `tail` reads only its last `tail` bytes.
    It never exits or raises, so no hook or command can crash on a damaged record: each caller fails safe on its own
    terms (a damaged record that grants trust grants nothing), and says so with _note_unreadable.
    With `data` (bytes): parse those bytes instead of reading `path` (for a file's content taken from git)."""
    try:
        if data is not None:
            raw = data
        elif tail is None:
            raw = path.read_bytes()
        else:
            with open(path, "rb") as f:
                f.seek(max(0, os.fstat(f.fileno()).st_size - tail))
                raw = f.read()
    except FileNotFoundError:
        return (([] if lines else {}) if missing is _FRESH else missing), None
    except OSError as e:  # a folder or a link loop where the file goes, no permission, a parent that is a file
        return None, f"{type(e).__name__}: {e}"
    except Exception as e:  # never raise, whatever the file system does
        return None, f"{type(e).__name__}: {e}"
    if lines:
        out = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
        return out, None
    try:
        data = json.loads(raw.decode("utf-8-sig"))  # a leading byte-order mark is not damage
    except ValueError as e:  # invalid JSON, or bytes that are not UTF-8
        return None, f"{type(e).__name__}: {e}"
    except RecursionError:
        return None, "RecursionError: nested too deeply"
    if kind is not None and not isinstance(data, kind):
        return None, "not a JSON object" if kind is dict else f"not a JSON {getattr(kind, '__name__', kind)}"
    return data, None


def _write_record(path: Path, data, pretty: bool = False, raw: bool = False):
    """The one writer of Ratchet's records: replace the file in one rename, through a temp file of this process's
    own (pid plus a random token), flushed to disk first, so two writers never share a temp file, a reader never
    sees half a write, and a crash never leaves a half-written record. The temp file is removed on failure.
    Raises OSError when it can't write; every caller handles that on its own terms.
    raw=True: `data` is already the text to write, not a JSON value to encode (for a non-JSON generated
    artifact such as ci-export's workflow file); `pretty` is ignored in that case. When `data` is `bytes`
    (raw=True only), the temp file is opened in binary mode and those exact bytes are written, with no
    newline translation or re-encoding, so a caller that already computed its own encoded bytes (so a
    comparison, a write and a printed preview can never disagree) gets exactly those bytes on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
    text = data if raw else ((json.dumps(data, indent=2, ensure_ascii=False) + "\n") if pretty else json.dumps(data))
    try:
        if raw and isinstance(text, bytes):
            with open(tmp, "wb") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        else:
            # backslashreplace: a lone surrogate (from a reviewer's reply) is written as its own JSON escape,
            # "\ud83d", which reads back as the same string; every other character encodes as UTF-8 unchanged
            with open(tmp, "w", encoding="utf-8", errors="backslashreplace") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _append_record(path: Path, text: str):
    """The one appender for Ratchet's append-only records (human_log.jsonl, the run's .jsonl records, hook-errors.log,
    hook-crashes-unsaved.log): whole lines, added at the end, never rewriting what is there. Raises OSError."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


_NOTED_UNREADABLE = set()


def _note_unreadable(path: Path, why: str, outcome: str):
    """Say plainly, on stderr, that a damaged record was set aside and what Ratchet did instead (once per file and
    cause in a process)."""
    try:
        key = (str(path), why, outcome)
        if key in _NOTED_UNREADABLE:
            return
        _NOTED_UNREADABLE.add(key)
        print(f"ratchet: {path} could not be read ({why}); {outcome}", file=sys.stderr)
    except Exception:
        pass


class _BreakerLock:
    """The lock protocol both of Ratchet's lock files share (_ChainLock and its per-user siblings, and _StateLock), so
    they behave the same and a fix is made once. The lock file is created with O_CREAT|O_EXCL and holds this
    process's own token. It is only ever removed (a stale one, or the holder releasing its own) while holding a second
    O_CREAT|O_EXCL file, `<lock>.break`, and judged again under it, so two processes clearing one stale lock can't
    remove each other's fresh lock, and a release never removes another process's lock. Only a regular file is ever
    judged stale or removed (a folder or a link in its place is left alone). A breaker file older than BREAKER_STALE
    seconds was left by a process that died while holding it (it is held for a few system calls) and is removed, so
    a crash there can't disable the lock for good; only the very file judged stale is removed (_remove_stale_breaker),
    and a holder checks that its breaker is still in place before it removes anything under it (_holds_breaker), so
    two waiters behind one stale breaker can't both hold it. Subclasses say how old a lock must be to be stale (_stale_after)
    and how long to pause between tries (_pause)."""

    BREAKER_STALE = 30

    def __init__(self, path: Path, token: str):
        self.path = path
        self.token = token
        self.held = False
        self._breaker_id = None  # (device, inode) of the breaker file this process created, while it holds it

    @property
    def breaker(self) -> Path:
        return self.path.with_name(self.path.name + ".break")

    def _stale_after(self) -> float:
        raise NotImplementedError

    def _pause(self):
        time.sleep(0.005)

    def _create(self, path: Path):
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.token)

    def _is_stale(self, st) -> bool:
        """Whether a lock file with this lstat() result was left by a command that stopped: a regular file older
        than _stale_after() seconds, or one whose owner is provably gone (_owner_gone)."""
        return stat.S_ISREG(st.st_mode) and (time.time() - st.st_mtime > self._stale_after() or self._owner_gone())

    def _owner_gone(self) -> bool:
        """Whether the process that made the lock file is provably gone. False unless a subclass can tell."""
        return False

    def _take_breaker(self, wait: float) -> bool:
        """True once this process holds the breaker file; False when another process holds it for longer than
        `wait`. Raises OSError when it can't be created at all (the caller decides what that means)."""
        deadline = time.monotonic() + wait
        while True:
            try:
                self._create(self.breaker)
                try:  # the file this process created: what _holds_breaker checks later
                    st = os.lstat(self.breaker)
                except OSError:  # moved away at once by a waiter clearing a stale breaker: try again
                    continue
                self._breaker_id = (st.st_dev, st.st_ino)
                return True
            except FileExistsError:
                try:  # held for a few system calls: one this old was left by a process that died holding it
                    st = os.lstat(self.breaker)
                    if stat.S_ISREG(st.st_mode) and time.time() - st.st_mtime > self.BREAKER_STALE:
                        self._remove_stale_breaker(st)
                        continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                return False
            self._pause()

    def _remove_stale_breaker(self, st):
        """Remove the breaker file judged stale (its lstat() result is `st`), and only that file. Another waiter may
        have removed it and taken a fresh breaker since it was judged, so it is never unlinked by path: it is first
        renamed to a name of this process's own, and only removed when the renamed file is the one judged (same
        device, inode and modification time). Otherwise it is a fresh breaker another process holds, and it is put
        back at once (a hard link that never replaces a newer breaker). Raises OSError when the rename fails (the
        file is gone already: the caller tries to create the breaker again)."""
        mine = self.breaker.with_name(f"{self.breaker.name}.{os.getpid()}-{secrets.token_hex(4)}.stale")
        os.rename(self.breaker, mine)
        try:
            got = os.lstat(mine)
            if (got.st_dev, got.st_ino, got.st_mtime_ns) != (st.st_dev, st.st_ino, st.st_mtime_ns):
                try:  # a live breaker taken by mistake: give it back
                    os.link(mine, self.breaker)
                except OSError:
                    pass
        finally:
            try:
                os.unlink(mine)
            except OSError:
                pass

    def _holds_breaker(self) -> bool:
        """Whether the breaker file in place is still the one this process created (a waiter clearing a stale
        breaker may have moved it away for a moment). Checked right before removing anything under it."""
        try:
            st = os.lstat(self.breaker)
        except OSError:
            return False
        return self._breaker_id is not None and (st.st_dev, st.st_ino) == self._breaker_id

    def _drop_breaker(self):
        try:
            if self._holds_breaker():  # never another process's breaker
                os.unlink(self.breaker)
        except OSError:
            pass
        self._breaker_id = None

    def _clear_stale(self) -> bool:
        """Remove the lock file if it is still stale when judged again while holding the breaker file. True when
        the lock file is gone now (removed, or already released), so the caller can try to take it at once; False
        when another process holds the breaker or the lock is live. Raises OSError for a file-system error."""
        if not self._take_breaker(0):
            return False  # another process is clearing or releasing the lock right now: try again shortly
        try:
            if not self._holds_breaker():
                return False  # moved away by a waiter clearing a stale breaker: judge again later
            if self._is_stale(os.lstat(self.path)):
                os.unlink(self.path)
                return True
            return False  # a newer, live lock (or not a lock file): wait for it
        except FileNotFoundError:
            return True
        finally:
            self._drop_breaker()

    def _release_own(self, wait: float):
        """Remove the lock file if it still holds this process's token, under the breaker file. When the breaker
        can't be had within `wait` seconds the lock is left in place (it goes stale and is cleared later): a lock
        that might not be ours is never removed."""
        try:
            if not self._take_breaker(wait):
                return
        except OSError:
            return
        try:
            if self._holds_breaker() and self.path.read_text(encoding="utf-8") == self.token:
                os.unlink(self.path)
        except OSError:
            pass
        finally:
            self._drop_breaker()


class _ChainLock(_BreakerLock):
    """A short lock (milliseconds) around the hash record: its read-modify-write, an append to a recorded file
    together with its new hash, and a check of a file against its hash. The prompt hook appends to human_log.jsonl
    without the state lock (it never waits on a long command), so a reader must never see that file between the
    append and its new hash, and two writers must never drop each other's hash. Best effort: when it can't be had
    within a few seconds (an unwritable per-user folder, say) the caller goes on without it, as before.
    The breaker protocol is _BreakerLock's; a lock older than STALE seconds was left by a process that died."""

    STALE = 30

    def __init__(self, wait: float = 5.0):
        super().__init__(ratchet_home() / "file-hashes.lock", f"{os.getpid()} {secrets.token_hex(8)}")
        self.wait = wait

    def _stale_after(self) -> float:
        return self.STALE

    def __enter__(self):
        deadline = time.monotonic() + self.wait
        while True:
            try:  # no per-user folder yet: there is no hash record to protect
                self._create(self.path)
                self.held = True
                return self
            except FileExistsError:
                try:  # held for milliseconds: one older than half a minute was left by a process that died
                    if self._is_stale(os.lstat(self.path)) and self._clear_stale():
                        continue
                except OSError:
                    pass
            except OSError:
                return self
            if time.monotonic() >= deadline:
                return self
            time.sleep(0.01)

    def __exit__(self, *exc):
        if self.held:
            self.held = False
            self._release_own(1.0)  # can't be had: leave the lock; it goes stale and is cleared later
        return False


def _record_lock_path(record: Path) -> Path:
    """The lock file a shared record is protected by: named after the record, next to it (`active-runs.lock` for
    active-runs.json, `enrolled.lock`, `hook-heartbeat.lock`, ...). The one place that name is built, so _RecordLock
    and every busy message that names the lock stay in step."""
    return record.with_name(record.stem + ".lock")


class _RecordLock(_ChainLock):
    """The short lock update_record holds by default around one shared record's read-modify-write: a lock file named
    after the record, next to it (`active-runs.lock` for active-runs.json, `enrolled.lock`, `hook-heartbeat.lock`,
    ...). Built like file-hashes.lock: the breaker protocol is _BreakerLock's, it is held for milliseconds, and one
    older than STALE seconds was left by a process that died and is cleared."""

    def __init__(self, record: Path, wait: float = 5.0):
        super().__init__(wait)
        self.path = _record_lock_path(record)


KEEP = object()  # update_record: `fn` or `damaged` returns it to leave the record exactly as it is


def update_record(path: Path, fn, lock=None, damaged=None, pretty: bool = False, need_lock: bool = True,
                  wait: float = 5.0) -> str:
    """The one read-modify-write of a shared record: a file more than one process can update at once (every record in
    RATCHET_HOME, and in .ratchet/ the ones hooks or commands outside the state lock rewrite). Two unlocked writers
    would each read the file, add their own entry and write the whole file back, and the later write would silently
    drop the other's entry. So this holds a _BreakerLock-based lock for that file from the read to the write: it reads
    with _read_record, applies `fn(data)` and writes the result with _write_record. The lock is a _RecordLock next to
    the record, taken here, unless the caller passes `lock`: one it has already entered (its own lock file for this
    record, or a wider step under the same lock), used as it is. The default lock is waited for at most `wait`
    seconds.
    `fn(data)` returns the new data, or KEEP to leave the file as it is.
    `damaged` is the file's own rule for a record that can't be read: a sentence (it reads as empty through read_json,
    which says so on stderr with that sentence, and is replaced), or a function of the reason returning the data to
    start from (the file is then replaced) or KEEP. Default: it is left as it is, and stderr says so.
    Returns "written", "kept" (fn kept it), "damaged" (left as it is) or "busy" (the lock could not be had in time:
    nothing was read or written, unless need_lock is False, when it goes on without it, best effort as before).
    Raises OSError when the folder or the write fails; every caller handles that on its own terms."""
    with contextlib.ExitStack() as stack:
        if lock is None:
            path.parent.mkdir(parents=True, exist_ok=True)  # the lock file goes in this folder
        lk = lock if lock is not None else stack.enter_context(_RecordLock(path, wait))
        if not lk.held and need_lock:
            return "busy"
        if isinstance(damaged, str):
            data = read_json(path, {}, record=damaged)
        else:
            data, why = _read_record(path)
            if why is not None:
                if damaged is None:
                    _note_unreadable(path, why, "Ratchet left it as it is and did not write over it")
                    return "damaged"
                data = damaged(why)
                if data is KEEP:
                    return "damaged"
        new = fn(data)
        if new is KEEP:
            return "kept"
        _write_record(path, new, pretty=pretty)
        return "written"


_CHAIN_REBUILD = False  # set by accept-state (a person confirmed at their terminal): an unreadable record is rebuilt


def _chain_record():
    """(the hash record, None), or (None, why) when it can't be read or is not a JSON object."""
    return _read_record(_chain_path())


def _record_chain_now(p: Path, lock: _ChainLock):
    """Record p's hash, under `lock` (the caller's _ChainLock, already entered; best effort as ever: when it could
    not be had, the hash is still recorded). An unreadable hash record is never replaced here (a fresh record would
    drop every other file's hash, and a file with no recorded hash is trusted): it is left as it is, so every check
    against it keeps refusing, until accept-state, run by a person at their own terminal, rebuilds it. Never exits."""
    cp = _chain_path()

    def damaged(why):
        if not _CHAIN_REBUILD:
            _note_unreadable(cp, why, "the new hash was not recorded and the file was left as it is; every check "
                             "against it refuses until the user runs accept-state")
            return KEEP
        _note_unreadable(cp, why, "accept-state replaces it with a new record")
        return {}

    def add(data):
        raw = p.read_bytes()
        key = str(p.resolve())
        data[key] = hashlib.sha256(raw).hexdigest()
        return data
    try:
        _ = update_record(cp, add, lock=lock, damaged=damaged, need_lock=False)  # need_lock=False: never "busy"
    except OSError:
        pass


def record_chain(p: Path):
    with _ChainLock() as lock:
        _record_chain_now(p, lock)


def append_recorded(p: Path, line: str):
    """Append one line to a file Ratchet keeps a hash of and record its new hash, as one step for every reader."""
    with _ChainLock() as lock:
        _append_record(p, line)
        _record_chain_now(p, lock)


ACCEPT_STATE_CMD = "`RATCHET_HUMAN_OVERRIDE=1 ratchet.py accept-state`"


def chain_problem(p: Path, label: str) -> str | None:
    """Why `p` can't be trusted against Ratchet's record of file hashes (None when it can). Never exits, so a hook can
    use it: a damaged record vouches for nothing (it is a problem, never permission), and so does a file that can't
    be read."""
    try:
        with _ChainLock():
            record, why = _chain_record()
            known = (record or {}).get(str(p.resolve()))
            same = why is None and (not known or not p.exists()
                                    or hashlib.sha256(p.read_bytes()).hexdigest() == known)
    except OSError as e:
        return (f"{label} ({p}) can't be read ({type(e).__name__}: {e.strerror or e}), so Ratchet can't confirm it "
                "was not changed outside ratchet.py, and won't trust it. Tell the user; check that it is a file "
                "Ratchet can read.")
    if why is not None:  # a damaged record vouches for nothing: refuse rather than trust the file
        return (f"Ratchet's record of file hashes ({_chain_path()}) can't be read ({why}), so it can't confirm that "
                f"{label} ({p}) was not changed outside ratchet.py, and won't trust it. Tell the user; only if they "
                f"are sure the file is right, they can rebuild the record from their own terminal with "
                f"{ACCEPT_STATE_CMD} (it asks them to type back a short code).")
    if not same:
        return (f"{label} was changed outside ratchet.py ({p}). Ratchet won't trust it. Tell the user; restore the "
                "file, or, only if they are sure, they can accept it from their own terminal with "
                f"{ACCEPT_STATE_CMD} (it asks them to type back a short code).")
    return None


def verify_chain(p: Path, label: str):
    if not _IN_HOOK:  # the record of hashes lives in the per-user folder: only trust the one the hooks use
        for cand in p.resolve().parents:
            if cand.name == RATCHET_DIR:
                require_hook_env(cand.parent)
                break
    why = chain_problem(p, label)
    if why:
        die(why)


_IN_HOOK = False  # this module's own copy, always False: see this module's docstring and verify_chain's note
# below. verify_chain's body (unchanged by the move) reads `_IN_HOOK` as a bare name, so it needs SOME value
# bound here; it is never set True, because setting it would have no safe way back to False from outside (no
# caller mutates this module's copy the way ratchet.py's run_hook sets its own). That is harmless: when a real
# hook sets ratchet.py's own `_IN_HOOK` True and calls into verify_chain here, this module's `_IN_HOOK` is still
# False, so the `if not _IN_HOOK:` branch runs require_hook_env() anyway -- but require_hook_env() (reached
# through _ratchet_module() above) checks ratchet.py's OWN `_IN_HOOK`, which IS True there, so it does nothing
# (why = None, no die): the exact same outcome as before the split, just by way of one extra no-op call.
