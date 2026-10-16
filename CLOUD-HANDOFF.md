# Faster feedback: Claude Code cloud handoff

This branch is a portable snapshot of lane H on 2026-10-16. It starts at
`9189bcb`, where checkpoints 1–3 are committed, and carries the unfinished
checkpoint 4 source, documentation, and tests from the local worktree. The
snapshot is **work in progress**: checkpoint 4 has not passed its Ratchet gate,
and checkpoints 5–6 have not started. Do not merge this branch into `main`.

## Work to continue

The feature runs related tests before the full suite, stops a running test
command when the code changes under it, gives a fix round one reviewer, and
shows that reviewer only the fix. Checkpoints 1–3 cover load-aware time limits
and the related-tests pass. Checkpoint 4 is the current task: stop a suite
promptly when code changes, release the suite lock, and leave the gate,
attempt count, and flaky-retry allowance unchanged.

The last blocking review found that the current watcher misses a **new source
file added while the suite runs**. It must watch newly added project source
files as well as edits and deletions. It must still ignore generated build,
cache, report, and scratch output. Add a black-box regression for a new source
file that appears during a running suite, alongside a case showing generated
output does not stop it. The current `README.md` text and tests still describe
all newly appearing files as ignored; update them with the corrected rule.
Preserve the existing tests and their assertions. Investigate other failures
on their merits; do not weaken checks to obtain a pass.

Start with `tests/test_stop_on_change.py` and
`tests/test_stop_on_change_fixes.py`. They exercise the real engine in
throwaway projects and can be run directly with `python3`. After the fix,
run both and any related tests the change affects. Report the commands and
results. The full repository suite is `python3 tests/run_all.py` and should be
run before proposing integration if the cloud environment supports it.

Checkpoints 5 and 6 remain: a fix round uses exactly one reviewer, and its
review prompt contains only the fixer's diff plus the open findings. Treat
these as separate follow-on work after checkpoint 4 is sound.

## Integration boundary

The original H lane is paused in a director-enrolled Ratchet run on another
machine. Its `.ratchet` state, approvals, and review evidence are deliberately
not in this snapshot. The installed Ratchet workflow is not available in the
cloud environment: do not invoke its workflow commands or try to resume the
enrolled run there. Run the repository's plain Python tests only; those tests
exercise the engine source in throwaway projects. Cloud work cannot claim that run's gates
have passed. Work on the new branch Claude Code creates, and leave it for
review; do not merge to `main`. The local director will bring validated
changes into H's enrolled run and complete its checks. The overall merge
order remains H, then J, then I.

## Suggested task for the cloud session

Read `CLOUD-HANDOFF.md` and continue lane H checkpoint 4. Fix the watcher so
a newly added project source file stops an in-flight suite within the watch
interval, while generated build/cache/report/scratch files do not. Add
black-box regression coverage, align the documentation, run the two CP4 test
files and related tests as plain Python commands, and report evidence. The
installed Ratchet workflow is unavailable in the cloud; do not try to run its gates. Keep this as a
reviewable cloud branch; do not merge to main or claim Ratchet gates passed.

## Cloud session result (2026-10-16)

Checkpoint 4's missed-new-file finding is fixed in commit `50b5595` on
`claude/suite-watcher-source-detection-zrt427`. The branch is not merged and
has no PR. No Ratchet gate was run or claimed.

- **Fix** (`ratchet.py`): `_new_source()` decides whether a newly appearing
  file counts as a code change under a running suite. It counts when it has a
  source extension, sits outside every build, dependency, cache, coverage,
  report, snapshot, log, temp, scratch and dot folder, and is not a generated
  name (`*.min.*`, `*.map`, `*~`). A periodic look finds it and stops the
  suite. If only the last look finds it, the run is refused with its own
  guidance: keep the file if you added it; if the command wrote it, remove it
  or add it to `.gitignore`. Edits, deletions, lock release and the unchanged
  gate, attempt and flaky counts behave as before.
- **Tests**: new case `CP4-A6` in `tests/test_stop_on_change_fixes.py`
  (`test_stop_on_change.py` is frozen). It covers a new source file stopping
  `behavior` and `complete`, generated output (not gitignored) never stopping
  a run, a quick suite that writes a source file itself, and the engine rule.
  The new-source checks fail on `9e950b9`. The A4 and C5 check names were
  reworded; their assertions are unchanged.
- **Docs**: `README.md`, `SKILL.md`, `references/schemas.md` and
  `agents/ratchet-test-runner.md` state the new rule.
- **Results**:
  - `python3 tests/test_stop_on_change.py`: 124 passed, 0 failed.
  - `python3 tests/test_stop_on_change_fixes.py`: 89 passed, 0 failed as a
    non-root user. As root, 4 CP4-A2 checks fail, also on `9e950b9`: they need
    a `chmod 0` file that git cannot read, and root can read it.
  - `python3 tests/run_all.py`: `test_receipt` (CP2-X02/X06),
    `test_split_check` (CP2-T07) and `test_trust_cp5_d` (CP5-X65) fail, and
    fail the same way on `9e950b9`. They look environmental; please confirm
    locally. Every other suite passed.
- **Open decision for the director**: watched source patterns are a fixed
  list. They could be a config key locked at plan approval instead.
- Checkpoints 5 and 6 have not started.
