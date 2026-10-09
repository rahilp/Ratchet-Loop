# Make the test suite fast and let the agent wait for background work: every suite cleans up its temp folders, test_trust is split into smaller fast files, the full test suite runs in parallel and finishes in a few minutes, and the agent can tell Ratchet it is waiting on background reviewers or sub-agents without that counting as a blocked stop

## Purpose
What this feature does, as built and checked by Ratchet (run make-reviews-converge-cheaply-and-the-test-suite, 2026-10-07). Each scenario names the test that proves it. Measured result: the full regression run now takes about 9 minutes with `python3 tests/run_all.py`, down from 35+ minutes running every `tests/test_*.py` file one after another.

## Requirements

### Requirement: Test suites delete their temp folders
The system SHALL do this: running any `tests/test_*.py` suite, alone or all together, leaves no ratchet-* temp folders behind, whether it passes, fails a check on purpose, or crashes, and it stops any background server it started.

- A new `tests/_tmp.py` helper provides `_tmp.mkdtemp(prefix="...")`, usable as a context manager (`with _tmp.mkdtemp(...) as d:`) that removes the directory whether the block exits normally, via `sys.exit()`, or via an uncaught exception, and also removes it at process exit via `atexit` if it was never used as a context manager
- The returned object's `.track(proc)` method registers a `subprocess.Popen` (or anything with `.poll()`/`.terminate()`/`.wait()`) so it is terminated whenever the directory is cleaned up
- Every `tests/test_*.py` file uses this helper instead of calling `tempfile.mkdtemp`/`mkstemp`/`TemporaryDirectory`/`NamedTemporaryFile` directly; `tests/test_cleanup.py` itself is the one exception, since it tests the helper and so makes and removes its own throwaway folders without it
- No existing `check(...)` call in any suite that existed at the pinned base commit was added, removed, or reworded to make this true

#### Scenario: No suite calls tempfile.mkdtemp/mkstemp directly
- **GIVEN** an AST scan of every `tests/test_*.py` file
- **WHEN** looking for raw `tempfile.mkdtemp`/`mkstemp` calls outside `tests/_tmp.py` itself
- **THEN** none are found
<!-- proven by test CP1-T01 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A clean exit through the context-manager form leaves the private TMPDIR empty
- **GIVEN** a child process built around `_tmp.mkdtemp(...)` used as `with ... as d:`, run with TMPDIR pointed at a private, empty sandbox
- **WHEN** the child runs to completion and exits normally
- **THEN** it runs to completion and the private TMPDIR is empty again afterward
<!-- proven by test CP1-T02 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A deliberately failing check inside the with-block still cleans up
- **GIVEN** the same child, but it deliberately fails a check inside the with-block
- **WHEN** the child exits non-zero
- **THEN** the private TMPDIR is still empty afterward
<!-- proven by test CP1-T03 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: An uncaught exception inside the with-block still cleans up
- **GIVEN** the same child, now raising an uncaught exception inside the with-block
- **WHEN** the child crashes
- **THEN** the private TMPDIR is still empty afterward
<!-- proven by test CP1-T04 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A direct sys.exit() after the with-block already exited still leaves nothing behind
- **GIVEN** a child that calls `sys.exit(1)` after its with-block has already exited normally
- **WHEN** the child exits
- **THEN** the private TMPDIR is still empty afterward
<!-- proven by test CP1-T05 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A folder never entered as a context manager is still removed via atexit
- **GIVEN** a child that calls `_tmp.mkdtemp()` but never uses it as a context manager
- **WHEN** the child returns normally
- **THEN** `atexit` still removed the folder, leaving the private TMPDIR empty
<!-- proven by test CP1-T06 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A crash after tracking a background server stops that server too
- **GIVEN** a child that starts a background server and registers it with `.track(proc)`, then crashes
- **WHEN** the crash happens
- **THEN** the private TMPDIR is empty afterward and the tracked background server is no longer running
<!-- proven by test CP1-T07 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A real suite run under a private TMPDIR leaves it exactly as it started
- **GIVEN** `tests/test_hooks.py`, run with a private TMPDIR
- **WHEN** it runs to completion, pass or fail
- **THEN** the private TMPDIR has the same contents before and after the run
<!-- proven by test CP1-T08 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: The checkpoint's own command sequence leaves nothing behind, without recursing
- **GIVEN** the checkpoint's own two-command test sequence, run under a private TMPDIR
- **WHEN** it runs to completion, pass or fail
- **THEN** it is not re-run recursively, and the private TMPDIR is empty afterward, proving the harness itself does not leak
<!-- proven by test CP1-T09 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: No pre-existing check was weakened by the cleanup change
- **GIVEN** the pinned base commit `a24a31d` and its `tests/test_*.py` files, read via `git show`
- **WHEN** diffing them against the current tree
- **THEN** no added or removed line in a suite that existed at that commit touches a `check(...)` call
<!-- proven by test CP1-T10 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A planted bad temp-folder call is caught, and the helper's own calls are not
- **GIVEN** the same scan, extended to also flag `TemporaryDirectory`, `NamedTemporaryFile`, and aliased imports of `mkdtemp`/`mkstemp`
- **WHEN** a disguised bad call is planted as a teeth check, alongside the helper's own legitimate `mkdtemp`/`tempfile.gettempdir()` calls
- **THEN** the planted bad call is found, and the helper's own calls are not flagged
<!-- proven by test CP1-X01 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: A child script that makes its own folders is still cleaned up on crash
- **GIVEN** a child script resembling `test_docs.py`'s engine probes, which itself creates folders inside the suite's helper-managed sandbox
- **WHEN** it crashes
- **THEN** the private TMPDIR is empty afterward, including the child script's own folder
<!-- proven by test CP1-X02 in tests/test_cleanup.py, commit f624da1 -->

#### Scenario: The helper has no third-party dependency
- **GIVEN** the new `tests/_tmp.py` module
- **WHEN** its imports are inspected
- **THEN** it imports only the standard library
<!-- proven by test CP1-X03 in tests/test_cleanup.py, commit f624da1 -->

### Requirement: The slow trust test file is split into fast parts
The system SHALL do this: `tests/test_trust.py` (a single ~7,061-line, roughly 15-minute file) is replaced by `tests/trust_helpers.py` plus several smaller `tests/test_trust_cpN(*).py` files grouped by the step they test, with every check kept exactly as it was and nothing lost or duplicated.

- Every top-level definition (function, class, or assignment) from the pinned pre-split source lands, body unchanged, in exactly one new file, never duplicated and never dropped
- Every `check(...)` call from the pinned original appears, unchanged, in exactly one new file; none is added, removed, or reworded
- `tests/test_trust.py` no longer exists, and no single new file carries every CP id prefix from the old file as a whole-file copy
- CP5 (the largest pinned section) is divided across at least two files, and no new file mixes checks from two different CP groups
- Every new split file imports cleanly on its own
- The two suites that borrowed shared helpers from the old file, `tests/test_results.py` and `tests/test_signing.py`, still pass in full after repointing their import to `tests/trust_helpers.py`
- README's test list no longer runs `tests/test_trust.py` and instead names every new split file; every "proven by test ... in tests/test_trust.py" annotation in the close-ratchet-s-trust-holes-reviews-only-count-f spec now names the new file that holds that test

#### Scenario: Every check(...) call survives the split, none duplicated or reworded
- **GIVEN** the pinned original `tests/test_trust.py` (read via `git show` at the pinned commit), which contains at least one `check(...)` call
- **WHEN** comparing it against every new split file
- **THEN** each `check(...)` call lands in exactly one new file, verbatim; none is missing, none is duplicated, and none appears reworded
<!-- proven by test CP2-T01 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: Every top-level definition survives, body unchanged, in exactly one file
- **GIVEN** the pinned original, with its top-level functions, classes, and assignments
- **WHEN** scanning every new split file (including `tests/trust_helpers.py`) for each pinned definition
- **THEN** every pinned definition appears in exactly one new file with a body identical, verbatim, to the pinned source; none is redefined in more than one file
<!-- proven by test CP2-T02 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: Nothing just renamed the whole file
- **GIVEN** the pinned original covers more than one CP id prefix
- **WHEN** checking the new files and the old path
- **THEN** `tests/test_trust.py` no longer exists, and no single new file contains every CP id prefix from the old file
<!-- proven by test CP2-T03 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: The split groups by CP, with the largest section divided further
- **GIVEN** at least one new split file exists
- **WHEN** checking which CP groups' checks land in which file
- **THEN** CP5 (the largest pinned section) is divided across at least two files, and no new file mixes checks from two different CP groups
<!-- proven by test CP2-T04 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: Every new split file imports cleanly
- **GIVEN** at least one new split file or the helper module exists
- **WHEN** importing it
- **THEN** it imports with no ImportError, NameError, or AttributeError
<!-- proven by test CP2-T05 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: test_results.py still passes after repointing its helper import
- **GIVEN** `tests/test_results.py`, which borrowed shared helpers from the old `tests/test_trust.py`
- **WHEN** its import is repointed to `tests/trust_helpers.py` and the full suite runs
- **THEN** it passes in full
<!-- proven by test CP2-T06 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: test_signing.py still passes after repointing its helper import
- **GIVEN** `tests/test_signing.py`, which borrowed shared helpers the same way
- **WHEN** its import is repointed to `tests/trust_helpers.py` and the full suite runs
- **THEN** it passes in full
<!-- proven by test CP2-T07 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: README's test list names every new file and drops the old one
- **GIVEN** README.md's list of runnable test suites
- **WHEN** scanning it after the split
- **THEN** it no longer lists `python3 tests/test_trust.py`, and it names every new split file
<!-- proven by test CP2-T08 in tests/test_split_check.py, commit 5f394c1 -->

#### Scenario: Spec annotations point at the new files, not the retired one
- **GIVEN** the close-ratchet-s-trust-holes-reviews-only-count-f spec.md, which has "proven by test ... in tests/test_trust.py" annotations
- **WHEN** scanning it after the split
- **THEN** no annotation still names `tests/test_trust.py`; each names the new file that now holds that test
<!-- proven by test CP2-T09 in tests/test_split_check.py, commit 5f394c1 -->

### Requirement: Every test suite runs in parallel, finishing in minutes instead of serially
The system SHALL do this: `python3 tests/run_all.py` discovers and runs every `tests/test_*.py` suite at the same time, each in its own private temp folder, and reports a pass/fail table sorted slowest-first with measured durations, exiting non-zero if any suite failed or left temp files behind.

- Runs half as many suites at a time as the machine has CPU cores, at least 2 (overridable with `--workers N`), because one suite per core was measured to be slower since each suite starts processes of its own
- Kills a suite that outlives `--timeout` (default 15 minutes) and the whole process tree it started, including a process it detached into its own session, found via a tag (`RATCHET_RUN_ALL_TAG`) each suite's processes inherit; on a system without `/proc` it can only stop the suite's own process group, and the summary says so
- Gives each suite its own empty temp folder and reports it as failed, even if its own checks passed, if anything is left in that folder afterward
- Schedules the slowest suites first, remembering how long each suite took last time in a small per-suite duration cache under `~/.cache/ratchet-loop/` (never inside the repo), guessing from file size for a suite not yet timed
- Shows the end of output of every failed suite, a table of every suite and its measured duration (slowest first), and a final "N passed, M failed" line counting suites, not individual checks
- Exits non-zero with a plain "no suites found" message on an empty suite folder, rather than silently claiming success
- README names `python3 tests/run_all.py` as the one command for the full regression run, and `.ratchet/config.json`'s `regression_command` runs it
- `tests/test_runner.py` proves `run_all.py` itself against small, fast, fully deterministic fake suites it writes to a throwaway folder; it never runs the real suites, since that would take 35+ minutes

#### Scenario: Every suite passing is reported and exits 0
- **GIVEN** 5 fake suites that all pass
- **WHEN** `run_all.py` runs
- **THEN** it exits 0, every suite is named in the output, and the summary counts all 5 as passed and none failed
<!-- proven by test CP3-T01 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: Slow suites genuinely run concurrently
- **GIVEN** 4 fake suites that each sleep 2 seconds
- **WHEN** `run_all.py` runs them
- **THEN** it exits 0 and all 4 finish in well under 8 seconds, proving they ran concurrently rather than one after another
<!-- proven by test CP3-T02 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: A failing suite is named and the run exits non-zero
- **GIVEN** one fake suite, `test_bad.py`, that fails on purpose and prints a distinctive marker line
- **WHEN** `run_all.py` runs
- **THEN** it exits non-zero, names `test_bad.py` as failed (not passed), and shows the distinctive marker line from its output
<!-- proven by test CP3-T03 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: A crashing suite is flagged failed with its traceback shown
- **GIVEN** one fake suite, `test_crash.py`, that crashes outright with an uncaught exception
- **WHEN** `run_all.py` runs
- **THEN** it exits non-zero, names `test_crash.py` as failed the same as an ordinary assertion failure, and shows the end of its traceback (exception type and message)
<!-- proven by test CP3-T04 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: Each suite's private temp folder avoids collisions between suites
- **GIVEN** two fake suites that each use a fixed basename inside their own temp folder
- **WHEN** `run_all.py` runs them concurrently
- **THEN** both pass, and neither reports a collision from a shared temp folder
<!-- proven by test CP3-T05 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: A suite that leaks temp files is flagged failed even with exit code 0
- **GIVEN** a fake suite whose own checks pass (exit code 0) but that leaves a stray file in its private temp folder
- **WHEN** `run_all.py` runs
- **THEN** it exits non-zero, names the leaky suite as failed, and the reason mentions leftover/stray temp files, not a check failure
<!-- proven by test CP3-T06 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: A suite that cleans up fully is reported as passed
- **GIVEN** a fake suite that cleans up its private temp folder completely before exiting
- **WHEN** `run_all.py` runs
- **THEN** it exits 0, and the suite is named and reported as passed, not flagged for leftovers
<!-- proven by test CP3-T07 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: README tells a developer to run run_all.py for the full suite
- **GIVEN** README.md's "Running the tests" section
- **WHEN** scanning it
- **THEN** it tells a developer to run `python3 tests/run_all.py`, and that instruction sits near wording about the full/regression test run
<!-- proven by test CP3-T08 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: An empty suite folder is reported, not silently treated as success
- **GIVEN** an empty folder with no `test_*.py` files
- **WHEN** `run_all.py --dir` points at it
- **THEN** it does not crash with an uncaught traceback, prints a plain "no suites found" style message, and exits non-zero rather than silently claiming success
<!-- proven by test CP3-T09 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: --workers 1 is measurably slower than the default worker count
- **GIVEN** 4 fake suites that each sleep about 1 second
- **WHEN** run once with the default worker count and once with `--workers 1`
- **THEN** both runs pass, and `--workers 1` takes measurably longer than the default
<!-- proven by test CP3-T10 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: A hung suite is killed and timed out without blocking the others
- **GIVEN** one fake suite that sleeps 600 seconds (hangs) alongside other fast, passing suites
- **WHEN** `run_all.py` runs with its own `--timeout`
- **THEN** it terminates on its own well under 600 seconds, exits non-zero, names the hung suite as failed/timed out, and the other fast suites still complete and report correctly
<!-- proven by test CP3-T11 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: The final table is sorted slowest-first with measured durations
- **GIVEN** 5 fake suites that all pass, with different run times
- **WHEN** `run_all.py` runs
- **THEN** it exits 0, the final table lists every suite with a measured duration, and the suites are ordered by that duration (not scrambled)
<!-- proven by test CP3-T12 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: One suite's failure doesn't mark the others as failed
- **GIVEN** several passing fake suites alongside one, `test_bad.py`, that fails
- **WHEN** `run_all.py` runs
- **THEN** the overall exit code is non-zero, only `test_bad.py` is flagged as failed, and every passing suite is still marked passed
<!-- proven by test CP3-T13 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: A forced outer timeout leaves no process behind
- **GIVEN** `run_runner()`'s own short outer timeout is forced to fire on a hung fixture
- **WHEN** it fires
- **THEN** `run_runner()` raises TimeoutExpired, reports no return code, and returns quickly; no fake-suite process or setsid-detached grandchild survives (skipped where `/proc` is unavailable)
<!-- proven by test CP3-X01 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: run_all.py's own --timeout kills a hung suite's whole process tree
- **GIVEN** a hung fake suite
- **WHEN** `run_all.py --timeout` fires on its own, before `run_runner()`'s outer timeout would
- **THEN** it exits non-zero and reports the hung suite as timed out, with no fixture process (the hung suite or its detached grandchild) left running afterward (skipped where `/proc` is unavailable)
<!-- proven by test CP3-X02 in tests/test_runner.py, commit 63ab36f -->

#### Scenario: A passing suite's detached background process is still cleaned up
- **GIVEN** a fake suite that detaches a sleeper process tagged with the `RATCHET_RUN_ALL_TAG` run_all.py sets
- **WHEN** the suite passes and `run_all.py` exits 0
- **THEN** no process carrying that run's tag survives afterward (skipped where `/proc` is unavailable)
<!-- proven by test CP3-X03 in tests/test_runner.py, commit 63ab36f -->

### Requirement: The agent can tell Ratchet it is waiting on background work
The system SHALL do this: `ratchet.py wait --for "<reason>" --minutes N --session <id>` lets the run's own chat end its turn while background reviewers or sub-agents are running, without that stop counting toward the blocked-stop limit, for up to N minutes (clamped to 1..120, default 30 when not given).

- Accepted only for the run's owner session; Ratchet decides which chat is the owner from what its own hooks saw, never from an id the caller passes in, and refuses a non-owner session's `wait` with a plain message, writing no record
- While the wait is live and unexpired, and no gate has made progress since it was set, the stop hook allows the stop with no line added to `stop-blocks.jsonl`, so repeated stops during the window never trip the run into 'blocked'
- `--minutes` is clamped: 0 (or anything too low) clamps "until" to about 1 minute out, never 0 or negative; 500 (or anything too high) clamps to no more than about 120 minutes out
- The wait ends early as soon as a gate makes progress, even though "until" hasn't passed yet, so the stop hook blocks again right after progress
- `status`, `status --json`, and `next` carry the wait's reason and until-time while it is live, and stop showing it once it has expired
- README and the ratchet-orchestrator SKILL.md describe the `wait` command

#### Scenario: wait is accepted for the owner session and the stop hook allows the stop
- **GIVEN** a run in progress with its owner session established
- **WHEN** that session runs `ratchet.py wait --for "waiting on reviewers" --minutes 10`
- **THEN** `wait` is accepted, the stop hook allows the stop with no "RATCHET GATE" text, and no line is appended to `stop-blocks.jsonl` for this stop
<!-- proven by test CP4-T01 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: Repeated stops during the wait window never trip the blocked-stop valve
- **GIVEN** one blocked stop has already happened (not yet enough to trip the valve), then the owner session calls `wait`
- **WHEN** 3 more stops happen during the wait window
- **THEN** none of them force the run into 'blocked'
<!-- proven by test CP4-T02 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: --minutes is clamped to 1..120
- **GIVEN** the owner session calls `wait --minutes 0` and separately `wait --minutes 500`
- **WHEN** the resulting "until" time is read
- **THEN** both calls are accepted (clamped, not refused): 0 clamps to about 1 minute out, and 500 clamps to no more than about 120 minutes out
<!-- proven by test CP4-T03 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: An expired wait no longer keeps the stop hook from blocking
- **GIVEN** a wait record backdated so it has already expired
- **WHEN** the stop hook runs again
- **THEN** it blocks again as normal, the blocked stop counts normally toward `max_stop_blocks`, and "Next required action" is still shown in the block message
<!-- proven by test CP4-T04 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: Gate progress ends the wait early
- **GIVEN** a live, unexpired wait is set
- **WHEN** a gate makes progress before "until" has passed
- **THEN** the stop hook blocks again right after that progress, and `status` no longer reports an active wait
<!-- proven by test CP4-T05 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: status and next carry the wait's reason and until-time, then stop once expired
- **GIVEN** a live wait with a reason set
- **WHEN** `next` and `status --json` are read while it is live, and again once it has expired
- **THEN** both carry the wait's reason and an until time (about 7 minutes out, in this fixture) while live, and neither shows it once expired
<!-- proven by test CP4-T06 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: A non-owner session's wait is refused
- **GIVEN** a run whose owner session is established
- **WHEN** a different, non-owner session calls `wait`
- **THEN** it is refused (non-zero exit) with a plain message, no waiting record is created, and the owner session's stop hook is still blocked as normal right after
<!-- proven by test CP4-T07 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: README and the orchestrator skill describe ending a turn to wait
- **GIVEN** README.md and skills/ratchet-orchestrator/SKILL.md
- **WHEN** scanned for text about background work
- **THEN** README describes ending a turn to wait on background work without a blocked stop, and both README and the orchestrator SKILL.md mention the `wait` command
<!-- proven by test CP4-T10 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: A copied owner_session value alone is not proof of being the owner
- **GIVEN** a `--session` value copied from the real owner session, but passed by a process the guard hook never noted as the owner (no tty, no guard-hook note)
- **WHEN** `wait --session <copied id>` is run, and separately with no `--session` at all
- **THEN** both are refused: the refusal is a plain message saying `--session` alone is not proof, no waiting record is created, state.json holds no wait record, and the real owner chat's stop is still blocked and counted as normal
<!-- proven by test CP4-X01 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: Ownership is decided by what the guard hook noted, not by the id argument
- **GIVEN** the guard hook has noted a specific chat as the owner
- **WHEN** a non-owner chat calls `wait` with the owner's id passed via `--session`, and separately the owner chat calls `wait`
- **THEN** the non-owner chat's call is refused and creates no waiting record, while the owner chat's call succeeds and the wait is recorded for the owner chat, whichever chat the user typed to last
<!-- proven by test CP4-X02 in tests/test_waiting.py, commit 3332492 -->

### Requirement: Host-generated turns are never recorded as the human's reply
The system SHALL do this: a turn the CLI itself generates, such as a background sub-agent handing its work back or a background task's own notice, is never recorded as the human's answer to a pending Ratchet question, even when its text contains a listed yes.

- A turn is dropped when any of its lines, after stripping leading whitespace and any `<system-reminder>`/`</system-reminder>` wrapper tags on that line, starts with one of: `Another Claude session sent a message:`, `<task-notification>`, or `[SYSTEM NOTIFICATION - NOT USER INPUT]`
- Any amount of ordinary text may precede the marker line, including several lines of preamble or stacked wrapper tags; piling on preamble does not get the turn recorded
- Only the start of a line is checked: a real message that mentions one of these phrases mid-sentence is recorded as usual
- README or the ratchet-orchestrator SKILL.md says plainly that only a message LEADING with a marker is dropped, and that the human must resend a dropped reply

#### Scenario: A sub-agent hand-back turn is dropped and the question stays open
- **GIVEN** `ask approve-plan` was just run for the owner session
- **WHEN** a turn wrapped in `<system-reminder>` tags reading "Another Claude session sent a message: yes" is sent, followed later by the owner's real "yes, go ahead"
- **THEN** the hand-back turn adds no line to `human_log.jsonl` and leaves the phase at `awaiting_plan_approval`; `approve-plan` still refuses right after the hand-back turn but succeeds after the real reply, which adds exactly one new line
<!-- proven by test CP4-T11 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: A hand-back turn whose wrapped body is a bare "yes" still does not approve
- **GIVEN** `ask approve-plan` was just run
- **WHEN** a hand-back turn whose entire wrapped body is just "yes" is sent
- **THEN** no line is added to `human_log.jsonl`, the pending approve-plan question is still open, `approve-plan` refuses (non-zero exit) with a plain message, and nothing is recorded as a real reply
<!-- proven by test CP4-T12 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: A real reply that only mentions a marker mid-sentence is still recorded
- **GIVEN** `ask approve-plan` was just run
- **WHEN** the owner sends an ordinary message that merely mentions one of the marker phrases mid-sentence (not at the start of a line)
- **THEN** the message is recorded to `human_log.jsonl` unchanged, with its prompt text exactly as sent
<!-- proven by test CP4-T13 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: Stacked wrapper tags around a hand-back marker are still dropped
- **GIVEN** `ask approve-plan` was just run
- **WHEN** turns arrive with several stacked `<system-reminder>` tags, a whole leading reminder block of context lines, or unusual Unicode/zero-width whitespace around the wrapper, each ending in a hand-back or notification marker
- **THEN** none of them add a line to `human_log.jsonl`, and the real "yes, go ahead" sent afterward is still the first reply that approves the plan
<!-- proven by test CP4-X03 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: A marker on a later line of the turn is still dropped
- **GIVEN** `ask approve-plan` was just run
- **WHEN** turns arrive where the marker starts the turn's second or third non-empty line, after other text or blank lines
- **THEN** none of them add a line to `human_log.jsonl`, and the owner's real reply afterward is still the first reply that approves the plan
<!-- proven by test CP4-X04 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: Real messages that mention a marker phrase are recorded, worded two different ways
- **GIVEN** `ask approve-plan` was just run
- **WHEN** the owner sends "can you explain what [SYSTEM NOTIFICATION - NOT USER INPUT] means in that notice?" and separately "why did it say Another Claude session sent a message: earlier?"
- **THEN** each is recorded unchanged to `human_log.jsonl`, adding exactly one line
<!-- proven by test CP4-X05 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: A real yes after 10 lines of ordinary text and a trailing marker line is still dropped, and the next real reply counts
- **GIVEN** `ask approve-plan` was just run
- **WHEN** a turn with 10 lines of ordinary text followed by a marker line is sent, then the owner sends a real "yes, go ahead"
- **THEN** the first turn adds no line to `human_log.jsonl` and leaves the question pending; the owner's real reply afterward adds exactly one line, is the first real reply, and `approve-plan` succeeds
<!-- proven by test CP4-X06 in tests/test_waiting.py, commit 3332492 -->

### Requirement: An oversized reviewer prompt fails cleanly instead of crashing the slot
The system SHALL do this: a reviewer whose CLI takes the prompt only as a command-line argument, and whose rendered prompt would not fit in one exec argument or in the arguments-plus-environment the system allows, is never started; it is marked unavailable with a plain reason, and the slot moves to the next provider.

- `argv_problem()` checks the rendered command against the system's actual per-argument and total argv+env limits (Linux's `MAX_ARG_STRLEN`/`SC_ARG_MAX`, Windows' 32,767-character command line) before `subprocess.run` is ever called, with a safety margin
- An `OSError` with `errno.E2BIG` that still occurs when starting the process is caught and reported the same plain way, not as an uncaught traceback
- `external_review.py` exits 3 and goes through the existing `mark_unavailable()` path, the same as any other reviewer failure, so `ratchet.py reviewers CPn` hands the slot to the next provider without stalling
- The review's output file is written to a temp path and moved into place with `os.replace`, so nothing is ever left half-written at the real output path
- The prompt and the CLI's reply are kept in a per-reviewer temp folder (`ratchet-<reviewer>-...`) only while the review runs, and removed in a `finally` block when it ends, pass or fail; with `--dry-run` it is kept, so the prompt can be read

#### Scenario: An oversized prompt exits 3 with a plain reason, not a crash
- **GIVEN** a reviewer whose command takes the prompt as a literal CLI argument, and a rendered prompt larger than the system allows for one exec argument
- **WHEN** `external_review.py` is run for that reviewer
- **THEN** it exits 3, the failure reason is plain text (not an uncaught OSError traceback), and nothing is left half-written at the review's output path
<!-- proven by test CP4-T08 in tests/test_waiting.py, commit 3332492 -->

#### Scenario: The oversized reviewer's slot moves to the next provider
- **GIVEN** the CP4-T08 failure has just happened
- **WHEN** `ratchet.py reviewers CP1` is run again
- **THEN** it still succeeds (doesn't stall), the failed reviewer is recorded in `unavailable-reviewers.json`, and the round's slot moves to the next available provider instead of the failed one
<!-- proven by test CP4-T09 in tests/test_waiting.py, commit 3332492 -->

## Not in this release
- The guard read-only change (CP5, "Guard lets read-only commands see run files") was pulled after 10 review rounds kept finding new bypass classes in the read-only/write classification, and is saved for the next run.
- Fair round and attempt counting (CP6: a duplicate reviewer slot counts once, a round nobody reviewed is free, a one-off flaky test failure doesn't spend an attempt) moved to the next run.
- Later review rounds focusing on what matters (CP7: minors stop blocking after round 2, majors must be in-scope or a repeat, only reviewers with open findings run again) moved to the next run.
- Review spend visibility, the early-stop offer for a repeatedly-narrowed finding, and letting the user accept a specific open finding with a reason (CP8) moved to the next run.
- The "untested-behavior" guardrail check, an automatic check that fails fast when a new guard rule ships without a test exercising it (CP9), moved to the next run.
