#!/usr/bin/env python3
"""
CP6 tests: one flaky failure does not cost a round.   python3 tests/test_flaky.py

Covers four independent rules:
  - a behavior-gate test run that fails once and passes on one immediate automatic re-run passes the gate
    marked flaky, once per step, without spending an attempt (CP6-T01..T04);
  - the full regression run at `complete` gets the same one-time allowance, tracked for the run (CP6-T05, T06);
  - a reviewer CLI reply cut off mid-JSON is retried once; a second cut-off falls back only that slot, not the
    whole provider, and a review another slot already finished in the round is kept untouched (CP6-T07..T11);
  - docs describe both rules (CP6-T12);
  - the prompt hook tells a sub-agent hand-back (wrapped `<agent-message from="...">...</agent-message>`) apart
    from the user's own message, keeping the old on-screen-rendering marker working too (CP6-T13..T15);
  - a too-large review diff puts files changed since the previous round first and names what was cut (CP6-T16..T18);
    a lane H CP6 scoped small-fix round, by contrast, excludes an unrelated untouched file outright rather than
    merely deprioritizing it (CP6-X10).

Reuses tests/test_gates.py's project()/approve_plan()/write_tests()/to_adversarial()/open_round()/R0()/
write_review()/say()/guard() helpers, tests/test_providers.py's fake_bin()/env_with()/ids() ("other provider" CLI
on a private PATH, never a real reviewer CLI), and tests/test_signing.py's project2()/pass_behavior2() two-checkpoint
fixture for the run-wide regression allowance. No real agent CLI, real network call or real reviewer CLI is ever
invoked; every "other provider" is a fake shell script this file writes to a private PATH.

Writes a per-test JSON report to RATCHET_TEST_REPORT when set:
  {"cases": [{"id": "CP6-T01", "status": "passed"|"failed", "message": "..."}]}
"""
from __future__ import annotations

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
import _tmp  # noqa: E402
import test_gates as g  # noqa: E402  (project, approve_plan, write_tests, to_adversarial, open_round, R0, ...)
import test_providers as tp  # noqa: E402  (fake_bin, env_with, ids)
import test_signing as ts  # noqa: E402  (project2, pass_behavior2: a clean two-checkpoint fixture)

EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP6-[TX]\d+)\]")


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


def state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def say2(d, env, text, session="s-owner"):
    """The user types `text` in a test_signing.project2()-style project (its own d/t.jsonl transcript)."""
    tpth = Path(d) / "t.jsonl"
    with tpth.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": ts.now_dt().isoformat()}) + "\n")
    return ts.rx(d, env, "hook", "prompt", "--host", "claude",
                stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": session,
                                  "transcript_path": str(tpth)}))


def set_regression_command_confirmed(d, env, cmd):
    """Sets cfg.regression_command after the plan was already approved, and confirms the change the way the
    engine requires (ask approve-changes, the user's own reply, then approve-changes) so later commands don't
    refuse the run for an unconfirmed rule change."""
    assert ts.rx(d, env, "config", "--set", "regression_command=" + cmd).returncode == 0
    ts.rx(d, env, "ask", "approve-changes")
    say2(d, env, "yes, go ahead")
    r = ts.rx(d, env, "approve-changes")
    assert r.returncode == 0, r.stdout + r.stderr


# ---- a counter-driven fake test/regression command: exit code per invocation comes from `outcomes`, the last
# entry repeats for every invocation past the end of the list. Each call to flaky_cmd_from_outcomes() gets its
# own counter file, so independent test cases never share state. ------------------------------------------------

_FLAKY_SCRIPT_SRC = """import json, sys
from pathlib import Path
counter_path = Path(sys.argv[1])
outcomes = json.loads(sys.argv[2])
n = int(counter_path.read_text() or "0") + 1
counter_path.write_text(str(n))
idx = min(n, len(outcomes)) - 1
sys.exit(int(outcomes[idx]))
"""
_FLAKY_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-flaky-script-")) / "flaky_cmd.py"
_FLAKY_SCRIPT.write_text(_FLAKY_SCRIPT_SRC)


def flaky_cmd_from_outcomes(outcomes):
    """A shell command (for cp['tests']['command'] or cfg.regression_command) that exits with outcomes[0] on its
    first call, outcomes[1] on its second, ... and outcomes[-1] forever after. Returns (command, counter_path)."""
    counter = Path(_tmp.mkdtemp(prefix="ratchet-flaky-ctr-")) / "n"
    counter.write_text("0")
    cmd = (f"{shlex.quote(sys.executable)} {shlex.quote(str(_FLAKY_SCRIPT))} {shlex.quote(str(counter))} "
           f"{shlex.quote(json.dumps(outcomes))}")
    return cmd, counter


def behavior_project(name, outcomes):
    """A one-checkpoint project whose behavior gate command is the counter-driven fake above. The red step
    consumes outcomes[0] (and must fail, as red requires), so outcomes[1:] are what the behavior gate itself sees."""
    cmd, counter = flaky_cmd_from_outcomes(outcomes)
    d = g.project(name, extra_cp={"tests": {"files": ["tests/cp1.test.js"], "command": cmd}})
    g.approve_plan(d)
    g.write_tests(d)
    assert g.rx(d, "tests-written", "CP1").returncode == 0
    r = g.rx(d, "red", "CP1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert g.rx(d, "implemented", "CP1").returncode == 0
    return d, counter


# ---- CP6-T01..T04: the behavior gate's own one-time flaky allowance -------------------------------------------

def t01_flaky_behavior_passes_without_attempt():
    print("CP6-T01: a flaky test failure passes on one immediate re-run without spending an attempt")
    d, _ = behavior_project("cp6-01", [1, 1, 0])
    before = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]["attempts"]
    r = g.rx(d, "behavior", "CP1")
    gate = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-T01] the gate passes", r.returncode == 0 and gate["status"] == "passed",
          (r.returncode, r.stdout + r.stderr, gate))
    check("[CP6-T01] it is marked flaky", gate.get("flaky") is True, gate)
    check("[CP6-T01] attempts is unchanged from before this call", gate["attempts"] == before,
          (before, gate["attempts"]))


def t02_second_flaky_failure_not_forgiven():
    print("CP6-T02: a second flaky failure in the same step is not forgiven")
    d, _ = behavior_project("cp6-02", [1, 1, 0, 1, 0])
    r1 = g.rx(d, "behavior", "CP1")
    gate1 = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-T02] setup: the first flaky failure already passed and used this step's allowance",
          r1.returncode == 0 and gate1.get("flaky") is True, (r1.returncode, gate1))
    attempts_before = gate1["attempts"]
    r2 = g.rx(d, "behavior", "CP1")
    gate2 = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-T02] a second failure that would again pass on an immediate retry is reported as a normal failure",
          r2.returncode != 0 and gate2["status"] == "failed", (r2.returncode, r2.stdout + r2.stderr, gate2))
    check("[CP6-T02] attempts increases by one", gate2["attempts"] == attempts_before + 1,
          (attempts_before, gate2["attempts"]))


def t03_two_genuine_failures_never_flaky():
    print("CP6-T03: two genuine consecutive failures are never marked flaky")
    d, _ = behavior_project("cp6-03", [1, 1, 1])
    r = g.rx(d, "behavior", "CP1")
    gate = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-T03] reported failed, not flaky", r.returncode != 0 and gate["status"] == "failed"
          and not gate.get("flaky"), (r.returncode, gate))
    check("[CP6-T03] attempts increases by one", gate["attempts"] == 1, gate)


def t04_flaky_visible_in_status_evidence_and_final_review():
    print("CP6-T04: a flaky pass is visible in status, evidence and the final review")
    d, _ = behavior_project("cp6-04", [1, 1, 0])
    r = g.rx(d, "behavior", "CP1")
    gate = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-T04] setup: the flaky pass happened", r.returncode == 0 and gate.get("flaky") is True,
          (r.returncode, gate))
    sj = json.loads(g.rx(d, "status", "--json").stdout)
    cp_json = next((c for c in sj.get("checkpoints", []) if c["id"] == "CP1"), {})
    check("[CP6-T04] `status --json` marks the behavior gate flaky",
          (cp_json.get("gates", {}).get("behavior", {}) or {}).get("flaky") is True, cp_json)
    ev = Path(d) / ".ratchet/runs/f/evidence/CP1"
    logs = {p.name: p.read_text() for p in ev.glob("behavior-*.log")}
    check("[CP6-T04] both the failing and the passing behavior logs are kept in evidence",
          any("exit=1" in c for c in logs.values()) and any("exit=0" in c for c in logs.values()), sorted(logs))
    rnd = g.open_round(d)
    assert g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd, g.R0(rnd))).returncode == 0
    assert g.rx(d, "complete", "CP1").returncode == 0
    final_text = g.rx(d, "status").stdout
    check("[CP6-T04] the final review text lists this step as flaky",
          "CP1" in final_text and "flaky" in final_text.lower(), final_text)


# ---- CP6-T05, T06: the regression run's own one-time flaky allowance -------------------------------------------

def t05_regression_flaky_allowance():
    print("CP6-T05: the regression run at complete gets its own one-time flaky allowance")
    env = ts.make_env("claude")
    d = ts.project2(env)
    ts.pass_behavior2(d, env, "CP1", 1)
    rnd = ts.open_round(d, env, "CP1")
    _, r0 = ts.record_host_review(d, env, rnd, findings=[], cp="CP1", files_reviewed=["src/f1.js"])
    assert r0.returncode == 0, r0.stderr + r0.stdout
    cmd, _ = flaky_cmd_from_outcomes([1, 0])
    set_regression_command_confirmed(d, env, cmd)
    r = ts.rx(d, env, "complete", "CP1")
    check("[CP6-T05] complete succeeds on a regression failure that passes on an immediate re-run",
          r.returncode == 0, r.stdout + r.stderr)
    st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    check("[CP6-T05] no human-decision block is raised for it", st.get("blocked_reason") is None,
          st.get("blocked_reason"))
    ev = Path(d) / ".ratchet/runs/f/evidence/CP1"
    logs = {p.name: p.read_text() for p in ev.glob("regression*.log")}
    check("[CP6-T05] the regression result is marked flaky: both the failing and passing runs are kept",
          any("exit=1" in c for c in logs.values()) and any("exit=0" in c for c in logs.values()), sorted(logs))


def t06_second_regression_failure_not_forgiven():
    print("CP6-T06: a second regression failure is not forgiven")
    env = ts.make_env("claude")
    d = ts.project2(env)
    ts.pass_behavior2(d, env, "CP1", 1)
    rnd1 = ts.open_round(d, env, "CP1")
    _, r0 = ts.record_host_review(d, env, rnd1, findings=[], cp="CP1", files_reviewed=["src/f1.js"])
    assert r0.returncode == 0, r0.stderr + r0.stdout
    # CP1's complete: fails then passes on an immediate re-run (uses up the run's one-time allowance);
    # CP2's complete, later in the same run: fails again for real.
    cmd, _ = flaky_cmd_from_outcomes([1, 0, 1])
    set_regression_command_confirmed(d, env, cmd)
    r1 = ts.rx(d, env, "complete", "CP1")
    assert r1.returncode == 0, r1.stdout + r1.stderr  # setup precondition: CP1's flaky allowance was just used
    ts.pass_behavior2(d, env, "CP2", 2)
    rnd2 = ts.open_round(d, env, "CP2")
    _, r2a = ts.record_host_review(d, env, rnd2, findings=[], cp="CP2", files_reviewed=["src/f2.js"])
    assert r2a.returncode == 0, r2a.stderr + r2a.stdout
    r2 = ts.rx(d, env, "complete", "CP2")
    check("[CP6-T06] complete is refused, the regression failure reported plainly (not flaky)",
          r2.returncode != 0 and "Regression suite failed" in (r2.stdout + r2.stderr),
          (r2.returncode, r2.stdout + r2.stderr))
    st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    check("[CP6-T06] CP2 is not marked done", st["checkpoints"]["CP2"].get("status") != "done",
          st["checkpoints"]["CP2"].get("status"))


# ---- CP6-T07..T11: a cut-off reviewer reply is retried once, failing it and nothing else -----------------------

_CUTOFF_TEMPLATE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in
  *"connectivity check"*)
    if [ -n "$out" ]; then printf '%s' "RATCHET-PROBE-OK" > "$out"; fi
    printf '%s\n' "RATCHET-PROBE-OK"
    exit 0;;
esac
counter="__COUNTER__"
n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$counter"
mode="__MODE__"
case "$mode" in
  truncate_then_complete)
    if [ "$n" -eq 1 ]; then reply='{"findings": [{"id": "A1", "sev'
    else reply='{"findings": []}'
    fi;;
  always_truncated)
    reply='{"findings": [{"id": "A1", "sev';;
  plain_error)
    echo "error: quota exceeded" 1>&2
    exit 1;;
  timeout)
    sleep 999
    exit 0;;
esac
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""


def cutoff_bin(name, mode):
    """A fake agent CLI on its own private PATH whose reply depends on an invocation counter: a cut-off (truncated
    mid-JSON) reply once or always, a plain non-JSON error, or a hang past --timeout. Returns (bin_dir, counter)."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-cutoff-bin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename", "sleep"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    counter = Path(_tmp.mkdtemp(prefix="ratchet-cutoff-ctr-")) / "n"
    counter.write_text("0")
    script = _CUTOFF_TEMPLATE.replace("__COUNTER__", str(counter)).replace("__MODE__", mode)
    (b / name).write_text(script)
    (b / name).chmod(0o755)
    return b, counter


def run_external(d, env, rid, out, timeout=None):
    cmd = [sys.executable, str(EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out)]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    return subprocess.run(cmd, cwd=d, capture_output=True, text=True, env=env)


def t07_cutoff_retried_once():
    print("CP6-T07: a reply cut off mid-answer is retried once and the retry's reply is used")
    b, counter = cutoff_bin("codex", "truncate_then_complete")
    env = tp.env_with(b)
    d = g.project("cp6-07")
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    rid = tp.ids(rnd)[0]
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    r = run_external(d, env, rid, out)
    check("[CP6-T07] external_review.py succeeds using the retried, complete reply",
          r.returncode == 0 and out.exists(), (r.returncode, r.stderr, r.stdout))
    check("[CP6-T07] it is called exactly twice", int(counter.read_text() or "0") == 2, counter.read_text())
    body = json.loads(out.read_text()) if out.exists() else {}
    check("[CP6-T07] the review file written is the complete reply", body.get("findings") == [], body)
    rs = g.rx(d, "reviewer-status", env=env).stdout
    check("[CP6-T07] the reviewer is not marked unavailable (reviewer-status still shows it available)",
          re.search(rf"✓\s+{re.escape(rid)}\b", rs) is not None, rs)


def t08_second_cutoff_falls_back_only_slot():
    print("CP6-T08: a second cut-off in the same round falls back only that slot, not the whole provider")
    b, counter = cutoff_bin("codex", "always_truncated")
    env = tp.env_with(b)
    d = g.project("cp6-08", extra_cp={"risk": "normal"})
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    ids = tp.ids(rnd)
    check("[CP6-T08] setup: both review slots use the codex binary",
          len(ids) == 2 and all(i.startswith("codex") for i in ids), ids)
    rid_a, rid_b = ids[0], ids[1]
    out_a = Path(rnd["output_dir"]) / f"{rid_a}.json"
    r1 = run_external(d, env, rid_a, out_a)
    check("[CP6-T08] setup: the slot's own cut-off attempts (one retry) both fail",
          r1.returncode == 3 and int(counter.read_text() or "0") == 2, (r1.returncode, r1.stderr, counter.read_text()))
    rnd2 = tp.open_round(d, env)
    ids2 = tp.ids(rnd2)
    check("[CP6-T08] only the cut-off slot is replaced for this round",
          rid_b in ids2 and rid_a not in ids2 and len(ids2) == 2, ids2)
    rs = g.rx(d, "reviewer-status", env=env).stdout
    check("[CP6-T08] reviewer-status marks neither reviewer unavailable (unlike a genuine quota/auth failure)",
          re.search(rf"✓\s+{re.escape(rid_a)}\b", rs) is not None
          and re.search(rf"✓\s+{re.escape(rid_b)}\b", rs) is not None, rs)


def t09_finished_review_kept_when_other_slot_falls_back():
    print("CP6-T09: a finished review in the round is kept when another slot falls back")
    b, counter = cutoff_bin("codex", "always_truncated")
    env = tp.env_with(b)
    d = g.project("cp6-09", extra_cp={"risk": "normal"})
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    ids = tp.ids(rnd)
    rid_a, rid_b = ids[0], ids[1]
    path_a = Path(g.write_review(d, rnd, rid_a))
    before = path_a.read_text()
    out_b = Path(rnd["output_dir"]) / f"{rid_b}.json"
    r = run_external(d, env, rid_b, out_b)
    check("[CP6-T09] setup: the other slot's cut-off attempts both fail",
          r.returncode == 3, (r.returncode, r.stderr))
    rnd2 = tp.open_round(d, env)
    check("[CP6-T09] the already-written review file is untouched (same content, same path)",
          path_a.exists() and path_a.read_text() == before, str(path_a))
    check("[CP6-T09] that slot is not re-resolved or asked to run again", rid_a in tp.ids(rnd2), tp.ids(rnd2))


def t10_plain_failure_no_retry():
    print("CP6-T10: a plain no-JSON failure is still marked unavailable immediately, with no retry")
    b, counter = cutoff_bin("codex", "plain_error")
    env = tp.env_with(b)
    d = g.project("cp6-10")
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    rid = tp.ids(rnd)[0]
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    r = run_external(d, env, rid, out)
    check("[CP6-T10] called exactly once (no retry wasted on a reply that was never JSON)",
          int(counter.read_text() or "0") == 1, counter.read_text())
    check("[CP6-T10] marked unavailable and the slot falls back",
          r.returncode == 3 and "UNAVAILABLE" in r.stderr, (r.returncode, r.stderr))
    rs = g.rx(d, "reviewer-status", env=env).stdout
    check("[CP6-T10] reviewer-status shows it unavailable", re.search(rf"·\s+{re.escape(rid)}\b", rs) is not None, rs)


def t11_timeout_once_not_twice():
    print("CP6-T11: a hung reviewer times out once, not twice")
    b, counter = cutoff_bin("codex", "timeout")
    env = tp.env_with(b)
    d = g.project("cp6-11")
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', "min_review_timeout_sec=1", env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    rid = tp.ids(rnd)[0]
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    r = run_external(d, env, rid, out, timeout=2)
    check("[CP6-T11] called exactly once (the cut-off retry never doubles the wait on a hang)",
          int(counter.read_text() or "0") == 1, counter.read_text())
    check("[CP6-T11] marked unavailable after the single timeout", r.returncode == 3, (r.returncode, r.stderr))


# ---- CP6-T12: docs describe both rules --------------------------------------------------------------------------

def t12_docs_describe_both_rules():
    print("CP6-T12: docs describe both the flaky-test allowance and the cut-off retry")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md").read_text(encoding="utf-8")
    retry_re = re.compile(r"retr(?:y|ied).{0,25}once|once.{0,25}retr(?:y|ied)", re.I | re.S)
    check("[CP6-T12] README describes the one-time flaky test allowance", re.search(r"flaky", readme, re.I) is not None,
          "no 'flaky' in README.md")
    check("[CP6-T12] README describes the cut-off reviewer retry rule", retry_re.search(readme) is not None,
          "no retry-once wording in README.md")
    check("[CP6-T12] schemas.md describes the flaky allowance", re.search(r"flaky", schemas, re.I) is not None,
          "no 'flaky' in schemas.md")
    check("[CP6-T12] the adversarial-loop skill describes the cut-off retry rule", retry_re.search(skill) is not None,
          "no retry-once wording in ratchet-adversarial-loop/SKILL.md")


# ---- CP6-T13..T15: the prompt hook tells a sub-agent hand-back apart from the user's own message ----------------

def final_review_project(name):
    d = g.project(name)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    assert g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd, g.R0(rnd))).returncode == 0
    assert g.rx(d, "complete", "CP1").returncode == 0
    g.rx(d, "ask", "human-approve")
    return d


def _human_log(d):
    p = Path(d) / ".ratchet" / "human_log.jsonl"
    return p.read_text() if p.exists() else ""


def t13_handback_not_recorded():
    print('CP6-T13: a sub-agent hand-back wrapped in the real marker is not recorded as the user\'s reply')
    d = final_review_project("cp6-13")
    before = _human_log(d)
    r = g.say(d, '<agent-message from="builder">yes</agent-message>')
    after = _human_log(d)
    check("[CP6-T13] the hook tells the agent this turn was not typed by the user",
          "not typed by the user" in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("[CP6-T13] the turn is not appended to human_log.jsonl", after == before, (before, after))
    r2 = g.rx(d, "human-approve")
    check("[CP6-T13] the waiting question stays unanswered", r2.returncode != 0, (r2.returncode, r2.stdout + r2.stderr))


def t14_rendering_string_still_caught():
    print("CP6-T14: the on-screen rendering string alone is still caught")
    d = final_review_project("cp6-14")
    before = _human_log(d)
    r = g.say(d, "Another Claude session sent a message: yes")
    after = _human_log(d)
    check("[CP6-T14] still told apart as not typed by the user",
          "not typed by the user" in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("[CP6-T14] still not appended to human_log.jsonl", after == before, (before, after))


def t15_midsentence_mention_recorded():
    print("CP6-T15: a real user message that only mentions agent-message mid-sentence is recorded")
    d = final_review_project("cp6-15")
    before = _human_log(d)
    text = "I saw an agent-message come through earlier, but yes, go ahead"
    r = g.say(d, text)
    after = _human_log(d)
    check("[CP6-T15] not flagged as agent-generated",
          "not typed by the user" not in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("[CP6-T15] recorded as usual in human_log.jsonl", after != before and text in after, (before, after))


# ---- CP6-T16..T18: a too-large review diff is prioritized by what changed since the previous round --------------

FILLER_MARKER = "FILLER-UNIQUE-9f3a21"
ROUND2_MARKER = "ROUND2-UNIQUE-7b2c48"


def _prompt_dry_run(d, env, rid):
    """Runs external_review.py --dry-run for `rid` and returns the written prompt's text."""
    out = Path(_tmp.mkdtemp(prefix="ratchet-prompt-out-")) / "out.json"
    r = subprocess.run([sys.executable, str(EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out), "--dry-run"],
                       cwd=d, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"prompt written: (\S+) ", r.stdout)
    assert m, r.stdout
    return Path(m.group(1)).read_text()


def truncation_project(name, small_edit=False):
    """A low-risk project with one command reviewer (fake 'codex'), a huge filler file written right after the
    plan is approved (so it is part of round 1's diff already), round 1 approved with no findings, then a tweak
    to src/f.js to open round 2 at a new fingerprint. Returns (d, env, rnd1, rnd2). With `small_edit` False
    (the default, for T16/T17: a decisive UNSCOPED full round), the tweak is well over `small_edit_lines`
    (default 20) so round 2 stays a normal full round -- not a lane H CP6 scoped small fix -- and keeps the
    pre-CP6 whole-checkpoint-diff truncation/prioritization this project exists to test. With `small_edit` True,
    the tweak is one line, so round 2 IS a CP6 scoped small fix, for the test that checks scoping excludes the
    filler entirely rather than merely deprioritizing it."""
    b = tp.fake_bin("codex")
    env = tp.env_with(b)
    d = g.project(name, extra_cp={"risk": "low"})
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    (Path(d) / "src" / "aaa-filler.js").write_text((f"// {FILLER_MARKER}\n") * 10000)  # ~220K chars, over MAX_DIFF
    g.write_tests(d)
    assert g.rx(d, "tests-written", "CP1").returncode == 0
    assert g.rx(d, "red", "CP1").returncode == 0
    (Path(d) / "src/f.js").write_text("export const f = () => 1;\n")
    assert g.rx(d, "implemented", "CP1").returncode == 0
    assert g.rx(d, "behavior", "CP1").returncode == 0
    rnd1 = tp.open_round(d, env)
    rid1 = tp.ids(rnd1)[0]
    out1 = Path(rnd1["output_dir"]) / f"{rid1}.json"
    rp = run_external(d, env, rid1, out1)
    assert rp.returncode == 0, rp.stdout + rp.stderr
    r0 = g.rx(d, "adv-record", "CP1", "--reviews", str(out1), env=env)
    assert r0.returncode == 0, r0.stdout + r0.stderr
    if small_edit:
        (Path(d) / "src/f.js").write_text(f"export const f = () => 1; // {ROUND2_MARKER}\n")
    else:
        pad = "".join(f"export const pad_{i} = {i}; // filler padding line {i}\n" for i in range(24))
        (Path(d) / "src/f.js").write_text(pad + f"export const f = () => 1; // {ROUND2_MARKER}\n")
    rnd2 = tp.open_round(d, env)
    return d, env, rnd1, rnd2


def t16_prioritizes_files_changed_since_previous_round():
    print("CP6-T16: a too-large review diff puts files changed since the previous round first")
    d, env, rnd1, rnd2 = truncation_project("cp6-16")
    check("[CP6-T16] setup: round 2 is open at a new fingerprint",
          rnd2["round"] == rnd1["round"] + 1 and rnd2["fingerprint"] != rnd1["fingerprint"], (rnd1, rnd2))
    check("[CP6-T16] setup: round 2 is a normal full round, not a lane H CP6 scoped small fix -- this project "
          "tests the pre-CP6 whole-checkpoint truncation/prioritization, which only an unscoped round still uses",
          rnd2.get("small_fix") is not True and rnd2.get("scope") is None, rnd2)
    prompt = _prompt_dry_run(d, env, tp.ids(rnd2)[0])
    i_round2 = prompt.find(ROUND2_MARKER)
    i_filler = prompt.find(FILLER_MARKER)
    check("[CP6-T16] the file changed since round 1 (src/f.js) is present in the diff",
          i_round2 != -1, prompt[:0])
    check("[CP6-T16] it appears before the rest of the diff (the unchanged-since-round-1 filler file)",
          i_round2 != -1 and i_filler != -1 and i_round2 < i_filler, (i_round2, i_filler))
    i_cut = prompt.find("truncated")
    check("[CP6-T16] it is not the part cut by the size limit",
          i_round2 != -1 and (i_cut == -1 or i_round2 < i_cut), (i_round2, i_cut))


def t17_names_which_files_were_cut():
    print("CP6-T17: the prompt names which files were cut")
    d, env, rnd1, rnd2 = truncation_project("cp6-17")
    prompt = _prompt_dry_run(d, env, tp.ids(rnd2)[0])
    check("[CP6-T17] names the left-out file (src/aaa-filler.js) by name, not only a generic count",
          "src/aaa-filler.js" in prompt, prompt[-0:])
    cut_mention = re.search(r"(left out|cut|omitted|truncated)[^\n]{0,200}src/aaa-filler\.js"
                            r"|src/aaa-filler\.js[^\n]{0,200}(left out|cut|omitted|truncated)", prompt, re.I)
    check("[CP6-T17] names it plainly, next to a cut/left-out explanation",
          cut_mention is not None, "no 'left out'/'cut' wording near the filename")


def t18_round1_keeps_plain_order():
    print("CP6-T18: round 1 keeps the plain diff order")
    # truncation_project() opens round 2 before returning, so passing round 1's reviewer id to _prompt_dry_run
    # would read round 2's currently-open prompt instead. _truncation_round1_only() stops after round 1, so this
    # inspects the round it actually claims to.
    d, env, rnd1 = _truncation_round1_only("cp6-18")
    prompt = _prompt_dry_run(d, env, tp.ids(rnd1)[0])
    check("[CP6-T18] the diff is still produced and truncated as before this change",
          "diff truncated at" in prompt and "180" in prompt, prompt[-0:])
    check("[CP6-T18] round 1 (no previous round) applies no previous-round prioritization",
          re.search(r"previous round|changed since round", prompt, re.I) is None, prompt[-0:])


def t19_scoped_small_fix_excludes_the_filler_entirely():
    print("CP6-X10: a scoped small-fix round (lane H CP6) omits the unrelated filler file outright, not just "
          "deprioritized behind it")
    d, env, rnd1, rnd2 = truncation_project("cp6-19", small_edit=True)
    check("[CP6-X10] setup: this tiny fix opens round 2 as a CP6 scoped small fix",
          rnd2.get("small_fix") is True and rnd2.get("scope") is not None, rnd2)
    prompt = _prompt_dry_run(d, env, tp.ids(rnd2)[0])
    check("[CP6-X10] the file actually fixed (src/f.js) is in the scoped prompt",
          ROUND2_MARKER in prompt, prompt[-0:])
    check("[CP6-X10] the untouched filler file is excluded entirely -- not merely moved after it, truncated, or "
          "named as cut, since it was never in this round's scope to begin with",
          FILLER_MARKER not in prompt and "src/aaa-filler.js" not in prompt, prompt[-0:])


TESTS = [
    ("CP6-T01", t01_flaky_behavior_passes_without_attempt),
    ("CP6-T02", t02_second_flaky_failure_not_forgiven),
    ("CP6-T03", t03_two_genuine_failures_never_flaky),
    ("CP6-T04", t04_flaky_visible_in_status_evidence_and_final_review),
    ("CP6-T05", t05_regression_flaky_allowance),
    ("CP6-T06", t06_second_regression_failure_not_forgiven),
    ("CP6-T07", t07_cutoff_retried_once),
    ("CP6-T08", t08_second_cutoff_falls_back_only_slot),
    ("CP6-T09", t09_finished_review_kept_when_other_slot_falls_back),
    ("CP6-T10", t10_plain_failure_no_retry),
    ("CP6-T11", t11_timeout_once_not_twice),
    ("CP6-T12", t12_docs_describe_both_rules),
    ("CP6-T13", t13_handback_not_recorded),
    ("CP6-T14", t14_rendering_string_still_caught),
    ("CP6-T15", t15_midsentence_mention_recorded),
    ("CP6-T16", t16_prioritizes_files_changed_since_previous_round),
    ("CP6-T17", t17_names_which_files_were_cut),
    ("CP6-T18", t18_round1_keeps_plain_order),
    ("CP6-X10", t19_scoped_small_fix_excludes_the_filler_entirely),
    ("CP6-X01", lambda: x01_cutoff_then_genuine_failure_marks_unavailable()),
    ("CP6-X02", lambda: x02_unicode_filename_not_dropped_from_diff()),
    ("CP6-X03", lambda: x03_flaky_log_not_overwritten_and_mark_sticky()),
]


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
        except Exception as e:  # a crash still counts as a result for this case, not a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


# ---- CP6-X01..X03: adv-round-01 fixes (retry misclassification, unicode diff paths, flaky log/mark) -----------

_CUTOFF_THEN_ERROR_TEMPLATE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in
  *"connectivity check"*)
    if [ -n "$out" ]; then printf '%s' "RATCHET-PROBE-OK" > "$out"; fi
    printf '%s\n' "RATCHET-PROBE-OK"
    exit 0;;
esac
counter="__COUNTER__"
n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$counter"
if [ "$n" -eq 1 ]; then
  reply='{"findings": [{"id": "A1", "sev'
  if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
  printf '%s\n' "$reply"
else
  echo "error: quota exceeded" 1>&2
  exit 1
fi
"""


def cutoff_then_quota_bin(name):
    """A fake agent CLI: cut off mid-JSON on its first call, then a genuine (non-JSON) quota error on its retry.
    Used to check that the retry is classified by its OWN reply, not assumed to be another cut-off."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-cutoff2-bin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    counter = Path(_tmp.mkdtemp(prefix="ratchet-cutoff2-ctr-")) / "n"
    counter.write_text("0")
    script = _CUTOFF_THEN_ERROR_TEMPLATE.replace("__COUNTER__", str(counter))
    (b / name).write_text(script)
    (b / name).chmod(0o755)
    return b, counter


def x01_cutoff_then_genuine_failure_marks_unavailable():
    print("[CP6-X01] a retry that reports a genuine failure (not another cut-off) is marked unavailable")
    b, counter = cutoff_then_quota_bin("codex")
    env = tp.env_with(b)
    d = g.project("cp6-x01")
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    rid = tp.ids(rnd)[0]
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    r = run_external(d, env, rid, out)
    check("[CP6-X01] called exactly twice (one retry after the cut-off)",
          int(counter.read_text() or "0") == 2, counter.read_text())
    check("[CP6-X01] marked unavailable (a genuine failure on the retry, not a second cut-off)",
          r.returncode == 3 and "UNAVAILABLE" in r.stderr, (r.returncode, r.stderr))
    check("[CP6-X01] no cutoff-only marker was written (it would wrongly leave the provider available)",
          not (out.parent / f"{rid}.cutoff.json").exists(), list(out.parent.glob("*")))
    rs = g.rx(d, "reviewer-status", env=env).stdout
    check("[CP6-X01] reviewer-status shows it unavailable, not still available",
          re.search(rf"·\s+{re.escape(rid)}\b", rs) is not None, rs)


def x02_unicode_filename_not_dropped_from_diff():
    print("[CP6-X02] a file with a non-ASCII name changed since the previous round is not dropped from the diff")
    b = tp.fake_bin("codex")
    env = tp.env_with(b)
    d = g.project("cp6-x02")
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = tp.open_round(d, env)
    rid1 = tp.ids(rnd1)[0]
    out1 = Path(rnd1["output_dir"]) / f"{rid1}.json"
    rp = run_external(d, env, rid1, out1)
    assert rp.returncode == 0, rp.stdout + rp.stderr
    r0 = g.rx(d, "adv-record", "CP1", "--reviews", str(out1), env=env)
    assert r0.returncode == 0, r0.stdout + r0.stderr
    unicode_name = "café.js"
    marker = "UNICODE-FILE-UNIQUE-4d8e71"
    (Path(d) / "src" / unicode_name).write_text(f"// {marker}\nexport const cafe = 1;\n")
    rnd2 = tp.open_round(d, env)
    check("[CP6-X02] setup: round 2 is open at a new fingerprint",
          rnd2["round"] == rnd1["round"] + 1 and rnd2["fingerprint"] != rnd1["fingerprint"], (rnd1, rnd2))
    prompt = _prompt_dry_run(d, env, tp.ids(rnd2)[0])
    check("[CP6-X02] the unicode-named file's own content marker is present in the diff",
          marker in prompt, prompt[-0:])
    check("[CP6-X02] its filename appears as plain UTF-8, not dropped or octal-escaped",
          unicode_name in prompt and "\\303\\251" not in prompt, prompt[-0:])


def _truncation_round1_only(name):
    """Like truncation_project(), but stops after round 1 is open (never opens round 2), so a test that wants
    round 1's own dry-run prompt actually inspects round 1, not whatever round happens to be open afterward."""
    b = tp.fake_bin("codex")
    env = tp.env_with(b)
    d = g.project(name, extra_cp={"risk": "low"})
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    (Path(d) / "src" / "aaa-filler.js").write_text((f"// {FILLER_MARKER}\n") * 10000)  # ~220K chars, over MAX_DIFF
    g.write_tests(d)
    assert g.rx(d, "tests-written", "CP1").returncode == 0
    assert g.rx(d, "red", "CP1").returncode == 0
    (Path(d) / "src/f.js").write_text("export const f = () => 1;\n")
    assert g.rx(d, "implemented", "CP1").returncode == 0
    assert g.rx(d, "behavior", "CP1").returncode == 0
    rnd1 = tp.open_round(d, env)
    return d, env, rnd1


def x03_flaky_log_not_overwritten_and_mark_sticky():
    print("[CP6-X03] a flaky pass's logs are never reused by a later invocation, and the flaky mark stays sticky")
    d, _ = behavior_project("cp6-x03", [1, 1, 0, 0])
    r1 = g.rx(d, "behavior", "CP1")
    gate1 = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-X03] setup: the first call passed flaky", r1.returncode == 0 and gate1.get("flaky") is True,
          (r1.returncode, gate1))
    ev = Path(d) / ".ratchet/runs/f/evidence/CP1"
    before_logs = {p.name: p.read_text() for p in ev.glob("behavior-*.log")}
    r2 = g.rx(d, "behavior", "CP1")
    gate2 = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-X03] the second, clean call also passes", r2.returncode == 0 and gate2["status"] == "passed",
          (r2.returncode, r2.stdout + r2.stderr, gate2))
    after_logs = {p.name: p.read_text() for p in ev.glob("behavior-*.log")}
    check("[CP6-X03] none of the first call's logs were overwritten",
          all(after_logs.get(n) == c for n, c in before_logs.items()), (before_logs, after_logs))
    check("[CP6-X03] the second call got its own, new log file(s)",
          len(after_logs) > len(before_logs), (sorted(before_logs), sorted(after_logs)))
    check("[CP6-X03] the flaky mark stays sticky even though the second call was a clean pass",
          gate2.get("flaky") is True, gate2)
    sj = json.loads(g.rx(d, "status", "--json").stdout)
    cp_json = next((c for c in sj.get("checkpoints", []) if c["id"] == "CP1"), {})
    check("[CP6-X03] status --json still marks it flaky after the clean second call",
          (cp_json.get("gates", {}).get("behavior", {}) or {}).get("flaky") is True, cp_json)


# ---- CP6-X04: adv-round-02 fix (a complete structured JSON error is never mistaken for a cut-off reply) -------

_JSON_ERROR_TEMPLATE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in
  *"connectivity check"*)
    if [ -n "$out" ]; then printf '%s' "RATCHET-PROBE-OK" > "$out"; fi
    printf '%s\n' "RATCHET-PROBE-OK"
    exit 0;;
esac
counter="__COUNTER__"
n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$counter"
reply='{"error": {"message": "quota exceeded"}}'
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""


def json_error_bin(name):
    """A fake agent CLI that always replies with a complete, structured JSON error (never a 'findings'-shaped
    object), on every call. Used to check that a complete JSON reply with no findings-reply key is never treated
    as a cut-off, however many times it would be called."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-jsonerr-bin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    counter = Path(_tmp.mkdtemp(prefix="ratchet-jsonerr-ctr-")) / "n"
    counter.write_text("0")
    script = _JSON_ERROR_TEMPLATE.replace("__COUNTER__", str(counter))
    (b / name).write_text(script)
    (b / name).chmod(0o755)
    return b, counter


def x04_structured_json_error_not_mistaken_for_cutoff():
    print("[CP6-X04] a complete structured JSON error marks the provider unavailable, not a cut-off")
    b, counter = json_error_bin("codex")
    env = tp.env_with(b)
    d = g.project("cp6-x04")
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = tp.open_round(d, env)
    rid = tp.ids(rnd)[0]
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    r = run_external(d, env, rid, out)
    check("[CP6-X04] called exactly once (a complete JSON error is never treated as cut off, so no retry runs)",
          int(counter.read_text() or "0") == 1, counter.read_text())
    check("[CP6-X04] marked unavailable, same as any other genuine failure",
          r.returncode == 3 and "UNAVAILABLE" in r.stderr, (r.returncode, r.stderr))
    check("[CP6-X04] no cutoff-only marker was written (it would wrongly leave the provider available)",
          not (out.parent / f"{rid}.cutoff.json").exists(), list(out.parent.glob("*")))
    rs = g.rx(d, "reviewer-status", env=env).stdout
    check("[CP6-X04] reviewer-status shows it unavailable, not still available",
          re.search(rf"·\s+{re.escape(rid)}\b", rs) is not None, rs)


TESTS.append(("CP6-X04", lambda: x04_structured_json_error_not_mistaken_for_cutoff()))


# ---- CP6-X05..X07: adv-round-03 fix (raw_decode-based parsing) — an escaped quote inside a finding's own text,
# next to closing braces, must never misclassify a complete reply as cut off, and a reply genuinely truncated
# mid-string must still be classified as cut off -----------------------------------------------------------------

import importlib.util as _ilu  # noqa: E402  (new import, its own line, outside every planned test above)


def _load_external_review():
    spec = _ilu.spec_from_file_location("ratchet_external_review_test", EXT)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The real, complete reply codex-correctness wrote in CP6 adv-round-03 (copied verbatim from this run's
# reviews/CP6/adv-round-03/codex-correctness.raw.txt, first line): a valid findings-reply JSON object whose own
# "description" text quotes an escaped-quote example (\"...\") right next to closing braces — the exact shape
# that the old hand-written brace/string scanner misclassified as cut off, which this round's open-findings A1
# raised with this very reply as the evidence.
CODEX_CORRECTNESS_RAW_REPLY = '{"reviewer":"codex-correctness","lens":"correctness","checkpoint":"CP6","fingerprint":"3d6ce518844e7ad95e9ae5a39d7737a9a1e58368","verdict":"CHANGES_REQUESTED","findings":[{"id":"X1","severity":"major","kind":"cutoff-misclassification","file":"skills/ratchet-adversarial-loop/scripts/external_review.py","line":331,"rule":"CP6 requires truncated reviewer replies to receive one retry without sidelining the provider.","description":"The string scanner clears esc before checking whether a quote was escaped, so an escaped quote incorrectly ends the JSON string. For example, a reply truncated at {\\"reviewer\\":\\"codex-correctness\\",\\"findings\\":[{\\"description\\":\\"Remove the extra closing braces \\\\\\"}}\\\\\\" from the template is classified as complete: the two braces inside the description close the nested and outer objects in the scanner. _looks_cut_off returns false, skipping the retry and marking the entire provider unavailable.","fix":"Check whether a character is escaped before updating the escape state; escaped quotes must not leave string mode. Add a regression test with a truncated finding containing escaped quotes and closing braces, asserting that it retries and does not mark the provider unavailable."}]}'


def x05_real_codex_reply_with_escaped_quotes_parses_as_complete():
    print("[CP6-X05] the real codex-correctness reply quoting an escaped quote next to braces parses as complete")
    er = _load_external_review()
    review = er.parse_findings(CODEX_CORRECTNESS_RAW_REPLY)
    check("[CP6-X05] parse_findings returns the findings-reply object, not None",
          isinstance(review, dict) and review.get("reviewer") == "codex-correctness", review)
    check("[CP6-X05] its one finding is kept intact",
          isinstance(review, dict) and isinstance(review.get("findings"), list) and len(review["findings"]) == 1,
          review and review.get("findings"))
    check("[CP6-X05] _looks_cut_off does not mistake this complete reply for a truncation",
          er._looks_cut_off(CODEX_CORRECTNESS_RAW_REPLY) is False, None)


def x06_escaped_quote_inside_description_parses():
    print("[CP6-X06] a reply with escaped quotes inside a finding's own description parses correctly")
    er = _load_external_review()
    reply = ('{"reviewer":"x","verdict":"CHANGES_REQUESTED","findings":'
             '[{"id":"A1","description":"remove \\"}}\\" from the template","sev":"major"}]}')
    review = er.parse_findings(reply)
    check("[CP6-X06] parse_findings parses the whole object despite the escaped quotes",
          isinstance(review, dict) and review.get("findings"), review)
    check("[CP6-X06] the description text keeps its escaped quotes and braces as literal characters",
          isinstance(review, dict) and review["findings"][0]["description"] == 'remove "}}" from the template',
          review)
    check("[CP6-X06] _looks_cut_off does not mistake this complete reply for a truncation",
          er._looks_cut_off(reply) is False, None)


def x07_truncated_mid_escaped_string_is_cut_off():
    print("[CP6-X07] the same reply, truncated mid-string right after the escaped quote, is classified as cut off")
    er = _load_external_review()
    # Cut the X06 reply off partway through the description's text, right after its escaped quote and with no
    # closing quote: a genuine truncation, not a complete reply, and the kind of mid-string cut the old
    # escaped-quote bug let through unretried.
    truncated = '{"reviewer":"x","verdict":"CHANGES_REQUESTED","findings":[{"id":"A1","description":"remove \\"}}\\" fr'
    check("[CP6-X07] parse_findings finds no complete findings-reply object",
          er.parse_findings(truncated) is None, truncated)
    check("[CP6-X07] _looks_cut_off classifies it as cut off, so it is retried rather than marked unavailable",
          er._looks_cut_off(truncated) is True, truncated)


TESTS.append(("CP6-X05", x05_real_codex_reply_with_escaped_quotes_parses_as_complete))
TESTS.append(("CP6-X06", x06_escaped_quote_inside_description_parses))
TESTS.append(("CP6-X07", x07_truncated_mid_escaped_string_is_cut_off))


# ---- CP6-X08 (adv-round-04 fix A1): a finding whose own evidence nests a literal "findings" key must not be
# mistaken for the real top-level reply -------------------------------------------------------------------------

def x08_nested_findings_key_does_not_overwrite_outer_reply():
    print("[CP6-X08] a finding citing 'evidence': {'findings': 2} still parses to the real outer reply")
    er = _load_external_review()
    reply = ('{"reviewer":"x","verdict":"CHANGES_REQUESTED","findings":'
             '[{"id":"A1","description":"d","evidence":{"findings":2}}]}')
    review = er.parse_findings(reply)
    check("[CP6-X08] parse_findings returns the outer reply object, not the nested evidence dict",
          isinstance(review, dict) and review.get("reviewer") == "x", review)
    check("[CP6-X08] the outer reply's findings list is kept intact, with the nested evidence untouched",
          isinstance(review, dict) and isinstance(review.get("findings"), list) and len(review["findings"]) == 1
          and review["findings"][0].get("evidence") == {"findings": 2}, review)


TESTS.append(("CP6-X08", x08_nested_findings_key_does_not_overwrite_outer_reply))


# ---- CP6-X09 (adv-round-04 fix B3): the flaky allowance is spent the moment a retry is attempted, win or lose,
# not only when the retry succeeds -------------------------------------------------------------------------------

def x09_flaky_allowance_spent_on_attempted_retry_even_if_it_fails():
    print("[CP6-X09] a call whose automatic retry also fails still spends the step's flaky allowance")
    # outcomes[0] is red's own required failure. Call 1 sees outcomes[1] and outcomes[2] (both failures: the
    # retry is attempted and also fails, a genuine, correctly-counted single attempt, not flaky). Call 2 sees
    # outcomes[3] (a failure) and, if a free retry were wrongly granted again, outcomes[4] (a pass) next.
    d, counter = behavior_project("cp6-x09", [1, 1, 1, 1, 0])
    r1 = g.rx(d, "behavior", "CP1")
    gate1 = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-X09] call 1: reported as a real failure, not flaky",
          r1.returncode != 0 and gate1["status"] == "failed" and not gate1.get("flaky"),
          (r1.returncode, gate1))
    check("[CP6-X09] call 1: attempts increases by exactly one", gate1["attempts"] == 1, gate1)
    check("[CP6-X09] call 1: the step's flaky allowance is already marked used, even though the retry failed",
          gate1.get("flaky_used") is True, gate1)

    r2 = g.rx(d, "behavior", "CP1")
    gate2 = state(d)["checkpoints"]["CP1"]["gates"]["behavior"]
    check("[CP6-X09] call 2: gets no free retry, so it fails on its first run rather than passing",
          r2.returncode != 0 and gate2["status"] == "failed" and not gate2.get("flaky"),
          (r2.returncode, gate2))
    check("[CP6-X09] call 2: attempts increases again (two real attempts total), not left at one",
          gate2["attempts"] == 2, gate2)
    check("[CP6-X09] call 2 never ran a retry: the command was invoked exactly 4 times total (red + 2 per call), "
          "the would-be-passing 5th outcome was never consumed",
          int(counter.read_text() or "0") == 4, counter.read_text())


TESTS.append(("CP6-X09", x09_flaky_allowance_spent_on_attempted_retry_even_if_it_fails))


# ---- CP6-X10 (adv-round-06 fix D1): a round replaced for free (nobody reviewed it) must not become the next
# round's diff-prioritization baseline -- the baseline stays the last round a reviewer actually reviewed ---------

def x10_free_replace_keeps_last_reviewed_baseline():
    print("[CP6-X10] round 2 replaced before any review: round 3's baseline is still round 1 (last reviewed), "
          "never round 2 (never reviewed)")
    d = g.project("cp6-x10")
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = g.open_round(d)
    rid1 = g.R0(rnd1)
    r0 = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd1, rid1))
    assert r0.returncode == 0, r0.stderr
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // round2\n")
    rnd2 = g.open_round(d)
    check("[CP6-X10] setup: round 2 opened at a new fingerprint",
          rnd2["round"] == 2 and rnd2["fingerprint"] != rnd1["fingerprint"], rnd2)
    before = state(d)["checkpoints"]["CP1"]["adv_round"]["prev_round_tree"]
    check("[CP6-X10] setup: round 2's own baseline is round 1's reviewed fingerprint",
          before == rnd1["fingerprint"], (before, rnd1["fingerprint"]))
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // round3, round2 never reviewed\n")
    rnd3 = g.open_round(d)  # replaces round 2 for free: round 2's own review folder is still empty
    check("[CP6-X10] round 2 was replaced for free (a new fingerprint for round 3)",
          rnd3["round"] == 3 and rnd3["fingerprint"] != rnd2["fingerprint"], rnd3)
    after = state(d)["checkpoints"]["CP1"]["adv_round"]["prev_round_tree"]
    check("[CP6-X10] round 3's baseline is still round 1's reviewed tree, not round 2's discarded fingerprint",
          after == rnd1["fingerprint"] and after != rnd2["fingerprint"], (after, rnd1["fingerprint"], rnd2["fingerprint"]))


TESTS.append(("CP6-X10", x10_free_replace_keeps_last_reviewed_baseline))


# ---- CP6-X11 (adv-round-07 fix E1): a round where every reviewer slot only ever left a cutoff marker (no real
# review) is still treated as unreviewed -- the next round's baseline stays the last round a reviewer actually
# reviewed, never the cut-off round's own fingerprint --------------------------------------------------------------

def x11_all_cutoff_markers_round_is_free_replace():
    print("[CP6-X11] a round where both slots only left .cutoff.json markers is still unreviewed: the next "
          "round's baseline stays the last round a reviewer actually reviewed")
    good_env = tp.env_with(tp.fake_bin("codex"))
    cut_bin, counter = cutoff_bin("codex", "always_truncated")
    cut_env = tp.env_with(cut_bin)
    d = g.project("cp6-x11", extra_cp={"risk": "normal"})
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', 'provider_share={"max":1000,"minutes":15}', env=good_env)
    # Keep round 2 a full two-slot round: disable the small-fix one-reviewer path (CP3) so this
    # test still covers two cutoff slots.
    g.rx(d, "config", "--set", "small_edit_lines=0", env=good_env)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd1 = tp.open_round(d, good_env)
    reviews1 = []
    for rid in tp.ids(rnd1):
        out = Path(rnd1["output_dir"]) / f"{rid}.json"
        r = run_external(d, good_env, rid, out)
        assert r.returncode == 0, r.stderr
        reviews1.append(str(out))
    r0 = g.rx(d, "adv-record", "CP1", "--reviews", *reviews1, env=good_env)
    assert r0.returncode == 0, r0.stderr
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // round2\n")
    rnd2 = tp.open_round(d, cut_env)
    ids2 = tp.ids(rnd2)
    check("[CP6-X11] setup: round 2 opened with two codex slots",
          len(ids2) == 2 and all(i.startswith("codex") for i in ids2), ids2)
    for rid in ids2:
        out = Path(rnd2["output_dir"]) / f"{rid}.json"
        r = run_external(d, cut_env, rid, out)
        check(f"[CP6-X11] setup: slot {rid}'s cut-off attempts (one retry) both fail, leaving only a cutoff marker",
              r.returncode == 3 and not out.exists() and (out.parent / f"{rid}.cutoff.json").exists(),
              (r.returncode, r.stderr))
    before = state(d)["checkpoints"]["CP1"]["adv_round"]["prev_round_tree"]
    check("[CP6-X11] setup: round 2's own baseline is round 1's reviewed fingerprint",
          before == rnd1["fingerprint"], (before, rnd1["fingerprint"]))
    (Path(d) / "src/f.js").write_text("export const f = () => 1; // round3, round2 never reviewed\n")
    rnd3 = tp.open_round(d, cut_env)
    check("[CP6-X11] round 3 opened at a new fingerprint, replacing round 2 which only ever had cutoff markers",
          rnd3["round"] == 3 and rnd3["fingerprint"] != rnd2["fingerprint"], rnd3)
    after = state(d)["checkpoints"]["CP1"]["adv_round"]["prev_round_tree"]
    check("[CP6-X11] round 3's baseline is still round 1's reviewed tree, not round 2's never-reviewed fingerprint",
          after == rnd1["fingerprint"] and after != rnd2["fingerprint"],
          (after, rnd1["fingerprint"], rnd2["fingerprint"]))


TESTS.append(("CP6-X11", x11_all_cutoff_markers_round_is_free_replace))


if __name__ == "__main__":
    main()
