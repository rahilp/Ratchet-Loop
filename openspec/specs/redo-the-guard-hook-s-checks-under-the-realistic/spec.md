# Redo the guard hook's checks under the realistic-use bar, so it stops blocking harmless commands (read-only commands, and ordinary writes that merely mention Ratchet's names) while still stopping a cooperative agent from stumbling past a gate or taking an easy shortcut: hand-editing state, evidence or the human log, or actually setting Ratchet's reserved variables.

## Purpose
What this feature does, as built and checked by Ratchet (run redo-the-guard-hook-s-checks-under-the-realistic, 2026-10-12). Each scenario names the test that proves it.

## Requirements

### Requirement: Notes mentioning reserved names are not refused
The system SHALL do this: An agent can write a brief, a note or a commit message that mentions Ratchet's reserved variables or its hook command, while actually setting a reserved variable or calling the hook is still refused.

- Writing the live example brief to /tmp (a quoted heredoc holding RATCHET="python3 .../ratchet.py" and prose that names the reserved variables and words like export, source, local or eval) is allowed
- A commit message, echo or printf to a file outside .ratchet/ that says something like 'RATCHET_ROLE=director is for the director only' is allowed, and so is a grep whose pattern names a reserved variable or 'hook guard', even with a $(...) or backtick elsewhere in the command
- These are still refused: export RATCHET_HUMAN_OVERRIDE=1, RATCHET_ROLE=director before any command (ls included), env RATCHET_HOME=x cmd, declare -x / typeset, V=NAME; eval "$V=1 ...", python3 -c writing os.environ, bash -c 'export ...', a heredoc fed to bash or python that sets one, and a real ratchet.py hook call (directly, through $RATCHET, or inside $(...))
- README and schemas.md ('Variables a builder session may read but never set') say text that only mentions these names is fine

#### Scenario: Writing the live example brief to /tmp is allowed
- **GIVEN** A building-phase project
- **WHEN** The agent runs the exact live false positive: `cat > /tmp/brief.md <<'EOF'` followed by a line setting RATCHET="python3 .../ratchet.py" and prose mentioning RATCHET_HUMAN_OVERRIDE, RATCHET_ROLE, export, source, local and eval, closed with `EOF`
- **THEN** The guard allows the command (exit 0, no RATCHET GUARD message)
<!-- proven by test CP1-T01 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: A note, echo, printf or commit message that only names a reserved variable is allowed
- **GIVEN** A building-phase project
- **WHEN** The agent runs `printf '%s\n' "RATCHET_HUMAN_OVERRIDE=1 is for humans" > /tmp/n.md`, `echo "RATCHET_ROLE=director is for the director only" > /tmp/note.txt`, and `git commit -m 'guard: refuse export RATCHET_HUMAN_OVERRIDE=1'`, each writing outside .ratchet/
- **THEN** Every one of these is allowed (exit 0)
<!-- proven by test CP1-T02 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: A grep/search pattern naming a reserved variable or 'hook guard' is allowed, even next to a $(...) or backtick elsewhere in the command
- **GIVEN** A building-phase project with a tests/ folder to search
- **WHEN** The agent runs a grep whose pattern has alternation and names a reserved variable (for example a pattern matching 'RATCHET_ROLE' or similar text), `grep -rn "Calling \`ratchet.py hook guard\`" tests/`, and `for f in $(ls tests/*.py); do grep -c 'hook guard' "$f"; done`
- **THEN** Every one of these is allowed (exit 0); none is treated as a hook call or a reserved-name assignment just because the words appear
<!-- proven by test CP1-T03 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: Plainly reading a reserved variable, or assigning RATCHET to the engine path and running a non-hook command with it, is allowed
- **GIVEN** A building-phase project
- **WHEN** The agent runs `echo $RATCHET_ROLE`, `printenv RATCHET_HOME`, and `RATCHET="python3 .../ratchet.py"; $RATCHET status`
- **THEN** Every one of these is allowed (exit 0)
<!-- proven by test CP1-T04 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: Setting RATCHET_HUMAN_OVERRIDE, directly or indirectly, is still refused
- **GIVEN** A building-phase project
- **WHEN** The agent runs `export RATCHET_HUMAN_OVERRIDE=1; python3 ratchet.py human-approve`, `declare -x RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve`, `typeset RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve`, `V=RATCHET_HUMAN_OVERRIDE; eval "$V=1 ratchet.py human-approve"`, and a python3 -c that assigns os.environ['RATCHET_HUMAN_OVERRIDE']
- **THEN** Every one of these is refused (exit 2) with a message naming the override as reserved for a person at their own terminal
<!-- proven by test CP1-T05 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: Setting RATCHET_ROLE is still refused, even as a leading assignment before a read-only command like ls (closes today's gap)
- **GIVEN** A building-phase project
- **WHEN** The agent runs `RATCHET_ROLE=director ls`, `export RATCHET_ROLE=director; python3 ratchet.py status`, `declare -x RATCHET_ROLE=director; ratchet.py director-key`, `typeset RATCHET_ROLE=director; ratchet.py director-sign approve-plan`, `V=RATCHET_ROLE; eval "$V=director ratchet.py director-key"`, and a python3 -c that assigns os.environ['RATCHET_ROLE']
- **THEN** Every one of these is refused (exit 2), including `RATCHET_ROLE=director ls` which the live guard allows today because ls is read-only; the message names RATCHET_ROLE or the director session
<!-- proven by test CP1-T06 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: Setting RATCHET_HOME is still refused
- **GIVEN** A building-phase project
- **WHEN** The agent runs `env RATCHET_HOME=./evil-home ratchet.py status`, `export RATCHET_HOME=./evil-home; python3 ratchet.py status`, `declare -x RATCHET_HOME=./evil-home; ratchet.py status`, `typeset RATCHET_HOME=./evil-home; ratchet.py status`, and `V=RATCHET_HOME; eval "$V=./evil-home ratchet.py status"`
- **THEN** Every one of these is refused (exit 2) with a message naming RATCHET_HOME or Ratchet's per-user folder
<!-- proven by test CP1-T07 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: Setting RATCHET_DIRECTOR_KEY_FILE is still refused
- **GIVEN** A building-phase project
- **WHEN** The agent runs `declare -x RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan`, `typeset RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan`, `V=RATCHET_DIRECTOR_KEY_FILE; eval "$V=./evil.key ratchet.py director-sign approve-plan"`, and a python3 -c that assigns os.environ['RATCHET_DIRECTOR_KEY_FILE']
- **THEN** Every one of these is refused (exit 2) with a message naming the director key path
<!-- proven by test CP1-T08 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: A heredoc body saved to a plain file is data, but the same body fed to bash or python is still code and is refused if it sets a reserved variable
- **GIVEN** A building-phase project
- **WHEN** The agent runs `bash <<'EOF'\nexport RATCHET_ROLE=director\npython3 ratchet.py status\nEOF` and `python3 - <<'EOF'\nimport os\nos.environ['RATCHET_HUMAN_OVERRIDE']='1'\nEOF`, as well as `bash -c 'export RATCHET_ROLE=director; python3 ratchet.py status'`
- **THEN** Every one of these is refused (exit 2), unlike CP1-T01's heredoc which only writes a file and is never run
<!-- proven by test CP1-T09 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: Calling ratchet.py's own hook command is still refused, even directly, through $RATCHET, or inside a $(...) substitution, in a command that also contains a harmless mention elsewhere
- **GIVEN** A building-phase project
- **WHEN** The agent runs `python3 ratchet.py hook guard --host claude`, `RATCHET="python3 .../ratchet.py"; $RATCHET hook prompt --host claude`, and `echo "testing hook guard" && $(python3 ratchet.py hook guard --host claude)`
- **THEN** Every one of these is refused (exit 2) with the existing 'hooks are called by your agent CLI, not by you' message, proving the mention-is-fine fix did not weaken the hook-call check
<!-- proven by test CP1-T10 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: Every form the CP4 guard tests already proved refused stays refused (no regression)
- **GIVEN** A building-phase project
- **WHEN** The agent runs the CP4-T05..T08 and CP4-X05 forms: `export RATCHET_ROLE=director; python3 ratchet.py status`, `env RATCHET_ROLE=director python3 ratchet.py status`, `(RATCHET_ROLE=director python3 ratchet.py status)`, `RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan`, `export RATCHET_HUMAN_OVERRIDE=1; python3 ratchet.py human-approve`, and `env FOO=1 RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py human-approve`
- **THEN** Every one of these is still refused (exit 2); this fast file proves the same forms tests/test_trust_cp4.py proves, without needing to run the slower suite
<!-- proven by test CP1-T11 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: A second host dialect agrees with Claude's guard on one allow and one refuse row
- **GIVEN** A building-phase project with hooks installed for a second host
- **WHEN** The same FP1 heredoc-to-/tmp command and the same `export RATCHET_ROLE=director; ...` command are sent through the guard as a codex argv-list Bash call and as a gemini run_shell_command call
- **THEN** The codex and gemini payloads reach the same verdicts (allow / refuse) as the claude payload for both commands
<!-- proven by test CP1-T12 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

#### Scenario: README, the orchestrator SKILL.md and schemas.md say plainly what the guard allows and still refuses
- **GIVEN** The repository's docs
- **WHEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md are read
- **THEN** At least one of them states that a command that only mentions Ratchet's reserved variable names, paths or hook command in data or text is allowed, and that actually setting a reserved variable or calling the hook directly is still refused
<!-- proven by test CP1-T13 in tests/test_guard_reserved_names.py, commit e2c54b4 -->

### Requirement: Read-only commands on run files pass
The system SHALL do this: An agent can read Ratchet's run files with ordinary shell commands (after cd into .ratchet/, in loops and pipelines) and write files elsewhere that mention a run path, while any shell write into state, evidence or the human log is still refused.

- Both live orchestrator commands from Oct 11 are allowed: `cd .ratchet && wc -l hook-errors.log && ls runs/ && find runs -name stop-blocks.jsonl | xargs wc -l; tail -c 3000 hook-errors.log` and `cat .ratchet/hook-errors.log | cut -c1-600; for f in .ratchet/runs/*/stop-blocks.jsonl; do echo "== $f"; wc -l < "$f"; done 2>/dev/null`
- Reading with cut, sort, uniq, tr, awk without > or |, sed without -i, xargs running a read-only program, while-read loops, input redirects (<, < <(ls ...)) and cd into .ratchet/ then reading or saving a copy to /tmp is allowed
- Writing outside .ratchet/ with text that only names a run path is allowed: echo 'see .ratchet/runs/x/state.json' > /tmp/note.txt, and a git commit -m message naming .ratchet/runs/x/state.json
- Still refused: echo x > .ratchet/runs/r/state.json (also after cd into the run folder), sed -i on human_log.jsonl, cp, mv, tee, touch, truncate or rm into run files, cat >> human_log.jsonl, jq ... > /tmp/s && mv /tmp/s state.json, find -delete or -exec, and find .ratchet ... | xargs rm (allowed today; this step closes it)
- README, the orchestrator SKILL.md and schemas.md list what the guard allows on run files and what it still refuses

#### Scenario: The live orchestrator diagnostic command (a) is allowed
- **GIVEN** a project with .ratchet/hook-errors.log and .ratchet/runs/*/stop-blocks.jsonl on disk
- **WHEN** the agent's Bash tool runs `cd .ratchet && wc -l hook-errors.log && ls runs/ && find runs -name stop-blocks.jsonl | xargs wc -l; tail -c 3000 hook-errors.log`
- **THEN** the guard allows it (exit 0, no deny) for claude, codex and gemini payload shapes
<!-- proven by test CP2-T01 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: The live orchestrator diagnostic command (b) is allowed
- **GIVEN** the same project
- **WHEN** the agent's Bash tool runs `cat .ratchet/hook-errors.log | cut -c1-600; for f in .ratchet/runs/*/stop-blocks.jsonl; do echo "== $f"; wc -l < "$f"; done 2>/dev/null`
- **THEN** the guard allows it (exit 0, no deny)
<!-- proven by test CP2-T02 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: Plain read-only filters on a run file are allowed
- **GIVEN** the project's .ratchet/runs/hk/state.json and .ratchet/human_log.jsonl
- **WHEN** the agent runs each of: `cut -c1-80 .ratchet/runs/hk/state.json`, `cd .ratchet && sort runs/hk/human_log.jsonl | uniq -c`, `tr -d '\n' < .ratchet/runs/hk/state.json`, `awk '{print $1}' .ratchet/human_log.jsonl`, and `cd .ratchet && cat runs/hk/human_log.jsonl | awk -F, '{print $1}'` (awk reading only, no `>` or further write)
- **THEN** every form is allowed
<!-- proven by test CP2-T03 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: sed used to read (no -i) is allowed
- **GIVEN** the same project
- **WHEN** the agent runs `sed -n '1,5p' .ratchet/runs/hk/state.json` and, after `cd .ratchet`, `sed -n '2p' human_log.jsonl`
- **THEN** both are allowed
<!-- proven by test CP2-T04 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: xargs is allowed when the program it runs is read-only
- **GIVEN** the same project
- **WHEN** the agent runs `find .ratchet/runs -name '*.json' | xargs cat` and `find .ratchet/runs -name '*.json' | xargs wc -l`
- **THEN** both are allowed
<!-- proven by test CP2-T05 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: A while-read loop over an input redirect is allowed
- **GIVEN** the same project
- **WHEN** the agent runs `while read -r f; do wc -l "$f"; done < <(ls .ratchet/runs/hk/*.json)`
- **THEN** it is allowed
<!-- proven by test CP2-T06 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: After cd into .ratchet/, reading and saving a copy to /tmp is allowed
- **GIVEN** the same project
- **WHEN** the agent runs `cd .ratchet && cat runs/hk/state.json > /tmp/copy.json` and `cd .ratchet/runs/hk && cp state.json /tmp/copy2.json`
- **THEN** both are allowed: the read side is fine, and the destination (/tmp) is outside .ratchet/ so the redirect/copy target itself is not a protected write
<!-- proven by test CP2-T07 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: Writing outside .ratchet/ that only names a run path is allowed
- **GIVEN** a scratch file /tmp/note.txt outside the project's .ratchet/
- **WHEN** the agent runs `echo 'see .ratchet/runs/hk/state.json' > /tmp/note.txt`
- **THEN** it is allowed (the redirect target /tmp/note.txt is not inside .ratchet/; the path mentioned in the text is data, not a target)
<!-- proven by test CP2-T08 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: A commit message that names a run path is allowed
- **GIVEN** the same git project with a change staged
- **WHEN** the agent runs `git commit -m 'guard: block writes to .ratchet/runs/hk/state.json'`
- **THEN** it is allowed
<!-- proven by test CP2-T09 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: A direct write into a run file is still refused, with or without cd
- **GIVEN** the same project
- **WHEN** the agent runs `echo x > .ratchet/runs/hk/state.json` and, separately, `cd .ratchet/runs/hk && echo x > state.json`
- **THEN** both are refused (exit 2 / deny) with the Ratchet-owns-this-file message
<!-- proven by test CP2-T10 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: sed -i on the human log is still refused
- **GIVEN** the same project
- **WHEN** the agent runs `sed -i 's/a/b/' .ratchet/human_log.jsonl` and, after `cd .ratchet`, `sed -i '' -e 's/a/b/' human_log.jsonl`
- **THEN** both are refused
<!-- proven by test CP2-T11 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: cp, mv, tee, touch and truncate into a run file are still refused
- **GIVEN** the same project and a scratch file /tmp/x.json
- **WHEN** the agent runs each of: `cp /tmp/x.json .ratchet/runs/hk/state.json`, `mv /tmp/x.json .ratchet/runs/hk/state.json`, `echo x | tee .ratchet/runs/hk/state.json`, `touch .ratchet/runs/hk/state.json`, `truncate -s 0 .ratchet/runs/hk/state.json`, and `: > .ratchet/runs/hk/state.json`
- **THEN** every form is refused
<!-- proven by test CP2-T12 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: rm on a run file is still refused
- **GIVEN** the same project
- **WHEN** the agent runs `rm .ratchet/runs/hk/stop-blocks.jsonl` and, after `cd .ratchet/runs/hk`, `rm state.json`
- **THEN** both are refused
<!-- proven by test CP2-T13 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: Appending to the human log is still refused
- **GIVEN** the same project
- **WHEN** the agent runs `cat extra.jsonl >> .ratchet/human_log.jsonl`
- **THEN** it is refused
<!-- proven by test CP2-T14 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: jq-edit-then-replace on a run file is still refused
- **GIVEN** the same project
- **WHEN** the agent runs `jq '.phase="done"' .ratchet/runs/hk/state.json > /tmp/s.json && mv /tmp/s.json .ratchet/runs/hk/state.json`
- **THEN** the command is refused: the mv half's target is a protected run file, so the whole pipeline is denied even though the jq/redirect half alone only touches /tmp
<!-- proven by test CP2-T15 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: find -delete / -exec on run files is still refused
- **GIVEN** the same project
- **WHEN** the agent runs `find .ratchet/runs/hk -name '*.jsonl' -delete` and `find .ratchet/runs/hk -name 'state.json' -exec rm {} \;`
- **THEN** both are refused
<!-- proven by test CP2-T16 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: find piped into xargs rm closes the known gap: still refused
- **GIVEN** the same project (this exact form is allowed before this step and must become refused by it)
- **WHEN** the agent runs `find .ratchet | xargs rm`
- **THEN** it is refused, proving the gap named in this step's notes is closed
<!-- proven by test CP2-T17 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: Codex's Bash-command-as-list payload agrees with the other hosts
- **GIVEN** the same project
- **WHEN** the codex PreToolUse payload carries `tool_input.command` as a list `["bash", "-lc", "<command>"]` for one allowed form (CP2-T01's command) and one refused form (CP2-T10's `echo x > .ratchet/runs/hk/state.json`)
- **THEN** the allowed form passes and the refused form is denied, exactly like the claude dialect
<!-- proven by test CP2-T18 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: Gemini's run_shell_command payload agrees with the other hosts
- **GIVEN** the same project
- **WHEN** the gemini BeforeTool / run_shell_command payload carries the same allowed form (CP2-T07's cd-then-copy-to-/tmp) and the same refused form (CP2-T13's rm on a run file)
- **THEN** the allowed form passes and the refused form is denied
<!-- proven by test CP2-T19 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: Loosening read-only commands inside .ratchet/ does not loosen CP1's reserved-variable refusal
- **GIVEN** the same project
- **WHEN** the agent runs `cd .ratchet && RATCHET_HOME=./evil ls` (a reserved-variable assignment in front of an otherwise read-only, in-.ratchet command)
- **THEN** it is still refused, with the reserved-variable message, not silently allowed because `ls` alone would be read-only
<!-- proven by test CP2-T20 in tests/test_guard_reads.py, commit 08824dd -->

#### Scenario: Docs say plainly what the guard allows and still refuses on run files
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md after this step
- **WHEN** a developer reads them
- **THEN** they state that read-only commands on .ratchet/ run files (including after cd, in loops and pipelines) are allowed, writes outside .ratchet/ that merely mention a run path are allowed, and any shell write into state, evidence or the human log is still refused
<!-- proven by test CP2-T21 in tests/test_guard_reads.py, commit 08824dd -->

### Requirement: Python one-liners that only read pass
The system SHALL do this: An agent can inspect a run file with a short python3 -c or python3 heredoc that only reads it (for example printing a field of state.json), while a one-liner that writes, renames or deletes a run file is still refused.

- `cd .ratchet/runs/x && python3 -c "import json; d=json.load(open('state.json')); print(d['phase'])"` and the same read as a python3 heredoc are allowed, and so is `cat state.json | python3 -m json.tool`
- Still refused when they touch a run file: open(..., 'w'/'a'/'x'/'+'), write_text or write_bytes, .write(, json.dump to a file, os.remove, unlink, rename or replace, shutil, subprocess and os.system
- README and schemas.md say a python one-liner that only reads run files is allowed and what counts as a write

#### Scenario: A python -c one-liner that only reads state.json after cd is allowed
- **GIVEN** the run folder .ratchet/runs/x has a state.json with phase "building"
- **WHEN** the agent runs: cd .ratchet/runs/x && python3 -c "import json; d=json.load(open('state.json')); print(d['phase'])"
- **THEN** the guard allows the command (decide_guard returns no block)
<!-- proven by test CP3-T01 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: The same read as a python heredoc after cd is allowed
- **GIVEN** the run folder .ratchet/runs/x has a state.json with phase "building"
- **WHEN** the agent runs: cd .ratchet/runs/x && python3 <<'PY'\nimport json\nprint(json.load(open('state.json'))['phase'])\nPY
- **THEN** the guard allows the command
<!-- proven by test CP3-T02 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Piping a run file into python3 -m json.tool is allowed
- **GIVEN** the run folder .ratchet/runs/x has a state.json
- **WHEN** the agent runs: cd .ratchet/runs/x && cat state.json | python3 -m json.tool
- **THEN** the guard allows the command
<!-- proven by test CP3-T03 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: json.tool given the run file as its only argument, no cd, is allowed
- **GIVEN** the run folder .ratchet/runs/x has a state.json, and the agent has not cd'd anywhere
- **WHEN** the agent runs: python3 -m json.tool .ratchet/runs/x/state.json
- **THEN** the guard allows the command, because json.tool with no output-file argument is read-only
<!-- proven by test CP3-T04 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: A python -c one-liner naming the run path directly, with no cd, is allowed
- **GIVEN** the run folder .ratchet/runs/x has a state.json, and the command never changes directory
- **WHEN** the agent runs: python3 -c "import json; print(json.load(open('.ratchet/runs/x/state.json'))['phase'])"
- **THEN** the guard allows the command (this is the exact over-blocked form the brief reproduced live; it must now pass without a cd)
<!-- proven by test CP3-T05 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Appending to the human log from python is still refused
- **GIVEN** the run folder has .ratchet/human_log.jsonl with one line already in it
- **WHEN** the agent runs: python3 -c "open('.ratchet/human_log.jsonl', 'a').write('{}\\n')"
- **THEN** the guard refuses the command with a RATCHET GUARD message naming Ratchet's run state, and human_log.jsonl is unchanged
<!-- proven by test CP3-T06 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Rewriting state.json with write_text from a heredoc is still refused
- **GIVEN** the run folder .ratchet/runs/x has a state.json
- **WHEN** the agent runs a python3 heredoc that does: Path('.ratchet/runs/x/state.json').write_text('{}')
- **THEN** the guard refuses the command, and state.json is unchanged
<!-- proven by test CP3-T07 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: A bare .write( call on a file opened without an explicit w/a/x mode is still refused
- **GIVEN** the run folder .ratchet/runs/x has a state.json
- **WHEN** the agent runs: python3 -c "f=open('.ratchet/runs/x/state.json', 'r+'); f.write('{}')"
- **THEN** the guard refuses the command on the .write( marker alone, even though the open() mode itself is not 'w', 'a' or 'x'
<!-- proven by test CP3-T08 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Forging gate evidence with json.dump is still refused
- **GIVEN** the run folder has .ratchet/runs/x/evidence/CP1/behavior.json holding {"status": "PASS"}
- **WHEN** the agent runs: python3 -c "import json; json.dump({'status': 'PASS'}, open('.ratchet/runs/x/evidence/CP1/behavior.json', 'w'))"
- **THEN** the guard refuses the command, and the evidence file is unchanged
<!-- proven by test CP3-T09 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Deleting state.json with os.remove is still refused
- **GIVEN** the run folder .ratchet/runs/x has a state.json
- **WHEN** the agent runs: python3 -c "import os; os.remove('.ratchet/runs/x/state.json')"
- **THEN** the guard refuses the command, and state.json still exists
<!-- proven by test CP3-T10 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Overwriting state.json with shutil is still refused
- **GIVEN** the run folder .ratchet/runs/x has a state.json, and /tmp has an unrelated fabricated JSON file
- **WHEN** the agent runs: python3 -c "import shutil; shutil.copy('/tmp/fake_state.json', '.ratchet/runs/x/state.json')"
- **THEN** the guard refuses the command, and state.json is unchanged
<!-- proven by test CP3-T11 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Deleting evidence through subprocess is still refused
- **GIVEN** the run folder has .ratchet/runs/x/evidence/CP1/behavior.json
- **WHEN** the agent runs: python3 -c "import subprocess; subprocess.run(['rm', '.ratchet/runs/x/evidence/CP1/behavior.json'])"
- **THEN** the guard refuses the command, and the evidence file still exists
<!-- proven by test CP3-T12 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: Deleting the human log through os.system is still refused
- **GIVEN** the run folder has .ratchet/human_log.jsonl with one line in it
- **WHEN** the agent runs: python3 -c "import os; os.system('rm .ratchet/human_log.jsonl')"
- **THEN** the guard refuses the command, and human_log.jsonl still exists
<!-- proven by test CP3-T13 in tests/test_guard_python_reads.py, commit 83c48f7 -->

#### Scenario: json.tool used to rewrite a run file in place is still refused
- **GIVEN** the run folder .ratchet/runs/x has a state.json
- **WHEN** the agent runs: cd .ratchet/runs/x && python3 -m json.tool state.json state.json
- **THEN** the guard refuses the command, because the output-file argument targets the same protected run file, and state.json is unchanged
<!-- proven by test CP3-T14 in tests/test_guard_python_reads.py, commit 83c48f7 -->

### Requirement: Redirect and loop writes into run files refused
The system SHALL do this: An agent that writes into a run's state, evidence or human log through any shell redirect form, a process substitution, uniq's output operand or a loop over a .ratchet listing is refused, while every read form CP1-CP3 allow still passes.

- Each of these is refused when its target is a run's state.json, a file under evidence/ or human_log.jsonl (plain path and after cd into the run folder): `jq . /tmp/new.json &> <file>`, `&>>`, `cat /tmp/new.json 2> <file>`, `2>>`, `cat x 1> <file>`, `1>>`, `>| <file>`, `>& <file>`, `cat README.md > >(tee <file>)`, `cat /tmp/new | uniq - <file>`, and `find .ratchet/runs -name state.json | while read f; do echo x > "$f"; done`
- The director's probe (/tmp/guardlane_director_probe.py) prints REFUSED for every write row and ALLOWED for `cat <state file>`, and a test copies each of its rows
- Every read form CP1-CP3 allow still passes, including `... 2>/dev/null`, `2>&1`, `>&2`, `cat <state file> > /tmp/s.json`, `cd .ratchet && cat runs/hk/state.json > /tmp/s.json`, `uniq <run file>`, the Oct 11 for-loop with `wc -l < "$f"` and the while-read loop over `< <(ls .ratchet/...)`; tests/test_guard_reads.py, test_guard_reserved_names.py and test_guard_python_reads.py pass unchanged
- README, the orchestrator SKILL.md and schemas.md state the exact redirect and loop rule: which redirect forms count as a write into .ratchet/, that uniq's second operand or `-` makes it a writer, and that a loop over a .ratchet listing whose body writes through its loop variable is refused

#### Scenario: Every fd-numbered or combined-stream redirect into a run file is refused
- **GIVEN** the run folder .ratchet/runs/hk has state.json, evidence/CP1/x.json and human_log.jsonl
- **WHEN** the agent runs any of: `jq . /tmp/new.json &> <target>`, `&>>`, `cat /tmp/new.json 2> <target>`, `2>>`, `cat x 1> <target>`, `1>>`, `>| <target>`, `>& <target>` — against each of the three target kinds, both as a plain .ratchet/runs/hk/... path and after `cd .ratchet/runs/hk`
- **THEN** the guard refuses every one (exit 2), with the Ratchet-owns-this-file message, and none of the three target files is changed
<!-- proven by test CP4-T01 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: A process-substitution writer onto a run file is refused
- **GIVEN** the run folder has state.json, evidence/CP1/x.json and human_log.jsonl, and /tmp/README.md is a harmless external file
- **WHEN** the agent runs `cat /tmp/README.md > >(tee <target>)` against each of the three target kinds, plain path and after cd into the run folder
- **THEN** the guard refuses every one (exit 2) with the Ratchet-owns-this-file message: `>(...)` anywhere makes the command not provably read-only, and the program inside it (tee) is judged as its own writer
<!-- proven by test CP4-T02 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: uniq with a `-` stdin marker or a second file operand writing onto a run file is refused
- **GIVEN** the run folder has state.json, evidence/CP1/x.json and human_log.jsonl, and /tmp/new has unrelated content
- **WHEN** the agent runs `cat /tmp/new | uniq - <target>` and `uniq /tmp/new <target>` against each of the three target kinds, plain path and after cd
- **THEN** the guard refuses every one (exit 2) with the Ratchet-owns-this-file message: uniq's second operand (or `-` plus a second operand) is a write target, not a second input
<!-- proven by test CP4-T03 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: A loop over a .ratchet listing whose body writes through its loop variable is refused, whatever the writer or variable name
- **GIVEN** the run folder .ratchet/runs/hk has state.json, evidence/CP1/x.json and human_log.jsonl, each matched by a `find`/glob listing rooted at .ratchet/runs
- **WHEN** the agent runs `find .ratchet/runs -name state.json | while read f; do echo x > "$f"; done`, the same shape with `rm "$f"` as the body, and a `for v in .ratchet/runs/hk/evidence/CP1/*.json; do echo x > "$v"; done` for-loop
- **THEN** the guard refuses every one (exit 2) with the Ratchet-owns-this-file message, and no listed file is changed or removed
<!-- proven by test CP4-T04 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: The director's probe agrees row for row with the live guard
- **GIVEN** the exact fixture and command dict /tmp/guardlane_director_probe.py builds (plain redirect, quoted redirect target, &> redirect, 2> redirect, 1> redirect, tee, process subst tee, uniq stdin operand, cp -rt combined, while read loop, sed -i, python write, and the READ cat row)
- **WHEN** the test fires each row through `ratchet.py hook guard --host claude`
- **THEN** every row the probe labels a writer onto state.json is refused (exit 2) and the READ cat row is allowed (exit 0) — the live guard's verdict matches every row of the probe, not just the ones this run already fixed
<!-- proven by test CP4-T05 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: Codex's Bash-command-as-list payload agrees on a redirect-write form
- **GIVEN** the run folder has state.json
- **WHEN** the agent's command is delivered as codex's list-shaped tool_input (`["bash", "-lc", "jq . /tmp/new.json &> .ratchet/runs/hk/state.json"]`), not a plain string
- **THEN** the guard refuses it (exit 2) with the Ratchet-owns-this-file message, the same verdict claude's string payload gets for the identical command
<!-- proven by test CP4-T06 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: Stderr-only and fd-duplication suffixes on an otherwise read-only command stay allowed
- **GIVEN** the run folder has state.json readable at .ratchet/runs/hk/state.json
- **WHEN** the agent runs `jq . .ratchet/runs/hk/state.json 2>/dev/null`, the same command with `2>&1` appended, and a command ending `>&2`
- **THEN** the guard allows all three (exit 0): none of /dev/null, /dev/stdout, /dev/stderr or an fd duplication is a write into .ratchet/
<!-- proven by test CP4-T07 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: Reading a run file and redirecting the copy to /tmp stays allowed, plain path and after cd
- **GIVEN** the run folder has state.json
- **WHEN** the agent runs `cat .ratchet/runs/hk/state.json > /tmp/s.json` and, after `cd .ratchet && cat runs/hk/state.json > /tmp/s.json`
- **THEN** the guard allows both (exit 0): the redirect's target is outside .ratchet/, so the read is unaffected by CP4's write-detection changes
<!-- proven by test CP4-T08 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: uniq given a single run file to read stays allowed
- **GIVEN** the run folder has human_log.jsonl with more than one line
- **WHEN** the agent runs `uniq .ratchet/human_log.jsonl`
- **THEN** the guard allows it (exit 0): one operand and no `-` is a read, not a writer
<!-- proven by test CP4-T09 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: The two Oct 11 orchestrator diagnostic loops stay allowed
- **GIVEN** the run folder has hook-errors.log and runs/*/stop-blocks.jsonl, matching tests/test_guard_reads.py's CMD_LIVE_A/CMD_LIVE_B fixture
- **WHEN** the agent runs CMD_LIVE_A (`cd .ratchet && wc -l hook-errors.log && ls runs/ && find runs -name stop-blocks.jsonl | xargs wc -l; tail -c 3000 hook-errors.log`) and CMD_LIVE_B (the `for f in .ratchet/runs/*/stop-blocks.jsonl; do echo "== $f"; wc -l < "$f"; done` loop)
- **THEN** the guard allows both (exit 0) with no RATCHET GUARD denial: a loop whose body only reads through its loop variable (`wc -l < "$f"`, `echo "== $f"`) is never treated as a writer
<!-- proven by test CP4-T10 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: jq reading a run file with stderr silenced stays allowed, plain path and after cd
- **GIVEN** the run folder has state.json
- **WHEN** the agent runs `jq . .ratchet/runs/hk/state.json 2>/dev/null` and, after `cd .ratchet/runs/hk`, `jq . state.json 2>/dev/null`
- **THEN** the guard allows both (exit 0)
<!-- proven by test CP4-T11 in tests/test_guard_writes.py, commit 9d0f63d -->

#### Scenario: README, SKILL.md and schemas.md state the exact redirect and loop rule
- **GIVEN** the current text of README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** the docs are searched for the redirect/loop rule
- **THEN** together the docs say plainly: every output-redirect form (including fd-numbered, &>, >|, >&word and process substitution) that targets a run file is a write and is refused; uniq's second operand or a bare `-` with a target makes it a writer; and a loop over a .ratchet listing whose body writes through its loop variable is refused whatever the writer or variable name, while a read-only loop body stays allowed
<!-- proven by test CP4-T12 in tests/test_guard_writes.py, commit 9d0f63d -->
