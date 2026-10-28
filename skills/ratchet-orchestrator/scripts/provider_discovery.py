#!/usr/bin/env python3
"""
provider_discovery.py -- reviewer-provider discovery, probing, quota and fallback bookkeeping for ratchet.py's
engine.

update_review_starts/_reserve_under_lock/refund_review_start/provider_bench/shared_unavailable/event_stream/
probe_provider/_record_probe/quota_failure/quota_retest_minutes/provider_share/bench_quota/
record_round_failure/review_succeeded/probe_due/probe_shared, moved verbatim out of ratchet.py (see
tests/test_modularize_cp5.py, CP5-T01) so this one responsibility -- which provider a review round gets, how a
test request is sent and recorded, and how a provider's quota bench and a round's own failures are tracked,
all under the shared provider-health.json record's lock -- can be read and changed without reading or risking
the rest of the 21,000+ line engine. ratchet.py imports these sixteen names explicitly (`from
provider_discovery import ...`); it never does a star import, and keeps no `def` of its own for any of them.
_EVENT_TYPES moves here too: it is used only inside event_stream, nowhere else in ratchet.py, so it is not a
forward dependency, just the one constant this move's own event_stream needs beside it (the same "a related
helper right next to the function that uses it alone" shape fingerprint.py's docstring describes for its own
module-level constants) -- a plain tuple, so CP5-T03's mutable-literal scan (which only flags a Dict/List/Set
literal or dict()/list()/set()/Counter()/defaultdict()/OrderedDict() call) does not flag it.

cmd_providers (the CLI handler) stays in ratchet.py, unchanged except that the sixteen names it calls now
resolve through this module's explicit import, the same way records.py's own sixteen names are reached from
cmd_accept_state and friends.

Many of these sixteen functions' own bodies (unchanged by the move) read a bare name that is NOT one of the
sixteen and stays in ratchet.py for now (most of these look like good candidates for a later checkpoint's own
module, not this one's responsibility): _start_id, _reviewer_health_key, _start, _update_health,
_note_record_busy, _health_path, _status_path, _unavailable_path, _update_unavailable,
_update_provider_status, _health_key, provider_health, roster, per_use_unapproved, provider_command,
reviewer_binary, unavailable_reviewers, provider_by_id and _StateLock (ratchet.py's own top-level run lock,
which done-means #1 keeps in ratchet.py as the stable entry point -- see checkpoints.json's out_of_scope).
Every one of these is reached lazily, by file path, at call time only, through _ratchet_module() below --
never a disk read or an AST parse of ratchet.py's text at THIS module's import time, and never a module-level
`import ratchet` that would create an import cycle (ratchet.py already imports this module). This is the exact
shape records.py's own ratchet_home()/require_hook_env() wrappers use for their own forward dependency on
ratchet.py (see that module's docstring); the dynamic half of tests/test_modularize_cp5.py's CP5-T02 (importing
this module alone, with ratchet.py never previously imported) proves the import itself never pays for any of
them: each wrapper's own _ratchet_module() call happens inside its body, only once one of the sixteen moved
functions is actually CALLED, which in the real CLI only happens after ratchet.py's own module body has
finished running top to bottom (main() dispatches only then) -- so every one of these names is always bound on
the real ratchet.py module object by the time any wrapper reaches for it.

Four of the sixteen functions' own bodies also read a bare name that is a ratchet.py-resident CONSTANT, not a
function call, so it cannot be wrapped the way a function call can (there is nothing to call lazily; the bare
name itself must already resolve to a usable value in THIS module's own globals by call time). PROBE_TOKEN
(probe_provider) and _PROBE_CLAIM_SLACK (probe_shared) are small, fixed literals -- the exact values ratchet.py
itself defines, which do not vary with any run-time state -- so they are simply given here too, the same
"a literal placed directly, with a comment saying why" shape records.py's own docstring documents for
verify_chain's _IN_HOOK (there, the literal is deliberately always False and still correct by the time it
matters; here, the literals are the live constants themselves, so there is nothing to drift). LENSES
(_record_probe, only ever iterated) is likewise a small, fixed tuple -- an immutable literal, so CP5-T03's scan
does not flag it either. PRESETS (probe_provider, only ever `.get()`-called) is different: a large, evolving
registry of provider CLI command templates that ratchet.py keeps adding to, so copying its value here would be
exactly the hand-synced-copy drift risk the project's own precedent (records.py's KEEP/ACCEPT_STATE_CMD
reasoning) warns against. _LazyPresets below is a tiny stand-in bound once, at this module's own load time (its
own construction needs nothing from ratchet.py), whose one method (`.get`, the only one probe_provider's body
calls on this bare name) reaches ratchet.py lazily, through _ratchet_module(), at call time -- never a second
copy of the registry itself.

die/now are the only two engine_utils.py functions these sixteen functions call; both are imported explicitly
from engine_utils, never duplicated. _record_lock_path is the only records.py name they call (_reserve_under_lock's
busy message); imported explicitly from records, the same direction fingerprint.py and records.py already
import engine_utils in, so there is no cycle risk from this module's side. provider_health.py (imported here as
`phealth`, the same alias ratchet.py itself uses) is already a leaf module with no dependency on ratchet.py or
on this module, so it is imported here the ordinary way, exactly as ratchet.py does.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# A loader that reaches this file directly (spec_from_file_location, the way this project's own
# source-inspecting tests do, and the way ratchet.py never does -- ratchet.py always has its own directory on
# sys.path already) may never have put this file's own directory on sys.path, so the sibling imports just
# below would fail; ratchet.py itself does the same insert before its own sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import provider_health as phealth
from engine_utils import die, now
from records import _record_lock_path


def _ratchet_module():
    """The engine's own module, with every ratchet.py-resident name these sixteen functions still reach for
    (not yet moved anywhere; most look like candidates for a later checkpoint's own module) -- loaded by file
    path, right beside this module, never via a bare `import ratchet` that would depend on sys.path (and would
    be a real import cycle: ratchet.py already imports this module). Reuses an already-running ratchet.py
    (registered under "ratchet" in sys.modules -- including by ratchet.py itself when it runs as __main__)
    when there is one, so this never re-executes the file as a second, separate module instance in the normal
    case. Mirrors records.py's own helper of the same name and shape."""
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


def _start_id(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own review-start id builder -- see _ratchet_module()."""
    return _ratchet_module()._start_id(*args, **kwargs)


def _reviewer_health_key(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own reviewer-to-provider-health-key lookup -- see
    _ratchet_module()."""
    return _ratchet_module()._reviewer_health_key(*args, **kwargs)


def _start(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own review-start recorder -- see _ratchet_module()."""
    return _ratchet_module()._start(*args, **kwargs)


def _update_health(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own provider-health.json updater -- see
    _ratchet_module()."""
    return _ratchet_module()._update_health(*args, **kwargs)


def _note_record_busy(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own busy-record stderr note -- see _ratchet_module()."""
    return _ratchet_module()._note_record_busy(*args, **kwargs)


def _health_path(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own provider-health.json path -- see
    _ratchet_module()."""
    return _ratchet_module()._health_path(*args, **kwargs)


def _status_path(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own provider-status.json path -- see
    _ratchet_module()."""
    return _ratchet_module()._status_path(*args, **kwargs)


def _unavailable_path(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own unavailable-reviewers.json path -- see
    _ratchet_module()."""
    return _ratchet_module()._unavailable_path(*args, **kwargs)


def _update_unavailable(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own unavailable-reviewers.json updater -- see
    _ratchet_module()."""
    return _ratchet_module()._update_unavailable(*args, **kwargs)


def _update_provider_status(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own provider-status.json updater -- see
    _ratchet_module()."""
    return _ratchet_module()._update_provider_status(*args, **kwargs)


def _health_key(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own provider-health-record key builder -- see
    _ratchet_module()."""
    return _ratchet_module()._health_key(*args, **kwargs)


def provider_health(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own shared provider-health.json reader -- see
    _ratchet_module()."""
    return _ratchet_module().provider_health(*args, **kwargs)


def roster(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own reviewer roster -- see _ratchet_module()."""
    return _ratchet_module().roster(*args, **kwargs)


def per_use_unapproved(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own per-use-cost-without-OK check -- see
    _ratchet_module()."""
    return _ratchet_module().per_use_unapproved(*args, **kwargs)


def provider_command(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own provider command builder -- see
    _ratchet_module()."""
    return _ratchet_module().provider_command(*args, **kwargs)


def reviewer_binary(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own reviewer-to-binary lookup -- see
    _ratchet_module()."""
    return _ratchet_module().reviewer_binary(*args, **kwargs)


def unavailable_reviewers(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own this-project unavailable-reviewers reader -- see
    _ratchet_module()."""
    return _ratchet_module().unavailable_reviewers(*args, **kwargs)


def provider_by_id(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own provider-by-id lookup -- see _ratchet_module()."""
    return _ratchet_module().provider_by_id(*args, **kwargs)


def _StateLock(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own top-level run lock (kept in ratchet.py as the
    stable entry point, per this run's done-means #1 and out_of_scope) -- see _ratchet_module(). Calling this
    returns a real instance of ratchet.py's own _StateLock class, used by record_round_failure exactly as
    before the move."""
    return _ratchet_module()._StateLock(*args, **kwargs)


class _LazyPresets:
    """A stand-in for ratchet.py's own PRESETS dict (not one of this module's sixteen moved names, and not
    duplicated here -- see this module's own docstring): probe_provider's body (unchanged by the move) only
    ever calls `.get()` on this bare name, so that is the only method this stand-in needs; it reaches
    ratchet.py lazily, at call time only, through _ratchet_module()."""

    def get(self, key, default=None):
        return _ratchet_module().PRESETS.get(key, default)


PRESETS = _LazyPresets()

PROBE_TOKEN = "RATCHET-PROBE-OK"  # ratchet.py's own fixed probe-reply token (see this module's docstring)
_PROBE_CLAIM_SLACK = 30.0  # seconds a test-request claim lasts beyond the request's own timeout (and a waiter waits)
LENSES = ("correctness", "standards", "both")  # ratchet.py's own fixed lens names (see this module's docstring)


# --------------------------------------------------------------------------- provider discovery, probing and quota

def update_review_starts(cfg, nonce, add=(), give_back=()) -> None:
    """Change the shared provider health record's review starts in one save, without choosing anything: the unlocked,
    best-effort recorder. Opening and refilling a round reserve their starts through _reserve_under_lock (which also
    checks provider_share); this is used for those only when that lock was busy (the starts are recorded afterwards, with
    no share check), for releasing a reservation whose provider was never tested, and for external_review's refunds.
    `add` are the reviewer ids a round (`nonce`) starts with: each reviewer's provider gets a start (a host reviewer has
    no provider), so the next round, in this project or any other on the machine, spreads to the providers used least.
    `give_back` are reviewer ids of this round that never really used their provider: exactly the start recorded for
    that reviewer in that round is taken back, once. A busy lock prints a note naming the review start and the round
    goes on."""
    adds = [(k, _start_id(nonce, r)) for r in add for k in [_reviewer_health_key(cfg, r)] if k]
    backs = [(k, _start_id(nonce, r)) for r in give_back for k in [_reviewer_health_key(cfg, r)] if k]
    if not adds and not backs:
        return
    at = _dt.datetime.now(_dt.timezone.utc)

    def change(data):
        for k, sid in backs:
            phealth.refund_start(data, k, sid)
        for k, sid in adds:
            _start(data, k, at, sid)
    if _update_health(change, note=False) == "busy":
        _note_record_busy(_health_path(), "a review start" if adds else "the give-back of a review start")


def _reserve_under_lock(cfg, ignore, pick, refunds, adds, keep_picked=False):
    """Choose reviewers and reserve their starts as ONE step under the shared provider record's lock, so lanes
    opening rounds at the same moment can never take more than provider_share.max starts on one provider in the window:
    the second lane's choice is made after the first lane's reservation is saved. Inside the lock this calls
    `pick(share)` (resolve_reviewers with the record read under the lock; `share` = {"data", "ignore"} where `ignore`
    are start ids about to be given back and so not counted), then gives back the starts `refunds(picked)` lists and
    records the starts `adds(picked)` lists, both as [(reviewer id, round nonce)], and saves them together
    (`keep_picked`: the given-back starts keep their pick time, for a round replaced unreviewed). Returns what
    `pick` returned, or None when the lock could not be had (a note says the starts could not be reserved under the
    lock; the caller goes on without a reservation and records its starts afterwards, best effort). What it then
    goes on with differs: a round being opened keeps the read-only share pick it made just before (the share was
    checked on an unlocked read, only the atomic reservation is lost); a same-round refill picks with no share."""
    out = []

    def change(data):
        picked = pick({"data": data, "ignore": set(ignore)})
        at = _dt.datetime.now(_dt.timezone.utc)
        for rid, nonce in refunds(picked):
            key = _reviewer_health_key(cfg, rid)
            if key:
                phealth.refund_start(data, key, _start_id(nonce, rid), keep_picked)
        for rid, nonce in adds(picked):
            key = _reviewer_health_key(cfg, rid)
            if key:
                _start(data, key, at, _start_id(nonce, rid))
        out.append(picked)
    if _update_health(change, note=False) == "busy":
        p = _health_path()
        print(f"ratchet: {p} was being updated by another Ratchet command (its lock {_record_lock_path(p)} could not be "
              "had), so this round's review starts could not be reserved under the lock (the provider share was checked "
              "on an unlocked read, or not at all for a same-round refill); they are recorded afterwards if the lock "
              "frees up.", file=sys.stderr)
        return None
    return out[0]


def refund_review_start(cfg, nonce, rid) -> None:
    """The review by `rid` in the round `nonce` never used its provider (the CLI failed before it answered): take back
    exactly the start that round recorded for it (see update_review_starts). Once only: a second call finds none."""
    update_review_starts(cfg, nonce, give_back=[rid])


def provider_bench(cfg, rid) -> dict | None:
    """The live bench ({reason, since, until}) of the provider behind reviewer `rid`, as every lane sees it."""
    key = _reviewer_health_key(cfg, rid)
    if key is None:
        return None
    return phealth.live_bench(provider_health(), key, _dt.datetime.now(_dt.timezone.utc))


def shared_unavailable(cfg) -> dict:
    """Every roster reviewer whose provider is benched in the shared health record, as {reviewer id: {reason, since,
    until}}: the same shape as unavailable-reviewers.json, so `status --json` shows a provider any lane benched."""
    data, at, out = provider_health(), _dt.datetime.now(_dt.timezone.utc), {}
    for rid in roster(cfg):
        key = _reviewer_health_key(cfg, rid)
        bench = phealth.live_bench(data, key, at) if key else None
        if bench:
            out[rid] = {k: bench.get(k) for k in ("reason", "since", "until")}
    return out


_EVENT_TYPES = ("step_start", "step_finish", "text", "reasoning", "tool_use", "tool_result", "error")


def event_stream(stdout) -> dict | None:
    """Read the output of `opencode run --format json`: one JSON event per line ({"type", "part": {...}} with type
    step_start, text, reasoning, tool_use, step_finish or error). Returns {"text": the text events' answer text only,
    "errors": the error events' messages, "length": a step finished with reason "length" (the model ran out of
    output room)}, or None when the output holds no event (an older CLI or a plain-text program), which callers read
    as plain text. A reasoning or tool event, and the request echoed in either, is never part of the answer."""
    text, errors, length, seen = [], [], False, False
    for line in str(stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:  # a line of a program's output, not a record Ratchet keeps (so not _read_record)
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("type") not in _EVENT_TYPES:
            continue
        seen = True
        part = ev["part"] if isinstance(ev.get("part"), dict) else {}
        if ev["type"] == "text":
            text.append(str(part.get("text") or ""))
        elif ev["type"] == "step_finish" and part.get("reason") == "length":
            length = True
        elif ev["type"] == "error":
            err = ev["error"] if isinstance(ev.get("error"), dict) else {}
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            errors.append(str(data.get("message") or err.get("message") or err.get("name") or "error"))
    return {"text": "\n".join(text), "errors": errors, "length": length} if seen else None


def probe_provider(ctx, cfg, pv, bench=True, note: bool = True, record: bool = True,
                   by: str | None = None) -> tuple[bool, str, bool]:
    """Send a tiny request through the provider as a review would go (the same command with the variant arguments),
    plus the preset's `probe_args` (opencode: --title=connectivity check, which replaces the review's title); a JSON
    event stream in its output is read with event_stream (only the answer text can hold the token, and an error
    event reads like stderr). The result is recorded (_record_probe: the shared provider-health.json, then this
    project's unavailable-reviewers.json), dated when the request was
    sent; `by` is the claim this lane holds on the provider (probe_shared), ended with the result.
    Returns (good, detail, saved): `saved` is False when either of those writes could not be made because its lock
    was busy (True when both were saved, or when nothing was sent because the provider has no command or isn't
    installed, so there was nothing to record). Every recording caller is best effort and leaves note=True, so a
    stderr line speaks for a busy write. A caller that must not claim anything was saved unless all of it was (a
    checked `providers --add`) passes record=False (nothing is written, and `saved` is False once a request was
    sent), then takes the three records' locks (provider-status, provider-health, unavailable-reviewers) itself and
    calls _record_probe."""
    if per_use_unapproved(pv):  # costs money per use and no recorded OK: not even a test request
        return False, "costs money per use and the developer has not given the exact OK; no request sent", True
    cmd_t, use_stdin = provider_command(pv)
    if not cmd_t:
        return False, "no command", True
    if not shutil.which(cmd_t[0]):
        return False, f"'{cmd_t[0]}' is not installed or not on PATH", True
    prompt = f"This is a connectivity check. Reply with exactly this and nothing else: {PROBE_TOKEN}"
    work = Path(tempfile.mkdtemp(prefix="ratchet-probe-"))
    out_file = work / "last-message.txt"
    subs = {"{prompt_file}": str(work / "prompt.md"), "{prompt}": prompt, "{out_file}": str(out_file),
            "{root}": str(ctx.root), "{model}": str(pv.get("model") or "")}
    (work / "prompt.md").write_text(prompt)
    cmd = []
    for part in cmd_t:
        for k, v in subs.items():
            part = part.replace(k, v)
        cmd.append(part)
    if not pv.get("command"):  # a preset may label its test request (opencode titles the session)
        extra = list((PRESETS.get(pv.get("preset", "")) or {}).get("probe_args") or [])
        names = tuple(a.split("=", 1)[0] + "=" for a in extra if a.startswith("--") and "=" in a)
        cmd = [c for c in cmd if not c.startswith(names)] + extra  # its own value replaces the preset's, never doubled
    sent = _dt.datetime.now(_dt.timezone.utc)  # the result is dated by this, not by when the answer came back
    quota_until = None  # set when the provider said it is out of credits: the time it said it is back
    try:
        r = subprocess.run(cmd, cwd=work, input=prompt if use_stdin else None, capture_output=True, text=True,
                           stdin=None if use_stdin else subprocess.DEVNULL,  # a CLI waiting on stdin must not hang
                           timeout=int(cfg.get("probe_timeout_sec", 120)),
                           env=dict(os.environ, RATCHET_NESTED_REVIEW="1"))
        ev = event_stream(r.stdout)
        if ev:  # a JSON event stream: only its answer text can hold the token (not the request echoed in an event)
            r.stdout, r.stderr = ev["text"], (r.stderr or "") + "\n".join(ev["errors"])
        text = (out_file.read_text() if out_file.exists() else "") + (r.stdout or "") + (r.stderr or "")
        good = PROBE_TOKEN in text.replace("\n", "")
        detail = "answered the test request" if good else (
            f"exit {r.returncode}: " + ((r.stderr or r.stdout or "no output").strip().splitlines() or ["no output"])[-1][:200])
        if not good:
            quota = quota_failure([r.stderr, r.stdout, out_file.read_text() if out_file.exists() else ""], cfg, sent)
            if quota:
                quota_until, detail = quota[0], f"out of credits: {quota[1]}"
    except subprocess.TimeoutExpired:
        good, detail = False, f"no answer within {cfg.get('probe_timeout_sec', 120)}s"
    except OSError as e:
        good, detail = False, str(e)[:200]
    shutil.rmtree(work, ignore_errors=True)
    if not record:
        return good, detail, False
    busy = _record_probe(ctx, pv, good, detail, bench=bench, note=note, sent=sent, by=by, quota_until=quota_until)
    return good, detail, not busy


def _record_probe(ctx, pv, good: bool, detail: str, bench: bool = True, note: bool = True, locks=None,
                  local_copy: bool = False, sent=None, by: str | None = None, quota_until=None) -> list:
    """Record a probe's result in the shared provider health record (a failing provider is benched for every lane
    when `bench`), then clear this project's reviewer marks for a provider that passed or is not benched. `sent` is
    when the request was sent (default: now): a result older than the one already recorded is dropped. `by` is the
    claim this lane holds on the provider, ended with the result. `quota_until`: the provider said it is out of
    credits and is back then: its bench is a quota bench to that time (a passing test request does not end it).
    `local_copy`: a checked `providers --add` also keeps its result in this project's own provider-status.json, as
    it always has (nothing reads that file for a decision any more). Returns the records whose write was busy, in
    the order tried (empty when every write was saved). `locks` maps a record's path to its _RecordLock the caller
    already holds: a caller that takes them all first can't have one written and then find another busy."""
    locks = locks or {}
    hp, sp, up = _health_path(), _status_path(ctx), _unavailable_path(ctx)
    busy = []
    if local_copy and _update_provider_status(
            ctx, lambda data: data.__setitem__(pv["id"], {"ok": good, "at": now(), "detail": detail}),
            note=note, lock=locks.get(sp)) == "busy":
        busy.append(sp)
    at = _dt.datetime.now(_dt.timezone.utc)
    sent = sent or at
    benched = not good and bench
    quota = benched and quota_until is not None
    until = quota_until if quota else (
        at + _dt.timedelta(hours=float(ctx.config().get("unavailable_ttl_hours", 6))) if benched else None)
    if _update_health(lambda data: phealth.record_probe(data, _health_key(pv), good, detail, sent,
                                                        f"test request failed: {detail}", until, now=at, by=by,
                                                        quota=quota),
                      note=note, lock=locks.get(hp)) == "busy":
        busy.append(hp)
    if not benched:
        def clear(live):
            for lens in LENSES:
                k = f"{pv['id']}-{lens}"
                if not (live.get(k) or {}).get("quota"):  # a quota wait ends at its time or by a real review only
                    live.pop(k, None)
        if _update_unavailable(ctx, clear, note=note, lock=locks.get(up)) == "busy":
            busy.append(up)
    return busy


def quota_failure(texts, cfg, now=None):
    """(the time the provider is back, the line that said so) when a provider's own output says it is out of credits,
    else None (provider_health.quota_failure). A reset time it did not give, or one already past, becomes
    quota_retest_minutes (default 20) from now."""
    return phealth.quota_failure(texts, now or _dt.datetime.now(_dt.timezone.utc), quota_retest_minutes(cfg))


def quota_retest_minutes(cfg) -> float:
    """The quota_retest_minutes setting: a positive number, else a plain error naming the setting and its value (a
    zero or negative wait would end a quota bench the moment it begins)."""
    v = cfg.get("quota_retest_minutes", 20)
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = 0.0
    if isinstance(v, bool) or not 0 < n < float("inf"):
        die(f"quota_retest_minutes must be a positive number of minutes, but it is {json.dumps(v)}. Set it with "
            "`ratchet.py config --set quota_retest_minutes=20`.")
    return n


def provider_share(cfg) -> tuple[int, float]:
    """The provider_share setting as (max, minutes): at most `max` review starts on one provider in any `minutes`
    minutes, across every lane on the machine. `max` is a positive whole number and `minutes` a positive number
    (decimals accepted); anything else is a plain error naming the setting and its value."""
    v = cfg.get("provider_share", {"max": 3, "minutes": 15})
    if not isinstance(v, dict):
        die(f"provider_share must be {{\"max\": <whole number>, \"minutes\": <number>}}, but it is {json.dumps(v)}. "
            "Set it with `ratchet.py config --set provider_share.max=3 provider_share.minutes=15`.")
    mx, mins = v.get("max", 3), v.get("minutes", 15)
    if isinstance(mx, bool) or not isinstance(mx, int) or mx < 1:
        die(f"provider_share.max must be a positive whole number of review starts, but it is {json.dumps(mx)}. Set it "
            "with `ratchet.py config --set provider_share.max=3`.")
    ok_mins = isinstance(mins, (int, float)) and not isinstance(mins, bool) and 0 < mins < float("inf")
    if not ok_mins:
        die(f"provider_share.minutes must be a positive number of minutes, but it is {json.dumps(mins)}. Set it with "
            "`ratchet.py config --set provider_share.minutes=15`.")
    return mx, float(mins)


def bench_quota(cfg, rid, reason, until) -> bool:
    """A review by reviewer `rid` found its provider out of credits: bench the provider for every lane until `until`
    (a datetime) in the shared record. False when it could not be saved (no provider behind `rid`, or the record's
    lock was busy), so the caller marks the reviewer in its own project instead."""
    key = _reviewer_health_key(cfg, rid)
    if key is None:
        return False
    at = _dt.datetime.now(_dt.timezone.utc)
    return _update_health(lambda data: phealth.record_probe(data, key, False, reason, at, reason, until, now=at,
                                                            quota=True), note=False) != "busy"


def record_round_failure(ctx, cfg, cp_id, round_no, rids, own=None) -> bool:
    """A real review in round `round_no` of `cp_id` failed: record the provider behind each reviewer in `rids` in that
    round's `failed_providers` at once, so a test request that passes before the next `reviewers` call (it clears a
    plain bench) can't put the provider back in the round. Done under the state lock, like every other change to the
    run; the round is left alone when it has moved on (another round, or already recorded). `own` is the reviewer whose
    own run failed: it goes in the round's `failed_reviewers`, so a sibling swapped out without running is told apart.
    False when the lock could
    not be had in about 10 seconds (it is tried five times, so a long command holding it briefly does not lose the record): the bench still keeps the provider out until `reviewers` records it."""
    lock = _StateLock(ctx.root, cmd="review-failure")
    for _ in range(5):  # behind a long command (reviewers) one try waits only ~2s; this is a short write: ~10s in all
        if not lock.acquire():
            break
    else:
        return False
    try:
        st = ctx.state()
        rnd = (st.get("checkpoints", {}).get(cp_id) or {}).get("adv_round") or {}
        if not rnd or rnd.get("round") != round_no or rnd.get("recorded"):
            return True
        failed = set(rnd.get("failed_providers") or [])
        failed |= {_reviewer_health_key(cfg, r) or r for r in rids}
        own_failed = set(rnd.get("failed_reviewers") or []) | ({own} if own else set())
        if sorted(failed) != sorted(rnd.get("failed_providers") or []) or sorted(own_failed) != sorted(rnd.get("failed_reviewers") or []):
            rnd["failed_providers"] = sorted(failed)
            rnd["failed_reviewers"] = sorted(own_failed)
            ctx.save_state(st)
        return True
    finally:
        lock.release()


def review_succeeded(cfg, rid, sent, ctx=None) -> bool:
    """A real review by `rid`, sent at `sent` (a datetime), succeeded: its provider is back, so a quota bench that
    began before the review was sent (kept through passing test requests) ends now; a review already under way when
    another lane found the provider out of credits does not end that newer bench. With `ctx` (the project), the
    project's own quota marks (the fallback when the shared record was busy) for the same CLI end the same way; its
    other marks stay. Reads first, so an ordinary review writes nothing. False when a bench could not be ended (a
    record's lock was busy): it then ends at its own time."""
    done = True
    binary = reviewer_binary(cfg, rid)
    if ctx is not None and binary:
        def began_before(m):
            t = phealth._time(m.get("since"))
            return t is not None and t < sent

        def clear(live):
            for r in [r for r, m in live.items() if m.get("quota") and reviewer_binary(cfg, r) == binary
                      and began_before(m)]:
                live.pop(r)
        if any(m.get("quota") and reviewer_binary(cfg, r) == binary and began_before(m)
               for r, m in unavailable_reviewers(ctx).items()):
            done = _update_unavailable(ctx, clear, note=False) != "busy"
    key = _reviewer_health_key(cfg, rid)
    entry = provider_health().get(key) if key else None
    b = entry.get("bench") if isinstance(entry, dict) else None
    if isinstance(b, dict) and b.get("quota"):
        done = (_update_health(lambda data: phealth.end_quota_bench(data, key, sent), note=False) != "busy") and done
    return done


def probe_due(ctx, cfg, pid) -> bool:
    """Whether provider `pid` needs a test request now: not while any lane's recent test passed (inside
    probe_ttl_minutes) or while the provider is benched (it is tried again when the bench ends)."""
    pv = provider_by_id(cfg, pid)
    if not cfg.get("probe_providers", True) or pv is None:
        return False
    key, at, data = _health_key(pv), _dt.datetime.now(_dt.timezone.utc), provider_health()
    if phealth.live_bench(data, key, at):
        return False
    return not phealth.fresh_ok(data, key, at, float(cfg.get("probe_ttl_minutes", 120)))


def probe_shared(ctx, cfg, pv) -> tuple[bool, str]:
    """The automatic test request before a review round relies on provider `pv`: one lane sends it, the others use
    its result. Under the shared record's lock this looks at the record again and either finds a fresh pass or a
    bench (use it, send nothing), finds another lane's live claim (wait for that lane's result, polling, no longer
    than the claim lasts, then look again), or takes the claim itself and sends the request (probe_provider records
    the result and ends the claim; a claim whose lane died ends on its own). When the record's lock can't be had or
    the wait runs out, it sends its own request as it always did. Returns (good, detail)."""
    key, by = _health_key(pv), f"{os.getpid()}-{secrets.token_hex(4)}"
    timeout = float(cfg.get("probe_timeout_sec", 120))
    ttl = float(cfg.get("probe_ttl_minutes", 120))
    hold = timeout + _PROBE_CLAIM_SLACK  # the claim outlives the request: it starts a little after, and lands after
    give_up = time.monotonic() + hold
    while time.monotonic() < give_up:
        got = []
        res = _update_health(lambda data: got.append(phealth.claim(data, key, by, _dt.datetime.now(_dt.timezone.utc),
                                                                   ttl, hold)), note=False)
        if res == "busy":
            break
        what, _claim = got[0]
        if what == "claimed":
            try:
                return probe_provider(ctx, cfg, pv, by=by)[:2]
            finally:  # an early return (no command, not installed) records nothing: don't hold the claim
                _update_health(lambda data: phealth.release(data, key, by), note=False)  # busy: ends at its `until`
        if what in ("fresh", "benched"):
            e = provider_health().get(key)
            e = e if isinstance(e, dict) else {}
            return bool(e.get("ok")) and what == "fresh", str(e.get("detail") or "")
        while time.monotonic() < give_up:  # another lane's request is on its way
            time.sleep(0.5)
            if phealth.live_claim(provider_health(), key, _dt.datetime.now(_dt.timezone.utc)) is None:
                break
    return probe_provider(ctx, cfg, pv)[:2]
