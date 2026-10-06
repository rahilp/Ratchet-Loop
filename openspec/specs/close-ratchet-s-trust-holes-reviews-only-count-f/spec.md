# Close Ratchet's trust holes: reviews only count from the round's own folder and are signed by the review scripts; approvals are tied to the question the engine actually asked; the test gate reads per-test results so skipped, vacuous or crashing tests cannot pass; environment overrides an agent can set are not trusted; hook errors are visible in next and status; the README states honestly what each CLI enforces

## Purpose
What this feature does, as built and checked by Ratchet (run close-ratchet-s-trust-holes-reviews-only-count-f, 2026-10-06). Each scenario names the test that proves it.

## Requirements

### Requirement: Show hook errors in next and status
The system SHALL do this: When a Ratchet hook crashes, the agent and the user see it in next and status instead of the hooks looking healthy, and Cursor reviewers run headless in read-only mode.

- After a hook crashes, `ratchet.py status` shows a plain line saying a safety hook failed, when, and what to do
- `ratchet.py next` carries the same recent hook errors so the agent sees them without running doctor
- A hook that crashes no longer counts as a hook that is working: the 'hooks are running' check stays false until it runs cleanly
- Adding Cursor as a reviewer gives a command with --trust and --mode ask, so it runs without a prompt and cannot edit files

#### Scenario: A crashed safety hook shows up in plain status
- **GIVEN** a guard hook crash was logged a few minutes ago
- **WHEN** running `ratchet.py status` (plain text)
- **THEN** the output includes a plain-language line saying a safety hook failed, roughly when it happened, and what to do about it (not just the raw exception text)
<!-- proven by test CP1-T01 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: The same crash shows up in status --json
- **GIVEN** the same guard hook crash
- **WHEN** running `ratchet.py status --json`
- **THEN** the JSON carries the recent hook error (its event, host and time) alongside the existing `hooks`/`hooks_warning` fields, not only in the plain-text form
<!-- proven by test CP1-T02 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: next carries the same error without the agent running doctor
- **GIVEN** the same guard hook crash, and `doctor` has not been run
- **WHEN** running `ratchet.py next`
- **THEN** its output names the same recent hook error (same event/host/time content) as `status --json`, so the agent sees it without a separate doctor call
<!-- proven by test CP1-T03 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: A hook that crashes no longer counts as working, even with a fresh heartbeat
- **GIVEN** a guard heartbeat timestamped seconds ago (would normally read as fresh/enforcing) plus a hook-errors.log entry for that same guard invocation, timestamped at/after the heartbeat
- **WHEN** checking `doctor --json` (the hooks object)
- **THEN** the 'hooks are running' signal (today's `enforcing`/`missing` fields) reads as not working, despite the fresh heartbeat timestamp
<!-- proven by test CP1-T04 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: A later clean run clears the crash and restores working status
- **GIVEN** the crashed-then-fresh-heartbeat fixture from CP1-T04, followed by a later guard hook invocation that completes with no new error
- **WHEN** checking `doctor --json` / `status --json` again
- **THEN** the 'hooks are running' signal is true again; a past crash does not permanently wedge the status
<!-- proven by test CP1-T05 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: Errors older than 24 hours are not 'recent'
- **GIVEN** only a hook-errors.log entry from more than 24 hours ago, no recent ones
- **WHEN** running `status` and `next`
- **THEN** no hook-failure line or field appears, and the 'hooks are running' signal is decided from the heartbeat alone, unaffected by the stale error
<!-- proven by test CP1-T06 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: At most the 3 newest errors are shown
- **GIVEN** 5 distinct hook-errors.log entries within the last 24 hours at different timestamps
- **WHEN** running `status --json` and `next`
- **THEN** exactly the 3 most recent entries are present in the output; the other 2 are left out
<!-- proven by test CP1-T07 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: An error logged under another host's dialect does not bleed into this host's status
- **GIVEN** a hook-errors.log entry logged with host 'codex' while the project's detected host is 'claude'
- **WHEN** running `status`/`next` as claude, then again with the host set to codex
- **THEN** the codex error is absent from the claude view and present in the codex view, matching the existing per-host heartbeat scoping
<!-- proven by test CP1-T08 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: No crashes ever: status and next are unaffected
- **GIVEN** a clean project with hooks installed and firing normally, no hook-errors.log
- **WHEN** running `status`, `status --json`, and `next`
- **THEN** no hook-failure line or field appears anywhere, and all previously-existing keys/shape are unchanged (purely additive, like CP1-T13 in tests/test_hooks_status.py)
<!-- proven by test CP1-T09 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: A corrupt hook-errors.log cannot crash status or next
- **GIVEN** hook-errors.log replaced with garbage bytes, and separately with a directory in its place
- **WHEN** running `status`, `status --json`, `next`, and `doctor`
- **THEN** every command exits 0 with no traceback, and the output falls back to 'no known recent errors' instead of failing
<!-- proven by test CP1-T10 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: The surfaced message is plain language, not a raw traceback
- **GIVEN** a hook-errors.log line in run_hook's own format ('<time> <host> <event>: <ExceptionType>: <message>')
- **WHEN** running `status` (plain text)
- **THEN** the line shown to the user is a plain English sentence (mentions the safety hook / check in plain words) and does not dump the raw 'ExceptionType: message' text verbatim
<!-- proven by test CP1-T11 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: The Cursor preset runs headless and cannot edit files
- **GIVEN** the 'cursor' entry in PRESETS (ratchet.py, imported as a module)
- **WHEN** reading its resolved command list
- **THEN** the command includes `--trust` and `--mode ask`, so cursor-agent runs without an interactive prompt and in a mode that cannot edit files
<!-- proven by test CP1-T12 in tests/test_trust.py, commit 89d67d0 -->

#### Scenario: A cursor-preset reviewer still resolves after the new flags are added
- **GIVEN** a project with a provider added via `providers --add cursor --preset cursor --no-check`
- **WHEN** calling `reviewer_command()` for that provider id (or running `reviewers CPn` where it is selected)
- **THEN** the resolved command is the cursor command including --trust and --mode ask, and provider resolution/selection does not error (regression guard alongside tests/test_providers.py)
<!-- proven by test CP1-T13 in tests/test_trust.py, commit 89d67d0 -->

### Requirement: README states what each CLI enforces
The system SHALL do this: A reader of the README can see, per agent CLI, which protections Ratchet actually enforces and which it cannot.

- The README no longer says the hooks work the same on all four CLIs
- A short table lists Claude Code, Codex, Gemini and Copilot with what each one enforces (record protection, reviewer identity checks, stop hook, recording the user's replies)
- The table says plainly that only Claude Code tells Ratchet which sub-agent is acting, and that Copilot drops the prompt hook's message
- The README says hook errors now show up in status and next

#### Scenario: The 'hooks work the same everywhere' claim is gone
- **GIVEN** README.md's 'Using it with Codex, Gemini or Copilot' section
- **WHEN** the docs lint scans that section's text
- **THEN** no sentence claims the hooks work the same (or identically/equally) on all four CLIs
<!-- proven by test CP2-T01 in tests/test_docs.py, commit 9567683 -->

#### Scenario: A table names all four CLIs, no more and no fewer
- **GIVEN** the same section, after the old claim was removed
- **WHEN** the lint looks for a markdown table in it
- **THEN** a table exists with exactly one row per CLI for Claude (Code), Codex, Gemini and Copilot; none of the four is missing or duplicated, and no fifth unrelated CLI is presented as if it were one of these four
<!-- proven by test CP2-T02 in tests/test_docs.py, commit 9567683 -->

#### Scenario: Table covers record/state protection per CLI
- **GIVEN** the table from CP2-T02
- **WHEN** the lint checks each CLI's row
- **THEN** every row says something about Ratchet's records/state being protected from hand-edits for that CLI (not just a column header with no content)
<!-- proven by test CP2-T03 in tests/test_docs.py, commit 9567683 -->

#### Scenario: Table covers reviewer identity checks per CLI
- **GIVEN** the table from CP2-T02
- **WHEN** the lint checks each CLI's row
- **THEN** every row speaks to whether that CLI's reviewer/sub-agent identity can be confirmed
<!-- proven by test CP2-T04 in tests/test_docs.py, commit 9567683 -->

#### Scenario: Table covers the stop hook per CLI
- **GIVEN** the table from CP2-T02
- **WHEN** the lint checks each CLI's row
- **THEN** every row states whether the stop hook (blocking the agent from ending its turn early) is enforced for that CLI
<!-- proven by test CP2-T05 in tests/test_docs.py, commit 9567683 -->

#### Scenario: Table covers recording the user's replies per CLI
- **GIVEN** the table from CP2-T02
- **WHEN** the lint checks each CLI's row
- **THEN** every row states whether that CLI's prompt hook records the user's replies (the data approvals and hooks-waive rely on)
<!-- proven by test CP2-T06 in tests/test_docs.py, commit 9567683 -->

#### Scenario: Only Claude Code is credited with telling Ratchet which sub-agent is acting
- **GIVEN** the table and surrounding text
- **WHEN** the lint checks the Claude Code row/callout against the Codex, Gemini and Copilot rows
- **THEN** the text plainly says only Claude Code tells Ratchet which sub-agent is acting, and none of the other three rows claims that same capability for itself
<!-- proven by test CP2-T07 in tests/test_docs.py, commit 9567683 -->

#### Scenario: Copilot dropping the prompt hook's message is called out plainly
- **GIVEN** the table and surrounding text
- **WHEN** the lint checks the Copilot row/callout
- **THEN** the text plainly says Copilot's prompt hook drops the message (so Copilot cannot record the user's replies the way the others can), matching the real `hooks` dialect handling in ratchet.py rather than contradicting it
<!-- proven by test CP2-T08 in tests/test_docs.py, commit 9567683 -->

#### Scenario: README says hook errors now surface in status and next
- **GIVEN** the paragraph that used to say a broken hook only writes to `.ratchet/hook-errors.log`
- **WHEN** the lint checks that paragraph (or its replacement) after CP1 landed
- **THEN** the text says plainly that recent hook errors also show up in `ratchet.py status` and `ratchet.py next`, not only in the log file
<!-- proven by test CP2-T09 in tests/test_docs.py, commit 9567683 -->

#### Scenario: The log-file sentence no longer reads as the only way errors are surfaced
- **GIVEN** the same paragraph as CP2-T09
- **WHEN** the lint reads the whole paragraph together
- **THEN** the log-file sentence and the status/next sentence sit together without contradiction (e.g. it doesn't still say errors are only written to the log and never shown elsewhere)
<!-- proven by test CP2-T10 in tests/test_docs.py, commit 9567683 -->

#### Scenario: No em dashes introduced in the new table or surrounding prose
- **GIVEN** the new table and the rewritten paragraphs added for CP2
- **WHEN** the lint scans that text for the em dash character
- **THEN** none is found, matching the project's no-em-dash writing rule
<!-- proven by test CP2-T11 in tests/test_docs.py, commit 9567683 -->

### Requirement: Reviews only count from their round's folder
The system SHALL do this: Ratchet only accepts adversarial and UI reviews that sit in the folder of the review round it opened, so a review file from anywhere else is refused.

- Recording a review that lives outside this round's own folder is refused with a plain message saying where reviews must be
- A review copied in from an earlier round's folder is refused
- Reviews written by the review scripts or reviewer sub-agents into the right folder still pass as before
- The same rule applies to the combined UI review

#### Scenario: A review written outside the round's own folder is refused
- **GIVEN** round 1 is open for CP1 with output_dir reviews/CP1/adv-round-01/; a reviewer's JSON is otherwise perfectly valid (right reviewer id, fingerprint, nonce, files_reviewed) but is saved in an unrelated tempdir, not under reviews/CP1/adv-round-01/
- **WHEN** `adv-record CP1 --reviews <that path>` is run
- **THEN** the command exits non-zero, the message names the round's own folder (reviews/CP1/adv-round-01) as where reviews must be, and nothing is recorded: the round stays open and no evidence is written under adversarial/
<!-- proven by test CP3-T01 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A review copied in from an earlier round's folder is refused even with edited fields
- **GIVEN** round 1 for CP1 is opened and its review sits in reviews/CP1/adv-round-01/; the code then changes and round 2 is opened (new fingerprint and nonce); the round-1 file is left physically in adv-round-01/ but its fingerprint and round_nonce fields are hand-edited to match round 2
- **WHEN** `adv-record CP1 --reviews <the adv-round-01 path>` is run for round 2
- **THEN** the command is refused for not being inside round 2's own folder (adv-round-02), even though its fingerprint and nonce now match round 2
<!-- proven by test CP3-T02 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A review written straight into the round's own folder still passes (regression)
- **GIVEN** round 1 is open for CP1; the reviewer's JSON is written to <output_dir>/<reviewer-id>.json exactly as `reviewers CPn` tells it to (same as every existing adversarial test)
- **WHEN** `adv-record CP1 --reviews <output_dir>/<reviewer-id>.json` is run
- **THEN** Gate 3 (adversarial) PASSES exactly as before the fix; this flow is unchanged
<!-- proven by test CP3-T03 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A symlink inside the round's folder pointing outside it is refused
- **GIVEN** round 1's folder reviews/CP1/adv-round-01/ contains a symlink named <reviewer-id>.json that points at a review file the test wrote in /tmp with otherwise-correct fingerprint and nonce
- **WHEN** `adv-record CP1 --reviews reviews/CP1/adv-round-01/<reviewer-id>.json` is run (the symlink's own path IS inside the round folder)
- **THEN** the command is refused: the real (resolved) location is outside the round folder, so the symlink does not launder the file in
<!-- proven by test CP3-T04 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A `..`-laden path that only textually mentions the round folder is refused
- **GIVEN** round 1 is open for CP1; a correct review file exists outside the run, and the path passed is something like `.../reviews/CP1/adv-round-01/../../../tmp/x.json`
- **WHEN** `adv-record CP1 --reviews <that path>` is run
- **THEN** the command is refused for the same 'not in this round's folder' reason once the path is resolved, not fooled by the literal text 'adv-round-01' appearing in it
<!-- proven by test CP3-T05 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A combined UI review living outside the round's own folder is refused
- **GIVEN** a UI-gated CP1 is at the ui step; a combined.json is otherwise valid (current fingerprint, two reviewers, checkpoint, compared, no differences) but is saved in an unrelated tempdir, not under reviews/CP1/round-01/
- **WHEN** `ui-record CP1 --review <that path>` is run
- **THEN** the command is refused, the message names the round's own folder (reviews/CP1/round-01) as where the review must be, and the UI gate is left open (not passed)
<!-- proven by test CP3-T06 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A combined review copied in from an earlier UI round's folder is refused even with an edited fingerprint
- **GIVEN** round 1's combined.json sits in reviews/CP1/round-01/ and FAILed (so the capture spec is frozen); a fix changes the code (new fingerprint) and round 2 is opened with the SAME compared spec/scope; the round-1 file is left physically in round-01/ but its fingerprint field is hand-edited to match round 2's code
- **WHEN** `ui-record CP1 --review <the round-01 path>` is run for round 2
- **THEN** the command is refused for not being inside round 2's own folder (reviews/CP1/round-02), even though its fingerprint now matches
<!-- proven by test CP3-T07 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A combined review written into the round's own folder still passes (regression)
- **GIVEN** a UI-gated CP1's combined.json is written to reviews/CP1/round-01/combined.json exactly as the ratchet-ui-compare skill tells it to, with a valid current fingerprint, two reviewers and no differences
- **WHEN** `ui-record CP1 --review reviews/CP1/round-01/combined.json` is run
- **THEN** Gate 2 (UI) PASSES exactly as before the fix; this flow is unchanged
<!-- proven by test CP3-T08 in tests/test_trust.py, commit 56da599 -->

#### Scenario: A symlink inside the UI round's folder pointing outside it is refused
- **GIVEN** reviews/CP1/round-01/ contains a symlink named combined.json pointing at a combined review the test wrote in /tmp with an otherwise-correct current fingerprint
- **WHEN** `ui-record CP1 --review reviews/CP1/round-01/combined.json` is run
- **THEN** the command is refused: the real (resolved) location is outside the round folder
<!-- proven by test CP3-T09 in tests/test_trust.py, commit 56da599 -->

### Requirement: Agent-set overrides no longer bypass checks
The system SHALL do this: An agent can no longer approve work or act as the director by setting Ratchet's environment variables, while a person at their own terminal and a real director still can.

- Setting RATCHET_HUMAN_OVERRIDE=1 without a real terminal (as an agent's shell does) does not approve anything and says why
- A person at their own terminal can still use the override after typing back the short code Ratchet shows
- A builder that sets RATCHET_ROLE, RATCHET_HUMAN_OVERRIDE, RATCHET_HOME or RATCHET_DIRECTOR_KEY_FILE in any of the ways the tests try is blocked by the guard or ignored by Ratchet
- If Ratchet's per-user folder differs from the one the hooks use, the tamper check refuses to trust it and says so plainly
- A real director session still enrolls lanes and signs approvals

#### Scenario: Setting the override without a real terminal approves nothing
- **GIVEN** a checkpoint awaiting human-approve; RATCHET_HUMAN_OVERRIDE=1 is set in the env of a plain subprocess call with no tty (stdin/stdout are pipes, as an agent's Bash tool call would be)
- **WHEN** `ratchet.py human-approve` (and separately `accept-state`) is run with that env
- **THEN** the command exits non-zero, nothing is recorded as approved/accepted, and the message says the override is for a person at their own terminal
<!-- proven by test CP4-T01 in tests/test_trust.py, commit 7590109 -->

#### Scenario: A person at a real terminal can type back the code and approve
- **GIVEN** the same checkpoint, RATCHET_HUMAN_OVERRIDE=1, and the command is run attached to a real pty
- **WHEN** Ratchet prints a short confirmation code and the test, acting as the person, types that exact code back on the pty
- **THEN** the approval is recorded with `by: human` noting the override, and the run advances past the pause
<!-- proven by test CP4-T02 in tests/test_trust.py, commit 7590109 -->

#### Scenario: Typing the wrong code rejects the override
- **GIVEN** the same pty setup and the code Ratchet printed
- **WHEN** the test types back a code that does not match (e.g. one digit changed)
- **THEN** nothing is approved, the command dies with a plain message, and the run is still waiting
<!-- proven by test CP4-T03 in tests/test_trust.py, commit 7590109 -->

#### Scenario: A pty with no reply does not approve
- **GIVEN** the override is set and a real pty is attached, but the master side closes / sends EOF before answering the printed code
- **WHEN** the command runs to completion
- **THEN** nothing is approved; Ratchet does not fall back to treating the bare env var as consent
<!-- proven by test CP4-T04 in tests/test_trust.py, commit 7590109 -->

#### Scenario: Guard blocks indirect ways of setting RATCHET_HUMAN_OVERRIDE
- **GIVEN** a table of Bash tool_input commands that each make RATCHET_HUMAN_OVERRIDE=1 true for the next ratchet.py call without matching a bare `RATCHET_HUMAN_OVERRIDE=` token: `declare -x RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve`, `typeset RATCHET_HUMAN_OVERRIDE=1 ...`, `V=RATCHET_HUMAN_OVERRIDE; eval "$V=1 ratchet.py human-approve"`, `python3 -c "import os,subprocess; os.environ['RATCHET_HUMAN_OVERRIDE']='1'; subprocess.run(['python3','ratchet.py','human-approve'])"`
- **WHEN** each is sent to `ratchet.py hook guard`
- **THEN** every one is refused (exit 2) by the guard, naming the override as reserved for a person at their own terminal
<!-- proven by test CP4-T05 in tests/test_trust.py, commit 7590109 -->

#### Scenario: Guard blocks indirect ways of setting RATCHET_ROLE
- **GIVEN** the same style of table for RATCHET_ROLE=director: declare -x, typeset, eval with an indirect variable name, and a python3 -c os.environ assignment before calling director-key/director-sign
- **WHEN** each is sent to `ratchet.py hook guard`
- **THEN** every one is refused (exit 2), naming RATCHET_ROLE/director commands as reserved for the director session
<!-- proven by test CP4-T06 in tests/test_trust.py, commit 7590109 -->

#### Scenario: Guard blocks indirect ways of setting RATCHET_HOME
- **GIVEN** the same style of table redirecting RATCHET_HOME to a folder the agent fully controls (declare -x, typeset, eval indirect, python3 -c os.environ) ahead of a ratchet.py call
- **WHEN** each is sent to `ratchet.py hook guard`
- **THEN** every one is refused (exit 2), naming Ratchet's per-user folder as off-limits to builder sessions
<!-- proven by test CP4-T07 in tests/test_trust.py, commit 7590109 -->

#### Scenario: Guard blocks indirect ways of setting RATCHET_DIRECTOR_KEY_FILE
- **GIVEN** the same style of table pointing RATCHET_DIRECTOR_KEY_FILE at a key file of the agent's own making (declare -x, typeset, eval indirect, python3 -c os.environ)
- **WHEN** each is sent to `ratchet.py hook guard`
- **THEN** every one is refused (exit 2); the director key path cannot be redirected by a builder session
<!-- proven by test CP4-T08 in tests/test_trust.py, commit 7590109 -->

#### Scenario: A tampered role/home that slips past the guard is still ignored by the engine
- **GIVEN** ratchet.py is invoked directly (bypassing the PreToolUse guard entirely, as if the bypass succeeded) with RATCHET_ROLE=director and RATCHET_HOME pointed at a fresh folder the test fully controls, with no prior legitimate director key or hook history
- **WHEN** `director-key`, `director-enroll` and `director-sign` are attempted against the real project
- **THEN** the lane is not enrolled and no approval is accepted on the strength of this session alone: either the commands refuse because this folder has no history the hooks ever recorded, or the resulting enrollment/signature is rejected when the real lane checks it
<!-- proven by test CP4-T09 in tests/test_trust.py, commit 7590109 -->

#### Scenario: A RATCHET_HOME that disagrees with what the hooks recorded is refused
- **GIVEN** the hooks (guard/prompt) have already run at least once against this project with the real per-user folder H1
- **WHEN** a later ratchet.py command runs with RATCHET_HOME=H2, a different folder
- **THEN** Ratchet refuses to trust state/approvals under H2 and says plainly that its per-user folder does not match what the hooks use, instead of silently operating against H2
<!-- proven by test CP4-T10 in tests/test_trust.py, commit 7590109 -->

#### Scenario: The same RATCHET_HOME the hooks recorded keeps working
- **GIVEN** the hooks have run with folder H1 and the next command also uses RATCHET_HOME=H1
- **WHEN** a normal command (e.g. `status`) runs
- **THEN** it succeeds exactly as before; the new check does not false-positive on the legitimate, unchanged folder
<!-- proven by test CP4-T11 in tests/test_trust.py, commit 7590109 -->

#### Scenario: A real director session still enrolls a lane and signs an approval
- **GIVEN** RATCHET_ROLE=director set as the actual process environment of a session (not injected via a guarded shell command), with a freshly created director key
- **WHEN** that session runs `director-key`, then `director-enroll` on the project, then `director-sign approve-plan` while the run is awaiting plan approval
- **THEN** the key is created, the lane becomes director-enrolled, and the signed token is accepted by `approve-plan --director-token`, advancing the run
<!-- proven by test CP4-T12 in tests/test_trust.py, commit 7590109 -->

#### Scenario: A person at their own terminal can still use the override for accept-state
- **GIVEN** state.json was hand-edited outside ratchet.py (tamper fixture) and `accept-state` is attached to a real pty with RATCHET_HUMAN_OVERRIDE=1
- **WHEN** the test types back the printed code
- **THEN** the new baseline is accepted and recorded, matching the accept-state flow other suites' fixtures rely on
<!-- proven by test CP4-T13 in tests/test_trust.py, commit 7590109 -->

#### Scenario: The existing inline-override guard case is not weakened
- **GIVEN** the literal form already covered by tests/test_gates.py, `RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan`, sent to the guard
- **WHEN** the guard evaluates it
- **THEN** it is still refused (exit 2) exactly as before CP4, proving the new terminal/code requirement is additive, not a replacement that loosens this check
<!-- proven by test CP4-T14 in tests/test_trust.py, commit 7590109 -->

### Requirement: Approvals answer the question Ratchet asked
The system SHALL do this: A plan or step approval only counts when the user's first reply after Ratchet printed that exact question is one of the listed yes replies.

- When the plan or a finished step needs approval, Ratchet prints the question and the replies that count as yes
- A yes typed before the question was printed does not approve anything
- A yes to some other question the agent asked in chat does not approve the plan or a step; the agent must print the question again
- If the user's first reply after the question is not a listed yes, nothing is approved and the agent is told to ask again
- A run that was already waiting for approval before this change can carry on after the agent prints the question once more

#### Scenario: Asking for plan approval prints the question and the exact replies that count as yes
- **GIVEN** a plan just past plan-submit, in awaiting_plan_approval
- **WHEN** the agent runs `ratchet.py ask approve-plan`
- **THEN** stdout contains an ASK THE USER line naming the plan approval and at least one exact reply text the user can send that will count as yes
<!-- proven by test CP5-T01 in tests/test_trust.py, commit ba71dff -->

#### Scenario: A listed yes sent after the question approves the plan
- **GIVEN** `ask approve-plan` was just run
- **WHEN** the user's next message is one of the printed yes replies and the agent runs `ratchet.py approve-plan`
- **THEN** approve-plan exits 0 and the run's phase leaves awaiting_plan_approval (moves to building)
<!-- proven by test CP5-T02 in tests/test_trust.py, commit ba71dff -->

#### Scenario: A yes typed before the question was printed does not approve the plan
- **GIVEN** the user already said "yes, build it" in chat, then the agent runs `ask approve-plan` afterwards
- **WHEN** the agent runs `ratchet.py approve-plan` with no further user reply
- **THEN** approve-plan exits non-zero, nothing is approved, and the run is still in awaiting_plan_approval
<!-- proven by test CP5-T03 in tests/test_trust.py, commit ba71dff -->

#### Scenario: A yes to an unrelated question the agent asked does not approve the plan
- **GIVEN** `ask approve-plan` was run, then the agent separately asks the user something else in chat (not a registered Ratchet question) and the user replies with ordinary chat text, not one of the listed yes replies
- **WHEN** the agent runs `ratchet.py approve-plan`
- **THEN** approve-plan exits non-zero, says the first reply was not a clear/listed yes, and tells the agent to print the question again; the plan stays unapproved
<!-- proven by test CP5-T04 in tests/test_trust.py, commit ba71dff -->

#### Scenario: A yes to a different pending Ratchet question does not approve the plan
- **GIVEN** `ask approve-plan` was run, then the agent runs `ask set-review` (a different registered question) before the user replies
- **WHEN** the user then says yes and the agent runs `ratchet.py approve-plan`
- **THEN** approve-plan exits non-zero, names the other question that is actually pending, and does not change the plan's approval state
<!-- proven by test CP5-T05 in tests/test_trust.py, commit ba71dff -->

#### Scenario: A non-yes first reply spends the question; a later yes does not retroactively approve
- **GIVEN** `ask approve-plan` was run
- **WHEN** the user's first reply is "ok" (not an exact listed yes), then a later message says one of the exact listed yes replies, and the agent runs `ratchet.py approve-plan`
- **THEN** approve-plan exits non-zero because the FIRST reply after the question was not a listed yes; the agent is told to print the question again, and after it does and the user replies with a listed yes, approve-plan then succeeds
<!-- proven by test CP5-T06 in tests/test_trust.py, commit ba71dff -->

#### Scenario: Step review (human-approve) also requires the printed question first
- **GIVEN** a run with human_review=each where CP1 just completed and is awaiting_checkpoint_review
- **WHEN** the agent runs `ratchet.py human-approve` without having run `ratchet.py ask human-approve` and without a prior listed yes after that ask
- **THEN** human-approve exits non-zero asking the agent to print the question; after `ask human-approve` and a listed yes, human-approve then succeeds and the checkpoint's human gate is marked passed
<!-- proven by test CP5-T07 in tests/test_trust.py, commit ba71dff -->

#### Scenario: A pre-upgrade run already waiting for approval can carry on once the question is printed once more
- **GIVEN** a state.json hand-edited to awaiting_plan_approval with awaiting_since set but no `pending` entry (modelling a run started before this change), and a human message already sitting in the log from before the upgrade
- **WHEN** the agent runs `ratchet.py ask approve-plan` (printing the question fresh) and the user replies with a listed yes afterward
- **THEN** approve-plan succeeds; the stale pre-upgrade message alone (without a fresh ask + yes) does not approve anything
<!-- proven by test CP5-T08 in tests/test_trust.py, commit ba71dff -->

#### Scenario: A director-signed approval still works without any chat question being printed
- **GIVEN** a director-enrolled lane paused in awaiting_plan_approval
- **WHEN** the director runs `director-sign approve-plan` and the builder runs `approve-plan --director-token <token>`
- **THEN** approve-plan succeeds by token, with no `ask` step and no chat reply required
<!-- proven by test CP5-T09 in tests/test_trust.py, commit ba71dff -->

#### Scenario: Feedback at a review point still accepts freeform text, not just a listed yes
- **GIVEN** a run in awaiting_final_review
- **WHEN** the user sends ordinary feedback text (not one of the approval phrases) and the agent runs `ratchet.py feedback`
- **THEN** feedback is recorded from that text as before; CP5's yes/no allow-list does not block ordinary feedback replies
<!-- proven by test CP5-T10 in tests/test_trust.py, commit ba71dff -->

#### Scenario: Existing approval flows in test_gates.py and test_hooks_waiver.py still pass once they ask first
- **GIVEN** the shared `approve_plan()` / review helpers in tests/test_gates.py and the hooks-waiver flow in tests/test_hooks_waiver.py, updated to call `ask approve-plan` (or the relevant ask) before saying yes
- **WHEN** the full test_gates.py and test_hooks_waiver.py suites run
- **THEN** both suites pass in full (regression guard: CP5 must not break CP1-CP4 approval, hooks-waiver, or gate-integrity behavior)
<!-- proven by test CP5-T11 in tests/test_trust.py, commit ba71dff -->

### Requirement: Test check reads each test's result
The system SHALL do this: The test check passes only when every planned test case actually ran, failed for a real reason before the build, and passes after it, so skipped, empty or crashing tests cannot pass.

- Before the build, a test that crashes (for example a missing import) does not count as the required failure; only a failed check does
- After the build, a planned test case that was skipped, did not run, or only appears in a comment fails the check and is named
- A skip added to a test after the red run is flagged and fails the check
- Tests that write JUnit XML or Ratchet's JSON results file are read case by case; a step marked in the approved plan as having no results file is checked by exit code only, and status says so plainly
- Runs already in progress keep the old exit-code check for steps planned before this change

#### Scenario: A genuine failing test still counts as red before the build
- **GIVEN** tests.report is 'json'; the fixture reports the planned case as a real assertion failure (status 'failed', exit code 1)
- **WHEN** ratchet.py red CP6 runs
- **THEN** the red run is accepted as before (recorded, step moves to implement); nothing about the new report reading blocks a genuine failure
<!-- proven by test CP6-T01 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A crash before the build is not accepted as the required failure
- **GIVEN** tests.report is 'json'; the fixture crashes with an uncaught exception (e.g. simulating a missing import) and reports the planned case's status as 'error', not 'failed'
- **WHEN** ratchet.py red CP6 runs
- **THEN** red is refused with a message saying a crash does not count as the required failure and naming the case id; the checkpoint's step stays at 'red', not 'implement'
<!-- proven by test CP6-T02 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A skipped planned case fails the gate after the build
- **GIVEN** after implementation, the fixture's report marks the planned case's status as 'skipped' while the process exit code is 0
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the behavior gate FAILS and the message names the skipped case id; state records gates.behavior.status as 'failed'
<!-- proven by test CP6-T03 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A planned case missing from the report fails the gate even with exit 0
- **GIVEN** the report simply omits an entry for the planned case id (the test runner filtered it out) while every other case passes and exit code is 0
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate FAILS and names the missing case id, even though the overall exit code was 0
<!-- proven by test CP6-T04 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A case id that only appears in a comment is not treated as proven
- **GIVEN** the test source file has the planned case id written inside a comment (e.g. a TODO) but the report has no case entry for it and exit code is 0
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate FAILS and names the case id as not run, proving the check reads the report, not just the source text (the old source-grep coverage check alone would have passed this)
<!-- proven by test CP6-T05 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A skip added after the red run is flagged even though the test file only grew
- **GIVEN** the red-run snapshot's report showed the planned case as a real failure; before the behavior gate, a skip marker is added around that case (the test file's old lines are all still present, in order, so a pure line-additive check would not catch it) and the current report now shows that case as 'skipped'
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate FAILS, specifically naming the case as newly skipped since the red run, not just a generic 'weakened tests' message
<!-- proven by test CP6-T06 in tests/test_results.py, commit 894aa42 -->

#### Scenario: Every planned case passing in a JUnit report passes the gate
- **GIVEN** tests.report is 'junit'; the fixture writes a JUnit XML report where every planned case's <testcase> has no <failure>/<error>/<skipped> child, and exit code is 0
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate PASSES as before
<!-- proven by test CP6-T07 in tests/test_results.py, commit 894aa42 -->

#### Scenario: Every planned case passing in Ratchet's JSON report passes the gate
- **GIVEN** tests.report is 'json'; the fixture writes {"cases":[{"id": <planned id>, "status": "passed"}]} for every planned case, exit code 0
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate PASSES as before
<!-- proven by test CP6-T08 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A step marked with no report file is checked by exit code only, and status says so
- **GIVEN** the approved plan has tests.report: 'none' with a reason string for this checkpoint; the fixture never writes a report file
- **WHEN** ratchet.py red/behavior run (pass and fail variants) and then ratchet.py status is read
- **THEN** the gate behaves exactly like the old exit-code-only check (fails when exit != 0, passes when exit == 0) and status/next shows a plain line that this checkpoint has no per-test report and is checked by exit code only, including the reason
<!-- proven by test CP6-T09 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A legacy plan without a report field keeps the old exit-code check
- **GIVEN** a checkpoint's tests dict has no 'report' key at all (as every plan looked before this change), simulating a run already in progress; RATCHET_TEST_REPORT is never read by the fixture
- **WHEN** ratchet.py red and ratchet.py behavior run with passing and failing fixture commands
- **THEN** neither command errors about a missing report or demands one; both gates behave exactly as the pre-CP6 exit-code-only check did
<!-- proven by test CP6-T10 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A required report that was never written fails the gate, not silently trusted via exit code
- **GIVEN** tests.report is 'json' but the fixture exits 0 without writing anything to RATCHET_TEST_REPORT
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate FAILS with a message that no per-test report was produced at the expected path; it does not fall back to trusting the exit code
<!-- proven by test CP6-T11 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A malformed report fails the gate with a clear message, not a crash
- **GIVEN** tests.report is 'json' (and separately 'junit'); the fixture writes unparseable content (truncated JSON / invalid XML) to RATCHET_TEST_REPORT
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate FAILS with a message naming the report as unreadable/malformed; ratchet.py does not crash with a raw traceback and does not pass the gate
<!-- proven by test CP6-T12 in tests/test_results.py, commit 894aa42 -->

#### Scenario: A stale report left over from a previous attempt is not reused
- **GIVEN** a report file already exists at the path Ratchet will use (left over from an earlier failed attempt) showing all planned cases as passed; the current run's fixture this time crashes and does not rewrite that file
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate does not treat the stale all-pass report as proof; it fails (e.g. because the report is not fresh for this attempt), so a leftover file from a prior run can't be reused to fake a pass
<!-- proven by test CP6-T13 in tests/test_results.py, commit 894aa42 -->

#### Scenario: Guardrail checks still gate on exit code alongside per-case test reading
- **GIVEN** tests.report is 'json' with every planned case genuinely passing, but the checkpoint also has a `checks` entry whose command exits non-zero
- **WHEN** ratchet.py behavior CP6 runs
- **THEN** the gate still FAILS because of the failing check, exactly as test_guardrails.py already expects; the new per-case reading does not change or bypass the checks mechanism
<!-- proven by test CP6-T14 in tests/test_results.py, commit 894aa42 -->

#### Scenario: Spec status does not call a scenario proven on a false exit-0 pass
- **GIVEN** a spec-linked checkpoint has a case tied to a spec scenario; the report shows that case as 'failed' even though the overall test command exits 0 (e.g. a suite that doesn't fail the process on an assertion failure)
- **WHEN** ratchet.py behavior CP6 runs and then ratchet.py spec status is read
- **THEN** the behavior gate FAILS (per CP6-T04/T08 logic) so the checkpoint never completes, and spec status does not report that scenario as 'proven'
<!-- proven by test CP6-T15 in tests/test_results.py, commit 894aa42 -->

### Requirement: Review scripts sign the reviews they write
The system SHALL do this: Ratchet only accepts an outside-provider or UI review that the review script signed with a secret for that round, and it marks host sub-agent reviews it cannot confirm.

- A review written by hand into the round folder, with the right nonce and code version, is refused as unsigned
- Reviews from external_review.py and the combined review from ui_review.py are accepted for their own round and refused for any other round
- Host sub-agent reviews are accepted; on Claude Code the hook confirms the writer, and on other CLIs status and the evidence say 'not independently confirmed'
- The round's secret is never printed, never inside the project folder, and gone after the round is recorded
- The README table row for review checks matches what each CLI now enforces

#### Scenario: A hand-typed review with the right nonce and fingerprint is refused as unsigned
- **GIVEN** an open adversarial round for CP1 with its printed nonce and the current code fingerprint
- **WHEN** a review JSON is written by hand (not via external_review.py or a confirmed sub-agent) straight into the round's output folder, copying the real nonce, fingerprint and checkpoint, with findings: []
- **THEN** `adv-record` exits non-zero, says the review is unsigned, and the gate does not pass
<!-- proven by test CP7-T01 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: A review written by external_review.py for its own round is accepted
- **GIVEN** an open round whose reviewer is a 'command' provider backed by a fake CLI that replies with no findings
- **WHEN** external_review.py is run for that reviewer and round, then `adv-record` is given its output file
- **THEN** adv-record succeeds and the round is recorded as passed
<!-- proven by test CP7-T02 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: The combined UI review written by ui_review.py is accepted for its round
- **GIVEN** a capture manifest and two per-role reviews combined by ui_review.py's combine step into combined.json in the round's own folder
- **WHEN** `ui-record` is given that combined.json
- **THEN** ui-record accepts it (passes or fails on its merits, not on signature) because it was signed by the script that produced it
<!-- proven by test CP7-T03 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: A signed review from a closed round does not validate against a new round
- **GIVEN** a validly signed, no-findings review recorded for round 1 of CP1, then a fresh round 2 opened later (e.g. after a code change)
- **WHEN** the round-1 signed file (byte-for-byte, including its signature) is submitted to adv-record for round 2
- **THEN** adv-record refuses it: the nonce no longer matches the open round, and the signature does not re-validate for the new round either
<!-- proven by test CP7-T04 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: A round's secret cannot sign for a different checkpoint
- **GIVEN** two checkpoints each with their own open adversarial round and their own secret
- **WHEN** a review correctly signed for CP1's round is relabelled with CP2's checkpoint id, fingerprint and nonce and submitted to CP2's adv-record
- **THEN** adv-record refuses it as unsigned for CP2's round
<!-- proven by test CP7-T05 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: Editing a signed review after signing breaks the signature
- **GIVEN** a review signed and written by external_review.py with one finding
- **WHEN** a single character is changed in that finding's description (or severity) after the file is written, and the edited file is given to adv-record
- **THEN** adv-record refuses it as unsigned/tampered, naming the file, even though nonce, fingerprint and checkpoint still match
<!-- proven by test CP7-T06 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: A host sub-agent review is accepted on Claude Code, and the hook's confirmation of the writer is recorded
- **GIVEN** an open round whose reviewer is the host's own ratchet-adversary sub-agent
- **WHEN** a `hook guard --host claude` call approves a Write of the round's own review file by agent_type ratchet-adversary, the file is then written with findings: [], and that path is given to adv-record
- **THEN** adv-record accepts it and nothing in status or the recorded evidence calls it 'not independently confirmed'
<!-- proven by test CP7-T07 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: A host sub-agent review on a non-Claude CLI is still accepted, but marked not independently confirmed
- **GIVEN** the same setup as CP7-T07 except the guard hook call is made with --host codex (or no agent_type at all, as Codex/Gemini/Copilot give)
- **WHEN** the review file is written and given to adv-record
- **THEN** adv-record still accepts it (the run is never stalled) and both `ratchet.py status` and the round's recorded evidence say this review is 'not independently confirmed'
<!-- proven by test CP7-T08 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: The round's secret is never printed
- **GIVEN** an adversarial round is opened for CP1
- **WHEN** `reviewers CP1` is run both in plain text and with --json
- **THEN** neither output contains the round's secret value anywhere
<!-- proven by test CP7-T09 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: The round's secret is stored outside the project folder
- **GIVEN** an open adversarial round and an open UI round
- **WHEN** the project directory tree (everything under the repo root, including .ratchet/) is searched for the secret value
- **THEN** it is not found anywhere under the project; it only exists under RATCHET_HOME (Ratchet's per-user folder), the same place the director key lives
<!-- proven by test CP7-T10 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: The round's secret is gone once the round is recorded
- **GIVEN** an open adversarial round for CP1 with a valid signed no-findings review
- **WHEN** adv-record records the round successfully
- **THEN** the per-round secret file under RATCHET_HOME no longer exists (or is rotated so the old value no longer verifies), so a copy of the already-accepted review file cannot be replayed to forge a later round
<!-- proven by test CP7-T11 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: Signed reviews with real findings still flow through normally
- **GIVEN** a signed review from external_review.py carrying one major finding
- **WHEN** adv-record is given it
- **THEN** it is accepted as a real review (not rejected for being signed), the finding is recorded, and the gate behaves exactly as the severity/scope rules require (CP3's and the existing rules' behaviour is unaffected by signing)
<!-- proven by test CP7-T12 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: A hand-typed combined UI review is refused the same way
- **GIVEN** an open UI round for a checkpoint with a UI gate
- **WHEN** a combined.json is written by hand (not via ui_review.py) into the round's folder with a plausible verdict and fingerprint
- **THEN** `ui-record` refuses it as unsigned, the same as the adversarial case
<!-- proven by test CP7-T13 in tests/test_signing.py, commit ae389e2 -->

#### Scenario: The README's review-checks row matches what each CLI now enforces
- **GIVEN** the README's per-CLI table (added in CP2, updated by later steps)
- **WHEN** the row for review/signature checks is read
- **THEN** it says plainly that a script-signed review is required or a host sub-agent review is accepted, that only Claude Code confirms the sub-agent writer (others are marked not independently confirmed), and that the protection is against a different process on the same machine, not against the operating system
<!-- proven by test CP7-T14 in tests/test_signing.py, commit ae389e2 -->
