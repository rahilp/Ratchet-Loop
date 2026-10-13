# Fewer questions and waits for multi-lane runs: (1) a fixer or test writer may adjust tests that this same lane added during this run without asking for approve-changes, while tests planned before the build (the plan's planned cases, frozen at red) stay locked; (2) merging main into a lane at a step boundary is allowed by default (Ratchet and its guard accept it, with the gates re-checked against the merged code); (3) status and next show when a question is waiting for the user (or director), including its kind and since when, so nobody mistakes a waiting lane for a building one; (4) a provably leftover state.lock in the run folder (no live Ratchet process holds it) clears itself instead of waiting 65 minutes, and complete warns when run as a foreground call that a tool timeout could cut off.

## Purpose
What this feature does, as built and checked by Ratchet (run fewer-questions-and-waits-for-multi-lane-runs-1, 2026-10-13). Each scenario names the test that proves it.

## Requirements

### Requirement: Show when a question waits for an answer
The system SHALL do this: An agent or director reading status or next can see that a lane is waiting for someone's answer, to which kind of question, and since when.

- While any question waits (plan approval, a test change, a lettered choice, raising a review limit, and so on), plain status says the run is waiting for the user's answer (or the director's, in a director lane), names the kind of question, and says since when
- status --json and next carry the same facts as a 'question' entry, even while the phase still reads building
- Once the question is answered or withdrawn, the line and the entry are gone
- A run with no waiting question prints exactly what it printed before
- README, the orchestrator skill and schemas.md describe the new line and entry

#### Scenario: A waiting plan-approval question shows in status, status --json and next
- **GIVEN** A one-step project whose plan is submitted, and the agent has run `ask approve-plan` (no reply yet)
- **WHEN** The agent runs `status`, `status --json` and `next`
- **THEN** Plain status has one line saying the run is waiting for the user's answer, naming the plan question and giving the time asked (the date and HH:MM of pending.asked_at in state.json). status --json and next carry question = {kind: 'approve-plan', since: <exactly pending.asked_at>, waiting_on: 'user', about: <any>}; the two JSON entries are equal
<!-- proven by test CP1-T01 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: The question shows even while the phase still reads building
- **GIVEN** The plan is approved and the run is in the building phase; the agent runs `ask set-review each`
- **WHEN** The agent runs `status`, `status --json` and `next`
- **THEN** phase is still 'building' and the existing waiting_on is still null (additive only), yet the plain line, status --json question (kind 'set-review', waiting_on 'user') and next question all say a question is waiting. Without this a waiting lane looks like a building one
<!-- proven by test CP1-T02 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: A hooks question gets its time from when it was asked
- **GIVEN** A run in building phase; the agent runs `ask hooks-waive`, which never sets awaiting_since
- **WHEN** The agent runs `status --json` and `next`
- **THEN** question.kind is 'hooks-waive' and question.since equals pending.asked_at, not null and not awaiting_since (which stays null); the plain line also carries a time
<!-- proven by test CP1-T03 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: Each kind of question reports its own kind
- **GIVEN** Separate runs, each with one waiting question: `ask choice --options A,B`, `ask cancel`, `ask approve-changes` (after the approved plan file was edited so changes need approving), and `ask accept-finding CP1 F1,F2` on a step whose review round left F1 and F2 open (reuse blocked_round() in tests/test_accept_finding.py); `ask raise-limit CP1` at a step at its review-round limit if the test-writer can reach it with the helper in tests/test_limits.py
- **WHEN** The agent reads `status --json` and plain `status` for each
- **THEN** question.kind is 'choice', 'cancel', 'approve-changes', 'accept-finding' (and 'raise-limit') respectively; for accept-finding and raise-limit question.about names CP1; each plain line names its kind in words that fit it (choice -> option/choice, cancel -> cancel, approve-changes -> change, accept-finding -> finding, raise-limit -> limit). A stub that always says 'approve-plan' fails
<!-- proven by test CP1-T04 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: In a director lane the question says it waits for the director
- **GIVEN** A one-step project enrolled with director-key and director-enroll (pattern in tests/test_accept_finding.py CP3-X03), and `ask accept-finding CP1 F1,F2` is waiting (that command still works in a director lane)
- **WHEN** The agent reads `status`, `status --json` and `next`
- **THEN** question.waiting_on is 'director' (not 'user'), and the plain line says the director's answer is awaited and does not say the user's
<!-- proven by test CP1-T05 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: The question disappears once answered
- **GIVEN** A run with `ask approve-plan` waiting
- **WHEN** The user replies 'yes, go ahead' in the owner chat and the agent runs `approve-plan`; then the agent reads status, status --json and next
- **THEN** The plain status has no waiting-question line; status --json and next have question null (or the key absent); no pending key remains in state.json
<!-- proven by test CP1-T06 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: The question disappears once withdrawn, and a new question gets a new time
- **GIVEN** A run in building phase with `ask set-review each` waiting; its asked_at is recorded
- **WHEN** The agent runs `ask withdraw`, reads status, then runs `ask cancel` and reads status --json again
- **THEN** After the withdraw the line is gone and question is null/absent. After the second ask question.kind is 'cancel' and question.since is the new asked_at (later than the first), never the old time
<!-- proven by test CP1-T07 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: A run with no waiting question prints what it printed before
- **GIVEN** A building-phase run with nothing asked, and the same run with `ask set-review each` waiting
- **WHEN** The agent runs plain `status` in both states, and `status --json` in the first
- **THEN** With nothing asked, plain status has no line mentioning a waiting question or an answer being awaited and the output equals the output after an ask followed by `ask withdraw`. With a question waiting, plain status has exactly one more line than without, and every other line is unchanged. status --json with nothing asked has question null/absent and all its other keys (phase, waiting_on, awaiting_since, next, stop_ok, done, total) as before
<!-- proven by test CP1-T08 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: Reading status does not change the question or the run
- **GIVEN** A run with `ask approve-plan` waiting, and state.json's content noted
- **WHEN** The agent runs `status`, `status --json` and `next` three times each
- **THEN** question.since is the same every time, and state.json is byte-identical afterwards (the new helper only reads)
<!-- proven by test CP1-T09 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: A plan waiting to be asked is not yet a waiting question
- **GIVEN** A project right after plan-submit: phase awaiting_plan_approval, `ask approve-plan` not yet run
- **WHEN** The agent reads `status --json`, `next` and plain `status`
- **THEN** The existing waiting_on is still 'human' and awaiting_since is unchanged, but question is null/absent and the plain status has no question line, because no question has been put to anyone yet
<!-- proven by test CP1-T10 in tests/test_question_waiting.py, commit 702e176 -->

#### Scenario: README, the orchestrator skill and schemas.md describe the new line and entry
- **GIVEN** The repository docs after the step
- **WHEN** A test reads README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **THEN** Each mentions the `question` entry of status --json and next together with its kind and since fields, and the waiting-for-answer line of status; schemas.md lists waiting_on values 'user' and 'director' and says the entry is absent/null once answered or withdrawn
<!-- proven by test CP1-T11 in tests/test_question_waiting.py, commit 702e176 -->

### Requirement: Clear a lock left by a stopped command
The system SHALL do this: When a Ratchet command was killed mid-run, the next command on that machine runs at once instead of waiting about 65 minutes, while a lock held by a live command is still respected.

- After a Ratchet command is killed mid-run (for example cut off by a tool timeout), the next Ratchet command on that machine clears the leftover lock and runs at once
- A lock held by a command that is still running is respected exactly as before: a short command waits, and behind a long one the agent is told Ratchet is busy
- A lock whose owner Ratchet can't check (an older lock file, one written on another machine, or on Windows) keeps today's timed clearing
- The busy message no longer tells the agent to wait 65 minutes when the lock's owner is known to be gone, and README, the orchestrator skill and schemas.md say how a leftover lock is cleared

#### Scenario: A command that was killed leaves no hour-long wait behind
- **GIVEN** A project where 'ratchet.py red CP1' is running its slow test and holds state.lock, then is killed with SIGKILL (its process group), so state.lock is still on disk and was written moments ago
- **WHEN** The agent runs 'ratchet.py red CP1' again (the go file now exists, so its test fails fast)
- **THEN** The second red exits 0 in well under the 30 s lock wait (under 15 s), its output does not say Ratchet is busy and does not mention minutes of waiting, the step's red is recorded, and no state.lock or state.lock.break is left behind afterwards
<!-- proven by test CP2-T01 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: A command that is still running keeps its lock
- **GIVEN** 'ratchet.py red CP1' is running its slow test (marker file written) and holds state.lock; state.json and the lock file's text are noted
- **WHEN** The agent runs a second 'ratchet.py red CP1' and then another state-changing command (ask approve-plan) against the same project
- **THEN** Each is refused within a few seconds with a message that says Ratchet is busy running red; the lock file text is unchanged, state.json is byte-identical to before, and the first command is still running; once the go file is written the first command finishes, and state.lock is gone
<!-- proven by test CP2-T02 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: A short command waits for a live lock and takes it the moment it is released
- **GIVEN** A lock file in today's format naming this test's own (live) process id, written just now, in a project root; a helper thread removes it after about 0.4 s
- **WHEN** _StateLock(root, wait=3).acquire() is called, and separately _StateLock(root, wait=0.3).acquire() while the lock stays
- **THEN** The first call waits (takes at least 0.3 s), then returns None (lock held) and its own lock file replaces the removed one; the second returns the 'still changing this run' busy text after waiting about its full 0.3 s, and the held lock's text is unchanged
<!-- proven by test CP2-T03 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: An old-style lock whose owner is gone is still cleared only by age
- **GIVEN** A lock file in today's format ('<dead pid> held-by-test', where the pid is the real dead pid of the killed command from T01's setup) written just now; and a second copy of it with its modification time set older than the timed limit (test_timeout_sec plus 300 s)
- **WHEN** _StateLock(root, wait=0.3).acquire() runs against the fresh copy, then against the backdated copy
- **THEN** The fresh copy is respected (busy text returned, file untouched, so existing tests that fake a held lock keep passing); the backdated copy is cleared by the timed rule and the lock is acquired
<!-- proven by test CP2-T04 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: A lock written on another machine is not judged by this machine's processes
- **GIVEN** The real state.lock left by the killed command (owner dead on this machine), with this machine's name in its text replaced by 'build-box-2.invalid'; written just now. A second copy is backdated past the timed limit
- **WHEN** _StateLock(root, wait=0.3).acquire() runs against the fresh copy, then against the backdated copy
- **THEN** The fresh copy is respected (busy text returned, file untouched) even though its process id is not running here; the backdated copy is cleared by the timed rule. If this machine's name cannot be found in the real lock text the case fails with a message saying so
<!-- proven by test CP2-T05 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: A reused process id that is not a Ratchet command does not hold the lock
- **GIVEN** On Linux: the real state.lock left by the killed command, with its process id replaced by the id of a live 'sleep 120' process the test started (an id the system handed to something that is not running ratchet.py)
- **WHEN** The agent runs a real state-changing 'ratchet.py' command (ask approve-plan) against that project
- **THEN** The lock is cleared at once and the command runs (no busy message, no wait); the sleep process is untouched and still running. A companion check swaps in the id of a live ratchet.py command (the running red from T02's setup) and the lock is respected as busy
<!-- proven by test CP2-T06 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: Two commands started at once behind a dead owner's lock both run, one after the other
- **GIVEN** The real state.lock left by a killed command, and two 'ratchet.py ask approve-plan' (or other cheap state-changing) commands started at the same instant in the same project
- **WHEN** Both finish
- **THEN** Neither prints a busy or lock-file error, state.json is still valid JSON, and no state.lock, state.lock.break or *.stale file is left; repeated for 5 rounds with a fresh dead lock each time, so the clearing is serialised under the breaker file (never two commands holding the lock together, never a live lock removed)
<!-- proven by test CP2-T07 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: When a dead owner's lock cannot be cleared the message does not say to wait 65 minutes
- **GIVEN** The real state.lock left by a killed command, plus a fresh state.lock.break file (held by some other process for a few system calls), so the leftover lock cannot be removed right now
- **WHEN** _StateLock(root, wait=0.3).acquire() returns its reason
- **THEN** The reason is a refusal that changes nothing (both files still on disk), and it does not contain '65 minutes' or tell the agent to wait for the lock to clear itself after some minutes; the text of a live holder's busy message (T02) still names the command and still says to try again when it finishes
<!-- proven by test CP2-T08 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: A normal command removes its own lock and leaves no breaker file
- **GIVEN** A project with no state.lock
- **WHEN** A normal state-changing command runs and exits, and another runs straight after
- **THEN** state.lock and state.lock.break do not exist after each; the second command is never told Ratchet is busy, and a lock held by a live second command (the running red from T02's setup) is not cleared just because an earlier command with another process id has exited
<!-- proven by test CP2-T09 in tests/test_stale_lock.py, commit e71f2f2 -->

#### Scenario: The README, the orchestrator skill and schemas.md say how a leftover lock is cleared
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md after this step
- **WHEN** The part of each that talks about state.lock is read
- **THEN** Each says a lock left by a command whose process is no longer running on this machine is cleared at once, and that a lock it cannot check (older lock file, another machine, Windows) still clears by the timed rule; schemas.md also states that a lock held by a live command is respected, and none of the three still says a stopped command's lock always waits the full timed period
<!-- proven by test CP2-T10 in tests/test_stale_lock.py, commit e71f2f2 -->

### Requirement: Warn when complete may be cut off
The system SHALL do this: An agent that runs complete as a foreground call is warned first that a tool timeout could cut it off, and told to run it in the background.

- When an agent runs complete as an ordinary foreground call, the first thing complete prints is a plain warning that a tool timeout could cut it off, how long it may take, and to run it in the background instead
- When the agent's host says the call runs in the background, complete prints no warning
- The warning never stops or slows complete, and it still finishes as before
- README and the orchestrator skill tell agents to run complete in the background

#### Scenario: A foreground complete warns first that a tool timeout could cut it off
- **GIVEN** A finished one-step run (gates passed, regression command set to 'exits 0'). The agent's host sent the guard a Bash call 'complete CP1' with run_in_background false and a 120000 ms timeout, and the guard let it through
- **WHEN** The agent runs `ratchet.py complete CP1` with exactly those arguments
- **THEN** The first thing complete prints (on its error stream, flushed) is a plain warning that a tool timeout could cut complete off, that gives a how-long-it-may-take figure with a time unit (for example the regression run's allowed minutes), and that says to run complete in the background instead; the warning text is not mixed into complete's normal output (stdout has no trace of it)
<!-- proven by test CP3-T01 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: The warning appears before the regression suite starts
- **GIVEN** Same finished run, but the regression command waits (up to about 20 seconds) for a release file that the test creates, and the guard noted the call as foreground
- **WHEN** The test starts `complete CP1`, reads the first line from its error stream while the regression command is still blocked, and only then creates the release file
- **THEN** The warning line arrives while the regression suite has not finished (so it was printed and flushed before the suite ran, not buffered until the end), and complete then goes on to finish with exit status 0 once the file exists
<!-- proven by test CP3-T02 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: The warning never stops or changes complete
- **GIVEN** Two identical finished one-step runs: one with a foreground note, one with a background note
- **WHEN** `complete CP1` runs in each
- **THEN** Both exit with status 0, both mark CP1 done in status --json, both make the same step commit, and the regression command ran exactly once in each; the warned run differs from the unwarned one only by the warning text
<!-- proven by test CP3-T03 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: No warning when the host says the call runs in the background
- **GIVEN** A finished one-step run; the guard noted 'complete CP1' with run_in_background true
- **WHEN** The agent runs `ratchet.py complete CP1`
- **THEN** Nothing about timeouts or running in the background is printed on either stream, and the step is completed
<!-- proven by test CP3-T04 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: No signal from the host: the warning is still printed
- **GIVEN** A finished one-step run where no guard hook ran at all for this call (a host without the hook, so nothing was noted)
- **WHEN** The agent runs `ratchet.py complete CP1`
- **THEN** The same warning is printed first (Ratchet cannot tell the call is in the background, so it warns), and complete still finishes with exit status 0
<!-- proven by test CP3-T05 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: A host that does not say either way is treated as foreground
- **GIVEN** A finished one-step run; the guard noted 'complete CP1' from a host whose tool input has a command but no run_in_background field and no timeout field
- **WHEN** The agent runs `ratchet.py complete CP1`
- **THEN** The warning is printed (an unknown is not taken as background), and a note with a timeout but no background flag behaves the same; complete still finishes
<!-- proven by test CP3-T06 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: A regression re-run does not repeat the warning
- **GIVEN** A finished one-step run, a foreground note, and a regression command that fails first and passes on its immediate automatic re-run
- **WHEN** The agent runs `ratchet.py complete CP1`
- **THEN** The warning text appears exactly once across complete's output, and complete still succeeds and marks the regression result flaky as before
<!-- proven by test CP3-T07 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: A refused complete keeps its message and exit status; other commands print no warning
- **GIVEN** A fresh one-step project at the tests step (no gate passed), with a foreground note for 'complete CP1'
- **WHEN** The agent runs `complete CP1`, then `status`, `status --json` and `next`
- **THEN** complete is refused with the same non-zero status and the same plain reason as without any note, with no traceback; status, status --json and next never mention a timeout cut-off or running in the background, and status --json is still valid JSON
<!-- proven by test CP3-T08 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: The guard still lets a foreground complete through
- **GIVEN** A project with an active run and a foreground complete call
- **WHEN** The Bash call reaches the guard hook
- **THEN** The hook exits 0 with no refusal and prints no warning itself (noting the call never blocks it), and a call with a damaged or unwritable notes file still lets complete run
<!-- proven by test CP3-T09 in tests/test_complete_foreground.py, commit d42952b -->

#### Scenario: README and the orchestrator skill tell agents to run complete in the background
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md, and this test file
- **WHEN** Their text is read
- **THEN** Both name `complete` together with running it in the background (and the tool timeout reason) in the same passage, and README's list of test files names tests/test_complete_foreground.py
<!-- proven by test CP3-T10 in tests/test_complete_foreground.py, commit d42952b -->

### Requirement: Let a lane adjust tests it added
The system SHALL do this: After the red run, a fixer or test writer can change or remove a test this lane added during the run without an approve-changes question, while planned cases and everything they use stay frozen.

- After red, a fixer or test writer can change or remove a test this run added (one that is not a planned case and that no planned case uses), and the next gate goes on without an approve-changes question
- Any change to a planned case, to something a planned case uses (a helper, setUp, a fixture, an import or constant at the top of the file), or to a test that was in the file before this run started still stops the build and asks, with today's message
- A test the lane added can't take over the name of a planned case or of anything a planned case uses
- This stays true after an approve-changes re-snapshot of the test files
- README, schemas.md, the orchestrator skill and the fixer and test-writer instructions say what may change freely and what stays locked

#### Scenario: A fixer can change a test the run added, without a question
- **GIVEN** CP1 is built and green after red; after red the fixer appended test_extra_negative_quantity (not a planned case, not used by one) and the first behavior run passed
- **WHEN** The fixer changes that test's assertion from == 0 to == -1 and runs behavior again
- **THEN** behavior exits 0 with no 'since the red run' or approve-changes text, no pending approve-changes question is recorded, nothing is added to the human log, and the step moves on to its next gate
<!-- proven by test CP4-T01 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: A fixer can remove a test the run added, without a question
- **GIVEN** The same project, with test_extra_negative_quantity and a lane-added helper (make_bad_cart) used only by it, both appended after red
- **WHEN** The fixer deletes the test and then, in a second run, deletes its helper too, running behavior after each
- **THEN** Both behavior runs exit 0 with no question; the planned cases CP1-T01 and CP1-T02 are still found and still pass
<!-- proven by test CP4-T02 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: An extra test the test writer added before red is also free to change
- **GIVEN** Before the red run the test writer wrote test_extra_rounding in the same file; it is not in the plan, it is not in the file as it was when the run started, and no planned case calls it
- **WHEN** After red the fixer rewrites its body and, separately, deletes it, running behavior after each
- **THEN** Both behavior runs go on with no question, while the red run itself still counted only the planned cases
<!-- proven by test CP4-T03 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: A test the run added may use planned helpers; only the planned side stays locked
- **GIVEN** A lane-added test calls build_cart (a helper CP1-T01 uses) and a lane-added helper that only lane-added tests use
- **WHEN** The fixer edits the lane-added test and the lane-added helper (build_cart itself is untouched)
- **THEN** behavior goes on with no question. Then build_cart is edited as well, and behavior asks, naming the test file and the planned test that uses it
<!-- proven by test CP4-T04 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: Editing a planned case still stops the build and asks, with today's message
- **GIVEN** CP1 is past red; CP1-T01 is a planned case and a lane-added test also exists
- **WHEN** The fixer changes CP1-T01's assertion to a weaker one (alone, then together with an edit to the lane-added test), and also in a third try inserts an early return into it, running behavior each time
- **THEN** Every try exits non-zero naming the test file and test_cp1_t01 (CP1-T01), saying 'changed since the red run' and that a planned test can't change after its first run, and pointing to `ratchet.py ask approve-changes`; a lane-added edit made together with the planned edit does not make the refusal go away
<!-- proven by test CP4-T05 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: Helpers a planned case uses stay locked, including helpers of helpers
- **GIVEN** CP1-T01 calls build_cart, which calls price_of; a helper bulk_discount was written by the test writer before red and is called by CP1-T02
- **WHEN** The fixer edits build_cart, then (restored) price_of, then (restored) bulk_discount, or deletes one of them, running behavior each time
- **THEN** Each try asks with today's message naming the test file; none is treated as a free lane-added test even though bulk_discount is not in the plan and not in the file as the run started
<!-- proven by test CP4-T06 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: setUp and fixtures a planned case uses stay locked
- **GIVEN** CP1-T02 runs inside a test class whose setUp builds the cart, and (second variant file) uses a pytest-style fixture function by parameter name
- **WHEN** The fixer changes the setUp body (for example makes it return early) or the fixture body, running behavior each time
- **THEN** Each try asks with today's message; the same project accepts a new lane-added method added to the same class and a new lane-added fixture used only by a lane-added test
<!-- proven by test CP4-T07 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: Imports and constants at the top of the file that planned cases use stay locked
- **GIVEN** The planned file starts with an import of the source module and LIMIT = 5, used by CP1-T01
- **WHEN** The fixer changes LIMIT to 500, then (restored) drops or swaps the import line, then (restored) adds a brand-new unrelated constant for a lane-added test, running behavior each time
- **THEN** The first two asks with today's message; the third goes on with no question
<!-- proven by test CP4-T08 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: A test that was in the file before the run started still asks
- **GIVEN** tests/test_cart.py held test_legacy_totals, committed before the run; the planned cases were added below it; it is not a planned case, and the run did not add it
- **WHEN** The fixer edits test_legacy_totals's assertion, and separately deletes it, running behavior after each (file restored in between)
- **THEN** Both tries exit non-zero with today's 'lines changed or removed since the red run' text naming the file, and the approve-changes route; it is not treated as a free lane-added test
<!-- proven by test CP4-T09 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: A lane-added test can't take the name of a planned case or of anything one uses
- **GIVEN** CP1-T01 is a planned case that uses build_cart, LIMIT and the Cart class; lane-added tests are otherwise free
- **WHEN** The fixer appends, one at a time, a new def test_cp1_t01 with an empty body, a new def build_cart that returns a fixed value, a second assignment LIMIT = 0, a new class Cart, and a def test_legacy_totals duplicate of the old test, running behavior each time
- **THEN** Every try asks, saying the earlier definition is defined again or replaced; a new test with a fresh unused name in the same run still goes on freely
<!-- proven by test CP4-T10 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: After approve-changes re-snapshots the test files, lane-added tests stay free and locked things stay locked
- **GIVEN** A fix round added test_extra_negative_quantity; then the user approved a change to CP1-T02's assertion (ask approve-changes, the user's yes, approve-changes), which re-snapshots the test files
- **WHEN** The fixer edits and deletes test_extra_negative_quantity, then edits CP1-T02 (its newly approved form), build_cart and test_legacy_totals again, running behavior each time
- **THEN** Editing or deleting the lane-added test still goes on with no question (it did not become frozen by the re-snapshot); the other edits ask again with today's message, and the newly approved CP1-T02 text is the one now protected
<!-- proven by test CP4-T11 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: An earlier step's tests stay locked in a later step; the later step's own added tests are free
- **GIVEN** CP1 and CP2 share tests/test_cart.py; CP1 finished (its planned tests were added during this run, so they are not in the file as the run started); CP2 is past red with its planned CP2-T01
- **WHEN** In CP2's build the fixer edits CP1's planned test_cp1_t01, then (restored) edits a lane-added test added during CP2, running behavior each time
- **THEN** Editing CP1's test asks with today's message (it is a planned case of another step, never free); editing the CP2 lane-added test goes on with no question
<!-- proven by test CP4-T12 in tests/test_lane_added_tests.py, commit f6af5c4 -->

#### Scenario: Outside Python the documented limit holds: only tests added after red are free
- **GIVEN** A JavaScript test file (tests/cart.test.js) with planned CP1-T01 and CP1-T02 and one extra unplanned test written before red; after red the fixer appended a new test at the end of the file
- **WHEN** The fixer edits, then deletes, the test appended after red; separately edits the unplanned extra test that was in the file at red; and separately edits CP1-T01, running behavior each time
- **THEN** The appended test is free to edit and delete with no question; the unplanned test that was already in the file at red and CP1-T01 both ask with today's message, because without parsing the language the file as it stood at red stays locked as a whole; the docs name this limit (README or schemas.md says non-Python extras written before red stay frozen)
<!-- proven by test CP4-T13 in tests/test_lane_added_tests.py, commit f6af5c4 -->

### Requirement: Accept a merge of main between steps
The system SHALL do this: After a step is done, an agent can merge main into the lane and run one Ratchet command; Ratchet re-checks the earlier steps on the merged code and the lane carries on with no approval question.

- After a step completes, the agent merges main and runs `ratchet.py merged`; the lane carries on with no approval question
- Ratchet re-runs the earlier steps' checks (the regression command, or each completed step's own tests) on the merged code; if they fail, the run stops and says plainly which step's tests broke
- Reviewers of the next step see only that step's own changes, not what came from main, and its commit holds only its own changes
- Rule, standards or design-file changes that came with main, exactly as main has them, become the new baseline and are named in the output; the plan, test plans, spec and any other change still need approval
- `ratchet.py merged` in the middle of a step is refused with a plain message to finish the step first; README, the orchestrator skill (director section) and schemas.md describe the command

#### Scenario: After step 1 is done, merging main and running merged carries the lane on with no question
- **GIVEN** Lane at CP2 (building, step tests) after CP1 completed; main has M1-code-only; the lane merged main with git merge --no-ff; the user has said nothing since approving the plan
- **WHEN** The agent runs ratchet.py merged
- **THEN** It exits 0 and prints that the merge was checked and accepted; no ask or approve-changes was needed (no pending question in state.json, no new line in the human log, status does not say the run is waiting); phase is still building with CP2 the current step and CP1 still done; the run log has a merged_main event; the next command for CP2 (tests-written) is no longer blocked by integrity
<!-- proven by test CP5-T01 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: If main's change breaks an earlier step, the run stops and names that step's tests
- **GIVEN** Same lane, but main has M1-break so CP1's test (f() equals 1) fails on the merged code; CP1's own test command is the only check (no regression_command)
- **WHEN** The agent runs ratchet.py merged
- **THEN** It exits non-zero; the run is blocked (phase blocked) with a plain reason naming CP1 ('Price helper') and its test command 'node --test tests/cp1.test.js'; CP2 has not started its work (still step tests, no base_tree change); the message offers the existing blocked options (such as feedback to add a fix step) and does not tell the agent to approve anything
<!-- proven by test CP5-T02 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: When a regression command is set, it is the check that runs on the merged code
- **GIVEN** Config regression_command is a command that exits 0 only if src/m.js exists (main's file) and appends a line to a marker file outside the project; main has M1-code-only; the lane merged it
- **WHEN** The agent runs ratchet.py merged
- **THEN** It exits 0; the marker file got exactly one new line (the command ran once, on the merged files, which is why src/m.js existed); CP1's own test command was not run instead of it
<!-- proven by test CP5-T03 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: A failing regression command stops the run and is named as such
- **GIVEN** Same as the regression case, but the regression command exits 1 on the merged code while CP1's own test still passes
- **WHEN** The agent runs ratchet.py merged
- **THEN** It exits non-zero and the run is blocked; the reason names the regression command (not CP1's tests) and says an earlier behaviour broke on the merged code; the failing run's log is kept under the evidence folder
<!-- proven by test CP5-T04 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: The next step's reviewers see only that step's own changes, not what came from main
- **GIVEN** Lane merged M1 (src/m.js and a standards line) and merged accepted it; the test then writes CP2's own files tests/cp2.test.js and src/cart.js
- **WHEN** The test lists what CP2's review would diff: the changed files between CP2's base_tree and the current fingerprint (the same comparison adv-record uses), and the files_reviewed an honest default review lists
- **THEN** The list is exactly tests/cp2.test.js and src/cart.js; src/m.js, .ratchet/standards.md and CP1's files are not in it; a review that lists only main's src/m.js is not accepted as covering CP2's changes
<!-- proven by test CP5-T05 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: The next step's commit holds only its own changes
- **GIVEN** Same lane after merged; CP2 is taken through its gates with its own two files and completed
- **WHEN** The test reads the commit complete made for CP2 (git show --name-only) and the branch history
- **THEN** CP2's commit lists only tests/cp2.test.js, src/cart.js and the shared .ratchet files Ratchet normally commits; it does not list src/m.js or any file that came from main; the merge commit is still in the lane's history and main's M1 is an ancestor of HEAD (nothing was rewritten or squashed)
<!-- proven by test CP5-T06 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: Standards that came with main become the new baseline and are named
- **GIVEN** Main's M1 appended 'No magic numbers' to .ratchet/standards.md; the lane merged it with no conflict, so the file equals main's version. Before merged is run, any other Ratchet command (for example tests-written CP2) refuses with 'reference file .ratchet/standards.md' changed after approval
- **WHEN** The agent runs ratchet.py merged, then tests-written CP2
- **THEN** merged exits 0 and its output names .ratchet/standards.md as accepted from main; tests-written CP2 now works with no approve-changes; reviewers' standards for CP2 include the new line; the same holds for one config rule changed on main (for example max_behavior_attempts raised) and is named too
<!-- proven by test CP5-T07 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: A change that did not come from main as main has it still needs approval
- **GIVEN** Lane merged M1, and the agent resolved a conflict in .ratchet/config.json by keeping its own value, so a rule (regression_command removed, or max_behavior_attempts lowered) differs from main's version of the file. Second variant: the plan's CP2 done_when line was edited in checkpoints.json before the merge
- **WHEN** The agent runs ratchet.py merged, then tries tests-written CP2
- **THEN** The differing config rule (and in the second variant checkpoint CP2) is still named as changed after approval and needs approve-changes (the user's yes); merged does not silently re-baseline it; the standards line that did arrive exactly as main has it is still accepted and named; the lane does not carry on past the approval
<!-- proven by test CP5-T08 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: merged in the middle of a step is refused with a plain message to finish the step
- **GIVEN** CP2 is in progress: its tests are written and tests-written has run; the agent then merges main (M1-code-only) and runs merged
- **WHEN** The agent runs ratchet.py merged
- **THEN** It exits non-zero with a plain message saying to finish the step (complete CP2) first; state.json is unchanged (CP2's base_tree and the approved snapshot are the same as before, no merged_main event); the same refusal holds if CP2 has passed red and implemented
<!-- proven by test CP5-T09 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: merged with no finished merge to check is refused and changes nothing
- **GIVEN** At the step boundary, three situations in turn: (a) the agent runs merged without having merged anything (HEAD is an ordinary commit); (b) it ran git merge --no-commit main (or left a conflict unresolved) and the merge is not finished; (c) the merge is done but a tracked file was edited afterwards and not committed
- **WHEN** The agent runs ratchet.py merged in each
- **THEN** Each time it exits non-zero with a plain message saying what is missing (merge main and finish the merge, or commit the edit) and state.json is unchanged; after finishing the merge properly merged is accepted as in the first case
<!-- proven by test CP5-T10 in tests/test_merge_main.py, commit 0a02eed -->

#### Scenario: merged while a step review waits is accepted and does not count as the user's review
- **GIVEN** human_review is 'each', so after CP1 completes the run is in awaiting_checkpoint_review; the agent merges main (M1-code-only) and runs merged
- **WHEN** The agent runs ratchet.py merged
- **THEN** It exits 0 and the merge is accepted and re-checked; the phase is still awaiting_checkpoint_review and CP1's human gate is still not passed (merged approved nothing); the later human-approve with the user's yes still moves the run to CP2, whose base_tree is the merged fingerprint so main's files are not part of CP2's own changes
<!-- proven by test CP5-T11 in tests/test_merge_main.py, commit 0a02eed -->

### Requirement: Steer merges to happen only between steps
The system SHALL do this: Between steps the guard lets an agent merge main and resolve conflicts in Ratchet's shared files; mid-step it refuses with a plain message, and next points the agent to `ratchet.py merged`.

- Between steps, an agent can run `git merge main`, resolve a conflict in Ratchet's shared files (.ratchet/config.json, standards.md, learnings.md, .gitignore) with git, and finish the merge without the guard refusing
- In the middle of a step, `git merge` and `git pull` are refused with a plain message to finish the step (complete) first
- When a merge landed that Ratchet has not checked yet, next tells the agent to run `ratchet.py merged`, and the step's first command (tests-written) refuses until it has
- Every other guard rule behaves as before, and README, the orchestrator skill and schemas.md (Hooks section) describe the merge rule

#### Scenario: Between steps, an agent may merge or pull main
- **GIVEN** A two-step run where CP1 is complete and CP2 has not started its work (nothing changed since CP1's commit); main has one extra commit adding src/upstream.js
- **WHEN** The guard is asked about `git merge main`, `git pull origin main` and `git merge --no-ff main`, and then git really runs `git merge main`
- **THEN** The guard allows all three (exit 0, no message), and the real merge succeeds with src/upstream.js in the lane
<!-- proven by test CP6-T01 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: At the very start of the first step, a merge is also allowed
- **GIVEN** A one-step plan just approved, CP1 current with nothing written or changed yet, and main one commit ahead
- **WHEN** The guard is asked about `git merge main`
- **THEN** The guard allows it (exit 0), because the step has no work of its own yet
<!-- proven by test CP6-T02 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: Between steps, a conflict in Ratchet's shared files can be resolved with git
- **GIVEN** CP2 current at the step boundary; `git merge main` was run for real and stopped on conflicts in .ratchet/config.json, .ratchet/standards.md, .ratchet/learnings.md and .ratchet/.gitignore, so MERGE_HEAD exists
- **WHEN** The guard is asked about `git checkout --theirs .ratchet/config.json`, `git checkout --ours .ratchet/learnings.md`, `git checkout main -- .ratchet/standards.md`, `git add .ratchet/config.json .ratchet/standards.md .ratchet/learnings.md .ratchet/.gitignore`, `git commit --no-edit` and `git merge --continue`; then git really resolves and commits the merge
- **THEN** The guard allows every one of those commands (exit 0), and the real merge ends as a merge commit with no conflict markers left in the four files; `git merge --abort` is also allowed at that point
<!-- proven by test CP6-T03 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: In the middle of a step, merge and pull are refused with a plain message
- **GIVEN** CP2 is current and its tests were written and recorded (`tests-written CP2` ran, then `red CP2`)
- **WHEN** The guard is asked about `git merge main` and `git pull origin main`, at each of those two points
- **THEN** Both are refused (exit 2) with a message that tells the agent to finish the step first and names `complete`; the message is not the generic 'could change Ratchet's run state' text, and the working tree and branch are untouched
<!-- proven by test CP6-T04 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: Test files already written for the step count as mid-step
- **GIVEN** CP2 is current and the test writer has saved tests/cp2.test.js, but `tests-written CP2` has not been run yet, so the working tree differs from the last commit
- **WHEN** The guard is asked about `git merge main`
- **THEN** It is refused (exit 2) with the same finish-the-step message, so uncommitted step work is never mixed into a merge
<!-- proven by test CP6-T05 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: While the run waits for the final review, a merge is allowed
- **GIVEN** A one-step run where CP1 is complete and the run is waiting for the final review (phase awaiting_final_review)
- **WHEN** The guard is asked about `git merge main`, and then about `git checkout --theirs .ratchet/standards.md` after a real conflicting merge
- **THEN** Both are allowed (exit 0)
<!-- proven by test CP6-T06 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: A merge Ratchet has not checked yet is pointed out by next and blocks tests-written
- **GIVEN** Between steps, `git merge main` was run for real and committed (HEAD is a merge commit), and the agent has not run `ratchet.py merged`
- **WHEN** The agent runs `next` and then `tests-written CP2`
- **THEN** next's action tells the agent to run `ratchet.py merged`; tests-written exits non-zero with a message that names `merged` as the command to run first, and the step's recorded state is unchanged (still building, tests not recorded)
<!-- proven by test CP6-T07 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: Once merged has passed, next and tests-written carry on as normal
- **GIVEN** The same merged-but-unchecked lane as CP6-T07
- **WHEN** The agent runs `ratchet.py merged`, then `next`, then writes CP2's tests and runs `tests-written CP2`
- **THEN** merged succeeds, next no longer mentions `merged`, and tests-written succeeds, so the step proceeds
<!-- proven by test CP6-T08 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: The merge allowance is narrow: run state and everything else stay protected
- **GIVEN** Two situations at the step boundary. (a) No merge in progress (no MERGE_HEAD). (b) A conflicted merge in progress (MERGE_HEAD exists)
- **WHEN** In (a) the guard is asked about `git add .ratchet/config.json` and `git checkout --theirs .ratchet/config.json`; in (b) it is asked about `git checkout main -- .ratchet/runs/f/state.json`, `git add .ratchet/runs/f/state.json`, `git checkout --theirs .ratchet/runs/f/checkpoints.json`, `echo x > .ratchet/config.json`, `sed -i s/a/b/ .ratchet/standards.md`, `git stash -u` and `git clean -fdx`
- **THEN** Every one of those is still refused (exit 2) with the guard's existing message for that kind of command; only plain git commands on the four shared files are newly allowed, and only while a merge is in progress between steps
<!-- proven by test CP6-T09 in tests/test_merge_guard.py, commit ed4bcb3 -->

#### Scenario: A lane that never merges sees no change
- **GIVEN** A two-step run mid-step (CP2 after tests-written) and another at the boundary, neither with a merge in history
- **WHEN** The guard is asked about `git status`, `git log --oneline`, `git diff`, `git checkout -b scratch` and `git commit -m x` mid-step, and `next` is run at both points
- **THEN** The git commands are allowed exactly as before (exit 0), no message mentions merging, and next's action never mentions `merged`
<!-- proven by test CP6-T10 in tests/test_merge_guard.py, commit ed4bcb3 -->

### Requirement: Close three gaps found in director QA
The system SHALL do this: Tests that came in from main stay frozen, merged never skips its check for unreviewed lane commits, and a question that was never shown is not reported as waiting.

- A test that reached a planned test file from main (through any merge, including one made before the plan was approved) stays frozen after the red run: changing or removing it asks for approval exactly like a planned case, while a test this lane's own fixer or test writer added is still free
- With commit_per_checkpoint set to false, running merged between steps after the lane made a commit no step reviewed is refused with a plain reason naming those files; it never succeeds silently
- A question that was recorded but printed as NOT ASKED (it replaced another question still waiting for its first reply) is not reported as waiting by status, status --json or next; the question it replaced is not reported either

#### Scenario: A test that came from main before the plan was approved stays frozen
- **GIVEN** A Python project whose base commit holds tests/test_cart.py with one old test. After `init`, a branch `main` adds a second test, test_from_main (asserts 3 + 3 == 6), to that file; the lane makes its own commit, runs a real `git merge --no-ff main`, then makes one more commit (so HEAD is not the merge itself and tests-written is not stopped by the unchecked-merge notice). The plan is approved, the test writer saves the file with main's test kept plus the two planned cases, tests-written and red run, the implementation goes in, and the first behavior run passes
- **WHEN** A fixer changes test_from_main to assert 3 + 3 >= 6 and runs behavior CP1
- **THEN** behavior exits non-zero with the engine's line 'These changed after the user approved the plan' naming tests/test_cart.py and the approve-changes route; the step does not move on to adversarial and no approval is recorded. Fails on today's code: the merge came in after the run's base tree was taken, so main's test counts as lane-added and the edit goes through
<!-- proven by test CP7-T01 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: Removing a test that came from main is also asked about
- **GIVEN** The same merged-before-approval project as CP7-T01, built and at a passing behavior run
- **WHEN** A fixer deletes test_from_main from tests/test_cart.py and runs behavior CP1
- **THEN** behavior is refused with the same 'These changed after the user approved the plan' line naming tests/test_cart.py; the file on disk is not touched by Ratchet and CP1 stays at its step. Fails on today's code (the deletion goes through)
<!-- proven by test CP7-T02 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: A fast-forward of main before the plan was approved is also frozen
- **GIVEN** Same project, but the lane has no commit of its own: after `init` the lane fast-forwards to main (git merge --ff-only main), which adds test_from_main to tests/test_cart.py; then plan approval, the test writer, tests-written, red, implementation and a passing first behavior run
- **WHEN** A fixer changes test_from_main's assertion and runs behavior CP1
- **THEN** behavior is refused with the 'These changed after the user approved the plan' line naming tests/test_cart.py. Fails on today's code
<!-- proven by test CP7-T03 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: A merge does not lock the lane's own added tests, and planned cases stay locked
- **GIVEN** The merged-before-approval project of CP7-T01 at a passing behavior run; the fixer then appends a helper make_bad_cart and a test test_extra_negative_quantity (new this run, used by no planned case) to tests/test_cart.py and behavior passes
- **WHEN** The fixer changes the added test's assertion and runs behavior CP1; then removes the added test and helper; then, separately, edits a planned case (CP1-T01's assertion) and runs behavior
- **THEN** The first two behavior runs exit 0 with no approve-changes question pending and nothing added to the human log; the third is refused with the 'These changed after the user approved the plan' line. Guard against over-locking: this passes on today's code and must keep passing
<!-- proven by test CP7-T04 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: A test brought in by `merged` between steps is frozen once the next step's tests are red
- **GIVEN** A two-step project (tests/py_runner.py style, CP2's planned file tests/test_cart.py). CP1 is complete; main adds test_from_main to tests/test_cart.py; the lane merges with `git merge --no-ff main` and runs `merged` (exit 0); the test writer saves the file with main's test kept plus CP2's planned case; tests-written, red, implementation and a passing behavior run follow
- **WHEN** A fixer changes test_from_main's assertion and runs behavior CP2
- **THEN** behavior is refused with the 'These changed after the user approved the plan' line naming tests/test_cart.py. Guard for the path merged already covers: passes on today's code and must keep passing
<!-- proven by test CP7-T05 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: With commit-per-checkpoint off, merged refuses lane commits no step reviewed
- **GIVEN** A project with config commit_per_checkpoint=false and human_review each. CP1 is taken through every gate and completed, so complete commits nothing and the run waits for the step review. The lane then commits by hand everything in the working tree (the reviewed src/f.js and tests/cp1.test.js) together with an edit to src/a.js and a new file src/extra.js that no step reviewed; main (forked earlier, adds src/m.js) is merged with `git merge --no-ff main`; the tree is clean
- **WHEN** The agent runs `ratchet.py merged`
- **THEN** merged exits non-zero with the engine's own refusal line 'The lane has commits since the last step finished that never went through the gates', which names src/a.js and src/extra.js and does not name src/f.js or tests/cp1.test.js (the reviewed work); state.json is unchanged (phase awaiting_checkpoint_review, no merged_main event, base trees unchanged). Fails on today's code: merged exits 0 and logs a merged_main event, because no commit was recorded to compare against
<!-- proven by test CP7-T06 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: With commit-per-checkpoint off, committing exactly the reviewed work is still accepted
- **GIVEN** The same commit_per_checkpoint=false project at the waiting step review; the lane commits by hand only what the step's reviewers saw (git add -A, no other edit) and merges main with `git merge --no-ff main`
- **WHEN** The agent runs `ratchet.py merged`
- **THEN** merged exits 0, logs exactly one merged_main event, and the run stays in the waiting phase with no question pending. Guard against over-refusing: this passes on today's code and must keep passing
<!-- proven by test CP7-T07 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: A question printed as NOT ASKED is not reported as waiting
- **GIVEN** A building project (CP1 at its tests step). `ask set-review each` was printed and is waiting for its first reply; then `ask cancel` is run, which prints the 'NOT ASKED: a different question ... was still waiting' line and records the cancel question as overtaking
- **WHEN** The agent runs status, status --json and next
- **THEN** status prints no 'waiting for the user's answer' line (and nothing about a set-review or cancel question); the `question` entry of status --json and of next is null or absent for both; the question it replaced (set-review) is not reported either. state.json still holds the overtaking cancel entry, so no reply can approve it. Fails on today's code: all three report the cancel question as waiting since the time it was recorded
<!-- proven by test CP7-T08 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: A real waiting question is still reported, including one asked again after NOT ASKED
- **GIVEN** The building project of CP7-T08: first, only `ask set-review each` is waiting; later, after the NOT ASKED cancel, the agent runs `ask cancel` again, which prints the real question (ASK THE USER) this time
- **WHEN** The agent runs status, status --json and next after each of those two points
- **THEN** Both times status has its one waiting line, and status --json and next carry a question entry with the right kind (set-review, then cancel), waiting_on 'user' and 'since' equal to that entry's asked_at in state.json (the second 'since' is later than the first). Guard against hiding real questions: passes on today's code and must keep passing
<!-- proven by test CP7-T09 in tests/test_feedback_fixes.py, commit 36ace71 -->

#### Scenario: A reply cannot approve a question that was printed as NOT ASKED
- **GIVEN** The building project after `ask set-review each` then `ask cancel` printed NOT ASKED, and the user then replies 'yes, go ahead' in the lane's chat
- **WHEN** The agent runs the command that records a yes for the cancel question
- **THEN** It is refused with the engine's line 'This question was printed while another one (shall I change how often they review ...' and tells the agent to print the question again on its own; the run is not cancelled and the phase is still building. Keeps the existing behavior that the fix to status must not change; passes on today's code
<!-- proven by test CP7-T10 in tests/test_feedback_fixes.py, commit 36ace71 -->
