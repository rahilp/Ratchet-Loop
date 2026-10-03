#!/usr/bin/env python3
"""
Building from a spec tool's files:   python3 tests/test_specs.py

OpenSpec changes, GitHub Spec Kit features and BMAD stories (v4 and v6) are read as a draft plan; every scenario or
acceptance criterion must have a planned test and every task a step; the spec is locked at plan approval; when a
step passes its gates Ratchet ticks its tasks in the tool's own files (in the same commit); a finished BMAD story is
marked done with Ratchet's evidence; an approved OpenSpec change is archived with OpenSpec's own CLI.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "ratchet-orchestrator" / "scripts"))
import test_gates as g  # noqa: E402
import spec_sources as ss  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:500]}"))


rx = g.rx


def write(root: Path, files: dict):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


# ------------------------------------------------------------------------------------------------ fixtures

OPENSPEC = {
    "openspec/config.yaml": "schema: spec-driven\ncontext: |\n  Plain JavaScript, no frameworks.\n",
    "openspec/specs/ui/spec.md": "# UI\n\n## Requirements\n\n### Requirement: Light only\nThe app SHALL be light.\n",
    "openspec/changes/add-dark-mode/proposal.md": "# Add dark mode\n\n## Why\nPeople read at night.\n",
    "openspec/changes/add-dark-mode/tasks.md":
        "## 1. Theme switch\n- [ ] 1.1 Add the theme store\n- [ ] 1.2 Add the theme toggle button\n\n"
        "## 2. Follow the operating system\n- [ ] 2.1 Read the operating system default setting\n",
    "openspec/changes/add-dark-mode/specs/ui/spec.md": """# Delta for UI

## ADDED Requirements

### Requirement: Theme toggle
The system SHALL let users switch the theme between light and dark.

#### Scenario: Switch to dark
- **WHEN** the user picks dark mode
- **THEN** the page uses the dark palette

#### Scenario: Remember choice
- GIVEN the user picked dark mode
- WHEN they reload the page
- THEN dark mode is still on
- AND no flash of the light theme appears

## MODIFIED Requirements

### Requirement: Default setting
The system SHALL follow the operating system default setting.

#### Scenario: OS is dark
- **WHEN** the operating system prefers dark
- **THEN** the dark palette is used

## REMOVED Requirements

### Requirement: Light only
**Reason**: replaced by the theme toggle
""",
    "openspec/changes/archive/2026-01-01-old-thing/proposal.md": "# Old\n",
}

SPECKIT = {
    ".specify/memory/constitution.md": "# Constitution\n\n1. Every feature has tests first.\n",
    ".specify/feature.json": json.dumps({"feature_directory": "specs/001-photo-albums"}),
    "specs/001-photo-albums/spec.md": """# Feature Specification: Photo albums

**Feature Branch**: `001-photo-albums`
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create an album (Priority: P1)

Users group photos into albums.

**Acceptance Scenarios**:

1. **Given** a user with photos, **When** they create an album, **Then** the album appears in the list
2. **Given** an album, **When** they rename it, **Then** the new name shows

### User Story 2 - Share an album (Priority: P2)

**Acceptance Scenarios**:

1. **Given** an album, **When** the owner shares it, **Then** a link is created

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST let users create albums
- **FR-002**: System MUST keep album names unique per user
- **FR-003**: System MUST expire share links after [NEEDS CLARIFICATION: how long?]
""",
    "specs/001-photo-albums/tasks.md": "# Tasks\n\n## Phase 1: Setup\n- [ ] T001 Create the albums table\n\n"
        "## Phase 3: User Story 1\n- [ ] T002 [P] [US1] Album model in src/albums.js\n- [ ] T003 [US1] Rename endpoint\n\n"
        "## Phase 4: User Story 2\n- [ ] T004 [US2] Share link endpoint\n",
}

BMAD4 = {
    "bmad-core/core-config.yaml": "devStoryLocation: docs/stories\nprd:\n  prdFile: docs/prd.md\n",
    "docs/architecture/coding-standards.md": "# Coding standards\n\n- No globals.\n",
    "docs/stories/1.1.setup.md": "# Story 1.1: Set up\n\n## Status\n\nDone\n\n## Acceptance Criteria\n\n1. It runs\n",
    "docs/stories/1.2.login.md": """# Story 1.2: Log in with email

## Status

Approved

## Story

**As a** user, **I want** to log in, **so that** I see my data.

## Acceptance Criteria

1. Given a registered user, when they submit the right password, then they are logged in
2. Wrong password shows an error and does not log in

## Tasks / Subtasks

- [ ] Build the login form (AC: 1, 2)
  - [ ] Email and password fields
  - [ ] Error message area
- [ ] Session handling (AC: 1)

## Dev Notes

Use the existing session store.

## QA Results
""",
}

BMAD6 = {
    "_bmad/config.toml": "[core]\nproject_name = \"x\"\n",
    "_bmad-output/implementation-artifacts/2-1-sign-up.md": """# Story 2.1: Sign up

Status: ready-for-dev

## Story

As a visitor, I want to sign up, so that I have an account.

## Acceptance Criteria

1. **Given** a visitor **When** they sign up with a new email **Then** an account is created

## Tasks / Subtasks

- [ ] Task 1: Sign-up form (AC: #1)
  - [ ] Subtask 1.1: fields

## Dev Agent Record
""",
    "_bmad-output/implementation-artifacts/sprint-status.yaml":
        "development_status:\n  epic-2: in-progress\n  2-1-sign-up: ready-for-dev\n",
}


def tmp(name, files) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"ratchet-spec-{name}-"))
    write(d, files)
    return d


# ------------------------------------------------------------------------------------------------ project helpers

def project(name, files) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"ratchet-spec-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    write(d, dict(files, **{"src/a.js": "export const a = 1;\n"}))
    (d / "tests").mkdir(exist_ok=True)
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the spec")
    r = rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    (d / ".ratchet/runs/f/app-scout.json").write_text("{}")
    return d


RUN = lambda d: d / ".ratchet/runs/f"


def plan_of(d):
    return json.loads((RUN(d) / "checkpoints.json").read_text())


def save_plan(d, plan):
    (RUN(d) / "checkpoints.json").write_text(json.dumps(plan))


def refine(d):
    """What the planner does with the draft: test files and commands, low risk (one reviewer, fast tests)."""
    plan = plan_of(d)
    for i, cp in enumerate(plan["checkpoints"], 1):
        cp["tests"] = {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}
        cp["risk"] = "low"
    save_plan(d, plan)
    return plan


def cases(d, cp):
    return json.loads((RUN(d) / "tests" / f"{cp}.json").read_text())["cases"]


def build(d, cp):
    """Take one step through every gate with real tests that name each planned case."""
    n = cp[2:]
    body = "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f%s.js';\n" % n
    for c in cases(d, cp):
        body += f"t('[{c['id']}] {c.get('title', '')[:40]}', () => a.equal(f(), 1));\n"
    (d / f"tests/cp{n}.test.js").write_text(body)
    for step in (("tests-written", cp), ("red", cp)):
        r = rx(d, *step)
        assert r.returncode == 0, (step, r.stdout, r.stderr)
    (d / f"src/f{n}.js").write_text("export const f = () => 1;\n")
    for step in (("implemented", cp), ("behavior", cp)):
        r = rx(d, *step)
        assert r.returncode == 0, (step, r.stdout, r.stderr)
    rnd = g.open_round(d, cp)
    paths = [g.write_review(d, rnd, rv["id"], files_reviewed=[f"src/f{n}.js"]) for rv in rnd["reviewers"]]
    r = rx(d, "adv-record", cp, "--reviews", *paths)
    assert r.returncode == 0, r.stdout + r.stderr
    return rx(d, "complete", cp)


def fake_openspec() -> dict:
    b = Path(tempfile.mkdtemp(prefix="ratchet-bin-"))
    (b / "openspec").write_text('#!/bin/sh\n[ "$1" = archive ] || exit 2\nmkdir -p openspec/changes/archive && '
                                'mv "openspec/changes/$2" "openspec/changes/archive/2026-10-03-$2" && '
                                'echo "Change \'$2\' archived as 2026-10-03-$2."\n')
    (b / "openspec").chmod(0o755)
    return dict(g.ENV, PATH=f"{b}:{g.ENV.get('PATH', '')}")


# ------------------------------------------------------------------------------------------------ tests

def parsers():
    print("Reading OpenSpec")
    d = tmp("os", OPENSPEC)
    ch = ss.load_openspec_change(d, "add-dark-mode")
    check("the title comes from the proposal", ch["title"] == "Add dark mode", ch["title"])
    check("ADDED and MODIFIED requirements are read; REMOVED ones are listed apart",
          [r["title"] for r in ch["requirements"]] == ["Theme toggle", "Default setting"]
          and [r["title"] for r in ch["removed"]] == ["Light only"], ch["requirements"])
    sc = ch["requirements"][0]["scenarios"]
    check("bold WHEN/THEN and plain GIVEN/WHEN/THEN/AND scenarios are both read",
          [s["name"] for s in sc] == ["Switch to dark", "Remember choice"] and sc[0]["when"] == "the user picks dark mode"
          and sc[1]["given"] == "the user picked dark mode" and "no flash" in sc[1]["then"], sc)
    check("scenario refs are stable ids", sc[0]["ref"] == "add-dark-mode#req:ui/theme-toggle/sc:switch-to-dark", sc[0]["ref"])
    check("tasks keep their numbers and group headings",
          [(t["ref"], t["group"]) for t in ch["tasks"]] == [("add-dark-mode#task:1.1", "Theme switch"),
                                                             ("add-dark-mode#task:1.2", "Theme switch"),
                                                             ("add-dark-mode#task:2.1", "Follow the operating system")],
          ch["tasks"])
    check("openspec/config.yaml is a standard reviewers enforce", ch["standards"] == ["openspec/config.yaml"], ch["standards"])
    found = ss.detect(d)
    check("detect lists open changes, not archived ones", [x["id"] for x in found.get("openspec", [])] == ["add-dark-mode"]
          and found["openspec"][0]["scenarios"] == 3, found)
    plan, tests = ss.draft_plan([ch], "dark mode")
    cps = plan["checkpoints"]
    check("one draft step per task group, named after it", [c["title"] for c in cps] == ["Theme switch",
                                                                                          "Follow the operating system"], cps)
    check("each requirement lands with the task group about the same thing",
          [r["title"] for r in cps[0]["spec"]["requirements"]] == ["Theme toggle"]
          and [r["title"] for r in cps[1]["spec"]["requirements"]] == ["Default setting"], [c["spec"] for c in cps])
    check("every scenario gets a draft test case pointing back at it",
          sorted(c["spec_ref"] for t in tests.values() for c in t["cases"]) == sorted(ss.scenario_refs([ch])), tests)
    check("tasks are mapped to steps", cps[0]["spec_refs"][:2] == ["add-dark-mode#task:1.1", "add-dark-mode#task:1.2"],
          cps[0]["spec_refs"])

    print("Reading Spec Kit")
    d = tmp("sk", SPECKIT)
    f = ss.load_speckit_feature(d, "001-photo-albums")
    st = [r for r in f["requirements"] if "#US" in r["ref"]]
    check("user stories and their Given/When/Then scenarios are read",
          [(r["title"], len(r["scenarios"])) for r in st] == [("Create an album", 2), ("Share an album", 1)]
          and st[0]["scenarios"][1]["when"] == "they rename it", st)
    check("FR-### requirements are read; NEEDS CLARIFICATION is flagged",
          [r["title"] for r in f["requirements"] if "#FR-" in r["ref"]] == ["FR-001", "FR-002", "FR-003"]
          and f["unclear"] == ["001-photo-albums#FR-003"], f["unclear"])
    check("tasks are T### refs", [t["ref"].split("#")[1] for t in f["tasks"]] == ["T001", "T002", "T003", "T004"], f["tasks"])
    check("the current feature (.specify/feature.json) and the constitution are found",
          f["current"] and f["standards"] == [".specify/memory/constitution.md"], f)
    plan, tests = ss.draft_plan([f], "albums")
    cps = plan["checkpoints"]
    check("one draft step per user story; setup tasks go first, FRs are checked in the last step",
          [c["title"] for c in cps] == ["Create an album", "Share an album"]
          and "001-photo-albums#T001" in cps[0]["spec_refs"]
          and all(f"001-photo-albums#FR-00{i}" in cps[1]["spec_refs"] for i in (1, 2, 3)), [c["spec_refs"] for c in cps])

    print("Reading BMAD v4 and v6")
    d = tmp("b4", BMAD4)
    found = ss.detect(d)
    check("v4: stories come from devStoryLocation; Done stories are left out",
          [x["id"] for x in found.get("bmad", [])] == ["1.2"], found)
    s = ss.load(d, "bmad", "1.2")
    check("v4: status, numbered acceptance criteria, tasks with their AC links",
          s["status"] == "Approved" and s["approved"] and [r["ref"] for r in s["requirements"]] == ["1.2#AC1", "1.2#AC2"]
          and s["tasks"][0]["acs"] == ["1.2#AC1", "1.2#AC2"], s)
    check("v4: subtasks get stable refs under their task",
          [t["ref"] for t in s["tasks"]] == ["1.2#task:1", "1.2#task:1.1", "1.2#task:1.2", "1.2#task:2"], s["tasks"])
    check("v4: the coding standards are found", s["standards"] == ["docs/architecture/coding-standards.md"], s["standards"])
    check("an AC in Given/when/then words is split up", s["requirements"][0]["scenarios"][0]["when"].startswith("they submit"),
          s["requirements"][0])
    d6 = tmp("b6", BMAD6)
    s6 = ss.load(d6, "bmad", "2-1-sign-up")
    check("v6: found by file name too, ready-for-dev counts as approved",
          s6["id"] == "2.1" and s6["version"] == "v6" and s6["approved"] and s6["tasks"][0]["acs"] == ["2.1#AC1"], s6)

    print("Writing back")
    changed = ss.tick_tasks(d, [s], {"1.2#task:1"})
    text = (d / "docs/stories/1.2.login.md").read_text()
    check("ticking a BMAD task ticks its subtasks too, nothing else",
          changed == ["docs/stories/1.2.login.md"] and text.count("- [x]") == 3 and "- [ ] Session handling" in text, text)
    ss.bmad_finish(d, s, "first")
    ss.bmad_finish(d, ss.load(d, "bmad", "1.2"), "second evidence")
    text = (d / "docs/stories/1.2.login.md").read_text()
    check("finishing a v4 story marks it Done and records the evidence under QA Results (once, even if re-run)",
          "## Status\n\nDone" in text and text.count("### Ratchet verification") == 1 and "second evidence" in text
          and "first" not in text, text)
    changed = ss.bmad_finish(d6, s6, "ok")
    check("finishing a v6 story sets Status: done and updates sprint-status.yaml",
          "Status: done" in (d6 / s6["path"]).read_text() and "2-1-sign-up: done" in
          (d6 / "_bmad-output/implementation-artifacts/sprint-status.yaml").read_text()
          and "epic-2: in-progress" in (d6 / "_bmad-output/implementation-artifacts/sprint-status.yaml").read_text(), changed)
    d = tmp("os2", OPENSPEC)
    ch = ss.load_openspec_change(d, "add-dark-mode")
    (d / "openspec/changes/archive").mkdir(exist_ok=True)
    os.rename(d / "openspec/changes/add-dark-mode", d / "openspec/changes/archive/2026-10-03-add-dark-mode")
    ch2 = ss.load_openspec_change(d, "add-dark-mode")
    check("an archived change can still be read (same spec digest)", ch2["archived"] and
          ss.spec_digest([ch2]) == ss.spec_digest([ch]))
    check("ticks and status don't change what was approved",
          ss.spec_digest([dict(ch, status="x", tasks=[dict(t, done=True) for t in ch["tasks"]])]) == ss.spec_digest([ch]))


def review_fixes():
    print("Real-world spec files")
    d = Path(tempfile.mkdtemp(prefix="ratchet-spec-rw-"))
    tm = d / "tasks.md"
    tm.write_bytes(b"## 1. Setup\r\n- [ ] 1. Make the store\r\n* [ ] Name caf\xe9 (not UTF-8)\r\n  - [ ] 1.1 nested\r\n")
    ts = ss.parse_tasks_md(tm, "c", "openspec")
    check("'1.' numbering, '*' bullets and nested checkboxes are all tasks (nested ones count, outside BMAD)",
          [t["ref"] for t in ts] == ["c#task:1", "c#task:name-caf-not-utf-8", "c#task:1.1"]
          and not any(t["subtask"] for t in ts), [t["ref"] for t in ts])
    ss.tick_tasks(d, [{"id": "c", "tool": "openspec", "tasks": ts}], {"c#task:name-caf-not-utf-8"})
    check("ticking keeps CRLF line ends and bytes that aren't UTF-8, and changes only that line",
          tm.read_bytes() == b"## 1. Setup\r\n- [ ] 1. Make the store\r\n* [x] Name caf\xe9 (not UTF-8)\r\n  - [ ] 1.1 nested\r\n",
          tm.read_bytes())
    t1 = ss.parse_tasks_text("- [ ] Do a thing\n- [ ] Do a thing\n", "c", "openspec")
    t2 = ss.parse_tasks_text("\n\n- [ ] Do a thing\n\n- [ ] Do a thing\n", "c", "openspec")
    check("unnumbered tasks get refs from their text (a blank line doesn't move them); duplicates get their own ref",
          [t["ref"] for t in t1] == [t["ref"] for t in t2] == ["c#task:do-a-thing", "c#task:do-a-thing~2"], t1)
    check("ticking finds the task by ref even if the file moved lines since it was read",
          ss.tick_text("intro\n\n- [ ] Do a thing\n- [ ] Do a thing\n", "c", "openspec", {"c#task:do-a-thing~2"})
          == "intro\n\n- [ ] Do a thing\n- [x] Do a thing\n")
    reqs, _ = ss.parse_openspec_delta("## ADDED Requirements\n### Requirement: Login\nx\n#### Scenario: Login!\n- WHEN a\n"
                                      "- THEN b\n#### Scenario: Login?\n- WHEN c\n- THEN d\n### Requirement: Login\ny\n", "c", "auth")
    refs = ss.scenario_refs([{"requirements": reqs}])
    check("scenarios or requirements whose names slug the same get distinct refs (one test can't cover both)",
          len(refs) == len(set(refs)) == 3 and len({r["ref"] for r in reqs}) == 2, refs)
    story = ("# Story 3.1: Pay\n\n**Status:** In Progress\n\n## Acceptance Criteria\n\n- Pays by card\n- Shows a receipt\n\n"
             "## Tasks\n\n- [ ] Card form (AC: 1)\n\n## QA Results\n\nGate: PASS\n")
    (d / "docs/stories").mkdir(parents=True)
    (d / "docs/stories/3.1.pay.md").write_text(story)
    st = ss.load_bmad_story(d, d / "docs/stories/3.1.pay.md")
    check("a bold, several-word status is read and counts as approved; bullet acceptance criteria are read",
          st["status"] == "In Progress" and st["approved"] and [r["ref"] for r in st["requirements"]] == ["3.1#AC1", "3.1#AC2"], st)
    out, ok_ = ss.bmad_finish_text(story, "v4", "CP1 \\d+ title, tests C:\\path")
    out2, _ = ss.bmad_finish_text(out, "v4", "again")
    check("finishing replaces the whole status, survives backslashes in the evidence, keeps the team's own QA text",
          ok_ and "**Status:** Done\n" in out and "\\d+" in out and out2.count("### Ratchet verification") == 1
          and "again" in out2 and "Gate: PASS" in out2, out2)
    _, ok2 = ss.bmad_finish_text("# Story 1.1: X\n\n## Acceptance Criteria\n1. y\n", "v4", "e")
    check("a story without a status line is reported, not silently 'done'", ok2 is False)
    check("sprint-status keeps comments", ss.sprint_done_text("  2-1-a: review  # moved\n", "2-1-a") == "  2-1-a: done  # moved\n")


def openspec_run():
    print("A run built from an OpenSpec change")
    d = project("osrun", OPENSPEC)
    r = rx(d, "spec", "detect")
    check("detect names the change in plain words", "OpenSpec changes still open: 1" in r.stdout and "add-dark-mode" in r.stdout,
          r.stdout)
    r = rx(d, "spec", "import", "auto")
    check("with one open change, import picks it", r.returncode == 0 and "Imported OpenSpec change add-dark-mode" in r.stdout
          and "Removes 1 requirement" in r.stdout, r.stdout + r.stderr)
    check("the draft plan and test plans are written", len(plan_of(d)["checkpoints"]) == 2 and cases(d, "CP1"))
    cfg = json.loads((d / ".ratchet/config.json").read_text())
    check("openspec/config.yaml is added to the standards", "openspec/config.yaml" in cfg.get("standards", []), cfg)
    r = rx(d, "plan-submit")
    check("the raw draft isn't a valid plan yet (no test command)", r.returncode != 0 and "tests.command" in r.stderr, r.stderr)
    refine(d)
    keep = (RUN(d) / "tests/CP1.json").read_text()
    tp = json.loads(keep)
    tp["cases"] = tp["cases"][:1]
    (RUN(d) / "tests/CP1.json").write_text(json.dumps(tp))
    r = rx(d, "plan-submit")
    check("a scenario with no planned test is refused, by name",
          r.returncode != 0 and "sc:remember-choice" in r.stderr and "no planned test" in r.stderr, r.stderr)
    plan = plan_of(d)
    plan["spec_unmapped"] = [{"ref": "add-dark-mode#req:ui/theme-toggle/sc:remember-choice"}]
    save_plan(d, plan)
    r = rx(d, "plan-submit")
    check("leaving one out needs a reason", r.returncode != 0 and "needs a ref and a reason" in r.stderr, r.stderr)
    plan["spec_unmapped"][0]["reason"] = "covered by the browser test suite"
    save_plan(d, plan)
    r = rx(d, "plan-submit")
    check("with a reason it is accepted", r.returncode == 0, r.stderr)
    s = rx(d, "plan-summary").stdout
    check("the plan summary says what it was built from and what was left out",
          "Built from: OpenSpec change add-dark-mode (Add dark mode) (already agreed by the team)" in s
          and "Left out on purpose" in s and "From the spec:" in s, s)
    (RUN(d) / "tests/CP1.json").write_text(keep)
    plan.pop("spec_unmapped")
    plan["checkpoints"][1]["spec_refs"] = [r_ for r_ in plan["checkpoints"][1]["spec_refs"] if "task:2.1" not in r_]
    plan["checkpoints"][0]["spec_refs"].append("add-dark-mode#task:9.9")
    save_plan(d, plan)
    r = rx(d, "plan-submit")
    check("an unmapped task and an unknown ref are both refused",
          r.returncode != 0 and "task:2.1" in r.stderr and "task:9.9" in r.stderr, r.stderr)
    plan["checkpoints"][0]["spec_refs"].pop()
    plan["checkpoints"][1]["spec_refs"].append("add-dark-mode#task:2.1")
    save_plan(d, plan)
    r = rx(d, "plan-submit")
    check("the full plan is accepted", r.returncode == 0, r.stderr)

    tasks_md = d / "openspec/changes/add-dark-mode/tasks.md"
    tasks_md.write_text(tasks_md.read_text() + "\n<!-- my own draft note -->\n")   # the user's edit, not committed
    g.approve_plan(d)

    print("Building it")
    spec_md = d / "openspec/changes/add-dark-mode/specs/ui/spec.md"
    orig = spec_md.read_text()
    spec_md.write_text(orig.replace("the dark palette is used", "the light palette is used"))
    r = rx(d, "tests-written", "CP1")
    check("changing the spec after approval stops the run", r.returncode != 0 and
          "the spec this run was built from (openspec add-dark-mode)" in r.stderr, r.stderr)
    spec_md.write_text(orig)
    r = build(d, "CP1")
    check("CP1 completes and says which tasks it ticked", r.returncode == 0 and "Spec: ticked 2 task(s)" in r.stdout,
          r.stdout + r.stderr)
    tasks = (d / "openspec/changes/add-dark-mode/tasks.md").read_text()
    check("its tasks are ticked in tasks.md, the other group's aren't",
          "- [x] 1.1" in tasks and "- [x] 1.2" in tasks and "- [ ] 2.1" in tasks, tasks)
    show = g.git(d, "show", "--stat", "--format=%B", "HEAD").stdout
    check("the ticks are in the step's own commit", "tasks.md" in show and "src/f1.js" in show and "Spec: " in show, show)
    diff = g.git(d, "diff", "-U0", "--", "openspec").stdout
    check("the user's own uncommitted edit to tasks.md stays theirs: not in the commit, still in the file",
          "my own draft note" not in g.git(d, "show", "HEAD:openspec/changes/add-dark-mode/tasks.md").stdout
          and "+<!-- my own draft note -->" in diff and "[x]" not in diff and "[ ]" not in diff and "- [x] 1.1" in tasks_md.read_text(), diff)
    check("nothing else is left uncommitted", g.git(d, "status", "--porcelain", "--", "src", "tests").stdout == "",
          g.git(d, "status", "--porcelain").stdout)
    r = build(d, "CP2")
    check("ticking didn't trip the approval lock; CP2 completes", r.returncode == 0, r.stdout + r.stderr)
    stj = json.loads(rx(d, "spec", "status", "--json").stdout)
    check("spec status: every scenario proven, every task ticked",
          all(x["state"] == "proven" for x in stj["scenarios"]) and all(t["ticked"] for t in stj["tasks"]), stj)
    vd = json.loads((RUN(d) / "viewer-data.json").read_text())
    check("the viewer gets the coverage table", vd.get("spec") and len(vd["spec"]["rows"]) == 3
          and vd["spec"]["tasks_ticked"] == 3, vd.get("spec"))

    print("Approving the whole run archives the change")
    write(d, {"openspec/changes/draft2/proposal.md": "# Someone else's draft\n", "openspec/staged.md": "staged\n"})
    g.git(d, "add", "openspec/staged.md")
    env = fake_openspec()
    g.say(d, "yes, approved")
    r = rx(d, "human-approve", env=env)
    check("the change is archived with OpenSpec's own CLI, in its own commit",
          r.returncode == 0 and "Archived OpenSpec change add-dark-mode" in r.stdout
          and (d / "openspec/changes/archive/2026-10-03-add-dark-mode/tasks.md").exists(), r.stdout + r.stderr)
    log = g.git(d, "log", "--format=%s", "-1").stdout
    check("the archive commit is on top", "archive OpenSpec change add-dark-mode" in log, log)
    files = g.git(d, "show", "--name-only", "--format=", "HEAD").stdout
    check("the archive commit holds only what the archive moved, not other drafts or staged files",
          "archive/2026-10-03-add-dark-mode/tasks.md" in files and "draft2" not in files and "staged.md" not in files, files)
    check("the user's staged file is still staged",
          "openspec/staged.md" in g.git(d, "diff", "--cached", "--name-only").stdout, g.git(d, "status", "--porcelain").stdout)


def openspec_no_cli():
    print("Without the OpenSpec CLI")
    d = project("osnocli", OPENSPEC)
    rx(d, "spec", "import", "openspec", "add-dark-mode")
    refine(d)
    assert rx(d, "plan-submit").returncode == 0
    g.approve_plan(d)
    build(d, "CP1")
    build(d, "CP2")
    g.say(d, "yes, approved")
    env = dict(g.ENV)
    env["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                  if not (Path(p) / "openspec").exists())
    r = rx(d, "human-approve", env=env)
    check("the run still finishes and says plainly the change wasn't archived",
          r.returncode == 0 and "wasn't archived" in r.stdout and "isn't installed" in r.stdout, r.stdout + r.stderr)


def speckit_run():
    print("A run built from a Spec Kit feature")
    d = project("skrun", SPECKIT)
    r = rx(d, "spec", "detect")
    check("detect flags the unclear requirement", "NEEDS CLARIFICATION" in r.stdout and "current feature" in r.stdout, r.stdout)
    r = rx(d, "spec", "import", "speckit", "001-photo-albums")
    check("import warns about the unclear requirement", r.returncode == 0 and "FR-003" in r.stdout, r.stdout + r.stderr)
    refine(d)
    r = rx(d, "plan-submit")
    check("the refined draft covers everything", r.returncode == 0, r.stderr)
    s = rx(d, "plan-summary").stdout
    check("the summary says the spec still has an open question", "NEEDS CLARIFICATION" in s, s)
    g.approve_plan(d)
    r = build(d, "CP1")
    tasks = (d / "specs/001-photo-albums/tasks.md").read_text()
    check("CP1 ticks its setup and US1 tasks", r.returncode == 0 and "- [x] T001" in tasks and "- [x] T003" in tasks
          and "- [ ] T004" in tasks, r.stdout + tasks)


def bmad_run():
    print("A run built from a BMAD story (v6)")
    d = project("b6run", BMAD6)
    r = rx(d, "spec", "import", "auto")
    check("the one open story is picked", r.returncode == 0 and "BMAD story 2.1" in r.stdout, r.stdout + r.stderr)
    refine(d)
    assert rx(d, "plan-submit").returncode == 0, rx(d, "plan-submit").stderr
    g.approve_plan(d)
    r = build(d, "CP1")
    story = (d / "_bmad-output/implementation-artifacts/2-1-sign-up.md").read_text()
    sprint = (d / "_bmad-output/implementation-artifacts/sprint-status.yaml").read_text()
    check("when the story's last step passes, the story is done with Ratchet's evidence",
          r.returncode == 0 and "Status: done" in story and "### Ratchet verification" in story and "CP1-T01" in story
          and "2-1-sign-up: done" in sprint and "- [x] Task 1" in story and "- [x] Subtask 1.1" in story,
          r.stdout + story + sprint)
    show = g.git(d, "show", "--stat", "--format=%B", "HEAD").stdout
    check("the story and sprint status are in the step's commit", "2-1-sign-up.md" in show and "sprint-status.yaml" in show, show)

    print("BMAD v4, a story still in Draft")
    files = dict(BMAD4)
    files["docs/stories/1.2.login.md"] = files["docs/stories/1.2.login.md"].replace("Approved", "Draft")
    d = project("b4run", files)
    r = rx(d, "spec", "import", "bmad", "1.2")
    check("import says the story may not be approved yet", r.returncode == 0 and "may not have approved" in r.stdout, r.stdout)
    r = rx(d, "spec", "import", "auto", "9.9")
    check("auto refuses when nothing matches", r.returncode != 0 and "Can't pick" in r.stderr, r.stderr)
    r = rx(d, "spec", "status")
    check("spec status works before building", r.returncode == 0 and "0 of 2" in r.stdout, r.stdout + r.stderr)


def no_spec():
    print("A run without a spec is unchanged")
    d = g.project("nospec")
    r = rx(d, "spec", "status")
    check("spec status says so", "wasn't started from a spec" in r.stdout, r.stdout)
    r = rx(d, "spec", "detect")
    check("detect says nothing was found", "No OpenSpec, Spec Kit or BMAD" in r.stdout, r.stdout)


def main():
    parsers()
    review_fixes()
    openspec_run()
    openspec_no_cli()
    speckit_run()
    bmad_run()
    no_spec()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
