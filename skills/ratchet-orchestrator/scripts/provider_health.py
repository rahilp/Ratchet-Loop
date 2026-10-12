"""The shared provider health record: what Ratchet knows about each review provider, kept once per user.

One file, provider-health.json, sits directly in the per-user folder (RATCHET_HOME), so every project and lane on a
machine sees the same provider test results and benches. A provider is one entry no matter which project asked about
it, keyed by its tool plus its model ('codex', 'opencode:opencode-go/glm-5.3'):

    {"<key>": {"ok": true|false, "at": "<time the last test request was SENT>", "detail": "<what came back>",
               "bench": {"reason": "...", "since": "<time>", "until": "<time>", "quota": true},
               "claim": {"by": "<lane's token>", "since": "<time>", "until": "<time>"},
               "starts": [{"at": "<time a review round was opened with this provider>", "id": "<start id>"}, ...]}}

`bench` is present only while the provider is being skipped; it ends on its own at `until`. A bench with `quota` set
is a provider that said it is out of credits: it waits for the reset time the provider gave (or a short re-test), a
passing test request does not end it, and a real review that was sent after the bench began and succeeds does. `claim` is present only
while one lane's test request is on its way: other lanes wait for its result instead of sending their own, and a
claim whose lane never answered ends on its own at `until`. A result is dated by when its request was sent, and a
result older than the one already recorded never replaces it, so a slow answer that lands late cannot undo a newer
one.

`starts` lists when a review round was opened with the provider (any lane, any project), newest last, and only the
last STARTS_HOURS hours are kept. Each start has an id (the round's nonce plus the reviewer id, so a start belongs to
one reviewer in one round of one lane). Choosing a reviewer reads it to spread rounds: of two providers equally good
for a slot, the one with fewer recent starts (then the one started longest ago) reviews. A provider that was not really
used (no run of its CLI produced any output, or its slot was refilled or dropped in the same round before it
reviewed) gives back exactly its own start by that id (refund_start), once: a second refund finds nothing to take
back. A round replaced before anyone reviewed it keeps its starts.

This module is pure record logic: every function takes the record's data (a dict) and edits it in place, or answers a
question about it. It reads and writes no file. ratchet.py does that, only through update_record (under the record's
lock), so two lanes recording a result at the same moment both end up saved.
"""
from __future__ import annotations

import datetime as _dt
import re

FILE = "provider-health.json"
STARTS_HOURS = 6  # how long a review start counts as recent (and stays in the record)
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


def _start_time(s):
    """The time of one stored start: {"at", "id"}, or a bare time string written before starts had ids."""
    return _time(s.get("at") if isinstance(s, dict) else s)


def record_start(data: dict, key: str, at: _dt.datetime, sid: str) -> dict:
    """Note that a review round was opened with the provider at `at` (start id `sid`), and forget starts older than
    STARTS_HOURS. A start with this id already there is not added twice. Edits the data in place and returns it."""
    e = dict(_entry(data, key))
    old = e.get("starts") or []
    keep = [s for s in old if (_start_time(s) or at) > at - _dt.timedelta(hours=STARTS_HOURS)]
    if not any(isinstance(s, dict) and s.get("id") == sid for s in keep):
        keep.append({"at": at.isoformat(timespec="microseconds"), "id": sid})
    e["starts"] = keep
    data[key] = e
    return data


def refund_start(data: dict, key: str, sid: str) -> dict:
    """Take back the start with id `sid`, and only that one: a provider that was not really used does not count as
    used. A no-op when there is no such start (already taken back, expired, or never saved). Returns the data."""
    e = _entry(data, key)
    starts = list(e.get("starts") or [])
    left = [s for s in starts if not (isinstance(s, dict) and s.get("id") == sid)]
    if len(left) != len(starts):
        if left:
            e["starts"] = left
        else:
            e.pop("starts", None)
            if not e:
                data.pop(key, None)
    return data


def recent_starts(data: dict, key: str, at: _dt.datetime) -> list:
    """The times (datetimes, oldest first) a review round was opened with the provider in the last STARTS_HOURS
    hours before `at`."""
    times = [_start_time(s) for s in (_entry(data, key).get("starts") or [])]
    return sorted(t for t in times if t is not None and t > at - _dt.timedelta(hours=STARTS_HOURS))


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
                 now: _dt.datetime | None = None, by: str | None = None, quota: bool = False) -> dict:
    """Record a test request's result, dated `sent` (when the request was sent, not when it came back). A passing
    one ends a bench (a plain failure is cleared by a later pass); a failing one starts a bench to `bench_until`
    when given (`now`, default `sent`, is when it begins). A result sent before the one already recorded is
    dropped (an older pass cannot erase a newer failure, nor an older failure bench over a newer pass). Ends the
    claim `by` holds, if any. `quota`: the failure is a provider out of credits, so the bench is marked as a quota
    bench: a passing result does not end a quota bench that is still running, and a later quota failure keeps the
    later of the two end times. Returns the data."""
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
    held_quota = live_bench(data, key, now) if (_entry(data, key).get("bench") or {}).get("quota") else None
    if good:
        if held_quota is None:
            e.pop("bench", None)
    elif bench_until is not None:
        if quota and held_quota is not None:
            bench_until = max(bench_until, _time(held_quota.get("until")) or bench_until)
        if quota or held_quota is None:
            e["bench"] = _bench(bench_reason or detail, now, bench_until, quota)
    data[key] = e
    return data


def _bench(reason: str, at: _dt.datetime, until: _dt.datetime, quota: bool = False) -> dict:
    b = {"reason": str(reason)[:300], "since": at.isoformat(timespec="microseconds"),
         "until": until.isoformat(timespec="microseconds")}
    if quota:
        b["quota"] = True
    return b


def end_quota_bench(data: dict, key: str, sent: _dt.datetime) -> bool:
    """A real review by the provider succeeded: end its quota bench (a bench of any other kind stays), but only a
    bench that began before the review was sent (`sent`): a review already under way when another lane found the
    provider out of credits proves nothing about the reset. Returns True when a bench was ended."""
    e = _entry(data, key)
    b = e.get("bench")
    began = _time(b.get("since")) if isinstance(b, dict) else None
    if isinstance(b, dict) and b.get("quota") and began is not None and began < sent:
        e.pop("bench", None)
        return True
    return False


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


# ---- recognising a provider that is out of credits, from its own output ----------------------------------------

_QUOTA_RE = re.compile(r"hit your usage limit|usage limit reached|out of credits", re.I)
_RESET_RE = re.compile(r"(?:it )?will reset in", re.I)  # loose: only trusted on stderr, the CLI's own error channel
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DATE_RE = re.compile(r"try again at\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\s+"
                      r"(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])", re.I)
_CLOCK_RE = re.compile(r"try again at\s+(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])", re.I)
_IN_RE = re.compile(r"(?:try again in|resets? in)\s+([^.\n]{0,60})", re.I)
_UNIT_RE = re.compile(r"(\d+)\s*(days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)\b", re.I)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_TAIL_LINES = 30  # only the end of each output is read: a CLI that echoes the prompt first must not be misread


def _hour24(hour: int, ampm: str) -> int:
    return hour % 12 + (12 if ampm.lower() == "pm" else 0)


def quota_failure(texts, now: _dt.datetime, retest_minutes: float = 20):
    """If a provider's own output (`texts`: its stderr first, then its stdout and reply file, each read from its last
    lines up) says it is out of credits (the loose "It will reset in" wording counts on stderr only, so a reply that
    merely quotes it never benches anyone), returns (until, line): `until` is when it said it is back (UTC; "try again at <date or
    clock time>" read in the local zone, or "in <duration>" / "It will reset in <duration>" counted from `now`), or
    `retest_minutes` from `now` when it gave no time or one that is already past (a clock time earlier today
    included). None when the output is not about quota."""
    for i, text in enumerate(texts if isinstance(texts, (list, tuple)) else [texts]):
        lines = [x for x in str(text or "").splitlines() if x.strip()][-_TAIL_LINES:]
        hits = [x for x in lines if _QUOTA_RE.search(x) or (i == 0 and _RESET_RE.search(x))]
        if not hits:
            continue
        tail = "\n".join(lines)
        reset = _parse_reset(tail, now)
        if reset is None or reset <= now:
            reset = now + _dt.timedelta(minutes=float(retest_minutes))
        return reset, hits[-1].strip()[:200]
    return None


def _parse_reset(text: str, now: _dt.datetime):
    m = _DATE_RE.search(text)
    try:
        if m and m.group(1).lower() in _MONTHS:
            naive = _dt.datetime(int(m.group(3)), _MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)),
                                 _hour24(int(m.group(4)), m.group(6)), int(m.group(5) or 0))
            return naive.astimezone(_dt.timezone.utc)  # a naive time is read in the local zone (TZ)
        m = _CLOCK_RE.search(text)
        if m:
            today = now.astimezone().date()
            naive = _dt.datetime(today.year, today.month, today.day, _hour24(int(m.group(1)), m.group(3)),
                                 int(m.group(2) or 0))
            return naive.astimezone(_dt.timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    m = _IN_RE.search(text)
    if m:
        secs = sum(int(n) * _UNIT_SECONDS[u[0].lower()] for n, u in _UNIT_RE.findall(m.group(1)))
        if secs > 0:
            return now + _dt.timedelta(seconds=secs)
    return None
