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


# ================================================================================================ CP2 section
# CP2: `status` / `status --json` show the projected finish. Black-box through the CLI on small synthetic projects:
# one approved 5-step project is built once, cloned per case and hand-edited the way tests/test_modularize_cp8.py
# does (state.json edited, then re-baselined with the engine's own record_chain). The engine has no fake clock, so
# every timestamp is written relative to the real time the test runs and finish times are asserted as "now + N
# minutes within 10 minutes". The pinned engine at 30b2b55 (cp8's pinned_script) is the "before" for every pin case.
#
# Pinned shape of the new output (the implementer chooses the wording around it):
#   plain `status`: exactly one line that STARTS with "Projected finish:" after the "Phase:" line while a step is left;
#     it carries the steps left, the label (rough / estimate / no estimate yet), the finish and range as
#     "YYYY-MM-DD HH:MM UTC", the expected review rounds against the run-wide limit, and the state.json field names.
#   `status --json`: a top-level `schedule` object, always present: {steps_left, eta: null | {finish, earliest, latest,
#     label ('rough' | 'estimate'), based_on_steps}, rounds: null | {expected, used, run_limit, over_limit},
#     sources: [state.json field names], line: the exact plain line (null when no line is shown)}.

import io          # noqa: E402  (CP2 section: output capture and subprocess calls)
import subprocess  # noqa: E402

S2_PREFIX = "Projected finish:"
S2_CLOCK = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d) UTC")
S2_CLOCK_ONLY = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d UTC$")
S2_OVER = re.compile(r"\b(more than|past|exceed\w*|over the)\b", re.I)
S2_SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
S2_BASELINE = HERE / "fixtures" / "modularize_cp1" / "baseline" / "status_json.json"
S2_RUN_DIR = ROOT / ".ratchet" / "runs" / "schedule-awareness-projected-completion-eta-per"
S2_LANE_BASE = "6087151d4a11caace22f9c37d22581e382f15093"   # the commit CP2 started from (CP1's landing commit)
_S2 = {}


def s2c():
    """tests/test_modularize_cp8.py, imported when first needed (its pinned-engine and fixture helpers)."""
    if "c8" not in _S2:
        import test_modularize_cp8
        _S2["c8"] = test_modularize_cp8
    return _S2["c8"]


def s2_base(kind):
    """The shared throwaway project a case clones: 'approved' (5 steps, building), 'pre' (plan submitted, not yet
    approved), 'question' (the approve-plan question is open) or 'noplan' (init only)."""
    if kind not in _S2:
        c = s2c()
        g = c.g
        cfg = ["max_adversarial_rounds_run=10"]
        if kind == "approved":
            d = g.project("sched", cps=5, config=cfg)
            g.approve_plan(d)
        elif kind == "pre":
            d = g.project("sched-pre", cps=5, config=cfg)
        elif kind == "question":
            d = c.clone(s2_base("pre"))
            g.rx(d, "ask", "approve-plan")
        else:
            d = Path(_tmp.mkdtemp(prefix="ratchet-sched-noplan-"))
            for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
                g.git(d, *cmd)
            (d / "a.txt").write_text("a\n")
            g.git(d, "add", "-A")
            g.git(d, "commit", "-qm", "init")
            g.say(d, "please build the feature")
            r = g.rx(d, "init", "feature", "--slug", "f")
            assert r.returncode == 0, r.stderr
        _S2[kind] = d
    return _S2[kind]


def s2_fx(spans=(), attempts=None, cur=None, adv_total=0, phase="building", edit=None, base="approved"):
    """A clone of a base project with `spans` finished steps (each ended 5 minutes before now), then the current step
    started `cur` ago when given, the rest not started; adv_rounds_total and the phase are set; `edit(st)` last."""
    c = s2c()
    d = c.clone(s2_base(base))

    def fn(st):
        now = datetime.now(timezone.utc)
        ids = list(st["checkpoints"])
        for i, cid in enumerate(ids):
            cp = st["checkpoints"][cid]
            if i < len(spans):
                cp["status"], cp["step"] = "done", "complete"
                cp["started_at"], cp["completed_at"] = iso(now - M(5) - spans[i]), iso(now - M(5))
                for gt in cp["gates"].values():
                    if isinstance(gt, dict):
                        gt["status"] = "passed"
                cp["gates"]["adversarial"]["attempts"] = attempts[i] if attempts else 1
            elif i == len(spans) and cur is not None:
                cp["status"], cp["step"], cp["started_at"], cp["completed_at"] = "in_progress", "tests", iso(now - cur), None
            else:
                cp["status"], cp["step"], cp["started_at"], cp["completed_at"] = "pending", "tests", None, None
        st["current"] = ids[len(spans)] if len(spans) < len(ids) else None
        st["adv_rounds_total"] = adv_total
        st["phase"] = phase
        if edit:
            edit(st)

    c.mutate(d, fn)
    return d


def s2_f0():
    return s2_fx(cur=M(1))


def s2_f1():
    return s2_fx(spans=(M(60),), attempts=(2,), cur=M(5), adv_total=2)


def s2_f2():
    return s2_fx(spans=(M(40), M(80)), attempts=(1, 3), cur=M(1), adv_total=4)


def s2_f3(adv_total=3, phase="building", edit=None):
    return s2_fx(spans=(M(60),) * 3, attempts=(2, 1, 3), cur=M(1), adv_total=adv_total, phase=phase, edit=edit)


def s2_fdone():
    return s2_fx(spans=(M(60),) * 5, attempts=(1,) * 5, phase="awaiting_final_review")


def s2_fblocked():
    return s2_f3(edit=s2c().blocked("CP4: 3 adversarial rounds used without approval"))


def s2_out(d, env=None, engine=None):
    """(plain status result, status --json result, parsed json or None)."""
    c = s2c()
    eng = engine or c.ENGINE
    rp = c.run(eng, d, ["status"], env=env)
    rj = c.run(eng, d, ["status", "--json"], env=env)
    try:
        j = json.loads(rj.stdout)
    except ValueError:
        j = None
    return rp, rj, j


def s2_lines(text):
    return [ln.rstrip("\n") for ln in text.splitlines() if ln.startswith(S2_PREFIX)]


def s2_dict(o, key=None):
    v = o.get(key) if (key is not None and isinstance(o, dict)) else (o if key is None else None)
    return v if isinstance(v, dict) else {}


def s2_settled(d, env=None):
    """s2_out, run once more when the plain line and the JSON line differ (the two calls can straddle a 5-minute mark)."""
    for _ in range(2):
        rp, rj, j = s2_out(d, env)
        lines = s2_lines(rp.stdout)
        if lines and s2_dict(s2_dict(j, "schedule"), "line") == lines[0]:
            break
        if not lines and "line" not in s2_dict(j, "schedule"):
            break
    return rp, rj, j


def s2_line(rp):
    """The single plain 'Projected finish:' line of a `status` result, or None; a failed check when there is not one."""
    lines = s2_lines(rp.stdout)
    check("`status` exits 0", rp.returncode == 0, (rp.returncode, rp.stderr[-300:]))
    check("`status` prints exactly one line starting 'Projected finish:'", len(lines) == 1, lines)
    return lines[0] if len(lines) == 1 else None


def s2_sch(j, rj):
    check("`status --json` exits 0 and parses to an object", rj.returncode == 0 and isinstance(j, dict),
          (rj.returncode, rj.stderr[-300:], rj.stdout[-200:]))
    sch = s2_dict(j, "schedule") if isinstance(j, dict) else {}
    check("`status --json` has a top-level `schedule` object", isinstance(j, dict) and isinstance(j.get("schedule"), dict),
          list(j) if isinstance(j, dict) else None)
    return sch


def s2_pclock(text):
    try:
        # S2_CLOCK.findall captures the clock without its ' UTC' suffix; the JSON fields carry it. Accept both forms.
        if isinstance(text, str) and not text.endswith(" UTC"):
            text += " UTC"
        return datetime.strptime(text, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def s2_near(text, minutes, tol=10):
    """Whether the clock text is real-now + `minutes`, within `tol` minutes."""
    d = s2_pclock(text)
    return d is not None and abs((d - (datetime.now(timezone.utc) + M(minutes))).total_seconds()) <= tol * 60


def s2_nums(line):
    """The standalone numbers of a line, clock times left out."""
    return re.findall(r"(?<![\w.:/-])\d+(?:\.\d+)?(?![\w:/-])", S2_CLOCK.sub("", line or ""))


def s2_has_num(line, n):
    return any(float(x) == float(n) for x in s2_nums(line))


@contextlib.contextmanager
def s2_pinned(tag):
    p = s2c().pinned_script(tag)
    try:
        yield p
    finally:
        p.unlink(missing_ok=True)


def s2_norm(text, own, other):
    return s2c()._normalize_clone_text(s2c().drop_schedule_output(text), own, other)


def s2_pair(d, args, pin, env=None, stdin=None):
    """(pinned result, live result, pinned text, live text): the same command on the pinned engine and on the built
    engine, each in its own clone of `d`, with the schedule additions dropped and each clone's own path normalized."""
    c = s2c()
    d1, d2 = c.clone(d), c.clone(d)
    if stdin is None:
        r1, r2 = c.run(pin, d1, args, env=env), c.run(c.ENGINE, d2, args, env=env)
    else:
        r1 = subprocess.run([sys.executable, str(pin), *args], cwd=str(d1), capture_output=True, text=True,
                            env=env or c.g.ENV, timeout=60, input=stdin(d1))
        r2 = subprocess.run([sys.executable, str(c.ENGINE), *args], cwd=str(d2), capture_output=True, text=True,
                            env=env or c.g.ENV, timeout=60, input=stdin(d2))
    return r1, r2, s2_norm(r1.stdout, d1, d2), s2_norm(r2.stdout, d2, d1)


@case("CP2-T01")
def test_CP2_T01_no_finished_step_says_no_estimate_yet():
    d = s2_f0()
    rp, rj, j = s2_settled(d)
    line = s2_line(rp)
    sch = s2_sch(j, rj)
    lo = (line or "").lower()
    check("the line says 'no estimate yet'", "no estimate yet" in lo, line)
    check("the line gives the steps left (5)", s2_has_num(line, 5) and "left" in lo, line)
    check("the line shows no clock time", S2_CLOCK.search(line or "") is None, line)
    check("the line shows no range", "between" not in lo and "range" not in lo, line)
    out = rp.stdout.splitlines()
    ph = [i for i, ln in enumerate(out) if ln.startswith("Phase:")]
    at = [i for i, ln in enumerate(out) if ln.startswith(S2_PREFIX)]
    check("the line sits after the 'Phase:' line", bool(ph) and bool(at) and at[0] > ph[0], (ph, at))
    work = [i for i, ln in enumerate(out) if ln.startswith("Working on step")]
    check("the line sits after the 'Working on step' (plain-now) line", bool(work) and bool(at) and at[0] > work[0],
          (work, at))
    check("schedule.eta is null", "eta" in sch and sch["eta"] is None, sch.get("eta", "missing"))
    check("schedule.steps_left is 5", sch.get("steps_left") == 5, sch.get("steps_left"))
    check("schedule.line equals the plain line", line is not None and sch.get("line") == line, (sch.get("line"), line))


@case("CP2-T02")
def test_CP2_T02_one_finished_step_is_rough_and_says_it_rests_on_one():
    rp, rj, j = s2_settled(s2_f1())
    line = s2_line(rp)
    sch = s2_sch(j, rj)
    eta = s2_dict(sch, "eta")
    clocks = S2_CLOCK.findall(line or "")
    check("the line is labelled rough", "rough" in (line or "").lower(), line)
    check("the line says it rests on 1 finished step",
          "1 finished step" in (line or "") and "1 finished steps" not in (line or ""), line)
    check("the line shows a finish time about 55 + 3 x 60 = 235 minutes from now", bool(clocks) and s2_near(clocks[0], 235),
          clocks)
    check("with one finished step every clock on the line is the same time", len(clocks) >= 1 and len(set(clocks)) == 1,
          clocks)
    check("schedule.eta.label is 'rough'", eta.get("label") == "rough", eta)
    check("schedule.eta.based_on_steps is 1", eta.get("based_on_steps") == 1, eta)
    check("schedule.eta.finish is about 235 minutes from now", s2_near(eta.get("finish"), 235), eta.get("finish"))
    check("schedule.eta.earliest and latest are the same as finish",
          eta.get("earliest") == eta.get("finish") == eta.get("latest") and eta.get("finish") is not None, eta)
    check("schedule.steps_left is 4", sch.get("steps_left") == 4, sch.get("steps_left"))


@case("CP2-T03")
def test_CP2_T03_two_finished_steps_are_rough_with_a_fastest_to_slowest_range():
    rp, rj, j = s2_settled(s2_f2())
    line = s2_line(rp)
    sch = s2_sch(j, rj)
    eta = s2_dict(sch, "eta")
    check("the line is labelled rough", "rough" in (line or "").lower(), line)
    check("the line says it rests on 2 finished steps", "2 finished steps" in (line or ""), line)
    check("finish is about now + 3 x 60 - 1 = 180 minutes", s2_near(eta.get("finish"), 180), eta.get("finish"))
    check("earliest is about now + 3 x 40 = 120 minutes", s2_near(eta.get("earliest"), 120), eta.get("earliest"))
    check("latest is about now + 3 x 80 = 240 minutes", s2_near(eta.get("latest"), 240), eta.get("latest"))
    f, lo, hi = (s2_pclock(eta.get(k)) for k in ("finish", "earliest", "latest"))
    check("earliest <= finish <= latest", None not in (f, lo, hi) and lo <= f <= hi, eta)
    check("the line shows exactly those three clock times",
          set(S2_CLOCK.findall(line or "")) == {str(eta.get(k))[:-4] for k in ("finish", "earliest", "latest")},
          (S2_CLOCK.findall(line or ""), eta))
    check("schedule.eta.label is 'rough' on 2 finished steps", eta.get("label") == "rough", eta)
    check("schedule.eta.based_on_steps is 2", eta.get("based_on_steps") == 2, eta)


@case("CP2-T04")
def test_CP2_T04_three_finished_steps_is_an_estimate_not_rough():
    rp, rj, j = s2_settled(s2_f3())
    line = s2_line(rp)
    sch = s2_sch(j, rj)
    eta = s2_dict(sch, "eta")
    check("the word 'rough' is not in the line", line is not None and "rough" not in line.lower(), line)
    check("the line says it rests on 3 finished steps", "3 finished steps" in (line or ""), line)
    check("the line says estimate", "estimate" in (line or "").lower() and "no estimate" not in (line or "").lower(), line)
    clocks = S2_CLOCK.findall(line or "")
    check("the line shows a finish about now + 59 + 60 = 120 minutes", bool(clocks) and s2_near(clocks[0], 120), clocks)
    check("schedule.eta.label is 'estimate'", eta.get("label") == "estimate", eta)
    check("schedule.eta.based_on_steps is 3", eta.get("based_on_steps") == 3, eta)
    check("schedule.eta.finish is about 120 minutes from now", s2_near(eta.get("finish"), 120), eta.get("finish"))


@case("CP2-T05")
def test_CP2_T05_expected_rounds_are_the_average_times_the_steps_left():
    rp, rj, j = s2_settled(s2_f3())
    line = s2_line(rp)
    rounds = s2_dict(s2_sch(j, rj), "rounds")
    check("the line mentions review rounds", "round" in (line or "").lower(), line)
    check("the line gives 4 expected rounds (mean 2 x 2 steps left)", s2_has_num(line, 4), line)
    check("schedule.rounds.expected is 4", rounds.get("expected") == 4, rounds)
    check("schedule.rounds.run_limit is 10", rounds.get("run_limit") == 10, rounds)
    rp2, rj2, j2 = s2_settled(s2_f2())
    line2 = s2_line(rp2)
    rounds2 = s2_dict(s2_sch(j2, rj2), "rounds")
    check("fixture F2 (1 and 3 rounds, 3 steps left): the line gives 6 expected rounds", s2_has_num(line2, 6), line2)
    check("fixture F2: schedule.rounds.expected is 6, so a constant cannot pass", rounds2.get("expected") == 6, rounds2)


@case("CP2-T06")
def test_CP2_T06_rounds_past_the_run_limit_are_called_out_plainly():
    for label, used, over in (("F3-over (8 used + 4 expected > 10)", 8, True), ("F3-ok (3 used + 4 expected)", 3, False),
                              ("exact fit (6 used + 4 expected = 10 of 10)", 6, False)):
        rp, rj, j = s2_settled(s2_f3(adv_total=used))
        line = s2_line(rp)
        rounds = s2_dict(s2_sch(j, rj), "rounds")
        check(f"{label}: schedule.rounds.used is {used}, expected 4, run_limit 10",
              (rounds.get("used"), rounds.get("expected"), rounds.get("run_limit")) == (used, 4, 10), rounds)
        check(f"{label}: schedule.rounds.over_limit is {over}", rounds.get("over_limit") is over, rounds)
        said = S2_OVER.search(line or "") is not None and "limit" in (line or "").lower()
        if over:
            check(f"{label}: the line says plainly the expected rounds go past the run-wide limit", said, line)
        else:
            check(f"{label}: the line has no 'past the limit' sentence", line is not None and not S2_OVER.search(line), line)


@case("CP2-T07")
def test_CP2_T07_finish_times_are_utc_and_never_finer_than_five_minutes():
    zones = ("America/New_York", "Asia/Kolkata")
    g = s2c().g
    for label, mk in (("F1", s2_f1), ("F2", s2_f2), ("F3", s2_f3)):
        d = mk()

        def one_round():
            got = {}
            for tz in zones:
                rp, rj, j = s2_out(d, dict(g.ENV, TZ=tz))
                got[tz] = ((s2_lines(rp.stdout) or [""])[0], s2_dict(j, "schedule"))
            return got

        got = one_round()
        if got[zones[0]][0] != got[zones[1]][0]:   # the two zones' calls may straddle a 5-minute mark: once more
            got = one_round()
        for tz in zones:
            line, sch = got[tz]
            clocks = S2_CLOCK.findall(line)
            check(f"{label} {tz}: the line shows at least one clock time", len(clocks) >= 1, line)
            check(f"{label} {tz}: every clock minute is divisible by 5", all(int(c[-2:]) % 5 == 0 for c in clocks), clocks)
            check(f"{label} {tz}: no seconds, offset or zone name on the line",
                  re.search(r"\d\d:\d\d:\d\d|[+-]\d\d:?\d\d\b|\b(EST|EDT|IST|GMT)\b", S2_CLOCK.sub("", line)) is None, line)
            eta = s2_dict(sch, "eta")
            strs = [eta.get(k) for k in ("finish", "earliest", "latest")]
            check(f"{label} {tz}: the JSON eta strings are '%Y-%m-%d %H:%M UTC' on a 5-minute mark",
                  all(isinstance(x, str) and S2_CLOCK_ONLY.match(x) and int(x[-6:-4]) % 5 == 0 for x in strs), strs)
        check(f"{label}: the line is the same text under both time zones", got[zones[0]][0] == got[zones[1]][0]
              and got[zones[0]][0] != "", (got[zones[0]][0], got[zones[1]][0]))


@case("CP2-T08")
def test_CP2_T08_the_plain_line_and_the_json_field_carry_the_same_numbers():
    for label, mk in (("F0", s2_f0), ("F1", s2_f1), ("F2", s2_f2), ("F3-over", lambda: s2_f3(adv_total=8))):
        rp, rj, j = s2_settled(mk())
        line = s2_line(rp)
        sch = s2_sch(j, rj)
        eta, rounds = sch.get("eta"), sch.get("rounds")
        check(f"{label}: schedule.line is byte-equal to the plain line", line is not None and sch.get("line") == line,
              (sch.get("line"), line))
        check(f"{label}: the steps left on the line equal schedule.steps_left",
              isinstance(sch.get("steps_left"), int) and s2_has_num(line, sch["steps_left"]), (sch.get("steps_left"), line))
        if isinstance(eta, dict):
            check(f"{label}: the line's three clock times are the JSON finish, earliest and latest",
                  set(S2_CLOCK.findall(line or "")) == {str(eta.get(k))[:-4] for k in ("finish", "earliest", "latest")},
                  (S2_CLOCK.findall(line or ""), eta))
            check(f"{label}: the label on the line is the JSON label", (eta.get("label") == "rough") == ("rough" in line.lower()),
                  (eta.get("label"), line))
            check(f"{label}: the based-on count on the line equals based_on_steps",
                  f"{eta.get('based_on_steps')} finished step" in line, (eta.get("based_on_steps"), line))
        else:
            check(f"{label}: no eta in the JSON means no clock time on the line", eta is None and not S2_CLOCK.search(line or ""),
                  (eta, line))
        if isinstance(rounds, dict):
            check(f"{label}: expected rounds, rounds used and the run limit on the line equal schedule.rounds",
                  all(rounds.get(k) is not None and s2_has_num(line, rounds.get(k)) for k in ("expected", "used", "run_limit")),
                  (rounds, line))


@case("CP2-T09")
def test_CP2_T09_both_outputs_name_the_state_fields_the_numbers_came_from():
    c = s2c()
    for label, mk in (("F0", s2_f0), ("F3", s2_f3)):
        d = mk()
        rp, rj, j = s2_settled(d)
        line = s2_line(rp)
        s2_sch(j, rj)
        src = s2_dict(j, "schedule").get("sources")
        check(f"{label}: schedule.sources is a non-empty list of names while a line is printed",
              isinstance(src, list) and bool(src) and all(isinstance(x, str) and x for x in src), src)
        src = src if isinstance(src, list) else []
        leaf = lambda n: n.split(".")[-1].replace("[]", "")  # noqa: E731
        miss = [x for x in src if isinstance(x, str) and leaf(x) not in (line or "")]
        check(f"{label}: the plain line names every field in schedule.sources", bool(src) and not miss, (miss, line))
        check(f"{label}: the line names started_at", "started_at" in (line or ""), line)
        if label == "F3":
            for want in ("started_at", "completed_at", "gates.adversarial.attempts", "adv_rounds_total",
                         "max_adversarial_rounds_run"):
                check(f"F3: schedule.sources lists {want}", any(isinstance(x, str) and x.endswith(want) for x in src), src)
            for want in ("completed_at", "attempts", "adv_rounds_total", "max_adversarial_rounds_run"):
                check(f"F3: the plain line names {want}", want in (line or ""), line)
            st = json.loads(c._spath(d).read_text())
            cfg = json.loads((Path(d) / ".ratchet" / "config.json").read_text())
            gone = [x for x in src if isinstance(x, str) and not source_exists(x, st, cfg)]
            check("F3: every listed source is a field that state.json or the config really has", not gone, gone)


@case("CP2-T10")
def test_CP2_T10_no_line_and_an_empty_schedule_when_no_step_is_left():
    cancel = s2c().phase("cancelled", blocked_reason="cancelled by the user")
    for label, d in (("Fdone (finish pause)", s2_fdone()),
                     ("Fcancel (cancelled run)", s2_fx(spans=(M(60),) * 3, attempts=(1, 1, 1), cur=M(1), edit=cancel))):
        rp, rj, j = s2_out(d)
        check(f"{label}: `status` exits 0", rp.returncode == 0, (rp.returncode, rp.stderr[-300:]))
        check(f"{label}: no line starts with 'Projected finish:'", s2_lines(rp.stdout) == [], s2_lines(rp.stdout))
        sch = s2_sch(j, rj)
        check(f"{label}: steps_left is 0 or null", "steps_left" in sch and sch["steps_left"] in (0, None), sch)
        check(f"{label}: eta is null", "eta" in sch and sch["eta"] is None, sch)
        check(f"{label}: rounds is null", "rounds" in sch and sch["rounds"] is None, sch)
        check(f"{label}: no line text in the JSON", sch.get("line") in (None, ""), sch.get("line"))
    rp, rj, j = s2_settled(s2_fx(spans=(M(60),) * 4, attempts=(1,) * 4, cur=M(1)))
    line = s2_line(rp)
    sch = s2_sch(j, rj)
    check("4 of 5 done: the line gives 1 step left", s2_has_num(line, 1) and "left" in (line or "").lower(), line)
    check("4 of 5 done: schedule.steps_left is 1", sch.get("steps_left") == 1, sch.get("steps_left"))


@case("CP2-T11")
def test_CP2_T11_damaged_timestamps_never_crash_status_and_never_feed_the_estimate():
    long_ago = lambda m: iso(datetime.now(timezone.utc) - M(m))  # noqa: E731

    def wreck(st):
        """CP1..CP4 done with valid 60-minute spans, CP5 running since 5 minutes; each case then damages one field."""
        for i, cid in enumerate(("CP1", "CP2", "CP3", "CP4")):
            st["checkpoints"][cid].update(status="done", step="complete", started_at=long_ago(300 - 60 * i),
                                          completed_at=long_ago(240 - 60 * i))
        st["checkpoints"]["CP5"].update(status="in_progress", started_at=long_ago(5), completed_at=None)
        st["current"] = "CP5"

    def bad_all(st):
        cps = st["checkpoints"]
        cps["CP1"]["completed_at"] = None                                 # missing
        cps["CP2"]["completed_at"] = "not-a-date"                         # not a date
        cps["CP3"]["completed_at"] = long_ago(400)                        # ends before it started
        cps["CP4"]["started_at"] = 12345                                  # a number

    def bad_with_good(st):
        bad_all(st)
        st["checkpoints"]["CP1"].update(started_at=long_ago(65), completed_at=long_ago(5))   # one good 60-minute step

    for label, fn in (("only bad steps", bad_all), ("one good 60-minute step among the bad ones", bad_with_good)):
        d = s2_fx(edit=lambda st, fn=fn: (wreck(st), fn(st)))
        rp, rj, j = s2_settled(d)
        check(f"{label}: `status` exits 0 with no traceback", rp.returncode == 0 and "Traceback" not in rp.stderr + rp.stdout,
              (rp.returncode, rp.stderr[-300:]))
        check(f"{label}: `status --json` exits 0 with valid JSON and no traceback",
              rj.returncode == 0 and isinstance(j, dict) and "Traceback" not in rj.stderr, (rj.returncode, rj.stderr[-300:]))
        line = s2_line(rp)
        sch = s2_dict(j, "schedule")
        if label == "only bad steps":
            check("only bad steps: the line says 'no estimate yet'", "no estimate yet" in (line or "").lower(), line)
            check("only bad steps: schedule.eta is null", "eta" in sch and sch["eta"] is None, sch.get("eta", "missing"))
        else:
            eta = s2_dict(sch, "eta")
            check("variant: the line says it rests on 1 finished step",
                  "1 finished step" in (line or "") and "1 finished steps" not in (line or ""), line)
            check("variant: based_on_steps is 1", eta.get("based_on_steps") == 1, eta)
            check("variant: the finish uses the one good 60 minutes (the last step ran 5 minutes: about 55 minutes left)",
                  s2_near(eta.get("finish"), 55), eta.get("finish"))


@case("CP2-T12")
def test_CP2_T12_the_schedule_key_is_present_in_every_phase_and_nothing_else_is_added():
    fixtures = (("awaiting plan approval", s2_base("pre")), ("building (F3)", s2_f3()), ("blocked", s2_fblocked()),
                ("awaiting_final_review", s2_fdone()), ("no plan yet", s2_base("noplan")))
    with s2_pinned("cp2t12") as pin:
        for label, d in fixtures:
            r1, r2, _a, _b = s2_pair(d, ["status", "--json"], pin)
            try:
                j1, j2 = json.loads(r1.stdout), json.loads(r2.stdout)
            except ValueError:
                j1 = j2 = None
            check(f"{label}: both engines print JSON", isinstance(j1, dict) and isinstance(j2, dict),
                  (r1.stdout[-200:], r2.stdout[-200:], r2.stderr[-200:]))
            if not (isinstance(j1, dict) and isinstance(j2, dict)):
                continue
            check(f"{label}: the new top-level keys are exactly {{'schedule'}}", set(j2) - set(j1) == {"schedule"},
                  (sorted(set(j2) - set(j1)), sorted(set(j1) - set(j2))))
            check(f"{label}: no key of the pinned engine is gone", not (set(j1) - set(j2)), sorted(set(j1) - set(j2)))
            check(f"{label}: `schedule` is an object", isinstance(j2.get("schedule"), dict), j2.get("schedule"))
            if label == "awaiting plan approval":
                sch = s2_dict(j2, "schedule")
                check("awaiting plan approval: the same 'no estimate yet' line as T01, with 5 steps left",
                      "no estimate yet" in str(sch.get("line")).lower() and sch.get("steps_left") == 5, sch)


@case("CP2-T13")
def test_CP2_T13_every_existing_status_json_field_keeps_its_value():
    fixtures = (("F0", s2_f0()), ("F3", s2_f3()), ("Fblocked", s2_fblocked()), ("Fdone", s2_fdone()))
    with s2_pinned("cp2t13") as pin:
        for label, d in fixtures:
            c = s2c()
            d1, d2 = c.clone(d), c.clone(d)
            r1, r2 = c.run(pin, d1, ["status", "--json"]), c.run(c.ENGINE, d2, ["status", "--json"])
            try:
                j1, j2 = json.loads(r1.stdout), json.loads(r2.stdout)
            except ValueError:
                j1 = j2 = None
            check(f"{label}: both engines print JSON", isinstance(j1, dict) and isinstance(j2, dict),
                  (r1.stdout[-200:], r2.stdout[-200:], r2.stderr[-200:]))
            if not (isinstance(j1, dict) and isinstance(j2, dict)):
                continue
            check(f"{label}: the key sets differ by exactly {{'schedule'}}", set(j2) ^ set(j1) == {"schedule"},
                  (sorted(set(j2) - set(j1)), sorted(set(j1) - set(j2))))
            for j in (j1, j2):
                for k in ("root", "viewer_url"):
                    j.pop(k, None)
            j2.pop("schedule", None)
            diff = sorted(k for k in set(j1) | set(j2) if j1.get(k) != j2.get(k))
            check(f"{label}: after removing only `schedule`, every other field equals the pinned engine's", not diff,
                  [(k, j1.get(k), j2.get(k)) for k in diff][:3])


@case("CP2-T14")
def test_CP2_T14_taking_the_new_lines_out_leaves_the_old_bytes_exactly():
    c = s2c()
    drop = getattr(c, "drop_schedule_output", None)
    check("tests/test_modularize_cp8.py defines the one normalizer drop_schedule_output", callable(drop), None)
    if not callable(drop):
        return
    # the normalizer drops no more than it must (synthetic texts)
    plain = "Step 1 of 5 done\nPhase: building\nProjected finish: no estimate yet\nWorking on step 2\n"
    check("a line that starts with 'Projected finish:' is dropped and nothing else",
          drop(plain) == "Step 1 of 5 done\nPhase: building\nWorking on step 2\n", drop(plain))
    keep = ("Note: Projected finish: mid-line\nschedule: changed\n Projected finish: indented\nthe schedule moved\n"
            "Projected  finish:\n")
    check("lines that only mention the words, mid-line or after a space, are kept", drop(keep) == keep, drop(keep))
    pinned_out = "Phase: building\nWorking on step 2\n"
    check("a pinned output with one extra line is NOT equal after normalizing",
          drop(pinned_out + "Something new\n") != pinned_out and drop(pinned_out + "A schedule line\n") != pinned_out, None)
    check("a changed line is NOT equal after normalizing", drop("Phase: blocked\nWorking on step 2\n") != pinned_out, None)
    doc = {"a": 1, "schedule": {"x": 1}, "b": [1, 2], "checkpoints": [{"id": "CP1", "schedule": 2}], "schedule2": 3}
    want = {k: v for k, v in doc.items() if k != "schedule"}
    check("the top-level `schedule` key of a status --json dict is dropped, nothing else",
          drop(json.dumps(doc, indent=2) + "\n") == json.dumps(want, indent=2) + "\n", drop(json.dumps(doc, indent=2) + "\n"))
    other = json.dumps({"a": 1, "checkpoints": [{"id": "CP1", "schedule": 2}]}, indent=2) + "\n"
    check("JSON without a top-level `schedule` key is returned unchanged (a nested `schedule` is kept)",
          drop(other) == other, drop(other))
    # real outputs on small fixtures: the same bytes before and after, apart from the removed lines
    pre, q, f0, f3 = s2_base("pre"), s2_base("question"), s2_f0(), s2_f3()
    fixtures = (("F0 building", f0), ("F3 building", f3), ("awaiting plan approval", pre), ("question open", q),
                ("blocked", s2_fblocked()), ("finish pause", s2_fdone()))
    with s2_pinned("cp2t14") as pin:
        for label, d in fixtures:
            for args in (["status"], ["next"], ["plan-summary"]):
                r1, r2, o1, o2 = s2_pair(d, args, pin)
                check(f"{label}: `{' '.join(args)}` exits with the same code", r1.returncode == r2.returncode,
                      (r1.returncode, r2.returncode, r2.stderr[-200:]))
                check(f"{label}: `{' '.join(args)}` stdout is byte-identical once the schedule lines are dropped",
                      o1 == o2, (o1[-300:], o2[-300:]))
        d1, d2 = c.clone(f3), c.clone(f3)
        raw1, raw2 = c.run(pin, d1, ["status"]).stdout, c.run(c.ENGINE, d2, ["status"]).stdout
        n1, n2 = c._normalize_clone_text(raw1, d1, d2), c._normalize_clone_text(raw2, d2, d1)
        added = [ln for ln in n2.splitlines(keepends=True) if ln.startswith(S2_PREFIX)]
        rest = "".join(ln for ln in n2.splitlines(keepends=True) if not ln.startswith(S2_PREFIX))
        check("without the normalizer the raw `status` outputs on F3 differ (the normalizer is needed)", n1 != n2, None)
        check("the raw outputs differ by exactly the schedule lines and nothing else", bool(added) and rest == n1,
              (added, rest[-200:], n1[-200:]))
        rn = c.run(c.ENGINE, c.clone(f3), ["next"])
        try:
            nj = json.loads(rn.stdout)
        except ValueError:
            nj = None
        check("`next` has no schedule key and no 'Projected finish:' text in CP2",
              isinstance(nj, dict) and "schedule" not in nj and S2_PREFIX not in rn.stdout, rn.stdout[-300:])


@case("CP2-T15")
def test_CP2_T15_the_earlier_byte_pins_and_key_set_checks_still_pass_with_only_additive_edits():
    c = s2c()
    ran = []
    for name in ("t05_status_text_byte_identical_across_phases", "t06_next_plain_fields_byte_identical",
                 "t07_plan_summary_byte_identical", "t08_blocked_options_byte_identical_per_reason",
                 "t09_finish_pause_block_byte_identical", "t11_exit_code_only_note_byte_identical",
                 "t12_hook_crash_plain_lines_byte_identical", "t13_status_and_next_json_carry_same_fields"):
        fn = getattr(c, name, None)
        if not callable(fn):
            check(f"cp8 pin case {name} exists", False, None)
            continue
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                fn()
                crashed = None
            except Exception as e:  # a crash is a failed check, not a crash of this file
                crashed = f"{type(e).__name__}: {e}"
        bad = [ln.strip() for ln in buf.getvalue().splitlines() if "\u2717" in ln]
        ran.append(name)
        check(f"cp8 {name}: every byte-pin check passes with its check() calls as written",
              crashed is None and not bad, (crashed, bad[:2]))
    check("(sanity) eight cp8 pin cases were called", len(ran) == 8, ran)
    if not S2_RUN_DIR.exists():
        check("(not in this lane's run: the diff-scope check is skipped, as cp8's own T17 does)", True)
    else:
        r = subprocess.run(["git", "cat-file", "-e", S2_LANE_BASE + "^{commit}"], cwd=str(ROOT), capture_output=True)
        if r.returncode == 0:
            d = subprocess.run(["git", "diff", "--numstat", S2_LANE_BASE, "--", "tests/test_modularize_cp8.py"],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=60)
            parts = d.stdout.split()
            check("git diff of tests/test_modularize_cp8.py against its base shows only added lines (none removed)",
                  d.returncode == 0 and len(parts) >= 3 and parts[1] == "0" and parts[0].isdigit() and int(parts[0]) > 0,
                  (d.returncode, d.stdout, d.stderr[-200:]))
    try:
        saved = json.loads(S2_BASELINE.read_text())
    except (OSError, ValueError) as e:
        saved = None
        check("the CP1 baseline status_json.json parses", False, str(e))
    rl = subprocess.run([sys.executable, str(c.ENGINE), "status", "--json"], cwd=str(ROOT), capture_output=True,
                        text=True, timeout=60)
    try:
        live = json.loads(rl.stdout) if rl.returncode == 0 else None
    except ValueError:
        live = None
    check("a live `status --json` succeeds", isinstance(live, dict), (rl.returncode, rl.stdout[-200:]))
    if isinstance(live, dict) and isinstance(saved, dict):
        check("the live top-level key set differs from the baseline by exactly {'schedule'}",
              set(live) - set(saved) == {"schedule"} and not (set(saved) - set(live)),
              (sorted(set(live) - set(saved)), sorted(set(saved) - set(live))))


def s2_tree_hash(d):
    import hashlib
    h = hashlib.sha256()
    run_dir = Path(d) / ".ratchet" / "runs" / "f"
    files = [run_dir / "state.json", Path(d) / ".ratchet" / "config.json"]
    ev = run_dir / "evidence"
    if ev.is_dir():
        files += sorted(p for p in ev.rglob("*") if p.is_file())
    for p in files:
        h.update(str(p.relative_to(d)).encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()


@case("CP2-T16")
def test_CP2_T16_showing_the_schedule_does_not_write_or_change_anything():
    c = s2c()
    d = s2_f3()
    rp0, rj0, j0 = s2_out(d)
    check("(sanity) the built `status` really shows the schedule line on F3", len(s2_lines(rp0.stdout)) == 1,
          s2_lines(rp0.stdout))
    check("(sanity) the built `status --json` really has the schedule object on F3",
          isinstance(j0, dict) and isinstance(j0.get("schedule"), dict), None)
    before = s2_tree_hash(d)
    for args in (["status"], ["status", "--json"], ["next"]) * 2:
        r = c.run(c.ENGINE, d, args)
        check(f"`{' '.join(args)}` exits 0", r.returncode == 0, (r.returncode, r.stderr[-200:]))
    check("state.json, the evidence folder and .ratchet/config.json are unchanged after showing the schedule",
          s2_tree_hash(d) == before, None)

    def stop_in(own):
        return json.dumps({"cwd": str(own), "session_id": "s-owner", "hook_event_name": "Stop"})

    with s2_pinned("cp2t16") as pin:
        for args in (["status"], ["status --json"], ["next"], ["hook", "stop", "--host", "claude"]):
            args = args[0].split() if len(args) == 1 else args
            r1, r2, o1, o2 = s2_pair(d, args, pin, stdin=stop_in if args[0] == "hook" else None)
            if args[0] == "hook":
                check("the Stop hook exits with the same code on both engines", r1.returncode == r2.returncode,
                      (r1.returncode, r2.returncode, r2.stderr[-200:]))
                check("the Stop hook's reply is identical on both engines for the same state", o1 == o2, (o1[-300:], o2[-300:]))
            else:
                check(f"`{' '.join(args)}` exits with the pinned engine's code", r1.returncode == r2.returncode,
                      (r1.returncode, r2.returncode))
        n1, n2, _a, _b = s2_pair(d, ["next"], pin)
        try:
            a, b = json.loads(n1.stdout), json.loads(n2.stdout)
        except ValueError:
            a = b = None
        check("`next` prints JSON on both engines", isinstance(a, dict) and isinstance(b, dict), (n1.stdout[-100:], n2.stdout[-100:]))
        if isinstance(a, dict) and isinstance(b, dict):
            check("`next`'s action equals the pinned engine's", a.get("action") == b.get("action"), (a.get("action"), b.get("action")))
            check("`next`'s stop_ok equals the pinned engine's", a.get("stop_ok") == b.get("stop_ok"),
                  (a.get("stop_ok"), b.get("stop_ok")))


@case("CP2-T17")
def test_CP2_T17_other_readers_of_status_keep_working_with_the_new_field():
    c = s2c()
    d = s2_f3()
    rs, rjs, js = s2_out(d)
    check("(sanity) `status --json` has the schedule object on the lane's run",
          isinstance(js, dict) and isinstance(js.get("schedule"), dict), None)

    def rows(engine, own):
        r = c.run(engine, own, ["lanes", "--scan", str(own), "--json"])
        try:
            out = json.loads(r.stdout)
            for row in out:
                for k in ("root", "viewer_url"):   # each clone's own path is the only variable part of a row
                    row.pop(k, None)
        except (ValueError, AttributeError, TypeError):
            out = None
        return r, out

    with s2_pinned("cp2t17") as pin:
        d1, d2 = c.clone(d), c.clone(d)
        r1, a = rows(pin, d1)
        r2, b = rows(c.ENGINE, d2)
    check("both engines list the lane (`lanes --scan --json` exits 0 and prints one row)",
          r1.returncode == 0 and r2.returncode == 0 and isinstance(a, list) and isinstance(b, list) and len(a) == 1 == len(b),
          (r1.returncode, r2.returncode, r2.stderr[-200:], r2.stdout[-200:]))
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == 1 == len(b)):
        return
    for k in ("phase", "done", "total", "waiting_on", "awaiting_since"):
        check(f"the lane row's {k} equals the pinned engine's", a[0].get(k) == b[0].get(k), (k, a[0].get(k), b[0].get(k)))
    check("the lane row shows done 3 of total 5 in phase building",
          (b[0].get("done"), b[0].get("total"), b[0].get("phase")) == (3, 5, "building"), b[0])
    check("the lane row has no key the pinned engine's row lacks, other than `schedule`",
          set(b[0]) - set(a[0]) <= {"schedule"} and not (set(a[0]) - set(b[0])), (sorted(set(b[0]) ^ set(a[0]))))
    x, y = dict(a[0]), dict(b[0])
    y.pop("schedule", None)
    diff = sorted(k for k in set(x) | set(y) if x.get(k) != y.get(k))
    check("every other field of the lane row equals the pinned engine's", not diff, [(k, x.get(k), y.get(k)) for k in diff][:3])


@case("CP2-T18")
def test_CP2_T18_schemas_section_9_shows_the_schedule_field_and_calls_it_an_estimate():
    try:
        doc_text = S2_SCHEMAS.read_text(encoding="utf-8")
    except OSError as e:
        check("schemas.md is readable", False, str(e))
        return
    m = re.search(r"^## 9\. .*?(?=^## 10\. )", doc_text, re.S | re.M)
    check("(fixture sanity) schemas.md has a section 9", m is not None)
    if m is None:
        return
    sec = m.group(0)
    blk = re.search(r"```json\n(.*?)```", sec, re.S)
    try:
        ex = json.loads(blk.group(1)) if blk else None
    except ValueError:
        ex = None
    check("the section 9 status --json example parses", isinstance(ex, dict), None)
    ex = ex if isinstance(ex, dict) else {}
    rp, rj, j = s2_out(s2_f3())
    real = j if isinstance(j, dict) else {}
    check("(fixture sanity) a real status --json from F3 parses", bool(real), rj.stderr[-200:])
    check("the example has a `schedule` key", "schedule" in ex, sorted(ex))
    check("the example's `schedule` is an object with the same keys as the real schedule object",
          isinstance(ex.get("schedule"), dict) and isinstance(real.get("schedule"), dict)
          and set(ex["schedule"]) == set(real["schedule"]), (ex.get("schedule"), real.get("schedule")))
    check("the example's top-level keys equal the real top-level keys exactly",
          set(ex) == set(real), (sorted(set(real) - set(ex)), sorted(set(ex) - set(real))))
    dcp = (ex.get("checkpoints") or [{}])[0]
    rcp = (real.get("checkpoints") or [{}])[0]
    check("the per-checkpoint keys in the example are unchanged (all documented, none invented)",
          bool(rcp) and set(dcp) == set(rcp), (sorted(set(rcp) - set(dcp)), sorted(set(dcp) - set(rcp))))
    sentences = re.split(r"(?<=[.!?])\s+", sec.replace("\n", " "))
    mine = [s for s in sentences if "schedule" in s.lower() and "estimate" in s.lower() and "not a deadline" in s.lower()]
    check("the section has a sentence saying the schedule is an estimate, not a deadline", len(mine) >= 1, None)


# ================================================================================================ CP3 section
# CP3: the STALL warning. Black-box through the CLI on the same small synthetic projects as CP2 (s2_fx clones; the engine
# has no fake clock, so every time is written relative to the real clock when the fixture is built). "Fixture B" = phase
# building, CP1 'Step 1' done after 3 hours, CP2 'Step 2' current at step `implement` (which status words as "building
# it") and started N minutes ago. Pinned shape of the new output (the implementer chooses the wording around it):
#   plain `status`: one line that STARTS with "STALL:" after the "Phase:" and "Working on" lines (only when a stall fires);
#   `status --json`: schedule.stall, ALWAYS present inside `schedule` (null or {checkpoint, step, elapsed_minutes,
#     budget_minutes, since, since_event, message}); no new top-level key besides `schedule`, no per-checkpoint key;
#   `next`: a new top-level `stall` key, always present (null or the same object).
# `since` is accepted as UTC ISO text or as "YYYY-MM-DD HH:MM UTC" (the two spellings the engine already uses).
# The one pre-existing crash this section stays clear of: a building run whose current step has no checkpoint entry makes
# `status --json` and `next` raise KeyError in next_action today (the engine without CP3), so T17 asks only plain `status`.

S3_TAG = "STALL:"
S3_DUR = re.compile(r"(?:(\d+)\s*(?:h|hrs?|hours?)\s*(?:and\s*)?)?(\d+)\s*(?:m|mins?|minutes?)\b", re.I)
S3_PHASES = ("paused", "blocked", "awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review",
             "planning", "done", "cancelled")


def s3_phase_fields(st, ph):
    """The extra state fields the real engine sets alongside each non-building phase."""
    if ph == "paused":
        st.update(paused_from="building", blocked_reason="paused")
    elif ph == "blocked":
        st.update(blocked_from="building", blocked_reason="CP2: 3 adversarial rounds used without approval")
    elif ph == "awaiting_checkpoint_review":
        st["review_target"] = "CP2"
    elif ph == "cancelled":
        st["blocked_reason"] = "cancelled by the user"


def s3_fx(started=100, step="implement", phase="building", history=(), edit=None, adv_total=0, attempts2=None):
    """Fixture B: a clone with CP1 done (3 hours) and CP2 current at `step`, started `started` minutes ago. `history`
    is a list of (minutes_ago, event, cp_or_None) entries appended after the project's own history."""
    def e(st):
        now = datetime.now(timezone.utc)
        cp = st["checkpoints"]["CP2"]
        cp["step"] = step
        if attempts2 is not None:
            cp["gates"]["adversarial"]["attempts"] = attempts2
        h = st["history"] if isinstance(st.get("history"), list) else []
        for ago, ev, who in history:
            ent = {"at": iso(now - M(ago)), "event": ev}
            if who:
                ent["cp"] = who
            h.append(ent)
        st["history"] = h
        s3_phase_fields(st, phase)
        if edit:
            edit(st)
    return s2_fx(spans=(M(180),), attempts=(1,), cur=M(started), phase=phase, adv_total=adv_total, edit=e)


def s3_state(d):
    return json.loads((Path(d) / ".ratchet" / "runs" / "f" / "state.json").read_text())


def s3_cfg_path(d):
    return Path(d) / ".ratchet" / "config.json"


def s3_set_cfg(d, key, value, absent=False):
    p = s3_cfg_path(d)
    cf = json.loads(p.read_text())
    if absent:
        cf.pop(key, None)
    else:
        cf[key] = value
    p.write_text(json.dumps(cf, indent=2))


def s3_json(text):
    try:
        v = json.loads(text)
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def s3_cmds(d, engine=None):
    """status, status --json and next, each run once: .rp/.rj/.rn are the results, .j/.n the parsed JSON (or None)."""
    c = s2c()
    eng = engine or c.ENGINE
    o = types.SimpleNamespace()
    o.rp, o.rj, o.rn = (c.run(eng, d, a) for a in (["status"], ["status", "--json"], ["next"]))
    o.j, o.n = s3_json(o.rj.stdout), s3_json(o.rn.stdout)
    return o


def s3_exit(o, label):
    for name, r in (("status", o.rp), ("status --json", o.rj), ("next", o.rn)):
        check(f"{label}: `{name}` exits 0 with no traceback", r.returncode == 0 and "Traceback" not in r.stderr,
              (r.returncode, r.stderr[-200:]))
    check(f"{label}: `status --json` and `next` print JSON objects", o.j is not None and o.n is not None,
          (o.rj.stdout[-100:], o.rn.stdout[-100:]))


def s3_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith(S3_TAG)]


def s3_sj(o):
    """(present, value) of schedule.stall in the parsed status --json."""
    sch = o.j.get("schedule") if isinstance(o.j, dict) else None
    if not isinstance(sch, dict) or "stall" not in sch:
        return False, None
    return True, sch["stall"]


def s3_nj(o):
    """(present, value) of the top-level `stall` in the parsed next output."""
    if not isinstance(o.n, dict) or "stall" not in o.n:
        return False, None
    return True, o.n["stall"]


def s3_none(o, label):
    """No stall anywhere: no STALL text, schedule.stall present and null, next.stall present and null."""
    check(f"{label}: plain `status` has no STALL text", "STALL" not in o.rp.stdout, o.rp.stdout[:300])
    present, val = s3_sj(o)
    check(f"{label}: `status --json` has schedule.stall, and it is null", present and val is None, (present, val))
    present, val = s3_nj(o)
    check(f"{label}: `next` carries the key `stall`, and it is null", present and val is None, (present, val))
    check(f"{label}: no STALL text in the `next` output", "STALL" not in o.rn.stdout, o.rn.stdout[:200])


def s3_mins(text):
    """Every duration in a text ('1h 40m', '1 hour 40 minutes', '45 minutes') as whole minutes."""
    return [int(h or 0) * 60 + int(m) for h, m in S3_DUR.findall(text or "")]


def s3_has_mins(text, want, tol=2):
    return any(abs(x - want) <= tol for x in s3_mins(text))


def s3_when(v):
    """A stall time as an aware datetime, from UTC ISO text or from 'YYYY-MM-DD HH:MM UTC'; None otherwise."""
    return pt(v) or s2_pclock(v)


def s3_near(v, when, tol=60):
    d = s3_when(v)
    return d is not None and abs((d - when).total_seconds()) <= tol


def s3_obj(label, name, v, cp="CP2", step="implement", budget=90, minutes=None):
    """Checks the shape of a stall object; returns it as a dict (empty when it is not an object)."""
    check(f"{label}: {name} is an object", isinstance(v, dict), v)
    v = v if isinstance(v, dict) else {}
    check(f"{label}: {name}.checkpoint is {cp!r} and .step is {step!r}", (v.get("checkpoint"), v.get("step")) == (cp, step),
          (v.get("checkpoint"), v.get("step")))
    check(f"{label}: {name}.budget_minutes is {budget}", v.get("budget_minutes") == budget, v.get("budget_minutes"))
    el = v.get("elapsed_minutes")
    check(f"{label}: {name}.elapsed_minutes is a whole number", isinstance(el, int) and not isinstance(el, bool), el)
    if minutes is not None:
        check(f"{label}: {name}.elapsed_minutes is {minutes} (within 1 minute)",
              isinstance(el, int) and abs(el - minutes) <= 1, el)
    return v


def s3_fires(o, label, minutes, budget=90, since=None, since_event=None):
    """The stall fires in all three outputs. Returns (status stall dict, next stall dict, the STALL line or None)."""
    s3_exit(o, label)
    lines = s3_lines(o.rp.stdout)
    check(f"{label}: plain `status` has exactly one line starting 'STALL:'", len(lines) == 1, lines)
    present, sv = s3_sj(o)
    check(f"{label}: `status --json` has schedule.stall", present, o.j and o.j.get("schedule"))
    sv = s3_obj(label, "schedule.stall", sv, budget=budget, minutes=minutes)
    present, nv = s3_nj(o)
    check(f"{label}: `next` carries the key `stall`", present, o.n and sorted(o.n))
    nv = s3_obj(label, "next.stall", nv, budget=budget, minutes=minutes)
    if since is not None:
        check(f"{label}: schedule.stall.since is {since.isoformat()} (the moment the clock started)",
              s3_near(sv.get("since"), since), sv.get("since"))
        check(f"{label}: next.stall.since is the same moment", s3_near(nv.get("since"), since), nv.get("since"))
    if since_event is not None:
        ev = sv.get("since_event")
        ok = isinstance(ev, str) and (since_event in ev.lower() if since_event == "start" else ev == since_event)
        check(f"{label}: schedule.stall.since_event names {since_event!r}", ok, ev)
    return sv, nv, (lines[0] if len(lines) == 1 else None)


def s3_line_facts(label, line, minutes, since, budget=90, nope=()):
    """What a STALL line must tell: the step, how long, since when, and none of the durations in `nope`."""
    if line is None:
        check(f"{label}: there is a STALL line to read", False, None)
        return
    check(f"{label}: the line names CP2", "CP2" in line, line)
    check(f"{label}: the line words the step the way status does ('building it')", "building it" in line, line)
    check(f"{label}: the line gives the time elapsed (about {minutes} minutes)", s3_has_mins(line, minutes), line)
    for bad in nope:
        check(f"{label}: the line does not give {bad} minutes", not s3_has_mins(line, bad), line)
    clocks = [s2_pclock(x) for x in S2_CLOCK.findall(line)]
    check(f"{label}: the line says when progress was last recorded ({since.strftime('%Y-%m-%d %H:%M')} UTC)",
          any(c3 is not None and abs((c3 - since).total_seconds()) <= 120 for c3 in clocks), line)


def s3_started(d, cp="CP2"):
    return s3_when(s3_state(d)["checkpoints"][cp].get("started_at"))


def s3_hist_time(d, event, cp="CP2"):
    for e in s3_state(d)["history"]:
        if isinstance(e, dict) and e.get("event") == event and e.get("cp") == cp:
            return s3_when(e.get("at"))
    return None


def s3_run_hist_time(d, event):
    for e in reversed(s3_state(d)["history"]):
        if isinstance(e, dict) and e.get("event") == event:
            return s3_when(e.get("at"))
    return None


@case("CP3-T01")
def test_CP3_T01_a_step_stuck_over_90_minutes_shows_a_stall_line_near_the_top():
    d = s3_fx(started=100)
    o = s3_cmds(d)
    sv, nv, line = s3_fires(o, "100 minutes", 100)
    lines = o.rp.stdout.splitlines()
    pi = next((i for i, ln in enumerate(lines) if ln.startswith("Phase:")), None)
    wi = next((i for i, ln in enumerate(lines) if ln.startswith("Working on")), None)
    si = next((i for i, ln in enumerate(lines) if ln.startswith(S3_TAG)), None)
    check("(fixture sanity) status prints a Phase line and a 'Working on' line", pi is not None and wi is not None, lines[:6])
    check("the STALL line comes after the Phase and 'Working on' lines and within the first few lines",
          si is not None and pi is not None and wi is not None and si > pi and si > wi and si <= pi + 4, (pi, wi, si))
    s3_line_facts("100 minutes", line, 100, s3_started(d), nope=(180, 185))


@case("CP3-T02")
def test_CP3_T02_just_under_the_budget_prints_no_stall_anywhere():
    d = s3_fx(started=89)
    o = s3_cmds(d)
    s3_exit(o, "89 minutes")
    s3_none(o, "89 minutes")
    check("(sanity) no empty STALL heading or bare marker is printed", not re.search(r"(?mi)^\s*stall\b", o.rp.stdout),
          o.rp.stdout[:200])


@case("CP3-T03")
def test_CP3_T03_just_over_the_budget_does_fire():
    d = s3_fx(started=91)
    o = s3_cmds(d)
    sv, nv, line = s3_fires(o, "91 minutes", 91)
    check("the text names the 90 minute budget", line is not None and s3_has_mins(line, 90, 0), line)
    check("schedule.stall and next.stall carry the same checkpoint, step and budget",
          (sv.get("checkpoint"), sv.get("step"), sv.get("budget_minutes"))
          == (nv.get("checkpoint"), nv.get("step"), nv.get("budget_minutes")), (sv, nv))


@case("CP3-T04")
def test_CP3_T04_status_json_carries_the_stall_in_schedule_stall_with_the_agreed_fields():
    c = s2c()
    d = s3_fx(started=100)
    o = s3_cmds(d)
    s3_exit(o, "stalled")
    with s2_pinned("cp3t04") as pin:
        p = s3_json(c.run(pin, c.clone(d), ["status", "--json"]).stdout)
    check("(sanity) the pinned engine's `status --json` parses", p is not None, None)
    if o.j is not None and p is not None:
        check("the only new top-level key is `schedule`; no top-level `stall`",
              set(o.j) - set(p) == {"schedule"} and "stall" not in o.j, (sorted(set(o.j) - set(p)), sorted(set(p) - set(o.j))))
        new = [sorted(set(a) ^ set(b)) for a, b in zip(o.j.get("checkpoints") or [], p.get("checkpoints") or [])]
        check("no checkpoint entry gets a new key", all(not x for x in new) and
              len(o.j.get("checkpoints") or []) == len(p.get("checkpoints") or []), new)
    present, sv = s3_sj(o)
    check("`status --json` has schedule.stall", present, o.j and o.j.get("schedule"))
    sv = s3_obj("stalled", "schedule.stall", sv, minutes=100)
    check("schedule.stall.since is CP2's started_at", s3_near(sv.get("since"), s3_started(d)), (sv.get("since"), s3_started(d)))
    ev = sv.get("since_event")
    check("schedule.stall.since_event names the step start", isinstance(ev, str) and "start" in ev.lower(), ev)
    msg = sv.get("message")
    check("schedule.stall.message is a non-empty text that names CP2", isinstance(msg, str) and msg.strip() != "" and "CP2" in msg, msg)


@case("CP3-T05")
def test_CP3_T05_next_carries_the_same_stall_and_leaves_its_other_fields_as_they_were():
    d, twin = s3_fx(started=100), s3_fx(started=10)
    o, ot = s3_cmds(d), s3_cmds(twin)
    sv, nv, _line = s3_fires(o, "stalled", 100)
    check("next.stall equals schedule.stall (checkpoint, step, since, budget; elapsed within 1 minute)",
          all(nv.get(k) == sv.get(k) for k in ("checkpoint", "step", "since", "budget_minutes"))
          and isinstance(nv.get("elapsed_minutes"), int) and isinstance(sv.get("elapsed_minutes"), int)
          and abs(nv["elapsed_minutes"] - sv["elapsed_minutes"]) <= 1, (nv, sv))
    s3_exit(ot, "twin")
    present, val = s3_nj(ot)
    check("the twin's `stall` is present and null", present and val is None, (present, val))
    if o.n is not None and ot.n is not None:
        a, b = dict(o.n), dict(ot.n)
        a.pop("stall", None)
        b.pop("stall", None)
        check("after dropping `stall`, both outputs have the same keys", set(a) == set(b), sorted(set(a) ^ set(b)))
        for k in ("phase", "checkpoint", "step", "action", "stop_ok"):
            check(f"next.{k} is the same in both", a.get(k) == b.get(k), (a.get(k), b.get(k)))
        check("next.stop_ok is false in both", a.get("stop_ok") is False and b.get("stop_ok") is False,
              (a.get("stop_ok"), b.get("stop_ok")))


@case("CP3-T06")
def test_CP3_T06_new_gate_progress_restarts_the_clock():
    d = s3_fx(started=200, history=[(20, "implemented", "CP2")])
    o = s3_cmds(d)
    s3_exit(o, "progress 20 minutes ago")
    s3_none(o, "progress 20 minutes ago")
    check("(sanity) the step as a whole really is older than the budget", datetime.now(timezone.utc) - s3_started(d) > M(150), None)


@case("CP3-T07")
def test_CP3_T07_the_stall_is_counted_from_the_last_progress_and_says_which_one():
    d = s3_fx(started=300, history=[(200, "tests_written", "CP2"), (100, "red", "CP2")])
    o = s3_cmds(d)
    red = s3_hist_time(d, "red")
    sv, nv, line = s3_fires(o, "last progress 100 minutes ago", 100, since=red, since_event="red")
    check("next.stall.since_event is 'red' too", nv.get("since_event") == "red", nv.get("since_event"))
    s3_line_facts("last progress 100 minutes ago", line, 100, red, nope=(200, 300))


@case("CP3-T08")
def test_CP3_T08_progress_of_another_step_or_a_non_progress_entry_does_not_restart_the_clock():
    d = s3_fx(started=120, history=[(5, "behavior_passed", "CP1"), (5, "note", "CP2")])
    o = s3_cmds(d)
    sv, nv, _line = s3_fires(o, "other-step progress and a note", 120, since=s3_started(d), since_event="start")


@case("CP3-T09")
def test_CP3_T09_a_pause_followed_by_a_resume_restarts_the_clock():
    c = s2c()
    d = s3_fx(started=300, history=[(200, "paused", None), (5, "resumed", None)])
    o = s3_cmds(d)
    s3_exit(o, "resumed 5 minutes ago")
    s3_none(o, "resumed 5 minutes ago")

    def move(st):
        now = datetime.now(timezone.utc)
        for e in st["history"]:
            if isinstance(e, dict) and e.get("event") == "resumed":
                e["at"] = iso(now - M(100))
    c.mutate(d, move)
    o2 = s3_cmds(d)
    s3_fires(o2, "resumed 100 minutes ago", 100, since=s3_run_hist_time(d, "resumed"), since_event="resumed")


@case("CP3-T10")
def test_CP3_T10_unblocking_a_blocked_run_also_restarts_the_clock():
    c = s2c()
    d = s3_fx(started=300, history=[(200, "blocked", None), (10, "unblocked", None)])
    o = s3_cmds(d)
    s3_exit(o, "unblocked 10 minutes ago")
    s3_none(o, "unblocked 10 minutes ago")

    def move(st):
        now = datetime.now(timezone.utc)
        for e in st["history"]:
            if isinstance(e, dict) and e.get("event") == "unblocked":
                e["at"] = iso(now - M(100))
    c.mutate(d, move)
    o2 = s3_cmds(d)
    s3_fires(o2, "unblocked 100 minutes ago", 100, since=s3_run_hist_time(d, "unblocked"), since_event="unblocked")


@case("CP3-T11")
def test_CP3_T11_a_run_that_is_not_building_never_shows_a_stall_whatever_the_step_age():
    c = s2c()
    with s2_pinned("cp3t11") as pin:
        for ph in S3_PHASES:
            d = s3_fx(started=300, phase=ph)
            o = s3_cmds(d)
            s3_exit(o, ph)
            s3_none(o, ph)
            before = s3_json(c.run(pin, c.clone(d), ["next"]).stdout)
            check(f"{ph}: (sanity) the pinned engine prints this phase's `next`", before is not None, None)
            if before is not None and o.n is not None:
                for k in ("phase", "action", "stop_ok"):
                    check(f"{ph}: next.{k} is what the pinned engine printed", o.n.get(k) == before.get(k),
                          (o.n.get(k), before.get(k)))


@case("CP3-T12")
def test_CP3_T12_a_building_run_with_no_current_step_shows_no_stall_and_does_not_crash():
    def nocur(st):
        st["current"] = None
        for e in st["history"]:
            if isinstance(e, dict):
                e["at"] = iso(datetime.now(timezone.utc) - M(600))
    d = s3_fx(started=300, edit=nocur)
    o = s3_cmds(d)
    s3_exit(o, "no current step")
    s3_none(o, "no current step")
    act = (o.n or {}).get("action")
    check("`next` still says there is no current checkpoint",
          isinstance(act, str) and "no current checkpoint" in act.lower(), act)


@case("CP3-T13")
def test_CP3_T13_setting_stall_budget_minutes_to_45_changes_the_budget():
    c = s2c()
    d = s3_fx(started=50)
    r = c.run(c.ENGINE, d, ["config", "--set", "stall_budget_minutes=45"])
    check("`config --set stall_budget_minutes=45` is accepted (exit 0, not refused as unknown)",
          r.returncode == 0 and "unknown" not in (r.stdout + r.stderr).lower(), (r.returncode, r.stdout[-200:], r.stderr[-200:]))
    check("the setting is stored as 45", s3_json(s3_cfg_path(d).read_text()) is not None
          and s3_json(s3_cfg_path(d).read_text()).get("stall_budget_minutes") == 45, None)
    o = s3_cmds(d)
    sv, nv, line = s3_fires(o, "budget 45, 50 minutes in", 50, budget=45)
    check("the STALL text says the 45 minute budget", line is not None and s3_has_mins(line, 45, 0), line)
    r = c.run(c.ENGINE, d, ["config", "--set", "stall_budget_minutes=60"])
    check("`config --set stall_budget_minutes=60` is accepted", r.returncode == 0, (r.returncode, r.stderr[-200:]))
    o = s3_cmds(d)
    s3_exit(o, "budget 60, 50 minutes in")
    s3_none(o, "budget 60, 50 minutes in")
    s3_none(s3_cmds(s3_fx(started=50)), "no setting, 50 minutes in")


@case("CP3-T14")
def test_CP3_T14_changing_the_stall_budget_needs_no_approve_changes_and_moves_no_gate():
    c = s2c()
    d = s3_fx(started=60)
    before = s3_json(s3_cfg_path(d).read_text())
    r = c.run(c.ENGINE, d, ["config", "--set", "stall_budget_minutes=45"])
    check("`config --set stall_budget_minutes=45` exits 0", r.returncode == 0, (r.returncode, r.stdout[-200:], r.stderr[-200:]))
    after = s3_json(s3_cfg_path(d).read_text())
    check("every other config value is unchanged (only stall_budget_minutes is new)",
          before is not None and after == dict(before, stall_budget_minutes=45), (before, after))
    o = s3_cmds(d)
    s3_exit(o, "after the change")
    n = o.n or {}
    check("next is still the building action for CP2",
          (n.get("phase"), n.get("checkpoint"), n.get("step")) == ("building", "CP2", "implement")
          and str(n.get("action", "")).startswith("CP2: spawn ratchet-implementer"), n)
    check("the run is not held: stop_ok false, no question, no warning of pending changes",
          n.get("stop_ok") is False and n.get("question") is None, (n.get("stop_ok"), n.get("question")))
    check("status --json is still building and not waiting on anyone",
          (o.j or {}).get("phase") == "building" and (o.j or {}).get("waiting_on") is None, (o.j or {}).get("phase"))
    blob = (r.stdout + r.stderr + o.rp.stdout + o.rn.stdout).lower()
    check("nothing mentions approve-changes or changes pending",
          "approve-changes" not in blob and "changes pending" not in blob and "approved plan files" not in blob, blob[:300])
    check("(link to the feature) with the 45 minute budget the 60 minute old step now shows a stall",
          isinstance(s3_nj(o)[1], dict) and (s3_nj(o)[1] or {}).get("budget_minutes") == 45, s3_nj(o))


@case("CP3-T15")
def test_CP3_T15_a_nonsense_stall_budget_falls_back_to_90_and_status_says_so():
    d = s3_fx(started=100)
    for v in (0, -30, "soon", True, None):
        s3_set_cfg(d, "stall_budget_minutes", v)
        label = f"stall_budget_minutes={v!r}"
        o = s3_cmds(d)
        sv, nv, line = s3_fires(o, label, 100, budget=90)
        blob = (line or "") + " " + json.dumps(sv)
        check(f"{label}: the output says plainly that stall_budget_minutes could not be used and 90 is in use",
              "stall_budget_minutes" in blob and re.search(r"\b90\b", blob) is not None
              and re.search(r"ignor|invalid|not (?:a )?(?:valid|usable|positive)|could not|cannot|fall", blob, re.I) is not None, blob)
    d2 = s3_fx(started=60)
    s3_set_cfg(d2, "stall_budget_minutes", 0)
    o = s3_cmds(d2)
    s3_exit(o, "value 0, 60 minutes in")
    s3_none(o, "value 0, 60 minutes in")


@case("CP3-T16")
def test_CP3_T16_a_live_declared_wait_does_not_hide_the_stall_and_is_named():
    c = s2c()
    g = c.g
    d = s3_fx(started=100)
    g.say(d, "I will wait for the long test run")
    r = g.rx(d, "wait", "--for", "long test run", "--minutes", "30", "--session", "s-owner")
    check("(setup) `ratchet.py wait` is accepted", r.returncode == 0, (r.returncode, r.stdout[-200:], r.stderr[-200:]))

    def back(st):
        now = datetime.now(timezone.utc)
        if isinstance(st.get("wait"), dict):
            st["wait"]["set_at"], st["wait"]["until"] = iso(now - M(10)), iso(now + M(20))
        # the wait must be the newest event: back-date the fixture's own seconds-old history entries to before it
        for ent in st.get("history") or []:
            t = pt(ent.get("at")) if isinstance(ent, dict) else None
            if t is not None and t > now - M(10):
                ent["at"] = iso(now - M(12))
    c.mutate(d, back)
    o = s3_cmds(d)
    sv, nv, line = s3_fires(o, "stalled while waiting", 100)
    check("the STALL line mentions the declared wait", line is not None and
          (re.search(r"\bwait", line, re.I) is not None or "long test run" in line), line)
    w = (o.n or {}).get("wait")
    check("next.wait still holds the wait and its reason", isinstance(w, dict) and w.get("reason") == "long test run", w)
    with s2_pinned("cp3t16") as pin:
        before = s3_json(c.run(pin, c.clone(d), ["next"]).stdout)
    check("(sanity) the pinned engine prints this `next`", before is not None, None)
    if before is not None and o.n is not None:
        check("next.stop_ok is what it was without the stall", o.n.get("stop_ok") == before.get("stop_ok"),
              (o.n.get("stop_ok"), before.get("stop_ok")))
        check("next.wait equals the pinned engine's", o.n.get("wait") == before.get("wait"), (o.n.get("wait"), before.get("wait")))


@case("CP3-T17")
def test_CP3_T17_broken_or_missing_timestamps_degrade_quietly():
    c = s2c()
    try:
        saved = set(json.loads(S2_BASELINE.read_text()))
    except (OSError, ValueError):
        saved = set()
    check("(sanity) the saved status --json baseline has keys", len(saved) > 5, saved)

    def start(v):
        def f(st):
            st["checkpoints"]["CP2"]["started_at"] = v
        return f

    def hist_bad(st):
        st["history"].append({"at": "not-a-time", "event": "red", "cp": "CP2"})

    def hist_not_list(st):
        st["history"] = "oops"

    quiet = (("started_at missing", start(None), 100), ("started_at 'yesterday-ish'", start("yesterday-ish"), 100),
             ("started_at 2 hours in the future", lambda st: st["checkpoints"]["CP2"].update(
                 started_at=iso(datetime.now(timezone.utc) + M(120))), 100))
    for label, edit, _m in quiet:
        o = s3_cmds(s3_fx(started=100, edit=edit))
        s3_exit(o, label)
        s3_none(o, label)
        if o.j is not None:
            check(f"{label}: every existing status --json key still prints", saved <= set(o.j), sorted(saved - set(o.j)))
        if o.n is not None:
            check(f"{label}: next still has phase, checkpoint, step and action",
                  all(k in o.n for k in ("phase", "checkpoint", "step", "action")), sorted(o.n))
    for label, edit in (("a history entry with an unreadable time", hist_bad), ("history is not a list", hist_not_list)):
        d = s3_fx(started=100, edit=edit)
        o = s3_cmds(d)
        s3_fires(o, label + " beside a valid start", 100, since=s3_started(d), since_event="start")
        if o.j is not None:
            check(f"{label}: every existing status --json key still prints", saved <= set(o.j), sorted(saved - set(o.j)))
    d = s3_fx(started=100, edit=lambda st: st["checkpoints"].pop("CP2"))
    r = c.run(c.ENGINE, d, ["status"])
    check("the current step's checkpoint entry missing: plain `status` exits 0 with no traceback and no STALL",
          r.returncode == 0 and "Traceback" not in r.stderr and "STALL" not in r.stdout, (r.returncode, r.stderr[-200:], r.stdout[:200]))
    check("(same variant) plain `status` still prints its Phase line", "Phase:" in r.stdout, r.stdout[:200])


def s3_strip(text, d, other):
    """A plain-status text with this clone's own path normalized and the lines that depend on the step's start time
    (the CP2 projected finish) removed; STALL lines are left in."""
    t = s2c()._normalize_clone_text(text, d, other)
    return "".join(ln for ln in t.splitlines(keepends=True) if not ln.startswith(S2_PREFIX))


def s3_twins(step="implement", attempts2=None, adv_total=0):
    return (s3_fx(started=100, step=step, attempts2=attempts2, adv_total=adv_total),
            s3_fx(started=10, step=step, attempts2=attempts2, adv_total=adv_total))


@case("CP3-T18")
def test_CP3_T18_a_stall_changes_no_existing_status_or_next_field():
    d, twin = s3_twins()
    o, ot = s3_cmds(d), s3_cmds(twin)
    s3_fires(o, "stalled", 100)
    s3_exit(ot, "twin")
    if o.j is not None and ot.j is not None:
        a, b = dict(o.j), dict(ot.j)
        for x in (a, b):
            for k in ("root", "viewer_url", "schedule"):
                x.pop(k, None)
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        check("status --json: every field but `schedule` is identical in the stalled and the unstalled project", not diff,
              [(k, a.get(k), b.get(k)) for k in diff][:3])
    if o.n is not None and ot.n is not None:
        a, b = dict(o.n), dict(ot.n)
        a.pop("stall", None)
        b.pop("stall", None)
        check("next: every field but `stall` is identical", a == b, [(k, a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)][:3])
    ts, tt = s3_strip(o.rp.stdout, d, twin), s3_strip(ot.rp.stdout, twin, d)
    only = [ln for ln in ts.splitlines(keepends=True) if ln.startswith(S3_TAG)]
    rest = "".join(ln for ln in ts.splitlines(keepends=True) if not ln.startswith(S3_TAG))
    check("the stalled status text is the unstalled text plus exactly one STALL line", len(only) == 1 and rest == tt and tt != "",
          (only, rest[-150:], tt[-150:]))


def s3_stop(d):
    c = s2c()
    payload = json.dumps({"cwd": str(d), "session_id": "s-owner", "hook_event_name": "Stop"})
    return subprocess.run([sys.executable, str(c.ENGINE), "hook", "stop", "--host", "claude"], cwd=str(d),
                          capture_output=True, text=True, env=c.g.ENV, timeout=60, input=payload)


@case("CP3-T19")
def test_CP3_T19_the_stop_hook_decides_the_same_with_and_without_a_stall():
    c = s2c()
    d, twin = s3_twins()
    check("(sanity) the stalled twin really shows a stall in `next`", isinstance(s3_nj(s3_cmds(d))[1], dict), None)
    r1, r2 = s3_stop(d), s3_stop(twin)
    check("(sanity) the stop is held while the step is unfinished (exit 2 in both)", r1.returncode == 2 == r2.returncode,
          (r1.returncode, r2.returncode, r1.stderr[-200:]))
    n1 = (c._normalize_clone_text(r1.stdout, d, twin), c._normalize_clone_text(r1.stderr, d, twin))
    n2 = (c._normalize_clone_text(r2.stdout, twin, d), c._normalize_clone_text(r2.stderr, twin, d))
    check("the hook gives the same exit code, decision and reason text with and without the stall",
          r1.returncode == r2.returncode and n1 == n2, (n1, n2))
    check("no STALL text enters the hook output", "STALL" not in r1.stdout + r1.stderr, (r1.stdout + r1.stderr)[:300])
    for label, dd in (("stalled", d), ("twin", twin)):
        log = Path(dd) / ".ratchet" / "hook-errors.log"
        check(f"{label}: the hook error log stays empty", (not log.exists()) or log.read_text() == "", None)
        crashes = Path(dd) / ".ratchet" / "hook-crashes"
        marks = [p.name for p in crashes.iterdir() if ".crash." in p.name] if crashes.is_dir() else []
        check(f"{label}: no hook crash is recorded", not marks, marks)


@case("CP3-T20")
def test_CP3_T20_checking_status_and_next_is_read_only():
    c = s2c()
    d = s3_fx(started=100)
    p = Path(d) / ".ratchet" / "runs" / "f" / "state.json"
    first = s3_cmds(d)
    check("(sanity) the stall is visible in all three outputs",
          len(s3_lines(first.rp.stdout)) == 1 and isinstance(s3_sj(first)[1], dict) and isinstance(s3_nj(first)[1], dict), None)
    raw, hist_len, stamp, tree = p.read_bytes(), len(s3_state(d)["history"]), s3_state(d).get("updated_at"), s2_tree_hash(d)
    for args in (["status"], ["status", "--json"], ["next"]) * 2:
        r = c.run(c.ENGINE, d, args)
        check(f"`{' '.join(args)}` exits 0", r.returncode == 0, (r.returncode, r.stderr[-200:]))
    check("state.json is byte-identical after checking", p.read_bytes() == raw, None)
    check("no history entry was added (a warning is not a logged event)", len(s3_state(d)["history"]) == hist_len,
          (hist_len, len(s3_state(d)["history"])))
    check("updated_at is unchanged", s3_state(d).get("updated_at") == stamp, (stamp, s3_state(d).get("updated_at")))
    check("state.json, config.json and the evidence folder are unchanged", s2_tree_hash(d) == tree, None)


@case("CP3-T21")
def test_CP3_T21_gate_counters_and_limits_are_the_same_in_a_stalled_run():
    d, twin = s3_twins(step="adversarial", attempts2=2, adv_total=2)
    o, ot = s3_cmds(d), s3_cmds(twin)
    s3_exit(o, "stalled at adversarial")
    s3_exit(ot, "twin at adversarial")
    check("the stalled run shows its stall (it is the only difference)", len(s3_lines(o.rp.stdout)) == 1
          and isinstance(s3_sj(o)[1], dict) and isinstance(s3_nj(o)[1], dict), (s3_lines(o.rp.stdout), s3_sj(o), s3_nj(o)))
    if o.j is None or ot.j is None or o.n is None or ot.n is None:
        return

    def gates(j):
        return [(x.get("id"), x.get("status"), x.get("step"), x.get("gates")) for x in j.get("checkpoints") or []]
    check("(sanity) two review rounds are recorded on CP2's adversarial gate and in the run total",
          o.j.get("adv_rounds_used") == 2 and ((o.j["checkpoints"][1].get("gates") or {}).get("adversarial") or {}).get("attempts") == 2,
          (o.j.get("adv_rounds_used"), o.j["checkpoints"][1].get("gates")))
    check("attempts, rounds and every gate entry are identical", gates(o.j) == gates(ot.j), (gates(o.j), gates(ot.j)))
    for k in ("adv_rounds_used", "blocked_reason", "blocked_reason_plain", "accept_note", "waiting_on", "stop_ok", "phase", "step",
              "current", "next", "approval_mode"):
        check(f"status --json {k} is identical", o.j.get(k) == ot.j.get(k), (o.j.get(k), ot.j.get(k)))
    a, b = dict(o.n), dict(ot.n)
    a.pop("stall", None)
    b.pop("stall", None)
    check("next (apart from `stall`) is identical: action, options, limits and counters",
          a == b, [(k, a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)][:3])


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
