# FF-L fast-follow cleanups for agent provider/model config

## Purpose
What this feature does, as built and checked by Ratchet (run ff-l-fast-follow-cleanups-for-agent-provider-mod, 2026-11-01). Each scenario names the test that proves it.

## Requirements

### Requirement: Honest repair wording and full-id message
The system SHALL do this: Developers and their agents read docs and messages that say exactly what the repair commands do and give one consistent answer about full model ids.

- README, the orchestrator SKILL.md and schemas.md say the repair commands always run, that unsetting the offending key in the file that holds it repairs it, and that a repair aimed at a different file or key leaves the bad value in place
- Setting a role to a misspelled model on an Anthropic host suggests a full id only in a form the host then accepts, and setting a full id there points to that same form instead of only saying 'can't use'
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it reads engine source, so it is counted with the rest

#### Scenario: README says exactly what the repair commands do
- **GIVEN** README.md, the sentence about repair commands before 'The model part can't be checked offline'
- **WHEN** the test reads that sentence (the 700 characters before the anchor)
- **THEN** it says unsetting the offending key in the file that holds it repairs it, and that a repair aimed at a different file or a different key leaves the bad value in place; it no longer claims the repair commands 'always work' with nothing after it. Fails today on: no 'offending key / file that holds it' wording and no 'leaves the bad value in place' wording (the sentence ends '... always work).').
<!-- proven by test CP1-T01 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: The orchestrator SKILL.md says the same thing as the README
- **GIVEN** skills/ratchet-orchestrator/SKILL.md line about the repair commands (the 'Each role has a model' bullet)
- **WHEN** the test reads the span before 'the model part can't be checked offline'
- **THEN** same required statements as CP1-T01 (offending key in the file that holds it repairs it; a different file or key leaves it in place); the bare '(... always work)' claim is gone. Fails today on the same missing wording.
<!-- proven by test CP1-T02 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: schemas.md says the same thing as the README
- **GIVEN** skills/ratchet-orchestrator/references/schemas.md 'Models per role' paragraph
- **WHEN** the test reads the span before 'the model part can't be checked offline'
- **THEN** same required statements as CP1-T01. Fails today on the same missing wording. skills/ratchet-adversarial-loop/SKILL.md is not required to carry it (it has no such claim today); the case also asserts no doc anywhere still carries the unqualified 'repair commands ... always work)' sentence.
<!-- proven by test CP1-T03 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: The new wording still passes the earlier docs rule (a truthful 'always work' phrase, no em dash)
- **GIVEN** the three docs after the rewrite
- **WHEN** the test repeats the CP8-T14 check on its own: within the 700 characters before 'the model part can't be checked offline' find every PROVIDER_PARTS phrase (provider part, configured provider or a known company, set-time, at load, repair commands, always work)
- **THEN** all are still present, and every 'always work' is followed by a scope (for example 'on the file and key they name'), never by ')' or '.' directly; no em dash appears in the rewritten sentence. Nothing edits tests/test_agents_docs.py (git status of that file is clean), so CP8-T14 keeps passing unchanged. Fails today on: 'always work' is followed by ')' in all three docs.
<!-- proven by test CP1-T04 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: What the docs claim matches what the repair commands really do
- **GIVEN** a project file with agents.implementer = "nope/x" (host anthropic), and the same bad value in the machine-wide file in a second project
- **WHEN** the test runs, each in a fresh copy: `config --unset agents.implementer`; `config --set agents.implementer=opus`; `config --unset agents` (the offending key, in its own file); `config --global --unset agents.implementer` and `config --global --set agents.fixer=opus` (the other file); `config --unset agents.fixer` and `config --set agents.fixer=opus` (another key in the same file); then `models` after each
- **THEN** no command prints a traceback. The three offending-key repairs exit 0 and `models` then exits 0. The other-file repairs exit 0 (nothing to remove, or sets the other key) and `models` still refuses, naming the project file and agents.implementer. The other-key-in-the-same-file commands are stopped with exit 1 and the same message naming agents.implementer, and the bad value is still in the file. The README sentence describes exactly those three outcomes (repairs; runs but leaves it; stopped naming the offending key), not 'always run'. Fails today on the README assertion: the outcomes are already true, the sentence does not state them.
<!-- proven by test CP1-T05 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: Refusing a full model id on an Anthropic host points to the form the host accepts
- **GIVEN** an empty project, host anthropic
- **WHEN** `config --set agents.orchestrator=claude-opus-4-1`
- **THEN** exit 1, nothing written to the project file, and the message names the accepted form `anthropic/claude-opus-4-1` (the host company, a slash, the same id the person typed), not only the generic 'or write provider/model'. Fails today on: the message ends 'or write provider/model.' with no concrete form.
<!-- proven by test CP1-T06 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: The misspelling hint no longer offers a bare full id the host then refuses
- **GIVEN** an empty project, host anthropic
- **WHEN** `config --set agents.orchestrator=sonet` (a typo)
- **THEN** exit 1, nothing written, the message still lists haiku, inherit, opus, sonnet, and offers a full id only as provider/model with the concrete example `anthropic/claude-opus-4-1`; it does not say 'or a full model id such as claude-opus-4-1' as if the bare id worked. Fails today on: the hint says '(or a full model id such as claude-opus-4-1, or provider/model)'.
<!-- proven by test CP1-T07 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: Every id either message suggests is really accepted, on both kinds of host
- **GIVEN** an empty project on host anthropic, another on host openai (no model list: a bare full id is accepted there)
- **WHEN** for each host the test triggers the typo hint (`agents.orchestrator=sonet`) and, on anthropic, the full-id refusal; it takes the example id or provider/model form each message names and runs `config --set agents.orchestrator=<that example>` in a fresh project; it also triggers the role_models typo hint (`role_models.ratchet-implementer=sonet`) and runs its example
- **THEN** every example taken from a message exits 0 and is stored. On anthropic the example from the agents typo hint and the one from the refusal are the same form. The role_models hint keeps saying a bare full id works and `role_models.ratchet-implementer=claude-opus-4-1` still exits 0 (that message stays accurate). Fails today on the anthropic agents hint: its example `claude-opus-4-1` is refused with 'can't use'.
<!-- proven by test CP1-T08 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: The function that words these messages describes the same rule
- **GIVEN** the engine source (parsed as text, not run)
- **WHEN** the test reads the docstring of `_check_agent_choices` with ast
- **THEN** it says a full model id is written as provider/model (for example anthropic/claude-opus-4-1) on a host that lists its models, and that a bare full id there is refused with that form named. Fails today on: the docstring never mentions a full id or that form. Because this case parses ratchet.py's text, tests/test_ffl_wording.py must be listed in tests/fixtures/modularize_cp1/test-inventory.md (see CP1-T10).
<!-- proven by test CP1-T09 in tests/test_ffl_wording.py, commit 011276a -->

#### Scenario: The new test file is listed in the README's test list and, as it reads engine source, in the inventory
- **GIVEN** README.md 'Running the tests' list, tests/fixtures/modularize_cp1/test-inventory.md, and the new file's own text
- **WHEN** the test looks for the line `python3 tests/test_ffl_wording.py` in the README list, and recomputes live (as CP8-T11 does, with the patterns assembled from pieces so it never matches itself) whether this file loads the engine by spec or reads engine source
- **THEN** the README list holds the line; the inventory names test_ffl_wording.py exactly when the file scans (it does, via CP1-T09), with an entry longer than 80 characters that mentions README and ratchet.py. Fails today on: the README list has no such line and the inventory has no entry.
<!-- proven by test CP1-T10 in tests/test_ffl_wording.py, commit 011276a -->

### Requirement: Role rows name the real default
The system SHALL do this: A developer's agent reading `models` sees the shipped default a role really runs on, and a misspelled role_models value shown as invalid with a reason.

- For a role nothing sets, `models` names the shipped default it runs on (the tier and its model) instead of a bare inherit with no explanation
- A misspelled role_models value is shown as invalid with a plain reason, never as the model in use, including when it sits under an agents choice that can't apply on this host
- What really launches is unchanged: the earlier defaults-parity tests still pass
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it reads engine source, so it is counted with the rest

#### Scenario: A misspelled role_models value below a choice that cannot apply is shown as invalid, not as the model in use
- **GIVEN** Anthropic host; agents.test-writer = openai/gpt-5 in the project file (a sub-agent on an Anthropic host cannot use another company's provider, so it cannot apply); role_models.ratchet-test-writer = sonet in the machine-wide file
- **WHEN** the developer's agent runs `models --json`
- **THEN** the ratchet-test-writer detail row has requested openai/gpt-5, effective sonnet (the shipped standard-tier model, never sonet), and a reason that names sonet as invalid or unknown with the valid names, as well as saying the openai/gpt-5 choice cannot apply on this host
<!-- proven by test CP2-T01 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: The plain `models` text and the warning note never present that typo as the model a role runs on
- **GIVEN** The same settings as CP2-T01
- **WHEN** the developer's agent runs plain `models` and reads its output and its stderr notes
- **THEN** no line says the role uses sonet; the 'Spawn each role's sub-agent with this model' row for ratchet-test-writer is not sonet; the 'asked ...; uses ...' line says it uses sonnet; the RATCHET note for agents.test-writer does not say the role keeps the model sonet; the text calls sonet invalid or unknown; the command exits 0 and prints no traceback
<!-- proven by test CP2-T02 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: The same holds for a role whose shipped default is its own session's model
- **GIVEN** Anthropic host; agents.implementer = openai/gpt-5 in the project file; role_models.ratchet-implementer = opsu in the machine-wide file
- **WHEN** `models --json` is read
- **THEN** ratchet-implementer shows effective inherit (the shipped strong tier on this host, 'your session's model' in plain text), never opsu, and the reason names opsu as invalid
<!-- proven by test CP2-T03 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: A directly misspelled role_models value is not shown as a spawn model in the plain `models` table, in either settings file
- **GIVEN** Anthropic host; role_models.ratchet-test-writer = sonet, once only in the project file and once only in the machine-wide file (two separate worlds)
- **WHEN** plain `models` is run in each world
- **THEN** the 'Spawn each role's sub-agent with this model' row for ratchet-test-writer neither equals nor ends in sonet (it shows the model the role really keeps, or marks the value invalid); the asked/uses line still says asked sonet and uses sonnet with an invalid-or-unknown reason; the other roles' rows are unchanged; exit 0; neither file changed on disk. The old JSON `roles` field is deliberately not pinned here (see the open question).
<!-- proven by test CP2-T04 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: a directly misspelled role_models value already shows as invalid in the detail row
- **GIVEN** Anthropic host; role_models.ratchet-implementer = opsu in the project file, and separately in the machine-wide file
- **WHEN** `models --json` is read
- **THEN** the detail row has requested opsu, effective inherit, source project (or global), and a reason that names opsu as invalid or unknown with the valid names and says brief and the launch guard refuse it
<!-- proven by test CP2-T05 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: with nothing set, every role names the shipped tier and the model it runs on
- **GIVEN** Anthropic host; no setting in either file
- **WHEN** `models --json` and plain `models` are read
- **THEN** for each of the 13 sub-agent roles, requested is its DEFAULT_CONFIG role_models tier (strong for implementer, fixer, planner, adversary, visual reviewer; standard for test-writer, test-planner, app-scout, behavior reviewer, design system, prototyper, variant writer; light for test runner), effective is the recorded model (inherit, sonnet or haiku), source is builtin, reason is empty; the plain line reads 'asked <tier>; uses <model or your session's model>; from builtin'; the `roles` JSON map and the spawn-table text equal the recorded pre-change values exactly (OLD_ROLES and OLD_HEADER in tests/test_models_explain.py)
<!-- proven by test CP2-T06 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: on a host that cannot choose a model per sub-agent, an unset role still names its tier and says why it runs on the session's model
- **GIVEN** The host set to openai through the host environment variable (no per-sub-agent model list); no settings
- **WHEN** `models --json` is read
- **THEN** ratchet-implementer shows requested strong, effective inherit, empty reason; ratchet-test-writer shows requested standard, effective inherit and a reason saying the host (openai) cannot choose a model per sub-agent; the roles map is all inherit
<!-- proven by test CP2-T07 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: a choice that cannot apply, with nothing below it, falls to the shipped default and is not called invalid
- **GIVEN** Anthropic host; agents.test-writer = openai/gpt-5 in the project file; no role_models value anywhere
- **WHEN** `models --json` is read
- **THEN** ratchet-test-writer has requested openai/gpt-5, effective sonnet, a reason saying a sub-agent can't use another company's provider, and no 'invalid' or 'unknown' wording
<!-- proven by test CP2-T08 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: a choice that cannot apply, over a valid role_models tier, runs on that tier's model
- **GIVEN** Anthropic host; agents.test-writer = openai/gpt-5 in the project file; role_models.ratchet-test-writer = light in the machine-wide file
- **WHEN** `models --json` is read
- **THEN** ratchet-test-writer effective is haiku, the reason is about the agents choice only, and there is no 'invalid' wording
<!-- proven by test CP2-T09 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: a full model id in role_models is never called invalid, directly or under a choice that cannot apply
- **GIVEN** Anthropic host; role_models.ratchet-test-writer = claude-opus-4-1, once alone in the project file, and once in the machine-wide file below the cannot-apply agents choice (project agents.test-writer = openai/gpt-5)
- **WHEN** `models --json` is read in each world
- **THEN** effective is claude-opus-4-1 as written in both, there is no 'invalid' wording, and the direct case keeps its 'passed on as written and the host may refuse it' reason
<!-- proven by test CP2-T10 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: a valid project value over a machine-wide typo is shown as the value in use
- **GIVEN** Anthropic host; machine-wide role_models.ratchet-test-writer = sonet; project role_models.ratchet-test-writer = light
- **WHEN** `models --json` and plain `models` are read
- **THEN** ratchet-test-writer has requested light, effective haiku, source project, an empty reason, and nothing marks sonet invalid for this role
<!-- proven by test CP2-T11 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: what really launches is unchanged, loading stays tolerant, and the repair command still works
- **GIVEN** The same settings as CP2-T01, in a throwaway project with an approved plan
- **WHEN** `config`, `status`, `models` and `brief CP1 --role test-writer` are run, then `config --global --unset role_models.ratchet-test-writer`, then `models --json` again
- **THEN** config, status and models exit 0 with no traceback and change no file; brief refuses, naming the machine-wide file, role_models.ratchet-test-writer, sonet and the valid names, and prints no `Launch with model: sonet`; after the unset, models shows ratchet-test-writer effective sonnet and the invalid wording is gone; with a valid choice (role_models.ratchet-test-writer = light) brief still prints exactly one `Launch with model: haiku` line
<!-- proven by test CP2-T12 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: Guard: a valid role_models tier is shown as in use in the detail row and the spawn table
- **GIVEN** Anthropic host; role_models.ratchet-implementer = light in the project file
- **WHEN** `models --json` and plain `models` are read
- **THEN** implementer has requested light, effective haiku, source project, an empty reason; the spawn row reads haiku; the other 12 rows equal the defaults
<!-- proven by test CP2-T13 in tests/test_ffl_role_report.py, commit 797db58 -->

#### Scenario: The new test file is registered in the README test list and the inventory stays consistent
- **GIVEN** the finished change
- **WHEN** README.md 'Running the tests' and tests/fixtures/modularize_cp1/test-inventory.md are read
- **THEN** README lists the line 'python3 tests/test_ffl_role_report.py'; if the test file reads engine .py source as text, test-inventory.md names it, otherwise no inventory entry is required; the `models` description in README, the orchestrator SKILL.md and schemas.md matches any changed text
<!-- proven by test CP2-T14 in tests/test_ffl_role_report.py, commit 797db58 -->

### Requirement: Wrong provider fields refused, never crash
The system SHALL do this: A developer whose project settings hold a wrongly shaped provider field gets one plain line naming the file and field, no crash in any command, and a repair that still works.

- With a wrongly shaped provider field (for_risk, prefer_for, stdin, command, model, variant) in either settings file, every command either works or stops with one line naming the file, the field and the shape it needs, never a traceback
- The repair command (`config --unset providers`, or the --global form for the machine-wide file) still runs and fixes it while the bad field is there
- Settings that were already valid load exactly as before
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it reads engine source, so it is counted with the rest

#### Scenario: A project's for_risk written as a plain word or number is refused in one line, not a crash
- **GIVEN** A project whose project settings file holds providers=[{id:'x',preset:'codex',for_risk:5}] (and, separately, for_risk:'high'), an empty machine-wide file, and an active run
- **WHEN** The developer's agent runs `status`, `models`, `models --json`, `providers`, `config`, `config --sources` and `config --scope project`, each in-process
- **THEN** Every one of them gives exactly one non-empty line of output, naming the settings file's full path, the field (providers[0].<field>) and the shape it needs, a non-zero exit code, and no 'Traceback' anywhere in stdout or stderr (the shape named is 'a list of risk names (low, normal, high)'); the project file is byte-identical afterwards
<!-- proven by test CP3-T01 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: Same refusal on the reviewer path that used to crash on for_risk
- **GIVEN** A project with one checkpoint approved into building at risk 'high' (approved_project-style fixture, no real CLIs installed) whose providers=[{id:'x',preset:'codex',for_risk:5}] is written to the project file after approval
- **WHEN** `reviewers CP1`, `reviewers CP1 --json` and `brief CP1 --role implementer` are run
- **THEN** Each gives exactly one non-empty line of output, naming the settings file's full path, the field (providers[0].<field>) and the shape it needs, a non-zero exit code, and no 'Traceback' anywhere in stdout or stderr; none reaches the reviewer offer (no 'not set up for' line, no TypeError text)
<!-- proven by test CP3-T02 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: Every provider field has its own refusal in the project file
- **GIVEN** One project per case, providers=[{id:'x',preset:'codex', <field>: <wrong value>}] with the wrong values: command 'ls', command [], command [''], model 1, model '', company 1, cost 1, variant 1, stdin 'yes', stdin 1, for_risk 'high', for_risk ['urgent'], for_risk [5], for_risk [[]], prefer_for 'both', prefer_for ['nope'], prefer_for [['a']], preset 5, id 5
- **WHEN** `status` is run on each
- **THEN** Each gives exactly one non-empty line of output, naming the settings file's full path, the field (providers[0].<field>) and the shape it needs, a non-zero exit code, and no 'Traceback' anywhere in stdout or stderr (for id and preset the line names providers[0] / providers[0].preset); nothing writes to the file
<!-- proven by test CP3-T03 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: Whole-entry mistakes in the project file are refused the same way
- **GIVEN** Project files holding: providers as a string, providers as an object, providers=[5], an entry with no id, two entries with the same id, an entry with an unknown preset, an entry with neither preset nor command
- **WHEN** `status` and `models` are run on each
- **THEN** Each gives exactly one non-empty line of output, naming the settings file's full path, the field (providers[0].<field>) and the shape it needs, a non-zero exit code, and no 'Traceback' anywhere in stdout or stderr (naming providers or providers[i], as the rule does for the machine-wide file)
<!-- proven by test CP3-T04 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: The same wrong field in the machine-wide file gives the same one line
- **GIVEN** No project providers; the machine-wide file holds the same wrong-shaped entries as T01 and T03 (for_risk, prefer_for, stdin, command, model, variant)
- **WHEN** `status`, `models` and `config` are run
- **THEN** Each gives the same refusal as T01 but naming the machine-wide file's path; the text is identical to the project-file case apart from the path; still no traceback (guards that the already-validated global path keeps working)
<!-- proven by test CP3-T05 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: `config --unset providers` repairs a wrongly shaped project providers field
- **GIVEN** A project file holding providers=[{id:'x',preset:'codex',for_risk:5}] plus an unrelated related_tests_max=4, and a valid machine-wide file
- **WHEN** The agent runs `config --unset providers`, then `status`, `models` and `config`
- **THEN** The unset exits 0 and prints a Removed line naming the project file; related_tests_max=4 is still there; the three follow-up commands exit 0 with no refusal; the file no longer has a providers key
<!-- proven by test CP3-T06 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: `config --global --unset providers` repairs a wrongly shaped machine-wide providers field
- **GIVEN** The machine-wide file holds providers=[{id:'x',preset:'codex',for_risk:5}] and role_models {ratchet-fixer:'light'}; the project file is valid
- **WHEN** The agent runs `config --global --unset providers`, then `status` and `config`
- **THEN** The unset exits 0 (today the same shape refusal stops it at the start) and the global file keeps role_models but has no providers; the follow-up commands exit 0
<!-- proven by test CP3-T07 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: A bad providers field in the file not being written never blocks the repair of the other file
- **GIVEN** (a) project file has providers for_risk:5, machine-wide file valid; (b) machine-wide file has providers for_risk:5, project file valid
- **WHEN** (a) `config --global --set role_models.ratchet-fixer=light`; (b) `config --set related_tests_max=5`
- **THEN** Both exit 0 and write only the file they were aimed at; the other file is byte-identical and its bad value is still reported by a following `status`, so nothing is silently fixed or lost (OPEN: whether (b), a project write while the machine-wide file is bad, is meant to be set aside like host_models; if not, (b) must be a one-line refusal naming the machine-wide file and leave both files unchanged)
<!-- proven by test CP3-T08 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: A repair aimed at another key of the same file does not fix it
- **GIVEN** Project file holds providers for_risk:5 and related_tests_max=4
- **WHEN** `config --unset related_tests_max` is run, and then `config --set related_tests_max=9`
- **THEN** Both are refused with the one-line refusal naming the project file and providers[0].for_risk (no traceback), the file is byte-identical, and no Removed/Set line is printed; `config --unset providers` afterwards still works
<!-- proven by test CP3-T09 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: `providers --remove` on a project with a bad providers field fails plainly or repairs
- **GIVEN** Project file holds providers=[{id:'x',preset:'codex',for_risk:5}]
- **WHEN** `providers --remove x` is run
- **THEN** No traceback. Either it exits 0 and a following `status` works with the file no longer holding that entry, or it exits non-zero with the one-line refusal naming the file and field and leaves the file byte-identical (OPEN: which of the two; the notes say it 'should also still run')
<!-- proven by test CP3-T10 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: Valid providers settings load exactly as before
- **GIVEN** A project and a machine-wide file with fully valid entries: preset-only, command with stdin, model/company/cost/variant strings, for_risk [low,high], prefer_for [correctness], a project entry with paid_ok_at, an extra unknown field note:5, and an empty providers list
- **WHEN** `config`, `config --scope project` and `models --json` are run, with the old_merge helper's pre-change merge as the expected value
- **THEN** All exit 0, the output equals the unchanged expectation key by key (providers returned as written, extras kept), and paid_ok_at in the project is accepted (it is refused only in the machine-wide file)
<!-- proven by test CP3-T11 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: Structural check: every provider field the engine reads has a refusal in both files
- **GIVEN** An AST scan of every skills/*/scripts/*.py for constant keys read or written on provider entries (pv/prov subscripts and get/pop/setdefault, the scan test_config_layers x19 uses), a table of one wrong value per field, and an exempt list that needs a written reason (launch_model; paid_ok_at in the project file)
- **WHEN** For each scanned field the wrong value is written into the project file, then into the machine-wide file, and `status` is run
- **THEN** The scan finds at least id, preset, command, model, stdin, for_risk, prefer_for; a field in neither the table nor the exempt list fails (so a new provider field cannot be added unchecked); each field gives exactly one non-empty line of output, naming the settings file's full path, the field (providers[0].<field>) and the shape it needs, a non-zero exit code, and no 'Traceback' anywhere in stdout or stderr for both files
<!-- proven by test CP3-T12 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: Structural check: no command prints a traceback while the project providers field is wrong
- **GIVEN** A project with a run and one checkpoint, providers for_risk:5 in the project file, and the list of every sub-command the engine's own parser registers (minus a written exempt list, each with a reason, of commands that need a real agent CLI or network)
- **WHEN** Each read-only or status-like command is run in-process with its minimal arguments; the repair command is run last
- **THEN** No command's output contains 'Traceback'; each exits 0 or gives the one-line refusal naming the project file and the field; an exempt command has a reason string
<!-- proven by test CP3-T13 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: The README lists the new test file
- **GIVEN** README.md 'Running the tests' list
- **WHEN** The list is read
- **THEN** It contains `python3 tests/test_ffl_provider_shape.py`
<!-- proven by test CP3-T14 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: The test inventory lists the new file because it reads engine source
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md and the new test file
- **WHEN** The new file is scanned for engine-source reads (ast scan, spec_from_file_location, reading the engine script)
- **THEN** If it reads engine source it is listed in the inventory with its purpose; if it does not, no entry is needed
<!-- proven by test CP3-T15 in tests/test_ffl_provider_shape.py, commit a46782f -->

#### Scenario: The docs state that provider fields in either settings file are checked when loaded, and how to repair
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and references/schemas.md section 8
- **WHEN** The providers rule text is read in each
- **THEN** Each says a wrong-shaped providers field in either settings file is refused with one line naming the file and field, and that `config --unset providers` (or `--global --unset providers`) repairs it; none still says only the machine-wide file is shape-checked at load
<!-- proven by test CP3-T16 in tests/test_ffl_provider_shape.py, commit a46782f -->

### Requirement: Models view agrees with the reviewer pick
The system SHALL do this: A developer's agent reading `models` never sees 'no provider of that company is set up' while the reviewer pick would run that company's model.

- With a Copilot provider running a gpt model and the reviewer set to openai, `models` and the reviewers note no longer claim nothing of that company exists: they name that provider, say why it was not chosen by the company name, and how to choose it
- Which provider launches, and how cross-company review is judged, stays exactly as before
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it reads engine source, so it is counted with the rest

#### Scenario: The models view names the Copilot provider instead of saying no openai provider exists
- **GIVEN** A project whose only provider is a Copilot provider (id copilot, preset copilot, model gpt-5), agents.reviewer = 'openai', a Claude host, stub CLIs for codex/copilot on PATH
- **WHEN** The developer runs `models --json` and reads the reviewer row
- **THEN** The row's reason no longer contains "no provider of the company 'openai' is set up"; it names the 'copilot' provider (and its model gpt-5), says it answers to its tool's company name (github) so the name 'openai' did not choose it, and says how to choose it (`copilot` or `copilot/gpt-5`). The row's requested stays 'openai' and effective stays the automatic-pick wording
<!-- proven by test CP4-T01 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: A company/model request (openai/gpt-5) gets the same honest explanation
- **GIVEN** The same project with agents.reviewer = 'openai/gpt-5'
- **WHEN** The developer runs `models --json`
- **THEN** The reviewer row names the 'copilot' provider and suggests `copilot/gpt-5` as the way to choose it; it never says no provider of that company is set up
<!-- proven by test CP4-T02 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: The round-opening reviewers note says the same thing as the models view
- **GIVEN** An approved project taken to the review step (helper: approved_world) with a Copilot provider (id copilot, preset copilot, model gpt-5) as the only provider and agents.reviewer = 'openai'
- **WHEN** The agent opens the round with `reviewers CP1 --json` and reads the RATCHET note on its streams
- **THEN** The note about agents.reviewer = 'openai' names the 'copilot' provider, says why the company name did not choose it and how to choose it (`copilot` or `copilot/gpt-5`), and no longer claims no provider of the company is set up; the round still lists exactly copilot-both on model gpt-5
<!-- proven by test CP4-T03 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: The plain-text models view carries the same lines
- **GIVEN** The case-1 project
- **WHEN** The developer runs `models` (no --json)
- **THEN** The 'reviewer' row's why: line under it names copilot and how to choose it, and contains no 'no provider of the company' claim; the thirteen role rows above it are unchanged
<!-- proven by test CP4-T04 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: Every provider of that company is named, in config order
- **GIVEN** Two providers on gpt models listed in this order: Copilot (id copilot, gpt-5) then OpenCode (id opencode, preset opencode, model gpt-5-codex or another openai model), agents.reviewer = 'openai'
- **WHEN** `models --json` and the opening `reviewers CP1` note are read
- **THEN** Both the row's reason and the note name copilot before opencode (config order) with their own choose-it forms; neither claims nothing of that company exists
<!-- proven by test CP4-T05 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: With no provider of that company at all, the old message and add-hint stay
- **GIVEN** (a) no providers; (b) only a Copilot provider with no model; (c) only a Copilot provider on a claude model, with a codex-less project; agents.reviewer = 'openai'
- **WHEN** `models --json` and the opening `reviewers` note are read for each
- **THEN** Each still says "no provider of the company 'openai' is set up in this project" and, in the note, the `providers --add codex --preset codex` hint; copilot is never named as an openai provider
<!-- proven by test CP4-T06 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: A Copilot provider on a gpt model is not offered for a different company
- **GIVEN** a Copilot provider (id copilot, preset copilot, model gpt-5), agents.reviewer = 'google'
- **WHEN** `models --json` and the opening `reviewers` note are read
- **THEN** Both keep today's 'no provider of the company google is set up' text and do not name copilot
<!-- proven by test CP4-T07 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: A real openai provider listed after Copilot still wins the company name, with no 'not chosen' text
- **GIVEN** Providers in this order: Copilot (gpt-5) then Codex (preset codex), agents.reviewer = 'openai/gpt-5'
- **WHEN** `models --json`, `resolve_reviewers` and the opening `reviewers` call run
- **THEN** The pick is codex-both on gpt-5 (as before), the reviewer row names Codex and carries no reason about copilot, and the note section has no 'not applied' line
<!-- proven by test CP4-T08 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: The way the view says to choose the Copilot provider really works
- **GIVEN** a Copilot provider (id copilot, preset copilot, model gpt-5); run three times with agents.reviewer = 'copilot', then 'github', then 'copilot/gpt-5'
- **WHEN** `models --json` and the resolved reviewers are read for each
- **THEN** Each row has an empty reason and names Copilot, gpt-5 as the effective reviewer; the pick is copilot-both with model gpt-5 each time. The exact form the openai message suggests is therefore accepted
<!-- proven by test CP4-T09 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: Which provider launches does not change for any company-name request
- **GIVEN** A matrix of (agents.reviewer, providers): ('openai', [copilot gpt-5]) -> copilot-both on gpt-5 by the automatic pick; ('github', [copilot gpt-5]) -> copilot-both; ('openai/gpt-5', [copilot, codex]) -> codex-both; ('codex', [copilot, codex]) -> codex-both; ('openai', []) -> the host's own sub-agent
- **WHEN** resolve_reviewers is run on each, and `reviewers --json` for the first one
- **THEN** The reviewer ids, slots, models and the 'vendor' field of every reviewer are exactly these expected literals (copilot-both reports vendor 'openai')
<!-- proven by test CP4-T10 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: Cross-company review is judged by the model's company, exactly as before
- **GIVEN** On an Anthropic host: a Copilot provider on claude-sonnet-4 plus a Codex provider, no agents setting. On an OpenAI host (RATCHET_HOST_PROVIDER=openai): a Copilot provider on gpt-5 plus a Gemini provider
- **WHEN** resolve_reviewers runs at low and normal risk
- **THEN** The Copilot provider is set aside as 'same company as the host' on both hosts and the other company's provider reviews instead (codex-both on the first, gemini on the second); at normal risk no slot is a copilot reviewer
<!-- proven by test CP4-T11 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: A Copilot provider on a Claude model, asked for as 'anthropic', is named but still not independent
- **GIVEN** An Anthropic host, a Copilot provider on claude-sonnet-4 and a Codex provider, agents.reviewer = 'anthropic'
- **WHEN** `models --json` and the opening `reviewers` note are read
- **THEN** The text does not claim no provider of the company anthropic is set up; it names copilot and says it is the same company as the host, so it is never an independent reviewer; the pick is unchanged (codex-both)
<!-- proven by test CP4-T12 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: Orchestrator start-command resolution is unchanged by the wording fix
- **GIVEN** a Copilot provider (id copilot, preset copilot, model gpt-5) and agents.orchestrator = 'openai/gpt-5'
- **WHEN** `models` is read
- **THEN** The orchestrator row still gives the start command `codex -m gpt-5` (the company name still means codex, never copilot); the reviewer row for that project is unchanged ('automatic')
<!-- proven by test CP4-T13 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: The view stays read-only and its JSON shape is unchanged
- **GIVEN** The case-1 project with fake agent CLIs (copilot, codex, claude, gemini) that log if run
- **WHEN** `models --json` runs
- **THEN** No fake CLI ran, no file in the project or home changed, the top-level keys stay host/roles/alternates/detail and every row still has exactly requested/effective/source/reason
<!-- proven by test CP4-T14 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: The docs describe the company-name rule the view now follows
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and references/schemas.md (the 'company name for an outside reviewer' sentence)
- **WHEN** Each doc's text around that sentence is read
- **THEN** Each says a provider answers to its tool's company name (github for copilot), so a Copilot provider on a gpt model is chosen by its id (`copilot` or `copilot/gpt-5`) rather than by openai, that `reviewers` and `models` name it, and that cross-company review is still judged by the model's company; no new em dash is added
<!-- proven by test CP4-T15 in tests/test_ffl_company_view.py, commit de80267 -->

#### Scenario: The new test is listed in the README and the test inventory
- **GIVEN** README.md 'Running the tests' list and tests/fixtures/modularize_cp1/test-inventory.md
- **WHEN** The lists are read, and the live inventory greps (module-load and source-text patterns) are run over tests/
- **THEN** README contains `python3 tests/test_ffl_company_view.py`; the inventory names tests/test_ffl_company_view.py if and only if the live grep matches it, and its counts still agree with the live grep
<!-- proven by test CP4-T16 in tests/test_ffl_company_view.py, commit de80267 -->

### Requirement: Remember a chosen model's passed test
The system SHALL do this: A developer whose reviewer runs a chosen model no longer pays for a fresh test request every time a review round opens while the last pass is still fresh.

- After a provider on a chosen model passes its test request, the next round-opening `reviewers` call (from this project or another) sends no new test request until the pass is older than probe_ttl_minutes
- A failed test of a chosen model still only marks that provider unavailable in this project, and the provider's own model record is never marked tested by it
- schemas.md documents the new record entry
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it reads engine source, so it is counted with the rest

#### Scenario: A passed test of the chosen model is remembered and reused by the next round-opening call
- **GIVEN** agents.reviewer = codex/gpt-5.1; the first project's reviewers CP1 call opened its round and sent exactly one test request on gpt-5.1, which passed
- **WHEN** a second project on the same machine (same RATCHET_HOME, same providers, same agents.reviewer) opens its round with reviewers CP1 --json
- **THEN** the stub saw no further test request (still one in total, on gpt-5.1); the shared provider-health.json holds an entry codex:gpt-5.1 with ok true, an `at` time from when the request was sent and a detail; the second project's round still lists the codex reviewer
<!-- proven by test CP5-T01 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: The same project's next round does not re-send the test request while the pass is fresh
- **GIVEN** the T01 first call passed on gpt-5.1; the round is reopened in the same project (a new round opens, for example the code changed before any review was written, as tests/test_fix_round.py does)
- **WHEN** reviewers CP1 --json opens round 2
- **THEN** round 2 is open and reports the chosen model; the stub's test-request count is still 1
<!-- proven by test CP5-T02 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A pass older than probe_ttl_minutes is tested again
- **GIVEN** the shared record holds a passed codex:gpt-5.1 entry whose `at` is older than probe_ttl_minutes (the test sets probe_ttl_minutes=10 and ages `at` by 11 minutes)
- **WHEN** a round-opening reviewers call runs
- **THEN** exactly one new test request on gpt-5.1 reaches the stub; the entry's `at` moves forward and ok stays true
<!-- proven by test CP5-T03 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: The pass stays trusted just inside the window
- **GIVEN** the same entry aged to just under probe_ttl_minutes (9 minutes with a 10 minute setting)
- **WHEN** a round-opening reviewers call runs
- **THEN** no test request is sent
<!-- proven by test CP5-T04 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: probe_ttl_minutes=0 still tests every round
- **GIVEN** probe_ttl_minutes=0 in the project; a fresh pass is recorded
- **WHEN** two round-opening reviewers calls run
- **THEN** each sends its own test request (the stub count rises with every call), as a provider on its own model does
<!-- proven by test CP5-T05 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A failed test of a chosen model is not recorded as a pass
- **GIVEN** agents.reviewer = codex/badmodel; the stub fails any request that names badmodel (as FAIL_BAD_STUB in tests/test_agents_reviewers.py)
- **WHEN** the first reviewers CP1 call opens its round (the test request fails)
- **THEN** provider-health.json has no entry with ok true for codex:badmodel and no entry at all for codex or codex:gpt-5 (own model); this project's unavailable-reviewers.json still marks the codex reviewers for badmodel only; the round falls back to gemini as today
<!-- proven by test CP5-T06 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A failure in one project does not stop another project's fresh pass
- **GIVEN** project A chose codex/badmodel and failed; project B (same RATCHET_HOME) chose codex/gpt-5.1 and its test passed
- **WHEN** project A is shown again and project B opens another round
- **THEN** B sends no new request; the shared record has an ok entry only for codex:gpt-5.1; A's mark names badmodel only and B has no mark
<!-- proven by test CP5-T07 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A chosen model's pass never marks the provider's own model as tested
- **GIVEN** agents.reviewer = codex/gpt-5.1 passed; the provider's own model is gpt-5; nothing else is in the shared record
- **WHEN** another project on the same machine with no agents setting opens a round on codex
- **THEN** that project sends its own test request on -m gpt-5 (the gpt-5.1 pass does not cover it); the record after the first project's call has no codex:gpt-5 (or bare codex) entry with ok or at
<!-- proven by test CP5-T08 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A fresh pass of the provider's own model does not cover the chosen model
- **GIVEN** the shared record holds a fresh ok entry for the provider's own model (codex:gpt-5, written by a project with no setting) and none for gpt-5.1
- **WHEN** a project with agents.reviewer = codex/gpt-5.1 opens its round
- **THEN** one test request on gpt-5.1 is sent and recorded under codex:gpt-5.1; the codex:gpt-5 entry's ok and at are unchanged
<!-- proven by test CP5-T09 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A pass for one chosen model does not cover a different model on the same provider
- **GIVEN** project A passed codex/gpt-5.1 (fresh entry)
- **WHEN** project B (same machine) chooses codex/gpt-5.2 and opens a round; then A opens another round
- **THEN** B sends one test request on gpt-5.2 and the record has both codex:gpt-5.1 and codex:gpt-5.2 with ok true; A's second round sends nothing more
<!-- proven by test CP5-T10 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: One reviewers call still tests a chosen provider only once
- **GIVEN** a normal-risk step (two slots) where the chosen codex leads both slots
- **WHEN** one reviewers call opens the round
- **THEN** exactly one test request on the chosen model, not one per slot
<!-- proven by test CP5-T11 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A pinned reviewer still uses its own model and the existing shared-record behaviour
- **GIVEN** agents.reviewer = codex/gpt-5.1 but the step pins reviewers [codex-both], so the written model gpt-5 runs
- **WHEN** two round-opening reviewers calls run
- **THEN** the test requests name gpt-5 (never gpt-5.1) and are sent once while fresh, as today; the record holds codex:gpt-5 and no codex:gpt-5.1 entry
<!-- proven by test CP5-T12 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: probe_providers=false sends and records nothing
- **GIVEN** probe_providers=false in the project, agents.reviewer = codex/gpt-5.1
- **WHEN** a round-opening reviewers call runs
- **THEN** the stub saw no test request and provider-health.json has no codex:gpt-5.1 ok entry; the round still opens
<!-- proven by test CP5-T13 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A damaged or undated shared entry is treated as not fresh, never a crash
- **GIVEN** the shared record's codex:gpt-5.1 entry is {"ok": true, "at": "garbage"} (and, in a second run, is a plain string)
- **WHEN** a round-opening reviewers call runs
- **THEN** the command exits 0 with no traceback, sends one test request, and the entry now holds a real ok/at
<!-- proven by test CP5-T14 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: A recorded pass is dated when the request was sent
- **GIVEN** the first call of T01 with the clock read before and after it
- **WHEN** the record is read
- **THEN** the entry's `at` lies between the two readings and parses as a time with a zone (the shape provider_health.record_probe writes)
<!-- proven by test CP5-T15 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: schemas.md documents the new shared-record entry
- **GIVEN** skills/ratchet-orchestrator/references/schemas.md
- **WHEN** the paragraph on the shared provider-health record and chosen models is read
- **THEN** it says a passed test request of the model agents.reviewer chose is kept in the shared record under the provider's tool plus that model, is reused by every project for probe_ttl_minutes, never marks the provider's own model, and a failure stays a project mark; the old wording that a chosen model's test is kept in its project only is gone
<!-- proven by test CP5-T16 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: README and the two SKILL.md files say the same about chosen-model tests
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md, skills/ratchet-adversarial-loop/SKILL.md
- **WHEN** the lines that describe testing a chosen model are read
- **THEN** none still says the test is sent at every round or kept only in the project; each agrees with schemas.md
<!-- proven by test CP5-T17 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: README 'Running the tests' lists the new file exactly once
- **GIVEN** README.md
- **WHEN** the block after '### Running the tests' is read
- **THEN** the line `python3 tests/test_ffl_probe_memory.py` appears exactly once
<!-- proven by test CP5-T18 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

#### Scenario: The test inventory registers the new file when it reads engine source
- **GIVEN** tests/fixtures/modularize_cp1/test-inventory.md and this file's own source
- **WHEN** they are compared as tests/test_agents_reviewers.py's T26 does
- **THEN** test_ffl_probe_memory.py is listed in the inventory exactly when the file loads a module by path (it does), and the Kind 1 heading's count equals the entries under it
<!-- proven by test CP5-T19 in tests/test_ffl_probe_memory.py, commit 1f8e6f3 -->

### Requirement: Old copied settings stop hiding machine-wide ones
The system SHALL do this: A developer with an older project sees their machine-wide host_provider and ui_reviewers take effect, while any value they really set in the project still wins.

- In a project whose file still holds the old setup's copied host_provider and ui_reviewers (the shipped defaults), the machine-wide values take effect and `config --sources` says they come from global
- A project value that differs from the shipped default still wins, the project file on disk is never rewritten, and running any command twice gives the same result
- The README, orchestrator SKILL.md and schemas.md say how old copies are treated and name the one case it cannot tell apart
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it reads engine source, so it is counted with the rest

#### Scenario: An old project's copied host provider no longer hides the machine-wide one
- **GIVEN** A project whose .ratchet/config.json holds host_provider 'auto' (the old init copy) and a machine-wide config.json with host_provider 'codex'
- **WHEN** The developer runs `config` and `config --sources`
- **THEN** The effective host_provider is 'codex' and --sources reports host_provider as 'global', not 'project'
<!-- proven by test CP6-T01 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: An old project's copied review-lens lists no longer hide the machine-wide ones
- **GIVEN** A project file holding the shipped ui_reviewers exactly (visual gemini,claude,codex; behavior claude,gemini,codex) and a machine-wide ui_reviewers {visual:[codex], behavior:[codex]}
- **WHEN** The developer runs `config` and `config --sources`
- **THEN** The effective ui_reviewers.visual and .behavior are ['codex'], and --sources says ui_reviewers comes from global
<!-- proven by test CP6-T02 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: The models view shows the machine-wide lens order and names global as its source
- **GIVEN** The same old project (copied ui_reviewers default) with a machine-wide ui_reviewers {visual:[codex]}
- **WHEN** The developer runs `models --json`
- **THEN** The ui-reviewer-visual row lists 'codex' as the requested order and its source is global, not project or builtin
<!-- proven by test CP6-T03 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: A host provider the developer really set in the project still wins
- **GIVEN** A project file with host_provider 'google' (not the shipped default) and a machine-wide host_provider 'codex'
- **WHEN** The developer runs `config` and `config --sources`
- **THEN** The effective host_provider is 'google' and --sources says project
<!-- proven by test CP6-T04 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: A lens list the developer really changed in the project is kept
- **GIVEN** A project file with ui_reviewers {visual:[claude], behavior:[claude,gemini,codex]} (one lens differs from the default) and a machine-wide ui_reviewers {visual:[codex], behavior:[codex]}
- **WHEN** The developer runs `config` and `config --sources`
- **THEN** The project's lists win key by key (visual is ['claude']) and --sources says ui_reviewers is project; a copy of the default is dropped only when it matches the shipped value exactly
<!-- proven by test CP6-T05 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: A mixed project keeps the real value and drops only the copy
- **GIVEN** A project file with host_provider 'auto' (copy) and ui_reviewers {visual:[claude]} (real), a machine-wide file setting both host_provider 'codex' and ui_reviewers {visual:[codex], behavior:[codex]}
- **WHEN** The developer runs `config --sources` and `config`
- **THEN** host_provider is 'codex' from global while ui_reviewers.visual is ['claude'] from project: each key is judged on its own
<!-- proven by test CP6-T06 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: With no machine-wide value an old copy changes nothing
- **GIVEN** An old project holding the shipped host_provider and ui_reviewers, and either no machine-wide file or one that sets only role_models
- **WHEN** The developer runs `config` and `config --sources`
- **THEN** The effective settings equal what they were before this fix (host_provider 'auto', the shipped lens lists) and --sources still says project for both keys
<!-- proven by test CP6-T07 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: The project file is never rewritten, and the raw file still shows the copy
- **GIVEN** An old project file with the copied values, a machine-wide file that sets both keys, and the project file's bytes and modified time recorded
- **WHEN** The developer runs config, config --sources, models and status, and then `config --scope project`
- **THEN** The project file bytes are identical to before; --scope project still prints the copied host_provider and ui_reviewers exactly as on disk
<!-- proven by test CP6-T08 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: Running any command twice gives the same answer
- **GIVEN** An old project with copied values and a machine-wide file setting both keys
- **WHEN** The developer runs `config` and `config --sources` twice in a row
- **THEN** The second output equals the first byte for byte, both times showing the machine-wide values
<!-- proven by test CP6-T09 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: Removing the machine-wide value brings the project's own back, and the explicit unset still works
- **GIVEN** An old project with copied values and a machine-wide host_provider 'codex' (effective 'codex')
- **WHEN** The developer unsets host_provider in the machine-wide file (`config --global --unset host_provider`), then separately runs `config --unset host_provider` on the project
- **THEN** After the global unset the project's 'auto' applies and --sources says project; after the project unset the key is gone from the file and --sources says builtin
<!-- proven by test CP6-T10 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: A fresh init does not copy a machine-wide key, and the old behavior for other keys is unchanged
- **GIVEN** An empty project and a machine-wide file that sets host_provider 'codex' but not ui_reviewers
- **WHEN** The developer runs `init` (no .ratchet/config.json yet) and then `config --scope project`
- **THEN** The new project file has no host_provider but still carries ui_reviewers and the other defaults it always copied, so init's copy list is unchanged; the effective host_provider is 'codex'
<!-- proven by test CP6-T11 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: A copy made before the machine-wide file existed stops hiding it once it is added
- **GIVEN** A project initialised with no machine-wide file (so it holds the copied host_provider 'auto' and ui_reviewers), and a machine-wide file with host_provider 'codex' added afterwards
- **WHEN** The developer runs `config --sources`
- **THEN** host_provider is 'codex' from global (the case init's own skip rule cannot reach)
<!-- proven by test CP6-T12 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: The existing review_depth old-default rule and other project keys are unaffected
- **GIVEN** An old project holding the old default review_depth, a changed reviewers value and the copied host_provider, with a machine-wide host_provider 'codex'
- **WHEN** The developer runs `config --sources`
- **THEN** review_depth is still reported as builtin, reviewers as project, and only host_provider moves to global
<!-- proven by test CP6-T13 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: A copy next to a damaged machine-wide key is still refused, not silently skipped
- **GIVEN** An old project with the copied host_provider and a machine-wide file with host_provider set to an empty string
- **WHEN** The developer runs `config`
- **THEN** The command is refused with one plain line naming the machine-wide file and host_provider, with no traceback, as it was before this fix
<!-- proven by test CP6-T14 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: The documented limit is real: a deliberately chosen default is treated as a copy
- **GIVEN** A project that deliberately sets host_provider to the shipped default 'auto' to override a machine-wide 'codex'
- **WHEN** The developer runs `config --sources`
- **THEN** The machine-wide 'codex' wins and the source is global, which is the one case the README, orchestrator SKILL.md and schemas.md name as indistinguishable from an old copy
<!-- proven by test CP6-T15 in tests/test_ffl_migration.py, commit 2a8e0c1 -->

#### Scenario: The docs and the test list describe the old-copy rule
- **GIVEN** The repository's README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** The test reads each file
- **THEN** Each says an old project's copied host_provider and ui_reviewers (the shipped defaults) yield to a machine-wide value, that nothing is rewritten, and names the case it cannot tell apart; the README 'Running the tests' list contains `python3 tests/test_ffl_migration.py`
<!-- proven by test CP6-T16 in tests/test_ffl_migration.py, commit 2a8e0c1 -->
