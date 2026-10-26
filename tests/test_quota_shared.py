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
import test_review_diff_large_files as base  # noqa: E402  (CP4-T10/T11: ER, _mk_repo, _snapshot)

# [CP4 fix round 1: omp-glm-correctness/A1, omp-glm-standards/X1, codex-standards/X1] A monotonic mark taken as
# close to process start as this file can manage (right after its own imports), so CP4-T02's real top-level check
# (now computed at the very end of main(), after every CP4-Txx case including CP4-T09 has run -- see main() below)
# can bound the REAL top-level wall-clock of `python3 -I tests/test_quota_shared.py` (what a user, tests/run_all.py
# and the checkpoint's own gate command actually run and wait on), with no second nested full-suite spawn. Same
# reasoning and placement as test_trust_cp5_d.py's CP3 block's _CP3_PROCESS_START / test_trust_cp5_e.py's CP2
# block's _CP2_PROCESS_START.
_CP4_PROCESS_START = time.monotonic()

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
        # fast-path replaces: python3 ratchet.py init feature / config --set / plan-submit (project()'s own setup)
        self.dir = g.project(name, extra_cp={"risk": "normal"}, fast=True)
        # providers and rules are set up BEFORE the plan is approved: a change to the review rules after approval
        # would stop every reviewers call until approve-changes
        for n in providers:
            # fast-path replaces: python3 ratchet.py providers --add <n> --command ... --company ... --no-check
            r = self.run("providers", "--add", n, "--command", json.dumps([n, "{prompt}"]), "--company",
                         COMPANY[n], "--no-check", fast=True)
            assert r.returncode == 0, r.stdout + r.stderr
        sets = list(settings)
        if depth is not None:
            sets.append("review_depth=" + json.dumps({"normal": depth}))
        if sets:
            r = self.run("config", "--set", *sets, fast=True)  # fast-path replaces: python3 ratchet.py config --set ...
            assert r.returncode == 0, r.stdout + r.stderr
        g.approve_plan(self.dir, fast=True)     # fast-path replaces: python3 ratchet.py ask approve-plan / approve-plan
        g.to_adversarial(self.dir, fast=True)   # fast-path replaces: python3 ratchet.py tests-written/red/implemented/behavior

    def run(self, *args, fast=False):
        return g.rx(self.dir, *args, env=self.m.env, fast=fast)

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
    # kept as real Lane popen() subprocesses, not the fast path: this is the thing the case proves -- eight
    # lanes genuinely race as separate OS processes for provider-health.json's real file lock contention
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
    # kept as real Lane popen() subprocesses, not the fast path: two lanes genuinely race as separate OS
    # processes for provider-health.json's real file lock contention -- the thing this case proves
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
    # [CP4 fix round 1: omp-glm-correctness/A1, omp-glm-standards/X1, codex-standards/X1] The REAL top-level
    # wall-clock check for {CP4-T02}, moved here (from inside cp4t02_suite_finishes_well_under_120s_via_cp1_harness,
    # see that function's own docstring) so it runs AFTER every other TESTS case including CP4-T09 has finished --
    # the true end of `python3 -I tests/test_quota_shared.py`, the same end-to-end quantity CP1's baseline harness
    # measured, with no second nested full-suite spawn. check() merges every call tagged with one id (an id only
    # passes if every call tagged with it does), so this still counts under {CP4-T02} even though it runs outside
    # that function's own scheduled turn in the loop above.
    # [CP4 fix round N: codex-correctness/X2] no longer measured against anything live -- see
    # CP4_T02_FIXED_BUDGET_SEC's own comment below for why.
    _cp4_real_elapsed = time.monotonic() - _CP4_PROCESS_START
    budget = CP4_T02_FIXED_BUDGET_SEC
    _CUR_CASE = "CP4-T02"
    check("{CP4-T02} the REAL top-level solo invocation's own wall-clock (measured end-to-end in this very "
          "process, from _CP4_PROCESS_START to right after every other TESTS case has run) is under a fixed "
          f"ceiling of {budget:.1f}s (CP4_T02_FIXED_BUDGET_SEC -- well below CP1's own ~260-300s original "
          f"baseline, so a real regression still goes red) = {budget:.1f}s, measured the same way CP1's "
          "baseline harness measured it -- down from the ~286-300s CP1 recorded before this checkpoint",
          _cp4_real_elapsed < budget, (_cp4_real_elapsed, budget))
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
    # kept as real Lane popen() subprocesses, not the fast path: three lanes genuinely race as separate OS
    # processes for provider-health.json's real file lock contention -- the thing this case proves
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
    # kept as real Lane popen() subprocesses, not the fast path: three lanes genuinely race as separate OS
    # processes for provider-health.json's real file lock contention -- the thing this case proves
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
        # fast-path replaces: python3 ratchet.py init feature / config --set / plan-submit (project()'s own setup)
        self.dir = g.project(name, extra_cp={"risk": "normal"}, fast=True)
        for pid, prog, model, company in specs:
            # fast-path replaces: python3 ratchet.py providers --add <pid> --command ... --model ... --company ... --no-check
            r = self.run("providers", "--add", pid, "--command", json.dumps([prog, "--model", "{model}", "{prompt}"]),
                         "--model", model, "--company", company, "--no-check", fast=True)
            assert r.returncode == 0, r.stdout + r.stderr
        depth = [[f"{pid}-both"] for pid, _p, _m, _c in specs]
        # fast-path replaces: python3 ratchet.py config --set review_depth=...
        r = self.run("config", "--set", "review_depth=" + json.dumps({"normal": depth}), fast=True)
        assert r.returncode == 0, r.stdout + r.stderr
        g.approve_plan(self.dir, fast=True)     # fast-path replaces: python3 ratchet.py ask approve-plan / approve-plan
        g.to_adversarial(self.dir, fast=True)   # fast-path replaces: python3 ratchet.py tests-written/red/implemented/behavior


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


# ------------------------------------------------------------------------------------------------ CP4
# Converts this file's and test_gates.py's pure-setup subprocess calls to an in-process engine call, so this
# whole suite finishes in well under 120s instead of its current ~286-300s. The 23 cases above are not
# touched: these CP4-Txx cases prove the conversion kept every one of them passing, is fast, names every
# call site it converted (and every real subprocess it deliberately kept), and left ratchet.py itself and
# every other importer of test_gates.py's helpers unchanged. New check() messages below use the CURLY
# {CP4-Txx} tag convention (never square [CP4-Txx]), so tests/test_split_check.py's and
# tests/test_integrate_safety.py's square-bracket CP-group scans never see them.
import ast                      # noqa: E402  (CP4-T03/T07: finds real fast=True call sites and add_parser() calls)
import measure_suite_baseline as _msb  # noqa: E402  (CP4-T09: REAL_SUITES, the schema it writes)

CP4_ENGINE_REL = "skills/ratchet-orchestrator/scripts/ratchet.py"
CP4_MEASURE_REL = "tests/measure_suite_baseline.py"
# [CP4 fix round 2 per director check-in 'simplify': host-alt-standards/B1+B2, host-alt-correctness/C1+C2 --
# forever-freezing ratchet.py/measure_suite_baseline.py against a committed snapshot (round 1's answer to
# codex-correctness/X2 et al) broke the first time either file had a normal edit, which this plan's own
# out-of-scope line (Lane M's concurrent ratchet.py refactor) names as expected, imminent work. CP4-T07/T09 no
# longer hold either file to a historical byte-for-byte freeze of any kind (no `git show`, no committed snapshot
# compared for equality). CP1's evidence fixture below is kept only as a schema example for a live contract
# check (CP4-T09), never hashed or compared for byte equality.]
CP4_FIXTURES_DIR = HERE / "fixtures" / "cp4_base"
CP4_EVIDENCE_PATH = CP4_FIXTURES_DIR / "baseline_timings_at_cp4_write_time.json"

# [CP4 fix round 2: host-alt-correctness/C3] CP4-T02's real top-level budget used to scale with THIS box's own
# load average, and [CP4 fix round N: codex-correctness/X2] a later round added a live-measured "speed factor"
# (timing a tiny reference `ratchet.py --help` workload) on top of that. Both were removed: the speed factor
# measured the startup cost of the very engine the budget is supposed to police, so an engine regression that
# slowed startup also raised the budget meant to catch it, hiding the regression instead of failing on it.
# Replaced with a single fixed ceiling tied directly to this checkpoint's own objective ("well under 120s"):
# measured worst case so far is ~93.7s under heavy load and ~82.1s solo, so 115.0s stays comfortably above both
# while staying under the 120s objective limit -- never self-calibrated, never scaled by anything live.
CP4_T02_FIXED_BUDGET_SEC = 115.0


_REPLACES_COMMENT_RE = re.compile(r"#.*fast-path replaces:", re.IGNORECASE)
_CASE_DECL_RE = re.compile(r'@case\("([^"]+)"\)')
_POPEN_REASON_MARKERS = ("separate os process", "real os process", "genuinely race", "file lock contention",
                         "file lock", "race as separate", "contend")
_INPROCESS_RACE_MARKERS = ("already in-process", "already-in-process", "forced-interleave", "already a forced")


def cp4_committed_base(path):
    """The committed fixture file's content at `path` (a plain Path.read_text(), never `git show` of a historical
    commit and never a read of uncommitted .ratchet/ run state -- see the CP4_*_PATH constants above)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"reading committed fixture {path} failed: {e}")


def cp4_fast_path_call_sites(path):
    """[(1-based lineno, has_replaces_comment)] for every real Call node in `path` that passes a `fast=True`
    keyword argument to a helper. Uses `ast`, not a text search, so a docstring or comment that merely
    mentions the words "fast=True" (as this very file's own docstrings do, describing the convention) is
    never mistaken for an actual call site."""
    src = Path(path).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src, filename=str(path))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "fast" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                lineno = node.lineno
                nearby = "\n".join(lines[max(0, lineno - 3):lineno])
                sites.append((lineno, bool(_REPLACES_COMMENT_RE.search(nearby))))
    return sites


def cp4_case_owning_line(lines, idx):
    """Which @case("...") id the 0-based `idx` line falls under, scanning upward from it."""
    for i in range(idx, -1, -1):
        m = _CASE_DECL_RE.match(lines[i].strip())
        if m:
            return m.group(1)
    return None


@case("CP4-T01")
def cp4t01_all_23_original_cases_still_pass():
    """{CP4-T01} By the time this case runs, every one of the 23 original CP2-Txx/CP2-Xxx cases has already
    run earlier in this same process (TESTS keeps file order, and this case is registered last) -- so this
    reads their already-recorded CASE_STATUS/CASE_MESSAGES rather than re-running or re-measuring anything,
    exactly what the per-test report write_report() is about to produce for the whole file."""
    original_ids = [cid for cid, _fn in TESTS if cid.startswith("CP2-")]
    check("{CP4-T01} exactly the 23 original cases are registered, none missing and none duplicated",
          len(original_ids) == 23 and len(set(original_ids)) == 23, sorted(original_ids))
    missing = [cid for cid in original_ids if cid not in CASE_STATUS]
    check("{CP4-T01} every original case actually ran before this one (none skipped, none reordered after "
          "this case)", missing == [], missing)
    failed = [cid for cid in original_ids if not CASE_STATUS.get(cid, False)]
    check("{CP4-T01} all 23 original cases passed -- none failed, none silently dropped, none weakened",
          failed == [], {cid: CASE_MESSAGES.get(cid) for cid in failed})


@case("CP4-T02")
def cp4t02_suite_finishes_well_under_120s_via_cp1_harness():
    """{CP4-T02} [Fix round 1: omp-glm-correctness/A1, omp-glm-standards/X1, codex-standards/X1 -- self-timing
    doubled runtime] This case used to shell out to CP1's own tests/measure_suite_baseline.py to time itself,
    which re-ran this ENTIRE file (all cases, including this very one, guarded one level deep by a nested-run
    sentinel) as a child process just to produce a number: every REAL invocation of
    `python3 -I tests/test_quota_shared.py` (the checkpoint's own gate command, tests/run_all.py, any
    contributor running the file directly) therefore paid for TWO full passes, while the old check here only
    bounded the nested half -- half the real cost.

    The REAL top-level, end-to-end wall-clock assertion -- still filed under this same {CP4-T02} id (check()
    merges every call tagged with one id: an id only passes if every call tagged with it does) -- now runs at
    the TRUE end of main(), after every other TESTS case including CP4-T09 has finished, right before the final
    report is written: see main() below. Nothing needs to run at this case's own scheduled position in TESTS;
    this is the same correction CP3 made to test_trust_cp5_d.py's own self-timing case (see that file's
    t02_wall_clock_well_under_120s() docstring)."""


@case("CP4-T03")
def cp4t03_every_fast_path_call_site_names_what_it_replaces():
    """{CP4-T03} Every call site that opts into the in-process fast path (fast=True) must carry a comment,
    on its own line or one of the two lines above it, naming the real subprocess command it replaces -- a
    silent fast=True call site is not allowed. Also demands at least one such site exist: before the
    conversion lands there are none, so this fails for the right reason (feature missing), not vacuously."""
    sites, offenders = [], []
    for rel in ("tests/test_gates.py", "tests/test_quota_shared.py"):
        for lineno, has_comment in cp4_fast_path_call_sites(ROOT / rel):
            sites.append(f"{rel}:{lineno}")
            if not has_comment:
                offenders.append(f"{rel}:{lineno}")
    check("{CP4-T03} at least one fast-path (fast=True) call site exists in test_gates.py or "
          "test_quota_shared.py", len(sites) > 0, "no fast=True call site found yet")
    check("{CP4-T03} every fast-path call site names the real subprocess command it replaces in a nearby "
          "comment ('# fast-path replaces: ...')", offenders == [], offenders)


@case("CP4-T04")
def cp4t04_every_real_subprocess_kept_names_its_reason():
    """{CP4-T04} Lane.popen() call sites inside CP2-T03, CP2-T11, CP2-X07 and CP2-X10 -- which make two or
    three lanes genuinely race as separate OS processes for provider-health.json's real file lock -- and
    CP2-X11's already in-process forced-interleave race, must each carry a comment naming that specific
    reason. This is the exact carve-out the objective names: a converted or silently-reasoned site fails
    this, naming file:line."""
    path = ROOT / "tests" / "test_quota_shared.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    expected = {"CP2-T03", "CP2-T11", "CP2-X07", "CP2-X10"}
    found, offenders = set(), []
    for i, line in enumerate(lines):
        if ".popen(" not in line or "def popen" in line:
            continue
        cid = cp4_case_owning_line(lines, i)
        if cid not in expected:
            continue
        found.add(cid)
        nearby = "\n".join(lines[max(0, i - 2):i + 1]).lower()
        if not any(marker in nearby for marker in _POPEN_REASON_MARKERS):
            offenders.append(f"test_quota_shared.py:{i + 1} ({cid})")
    check("{CP4-T04} all four real-subprocess cases (CP2-T03, CP2-T11, CP2-X07, CP2-X10) still call "
          "Lane.popen() at least once (the boundary itself is not converted away)",
          expected <= found, sorted(expected - found))
    check("{CP4-T04} every one of those popen() call sites names the real-OS-process file-lock-contention "
          "reason in a nearby comment", offenders == [], offenders)
    start = next((i for i, l in enumerate(lines) if '@case("CP2-X11")' in l), None)
    end = len(lines)
    if start is not None:
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("@case(") or lines[j].startswith("class "):
                end = j
                break
    section = "\n".join(lines[start:end]).lower() if start is not None else ""
    check("{CP4-T04} CP2-X11's already in-process forced-interleave race names that specific reason in a "
          "comment (not silently converted, not silently re-reasoned)",
          start is not None and any(m in section for m in _INPROCESS_RACE_MARKERS),
          "CP2-X11 section not found" if start is None else "no matching reason marker found in it")


@case("CP4-T05")
def cp4t05_test_gates_default_helpers_still_work_unchanged():
    """{CP4-T05} Exercises test_gates.py's own project()/approve_plan()/to_adversarial()/rx() -- the exact
    functions the opt-in fast path is added to -- directly, with NO fast argument anywhere, proving their
    default (unchanged) behavior still drives a real project through real ratchet.py subprocesses end to
    end. Deliberately does NOT spawn test_gates.py's own ~300-call, ~228s self-test file as a subprocess:
    embedding that cost inside test_quota_shared.py would make CP4-T02's own well-under-120s target for THIS
    file permanently unmeetable, no matter how fast the 23 original cases become -- which would defeat the
    point of both cases. A fresh, independent run of test_gates.py's own suite (itself unaffected by this
    checkpoint, since its default path is untouched) is already covered by tests/run_all.py running
    test_gates.py as its own suite, not re-embedded here."""
    d = g.project("cp4t05")
    r = g.approve_plan(d)
    check("{CP4-T05} g.approve_plan() with no fast argument still succeeds via a real subprocess",
          r.returncode == 0, r.stderr)
    g.to_adversarial(d)  # raises (a crash, counted as this case failing) if any of its own asserts fail
    r = g.rx(d, "status")
    check("{CP4-T05} g.rx() with no fast argument still runs ratchet.py as a real subprocess and succeeds",
          r.returncode == 0, r.stderr)
    state = json.loads((d / ".ratchet" / "runs" / "f" / "state.json").read_text())
    gate = (state.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("behavior", {}))
    check("{CP4-T05} the project really reached and passed the adversarial step end to end, through "
          "project()->approve_plan()->to_adversarial()->rx() exactly as before",
          gate.get("status") == "passed", gate)


@case("CP4-T06")
def cp4t06_a_concrete_other_importer_of_test_gates_still_passes_unchanged():
    """{CP4-T06} tests/test_providers.py is a concrete OTHER suite that imports test_gates and calls
    project()/approve_plan()/to_adversarial()/rx() with no fast-path argument at all. Imports it for real
    (proving it still imports cleanly against the new, extended helper signatures) and drives one project
    through ITS OWN env_with()/fake_bin()/rx() fixtures end to end, the same cheap, bounded way CP4-T05
    exercises test_gates directly -- not by re-running its whole ~65-check, ~90s self-test file: embedding
    that cost here, on top of CP4-T05's and the 23 original cases', would make CP4-T02's own
    well-under-120s target for this file unmeetable no matter how good the conversion is. A fresh,
    independent run of test_providers.py's own suite (itself unaffected by this checkpoint) is already
    covered by tests/run_all.py, not re-embedded here."""
    tp = __import__("test_providers")
    b = tp.fake_bin("codex")
    env = tp.env_with(b)
    d = g.project("cp4t06")
    r = tp.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', env=env)
    check("{CP4-T06} test_providers.py's own rx() wrapper (built on test_gates.g.rx with no fast argument) "
          "still runs ratchet.py as a real subprocess and succeeds", r.returncode == 0, r.stderr)
    g.approve_plan(d)
    g.to_adversarial(d)
    state = json.loads((d / ".ratchet" / "runs" / "f" / "state.json").read_text())
    gate = (state.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("behavior", {}))
    check("{CP4-T06} the project really reached and passed the adversarial step end to end, through "
          "test_providers.py's own fixtures built on test_gates' unmodified default helpers",
          gate.get("status") == "passed", gate)


@case("CP4-T07")
def cp4t07_fast_path_matches_real_subprocess_and_no_hidden_cli_test_only_entry_point():
    """{CP4-T07} [Fix round 2 per director check-in 'simplify': the historical freeze this case used to hold
    ratchet.py to (a committed snapshot compared for byte equality -- host-alt-standards/B1, host-alt-
    correctness/C1, and round-1's own codex-correctness/X2, omp-glm-correctness/A3, codex-standards/X2) is
    gone. Freezing ratchet.py forever broke the moment any ordinary feature commit touched it, which this
    plan's own out-of-scope line (Lane M's concurrent ratchet.py refactor) names as expected, imminent work.
    What this case actually needs to keep proving -- that the fast path is a faithful stand-in for a real
    subprocess, and that ratchet.py was not given a hidden, CLI-reachable test-only entry point -- is checked
    directly, never by pinning the file's bytes against a historical commit.

    Parity: the exact same read-only command, through trust_helpers.cp5y_load_engine()'s in-process fast path
    and through a real `python3 ratchet.py` subprocess, must agree on exit code and on every status field
    that is not itself about wall-clock timing -- able to fail: a fast path that silently diverged from real
    subprocess behavior (stale cached state, a different CLI dispatch) shows up here as a mismatch.

    Structural: ratchet.py's normal CLI dispatch (every `add_parser(...)` call that wires up a real
    subcommand) must not itself be the thing a test-only addition hid behind -- no subcommand's name or help
    text calls itself test-only. A live, current-state check of the one guarantee the old diff-based check
    tried to get (that a "test-only" carve-out never becomes reachable from the real CLI), able to fail: adding
    `sp.add_parser("test-only-x", help="test-only: ...")` would trip it."""
    d = g.project("cp4t07")
    r_fast = g.rx(d, "status", "--json", fast=True)  # fast-path replaces: python3 ratchet.py status --json
    r_real = g.rx(d, "status", "--json")
    check("{CP4-T07} the fast in-process path and a real `python3 ratchet.py` subprocess agree on exit code "
          "for the same read-only command", r_fast.returncode == r_real.returncode,
          (r_fast.returncode, r_real.returncode, r_fast.stderr[:300], r_real.stderr[:300]))
    try:
        j_fast, j_real = json.loads(r_fast.stdout), json.loads(r_real.stdout)
    except (json.JSONDecodeError, TypeError) as e:
        check("{CP4-T07} both status --json outputs parse as JSON", False, str(e))
    else:
        fields = ("phase", "current", "step", "next", "stop_ok", "done", "total", "slug", "feature", "root")
        mismatches = {k: (j_fast.get(k), j_real.get(k)) for k in fields if j_fast.get(k) != j_real.get(k)}
        check("{CP4-T07} the fast path and a real subprocess agree on status's observable, non-timing fields "
              "(phase, current step, next action, stop_ok, done/total checkpoint counts, slug, feature, root)",
              mismatches == {}, mismatches)

    src = (ROOT / CP4_ENGINE_REL).read_text(encoding="utf-8")
    tree = ast.parse(src, filename=CP4_ENGINE_REL)
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser"):
            continue
        name = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        help_kw = next((kw.value.value for kw in node.keywords
                        if kw.arg == "help" and isinstance(kw.value, ast.Constant)), None)
        text = " ".join(str(x) for x in (name, help_kw) if x)
        if re.search(r"test.?only", text, re.IGNORECASE):
            offenders.append(f"{CP4_ENGINE_REL}:{node.lineno} ({name!r})")
    check("{CP4-T07} ratchet.py's normal CLI dispatch (add_parser) wires up no subcommand whose name or help "
          "text calls itself test-only", offenders == [], offenders)


@case("CP4-T08")
def cp4t08_cp2_t11_and_cp2_x11_still_hold_after_fast_path_setup():
    """{CP4-T08} CP2-T11 (two lanes' setup now built through the fast path, but their concurrent
    `providers --check` calls still go through Lane.popen's real, separate OS processes) and CP2-X11 (the
    lockless-writer race) already ran earlier in this same process (see CP4-T01's docstring on TESTS'
    ordering); this reads their already-recorded status so a quota-accounting regression the fast-path setup
    introduces (writing provider-health.json outside update_record's real lock) is caught here by name, not
    only folded into CP4-T01's generic parity count."""
    for cid in ("CP2-T11", "CP2-X11"):
        check(f"{{CP4-T08}} {cid} ran earlier in this process and is recorded", cid in CASE_STATUS,
              sorted(CASE_STATUS))
        check(f"{{CP4-T08}} {cid} passed (the shared record's real lock still caught every case it proves)",
              CASE_STATUS.get(cid, False), CASE_MESSAGES.get(cid, []))


@case("CP4-T09")
def cp4t09_measure_harness_contract_and_cp1_evidence_schema():
    """{CP4-T09} [Fix round 2 per director check-in 'simplify': the byte-identity freeze this case used to hold
    tests/measure_suite_baseline.py to, and the hash check it ran against CP1's baseline_timings.json fixture
    (host-alt-standards/B1+B2, host-alt-correctness/C1+C2), are both gone. The hash half was tautological: it
    hashed a fixture this same fix round created and compared it to a constant committed in the same change --
    it could only fail if someone edited the fixture itself, never if CP1's real evidence changed, even though
    its own message claimed the opposite. What this case actually needs to keep proving -- that the measure
    harness still runs and writes evidence in its documented schema, and that CP1's recorded evidence still
    matches that schema -- is checked directly as a live contract/schema check. No hash, no historical diff."""
    tmp = Path(_tmp.mkdtemp(prefix="cp4t09-measure-"))
    fake = tmp / "fake_ok.py"
    fake.write_text("import sys, time\ntime.sleep(0.05)\nsys.exit(0)\n", encoding="utf-8")
    out = tmp / "out.json"
    env = dict(os.environ)
    env.pop("RATCHET_TEST_REPORT", None)
    r = subprocess.run([sys.executable, "-I", str(ROOT / CP4_MEASURE_REL), "--suites", str(fake), "--out", str(out)],
                       capture_output=True, text=True, timeout=60, env=env)
    check("{CP4-T09} tests/measure_suite_baseline.py --suites ... --out ... still runs and exits 0 for a "
          "passing fake suite", r.returncode == 0, (r.returncode, r.stderr[:300]))
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except OSError as e:
        check("{CP4-T09} setup: the --out evidence file was written", False, str(e))
        return
    entry = (data.get("suites") or {}).get(fake.name)
    check('{CP4-T09} the evidence it writes still matches its documented schema: {"command": str, "suites": '
          '{<basename>: {"elapsed_sec": number, "passed": bool}}}',
          isinstance(data.get("command"), str) and "--suites" in data["command"] and isinstance(entry, dict)
          and isinstance(entry.get("elapsed_sec"), (int, float)) and entry.get("elapsed_sec") >= 0
          and entry.get("passed") is True, data)

    try:
        cp1 = json.loads(cp4_committed_base(CP4_EVIDENCE_PATH))
    except (RuntimeError, json.JSONDecodeError) as e:
        check("{CP4-T09} setup: CP1's committed baseline_timings.json fixture is readable and parses as JSON",
              False, str(e))
        return
    missing = [s for s in _msb.REAL_SUITES
               if not (isinstance((entry := (cp1.get("suites") or {}).get(Path(s).name)), dict)
                       and all(k in entry for k in ("elapsed_sec", "passed")))]
    check("{CP4-T09} CP1's recorded baseline_timings.json evidence still has every real suite's entry in that "
          "same schema (command string, suites[<basename>] = {elapsed_sec, passed}) -- a contract check, not "
          "a byte-for-byte freeze", isinstance(cp1.get("command"), str) and missing == [],
          {"missing": missing, "command": cp1.get("command")})


@case("CP4-T10")
def cp4t10_scoped_diff_real_changes_before_adds_adds_smallest_first():
    """{CP4-T10} [host-alt-correctness/A1] scoped_diff_parts' MAX_DIFF-budget packing order -- every modified
    file and pure delete before any pure add, smallest-add-first among the adds -- was nothing but an in-code
    comment: no existing case (test_review_diff_large_files.py, test_review_diff_large_files_fixes.py,
    test_review_diff_full_rounds.py, test_review_diff_full_rounds_fixes.py, test_since_tree_rejected_round.py)
    exercised it, and a plain git-order loop also passed every one of them. Builds a since->current diff where
    a large pure add ("aaa_big_add.txt") and a small pure add ("bbb_small_add.txt") both sort before a real
    modification ("zzz_modified.txt") in git's natural order, over MAX_DIFF. Mutation-proved: reverting the
    ordering fix to plain git order makes the big add win the budget instead and the real modification get
    omitted -- the exact starvation this fix prevents and this run's own CP4 adversarial rounds 3/5/6/8/9 hit
    repeatedly."""
    d = base._mk_repo("t10")
    (d / "zzz_modified.txt").write_text("line\n" * 100, encoding="utf-8")
    since = base._snapshot(d)
    (d / "zzz_modified.txt").write_text("line\n" * 100 + "CHANGED\n" * 2000, encoding="utf-8")
    (d / "aaa_big_add.txt").write_text("x" * 171000, encoding="utf-8")
    (d / "bbb_small_add.txt").write_text("y" * 500, encoding="utf-8")
    current = base._snapshot(d)
    _text, shown, omitted = base.ER.scoped_diff_parts(d, since, current)
    check("{CP4-T10} the real modification is shown, not starved out by an unrelated merge-in pure add",
          "zzz_modified.txt" in shown, (shown, omitted))
    check("{CP4-T10} the large pure add is the one omitted, not the modification",
          "aaa_big_add.txt" in omitted and "zzz_modified.txt" not in omitted, (shown, omitted))
    check("{CP4-T10} among the adds, the smaller one is kept (adds-smallest-first)",
          "bbb_small_add.txt" in shown, (shown, omitted))
    check("{CP4-T10} exactly {zzz_modified.txt, bbb_small_add.txt} shown and {aaa_big_add.txt} omitted",
          set(shown) == {"zzz_modified.txt", "bbb_small_add.txt"} and set(omitted) == {"aaa_big_add.txt"},
          (shown, omitted))


@case("CP4-T11")
def cp4t11_scoped_diff_adds_smallest_first_actually_decides_outcome():
    """{CP4-T11} [host-alt-correctness/A1 re-raise] CP4-T10 proves non-adds-before-adds, but in its numbers the
    smallest-first sort among the adds never actually changes which add is kept (the 171000-byte add can never
    fit after the ~18000-byte real modification regardless of add order, and the 500-byte add always fits
    regardless of order). This case makes the real modification SMALL on purpose (never the deciding factor) so
    which add is tried first is what decides the result: a large pure add ("aaa_big_add.txt") and a smaller pure
    add ("bbb_small_add.txt") both sort before the real modification ("zzz_modified.txt") in git's natural
    order. Mutation-proved against a copy of scoped_diff_parts with only the smallest-first sort among adds
    removed (non-adds-first kept): that mutation flips the result to {aaa_big_add.txt, zzz_modified.txt} shown
    and {bbb_small_add.txt} omitted -- the opposite of what this case expects."""
    d = base._mk_repo("t11")
    (d / "zzz_modified.txt").write_text("line\n" * 100, encoding="utf-8")
    since = base._snapshot(d)
    (d / "zzz_modified.txt").write_text("line\n" * 100 + "CHANGED\n" * 20, encoding="utf-8")
    (d / "aaa_big_add.txt").write_text("x" * 171000, encoding="utf-8")
    (d / "bbb_small_add.txt").write_text("y" * 15000, encoding="utf-8")
    current = base._snapshot(d)
    _text, shown, omitted = base.ER.scoped_diff_parts(d, since, current)
    check("{CP4-T11} the small real modification is shown",
          "zzz_modified.txt" in shown, (shown, omitted))
    check("{CP4-T11} among the adds, the smaller one is kept (adds-smallest-first actually decides it here)",
          "bbb_small_add.txt" in shown and "aaa_big_add.txt" in omitted, (shown, omitted))
    check("{CP4-T11} exactly {zzz_modified.txt, bbb_small_add.txt} shown and {aaa_big_add.txt} omitted",
          set(shown) == {"zzz_modified.txt", "bbb_small_add.txt"} and set(omitted) == {"aaa_big_add.txt"},
          (shown, omitted))


if __name__ == "__main__":
    main()
