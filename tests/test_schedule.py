#!/usr/bin/env python3
"""
Schedule awareness tests (projected finish, stall warning, critical path).   python3 tests/test_schedule.py

One file, one clearly delimited section per checkpoint. CP1 (this section) tests two pure helpers in
skills/ratchet-orchestrator/scripts/status_text.py, in-process, with an injected `now` (no sleeping, no git, no
subprocess, no disk):

  schedule_numbers(st, plan_ids, cfg, now) -> dict
      `st` is the run's state.json dict, `plan_ids` the plan's ordered step ids, `cfg` the config dict and `now` a
      timezone-aware datetime (UTC). The result dict pins these keys:
        label                'none' (no finished step) | 'rough' (1 or 2) | 'estimate' (3 or more)
        finished             how many finished steps the numbers rest on (status done with both timestamps read and a
                             span that is not negative)
        left                 steps left, the current step included, not-started steps included
        finish               projected finish as UTC ISO text, or None
        earliest, latest     the range ends (UTC ISO text) from the fastest to the slowest finished step, or None
        avg_rounds           mean gates.adversarial.attempts per finished step (a missing record counts as 0)
        expected_rounds_left avg_rounds x left
        allowed_rounds_left  max(0, max_adversarial_rounds_run - adv_rounds_total)
        sources              list of the state.json / config field names the numbers came from
        text                 one plain line
      finish = now + (left - 1 steps not started) x mean span + max(0, mean span - current step elapsed); earliest and
      latest use the fastest and the slowest finished span in place of the mean; every clock time is rounded to a
      multiple of 5 minutes.

  step_stall(st, cfg, now) -> None | dict
      None when nothing is stalled, else a dict with cp, step, elapsed_seconds, since (UTC ISO text of the moment the
      stall clock started), budget_minutes, note ('' or None unless the budget setting was ignored) and text.

The runner at the bottom picks up every case registered with @case("CPn-Tnn"): later checkpoints append their own
section ABOVE the runner block, keep the shared helper block as it is, and add no new `main`.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import os
import re
import shutil
import sys
import time
import traceback
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
STATUS_TEXT_PATH = SCRIPTS / "status_text.py"
README_PATH = ROOT / "README.md"
INVENTORY_PATH = HERE / "fixtures" / "modularize_cp1" / "test-inventory.md"
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402

PASS = FAIL = 0
CASE_STATUS = {}
CASE_MESSAGES = {}
CASES = []
CUR = ["?"]


# ================================================================================================ shared helpers

def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    cid = CUR[0]
    CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
    if not cond:
        CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'ok ' if cond else 'FAIL'} [{cid}] {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def case(cid):
    def deco(fn):
        CASES.append((cid, fn))
        return fn
    return deco


_MOD = []


def mod():
    """status_text.py loaded by file path (the way the other suites load engine modules)."""
    if not _MOD:
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location("status_text_schedule_under_test", STATUS_TEXT_PATH)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MOD.append(m)
    return _MOD[0]


def run(fn_name, *args):
    """(True, result) or (False, None) after recording a failed check: the helper is missing or raised."""
    fn = getattr(mod(), fn_name, None)
    if not callable(fn):
        check(f"status_text.py defines {fn_name}()", False, "not defined yet")
        return False, None
    try:
        return True, fn(*args)
    except Exception as e:  # a raise is a failed check for the case, never a crash of the whole file
        check(f"{fn_name}() does not raise", False, f"{type(e).__name__}: {e}")
        return False, None


def sched(st, ids, cfg, now):
    return run("schedule_numbers", st, ids, cfg, now)


def stall(st, cfg, now):
    return run("step_stall", st, cfg, now)


NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
CFG = {"max_adversarial_rounds_run": 50}
NO_GATE = "no adversarial gate entry at all"
NO_ATTEMPTS = "adversarial entry without attempts"


def M(n):
    return timedelta(minutes=n)


def iso(dt):
    return dt.isoformat()


def pt(s):
    """A UTC ISO text read back as an aware datetime; None when it is not text, not ISO or not in UTC."""
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None or d.utcoffset() != timedelta(0):
        return None
    return d


def build(now=NOW, spans=(), attempts=None, cur=None, total=5, phase="building", adv_total=0, history=None,
          fmt=iso, cur_step="tests"):
    """(state, plan_ids): `spans` finished steps CP1.. (each ended 5 minutes before now and lasted the span), then
    the current step (started `cur` before now) when `cur` is given, then not-started steps up to `total`."""
    ids = [f"CP{i}" for i in range(1, total + 1)]
    cps, current = {}, None
    for i, cid in enumerate(ids):
        gates = {"behavior": {"required": True, "status": "pending", "attempts": 0},
                 "adversarial": {"required": True, "status": "pending", "attempts": 0}}
        c = {"status": "pending", "step": "tests", "gates": gates, "started_at": None, "completed_at": None}
        if i < len(spans):
            done_at = now - M(5)
            c.update(status="done", step="complete", started_at=fmt(done_at - spans[i]), completed_at=fmt(done_at))
            a = attempts[i] if attempts is not None else 1
            if a == NO_GATE:
                del gates["adversarial"]
            elif a == NO_ATTEMPTS:
                del gates["adversarial"]["attempts"]
            else:
                gates["adversarial"]["attempts"] = a
        elif i == len(spans) and cur is not None:
            c.update(status="in_progress", step=cur_step, started_at=fmt(now - cur))
            current = cid
        cps[cid] = c
    st = {"phase": phase, "current": current, "checkpoints": cps, "adv_rounds_total": adv_total,
          "history": list(history or [])}
    return st, ids


def hist(now, ago, event, cp="CP2"):
    e = {"at": iso(now - ago), "event": event}
    if cp is not None:
        e["cp"] = cp
    return e


def stall_state(now=NOW, started_ago=M(100), cp="CP2", step="adversarial", phase="building", history=None):
    done = {"status": "done", "step": "complete", "started_at": iso(now - M(300)),
            "completed_at": iso(now - M(200)), "gates": {}}
    cur = {"status": "in_progress", "step": step, "started_at": iso(now - started_ago), "gates": {}}
    return {"phase": phase, "current": cp, "checkpoints": {"CP1": done, cp: cur}, "history": list(history or [])}


def has_field(obj, tokens):
    """Whether a source name such as checkpoints[].gates.adversarial.attempts is a field `obj` really has."""
    if not tokens:
        return True
    t = tokens[0]
    if t.endswith("[]"):
        inner = obj.get(t[:-2]) if isinstance(obj, dict) else None
        items = list(inner.values()) if isinstance(inner, dict) else (inner if isinstance(inner, list) else [])
        return any(has_field(i, tokens[1:]) for i in items)
    return isinstance(obj, dict) and t in obj and has_field(obj[t], tokens[1:])


def source_exists(name, st, cfg):
    name = name[len("config."):] if name.startswith("config.") else name
    toks = name.split(".")
    return has_field(st, toks) or has_field(cfg, toks)


def minute_tokens(text):
    return [int(x) for x in re.findall(r"(?<![\w:])(\d+)\s*(?:m|min|mins|minutes?)\b", text)]


# ================================================================================================ CP1 section
# CP1: the schedule numbers and the stall check, as pure helpers in status_text.py.

@case("CP1-T01")
def test_CP1_T01_no_finished_step_means_no_estimate_yet():
    st, ids = build(cur=M(20))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    check("returns a dict", isinstance(r, dict), r)
    r = r if isinstance(r, dict) else {}
    check("label is 'none'", r.get("label") == "none", r.get("label"))
    check("no finish time", r.get("finish") is None, r.get("finish"))
    check("no range", r.get("earliest") is None and r.get("latest") is None, (r.get("earliest"), r.get("latest")))
    check("finished is 0", r.get("finished") == 0, r.get("finished"))
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("text says 'no estimate yet'", "no estimate yet" in text.lower(), text)
    check("text names the number of finished steps (0)", "0 finished steps" in text, text)


@case("CP1-T02")
def test_CP1_T02_one_finished_step_is_rough_and_says_so():
    st, ids = build(spans=(M(60),), cur=M(20))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("label is 'rough'", r.get("label") == "rough", r.get("label"))
    check("finished is 1", r.get("finished") == 1, r.get("finished"))
    check("left is 4 (the current step and three not started)", r.get("left") == 4, r.get("left"))
    check("finish = now + 40m left on the current step + 3 x 60m",
          pt(r.get("finish")) == NOW + M(40) + 3 * M(60), r.get("finish"))
    check("text contains the word rough", "rough" in text.lower(), text)
    check("text says it rests on 1 finished step (singular)",
          "1 finished step" in text and "1 finished steps" not in text, text)


@case("CP1-T03")
def test_CP1_T03_two_finished_steps_are_still_rough():
    st, ids = build(spans=(M(60), M(60)), cur=M(20))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("label is 'rough'", r.get("label") == "rough", r.get("label"))
    check("finished is 2", r.get("finished") == 2, r.get("finished"))
    check("finish = now + 40m + 2 x 60m", pt(r.get("finish")) == NOW + M(40) + 2 * M(60), r.get("finish"))
    check("text says it rests on 2 finished steps", "2 finished steps" in text, text)


@case("CP1-T04")
def test_CP1_T04_three_finished_steps_is_an_estimate_not_rough():
    st, ids = build(spans=(M(30), M(60), M(90)), cur=M(20))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("label is 'estimate'", r.get("label") == "estimate", r.get("label"))
    check("finished is 3", r.get("finished") == 3, r.get("finished"))
    check("text does not contain the word rough", "rough" not in text.lower(), text)
    check("text says it rests on 3 finished steps", "3 finished steps" in text, text)


@case("CP1-T05")
def test_CP1_T05_projected_finish_is_now_plus_steps_left_at_the_average():
    st, ids = build(spans=(M(30), M(60), M(90)), cur=M(20))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    check("left is 2 (the current step and CP5)", r.get("left") == 2, r.get("left"))
    check("finish is 13:40 UTC (12:00 + 40m + 60m)", pt(r.get("finish")) == NOW + M(100), r.get("finish"))
    check("earliest is 12:40 UTC (12:00 + 10m + 30m)", pt(r.get("earliest")) == NOW + M(40), r.get("earliest"))
    check("latest is 14:40 UTC (12:00 + 70m + 90m)", pt(r.get("latest")) == NOW + M(160), r.get("latest"))


@case("CP1-T06")
def test_CP1_T06_a_current_step_past_the_average_subtracts_nothing_negative():
    st, ids = build(spans=(M(30), M(60), M(90)), cur=M(100))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    fin, lo, hi = pt(r.get("finish")), pt(r.get("earliest")), pt(r.get("latest"))
    check("finish is now + only the 60m for CP5 (13:00 UTC)", fin == NOW + M(60), r.get("finish"))
    check("finish is never earlier than now", fin is not None and fin >= NOW, r.get("finish"))
    check("range ends are never earlier than now", lo is not None and hi is not None and lo >= NOW and hi >= NOW,
          (r.get("earliest"), r.get("latest")))
    check("range brackets the finish", lo is not None and hi is not None and fin is not None and lo <= fin <= hi,
          (lo, fin, hi))


@case("CP1-T07")
def test_CP1_T07_nothing_is_shown_finer_than_five_minutes():
    start = datetime(2026, 11, 1, 11, 41, 17, tzinfo=timezone.utc)
    base = datetime(2026, 11, 1, 12, 3, 41, tzinfo=timezone.utc)
    spans = (timedelta(minutes=37, seconds=13), timedelta(minutes=41, seconds=52), timedelta(minutes=58, seconds=7))
    bad_clock, bad_text, n, results = [], [], 0, 0
    for k in range(0, 201):
        now = base + k * timedelta(minutes=7, seconds=11)
        st, ids = build(now=now, spans=spans, cur=M(20))
        st["checkpoints"]["CP4"]["started_at"] = iso(start)
        ok, r = sched(st, ids, CFG, now)
        if not ok:
            return
        n += 1
        r = r if isinstance(r, dict) else {}
        for key in ("finish", "earliest", "latest"):
            d = pt(r.get(key))
            if d is None or d.minute % 5 or d.second or d.microsecond:
                bad_clock.append((k, key, r.get(key)))
        text = r.get("text") if isinstance(r.get("text"), str) else ""
        toks = minute_tokens(text)
        if any(t % 5 for t in toks) or re.search(r"\d\d:\d\d:\d\d|(?<![\w:])\d+\s*(?:s|sec|secs|seconds?)\b", text):
            bad_text.append((k, text))
        results += 1
    check("the helper ran for every injected now", n == 201 and results == 201, n)
    check("finish, earliest and latest always sit on a 5-minute mark with zero seconds", not bad_clock, bad_clock[:3])
    check("durations written in the text are whole multiples of 5 minutes, never seconds", not bad_text, bad_text[:2])


@case("CP1-T08")
def test_CP1_T08_a_range_is_always_given_with_an_estimate():
    st, ids = build(spans=(M(60),), cur=M(20))
    ok, r1 = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r1 = r1 if isinstance(r1, dict) else {}
    lo, hi = pt(r1.get("earliest")), pt(r1.get("latest"))
    check("one finished step: a range is present", lo is not None and hi is not None, (lo, hi))
    check("one finished step: both ends are the same time", lo == hi, (lo, hi))
    st, ids = build(spans=(M(30), M(60), M(90)), cur=M(20))
    ok, r3 = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r3 = r3 if isinstance(r3, dict) else {}
    lo, hi = pt(r3.get("earliest")), pt(r3.get("latest"))
    check("three finished steps: a range is present", lo is not None and hi is not None, (lo, hi))
    check("three finished steps: the earliest is before the latest", lo is not None and hi is not None and lo < hi,
          (lo, hi))


@case("CP1-T09")
def test_CP1_T09_expected_rounds_are_average_times_steps_left():
    st, ids = build(spans=(M(60),) * 4, attempts=(1, 2, 2, 3), cur=M(20), total=6, adv_total=14)
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("left is 2", r.get("left") == 2, r.get("left"))
    check("avg_rounds is 2.0", r.get("avg_rounds") == 2.0, r.get("avg_rounds"))
    check("expected_rounds_left is 4", r.get("expected_rounds_left") == 4, r.get("expected_rounds_left"))
    check("allowed_rounds_left is 36 (50 - 14)", r.get("allowed_rounds_left") == 36, r.get("allowed_rounds_left"))
    check("text states expected against allowed side by side (4 ... 36)",
          re.search(r"(?<![\w.])4(?![\w.]).*(?<![\w.])36(?![\w.])", text, re.S) is not None, text)
    check("text does not claim the rounds go past the limit when they fit", "more than" not in text.lower(), text)


@case("CP1-T10")
def test_CP1_T10_says_plainly_when_expected_rounds_will_not_fit():
    st, ids = build(spans=(M(60),) * 3, attempts=(3, 3, 3), cur=M(20), total=7, adv_total=45)
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("left is 4", r.get("left") == 4, r.get("left"))
    check("expected_rounds_left is 12", r.get("expected_rounds_left") == 12, r.get("expected_rounds_left"))
    check("allowed_rounds_left is 5", r.get("allowed_rounds_left") == 5, r.get("allowed_rounds_left"))
    check("text says the expected rounds are more than the limit still allows",
          "more than" in text.lower() and "limit" in text.lower(), text)
    check("text does not say it fits", "fits" not in text.lower(), text)


@case("CP1-T11")
def test_CP1_T11_missing_adversarial_records_count_as_zero_rounds():
    st, ids = build(spans=(M(60),) * 3, attempts=(NO_GATE, NO_ATTEMPTS, 2), cur=M(20), adv_total=60)
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    check("all three still count as finished", r.get("finished") == 3, r.get("finished"))
    avg = r.get("avg_rounds")
    check("average rounds is 2/3", isinstance(avg, (int, float)) and abs(avg - 2 / 3) < 0.01, avg)
    check("allowed_rounds_left never goes below 0 when adv_rounds_total is above the limit",
          r.get("allowed_rounds_left") == 0, r.get("allowed_rounds_left"))


@case("CP1-T12")
def test_CP1_T12_the_result_names_the_fields_it_came_from():
    wanted = ("checkpoints[].started_at", "checkpoints[].completed_at", "checkpoints[].gates.adversarial.attempts",
              "adv_rounds_total", "max_adversarial_rounds_run")
    states = {
        "T05 state": build(spans=(M(30), M(60), M(90)), cur=M(20)),
        "T09 state": build(spans=(M(60),) * 4, attempts=(1, 2, 2, 3), cur=M(20), total=6, adv_total=14),
    }
    for label, (st, ids) in states.items():
        ok, r = sched(st, ids, CFG, NOW)
        if not ok:
            return
        src = r.get("sources") if isinstance(r, dict) else None
        check(f"{label}: sources is a non-empty list of text names",
              isinstance(src, list) and bool(src) and all(isinstance(s, str) for s in src), src)
        src = src if isinstance(src, list) else []
        for w in wanted:
            check(f"{label}: sources names {w}", w in src, src)
        invented = [s for s in src if not (isinstance(s, str) and source_exists(s, st, CFG))]
        check(f"{label}: every name cited is a field the fixture really has", not invented, invented)
    st, ids = build(cur=M(20))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    src = r.get("sources") if isinstance(r, dict) else None
    check("with no finished step it still names the fields it looked at",
          isinstance(src, list) and "checkpoints[].started_at" in src and "checkpoints[].completed_at" in src, src)
    invented = [s for s in (src if isinstance(src, list) else []) if not source_exists(str(s), st, CFG)]
    check("with no finished step every name cited is still a real field", not invented, invented)


@case("CP1-T13")
def test_CP1_T13_text_says_wall_clock_including_waits_in_utc():
    zones = r"\b(EST|EDT|CST|CDT|MST|MDT|PST|PDT|BST|CET|CEST|IST|GMT|local time)\b|[+-]\d\d:?\d\d\b"
    for label, spans in (("one finished step", (M(60),)), ("three finished steps", (M(30), M(60), M(90)))):
        st, ids = build(spans=spans, cur=M(20))
        ok, r = sched(st, ids, CFG, NOW)
        if not ok:
            return
        text = r.get("text") if isinstance(r, dict) and isinstance(r.get("text"), str) else ""
        check(f"{label}: text says the step lengths are wall-clock", re.search(r"wall[- ]clock", text, re.I) is not None,
              text)
        check(f"{label}: text says waits inside a step are included", "wait" in text.lower(), text)
        check(f"{label}: text shows the finish with the word UTC", "UTC" in text, text)
        check(f"{label}: text prints no local time zone name or offset", re.search(zones, text) is None, text)
        check(f"{label}: text is one plain line", "\n" not in text.strip() and bool(text.strip()), repr(text))


@case("CP1-T14")
def test_CP1_T14_damaged_timestamps_degrade_without_a_crash():
    def pop(key):
        return lambda c: c.pop(key, None)

    def put(key, value):
        return lambda c: c.__setitem__(key, value)

    damages = (
        ("completed_at missing", pop("completed_at")),
        ("started_at missing", pop("started_at")),
        ("completed_at garbage", put("completed_at", "garbage")),
        ("completed_at before started_at", lambda c: c.__setitem__("completed_at", iso(pt(c["started_at"]) - M(1)))),
        ("completed_at a number", put("completed_at", 12345)),
        ("completed_at null", put("completed_at", None)),
        ("started_at garbage", put("started_at", "garbage")),
    )
    for label, dmg in damages:
        # only 'finished' step is damaged
        st, ids = build(spans=(M(60),), cur=M(20), total=3)
        dmg(st["checkpoints"]["CP1"])
        ok, r = sched(st, ids, CFG, NOW)
        if not ok:
            continue
        r = r if isinstance(r, dict) else {}
        check(f"{label}: the damaged step is not counted (finished 0, label 'none')",
              r.get("finished") == 0 and r.get("label") == "none", (r.get("finished"), r.get("label")))
        check(f"{label}: the run says 'no estimate yet'", "no estimate yet" in str(r.get("text")).lower(), r.get("text"))
        # a good finished step next to a bad one still counts
        st, ids = build(spans=(M(60), M(60)), cur=M(20), total=4)
        dmg(st["checkpoints"]["CP2"])
        ok, r = sched(st, ids, CFG, NOW)
        if not ok:
            continue
        r = r if isinstance(r, dict) else {}
        check(f"{label}: next to a good finished step it counts 1 and is labelled rough",
              r.get("finished") == 1 and r.get("label") == "rough", (r.get("finished"), r.get("label")))
    for label, value in (("garbage", "garbage"), ("a number", 99), ("null", None), ("missing", "<pop>")):
        st, ids = build(spans=(M(60),), cur=M(20), total=3)
        if value == "<pop>":
            st["checkpoints"]["CP2"].pop("started_at")
        else:
            st["checkpoints"]["CP2"]["started_at"] = value
        ok, r = sched(st, ids, CFG, NOW)
        if not ok:
            continue
        r = r if isinstance(r, dict) else {}
        text = r.get("text") if isinstance(r.get("text"), str) else ""
        fin = pt(r.get("finish"))
        check(f"current step started_at {label}: the finish is still computed", fin is not None and fin >= NOW,
              r.get("finish"))
        check(f"current step started_at {label}: label rough, 1 finished", r.get("label") == "rough" and
              r.get("finished") == 1, (r.get("label"), r.get("finished")))
        check(f"current step started_at {label}: the text says the current step's start could not be used",
              "current step" in text.lower() and re.search(r"unreadable|unknown|not (?:counted|known|readable)|"
                                                           r"no readable", text.lower()) is not None, text)
    for label, st in (("no checkpoints key", {}), ("checkpoints null", {"phase": "building", "checkpoints": None}),
                      ("only a phase", {"phase": "building"})):
        for ids in ([], ["CP1", "CP2"]):
            ok, r = sched(st, ids, CFG, NOW)
            if not ok:
                continue
            r = r if isinstance(r, dict) else {}
            check(f"{label} (plan of {len(ids)}): no crash, no finished step, label 'none'",
                  r.get("finished") == 0 and r.get("label") == "none", (r.get("finished"), r.get("label")))


@case("CP1-T15")
def test_CP1_T15_every_timestamp_spelling_reads_the_same():
    plus2 = timezone(timedelta(hours=2))
    fmts = (
        ("Z suffix", lambda d: d.strftime("%Y-%m-%dT%H:%M:%S") + "Z"),
        ("+00:00", lambda d: d.isoformat()),
        ("+02:00 offset", lambda d: d.astimezone(plus2).isoformat()),
        ("naive", lambda d: d.replace(tzinfo=None).isoformat()),
        ("fractional seconds", lambda d: d.strftime("%Y-%m-%dT%H:%M:%S") + ".123456+00:00"),
    )
    seen = []
    for label, f in fmts:
        st, ids = build(spans=(M(30), M(60), M(90)), cur=M(20), fmt=f)
        ok, r = sched(st, ids, CFG, NOW)
        if not ok:
            return
        r = r if isinstance(r, dict) else {}
        fin = pt(r.get("finish"))
        check(f"{label}: finish is UTC text", fin is not None, r.get("finish"))
        seen.append(fin)
        check(f"{label}: finish is 13:40 UTC", fin == NOW + M(100), r.get("finish"))
        check(f"{label}: label estimate on 3 finished steps", r.get("label") == "estimate" and r.get("finished") == 3,
              (r.get("label"), r.get("finished")))
    check("all spellings give the same finish instant", len(set(seen)) == 1, seen)


@case("CP1-T16")
def test_CP1_T16_nothing_left_is_said_instead_of_dividing_by_zero():
    st, ids = build(spans=(M(30), M(60), M(90)), total=3, phase="awaiting_final_review", attempts=(1, 2, 3))
    ok, r = sched(st, ids, CFG, NOW)
    if not ok:
        return
    r = r if isinstance(r, dict) else {}
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("left is 0", r.get("left") == 0, r.get("left"))
    check("expected_rounds_left is 0", r.get("expected_rounds_left") == 0, r.get("expected_rounds_left"))
    check("no future finish time", r.get("finish") is None, r.get("finish"))
    check("the text says nothing is left", re.search(r"nothing (?:is )?left|no steps? (?:are )?left|0 steps? left",
                                                     text.lower()) is not None, text)
    check("the text shows no clock time", re.search(r"\d\d:\d\d", text) is None, text)


@case("CP1-T17")
def test_CP1_T17_helpers_only_read_and_use_the_injected_clock():
    states = [build(spans=(M(30), M(60), M(90)), cur=M(20)), build(spans=(M(60),), cur=M(20), total=3),
              build(cur=M(20))]
    damaged, ids = build(spans=(M(60),), cur=M(20), total=3)
    damaged["checkpoints"]["CP1"]["completed_at"] = "garbage"
    damaged["checkpoints"]["CP2"]["started_at"] = 5
    damaged["history"] = ["junk", {"at": "garbage", "event": "behavior_passed", "cp": "CP2"}, None]
    states.append((damaged, ids))
    stall_states = [stall_state(), stall_state(history=[hist(NOW, M(5), "behavior_passed")]), {}, {"phase": "building"},
                    damaged]
    old_cwd, tmp = os.getcwd(), _tmp.mkdtemp(prefix="ratchet-schedule-purity-")
    try:
        os.chdir(tmp)
        m = mod()
        pre = getattr(m, "_ratchet_module", None)
        if callable(pre):
            pre()  # load the engine once now, outside the no-real-clock window
        with no_real_clock():
            for i, (st, ids) in enumerate(states):
                st0, cfg0 = copy.deepcopy(st), copy.deepcopy(CFG)
                ok, a = sched(st, ids, CFG, NOW)
                if not ok:
                    return
                ok, b = sched(st, ids, CFG, NOW)
                check(f"state {i}: schedule_numbers leaves the state dict untouched", st == st0)
                check(f"state {i}: schedule_numbers leaves the config dict untouched", CFG == cfg0)
                check(f"state {i}: the same inputs give the same output on a repeat call", a == b, (a, b))
            for i, st in enumerate(stall_states):
                st0, cfg0 = copy.deepcopy(st), {}
                cfg = {}
                ok, a = stall(st, cfg, NOW)
                if not ok:
                    return
                ok, b = stall(st, cfg, NOW)
                check(f"stall state {i}: step_stall leaves the state and config dicts untouched",
                      st == st0 and cfg == cfg0)
                check(f"stall state {i}: the same inputs give the same output on a repeat call", a == b, (a, b))
            later = NOW + timedelta(hours=3)
            st1, ids1 = build(now=NOW, spans=(M(30), M(60), M(90)), cur=M(20))
            st2, ids2 = build(now=later, spans=(M(30), M(60), M(90)), cur=M(20))
            ok, r1 = sched(st1, ids1, CFG, NOW)
            ok2, r2 = sched(st2, ids2, CFG, later)
            if not (ok and ok2):
                return
            f1, f2 = pt(r1.get("finish")), pt(r2.get("finish"))
            check("the finish moves exactly with the injected now", f1 is not None and f2 is not None and
                  f2 - f1 == timedelta(hours=3), (f1, f2))
    finally:
        os.chdir(old_cwd)
        left_over = os.listdir(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
    check("the working folder is still empty (nothing written)", left_over == [], left_over)


@contextlib.contextmanager
def no_real_clock():
    """status_text's datetime module alias and time.time() raise while the helpers run: only `now` may be used."""
    m = mod()
    real_dt, real_time, real_ns = getattr(m, "_dt", None), time.time, time.time_ns

    def boom(*a, **k):
        raise AssertionError("the real clock was read")

    if real_dt is not None:
        class NoClock(real_dt.datetime):
            now = utcnow = today = classmethod(lambda cls, *a, **k: boom())

        fake = types.SimpleNamespace(**{k: getattr(real_dt, k) for k in dir(real_dt) if not k.startswith("__")})
        fake.datetime = NoClock
        m._dt = fake
    time.time, time.time_ns = boom, boom
    try:
        yield
    finally:
        time.time, time.time_ns = real_time, real_ns
        if real_dt is not None:
            m._dt = real_dt


def _stalled(r, seconds=6000):
    return isinstance(r, dict) and r.get("elapsed_seconds") == seconds


@case("CP1-T18")
def test_CP1_T18_a_building_step_with_no_progress_past_90_minutes_is_stalled():
    st = stall_state(started_ago=M(100))
    ok, r = stall(st, {}, NOW)
    if not ok:
        return
    check("a stall dict is returned", isinstance(r, dict), r)
    r = r if isinstance(r, dict) else {}
    text = r.get("text") if isinstance(r.get("text"), str) else ""
    check("names CP2", r.get("cp") == "CP2", r.get("cp"))
    check("names the step", r.get("step") == "adversarial", r.get("step"))
    check("elapsed_seconds is 6000 (1h 40m)", r.get("elapsed_seconds") == 6000, r.get("elapsed_seconds"))
    check("budget_minutes is 90", r.get("budget_minutes") == 90, r.get("budget_minutes"))
    check("since is the step's start, as UTC text", pt(r.get("since")) == NOW - M(100), r.get("since"))
    check("text names the checkpoint", "CP2" in text, text)
    check("text names the step in plain words", "getting a second opinion" in text, text)
    check("text names the time elapsed as 1h 40m", "1h 40m" in text, text)


@case("CP1-T19")
def test_CP1_T19_exactly_at_the_budget_is_not_a_stall_one_second_over_is():
    for label, ago, expect in (("89m59s", timedelta(minutes=89, seconds=59), None),
                               ("90m00s", timedelta(minutes=90), None),
                               ("90m01s", timedelta(minutes=90, seconds=1), 5401)):
        ok, r = stall(stall_state(started_ago=ago), {}, NOW)
        if not ok:
            return
        if expect is None:
            check(f"{label}: no stall", r is None, r)
        else:
            check(f"{label}: a stall is reported", _stalled(r, expect), r)


@case("CP1-T20")
def test_CP1_T20_new_gate_progress_restarts_the_stall_clock():
    ok, r = stall(stall_state(started_ago=M(180), history=[hist(NOW, M(40), "behavior_passed")]), {}, NOW)
    if not ok:
        return
    check("a progress event 40 minutes ago: no stall", r is None, r)
    ev_at = NOW - M(100)
    ok, r = stall(stall_state(started_ago=M(180), history=[hist(NOW, M(100), "behavior_passed")]), {}, NOW)
    if not ok:
        return
    check("the same event 100 minutes ago: a stall of 1h 40m", _stalled(r), r)
    r = r if isinstance(r, dict) else {}
    check("measured from the event, not from the step start", pt(r.get("since")) == ev_at, r.get("since"))
    check("text says 1h 40m", "1h 40m" in str(r.get("text")), r.get("text"))


@case("CP1-T21")
def test_CP1_T21_every_engine_progress_event_restarts_the_clock():
    m = mod()
    getter = getattr(m, "_ratchet_module", None)
    events = sorted(getter().PROGRESS_EVENTS) if callable(getter) else []
    check("the engine's PROGRESS_EVENTS is not empty (so the loop can fail)", len(events) > 0, events)
    for ev in events:
        ok, r = stall(stall_state(started_ago=M(180), history=[hist(NOW, M(10), ev)]), {}, NOW)
        if not ok:
            return
        check(f"progress event {ev}: no stall", r is None, r)


@case("CP1-T22")
def test_CP1_T22_non_progress_or_other_step_events_do_not_restart_the_clock():
    m = mod()
    getter = getattr(m, "_ratchet_module", None)
    prog = set(getter().PROGRESS_EVENTS) if callable(getter) else set()
    plain = [e for e in ("note", "blocked", "wait_started", "reviewer_chosen") if e not in prog
             and e not in ("resumed", "unblocked")]
    check("(fixture sanity) at least two non-progress event names exist", len(plain) >= 2, plain)
    for ev in plain:
        h = [hist(NOW, M(5), ev, cp="CP2"), hist(NOW, M(5), "behavior_passed", cp="CP1")]
        ok, r = stall(stall_state(started_ago=M(100), history=h), {}, NOW)
        if not ok:
            return
        check(f"a {ev} event for CP2 and a progress event for CP1 5 minutes ago: still a 1h 40m stall",
              _stalled(r), r)


@case("CP1-T23")
def test_CP1_T23_a_resume_or_an_unblock_restarts_the_stall_clock():
    for ev in ("resumed", "unblocked"):
        ok, r = stall(stall_state(started_ago=M(180), history=[hist(NOW, M(30), ev, cp=None)]), {}, NOW)
        if not ok:
            return
        check(f"{ev} 30 minutes ago: no stall", r is None, r)
        ok, r = stall(stall_state(started_ago=M(180), history=[hist(NOW, M(100), ev, cp=None)]), {}, NOW)
        if not ok:
            return
        check(f"{ev} 100 minutes ago: a stall of 1h 40m", _stalled(r), r)
        check(f"{ev} 100 minutes ago: measured from the {ev} entry",
              isinstance(r, dict) and pt(r.get("since")) == NOW - M(100), r)


@case("CP1-T24")
def test_CP1_T24_the_budget_is_read_from_stall_budget_minutes():
    st = stall_state(started_ago=M(45))
    for label, cfg, expect in (("30", {"stall_budget_minutes": 30}, 30), ("60", {"stall_budget_minutes": 60}, None),
                               ("unset", {}, None)):
        ok, r = stall(st, cfg, NOW)
        if not ok:
            return
        if expect is None:
            check(f"stall_budget_minutes {label}: no stall at 45 minutes", r is None, r)
        else:
            check(f"stall_budget_minutes {label}: a stall with budget_minutes {expect}",
                  isinstance(r, dict) and r.get("budget_minutes") == expect, r)


@case("CP1-T25")
def test_CP1_T25_a_bad_budget_falls_back_to_90_and_says_so():
    st = stall_state(started_ago=M(100))
    for label, val in (("true", True), ("false", False), ("'45'", "45"), ("0", 0), ("-10", -10), ("null", None),
                       ("a list", [30])):
        ok, r = stall(st, {"stall_budget_minutes": val}, NOW)
        if not ok:
            return
        check(f"budget {label}: 90 is used (the 1h 40m stall is reported)",
              _stalled(r) and r.get("budget_minutes") == 90, r)
        note = r.get("note") if isinstance(r, dict) else None
        check(f"budget {label}: a note says the setting was ignored and 90 used",
              isinstance(note, str) and "ignor" in note.lower() and "90" in note, note)
    ok, r = stall(st, {}, NOW)
    if not ok:
        return
    check("unset: the stall is reported with no note", _stalled(r) and not r.get("note"), r)


@case("CP1-T26")
def test_CP1_T26_no_stall_unless_the_run_is_building():
    for phase in ("awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review", "blocked",
                  "paused", "planning", "done", "cancelled"):
        ok, r = stall(stall_state(started_ago=M(300), phase=phase), {}, NOW)
        if not ok:
            return
        check(f"phase {phase}: no stall", r is None, r)
    ok, r = stall(stall_state(started_ago=M(300), phase="building"), {}, NOW)
    if not ok:
        return
    check("phase building: the same state is a stall", _stalled(r, 18000), r)


@case("CP1-T27")
def test_CP1_T27_damaged_times_or_no_current_step_never_crash_or_blame_the_wrong_step():
    def with_cp(**over):
        st = stall_state(started_ago=M(100))
        st["checkpoints"]["CP2"].update(over)
        return st

    nocur = stall_state(started_ago=M(100))
    nocur["current"] = None
    nokey = stall_state(started_ago=M(100))
    del nokey["current"]
    unknown = stall_state(started_ago=M(100))
    unknown["current"] = "CP9"
    nostart = with_cp()
    del nostart["checkpoints"]["CP2"]["started_at"]
    junk = {"at": "garbage", "event": "behavior_passed", "cp": "CP2"}
    good_event = hist(NOW, M(100), "behavior_passed")
    h_dict = stall_state(started_ago=M(100))
    h_dict["history"] = {"at": iso(NOW - M(5))}
    h_str = stall_state(started_ago=M(100))
    h_str["history"] = "garbage"
    h_none = stall_state(started_ago=M(100))
    h_none["history"] = None
    no_cps = stall_state(started_ago=M(100))
    no_cps["checkpoints"] = None
    cp_none = stall_state(started_ago=M(100))
    cp_none["checkpoints"]["CP2"] = None
    rows = (
        ("no current step (None)", nocur, None),
        ("no current key at all", nokey, None),
        ("current id not in checkpoints", unknown, None),
        ("checkpoints null", no_cps, None),
        ("current step record null", cp_none, None),
        ("started_at missing, no event", nostart, None),
        ("started_at garbage, no event", with_cp(started_at="garbage"), None),
        ("started_at a number, no event", with_cp(started_at=7), None),
        ("started_at garbage and only an unreadable event",
         dict(with_cp(started_at="garbage"), history=[junk]), None),
        ("started_at garbage, readable event 100m ago, plus an unreadable entry",
         dict(with_cp(started_at="garbage"), history=[junk, "junk", None, good_event]), 6000),
        ("an unreadable history entry is skipped, the start still counts",
         dict(stall_state(started_ago=M(100)), history=[junk]), 6000),
        ("history is a dict", h_dict, 6000),
        ("history is a string", h_str, 6000),
        ("history is null", h_none, 6000),
        ("a progress entry with no cp does not blame the current step",
         dict(stall_state(started_ago=M(100)), history=[{"at": iso(NOW - M(5)), "event": "behavior_passed"}]), 6000),
        ("non-dict entries are skipped",
         dict(stall_state(started_ago=M(100)), history=[None, 3, "x", []]), 6000),
    )
    for label, st, expect in rows:
        ok, r = stall(st, {}, NOW)
        if not ok:
            continue
        if expect is None:
            check(f"{label}: no stall claim", r is None, r)
        else:
            check(f"{label}: stall measured as {expect}s", _stalled(r, expect), r)


def _readme_test_lines():
    try:
        return README_PATH.read_text().splitlines()
    except OSError:
        return []


@case("CP1-T28")
def test_CP1_T28_readme_lists_the_new_test_file_in_order():
    lines = _readme_test_lines()
    check("(fixture sanity) README.md has a 'Running the tests' heading", any("Running the tests" in l for l in lines))
    mine = [i for i, l in enumerate(lines) if l.strip() == "python3 tests/test_schedule.py"]
    check("README has exactly one line 'python3 tests/test_schedule.py'", len(mine) == 1, mine)
    before = [i for i, l in enumerate(lines) if l.strip() == "python3 tests/test_scoped_diff_priority.py"]
    after = [i for i, l in enumerate(lines) if l.strip() == "python3 tests/test_shortcuts.py"]
    check("(fixture sanity) the neighbouring test_scoped_diff_priority.py and test_shortcuts.py lines each appear once",
          len(before) == 1 and len(after) == 1, (before, after))
    if len(mine) == 1 and len(before) == 1 and len(after) == 1:
        check("the new line sits between test_scoped_diff_priority.py and test_shortcuts.py",
              before[0] < mine[0] < after[0], (before, mine, after))
        head = [i for i, l in enumerate(lines) if "Running the tests" in l]
        check("it sits in the list under the 'Running the tests' heading", bool(head) and head[0] < mine[0],
              (head, mine))
        block = lines[before[0]:after[0] + 1]
        check("no blank line or prose breaks the list between its neighbours",
              all(l.strip().startswith("python3 tests/") for l in block), block[:6])


def _live_matches(pattern_bytes):
    """Every file under tests/ (recursive, __pycache__ and impact_analysis_* sample-data folders skipped, the
    inventory suite itself dropped) whose bytes match the pattern, as paths relative to tests/."""
    rx = re.compile(pattern_bytes)
    out = set()
    for dirpath, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith("impact_analysis_")]
        for f in files:
            p = Path(dirpath) / f
            if p.is_symlink() or p.name == "test_modularize_inventory.py":
                continue
            try:
                if rx.search(p.read_bytes()):
                    out.add(str(p.relative_to(HERE)))
            except OSError:
                pass
    return out


@case("CP1-T29")
def test_CP1_T29_inventory_lists_the_new_file_under_module_load():
    load = _live_matches(rb"spec_from_file_" + rb"location")
    text_kind = _live_matches(rb"ENGINE\.read_" + rb"text|a" + rb"st\.parse|a" + rb"st\.walk|get" + rb"source")
    check("(fixture sanity) test_schedule.py really matches the module-load grep", "test_schedule.py" in load, None)
    check("(fixture sanity) test_schedule.py matches no source-text grep (so it is Kind 1, module-load only)",
          "test_schedule.py" not in text_kind, None)
    try:
        inv = INVENTORY_PATH.read_text()
    except OSError:
        check("the inventory file exists", False, str(INVENTORY_PATH))
        return
    k1 = re.search(r"^## Kind 1\b.*?(?=^## Kind 2\b)", inv, re.S | re.M)
    check("(fixture sanity) the inventory has a Kind 1 section", k1 is not None)
    if k1 is None:
        return
    sect = k1.group(0)
    check("test_schedule.py is listed by name under Kind 1",
          re.search(r"(?<![A-Za-z0-9_])test_schedule\.py(?![A-Za-z0-9_])", sect) is not None)
    rest = inv.replace(sect, "")
    check("it is not also listed under another Kind",
          re.search(r"(?<![A-Za-z0-9_])test_schedule\.py(?![A-Za-z0-9_])", rest) is None)
    live_only_load = len(load - text_kind)
    m = re.search(r"never inspects its on-disk text directly\):\s*(\d+) files", sect)
    check("the Kind 1 heading's file count is the live count of module-load-only files",
          m is not None and int(m.group(1)) == live_only_load, (m.group(1) if m else None, live_only_load))


def _top_level_nodes(tree):
    """Top-level statements, including those inside top-level if/try/with blocks (not inside defs or classes)."""
    import ast
    out = []
    for n in tree.body:
        out.append(n)
        if isinstance(n, (ast.If, ast.Try, ast.With)):
            stack = [n]
            while stack:
                cur = stack.pop()
                for field in ("body", "orelse", "finalbody"):
                    for sub in getattr(cur, field, []) or []:
                        out.append(sub)
                        if isinstance(sub, (ast.If, ast.Try, ast.With)):
                            stack.append(sub)
                for h in getattr(cur, "handlers", []) or []:
                    for sub in h.body:
                        out.append(sub)
    return out


def _calls_name(node, name):
    import ast
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name) and cur.func.id == name:
            return True
        stack.extend(ast.iter_child_nodes(cur))
    return False


@case("CP1-T30")
def test_CP1_T30_no_new_module_and_no_module_level_collection():
    import ast
    src = STATUS_TEXT_PATH.read_text()
    tree = compile(src, "status_text.py", "exec", flags=ast.PyCF_ONLY_AST)
    defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    check("status_text.py defines schedule_numbers at the top level", "schedule_numbers" in defs, sorted(defs)[:5])
    check("status_text.py defines step_stall at the top level", "step_stall" in defs, sorted(defs)[:5])
    known = ("engine_utils.py", "fingerprint.py", "plan_support.py", "provider_discovery.py", "provider_health.py",
             "ratchet.py", "records.py", "run_records.py", "spec_sources.py", "status_text.py")
    now_modules = sorted(p.name for p in SCRIPTS.glob("*.py"))
    check("no new .py module in the scripts folder", now_modules == sorted(known), now_modules)
    offenders = []
    for n in _top_level_nodes(tree):
        v = n.value if isinstance(n, (ast.Assign, ast.AnnAssign)) else None
        if v is None:
            continue
        lit = isinstance(v, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp))
        call = (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id in ("dict", "list", "set", "Counter", "defaultdict", "OrderedDict"))
        if lit or call:
            offenders.append(ast.unparse(n)[:60])
    check("no module-level dict/list/set literal or dict()/list()/set() call", not offenders, offenders)
    imports = []
    for n in _top_level_nodes(tree):
        if isinstance(n, ast.Import):
            imports += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            imports.append(n.module or "")
    check("status_text.py does not import ratchet at module level",
          not [i for i in imports if i.split(".")[0] == "ratchet"], imports)
    stall_fn = defs.get("step_stall")
    check("step_stall reaches the engine only through the lazy _ratchet_module()",
          stall_fn is not None and _calls_name(stall_fn, "_ratchet_module"), None)


@case("CP1-X01")
def test_CP1_X01_stall_budget_states_a_bad_setting_even_while_under_budget():
    for label, val in (("'45'", "45"), ("0", 0), ("-10", -10), ("true", True), ("false", False), ("null", None)):
        ok, r = run("stall_budget", {"stall_budget_minutes": val})
        if not ok:
            return
        okr = isinstance(r, tuple) and len(r) == 2
        check(f"bad budget {label}: stall_budget() returns (minutes, note)", okr, r)
        if not okr:
            continue
        minutes, note = r
        check(f"bad budget {label}: 90 minutes are used", minutes == 90, r)
        check(f"bad budget {label}: the note says it was ignored and 90 used",
              isinstance(note, str) and "ignor" in note.lower() and "90" in note, note)
        # Under budget (10 minutes in): no stall fires, yet the note above is still available to the caller.
        ok, s = stall(stall_state(started_ago=M(10)), {"stall_budget_minutes": val}, NOW)
        if not ok:
            return
        check(f"bad budget {label}: an under-budget step is not a stall", s is None, s)
    ok, r = run("stall_budget", {"stall_budget_minutes": 45})
    if not ok:
        return
    check("good budget 45: (45, no note)", isinstance(r, tuple) and r[0] == 45 and not r[1], r)
    ok, r = run("stall_budget", {})
    if not ok:
        return
    check("absent key: (90, no note)", isinstance(r, tuple) and r[0] == 90 and not r[1], r)


# ================================================================================================ runner
# Keep this block last. Later checkpoints add their own section (with @case registrations) above it.

def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid, _ in CASES:
        ok = CASE_STATUS.get(cid, False)
        cases.append({"id": cid, "status": "passed" if ok else "failed",
                      "message": "" if ok else "; ".join(CASE_MESSAGES.get(cid, ["no check ran"]))[:300]})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    for cid, fn in CASES:
        CUR[0] = cid
        CASE_STATUS.setdefault(cid, True)
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception as e:  # reported as a failed check for the case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"raised {type(e).__name__}: {e}")
            print(f"  FAIL [{cid}] raised {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} checks passed, {FAIL} failed; "
          f"{sum(1 for c, _ in CASES if CASE_STATUS.get(c))} of {len(CASES)} cases passed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
