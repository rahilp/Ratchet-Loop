# Engine Split (Lane S) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the body of `skills/ratchet-orchestrator/scripts/ratchet.py` into `ratchet_engine.py` and leave `ratchet.py` as a ~5-line launcher, preserving CLI, hook, and fixture behavior.

**Architecture:** Mechanical body move (design approach A). Process entry stays on `ratchet.py`. Internals load `ratchet_engine.py` by path. Subprocess CLI invocations use the launcher; in-process/`spec_from_file_location`/source scans use the engine file. No logic changes in the engine body; no lazy-import work.

**Tech Stack:** Python 3.8+ stdlib only; existing `tests/run_all.py` suite runner; `importlib.util.spec_from_file_location` loaders.

**Spec:** `docs/superpowers/specs/2026-11-26-engine-split-design.md`

## Global Constraints

- Move-only for the engine body: no intentional behavior or logic edits in the same change set as the split.
- Public CLI/hook entry path remains `…/scripts/ratchet.py` (filename unchanged).
- Launcher does not re-export engine internals (no `import *` facade).
- Stdlib only; no new third-party deps.
- Full suite green is the merge bar; hook-latency measurement is optional and not a gate.
- Naming convention after the split:
  - `ENGINE` → `…/scripts/ratchet_engine.py` (load / AST / source scan)
  - `LAUNCHER` → `…/scripts/ratchet.py` (subprocess CLI: `python3 LAUNCHER …`)

---

## File map

| Path | Responsibility after S |
|------|------------------------|
| `skills/ratchet-orchestrator/scripts/ratchet_engine.py` | Former `ratchet.py` body (implementation + `main`) |
| `skills/ratchet-orchestrator/scripts/ratchet.py` | Tiny launcher calling `ratchet_engine.main` |
| `skills/ratchet-orchestrator/scripts/{engine_utils,records,run_records,plan_support,provider_discovery,status_text}.py` | `_ratchet_module()` path → `ratchet_engine.py` |
| `skills/ratchet-ui-compare/scripts/ui_review.py` | `ENGINE` → `ratchet_engine.py` |
| `skills/ratchet-adversarial-loop/scripts/external_review.py` | `ENGINE` → `ratchet_engine.py` |
| `tests/trust_helpers.py` | Introduce `LAUNCHER` + retarget `ENGINE`; subprocess uses `LAUNCHER` |
| `tests/test_*.py` (and helpers) that define `ENGINE = …/ratchet.py` | Split launcher vs engine per convention |
| `tests/fixtures/modularize_cp1/test-inventory.md` | Engine file = `ratchet_engine.py` |
| `skills/ratchet-orchestrator/references/modules.md` | Document launcher vs engine |
| `tests/test_engine_split.py` | New guard suite for the split invariants |

---

### Task 1: Guard tests for the split

**Files:**
- Create: `tests/test_engine_split.py`
- Modify: none yet (tests must fail before the move)

**Interfaces:**
- Consumes: on-disk paths under `skills/ratchet-orchestrator/scripts/`
- Produces: failing checks that become the S done-when bar

- [ ] **Step 1: Write the guard suite**

```python
#!/usr/bin/env python3
"""Lane S invariants: tiny launcher, engine body, load rules."""
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
LAUNCHER = SCRIPTS / "ratchet.py"
ENGINE = SCRIPTS / "ratchet_engine.py"
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {detail!s}"[:400]))


def t01_files_exist():
    check("[S-T01] ratchet_engine.py exists", ENGINE.is_file(), ENGINE)
    check("[S-T01] ratchet.py exists", LAUNCHER.is_file(), LAUNCHER)


def t02_launcher_is_tiny():
    lines = LAUNCHER.read_text(encoding="utf-8").splitlines()
    check("[S-T02] launcher has at most 12 non-empty lines",
          sum(1 for l in lines if l.strip()) <= 12, len(lines))
    text = LAUNCHER.read_text(encoding="utf-8")
    check("[S-T02] launcher imports ratchet_engine.main",
          "from ratchet_engine import main" in text or "import ratchet_engine" in text, text)


def t03_engine_defines_main():
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    check("[S-T03] ratchet_engine.py defines main", "main" in names, sorted(names)[:20])


def t04_launcher_help():
    r = subprocess.run([sys.executable, str(LAUNCHER), "--help"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    check("[S-T04] launcher --help exits 0", r.returncode == 0, r.stderr[-400:])
    check("[S-T04] launcher --help mentions usage/subcommands",
          "usage" in (r.stdout + r.stderr).lower() or "init" in (r.stdout + r.stderr).lower(),
          (r.stdout + r.stderr)[:400])


def t05_inprocess_loads_engine_file():
    spec = importlib.util.spec_from_file_location("ratchet_engine_split_guard", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    check("[S-T05] loading ratchet_engine.py exposes main", callable(getattr(m, "main", None)))


def t06_no_internal_loader_points_at_launcher():
    bad = []
    for path in SCRIPTS.glob("*.py"):
        if path.name in ("ratchet.py", "ratchet_engine.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if 'with_name("ratchet.py")' in text or "with_name('ratchet.py')" in text:
            bad.append(path.name)
    check("[S-T06] sibling _ratchet_module paths do not load the launcher",
          not bad, bad)


CASES = [
    ("S-T01", t01_files_exist), ("S-T02", t02_launcher_is_tiny), ("S-T03", t03_engine_defines_main),
    ("S-T04", t04_launcher_help), ("S-T05", t05_inprocess_loads_engine_file),
    ("S-T06", t06_no_internal_loader_points_at_launcher),
]


if __name__ == "__main__":
    for cid, fn in CASES:
        print(cid)
        try:
            fn()
        except Exception as e:
            check(f"[{cid}] raised", False, e)
    print(f"{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
```

- [ ] **Step 2: Run the suite and confirm it fails before the move**

Run: `python3 tests/test_engine_split.py`

Expected: FAIL on S-T01 (`ratchet_engine.py` missing) and related checks; non-zero exit.

- [ ] **Step 3: Commit the failing guard suite**

```bash
git add tests/test_engine_split.py
git commit -m "test: add Lane S engine-split guard suite (expect fail pre-move)"
```

---

### Task 2: Move body + install launcher

**Files:**
- Create: `skills/ratchet-orchestrator/scripts/ratchet_engine.py` (via `git mv`)
- Modify: `skills/ratchet-orchestrator/scripts/ratchet.py` (replace with launcher)
- Test: `tests/test_engine_split.py`

**Interfaces:**
- Consumes: existing `main()` in the moved body
- Produces: `ratchet_engine.main` callable; launcher entry at `ratchet.py`

- [ ] **Step 1: `git mv` the body**

```bash
git mv skills/ratchet-orchestrator/scripts/ratchet.py \
      skills/ratchet-orchestrator/scripts/ratchet_engine.py
```

- [ ] **Step 2: Write the launcher at `ratchet.py`**

```python
#!/usr/bin/env python3
"""CLI/hook entry: thin launcher for ratchet_engine.main."""
from ratchet_engine import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Keep the file executable (`chmod +x` if needed). Do not re-export other symbols.

- [ ] **Step 3: Confirm the engine still aliases itself as `ratchet` in `sys.modules`**

The moved body already contains (near the top, after `SCRIPT_DIR`):

```python
if "ratchet" not in sys.modules and __name__ in sys.modules:
    sys.modules["ratchet"] = sys.modules[__name__]
```

Leave that block unchanged. When the launcher does `from ratchet_engine import main`, `__name__` is `ratchet_engine`, so `sys.modules["ratchet"]` aliases the same module and `_ratchet_module()` reuse keeps working.

- [ ] **Step 4: Smoke the launcher**

Run: `python3 skills/ratchet-orchestrator/scripts/ratchet.py --help`

Expected: exit 0; help text printed (engine loads through launcher).

- [ ] **Step 5: Commit**

```bash
git add skills/ratchet-orchestrator/scripts/ratchet.py \
        skills/ratchet-orchestrator/scripts/ratchet_engine.py
git commit -m "refactor: move engine body to ratchet_engine.py; leave tiny launcher"
```

---

### Task 3: Retarget sibling `_ratchet_module()` loaders

**Files:**
- Modify: `skills/ratchet-orchestrator/scripts/engine_utils.py` (`_ratchet_module`, ~line 219)
- Modify: `skills/ratchet-orchestrator/scripts/records.py` (~line 71)
- Modify: `skills/ratchet-orchestrator/scripts/run_records.py` (~line 121)
- Modify: `skills/ratchet-orchestrator/scripts/plan_support.py` (~line 81)
- Modify: `skills/ratchet-orchestrator/scripts/provider_discovery.py` (~line 102)
- Modify: `skills/ratchet-orchestrator/scripts/status_text.py` (~line 90)
- Test: `tests/test_engine_split.py` (S-T06)

**Interfaces:**
- Consumes: `ratchet_engine.py` on disk beside each module
- Produces: cold-load path that executes the engine body, still registered under `"ratchet"` in `sys.modules`

- [ ] **Step 1: In each of the six files, change only the path line**

From:

```python
path = Path(__file__).resolve().with_name("ratchet.py")
```

To:

```python
path = Path(__file__).resolve().with_name("ratchet_engine.py")
```

Keep `spec_from_file_location("ratchet", path)` (module name `"ratchet"`) so existing `sys.modules.get("ratchet")` reuse stays valid. Update nearby comments that say the file is `ratchet.py` only where they describe the load path (do not rewrite historical modularize narrative unless a test asserts the comment text).

- [ ] **Step 2: Run the guard suite**

Run: `python3 tests/test_engine_split.py`

Expected: S-T01–T06 PASS (S-T06 specifically).

- [ ] **Step 3: Commit**

```bash
git add skills/ratchet-orchestrator/scripts/engine_utils.py \
        skills/ratchet-orchestrator/scripts/records.py \
        skills/ratchet-orchestrator/scripts/run_records.py \
        skills/ratchet-orchestrator/scripts/plan_support.py \
        skills/ratchet-orchestrator/scripts/provider_discovery.py \
        skills/ratchet-orchestrator/scripts/status_text.py
git commit -m "fix: point sibling _ratchet_module loaders at ratchet_engine.py"
```

---

### Task 4: Retarget shared test/skill `ENGINE` constants

**Files:**
- Modify: `tests/trust_helpers.py`
- Modify: `skills/ratchet-ui-compare/scripts/ui_review.py`
- Modify: `skills/ratchet-adversarial-loop/scripts/external_review.py`
- Modify: every test file that defines `ENGINE = …/ratchet.py` for in-process load (use the file list from the File map / ripgrep of `ENGINE = .*ratchet.py`)

**Interfaces:**
- Consumes: `LAUNCHER` for subprocess; `ENGINE` for `spec_from_file_location` / source reads
- Produces: helpers that exercise the engine body without treating the launcher as the implementation

- [ ] **Step 1: Update `tests/trust_helpers.py`**

```python
SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts"
LAUNCHER = SCRIPTS / "ratchet.py"
ENGINE = SCRIPTS / "ratchet_engine.py"
RECORDS = ENGINE.with_name("records.py")
PROVIDER_DISCOVERY = ENGINE.with_name("provider_discovery.py")
RUN_RECORDS = ENGINE.with_name("run_records.py")
```

Change every subprocess invocation that ran `sys.executable, str(ENGINE), …` to `str(LAUNCHER)` (including `rx`, `os.execvp`, and guard command strings that embed the CLI path). Keep `spec_from_file_location` / `cp5y_load_engine` / `engine()` on `ENGINE`.

- [ ] **Step 2: Update skill helpers**

In `ui_review.py` and `external_review.py`:

```python
ENGINE = PLUGIN_ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet_engine.py"
```

(They already load via `spec_from_file_location("ratchet_engine", ENGINE)`.)

- [ ] **Step 3: Batch-fix test-local `ENGINE` definitions**

For each test module that sets `ENGINE = …/ratchet.py`:

1. If it only subprocess-runs the CLI → point at `LAUNCHER` (or keep name `ENGINE` but path `ratchet.py` **only** when there is no in-process load/source scan in that file).
2. If it in-process loads or `read_text()`/AST-parses for symbols → path must be `ratchet_engine.py`.
3. If it does both → introduce both constants and use each correctly.

Prefer importing `ENGINE`/`LAUNCHER` from `trust_helpers` when the file already depends on it (`th.ENGINE`), to avoid drift.

- [ ] **Step 4: Fix bare `import ratchet as rt` callers that need internals**

Files such as `tests/test_dm_findings_extra.py`, `tests/test_objective.py`, `tests/test_review_objective.py`, `tests/test_run_cmd_env.py`, `tests/lane_added_cases.py`, and generated snippets in `tests/test_receipt_proof.py` / `tests/test_limits.py` that expect engine symbols:

```python
import ratchet_engine as rt
```

(or path-load `ENGINE`). Ensure `scripts/` is on `sys.path` the same way as today for `import ratchet`.

- [ ] **Step 5: Run a focused slice**

Run:

```bash
python3 tests/test_engine_split.py
python3 tests/test_gates.py
python3 tests/test_hook_list_payload.py
```

Expected: all three exit 0.

- [ ] **Step 6: Commit**

```bash
git add tests/trust_helpers.py \
        skills/ratchet-ui-compare/scripts/ui_review.py \
        skills/ratchet-adversarial-loop/scripts/external_review.py \
        tests/
git commit -m "fix: retarget ENGINE loaders to ratchet_engine; CLI uses LAUNCHER"
```

---

### Task 5: Inventory, modules map, and source-scan docs

**Files:**
- Modify: `tests/fixtures/modularize_cp1/test-inventory.md`
- Modify: `skills/ratchet-orchestrator/references/modules.md`
- Modify: any modularize test that asserts the engine filename is literally `ratchet.py` (e.g. `tests/test_modularize_inventory.py`, `tests/test_modularize_cp2.py`, `tests/test_config_layers.py` if it requires `f.name == "ratchet.py"` for an engine scan)
- Test: `python3 tests/test_modularize_inventory.py` (and related)

**Interfaces:**
- Consumes: on-disk `ratchet_engine.py` + launcher
- Produces: docs/inventory that describe launcher vs engine correctly

- [ ] **Step 1: Update `modules.md` opening**

State clearly:

- `ratchet.py` — tiny CLI/hook launcher
- `ratchet_engine.py` — implementation (`main` and commands)
- Sibling modules’ `_ratchet_module()` loads `ratchet_engine.py`

Update “Still inside ratchet.py” headings to “Still inside ratchet_engine.py” where they list deferred areas.

- [ ] **Step 2: Update `test-inventory.md`**

Replace “loads/parses ratchet.py” with “loads/parses ratchet_engine.py” for Kind 1/2 entries. Note the launcher file exists but is not the engine source under scan.

- [ ] **Step 3: Fix modularize assertions that require the basename `ratchet.py` as the engine**

Example pattern in `test_config_layers.py`:

```python
check("[CP1-X13] the scan sees the engine sources",
      any(f.name == "ratchet_engine.py" for f in files))
```

Only change assertions that mean “the implementation file,” not ones that mean “the CLI entry path still exists.”

- [ ] **Step 4: Run modularize + docs slice**

Run:

```bash
python3 tests/test_modularize_inventory.py
python3 tests/test_modularize_cp2.py
python3 tests/test_engine_split.py
```

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/modularize_cp1/test-inventory.md \
        skills/ratchet-orchestrator/references/modules.md \
        tests/test_modularize_inventory.py \
        tests/test_modularize_cp2.py \
        tests/test_config_layers.py
git commit -m "docs: record launcher vs ratchet_engine in module map and inventory"
```

---

### Task 6: Full-suite verification and sweep

**Files:**
- Modify: any remaining hardcodes found by search (fixtures that stub `scripts/ratchet.py`, OpenCode tests that only assert the entry filename, etc.)
- Test: `python3 tests/run_all.py`

**Interfaces:**
- Consumes: completed split
- Produces: green suite; no internals loaded from launcher

- [ ] **Step 1: Repo sweep**

Run:

```bash
rg -n 'with_name\("ratchet\.py"\)|scripts" / "ratchet\.py"|/ratchet\.py"\)' \
  skills/ratchet-orchestrator/scripts tests skills/ratchet-ui-compare skills/ratchet-adversarial-loop
```

Classify each hit:

- CLI/hook entry / plugin embed of `ratchet.py` → keep
- Load/scan/internals → must be `ratchet_engine.py`
- Fixture stub of entry file → launcher-shaped stub (may also add a tiny `ratchet_engine.py` stub if the fixture imports it)

Fix stragglers.

- [ ] **Step 2: Run the full suite**

Run: `python3 tests/run_all.py`

Expected: `N passed, 0 failed` (N is the current suite count; today ~175 `test_*.py` files).

- [ ] **Step 3: Optional informational timing**

```bash
python3 -c "import time; t=time.perf_counter(); import importlib.util; from pathlib import Path; p=Path('skills/ratchet-orchestrator/scripts/ratchet_engine.py'); s=importlib.util.spec_from_file_location('e', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(time.perf_counter()-t)"
```

Record the number in the PR body only; do not gate on it.

- [ ] **Step 4: Final commit if sweep fixes landed**

```bash
git add -u
git commit -m "fix: finish engine-split path sweep; full suite green"
```

- [ ] **Step 5: Done-when checklist**

- [ ] `ratchet.py` is the tiny launcher; body lives in `ratchet_engine.py`
- [ ] `tests/test_engine_split.py` passes
- [ ] Full suite green
- [ ] No sibling `_ratchet_module()` points at the launcher
- [ ] No intentional engine logic changes in this branch

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| Body → `ratchet_engine.py`, tiny launcher | Task 2 |
| CLI/hooks stay on `ratchet.py` | Tasks 2, 4 (LAUNCHER) |
| Internals load `ratchet_engine.py` | Tasks 3–4 |
| Fixture / `spec_from_file_location` / source scans retargeted | Tasks 4–5 |
| Inventory + module map updated | Task 5 |
| Move-only / no logic edits | Global constraint + Task 2 |
| Full suite green | Task 6 |
| Latency not a merge gate | Task 6 optional timing |
| No launcher star-import facade | Task 2 launcher code |

No TBD/placeholder steps remain. `ENGINE`/`LAUNCHER` naming is consistent across tasks.
