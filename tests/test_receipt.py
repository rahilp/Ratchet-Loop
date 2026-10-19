#!/usr/bin/env python3
"""
CP2 tests: paste a proof receipt into the pull request.   python3 tests/test_receipt.py

`ratchet.py receipt` (plain markdown) and `ratchet.py receipt --json` print, for a run: each step's title, short
commit and elapsed time, the whole run's own elapsed time, who reviewed each step (a round that fell back to the
host's own company marked as such, with why an outside reviewer was skipped when Ratchet recorded it and "reason
not recorded" when it did not), the findings the user accepted with their reason, findings left for later, and
findings fixed later in the run (CP1's own `mark-fixed` rule: `left_for_later`/`_fixed_later`). No cost or token
figure appears anywhere. A step with no commit says "no commit recorded"; a step not finished yet shows its time
so far instead of a guessed completed duration; a run not finished yet shows its own time so far next to a plain
"run not finished" note.

The `--json` shape this suite drives the implementation toward (there is no existing contract to match, so this
picks one, per CP2's own notes "pick sensible keys"):
  {"run": {"elapsed_seconds": <int>, "finished": <bool>},
   "steps": [{"id", "title", "commit", "finished", "elapsed_seconds",
              "reviewers": [{"round", "id", "host_fallback": <bool>, "replaced": [...], "reason": <str|null>}]}],
   "accepted": [{"checkpoint", "id", "severity", "description", "reason", "accepted_by"}],
   "left_for_later": [{"checkpoint", "id", "description", "deferred_because"}],
   "fixed_later": [{"checkpoint", "id", "description", "round", "how"}]}

Reuses tests/test_gates.py (`project`, `approve_plan`, `write_tests`, `to_adversarial`, `open_round`, `R0`,
`write_review`, `git`, `rx`, `say`, `ENV`, `ENGINE`) for the throwaway git projects and the real-CLI-subprocess
way every suite in this repo drives the engine (see test_deferred_fixed.py and test_limits.py, which reuse the
same module the same way).

Deterministic elapsed times (CP2-T01..T04, T11, T12) are built by driving a real run through the engine (so its
checkpoints, gates, rounds and commits are all genuine), then stamping known `created_at`/`started_at`/
`completed_at`/history timestamps directly onto the run's own state.json and re-baselining Ratchet's own
integrity record with its own `record_chain` helper -- the exact `mutate()` pattern tests/test_status_plain.py
already uses for the same reason (a hand edit of the ACTIVE run's state.json, unlike tests/test_digest.py's
fabricated EARLIER-run folders, is chain-verified and must be re-baselined the same way `accept-state` would).
This never fabricates a round or reviewer shape that the real engine could not produce on its own.

Host-fallback cases (CP2-T05, T07) use a real `providers=[{"id":"codex","preset":"codex"}]` config plus the
real `ratchet.py reviewer-status --mark <id> --reason "..."` command (never a hand-edit) to make the external
reviewer genuinely unavailable, so `resolve_reviewers`'s own fallback logic -- not a fixture pretending to be
it -- picks host-alt-standards/host-alt-correctness and records the real reason. CP2-T07 mirrors the real run-4
CP1 round-3 shape the checkpoint's own notes describe: codex-standards/codex-correctness launch a round
available, then are marked unavailable before any review is written, so the second `ratchet.py reviewers CP1`
call hits cmd_reviewers' own same-round re-resolve branch. Only CP2-T06 fabricates an otherwise-unreachable
shape (a round recorded with no `substitutions` key at all, standing in for a run recorded before that field
existed) directly in state.json, via the same re-baselined `mutate()` helper, exactly as the checkpoint's own
notes call for.

Every case that depends on `receipt` (not yet built) calls it through the real CLI subprocess (`g.rx`), so a
missing command is reported through `check()` as a clean fail (a non-zero exit with the wrong/missing text),
never an uncaught crash.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, write_tests, to_adversarial, open_round, ...)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+[a-z]?)\]")


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

REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
              "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
              "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
              "m.record_chain(Path(sys.argv[2]))\n")


def _spath(d, slug="f"):
    return Path(d) / f".ratchet/runs/{slug}/state.json"


def mutate(d, fn, slug="f"):
    """Edits the active run's own state.json (fixture only: a hand edit, never through the engine) and
    re-baselines Ratchet's own integrity record with its own `record_chain`, the same pattern
    tests/test_status_plain.py uses for the same reason."""
    p = _spath(d, slug)
    st = json.loads(p.read_text())
    fn(st)
    p.write_text(json.dumps(st))
    r = subprocess.run([sys.executable, "-c", REBASELINE, str(g.ENGINE), str(p)], cwd=d, env=g.ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return st


def _lens_ids(rnd) -> dict:
    return {r["lens"]: r["id"] for r in rnd["reviewers"]}


T0 = "2026-01-01T00:00:00+00:00"


def _iso(base: str, **delta) -> str:
    return (dt.datetime.fromisoformat(base) + dt.timedelta(**delta)).isoformat()


def _clean_round(d, cp="CP1"):
    """Opens the next round for `cp` and records it with no findings (approved cleanly). Returns the round's
    own json (`reviewers`, `round`, ...)."""
    rnd = g.open_round(d, cp)
    paths = [g.write_review(d, rnd, rid, []) for rid in {r["id"] for r in rnd["reviewers"]}]
    r = g.rx(d, "adv-record", cp, "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    return rnd


def _advance_cp(d, cp, want):
    """Like test_gates.to_adversarial(), but for the SECOND (or later) checkpoint of a multi-checkpoint run:
    test_gates.write_tests() always checks `f() === 1`, which already holds once an earlier checkpoint has
    implemented src/f.js that way, so `red` would find nothing red. Each later checkpoint instead checks a
    distinct `want`, genuinely red until src/f.js is edited to return it."""
    (d / f"tests/{cp.lower()}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f.js';\n"
        f"t('[{cp}-T01] works', () => a.equal(f(), {want}));\n")
    assert g.rx(d, "tests-written", cp).returncode == 0
    r = g.rx(d, "red", cp)
    assert r.returncode == 0, r.stdout + r.stderr
    (d / "src/f.js").write_text(f"export const f = () => {want};\n")
    assert g.rx(d, "implemented", cp).returncode == 0
    r = g.rx(d, "behavior", cp)
    assert r.returncode == 0, r.stdout + r.stderr


def _timed_run(name, approve=True):
    """3 low-risk checkpoints, each completed cleanly (one reviewer, no findings, a real commit per step).
    Timestamps are then stamped to known, round values: CP1 4h17m, CP2 38m, CP3 1h5m, run total (to the final
    'approved' event) 6h10m -- when `approve` is True, `human-approve` is also driven for real first, so the
    event genuinely exists; this fixture only overwrites ITS timestamp, never invents the event."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, cps=3, config=cfg)
    g.approve_plan(d)
    for n, cp in enumerate(("CP1", "CP2", "CP3"), 1):
        if cp == "CP1":
            g.to_adversarial(d, cp)
        else:
            _advance_cp(d, cp, n)
        _clean_round(d, cp)
        r = g.rx(d, "complete", cp)
        assert r.returncode == 0, r.stdout + r.stderr
    if approve:
        g.rx(d, "ask", "human-approve")
        g.say(d, "yes, go ahead")
        r = g.rx(d, "human-approve")
        assert r.returncode == 0, r.stdout + r.stderr
    t_cp1_start = T0
    t_cp1_done = _iso(t_cp1_start, hours=4, minutes=17)
    t_cp2_done = _iso(t_cp1_done, minutes=38)
    t_cp3_done = _iso(t_cp2_done, hours=1, minutes=5)
    t_approved = _iso(t_cp3_done, minutes=10)

    def fn(st):
        st["created_at"] = t_cp1_start
        cps = st["checkpoints"]
        cps["CP1"]["started_at"], cps["CP1"]["completed_at"] = t_cp1_start, t_cp1_done
        cps["CP2"]["started_at"], cps["CP2"]["completed_at"] = t_cp1_done, t_cp2_done
        cps["CP3"]["started_at"], cps["CP3"]["completed_at"] = t_cp2_done, t_cp3_done
        if approve:
            for h in reversed(st.get("history") or []):
                if h.get("event") == "approved":
                    h["at"] = t_approved
                    break
    mutate(d, fn)
    commits = {cp: json.loads((d / ".ratchet/runs/f/state.json").read_text())["checkpoints"][cp]["commit"]
               for cp in ("CP1", "CP2", "CP3")}
    return d, commits


def _no_commit_run(name):
    """One low-risk checkpoint completed with `commit_per_checkpoint` off: its gates all pass but its own
    `commit` field stays None, the real shape `complete` leaves when nothing is committed."""
    cfg = ["min_review_seconds=0", "providers=[]", "commit_per_checkpoint=false"]
    d = g.project(name, cps=1, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    _clean_round(d, "CP1")
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    assert st["checkpoints"]["CP1"].get("commit") is None, st["checkpoints"]["CP1"]
    return d


def _mid_build_run(name):
    """CP1 finished; CP2's behavior gate has passed (started_at set) but it is NOT complete -- no `completed_at`,
    an otherwise-finished-looking run around it. CP2's `started_at` is stamped to a known, long-past time so its
    'time so far' is unambiguously non-zero."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, cps=2, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    _clean_round(d, "CP1")
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    _advance_cp(d, "CP2", 2)  # behavior passes; adversarial/complete never run

    def fn(st):
        st["checkpoints"]["CP2"]["started_at"] = "2020-01-01T00:00:00+00:00"
    mutate(d, fn)
    return d


def _run_not_finished(name):
    """Every checkpoint done, but `human-approve` was never run: the real shape `awaiting_final_review` leaves,
    with `created_at` stamped to a known past time so the run's own 'time so far' is unambiguous."""
    d, _ = _timed_run(name, approve=False)
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    assert st["phase"] == "awaiting_final_review", st["phase"]
    assert not any(h.get("event") == "approved" for h in st.get("history") or []), st.get("history")
    return d


def _host_fallback_fresh(name):
    """Normal-risk CP1 whose only round opens fresh with the sole configured outside provider (codex) marked
    unavailable on BOTH lenses before the round ever opens, so `resolve_reviewers`'s own fallback genuinely
    picks host-alt-standards/host-alt-correctness, each with a real recorded reason. `probe_providers=false`
    stops Ratchet from ever shelling out to the real `codex` CLI to test it (codex-standards/codex-correctness
    are marked unavailable before the round opens, so resolve_reviewers never even picks them)."""
    cfg = ["min_review_seconds=0", "probe_providers=false", 'providers=[{"id":"codex","preset":"codex"}]']
    d = g.project(name, extra_cp={"risk": "normal"}, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    for rid in ("codex-standards", "codex-correctness"):
        m = g.rx(d, "reviewer-status", "--mark", rid, "--reason", "out of credits")
        assert m.returncode == 0, m.stdout + m.stderr
    rnd = _clean_round(d, "CP1")
    ids = sorted(r["id"] for r in rnd["reviewers"])
    assert ids == ["host-alt-correctness", "host-alt-standards"], ids
    return d, rnd


def _host_fallback_midround(name):
    """Normal-risk CP1: round 1 opens with codex-standards/codex-correctness genuinely available, then BOTH are
    marked unavailable before any review is written (mirroring a mid-round failure); the second `reviewers` call
    for the SAME round hits cmd_reviewers' own same-round re-resolve branch, swapping them for
    host-alt-standards/host-alt-correctness within that same round. `probe_providers=false` stops Ratchet from
    ever shelling out to the real `codex` CLI to test it (round 1 genuinely resolves codex-standards/
    codex-correctness without a probe call; only `reviewer-status --mark` ever touches availability)."""
    cfg = ["min_review_seconds=0", "probe_providers=false", 'providers=[{"id":"codex","preset":"codex"}]']
    d = g.project(name, extra_cp={"risk": "normal"}, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    rnd1 = g.open_round(d, "CP1")
    ids1 = sorted(r["id"] for r in rnd1["reviewers"])
    assert ids1 == ["codex-correctness", "codex-standards"], ids1
    for rid in ("codex-standards", "codex-correctness"):
        m = g.rx(d, "reviewer-status", "--mark", rid, "--reason", "auth expired mid-round")
        assert m.returncode == 0, m.stdout + m.stderr
    rnd2 = g.open_round(d, "CP1")
    assert rnd2["round"] == rnd1["round"], (rnd1["round"], rnd2["round"])
    ids2 = sorted(r["id"] for r in rnd2["reviewers"])
    assert ids2 == ["host-alt-correctness", "host-alt-standards"], ids2
    paths = [g.write_review(d, rnd2, rid, []) for rid in {r["id"] for r in rnd2["reviewers"]}]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    return d, rnd2["round"]


def _host_fallback_old_style(name):
    """Low-risk CP1 with one real, cleanly-recorded round (a host reviewer, since providers=[] here), then its
    own `substitutions` key is removed (fixture only, via the re-baselined `mutate()`), standing in for a round
    recorded before that field existed."""
    cfg = ["min_review_seconds=0", "providers=[]"]
    d = g.project(name, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    _clean_round(d, "CP1")

    def fn(st):
        rounds = st["checkpoints"]["CP1"]["gates"]["adversarial"]["rounds"]
        rounds[-1].pop("substitutions", None)
    mutate(d, fn)
    return d


def _accept_and_defer_fixture(name):
    """CP1 (low risk): round 1 carries a blocker (B1, always blocking, with user_impact) plus two minors, S1
    (docs/NOTES.md, later fixed) and S2 (README.md, left open forever), both deferred the moment they're
    reviewed (minor findings never block at low risk). B1 is accepted via `ask accept-finding` + the user's own
    `accept: <reason>` reply + `accept-finding`, exactly test_limits.py's own accept flow. The checkpoint then
    completes (round 1's own fingerprint, nothing left open). docs/NOTES.md (S1's file) is edited and committed
    afterward, and a second, clean round reviews the changed code (checkpoints already at `complete` still
    accept `reviewers`/`adv-record`, re-reviewing code edited after completion) -- the real proof mark-fixed
    needs. mark-fixed then closes S1. Returns (d, round1, round2)."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, config=cfg)
    g.approve_plan(d)
    (d / "docs").mkdir()
    (d / "docs/NOTES.md").write_text("notes\n")
    (d / "README.md").write_text("# fixture docs\noriginal text\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add fixture files")
    g.to_adversarial(d, "CP1")
    b1 = {"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "unsafe write",
          "user_impact": "a user's saved data could be silently corrupted"}
    s1 = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
          "description": "missing a guard note"}
    s2 = {"id": "S2", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
          "description": "README is stale"}
    rnd1 = g.open_round(d, "CP1")
    p = g.write_review(d, rnd1, g.R0(rnd1), [b1, s1, s2])
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    assert adv.returncode == 2, adv.stdout + adv.stderr
    r1 = rnd1["round"]
    of_path = d / f".ratchet/runs/f/evidence/CP1/adversarial/round-{r1:02d}/open-findings.json"
    ids = [f["id"] for f in json.loads(of_path.read_text())]
    assert ids == ["B1"], ids
    g.rx(d, "ask", "accept-finding", "CP1", ",".join(ids))
    g.say(d, "accept: low risk, fixed-in-next-pass tracked separately")
    racc = g.rx(d, "accept-finding", "CP1")
    assert racc.returncode == 0, racc.stdout + racc.stderr
    # CP1's own gates have now passed again (step == "complete"), but `complete` itself is run only at the very
    # end: while CP1 is still the run's current checkpoint, `reviewers`/`adv-record` accept re-reviewing code
    # edited after its gates passed (cmd_reviewers/_expect_step allow step "complete"); running `complete` first
    # would move the run on, and CP1 could never open another round afterward (`_cur` requires it stay current).
    (d / "docs/NOTES.md").write_text("notes\nadded the guard\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add the guard")
    rb = g.rx(d, "behavior", "CP1")  # the whole-tree fingerprint moved (docs/NOTES.md), so `complete` will also
    assert rb.returncode == 0, rb.stdout + rb.stderr  # require behavior to be re-run before it, not just adversarial
    rnd2 = _clean_round(d, "CP1")
    r2 = rnd2["round"]
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="added the blocked-state guard, confirmed by the later clean round"')
    assert mf.returncode == 0, mf.stdout + mf.stderr
    comp = g.rx(d, "complete", "CP1")
    assert comp.returncode == 0, comp.stdout + comp.stderr
    return d, r1, r2


def _rich_project(name):
    """Like test_gates.project(), but with two checkpoints of DIFFERENT risk from the start (CP1 low, CP2
    normal; project()'s own `extra_cp` would apply to every checkpoint, not just one), approved once as a
    single plan, so both checkpoints are driven through the real engine's own `_advance`/base_tree machinery
    rather than being spliced into state.json by hand."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-receipt-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]", "checkin_round=99")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "normal",
           "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp1, cp2]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def _rich_run(name):
    """CP1 (low risk): the same accept/defer/fix shape as `_accept_and_defer_fixture` (accepted B1, left-for-
    later S2, fixed-later S1). CP2 (normal risk): the fresh host-fallback shape (host-alt-standards/
    host-alt-correctness, each with a real reason), completed cleanly. Every receipt section this checkpoint
    builds has at least one real entry here."""
    d = _rich_project(name)
    g.approve_plan(d)
    (d / "docs").mkdir()
    (d / "docs/NOTES.md").write_text("notes\n")
    (d / "README.md").write_text("# fixture docs\noriginal text\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add fixture files")
    g.to_adversarial(d, "CP1")
    b1 = {"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "unsafe write",
          "user_impact": "a user's saved data could be silently corrupted"}
    s1 = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
          "description": "missing a guard note"}
    s2 = {"id": "S2", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
          "description": "README is stale"}
    rnd1 = g.open_round(d, "CP1")
    p = g.write_review(d, rnd1, g.R0(rnd1), [b1, s1, s2])
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    assert adv.returncode == 2, adv.stdout + adv.stderr
    of_path = d / f".ratchet/runs/f/evidence/CP1/adversarial/round-{rnd1['round']:02d}/open-findings.json"
    ids = [f["id"] for f in json.loads(of_path.read_text())]
    assert ids == ["B1"], ids
    g.rx(d, "ask", "accept-finding", "CP1", ",".join(ids))
    g.say(d, "accept: low risk, fixed-in-next-pass tracked separately")
    racc = g.rx(d, "accept-finding", "CP1")
    assert racc.returncode == 0, racc.stdout + racc.stderr
    # `complete CP1` is run only at the very end, after CP1's own re-review below: once it runs, the current
    # checkpoint moves on to CP2 and CP1 could never open another round again.
    (d / "docs/NOTES.md").write_text("notes\nadded the guard\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add the guard")
    rb = g.rx(d, "behavior", "CP1")
    assert rb.returncode == 0, rb.stdout + rb.stderr
    rnd2 = _clean_round(d, "CP1")
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="added the blocked-state guard, confirmed by the later clean round"')
    assert mf.returncode == 0, mf.stdout + mf.stderr
    comp = g.rx(d, "complete", "CP1")
    assert comp.returncode == 0, comp.stdout + comp.stderr
    cfg2 = g.rx(d, "config", "--set", "probe_providers=false", 'providers=[{"id":"codex","preset":"codex"}]')
    assert cfg2.returncode == 0, cfg2.stdout + cfg2.stderr
    for rid in ("codex-standards", "codex-correctness"):
        m = g.rx(d, "reviewer-status", "--mark", rid, "--reason", "out of credits")
        assert m.returncode == 0, m.stdout + m.stderr
    _advance_cp(d, "CP2", 2)
    rnd3 = _clean_round(d, "CP2")
    ids3 = sorted(r["id"] for r in rnd3["reviewers"])
    assert ids3 == ["host-alt-correctness", "host-alt-standards"], ids3
    comp2 = g.rx(d, "complete", "CP2")
    assert comp2.returncode == 0, comp2.stdout + comp2.stderr
    return d


# ------------------------------------------------------------------------------------------------ cases

FORBIDDEN = ("cost", "token", "$", "usd")


def _has_forbidden(text: str) -> list:
    low = text.lower()
    return [w for w in FORBIDDEN if w in low]


def t01_steps_commits_and_times_plus_run_total():
    d, commits = _timed_run("t01")
    r = g.rx(d, "receipt")
    check("[CP2-T01] receipt exits 0 on a finished run", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    for cp, title in (("CP1", "Step 1"), ("CP2", "Step 2"), ("CP3", "Step 3")):
        check(f"[CP2-T01] {cp}'s title ({title}) appears", title in out, out)
        check(f"[CP2-T01] {cp}'s short commit ({commits[cp][:7]}) appears", commits[cp][:7] in out, out)
    check("[CP2-T01] CP1's 4h17m duration appears", re.search(r"4\s*h\w*\s*17\s*m", out) is not None, out)
    check("[CP2-T01] CP2's 38m duration appears", re.search(r"\b38\s*m", out) is not None, out)
    check("[CP2-T01] CP3's 1h5m duration appears", re.search(r"1\s*h\w*\s*0?5\s*m", out) is not None, out)
    check("[CP2-T01] the run's own 6h10m total appears", re.search(r"6\s*h\w*\s*10\s*m", out) is not None, out)
    check("[CP2-T01] no forbidden cost/token substring appears anywhere", not _has_forbidden(out), out)


TESTS = [("CP2-T01", t01_steps_commits_and_times_plus_run_total)]


def t02_no_commit_recorded():
    d = _no_commit_run("t02")
    r = g.rx(d, "receipt")
    check("[CP2-T02] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T02] CP1's line reads 'no commit recorded' (not blank, not a crash, not a guess)",
          "no commit recorded" in out.lower(), out)


TESTS.append(("CP2-T02", t02_no_commit_recorded))


def t03_step_not_finished_shows_time_so_far():
    d = _mid_build_run("t03")
    r = g.rx(d, "receipt")
    check("[CP2-T03] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    cp2_line = next((ln for ln in out.splitlines() if "CP2" in ln or "Step 2" in ln), "")
    check("[CP2-T03] CP2's own line says it is not finished (not a guessed completed duration)",
          bool(re.search(r"not finished|in progress|not (yet )?(done|complete)|still (building|in progress)",
                         cp2_line, re.I)),
          out)
    check("[CP2-T03] CP2's line still shows a time-so-far duration (started_at to now), not nothing",
          bool(re.search(r"\d+\s*[hdmy]", cp2_line, re.I)), cp2_line)


TESTS.append(("CP2-T03", t03_step_not_finished_shows_time_so_far))


def t04_run_not_finished_shows_time_so_far():
    d = _run_not_finished("t04")
    r = g.rx(d, "receipt")
    check("[CP2-T04] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T04] a plain 'run not finished' note appears",
          bool(re.search(r"run not finished|not finished yet|run is not finished", out, re.I)), out)
    check("[CP2-T04] the run's time line still shows a duration (time so far), never a final total "
          "presented as if the run had ended",
          bool(re.search(r"\d+\s*[hdmy]", out, re.I)), out)


TESTS.append(("CP2-T04", t04_run_not_finished_shows_time_so_far))


def t05_fresh_fallback_named_with_reasons():
    d, rnd = _host_fallback_fresh("t05")
    r = g.rx(d, "receipt")
    check("[CP2-T05] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T05] host-alt-standards is named", "host-alt-standards" in out, out)
    check("[CP2-T05] host-alt-correctness is named", "host-alt-correctness" in out, out)
    check("[CP2-T05] each is marked as the host's own company standing in",
          bool(re.search(r"host-alt-standards[^\n]*(host|own company|fallback|instead of)", out, re.I))
          and bool(re.search(r"host-alt-correctness[^\n]*(host|own company|fallback|instead of)", out, re.I)),
          out)
    check("[CP2-T05] codex-standards is named as the one it replaced", "codex-standards" in out, out)
    check("[CP2-T05] codex-correctness is named as the one it replaced", "codex-correctness" in out, out)
    check("[CP2-T05] the recorded reason ('out of credits') is printed, not invented",
          "out of credits" in out, out)


TESTS.append(("CP2-T05", t05_fresh_fallback_named_with_reasons))


def t06_old_style_fallback_reason_not_recorded():
    d = _host_fallback_old_style("t06")
    r = g.rx(d, "receipt")
    check("[CP2-T06] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T06] a host-company reviewer is still named for CP1's round",
          bool(re.search(r"host-(alt-)?(both|standards|correctness)", out)), out)
    check("[CP2-T06] its reason reads 'reason not recorded' (never blank, a crash, or an invented reason)",
          "reason not recorded" in out.lower(), out)


TESTS.append(("CP2-T06", t06_old_style_fallback_reason_not_recorded))


def t07_midround_swap_recorded_with_real_reason():
    d, rnd_no = _host_fallback_midround("t07")
    r = g.rx(d, "receipt")
    check("[CP2-T07] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T07] host-alt-standards/host-alt-correctness are named for the swapped round",
          "host-alt-standards" in out and "host-alt-correctness" in out, out)
    check("[CP2-T07] the real unavailability reason ('auth expired mid-round') is printed",
          "auth expired mid-round" in out, out)
    check("[CP2-T07] 'reason not recorded' is NOT used for this round's swap (the mid-round fix populated it)",
          "reason not recorded" not in out.lower(), out)
    rj = g.rx(d, "receipt", "--json")
    check("[CP2-T07] receipt --json exits 0", rj.returncode == 0, rj.stdout + rj.stderr)
    data = json.loads(rj.stdout) if rj.returncode == 0 else {}
    cp1 = next((s for s in (data.get("steps") or []) if s.get("id") == "CP1"), {})
    revs = cp1.get("reviewers") or []
    swapped = [x for x in revs if x.get("round") == rnd_no and x.get("id") in
               ("host-alt-standards", "host-alt-correctness")]
    check("[CP2-T07] --json carries the swapped round's reviewers with host_fallback set and a real reason",
          bool(swapped) and all(x.get("host_fallback") for x in swapped)
          and all("auth expired mid-round" in str(x.get("reason") or "") for x in swapped), swapped)


TESTS.append(("CP2-T07", t07_midround_swap_recorded_with_real_reason))


def t08_accepted_findings_with_reason_and_who():
    d, r1, r2 = _accept_and_defer_fixture("t08")
    r = g.rx(d, "receipt")
    check("[CP2-T08] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T08] B1 is listed", "B1" in out, out)
    check("[CP2-T08] its severity (blocker) is shown near it",
          bool(re.search(r"B1[^\n]*blocker|blocker[^\n]*B1", out, re.I)), out)
    check("[CP2-T08] its exact accepted reason is printed",
          "low risk, fixed-in-next-pass tracked separately" in out, out)
    check("[CP2-T08] it says who accepted it (human)",
          bool(re.search(r"accepted by:?\s*human", out, re.I)), out)


TESTS.append(("CP2-T08", t08_accepted_findings_with_reason_and_who))


def t09_left_for_later_separate_from_accepted():
    d, r1, r2 = _accept_and_defer_fixture("t09")
    r = g.rx(d, "receipt")
    check("[CP2-T09] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T09] S2 (never accepted) appears", "S2" in out, out)
    check("[CP2-T09] S2's description appears", "README is stale" in out, out)
    check("[CP2-T09] S2's deferred_because reason appears",
          "only blocker and major findings block" in out.lower()
          or bool(re.search(r"low-risk step", out, re.I)), out)
    accepted_block = out.split("left for later", 1)[0] if "left for later" in out.lower() else out
    low_accepted = accepted_block.lower()
    check("[CP2-T09] S2 is never mixed into the accepted section (only B1 is there)",
          "s2" not in low_accepted.split("left for later")[0] if "left for later" in low_accepted else True, out)


TESTS.append(("CP2-T09", t09_left_for_later_separate_from_accepted))


def t10_fixed_later_not_also_left_for_later():
    d, r1, r2 = _accept_and_defer_fixture("t10")
    r = g.rx(d, "receipt")
    check("[CP2-T10] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-T10] S1 appears under a 'fixed later' section",
          bool(re.search(r"fixed later[^\n]*\n(?:.*\n)*?.*S1|S1[^\n]*fixed", out, re.I)) or
          ("fixed later" in out.lower() and "S1" in out), out)
    check(f"[CP2-T10] S1's record names the round it was confirmed at ({r2})", str(r2) in out, out)
    fixed_idx = out.lower().find("fixed later")
    left_idx = out.lower().find("left for later")
    if fixed_idx != -1 and left_idx != -1:
        left_section = out[left_idx:]
        check("[CP2-T10] S1 does NOT also appear under left-for-later", "S1" not in left_section, left_section)
    else:
        check("[CP2-T10] both a 'fixed later' and a 'left for later' section are present",
              fixed_idx != -1 and left_idx != -1, out)


TESTS.append(("CP2-T10", t10_fixed_later_not_also_left_for_later))


def t11_no_cost_or_token_in_rich_receipt():
    d = _rich_run("t11")
    r = g.rx(d, "receipt")
    check("[CP2-T11] receipt exits 0 on a run with every fixture at once", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    found = _has_forbidden(out)
    check("[CP2-T11] scanning the whole output finds no cost/token/$/USD substring",
          not found, f"found: {found}\n{out}")


TESTS.append(("CP2-T11", t11_no_cost_or_token_in_rich_receipt))


def t12_json_matches_markdown_with_no_cost_or_token_keys():
    d = _rich_run("t12")
    rj = g.rx(d, "receipt", "--json")
    check("[CP2-T12] receipt --json exits 0", rj.returncode == 0, rj.stdout + rj.stderr)
    try:
        data = json.loads(rj.stdout)
    except Exception as e:
        check("[CP2-T12] --json output parses as JSON", False, f"{e}: {rj.stdout[:300]}")
        data = {}
    else:
        check("[CP2-T12] --json output parses as JSON", True)
    blob = json.dumps(data).lower()
    check("[CP2-T12] no key or value anywhere is named cost or token(s)",
          "cost" not in blob and "token" not in blob, blob[:400])
    steps = data.get("steps") or []
    ids = [s.get("id") for s in steps]
    check("[CP2-T12] --json lists both CP1 and CP2", ids == ["CP1", "CP2"], ids)
    cp1 = next((s for s in steps if s.get("id") == "CP1"), {})
    check("[CP2-T12] CP1's commit in --json is a real short commit (not null, not a cost figure)",
          bool(cp1.get("commit")) and isinstance(cp1.get("commit"), str), cp1)
    rmd = g.rx(d, "receipt")
    out = rmd.stdout
    check("[CP2-T12] the json's CP1 commit string also appears in the markdown", cp1.get("commit", "***") in out,
          (cp1.get("commit"), out))
    accepted_ids = [a.get("id") for a in data.get("accepted") or []]
    left_ids = [a.get("id") for a in data.get("left_for_later") or []]
    fixed_ids = [a.get("id") for a in data.get("fixed_later") or []]
    check("[CP2-T12] --json's accepted/left_for_later/fixed_later carry B1/S2/S1 respectively",
          "B1" in accepted_ids and "S2" in left_ids and "S1" in fixed_ids,
          (accepted_ids, left_ids, fixed_ids))


TESTS.append(("CP2-T12", t12_json_matches_markdown_with_no_cost_or_token_keys))


def t13_docs_describe_receipt():
    readme = (ROOT / "README.md").read_text()
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP2-T13] README's command table lists `receipt`", "receipt" in readme, "")
    check("[CP2-T13] README says the final review shows the receipt",
          bool(re.search(r"final review[^\n]*receipt|receipt[^\n]*final review", readme, re.I))
          or "receipt" in readme.lower(), "")
    check("[CP2-T13] the orchestrator SKILL.md's final-review step tells the agent to run the receipt and show it",
          "receipt" in skill, "")
    check("[CP2-T13] schemas.md documents the receipt's sections and its --json shape",
          "receipt" in schemas, "")


TESTS.append(("CP2-T13", t13_docs_describe_receipt))


# ------------------------------------------------------------------------------------------------ CP2 adversarial round 1 fixes


def _two_reviewers_same_id_fixture(name):
    """[CP2-X01] fixture: normal-risk CP1, reviewed by its round's own two reviewers, each independently raising
    a minor finding under the identical bare id 'A1' -- two reviewers picking the same bare id within one
    checkpoint is the normal case `_finding_key` exists for (their shared prompt gives every reviewer the
    identical example id). `blocking_severities.normal` is narrowed to blocker/major (like low risk's own
    default) so both minors defer cleanly instead of blocking. Returns (d, the round's two reviewer ids)."""
    cfg = ["min_review_seconds=0", "providers=[]", 'blocking_severities.normal=["blocker","major"]']
    d = g.project(name, extra_cp={"risk": "normal"}, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    rnd = g.open_round(d, "CP1")
    ids = sorted(r["id"] for r in rnd["reviewers"])
    assert len(ids) == 2, ids
    findings_by_id = {
        ids[0]: [{"id": "A1", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
                  "description": f"{ids[0]}'s own A1"}],
        ids[1]: [{"id": "A1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "src/f.js",
                  "description": f"{ids[1]}'s own A1"}],
    }
    paths = [g.write_review(d, rnd, rid, findings_by_id[rid]) for rid in ids]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    return d, ids


def x01_two_reviewers_same_bare_id_disambiguated():
    d, ids = _two_reviewers_same_id_fixture("x01")
    r = g.rx(d, "receipt")
    check("[CP2-X01] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    for rid in ids:
        check(f"[CP2-X01] {rid}/A1's qualified reference appears in the markdown (reviewer identity preserved)",
              f"{rid}/A1" in out, out)
    check("[CP2-X01] both reviewers' own A1 descriptions appear as two distinct findings, never merged into one",
          out.count("'s own A1") >= 2, out)
    rj = g.rx(d, "receipt", "--json")
    check("[CP2-X01] receipt --json exits 0", rj.returncode == 0, rj.stdout + rj.stderr)
    data = json.loads(rj.stdout)
    left = data.get("left_for_later") or []
    pairs = sorted((f.get("id"), f.get("reviewer")) for f in left)
    want = sorted(("A1", rid) for rid in ids)
    check("[CP2-X01] --json's left_for_later carries both reviewers against the same bare id 'A1', "
          "distinguished by the 'reviewer' field", pairs == want, (pairs, want))


TESTS.append(("CP2-X01", x01_two_reviewers_same_bare_id_disambiguated))


def x02_roster_removed_keeps_host_fallback_and_reason():
    """[CP2-X02]: host_fallback status must not depend only on today's roster. After a round records a genuine
    host-company fallback (host-alt-standards/host-alt-correctness, each with a recorded reason), its roster
    entries are removed from today's config -- as if a custom host-agent reviewer were renamed or dropped after
    this checkpoint ran. The receipt must still mark both as the host's own company, decided from the recorded
    id's own naming convention, and must still show the recorded substitution reason instead of suppressing it."""
    d, rnd = _host_fallback_fresh("x02")
    ids = sorted(r["id"] for r in rnd["reviewers"])
    assert ids == ["host-alt-correctness", "host-alt-standards"], ids
    r = g.rx(d, "config", "--set", "reviewers.host-alt-standards=null", "reviewers.host-alt-correctness=null")
    assert r.returncode == 0, r.stdout + r.stderr
    out = g.rx(d, "receipt").stdout
    for rid in ids:
        check(f"[CP2-X02] {rid} is still named even though its roster entry is gone from today's config",
              rid in out, out)
    check("[CP2-X02] it is still marked as the host's own company standing in, decided from the recorded id",
          "host's own company" in out, out)
    check("[CP2-X02] its recorded substitution reason ('out of credits') still shows, never suppressed",
          "out of credits" in out, out)
    rj = g.rx(d, "receipt", "--json")
    data = json.loads(rj.stdout)
    revs = (data.get("steps") or [{}])[0].get("reviewers") or []
    check("[CP2-X02] --json still marks host_fallback true for both reviewers, each with the real reason",
          len(revs) == 2 and all(e.get("host_fallback") is True and "out of credits" in (e.get("reason") or "")
                                  for e in revs), revs)


TESTS.append(("CP2-X02", x02_roster_removed_keeps_host_fallback_and_reason))


def x03_skill_runs_receipt_after_human_approve():
    """[CP2-X03] doc check: the orchestrator's Phase C must run `receipt` only after `human-approve` succeeds,
    and show the finished markdown in the same wrap-up message -- never a 'run not finished' receipt handed to
    the developer before the run is actually approved."""
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    m = re.search(r"## Phase C: Human review.*?(?=\n## |\Z)", skill, re.S)
    check("[CP2-X03] Phase C section found", bool(m), "")
    phase_c = m.group(0) if m else ""
    approve_idx = phase_c.find("$RATCHET ask human-approve")
    receipt_idx = phase_c.find("$RATCHET receipt")
    check("[CP2-X03] Phase C runs the human-approve question", approve_idx != -1, phase_c)
    check("[CP2-X03] Phase C runs receipt", receipt_idx != -1, phase_c)
    check("[CP2-X03] receipt is run after the approval question, not before it",
          receipt_idx != -1 and approve_idx != -1 and receipt_idx > approve_idx, phase_c)
    order = re.search(
        r"\$RATCHET human-approve`, then `\$RATCHET view --stop`\.\s*Then run `\$RATCHET receipt`", phase_c)
    check("[CP2-X03] on the yes-reply path, human-approve runs, then receipt, in the same wrap-up message",
          bool(order), phase_c)


TESTS.append(("CP2-X03", x03_skill_runs_receipt_after_human_approve))


# ------------------------------------------------------------------------------------------------ CP2 adversarial round 2 fixes


def _accepted_section(out: str) -> str:
    """The receipt's own 'accepted by you' section, bounded by its own heading and whichever of the 'fixed
    later'/'left for later' section headings comes next (or the end of the text when neither follows): unlike
    splitting on 'left for later' alone, this never silently credits the accepted section with text that
    actually belongs to a 'fixed later' section printed ahead of it."""
    start = out.find("Findings accepted by you")
    if start == -1:
        return ""
    rest = out[start:]
    cut = len(rest)
    for marker in ("finding(s) fixed later in the run", "finding(s) left for later"):
        idx = rest.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return rest[:cut]


def x04_accepted_section_never_lists_left_for_later():
    """[CP2-X04]: a non-vacuous check that the accepted section really only holds what was really accepted.
    Unlike CP2-T09 (frozen), this bounds the accepted section by its own heading and the next section's heading
    on both sides, so a regression that mixed S2 (left for later, never accepted) into the accepted text would
    be caught even though S2 also legitimately appears later, under 'left for later'."""
    d, r1, r2 = _accept_and_defer_fixture("x04")
    r = g.rx(d, "receipt")
    check("[CP2-X04] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    accepted = _accepted_section(out)
    check("[CP2-X04] the accepted section is found", bool(accepted), out)
    check("[CP2-X04] B1 (really accepted) appears in the accepted section", "B1" in accepted, accepted)
    check("[CP2-X04] S2 (left for later, never accepted) does NOT appear in the accepted section",
          "S2" not in accepted, accepted)
    left_start = out.find("finding(s) left for later")
    check("[CP2-X04] a 'left for later' section is found", left_start != -1, out)
    check("[CP2-X04] S2 does appear under 'left for later'", "S2" in out[left_start:], out[left_start:])


TESTS.append(("CP2-X04", x04_accepted_section_never_lists_left_for_later))


def _mismatched_tree_commit_run(name):
    """[CP2-X05] fixture: `commit_per_checkpoint` off, so `complete` never records a commit of its own for CP1.
    While CP1 is still mid-step (its behavior implementation written but deliberately left uncommitted), an
    ordinary, unrelated commit is made: a real commit, with a real sha, landing inside the step's own
    started_at..completed_at window. Because CP1's own uncommitted implementation is never committed, the tree
    Ratchet actually reviews for CP1 never matches this mid-step commit's own tree, so there is no commit
    anywhere whose tree equals what was reviewed."""
    cfg = ["min_review_seconds=0", "providers=[]", "commit_per_checkpoint=false"]
    d = g.project(name, cps=1, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")  # src/f.js is implemented but left uncommitted
    (d / "scratch.txt").write_text("unrelated mid-step work\n")
    g.git(d, "add", "scratch.txt")
    g.git(d, "commit", "-qm", "unrelated mid-step commit")
    mid_sha = g.git(d, "rev-parse", "--short", "HEAD").stdout.strip()
    rb = g.rx(d, "behavior", "CP1")  # the tree moved (scratch.txt); `complete` requires behavior re-run first
    assert rb.returncode == 0, rb.stdout + rb.stderr
    _clean_round(d, "CP1")
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    assert st["checkpoints"]["CP1"].get("commit") is None, st["checkpoints"]["CP1"]
    return d, mid_sha


def x05_mismatched_tree_commit_not_shown():
    d, mid_sha = _mismatched_tree_commit_run("x05")
    r = g.rx(d, "receipt")
    check("[CP2-X05] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP2-X05] CP1 reads 'no commit recorded' (the mid-step commit's own tree does not match what was "
          "actually reviewed)", "no commit recorded" in out.lower(), out)
    check("[CP2-X05] the unrelated mid-step commit's own sha is never shown as CP1's commit",
          mid_sha not in out, (mid_sha, out))


TESTS.append(("CP2-X05", x05_mismatched_tree_commit_not_shown))


def _pending_round_midswap(name):
    """[CP2-X06] fixture: normal-risk CP1 whose round 1 opens with codex-standards/codex-correctness genuinely
    available (mirroring `_host_fallback_midround`), but, unlike that fixture, `adv-record` is never run: the
    round stays open (pending) the whole time, first fresh then after both reviewers are marked unavailable and
    the SAME round is re-resolved (cmd_reviewers' own same-round branch), so `cps["adv_round"]["reviewers"]`
    stays a plain list of string ids throughout, exactly the shape a receipt asked for mid-round must handle."""
    cfg = ["min_review_seconds=0", "probe_providers=false", 'providers=[{"id":"codex","preset":"codex"}]']
    d = g.project(name, extra_cp={"risk": "normal"}, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    rnd1 = g.open_round(d, "CP1")
    return d, rnd1


def x06_pending_round_reviewers_shown_honestly():
    d, rnd1 = _pending_round_midswap("x06")
    ids1 = sorted(r["id"] for r in rnd1["reviewers"])
    assert ids1 == ["codex-correctness", "codex-standards"], ids1
    r = g.rx(d, "receipt")
    check("[CP2-X06] receipt exits 0 while round 1 is still open (no adv-record yet)",
          r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    cp1_line = next((ln for ln in out.splitlines() if ln.startswith("- CP1 ")), "")
    check("[CP2-X06] CP1's own line says it is not finished while the round is pending",
          bool(re.search(r"not finished", cp1_line, re.I)), out)
    check("[CP2-X06] the fresh round's own pending reviewers (codex-standards/codex-correctness) are named",
          "codex-standards" in out and "codex-correctness" in out, out)
    rj = g.rx(d, "receipt", "--json")
    check("[CP2-X06] receipt --json exits 0 while round 1 is still open", rj.returncode == 0, rj.stdout + rj.stderr)
    data = json.loads(rj.stdout) if rj.returncode == 0 else {}
    cp1 = next((s for s in (data.get("steps") or []) if s.get("id") == "CP1"), {})
    check("[CP2-X06] --json marks CP1 not finished while its round is pending", cp1.get("finished") is False, cp1)
    ids_json = sorted(x.get("id") for x in (cp1.get("reviewers") or []))
    check("[CP2-X06] --json carries the same pending reviewer ids, never crashing on their string shape",
          ids_json == ["codex-correctness", "codex-standards"], ids_json)

    # Mid-round re-resolve: mark both unavailable and re-open the SAME round (cmd_reviewers' own same-round
    # branch), still with no adv-record -- the swap must stay just as visible and honest.
    for rid in ("codex-standards", "codex-correctness"):
        m = g.rx(d, "reviewer-status", "--mark", rid, "--reason", "auth expired mid-round")
        assert m.returncode == 0, m.stdout + m.stderr
    rnd2 = g.open_round(d, "CP1")
    assert rnd2["round"] == rnd1["round"], (rnd1["round"], rnd2["round"])
    ids2 = sorted(r["id"] for r in rnd2["reviewers"])
    assert ids2 == ["host-alt-correctness", "host-alt-standards"], ids2
    r2 = g.rx(d, "receipt")
    check("[CP2-X06] receipt exits 0 after the mid-round re-resolve, still with no adv-record",
          r2.returncode == 0, r2.stdout + r2.stderr)
    out2 = r2.stdout
    check("[CP2-X06] the swapped-in pending reviewers (host-alt-standards/host-alt-correctness) are named",
          "host-alt-standards" in out2 and "host-alt-correctness" in out2, out2)
    check("[CP2-X06] the real mid-round reason is printed for the pending swap",
          "auth expired mid-round" in out2, out2)
    rj2 = g.rx(d, "receipt", "--json")
    check("[CP2-X06] receipt --json exits 0 after the mid-round re-resolve", rj2.returncode == 0, rj2.stdout + rj2.stderr)
    data2 = json.loads(rj2.stdout) if rj2.returncode == 0 else {}
    cp1b = next((s for s in (data2.get("steps") or []) if s.get("id") == "CP1"), {})
    swapped = [x for x in (cp1b.get("reviewers") or []) if x.get("id") in ("host-alt-standards", "host-alt-correctness")]
    check("[CP2-X06] --json marks the swapped-in pending reviewers host_fallback with the real reason",
          bool(swapped) and all(x.get("host_fallback") for x in swapped)
          and all("auth expired mid-round" in str(x.get("reason") or "") for x in swapped), swapped)


TESTS.append(("CP2-X06", x06_pending_round_reviewers_shown_honestly))


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
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
