#!/usr/bin/env python3
"""
CP3 tests: out of credits waits for the real reset.   python3 tests/test_quota_reset.py

When a provider says it is out of credits (Codex: "You've hit your usage limit ... try again at <date>", "You're out
of credits"; OpenCode Go: "Rolling usage limit reached. It will reset in 3 hours 12 minutes."), a review that fails
with that message benches the provider until the reset time the provider gave (or for quota_retest_minutes, default
20, when it gave none or a time already past), and a passing tiny test request does not end that bench. Only the
bench's end, or a real review that succeeds, does. A failure that is not about quota (signed out, a 503) keeps the
usual unavailable_ttl_hours bench, and a good review that merely quotes the limit message does not bench anyone.

Every project is a throwaway git project (tests/test_gates.py's project(), taken to the adversarial step of a
normal-risk step) with its own per-user folder (RATCHET_HOME) and its own private PATH holding only fake agent CLIs
(`codex`, `opencode`) plus the few real binaries the engine itself needs. A fake answers the connectivity check
normally; when a `<name>.say` file sits beside it, it prints that file's text (on stderr, or on stdout when a
`<name>.stdout` marker is there too) and exits 1 like a CLI that is out of quota; delete the file and it is healthy
again. `<name>.reply` makes it return that text as its review. Each call is logged to $RATCHET_FAKE_LOGS/calls.log.
The fixture texts are the ones the installed CLIs print.

Times are built from this test's own clock at run time (now plus N minutes, cut to the minute), written as
wall-clock text in the fixed-offset POSIX zone given to every engine subprocess in TZ (IST-5:30, PST8, and a zone
chosen so the local time is about noon, so a reset 40 minutes away never crosses midnight). No test waits for a
real reset (the one that waits for a bench to end uses a bench of under two seconds) and none depends on the
machine's own zone. "Benched until" is read from the plain `reviewer-status` line
"failed recently (...); retry after YYYY-MM-DDTHH:MMZ" (UTC, minute precision) and compared to the expected UTC time
within 3 minutes; the gaps between the right answer and the wrong ones (6 hours, 20 minutes, a zone misread of 5.5
or 8 hours) are far larger. No real provider is called, there is no network and no money is spent.

Each case is its own function, registered with @case("CP3-Txx") on the line directly above it and run from main()
inside try/except, so a crash is a failed case, never a silent drop. Writes a per-test JSON report to
RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP3-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, write_tests, rx, ENV)

EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
UTC = datetime.timezone.utc
MIN = datetime.timedelta(minutes=1)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
_CUR_CASE: str | None = None
TESTS: list = []
ORIG_HOME = g.ENV["RATCHET_HOME"]


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    label = f"[{_CUR_CASE}] {name}" if _CUR_CASE else name
    if _CUR_CASE:
        CASE_STATUS[_CUR_CASE] = CASE_STATUS.get(_CUR_CASE, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(_CUR_CASE, []).append(name)
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"   {str(detail)[:400]}"))


class Stop(Exception):
    """A set-up step of the case failed (already reported as a failed check): nothing later can mean anything."""


def need(name, cond, detail=""):
    check(name, cond, detail)
    if not cond:
        raise Stop(name)


def case(cid):
    def deco(fn):
        TESTS.append((cid, fn))
        return fn
    return deco


# ------------------------------------------------------------------------------------------------ the clock

def utcnow():
    return datetime.datetime.now(UTC)


def in_minutes(n):
    """The reset moment 'now plus n minutes', cut to the minute (the way a provider writes it)."""
    return (utcnow() + datetime.timedelta(minutes=n)).replace(second=0, microsecond=0)


# Fixed-offset POSIX zones (no daylight saving): the TZ string and the zone's offset from UTC.
IST = ("IST-5:30", datetime.timedelta(hours=5, minutes=30))
PST = ("PST8", datetime.timedelta(hours=-8))


def midday_zone():
    """A whole-hour zone in which the local time is about noon right now."""
    off = (12 - utcnow().hour) % 24
    if off > 12:
        off -= 24
    return (f"MID{-off}" if off else "MID0", datetime.timedelta(hours=off))


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def clock_text(moment, zone):
    """'2:04 PM' - the wall-clock time of `moment` in `zone` (Codex's text for a same-day reset)."""
    loc = moment + zone[1]
    return f"{loc.hour % 12 or 12}:{loc.minute:02d} {'AM' if loc.hour < 12 else 'PM'}"


def date_text(moment, zone, ordinal=False):
    """'Oct 14, 2026 2:04 PM' (or 'Oct 14th, 2026 2:04 PM'): `moment` as wall-clock text in `zone`."""
    loc = moment + zone[1]
    day = _ordinal(loc.day) if ordinal else str(loc.day)
    return f"{MONTHS[loc.month - 1]} {day}, {loc.year} {clock_text(moment, zone)}"


def near(t, expected, minutes=3):
    return t is not None and abs((t - expected).total_seconds()) <= minutes * 60


def retry_after(out, rid):
    """The UTC time on reviewer-status's 'retry after YYYY-MM-DDTHH:MMZ' for reviewer `rid`, or None."""
    m = re.search(r"retry after (\d{4}-\d\d-\d\dT\d\d:\d\d)Z", status_line(out, rid))
    return datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M").replace(tzinfo=UTC) if m else None


def stamps(text):
    """Every YYYY-MM-DDTHH:MM time (UTC) printed in `text`."""
    return [datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)
            for s in re.findall(r"\d{4}-\d\d-\d\dT\d\d:\d\d", text or "")]


# ------------------------------------------------------------------------------------------------ the provider texts

def codex_limit(when):
    """What Codex prints when the plan's usage limit is reached; `when` is the text after 'try again at'."""
    return ("You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
            f"https://chatgpt.com/codex/settings/usage to purchase more credits or try again at {when}.")


CODEX_NO_TIME = "You're out of credits. Your workspace is out of credits. Add credits to continue using Codex."


def opencode_limit(duration):
    return (f"Rolling usage limit reached. It will reset in {duration}. To continue using this model now, enable "
            "usage from your available balance - https://opencode.ai/workspace/wrk_demo/go")


CODEX = [{"id": "codex", "preset": "codex"}]
OPENCODE = [{"id": "opencode-glm", "preset": "opencode", "model": "opencode-go/glm-5.3", "cost": "flat"}]


# ------------------------------------------------------------------------------------------------ fixtures

# One fake agent CLI for every name. It logs "<name> probe|review" first, prints <name>.say (stderr, or stdout with
# the <name>.stdout marker) and exits 1 while that file exists, and otherwise answers the connectivity check with
# RATCHET-PROBE-OK and a review request with <name>.reply (default: a review with no findings).
FAKE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
kind=review
case "$input $args" in *"connectivity check"*) kind=probe;; esac
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s %s\n' "$me" "$kind" >> "$RATCHET_FAKE_LOGS/calls.log"; fi
if [ -f "$here/$me.say" ]; then
  if [ -f "$here/$me.stdout" ]; then cat "$here/$me.say"; else cat "$here/$me.say" >&2; fi
  exit 1
fi
if [ "$kind" = probe ]; then reply="RATCHET-PROBE-OK"
elif [ -f "$here/$me.reply" ]; then reply=$(cat "$here/$me.reply")
else reply='{"findings": []}'; fi
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""


def status_line(out, rid):
    for line in out.splitlines():
        if rid in line.split():
            return line
    return ""


def row(out, rid):
    """What reviewer-status says about `rid` (the whole line), for failure messages."""
    return status_line(out, rid) or "(no line for it) " + out[-200:]


def listed_failed(out, rid):
    line = status_line(out, rid)
    return bool(line) and "failed recently" in line and line.strip().startswith("·")


def listed_available(out, rid):
    line = status_line(out, rid)
    return bool(line) and line.strip().startswith("✓")


def ids(rnd):
    return [x["id"] for x in (rnd.get("reviewers") or [])]


def said(r):
    return (r.stdout or "") + (r.stderr or "")


class Proj:
    """One throwaway project at the adversarial step of a normal-risk step ("plan": just after the plan was
    submitted), with its own per-user folder and a private PATH of fakes, the engine run in time zone `zone`."""

    def __init__(self, tag, fakes, providers, settings=(), zone=None, stage="adv"):
        b = Path(_tmp.mkdtemp(prefix="ratchet-fk-bin-"))
        for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
            p = shutil.which(n)
            if p:
                (b / n).symlink_to(p)
        for n in fakes:
            (b / n).write_text(FAKE)
            (b / n).chmod(0o755)
        self.bin = b
        self.logs = Path(_tmp.mkdtemp(prefix="ratchet-fk-log-"))
        self.home = Path(_tmp.mkdtemp(prefix="ratchet-fk-user-"))
        self.prog = fakes[0]
        self.rounds = 0
        g.ENV["RATCHET_HOME"] = str(self.home)       # set-up uses test_gates' environment: point it at this folder
        try:
            # providers and rules are set BEFORE the plan is approved (a later rule change stops every reviewers call)
            self.dir = g.project(tag, extra_cp={"risk": "normal"},
                                 config=["providers=" + json.dumps(providers), *settings])
            if stage == "adv":
                g.approve_plan(self.dir)
                g.to_adversarial(self.dir)
            self.env = dict(g.ENV)
        finally:
            g.ENV["RATCHET_HOME"] = ORIG_HOME
        self.env["PATH"] = str(b)
        self.env["RATCHET_HOST_PROVIDER"] = "anthropic"
        self.env["RATCHET_FAKE_LOGS"] = str(self.logs)
        self.env["RATCHET_HOME"] = str(self.home)
        if zone:
            self.env["TZ"] = zone[0]

    def run(self, *args):
        return g.rx(self.dir, *args, env=self.env)

    def say(self, text, stdout=False, prog=None):
        """The fake starts failing with `text`, like a CLI that is out of quota (or signed out)."""
        prog = prog or self.prog
        (self.bin / f"{prog}.say").write_text(text + "\n")
        marker = self.bin / f"{prog}.stdout"
        if stdout:
            marker.write_text("x")
        elif marker.exists():
            marker.unlink()

    def heal(self, prog=None):
        f = self.bin / f"{prog or self.prog}.say"
        if f.exists():
            f.unlink()

    def reply(self, text, prog=None):
        (self.bin / f"{prog or self.prog}.reply").write_text(text)

    def probes(self, prog=None):
        p = self.logs / "calls.log"
        lines = p.read_text().splitlines() if p.exists() else []
        return len([x for x in lines if x.endswith(" probe") and x.split()[0] == (prog or self.prog)])

    def open_round(self):
        """Opens a review round (the code is nudged first when a round was already opened, so the old round is
        replaced rather than shown again). Returns (process, round as printed, or {} when it failed)."""
        if self.rounds:
            with open(self.dir / "src" / "f.js", "a") as f:
                f.write(f"// nudge {self.rounds}\n")
        self.rounds += 1
        r = self.run("reviewers", "CP1", "--json")
        try:
            rnd = json.loads(r.stdout) if r.returncode == 0 else {}
        except ValueError:
            rnd = {}
        return r, rnd

    def review(self, rnd, rid):
        """The agent runs one reviewer through external_review.py. Returns (process, the review file's path)."""
        out = Path(rnd["output_dir"]) / f"{rid}.json"
        r = subprocess.run([sys.executable, str(EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out)],
                           cwd=self.dir, capture_output=True, text=True, env=self.env)
        return r, out

    def status(self):
        return self.run("reviewer-status").stdout


def fail_a_review(p, text, stdout=False):
    """Opens the round, makes the fake print `text` and exit 1, and runs the first reviewer: returns
    (reviewer id, the round, external_review's result)."""
    r, rnd = p.open_round()
    need("the round opens with a codex-or-opencode reviewer", r.returncode == 0 and ids(rnd)
         and not ids(rnd)[0].startswith("host"), (said(r)[-300:], ids(rnd)))
    p.say(text, stdout=stdout)
    rid = ids(rnd)[0]
    ext, _out = p.review(rnd, rid)
    return rid, rnd, ext


def expect_bench(label, p, rid, ext, expected, minutes=3):
    """The review failed (exit 3) and reviewer-status shows `rid` failed recently with a retry time within
    `minutes` of `expected`. Returns the retry time."""
    out = p.status()
    got = retry_after(out, rid)
    check(f"{label}: external_review exits 3", ext.returncode == 3, (ext.returncode, ext.stderr[-300:]))
    check(f"{label}: reviewer-status lists {rid} as failed recently", listed_failed(out, rid), row(out, rid))
    check(f"{label}: the retry time is within {minutes} minutes of {expected:%Y-%m-%dT%H:%M}Z", near(got, expected, minutes),
          (f"retry after {got:%Y-%m-%dT%H:%M}Z" if got else status_line(out, rid)))
    return got


# ------------------------------------------------------------------------------------------------ cases

@case("CP3-T01")
def t01_codex_limit_waits_for_the_reset_time_codex_gave_in_the_right_zone():
    for label, zone, ordinal in (("IST-5:30", IST, False), ("PST8", PST, True)):
        p = Proj("pj-01" + label[:3].lower(), ["codex"], CODEX, zone=zone)
        when = in_minutes(90)
        rid, _rnd, ext = fail_a_review(p, codex_limit(date_text(when, zone, ordinal)))
        got = expect_bench(f"{label}", p, rid, ext, when)
        six_hours = utcnow() + datetime.timedelta(hours=6)
        check(f"{label}: the retry time is not the 6-hour default", got is not None and not near(got, six_hours, 30),
              got)
        mis = [when - zone[1], when + zone[1]]
        check(f"{label}: the retry time is not a misread of the zone", got is not None
              and not any(near(got, m, 60) for m in mis), (got, mis))
        printed = stamps(ext.stderr)
        check(f"{label}: external_review's message names the reset time (and not a time 6 hours later)",
              any(near(t, when) for t in printed) and not any(near(t, six_hours, 30) for t in printed),
              ext.stderr[-400:])


@case("CP3-T02")
def t02_a_clock_time_only_reset_means_later_today():
    zone = midday_zone()
    p = Proj("pj-02", ["codex"], CODEX, zone=zone)
    when = in_minutes(40)
    rid, _rnd, ext = fail_a_review(p, codex_limit(clock_text(when, zone)))
    got = expect_bench("a clock-only reset (" + clock_text(when, zone) + ")", p, rid, ext, when)
    check("the retry time is not a day later and not the 6-hour default", got is not None
          and not near(got, when + datetime.timedelta(hours=24), 60) and not near(got, utcnow() + datetime.timedelta(hours=6), 30),
          got)


@case("CP3-T03")
def t03_a_reset_time_already_past_is_tried_again_soon():
    zone = midday_zone()
    full = Proj("pj-03a", ["codex"], CODEX, zone=IST)
    past = in_minutes(-30)
    rid, _rnd, ext = fail_a_review(full, codex_limit(date_text(past, IST)))
    expect_bench("a full date 30 minutes in the past", full, rid, ext, in_minutes(20))
    clock = Proj("pj-03b", ["codex"], CODEX, zone=zone)
    past = in_minutes(-30)
    rid, _rnd, ext = fail_a_review(clock, codex_limit(clock_text(past, zone)))
    got = expect_bench("a clock time 30 minutes earlier today", clock, rid, ext, in_minutes(20))
    check("the clock-only past time is not read as tomorrow (no bench of about 23.5 hours)", got is not None
          and not near(got, utcnow() + datetime.timedelta(hours=23.5), 60), got)


@case("CP3-T04")
def t04_try_again_in_a_duration_waits_that_long():
    p = Proj("pj-04a", ["codex"], CODEX, zone=IST)
    when = utcnow() + datetime.timedelta(minutes=135)
    rid, _rnd, ext = fail_a_review(p, "You've hit your usage limit. Try again in 2 hours 15 minutes.")
    expect_bench("'in 2 hours 15 minutes'", p, rid, ext, when)
    q = Proj("pj-04b", ["codex"], CODEX, zone=IST)
    when = utcnow() + datetime.timedelta(hours=27)
    rid, _rnd, ext = fail_a_review(q, "You've hit your usage limit. Try again in 1 day 3 hours.")
    expect_bench("'in 1 day 3 hours'", q, rid, ext, when)


@case("CP3-T05")
def t05_opencode_go_limit_waits_for_the_it_will_reset_in_time():
    variants = [
        ("a", "3 hours 12 minutes", 192, opencode_limit("3 hours 12 minutes")),
        ("b", "1 day 2 hours", 26 * 60, opencode_limit("1 day 2 hours")),
        ("c", "5 minutes", 5, opencode_limit("5 minutes")),
        ("d", "less than a minute", 20, opencode_limit("less than a minute")),
        ("e", "(no time)", 20, opencode_limit("")),
    ]
    for tag, label, minutes, text in variants:
        p = Proj("pj-05" + tag, ["opencode"], OPENCODE, zone=IST)
        when = utcnow() + datetime.timedelta(minutes=minutes)
        rid, _rnd, ext = fail_a_review(p, text)
        got = expect_bench(f"'It will reset in {label}'", p, rid, ext, when)
        check(f"'It will reset in {label}': not the 6-hour default",
              got is not None and not near(got, utcnow() + datetime.timedelta(hours=6), 30), got)


@case("CP3-T06")
def t06_out_of_credits_with_no_reset_time_waits_the_configured_minutes():
    p = Proj("pj-06a", ["codex"], CODEX, zone=IST)
    when = utcnow() + datetime.timedelta(minutes=20)
    rid, _rnd, ext = fail_a_review(p, CODEX_NO_TIME)
    expect_bench("default quota_retest_minutes (20)", p, rid, ext, when)
    q = Proj("pj-06b", ["codex"], CODEX, settings=["quota_retest_minutes=45"], zone=IST)
    when = utcnow() + datetime.timedelta(minutes=45)
    rid, _rnd, ext = fail_a_review(q, CODEX_NO_TIME)
    got = expect_bench("quota_retest_minutes=45", q, rid, ext, when)
    check("quota_retest_minutes=45: not the 6-hour default", got is not None
          and not near(got, utcnow() + datetime.timedelta(hours=6), 30), got)


@case("CP3-T07")
def t07_the_limit_message_is_found_when_not_last_or_on_stdout():
    for tag, stdout in (("a", False), ("b", True)):
        zone = IST
        p = Proj("pj-07" + tag, ["codex"], CODEX, zone=zone)
        when = in_minutes(90)
        text = codex_limit(date_text(when, zone)) + "\ntokens used\n12,345"
        rid, _rnd, ext = fail_a_review(p, text, stdout=stdout)
        got = expect_bench("limit message followed by two more lines, on " + ("stdout" if stdout else "stderr"),
                           p, rid, ext, when)
        check("not the 6-hour default (" + ("stdout" if stdout else "stderr") + ")", got is not None
              and not near(got, utcnow() + datetime.timedelta(hours=6), 30), got)


@case("CP3-T08")
def t08_a_passing_test_request_does_not_end_a_quota_bench():
    zone = IST
    p = Proj("pj-08a", ["codex"], CODEX, zone=zone)
    when = in_minutes(180)
    rid, _rnd, ext = fail_a_review(p, codex_limit(date_text(when, zone)))
    before_out = p.status()
    before = retry_after(before_out, rid)
    need("set-up: the failed review benched codex", listed_failed(before_out, rid) and before is not None, row(before_out, rid))
    check("set-up: ... until the reset time codex gave", near(before, when), before)
    p.heal()
    sent = p.probes()
    r = p.run("providers", "--check", "all", "--json")
    try:
        rows = {x["id"]: x for x in json.loads(r.stdout).get("providers") or []}
    except (ValueError, TypeError):
        rows = {}
    check("providers --check all exits 0 and reports the test request passed", r.returncode == 0
          and (rows.get("codex") or {}).get("ok") is True, (said(r)[-300:], rows.get("codex")))
    check("providers --check all really sent a test request", p.probes() > sent, p.probes())
    out = p.status()
    check("after the check, reviewer-status still lists codex as failed recently", listed_failed(out, rid), row(out, rid))
    check("... with the same retry time as before", retry_after(out, rid) is not None
          and near(retry_after(out, rid), before, 1), (retry_after(out, rid), before))
    sent = p.probes()
    r = p.run("providers", "--check", "codex-correctness", "--json")
    try:
        rows = {x["id"]: x for x in json.loads(r.stdout).get("providers") or []}
    except (ValueError, TypeError):
        rows = {}
    check("providers --check codex-correctness exits 0 and reports the test request passed", r.returncode == 0
          and (rows.get("codex") or {}).get("ok") is True, (said(r)[-300:], rows.get("codex")))
    check("providers --check codex-correctness really sent a test request", p.probes() > sent, p.probes())
    out = p.status()
    check("after that check too, codex is still failed recently with the same retry time",
          listed_failed(out, rid) and near(retry_after(out, rid), before, 1), (retry_after(out, rid), before))
    r2, rnd2 = p.open_round()
    check("a new round opens", r2.returncode == 0 and ids(rnd2) != [], said(r2)[-300:])
    check("the new round's reviewer list contains no codex reviewer",
          ids(rnd2) != [] and not any(i.startswith("codex") for i in ids(rnd2)), ids(rnd2))
    # a failing test request found by a check puts a quota bench at the provider's own reset time
    q = Proj("pj-08b", ["codex"], CODEX, zone=zone, stage="plan")
    when = in_minutes(180)
    q.say(codex_limit(date_text(when, zone)))
    r = q.run("providers", "--check", "codex")
    check("providers --check on a provider that is out of credits exits 0", r.returncode == 0, said(r)[-300:])
    out = q.status()
    got = retry_after(out, "codex-correctness")
    check("a failing test request benches codex until the provider's own reset time", listed_failed(out, "codex-correctness")
          and near(got, when), (got, status_line(out, "codex-correctness")))
    check("... not the 6-hour default", got is not None and not near(got, utcnow() + datetime.timedelta(hours=6), 30),
          got)


@case("CP3-T09")
def t09_a_quota_bench_ends_on_its_own_at_its_time():
    p = Proj("pj-09", ["codex"], CODEX, settings=["quota_retest_minutes=0.03"], zone=IST)
    rid, _rnd, ext = fail_a_review(p, CODEX_NO_TIME)
    p.heal()
    check("the review fails with exit 3", ext.returncode == 3, (ext.returncode, ext.stderr[-300:]))
    out = p.status()
    check("immediately, reviewer-status lists codex as failed recently", listed_failed(out, rid), row(out, rid))
    time.sleep(4.5)
    out = p.status()
    check("after about 4 seconds the bench has ended: codex is available again", listed_available(out, rid), row(out, rid))
    r, rnd = p.open_round()
    check("a new round opens", r.returncode == 0 and ids(rnd) != [], said(r)[-300:])
    check("... and lists a codex reviewer", any(i.startswith("codex") for i in ids(rnd)), ids(rnd))


@case("CP3-T10")
def t10_a_real_review_that_succeeds_ends_the_quota_bench_early():
    zone = IST
    p = Proj("pj-10", ["codex"], CODEX, zone=zone)
    when = in_minutes(180)
    rid, rnd, ext = fail_a_review(p, codex_limit(date_text(when, zone)))
    out = p.status()
    need("set-up: codex is benched", listed_failed(out, rid), row(out, rid))
    check("set-up: ... until the reset time codex gave", near(retry_after(out, rid), when), out)
    p.heal()
    ext2, review = p.review(rnd, rid)
    check("the next run of the codex reviewer exits 0", ext2.returncode == 0, (ext2.returncode, ext2.stderr[-300:]))
    body = {}
    if review.exists():
        try:
            body = json.loads(review.read_text())
        except ValueError:
            body = {}
    check("... and writes the review with no findings", body.get("findings") == [] and body.get("reviewer") == rid, body)
    sig = body.get("signature") if isinstance(body.get("signature"), dict) else {}
    check("... signed by external_review.py", sig.get("alg") == "hmac-sha256" and sig.get("by") == "external_review.py",
          sig)
    out = p.status()
    check("reviewer-status then shows the codex reviewer as available", listed_available(out, rid), row(out, rid))


@case("CP3-T11")
def t11_a_failure_that_is_not_about_quota_keeps_todays_handling():
    p = Proj("pj-11a", ["codex"], CODEX, zone=IST)
    rid, _rnd, ext = fail_a_review(p, "error: not signed in. Run codex login")
    expect_bench("not signed in", p, rid, ext, utcnow() + datetime.timedelta(hours=6))
    q = Proj("pj-11b", ["codex"], CODEX, zone=IST)
    rid, _rnd, ext = fail_a_review(q, "error: service unavailable (503), try again later")
    expect_bench("a 503 that says try again later", q, rid, ext, utcnow() + datetime.timedelta(hours=6))
    s = Proj("pj-11c", ["codex"], CODEX, settings=["unavailable_ttl_hours=2"], zone=IST)
    rid, _rnd, ext = fail_a_review(s, "error: not signed in. Run codex login")
    expect_bench("not signed in with unavailable_ttl_hours=2", s, rid, ext, utcnow() + datetime.timedelta(hours=2))


@case("CP3-T12")
def t12_a_good_review_that_merely_quotes_the_limit_message_does_not_bench_the_provider():
    p = Proj("pj-12", ["codex"], CODEX, zone=IST)
    r, rnd = p.open_round()
    need("the round opens with a codex reviewer", r.returncode == 0 and ids(rnd) and ids(rnd)[0].startswith("codex"),
         (said(r)[-300:], ids(rnd)))
    desc = ("The code shows the message You've hit your usage limit ... try again at 3:00 PM to the user, "
            "which hides what the user can do next")
    p.reply(json.dumps({"findings": [{"id": "Q1", "severity": "minor", "file": "src/f.js", "description": desc}]}))
    rid = ids(rnd)[0]
    ext, review = p.review(rnd, rid)
    check("external_review exits 0", ext.returncode == 0, (ext.returncode, ext.stderr[-300:]))
    body = {}
    if review.exists():
        try:
            body = json.loads(review.read_text())
        except ValueError:
            body = {}
    found = [f.get("description") for f in body.get("findings") or []]
    check("the review is saved with that finding", found == [desc], found)
    out = p.status()
    check("reviewer-status shows the codex reviewer as available", listed_available(out, rid), row(out, rid))


@case("CP3-T13")
def t13_docs_describe_quota_benches_reset_times_and_the_retest_setting():
    docs = {"README.md": ROOT / "README.md",
            "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
            "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md"}
    texts = {k: v.read_text() for k, v in docs.items()}

    def windows(label, needle, before=500, after=700):
        low = texts[label].lower()
        return [low[max(0, m.start() - before):m.end() + after] for m in re.finditer(re.escape(needle.lower()), low)]

    for label in ("schemas.md", "README.md"):
        ws = windows(label, "quota_retest_minutes", 300, 400)
        check(f"{label} names quota_retest_minutes", bool(ws), "not mentioned")
        check(f"{label} gives quota_retest_minutes its default of 20", any(re.search(r"\b20\b", w) for w in ws),
              "no 20 near quota_retest_minutes")
    for label in ("README.md", "SKILL.md", "schemas.md"):
        ws = windows(label, "out of credits")
        check(f"{label} says a provider out of credits waits for the reset time it gave",
              any("reset time" in w and re.search(r"wait|until|bench|skipped", w) for w in ws),
              "no 'out of credits' near 'reset time' and a wait")
    ws = windows("schemas.md", "quota bench")
    check("schemas.md says a passing test request does not end a quota bench",
          any("test request" in w and re.search(
              r"(does not|doesn't|do not|never|not) (end|clear|lift|cancel|undo|put|bring|release)|cannot|can't", w)
              for w in ws), "no 'quota bench' near 'test request' and a 'does not end'")


# ------------------------------------------------------------------------------------------------ round 1 fixes

sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import provider_health as ph    # noqa: E402


@case("CP3-X01")
def x01_a_review_sent_before_a_newer_quota_failure_does_not_end_its_bench():
    t0 = datetime.datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def sec(n):
        return t0 + datetime.timedelta(seconds=n)
    bench_to = sec(6 * 3600)
    # a quota failure is recorded at 10 s; a review sent at 5 s (already under way) finishes later: no effect
    d = {}
    ph.record_probe(d, "k", False, "out of credits", sec(8), "out of credits", bench_to, now=sec(10), quota=True)
    check("a review sent before the quota bench began does not end it", ph.end_quota_bench(d, "k", sec(5)) is False
          and ph.live_bench(d, "k", sec(200)) is not None, d)
    # a review sent after the bench began and then succeeded ends it
    check("a review sent after the quota bench began ends it", ph.end_quota_bench(d, "k", sec(20)) is True
          and ph.live_bench(d, "k", sec(200)) is None, d)
    # a second, newer quota failure: a review sent between the two failures does not end the newer bench
    d = {}
    ph.record_probe(d, "k", False, "out of credits", sec(0), "out of credits", bench_to, now=sec(10), quota=True)
    ph.record_probe(d, "k", False, "out of credits", sec(45), "out of credits", bench_to, now=sec(50), quota=True)
    check("a review sent between two quota failures does not end the newer bench",
          ph.end_quota_bench(d, "k", sec(30)) is False and ph.live_bench(d, "k", sec(200)) is not None, d)
    # a bench of any other kind is never ended by a review
    d = {}
    ph.record_probe(d, "k", False, "signed out", sec(0), "signed out", bench_to, now=sec(10))
    check("a bench that is not a quota bench stays", ph.end_quota_bench(d, "k", sec(20)) is False
          and ph.live_bench(d, "k", sec(200)) is not None, d)
    # end to end: external_review hands over when it sent the review, so a newer bench survives a late success
    p = Proj("pj-x01", ["codex"], CODEX, zone=IST)
    when = in_minutes(180)
    rid, rnd, ext = fail_a_review(p, codex_limit(date_text(when, IST)))
    need("set-up: codex is benched", listed_failed(p.status(), rid), row(p.status(), rid))
    health = p.home / "provider-health.json"
    data = json.loads(health.read_text())
    for entry in data.values():
        if isinstance(entry, dict) and isinstance(entry.get("bench"), dict):
            entry["bench"]["since"] = (utcnow() + datetime.timedelta(hours=1)).isoformat()   # begins after the review
    health.write_text(json.dumps(data))
    p.heal()
    ext2, _review = p.review(rnd, rid)
    check("the review itself succeeds", ext2.returncode == 0, (ext2.returncode, ext2.stderr[-300:]))
    check("... but the bench that began after it was sent stays", listed_failed(p.status(), rid), row(p.status(), rid))


@case("CP3-X02")
def x02_a_failed_reply_that_quotes_the_loose_reset_phrase_does_not_bench():
    now = utcnow()
    check("'It will reset in' on stderr is a quota failure", ph.quota_failure(["It will reset in 3 hours", ""], now)
          is not None)
    check("... but the same words on stdout or in the reply file are not",
          ph.quota_failure(["", "It will reset in 3 hours", "the plan says it will reset in 5 minutes"], now) is None)
    check("the strict wording still counts on stdout", ph.quota_failure(["", "You're out of credits"], now)
          is not None)
    p = Proj("pj-x02", ["codex"], CODEX, zone=IST)
    rid, _rnd, ext = fail_a_review(p, "My notes: the message says the quota will reset in 3 hours, which is shown "
                                      "to the user. (no findings JSON)", stdout=True)
    expect_bench("a failed stdout reply quoting 'will reset in'", p, rid, ext, utcnow() + datetime.timedelta(hours=6))


@case("CP3-X03")
def x03_a_fallback_quota_mark_is_not_cleared_by_a_passing_test_request():
    p = Proj("pj-x03", ["codex"], CODEX, zone=IST)
    r, rnd = p.open_round()
    need("the round opens with a codex reviewer", r.returncode == 0 and ids(rnd) and ids(rnd)[0].startswith("codex"),
         (said(r)[-300:], ids(rnd)))
    p.say(codex_limit(date_text(in_minutes(180), IST)))
    lock = p.home / "provider-health.lock"
    lock.write_text("held by this test")       # the shared record is busy: the quota wait is kept in this project
    try:
        ext, _out = p.review(rnd, ids(rnd)[0])
    finally:
        lock.unlink(missing_ok=True)
    marks = json.loads((p.dir / ".ratchet" / "unavailable-reviewers.json").read_text())
    check("the review fails with exit 3", ext.returncode == 3, (ext.returncode, ext.stderr[-300:]))
    check("the project's own mark says it is a quota wait", marks and all(m.get("quota") is True for m in marks.values()),
          marks)
    p.heal()
    r = p.run("providers", "--check", "all", "--json")
    check("a passing test request is recorded", r.returncode == 0, said(r)[-300:])
    out = p.status()
    check("... and the reviewer is still listed as failed recently", listed_failed(out, ids(rnd)[0]),
          row(out, ids(rnd)[0]))


@case("CP3-X04")
def x04_quota_retest_minutes_must_be_a_positive_number():
    p = Proj("pj-x04", ["codex"], CODEX, zone=IST)
    for bad in ("0", "-5", "abc"):
        r = p.run("config", "--set", f"quota_retest_minutes={bad}")
        check(f"config --set quota_retest_minutes={bad} is refused", r.returncode != 0, said(r)[-200:])
        check("... naming the setting and the value, with no traceback", "quota_retest_minutes" in r.stderr
              and bad in r.stderr and "Traceback" not in r.stderr, r.stderr[-300:])
    r = p.run("config", "--set", "quota_retest_minutes=2.5")
    check("a positive number (decimals included) is accepted", r.returncode == 0, said(r)[-200:])
    # a value written into the config by hand is refused when a review needs it
    q = Proj("pj-x04b", ["codex"], CODEX, zone=IST)
    r, rnd = q.open_round()
    need("the round opens", r.returncode == 0 and ids(rnd), said(r)[-300:])
    cfg_path = q.dir / ".ratchet" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["quota_retest_minutes"] = 0
    cfg_path.write_text(json.dumps(cfg))
    q.say(CODEX_NO_TIME)
    ext, _out = q.review(rnd, ids(rnd)[0])
    check("a review that finds the provider out of credits with quota_retest_minutes 0 stops with a plain error",
          ext.returncode != 0 and "quota_retest_minutes" in ext.stderr and "Traceback" not in ext.stderr,
          (ext.returncode, ext.stderr[-300:]))


@case("CP3-X05")
def x05_a_real_review_clears_the_projects_own_quota_marks_sent_after_they_began():
    p = Proj("pj-x05", ["codex"], CODEX, zone=IST)
    r, rnd = p.open_round()
    need("the round opens with a codex reviewer", r.returncode == 0 and ids(rnd) and ids(rnd)[0].startswith("codex"),
         (said(r)[-300:], ids(rnd)))
    rid = ids(rnd)[0]
    p.say(codex_limit(date_text(in_minutes(180), IST)))
    lock = p.home / "provider-health.lock"
    lock.write_text("held by this test")       # the shared record is busy: the quota wait is kept in this project
    try:
        ext, _out = p.review(rnd, rid)
    finally:
        lock.unlink(missing_ok=True)
    need("set-up: the review failed", ext.returncode == 3, (ext.returncode, ext.stderr[-300:]))
    marks_path = p.dir / ".ratchet" / "unavailable-reviewers.json"
    marks = json.loads(marks_path.read_text())
    need("set-up: this project holds quota marks", len(marks) >= 1 and all(m.get("quota") for m in marks.values()), marks)
    saved = json.loads(marks_path.read_text())
    # a mark that began after the review was sent (a newer failure) survives a late success
    later = json.loads(json.dumps(saved))
    for m in later.values():
        m["since"] = (utcnow() + datetime.timedelta(hours=1)).isoformat()
    marks_path.write_text(json.dumps(later))
    p.heal()
    ext2, _review = p.review(rnd, rid)
    check("the review succeeds", ext2.returncode == 0, (ext2.returncode, ext2.stderr[-300:]))
    check("a mark that began after the review was sent stays", listed_failed(p.status(), rid), row(p.status(), rid))
    # a mark that began before the review was sent ends; a mark that is not a quota wait stays
    other = sorted(saved)[-1]
    kept = json.loads(json.dumps(saved))
    kept[other].pop("quota", None)
    marks_path.write_text(json.dumps(kept))
    ext3, _review = p.review(rnd, rid)
    check("the next review succeeds", ext3.returncode == 0, (ext3.returncode, ext3.stderr[-300:]))
    left = json.loads(marks_path.read_text())
    check("the quota marks that began before the review was sent are cleared, and the non-quota mark stays",
          list(left) == [other], (list(left), sorted(saved)))
    if other != rid:
        check("... so the reviewer that ran is listed as available again", listed_available(p.status(), rid),
              row(p.status(), rid))


# ------------------------------------------------------------------------------------------------ runner

def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global _CUR_CASE, FAIL
    for cid, fn in TESTS:
        _CUR_CASE = cid
        CASE_STATUS.setdefault(cid, True)
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Stop:
            pass                       # reported as a failed check already
        except Exception as e:         # a crash is this case's failed result, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        finally:
            g.ENV["RATCHET_HOME"] = ORIG_HOME
    _CUR_CASE = None
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
