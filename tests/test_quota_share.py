#!/usr/bin/env python3
"""
CP6 tests: share a returning provider across lanes.   python3 tests/test_quota_share.py

Every lane on a machine (a lane is one project with its own run) shares one RATCHET_HOME and so one provider-health.json.
When a round opens, Ratchet reserves a review start on the provider it picks for each slot, atomically under that
record's lock, and a provider that already has `provider_share.max` starts inside `provider_share.minutes` (default 3
per 15 minutes) takes no more: the slot goes to the next working provider, flat-rate first, and the lane is told why in
plain words. A round replaced before anyone reviewed it gives its reservations back; one that already has a review file
keeps them. Showing the same open round again never reserves again.

Every case builds throwaway git projects (tests/test_gates.py's project(), taken to the adversarial step) that share one
temp RATCHET_HOME per scenario, and a private PATH holding only fake `codex` and `opencode` programs plus the few real
binaries the engine itself needs, so no real provider is called, there is no network and no money is spent. The fake
answers the connectivity check with RATCHET-PROBE-OK and any other request with a review that has no findings; it logs
"<tag> probe|review" to $RATCHET_FAKE_LOGS/calls.log (the tag is the program plus -glm / -kimi / -qwen from the -m model),
so "never asked" is read from that log. Lanes are played through the public command `reviewers CP1 --json` only (the
concurrency cases start real parallel ratchet.py processes). Nothing here sleeps: the window passing is tested with a
lane whose own provider_share.minutes is tiny.

Each case is its own function, registered with @case("CP6-Txx") on the line directly above it and run from main()
inside try/except, so a crash is a failed case, never a silent drop. Writes a per-test JSON report to
RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP6-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, write_review, rx, ENV)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
_CUR_CASE: str | None = None
TESTS: list = []
ORIG_HOME = g.ENV["RATCHET_HOME"]
PAID_OK = "2026-10-01T00:00:00Z"            # what the developer's exact OK leaves in a provider entry: a fixed date
ENGINE = g.ENGINE
RUN_TIMEOUT = 240                          # a parallel lane's command may wait for another lane's lock or test request


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


# ------------------------------------------------------------------------------------------------ fixtures

# One fake agent CLI for every program name. Its tag is the program plus -glm / -kimi / -qwen from the -m model. It logs
# "<tag> probe|review" first, answers the connectivity check with RATCHET-PROBE-OK and anything else with a review that
# has no findings; codex also writes its answer to --output-last-message.
FAKE = r"""#!/bin/sh
me=$(basename "$0")
out=""
model=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in
    --output-last-message) out="$2"; shift 2;;
    -m) model="$2"; shift 2;;
    *) shift;;
  esac
done
tag="$me"
case "$model" in *glm*) tag="$me-glm";; *kimi*) tag="$me-kimi";; *qwen*) tag="$me-qwen";; esac
input=""
if [ ! -t 0 ]; then input=$(cat); fi
kind=review
case "$input $args" in *"connectivity check"*) kind=probe;; esac
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s %s\n' "$tag" "$kind" >> "$RATCHET_FAKE_LOGS/calls.log"; fi
if [ "$kind" = probe ]; then reply="RATCHET-PROBE-OK"; else reply='{"findings": []}'; fi
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""

CODEX = {"id": "codex", "preset": "codex"}
GLM = {"id": "opencode-glm", "preset": "opencode", "model": "opencode-go/glm-5.3", "cost": "flat"}
KIMI = {"id": "opencode-kimi", "preset": "opencode", "model": "opencode/kimi-k2", "cost": "per-use", "paid_ok_at": PAID_OK}
QWEN = {"id": "opencode-qwen", "preset": "opencode", "model": "openrouter/qwen3-coder", "cost": "per-use"}   # no recorded OK

ONE = [["any-both"]]


def ids(rnd):
    return [x["id"] for x in (rnd.get("reviewers") or [])]


def first_id(rnd):
    return (ids(rnd) or [None])[0]


def row_of(rnd, rid):
    return next((x for x in (rnd.get("reviewers") or []) if x["id"] == rid), {})


def said(r):
    return (r.stdout or "") + (r.stderr or "")


def parse(r):
    try:
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        return {}


class Scenario:
    """One machine: a fresh RATCHET_HOME, a private PATH of fakes and a call log, shared by every lane built from it.
    Use as a context manager: g.ENV points at this home while lanes are being built, and is put back afterwards."""

    def __init__(self):
        self.home = Path(_tmp.mkdtemp(prefix="ratchet-fk-user-"))
        b = Path(_tmp.mkdtemp(prefix="ratchet-fk-bin-"))
        for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
            p = shutil.which(n)
            if p:
                (b / n).symlink_to(p)
        for n in ("codex", "opencode"):
            (b / n).write_text(FAKE)
            (b / n).chmod(0o755)
        self.bin = b
        self.logs = Path(_tmp.mkdtemp(prefix="ratchet-fk-log-"))
        self.lanes: list = []

    def __enter__(self):
        g.ENV["RATCHET_HOME"] = str(self.home)
        return self

    def __exit__(self, *exc):
        g.ENV["RATCHET_HOME"] = ORIG_HOME
        return False

    def lane(self, tag, providers, slots=None, share=None, risk="low", extra_cp=None):
        ln = Lane(self, tag, providers, slots, share, risk, extra_cp)
        self.lanes.append(ln)
        return ln

    def lanes_parallel(self, specs):
        """Build several lanes on a small thread pool (set-up is the slow part): specs are (tag, kwargs)."""
        with ThreadPoolExecutor(max_workers=4) as pool:
            made = list(pool.map(lambda s: Lane(self, s[0], **s[1]), specs))
        self.lanes += made
        return made

    def calls(self, tag=None, kind=None):
        p = self.logs / "calls.log"
        lines = p.read_text().splitlines() if p.exists() else []
        return [x for x in lines if (tag is None or x.split()[0] == tag) and (kind is None or x.split()[1] == kind)]

    def health_path(self):
        return self.home / "provider-health.json"

    def health(self):
        try:
            return json.loads(self.health_path().read_text())
        except (OSError, ValueError):
            return {}

    def starts(self, key):
        return (self.health().get(key) or {}).get("starts") or []


class Lane:
    """One throwaway project (a lane): at the adversarial step, providers and rules set BEFORE the plan is approved
    (a later rule change stops every reviewers call). `slots` None keeps the default review_depth for the risk."""

    def __init__(self, scn, tag, providers, slots=None, share=None, risk="low", extra_cp=None):
        self.scn = scn
        self.rounds = 0
        cfg = ["providers=" + json.dumps(providers)]
        if slots is not None:
            cfg.append("review_depth=" + json.dumps({risk: slots}))
        if share is not None:
            cfg.append("provider_share=" + json.dumps({"max": share[0], "minutes": share[1]}))
        cp = {"risk": risk}
        cp.update(extra_cp or {})
        self.dir = g.project(tag, extra_cp=cp, config=cfg)
        g.approve_plan(self.dir)
        g.to_adversarial(self.dir)
        self.env = dict(g.ENV)
        self.env["PATH"] = str(scn.bin)
        self.env["RATCHET_HOST_PROVIDER"] = "anthropic"
        self.env["RATCHET_FAKE_LOGS"] = str(scn.logs)
        self.env["RATCHET_HOME"] = str(scn.home)

    def run(self, *args):
        return g.rx(self.dir, *args, env=self.env)

    def popen(self, *args):
        return subprocess.Popen([sys.executable, str(ENGINE), *args], cwd=self.dir, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def nudge(self):
        with open(self.dir / "src" / "f.js", "a") as f:
            f.write(f"// nudge {self.rounds}\n")

    def open_round(self, nudge=False):
        """`reviewers CP1 --json` (the code nudged first when asked, so a new round opens rather than the open one
        being shown again). Returns (process, round as printed or {} when it failed)."""
        if nudge:
            self.nudge()
        self.rounds += 1
        return self.again()

    def again(self):
        r = self.run("reviewers", "CP1", "--json")
        return r, parse(r)

    def review_file(self, rnd, rid):
        """A review written into the round's own folder (through the guard hook, as the agent's reviewer would)."""
        old = g.ENV["RATCHET_HOME"]
        g.ENV["RATCHET_HOME"] = str(self.scn.home)
        try:
            return g.write_review(self.dir, rnd, rid)
        finally:
            g.ENV["RATCHET_HOME"] = old


def parallel_open(lanes):
    """Every lane runs `reviewers CP1 --json` as its own ratchet.py process, all started together. Returns
    [(process-like with returncode/stdout/stderr, round)], in lane order."""
    procs = [ln.popen("reviewers", "CP1", "--json") for ln in lanes]
    res = []
    for p in procs:
        try:
            out, err = p.communicate(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
            err += "\n(timed out)"
        r = subprocess.CompletedProcess(p.args, p.returncode, out, err)
        res.append((r, parse(r)))
    return res


def ready(results, n):
    need(f"all {n} lanes exit 0 with a round of one reviewer",
         len(results) == n and all(r.returncode == 0 and len(ids(rnd)) == 1 for r, rnd in results),
         [(r.returncode, said(r)[-200:]) for r, _ in results])


def count(picked, rid):
    return sum(1 for x in picked if x == rid)


# ------------------------------------------------------------------------------------------------ cases

@case("CP6-T01")
def cp6_t01_four_simultaneous_lanes_never_start_more_than_three_reviews_on_one_provider():
    with Scenario() as scn:
        lanes = scn.lanes_parallel([(f"lane-{c}", {"providers": [CODEX]}) for c in "abcd"])
        res = parallel_open(lanes)
    ready(res, 4)
    picked = [first_id(rnd) for _r, rnd in res]
    check("exactly 3 lanes have codex-both", count(picked, "codex-both") == 3, picked)
    check("exactly 1 lane has host-alt-both", count(picked, "host-alt-both") == 1, picked)
    check("never 4 on codex", count(picked, "codex-both") < 4, picked)
    check("the record holds exactly 3 codex starts", len(scn.starts("codex")) == 3, scn.health())


@case("CP6-T02")
def cp6_t02_six_simultaneous_lanes_spread_over_two_providers_none_over_its_share_none_unused():
    with Scenario() as scn:
        lanes = scn.lanes_parallel([(f"lane-{c}", {"providers": [CODEX, GLM], "share": (2, 15)}) for c in "abcdef"])
        res = parallel_open(lanes)
    ready(res, 6)
    picked = [first_id(rnd) for _r, rnd in res]
    check("codex-both appears in at most 2 lanes", count(picked, "codex-both") <= 2, picked)
    check("opencode-glm-both appears in at most 2 lanes", count(picked, "opencode-glm-both") <= 2, picked)
    other = [x for x in picked if x and not x.startswith("host-")]
    check("exactly 4 lanes use an other-company provider (the capacity is fully used)", len(other) == 4, picked)
    check("exactly 2 lanes use host-alt-both", count(picked, "host-alt-both") == 2, picked)
    check("each provider ends with exactly 2 starts in the record",
          len(scn.starts("codex")) == 2 and len(scn.starts("opencode:opencode-go/glm-5.3")) == 2, scn.health())


@case("CP6-T03")
def cp6_t03_a_lane_over_the_cap_goes_to_the_next_provider_flat_first_and_no_consent_means_never_used():
    with Scenario() as scn:
        lanes = scn.lanes_parallel([(f"lane-{i}", {"providers": [GLM, CODEX, KIMI, QWEN], "share": (1, 15)})
                                    for i in range(1, 6)])
        picked = []
        for ln in lanes:
            r, rnd = ln.open_round()
            need(f"{ln.dir.name}'s round opens with one reviewer", r.returncode == 0 and len(ids(rnd)) == 1,
                 (said(r)[-300:], ids(rnd)))
            picked.append(first_id(rnd))
    check("lane 1 gets opencode-glm-both (flat first)", picked[0] == "opencode-glm-both", picked)
    check("lane 2 gets codex-both (glm is at its cap; unlabeled before per-use)", picked[1] == "codex-both", picked)
    check("lane 3 gets opencode-kimi-both (per-use only after the others are full, and it has the OK)",
          picked[2] == "opencode-kimi-both", picked)
    check("lane 4 gets host-alt-both", picked[3] == "host-alt-both", picked)
    check("lane 5 gets host-alt-both", picked[4] == "host-alt-both", picked)
    check("opencode-qwen is never chosen by any lane", not any("qwen" in (x or "") for x in picked), picked)
    check("the per-use provider without the developer's OK was never asked, not even a test request",
          scn.calls("opencode-qwen") == [], scn.calls())


@case("CP6-T04")
def cp6_t04_a_lane_sent_elsewhere_is_told_why_in_plain_words():
    with Scenario() as scn:
        l1, l2 = scn.lanes_parallel([(f"lane-{i}", {"providers": [CODEX], "share": (1, 15)}) for i in (1, 2)])
        r1, rnd1 = l1.open_round()
        r2, rnd2 = l2.open_round()
        plain = l2.run("reviewers", "CP1")
    need("both rounds open", r1.returncode == 0 and r2.returncode == 0 and ids(rnd1) and ids(rnd2),
         (said(r1)[-200:], said(r2)[-200:]))
    check("lane 1 gets codex-both", ids(rnd1) == ["codex-both"], ids(rnd1))
    check("lane 2 gets host-alt-both", ids(rnd2) == ["host-alt-both"], ids(rnd2))
    row = row_of(rnd2, "host-alt-both")
    check("lane 2's round records codex-both as skipped", "codex-both" in (row.get("fallback_for") or []), row)
    why = str((row.get("reasons") or {}).get("codex-both", ""))
    check("the reason names the cap (1) and the window (15 minutes)",
          re.search(r"\b1\b", why) is not None and "15 minutes" in why, why)
    line = f"(instead of codex-both: {why})"
    check("the plain-text output shows the '(instead of codex-both: ...)' line with that reason",
          plain.returncode == 0 and bool(why) and line in plain.stdout, plain.stdout[-500:])
    check("lane 1 has no such note", not row_of(rnd1, "codex-both").get("fallback_for"), row_of(rnd1, "codex-both"))


@case("CP6-T05")
def cp6_t05_when_the_window_passes_the_provider_takes_new_reviews_again():
    with Scenario() as scn:
        specs = [("lane-1", {"providers": [CODEX], "share": (1, 15)}),
                 ("lane-2", {"providers": [CODEX], "share": (1, 15)}),
                 ("lane-3", {"providers": [CODEX], "share": (1, 0.001)})]
        l1, l2, l3 = scn.lanes_parallel(specs)
        got = []
        for ln in (l1, l2, l3):
            r, rnd = ln.open_round()
            need(f"{ln.dir.name}'s round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
            got.append(first_id(rnd))
    check("lane 1 gets codex-both", got[0] == "codex-both", got)
    check("lane 2 (the reservation is inside its window) gets host-alt-both", got[1] == "host-alt-both", got)
    check("lane 3 (the reservation is outside its tiny window) gets codex-both again", got[2] == "codex-both", got)


@case("CP6-T06")
def cp6_t06_a_round_replaced_before_anyone_reviewed_it_gives_its_reservations_back():
    with Scenario() as scn:
        a, b = scn.lanes_parallel([(f"lane-{c}", {"providers": [CODEX], "share": (1, 15)}) for c in "ab"])
        r1, rnd1 = a.open_round()
        need("lane A's round 1 opens on codex-both", r1.returncode == 0 and ids(rnd1) == ["codex-both"],
             (said(r1)[-300:], ids(rnd1)))
        need("no review file exists in round 1", not list(Path(rnd1["output_dir"]).glob("*.json")), rnd1["output_dir"])
        r2, rnd2 = a.open_round(nudge=True)
        r3, rnd3 = b.open_round()
    need("lane A's replacement round opens", r2.returncode == 0 and len(ids(rnd2)) == 1, (said(r2)[-300:], ids(rnd2)))
    check("the replacement round is round 2", rnd2.get("round") == 2, rnd2.get("round"))
    check("the replacement round gets codex-both again (its own reservation was given back)",
          ids(rnd2) == ["codex-both"], (ids(rnd2), row_of(rnd2, first_id(rnd2)).get("reasons")))
    check("lane B gets host-alt-both (only one reservation exists on codex, not two)",
          r3.returncode == 0 and ids(rnd3) == ["host-alt-both"], (said(r3)[-300:], ids(rnd3)))
    check("the replacement is not charged against the run's round count", rnd2.get("run_rounds_used") == 1,
          rnd2.get("run_rounds_used"))
    check("the record holds exactly one codex start", len(scn.starts("codex")) == 1, scn.health())


@case("CP6-T07")
def cp6_t07_a_round_that_was_already_reviewed_keeps_its_reservation_when_replaced():
    with Scenario() as scn:
        a = scn.lane("lane-a", [CODEX], share=(1, 15))
        r1, rnd1 = a.open_round()
        need("round 1 opens on codex-both", r1.returncode == 0 and ids(rnd1) == ["codex-both"], (said(r1)[-300:], ids(rnd1)))
        a.review_file(rnd1, "codex-both")
        need("a review file now exists in round 1's folder", bool(list(Path(rnd1["output_dir"]).glob("codex-both.json"))),
             rnd1["output_dir"])
        r2, rnd2 = a.open_round(nudge=True)
    need("round 2 opens", r2.returncode == 0 and len(ids(rnd2)) == 1, (said(r2)[-300:], ids(rnd2)))
    check("it is round 2", rnd2.get("round") == 2, rnd2.get("round"))
    check("round 2 gets host-alt-both: the reviewed round's reservation was not handed back", ids(rnd2) == ["host-alt-both"],
          ids(rnd2))
    check("the record still holds the reviewed round's codex start", len(scn.starts("codex")) == 1, scn.health())


@case("CP6-T08")
def cp6_t08_showing_the_same_open_round_again_does_not_reserve_again():
    with Scenario() as scn:
        a, b, c = scn.lanes_parallel([(f"lane-{x}", {"providers": [CODEX], "share": (2, 15)}) for x in "abc"])
        r1, rnd1 = a.open_round()
        need("lane A's round opens on codex-both", r1.returncode == 0 and ids(rnd1) == ["codex-both"],
             (said(r1)[-300:], ids(rnd1)))
        again = [a.again() for _ in range(3)]
        rb, rnd_b = b.open_round()
        rc, rnd_c = c.open_round()
    for n, (r, rnd) in enumerate(again, 1):
        check(f"lane A still shows codex-both on repeat {n}, the same round",
              r.returncode == 0 and ids(rnd) == ["codex-both"] and rnd.get("round") == rnd1.get("round")
              and rnd.get("round_nonce") == rnd1.get("round_nonce"), (said(r)[-200:], ids(rnd)))
    check("lane B gets codex-both (the second reservation)", rb.returncode == 0 and ids(rnd_b) == ["codex-both"],
          (said(rb)[-200:], ids(rnd_b)))
    check("lane C gets host-alt-both", rc.returncode == 0 and ids(rnd_c) == ["host-alt-both"], (said(rc)[-200:], ids(rnd_c)))
    check("the record holds exactly 2 codex starts", len(scn.starts("codex")) == 2, scn.health())


@case("CP6-T09")
def cp6_t09_every_review_slot_of_a_round_counts_as_one_start_on_its_provider():
    with Scenario() as scn:
        l1, l2 = scn.lanes_parallel([(f"lane-{i}", {"providers": [CODEX], "risk": "normal"}) for i in (1, 2)])
        r1, rnd1 = l1.open_round()
        r2, rnd2 = l2.open_round()
    need("both rounds open", r1.returncode == 0 and r2.returncode == 0, (said(r1)[-200:], said(r2)[-200:]))
    check("lane 1 has codex-standards and codex-correctness", sorted(ids(rnd1)) == ["codex-correctness", "codex-standards"],
          ids(rnd1))
    mine = ids(rnd2)
    check("lane 2 has exactly one codex reviewer and one host-alt reviewer (only 1 start was left)",
          len(mine) == 2 and sum(1 for x in mine if x.startswith("codex-")) == 1
          and sum(1 for x in mine if x.startswith("host-alt-")) == 1, mine)
    check("across the two lanes codex has 3 starts, not 4", len(scn.starts("codex")) == 3, scn.health())


@case("CP6-T10")
def cp6_t10_an_explicit_reviewer_list_over_its_cap_tries_another_working_provider_before_the_host():
    with Scenario() as scn:
        a = scn.lane("lane-a", [CODEX], share=(1, 15))
        b = scn.lane("lane-b", [CODEX, GLM], share=(1, 15), extra_cp={"reviewers": ["codex-both"]})
        ra, rnd_a = a.open_round()
        need("lane A (any-both) takes codex-both", ra.returncode == 0 and ids(rnd_a) == ["codex-both"],
             (said(ra)[-300:], ids(rnd_a)))
        rb, rnd_b = b.open_round()
    need("lane B's round opens with one reviewer", rb.returncode == 0 and len(ids(rnd_b)) == 1, (said(rb)[-300:], ids(rnd_b)))
    check("lane B's reviewer is opencode-glm-both, not host-alt-both", ids(rnd_b) == ["opencode-glm-both"], ids(rnd_b))
    row = row_of(rnd_b, "opencode-glm-both")
    check("it records that codex-both was skipped", "codex-both" in (row.get("fallback_for") or []), row)
    why = str((row.get("reasons") or {}).get("codex-both", ""))
    check("and says why, naming the cap (1) and the window (15 minutes)",
          re.search(r"\b1\b", why) is not None and "15 minutes" in why, why)


@case("CP6-T11")
def cp6_t11_a_damaged_shared_record_does_not_stall_a_lane_or_lose_later_counting():
    with Scenario() as scn:
        l1, l2, l3 = scn.lanes_parallel([(f"lane-{i}", {"providers": [CODEX], "share": (1, 15)}) for i in (1, 2, 3)])
        r1, rnd1 = l1.open_round()
        need("lane 1's round opens on codex-both", r1.returncode == 0 and ids(rnd1) == ["codex-both"],
             (said(r1)[-300:], ids(rnd1)))
        damaged = []
        for p in sorted(scn.home.rglob("*")):
            low = p.name.lower()
            if not p.is_file() or "director" in low or "hook" in low or "key" in low or p.suffix == ".lock":
                continue
            try:
                if "codex" in p.read_text():
                    p.write_text("{not json")
                    damaged.append(p.name)
            except (OSError, UnicodeDecodeError):
                continue
        need("the shared record existed and was damaged", "provider-health.json" in damaged, damaged)
        r2, rnd2 = l2.open_round()
        r3, rnd3 = l3.open_round()
    check("lane 2 exits 0 with a valid round", r2.returncode == 0 and first_id(rnd2) in ("codex-both", "host-alt-both"),
          (r2.returncode, said(r2)[-300:]))
    check("lane 2 did not crash with a traceback", "Traceback" not in r2.stderr, r2.stderr[-300:])
    check("lane 2 says plainly on stderr that the shared record was unreadable and rebuilt",
          "provider-health.json" in r2.stderr and re.search(r"could not be read|unreadable", r2.stderr) is not None
          and "rebuild" in r2.stderr, r2.stderr[-400:])
    check("lane 3 exits 0 with a valid round", r3.returncode == 0 and first_id(rnd3) in ("codex-both", "host-alt-both"),
          (r3.returncode, said(r3)[-300:]))
    got = [first_id(rnd2), first_id(rnd3)]
    check("share counting works again: with max 1, exactly one of lanes 2 and 3 gets codex-both",
          count(got, "codex-both") == 1 and count(got, "host-alt-both") == 1, got)
    check("lane 3 does not get codex-both when lane 2 did", not (got[0] == "codex-both" and got[1] == "codex-both"), got)


@case("CP6-T12")
def cp6_t12_the_docs_describe_the_per_provider_share_and_its_setting():
    files = {"README.md": ROOT / "README.md",
             "skills/ratchet-orchestrator/references/schemas.md":
                 ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"}
    for name, path in files.items():
        text = path.read_text()
        check(f"{name} mentions provider_share", "provider_share" in text, name)
        spots = [m.start() for m in re.finditer("provider_share", text)]
        near = re.sub(r"\s+", " ", " ".join(text[max(0, s - 1200):s + 1200] for s in spots)).lower()
        check(f"{name} gives the default of 3 per 15 minutes",
              re.search(r"\b(3|three)\b[^.]{0,60}15 minutes|15 minutes[^.]{0,60}\b(3|three)\b", near) is not None, name)
        check(f"{name} says it applies across all lanes on the machine",
              "lanes" in near and re.search(r"machine|every project|all lanes|every lane|across", near) is not None, name)
        check(f"{name} says a round replaced before any review gives its reservation back",
              "replaced" in near and re.search(r"gives? (it|them|its|their)[^.]{0,40}back|given back|handed back|gives? back",
                                                near) is not None and "reserv" in near, name)


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


# ------------------------------------------------------------------------------------------------ fix-round cases

import datetime as _xdt                 # noqa: E402
sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import provider_health as _xph          # noqa: E402  (the shared record's own helpers)

XKIMI_FLAT = {"id": "opencode-kimi", "preset": "opencode", "model": "opencode/kimi-k2", "cost": "flat"}
XBLOCKER = [{"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "unsafe write",
             "user_impact": "a user's saved data could be silently corrupted"}]


def _xnow_iso():
    return _xdt.datetime.now(_xdt.timezone.utc).isoformat(timespec="microseconds")


# The same fake, but a review request is answered with the text in $RATCHET_FAKE_LOGS/reply.json when that file exists.
XFAKE = FAKE.replace("else reply='{\"findings\": []}'; fi",
                     "elif [ -f \"$RATCHET_FAKE_LOGS/reply.json\" ]; then reply=$(cat \"$RATCHET_FAKE_LOGS/reply.json\"); "
                     "else reply='{\"findings\": []}'; fi")
assert XFAKE != FAKE


def _xrun_review(lane, rnd, rid, findings=None):
    """The agent runs one external reviewer through external_review.py; the fake answers with `findings`."""
    for n in ("codex", "opencode"):
        (lane.scn.bin / n).write_text(XFAKE)
    reply = lane.scn.logs / "reply.json"
    if findings:
        reply.write_text(json.dumps({"findings": findings}))
    else:
        reply.unlink(missing_ok=True)
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    r = subprocess.run([sys.executable, str(ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"),
                        "--reviewer", rid, "--cp", "CP1", "--out", str(out)],
                       cwd=lane.dir, capture_output=True, text=True, env=lane.env)
    need(f"{rid}'s review is written", r.returncode == 0 and out.exists(), said(r)[-300:])
    return str(out)


def _xseed(scn, record):
    scn.health_path().write_text(json.dumps(record))


def _xblock_then_reopen(lane, rnd, blocked_rid):
    """Records the round with a blocker from `blocked_rid` only, fixes it, and opens the next round."""
    paths = [_xrun_review(lane, rnd, x["id"], XBLOCKER if x["id"] == blocked_rid else []) for x in rnd["reviewers"]]
    rec = lane.run("adv-record", "CP1", "--reviews", *paths)
    need("the blocking round is recorded (exit 2)", rec.returncode == 2, said(rec)[-300:])
    lane.nudge()
    r = lane.run("reviewers", "CP1", "--json")
    m = re.search(r"no fix-or-rebut decision: (.+?)\. Run", r.stderr or "")
    for ref in (m.group(1).split(", ") if m else []):
        lane.run("triage", "CP1", f'{ref}=fix:"fixed"')
    if m:
        r = lane.run("reviewers", "CP1", "--json")
    return r, parse(r)


@case("CP6-X01")
def cp6_x01_a_narrowed_round_reserves_only_the_lens_that_still_has_a_blocker():
    with Scenario() as scn:
        a = scn.lane("lane-a", [CODEX], risk="normal")
        r1, rnd1 = a.open_round()
        need("round 1 has codex-standards and codex-correctness",
             r1.returncode == 0 and sorted(ids(rnd1)) == ["codex-correctness", "codex-standards"], (said(r1)[-300:], ids(rnd1)))
        need("two codex starts so far", len(scn.starts("codex")) == 2, scn.health())
        r2, rnd2 = _xblock_then_reopen(a, rnd1, "codex-correctness")
    need("round 2 opens", r2.returncode == 0, said(r2)[-300:])
    # lane H CP5 supersedes narrowing here: after a block and a fixer run, round 2 is a fix round (not "narrowed"),
    # tied to the one reviewer (codex-correctness) who raised the blocker -- the slot-resolution mechanics below
    # (which share this test exercises) are unaffected by that label change.
    check("round 2 is a fix round naming only the correctness reviewer",
          rnd2.get("fix_round") is True and len(ids(rnd2)) == 1, (rnd2.get("fix_round"), ids(rnd2)))
    check("it keeps codex-correctness, not host-alt-correctness (the dropped standards slot used no share)",
          ids(rnd2) == ["codex-correctness"], (ids(rnd2), row_of(rnd2, first_id(rnd2)).get("reasons")))
    check("codex holds 3 starts: the 2 from round 1 and 1 for the kept slot, none for the dropped one",
          len(scn.starts("codex")) == 3, scn.health())


@case("CP6-X02")
def cp6_x02_a_refill_over_the_cap_goes_on_with_the_reason_and_gives_the_idle_start_back():
    with Scenario() as scn:
        b = scn.lane("lane-b", [CODEX, GLM], share=(1, 15))
        a = scn.lane("lane-a", [CODEX, GLM], share=(1, 15))
        rb, rnd_b = b.open_round()
        need("lane B takes the flat-rate opencode-glm-both", rb.returncode == 0 and ids(rnd_b) == ["opencode-glm-both"],
             (said(rb)[-300:], ids(rnd_b)))
        ra, rnd_a = a.open_round()
        need("lane A (glm is at its share) takes codex-both", ra.returncode == 0 and ids(rnd_a) == ["codex-both"],
             (said(ra)[-300:], ids(rnd_a)))
        m = a.run("reviewer-status", "--mark", "codex-both", "--reason", "auth expired mid-round")
        need("codex-both is marked failed mid-round", m.returncode == 0, said(m)[-300:])
        rf, rnd_f = a.again()
    need("the same round is shown again", rf.returncode == 0 and rnd_f.get("round") == rnd_a.get("round"), said(rf)[-300:])
    check("the slot goes to the host (glm is at its share, codex failed)", ids(rnd_f) == ["host-alt-both"], ids(rnd_f))
    row = row_of(rnd_f, "host-alt-both")
    check("the replaced codex-both is named in fallback_for", "codex-both" in (row.get("fallback_for") or []), row)
    why = str((row.get("reasons") or {}).get("opencode-glm-both", ""))
    check("the reason names glm's cap and the 15 minute window", "15 minutes" in why and re.search(r"\b1\b", why), why)
    check("codex's idle start was given back", len(scn.starts("codex")) == 0, scn.health())
    check("glm still holds only lane B's start", len(scn.starts("opencode:opencode-go/glm-5.3")) == 1, scn.health())


@case("CP6-X03")
def cp6_x03_a_refill_reserves_the_replacement_and_names_the_replaced_reviewer_first():
    with Scenario() as scn:
        b = scn.lane("lane-b", [CODEX, GLM])
        a = scn.lane("lane-a", [CODEX, GLM])
        rb, rnd_b = b.open_round()
        need("lane B takes opencode-glm-both", rb.returncode == 0 and ids(rnd_b) == ["opencode-glm-both"], (said(rb)[-300:], ids(rnd_b)))
        ra, rnd_a = a.open_round()
        need("lane A takes codex-both (fewer starts)", ra.returncode == 0 and ids(rnd_a) == ["codex-both"], (said(ra)[-300:], ids(rnd_a)))
        m = a.run("reviewer-status", "--mark", "codex-both", "--reason", "auth expired mid-round")
        need("codex-both is marked failed mid-round", m.returncode == 0, said(m)[-300:])
        rf, rnd_f = a.again()
    need("the same round is shown again", rf.returncode == 0 and rnd_f.get("round") == rnd_a.get("round"), said(rf)[-300:])
    check("the slot goes to opencode-glm-both", ids(rnd_f) == ["opencode-glm-both"], ids(rnd_f))
    row = row_of(rnd_f, "opencode-glm-both")
    check("codex-both ranks after glm now, yet is named first in fallback_for", (row.get("fallback_for") or [""])[0] == "codex-both", row)
    check("with a reason", bool((row.get("reasons") or {}).get("codex-both")), row)
    check("glm holds two starts (lane B's and this reservation)", len(scn.starts("opencode:opencode-go/glm-5.3")) == 2, scn.health())
    check("codex's start was given back", len(scn.starts("codex")) == 0, scn.health())


@case("CP6-X04")
def cp6_x04_a_pick_survives_a_give_back_and_keeps_the_rotation_going():
    ph = _xph
    at = _xdt.datetime.now(_xdt.timezone.utc)
    data = {}
    ph.record_start(data, "k", at, "n:r")
    ph.mark_picked(data, "k", at)
    ph.refund_start(data, "k", "n:r", keep_picked=True)
    check("a replaced unreviewed round's give-back keeps the pick time", ph.last_picked(data, "k", at) == at.timestamp(), data)
    check("... while the start itself is gone", ph.starts_within(data, "k", at, 15) == 0, data)
    other = {}
    ph.record_start(other, "k", at, "n:r")
    ph.mark_picked(other, "k", at)
    ph.refund_start(other, "k", "n:r")
    check("a plain give-back forgets the pick time", ph.last_picked(other, "k", at) == 0.0, other)
    with Scenario() as scn:
        _xseed(scn, {"opencode:opencode-go/glm-5.3": {"picked": _xnow_iso()}})
        a = scn.lane("lane-a", [GLM, XKIMI_FLAT])
        r, rnd = a.open_round()
    need("the round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    check("with no starts anywhere, the provider picked longest ago (kimi) goes first, not the first one in config",
          ids(rnd) == ["opencode-kimi-both"], ids(rnd))


@case("CP6-X05")
def cp6_x05_a_provider_at_its_share_gets_no_test_request():
    with Scenario() as scn:
        _xseed(scn, {"codex": {"starts": [{"at": _xnow_iso(), "id": "other-lane:codex-both"}]}})
        a = scn.lane("lane-a", [CODEX], share=(1, 15))
        r, rnd = a.open_round()
    need("the round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    check("the slot goes to the host", ids(rnd) == ["host-alt-both"], ids(rnd))
    check("codex was sent no test request", scn.calls("codex", "probe") == [], scn.calls())
    check("and no review", scn.calls("codex", "review") == [], scn.calls())


@case("CP6-X06")
def cp6_x06_a_busy_record_says_the_share_check_was_skipped_and_the_starts_come_afterwards():
    with Scenario() as scn:
        a = scn.lane("lane-a", [CODEX])
        b = scn.lane("lane-b", [CODEX])
        ra, rnd_a = a.open_round()
        need("lane A's round opens and tests codex", ra.returncode == 0 and ids(rnd_a) == ["codex-both"], (said(ra)[-300:], ids(rnd_a)))
        lock = scn.home / "provider-health.lock"
        lock.write_text("held by this test")
        try:
            rb, rnd_b = b.open_round()
        finally:
            lock.unlink(missing_ok=True)
    need("lane B's round still opens", rb.returncode == 0 and len(ids(rnd_b)) == 1, said(rb)[-300:])
    err = (rb.stderr or "").lower()
    check("the note says the starts could not be reserved under the lock", "could not be reserved under the lock" in err, rb.stderr[-400:])
    check("... and that the starts are recorded afterwards", "recorded afterwards" in err, rb.stderr[-400:])
    check("... and no longer says a review start was not saved", "review start was not saved" not in err, rb.stderr[-400:])


@case("CP6-X07")
def cp6_x07_a_bad_provider_share_is_refused_by_name_and_leaves_the_config_alone():
    d = g.project("lane-share-bad")
    cfg = d / ".ratchet" / "config.json"
    for setting, key in [("provider_share.max=0", "provider_share.max"), ("provider_share.max=-1", "provider_share.max"),
                         ("provider_share.max=true", "provider_share.max"), ("provider_share.max=0.5", "provider_share.max"),
                         ("provider_share.max=2.5", "provider_share.max"), ("provider_share.minutes=0", "provider_share.minutes"),
                         ("provider_share.minutes=-5", "provider_share.minutes"),
                         ('provider_share.minutes="soon"', "provider_share.minutes"),
                         ("provider_share.minutes=true", "provider_share.minutes"), ("provider_share=5", "provider_share")]:
        before = cfg.read_bytes()
        r = g.rx(d, "config", "--set", setting)
        check(f"{setting} is refused", r.returncode != 0, said(r)[-300:])
        check(f"{setting}: the message names {key}", key in said(r), said(r)[-300:])
        check(f"{setting}: config.json is unchanged", cfg.read_bytes() == before)
    r = g.rx(d, "config", "--set", "provider_share.max=2", "provider_share.minutes=7.5")
    check("a good value is still accepted", r.returncode == 0, said(r)[-300:])


# `reviewers CP1 --json` run in-process with the read-only share view made stale (every provider's starts hidden from
# provider_health()), so the choice made under the lock differs from the one made just before it.
XSTALE_DRIVER = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ratchet_engine_stale_view", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules["ratchet_engine_stale_view"] = m
spec.loader.exec_module(m)
real = m.provider_health
def stale():
    d = json.loads(json.dumps(real()))
    for e in d.values():
        if isinstance(e, dict):
            e.pop("starts", None)
    return d
m.provider_health = stale
m.main(["reviewers", "CP1", "--json"])
"""


@case("CP6-X08")
def cp6_x08_a_reservation_released_for_a_test_request_leaves_no_start_behind():
    with Scenario() as scn:
        # glm: a fresh passing test and one start (its share of 1 is used); codex: never tested. Seen through the stale
        # view, glm still has room, so the unlocked pick is glm; under the lock glm is at its share, the pick is codex,
        # which is due a test request: that reservation is released, codex is tested (the fake fails it), and the
        # re-pick goes to the host.
        _xseed(scn, {"opencode:opencode-go/glm-5.3": {"ok": True, "at": _xnow_iso(), "detail": "answered the test",
                                                    "starts": [{"at": _xnow_iso(), "id": "other-lane:opencode-glm-both"}]}})
        a = scn.lane("lane-a", [CODEX, GLM], share=(1, 15))
        (scn.bin / "codex").write_text(FAKE.replace('reply="RATCHET-PROBE-OK"', 'reply="no thanks"'))
        r = subprocess.run([sys.executable, "-c", XSTALE_DRIVER, str(ENGINE)], cwd=a.dir, env=a.env,
                           capture_output=True, text=True, timeout=RUN_TIMEOUT)
        rnd = parse(r)
    need("the round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-400:], ids(rnd)))
    check("codex was sent a test request (the released pick was due one)", scn.calls("codex", "probe") != [], scn.calls())
    check("the slot goes to the host (codex failed its test, glm is at its share)", ids(rnd) == ["host-alt-both"], ids(rnd))
    check("codex holds no start: the released reservation was not left behind", len(scn.starts("codex")) == 0, scn.health())
    check("glm still holds only the other lane's start", len(scn.starts("opencode:opencode-go/glm-5.3")) == 1, scn.health())


if __name__ == "__main__":
    main()
