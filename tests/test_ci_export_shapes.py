#!/usr/bin/env python3
"""
CP4 tests: skip past runs with damaged records.   python3 tests/test_ci_export_shapes.py

Drives `ratchet.py ci-export --stdout` as a real subprocess against throwaway temp git projects (the same
`project`/`rx` subprocess pattern tests/test_ci_export.py uses), each with its own fake .ratchet/runs/<slug>/
folders. Where tests/test_ci_export.py's `add_fake_run` only ever writes the well-formed shape ratchet.py
plan-submit itself would write, the cases here hand-write a run's state.json or checkpoints.json with a
wrong-shaped "checkpoints" value (or one wrong-shaped entry/item inside it) so the saved value has the wrong
JSON shape, then assert the process still exits 0, prints no traceback, still prints a good run's own step
command, and names the damaged run's file in a one-line plain stderr note.

This file stands alone: every helper it needs (project, set_cfg, add_fake_run, check, the json report
pattern) is copied here from tests/test_ci_export.py rather than imported from it, per this checkpoint's brief.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP4-T01", "status": "passed"|"failed"|"error"|"skipped", "message": "..."}]}.
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
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
ENV = {k: v for k, v in os.environ.items() if k not in ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT")}
ENV["RATCHET_HOME"] = str(_tmp.mkdtemp(prefix="ratchet-ci-shapes-home-"))
ENV["RATCHET_HOST_PROVIDER"] = "anthropic"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
CASE_CHECKS: dict[str, int] = {}
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP4-[TF]\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def rx(d, *args):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True, env=ENV)


def project(name, config=None, files=None) -> Path:
    """A fresh throwaway git repo, committed, with a .ratchet/config.json written only through
    `ratchet.py config --set` (never hand-edited), per standard 2/24."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-ci-shapes-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    for rel, text in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    if config:
        r = rx(d, "config", "--set", *config)
        assert r.returncode == 0, r.stderr + r.stdout
    return d


def set_cfg(key, value) -> str:
    """One "key=<json>" entry for `config --set`."""
    return f"{key}={json.dumps(value)}"


def add_fake_run(d: Path, slug: str, cp_id: str, test_command: str, created_at: str, title="step") -> None:
    """A completed prior run: .ratchet/runs/<slug>/state.json marks `cp_id` done, and the run's own
    checkpoints.json gives that checkpoint's tests.command, the same well-formed shape ratchet.py
    plan-submit writes."""
    run_dir = d / ".ratchet" / "runs" / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"slug": slug, "feature": slug, "phase": "done", "created_at": created_at,
              "checkpoints": {cp_id: {"status": "done"}}}
    (run_dir / "state.json").write_text(json.dumps(state))
    plan = {"feature": slug, "objective": _tmp.OBJECTIVE_MIN,
            "checkpoints": [{"id": cp_id, "title": title, "summary": "s", "done_when": ["works"],
                             "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
                             "tests": {"files": [f"tests/{cp_id.lower()}.test.js"], "command": test_command}}]}
    (run_dir / "checkpoints.json").write_text(json.dumps(plan))
    (run_dir / "tests").mkdir(exist_ok=True)  # CP2: the one done_means line is linked to a case, like a real plan
    (run_dir / "tests" / f"{cp_id}.json").write_text(
        json.dumps({"cases": [{"id": f"{cp_id}-T01", "layer": "code", "proves": [1]}]}))


def cp_item(cp_id: str, command: str, title="step", tests_value=None) -> dict:
    """One well-formed checkpoints.json checkpoint item, the same shape add_fake_run writes; `tests_value`
    overrides the whole "tests" value with a deliberately wrong shape when given."""
    item = {"id": cp_id, "title": title, "summary": "s", "done_when": ["works"],
            "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
            "tests": {"files": [f"tests/{cp_id.lower()}.test.js"], "command": command}}
    if tests_value is not None:
        item["tests"] = tests_value
    return item


def write_run(d: Path, slug: str, state: dict, plan: dict) -> Path:
    """Writes a run folder's state.json/checkpoints.json exactly as given, with no shape validation --
    used to build the deliberately damaged fixtures this checkpoint's cases need."""
    run_dir = d / ".ratchet" / "runs" / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "checkpoints.json").write_text(json.dumps(plan))
    return run_dir


CHECK1 = {"kind": "lint-no-console", "title": "No console.log left in", "command": "true # check-one",
          "added": "2026-01-01", "from": "x/CP4"}

NO_TRACEBACK = "Traceback (most recent call last)"


# ------------------------------------------------------------------------------------------------ cases

def t01_state_checkpoints_list_is_skipped():
    d = project("t01", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(d, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    state = {"slug": "bad-run", "feature": "bad-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00", "checkpoints": ["CP1"]}
    plan = {"feature": "bad-run", "objective": _tmp.OBJECTIVE_MIN,
            "checkpoints": [cp_item("CP1", "node --test tests/bad.test.js")]}
    run_dir = write_run(d, "bad-run", state, plan)
    r = rx(d, "ci-export", "--stdout")
    check("[CP4-T01] ci-export --stdout exits 0 when a run's state.json \"checkpoints\" is a list",
          r.returncode == 0, r.returncode)
    check("[CP4-T01] no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (r.stdout + r.stderr), (r.stdout + r.stderr)[:600])
    check("[CP4-T01] the good run's own step command still appears in the printed workflow",
          "good.test.js" in r.stdout, r.stdout)
    check("[CP4-T01] stderr carries a one-line plain note naming the damaged run's state.json",
          "bad-run" in r.stderr and "state.json" in r.stderr, r.stderr)


def t02_one_malformed_checkpoint_entry_is_skipped():
    d = project("t02", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(d, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    state = {"slug": "bad-run", "feature": "bad-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": {"status": "done"}, "CP2": "done"}}
    plan = {"feature": "bad-run", "objective": _tmp.OBJECTIVE_MIN,
            "checkpoints": [cp_item("CP1", "node --test tests/bad-good.test.js"),
                             cp_item("CP2", "node --test tests/bad-bad.test.js")]}
    write_run(d, "bad-run", state, plan)
    r = rx(d, "ci-export", "--stdout")
    check("[CP4-T02] ci-export --stdout exits 0 with one malformed checkpoint entry in state.json",
          r.returncode == 0, r.returncode)
    check("[CP4-T02] no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (r.stdout + r.stderr), (r.stdout + r.stderr)[:600])
    check("[CP4-T02] the run's other, well-formed checkpoint's tests.command still appears",
          "bad-good.test.js" in r.stdout, r.stdout)
    check("[CP4-T02] the malformed checkpoint's own tests.command is left out",
          "bad-bad.test.js" not in r.stdout, r.stdout)
    check("[CP4-T02] the good run's own step command still appears in the printed workflow",
          "good.test.js" in r.stdout, r.stdout)
    check("[CP4-T02] stderr carries a one-line plain note naming the skipped checkpoint or its run",
          "CP2" in r.stderr or "bad-run" in r.stderr, r.stderr)


def t03_checkpoints_json_checkpoints_object_is_skipped():
    d = project("t03", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(d, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    state = {"slug": "bad-run", "feature": "bad-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00", "checkpoints": {"CP1": {"status": "done"}}}
    plan = {"feature": "bad-run", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": {"id": "CP1"}}
    write_run(d, "bad-run", state, plan)
    r = rx(d, "ci-export", "--stdout")
    check("[CP4-T03] ci-export --stdout exits 0 when a run's checkpoints.json \"checkpoints\" is an object",
          r.returncode == 0, r.returncode)
    check("[CP4-T03] no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (r.stdout + r.stderr), (r.stdout + r.stderr)[:600])
    check("[CP4-T03] the good run's own step command still appears in the printed workflow",
          "good.test.js" in r.stdout, r.stdout)
    check("[CP4-T03] stderr carries a one-line plain note naming the damaged run's checkpoints.json",
          "bad-run" in r.stderr and "checkpoints.json" in r.stderr, r.stderr)


def t04_malformed_item_or_tests_is_skipped():
    # project D: one item's "tests" value is a JSON list instead of an object holding "command"
    dD = project("t04d", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dD, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateD = {"slug": "mixed-run", "feature": "mixed-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": {"status": "done"}, "CP2": {"status": "done"}}}
    planD = {"feature": "mixed-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/mixed-good.test.js"),
                              cp_item("CP2", "", tests_value=["tests/x.test.js"])]}
    write_run(dD, "mixed-run", stateD, planD)
    rD = rx(dD, "ci-export", "--stdout")
    check("[CP4-T04] project D: ci-export --stdout exits 0 when one item's \"tests\" value is a list",
          rD.returncode == 0, rD.returncode)
    check("[CP4-T04] project D: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rD.stdout + rD.stderr), (rD.stdout + rD.stderr)[:600])
    check("[CP4-T04] project D: the run's other, well-formed item's tests.command still appears",
          "mixed-good.test.js" in rD.stdout, rD.stdout)
    check("[CP4-T04] project D: the good run's own step command still appears",
          "good.test.js" in rD.stdout, rD.stdout)
    check("[CP4-T04] project D: stderr carries a one-line plain note naming the skipped item or its run",
          "mixed-run" in rD.stderr or "CP2" in rD.stderr, rD.stderr)

    # project E: one item in the "checkpoints" list is a bare string, not a dict at all
    dE = project("t04e", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dE, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateE = {"slug": "mixed-run", "feature": "mixed-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": {"status": "done"}, "CP2": {"status": "done"}}}
    planE = {"feature": "mixed-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/mixed-good.test.js"), "CP2"]}
    write_run(dE, "mixed-run", stateE, planE)
    rE = rx(dE, "ci-export", "--stdout")
    check("[CP4-T04] project E: ci-export --stdout exits 0 when one checkpoints.json item is a bare string",
          rE.returncode == 0, rE.returncode)
    check("[CP4-T04] project E: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rE.stdout + rE.stderr), (rE.stdout + rE.stderr)[:600])
    check("[CP4-T04] project E: the run's other, well-formed item's tests.command still appears",
          "mixed-good.test.js" in rE.stdout, rE.stdout)
    check("[CP4-T04] project E: the good run's own step command still appears",
          "good.test.js" in rE.stdout, rE.stdout)
    check("[CP4-T04] project E: stderr carries a one-line plain note naming the skipped item or its run",
          "mixed-run" in rE.stderr or "CP2" in rE.stderr, rE.stderr)


def t05_well_formed_runs_export_unchanged():
    d = project("t05", config=[set_cfg("checks", [CHECK1])])
    # created_at timestamps establish run order; slugs are deliberately NOT alphabetical in that same
    # order, matching tests/test_ci_export.py's CP1-T07 fixture exactly.
    add_fake_run(d, "zz-older-run", "CP1", "node --test tests/older.test.js", "2026-01-01T00:00:00+00:00")
    add_fake_run(d, "aa-newer-run", "CP1", "node --test tests/newer.test.js", "2026-02-01T00:00:00+00:00")
    r = rx(d, "ci-export", "--stdout")
    check("[CP4-T05] ci-export --stdout exits 0 with only well-formed prior runs",
          r.returncode == 0, r.stderr + r.stdout)
    i_chk = r.stdout.find("true # check-one")
    i_older = r.stdout.find("older.test.js")
    i_newer = r.stdout.find("newer.test.js")
    check("[CP4-T05] the check's step is present", i_chk != -1, r.stdout)
    check("[CP4-T05] both prior runs' tests.command are present", i_older != -1 and i_newer != -1, r.stdout)
    check("[CP4-T05] the check's step comes before either prior run's step",
          i_chk != -1 and i_older != -1 and i_newer != -1 and i_chk < i_older and i_chk < i_newer,
          (i_chk, i_older, i_newer))
    check("[CP4-T05] the older run's step comes before the newer run's step (run order)",
          i_older != -1 and i_newer != -1 and i_older < i_newer, (i_older, i_newer))
    check("[CP4-T05] stderr carries no skip note at all for well-formed runs",
          r.stderr.strip() == "", r.stderr)


TESTS = [
    ("CP4-T01", t01_state_checkpoints_list_is_skipped),
    ("CP4-T02", t02_one_malformed_checkpoint_entry_is_skipped),
    ("CP4-T03", t03_checkpoints_json_checkpoints_object_is_skipped),
    ("CP4-T04", t04_malformed_item_or_tests_is_skipped),
    ("CP4-T05", t05_well_formed_runs_export_unchanged),
]


# ------------------------------------------------------------------------------------------------ CP4-F01

EXPECTED_F01_WORKFLOW = (
    "# Generated by Ratchet (ratchet.py ci-export)\n"
    "#\n"
    "# This workflow runs the mechanical checks this project's Ratchet run relied on: the regression\n"
    "# command (or, lacking one, completed steps' own test commands) and every automatic check saved\n"
    "# from a guardrail step.\n"
    "#\n"
    "# It does not run Ratchet's AI review gates (adversarial review, UI review, human review). CI\n"
    "# machines don't have the agent CLIs and credentials those gates need, so those gates stay a\n"
    "# step a person or an agent runs, not a CI job.\n"
    "#\n"
    "# To regenerate this file, ask your agent to run Ratchet's CI export; don't edit it by hand.\n"
    "name: Ratchet checks\n"
    "on:\n"
    "  push:\n"
    "  pull_request:\n"
    "jobs:\n"
    "  ratchet:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - name: \"No console.log left in\"\n"
    "        timeout-minutes: 15\n"
    "        run: |\n"
    "          true # check-one\n"
    "      - name: \"zz-older-run CP1 tests\"\n"
    "        timeout-minutes: 15\n"
    "        run: |\n"
    "          node --test tests/older.test.js\n"
    "      - name: \"aa-newer-run CP1 tests\"\n"
    "        timeout-minutes: 15\n"
    "        run: |\n"
    "          node --test tests/newer.test.js\n"
)


def f01_well_formed_export_matches_exact_expected_text():
    """[CP4-F01] The full `ci-export --stdout` output for a fixed, well-formed fixture (a saved check plus two
    completed prior runs, the same fixture t05 uses) must match an exact expected text byte for byte, not just
    contain the right commands in the right relative order: the promised "exactly the same file as before this
    change" covers triggers, the job shape and every `run:` block scalar, not only command presence. A second,
    independent project built from the identical fixture must render the identical bytes too, proving the match
    isn't an accident of one run."""
    d1 = project("f01a", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(d1, "zz-older-run", "CP1", "node --test tests/older.test.js", "2026-01-01T00:00:00+00:00")
    add_fake_run(d1, "aa-newer-run", "CP1", "node --test tests/newer.test.js", "2026-02-01T00:00:00+00:00")
    r1 = rx(d1, "ci-export", "--stdout")
    check("[CP4-F01] exit 0 for the fixed fixture", r1.returncode == 0, r1.stderr + r1.stdout)
    check("[CP4-F01] the full stdout matches the exact expected workflow text byte for byte",
          r1.stdout == EXPECTED_F01_WORKFLOW, r1.stdout)
    check("[CP4-F01] triggers run on both push and pull_request",
          "on:\n  push:\n  pull_request:\n" in r1.stdout, r1.stdout)
    check("[CP4-F01] each command is its own run: block scalar",
          r1.stdout.count("        run: |\n") == 3, r1.stdout)

    d2 = project("f01b", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(d2, "zz-older-run", "CP1", "node --test tests/older.test.js", "2026-01-01T00:00:00+00:00")
    add_fake_run(d2, "aa-newer-run", "CP1", "node --test tests/newer.test.js", "2026-02-01T00:00:00+00:00")
    r2 = rx(d2, "ci-export", "--stdout")
    check("[CP4-F01] a second, independent project built from the identical fixture renders identical bytes",
          r2.stdout == r1.stdout, (r1.stdout, r2.stdout))


TESTS.append(("CP4-F01", f01_well_formed_export_matches_exact_expected_text))


# ------------------------------------------------------------------------------------------------ CP4-F02

def f02_non_string_id_is_skipped_not_a_crash():
    """[CP4-F02] A checkpoints.json item whose "id" is a list or an object must not crash ci-export:
    done.get(cp_id) would raise TypeError (unhashable type) if an unvalidated id reached it. Each case exits
    0, prints no traceback, still exports the run's other well-formed item and a separate good run's own
    step, and names the damaged checkpoints.json on stderr."""
    # a list id
    dF = project("f02f", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dF, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateF = {"slug": "mixed-run", "feature": "mixed-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": {"status": "done"}, "CP2": {"status": "done"}}}
    bad_list_item = cp_item("CP2", "node --test tests/mixed-bad.test.js")
    bad_list_item["id"] = ["CP2"]
    planF = {"feature": "mixed-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/mixed-good.test.js"), bad_list_item]}
    write_run(dF, "mixed-run", stateF, planF)
    rF = rx(dF, "ci-export", "--stdout")
    check("[CP4-F02] list id: ci-export --stdout exits 0", rF.returncode == 0, rF.returncode)
    check("[CP4-F02] list id: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rF.stdout + rF.stderr), (rF.stdout + rF.stderr)[:600])
    check("[CP4-F02] list id: the run's other, well-formed item's tests.command still appears",
          "mixed-good.test.js" in rF.stdout, rF.stdout)
    check("[CP4-F02] list id: the separate good run's own step command still appears",
          "good.test.js" in rF.stdout, rF.stdout)
    check("[CP4-F02] list id: stderr names the damaged checkpoints.json",
          "mixed-run" in rF.stderr and "checkpoints.json" in rF.stderr, rF.stderr)

    # an object id
    dG = project("f02g", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dG, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateG = {"slug": "mixed-run", "feature": "mixed-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": {"status": "done"}, "CP2": {"status": "done"}}}
    bad_obj_item = cp_item("CP2", "node --test tests/mixed-bad.test.js")
    bad_obj_item["id"] = {"x": 1}
    planG = {"feature": "mixed-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/mixed-good.test.js"), bad_obj_item]}
    write_run(dG, "mixed-run", stateG, planG)
    rG = rx(dG, "ci-export", "--stdout")
    check("[CP4-F02] object id: ci-export --stdout exits 0", rG.returncode == 0, rG.returncode)
    check("[CP4-F02] object id: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rG.stdout + rG.stderr), (rG.stdout + rG.stderr)[:600])
    check("[CP4-F02] object id: the run's other, well-formed item's tests.command still appears",
          "mixed-good.test.js" in rG.stdout, rG.stdout)
    check("[CP4-F02] object id: the separate good run's own step command still appears",
          "good.test.js" in rG.stdout, rG.stdout)
    check("[CP4-F02] object id: stderr names the damaged checkpoints.json",
          "mixed-run" in rG.stderr and "checkpoints.json" in rG.stderr, rG.stderr)


TESTS.append(("CP4-F02", f02_non_string_id_is_skipped_not_a_crash))


# ------------------------------------------------------------------------------------------------ CP4-F03

def f03_null_value_present_key_is_bad_shape_not_silent_skip():
    """[CP4-F03] A key that is present but holds JSON null must be treated as the wrong shape (its own
    stderr note), never the same as the key being absent (which stays silent): checkpoints.json's
    "checkpoints": null, a state.json checkpoint entry "CP1": null, and an item's "tests": null. Each case
    exits 0, prints no traceback, still exports the run's other well-formed checkpoint and a separate good
    run's own step, and names the damaged file on stderr."""
    # checkpoints.json "checkpoints": null
    dA = project("f03a", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dA, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateA = {"slug": "null-plan-run", "feature": "null-plan-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": {"status": "done"}}}
    planA = {"feature": "null-plan-run", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": None}
    write_run(dA, "null-plan-run", stateA, planA)
    rA = rx(dA, "ci-export", "--stdout")
    check("[CP4-F03] null checkpoints.json \"checkpoints\": ci-export --stdout exits 0",
          rA.returncode == 0, rA.returncode)
    check("[CP4-F03] null checkpoints.json \"checkpoints\": no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rA.stdout + rA.stderr), (rA.stdout + rA.stderr)[:600])
    check("[CP4-F03] null checkpoints.json \"checkpoints\": the separate good run's own step command still appears",
          "good.test.js" in rA.stdout, rA.stdout)
    check("[CP4-F03] null checkpoints.json \"checkpoints\": stderr names the damaged checkpoints.json",
          "null-plan-run" in rA.stderr and "checkpoints.json" in rA.stderr, rA.stderr)

    # a state.json checkpoint entry "CP1": null
    dB = project("f03b", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dB, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateB = {"slug": "null-entry-run", "feature": "null-entry-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": None, "CP2": {"status": "done"}}}
    planB = {"feature": "null-entry-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/null-entry-bad.test.js"),
                             cp_item("CP2", "node --test tests/null-entry-good.test.js")]}
    write_run(dB, "null-entry-run", stateB, planB)
    rB = rx(dB, "ci-export", "--stdout")
    check("[CP4-F03] null state.json checkpoint entry: ci-export --stdout exits 0",
          rB.returncode == 0, rB.returncode)
    check("[CP4-F03] null state.json checkpoint entry: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rB.stdout + rB.stderr), (rB.stdout + rB.stderr)[:600])
    check("[CP4-F03] null state.json checkpoint entry: the other, well-formed checkpoint's tests.command "
          "still appears", "null-entry-good.test.js" in rB.stdout, rB.stdout)
    check("[CP4-F03] null state.json checkpoint entry: the null entry's own tests.command is left out",
          "null-entry-bad.test.js" not in rB.stdout, rB.stdout)
    check("[CP4-F03] null state.json checkpoint entry: the separate good run's own step command still appears",
          "good.test.js" in rB.stdout, rB.stdout)
    check("[CP4-F03] null state.json checkpoint entry: stderr names the damaged state.json",
          "null-entry-run" in rB.stderr and "state.json" in rB.stderr, rB.stderr)

    # an item's "tests": null
    dC = project("f03c", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dC, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateC = {"slug": "null-tests-run", "feature": "null-tests-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00",
              "checkpoints": {"CP1": {"status": "done"}, "CP2": {"status": "done"}}}
    item_null_tests = cp_item("CP2", "node --test tests/null-tests-bad.test.js")
    item_null_tests["tests"] = None
    planC = {"feature": "null-tests-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/null-tests-good.test.js"), item_null_tests]}
    write_run(dC, "null-tests-run", stateC, planC)
    rC = rx(dC, "ci-export", "--stdout")
    check("[CP4-F03] null \"tests\" value: ci-export --stdout exits 0", rC.returncode == 0, rC.returncode)
    check("[CP4-F03] null \"tests\" value: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rC.stdout + rC.stderr), (rC.stdout + rC.stderr)[:600])
    check("[CP4-F03] null \"tests\" value: the other, well-formed checkpoint's tests.command still appears",
          "null-tests-good.test.js" in rC.stdout, rC.stdout)
    check("[CP4-F03] null \"tests\" value: the null-tests checkpoint's own command is left out",
          "null-tests-bad.test.js" not in rC.stdout, rC.stdout)
    check("[CP4-F03] null \"tests\" value: the separate good run's own step command still appears",
          "good.test.js" in rC.stdout, rC.stdout)
    check("[CP4-F03] null \"tests\" value: stderr names the damaged checkpoints.json",
          "null-tests-run" in rC.stderr and "checkpoints.json" in rC.stderr, rC.stderr)


TESTS.append(("CP4-F03", f03_null_value_present_key_is_bad_shape_not_silent_skip))


# ------------------------------------------------------------------------------------------------ CP4-F04

def f04_absent_state_checkpoints_key_is_silent_not_a_bad_shape_note():
    """[CP4-F04] A state.json with no "checkpoints" key at all must be treated like the plan's absent
    "checkpoints" key already is: silently, with no wrong-shape note, since the key being present but
    holding the wrong shape (or null) is the only case that earns one. A separate run whose state.json
    "checkpoints" is present but null must still get the note."""
    # absent "checkpoints" key entirely: silent, exit 0, no note for this run's state.json
    dA = project("f04a", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dA, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateA = {"slug": "no-checkpoints-key-run", "feature": "no-checkpoints-key-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00"}
    planA = {"feature": "no-checkpoints-key-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/no-checkpoints-key.test.js")]}
    write_run(dA, "no-checkpoints-key-run", stateA, planA)
    rA = rx(dA, "ci-export", "--stdout")
    check("[CP4-F04] absent \"checkpoints\" key: ci-export --stdout exits 0", rA.returncode == 0, rA.returncode)
    check("[CP4-F04] absent \"checkpoints\" key: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rA.stdout + rA.stderr), (rA.stdout + rA.stderr)[:600])
    check("[CP4-F04] absent \"checkpoints\" key: the separate good run's own step command still appears",
          "good.test.js" in rA.stdout, rA.stdout)
    check("[CP4-F04] absent \"checkpoints\" key: stderr carries no wrong-shape note for this run's state.json",
          "no-checkpoints-key-run" not in rA.stderr, rA.stderr)

    # present but null "checkpoints" key: still gets the note
    dB = project("f04b", config=[set_cfg("checks", [CHECK1])])
    add_fake_run(dB, "good-run", "CP1", "node --test tests/good.test.js", "2026-01-01T00:00:00+00:00")
    stateB = {"slug": "null-checkpoints-key-run", "feature": "null-checkpoints-key-run", "phase": "done",
              "created_at": "2026-01-02T00:00:00+00:00", "checkpoints": None}
    planB = {"feature": "null-checkpoints-key-run", "objective": _tmp.OBJECTIVE_MIN,
             "checkpoints": [cp_item("CP1", "node --test tests/null-checkpoints-key.test.js")]}
    write_run(dB, "null-checkpoints-key-run", stateB, planB)
    rB = rx(dB, "ci-export", "--stdout")
    check("[CP4-F04] null \"checkpoints\" key: ci-export --stdout exits 0", rB.returncode == 0, rB.returncode)
    check("[CP4-F04] null \"checkpoints\" key: no Python traceback appears anywhere in stdout/stderr",
          NO_TRACEBACK not in (rB.stdout + rB.stderr), (rB.stdout + rB.stderr)[:600])
    check("[CP4-F04] null \"checkpoints\" key: the separate good run's own step command still appears",
          "good.test.js" in rB.stdout, rB.stdout)
    check("[CP4-F04] null \"checkpoints\" key: stderr names the damaged state.json",
          "null-checkpoints-key-run" in rB.stderr and "state.json" in rB.stderr, rB.stderr)


TESTS.append(("CP4-F04", f04_absent_state_checkpoints_key_is_silent_not_a_bad_shape_note))


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return

    def status(cid):
        if cid in CASE_SKIPPED:
            return "skipped"
        return "passed" if CASE_STATUS.get(cid, False) else "failed"

    cases = [{"id": cid, "status": status(cid),
              "message": "not run: it made no checks" if status(cid) == "skipped"
              else "" if status(cid) == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash in one case still counts as a result for that case, not a crash of the file
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        else:
            if not CASE_CHECKS.get(cid):
                CASE_SKIPPED.add(cid)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
