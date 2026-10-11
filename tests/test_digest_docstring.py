#!/usr/bin/env python3
"""
CP3 tests: the spec-digest helper's docstring states the exact BMAD done rule.   python3 tests/test_digest_docstring.py

`_related_spec_items` in skills/ratchet-orchestrator/scripts/ratchet.py already decides a BMAD story is "done"
with an exact, case/space-tolerant comparison (`str(status).strip().lower() == "done"`), but its docstring still
said a story counts when its status text "contains" done -- a wider rule than the code actually applies. This
checkpoint only fixes the docstring (and lists this file in README.md's test list); the runtime rule does not
change.

T01/T02 read the docstring straight off disk with `ast` (no subprocess, no project/`.ratchet` setup needed):
parse skills/ratchet-orchestrator/scripts/ratchet.py, find the `_related_spec_items` FunctionDef, and read its
docstring with `ast.get_docstring`.

T03 checks (a) the runtime rule is unchanged, by importing the engine module directly (`import ratchet as rt`,
the same direct-call style tests/test_objective.py uses) and calling `_related_spec_items` on a plain temp
folder (no git, no .ratchet) holding three hand-written docs/stories/*.md BMAD files -- a done one, a
not-done one, and an oddly-cased/spaced done one -- and (b) that README.md's test list names this file with a
runnable `python3 tests/test_digest_docstring.py` command, in the same place every other tests/test_*.py is
listed (the shape tests/test_docs.py's CP6-T05 check requires).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = ROOT / "README.md"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP3-[TX]\d+[a-z]?)\]")


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


# ------------------------------------------------------------------------------------------------ fixtures / helpers

def _func_docstring(name: str) -> str:
    """The docstring of the top-level function `name` in ratchet.py, read with ast (no import, no subprocess)."""
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"), filename=str(ENGINE))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"function {name!r} not found in {ENGINE}")


def _bmad_clause(doc: str) -> str:
    """The stretch of the docstring that talks about BMAD stories, or '' if it never mentions BMAD."""
    idx = doc.lower().find("bmad")
    return doc[idx: idx + 300] if idx >= 0 else ""


def _bmad_story(d: Path, sid: str, fname: str, title: str, status: str) -> Path:
    """docs/stories/<fname>, the same minimal shape spec_sources.bmad_stories' docs/stories/ fallback reads
    (no bmad-core config needed), matching tests/test_digest_specs.py's own `_bmad_story` fixture."""
    p = d / "docs" / "stories" / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# Story {sid}: {title}\n\n## Status\n\n{status}\n\n## Acceptance Criteria\n\n1. It works\n")
    return p


# ------------------------------------------------------------------------------------------------ CP3-T01

def t01_no_containing_wording():
    doc = _func_docstring("_related_spec_items")
    check("[CP3-T01] _related_spec_items has a docstring", bool(doc), "")
    bad = re.search(r'contain\w*\s+["“]?done["”]?', doc, re.I)
    check("[CP3-T01] the docstring does not say a BMAD story's status 'contains'/'containing' done",
          bad is None, doc)


# ------------------------------------------------------------------------------------------------ CP3-T02

def t02_exact_match_rule_stated():
    doc = _func_docstring("_related_spec_items")
    clause = _bmad_clause(doc)
    check("[CP3-T02] the docstring mentions BMAD stories at all", bool(clause), doc)
    has_exact = re.search(r'exactly\s+["“]?done["”]?|==\s*["“]?done["”]?', clause, re.I) \
        is not None
    has_case = re.search(r'case.?insensitiv\w*|ignoring case|regardless of case|lower[- ]?cas\w*', clause, re.I) \
        is not None
    has_space = re.search(r'surrounding\s+(white)?space|ignoring\s+(surrounding\s+)?(white)?space|trimm?\w*|'
                           r'stripp?\w*', clause, re.I) is not None
    check("[CP3-T02] the clause says the status must equal 'done' exactly, not merely contain it",
          has_exact, clause)
    check("[CP3-T02] the clause says the comparison ignores case", has_case, clause)
    check("[CP3-T02] the clause says the comparison ignores surrounding whitespace", has_space, clause)


# ------------------------------------------------------------------------------------------------ CP3-T03

def t03a_runtime_done_rule_unchanged():
    import ratchet as rt  # noqa: E402  (imported late so T01/T02 never need the module to exist)
    d = Path(_tmp.mkdtemp(prefix="ratchet-digest-docstring-"))
    _bmad_story(d, "1.1", "1.1.done.md", "T03DONE a done story", "Done")
    _bmad_story(d, "1.2", "1.2.notdone.md", "T03NOTDONE a not-done story", "In Progress")
    _bmad_story(d, "1.3", "1.3.oddcase.md", "T03ODD an oddly-cased/spaced done story", " DONE ")
    items = rt._related_spec_items(rt.Ctx(d), set())
    lines = [line for _, line in items]
    joined = "\n".join(lines)
    check("[CP3-T03] the done story is included", any("T03DONE" in l for l in lines), joined)
    check("[CP3-T03] the not-done story is excluded", not any("T03NOTDONE" in l for l in lines), joined)
    check("[CP3-T03] the oddly-cased/spaced done story is still included",
          any("T03ODD" in l for l in lines), joined)


def t03b_readme_lists_this_test_file():
    text = README.read_text(encoding="utf-8")
    heads = list(re.finditer(r"(?m)^#{2,3}\s+(.+)$", text))
    ref_start = next((m.start() for m in heads if re.search(r"reference", m.group(1), re.I)), -1)
    check("[CP3-T03] README has a reference section", ref_start >= 0, "")
    ref = text[ref_start:] if ref_start >= 0 else ""
    check("[CP3-T03] the reference section names tests/test_digest_docstring.py with a runnable command",
          bool(ref) and re.search(r"python3?\s+tests/test_digest_docstring\.py", ref) is not None, ref[-800:])
    other_tests = ["test_digest.py", "test_digest_specs.py", "test_docs.py"]
    check("[CP3-T03] it sits in the same place every other tests/test_*.py command is listed",
          all(re.search(r"python3?\s+tests/" + re.escape(f), ref) for f in other_tests), ref[-800:])


TESTS = [
    ("CP3-T01", t01_no_containing_wording),
    ("CP3-T02", t02_exact_match_rule_stated),
    ("CP3-T03", t03a_runtime_done_rule_unchanged),
    ("CP3-T03", t03b_readme_lists_this_test_file),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    seen = {}
    for cid, _ in TESTS:
        seen[cid] = True
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid in seen]
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
