#!/usr/bin/env python3
"""
A mistake that keeps coming back becomes an automatic check:   python3 tests/test_guardrails.py

Reviewers label findings with a `kind`; a kind seen twice is offered as a guardrail straight away; after the user
picks it, a guardrail step is inserted right after the current one, proves its check red/green through every gate,
and its check then runs with the tests at every later behavior gate (and is kept in the project's config).
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gates as g  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


rx = g.rx


def open_round(d, cp):
    r = rx(d, "reviewers", cp, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def record(d, cp, rnd, findings_by_reviewer=None):
    paths = []
    for i, rv in enumerate(rnd["reviewers"]):
        f = (findings_by_reviewer or {}).get(i)
        paths.append(g.write_review(d, rnd, rv["id"], findings=f, files_reviewed=["src/f.js", "src/g.js", "guard/check.mjs", "tests/guard.test.js", "tests/cp2.test.js"]))
    return rx(d, "adv-record", cp, "--reviews", *paths)


DBF = lambda where: [{"id": "D1", "severity": "major", "kind": "Handler imports DB", "file": where,
                      "rule": "standards.md §2", "description": "handler reaches into the database layer"}]

CHECK_JS = """import fs from 'node:fs'; import path from 'node:path';
// guardrail: no source file may contain the word FORBIDDEN
const root = process.argv[2] || 'src';
let bad = [];
const walk = d => { for (const n of fs.readdirSync(d)) { const p = path.join(d, n);
  if (fs.statSync(p).isDirectory()) walk(p); else if (fs.readFileSync(p, 'utf8').includes('FORBIDDEN')) bad.push(p); } };
walk(root);
if (bad.length) { console.error('guardrail: forbidden pattern in ' + bad.join(', ')); process.exit(1); }
"""

GUARD_TEST = """import t from 'node:test'; import a from 'node:assert'; import { spawnSync } from 'node:child_process';
const run = dir => spawnSync(process.execPath, ['guard/check.mjs', dir], { encoding: 'utf8' });
t('[{cp}-T01] the check catches the mistake', () => a.notEqual(run('tests/fixtures/bad').status, 0));
t('[{cp}-T02] the check passes on the current code', () => a.equal(run('src').status, 0));
"""


def main():
    print("Spotting a repeat")
    d = g.project("guard", cps=2, extra_cp={"risk": "normal"}, config=["max_adversarial_rounds=20"])
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    rnd = open_round(d, "CP1")
    r = record(d, "CP1", rnd, {0: DBF("src/f.js")})
    check("round 1: the finding blocks, no repeat yet", r.returncode == 2 and "REPEAT" not in r.stdout, r.stdout)
    (d / "src/f.js").write_text("export const f = () => 1; // fixed\n")
    rx(d, "behavior", "CP1")
    rnd = open_round(d, "CP1")
    r = record(d, "CP1", rnd, {1: DBF("src/f.js")})
    check("the same unfixed finding raised again is not a repeat", "REPEAT" not in r.stdout, r.stdout)
    (d / "src/f.js").write_text("export const f = () => 1; // fixed twice\n")
    rx(d, "behavior", "CP1")
    rnd = open_round(d, "CP1")
    r = record(d, "CP1", rnd, {1: DBF("src/a.js")})
    check("the same kind in another place is announced as a repeat", "REPEAT" in r.stdout and "handler-imports-db" in r.stdout,
          r.stdout)
    nx = json.loads(rx(d, "next").stdout)
    check("next says to offer the guardrail first", nx["action"].startswith("First offer the user a guardrail")
          and nx["guardrail_candidates"][0]["kind"] == "handler-imports-db", nx)
    reps = json.loads(rx(d, "repeats", "--json").stdout)
    check("`repeats` lists it", [x["kind"] for x in reps] == ["handler-imports-db"], reps)

    print("Offering it, and the user's pick")
    GARGS = ["--title", "Handlers never touch the DB", "--why", "Request handlers must not import the database layer",
             "--check-command", "node guard/check.mjs {target}", "--target", "src", "--bad-example", "tests/fixtures/bad",
             "--test-command", "node --test tests/guard.test.js", "--test-file", "tests/guard.test.js"]
    r = rx(d, "guardrail", "--kind", "handler-imports-db", *GARGS)
    check("a guardrail can't be added before the user picks it", r.returncode != 0 and "ask choice" in r.stderr, r.stderr)
    r = rx(d, "guardrail", "--kind", "security", *GARGS)
    check("a kind that hasn't repeated can't get a guardrail (no minting your own)",
          r.returncode != 0 and "isn't a repeated mistake" in r.stderr, r.stderr)
    q = rx(d, "ask", "choice", "--options", "A,B,C", "--guardrail", "A=Handler Imports DB")
    check("the question tells the agent what option A does", "adds an automatic check" in q.stdout, q.stdout)
    check("once asked, it is no longer a pending candidate", json.loads(rx(d, "repeats", "--json").stdout) == [])
    g.say(d, "A")
    r = rx(d, "choice")
    check("the pick of A is recorded as a guardrail", r.returncode == 0 and "guardrail" in r.stdout, r.stdout + r.stderr)
    r = rx(d, "guardrail", "--kind", "handler-imports-db", *[x if x != "node guard/check.mjs {target}" else "node guard/check.mjs src" for x in GARGS])
    check("the check command must say where the code to check goes ({target})", r.returncode != 0 and "{target}" in r.stderr, r.stderr)
    r = rx(d, "guardrail", "--kind", "handler-imports-db", *GARGS)
    check("the guardrail step is added (the kind label typed differently still matches)",
          r.returncode == 0 and "CP3" in r.stdout, r.stdout + r.stderr)
    plan = json.loads((d / ".ratchet/runs/f/checkpoints.json").read_text())
    check("it is placed right after the current step", [c["id"] for c in plan["checkpoints"]] == ["CP1", "CP3", "CP2"],
          [c["id"] for c in plan["checkpoints"]])
    tp = json.loads((d / ".ratchet/runs/f/tests/CP3.json").read_text())
    check("it comes with a red/green test plan", [c["id"] for c in tp["cases"]] == ["CP3-T01", "CP3-T02"], tp)
    r = rx(d, "guardrail", "--kind", "handler-imports-db", *GARGS)
    check("the pick is used once", r.returncode != 0, r.stdout)
    r = rx(d, "tests-written", "CP1")
    st = json.loads(rx(d, "status", "--json").stdout)
    check("adding it doesn't trip the approval lock", st["phase"] == "building" and "changed after" not in r.stderr, r.stderr)

    print("Finishing the current step, then building the guardrail next")
    rnd = open_round(d, "CP1")
    r = record(d, "CP1", rnd)
    check("CP1's review approves", r.returncode == 0, r.stdout + r.stderr)
    r = rx(d, "complete", "CP1")
    check("CP1 completes and the guardrail step is next", r.returncode == 0 and
          json.loads(rx(d, "status", "--json").stdout)["current"] == "CP3", r.stdout + r.stderr)
    (d / "tests/fixtures/bad").mkdir(parents=True)
    (d / "tests/fixtures/bad/handler.js").write_text("// FORBIDDEN: import db\n")
    (d / "tests/guard.test.js").write_text(GUARD_TEST.replace("{cp}", "CP3"))
    check("tests written", rx(d, "tests-written", "CP3").returncode == 0)
    check("red: the tests fail before the check exists", rx(d, "red", "CP3").returncode == 0)
    (d / "guard").mkdir()
    (d / "guard/check.mjs").write_text(CHECK_JS)
    check("implemented", rx(d, "implemented", "CP3").returncode == 0)
    r = rx(d, "behavior", "CP3")
    check("green: the check catches the bad example and passes on today's code", r.returncode == 0, r.stdout + r.stderr)
    rnd = open_round(d, "CP3")
    check("the guardrail step is reviewed like any other", len(rnd["reviewers"]) == 2, rnd["reviewers"])
    check("CP3's review approves", record(d, "CP3", rnd).returncode == 0)
    real = (d / "guard/check.mjs").read_text()
    (d / "guard/check.mjs").write_text("process.exit(0)\n")      # a check that can't fail
    r = rx(d, "complete", "CP3")
    check("a check that passes on the bad example can't complete (it guards nothing)",
          r.returncode != 0 and ("doesn't catch the mistake" in r.stderr or "behavior" in r.stderr), r.stderr)
    (d / "guard/check.mjs").write_text(real)
    for gate in ("behavior",):
        rx(d, gate, "CP3")
    rnd = open_round(d, "CP3")
    record(d, "CP3", rnd)
    r = rx(d, "complete", "CP3")
    check("the guardrail step completes once Ratchet itself sees the check fail on the bad example and pass today",
          r.returncode == 0, r.stdout + r.stderr)
    cfg = json.loads((d / ".ratchet/config.json").read_text())
    check("its check is now part of the project's config",
          any(c["kind"] == "handler-imports-db" and c["command"] == "node guard/check.mjs src" for c in cfg.get("checks", [])),
          cfg.get("checks"))
    learn = (d / ".ratchet/learnings.md").read_text()
    check("learnings note the rule as enforced", "enforced by `node guard/check.mjs src`" in learn, learn[-300:])
    files = g.git(d, "show", "--name-only", "--format=", "HEAD").stdout.split()
    check("the check, its tests and the config entry are committed together",
          "guard/check.mjs" in files and ".ratchet/config.json" in files, files)

    print("The check guards every later step")
    (d / "tests/cp2.test.js").write_text("import t from 'node:test'; import a from 'node:assert'; "
                                          "import { g as h } from '../src/g.js';\nt('[CP2-T01] works', () => a.equal(h(), 2));\n")
    rx(d, "tests-written", "CP2")
    rx(d, "red", "CP2")
    (d / "src/g.js").write_text("// FORBIDDEN: import db\nexport const g = () => 2;\n")
    rx(d, "implemented", "CP2")
    r = rx(d, "behavior", "CP2")
    check("the mistake now fails the behavior gate in seconds, naming the check",
          r.returncode == 2 and "automatic check 'handler-imports-db'" in r.stderr, r.stdout + r.stderr)
    (d / "src/g.js").write_text("export const g = () => 2;\n")
    r = rx(d, "behavior", "CP2")
    check("fixed code passes the tests and the check", r.returncode == 0, r.stdout + r.stderr)
    rnd = open_round(d, "CP2")
    check("reviewers are told the kind is already covered",
          any("handler-imports-db" in x for x in rnd["review_rules"]), rnd["review_rules"])
    r = record(d, "CP2", rnd, {0: DBF("src/g.js")})
    check("a MAJOR finding of a covered kind still blocks (a label never switches a finding off)",
          r.returncode == 2, r.stdout + r.stderr)
    (d / "src/g.js").write_text("export const g = () => 2; // reviewed\n")
    rx(d, "behavior", "CP2")
    rnd = open_round(d, "CP2")
    minor = [dict(DBF("src/g.js")[0], severity="minor")]
    r = rx(d, "adv-record", "CP2", "--reviews", g.write_review(d, rnd, rnd["reviewers"][0]["id"], findings=minor,
           files_reviewed=["src/g.js"]), g.write_review(d, rnd, rnd["reviewers"][1]["id"], files_reviewed=["src/g.js"]))
    check("a minor finding of a covered kind is noted, not fixed", r.returncode == 0 and "recorded for later" in r.stdout,
          r.stdout + r.stderr)
    check("… and the deferred list says why", "automatic check already covers" in rx(d, "deferred", "--json").stdout)

    print("Checks can't be quietly removed")
    cfg = json.loads((d / ".ratchet/config.json").read_text())
    cfg["checks"] = []
    (d / ".ratchet/config.json").write_text(json.dumps(cfg))
    r = rx(d, "complete", "CP2")
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    r2 = rx(d, "reviewers", "CP2")
    check("removing a check after approval stops the run until the user confirms",
          "checks" in (r.stderr + r2.stderr), (r.stderr[-300:], r2.stderr[-300:]))

    print("Kinds come only from the reviewer's own label")
    import importlib.util as iu
    spec = iu.spec_from_file_location("eng_g", g.ENGINE)
    m = iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    check("a cited rule alone is not a kind (too broad to check)", m.finding_kind({"rule": "standards.md §3"}) is None)
    check("an explicit kind is normalised", m.finding_kind({"kind": "Unvalidated Input!"}) == "unvalidated-input")

    print("A guardrail can't replace an existing check")
    d2 = g.project("guard2", cps=2, extra_cp={"risk": "normal"}, config=["max_adversarial_rounds=20",
                   'checks=[{"kind":"sqli","command":"node -e 0"}]'])
    g.approve_plan(d2)
    g.to_adversarial(d2, "CP1")
    for where in ("src/f.js", "src/a.js"):
        rnd = open_round(d2, "CP1")
        record(d2, "CP1", rnd, {0: [{"id": "S1", "severity": "major", "kind": "sqli", "file": where, "description": "x"}]})
        (d2 / "src/f.js").write_text(f"export const f = () => 1; // {where}\n")
        rx(d2, "behavior", "CP1")
    check("a kind that already has a check is not offered again", json.loads(rx(d2, "repeats", "--json").stdout) == [])
    r = rx(d2, "guardrail", "--kind", "sqli", *GARGS)
    check("… and a guardrail for it is refused", r.returncode != 0 and "already exists" in r.stderr, r.stderr)

    print("A check that can't fail is refused, even when its tests were gamed")
    d3 = g.project("guard3", cps=2, extra_cp={"risk": "normal"}, config=["max_adversarial_rounds=20"])
    g.approve_plan(d3)
    g.to_adversarial(d3, "CP1")
    for where in ("src/f.js", "src/a.js"):
        rnd = open_round(d3, "CP1")
        record(d3, "CP1", rnd, {0: DBF(where)})
        (d3 / "src/f.js").write_text(f"export const f = () => 1; // {where}\n")
        rx(d3, "behavior", "CP1")
    rx(d3, "ask", "choice", "--options", "A,B,C", "--guardrail", "A=handler-imports-db")
    g.say(d3, "A")
    rx(d3, "choice")
    check("guardrail added", rx(d3, "guardrail", "--kind", "handler-imports-db", *GARGS).returncode == 0)
    rnd = open_round(d3, "CP1")
    record(d3, "CP1", rnd)
    rx(d3, "complete", "CP1")
    (d3 / "tests/fixtures/bad").mkdir(parents=True)
    (d3 / "tests/fixtures/bad/handler.js").write_text("// FORBIDDEN\n")
    (d3 / "tests/guard.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import fs from 'node:fs';\n"
        "t('[CP3-T01] gamed', () => a.ok(fs.existsSync('guard/check.mjs')));\n"
        "t('[CP3-T02] gamed', () => a.ok(fs.existsSync('guard/check.mjs')));\n")
    rx(d3, "tests-written", "CP3"); rx(d3, "red", "CP3")
    (d3 / "guard").mkdir()
    (d3 / "guard/check.mjs").write_text("process.exit(0)\n")
    rx(d3, "implemented", "CP3")
    check("the gamed tests pass the behavior gate", rx(d3, "behavior", "CP3").returncode == 0)
    rnd = open_round(d3, "CP3")
    record(d3, "CP3", rnd)
    r = rx(d3, "complete", "CP3")
    check("… but completing runs the real check on the bad example and refuses a check that can't fail",
          r.returncode != 0 and "doesn't catch the mistake" in r.stderr, r.stderr)
    cfg = json.loads((d3 / ".ratchet/config.json").read_text())
    check("… and nothing was added to the project's checks", not cfg.get("checks"), cfg.get("checks"))

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
