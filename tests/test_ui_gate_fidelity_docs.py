#!/usr/bin/env python3
"""
CP6 tests: tell reviewers and planners the new rules.   python3 tests/test_ui_gate_fidelity_docs.py

After the capture-fidelity work (a state's own width, the capture check and scaffolded stubs, and the reviewers'
`insufficient_evidence` list), the reviewers' instructions and the docs must say so, or the next agent will keep
doing the old thing (report an undemonstrable browser case as a code fix and burn a review round on it).

Method: no browser and no network.
  * The INPUT.md the real `ui_review.stage()` writes (imported with sys.path.insert, called in process) is read for
    the behavior reviewer, with and without browser cases (CP6-T01..T03).
  * The repository's own .md files (the behavior reviewer prompt, the UI compare skill, the test planner skill, the
    orchestrator skill and schemas.md) are read only inside the cases that need them and matched with short,
    tolerant, case-insensitive phrases, so rewording does not break a case; a missing file fails its case, never
    crashes the run.
  * CP6-T17 runs the existing UI-gate and doc suites as separate processes in parallel (never run_all.py).

The teeth of the gate are pinned on purpose (CP6-T06, T10, T14, T15): insufficient_evidence is only for a case with
no usable evidence, never for something the reviewer saw differ, and a real difference still fails the gate and goes
to the implementer.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
"status": "passed"|"failed", "message": "..."}]}. A case that raises is recorded as failed, never dropped.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402

UIC_SCRIPTS = ROOT / "skills" / "ratchet-ui-compare" / "scripts"
UI_REVIEW = UIC_SCRIPTS / "ui_review.py"
UIC_SKILL = ROOT / "skills" / "ratchet-ui-compare" / "SKILL.md"
PLANNER_SKILL = ROOT / "skills" / "ratchet-tdd-planner" / "SKILL.md"
ORCH_SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
REVIEWER = ROOT / "agents" / "ratchet-behavior-reviewer.md"
README = ROOT / "README.md"
INVENTORY = HERE / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS = Path(__file__).resolve()

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP6-T\d+)\]")
IE_FIELD = "insufficient_evidence"
IE_PREFIX = "INSUFFICIENT-EVIDENCE:"
EM, EN = chr(0x2014), chr(0x2013)
# Em plus en dash characters each doc held before this step; a doc may lose some, never gain one.
DASH_BASELINE = {"ui-compare skill": 2, "schemas.md": 59, "orchestrator skill": 26, "behavior reviewer": 4,
                 "planner skill": 2}
# Planner skill pins: the lines before and after its `browser` bullet, as they were before this step.
PLANNER_BEFORE_SHA = "9750c1f4f5f15da2"
PLANNER_AFTER_SHA = "7d7ebc9f7ea46694"
# Orchestrator Step 4 lines that may not change at all (the intro, the flow line, the already approved lint bullet).
ORCH_STEP4_FIXED = {"011ad53e0315c542", "6b92f2217b1382d6", "236381495c689597"}


def check(name, cond, detail=""):
    global PASS, FAIL
    cond = bool(cond)
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and cond
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ helpers

def rd(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]


def section(text: str, start: str, end: str | None) -> str:
    """The text from the first `start` up to the next `end` after it (or the end); '' when `start` is missing."""
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start)) if end else -1
    return text[i:j] if j >= 0 else text[i:]


def windows(text: str, needle: str, radius: int = 300) -> list:
    return [text[max(0, m.start() - radius): m.end() + radius] for m in re.finditer(re.escape(needle), text)]


def any_window(text: str, needle: str, *patterns, radius: int = 300) -> bool:
    return any(all(re.search(p, w, re.I | re.S) for p in patterns) for w in windows(text, needle, radius))


def dash_count(text: str) -> int:
    return text.count(EM) + text.count(EN)


def load_ui_review():
    if str(UIC_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(UIC_SCRIPTS))
    import ui_review as ur
    return ur


# ------------------------------------------------------------------------------------------------ staging fixtures

_fx: list = []


def fixture_dir() -> Path:
    if not _fx:
        d = Path(_tmp.mkdtemp(prefix="ratchet-cp6-docs-"))
        for n in ("ref.png", "app.png", "step.png", "phone-ref.png", "phone-app.png"):
            (d / n).write_bytes(b"\x89PNG fixture " + n.encode())
        plan = {"checkpoint": "CP6", "cases": [
            {"id": "CP2-T07", "title": "focus moves to the dialog", "layer": "browser", "ui_state": "phone-checkout",
             "given": "g", "when": "w", "then": "t"},
            {"id": "CP2-T01", "title": "approve works", "layer": "code", "given": "g", "when": "w", "then": "t"}]}
        (d / "plan-browser.json").write_text(json.dumps(plan))
        plan["cases"] = [c for c in plan["cases"] if c["layer"] == "code"]
        (d / "plan-code.json").write_text(json.dumps(plan))
        _fx.append(d)
    return _fx[0]


def manifest(stub: bool = False) -> dict:
    d = fixture_dir()
    states = [
        {"name": "checkout", "scope": "checkout form", "reference": {"screenshot": str(d / "ref.png")},
         "app": {"screenshot": str(d / "app.png"),
                 "journey": [{"i": 1, "step": {"click": "#pay"}, "ok": True, "url": "/pay",
                              "screenshot": str(d / "step.png")}]}},
        {"name": "phone-checkout", "scope": "checkout form at phone width", "viewport": {"width": 390},
         "reference": {"screenshot": str(d / "phone-ref.png")}, "app": {"screenshot": str(d / "phone-app.png")}},
    ]
    if stub:
        states.append({"name": "stub-CP2-T07", "stub": True, "cases": ["CP2-T07"],
                       "scope": "STUB, UNREVIEWED: add the journey", "reference": {}, "app": {}})
    return {"checkpoint": "CP6", "viewport": {"width": 1280, "height": 800}, "states": states}


def stage_input(kind: str, plan: str | None, stub: bool = False):
    """(INPUT.md text, the staged file names) from the real ui_review.stage(); the staged folder is removed."""
    ur = load_ui_review()
    cases = fixture_dir() / plan if plan else None
    td = ur.stage(manifest(stub), None, "checkout", cases, kind)
    try:
        return (Path(td) / "INPUT.md").read_text(), sorted(p.name for p in Path(td).iterdir())
    finally:
        shutil.rmtree(td, ignore_errors=True)


def browser_window(text: str) -> str:
    """INPUT.md around the 'Browser-only test cases you must also judge' line (a few lines before, the rest after)."""
    lines = text.split("\n")
    idx = next((i for i, ln in enumerate(lines) if "Browser-only test cases you must also judge" in ln), None)
    return "" if idx is None else "\n".join(lines[max(0, idx - 2): idx + 15])


# ------------------------------------------------------------------------------------------------ T01..T03: INPUT.md

def t01_input_names_unjudged_list():
    print("CP6-T01: the behavior reviewer's INPUT.md tells it to list a case it cannot judge as insufficient_evidence")
    text, files = stage_input("behavior", "plan-browser.json")
    win = browser_window(text)
    check("[CP6-T01] setup: the Browser-only line is there and browser-cases.json is staged",
          bool(win) and "browser-cases.json" in files, files)
    check("[CP6-T01] next to that line INPUT.md names the insufficient_evidence list", IE_FIELD in win, win[-300:])
    check("[CP6-T01] it ties the list to a case the reviewer cannot judge from the captured evidence",
          re.search(r"(cannot|can't|can not|unable to|not able to|could not|couldn't)\b[^.\n]{0,60}"
                    r"\b(judge|judged|show|shown|demonstrat\w*|prove|proved|determine|tell)", win, re.I) is not None
          and re.search(r"evidence|captur", win, re.I) is not None, win[-300:])
    check("[CP6-T01] it asks for the case id, the state and what is missing",
          re.search(r"\bcase\b", win, re.I) and re.search(r"\bstate\b", win, re.I)
          and re.search(r"missing", win, re.I), win[-300:])
    check("[CP6-T01] it does not ask for the old 'not demonstrable ... fixable_in_code' wording",
          re.search(r"not demonstrable|fixable_in_code", win, re.I) is None, win[-300:])


def t02_input_never_a_difference():
    print("CP6-T02: the unjudged list is never a difference and never used for one the reviewer saw")
    text, _ = stage_input("behavior", "plan-browser.json")
    win = browser_window(text)
    check("[CP6-T02] setup: the instruction exists next to the browser-cases line", IE_FIELD in win, win[-300:])
    check("[CP6-T02] a case it cannot judge is not reported as a difference",
          re.search(r"\b(not|never|don't|do not|instead of)\b[^.\n]{0,80}\bdifferenc", win, re.I) is not None, win[-300:])
    check("[CP6-T02] the list is never used for a difference it did see (a seen failure stays a normal difference)",
          any(re.search(r"\b(never|must not|do not|don't|not for|only)\b", s, re.I)
              and re.search(r"\b(saw|seen|see|shows?|showed|failing|fails|failed)\b", s, re.I)
              and re.search(r"differenc|fail", s, re.I) for s in sentences(win)), win[-400:])


def t03_only_behavior_with_browser_cases():
    print("CP6-T03: only a behavior review with browser cases gets the instruction; the rest is staged as before")
    new, new_files = stage_input("behavior", "plan-browser.json")
    code_only, code_files = stage_input("behavior", "plan-code.json")
    no_cases, no_files = stage_input("behavior", None)
    visual_plain, _ = stage_input("visual", None)
    visual, vis_files = stage_input("visual", "plan-browser.json")
    check("[CP6-T03] the behavior review with a browser case has the instruction and browser-cases.json",
          IE_FIELD in new and "browser-cases.json" in new_files, new_files)
    check("[CP6-T03] a code-only plan has neither the instruction nor browser-cases.json",
          IE_FIELD not in code_only and "browser-cases.json" not in code_files, code_files)
    check("[CP6-T03] no --cases has neither", IE_FIELD not in no_cases and "browser-cases.json" not in no_files, no_files)
    check("[CP6-T03] the visual reviewer has neither, and its INPUT.md is the same with and without a plan",
          IE_FIELD not in visual and "browser-cases.json" not in vis_files and visual == visual_plain, vis_files)
    check("[CP6-T03] a code-only plan and no --cases stage the same INPUT.md, with no Browser-only line",
          code_only == no_cases and "Browser-only" not in code_only, code_only[-300:])
    base = no_cases.rstrip("\n").split("\n")
    lines = new.rstrip("\n").split("\n")
    at = next((i for i, ln in enumerate(lines) if "Browser-only test cases you must also judge" in ln), len(lines))
    while at > 0 and lines[at - 1] == "":  # the blank line that separates the states from the browser line
        at -= 1
    check("[CP6-T03] every state, screenshot and journey line is listed exactly where it was, before the browser line",
          at == len(base) and lines[:at] == base and "## State `phone-checkout`" in new and "viewport: 390x800" in new,
          (at, len(base)))


# ------------------------------------------------------------------------------------------------ T04..T06: reviewer

def browser_bullet(prompt: str) -> str:
    return next((ln for ln in prompt.split("\n") if ln.lstrip().startswith("- **browser-cases.json**")), "")


def t04_reviewer_no_code_fix_for_undemonstrable():
    print("CP6-T04: the behavior reviewer is no longer told to report an undemonstrable case as a code fix")
    prompt = rd(REVIEWER)
    check("[CP6-T04] setup: the prompt is readable and has the browser-cases bullet", bool(browser_bullet(prompt)))
    bad = []
    for s in sentences(prompt):
        if re.search(r"not demonstrable|undemonstrable|add a journey step", s, re.I):
            rest = re.sub(r"not demonstrable|undemonstrable", "", s, flags=re.I)
            if re.search(r"fixable_in_code\W+true|\bminor\b", s, re.I) and not re.search(
                    r"\b(never|don't|do not|instead|rather than|no longer|must not)\b", rest, re.I):
                bad.append(s[:160])
    check("[CP6-T04] no sentence pairs 'not demonstrable' / 'add a journey step' with fixable_in_code: true or minor",
          bad == [], bad)
    check("[CP6-T04] the old wording 'case CPn-T07 not demonstrable' is gone",
          "CPn-T07 not demonstrable" not in prompt and "add a journey step for it" not in prompt)


def t05_reviewer_uses_insufficient_evidence():
    print("CP6-T05: the behavior reviewer puts a case it cannot judge in insufficient_evidence")
    prompt = rd(REVIEWER)
    bullet = browser_bullet(prompt)
    check("[CP6-T05] the browser-cases bullet names insufficient_evidence", IE_FIELD in bullet, bullet[-300:])
    check("[CP6-T05] it asks for the case, the state and what is missing",
          re.search(r"\bcase\b", bullet, re.I) and re.search(r"\bstate\b", bullet, re.I)
          and re.search(r"missing", bullet, re.I), bullet[-300:])
    check("[CP6-T05] it says the capture spec is what gets fixed, not the app",
          re.search(r"capture spec|states/CPn", bullet, re.I) is not None, bullet[-300:])
    out = section(prompt, "## Output", None)
    check("[CP6-T05] the Output JSON block lists an insufficient_evidence field next to differences",
          '"insufficient_evidence"' in out and '"differences"' in out
          and abs(out.index('"insufficient_evidence"') - out.index('"differences"')) < 500, out[-400:])


def t06_reviewer_keeps_its_teeth():
    print("CP6-T06: a seen failure is still a difference; insufficient_evidence is only for a case with no usable evidence")
    prompt = rd(REVIEWER)
    bullet = browser_bullet(prompt)
    rules = section(prompt, "## Rules", "## Output")
    txt = bullet + "\n" + rules
    check("[CP6-T06] setup: the bullet and the Rules section exist", bool(bullet) and bool(rules))
    check("[CP6-T06] a case the evidence shows failing is still reported as a difference",
          re.search(r"report a difference", bullet, re.I) is not None
          and re.search(r"evidence shows|shows it failing|\bseen\b|\bsaw\b", bullet, re.I) is not None, bullet[-300:])
    check("[CP6-T06] insufficient_evidence is only for a case the evidence cannot show",
          any_window(txt, IE_FIELD, r"\bonly\b",
                     r"(cannot|can't|can not|no usable|no evidence|not (shown|captured|exercised)|unable)"), txt[-400:])
    check("[CP6-T06] it is never a way to turn a difference the reviewer saw into something else",
          any_window(txt, IE_FIELD, r"\b(never|must not|do not|don't|not a way|not for|not to be used)\b",
                     r"\b(saw|seen|see|shows?|showed|failing|fails?|differ\w*)\b"), txt[-400:])
    check("[CP6-T06] the rule 'PASS only when no difference has fixable_in_code: true' is still there",
          re.search(r"PASS`? only when no difference has `?fixable_in_code: true", rules) is not None, rules[-300:])
    check("[CP6-T06] the prompt gains no em or en dash", dash_count(prompt) <= DASH_BASELINE["behavior reviewer"],
          dash_count(prompt))


# ------------------------------------------------------------------------------------------------ T07..T10: UI compare

def uic_sections(skill: str):
    return (section(skill, "## 2.", "## 3."), section(skill, "## 3.", "## 4."),
            section(skill, "## 5.", "## What never happens"))


def t07_skill_own_width():
    print("CP6-T07: the UI compare skill explains a state can have its own width")
    skill = rd(UIC_SKILL)
    s2, s3, _ = uic_sections(skill)
    check("[CP6-T07] setup: the capture spec and capture sections are there", bool(s2) and bool(s3))
    check("[CP6-T07] a state may carry its own viewport, for example a phone width",
          re.search(r"own\s+`?viewport", s2, re.I) is not None and re.search(r"phone", s2, re.I) is not None, s2[:300])
    check("[CP6-T07] the state's width overrides the spec-level width for that state only",
          re.search(r"overrid|takes precedence|beats|instead of the spec|replaces the spec|wins over", s2 + s3, re.I)
          is not None and re.search(r"that state only|only (that|this|the) state|only for (that|this|the) state",
                                    s2 + s3, re.I) is not None, s2[:300])
    check("[CP6-T07] other states keep the spec width or the default",
          re.search(r"\bother\b[^.\n]{0,60}(spec|default)|(spec|default)[^.\n]{0,60}\bother states", s2 + s3, re.I)
          is not None, s2[:300])
    check("[CP6-T07] the reviewers' input shows each state's width",
          any(re.search(r"reviewer|INPUT\.md", s, re.I) and re.search(r"width|viewport", s, re.I)
              and re.search(r"each state|every state|its own|per state|state's", s, re.I) for s in sentences(skill)),
          "no sentence")
    check("[CP6-T07] the capture section says each state is captured at its own width",
          re.search(r"width|viewport", s3, re.I) is not None, s3[:300])


def t08_skill_capture_check_and_scaffold():
    print("CP6-T08: the UI compare skill adds the capture check, and scaffold, before capture")
    skill = rd(UIC_SKILL)
    s2, s3, _ = uic_sections(skill)
    lint_at = skill.find("ui_review.py lint")
    capture_at = skill.find("capture.mjs --spec")
    check("[CP6-T08] a lint step on the capture spec and the test plan comes before the capture command",
          0 <= lint_at < capture_at and "--spec" in s2 and "--cases" in s2, (lint_at, capture_at))
    lint_txt = "\n".join(windows(s2, "ui_review.py lint", 900)) if lint_at >= 0 else ""
    check("[CP6-T08] it warns about a browser case with no state",
          re.search(r"(no|missing|without)\s+(capture\s+)?state", lint_txt, re.I) is not None, lint_txt[:300])
    check("[CP6-T08] it warns about a browser case with no journey steps",
          re.search(r"(no|without|empty|missing)\s+(journey|steps)", lint_txt, re.I) is not None, lint_txt[:300])
    check("[CP6-T08] it warns about a browser case at the wrong width",
          re.search(r"wrong\s+(width|viewport)|(width|viewport)[^.\n]{0,40}(does not|doesn't|differs|mismatch|wrong)"
                    r"|different (width|viewport)", lint_txt, re.I) is not None, lint_txt[:300])
    check("[CP6-T08] it names the case in each warning, and only warns",
          re.search(r"nam(es|ing|ed)|by name|one warning per|WARN line per", lint_txt, re.I) is not None
          and re.search(r"only warns|just warns|does not (stop|block|fail)|still proceeds|exit 0", lint_txt, re.I)
          is not None, lint_txt[:300])
    check("[CP6-T08] --scaffold is described as adding stub states for what is missing",
          re.search(r"--scaffold[^.\n]{0,160}stub|stub[^.\n]{0,160}--scaffold", lint_txt, re.I) is not None, lint_txt[:300])


def t09_skill_stub_marked_unreviewed_never_passes():
    print("CP6-T09: the UI compare skill says a stub is marked, unreviewed and never passes")
    skill = rd(UIC_SKILL)
    stubs = " ".join(s for s in sentences(skill) if re.search(r"\bstub", s, re.I))
    check("[CP6-T09] a stub state is marked as a stub and unreviewed",
          re.search(r"stub: true|marked", stubs, re.I) is not None and re.search(r"unreviewed", stubs, re.I) is not None,
          stubs[:300])
    check("[CP6-T09] a review whose capture still holds an unfilled stub stops before any reviewer runs",
          re.search(r"(refuses to start|stops|will not start|does not start)[^.\n]{0,100}stub|"
                    r"stub[^.\n]{0,120}(before any reviewer|refuses|stops)", skill, re.I) is not None, stubs[:300])
    check("[CP6-T09] a stub never lets the gate pass",
          any(re.search(r"\bstub", s, re.I) and re.search(r"\b(never|not)\s+(?:\w+\s+){0,4}pass", s, re.I)
              for s in sentences(skill)), stubs[:300])
    check("[CP6-T09] the way to clear a stub is named: fill in the journey and remove the stub mark",
          re.search(r"journey", stubs, re.I) is not None and re.search(r"remove[^.\n]{0,30}stub|stub[^.\n]{0,30}remov",
                                                                         stubs, re.I) is not None, stubs[:300])


def bullet_of(text: str, head: str) -> str:
    return next((ln for ln in text.split("\n") if ln.startswith(head)), "")


def t10_skill_routing():
    print("CP6-T10: an unjudged case goes to the capture spec; real differences keep going to the implementer")
    skill = rd(UIC_SKILL)
    _, _, s5 = uic_sections(skill)
    check("[CP6-T10] section 5 names INSUFFICIENT-EVIDENCE", "INSUFFICIENT-EVIDENCE" in s5, s5[:200])
    check("[CP6-T10] it lists the cases case by case",
          any_window(s5, "INSUFFICIENT-EVIDENCE", r"case[ -]by[ -]case|each (unjudged )?case|every (unjudged )?case|by name"
                     r"|nam(es|ing)"), s5[:300])
    check("[CP6-T10] it means fix states/CPn.json and capture again, not spawn the implementer",
          any_window(s5, "INSUFFICIENT-EVIDENCE", r"states/CPn\.json|capture spec", r"re-?captur|capture again|step 3",
                     r"\b(not|never|don't|do not)\b[^.\n]{0,60}(implementer|app code)"), s5[:300])
    fail = bullet_of(s5, "- **FAIL**")
    check("[CP6-T10] FAIL still spawns ratchet-implementer with every reported difference, unchanged",
          "ratchet-implementer" in fail and re.search(r"\bunchanged\b", s5, re.I) is not None
          and re.search(r"\b(every|all)\b[^.\n]{0,40}differenc", s5, re.I) is not None, fail[:300])
    check("[CP6-T10] when both happen the differences go to the implementer and the unjudged cases are listed separately",
          any(re.search(r"\bboth\b", w, re.I) and re.search(r"implementer", w, re.I) and re.search(r"separate", w, re.I)
              for w in [s5[max(0, m.start() - 300): m.end() + 400] for m in re.finditer(r"\bboth\b", s5, re.I)]), s5[:300])
    check("[CP6-T10] a gate with an unjudged case never passes",
          any(re.search(r"unjudged|insufficient", s, re.I) and re.search(r"\b(never|not)\s+(?:\w+\s+){0,3}pass", s, re.I)
              for s in sentences(s5)), s5[:300])
    first = next((ln for ln in skill.split("\n") if "ask ui-respec" in ln), "")
    check("[CP6-T10] the first line holding 'ask ui-respec' is still the real ui-respec paragraph",
          "word for word" in first and "first reply" in first, first[:120])
    check("[CP6-T10] the skill gains no em or en dash", dash_count(skill) <= DASH_BASELINE["ui-compare skill"],
          dash_count(skill))


# ------------------------------------------------------------------------------------------------ T11: planner

def t11_planner_browser_note():
    print("CP6-T11: the test planner's browser-case note tells planners to name the capture state and width")
    lines = rd(PLANNER_SKILL).split("\n")
    idx = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("- `browser`")), None)
    check("[CP6-T11] setup: the browser bullet exists", idx is not None)
    if idx is None:
        return
    bullet = lines[idx]
    check("[CP6-T11] the bullet still says to keep browser cases few", re.search(r"keep them few", bullet, re.I) is not None,
          bullet[:200])
    check("[CP6-T11] a browser case names its ui_state, the capture state that shows it",
          "ui_state" in bullet and re.search(r"capture state", bullet, re.I) is not None, bullet[:300])
    check("[CP6-T11] and its viewport when the width is not the default", "viewport" in bullet
          and re.search(r"default|width", bullet, re.I) is not None, bullet[:300])
    check("[CP6-T11] the capture check (lint) holds the case to that and warns by name when it does not match",
          re.search(r"\blint\b|capture check", bullet, re.I) is not None and re.search(r"warn", bullet, re.I) is not None,
          bullet[:300])
    check("[CP6-T11] the rest of the skill is byte-identical to before",
          sha("\n".join(lines[:idx])) == PLANNER_BEFORE_SHA and sha("\n".join(lines[idx + 1:])) == PLANNER_AFTER_SHA,
          (sha("\n".join(lines[:idx])), sha("\n".join(lines[idx + 1:]))))
    check("[CP6-T11] the planner skill gains no em or en dash", dash_count("\n".join(lines)) <= DASH_BASELINE["planner skill"])


# ------------------------------------------------------------------------------------------------ T12..T14: schemas.md

def t12_schemas_section5():
    print("CP6-T12: schemas.md section 5 describes a state's own width, its cases list and the stub mark")
    s5 = section(rd(SCHEMAS), "## 5.", "## 6.")
    check("[CP6-T12] setup: section 5 is there", bool(s5))
    m = re.search(r"```json\n(.*?)```", s5, re.S)
    try:
        doc = json.loads(m.group(1)) if m else None
    except ValueError:
        doc = None
    check("[CP6-T12] the first JSON example still parses and holds a state with its own viewport",
          isinstance(doc, dict) and any(isinstance(st, dict) and "viewport" in st for st in doc.get("states", [])), m)
    prose = s5.replace(m.group(0), "") if m else s5
    check("[CP6-T12] the text says a state may carry viewport",
          re.search(r"state['`]?s? (own )?`?viewport|`viewport`", prose, re.I) is not None, prose[:200])
    check("[CP6-T12] the text says cases lists the test-case ids a state demonstrates",
          re.search(r"`cases`[^.\n]{0,120}\b(id|ids|test case|case)\b", prose, re.I) is not None, prose[:200])
    check("[CP6-T12] the text describes stub as unreviewed",
          re.search(r"`?stub`?[^.\n]{0,200}unreviewed|unreviewed[^.\n]{0,200}stub", prose, re.I) is not None, prose[:200])
    check("[CP6-T12] a stub never lets the gate pass",
          any(re.search(r"\bstub", s, re.I) and re.search(r"\b(never|not)\s+(?:\w+\s+){0,4}pass", s, re.I)
              for s in sentences(prose)), prose[-400:])
    check("[CP6-T12] schemas.md gains no em or en dash", dash_count(rd(SCHEMAS)) <= DASH_BASELINE["schemas.md"],
          dash_count(rd(SCHEMAS)))


def t13_schemas_section6_entry():
    print("CP6-T13: schemas.md section 6 describes the insufficient_evidence entry and where it ends up")
    s6 = section(rd(SCHEMAS), "## 6.", "## 7.")
    check("[CP6-T13] setup: section 6 is there", bool(s6))
    check("[CP6-T13] a reviewer file may hold an optional insufficient_evidence list",
          IE_FIELD in s6 and re.search(r"optional", s6, re.I) is not None, s6[:200])
    check("[CP6-T13] each entry is {case, state, missing}",
          any_window(s6, IE_FIELD, r"\bcase\b", r"\bstate\b", r"\bmissing\b", radius=400), s6[:200])
    check("[CP6-T13] the list is kept apart from differences",
          any_window(s6, IE_FIELD, r"apart|separate|not a difference|not in `?differences|instead of", radius=400), s6[:200])
    check("[CP6-T13] combined.json carries a separate insufficient_evidence list",
          any_window(s6, "combined.json", IE_FIELD, radius=500) or
          any_window(s6, IE_FIELD, r"combined", r"separate|own|also", radius=500), s6[-300:])
    check("[CP6-T13] a malformed entry (no case) still blocks",
          any_window(s6, IE_FIELD, r"malformed|no `?case|without a `?case|missing `?case|not a list", r"block", radius=600),
          s6[-300:])


def t14_schemas_verdict_order():
    print("CP6-T14: schemas.md states the combined verdict order and that real differences keep their teeth")
    s6 = section(rd(SCHEMAS), "## 6.", "## 7.")
    p = s6.find(IE_PREFIX)
    check("[CP6-T14] setup: section 6 spells out the INSUFFICIENT-EVIDENCE: prefix", p >= 0, s6[-200:])
    if p >= 0:
        pre, post = s6[:p], s6[p:p + 700]
        f = pre.rfind("FAIL")
        i = pre.rfind("INVALID", 0, max(f, 0)) if f >= 0 else -1
        check("[CP6-T14] in order: a reviewer's INVALID, then FAIL for any difference, then INSUFFICIENT-EVIDENCE",
              i >= 0 and f >= 0 and f - i < 700 and p - f < 800, (i, f, p))
        check("[CP6-T14] a difference gives FAIL with all differences kept unchanged for the implementer",
              re.search(r"unchanged", pre[f - 400: p + 100], re.I) is not None
              and re.search(r"implementer", s6, re.I) is not None, pre[max(0, f - 200): p][-400:])
        check("[CP6-T14] only with no difference, an unjudged entry or an unfilled stub gives INVALID",
              re.search(r"no differenc|only when|without (a|any) differenc|only if", pre[f: p + 100], re.I) is not None
              and re.search(r"stub", pre[f - 200: p + 400], re.I) is not None, pre[f: p + 100])
        check("[CP6-T14] otherwise PASS", re.search(r"\bPASS\b", post) is not None, post[:300])
    check("[CP6-T14] the sentence that ratchet.py recomputes, with any fixable_in_code difference blocking, is still there",
          re.search(r"ratchet\.py ignores the self-reported PASS/FAIL and recomputes: any difference with "
                    r"`fixable_in_code: true` blocks", s6) is not None, s6[:200])


# ------------------------------------------------------------------------------------------------ T15: orchestrator

def t15_orchestrator_step4():
    print("CP6-T15: the orchestrator skill's Gate 2 step says to run the capture check and how to read INSUFFICIENT-EVIDENCE")
    orch = rd(ORCH_SKILL)
    s4 = section(orch, "**Step 4: Gate 2", "**Step 5:")
    check("[CP6-T15] setup: Step 4 is there", bool(s4))
    check("[CP6-T15] the flow runs the capture check (lint) before capture.mjs",
          0 <= s4.find("ui_review.py lint") < s4.find("capture.mjs"), s4[:300])
    check("[CP6-T15] INVALID with INSUFFICIENT-EVIDENCE means fix the capture spec, not the app code or the implementer",
          any_window(s4, "INSUFFICIENT-EVIDENCE", r"capture spec|states/CPn",
                     r"\b(not|never|don't|do not)\b[^.\n]{0,60}(implementer|app code|the app)"), s4[-400:])
    check("[CP6-T15] the FAIL bullet still spawns ratchet-implementer with the blocking differences",
          re.search(r"- FAIL.*ratchet-implementer.*blocking differences", s4) is not None, s4[-300:])
    lines = [ln for ln in s4.split("\n") if ln.strip()]
    base_changed = [ln for ln in lines if sha(ln) not in ORCH_STEP4_FIXED]
    check("[CP6-T15] the change is one line: the intro, the flow line and the lint bullet are byte-identical, and Step 4 "
          "gains at most one line",
          ORCH_STEP4_FIXED <= {sha(ln) for ln in lines} and len(lines) <= 6 and len(base_changed) <= 3,
          (len(lines), len(base_changed)))
    check("[CP6-T15] the skill gains no em or en dash", dash_count(orch) <= DASH_BASELINE["orchestrator skill"],
          dash_count(orch))


# ------------------------------------------------------------------------------------------------ T16: README

def t16_readme_and_inventory():
    print("CP6-T16: the new test file is in the README list and counted correctly against the inventory")
    readme = README.read_text().splitlines()
    start = next((i for i, ln in enumerate(readme) if ln.strip() == "### Running the tests"), None)
    check("[CP6-T16] setup: README has a 'Running the tests' section", start is not None)
    if start is not None:
        end = next((i for i in range(start + 1, len(readme)) if readme[i].startswith("## ") or readme[i].startswith("### ")),
                   len(readme))
        n = [ln.strip() for ln in readme[start:end]].count("python3 tests/test_ui_gate_fidelity_docs.py")
        check("[CP6-T16] the block holds the line `python3 tests/test_ui_gate_fidelity_docs.py` exactly once", n == 1, n)
    check("[CP6-T16] this file is tests/test_ui_gate_fidelity_docs.py",
          THIS.name == "test_ui_gate_fidelity_docs.py" and THIS.exists())
    # The two inventory greps from test-inventory.md, built at run time so this file does not match them itself.
    module_load = "spec_from" + "_file_location"
    source_scan = "|".join(["ENGINE" + r"\.read_text", "as" + "t" + r"\.parse", "as" + "t" + r"\.walk", "get" + "source"])
    src = THIS.read_text()
    hits = [pat for pat in (module_load, source_scan) if re.search(pat, src)]
    inv = INVENTORY.read_text()
    check("[CP6-T16] the inventory rule holds both ways: named exactly when a pattern matches",
          (THIS.name in inv) == bool(hits), (THIS.name in inv, hits))
    g = subprocess.run(["grep", "-lE", module_load + "|" + source_scan, str(THIS)], capture_output=True, text=True,
                       timeout=30)
    check("[CP6-T16] the live grep over this file finds nothing", g.returncode == 1 and g.stdout == "", g.stdout)
    check("[CP6-T16] this file holds no em or en dash", dash_count(src) == 0, dash_count(src))


# ------------------------------------------------------------------------------------------------ T17: guard

GUARD_SUITES = [
    "test_gates", "test_agents_reviewers", "test_agents_docs", "test_docs", "test_docs_accuracy",
    "test_ui_capture_viewport", "test_ui_capture_lint", "test_ui_capture_stub", "test_ui_review_precheck",
    "test_ui_insufficient_evidence", "test_trust_cp5_a", "test_ffl_company_view", "test_ffl_probe_memory",
]
GUARD_TIMEOUT = 1800


def run_suite(name: str):
    """(name, 'ok' or why not, tail of the output). Each suite gets its own empty temp folder as cwd and TMPDIR."""
    path = HERE / f"{name}.py"
    if not path.is_file():
        return name, "missing from disk", ""
    work = Path(_tmp.mkdtemp(prefix=f"ratchet-cp6-{name}-"))
    env = dict(os.environ, TMPDIR=str(work), TEMP=str(work), TMP=str(work), PYTHONUNBUFFERED="1")
    env.pop("RATCHET_TEST_REPORT", None)
    try:
        r = subprocess.run([sys.executable, str(path)], cwd=str(work), env=env, capture_output=True, text=True,
                           timeout=GUARD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return name, f"timed out after {GUARD_TIMEOUT}s", ""
    tail = ((r.stdout or "") + (r.stderr or ""))[-300:]
    failed = re.search(r"\b([1-9]\d*) failed\b", r.stdout or "")
    if r.returncode != 0:
        return name, f"exit {r.returncode}", tail
    if failed:
        return name, f"{failed.group(1)} failed", tail
    return name, "ok", tail


def t17_existing_suites_still_pass():
    print("CP6-T17: the existing UI-gate and doc suites still pass together with the new docs")
    readme = README.read_text().splitlines()
    start = next((i for i, ln in enumerate(readme) if ln.strip() == "### Running the tests"), 0)
    listed = {ln.strip() for ln in readme[start:]}
    wanted = ["python3 tests/test_ui_gate_fidelity_docs.py"] + [f"python3 tests/{n}.py" for n in GUARD_SUITES]
    unlisted = [w for w in wanted if w not in listed]
    check("[CP6-T17] every guarded suite and this file are on the README list", unlisted == [], unlisted)
    absent = [n for n in GUARD_SUITES if not (HERE / f"{n}.py").is_file()]
    check("[CP6-T17] every guarded suite exists on disk", absent == [], absent)
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run_suite, GUARD_SUITES))
    for name, verdict, tail in results:
        check(f"[CP6-T17] {name} passes (exit 0, no failed count)", verdict == "ok", f"{verdict}: {tail}")


# ------------------------------------------------------------------------------------------------ T18: no drift

def t18_docs_match_the_script():
    print("CP6-T18: the docs name exactly what ui_review.py has")
    ur = load_ui_review()
    skill, schemas, orch, prompt = rd(UIC_SKILL), rd(SCHEMAS), rd(ORCH_SKILL), rd(REVIEWER)
    h = subprocess.run([sys.executable, str(UI_REVIEW), "--help"], capture_output=True, text=True, timeout=60)
    m = re.search(r"\{([a-z,-]+)\}", h.stdout)
    subs = m.group(1).split(",") if m else []
    check("[CP6-T18] setup: the script lists its subcommands, including lint", "lint" in subs and len(subs) >= 4, subs)
    missing = [s for s in subs if not re.search(rf"`{s}`|ui_review\.py {s}\b", skill)]
    check("[CP6-T18] every subcommand the script has is named in the UI compare skill", missing == [], missing)
    lh = subprocess.run([sys.executable, str(UI_REVIEW), "lint", "--help"], capture_output=True, text=True, timeout=60)
    flags = sorted(set(re.findall(r"--[a-z][a-z-]*", lh.stdout)) - {"--help"})
    check("[CP6-T18] setup: lint has --scaffold and --cases", "--scaffold" in flags and "--cases" in flags, flags)
    absent = [f for f in flags if f not in skill]
    check("[CP6-T18] every lint flag is named in the UI compare skill", absent == [], absent)
    props = (getattr(ur, "REVIEW_SCHEMA", {}) or {}).get("properties", {})
    check("[CP6-T18] the code spells the field insufficient_evidence and the verdict prefix INSUFFICIENT-EVIDENCE:",
          IE_FIELD in props and getattr(ur, "IE_PREFIX", None) == IE_PREFIX, (list(props), getattr(ur, "IE_PREFIX", None)))
    wrong = [n for n, t in (("skill", skill), ("reviewer prompt", prompt), ("schemas.md", schemas)) if IE_FIELD not in t]
    check("[CP6-T18] the field is spelled insufficient_evidence in the skill, the reviewer prompt and schemas.md",
          wrong == [], wrong)
    wrong = [n for n, t in (("skill", skill), ("schemas.md", schemas), ("orchestrator line", orch))
             if "INSUFFICIENT-EVIDENCE" not in t]
    check("[CP6-T18] the verdict prefix is spelled INSUFFICIENT-EVIDENCE in the skill, schemas.md and the orchestrator line",
          wrong == [], wrong)
    camel = [n for n, t in (("skill", skill), ("reviewer prompt", prompt), ("schemas.md", schemas), ("orchestrator", orch))
             if re.search(r"insufficientEvidence|insufficient-evidence", t)]
    check("[CP6-T18] no doc spells the field a different way (camel case or hyphenated lower case)", camel == [], camel)
    text, _ = stage_input("behavior", "plan-browser.json", stub=True)
    mark = next((ln[2:].split(":")[0] for ln in text.split("\n") if ln.startswith("- STUB")), "")
    check("[CP6-T18] setup: the code writes a stub marker into INPUT.md", mark.startswith("STUB"), text[-300:])
    wrong = [n for n, t in (("skill", skill), ("schemas.md", schemas), ("orchestrator line", orch)) if mark not in t]
    check("[CP6-T18] the stub marker the docs use is the one the code writes", mark != "" and wrong == [], (mark, wrong))


# ------------------------------------------------------------------------------------------------ run everything

TESTS = [
    ("CP6-T01", t01_input_names_unjudged_list), ("CP6-T02", t02_input_never_a_difference),
    ("CP6-T03", t03_only_behavior_with_browser_cases), ("CP6-T04", t04_reviewer_no_code_fix_for_undemonstrable),
    ("CP6-T05", t05_reviewer_uses_insufficient_evidence), ("CP6-T06", t06_reviewer_keeps_its_teeth),
    ("CP6-T07", t07_skill_own_width), ("CP6-T08", t08_skill_capture_check_and_scaffold),
    ("CP6-T09", t09_skill_stub_marked_unreviewed_never_passes), ("CP6-T10", t10_skill_routing),
    ("CP6-T11", t11_planner_browser_note), ("CP6-T12", t12_schemas_section5),
    ("CP6-T13", t13_schemas_section6_entry), ("CP6-T14", t14_schemas_verdict_order),
    ("CP6-T15", t15_orchestrator_step4), ("CP6-T16", t16_readme_and_inventory),
    ("CP6-T17", t17_existing_suites_still_pass), ("CP6-T18", t18_docs_match_the_script),
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
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(e).__name__}: {e}")
            print(f"  ✗ [{cid}] CRASHED: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
