# Stabilize the full-suite load-flaky integration tests (test_status_plain, test_hooks, test_install, test_shortcuts_extra, test_lanes_start_extra) and make run_all's full-suite worker concurrency load-aware

## Purpose
What this feature does, as built and checked by Ratchet (run stabilize-the-full-suite-load-flaky-integration, 2026-10-23). Each scenario names the test that proves it.

## Requirements

### Requirement: Load-aware timeout for install tests
The system SHALL do this: test_install.py's install.sh runs wait longer only when the machine is actually busy, same as today when it isn't.

- Running test_install.py on a normal, idle, or unreadable-load machine behaves exactly as before (effective 120s cap, unchanged)
- A deterministic test (RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS) proves the cap grows under simulated high load, following the same stretch formula run_all.py already uses
- A genuinely hanging install.sh invocation is still stopped and the test still fails -- it is just given a fair, load-scaled amount of time first, never an unbounded wait

#### Scenario: Explicit idle load keeps the install.sh timeout at 120s
- **GIVEN** RATCHET_FAKE_LOADAVG=1, RATCHET_FAKE_CPUS=8 (load/cpus = 0.125, well under 1)
- **WHEN** Env.run() is called
- **THEN** the subprocess timeout passed is exactly 120, unchanged from today
<!-- proven by test CP1-T01 in tests/test_install.py, commit 699da72 -->

#### Scenario: Unreadable load (RATCHET_FAKE_LOADAVG=unavailable) keeps the timeout at 120s
- **GIVEN** RATCHET_FAKE_LOADAVG="unavailable"
- **WHEN** Env.run() is called
- **THEN** the subprocess timeout passed is exactly 120, the same as when load cannot be stretched
<!-- proven by test CP1-T02 in tests/test_install.py, commit 699da72 -->

#### Scenario: A garbage load reading falls back to 120s instead of crashing
- **GIVEN** RATCHET_FAKE_LOADAVG="not-a-number"
- **WHEN** Env.run() is called
- **THEN** no exception is raised and the subprocess timeout passed is exactly 120, matching run_all.read_load()'s own fallback for an unusable reading
<!-- proven by test CP1-T03 in tests/test_install.py, commit 699da72 -->

#### Scenario: Moderate simulated load stretches the timeout by exactly run_all's own factor
- **GIVEN** RATCHET_FAKE_LOADAVG=8, RATCHET_FAKE_CPUS=4 (load/cpus = 2.0, under run_all.DEFAULT_MAX_STRETCH)
- **WHEN** Env.run() is called
- **THEN** the subprocess timeout passed equals 120 * run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] computed fresh with the same fake env (240.0), not a hardcoded or independently-derived number, and the call completes normally (no crash) under the simulated load
<!-- proven by test CP1-T04 in tests/test_install.py, commit 699da72 -->

#### Scenario: Extreme simulated load is capped at run_all's own max stretch, never beyond it
- **GIVEN** RATCHET_FAKE_LOADAVG=400, RATCHET_FAKE_CPUS=4 (load/cpus = 100, far past the ceiling)
- **WHEN** Env.run() is called
- **THEN** the subprocess timeout passed equals 120 * run_all.DEFAULT_MAX_STRETCH (480.0), matching run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] exactly, never 120*100 and never unbounded; the call still completes normally under the simulated extreme load
<!-- proven by test CP1-T05 in tests/test_install.py, commit 699da72 -->

#### Scenario: A genuinely hanging install.sh invocation is still stopped, at a fair stretched limit, never forever
- **GIVEN** RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS set to a known small stretch factor, and the Env instance pointed at a throwaway script that only sleeps, standing in for install.sh so the test does not wait minutes
- **WHEN** Env.run() is called against the hanging script
- **THEN** subprocess.TimeoutExpired is raised at the stretched limit (not the old flat 120s and not an unbounded wait), and the process group is gone afterward, so the surrounding check(...) still reports a clean failure rather than hanging the whole suite
<!-- proven by test CP1-T06 in tests/test_install.py, commit 699da72 -->

### Requirement: Dedup and load-aware timeout in status tests
The system SHALL do this: test_status_plain.py stops double-running test_gates.py/test_hooks.py when run_all.py already covered them, and everything it still runs waits a fair, load-scaled amount of time instead of a flat one.

- When run_all.py's own full run already ran test_gates.py and test_hooks.py as their own suites, CP3-T21 skips re-running them instead of doubling that work
- When run_all.py did not cover them (a filtered run, or test_status_plain.py run on its own), CP3-T21 still runs both suites in full and still proves they pass, now with a load-aware timeout instead of the flat 590s
- Every one of the suite's own rx() engine CLI calls waits longer only when the machine is genuinely busy, matching run_all.py's stretch formula; on a normal machine the wait is the same 120s as today
- A deterministic test (RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS) proves both the skip/don't-skip decision and the stretched timeout, without depending on real machine timing

#### Scenario: rx()'s engine-CLI timeout is unchanged on a normal, idle, or unreadable-load machine
- **GIVEN** RATCHET_FAKE_LOADAVG is unset (real machine), or set to a low number so load/cpus <= 1, or set to "unavailable"
- **WHEN** rx() runs any engine CLI call (e.g. `status`)
- **THEN** the subprocess timeout used is exactly 120, byte for byte the same as before this checkpoint, for all three conditions
<!-- proven by test CP2-T01 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: CP3-T21's own subprocess timeout is unchanged when a suite actually runs on a normal, idle, or unreadable-load machine
- **GIVEN** RATCHET_RUN_ALL unset (so test_gates.py/test_hooks.py are not skipped) and RATCHET_FAKE_LOADAVG unset, low, or "unavailable"
- **WHEN** the CP3-T21 block runs one of the two suites as a real subprocess
- **THEN** the timeout passed to subprocess.run is exactly 590 in every one of the three load conditions, unchanged from before this checkpoint
<!-- proven by test CP2-T02 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: rx()'s timeout stretches under simulated high load, matching run_all.py's own stretch formula exactly
- **GIVEN** RATCHET_FAKE_LOADAVG and RATCHET_FAKE_CPUS set to a handful of explicit high-load pairs (e.g. load=16,cpus=4 and load=40,cpus=4)
- **WHEN** rx() computes its timeout for an engine CLI call
- **THEN** the timeout equals 120 * the factor run_all.stretch_factor(max_stretch) itself returns for that same reading (imported from run_all, not re-derived), never less than 120, and a real `status` call against a throwaway project still completes and passes inside that stretched budget
<!-- proven by test CP2-T03 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: CP3-T21's nested-suite timeout stretches under simulated high load, matching run_all.py's stretch formula exactly
- **GIVEN** RATCHET_RUN_ALL unset (so the suite actually runs) and the same explicit high-load RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS pairs as CP2-T03
- **WHEN** the CP3-T21 block runs test_gates.py or test_hooks.py as a real subprocess
- **THEN** the timeout passed equals 590 * run_all.stretch_factor(max_stretch)'s factor for that same reading, never less than 590, and the real suite still finishes and passes inside that stretched budget (never a shortened one)
<!-- proven by test CP2-T04 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: RATCHET_RUN_ALL unset: CP3-T21 runs both suites as real subprocesses, regardless of RATCHET_RUN_ALL_SUITES
- **GIVEN** RATCHET_RUN_ALL is unset (RATCHET_RUN_ALL_SUITES may be unset, empty, or hold both names)
- **WHEN** the CP3-T21 block runs
- **THEN** test_gates.py and test_hooks.py are both run as real subprocess calls (never skipped) and the block's check(...) call records a pass for each from the real result, exactly as it did before this checkpoint
<!-- proven by test CP2-T05 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: RATCHET_RUN_ALL=1 and RATCHET_RUN_ALL_SUITES names both suites: CP3-T21 skips the subprocess re-run for both, through its one existing check(...) call
- **GIVEN** RATCHET_RUN_ALL=1 and RATCHET_RUN_ALL_SUITES names test_gates.py and test_hooks.py (plus other unrelated suite names, as run_all.py would list)
- **WHEN** the CP3-T21 block runs (subprocess.run stubbed so a real re-run would be visible if it happened)
- **THEN** no subprocess re-run of either file happens, and the SAME pre-existing check(...) call per suite still fires and records a pass for each -- not a second check() call added for the skip path
<!-- proven by test CP2-T06 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: RATCHET_RUN_ALL=1 but RATCHET_RUN_ALL_SUITES names only one of the two suites: the unnamed one still runs in full
- **GIVEN** RATCHET_RUN_ALL=1 and RATCHET_RUN_ALL_SUITES names only test_gates.py
- **WHEN** the CP3-T21 block runs
- **THEN** test_gates.py is skipped (no subprocess call) but test_hooks.py is still run as a real subprocess and still proven pass/fail by its result
<!-- proven by test CP2-T07 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: RATCHET_RUN_ALL_SUITES alone, without RATCHET_RUN_ALL, changes nothing
- **GIVEN** RATCHET_RUN_ALL is unset and RATCHET_RUN_ALL_SUITES names both test_gates.py and test_hooks.py
- **WHEN** the CP3-T21 block runs
- **THEN** both suites still run as real subprocesses -- the suites list alone, without the flag, never triggers a skip
<!-- proven by test CP2-T08 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: The new dedup helper has the exact same shape and behavior as test_trust_cp5_regression.run_all_covers()
- **GIVEN** a matrix of RATCHET_RUN_ALL/RATCHET_RUN_ALL_SUITES combinations (unset; set with both names; set with one name; set with extra unrelated names; suites-only without the flag)
- **WHEN** test_status_plain.py's own covered-set computation for {"test_gates.py", "test_hooks.py"} is compared, combo by combo, against test_trust_cp5_regression.run_all_covers() run on the same environment
- **THEN** the two computed sets are identical for every combination in the matrix -- the same dedup pattern, not a lookalike with different edge behavior
<!-- proven by test CP2-T09 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: The load-aware timeout formula used by rx() and CP3-T21 is exactly run_all.py's stretch_factor(), not a separate formula
- **GIVEN** several explicit (RATCHET_FAKE_LOADAVG, RATCHET_FAKE_CPUS) pairs spanning idle, borderline, and high load, plus "unavailable"
- **WHEN** the effective timeout for rx() (floor 120) and for a CP3-T21 suite that actually runs (floor 590) is computed for each pair
- **THEN** each effective timeout equals floor * run_all.stretch_factor(max_stretch)'s own returned factor for that exact reading -- read via the run_all module test_status_plain.py imports, never a second copy of the math
<!-- proven by test CP2-T10 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: Static guard: every check(...) call present before this checkpoint's fix is still present, unreworded, with its original condition
- **GIVEN** a pinned `git show <base-commit>:tests/test_status_plain.py` taken at the start of this checkpoint, parsed into (name-text, condition-source) pairs for every check(...) call site via ast
- **WHEN** the same extraction runs against the working tree's tests/test_status_plain.py after the fix
- **THEN** every pinned check(...) call's name text and condition source still appears, unchanged, in the working tree (an exact multiset match; new check(...) calls may be ADDED for this checkpoint's own new cases, but none existing may be removed, reworded, or have its condition narrowed) -- this proves only the STATIC half of done_means line 3; the DYNAMIC half (a real regression planted in test_gates.py/test_hooks.py/rx()'s target still failing a real full-suite run under real load) is the orchestrator's own manual, director-facing proof performed outside this checkpoint's test file, and is not automated here
<!-- proven by test CP2-T11 in tests/test_status_plain.py, commit 85eee8e -->

#### Scenario: The CP3-T21 block still records through exactly one check(...) call site per suite name after the fix
- **GIVEN** the working tree's tests/test_status_plain.py after the fix, parsed with ast for check(...) call sites whose name text contains "CP3-T21"
- **WHEN** those call sites are counted
- **THEN** there is exactly one check(...) call site that fires for test_gates.py's result and exactly one for test_hooks.py's result (covered or not), matching the test_trust_cp5_regression.py precedent of folding the skip into the pre-existing call rather than adding a second one
<!-- proven by test CP2-T12 in tests/test_status_plain.py, commit 85eee8e -->

### Requirement: Cap run_all workers under real load
The system SHALL do this: run_all.py starts fewer new suites at once when the machine is already heavily loaded by other work, instead of always launching the same fixed count.

- On a normal, idle, or unreadable-load machine, run_all.py starts exactly as many suites at once as it does today (same default, same --workers behavior) -- byte for byte unchanged
- Under simulated high load (RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS), run_all.py starts fewer new suites at once than requested, never fewer than 2, and never more than the requested or computed-default count
- test_hooks.py, test_shortcuts_extra.py and test_lanes_start_extra.py (the suites the investigation named as symptoms) are not edited; the fix is entirely the shared worker cap in run_all.py
- A deterministic structural test proves the worker count scales down with fake load and is unaffected when load is normal or unavailable, without depending on real machine timing

#### Scenario: Explicit --workers is unchanged on an idle machine
- **GIVEN** A folder with 8 quick suites and RATCHET_FAKE_LOADAVG=1, RATCHET_FAKE_CPUS=8 (load/cpus well under 1, i.e. idle)
- **WHEN** run_all.py --dir <folder> --workers 6 runs
- **THEN** the printed 'Running 8 suites ... 6 at a time' line shows exactly 6, byte for byte what --workers 6 requested, never reduced and never raised
<!-- proven by test CP3-T01 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

#### Scenario: Explicit --workers is unchanged when load is unreadable
- **GIVEN** A folder with 8 quick suites and RATCHET_FAKE_LOADAVG=unavailable
- **WHEN** run_all.py --dir <folder> --workers 6 runs
- **THEN** the printed line shows exactly 6 at a time, identical to today's behavior with no usable load reading
<!-- proven by test CP3-T02 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

#### Scenario: Default worker count is unchanged on an idle machine
- **GIVEN** A folder with run_all.default_workers() + 5 quick suites (no --workers flag) and RATCHET_FAKE_LOADAVG=0.3 (idle)
- **WHEN** run_all.py --dir <folder> runs with no --workers argument
- **THEN** the printed '... W at a time' count equals run_all.default_workers() exactly, the same static value parse_args() used before this checkpoint
<!-- proven by test CP3-T03 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

#### Scenario: Default worker count is unchanged when load is unreadable
- **GIVEN** A folder with run_all.default_workers() + 5 quick suites (no --workers flag) and RATCHET_FAKE_LOADAVG=unavailable
- **WHEN** run_all.py --dir <folder> runs with no --workers argument
- **THEN** the printed count equals run_all.default_workers() exactly, same as before this checkpoint
<!-- proven by test CP3-T04 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

#### Scenario: High simulated load starts fewer suites at once than requested
- **GIVEN** A folder with 8 quick suites and RATCHET_FAKE_LOADAVG=32, RATCHET_FAKE_CPUS=4 (load/cpus=8, heavily loaded)
- **WHEN** run_all.py --dir <folder> --workers 8 runs
- **THEN** the printed count is less than 8 and at least 2 -- fewer new suites are dispatched at once under genuine high load, which is the oversubscription this checkpoint removes
<!-- proven by test CP3-T05 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

#### Scenario: The cap never drops below the floor of 2 even under extreme load
- **GIVEN** A folder with 8 quick suites and RATCHET_FAKE_LOADAVG=1000, RATCHET_FAKE_CPUS=4 (load/cpus=250, far past any realistic ceiling)
- **WHEN** run_all.py --dir <folder> --workers 8 runs
- **THEN** the printed count is exactly 2, never 0 or 1, no matter how extreme the simulated load
<!-- proven by test CP3-T06 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

#### Scenario: The cap is a monotonic function of load, not a constant
- **GIVEN** The same folder of 8 quick suites and --workers 8, run three times with RATCHET_FAKE_CPUS=4 fixed and RATCHET_FAKE_LOADAVG at 4 (load/cpus=1, idle boundary), 16 (load/cpus=4) and 64 (load/cpus=16) in turn
- **WHEN** each of the three run_all.py invocations runs to completion
- **THEN** the printed worker count is non-increasing as load rises across the three runs, and strictly lower at the highest load than at the lowest -- proving the cap reads real load/cpus each time (the same stretch_factor()/read_load() reading style already in the file) rather than returning a hardcoded number, a sleep, or a retry
<!-- proven by test CP3-T07 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

#### Scenario: The cap never raises the count above what was requested, even at the zero-load edge
- **GIVEN** A folder with 6 quick suites and RATCHET_FAKE_LOADAVG=0, RATCHET_FAKE_CPUS=8 (load/cpus=0, the lower edge where a naive inverse formula could overshoot)
- **WHEN** run_all.py --dir <folder> --workers 4 runs
- **THEN** the printed count is exactly 4, never more than the --workers value the user passed, confirming the cap only ever reduces, never inflates, the requested or computed-default count
<!-- proven by test CP3-T08 in tests/test_runner_load_concurrency.py, commit 0d3fd10 -->

### Requirement: No test reads the real machine load
The system SHALL do this: The default-load cases in the status and install tests give the same exact 120s/590s answer on any machine, however busy, and the new worker-cap test file is listed in the README so the docs check passes.

- test_status_plain.py's default-load ('unset'/'idle') cases in CP2-T01, CP2-T02 and CP2-T10 never read the real machine's load or CPU count; they use a known, explicit idle reading
- With the host made to look heavily loaded (os.getloadavg/os.cpu_count faked high, RATCHET_FAKE_* unset), those default cases still check exactly 120 and 590, and test_install.py's CP1-T01/T02 still check exactly 120
- Every ==120 / ==590 check stays as strict as today; none is loosened, skipped or removed
- README.md's 'Running the tests' section lists python3 tests/test_runner_load_concurrency.py (and the new proof file), and test_docs.py's CP6-T05 check passes for every tests/test_*.py

#### Scenario: CP2-T01's default ('unset') sub-case still gives rx() exactly 120s when the real host looks heavily loaded
- **GIVEN** os.getloadavg mocked to return load 64.0 and os.cpu_count mocked to 4 (ratio 16, saturating the 4x stretch cap), with RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS popped so that mock is what read_load()'s fallback actually reads
- **WHEN** test_status_plain.cp2_t01()'s 'unset' sub-case runs rx() under that mock
- **THEN** its own check(...) for 'rx()'s subprocess timeout is exactly 120' passes with r.timeout_limit == 120, not 480 -- on the pre-fix code this same setup makes that check fail (timeout_limit stretched to 480), which is the red proof this case exists to make reproducible without waiting for a genuinely busy real machine
<!-- proven by test CP4-T01 in tests/test_load_determinism.py, commit 425c319 -->

#### Scenario: CP2-T02's default ('unset') sub-case still gives CP3-T21's test_hooks.py run exactly 590s when the real host looks heavily loaded
- **GIVEN** the same os.getloadavg=64.0/os.cpu_count=4 mock as CP4-T01, RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS popped, RATCHET_RUN_ALL unset so the suite is not skipped
- **WHEN** test_status_plain.cp2_t02()'s 'unset' sub-case runs cp3_t21_run_one('test_hooks.py') (stubbed popen, no real suite started) under that mock
- **THEN** its own check(...) for 'the timeout used for test_hooks.py is exactly 590' passes with result.timeout_limit == 590, not 2360 -- on pre-fix code this check fails under the same mock
<!-- proven by test CP4-T02 in tests/test_load_determinism.py, commit 425c319 -->

#### Scenario: CP2-T10's 'idle' sub-case still gives both rx() and CP3-T21 their exact floor timeouts (120/590) when the real host looks heavily loaded
- **GIVEN** the same os.getloadavg=64.0/os.cpu_count=4 mock, RATCHET_FAKE_LOADAVG/RATCHET_FAKE_CPUS popped
- **WHEN** test_status_plain.cp2_t10()'s 'idle' sub-case runs both its rx() call and its stubbed cp3_t21_run_one() call under that mock
- **THEN** both of its own check(...) calls pass: rx()'s effective timeout equals exactly 120.0 and CP3-T21's effective timeout equals exactly 590.0, not the stretched 480.0/2360.0 the pre-fix code would produce under this same mock
<!-- proven by test CP4-T03 in tests/test_load_determinism.py, commit 425c319 -->

#### Scenario: test_install.py's CP1-T01 and CP1-T02 stay exactly 120s when the real host looks heavily loaded -- no fix needed there
- **GIVEN** the same os.getloadavg=64.0/os.cpu_count=4 mock active, while CP1-T01 sets RATCHET_FAKE_LOADAVG=1,RATCHET_FAKE_CPUS=8 and CP1-T02 sets RATCHET_FAKE_LOADAVG=unavailable, exactly as those cases already do
- **WHEN** test_install.cp1_t01() and test_install.cp1_t02() each run Env.run() under that ambient mock
- **THEN** both keep passing their own existing check(...) for exactly 120, unaffected by the mocked host -- confirming these two cases never had the real-machine-fallback defect and guarding against a later, unnecessary rewrite of test_install.py to 'fix' something that was already correct
<!-- proven by test CP4-T04 in tests/test_load_determinism.py, commit 425c319 -->

#### Scenario: Static guard: the fix changes no existing check(...) call's name or condition in test_status_plain.py or test_install.py
- **GIVEN** a pinned `git show <base-commit>:tests/test_status_plain.py` and `git show <base-commit>:tests/test_install.py`, each parsed via ast into (name-text, condition-source) pairs for every check(...) call site
- **WHEN** the same ast extraction runs against the working tree's copies of both files after CP4's fix
- **THEN** every pinned pair from both files still appears, unchanged, in the working tree (an exact multiset match; new check(...) calls, and new setup lines around an existing call such as the idle-mock, may be ADDED, but no existing call's name text or condition source is removed, reworded, or narrowed)
<!-- proven by test CP4-T05 in tests/test_load_determinism.py, commit 425c319 -->

#### Scenario: README documents both of this lane's load-aware test files, and test_docs.py's own doc check passes for every tests/test_*.py file on disk
- **GIVEN** the working tree's README.md after CP4's fix and the real file listing of tests/test_*.py
- **WHEN** test_docs.cp6_tests_documented(readme_text, test_docs.cp6_tests_on_disk()) is called directly, and the 'Running the tests' command list is inspected for the two new lines
- **THEN** cp6_tests_documented(...) returns true, and the command list contains both `python3 tests/test_runner_load_concurrency.py` and `python3 tests/test_load_determinism.py`, each on its own line next to the other load-aware suites
<!-- proven by test CP4-T06 in tests/test_load_determinism.py, commit 425c319 -->

### Requirement: Stop the child when a run is interrupted
The system SHALL do this: When a full-suite run is stopped with Ctrl-C or a terminate signal while a status or install test is waiting on a command, that command is stopped too instead of being left running in the background.

- If a run is interrupted (Ctrl-C) while run_with_stretched_timeout() is waiting on its command, that command's process tree is stopped, and the interrupt still reaches the caller: it is not swallowed
- The same holds when the run is stopped by a terminate signal that the test harness turns into SystemExit
- A command that finishes normally still returns the same result with the same .timeout_limit, and its process tree is not signalled
- A command that runs out of time is still stopped exactly once and still raises the same timeout error with the same limit as before

#### Scenario: A KeyboardInterrupt while waiting on the command still stops its process tree before it propagates
- **GIVEN** a fake proc whose .communicate() raises KeyboardInterrupt on its first poll, with run_all.kill_tree swapped for a spy
- **WHEN** run_with_stretched_timeout() is called against that fake proc (via the injected popen=) and the call is wrapped in the test's own except KeyboardInterrupt:
- **THEN** the spy recorded exactly one call, with that exact fake proc as its argument, and it was called before the test's except KeyboardInterrupt: block catches the exception -- the interrupt is not swallowed inside run_with_stretched_timeout(), it still reaches the caller
<!-- proven by test CP5-T01 in tests/test_stretched_timeout_cleanup.py, commit 3e00b6e -->

#### Scenario: A SystemExit while waiting on the command still stops its process tree before it propagates
- **GIVEN** a fake proc whose .communicate() raises SystemExit on its first poll (standing in for _tmp.py's SIGTERM-to-SystemExit conversion), with run_all.kill_tree swapped for a spy
- **WHEN** run_with_stretched_timeout() is called against that fake proc and the call is wrapped in the test's own except SystemExit:
- **THEN** the spy recorded exactly one call, with that exact fake proc as its argument, and the test's except SystemExit: block still catches it -- the interrupt is not swallowed, it still reaches the caller
<!-- proven by test CP5-T02 in tests/test_stretched_timeout_cleanup.py, commit 3e00b6e -->

#### Scenario: A command that finishes normally is not touched by the interrupt fix
- **GIVEN** a fake proc whose .communicate() returns a normal (returncode, stdout, stderr) with no exception raised, under the fixed fake load
- **WHEN** run_with_stretched_timeout() is called against that fake proc
- **THEN** the result is a CompletedProcess with that exact returncode/stdout/stderr, its .timeout_limit equals base times the fixed stretch factor computed the same way run_all.stretch_factor() computes it under the fixed fake load, and the kill_tree spy recorded exactly zero calls -- the success path is byte-for-byte unchanged by the fix
<!-- proven by test CP5-T03 in tests/test_stretched_timeout_cleanup.py, commit 3e00b6e -->

#### Scenario: A command that runs out of time is still stopped exactly once, not twice
- **GIVEN** a fake proc and fake monotonic() sequence arranged so the loop's own remaining<=0 branch fires with no exception injected (the ordinary timeout-expiry path), under the fixed fake load
- **WHEN** run_with_stretched_timeout() is called against that fake proc
- **THEN** subprocess.TimeoutExpired is raised with the same .timeout as before this fix (base times the fixed stretch factor, the limit in force), and the kill_tree spy recorded exactly one call -- the existing expiry-path call is not duplicated by the new exception handling
<!-- proven by test CP5-T04 in tests/test_stretched_timeout_cleanup.py, commit 3e00b6e -->
