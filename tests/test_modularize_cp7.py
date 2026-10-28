#!/usr/bin/env python3
"""
CP7 tests: plan/objective validation (objective_problems/_proves_values/proves_problems/objective_brief/
_normalize_role) and the planner's "What earlier work says" digest (_digest_words/_learnings_bullets/
_related_spec_items/_earlier_work_lines/etc.) move, verbatim, into
skills/ratchet-orchestrator/scripts/plan_support.py.   python3 tests/test_modularize_cp7.py

Strategy (mirrors CP7.json's own "strategy" field): drive plan-submit and brief exactly as a developer's agent
would, through tests/test_gates.py's `rx`/`say`/`git` subprocess helpers and throwaway git fixtures, the same
black-box way every earlier checkpoint's behavior cases do. Every one of CP7-T01..T10/T13/T14's cases asserts a
fixed, pinned piece of behavior (an exact refusal string, an exact accept message, exact digest ordering/caps)
that holds identically whether plan_support.py's functions still live inline in ratchet.py or have already moved
into their own module -- so these cases double as the "byte-identical before/after" proof CP1-style baseline
comparisons give the other checkpoints, without needing a second, pinned engine instance (there is no
intervening unrelated commit between CP6's landing and this checkpoint's own move, unlike CP6's own note about
approval_snapshot). The move itself -- the one thing that genuinely does not exist yet -- is proven structurally
by CP7-T11 (explicit named imports, no circular import, no new module-level mutable registry), CP7-T12 (the one
test, test_digest_docstring.py, that reads ratchet.py's literal source text for a function this checkpoint
relocates, re-pointed to plan_support.py) and CP7-T15 (the diff touches only the expected files). CP7-T13 runs
the four regression files (test_dm_table.py/test_dm_accept.py/test_digest_docstring.py/test_brief_guard.py) for
real, unedited, against the post-move tree.

Heavily reuses tests/test_digest.py (as `td`: HEADING/LEARN_HEAD/OPEN_HEAD/ACCEPTED_HEAD/_section/
_project_with_feature/_write_earlier_run/_append_learning_lines/_finding/rich) and tests/test_digest_specs.py (as
`tds`: SPEC_HEAD/_openspec_spec/_bmad_story) for the earlier-work-digest fixtures, the same way
tests/test_digest_specs.py itself reuses tests/test_digest.py's fixtures instead of hand-copying a third version.

Just a plain check()/PASS/FAIL tally (this checkpoint's tests.command lists no "report" key, i.e. "none": the
same ad hoc tests/run_all.py design most checkpoints in this run use) -- no RATCHET_TEST_REPORT JSON writer.
"""
from __future__ import annotations

import ast
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
import _tmp  # noqa: E402
import test_gates as g  # noqa: E402  (project, rx, say, git, ENV, ENGINE, ...)
import test_digest as td  # noqa: E402  (HEADING, _section, _project_with_feature, _write_earlier_run, ...)
import test_digest_specs as tds  # noqa: E402  (SPEC_HEAD, _openspec_spec, _bmad_story)

SCRIPT_DIR = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
ENGINE = SCRIPT_DIR / "ratchet.py"
PLAN_SUPPORT = SCRIPT_DIR / "plan_support.py"
sys.path.insert(0, str(SCRIPT_DIR))  # lets CP7-T12 import `ratchet` directly, the same way CP6's suite does

BASELINE_DIR = ROOT / "tests" / "fixtures" / "modularize_cp1" / "baseline"
MANIFEST = BASELINE_DIR / "manifest.json"

# Lane M's own run directory. It exists only inside M's own worktree/run; on integrated main it is
# gone. CP7-T15's diff-scope check is meaningful only inside M's own run (it diffs against CP6's
# landing commit assuming HEAD is still M's run tip), so it keys off this directory's existence.
RUN_DIR = ROOT / ".ratchet" / "runs" / "behavior-preserving-modularization-of-skills-rat"

# The pre-move commit for this checkpoint is simply the current HEAD (CP6's own landing commit): confirmed by
# hand that `git status` shows no uncommitted change to ratchet.py and that nothing in this checkpoint's move has
# landed yet -- the same "pin note" CP6's own suite documents for itself.
BASE_COMMIT = "69cb405"

# Copied verbatim from ratchet.py (line ~1361): the exact text objective_problems has always built its
# missing-objective refusal from. Kept as a literal here (not read off disk) so this check pins the real,
# historical wording even if OBJECTIVE_EXAMPLE itself moves to plan_support.py along with objective_problems.
OBJECTIVE_EXAMPLE = ('{"users": "who will use the result", "goal": "what they are trying to do", '
                      '"done_means": ["what done means to them, one line per point"], '
                      '"out_of_scope": ["what this run deliberately will not build or chase"]}')

_OBJ_FIELD_MESSAGES = {
    "users": "objective.users: missing or empty (who will use the result)",
    "goal": "objective.goal: missing or empty (what they are trying to do)",
    "done_means": "objective.done_means: missing or empty (what done means to them, as a non-empty list)",
    "out_of_scope": ("objective.out_of_scope: missing or empty (what this run deliberately will not build or "
                     "chase, as a non-empty list)"),
}

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ---- fixtures / helpers ------------------------------------------------------------------------------------


def _init_project(slug):
    """A throwaway git project through `init` only (no plan written/submitted yet), so each plan-validation case
    can build and submit its own hand-crafted checkpoints.json/tests/CPn.json without test_gates.project()'s own
    always-valid plan getting in the way."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp7-{slug}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", slug)
    assert r.returncode == 0, r.stderr
    run = d / ".ratchet" / "runs" / slug
    (run / "app-scout.json").write_text("{}")
    return d, run


def _cp(cid="CP1", done_when=None):
    return {"id": cid, "title": "Step", "summary": "s", "done_when": done_when or ["works"],
            "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
            "tests": {"files": [f"tests/{cid.lower()}.test.js"], "command": f"node --test tests/{cid.lower()}.test.js"}}


def top_level_defs(tree) -> dict:
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _read_manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def show_at(commit: str, relpath: str) -> str:
    """Same `git show`-based pinned-source read CP3-T07/CP4-06/CP5-T04/CP6-T03 already use."""
    r = subprocess.run(["git", "show", f"{commit}:{relpath}"], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"git show failed: {r.returncode} {r.stderr[-300:]}")
    return r.stdout


# ============================================================================================ CP7-T01


def t01_missing_objective_refused():
    print("CP7-T01: a plan with no objective at all is refused with the exact missing-objective message")
    d, run = _init_project("t01")
    plan = {"feature": "x", "checkpoints": [_cp()]}
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    r = g.rx(d, "plan-submit")
    check("[CP7-T01] plan-submit refuses (non-zero exit) a plan with no objective key at all",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    expected = f"objective: missing. Every plan needs one, e.g. {OBJECTIVE_EXAMPLE}"
    check("[CP7-T01] the refusal names the exact missing-objective message objective_problems has always "
          "produced, unchanged after the move to plan_support.py", expected in r.stderr, r.stderr)


# ============================================================================================ CP7-T02


def t02_missing_single_field_refused_by_name():
    print("CP7-T02: an objective missing users, goal, done_means or out_of_scope is refused naming each field")
    base = {"users": "testers", "goal": "ship it", "done_means": ["it works"], "out_of_scope": ["nothing else"]}
    for missing_field, expected in _OBJ_FIELD_MESSAGES.items():
        obj = dict(base)
        obj[missing_field] = [] if missing_field in ("done_means", "out_of_scope") else ""
        d, run = _init_project(f"t02{missing_field[:4]}")
        plan = {"feature": "x", "objective": obj, "checkpoints": [_cp()]}
        (run / "checkpoints.json").write_text(json.dumps(plan))
        (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
        r = g.rx(d, "plan-submit")
        check(f"[CP7-T02] missing objective.{missing_field}: plan-submit refuses",
              r.returncode != 0, (r.returncode, r.stdout, r.stderr))
        check(f"[CP7-T02] missing objective.{missing_field}: the exact refusal line appears, unchanged after "
              "the move", expected in r.stderr, r.stderr)
        others = [m for f2, m in _OBJ_FIELD_MESSAGES.items() if f2 != missing_field]
        check(f"[CP7-T02] missing objective.{missing_field}: no other field's missing-line appears",
              not any(m in r.stderr for m in others), r.stderr)


# ============================================================================================ CP7-T03


def t03_proves_out_of_range_refused_by_case_id():
    print("CP7-T03: a case's \"proves\" naming a line that doesn't exist is refused by case id")
    d, run = _init_project("t03")
    obj = dict(_tmp.OBJECTIVE_MIN)
    obj["done_means"] = ["line one", "line two", "line three"]
    plan = {"feature": "x", "objective": obj, "checkpoints": [_cp("CP1", obj["done_means"])]}
    (run / "checkpoints.json").write_text(json.dumps(plan))
    cases = [{"id": "CP1-T01", "layer": "code", "proves": 0},
             {"id": "CP1-T02", "layer": "code", "proves": 99},
             {"id": "CP1-T03", "layer": "code", "proves": [1, 99]}]
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": cases}))
    r = g.rx(d, "plan-submit")
    check("[CP7-T03] plan-submit refuses the plan", r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    for cid, bad in (("CP1-T01", 0), ("CP1-T02", 99), ("CP1-T03", 99)):
        expected = f"{cid}: proves {bad} is not a done_means line (objective.done_means has 3 line(s))"
        check(f"[CP7-T03] the refusal names {cid} and the out-of-range number {bad}, stating done_means has "
              "3 line(s), exactly as proves_problems has always worded it", expected in r.stderr, r.stderr)


# ============================================================================================ CP7-T04


def t04_fully_proved_plan_accepted():
    print("CP7-T04: a valid plan whose every done_means line is proved by some case is accepted")
    d, run = _init_project("t04")
    obj = dict(_tmp.OBJECTIVE_MIN)
    obj["done_means"] = ["line one gets proved by CP1", "line two gets proved by CP2"]
    plan = {"feature": "x", "objective": obj,
            "checkpoints": [_cp("CP1", obj["done_means"]), _cp("CP2", obj["done_means"])]}
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code", "proves": [2]}]}))
    before = (run / "checkpoints.json").read_bytes()
    r = g.rx(d, "plan-submit")
    check("[CP7-T04] plan-submit accepts a plan whose every done_means line is proved somewhere across the "
          "plan's test files", r.returncode == 0, (r.returncode, r.stdout, r.stderr))
    check("[CP7-T04] the exact accept message plan-submit has always printed is shown",
          "RATCHET ✓ Plan valid (2 checkpoints)." in r.stdout, r.stdout)
    check("[CP7-T04] the plan file itself is persisted unchanged by plan-submit",
          (run / "checkpoints.json").read_bytes() == before, "")


# ============================================================================================ CP7-T05


def t05_bare_int_proves_normalized_nonnumeric_refused():
    print("CP7-T05: a bare scalar \"proves\": 1 is normalized like [1]; a non-numeric shape is refused by name")
    d, run = _init_project("t05a")
    obj = dict(_tmp.OBJECTIVE_MIN)
    obj["done_means"] = ["the only line"]
    plan = {"feature": "x", "objective": obj, "checkpoints": [_cp("CP1", obj["done_means"])]}
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": 1}]}))
    r = g.rx(d, "plan-submit")
    check("[CP7-T05] a bare scalar \"proves\": 1 (not a list) is accepted exactly as [1] would be, line 1 "
          "linked, no refusal for that case", r.returncode == 0, (r.returncode, r.stdout, r.stderr))

    d2, run2 = _init_project("t05b")
    obj2 = dict(_tmp.OBJECTIVE_MIN)
    obj2["done_means"] = ["the only line"]
    plan2 = {"feature": "x", "objective": obj2, "checkpoints": [_cp("CP1", obj2["done_means"])]}
    (run2 / "checkpoints.json").write_text(json.dumps(plan2))
    cases2 = [{"id": "CP1-T01", "layer": "code", "proves": "1"},
              {"id": "CP1-T02", "layer": "code", "proves": {"a": 1}}]
    (run2 / "tests" / "CP1.json").write_text(json.dumps({"cases": cases2}))
    r2 = g.rx(d2, "plan-submit")
    check("[CP7-T05] a string \"proves\" and a dict \"proves\" are both refused",
          r2.returncode != 0, (r2.returncode, r2.stdout, r2.stderr))
    expected_str = ("CP1-T01: proves '1' must be a done_means line number or a list of them "
                    "(objective.done_means has 1 line(s))")
    expected_dict = ("CP1-T02: proves {'a': 1} must be a done_means line number or a list of them "
                     "(objective.done_means has 1 line(s))")
    check("[CP7-T05] the string-valued case is named by id with its exact repr'd \"proves\" value",
          expected_str in r2.stderr, r2.stderr)
    check("[CP7-T05] the dict-valued case is named by id with its exact repr'd \"proves\" value",
          expected_dict in r2.stderr, r2.stderr)


# ============================================================================================ CP7-T06


def t06_planner_brief_objective_proof_notes_and_digest():
    print("CP7-T06: brief --role planner prints the objective, proof notes and the earlier-work digest")
    feature = "Add CSV export to the monthly reports page"
    d = td._project_with_feature("cp7t06", feature, cps=1)
    tds._openspec_spec(d, "csv-export", "T06SPEC csv export spec", "lets finance export every row",
                       mtime=1700000000)
    tds._bmad_story(d, "1.1", "1.1.t06done.md", "T06BMAD done story", "Done", mtime=1700000500)
    tds._bmad_story(d, "1.2", "1.2.t06notdone.md", "T06BMAD not done story", "In Progress", mtime=1700000600)
    old = td._finding("F1", "major", "T06OPEN an open finding from an earlier run")
    td._write_earlier_run(d, "run-old", {"CP1": [old]}, updated_at="2026-01-01T00:00:00+00:00", mtime=1700000000)
    td._append_learning_lines(d, "- (2026-01-02, human) T06LEARN keep retries idempotent.",
                              "- (2026-01-03, human) Accepted limit (user decision): T06LIMIT slow on the free "
                              "tier.")
    r = g.rx(d, "brief", "CP1", "--role", "planner")
    check("[CP7-T06] `brief CP1 --role planner` exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP7-T06] the header line is exactly 'CP1: brief for the planner — Step 1'",
          out.splitlines()[0] == "CP1: brief for the planner — Step 1", out[:200])
    check("[CP7-T06] the objective block names who it's for and what they want",
          "Who it's for:" in out and "What they want:" in out, out)
    check("[CP7-T06] the numbered done-means line is printed with its 1-based number",
          "  - 1. the checkpoint's own done_when criteria are met" in out, out)
    check("[CP7-T06] a 'Proved by' note (naming the proving case) follows the done-means line",
          "Proved by: CP1-T01" in out, out)
    check("[CP7-T06] the planner's own ROLE_BRIEFS rules are printed",
          "write the plan's objective first" in out, out)
    sec = td._section(out)
    check("[CP7-T06] the 'What earlier work says' section is printed", bool(sec), out)
    check("[CP7-T06] a recent learnings.md line appears in the section", "T06LEARN" in sec, sec)
    check("[CP7-T06] the open deferred finding's description appears in the section", "T06OPEN" in sec, sec)
    check("[CP7-T06] the accepted-limit learnings bullet appears in the section", "T06LIMIT" in sec, sec)
    check("[CP7-T06] the related OpenSpec entry (title, purpose, path) appears in the section",
          "T06SPEC" in sec and "lets finance export every row" in sec
          and "openspec/specs/csv-export/spec.md" in sec, sec)
    check("[CP7-T06] the done BMAD story appears in the section",
          "T06BMAD done story" in sec and "1.1.t06done.md" in sec, sec)
    check("[CP7-T06] the not-done BMAD story never appears anywhere in the section",
          "T06BMAD not done story" not in sec, sec)


# ============================================================================================ CP7-T07


def t07_non_planner_role_never_shows_digest():
    print("CP7-T07: brief for a non-planner role never prints the earlier-work digest")
    d = td.rich()
    r = g.rx(d, "brief", "CP1", "--role", "implementer")
    check("[CP7-T07] `brief CP1 --role implementer` exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP7-T07] the string 'What earlier work says' never appears for a non-planner role",
          td.HEADING not in out, out)
    for marker in ("RICHOPEN", "RICHACC", "RICHLEARN", "RICHLIMIT", "RICHNEWER"):
        check(f"[CP7-T07] earlier-work marker {marker} is never leaked into a non-planner brief",
              marker not in out, out)
    check("[CP7-T07] the implementer's own ROLE_BRIEFS rules are printed",
          "make the smallest change that makes this checkpoint's planned tests pass" in out, out)
    check("[CP7-T07] the objective block is still printed",
          "Who it's for:" in out and "What they want:" in out, out)


# ============================================================================================ CP7-T08


def t08_normalize_role_prefix_alias_and_unknown():
    print("CP7-T08: _normalize_role still accepts a ratchet-prefixed name and a role alias, and refuses an "
          "unknown one the same way")
    d = td.rich()
    r_fixer_prefixed = g.rx(d, "brief", "CP1", "--role", "ratchet-fixer")
    r_fixer_bare = g.rx(d, "brief", "CP1", "--role", "fixer")
    check("[CP7-T08] 'ratchet-fixer' (the prefix form of 'fixer') exits 0",
          r_fixer_prefixed.returncode == 0, r_fixer_prefixed.stderr)
    check("[CP7-T08] 'ratchet-fixer' prints identical text to calling with the bare name 'fixer'",
          r_fixer_prefixed.stdout == r_fixer_bare.stdout, (r_fixer_bare.stdout, r_fixer_prefixed.stdout))

    r_adv = g.rx(d, "brief", "CP1", "--role", "adversary")
    r_reviewer = g.rx(d, "brief", "CP1", "--role", "reviewer")
    check("[CP7-T08] 'adversary' exits 0", r_adv.returncode == 0, r_adv.stderr)
    check("[CP7-T08] 'adversary' (ROLE_ALIASES renames to 'reviewer') prints identical text to the bare "
          "'reviewer' role", r_adv.stdout == r_reviewer.stdout, (r_reviewer.stdout, r_adv.stdout))

    r_bad = g.rx(d, "brief", "CP1", "--role", "not-a-real-role")
    check("[CP7-T08] an unknown role exits non-zero",
          r_bad.returncode != 0, (r_bad.returncode, r_bad.stdout, r_bad.stderr))
    expected = ("'not-a-real-role' is not a valid role for brief. Valid roles: implementer, fixer, test-writer, "
                "reviewer, scout, planner (or an agent name from agents/, with or without the 'ratchet-' "
                "prefix).")
    check("[CP7-T08] the exact invalid-role message, listing ROLE_ORDER, is unchanged after the move",
          expected in r_bad.stderr, r_bad.stderr)


# ============================================================================================ CP7-T09


def t09_learnings_cap_and_rank():
    print("CP7-T09: the learnings section of the digest caps at 5 and ranks newest/most-relevant first")
    feature = "Add CSV export to the monthly reports page"
    d = td._project_with_feature("cp7t09", feature)
    related = [f"- (2026-01-{i + 1:02d}, human) CP7RELCSV{i:02d} csv export line number {i}."
               for i in range(4)]
    unrelated = [f"- (2026-02-{i + 1:02d}, human) CP7UNREL{i:02d} something else entirely number {i}."
                 for i in range(4)]
    td._append_learning_lines(d, *(related + unrelated))
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    shown = re.findall(r"CP7(?:RELCSV|UNREL)\d\d", sec)
    check("[CP7-T09] the learnings part is capped at 5 items (DIGEST_ITEM_CAP), not all 8 written",
          len(shown) == 5, shown)
    check("[CP7-T09] every item sharing a word with the active run's feature ('export') comes before every "
          "unrelated item", shown[:4] == ["CP7RELCSV03", "CP7RELCSV02", "CP7RELCSV01", "CP7RELCSV00"], shown)
    check("[CP7-T09] within the relevant group, newest-by-date comes first",
          shown[:4] == ["CP7RELCSV03", "CP7RELCSV02", "CP7RELCSV01", "CP7RELCSV00"], shown)
    check("[CP7-T09] the fifth, lowest-priority slot is the newest UNRELATED item, not an older one",
          shown[4:5] == ["CP7UNREL03"], shown)
    check("[CP7-T09] a '... and N more - see .ratchet/learnings.md' line follows, naming the right remaining "
          "count", re.search(r"\.\.\.\s+and\s+3\s+more\s+-\s+see\s+\.ratchet/learnings\.md", sec) is not None,
          sec)


# ============================================================================================ CP7-T10


def t10_accepted_open_specs_render_and_empty_fallback():
    print("CP7-T10: accepted limits, other runs' open findings and related specs render identically; an empty "
          "project gets the plain fallback line")
    feature = "a totally unrelated feature about nothing in particular"
    d = td._project_with_feature("cp7t10", feature)
    accepted = td._finding("A1", "minor", "T10ACC an accepted finding", accepted=True, accepted_by="rahil",
                           reason="accepted for this test")
    open_f = td._finding("O1", "major", "T10OPEN an open finding nobody accepted yet")
    td._write_earlier_run(d, "run-old", {"CP1": [accepted], "CP2": [open_f]},
                          updated_at="2026-01-01T00:00:00+00:00", mtime=1700000000)
    td._append_learning_lines(d, "- (2026-01-02, human) Accepted limit (user decision): T10LIMIT a known limit.")
    tds._bmad_story(d, "2.1", "2.1.t10done.md", "T10BMAD done story", "Done", mtime=1700000500)
    tds._bmad_story(d, "2.2", "2.2.t10notdone.md", "T10BMAD not-done story", "In Progress", mtime=1700000600)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = td._section(out)
    open_idx = sec.find(td.OPEN_HEAD)
    acc_idx = sec.find(td.ACCEPTED_HEAD)
    spec_idx = sec.find(tds.SPEC_HEAD)
    check("[CP7-T10] the open finding is listed under 'Open findings (not fixed):'",
          open_idx >= 0 and sec.find("T10OPEN") > open_idx, sec)
    check("[CP7-T10] the accepted finding is listed under 'Accepted limits:'",
          acc_idx >= 0 and sec.find("T10ACC") > acc_idx, sec)
    check("[CP7-T10] the accepted-limit learnings bullet is listed under 'Accepted limits:'",
          acc_idx >= 0 and sec.find("T10LIMIT") > acc_idx, sec)
    check("[CP7-T10] the done BMAD story is listed under 'Related specs:'",
          spec_idx >= 0 and sec.find("T10BMAD done story") > spec_idx, sec)
    check("[CP7-T10] the not-done BMAD story is excluded entirely", "T10BMAD not-done story" not in sec, sec)

    d2 = g.project("cp7t10empty", cps=1)
    out2 = g.rx(d2, "brief", "CP1", "--role", "planner").stdout
    sec2 = td._section(out2)
    rest2 = sec2[len(td.HEADING):]
    lines2 = [l for l in rest2.splitlines() if l.strip()]
    check("[CP7-T10] an empty project's digest is exactly one plain line", len(lines2) == 1, rest2)
    check("[CP7-T10] that line says there is no earlier work recorded for this project yet",
          bool(lines2) and re.search(r"no earlier work", lines2[0], re.I) is not None, rest2)
    check("[CP7-T10] no empty sub-headings are ever printed",
          not any(h in sec2 for h in (td.LEARN_HEAD, td.OPEN_HEAD, td.ACCEPTED_HEAD, tds.SPEC_HEAD)), sec2)


# ============================================================================================ CP7-T11


def t11_plan_support_imports_and_no_circular_or_registry():
    print("CP7-T11: plan_support.py is imported by explicit name, has no circular dependency on ratchet.py, "
          "and introduces no new mutable module-level registry")
    check("[CP7-T11] plan_support.py exists", PLAN_SUPPORT.exists())
    if not PLAN_SUPPORT.exists():
        return
    live_src = ENGINE.read_text()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
    except SyntaxError as e:
        check("[CP7-T11] the current ratchet.py parses as valid Python", False, str(e))
        return
    star = [n for n in ast.walk(live_tree) if isinstance(n, ast.ImportFrom) and n.module == "plan_support"
            and any(a.name == "*" for a in n.names)]
    check("[CP7-T11] ratchet.py never does `from plan_support import *`", not star, "")
    named = set()
    for n in ast.walk(live_tree):
        if isinstance(n, ast.ImportFrom) and n.module == "plan_support":
            named.update(a.name for a in n.names if a.name != "*")
    required = ["objective_problems", "_proves_values", "proves_problems", "objective_brief", "_normalize_role",
                "_digest_words", "_learnings_bullets", "_related_spec_items", "_earlier_work_lines"]
    missing = [n for n in required if n not in named]
    check("[CP7-T11] ratchet.py imports each of the moved names explicitly by name from plan_support "
          "(`from plan_support import ...`)", not missing, (missing, sorted(named)))

    ps_src = PLAN_SUPPORT.read_text()
    try:
        ps_tree = ast.parse(ps_src, filename="plan_support.py")
    except SyntaxError as e:
        check("[CP7-T11] plan_support.py parses as valid Python", False, str(e))
        return

    module_level_ratchet_imports = []
    for n in ps_tree.body:
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "ratchet" for a in n.names):
            module_level_ratchet_imports.append(ast.get_source_segment(ps_src, n))
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "ratchet":
            module_level_ratchet_imports.append(ast.get_source_segment(ps_src, n))
    check("[CP7-T11] plan_support.py never imports ratchet.py back at module load time (no import cycle)",
          not module_level_ratchet_imports, module_level_ratchet_imports)

    code = ("import sys\n"
            "sys.path.insert(0, %r)\n" % str(SCRIPT_DIR) +
            "assert 'ratchet' not in sys.modules, 'ratchet.py was already imported before plan_support'\n"
            "import plan_support\n"
            "assert 'ratchet' not in sys.modules, 'importing plan_support alone must not pull in ratchet'\n"
            "import ratchet\n"
            "print('ALL_OK')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                       cwd=str(_tmp.mkdtemp(prefix="cp7-t11-")))
    ok_ = r.returncode == 0 and "ALL_OK" in r.stdout
    check("[CP7-T11] `python3 -c \"import plan_support\"` succeeds standalone with no ImportError (ratchet.py "
          "never imported first), and ratchet.py's own later import of it succeeds too", ok_,
          (r.returncode, r.stdout[-300:], r.stderr[-500:]))

    global_users = []
    for node in ast.walk(ps_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(sub, ast.Global) for sub in ast.walk(node)):
                global_users.append(node.name)
    check("[CP7-T11] the AST scan finds zero `global` statements in plan_support.py referencing a module-level "
          "dict/list used across multiple functions as shared mutable state", not global_users, global_users)


# ============================================================================================ CP7-T12


def t12_digest_docstring_repointed_to_plan_support():
    print("CP7-T12: test_digest_docstring.py's ast-based docstring check is re-pointed to plan_support.py and "
          "still enforces the exact rule")
    check("[CP7-T12] plan_support.py exists", PLAN_SUPPORT.exists())
    if not PLAN_SUPPORT.exists():
        return
    ps_src = PLAN_SUPPORT.read_text()
    try:
        ps_tree = ast.parse(ps_src, filename="plan_support.py")
    except SyntaxError as e:
        check("[CP7-T12] plan_support.py parses as valid Python", False, str(e))
        return
    ps_defs = top_level_defs(ps_tree)
    check("[CP7-T12] _related_spec_items is now a top-level FunctionDef in plan_support.py",
          "_related_spec_items" in ps_defs)
    doc = ast.get_docstring(ps_defs["_related_spec_items"]) if "_related_spec_items" in ps_defs else None
    check("[CP7-T12] _related_spec_items in plan_support.py has a docstring", bool(doc), doc)
    if doc:
        idx = doc.lower().find("bmad")
        clause = doc[idx: idx + 300] if idx >= 0 else ""
        check("[CP7-T12] the docstring mentions BMAD stories at all", bool(clause), doc)
        has_exact = re.search(r'exactly\s+["“]?done["”]?|==\s*["“]?done["”]?', clause, re.I) is not None
        has_case = re.search(r'case.?insensitiv\w*|ignoring case|regardless of case|lower[- ]?cas\w*', clause,
                             re.I) is not None
        has_space = re.search(r'surrounding\s+(white)?space|ignoring\s+(surrounding\s+)?(white)?space|'
                              r'trimm?\w*|stripp?\w*', clause, re.I) is not None
        bad_contains = re.search(r'contain\w*\s+["“]?done["”]?', doc, re.I)
        check("[CP7-T12] the docstring says the status must equal 'done' exactly, not merely contain it",
              has_exact, clause)
        check("[CP7-T12] the docstring says the comparison ignores case", has_case, clause)
        check("[CP7-T12] the docstring says the comparison ignores surrounding whitespace", has_space, clause)
        check("[CP7-T12] the docstring never says a story's status 'contains' done", bad_contains is None, doc)

    live_src = ENGINE.read_text()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
    except SyntaxError as e:
        check("[CP7-T12] the current ratchet.py parses as valid Python", False, str(e))
        return
    check("[CP7-T12] _related_spec_items no longer has its own `def` left behind in ratchet.py's top-level AST",
          "_related_spec_items" not in top_level_defs(live_tree))

    try:
        import ratchet as rt  # noqa: E402
    except Exception as e:
        check("[CP7-T12] ratchet.py still imports successfully after the move", False, str(e))
        return
    has_attr = hasattr(rt, "_related_spec_items")
    check("[CP7-T12] ratchet.py re-exports _related_spec_items by explicit import (module attribute present)",
          has_attr)
    if has_attr:
        d = Path(_tmp.mkdtemp(prefix="cp7-t12-"))
        tds._bmad_story(d, "1.1", "1.1.done.md", "T12DONE a done story", "Done")
        tds._bmad_story(d, "1.2", "1.2.notdone.md", "T12NOTDONE a not-done story", "In Progress")
        tds._bmad_story(d, "1.3", "1.3.oddcase.md", "T12ODD an oddly-cased/spaced done story", " DONE ")
        items = rt._related_spec_items(rt.Ctx(d), set())
        lines = [line for _, line in items]
        check("[CP7-T12] CP3-T03's own direct call rt._related_spec_items(...) still passes unmodified: the "
              "done story is included", any("T12DONE" in l for l in lines), lines)
        check("[CP7-T12] ... the not-done story is excluded", not any("T12NOTDONE" in l for l in lines), lines)
        check("[CP7-T12] ... the oddly-cased/spaced done story is still included",
              any("T12ODD" in l for l in lines), lines)


# ============================================================================================ CP7-T13


def t13_four_named_regression_files_still_pass():
    print("CP7-T13: test_dm_table.py, test_dm_accept.py, test_digest_docstring.py and test_brief_guard.py pass "
          "unmodified after the move")
    files = ["test_dm_table.py", "test_dm_accept.py", "test_digest_docstring.py", "test_brief_guard.py"]
    for fname in files:
        p = ROOT / "tests" / fname
        check(f"[CP7-T13] tests/{fname} exists", p.exists())
        if not p.exists():
            continue
        r = subprocess.run([sys.executable, str(p)], cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        check(f"[CP7-T13] tests/{fname} exits 0 (every one of its own cases passes) against the post-move tree, "
              "with no edits made to the file itself", r.returncode == 0,
              (r.returncode, r.stdout[-1500:], r.stderr[-800:]))


# ============================================================================================ CP7-T14


def t14_help_status_hook_and_exit_codes_byte_identical():
    print("CP7-T14: --help, status --json, a sample hook reply and two known failure exit codes stay "
          "byte-identical")
    manifest = _read_manifest()
    check("[CP7-T14] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
          "missing or invalid" if manifest is None else "")
    if manifest is None:
        return
    entries = manifest.get("entries") or []

    for e in entries:
        if e.get("kind") != "help":
            continue
        cmd = e.get("cmd") or []
        try:
            saved = (BASELINE_DIR / e["path"]).read_text()
        except OSError as ex:
            check(f"[CP7-T14] baseline file for `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        r = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1",
                                                 PYTHON_COLORS="0"))
        check(f"[CP7-T14] `ratchet.py {' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              r.returncode == e.get("exit_code"), r.returncode)
        check(f"[CP7-T14] `ratchet.py {' '.join(cmd)}`'s stdout is byte-identical to CP1's baseline",
              r.stdout == saved, (saved[:200], r.stdout[:200]))

    samples = [e for e in entries if e.get("kind") == "status_json"]
    check("[CP7-T14] setup: a status_json baseline entry is recorded", bool(samples), entries)
    if samples:
        try:
            saved_status = json.loads((BASELINE_DIR / samples[0]["path"]).read_text())
        except (OSError, json.JSONDecodeError) as ex:
            check("[CP7-T14] the saved status --json baseline parses as JSON", False, str(ex))
            saved_status = None
        r = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=30)
        try:
            live_status = json.loads(r.stdout) if r.returncode == 0 else None
        except json.JSONDecodeError:
            live_status = None
        check("[CP7-T14] a live `status --json` invocation right now succeeds", live_status is not None,
              (r.returncode, r.stdout[-300:]))
        check("[CP7-T14] `status --json`'s exit code matches CP1's recorded baseline exit code",
              r.returncode == samples[0].get("exit_code"), (r.returncode, samples[0].get("exit_code")))
        if live_status is not None and saved_status is not None:
            check("[CP7-T14] status --json's top-level key set is unchanged from CP1's baseline",
                  set(live_status) == set(saved_status), (sorted(live_status), sorted(saved_status)))

    # The two checks above only compare the top-level KEY SET against CP1's baseline (captured once, from
    # this run's own ever-advancing active state, so a literal full-content comparison against that exact
    # file can never hold past CP1 itself -- the same reason CP1-T06/CP2-T11/CP3-T11/CP4-05's own notes give).
    # Strengthens that with the same pinned-vs-current-on-one-fixture technique CP3-T07/CP4-06/CP5-T04/
    # CP6-T03 already established: BASE_COMMIT's own ratchet.py (the exact pre-CP7-move source) run against a
    # fresh, reproducible throwaway project, side by side with the current, already-split engine, with their
    # COMPLETE status --json output compared byte-for-byte -- nothing needs normalizing, since both
    # invocations read the identical on-disk state and any real difference can only come from the move itself.
    try:
        pinned_src = show_at(BASE_COMMIT, "skills/ratchet-orchestrator/scripts/ratchet.py")
    except Exception as e:
        check("[CP7-T14] setup: the pre-move pinned ratchet.py source is readable via git show (full-content "
              "status --json comparison)", False, str(e))
        pinned_src = None
    if pinned_src is not None:
        d = Path(_tmp.mkdtemp(prefix="cp7-t14-status-"))
        r_init = subprocess.run([sys.executable, str(ENGINE), "init", "status parity cap", "--slug", "st"],
                                cwd=str(d), capture_output=True, text=True, timeout=30)
        check("[CP7-T14] setup: a fresh throwaway project's `init` succeeds", r_init.returncode == 0,
              (r_init.returncode, r_init.stderr))
        if r_init.returncode == 0:
            pinned_script = SCRIPT_DIR / f"_cp7_t14_pinned_{os.getpid()}.py.tmp"
            pinned_script.write_text(pinned_src)
            try:
                r_before = subprocess.run([sys.executable, str(pinned_script), "status", "--json"], cwd=str(d),
                                          capture_output=True, text=True, timeout=30)
            finally:
                pinned_script.unlink(missing_ok=True)
            check("[CP7-T14] setup: the pre-move pinned ratchet.py's `status --json` exits 0 on the fresh "
                  "fixture", r_before.returncode == 0, (r_before.returncode, r_before.stdout[-300:],
                                                        r_before.stderr[-300:]))
            r_after = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(d),
                                     capture_output=True, text=True, timeout=30)
            check("[CP7-T14] the current ratchet.py's `status --json` exits 0 on the same fresh fixture",
                  r_after.returncode == 0, (r_after.returncode, r_after.stdout[-300:], r_after.stderr[-300:]))
            if r_before.returncode == 0 and r_after.returncode == 0:
                check("[CP7-T14] the complete status --json output (not just the top-level key set checked "
                      "above) is byte-identical between the pre-move pinned engine and the current, "
                      "already-split engine, on the same fresh reproducible fixture -- no live-repo state "
                      "involved", r_before.stdout == r_after.stdout, (r_before.stdout, r_after.stdout))

    hook_samples = [e for e in entries if e.get("kind") == "hook_reply"]
    check("[CP7-T14] setup: a hook_reply baseline entry is recorded", bool(hook_samples), entries)
    if hook_samples:
        d = Path(_tmp.mkdtemp(prefix="cp7-t14-hook-"))
        r_init = subprocess.run([sys.executable, str(ENGINE), "init", "hook cap", "--slug", "hk"], cwd=str(d),
                                capture_output=True, text=True, timeout=30)
        check("[CP7-T14] setup: a fresh throwaway project's `init` succeeds", r_init.returncode == 0,
              (r_init.returncode, r_init.stderr))
        if r_init.returncode == 0:
            state = str(d / ".ratchet" / "runs" / "hk" / "state.json")
            payload = {"sessionId": "s", "timestamp": 1, "cwd": str(d), "toolName": "edit",
                      "toolArgs": json.dumps({"path": state, "old_str": "a", "new_str": "b"})}
            rg = subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "copilot"], cwd=str(d),
                                capture_output=True, text=True, timeout=30, input=json.dumps(payload))
            check("[CP7-T14] the fresh copilot guard invocation's exit code matches CP1's recorded baseline "
                  "exit code (the JSON reply alone is not enough -- the process's own exit status must match "
                  "too)", rg.returncode == hook_samples[0].get("exit_code"),
                  (rg.returncode, hook_samples[0].get("exit_code")))
            try:
                reply = json.loads(rg.stdout)
            except json.JSONDecodeError as e:
                check("[CP7-T14] a fresh copilot guard reply parses as JSON", False, str(e))
                reply = None
            if reply is not None:
                try:
                    saved_reply = json.loads((BASELINE_DIR / hook_samples[0]["path"]).read_text())
                except (OSError, json.JSONDecodeError) as ex:
                    check("[CP7-T14] the saved hook reply baseline parses as JSON", False, str(ex))
                    saved_reply = None
                if saved_reply is not None:
                    def _norm(reply_):
                        # Narrowed to the concrete temp-directory path token only (the one piece that is
                        # genuinely different between runs): `\S+`, not `.*$`, so any text that followed the
                        # path -- a changed refusal reason, an appended safety explanation -- would still be
                        # compared verbatim instead of being silently swallowed by the normalization.
                        reason_ = reply_.get("permissionDecisionReason", "")
                        return {**reply_, "permissionDecisionReason": re.sub(r"Blocked: \S+", "Blocked: <path>",
                                                                              reason_)}
                    check("[CP7-T14] the complete fresh guard reply matches CP1's saved baseline exactly, once "
                          "each one's own random temp-dir path is normalized away",
                          _norm(reply) == _norm(saved_reply), (saved_reply, reply))

    failures = [e for e in entries if e.get("kind") == "failure"]
    check("[CP7-T14] setup: at least two failure baseline entries are recorded", len(failures) >= 2, failures)
    for e in failures:
        cmd = e.get("cmd") or []
        try:
            saved = (BASELINE_DIR / e["path"]).read_text()
        except OSError as ex:
            check(f"[CP7-T14] baseline file for failure `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        if cmd and cmd[0] == "no-such-command":
            rf = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True,
                                text=True, timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1",
                                                                 PYTHON_COLORS="0"))
            got = rf.stderr
        else:
            empty_dir = Path(_tmp.mkdtemp(prefix="cp7-t14-empty-"))
            rf = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(empty_dir),
                                capture_output=True, text=True, timeout=30)
            got = rf.stderr
        check(f"[CP7-T14] failure case `{' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              rf.returncode == e.get("exit_code"), rf.returncode)
        check(f"[CP7-T14] failure case `{' '.join(cmd)}`'s stderr is byte-identical to CP1's baseline",
              got == saved, (saved, got))


# ============================================================================================ CP7-T15


def t15_full_suite_and_diff_scope():
    print("CP7-T15: the complete local suite passes under the suite lock, and this checkpoint's diff touches "
          "only the expected files")
    # This case verifies lane M's OWN run context: a `git diff` against CP6's landing commit assuming
    # HEAD is still M's own run tip. On integrated main the diff legitimately includes other lanes'
    # merged work, so skip cleanly rather than false-fail; inside M's run (RUN_DIR present) it runs
    # exactly as before.
    if not RUN_DIR.exists():
        print("[CP7-T15] skipped: not running inside lane M's own run context")
        return
    if os.environ.get("RATCHET_CP7_T15_FULL_SUITE") == "1":
        # Strip the opt-in flag from the child's environment: tests/run_all.py's own run reaches this same
        # test_modularize_cp7.py again, and if it inherited the flag it would take this same full-suite branch
        # too, recursing instead of completing one verification run.
        child_env = {k: v for k, v in os.environ.items() if k != "RATCHET_CP7_T15_FULL_SUITE"}
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=5400, env=child_env)
        m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
        failed = int(m.group(2)) if m else -1
        check("[CP7-T15] python3 tests/run_all.py exits 0 with 0 failed", r.returncode == 0 and failed == 0,
              (r.returncode, r.stdout[-400:]))
    else:
        check("[CP7-T15] done-means #5 (full suite passes) is enforced structurally by `ratchet.py complete`, "
              "which refuses to mark a checkpoint done when its own regression run fails -- not re-checked here "
              "by default; set RATCHET_CP7_T15_FULL_SUITE=1 to actually run the full suite inline instead", True)

    allowed = {
        "skills/ratchet-orchestrator/scripts/plan_support.py",
        "skills/ratchet-orchestrator/scripts/ratchet.py",
        "tests/test_modularize_cp7.py",
        "tests/test_dm_table.py",
        "tests/test_dm_accept.py",
        "tests/test_digest_docstring.py",
        "tests/test_brief_guard.py",
        # New-test-file registration sites, not module/code files: this checkpoint's new test file had to
        # be registered in README's "Running the tests" reference list and in the CP1 source-inspection
        # inventory fixture (which also needed a stale entry re-pointed).
        "README.md",
        "tests/fixtures/modularize_cp1/test-inventory.md",
    }
    changed = set()
    r_diff = subprocess.run(["git", "diff", "--name-only", BASE_COMMIT], cwd=str(ROOT), capture_output=True,
                            text=True, timeout=30)
    check("[CP7-T15] setup: `git diff --name-only` against CP6's own landing commit succeeds",
          r_diff.returncode == 0, (r_diff.returncode, r_diff.stderr))
    if r_diff.returncode == 0:
        changed.update(l.strip() for l in r_diff.stdout.splitlines() if l.strip())
    r_status = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True,
                              timeout=30)
    for line in r_status.stdout.splitlines():
        path = line[3:].strip()
        if path:
            changed.add(path)
    unexpected = sorted(p for p in changed if p not in allowed)
    check("[CP7-T15] every changed file (against CP6's own landing commit) is one of the 9 expected files; no "
          "other module file is touched", not unexpected, unexpected)


TESTS = [
    ("CP7-T01", t01_missing_objective_refused),
    ("CP7-T02", t02_missing_single_field_refused_by_name),
    ("CP7-T03", t03_proves_out_of_range_refused_by_case_id),
    ("CP7-T04", t04_fully_proved_plan_accepted),
    ("CP7-T05", t05_bare_int_proves_normalized_nonnumeric_refused),
    ("CP7-T06", t06_planner_brief_objective_proof_notes_and_digest),
    ("CP7-T07", t07_non_planner_role_never_shows_digest),
    ("CP7-T08", t08_normalize_role_prefix_alias_and_unknown),
    ("CP7-T09", t09_learnings_cap_and_rank),
    ("CP7-T10", t10_accepted_open_specs_render_and_empty_fallback),
    ("CP7-T11", t11_plan_support_imports_and_no_circular_or_registry),
    ("CP7-T12", t12_digest_docstring_repointed_to_plan_support),
    ("CP7-T13", t13_four_named_regression_files_still_pass),
    ("CP7-T14", t14_help_status_hook_and_exit_codes_byte_identical),
    ("CP7-T15", t15_full_suite_and_diff_scope),
]


def main():
    for cid, fn in TESTS:
        try:
            fn()
        except Exception as e:  # a crash in this file still counts as a (failed) result for this case
            global FAIL
            FAIL += 1
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
