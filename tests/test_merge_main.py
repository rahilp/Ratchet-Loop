#!/usr/bin/env python3
"""
CP5 tests: accept a merge of main between steps.   python3 tests/test_merge_main.py

After a step is done, an agent can merge main into the lane (real `git merge --no-ff`) and run one new Ratchet
command, `ratchet.py merged`. Ratchet then re-checks the earlier steps on the merged code, makes what the merge
brought in the new baseline (so the next step's reviewers and commit see only that step's own changes), accepts
rule, standards and design-file changes exactly as main has them (and names them), and asks for approval for
anything else. These tests drive the repo's own ratchet.py through real commands in throwaway git projects (a
2-step plan, step 1 completed, a main branch with a new commit, a real merge), reusing tests/test_gates.py's
helpers by import. state.json is only ever read.

Project 'shop': src/a.js and .ratchet/standards.md ("Keep functions short") are in the first commit. The lane
takes CP1 'Price helper' (tests/cp1.test.js, `node --test`) through every gate and completes it. Main is then
forked at that commit and gets its own commit M1 (src/m.js plus, by variant, a standards line, a config rule or a
change to src/f.js that breaks CP1). The test merges main into the lane with git and runs `merged`.

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import test_gates as tg  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-[TX]\d+[a-z]?)\]")
AUX = Path(_tmp.mkdtemp(prefix="ratchet-merge-aux-"))   # files outside every project: markers, scripts
REFUSAL = "These changed after the user approved the plan"
STD_LINE = "No magic numbers"


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


class Stop(Exception):
    """A case cannot go on after a failed check that was already recorded."""


def need(name, cond, detail=""):
    check(name, cond, detail)
    if not cond:
        raise Stop(name)


# ------------------------------------------------------------------------------------------------ helpers

PATH_RE = re.compile(r"file://\S*|(?<![\w.\-/])/[^\s'\"),;>]+")


def said(r) -> str:
    """Everything a command printed, with absolute paths cut out (the suite's temp folders are named after the
    suite, so a word match must never run over a path)."""
    return PATH_RE.sub("<path>", (r.stdout or "") + "\n" + (r.stderr or ""))


def known(r) -> bool:
    """The command exists: the failure is the engine's own message, not an argparse usage error."""
    t = said(r)
    return "invalid choice" not in t and "usage:" not in t


def ours(r) -> str:
    """What a command said, only when the command exists: an argparse usage error lists every command name, so
    its words must never count as the engine's message."""
    return said(r) if known(r) else ""


def rx(d, *a):
    return tg.rx(d, *a)


def git(d, *a):
    return tg.git(d, *a)


def sj(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def history(d, event):
    return [e for e in sj(d).get("history", []) if e.get("event") == event]


def human_log_len(d):
    p = Path(d) / ".ratchet/human_log.jsonl"
    return len([ln for ln in p.read_text().splitlines() if ln.strip()]) if p.exists() else 0


def snap(d):
    """The parts of state.json a refused or a no-op command must leave alone."""
    st = sj(d)
    return {"phase": st.get("phase"), "current": st.get("current"), "approved": st.get("approved"),
            "base_tree": st.get("base_tree"), "pending": st.get("pending"),
            "checkpoints": {k: {f: v.get(f) for f in ("status", "step", "base_tree", "commit")}
                            for k, v in (st.get("checkpoints") or {}).items()},
            "merged": [e for e in st.get("history", []) if e.get("event") == "merged_main"]}


def fp(d):
    return json.loads(rx(d, "fingerprint").stdout)["id"]


def changed_since(d, base):
    r = git(d, "diff-tree", "-r", "--no-renames", "--name-only", base, fp(d))
    return sorted(ln for ln in r.stdout.splitlines() if ln)


# ------------------------------------------------------------------------------------------------ fixtures

CP1_TEST = ("import t from 'node:test'; import a from 'node:assert'; import fs from 'node:fs';\n"
            "import { f } from '../src/f.js';\n"
            "fs.appendFileSync({marker!r}, 'cp1 test ran\\n');\n"
            "t('[CP1-T01] price helper works', () => a.equal(f(), 1));\n")
CP2_TEST_JS = ("import t from 'node:test'; import a from 'node:assert';\n"
               "import { total } from '../src/cart.js';\n"
               "t('[CP2-T01] cart total adds lines', () => a.equal(total([1, 2]), 3));\n")
CART_JS = "export const total = (xs) => xs.reduce((s, x) => s + x, 0);\n"
PY_RUNNER = '''import sys
import traceback

sys.path.insert(0, ".")
ns = {"__name__": "cart_tests"}
exec(compile(open(sys.argv[1]).read(), sys.argv[1], "exec"), ns)
bad = 0
for name, fn in list(ns.items()):
    if name.startswith("test_") and callable(fn):
        try:
            fn()
        except BaseException:
            bad += 1
            print("FAILED", name)
            traceback.print_exc()
sys.exit(1 if bad else 0)
'''
MAIN_PY_TEST = "def test_from_main():\n    assert 1 + 1 == 2\n"
PY_CP2_TEST = '''

def test_cp2_t01():
    """[CP2-T01] a cart totals its lines"""
    from src import cart
    assert cart.total([1, 2]) == 3
'''
CART_PY = "def total(xs):\n    return sum(xs)\n"


def commit_all(d, msg):
    git(d, "add", "-A")
    r = git(d, "commit", "-qm", msg)
    assert r.returncode == 0, r.stderr + r.stdout


def build(name, human_review="end", regression=None, cp2="js"):
    """The lane at the step boundary: CP1 'Price helper' completed. regression is None, 'pass_if_merged' (exits 0
    only when src/m.js exists once the test has raised the flag) or 'fail_after_merge' (exits 1 once the flag is
    up). With human_review 'each' the run waits for the user's review of CP1."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-mm-{name}-"))
    marker, marker2, flag = AUX / f"{d.name}.regression", AUX / f"{d.name}.cp1runs", AUX / f"{d.name}.flag"
    for c in (["init", "-q", "-b", "lane"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / ".ratchet").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    (d / ".ratchet/standards.md").write_text("Keep functions short\n")
    (d / "tests/py_runner.py").write_text(PY_RUNNER)
    commit_all(d, "init")
    tg.say(d, "please build the feature")
    args = ["init", "feature", "--slug", "f"] + (["--human-review", "each"] if human_review == "each" else [])
    r = rx(d, *args)
    assert r.returncode == 0, r.stderr + r.stdout
    cfg = ["min_review_seconds=0", "providers=[]"]
    if regression:
        script = AUX / f"{d.name}-reg.sh"
        body = {"pass_if_merged": f'echo ran >> {marker}\nif [ -e {flag} ]; then test -f src/m.js; fi\n',
                "fail_after_merge": f'echo ran >> {marker}\nif [ -e {flag} ]; then echo MERGED-RUN-MARKER; exit 1; fi\n'}[regression]
        script.write_text(body)
        cfg.append(f"regression_command=sh {script}")
    r = rx(d, "config", "--set", *cfg)
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    if cp2 == "py":
        cp2_tests = {"files": ["tests/test_cart.py"], "command": "python3 tests/py_runner.py tests/test_cart.py"}
    else:
        cp2_tests = {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}
    cps = [
        {"id": "CP1", "title": "Price helper", "summary": "s", "done_when": ["works"],
         "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
         "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}},
        {"id": "CP2", "title": "Cart total", "summary": "s", "done_when": ["a cart adds its lines"],
         "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low", "tests": cp2_tests}]
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": cps}))
    for cid in ("CP1", "CP2"):
        (run / "tests" / f"{cid}.json").write_text(json.dumps({"cases": [{"id": f"{cid}-T01", "layer": "code", "proves": [1]}]}))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    tg.approve_plan(d)
    (d / "tests/cp1.test.js").write_text(CP1_TEST.replace("{marker!r}", repr(str(marker2))))
    for step in ("tests-written", "red"):
        r = rx(d, step, "CP1")
        assert r.returncode == 0, step + (r.stderr + r.stdout)[-400:]
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    for step in ("implemented", "behavior"):
        r = rx(d, step, "CP1")
        assert r.returncode == 0, step + (r.stderr + r.stdout)[-400:]
    rnd = tg.open_round(d, "CP1")
    r = rx(d, "adv-record", "CP1", "--reviews", tg.write_review(d, rnd, tg.R0(rnd)))
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    assert not git(d, "status", "--porcelain").stdout.strip(), git(d, "status", "--porcelain").stdout
    want = "awaiting_checkpoint_review" if human_review == "each" else "building"
    assert sj(d)["phase"] == want, sj(d)["phase"]
    AUXES[str(d)] = {"marker": marker, "marker2": marker2, "flag": flag}
    return d


AUXES: dict[str, dict] = {}


def lines(path):
    p = Path(path)
    return len(p.read_text().splitlines()) if p.exists() else 0


def make_main(d, std=False, config=None, brk=False, code=True, py_test=False):
    """Fork main from the lane's commit (the one made when step 1 completed) and give it M1."""
    git(d, "checkout", "-q", "-b", "main")
    if code:
        (d / "src/m.js").write_text("export const tax = () => 0.2;\n")
    if std:
        with (d / ".ratchet/standards.md").open("a") as f:
            f.write(STD_LINE + "\n")
    if config:
        p = d / ".ratchet/config.json"
        c = json.loads(p.read_text())
        c.update(config)
        p.write_text(json.dumps(c, indent=2) + "\n")
    if brk:
        (d / "src/f.js").write_text("export const f = () => 2;\n")
    if py_test:
        (d / "tests/test_cart.py").write_text(MAIN_PY_TEST)
    commit_all(d, "M1 from main")
    r = git(d, "checkout", "-q", "lane")
    assert r.returncode == 0, r.stderr


def merge_main(d):
    r = git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    assert r.returncode == 0, r.stderr + r.stdout
    assert len(git(d, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) == 3   # a real merge commit


def raise_flag(d):
    AUXES[str(d)]["flag"].write_text("merged\n")


def cp2_tests_written(d, *, to_complete=False):
    """The step's own work: its tests, then (optionally) all the way through the gates to complete."""
    (d / "tests/cp2.test.js").write_text(CP2_TEST_JS)
    r = rx(d, "tests-written", "CP2")
    if not to_complete:
        return r
    need("(setup) tests-written CP2 accepted", r.returncode == 0, said(r)[-300:])
    r = rx(d, "red", "CP2")
    need("(setup) red CP2", r.returncode == 0, said(r)[-300:])
    (d / "src/cart.js").write_text(CART_JS)
    for step in ("implemented", "behavior"):
        r = rx(d, step, "CP2")
        need(f"(setup) {step} CP2", r.returncode == 0, said(r)[-300:])
    return None


def merged_ok(d, case):
    """Runs `merged` and records that it was accepted (exit 0). Stops the case when it was not."""
    r = rx(d, "merged")
    need(f"[{case}] (setup) merged accepts the merge", r.returncode == 0 and known(r), (r.returncode, said(r)[-400:]))
    return r


# ------------------------------------------------------------------------------------------------ cases

def t01_merge_then_merged_carries_on():
    print("CP5-T01: after step 1, merging main and running merged carries the lane on with no question")
    d = build("t01")
    make_main(d)
    merge_main(d)
    log, before = human_log_len(d), snap(d)
    check("[CP5-T01] (setup) no question is pending before merged", not before["pending"], before["pending"])
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T01] merged exits 0", r.returncode == 0 and known(r), (r.returncode, out[-400:]))
    check("[CP5-T01] it says the merge was checked and accepted",
          re.search(r"merge", out, re.I) is not None and re.search(r"accept", out, re.I) is not None, out[-400:])
    st = sj(d)
    check("[CP5-T01] no question is pending in state.json", not st.get("pending"), st.get("pending"))
    check("[CP5-T01] nothing was added to the human log", human_log_len(d) == log, (log, human_log_len(d)))
    check("[CP5-T01] phase is still building with CP2 the current step and CP1 still done",
          st["phase"] == "building" and st["current"] == "CP2" and st["checkpoints"]["CP1"]["status"] == "done"
          and st["checkpoints"]["CP2"]["step"] == "tests", (st["phase"], st["current"]))
    ev = history(d, "merged_main")
    check("[CP5-T01] the run log has exactly one merged_main event", len(ev) == 1, ev)
    s = rx(d, "status")
    sjn = json.loads(rx(d, "status", "--json").stdout)
    check("[CP5-T01] status does not say the run is waiting for an answer",
          not [ln for ln in said(s).splitlines() if re.search(r"wait", ln, re.I) and re.search(r"answer", ln, re.I)]
          and not sjn.get("question"), (said(s)[-300:], sjn.get("question")))
    (d / "tests/cp2.test.js").write_text(CP2_TEST_JS)
    r = rx(d, "tests-written", "CP2")
    check("[CP5-T01] the next command for CP2 (tests-written) is not blocked by integrity",
          r.returncode == 0 and REFUSAL not in said(r), (r.returncode, said(r)[-300:]))


def t02_main_breaks_an_earlier_step():
    print("CP5-T02: if main's change breaks an earlier step, the run stops and names that step's tests")
    d = build("t02")
    make_main(d, brk=True)
    merge_main(d)
    before = snap(d)
    r = rx(d, "merged")
    out = ours(r)
    st = sj(d)
    check("[CP5-T02] merged exits non-zero with the engine's own message", r.returncode != 0 and known(r),
          (r.returncode, out[-400:]))
    check("[CP5-T02] the run is blocked", st["phase"] == "blocked", st["phase"])
    reason = (st.get("blocked_reason") or "") + "\n" + out
    check("[CP5-T02] the reason names CP1, its title and its test command",
          "CP1" in reason and "Price helper" in reason and "node --test tests/cp1.test.js" in reason, reason[-500:])
    check("[CP5-T02] CP2 has not started its work: still step 'tests', base_tree unchanged",
          st["checkpoints"]["CP2"]["step"] == "tests"
          and st["checkpoints"]["CP2"]["base_tree"] == before["checkpoints"]["CP2"]["base_tree"], st["checkpoints"]["CP2"])
    check("[CP5-T02] no merged_main event was logged for a failed re-check", not history(d, "merged_main"),
          history(d, "merged_main"))
    check("[CP5-T02] the message offers the existing blocked options (feedback to add a fix step)",
          "feedback" in out.lower(), out[-400:])
    check("[CP5-T02] it does not tell the agent to approve anything",
          "approve-changes" not in out and "approve-plan" not in out, out[-400:])


def t03_regression_command_is_the_check():
    print("CP5-T03: when a regression command is set, it is the check that runs on the merged code")
    d = build("t03", regression="pass_if_merged")
    aux = AUXES[str(d)]
    make_main(d)
    merge_main(d)
    raise_flag(d)
    reg_before, cp1_before = lines(aux["marker"]), lines(aux["marker2"])
    check("[CP5-T03] (setup) the regression command already ran once, at CP1's completion", reg_before == 1,
          reg_before)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T03] merged exits 0 (the command passed because src/m.js exists on the merged files)",
          r.returncode == 0 and known(r), (r.returncode, out[-400:]))
    check("[CP5-T03] the regression command ran exactly once on the merged code",
          lines(aux["marker"]) == reg_before + 1, (reg_before, lines(aux["marker"])))
    check("[CP5-T03] CP1's own test command was not run instead of it", lines(aux["marker2"]) == cp1_before,
          (cp1_before, lines(aux["marker2"])))
    check("[CP5-T03] the run goes on (building, CP2 current)",
          sj(d)["phase"] == "building" and sj(d)["current"] == "CP2", sj(d)["phase"])


def t04_failing_regression_command_stops_the_run():
    print("CP5-T04: a failing regression command stops the run and is named as such")
    d = build("t04", regression="fail_after_merge")
    aux = AUXES[str(d)]
    make_main(d)
    merge_main(d)
    raise_flag(d)
    r = rx(d, "merged")
    out = ours(r)
    st = sj(d)
    check("[CP5-T04] merged exits non-zero with the engine's own message", r.returncode != 0 and known(r),
          (r.returncode, out[-400:]))
    check("[CP5-T04] the run is blocked", st["phase"] == "blocked", st["phase"])
    reason = (st.get("blocked_reason") or "") + "\n" + out
    check("[CP5-T04] the reason names the regression command and says an earlier behaviour broke",
          re.search(r"regression", reason, re.I) is not None and re.search(r"earlier", reason, re.I) is not None
          and re.search(r"behaviou?r", reason, re.I) is not None, reason[-500:])
    check("[CP5-T04] it does not blame CP1's own tests", "node --test tests/cp1.test.js" not in reason, reason[-500:])
    check("[CP5-T04] the regression command ran on the merged code", lines(aux["marker"]) >= 2, lines(aux["marker"]))
    kept = [p.name for p in (d / ".ratchet/runs/f/evidence").rglob("*")
            if p.is_file() and "MERGED-RUN-MARKER" in p.read_text(errors="replace")]
    check("[CP5-T04] the failing run's log is kept under the evidence folder", bool(kept), kept)
    check("[CP5-T04] no merged_main event was logged", not history(d, "merged_main"), history(d, "merged_main"))


def t05_next_step_reviewers_see_only_its_own_changes():
    print("CP5-T05: the next step's reviewers see only that step's own changes, not what came from main")
    try:
        t05_main_part()
    finally:
        py_freeze_after_merge()


def t05_main_part():
    d = build("t05")
    make_main(d, std=True)
    merge_main(d)
    merged_ok(d, "CP5-T05")
    cp2_tests_written(d, to_complete=True)
    base = sj(d)["checkpoints"]["CP2"]["base_tree"]
    own = ["src/cart.js", "tests/cp2.test.js"]
    got = changed_since(d, base)
    check("[CP5-T05] CP2's own changes (base_tree to now) are exactly its two files", got == own, got)
    rnd = tg.open_round(d, "CP2")
    honest = sorted(tg._default_files_reviewed(d, rnd))
    check("[CP5-T05] an honest default review lists exactly those two files", honest == own, honest)
    check("[CP5-T05] neither main's src/m.js, the standards file nor CP1's files are in it",
          not {"src/m.js", ".ratchet/standards.md", "src/f.js", "tests/cp1.test.js"} & set(honest), honest)
    p = tg.write_review(d, rnd, tg.R0(rnd), files_reviewed=["src/m.js"])
    r = rx(d, "adv-record", "CP2", "--reviews", p)
    check("[CP5-T05] a review that lists only main's src/m.js is not accepted as covering CP2's changes",
          r.returncode != 0 and sj(d)["checkpoints"]["CP2"]["gates"]["adversarial"]["status"] != "passed",
          (r.returncode, said(r)[-300:]))


def py_freeze_after_merge():
    """Main brought a test into the file CP2 plans to write in; once merged has run, removing it asks."""
    d = build("t05py", cp2="py")
    make_main(d, py_test=True)
    merge_main(d)
    r = rx(d, "merged")
    check("[CP5-T05] (python step) merged accepts the merge", r.returncode == 0 and known(r), said(r)[-300:])
    tf = d / "tests/test_cart.py"
    tf.write_text(MAIN_PY_TEST + PY_CP2_TEST)
    for step in ("tests-written", "red"):
        r = rx(d, step, "CP2")
        need(f"[CP5-T05] (setup) {step} CP2 in the Python step", r.returncode == 0, said(r)[-300:])
    (d / "src/cart.py").write_text(CART_PY)
    for step in ("implemented", "behavior"):
        r = rx(d, step, "CP2")
        need(f"[CP5-T05] (setup) {step} CP2 in the Python step", r.returncode == 0, said(r)[-300:])
    red = tf.read_text()
    tf.write_text(red + "\n\ndef test_extra_by_this_lane():\n    assert 2 + 2 == 4\n")
    r = rx(d, "behavior", "CP2")
    check("[CP5-T05] a test this lane adds after red is still free to change (no question)",
          r.returncode == 0 and REFUSAL not in said(r), (r.returncode, said(r)[-300:]))
    tf.write_text(red)
    r = rx(d, "behavior", "CP2")
    check("[CP5-T05] ... and to remove again (no question)",
          r.returncode == 0 and REFUSAL not in said(r), (r.returncode, said(r)[-300:]))
    tf.write_text(red.replace(MAIN_PY_TEST, ""))
    r = rx(d, "behavior", "CP2")
    out = ours(r)
    check("[CP5-T05] removing the test that came in from main asks (it is not lane-added): exit non-zero",
          r.returncode != 0, (r.returncode, out[-300:]))
    check("[CP5-T05] ... with the freeze refusal naming tests/test_cart.py",
          REFUSAL in out and "tests/test_cart.py" in out, out[-400:])


def t06_next_commit_holds_only_its_own_changes():
    print("CP5-T06: the next step's commit holds only its own changes")
    d = build("t06")
    make_main(d, std=True)
    merge_main(d)
    merge_sha = git(d, "rev-parse", "HEAD").stdout.strip()
    merged_ok(d, "CP5-T06")
    cp2_tests_written(d, to_complete=True)
    rnd = tg.open_round(d, "CP2")
    r = rx(d, "adv-record", "CP2", "--reviews", tg.write_review(d, rnd, tg.R0(rnd)))
    need("[CP5-T06] (setup) the clean CP2 review is recorded", r.returncode == 0, said(r)[-300:])
    r = rx(d, "complete", "CP2")
    need("[CP5-T06] (setup) complete CP2 commits", r.returncode == 0, said(r)[-300:])
    files = sorted(git(d, "show", "--name-only", "--format=", "HEAD").stdout.split())
    shared = {".ratchet/.gitignore", ".ratchet/config.json", ".ratchet/learnings.md", ".ratchet/standards.md"}
    check("[CP5-T06] CP2's commit lists its own two files", {"src/cart.js", "tests/cp2.test.js"} <= set(files), files)
    check("[CP5-T06] ... and nothing else but the shared .ratchet files",
          set(files) <= {"src/cart.js", "tests/cp2.test.js"} | shared, files)
    check("[CP5-T06] ... in particular not src/m.js or CP1's files",
          not {"src/m.js", "src/f.js", "tests/cp1.test.js"} & set(files), files)
    check("[CP5-T06] the merge commit is still in the lane's history, right under CP2's commit",
          git(d, "rev-parse", "HEAD~1").stdout.strip() == merge_sha, (git(d, "rev-parse", "HEAD~1").stdout, merge_sha))
    check("[CP5-T06] main's commit is an ancestor of HEAD (nothing rewritten or squashed)",
          git(d, "merge-base", "--is-ancestor", "main", "HEAD").returncode == 0)
    check("[CP5-T06] exactly one merge commit is in the history",
          len(git(d, "rev-list", "--merges", "HEAD").stdout.split()) == 1, git(d, "rev-list", "--merges", "HEAD").stdout)


def t07_standards_and_config_from_main_become_the_baseline():
    print("CP5-T07: standards and config rules that came with main become the new baseline and are named")
    d = build("t07")
    make_main(d, std=True)
    merge_main(d)
    (d / "tests/cp2.test.js").write_text(CP2_TEST_JS)
    r = rx(d, "tests-written", "CP2")
    check("[CP5-T07] (before) another command refuses: reference file .ratchet/standards.md changed",
          r.returncode != 0 and "reference file .ratchet/standards.md" in said(r), (r.returncode, said(r)[-300:]))
    before, log = sj(d)["approved"]["files"][".ratchet/standards.md"], human_log_len(d)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T07] merged exits 0", r.returncode == 0 and known(r), (r.returncode, out[-400:]))
    check("[CP5-T07] its output names .ratchet/standards.md as accepted from main",
          ".ratchet/standards.md" in out and re.search(r"accept", out, re.I) is not None, out[-400:])
    check("[CP5-T07] the approved snapshot of the standards file moved to the merged file",
          sj(d)["approved"]["files"][".ratchet/standards.md"] != before, before)
    check("[CP5-T07] the standards file holds main's new line", STD_LINE in (d / ".ratchet/standards.md").read_text())
    r = rx(d, "tests-written", "CP2")
    check("[CP5-T07] tests-written CP2 now works with no approve-changes",
          r.returncode == 0 and REFUSAL not in said(r) and "approve-changes" not in said(r), (r.returncode, said(r)[-300:]))
    check("[CP5-T07] no question is pending and the human log did not grow",
          not sj(d).get("pending") and human_log_len(d) == log, (sj(d).get("pending"), log, human_log_len(d)))

    d2 = build("t07cfg")
    make_main(d2, config={"max_behavior_attempts": 7})
    merge_main(d2)
    (d2 / "tests/cp2.test.js").write_text(CP2_TEST_JS)
    r = rx(d2, "tests-written", "CP2")
    check("[CP5-T07] (before) a rule changed on main is refused as 'config rule max_behavior_attempts'",
          r.returncode != 0 and "config rule 'max_behavior_attempts'" in said(r), (r.returncode, said(r)[-300:]))
    r = rx(d2, "merged")
    out = ours(r)
    check("[CP5-T07] merged exits 0 for a config rule changed on main", r.returncode == 0 and known(r),
          (r.returncode, out[-400:]))
    check("[CP5-T07] its output names the rule max_behavior_attempts", "max_behavior_attempts" in out, out[-400:])
    r = rx(d2, "tests-written", "CP2")
    check("[CP5-T07] tests-written CP2 then works with no approve-changes",
          r.returncode == 0 and REFUSAL not in said(r), (r.returncode, said(r)[-300:]))


def t08_changes_not_exactly_as_main_still_need_approval():
    print("CP5-T08: a change that did not come from main as main has it still needs approval")
    # Variant A: main raised max_behavior_attempts to 9 and appended a standards line; the agent resolved the
    # config file by hand to a value neither side has.
    d = build("t08a")
    make_main(d, std=True, config={"max_behavior_attempts": 9})
    r = git(d, "merge", "--no-ff", "--no-commit", "main")
    need("[CP5-T08] (setup) the merge went in without committing", r.returncode == 0, r.stdout + r.stderr)
    cfgp = d / ".ratchet/config.json"
    c = json.loads(cfgp.read_text())
    c["max_behavior_attempts"] = 4
    cfgp.write_text(json.dumps(c, indent=2) + "\n")
    commit_all(d, "Merge main into lane, config resolved by hand")
    before = sj(d)["approved"]
    (d / "tests/cp2.test.js").write_text(CP2_TEST_JS)
    r = rx(d, "merged")
    out = ours(r)
    after = sj(d)["approved"]
    check("[CP5-T08] merged output names the standards file as accepted from main",
          ".ratchet/standards.md" in out, out[-400:])
    check("[CP5-T08] the standards line that arrived exactly as main has it was re-baselined",
          after["files"][".ratchet/standards.md"] != before["files"][".ratchet/standards.md"],
          before["files"][".ratchet/standards.md"])
    check("[CP5-T08] the hand-resolved rule was NOT re-baselined silently",
          after["rules"]["max_behavior_attempts"] == before["rules"]["max_behavior_attempts"],
          (before["rules"]["max_behavior_attempts"], after["rules"]["max_behavior_attempts"]))
    r2 = rx(d, "tests-written", "CP2")
    out2 = said(r2) + out
    check("[CP5-T08] the rule is still named as changed after approval and needs approve-changes",
          "config rule 'max_behavior_attempts'" in out2 and "approve-changes" in said(r2), said(r2)[-400:])
    check("[CP5-T08] ... but the standards file is not named any more",
          "reference file .ratchet/standards.md" not in said(r2), said(r2)[-400:])
    check("[CP5-T08] the lane does not carry on past the approval: tests-written refused, CP2 still at 'tests'",
          r2.returncode != 0 and sj(d)["checkpoints"]["CP2"]["step"] == "tests", (r2.returncode, sj(d)["phase"]))

    # Variant B: the plan's CP2 done_when line was edited before the merge.
    d = build("t08b")
    make_main(d, std=True)
    cpp = d / ".ratchet/runs/f/checkpoints.json"
    plan = json.loads(cpp.read_text())
    plan["checkpoints"][1]["done_when"] = ["a cart adds its lines and applies tax"]
    cpp.write_text(json.dumps(plan))
    merge_main(d)
    before = sj(d)["approved"]
    (d / "tests/cp2.test.js").write_text(CP2_TEST_JS)
    r = rx(d, "merged")
    out = ours(r)
    after = sj(d)["approved"]
    check("[CP5-T08] (plan edited) merged output names the standards file as accepted from main",
          ".ratchet/standards.md" in out, out[-400:])
    check("[CP5-T08] (plan edited) the standards file was re-baselined",
          after["files"][".ratchet/standards.md"] != before["files"][".ratchet/standards.md"], "")
    check("[CP5-T08] (plan edited) checkpoint CP2's approved plan hash was NOT re-baselined",
          after["cps"]["CP2"] == before["cps"]["CP2"], (before["cps"]["CP2"], after["cps"]["CP2"]))
    r2 = rx(d, "tests-written", "CP2")
    check("[CP5-T08] (plan edited) checkpoint CP2 is still named as changed and needs approve-changes",
          r2.returncode != 0 and "checkpoint CP2 in checkpoints.json" in said(r2) and "approve-changes" in said(r2),
          (r2.returncode, said(r2)[-400:]))
    check("[CP5-T08] (plan edited) the standards file is not named any more",
          "reference file .ratchet/standards.md" not in said(r2), said(r2)[-400:])


def t09_merged_in_the_middle_of_a_step_is_refused():
    print("CP5-T09: merged in the middle of a step is refused with a plain message to finish the step")
    d = build("t09")
    make_main(d)
    (d / "tests/cp2.test.js").write_text(CP2_TEST_JS)
    need("[CP5-T09] (setup) tests-written CP2 ran", rx(d, "tests-written", "CP2").returncode == 0, "")
    merge_main(d)
    before = snap(d)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T09] merged exits non-zero with the engine's own message", r.returncode != 0 and known(r),
          (r.returncode, out[-300:]))
    check("[CP5-T09] the message says to finish the step first", re.search(r"finish", out, re.I) is not None
          and "CP2" in out, out[-300:])
    check("[CP5-T09] state.json is unchanged (base_tree, approved snapshot, no merged_main event)",
          snap(d) == before and not history(d, "merged_main"), "")
    need("[CP5-T09] (setup) red CP2", rx(d, "red", "CP2").returncode == 0, "")
    (d / "src/cart.js").write_text(CART_JS)
    need("[CP5-T09] (setup) implemented CP2", rx(d, "implemented", "CP2").returncode == 0, "")
    before = snap(d)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T09] after red and implemented the same refusal holds",
          r.returncode != 0 and known(r) and re.search(r"finish", out, re.I) is not None, (r.returncode, out[-300:]))
    check("[CP5-T09] ... and state.json is still unchanged", snap(d) == before and not history(d, "merged_main"), "")


def t10_merged_without_a_finished_merge_is_refused():
    print("CP5-T10: merged with no finished merge to check is refused and changes nothing")
    d = build("t10")
    make_main(d)
    before = snap(d)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T10] (a) nothing merged: exits non-zero with the engine's own message, saying to merge main",
          r.returncode != 0 and known(r) and re.search(r"merge", out, re.I) is not None, (r.returncode, out[-300:]))
    check("[CP5-T10] (a) state.json is unchanged", snap(d) == before, "")
    r = git(d, "merge", "--no-ff", "--no-commit", "main")
    need("[CP5-T10] (setup) git merge --no-commit stopped before committing",
         (d / ".git/MERGE_HEAD").exists(), r.stdout + r.stderr)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T10] (b) merge not finished: exits non-zero with the engine's own message, saying to finish it",
          r.returncode != 0 and known(r) and re.search(r"merge", out, re.I) is not None
          and re.search(r"finish|commit|complete", out, re.I) is not None, (r.returncode, out[-300:]))
    check("[CP5-T10] (b) state.json is unchanged", snap(d) == before, "")
    need("[CP5-T10] (setup) the merge is finished", git(d, "commit", "-qm", "Merge main").returncode == 0, "")
    with (d / "src/f.js").open("a") as f:
        f.write("// edited after the merge, not committed\n")
    before = snap(d)
    r = rx(d, "merged")
    out = ours(r)
    check("[CP5-T10] (c) a tracked file edited after the merge: exits non-zero, saying to commit the edit",
          r.returncode != 0 and known(r) and re.search(r"commit", out, re.I) is not None, (r.returncode, out[-300:]))
    check("[CP5-T10] (c) state.json is unchanged", snap(d) == before, "")
    git(d, "checkout", "--", "src/f.js")
    r = rx(d, "merged")
    check("[CP5-T10] after finishing the merge properly, merged is accepted as in the first case",
          r.returncode == 0 and known(r) and len(history(d, "merged_main")) == 1, (r.returncode, said(r)[-300:]))


def t11_merged_while_a_step_review_waits():
    print("CP5-T11: merged while a step review waits is accepted and does not count as the user's review")
    d = build("t11", human_review="each")
    make_main(d)
    merge_main(d)
    st = sj(d)
    check("[CP5-T11] (setup) the run waits for the user's review of CP1",
          st["phase"] == "awaiting_checkpoint_review" and st["checkpoints"]["CP1"]["gates"]["human"]["status"] != "passed",
          st["phase"])
    r = rx(d, "merged")
    out = ours(r)
    st = sj(d)
    check("[CP5-T11] merged exits 0 (accepted and re-checked)", r.returncode == 0 and known(r),
          (r.returncode, out[-400:]))
    check("[CP5-T11] the phase is still awaiting_checkpoint_review",
          st["phase"] == "awaiting_checkpoint_review", st["phase"])
    check("[CP5-T11] CP1's human gate is still not passed (merged approved nothing)",
          st["checkpoints"]["CP1"]["gates"]["human"]["status"] != "passed", st["checkpoints"]["CP1"]["gates"]["human"])
    check("[CP5-T11] a merged_main event was logged", len(history(d, "merged_main")) == 1, history(d, "merged_main"))
    need("[CP5-T11] (setup) asking for the review works", rx(d, "ask", "human-approve").returncode == 0, "")
    tg.say(d, "yes, go ahead")
    r = rx(d, "human-approve")
    st = sj(d)
    check("[CP5-T11] the user's yes then moves the run on to CP2",
          r.returncode == 0 and st["phase"] == "building" and st["current"] == "CP2", (r.returncode, st["phase"], said(r)[-300:]))
    check("[CP5-T11] CP1's human gate is passed only now", st["checkpoints"]["CP1"]["gates"]["human"]["status"] == "passed",
          st["checkpoints"]["CP1"]["gates"]["human"])
    base = st["checkpoints"]["CP2"].get("base_tree")
    check("[CP5-T11] CP2's base_tree is the merged fingerprint, so main's files are not CP2's own changes",
          base == fp(d) and changed_since(d, base) == [], (base, changed_since(d, base) if base else None))


# ------------------------------------------------------------------------------------------------ driver

TESTS = [
    ("CP5-T01", t01_merge_then_merged_carries_on),
    ("CP5-T02", t02_main_breaks_an_earlier_step),
    ("CP5-T03", t03_regression_command_is_the_check),
    ("CP5-T04", t04_failing_regression_command_stops_the_run),
    ("CP5-T05", t05_next_step_reviewers_see_only_its_own_changes),
    ("CP5-T06", t06_next_commit_holds_only_its_own_changes),
    ("CP5-T07", t07_standards_and_config_from_main_become_the_baseline),
    ("CP5-T08", t08_changes_not_exactly_as_main_still_need_approval),
    ("CP5-T09", t09_merged_in_the_middle_of_a_step_is_refused),
    ("CP5-T10", t10_merged_without_a_finished_merge_is_refused),
    ("CP5-T11", t11_merged_while_a_step_review_waits),
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
    global FAIL
    only = [a.upper() for a in sys.argv[1:]]   # optional: case ids to run while working on one ("T05 T08")
    for cid, fn in TESTS:
        if only and not any(cid.endswith(o) for o in only):
            continue
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Stop:
            pass    # the failed check that stopped the case is already recorded
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"[{cid}] stopped early: {type(e).__name__}: {e}"[:200])
            print(f"  ✗ [{cid}] stopped early: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


import merge_main_cases  # noqa: E402,F401  (adds the CP5-X cases after the planned ones)


if __name__ == "__main__":
    main()
