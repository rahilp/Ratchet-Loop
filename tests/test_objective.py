#!/usr/bin/env python3
"""
CP1 tests: every plan states its objective.   python3 tests/test_objective.py

A plan must carry a top-level 'objective' ({"users", "goal", "done_means": [...], "out_of_scope": [...]}), all four
parts non-empty, checked at plan-submit (not shared validate_plan, which plan-extend also uses: plan-extend instead
refuses to let feedback drop an objective the approved plan had). plan-summary shows the objective before the list
of steps. A plan drafted from a spec (OpenSpec, Spec Kit) gets a draft objective from the spec. A run approved
before this change keeps working.

Reuses tests/test_gates.py's project()/rx() CLI-level pattern (throwaway git repos, RATCHET_HOME in a temp dir,
providers=[] so no real agent CLI runs) and tests/test_specs.py's direct calls into spec_sources.draft_plan().

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
import copy
import json
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
import os
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402
import test_gates as g  # noqa: E402
import spec_sources as ss  # noqa: E402
import ratchet as rt  # noqa: E402

os.environ["RATCHET_HOME"] = str(g.HOME)  # keep this file's direct (non-subprocess) engine calls in the same
                                           # sandboxed per-user folder test_gates.py already uses for its own

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP1-[TX]\d+)\]")


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


# ------------------------------------------------------------------------------------------------ fixtures

OBJ_OK = {
    "users": "Families sharing one grocery list across their phones",
    "goal": "Keep everyone's grocery list in sync without manual re-entry",
    "done_means": [
        "An item added on one phone appears on every other phone within 2 seconds",
        "Checking an item off removes it from everyone's list at once",
        "The list is still there after closing and reopening the app",
    ],
    "out_of_scope": [
        "Sharing one list between two different families",
        "Editing the list while a phone has no network connection",
    ],
}

OMIT = object()  # "don't add an 'objective' key at all" (distinct from an emptied one)


def obj_project(name, objective=OMIT, cps=1):
    """Like test_gates.project(), but lets the caller control the plan's top-level 'objective' (or omit it), and
    does not assert plan-submit succeeded: returns (dir, the plan-submit result) so a case can check either way."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-obj-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "x", "checkpoints": []}
    if objective is not OMIT:
        plan["objective"] = objective
    # CP2: every done_means line the objective has is linked to a case ("proves"), so a valid objective still
    # submits; an objective with no usable done_means list gets no link (those cases test its own refusal).
    dm = objective.get("done_means") if isinstance(objective, dict) else None
    links = {"proves": list(range(1, len(dm) + 1))} if isinstance(dm, list) and dm else {}
    for i in range(1, cps + 1):
        cp = {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
              "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
              "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}}
        plan["checkpoints"].append(cp)
        (run / "tests" / f"CP{i}.json").write_text(
            json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code", **links}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-submit")
    return d, r


def write(root: Path, files: dict):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def tmp(name, files) -> Path:
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-obj-spec-{name}-"))
    write(d, files)
    return d


OPENSPEC_MIN = {
    "openspec/config.yaml": "schema: spec-driven\ncontext: |\n  Plain JavaScript, no frameworks.\n",
    "openspec/changes/add-autosave/proposal.md":
        "# Add autosave\n\n## Why\nPeople lose their work when the tab closes unexpectedly.\n",
    "openspec/changes/add-autosave/tasks.md":
        "## 1. Save on close\n- [ ] 1.1 Persist state before unload\n",
    "openspec/changes/add-autosave/specs/core/spec.md":
        "# Delta for Core\n\n## ADDED Requirements\n\n### Requirement: Autosave\n"
        "The system SHALL save state automatically.\n\n#### Scenario: Save before close\n"
        "- **WHEN** the user closes the tab\n- **THEN** their work is saved\n",
}

SPECKIT_MIN = {
    ".specify/memory/constitution.md": "# Constitution\n\n1. Every feature has tests first.\n",
    ".specify/feature.json": json.dumps({"feature_directory": "specs/002-grocery-list"}),
    "specs/002-grocery-list/spec.md":
        "# Feature Specification: Grocery list\n\n**Feature Branch**: `002-grocery-list`\n**Status**: Draft\n\n"
        "## User Scenarios & Testing *(mandatory)*\n\n### User Story 1 - Add an item (Priority: P1)\n\n"
        "Shoppers add items to a shared list.\n\n**Acceptance Scenarios**:\n\n"
        "1. **Given** a shopper with a list, **When** they add an item, **Then** the item appears for everyone\n\n"
        "## Requirements *(mandatory)*\n\n### Functional Requirements\n\n"
        "- **FR-001**: System MUST let shoppers add items\n",
    "specs/002-grocery-list/tasks.md":
        "# Tasks\n\n## Phase 1: Setup\n- [ ] T001 Create the list table\n\n"
        "## Phase 3: User Story 1\n- [ ] T002 [US1] Add-item endpoint\n",
}


def _objective_shape_ok(obj) -> bool:
    return (isinstance(obj, dict)
            and all(str(obj.get(k) or "").strip() for k in ("users", "goal"))
            and isinstance(obj.get("done_means"), list) and len(obj.get("done_means")) > 0
            and all(str(x or "").strip() for x in obj.get("done_means"))
            and isinstance(obj.get("out_of_scope"), list) and len(obj.get("out_of_scope")) > 0
            and all(str(x or "").strip() for x in obj.get("out_of_scope")))


# ------------------------------------------------------------------------------------------------ CP1-T01..T06: plan-submit

def t01_complete_objective_submits():
    d, r = obj_project("t01", objective=OBJ_OK)
    check("[CP1-T01] a plan with a complete objective submits (exit 0)", r.returncode == 0, r.stderr + r.stdout)
    st = json.loads(g.rx(d, "status", "--json").stdout or "{}")
    check("[CP1-T01] the run's phase becomes awaiting_plan_approval", st.get("phase") == "awaiting_plan_approval", st)


def t02_no_objective_key_refused():
    d, r = obj_project("t02", objective=OMIT)
    check("[CP1-T02] a plan with no top-level 'objective' key is refused (exit non-zero)", r.returncode != 0, r.stdout)
    check("[CP1-T02] the error names 'objective' as missing", bool(re.search(r"objective", r.stderr, re.I)), r.stderr)


def t03_empty_users_refused():
    obj = copy.deepcopy(OBJ_OK)
    obj["users"] = ""
    d, r = obj_project("t03", objective=obj)
    check("[CP1-T03] an objective with no stated users is refused (exit non-zero)", r.returncode != 0, r.stdout)
    check("[CP1-T03] the error names 'users' as the missing part", bool(re.search(r"\busers\b", r.stderr, re.I)), r.stderr)


def t04_empty_goal_refused():
    obj = copy.deepcopy(OBJ_OK)
    obj["goal"] = ""
    d, r = obj_project("t04", objective=obj)
    check("[CP1-T04] an objective with no stated goal is refused (exit non-zero)", r.returncode != 0, r.stdout)
    check("[CP1-T04] the error names 'goal' as the missing part", bool(re.search(r"\bgoal\b", r.stderr, re.I)), r.stderr)


def t05_empty_done_means_refused():
    obj = copy.deepcopy(OBJ_OK)
    obj["done_means"] = []
    d, r = obj_project("t05", objective=obj)
    check("[CP1-T05] an objective with no done_means is refused (exit non-zero)", r.returncode != 0, r.stdout)
    check("[CP1-T05] the error names 'done_means' as the missing part",
          bool(re.search(r"done[_ ]?means", r.stderr, re.I)), r.stderr)


def t06_empty_out_of_scope_refused():
    obj = copy.deepcopy(OBJ_OK)
    obj["out_of_scope"] = []
    d, r = obj_project("t06", objective=obj)
    check("[CP1-T06] an objective with no out_of_scope is refused (exit non-zero)", r.returncode != 0, r.stdout)
    check("[CP1-T06] the error names 'out_of_scope' as the missing part",
          bool(re.search(r"out[_ ]?of[_ ]?scope", r.stderr, re.I)), r.stderr)


# ------------------------------------------------------------------------------------------------ CP1-T07..T08: plan-summary

def t07_plan_summary_opens_with_objective():
    d, r = obj_project("t07", objective=OBJ_OK)
    assert r.returncode == 0, r.stderr
    out = g.rx(d, "plan-summary").stdout
    step_idx = out.find("\n1. ")
    check("[CP1-T07] plan-summary's output has a first numbered step line", step_idx != -1, out)

    def before_steps(needle):
        i = out.find(needle)
        return i != -1 and step_idx != -1 and i < step_idx

    check("[CP1-T07] the users text appears before the first step", before_steps(OBJ_OK["users"]), out)
    check("[CP1-T07] the goal text appears before the first step", before_steps(OBJ_OK["goal"]), out)
    check("[CP1-T07] the first done_means line appears before the first step", before_steps(OBJ_OK["done_means"][0]), out)
    check("[CP1-T07] the first out_of_scope line appears before the first step", before_steps(OBJ_OK["out_of_scope"][0]), out)


def t08_plan_summary_lists_every_line():
    d, r = obj_project("t08", objective=OBJ_OK)
    assert r.returncode == 0, r.stderr
    out = g.rx(d, "plan-summary").stdout
    check("[CP1-T08] all three done_means lines appear, not just the first",
          all(line in out for line in OBJ_OK["done_means"]), out)
    check("[CP1-T08] both out_of_scope lines appear, not just the first",
          all(line in out for line in OBJ_OK["out_of_scope"]), out)


# ------------------------------------------------------------------------------------------------ CP1-T09..T10: spec draft

def t09_openspec_draft_gets_objective():
    d = tmp("os", OPENSPEC_MIN)
    ch = ss.load_openspec_change(d, "add-autosave")
    plan, _tests = ss.draft_plan([ch], "autosave")
    obj = plan.get("objective")
    shape_ok = _objective_shape_ok(obj)
    check("[CP1-T09] draft_plan() from an OpenSpec change returns a top-level objective with all four parts filled in",
          shape_ok, obj)
    check("[CP1-T09] the objective's goal is drawn from the proposal's Why paragraph",
          shape_ok and "lose their work" in str(obj.get("goal", "")).lower(), obj.get("goal") if shape_ok else obj)


def t10_speckit_draft_gets_objective():
    d = tmp("sk", SPECKIT_MIN)
    f = ss.load_speckit_feature(d, "002-grocery-list")
    plan, _tests = ss.draft_plan([f], "grocery list")
    obj = plan.get("objective")
    shape_ok = _objective_shape_ok(obj)
    check("[CP1-T10] draft_plan() from a Spec Kit feature returns a top-level objective with all four parts filled in",
          shape_ok, obj)
    check("[CP1-T10] with no non-goals section in the spec, out_of_scope still gets a sensible default line",
          shape_ok, obj.get("out_of_scope") if shape_ok else obj)


# ------------------------------------------------------------------------------------------------ CP1-T11: pre-existing run

def _legacy_project(name):
    """A run approved before this feature existed: its plan has no 'objective' field at all, and it reached
    'awaiting_plan_approval' the way the old engine's plan-submit did, before objective_problems() existed.
    test_gates.project()/plan-submit can no longer produce that shape (plan-submit now refuses a plan with no
    objective), so this builds it directly: the same init/config steps as test_gates.project(), then the exact
    state change cmd_plan_submit makes, called straight from the engine module (not through the CLI, and
    skipping only the objective check that did not exist yet), never by hand-editing state.json's fields."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-obj-legacy-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "x", "checkpoints": [{
        "id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
        "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
        "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"},
    }]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    ctx = rt.Ctx(d)
    st = ctx.state()
    loaded_plan = ctx.plan()
    st["phase"] = "awaiting_plan_approval"
    st["awaiting_since"] = rt.now()
    st["plan_hash"] = rt._plan_hash(loaded_plan, ctx)
    st["checkpoints"] = {cp["id"]: st["checkpoints"].get(cp["id"]) or rt.new_cp_state(cp)
                         for cp in loaded_plan["checkpoints"]}
    ctx.log(st, "plan_submitted", hash=st["plan_hash"], count=len(loaded_plan["checkpoints"]))
    ctx.save_state(st)
    return d


def t11_preexisting_run_keeps_working():
    d = _legacy_project("t11")  # a plan with no 'objective' field at all, the pre-this-feature shape
    g.approve_plan(d)
    r = g.rx(d, "status")
    check("[CP1-T11] status still exits 0 for a run whose approved plan has no objective field", r.returncode == 0,
          r.stderr + r.stdout)
    combined = (r.stdout + r.stderr).lower()
    check("[CP1-T11] nothing in the output complains about a missing objective",
          "objective" not in combined or "missing" not in combined, r.stdout + r.stderr)


# ------------------------------------------------------------------------------------------------ CP1-T12..T13: plan-extend

def _append_cp2(run: Path, plan: dict):
    plan["checkpoints"].append({
        "id": "CP2", "title": "Export", "summary": "s", "done_when": ["exports work"],
        "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
        "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"},
    })
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))


def _run_cp1_to_feedback(name):
    """An approved plan with an objective, built through to the end and given feedback: the fixture note says "an
    approved plan with an objective, followed by ratchet.py feedback and an appended checkpoint, to exercise
    plan-extend". Returns (dir, run_dir, plan dict loaded from disk) with phase == planning_feedback."""
    d, r = obj_project(name, objective=OBJ_OK, cps=1)
    assert r.returncode == 0, r.stderr
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    rnd = g.open_round(d, "CP1")
    review_path = g.write_review(d, rnd, g.R0(rnd), findings=[])
    r = g.rx(d, "adv-record", "CP1", "--reviews", review_path)
    assert r.returncode == 0, r.stderr
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, r.stderr
    g.rx(d, "ask", "human-approve")
    g.say(d, "approved")
    r = g.rx(d, "human-approve")
    assert r.returncode == 0, r.stderr
    g.say(d, "actually, let's also add CSV export")
    r = g.rx(d, "feedback", "--text", "add CSV export")
    assert r.returncode == 0, r.stderr
    run = d / ".ratchet/runs/f"
    plan = json.loads((run / "checkpoints.json").read_text())
    return d, run, plan


def t12_plan_extend_keeps_objective():
    d, run, plan = _run_cp1_to_feedback("t12")
    _append_cp2(run, plan)  # objective block left untouched
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-extend")
    check("[CP1-T12] plan-extend accepts a feedback plan that keeps the objective untouched (exit 0)",
          r.returncode == 0, r.stderr + r.stdout)
    out = g.rx(d, "plan-summary").stdout
    check("[CP1-T12] the saved plan's objective (its goal text) is still shown, unchanged, after plan-extend",
          OBJ_OK["goal"] in out, out)


def t13_plan_extend_refuses_dropped_objective():
    d, run, plan = _run_cp1_to_feedback("t13")
    _append_cp2(run, plan)
    plan["objective"]["goal"] = ""  # feedback drops a required part of the objective
    (run / "checkpoints.json").write_text(json.dumps(plan))
    before_state = json.loads((run / "state.json").read_text())
    r = g.rx(d, "plan-extend")
    check("[CP1-T13] plan-extend refuses a feedback plan that drops the objective (exit non-zero)",
          r.returncode != 0, r.stdout)
    check("[CP1-T13] the message says the objective cannot be dropped",
          bool(re.search(r"objective", r.stderr, re.I)) and bool(re.search(r"drop", r.stderr, re.I)), r.stderr)
    after_state = json.loads((run / "state.json").read_text())
    check("[CP1-T13] the previously saved plan/state is unchanged (CP2 was not added)",
          "CP2" not in (after_state.get("checkpoints") or {})
          and after_state.get("phase") == before_state.get("phase"),
          (before_state.get("phase"), after_state.get("phase"), list((after_state.get("checkpoints") or {}).keys())))


# ------------------------------------------------------------------------------------------------ CP1-T14: docs

ROOT = HERE.parent
DOC_PATHS = {
    "README.md": ROOT / "README.md",
    "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md",
    "ratchet-checkpoint-planner.md": ROOT / "agents/ratchet-checkpoint-planner.md",
    "ratchet-orchestrator SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
}

PART_PATTERNS = {
    "users": re.compile(r"\busers\b", re.I),
    "goal": re.compile(r"\bgoal\b", re.I),
    "done_means": re.compile(r"done[_ ]?means|done[_ ]?when", re.I),
    "out_of_scope": re.compile(r"out[_ ]?of[_ ]?scope", re.I),
}


def _has_example(lines, pat):
    """A line mentioning the part that also carries real content beyond the keyword itself (a worked example),
    not just a bare mention of the field's name."""
    for line in lines:
        m = pat.search(line)
        if m:
            rest = (line[:m.start()] + line[m.end():]).strip(" :\"'-*#`,.")
            if len(re.sub(r"\s+", "", rest)) >= 15:
                return True
    return False


def t14_docs_describe_objective():
    texts = {}
    for name, path in DOC_PATHS.items():
        try:
            texts[name] = path.read_text()
        except OSError as e:
            texts[name] = ""
            check(f"[CP1-T14] {name} can be read", False, str(e))
            continue
        check(f"[CP1-T14] {name} mentions the objective", "objective" in texts[name].lower(), name)
        for part, pat in PART_PATTERNS.items():
            check(f"[CP1-T14] {name} mentions '{part}'", bool(pat.search(texts[name])), name)
    all_lines = [l for t in texts.values() for l in t.splitlines()]
    for part, pat in PART_PATTERNS.items():
        check(f"[CP1-T14] at least one doc shows a worked example value for '{part}'", _has_example(all_lines, pat),
              part)


# ------------------------------------------------------------------------------------------------ CP1-X01..X02:
# adversarial round 1 follow-ups -- real audience extraction, and the FILL IN placeholder is actually refused

GROCERY_WITH_STORY = {
    ".specify/memory/constitution.md": "# Constitution\n\n1. Every feature has tests first.\n",
    ".specify/feature.json": json.dumps({"feature_directory": "specs/003-grocery-list"}),
    "specs/003-grocery-list/spec.md":
        "# Feature Specification: Grocery list\n\n**Feature Branch**: `003-grocery-list`\n**Status**: Draft\n\n"
        "## User Scenarios & Testing *(mandatory)*\n\n### User Story 1 - Add an item (Priority: P1)\n\n"
        "As a shopper, I want to add items to a shared grocery list, so that my family always has what we need.\n\n"
        "**Acceptance Scenarios**:\n\n"
        "1. **Given** a shopper with a list, **When** they add an item, **Then** the item appears for everyone\n\n"
        "## Requirements *(mandatory)*\n\n### Functional Requirements\n\n"
        "- **FR-001**: System MUST let shoppers add items\n",
    "specs/003-grocery-list/tasks.md":
        "# Tasks\n\n## Phase 1: Setup\n- [ ] T001 Create the list table\n\n"
        "## Phase 3: User Story 1\n- [ ] T002 [US1] Add-item endpoint\n",
}


def x01_grocery_fixture_gets_shopper_audience():
    d = tmp("grocery-story", GROCERY_WITH_STORY)
    f = ss.load_speckit_feature(d, "003-grocery-list")
    plan, _tests = ss.draft_plan([f], "grocery list")
    users = str((plan.get("objective") or {}).get("users", ""))
    check("[CP1-X01] a Spec Kit story's 'As a <user>, I want ...' narrative names the audience ('shopper')",
          "shopper" in users.lower(), users)
    check("[CP1-X01] the audience is not the story's title ('Add an item')", users.strip() != "Add an item", users)
    check("[CP1-X01] the audience is not the unfilled FILL IN placeholder", not users.startswith(ss.FILL_IN_PREFIX),
          users)


def x02_fill_in_placeholder_is_refused():
    obj = copy.deepcopy(OBJ_OK)
    obj["users"] = f"{ss.FILL_IN_PREFIX} who will use this?"
    d, r = obj_project("x02", objective=obj)
    check("[CP1-X02] a plan whose objective.users is still the FILL IN placeholder is refused (exit non-zero)",
          r.returncode != 0, r.stdout)
    check("[CP1-X02] the error names 'users' as the part still needing an answer",
          bool(re.search(r"\busers\b", r.stderr, re.I)) and "FILL IN" in r.stderr, r.stderr)


def x03_malformed_scalar_objective_parts_refused_without_crash():
    """[CP1-X03] done_means=true and out_of_scope=3 (neither a list) must not reach _objective_fill_ins's
    enumerate() and crash; plan-submit should still return the ordinary objective-validation message."""
    obj = copy.deepcopy(OBJ_OK)
    obj["done_means"] = True
    obj["out_of_scope"] = 3
    d, r = obj_project("x03", objective=obj)
    check("[CP1-X03] a plan with done_means=true and out_of_scope=3 is refused (exit non-zero), not a crash",
          r.returncode != 0, r.stdout + r.stderr)
    check("[CP1-X03] the error names 'done_means' as the invalid part",
          bool(re.search(r"done[_ ]?means", r.stderr, re.I)), r.stderr)
    check("[CP1-X03] the error names 'out_of_scope' as the invalid part",
          bool(re.search(r"out[_ ]?of[_ ]?scope", r.stderr, re.I)), r.stderr)
    check("[CP1-X03] no traceback leaks to the user",
          "Traceback" not in r.stderr and "Traceback" not in r.stdout, r.stderr + r.stdout)


# ------------------------------------------------------------------------------------------------ runner

TESTS = [
    ("CP1-T01", t01_complete_objective_submits),
    ("CP1-T02", t02_no_objective_key_refused),
    ("CP1-T03", t03_empty_users_refused),
    ("CP1-T04", t04_empty_goal_refused),
    ("CP1-T05", t05_empty_done_means_refused),
    ("CP1-T06", t06_empty_out_of_scope_refused),
    ("CP1-T07", t07_plan_summary_opens_with_objective),
    ("CP1-T08", t08_plan_summary_lists_every_line),
    ("CP1-T09", t09_openspec_draft_gets_objective),
    ("CP1-T10", t10_speckit_draft_gets_objective),
    ("CP1-T11", t11_preexisting_run_keeps_working),
    ("CP1-T12", t12_plan_extend_keeps_objective),
    ("CP1-T13", t13_plan_extend_refuses_dropped_objective),
    ("CP1-T14", t14_docs_describe_objective),
    ("CP1-X01", x01_grocery_fixture_gets_shopper_audience),
    ("CP1-X02", x02_fill_in_placeholder_is_refused),
    ("CP1-X03", x03_malformed_scalar_objective_parts_refused_without_crash),
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
        print(f"\n{cid}: {fn.__name__}")
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, not a silent drop
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
