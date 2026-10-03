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
if (fs.statSync(root).isDirectory()) walk(root); else if (fs.readFileSync(root, 'utf8').includes('FORBIDDEN')) bad.push(root);
if (bad.length) { console.error('guardrail: forbidden pattern in ' + bad.join(', ')); process.exit(1); }
"""

GUARD_TEST = """import t from 'node:test'; import a from 'node:assert'; import { spawnSync } from 'node:child_process';
const run = dir => spawnSync(process.execPath, ['guard/check.mjs', dir], { encoding: 'utf8' });
t('[{cp}-T01] the check catches the mistake', () => a.notEqual(run('tests/fixtures/bad').status, 0));
t('[{cp}-T02] the check passes on the current code', () => a.equal(run('src').status, 0));
"""


def put(d, cp, files):
    """Make the reviewed code really look like this (the reviewers' findings must be true: Ratchet later proves the
    check against these exact versions)."""
    for rel, text in files.items():
        (d / rel).write_text(text)
    r = rx(d, "behavior", cp)
    assert r.returncode == 0, r.stderr


OK_F = "export const f = () => 1;\n"
BAD_F = "export const f = () => 1; // FORBIDDEN: import db\n"
BAD_A = "// FORBIDDEN: import db\nexport const a = 1;\n"


def main():
    print("Spotting a repeat")
    d = g.project("guard", cps=2, extra_cp={"risk": "normal"}, config=["max_adversarial_rounds=20"])
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    put(d, "CP1", {"src/f.js": BAD_F})
    rnd = open_round(d, "CP1")
    r = record(d, "CP1", rnd, {0: DBF("src/f.js")})
    check("round 1: the finding blocks, no repeat yet", r.returncode == 2 and "REPEAT" not in r.stdout, r.stdout)
    put(d, "CP1", {"src/f.js": BAD_F.replace("1;", "1; ")})          # "fixed", but the mistake is still there
    rnd = open_round(d, "CP1")
    r = record(d, "CP1", rnd, {1: DBF("src/f.js")})
    check("the same unfixed finding raised again is not a repeat", "REPEAT" not in r.stdout, r.stdout)
    put(d, "CP1", {"src/f.js": OK_F, "src/a.js": BAD_A})
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
    put(d, "CP1", {"src/a.js": "export const a = 1;\n"})
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
    narrow = real.replace("fs.readFileSync(p, 'utf8').includes('FORBIDDEN')",
                          "p.endsWith('handler.js') && fs.readFileSync(p, 'utf8').includes('FORBIDDEN')")
    (d / "guard/check.mjs").write_text(narrow)                 # catches the fixture, misses the real code
    rx(d, "behavior", "CP3")
    record(d, "CP3", open_round(d, "CP3"))
    r = rx(d, "complete", "CP3")
    check("a check that only catches its own fixture is refused: Ratchet runs it on the real past code",
          r.returncode != 0 and "real code where reviewers found the mistake" in r.stderr, r.stderr)
    check("… and every run is kept as evidence",
          any((d / ".ratchet/runs/f/evidence/CP3/guardrail").glob("real-occurrence-*.log")))
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
    put(d, "CP2", {"src/g.js": "// forbidden: import db\nexport const g = () => 2;\n"})  # the check misses lower case
    rnd = open_round(d, "CP2")
    check("reviewers are told the kind is already covered",
          any("handler-imports-db" in x for x in rnd["review_rules"]), rnd["review_rules"])
    r = record(d, "CP2", rnd, {0: DBF("src/g.js")})
    check("a MAJOR finding of a covered kind still blocks (a label never switches a finding off)",
          r.returncode == 2, r.stdout + r.stderr)
    check("the check's miss is spotted and a step to strengthen it is added automatically, no question",
          "STRENGTHEN" in r.stdout and "CP4" in r.stdout, r.stdout)
    plan = json.loads((d / ".ratchet/runs/f/checkpoints.json").read_text())
    check("the strengthen step comes right after the current one",
          [c["id"] for c in plan["checkpoints"]] == ["CP1", "CP3", "CP2", "CP4"], [c["id"] for c in plan["checkpoints"]])
    cp4 = plan["checkpoints"][-1]
    check("it reuses the check's command and tests, and is marked as strengthening",
          cp4["guardrail"]["strengthen"] is True and cp4["guardrail"]["check_command"] == "node guard/check.mjs {target}"
          and cp4["tests"]["command"] == "node --test tests/guard.test.js", cp4)
    tp4 = json.loads((d / ".ratchet/runs/f/tests/CP4.json").read_text())
    check("its test plan adds the missed case", [c["id"] for c in tp4["cases"]] == ["CP4-T01", "CP4-T02", "CP4-T03"], tp4)
    put(d, "CP2", {"src/g.js": "export const g = () => 2; // reviewed\n"})
    rnd = open_round(d, "CP2")
    minor = [dict(DBF("src/g.js")[0], severity="minor")]
    r = rx(d, "adv-record", "CP2", "--reviews", g.write_review(d, rnd, rnd["reviewers"][0]["id"], findings=minor,
           files_reviewed=["src/g.js"]), g.write_review(d, rnd, rnd["reviewers"][1]["id"], files_reviewed=["src/g.js"]))
    check("a minor finding of a covered kind is noted, not fixed", r.returncode == 0 and "recorded for later" in r.stdout,
          r.stdout + r.stderr)
    check("… and the deferred list says why", "automatic check already covers" in rx(d, "deferred", "--json").stdout)

    print("Strengthening the check automatically")
    r = rx(d, "complete", "CP2")
    check("CP2 completes; the strengthen step is next", r.returncode == 0
          and json.loads(rx(d, "status", "--json").stdout)["current"] == "CP4", r.stdout + r.stderr)
    (d / "tests/fixtures/bad2").mkdir(parents=True)
    (d / "tests/fixtures/bad2/g.js").write_text("// forbidden: import db\n")
    with (d / "tests/guard.test.js").open("a") as fh:
        fh.write("t('[CP4-T01] still catches', () => a.notEqual(run('tests/fixtures/bad').status, 0));\n"
                 "t('[CP4-T02] still passes today', () => a.equal(run('src').status, 0));\n"
                 "t('[CP4-T03] catches the missed case', () => a.notEqual(run('tests/fixtures/bad2').status, 0));\n")
    check("tests written", rx(d, "tests-written", "CP4").returncode == 0)
    check("red: the missed case fails before the fix", rx(d, "red", "CP4").returncode == 0)
    (d / "guard/check.mjs").write_text(real.replace(".includes('FORBIDDEN')", ".toUpperCase().includes('FORBIDDEN')"))
    rx(d, "implemented", "CP4")
    r = rx(d, "behavior", "CP4")
    check("green", r.returncode == 0, r.stdout + r.stderr)
    record(d, "CP4", open_round(d, "CP4"))
    r = rx(d, "complete", "CP4")
    check("the strengthened check is proven on every real past case (including the one it missed) and completes",
          r.returncode == 0, r.stdout + r.stderr)
    cfg = json.loads((d / ".ratchet/config.json").read_text())
    ks = [c for c in cfg["checks"] if c["kind"] == "handler-imports-db"]
    check("it replaces the old check (one check per kind)", len(ks) == 1 and ks[0]["from"].endswith("CP4"), ks)
    first_cases = len(json.loads(g.git(d, "show", "HEAD~1:.ratchet/config.json").stdout)["checks"][0].get("cases") or [])
    check("the check keeps every past case it was proven on, so a later strengthening can't drop one",
          first_cases >= 2 and len(ks[0].get("cases") or []) >= first_cases + 1, (first_cases, ks[0].get("cases")))

    print("Checks can't be quietly removed")
    cfg = json.loads((d / ".ratchet/config.json").read_text())
    cfg["checks"] = []
    (d / ".ratchet/config.json").write_text(json.dumps(cfg))
    r = rx(d, "status", "--json")
    r2 = rx(d, "deferred")
    probs = __import__("importlib.util").util
    spec = probs.spec_from_file_location("eng_rm", g.ENGINE)
    m_ = probs.module_from_spec(spec)
    spec.loader.exec_module(m_)
    import os as _os
    _os.environ["RATCHET_HOST_PROVIDER"] = "anthropic"
    _os.environ["RATCHET_HOME"] = g.ENV["RATCHET_HOME"]
    ctx_ = m_.Ctx(d)
    check("removing a check after approval stops the run until the user confirms",
          any("checks" in p for p in m_.integrity_problems(ctx_, ctx_.state())), m_.integrity_problems(ctx_, ctx_.state()))

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
        put(d3, "CP1", {where: BAD_F if where.endswith("f.js") else BAD_A})
        rnd = open_round(d3, "CP1")
        record(d3, "CP1", rnd, {0: DBF(where)})
    put(d3, "CP1", {"src/f.js": OK_F, "src/a.js": "export const a = 1;\n"})
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

    print("High-risk kinds: disguised variants written without seeing the check")
    d5 = g.project("guard5", cps=2, extra_cp={"risk": "normal"}, config=["max_adversarial_rounds=20"])
    g.approve_plan(d5)
    g.to_adversarial(d5, "CP1")
    BLK = lambda where: [dict(DBF(where)[0], severity="blocker")]
    for where, text in (("src/f.js", BAD_F), ("src/a.js", BAD_A)):
        put(d5, "CP1", {where: text})
        record(d5, "CP1", open_round(d5, "CP1"), {0: BLK(where)})
    put(d5, "CP1", {"src/f.js": OK_F, "src/a.js": "export const a = 1;\n"})
    rx(d5, "ask", "choice", "--options", "A,B,C", "--guardrail", "A=handler-imports-db")
    g.say(d5, "A")
    rx(d5, "choice")
    check("guardrail added", rx(d5, "guardrail", "--kind", "handler-imports-db", *GARGS).returncode == 0)
    plan = json.loads((d5 / ".ratchet/runs/f/checkpoints.json").read_text())
    gcp = next(c for c in plan["checkpoints"] if c.get("guardrail"))
    check("a kind once rated a blocker makes a high-risk guardrail step", gcp["risk"] == "high"
          and gcp["guardrail"]["high_risk"] is True, gcp)
    record(d5, "CP1", open_round(d5, "CP1"))
    rx(d5, "complete", "CP1")
    nx = json.loads(rx(d5, "next").stdout)
    check("next says to have the variant writer go first, without the check",
          "ratchet-variant-writer" in nx["action"] and "never the check" in nx["action"], nx["action"])
    (d5 / "tests/fixtures/bad").mkdir(parents=True)
    (d5 / "tests/fixtures/bad/handler.js").write_text("// FORBIDDEN: import db\n")
    (d5 / "tests/guard.test.js").write_text(GUARD_TEST.replace("{cp}", "CP3"))
    r = rx(d5, "tests-written", "CP3")
    check("tests can't be marked written before the variants exist", r.returncode != 0 and "ratchet-variant-writer" in r.stderr,
          r.stderr)
    vd = d5 / "tests/fixtures/guardrails/handler-imports-db/variants"
    for i, text in enumerate(["// FORBIDDEN import db\n", "/* FORBIDDEN */ const x = 1;\n", "// forbidden: db\n"]):
        (vd / "bad").mkdir(parents=True, exist_ok=True)
        (vd / "bad" / f"v{i}.js").write_text(text)
    for i, text in enumerate(["// allowed: import logger\n", "const forbiddenWords = [];\n".replace("forbidden", "banned")]):
        (vd / "good").mkdir(parents=True, exist_ok=True)
        (vd / "good" / f"ok{i}.js").write_text(text)
    check("tests written once the variants exist", rx(d5, "tests-written", "CP3").returncode == 0)
    rx(d5, "red", "CP3")
    (d5 / "guard").mkdir()
    (d5 / "guard/check.mjs").write_text(CHECK_JS)                 # upper case only: misses the lower-case variant
    rx(d5, "implemented", "CP3")
    check("behavior passes", rx(d5, "behavior", "CP3").returncode == 0)
    rnd = open_round(d5, "CP3")
    check("a high-risk guardrail gets four reviewers", len(rnd["reviewers"]) == 4, rnd["reviewers"])
    record(d5, "CP3", rnd)
    r = rx(d5, "complete", "CP3")
    check("a check that misses a disguised variant can't complete, and Ratchet names the variant",
          r.returncode != 0 and "misses the disguised bad variant" in r.stderr and "v2.js" in r.stderr, r.stderr)

    print("Rebuilding past code safely, and only from findings that were acted on")
    import importlib.util as iu2, os as _os, subprocess as sp, tempfile as tf_
    spec = iu2.spec_from_file_location("eng_h", g.ENGINE)
    m = iu2.module_from_spec(spec)
    spec.loader.exec_module(m)
    _os.environ["RATCHET_HOST_PROVIDER"] = "anthropic"
    _os.environ["RATCHET_HOME"] = g.ENV["RATCHET_HOME"]
    repo = Path(tf_.mkdtemp(prefix="ratchet-mat-"))
    sp.run(["git", "init", "-q"], cwd=repo)
    (repo / "f.js").write_text("one\n")
    (repo / "abs").symlink_to("/etc/passwd")
    (repo / "up").symlink_to("../../outside")
    ctx = m.Ctx(repo)
    t1 = m.fingerprint(repo)["id"]
    out = m._materialize(ctx, t1, [])
    check("past code with links pointing outside is rebuilt without them (no crash, nothing written outside)",
          out is not None and (out / "f.js").exists() and not (out / "abs").exists() and not (out / "up").exists(), out)
    (repo / "g.js").write_text("other\n")
    t2 = m.fingerprint(repo)["id"]                       # f.js unchanged: the finding on it was rebutted
    (repo / "f.js").write_text("two\n")
    t3 = m.fingerprint(repo)["id"]                       # f.js changed: acted on
    hit = {"cp": "CP1", "round": 1, "file": "f.js", "tree": t1, "severity": "major"}
    st = {"finding_kinds": {"k": [hit]},
          "checkpoints": {"CP1": {"gates": {"adversarial": {"rounds": [{"fingerprint": t1}, {"fingerprint": t2}]}}}}}
    check("a finding whose file didn't change afterwards (disproved) is not used as proof",
          m.kind_occurrences(st, "k", ctx=ctx) == [])
    st["checkpoints"]["CP1"]["gates"]["adversarial"]["rounds"][1]["fingerprint"] = t3
    check("a finding that was fixed is used as proof", len(m.kind_occurrences(st, "k", ctx=ctx)) == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
