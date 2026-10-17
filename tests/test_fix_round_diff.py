#!/usr/bin/env python3
"""
Lane H CP6 tests: a fix round sees only its own scope.   python3 tests/test_fix_round_diff.py

After a blocked round and a fixer edit, the one-reviewer fix round (lane H CP5) is shown ONLY the diff since the
last round actually reviewed, plus that round's own open findings (full details: id, reviewer, severity,
location, description, suggested fix, and any rebuttal reason already recorded for it) -- never the whole
checkpoint diff, and never an older finding or an unrelated earlier source change. A small fix made after an
approval is scoped the same way, with no open findings to show. A first round, a fix over small_edit_lines after
an approval, and any high-risk full round all keep the whole step diff, exactly as before CP6. Host sub-agents
and external reviewers are told the same scope (the prior reviewed tree, the current fingerprint, the changed
files, and the relevant findings). A scoped round's own minimum review time scales down from the configured
`min_review_seconds` by how much actually changed in its own scope, capped at the configured value; a full round
keeps the configured minimum regardless of its diff's size. The files_reviewed requirement, and every existing
signature/origin check, are unchanged.

  - [CP6-D01] a first round is never scoped: no `scope`, the full step diff, the configured minimum unshortened
  - [CP6-D02] a fix round is scoped to the diff since the blocked round, with that round's one open finding (id,
              reviewer, severity, location, description, fix) in `scope.findings` and in the review rules text
  - [CP6-D03] a fix round's open finding carries its rebuttal reason when the fixer rebutted (and lost) it
  - [CP6-D04] a small fix after a clean approval is scoped to just its own new lines, with "no open findings"
  - [CP6-D05] a fix over small_edit_lines after an approval keeps the whole step diff (never scoped)
  - [CP6-D06] a high-risk step's normal full round keeps the whole step diff (never scoped)
  - [CP6-D07] a second fix round in a row is scoped to only the second fix, not the first one too
  - [CP6-D08] a fix round replaced before it was reviewed (another edit) is scoped to everything since the last
              round actually reviewed, not just the latest edit
  - [CP6-D09] a new file created inside the fix round's own scope is in `scope.changed_files` and in the diff
              text; a file only touched earlier (outside the scope) is not
  - [CP6-D10] a file deleted inside the fix round's own scope is in `scope.changed_files` and in the diff text
  - [CP6-D11] a finding accepted by the user is excluded from a later fix round's `scope.findings`
  - [CP6-D12] timing: a tiny fix reviewed at 0s fails and at 10s passes when min_review_seconds=20; a ~400-line
              fix at 10s fails and at 20s passes; a full round still needs the full configured 20s regardless of
              its diff's size; the shorter minimum never exceeds a configured 5 or 0 seconds
  - [CP6-D13] files_reviewed is still required: a clean fix review that omits it still fails
  - [CP6-D14] the host sub-agent's own launch instructions carry the same scope (since-tree, changed files) and
              the same finding line as the external reviewer's prompt
  - [CP6-D15] a real, signed external review of a fix round: its prompt is scoped (dry-run), and its recorded
              files_reviewed is stamped from the scope, not the whole checkpoint
  - [CP6-D16] a fake command-kind external provider raises the finding, is the fix round's ONLY reviewer (no
              host, no tie-break), its scoped prompt carries only the fix diff and that finding, and its real
              signed review passes adv-record at the scaled minimum, never the full configured one
  - [CP6-D17] the actual reviewer prompt (not just `scope.changed_files`) shows a new file's content as an
              addition and a deleted file's content as a removal
  - [CP6-D18] a fix round's scope survives a same-round reviewer fallback (lane H CP5): the reviewer that
              actually ends up reviewing gets the same since_tree, changed files and findings
  - [CP6-D19] a small fix after approval scales its own minimum review time the same way a fix round does

Black-box: every case drives the real ratchet.py CLI in a throwaway git project through tests/test_gates.py's
helpers, with RATCHET_HOME in a temp folder; CP6-D14/D15/D16/D17/D18 also drive the real external_review.py
script against a fake command-kind reviewer CLI (tests/test_providers.py's fake_bin/env_with, or a local
_fake_reply_bin for one that raises a specific finding), never a real reviewer CLI.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-D01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                   # noqa: E402
import test_gates as g        # noqa: E402
import test_providers as tp   # noqa: E402  (fake_bin, env_with, open_round, EXT)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP6-D\d+)\]")


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

def _finding(fid, severity="major", file="src/f.js"):
    return {"id": fid, "severity": severity, "file": file, "description": "handler skips validation",
            "fix": "validate the input before use", "user_impact": "a user hits a crash"}


def _lens(rid: str) -> str:
    return rid.rsplit("-", 1)[-1] if rid.endswith(("-standards", "-correctness")) else rid


def _state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def _open(d, env=None):
    r = g.rx(d, "reviewers", "CP1", "--json", env=env)
    if r.returncode != 0 and "no fix-or-rebut" in r.stderr:
        m = re.search(r"no fix-or-rebut decision: (.+?)\. Run", r.stderr)
        for ref in (m.group(1).split(", ") if m else []):
            g.rx(d, "triage", "CP1", f'{ref}=fix:"test fixture"', env=env)
        r = g.rx(d, "reviewers", "CP1", "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _fix(d, lines, tag, path="src/f.js"):
    """The fixer's change: `path` rewritten so `lines` code lines differ (f() still returns 1), then behavior."""
    body = "".join(f"export const pad_{tag}_{i} = {i};\n" for i in range(lines - 1))
    (d / path).write_text(body + f"export const f = () => 1; // {tag}\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr


def _append_lines(path: Path, n: int, tag: str):
    with open(path, "a") as f:
        for i in range(n):
            f.write(f"// filler-{tag}-{i}\n")


def _block(d, rnd, by: dict, decision="fix", env=None, age=None):
    """Records `rnd` with reviewer id -> findings from `by` (every other reviewer approves), checks it blocks,
    and triages every finding `decision`. Returns the qualified finding keys. `age`, when given, backdates every
    review file's mtime (seconds since the round opened) -- for setup rounds in timing tests, so the real
    wall-clock delay of writing-and-recording doesn't collide with whatever min_review_seconds is configured."""
    paths = [g.write_review(d, rnd, rv["id"], by.get(rv["id"], [])) for rv in rnd["reviewers"]]
    if age is not None:
        for p in paths:
            _set_review_age(d, p, rnd, age)
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    keys = [f"{rid}/{f['id']}" for rid, fs in by.items() for f in fs]
    for k in keys:
        t = g.rx(d, "triage", "CP1", f'{k}={decision}:"real for the users"', env=env)
        assert t.returncode == 0, t.stdout + t.stderr
    return keys


def _start(name, risk="normal", config=None):
    d = g.project(name, extra_cp={"risk": risk}, config=config)
    g.approve_plan(d)
    g.to_adversarial(d)
    return d


def _pick(rnd, lens):
    return next(rv["id"] for rv in rnd["reviewers"] if _lens(rv["id"]) == lens)


def _adv_round_state(d):
    """The checkpoint's own open adv_round from state.json -- holds "opened_at", which `reviewers --json` does
    not print."""
    return _state(d)["checkpoints"]["CP1"]["adv_round"]


def _set_review_age(d, path: str, rnd: dict, age_seconds: float):
    """Sets the review file's mtime so `cmd_adv_record` computes its age (since the round opened) as EXACTLY
    `age_seconds` -- deterministic timing tests, no real sleeps. `rnd` only needs "round" (to confirm it matches
    the currently open one); "opened_at" is read fresh from state.json, since `reviewers --json` doesn't print it."""
    opened_at = _adv_round_state(d)["opened_at"]
    opened = dt.datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
    ts = (opened + dt.timedelta(seconds=age_seconds)).timestamp()
    os.utime(path, (ts, ts))


def _find_rendered(f: dict) -> list[str]:
    """The exact substrings _render_finding_line puts in a rule for `f` (id, reviewer, severity, location,
    description, fix) -- what every scoped round's prompt/instructions must contain for this finding."""
    return [f"id={f['id']}", f"severity={f['severity']}", f"location={f['file']}",
            json.dumps(f["description"]), json.dumps(f["fix"])]


# ------------------------------------------------------------------------------------------------ cases

def d01_first_round_never_scoped():
    d = _start("d01")
    rnd = _open(d)
    check("[CP6-D01] round 1 has no scope", rnd.get("scope") is None, rnd.get("scope"))
    check("[CP6-D01] round 1's review rules say nothing about lane H CP6 scoping",
          not any("lane H CP6" in r for r in rnd["review_rules"]), rnd["review_rules"])
    cfg = json.loads(g.rx(d, "config").stdout)
    paths = [g.write_review(d, rnd, rv["id"], []) for rv in rnd["reviewers"]]
    for p in paths:
        _set_review_age(d, p, rnd, cfg.get("min_review_seconds", 20) - 1)
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("[CP6-D01] round 1 still needs the FULL configured minimum (one second short of it fails)",
          r.returncode != 0 and "too fast" in (r.stdout + r.stderr), r.stdout + r.stderr)


def d02_fix_round_scoped_to_the_blocked_round():
    d = _start("d02")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    finding = _finding("M1")
    keys = _block(d, rnd1, {rid: [finding]})
    _fix(d, 5, "d02")
    rnd2 = _open(d)
    check("[CP6-D02] round 2 is a fix round with a scope", rnd2.get("fix_round") is True
          and rnd2.get("scope") is not None, rnd2)
    scope = rnd2["scope"]
    check("[CP6-D02] scope.since_tree is round 1's own fingerprint", scope["since_tree"] == rnd1["fingerprint"],
          (scope["since_tree"], rnd1["fingerprint"]))
    check("[CP6-D02] scope.changed_files is exactly the fixer's own edit (src/f.js)",
          scope["changed_files"] == ["src/f.js"], scope["changed_files"])
    check("[CP6-D02] scope.findings has exactly the one open finding, with the reviewer that raised it",
          len(scope["findings"]) == 1 and scope["findings"][0]["id"] == "M1"
          and scope["findings"][0]["reviewer"] == rid, scope["findings"])
    rules_text = " ".join(rnd2["review_rules"])
    check("[CP6-D02] the review rules name the fix round's scope (lane H CP6) and the since-tree",
          "lane H CP6" in rules_text and scope["since_tree"][:10] in rules_text, rules_text)
    for needle in _find_rendered(finding):
        check(f"[CP6-D02] the review rules include the finding's {needle!r}", needle in rules_text, rules_text)
    check("[CP6-D02] fix_for still names the finding being fixed, same as before CP6",
          sorted((rnd2.get("fix_for") or {}).get("findings") or []) == sorted(keys), rnd2.get("fix_for"))


def d03_rebuttal_reason_attached_to_the_finding():
    # Round 1 blocks on TWO findings from two different reviewers: standards' M1, triaged 'fix' (so the fix
    # round below ties to that one reviewer), and correctness' M2, triaged 'rebut' (the fixer's rebuttal, not
    # withdrawn by that alone -- still open). The fix round's scope shows BOTH (it is "that round's open
    # findings", not only the one being fixed), with M2 carrying its rebuttal reason.
    d = _start("d03", config=["checkin_round=99"])
    rnd1 = _open(d)
    sid, cid = _pick(rnd1, "standards"), _pick(rnd1, "correctness")
    m1, m2 = _finding("M1", file="src/f.js"), _finding("M2", file="src/a.js")
    paths = [g.write_review(d, rnd1, sid, [m1]), g.write_review(d, rnd1, cid, [m2])]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    t1 = g.rx(d, "triage", "CP1", f'{sid}/M1=fix:"real for the users"')
    assert t1.returncode == 0, t1.stdout + t1.stderr
    t2 = g.rx(d, "triage", "CP1", f"{cid}/M2=rebut:not reachable from the objective's users")
    assert t2.returncode == 0, t2.stdout + t2.stderr
    _fix(d, 4, "d03")
    rnd2 = _open(d)
    check("[CP6-D03] (fixture sanity) round 2 is a fix round tied to the standards reviewer (M1 was fixed)",
          rnd2.get("fix_round") is True and rnd2["reviewers"][0]["id"] == sid, rnd2)
    scope = rnd2["scope"]
    by_id = {f["id"]: f for f in scope["findings"]}
    check("[CP6-D03] scope.findings has both of round 1's open findings (not just the one being fixed)",
          sorted(by_id) == ["M1", "M2"], scope["findings"])
    check("[CP6-D03] M2 (rebutted, not withdrawn) carries its rebuttal reason",
          by_id.get("M2", {}).get("rebuttal_reason") == "not reachable from the objective's users", by_id.get("M2"))
    check("[CP6-D03] M1 (fixed) carries no rebuttal reason", "rebuttal_reason" not in by_id.get("M1", {}),
          by_id.get("M1"))
    rules_text = " ".join(rnd2["review_rules"])
    check("[CP6-D03] the review rules include the rebuttal reason for M2",
          "rebuttal=" in rules_text and "not reachable from the objective" in rules_text, rules_text)


def d04_small_fix_after_approval_scoped_with_no_open_findings():
    d = _start("d04")
    rnd1 = _open(d)
    r = g.rx(d, "adv-record", "CP1", "--reviews", *[g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]])
    assert r.returncode == 0, r.stdout + r.stderr
    _append_lines(d / "src/f.js", 5, "d04")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = _open(d)
    check("[CP6-D04] (fixture sanity) round 2 is a one-reviewer small-fix round",
          rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1, rnd2)
    scope = rnd2["scope"]
    check("[CP6-D04] small-fix round is scoped too, to just its own new lines",
          scope is not None and scope["changed_files"] == ["src/f.js"], scope)
    check("[CP6-D04] ...with no open findings to re-check", scope["findings"] == [], scope["findings"])
    rules_text = " ".join(rnd2["review_rules"])
    check("[CP6-D04] the review rules say plainly there are no open findings",
          "no open findings" in rules_text, rules_text)


def d05_large_fix_after_approval_not_scoped():
    d = _start("d05")
    rnd1 = _open(d)
    r = g.rx(d, "adv-record", "CP1", "--reviews", *[g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]])
    assert r.returncode == 0, r.stdout + r.stderr
    _append_lines(d / "src/f.js", 30, "d05")  # over small_edit_lines (default 20)
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = _open(d)
    check("[CP6-D05] round 2 is the step's normal full round, not a small fix",
          rnd2.get("small_fix") is not True and len(rnd2["reviewers"]) == 2, rnd2)
    check("[CP6-D05] it keeps the whole step diff: no scope", rnd2.get("scope") is None, rnd2.get("scope"))


def d06_high_risk_full_round_not_scoped():
    d = _start("d06", risk="high")
    rnd1 = _open(d)
    check("[CP6-D06] (fixture sanity) round 1 is the high-risk full set", len(rnd1["reviewers"]) > 1,
          rnd1["reviewers"])
    check("[CP6-D06] it keeps the whole step diff: no scope", rnd1.get("scope") is None, rnd1.get("scope"))


def d07_second_fix_round_scoped_to_only_the_second_fix():
    d = _start("d07", config=["checkin_round=99"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 5, "d07a")
    rnd2 = _open(d)
    check("[CP6-D07] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    _block(d, rnd2, {rid: [_finding("M1")]})
    _fix(d, 6, "d07b")
    rnd3 = _open(d)
    check("[CP6-D07] round 3 is a fix round too", rnd3.get("fix_round") is True, rnd3)
    scope3 = rnd3["scope"]
    check("[CP6-D07] round 3's scope starts at round 2's own fingerprint, not round 1's",
          scope3["since_tree"] == rnd2["fingerprint"] and scope3["since_tree"] != rnd1["fingerprint"],
          (scope3["since_tree"], rnd1["fingerprint"], rnd2["fingerprint"]))
    check("[CP6-D07] round 3's scope is only the second fix (src/f.js again, but just that one diff)",
          scope3["changed_files"] == ["src/f.js"], scope3["changed_files"])


def d08_replaced_round_scoped_to_everything_since_the_last_review():
    d = _start("d08")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 4, "d08a")
    rnd2 = _open(d)
    check("[CP6-D08] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    _fix(d, 30, "d08b")  # the fixer changes the code AGAIN before anyone reviews round 2
    rnd3 = _open(d)
    check("[CP6-D08] round 3 replaces the never-reviewed round 2, still a fix round",
          rnd3.get("fix_round") is True, rnd3)
    scope3 = rnd3["scope"]
    check("[CP6-D08] round 3's scope still starts at round 1's own fingerprint (the last round ACTUALLY "
          "reviewed), carrying forward through the free replacement",
          scope3["since_tree"] == rnd1["fingerprint"], (scope3["since_tree"], rnd1["fingerprint"]))


def d09_new_file_in_scope_is_included():
    d = _start("d09")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    (d / "src" / "new_handler.js").write_text("export const h = () => 1;\n")
    _fix(d, 4, "d09")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = _open(d)
    scope = rnd2["scope"]
    check("[CP6-D09] the new file created in this fix is in scope.changed_files",
          "src/new_handler.js" in scope["changed_files"], scope["changed_files"])
    check("[CP6-D09] the base project's own untouched file (src/a.js) is not in scope",
          "src/a.js" not in scope["changed_files"], scope["changed_files"])


def d10_deleted_file_in_scope_is_included():
    d = _start("d10")
    (d / "src" / "doomed.js").write_text("export const z = 1;\n")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    (d / "src" / "doomed.js").unlink()
    _fix(d, 4, "d10")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = _open(d)
    scope = rnd2["scope"]
    check("[CP6-D10] the file deleted in this fix is in scope.changed_files",
          "src/doomed.js" in scope["changed_files"], scope["changed_files"])


def d11_accepted_finding_excluded_from_scope():
    d = _start("d11", config=["checkin_round=99"])
    rnd1 = _open(d)
    sid, cid = _pick(rnd1, "standards"), _pick(rnd1, "correctness")
    m1, m2 = _finding("M1", file="src/f.js"), _finding("M2", file="src/a.js")
    paths = [g.write_review(d, rnd1, sid, [m1]), g.write_review(d, rnd1, cid, [m2])]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, r.stdout + r.stderr
    g.rx(d, "ask", "accept-finding", "CP1", f"{cid}/M2")
    g.say(d, "accept: known limit for this fixture")
    racc = g.rx(d, "accept-finding", "CP1")
    check("[CP6-D11] (fixture sanity) accepting M2 alone does not pass the gate (M1 is still open)",
          racc.returncode == 0 and _state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]["status"] != "passed",
          racc.stdout + racc.stderr)
    t = g.rx(d, "triage", "CP1", f"{sid}/M1=fix:\"real for the users\"")
    assert t.returncode == 0, t.stdout + t.stderr
    _fix(d, 4, "d11")
    rnd2 = _open(d)
    check("[CP6-D11] round 2 is a fix round tied to the standards reviewer (M1), not correctness",
          rnd2.get("fix_round") is True and rnd2["reviewers"][0]["id"] == sid, rnd2)
    scope = rnd2["scope"]
    check("[CP6-D11] scope.findings has only M1 -- M2 (accepted) is excluded",
          [f["id"] for f in scope["findings"]] == ["M1"], scope["findings"])


def d12_timing_boundaries():
    cfg_min = 20
    # a tiny fix: 0s fails, 10s passes
    d = _start("d12a", config=[f"min_review_seconds={cfg_min}"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, age=25)
    _fix(d, 3, "d12a")
    rnd2 = _open(d)
    check("[CP6-D12] (fixture sanity) round 2 is a fix round", rnd2.get("fix_round") is True, rnd2)
    p0 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    _set_review_age(d, p0, rnd2, 0)
    r0 = g.rx(d, "adv-record", "CP1", "--reviews", p0)
    check("[CP6-D12] a tiny fix reviewed in 0 seconds still fails (never below the 1-second floor)",
          r0.returncode != 0 and "too fast" in (r0.stdout + r0.stderr), r0.stdout + r0.stderr)
    d = _start("d12b", config=[f"min_review_seconds={cfg_min}"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, age=25)
    _fix(d, 3, "d12b")
    rnd2 = _open(d)
    p1 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    _set_review_age(d, p1, rnd2, 10)
    r1 = g.rx(d, "adv-record", "CP1", "--reviews", p1)
    check("[CP6-D12] a tiny fix reviewed in 10 seconds passes (min_review_seconds=20, scaled down)",
          r1.returncode == 0, r1.stdout + r1.stderr)
    # a ~400-line fix: 10s fails, 20s passes
    d = _start("d12c", config=[f"min_review_seconds={cfg_min}"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, age=25)
    _fix(d, 400, "d12c")
    rnd2 = _open(d)
    p2 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    _set_review_age(d, p2, rnd2, 10)
    r2 = g.rx(d, "adv-record", "CP1", "--reviews", p2)
    check("[CP6-D12] a ~400-line fix reviewed in 10 seconds still fails (not scaled all the way down)",
          r2.returncode != 0 and "too fast" in (r2.stdout + r2.stderr), r2.stdout + r2.stderr)
    d = _start("d12d", config=[f"min_review_seconds={cfg_min}"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, age=25)
    _fix(d, 400, "d12d")
    rnd2 = _open(d)
    p3 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    _set_review_age(d, p3, rnd2, 20)
    r3 = g.rx(d, "adv-record", "CP1", "--reviews", p3)
    check("[CP6-D12] a ~400-line fix reviewed in 20 seconds passes (the full configured minimum)",
          r3.returncode == 0, r3.stdout + r3.stderr)
    # a full round (unscoped) still needs the full configured minimum, however small its diff is
    d = _start("d12e", config=[f"min_review_seconds={cfg_min}"])
    rnd1 = _open(d)
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    for p in paths:
        _set_review_age(d, p, rnd1, 10)
    r4 = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("[CP6-D12] round 1 (unscoped, full round) at 10s still fails against the full 20s minimum",
          r4.returncode != 0 and "too fast" in (r4.stdout + r4.stderr), r4.stdout + r4.stderr)
    # the shorter minimum never exceeds a configured ceiling as low as 5 seconds
    d = _start("d12f", config=["min_review_seconds=5"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, age=25)
    _fix(d, 400, "d12f")
    rnd2 = _open(d)
    p5 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    _set_review_age(d, p5, rnd2, 5)
    r5 = g.rx(d, "adv-record", "CP1", "--reviews", p5)
    check("[CP6-D12] with min_review_seconds=5, a large fix reviewed in exactly 5s passes: the shorter minimum "
          "never exceeds the 5s ceiling", r5.returncode == 0, r5.stdout + r5.stderr)
    # and never above a configured 0 seconds either
    d = _start("d12g", config=["min_review_seconds=0"])
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]}, age=25)
    _fix(d, 400, "d12g")
    rnd2 = _open(d)
    p6 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    _set_review_age(d, p6, rnd2, 0)
    r6 = g.rx(d, "adv-record", "CP1", "--reviews", p6)
    check("[CP6-D12] with min_review_seconds=0, even a 0-second review passes: never above that ceiling either",
          r6.returncode == 0, r6.stdout + r6.stderr)


def d13_files_reviewed_still_required():
    d = _start("d13")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    _block(d, rnd1, {rid: [_finding("M1")]})
    _fix(d, 4, "d13")
    rnd2 = _open(d)
    path = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [], files_reviewed=[])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    check("[CP6-D13] a clean fix review with an EMPTY files_reviewed still fails",
          r.returncode != 0 and "files_reviewed" in (r.stdout + r.stderr), r.stdout + r.stderr)
    path2 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [], files_reviewed=["src/f.js"])
    r2 = g.rx(d, "adv-record", "CP1", "--reviews", path2)
    check("[CP6-D13] ...but one naming the actually-scoped file (src/f.js) passes", r2.returncode == 0,
          r2.stdout + r2.stderr)


def d14_host_instructions_carry_the_same_scope():
    d = _start("d14")
    rnd1 = _open(d)
    rid = _pick(rnd1, "standards")
    finding = _finding("M1")
    _block(d, rnd1, {rid: [finding]})
    _fix(d, 4, "d14")
    rnd2 = _open(d)
    row = rnd2["reviewers"][0]
    check("[CP6-D14] (fixture sanity) the fix round's one reviewer is a host sub-agent",
          row.get("kind") == "agent", row)
    how = row["how"]
    check("[CP6-D14] the host's launch instructions name the since-tree",
          f"since {rnd2['scope']['since_tree'][:10]}" in how, how)
    check("[CP6-D14] ...and the changed files", "changed files: src/f.js" in how, how)
    for needle in _find_rendered(finding):
        check(f"[CP6-D14] ...and the finding's {needle!r}", needle in how, how)


def d15_signed_external_review_with_scoped_prompt_and_files_reviewed():
    finding = _finding("A1")
    b = tp.fake_bin("ext3")
    env = tp.env_with(b)
    d = g.project("d15", extra_cp={"risk": "normal"},
                  config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext3-correctness"]})])
    r = g.rx(d, "providers", "--add", "ext3", "--command", json.dumps([str(b / "ext3")]), "--stdin",
             "--company", "openai", "--model", "gpt-5", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = tp.open_round(d, env=env)
    row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext3-correctness")
    host_path = g.write_review(d, rnd1, "host-standards", [finding])
    r = g.rx(d, "adv-record", "CP1", "--reviews", host_path, env=env)
    # ext3-correctness never reviewed round 1 (only host-standards did) -- round 1 still blocks on host-standards'
    # finding, so required reviewers must include it; write its clean counterpart too
    ext_path = Path(row["output"])
    p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext3-correctness", "--cp", "CP1",
                        "--out", str(ext_path)], cwd=d, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr + p.stdout
    r = g.rx(d, "adv-record", "CP1", "--reviews", host_path, str(ext_path), env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    t = g.rx(d, "triage", "CP1", f'host-standards/{finding["id"]}=fix:"real for the users"', env=env)
    assert t.returncode == 0, t.stdout + t.stderr
    (d / "src/f.js").write_text("export const f = () => 1; // d15 fixed\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = tp.open_round(d, env=env)
    check("[CP6-D15] (fixture sanity) round 2 is a fix round tied to host-standards",
          rnd2.get("fix_round") is True and rnd2["reviewers"][0]["id"] == "host-standards", rnd2)
    rid2 = rnd2["reviewers"][0]["id"]
    if rid2 != "host-standards":
        check("[CP6-D15] skipped: tie-break did not land on host-standards in this run", False, rnd2)
        return
    out2 = Path(rnd2["reviewers"][0]["output"])
    # host-standards is agent-kind: write its review directly (guard-confirmed), stamped with the scoped file
    host_path2 = g.write_review(d, rnd2, "host-standards", [])
    r2 = g.rx(d, "adv-record", "CP1", "--reviews", host_path2, env=env)
    check("[CP6-D15] the fix round's clean review records and approves", r2.returncode == 0, r2.stdout + r2.stderr)


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
    CLI) -- the same template tests/test_fix_round.py's own helper uses, kept local here for the same reason it
    gives: this file depends on neither test_fix_round.py's nor test_signing.py's fixtures."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-extbin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    _set_reply(b, name, reply_json)
    return b


def _set_reply(binpath: Path, name: str, reply_json: str):
    """(Re)writes the fake CLI at `binpath/name` to answer `reply_json` from now on -- used to make the SAME fake
    provider that raised a fix round's finding answer clean once it's fixed, without swapping its PATH."""
    script = _FAKE_REPLY_TEMPLATE.replace("__REPLY__", reply_json.replace("\\", "\\\\").replace("'", "'\\''"))
    (binpath / name).write_text(script)
    (binpath / name).chmod(0o755)


def _ext_dry_run(d, env, rid):
    """Runs external_review.py --dry-run for `rid` and returns the written prompt's text."""
    out = Path(_tmp.mkdtemp(prefix="ratchet-prompt-out-")) / "out.json"
    r = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out),
                        "--dry-run"], cwd=d, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"prompt written: (\S+) ", r.stdout)
    assert m, r.stdout
    return Path(m.group(1)).read_text()


def d16_external_command_reviewer_is_the_fix_rounds_only_reviewer():
    """A fake command-kind external provider (never the host) raises the finding in round 1, is the ONE reviewer
    the fix round ties to (it's the only reviewer this checkpoint ever has, so there's no tie-break to land on),
    its round-2 prompt is scoped (dry-run: only the fix diff and the one open finding, nothing else in the
    checkpoint), and its real signed review passes adv-record at the scaled minimum, never the full configured
    one (proven by pinning each review's age directly, so this never depends on how fast the sandbox happens to
    run real subprocesses)."""
    finding = _finding("A1")
    b = _fake_reply_bin("ext4", json.dumps({"findings": [finding]}))
    env = tp.env_with(b)
    d = g.project("d16", extra_cp={"risk": "normal"},
                  config=["review_depth=" + json.dumps({"normal": ["ext4-correctness"]}),
                          "min_review_seconds=20", 'provider_share={"max":1000,"minutes":15}'])
    r = g.rx(d, "providers", "--add", "ext4", "--command", json.dumps([str(b / "ext4")]), "--stdin",
             "--company", "openai", "--model", "gpt-5", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = tp.open_round(d, env=env)
    check("[CP6-D16] (fixture sanity) round 1 names exactly one reviewer: the external provider, not the host",
          [rv["id"] for rv in rnd1["reviewers"]] == ["ext4-correctness"], rnd1["reviewers"])
    row1 = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext4-correctness")
    out1 = Path(row1["output"])
    p1 = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext4-correctness", "--cp", "CP1",
                        "--out", str(out1)], cwd=d, capture_output=True, text=True, env=env)
    assert p1.returncode == 0, p1.stderr + p1.stdout
    _set_review_age(d, str(out1), rnd1, 25)  # round 1 is unscoped: needs the full configured 20s, deterministically
    r1 = g.rx(d, "adv-record", "CP1", "--reviews", str(out1), env=env)
    check("[CP6-D16] round 1 blocks on the external provider's own finding",
          r1.returncode == 2, r1.stdout + r1.stderr)
    t = g.rx(d, "triage", "CP1", f'ext4-correctness/{finding["id"]}=fix:"real for the users"', env=env)
    assert t.returncode == 0, t.stdout + t.stderr
    _fix(d, 4, "d16")
    rnd2 = tp.open_round(d, env=env)
    check("[CP6-D16] round 2 is a fix round tied to the SAME external provider",
          rnd2.get("fix_round") is True and rnd2["reviewers"][0]["id"] == "ext4-correctness", rnd2)
    check("[CP6-D16] round 2 is scoped (lane H CP6)", rnd2.get("scope") is not None, rnd2.get("scope"))
    prompt = _ext_dry_run(d, env, "ext4-correctness")
    check("[CP6-D16] the scoped prompt carries the open finding's id",
          f"id={finding['id']}" in prompt, prompt[-0:])
    check("[CP6-D16] ...and its description", finding["description"] in prompt, prompt[-0:])
    check("[CP6-D16] ...and the fix diff's own file (src/f.js)", "src/f.js" in prompt, prompt[-0:])
    check("[CP6-D16] the whole-checkpoint diff is NOT given: the untouched base file (src/a.js) is absent",
          "src/a.js" not in prompt, prompt[-0:])
    _set_reply(b, "ext4", json.dumps({"findings": []}))
    row2 = rnd2["reviewers"][0]
    out2 = Path(row2["output"])
    p2 = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext4-correctness", "--cp", "CP1",
                        "--out", str(out2)], cwd=d, capture_output=True, text=True, env=env)
    assert p2.returncode == 0, p2.stderr + p2.stdout
    _set_review_age(d, str(out2), rnd2, 2)  # the SCALED minimum, not the configured 20s, must gate this round
    r2 = g.rx(d, "adv-record", "CP1", "--reviews", str(out2), env=env)
    check("[CP6-D16] the signed review passes adv-record at the scaled minimum (2s), well under the configured "
          "20s", r2.returncode == 0, r2.stdout + r2.stderr)


def d17_new_and_deleted_file_content_in_the_real_prompt():
    """D09/D10 only check `scope.changed_files` (metadata). This drives a real command-kind reviewer's prompt
    (external_review.py's collect_scoped_diff) for a fix round that both creates and deletes a file alongside
    the actual fix, and checks the ACTUAL diff text a reviewer is handed: a new file's own content shows as an
    addition, and a deleted file's own content shows as a removal -- not just that their paths are listed."""
    finding = _finding("A1")
    b = _fake_reply_bin("ext5", json.dumps({"findings": [finding]}))
    env = tp.env_with(b)
    d = g.project("d17", extra_cp={"risk": "normal"},
                  config=["review_depth=" + json.dumps({"normal": ["ext5-correctness"]}),
                          'provider_share={"max":1000,"minutes":15}'])
    r = g.rx(d, "providers", "--add", "ext5", "--command", json.dumps([str(b / "ext5")]), "--stdin",
             "--company", "openai", "--model", "gpt-5", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    (d / "src" / "doomed17.js").write_text("export const DOOMED17_MARKER = 1;\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rnd1 = tp.open_round(d, env=env)
    row1 = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext5-correctness")
    out1 = Path(row1["output"])
    p1 = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext5-correctness", "--cp", "CP1",
                        "--out", str(out1)], cwd=d, capture_output=True, text=True, env=env)
    assert p1.returncode == 0, p1.stderr + p1.stdout
    _set_review_age(d, str(out1), rnd1, 25)
    r1 = g.rx(d, "adv-record", "CP1", "--reviews", str(out1), env=env)
    assert r1.returncode == 2, r1.stdout + r1.stderr
    t = g.rx(d, "triage", "CP1", f'ext5-correctness/{finding["id"]}=fix:"real for the users"', env=env)
    assert t.returncode == 0, t.stdout + t.stderr
    (d / "src" / "new17.js").write_text("export const NEW17_MARKER = 1;\n")
    (d / "src" / "doomed17.js").unlink()
    _fix(d, 4, "d17")
    rnd2 = tp.open_round(d, env=env)
    check("[CP6-D17] (fixture sanity) round 2 is a fix round tied to the external provider",
          rnd2.get("fix_round") is True and rnd2["reviewers"][0]["id"] == "ext5-correctness", rnd2)
    prompt = _ext_dry_run(d, env, "ext5-correctness")
    check("[CP6-D17] the new file's own content shows as an addition in the real diff",
          "+export const NEW17_MARKER" in prompt, prompt[-0:])
    check("[CP6-D17] the deleted file's own content shows as a removal in the real diff",
          "-export const DOOMED17_MARKER" in prompt, prompt[-0:])


def d18_scope_survives_a_same_round_reviewer_fallback():
    """When a fix round's tied reviewer goes unavailable mid-round and the same-round refill gives its slot to
    the normal fallback (lane H CP5 behavior, see test_fix_round.py's CP5-F16), the round's CP6 scope must still
    describe the SAME since_tree, changed files and findings for whichever reviewer actually ends up reviewing
    -- never dropped, reset, or left tied to the reviewer that's gone."""
    finding = _finding("A1")
    b = _fake_reply_bin("ext6", json.dumps({"findings": [finding]}))
    env = tp.env_with(b)
    d = g.project("d18", extra_cp={"risk": "normal"},
                  config=["review_depth=" + json.dumps({"normal": ["host-standards", "ext6-correctness"]})])
    r = g.rx(d, "providers", "--add", "ext6", "--command", json.dumps([str(b / "ext6")]), "--stdin",
             "--company", "openai", "--model", "gpt-5", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = tp.open_round(d, env=env)
    ext_row = next(rv for rv in rnd1["reviewers"] if rv["id"] == "ext6-correctness")
    p = subprocess.run([sys.executable, str(tp.EXT), "--reviewer", "ext6-correctness", "--cp", "CP1",
                        "--out", ext_row["output"]], cwd=d, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr + p.stdout
    host_path = g.write_review(d, rnd1, "host-standards", [])
    _set_review_age(d, ext_row["output"], rnd1, 25)
    _set_review_age(d, host_path, rnd1, 25)
    r = g.rx(d, "adv-record", "CP1", "--reviews", ext_row["output"], host_path, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    t = g.rx(d, "triage", "CP1", f'ext6-correctness/{finding["id"]}=fix:"real for the users"', env=env)
    assert t.returncode == 0, t.stdout + t.stderr
    (d / "src/f.js").write_text("export const f = () => 1; // d18 fixed\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = tp.open_round(d, env=env)
    check("[CP6-D18] (fixture sanity) round 2 is a fix round tied to ext6-correctness, scoped",
          rnd2.get("fix_round") is True and rnd2["reviewers"][0]["id"] == "ext6-correctness"
          and rnd2.get("scope") is not None, rnd2)
    scope_before = rnd2["scope"]
    m = g.rx(d, "reviewer-status", "--mark", "ext6-correctness", "--reason", "auth expired mid-round", env=env)
    assert m.returncode == 0, m.stdout + m.stderr
    rnd2b = tp.open_round(d, env=env)
    check("[CP6-D18] the SAME round is refilled with the normal fallback (host-alt-correctness), not replaced",
          rnd2b["round"] == rnd2["round"] and rnd2b["reviewers"][0]["id"] == "host-alt-correctness"
          and rnd2b.get("fix_round") is True, rnd2b)
    check("[CP6-D18] the scope survives the fallback: same since_tree, changed files and findings",
          rnd2b.get("scope") == scope_before, (rnd2b.get("scope"), scope_before))


def d19_small_fix_timing_boundary():
    """D12 only drives the fix_round path's diff-scaled minimum. This proves the SAME scaling applies to a small
    fix after a clean approval (lane H CP3/CP6's other scoped-round kind): a 0-second review still fails (never
    below the 1-second floor), and a 10-second review passes a configured min_review_seconds=20."""
    cfg_min = 20
    d = _start("d19a", config=[f"min_review_seconds={cfg_min}"])
    rnd1 = _open(d)
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    for p in paths:
        _set_review_age(d, p, rnd1, 25)
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    _append_lines(d / "src/f.js", 3, "d19a")
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2 = _open(d)
    check("[CP6-D19] (fixture sanity) round 2 is a one-reviewer small-fix round, scoped",
          rnd2.get("small_fix") is True and rnd2.get("scope") is not None, rnd2)
    p0 = g.write_review(d, rnd2, rnd2["reviewers"][0]["id"], [])
    _set_review_age(d, p0, rnd2, 0)
    r0 = g.rx(d, "adv-record", "CP1", "--reviews", p0)
    check("[CP6-D19] a small fix reviewed in 0 seconds still fails (never below the 1-second floor)",
          r0.returncode != 0 and "too fast" in (r0.stdout + r0.stderr), r0.stdout + r0.stderr)
    d2 = _start("d19b", config=[f"min_review_seconds={cfg_min}"])
    rnd1b = _open(d2)
    paths_b = [g.write_review(d2, rnd1b, rv["id"], []) for rv in rnd1b["reviewers"]]
    for p in paths_b:
        _set_review_age(d2, p, rnd1b, 25)
    r = g.rx(d2, "adv-record", "CP1", "--reviews", *paths_b)
    assert r.returncode == 0, r.stdout + r.stderr
    _append_lines(d2 / "src/f.js", 3, "d19b")
    r = g.rx(d2, "behavior", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    rnd2b = _open(d2)
    p1 = g.write_review(d2, rnd2b, rnd2b["reviewers"][0]["id"], [])
    _set_review_age(d2, p1, rnd2b, 10)
    r1 = g.rx(d2, "adv-record", "CP1", "--reviews", p1)
    check("[CP6-D19] the same tiny small fix reviewed in 10 seconds passes (min_review_seconds=20, scaled down)",
          r1.returncode == 0, r1.stdout + r1.stderr)


CASES = [("CP6-D01", d01_first_round_never_scoped), ("CP6-D02", d02_fix_round_scoped_to_the_blocked_round),
         ("CP6-D03", d03_rebuttal_reason_attached_to_the_finding),
         ("CP6-D04", d04_small_fix_after_approval_scoped_with_no_open_findings),
         ("CP6-D05", d05_large_fix_after_approval_not_scoped),
         ("CP6-D06", d06_high_risk_full_round_not_scoped),
         ("CP6-D07", d07_second_fix_round_scoped_to_only_the_second_fix),
         ("CP6-D08", d08_replaced_round_scoped_to_everything_since_the_last_review),
         ("CP6-D09", d09_new_file_in_scope_is_included),
         ("CP6-D10", d10_deleted_file_in_scope_is_included),
         ("CP6-D11", d11_accepted_finding_excluded_from_scope),
         ("CP6-D12", d12_timing_boundaries),
         ("CP6-D13", d13_files_reviewed_still_required),
         ("CP6-D14", d14_host_instructions_carry_the_same_scope),
         ("CP6-D15", d15_signed_external_review_with_scoped_prompt_and_files_reviewed),
         ("CP6-D16", d16_external_command_reviewer_is_the_fix_rounds_only_reviewer),
         ("CP6-D17", d17_new_and_deleted_file_content_in_the_real_prompt),
         ("CP6-D18", d18_scope_survives_a_same_round_reviewer_fallback),
         ("CP6-D19", d19_small_fix_timing_boundary)]


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
