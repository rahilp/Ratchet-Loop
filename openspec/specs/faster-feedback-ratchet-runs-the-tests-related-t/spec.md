# Faster feedback: Ratchet runs the tests related to the files a step changed before the full suite, so a break shows up in seconds; stops a running suite as soon as the code changes under it and frees its locks; a review round after a fix looks only at the fixer's diff, with one reviewer; and test time limits stretch with machine load instead of failing busy machines.

## Purpose
What this feature does, as built and checked by Ratchet (run faster-feedback-ratchet-runs-the-tests-related-t, 2026-10-19). Each scenario names the test that proves it.

## Requirements

### Requirement: Suite time limits stretch on a busy machine
The system SHALL do this: Ratchet's own full test run gives each test file more time when the machine is busy, instead of failing it for being slow.

- On a busy machine a slow test file that would have hit the 15-minute limit is given more time and passes
- On an idle machine the limit is the same as today
- A test file that really hangs is still stopped, at the stretched limit, and reported as timed out
- The summary says when a limit was stretched, by how much and at what load
- README's test section and the run_all.py help describe the stretch and its ceiling

#### Scenario: A slow test file on a busy machine gets more time and passes
- **GIVEN** RATCHET_FAKE_LOADAVG=8 and RATCHET_FAKE_CPUS=4 (a 2x stretch); one fake slow suite sleeps 4.5s; run_all.py is started with --timeout 3
- **WHEN** run_all.py runs that folder
- **THEN** the slow suite is reported passed (not timed out), run_all.py exits 0 and prints '1 passed, 0 failed'
<!-- proven by test CP1-T01 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: On an idle machine the time limit is exactly the configured one
- **GIVEN** RATCHET_FAKE_LOADAVG=0.5 and RATCHET_FAKE_CPUS=4; the same slow suite sleeping 4.5s; --timeout 3
- **WHEN** run_all.py runs that folder
- **THEN** the suite is stopped and reported timed out after about 3s (its stop time is never earlier than 2.9s and the message says 'timed out after 3s' or 3 plus/minus 1), run_all.py exits 1, and the output contains no word 'stretched'
<!-- proven by test CP1-T02 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: A test file that really hangs is still stopped, at the stretched limit
- **GIVEN** RATCHET_FAKE_LOADAVG=8 and RATCHET_FAKE_CPUS=4; one hang suite that never finishes; --timeout 2
- **WHEN** run_all.py runs that folder
- **THEN** the hang suite is stopped no earlier than the stretched 4s (measured duration at least 3.9s) and well before it could finish on its own, is reported FAILED with 'timed out' in its reason, run_all.py exits 1, and the output says 'timed out after 4s' (or 4 plus/minus 1) rather than 2s
<!-- proven by test CP1-T03 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: The summary says when a limit was stretched, by how much and at what load
- **GIVEN** the busy run of T01 (load 8 on 4 CPUs, --timeout 3, slow suite passes at about 4.5s)
- **WHEN** the run ends and prints its summary
- **THEN** the output has one line naming test_alpha.py (the slow suite) that contains 'stretched', the factor '2.0x', the configured limit '3s' and the stretched limit '6s', the load '8.0' and '4 CPUs'; the same line also appears, with the same facts, for a stretched hang that is stopped (T03's run, factor 2.0x, 2s to 4s)
<!-- proven by test CP1-T04 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: README and the --help text describe the stretch and its ceiling
- **GIVEN** README.md's test section (the part that describes tests/run_all.py and --timeout) and the output of 'python3 tests/run_all.py --help'
- **WHEN** both are read
- **THEN** --help lists --max-stretch, states its default of 4 and that the time limit grows with machine load; README's test section mentions --max-stretch, the default ceiling of 4 and that a stretch is reported in the summary, and still mentions the 15-minute limit and --timeout
<!-- proven by test CP1-T05 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: The default ceiling stops a very busy machine from stretching past 4x
- **GIVEN** RATCHET_FAKE_LOADAVG=40 and RATCHET_FAKE_CPUS=4 (a 10x reading); one hang suite; --timeout 1; no --max-stretch given
- **WHEN** run_all.py runs that folder
- **THEN** the hang suite is stopped at the ceiling: duration at least 3.9s and under 8s (not held for 10s), reported timed out, and the summary line says the factor is '4.0x' (1s to 4s) at load 40.0 on 4 CPUs
<!-- proven by test CP1-T06 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: --max-stretch lowers the ceiling
- **GIVEN** RATCHET_FAKE_LOADAVG=40 and RATCHET_FAKE_CPUS=4; one hang suite; --timeout 1 --max-stretch 2
- **WHEN** run_all.py runs that folder
- **THEN** the hang suite is stopped no earlier than 1.9s and before 3.9s (the 4x default would still be running), and the summary line says '2.0x' (1s to 2s)
<!-- proven by test CP1-T07 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: --max-stretch 1 turns the stretch off
- **GIVEN** RATCHET_FAKE_LOADAVG=8 and RATCHET_FAKE_CPUS=4; the slow suite (4.5s); --timeout 3 --max-stretch 1
- **WHEN** run_all.py runs that folder
- **THEN** the slow suite is reported timed out at about 3s exactly as on an idle machine, run_all.py exits 1, and the output contains no word 'stretched'
<!-- proven by test CP1-T08 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: A --max-stretch below 1 or not a number is refused before any suite runs
- **GIVEN** a folder with one quick suite that would leave a marker file in a test-owned folder if it ran
- **WHEN** run_all.py is started with --max-stretch 0.5, then 0, then abc
- **THEN** each call exits 2 and its error text names --max-stretch; no marker file exists, so no suite ran
<!-- proven by test CP1-T09 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: A load spike that starts while a suite is running still extends its limit
- **GIVEN** RATCHET_FAKE_LOADAVG=@<path of reading.txt>, the file starts at 0.5 (idle), RATCHET_FAKE_CPUS=4; the slow suite sleeps 4.5s; --timeout 3; after about 1.5s the test replaces reading.txt atomically with 8
- **WHEN** run_all.py keeps running that suite past its original 3s deadline
- **THEN** the suite is not stopped at 3s: it is reported passed, run_all.py exits 0, and the summary line says the limit was stretched to 6s at load 8.0 (a run that read the load only once at the start would have failed it)
<!-- proven by test CP1-T10 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: With no usable load reading the limit is unchanged and nothing crashes
- **GIVEN** three runs of the slow suite (4.5s) with --timeout 3: RATCHET_FAKE_LOADAVG=unavailable; RATCHET_FAKE_LOADAVG='garbage here'; RATCHET_FAKE_LOADAVG=@<path of a file that does not exist> (RATCHET_FAKE_CPUS=4 in all three)
- **WHEN** run_all.py runs each folder
- **THEN** in all three the slow suite is timed out at about 3s (not stretched), there is no Python traceback in the output, and the output contains no word 'stretched'
<!-- proven by test CP1-T11 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: A machine that is full but not overloaded gets no extra time
- **GIVEN** RATCHET_FAKE_LOADAVG=4 with RATCHET_FAKE_CPUS=4 (load equal to the CPU count, a factor of exactly 1), then again 1 with 8 (load below the CPU count); one hang suite; --timeout 2
- **WHEN** run_all.py runs each folder
- **THEN** in both runs the hang suite is stopped at the configured 2s (never shorter than 1.9s and under 3.9s), reported timed out, and nothing says 'stretched' (the factor never drops below 1, so a lightly loaded machine does not shorten the limit either)
<!-- proven by test CP1-T12 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: A quick suite on a busy machine is not mentioned and is not slowed by the polling
- **GIVEN** RATCHET_FAKE_LOADAVG=40 and RATCHET_FAKE_CPUS=4; one quick suite; default --timeout (900)
- **WHEN** run_all.py runs that folder
- **THEN** the suite passes, run_all.py exits 0 within 5 seconds of wall time, and the output contains no word 'stretched' (the limit was never the thing that mattered, so no stretch is reported)
<!-- proven by test CP1-T13 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: A hang stopped at the stretched limit leaves no process behind
- **GIVEN** RATCHET_FAKE_LOADAVG=8 and RATCHET_FAKE_CPUS=4; the hang-with-child suite, whose detached child records its pid in a test-owned folder; --timeout 2
- **WHEN** run_all.py finishes (the suite is stopped at the stretched 4s)
- **THEN** the suite is reported timed out, and after run_all.py exits neither the suite process nor the recorded child pid is still alive
<!-- proven by test CP1-T14 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: Each suite gets its own stretched deadline when run together
- **GIVEN** RATCHET_FAKE_LOADAVG=8 and RATCHET_FAKE_CPUS=4; two fake suites in one folder, test_alpha.py (slow, 4.5s) and test_beta.py (hang); --timeout 3; --workers 2
- **WHEN** run_all.py runs the folder
- **THEN** test_alpha.py passes and test_beta.py is stopped at 6s and reported timed out; run_all.py exits 1 with '1 passed, 1 failed'; the stretch summary names both suites
<!-- proven by test CP1-T15 in tests/test_runner_load.py, commit c806278 -->

#### Scenario: The tag and run-all variables each suite sees are unchanged
- **GIVEN** RATCHET_FAKE_LOADAVG=8 and RATCHET_FAKE_CPUS=4; the env-probe suite; --timeout 3
- **WHEN** run_all.py runs the folder and the suite records its environment
- **THEN** the recorded environment has RATCHET_RUN_ALL equal to 1, RATCHET_RUN_ALL_SUITES listing the env-probe suite's own file name, and a non-empty RATCHET_RUN_ALL_TAG that contains the suite's stem; TMPDIR points inside run_all.py's own temp area (not the test's folder)
<!-- proven by test CP1-T16 in tests/test_runner_load.py, commit c806278 -->

### Requirement: Ratchet's test runs stretch with load too
The system SHALL do this: When Ratchet runs a step's tests, checks or the full suite on a busy machine, its time limit grows with the load instead of failing the step.

- A step's tests that run slowly only because the machine is busy are not failed for time
- A test command that really hangs is still stopped, at the stretched limit, and the step says it timed out
- The saved test log says the limit was stretched, by how much and at what load
- On an idle machine every limit is exactly the configured one
- README, the orchestrator SKILL.md and schemas.md describe the stretch and its config ceiling

#### Scenario: A step's tests that are slow only because the machine is busy are not failed
- **GIVEN** test_timeout_sec is 2; the step's test script needs about 2.8 seconds; the machine reads load 8 on 4 CPUs (limit should become 4 seconds)
- **WHEN** ratchet.py behavior CP1 is run (red was run first and failed fast)
- **THEN** behavior exits 0 and the gate is passed; the command ran once (no behavior-01b.log, gates.behavior.flaky not set, attempts still 1); the log does not contain 'TIMEOUT'
<!-- proven by test CP2-T01 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: The saved log says the limit was stretched, by how much and at what load
- **GIVEN** the same busy run as CP2-T01 (load 8 on 4 CPUs, configured limit 2 seconds)
- **WHEN** the evidence log for that run (evidence/CP1/behavior-01.log) is read
- **THEN** its header says the limit was stretched (the word 'stretched'), names the configured limit (2s), the limit used (4s) and the load reading (8) with the CPU count (4); the existing first line `$ <command>` and the `# exit=0 duration=` line are still there
<!-- proven by test CP2-T02 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: On an idle machine the limit is exactly the configured one
- **GIVEN** test_timeout_sec is 1; the hang script; three readings in turn: load 0.5 on 4 CPUs, load 4 on 4 CPUs (exactly full), load 0 on 1 CPU
- **WHEN** ratchet.py red CP1 is run once for each reading
- **THEN** each red.log says 'TIMEOUT after 1s' (not 2s, never a shorter limit) and contains no 'stretched' wording; each run took at least 1 second and under 3 seconds
<!-- proven by test CP2-T03 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: A command that really hangs is still stopped, at the stretched limit, and the step says it timed out
- **GIVEN** test_timeout_sec is 1; the hang script; the machine reads load 8 on 4 CPUs (limit becomes 2 seconds); max_behavior_attempts left at its default; src/f.js exists so the script reaches its hang
- **WHEN** ratchet.py behavior CP1 is run
- **THEN** behavior exits non-zero with 'Behavior gate FAILED' and the output shows 'TIMEOUT after 2s'; the gate is failed with attempts 1; behavior-01.log holds the TIMEOUT line and the stretch note; the first run took at least 2 seconds (it was not cut at 1) and the call returned well before 300 seconds
<!-- proven by test CP2-T04 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: A timed-out command's whole process tree is killed, not only its shell
- **GIVEN** the hang script started a background child and wrote its pid to a file; busy load reading so the stop comes at the stretched limit
- **WHEN** red CP1 returns after the timeout
- **THEN** the pid written by the child is no longer alive (signal-0 probe, after a grace of at most 2 seconds), and neither is the script's own pid
<!-- proven by test CP2-T05 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: A load spike in the middle of a run still extends the limit
- **GIVEN** test_timeout_sec is 2; the step script needs 2.8 seconds; the load file starts at 1 on 4 CPUs (factor 1) and the test rewrites it to 12 on 4 CPUs (factor 3) about 0.5 seconds after the command starts
- **WHEN** ratchet.py behavior CP1 is run
- **THEN** behavior exits 0 (the deadline was recomputed from the new reading while the command was running); the log says the limit was stretched and names the higher load 12
<!-- proven by test CP2-T06 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: The stretch has a ceiling, and the ceiling is configurable
- **GIVEN** test_timeout_sec is 1; the hang script; the machine reads load 100 on 4 CPUs (factor 25); load_stretch_max set to 2 for the timing run
- **WHEN** red CP1 is run, and `ratchet.py config` is printed on a fresh project
- **THEN** red.log says 'TIMEOUT after 2s' (not 25s) and the run took under 5 seconds; the printed default config has load_stretch_max equal to 4 (checked on the printed config, not by waiting 4 seconds)
<!-- proven by test CP2-T07 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: A machine with no load reading keeps the configured limit and does not crash
- **GIVEN** RATCHET_FAKE_LOADAVG is 'unavailable' (the same path as a platform without a load average); test_timeout_sec is 1; the hang script
- **WHEN** ratchet.py red CP1 is run
- **THEN** red.log says 'TIMEOUT after 1s' with no stretch wording; the command's stdout and stderr contain no Python traceback
<!-- proven by test CP2-T08 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: The full suite at the end of a step is given more time on a busy machine
- **GIVEN** a reviewed first step of a two-step project; regression_command is a script that sleeps 2.8 seconds and exits 0 (set and confirmed through approve-changes); test_timeout_sec is 2; load 8 on 4 CPUs
- **WHEN** ratchet.py complete CP1 is run
- **THEN** complete exits 0; evidence/CP1/regression.log has the stretch note (2s to 4s, load 8) and no TIMEOUT; no regression-retry.log exists and the run's regression_flaky_used is still unset (a stretched pass is not a retry)
<!-- proven by test CP2-T09 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: An automatic check run after the tests is stretched too
- **GIVEN** one configured automatic check (same shape as CHECK1 in tests/test_ci_export.py) whose command sleeps 2.8 seconds and exits 0, set before plan approval; test_timeout_sec 2; load 8 on 4 CPUs; the step's own tests are fast
- **WHEN** ratchet.py behavior CP1 is run
- **THEN** behavior exits 0; the check's own log (evidence/CP1/check-*-01.log) carries the stretch note and no TIMEOUT
<!-- proven by test CP2-T10 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: A test command that prints a lot of output does not stall or time out
- **GIVEN** a step script that writes about 600 KB to stdout and 600 KB to stderr in one go, ends with a distinctive last line, and exits 0; idle machine; test_timeout_sec 5
- **WHEN** ratchet.py behavior CP1 is run
- **THEN** behavior exits 0 in well under 5 seconds (the wait loop does not deadlock on a full pipe); the log keeps the first command line and the distinctive last line
<!-- proven by test CP2-T11 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: Running a command still behaves as before: environment, folder, exit code, log header
- **GIVEN** an idle machine; a step script that prints its current folder, the CI and FORCE_COLOR environment values and whether RATCHET_TEST_REPORT is set, then exits 3 once src/f.js exists
- **WHEN** red CP1 and then behavior CP1 are run; and once more on a step whose tests.report is 'json' with an old report file already in evidence/CP1
- **THEN** the log shows the project folder, CI=1, FORCE_COLOR=0 and no RATCHET_TEST_REPORT when the step has no report; red.log has the `# exit=3` header; behavior fails with 'exit 3' in its message; with report 'json' the script sees RATCHET_TEST_REPORT pointing inside evidence/CP1 and the old report file is gone before the run
<!-- proven by test CP2-T12 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: A stretched run that passes does not use up the flaky-retry allowance
- **GIVEN** the busy pass of CP2-T01 in a one-step project; then the step's tests are made to fail once and pass on an immediate re-run (a counter file, as in tests/test_flaky.py)
- **WHEN** behavior is run for the busy pass first, and a second step in the same project is run through behavior with the fail-once script
- **THEN** after the stretched pass, gates.behavior.flaky_used is unset on that step; the other step's one-time retry still works and passes marked flaky
<!-- proven by test CP2-T13 in tests/test_load_timeouts.py, commit 7634412 -->

#### Scenario: README, the orchestrator SKILL.md and schemas.md describe the stretch and its ceiling
- **GIVEN** the repository's README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** each file is read
- **THEN** each mentions load_stretch_max and that the time limit stretches with machine load; schemas.md section 8 lists load_stretch_max with default 4 and says an idle machine keeps the configured limit and that the log notes a stretch
<!-- proven by test CP2-T14 in tests/test_load_timeouts.py, commit 7634412 -->

### Requirement: Related tests run before the full suite
The system SHALL do this: Before the full suite at the end of a step, Ratchet runs the few test files related to what the step changed, so a break shows up in seconds.

- Completing a step first runs its related test files (its own tests, test files it changed, and tests that name a file it changed) and says which ones and why each was picked
- If one fails, the step stops within seconds, names the failing file, and the full suite is not started
- If they pass, the full suite still runs afterwards and must still pass, so capping the related list at 8 files never drops coverage
- A change to one big shared file still gives a short related list (at most the configured cap), not the whole suite; but a step that adds a test file, changes a doc, or changes a quoted string in code always also runs the project's doc and wording checks (in Ratchet's own repo, tests/test_docs.py), outside the cap, labelled with the reason such as "doc check: new test file"
- With no related-test command configured, the step goes straight to the full suite and says the related pass is off; README, the orchestrator SKILL.md and schemas.md describe the related pass, its cap, and that the full suite still runs

#### Scenario: Completing a step runs its related test files first, then the full suite
- **GIVEN** Project A with a passing related command and a counting regression command; the step edited src/alpha.js and added tests/new_check.test.js; the step's gates have all passed
- **WHEN** the step is completed
- **THEN** the related command ran exactly once, with exactly tests/cp1.test.js, tests/new_check.test.js and tests/alpha.test.js (not tests/beta.test.js, not tests/misc.test.js); the regression counter read 0 when it ran; afterwards the counter is 1; the step is done and committed
<!-- proven by test CP3-T01 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: The output says which related files were picked and why
- **GIVEN** Project A completed as in T01
- **WHEN** the step is completed
- **THEN** the output has one line per picked file naming the file with its reason: tests/cp1.test.js with 'planned test', tests/new_check.test.js with 'changed test', tests/alpha.test.js with 'names src/alpha.js'; tests/beta.test.js and tests/misc.test.js appear on no such line
<!-- proven by test CP3-T02 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A break in a related test stops the step before the full suite starts
- **GIVEN** Project A; tests/alpha.test.js contains BREAK_ME so the related command fails naming it; the regression command is the 60-second sleeper
- **WHEN** the step is completed
- **THEN** complete exits non-zero; its message names tests/alpha.test.js and says a related test failed; the sleeper was never started (its marker file does not exist); the step is not done and no commit was made
<!-- proven by test CP3-T03 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A related failure costs nothing: gates stay passed, no flaky allowance spent, no automatic re-run
- **GIVEN** Project A as in T03, with a counting regression command
- **WHEN** complete fails on the related pass, the break is removed from tests/alpha.test.js (a test-only edit is restored to the same text it had at behavior time), and complete is run again
- **THEN** after the failure the related command had been called once (no automatic retry), state shows regression_flaky_used unset, the behavior and adversarial gates are still 'passed' with unchanged attempt counts; after the fix the second complete passes the related pass, runs the full suite once, and finishes
<!-- proven by test CP3-T04 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: The related run's output is kept as evidence
- **GIVEN** Project A with a failing related command that prints 'FAIL tests/alpha.test.js'
- **WHEN** the step is completed (fails), and again on a passing project
- **THEN** evidence/CP1/related-tests.log exists in both runs; in the failing run it contains the 'FAIL tests/alpha.test.js' line
<!-- proven by test CP3-T05 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: Related tests passing does not replace the full suite
- **GIVEN** Project A with a passing related command and a regression command that always exits 1
- **WHEN** the step is completed
- **THEN** the regression command ran (counter is at least 1), complete exits non-zero with the existing 'Regression suite failed' message, and the step is not done
<!-- proven by test CP3-T06 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: The full suite's one flaky retry still works, and the related pass is not repeated for it
- **GIVEN** Project A with a passing related command and a regression command that fails once then passes
- **WHEN** the step is completed
- **THEN** the step finishes marked regression_flaky; the regression counter is 2; the related command was called exactly once
<!-- proven by test CP3-T07 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: At most 8 related files are run by default
- **GIVEN** Project C: 12 test files all name the changed src/mod.js; related_tests_max is not set
- **WHEN** the step is completed
- **THEN** the related command received exactly 8 files in one call, the output says it picked 8 of 12 (the cap), and the full suite still ran after it (counter 1)
<!-- proven by test CP3-T08 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A change to one big shared file gives a short list, ranked by the functions that changed
- **GIVEN** Project B: the step changed two functions in src/shared.js; 15 test files name shared.js, 2 of them also mention parseInvoice, 1 also mentions renderTotal
- **WHEN** the step is completed
- **THEN** the related command received at most 8 files; the 3 files that mention parseInvoice or renderTotal are all among them and come before the files that mention neither; the step's own planned test file is among them; the other function names in the file (changed by nobody) do not pull in extra files
<!-- proven by test CP3-T09 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: The cap is a setting
- **GIVEN** Project C with related_tests_max set to 3 before the plan is approved
- **WHEN** the step is completed
- **THEN** the related command received exactly 3 files, including the step's own planned test file; the output says 3 of the total; the full suite still ran
<!-- proven by test CP3-T10 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: With no related-test command configured the step goes straight to the full suite and says so
- **GIVEN** Project A with related_test_command unset
- **WHEN** the step is completed
- **THEN** the output states the related pass is off and names related_test_command; no related log is written; the regression counter is 1; the step finishes as it does today
<!-- proven by test CP3-T11 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: File names with spaces reach the command intact
- **GIVEN** Project D: tests/odd name.test.js names the changed src/lonely2.js
- **WHEN** the step is completed
- **THEN** the related command's arguments include 'tests/odd name.test.js' as a single argument, never split into 'tests/odd' and 'name.test.js'
<!-- proven by test CP3-T12 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A test file the step deleted is not passed to the command
- **GIVEN** Project D: the step deleted tests/old.test.js, which still names src/alpha.js in the base tree
- **WHEN** the step is completed
- **THEN** tests/old.test.js is not among the arguments (the command is not handed a path that no longer exists) and the step completes
<!-- proven by test CP3-T13 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A change no test names still runs the step's own tests, not nothing and not everything
- **GIVEN** Project D: the step changed only src/lonely.js, which no test file names, and added no test files
- **WHEN** the step is completed
- **THEN** the related command received exactly the step's planned test file; unchanged tests/misc.test.js and tests/beta.test.js were not given to it
<!-- proven by test CP3-T14 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A step refused for a stale gate does not start the related pass
- **GIVEN** Project A where a source file was edited after the behavior gate passed, so complete refuses the step as stale
- **WHEN** the step is completed
- **THEN** complete is refused with the existing 'code changed since behavior passed' message; the related command was never called (its log has no lines) and the regression counter is 0
<!-- proven by test CP3-T15 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: Changing the related-test command after approval is a rule change the user must approve
- **GIVEN** an approved plan in Project A; an agent sets related_test_command to a command that always exits 0 using config --set
- **WHEN** the agent then tries to complete the step
- **THEN** the step is refused because a config rule 'related_test_command' changed since approval (the same way doc_test_command is protected); the always-passing command was never run; after approve-changes by the user the new command is accepted
<!-- proven by test CP3-T16 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: The docs describe the related pass
- **GIVEN** the repository's README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** each is read
- **THEN** each mentions related_test_command and related_tests_max (with default 8); each says the full suite still runs afterwards; the README or SKILL.md names the three reasons a file is picked (planned test, changed test, names a changed file)
<!-- proven by test CP3-T17 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: run_all.py can run just the suite files it is given
- **GIVEN** a temp folder of fake suites test_aa.py, test_bb.py, test_cc.py (all passing here) each logging its own name
- **WHEN** run_all.py is called with --dir on that folder and the two file paths test_aa.py and test_bb.py
- **THEN** exit code 0; the log shows only aa and bb ran; the summary lists 2 suites, not 3
<!-- proven by test CP3-T18 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: run_all.py with a failing named file fails and names it
- **GIVEN** the same folder where test_cc.py exits 1
- **WHEN** run_all.py is called with test_aa.py and test_cc.py
- **THEN** exit code 1, the output names test_cc.py as failed, test_bb.py never ran
<!-- proven by test CP3-T19 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: run_all.py refuses a named file that does not exist
- **GIVEN** the same folder
- **WHEN** run_all.py is called with test_aa.py and test_missing.py
- **THEN** non-zero exit with a plain message naming test_missing.py; test_aa.py did not run (nothing starts on a partly wrong list)
<!-- proven by test CP3-T20 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: run_all.py with no file arguments still runs every suite in the folder
- **GIVEN** the same folder (with test_cc.py passing for this case)
- **WHEN** run_all.py is called with only --dir
- **THEN** all three suites ran, exit code 0, as before this change
<!-- proven by test CP3-T21 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A cap that is not a whole number of 1 or more falls back to 8 and says so
- **GIVEN** Project C with related_tests_max set to 0 (and, in a second run, to the text 'many')
- **WHEN** the step is completed
- **THEN** the related command received 8 files and the output says the setting was ignored and 8 was used; the step is never run with zero related files by accident
<!-- proven by test CP3-T22 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A step that adds a new test file always gets the doc check in its related set
- **GIVEN** Project A with related_always = ["tests/doc_check.test.js"] (a file that names no changed source file); the step adds tests/new_check.test.js and changes no doc and no string literal
- **WHEN** the step is completed
- **THEN** the related command received tests/doc_check.test.js along with the usual picks; the output has a line for it with a reason naming the trigger, containing 'doc check' and 'new test file'; the full suite still ran after
<!-- proven by test CP3-T23 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A step that changes a doc file gets the doc check
- **GIVEN** Project A with related_always = ["tests/doc_check.test.js"]; the step edits README.md and adds no test file; its code edits hold no quoted string
- **WHEN** the step is completed
- **THEN** the related command received tests/doc_check.test.js; its reason line contains 'doc check' and 'doc file changed' (a reason different from T23's)
<!-- proven by test CP3-T24 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A step that changes a code line holding a string literal gets the doc check
- **GIVEN** Project A with related_always = ["tests/doc_check.test.js"]; the step edits src/alpha.js so one changed line holds a quoted string (a new user-facing message), adds no test file and changes no doc
- **WHEN** the step is completed
- **THEN** the related command received tests/doc_check.test.js; its reason line contains 'doc check' and 'user-facing text' (or 'string')
<!-- proven by test CP3-T25 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A code-only change with no string literal, no new test and no doc does not add the doc check
- **GIVEN** Project A with related_always = ["tests/doc_check.test.js"]; the step edits only src/alpha.js lines that hold no quote character (e.g. a numeric change and a renamed variable) and adds no test or doc; the step's own tests/cp1.test.js was committed in the base and is only rewritten (its quoted strings do not count)
- **WHEN** the step is completed
- **THEN** tests/doc_check.test.js is not among the related command's arguments (unless it was picked for another reason, which this fixture avoids); the output has no 'doc check' line
<!-- proven by test CP3-T26 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A removed line that held a string literal does not trigger the doc check
- **GIVEN** Project A with related_always set; the step only deletes a line that held a quoted string and changes no other quoted line, adds no test, changes no doc
- **WHEN** the step is completed
- **THEN** the doc check is not added (only a changed or added line holding a string literal counts)
<!-- proven by test CP3-T27 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: The always-related files are kept outside the cap of 8
- **GIVEN** Project C (12 tests name the changed src/mod.js) with related_always = ["tests/doc_check.test.js"], and the step adds a test file so the trigger fires; related_tests_max is unset
- **WHEN** the step is completed
- **THEN** the related command received exactly 9 files in one call: the 8 ranked picks plus tests/doc_check.test.js; the output says 8 of 12 picked by the cap and the doc check was added on top; with related_tests_max=3 the call has 4 files
<!-- proven by test CP3-T28 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: With no related_always setting nothing is added
- **GIVEN** Project A with related_always unset, and a step that adds a test file, changes README.md and changes a quoted string
- **WHEN** the step is completed
- **THEN** the related command received exactly the T01 set (own, changed, naming); no line says 'doc check'; tests/doc_check.test.js was not given to it
<!-- proven by test CP3-T29 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: A failure in the always-related file stops the step before the full suite and names it
- **GIVEN** Project A with related_always = ["tests/doc_check.test.js"]; the step adds a test file; tests/doc_check.test.js contains BREAK_ME so the related command fails naming it; the regression command is the 60-second sleeper
- **WHEN** the step is completed
- **THEN** complete exits non-zero, its message names tests/doc_check.test.js, the sleeper was never started, the step is not done, no flaky allowance was spent
<!-- proven by test CP3-T30 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: Changing related_always after approval is a locked rule change
- **GIVEN** an approved plan in Project A; an agent empties related_always (or points it at a file that always passes) with config --set
- **WHEN** the agent then tries to complete the step
- **THEN** the step is refused because the config rule 'related_always' changed since approval; the weakened list was not used; after approve-changes by the user the new list is accepted
<!-- proven by test CP3-T31 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: An always-related file that is also a ranked pick is passed only once
- **GIVEN** Project A with related_always = ["tests/alpha.test.js"] (which T01 already picks by naming), and the step adds a test file
- **WHEN** the step is completed
- **THEN** tests/alpha.test.js appears once in the related command's arguments; its reason line names both reasons or the first, never two lines; the count against the cap is as if it were a ranked pick
<!-- proven by test CP3-T32 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: An always-related file that does not exist is skipped with a plain note
- **GIVEN** Project A with related_always = ["tests/gone.test.js"] and a step that adds a test file
- **WHEN** the step is completed
- **THEN** the related command is not handed tests/gone.test.js; the output says the file named in related_always was not found; the step still runs the rest and the full suite
<!-- proven by test CP3-T33 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: Ratchet's own repo setup lists tests/test_docs.py as always related by default
- **GIVEN** a fresh throwaway project with no related_always setting (this repo's own ["tests/test_docs.py"] line is added through approve-changes and is not read by this test)
- **WHEN** DEFAULT config is loaded in a fresh project
- **THEN** related_always is an empty list for any project; the README, orchestrator SKILL.md and schemas.md each mention related_always, its three triggers (new test file, doc file changed, user-facing string) and that these files sit outside the cap
<!-- proven by test CP3-T34 in tests/test_related_tests.py, commit 9189bcb -->

#### Scenario: No related pass when the docs-only skip means the full suite does not run
- **GIVEN** the two-step fixture used by the docs quick path tests (CP2 test already true, green_first_ok): CP1 completed with the counting regression command passing (counter 1) and doc_test_command set; related_test_command set to the logging fake; after CP2's gates pass, the only change since the passing suite is an edit to README.md that a test file names
- **WHEN** CP2 is completed
- **THEN** the full suite is skipped (counter stays 1) and the doc tests ran for the README edit; the related command was not called for CP2 (its log has no CP2 line) and the output does not say the related pass failed or is off for lack of configuration; a CP2 completion where the full suite does run still calls it (guard against a stub that never runs it)
<!-- proven by test CP3-T35 in tests/test_related_tests.py, commit 9189bcb -->

### Requirement: Stop the suite when the code changes
The system SHALL do this: If the code changes while Ratchet is running tests for a step, it stops that run at once, says why and frees its locks, instead of finishing a result that can no longer count.

- Editing a file while a step's suite is running stops the suite within about half a minute, with a message saying the code changed and to run the step again
- After the stop, the next Ratchet command in that project runs at once (no lock left behind) and no test process from the stopped run is still alive
- A stopped run never counts as a pass, never counts as a failed attempt, and never uses up the one flaky-retry allowance
- A run where nothing changed finishes exactly as before
- README, the orchestrator SKILL.md and schemas.md describe the stop and its check interval

#### Scenario: Editing a file while a step's tests run stops the run quickly and says why
- **GIVEN** The step is at the behavior stage, its test command sleeps for up to two minutes, and suite_watch_seconds is 0.3
- **WHEN** `ratchet.py behavior CP1` is running and, once the tests have started, the agent appends a line to src/a.js
- **THEN** The behavior command ends within a few seconds of the edit (far less than the tests' two minutes) with a non-zero exit, and its output says the code changed while the tests were running, that the run was stopped for that reason, and to run `behavior CP1` again
<!-- proven by test CP4-T01 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: The saved log of a stopped run says it was stopped because the code changed
- **GIVEN** The same stopped behavior run as CP4-T01
- **WHEN** The step's evidence folder is read
- **THEN** The log for that run exists and says the run was stopped because the code changed; it does not read like a test failure or a timeout
<!-- proven by test CP4-T02 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: No test process from the stopped run is still alive
- **GIVEN** The fake tests start a shell, a grandchild process, and a second grandchild, all in the tests' own process group, and tag their environment with a unique value
- **WHEN** The run is stopped by an edit and the ratchet command has exited
- **THEN** Within a few seconds none of the recorded pids is alive (a zombie awaiting reaping does not count as alive) and no process anywhere carries the tag
<!-- proven by test CP4-T03 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A test run that ignores the polite stop is still removed
- **GIVEN** The fake tests trap and ignore the terminate signal, and so does their grandchild
- **WHEN** The run is stopped by an edit
- **THEN** The ratchet command still ends within about ten seconds and no process carrying the tag is alive afterwards
<!-- proven by test CP4-T04 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: Test runs that start their own detached helpers are cleaned up like tests/run_all.py does
- **GIVEN** The fake tests start a helper in its own session (as run_all.py's suites do) and, on the terminate signal, stop that helper before exiting
- **WHEN** The run is stopped by an edit
- **THEN** Neither the fake tests nor the detached helper is alive afterwards, and the ratchet command exited normally
<!-- proven by test CP4-T05 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: Only the stopped run's processes are stopped, not bystanders or Ratchet itself
- **GIVEN** A bystander 'sleep 120' in its own process group is running in the same project folder, and the ratchet command runs in its own group
- **WHEN** A run is stopped by an edit
- **THEN** The bystander is still alive, and the ratchet command ended by exiting itself with its own exit code (not killed by a signal) after printing its message
<!-- proven by test CP4-T06 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: After a stop the next command runs at once with no lock left behind
- **GIVEN** A behavior run was just stopped by an edit
- **WHEN** The agent immediately runs `ratchet.py behavior CP1` again (the edit is kept, the test command now exits 0 after a moment) and then looks in .ratchet
- **THEN** Right after the stop there is no state.lock and no state.lock.break file; the next command is not refused as busy, does not wait for a lock to expire, and starts its tests within a couple of seconds
<!-- proven by test CP4-T07 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A stopped run is not a pass and not a failed attempt
- **GIVEN** A step with behavior attempts 0, no flaky allowance used, behavior not yet passed
- **WHEN** A behavior run is stopped by an edit
- **THEN** In state.json the behavior gate is not passed and holds no pass fingerprint, its attempts count, flaky_used and `last` are exactly as before the call (`runs` may have gone up, it only numbers log files), the step has not moved forward, and the history has no behavior_failed entry
<!-- proven by test CP4-T08 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: Stopping again and again never blocks the step
- **GIVEN** max_behavior_attempts is 2
- **WHEN** Three behavior runs in a row are each stopped by an edit
- **THEN** The step is never marked blocked or needing a human, attempts is still 0, and the following uninterrupted run (tests exit 0) still passes the gate on its first counted attempt
<!-- proven by test CP4-T09 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A stopped run does not use up the one flaky-retry allowance
- **GIVEN** A step whose test command is counter-driven
- **WHEN** One run is stopped by an edit, and the next behavior run has the tests fail once and then pass on the automatic immediate re-run
- **THEN** After the stop flaky_used is still unset; the next run then passes marked flaky (the allowance was still available) and attempts stays at what that run's own start gave
<!-- proven by test CP4-T10 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A stopped automatic re-run puts the allowance and the attempt back
- **GIVEN** The step's tests fail at once on their first call, and the automatic immediate re-run hangs
- **WHEN** The edit arrives while the re-run is going and it is stopped
- **THEN** The command ends as a stop (not as a failure), and in state.json flaky_used is unset and attempts is what it was before the call, because the failing first call belonged to code that no longer counts
<!-- proven by test CP4-T11 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: Completing a step stops when the code changes during the full suite
- **GIVEN** A step at the complete stage with every gate passed and regression_command running a slow fake that sleeps up to two minutes
- **WHEN** `ratchet.py complete CP1` is running and the agent appends a line to src/a.js
- **THEN** The complete command ends within a few seconds with a message that the code changed and the step must be run again; no checkpoint commit is made (git log is unchanged), the step is still at complete or stale as the engine's normal rule leaves it, regression_flaky_used and regression_flaky are unset, no state.lock is left, and the regression log says the run was stopped because the code changed
<!-- proven by test CP4-T12 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A stopped full-suite re-run does not use up the run's flaky allowance
- **GIVEN** The regression command fails at once on its first call and the automatic re-run hangs
- **WHEN** The edit arrives during the re-run and it is stopped
- **THEN** regression_flaky_used is unset in state.json, nothing is marked as a failed regression, the stop message is shown, and no process of the stopped re-run is alive
<!-- proven by test CP4-T13 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: After the edit is undone, completing the step works as if nothing had happened
- **GIVEN** A complete run was stopped by an edit to src/a.js
- **WHEN** The agent restores src/a.js to what it was (so the code matches what the gates passed on) and runs `complete CP1` again, now with a fast regression command
- **THEN** The step completes, the checkpoint commit is made, and the regression run counted as a normal first run (flaky allowance still available)
<!-- proven by test CP4-T14 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A stop during the related-tests pass never starts the full suite
- **GIVEN** related_test_command is set to the slow fake and regression_command to a fake that writes a marker file if it is ever started
- **WHEN** `complete CP1` is in its related-tests pass and the agent edits src/a.js
- **THEN** The complete command ends with the code-changed message, not with a 'related tests failed' message naming a test file, the regression marker file does not exist, and the run's counters are unchanged
<!-- proven by test CP4-T15 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A stop during an automatic check is not a failed check
- **GIVEN** The config has one automatic check whose command is the slow fake, and the step's own tests pass quickly
- **WHEN** `behavior CP1` is running the check and the agent edits src/a.js
- **THEN** The command ends with the code-changed message, not 'automatic check failed'; attempts, gate status and `last` are as before the call, and no process of the check is alive
<!-- proven by test CP4-T16 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A behavior run that passes after the code was edited under it does not count
- **GIVEN** suite_watch_seconds is large (30) and the step's tests take about 2 s then exit 0
- **WHEN** The agent edits src/a.js one second into the run, so the tests end before the next look
- **THEN** The behavior gate does not become passed, no pass fingerprint covering the edited code is saved, attempts is unchanged, and the output says the code changed during the run and to run the step again
<!-- proven by test CP4-T17 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A full suite that passes after the code was edited under it does not complete the step
- **GIVEN** suite_watch_seconds is large (30) and the regression command takes about 2 s then exits 0
- **WHEN** The agent edits src/a.js one second into the run
- **THEN** complete does not commit and does not finish the step, says the code changed during the run (not that the suite itself wrote files), and leaves the flaky allowance unset
<!-- proven by test CP4-T18 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A run where nothing changes finishes exactly as before
- **GIVEN** suite_watch_seconds is 0.3 and the step's tests sleep 2 s then exit 0, with nobody editing anything
- **WHEN** `behavior CP1` runs
- **THEN** The gate passes with attempts 1, the log shows the tests' output with exit 0 and no mention of a stop, flaky is not set, and the exit code and message are the same as the same project run with suite_watch_seconds at its default
<!-- proven by test CP4-T19 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: An unchanged full-suite run still completes and commits
- **GIVEN** A step at complete with a regression command that sleeps 2 s then exits 0, suite_watch_seconds 0.3
- **WHEN** `complete CP1` runs with nobody editing anything
- **THEN** The step completes, one checkpoint commit is made with the usual message, and regression.log has no mention of a stop
<!-- proven by test CP4-T20 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: Files the tests themselves write do not count as a code change
- **GIVEN** The fake suite, while running for a few seconds with the watch on, writes into a folder listed in .gitignore, into a __pycache__ folder, into the step's own test report file under .ratchet, and into a scratch folder outside the project
- **WHEN** The behavior run and the complete run each go through it
- **THEN** Neither is stopped; both finish and pass as in CP4-T19 and CP4-T20, and the gate's saved fingerprint equals the one a run with no such writes gets
<!-- proven by test CP4-T21 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: The agent's own chat transcript changing inside the project does not stop a run
- **GIVEN** The chat transcript is a file inside the project that the prompt hook has recorded for a real user message, and the tests run for a few seconds
- **WHEN** A new chat line is appended to that transcript while the tests run
- **THEN** The run is not stopped and finishes normally, because the same rule that `complete` uses to ignore the chat transcript for staleness decides here too
<!-- proven by test CP4-T22 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: A test run that really hangs still times out as a failure
- **GIVEN** test_timeout_sec is a few seconds, the tests hang, and nothing is edited
- **WHEN** `behavior CP1` runs
- **THEN** The run is stopped by the time limit, the output says it timed out (not that the code changed), and it counts as one failed attempt exactly as it did before this step
<!-- proven by test CP4-T23 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: The check interval has a default of 30 seconds and refuses nonsense values
- **GIVEN** A fresh project with no suite_watch_seconds set
- **WHEN** The config is shown, then `config --set suite_watch_seconds=0.3`, then 0, -5 and abc are tried
- **THEN** The default shown is 30; 0.3 is accepted and used; 0, a negative number and a non-number are each refused (or rejected with a plain message) and leave the stored value as it was
<!-- proven by test CP4-T24 in tests/test_stop_on_change.py, commit 582e560 -->

#### Scenario: The docs describe the stop and its check interval
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** They are searched
- **THEN** Each names suite_watch_seconds and says a suite is stopped when the code changes, that a stopped run is not a pass, not a failed attempt and does not use the flaky retry, and that the default check interval is 30 seconds; the schemas config table lists the key with its default
<!-- proven by test CP4-T25 in tests/test_stop_on_change.py, commit 582e560 -->

### Requirement: Fix rounds get one reviewer
The system SHALL do this: After reviewers block a step and the fixer has run, the next review round asks one reviewer instead of the step's whole set.

- Every round that follows a blocked round, once the fixer has run, names exactly one reviewer and says it is a fix round, whatever the fix's size or the step's risk; a first round, or one that does not follow a block, still gets the full set
- That reviewer is the one whose blocking finding is being fixed, same provider and same kind of review (a Codex correctness finding is checked by Codex correctness); when several blocked, one from a company other than the host's is preferred
- If that reviewer's provider is unavailable, the slot's usual stand-in for that kind of review takes it, never the host's catch-all reviewer used for low-risk steps
- A fix round that blocks again is handled by the existing check-in and round limits, unchanged, and every fix round still counts toward the step's and the run's review limits
- The commit message and status name each fix round and who reviewed it, and README, both SKILL.md files and schemas.md describe the fix round

#### Scenario: After a block and a fix, the next round asks one reviewer and says it is a fix round
- **GIVEN** A normal-risk step (host-only reviewers, providers=[]) whose round 1 named two reviewers; only the correctness reviewer wrote a blocker (src/f.js, user_impact set); the finding is triaged as fix and the fixer rewrites about 40 lines of src/f.js; behavior is re-run
- **WHEN** `reviewers CP1 --json` opens round 2 (and the plain `reviewers CP1` text is read)
- **THEN** round 2 names exactly one reviewer (not two) and it is the reviewer whose finding was fixed (the round-1 correctness reviewer's id, lens correctness), round is 2, `small_fix` is true, the kind value says it follows a block (not approval), and the printed text says it is a fix round with one reviewer
<!-- proven by test CP5-T01 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A fix round gets one reviewer even in a high-risk step and even for a big fix
- **GIVEN** A high-risk step whose round 1 named four reviewers and blocked; the fixer changed 60 lines
- **WHEN** round 2 opens
- **THEN** round 2 names exactly one reviewer, the one (a host reviewer here) whose finding was fixed, `small_fix` true, kind 'after a block'; no size or risk condition applies
<!-- proven by test CP5-T02 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: When an outside reviewer blocked, that same provider and lens re-checks the fix, and the host never does
- **GIVEN** A normal-risk step with a fake codex CLI on PATH (providers=[codex]) and review_depth.normal = ["codex-standards", "host-correctness"]; round 1 ran both, only codex-standards wrote a blocker (triaged fix); the fixer ran; behavior re-run
- **WHEN** round 2 opens (`reviewers CP1 --json`)
- **THEN** round 2 names exactly one reviewer, exactly codex-standards (same provider, same lens), `small_fix` true and kind 'after a block'; it is not host-both, not review_depth["low"]'s pick, and not host-correctness
<!-- proven by test CP5-T03 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A first round still gets the step's full reviewer set
- **GIVEN** A normal-risk step and a high-risk step, each with no earlier round; and a step whose round 1 was opened, then src/f.js edited again before any review was written
- **WHEN** round 1 opens (and is re-opened after the extra edit)
- **THEN** normal risk names two reviewers, high risk names four, `small_fix` false and no fix-round wording; the re-opened round 1 is still the full set and costs no extra attempt
<!-- proven by test CP5-T04 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A round after a block with no fix made is not a fix round
- **GIVEN** A normal-risk step whose round 1 blocked with blockers from both reviewers; every finding is triaged as rebut with a reason, and no file is changed
- **WHEN** round 2 opens
- **THEN** `small_fix` is false and the round carries no fix-round wording; it follows today's rules (the rebuttal is shown, narrowing as before), not the one-reviewer path
<!-- proven by test CP5-T05 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A round after a clean approval is not a post-block fix round, however the edit looks
- **GIVEN** A normal-risk step whose round 1 was approved clean by both reviewers, then a 30-line code edit (over lane B's 20-line limit)
- **WHEN** the next round opens
- **THEN** it names the full two-reviewer set, `small_fix` false, no fix-round wording: only a blocked round followed by a fixer run triggers this step's rule
<!-- proven by test CP5-T06 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: Lane B's small fix after approval still works and is labelled as after approval
- **GIVEN** A normal-risk step approved clean at round 1, then a 5-line edit
- **WHEN** the next round opens
- **THEN** one reviewer, `small_fix` true, and the kind value says after approval (not after a block); this step did not change B's size or risk rules
<!-- proven by test CP5-T07 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A step passed by the user accepting a finding does not make the next round a fix round
- **GIVEN** A normal-risk step whose round 1 blocked; the user accepted the open finding with their own `accept:` reply so the gate shows passed; the code is then edited
- **WHEN** the next round opens
- **THEN** it names the full two-reviewer set, `small_fix` false: an accepted finding is not a blocked round
<!-- proven by test CP5-T08 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: The fix round never consults the step's low-risk reviewer setting
- **GIVEN** Two normal-risk projects (fake codex CLI on PATH, review_depth.normal = ["codex-standards", "host-correctness"], codex-standards blocked and fixed): one with review_depth.low left at its default (an any-both slot that would resolve to host-both), one with review_depth.low customized to two slots, and one with it set to a single-lens id
- **WHEN** round 2 opens in each
- **THEN** in all three round 2 names exactly one reviewer, codex-standards, `small_fix` true; the answer does not change with review_depth.low, and the round never falls back to a full round just because low would not resolve to one both-lens reviewer (lane B's post-approval rule, T07, still uses its own resolver)
<!-- proven by test CP5-T09 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A step that pins its own reviewer list keeps its pinned set after a block
- **GIVEN** A step whose plan lists its own `reviewers` (two ids); round 1 blocked and fixed
- **WHEN** round 2 opens
- **THEN** round 2 names the pinned reviewers, `small_fix` false, as lane B's path already treats a pinned list
<!-- proven by test CP5-T10 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A fix round counts toward the step's and the run's review budget
- **GIVEN** A normal-risk step: round 1 blocked and fixed; attempts and the run-wide round count are read from state.json before opening round 2
- **WHEN** round 2 (a fix round) opens, and then a replaced-before-review re-open of that fix round happens after one more edit
- **THEN** the gate's attempts and the run's adv_rounds_total each rise by exactly one for the fix round, like any round; the re-open before any review was written is refunded as today and adds nothing
<!-- proven by test CP5-T11 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A fix round that blocks again at the step's round limit blocks the step; no extra round is granted
- **GIVEN** max_adversarial_rounds=2 and checkin_round=99; round 1 blocked and fixed; round 2 is a one-reviewer fix round that records a blocker again
- **WHEN** the agent triages, fixes and asks for another round
- **THEN** the step is blocked at 2 of 2 rounds with the usual escalation message, `reviewers` is refused and attempts stays 2, and no third round folder exists
<!-- proven by test CP5-T12 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: The run-wide round limit still stops fix rounds
- **GIVEN** max_adversarial_rounds_run=2 and checkin_round=99; round 1 blocked and fixed; round 2 (fix round) blocked again
- **WHEN** another round is requested
- **THEN** it is refused with the run-wide cap message and adv_rounds_total stays 2
<!-- proven by test CP5-T13 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A user-approved raise grants exactly one more round, and it is again a one-reviewer fix round
- **GIVEN** A step blocked at its 2-round cap by a blocking fix round; the user replies `raise: <reason>` after `ask raise-limit CP1`; the fixer runs again
- **WHEN** `reviewers CP1` opens the one extra round and it records a blocker again
- **THEN** the extra round names one reviewer and is a fix round; attempts equals the raised cap, the step is blocked again, and a further round is refused: the budget was not reset by this step
<!-- proven by test CP5-T14 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A fix round that blocks again goes to the usual check-in before another round opens
- **GIVEN** default checkin_round=2 and max_adversarial_rounds=6; round 1 blocked and fixed; round 2 is a one-reviewer fix round that blocks again
- **WHEN** `reviewers CP1` is run again, then `check-in CP1 --decision simplify --why ...` is recorded, the finding is triaged, the fixer runs and `reviewers CP1` is run
- **THEN** the first call is refused naming the open check-in and no round 3 opens; after the check-in is answered round 3 opens as a fix round with one reviewer (not the full set), and attempts is 3
<!-- proven by test CP5-T15 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A second fix round after a blocked fix round also gets one reviewer
- **GIVEN** A normal-risk step: round 1 blocked, round 2 (fix round) blocked again, the fixer ran again; limits raised out of the way (checkin_round=99)
- **WHEN** round 3 opens
- **THEN** round 3 names exactly one reviewer, the same reviewer that blocked round 2, and is a fix round: a blocked fix round does not bring back the full set (today's narrowing rule is replaced by the fix round for these rounds)
<!-- proven by test CP5-T16 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A fix round approved by its one reviewer passes the gate, and the step's tests and full suite still run
- **GIVEN** A normal-risk step whose round 1 blocked and was fixed; round 2 (fix round) is approved by its one reviewer with a clean review; the regression command is a script that always fails
- **WHEN** `complete CP1` is run after the real sequence (fixer edit, `complete` refused as stale, `behavior CP1`, `reviewers CP1`, `adv-record`)
- **THEN** adv-record accepts the single review, the adversarial gate shows passed, and `complete CP1` still runs the regression command and fails naming it (the suite is not skipped); with a passing regression command it then completes
<!-- proven by test CP5-T17 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: Status and the commit message name which rounds were fix rounds and who reviewed them
- **GIVEN** A normal-risk step: round 1 (two reviewers) blocked, round 2 (fix round) blocked again, round 3 (fix round) approved by its one reviewer; the step completes
- **WHEN** `status --json`, the plain status and the commit message are read
- **THEN** each says rounds 2 and 3 were fix rounds after a block and names the reviewer of each; round 1 is not called a fix round; the text matches the real round numbers and reviewer ids
<!-- proven by test CP5-T18 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: Status names a fix round and its reviewer as soon as it has been reviewed, even when it blocked
- **GIVEN** A normal-risk step whose round 2 (fix round) was recorded with a blocker, nothing approved yet
- **WHEN** `status --json` is read
- **THEN** it already says round 2 was a fix round after a block and names its reviewer; the full round 1 is not listed as one
<!-- proven by test CP5-T19 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A fix round's approval is not treated as a full review for later small-fix decisions
- **GIVEN** A normal-risk step: round 1 blocked and fixed, round 2 (fix round) approved by its one reviewer; behavior passes; then a 3-line edit is made before completing
- **WHEN** the next round opens
- **THEN** it names the step's full two-reviewer set, `small_fix` false (the one-reviewer approval never becomes the baseline lane B measures a later small fix against, and `last_full_review_fingerprint` is unchanged)
<!-- proven by test CP5-T20 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: A fix round that is replaced before anyone reviewed it is still a fix round
- **GIVEN** A normal-risk step whose round 1 blocked and was fixed; round 2 (fix round, one reviewer) was opened but nothing was written; the fixer edits src/f.js once more
- **WHEN** `reviewers CP1` opens the replacing round
- **THEN** it names one reviewer, `small_fix` is true with kind 'after a block', and attempts and the run total are the same as before the replacement (refunded as today)
<!-- proven by test CP5-T21 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: If the fix round's reviewer fails mid-round, the slot's normal replacement for the same lens takes over and it stays a one-reviewer fix round
- **GIVEN** The CP5-T03 project (codex-standards blocked, fixed) with round 2 open as a fix round for codex-standards; the fake codex CLI then fails with exit code 3 (or the reviewer is marked unavailable with `reviewer-status --mark`) before it writes its review (fake-CLI approach as lane B's CP3-X02)
- **WHEN** `reviewers CP1` is run again in the same round, the replacement writes a clean review, adv-record runs and the step completes
- **THEN** the round still names exactly one reviewer, with lens standards and not codex-standards (the observable result of the engine's existing same-round replacement for that slot, host fallback included; the test checks the outcome, not how it is computed), `small_fix` stays true; the commit and status name the replacement as the reviewer, not codex-standards; no provider that had not reviewed the step is added
<!-- proven by test CP5-T22 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: The docs describe the fix round after a block
- **GIVEN** The shipped README.md, skills/ratchet-adversarial-loop/SKILL.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md, read as text
- **WHEN** each is searched for the fix-round rule
- **THEN** each says that a round after a blocked round, once the fixer has run, asks one reviewer (the one whose blocking findings were fixed: same provider and lens, preferring a company other than the host's, using that slot's normal fallback if it is unavailable, and never a provider that had not reviewed the step) whatever the fix's size or the step's risk, that it still counts toward the step and run limits and goes to the usual check-in when it blocks again, and schemas.md names the new kind field; README's 'Later rounds cost less' paragraph and B's small-fix paragraph agree with each other
<!-- proven by test CP5-T23 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: Two reviewers blocked, one the host and one another company: the other company re-checks the fix
- **GIVEN** Normal-risk step, fake codex CLI on PATH, review_depth.normal = ["host-correctness", "codex-standards"] (the host reviewer listed first on purpose); both wrote a blocker, both triaged fix; the fixer ran
- **WHEN** round 2 opens
- **THEN** round 2 names exactly one reviewer, codex-standards, `small_fix` true; the host reviewer, although first in the list, is not chosen
<!-- proven by test CP5-T24 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: Two reviewers from other companies blocked: the pick is the first one listed in the blocked round, every time
- **GIVEN** Normal-risk step, fake codex and fake gemini CLIs on PATH; project A has review_depth.normal = ["codex-standards", "gemini-correctness"], project B the same list reversed; both reviewers blocked in each and were triaged fix; the fixer ran. Rule under test: among the blocking other-company reviewers, the one with the lowest slot number in the blocked round
- **WHEN** round 2 opens in each project, and `reviewers CP1 --json` is then called a second time in the same project
- **THEN** project A names exactly codex-standards, project B exactly gemini-correctness; the second call in each project names the same reviewer and the same round (no reshuffle, no extra round)
<!-- proven by test CP5-T25 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: If the reviewer that would re-check is unavailable, the slot's normal fallback for that lens is used
- **GIVEN** The CP5-T03 project (codex-standards blocked, fixed) with codex-standards benched before the next round opens (`reviewer-status --mark codex-standards --reason "quota exceeded"`)
- **WHEN** round 2 opens
- **THEN** round 2 names exactly one reviewer with lens standards that is not codex-standards (the slot's same-lens replacement as the engine's own reviewer selection gives it today, host fallback included; the test checks that observable result and does not re-implement the selection), `small_fix` true, the reviewer row shows it stood in for codex-standards with the reason, it is never the low-risk host-both pick, and codex-standards' bench mark (its entry in the unavailable-reviewers record, reason and until time) is exactly as it was before the round opened; other availability or start-count records may change, since opening a round reserves starts and probes providers, and are not asserted. The same expectation holds when the share limit, not a bench mark, refuses codex-standards (provider_share.max=0 for codex): same-lens replacement, still one reviewer
<!-- proven by test CP5-T26 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: The fix reviewer always comes from the blocked round, never a provider that had not reviewed the step
- **GIVEN** Two projects with fake codex and fake gemini CLIs on PATH: (a) review_depth.normal = ["codex-standards", "host-correctness"], codex-standards blocked; gemini is installed and configured but never reviewed; (b) a host-only round 1 (providers=[] then codex added to config before the fix round), host-correctness blocked
- **WHEN** round 2 opens in each
- **THEN** (a) names codex-standards and never any gemini-* id; (b) names host-correctness and never codex-* or gemini-*: the chosen id is always one of the blocked round's own reviewer ids
<!-- proven by test CP5-T27 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: In a high-risk step, an outside reviewer's block is re-checked by that reviewer, not by the host
- **GIVEN** High-risk step with a fake codex CLI on PATH and review_depth.high = ["codex-standards", "codex-correctness", "host-correctness", "host-standards"]; only codex-correctness wrote a blocker (triaged fix); the fixer changed 60 lines
- **WHEN** round 2 opens
- **THEN** round 2 names exactly one reviewer, exactly codex-correctness; no host-* id (host-both included) is named
<!-- proven by test CP5-T28 in tests/test_fix_round.py, commit c643617 -->

#### Scenario: Only a reviewer whose blocking finding the fixer was meant to fix can be chosen
- **GIVEN** Normal-risk step, fake codex CLI on PATH, review_depth.normal = ["codex-standards", "host-correctness"]; both blocked, but codex-standards's finding was triaged rebut (with a reason) and host-correctness's was triaged fix; the fixer ran
- **WHEN** round 2 opens
- **THEN** round 2 names exactly one reviewer, host-correctness (the only reviewer with a finding to be fixed), even though codex-standards is the other company
<!-- proven by test CP5-T29 in tests/test_fix_round.py, commit c643617 -->

### Requirement: Fix reviewer sees only the fix
The system SHALL do this: In a one-reviewer fix round, the reviewer is given only the fixer's diff and the findings it was meant to fix, and a quick review of a tiny fix is accepted.

- In a fix round (after a block, or B's small fix after approval) the reviewer's prompt holds only what changed since the reviewed round, plus the open findings, not the whole step's diff
- A full round still gets the whole step's diff as today
- A review of a 2-line fix that comes back in about 10 seconds is accepted; a full round still needs the configured minimum time
- README, the adversarial-loop SKILL.md and schemas.md describe what a fix reviewer sees and the shorter minimum

#### Scenario: Other-provider reviewer after a block sees only the fixer's change
- **GIVEN** A normal-risk step with src/alpha.js, src/bravo.js and src/charlie.js (each with its own marker line) whose round 1 was recorded with a blocking finding on src/alpha.js from the other-provider reviewer (own RATCHET_HOME, so the share limit cannot send it to the host); the fixer changed only src/alpha.js (new marker ZQ-NEW-ALPHA) and the code is re-proven
- **WHEN** The next round is opened and the other-provider reviewer's prompt is produced (external_review.py --dry-run, the kept prompt file)
- **THEN** reviewers --json marks the round as a one-reviewer fix round; the prompt shows the ZQ-NEW-ALPHA change and names src/alpha.js as changed by the fix; it contains neither the bravo nor the charlie marker nor the test file's content; its header says the diff is what changed since the reviewed round, not 'Changes in this checkpoint' from the step's start
<!-- proven by test CP6-T01 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: That reviewer is also told which findings the fix was meant to fix
- **GIVEN** The same step with two blocking findings in round 1, both from the other-provider reviewer that will review the fix (a major on src/bravo.js with description text DESC-B and suggested fix FIX-B, and another on src/alpha.js), one of them triaged as a rebuttal with a reason; the fixer ran
- **WHEN** The fix round's prompt is produced
- **THEN** The prompt lists every open blocking finding of the replaced round with its reviewer, id, severity, file, line, description and suggested fix (DESC-B and FIX-B appear) and the rebuttal reason for the rebutted one; it lists no finding from any older round
<!-- proven by test CP6-T02 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A full round still gets the whole step's diff
- **GIVEN** The same step at round 1 (no earlier round)
- **WHEN** The other-provider reviewer's prompt is produced
- **THEN** The prompt holds the diff of all three source files and the test file under the 'Changes in this checkpoint' header, with no fix-only header and no findings-to-fix section, exactly as today
<!-- proven by test CP6-T03 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A small fix after approval is shown to the reviewer as just those lines
- **GIVEN** The same step whose round 1 was approved clean, then a 2-line edit to src/bravo.js (marker ZQ-TWO-LINE)
- **WHEN** The next round is opened and the other-provider reviewer's prompt is produced
- **THEN** reviewers --json says small_fix is true with one reviewer; the prompt shows only the ZQ-TWO-LINE change measured from the approved tree and not the alpha or charlie markers; it says there are no open findings to fix and lists none
<!-- proven by test CP6-T04 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: An over-limit fix after approval keeps the full round and the full diff
- **GIVEN** The same approved step, then a fix of 25 added lines to src/bravo.js (over config small_edit_lines, default 20)
- **WHEN** The next round is opened and its prompt produced
- **THEN** reviewers --json has small_fix false and the step's whole reviewer set, and the prompt holds all three files' diffs as today (files changed since the last round first)
<!-- proven by test CP6-T05 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A high-risk step's fix after approval keeps the full round and the full diff
- **GIVEN** A high-risk step (risk_reason given) approved clean, then a 2-line edit
- **WHEN** The next round is opened and its prompt produced
- **THEN** The round is the step's full reviewer set, not a fix round, and the prompt holds the whole step's diff
<!-- proven by test CP6-T06 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A second fix round shows only the second fix
- **GIVEN** The step above where fix A (marker ZQ-FIX-A in alpha.js) was reviewed in round 2, which blocked again on a new finding, and the fixer then made fix B (marker ZQ-FIX-B in bravo.js)
- **WHEN** Round 3 is opened and the other-provider reviewer's prompt is produced
- **THEN** The prompt shows ZQ-FIX-B and round 2's open findings; it does not show ZQ-FIX-A or the round 1 findings; the round still counts as round 3 toward the step and run round limits
<!-- proven by test CP6-T07 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Edits made before anyone reviewed the round are all shown
- **GIVEN** The step above where a fix round was opened and no review was written for it, then the code changed again (marker ZQ-LATE in charlie.js on top of the fixer's alpha.js change)
- **WHEN** reviewers is run again and the prompt produced
- **THEN** The replaced round costs nothing (existing rule); the prompt's diff is measured from the last tree a reviewer actually saw, so it shows both the alpha.js fix and ZQ-LATE and still omits bravo.js
<!-- proven by test CP6-T08 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A fix that adds a new file or deletes one is shown correctly
- **GIVEN** The step above where the fixer created src/delta.js (not yet committed, marker ZQ-NEWFILE) and deleted src/charlie.js
- **WHEN** The fix round's prompt is produced
- **THEN** The prompt shows src/delta.js as a new file with its content and src/charlie.js as removed, and omits bravo.js's unchanged content
<!-- proven by test CP6-T09 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A finding the user accepted as a known limit is not listed as one to fix
- **GIVEN** The step above with two blocking findings in round 1; the user accepted one as a known limit and the fixer fixed the other
- **WHEN** The fix round's prompt is produced
- **THEN** Only the finding that was fixed is listed under the findings to fix; the accepted one is absent
<!-- proven by test CP6-T10 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Host sub-agent reviewer after a block is told to look only at the fix
- **GIVEN** The step above with host reviewers only (no other providers) and round 1 recorded with a blocking finding from one host reviewer (its lens, e.g. correctness); the fixer changed only src/alpha.js
- **WHEN** reviewers --json is read for the fix round
- **THEN** The one host reviewer's instruction ('how' plus review_rules) names the previously reviewed tree and the round's fingerprint (at least their first 10 characters), names src/alpha.js as the file the fix changed, lists the open finding ids and descriptions, and does not name the step's starting tree; small_fix is true
<!-- proven by test CP6-T11 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Host sub-agent reviewer after a small fix is told the same
- **GIVEN** An approved normal-risk step with host reviewers only, then a 2-line edit to src/bravo.js
- **WHEN** reviewers --json is read
- **THEN** The instruction names the approved tree, the current fingerprint and src/bravo.js as the changed file, says there are no open findings, and does not name the step's starting tree
<!-- proven by test CP6-T12 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Host sub-agent reviewer in a full round gets no fix-only instruction
- **GIVEN** A normal-risk step with host reviewers only in round 1, and separately an approved step with an over-limit fix after approval
- **WHEN** reviewers --json is read for each
- **THEN** Neither round's instruction names a previous-tree range or carries a findings-to-fix list; the review rules are the same as today's
<!-- proven by test CP6-T13 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A 2-line fix review that comes back in 10 seconds is accepted
- **GIVEN** A fix round after a block for a 2-line fix, config min_review_seconds 20; the host reviewer's clean review lists the fixed file and its file time is set to 10 seconds after the round opened (no waiting)
- **WHEN** adv-record is run, and again with a review dated 0 seconds after the round opened
- **THEN** The 10-second review is accepted; the 0-second one is refused as too fast (the shorter minimum has a fixed floor above zero)
<!-- proven by test CP6-T14 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A full round still needs the configured minimum time
- **GIVEN** A first round with config min_review_seconds 20
- **WHEN** adv-record is run with a review dated 10 seconds after the round opened, then with one dated 20 seconds after
- **THEN** The 10-second review is refused as too fast; the 20-second one is accepted
<!-- proven by test CP6-T15 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A bigger fix needs more review time than a tiny one
- **GIVEN** A fix round after a block where the fixer changed about 400 lines, config min_review_seconds 20
- **WHEN** adv-record is run with a review dated 10 seconds after the round opened, then one dated 20 seconds after
- **THEN** The 10-second review is refused (the minimum scales up with the diff); the 20-second one is accepted
<!-- proven by test CP6-T16 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: The shorter minimum never exceeds the configured one
- **GIVEN** A fix round for a 2-line fix with min_review_seconds 5 in one project and 0 in another
- **WHEN** adv-record is run with a review dated 5 seconds after the round opened (first project) and 0 seconds (second)
- **THEN** Both reviews are accepted: a fix round's minimum is never higher than min_review_seconds
<!-- proven by test CP6-T17 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Only a real small fix after approval gets the shorter minimum
- **GIVEN** An approved step with min_review_seconds 20 and a review dated 10 seconds after the round opened for each of: (a) a 2-line fix after approval, (b) an over-limit fix after approval, (c) a 2-line fix on a high-risk step
- **WHEN** adv-record is run for each
- **THEN** (a) is accepted; (b) and (c) are refused as too fast because they are full rounds
<!-- proven by test CP6-T18 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: A small fix after approval that cannot get one reviewer keeps the full diff and minimum
- **GIVEN** An approved normal-risk step, then a 2-line edit, with config review_depth.low customised (two slots) so B's resolver cannot give one both-lens reviewer and the round is not labelled a small fix. (A fix round after a block is not affected by review_depth.low: it goes to the reviewer whose finding is being fixed, so T01 and T11 stay fix rounds under this same config.)
- **WHEN** The round is opened, its prompt produced and a review dated 10 seconds after opening recorded
- **THEN** small_fix is false, the prompt holds the whole step's diff, and the 10-second review is refused with min_review_seconds 20
<!-- proven by test CP6-T19 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Approving a fix round still has to list changed files
- **GIVEN** A fix round (after a block) with one clean review that has no files_reviewed list and another that lists only the file the fix changed
- **WHEN** adv-record is run on each
- **THEN** The first is refused for not listing the changed files; the second is accepted (it need not list every file of the step)
<!-- proven by test CP6-T20 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Other-provider fix review end to end is accepted at 10 seconds
- **GIVEN** A fix round after a block, with the fake other-provider CLI as the reviewer whose finding is being fixed (own RATCHET_HOME, asserted to be the reviewer that runs); external_review.py ran for real and wrote its signed review; the file's time is then set to 10 seconds after the round opened; min_review_seconds 20
- **WHEN** adv-record is run
- **THEN** The round is recorded; the review is signed and stamped with the round's fingerprint and the files_reviewed check passes
<!-- proven by test CP6-T21 in tests/test_fix_round_diff.py, commit c9e278e -->

#### Scenario: Docs describe what a fix reviewer sees and the shorter minimum
- **GIVEN** The repository docs after the change
- **WHEN** README.md, skills/ratchet-adversarial-loop/SKILL.md, skills/ratchet-orchestrator/references/schemas.md and agents/ratchet-adversary.md are read
- **THEN** Each says a fix round's reviewer sees only the fixer's diff plus the findings it was meant to fix; the first three also say the shorter, diff-scaled minimum applies only to fix rounds and a full round keeps min_review_seconds (default 20); the adversarial-loop SKILL's 'full diff' and 'don't give earlier rounds' findings' lines now except fix rounds
<!-- proven by test CP6-T22 in tests/test_fix_round_diff.py, commit c9e278e -->

### Requirement: Docs and messages match what was built
The system SHALL do this: The skill docs, the timeout message and the code comments now say what Ratchet actually does, so nobody is misled by stale text.

- The adversarial-loop SKILL.md, the orchestrator SKILL.md and agents/ratchet-adversary.md all say a review that leaves files out of a fix round's diff is refused; none says the 'too large' case never applies to a fix round
- A command stopped by its time limit, whose files also changed, is reported as timed out (with the TIMEOUT marker), not as 'ended on its own'; pass and fail counts are unchanged
- The _Watch and _run_cmd docstrings describe only rules the code still has
- The case list at the top of tests/test_stop_on_change_fixes.py runs through [CP4-A7], and the one in tests/test_load_timeouts_fixes.py lists X01 to X05

#### Scenario: The adversarial-loop SKILL.md says a review that leaves files out of a fix round's diff is refused
- **GIVEN** skills/ratchet-adversarial-loop/SKILL.md
- **WHEN** Its text is read
- **THEN** It states that a review which leaves files out of the fix round's diff is refused (never counts), and it no longer says the 'too large' fallback never applies to a scoped round
<!-- proven by test CP7-T01 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: The orchestrator SKILL.md says the same
- **GIVEN** skills/ratchet-orchestrator/SKILL.md
- **WHEN** Its text is read
- **THEN** It describes the omitted-files refusal for a fix round and says nowhere that the 'too large' case never applies to a scoped round
<!-- proven by test CP7-T02 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: agents/ratchet-adversary.md says the same
- **GIVEN** agents/ratchet-adversary.md
- **WHEN** Its text is read
- **THEN** It describes the omitted-files refusal and makes no 'never applies' claim about the too-large fallback
<!-- proven by test CP7-T03 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: No sentence in any of the three says the too-large fallback never applies to a scoped round
- **GIVEN** The three documents
- **WHEN** Each is searched for a sentence pairing 'too large' with 'never' and a scoped or fix round
- **THEN** None is found; the failure names the file that still has one
<!-- proven by test CP7-T04 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: A command killed by its time limit whose files also changed is reported as timed out
- **GIVEN** The hanging-command project: the command writes a tracked file, then hangs past test_timeout_sec (2s), idle fake load
- **WHEN** ratchet.py behavior CP1 is run for real
- **THEN** The output and the kept log carry 'TIMEOUT after'; neither contains 'ended on its own' or 'found when it ended'; the log has no '# code changed under the run' header
<!-- proven by test CP7-T05 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: The timed-out run's attempt accounting is unchanged
- **GIVEN** The same run as CP7-T05, status --json read before and after (and compared with a plain timeout of an unchanged tree)
- **WHEN** The behavior call ends
- **THEN** gates.behavior.attempts, flaky_used and runs move exactly as for a plain timeout with no file change, and no stopped-runs.json entry is written for the command
<!-- proven by test CP7-T06 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: Contrast: a command that ends on its own after changing a tracked file is still the uncounted refusal
- **GIVEN** The twin project whose command writes the tracked file and exits 0 on its own
- **WHEN** ratchet.py behavior CP1 is run
- **THEN** The log still says 'found when it ended', there is no TIMEOUT marker, and attempts did not change
<!-- proven by test CP7-T07 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: A timeout with no file change is reported as before
- **GIVEN** A hanging command that writes nothing, same short limit
- **WHEN** behavior is run
- **THEN** TIMEOUT marker present, no 'code changed' wording, same accounting as CP7-T06
<!-- proven by test CP7-T08 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: The _Watch docstring names no removed rule
- **GIVEN** ratchet._Watch.__doc__
- **WHEN** Read through the imported module
- **THEN** The removed rule is gone (the substring 'source extension outside every build' does not appear) and the current rule is named: any added file git would commit counts as new source unless a fixed generated or doc list names it, and report folders are not generated. Also named: periodic look in one kept index, a skipped look noted in `skipped`, the last look as the full fingerprint, `found_at_end`, and that only edited or deleted started files plus new project source count
<!-- proven by test CP7-T09 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: The _run_cmd docstring names no removed rule
- **GIVEN** ratchet._run_cmd.__doc__
- **WHEN** Read through the imported module
- **THEN** The removed phrase is gone (the substring 'delete or ignore it' does not appear) and the current rule is named: every named file is restored from the run's starting tree and a file the run started with is never deleted. Also named: stretched limit (highest reading wins, never below test_timeout_sec, capped by load_stretch_max), `TIMEOUT after <limit>s` exit 124, the stop on a code change with no attempt or flaky change, and that a kill by the time limit is reported as a timeout even when the tree also changed
<!-- proven by test CP7-T10 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: Every marker or setting the two docstrings name still exists in the code
- **GIVEN** The two docstrings
- **WHEN** Each named identifier or log marker (stopped-runs.json, 'stopped: code changed', found_at_end, RATCHET_TEST_REPORT, load_stretch_max, suite_watch_seconds, 'TIMEOUT after') is looked up in the module source outside the docstrings
- **THEN** Each one is found
<!-- proven by test CP7-T11 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: The case list in tests/test_stop_on_change_fixes.py covers every CP4 id the file runs
- **GIVEN** The file's text
- **WHEN** The ids in its opening docstring are compared with every [CP4-...] id used in the body (both derived from the file)
- **THEN** Every id the body runs is listed in the docstring, [CP4-A7] included; the failure names the missing ids
<!-- proven by test CP7-T12 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: The case list in tests/test_load_timeouts_fixes.py covers every CP2 id the file runs
- **GIVEN** The file's text
- **WHEN** The same comparison is made for [CP2-...] ids
- **THEN** Every id the body runs is listed in the docstring, X01 to X05 included
<!-- proven by test CP7-T13 in tests/test_docs_accuracy.py, commit 9b1e604 -->

#### Scenario: The index check can fail
- **GIVEN** A synthetic file text whose docstring lists A1 to A3 while its body runs [CP4-A4]
- **WHEN** The same comparison is applied
- **THEN** It reports A4 as missing, so the check is not vacuous
<!-- proven by test CP7-T14 in tests/test_docs_accuracy.py, commit 9b1e604 -->
