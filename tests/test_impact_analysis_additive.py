#!/usr/bin/env python3
"""
CP5 tests: guarantee the impact-merge can only add, never weaken.   python3 tests/test_impact_analysis_additive.py

Drives skills/ratchet-checkpoint-planner/scripts/impact_analysis.py's new `merge` subcommand purely
as an external CLI subprocess (argv/stdin/stdout) -- never by importing the module into the test
process, exactly like every CP1-CP4 test file. `merge` folds CP2-CP4's enumerated_items (new
done_when lines, new planned test-case dicts) into an approved checkpoint's existing done_when list
and cases list. It is the single most safety-critical piece of this whole feature: a bug that let it
drop, reword or reorder an already-approved line would silently weaken every later gate, reviewer and
build that trusts an approved plan still means what it said.

------------------------------------------------------------------------------------------------ CLI CONTRACT
`merge` is implemented in skills/ratchet-checkpoint-planner/scripts/impact_analysis.py; that module's
own docstring (its `merge` section) is the CANONICAL definition of this CLI contract, including the
strict shape-validation layer added in CP5's round-01 fix pass (reason "invalid_shape") and the
`existing_done_when_override` field's status as a real, optional production feature, not merely a
test-only hook. The summary below restates that contract for this file's own readability but is kept
in sync with it rather than the other way around -- if the two ever disagree, impact_analysis.py's
docstring wins.

Usage (always a plain OS subprocess, never an in-process import):

    python3 skills/ratchet-checkpoint-planner/scripts/impact_analysis.py merge

No positional arguments and no flags. stdin: a single JSON object:

    {
      "checkpoint": {
        "done_when": ["<existing line>", ...],
        "cases": [{"id": "...", "given": "...", "when": "...", "then": "...",
                    "layer": "...", "edge": bool}, ...]
      },
      "enumerated_items": {
        "done_when": ["<new line>", ...],
        "cases": [{"id": "...", "given": "...", "when": "...", "then": "...",
                    "layer": "...", "edge": bool}, ...],
        "existing_done_when_override": null | ["<line>", ...]
      }
    }

- `checkpoint.done_when` / `checkpoint.cases`: the approved checkpoint's existing content, exactly as
  checkpoints.json carries it right now, in order.
- `enumerated_items.done_when` / `enumerated_items.cases`: the new lines/cases CP2-CP4's scans found
  for this checkpoint. The normal, well-behaved path: these are ALWAYS appended after
  `checkpoint.done_when` / `checkpoint.cases`, in the order given, never inserted earlier and never
  replacing anything. A `done_when` entry whose text exactly matches one already present in
  `checkpoint.done_when` is a verbatim duplicate and is skipped, not appended again (and the same rule
  applies to a `cases` entry whose id AND every field exactly match an existing case -- this is what
  makes re-running `merge` on its own prior output idempotent, see CP5-T06/T08 below). A `cases` entry
  whose `id` collides with an existing case's id but whose content differs in any field is a
  corruption attempt (see "Refusal" below), never an overwrite and never a silent rename/renumber.
- `enumerated_items.existing_done_when_override`: optional, absent/null on every payload CP2-CP4's
  real scans ever produce. It is a real, optional production feature: an explicit guard rail any
  caller (not just a test) may use to assert what it believes `checkpoint.done_when` currently looks
  like. When present, `merge` independently verifies -- before doing anything else -- that this list
  is EXACTLY equal (same length, same strings, same order) to `checkpoint.done_when`. It is never
  required for a normal merge and never changes a successful merge's output in any way (the output's
  existing lines always come from `checkpoint.done_when` itself, never from this field) -- its only
  effect is that any divergence (a reworded line, a dropped line, two lines swapped) makes `merge`
  refuse loudly instead of silently trusting the caller's assertion.

  DESIGN DECISION (see the CP5 test-writer brief, which required this to be documented and reasoned
  about explicitly): the normal `merge` contract (existing checkpoint + enumerated new items) gives a
  well-behaved caller no way to ask for an existing line to be reworded, dropped or reordered -- by
  construction the normal path only ever appends. To prove the GUARD itself actively rejects
  corruption (not merely that a well-behaved caller happens to be safe), this test file picks option
  (a) from the brief: the enumerated_items schema carries one additional, explicit, optional field
  (`existing_done_when_override`) that any caller, this test file included, may use to hand the merge
  an assertion about the existing content (divergent or not), so the guard's own comparison logic --
  not a mock, not a test-only code path -- is what catches a divergent one.
  This was preferred over option (b) (importing a pure guard function in-process) because it keeps
  every one of CP5-T01..T09 driving the exact same public entry point (a plain OS subprocess,
  argv/stdin JSON in, stdout/stderr JSON out) that every other caller of `merge` uses, matching this
  project's existing CP1-CP4 convention of never importing impact_analysis.py in-process, and because
  it exercises the guard logic that ships inside the real `merge` subcommand itself, not a separate
  code path that only the test process ever calls.

stdout on SUCCESS: exit code 0, ONE JSON object and nothing else:

    {"merged": {"done_when": [...], "cases": [...]}}

stdout on REFUSAL: exit code 3 -- reserved EXCLUSIVELY for "merge refused due to corruption or a
malformed shape", distinct from exit code 2 (a plain CLI usage error, same meaning CP1-CP4 already
give it: malformed stdin JSON, or stdin missing the top-level "checkpoint" or "enumerated_items" key
entirely). On a refusal, stdout is EMPTY (no partial or speculative output is ever produced), and
stderr carries ONE JSON object naming the offending field, line or case:

    {"refused": true, "reason": "<short machine-readable reason>", "detail": "<human-readable message
     naming the offending field, done_when index/text, or case id>"}

`reason` is one of "invalid_shape" (checkpoint/enumerated_items, or one of their done_when/cases/
existing_done_when_override fields, was not the required type -- e.g. a bare string where a list was
required, or a case object missing its "id" -- caught by strict validation before any merging logic
runs, never coerced with a bare `list(...)`), "done_when_override_mismatch" (the override diverged
from checkpoint.done_when at some index -- covers a reworded line, a dropped line, and two lines
swapped, since all three make the override list stop matching the checkpoint's own list at some
position), or "case_id_collision" (an enumerated case's id matches an existing case's id but its
content differs).

Refusal is ALL-OR-NOTHING for the whole call: when a single `merge` invocation's enumerated_items
contains both a genuinely new, non-colliding item and a corruption attempt, the WHOLE call refuses
(no stdout at all) -- it never partially applies the safe part of the payload and refuses only the
unsafe part. This matches this project's broader fail-safe convention (see classify()'s and
registration_check()'s own module docstring sections) of never partially succeeding when any part of
a request can't be trusted.

Idempotence: re-running `merge` with the merged checkpoint fed back in as the new `checkpoint`,
together with the SAME enumerated_items payload, must add nothing new the second time (CP5-T06).
Running `merge` with an enumerated_items payload that adds nothing at all (empty done_when and cases
lists, no override) is a true no-op: the output is identical to the input, in the same order
(CP5-T07).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id":
"CP5-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

IMPACT_SCRIPT = ROOT / "skills" / "ratchet-checkpoint-planner" / "scripts" / "impact_analysis.py"

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


# ------------------------------------------------------------------------------------------------ fixtures

def _case(cid, given, when, then, layer="code", edge=False):
    return {"id": cid, "given": given, "when": when, "then": then, "layer": layer, "edge": edge}


BASE_DONE_WHEN = [
    "Given a brownfield checkpoint's files_likely, classify() reports brownfield-modify whenever "
    "any path already exists in the repo root",
    "The repoint scan names the exact file+line of every monkeypatch-by-name site for a symbol "
    "moving from one module to another",
    "A new test file that source-scans another project file gets a registration item for the "
    "inventory fixture, not just the README list",
]

BASE_CASES = [
    _case("CPx-T01", "A files_likely list with one existing path", "classify() runs",
          "the verdict is brownfield-modify with a non-empty reason", edge=False),
    _case("CPx-T02", "A moved symbol with a monkeypatch.setattr site in a consumer file",
          "repoint scan runs", "a monkeypatch finding names that file and line", edge=True),
]


def base_checkpoint():
    """A fresh, independent copy every time -- tests must never share mutable fixture state."""
    return {"done_when": list(BASE_DONE_WHEN), "cases": copy.deepcopy(BASE_CASES)}


NEW_DONE_WHEN_LINE = (
    "Re-point skills/ratchet-checkpoint-planner/tests/fixtures/consumer.py:42, the only "
    "monkeypatch-by-name site for the moved symbol"
)
NEW_CASE = _case("CPx-T03", "A brand-new re-point finding for the moved symbol",
                  "the planner enumerates it", "a done_when line and case are added for it",
                  edge=False)


def enumerated(done_when=None, cases=None, override=None):
    payload = {"done_when": list(done_when or []), "cases": copy.deepcopy(cases or [])}
    if override is not None:
        payload["existing_done_when_override"] = list(override)
    return payload


# ------------------------------------------------------------------------------------------------ helpers

def run_merge(checkpoint: dict, enumerated_items: dict):
    """Invokes the `merge` subcommand as a plain OS subprocess. Returns
    (returncode, stdout, stderr, parsed_stdout_json_or_None)."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "merge"],
        input=json.dumps({"checkpoint": checkpoint, "enumerated_items": enumerated_items}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        parsed = json.loads(proc.stdout) if proc.stdout.strip() else None
    except Exception:
        parsed = None
    return proc.returncode, proc.stdout, proc.stderr, parsed


def merged_of(parsed):
    return (parsed or {}).get("merged", {})


# ------------------------------------------------------------------------------------------------ CP5-T01

def t01_merge_adds_new_line_and_case_after_the_existing_ones():
    cid = "CP5-T01"
    checkpoint = base_checkpoint()
    items = enumerated(done_when=[NEW_DONE_WHEN_LINE], cases=[NEW_CASE])
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    merged = merged_of(parsed)
    done_when = merged.get("done_when", [])
    cases = merged.get("cases", [])
    check(cid, "result has 4 done_when lines", len(done_when) == 4, done_when)
    check(cid, "result has 3 cases", len(cases) == 3, [c.get("id") for c in cases])
    check(cid, "the new line is present, after the existing 3",
          done_when[3:4] == [NEW_DONE_WHEN_LINE], done_when)
    check(cid, "the new case is present, after the existing 2",
          len(cases) == 3 and cases[2].get("id") == "CPx-T03", [c.get("id") for c in cases])


# ------------------------------------------------------------------------------------------------ CP5-T02

def t02_every_pre_existing_line_and_case_comes_out_byte_for_byte_identical():
    cid = "CP5-T02"
    checkpoint = base_checkpoint()
    items = enumerated(done_when=[NEW_DONE_WHEN_LINE], cases=[NEW_CASE])
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    merged = merged_of(parsed)
    done_when = merged.get("done_when", [])
    cases = merged.get("cases", [])
    for i, original in enumerate(BASE_DONE_WHEN):
        check(cid, f"done_when[{i}] is character-for-character identical to the original",
              len(done_when) > i and done_when[i] == original, (i, done_when[i:i + 1], original))
    for i, original in enumerate(BASE_CASES):
        same = len(cases) > i and all(cases[i].get(k) == original.get(k)
                                        for k in ("id", "given", "when", "then", "layer", "edge"))
        check(cid, f"cases[{i}] has every field identical to the original input, not just id",
              same, (i, cases[i:i + 1] if len(cases) > i else None, original))


# ------------------------------------------------------------------------------------------------ CP5-T03

def t03_merge_refuses_to_reword_an_already_approved_line():
    cid = "CP5-T03"
    checkpoint = base_checkpoint()
    reworded = list(BASE_DONE_WHEN)
    reworded[1] = BASE_DONE_WHEN[1].replace("names the exact file+line", "names the approximate location")
    check(cid, "fixture sanity: the reworded line really differs from the original",
          reworded[1] != BASE_DONE_WHEN[1], (reworded[1], BASE_DONE_WHEN[1]))
    items = enumerated(done_when=[NEW_DONE_WHEN_LINE], override=reworded)
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "the merge refuses (exit 3), it does not return a value", rc == 3, (rc, out, err))
    check(cid, "no output is produced where that line's wording changed", out.strip() == "", out)
    check(cid, "stderr names the reworded line's index or text",
          "1" in err or BASE_DONE_WHEN[1][:20] in err or "reword" in err.lower(), err)


# ------------------------------------------------------------------------------------------------ CP5-T04

def t04_merge_refuses_to_drop_an_already_approved_line():
    cid = "CP5-T04"
    checkpoint = base_checkpoint()
    dropped = [BASE_DONE_WHEN[0], BASE_DONE_WHEN[2]]  # middle line missing
    items = enumerated(done_when=[NEW_DONE_WHEN_LINE], override=dropped)
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "the merge refuses (exit 3) rather than silently returning a shorter list",
          rc == 3, (rc, out, err))
    check(cid, "no output is produced (a direct check that no result with fewer existing-line "
               "matches than the input ever gets returned)", out.strip() == "", out)
    check(cid, "stderr names the dropped line", "2" in err or BASE_DONE_WHEN[1][:20] in err, err)


# ------------------------------------------------------------------------------------------------ CP5-T05

def t05_merge_refuses_to_reorder_an_existing_line_away_from_its_position():
    cid = "CP5-T05"
    checkpoint = base_checkpoint()
    reordered = [BASE_DONE_WHEN[2], BASE_DONE_WHEN[1], BASE_DONE_WHEN[0]]  # all text intact, order permuted
    items = enumerated(done_when=[NEW_DONE_WHEN_LINE], override=reordered)
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "the merge refuses (exit 3)", rc == 3, (rc, out, err))
    check(cid, "no output is produced with the existing lines out of order", out.strip() == "", out)

    # a well-behaved call (no override) on the same checkpoint keeps the original relative order,
    # with the new item only ever appended after them -- proving the preserved-order half of the claim.
    rc2, out2, err2, parsed2 = run_merge(checkpoint, enumerated(done_when=[NEW_DONE_WHEN_LINE]))
    check(cid, "a well-behaved call still exits 0", rc2 == 0, (rc2, out2, err2))
    if parsed2 is not None:
        done_when2 = merged_of(parsed2).get("done_when", [])
        check(cid, "the existing lines keep their original relative order, new item appended last",
              done_when2[:3] == BASE_DONE_WHEN and done_when2[3:4] == [NEW_DONE_WHEN_LINE], done_when2)


# ------------------------------------------------------------------------------------------------ CP5-T06

def t06_rerunning_with_the_same_new_items_twice_adds_nothing_the_second_time():
    cid = "CP5-T06"
    checkpoint = base_checkpoint()
    items = enumerated(done_when=[NEW_DONE_WHEN_LINE], cases=[NEW_CASE])

    rc1, out1, err1, parsed1 = run_merge(checkpoint, items)
    check(cid, "first merge exits 0", rc1 == 0, (rc1, out1, err1))
    check(cid, "first merge parses as JSON", parsed1 is not None, out1)
    if parsed1 is None:
        return
    merged_once = merged_of(parsed1)

    rc2, out2, err2, parsed2 = run_merge(merged_once, items)
    check(cid, "second merge (on merged_once) exits 0", rc2 == 0, (rc2, out2, err2))
    check(cid, "second merge parses as JSON", parsed2 is not None, out2)
    if parsed2 is None:
        return
    merged_twice = merged_of(parsed2)

    check(cid, "merged_twice's done_when is exactly equal to merged_once's",
          merged_twice.get("done_when") == merged_once.get("done_when"),
          (merged_twice.get("done_when"), merged_once.get("done_when")))
    check(cid, "merged_twice's cases is exactly equal to merged_once's",
          merged_twice.get("cases") == merged_once.get("cases"),
          ([c.get("id") for c in merged_twice.get("cases", [])],
           [c.get("id") for c in merged_once.get("cases", [])]))
    check(cid, "no duplicate done_when lines were introduced",
          len(merged_twice.get("done_when", [])) == len(set(merged_twice.get("done_when", []))),
          merged_twice.get("done_when"))
    ids = [c.get("id") for c in merged_twice.get("cases", [])]
    check(cid, "no duplicate case ids were introduced", len(ids) == len(set(ids)), ids)


# ------------------------------------------------------------------------------------------------ CP5-T07

def t07_when_there_is_nothing_to_add_the_merge_is_a_true_no_op():
    cid = "CP5-T07"
    # an independent base checkpoint, per the plan's fixture list, kept separate from BASE_* above.
    checkpoint = {
        "done_when": [
            "A greenfield checkpoint whose files_likely are all confirmed absent is classified "
            "greenfield-create with an empty reason",
        ],
        "cases": [_case("CPy-T01", "A files_likely list with zero existing paths", "classify() runs",
                          "the verdict is greenfield-create with reason=''", edge=False)],
    }
    items = enumerated()  # nothing to add, no override
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    merged = merged_of(parsed)
    check(cid, "the output done_when is identical to the input, same order",
          merged.get("done_when") == checkpoint["done_when"],
          (merged.get("done_when"), checkpoint["done_when"]))
    check(cid, "the output cases is identical to the input, same order",
          merged.get("cases") == checkpoint["cases"],
          ([c.get("id") for c in merged.get("cases", [])],
           [c.get("id") for c in checkpoint["cases"]]))


# ------------------------------------------------------------------------------------------------ CP5-T08

def t08_an_enumerated_item_duplicating_an_existing_line_verbatim_is_not_added_again():
    cid = "CP5-T08"
    checkpoint = base_checkpoint()
    genuinely_new = "A genuinely new done_when line this checkpoint never had before"
    items = enumerated(done_when=[BASE_DONE_WHEN[0], genuinely_new])  # BASE_DONE_WHEN[0] is verbatim dup
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    done_when = merged_of(parsed).get("done_when", [])
    check(cid, "exactly 1 new line is added (the genuinely new one)", len(done_when) == 4, done_when)
    check(cid, "the genuinely new line is present", genuinely_new in done_when, done_when)
    check(cid, "the verbatim duplicate is not appended a second time",
          done_when.count(BASE_DONE_WHEN[0]) == 1, done_when)


# ------------------------------------------------------------------------------------------------ CP5-T09

def t09_a_new_case_id_colliding_with_an_existing_one_is_refused_not_overwritten():
    cid = "CP5-T09"
    checkpoint = base_checkpoint()
    colliding = _case("CPx-T01", "A DIFFERENT given than the real CPx-T01", "a different when",
                        "a different then", layer="code", edge=True)
    check(cid, "fixture sanity: the colliding case's content really differs from the real CPx-T01",
          colliding != BASE_CASES[0], (colliding, BASE_CASES[0]))

    # part 1: the fresh-id case alone is appended normally (the well-behaved half of the claim).
    rc1, out1, err1, parsed1 = run_merge(checkpoint, enumerated(cases=[NEW_CASE]))
    check(cid, "a fresh-id case alone exits 0", rc1 == 0, (rc1, out1, err1))
    if parsed1 is not None:
        cases1 = merged_of(parsed1).get("cases", [])
        check(cid, "the fresh-id case (CPx-T03) is appended",
              any(c.get("id") == "CPx-T03" for c in cases1), [c.get("id") for c in cases1])

    # part 2: adding the id-colliding case (alongside the fresh one) makes the whole call refuse,
    # rather than overwriting CPx-T01's content or silently renumbering it.
    rc2, out2, err2, parsed2 = run_merge(checkpoint, enumerated(cases=[NEW_CASE, colliding]))
    check(cid, "the merge refuses (exit 3) on the id-colliding case", rc2 == 3, (rc2, out2, err2))
    check(cid, "no output is produced (CPx-T01 is never overwritten or renumbered)",
          out2.strip() == "", out2)
    check(cid, "stderr names the colliding case id", "CPx-T01" in err2, err2)


TESTS = [
    ("CP5-T01", t01_merge_adds_new_line_and_case_after_the_existing_ones),
    ("CP5-T02", t02_every_pre_existing_line_and_case_comes_out_byte_for_byte_identical),
    ("CP5-T03", t03_merge_refuses_to_reword_an_already_approved_line),
    ("CP5-T04", t04_merge_refuses_to_drop_an_already_approved_line),
    ("CP5-T05", t05_merge_refuses_to_reorder_an_existing_line_away_from_its_position),
    ("CP5-T06", t06_rerunning_with_the_same_new_items_twice_adds_nothing_the_second_time),
    ("CP5-T07", t07_when_there_is_nothing_to_add_the_merge_is_a_true_no_op),
    ("CP5-T08", t08_an_enumerated_item_duplicating_an_existing_line_verbatim_is_not_added_again),
    ("CP5-T09", t09_a_new_case_id_colliding_with_an_existing_one_is_refused_not_overwritten),
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


# ------------------------------------------------------------------------------------------------
# CP5-X01..X11: extra checks added after adversarial review round 1. New blocks appended at the end
# of this file, after the planned CP5-T01..T09 (whose own bodies are frozen and untouched above).
#
# Round 1 found that `merge_checkpoint` built its existing_done_when/existing_cases with a bare
# `list(checkpoint.get(...) or [])`, with no shape validation: a one-line done_when authored
# unwrapped as a plain string got shredded into one entry per character, and a single case dict
# authored unwrapped (or the whole tests/CPn.json object handed in by mistake) came out as a list of
# its own field/key names -- both with exit 0, no refusal. The fix added a strict shape-validation
# layer (reason "invalid_shape") that runs before any merging logic. These cases prove that layer
# actually refuses every one of those shapes instead of crashing or silently reshaping content, and
# also strengthen the reword/drop/reorder/collision refusal checks to parse the stderr JSON's
# `reason` field (rather than a loose substring match), and widen the byte-for-byte-unchanged proof
# to cover every field of a real planned-case shape (including `title`/`proves`, which the planned
# CP5-T02's hardcoded 6-field comparison does not check).
#
# CP5-X01: an unwrapped string checkpoint.done_when, and an unwrapped dict checkpoint.cases, are
#          each refused (reason "invalid_shape"), never shredded into characters or key names.
# CP5-X02: the whole tests/CPn.json-shaped object (and an id-keyed dict) handed in as
#          checkpoint.cases is refused the same way.
# CP5-X03: a checkpoint missing its 'done_when' or 'cases' key entirely is refused, not treated as
#          an empty list.
# CP5-X04: a non-dict checkpoint or enumerated_items (string, list, None) is refused cleanly (exit
#          3, JSON on stderr), never a bare Python traceback.
# CP5-X05: a non-list existing_done_when_override (dict, int, or a string that happens to match the
#          existing lines character-by-character) is refused, never crashes and never silently
#          passes by coincidence.
# CP5-X06: an unwrapped string enumerated_items.done_when and an unwrapped dict
#          enumerated_items.cases are refused, never injected as per-character/per-key garbage
#          entries.
# CP5-X07: an enumerated case with no 'id' field at all is refused as an invalid shape, never
#          misreported as colliding with some unrelated existing case.
# CP5-X08: the reword/drop/reorder refusals parse the stderr JSON and assert the actual `reason`
#          ("done_when_override_mismatch") and the diverging index named in `detail`.
# CP5-X09: the id-collision refusal parses the stderr JSON and asserts `reason` ==
#          "case_id_collision".
# CP5-X10: the documented exit-2 usage-error paths (malformed top-level JSON, a missing
#          'checkpoint' or 'enumerated_items' top-level key) are exercised directly.
# CP5-X11: a full-dict-equality check (every key, not a hardcoded subset) on richer case fixtures
#          that include 'title' and 'proves' -- the fields a real planned case (tests/CP5.json)
#          carries that CP5-T02's own hardcoded 6-field comparison does not check, so a regression
#          that stripped them would still pass the whole planned suite. Also proves a newly
#          enumerated case's own title/proves survive, and that an unrecognized extra key in any
#          case dict is refused rather than silently stripped or passed through.
#
# CP5-X12..X14: extra checks added after adversarial review round 2. Round 2 found that
# `enumerated_items`'s own schema was still OPEN (an unrecognized top-level key, e.g. a typo like
# "done-when", was silently read as "nothing to add" instead of refused) and that enumerated case
# dicts were validated only for a non-empty 'id', not for 'given'/'when'/'then'. The fix closed both
# schemas. It also found that a case-id collision between two NEWLY enumerated cases (never present
# in checkpoint.cases at all) was misreported as "already exists in checkpoint.cases" -- the fix
# tracks which source (checkpoint.cases vs. this same enumerated_items.cases call) the colliding id
# actually came from and names it accurately.
#
# CP5-X12: an unrecognized top-level key in enumerated_items (the exact round-2 repro: {"done-when":
#          [...], "test_cases": [...]}) is refused as invalid_shape, never silently treated as an
#          empty/no-op payload.
# CP5-X13: two enumerated cases that share a new id (absent from checkpoint.cases) but differ in
#          content are refused as case_id_collision, with detail naming the duplicate-within-
#          enumerated_items source, never misattributing it to checkpoint.cases. A sibling case
#          proves the opposite source (an id genuinely already in checkpoint.cases) still names
#          checkpoint.cases.
# CP5-X14: an enumerated case missing 'given'/'when'/'then', carrying the wrong type for one of
#          them, or carrying a whitespace-only 'id', is refused as invalid_shape naming the specific
#          offending field -- never merged in with holes a builder can't implement.
# ------------------------------------------------------------------------------------------------

def _parsed_err(err):
    try:
        return json.loads(err)
    except Exception:
        return None


def _run_merge_raw(stdin_text):
    """Invokes `merge` with raw stdin text, bypassing run_merge's always-both-keys JSON building, so
    usage-error paths (malformed JSON, a missing top-level key) can be exercised directly."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "merge"],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def x01_unwrapped_string_done_when_and_dict_cases_are_refused_not_shredded():
    cid = "CP5-X01"
    checkpoint = {"done_when": "The one existing approved line, unwrapped", "cases": []}
    rc, out, err, parsed = run_merge(checkpoint, enumerated())
    check(cid, "a string done_when is refused (exit 3), not shredded into one entry per character",
          rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "stderr names reason invalid_shape for the string done_when",
          err_json is not None and err_json.get("reason") == "invalid_shape", err)

    checkpoint2 = {"done_when": [], "cases": {"id": "X1", "given": "G", "when": "W", "then": "T"}}
    rc2, out2, err2, parsed2 = run_merge(checkpoint2, enumerated())
    check(cid, "a dict cases value is refused (exit 3), not turned into a list of its own key names",
          rc2 == 3 and out2.strip() == "", (rc2, out2, err2))
    err2_json = _parsed_err(err2)
    check(cid, "stderr names reason invalid_shape for the dict cases",
          err2_json is not None and err2_json.get("reason") == "invalid_shape", err2)


def x02_the_whole_cpn_json_shaped_object_or_an_id_keyed_dict_as_cases_is_refused():
    cid = "CP5-X02"
    whole_plan_object = {
        "checkpoint": "CP5", "strategy": "...", "fixtures": [], "cases": [{"id": "T01"}],
        "files": ["tests/test_impact_analysis_additive.py"], "command": "python3 x.py", "report": "json",
    }
    checkpoint = {"done_when": [], "cases": whole_plan_object}
    rc, out, err, parsed = run_merge(checkpoint, enumerated())
    check(cid, "passing the whole tests/CPn.json-shaped object as checkpoint.cases is refused, not "
               "turned into a list of its top-level field names",
          rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "stderr names reason invalid_shape", err_json is not None and err_json.get("reason") == "invalid_shape", err)
    check(cid, "detail names the offending field checkpoint.cases",
          err_json is not None and "checkpoint.cases" in err_json.get("detail", ""), err)

    id_keyed = {"T01": {"id": "T01", "given": "G", "when": "W", "then": "T"}}
    checkpoint2 = {"done_when": [], "cases": id_keyed}
    rc2, out2, err2, parsed2 = run_merge(checkpoint2, enumerated())
    check(cid, "an id-keyed dict as checkpoint.cases is refused, not turned into a list of its keys",
          rc2 == 3 and out2.strip() == "", (rc2, out2, err2))
    err2_json = _parsed_err(err2)
    check(cid, "stderr names reason invalid_shape for the id-keyed dict",
          err2_json is not None and err2_json.get("reason") == "invalid_shape", err2)
    check(cid, "detail names the offending field checkpoint.cases",
          err2_json is not None and "checkpoint.cases" in err2_json.get("detail", ""), err2)


def x03_a_checkpoint_missing_done_when_or_cases_entirely_is_refused():
    cid = "CP5-X03"
    rc, out, err, parsed = run_merge({"done_when": list(BASE_DONE_WHEN)}, enumerated())
    check(cid, "a checkpoint with no 'cases' key at all is refused, not treated as an empty case list",
          rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "detail names the missing 'cases' field",
          err_json is not None and "cases" in err_json.get("detail", ""), err)

    rc2, out2, err2, parsed2 = run_merge({"cases": copy.deepcopy(BASE_CASES)}, enumerated())
    check(cid, "a checkpoint with no 'done_when' key at all is refused, not treated as an empty list",
          rc2 == 3 and out2.strip() == "", (rc2, out2, err2))
    err2_json = _parsed_err(err2)
    check(cid, "detail names the missing 'done_when' field",
          err2_json is not None and "done_when" in err2_json.get("detail", ""), err2)


def x04_non_dict_checkpoint_or_enumerated_items_is_refused_cleanly_never_a_traceback():
    cid = "CP5-X04"
    for bad_checkpoint in ("not-a-dict", ["x"], None):
        rc, out, err, parsed = run_merge(bad_checkpoint, enumerated())
        check(cid, f"a non-dict checkpoint ({bad_checkpoint!r}) is refused (exit 3), not a crash",
              rc == 3 and out.strip() == "", (rc, out, err))
        check(cid, "no bare traceback appears on stderr", "Traceback" not in err, err)
        err_json = _parsed_err(err)
        check(cid, "stderr is one clean JSON object with reason invalid_shape",
              err_json is not None and err_json.get("reason") == "invalid_shape", err)

    checkpoint = base_checkpoint()
    for bad_items in ("nope", ["y"]):
        rc, out, err, parsed = run_merge(checkpoint, bad_items)
        check(cid, f"a non-dict enumerated_items ({bad_items!r}) is refused (exit 3), not a crash",
              rc == 3 and out.strip() == "", (rc, out, err))
        check(cid, "no bare traceback appears on stderr", "Traceback" not in err, err)
        err_json = _parsed_err(err)
        check(cid, "stderr names reason invalid_shape for the non-dict enumerated_items",
              err_json is not None and err_json.get("reason") == "invalid_shape", err)
        check(cid, "detail names enumerated_items as the offending field",
              err_json is not None and "enumerated_items must be" in err_json.get("detail", ""), err)


def x05_a_non_list_override_is_refused_never_crashes_never_passes_by_coincidence():
    cid = "CP5-X05"
    checkpoint = {"done_when": ["a", "b"], "cases": []}

    rc, out, err, parsed = run_merge(
        checkpoint, {"done_when": [], "cases": [], "existing_done_when_override": {"0": "a"}})
    check(cid, "a dict override is refused (exit 3), not a KeyError crash",
          rc == 3 and out.strip() == "", (rc, out, err))
    check(cid, "no bare traceback appears on stderr", "Traceback" not in err, err)
    err_json = _parsed_err(err)
    check(cid, "stderr names reason invalid_shape for the dict override",
          err_json is not None and err_json.get("reason") == "invalid_shape", err)
    check(cid, "detail names existing_done_when_override as the offending field",
          err_json is not None and "existing_done_when_override" in err_json.get("detail", ""), err)

    rc2, out2, err2, parsed2 = run_merge(
        checkpoint, {"done_when": [], "cases": [], "existing_done_when_override": 5})
    check(cid, "an int override is refused (exit 3), not a TypeError crash",
          rc2 == 3 and out2.strip() == "", (rc2, out2, err2))
    err2_json = _parsed_err(err2)
    check(cid, "stderr names reason invalid_shape for the int override",
          err2_json is not None and err2_json.get("reason") == "invalid_shape", err2)
    check(cid, "detail names existing_done_when_override as the offending field",
          err2_json is not None and "existing_done_when_override" in err2_json.get("detail", ""), err2)

    # a string override that happens to match the existing lines character-by-character must still
    # be refused as the wrong shape, never silently accepted as "no divergence".
    rc3, out3, err3, parsed3 = run_merge(
        checkpoint, {"done_when": [], "cases": [], "existing_done_when_override": "ab"})
    check(cid, "a coincidentally-matching string override is refused, not silently accepted",
          rc3 == 3 and out3.strip() == "", (rc3, out3, err3))
    err3_json = _parsed_err(err3)
    check(cid, "stderr names reason invalid_shape for the string override",
          err3_json is not None and err3_json.get("reason") == "invalid_shape", err3)


def x06_unwrapped_enumerated_done_when_or_cases_is_refused_not_injected_as_garbage():
    cid = "CP5-X06"
    checkpoint = base_checkpoint()
    rc, out, err, parsed = run_merge(checkpoint, {"done_when": "xyz", "cases": []})
    check(cid, "a string enumerated done_when is refused, not injected as one line per character",
          rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "stderr names reason invalid_shape for the string enumerated done_when",
          err_json is not None and err_json.get("reason") == "invalid_shape", err)
    check(cid, "detail names enumerated_items.done_when as the offending field",
          err_json is not None and "enumerated_items.done_when" in err_json.get("detail", ""), err)

    rc2, out2, err2, parsed2 = run_merge(checkpoint, {"done_when": [], "cases": {"X01": "whatever"}})
    check(cid, "a dict enumerated cases is refused, not injected as its own key names",
          rc2 == 3 and out2.strip() == "", (rc2, out2, err2))
    err2_json = _parsed_err(err2)
    check(cid, "stderr names reason invalid_shape for the dict enumerated cases",
          err2_json is not None and err2_json.get("reason") == "invalid_shape", err2)
    check(cid, "detail names enumerated_items.cases as the offending field",
          err2_json is not None and "enumerated_items.cases" in err2_json.get("detail", ""), err2)


def x07_an_enumerated_case_with_no_id_is_refused_as_invalid_shape_not_misreported():
    cid = "CP5-X07"
    checkpoint = base_checkpoint()
    items = enumerated(cases=[{"desc": "no id case A"}, {"desc": "no id case B"}])
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "two id-less enumerated cases are refused (exit 3)", rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "the refusal reason is invalid_shape, not case_id_collision",
          err_json is not None and err_json.get("reason") == "invalid_shape", err)
    check(cid, "the detail does not misreport this as colliding with an existing case",
          err_json is not None and "existing case" not in err_json.get("detail", ""), err)
    check(cid, "detail names cases[0] and the missing 'id' field",
          err_json is not None and "cases[0]" in err_json.get("detail", "")
          and "id" in err_json.get("detail", ""), err)


def x08_reword_drop_reorder_refusals_report_the_documented_reason_and_index():
    cid = "CP5-X08"
    checkpoint = base_checkpoint()

    reworded = list(BASE_DONE_WHEN)
    reworded[1] = BASE_DONE_WHEN[1].replace("names the exact file+line", "names the approximate location")
    rc, out, err, parsed = run_merge(checkpoint, enumerated(override=reworded))
    err_json = _parsed_err(err)
    check(cid, "reword: reason is done_when_override_mismatch",
          rc == 3 and err_json is not None and err_json.get("reason") == "done_when_override_mismatch", err)
    check(cid, "reword: detail names index 1", err_json is not None and "index 1" in err_json.get("detail", ""), err)

    dropped = [BASE_DONE_WHEN[0], BASE_DONE_WHEN[2]]
    rc2, out2, err2, parsed2 = run_merge(checkpoint, enumerated(override=dropped))
    err2_json = _parsed_err(err2)
    check(cid, "drop: reason is done_when_override_mismatch",
          rc2 == 3 and err2_json is not None and err2_json.get("reason") == "done_when_override_mismatch", err2)
    check(cid, "drop: detail names the first diverging index (1, since index 0 still matches)",
          err2_json is not None and "index 1" in err2_json.get("detail", ""), err2)

    reordered = [BASE_DONE_WHEN[2], BASE_DONE_WHEN[1], BASE_DONE_WHEN[0]]
    rc3, out3, err3, parsed3 = run_merge(checkpoint, enumerated(override=reordered))
    err3_json = _parsed_err(err3)
    check(cid, "reorder: reason is done_when_override_mismatch",
          rc3 == 3 and err3_json is not None and err3_json.get("reason") == "done_when_override_mismatch", err3)
    check(cid, "reorder: detail names the first diverging index (0)",
          err3_json is not None and "index 0" in err3_json.get("detail", ""), err3)


def x09_the_id_collision_refusal_reports_the_documented_reason():
    cid = "CP5-X09"
    checkpoint = base_checkpoint()
    colliding = _case("CPx-T01", "A DIFFERENT given than the real CPx-T01", "a different when",
                       "a different then", layer="code", edge=True)
    rc, out, err, parsed = run_merge(checkpoint, enumerated(cases=[NEW_CASE, colliding]))
    err_json = _parsed_err(err)
    check(cid, "the refusal reason is case_id_collision",
          rc == 3 and err_json is not None and err_json.get("reason") == "case_id_collision", err)
    check(cid, "the detail names the colliding id",
          err_json is not None and "CPx-T01" in err_json.get("detail", ""), err)


def x10_the_documented_exit_2_usage_error_paths_are_exercised():
    cid = "CP5-X10"
    rc, out, err = _run_merge_raw("{not valid json")
    check(cid, "malformed top-level JSON exits 2", rc == 2 and out.strip() == "", (rc, out, err))

    rc2, out2, err2 = _run_merge_raw(json.dumps({"enumerated_items": enumerated()}))
    check(cid, "stdin missing the top-level 'checkpoint' key exits 2",
          rc2 == 2 and out2.strip() == "", (rc2, out2, err2))

    rc3, out3, err3 = _run_merge_raw(json.dumps({"checkpoint": base_checkpoint()}))
    check(cid, "stdin missing the top-level 'enumerated_items' key exits 2",
          rc3 == 2 and out3.strip() == "", (rc3, out3, err3))


def x11_every_field_of_a_real_planned_case_shape_survives_the_merge_unchanged():
    cid = "CP5-X11"
    rich_cases = [
        {"id": "CPx-T01", "title": "A files_likely list with one existing path", "given": "g1",
         "when": "w1", "then": "t1", "layer": "code", "edge": False, "proves": [1],
         "x_future_field": {"nested": [1]}},
        {"id": "CPx-T02", "title": "A moved symbol with a monkeypatch.setattr site", "given": "g2",
         "when": "w2", "then": "t2", "layer": "code", "edge": True, "proves": [1, 2],
         "x_future_field": {"nested": [2]}},
    ]
    new_rich_case = {
        "id": "CPx-T04", "title": "A brand-new enumerated case with its own title and proves",
        "given": "g3", "when": "w3", "then": "t3", "layer": "code", "edge": False, "proves": [3],
    }
    checkpoint = {"done_when": list(BASE_DONE_WHEN), "cases": copy.deepcopy(rich_cases)}
    items = enumerated(done_when=[NEW_DONE_WHEN_LINE], cases=[NEW_CASE, new_rich_case])
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    if parsed is None:
        return
    merged_cases = merged_of(parsed).get("cases", [])
    for i, original in enumerate(rich_cases):
        check(cid, f"cases[{i}] is a full dict-equal match to the original (every key, including "
                   "title, proves and an unrecognized extra key, not a hardcoded subset)",
              len(merged_cases) > i and merged_cases[i] == original,
              (i, merged_cases[i:i + 1] if len(merged_cases) > i else None, original))
    newly_appended_index = len(rich_cases) + 1  # NEW_CASE is appended first, new_rich_case second
    check(cid, "the brand-new enumerated case's own title/proves survive unchanged, not just "
               "pre-existing cases' fields",
          len(merged_cases) > newly_appended_index
          and merged_cases[newly_appended_index] == new_rich_case,
          (newly_appended_index,
           merged_cases[newly_appended_index:newly_appended_index + 1]
           if len(merged_cases) > newly_appended_index else None,
           new_rich_case))

    # an unrecognized key in a NEWLY enumerated case is refused as invalid_shape -- never silently
    # stripped (which would pass this suite forever even if a future "normalize to known keys"
    # regression dropped it) and never silently passed through unvalidated.
    bad_case = dict(new_rich_case, id="CPx-T05", x_unexpected="nope")
    rc2, out2, err2, parsed2 = run_merge(checkpoint, enumerated(cases=[bad_case]))
    check(cid, "an enumerated case with an unrecognized extra key is refused (exit 3), not silently "
               "stripped or passed through",
          rc2 == 3 and out2.strip() == "", (rc2, out2, err2))
    err2_json = _parsed_err(err2)
    check(cid, "stderr names reason invalid_shape for the unrecognized case key",
          err2_json is not None and err2_json.get("reason") == "invalid_shape", err2)
    check(cid, "detail names the unexpected key x_unexpected",
          err2_json is not None and "x_unexpected" in err2_json.get("detail", ""), err2)


def x12_an_unrecognized_enumerated_items_key_is_refused_not_treated_as_nothing_to_add():
    cid = "CP5-X12"
    checkpoint = base_checkpoint()
    # the exact round-2 repro: a typo'd "done-when" and a stale "test_cases" name instead of the
    # real "done_when"/"cases" keys -- both unrecognized, so the whole payload must refuse rather
    # than being read as an enumerated_items with nothing usable in it (silent no-op).
    items = {"done-when": ["NEW"], "test_cases": [{"id": "N"}]}
    rc, out, err, parsed = run_merge(checkpoint, items)
    check(cid, "an enumerated_items with only unrecognized keys is refused (exit 3), not treated as "
               "a no-op that leaves the checkpoint unchanged",
          rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "stderr names reason invalid_shape",
          err_json is not None and err_json.get("reason") == "invalid_shape", err)
    check(cid, "detail names both unrecognized keys",
          err_json is not None and "done-when" in err_json.get("detail", "")
          and "test_cases" in err_json.get("detail", ""), err)


def x13_sibling_case_id_collisions_are_attributed_to_the_right_source():
    cid = "CP5-X13"
    checkpoint = base_checkpoint()  # BASE_CASES has id "CPx-T01", nothing named "N1"

    # two brand-new enumerated cases sharing id "N1" (never in checkpoint.cases at all) must be
    # attributed to enumerated_items, never misreported as colliding with an existing case.
    sibling_a = {"id": "N1", "given": "a", "when": "w", "then": "t"}
    sibling_b = {"id": "N1", "given": "b", "when": "w", "then": "t"}
    rc, out, err, parsed = run_merge(checkpoint, enumerated(cases=[sibling_a, sibling_b]))
    check(cid, "two new sibling cases sharing an id are refused (exit 3)",
          rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "the refusal reason is case_id_collision",
          err_json is not None and err_json.get("reason") == "case_id_collision", err)
    check(cid, "the detail attributes the collision to enumerated_items, not checkpoint.cases",
          err_json is not None and "enumerated_items" in err_json.get("detail", "")
          and "already exists in checkpoint.cases" not in err_json.get("detail", ""), err)

    # the real collision case (CPx-T01 already exists in checkpoint.cases) must still be attributed
    # to checkpoint.cases, proving the two messages are genuinely distinguished, not just renamed.
    colliding_existing = {"id": "CPx-T01", "given": "different", "when": "w", "then": "t"}
    rc2, out2, err2, parsed2 = run_merge(checkpoint, enumerated(cases=[colliding_existing]))
    err2_json = _parsed_err(err2)
    check(cid, "a real collision with an existing case is still attributed to checkpoint.cases",
          rc2 == 3 and err2_json is not None
          and err2_json.get("reason") == "case_id_collision"
          and "already exists in checkpoint.cases" in err2_json.get("detail", ""), err2)


def x14_an_enumerated_case_missing_given_when_then_or_with_a_blank_id_is_refused():
    cid = "CP5-X14"
    checkpoint = base_checkpoint()

    rc, out, err, parsed = run_merge(checkpoint, enumerated(cases=[{"id": "N1"}]))
    check(cid, "a case with only 'id' (no given/when/then) is refused (exit 3)",
          rc == 3 and out.strip() == "", (rc, out, err))
    err_json = _parsed_err(err)
    check(cid, "detail names the missing 'given' field",
          err_json is not None and "given" in err_json.get("detail", ""), err)

    bad_types = {"id": "N1", "given": 1, "when": None, "then": {"x": {}}, "edge": "yes"}
    rc2, out2, err2, parsed2 = run_merge(checkpoint, enumerated(cases=[bad_types]))
    check(cid, "a case with the wrong type for 'given' is refused (exit 3)",
          rc2 == 3 and out2.strip() == "", (rc2, out2, err2))
    err2_json = _parsed_err(err2)
    check(cid, "detail names the offending 'given' field",
          err2_json is not None and "given" in err2_json.get("detail", ""), err2)

    whitespace_id = {"id": "   ", "given": "g", "when": "w", "then": "t"}
    rc3, out3, err3, parsed3 = run_merge(checkpoint, enumerated(cases=[whitespace_id]))
    check(cid, "a whitespace-only id is refused (exit 3), not accepted as a non-empty string",
          rc3 == 3 and out3.strip() == "", (rc3, out3, err3))
    err3_json = _parsed_err(err3)
    check(cid, "detail names the blank 'id' field",
          err3_json is not None and "id" in err3_json.get("detail", ""), err3)


TESTS += [
    ("CP5-X01", x01_unwrapped_string_done_when_and_dict_cases_are_refused_not_shredded),
    ("CP5-X02", x02_the_whole_cpn_json_shaped_object_or_an_id_keyed_dict_as_cases_is_refused),
    ("CP5-X03", x03_a_checkpoint_missing_done_when_or_cases_entirely_is_refused),
    ("CP5-X04", x04_non_dict_checkpoint_or_enumerated_items_is_refused_cleanly_never_a_traceback),
    ("CP5-X05", x05_a_non_list_override_is_refused_never_crashes_never_passes_by_coincidence),
    ("CP5-X06", x06_unwrapped_enumerated_done_when_or_cases_is_refused_not_injected_as_garbage),
    ("CP5-X07", x07_an_enumerated_case_with_no_id_is_refused_as_invalid_shape_not_misreported),
    ("CP5-X08", x08_reword_drop_reorder_refusals_report_the_documented_reason_and_index),
    ("CP5-X09", x09_the_id_collision_refusal_reports_the_documented_reason),
    ("CP5-X10", x10_the_documented_exit_2_usage_error_paths_are_exercised),
    ("CP5-X11", x11_every_field_of_a_real_planned_case_shape_survives_the_merge_unchanged),
    ("CP5-X12", x12_an_unrecognized_enumerated_items_key_is_refused_not_treated_as_nothing_to_add),
    ("CP5-X13", x13_sibling_case_id_collisions_are_attributed_to_the_right_source),
    ("CP5-X14", x14_an_enumerated_case_missing_given_when_then_or_with_a_blank_id_is_refused),
]


if __name__ == "__main__":
    main()
