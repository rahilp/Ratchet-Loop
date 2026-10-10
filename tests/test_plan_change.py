#!/usr/bin/env python3
"""
CP6 test: approve-changes must not leave a newly-added checkpoint without a state entry.   python3 tests/test_plan_change.py

When a user approves a plan change that adds a new checkpoint to checkpoints.json (edited directly, not through
feedback + plan-extend), `approve-changes` must give the new checkpoint a state entry of its own (the same shape
plan-extend creates for a new checkpoint), built in plan order, so that a later `complete` of the current
checkpoint and `next` do not crash with a KeyError once they reach the new checkpoint.

Reuses tests/test_gates.py's project()/approve_plan()/write_tests()/say()/rx() helpers.

Writes a per-test JSON report to RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP6-X01", "status": "passed"|"failed",
"message": "..."}]}.
"""
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import test_gates as g  # noqa: E402  (project, approve_plan, write_tests, say, rx)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP6-X\d+)\]")


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


def x01_approve_changes_builds_state_for_new_checkpoint():
    print("CP6-X01: approve-changes gives a newly-added checkpoint a state entry, so complete/next don't crash")
    d = g.project("cp6-x01")
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")  # tests-written, red, implemented, behavior
    rnd = g.open_round(d, "CP1")
    p = g.write_review(d, rnd, g.R0(rnd))  # no findings -> a clean review approves the adversarial gate
    r = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP6-X01] setup: the adversarial gate passes cleanly", r.returncode == 0, r.stdout + r.stderr)

    run = d / ".ratchet/runs/f"
    plan = json.loads((run / "checkpoints.json").read_text())
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}
    plan["checkpoints"].append(cp2)
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))

    ask = g.rx(d, "ask", "approve-changes")
    check("[CP6-X01] setup: approve-changes asks about the new checkpoint", "CP2" in ask.stdout, ask.stdout)
    g.say(d, "yes, go ahead")
    r = g.rx(d, "approve-changes")
    check("[CP6-X01] approve-changes accepts the new checkpoint as the baseline", r.returncode == 0, r.stderr)

    r = g.rx(d, "complete", "CP1")
    check("[CP6-X01] completing the current checkpoint does not crash (no KeyError for the new checkpoint)",
          r.returncode == 0, r.stdout + r.stderr)

    r = g.rx(d, "next")
    check("[CP6-X01] next does not crash either", r.returncode == 0, r.stdout + r.stderr)

    r = g.rx(d, "status", "--json")
    check("[CP6-X01] status succeeds", r.returncode == 0, r.stdout + r.stderr)
    st = json.loads(r.stdout)
    ids = [c["id"] for c in st["checkpoints"]]
    check("[CP6-X01] the new checkpoint appears in status", "CP2" in ids, ids)
    check("[CP6-X01] the run moved on to building the new checkpoint", st.get("current") == "CP2", st)


TESTS = [
    ("CP6-X01", x01_approve_changes_builds_state_for_new_checkpoint),
]


def x02_approve_changes_refuses_checkpoint_missing_test_plan():
    print("[CP6-X02] approve-changes refuses a new checkpoint with a behavior gate but no test plan, "
          "the same validation plan-extend runs")
    d = g.project("cp6-x02")
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")  # tests-written, red, implemented, behavior
    rnd = g.open_round(d, "CP1")
    p = g.write_review(d, rnd, g.R0(rnd))  # no findings -> a clean review approves the adversarial gate
    r = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP6-X02] setup: the adversarial gate passes cleanly", r.returncode == 0, r.stdout + r.stderr)

    run = d / ".ratchet/runs/f"
    plan = json.loads((run / "checkpoints.json").read_text())
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}
    plan["checkpoints"].append(cp2)
    (run / "checkpoints.json").write_text(json.dumps(plan))
    # Deliberately no tests/CP2.json written: this new checkpoint has a behavior gate but no test plan,
    # the same shape plan-extend refuses with "Test plans missing for new checkpoints".

    ask = g.rx(d, "ask", "approve-changes")
    check("[CP6-X02] setup: approve-changes asks about the new checkpoint", "CP2" in ask.stdout, ask.stdout)
    g.say(d, "yes, go ahead")
    r = g.rx(d, "approve-changes")
    check("[CP6-X02] approve-changes refuses instead of registering the checkpoint",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    check("[CP6-X02] it names the missing test plan, like plan-extend's own refusal",
          "Test plans missing" in (r.stdout + r.stderr) and "CP2" in (r.stdout + r.stderr),
          r.stdout + r.stderr)

    r = g.rx(d, "status", "--json")
    check("[CP6-X02] status still works", r.returncode == 0, r.stdout + r.stderr)
    st = json.loads((run / "state.json").read_text())
    check("[CP6-X02] the new checkpoint was never registered in state (refused, not silently accepted)",
          "CP2" not in st["checkpoints"], st["checkpoints"].keys())


TESTS.append(("CP6-X02", x02_approve_changes_refuses_checkpoint_missing_test_plan))


def x03_approve_changes_refuses_checkpoint_missing_gates():
    print("[CP6-X03] approve-changes refuses a new checkpoint with no 'gates' field at all, "
          "with the plan-validation message, same as plan-extend's own refusal")
    d = g.project("cp6-x03")
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")  # tests-written, red, implemented, behavior
    rnd = g.open_round(d, "CP1")
    p = g.write_review(d, rnd, g.R0(rnd))  # no findings -> a clean review approves the adversarial gate
    r = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP6-X03] setup: the adversarial gate passes cleanly", r.returncode == 0, r.stdout + r.stderr)

    run = d / ".ratchet/runs/f"
    plan = json.loads((run / "checkpoints.json").read_text())
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"]}
    # Deliberately no "gates" field at all: a plan-validation error ("missing 'gates'"), not a missing-test-plan one.
    plan["checkpoints"].append(cp2)
    (run / "checkpoints.json").write_text(json.dumps(plan))

    ask = g.rx(d, "ask", "approve-changes")
    check("[CP6-X03] setup: approve-changes asks about the new checkpoint", "CP2" in ask.stdout, ask.stdout)
    g.say(d, "yes, go ahead")
    r = g.rx(d, "approve-changes")
    check("[CP6-X03] approve-changes refuses instead of registering the checkpoint",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    out = r.stdout + r.stderr
    check("[CP6-X03] it gives the plan-validation message naming the missing 'gates' field",
          "Plan invalid" in out and "missing 'gates'" in out, out)

    st = json.loads((run / "state.json").read_text())
    check("[CP6-X03] the new checkpoint was never registered in state (refused, not silently accepted)",
          "CP2" not in st["checkpoints"], st["checkpoints"].keys())


TESTS.append(("CP6-X03", x03_approve_changes_refuses_checkpoint_missing_gates))


def x04_checkpoint_missing_id_is_refused_not_a_crash():
    print("[CP6-X04] a new checkpoint with no 'id' field at all is refused with the plan-validation message by "
          "ask approve-changes, approve-changes and plan-extend, never a bare KeyError traceback, and status "
          "still works afterward")
    d = g.project("cp6-x04")
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")  # tests-written, red, implemented, behavior
    rnd = g.open_round(d, "CP1")
    p = g.write_review(d, rnd, g.R0(rnd))  # no findings -> a clean review approves the adversarial gate
    r = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP6-X04] setup: the adversarial gate passes cleanly", r.returncode == 0, r.stdout + r.stderr)

    run = d / ".ratchet/runs/f"
    plan = json.loads((run / "checkpoints.json").read_text())
    cp2 = {"title": "Step 2", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}
    # Deliberately no "id" field at all: a plausible copy/paste or templating slip, not an adversarial one.
    plan["checkpoints"].append(cp2)
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))

    ask = g.rx(d, "ask", "approve-changes")
    check("[CP6-X04] ask approve-changes does not crash for the id-less checkpoint either (same pattern as "
          "CP6-X01..X03: the mutating command below is what refuses)",
          ask.returncode == 0 and "Traceback" not in (ask.stdout + ask.stderr), (ask.returncode, ask.stdout, ask.stderr))
    g.say(d, "yes, go ahead")

    r = g.rx(d, "approve-changes")
    check("[CP6-X04] approve-changes refuses with the plan-validation message, not a traceback",
          r.returncode != 0 and "Plan invalid" in (r.stdout + r.stderr)
          and "missing 'id'" in (r.stdout + r.stderr)
          and "Traceback" not in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr))

    r = g.rx(d, "status", "--json")
    check("[CP6-X04] status still works after the malformed checkpoint was refused",
          r.returncode == 0 and "Traceback" not in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr))

    r = g.rx(d, "complete", "CP1")
    check("[CP6-X04] complete on the unrelated, already-approved current checkpoint does not crash either",
          r.returncode != 0 and "Traceback" not in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr))

    st = json.loads((run / "state.json").read_text())
    check("[CP6-X04] the malformed checkpoint was never registered in state",
          "CP2" not in st["checkpoints"], st["checkpoints"].keys())

    d2 = g.project("cp6-x04-extend")
    g.approve_plan(d2)
    g.to_adversarial(d2, "CP1")
    rnd2 = g.open_round(d2, "CP1")
    p2 = g.write_review(d2, rnd2, g.R0(rnd2))
    r = g.rx(d2, "adv-record", "CP1", "--reviews", p2)
    check("[CP6-X04] plan-extend setup: the adversarial gate passes cleanly", r.returncode == 0, r.stdout + r.stderr)
    r = g.rx(d2, "complete", "CP1")
    check("[CP6-X04] plan-extend setup: complete CP1", r.returncode == 0, r.stdout + r.stderr)
    r = g.rx(d2, "ask", "human-approve")
    check("[CP6-X04] plan-extend setup: ask human-approve", r.returncode == 0, r.stdout + r.stderr)
    g.say(d2, "approved")
    r = g.rx(d2, "human-approve")
    check("[CP6-X04] plan-extend setup: human-approve", r.returncode == 0, r.stdout + r.stderr)
    g.say(d2, "can we add a blue button")
    r = g.rx(d2, "feedback", "--text", "blue button")
    check("[CP6-X04] plan-extend setup: feedback", r.returncode == 0, r.stdout + r.stderr)

    run2 = d2 / ".ratchet/runs/f"
    plan2 = json.loads((run2 / "checkpoints.json").read_text())
    plan2["checkpoints"].append(dict(cp2))
    (run2 / "checkpoints.json").write_text(json.dumps(plan2))
    (run2 / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))

    r = g.rx(d2, "plan-extend")
    check("[CP6-X04] plan-extend refuses with the plan-validation message, not a traceback",
          r.returncode != 0 and "Plan invalid" in (r.stdout + r.stderr)
          and "missing 'id'" in (r.stdout + r.stderr)
          and "Traceback" not in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr))

    r = g.rx(d2, "status", "--json")
    check("[CP6-X04] status still works after plan-extend refused the malformed checkpoint",
          r.returncode == 0 and "Traceback" not in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr))


TESTS.append(("CP6-X04", x04_checkpoint_missing_id_is_refused_not_a_crash))


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
        print(f"\n{cid}: {fn.__name__}")
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


if __name__ == "__main__":
    main()
