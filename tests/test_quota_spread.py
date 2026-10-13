#!/usr/bin/env python3
"""
CP5 tests: spread reviews by lens, flat-rate first.   python3 tests/test_quota_spread.py

When a round opens, Ratchet orders the working providers for each any-<lens> slot: flat-rate before a provider with
no cost label before a per-use provider (and a per-use provider only with the developer's recorded OK, paid_ok_at);
inside one cost tier the provider preferred for that lens comes first (providers --add ... --prefer-for <lens>), then
the one reviewed least recently across every lane on the machine (the shared record under RATCHET_HOME), then the
order written. An explicit slot list keeps its written order. A per-use provider without a recorded OK is never asked
anything, not even a test request, even when nothing else works: the host reviews instead.

Every case builds throwaway git projects (tests/test_gates.py's project(), taken to the adversarial step of a
normal-risk step, neutral names such as "lane-a") with a per-user folder (RATCHET_HOME; several projects share one
when a case is about several lanes on one machine) and a private PATH holding only fake agent CLIs plus the few real
binaries the engine itself needs. Each provider is a hand-written command provider named like its fake program
(prov-one, prov-metered ...), so no preset, no model list and no paid-provider question is involved; the providers are
registered with `providers --add --command ... --company ... --no-check` and the cost label and the developer's OK are
then written straight into the project's config (lane E's consent flow is not touched). Each fake logs
"<program> probe|review" to $RATCHET_FAKE_LOGS/calls.log, so "never asked, not even a test request" is read from that
log alone. The agent's side is played through the public commands only: `reviewers CP1 --json`, `reviewer-status
--mark`, `providers --add / --json`. No real provider is called, there is no network and no money is spent.

Each case is its own function, registered with @case("CP5-Txx") on the line directly above it and run from main()
inside try/except, so a crash is a failed case, never a silent drop. Writes a per-test JSON report to
RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP5-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import traceback
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
PAID_OK = "2026-10-01T12:00:00+00:00"       # what lane E's consent leaves in a provider entry: a fixed date


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

# One fake agent CLI for every provider. Its tag is its own program name. It logs "<tag> probe|review" first, then
# answers the connectivity check with RATCHET-PROBE-OK and anything else with a review that has no findings.
FAKE = r"""#!/bin/sh
me=$(basename "$0")
input=""
if [ ! -t 0 ]; then input=$(cat); fi
kind=review
case "$input $*" in *"connectivity check"*) kind=probe;; esac
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s %s\n' "$me" "$kind" >> "$RATCHET_FAKE_LOGS/calls.log"; fi
if [ "$kind" = probe ]; then reply="RATCHET-PROBE-OK"; else reply='{"findings": []}'; fi
printf '%s\n' "$reply"
"""

# id -> (company, fields written straight into the config entry after the provider is added)
COMPANY = {"prov-one": "acme", "prov-two": "borealis", "prov-three": "cirrus", "prov-flat": "acme",
           "prov-plain": "borealis", "prov-metered": "cirrus", "opencode-glm": "zhipu", "opencode-kimi": "moonshot",
           "codex": "openai"}
LENS_NAMES = ("correctness", "standards", "both")


def P(pid, cost=None, ok=False, prefer=None, absent=False):
    """One provider: `cost` 'flat' / 'per-use' / None (no cost label), `ok` = the developer's recorded OK
    (paid_ok_at), `prefer` = lenses for --prefer-for, `absent` = its program is not installed yet."""
    return {"id": pid, "cost": cost, "ok": ok, "prefer": prefer or [], "absent": absent}


def ids(rnd):
    return [x["id"] for x in (rnd.get("reviewers") or [])]


def row_of(rnd, rid):
    return next((x for x in (rnd.get("reviewers") or []) if x["id"] == rid), {})


def said(r):
    return (r.stdout or "") + (r.stderr or "")


def add_provider(d, env, spec):
    """providers --add for one hand-written command provider, through the public command. The lens preference is
    given as one comma list; when that is refused, as a repeated flag (either form is acceptable). Returns the last
    process."""
    base = ["providers", "--add", spec["id"], "--command", json.dumps([spec["id"], "{prompt}"]),
            "--company", COMPANY[spec["id"]], "--no-check"]
    pref = spec["prefer"]
    if not pref:
        return g.rx(d, *base, env=env)
    r = g.rx(d, *base, "--prefer-for", ",".join(pref), env=env)
    if r.returncode != 0 and len(pref) > 1:
        r = g.rx(d, *base, *[x for lens in pref for x in ("--prefer-for", lens)], env=env)
    return r


class Proj:
    """One throwaway project (a lane) at the adversarial step of a normal-risk step. `specs` are P(...) providers in
    the order they are added; `slots` is the project's review_depth for normal risk. Providers and rules are set
    BEFORE the plan is approved (a later rule change stops every reviewers call)."""

    def __init__(self, tag, specs, slots, home=None):
        b = Path(_tmp.mkdtemp(prefix="ratchet-fk-bin-"))
        for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
            p = shutil.which(n)
            if p:
                (b / n).symlink_to(p)
        self.bin = b
        self.logs = Path(_tmp.mkdtemp(prefix="ratchet-fk-log-"))
        self.home = Path(home) if home else Path(_tmp.mkdtemp(prefix="ratchet-fk-user-"))
        self.rounds = 0
        self.specs = {s["id"]: s for s in specs}
        for s in specs:
            if not s["absent"]:
                self.install(s["id"])
        g.ENV["RATCHET_HOME"] = str(self.home)       # set-up uses test_gates' environment: point it at this folder
        try:
            self.dir = g.project(tag, extra_cp={"risk": "normal"},
                                 config=["review_depth=" + json.dumps({"normal": slots})])
            self.env = dict(g.ENV)
            self.env["PATH"] = str(b)
            self.env["RATCHET_HOST_PROVIDER"] = "anthropic"
            self.env["RATCHET_FAKE_LOGS"] = str(self.logs)
            self.env["RATCHET_HOME"] = str(self.home)
            self.add_failures = []
            for s in specs:
                r = add_provider(self.dir, self.env, s)
                if r.returncode != 0:
                    self.add_failures.append((s["id"], said(r)[-300:]))
            if not self.add_failures:
                self._write_labels()
            g.approve_plan(self.dir)
            g.to_adversarial(self.dir)
        finally:
            g.ENV["RATCHET_HOME"] = ORIG_HOME

    def install(self, pid):
        (self.bin / pid).write_text(FAKE)
        (self.bin / pid).chmod(0o755)

    def _write_labels(self):
        """The cost label and the developer's recorded OK, written straight into the config entries (what lane E's
        flow leaves behind), keeping whatever else `providers --add` stored (such as the lens preference)."""
        cfg_path = self.dir / ".ratchet" / "config.json"
        cfg = json.loads(cfg_path.read_text())
        plist = cfg.get("providers") or []
        for pv in plist:
            s = self.specs.get(pv["id"])
            if s and s["cost"]:
                pv["cost"] = s["cost"]
            if s and s["ok"]:
                pv["paid_ok_at"] = PAID_OK
        r = g.rx(self.dir, "config", "--set", "providers=" + json.dumps(plist), env=self.env)
        if r.returncode != 0:
            self.add_failures.append(("config", said(r)[-300:]))

    def run(self, *args):
        return g.rx(self.dir, *args, env=self.env)

    def out(self, *pids, lenses=LENS_NAMES):
        """The engine's own unavailable-mark for each lens of each provider that is out (reviewer-status --mark)."""
        res = []
        for pid in pids:
            for lens in lenses:
                res.append(self.run("reviewer-status", "--mark", f"{pid}-{lens}", "--reason", "marked out for the test"))
        return res

    def calls(self, tag=None, kind=None):
        p = self.logs / "calls.log"
        lines = p.read_text().splitlines() if p.exists() else []
        return [x for x in lines if (tag is None or x.split()[0] == tag) and (kind is None or x.split()[1] == kind)]

    def open_round(self, nudge=False):
        """`reviewers CP1 --json` (the code is nudged first when asked, so a new round opens rather than the open
        one being shown again). Returns (process, round as printed, or {} when it failed)."""
        if nudge:
            with open(self.dir / "src" / "f.js", "a") as f:
                f.write(f"// nudge {self.rounds}\n")
        self.rounds += 1
        r = self.run("reviewers", "CP1", "--json")
        try:
            rnd = json.loads(r.stdout) if r.returncode == 0 else {}
        except ValueError:
            rnd = {}
        return r, rnd

    def next_round(self):
        return self.open_round(nudge=True)


def ready(p):
    need("every provider was added and labelled through the public commands", not p.add_failures, p.add_failures)


def skipped_text(row):
    """What a reviewer row says about the providers it was chosen instead of."""
    return json.dumps(row.get("fallback_for", [])) + json.dumps(row.get("reasons", {}))


def first_id(rnd):
    return (ids(rnd) or [None])[0]


ONE = [["any-both"]]
TWO = [["any-standards"], ["any-correctness"]]


# ------------------------------------------------------------------------------------------------ cases

@case("CP5-T01")
def cp5_t01_each_provider_reviews_the_lens_it_is_preferred_for():
    got = {}
    for order in ("glm-first", "codex-first"):
        glm = P("opencode-glm", "flat", prefer=["standards"])
        cdx = P("codex", "flat", prefer=["correctness"])
        p = Proj("lane-a" if order == "glm-first" else "lane-b", [glm, cdx] if order == "glm-first" else [cdx, glm], TWO)
        ready(p)
        r, rnd = p.open_round()
        need(f"{order}: the round opens", r.returncode == 0 and len(ids(rnd)) == 2, (said(r)[-300:], ids(rnd)))
        got[order] = {x["lens"] if "lens" in x else x["id"].rsplit("-", 1)[-1]: x["id"] for x in rnd["reviewers"]}
        check(f"{order}: the correctness reviewer is codex", got[order].get("correctness") == "codex-correctness", rnd["reviewers"])
        check(f"{order}: the standards reviewer is opencode-glm", got[order].get("standards") == "opencode-glm-standards",
              rnd["reviewers"])
    check("swapping the order the two providers were added gives the same two reviewers",
          got["glm-first"] == got["codex-first"], got)


@case("CP5-T02")
def cp5_t02_no_preference_rounds_rotate_across_the_working_providers():
    p = Proj("lane-a", [P("prov-one", "flat"), P("prov-two", "flat"), P("prov-three", "flat")], ONE)
    ready(p)
    picked = []
    for n in range(4):
        r, rnd = p.open_round(nudge=n > 0)
        need(f"round {n + 1} opens with one reviewer", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
        picked.append(first_id(rnd))
    check("the first three rounds went to three different providers, not all to prov-one",
          len(set(picked[:3])) == 3 and set(picked[:3]) == {"prov-one-both", "prov-two-both", "prov-three-both"}, picked)
    check("the fourth round goes back to the least recently used provider, the one used first", picked[3] == picked[0], picked)


@case("CP5-T03")
def cp5_t03_a_second_project_on_the_same_machine_continues_the_rotation():
    home = _tmp.mkdtemp(prefix="ratchet-fk-shared-")
    specs = lambda: [P("prov-one", "flat"), P("prov-two", "flat"), P("prov-three", "flat")]       # noqa: E731
    lane_a = Proj("lane-a", specs(), ONE, home=home)
    lane_b = Proj("lane-b", specs(), ONE, home=home)
    alone = Proj("lane-c", specs(), ONE)                       # its own empty RATCHET_HOME
    for p in (lane_a, lane_b, alone):
        ready(p)
    ra, rnd_a = lane_a.open_round()
    need("lane-a's round opens and goes to prov-one", ra.returncode == 0 and first_id(rnd_a) == "prov-one-both",
         (said(ra)[-300:], ids(rnd_a)))
    rb, rnd_b = lane_b.open_round()
    need("lane-b's first round opens", rb.returncode == 0 and len(ids(rnd_b)) == 1, (said(rb)[-300:], ids(rnd_b)))
    check("lane-b's reviewer is not prov-one (lane-a just used it)", first_id(rnd_b) in ("prov-two-both", "prov-three-both"),
          ids(rnd_b))
    rc, rnd_c = alone.open_round()
    need("the lane with its own empty RATCHET_HOME opens a round", rc.returncode == 0 and len(ids(rnd_c)) == 1,
         (said(rc)[-300:], ids(rnd_c)))
    check("with its own empty RATCHET_HOME the same lane would get prov-one, so the spreading comes from the shared record",
          first_id(rnd_c) == "prov-one-both", ids(rnd_c))


@case("CP5-T04")
def cp5_t04_a_flat_provider_is_chosen_before_a_per_use_one_even_when_it_was_used_more():
    p = Proj("lane-a", [P("prov-metered", "per-use", ok=True, absent=True), P("prov-flat", "flat")], ONE)
    ready(p)
    for n in range(2):                          # the per-use program is not installed yet: the flat one reviews twice
        r, rnd = p.open_round(nudge=n > 0)
        need(f"earlier round {n + 1} goes to prov-flat", r.returncode == 0 and first_id(rnd) == "prov-flat-both",
             (said(r)[-300:], ids(rnd)))
    p.install("prov-metered")                   # now the per-use provider (listed first, unused, with the developer's OK) can run
    r, rnd = p.open_round(nudge=True)
    need("the next round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    check("the slot goes to the flat-rate provider", first_id(rnd) == "prov-flat-both", ids(rnd))
    check("the per-use provider was never asked: no review request and no test request",
          p.calls("prov-metered") == [], p.calls())


@case("CP5-T05")
def cp5_t05_a_flat_provider_beats_a_per_use_one_even_when_per_use_is_preferred_for_the_lens():
    p = Proj("lane-a", [P("prov-metered", "per-use", ok=True, prefer=["correctness"]), P("prov-flat", "flat")],
             [["any-correctness"]])
    ready(p)
    r, rnd = p.open_round()
    need("the round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    check("the correctness reviewer is the flat-rate provider", first_id(rnd) == "prov-flat-correctness", ids(rnd))
    check("the per-use provider is not asked", p.calls("prov-metered") == [], p.calls())


@case("CP5-T06")
def cp5_t06_order_of_use_is_flat_then_no_cost_label_then_per_use_with_the_developers_ok():
    p = Proj("lane-a", [P("prov-metered", "per-use", ok=True), P("prov-plain", None), P("prov-flat", "flat")], ONE)
    ready(p)
    picked = []
    r, rnd = p.open_round()
    need("round 1 opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    picked.append(first_id(rnd))
    for res in p.out("prov-flat"):
        need("prov-flat is marked unavailable", res.returncode == 0, said(res)[-300:])
    r, rnd = p.next_round()
    need("round 2 opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    picked.append(first_id(rnd))
    for res in p.out("prov-plain"):
        need("prov-plain is marked unavailable", res.returncode == 0, said(res)[-300:])
    r, rnd = p.next_round()
    need("round 3 opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    picked.append(first_id(rnd))
    check("the three rounds go to flat, then the unlabeled one, then per-use with the OK, in that order",
          picked == ["prov-flat-both", "prov-plain-both", "prov-metered-both"], picked)


@case("CP5-T07")
def cp5_t07_a_per_use_provider_with_no_recorded_ok_is_never_used_even_when_nothing_else_works():
    # with the default any-<lens> slots
    p = Proj("lane-a", [P("prov-metered", "per-use", ok=False)], TWO)
    ready(p)
    r, rnd = p.open_round()
    need("any slots: the round still opens", r.returncode == 0 and len(ids(rnd)) >= 1, (said(r)[-300:], ids(rnd)))
    check("any slots: every reviewer is the host's own", all(i.startswith("host") for i in ids(rnd)), ids(rnd))
    check("any slots: the round says the per-use provider was skipped",
          bool(rnd.get("reviewers")) and all("prov-metered" in skipped_text(x) for x in rnd["reviewers"]), rnd.get("reviewers"))
    check("any slots: the per-use provider was never run, not even for a test request", p.calls("prov-metered") == [], p.calls())
    check("any slots: nothing asks the developer for money by itself", "pay per use" not in said(r).lower(), said(r)[-300:])
    # with an explicit slot list that names this provider's reviewer
    q = Proj("lane-b", [P("prov-metered", "per-use", ok=False)], [["prov-metered-standards"], ["prov-metered-correctness"]])
    ready(q)
    r2, rnd2 = q.open_round()
    need("explicit slots: the round still opens", r2.returncode == 0 and len(ids(rnd2)) >= 1, (said(r2)[-300:], ids(rnd2)))
    check("explicit slots: every reviewer is the host's own", all(i.startswith("host") for i in ids(rnd2)), ids(rnd2))
    check("explicit slots: the round says the per-use provider was skipped",
          bool(rnd2.get("reviewers")) and all("prov-metered" in skipped_text(x) for x in rnd2["reviewers"]), rnd2.get("reviewers"))
    check("explicit slots: the per-use provider was never run, not even for a test request", q.calls("prov-metered") == [], q.calls())
    check("explicit slots: nothing asks the developer for money by itself", "pay per use" not in said(r2).lower(), said(r2)[-300:])


@case("CP5-T08")
def cp5_t08_a_per_use_provider_is_only_used_once_its_recorded_ok_exists():
    without = Proj("lane-a", [P("prov-metered", "per-use", ok=False)], ONE)
    ready(without)
    r, rnd = without.open_round()
    need("without the stamp: the round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    check("without the stamp: the host reviews", first_id(rnd) is not None and first_id(rnd).startswith("host"), ids(rnd))
    check("without the stamp: the provider is not contacted", without.calls("prov-metered") == [], without.calls())
    stamped = Proj("lane-b", [P("prov-metered", "per-use", ok=True)], ONE)
    ready(stamped)
    r2, rnd2 = stamped.open_round()
    need("with the stamp: the round opens", r2.returncode == 0 and len(ids(rnd2)) == 1, (said(r2)[-300:], ids(rnd2)))
    check("with the stamp: the provider reviews the slot", first_id(rnd2) == "prov-metered-both", ids(rnd2))


@case("CP5-T09")
def cp5_t09_an_explicit_slot_list_keeps_the_order_it_was_written_in():
    p = Proj("lane-a", [P("prov-one", "flat"), P("prov-two", None)], [["prov-two-correctness", "prov-one-correctness"]])
    ready(p)
    picked = []
    for n in range(4):                          # the first round, then three more after prov-two has recent reviews
        r, rnd = p.open_round(nudge=n > 0)
        need(f"round {n + 1} opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
        picked.append(first_id(rnd))
    check("every round gives the correctness slot to prov-two, the first one written",
          picked == ["prov-two-correctness"] * 4, picked)


@case("CP5-T10")
def cp5_t10_when_every_provider_in_an_explicit_list_is_out_other_working_providers_come_before_the_host():
    p = Proj("lane-a", [P("prov-one", "flat"), P("prov-two", None), P("prov-three", "flat")], [["prov-one-correctness"]])
    ready(p)
    for res in p.out("prov-one", lenses=("correctness",)):
        need("prov-one-correctness is marked unavailable", res.returncode == 0, said(res)[-300:])
    r, rnd = p.open_round()
    need("the round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
    check("the slot goes to prov-three (flat first), not to prov-two and not to the host",
          first_id(rnd) == "prov-three-correctness", ids(rnd))
    for res in p.out("prov-two", "prov-three", lenses=("correctness",)):
        need("prov-two and prov-three are marked unavailable too", res.returncode == 0, said(res)[-300:])
    r2, rnd2 = p.next_round()
    need("the next round opens", r2.returncode == 0 and len(ids(rnd2)) == 1, (said(r2)[-300:], ids(rnd2)))
    check("with every other-company provider out, the host fills the slot",
          first_id(rnd2) is not None and first_id(rnd2).startswith("host"), ids(rnd2))


@case("CP5-T11")
def cp5_t11_a_preferred_provider_that_is_out_does_not_leave_its_lens_to_the_host():
    p = Proj("lane-a", [P("codex", "flat", prefer=["correctness"]), P("opencode-glm", "flat"), P("opencode-kimi", "flat")],
             TWO)
    ready(p)
    for res in p.out("codex"):
        need("codex is marked unavailable", res.returncode == 0, said(res)[-300:])
    r, rnd = p.open_round()
    need("the round opens with two reviewers", r.returncode == 0 and len(ids(rnd)) == 2, (said(r)[-300:], ids(rnd)))
    corr = next((x for x in rnd["reviewers"] if x["id"].endswith("-correctness")), {})
    check("the correctness reviewer is a working provider, not a host reviewer",
          corr.get("id") in ("opencode-glm-correctness", "opencode-kimi-correctness"), rnd["reviewers"])
    check("no reviewer of the round is a host reviewer", not any(i.startswith("host") for i in ids(rnd)), ids(rnd))
    check("the round says codex was skipped", "codex" in json.dumps(corr.get("fallback_for", [])) + json.dumps(corr.get("reasons", {})),
          corr)


@case("CP5-T12")
def cp5_t12_each_round_still_prefers_a_company_that_is_not_yet_reviewing():
    p = Proj("lane-a", [P("codex", "flat", prefer=["correctness", "standards"]), P("opencode-glm", "flat")], TWO)
    ready(p)
    r, rnd = p.open_round()
    need("the round opens with two reviewers", r.returncode == 0 and len(ids(rnd)) == 2, (said(r)[-300:], ids(rnd)))
    vendors = [x.get("vendor") for x in rnd["reviewers"]]
    check("the two reviewers come from two different companies", len(set(vendors)) == 2, (vendors, ids(rnd)))
    check("codex reviews one lens and the other company's provider the other",
          sorted(i.rsplit("-", 1)[0] for i in ids(rnd)) == ["codex", "opencode-glm"], ids(rnd))


@case("CP5-T13")
def cp5_t13_providers_add_accepts_a_lens_preference_and_refuses_an_unknown_lens():
    p = Proj("lane-a", [], ONE)
    base = ["providers", "--add", "prov-one", "--command", json.dumps(["prov-one", "{prompt}"]), "--company", "acme"]
    p.install("prov-one")
    ok_ = p.run(*base, "--prefer-for", "correctness")
    check("--prefer-for correctness adds the provider", ok_.returncode == 0, said(ok_)[-300:])
    listing = p.run("providers", "--json")
    try:
        rows = json.loads(listing.stdout)
    except ValueError:
        rows = []
    rows = rows if isinstance(rows, list) else (rows.get("providers") or [])
    row = next((x for x in rows if isinstance(x, dict) and x.get("id") == "prov-one"), {})
    pref = [v for k, v in row.items() if "prefer" in k.lower()]
    check("providers --json shows the preference for correctness",
          bool(pref) and "correctness" in json.dumps(pref) and "standards" not in json.dumps(pref), row)
    bad = p.run("providers", "--add", "prov-two", "--command", json.dumps(["prov-one", "{prompt}"]), "--company", "borealis",
                "--prefer-for", "speed")
    check("--prefer-for speed exits non-zero", bad.returncode != 0, (bad.returncode, said(bad)[-300:]))
    msg = said(bad).lower()
    check("the refusal names the allowed lenses", all(x in msg for x in LENS_NAMES) and "speed" in msg, said(bad)[-300:])
    after = p.run("providers", "--json")
    check("nothing was added for the refused lens", "prov-two" not in after.stdout and "prov-one" in after.stdout, after.stdout[-300:])


@case("CP5-T14")
def cp5_t14_the_docs_describe_the_lens_preference_and_the_ordering():
    files = {"README.md": ROOT / "README.md",
             "skills/ratchet-orchestrator/SKILL.md": ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md",
             "skills/ratchet-orchestrator/references/schemas.md":
                 ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"}
    for name, path in files.items():
        text = path.read_text()
        check(f"{name} mentions --prefer-for (or the prefer_for field)", "--prefer-for" in text or "prefer_for" in text, name)
        paras = [re.sub(r"\s+", " ", x).lower() for x in re.split(r"\n\s*\n", text)]
        order = [x for x in paras if "flat" in x and re.search(r"per[- ]use", x) and re.search(r"\b(before|ahead of|first)\b", x)
                 and ("prefer" in x or "order" in x or "rank" in x)]
        check(f"{name} says flat-rate comes before per-use", bool(order), name)
        needs_ok = [x for x in paras if re.search(r"per[- ]use", x) and re.search(r"\bok\b|consent|paid_ok_at|exact", x)]
        check(f"{name} says a per-use provider needs the developer's OK", bool(needs_ok), name)


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


# ------------------------------------------------------------------------------------------------ cases from review round 1

# Fakes that behave differently from FAKE on a review (a probe always passes): a CLI that errors out at once, one that
# says it is out of credits, and one that exits 0 with an answer that is not a review. They replace a provider's
# program after its round is open.
FAKE_ERR = FAKE.replace("printf '%s\\n' \"$reply\"", "if [ \"$kind\" = review ]; then echo 'error: internal server error' >&2; exit 1; fi\nprintf '%s\\n' \"$reply\"")
FAKE_OUT = FAKE.replace("printf '%s\\n' \"$reply\"", "if [ \"$kind\" = review ]; then echo \"You've hit your usage limit. Try again in 3 days.\" >&2; exit 1; fi\nprintf '%s\\n' \"$reply\"")
FAKE_JUNK = FAKE.replace("printf '%s\\n' \"$reply\"", "if [ \"$kind\" = review ]; then echo 'I looked and it seems fine to me.'; exit 0; fi\nprintf '%s\\n' \"$reply\"")
EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
HEALTH = ROOT / "skills" / "ratchet-orchestrator" / "scripts"


def swap_program(p, pid, script):
    (p.bin / pid).write_text(script)
    (p.bin / pid).chmod(0o755)


def run_review(p, rnd, rid):
    """The agent runs one reviewer through external_review.py (the public way a command reviewer runs)."""
    import subprocess
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    return subprocess.run([sys.executable, str(EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out)],
                          cwd=p.dir, capture_output=True, text=True, env=p.env)


def starts_in(p, key):
    """The review starts recorded for the provider `key` in the shared record of this lane's machine."""
    path = p.home / "provider-health.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    return (data.get(key) or {}).get("starts") or []


def start_ids(p, key):
    return sorted(x.get("id") for x in starts_in(p, key) if isinstance(x, dict))


def phealth_module():
    sys.path.insert(0, str(HEALTH))
    import provider_health as ph           # noqa: E402
    return ph


@case("CP5-T15")
def cp5_t15_a_per_use_provider_is_not_picked_or_tested_while_flat_ones_can_take_the_slot():
    p = Proj("lane-a", [P("prov-one", "flat"), P("prov-flat", "flat"), P("prov-metered", "per-use", ok=True)], TWO)
    ready(p)
    r, rnd = p.open_round()
    need("the round opens with two reviewers", r.returncode == 0 and len(ids(rnd)) == 2, (said(r)[-300:], ids(rnd)))
    check("neither reviewer is the per-use provider", not any("prov-metered" in i for i in ids(rnd)), ids(rnd))
    check("the per-use provider was not even sent a test request", p.calls("prov-metered") == [], p.calls())
    q = Proj("lane-b", [P("codex", "flat"), P("prov-metered", "per-use", ok=True)], TWO)
    ready(q)
    r2, rnd2 = q.open_round()
    need("flat codex plus a per-use provider: the round opens", r2.returncode == 0 and len(ids(rnd2)) == 2,
         (said(r2)[-300:], ids(rnd2)))
    check("both slots go to codex", sorted(ids(rnd2)) == ["codex-correctness", "codex-standards"], ids(rnd2))
    check("the per-use provider was never called", q.calls("prov-metered") == [], q.calls())


@case("CP5-T16")
def cp5_t16_unlabeled_and_flat_providers_share_the_first_tier():
    p = Proj("lane-a", [P("codex", None, prefer=["correctness"]), P("opencode-glm", "flat", prefer=["standards"])], TWO)
    ready(p)
    r, rnd = p.open_round()
    need("the round opens with two reviewers", r.returncode == 0 and len(ids(rnd)) == 2, (said(r)[-300:], ids(rnd)))
    check("correctness goes to the unlabeled codex and standards to the flat GLM",
          sorted(ids(rnd)) == ["codex-correctness", "opencode-glm-standards"], ids(rnd))
    q = Proj("lane-b", [P("codex", None), P("opencode-glm", "flat")], ONE)
    ready(q)
    picked = []
    for n in range(4):
        r2, rnd2 = q.open_round(nudge=n > 0)
        need(f"round {n + 1} opens", r2.returncode == 0 and len(ids(rnd2)) == 1, (said(r2)[-300:], ids(rnd2)))
        picked.append(first_id(rnd2))
    check("single-slot rounds rotate between the two instead of all going to the flat one",
          set(picked[:2]) == {"codex-both", "opencode-glm-both"} and picked[0] != picked[1], picked)
    check("with equal use the flat provider goes first", picked[0] == "opencode-glm-both", picked)


@case("CP5-T17")
def cp5_t17_adding_a_provider_again_keeps_its_cost_label_and_a_listed_flat_model_is_labelled_flat():
    p = Proj("lane-a", [P("prov-one", "flat")], ONE)
    ready(p)
    again = p.run("providers", "--add", "prov-one", "--command", json.dumps(["prov-one", "{prompt}"]), "--company", "acme",
                  "--no-check", "--prefer-for", "correctness")
    need("adding prov-one again to set a preference works", again.returncode == 0, said(again)[-300:])
    cfg = json.loads((p.dir / ".ratchet" / "config.json").read_text())
    row = next((x for x in cfg.get("providers") or [] if x.get("id") == "prov-one"), {})
    check("the entry keeps \"cost\": \"flat\"", row.get("cost") == "flat", row)
    check("and has the new preference", row.get("prefer_for") == ["correctness"], row)
    hand = p.run("providers", "--add", "opencode-go-glm", "--preset", "opencode", "--model", "opencode-go/glm-5.3",
                 "--no-check")
    need("a hand-added OpenCode Go model is added", hand.returncode == 0, said(hand)[-300:])
    cfg = json.loads((p.dir / ".ratchet" / "config.json").read_text())
    row = next((x for x in cfg.get("providers") or [] if x.get("id") == "opencode-go-glm"), {})
    check("it is labelled flat-rate like a discovered one", row.get("cost") == "flat" and not row.get("paid_ok_at"), row)


@case("CP5-T18")
def cp5_t18_a_failed_review_gives_back_its_own_start_once_and_never_another_lanes():
    home = _tmp.mkdtemp(prefix="ratchet-fk-shared-")
    lane_a = Proj("lane-a", [P("prov-one", "flat")], ONE, home=home)
    lane_b = Proj("lane-b", [P("prov-one", "flat")], ONE, home=home)
    for q in (lane_a, lane_b):
        ready(q)
    ra, rnd_a = lane_a.open_round()
    rb, rnd_b = lane_b.open_round()
    need("both lanes open a round on prov-one", ra.returncode == 0 and rb.returncode == 0
         and first_id(rnd_a) == first_id(rnd_b) == "prov-one-both", (ids(rnd_a), ids(rnd_b)))
    id_a, id_b = f"{rnd_a['round_nonce']}:prov-one-both", f"{rnd_b['round_nonce']}:prov-one-both"
    check("each round recorded one start with its own id", start_ids(lane_a, "prov-one") == sorted([id_a, id_b]),
          start_ids(lane_a, "prov-one"))
    swap_program(lane_a, "prov-one", FAKE_ERR)
    ext = run_review(lane_a, rnd_a, "prov-one-both")
    need("the review fails and external_review exits 3", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    check("lane-a's own start is given back and lane-b's stays", start_ids(lane_a, "prov-one") == [id_b],
          start_ids(lane_a, "prov-one"))
    run_review(lane_a, rnd_a, "prov-one-both")                  # the agent runs the failing reviewer again
    check("a second failed run gives nothing more back (lane-b's start is still there)",
          start_ids(lane_a, "prov-one") == [id_b], start_ids(lane_a, "prov-one"))


@case("CP5-T19")
def cp5_t19_only_a_provider_that_was_not_really_used_gives_its_start_back():
    for label, script, expect_back in (("an error exit before any answer", FAKE_ERR, True),
                                       ("out of credits", FAKE_OUT, False),
                                       ("an exit 0 with an answer that is no review", FAKE_JUNK, False),
                                       ("the program missing", None, True)):
        p = Proj("lane-" + label.split()[0][:3], [P("prov-one", "flat")], ONE)
        ready(p)
        r, rnd = p.open_round()
        need(f"{label}: the round opens", r.returncode == 0 and first_id(rnd) == "prov-one-both", (said(r)[-300:], ids(rnd)))
        if script is None:
            (p.bin / "prov-one").unlink()
        else:
            swap_program(p, "prov-one", script)
        ext = run_review(p, rnd, "prov-one-both")
        need(f"{label}: the review fails and external_review exits 3", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
        n = len(starts_in(p, "prov-one"))
        check(f"{label}: its start is {'given back' if expect_back else 'kept'}", n == (0 if expect_back else 1),
              (n, said(ext)[-200:]))


@case("CP5-T20")
def cp5_t20_a_slot_refilled_in_the_same_round_starts_the_new_provider_and_gives_back_the_old_one():
    for how in ("failed", "marked-out"):
        p = Proj("lane-" + how[:3], [P("prov-one", "flat"), P("prov-two", "flat")], ONE)
        ready(p)
        r, rnd = p.open_round()
        need(f"{how}: the round opens on prov-one", r.returncode == 0 and first_id(rnd) == "prov-one-both",
             (said(r)[-300:], ids(rnd)))
        if how == "failed":
            swap_program(p, "prov-one", FAKE_ERR)
            ext = run_review(p, rnd, "prov-one-both")
            need("the review fails", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
        else:
            for res in p.out("prov-one"):
                need("prov-one is marked out", res.returncode == 0, said(res)[-300:])
        r2, rnd2 = p.open_round()
        need(f"{how}: the same round is shown with prov-two in the slot",
             r2.returncode == 0 and rnd2.get("round") == rnd.get("round") and ids(rnd2) == ["prov-two-both"],
             (said(r2)[-300:], ids(rnd2)))
        check(f"{how}: prov-one has no start left", start_ids(p, "prov-one") == [], start_ids(p, "prov-one"))
        check(f"{how}: prov-two has exactly the start of the swap", start_ids(p, "prov-two") ==
              [f"{rnd['round_nonce']}:prov-two-both"], start_ids(p, "prov-two"))


@case("CP5-T21")
def cp5_t21_a_start_older_than_six_hours_does_not_count_and_a_refund_is_exact_and_once():
    import datetime as dt
    ph = phealth_module()
    now = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)
    data = {}
    ph.record_start(data, "k", now - dt.timedelta(hours=7), "old:x")
    ph.record_start(data, "k", now - dt.timedelta(hours=1), "n1:x")
    check("a start from 7 hours ago does not count", len(ph.recent_starts(data, "k", now)) == 1, data)
    ph.record_start(data, "k", now, "n2:x")
    check("writing a new start drops the expired one", [s["id"] for s in data["k"]["starts"]] == ["n1:x", "n2:x"], data)
    ph.record_start(data, "k", now, "n2:x")
    check("the same start id is not added twice", len(data["k"]["starts"]) == 2, data)
    ph.refund_start(data, "k", "n2:x")
    check("a refund removes exactly that id", [s["id"] for s in data["k"]["starts"]] == ["n1:x"], data)
    ph.refund_start(data, "k", "n2:x")
    check("a second refund of the same id removes nothing", [s["id"] for s in data["k"]["starts"]] == ["n1:x"], data)
    ph.refund_start(data, "k", "n1:x")
    check("the last refund leaves no empty entry behind", "k" not in data, data)
    # the same through the ranking: recent starts push a provider down, expired ones do not
    results = {}
    for label, hours in (("recent", 1), ("expired", 7)):
        p = Proj("lane-" + label[:3], [P("prov-one", "flat"), P("prov-two", "flat")], ONE)
        ready(p)
        at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
        (p.home / "provider-health.json").write_text(json.dumps({"prov-one": {"starts": [
            {"at": (at + dt.timedelta(seconds=i)).isoformat(), "id": f"old{i}:prov-one-both"} for i in range(3)]}}))
        r, rnd = p.open_round()
        need(f"{label}: the round opens", r.returncode == 0 and len(ids(rnd)) == 1, (said(r)[-300:], ids(rnd)))
        results[label] = first_id(rnd)
    check("three starts an hour ago send the round to prov-two", results["recent"] == "prov-two-both", results)
    check("three starts seven hours ago are forgotten: prov-one (first written) reviews", results["expired"] == "prov-one-both",
          results)


FAKE_RAN = FAKE.replace("printf '%s\\n' \"$reply\"", "if [ \"$kind\" = review ]; then echo 'I read the whole diff but crashed before writing it up.'; exit 1; fi\nprintf '%s\\n' \"$reply\"")
# the first review run is cut off mid-answer (exit 0), every later one fails with an error and no output
FAKE_CUT = FAKE.replace("printf '%s\\n' \"$reply\"", "if [ \"$kind\" = review ]; then here=$(dirname \"$0\"); if [ ! -f \"$here/$me.cut\" ]; then : > \"$here/$me.cut\"; echo '{\"verdict\": \"APPROVE\", \"findings\": [{\"title\"'; exit 0; fi; echo 'error: internal server error' >&2; exit 1; fi\nprintf '%s\\n' \"$reply\"")


@case("CP5-T22")
def cp5_t22_the_other_reviewers_of_a_failed_provider_swapped_out_without_running_get_their_starts_back():
    p = Proj("lane-a", [P("prov-one", "flat"), P("prov-two", "flat")], [["prov-one-standards"], ["prov-one-correctness"]])
    ready(p)
    r, rnd = p.open_round()
    need("the round opens with both slots on prov-one", r.returncode == 0 and sorted(ids(rnd)) ==
         ["prov-one-correctness", "prov-one-standards"], (said(r)[-300:], ids(rnd)))
    nonce = rnd["round_nonce"]
    check("prov-one has a start for each of its two reviewers", start_ids(p, "prov-one") ==
          sorted([f"{nonce}:prov-one-standards", f"{nonce}:prov-one-correctness"]), start_ids(p, "prov-one"))
    swap_program(p, "prov-one", FAKE_ERR)
    ext = run_review(p, rnd, "prov-one-standards")
    need("prov-one-standards fails before answering", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.open_round()
    need("the same round is shown again with both slots on prov-two",
         r2.returncode == 0 and rnd2.get("round") == rnd.get("round") and
         sorted(ids(rnd2)) == ["prov-two-correctness", "prov-two-standards"], (said(r2)[-300:], ids(rnd2)))
    check("prov-one has no start left: the failed reviewer's and the one swapped out without running", start_ids(p, "prov-one") == [],
          start_ids(p, "prov-one"))
    check("prov-two has a start for each reviewer that took a slot", start_ids(p, "prov-two") ==
          sorted([f"{nonce}:prov-two-standards", f"{nonce}:prov-two-correctness"]), start_ids(p, "prov-two"))


@case("CP5-T23")
def cp5_t23_a_start_is_given_back_only_when_no_run_produced_any_output():
    for label, script, expect_back in (("a non-zero exit after a run that printed text", FAKE_RAN, False),
                                       ("a retry that fails after a cut-off first run", FAKE_CUT, False),
                                       ("a non-zero exit with no output at all", FAKE_ERR, True)):
        p = Proj("lane-" + label.split()[-1][:3] + label.split()[0][:2], [P("prov-one", "flat")], ONE)
        ready(p)
        r, rnd = p.open_round()
        need(f"{label}: the round opens", r.returncode == 0 and first_id(rnd) == "prov-one-both", (said(r)[-300:], ids(rnd)))
        swap_program(p, "prov-one", script)
        ext = run_review(p, rnd, "prov-one-both")
        need(f"{label}: the review fails and external_review exits 3", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
        n = len(starts_in(p, "prov-one"))
        check(f"{label}: its start is {'given back' if expect_back else 'kept'}", n == (0 if expect_back else 1),
              (n, said(ext)[-200:]))


if __name__ == "__main__":
    main()
