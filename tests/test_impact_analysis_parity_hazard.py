#!/usr/bin/env python3
"""
CP4 tests: attach parity gates and flag report-race hazards.   python3 tests/test_impact_analysis_parity_hazard.py

Drives skills/ratchet-checkpoint-planner/scripts/impact_analysis.py's `parity` and `hazard` entry
points purely as external CLI subprocesses (argv/stdin/stdout) -- never by importing the module into
the test process -- against the static fixtures under
tests/fixtures/impact_analysis_parity_hazard/. Neither subcommand touches the filesystem beyond
reading its own stdin: `parity` decides whether a checkpoint description is an extraction and, if so,
what standing parity items to attach; `hazard` is a pure text-pattern check over a planned
tests.command string. Never touches the real tests/run_all.py, this project's real checkpoints.json,
or any file outside this file's own fixtures.

------------------------------------------------------------------------------------------------ CLI CONTRACT
This test file is this contract's first and only definition right now (CP4 does not exist as code
yet); later checkpoints' (CP5-CP7) planner prompts, helper scripts and tests must follow it exactly.

==== `parity` =====================================================================================
Usage (always a plain OS subprocess, never an in-process import):

    python3 skills/ratchet-checkpoint-planner/scripts/impact_analysis.py parity

No positional arguments and no flags. stdin: a single JSON object describing the checkpoint:

    {
      "move": null | {"symbol": "<name>", "module_from": "<dotted.module>", "module_to": "<dotted.module>"},
      "done_when": ["<done_when line>", ...],
      "baseline": null | {"module": "<dotted.module>", "startup_ms": <number>, "threshold_pct": <number>}
    }

- `move`: null for a non-extraction checkpoint (an in-place edit, or a greenfield-create checkpoint
  with no symbol move at all). A non-null `move` whose `module_from` and `module_to` are the SAME
  dotted module is a same-module rename, not an extraction either (nothing left the module). It is
  an extraction only when `module_from != module_to` and both are non-empty.
- `done_when`: the checkpoint's own planned done_when list, exactly as the plan carries it. Used only
  to decide whether a startup-parity line is already present, never mutated or echoed back.
- `baseline`: optional. When present, its `threshold_pct` (an integer percent) is the stated
  threshold the emitted startup-parity item's line names; when absent, the pass states its own fixed
  default threshold of 10 percent instead of silently omitting a number.

stdout: on every run, ONE JSON object and nothing else:

    {
      "is_extraction": true | false,
      "items": [
        {"kind": "startup-parity", "line": "<text naming import/startup cost and the % threshold>",
         "already_present": true | false},
        {"kind": "behavior-parity", "line": "<text naming a byte-identical behavior snapshot>",
         "already_present": true | false}
      ]
    }

`items` is `[]` whenever `is_extraction` is `false` -- a non-extraction or greenfield checkpoint gets
no parity item at all. When `is_extraction` is `true`, `items` always has exactly one
"startup-parity" item and exactly one "behavior-parity" item, every time, whether or not `done_when`
already asked for one: nothing is ever silently dropped, and nothing is ever duplicated into two
items of the same kind. A done_when line counts as "already asking for a startup-parity check" only
when it contains, case-insensitively, both the substrings "startup" and "parity" -- when such a line
is found, the emitted "startup-parity" item still appears (so the planner can still see/attach it)
but with `already_present: true`, meaning nothing more needs to be added to done_when. The same rule,
with substrings "byte-identical" and "parity"/"behavior", governs the "behavior-parity" item's
`already_present` flag; none of this test file's fixtures pre-populate that one, so it is always
`false` here. The two items' `line` text never share the same sentence: the startup-parity line
names import/startup cost and never says "byte-identical"; the behavior-parity line says
"byte-identical" and never states a startup/import-cost threshold.

Exit code 0 for every run (a decision, never a usage error, since stdin shape issues still describe
a valid-if-degenerate checkpoint). Nonzero is reserved for stdin that is not valid JSON at all.

==== `hazard` ======================================================================================
Usage:

    python3 skills/ratchet-checkpoint-planner/scripts/impact_analysis.py hazard

No positional arguments and no flags. stdin: a single JSON object:

    {"tests_command": "<the plan's tests.command string, exactly as written>"}

A pure text-pattern check: it never executes `tests_command`, never touches the filesystem, and never
tries to determine whether a named path is a real, existing, report-writing suite -- it only counts
how many separate `python3 <path>.py` / `python <path>.py` suite invocations appear in the string
once it is split on the shell list separators `&&`, `;` and `||`. A command invoking two or more
suites this way is a hazard, EXCEPT that a single invocation of the whole command is never a hazard
by itself (one suite alone, or one invocation of tests/run_all.py alone, is always safe) -- pairing
tests/run_all.py with one more suite beside it is still 2 invocations and is still flagged, because
the fixed runner's own safety does not extend to whatever else shares its command line. A shell
prefix with no `.py` suite invocation of its own (e.g. a lone `cd tests`) contributes nothing to the
count.

stdout: on every run, ONE JSON object and nothing else:

    {
      "hazard": true | false,
      "suites": ["<path>", ...],
      "detail": "<human-readable text naming every suite in `suites` when hazard is true, else \"\">"
    }

`suites` lists every suite invocation found, in the order they appear in `tests_command`, exactly as
spelled in the string (no normalization). `hazard` is `true` exactly when `len(suites) >= 2`. An
empty `tests_command`, or one with no recognizable suite invocation at all, never raises and always
returns `{"hazard": false, "suites": [], "detail": ""}`.

Exit code 0 for every run, including an empty or suite-less `tests_command`. Nonzero is reserved for
stdin that is not valid JSON at all, or missing the `tests_command` key.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id":
"CP4-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

IMPACT_SCRIPT = ROOT / "skills" / "ratchet-checkpoint-planner" / "scripts" / "impact_analysis.py"
FIXTURES = HERE / "fixtures" / "impact_analysis_parity_hazard"

EXTRACTION_NO_PARITY = json.loads((FIXTURES / "extraction_no_existing_parity_line.json").read_text())
EXTRACTION_WITH_PARITY = json.loads((FIXTURES / "extraction_with_existing_parity_line.json").read_text())
NON_EXTRACTION_EDIT = json.loads((FIXTURES / "non_extraction_edit_in_place.json").read_text())
SAME_MODULE_RENAME = json.loads((FIXTURES / "same_module_rename.json").read_text())
GREENFIELD_CREATE = json.loads((FIXTURES / "greenfield_create.json").read_text())
BASELINE = json.loads((FIXTURES / "baseline_timing.json").read_text())

# the three fixture "report-writing suite" scripts (never executed -- see module docstring); their
# real paths on disk, kept under tests/fixtures/ so tests/run_all.py's non-recursive test_*.py glob
# never picks them up as real suites of its own.
FIXTURE_SUITE_A = FIXTURES / "test_fixture_a.py"
FIXTURE_SUITE_B = FIXTURES / "test_fixture_b.py"
FIXTURE_SUITE_C = FIXTURES / "test_fixture_c.py"
assert FIXTURE_SUITE_A.is_file() and FIXTURE_SUITE_B.is_file() and FIXTURE_SUITE_C.is_file()

# the project's own real fixed multi-suite runner -- read-only reference, never modified or executed
# by this suite; only its path (as plain text) matters to the hazard check.
REAL_RUN_ALL = ROOT / "tests" / "run_all.py"
assert REAL_RUN_ALL.is_file()

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}


def check(cid, name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
    if not cond:
        CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} [{cid}] {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ helpers

def run_parity(payload: dict):
    """Invokes the `parity` subcommand as a plain OS subprocess. Returns
    (returncode, stdout, stderr, parsed_json_or_None)."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "parity"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        parsed = json.loads(proc.stdout)
    except Exception:
        parsed = None
    return proc.returncode, proc.stdout, proc.stderr, parsed


def run_hazard(tests_command: str):
    """Invokes the `hazard` subcommand as a plain OS subprocess. Returns
    (returncode, stdout, stderr, parsed_json_or_None)."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "hazard"],
        input=json.dumps({"tests_command": tests_command}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        parsed = json.loads(proc.stdout)
    except Exception:
        parsed = None
    return proc.returncode, proc.stdout, proc.stderr, parsed


def items_of_kind(parsed, kind):
    return [it for it in (parsed or {}).get("items", []) if it.get("kind") == kind]


def with_baseline(fixture: dict) -> dict:
    payload = dict(fixture)
    payload["baseline"] = BASELINE
    return payload


# ------------------------------------------------------------------------------------------------ CP4-T01

def t01_extraction_always_gets_both_parity_items_even_without_asking():
    cid = "CP4-T01"
    rc, out, err, parsed = run_parity(with_baseline(EXTRACTION_NO_PARITY))
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "is classified as an extraction", parsed.get("is_extraction") is True, parsed)
    items = parsed.get("items", [])
    check(cid, "emits exactly two items", len(items) == 2, items)
    startup = items_of_kind(parsed, "startup-parity")
    behavior = items_of_kind(parsed, "behavior-parity")
    check(cid, "emits exactly one startup-parity item", len(startup) == 1, items)
    check(cid, "emits exactly one behavior-parity item", len(behavior) == 1, items)
    if startup:
        line = startup[0].get("line", "")
        check(cid, "the startup-parity item names import/startup cost and the stated threshold",
              ("startup" in line.lower() or "import" in line.lower()) and "10%" in line
              and "baseline" in line.lower(), line)
    if behavior:
        line = behavior[0].get("line", "")
        check(cid, "the behavior-parity item names a byte-identical snapshot",
              "byte-identical" in line.lower(), line)


# ------------------------------------------------------------------------------------------------ CP4-T02

def t02_non_extraction_gets_no_parity_item():
    cid = "CP4-T02"
    rc, out, err, parsed = run_parity(with_baseline(NON_EXTRACTION_EDIT))
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "is not classified as an extraction", parsed.get("is_extraction") is False, parsed)
    check(cid, "emits zero parity items", parsed.get("items", []) == [], parsed.get("items"))


# ------------------------------------------------------------------------------------------------ CP4-T03

def t03_greenfield_gets_no_parity_item():
    cid = "CP4-T03"
    rc, out, err, parsed = run_parity(with_baseline(GREENFIELD_CREATE))
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "is not classified as an extraction", parsed.get("is_extraction") is False, parsed)
    check(cid, "emits zero parity items, same as any other non-extraction checkpoint",
          parsed.get("items", []) == [], parsed.get("items"))


# ------------------------------------------------------------------------------------------------ CP4-T04

def t04_same_module_rename_is_not_an_extraction():
    cid = "CP4-T04"
    rc, out, err, parsed = run_parity(with_baseline(SAME_MODULE_RENAME))
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "is not classified as an extraction (nothing left the module)",
          parsed.get("is_extraction") is False, parsed)
    check(cid, "emits zero parity items", parsed.get("items", []) == [], parsed.get("items"))


# ------------------------------------------------------------------------------------------------ CP4-T05

def t05_existing_startup_parity_line_is_not_duplicated():
    cid = "CP4-T05"
    rc, out, err, parsed = run_parity(with_baseline(EXTRACTION_WITH_PARITY))
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "is classified as an extraction", parsed.get("is_extraction") is True, parsed)
    items = parsed.get("items", [])
    startup = items_of_kind(parsed, "startup-parity")
    behavior = items_of_kind(parsed, "behavior-parity")
    check(cid, "emits exactly one startup-parity item, not two", len(startup) == 1, items)
    check(cid, "emits exactly one behavior-parity item too", len(behavior) == 1, items)
    check(cid, "emits exactly two items total (nothing silently dropped, nothing duplicated)",
          len(items) == 2, items)
    if startup:
        check(cid, "the startup-parity item is flagged already_present (done_when already asked)",
              startup[0].get("already_present") is True, startup[0])
    if behavior:
        check(cid, "the behavior-parity item is NOT flagged already_present (done_when never asked "
                   "for a byte-identical snapshot)", behavior[0].get("already_present") is False,
              behavior[0])


# ------------------------------------------------------------------------------------------------ CP4-T06

def t06_the_two_parity_items_stay_distinct():
    cid = "CP4-T06"
    rc, out, err, parsed = run_parity(with_baseline(EXTRACTION_NO_PARITY))
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    startup = items_of_kind(parsed, "startup-parity")
    behavior = items_of_kind(parsed, "behavior-parity")
    check(cid, "both kinds are present as separately identifiable items",
          len(startup) == 1 and len(behavior) == 1, parsed.get("items"))
    if startup and behavior:
        startup_line, behavior_line = startup[0].get("line", ""), behavior[0].get("line", "")
        check(cid, "the two items' lines are not the same sentence", startup_line != behavior_line,
              (startup_line, behavior_line))
        check(cid, "the startup-parity line never also claims byte-identical behavior",
              "byte-identical" not in startup_line.lower(), startup_line)
        check(cid, "the behavior-parity line never also states an import/startup-cost threshold",
              "%" not in behavior_line, behavior_line)


# ------------------------------------------------------------------------------------------------ CP4-T07

def t07_two_report_writing_suites_joined_with_and_are_flagged_naming_both():
    cid = "CP4-T07"
    cmd = "python3 tests/test_fixture_a.py && python3 tests/test_fixture_b.py"
    rc, out, err, parsed = run_hazard(cmd)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "raises the hazard flag", parsed.get("hazard") is True, parsed)
    suites = parsed.get("suites", [])
    check(cid, "names exactly two suites", len(suites) == 2, suites)
    check(cid, "names tests/test_fixture_a.py", any("test_fixture_a.py" in s for s in suites), suites)
    check(cid, "names tests/test_fixture_b.py", any("test_fixture_b.py" in s for s in suites), suites)
    detail = parsed.get("detail", "")
    check(cid, "the detail text names both suites",
          "test_fixture_a.py" in detail and "test_fixture_b.py" in detail, detail)


# ------------------------------------------------------------------------------------------------ CP4-T08

def t08_three_report_writing_suites_joined_with_semicolons_name_all_three():
    cid = "CP4-T08"
    cmd = "python3 tests/test_fixture_a.py; python3 tests/test_fixture_b.py; python3 tests/test_fixture_c.py"
    rc, out, err, parsed = run_hazard(cmd)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "raises the hazard flag", parsed.get("hazard") is True, parsed)
    suites = parsed.get("suites", [])
    check(cid, "names exactly three suites, not just the first pair found", len(suites) == 3, suites)
    for name in ("test_fixture_a.py", "test_fixture_b.py", "test_fixture_c.py"):
        check(cid, f"names {name}", any(name in s for s in suites), suites)
    detail = parsed.get("detail", "")
    check(cid, "the detail text names all three suites",
          all(name in detail for name in ("test_fixture_a.py", "test_fixture_b.py", "test_fixture_c.py")),
          detail)


# ------------------------------------------------------------------------------------------------ CP4-T09

def t09_one_suite_alone_raises_no_hazard():
    cid = "CP4-T09"
    cmd = "python3 tests/test_fixture_a.py"
    rc, out, err, parsed = run_hazard(cmd)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "no hazard flag is raised", parsed.get("hazard") is False, parsed)


# ------------------------------------------------------------------------------------------------ CP4-T10

def t10_the_fixed_multi_suite_runner_alone_raises_no_hazard():
    cid = "CP4-T10"
    cmd = "python3 tests/run_all.py"
    rc, out, err, parsed = run_hazard(cmd)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "no hazard flag is raised, even though run_all.py itself runs many suites",
          parsed.get("hazard") is False, parsed)


# ------------------------------------------------------------------------------------------------ CP4-T11

def t11_fixed_runner_plus_one_extra_suite_still_raises_a_hazard():
    cid = "CP4-T11"
    cmd = "python3 tests/run_all.py && python3 tests/test_fixture_c.py"
    rc, out, err, parsed = run_hazard(cmd)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "raises the hazard flag (the runner's own safety does not extend to an extra suite "
               "sharing its command line)", parsed.get("hazard") is True, parsed)
    suites = parsed.get("suites", [])
    check(cid, "names exactly two colliding suites", len(suites) == 2, suites)
    check(cid, "names tests/run_all.py", any("run_all.py" in s for s in suites), suites)
    check(cid, "names tests/test_fixture_c.py", any("test_fixture_c.py" in s for s in suites), suites)
    detail = parsed.get("detail", "")
    check(cid, "the detail text names both colliding suites",
          "run_all.py" in detail and "test_fixture_c.py" in detail, detail)


# ------------------------------------------------------------------------------------------------ CP4-T12

def t12_empty_or_suite_less_tests_command_raises_no_hazard_and_never_crashes():
    cid = "CP4-T12"
    rc, out, err, parsed = run_hazard("")
    check(cid, "an empty tests.command exits 0", rc == 0, (rc, out, err))
    check(cid, "an empty tests.command parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "an empty tests.command returns hazard=false", parsed.get("hazard") is False, parsed)
        check(cid, "an empty tests.command names zero suites", parsed.get("suites", []) == [], parsed)

    rc2, out2, err2, parsed2 = run_hazard("echo hello world")
    check(cid, "a suite-less tests.command exits 0", rc2 == 0, (rc2, out2, err2))
    check(cid, "a suite-less tests.command parses as JSON", parsed2 is not None, out2)
    if parsed2 is not None:
        check(cid, "a suite-less tests.command returns hazard=false", parsed2.get("hazard") is False,
              parsed2)
        check(cid, "a suite-less tests.command names zero suites", parsed2.get("suites", []) == [],
              parsed2)


# ------------------------------------------------------------------------------------------------ CP4-T13

def t13_single_suite_wrapped_in_a_harmless_shell_prefix_reads_as_one_suite():
    cid = "CP4-T13"
    cmd = "cd tests && python3 test_fixture_a.py"
    rc, out, err, parsed = run_hazard(cmd)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    check(cid, "no hazard flag is raised (only one suite is actually being run)",
          parsed.get("hazard") is False, parsed)
    check(cid, "exactly one suite is counted, not two", len(parsed.get("suites", [])) == 1, parsed)


# ------------------------------------------------------------------------------------------------ CP4-X01 (not planned; round-03 structural-rewrite regression,
# supersedes the round-02 -u-only case: a table-driven pass over interpreter-invocation spellings)

X01_CASES = [
    ("bare python3, single suite, no hazard",
     "python3 tests/test_fixture_a.py", False, ["test_fixture_a.py"]),
    ("versioned python3.12, single suite, no hazard",
     "python3.12 tests/test_fixture_a.py", False, ["test_fixture_a.py"]),
    ("path-prefixed interpreter, single suite, no hazard",
     "/usr/bin/python3 tests/test_fixture_a.py", False, ["test_fixture_a.py"]),
    ("bare flag -u between interpreter and script, single suite, no hazard",
     "python3 -u tests/test_fixture_a.py", False, ["test_fixture_a.py"]),
    ("argument-taking flag -W error, single suite, no hazard",
     "python3 -W error tests/test_fixture_a.py", False, ["test_fixture_a.py"]),
    ("two suites, versioned + path-prefixed interpreters, hazard",
     "python3.12 tests/test_fixture_a.py && /usr/bin/python3 tests/test_fixture_b.py",
     True, ["test_fixture_a.py", "test_fixture_b.py"]),
    ("two suites, bare flag + argument-taking flag, hazard",
     "python3 -u tests/test_fixture_a.py && python3 -W error tests/test_fixture_b.py",
     True, ["test_fixture_a.py", "test_fixture_b.py"]),
    ("-m pytest style invocation: no script path follows, not counted as a suite",
     "python3 -m pytest tests/test_fixture_a.py", False, []),
]


def x01_table_driven_interpreter_invocation_spellings():
    cid = "CP4-X01"
    for name, cmd, expect_hazard, expect_suite_names in X01_CASES:
        rc, out, err, parsed = run_hazard(cmd)
        check(cid, f"[{name}] exits 0", rc == 0, (cmd, rc, out, err))
        check(cid, f"[{name}] parses as JSON", parsed is not None, (cmd, out))
        if parsed is None:
            continue
        check(cid, f"[{name}] hazard is {expect_hazard}", parsed.get("hazard") is expect_hazard,
              (cmd, parsed))
        suites = parsed.get("suites", [])
        check(cid, f"[{name}] names exactly {len(expect_suite_names)} suite(s)",
              len(suites) == len(expect_suite_names), (cmd, suites))
        for suite_name in expect_suite_names:
            check(cid, f"[{name}] names {suite_name}", any(suite_name in s for s in suites),
                  (cmd, suites))


TESTS = [
    ("CP4-T01", t01_extraction_always_gets_both_parity_items_even_without_asking),
    ("CP4-T02", t02_non_extraction_gets_no_parity_item),
    ("CP4-T03", t03_greenfield_gets_no_parity_item),
    ("CP4-T04", t04_same_module_rename_is_not_an_extraction),
    ("CP4-T05", t05_existing_startup_parity_line_is_not_duplicated),
    ("CP4-T06", t06_the_two_parity_items_stay_distinct),
    ("CP4-T07", t07_two_report_writing_suites_joined_with_and_are_flagged_naming_both),
    ("CP4-T08", t08_three_report_writing_suites_joined_with_semicolons_name_all_three),
    ("CP4-T09", t09_one_suite_alone_raises_no_hazard),
    ("CP4-T10", t10_the_fixed_multi_suite_runner_alone_raises_no_hazard),
    ("CP4-T11", t11_fixed_runner_plus_one_extra_suite_still_raises_a_hazard),
    ("CP4-T12", t12_empty_or_suite_less_tests_command_raises_no_hazard_and_never_crashes),
    ("CP4-T13", t13_single_suite_wrapped_in_a_harmless_shell_prefix_reads_as_one_suite),
    ("CP4-X01", x01_table_driven_interpreter_invocation_spellings),
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
