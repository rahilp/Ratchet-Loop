# Engine split (Lane S) — design

Date: 2026-11-26  
Status: approved for implementation planning  
Approach: A — mechanical body move (no hook-path lazy imports)

## Context

`skills/ratchet-orchestrator/scripts/ratchet.py` holds roughly 21,000 lines and costs about 0.23s to compile on every invocation. That cost shows up hard on the PreToolUse guard hook, which runs on every tool call.

Lane S is the foundation for the later director redesign (D), test-speed work (T), and measured UI gate (U). Those lanes assume a stable entry file and an engine module they can load by path.

Out of scope for S: director redesign, test-runtime goals, UI measure gate, and further splitting or lazy-loading inside the engine (approach B, later if needed).

## Goals

- Move the implementation into `ratchet_engine.py`.
- Leave `ratchet.py` as a roughly five-line launcher.
- Preserve existing CLI, hook, import, and test-fixture behavior.
- Land before D and T so they do not keep patching the monolith path.

Non-goals:

- A measured PreToolUse latency win (informational timing only).
- Re-exporting the whole engine from the launcher.
- Renaming the public CLI or hook entry path.

## Architecture

Same directory: `skills/ratchet-orchestrator/scripts/`.

| File | Role |
|------|------|
| `ratchet_engine.py` | Today’s `ratchet.py` body. Behavior unchanged. Keeps existing sibling imports (`engine_utils`, `fingerprint`, `records`, etc.). |
| `ratchet.py` | Tiny launcher only. |

Launcher shape:

```python
#!/usr/bin/env python3
from ratchet_engine import main
if __name__ == "__main__":
    raise SystemExit(main())
```

Load rules:

- **Process entry** (CLI, hooks, `python3 …/ratchet.py …`) stays on `ratchet.py` so installed hook paths and docs keep working.
- **Internals** (`spec_from_file_location`, `cp5y_load_engine`, AST/source scans, helper `_ratchet_module()` paths) load **`ratchet_engine.py`**, where the symbols live.
- Sibling modules that today resolve `…/ratchet.py` by path switch to `…/ratchet_engine.py` (or “engine file beside this module”).
- No public API rename, no new package layout, no lazy-import work in S.

## Compatibility and fixtures

### Keep on the launcher path (`ratchet.py`)

- Any `python3 …/ratchet.py <cmd>` invocation (hooks, install scripts, OpenCode plugin `RATCHET_ENGINE`, docs copy-paste).
- Suites that assert the entry file exists or that hooks embed a path ending in `ratchet.py`.
- Subprocess CLI invocations (still run the launcher, which imports and calls `main`).
- The `main()` / `SystemExit` contract used by `InProcessCall` (the loader points at `ratchet_engine.py` and calls `main` in-process).

### Retarget to `ratchet_engine.py`

- Shared loaders: `tests/trust_helpers.py` `ENGINE` / `cp5y_load_engine()`, and every suite that points `ENGINE` at `…/ratchet.py` for `spec_from_file_location` or in-process `main()`.
- Skill helpers that load the engine by path (`ui_review.py`, `external_review.py`, and `_ratchet_module()` in `engine_utils`, `records`, `run_records`, `plan_support`, `provider_discovery`, `status_text`, `fingerprint`).
- Source/AST inspectors that `read_text()` or parse the engine for symbols (`HOST_ENFORCEMENT`, `LONG_LOCKED_CMDS`, and similar). They must read `ratchet_engine.py`.
- Modularize inventory and docs that define “the engine file” as `ratchet.py` — update to: engine = `ratchet_engine.py`; launcher = `ratchet.py`.

### Special cases

- Fixture trees that write a stub `…/scripts/ratchet.py` keep a launcher-shaped stub (or launcher plus a tiny engine) so path joins still resolve.
- Prefer path load of `ratchet_engine` over bare `import ratchet`. The known `import ratchet as rt` in tests gets the same path-load treatment.
- `spec_from_file_location` module name: use `"ratchet_engine"` unless a test pins another name; match existing assertions rather than renaming for sport.

### Compatibility shim

The launcher does **not** re-export the whole engine. Callers that need internals load `ratchet_engine.py` explicitly. No `ratchet.py` star-import facade (avoids double-loading and keeps the launcher tiny).

## Rollout

Single change set, no feature flag:

1. Add `ratchet_engine.py` via `git mv` of today’s `ratchet.py` (preserve blame), then restore a launcher at `ratchet.py`.
2. Retarget path loaders, source scanners, and `_ratchet_module()` helpers to `ratchet_engine.py`.
3. Update modularize inventory and any doc line that defines “the engine file”.
4. Run the full suite; fix only breakages caused by the path split.
5. Move-only rule for the engine body: no intentional logic changes in the same PR.

Land on `main` before D/T start.

## Risks

| Risk | Mitigation |
|------|------------|
| Missed `…/ratchet.py` hardcode in tests/skills | Repo-wide search for the scripts path and `spec_from_file_location`; source-scanners must open the engine file for symbols, not the launcher |
| Double-load (`ratchet` vs `ratchet_engine` in `sys.modules`) | Loaders use one canonical path/name; launcher never `import *` |
| Hook plugins embed an absolute path to the entry file | Keep the entry filename `ratchet.py`; only internals move |
| Inventory/modularize suites pin “engine is ratchet.py” | Update inventory expectations in the same PR |
| Accidental behavior edit while moving | Move-only PR: no logic changes in the engine body |

## Verification

- Full existing suite green with no intentional behavior diffs.
- Spot-check: `ratchet.py --help`, one hook invocation, and one in-process `cp5y_load_engine()` call all reach engine code through the new layout.
- Optional: record PreToolUse / import timing before and after (informational only; not a merge gate for S).

## Done when

- `ratchet.py` is a tiny launcher; implementation lives in `ratchet_engine.py`.
- CLI, hooks, and in-process fixtures behave as before (full suite green).
- No test or skill loads engine internals from the launcher file.
- S does not require a measured hook-latency win.

## Sequencing after S

D (director/ownership redesign) and T (test speed/output, including tests for D’s new contract) run in parallel on this layout. U (measured UI gate) stays last and is built with Ratchet.
