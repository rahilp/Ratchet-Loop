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

Black-box: every case drives the real ratchet.py CLI in a throwaway git project through tests/test_gates.py's
helpers (`project`, `approve_plan`, `to_adversarial`, `write_review`, `rx`), with RATCHET_HOME in a temp folder and
no other-provider CLI configured (providers=[]), so only the host's own reviewer ids are used, except CP5-F10
which registers one real command-kind provider (tests/test_providers.py's `fake_bin`/`env_with`) to exercise the
different-company preference.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-F01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

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
ID_RE = re.compile(r"\[(CP5-F\d+)\]")


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
    """Changed lines in `path` between the tree round `rnd` reviewed and the working tree (added + removed)."""
    old = g.git(d, "show", f"{rnd['fingerprint']}:{path}").stdout.splitlines()
    new = (Path(d) / path).read_text().splitlines()
    return sum(1 for ln in difflib.ndiff(old, new) if ln[:2] in ("+ ", "- "))


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
    # Regression test for the full-review-baseline rule: a one-reviewer fix round's clean approval must not set
    # last_full_review_fingerprint (what a later small fix measures against) and must not clear an existing
    # lighter-review note (small_fix_review here), unlike a full round's own approval.
    d = _start("f12")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    full_fp_before = _state(d)["checkpoints"]["CP1"].get("last_full_review_fingerprint")
    check("[CP5-F12] (fixture sanity) round 1's last_full_review_fingerprint is not yet set "
          "(no FULL round has been approved)", full_fp_before is None, full_fp_before)
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 5, "f12")
    rnd2 = _open(d)
    check("[CP5-F12] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    path = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    assert r.returncode == 0, r.stdout + r.stderr
    cps_after = _state(d)["checkpoints"]["CP1"]
    check("[CP5-F12] after the fix round's clean approval, last_full_review_fingerprint is STILL not set",
          cps_after.get("last_full_review_fingerprint") is None, cps_after)
    check("[CP5-F12] ...and small_fix_review is not set either (a fix round is not a small-fix round)",
          not cps_after.get("small_fix_review"), cps_after)
    msg = g.git(d, "log", "-1", "--format=%B").stdout
    check("[CP5-F12] the commit message does not claim a small-fix one-reviewer review for this round",
          "small fix" not in msg.lower(), msg)


CASES = [("CP5-F01", f01_normal_risk_large_fix), ("CP5-F02", f02_reviewer_tied_to_the_finding),
         ("CP5-F03", f03_high_risk_small_fix), ("CP5-F04", f04_high_risk_large_fix),
         ("CP5-F05", f05_first_round_keeps_full_set), ("CP5-F06", f06_rounds_not_following_a_block),
         ("CP5-F07", f07_two_reviewers_tied_by_lowest_slot_when_neither_differs_from_host),
         ("CP5-F08", f08_repeated_blocked_fix_rounds_never_revert_to_the_full_set),
         ("CP5-F09", f09_replaced_fix_round_stays_a_fix_round),
         ("CP5-F10", f10_prefers_a_different_company_than_the_host_over_a_lower_slot),
         ("CP5-F11", f11_pinned_reviewer_list_is_the_approved_exception),
         ("CP5-F12", f12_fix_round_approval_never_advances_the_full_review_baseline)]


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
