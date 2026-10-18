#!/usr/bin/env python3
"""
Lane H CP5 tests: a fix round names exactly one reviewer.   python3 tests/test_fix_round.py

After a review round BLOCKS and the fixer changes the code, the next round is a fix round: `reviewers` names
exactly one reviewer, tied to the finding(s) triaged `fix`, says it is a fix round (`fix_round`, `fix_for` in
--json and state.json, a "Fix round:" line in its text output), whatever the fix's size and whatever the step's
risk. A first round, and a round that does not follow a block, keep their normal reviewer set. A fix round that
blocks again is followed by ANOTHER fix round, however many times it repeats -- never the full set (this
supersedes the older convergence rule for fix rounds specifically). A step that pins its own `reviewers` list is
the one approved exception: the fix-round mechanism never applies to it, so it always keeps the old narrow/full
convergence behavior.

When the findings being fixed came from more than one reviewer, the one reviewer a fix round ties to is picked by:
first, whichever of them is not the same company as the host (the host's own sub-agents, and a command reviewer
whose company matches the host's, both count as "the host"); second, among the preferred ones (or, if none differ
from the host, among all of them), the lowest slot number in the blocked round. If that reviewer can no longer
run, its own slot's normal fallback order picks a stand-in. Only findings triaged `fix` ever enter this pool: a
reviewer whose only finding was triaged `rebut` is never a candidate, let alone the pick.

A fix round's own approval is not a full review: it never advances `last_full_review_fingerprint` (what a later
small-fix round measures against) and never clears an existing lighter-review note (small_fix_review,
docs_only_review_skip, doc_edits_unreviewed) -- those stay exactly as they were before the fix round opened.

  - [CP5-F01] normal risk, a fix over 20 changed lines: one reviewer, the standards reviewer that raised it
  - [CP5-F02] normal risk, the correctness reviewer raised it: that reviewer is the one named
  - [CP5-F03] high risk, a small fix: one reviewer, the one that raised it
  - [CP5-F04] high risk, a fix over 20 changed lines: one reviewer, the one that raised it
  - [CP5-F05] a first round keeps its full set (normal and high risk) and is not a fix round
  - [CP5-F06] rounds that do not follow a block keep their normal set: a round replaced before anyone reviewed
              it, a fix over 20 lines after an approval, and a blocked round whose findings were all rebutted
              with no code change
  - [CP5-F07] findings being fixed from two reviewers, neither a different company than the host: tie-broken by
              the lowest slot number in the blocked round
  - [CP5-F08] repeated blocked fix rounds: a fix round that blocks stays a fix round, tied to the same reviewer,
              for as many rounds in a row as it keeps blocking; its eventual clean approval names one reviewer
  - [CP5-F09] a fix round replaced before it was reviewed (the fixer changed the code again) stays a fix round
              for the same reviewer and findings
  - [CP5-F10] findings being fixed from two reviewers, one of them a different company than the host: that
              reviewer is preferred even though it has the higher slot number
  - [CP5-F11] a step with a pinned `reviewers` list never gets a fix round: after a block and a fix, it keeps
              the old narrow/full convergence behavior, including bringing back the full set on a second block
  - [CP5-F12] a fix round's clean approval does not advance last_full_review_fingerprint and does not clear an
              existing lighter-review note (regression test for the full-review-baseline rule above)
  - [CP5-F13] round 1 blocks; with no code edit, round 2 opens narrowed (the fixer hasn't run yet, so the
              fix-round mechanism doesn't apply) and blocks again; only once the fixer actually changes the code
              does round 3 become a fix round, tied to the reviewer the narrowed round's finding came from
  - [CP5-F14] the same sequence as CP5-F13, on a step with a pinned `reviewers` list: round 3 stays the old
              narrow/full convergence (full set), the fix-round mechanism never applying to it
  - [CP5-F15] every recorded fix round for a checkpoint -- one that blocked and one that later approved -- is
              named by round number and its actual reviewer in `status --json`, plain `status`, and the
              checkpoint's own commit message
  - [CP5-F16] when the fix round's originally-tied reviewer becomes unavailable mid-round and a same-round
              refill replaces it, that evidence names the reviewer that actually ran, never the one the fix
              round was originally tied to

Black-box: every case drives the real ratchet.py CLI in a throwaway git project through tests/test_gates.py's
helpers (`project`, `approve_plan`, `to_adversarial`, `write_review`, `rx`), with RATCHET_HOME in a temp folder and
no other-provider CLI configured (providers=[]), so only the host's own reviewer ids are used, except CP5-F10
which registers one real command-kind provider (tests/test_providers.py's `fake_bin`/`env_with`) to exercise the
different-company preference.

CP5-T01 through CP5-T29 (below CP5-F16) are the approved plan's own requirement ids for this file. Most are
proved by one of CP5-F01..F16 above (see this change's own coverage table for exactly which); the ones below
are new black-box cases added only where an existing CP5-F case gave no direct evidence: an outside-only
blocker (T03), accept-finding never ties a fix round (T08), a low-risk checkpoint's own reviewer survives the
mechanism (T09), fix rounds counting toward and still blocked by the step and run-wide round budgets, and an
approved raise granting exactly one more (T11-T14), a repeat block through fix rounds still checking in (T15),
a fix round's approval still running `complete`'s tests and regression command (T17), the docs explaining the
rule (T23), two different-company outside reviewers tie-broken by slot (T25), a reviewer id absent from the
blocked round never chosen (T27), a high-risk outside blocker re-checked outside the host (T28), and a
rebutted reviewer excluded from the pick pool even at a lower slot (T29). Each CP5-T case needing a fake
external provider gets its own fresh RATCHET_HOME (`_fresh_home`), never test_gates.py's single shared one, so
`provider_share`'s cross-test bookkeeping can never affect which reviewer a fix round picks.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-F01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp                   # noqa: E402
import test_gates as g        # noqa: E402
import test_providers as tp   # noqa: E402  (fake_bin, env_with, open_round for a command-kind reviewer)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
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
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

def _finding(fid, severity="major"):
    return {"id": fid, "severity": severity, "file": "src/f.js", "description": "bug",
            "user_impact": "a user hits a crash"}


def _lens(rid: str) -> str:
    return rid.rsplit("-", 1)[-1] if rid.endswith(("-standards", "-correctness")) else rid


def _state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def _reviewers(d, *extra):
    return g.rx(d, "reviewers", "CP1", *extra)


def _open(d):
    r = _reviewers(d, "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _fix(d, lines, tag):
    """The fixer's change: src/f.js rewritten so `lines` code lines differ (f() still returns 1), then behavior."""
    body = "".join(f"export const pad_{tag}_{i} = {i};\n" for i in range(lines - 1))
    (d / "src/f.js").write_text(body + f"export const f = () => 1; // {tag}\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr


def _lines_since(d, rnd, path="src/f.js"):
    """Changed lines in `path` between the tree round `rnd` reviewed and the working tree (added + removed).
    `rnd` only needs a "fingerprint" key holding a tree id, so a plain {"fingerprint": <tree id>} works too."""
    old = g.git(d, "show", f"{rnd['fingerprint']}:{path}").stdout.splitlines()
    new = (Path(d) / path).read_text().splitlines()
    return sum(1 for ln in difflib.ndiff(old, new) if ln[:2] in ("+ ", "- "))


def _append_lines(path: Path, n: int, tag: str):
    """Appends exactly `n` new line-comments to `path` (0 removed), so the code-lines-changed measurement since
    any earlier tree is exactly the sum of every `n` appended since then -- used to size a fix precisely and to
    make several edits add up predictably. Same pattern tests/test_quick_path_small.py's CP3 fixture uses."""
    with open(path, "a") as f:
        for i in range(n):
            f.write(f"// filler-{tag}-{i}\n")


def _block(d, rnd, by: dict, decision="fix"):
    """Records `rnd` with reviewer id -> findings from `by` (every other reviewer approves), checks it blocks,
    and triages every finding `decision`. Returns the qualified finding keys."""
    paths = [g.write_review(d, rnd, rv["id"], by.get(rv["id"], [])) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    keys = [f"{rid}/{f['id']}" for rid, fs in by.items() for f in fs]
    for k in keys:
        t = g.rx(d, "triage", "CP1", f'{k}={decision}:"real for the users"')
        assert t.returncode == 0, t.stdout + t.stderr
    return keys


def _start(name, risk="normal", config=None):
    d = g.project(name, extra_cp={"risk": risk}, config=config)
    g.approve_plan(d)
    g.to_adversarial(d)
    return d


def _pick(rnd, lens):
    return next(rv["id"] for rv in rnd["reviewers"] if _lens(rv["id"]) == lens)


def _is_fix_round(case, d, rnd, reviewer, keys, what):
    check(f"[{case}] {what}: exactly one reviewer is named", len(rnd["reviewers"]) == 1, rnd["reviewers"])
    check(f"[{case}] {what}: it is {reviewer}, the reviewer that raised the finding being fixed",
          [rv["id"] for rv in rnd["reviewers"]] == [reviewer], rnd["reviewers"])
    check(f"[{case}] {what}: --json says it is a fix round", rnd.get("fix_round") is True, rnd)
    ff = rnd.get("fix_for") or {}
    check(f"[{case}] {what}: fix_for names the blocked round, its reviewer and the findings being fixed",
          ff.get("round") == rnd["round"] - 1 and ff.get("reviewer") == reviewer
          and sorted(ff.get("findings") or []) == sorted(keys), ff)
    check(f"[{case}] {what}: it is neither a small-fix nor a narrowed round",
          rnd.get("small_fix") is False and rnd.get("narrowed") is False, rnd)
    adv = _state(d)["checkpoints"]["CP1"]["adv_round"]
    check(f"[{case}] {what}: state.json's open round records fix_round and its one reviewer",
          adv.get("fix_round") is True and adv.get("reviewers") == [reviewer], adv)
    txt = _reviewers(d)
    check(f"[{case}] {what}: the text output identifies the fix round and its reviewer",
          txt.returncode == 0 and "Fix round: one reviewer" in txt.stdout and reviewer in txt.stdout,
          txt.stdout + txt.stderr)


def _not_fix_round(case, rnd, what):
    check(f"[{case}] {what}: not labelled a fix round",
          rnd.get("fix_round") is False and rnd.get("fix_for") is None, rnd)


# ------------------------------------------------------------------------------------------------ cases

def f01_normal_risk_large_fix():
    d = _start("f01")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    keys = _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 30, "f01")
    n = _lines_since(d, rnd1)
    check("[CP5-F01] (fixture sanity) the fix changes more than 20 lines since round 1", n > 20, n)
    _is_fix_round("CP5-F01", d, _open(d), rid, keys, "normal risk, a 30-line fix")


def f02_reviewer_tied_to_the_finding():
    d = _start("f02")
    rnd1 = _open(d)
    rid = _pick(rnd1, "correctness")
    keys = _block(d, rnd1, {rid: [_finding("C1"), _finding("C2", "blocker")]})
    _fix(d, 3, "f02")
    _is_fix_round("CP5-F02", d, _open(d), rid, keys, "normal risk, the correctness reviewer's two findings")


def f03_high_risk_small_fix():
    d = _start("f03", risk="high")
    rnd1 = _open(d)
    check("[CP5-F03] (fixture sanity) a high-risk first round names more than one reviewer",
          len(rnd1["reviewers"]) > 1, rnd1["reviewers"])
    rid = rnd1["reviewers"][-1]["id"]
    keys = _block(d, rnd1, {rid: [_finding("H1", "blocker")]})
    _fix(d, 2, "f03")
    _is_fix_round("CP5-F03", d, _open(d), rid, keys, "high risk, a 2-line fix")


def f04_high_risk_large_fix():
    d = _start("f04", risk="high")
    rnd1 = _open(d)
    rid = rnd1["reviewers"][0]["id"]
    keys = _block(d, rnd1, {rid: [_finding("H1")]})
    _fix(d, 40, "f04")
    n = _lines_since(d, rnd1)
    check("[CP5-F04] (fixture sanity) the fix changes more than 20 lines since round 1", n > 20, n)
    _is_fix_round("CP5-F04", d, _open(d), rid, keys, "high risk, a 40-line fix")


def f05_first_round_keeps_full_set():
    for risk, want in (("normal", 2), ("high", None)):
        d = _start(f"f05-{risk}", risk=risk)
        rnd1 = _open(d)
        n = len(json.loads(g.rx(d, "config").stdout)["review_depth"][risk]) if want is None else want
        check(f"[CP5-F05] {risk} risk: round 1 names its full reviewer set (more than one reviewer)",
              len(rnd1["reviewers"]) > 1 and len(rnd1["reviewers"]) <= n, rnd1["reviewers"])
        _not_fix_round("CP5-F05", rnd1, f"{risk} risk round 1")
        check(f"[CP5-F05] {risk} risk: round 1's text output has no fix-round line",
              "Fix round" not in _reviewers(d).stdout)


def f06_rounds_not_following_a_block():
    # a round replaced before anyone reviewed it: the next round keeps the full set
    d = _start("f06a")
    rnd1 = _open(d)
    _fix(d, 30, "f06a")
    rnd2 = _open(d)
    check("[CP5-F06] a round replacing an unreviewed one keeps the full set",
          sorted(rv["id"] for rv in rnd2["reviewers"]) == sorted(rv["id"] for rv in rnd1["reviewers"]),
          rnd2["reviewers"])
    _not_fix_round("CP5-F06", rnd2, "replacing an unreviewed round")
    # an approved round, then a fix over 20 lines: the normal full set, not a fix round
    d = _start("f06b")
    rnd1 = _open(d)
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    assert g.rx(d, "adv-record", "CP1", "--reviews", *paths).returncode == 0
    _fix(d, 30, "f06b")
    rnd2 = _open(d)
    check("[CP5-F06] after an approval, a 30-line change opens the full set",
          sorted(rv["id"] for rv in rnd2["reviewers"]) == sorted(rv["id"] for rv in rnd1["reviewers"]),
          rnd2["reviewers"])
    _not_fix_round("CP5-F06", rnd2, "after an approval")
    # a blocked round whose only finding was rebutted, code unchanged: no fixer ran, so not a fix round
    d = _start("f06c")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, decision="rebut")
    rnd2 = _open(d)
    _not_fix_round("CP5-F06", rnd2, "after a block that was only rebutted")


def f07_two_reviewers_tied_by_lowest_slot_when_neither_differs_from_host():
    d = _start("f07")
    rnd1 = _open(d)
    standards_id = _pick(rnd1, "standards")  # slot 1 -- lower slot than correctness (slot 2)
    by = {rv["id"]: [_finding("A1")] for rv in rnd1["reviewers"]}  # both reviewers raise a fix-triaged finding
    keys = _block(d, rnd1, by)
    _fix(d, 3, "f07")
    rnd2 = _open(d)
    _is_fix_round("CP5-F07", d, rnd2, standards_id, keys,
                  "neither reviewer differs from the host company: tie-broken by the lowest slot number "
                  "(standards, slot 1)")


def f08_repeated_blocked_fix_rounds_never_revert_to_the_full_set():
    d = _start("f08", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    keys1 = _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 25, "f08")
    rnd2 = _open(d)
    _is_fix_round("CP5-F08", d, rnd2, rid, keys1, "round 2, the first fix round")
    keys2 = _block(d, rnd2, {rid: [_finding("M1")]})
    _fix(d, 26, "f08b")
    rnd3 = _open(d)
    check("[CP5-F08] a fix round that blocks AGAIN stays a one-reviewer fix round, never the full set",
          rnd3.get("fix_round") is True and len(rnd3["reviewers"]) == 1, rnd3)
    _is_fix_round("CP5-F08", d, rnd3, rid, keys2, "round 3, the second fix round in a row")
    keys3 = _block(d, rnd3, {rid: [_finding("M1")]})
    _fix(d, 27, "f08c")
    rnd4 = _open(d)
    check("[CP5-F08] a THIRD block in a row still stays a one-reviewer fix round",
          rnd4.get("fix_round") is True and len(rnd4["reviewers"]) == 1, rnd4)
    _is_fix_round("CP5-F08", d, rnd4, rid, keys3, "round 4, the third fix round in a row")
    path = g.write_review(d, rnd4, rnd4["reviewers"][0]["id"], [])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    check("[CP5-F08] the fix round's eventual clean review approves the gate, naming one reviewer",
          r.returncode == 0 and "all 1 reviewer(s)" in r.stdout, r.stdout + r.stderr)


def f09_replaced_fix_round_stays_a_fix_round():
    d = _start("f09")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    keys = _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 4, "f09")
    rnd2 = _open(d)
    check("[CP5-F09] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    _fix(d, 30, "f09b")  # the fixer changes the code again before anyone reviews round 2
    rnd3 = _open(d)
    check("[CP5-F09] the round replacing an unreviewed fix round is still a fix round with one reviewer",
          rnd3.get("fix_round") is True and [rv["id"] for rv in rnd3["reviewers"]] == [rid], rnd3)
    check("[CP5-F09] its fix_for still names round 1's reviewer and findings",
          (rnd3.get("fix_for") or {}).get("round") == 1 and (rnd3.get("fix_for") or {}).get("reviewer") == rid
          and sorted((rnd3.get("fix_for") or {}).get("findings") or []) == sorted(keys), rnd3.get("fix_for"))


_FAKE_REPLY_TEMPLATE = r"""#!/bin/sh
# fake agent CLI: answers the connectivity probe, otherwise prints a fixed findings reply (never a real CLI).
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in
  *"connectivity check"*) reply="RATCHET-PROBE-OK";;
  *) reply='__REPLY__';;
esac
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""


def _fake_reply_bin(name, reply_json):
    """A fake command-kind reviewer CLI, on its own private PATH, that always answers `reply_json` (never a real
    CLI) -- the same template tests/test_signing.py's own fake_bin uses, kept local here so this file depends on
    neither test_signing.py's trust_helpers-based fixtures nor test_providers.py's fixed-empty-findings fake."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-fix-fakebin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    script = _FAKE_REPLY_TEMPLATE.replace("__REPLY__", reply_json.replace("\\", "\\\\").replace("'", "'\\''"))
    (b / name).write_text(script)
    (b / name).chmod(0o755)
    return b


def _fake_reply_bin_multi(pairs):
    """Like `_fake_reply_bin`, but for several differently-named fake reviewer CLIs sharing one PATH, each
    answering its own fixed reply -- for a case (CP5-T25) where two outside providers both have to run in the
    same round."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-fix-fakebin-multi-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    for name, reply_json in pairs:
        script = _FAKE_REPLY_TEMPLATE.replace("__REPLY__", reply_json.replace("\\", "\\\\").replace("'", "'\\''"))
        (b / name).write_text(script)
        (b / name).chmod(0o755)
    return b


@contextlib.contextmanager
def _fresh_home():
    """Temporarily points test_gates.py's shared RATCHET_HOME at a brand-new, empty folder for the whole scope
    of one CP5-T fake-external-provider case (project creation included -- a project's installed hooks bake in
    whichever RATCHET_HOME was active when `init` ran, so switching it only for LATER calls leaves the hooks
    pointed at the old one and every later command refused as a folder mismatch). Restores the previous value
    on exit, so no later case in this file is affected by this one's provider_share bookkeeping (3 starts per
    provider id per 15 minutes, shared by every lane under one RATCHET_HOME)."""
    old = g.ENV.get("RATCHET_HOME")
    home = Path(_tmp.mkdtemp(prefix="ratchet-fixround-freshhome-"))
    g.ENV["RATCHET_HOME"] = str(home)
    try:
        yield home
    finally:
        if old is None:
            g.ENV.pop("RATCHET_HOME", None)
        else:
            g.ENV["RATCHET_HOME"] = old


def _fresh_env(binpath):
    """The env for a provider-related call inside a `with _fresh_home():` block: the now-active fresh
    RATCHET_HOME (read live from g.ENV), plus `binpath` on PATH for the fake reviewer CLI(s)."""
    return dict(g.ENV, PATH=str(binpath))


def f10_prefers_a_different_company_than_the_host_over_a_lower_slot():
    # Slot 1 (the lower slot) is a host sub-agent (same company as the host); slot 2 is a real command-kind
    # reviewer of a different company (openai), answering through a fake CLI that always reports finding A1.
    # Both raise a fix-triaged finding: the different-company one (slot 2) must be preferred, even though its
    # slot number is higher.
    finding = _finding("A1")
    b = _fake_reply_bin("ext", json.dumps({"findings": [finding]}))
    env = dict(g.ENV, PATH=str(b))
    d = g.project("f10", extra_cp={"risk": "normal"},
                  config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext-correctness"]})])
    r = g.rx(d, "providers", "--add", "ext", "--command", json.dumps([str(b / "ext")]), "--stdin",
             "--company", "openai", "--model", "gpt-5", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = tp.open_round(d, env=env)
    check("[CP5-F10] (fixture sanity) round 1 names the host (slot 1, standards) and ext (slot 2, correctness)",
          sorted(rv["id"] for rv in rnd1["reviewers"]) == ["ext-correctness", "host-standards"]
          and next(rv["slot"] for rv in rnd1["reviewers"] if rv["id"] == "host-standards") == 1
          and next(rv["slot"] for rv in rnd1["reviewers"] if rv["id"] == "ext-correctness") == 2, rnd1["reviewers"])
    ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext-correctness")
    ext_out = Path(ext_row["output"])
    p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext-correctness", "--cp", "CP1",
                        "--out", str(ext_out)], cwd=d, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr + p.stdout  # a real, signed review carrying finding A1
    host_path = g.write_review(d, rnd1, "host-standards", [finding])  # the agent-kind reviewer, guard-confirmed
    r = g.rx(d, "adv-record", "CP1", "--reviews", str(ext_out), host_path, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    keys = sorted([f"ext-correctness/{finding['id']}", f"host-standards/{finding['id']}"])
    for k in keys:
        t = g.rx(d, "triage", "CP1", f'{k}=fix:"real for the users"', env=env)
        assert t.returncode == 0, t.stdout + t.stderr
    (d / "src/f.js").write_text("export const f = () => 1; // f10 fixed\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = tp.open_round(d, env=env)
    check("[CP5-F10] round 2 is a fix round naming exactly one reviewer",
          rnd2.get("fix_round") is True and len(rnd2["reviewers"]) == 1, rnd2)
    check("[CP5-F10] ...and it is ext-correctness (a different company than the host), not host-standards "
          "even though host-standards had the lower slot",
          rnd2["reviewers"][0]["id"] == "ext-correctness", rnd2["reviewers"])
    ff = rnd2.get("fix_for") or {}
    check("[CP5-F10] fix_for names ext-correctness and both findings being fixed",
          ff.get("reviewer") == "ext-correctness" and sorted(ff.get("findings") or []) == sorted(keys), ff)


def f11_pinned_reviewer_list_is_the_approved_exception():
    # A step that pins its own reviewer list never gets a fix round: the fix-round mechanism (lane H CP5) never
    # applies to it, so a block followed by a fix keeps the OLD narrow/full convergence behavior, including
    # bringing the full set back if a second block follows (the behavior CP5-T03 asserts for unpinned steps was
    # changed; a pinned step's behavior is explicitly NOT).
    d = g.project("f11", extra_cp={"risk": "normal", "reviewers": ["host-standards", "host-correctness"]},
                  config=["checkin_round=99"])
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = _open(d)
    check("[CP5-F11] (fixture sanity) round 1 names the pinned two-reviewer set",
          sorted(rv["id"] for rv in rnd1["reviewers"]) == ["host-correctness", "host-standards"], rnd1["reviewers"])
    standards_id = _pick(rnd1, "standards")
    _block(d, rnd1, {standards_id: [_finding("M1")]})
    _fix(d, 3, "f11")
    rnd2 = _open(d)
    _not_fix_round("CP5-F11", rnd2, "a pinned-reviewer step after a block and a fix")
    check("[CP5-F11] round 2 narrows to the one lens with an open finding (standards), the old rule",
          len(rnd2["reviewers"]) == 1 and rnd2["reviewers"][0]["id"] == standards_id, rnd2["reviewers"])
    _block(d, rnd2, {standards_id: [_finding("M1")]})
    _fix(d, 4, "f11b")
    rnd3 = _open(d)
    _not_fix_round("CP5-F11", rnd3, "a pinned-reviewer step after a second block")
    check("[CP5-F11] round 3 brings back the pinned step's full set (the old converge rule, unaffected by lane "
          "H CP5)", sorted(rv["id"] for rv in rnd3["reviewers"]) == ["host-correctness", "host-standards"],
          rnd3["reviewers"])


def f12_fix_round_approval_never_advances_the_full_review_baseline():
    # Regression test for the full-review-baseline rule, strengthened: it starts with an EXISTING lighter-review
    # note (a real, approved small-fix round's small_fix_review) and proves the fix round's own approval leaves
    # it exactly as it was -- never clearing it, never overwriting it with the fix round's own round/reviewer --
    # and that last_full_review_fingerprint stays at round 1's tree throughout. It then proves the baseline a
    # LATER small fix measures against still accumulates correctly through both the small-fix round and the fix
    # round: a further edit that only pushes the total past small_edit_lines when counted from round 1's own
    # baseline must open the step's normal full round, not a small-fix round.
    d = _start("f12")
    rnd1 = _open(d)
    full_fp_before = _state(d)["checkpoints"]["CP1"].get("last_full_review_fingerprint")
    check("[CP5-F12] (fixture sanity) round 1's last_full_review_fingerprint is not yet set", full_fp_before is None,
          full_fp_before)
    # round 1: a full, clean approval sets the baseline (last_full_review_fingerprint)
    r = g.rx(d, "adv-record", "CP1", "--reviews", *[g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]])
    assert r.returncode == 0, r.stdout + r.stderr
    baseline_fp = _state(d)["checkpoints"]["CP1"].get("last_full_review_fingerprint")
    check("[CP5-F12] (fixture sanity) round 1's approval set the baseline", bool((baseline_fp or {}).get("id")),
          baseline_fp)
    # a small (5-line) fix opens and approves as a one-reviewer small-fix round: the EXISTING lighter-review note
    _append_lines(d / "src/f.js", 5, "f12a")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = _open(d)
    check("[CP5-F12] (fixture sanity) round 2 is a one-reviewer small-fix round",
          rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1, rnd2)
    small_fix_rid = rnd2["reviewers"][0]["id"]
    r = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd2, small_fix_rid, []))
    assert r.returncode == 0, r.stdout + r.stderr
    small_fix_review_before = _state(d)["checkpoints"]["CP1"].get("small_fix_review")
    check("[CP5-F12] (fixture sanity) small_fix_review is now set (round 2, this reviewer) -- the EXISTING "
          "lighter-review note the fix round below must not disturb",
          small_fix_review_before == {"round": 2, "reviewer": small_fix_rid}, small_fix_review_before)
    check("[CP5-F12] (fixture sanity) the baseline is still round 1's (a small-fix approval never moves it)",
          _state(d)["checkpoints"]["CP1"].get("last_full_review_fingerprint") == baseline_fp, _state(d))
    # a further small (5-line) edit introduces a real finding from that same reviewer: it blocks, and the
    # fixer's own fix (3 more lines) is a real code change -- the next round is a fix round, tied to round 3's
    # reviewer, not another small-fix round
    _append_lines(d / "src/f.js", 5, "f12b")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd3 = _open(d)
    check("[CP5-F12] (fixture sanity) round 3 is still a one-reviewer small-fix round (10 lines since the "
          "baseline)", rnd3.get("small_fix") is True and len(rnd3["reviewers"]) == 1, rnd3)
    _block(d, rnd3, {rnd3["reviewers"][0]["id"]: [_finding("M1")]})
    _append_lines(d / "src/f.js", 3, "f12c")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd4 = _open(d)
    check("[CP5-F12] round 4 is a fix round (not another small-fix round), tied to round 3's reviewer",
          rnd4.get("fix_round") is True and rnd4.get("small_fix") is False and len(rnd4["reviewers"]) == 1
          and rnd4["reviewers"][0]["id"] == rnd3["reviewers"][0]["id"], rnd4)
    r = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd4, rnd4["reviewers"][0]["id"], []))
    assert r.returncode == 0, r.stdout + r.stderr
    cps_after = _state(d)["checkpoints"]["CP1"]
    check("[CP5-F12] after the fix round's clean approval, last_full_review_fingerprint is STILL round 1's "
          "baseline, not round 3's or round 4's tree",
          cps_after.get("last_full_review_fingerprint") == baseline_fp, (cps_after, baseline_fp))
    check("[CP5-F12] ...and the EXISTING small_fix_review note (round 2) is exactly as it was before the fix "
          "round -- not cleared, and not overwritten with round 4's own round/reviewer",
          cps_after.get("small_fix_review") == small_fix_review_before, cps_after)
    msg = g.git(d, "log", "-1", "--format=%B").stdout
    check("[CP5-F12] the commit message does not claim round 4 was reviewed as a small fix",
          not re.search(r"round 4 was reviewed as a small fix", msg, re.I), msg)
    # the later-small-fix behavior against the baseline: a further 10-line edit brings the total since round 1's
    # own baseline to 23 lines (5 + 5 + 3 + 10) -- over small_edit_lines (20) -- so the NEXT round must be the
    # step's normal full round, proving the accumulation still runs from round 1, through both the small-fix
    # round and the fix round, never reset or skipped by either.
    _append_lines(d / "src/f.js", 10, "f12d")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    since_baseline = _lines_since(d, {"fingerprint": baseline_fp["id"]})
    check("[CP5-F12] (fixture sanity) 23 code lines have changed since round 1's own baseline",
          since_baseline == 23, since_baseline)
    rnd5 = _open(d)
    check("[CP5-F12] round 5 is the step's normal full round (23 lines since the baseline is over the limit): "
          "the accumulation through the small-fix round and the fix round was never lost",
          rnd5.get("small_fix") is not True and rnd5.get("fix_round") is not True and len(rnd5["reviewers"]) == 2,
          rnd5)


def f13_narrowed_round_that_blocks_then_gets_a_real_fix_becomes_a_fix_round():
    # Director-reported gap: round 1 blocks; with NO code edit, round 2 opens narrowed (the fix-round mechanism
    # doesn't apply yet -- nothing has changed since round 1, so there's no fixer run to tie a fix round to) and
    # blocks again on the same, still-unfixed finding; only once the fixer actually changes the code does the
    # next round become a fix round, tied to the reviewer whose finding it was -- never the full set.
    d = _start("f13", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    # no code edit here: round 2 opens on round 1's own, unchanged fingerprint
    rnd2 = _open(d)
    check("[CP5-F13] (fixture sanity) round 2 opens NARROWED to the one reviewer with the open finding, not a "
          "fix round (no code has changed since round 1, so there's no fixer run to tie a fix round to)",
          rnd2.get("narrowed") is True and rnd2.get("fix_round") is False and len(rnd2["reviewers"]) == 1
          and rnd2["reviewers"][0]["id"] == rid, rnd2)
    keys2 = _block(d, rnd2, {rid: [_finding("M1")]})  # the same, still-unfixed finding blocks again
    # NOW the fixer actually changes the code and reruns behavior
    _fix(d, 5, "f13")
    rnd3 = _open(d)
    _is_fix_round("CP5-F13", d, rnd3, rid, keys2,
                  "round 3, after a NARROWED round (not a fix round) blocked and the fixer then actually ran")


def f14_pinned_reviewer_exception_holds_for_the_narrowed_then_fixed_sequence():
    # The same sequence as CP5-F13, on a step with a pinned `reviewers` list: the fix-round mechanism must never
    # apply to it, so round 3 stays the OLD narrow/full convergence rule (bringing back the full set after a
    # second block), exactly as CP5-F11 already proves for the simpler, full-round-first case.
    d = g.project("f14", extra_cp={"risk": "normal", "reviewers": ["host-standards", "host-correctness"]},
                  config=["checkin_round=99"])
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = _open(d)
    check("[CP5-F14] (fixture sanity) round 1 names the pinned two-reviewer set",
          sorted(rv["id"] for rv in rnd1["reviewers"]) == ["host-correctness", "host-standards"], rnd1["reviewers"])
    standards_id = _pick(rnd1, "standards")
    _block(d, rnd1, {standards_id: [_finding("M1")]})
    # no code edit here either: round 2 opens on round 1's own, unchanged fingerprint
    rnd2 = _open(d)
    _not_fix_round("CP5-F14", rnd2, "a pinned-reviewer step, round 2 (no code change yet)")
    check("[CP5-F14] round 2 narrows to the one lens with an open finding (standards), the old rule",
          len(rnd2["reviewers"]) == 1 and rnd2["reviewers"][0]["id"] == standards_id, rnd2["reviewers"])
    _block(d, rnd2, {standards_id: [_finding("M1")]})
    # the fixer now actually changes the code
    _fix(d, 5, "f14")
    rnd3 = _open(d)
    _not_fix_round("CP5-F14", rnd3, "a pinned-reviewer step, round 3 (after the fixer actually ran)")
    check("[CP5-F14] round 3 brings back the pinned step's full set (the old converge rule): the fix-round "
          "mechanism never applies to a pinned reviewer list, even in this exact sequence",
          sorted(rv["id"] for rv in rnd3["reviewers"]) == ["host-correctness", "host-standards"],
          rnd3["reviewers"])


def f15_fix_round_evidence_in_status_and_commit_message():
    # The approved CP5 plan requires every recorded fix round -- blocked or approved -- identified by round
    # number and its actual reviewer, in status --json, plain status and the checkpoint's own commit message.
    d = _start("f15", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 5, "f15")
    rnd2 = _open(d)
    check("[CP5-F15] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    _block(d, rnd2, {rid: [_finding("M1")]})  # round 2 (a fix round) blocks
    _fix(d, 6, "f15b")
    rnd3 = _open(d)
    check("[CP5-F15] (fixture sanity) round 3 is a fix round too", rnd3.get("fix_round") is True, rnd3)
    r = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd3, rnd3["reviewers"][0]["id"], []))
    assert r.returncode == 0, r.stdout + r.stderr  # round 3 (a fix round) approves

    expect_blocked = f"CP1: round 2 was reviewed as a fix round by {rid} (blocked)."
    expect_approved = f"CP1: round 3 was reviewed as a fix round by {rid} (approved)."

    sj = json.loads(g.rx(d, "status", "--json").stdout)
    lines = sj.get("fix_round_reviews") or []
    check("[CP5-F15] status --json's fix_round_reviews names round 2, blocked", expect_blocked in lines, lines)
    check("[CP5-F15] ...and round 3, approved", expect_approved in lines, lines)

    txt = g.rx(d, "status").stdout
    check("[CP5-F15] plain status prints the round-2 (blocked) line", expect_blocked in txt, txt)
    check("[CP5-F15] ...and the round-3 (approved) line", expect_approved in txt, txt)

    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    msg = g.git(d, "log", "-1", "--format=%B").stdout
    check("[CP5-F15] the checkpoint's commit message names round 2 (blocked) as a fix round", expect_blocked in msg,
          msg)
    check("[CP5-F15] ...and round 3 (approved) too", expect_approved in msg, msg)


def f16_fix_round_evidence_names_the_replacement_reviewer():
    # When the fix round's originally-tied reviewer goes unavailable mid-round and the normal same-round refill
    # gives its slot to a fallback, the recorded evidence must name whichever reviewer actually ran -- never the
    # one the fix round was originally tied to.
    finding = _finding("A1")
    b = _fake_reply_bin("ext2", json.dumps({"findings": [finding]}))
    env = dict(g.ENV, PATH=str(b))
    d = g.project("f16", extra_cp={"risk": "normal"},
                  config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext2-correctness"]})])
    r = g.rx(d, "providers", "--add", "ext2", "--command", json.dumps([str(b / "ext2")]), "--stdin",
             "--company", "openai", "--model", "gpt-5", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = tp.open_round(d, env=env)
    check("[CP5-F16] (fixture sanity) round 1 ties the correctness slot to the registered provider",
          any(rv["id"] == "ext2-correctness" for rv in rnd1["reviewers"]), rnd1["reviewers"])
    ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext2-correctness")
    p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext2-correctness", "--cp", "CP1",
                        "--out", ext_row["output"]], cwd=d, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr + p.stdout  # a real, signed review carrying finding A1
    host_path = g.write_review(d, rnd1, "host-standards", [])
    r = g.rx(d, "adv-record", "CP1", "--reviews", ext_row["output"], host_path, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    t = g.rx(d, "triage", "CP1", f'ext2-correctness/{finding["id"]}=fix:"real for the users"', env=env)
    assert t.returncode == 0, t.stdout + t.stderr
    (d / "src/f.js").write_text("export const f = () => 1; // f16 fixed\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = tp.open_round(d, env=env)
    check("[CP5-F16] (fixture sanity) round 2 is a fix round tied to ext2-correctness",
          rnd2.get("fix_round") is True and len(rnd2["reviewers"]) == 1
          and rnd2["reviewers"][0]["id"] == "ext2-correctness", rnd2)
    # ext2-correctness goes unavailable mid-round (e.g. its CLI starts failing): re-running `reviewers` for the
    # SAME round refills its slot with the normal fallback, not a different reviewer's slot
    m = g.rx(d, "reviewer-status", "--mark", "ext2-correctness", "--reason", "auth expired mid-round", env=env)
    assert m.returncode == 0, m.stdout + m.stderr
    rnd2b = tp.open_round(d, env=env)
    check("[CP5-F16] the SAME round (2) is refilled, not replaced by a new round", rnd2b["round"] == rnd2["round"],
          rnd2b)
    check("[CP5-F16] ...with the normal fallback for that slot (host-alt-correctness), still a fix round, still "
          "exactly one reviewer",
          rnd2b.get("fix_round") is True and len(rnd2b["reviewers"]) == 1
          and rnd2b["reviewers"][0]["id"] == "host-alt-correctness", rnd2b)
    replacement = rnd2b["reviewers"][0]["id"]
    r = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd2b, replacement, []), env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    expect = f"CP1: round 2 was reviewed as a fix round by {replacement} (approved)."

    sj = json.loads(g.rx(d, "status", "--json").stdout)
    check("[CP5-F16] status --json's evidence names the REPLACEMENT reviewer that actually ran, not the "
          "originally-tied ext2-correctness", expect in (sj.get("fix_round_reviews") or []),
          sj.get("fix_round_reviews"))
    txt = g.rx(d, "status").stdout
    check("[CP5-F16] plain status names the replacement too", expect in txt, txt)
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    msg = g.git(d, "log", "-1", "--format=%B").stdout
    check("[CP5-F16] the commit message names the replacement reviewer, never the one originally tied to",
          expect in msg and "ext2-correctness" not in msg, msg)


# ------------------------------------------------------------------------------------------------ CP5-T cases:
# black-box CLI tests under the approved plan's own CP5-T01..T29 ids, for the requirements CP5-F01..F16 above
# (lane H CP5's engineering test suite, written first) don't already give direct evidence for. See the
# coverage table in the PR/report that accompanies this change for exactly which existing CP5-F case (or which
# of these) proves each of CP5-T01..T29; nothing here duplicates an existing CP5-F case's own scenario.

def t03_outside_blocker_fix_round_ties_to_the_same_provider_and_lens_without_the_host():
    # [CP5-T03] When only the OUTSIDE (non-host) reviewer's own lens blocks -- the host's own review of this
    # round is clean -- the fix round must tie to that SAME outside provider and lens, never substituting the
    # host: the host is never asked to review again.
    with _fresh_home():
        finding = _finding("A1")
        b = _fake_reply_bin("ext10", json.dumps({"findings": [finding]}))
        env = _fresh_env(b)
        d = g.project("t03", extra_cp={"risk": "normal"},
                      config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext10-correctness"]})])
        r = g.rx(d, "providers", "--add", "ext10", "--command", json.dumps([str(b / "ext10")]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd1 = tp.open_round(d, env=env)
        check("[CP5-T03] (fixture sanity) round 1 names the host (standards) and the outside provider "
              "(correctness)",
              sorted(rv["id"] for rv in rnd1["reviewers"]) == ["ext10-correctness", "host-standards"],
              rnd1["reviewers"])
        ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext10-correctness")
        p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext10-correctness", "--cp", "CP1",
                            "--out", ext_row["output"]], cwd=d, capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr + p.stdout
        host_path = g.write_review(d, rnd1, "host-standards", [])  # the host's own review of this round is clean
        r = g.rx(d, "adv-record", "CP1", "--reviews", ext_row["output"], host_path, env=env)
        assert r.returncode == 2, r.stdout + r.stderr
        t = g.rx(d, "triage", "CP1", f'ext10-correctness/{finding["id"]}=fix:"real for the users"', env=env)
        assert t.returncode == 0, t.stdout + t.stderr
        (d / "src/f.js").write_text("export const f = () => 1; // t03 fixed\n")
        r = g.rx(d, "behavior", "CP1", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        rnd2 = tp.open_round(d, env=env)
        check("[CP5-T03] round 2 is a fix round naming exactly one reviewer",
              rnd2.get("fix_round") is True and len(rnd2["reviewers"]) == 1, rnd2)
        check("[CP5-T03] ...and it is the SAME outside provider and lens (ext10-correctness), never "
              "host-standards", rnd2["reviewers"][0]["id"] == "ext10-correctness", rnd2["reviewers"])


def t08_accepting_a_finding_never_creates_a_fix_round():
    # [CP5-T08] A finding resolved through accept-finding (CP3), not fixed, never ties a fix round to it: the
    # gate passes directly on acceptance (no round at all), and a LATER code change opens the step's normal
    # round, never a fix round tied to the accepted finding.
    d = _start("t08")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    finding = _finding("M1")
    paths = [g.write_review(d, rnd1, rv["id"], [finding] if rv["id"] == rid else [])
             for rv in rnd1["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    key = f"{rid}/{finding['id']}"
    g.rx(d, "ask", "accept-finding", "CP1", key)
    g.say(d, "accept: not reachable by a real user of this project")
    r = g.rx(d, "accept-finding", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    gate = _state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T08] (fixture sanity) the gate passed directly on acceptance, with no new round opened",
          gate.get("status") == "passed", gate)
    check("[CP5-T08] no fix round was ever recorded for this acceptance",
          not any(rr.get("fix_round") for rr in gate.get("rounds") or []), gate.get("rounds"))
    _fix(d, 5, "t08b")
    rnd2 = _open(d)
    _not_fix_round("CP5-T08", rnd2, "a later code change after an accepted finding")
    check("[CP5-T08] the later round is the step's normal full set, not tied to the accepted finding's reviewer",
          sorted(rv["id"] for rv in rnd2["reviewers"]) == sorted(rv["id"] for rv in rnd1["reviewers"]),
          rnd2["reviewers"])


def t09_low_risk_settings_do_not_override_fix_round_selection():
    # [CP5-T09] At low risk, round 1 already names exactly one (both-lens) reviewer via review_depth["low"].
    # After it blocks and is fixed, the fix round must still tie to that SAME reviewer -- never re-resolved
    # through some other low-risk default -- proving the low-risk reviewer setting never overrides the
    # fix-round mechanism's own reviewer-selection rule.
    d = _start("t09", risk="low")
    rnd1 = _open(d)
    check("[CP5-T09] (fixture sanity) a low-risk first round names exactly one (both-lens) reviewer",
          len(rnd1["reviewers"]) == 1, rnd1["reviewers"])
    rid = rnd1["reviewers"][0]["id"]
    keys = _block(d, rnd1, {rid: [_finding("L1")]})
    _fix(d, 3, "t09")
    rnd2 = _open(d)
    _is_fix_round("CP5-T09", d, rnd2, rid, keys,
                  "low risk: the fix round ties to round 1's own (low-risk) reviewer")


def t11_fix_rounds_count_toward_step_and_run_budgets():
    # [CP5-T11] A fix round costs exactly like any other round: opening one still increments the step's own
    # attempts and the run-wide adv_rounds_total -- the new mechanism is never exempted from the existing
    # budgets.
    d = _start("t11", config=["checkin_round=99"])
    rnd1 = _open(d)
    st0 = _state(d)
    gate0 = st0["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T11] (fixture sanity) round 1 already counted (attempts=1, run total=1)",
          gate0.get("attempts") == 1 and st0.get("adv_rounds_total") == 1,
          (gate0.get("attempts"), st0.get("adv_rounds_total")))
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t11")
    rnd2 = _open(d)
    check("[CP5-T11] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    st2 = _state(d)
    gate2 = st2["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T11] opening the fix round still increments the step's own attempts (2)",
          gate2.get("attempts") == 2, gate2.get("attempts"))
    check("[CP5-T11] ...and the run-wide total too (2)", st2.get("adv_rounds_total") == 2,
          st2.get("adv_rounds_total"))
    _block(d, rnd2, {rid: [_finding("M1")]})
    _fix(d, 4, "t11b")
    rnd3 = _open(d)
    check("[CP5-T11] (fixture sanity) round 3 is a fix round too", rnd3.get("fix_round") is True, rnd3)
    st3 = _state(d)
    gate3 = st3["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T11] a second fix round in a row increments both counters again (3, 3)",
          gate3.get("attempts") == 3 and st3.get("adv_rounds_total") == 3,
          (gate3.get("attempts"), st3.get("adv_rounds_total")))


def t12_the_step_round_limit_still_blocks_through_fix_rounds():
    # [CP5-T12] Reaching the step's own max_adversarial_rounds purely through repeated fix rounds still blocks
    # the run, exactly as it would through ordinary rounds.
    d = _start("t12", config=["max_adversarial_rounds=2", "checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t12")
    rnd2 = _open(d)
    check("[CP5-T12] (fixture sanity) round 2 is a fix round, attempts now at the step's own cap (2)",
          rnd2.get("fix_round") is True
          and _state(d)["checkpoints"]["CP1"]["gates"]["adversarial"].get("attempts") == 2, rnd2)
    paths = [g.write_review(d, rnd2, rv["id"], [_finding("M1")]) for rv in rnd2["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("[CP5-T12] blocking again at the cap is still refused as NOT approved",
          r.returncode == 2, r.stdout + r.stderr)
    st = _state(d)
    check("[CP5-T12] the run is now blocked on the step's own round limit, reached purely through fix rounds",
          st.get("phase") == "blocked" and "human/director decision" in (st.get("blocked_reason") or ""),
          st.get("blocked_reason"))


def t13_the_run_wide_round_limit_still_blocks_through_fix_rounds():
    # [CP5-T13] Reaching the run-wide max_adversarial_rounds_run purely through repeated fix rounds still
    # blocks the run, exactly as it would through ordinary rounds.
    d = _start("t13", config=["max_adversarial_rounds=99", "max_adversarial_rounds_run=2", "checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t13")
    rnd2 = _open(d)
    check("[CP5-T13] (fixture sanity) round 2 is a fix round, the run-wide total now at its cap (2)",
          rnd2.get("fix_round") is True and _state(d).get("adv_rounds_total") == 2, rnd2)
    paths = [g.write_review(d, rnd2, rv["id"], [_finding("M1")]) for rv in rnd2["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    t = g.rx(d, "triage", "CP1", f'{rid}/M1=fix:"real for the users"')
    assert t.returncode == 0, t.stdout + t.stderr
    _fix(d, 4, "t13b")
    r2 = g.rx(d, "reviewers", "CP1", "--json")
    check("[CP5-T13] no further round opens: the run-wide cap, reached through fix rounds, still blocks",
          r2.returncode != 0, r2.stdout + r2.stderr)
    st = _state(d)
    check("[CP5-T13] the run is blocked specifically on the run-wide cap",
          st.get("phase") == "blocked" and "Run-wide adversarial round cap" in (st.get("blocked_reason") or ""),
          st.get("blocked_reason"))


def t14_an_approved_raise_grants_exactly_one_further_fix_round():
    # [CP5-T14] After the step's round limit blocks through fix rounds, an approved raise-limit grants exactly
    # one further round, and that round is still a one-reviewer fix round, never the full set.
    d = _start("t14", config=["max_adversarial_rounds=2", "checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t14")
    rnd2 = _open(d)
    check("[CP5-T14] (fixture sanity) round 2 is a fix round at the step's own cap (2)",
          rnd2.get("fix_round") is True
          and _state(d)["checkpoints"]["CP1"]["gates"]["adversarial"].get("attempts") == 2, rnd2)
    paths = [g.write_review(d, rnd2, rv["id"], [_finding("M1")]) for rv in rnd2["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    check("[CP5-T14] (fixture sanity) the run is now blocked on the step's own round limit",
          _state(d).get("phase") == "blocked", _state(d).get("blocked_reason"))
    t = g.rx(d, "triage", "CP1", f'{rid}/M1=fix:"real for the users"')
    assert t.returncode == 0, t.stdout + t.stderr
    g.rx(d, "ask", "raise-limit", "CP1")
    g.say(d, "raise: one more look is worth it here")
    rr = g.rx(d, "raise-limit", "CP1")
    check("[CP5-T14] raise-limit succeeds", rr.returncode == 0, rr.stdout + rr.stderr)
    _fix(d, 4, "t14b")  # the run is unblocked now (raise-limit above), so behavior can run again
    rnd3 = _open(d)
    check("[CP5-T14] exactly one further round opens, and it is STILL a one-reviewer fix round, not the full set",
          rnd3.get("fix_round") is True and len(rnd3["reviewers"]) == 1 and rnd3["reviewers"][0]["id"] == rid,
          rnd3)
    paths3 = [g.write_review(d, rnd3, rv["id"], [_finding("M1")]) for rv in rnd3["reviewers"]]
    r3 = g.rx(d, "adv-record", "CP1", "--reviews", *paths3)
    check("[CP5-T14] blocking that one extra round again hits the raised cap, refused again",
          r3.returncode == 2, r3.stdout + r3.stderr)
    check("[CP5-T14] the raise was truly one-time: the run blocks again, never granting a second extra round",
          _state(d).get("phase") == "blocked", _state(d).get("blocked_reason"))


def t15_a_repeat_block_through_fix_rounds_still_triggers_the_usual_check_in():
    # [CP5-T15] The usual check-in (reviews dragging) still opens after the configured number of consecutive
    # blocking rounds, even when those rounds are fix rounds rather than the old narrowed mechanism.
    d = _start("t15")  # default checkin_round=2, not suppressed here
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})  # round 1: first consecutive blocking round
    _fix(d, 3, "t15")
    rnd2 = _open(d)
    check("[CP5-T15] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    paths = [g.write_review(d, rnd2, rv["id"], [_finding("M1")]) for rv in rnd2["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)  # round 2: second consecutive blocking round
    check("[CP5-T15] the second consecutive blocked fix round opens a check-in",
          r.returncode == 2 and "check-in opened" in (r.stdout + r.stderr), r.stdout + r.stderr)
    t = g.rx(d, "triage", "CP1", f'{rid}/M1=fix:"real for the users"')
    assert t.returncode == 0, t.stdout + t.stderr
    _fix(d, 4, "t15b")
    r2 = g.rx(d, "reviewers", "CP1", "--json")
    check("[CP5-T15] no further round opens while the check-in is still open",
          r2.returncode != 0 and "check-in is open" in (r2.stdout + r2.stderr), r2.stdout + r2.stderr)
    r3 = g.rx(d, "check-in", "CP1", "--decision", "simplify", "--why", "keep the fix minimal and move on")
    check("[CP5-T15] the check-in can be resolved normally", r3.returncode == 0, r3.stdout + r3.stderr)
    rnd3 = _open(d)
    check("[CP5-T15] once resolved, the next round opens again -- still a fix round",
          rnd3.get("fix_round") is True, rnd3)


def t17_fix_round_approval_passes_the_gate_and_complete_still_runs_tests_and_the_full_suite():
    # [CP5-T17] A fix round's clean approval passes the gate (as every other approval does), and `complete`
    # afterward still runs the configured regression command -- it is never skipped just because the approval
    # came from a fix round.
    d = _start("t17", config=["checkin_round=99", "regression_command=node --test tests/cp1.test.js"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t17")
    rnd2 = _open(d)
    check("[CP5-T17] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    path = g.write_review(d, rnd2, rid, [])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    check("[CP5-T17] the fix round's clean review passes the gate",
          r.returncode == 0 and "all 1 reviewer(s)" in r.stdout, r.stdout + r.stderr)
    gate = _state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP5-T17] the adversarial gate is now passed", gate.get("status") == "passed", gate)
    rc = g.rx(d, "complete", "CP1")
    check("[CP5-T17] complete succeeds", rc.returncode == 0, rc.stdout + rc.stderr)
    log = d / ".ratchet/runs/f/evidence/CP1/regression.log"
    check("[CP5-T17] the full regression suite actually ran at complete (its log was written)",
          log.exists() and log.stat().st_size > 0, log)


def t23_docs_explain_the_fix_round_rule():
    # [CP5-T23] README, the orchestrator skill and schemas.md all explain the fix-round rule.
    root = HERE.parent
    readme = (root / "README.md").read_text()
    skill = (root / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    schemas = (root / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP5-T23] README explains the fix-round rule (one reviewer, however many times it blocks)",
          re.search(r"fix round.{0,80}one reviewer", readme, re.I | re.S) is not None, readme[:0])
    check("[CP5-T23] the orchestrator skill tells the agent what a fix round is and to launch only that reviewer",
          re.search(r"fix round", skill, re.I) is not None and "exactly one reviewer" in skill, skill[:0])
    check("[CP5-T23] schemas.md documents fix_round/fix_for in state.json / --json",
          "fix_round" in schemas and "fix_for" in schemas, schemas[:0])


def t25_two_outside_reviewers_block_the_earlier_one_in_the_round_wins():
    # [CP5-T25] When the findings being fixed came from TWO outside (non-host) reviewers of different
    # companies -- neither preferred over the other by the different-company rule, since both already differ
    # from the host -- the tie breaks by the lowest slot number in the blocked round, the same rule CP5-F07
    # proves for two host reviewers.
    with _fresh_home():
        f_a, f_b = _finding("A1"), _finding("B1")
        b = _fake_reply_bin_multi([("ext11", json.dumps({"findings": [f_a]})),
                                   ("ext12", json.dumps({"findings": [f_b]}))])
        env = _fresh_env(b)
        d = g.project("t25", extra_cp={"risk": "normal"},
                      config=["review_depth=" + json.dumps({"normal": ["ext11-standards", "ext12-correctness"]})])
        r = g.rx(d, "providers", "--add", "ext11", "--command", json.dumps([str(b / "ext11")]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        r = g.rx(d, "providers", "--add", "ext12", "--command", json.dumps([str(b / "ext12")]), "--stdin",
                 "--company", "google", "--model", "gemini-3", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd1 = tp.open_round(d, env=env)
        check("[CP5-T25] (fixture sanity) round 1 names ext11 (slot 1) and ext12 (slot 2)",
              next(rv["slot"] for rv in rnd1["reviewers"] if rv["id"] == "ext11-standards") == 1
              and next(rv["slot"] for rv in rnd1["reviewers"] if rv["id"] == "ext12-correctness") == 2,
              rnd1["reviewers"])
        paths = []
        for rv in rnd1["reviewers"]:
            out = Path(rv["output"])
            p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", rv["id"], "--cp", "CP1", "--out",
                               str(out)], cwd=d, capture_output=True, text=True, env=env)
            assert p.returncode == 0, p.stderr + p.stdout
            paths.append(str(out))
        r = g.rx(d, "adv-record", "CP1", "--reviews", *paths, env=env)
        assert r.returncode == 2, r.stdout + r.stderr
        keys = sorted([f"ext11-standards/{f_a['id']}", f"ext12-correctness/{f_b['id']}"])
        for k in keys:
            t = g.rx(d, "triage", "CP1", f'{k}=fix:"real for the users"', env=env)
            assert t.returncode == 0, t.stdout + t.stderr
        (d / "src/f.js").write_text("export const f = () => 1; // t25 fixed\n")
        r = g.rx(d, "behavior", "CP1", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        rnd2 = tp.open_round(d, env=env)
        check("[CP5-T25] round 2 is a fix round naming exactly one reviewer",
              rnd2.get("fix_round") is True and len(rnd2["reviewers"]) == 1, rnd2)
        check("[CP5-T25] ...and it is ext11-standards (slot 1, the earlier reviewer in the blocked round), not "
              "ext12-correctness (slot 2)", rnd2["reviewers"][0]["id"] == "ext11-standards", rnd2["reviewers"])


def t27_never_choose_a_reviewer_absent_from_the_blocked_round():
    # [CP5-T27] A finding naming a reviewer id the blocked round itself never listed (corrupted/bad data) is
    # never a fix-round candidate: the round falls back to the existing rules instead (here: the full set,
    # since the bogus id can't even be placed in a lens to narrow by), and fix_round_skipped says why.
    d = _start("t27", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    of_path = d / f".ratchet/runs/f/evidence/CP1/adversarial/round-{rnd1['round']:02d}/open-findings.json"
    findings = json.loads(of_path.read_text())
    assert findings and findings[0]["reviewer"] == rid, findings
    findings[0]["reviewer"] = "ghost-correctness"  # a reviewer id this round never listed
    of_path.write_text(json.dumps(findings))
    t = g.rx(d, "triage", "CP1", 'ghost-correctness/M1=fix:"real for the users"')
    assert t.returncode == 0, t.stdout + t.stderr
    _fix(d, 3, "t27")
    rnd2 = _open(d)
    check("[CP5-T27] the round never ties to the bogus reviewer id: not a fix round",
          rnd2.get("fix_round") is False, rnd2)
    check("[CP5-T27] fix_round_skipped names why (the reviewer isn't in the blocked round's own list)",
          bool(rnd2.get("fix_round_skipped")) and "ghost-correctness" in rnd2["fix_round_skipped"], rnd2)
    check("[CP5-T27] the step's own rule applies instead -- here, the full set (the bogus id can't be placed "
          "in a lens to narrow by, so nothing narrows)",
          sorted(rv["id"] for rv in rnd2["reviewers"]) == sorted(rv["id"] for rv in rnd1["reviewers"]),
          rnd2["reviewers"])


def t28_a_high_risk_outside_blocker_is_re_checked_outside_the_host():
    # [CP5-T28] High risk does not change who the fix round ties to: an outside provider's own blocking
    # finding in a high-risk checkpoint still re-checks that SAME outside provider, never the host, exactly as
    # at normal risk (CP5-F10).
    with _fresh_home():
        finding = _finding("A1")
        b = _fake_reply_bin("ext13", json.dumps({"findings": [finding]}))
        env = _fresh_env(b)
        d = g.project("t28", extra_cp={"risk": "high", "risk_reason": "touches the payment path"},
                      config=["review_depth=" + json.dumps({"high": ["host-standards", "ext13-correctness"]})])
        r = g.rx(d, "providers", "--add", "ext13", "--command", json.dumps([str(b / "ext13")]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd1 = tp.open_round(d, env=env)
        check("[CP5-T28] (fixture sanity) round 1 is the high-risk pair: host-standards and ext13-correctness",
              sorted(rv["id"] for rv in rnd1["reviewers"]) == ["ext13-correctness", "host-standards"],
              rnd1["reviewers"])
        ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext13-correctness")
        p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext13-correctness", "--cp", "CP1",
                            "--out", ext_row["output"]], cwd=d, capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr + p.stdout
        host_path = g.write_review(d, rnd1, "host-standards", [])
        r = g.rx(d, "adv-record", "CP1", "--reviews", ext_row["output"], host_path, env=env)
        assert r.returncode == 2, r.stdout + r.stderr
        t = g.rx(d, "triage", "CP1", f'ext13-correctness/{finding["id"]}=fix:"real for the users"', env=env)
        assert t.returncode == 0, t.stdout + t.stderr
        (d / "src/f.js").write_text("export const f = () => 1; // t28 fixed\n")
        r = g.rx(d, "behavior", "CP1", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        rnd2 = tp.open_round(d, env=env)
        check("[CP5-T28] round 2 is a fix round naming exactly one reviewer",
              rnd2.get("fix_round") is True and len(rnd2["reviewers"]) == 1, rnd2)
        check("[CP5-T28] ...and it is the outside provider (ext13-correctness), never the host, even at high "
              "risk", rnd2["reviewers"][0]["id"] == "ext13-correctness", rnd2["reviewers"])


def t29_only_a_fix_triaged_reviewer_is_ever_chosen_even_with_a_lower_slot():
    # [CP5-T29] When the findings being fixed came from two reviewers, only the one triaged `fix` is ever a
    # candidate -- a reviewer whose finding was triaged `rebut` is excluded from the pool entirely, not merely
    # deprioritized, even when it has the LOWER slot number (which would otherwise win the tie-break).
    d = _start("t29")
    rnd1 = _open(d)
    standards_id = _pick(rnd1, "standards")  # slot 1, the lower slot
    correctness_id = _pick(rnd1, "correctness")  # slot 2
    by = {standards_id: [_finding("M1")], correctness_id: [_finding("C1")]}
    paths = [g.write_review(d, rnd1, rv["id"], by.get(rv["id"], [])) for rv in rnd1["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    t1 = g.rx(d, "triage", "CP1", f'{standards_id}/M1=rebut:"not reachable by a real user"')
    assert t1.returncode == 0, t1.stdout + t1.stderr
    t2 = g.rx(d, "triage", "CP1", f'{correctness_id}/C1=fix:"real for the users"')
    assert t2.returncode == 0, t2.stdout + t2.stderr
    _fix(d, 3, "t29")
    rnd2 = _open(d)
    _is_fix_round("CP5-T29", d, rnd2, correctness_id, [f"{correctness_id}/C1"],
                  "the rebutted reviewer (standards, slot 1) is excluded from the pool; the fix-triaged one "
                  "(correctness, slot 2) is chosen despite its higher slot number")


# ------------------------------------------------------------------------------------------------ CP5-T cases,
# continued: direct behavioral evidence for the remaining approved-plan ids (T01, T02, T04, T05, T06, T07, T10,
# T16, T18, T19, T20, T21, T22, T24, T26) that CP5-F01..F16 above prove but never tag with their own [CP5-Tnn]
# check -- the director's generated report only counts an id actually tagged on a check(), not a prose mapping,
# so each of these reuses the existing CP5-F fixtures/helpers to assert the SAME behavior again, tagged under
# its own approved-plan id.

def t01_one_reviewer_after_a_block_and_fix():
    # [CP5-T01] After a round blocks and the fixer changes the code, the next round names exactly one
    # reviewer -- the basic case CP5-F01/F02 already drive in more specific variants.
    d = _start("t01req")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    keys = _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t01req")
    _is_fix_round("CP5-T01", d, _open(d), rid, keys, "a block followed by a fix opens a one-reviewer fix round")


def t02_one_reviewer_regardless_of_risk_or_fix_size():
    # [CP5-T02] The one-reviewer rule holds across the full risk x fix-size matrix (CP5-F01 normal/large,
    # CP5-F03 high/small, CP5-F04 high/large asserted this already; this drives all four combinations itself
    # under its own id, adding the missing normal/small cell).
    for risk, lines in (("normal", 25), ("normal", 3), ("high", 2), ("high", 30)):
        d = _start(f"t02req-{risk}-{lines}", risk=risk)
        rnd1 = _open(d)
        rid = rnd1["reviewers"][0]["id"] if risk == "high" else _pick(rnd1, "standards")
        _block(d, rnd1, {rid: [_finding("M1")]})
        _fix(d, lines, f"t02req_{risk}_{lines}")  # underscores only: this becomes a JS identifier in _fix
        rnd2 = _open(d)
        check(f"[CP5-T02] {risk} risk, a {lines}-line fix: exactly one reviewer, a fix round",
              len(rnd2["reviewers"]) == 1 and rnd2.get("fix_round") is True, rnd2["reviewers"])


def t04_first_round_keeps_the_full_set():
    # [CP5-T04] Round 1 always keeps its full reviewer set and is never labelled a fix round, at both normal
    # and high risk (CP5-F05's own scenario, re-asserted under the approved id).
    for risk in ("normal", "high"):
        d = _start(f"t04req-{risk}", risk=risk)
        rnd1 = _open(d)
        check(f"[CP5-T04] {risk} risk: round 1 names more than one reviewer (the full set)",
              len(rnd1["reviewers"]) > 1, rnd1["reviewers"])
        _not_fix_round("CP5-T04", rnd1, f"{risk} risk round 1")


def t05_no_fix_means_no_fix_round():
    # [CP5-T05] A blocked round whose only finding was rebutted, with no code change, never ties a fix round
    # to it -- no fixer ever ran (CP5-F06's third sub-case, re-asserted under the approved id).
    d = _start("t05req")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, decision="rebut")
    rnd2 = _open(d)
    _not_fix_round("CP5-T05", rnd2, "every finding rebutted, no fixer run: no fix round")


def t06_post_approval_edits_are_not_fix_rounds():
    # [CP5-T06] A code change made AFTER a clean approval (not after a block) opens the step's normal full
    # round, never a fix round (CP5-F06's second sub-case, re-asserted under the approved id).
    d = _start("t06req")
    rnd1 = _open(d)
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    _fix(d, 30, "t06req")
    rnd2 = _open(d)
    _not_fix_round("CP5-T06", rnd2, "a post-approval edit opens the normal full round, never a fix round")
    check("[CP5-T06] round 2 after approval names the full reviewer set, same as round 1",
          sorted(rv["id"] for rv in rnd2["reviewers"]) == sorted(rv["id"] for rv in rnd1["reviewers"]),
          rnd2["reviewers"])


def t07_small_fix_after_approval_remains_distinct_from_a_fix_round():
    # [CP5-T07] The existing small-fix-after-approval path (CP3/CP5) still opens as `small_fix`, never
    # `fix_round` -- the two mechanisms stay distinct (CP5-F12 shows this at length; this asserts it directly
    # and minimally under the approved id).
    d = _start("t07req")
    rnd1 = _open(d)
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    _append_lines(d / "src/f.js", 5, "t07req")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = _open(d)
    check("[CP5-T07] a small fix after approval opens small_fix=True, fix_round=False, one both-lens reviewer",
          rnd2.get("small_fix") is True and rnd2.get("fix_round") is False and len(rnd2["reviewers"]) == 1,
          rnd2)


def t10_pinned_reviewer_lists_retain_pinned_behavior():
    # [CP5-T10] A step with a pinned `reviewers` list never gets a fix round: after a block and a fix, it
    # keeps the old narrow/full convergence behavior (CP5-F11's own scenario, re-asserted under the approved
    # id).
    d = g.project("t10req", extra_cp={"risk": "normal", "reviewers": ["host-standards", "host-correctness"]},
                  config=["checkin_round=99"])
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = _open(d)
    standards_id = _pick(rnd1, "standards")
    _block(d, rnd1, {standards_id: [_finding("M1")]})
    _fix(d, 3, "t10req")
    rnd2 = _open(d)
    _not_fix_round("CP5-T10", rnd2, "a pinned-reviewer step never gets a fix round")
    check("[CP5-T10] round 2 narrows to the one lens with an open finding, the old pinned behavior",
          len(rnd2["reviewers"]) == 1 and rnd2["reviewers"][0]["id"] == standards_id, rnd2["reviewers"])


def t16_another_fix_round_after_a_blocked_fix_round_still_has_one_reviewer():
    # [CP5-T16] A fix round that blocks again is followed by ANOTHER one-reviewer fix round, tied to the same
    # reviewer, never the full set (CP5-F08's own scenario, re-asserted under the approved id).
    d = _start("t16req", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t16req")
    rnd2 = _open(d)
    check("[CP5-T16] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    keys2 = _block(d, rnd2, {rid: [_finding("M1")]})
    _fix(d, 4, "t16reqb")
    rnd3 = _open(d)
    _is_fix_round("CP5-T16", d, rnd3, rid, keys2, "a fix round that blocks again stays a one-reviewer fix round")


def t18_status_and_commit_identify_fix_rounds_and_reviewers():
    # [CP5-T18] `status --json`, plain `status` and the checkpoint's own commit message all name a fix round
    # by round number and its actual reviewer (CP5-F15's own scenario, re-asserted under the approved id).
    d = _start("t18req", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t18req")
    rnd2 = _open(d)
    check("[CP5-T18] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    r = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd2, rid, []))
    assert r.returncode == 0, r.stdout + r.stderr
    expect = f"CP1: round 2 was reviewed as a fix round by {rid} (approved)."
    sj = json.loads(g.rx(d, "status", "--json").stdout)
    check("[CP5-T18] status --json names the fix round and its reviewer",
          expect in (sj.get("fix_round_reviews") or []), sj.get("fix_round_reviews"))
    txt = g.rx(d, "status").stdout
    check("[CP5-T18] plain status names it too", expect in txt, txt)
    rc = g.rx(d, "complete", "CP1")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    msg = g.git(d, "log", "-1", "--format=%B").stdout
    check("[CP5-T18] the commit message names the fix round and its reviewer too", expect in msg, msg)


def t19_status_identifies_a_blocked_reviewed_fix_round():
    # [CP5-T19] `status --json` also names a fix round that was reviewed and BLOCKED, not just an approved one
    # (CP5-F15's own scenario, re-asserted under the approved id).
    d = _start("t19req", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 3, "t19req")
    rnd2 = _open(d)
    check("[CP5-T19] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    paths = [g.write_review(d, rnd2, rv["id"], [_finding("M1")]) for rv in rnd2["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    expect = f"CP1: round 2 was reviewed as a fix round by {rid} (blocked)."
    sj = json.loads(g.rx(d, "status", "--json").stdout)
    check("[CP5-T19] status --json names the BLOCKED fix round and its reviewer",
          expect in (sj.get("fix_round_reviews") or []), sj.get("fix_round_reviews"))


def t20_fix_round_approval_does_not_reset_the_full_review_baseline():
    # [CP5-T20] A fix round's own clean approval never advances last_full_review_fingerprint (CP5-F12's own
    # scenario, re-asserted minimally under the approved id).
    d = _start("t20req")
    rnd1 = _open(d)
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    baseline = _state(d)["checkpoints"]["CP1"].get("last_full_review_fingerprint")
    check("[CP5-T20] (fixture sanity) round 1's approval set the full-review baseline",
          bool((baseline or {}).get("id")), baseline)
    _fix(d, 30, "t20req")  # over small_edit_lines: the normal full round, never a small fix
    rnd2 = _open(d)
    check("[CP5-T20] (fixture sanity) round 2 is the step's normal full round",
          len(rnd2["reviewers"]) > 1 and rnd2.get("small_fix") is not True, rnd2)
    rid = _pick(rnd2, "standards")
    _block(d, rnd2, {rid: [_finding("M1")]})
    _fix(d, 3, "t20reqb")
    rnd3 = _open(d)
    check("[CP5-T20] (fixture sanity) round 3 is a fix round", rnd3.get("fix_round") is True, rnd3)
    r = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd3, rnd3["reviewers"][0]["id"], []))
    assert r.returncode == 0, r.stdout + r.stderr
    check("[CP5-T20] the fix round's approval never advances last_full_review_fingerprint, still round 1's",
          _state(d)["checkpoints"]["CP1"].get("last_full_review_fingerprint") == baseline, _state(d))


def t21_replacing_an_unreviewed_fix_round_preserves_selection():
    # [CP5-T21] A fix round replaced before anyone reviewed it (the fixer edited again) stays a fix round,
    # tied to the same reviewer and findings (CP5-F09's own scenario, re-asserted under the approved id).
    d = _start("t21req")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    keys = _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 4, "t21req")
    rnd2 = _open(d)
    check("[CP5-T21] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    _fix(d, 30, "t21reqb")  # replace round 2 before anyone reviews it
    rnd3 = _open(d)
    check("[CP5-T21] replacing an unreviewed fix round still ties to the SAME reviewer",
          rnd3.get("fix_round") is True and [rv["id"] for rv in rnd3["reviewers"]] == [rid], rnd3)
    ff = rnd3.get("fix_for") or {}
    check("[CP5-T21] fix_for still names round 1 and the same findings being fixed",
          ff.get("round") == 1 and sorted(ff.get("findings") or []) == sorted(keys), ff)


def t22_mid_round_failure_uses_normal_fallback_and_stays_one_reviewer():
    # [CP5-T22] When the fix round's tied reviewer fails MID-ROUND and the same-round refill fills its slot,
    # the normal fallback order picks the stand-in and the round is still exactly one reviewer (CP5-F16's own
    # scenario, re-asserted under the approved id).
    with _fresh_home():
        finding = _finding("A1")
        b = _fake_reply_bin("ext14", json.dumps({"findings": [finding]}))
        env = _fresh_env(b)
        d = g.project("t22req", extra_cp={"risk": "normal"},
                      config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext14-correctness"]})])
        r = g.rx(d, "providers", "--add", "ext14", "--command", json.dumps([str(b / "ext14")]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd1 = tp.open_round(d, env=env)
        ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext14-correctness")
        p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext14-correctness", "--cp", "CP1",
                            "--out", ext_row["output"]], cwd=d, capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr + p.stdout
        host_path = g.write_review(d, rnd1, "host-standards", [])
        r = g.rx(d, "adv-record", "CP1", "--reviews", ext_row["output"], host_path, env=env)
        assert r.returncode == 2, r.stdout + r.stderr
        t = g.rx(d, "triage", "CP1", f'ext14-correctness/{finding["id"]}=fix:"real for the users"', env=env)
        assert t.returncode == 0, t.stdout + t.stderr
        (d / "src/f.js").write_text("export const f = () => 1; // t22req fixed\n")
        r = g.rx(d, "behavior", "CP1", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        rnd2 = tp.open_round(d, env=env)
        check("[CP5-T22] (fixture sanity) round 2 is a fix round tied to ext14-correctness",
              rnd2.get("fix_round") is True and rnd2["reviewers"][0]["id"] == "ext14-correctness", rnd2)
        m = g.rx(d, "reviewer-status", "--mark", "ext14-correctness", "--reason", "auth expired mid-round",
                 env=env)
        assert m.returncode == 0, m.stdout + m.stderr
        rnd2b = tp.open_round(d, env=env)
        check("[CP5-T22] mid-round failure refills the SAME round with the normal fallback "
              "(host-alt-correctness), still exactly one reviewer, still a fix round",
              rnd2b["round"] == rnd2["round"] and rnd2b.get("fix_round") is True
              and len(rnd2b["reviewers"]) == 1 and rnd2b["reviewers"][0]["id"] == "host-alt-correctness", rnd2b)


def t24_when_host_and_another_company_both_block_the_other_company_re_checks():
    # [CP5-T24] When the findings being fixed came from both the host and an outside company, the OUTSIDE
    # company is preferred even at a higher slot number (CP5-F10's own scenario, re-asserted under the
    # approved id).
    with _fresh_home():
        finding_h, finding_e = _finding("H1"), _finding("E1")
        b = _fake_reply_bin("ext15", json.dumps({"findings": [finding_e]}))
        env = _fresh_env(b)
        d = g.project("t24req", extra_cp={"risk": "normal"},
                      config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext15-correctness"]})])
        r = g.rx(d, "providers", "--add", "ext15", "--command", json.dumps([str(b / "ext15")]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd1 = tp.open_round(d, env=env)
        ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext15-correctness")
        p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext15-correctness", "--cp", "CP1",
                            "--out", ext_row["output"]], cwd=d, capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr + p.stdout
        host_path = g.write_review(d, rnd1, "host-standards", [finding_h])
        r = g.rx(d, "adv-record", "CP1", "--reviews", ext_row["output"], host_path, env=env)
        assert r.returncode == 2, r.stdout + r.stderr
        keys = sorted([f"ext15-correctness/{finding_e['id']}", f"host-standards/{finding_h['id']}"])
        for k in keys:
            t = g.rx(d, "triage", "CP1", f'{k}=fix:"real for the users"', env=env)
            assert t.returncode == 0, t.stdout + t.stderr
        (d / "src/f.js").write_text("export const f = () => 1; // t24req fixed\n")
        r = g.rx(d, "behavior", "CP1", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        rnd2 = tp.open_round(d, env=env)
        check("[CP5-T24] round 2 is a fix round naming exactly one reviewer",
              rnd2.get("fix_round") is True and len(rnd2["reviewers"]) == 1, rnd2)
        check("[CP5-T24] when host and another company both blocked, the OTHER COMPANY re-checks, not the host",
              rnd2["reviewers"][0]["id"] == "ext15-correctness", rnd2["reviewers"])


def t26_an_unavailable_choice_uses_that_slots_normal_fallback():
    # [CP5-T26] When the reviewer a fix round is tied to is marked unavailable BEFORE the round is even
    # opened (not mid-round, see CP5-T22/F16), its own slot's normal fallback order picks the stand-in, never
    # a different reviewer's slot.
    with _fresh_home():
        finding = _finding("A1")
        b = _fake_reply_bin("ext16", json.dumps({"findings": [finding]}))
        env = _fresh_env(b)
        d = g.project("t26req", extra_cp={"risk": "normal"},
                      config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext16-correctness"]})])
        r = g.rx(d, "providers", "--add", "ext16", "--command", json.dumps([str(b / "ext16")]), "--stdin",
                 "--company", "openai", "--model", "gpt-5", env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd1 = tp.open_round(d, env=env)
        ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext16-correctness")
        p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext16-correctness", "--cp", "CP1",
                            "--out", ext_row["output"]], cwd=d, capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr + p.stdout
        host_path = g.write_review(d, rnd1, "host-standards", [])
        r = g.rx(d, "adv-record", "CP1", "--reviews", ext_row["output"], host_path, env=env)
        assert r.returncode == 2, r.stdout + r.stderr
        t = g.rx(d, "triage", "CP1", f'ext16-correctness/{finding["id"]}=fix:"real for the users"', env=env)
        assert t.returncode == 0, t.stdout + t.stderr
        (d / "src/f.js").write_text("export const f = () => 1; // t26req fixed\n")
        r = g.rx(d, "behavior", "CP1", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        # the reviewer the fix round WOULD tie to is marked unavailable before `reviewers` ever opens round 2
        m = g.rx(d, "reviewer-status", "--mark", "ext16-correctness", "--reason", "quota exhausted", env=env)
        assert m.returncode == 0, m.stdout + m.stderr
        rnd2 = tp.open_round(d, env=env)
        check("[CP5-T26] the fix round's tied slot is filled by its own normal fallback (host-alt-correctness) "
              "since the tied choice (ext16-correctness) is already unavailable when the round opens, never a "
              "different reviewer's slot, still exactly one reviewer",
              rnd2.get("fix_round") is True and len(rnd2["reviewers"]) == 1
              and rnd2["reviewers"][0]["id"] == "host-alt-correctness", rnd2)


CASES = [("CP5-F01", f01_normal_risk_large_fix), ("CP5-F02", f02_reviewer_tied_to_the_finding),
         ("CP5-F03", f03_high_risk_small_fix), ("CP5-F04", f04_high_risk_large_fix),
         ("CP5-F05", f05_first_round_keeps_full_set), ("CP5-F06", f06_rounds_not_following_a_block),
         ("CP5-F07", f07_two_reviewers_tied_by_lowest_slot_when_neither_differs_from_host),
         ("CP5-F08", f08_repeated_blocked_fix_rounds_never_revert_to_the_full_set),
         ("CP5-F09", f09_replaced_fix_round_stays_a_fix_round),
         ("CP5-F10", f10_prefers_a_different_company_than_the_host_over_a_lower_slot),
         ("CP5-F11", f11_pinned_reviewer_list_is_the_approved_exception),
         ("CP5-F12", f12_fix_round_approval_never_advances_the_full_review_baseline),
         ("CP5-F13", f13_narrowed_round_that_blocks_then_gets_a_real_fix_becomes_a_fix_round),
         ("CP5-F14", f14_pinned_reviewer_exception_holds_for_the_narrowed_then_fixed_sequence),
         ("CP5-F15", f15_fix_round_evidence_in_status_and_commit_message),
         ("CP5-F16", f16_fix_round_evidence_names_the_replacement_reviewer),
         ("CP5-T01", t01_one_reviewer_after_a_block_and_fix),
         ("CP5-T02", t02_one_reviewer_regardless_of_risk_or_fix_size),
         ("CP5-T03", t03_outside_blocker_fix_round_ties_to_the_same_provider_and_lens_without_the_host),
         ("CP5-T04", t04_first_round_keeps_the_full_set),
         ("CP5-T05", t05_no_fix_means_no_fix_round),
         ("CP5-T06", t06_post_approval_edits_are_not_fix_rounds),
         ("CP5-T07", t07_small_fix_after_approval_remains_distinct_from_a_fix_round),
         ("CP5-T08", t08_accepting_a_finding_never_creates_a_fix_round),
         ("CP5-T09", t09_low_risk_settings_do_not_override_fix_round_selection),
         ("CP5-T10", t10_pinned_reviewer_lists_retain_pinned_behavior),
         ("CP5-T11", t11_fix_rounds_count_toward_step_and_run_budgets),
         ("CP5-T12", t12_the_step_round_limit_still_blocks_through_fix_rounds),
         ("CP5-T13", t13_the_run_wide_round_limit_still_blocks_through_fix_rounds),
         ("CP5-T14", t14_an_approved_raise_grants_exactly_one_further_fix_round),
         ("CP5-T15", t15_a_repeat_block_through_fix_rounds_still_triggers_the_usual_check_in),
         ("CP5-T16", t16_another_fix_round_after_a_blocked_fix_round_still_has_one_reviewer),
         ("CP5-T17", t17_fix_round_approval_passes_the_gate_and_complete_still_runs_tests_and_the_full_suite),
         ("CP5-T18", t18_status_and_commit_identify_fix_rounds_and_reviewers),
         ("CP5-T19", t19_status_identifies_a_blocked_reviewed_fix_round),
         ("CP5-T20", t20_fix_round_approval_does_not_reset_the_full_review_baseline),
         ("CP5-T21", t21_replacing_an_unreviewed_fix_round_preserves_selection),
         ("CP5-T22", t22_mid_round_failure_uses_normal_fallback_and_stays_one_reviewer),
         ("CP5-T23", t23_docs_explain_the_fix_round_rule),
         ("CP5-T24", t24_when_host_and_another_company_both_block_the_other_company_re_checks),
         ("CP5-T25", t25_two_outside_reviewers_block_the_earlier_one_in_the_round_wins),
         ("CP5-T26", t26_an_unavailable_choice_uses_that_slots_normal_fallback),
         ("CP5-T27", t27_never_choose_a_reviewer_absent_from_the_blocked_round),
         ("CP5-T28", t28_a_high_risk_outside_blocker_is_re_checked_outside_the_host),
         ("CP5-T29", t29_only_a_fix_triaged_reviewer_is_ever_chosen_even_with_a_lower_slot)]


def main():
    for cid, fn in CASES:
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception:
            check(f"[{cid}] ran without an exception", False, traceback.format_exc())
    print(f"\n{PASS} passed, {FAIL} failed")
    rep = os.environ.get("RATCHET_TEST_REPORT")
    if rep:
        cases = [{"id": c, "status": "passed" if ok_ else "failed",
                  "message": "; ".join(CASE_MESSAGES.get(c, []))} for c, ok_ in sorted(CASE_STATUS.items())]
        Path(rep).write_text(json.dumps({"cases": cases}))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
