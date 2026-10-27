#!/usr/bin/env python3
"""
CP6 tests: wire mode detection into the planner agents (docs/prose only).
    python3 tests/test_impact_analysis_docs.py

This checkpoint edits no Python/CLI code: it adds prose to agents/ratchet-checkpoint-planner.md and
skills/ratchet-tdd-planner/SKILL.md, and registers CP1-CP5's new test files in README.md. So this test reads
those three files straight off disk and asserts on phrase/keyword presence tied to the right section, the same
way tests/test_digest_docstring.py and tests/test_docs_accuracy.py do for other docs in this repo. No
subprocess, no .ratchet/ setup, no mocked checkpoint run.

  - [CP6-T01] the planner doc states, for every checkpoint, a required classification into exactly one of
              'greenfield-create' / 'brownfield-modify'
  - [CP6-T02] the classify instruction names the field to write ('lane_mode' or 'mode') and says it must
              always be recorded, never silently
  - [CP6-T03] the fail-safe default to brownfield-modify covers BOTH a mixed checkpoint and an unsure one
  - [CP6-T04] the planner doc tells the agent to run impact_analysis.py via Bash before checkpoints.json is
              written, for brownfield/ambiguous checkpoints
  - [CP6-T05] the planner doc tells the agent to fold the script's enumerated items into done_when and notes
  - [CP6-T06] the fold instruction is explicitly additive-only (adds, never removes/reorders/rewords)
  - [CP6-T07] the pre-existing 'How to slice (PLAN mode)' and 'Starting from a spec' section text is still
              present byte-for-byte after CP6 (CP6 only adds prose, it does not rewrite the greenfield method)
  - [CP6-T08] the greenfield path has an explicit note that the impact pass is skipped
  - [CP6-T09] that skip note is scoped to greenfield only, never written into the brownfield instructions
  - [CP6-T10] skills/ratchet-tdd-planner/SKILL.md notes that brownfield re-point items may need their own
              test-edit cases
  - [CP6-T11] README's test list names every CP1-CP5 test file this feature added (all five)
  - [CP6-T12] those five lines sit inside the actual fenced, runnable test-list block, not just in prose

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PLANNER_DOC = ROOT / "agents" / "ratchet-checkpoint-planner.md"
SKILL_DOC = ROOT / "skills" / "ratchet-tdd-planner" / "SKILL.md"
README_DOC = ROOT / "README.md"
SCRIPT_PATH = "skills/ratchet-checkpoint-planner/scripts/impact_analysis.py"
MODE_GREEN = "greenfield-create"
MODE_BROWN = "brownfield-modify"

NEW_TEST_FILES = [
    "test_impact_analysis_mode.py",
    "test_impact_analysis_repoint.py",
    "test_impact_analysis_registration.py",
    "test_impact_analysis_parity_hazard.py",
    "test_impact_analysis_additive.py",
]

# ---- snapshot of agents/ratchet-checkpoint-planner.md's pre-CP6 text ----------------------------------------------
# Captured now, at the base (pre-CP6) tree, straight off the two sections' exact lines (verified against the file
# on disk before any CP6 edit landed). CP6-T07 diffs the CURRENT file against this frozen snapshot, so it still
# catches a later rewrite even once the real file has changed under CP6's own edits.

SNAPSHOT_SLICE = """## How to slice (PLAN mode)
- **Increasing complexity.** CP1 is almost always a skeleton: the route or screen exists, is reachable from navigation, shows a title and empty state, and has permissions wired. CP2 is one deliberately small, real slice. Later checkpoints grow only once earlier decisions are proven. Put the risky or architectural decision early and small (the data model, the permission rule), so a mistake is cheap to fix.
- **Each checkpoint** is one reviewable change (roughly ≤300 lines of diff), leaves the app working, and builds on the previous one. Typical feature: 4–8 checkpoints. Split anything bigger into two features.
- **Vertical, not horizontal.** Prefer "Approve a request (API + button)" over "all API endpoints", then "all UI".
- **Gates per checkpoint:**
  - `behavior: true` unless there's genuinely no logic (then `behavior_skip_reason`).
  - `ui: true` only if this checkpoint changes something a user sees. That includes static pages the project serves or renders (a dashboard, a report page, a generated HTML viewer): if it can be opened in a browser, it can be screenshotted and compared, so it gets the UI gate. Only set `ui:false` when there is truly nothing a person looks at. Pure logic, API, or migration checkpoints set `ui:false` + `ui_skip_reason`.
  - `adversarial: true`, always.
- **risk** (`low` / `normal` / `high`) sets how many independent adversarial reviewers must approve (`review_depth` in config). `normal` is the default risk: use it unless a step clearly belongs in one of the other two. `low`: skeletons, copy or style tweaks, renames. `high`: auth and permissions, money, data migrations, deletion, concurrency, security-sensitive input, anything an eval or lock depends on. Don't inflate it: high-risk review costs the most quota. **A `high`-risk step needs `risk_reason`**, a plain sentence naming which users could be harmed and how if this step gets it wrong (e.g. "a wrong check here would let a corner-cutting agent skip review and ship a bypassed auth check that hurts every signed-in user"); `plan-submit` refuses a new high-risk step with no `risk_reason`.
- **ui_scope** names exactly what the reviewer may judge *at this checkpoint*, and what to ignore because it isn't built yet ("Only the header and empty state; ignore the table"). The scope widens as checkpoints add parts.
- **ui_states** are the distinct visual states worth comparing (empty, loaded, validation error, success toast). Use short kebab-case names. The prototype will expose each one as `?state=<name>`, so keep names stable across checkpoints.
- **tests**: leave `files`/`command` as your best guess from app-scout conventions. The test planner finalizes them.
"""

SNAPSHOT_SPEC = """## Starting from a spec (OpenSpec, Spec Kit, BMAD)
If `<run>/reference/spec-source.json` exists, the team already wrote the spec, and `$RATCHET spec import` has put a **draft** plan in `checkpoints.json` and `tests/CPn.json`. The spec answers *what*; don't re-ask it or add requirements of your own. Refine the draft instead:
- Fill in what the spec doesn't say: `tests.files`/`command`, `risk`, UI gates, `ui_scope`/`ui_states`, plain titles and `done_when`. Merge tiny steps or split big ones as the slicing rules above say.
- **Keep every spec ref.** Each step lists the spec tasks it builds in `spec_refs` (and the requirements it covers); each spec task must be in some step. Keep the `spec` block on each step in step with its `spec_refs` (reviewers read it). If a spec task or scenario really shouldn't be built or tested here (already done elsewhere, out of scope, needs a manual check), add it to the plan's top-level `spec_unmapped` as `{"ref": "...", "reason": "..."}`. `plan-submit` refuses a plan that leaves anything out silently.
- A requirement marked NEEDS CLARIFICATION goes back as a QUESTION, never a guess.
"""

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_ERRORS: dict = {}


def check(cid, name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
    if not cond:
        CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} [{cid}] {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


# ---- doc helpers -----------------------------------------------------------------------------------------------

def read(p) -> str:
    return Path(p).read_text(encoding="utf-8", errors="replace")


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "")


def sentences(text: str) -> list:
    flat = squash(text)
    return [s for s in re.split(r"(?<=[.!?])\s+", flat) if s]


def section(text: str, heading: str, next_prefix: str = "## ") -> str:
    """The lines from the line equal to `heading` (stripped) up to (not including) the next line starting with
    `next_prefix`, or end of file. Empty string when the heading is not found at all."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == heading:
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith(next_prefix):
            end = j
            break
    return "\n".join(lines[start:end])


def near(text: str, anchor: str, window: int = 300) -> list:
    """Squashed-text windows of `window` chars on each side of every occurrence of `anchor`."""
    out = []
    for m in re.finditer(re.escape(anchor), text):
        s, e = max(0, m.start() - window), min(len(text), m.end() + window)
        out.append(text[s:e])
    return out


# ---- CP6-T01: classify instruction names both mode strings ------------------------------------------------------

def classify_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"classif", s, re.I)
            and re.search(r"every checkpoint|each checkpoint", s, re.I)
            and MODE_GREEN in s and MODE_BROWN in s]


def t01():
    text = read(PLANNER_DOC)
    found = classify_sentences(text)
    check("CP6-T01",
          "agents/ratchet-checkpoint-planner.md has a sentence requiring every checkpoint be classified into "
          f"'{MODE_GREEN}' or '{MODE_BROWN}'",
          bool(found), f"no matching sentence found; got {found[:2]}")


# ---- CP6-T02: the mode field must always be recorded, never silently --------------------------------------------

def record_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"lane_mode|\bmode\b", s)
            and re.search(r"record", s, re.I)
            and (re.search(r"never silently", s, re.I) or re.search(r"always record", s, re.I))]


def t02():
    text = read(PLANNER_DOC)
    found = record_sentences(text)
    check("CP6-T02",
          "the classify instruction names a field ('lane_mode' or 'mode') and says it must always be recorded, "
          "never silently",
          bool(found), f"no matching sentence found; got {found[:2]}")


# ---- CP6-T03: fail-safe covers BOTH 'mixed' and 'unsure', both tied to brownfield-modify -------------------------

def mixed_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"mix(ed|es)\b", s, re.I)
            and re.search(r"new and existing|existing and new", s, re.I)
            and MODE_BROWN in s]


def unsure_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"\bunsure\b|not sure|uncertain", s, re.I)
            and MODE_BROWN in s]


def t03():
    text = read(PLANNER_DOC)
    mixed, unsure = mixed_sentences(text), unsure_sentences(text)
    check("CP6-T03", "a sentence ties a checkpoint mixing new and existing files to the brownfield-modify default",
          bool(mixed), f"got {mixed[:2]}")
    check("CP6-T03", "a sentence ties the agent simply being unsure to the SAME brownfield-modify default",
          bool(unsure), f"got {unsure[:2]}")


# ---- CP6-T04: run impact_analysis.py via Bash before checkpoints.json is written ---------------------------------

def bash_before_write_contexts(text: str) -> list:
    squashed = squash(text)
    return [c for c in near(squashed, SCRIPT_PATH)
            if re.search(r"\bBash\b", c)
            and re.search(r"before[^.]{0,80}checkpoints\.json", c, re.I)]


def t04():
    text = read(PLANNER_DOC)
    found = bash_before_write_contexts(text)
    check("CP6-T04",
          f"the planner doc names {SCRIPT_PATH}, says to invoke it via Bash, before checkpoints.json is written",
          bool(found), f"no matching context found around '{SCRIPT_PATH}'")


# ---- CP6-T05: fold the enumerated items into done_when and notes -------------------------------------------------

def fold_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"\bfold\w*\b", s, re.I)
            and "done_when" in s and "notes" in s]


def t05():
    text = read(PLANNER_DOC)
    found = fold_sentences(text)
    check("CP6-T05", "a sentence says the enumerated items are folded into both done_when and notes",
          bool(found), f"got {found[:2]}")


# ---- CP6-T06: the fold is explicitly additive-only ----------------------------------------------------------------

def additive_only_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"additiv\w*", s, re.I)
            and re.search(r"never (remov\w*|reorder\w*|reword\w*)", s, re.I)]


def t06():
    text = read(PLANNER_DOC)
    found = additive_only_sentences(text)
    check("CP6-T06",
          "a sentence claims the fold is additive-only AND pairs it with a 'never remove/reorder/reword' guard",
          bool(found), f"got {found[:2]}")


# ---- CP6-T07: the pre-existing greenfield method text is untouched, byte-for-byte --------------------------------

def t07():
    text = read(PLANNER_DOC)
    cur_slice = section(text, "## How to slice (PLAN mode)")
    cur_spec = section(text, "## Starting from a spec (OpenSpec, Spec Kit, BMAD)")
    check("CP6-T07", "the existing 'How to slice (PLAN mode)' section text is still present byte-for-byte",
          SNAPSHOT_SLICE.rstrip("\n") in cur_slice,
          f"snapshot not found as a substring of current section ({len(cur_slice)} chars)")
    check("CP6-T07", "the existing 'Starting from a spec' section text is still present byte-for-byte",
          SNAPSHOT_SPEC.rstrip("\n") in cur_spec,
          f"snapshot not found as a substring of current section ({len(cur_spec)} chars)")


# ---- CP6-T08/T09: greenfield skip note, scoped to greenfield only -------------------------------------------------

def greenfield_skip_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"greenfield", s, re.I)
            and re.search(r"impact[- ]analysis pass|impact pass", s, re.I)
            and re.search(r"\bskip\w*\b", s, re.I)]


def brownfield_skip_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"brownfield", s, re.I)
            and re.search(r"impact[- ]analysis pass|impact pass", s, re.I)
            and re.search(r"\bskip\w*\b", s, re.I)]


def t08():
    text = read(PLANNER_DOC)
    found = greenfield_skip_sentences(text)
    check("CP6-T08", "the greenfield path has an explicit sentence saying the impact-analysis pass is skipped",
          bool(found), f"got {found[:2]}")


def t09():
    text = read(PLANNER_DOC)
    green, brown = greenfield_skip_sentences(text), brownfield_skip_sentences(text)
    check("CP6-T09", "the skip sentence exists (scoped to greenfield)", bool(green), f"got {green[:2]}")
    check("CP6-T09", "the brownfield-modify instructions never say the impact pass can be skipped",
          not brown, f"found in brownfield context: {brown[:2]}")


# ---- CP6-T10: SKILL.md notes brownfield re-point items may need their own test-edit cases ------------------------

def repoint_test_edit_sentences(text: str) -> list:
    return [s for s in sentences(text)
            if re.search(r"brownfield", s, re.I)
            and re.search(r"re-?point", s, re.I)
            and re.search(r"test-?edit cases|test edit cases|own test[- ]edit", s, re.I)]


def t10():
    text = read(SKILL_DOC)
    found = repoint_test_edit_sentences(text)
    check("CP6-T10",
          "skills/ratchet-tdd-planner/SKILL.md notes that a brownfield checkpoint's re-point items may need "
          "their own test-edit cases",
          bool(found), f"got {found[:2]}")


# ---- CP6-T11/T12: README registers all five CP1-CP5 test files, inside the real fenced test-list block -----------

def readme_fenced_blocks(text: str) -> list:
    """Every fenced ``` ... ``` block's raw inner text, in order."""
    return re.findall(r"```\n(.*?)```", text, re.S)


def t11():
    text = read(README_DOC)
    missing = [f for f in NEW_TEST_FILES if f"python3 tests/{f}" not in text]
    check("CP6-T11", "README names a 'python3 tests/test_X.py' line for every CP1-CP5 test file this feature added",
          not missing, f"missing: {missing}")


def t12():
    text = read(README_DOC)
    blocks = readme_fenced_blocks(text)
    test_list_blocks = [b for b in blocks if "python3 tests/run_all.py" in b or "tests/test_" in b]
    for f in NEW_TEST_FILES:
        in_fence = any(f"python3 tests/{f}" in b for b in test_list_blocks)
        check("CP6-T12", f"'{f}' is registered inside the fenced runnable test-list block, not just in prose",
              in_fence, f"not found in any of {len(test_list_blocks)} candidate fenced blocks")


# ---- CP6-X01 (new, non-planned): the documented "pass cases: []" merge pattern actually succeeds -------------
# Round 3's adversarial findings showed the doc's merge step didn't match the script's real contract (merge
# refuses a checkpoint with no `cases` key at all). This pipes a real checkpoint-shaped payload -- built the way
# the fixed doc now says to build it, with an explicit `cases: []` since no planned test cases exist yet at plan
# time -- through the real `merge` subcommand via subprocess, and asserts it exits 0. A future regression in
# either the doc or the script (e.g. merge tightening its contract again, or the doc drifting from it) would be
# caught here.

import subprocess

IMPACT_SCRIPT = ROOT / "skills" / "ratchet-checkpoint-planner" / "scripts" / "impact_analysis.py"


def x01():
    payload = {
        "checkpoint": {"done_when": ["A manager sees a confirmation."], "cases": []},
        "enumerated_items": {"done_when": ["Re-point the monkeypatch at tests/foo.py:42."]},
    }
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "merge"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    check("CP6-X01",
          "the documented checkpoint-shaped payload with explicit cases: [] merges successfully (exit 0)",
          proc.returncode == 0, f"exit {proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}")
    if proc.returncode == 0:
        merged = json.loads(proc.stdout)["merged"]
        check("CP6-X01",
              "the merged done_when contains both the existing and the newly enumerated line",
              merged["done_when"] == ["A manager sees a confirmation.",
                                       "Re-point the monkeypatch at tests/foo.py:42."],
              f"got {merged}")


TESTS = [
    ("CP6-T01", t01), ("CP6-T02", t02), ("CP6-T03", t03), ("CP6-T04", t04), ("CP6-T05", t05), ("CP6-T06", t06),
    ("CP6-T07", t07), ("CP6-T08", t08), ("CP6-T09", t09), ("CP6-T10", t10), ("CP6-T11", t11), ("CP6-T12", t12),
    ("CP6-X01", x01),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid, _ in TESTS:
        if cid in CASE_ERRORS:
            cases.append({"id": cid, "status": "error", "message": CASE_ERRORS[cid][:300]})
        elif CASE_STATUS.get(cid, False):
            cases.append({"id": cid, "status": "passed", "message": ""})
        else:
            cases.append({"id": cid, "status": "failed", "message": "; ".join(CASE_MESSAGES.get(cid, []))[:300]})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    only = set(sys.argv[1:])
    for cid, fn in TESTS:
        if only and cid not in only and fn.__name__ not in only:
            continue
        CASE_STATUS.setdefault(cid, True)
        print(f"{cid} {fn.__name__}", flush=True)
        try:
            fn()
        except BaseException as e:  # a crash is reported as an error for this case, never a silent drop
            if isinstance(e, KeyboardInterrupt):
                raise
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_ERRORS[cid] = f"crashed: {e}"
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
