# Module map

`skills/ratchet-orchestrator/scripts/ratchet.py` is a 21,000+ line engine. The behavior-preserving
modularization run (CP1-CP9) moved seven self-contained responsibilities out of it, verbatim, into their own
files, so a maintainer can read and change one of those responsibilities without reading or risking the rest
of ratchet.py. `ratchet.py` imports every name it still needs from these seven modules by explicit, non-star
import; it keeps no `def`/`class` of its own for any of them, and main()'s argparse setup and dispatch stay in
ratchet.py as the stable, thin entry point.

## Modules this run created

- **`engine_utils.py`** -- small, stateless leaf utilities (`now`, `die`, `ok`, `find_root`, `read_json`,
  `write_json`, `slugify`, `config_from_user`) that nearly every ratchet.py command calls into for config
  loading, JSON read/write and basic helpers.
- **`fingerprint.py`** -- working-tree fingerprinting and git-tree bookkeeping (`fingerprint`, `changed_files`,
  `touches_ui`, `_head_commit_named`, `_commit_reviewed`) used by `behavior`, `complete`, `approve-plan`,
  `changes`, `human-approve`, `reviewers`, `spec`, `guardrail`, `mark-fixed` and every other command that must
  tell whether a checkpoint's reviewed code still matches the code on disk.
- **`records.py`** -- owns tamper-evidence record read/write/append and the per-record lock, through its own
  dedicated path (`_read_record`, `_write_record`, `update_record`, `record_chain`, `append_recorded`,
  `chain_problem`, `verify_chain`), used by `accept-state` directly and by almost every other command whenever
  a run record is touched. This is separate from `engine_utils.py`'s plain `read_json`/`write_json`, which
  handle ordinary (non-tamper-evidence) JSON files such as config and checkpoint data.
- **`provider_discovery.py`** -- reviewer-provider discovery, probing, quota and fallback bookkeeping
  (`probe_provider`, `provider_bench`, `quota_failure`, `review_succeeded`, `shared_unavailable`,
  `event_stream`) used by the `providers` command and by the reviewer-selection path every adversarial review
  round takes.
- **`run_records.py`** -- run bookkeeping, hook-env trust and integrity verification (`require_hook_env`,
  `register_active`, `hooks_installed_anywhere`, `hook_env_problem`, `verify_integrity`,
  `approval_snapshot`) used by `approve-plan`, `human-approve`, `complete`, `cancel`, `ci-export`, `resume` and
  the prompt/stop hooks' own trust checks.
- **`plan_support.py`** -- plan/objective validation and the planner's "What earlier work says" earlier-work
  digest (`objective_problems`, `proves_problems`, `objective_brief`, `_earlier_work_lines`) used by `brief`
  and `plan-submit`.
- **`status_text.py`** -- plain-language status, next-action and done-means rendering (`plain_reason`,
  `blocked_options`, `plain_now`, `waiting_question`, `done_means_table`) used by `status-text`,
  `plan-summary` and the `next` command's own next-action text.

## Known, accepted couplings

These two couplings were reviewed and accepted by the director during this run (CP5 and CP6) as real,
intentional limits of a behavior-preserving verbatim-extraction scope -- not silent gaps.

- **CP5 forward-dependency pattern (`_ratchet_module()`).** `provider_discovery.py`, and the other modules
  extracted this run in the same pattern (`engine_utils.py`, `records.py`, `run_records.py`, `plan_support.py`,
  `status_text.py`), resolve roughly 19 lazy `_ratchet_module()`-wrapped names back into `ratchet.py` (names
  such as `ratchet_home`, `require_hook_env`, `_StateLock`, `provider_health`, `roster`, `detect_host`), plus
  `PRESETS`, so each module's own moved functions can still reach a handful of names that have not moved yet.
  `_ratchet_module()` loads `ratchet.py` lazily, by file path, only inside each wrapper's own body at call
  time -- never at the extracted module's own load time -- so this is a deliberate, behavior-preserving
  verbatim-extraction pattern, not a load-time circular import (importing any one of these modules alone, with
  `ratchet.py` never previously imported, never loads `ratchet.py` as a side effect). It is, however, a real,
  intentional forward-dependency coupling back to `ratchet.py`: these modules are not yet fully independent of
  the engine they were extracted from, and that is accepted as a known limit of this run's scope, not a design
  flaw to be fixed here.
- **CP6 `_HOST`/`bind_host()` coupling.** `run_records.py`'s `_HOST`/`bind_host()` first-binder-wins mechanism
  decides which loaded copy of the engine owns the live `_IN_HOOK`/`_HOOK_ENV_ADVISORY` cross-instance identity
  that `require_hook_env` reads. This is a known, accepted limit, forced by CP6-T01's own locked planned test,
  which requires `require_hook_env`'s body to move byte-identical -- ruling out a thread-local or thin-wrapper
  alternative that would have let each loaded copy keep its own, independent `_IN_HOOK` state. It is not a
  design choice that could trivially be improved away: in real use it is safe, because every real CLI or hook
  run is its own single process (only this project's own in-process test harness loads a second copy of the
  engine in the same process), and the alternative designs considered were either blocked by that same lock or
  strictly weaker on trust (leaving `_IN_HOOK` stuck `True` after a mid-hook crash). The director accepted
  `_HOST`/`bind_host()` as-is during CP6's review.

## Still inside ratchet.py, deferred to later features

This run moved seven specific responsibilities only. The following areas are still inside ratchet.py,
deferred to a later feature -- none of them was moved out or given its own module by this run:

- The hooks section (prompt hook, stop hook, guard/command-safety checks, hook installation) stays inside
  ratchet.py, deferred to a later feature.
- `cmd_init` stays inside ratchet.py, deferred to a later feature.
- `cmd_lanes_start` and `cmd_integrate` stay inside ratchet.py, deferred to a later feature.
- `cmd_ask` stays inside ratchet.py, deferred to a later feature.
- Director approvals and signing (director_policy, director_sign, director_key/enroll) stay inside ratchet.py,
  deferred to a later feature.
- Receipts stay inside ratchet.py, deferred to a later feature.
- Spec tools (OpenSpec, Spec Kit, BMAD import) stay inside ratchet.py, deferred to a later feature.
- Shortcut tables stay inside ratchet.py, deferred to a later feature.
- The viewer/dashboard stays inside ratchet.py, deferred to a later feature.
- CI export stays inside ratchet.py, deferred to a later feature.
