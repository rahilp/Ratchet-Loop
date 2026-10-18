#!/usr/bin/env python3
"""
CP5 tests: fix rounds get one reviewer.   python3 tests/test_fix_round.py

After reviewers BLOCK a step and the fixer has run, the next review round asks exactly ONE reviewer instead of
the step's whole set: the reviewer whose blocking finding is being fixed (same provider, same lens), preferring a
company other than the host's when several blocked, using that slot's own existing stand-in when it is not
available, and never a provider that had not reviewed the step. There is no size and no risk condition. The round
is marked with lane B's `small_fix` flag plus a `kind` that says it follows a block (lane B's own post-approval
small fix keeps `small_fix` with a kind that says it follows an approval). A fix round still counts toward the
step's and the run's review budgets, still goes to the usual check-in and round limits, and its one-reviewer
approval never becomes the full-review baseline a later small fix is measured against. Status, the commit message
and the four shipped docs name the rule and each fix round's own reviewer.

Black-box: every case drives the real `ratchet.py` CLI in a throwaway one-checkpoint git project built with
tests/test_gates.py's helpers (`project`, `approve_plan`, `to_adversarial`, `write_review`, `rx`, `git`, `say`),
the way tests/test_limits.py and tests/test_quick_path_small.py do. Host reviews are written into the round's own
folder through the real guard hook (`g.write_review`); other-company reviews are produced by
skills/ratchet-adversarial-loop/scripts/external_review.py against a fake agent CLI this file writes on a private
PATH (tests/test_providers.py's approach), so no real agent CLI is ever launched and tests/run_all.py is never
run. The engine always sees an idle machine (RATCHET_FAKE_LOADAVG=0), and every case that uses a fake external
CLI gets its own private RATCHET_HOME, because the machine-wide provider share record (3 review starts per
provider per 15 minutes) lives there and would otherwise quietly push a provider's slot to the host.

Requirement-to-check map (planned case -> what proves it; no case leans on another case's proof):
  CP5-T01  round 1 (normal, 2 reviewers) blocked by correctness only, 40-line fix: round 2 names exactly that one
           reviewer, round == 2, small_fix true, kind says after a block, and the plain `reviewers` text says
           "fix round" and "one reviewer" and names the reviewer.
  CP5-T02  high risk (round 1 = 4 reviewers) blocked by one, 60-line fix: round 2 names exactly that reviewer,
           small_fix true, kind after a block.
  CP5-T03  codex-standards (other company) blocks, host-correctness clean: round 2 is exactly codex-standards,
           never host-correctness, never a *-both pick.
  CP5-T04  round 1 names 2 (normal) and 4 (high) reviewers, small_fix not set, no fix-round wording; a round 1
           re-opened after another edit before any review is still the full set and attempts/run total unchanged.
  CP5-T05  both reviewers blocked, every finding rebutted, no code change: next round small_fix not set, no
           after-a-block kind, and it still opens (today's rules).
  CP5-T06  clean approval then a 30-line edit: the full two-reviewer set, small_fix not set.
  CP5-T07  clean approval then a 5-line edit: one reviewer, small_fix true, kind says after approval (not block).
  CP5-T08  blocked round the user accepted (`accept:` reply) then a 30-line edit: full set, small_fix not set.
  CP5-T09  three projects, codex-standards blocked and fixed, review_depth.low default / two slots / single-lens:
           all three name exactly codex-standards with small_fix true.
  CP5-T10  a step pinning its own two reviewers, both blocked and fixed: round 2 names both pinned ids, small_fix
           not set.
  CP5-T11  attempts and adv_rounds_total each rise by exactly 1 when the fix round opens; a replacement before
           any review is written adds nothing to either.
  CP5-T12  max_adversarial_rounds=2: the fix round blocks again, `reviewers` is refused, attempts stays 2, no
           adv-round-03 folder, and the step is blocked.
  CP5-T13  max_adversarial_rounds_run=2: refused with the run-wide cap message, adv_rounds_total stays 2.
  CP5-T14  raise-limit grants exactly one more round, it is a one-reviewer fix round, attempts == cap_raised_to,
           it blocks again and a further round is refused.
  CP5-T15  checkin_round=2: a blocking fix round opens the check-in, `reviewers` is refused naming it and opens
           no round 3; after `check-in --decision simplify` round 3 opens as a one-reviewer fix round, attempts 3.
  CP5-T16  round 2 (fix round) blocks, the fixer runs: round 3 names one reviewer, the same one, kind after a
           block (no return to the full set).
  CP5-T17  the stale `complete` is refused; the fix round's single clean review passes the gate; `complete` then
           runs the regression command, fails naming it (its log holds the command's own marker line), and
           completes once the command passes.
  CP5-T18  rounds 2 and 3 are fix rounds (blocked, then approved): status --json, plain status and the commit
           message each name "round 2"/"round 3" with its reviewer, "fix round" and the block; "round 1" is
           never called a fix round.
  CP5-T19  a fix round recorded with a blocker, nothing approved: status --json already names round 2, its
           reviewer, "fix round" and the block; round 1 is not named as one.
  CP5-T20  fix round approved, then a 3-line edit: the full two-reviewer set, small_fix not set, and
           last_full_review_fingerprint is exactly what it was before the fix round.
  CP5-T21  a fix round replaced before any review: one reviewer, small_fix true, kind after a block, and
           attempts and adv_rounds_total are the same numbers as before the replacement.
  CP5-T22  codex-standards benched mid-round: the same round number, exactly one reviewer, lens standards, not
           codex-standards, small_fix still true; status and the commit name the stand-in, not codex-standards.
  CP5-T23  README.md, skills/ratchet-adversarial-loop/SKILL.md, skills/ratchet-orchestrator/SKILL.md and
           skills/ratchet-orchestrator/references/schemas.md each state the rule, the selection, the stand-in and
           the limit/check-in rules; schemas.md names small_fix and kind; README's "Later rounds cost less"
           paragraph covers both the small fix and the fix round.
  CP5-T24  host-correctness listed first and codex-standards second, both blocked: round 2 is codex-standards.
  CP5-T25  project A [codex-standards, gemini-correctness] -> codex-standards; project B reversed ->
           gemini-correctness; a second `reviewers --json` call names the same reviewer and the same round.
  CP5-T26  codex-standards benched before the round opens: one reviewer, lens standards, not codex-standards,
           not a *-both pick, the row shows it stood in for codex-standards with the bench reason, and
           codex-standards' bench entry (reason, until) is byte-for-byte what it was; and again when the share
           limit, not a bench, refuses codex.
  CP5-T27  (a) gemini configured but never used: round 2 is codex-standards and no gemini id; (b) codex added
           after a host-only blocked round: round 2 is host-correctness and no codex/gemini id; (c) a finding
           naming an id the blocked round never listed is never chosen.
  CP5-T28  high risk, codex-correctness the only blocker: round 2 is exactly codex-correctness, no host-* id.
  CP5-T29  codex-standards rebutted, host-correctness fixed: round 2 is exactly host-correctness.
Extra (not planned) cases kept from the reviewed candidate: CP5-F15 (both a blocked and an approved fix round
named, by round and reviewer, in status --json, plain status and the commit message, and told apart) and CP5-F16
(after a mid-round stand-in, that evidence names the reviewer that actually ran and never the benched one).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, write_review, rx, git, say)
import test_providers as tp     # noqa: E402  (EXT: external_review.py)

# Every engine call in this file sees an idle machine, so CP1/CP2's load-stretched limits never change what a
# case measures. g.ENV is the env test_gates.py's own helpers pass, so this covers them too.
g.ENV["RATCHET_FAKE_LOADAVG"] = "0"

PLANNED = [f"CP5-T{i:02d}" for i in range(1, 30)]
PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP5-[FT]\d+)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'OK ' if cond else 'FAIL'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

def _out(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


def _finding(fid, severity="blocker", file="src/f.js"):
    return {"id": fid, "severity": severity, "file": file,
            "description": "the new path drops the edit the developer just made",
            "user_impact": "a developer loses the change they just made"}


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _cps(d):
    return _state(d)["checkpoints"]["CP1"]


def _gate(d):
    return _cps(d)["gates"]["adversarial"]


def _counters(d):
    """(this step's own adversarial attempts, the run-wide adversarial round total)."""
    st = _state(d)
    return (st["checkpoints"]["CP1"]["gates"]["adversarial"].get("attempts"),
            int(st.get("adv_rounds_total", 0)))


def _ids(rnd):
    return [rv["id"] for rv in rnd["reviewers"]]


def _lens(rid):
    return rid.rsplit("-", 1)[-1]


def _kind(rnd):
    """The round's kind value: which of the two one-reviewer rules opened it ('after a block' for this step's
    rule, 'after approval' for lane B's post-approval small fix). Read from the field the plan names (`kind`),
    accepting `small_fix_kind` as the same field under a longer name."""
    return str((rnd or {}).get("kind") or (rnd or {}).get("small_fix_kind") or "")


def _after_block(rnd):
    k = _kind(rnd).lower()
    return "block" in k and "approval" not in k


def _after_approval(rnd):
    k = _kind(rnd).lower()
    return "approval" in k and "block" not in k


def _reviewers(d, *extra, env=None):
    return g.rx(d, "reviewers", "CP1", *extra, env=env)


def _open(d, env=None):
    """Opens (or re-reads) the checkpoint's adversarial round and returns `reviewers --json`. Never auto-triages:
    every case decides fix or rebut itself."""
    r = _reviewers(d, "--json", env=env)
    assert r.returncode == 0, _out(r)
    return json.loads(r.stdout)


def _write_code(d, lines, tag, env=None):
    """The fixer's change: src/f.js rewritten so `lines` lines differ (f() still returns 1), then `behavior`."""
    tag = re.sub(r"[^a-z0-9_]", "_", tag.lower())
    body = "".join(f"export const pad_{tag}_{i} = {i};\n" for i in range(max(lines - 1, 0)))
    (Path(d) / "src/f.js").write_text(body + f"export const f = () => 1; // {tag}\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, _out(r)


def _append_lines(path: Path, n: int, tag: str):
    """Appends exactly `n` line comments, so the measured code-lines-changed since an earlier tree is exactly the
    sum of what was appended since then (tests/test_quick_path_small.py's own CP3 fixture pattern)."""
    with open(path, "a") as f:
        for i in range(n):
            f.write(f"// filler-{tag}-{i}\n")


def _record(d, rnd, paths, env=None):
    return g.rx(d, "adv-record", "CP1", "--reviews", *paths, env=env)


def _block(d, rnd, by, decision="fix", env=None):
    """Records `rnd` with reviewer id -> findings from `by` (every other reviewer approves clean), checks it
    blocked, and triages every finding `decision`. Returns the finding keys it triaged."""
    paths = [g.write_review(d, rnd, rv["id"], by.get(rv["id"], [])) for rv in rnd["reviewers"]]
    r = _record(d, rnd, paths, env=env)
    assert r.returncode == 2, _out(r)
    keys = [f"{rid}/{f['id']}" for rid, fs in by.items() for f in fs]
    _triage(d, keys, decision, env=env)
    return keys


def _triage(d, keys, decision="fix", env=None):
    for k in keys:
        t = g.rx(d, "triage", "CP1", f'{k}={decision}:"a developer really hits this"', env=env)
        assert t.returncode == 0, _out(t)


def _approve(d, rnd, env=None):
    paths = [g.write_review(d, rnd, rv["id"], []) for rv in rnd["reviewers"]]
    r = _record(d, rnd, paths, env=env)
    assert r.returncode == 0, _out(r)
    return r


def _start(name, risk="normal", config=None, extra_cp=None):
    """A throwaway one-checkpoint project at `risk`, driven to the adversarial gate. min_review_seconds=0 and
    providers=[] come from g.project: no reviewer CLI is ever launched for these."""
    extra = {"risk": risk}
    if extra_cp:
        extra.update(extra_cp)
    d = g.project(name, extra_cp=extra, config=config or [])
    g.approve_plan(d)
    g.to_adversarial(d)
    return d


def _pick(rnd, lens):
    return next(rv["id"] for rv in rnd["reviewers"] if _lens(rv["id"]) == lens)


def _json_strings(obj):
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _json_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _json_strings(v)]
    return []


def _any_says(strings, *tokens):
    """True when one of `strings` contains every token (case-insensitive): one sentence saying all of it, so a
    round number from one line and a reviewer id from another never count as naming that round's reviewer."""
    return any(all(t.lower() in s.lower() for t in tokens) for s in strings)


def _text_says(text, *tokens):
    return _any_says((text or "").splitlines(), *tokens)


def _status_json(d, env=None):
    r = g.rx(d, "status", "--json", env=env)
    assert r.returncode == 0, _out(r)
    return _json_strings(json.loads(r.stdout))


def _status_text(d, env=None):
    return g.rx(d, "status", env=env).stdout or ""


def _commit_message(d):
    return g.git(d, "log", "-1", "--format=%B").stdout or ""


def _check_fix_round(case, d, rnd, reviewer, what):
    """The contract of a post-block fix round: exactly one reviewer, that reviewer, `small_fix` true, and a kind
    that says it follows a block rather than an approval -- in `reviewers --json` and in the saved round."""
    check(f"[{case}] {what}: exactly one reviewer is named", len(rnd["reviewers"]) == 1, _ids(rnd))
    check(f"[{case}] {what}: the one reviewer is {reviewer}, whose blocking finding was fixed",
          _ids(rnd) == [reviewer], _ids(rnd))
    check(f"[{case}] {what}: --json marks it small_fix", rnd.get("small_fix") is True, rnd.get("small_fix"))
    check(f"[{case}] {what}: its kind says it follows a block, not an approval", _after_block(rnd),
          _kind(rnd) or "no kind field")
    adv = _cps(d).get("adv_round") or {}
    check(f"[{case}] {what}: the saved round records the one reviewer and small_fix",
          adv.get("reviewers") == [reviewer] and adv.get("small_fix") is True,
          (adv.get("reviewers"), adv.get("small_fix")))


def _check_not_fix_round(case, rnd, what):
    check(f"[{case}] {what}: small_fix is not set", rnd.get("small_fix") is not True, rnd.get("small_fix"))
    check(f"[{case}] {what}: no kind saying it follows a block", not _after_block(rnd),
          _kind(rnd) or "no kind field")


# --- fake other-company agent CLIs, each on its own private PATH, with its own private RATCHET_HOME -----------

# The reply is chosen by the reviewer id the prompt names ("Reviewer id: codex-standards"), never by its "Lens:"
# line: the shipped adversary instructions describe both lenses, so the prompt always holds both lens names.
_FAKE = r"""#!/bin/sh
# fake agent CLI: answers the connectivity probe, otherwise a fixed reply per reviewer id. Never a real CLI.
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in
  *"connectivity check"*) reply="RATCHET-PROBE-OK";;
  *"Reviewer id: __NAME__-correctness"*) reply='__CORRECTNESS__';;
  *"Reviewer id: __NAME__-standards"*) reply='__STANDARDS__';;
  *) reply='{"findings": []}';;
esac
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""

_NO_FINDINGS = '{"findings": []}'


def _fake_clis(specs):
    """One private folder holding a fake agent CLI per entry of `specs` ({name: (standards reply, correctness
    reply)}), plus the few real tools the engine shells out to, so PATH can be exactly this folder."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-cp5-bin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    for name, (std, corr) in specs.items():
        script = (_FAKE.replace("__STANDARDS__", std.replace("'", "'\\''"))
                       .replace("__CORRECTNESS__", corr.replace("'", "'\\''"))
                       .replace("__NAME__", name))
        (b / name).write_text(script)
        (b / name).chmod(0o755)
    return b


@contextlib.contextmanager
def _own_home():
    """Points test_gates.py's shared RATCHET_HOME at a brand-new empty folder for one case. The machine-wide
    provider share record (3 review starts per provider per 15 minutes, lane G) lives under RATCHET_HOME, so
    without this a later case's provider would be refused its slot because of an earlier case's starts. The
    project is created inside the block too: its installed hooks bake in the RATCHET_HOME that was active at
    `init`. Restored on exit."""
    old = g.ENV.get("RATCHET_HOME")
    home = Path(_tmp.mkdtemp(prefix="ratchet-cp5-home-"))
    g.ENV["RATCHET_HOME"] = str(home)
    try:
        yield home
    finally:
        if old is None:
            g.ENV.pop("RATCHET_HOME", None)
        else:
            g.ENV["RATCHET_HOME"] = old


def _ext_project(name, depth, provs, risk="normal", config=(), replies=None):
    """A project with other-company providers registered against fake CLIs and `review_depth` set to `depth`
    (so exactly which reviewer sits in which slot is known). `provs` is a list of (provider id, company, model);
    `replies` maps provider id -> (standards reply, correctness reply). Returns (project dir, env).
    provider_share.max is set high as a second guard beside this case's own RATCHET_HOME."""
    replies = replies or {}
    b = _fake_clis({pid: replies.get(pid, (_NO_FINDINGS, _NO_FINDINGS)) for pid, _c, _m in provs})
    env = dict(g.ENV, PATH=str(b))
    d = g.project(name, extra_cp={"risk": risk},
                  config=["review_depth=" + json.dumps(depth), "provider_share.max=9", *config])
    for pid, company, model in provs:
        r = g.rx(d, "providers", "--add", pid, "--command", json.dumps([str(b / pid)]), "--stdin",
                 "--company", company, "--model", model, env=env)
        assert r.returncode == 0, _out(r)
    g.approve_plan(d)
    g.to_adversarial(d)
    return d, env


def _ext_review(d, rnd, rid, env):
    """Runs the real external_review.py for one command-kind reviewer, so its review is signed the way a real
    other-company review is (tests/test_providers.py / lane B's CP3-X02 approach)."""
    row = next(rv for rv in rnd["reviewers"] if rv["id"] == rid)
    p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", rid, "--cp", "CP1", "--out", row["output"]],
                       cwd=d, capture_output=True, text=True, env=env)
    assert p.returncode == 0, _out(p)
    return row["output"]


def _ext_record(d, rnd, env, host_findings=None, expect=2):
    """Records a round whose reviewers are a mix of command-kind (reviewed through external_review.py, findings
    coming from the fake CLI's own reply) and host sub-agents (written into the round folder)."""
    host_findings = host_findings or {}
    paths = []
    for rv in rnd["reviewers"]:
        if rv.get("kind") == "command":
            paths.append(_ext_review(d, rnd, rv["id"], env))
        else:
            paths.append(g.write_review(d, rnd, rv["id"], host_findings.get(rv["id"], [])))
    r = _record(d, rnd, paths, env=env)
    assert r.returncode == expect, _out(r)
    return r


# A tiny regression command this file fully controls: it fails until its marker file exists, and always writes a
# line of its own into the log, so a case can prove the suite really ran. Never tests/run_all.py.
_CMD_DIR = Path(_tmp.mkdtemp(prefix="ratchet-cp5-cmd-"))
_SUITE = _CMD_DIR / "suite.py"
_SUITE.write_text("import os, sys\n"
                  "print('cp5-suite-ran')\n"
                  "sys.exit(0 if os.path.exists(sys.argv[1]) else 1)\n")


def _suite_command(marker: Path) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(_SUITE))} {shlex.quote(str(marker))}"


# ------------------------------------------------------------------------------------------------ CP5-T01..T29

def t01_one_reviewer_after_a_block_and_a_fix():
    d = _start("c01")
    rnd1 = _open(d)
    check("[CP5-T01] (fixture) round 1 of a normal-risk step names two reviewers", len(rnd1["reviewers"]) == 2,
          _ids(rnd1))
    rid = _pick(rnd1, "correctness")
    _block(d, rnd1, {rid: [_finding("C1")]})
    _write_code(d, 40, "c01")
    rnd2 = _open(d)
    check("[CP5-T01] the round after the block is round 2", rnd2.get("round") == 2, rnd2.get("round"))
    _check_fix_round("CP5-T01", d, rnd2, rid, "normal risk, a 40-line fix")
    txt = _reviewers(d).stdout or ""
    check("[CP5-T01] the plain `reviewers` text calls it a fix round with one reviewer and names the reviewer",
          "fix round" in txt.lower() and "one reviewer" in txt.lower() and rid in txt, txt[:600])


def t02_one_reviewer_in_a_high_risk_step_and_a_big_fix():
    d = _start("c02", risk="high")
    rnd1 = _open(d)
    check("[CP5-T02] (fixture) round 1 of a high-risk step names four reviewers", len(rnd1["reviewers"]) == 4,
          _ids(rnd1))
    rid = rnd1["reviewers"][-1]["id"]
    _block(d, rnd1, {rid: [_finding("H1")]})
    _write_code(d, 60, "c02")
    _check_fix_round("CP5-T02", d, _open(d), rid, "high risk, a 60-line fix")


def t03_the_outside_reviewer_rechecks_its_own_finding():
    with _own_home():
        d, env = _ext_project("c03", {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5")],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        check("[CP5-T03] (fixture) round 1 is codex-standards and host-correctness",
              sorted(_ids(rnd1)) == ["codex-standards", "host-correctness"], _ids(rnd1))
        _ext_record(d, rnd1, env)
        _triage(d, ["codex-standards/S1"], env=env)
        _write_code(d, 30, "c03", env=env)
        rnd2 = _open(d, env=env)
        _check_fix_round("CP5-T03", d, rnd2, "codex-standards", "the outside reviewer's own finding")
        check("[CP5-T03] the host is not asked to re-check it, and it is no catch-all both-lens pick",
              "host-correctness" not in _ids(rnd2) and not any(i.endswith("-both") for i in _ids(rnd2)),
              _ids(rnd2))


def t04_a_first_round_keeps_the_full_set():
    for risk, want in (("normal", 2), ("high", 4)):
        d = _start(f"c04{risk}", risk=risk)
        rnd1 = _open(d)
        check(f"[CP5-T04] {risk} risk: round 1 names its full set of {want} reviewers",
              len(rnd1["reviewers"]) == want, _ids(rnd1))
        _check_not_fix_round("CP5-T04", rnd1, f"{risk} risk round 1")
        check(f"[CP5-T04] {risk} risk: round 1's text has no fix-round wording",
              "fix round" not in (_reviewers(d).stdout or "").lower(), (_reviewers(d).stdout or "")[:400])
    # a round 1 replaced before anyone reviewed it: still the full set, and it costs no extra attempt
    d = _start("c04r")
    rnd1 = _open(d)
    before = _counters(d)
    _write_code(d, 25, "c04r")
    rnd2 = _open(d)
    check("[CP5-T04] a round re-opened before any review was written is still the full two-reviewer set",
          sorted(_ids(rnd2)) == sorted(_ids(rnd1)), _ids(rnd2))
    _check_not_fix_round("CP5-T04", rnd2, "a round replaced before review")
    check("[CP5-T04] that replacement costs no extra attempt and no extra run-wide round",
          _counters(d) == before, (before, _counters(d)))


def t05_a_block_with_no_fix_is_not_a_fix_round():
    d = _start("c05", config=["checkin_round=99"])
    rnd1 = _open(d)
    std, corr = _pick(rnd1, "standards"), _pick(rnd1, "correctness")
    _block(d, rnd1, {std: [_finding("S1")], corr: [_finding("C1")]}, decision="rebut")
    rnd2 = _open(d)
    check("[CP5-T05] a round still opens after the rebuttals", rnd2.get("round") == 2, rnd2.get("round"))
    _check_not_fix_round("CP5-T05", rnd2, "a blocked round with every finding rebutted and no code change")


def t06_a_round_after_a_clean_approval_is_not_a_post_block_fix_round():
    d = _start("c06")
    rnd1 = _open(d)
    _approve(d, rnd1)
    _write_code(d, 30, "c06")
    rnd2 = _open(d)
    check("[CP5-T06] after a clean approval a 30-line edit opens the full two-reviewer set",
          sorted(_ids(rnd2)) == sorted(_ids(rnd1)) and len(rnd2["reviewers"]) == 2, _ids(rnd2))
    _check_not_fix_round("CP5-T06", rnd2, "a 30-line edit after a clean approval")


def t07_lane_bs_small_fix_after_approval_is_labelled_after_approval():
    d = _start("c07")
    rnd1 = _open(d)
    _approve(d, rnd1)
    _append_lines(Path(d) / "src/f.js", 5, "c07")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = _open(d)
    check("[CP5-T07] a 5-line fix after approval still opens with exactly one reviewer",
          len(rnd2["reviewers"]) == 1, _ids(rnd2))
    check("[CP5-T07] it is still marked small_fix", rnd2.get("small_fix") is True, rnd2.get("small_fix"))
    check("[CP5-T07] its kind says it follows an approval, not a block", _after_approval(rnd2),
          _kind(rnd2) or "no kind field")


def t08_an_accepted_finding_is_not_a_blocked_round():
    d = _start("c08")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    paths = [g.write_review(d, rnd1, rv["id"], [_finding("S1")] if rv["id"] == rid else [])
             for rv in rnd1["reviewers"]]
    r = _record(d, rnd1, paths)
    assert r.returncode == 2, _out(r)
    a = g.rx(d, "ask", "accept-finding", "CP1", "S1")
    assert a.returncode == 0, _out(a)
    g.say(d, "accept: no real developer can reach this path in our product")
    r = g.rx(d, "accept-finding", "CP1")
    assert r.returncode == 0, _out(r)
    check("[CP5-T08] (fixture) the gate passed on the user's acceptance",
          _gate(d).get("status") == "passed", _gate(d).get("status"))
    _write_code(d, 30, "c08")
    rnd2 = _open(d)
    check("[CP5-T08] the next round names the step's full two-reviewer set",
          sorted(_ids(rnd2)) == sorted(_ids(rnd1)) and len(rnd2["reviewers"]) == 2, _ids(rnd2))
    _check_not_fix_round("CP5-T08", rnd2, "a code change after an accepted finding")


def t09_the_low_risk_setting_is_never_consulted():
    variants = [("default", None),
                ("two slots", ["any-both", "any-both"]),
                ("a single-lens id", ["host-standards"])]
    for label, low in variants:
        with _own_home():
            depth = {"normal": ["codex-standards", "host-correctness"]}
            if low is not None:
                depth["low"] = low
            d, env = _ext_project("c09", depth, [("codex", "openai", "gpt-5")],
                                  replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
            rnd1 = _open(d, env=env)
            _ext_record(d, rnd1, env)
            _triage(d, ["codex-standards/S1"], env=env)
            _write_code(d, 30, "c09", env=env)
            rnd2 = _open(d, env=env)
            check(f"[CP5-T09] review_depth.low = {label}: round 2 names exactly codex-standards",
                  _ids(rnd2) == ["codex-standards"], _ids(rnd2))
            check(f"[CP5-T09] review_depth.low = {label}: it is marked small_fix and follows a block",
                  rnd2.get("small_fix") is True and _after_block(rnd2),
                  (rnd2.get("small_fix"), _kind(rnd2) or "no kind field"))


def t10_a_pinned_reviewer_list_is_retained():
    pinned = ["host-standards", "host-correctness"]
    d = _start("c10", config=["checkin_round=99"], extra_cp={"reviewers": pinned})
    rnd1 = _open(d)
    check("[CP5-T10] (fixture) round 1 names the step's own pinned pair", sorted(_ids(rnd1)) == sorted(pinned),
          _ids(rnd1))
    _block(d, rnd1, {"host-standards": [_finding("S1")], "host-correctness": [_finding("C1")]})
    _write_code(d, 30, "c10")
    rnd2 = _open(d)
    check("[CP5-T10] after a block and a fix the pinned set is kept, both reviewers",
          sorted(_ids(rnd2)) == sorted(pinned), _ids(rnd2))
    _check_not_fix_round("CP5-T10", rnd2, "a step that pins its own reviewer list")


def t11_a_fix_round_costs_a_round_like_any_other():
    d = _start("c11", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c11")
    before = _counters(d)
    rnd2 = _open(d)
    check("[CP5-T11] (fixture) round 2 is the one-reviewer fix round",
          len(rnd2["reviewers"]) == 1 and rnd2.get("small_fix") is True and _after_block(rnd2), rnd2.get("round"))
    after = _counters(d)
    check("[CP5-T11] the step's attempts rose by exactly one", after[0] == before[0] + 1, (before, after))
    check("[CP5-T11] the run's adv_rounds_total rose by exactly one", after[1] == before[1] + 1, (before, after))
    _write_code(d, 31, "c11b")
    _open(d)
    check("[CP5-T11] replacing that fix round before any review was written adds nothing to either counter",
          _counters(d) == after, (after, _counters(d)))


def t12_the_steps_round_limit_still_blocks():
    d = _start("c12", config=["max_adversarial_rounds=2", "checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c12")
    rnd2 = _open(d)
    check("[CP5-T12] (fixture) round 2 is a one-reviewer fix round at the step's 2-round cap",
          len(rnd2["reviewers"]) == 1 and _after_block(rnd2) and _gate(d).get("attempts") == 2,
          (_ids(rnd2), _gate(d).get("attempts")))
    _block(d, rnd2, {rid: [_finding("S2")]})
    # the fixer's edit: written straight to disk, since `behavior` is refused once the step is blocked
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // c12 fixed again\n")
    r = _reviewers(d)
    out = _out(r)
    check("[CP5-T12] a further round is refused", r.returncode != 0, out)
    check("[CP5-T12] attempts stays at 2", _gate(d).get("attempts") == 2, _gate(d).get("attempts"))
    reason = _state(d).get("blocked_reason") or ""
    check("[CP5-T12] the step is blocked with the usual escalation message: still open after 2 rounds, a "
          "human/director decision", _state(d).get("phase") == "blocked" and "after 2 rounds" in reason
          and "human/director decision" in reason, (_state(d).get("phase"), reason))
    third = Path(d) / ".ratchet/runs/f/reviews/CP1/adv-round-03"
    check("[CP5-T12] no third round folder was created", not third.exists(), str(third))


def t13_the_run_wide_round_limit_still_blocks():
    d = _start("c13", config=["max_adversarial_rounds=99", "max_adversarial_rounds_run=2", "checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c13")
    rnd2 = _open(d)
    check("[CP5-T13] (fixture) round 2 is a one-reviewer fix round and the run total is at its cap of 2",
          len(rnd2["reviewers"]) == 1 and _after_block(rnd2) and _counters(d)[1] == 2,
          (_ids(rnd2), _counters(d)))
    _block(d, rnd2, {rid: [_finding("S2")]})
    _write_code(d, 32, "c13b")
    r = _reviewers(d)
    out = _out(r)
    check("[CP5-T13] a further round is refused with the run-wide cap message",
          r.returncode != 0 and "Run-wide adversarial round cap reached (2/2)" in out, out)
    check("[CP5-T13] adv_rounds_total stays at 2", _counters(d)[1] == 2, _counters(d))


def t14_an_approved_raise_grants_exactly_one_more_fix_round():
    d = _start("c14", config=["max_adversarial_rounds=2", "checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c14")
    rnd2 = _open(d)
    check("[CP5-T14] (fixture) round 2 is a one-reviewer fix round at the cap", len(rnd2["reviewers"]) == 1
          and _after_block(rnd2) and _gate(d).get("attempts") == 2, (_ids(rnd2), _gate(d).get("attempts")))
    _block(d, rnd2, {rid: [_finding("S2")]})
    check("[CP5-T14] (fixture) the step is blocked on its round limit", _state(d).get("phase") == "blocked",
          _state(d).get("blocked_reason"))
    g.rx(d, "ask", "raise-limit", "CP1")
    g.say(d, "raise: one more look at this fix is worth it")
    rr = g.rx(d, "raise-limit", "CP1")
    assert rr.returncode == 0, _out(rr)
    cap = _gate(d).get("cap_raised_to")
    _write_code(d, 33, "c14b")
    rnd3 = _open(d)
    _check_fix_round("CP5-T14", d, rnd3, rid, "the one extra round the user granted")
    check("[CP5-T14] attempts now equals the raised cap", _gate(d).get("attempts") == cap,
          (_gate(d).get("attempts"), cap))
    _block(d, rnd3, {rid: [_finding("S3")]})
    r = _reviewers(d)
    check("[CP5-T14] a further round is refused: the raise did not reset the budget", r.returncode != 0, _out(r))
    reason = _state(d).get("blocked_reason") or ""
    check("[CP5-T14] attempts is still the raised cap and the step is blocked again, at that cap",
          _gate(d).get("attempts") == cap and _state(d).get("phase") == "blocked"
          and f"after {cap} rounds" in reason, (_gate(d).get("attempts"), cap, reason))


def t15_a_blocking_fix_round_goes_to_the_usual_check_in():
    d = _start("c15", config=["max_adversarial_rounds=6"])  # default checkin_round=2
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c15")
    rnd2 = _open(d)
    check("[CP5-T15] (fixture) round 2 is a one-reviewer fix round",
          len(rnd2["reviewers"]) == 1 and _after_block(rnd2), (_ids(rnd2), _kind(rnd2)))
    _block(d, rnd2, {rid: [_finding("S2")]})
    _write_code(d, 32, "c15b")
    r = _reviewers(d)
    out = _out(r)
    check("[CP5-T15] the next `reviewers` call is refused naming the open check-in",
          r.returncode != 0 and "check-in is open" in out, out)
    third = Path(d) / ".ratchet/runs/f/reviews/CP1/adv-round-03"
    check("[CP5-T15] no round 3 opened while the check-in was open", not third.exists(), str(third))
    ci = g.rx(d, "check-in", "CP1", "--decision", "simplify", "--why", "keep the fix as small as it can be")
    assert ci.returncode == 0, _out(ci)
    rnd3 = _open(d)
    _check_fix_round("CP5-T15", d, rnd3, rid, "round 3, once the check-in was answered")
    check("[CP5-T15] attempts is 3", _gate(d).get("attempts") == 3, _gate(d).get("attempts"))


def t16_a_second_fix_round_also_gets_one_reviewer():
    d = _start("c16", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "correctness")
    _block(d, rnd1, {rid: [_finding("C1")]})
    _write_code(d, 30, "c16")
    rnd2 = _open(d)
    check("[CP5-T16] (fixture) round 2 is a one-reviewer fix round for that reviewer", _ids(rnd2) == [rid],
          _ids(rnd2))
    _block(d, rnd2, {rid: [_finding("C2")]})
    _write_code(d, 32, "c16b")
    _check_fix_round("CP5-T16", d, _open(d), rid, "round 3, after a blocked fix round")


def t17_the_gate_passes_and_the_suite_still_runs():
    marker = _CMD_DIR / "c17-pass-now"
    if marker.exists():
        marker.unlink()
    d = _start("c17", config=["checkin_round=99", f"regression_command={_suite_command(marker)}"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // c17 fixed\n")
    rc = g.rx(d, "complete", "CP1")
    check("[CP5-T17] `complete` right after the fixer's edit is refused: the gates are stale",
          rc.returncode != 0, _out(rc))
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = _open(d)
    check("[CP5-T17] (fixture) round 2 is a one-reviewer fix round",
          len(rnd2["reviewers"]) == 1 and _after_block(rnd2), (_ids(rnd2), _kind(rnd2)))
    r = _record(d, rnd2, [g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])])
    check("[CP5-T17] adv-record accepts the single review and the adversarial gate passes",
          r.returncode == 0 and _gate(d).get("status") == "passed", _out(r))
    rc = g.rx(d, "complete", "CP1")
    out = _out(rc)
    check("[CP5-T17] `complete` fails on the failing regression suite, naming it",
          rc.returncode != 0 and re.search(r"regression", out, re.I) is not None, out)
    log = Path(d) / ".ratchet/runs/f/evidence/CP1/regression.log"
    check("[CP5-T17] the regression command really ran (its own line is in the log)",
          log.exists() and "cp5-suite-ran" in log.read_text(), str(log))
    marker.write_text("x")
    rc = g.rx(d, "complete", "CP1")
    check("[CP5-T17] with a passing regression command the step completes", rc.returncode == 0, _out(rc))


def t18_status_and_the_commit_name_each_fix_round():
    d = _start("c18", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c18")
    rnd2 = _open(d)
    check("[CP5-T18] (fixture) round 2 is a one-reviewer fix round", _ids(rnd2) == [rid], _ids(rnd2))
    _block(d, rnd2, {rid: [_finding("S2")]})
    _write_code(d, 32, "c18b")
    rnd3 = _open(d)
    check("[CP5-T18] (fixture) round 3 is a one-reviewer fix round", _ids(rnd3) == [rid], _ids(rnd3))
    _approve(d, rnd3)
    sj, txt = _status_json(d), _status_text(d)
    for n in (2, 3):
        check(f"[CP5-T18] status --json says round {n} was a fix round after a block, by {rid}",
              _any_says(sj, f"round {n}", rid, "fix round", "block"), sj)
        check(f"[CP5-T18] plain status says the same about round {n}",
              _text_says(txt, f"round {n}", rid, "fix round", "block"), txt)
    check("[CP5-T18] round 1 is never called a fix round",
          not _any_says(sj, "round 1", "fix round") and not _text_says(txt, "round 1", "fix round"), (sj, txt))
    rc = g.rx(d, "complete", "CP1")
    assert rc.returncode == 0, _out(rc)
    msg = _commit_message(d)
    for n in (2, 3):
        check(f"[CP5-T18] the commit message says round {n} was a fix round after a block, by {rid}",
              _text_says(msg, f"round {n}", rid, "fix round", "block"), msg)
    check("[CP5-T18] the commit message does not call round 1 a fix round",
          not _text_says(msg, "round 1", "fix round"), msg)


def t19_status_names_a_blocked_fix_round_at_once():
    d = _start("c19", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "correctness")
    _block(d, rnd1, {rid: [_finding("C1")]})
    _write_code(d, 30, "c19")
    rnd2 = _open(d)
    check("[CP5-T19] (fixture) round 2 is a one-reviewer fix round", _ids(rnd2) == [rid], _ids(rnd2))
    paths = [g.write_review(d, rnd2, rid, [_finding("C2")])]
    r = _record(d, rnd2, paths)
    assert r.returncode == 2, _out(r)
    sj = _status_json(d)
    check("[CP5-T19] status --json already says round 2 was a fix round after a block, by its reviewer",
          _any_says(sj, "round 2", rid, "fix round", "block"), sj)
    check("[CP5-T19] the full round 1 is not listed as a fix round", not _any_says(sj, "round 1", "fix round"),
          sj)


def t20_a_fix_round_approval_is_not_the_full_review_baseline():
    d = _start("c20", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c20")
    baseline = _cps(d).get("last_full_review_fingerprint")
    rnd2 = _open(d)
    check("[CP5-T20] (fixture) round 2 is a one-reviewer fix round", _ids(rnd2) == [rid], _ids(rnd2))
    _approve(d, rnd2)
    check("[CP5-T20] the fix round's approval leaves last_full_review_fingerprint exactly as it was",
          _cps(d).get("last_full_review_fingerprint") == baseline,
          (baseline, _cps(d).get("last_full_review_fingerprint")))
    _append_lines(Path(d) / "src/f.js", 3, "c20b")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd3 = _open(d)
    check("[CP5-T20] the 3-line edit after it opens the step's full two-reviewer set",
          len(rnd3["reviewers"]) == 2, _ids(rnd3))
    _check_not_fix_round("CP5-T20", rnd3, "a 3-line edit after a fix round's approval")


def t21_a_fix_round_replaced_before_review_is_still_a_fix_round():
    d = _start("c21", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "c21")
    rnd2 = _open(d)
    check("[CP5-T21] (fixture) round 2 is a one-reviewer fix round", _ids(rnd2) == [rid], _ids(rnd2))
    before = _counters(d)
    _write_code(d, 34, "c21b")
    rnd3 = _open(d)
    _check_fix_round("CP5-T21", d, rnd3, rid, "the round replacing an unreviewed fix round")
    check("[CP5-T21] the step's attempts is the same number as before the replacement",
          _counters(d)[0] == before[0], (before, _counters(d)))
    check("[CP5-T21] the run's adv_rounds_total is the same number as before the replacement",
          _counters(d)[1] == before[1], (before, _counters(d)))


def t22_a_mid_round_failure_hands_the_slot_to_the_same_lens():
    with _own_home():
        d, env = _ext_project("c22", {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5")],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        _ext_record(d, rnd1, env)
        _triage(d, ["codex-standards/S1"], env=env)
        _write_code(d, 30, "c22", env=env)
        rnd2 = _open(d, env=env)
        check("[CP5-T22] (fixture) round 2 is a one-reviewer fix round for codex-standards",
              _ids(rnd2) == ["codex-standards"] and _after_block(rnd2), (_ids(rnd2), _kind(rnd2)))
        m = g.rx(d, "reviewer-status", "--mark", "codex-standards", "--reason", "auth expired mid-round", env=env)
        assert m.returncode == 0, _out(m)
        rnd2b = _open(d, env=env)
        check("[CP5-T22] the same round is refilled, not replaced by a new one",
              rnd2b.get("round") == rnd2.get("round"), (rnd2.get("round"), rnd2b.get("round")))
        check("[CP5-T22] it still names exactly one reviewer", len(rnd2b["reviewers"]) == 1, _ids(rnd2b))
        got = _ids(rnd2b)[0]
        check("[CP5-T22] the stand-in has lens standards and is not codex-standards",
              _lens(got) == "standards" and got != "codex-standards", got)
        check("[CP5-T22] no provider that had not reviewed the step is brought in (the host stands in)",
              got.startswith("host-"), got)
        check("[CP5-T22] it is still marked small_fix", rnd2b.get("small_fix") is True, rnd2b.get("small_fix"))
        r = _record(d, rnd2b, [g.write_review(d, rnd2b, got, [])], env=env)
        assert r.returncode == 0, _out(r)
        sj, txt = _status_json(d, env=env), _status_text(d, env=env)
        check("[CP5-T22] status names the stand-in as round 2's reviewer, not codex-standards",
              _any_says(sj, "round 2", got, "fix round") and not _any_says(sj, "round 2", "codex-standards",
                                                                           "fix round"), sj)
        rc = g.rx(d, "complete", "CP1", env=env)
        assert rc.returncode == 0, _out(rc)
        msg = _commit_message(d)
        check("[CP5-T22] the commit message names the stand-in as round 2's reviewer, not codex-standards",
              _text_says(msg, "round 2", got, "fix round") and not _text_says(msg, "round 2", "codex-standards"),
              msg)
        check("[CP5-T22] (fixture) plain status printed something to read", bool(txt.strip()), txt[:200])


def t23_the_docs_describe_the_fix_round():
    docs = {
        "README.md": ROOT / "README.md",
        "ratchet-adversarial-loop/SKILL.md": ROOT / "skills/ratchet-adversarial-loop/SKILL.md",
        "ratchet-orchestrator/SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
        "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md",
    }
    for label, p in docs.items():
        low = p.read_text(encoding="utf-8").lower()
        said = "fix round" in low and "one reviewer" in low and "block" in low
        check(f"[CP5-T23] {label} says a round after a blocked round, once the fixer has run, asks one reviewer",
              said, label)
        # the rest must be said where the rule is described, not anywhere in the file
        i = low.find("fix round")
        area = "" if i < 0 else low[max(0, i - 800):i + 3000]
        check(f"[CP5-T23] {label} says which reviewer: the same provider and lens, a company other than the "
              "host's preferred",
              "same provider" in area and ("lens" in area or "kind of review" in area)
              and bool(re.search(r"other than the host|another company|different company|not the host", area)),
              area[:500] or label)
        check(f"[CP5-T23] {label} says what happens when that reviewer is unavailable (its slot's own stand-in)",
              bool(re.search(r"stand-in|stands in|stand in|fallback|falls back", area))
              and ("unavailable" in area or "not available" in area), area[:500] or label)
        check(f"[CP5-T23] {label} says a fix round still counts toward the limits and goes to the usual check-in",
              bool(re.search(r"counts toward|limit|budget", area)) and "check-in" in area, area[:500] or label)
    schemas = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text(encoding="utf-8")
    check("[CP5-T23] schemas.md names the round's small_fix flag and its new kind field",
          "small_fix" in schemas and re.search(r"\bkind\b", schemas) is not None
          and re.search(r"after a block|after-a-block", schemas, re.I) is not None, "schemas.md")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"Later rounds cost less(.{0,4000})", readme, re.S)
    para = (m.group(1) if m else "").lower()
    check("[CP5-T23] README's 'Later rounds cost less' section covers both the small fix after approval and the "
          "fix round after a block", bool(m) and "small fix" in para and "fix round" in para, para[:600])


def t24_the_other_company_rechecks_even_when_the_host_is_listed_first():
    with _own_home():
        d, env = _ext_project("c24", {"normal": ["host-correctness", "codex-standards"]},
                              [("codex", "openai", "gpt-5")],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        check("[CP5-T24] (fixture) round 1 is host-correctness (slot 1) and codex-standards (slot 2)",
              [rv["id"] for rv in sorted(rnd1["reviewers"], key=lambda rv: rv["slot"])]
              == ["host-correctness", "codex-standards"], _ids(rnd1))
        _ext_record(d, rnd1, env, host_findings={"host-correctness": [_finding("C1")]})
        _triage(d, ["codex-standards/S1", "host-correctness/C1"], env=env)
        _write_code(d, 30, "c24", env=env)
        rnd2 = _open(d, env=env)
        _check_fix_round("CP5-T24", d, rnd2, "codex-standards", "host and other company both blocked")
        check("[CP5-T24] the host reviewer, although listed first, is not the one chosen",
              "host-correctness" not in _ids(rnd2), _ids(rnd2))


def t25_two_other_companies_the_first_slot_in_the_blocked_round_wins():
    projects = [("A", ["codex-standards", "gemini-correctness"], "codex-standards"),
                ("B", ["gemini-correctness", "codex-standards"], "gemini-correctness")]
    for label, slots, want in projects:
        with _own_home():
            d, env = _ext_project(f"c25{label.lower()}", {"normal": slots},
                                  [("codex", "openai", "gpt-5"), ("gemini", "google", "gemini-3")],
                                  replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS),
                                           "gemini": (_NO_FINDINGS,
                                                      json.dumps({"findings": [_finding("C1")]}))})
            rnd1 = _open(d, env=env)
            check(f"[CP5-T25] (fixture) project {label}'s round 1 is {slots} in that slot order",
                  [rv["id"] for rv in sorted(rnd1["reviewers"], key=lambda rv: rv["slot"])] == slots, _ids(rnd1))
            _ext_record(d, rnd1, env)
            _triage(d, ["codex-standards/S1", "gemini-correctness/C1"], env=env)
            _write_code(d, 30, "c25", env=env)
            rnd2 = _open(d, env=env)
            check(f"[CP5-T25] project {label}: round 2 names exactly {want}", _ids(rnd2) == [want], _ids(rnd2))
            check(f"[CP5-T25] project {label}: it is marked small_fix and follows a block",
                  rnd2.get("small_fix") is True and _after_block(rnd2),
                  (rnd2.get("small_fix"), _kind(rnd2) or "no kind field"))
            again = _open(d, env=env)
            check(f"[CP5-T25] project {label}: calling `reviewers --json` again names the same reviewer and the "
                  "same round, with no reshuffle", _ids(again) == [want]
                  and again.get("round") == rnd2.get("round"), (_ids(again), again.get("round")))


def t26_an_unavailable_choice_falls_back_within_its_own_lens():
    # (1) a bench mark
    with _own_home():
        d, env = _ext_project("c26a", {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5")],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        _ext_record(d, rnd1, env)
        _triage(d, ["codex-standards/S1"], env=env)
        _write_code(d, 30, "c26a", env=env)
        m = g.rx(d, "reviewer-status", "--mark", "codex-standards", "--reason", "quota exceeded", env=env)
        assert m.returncode == 0, _out(m)
        bench_path = Path(d) / ".ratchet/unavailable-reviewers.json"
        bench_before = json.loads(bench_path.read_text()).get("codex-standards")
        check("[CP5-T26] (fixture) codex-standards is benched with its reason before the round opens",
              (bench_before or {}).get("reason") == "quota exceeded" and bool((bench_before or {}).get("until")),
              bench_before)
        rnd2 = _open(d, env=env)
        check("[CP5-T26] the benched reviewer's slot is filled by exactly one reviewer",
              len(rnd2["reviewers"]) == 1, _ids(rnd2))
        got = _ids(rnd2)[0]
        check("[CP5-T26] the stand-in has lens standards, is not codex-standards and is no both-lens pick",
              _lens(got) == "standards" and got != "codex-standards", got)
        check("[CP5-T26] it is still marked small_fix and follows a block",
              rnd2.get("small_fix") is True and _after_block(rnd2),
              (rnd2.get("small_fix"), _kind(rnd2) or "no kind field"))
        row = rnd2["reviewers"][0]
        check("[CP5-T26] the reviewer row shows it stood in for codex-standards, with the bench reason",
              "codex-standards" in (row.get("fallback_for") or [])
              and "quota exceeded" in str((row.get("reasons") or {}).get("codex-standards", "")),
              (row.get("fallback_for"), row.get("reasons")))
        bench_after = json.loads(bench_path.read_text()).get("codex-standards")
        check("[CP5-T26] codex-standards' bench mark (reason and until) is exactly as it was before the round",
              bench_after == bench_before, (bench_before, bench_after))
    # (2) the share limit, not a bench mark: provider_share.max=1 is spent by codex's own round-1 start, so the
    # share refuses it for round 2 (max=0 is refused outright by config validation, so 1 is the way to get there)
    with _own_home():
        d, env = _ext_project("c26b", {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5")], config=["provider_share.max=1"],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        check("[CP5-T26] (fixture) codex-standards reviewed round 1, spending its one allowed start",
              "codex-standards" in _ids(rnd1), _ids(rnd1))
        _ext_record(d, rnd1, env)
        _triage(d, ["codex-standards/S1"], env=env)
        _write_code(d, 30, "c26b", env=env)
        rnd2 = _open(d, env=env)
        check("[CP5-T26] the share limit refusing codex still leaves exactly one reviewer",
              len(rnd2["reviewers"]) == 1, _ids(rnd2))
        got = _ids(rnd2)[0]
        check("[CP5-T26] that one reviewer keeps the standards lens and is not codex-standards",
              _lens(got) == "standards" and got != "codex-standards", got)
        check("[CP5-T26] it is still marked small_fix and follows a block",
              rnd2.get("small_fix") is True and _after_block(rnd2),
              (rnd2.get("small_fix"), _kind(rnd2) or "no kind field"))


def t27_the_fix_reviewer_always_comes_from_the_blocked_round():
    # (a) a second provider is installed and configured, but never reviewed this step
    with _own_home():
        d, env = _ext_project("c27a", {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5"), ("gemini", "google", "gemini-3")],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        check("[CP5-T27] (fixture) gemini is configured but reviewed nothing in round 1",
              not any(i.startswith("gemini-") for i in _ids(rnd1)), _ids(rnd1))
        _ext_record(d, rnd1, env)
        _triage(d, ["codex-standards/S1"], env=env)
        _write_code(d, 30, "c27a", env=env)
        rnd2 = _open(d, env=env)
        check("[CP5-T27] (a) round 2 names exactly codex-standards", _ids(rnd2) == ["codex-standards"],
              _ids(rnd2))
        check("[CP5-T27] (a) no gemini reviewer is named", not any(i.startswith("gemini-") for i in _ids(rnd2)),
              _ids(rnd2))
    # (b) a host-only blocked round, then a provider is added before the fix round opens
    with _own_home():
        b = _fake_clis({"codex": (_NO_FINDINGS, _NO_FINDINGS)})
        env = dict(g.ENV, PATH=str(b))
        d = g.project("c27b", extra_cp={"risk": "normal"}, config=["provider_share.max=9"])
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd1 = _open(d)
        check("[CP5-T27] (fixture) round 1 is host-only", all(i.startswith("host-") for i in _ids(rnd1)),
              _ids(rnd1))
        rid = _pick(rnd1, "correctness")
        _block(d, rnd1, {rid: [_finding("C1")]})
        r = g.rx(d, "providers", "--add", "codex", "--command", json.dumps([str(b / "codex")]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, _out(r)
        # a provider added mid-run is a rule change: the user's own approval re-snapshots it before the gates run
        a = g.rx(d, "ask", "approve-changes", env=env)
        assert a.returncode == 0, _out(a)
        g.say(d, "yes, go ahead")
        a = g.rx(d, "approve-changes", env=env)
        assert a.returncode == 0, _out(a)
        _write_code(d, 30, "c27b", env=env)
        rnd2 = _open(d, env=env)
        check("[CP5-T27] (b) round 2 names exactly the host reviewer that blocked", _ids(rnd2) == [rid],
              _ids(rnd2))
        check("[CP5-T27] (b) the provider added after the blocked round is never chosen",
              not any(i.startswith("codex-") or i.startswith("gemini-") for i in _ids(rnd2)), _ids(rnd2))
    # (c) a finding naming a reviewer id the blocked round never listed is never chosen
    d = _start("c27c", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    paths = [g.write_review(d, rnd1, rv["id"], [_finding("S1")] if rv["id"] == rid else [])
             for rv in rnd1["reviewers"]]
    r = _record(d, rnd1, paths)
    assert r.returncode == 2, _out(r)
    of = Path(d) / f".ratchet/runs/f/evidence/CP1/adversarial/round-{rnd1['round']:02d}/open-findings.json"
    findings = json.loads(of.read_text())
    assert findings and findings[0].get("reviewer") == rid, findings
    findings[0]["reviewer"] = "nobody-correctness"
    of.write_text(json.dumps(findings))
    _triage(d, ["nobody-correctness/S1"])
    _write_code(d, 30, "c27c")
    rnd2 = _open(d)
    check("[CP5-T27] (c) a reviewer id the blocked round never listed is never named in the next round",
          "nobody-correctness" not in _ids(rnd2), _ids(rnd2))
    check("[CP5-T27] (c) every reviewer the next round names is one of the blocked round's own",
          set(_ids(rnd2)) <= set(_ids(rnd1)), (_ids(rnd2), _ids(rnd1)))


def t28_a_high_risk_outside_block_is_rechecked_outside_the_host():
    with _own_home():
        d, env = _ext_project("c28", {"high": ["codex-standards", "codex-correctness", "host-correctness",
                                               "host-standards"]},
                              [("codex", "openai", "gpt-5")], risk="high",
                              replies={"codex": (_NO_FINDINGS, json.dumps({"findings": [_finding("C1")]}))})
        rnd1 = _open(d, env=env)
        check("[CP5-T28] (fixture) the high-risk round 1 names all four reviewers",
              sorted(_ids(rnd1)) == ["codex-correctness", "codex-standards", "host-correctness",
                                     "host-standards"], _ids(rnd1))
        _ext_record(d, rnd1, env)
        _triage(d, ["codex-correctness/C1"], env=env)
        _write_code(d, 60, "c28", env=env)
        rnd2 = _open(d, env=env)
        _check_fix_round("CP5-T28", d, rnd2, "codex-correctness", "high risk, an outside blocker")
        check("[CP5-T28] no host reviewer is named", not any(i.startswith("host-") for i in _ids(rnd2)),
              _ids(rnd2))


def t29_only_a_reviewer_whose_finding_is_being_fixed_is_chosen():
    with _own_home():
        d, env = _ext_project("c29", {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5")],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        _ext_record(d, rnd1, env, host_findings={"host-correctness": [_finding("C1")]})
        t = g.rx(d, "triage", "CP1", 'codex-standards/S1=rebut:"no developer can reach this path"', env=env)
        assert t.returncode == 0, _out(t)
        _triage(d, ["host-correctness/C1"], env=env)
        _write_code(d, 30, "c29", env=env)
        rnd2 = _open(d, env=env)
        _check_fix_round("CP5-T29", d, rnd2, "host-correctness", "the only reviewer with a finding to fix")
        check("[CP5-T29] the rebutted other-company reviewer is not chosen",
              "codex-standards" not in _ids(rnd2), _ids(rnd2))


# ------------------------------------------------------------------- extra cases kept from the reviewed candidate

def f15_a_blocked_and_an_approved_fix_round_are_both_named_and_told_apart():
    d = _start("c15b", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("S1")]})
    _write_code(d, 30, "f15")
    rnd2 = _open(d)
    _block(d, rnd2, {rid: [_finding("S2")]})
    _write_code(d, 32, "f15b")
    rnd3 = _open(d)
    _approve(d, rnd3)
    sj, txt = _status_json(d), _status_text(d)
    rc = g.rx(d, "complete", "CP1")
    assert rc.returncode == 0, _out(rc)
    msg = _commit_message(d)
    lines2 = [s for s in sj if "round 2" in s.lower() and "fix round" in s.lower()]
    lines3 = [s for s in sj if "round 3" in s.lower() and "fix round" in s.lower()]
    check("[CP5-F15] status --json names the blocked fix round (2) and the approved one (3) separately",
          bool(lines2) and bool(lines3) and lines2 != lines3, (lines2, lines3))
    check("[CP5-F15] the blocked fix round's own line says it blocked",
          any(re.search(r"block", s, re.I) for s in lines2), lines2)
    check("[CP5-F15] the approved fix round's own line says it was approved",
          any(re.search(r"approv", s, re.I) for s in lines3), lines3)
    for n in (2, 3):
        check(f"[CP5-F15] plain status and the commit message both name round {n} and {rid}",
              _text_says(txt, f"round {n}", rid) and _text_says(msg, f"round {n}", rid), (txt, msg))


def f16_the_evidence_names_the_reviewer_that_actually_ran():
    with _own_home():
        d, env = _ext_project("c16b", {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5")],
                              replies={"codex": (json.dumps({"findings": [_finding("S1")]}), _NO_FINDINGS)})
        rnd1 = _open(d, env=env)
        _ext_record(d, rnd1, env)
        _triage(d, ["codex-standards/S1"], env=env)
        _write_code(d, 30, "f16", env=env)
        rnd2 = _open(d, env=env)
        check("[CP5-F16] (fixture) round 2 is a one-reviewer fix round for codex-standards",
              _ids(rnd2) == ["codex-standards"], _ids(rnd2))
        m = g.rx(d, "reviewer-status", "--mark", "codex-standards", "--reason", "auth expired mid-round", env=env)
        assert m.returncode == 0, _out(m)
        rnd2b = _open(d, env=env)
        got = _ids(rnd2b)[0]
        check("[CP5-F16] the refilled round records the stand-in as its reviewer",
              (_cps(d).get("adv_round") or {}).get("reviewers") == [got] and got != "codex-standards",
              (_cps(d).get("adv_round") or {}).get("reviewers"))
        check("[CP5-F16] the round's own history says the stand-in came in for codex-standards",
              any("codex-standards" in (s.get("instead_of") or [])
                  for s in ((_cps(d).get("adv_round") or {}).get("substitutions") or [])),
              (_cps(d).get("adv_round") or {}).get("substitutions"))
        r = _record(d, rnd2b, [g.write_review(d, rnd2b, got, [])], env=env)
        assert r.returncode == 0, _out(r)
        rc = g.rx(d, "complete", "CP1", env=env)
        assert rc.returncode == 0, _out(rc)
        msg = _commit_message(d)
        check("[CP5-F16] the commit message names the reviewer that really ran and never the benched one as "
              "round 2's reviewer",
              _text_says(msg, "round 2", got) and not _text_says(msg, "round 2", "codex-standards"), msg)


CASES = [
    ("CP5-T01", t01_one_reviewer_after_a_block_and_a_fix),
    ("CP5-T02", t02_one_reviewer_in_a_high_risk_step_and_a_big_fix),
    ("CP5-T03", t03_the_outside_reviewer_rechecks_its_own_finding),
    ("CP5-T04", t04_a_first_round_keeps_the_full_set),
    ("CP5-T05", t05_a_block_with_no_fix_is_not_a_fix_round),
    ("CP5-T06", t06_a_round_after_a_clean_approval_is_not_a_post_block_fix_round),
    ("CP5-T07", t07_lane_bs_small_fix_after_approval_is_labelled_after_approval),
    ("CP5-T08", t08_an_accepted_finding_is_not_a_blocked_round),
    ("CP5-T09", t09_the_low_risk_setting_is_never_consulted),
    ("CP5-T10", t10_a_pinned_reviewer_list_is_retained),
    ("CP5-T11", t11_a_fix_round_costs_a_round_like_any_other),
    ("CP5-T12", t12_the_steps_round_limit_still_blocks),
    ("CP5-T13", t13_the_run_wide_round_limit_still_blocks),
    ("CP5-T14", t14_an_approved_raise_grants_exactly_one_more_fix_round),
    ("CP5-T15", t15_a_blocking_fix_round_goes_to_the_usual_check_in),
    ("CP5-T16", t16_a_second_fix_round_also_gets_one_reviewer),
    ("CP5-T17", t17_the_gate_passes_and_the_suite_still_runs),
    ("CP5-T18", t18_status_and_the_commit_name_each_fix_round),
    ("CP5-T19", t19_status_names_a_blocked_fix_round_at_once),
    ("CP5-T20", t20_a_fix_round_approval_is_not_the_full_review_baseline),
    ("CP5-T21", t21_a_fix_round_replaced_before_review_is_still_a_fix_round),
    ("CP5-T22", t22_a_mid_round_failure_hands_the_slot_to_the_same_lens),
    ("CP5-T23", t23_the_docs_describe_the_fix_round),
    ("CP5-T24", t24_the_other_company_rechecks_even_when_the_host_is_listed_first),
    ("CP5-T25", t25_two_other_companies_the_first_slot_in_the_blocked_round_wins),
    ("CP5-T26", t26_an_unavailable_choice_falls_back_within_its_own_lens),
    ("CP5-T27", t27_the_fix_reviewer_always_comes_from_the_blocked_round),
    ("CP5-T28", t28_a_high_risk_outside_block_is_rechecked_outside_the_host),
    ("CP5-T29", t29_only_a_reviewer_whose_finding_is_being_fixed_is_chosen),
    ("CP5-F15", f15_a_blocked_and_an_approved_fix_round_are_both_named_and_told_apart),
    ("CP5-F16", f16_the_evidence_names_the_reviewer_that_actually_ran),
]


def main():
    for cid, fn in CASES:
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception:
            check(f"[{cid}] ran to the end without an unexpected error", False, traceback.format_exc())
    for cid in PLANNED:
        if cid not in CASE_STATUS:
            check(f"[{cid}] recorded at least one check", False, "no check ran for this case")
    print(f"\n{PASS} passed, {FAIL} failed")
    rep = os.environ.get("RATCHET_TEST_REPORT")
    if rep:
        cases = [{"id": c, "status": "passed" if ok else "failed",
                  "message": "; ".join(CASE_MESSAGES.get(c, []))[:2000]}
                 for c, ok in sorted(CASE_STATUS.items())]
        Path(rep).write_text(json.dumps({"cases": cases}, indent=1))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
