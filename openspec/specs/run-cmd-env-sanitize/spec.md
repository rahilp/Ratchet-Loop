# Sanitize run-context env vars (RATCHET_ROOT etc) out of _run_cmd's child env so test/regression subprocesses don't inherit the current run's context and get redirected at the wrong worktree

## Purpose
What this feature does, as built and checked by Ratchet (run run-cmd-env-sanitize, 2026-10-25). Each scenario names the test that proves it.

## Requirements

### Requirement: Stop test runs leaking run context to children
The system SHALL do this: Test and regression commands that Ratchet launches through _run_cmd now run in a clean child environment, so env-sensitive suites exercise their own temp project instead of being redirected at the current run's real worktree by a leaked RATCHET_ROOT (or sibling var).

- A grep of every os.environ RATCHET_* read in ratchet.py names the exact set of current-run-pinning vars _run_cmd must strip (RATCHET_ROOT included; RATCHET_RUN_ALL/RATCHET_RUN_ALL_SUITES/RATCHET_RUN_ALL_TAG excluded), recorded as a named module-level constant with a short comment. Confirmed set: RATCHET_ROOT, RATCHET_ROLE, RATCHET_HOME, RATCHET_HUMAN_OVERRIDE, RATCHET_DIRECTOR_KEY_FILE (the last four match RESERVED_ENV's existing director/human-session-only names; RATCHET_HOME's inclusion also matches the RATCHET_HOME-guard error the app-scout saw in test_install.py)
- A red test, written before the fix, sets the stripped vars in the parent process env, runs a command through _run_cmd's env-building path, and asserts the child did not see them; it is shown to fail against git HEAD (pre-fix) and pass after the fix
- A guard test proves vars that should still pass through (CI, FORCE_COLOR, PATH, HOME, and the RATCHET_RUN_ALL* set) still reach the child, unchanged
- _run_cmd pops exactly the named leak-var set from the child env dict; no other behavior of _run_cmd changes

#### Scenario: Current-run context vars set in the parent do not reach the child _run_cmd launches
- **GIVEN** RATCHET_ROOT, RATCHET_ROLE, RATCHET_HOME, RATCHET_HUMAN_OVERRIDE and RATCHET_DIRECTOR_KEY_FILE are all set to distinctive sentinel values on the test process's own environment
- **WHEN** a command is run through _run_cmd
- **THEN** the child subprocess's own os.environ (read back from the file it wrote) contains none of those five keys
<!-- proven by test CP1-T01 in tests/test_run_cmd_env.py, commit d4ed21d -->

#### Scenario: Vars a child legitimately needs still reach it
- **GIVEN** RATCHET_RUN_ALL, RATCHET_RUN_ALL_SUITES and RATCHET_RUN_ALL_TAG are set to distinctive sentinel values on the test process's own environment
- **WHEN** a command is run through _run_cmd
- **THEN** the child's os.environ has those three vars unchanged, PATH and HOME equal to the parent's own values, CI="1" and FORCE_COLOR="0" (as _run_cmd always sets)
<!-- proven by test CP1-T02 in tests/test_run_cmd_env.py, commit d4ed21d -->
