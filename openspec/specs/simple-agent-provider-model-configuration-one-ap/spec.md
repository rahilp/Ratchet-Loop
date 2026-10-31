# Simple agent provider/model configuration: one approachable way to see and change which provider and model Ratchet uses for each agent role, with a global machine-wide config and a per-project config.

## Purpose
What this feature does, as built and checked by Ratchet (run simple-agent-provider-model-configuration-one-ap, 2026-10-31). Each scenario names the test that proves it.

## Requirements

### Requirement: Global config file under the project's
The system SHALL do this: Ratchet reads a machine-wide config file and lays the project's own config over it, key by key, without changing anything for users who have no global file.

- With no global file, every command shows exactly the same settings as before
- A model set in the global file is used by every project that does not set it, and a project's own value wins over it
- A damaged global file, or one trying to set paid consent or a project-only setting, stops the command naming the file and key; a locked setting changed there during an approved run asks for approval like a project change
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: No global file: settings are exactly as before
- **GIVEN** a throwaway project with a project config (reviewers 2, role_models {implementer: sonnet}) and an empty private per-user folder
- **WHEN** the effective config is loaded, and again with no project config at all
- **THEN** it equals the old defaults-plus-project result key for key in both cases, and no global file is created
<!-- proven by test CP1-T01 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: A global model applies to a project that does not set it
- **GIVEN** global file sets role_models {implementer: opus-x}; the project config does not mention role_models
- **WHEN** the effective config is loaded
- **THEN** role_models.implementer is opus-x and every other default role keeps its default
<!-- proven by test CP1-T02 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: A project's own value beats the global value
- **GIVEN** global sets role_models {implementer: opus-x, reviewer: haiku-y}; project sets {implementer: sonnet-z}
- **WHEN** the effective config is loaded
- **THEN** implementer is sonnet-z, reviewer is still haiku-y (dict keys merge one level deep) and defaults fill the rest
<!-- proven by test CP1-T03 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: Non-dict project values replace the global value whole
- **GIVEN** global sets reviewers 3 and a list-valued key; project sets reviewers 1 and an empty list for that key
- **WHEN** the effective config is loaded
- **THEN** the project's scalar and list replace the global ones; nothing is appended or averaged
<!-- proven by test CP1-T04 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: The old default review depth is dropped per level
- **GIVEN** three cases: global holds the old default review_depth with project silent; project holds the old default with global holding a real depth; both hold different real depths
- **WHEN** the effective config is loaded each time
- **THEN** an untouched old default never overrides the new default or a real value from another level, and a real project depth wins over a real global depth
<!-- proven by test CP1-T05 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: A damaged global file stops the command naming the file
- **GIVEN** the global config.json contains broken JSON
- **WHEN** a normal command (status) runs
- **THEN** it stops with a message naming the global file path (not the project file) and does not continue with defaults
<!-- proven by test CP1-T06 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: A global file that is not a JSON object is refused
- **GIVEN** the global config.json holds a JSON list
- **WHEN** a normal command runs
- **THEN** it stops naming the global file and saying an object is expected
<!-- proven by test CP1-T07 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: Paid consent in the global file is refused
- **GIVEN** global file sets providers with a paid_ok_at timestamp on one entry
- **WHEN** a normal command runs
- **THEN** it stops naming the global file and the key paid_ok_at; no paid consent is ever treated as granted from it; the same field in the project file behaves as it does today
<!-- proven by test CP1-T08 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: Project-only keys in the global file are refused
- **GIVEN** one global file per run setting exactly one of checks, regression_command, related_test_command, doc_test_command, app, lane_start_command
- **WHEN** a normal command runs
- **THEN** every run stops naming the global file and that exact key, never silently dropping it; the same keys in the project file still load
<!-- proven by test CP1-T09 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: Machine-wide settings in the global file still load
- **GIVEN** global file sets role_models, providers without paid_ok_at, reviewers and host_models
- **WHEN** a normal command runs
- **THEN** it loads without error and those values show in the effective config
<!-- proven by test CP1-T10 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: Project-file writers never copy global values into the project file
- **GIVEN** global sets role_models {implementer: opus-x}; a command that rewrites .ratchet/config.json runs (the raw writers that read the project config path)
- **WHEN** the project file is read back from disk
- **THEN** it holds only the project's own keys plus the intended change, with no global values copied in
<!-- proven by test CP1-T11 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: Changing a locked setting in the global file asks for approval
- **GIVEN** an approved run whose snapshot locks the effective config, then the global file changes a locked key such as reviewers
- **WHEN** the next gated command runs
- **THEN** the integrity check reports the locked setting changed and asks for approval like a project-file change; an unchanged global file does not trigger it
<!-- proven by test CP1-T12 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: A global file that sets only unlocked keys does not disturb an approved run
- **GIVEN** an approved run whose snapshot predates the global file, and a global file setting only unlocked keys
- **WHEN** gated commands run
- **THEN** no approval is requested and the run proceeds
<!-- proven by test CP1-T13 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: Only the global file in use affects the result
- **GIVEN** two different private per-user folders, one with a global file and one empty
- **WHEN** the config is loaded under each
- **THEN** only the one in use changes the result and the real home is never read
<!-- proven by test CP1-T14 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: The new test file is in the README test commands
- **GIVEN** the README 'Running the tests' section
- **WHEN** it is scanned
- **THEN** it lists python3 tests/test_config_layers.py
<!-- proven by test CP1-T15 in tests/test_config_layers.py, commit d2e6b50 -->

#### Scenario: The new test file is in the test-inventory fixture when it scans engine source
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md and the new test file's source
- **WHEN** the file is checked for reading ratchet.py source or loading it by spec_from_file_location
- **THEN** if it does, it is listed in the inventory; if it does not, no entry is required
<!-- proven by test CP1-T16 in tests/test_config_layers.py, commit d2e6b50 -->

### Requirement: Read and write each config level
The system SHALL do this: A developer's agent can show the global, project or combined settings, see which level each value came from, set or remove a value at either level, and a malformed set is refused instead of saved.

- Showing settings can pick global, project or combined, and the combined view names where each value came from (built-in, global or project)
- A value can be set or removed at the global or project level, and removing it returns that role to the next level down
- A set command with no '=' is refused and nothing is written (today it writes a junk setting)
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: Showing settings with no flags is unchanged
- **GIVEN** a project with config {"max_rounds": 7} and no global file
- **WHEN** the agent runs `config` with no flags
- **THEN** stdout is byte-identical to json.dumps of the merged settings with indent 2, as today, and the exit code is 0
<!-- proven by test CP2-T01 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Show only the global level
- **GIVEN** global file {"max_rounds": 9} and project file {"max_rounds": 4, "related_tests_max": 3}
- **WHEN** the agent runs `config --scope global`
- **THEN** the output is exactly the global file's content (max_rounds 9) and does not contain related_tests_max or any built-in default key
<!-- proven by test CP2-T02 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Show only the project level
- **GIVEN** same files as above
- **WHEN** the agent runs `config --scope project`
- **THEN** the output is exactly the project file's content (max_rounds 4, related_tests_max 3) and shows no global-only key
<!-- proven by test CP2-T03 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Show the combined view
- **GIVEN** same files as above
- **WHEN** the agent runs `config --scope effective`
- **THEN** the output equals the no-flag output: max_rounds 4 (project beats global) and built-in defaults present for untouched keys
<!-- proven by test CP2-T04 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Combined view names where each value came from
- **GIVEN** global sets related_tests_max=5 and max_rounds=9; project sets max_rounds=4; quota_retest_minutes is only a built-in default
- **WHEN** the agent runs `config --sources`
- **THEN** the output names project for max_rounds, global for related_tests_max and builtin for quota_retest_minutes, one entry per top-level key
<!-- proven by test CP2-T05 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Source of a key that merges across levels
- **GIVEN** global sets provider_share {"claude": 50}, project sets provider_share {"codex": 30}
- **WHEN** the agent runs `config --sources`
- **THEN** provider_share is reported as coming from project (the higher level that touched it) and the effective value keeps both entries
<!-- proven by test CP2-T06 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Set a value at the project level by default
- **GIVEN** empty project file and no global file
- **WHEN** the agent runs `config --set max_rounds=6`
- **THEN** .ratchet/config.json contains max_rounds 6, the global file is not created or changed, and the output says Set max_rounds = 6
<!-- proven by test CP2-T07 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Set a value at the global level
- **GIVEN** empty project file, no global config.json
- **WHEN** the agent runs `config --global --set max_rounds=6`
- **THEN** the global config.json contains max_rounds 6, the project file is unchanged, and a following `config --sources` reports max_rounds from global
<!-- proven by test CP2-T08 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Remove a project value falls back to global
- **GIVEN** global max_rounds=9 and project max_rounds=4
- **WHEN** the agent runs `config --unset max_rounds`
- **THEN** the project file no longer has max_rounds, the global file is untouched, and the combined view now shows 9 sourced from global
<!-- proven by test CP2-T09 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Remove a global value falls back to the built-in default
- **GIVEN** global max_rounds=9 and no project value
- **WHEN** the agent runs `config --global --unset max_rounds`
- **THEN** the global file no longer has max_rounds and the combined view shows the built-in default sourced as builtin
<!-- proven by test CP2-T10 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Remove one sub-key with a dotted path
- **GIVEN** project provider_share {"claude": 50, "codex": 30}
- **WHEN** the agent runs `config --unset provider_share.codex`
- **THEN** the project file keeps provider_share.claude 50 and has no codex entry; other keys are untouched
<!-- proven by test CP2-T11 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Removing a value that is not set is harmless
- **GIVEN** project file without related_tests_max
- **WHEN** the agent runs `config --unset related_tests_max`
- **THEN** exit code 0 with a plain message that nothing was set at that level, and the file content is unchanged
<!-- proven by test CP2-T12 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: A set with no equals sign is refused and writes nothing
- **GIVEN** project file {"max_rounds": 4}
- **WHEN** the agent runs `config --set max_rounds` (no '=') and again with `--global`
- **THEN** both calls exit non-zero with a message saying the argument must be key=value and naming it; the project file bytes are unchanged, the global file is not created, and no key with an empty value exists anywhere
<!-- proven by test CP2-T13 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: One bad item in a multi-set writes nothing
- **GIVEN** project file {"max_rounds": 4}
- **WHEN** the agent runs `config --set max_rounds=8 --set oops`
- **THEN** the call is refused and max_rounds is still 4 in the file (no partial write)
<!-- proven by test CP2-T14 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Existing per-key checks still apply at both levels
- **GIVEN** any project
- **WHEN** the agent runs `config --set quota_retest_minutes=abc`, `--set related_test_command=pytest` (no {files}) and `--global --set suite_watch_seconds=0`
- **THEN** each is refused with its existing message and nothing is written to the project or global file
<!-- proven by test CP2-T15 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: A global set cannot sneak in a project-only or paid setting
- **GIVEN** any project
- **WHEN** the agent runs `config --global --set checks=["x"]` and `config --global --set providers.codex.paid_ok_at="2026-01-01"`
- **THEN** both are refused naming the global file and the key (CP1 rule) and the global file is not created
<!-- proven by test CP2-T16 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Writing one level never copies the other level into it
- **GIVEN** global max_rounds=9 and a project file {"related_tests_max": 3}
- **WHEN** the agent runs `config --set quota_retest_minutes=15`
- **THEN** the project file holds exactly related_tests_max 3 and quota_retest_minutes 15, with no max_rounds and no default keys folded in
<!-- proven by test CP2-T17 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: A damaged global file stops a read naming it
- **GIVEN** global config.json contains `{not json`
- **WHEN** the agent runs `config --scope global` and `config --global --set max_rounds=3`
- **THEN** both exit non-zero with a message naming the global file's path, and the damaged file is not overwritten
<!-- proven by test CP2-T18 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: An unknown scope value is refused
- **GIVEN** any project
- **WHEN** the agent runs `config --scope everywhere`
- **THEN** the command is refused with a message listing global, project and effective
<!-- proven by test CP2-T19 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: Reading never writes
- **GIVEN** a project and no global file
- **WHEN** the agent runs `config`, `config --scope global` and `config --sources`
- **THEN** no global file appears and the project file's bytes are unchanged
<!-- proven by test CP2-T20 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: The new test file is listed in the README
- **GIVEN** README.md
- **WHEN** the "Running the tests" section is read
- **THEN** it contains the line `python3 tests/test_config_scopes.py`
<!-- proven by test CP2-T21 in tests/test_config_scopes.py, commit 7dcdc99 -->

#### Scenario: The new test file is in the test-inventory fixture
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md, and this test file loads ratchet.py with spec_from_file_location
- **WHEN** the inventory is read
- **THEN** tests/test_config_scopes.py appears in it, the way tests/test_modularize_inventory.py counts such files
<!-- proven by test CP2-T22 in tests/test_config_scopes.py, commit 7dcdc99 -->

### Requirement: One line per role: the agents setting
The system SHALL do this: A developer can set any role's model (or provider and model) with one short line using a plain role name, and a wrong role, provider or value is refused before any work starts.

- Setting agents.implementer to a model makes the implementer role use that model, at either level
- An unknown role, unknown provider or badly written value is refused with a message listing the valid choices, both when set and when any command loads the settings
- Setting the same role in both the simple and the advanced setting in one file is refused naming both; across files the project's choice wins
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: A project setting names a model for the implementer
- **GIVEN** A throwaway project whose .ratchet/config.json has agents.implementer = "opus" and no global file
- **WHEN** `models --json` is run
- **THEN** roles["ratchet-implementer"] is "opus" while every other role equals the built-in default
<!-- proven by test CP3-T01 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: The same line works in the global file
- **GIVEN** The global config.json has agents.test-writer = "haiku"; the project sets nothing
- **WHEN** `models --json` is run in the project
- **THEN** roles["ratchet-test-writer"] is "haiku"; other roles are unchanged
<!-- proven by test CP3-T02 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: The project's choice beats the global choice
- **GIVEN** Global agents.implementer = "sonnet", project agents.implementer = "opus"
- **WHEN** `models --json` is run, then the project value is removed and it is run again
- **THEN** ratchet-implementer is "opus" first and "sonnet" after the removal
<!-- proven by test CP3-T03 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Every friendly role name maps to its own agent
- **GIVEN** agents set for every sub-agent stem (checkpoint-planner, implementer, fixer, adversary, visual-reviewer, behavior-reviewer, app-scout, test-planner, test-writer, design-system, prototyper, test-runner, variant-writer), each to a distinct model name
- **WHEN** `models --json` is run
- **THEN** each ratchet-<stem> role shows exactly its own model, none swapped or dropped
<!-- proven by test CP3-T04 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: With no agents setting nothing changes
- **GIVEN** A project and a private Ratchet home with no agents key anywhere
- **WHEN** `models --json` and the effective `config` are read
- **THEN** the roles map equals the built-in tiers resolved as today and the effective config has no agents key
<!-- proven by test CP3-T05 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Setting agents.<role> through the config command is accepted at both levels
- **GIVEN** A project with no settings
- **WHEN** `config --set agents.fixer=opus` and then `config --global --set agents.app-scout=haiku` are run
- **THEN** each exits 0, writes only its own file (project vs global), and `models --json` shows both models
<!-- proven by test CP3-T06 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: A provider/model value is accepted for a known provider
- **GIVEN** The built-in providers list contains codex
- **WHEN** `config --set agents.reviewer=codex/gpt-5` is run
- **THEN** exits 0 and the project file stores "codex/gpt-5"
<!-- proven by test CP3-T07 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: An unknown role is refused on set, listing the valid roles
- **GIVEN** A project with no settings
- **WHEN** `config --set agents.implmenter=opus` (typo) is run
- **THEN** exits non-zero, the message names `implmenter` and lists the valid roles (implementer, test-writer, reviewer, orchestrator, director and the rest), and config.json is unchanged
<!-- proven by test CP3-T08 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: An unknown provider is refused on set, listing the valid providers
- **GIVEN** A project with no settings
- **WHEN** `config --set agents.reviewer=nosuchco/x1` is run
- **THEN** exits non-zero, the message names `nosuchco` and lists the known provider ids, and nothing is written
<!-- proven by test CP3-T09 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Badly written values are refused
- **GIVEN** A project with no settings
- **WHEN** agents.implementer is set to an empty string, "a/b/c", "/opus", "codex/" and the number 5
- **THEN** each is refused with a message showing the allowed forms ("model" or "provider/model") and nothing is written
<!-- proven by test CP3-T10 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: A hand-edited bad value is refused when any command loads settings
- **GIVEN** One project config.json hand-edited with agents.implementer = "codex/" and another with an unknown role agents.nobody
- **WHEN** `models --json`, `status` and `reviewers CP1` are run against each
- **THEN** each stops with a message naming the file and key and listing valid choices, before any run file changes
<!-- proven by test CP3-T11 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: A bad value in the global file names the global file
- **GIVEN** The global config.json has agents.fixer = "x/y/z"; the project file is fine
- **WHEN** `models --json` is run
- **THEN** it stops and the message names the global file path, not the project file
<!-- proven by test CP3-T12 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: agents must be an object
- **GIVEN** A project config.json with "agents": "opus", and another with a list
- **WHEN** `models --json` is run
- **THEN** refused naming the file and saying agents must map role names to values
<!-- proven by test CP3-T13 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: The same role in the simple and advanced setting in one file is refused
- **GIVEN** One config.json has agents.implementer = "opus" and role_models["ratchet-implementer"] = "light"
- **WHEN** `models --json` is run, and `config --set` is used to build that state from a file that has one of the two
- **THEN** both refuse, the message names both agents.implementer and role_models.ratchet-implementer, and the set leaves the file unchanged
<!-- proven by test CP3-T14 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Simple and advanced settings for different roles in one file are fine
- **GIVEN** One file has agents.implementer = "opus" and role_models["ratchet-fixer"] = "light"
- **WHEN** `models --json` is run
- **THEN** implementer is opus and fixer is the light-tier model; no conflict is reported
<!-- proven by test CP3-T15 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Across files the higher level wins whichever key it used
- **GIVEN** Arrangement A: global role_models["ratchet-implementer"] = "light", project agents.implementer = "opus". Arrangement B: global agents.implementer = "opus", project role_models["ratchet-implementer"] = "light"
- **WHEN** `models --json` is run for each
- **THEN** A shows opus, B shows the light-tier model (the project wins both times) and neither is reported as a conflict
<!-- proven by test CP3-T16 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Reviewer, orchestrator and director entries are accepted but change nothing yet
- **GIVEN** Config has agents.reviewer = "codex/gpt-5", agents.orchestrator = "opus", agents.director = "opus"
- **WHEN** `models --json` and `reviewers CP1` are run
- **THEN** no error; the roles map and the picked reviewers equal the no-agents result
<!-- proven by test CP3-T17 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: The agents setting is locked at approval
- **GIVEN** An approved run, then agents.implementer is added to the project config.json
- **WHEN** the next command that checks the approved settings runs
- **THEN** it asks for approval like any other locked-setting change, and the approval snapshot lists agents
<!-- proven by test CP3-T18 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: The agents setting is lane-local at integrate
- **GIVEN** The integrate lane-local key list
- **WHEN** it is read
- **THEN** it contains agents, like role_models
<!-- proven by test CP3-T19 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Choosing a model never approves paid use
- **GIVEN** A paid-per-use provider exists and agents.reviewer names it
- **WHEN** the effective config is read and `reviewers CP1` is run
- **THEN** paid_ok_at is set nowhere and that provider is still not used without the developer's own reply
<!-- proven by test CP3-T20 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: Earlier steps still hold
- **GIVEN** A global role_models entry and a project with no settings
- **WHEN** `config --set foo` (no '=') and `models --json` are run
- **THEN** the no-'=' set is still refused with nothing written and the global role_models value still shows
<!-- proven by test CP3-T21 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: README lists the new test file
- **GIVEN** README.md at the repo root
- **WHEN** its "Running the tests" block is read
- **THEN** it contains the line `python3 tests/test_agents_config.py`
<!-- proven by test CP3-T22 in tests/test_agents_config.py, commit 72c3941 -->

#### Scenario: The test inventory fixture counts the new test file
- **GIVEN** The test-inventory fixture under tests/fixtures that scans engine source
- **WHEN** it is read
- **THEN** it registers tests/test_agents_config.py with the other suites
<!-- proven by test CP3-T23 in tests/test_agents_config.py, commit 72c3941 -->

### Requirement: See requested versus real model per role
The system SHALL do this: One read-only command shows every role's requested model, the model that will really be used, where the request came from, and a plain reason whenever the two differ.

- Every role is listed, including orchestrator, director, reviewers and UI reviewers, with requested, real and source
- When they differ the line says why: unknown tier on this host, host cannot choose a model per sub-agent, provider unavailable or out of quota, same company as the host, or needs paid approval
- The command never contacts a provider or spends anything, and the old JSON fields stay the same for agents that read them
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: Every role is listed with asked-for, real and where it came from
- **GIVEN** a fresh project on an anthropic host, no agents setting anywhere
- **WHEN** run `models --json`
- **THEN** the new per-role detail list has an entry for every sub-agent role in the roles table plus orchestrator, director, reviewer and ui-reviewer, each with requested, effective, source and reason keys; source is builtin for all of them
<!-- proven by test CP4-T01 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: The old JSON fields are unchanged
- **GIVEN** a fresh project on an anthropic host, and one on a github host
- **WHEN** run `models --json` with the new detail list present
- **THEN** host, roles and alternates keep the same keys and values as before (test-writer sonnet, implementer inherit; github host: every role inherit); the detail list is an extra key only
<!-- proven by test CP4-T02 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: The plain text output keeps its old lines and adds the new ones
- **GIVEN** a fresh project, no settings
- **WHEN** run `models` without --json
- **THEN** the header line and one row per old role keep the old layout, followed by rows for orchestrator, director and reviewers showing requested, real and source
<!-- proven by test CP4-T03 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: A role set in the project file shows source project
- **GIVEN** the project file asks implementer to use sonnet, the global file is empty
- **WHEN** run `models --json`
- **THEN** implementer detail has requested sonnet, effective sonnet, source project and no reason
<!-- proven by test CP4-T04 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: A role set in the global file shows source global, and the project wins over it
- **GIVEN** global file sets implementer opus and test-writer haiku; project file sets implementer sonnet
- **WHEN** run `models --json`
- **THEN** implementer shows sonnet from project, test-writer shows haiku from global, each with its source named
<!-- proven by test CP4-T05 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: An unknown tier on this host is explained
- **GIVEN** an anthropic host, and a role asked for a tier name the host model table does not have
- **WHEN** run `models --json`
- **THEN** requested shows the tier, effective is the session's own model, and the reason says the tier is unknown on this host
<!-- proven by test CP4-T06 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: A host that cannot choose a model per sub-agent says so
- **GIVEN** RATCHET_HOST_PROVIDER=github (and a second case with openai), project asks implementer for sonnet
- **WHEN** run `models --json`
- **THEN** implementer effective is the session's own model, requested is still sonnet, and the reason says this host cannot choose a model per sub-agent; nothing is faked as applied
<!-- proven by test CP4-T07 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: A host with a model table but no entry for the asked tier falls back plainly
- **GIVEN** an anthropic host whose host_models table lacks the entry for a tier a role asks for
- **WHEN** run `models --json`
- **THEN** effective falls back to the session's model with a reason naming the missing host entry, and the command does not crash
<!-- proven by test CP4-T08 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: The reviewer line shows who will really review
- **GIVEN** the project asks the reviewer to be a registered, available provider codex with model gpt-5
- **WHEN** run `models --json`
- **THEN** reviewer detail requested names codex gpt-5, effective names the plain reviewer (Codex, gpt-5) and no reason is shown
<!-- proven by test CP4-T09 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: An unavailable or out-of-quota reviewer provider falls back with a reason
- **GIVEN** the asked reviewer provider is listed in unavailable-reviewers.json (and a second case with its quota used up)
- **WHEN** run `models --json`
- **THEN** reviewer effective is the fallback resolve_reviewers would pick now, and the reason says the provider is unavailable or out of quota
<!-- proven by test CP4-T10 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: Same company as the host is explained
- **GIVEN** an anthropic host, the reviewer asked for is a claude provider and no other company is available
- **WHEN** run `models --json`
- **THEN** the reviewer reason says it is the same company as the host, and effective is the host's own reviewer
<!-- proven by test CP4-T11 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: A paid reviewer model is shown as needing approval and the choice does not approve it
- **GIVEN** the reviewer asked for is a provider with a per-use cost and no paid_ok_at, chosen once in the project file and once in the global file
- **WHEN** run `models --json`, then read the config files and the human log
- **THEN** the reviewer reason says it needs paid approval, effective is not that provider, and no paid_ok_at exists anywhere afterwards
<!-- proven by test CP4-T12 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: UI reviewers are listed with requested and real
- **GIVEN** default config, and a second project with ui_reviewers set to a registered provider
- **WHEN** run `models --json`
- **THEN** ui-reviewer entries appear in the detail list: source builtin for the defaults, and the matching source and provider when set
<!-- proven by test CP4-T13 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: Orchestrator and director show what was asked, and are never claimed as switched
- **GIVEN** the project file asks orchestrator opus and director sonnet on an anthropic host
- **WHEN** run `models --json` and `models`
- **THEN** both appear with requested opus and sonnet and source project, and no line claims a running session was switched
<!-- proven by test CP4-T14 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: The command contacts no provider and spends nothing
- **GIVEN** fake agent CLIs on a private PATH that write a marker file when run, with reviewers configured, and the quota, provider-health, provider-status and unavailable-reviewers files recorded beforehand
- **WHEN** run `models` and `models --json`
- **THEN** exit code is 0, no fake CLI ran (no marker file), and every recorded file is byte-for-byte unchanged
<!-- proven by test CP4-T15 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: A bad role, provider or model name is refused with a plain message
- **GIVEN** the project file has an agents entry with an unknown role name (and a second case with an unknown provider name)
- **WHEN** run `models`
- **THEN** it exits non-zero with a message naming the bad name and the file it is in, and prints no table
<!-- proven by test CP4-T16 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: A damaged global config file stops the command naming the file
- **GIVEN** the global config file holds broken JSON
- **WHEN** run `models --json`
- **THEN** it exits non-zero, the message names the global file path, and stdout holds no half-printed JSON
<!-- proven by test CP4-T17 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: With no settings the output matches today's
- **GIVEN** a fresh project with an empty per-user config folder
- **WHEN** run `models --json` and `plan-summary`
- **THEN** the roles map equals the pre-change value for every old role and the plan-summary Models line wording is unchanged
<!-- proven by test CP4-T18 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: Listing models changes nothing about who reviews or how many
- **GIVEN** a project with the default review_depth and a normal-risk step
- **WHEN** resolve the review roster before and after running `models` with several roles and a reviewer set
- **THEN** the same reviewer ids in the same count are chosen both times
<!-- proven by test CP4-T19 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: The new test file is listed in the README's Running the tests commands
- **GIVEN** the repo README
- **WHEN** read the Running the tests command list
- **THEN** the line `python3 tests/test_models_explain.py` appears exactly once
<!-- proven by test CP4-T20 in tests/test_models_explain.py, commit aaa8ec4 -->

#### Scenario: The new test file is registered in the test-inventory fixture when it scans engine source
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md and a live search of tests/ for engine-source readers
- **WHEN** check whether tests/test_models_explain.py reads or parses ratchet.py source text
- **THEN** if it does, the inventory names it; if it does not, the inventory needs no entry and the live search used by test_modularize_inventory.py still agrees
<!-- proven by test CP4-T21 in tests/test_models_explain.py, commit aaa8ec4 -->

### Requirement: Choose the outside reviewer's provider and model
The system SHALL do this: A developer can pick which outside provider and model reviews code and UI first, and that is what actually runs, while the number of reviewers, cross-company review and paid approval stay exactly as they are.

- With no reviewer choice, the same reviewers are picked as today at low, normal and high risk
- A chosen reviewer provider and model is tried first for each review slot and the review record shows the model that really ran
- The number of reviewers per risk level never changes, a paid provider still needs the developer's own pay-per-use reply, and a same-company pick is explained and not counted as independent
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: No reviewer choice: same reviewers as today at every risk level
- **GIVEN** A fixture roster of three outside providers (two flat-rate from different companies, one pay-per-use) plus the host, and no agents setting at any level; today's resolve_reviewers output recorded for low, normal and high risk before the change
- **WHEN** Reviewers are resolved at low, normal and high risk
- **THEN** Ids, slot numbers, fallback_for and reasons equal the recorded output exactly for all three risks
<!-- proven by test CP5-T01 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: An empty agents setting changes nothing
- **GIVEN** Same roster; agents is {} in the project file
- **WHEN** Reviewers are resolved at all three risks
- **THEN** Output equals the no-agents output byte for byte
<!-- proven by test CP5-T02 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A chosen reviewer provider is tried first
- **GIVEN** Roster as above; agents.reviewer = 'codex' where the default ranking would put 'gemini' first
- **WHEN** Reviewers are resolved for a normal-risk step with an any-code slot
- **THEN** Slot 1 resolves to codex's reviewer id, and gemini is not skipped for a reason (it simply ranks after)
<!-- proven by test CP5-T03 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A provider/model choice launches that model
- **GIVEN** agents.reviewer = 'codex/gpt-5.1' and codex's preset has a different default model
- **WHEN** The reviewer command is built for the codex reviewer
- **THEN** The command carries gpt-5.1 in the model position, not the default, and resolve_reviewers reports model gpt-5.1
<!-- proven by test CP5-T04 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: The chosen provider leads every review slot's candidate order
- **GIVEN** Normal risk with two any-code slots; agents.reviewer = 'codex'
- **WHEN** Reviewers are resolved
- **THEN** Codex is the first candidate tried for each slot; the second slot still moves to a different company when the existing rule says so, with that reason shown
<!-- proven by test CP5-T05 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: Number of reviewers never changes with a choice
- **GIVEN** Low, normal and high risk each have a known slot count today (recorded); agents.reviewer = 'codex/gpt-5.1' set globally and in the project
- **WHEN** Reviewers are resolved at each risk
- **THEN** len(result) per risk equals today's count, review_depth is unchanged, and blocking_severities is unchanged
<!-- proven by test CP5-T06 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A paid provider choice still needs the developer's own reply
- **GIVEN** The chosen provider is pay-per-use with no recorded OK; a flat-rate provider is available
- **WHEN** Reviewers are resolved
- **THEN** The paid provider is not picked, the reason says it needs the developer's pay-per-use approval, the flat-rate one runs, and no paid_ok_at is written to any config or record
<!-- proven by test CP5-T07 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A paid choice with the developer's recorded OK is allowed but ranks after flat-rate
- **GIVEN** Chosen provider is pay-per-use with a recorded OK; a flat-rate provider of another company is available
- **WHEN** Reviewers are resolved
- **THEN** The preference does not lift the pay-per-use provider over the usable flat-rate one (preference works only inside the existing cost tier)
<!-- proven by test CP5-T08 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: Choosing a model never grants paid approval
- **GIVEN** agents.reviewer names a pay-per-use provider in the global file
- **WHEN** config is loaded, reviewers resolved and models shown
- **THEN** providers[].paid_ok_at stays unset, the paid provider is still reported as needing approval
<!-- proven by test CP5-T09 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A same-company pick is explained and not counted as independent
- **GIVEN** The host is Claude Code and agents.reviewer names the same company's provider; another company's provider is available
- **WHEN** Reviewers are resolved at normal and high risk
- **THEN** A different company still fills the independent slot, the same-company pick carries a plain reason saying it is not independent, and the count of independent reviewers is not raised by it
<!-- proven by test CP5-T10 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: Same-company choice with no other company is never silently counted
- **GIVEN** Only same-company providers exist and agents.reviewer names one
- **WHEN** Reviewers are resolved
- **THEN** The result still reports it as same-company (not independent) rather than hiding it; cross-company requirement is not removed
<!-- proven by test CP5-T11 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: An explicit reviewer id in review_depth keeps its written order
- **GIVEN** review_depth.normal = ['claude-code-both','codex-both'] and agents.reviewer = 'gemini'
- **WHEN** Reviewers are resolved
- **THEN** Written order is kept, and gemini is reported as not applied for that slot
<!-- proven by test CP5-T12 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A checkpoint reviewers pin keeps its written order
- **GIVEN** Checkpoint pins reviewers ['host-both'] and agents.reviewer = 'codex'
- **WHEN** Reviewers are resolved
- **THEN** Only the pinned reviewer is used and the output says agents.reviewer was not applied
<!-- proven by test CP5-T13 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: An unavailable chosen provider falls back plainly
- **GIVEN** agents.reviewer = 'codex' but codex is marked unavailable (quota)
- **WHEN** Reviewers are resolved
- **THEN** The slot goes to the next working provider, codex appears in fallback_for with its reason, host sub-agents remain the last fallback
<!-- proven by test CP5-T14 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A chosen provider already at its share cap is skipped
- **GIVEN** agents.reviewer = 'codex' and codex has hit provider_share max
- **WHEN** Reviewers are resolved
- **THEN** Codex is skipped with the share reason; the choice does not bypass the cap
<!-- proven by test CP5-T15 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A duplicate of the same company, model and lens still collapses
- **GIVEN** Two slots; chosen provider/model equals what slot 1 already picked
- **WHEN** Reviewers are resolved
- **THEN** The second slot collapses as it does today, with the existing reason
<!-- proven by test CP5-T16 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: An unknown reviewer provider is refused before any work
- **GIVEN** agents.reviewer = 'nosuch' in the project file
- **WHEN** reviewers (and plan-submit) are run
- **THEN** The command stops with a message listing valid providers; no round is opened, nothing is launched
<!-- proven by test CP5-T17 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: Visual and behavior reviewer choices go to the front of the UI order
- **GIVEN** ui_reviewers visual = [a,b] and behavior = [a,b]; agents.visual-reviewer = 'b', agents.behavior-reviewer = 'b/model-x'
- **WHEN** The UI reviewer order is resolved for each lens
- **THEN** b is first for both lenses, behavior uses model-x, and the host is still the final fallback
<!-- proven by test CP5-T18 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: UI reviewer with no choice is unchanged
- **GIVEN** No agents.visual-reviewer or behavior-reviewer
- **WHEN** The UI reviewer order is resolved
- **THEN** Order equals today's ui_reviewers order, host last
<!-- proven by test CP5-T19 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: The review record shows the model that really ran
- **GIVEN** A stub outside-reviewer CLI that records its argv; agents.reviewer = 'stub/model-x'
- **WHEN** external_review runs the reviewer and the record is saved
- **THEN** The record names model-x and the stub's argv contained model-x
<!-- proven by test CP5-T20 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: The model stamp is covered by review signing
- **GIVEN** A signed review record that names model-x
- **WHEN** The model field is edited by hand and adv-record reads it
- **THEN** The record is refused as not signed by Ratchet; the untouched record is accepted
<!-- proven by test CP5-T21 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: A provider-only choice records the provider's default model as today
- **GIVEN** agents.reviewer = 'stub' (no model)
- **WHEN** The reviewer runs
- **THEN** The record shows what ran as today, with no invented model name
<!-- proven by test CP5-T22 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: Global choice applies and project choice wins
- **GIVEN** Global agents.reviewer = 'gemini', project agents.reviewer = 'codex'
- **WHEN** Reviewers are resolved with the project, then with the project value removed
- **THEN** Codex first with the project value, gemini first without it
<!-- proven by test CP5-T23 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: Verdict counting is unchanged by a choice
- **GIVEN** A round with two reviewers whose findings give a known verdict with no choice
- **WHEN** The same round is recorded with agents.reviewer set
- **THEN** The verdict and blocking-severity outcome are identical
<!-- proven by test CP5-T24 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: README lists the new test file
- **GIVEN** README.md
- **WHEN** The Running the tests block is read
- **THEN** It contains the line python3 tests/test_agents_reviewers.py
<!-- proven by test CP5-T25 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: Test-inventory fixture registers the new file
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md (or the scanner's inventory) when it scans engine source
- **WHEN** The inventory check is run
- **THEN** tests/test_agents_reviewers.py is counted with the other files
<!-- proven by test CP5-T26 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: The models view says reviewer was used exactly when reviewers would pick it
- **GIVEN** Roster as above; agents.reviewer = a usable chosen provider, then an unusable one (benched, at its share cap, paid without the developer's reply)
- **WHEN** The models view and the real reviewer resolution are both run on the same settings
- **THEN** The view's effective reviewer matches the provider the real resolution picks first in every case: used when usable, a plain fallback reason when not (the CP4 requested-versus-real parity, re-checked now that the choice reaches real selection)
<!-- proven by test CP5-T27 in tests/test_agents_reviewers.py, commit 064b139 -->

#### Scenario: The UI review path honors paid consent
- **GIVEN** ui_reviewers (or agents.visual-reviewer / behavior-reviewer) names a per-use paid provider with no recorded developer OK, and a free one after it
- **WHEN** ui_review.py picks the runner for each lens
- **THEN** The unapproved paid provider is skipped with a plain reason and the next free runner or the host is used; with the developer's recorded OK it may be used; the models view no longer says the paid-consent check is tracked to be closed
<!-- proven by test CP5-T28 in tests/test_agents_reviewers.py, commit 064b139 -->

### Requirement: Sub-agents launch on the chosen model
The system SHALL do this: When a developer picks a model for a sub-agent role, the agent is told that model in its brief and, on Claude Code, a launch on any other model is stopped with a plain fix, so the setting is never decorative.

- A role's brief names the model to launch it with when one was chosen
- On Claude Code, launching that role on a different model is refused with a message naming the right one; with no choice made, launches work exactly as today
- On hosts that cannot pick a model per sub-agent, the read-only view says the choice cannot be applied there instead of claiming it is
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: The brief names the model chosen for a role
- **GIVEN** A building run whose project .ratchet/config.json sets agents.implementer to "sonnet"
- **WHEN** `ratchet.py brief CP1 --role implementer` runs
- **THEN** Output contains exactly one line `Launch with model: sonnet` besides the usual objective text; the same call for --role fixer (no choice) has no such line
<!-- proven by test CP6-T01 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: With no choice made the brief is unchanged
- **GIVEN** A building run with no agents, role_models or global config anywhere
- **WHEN** `brief CP1 --role implementer` runs
- **THEN** Output has no `Launch with model` line and is byte-identical to the brief produced from the same plan before any config file existed
<!-- proven by test CP6-T02 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A model chosen in the global file reaches the brief, and the project overrides it
- **GIVEN** Global config sets agents.test-writer to "anthropic/haiku"; project config has no agents key; later the project sets agents.test-writer to "opus"
- **WHEN** `brief CP1 --role test-writer` runs before and after the project value is set
- **THEN** First brief carries `Launch with model: haiku` (the model part of provider/model); second carries `Launch with model: opus`
<!-- proven by test CP6-T03 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: Launching a role on a different model is refused with the right name
- **GIVEN** Claude Code host, phase building, project agents.implementer = "sonnet", prompt carries the objective
- **WHEN** `hook guard --host claude` gets an Agent launch of ratchet-implementer with model "opus"
- **THEN** Exit 2; the message names `sonnet`, says to relaunch with it, and points at the agents line that set it; no state or config file is modified
<!-- proven by test CP6-T04 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: Launching on the chosen model is allowed
- **GIVEN** Same setup as CP6-T04
- **WHEN** Guard gets the launch with model "sonnet", then again as `ratchet-loop:ratchet-implementer`
- **THEN** Both exit 0 with no refusal text
<!-- proven by test CP6-T05 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A launch with no model parameter is refused when a model was chosen
- **GIVEN** Same setup as CP6-T04
- **WHEN** Guard gets a ratchet-implementer launch with no `model` field in tool_input
- **THEN** Exit 2 naming `sonnet`, since an unset model would inherit the parent session's model, not the chosen one
<!-- proven by test CP6-T06 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: Without a choice every launch works as today
- **GIVEN** Claude host, building, no agents or role_models entry at any level (built-in defaults only)
- **WHEN** Guard gets ratchet-implementer launches with model "opus", with "haiku" and with no model
- **THEN** All exit 0: defaults stay advisory and are never enforced
<!-- proven by test CP6-T07 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: The older role_models key is enforced the same way
- **GIVEN** Project config sets role_models.implementer (advanced key) to "opus" and no agents key
- **WHEN** Guard gets ratchet-implementer launches on "sonnet" and then on "opus"
- **THEN** "sonnet" exits 2 naming opus; "opus" exits 0
<!-- proven by test CP6-T08 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: The project level beats the global level at launch
- **GIVEN** Global agents.implementer = "haiku", project agents.implementer = "opus"
- **WHEN** Guard gets launches on haiku and on opus
- **THEN** haiku is refused naming opus; opus is allowed
<!-- proven by test CP6-T09 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: Hosts other than Claude Code are never refused
- **GIVEN** Same setup as CP6-T04
- **WHEN** `hook guard --host codex` (then gemini, copilot) gets an equivalent sub-agent launch with a different model
- **THEN** Exit 0 on each host
<!-- proven by test CP6-T10 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: Non-Ratchet sub-agents and runs that are not building are not checked
- **GIVEN** Same setup as CP6-T04
- **WHEN** Guard gets (a) subagent_type general-purpose on opus, (b) ratchet-implementer on opus while the phase is planning
- **THEN** Both exit 0
<!-- proven by test CP6-T11 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A choice for one role does not bind another role
- **GIVEN** Only agents.implementer = "sonnet" is set
- **WHEN** Guard gets a ratchet-fixer launch on opus
- **THEN** Exit 0 (only the role with an explicit choice is enforced)
<!-- proven by test CP6-T12 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: An unreadable config never blocks a launch
- **GIVEN** A building run; project config.json holds invalid JSON
- **WHEN** Guard gets a ratchet-implementer launch on any model
- **THEN** Exit 0 (fail open) and the hook crash is still recorded like any other hook crash, not a silent pass
<!-- proven by test CP6-T13 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: The read-only view says the choice cannot be applied on a host that cannot pick
- **GIVEN** Host detected as Codex (and separately Gemini); project agents.implementer = "sonnet"
- **WHEN** `ratchet.py models --json` and the plain `models` run
- **THEN** The implementer entry says the choice cannot be applied on this host and its real model differs from the request; nothing in the text says the model will be used; no provider is contacted
<!-- proven by test CP6-T14 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: On Claude Code the view says the choice is applied
- **GIVEN** Claude host; project agents.implementer = "sonnet"
- **WHEN** `models --json` runs
- **THEN** Implementer requested and real are both sonnet, source project, no cannot-apply reason; the legacy keys host, roles, alternates are unchanged
<!-- proven by test CP6-T15 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: The new test file is listed in the README
- **GIVEN** README.md in the repo
- **WHEN** Its "Running the tests" block is read
- **THEN** It contains the line `python3 tests/test_agents_launch.py` exactly once, in the same block as test_brief_guard.py
<!-- proven by test CP6-T16 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: The new test file is in the test-inventory fixture
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md and the test that scans it
- **WHEN** The fixture is read
- **THEN** test_agents_launch.py is registered the way test_brief_guard.py is, and the inventory-scanning tests (test_modularize_cp7.py) still pass when run targeted
<!-- proven by test CP6-T17 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A chosen sub-agent model that is not a real model name is refused before launch
- **GIVEN** Claude host; agents.implementer = a misspelled model such as "sonet" (not a tier, an alias or an id the host's model table knows)
- **WHEN** Commands load the settings (config, models, status), then `brief`, a launch of that role and `config --set` are run, with the misspelled name in the project file, then in the global file
- **THEN** LOAD STAYS TOLERANT (director ruling Oct 30): the file still loads, `config`, `models`, `status` and the repair commands (`config --unset`, `config --global --unset`, `config --global --set`) all run, and `models` shows the name as invalid (unknown tier or model) and never as used. The misspelled model is REFUSED where work starts: `brief` and the launch guard refuse it before launch with a message naming the file, agents.<role> and the valid tier, alias and id names; `config --set` refuses to write it. A full id the table cannot judge loads and is shown as not verified, never as used; a valid tier, alias or id still launches. A typo in the global file never blocks a project `--set` or `--unset` (keeps CP3-X16, CP3-X13, CP4-T06 and CP4-X01 green exactly as they are). Carried from the CP5 director ruling that real model-name validity is enforced here (closes the CP5 codex-correctness X1 class)
<!-- proven by test CP6-T18 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A chosen outside-reviewer model that the provider says it does not offer is refused before any reviewer launches
- **GIVEN** A provider that declares the models it offers (or whose recorded discovery lists them); agents.reviewer = provider/not-offered-model, then provider/offered-model, then a provider that declares nothing
- **WHEN** Config loads (still tolerant: it loads, `config`, `models` and the repair commands run), `reviewers` and `plan-submit` are run, and `models` is read
- **THEN** The not-offered model is refused by `reviewers` and `plan-submit` before any round opens or test request is spent, naming the model, the provider, the file and the provider's offered models (it does NOT stop the load or the repair commands; `models` shows it as not offered by that provider and never as used); the offered model is applied; for a provider that declares nothing the model is passed through and `models` says it is not verified (never claimed verified); no paid approval is granted by any of these paths
<!-- proven by test CP6-T19 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A review that fails partway on a chosen model does not leave the reviewer out after the choice is removed or changed
- **GIVEN** agents.reviewer = codex/model-x; a review launched on model-x fails partway (timeout, auth, non-quota error) so external_review.py marks the provider unavailable; the same with the failure coming from a failed test request
- **WHEN** The choice is removed, then changed to another model, and `reviewers` and `models` are read each time
- **THEN** The mark names model-x (external_review.py's mark_unavailable passes the launched model); with the choice removed or changed the provider is available at once on its own model; with the choice still model-x the reviewer stays out with the reason; must FAIL on the pre-fix code where the partway-failure mark carries no model and the provider stays out for unavailable_ttl_hours (carried from the CP5 firm obligation, external_review.py ~726)
<!-- proven by test CP6-T20 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A hand-written launch_model in a provider entry changes nothing
- **GIVEN** A providers entry in a settings file that carries a launch_model key (the key is in-memory only, set by the choice logic)
- **WHEN** Config loads, then reviewers are resolved and a reviewer is launched
- **THEN** The hand-written key is refused at load naming the key and file, or ignored so the launched model and the paid-approval check use the provider's own model; either way no model is launched or reported that no one chose (carried from the CP5 round-5 minor)
<!-- proven by test CP6-T21 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A bad UI reviewer model is refused where the UI review launches, and the view never shows it as used
- **GIVEN** agents.visual-reviewer and agents.behavior-reviewer name a provider/model; the model is not in the provider's declared models list (or is a misspelled bare model), then a valid listed model, then a provider that declares nothing
- **WHEN** Config loads, `models` is read, and the UI review launch (ui_review.py) picks the runner for each lens
- **THEN** Load stays tolerant and `config`, `models` and the repair commands run; `models` shows the bad model as invalid or not offered and never as used; ui_review.py refuses the bad model before it launches that runner, naming the model, the provider and the offered list, and uses the next free runner or the host; a valid listed model is applied and launched; a provider that declares nothing passes the model through and `models` says not verified; no paid approval is granted by any of these paths (director ruling Oct 30: UI reviewer roles are named by done-means line 3 and must not be an unchecked pass-through)
<!-- proven by test CP6-T22 in tests/test_agents_launch.py, commit 4c572ba -->

#### Scenario: A bad reviewer model blocks only real work-start: not a read-only display, not a review-skipped step
- **GIVEN** agents.reviewer names a model outside the provider's declared models list; (a) a round for the step is already open, (b) the step is docs-only so the outside review is skipped, (c) a step where a round would open
- **WHEN** `reviewers` is run read-only to display (a), is run for the docs-only step (b), and is run for the real step (c)
- **THEN** (a) and (b) never refuse and print what they print today; only (c), where a round would open or a reviewer would launch, refuses before any round opens or test request is spent, naming the model, provider, file and offered list (director ruling Oct 30: refuse at work-start only, never when merely showing state or when no outside review runs)
<!-- proven by test CP6-T23 in tests/test_agents_launch.py, commit 4c572ba -->

### Requirement: Orchestrator and director: start-time model
The system SHALL do this: A developer who picks a model for the orchestrator or director gets the exact command to start a session on it, lanes started by Ratchet use it, and Ratchet says plainly it cannot switch a session already running.

- The read-only view shows the orchestrator and director choice with the start command for this agent tool (for example claude --model, codex -m, gemini -m)
- A lane start command can carry the orchestrator's chosen model, so lanes Ratchet prints start on it
- Ratchet never claims a running session switched; it says the choice applies to the next session it starts
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: Orchestrator choice shows the start command for a Claude Code host
- **GIVEN** Host is Claude Code; project .ratchet/config.json sets agents.orchestrator to claude-opus-4-1
- **WHEN** The read-only models view runs (text and --json)
- **THEN** The orchestrator line names the requested model and prints the start command 'claude --model claude-opus-4-1'; the --json detail for orchestrator carries the same command; no provider is contacted
<!-- proven by test CP7-T01 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Director choice prints a start instruction only
- **GIVEN** agents.director is 'openai/gpt-5' in the global file
- **WHEN** The models view runs
- **THEN** The director line shows 'codex -m gpt-5' as an instruction for starting a new director session, says Ratchet does not inspect the running director's model, and no Paseo wording or dependency appears
<!-- proven by test CP7-T02 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Start command uses each host's own model flag
- **GIVEN** agents.orchestrator is set to a model M that the host's own tool can run (an OpenAI model on codex, a Google model on gemini, and for the multi-company tools copilot and opencode any known model), while the host is codex, then gemini, then copilot, then opencode
- **WHEN** The models view runs for each host
- **THEN** The commands are 'codex -m M', 'gemini -m M', 'copilot --model M', 'opencode -m M' (matching the PRESETS model flags), never the claude form (director amendment Oct 31: M is a model that host's tool can run, so no case blesses a start command the tool cannot run; see CP7-T18)
<!-- proven by test CP7-T03 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Ratchet never claims a running session switched
- **GIVEN** agents.orchestrator and agents.director are set; the session is already running
- **WHEN** The models view and config --set for these roles print their output
- **THEN** Output says the choice applies to the next session Ratchet starts and that a running session cannot be switched; it never says 'switched', 'now using' or 'applied to this session'
<!-- proven by test CP7-T04 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Current session model is reported as cannot confirm
- **GIVEN** agents.orchestrator is set to a model; nothing in the environment names the running model
- **WHEN** The models view runs
- **THEN** The line says Ratchet cannot confirm which model the current session runs on, and does not state the requested model as the current one
<!-- proven by test CP7-T05 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: No orchestrator or director choice leaves output unchanged
- **GIVEN** No agents.orchestrator or agents.director anywhere (default config)
- **WHEN** The models view runs and a lane start command is printed
- **THEN** No start command or 'next session' note is printed for these roles, and the existing --json keys (host, roles, alternates) are identical to today's
<!-- proven by test CP7-T06 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Lane start command fills {model} with the orchestrator's model
- **GIVEN** lane_start_command is ["mycli","--model","{model}","--cwd","{worktree}"] and agents.orchestrator is 'claude-opus-4-1'
- **WHEN** lanes-start prints each lane's start command (text and --json)
- **THEN** Each printed start command contains 'claude-opus-4-1' in place of {model} along with the worktree path, and is only printed, never run
<!-- proven by test CP7-T07 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Lane start command without {model} is unchanged
- **GIVEN** lane_start_command uses only {worktree}, {brief}, {name}, once with and once without an orchestrator choice
- **WHEN** lanes-start prints the start commands
- **THEN** The printed commands equal today's output exactly in both situations
<!-- proven by test CP7-T08 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: {model} is empty when no orchestrator model is chosen
- **GIVEN** lane_start_command contains the argument '--model={model}' and no agents.orchestrator is set
- **WHEN** lanes-start prints the start command
- **THEN** Output is accepted and the {model} slot becomes empty (no literal '{model}' text and no crash), and the other placeholders still fill
<!-- proven by test CP7-T09 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Validator still lists every placeholder including {model}
- **GIVEN** lane_start_command uses an unknown placeholder such as {branch}
- **WHEN** lanes-start runs
- **THEN** It is refused before anything is created, and the message lists {worktree}, {brief}, {name} and {model}; a command using {model} is accepted
<!-- proven by test CP7-T10 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: A badly written orchestrator model is refused before any work
- **GIVEN** agents.orchestrator holds a badly written value (empty string or 'a/b/c')
- **WHEN** The models view and lanes-start run
- **THEN** Both stop with a message naming the file and the valid forms, and no lane, branch or worktree is created
<!-- proven by test CP7-T11 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Choosing an orchestrator or director model does not approve paid use
- **GIVEN** agents.orchestrator is 'openai/gpt-5' where that provider is paid per use and has no paid_ok_at
- **WHEN** The models view runs and reviewers are resolved
- **THEN** The view shows the payment-approval note; paid_ok_at stays unset in every config file, and the reviewer pick and the cross-company rule are the same as without the setting
<!-- proven by test CP7-T12 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Setting the orchestrator model changes no reviewer or verdict rules
- **GIVEN** The same project run twice, with and without agents.orchestrator and agents.director
- **WHEN** reviewers and plan-summary are printed for a normal-risk step
- **THEN** Reviewer picks, reviewer count and blocking rules are identical; only the models and lane output differ
<!-- proven by test CP7-T13 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Project orchestrator choice overrides the global one
- **GIVEN** Global agents.orchestrator is 'model-a'; project agents.orchestrator is 'model-b'
- **WHEN** The models view and lanes-start run, then the project value is removed and they run again
- **THEN** First both show model-b and the view says it came from the project file; after removal both show model-a from the global file
<!-- proven by test CP7-T14 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: New test file is in the README Running the tests list
- **GIVEN** README.md 'Running the tests' command block
- **WHEN** Its lines are read
- **THEN** The line 'python3 tests/test_agents_session.py' is present exactly once, in the alphabetical place its neighbours use
<!-- proven by test CP7-T15 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: Test inventory fixture covers the new file
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md and tests/test_modularize_inventory.py, which scan test files that read engine source
- **WHEN** The inventory check runs for the new file
- **THEN** If tests/test_agents_session.py reads engine source it is listed in the fixture and the inventory test passes; if it does not read engine source the test says so and passes only then
<!-- proven by test CP7-T16 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: The complete local suite passes after this step
- **GIVEN** This step's changes are in place
- **WHEN** The step's own full-suite case runs in its default mode
- **THEN** It passes trivially (ratchet.py complete enforces the full run itself) and does not read regression.log; with RATCHET_CP7_T17_FULL_SUITE=1 it runs tests/run_all.py inline and can really fail
<!-- proven by test CP7-T17 in tests/test_agents_session.py, commit 2c68c72 -->

#### Scenario: A model the host's tool cannot run gets no start command, and a paid choice is not silently approved
- **GIVEN** agents.orchestrator or agents.director is a bare model of a company the host's tool does not run (an OpenAI or Google model on a Claude host, an Anthropic model on codex or gemini); separately provider/model on a per-use provider whose paid OK covers a different model; separately lane_start_command with {model} as its own word and no orchestrator model chosen
- **WHEN** The models view and lanes-start run
- **THEN** The row shows the choice, says plainly that this host's tool cannot run that model, prints NO start command that mixes the tool and the model (never 'claude --model gpt-5'), and points to the provider/model form (for example openai/gpt-5 gives 'codex -m gpt-5') or to starting a session in the tool that runs it; the provider/model form with a per-use provider whose paid OK does not cover the chosen model prints the paid-approval note exactly as the reviewer path judges it; lanes-start with an empty {model} word prints the command and a plain note that the model argument is empty because no orchestrator model is chosen (default output without {model} stays byte-identical). From the CP7 round-2 review (major done-means 4 plus two minors)
<!-- proven by test CP7-T18 in tests/test_agents_session.py, commit 2c68c72 -->

### Requirement: Newcomer guide: change one role, undo it
The system SHALL do this: A newcomer reading the README can change the model for one role, globally or for one project, check it took effect, and return to the defaults.

- The README has a short section with a copyable example for one role, at global and at project level
- It shows the read-only check, how to remove the choice to go back to defaults, and which level wins
- It states plainly what cannot be switched (a running orchestrator or director) and that a model choice never approves paid use, and the command table lists the new options
- The new test file is listed in the README's "Running the tests" commands
- The new test file is registered in the test-inventory fixture when it scans engine source, so it's counted with the rest

#### Scenario: README has a short section for changing one role, with a copyable global example
- **GIVEN** the README as written
- **WHEN** the test looks for the new agents section (heading naming agent/model configuration) and its fenced code blocks
- **THEN** one section exists, is under about 60 lines, and holds a copyable fenced command that sets the implementer role to a provider/model in the global file (names the global file location ~/.config/ratchet/config.json or RATCHET_HOME)
<!-- proven by test CP8-T01 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: README has a copyable project-level example next to the global one
- **GIVEN** the same section
- **WHEN** the test reads its fenced blocks
- **THEN** a second copyable example sets the same role for one project (.ratchet/config.json), and the two examples are different commands, not the same text twice
<!-- proven by test CP8-T02 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: README examples really work when copied
- **GIVEN** a temp project and a temp RATCHET_HOME
- **WHEN** the test extracts the section's copyable ratchet.py commands and runs them in order (set global, set project, run the read-only check)
- **THEN** each exits 0, the global file and the project file contain the role value shown, and the read-only check output names the chosen model and which level it came from
<!-- proven by test CP8-T03 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: README shows the read-only check and which level wins
- **GIVEN** the section
- **WHEN** the test searches it
- **THEN** it names `ratchet.py models` as the read-only check, says the project level beats the global level which beats the built-in defaults, and says models shows which level each value came from
<!-- proven by test CP8-T04 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: README shows how to go back to the defaults, and the steps work
- **GIVEN** a temp project with a global and a project choice for a role set
- **WHEN** the test runs the removal command shown in the README for the project level and then the global level
- **THEN** the README names a removal command for each level; after running them the models output shows the built-in default for that role and the choice no longer appears in either file
<!-- proven by test CP8-T05 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: README says a running orchestrator or director cannot be switched
- **GIVEN** the section
- **WHEN** the test searches it
- **THEN** it says Ratchet cannot switch an already-running orchestrator or director session and that the choice applies when the session is started (with the printed start command), not mid-session
<!-- proven by test CP8-T06 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: README says a model choice never approves paid use or weakens review
- **GIVEN** the section
- **WHEN** the test searches it
- **THEN** it says choosing a model never approves paid per-use spending (the exact reply `pay per use: <model>` is still needed) and never removes cross-company review or changes how many reviewers look
<!-- proven by test CP8-T07 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: Command table lists the new options
- **GIVEN** the README command table
- **WHEN** the test reads the `ratchet.py models` row and the `ratchet.py config` row
- **THEN** the models row mentions the new read-only options this run added (the requested-versus-real view and any per-role or --json form CP4 shipped) and the config row mentions the level choice (global or project) and the agents key; every option named in those rows is accepted by the real argument parser
<!-- proven by test CP8-T08 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: README does not claim more than the code does
- **GIVEN** the section
- **WHEN** the test checks host wording
- **THEN** it says Codex, Gemini and Copilot hosts cannot pick a model per sub-agent (reported, not faked), and names no flag that `ratchet.py` rejects
<!-- proven by test CP8-T09 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: New test file is listed in the README's Running the tests commands
- **GIVEN** the fenced command list under 'Running the tests'
- **WHEN** the test reads it
- **THEN** the line `python3 tests/test_agents_docs.py` is present exactly once, in alphabetical position among the other test lines, and every other agents test file from CP1 to CP7 that exists in tests/ is listed too
<!-- proven by test CP8-T10 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: Test inventory fixture counts the new file when it scans engine source
- **GIVEN** tests/test_agents_docs.py and tests/fixtures/modularize_cp1/test-inventory.md
- **WHEN** the test checks whether the new file matches the inventory's live pattern (ENGINE.read_text, ast.parse, ast.walk, getsource, spec_from_file_location)
- **THEN** if it matches, test-inventory.md has an entry for test_agents_docs.py saying what it checks; if it does not match, the file is not listed (no stale entry)
<!-- proven by test CP8-T11 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: SKILL.md and schemas.md agree with the README
- **GIVEN** skills/ratchet-orchestrator/SKILL.md and references/schemas.md
- **WHEN** the test searches for the role-model paragraph and the config section
- **THEN** SKILL.md points to the one-line agents setting and the models check, and schemas.md documents the global file location, the layer order (defaults, global, project) and the agents key
<!-- proven by test CP8-T12 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: Existing doc lint still passes and the new text has no em dash
- **GIVEN** the README and skills after the edit
- **WHEN** tests/test_docs.py runs unchanged and the new section is scanned
- **THEN** test_docs.py exits 0 with no new failures, and the new README section contains no em dash
<!-- proven by test CP8-T13 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: The provider and model validity rule is stated once, truthfully, everywhere
- **GIVEN** README.md, both SKILL.md files, schemas.md and the code that enforces the rule
- **WHEN** The text about provider/model choices is read and the rule is exercised
- **THEN** Every place says the same rule: the provider part must name a configured provider or a known company (refused at set-time and at load, with the repair commands always working); the model part cannot be checked offline, so it loads, the view marks an unknown one not verified or not offered, and it is refused the moment real work would launch (brief, launch guard, reviewers, plan-submit, ui_review, lanes-start, config --set); README's opening model-settings paragraph no longer says an unknown model is refused at load (CP7 deferred minor)
<!-- proven by test CP8-T14 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: Small CP7 follow-ups: dead f-string, inherit keyword, company-first resolution
- **GIVEN** skills/ratchet-orchestrator/scripts/ratchet.py after CP7 (director carry-over Oct 31)
- **WHEN** The source is scanned and the orchestrator and director choices are exercised
- **THEN** (1) the lanes-start empty-model note at ratchet.py ~5966 is a plain string, so the count of f-strings with no placeholder in ratchet.py is one lower than at the CP7 commit (pyflakes 39 back to 38); (2) the keyword inherit is exempt from the provider models-list check, so codex/inherit loads and runs with a codex models list and the README sentence about inherit is true; (3) inherit is handled the same in every case and path: bare INHERIT and anthropic/INHERIT behave identically (both refused as unknown names listing the valid ones); (4) a provider's company comes from its preset before its model in resolve_provider_name: github/gpt-5 matches a configured copilot provider and openai/gpt-5 never resolves to a copilot provider listed before codex, and the start command and the resolution agree; (5) a UI lens choice openai/gpt-5 that resolves to a configured provider whose id is not a runner name is either launched on the matching runner or shown plainly as not a runner in models and the plan, never silently dropped
<!-- proven by test CP8-T15 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: CP6 follow-ups: reviewer-status and the quota guard on a chosen model
- **GIVEN** agents.reviewer = provider/model-x; a review fails partway on model-x (mark names model-x); separately an out-of-credits failure on model-x
- **WHEN** reviewer-status and reviewers are read, with the choice kept, then removed
- **THEN** reviewer-status judges the mark the way reviewers do (the reviewer shows as unavailable while the choice that failed is still set, available once it is removed); an out-of-credits failure on a chosen model leaves a mark with no model (a quota wait for the whole provider), pinned by a test
<!-- proven by test CP8-T16 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: Deferred doc-staleness minors from CP1 to CP7 are corrected
- **GIVEN** the deferred list (ratchet.py deferred) for CP1 to CP7
- **WHEN** README.md, schemas.md, SKILL.md files and the engine docstrings named by those minors are read
- **THEN** These statements are true to the code: the README sentence that an agent session can edit .ratchet/config.json (the guard refuses it; config --set is the path) and the Codex bare-name sentence (CP1 A3, CP3 A7); the schemas.md writer list names config --unset (CP2 X1); schemas.md and the reviewer-row docstring say the models reviewer row judges the first slot of a low-risk step (CP5 A4, A7); launch_cfg's docstring no longer calls itself the one application (CP5 A3); global_config's docstring lists the nested shape checks it now makes (CP1 A3); ui_review.role_providers is either used or documented as test-only (CP5 A6)
<!-- proven by test CP8-T17 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->

#### Scenario: A wrong-shaped host_models never crashes a command, in either file, and the repair commands work
- **GIVEN** host_models written with a wrong shape by hand: a bare string per host ({"anthropic": "opus"}), a string for the whole key ("opus"), and a wrong tier value ({"anthropic": {"strong": 5}}), once in the project file and once in the global file (probe Oct 31: today the project file makes config, models, status and next crash with an AttributeError traceback, and `config --global --unset host_models` crashes or is refused whichever file holds the typo)
- **WHEN** config, models, models --json, status and next run, then the repair commands run: config --unset host_models, config --global --unset host_models, config --global --set host_models.anthropic.strong=opus
- **THEN** No command prints a traceback. The wrong shape is refused cleanly at load in BOTH files with one line naming the file, the key and the expected shape (an object of tier names to models, tiers non-empty strings, alternates a list), the same message the global file already gives; the repair commands exit 0 with the wrong shape already present and leave a valid file; a correct host_models in either file still loads exactly as before (director request Oct 31: a wrong SHAPE is offline-knowable so it is refused at load, and the repair commands always work)
<!-- proven by test CP8-T18 in tests/test_agents_docs.py, tests/test_docs.py, commit c979030 -->
