#!/usr/bin/env python3
"""
CP2 tests: one shared provider health record.   python3 tests/test_quota_shared.py

Every project (lane) on a developer's machine sees the same provider test results and benches, kept in one
per-user file, provider-health.json, directly in the per-user folder (RATCHET_HOME). One lane's discovery that a
provider is down or back reaches every other lane, so lanes neither hammer a provider that is out nor test it again
needlessly. A reviewer-level mark made by hand (`reviewer-status --mark`) stays in its own project.

Every case builds a fresh "machine": its own per-user folder (RATCHET_HOME), its own private PATH holding only fake
agent CLIs (fakea .. fakeh, each registered as a provider of a different company, none the host's) plus the few real
binaries the engine itself needs, and its own call log. Two or three lanes are throwaway git projects
(tests/test_gates.py's project(), taken to the adversarial step) that all share that machine's per-user folder.

Each fake CLI appends one line per request to the call log: its name plus `probe` for the connectivity check,
`review` otherwise; and it fails like a signed-out tool when a `<name>.fail` marker sits beside it. "Did lane B test
it again" is answered by counting probe lines in that log, never by reading engine internals. No real provider is
ever called, there is no network and no money is spent.

Each case is its own function, registered with @case("CP2-Txx") on the line directly above it and run from main()
inside try/except, so a crash is a failed case, never a silent drop. Writes a per-test JSON report to
RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP2-T01", "status": "passed"|"failed", "message": "..."}]}.
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, rx, ENV)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
_CUR_CASE: str | None = None
TESTS: list = []
ORIG_HOME = g.ENV["RATCHET_HOME"]
HEALTH_FILE = "provider-health.json"        # the binding decision: directly under RATCHET_HOME

COMPANY = {"fakea": "openai", "fakeb": "google", "fakec": "mistral", "faked": "xai", "fakee": "deepseek",
           "fakef": "moonshot", "fakeg": "zhipu", "fakeh": "meta"}


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    if _CUR_CASE:
        CASE_STATUS[_CUR_CASE] = CASE_STATUS.get(_CUR_CASE, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(_CUR_CASE, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def case(cid):
    def deco(fn):
        TESTS.append((cid, fn))
        return fn
    return deco


# ------------------------------------------------------------------------------------------------ fixtures

# One fake agent CLI for every name. It logs "<name> probe|review" to $RATCHET_FAKE_LOGS/calls.log before doing
# anything else (one short appended line, so lanes calling at once do not tear each other's lines), fails with an
# auth-style reason when <name>.fail sits beside it, and otherwise answers the connectivity check with
# RATCHET-PROBE-OK (a review request with an empty findings list).
FAKE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
kind=review
case "$*" in *"connectivity check"*) kind=probe;; esac
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s %s\n' "$me" "$kind" >> "$RATCHET_FAKE_LOGS/calls.log"; fi
if [ -f "$here/$me.fail" ]; then echo "error: not signed in, run login first" >&2; exit 1; fi
if [ "$kind" = probe ]; then echo "RATCHET-PROBE-OK"; else echo '{"findings": []}'; fi
exit 0
"""


class Machine:
    """One developer machine: its per-user folder, its fake CLIs on a private PATH and the call log."""

    def __init__(self, names):
        b = Path(_tmp.mkdtemp(prefix="ratchet-fk-bin-"))
        for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
            p = shutil.which(n)
            if p:
                (b / n).symlink_to(p)
        for n in names:
            (b / n).write_text(FAKE)
            (b / n).chmod(0o755)
        self.bin = b
        self.names = list(names)
        self.logs = Path(_tmp.mkdtemp(prefix="ratchet-fk-log-"))
        self.home = Path(_tmp.mkdtemp(prefix="ratchet-fk-user-"))
        # the setup helpers (project, approve_plan, to_adversarial) use test_gates' own environment: point it at
        # this machine's per-user folder for the length of the case (main() puts it back)
        g.ENV["RATCHET_HOME"] = str(self.home)
        self.env = dict(g.ENV)
        self.env["PATH"] = str(b)
        self.env["RATCHET_HOST_PROVIDER"] = "anthropic"
        self.env["RATCHET_FAKE_LOGS"] = str(self.logs)
        self.env["RATCHET_HOME"] = str(self.home)

    def fail(self, name, on=True):
        marker = self.bin / f"{name}.fail"
        if on:
            marker.write_text("x")
        elif marker.exists():
            marker.unlink()

    def log_lines(self):
        p = self.logs / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    def probes(self, name=None):
        """How many connectivity checks the fake `name` (or any fake) received, from the call log alone."""
        return len([x for x in self.log_lines() if x.endswith(" probe") and (name is None or x.split()[0] == name)])

    def health_file(self):
        return self.home / HEALTH_FILE

    def health_text(self):
        p = self.health_file()
        return p.read_text() if p.exists() else ""

    def health_json(self):
        try:
            return json.loads(self.health_text())
        except ValueError:
            return None


class Lane:
    """One throwaway project at the adversarial step of a normal-risk step, on a machine."""

    def __init__(self, m: Machine, name, providers=(), depth=None, settings=()):
        self.m = m
        self.rounds = 0
        self.dir = g.project(name, extra_cp={"risk": "normal"})
        # providers and rules are set up BEFORE the plan is approved: a change to the review rules after approval
        # would stop every reviewers call until approve-changes
        for n in providers:
            r = self.run("providers", "--add", n, "--command", json.dumps([n, "{prompt}"]), "--company",
                         COMPANY[n], "--no-check")
            assert r.returncode == 0, r.stdout + r.stderr
        sets = list(settings)
        if depth is not None:
            sets.append("review_depth=" + json.dumps({"normal": depth}))
        if sets:
            r = self.run("config", "--set", *sets)
            assert r.returncode == 0, r.stdout + r.stderr
        g.approve_plan(self.dir)
        g.to_adversarial(self.dir)

    def run(self, *args):
        return g.rx(self.dir, *args, env=self.m.env)

    def popen(self, *args):
        return subprocess.Popen([sys.executable, str(g.ENGINE), *args], cwd=self.dir, env=self.m.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def open_round(self):
        """Open a fresh review round (the code is nudged first when a round was already opened, so the old round
        is replaced rather than shown again). Returns (process, round as printed, or {} when it failed)."""
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

    def status(self):
        return self.run("reviewer-status").stdout


def make_lanes(m, specs):
    """Build several lanes at once: [(name, kwargs)] -> [Lane]. Setup is the slow part; nothing here sends a
    request to any fake."""
    with ThreadPoolExecutor(max_workers=len(specs)) as ex:
        futs = [ex.submit(Lane, m, name, **kw) for name, kw in specs]
        return [f.result() for f in futs]


def ids(rnd):
    return [x["id"] for x in (rnd.get("reviewers") or [])]


def used(rnd, name):
    """The round's reviewer ids that belong to provider `name` (fakea-both and so on)."""
    return [i for i in ids(rnd) if i.startswith(name + "-")]


def status_line(out, rid):
    for line in out.splitlines():
        if rid in line.split():
            return line
    return ""


def listed_failed(out, rid):
    line = status_line(out, rid)
    return bool(line) and "failed recently" in line and line.strip().startswith("·")


def listed_available(out, rid):
    line = status_line(out, rid)
    return bool(line) and line.strip().startswith("✓")


def said(r):
    return (r.stdout or "") + (r.stderr or "")


def depth_for(*names, lens="both"):
    """One slot per provider, each naming that provider's reviewer, so a round resolves (and so tests) every
    listed provider."""
    return [[f"{n}-{lens}"] for n in names]


def count_keys(obj, needle):
    """How many dict keys, at any depth, contain `needle`."""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            n += (1 if needle in str(k) else 0) + count_keys(v, needle)
    elif isinstance(obj, list):
        for v in obj:
            n += count_keys(v, needle)
    return n


# ------------------------------------------------------------------------------------------------ cases

@case("CP2-T01")
def t01_provider_one_lane_found_failing_is_skipped_by_another_without_testing_again():
    m = Machine(["fakea", "fakeb"])
    m.fail("fakea")
    depth = depth_for("fakea", "fakeb")
    A, B = make_lanes(m, [("lane-a", dict(providers=["fakea", "fakeb"], depth=depth)),
                          ("lane-b", dict(providers=["fakea", "fakeb"], depth=depth))])
    ra, rnd_a = A.open_round()
    check("[CP2-T01] lane A opens its round", ra.returncode == 0, said(ra))
    check("[CP2-T01] lane A tested fakea once (it failed)", m.probes("fakea") == 1, m.log_lines())
    check("[CP2-T01] lane A's round has no fakea reviewer but has fakeb", used(rnd_a, "fakea") == []
          and used(rnd_a, "fakeb") != [], ids(rnd_a))
    rb, rnd_b = B.open_round()
    check("[CP2-T01] lane B opens its round", rb.returncode == 0, said(rb))
    check("[CP2-T01] the call log shows exactly one fakea test request in total (lane A's)",
          m.probes("fakea") == 1, m.log_lines())
    check("[CP2-T01] lane B's round has no fakea reviewer", used(rnd_b, "fakea") == [], ids(rnd_b))
    check("[CP2-T01] lane B's round still has a fakeb reviewer", used(rnd_b, "fakeb") != [], ids(rnd_b))
    out = B.status()
    check("[CP2-T01] reviewer-status in lane B lists fakea as failed recently",
          listed_failed(out, "fakea-standards") and listed_failed(out, "fakea-correctness"), out)
    check("[CP2-T01] reviewer-status in lane B still lists fakeb as available", listed_available(out, "fakeb-both"),
          out)


@case("CP2-T02")
def t02_provider_one_lane_found_working_is_not_tested_again_by_another():
    m = Machine(["fakeb"])
    depth = depth_for("fakeb")
    A, B = make_lanes(m, [("lane-a", dict(providers=["fakeb"], depth=depth)),
                          ("lane-b", dict(providers=["fakeb"], depth=depth))])
    ra, rnd_a = A.open_round()
    check("[CP2-T02] lane A opens its round", ra.returncode == 0, said(ra))
    check("[CP2-T02] lane A tested fakeb once", m.probes("fakeb") == 1, m.log_lines())
    rb, rnd_b = B.open_round()
    check("[CP2-T02] lane B opens its round", rb.returncode == 0, said(rb))
    check("[CP2-T02] the call log shows exactly one fakeb test request across both lanes", m.probes("fakeb") == 1,
          m.log_lines())
    check("[CP2-T02] fakeb is a reviewer in lane A's round", used(rnd_a, "fakeb") != [], ids(rnd_a))
    check("[CP2-T02] fakeb is a reviewer in lane B's round", used(rnd_b, "fakeb") != [], ids(rnd_b))


@case("CP2-T03")
def t03_eight_lanes_results_at_the_same_moment_all_saved_and_record_intact():
    names = ["fakea", "fakeb", "fakec", "faked", "fakee", "fakef", "fakeg", "fakeh"]
    failing, working = names[:4], names[4:]
    m = Machine(names)
    for n in failing:
        m.fail(n)
    depth = depth_for(*names)
    A, B, C = make_lanes(m, [("lane-a", dict(providers=names)), ("lane-b", dict(providers=names)),
                             ("lane-c", dict(providers=names, depth=depth))])
    # four checks from each of two lanes, one per provider, all started before any is waited for
    mix = [(A, "fakea"), (B, "fakeb"), (A, "fakec"), (B, "faked"), (A, "fakee"), (B, "fakef"), (A, "fakeg"),
           (B, "fakeh")]
    procs = [(n, lane.popen("providers", "--check", n)) for lane, n in mix]
    results = [(n, p.wait(timeout=120), p.stderr.read()) for n, p in procs]
    check("[CP2-T03] all eight checks exit 0", all(rc == 0 for _n, rc, _e in results), results)
    check("[CP2-T03] each provider got exactly one test request from its own check", m.probes() == 8
          and all(m.probes(n) == 1 for n in names), m.log_lines())
    data = m.health_json()
    check("[CP2-T03] the shared record parses as JSON", isinstance(data, (dict, list)),
          m.health_text()[:300] or "no provider-health.json in the per-user folder")
    text = m.health_text()
    check("[CP2-T03] the shared record names all eight providers", all(n in text for n in names),
          [n for n in names if n not in text])
    check("[CP2-T03] no lock file is left behind in the per-user folder", sorted(x.name for x in m.home.glob("*.lock"))
          == [], sorted(x.name for x in m.home.glob("*.lock")))
    before = m.probes()
    rc, rnd_c = C.open_round()
    check("[CP2-T03] lane C opens a round listing all eight providers", rc.returncode == 0, said(rc))
    check("[CP2-T03] the call log gains zero new test requests (every result was kept)", m.probes() == before,
          m.log_lines()[before:])
    check("[CP2-T03] none of the four failing providers is in lane C's round",
          all(used(rnd_c, n) == [] for n in failing), ids(rnd_c))
    check("[CP2-T03] all four working providers are in lane C's round", all(used(rnd_c, n) != [] for n in working),
          ids(rnd_c))


@case("CP2-T04")
def t04_resetting_a_reviewer_clears_its_shared_bench():
    m = Machine(["fakea", "fakeb"])
    m.fail("fakea")
    depth = depth_for("fakea", "fakeb")
    A, B = make_lanes(m, [("lane-a", dict(providers=["fakea", "fakeb"], depth=depth)),
                          ("lane-b", dict(providers=["fakea", "fakeb"], depth=depth))])
    A.open_round()
    check("[CP2-T04] set-up: lane A tested fakea once", m.probes("fakea") == 1, m.log_lines())
    out = B.status()
    check("[CP2-T04] set-up: lane B lists fakea as failed recently", listed_failed(out, "fakea-standards"), out)
    m.fail("fakea", on=False)
    r = A.run("reviewer-status", "--reset", "fakea-standards")
    check("[CP2-T04] lane A's reviewer-status --reset fakea-standards succeeds", r.returncode == 0, said(r))
    out = B.status()
    check("[CP2-T04] reviewer-status in lane B no longer lists fakea as failed",
          not listed_failed(out, "fakea-standards") and not listed_failed(out, "fakea-correctness"), out)
    rb, rnd_b = B.open_round()
    check("[CP2-T04] lane B opens a round", rb.returncode == 0, said(rb))
    check("[CP2-T04] lane B's round tests fakea (two test requests in total)", m.probes("fakea") == 2,
          m.log_lines())
    check("[CP2-T04] fakea is a reviewer in lane B's round", used(rnd_b, "fakea") != [], ids(rnd_b))


@case("CP2-T05")
def t05_resetting_one_provider_leaves_another_providers_bench_in_place():
    m = Machine(["fakea", "fakeb", "fakec"])
    m.fail("fakea")
    m.fail("fakec")
    depth = depth_for("fakea", "fakeb", "fakec")
    provs = ["fakea", "fakeb", "fakec"]
    A, B = make_lanes(m, [("lane-a", dict(providers=provs, depth=depth)),
                          ("lane-b", dict(providers=provs, depth=depth))])
    A.open_round()
    check("[CP2-T05] set-up: fakea and fakec were each tested once, and failed",
          m.probes("fakea") == 1 and m.probes("fakec") == 1, m.log_lines())
    out = B.status()
    check("[CP2-T05] set-up: lane B lists both fakea and fakec as failed recently",
          listed_failed(out, "fakea-standards") and listed_failed(out, "fakec-standards"), out)
    r = A.run("reviewer-status", "--reset", "fakea-standards")
    check("[CP2-T05] lane A's reviewer-status --reset fakea-standards succeeds", r.returncode == 0, said(r))
    rb, rnd_b = B.open_round()
    check("[CP2-T05] lane B opens a round", rb.returncode == 0, said(rb))
    check("[CP2-T05] fakea is tried again (a second test request)", m.probes("fakea") == 2, m.log_lines())
    check("[CP2-T05] fakec is still skipped: probed once in total", m.probes("fakec") == 1, m.log_lines())
    check("[CP2-T05] fakec is not in lane B's round", used(rnd_b, "fakec") == [], ids(rnd_b))
    out = B.status()
    check("[CP2-T05] lane B still lists fakec as failed recently", listed_failed(out, "fakec-standards"), out)


@case("CP2-T06")
def t06_old_project_file_does_not_stand_in_for_the_shared_record():
    m = Machine(["faked"])
    (A,) = make_lanes(m, [("lane-a", dict(providers=["faked"], depth=depth_for("faked")))])
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat(
        timespec="microseconds")
    local = A.dir / ".ratchet" / "provider-status.json"
    body = json.dumps({"faked": {"ok": True, "at": old, "detail": "answered the test request"}}, indent=2)
    local.write_text(body)
    check("[CP2-T06] set-up: the shared record has nothing for faked", "faked" not in m.health_text(), m.health_text())
    ra, rnd = A.open_round()
    check("[CP2-T06] the round opens", ra.returncode == 0, said(ra))
    check("[CP2-T06] faked is tested once despite the project's own fresh-looking result", m.probes("faked") == 1,
          m.log_lines())
    check("[CP2-T06] the result landed in the shared record in the per-user folder",
          isinstance(m.health_json(), dict) and "faked" in m.health_text(),
          m.health_text()[:300] or "no provider-health.json in the per-user folder")
    check("[CP2-T06] the project's old file is left exactly as it was", local.read_text() == body, local.read_text())
    check("[CP2-T06] faked is a reviewer in the round", used(rnd, "faked") != [], ids(rnd))


@case("CP2-T07")
def t07_shared_bench_ends_on_its_own_and_provider_is_tried_again():
    bench_s = 8
    ttl = f"unavailable_ttl_hours={bench_s / 3600:.7f}"
    m = Machine(["fakea", "fakeb"])
    m.fail("fakea")
    depth = depth_for("fakea", "fakeb")
    kw = dict(providers=["fakea", "fakeb"], depth=depth, settings=[ttl])
    A, B = make_lanes(m, [("lane-a", kw), ("lane-b", kw)])
    ra, _ = A.open_round()
    t_bench = time.monotonic()
    check("[CP2-T07] set-up: lane A tested fakea once and it failed", ra.returncode == 0 and m.probes("fakea") == 1,
          said(ra) + str(m.log_lines()))
    m.fail("fakea", on=False)                    # fakea has since been fixed
    rb, rnd_b = B.open_round()
    check("[CP2-T07] lane B's first round opens", rb.returncode == 0, said(rb))
    check("[CP2-T07] lane B's first round has no fakea reviewer", used(rnd_b, "fakea") == [], ids(rnd_b))
    check("[CP2-T07] lane B's first round sends no new fakea test request", m.probes("fakea") == 1, m.log_lines())
    time.sleep(max(0.0, bench_s + 1.5 - (time.monotonic() - t_bench)))
    rb2, rnd_b2 = B.open_round()
    check("[CP2-T07] lane B's second round opens", rb2.returncode == 0, said(rb2))
    check("[CP2-T07] the second round tests fakea again (a second test request)", m.probes("fakea") == 2,
          m.log_lines())
    check("[CP2-T07] the second round uses fakea", used(rnd_b2, "fakea") != [], ids(rnd_b2))


@case("CP2-T08")
def t08_damaged_shared_record_is_treated_as_untested_and_replaced():
    m = Machine(["fakeb"])
    depth = depth_for("fakeb")
    A, B = make_lanes(m, [("lane-a", dict(providers=["fakeb"], depth=depth)),
                          ("lane-b", dict(providers=["fakeb"], depth=depth))])
    A.open_round()
    check("[CP2-T08] set-up: lane A's one test request left a shared record file", m.probes("fakeb") == 1
          and m.health_file().exists(), (m.log_lines(), sorted(x.name for x in m.home.iterdir())))
    m.health_file().write_text("{ this is not json at all")
    rb, rnd_b = B.open_round()
    check("[CP2-T08] lane B's round exits 0 with a damaged record", rb.returncode == 0, said(rb))
    check("[CP2-T08] stderr says the shared record could not be read",
          HEALTH_FILE in (rb.stderr or "") and "could not be read" in (rb.stderr or ""), rb.stderr)
    check("[CP2-T08] it is not a crash", "Traceback" not in said(rb), said(rb))
    check("[CP2-T08] fakeb is tested again (a second test request)", m.probes("fakeb") == 2, m.log_lines())
    check("[CP2-T08] the record now parses as JSON with fakeb in it", isinstance(m.health_json(), (dict, list))
          and "fakeb" in m.health_text(), m.health_text()[:300])
    check("[CP2-T08] fakeb is a reviewer in lane B's round", used(rnd_b, "fakeb") != [], ids(rnd_b))


@case("CP2-T09")
def t09_how_long_a_good_result_counts_is_a_time_window():
    m = Machine(["fakeb"])
    depth = depth_for("fakeb")
    kw = dict(providers=["fakeb"], depth=depth)
    A, B, C = make_lanes(m, [("lane-a", kw), ("lane-b", dict(kw, settings=["probe_ttl_minutes=0"])),
                             ("lane-c", kw)])
    A.open_round()
    check("[CP2-T09] set-up: lane A tested fakeb once and it worked", m.probes("fakeb") == 1, m.log_lines())
    rb, rnd_b = B.open_round()
    check("[CP2-T09] lane B (window 0) opens a round", rb.returncode == 0, said(rb))
    # (a window of 0 makes every pass of the engine's own re-test loop due, so "at least one more", not "exactly one")
    check("[CP2-T09] lane B sends at least one more fakeb test request: a fresh result is only trusted inside the "
          "window", m.probes("fakeb") >= 2, m.log_lines())
    check("[CP2-T09] fakeb is a reviewer in lane B's round", used(rnd_b, "fakeb") != [], ids(rnd_b))
    after_b = m.probes("fakeb")
    rc, rnd_c = C.open_round()
    check("[CP2-T09] lane C (default window) opens a round", rc.returncode == 0, said(rc))
    check("[CP2-T09] lane C adds no test request: lane B's fresh result is inside the default window",
          m.probes("fakeb") == after_b, m.log_lines()[after_b:])
    check("[CP2-T09] fakeb is a reviewer in lane C's round", used(rnd_c, "fakeb") != [], ids(rnd_c))


@case("CP2-T10")
def t10_a_reviewer_level_mark_stays_in_its_own_project():
    m = Machine(["fakeb"])
    depth = depth_for("fakeb", lens="standards")
    A, B = make_lanes(m, [("lane-a", dict(providers=["fakeb"], depth=depth)),
                          ("lane-b", dict(providers=["fakeb"], depth=depth))])
    r = A.run("reviewer-status", "--mark", "fakeb-standards", "--reason", "rate limited here")
    check("[CP2-T10] lane A's reviewer-status --mark succeeds", r.returncode == 0, said(r))
    out_a = A.status()
    check("[CP2-T10] lane A lists fakeb-standards as unavailable", listed_failed(out_a, "fakeb-standards"), out_a)
    out_b = B.status()
    check("[CP2-T10] lane B still lists fakeb-standards as available", listed_available(out_b, "fakeb-standards"),
          out_b)
    rb, rnd_b = B.open_round()
    check("[CP2-T10] lane B opens a round", rb.returncode == 0, said(rb))
    check("[CP2-T10] lane B uses fakeb-standards", "fakeb-standards" in ids(rnd_b), ids(rnd_b))
    out_a2 = A.status()
    check("[CP2-T10] lane A still lists fakeb-standards as unavailable afterwards", listed_failed(out_a2,
          "fakeb-standards"), out_a2)


@case("CP2-T11")
def t11_two_lanes_testing_the_same_provider_at_once_leave_one_clean_entry():
    m = Machine(["fakee"])
    kw = dict(providers=["fakee"])
    A, B, C = make_lanes(m, [("lane-a", kw), ("lane-b", kw), ("lane-c", dict(kw, depth=depth_for("fakee")))])
    procs = [A.popen("providers", "--check", "fakee"), B.popen("providers", "--check", "fakee"),
             A.popen("providers", "--check", "fakee"), B.popen("providers", "--check", "fakee")]
    codes = [p.wait(timeout=120) for p in procs]
    for p in procs:
        p.stderr.read()
    check("[CP2-T11] all four checks exit 0", codes == [0, 0, 0, 0], codes)
    data = m.health_json()
    check("[CP2-T11] the shared record parses as JSON", isinstance(data, (dict, list)),
          m.health_text()[:300] or "no provider-health.json in the per-user folder")
    check("[CP2-T11] the shared record has a single fakee entry", count_keys(data, "fakee") == 1,
          m.health_text()[:400])
    check("[CP2-T11] no lock file is left behind in the per-user folder",
          sorted(x.name for x in m.home.glob("*.lock")) == [], sorted(x.name for x in m.home.glob("*.lock")))
    before = m.probes("fakee")
    rc, rnd_c = C.open_round()
    check("[CP2-T11] lane C opens a round", rc.returncode == 0, said(rc))
    check("[CP2-T11] lane C adds no fakee test request", m.probes("fakee") == before, m.log_lines()[before:])
    check("[CP2-T11] fakee is marked working: it is a reviewer in lane C's round", used(rnd_c, "fakee") != [],
          ids(rnd_c))


@case("CP2-T12")
def t12_docs_say_where_the_shared_record_lives_and_what_it_holds():
    docs = {"README.md": ROOT / "README.md",
            "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md"}
    for label, path in docs.items():
        text = path.read_text()
        low = text.lower()
        spots = [m_.start() for m_ in re.finditer(re.escape(HEALTH_FILE), text)]
        check(f"[CP2-T12] {label} names {HEALTH_FILE}", bool(spots), "not mentioned")
        windows = [low[max(0, s - 700):s + 900] for s in spots]
        check(f"[CP2-T12] {label} says it is per user", any("per user" in w or "per-user" in w for w in windows),
              "no 'per user' near the file name")
        check(f"[CP2-T12] {label} says every project and lane share it",
              any(("every project" in w or "all projects" in w) and "lane" in w for w in windows),
              "no 'every project' and 'lane' near the file name")
        check(f"[CP2-T12] {label} says it holds provider test results",
              any("test result" in w for w in windows), "no 'test result' near the file name")
        check(f"[CP2-T12] {label} says it holds benches", any("bench" in w for w in windows),
              "no 'bench' near the file name")
        if label == "schemas.md":
            s2 = [m_.start() for m_ in re.finditer(r"provider-status\.json", low)]
            check("[CP2-T12] schemas.md says the old per-project provider-status.json is ignored",
                  any("ignored" in low[max(0, s - 400):s + 500] for s in s2),
                  "no 'ignored' near provider-status.json")


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
        except Exception as e:  # a crash is this case's failed result, never a silent drop
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


# ------------------------------------------------------------------------------------------------ fix round 1
# Added after the planned cases above (they are not touched): results are dated by when their request was SENT, an
# older result never replaces a newer one, one lane at a time sends the automatic test request (the others wait for
# its answer), a tool run under two models is two entries, and `status --json` shows a provider any lane benched.

sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import provider_health as ph    # noqa: E402
import threading                # noqa: E402

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

# Like FAKE, but what it does is decided when it STARTS (a <name>.fail / <name>.slow marker present at that moment),
# and a slow one answers 5 seconds later: a slow request can be overtaken by a later, fast one.
SLOW_FAKE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
kind=review
case "$*" in *"connectivity check"*) kind=probe;; esac
fail=0; [ -f "$here/$me.fail" ] && fail=1
slow=0; [ -f "$here/$me.slow" ] && slow=1
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s %s\n' "$me" "$kind" >> "$RATCHET_FAKE_LOGS/calls.log"; fi
if [ "$slow" = 1 ]; then sleep 5; fi
if [ "$fail" = 1 ]; then echo "error: not signed in, run login first" >&2; exit 1; fi
if [ "$kind" = probe ]; then echo "RATCHET-PROBE-OK"; else echo '{"findings": []}'; fi
exit 0
"""

# A tool that is told its model on the command line (`--model <m>`): logs "<name>:<model> probe|review" and fails
# when <name>.<last part of the model>.fail sits beside it.
MODEL_FAKE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
model=""; [ "$1" = "--model" ] && model="$2"
short=${model##*/}
kind=review
case "$*" in *"connectivity check"*) kind=probe;; esac
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s:%s %s\n' "$me" "$model" "$kind" >> "$RATCHET_FAKE_LOGS/calls.log"; fi
if [ -f "$here/$me.$short.fail" ]; then echo "error: out of quota" >&2; exit 1; fi
if [ "$kind" = probe ]; then echo "RATCHET-PROBE-OK"; else echo '{"findings": []}'; fi
exit 0
"""


def install_fake(m, name, script):
    """Replace the machine's fake `name` with another script (and give it `sleep`)."""
    (m.bin / name).write_text(script)
    (m.bin / name).chmod(0o755)
    if not (m.bin / "sleep").exists():
        (m.bin / "sleep").symlink_to(shutil.which("sleep"))


def wait_for(cond, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


def write_health(m, obj):
    """Write the machine's shared record as another lane would have left it (atomically)."""
    tmp = m.home / "provider-health.json.test-tmp"
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, m.health_file())


def iso(t):
    return t.isoformat(timespec="microseconds")


@case("CP2-X01")
def x01_an_older_result_never_replaces_a_newer_one():
    def sec(n):
        return T0 + datetime.timedelta(seconds=n)
    bench_to = sec(6 * 3600)
    # a slow failure (sent at 0 s, back at 100 s) lands after a fast pass (sent at 10 s, back at 11 s)
    d = {}
    ph.record_probe(d, "k", True, "answered", sec(10), now=sec(11))
    ph.record_probe(d, "k", False, "no answer within 120s", sec(0), "slow failure", bench_to, now=sec(100))
    check("[CP2-X01] a slow failure landing after a newer pass does not bench the provider",
          ph.live_bench(d, "k", sec(200)) is None and d["k"]["ok"] is True, d)
    check("[CP2-X01] ... and the entry is still dated by the newer request's send time",
          d["k"]["at"] == iso(sec(10)), d)
    # a slow pass (sent at 0 s) lands after a newer failure (sent at 10 s) that started a bench
    d = {}
    ph.record_probe(d, "k", False, "signed out", sec(10), "test request failed: signed out", bench_to, now=sec(11))
    ph.record_probe(d, "k", True, "answered", sec(0), now=sec(100))
    check("[CP2-X01] a slow pass landing after a newer failure does not end its bench",
          ph.live_bench(d, "k", sec(200)) is not None and d["k"]["ok"] is False, d)
    # in order, the newer pass still ends an older failure's bench
    d = {}
    ph.record_probe(d, "k", False, "signed out", sec(0), "x", bench_to, now=sec(1))
    ph.record_probe(d, "k", True, "answered", sec(10), now=sec(11))
    check("[CP2-X01] a newer pass still ends an older failure's bench",
          ph.live_bench(d, "k", sec(200)) is None and d["k"]["ok"] is True, d)
    # the bench is timed from when the failure was recorded, not from when its request was sent
    d = {}
    ph.record_probe(d, "k", False, "late", sec(0), "x", sec(3600), now=sec(120))
    check("[CP2-X01] a bench starts when the failure is recorded", d["k"]["bench"]["since"] == iso(sec(120)), d)


@case("CP2-X02")
def x02_only_one_lane_holds_the_claim_on_a_test_request():
    def sec(n):
        return T0 + datetime.timedelta(seconds=n)
    d = {}
    first = ph.claim(d, "k", "lane-a", sec(0), 120, 120)
    second = ph.claim(d, "k", "lane-b", sec(0), 120, 120)
    check("[CP2-X02] the first lane gets the claim and the second is told to wait", first[0] == "claimed"
          and second[0] == "wait" and second[1]["by"] == "lane-a", (first, second))
    check("[CP2-X02] the claim lasts the probe timeout", d["k"]["claim"]["until"] == iso(sec(120)), d)
    ph.record_probe(d, "k", True, "answered", sec(0), now=sec(2), by="lane-b")
    check("[CP2-X02] a result recorded by another lane does not end the claim", "claim" in d["k"], d)
    ph.record_probe(d, "k", True, "answered", sec(1), now=sec(3), by="lane-a")
    check("[CP2-X02] the claim ends with its own lane's result", "claim" not in d["k"], d)
    check("[CP2-X02] after a pass nobody claims again inside the window",
          ph.claim(d, "k", "lane-b", sec(60), 120, 120)[0] == "fresh", d)
    d = {"k": {"ok": False, "at": iso(sec(0)), "detail": "x",
               "bench": {"reason": "x", "since": iso(sec(0)), "until": iso(sec(3600))}}}
    check("[CP2-X02] while benched nobody claims", ph.claim(d, "k", "lane-a", sec(60), 120, 120)[0] == "benched", d)
    d = {}
    ph.claim(d, "k", "lane-a", sec(0), 120, 120)
    got = ph.claim(d, "k", "lane-b", sec(121), 120, 120)
    check("[CP2-X02] a claim whose lane never answered ends on its own, and another lane takes it",
          got[0] == "claimed" and d["k"]["claim"]["by"] == "lane-b", (got, d))
    ph.release(d, "k", "lane-a")
    check("[CP2-X02] only the holder can release a claim", d["k"]["claim"]["by"] == "lane-b", d)
    ph.release(d, "k", "lane-b")
    check("[CP2-X02] a released claim leaves no empty entry behind", d == {}, d)


@case("CP2-X03")
def x03_a_slow_failure_landing_after_a_newer_pass_does_not_bench_the_provider():
    m = Machine(["fakea"])
    install_fake(m, "fakea", SLOW_FAKE)
    A, B, C = make_lanes(m, [("lane-a", dict(providers=["fakea"])), ("lane-b", dict(providers=["fakea"])),
                             ("lane-c", dict(providers=["fakea"], depth=depth_for("fakea")))])
    m.fail("fakea")
    (m.bin / "fakea.slow").write_text("x")
    slow = A.popen("providers", "--check", "fakea")          # sent first; answers (fails) 5 s later
    started = wait_for(lambda: m.probes("fakea") == 1)
    m.fail("fakea", on=False)
    (m.bin / "fakea.slow").unlink()
    rb = B.run("providers", "--check", "fakea")              # sent later; passes at once
    slow.wait(timeout=120)
    slow.stderr.read()
    check("[CP2-X03] set-up: the slow request was sent first and the fast one passed", started and m.probes("fakea")
          == 2 and rb.returncode == 0, (m.log_lines(), said(rb)))
    data = m.health_json() or {}
    check("[CP2-X03] the newer pass stands: the slow failure that landed last did not overwrite it",
          (data.get("fakea") or {}).get("ok") is True and "bench" not in (data.get("fakea") or {}), m.health_text())
    before = m.probes()
    rc, rnd_c = C.open_round()
    check("[CP2-X03] a third lane uses fakea without testing it again", rc.returncode == 0
          and used(rnd_c, "fakea") != [] and m.probes() == before, (said(rc), m.log_lines()[before:]))


@case("CP2-X04")
def x04_a_slow_pass_landing_after_a_newer_failure_does_not_end_its_bench():
    m = Machine(["fakea", "fakeb"])
    install_fake(m, "fakea", SLOW_FAKE)
    A, B, C = make_lanes(m, [("lane-a", dict(providers=["fakea"])), ("lane-b", dict(providers=["fakea"])),
                             ("lane-c", dict(providers=["fakea", "fakeb"], depth=depth_for("fakea", "fakeb")))])
    (m.bin / "fakea.slow").write_text("x")
    slow = A.popen("providers", "--check", "fakea")          # sent first; would pass, 5 s later
    started = wait_for(lambda: m.probes("fakea") == 1)
    (m.bin / "fakea.slow").unlink()
    m.fail("fakea")
    rb = B.run("providers", "--check", "fakea")              # sent later; fails at once and benches fakea
    slow.wait(timeout=120)
    slow.stderr.read()
    check("[CP2-X04] set-up: the slow request was sent first and the fast one failed", started and m.probes("fakea")
          == 2 and rb.returncode == 0, (m.log_lines(), said(rb)))
    data = m.health_json() or {}
    check("[CP2-X04] the newer failure's bench stands: the slow pass that landed last did not end it",
          (data.get("fakea") or {}).get("ok") is False and "bench" in (data.get("fakea") or {}), m.health_text())
    rc, rnd_c = C.open_round()
    check("[CP2-X04] a third lane skips fakea and uses fakeb", rc.returncode == 0 and used(rnd_c, "fakea") == []
          and used(rnd_c, "fakeb") != [], (said(rc), ids(rnd_c)))


@case("CP2-X05")
def x05_a_live_claim_makes_a_second_lane_wait_instead_of_testing():
    m = Machine(["fakeb"])
    (A,) = make_lanes(m, [("lane-a", dict(providers=["fakeb"], depth=depth_for("fakeb")))])
    now_ = datetime.datetime.now(UTC)
    write_health(m, {"fakeb": {"claim": {"by": "another-lane", "since": iso(now_),
                                         "until": iso(now_ + datetime.timedelta(seconds=90))}}})

    def other_lane_answers():          # the lane holding the claim records its pass and ends the claim
        write_health(m, {"fakeb": {"ok": True, "at": iso(datetime.datetime.now(UTC)),
                                   "detail": "answered the test request"}})
    timer = threading.Timer(2.0, other_lane_answers)
    t_start = time.monotonic()
    timer.start()
    ra, rnd = A.open_round()
    waited = time.monotonic() - t_start
    timer.join()
    check("[CP2-X05] the round opens", ra.returncode == 0, said(ra))
    check("[CP2-X05] this lane sent no test request of its own", m.probes("fakeb") == 0, m.log_lines())
    check("[CP2-X05] it waited for the other lane's answer", waited >= 1.5, waited)
    check("[CP2-X05] and used the provider on that answer", used(rnd, "fakeb") != [], ids(rnd))


@case("CP2-X06")
def x06_a_claim_nobody_answers_ends_on_its_own_and_the_lane_tests_itself():
    m = Machine(["fakeb"])
    (A,) = make_lanes(m, [("lane-a", dict(providers=["fakeb"], depth=depth_for("fakeb")))])
    now_ = datetime.datetime.now(UTC)
    write_health(m, {"fakeb": {"claim": {"by": "a-lane-that-died", "since": iso(now_),
                                         "until": iso(now_ + datetime.timedelta(seconds=3))}}})
    t_start = time.monotonic()
    ra, rnd = A.open_round()
    waited = time.monotonic() - t_start
    check("[CP2-X06] the round opens", ra.returncode == 0, said(ra))
    check("[CP2-X06] it waited for the claim to run out", waited >= 2.0, waited)
    check("[CP2-X06] then tested the provider itself, once", m.probes("fakeb") == 1, m.log_lines())
    check("[CP2-X06] and used it", used(rnd, "fakeb") != [], ids(rnd))
    check("[CP2-X06] no claim is left in the shared record", "claim" not in m.health_text(), m.health_text())


@case("CP2-X07")
def x07_lanes_opening_rounds_at_the_same_moment_send_one_test_request():
    m = Machine(["fakeb"])
    install_fake(m, "fakeb", SLOW_FAKE)
    kw = dict(providers=["fakeb"], depth=depth_for("fakeb"))
    lanes = make_lanes(m, [("lane-a", kw), ("lane-b", kw), ("lane-c", kw)])
    (m.bin / "fakeb.slow").write_text("x")                   # the request takes 5 s: the others reach it meanwhile
    procs = [ln.popen("reviewers", "CP1", "--json") for ln in lanes]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        outs.append((p.returncode, out, err))
    check("[CP2-X07] all three rounds open", all(rc == 0 for rc, _o, _e in outs), [(rc, e[-200:]) for rc, _o, e in outs])
    check("[CP2-X07] the call log shows exactly one test request in total", m.probes("fakeb") == 1, m.log_lines())
    rounds = []
    for _rc, out, _e in outs:
        try:
            rounds.append(json.loads(out))
        except ValueError:
            rounds.append({})
    check("[CP2-X07] every lane's round uses fakeb", all(used(r, "fakeb") != [] for r in rounds),
          [ids(r) for r in rounds])


@case("CP2-X10")
def x10_lanes_opening_rounds_at_the_same_moment_while_the_provider_fails_send_one_test_request():
    m = Machine(["fakeb", "fakea"])
    install_fake(m, "fakeb", SLOW_FAKE)
    kw = dict(providers=["fakeb"], depth=depth_for("fakeb"))
    lanes = make_lanes(m, [("lane-a", kw), ("lane-b", kw), ("lane-c", kw)])
    m.fail("fakeb")
    (m.bin / "fakeb.slow").write_text("x")                   # the failing request takes 5 s: the others reach it meanwhile
    procs = [ln.popen("reviewers", "CP1", "--json") for ln in lanes]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        outs.append((p.returncode, out, err))
    check("[CP2-X10] all three rounds open", all(rc == 0 for rc, _o, _e in outs),
          [(rc, e[-200:]) for rc, _o, e in outs])
    check("[CP2-X10] the call log shows exactly one test request in total (the waiting lanes tested nothing)",
          m.probes("fakeb") == 1, m.log_lines())
    rounds = []
    for _rc, out, _e in outs:
        try:
            rounds.append(json.loads(out))
        except ValueError:
            rounds.append({})
    check("[CP2-X10] no lane's round uses fakeb", all(used(r, "fakeb") == [] and ids(r) for r in rounds),
          [ids(r) for r in rounds])
    data = m.health_json() or {}
    check("[CP2-X10] the shared record holds one bench for fakeb and no claim", "bench" in (data.get("fakeb") or {})
          and "claim" not in m.health_text(), m.health_text())


@case("CP2-X11")
def x11_the_health_record_is_only_written_under_its_lock_and_a_lockless_writer_loses_an_entry():
    import test_trust_cp5_e as tt               # the shared-record scan and the forced-interleave race live there
    from trust_helpers import cp5y_load_engine, engine_env, make_env
    src = (ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py").read_text(encoding="utf-8")
    probe = ("def stray(path):\n    write_json(_health_path(), {})\n"
             "def stray2():\n    q = _health_path()\n    _write_record(q, {})\n"
             "def fine():\n    update_record(_health_path(), lambda d: d)\n")
    flagged = sorted({fn for fn, _l, _w in tt.cp5r13_shared_write_violations(probe)})
    check("[CP2-X11] a hand-written write of provider-health.json's path, directly or through a local name, is "
          "flagged by the shared-record scan, and one through update_record is not", flagged == ["stray", "stray2"],
          flagged)
    check("[CP2-X11] the scan's lists name the record (its path comes from provider_health.FILE, which a file-name "
          "scan cannot see)", ph.FILE in tt._CP5R13_SHARED_NAMES and "_health_path" in tt._CP5R13_SHARED_PATHS,
          (ph.FILE, sorted(tt._CP5R13_SHARED_PATHS)))
    check("[CP2-X11] ratchet.py writes provider-health.json only inside update_record",
          tt.cp5r13_shared_write_violations(src) == [], tt.cp5r13_shared_write_violations(src)[:5])
    env = make_env("claude")
    with engine_env(env):
        mod = cp5y_load_engine()

        def race():
            for f in Path(env["RATCHET_HOME"]).glob("provider-health.*"):
                f.unlink()
            errs = tt.cp5r13_interleave(mod, mod._health_path(),
                                        [lambda: mod._update_health(lambda d: d.__setitem__("x", {"ok": True})),
                                         lambda: mod._update_health(lambda d: d.__setitem__("y", {"ok": True}))])
            return errs, sorted(tt.cp5r13_json(mod._health_path()) or {})
        locked = race()
        real = mod.update_record

        def lockless(path, fn, lock=None, damaged=None, pretty=False, need_lock=True, wait=5.0):
            data, why = mod._read_record(path)
            mod._write_record(path, fn(data if why is None else {}), pretty=pretty)
            return "written"
        mod.update_record = lockless
        try:
            unlocked = race()
        finally:
            mod.update_record = real
    check("[CP2-X11] two lanes recording at once with both reading first: both entries survive under the lock",
          locked == ([], ["x", "y"]), locked)
    check("[CP2-X11] the same race with a lock-less writer loses an entry (so the check above has teeth)",
          len(unlocked[1]) == 1, unlocked)


class ModelLane(Lane):
    """A lane whose providers are one hand-written tool run under named models: [(id, program, model, company)]."""

    def __init__(self, m, name, specs):
        self.m = m
        self.rounds = 0
        self.dir = g.project(name, extra_cp={"risk": "normal"})
        for pid, prog, model, company in specs:
            r = self.run("providers", "--add", pid, "--command", json.dumps([prog, "--model", "{model}", "{prompt}"]),
                         "--model", model, "--company", company, "--no-check")
            assert r.returncode == 0, r.stdout + r.stderr
        depth = [[f"{pid}-both"] for pid, _p, _m, _c in specs]
        r = self.run("config", "--set", "review_depth=" + json.dumps({"normal": depth}))
        assert r.returncode == 0, r.stdout + r.stderr
        g.approve_plan(self.dir)
        g.to_adversarial(self.dir)


@case("CP2-X08")
def x08_one_tool_under_two_models_is_two_entries_shared_across_lanes_whatever_the_ids():
    m = Machine(["fakeg"])
    install_fake(m, "fakeg", MODEL_FAKE)
    glm, kimi = "go/glm-5.3", "go/kimi-k2"
    (m.bin / "fakeg.glm-5.3.fail").write_text("x")           # only the GLM model is out of quota
    A = ModelLane(m, "lane-a", [("a-glm", "fakeg", glm, "zhipu"), ("a-kimi", "fakeg", kimi, "moonshot")])
    ra, rnd_a = A.open_round()
    check("[CP2-X08] lane A opens its round", ra.returncode == 0, said(ra))
    check("[CP2-X08] each model was tested once", m.probes(f"fakeg:{glm}") == 1 and m.probes(f"fakeg:{kimi}") == 1,
          m.log_lines())
    check("[CP2-X08] GLM failing did not bench Kimi: lane A's round has the Kimi reviewer and not the GLM one",
          used(rnd_a, "a-kimi") != [] and used(rnd_a, "a-glm") == [], ids(rnd_a))
    data = m.health_json() or {}
    check("[CP2-X08] the shared record has one entry per model, benched only for GLM",
          sorted(data) == sorted([f"fakeg:{glm}", f"fakeg:{kimi}"]) and "bench" in data[f"fakeg:{glm}"]
          and "bench" not in data[f"fakeg:{kimi}"], m.health_text())
    B = ModelLane(m, "lane-b", [("b-glm", "fakeg", glm, "zhipu"), ("b-kimi", "fakeg", kimi, "moonshot")])
    rb, rnd_b = B.open_round()
    check("[CP2-X08] lane B (same tool and models under other ids) opens its round", rb.returncode == 0, said(rb))
    check("[CP2-X08] lane B sent no new test request for either model", m.probes(f"fakeg:{glm}") == 1
          and m.probes(f"fakeg:{kimi}") == 1, m.log_lines())
    check("[CP2-X08] lane B skips GLM and uses Kimi", used(rnd_b, "b-glm") == [] and used(rnd_b, "b-kimi") != [],
          ids(rnd_b))


@case("CP2-X09")
def x09_status_json_lists_a_provider_any_lane_benched():
    m = Machine(["fakea", "fakeb"])
    m.fail("fakea")
    depth = depth_for("fakea", "fakeb")
    A, B = make_lanes(m, [("lane-a", dict(providers=["fakea", "fakeb"], depth=depth)),
                          ("lane-b", dict(providers=["fakea", "fakeb"], depth=depth))])
    A.open_round()
    r = B.run("status", "--json")
    try:
        down = json.loads(r.stdout).get("unavailable_reviewers") or {}
    except ValueError:
        down = {}
    check("[CP2-X09] lane B's status --json lists the reviewers of the provider lane A benched",
          all(k in down for k in ("fakea-correctness", "fakea-standards", "fakea-both")), (r.stdout[:300], r.stderr))
    one = down.get("fakea-both") or {}
    check("[CP2-X09] ... each with a reason, a start and an end", bool(one.get("reason")) and bool(one.get("since"))
          and bool(one.get("until")), one)
    check("[CP2-X09] ... and does not list the working provider", not any(k.startswith("fakeb-") for k in down), down)


if __name__ == "__main__":
    main()
