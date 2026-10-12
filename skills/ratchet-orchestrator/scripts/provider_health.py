"""The shared provider health record: what Ratchet knows about each review provider, kept once per user.

One file, provider-health.json, sits directly in the per-user folder (RATCHET_HOME), so every project and lane on a
machine sees the same provider test results and benches. A provider is one entry no matter which project asked about
it, keyed by its tool plus its model ('codex', 'opencode:opencode-go/glm-5.3'):

    {"<key>": {"ok": true|false, "at": "<time the last test request was SENT>", "detail": "<what came back>",
               "bench": {"reason": "...", "since": "<time>", "until": "<time>"},
               "claim": {"by": "<lane's token>", "since": "<time>", "until": "<time>"}}}

`bench` is present only while the provider is being skipped; it ends on its own at `until`. `claim` is present only
while one lane's test request is on its way: other lanes wait for its result instead of sending their own, and a
claim whose lane never answered ends on its own at `until`. A result is dated by when its request was sent, and a
result older than the one already recorded never replaces it, so a slow answer that lands late cannot undo a newer
one.

This module is pure record logic: every function takes the record's data (a dict) and edits it in place, or answers a
question about it. It reads and writes no file. ratchet.py does that, only through update_record (under the record's
lock), so two lanes recording a result at the same moment both end up saved.
"""
from __future__ import annotations

import datetime as _dt

FILE = "provider-health.json"
TEST_KEYS = ("ok", "at", "detail")


def entry_key(tool: str, model: str | None = None) -> str:
    """The one key a provider has in the record: its tool, plus ':<model>' when it runs a named model."""
    return f"{tool}:{model}" if model else str(tool)


def _time(value):
    try:
        t = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _entry(data: dict, key: str) -> dict:
    e = data.get(key) if isinstance(data, dict) else None
    return e if isinstance(e, dict) else {}


def live_bench(data: dict, key: str, at: _dt.datetime) -> dict | None:
    """The provider's bench ({reason, since, until}) while it is still running at `at`, else None."""
    b = _entry(data, key).get("bench")
    if not isinstance(b, dict):
        return None
    until = _time(b.get("until"))
    return b if until is not None and until > at else None


def fresh_ok(data: dict, key: str, at: _dt.datetime, ttl_minutes: float) -> bool:
    """True when the provider's last test request passed less than `ttl_minutes` before `at`."""
    e = _entry(data, key)
    t = _time(e.get("at"))
    return bool(e.get("ok") and t is not None and (at - t).total_seconds() < 60 * float(ttl_minutes))


def live_claim(data: dict, key: str, at: _dt.datetime) -> dict | None:
    """The in-flight claim ({by, since, until}) on the provider's test request while it lasts at `at`, else None."""
    c = _entry(data, key).get("claim")
    if not isinstance(c, dict):
        return None
    until = _time(c.get("until"))
    return c if until is not None and until > at else None


def prune(data: dict, at: _dt.datetime) -> None:
    """Drop benches and claims that have ended, so the record holds only what is still true."""
    for key in list(data):
        e = data[key]
        if isinstance(e, dict) and "bench" in e and live_bench(data, key, at) is None:
            e.pop("bench", None)
        if isinstance(e, dict) and "claim" in e and live_claim(data, key, at) is None:
            e.pop("claim", None)
            if not e:
                data.pop(key, None)


def claim(data: dict, key: str, by: str, at: _dt.datetime, ttl_minutes: float, timeout_sec: float):
    """The one decision to send a test request, made under the record's lock after looking at the record again.
    Returns (what, claim): 'benched' or 'fresh' (no request needed: use what is recorded), 'wait' (another lane's
    live claim, returned: its result is on its way), or 'claimed' (this lane `by` now holds the claim until
    `timeout_sec` from `at` and sends the request). Edits the data in place."""
    prune(data, at)
    if live_bench(data, key, at):
        return "benched", None
    if fresh_ok(data, key, at, ttl_minutes):
        return "fresh", None
    c = live_claim(data, key, at)
    if c is not None and c.get("by") != by:
        return "wait", c
    e = dict(_entry(data, key))
    e["claim"] = {"by": by, "since": at.isoformat(timespec="microseconds"),
                  "until": (at + _dt.timedelta(seconds=float(timeout_sec))).isoformat(timespec="microseconds")}
    data[key] = e
    return "claimed", e["claim"]


def release(data: dict, key: str, by: str) -> dict:
    """End the claim `by` holds on the provider (a no-op when it holds none). Returns the data."""
    e = _entry(data, key)
    c = e.get("claim")
    if isinstance(c, dict) and c.get("by") == by:
        e.pop("claim", None)
        if not e:
            data.pop(key, None)
    return data


def record_probe(data: dict, key: str, good: bool, detail: str, sent: _dt.datetime,
                 bench_reason: str | None = None, bench_until: _dt.datetime | None = None,
                 now: _dt.datetime | None = None, by: str | None = None) -> dict:
    """Record a test request's result, dated `sent` (when the request was sent, not when it came back). A passing
    one ends a bench (a plain failure is cleared by a later pass); a failing one starts a bench to `bench_until`
    when given (`now`, default `sent`, is when it begins). A result sent before the one already recorded is
    dropped (an older pass cannot erase a newer failure, nor an older failure bench over a newer pass). Ends the
    claim `by` holds, if any. Returns the data."""
    now = now or sent
    prune(data, now)
    e = dict(_entry(data, key))
    held = e.get("claim")
    if by is not None and isinstance(held, dict) and held.get("by") == by:
        e.pop("claim", None)
    stored = _time(e.get("at"))
    if stored is not None and stored > sent:
        if e:
            data[key] = e
        else:
            data.pop(key, None)
        return data
    e.update({"ok": bool(good), "at": sent.isoformat(timespec="microseconds"), "detail": detail})
    if good:
        e.pop("bench", None)
    elif bench_until is not None:
        e["bench"] = _bench(bench_reason or detail, now, bench_until)
    data[key] = e
    return data


def _bench(reason: str, at: _dt.datetime, until: _dt.datetime) -> dict:
    return {"reason": str(reason)[:300], "since": at.isoformat(timespec="microseconds"),
            "until": until.isoformat(timespec="microseconds")}


def clear(data: dict, key: str) -> dict:
    """Forget the provider's bench and its last test result, so every lane tests it again (reviewer-status --reset).
    Anything else the entry holds (a claim on a request in flight) stays; an entry left empty is dropped."""
    e = _entry(data, key)
    if e:
        for k in ("bench",) + TEST_KEYS:
            e.pop(k, None)
        if not e:
            data.pop(key, None)
    return data
