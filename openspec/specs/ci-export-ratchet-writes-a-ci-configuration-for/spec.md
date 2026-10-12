# CI export: Ratchet writes a CI configuration for the project so the checks Ratchet relied on keep running on every push and pull request after the run is over: the project's tests and regression command, and the automatic checks saved from guardrail steps (config checks). Start with GitHub Actions.

## Purpose
What this feature does, as built and checked by Ratchet (run ci-export-ratchet-writes-a-ci-configuration-for, 2026-10-11). Each scenario names the test that proves it.

## Requirements

### Requirement: Print the CI workflow Ratchet would write
The system SHALL do this: A developer can run `ratchet.py ci-export --stdout` and see a GitHub Actions workflow that runs their regression command and saved checks.

- Running `ratchet.py ci-export --stdout` on a project with a regression command and saved checks prints a workflow that runs each of them, in a fixed order, on push and pull request
- The printed file opens with a comment saying the AI review gates are left out and why
- Running it twice prints exactly the same text
- A project with nothing to run gets a plain message and no workflow
- A command with quotes, $ signs or several lines appears in the workflow exactly as written

#### Scenario: Workflow runs the regression command first, then each saved check, in order
- **GIVEN** project A: regression_command set, and config checks has two entries saved in order [check-1, check-2]
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the printed workflow has a job that runs the regression command before check-1's command, and check-1's command before check-2's command, triggered on push and pull_request
<!-- proven by test CP1-T01 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: Printing the workflow twice gives exactly the same text
- **GIVEN** project A, unchanged between the two calls
- **WHEN** running `ratchet.py ci-export --stdout` twice in a row
- **THEN** both runs print byte-for-byte identical text (no timestamp, run id or other value that changes between calls)
<!-- proven by test CP1-T02 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: The workflow opens with a comment explaining the AI review gates are left out
- **GIVEN** project A
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the printed text's header comment names the AI review gates (adversarial, UI and human review) and says plainly why they are not in CI (CI machines don't have the agent CLIs and credentials those gates need)
<!-- proven by test CP1-T03 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: A project with nothing to run is refused plainly
- **GIVEN** project C: no regression_command, no checks, no completed step with a tests.command anywhere
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the command exits non-zero, prints a plain message that there is nothing to run, and prints no workflow YAML
<!-- proven by test CP1-T04 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: A command with quotes, a $ sign and several lines survives exactly
- **GIVEN** project E: regression_command is a multi-line script holding a single-quoted string, a double-quoted string and a literal $VAR
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the exact original text of regression_command (every quote, the $ sign and every line) appears unchanged in the printed workflow's run step, not re-escaped or collapsed to one line
<!-- proven by test CP1-T05 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: Completed steps' own test commands are left out once a regression command exists
- **GIVEN** project D with regression_command also set (in addition to its one check and its two completed prior-run steps with their own tests.command)
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the workflow contains the regression command and the one check's command, but neither prior-run checkpoint's tests.command appears anywhere in the output
<!-- proven by test CP1-T06 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: With no regression command, completed steps' test commands run after the checks, in run-then-step order
- **GIVEN** project D with no regression_command: one check plus two completed checkpoints in two different prior runs, each with its own tests.command
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the workflow has no regression-command step, includes the check's command, and then both prior checkpoints' tests.command values, in the same order as their runs and checkpoint ids were saved
<!-- proven by test CP1-T07 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: An exact duplicate command is only written once
- **GIVEN** project F: two checks whose command text is byte-identical, and separately a check whose command equals regression_command exactly
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** that duplicated command text appears as exactly one step in the printed workflow, not twice
<!-- proven by test CP1-T08 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: A command that looks like it needs a running app gets a comment, not a guess
- **GIVEN** project G: config app.start_command (or app.url) is set, alongside an ordinary regression_command
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the workflow still includes the regression command's step as written (CI never starts the app or guesses how), and that step carries a comment noting it may need a running app/service that CI does not start
<!-- proven by test CP1-T09 in tests/test_ci_export.py, commit 4d667c2 -->

#### Scenario: The setup step matches the project's own language, Python or Node
- **GIVEN** project H (pyproject.toml present) and, separately, project I (package.json present), each with regression_command set
- **WHEN** running `ratchet.py ci-export --stdout` on each project
- **THEN** project H's workflow sets up Python (actions/setup-python) and project I's workflow sets up Node (actions/setup-node) and installs with npm ci; neither workflow sets up the other language
<!-- proven by test CP1-T10 in tests/test_ci_export.py, commit 4d667c2 -->

### Requirement: Write the workflow file without clobbering
The system SHALL do this: A developer can run `ratchet.py ci-export` and get .github/workflows/ratchet.yml written or updated, while a hand-written file at that path is left alone.

- Running `ratchet.py ci-export` with no workflow file creates .github/workflows/ratchet.yml with the same text --stdout prints
- Running it again after a check is added updates the Ratchet-written file in place
- If a file a person wrote is already at that path, nothing changes and Ratchet says it won't replace it and suggests --stdout
- Running it when nothing changed leaves the file byte-for-byte the same and says it is already up to date

#### Scenario: First run creates the workflow file, matching --stdout exactly
- **GIVEN** a fresh project with a regression_command set and no .github directory at all
- **WHEN** ratchet.py ci-export is run
- **THEN** .github/workflows/ratchet.yml exists, its bytes are byte-for-byte equal to what `ratchet.py ci-export --stdout` prints for the same project, both .github and .github/workflows were created, and the command exits 0
<!-- proven by test CP2-T01 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: Rerun after a check is added updates the Ratchet-written file in place
- **GIVEN** a project where ci-export already wrote .github/workflows/ratchet.yml once, then a new check command is appended to .ratchet/config.json's checks list
- **WHEN** ratchet.py ci-export is run again
- **THEN** the same file at the same path is overwritten with the new text (which includes a step for the added check) matching the new --stdout output exactly, no second file or backup is left behind, and the command exits 0
<!-- proven by test CP2-T02 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: A hand-written workflow at the path is never replaced
- **GIVEN** a workflow file at .github/workflows/ratchet.yml written by a person (ordinary YAML, no Ratchet marker comment on its first line)
- **WHEN** ratchet.py ci-export is run
- **THEN** the file's bytes are unchanged (compare before/after), the command exits non-zero, and its message plainly says it won't replace a hand-written file at that path and suggests --stdout
<!-- proven by test CP2-T03 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: A directory at the path is refused, not overwritten
- **GIVEN** a directory (not a file) already exists at .github/workflows/ratchet.yml
- **WHEN** ratchet.py ci-export is run
- **THEN** the directory and its contents are unchanged, no file is written over it, and the command exits non-zero with a plain message (no traceback)
<!-- proven by test CP2-T04 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: A symlink at the path is refused, not followed
- **GIVEN** .github/workflows/ratchet.yml is a symlink pointing at another file outside .github/
- **WHEN** ratchet.py ci-export is run
- **THEN** neither the symlink nor its target file is modified, nothing is written through the symlink, and the command exits non-zero with a plain message
<!-- proven by test CP2-T05 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: An unchanged rerun leaves the file byte-identical and says so
- **GIVEN** a project where ci-export already wrote .github/workflows/ratchet.yml and nothing about the regression command, checks or completed step tests has changed since
- **WHEN** ratchet.py ci-export is run again
- **THEN** the file's bytes and modification time are exactly the same as before the rerun, the command exits 0, and its message says the workflow is already up to date
<!-- proven by test CP2-T06 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: Nothing to run writes no file
- **GIVEN** a project with no regression_command, no checks, and no completed checkpoint whose tests.command could be used
- **WHEN** ratchet.py ci-export is run
- **THEN** no .github/workflows/ratchet.yml is created, no .github directory is created either, and the command exits non-zero with a plain message
<!-- proven by test CP2-T07 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: ci-export never stages or commits anything in git
- **GIVEN** a git-initialized project where ci-export is about to create .github/workflows/ratchet.yml for the first time
- **WHEN** ratchet.py ci-export is run
- **THEN** git status afterward shows the new file as untracked (not staged as added), and HEAD and the index are exactly what they were before the run, proving ci-export never ran git add or git commit
<!-- proven by test CP2-T08 in tests/test_ci_export_write.py, commit f901d52 -->

#### Scenario: .github already existing as a plain file is refused gracefully
- **GIVEN** a project where .github exists as a plain file, not a directory
- **WHEN** ratchet.py ci-export is run
- **THEN** the command exits non-zero with a plain message and no Python traceback, the .github file is left byte-for-byte unchanged, and no workflow is written
<!-- proven by test CP2-T09 in tests/test_ci_export_write.py, commit f901d52 -->

### Requirement: Suggest the export at run end; document it
The system SHALL do this: At the end of a run the agent gets one line saying it can export CI and mentions it to the developer in plain words; the README tells developers to ask their agent for CI, and the skill and schemas tell agents how.

- When the last step of a run is approved, Ratchet's output gives the agent one line saying it can export CI with `ci-export`, and asks no new question
- The line is not shown when .github/workflows/ratchet.yml is already up to date
- The README has a short CI export section, written for developers, that says to ask their agent for CI, what runs in CI, that the AI review gates do not and why, and that a hand-written workflow is never replaced; it gives the developer no ratchet.py command to type
- The orchestrator skill tells the agent to mention CI export to the developer in plain words at the end of a run and to run `ci-export` when the developer asks for CI; the skill and schemas.md document the command and its --stdout option for agents
- The generated workflow's header says to regenerate it by asking the agent to run Ratchet's CI export, not by typing a command

#### Scenario: Final human approval suggests the ci-export command, in one line
- **GIVEN** a one-checkpoint run with regression_command set, human_review=end, no .github/workflows/ratchet.yml anywhere in the project, driven through tests-written/red/implemented/behavior/adversarial approval and `complete` for its only checkpoint
- **WHEN** the final `ratchet.py human-approve` is run (the run has no more checkpoints, so this call finishes it)
- **THEN** that command's own stdout contains exactly one line naming `ratchet.py ci-export`
<!-- proven by test CP3-T01 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: With human_review=none the hint shows on the completing call itself, not on a later human-approve
- **GIVEN** the same one-checkpoint run but with config human_review=none, so finishing the run never calls human-approve at all
- **WHEN** `ratchet.py complete CP1` is run for the run's only (and last) checkpoint
- **THEN** that `complete` call's own stdout contains the ci-export suggestion line, since it alone takes the run straight to its finished phase
<!-- proven by test CP3-T02 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: The hint is absent once the workflow file is already up to date
- **GIVEN** a one-checkpoint run identical to CP3-T01, except `ratchet.py ci-export` was already run once before the final review so .github/workflows/ratchet.yml already holds the same text ci-export would write, and nothing about regression_command or checks changes afterward
- **WHEN** the final `ratchet.py human-approve` is run
- **THEN** that command's stdout does not mention ci-export or suggest running it
<!-- proven by test CP3-T03 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: The hint asks nothing new; nothing is left waiting on a reply
- **GIVEN** the same run as CP3-T01, right after the final `human-approve` call that printed the hint
- **WHEN** reading state.json's `pending` field and running `ratchet.py status --json` immediately after
- **THEN** `pending` is empty (no question about ci-export was opened), and status's reported phase/next action needs no reply about ci-export -- a developer can ignore the hint with no consequence
<!-- proven by test CP3-T04 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: The hint is not repeated on a later status read
- **GIVEN** the same run as CP3-T01, already finished with the hint already printed once by the final human-approve call
- **WHEN** `ratchet.py status --json` (or `ratchet.py next`) is run afterward, with nothing else having changed
- **THEN** its output does not contain the ci-export suggestion line again -- the hint belongs to the one command that ended the run, not to every later read of run state
<!-- proven by test CP3-T05 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: README's CI export section says what actually runs in CI
- **GIVEN** README.md as shipped, sliced from its CI-export heading (matched case-insensitively on 'ci export' / 'ci-export') to the next heading of equal or higher level
- **WHEN** that section's text is checked
- **THEN** it tells developers to ask their agent for CI, and states that the generated workflow runs the project's regression/test command and the automatic checks saved from guardrail steps, on push and on pull requests; the section gives the developer no ratchet.py command line to type
<!-- proven by test CP3-T06 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: README's CI export section explains the AI gates are left out, why, and that hand-written files are protected
- **GIVEN** the same README CI-export section as CP3-T06
- **WHEN** that section's text is checked
- **THEN** it names the AI review gates (adversarial review, UI review, human review) and says plainly they do not run in CI because CI machines lack the agent CLIs and credentials those gates need, and separately says a hand-written workflow already at that path is left alone (never replaced) and that the agent can show the file instead
<!-- proven by test CP3-T07 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: The orchestrator skill and schemas.md mention ci-export and --stdout
- **GIVEN** skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md as shipped
- **WHEN** each file's text is checked
- **THEN** both files mention the `ci-export` command, and both mention its `--stdout` option, and the skill tells the agent to mention CI export to the developer in plain words at the end of a run
<!-- proven by test CP3-T08 in tests/test_ci_export_docs.py, commit 16d2f98 -->

#### Scenario: The generated workflow's header says to regenerate it by asking the agent
- **GIVEN** a temp project with a regression command
- **WHEN** ci-export --stdout runs
- **THEN** the header comment says to regenerate the file by asking the agent to run Ratchet's CI export, and does not tell a person to type a ratchet.py command
<!-- proven by test CP3-T09 in tests/test_ci_export_docs.py, commit 16d2f98 -->

### Requirement: Skip past runs with damaged records
The system SHALL do this: When a past run's saved records have the wrong shape, CI export skips that run with a plain note and still writes the workflow from the good runs, instead of crashing.

- If a past run's state.json has a "checkpoints" value that is not an object (a list, a string, a number), ci-export skips that run, prints a plain one-line note on stderr naming the file, and exits 0
- The same holds when one checkpoint entry in state.json, a checkpoints.json "checkpoints" value, one of its items, or an item's "tests" has the wrong shape: that run (or that step) is skipped with a note, never a traceback
- Good runs next to the damaged one still have their step tests in the exported workflow, in the same order as before
- Exports from well-formed runs give exactly the same file as before this change

#### Scenario: A run whose state.json 'checkpoints' is a list is skipped, not crashed
- **GIVEN** project A: 'bad-run' state.json has a "checkpoints" list where ratchet.py plan-submit would normally write a checkpoint-id-keyed object; 'good-run' sits alongside it, unmodified
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the command exits 0, stdout/stderr contain no Python traceback, 'good-run''s own step command still appears in the printed workflow, and stderr carries a one-line plain note naming 'bad-run' (its state.json file) as skipped
<!-- proven by test CP4-T01 in tests/test_ci_export_shapes.py, commit af2eb96 -->

#### Scenario: One malformed checkpoint entry in state.json is skipped without losing its run's other, well-formed checkpoint
- **GIVEN** project B: 'bad-run' is otherwise well-shaped, but one of its two checkpoint entries under state.json's "checkpoints" object is a bare string instead of a {"status": ...} dict; the other checkpoint entry in the same run is a normal done dict with its own usable tests.command; 'good-run' sits alongside it, unmodified
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the command exits 0 with no traceback, 'bad-run''s well-formed checkpoint's tests.command still appears in the printed workflow (only the malformed entry's checkpoint is left out), 'good-run''s step command still appears, and stderr carries a one-line plain note naming the skipped checkpoint or its run
<!-- proven by test CP4-T02 in tests/test_ci_export_shapes.py, commit af2eb96 -->

#### Scenario: A run whose checkpoints.json 'checkpoints' is not a list is skipped, not crashed
- **GIVEN** project C: 'bad-run' checkpoints.json has a "checkpoints" object ({"id": "CP1"}) where ratchet.py plan-submit would normally write a list; 'good-run' sits alongside it, unmodified
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the command exits 0, stdout/stderr contain no Python traceback, 'good-run''s own step command still appears in the printed workflow, and stderr carries a one-line plain note naming 'bad-run' (its checkpoints.json file) as skipped
<!-- proven by test CP4-T03 in tests/test_ci_export_shapes.py, commit af2eb96 -->

#### Scenario: A checkpoint item with no usable tests.command (malformed 'tests', or the item itself malformed) is skipped without losing its run's other, well-formed item
- **GIVEN** project D (one item's "tests" value is a JSON list instead of an object holding "command") and, separately, project E (one item in the "checkpoints" list is a bare string instead of a dict at all); in both, the run's other checkpoint item is well-formed with a usable tests.command, and 'good-run' sits alongside it, unmodified
- **WHEN** running `ratchet.py ci-export --stdout` on project D and then on project E
- **THEN** on each project the command exits 0 with no traceback, the run's other, well-formed item's tests.command still appears in the printed workflow, 'good-run''s step command still appears, and stderr carries a one-line plain note naming the skipped item or its file
<!-- proven by test CP4-T04 in tests/test_ci_export_shapes.py, commit af2eb96 -->

#### Scenario: Well-formed runs export exactly the same workflow as before this change
- **GIVEN** project F: only well-shaped config and prior runs, the same fixtures CP1-T06 and CP1-T07 already use, with no malformed shape anywhere
- **WHEN** running `ratchet.py ci-export --stdout`
- **THEN** the printed workflow text is identical to what CP1's existing checks require (same step commands, same relative order, same header), stderr carries no skip note at all, and the command exits 0
<!-- proven by test CP4-T05 in tests/test_ci_export_shapes.py, commit af2eb96 -->
