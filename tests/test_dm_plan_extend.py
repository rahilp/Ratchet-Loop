#!/usr/bin/env python3
"""
Extra CP2 checks (adversarial round 1 fixes, not planned cases):   python3 tests/test_dm_plan_extend.py

`ratchet.py plan-extend` refuses, naming the line, a done-means line that feedback added after the plan was
approved and that no case in the plan proves; lines the approved plan already had (a run approved before this
rule) are never refused, so an older run keeps taking feedback as CP2-T15 shows. `plan-summary`'s heads-up
under an unlinked line is worded from the run's state: while planning it says plan-submit refuses the plan;
for a line added after approval it says plan-extend refuses the extension; only a line the approved plan had
gets the "approved before every line needed one" note.

`plan-extend` also applies plan-submit's "proves" shape and range check to the appended steps' cases only,
naming the case and the bad value in the same refusal; steps approved earlier are not judged again.

Reuses tests/test_dm_plan.py's fixtures and check(); the runner there only collects cp2_t* cases, so these
extra checks live here, tagged [CP2-X01], [CP2-X02], [CP2-X03] and [CP2-X04] (the reviewers `how`
objective is the rendering brief prints and the launch guard expects).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_gates as g        # noqa: E402
import test_dm_plan as dp     # noqa: E402
import test_brief_guard as bg  # noqa: E402  (task_guard, _force_phase_blocked)

check, _case, _cp, _out, _clean, _names_line, _state = (dp.check, dp._case, dp._cp, dp._out, dp._clean,
                                                       dp._names_line, dp._state)
LINE4 = "A user can log out from any page"


def _finish_cp1(d):
    """Drives CP1 of an approved run through the gates to the final approval, the moment feedback is taken."""
    g.to_adversarial(d, "CP1")
    rnd = g.open_round(d, "CP1")
    p = g.write_review(d, rnd, g.R0(rnd), [])
    assert g.rx(d, "adv-record", "CP1", "--reviews", p).returncode == 0
    assert g.rx(d, "complete", "CP1").returncode == 0
    g.rx(d, "ask", "human-approve")
    g.say(d, "approved")
    assert g.rx(d, "human-approve").returncode == 0


def _append_step(run, new_line, cp2_cases):
    plan = json.loads((run / "checkpoints.json").read_text())
    if new_line and new_line not in plan["objective"]["done_means"]:
        plan["objective"]["done_means"].append(new_line)
    plan["checkpoints"] = [c for c in plan["checkpoints"] if c["id"] != "CP2"] + [_cp(2)]
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests/CP2.json").write_text(json.dumps({"cases": cp2_cases}))


def cp2_x01_plan_extend_refuses_an_unlinked_line_feedback_added():
    """[CP2-X01] A run approved after this rule: feedback adds line 4 with no case proving it -> refused."""
    d, run, r = dp._submit("x01", [[_case("CP1-T01", [1, 2, 3])]])
    check("[CP2-X01] fixture: the fully linked plan submits", _clean(r), _out(r))
    g.approve_plan(d)
    _finish_cp1(d)
    g.say(d, "also add logout")
    rf = g.rx(d, "feedback", "--text", "also add logout")
    check("[CP2-X01] fixture: feedback is taken", _clean(rf), _out(rf))
    _append_step(run, LINE4, [_case("CP2-T01")])  # CP2 proves nothing
    rs = g.rx(d, "plan-summary")
    seg = rs.stdout[rs.stdout.find(LINE4):]
    check("[CP2-X01] before plan-extend, plan-summary's note under line 4 says it was added after approval and "
          "that plan-extend refuses the extension, not that the plan was approved before the rule",
          re.search(r"added after", seg) and "plan-extend" in seg and "approved before" not in seg, rs.stdout)
    re_ = g.rx(d, "plan-extend")
    msg = _out(re_)
    check("[CP2-X01] plan-extend is refused as 'Plan invalid' with no traceback",
          re_.returncode != 0 and "Plan invalid" in msg and "Traceback" not in msg, (re_.returncode, msg))
    check("[CP2-X01] the refusal names line 4 by number and by its text", _names_line(msg, 4, LINE4), msg)
    check("[CP2-X01] the refusal does not name the approved, linked lines 1-3",
          not any(dp._mentions_line_number(msg, i) for i in (1, 2, 3)), msg)
    st = _state(d)
    check("[CP2-X01] nothing was added: CP2 is not registered and the phase is still planning_feedback",
          "CP2" not in st.get("checkpoints", {}) and st.get("phase") == "planning_feedback",
          (st.get("phase"), list(st.get("checkpoints", {}))))
    _append_step(run, LINE4, [_case("CP2-T01", [4])])  # now CP2 proves line 4
    re2 = g.rx(d, "plan-extend")
    check("[CP2-X01] once a CP2 case proves line 4, plan-extend succeeds", _clean(re2), _out(re2))
    st = _state(d)
    check("[CP2-X01] CP2 is started (current CP2, phase building)",
          st.get("current") == "CP2" and st.get("phase") == "building", (st.get("current"), st.get("phase")))
    rs2 = g.rx(d, "plan-summary")
    check("[CP2-X01] plan-summary now shows line 4 proved by CP2-T01",
          "CP2-T01" in rs2.stdout[rs2.stdout.find(LINE4):], rs2.stdout)


def cp2_x02_legacy_run_lines_stay_unrefused_and_notes_follow_state():
    """[CP2-X02] Control: an older run's own unlinked lines never block feedback; only a line it adds does."""
    d, run = dp._legacy_project("x02", [[_case("CP1-T01", [1])]])  # lines 2 and 3 unlinked, approved before
    _finish_cp1(d)
    g.say(d, "also add logout")
    assert g.rx(d, "feedback", "--text", "also add logout").returncode == 0
    _append_step(run, None, [_case("CP2-T01")])  # no new line, CP2 proves nothing (CP2-T15's shape)
    re_ = g.rx(d, "plan-extend")
    check("[CP2-X02] a legacy run adding a step with no new line extends as before (exit 0)", _clean(re_),
          _out(re_))
    rs = g.rx(d, "plan-summary")
    seg = dp.rp._dm_segment(rs.stdout, dp.DM, 2)
    check("[CP2-X02] after the extension, the note under legacy line 2 says the plan was approved before the rule",
          "approved before every line needed one" in seg, rs.stdout)
    # The same older run shape, but feedback ADDS a line: refused for that line only.
    d2, run2 = dp._legacy_project("x02b", [[_case("CP1-T01", [1])]])
    _finish_cp1(d2)
    g.say(d2, "also add logout")
    assert g.rx(d2, "feedback", "--text", "also add logout").returncode == 0
    _append_step(run2, LINE4, [_case("CP2-T01")])
    re2 = g.rx(d2, "plan-extend")
    msg = _out(re2)
    check("[CP2-X02] a legacy run adding an unproved line 4 is refused naming line 4",
          re2.returncode != 0 and "Plan invalid" in msg and _names_line(msg, 4, LINE4), msg)
    check("[CP2-X02] the legacy, unlinked lines 2 and 3 are not named by that refusal",
          not dp._mentions_line_number(msg, 2) and not dp._mentions_line_number(msg, 3), msg)
    # Planning phase: the note says plan-submit refuses the plan, never that it was approved before the rule.
    d3, run3, r3 = dp._submit("x02c", [[_case("CP1-T01", [1])]])
    check("[CP2-X02] fixture: the unlinked plan is refused at plan-submit", r3.returncode != 0, _out(r3))
    rs3 = g.rx(d3, "plan-summary")
    check("[CP2-X02] while planning, plan-summary exits 0 and its note says plan-submit refuses the plan until a "
          "case proves the line, without claiming the plan was approved before the rule",
          _clean(rs3) and "plan-submit refuses" in rs3.stdout and "approved before" not in rs3.stdout, _out(rs3))


def cp2_x03_plan_extend_checks_proves_on_the_appended_steps_only():
    """[CP2-X03] plan-extend names a bad "proves" in an appended case; earlier approved steps are not re-judged."""
    # A run approved after the rule: feedback adds line 4, CP2-T01 says "proves": ["4"] (a string in the list).
    d, run, r = dp._submit("x03", [[_case("CP1-T01", [1, 2, 3])]])
    check("[CP2-X03] fixture: the fully linked plan submits", _clean(r), _out(r))
    g.approve_plan(d)
    _finish_cp1(d)
    g.say(d, "also add logout")
    assert g.rx(d, "feedback", "--text", "also add logout").returncode == 0
    _append_step(run, LINE4, [_case("CP2-T01", ["4"])])
    re_ = g.rx(d, "plan-extend")
    msg = _out(re_)
    check("[CP2-X03] a string \"4\" in an appended case's proves is refused as 'Plan invalid', no traceback",
          re_.returncode != 0 and "Plan invalid" in msg and "Traceback" not in msg, (re_.returncode, msg))
    check("[CP2-X03] the refusal names the case (CP2-T01) and the bad value, as plan-submit's wording does",
          "CP2-T01" in msg and "'4'" in msg and "proves" in msg, msg)
    check("[CP2-X03] the bad-proves message comes before the unproved-line message for line 4",
          msg.find("CP2-T01") < msg.find("done_means line 4"), msg)
    _append_step(run, LINE4, [_case("CP2-T01", [4])])
    re2 = g.rx(d, "plan-extend")
    check("[CP2-X03] with \"proves\": [4] the same extension succeeds", _clean(re2), _out(re2))
    # An older run, no new line (CP2-T15's shape): an out-of-range number in the appended case is refused.
    d2, run2 = dp._legacy_project("x03b", [[_case("CP1-T01", [1])]])
    _finish_cp1(d2)
    g.say(d2, "also add logout")
    assert g.rx(d2, "feedback", "--text", "also add logout").returncode == 0
    _append_step(run2, None, [_case("CP2-T01", [7])])
    re3 = g.rx(d2, "plan-extend")
    msg3 = _out(re3)
    check("[CP2-X03] an out-of-range proves 7 in an appended case is refused naming CP2-T01 and 7",
          re3.returncode != 0 and "Plan invalid" in msg3 and "CP2-T01" in msg3 and re.search(r"proves 7\b", msg3),
          msg3)
    check("[CP2-X03] that refusal does not name the older run's own unlinked lines 2 and 3",
          not dp._mentions_line_number(msg3, 2) and not dp._mentions_line_number(msg3, 3), msg3)
    # An older approved step whose own case carries an odd proves (never seen by plan-submit) is not re-judged.
    d3, run3 = dp._legacy_project("x03c", [[_case("CP1-T01", [1]), _case("CP1-T02", [9], layer="browser")]])
    _finish_cp1(d3)
    g.say(d3, "also add logout")
    assert g.rx(d3, "feedback", "--text", "also add logout").returncode == 0
    _append_step(run3, None, [_case("CP2-T01")])
    re4 = g.rx(d3, "plan-extend")
    check("[CP2-X03] an approved step's odd proves (CP1-T02: [9]) is not re-judged: plan-extend exits 0",
          _clean(re4) and "CP1-T02" not in _out(re4), _out(re4))
    st = _state(d3)
    check("[CP2-X03] CP2 is started on that older run (current CP2, phase building)",
          st.get("current") == "CP2" and st.get("phase") == "building", (st.get("current"), st.get("phase")))


def cp2_x04_reviewers_how_objective_passes_the_launch_guard():
    """[CP2-X04] The reviewers `how` objective is the rendering brief prints and the launch guard expects (with
    the Proved by / Heads up notes), so a prompt built from it passes _brief_guard on a linked plan."""
    d = g.project("dm-x04")   # a fully linked plan: plan-submit accepted it
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    hows = [r.get("how", "") for r in rnd["reviewers"] if r.get("vendor") == "host"]
    check("[CP2-X04] fixture: at least one host sub-agent reviewer with a `how` line", bool(hows), rnd["reviewers"])
    how = hows[0]
    m = re.match(r"Objective: (.*?) \| Spawn a ratchet-adversary sub-agent", how)
    check("[CP2-X04] the `how` line starts with the objective and then the spawn instruction", bool(m), how)
    obj = m.group(1) if m else ""
    check("[CP2-X04] the `how` objective carries the Proved by notes brief prints", "Proved by:" in obj, obj)
    brief = g.rx(d, "brief", "CP1", "--role", "reviewer").stdout
    check("[CP2-X04] the `how` objective equals brief's objective, whitespace collapsed",
          re.sub(r"\s+", " ", obj).strip() in re.sub(r"\s+", " ", brief), (obj, brief))
    # the guard only checks launches while the run is building or blocked; the round is open, so force 'blocked'
    bg._force_phase_blocked(d)
    refused = bg.task_guard(d, "ratchet-adversary", "Review checkpoint CP1 for correctness.")
    check("[CP2-X04] teeth: a prompt without the objective is refused while blocked",
          "RATCHET GUARD" in refused.stdout + refused.stderr, refused.stdout + refused.stderr)
    ok = bg.task_guard(d, "ratchet-adversary", obj + "\nReview checkpoint CP1 for correctness.")
    check("[CP2-X04] a prompt that starts with the `how` objective passes the launch guard",
          "RATCHET GUARD" not in ok.stdout + ok.stderr and ok.returncode == 0, ok.stdout + ok.stderr)


def main():
    tests = sorted((name, fn) for name, fn in globals().items() if callable(fn) and name.startswith("cp2_x"))
    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except Exception as e:
            dp.FAIL += 1
            print(f"  ✗ CRASHED: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{dp.PASS} passed, {dp.FAIL} failed")
    sys.exit(1 if dp.FAIL else 0)


if __name__ == "__main__":
    main()
