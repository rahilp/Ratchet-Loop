#!/usr/bin/env python3
"""
CP4 tests: a failed slot goes to another provider.   python3 tests/test_quota_refill.py

When a reviewer fails mid-round, its slot is refilled by a different working provider (for example Kimi) before it
falls back to the host reviewing itself. Only the failing model is sidelined: GLM and Kimi both run through the one
OpenCode program, but GLM failing must not take Kimi down. A program that is not installed still takes down every
reviewer that runs through it. A provider that failed a review stays out for the rest of that round even when its
tiny test request passes, and comes back in the next round. An explicit slot list whose providers are all out tries
other working providers before the host; only when no other-company provider works does the host fill the slot.

Every case builds throwaway git projects (tests/test_gates.py's project(), taken to the adversarial step of a
normal-risk step, neutral names such as "alpha") with their own per-user folder (RATCHET_HOME) and their own private
PATH holding only fake agent CLIs (`opencode`, `codex`, `claude`) plus the few real binaries the engine itself
needs. One fake `opencode` serves both providers (opencode-glm runs opencode-go/glm-5.3, opencode-kimi runs
opencode-go/kimi-k2.6) and decides from the `-m <model>` it was given: a `<tag>.fail` marker beside it (tag
`opencode-glm`, `opencode-kimi`, `codex`, `claude`) fails every call with a plain non-quota error, a
`<tag>.reviewfail` marker fails only a real review (the tiny test request still answers). Each call is logged as
"<tag> probe|review" to $RATCHET_FAKE_LOGS/calls.log, so "which provider really ran" is read from that log alone.
The agent's side is played through the public commands only: `reviewers CP1 --json`, external_review.py,
`reviewer-status` and `providers --check`. No real provider is called, there is no network and no money is spent.

Each case is its own function, registered with @case("CP4-Txx") on the line directly above it and run from main()
inside try/except, so a crash is a failed case, never a silent drop. Writes a per-test JSON report to
RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP4-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, rx, ENV)

EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"

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


# ------------------------------------------------------------------------------------------------ fixtures

# One fake agent CLI for every name. The tag is the program name, plus -glm or -kimi when the -m model says so. It
# logs "<tag> probe|review" first, fails with a plain non-quota error when <tag>.fail sits beside it (or
# <tag>.reviewfail on a real review only), and otherwise answers the connectivity check with RATCHET-PROBE-OK and a
# review request with a review that has no findings. codex writes its answer to --output-last-message as well.
FAKE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
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
case "$model" in *glm*) tag="$me-glm";; *kimi*) tag="$me-kimi";; esac
input=""
if [ ! -t 0 ]; then input=$(cat); fi
kind=review
case "$input $args" in *"connectivity check"*) kind=probe;; esac
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s %s\n' "$tag" "$kind" >> "$RATCHET_FAKE_LOGS/calls.log"; fi
if [ -f "$here/$tag.fail" ]; then echo "error: internal server error" >&2; exit 1; fi
if [ "$kind" = review ] && [ -f "$here/$tag.reviewfail" ]; then echo "error: internal server error" >&2; exit 1; fi
if [ "$kind" = probe ]; then reply="RATCHET-PROBE-OK"; else reply='{"findings": []}'; fi
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""

GLM = {"id": "opencode-glm", "preset": "opencode", "model": "opencode-go/glm-5.3", "cost": "flat"}
KIMI = {"id": "opencode-kimi", "preset": "opencode", "model": "opencode-go/kimi-k2.6", "cost": "flat"}
CODEX = {"id": "codex", "preset": "codex"}
CLAUDE = {"id": "claude", "preset": "claude"}      # the same company as the anthropic host


def ids(rnd):
    return [x["id"] for x in (rnd.get("reviewers") or [])]


def row_of(rnd, rid):
    return next((x for x in (rnd.get("reviewers") or []) if x["id"] == rid), {})


def said(r):
    return (r.stdout or "") + (r.stderr or "")


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


class Proj:
    """One throwaway project at the adversarial step of a normal-risk step, with its own per-user folder and a
    private PATH of fakes. `slots` is the project's review_depth for normal risk."""

    def __init__(self, tag, fakes, providers, slots):
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
        self.rounds = 0
        g.ENV["RATCHET_HOME"] = str(self.home)       # set-up uses test_gates' environment: point it at this folder
        try:
            # providers and rules are set BEFORE the plan is approved (a later rule change stops every reviewers call)
            self.dir = g.project(tag, extra_cp={"risk": "normal"},
                                 config=["providers=" + json.dumps(providers),
                                         "review_depth=" + json.dumps({"normal": slots})])
            g.approve_plan(self.dir)
            g.to_adversarial(self.dir)
            self.env = dict(g.ENV)
        finally:
            g.ENV["RATCHET_HOME"] = ORIG_HOME
        self.env["PATH"] = str(b)
        self.env["RATCHET_HOST_PROVIDER"] = "anthropic"
        self.env["RATCHET_FAKE_LOGS"] = str(self.logs)
        self.env["RATCHET_HOME"] = str(self.home)

    def run(self, *args):
        return g.rx(self.dir, *args, env=self.env)

    def fail(self, tag, mode="fail", on=True):
        """The fake `tag` (opencode-glm, opencode-kimi, codex, claude) starts failing with a plain non-quota error:
        mode "fail" fails every call, "reviewfail" fails real reviews only (its test request still answers)."""
        marker = self.bin / f"{tag}.{mode}"
        if on:
            marker.write_text("x")
        elif marker.exists():
            marker.unlink()

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
        return self.again()

    def again(self):
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


# ------------------------------------------------------------------------------------------------ cases

@case("CP4-T01")
def t01_failed_glm_slot_goes_to_kimi_not_glm_and_not_host():
    p = Proj("alpha", ["opencode"], [GLM, KIMI], [["any-both"]])
    r, rnd = p.open_round()
    need("the round opens with the glm reviewer in its slot", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    p.fail("opencode-glm", "reviewfail")
    ext, _out = p.review(rnd, "opencode-glm-both")
    check("opencode-glm-both fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    check("the same round is shown again", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"), said(r2)[-300:])
    check("the slot is now exactly opencode-kimi-both (not an opencode-glm-* id, not a host reviewer)",
          ids(rnd2) == ["opencode-kimi-both"], ids(rnd2))
    check("fallback_for lists opencode-glm-both", "opencode-glm-both" in (row_of(rnd2, "opencode-kimi-both").get("fallback_for") or []),
          row_of(rnd2, "opencode-kimi-both"))
    kimi, kimi_out = p.review(rnd2, "opencode-kimi-both") if "opencode-kimi-both" in ids(rnd2) else (None, None)
    check("the kimi review runs and exits 0", kimi is not None and kimi.returncode == 0,
          said(kimi)[-300:] if kimi else "kimi was not given the slot")
    check("the kimi review file is written", kimi_out is not None and kimi_out.exists(), kimi_out)
    check("the call log shows a real kimi review", p.calls("opencode-kimi", "review") != [], p.calls())
    out = p.status()
    check("reviewer-status still shows every opencode-kimi-* reviewer available",
          all(listed_available(out, f"opencode-kimi-{lens}") for lens in ("both", "standards", "correctness")), out)
    check("reviewer-status shows the glm reviewer that failed as not available", listed_failed(out, "opencode-glm-both"),
          status_line(out, "opencode-glm-both") or out)


@case("CP4-T02")
def t02_only_the_failing_model_is_sidelined_a_missing_program_takes_down_everything():
    # first project: a plain failure of glm leaves kimi on the same program alone
    p = Proj("bravo", ["opencode", "codex"], [GLM, KIMI, CODEX], [["opencode-glm-both"]])
    r, rnd = p.open_round()
    need("first project: the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    p.fail("opencode-glm", "reviewfail")
    ext, _out = p.review(rnd, "opencode-glm-both")
    check("first project: the glm review fails and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    out = p.status()
    check("first project: no opencode-kimi-* reviewer is marked unavailable",
          all(listed_available(out, f"opencode-kimi-{lens}") for lens in ("both", "standards", "correctness")), out)
    check("first project: the failing glm reviewer is marked unavailable", listed_failed(out, "opencode-glm-both"),
          status_line(out, "opencode-glm-both") or out)
    # second project: the program disappears after the round opened; both models behind it go down
    q = Proj("charlie", ["opencode", "codex"], [GLM, KIMI, CODEX], [["opencode-kimi-both"]])
    r, rnd = q.open_round()
    need("second project: the round opens with opencode-kimi-both", r.returncode == 0 and ids(rnd) == ["opencode-kimi-both"],
         (said(r)[-300:], ids(rnd)))
    hidden = q.bin / "opencode.away"
    (q.bin / "opencode").rename(hidden)
    ext, _out = q.review(rnd, "opencode-kimi-both")
    hidden.rename(q.bin / "opencode")
    check("second project: the review fails and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    out = q.status()
    for rid in ("opencode-glm-both", "opencode-kimi-both"):
        line = status_line(out, rid)
        check(f"second project: {rid} is unavailable with a reason saying the program is not installed",
              listed_failed(out, rid) and "not installed" in line, line or out)
    r2, rnd2 = q.again()
    check("second project: the next reviewers call keeps the round", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"),
          said(r2)[-300:])
    check("second project: the slot goes to codex-both", ids(rnd2) == ["codex-both"], ids(rnd2))


@case("CP4-T03")
def t03_failed_standards_reviewer_is_refilled_by_kimi_and_glm_is_not_reused():
    p = Proj("delta", ["opencode"], [GLM, KIMI], [["any-standards"], ["any-correctness"]])
    r, rnd = p.open_round()
    need("the round opens with glm-standards and kimi-correctness",
         r.returncode == 0 and ids(rnd) == ["opencode-glm-standards", "opencode-kimi-correctness"],
         (said(r)[-300:], ids(rnd)))
    done, done_out = p.review(rnd, "opencode-kimi-correctness")
    need("kimi-correctness has finished and written its review", done.returncode == 0 and done_out.exists(),
         said(done)[-300:])
    before = done_out.read_bytes()
    p.fail("opencode-glm", "reviewfail")
    ext, _out = p.review(rnd, "opencode-glm-standards")
    check("opencode-glm-standards fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    check("the same round is shown again", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"), said(r2)[-300:])
    check("the round is now opencode-kimi-standards and opencode-kimi-correctness",
          sorted(ids(rnd2)) == ["opencode-kimi-correctness", "opencode-kimi-standards"], ids(rnd2))
    check("no reviewer is an opencode-glm-* or a host-* id",
          not any(i.startswith("opencode-glm-") or i.startswith("host") for i in ids(rnd2)), ids(rnd2))
    check("the lenses are still standards and correctness",
          sorted(x.get("lens") for x in rnd2.get("reviewers") or []) == ["correctness", "standards"],
          [x.get("lens") for x in rnd2.get("reviewers") or []])
    check("the standards slot is the one given to opencode-kimi-standards",
          row_of(rnd2, "opencode-kimi-standards").get("slot") == row_of(rnd, "opencode-glm-standards").get("slot"),
          rnd2.get("reviewers"))
    check("the finished kimi-correctness review file is untouched", done_out.exists() and done_out.read_bytes() == before,
          done_out)


@case("CP4-T04")
def t04_provider_that_failed_this_round_stays_out_even_when_its_test_request_passes():
    p = Proj("echo", ["opencode"], [GLM, KIMI], [["any-both"]])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    p.fail("opencode-glm", "reviewfail")          # its tiny test request keeps answering normally
    ext, _out = p.review(rnd, "opencode-glm-both")
    check("opencode-glm-both fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    check("set-up: the slot went to opencode-kimi-both", ids(rnd2) == ["opencode-kimi-both"], ids(rnd2))
    probes_before = len(p.calls("opencode-glm", "probe"))
    chk = p.run("providers", "--check", "all")
    check("providers --check all succeeds", chk.returncode == 0, said(chk)[-300:])
    check("the call log shows the test request for glm was really sent",
          len(p.calls("opencode-glm", "probe")) == probes_before + 1, p.calls())
    r3, rnd3 = p.again()
    check("the same round is shown again", r3.returncode == 0 and rnd3.get("round") == rnd.get("round"), said(r3)[-300:])
    check("the reviewers listed are still opencode-kimi-both, not opencode-glm-both",
          ids(rnd3) == ["opencode-kimi-both"], ids(rnd3))


@case("CP4-T05")
def t05_round_only_exclusion_ends_with_the_round():
    p = Proj("foxtrot", ["opencode"], [GLM, KIMI], [["any-both"]])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    p.fail("opencode-glm", "reviewfail")
    ext, _out = p.review(rnd, "opencode-glm-both")
    check("opencode-glm-both fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    check("set-up: the slot went to opencode-kimi-both", ids(rnd2) == ["opencode-kimi-both"], ids(rnd2))
    chk = p.run("providers", "--check", "all")
    need("set-up: providers --check all succeeds (glm's test request passes and clears a plain bench)",
         chk.returncode == 0, said(chk)[-300:])
    p.fail("opencode-glm", "reviewfail", on=False)
    with open(p.dir / "src" / "f.js", "a") as f:
        f.write("// changed after the review failure\n")
    beh = p.run("behavior", "CP1")
    need("set-up: behavior CP1 passes on the edited code", beh.returncode == 0, said(beh)[-300:])
    r3, rnd3 = p.again()
    check("a new round opens: round 2", r3.returncode == 0 and rnd3.get("round") == 2, (said(r3)[-300:], rnd3.get("round")))
    check("the any-both slot lists opencode-glm-both again (first in config order, working)",
          ids(rnd3) == ["opencode-glm-both"], ids(rnd3))


@case("CP4-T06")
def t06_explicit_slot_with_every_listed_provider_out_tries_other_working_providers_before_the_host():
    p = Proj("golf", ["opencode", "codex"], [GLM, KIMI, CODEX], [["opencode-glm-both"]])
    mark = p.run("reviewer-status", "--mark", "opencode-glm-both", "--reason", "out")
    need("set-up: opencode-glm-both is marked unavailable", mark.returncode == 0, said(mark)[-300:])
    r, rnd = p.open_round()
    check("the round opens", r.returncode == 0, said(r)[-300:])
    check("the slot goes to a working other-company provider with the same lens (kimi or codex)",
          len(ids(rnd)) == 1 and ids(rnd)[0] in ("opencode-kimi-both", "codex-both"), ids(rnd))
    check("no host reviewer is used while a working provider exists", not any(i.startswith("host") for i in ids(rnd)),
          ids(rnd))
    row = rnd.get("reviewers", [{}])[0] if rnd.get("reviewers") else {}
    check("fallback_for contains opencode-glm-both", "opencode-glm-both" in (row.get("fallback_for") or []), row)
    check("reasons has a plain entry for opencode-glm-both",
          isinstance((row.get("reasons") or {}).get("opencode-glm-both"), str)
          and bool(row["reasons"]["opencode-glm-both"].strip()), row.get("reasons"))


@case("CP4-T07")
def t07_only_when_no_other_company_provider_works_the_slot_falls_back_to_the_host():
    p = Proj("hotel", ["opencode", "codex", "claude"], [GLM, KIMI, CODEX, CLAUDE], [["opencode-glm-both"]])
    p.fail("opencode-glm")
    p.fail("opencode-kimi")
    p.fail("codex")
    r, rnd = p.open_round()
    check("the round opens (the run does not stall)", r.returncode == 0, said(r)[-300:])
    check("the reviewer is host-alt-both (not claude-both, not a failed provider)", ids(rnd) == ["host-alt-both"], ids(rnd))
    row = row_of(rnd, "host-alt-both")
    check("fallback_for names the explicit slot's reviewer", "opencode-glm-both" in (row.get("fallback_for") or []), row)
    try:
        state = json.loads((p.dir / ".ratchet" / "runs" / "f" / "state.json").read_text())
        subs = ((state.get("checkpoints") or {}).get("CP1") or {}).get("adv_round", {}).get("substitutions") or []
    except (OSError, ValueError):
        subs = None
    check("the fallback is recorded in the round's substitutions",
          bool(subs) and any(s.get("used") == "host-alt-both" and "opencode-glm-both" in (s.get("instead_of") or [])
                             for s in subs), subs)
    check("no review request reached the claude fake (a same-company provider is never counted as independent)",
          p.calls("claude") == [], p.calls("claude"))


@case("CP4-T08")
def t08_working_explicit_slot_keeps_its_written_order_and_does_not_look_elsewhere():
    p = Proj("india", ["opencode", "codex"], [GLM, KIMI, CODEX], [["opencode-kimi-both", "opencode-glm-both"]])
    r, rnd = p.open_round()
    check("the round opens", r.returncode == 0, said(r)[-300:])
    check("the slot is opencode-kimi-both (the first one written)", ids(rnd) == ["opencode-kimi-both"], ids(rnd))
    row = row_of(rnd, "opencode-kimi-both")
    check("fallback_for is empty", (row.get("fallback_for") or []) == [], row)
    check("codex is not used", not any(i.startswith("codex") for i in ids(rnd)), ids(rnd))


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


@case("CP4-T09")
def t09_failure_is_recorded_in_the_round_before_any_test_request_runs():
    p = Proj("golf", ["opencode"], [GLM, KIMI], [["any-both"]])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    p.fail("opencode-glm", "reviewfail")          # its tiny test request keeps answering normally
    ext, _out = p.review(rnd, "opencode-glm-both")
    check("opencode-glm-both fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    chk = p.run("providers", "--check", "all")    # straight after the failure: no `reviewers` call in between
    check("providers --check all succeeds", chk.returncode == 0, said(chk)[-300:])
    check("the glm test request really passed (it was sent)", len(p.calls("opencode-glm", "probe")) >= 1, p.calls())
    r2, rnd2 = p.again()
    check("the same round is shown again", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"), said(r2)[-300:])
    check("the slot is opencode-kimi-both, not opencode-glm-both", ids(rnd2) == ["opencode-kimi-both"], ids(rnd2))
    p.fail("opencode-glm", "reviewfail", on=False)
    with open(p.dir / "src" / "f.js", "a") as f:
        f.write("// changed after the review failure\n")
    beh = p.run("behavior", "CP1")
    need("set-up: behavior CP1 passes on the edited code", beh.returncode == 0, said(beh)[-300:])
    r3, rnd3 = p.again()
    check("the next round may use opencode-glm-both again",
          r3.returncode == 0 and rnd3.get("round") == 2 and ids(rnd3) == ["opencode-glm-both"],
          (said(r3)[-300:], rnd3.get("round"), ids(rnd3)))


@case("CP4-T10")
def t10_same_round_refill_never_repeats_a_reviewer_another_slot_runs():
    slots = [["any-standards"], ["any-correctness"], ["any-correctness"], ["any-standards"]]
    p = Proj("hotel", ["opencode"], [GLM, KIMI], slots)
    r, rnd = p.open_round()
    need("the round opens with glm and kimi spread over four slots",
         r.returncode == 0 and len(ids(rnd)) == 4 and ids(rnd)[0] == "opencode-glm-standards", (said(r)[-300:], ids(rnd)))
    need("set-up: opencode-kimi-standards holds a slot", "opencode-kimi-standards" in ids(rnd), ids(rnd))
    p.fail("opencode-glm", "reviewfail")
    ext, _out = p.review(rnd, "opencode-glm-standards")
    check("opencode-glm-standards fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    got = ids(rnd2)
    check("the same round is shown again", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"), said(r2)[-300:])
    check("no reviewer id appears twice in the round", len(got) == len(set(got)), got)
    check("opencode-kimi-standards runs exactly once", got.count("opencode-kimi-standards") == 1, got)
    check("no opencode-glm-* reviewer is left in the round", not any(i.startswith("opencode-glm-") for i in got), got)
    check("the freed standards slot went to the host's other-model reviewer, not to a repeat",
          "host-alt-standards" in got, got)
    check("the round still has four slots", len(got) == 4, got)


@case("CP4-T11")
def t11_prompt_too_large_benches_only_that_model():
    p = Proj("india", ["opencode"], [GLM, KIMI], [["opencode-glm-both"]])
    (p.dir / "src" / "big.js").write_text("".join(f"// filler line {i} " + "x" * 60 + "\n" for i in range(3000)))
    beh = p.run("behavior", "CP1")
    need("set-up: behavior CP1 passes with the large file added", beh.returncode == 0, said(beh)[-300:])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    ext, _out = p.review(rnd, "opencode-glm-both")
    check("external_review exits 3 for a prompt too large for one argument", ext.returncode == 3 and "too large" in said(ext),
          (ext.returncode, said(ext)[-300:]))
    check("the CLI was never started", p.calls("opencode-glm", "review") == [], p.calls())
    out = p.status()
    check("opencode-glm-both is marked unavailable", listed_failed(out, "opencode-glm-both"),
          status_line(out, "opencode-glm-both") or out)
    check("every opencode-kimi-* reviewer stays available (only the failing model is benched)",
          all(listed_available(out, f"opencode-kimi-{lens}") for lens in ("both", "standards", "correctness")), out)


@case("CP4-T12")
def t12_a_program_that_cannot_be_started_benches_every_reviewer_on_it():
    p = Proj("juliet", ["opencode", "codex"], [GLM, KIMI, CODEX], [["opencode-kimi-both"]])
    r, rnd = p.open_round()
    need("the round opens with opencode-kimi-both", r.returncode == 0 and ids(rnd) == ["opencode-kimi-both"],
         (said(r)[-300:], ids(rnd)))
    prog = p.bin / "opencode"
    prog.chmod(0o644)                              # still there, but the system refuses to start it
    ext, _out = p.review(rnd, "opencode-kimi-both")
    prog.chmod(0o755)
    check("external_review exits 3", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    out = p.status()
    for rid in ("opencode-glm-both", "opencode-kimi-both"):
        line = status_line(out, rid)
        check(f"{rid} is unavailable with a reason saying the program could not be started",
              listed_failed(out, rid) and "could not be started" in line, line or out)
    check("codex, on another program, stays available", listed_available(out, "codex-both"),
          status_line(out, "codex-both") or out)


@case("CP4-T13")
def t13_replaced_slot_with_nothing_left_never_keeps_its_failed_reviewer():
    p = Proj("kilo", ["opencode"], [GLM, KIMI], [["any-both"], ["any-both"], ["any-both"]])
    r, rnd = p.open_round()
    need("the round opens with glm, kimi and a host reviewer",
         r.returncode == 0 and ids(rnd)[:2] == ["opencode-glm-both", "opencode-kimi-both"] and len(ids(rnd)) == 3,
         (said(r)[-300:], ids(rnd)))
    p.fail("opencode-glm", "reviewfail")
    p.fail("opencode-kimi", "reviewfail")
    for rid in ("opencode-glm-both", "opencode-kimi-both"):
        ext, _out = p.review(rnd, rid)
        check(f"{rid} fails its review and external_review exits 3", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    got = ids(rnd2)
    check("the same round is shown again", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"), said(r2)[-300:])
    check("no failed opencode reviewer stays in the round", not any(i.startswith("opencode-") for i in got), got)
    check("no reviewer id appears twice", len(got) == len(set(got)), got)
    check("the round still has a reviewer in it", len(got) >= 1, got)


@case("CP4-T14")
def t14_failure_is_recorded_even_when_the_state_lock_is_briefly_busy():
    import threading
    p = Proj("lima", ["opencode"], [GLM, KIMI], [["any-both"]])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    p.fail("opencode-glm", "reviewfail")
    lock = p.dir / ".ratchet" / "state.lock"
    lock.write_text(f"{os.getpid()} deadbeefdeadbeef reviewers")        # `reviewers` holds the lock for a few seconds
    t = threading.Timer(5.0, lambda: lock.unlink(missing_ok=True))
    t.start()
    ext, _out = p.review(rnd, "opencode-glm-both")
    t.join()
    check("opencode-glm-both fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    chk = p.run("providers", "--check", "all")
    check("providers --check all succeeds", chk.returncode == 0, said(chk)[-300:])
    r2, rnd2 = p.again()
    check("the same round keeps glm out: the slot is opencode-kimi-both", ids(rnd2) == ["opencode-kimi-both"], ids(rnd2))


@case("CP4-T15")
def t15_refill_gives_the_slot_to_an_idle_working_provider_not_the_host():
    p = Proj("mike", ["opencode", "codex"], [GLM, KIMI, CODEX], [["any-both"], ["any-both"]])
    r, rnd = p.open_round()
    need("the round opens with glm-both and kimi-both",
         r.returncode == 0 and ids(rnd) == ["opencode-glm-both", "opencode-kimi-both"], (said(r)[-300:], ids(rnd)))
    p.fail("opencode-kimi", "reviewfail")
    ext, _out = p.review(rnd, "opencode-kimi-both")
    check("opencode-kimi-both fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    check("the same round is shown again", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"), said(r2)[-300:])
    check("the freed slot goes to codex-both, not to a host reviewer", ids(rnd2) == ["opencode-glm-both", "codex-both"],
          ids(rnd2))


@case("CP4-T16")
def t16_high_risk_shape_refill_uses_a_free_provider_before_host_alt():
    slots = [["any-standards"], ["any-correctness"], ["any-correctness"], ["any-standards"]]
    p = Proj("november", ["opencode", "codex"], [GLM, KIMI, CODEX], slots)
    r, rnd = p.open_round()
    need("the round opens with codex-correctness in a correctness slot",
         r.returncode == 0 and len(ids(rnd)) == 4 and "codex-correctness" in ids(rnd), (said(r)[-300:], ids(rnd)))
    p.fail("codex", "reviewfail")
    ext, _out = p.review(rnd, "codex-correctness")
    check("codex-correctness fails its review and external_review exits 3", ext.returncode == 3,
          (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    got = ids(rnd2)
    check("the same round is shown again", r2.returncode == 0 and rnd2.get("round") == rnd.get("round"), said(r2)[-300:])
    check("no codex reviewer is left in the round", not any(i.startswith("codex") for i in got), got)
    check("the freed slot went to a free working provider, not host-alt-correctness",
          "host-alt-correctness" not in got and not any(i.startswith("host") for i in got), got)
    check("no reviewer id appears twice", len(got) == len(set(got)), got)


if __name__ == "__main__":
    main()
