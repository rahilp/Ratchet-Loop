# Measured UI gate (Lane U) — design

Date: 2026-11-27  
Status: approved  
Approach: measured compare as a first-class step inside the existing UI gate path (geometry then pixel; modes `off` / `report` / `enforce`)

## Who this is for

Ratchet is run by **AI agents** and an **AI harness**. People use the product through that harness. Lane U makes the UI gate **measured-first** without teaching anyone to open a terminal or changing the checkpoint / gate model.

## Context

After Lanes S (engine split), D (director redesign), and T (test speed), the product contract is stable. The UI gate today:

- Captures reference vs app (`capture.mjs`) with per-state viewports and fidelity rules already landed.
- Runs **LLM** visual + behavior reviewers (`ui_review.py`); `ui-record` computes the verdict.
- Routes insufficient evidence to the **capture spec**, not the implementer.

What is still missing from the original Lane U intent:

- Deterministic **geometry** and **pixel/image** checks.
- **Lasting scoped baselines** for intentional deviations.
- Rollout modes **`off` / `report` / `enforce`**.

Lane U does **not** reopen director identity, tokens, ownership, or test-speed policy.

## Goals (plain language)

1. **Measured-first UI gate.** Geometry compare, then pixel/image compare, against the paired reference capture (masks + tolerance).
2. **Modes with enforce default.** Config `ui_measure_mode`: `off` | `report` | `enforce`, default **`enforce`** when a step has a UI gate. Optional per-checkpoint override.
3. **LLM visual only when needed (enforce).** Clear measured `pass` / `fail` skip LLM visual; measured `inconclusive` (or measure cannot run) escalates to LLM visual. Behavior reviewer stays as today.
4. **Lasting scoped baselines.** Human/director accepts intentional drift (state + region/mask + reason) via an explicit accept path; later rounds treat that scope as expected.
5. **Keep USP.** Same gates/checkpoints; capture fidelity and insufficient-evidence → capture-spec routing stay.

## Non-goals

- Replacing the behavior reviewer.
- Redesigning capture journeys / viewport fidelity (already landed).
- Making measured checks a separate product outside the UI gate.
- Softening adversarial, behavior, or human gates.
- Related-replaces-full or other Lane T reopeners.

## Approach

**Measured compare inside the existing UI gate path** (Approach 1).

Extend capture → review → `ui-record` with a deterministic measure stage. Prefer one gate story for agents over a standalone measure CLI (Approach 2). Do not ship pixel-only v1 and defer geometry (Approach 3) — geometry-then-pixel is the measured layer.

## Architecture

Lane U stays inside the existing UI gate; no new gate type.

| Piece | Role |
|-------|------|
| Config `ui_measure_mode` (`off` / `report` / `enforce`, default **`enforce`**) | Per-project; optional per-checkpoint override |
| Capture (`capture.mjs` + manifest) | Unchanged journeys; may emit richer geometry hooks (a11y/DOM boxes) alongside screenshots |
| **Measure module** (new, beside `ui_review.py` / capture) | Per state: apply masks + baselines → geometry → pixel/image → machine JSON (`pass` / `fail` / `inconclusive` + diffs) |
| `ui_review.py` | Mode-aware orchestration (see Modes below) |
| `ratchet.py ui-record` | Honors mode when recording the UI gate outcome |
| **Baseline store** | Project-scoped accepted deviations (state id, region/mask, reason, who/when) |
| **Accept command** | Harness/director records a baseline from a measured diff (AI/harness-only copy; no pasteable secrets) |

**Invariant:** Behavior and adversarial gates are untouched. Browser `done_means` still require a **passed** UI gate.

## Modes, outcomes, errors

### Mode outcomes (UI gate)

| Mode | Measure runs? | LLM visual | Gate blocks on |
|------|---------------|------------|----------------|
| `off` | No | Always (today) | Today’s visual + behavior rules |
| `report` | Yes (evidence only) | Always | Today’s visual + behavior rules (measure never alone fails the gate) |
| `enforce` (default) | Yes | Only if measure is `inconclusive` (or measure cannot run) | Measured `fail`; or visual FAIL when visual ran; behavior FAIL as today |

### Measured verdicts

- **`pass`** — geometry and pixel within tolerance after masks/baselines.
- **`fail`** — clear geometry or pixel mismatch outside baselines.
- **`inconclusive`** — missing capture/geometry, tool error, or ambiguous threshold → escalate to LLM visual in `enforce`.

### Errors

- Capture/spec problems still route to **insufficient evidence / capture-spec** (not the implementer), same as today.
- Measure tool crash or missing deps → `inconclusive` in `enforce` (never silent-pass); `report` records the error; `off` unaffected.
- Accept baseline without a real measured diff / wrong state → refuse with harness-readable reason.
- Baseline applies only to its scoped state+region; it does **not** waive behavior or adversarial findings.

## Testing & proof

- Guard suite for measure: geometry mismatch → `fail`; masked/baseline region ignored; pixel within tolerance → `pass`; missing geometry → `inconclusive`.
- Mode pins: `off` never calls measure; `report` records measure but gate follows today’s visual verdict; `enforce` skips LLM visual on measured `pass`/`fail` and calls it on `inconclusive`.
- Accept-baseline pins: scoped accept clears only that region; wrong state refused; behavior gate still independent.
- Existing UI suites stay green (`test_ui_*`, fidelity docs, insufficient-evidence routing).
- Adversarial brief: silent-pass of measured fail? `report` accidentally enforcing? baseline waiving behavior?

## Risks

| Risk | Mitigation |
|------|------------|
| Default `enforce` breaks flaky UIs | Tight masks; baselines; `inconclusive`→visual; projects can set `report` / `off` |
| Geometry source fragile across frameworks | Prefer a11y/DOM boxes from capture; fall back toward pixel with `inconclusive` when geometry missing |
| Baseline sprawl waives real bugs | Scoped state+region+reason; listed in status; never waives behavior/adversarial |

## Implementation notes (for the plan)

- Prefer a small measure module next to `skills/ratchet-ui-compare/scripts/` and thin hooks in `ui_review.py` / `ui-record` in `ratchet_engine.py`.
- Emitted runnable paths still name `ratchet.py` (Lane S).
- AI/harness-only copy; commits as Rahil Pirani (`rahil@piranifam.com`).
- Failing-first guards for mode and measure invariants.
- Docs/schemas only where user-visible mode/baseline wording must match.

## Done when

- Modes work as specified; default `enforce`.
- Geometry-then-pixel measure + lasting scoped baselines + accept path land.
- In `enforce`, LLM visual only on `inconclusive` (or measure cannot run).
- Focused + full suite green; adversarial clean on “did we weaken the UI gate?”
- Design status → `approved; implemented`.

## Relationship to other lanes

| Lane | Relation |
|------|----------|
| S | Foundation (launcher + engine). U keeps emitted paths naming `ratchet.py`. |
| D | Done. U does not change director/ownership. |
| T | Done. U does not reopen suite-speed policy. |
| U | This lane — last of the planned four. |
