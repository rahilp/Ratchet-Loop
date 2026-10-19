# Done means proven line by line: each done-means line of a plan maps to the tests that prove it; the final review shows a table of proven and unproven lines; an unproven line, or an open finding against a line, blocks the finish instead of being filed as minor; and for guard-like features Ratchet runs a fixed table of shortcut commands against the old and new versions and shows the difference.

## Purpose
What this feature does, as built and checked by Ratchet (run done-means-proven-line-by-line-each-done-means-l, 2026-10-19). Each scenario names the test that proves it.

## Requirements

### Requirement: Final review shows a proven or unproven table
The system SHALL do this: At the final review the agent sees every done-means line marked proven or unproven, with the tests behind it and the reason a line is unproven.

- At the final review, status lists each done-means line as proven or unproven, with the linked tests and their results
- An unproven line says why: no test linked, a linked test failed or has no result, or an open finding names the line
- status --json carries the same table, so the director and the approval question read one source
- A run with no objective, or with no tests linked to any line, gets a plain note instead of a table or a crash

#### Scenario: Final review lists every done-means line as proven, with its tests and results
- **GIVEN** A finished one-step run (awaiting final review) with 4 done-means lines; cases CP1-T01 (proves line 1), CP1-T02 and CP1-T03 (both prove line 2), CP1-T04 (proves lines 3 and 4) all marked passed in the step's test report
- **WHEN** The agent runs status (plain text)
- **THEN** Each of the 4 lines appears with its own number and full text and is marked proven (the word 'unproven' appears nowhere); line 2's entry names both CP1-T02 and CP1-T03 with 'passed'; CP1-T04 appears under both line 3 and line 4; the old neutral 'check: <line>' prompts are gone and the heading no longer tells the agent to judge each line itself
<!-- proven by test CP1-T01 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: An unproven line says why: no test linked, a failed test, or no result
- **GIVEN** Finished run, 4 lines: line 1 linked to a passed case; line 2 linked to no case; line 3 linked to CP1-T03 whose report entry says failed; line 4 linked to CP1-T04 with no report at all and no recorded behavior-gate result for its step (state hand-edited)
- **WHEN** The agent runs status (plain text)
- **THEN** Line 1 is proven. Line 2 is unproven and the reason reads 'no test linked'. Line 3 is unproven and the reason names CP1-T03 and that it failed (never 'no test linked'). Line 4 is unproven and the reason names CP1-T04 and that it has no result. The three reasons are different from each other, so a stub that prints one generic reason fails
<!-- proven by test CP1-T02 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A minor finding that names a line makes it unproven even when every test passes
- **GIVEN** Finished run, 4 lines, every linked case passed. At the low-risk step, a real adv-record round deferred the minor finding S1 ('lockout message never shown') whose 'done_means' field is 3
- **WHEN** The agent runs status (plain text)
- **THEN** Line 3 is unproven although its case passed; its reason names the open finding S1 and its description; lines 1, 2 and 4 stay proven and mention no finding. This is the lane C miss: a finding filed as minor hid an unmet line
<!-- proven by test CP1-T03 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A line shows every reason at once, and a fixed or accepted finding stops counting
- **GIVEN** Same run as CP1-T03 but line 3's case also failed. Then the state is edited twice: S1 marked fixed (as ratchet.py mark-fixed records it), and separately S1 marked accepted with a reason
- **WHEN** The agent runs status after each state, and runs deferred --json
- **THEN** With S1 open and the case failed, line 3 lists both reasons (the failed test and the open finding). With S1 fixed, the finding reason disappears and only the failed test remains; with S1 accepted likewise. The set of open findings the table uses equals the left-for-later entries in deferred --json, so the table and deferred never disagree
<!-- proven by test CP1-T04 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A finding naming a line in either shape counts; a bad line number is ignored plainly
- **GIVEN** Finished run, 4 lines, every case passed. Findings deferred: S1 with 'done_means': 2 (a single number), S2 with 'done_means': [1, 4] (a list), S3 with 'done_means': 9 (no such line), S4 with 'done_means': 'two' (not a number), S5 with no 'done_means' field
- **WHEN** The agent runs status and status --json
- **THEN** Lines 1, 2 and 4 are unproven and name S2, S1 and S2 respectively; line 3 stays proven; S3, S4 and S5 mark nothing; both commands exit 0 with no traceback
<!-- proven by test CP1-T05 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: status --json carries the same table the text shows
- **GIVEN** The mixed run of CP1-T02 plus the open finding of CP1-T03 on line 1
- **WHEN** The agent runs status --json and status (plain text)
- **THEN** status --json has 'done_means_table' with 4 entries in order; each has the line's number, text, 'verdict' ('proven' or 'unproven'), the linked cases with id and result, and the 'reasons' list. For every line the verdict equals the one the plain text shows, and the reasons name the same case ids and finding id. Nothing else in status --json changed (phase, next, objective keys are still there)
<!-- proven by test CP1-T06 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: The proof receipt gives the same verdict as status for every line
- **GIVEN** The run of CP1-T06
- **WHEN** The agent runs receipt --json and receipt (plain text)
- **THEN** Each done_means line in receipt --json carries the same 'verdict' and 'reasons' as the status table, including the open-finding reason; the plain receipt marks line 1 unproven too, so one verdict function feeds both. The receipt's existing per-case lines (id, title, file, result) are still present
<!-- proven by test CP1-T07 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A run with no objective gets a plain note, not a table or a crash
- **GIVEN** An older plan with no 'objective' at all, approved through the legacy path, moved to awaiting final review
- **WHEN** The agent runs status, status --json and receipt (plain and --json)
- **THEN** All four calls exit 0 with no traceback; status prints the plain 'no objective was stated' note and no table and no 'unproven' word; status --json's table is null or the same note; receipt still prints its existing note
<!-- proven by test CP1-T08 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A plan with an objective but no linked tests gets a plain note instead of a table
- **GIVEN** A run approved before test linking existed: an objective with 4 lines, test plan cases with no 'proves' field anywhere, finished and at the final review
- **WHEN** The agent runs status and status --json
- **THEN** Both exit 0; status says plainly that no tests are linked to any done-means line in this plan (an older plan) and still lists the 4 lines; it does not print four 'unproven' verdicts or crash; status --json carries the same note in place of a table. Contrast with CP1-T02, where one unlinked line among linked ones does get a table entry
<!-- proven by test CP1-T09 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A step checked by exit code only still proves its linked line, and says so
- **GIVEN** A finished one-step run whose plan names no per-test report (no tests.report), CP1-T01 links to line 1, the step's behavior gate passed, and no test-report file exists
- **WHEN** The agent runs status
- **THEN** Line 1 is proven and its tests entry says passed by exit code only (not checked per case); a second line linked to a case in a step whose behavior gate is recorded failed shows unproven with the reason 'failed by exit code only'
<!-- proven by test CP1-T10 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A browser case proves its line only when the UI gate passed
- **GIVEN** Two finished one-step runs, each with a browser-layer case CP1-T05 linked to line 2. In the first, the step's UI gate is recorded passed; in the second the step has no UI gate (ui: false, hand-edited state)
- **WHEN** The agent runs status for each
- **THEN** First run: line 2 is proven and the entry says checked by the UI gate. Second run: line 2 is unproven and the reason says no UI gate checked it; a stub that treats every browser case as proven fails
<!-- proven by test CP1-T11 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: A line proven across two steps needs all of its tests; a finding in another step counts
- **GIVEN** Two-step run, both finished. Line 1 is linked to CP1-T01 (passed) and CP2-T01 (failed); line 2 is linked to CP2-T02 (passed) but step CP1 holds a deferred finding naming line 2; line 3 is linked to CP1-T03 and CP2-T03, both passed
- **WHEN** The agent runs status
- **THEN** Line 1 is unproven and the reason names CP2-T01 only (CP1-T01 is listed as passed); line 2 is unproven and names the finding even though it was raised in the other step; line 3 is proven with both cases listed under it
<!-- proven by test CP1-T12 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: The table appears only at the final review
- **GIVEN** A two-step run with step 1 done and step 2 building, with a done-means line linked and passed in step 1; separately the same run after the last step completes
- **WHEN** The agent runs status in each phase
- **THEN** Mid-run status shows no proven or unproven table and no per-line verdicts (its existing wording is unchanged); after the last step completes, status at the final review shows the table. Checking the phase keeps the table from claiming a half-built run is proven
<!-- proven by test CP1-T13 in tests/test_dm_table.py, commit 32e2c06 -->

#### Scenario: The docs describe the table in every place that described the old prompts
- **GIVEN** The repo's README.md, skills/ratchet-orchestrator/SKILL.md final-review step and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** The test reads the three files
- **THEN** Each says the final review shows every done-means line as proven or unproven with its tests and the reason; schemas.md names status --json's 'done_means_table', the 'done_means' finding field and the verdict fields; the orchestrator SKILL.md no longer tells the agent that status prints 'check:' prompts and to assess each line itself; none of the three still claims the final review carries 'no met or not-met claim'
<!-- proven by test CP1-T14 in tests/test_dm_table.py, commit 32e2c06 -->

### Requirement: Plans must link every done-means line
The system SHALL do this: A new plan whose done-means line has no test case proving it is refused before approval, with the line named, so every line maps to its tests from the start.

- Submitting a plan where some done-means line has no linked test case is refused, naming that line by number and text
- A plan where every line is linked submits as before, and plan-summary shows each line with its linked case ids
- The test planner's instructions and its brief say every line needs at least one linked case
- The existing test suite still passes, with its sample plans linking their done-means line

#### Scenario: A plan with a done-means line no test proves is refused, naming the line
- **GIVEN** Three done-means lines; CP1's test plan has CP1-T01 proving [1] and CP1-T02 proving [2]; nothing proves line 3 (the lockout line)
- **WHEN** The agent runs plan-submit
- **THEN** It exits non-zero with 'Plan invalid', the refusal names line 3 by number AND its exact text ('After 5 wrong attempts in a row, the account is locked for 15 minutes'), tells the agent to add a case proving it (the "proves" field), and does not name lines 1 or 2
<!-- proven by test CP2-T01 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: A refused plan changes nothing and cannot be approved
- **GIVEN** The plan from CP2-T01 (line 3 unlinked), run in planning phase
- **WHEN** plan-submit is refused, then the user says 'yes, go ahead' after an approve-plan question and the agent runs approve-plan
- **THEN** state.json phase is still 'planning' (never 'awaiting_plan_approval'), no plan_hash was stored, no plan_submitted entry was logged, and approve-plan is refused, so an unlinked plan can never reach approval
<!-- proven by test CP2-T02 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: Every unlinked line is named, not just the first
- **GIVEN** Three done-means lines; the only case proves [2]
- **WHEN** plan-submit runs
- **THEN** The refusal names line 1 with its text and line 3 with its text, each by number, and does not name line 2
<!-- proven by test CP2-T03 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: A plan whose every line is linked submits as before
- **GIVEN** Two checkpoints; CP1-T01 proves [1], CP1-T02 proves [2], CP2-T01 proves [3]
- **WHEN** plan-submit runs
- **THEN** It exits 0, prints 'Plan valid (2 checkpoints)', state.json phase is 'awaiting_plan_approval' with a plan_hash, and the VIEWER line still prints
<!-- proven by test CP2-T04 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: One case can link several lines, and a line can have several cases
- **GIVEN** One case with proves [1, 2, 3] in CP1; and in a second project line 1 proved by CP1-T01 and CP2-T01, lines 2 and 3 by CP1-T02
- **WHEN** plan-submit runs on each
- **THEN** Both submit with exit 0 (a single case linking all three lines is enough; extra cases per line are fine)
<!-- proven by test CP2-T05 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: plan-summary shows each done-means line with the case ids that prove it
- **GIVEN** A submitted plan: line 1 proved by CP1-T01 and CP2-T02, line 2 by CP1-T02 only, line 3 by CP2-T01 only, and CP2-T01 also proves line 2
- **WHEN** The agent runs approve-plan flow then plan-summary
- **THEN** Exit 0; reading the text under each done-means line (up to the next line's text), line 1 lists CP1-T01 and CP2-T02, line 2 lists CP1-T02 and CP2-T01, line 3 lists CP2-T01; no 'Heads up ... no test case proving it' appears for any line
<!-- proven by test CP2-T06 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: The agent can fix the plan and submit again
- **GIVEN** The refused plan from CP2-T01
- **WHEN** A case proving [3] is added to CP1's test plan and plan-submit runs again
- **THEN** Second run exits 0 and the phase becomes 'awaiting_plan_approval'
<!-- proven by test CP2-T07 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: An empty or missing 'proves' does not count as a link
- **GIVEN** Line 3's only candidate cases carry 'proves': [] , or no proves key at all, or only a title that mentions the lockout
- **WHEN** plan-submit runs
- **THEN** Refused naming line 3; a naive stub that accepts any plan with any case, or that matches on text, fails this
<!-- proven by test CP2-T08 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: A bare number and a browser-layer case both count as links
- **GIVEN** Line 1 proved by a case with scalar 'proves': 1, line 2 by a browser-layer case with 'proves': [2], line 3 by a code case with 'proves': [3]
- **WHEN** plan-submit runs
- **THEN** It exits 0 (scalar normalised as [1] by _proves_values :1681; a UI-checked case is a real link)
<!-- proven by test CP2-T09 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: A bad number and a missing link are both reported together
- **GIVEN** A case proving [9] (past the end of a 3-line list), and line 2 and line 3 proved by nothing
- **WHEN** plan-submit runs
- **THEN** One refusal lists the out-of-range number for its case (existing wording from proves_problems) AND names done-means lines 2 and 3 with their text; no traceback
<!-- proven by test CP2-T10 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: A line proved only by a step with no test plan file is refused without a crash
- **GIVEN** CP2 has gates.behavior false and no tests/CP2.json; CP1 proves lines 1 and 2; nothing else proves line 3
- **WHEN** plan-submit runs
- **THEN** Refused naming line 3 with no traceback (test_plan returns None, ratchet.py:2230); and when CP1 proves all three lines the same two-step plan submits fine
<!-- proven by test CP2-T11 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: Two lines with identical wording are told apart by number
- **GIVEN** done_means has line 1 'Works' and line 2 'Works'; only line 1 is proved
- **WHEN** plan-submit runs
- **THEN** Refused naming line 2 by number; a plan proving both submits
<!-- proven by test CP2-T12 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: A plan with no objective is refused for the objective only, not for links
- **GIVEN** checkpoints.json with no objective key
- **WHEN** plan-submit runs
- **THEN** Refused with the existing objective message; no traceback and no 'done-means line' linking message (there are no lines to link)
<!-- proven by test CP2-T13 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: A plan approved before this change, with an unlinked line, still works with a plain note
- **GIVEN** A run whose plan was approved earlier (fabricated with the engine's pure helpers, skipping plan-submit) with line 3 unlinked
- **WHEN** The agent runs plan-summary, status and next
- **THEN** All exit 0 with no traceback; plan-summary carries a plain note naming line 3 (number and text) as having no linked test case yet, worded as a note, not a refusal, and nothing in the output says the run is blocked
<!-- proven by test CP2-T14 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: An older approved run can still take feedback and add a step
- **GIVEN** The legacy run from CP2-T14 (line 3 unlinked); the user gives feedback and the agent appends CP2 whose test plan proves nothing
- **WHEN** The agent runs feedback and then plan-extend
- **THEN** plan-extend exits 0 and starts CP2 as before; the new rule does not refuse a run approved before it (the rule is a plan-submit check only)
<!-- proven by test CP2-T15 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: The test planner is told every done-means line needs a linked case
- **GIVEN** skills/ratchet-tdd-planner/SKILL.md, agents/ratchet-test-planner.md, skills/ratchet-orchestrator/references/schemas.md section 4 and README.md
- **WHEN** Their text is read
- **THEN** SKILL.md and the agent file each say that every objective done_means line needs at least one case whose "proves" names it and that plan-submit refuses a plan with an unlinked line; schemas.md no longer says an unproven line is only a heads-up 'without refusing the plan' and documents the refusal; README mentions it where it describes plan approval or the plan summary; SKILL.md no longer says a case may leave proves out as the general rule (only a case that proves none of the lines)
<!-- proven by test CP2-T16 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: The test planner's brief carries the same rule
- **GIVEN** A run with a plan; the agent runs brief CP1 --role test-planner (and --role ratchet-test-planner)
- **WHEN** The brief prints
- **THEN** After the objective it says every done-means line needs at least one linked case via "proves" (and that plan-submit refuses otherwise); the other previously generic roles (test-runner, prototyper, variant-writer) still print the generic line unchanged
<!-- proven by test CP2-T17 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: The shared sample plans the other suites build still submit
- **GIVEN** tests/test_gates.py project() with 1 and 3 steps, with a high-risk step, and tests/trust_helpers.py's plan builder; tests/_tmp.py OBJECTIVE_MIN
- **WHEN** Each builder is called (it runs the real plan-submit)
- **THEN** Each returns a submitted project (phase awaiting_plan_approval); OBJECTIVE_MIN has exactly one done_means line and every case a builder writes proves [1], so existing suites' plans link their done-means line without any test being changed otherwise
<!-- proven by test CP2-T18 in tests/test_dm_plan.py, commit c706213 -->

#### Scenario: No suite builds a plan with an objective and forgets to link it
- **GIVEN** Every tests/test_*.py and tests/trust_helpers.py file on disk (including the files lanes B, C, F and H bring in)
- **WHEN** Each file that uses OBJECTIVE_MIN or writes a plan with an 'objective' key is scanned
- **THEN** Each such file also writes a 'proves' link somewhere (or builds its plan only through test_gates.project / trust_helpers, which CP2-T18 covers); a file that submits a plan with an objective but never mentions proves fails, naming the file
<!-- proven by test CP2-T19 in tests/test_dm_plan.py, commit c706213 -->

### Requirement: Findings against a done-means line always block
The system SHALL do this: A reviewer can name the done-means line a finding contradicts, and such a finding blocks its step at any severity and round instead of being filed as minor or left for later.

- A minor finding that names a done-means line blocks the step, even past the round where minor findings stop blocking
- Such a finding is not set aside for being outside the changed code, for an automatic check covering its kind, or for having no user impact line
- Every reviewer's instructions and reply shape ask for the done-means line a finding contradicts, and say to name one only when that line is not met in normal use
- Findings that name no line follow exactly today's rules

#### Scenario: A minor finding that names a done-means line blocks even on a low-risk step
- **GIVEN** A low-risk step (only blocker and major findings normally block) in review round 1. The reviewer reports minor finding N1 on src/f.js with done_means [1].
- **WHEN** The round is recorded with adv-record
- **THEN** adv-record exits 2 (not approved) and open-findings.json lists N1 with its done_means field still [1]; deferred-findings.json does not exist or does not list N1; `ratchet.py deferred` does not show it. CONTROL in a twin project: the identical finding without done_means is deferred (exit 0) with the existing 'low-risk step: only blocker and major findings block' reason.
<!-- proven by test CP3-T01 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: A minor finding that names a line still blocks after the round where minor findings stop blocking
- **GIVEN** A normal-risk step carried through rounds 1 and 2 (each blocked on a minor finding and src/f.js edited between rounds, checkin_round=99), the same carry-to-round-3 setup as tests/test_converge.py. In round 3 (past converge_after_round=2) the reviewer reports a minor finding on src/f.js with done_means [1].
- **WHEN** Round 3 is recorded
- **THEN** adv-record exits 2 and the finding is in open-findings.json, not deferred. CONTROL: the same round-3 minor without done_means is approved (exit 0) and listed by `ratchet.py deferred` (tests/test_converge.py CP5-T07 still holds).
<!-- proven by test CP3-T02 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: A line-naming minor finding in code nobody changed still blocks in a later round
- **GIVEN** A normal-risk step. Round 1 blocks on a major finding (with user_impact) in src/f.js, which the fixer then edits. In round 2 the reviewer reports a minor finding on src/a.js (not changed since round 1, not among last round's blocking files) with done_means [1].
- **WHEN** Round 2 is recorded
- **THEN** adv-record exits 2 and the finding is open. CONTROL: the same round-2 minor on src/a.js without done_means is deferred with the existing 'later round: not in code changed since the previous round' reason (exit 0).
<!-- proven by test CP3-T03 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: A line-naming minor finding of a kind an automatic check covers still blocks
- **GIVEN** A normal-risk step whose config has an automatic check for kind docs-out-of-sync. The reviewer reports a minor finding of that kind on README.md with done_means [1].
- **WHEN** The round is recorded
- **THEN** adv-record exits 2 and the finding is open, not deferred as covered. CONTROL: the same minor of that kind without done_means is deferred with 'an automatic check already covers docs-out-of-sync' (exit 0).
<!-- proven by test CP3-T04 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: A finding that names a line needs no separate user-impact line to block
- **GIVEN** A normal-risk step. Reviewer 1 reports major finding M1 (no user_impact) with done_means [1]; in a twin project the same major M1 has no done_means; a third project has a minor N1 with no user_impact and done_means [1].
- **WHEN** Each round is recorded
- **THEN** With done_means: exit 2, M1 open, and its deferred reason is NOT 'no user impact stated'. Twin without done_means: M1 is deferred with exactly 'no user impact stated' (existing rule from tests/test_review_objective.py CP2-T05 unchanged) and the round is approved. The minor N1 with a line blocks.
<!-- proven by test CP3-T05 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: A finding naming a number that is not a real line still blocks and is shown as written
- **GIVEN** The objective has exactly one done-means line. A low-risk step reports minor findings with done_means [99] (past the end), done_means 99 (a bare number, not a list), and done_means [0].
- **WHEN** The round is recorded
- **THEN** adv-record exits 2 without a crash or traceback; each finding is in open-findings.json with its done_means value exactly as the reviewer wrote it ([99], 99, [0]); none is deferred.
<!-- proven by test CP3-T06 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: Findings that name no line follow exactly today's rules
- **GIVEN** A low-risk step. One round holds: minor N1 with no done_means key, minor N2 with done_means [], minor N3 with done_means null, major M1 with user_impact and no done_means, major M2 with no user_impact and no done_means.
- **WHEN** The round is recorded
- **THEN** Only M1 blocks (open-findings.json lists exactly M1). N1, N2 and N3 are deferred with the low-risk reason, and M2 is deferred with 'no user impact stated'. The recorded round counts findings 1 and deferred 4 and raw_findings 5, identical to the same round with the done_means keys deleted.
<!-- proven by test CP3-T07 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: Only the finding that names a line blocks; its neighbours are still set aside
- **GIVEN** A low-risk step. In one round, minor N1 names done_means [1] and minor N2 names nothing; both are on src/f.js.
- **WHEN** The round is recorded
- **THEN** open-findings.json lists only N1; deferred-findings.json lists only N2 with the low-risk reason; the round entry shows findings 1 and deferred 1. The rule is per finding, not per round.
<!-- proven by test CP3-T08 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: Line-naming findings stay inside the round limits and never reset a budget
- **GIVEN** A normal-risk step with max_adversarial_rounds=2 and checkin_round=99. Every round the reviewer reports only a minor finding with done_means [1] on src/f.js, and src/f.js is edited between rounds.
- **WHEN** Rounds 1 and 2 are recorded blocking, then the agent tries to open round 3
- **THEN** After round 2 the run is blocked on the existing review-round limit exactly as if a major finding had been raised (state blocked, gates.adversarial.attempts == 2, adv_rounds_total == 2, cap_raised_to unset), and `reviewers CP1` refuses to open round 3. Nothing about the new field raises, resets or bypasses a limit. tests/test_limits.py CP1-X02..X05 are listed in the step notes as the companion regression to run, not run by this file's command.
<!-- proven by test CP3-T09 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: The reviewer behind an open line-naming finding is asked again next round
- **GIVEN** A normal-risk step with two lenses. Round 1: the standards lens reports a major finding with user_impact (no done_means); the correctness lens reports a minor finding with done_means [1].
- **WHEN** Round 1 is recorded and the fixer edits src/f.js, then `reviewers CP1` opens round 2
- **THEN** Round 2 lists BOTH lenses (standards and correctness), so the open line-naming finding is re-checked. CONTROL: with the correctness lens's minor finding carrying no done_means, round 2 lists only the standards lens (existing narrowing, tests/test_converge.py CP5-T02).
<!-- proven by test CP3-T10 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: The user can still accept a blocking line-naming finding with their own reply
- **GIVEN** A low-risk step blocked by minor finding N1 naming done_means [1] (T01 setup).
- **WHEN** The agent runs `ask accept-finding CP1 N1`, the user replies exactly 'accept: shipping it, the line is covered elsewhere', and the agent runs `accept-finding CP1 N1`
- **THEN** The ask is not refused as 'only ever deferred/minor'; accepting works and the step's adversarial gate can pass. The accepted entry in `deferred --json` still carries done_means [1] and the user's reason. Before the user's reply the same `accept-finding` is refused (the agent cannot accept its own line-naming finding), and `triage CP1 N1=fix:...` / `=rebut:...` work as for any open finding while `=accept` is still refused.
<!-- proven by test CP3-T11 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: A run with an older plan that has no objective still records a line-naming finding without crashing
- **GIVEN** A legacy approved plan with no objective at all (fabricated with the engine's own helpers), one low-risk step in review. The reviewer reports minor N1 with done_means [1] and minor N2 with no line.
- **WHEN** The round is recorded
- **THEN** No crash or traceback. N1 blocks (open-findings.json keeps done_means [1] as written) and N2 is deferred by today's low-risk rule, exactly as in a plan with an objective. No refusal is raised because the plan lacks an objective.
<!-- proven by test CP3-T12 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: The other-company reviewer is asked for the line, with numbered lines to pick from
- **GIVEN** A project with a fake codex reviewer, external_review.py --dry-run keeping the prompt it would send.
- **WHEN** The prompt is read
- **THEN** The Done means lines in the objective section are numbered 1..N (the numbers a reviewer would write in done_means, matching plan-summary and proves). The reply shape's findings example (the text after '"findings":[{') contains a done_means key, and the prompt says to name a line only when that line is not met in normal use (and to leave it out otherwise). Its existing severity and user_impact keys remain.
<!-- proven by test CP3-T13 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: The host reviewer sub-agent and the review loop are told the same thing
- **GIVEN** agents/ratchet-adversary.md, skills/ratchet-adversarial-loop/SKILL.md, and a host reviewer's launch instructions from `reviewers CP1 --json` on a project with providers=[].
- **WHEN** Their text is read
- **THEN** agents/ratchet-adversary.md's JSON output example has a done_means key and its prose says to name a line only when it is not met in normal use and that such a finding always blocks; SKILL.md says the same in the objective paragraph; the host launch instructions (or the objective carried in `reviewers --json`) number the Done means lines. A finding without done_means is still described as following the severity rules.
<!-- proven by test CP3-T14 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: The docs describe the done_means finding field in every place the rule shows up
- **GIVEN** skills/ratchet-orchestrator/references/schemas.md (the finding section, next to user_impact), README.md (the final review and review sections), skills/ratchet-orchestrator/SKILL.md.
- **WHEN** They are searched for done_means
- **THEN** schemas.md section 7 documents the optional done_means finding field, saying it is a list of 1-based line numbers, that such a finding blocks at any severity and round and is never deferred for scope, a covering check or a missing user_impact, and that a number that is not a real line still blocks and is shown as written; README mentions that a finding naming a done-means line always blocks; the 'user_impact' paragraph in schemas.md no longer implies a major without user_impact can be set aside when it names a line. Docs-rule from .ratchet/learnings.md 2026-10-04.
<!-- proven by test CP3-T15 in tests/test_dm_findings.py, commit e69cbc9 -->

#### Scenario: A line-naming minor finding still blocks in a one-reviewer small-fix round
- **GIVEN** A normal-risk step (never high risk, so the small-fix path can open) whose round 1 was fully approved. A 5-line fix to src/f.js (at or under small_edit_lines, 20) then opens round 2 as a one-reviewer small-fix round (`reviewers CP1 --json` shows small_fix true and exactly one reviewer), the same setup as tests/test_quick_path_small.py CP3-T01. That reviewer reports minor finding N1 on src/a.js (not changed since round 1, so today's later-round scope rule would set it aside) with done_means [1].
- **WHEN** The small-fix round is recorded with adv-record
- **THEN** adv-record exits 2: N1 is in open-findings.json with done_means [1], is not in deferred-findings.json, the gate is not passed, and no small_fix_review is recorded on the step (a blocked small-fix round is not an approved one). CONTROL in a twin project with the identical setup: N1 on src/a.js without done_means is deferred with 'later round: not in code changed since the previous round', adv-record exits 0, and the small-fix review is recorded (today's small-fix rule, lane B, unchanged). The small-fix round still counts toward the step's round limit either way.
<!-- proven by test CP3-T16 in tests/test_dm_findings.py, commit e69cbc9 -->

### Requirement: Unproven lines block the finish
The system SHALL do this: The run cannot be approved as finished while a done-means line is unproven or has an open finding against it; the agent is told plainly how to move on.

- At the final review, approving the finish is refused while any line is unproven or has an open finding naming it, from a chat reply and from a director token alike
- The agent is never told to ask the user to approve while the finish is blocked; status and next name the lines and the ways forward
- A run set to need no final review stops at the final review instead of finishing when a line is unproven
- Editing the done-means lines after approval cannot clear the block: the table reads the approved lines, and feedback cannot remove or reword one
- A run whose plan was approved before this change finishes as before, with a plain note

#### Scenario: A finished run with a failed done-means line cannot be approved as done
- **GIVEN** Run FIN2 at the final review (human_review end) with CP1-T02 recorded failed, so line 2 ('A note longer than 200 characters is refused with a message') is unproven while line 1 is proven; the user has asked nothing yet
- **WHEN** The agent runs ask human-approve, then the user replies 'yes, go ahead', then the agent runs human-approve
- **THEN** ask human-approve exits non-zero without printing an ASK THE USER question and leaves no pending question; human-approve exits non-zero; both messages name line 2 by number and its full text, give the reason (a linked test failed) and do not name line 1; state.json phase is still awaiting_final_review, no 'approved' event is in history, no checkpoint gates.human is passed
<!-- proven by test CP4-T01 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A yes given before the line went unproven does not finish the run
- **GIVEN** Run FIN2 at the final review with everything proven; the agent runs ask human-approve and the user replies 'yes' (a valid first reply exists); after that reply and before human-approve, an open finding naming line 2 is added to the step (done_means [2])
- **WHEN** The agent runs human-approve with that already-recorded yes
- **THEN** human-approve exits non-zero naming line 2 and the open finding (step and finding id); phase stays awaiting_final_review; no 'approved' event; the blocked check runs at the moment of approval, not only when the question was asked
<!-- proven by test CP4-T02 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: The director cannot sign a finish past an unproven line either
- **GIVEN** A director-enrolled copy of Run FIN2 at the final review with CP1-T02 failed (line 2 unproven); the director session has run director-key and director-enroll and director-sign human-approve (setup: a token is printed)
- **WHEN** The agent runs human-approve --director-token <token>
- **THEN** The command exits non-zero naming line 2 (number and text) and the reason; phase stays awaiting_final_review; no 'approved' event; gates.human not passed; the refusal does not mention asking the user to reply yes
<!-- proven by test CP4-T03 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: The director's approval still finishes a run whose every line is proven
- **GIVEN** Director-enrolled Run FIN2 at the final review with both lines proven (all linked tests passed, no open finding)
- **WHEN** The director signs human-approve and the agent runs human-approve --director-token <token>
- **THEN** The command succeeds, phase is done, an 'approved' event with by 'director' is in history; a chat reply 'yes' without a token is still refused as before (director-enrolled message)
<!-- proven by test CP4-T04 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: While the finish is blocked, status and next name the lines and the ways forward, and never tell the agent to ask for approval
- **GIVEN** Run FIN2 at the final review with line 2 unproven (failed linked test) and, separately, with line 2 unproven through an open finding
- **WHEN** The agent runs status and next
- **THEN** Both outputs list line 2 by number and text with its reason and say the run cannot be marked done yet; they name the ways forward (ask the user for changes with feedback; for a finding, close it with mark-fixed); neither output contains an instruction to run 'ask human-approve' or to run human-approve on the user's yes; status does not print a 'waiting for the user's answer: an approval' line (lane F's question line) and says plainly that nothing is waiting on a reply; line 1 is not listed as blocking
<!-- proven by test CP4-T05 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: status --json carries the block so the director and the agent read one source
- **GIVEN** The same blocked Run FIN2 in a user lane and in a director-enrolled lane
- **WHEN** The agent runs status --json and next
- **THEN** In both lanes next/stop wording follows T05 (no ask-for-approval instruction; in the director lane no 'director-sign human-approve' instruction either); the JSON names line 2's text and reason somewhere in its fields; keys that existed before (phase, next, stop_ok, waiting_on, objective, approval_mode) are still present; if a waiting-question field exists (lane F) it is null for the human-approve question
<!-- proven by test CP4-T06 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: When every line is proven, the approval question and the approval work as before
- **GIVEN** Run FIN2 at the final review, both lines proven by passed per-case results, no open finding
- **WHEN** The agent runs ask human-approve, the user replies 'yes, go ahead', and the agent runs human-approve
- **THEN** ask prints the existing question ('mark the finished work as approved and close this run'), human-approve exits 0, phase is done, next/status before approval still tell the agent to run ask human-approve and carry no blocked wording
<!-- proven by test CP4-T07 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A run set to need no final review stops at the final review when a line is unproven
- **GIVEN** Run FIN2-none (human_review none) with CP1-T02 failed before the last complete
- **WHEN** The agent runs complete CP1
- **THEN** complete exits 0 for the step itself but the run's phase is awaiting_final_review, not done; the output says the run is not finished and names line 2 and its reason; the run is still the active run for the project (not unregistered), no CI-export hint or spec-finish note is printed as for a finished run; the same blocked-status text as T05 applies
<!-- proven by test CP4-T08 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A run set to need no final review still finishes straight away when every line is proven
- **GIVEN** Run FIN2-none with both lines proven
- **WHEN** The agent runs complete CP1
- **THEN** phase is done, as before; no blocked wording is printed
<!-- proven by test CP4-T09 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: In review-each-step mode the last step's approval is blocked, earlier steps are not
- **GIVEN** Run FIN3 with human_review each: CP1 proves line 1, CP2 will prove lines 2 and 3 and is not built yet. CP1 is complete and waiting for its review; later, CP2 is complete with line 3 unproven (failed linked case) and waiting for its review
- **WHEN** The agent approves CP1's review, then later approves CP2's review
- **THEN** CP1's approval succeeds even though lines 2 and 3 have no result yet (a line whose tests belong to a step not built yet is not a finish blocker); CP2's approval, which would finish the run, is refused naming line 3 (not line 1 or 2); phase stays awaiting_checkpoint_review and review_target stays CP2
<!-- proven by test CP4-T10 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: Editing the done-means lines after approval does not clear the block
- **GIVEN** Blocked Run FIN2 at the final review (line 2 unproven); the builder then edits checkpoints.json so objective.done_means holds only line 1, and, in a second run, so line 2 reads 'A long note is refused'
- **WHEN** The agent runs human-approve after a yes, and runs status
- **THEN** In both runs human-approve is still refused and the message and status name the ORIGINAL line 2 text with its number; the reworded text never appears as the blocking line; the table does not shrink to one line
<!-- proven by test CP4-T11 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: Feedback cannot remove, reword or reorder an approved done-means line
- **GIVEN** Run FIN2 approved and at the final review; the user asks for changes (feedback is recorded, phase planning_feedback); the planner's extend plan appends step CP2 and, in three separate tries, also (a) drops line 2, (b) rewords line 2, (c) swaps lines 1 and 2
- **WHEN** The agent runs plan-extend for each try
- **THEN** Each try is refused with a message naming the changed line by number and its approved text, nothing is registered (no CP2 in state.json), phase stays planning_feedback; a fourth try with the objective untouched and CP2 appended succeeds (CP2 in state, building)
<!-- proven by test CP4-T12 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: Feedback may add a new done-means line at the end, and the new line is then held to the finish rule
- **GIVEN** Run FIN2 at the final review; feedback; plan-extend with lines 1 and 2 unchanged, a third line 'A note can be deleted' appended, and new step CP2 whose case proves [3] and whose test passes
- **WHEN** CP2 is built and completed, reaching the final review again
- **THEN** plan-extend succeeds; the new final review reads three lines; line 3 is proven by CP2's passed case and the finish is not blocked on it; if CP2's case for line 3 were failed instead, line 3 would be named as blocking
<!-- proven by test CP4-T13 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: Approving some other plan change cannot sneak in an edited done-means line
- **GIVEN** Blocked Run FIN2 at the final review; the builder rewords line 2 in checkpoints.json and also changes a rule the user must confirm (a config rule such as review_depth) so approve-changes has something to show
- **WHEN** The user confirms the rule change (ask approve-changes, yes) and the agent runs approve-changes, then human-approve after a yes
- **THEN** approve-changes succeeds for the rule change, but afterwards status and the human-approve refusal still name the original line 2 text; the reworded line does not become the approved line
<!-- proven by test CP4-T14 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: Closing the open finding is a way forward: the run can then be approved
- **GIVEN** Run FIN2 at the final review where line 2 is unproven only because a deferred finding on docs/NOTES.md names it (done_means [2]); the file is then edited, committed and re-reviewed in a clean round so mark-fixed is accepted (same sequence as tests/test_receipt.py _accept_and_defer_fixture)
- **WHEN** The agent runs mark-fixed for that finding, then ask human-approve, the user replies yes, and the agent runs human-approve
- **THEN** Before mark-fixed, status names the finding; after it, status shows no blocked wording, ask human-approve prints the question, human-approve exits 0 and phase is done
<!-- proven by test CP4-T15 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: Feedback is still open while the finish is blocked, and does not clear the block by itself
- **GIVEN** Blocked Run FIN2 at the final review (line 2 unproven by a failed linked test)
- **WHEN** The user replies asking for a change and the agent runs feedback, then plan-extend with a new step CP2 (objective unchanged) that is built and completed
- **THEN** feedback exits 0 and moves to planning_feedback while blocked; after CP2 completes the run is back at the final review and the table is recomputed (the new step's cases are listed, line 2 is still named as unproven because its failed result is unchanged); human-approve is still refused naming line 2
<!-- proven by test CP4-T16 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A refused approval leaves the review budgets and every other counter untouched
- **GIVEN** Blocked Run FIN2 with known values: gates.adversarial.attempts, adv_rounds_total, per-step round counters, regression_flaky_used and the history length recorded
- **WHEN** The agent tries ask human-approve, human-approve after a yes, a director-token human-approve, and (run FIN2-none) complete CP1
- **THEN** All counters equal their recorded values after each refusal (nothing reset, nothing spent); the only change allowed is a 'refused'-type history line, never an 'approved' or 'unblocked' event; tests/test_limits.py CP1-X02..X05 are not edited
<!-- proven by test CP4-X01 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A run stuck at a review-limit block can still be unblocked; the finish rule applies only to finishing
- **GIVEN** Run FIN2 mid-build, current step CP1, phase blocked at a review limit, with line 2 unproven or not yet tested
- **WHEN** The user picks the carry-on answer and the agent runs human-approve (the path that calls _unblock)
- **THEN** The unblock works exactly as it does today (phase building, history 'unblocked'), the finish check does not refuse it, and no finish-blocked wording is shown
<!-- proven by test CP4-X02 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A finding that names no done-means line, and a proven line, never block the finish
- **GIVEN** Run FIN2 at the final review with both lines proven, one minor finding left for later that names no line (today's shape), and one finding fixed later that names line 2
- **WHEN** The agent runs ask human-approve, the user replies yes, and human-approve runs
- **THEN** The run finishes (phase done); status still prints the left-for-later count as before; only an OPEN finding that names a line counts as a blocker
<!-- proven by test CP4-X03 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A run whose plan was approved before this change finishes as before, with a plain note
- **GIVEN** Run FIN2 rebuilt as a legacy approval (approved snapshot reduced to the pre-change keys, no proves in the test plans) at the final review with line 2's linked result failed and an open finding naming line 2
- **WHEN** The agent runs status, ask human-approve, the user replies yes, and human-approve runs
- **THEN** No refusal and no crash: the question prints, human-approve exits 0, phase is done; status shows the done-means lines without a blocked wording and with one plain note that this run was approved before unproven lines blocked the finish so they are shown but do not stop it
<!-- proven by test CP4-X04 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: An older run set to need no final review, or with no objective at all, still finishes
- **GIVEN** Two legacy runs: (a) legacy Run FIN2-none with line 2 unproven; (b) a run whose plan has no objective at all (fabricated with the engine's own helpers _plan_hash, new_cp_state, approval_snapshot, fingerprint, as tests/test_receipt_proof.py CP3-T08 does), at its final review
- **WHEN** (a) the agent runs complete CP1; (b) ask human-approve, a yes, human-approve
- **THEN** (a) phase is done as before with the plain note; (b) human-approve exits 0, status prints the existing 'no stated objective' note, no traceback and no refusal
<!-- proven by test CP4-X05 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A legacy run that goes through feedback and plan-extend stays legacy
- **GIVEN** Legacy Run FIN2 (pre-change approval) at the final review with line 2 failed; the user asks for a change, feedback, plan-extend adds step CP2 and it is completed
- **WHEN** The agent reaches the final review again and runs ask human-approve, a yes, human-approve
- **THEN** The run finishes with the plain note; plan-extend did not turn the finish rule on (it re-snapshots approval at ratchet.py:10345 and approve-changes at :11099: the marker is carried forward, not newly added); likewise a legacy run that uses approve-changes stays legacy
<!-- proven by test CP4-X06 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: The docs tell agents about the blocked finish and the old-run note
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md (final review, Phase C and the director section) and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** The test reads them
- **THEN** Each says the run cannot be marked done while a done-means line is unproven or has an open finding, and that the agent does not ask the user to approve while it is blocked; SKILL.md's Phase C ask-then-human-approve instruction is conditioned on the finish not being blocked; schemas.md describes that the approved lines are the ones checked and that feedback cannot change them; each says runs approved before the change keep working with a plain note
<!-- proven by test CP4-X07 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: A last step that only edits docs, and so skips adversarial review, still cannot finish the run past an unproven line
- **GIVEN** Run FIN2-docs: CP1 done and proven for line 1; the last step CP2 changes only docs/NOTES.md, its adversarial review is skipped by the docs-only path (the step output says the review was skipped for a docs-only change), and line 2 is unproven (CP2's linked case failed in its test report, or an open finding names line 2)
- **WHEN** The agent completes CP2 in human_review end and then asks to finish, and, in a second copy set to human_review none, runs complete CP2
- **THEN** In end mode complete CP2 reaches awaiting_final_review and ask human-approve and human-approve are refused naming line 2 with its reason; in none mode complete CP2 leaves phase awaiting_final_review (not done) with the blocked wording; skipping review for docs never marks a line proven, and with line 2 proven the same docs-only run finishes as before in both modes
<!-- proven by test CP4-T17 in tests/test_dm_finish.py, commit 3842593 -->

#### Scenario: Merging main between steps does not change the done-means lines the finish reads
- **GIVEN** Blocked Run FIN2 at the final review (line 2 unproven by a failed linked test), main merged into the lane with a docs file and a config-rule change brought in, and, in a second copy, checkpoints.json with line 2 reworded before the merge
- **WHEN** The agent runs merged, then status, then ask human-approve, a yes, human-approve
- **THEN** merged accepts the merge (config rule moves into the approved baseline as it does today) but status and the human-approve refusal still name the ORIGINAL line 2 text and number in both copies; the finish stays blocked; the approved lines and every review counter are the same before and after merged; a clean-run copy with line 2 proven still approves and finishes after merged
<!-- proven by test CP4-T18 in tests/test_dm_finish.py, commit 3842593 -->

### Requirement: User can accept an unproven line
The system SHALL do this: When the finish is blocked, the user can accept named done-means lines as unproven with their own reason, and the table and receipt show that acceptance.

- The agent can ask to accept named lines, and the question shows the only reply that counts: accept: followed by the user's reason
- Only the user's own first reply in that form accepts them; in an enrolled lane only a director token does
- Accepted lines show as accepted, with the reason, in the final review table and the receipt, and the finish is no longer blocked by them
- Asking about a line that is already proven or not a real line is refused

#### Scenario: The agent can ask to accept a named unproven line and the question shows the only reply that counts
- **GIVEN** Project A, finish blocked because line 2 is unproven
- **WHEN** the agent runs `ask accept-line 2`
- **THEN** the command succeeds and prints a question that names line 2 with its text, says the reply must be exactly 'accept: ' followed by the user's own reason, says only the first reply counts, and does not offer the listed yes words (yes, ok...) as an equivalent; a question is recorded as waiting (action accept-line, ids [2]) and nothing is accepted yet
<!-- proven by test CP5-T01 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: The user's own first reply accepts the line and the reason is kept
- **GIVEN** the question for line 2 was asked and the owner chat replies 'accept: login lockout ships next sprint, tracked in the tracker'
- **WHEN** the agent runs `accept-line`
- **THEN** line 2 is recorded accepted with that exact reason, accepted_by human and a time; the reason is appended once to .ratchet/learnings.md; the run is still in the final review (the accept reply did not finish the run); lines 1 and 3 are untouched
<!-- proven by test CP5-T02 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: An accepted line shows as accepted with its reason and the finish is no longer blocked by it
- **GIVEN** Project A, line 2 refused before (human-approve refused naming line 2), then accepted with a reason
- **WHEN** the agent reads `status`, `status --json` and `next`, then asks for the final approval and the user says yes
- **THEN** the final-review table shows line 2 as accepted with the reason (not 'proven' and not 'unproven'), lines 1 and 3 as proven; status and next no longer list line 2 as blocking the finish; the human-approve question can be asked and the user's yes finishes the run (phase done)
<!-- proven by test CP5-T03 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: The receipt shows the accepted line and its reason, apart from proven lines
- **GIVEN** Project A with line 2 accepted with a reason
- **WHEN** the agent runs `receipt` and `receipt --json`
- **THEN** the done-means section lists line 2 as accepted without a test, with the reason; lines 1 and 3 as proven with their tests; the json carries the same state and reason; both commands change no file
<!-- proven by test CP5-T04 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Asking to accept a line that is already proven is refused
- **GIVEN** Project A (line 1 proven, line 2 unproven)
- **WHEN** the agent runs `ask accept-line 1` and `ask accept-line 1,2`
- **THEN** both are refused, the message names line 1 as already proven, no question is recorded as waiting, and state is unchanged; `ask accept-line 2` still works afterwards
<!-- proven by test CP5-T05 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Asking to accept a line that does not exist is refused
- **GIVEN** Project A (three lines)
- **WHEN** the agent asks for lines 0, 4, -1, 2.5, abc, an empty list and '2,,'
- **THEN** each is refused with a message naming the bad value and saying the real line numbers are 1 to 3 (the empty list says line numbers are needed); no question is recorded and state is unchanged
<!-- proven by test CP5-T06 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Accepting one of two unproven lines leaves the other blocking the finish
- **GIVEN** Project B (lines 2 and 3 unproven)
- **WHEN** the user accepts line 2 only, then the agent tries to finish (ask human-approve and the user's yes)
- **THEN** the table shows line 2 accepted and line 3 unproven; the finish is still refused and the refusal names line 3 and not line 2; accepting line 3 afterwards (a second ask and reply) unblocks the finish
<!-- proven by test CP5-T07 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Accepting a line that has an open finding against it clears that finding's block
- **GIVEN** Project D (line 2 unproven only through an open finding that names it; its linked cases passed)
- **WHEN** the user accepts line 2 with a reason
- **THEN** line 2 shows accepted, the finish is not blocked by that finding any more, and the finding itself is still listed by `deferred` (the acceptance does not erase or mark it fixed)
<!-- proven by test CP5-T08 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: In a director lane only a director token accepts a line
- **GIVEN** Project F, ask accept-line 2 asked, owner chat replies 'accept: a plain chat reply'
- **WHEN** the agent runs `accept-line` after the chat reply, then the director runs `director-sign accept-line` and the agent runs `accept-line --director-token T --reason 'director accepted this limit'`
- **THEN** the chat reply is refused, saying a director token is needed, and line 2 stays unproven; with the token and reason line 2 is accepted with accepted_by director and the director's own reason (not the chat text); the finish is no longer blocked by it
<!-- proven by test CP5-T09 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Asking to accept a line is refused when the finish is not blocked
- **GIVEN** Project C (every line proven, at the final review), and a run still building its step
- **WHEN** the agent runs `ask accept-line 1` in each
- **THEN** both are refused with a plain message (nothing is unproven, or the run is not at the final review), no question is recorded, and state is unchanged
<!-- proven by test CP5-X01 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: accept-line before any question was asked accepts nothing
- **GIVEN** Project A with no question asked, and the owner chat having said 'accept: fine' earlier
- **WHEN** the agent runs `accept-line`
- **THEN** it is refused saying to run `ask accept-line` first and show the question; line 2 stays unproven
<!-- proven by test CP5-X02 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: No reply yet means nothing is accepted
- **GIVEN** the question for line 2 asked, no reply typed
- **WHEN** the agent runs `accept-line`
- **THEN** it is refused saying no reply has been recorded in the owning chat yet and to wait; the question is still waiting (a later correct reply still works); line 2 stays unproven
<!-- proven by test CP5-X03 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A reply in a different chat never accepts a line
- **GIVEN** the question for line 2 asked in the owner chat; 'accept: because it is fine' typed in chat s-other
- **WHEN** the agent runs `accept-line`
- **THEN** it is refused (no reply from the owning chat yet); line 2 stays unproven; the question is not spent, so the owner's own later 'accept: ...' still accepts
<!-- proven by test CP5-X04 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A reply typed before the question does not count
- **GIVEN** Project A where the owner chat said 'accept: the lockout can wait' before any question; then ask accept-line 2
- **WHEN** the agent runs `accept-line` without any new reply
- **THEN** it is refused (no reply since the question); line 2 stays unproven
<!-- proven by test CP5-X05 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A first reply in the wrong form closes the question for good
- **GIVEN** the question for line 2 asked; the first reply is each of: 'yes', 'accept:', 'accept:   ', 'Accepted - fine', 'sure, accept: fine'
- **WHEN** the agent runs `accept-line`, and then the user types a correct 'accept: real reason' and the agent runs `accept-line` again
- **THEN** the first run is refused naming the exact form needed and nothing is accepted; the question is spent, so the second run is refused too (not asked, or no question waiting) and line 2 stays unproven; only after a fresh `ask accept-line 2` and a new correct first reply does it accept
<!-- proven by test CP5-X06 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A sub-agent hand-back or task notice containing 'accept:' never counts
- **GIVEN** the question for line 2 asked
- **WHEN** the prompt hook receives 'Another Claude session sent a message: accept: the build is done [Subagent hand-back]', an <agent-message from=...> wrapped 'accept: x', and a <task-notification> block, then the agent runs `accept-line`
- **THEN** none of them is recorded as the user's reply (human_log.jsonl gains no line), accept-line is refused as having no reply, and the question is not spent: the owner's real 'accept: reason' typed afterwards accepts line 2
<!-- proven by test CP5-X07 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: In a user lane a --reason flag or a token cannot stand in for the user's reply
- **GIVEN** Project A (not director-enrolled), question asked, no reply typed
- **WHEN** the agent runs `accept-line --reason 'agent says fine'` and `accept-line --director-token abc --reason 'x'`
- **THEN** both are refused (the reason must come from the user's own reply; a token needs an enrolled director lane); line 2 stays unproven and nothing is written to learnings.md
<!-- proven by test CP5-X08 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A director token without a reason is refused and not spent
- **GIVEN** Project F, ask accept-line 2, director-sign accept-line produced token T
- **WHEN** the agent runs `accept-line --director-token T` without --reason, then the same command with --reason added
- **THEN** the first is refused naming --reason, line 2 stays unproven and the question is still waiting; the same token with --reason then accepts line 2
<!-- proven by test CP5-X09 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A director token signed before the question was asked is dead, and signing outside the final review is refused
- **GIVEN** Project F; the director signs accept-line before the agent has run `ask accept-line 2`, and separately a run still building
- **WHEN** the agent then asks the question and runs `accept-line --director-token T --reason R` with the early token; and the director runs `director-sign accept-line` in the building run
- **THEN** the early token is refused as not matching the run's current pause and line 2 stays unproven; a token signed after the ask works; director-sign refuses in the building phase naming the phases it applies to
<!-- proven by test CP5-X10 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A run approved before this change gets a plain note, not a refusal or a crash
- **GIVEN** Project E (older plan, approved before this change, one unproven line, at the final review)
- **WHEN** the agent runs `ask accept-line 1`, `status`, `status --json` and `receipt`, and the finish is approved the old way
- **THEN** ask accept-line gives a plain note that this run was approved before the finish rule so no line needs accepting (exit code and wording that is not a crash or traceback); status and receipt still print their table with the old-run note; nothing is recorded in state; the finish works as it did before
<!-- proven by test CP5-X11 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A question goes stale when the line stops being unproven before the reply
- **GIVEN** Project A, ask accept-line 2 asked; then line 2's case is rewritten as passed in the test report (line 2 now proven)
- **WHEN** the owner chat replies 'accept: whatever' and the agent runs `accept-line`
- **THEN** it is refused saying line 2 is not unproven any more and nothing was accepted; the pending question is cleared; no learnings entry is written
<!-- proven by test CP5-X12 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Accepting a line covers that line only, not lines that fail later
- **GIVEN** Project A with line 2 accepted; then line 3's case is rewritten as failed
- **WHEN** the agent reads status and tries to finish
- **THEN** line 2 still shows accepted with its reason, line 3 shows unproven, and the finish is refused naming line 3 only
<!-- proven by test CP5-X13 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Accepting the same line twice is refused and writes nothing twice
- **GIVEN** Project A with line 2 accepted
- **WHEN** the agent runs `ask accept-line 2` again, and `ask accept-line 2,3` when line 3 is proven
- **THEN** both are refused, saying line 2 is already accepted (and, for the second, that line 3 is proven); learnings.md still has exactly one entry for line 2 and state has exactly one accepted record for it
<!-- proven by test CP5-X14 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Repeated line numbers count once and a multi-line reason stays one learnings line
- **GIVEN** Project B (lines 2 and 3 unproven)
- **WHEN** the agent asks `accept-line 3,2,3,2` and the user replies with a reason spanning three lines
- **THEN** the pending question lists lines 2 and 3 once each; both are accepted once with the full multi-line reason kept in state; learnings.md gets one single-line entry per accepted line, with the reason's whitespace collapsed
<!-- proven by test CP5-X15 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: An accept reply never approves the finish and a plain yes never accepts a line
- **GIVEN** Project A: (a) the accept-line question is waiting; (b) separately, the human-approve question is waiting
- **WHEN** (a) the user replies 'yes' and the agent runs `accept-line` and `human-approve`; (b) the user replies 'accept: fine' and the agent runs `accept-line` and `human-approve`
- **THEN** (a) accept-line refuses the wrong form and human-approve refuses (a different question is waiting, and the finish is blocked anyway); (b) human-approve refuses (the reply is not a listed yes, and the finish is blocked), accept-line refuses (it was not asked); the run is not done and line 2 stays unproven in every step
<!-- proven by test CP5-X16 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: The waiting accept question shows in status and next without a crash
- **GIVEN** Project A with the accept-line question for line 2 waiting
- **WHEN** the agent runs `status`, `status --json` and `next`
- **THEN** all succeed; the waiting question is named in plain words as a question about accepting an unproven line (not a blank or the raw action name crashing a lookup), and the lines still read unproven
<!-- proven by test CP5-X17 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: A write failure mid-accept records nothing and a retry accepts once
- **GIVEN** the question for line 2 asked and the owner's correct reply recorded; the state save is made to fail once (the way test_accept_finding.py failing_save does)
- **WHEN** the agent runs `accept-line` (crashes), then runs it again
- **THEN** after the crash state.json is byte-for-byte unchanged and line 2 is still unproven; the retry accepts it, with exactly one learnings.md entry and one accepted record
<!-- proven by test CP5-X18 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: Accepting a line leaves the review budgets and stored choices alone
- **GIVEN** Project A with some review rounds used and a stored block choice (choice_allow) from earlier
- **WHEN** line 2 is accepted
- **THEN** adv_rounds_total, each step's attempts and round counts, and choice_allow are exactly as before the accept
<!-- proven by test CP5-X19 in tests/test_dm_accept.py, commit 6008def -->

#### Scenario: The docs describe how to accept an unproven line
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** they are searched for the accept-line command
- **THEN** each names `ask accept-line`, the exact reply form `accept: <reason>`, the director token path, and that accepted lines show as accepted in the final-review table and the receipt
<!-- proven by test CP5-X20 in tests/test_dm_accept.py, commit 6008def -->

### Requirement: Before-and-after shortcut table
The system SHALL do this: For a change that touches a guard-like part, Ratchet runs that part's fixed table of shortcut commands against the starting code and the current code and shows what got worse, stayed open or got fixed.

- A table whose watched files this run changed is run against the starting code and the current code when the last step completes, and on demand; after main is merged into the lane, a regression this run made still shows and main's own changes are not counted as this run's
- The final review and the receipt show rows that got worse, stayed open, got fixed or errored, with counts
- A row that existed at the run's start still runs with its original expectation after the table file is edited or deleted
- A runner that crashes or prints anything but ALLOWED or REFUSED shows as an error, never as refused
- A test case can link a table to done-means lines; that line is proven only when every row is right on the current code

#### Scenario: A shortcut that used to be blocked and now gets through is shown as worse
- **GIVEN** toy project A: the starting commit's guard refuses 'cat x | tee state.json' and the edited working-tree guard allows it; the table expects it refused; likewise 'diff -u a b' expects allowed and the new guard over-refuses it
- **WHEN** the agent runs `ratchet.py shortcuts --json` and then plain `ratchet.py shortcuts`
- **THEN** exit 0; rows tee and diff have verdict 'worse' with old/new verdicts REFUSED/ALLOWED and ALLOWED/REFUSED respectively; the plain output names both under a 'got worse' heading with their commands; neither appears under fixed, still open or errored
<!-- proven by test CP6-T01 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A hole that was open at the start and is still open is shown as still open, not as fixed or ok
- **GIVEN** toy project A: neither guard version refuses 'sed -i s/a/b/ f' but the table expects it refused
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** row sed_i has old ALLOWED, new ALLOWED, verdict 'still_open'; the plain output lists it under 'still open'; it is not in the worse or fixed lists
<!-- proven by test CP6-T02 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A hole the run closed is shown as fixed
- **GIVEN** toy project A: the starting guard allows 'cp -t dir f' and the edited guard refuses it; the table expects it refused
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** row cp_t has old ALLOWED, new REFUSED, verdict 'fixed'; listed under 'fixed' in the plain output and nowhere else
<!-- proven by test CP6-T03 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: Rows that are right in both versions are not flagged, and the counts add up
- **GIVEN** toy project A with its six base rows
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** rows redirect and cat have verdict 'held' and are not named under worse, still open, fixed, errored or unknown; counts are worse 2, still_open 1, fixed 1, held 2, errored 0, unknown 0 and sum to the six rows; the plain output states the same counts in one line
<!-- proven by test CP6-T04 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A runner that crashes shows as an error, never as refused
- **GIVEN** toy project A with an extra row 'crash' (command CRASH, expect refused) and a row 'late_crash' (command PRINT_THEN_CRASH, expect refused) where the runner prints REFUSED and then exits 1 on both versions
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** both rows have old and new 'error' and verdict 'errored'; neither is counted as held, fixed, still_open or worse (a crash that happens to say REFUSED must not look like the row being right); counts.errored is 2; the plain output names them under 'errored' with the runner's last line of output; the other six rows still get their normal verdicts and the command still exits 0
<!-- proven by test CP6-T05 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: Anything the runner prints other than exactly ALLOWED or REFUSED shows as an error
- **GIVEN** toy project A with extra rows GARBAGE (prints 'refused: probably'), EMPTY (prints nothing) and TWO_LINES (prints ALLOWED then REFUSED), each expecting refused
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** all three rows are verdict 'errored' with a note that the output was not ALLOWED or REFUSED; none is read as REFUSED by a substring or case-insensitive match; a runner that times out or cannot be started (runner command naming a missing file) also gives 'errored' for its rows rather than aborting the whole table
<!-- proven by test CP6-T06 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A row deleted from the table file after the run started still runs
- **GIVEN** toy project A: after plan approval the builder edits tests/shortcuts/toy.json in the working tree to remove the tee row (the regression)
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** the tee row is still in the output with its original command and expectation refused, verdict 'worse'; counts are unchanged from CP6-T04; the output says the table file no longer has this row (a removed row note), so the removal is visible rather than silent
<!-- proven by test CP6-T07 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A row flipped to expect the opposite after the run started keeps its original expectation
- **GIVEN** toy project A: after plan approval the builder edits toy.json so sed_i expects allowed (making the open hole look fine) and tee expects allowed (hiding the regression), and also rewrites tee's command to 'true'
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** sed_i is still 'still_open' with expect refused; tee is still 'worse' with expect refused and its original command 'cat x | tee state.json'; the output notes both rows were changed since the run started and that the starting version was used
<!-- proven by test CP6-T08 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: Deleting the whole table file, or the whole shortcuts folder, does not make the table go away
- **GIVEN** toy project A: after plan approval the builder deletes tests/shortcuts/toy.json (the toy_runner.py file is left in place), and in a second variant deletes the whole tests/shortcuts folder
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** the table 'toy' still runs from its starting-commit copy with all six rows and the same verdicts as CP6-T04 (the runner comes from the old version too when the working copy is gone); the output says the table file was deleted since the run started; exit 0
<!-- proven by test CP6-T09 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A row or table added during the run also runs and is marked new
- **GIVEN** toy project A: after plan approval the builder adds a row 'newrow' (command 'echo y >> log', expect refused) to toy.json, and adds a second table tests/shortcuts/fresh.json (watching guard.py) that did not exist at the starting commit
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** newrow appears in table 'toy' with new_row true and a real old and new verdict (both REFUSED, held); table 'fresh' runs with every row new_row true; the six base rows have new_row false
<!-- proven by test CP6-T10 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A table runs only when the files it watches changed in this run
- **GIVEN** toy project B: table 'toy' watches guard.py (edited this run) and table 'other' watches other.py (never touched)
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** toy has ran true with results; other has ran false and is shown as not run because its watched files did not change; no result file is saved for other; the same project with guard.py edited back to its starting content also shows toy as not run
<!-- proven by test CP6-T11 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: Completing the last step runs the table and saves its result without blocking the finish
- **GIVEN** toy project A built through one full step with guard.py edited before the behavior gate, one clean adversarial round, and a worse row (tee) and a still-open row present
- **WHEN** the agent runs `ratchet.py complete CP1`
- **THEN** complete exits 0 (this run only shows the result; it never refuses a finish for a worse row, per the out-of-scope line on lane C); evidence/shortcuts/toy.json exists under the run folder with the verdicts and the code fingerprint at that moment; a run whose table crashes (CRASH row) still completes
<!-- proven by test CP6-T12 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: The final review shows which rows got worse, stayed open, got fixed or errored, with counts
- **GIVEN** the finished run from CP6-T12 waiting at the final review
- **WHEN** the agent runs `ratchet.py status` and `ratchet.py status --json`
- **THEN** the final review text has a shortcut-table block naming table 'toy', the counts (2 worse, 1 still open, 1 fixed, 2 held, 0 errored) and the commands of the worse rows; status --json carries the same counts and rows under the key 'shortcuts'; a run with no table whose files changed shows no table block and no error
<!-- proven by test CP6-T13 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: The receipt shows the same table result as the final review
- **GIVEN** the finished run from CP6-T12
- **WHEN** the agent runs `ratchet.py receipt` and `ratchet.py receipt --json`
- **THEN** the markdown has a shortcut-table section with the same counts and the same worse, still-open, fixed and errored row names as status; the JSON receipt carries them under one key; an errored row is listed as errored, never as refused or held
<!-- proven by test CP6-T14 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A saved table result is marked stale once the code changes again
- **GIVEN** toy project A with `shortcuts` already run once and its result saved
- **WHEN** the builder edits guard.py again and the agent runs `ratchet.py status` and then `ratchet.py shortcuts`
- **THEN** status says the saved shortcut-table result is out of date for the current code (stale true in status --json); after `shortcuts` runs again the stale mark is gone and the verdicts reflect the new guard
<!-- proven by test CP6-T15 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: Running the table changes no file in the project and leaves no copies behind
- **GIVEN** toy project A and toy project M (after its merge), each with the new guard in the working tree and some untracked and modified files; a hash of every file outside the run folder, `git status --porcelain` and the temp folder list are taken first
- **WHEN** the agent runs `ratchet.py shortcuts`, `shortcuts --json` and `ratchet.py complete` (with its table call)
- **THEN** every file's hash and the git status are identical to before (the old version is a throwaway copy, never a checkout, stash or reset of the working tree, and the merge-tree build writes no file or branch the project can see); the working-tree guard.py still holds the NEW rules; no 'ratchet-occurrence-*' folder remains in the temp folder; a runner that writes a file into its own version root (row command WRITE) leaves the project untouched
<!-- proven by test CP6-T16 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A done-means line linked to a table is unproven while any row is wrong on the current code
- **GIVEN** a plan with objective done-means lines (at least two) and a test case with layer 'shortcuts' naming table 'toy' and proving line 1; table 'toy' has a worse row, a still-open row and an errored row on the current code
- **WHEN** the agent runs `ratchet.py receipt` (and `status --json` once CP1's table exists in the build) after `shortcuts`
- **THEN** line 1 reads unproven and names table 'toy' and the wrong rows by id; the shortcuts case never reads 'no result recorded yet' once a result is saved, and reads exactly that when `shortcuts` was never run; the plan's other lines are unaffected; an errored row alone also keeps the line unproven
<!-- proven by test CP6-T17 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: The same linked line is proven once every row is right on the current code
- **GIVEN** the same plan as CP6-T17 but the edited guard is fixed so every row (including tee and sed -i) is right on the current code and no row errors; a variant where main was merged and the old side is 'unknown'
- **WHEN** the agent runs `ratchet.py shortcuts` and then `ratchet.py receipt`
- **THEN** the shortcuts case passes and line 1 reads proven with the table named; rows that were worse in the old version but are right now do not matter, only the current code decides (so an 'unknown' old side does not keep the line unproven); a stale saved result (code changed since) is not trusted and reads as unproven until `shortcuts` runs again
<!-- proven by test CP6-T18 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A plan whose shortcuts case names a table that does not exist is refused naming the case
- **GIVEN** a plan with a test case CP1-T02 of layer 'shortcuts' naming table 'nosuch' (no tests/shortcuts/nosuch.json at the starting commit or in the working tree); and a shortcuts case that names no table at all
- **WHEN** the agent runs `ratchet.py plan-submit`
- **THEN** plan-submit exits non-zero and names CP1-T02 and the missing table; the case that names no table is refused the same way; a plan with a valid 'toy' shortcuts case is accepted and `tests-written` does not demand that a shortcuts case's id appear in a test file
<!-- proven by test CP6-T19 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A project with no tables, and a run approved before this change, keep working with a plain note
- **GIVEN** toy project C: no tests/shortcuts folder, plan approved the legacy way with no objective and no run_start, guard.py edited; a second variant with a table file present in a run that has no run_start and has not merged main
- **WHEN** the agent runs `ratchet.py shortcuts`, `shortcuts --json`, `status` and `receipt` and completes the last step
- **THEN** shortcuts exits 0 with one plain line saying there are no shortcut tables in this project; no traceback; status and receipt show no table block and no refusal; the step completes as before; in the second variant the old side falls back to the step's base_tree, the table runs and is shown with no blocking effect
<!-- proven by test CP6-T20 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A table file that is not valid JSON is reported by name and does not stop the other tables
- **GIVEN** toy project B where tests/shortcuts/broken.json in the working tree is not valid JSON and also lacks 'rows', and tests/shortcuts/toy.json is fine
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** table 'broken' is reported unreadable with a plain reason and no traceback; toy still runs with its normal verdicts; if toy.json itself is the broken file but was valid in the old version, the old copy is used and the output says the current file is unreadable
<!-- proven by test CP6-T21 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: After main is merged, this run's regression still shows and main's own changes are not blamed on the run
- **GIVEN** toy project M after `merged` succeeded: the lane's step removed the tee rule from the guard; main (forked from the run's starting commit) separately added a rule that closes the sed -i hole and added a row 'dd' (expect refused, open everywhere) to the table; the merge was clean
- **WHEN** the agent runs `ratchet.py shortcuts --json` and then plain `ratchet.py shortcuts`
- **THEN** row tee is still 'worse' (old REFUSED, new ALLOWED), because the old side is main's side laid onto the run's starting commit and so still has the tee rule; row sed_i, which only main changed, is 'held' (old REFUSED, new REFUSED), never 'fixed' and never 'worse'; row dd, which main added, is 'still_open' with new_row false (it exists in the old side), never 'worse' or 'fixed'; counts are not inflated by main's changes; exit 0
<!-- proven by test CP6-T22 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: Merging main does not move where the run started
- **GIVEN** toy project M before and after `ratchet.py merged`, with the state's run_start read each time (and again after the next step starts and after a `feedback`/resume cycle if the fixture reaches one cheaply)
- **WHEN** the agent runs `ratchet.py merged`
- **THEN** run_start.tree and run_start.head are exactly what plan approval wrote (the starting commit's HEAD sha and the fingerprint id at approval) both before and after; the step's own base_tree and base_head do move to the merge, which proves the two fields are separate; a second merge of main leaves run_start unchanged as well
<!-- proven by test CP6-T23 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A guard change only main made does not trigger the table
- **GIVEN** toy project M2: after the merge the working-tree guard.py differs from the run's starting commit only because main changed it; the lane changed an unrelated file
- **WHEN** the agent runs `ratchet.py shortcuts --json` and completes the last step
- **THEN** table 'toy' has ran false, shown as not run because its watched files did not change in this run (compared between the old version built from the merge-tree and the working tree, not against the starting commit); no result is saved for it; the final review shows no table block; if the lane then also edits guard.py the table does run
<!-- proven by test CP6-T24 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: An older run that has no start record and merged main gets a plain note, with no crash and no block
- **GIVEN** toy project M with run_start removed from state.json (a run approved before this change, edited the way tests/test_status_plain.py does it, with the integrity record re-baselined), then merged main for real
- **WHEN** the agent runs `ratchet.py shortcuts --json`, `status`, `receipt` and then completes the last step
- **THEN** every row has old 'unknown' and verdict 'unknown' (not worse, fixed or still_open), still shows its new-side verdict, and counts.unknown equals the row count; one plain note says the starting version cannot be worked out because the run began before starting versions were recorded and main was merged; no traceback; status and receipt carry the note; complete exits 0 and nothing is blocked
<!-- proven by test CP6-T25 in tests/test_shortcuts.py, commit 88ee982 -->

#### Scenario: A merge-tree conflict shows the old side as unknown instead of guessing
- **GIVEN** toy project M3: the lane and main edited the same guard.py line differently, so building main's side onto the run's starting commit conflicts, although the merge commit itself was resolved by hand and accepted by `merged`
- **WHEN** the agent runs `ratchet.py shortcuts --json`
- **THEN** every row has old 'unknown' and verdict 'unknown' with one plain note naming the conflict, never a worse, fixed or still_open guess; new-side verdicts are still shown; exit 0; run_start is untouched
<!-- proven by test CP6-T26 in tests/test_shortcuts.py, commit 88ee982 -->

### Requirement: Ratchet's own guard gets a shortcut table
The system SHALL do this: Ratchet ships a fixed table of the director's shortcut commands, the guard tests' commands and everyday read-only commands for its own guard, so every run that changes the engine shows the guard's before-and-after result.

- The table holds every write the director's shortcut probe tried (plain, quoted, 1>, 2>, &> redirects, tee, process substitution, uniq -, cp -t, while-read, sed -i, a Python write) as should-refuse, and everyday reads (cat, grep, ls, diff, a test for a file, read-only Python) as should-allow
- The table also holds the write and read commands from the guard's own tests, each row naming where it came from, without repeating those tests' checks
- Running it on the current engine gives a result for every row, with no errors
- A run that changes the engine shows the guard table at its final review
- The orchestrator skill and README tell agents what the table is and that rows are only ever added

#### Scenario: Every shortcut the director's probe tried is in the table as should-refuse
- **GIVEN** The shipped table, and the director's twelve write shortcuts pinned in the test as literals (plain redirect, quoted redirect target, 1>, 2>, &>, tee, process-substitution tee, uniq - with the run file as output, cp -t, while-read loop writing the files it finds, sed -i, python3 -c write), each aimed at a run's state.json
- **WHEN** The test reads the table and looks for one row per shortcut (matched by the shortcut's shape, for example a row command containing ' 1> ' or 'uniq -' or 'cp -rt' or 'sed -i', so wording of the row name may differ)
- **THEN** Each of the twelve shortcuts has a row, every one of those rows expects REFUSED, and every one of them aims at a path under the run folder's state.json. Not one write row expects ALLOWED (so a row the guard still allows can only ever show as still open, never be turned into a pass by flipping its expectation). A table missing even the process-substitution row, or with the &> row set to ALLOWED, fails this case.
<!-- proven by test CP7-T01 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: Everyday read-only commands are in the table as should-allow
- **GIVEN** The shipped table, and the everyday reads pinned in the test: cat of a run file, grep over a run's evidence folder, ls of the run folder, diff of a run file against another file, test -f on a run file, read-only python3 -c that opens a run file with no write mode, and the heredoc that writes a /tmp note whose text mentions the run folder path
- **WHEN** The test looks for a row for each of the seven reads
- **THEN** Each has a row and every one expects ALLOWED. The table holds at least 12 should-refuse rows and at least 7 should-allow rows. A table that only lists refusals (so a guard that refuses everything would look perfect) fails this case.
<!-- proven by test CP7-T02 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: The table is well-formed and watches the engine file
- **GIVEN** The shipped table and the repo checkout
- **WHEN** The test loads the table and checks its shape
- **THEN** It is valid JSON; every row has a non-empty unique name and a source (checked further in CP7-T14), a non-empty command and an expected outcome that is exactly ALLOWED or REFUSED (no third value, no blank); no two rows share the same command text; the files the table watches include skills/ratchet-orchestrator/scripts/ratchet.py and every watched path exists in the repo (a mistyped watch path would mean the table never shows up at a final review); the table's runner path points at a file that exists and is tests/shortcuts/guard_runner.py
<!-- proven by test CP7-T03 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: Running the table on the current engine gives an answer for every row and no errors
- **GIVEN** The repo's own engine as the version root, and the shipped runner
- **WHEN** The test runs the table's runner command exactly as the table spells it, with the repo root as the version
- **THEN** It exits 0 with empty stderr; it prints exactly one line per table row, in table order, each line being ALLOWED or REFUSED followed by the row's name (nothing else: no error text, no traceback, no extra lines); the number of ALLOWED plus REFUSED lines equals the number of rows; none of the temp project folders it built is left behind in the temp area afterwards
<!-- proven by test CP7-T04 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: The runner reports what the guard really says, not what the table expects
- **GIVEN** Toy engine A (allows everything) and toy engine B (refuses everything) each as a version root
- **WHEN** The runner is run once against each
- **THEN** Against A every row prints ALLOWED, including the write rows; against B every row prints REFUSED, including the read rows; both runs list the same row names in the same order as the table and the real-engine run. A runner that prints the expected outcome, or ALLOWED for a read and REFUSED for a write regardless of the guard, fails one of the two runs.
<!-- proven by test CP7-T05 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: A guard that crashes on a row shows that row as an error, never as refused
- **GIVEN** Toy engine C (raises on any command containing '&>' and refuses everything else) as the version root
- **WHEN** The runner is run against it
- **THEN** The &> row does not print as REFUSED and does not print as ALLOWED: its output is an error line (or a non-ALLOWED/REFUSED result the runner marks as an error) naming the row; every other row still prints REFUSED; the runner does not stop at the first crash (rows after the &> row still get answers); the runner exits non-zero (or otherwise marks the run as having an error) so CP6's comparison counts the row as errored
<!-- proven by test CP7-T06 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: A version with no usable guard function is an error for the whole table, not a pass
- **GIVEN** Toy engine D (no _guard_shell) and toy engine E (ratchet.py with a syntax error) as version roots, and a version root path that does not exist
- **WHEN** The runner is run against each
- **THEN** In all three cases the run exits non-zero with a plain message naming what is wrong (no guard function / cannot load / no such version), prints no ALLOWED or REFUSED line for any row, and leaves no temp project behind
<!-- proven by test CP7-T07 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: The runner builds its own throwaway project and never runs a row's command or touches the real one
- **GIVEN** Toy engine F as the version root (it records the cwd it receives, whether .ratchet/active.json and the run folder named by the row commands exist there, and the state.json hash), plus a hash of the toy engine file and a listing of the real repo's .ratchet/ before the run
- **WHEN** The runner is run against F
- **THEN** Every call received a cwd that is a folder outside the repo checkout; in that folder .ratchet/active.json and the run folder the row commands name existed at the moment of the call (otherwise the write rows would be answered on a project that has nothing to protect); the state.json hash is the same on every call (no row's command was ever executed); after the run the engine file's hash and the real repo's .ratchet/ listing are unchanged, and the throwaway folder is gone
<!-- proven by test CP7-T08 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: Compared with the starting code, rows the engine answers wrongly show as still open, never fixed, dropped or flipped
- **GIVEN** Temp project guard-proj: first commit holds the table, the runner and toy engine A (allows everything); the run's step replaces the engine with the repo's real ratchet.py. The test first runs the runner directly on the real engine to learn which rows it answers wrongly (answer differs from the row's expectation) and which rightly
- **WHEN** The test asks Ratchet for the guard table's on-demand before-and-after result (CP6's `ratchet.py shortcuts` command) inside guard-proj
- **THEN** Every table row appears exactly once in the result. Each row the real engine answers rightly (its answer equals the expectation) is counted as fixed (engine A answered it wrongly) or as already right; each row the real engine still answers wrongly is listed by name under 'still open' and is never listed as fixed; the counts add up to the row count; errored is 0. The set of still-open rows is computed from the direct runner run, not hard-coded, so the case stays true if a later guard fix closes more rows.
<!-- proven by test CP7-T09 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: A run that changes the engine shows the guard table at its final review
- **GIVEN** Temp project guard-proj with its approved one-step plan whose step edits skills/ratchet-orchestrator/scripts/ratchet.py, taken through the step's gates to the last step's complete and then to the final review (the step where the agent shows the review text)
- **WHEN** The agent reads the final review (status text for the final review, and the receipt) after the last step completes
- **THEN** The text names the guard table (by its file name or title) and shows its counts for worse, still open, fixed and errored plus the names of any still-open rows; the same facts appear in the receipt; the line is there without anyone having to ask for it
<!-- proven by test CP7-T10 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: A run that does not touch the engine does not show the guard table
- **GIVEN** Temp project guard-proj variant whose only step edits README.md, taken to the final review
- **WHEN** The agent reads the final review and the receipt
- **THEN** Neither mentions the guard table, no shortcut-table result is saved for it, and the final review is otherwise as before (no error, no leftover empty section)
<!-- proven by test CP7-T11 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: A project with no shortcut tables is unaffected by a change to a file named like the engine
- **GIVEN** Temp project plain-proj with no tests/shortcuts folder, one approved step that edits skills/ratchet-orchestrator/scripts/ratchet.py (the same path the guard table watches in Ratchet's own repo)
- **WHEN** The step completes and the agent reads the final review and the receipt
- **THEN** The run finishes normally; the final review and receipt show no guard table, no error and no traceback; the saved evidence has no shortcut-table result. Ratchet's own table only ever runs in a project that ships it.
<!-- proven by test CP7-T12 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: The docs tell agents what the guard table is and that rows are only ever added
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** The test reads them (string lint, tolerant regex alternatives, like tests/test_docs.py)
- **THEN** README.md and the orchestrator SKILL.md each name the guard shortcut table (tests/shortcuts/guard.json or 'guard table'/'shortcut table'), say it is a fixed list of shortcut commands the guard should refuse next to everyday read-only commands it should allow, say a run that changes the engine shows its result at the final review, and say rows are only ever added (never removed, reworded or flipped, and a still-open row stays listed). schemas.md's shortcut-table section lists the shipped table and its runner. The SKILL.md text is aimed at the agent, not at the user typing a command.
<!-- proven by test CP7-T13 in tests/test_shortcuts_guard.py, commit 761b644 -->

#### Scenario: Every row says where it came from, and the guard tests' commands are in the table
- **GIVEN** The shipped table, and lane C's four guard test files in the checkout (tests/test_guard_writes.py with ids CP4-Txx, test_guard_reads.py CP2-Txx, test_guard_python_reads.py CP3-Txx, test_guard_reserved_names.py CP1-Txx)
- **WHEN** The test reads each row's source and, for a source that names a lane C test file, opens that file as text
- **THEN** Every row has a non-empty source that is exactly one of: 'director probe', 'dogfood log', or '<one of the four test file paths> <case id>'; for a test-file source the file exists and the case id appears in that file as a case id; each of the four files is the source of at least one write row or read row, and the table holds both should-refuse and should-allow rows from the writes and reserved-names files taken together; the director probe's twelve write rows (CP7-T01) and the seven everyday reads (CP7-T02) carry 'director probe' or 'dogfood log', not a test-file source. The case only checks which rows exist and where they came from: it never calls the guard and never repeats those files' own verdict checks (the shipped verdicts are shown by the runner, CP7-T04).
<!-- proven by test CP7-T14 in tests/test_shortcuts_guard.py, commit 761b644 -->
