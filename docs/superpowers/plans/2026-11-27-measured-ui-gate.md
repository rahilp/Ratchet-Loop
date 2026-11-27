# Measured UI Gate (Lane U) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the UI gate measured-first (geometry then pixel) with modes `off` / `report` / `enforce` (default `enforce`), lasting scoped baselines, and LLM visual only when measurement is inconclusive.

**Architecture:** Add a pure measure module beside `ui_review.py`; wire mode-aware orchestration into `ui_review.py` and `cmd_ui_record`; store baselines under the project Ratchet home; keep behavior/adversarial gates unchanged.

**Tech Stack:** Python 3 stdlib (+ Pillow only if already present; otherwise pure-stdlib PNG via `struct`/`zlib` or ship a tiny pixel helper without new mandatory deps); Node `capture.mjs` for optional geometry enrichment; existing `ui_review.py` / `ratchet_engine.py` / `engine_utils.DEFAULT_CONFIG`.

## Global Constraints

- Preserve USP: checkpoint/gate model unchanged; insufficient-evidence still routes to capture-spec.
- Behavior and adversarial gates untouched; baselines never waive them.
- Default `ui_measure_mode` is `enforce`; projects may set `report` or `off`.
- In `enforce`, LLM visual runs only on measured `inconclusive` (or measure cannot run).
- Emitted runnable paths still name `ratchet.py` (Lane S).
- AI/harness-only copy; commits as Rahil Pirani (`rahil@piranifam.com`).
- Failing-first guards before each behavior change.

## File map

| File | Responsibility |
|------|----------------|
| `tests/test_ui_measure.py` | Lane U guards: measure verdicts, modes, baselines, ui-record wiring |
| `skills/ratchet-ui-compare/scripts/ui_measure.py` | Pure measure: masks/baselines → geometry → pixel → `{verdict, states, diffs}` |
| `skills/ratchet-ui-compare/scripts/ui_review.py` | Mode-aware: when to run measure / skip or call LLM visual |
| `skills/ratchet-orchestrator/scripts/engine_utils.py` | `DEFAULT_CONFIG["ui_measure_mode"] = "enforce"` |
| `skills/ratchet-orchestrator/scripts/ratchet_engine.py` | Config validation; `ui-record` honors measure; `ui-baseline-accept` command |
| `skills/ratchet-ui-compare/scripts/capture.mjs` | Optional: emit per-state geometry boxes (a11y) into capture result |
| `docs/reference.md` / schemas / how-it-works | Mode + accept command wording when user-visible |
| `docs/superpowers/specs/2026-11-27-measured-ui-gate-design.md` | Status → approved / implemented |
| Existing `tests/test_ui_*.py` | Must stay green |

### Measure JSON contract (all later tasks use this)

```python
# ui_measure.py produces (and tests pin):
{
  "version": 1,
  "verdict": "pass" | "fail" | "inconclusive",
  "mode": "off" | "report" | "enforce",  # echoed from caller
  "states": [
    {
      "name": "list",
      "verdict": "pass" | "fail" | "inconclusive",
      "geometry": {"compared": True, "mismatches": [...]},
      "pixel": {"compared": True, "mismatch_ratio": 0.0, "threshold": 0.02},
      "diffs": [{"kind": "geometry"|"pixel", "region": "...", "detail": "..."}],
    }
  ],
  "error": None | str,  # tool-level; forces inconclusive when set
}
```

Baseline record (project store):

```python
{
  "id": "bl-...",
  "checkpoint": "CP1",   # optional scope
  "state": "list",
  "region": {"x": 0, "y": 0, "w": 40, "h": 20},  # or mask path key
  "reason": "clock widget intentionally different",
  "accepted_at": "<iso>",
  "accepted_by": "director|human",
}
```

---

### Task 1: Guard suite for measure + modes (failing first)

**Files:**
- Create: `tests/test_ui_measure.py`
- Test: `python3 tests/test_ui_measure.py`

**Interfaces:**
- Consumes: will call `ui_measure.measure_capture(...)` and engine config helpers once they exist
- Produces: failing checks U-T01..U-T06 that pin the design

- [ ] **Step 1: Write the failing guard suite**

Create `tests/test_ui_measure.py` using the same `check()` tally pattern as `test_test_speed.py` / `test_ui_insufficient_evidence.py`.

Pins:

1. **U-T01** — geometry mismatch (two box lists differ beyond tolerance) → state `fail`, overall `fail`.
2. **U-T02** — identical geometry + identical PNG bytes → `pass`.
3. **U-T03** — pixel differs only inside a baseline/mask region → `pass`; same diff without baseline → `fail`.
4. **U-T04** — missing geometry on both sides → `inconclusive` (not silent `pass`).
5. **U-T05** — mode helper: `off` → measure not required; `report` → measure attached but gate verdict still follows visual rules; `enforce` + measure `fail` → gate fails without needing visual FAIL; `enforce` + measure `pass` → gate can pass without visual reviewer JSON when behavior still required separately at record time (pin the orchestration helper, not a full live LLM).
6. **U-T06** — accept baseline for wrong state → refuse; accept for matching state+region → later measure `pass`.

Stub tiny 2×2 / 8×8 PNG fixtures as raw bytes in the test (or write via a helper in `ui_measure` once Task 2 lands — for Step 1, inline minimal PNG bytes).

- [ ] **Step 2: Run guard — expect fail (module missing)**

```bash
python3 tests/test_ui_measure.py
```

Expected: import/`measure_capture` missing → failures (or suite exits non-zero).

- [ ] **Step 3: Commit**

```bash
git add tests/test_ui_measure.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "test: Lane U guard suite for measure + modes (failing first)"
```

---

### Task 2: Measure module (geometry then pixel)

**Files:**
- Create: `skills/ratchet-ui-compare/scripts/ui_measure.py`
- Test: `tests/test_ui_measure.py` (U-T01..U-T04)

**Interfaces:**
- Consumes: capture layout dict (or paths): per-state `reference`/`app` screenshot paths, optional `geometry` lists `[{id,x,y,w,h}, ...]`, `masks`, `baselines`
- Produces: `measure_capture(capture, *, baselines=None, pixel_threshold=0.02, geometry_tolerance_px=2) -> dict` per contract above

- [ ] **Step 1: Implement `ui_measure.py`**

```python
"""Deterministic UI measure: geometry then pixel. No LLM. No project instructions."""

def measure_capture(capture: dict, *, baselines=None, pixel_threshold=0.02,
                    geometry_tolerance_px=2) -> dict:
    """Return measure JSON. Never raises for content mismatch — uses verdicts.
    Tool/IO failure sets error=str and verdict='inconclusive'."""
    ...
```

Rules:

1. For each state: if geometry present on both sides, compare boxes by `id` (or sorted by position if no id); mismatch outside baseline regions → geometry fail.
2. Else if geometry missing on either side → that state's geometry `compared=False`; if pixel also cannot run → state `inconclusive`.
3. Pixel: load PNGs; apply masks + baseline rectangles as ignore; `mismatch_ratio = changed_pixels / total`; `> pixel_threshold` → fail.
4. Overall verdict: any state `fail` → `fail`; else any `inconclusive` → `inconclusive`; else `pass`.
5. No network, no subprocess, no reading project source — only the paths passed in.

Prefer stdlib-only PNG decode if Pillow is not a declared dependency; if Pillow is already used elsewhere in the skill, reuse it. Do **not** add a new mandatory package without documenting it in the plan commit message and skill README.

- [ ] **Step 2: Re-run guards U-T01..U-T04 — expect pass**

```bash
python3 tests/test_ui_measure.py
```

Expected: U-T01..U-T04 pass; U-T05/U-T06 may still fail.

- [ ] **Step 3: Commit**

```bash
git add skills/ratchet-ui-compare/scripts/ui_measure.py tests/test_ui_measure.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "feat(ui): deterministic geometry-then-pixel measure module"
```

---

### Task 3: Config `ui_measure_mode` (default enforce)

**Files:**
- Modify: `skills/ratchet-orchestrator/scripts/engine_utils.py` (`DEFAULT_CONFIG`)
- Modify: `skills/ratchet-orchestrator/scripts/ratchet_engine.py` (config validation near other `ui_*` keys ~340–420)
- Test: `tests/test_ui_measure.py` (add U-T07 default + validation) and/or existing config suite if one asserts known keys

**Interfaces:**
- Consumes: `ctx.config()` / `DEFAULT_CONFIG`
- Produces: `cfg["ui_measure_mode"]` ∈ `{off, report, enforce}`, default `"enforce"`; invalid value refused at config load with harness-readable message

- [ ] **Step 1: Failing pin for default**

In `test_ui_measure.py`:

```python
def u_t07_default_mode_is_enforce():
    # load engine_utils DEFAULT_CONFIG the same way other suites do
    check("[U-T07] DEFAULT_CONFIG ui_measure_mode is enforce",
          DEFAULT_CONFIG.get("ui_measure_mode") == "enforce")
```

Run — expect fail until Step 2.

- [ ] **Step 2: Add default + validation**

In `engine_utils.py` `DEFAULT_CONFIG`, next to `ui_block_on_nits`:

```python
"ui_measure_mode": "enforce",  # off | report | enforce — measured UI gate (Lane U)
```

In `ratchet_engine.py` config validation: if key present and not in `("off", "report", "enforce")`, `bad(...)`. Allow optional per-checkpoint override later via `cp.get("ui_measure_mode")` resolved by a small helper:

```python
def ui_measure_mode_for(cfg, cp=None) -> str:
    raw = (cp or {}).get("ui_measure_mode") if cp else None
    if raw is None:
        raw = cfg.get("ui_measure_mode", "enforce")
    return raw if raw in ("off", "report", "enforce") else "enforce"
```

(Invalid checkpoint override: prefer refuse at plan-check time if easy; else fall back to project mode and log — pick **refuse at plan validate** if `gates.ui` is on and override is garbage.)

- [ ] **Step 3: Run pins + a smoke config test**

```bash
python3 tests/test_ui_measure.py
```

Expected: U-T07 pass.

- [ ] **Step 4: Commit**

```bash
git add skills/ratchet-orchestrator/scripts/engine_utils.py \
        skills/ratchet-orchestrator/scripts/ratchet_engine.py \
        tests/test_ui_measure.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "feat(config): ui_measure_mode off|report|enforce (default enforce)"
```

---

### Task 4: Orchestration helper + `ui_review.py` mode behavior

**Files:**
- Modify: `skills/ratchet-ui-compare/scripts/ui_review.py`
- Optionally add helpers on `ui_measure.py`: `should_run_visual(mode, measure_verdict) -> bool`
- Test: `tests/test_ui_measure.py` U-T05

**Interfaces:**
- Consumes: `ui_measure_mode`, measure JSON
- Produces: `should_run_visual(mode, verdict) -> bool`; `review` path writes `measure.json` beside combined when measure ran

```python
def should_run_visual(mode: str, measure_verdict: str | None) -> bool:
    if mode == "off":
        return True
    if mode == "report":
        return True
    # enforce
    if measure_verdict in (None, "inconclusive"):
        return True
    return False  # pass or fail: visual skipped
```

- [ ] **Step 1: Implement helper + pin U-T05**

Pins:

- `off` + any → visual True, measure not required  
- `report` + `fail` → visual True  
- `enforce` + `pass` → visual False  
- `enforce` + `fail` → visual False  
- `enforce` + `inconclusive` → visual True  

- [ ] **Step 2: Wire `ui_review.py` `review` command**

After capture/manifest is known, before launching reviewers:

1. Resolve mode from project config (engine load).
2. If mode != `off`: run `measure_capture`, write `<out>/measure.json`.
3. If `should_run_visual` is False: still run **behavior** reviewer as today; skip **visual** reviewer; build `combined.json` from measure-derived visual differences (map measured `fail` diffs to `differences` with `fixable_in_code=True`, severity `blocker`) **or** leave visual slot as a synthetic PASS/FAIL review object clearly marked `reviewer: "measure"` so `ui-record`'s "two reviewers" rule still holds — prefer synthetic measure-as-visual reviewer named `"measure"` with the mapped diffs.
4. If visual runs: today's path; attach `measure` key on combined for evidence.

Do **not** skip the behavior reviewer in any mode.

- [ ] **Step 3: Run U-T05 + existing UI precheck/insufficient-evidence suites**

```bash
python3 tests/test_ui_measure.py
python3 tests/test_ui_review_precheck.py
python3 tests/test_ui_insufficient_evidence.py
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add skills/ratchet-ui-compare/scripts/ui_review.py \
        skills/ratchet-ui-compare/scripts/ui_measure.py \
        tests/test_ui_measure.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "feat(ui): mode-aware measure-first review orchestration"
```

---

### Task 5: `ui-record` honors measure modes

**Files:**
- Modify: `skills/ratchet-orchestrator/scripts/ratchet_engine.py` (`cmd_ui_record`, `_ui_verdict` or a sibling `_ui_record_decide`)
- Test: `tests/test_ui_measure.py` (U-T05 extensions / U-T08)

**Interfaces:**
- Consumes: combined review that may include `measure` and/or `reviewers` containing `"measure"`
- Produces: same gate status transitions as today; in `enforce`, measured fail → FAIL even if a stray visual PASS were present; in `report`, measure never alone fails

- [ ] **Step 1: Failing pins for record decision**

Pure helper preferred (easy to test without full Ctx):

```python
def ui_gate_decision(mode: str, measure_verdict: str | None, visual_verdict: str | None,
                     behavior_ok: bool) -> str:
    """Return PASS|FAIL|INVALID. behavior_ok False → FAIL always (unchanged coupling: both lenses required)."""
```

Rules matching the design table. Pin them in U-T08.

- [ ] **Step 2: Implement helper; call from `cmd_ui_record`**

Keep signature checks, fingerprint, frozen-spec, and evidence copy unchanged. After parsing combined:

1. Read `mode = ui_measure_mode_for(cfg, cp)`.
2. Read measure verdict from `review.get("measure", {}).get("verdict")` if present.
3. Compute decision; map to existing PASS/FAIL/INVALID branches (INVALID still for insufficient evidence / stub path).

- [ ] **Step 3: Run measure + a focused gates/UI suite if one covers ui-record**

```bash
python3 tests/test_ui_measure.py
```

Expected: U-T08 pass.

- [ ] **Step 4: Commit**

```bash
git add skills/ratchet-orchestrator/scripts/ratchet_engine.py tests/test_ui_measure.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "feat(ui-record): honor ui_measure_mode enforce/report/off"
```

---

### Task 6: Baseline store + accept command

**Files:**
- Create helpers in `ui_measure.py` or `ratchet_engine.py`: load/save baselines
- Modify: `ratchet_engine.py` argparse + `cmd_ui_baseline_accept`
- Test: `tests/test_ui_measure.py` U-T06
- Docs: `docs/reference.md` / schemas if the new `$RATCHET` command must be listed

**Interfaces:**
- Store path: `<project>/.ratchet/ui_baselines.json` (or under run home if project conventions prefer — match existing evidence patterns; prefer **project** `.ratchet/` so baselines survive runs)
- Command: `ratchet.py ui-baseline-accept <cp> --state <name> --region <x,y,w,h> --reason <text>` (director/human via existing approval patterns if required; at minimum refuse empty reason / unknown state)

- [ ] **Step 1: Failing U-T06 already written — implement store + accept**

```python
def load_baselines(root: Path) -> list[dict]: ...
def save_baselines(root: Path, items: list[dict]) -> None: ...
def accept_baseline(root, *, checkpoint, state, region, reason, accepted_by) -> dict: ...
```

Refuse: empty reason; region with non-positive size; state not in latest measure diffs when a measure.json path is passed (optional `--from-measure`).

- [ ] **Step 2: Wire CLI; list in controls/docs if CP5/CP6 require it**

If `test_docs.py` / schemas require every `$RATCHET` command named in controls to exist, add `ui-baseline-accept` to the controls section and reference Running-the-tests / command tables as those suites demand. Run:

```bash
python3 tests/test_ui_measure.py
python3 tests/test_docs.py
```

Fix inventory / docs until green.

- [ ] **Step 3: Commit**

```bash
git add skills/ratchet-ui-compare/scripts/ui_measure.py \
        skills/ratchet-orchestrator/scripts/ratchet_engine.py \
        tests/test_ui_measure.py docs/
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "feat(ui): lasting scoped baselines + ui-baseline-accept"
```

---

### Task 7: Capture geometry enrichment (optional but in-scope)

**Files:**
- Modify: `skills/ratchet-ui-compare/scripts/capture.mjs`
- Test: extend `tests/test_ui_capture_viewport.py` or add a small node stub test in `test_ui_measure.py` that feeds geometry lists without live Playwright when possible

**Interfaces:**
- Produces: per-target capture result may include `geometry: [{id,x,y,w,h}, ...]` from accessibility snapshot boxes when available; absence remains valid (measure → inconclusive/pixel-only path)

- [ ] **Step 1: Emit geometry when a11y snapshot has bounds**

Keep import-safe `capture.mjs`. Do not fail capture if a11y lacks boxes — omit `geometry`.

- [ ] **Step 2: Run viewport + measure suites**

```bash
python3 tests/test_ui_capture_viewport.py
python3 tests/test_ui_measure.py
```

- [ ] **Step 3: Commit**

```bash
git add skills/ratchet-ui-compare/scripts/capture.mjs tests/
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "feat(capture): emit optional a11y geometry boxes for measure"
```

---

### Task 8: Docs, inventory, fidelity pins

**Files:**
- Modify: `docs/reference.md` (Running the tests: `test_ui_measure.py`; command docs for mode + accept)
- Modify: `skills/ratchet-orchestrator/references/schemas.md` / `docs/how-it-works.md` only where user-visible
- Modify: `tests/fixtures/modularize_cp1/test-inventory.md` if `test_ui_measure.py` matches Kind 1/2 greps
- Modify: `tests/test_ui_gate_fidelity_docs.py` only if it pins UI-gate doc sentences that must mention measure modes

- [ ] **Step 1: Update docs + inventory to match behavior**

- [ ] **Step 2: Run**

```bash
python3 tests/test_docs.py
python3 tests/test_ui_gate_fidelity_docs.py
python3 tests/test_modularize_inventory.py
python3 tests/test_ui_measure.py
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add docs/ skills/ratchet-orchestrator/references/schemas.md tests/
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "docs: Lane U measure modes, baselines, and test inventory"
```

---

### Task 9: Full suite + adversarial + design status

**Files:**
- Modify: `docs/superpowers/specs/2026-11-27-measured-ui-gate-design.md` (status → `approved; implemented`)
- Modify: this plan checkboxes

- [ ] **Step 1: Run full suite**

```bash
PYTHONUNBUFFERED=1 python3 tests/run_all.py
```

Expected: `N passed, 0 failed (suites)`.

- [ ] **Step 2: Adversarial review**

Dispatch a different model. Brief: silent-pass measured fail? `report` enforcing? baseline waiving behavior? visual skipped incorrectly on inconclusive? inventory/docs drift?

Fix Critical/Important; re-run focused suites; re-review until CLEAN.

- [ ] **Step 3: Mark design implemented; final commit; push; update PR**

```bash
git add docs/superpowers/specs/2026-11-27-measured-ui-gate-design.md \
        docs/superpowers/plans/2026-11-27-measured-ui-gate.md
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "docs: Lane U approved; implemented — suite green"
git push -u origin HEAD
```

---

## Spec coverage (self-review)

| Spec item | Task |
|-----------|------|
| Measured-first geometry then pixel | Tasks 1–2, 7 |
| Modes off/report/enforce, default enforce | Tasks 3–5 |
| LLM visual only on inconclusive in enforce | Task 4–5 |
| Behavior reviewer unchanged | Task 4 (never skipped) |
| Lasting scoped baselines + accept | Task 6 |
| Insufficient-evidence → capture-spec | Task 5 (INVALID path preserved) |
| Docs / inventory | Task 8 |
| Adversarial + implemented status | Task 9 |
| No new gate type; paths name `ratchet.py` | Global + Tasks 5–6 |

## Placeholder scan

None intentional. Task 7 notes geometry is optional at capture time; measure already handles absence via `inconclusive` / pixel path (Task 2).
