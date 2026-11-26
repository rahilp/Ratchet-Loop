# Engine split (Lane S) — design

Date: 2026-11-26  
Status: approved; amended mid-implementation for path-emission / name-check catches  
Approach: A — mechanical body move (no hook-path lazy imports)

## Context

`skills/ratchet-orchestrator/scripts/ratchet.py` holds roughly 21,000 lines and costs about 0.23s to compile on every invocation. That cost shows up hard on the PreToolUse guard hook, which runs on every tool call.

Lane S is the foundation for the later director redesign (D), test-speed work (T), and measured UI gate (U). Those lanes assume a stable entry file and an engine module they can load by path.

Out of scope for S: director redesign, test-runtime goals, UI measure gate, and further splitting or lazy-loading inside the engine (approach B, later if needed).

## Goals

- Move the implementation into `ratchet_engine.py`.
- Leave `ratchet.py` as a tiny launcher (may include a `sys.path` insert so sibling import works under `spec_from_file_location`).
- Preserve existing CLI, hook, import, and test-fixture behavior.
- Every path the engine *emits* for humans, hooks, plugins, retries, and background spawns must still name the **launcher** (`ratchet.py`), never `ratchet_engine.py`.
- Land before D and T so they do not keep patching the monolith path.

Non-goals:

- A measured PreToolUse latency win (informational timing only).
- Re-exporting the whole engine from the launcher.
- Renaming the public CLI or hook entry path.

## Architecture

Same directory: `skills/ratchet-orchestrator/scripts/`.

| File | Role |
|------|------|
| `ratchet_engine.py` | Former `ratchet.py` body. Behavior unchanged aside from the path-emission amendment below. Keeps existing sibling imports (`engine_utils`, `fingerprint`, `records`, etc.). |
| `ratchet.py` | Tiny launcher only. |

Launcher shape (canonical):

```python
#!/usr/bin/env python3
"""CLI/hook entry: thin launcher for ratchet_engine.main."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ratchet_engine import main

if __name__ == "__main__":
    raise SystemExit(main())
```

The `sys.path` insert is required so `from ratchet_engine import main` works when the launcher is loaded via `spec_from_file_location` (scripts/ not already on `sys.path`).

### Engine-side `LAUNCHER` constant (required amendment)

Inside `ratchet_engine.py`, next to `SCRIPT_DIR`:

```python
SCRIPT_DIR = Path(__file__).resolve().parent
LAUNCHER = SCRIPT_DIR / "ratchet.py"
```

Use `LAUNCHER` (or `str(LAUNCHER)`) everywhere the engine emits a runnable path. Do **not** use `Path(__file__).resolve()` for those sites after the split.

Must retarget at least:

| Site | Today (broken after naive move) | After amendment |
|------|----------------------------------|-----------------|
| `ENGINE_PATH` | `str(Path(__file__).resolve())` → engine file | `str(LAUNCHER)` |
| `_hook_cmd` / `install-hooks` | hooks invoke engine path | hooks invoke `LAUNCHER` |
| OpenCode plugin slot (`OPENCODE_ENGINE_SLOT`) | filled from engine `__file__` | filled with `LAUNCHER` |
| `_retry_command` | argv[0] = engine `__file__` | argv[0] = `LAUNCHER` |
| `_spawn_background` | spawns engine `__file__` | spawns `LAUNCHER` |
| Agent hint strings that embed `ENGINE_PATH` | point at engine | point at launcher |

Name checks that look for basename `ratchet.py` (`_pid_is_ratchet`, `_strip_ratchet`, hooks-installed greps, guard argv basename checks) stay correct **because** installs and spawns use the launcher. Do not broaden them to `ratchet_engine.py` unless a defense-in-depth pass is explicitly added later; the invariant is “live processes and hook configs name the launcher.”

### Load rules

- **Process entry** (CLI, hooks, `python3 …/ratchet.py …`) stays on `ratchet.py`.
- **Internals** (`spec_from_file_location`, `cp5y_load_engine`, AST/source scans, helper `_ratchet_module()` paths) load **`ratchet_engine.py`**.
- Sibling modules with `_ratchet_module()` (`engine_utils`, `records`, `run_records`, `plan_support`, `provider_discovery`, `status_text`) cold-load `ratchet_engine.py` by path. Keep registering the loaded module as `"ratchet"` in `sys.modules` so an already-running engine is reused. **`fingerprint.py` has no `_ratchet_module()`** — do not invent one.
- The engine body keeps its existing `sys.modules["ratchet"]` alias when loaded (so helpers reuse one instance). Do not rename that registration to `"ratchet_engine"` for the cold-load fallback.
- No public API rename, no new package layout, no lazy-import work in S.

### Test/skill naming convention

- `ENGINE` → `…/scripts/ratchet_engine.py` (in-process load / AST / source scan)
- `LAUNCHER` → `…/scripts/ratchet.py` (subprocess CLI)

`tests/trust_helpers.py` and any suite that both subprocesses and loads must define both.

## Compatibility and fixtures

### Keep on the launcher path (`ratchet.py`)

- Hook configs, install scripts, OpenCode plugin engine path, docs copy-paste.
- Suites that assert the entry file exists or that hooks embed a path ending in `ratchet.py`.
- Subprocess CLI invocations.
- The `main()` / `SystemExit` contract used by `InProcessCall` (loader points at `ratchet_engine.py`).

### Retarget to `ratchet_engine.py`

- Shared loaders: `tests/trust_helpers.py` `ENGINE` / `cp5y_load_engine()`.
- Skill helpers that load by path (`ui_review.py`, `external_review.py`, and the six `_ratchet_module()` siblings listed above).
- Source/AST inspectors for engine symbols.
- Modularize inventory and `modules.md`: engine = `ratchet_engine.py`; launcher = `ratchet.py`.

### Special cases

- Fixture trees that stub `…/scripts/ratchet.py` keep a launcher-shaped stub (and a tiny `ratchet_engine.py` if the stub imports it).
- Prefer path load / `import ratchet_engine` over bare `import ratchet` for internals. Generated “must not import ratchet” fixtures in modularize suites may keep the old string as a negative example.
- `spec_from_file_location` module name for cold loads: keep `"ratchet"` when reusing `sys.modules["ratchet"]`; skill helpers that already use `"ratchet_engine"` may keep that name if they do not participate in the sibling reuse protocol.

### Compatibility shim

The launcher does **not** re-export the whole engine. No `import *` facade.

### Bytecode / `.pyc`

The engine sets `dont_write_bytecode` around sibling imports so the plugin folder is not littered. Importing `ratchet_engine` from the launcher may still write a `.pyc`. That tradeoff is accepted for S unless a follow-up chooses `PYTHONPYCACHEPREFIX` or similar; document whichever choice lands. Correctness does not depend on suppressing `.pyc`.

## Adversarial review (required for this lane)

Lane S is built with a builder → focused tests → **adversarial review** loop before merge, not only task-scoped SDD reviews.

After the mechanical move and the path-emission amendment land:

1. Run the full suite.
2. Dispatch an adversarial reviewer (separate agent) whose job is to find ways the split violates “entry stays `ratchet.py`,” double-loads the engine, breaks hooks/doctor/guard, or leaves loaders pointing at the wrong file.
3. Treat Critical/Important adversarial findings like review-blocking findings: fix, re-test, re-review.
4. Known attack surface for that pass (non-exhaustive): `ENGINE_PATH`, `_hook_cmd`, `_retry_command`, `_spawn_background`, `_pid_is_ratchet`, `_strip_ratchet`, `install-hooks`, OpenCode plugin template, bare `import ratchet`, `sys.modules` double-load, guard suite gaps.

## Rollout

1. `git mv` body → `ratchet_engine.py`; restore launcher at `ratchet.py` (with `sys.path` insert).
2. Retarget path loaders / source scanners / `_ratchet_module()` to `ratchet_engine.py`; keep `"ratchet"` registration name.
3. Introduce engine-side `LAUNCHER`; retarget all emitted runnable paths listed above.
4. Retarget tests/skills to `ENGINE` / `LAUNCHER` convention.
5. Update inventory / `modules.md`.
6. Full suite green; adversarial review; fix findings.
7. Move-only rule for unrelated engine logic: no intentional behavior edits beyond the path-emission amendment.

## Risks

| Risk | Mitigation |
|------|------------|
| `ENGINE_PATH` / hooks point at `ratchet_engine.py` | Engine-side `LAUNCHER` constant; adversarial grep for `Path(__file__)` in emit sites |
| Name checks miss live processes | Keep installs/spawns on launcher basename `ratchet.py` |
| Missed test/skill hardcodes | Repo-wide sweep; full suite; adversarial pass |
| Double-load (`ratchet` vs `ratchet_engine`) | Keep `sys.modules["ratchet"]` alias; launcher never `import *` |
| Inventory pins old engine filename | Update in same PR |
| Accidental logic edits while moving | Diff discipline; only path-emission amendment is intentional |

## Verification

- Full existing suite green with no intentional behavior diffs beyond path emission.
- `install-hooks` (or equivalent unit coverage) writes commands containing `…/ratchet.py`, not `ratchet_engine.py`.
- `ENGINE_PATH` / OpenCode slot / `_retry_command` / `_spawn_background` resolve to the launcher.
- Spot-check: `ratchet.py --help`, one hook invocation, one in-process `cp5y_load_engine()` call.
- Adversarial review report attached (or summarized) on the PR with Critical/Important items closed.
- Optional: PreToolUse / import timing before and after (informational only).

## Done when

- `ratchet.py` is a tiny launcher; implementation lives in `ratchet_engine.py`.
- Every emitted runnable path names the launcher.
- CLI, hooks, and in-process fixtures behave as before (full suite green).
- No test or skill loads engine internals from the launcher file.
- Adversarial review completed with no open Critical/Important findings.
- S does not require a measured hook-latency win.

## Sequencing after S

D (director/ownership redesign) and T (test speed/output, including tests for D’s new contract) run in parallel on this layout. U (measured UI gate) stays last and is built with Ratchet.
