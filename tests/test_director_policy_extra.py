#!/usr/bin/env python3
"""
CP6 fix-round tests for the director policy.   python3 tests/test_director_policy_extra.py

The plain director-sign path signs under a covering policy and the lane's apply records the rule (and refuses a token the
policy no longer covers); approve-changes=test-files covers only add-only edits and never a skipped, removed, emptied or
swapped planned test or a deleted test file; a damaged policy file can be repaired with --set or --clear; clearing the last
rule is the same as no policy but keeps the dated history; --set and --clear exclude each other; the receipt names the
findings a rule covered; raise-limit max:N counts only raises the director signed.
Fixtures come from tests/test_director_policy.py (private RATCHET_HOME, real CLI as a subprocess).
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_director_policy as p             # noqa: E402
import test_director_sign_more as m          # noqa: E402
import test_gates as g                       # noqa: E402
import trust_helpers as th                   # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def e01_plain_path_signs_and_records():
    d, df = p.choice_lane("e01")
    p.reset()
    p.setrules(df, d, "choice=no-unlock")
    r = m.director(df, d, "director-sign", "choice", "A")
    tok = p.last_line(r)
    check("[E01] plain director-sign under a covering policy prints a token",
          r.returncode == 0 and p.TOKEN_LINE.match(tok) is not None, p.out(r))
    r2 = g.rx(d, "choice", "A", "--director-token", tok)
    ev = p.events(d, "choice_made")
    check("[E01] the lane's apply logs the rule on the same event",
          r2.returncode == 0 and len(ev) == 1 and ev[0].get("policy_rule") == "choice: no-unlock", p.out(r2))
    rc = g.rx(d, "receipt")
    check("[E01] and the receipt shows it", "choice: no-unlock" in rc.stdout, rc.stdout[-300:])
    # a token signed under a rule is refused at the apply once the policy no longer covers it
    d2, df2 = p.choice_lane("e01b")
    p.reset()
    p.setrules(df2, d2, "choice=no-unlock")
    tok2 = p.last_line(m.director(df2, d2, "director-sign", "choice", "A"))
    p.pol(df2, d2, "--clear", "choice")
    p.setrules(df2, d2, "raise-limit=max:1")
    before = p.raw(d2)
    r3 = g.rx(d2, "choice", "A", "--director-token", tok2)
    check("[E01] a token the policy no longer covers is refused at the apply, nothing changed",
          r3.returncode != 0 and p.raw(d2) == before and not p.events(d2, "choice_made"), p.out(r3))
    p.reset()


def red_lane(name, py=False):
    """An enrolled lane past red whose planned test file is JS (or Python when py)."""
    if py:
        d = g.project(name, extra_cp={"tests": {"files": ["tests/test_cp1.py"], "command": "python3 tests/test_cp1.py"}})
    else:
        d = g.project(name)
    g.approve_plan(d)
    if py:
        (Path(d) / "tests/test_cp1.py").write_text(
            "import sys\nimport unittest\n\nLIMIT = 2\n\n\ndef helper():\n    assert 1 == 1\n\n\n"
            "class T(unittest.TestCase):\n    def test_works(self):  # [CP1-T01]\n"
            "        helper()\n        self.assertEqual(1, LIMIT)\n\n\nif __name__ == '__main__':\n    unittest.main()\n")
        assert g.rx(d, "tests-written", "CP1").returncode == 0
        assert g.rx(d, "red", "CP1").returncode == 0
    else:
        p.da.to_red(d)
    return d, m.enroll(d)


def e02_weakened_planned_tests_are_refused():
    cases = {
        "js skip": lambda t: t.replace("t('[CP1-T01]", "t.skip('[CP1-T01]"),
        "js removed": lambda t: "\n".join(t.splitlines()[:1]) + "\n",
        "js emptied body": lambda t: t.replace("a.equal(f(), 1)", ""),
        "js swapped assertion": lambda t: t.replace("a.equal(f(), 1)", "a.ok(true)"),
    }
    for label, edit in cases.items():
        d, df = red_lane("e02" + label.split()[1])
        p.reset()
        p.setrules(df, d, "approve-changes=test-files")
        tf = Path(d) / "tests/cp1.test.js"
        tf.write_text(edit(tf.read_text()))
        before = p.raw(d)
        r = p.ds(df, d, "approve-changes")
        check(f"[E02] {label}: refused under approve-changes=test-files, ask the developer",
              r.returncode != 0 and p.ASK_DEV.search(p.out(r)) is not None and p.raw(d) == before, p.out(r))
    for n, line in enumerate(("// a new comment line above", "return;", "process.exit(0);", "t = () => {};")):
        d, df = red_lane(f"e02add{n}")
        p.reset()
        p.setrules(df, d, "approve-changes=test-files")
        tf = Path(d) / "tests/cp1.test.js"
        tf.write_text(line + "\n" + tf.read_text())
        before = p.raw(d)
        r = p.ds(df, d, "approve-changes")
        check(f"[E02] JS line {line!r} added above a planned test: refused, ask the developer, nothing changed",
              r.returncode != 0 and p.ASK_DEV.search(p.out(r)) is not None and p.raw(d) == before
              and not p.events(d, "changes_approved"), p.out(r))
    d, df = red_lane("e02del")
    p.reset()
    p.setrules(df, d, "approve-changes=test-files")
    (Path(d) / "tests/cp1.test.js").unlink()
    r = p.ds(df, d, "approve-changes")
    check("[E02] a deleted planned test file is refused",
          r.returncode != 0 and p.ASK_DEV.search(p.out(r)) is not None, p.out(r))
    d, df = red_lane("e02py", py=True)
    p.reset()
    p.setrules(df, d, "approve-changes=test-files")
    tf = Path(d) / "tests/test_cp1.py"
    tf.write_text(tf.read_text().replace("    def test_works", "    @unittest.skip('later')\n    def test_works"))
    r = p.ds(df, d, "approve-changes")
    check("[E02] a skipped planned Python test is refused",
          r.returncode != 0 and p.ASK_DEV.search(p.out(r)) is not None, p.out(r))
    for label, old, new in (("a helper's assert neutered", "    assert 1 == 1", "    pass"),
                            ("an import swapped", "import sys\n", "import os\n"),
                            ("a module-level constant a planned test uses", "LIMIT = 2", "LIMIT = 1")):
        d, df = red_lane("e02pyh", py=True)
        p.reset()
        p.setrules(df, d, "approve-changes=test-files")
        tf = Path(d) / "tests/test_cp1.py"
        tf.write_text(tf.read_text().replace(old, new, 1))
        before = p.raw(d)
        r = p.ds(df, d, "approve-changes")
        check(f"[E02] Python: {label} is refused, ask the developer, state unchanged",
              r.returncode != 0 and p.ASK_DEV.search(p.out(r)) is not None and p.raw(d) == before, p.out(r))
    p.reset()


def e03_damaged_policy_repair():
    d, df = p.da.building_lane("e03")
    p.reset()
    p.pfile().write_text("not json {{{")
    v = p.pol(df, d, "--set", "choice=no-unlock")
    data = p.policy_json(df, d) or {}
    check("[E03] --set rewrites a damaged file and logs the repair",
          v.returncode == 0 and data.get("rules") == {"choice": "no-unlock"}
          and any("repair" in json.dumps(c) for c in data.get("changes") or []), p.out(v))
    p.pfile().write_text("not json {{{")
    msg = p.pol(df, d)
    check("[E03] the damaged-file message names --set and --clear",
          "--set" in p.out(msg) and "--clear" in p.out(msg) and "director-policy.json" in p.out(msg), p.out(msg))
    c = p.pol(df, d, "--clear", "choice")
    data2 = p.policy_json(df, d) or {}
    check("[E03] --clear replaces a damaged file with an empty policy and logs it",
          c.returncode == 0 and data2.get("rules") == {}
          and any("repair" in json.dumps(x) for x in data2.get("changes") or []), p.out(c))
    p.reset()


def e04_clear_last_rule_fails_closed_and_remove_goes_back():
    d, df = p.choice_lane("e04")
    p.reset()
    p.setrules(df, d, "choice=no-unlock")
    c = p.pol(df, d, "--clear", "choice")
    v = p.pol(df, d)
    check("[E04] clearing the last rule: the view says a policy with no rules asks the developer",
          c.returncode == 0 and re.search(r"policy with no rules", p.out(v), re.I) is not None
          and re.search(r"asks the developer", p.out(v)) is not None, p.out(v))
    r = m.director(df, d, "director-sign", "choice", "A")
    check("[E04] routine signing is refused (nothing is covered), ask the developer",
          r.returncode != 0 and p.TOKEN_LINE.search(r.stdout) is None and p.ASK_DEV.search(p.out(r)) is not None, p.out(r))
    p.setrules(df, d, "accept-finding=max:minor")
    hist = (p.policy_json(df, d) or {}).get("changes") or []
    check("[E04] the dated history survives: set, clear, set", len(hist) == 3 and all(x.get("at") for x in hist), hist)
    p.pol(df, d, "--clear", "accept-finding")
    rm = p.pol(df, d, "--remove")
    v2 = p.pol(df, d)
    log = p.home() / "director-policy-removed.jsonl"
    check("[E04] --remove deletes the file, logs the removal, and the view says no policy is set",
          rm.returncode == 0 and not p.pfile().exists() and log.exists() and "\"at\"" in log.read_text()
          and re.search(r"no policy is set", p.out(v2), re.I) is not None, p.out(rm))
    r2 = m.director(df, d, "director-sign", "choice", "A")
    check("[E04] after --remove, routine signing works as with no policy",
          r2.returncode == 0 and p.TOKEN_LINE.search(r2.stdout) is not None, p.out(r2))
    again = p.pol(df, d, "--remove")
    check("[E04] --remove with no file is refused", again.returncode != 0, p.out(again))
    p.reset()


def e05_set_and_clear_exclusive():
    d, df = p.da.building_lane("e05")
    p.reset()
    r = p.pol(df, d, "--set", "choice=no-unlock", "--clear", "choice")
    check("[E05] --set with --clear is refused and writes nothing", r.returncode != 0 and not p.pfile().exists(), p.out(r))


def e06_receipt_names_findings():
    d, env, df = p.finding_lane("e06", [p.F_MAJ])
    p.reset(env)
    p.setrules(df, d, "accept-finding=max:major", env=env)
    r = p.ds(df, d, "accept-finding", "CP1", "--ids", "F1", "--reason", p.ACCEPT_REASON, env=env)
    rc = th.rx(d, env, "receipt")
    check("[E06] the receipt's policy line names the accepted finding",
          r.returncode == 0 and re.search(r"accept-finding CP1 \(.*F1.*\): accept-finding: max:major", rc.stdout) is not None,
          rc.stdout[-400:])
    p.reset(env)


def e07_human_raise_not_counted():
    d, r = p.tl._project_to_cap("e07", cps=2)
    ok = r.returncode != 0 and p.state(d).get("phase") == "blocked"
    if ok:
        hr = p.tl._raise(d, "CP1")  # the developer's own raise, by chat reply, before any director
        rnd = json.loads(p.tl._reviewers_after_triage(d, "CP1").stdout)
        path = g.write_review(d, rnd, g.R0(rnd), [])
        ok = (hr.returncode == 0 and g.rx(d, "adv-record", "CP1", "--reviews", path).returncode == 0
              and g.rx(d, "complete", "CP1").returncode == 0)
    if ok:
        p.tl._to_adversarial_own_source(d, "CP2", "g", "src/g.js")
        p.tl._drive_to_cap(d, "CP2")
        ok = p.state(d).get("phase") == "blocked" and p.state(d).get("current") == "CP2"
    check("[E07] setup: a chat raise on CP1, then CP2 blocked at its own limit", ok, p.state(d).get("phase"))
    if not ok:
        return
    df = m.enroll(d)
    p.reset()
    p.setrules(df, d, "raise-limit=max:1")
    r = p.ds(df, d, "raise-limit", "CP2", "--reason", p.REASON)
    ev = [e for e in p.events(d, "round_limit_raised") if e.get("by") == "director"]
    check("[E07] a raise the developer gave by chat does not use up raise-limit=max:1",
          r.returncode == 0 and len(ev) == 1 and ev[0].get("policy_rule") == "raise-limit: max:1 (1 of 1)", p.out(r))
    p.reset()


TESTS = [e01_plain_path_signs_and_records, e02_weakened_planned_tests_are_refused, e03_damaged_policy_repair,
         e04_clear_last_rule_fails_closed_and_remove_goes_back, e05_set_and_clear_exclusive, e06_receipt_names_findings,
         e07_human_raise_not_counted]


def main():
    global FAIL
    for fn in TESTS:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except Exception as e:
            FAIL += 1
            print(f"  ✗ CRASHED: {e}")
            traceback.print_exc()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
