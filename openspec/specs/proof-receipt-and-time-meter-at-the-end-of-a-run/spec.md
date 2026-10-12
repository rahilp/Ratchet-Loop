# Proof receipt and time meter at the end of a run

## Purpose
What this feature does, as built and checked by Ratchet (run proof-receipt-and-time-meter-at-the-end-of-a-run, 2026-10-11). Each scenario names the test that proves it.

## Requirements

### Requirement: Fixed findings leave the left-for-later list
The system SHALL do this: A finding recorded for later that was fixed later in the run can be marked fixed (only when the code really changed and was reviewed again), and the final review no longer lists it, or a re-raised finding twice, as left for later.

- After `ratchet.py mark-fixed CP1 S1="how"` on a finding whose file changed and was reviewed again in a later round, `ratchet.py deferred` shows it under 'fixed later in the run' with the round and reason, not under 'left for later'
- mark-fixed is refused with a plain message when the finding's file has not changed since the round that raised it, when no later review round has been recorded on the changed code, or when the finding was accepted or does not exist
- A finding re-raised in a later round of the same step by the same reviewer with the same id appears once in `deferred`, not twice; a different finding that merely shares a kind and file is never merged into it
- The final-review line in `ratchet.py status` counts only findings still left for later, and `deferred --json` carries each finding's fixed record
- README (command table), the orchestrator SKILL.md (fix rounds and final review: mark deferred findings a fix also closed) and schemas.md (the fixed record on deferred entries) describe the command

#### Scenario: A finding fixed and re-reviewed later moves to 'fixed later in the run'
- **GIVEN** S1 was deferred in round 3 on skills/ratchet-orchestrator/scripts/ratchet.py; that file was edited afterward and a later round 4 was recorded on the changed code
- **WHEN** the orchestrator runs `ratchet.py mark-fixed CP1 S1="added the blocked-state guard"`
- **THEN** `ratchet.py deferred` shows S1 under 'fixed later in the run' with round 4 and the reason, and it no longer appears under 'left for later'
<!-- proven by test CP1-T01 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: mark-fixed is refused when the finding's file never changed
- **GIVEN** S1 was deferred in round 3 on skills/ratchet-orchestrator/scripts/ratchet.py and no commit has touched that file since
- **WHEN** the orchestrator runs `ratchet.py mark-fixed CP1 S1="fixed it"` right away
- **THEN** the command exits non-zero with a plain message naming that the finding's file has not changed since the round that raised it, and `deferred` still lists S1 under 'left for later'
<!-- proven by test CP1-T02 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: mark-fixed is refused when the changed code was never reviewed again
- **GIVEN** S2 was deferred in round 3 on README.md and README.md was edited afterward, but no later adversarial round has been opened or recorded since that edit
- **WHEN** the orchestrator runs `ratchet.py mark-fixed CP1 S2="fixed the doc"`
- **THEN** the command exits non-zero with a plain message naming that no later review round has been recorded on the changed code, and `deferred` still lists S2 under 'left for later'
<!-- proven by test CP1-T03 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: mark-fixed is refused for a finding the user already accepted
- **GIVEN** an open blocking finding was accepted with the user's own `accept: <reason>` reply via `accept-finding`
- **WHEN** the orchestrator runs `ratchet.py mark-fixed CP1 <that finding's ref>="fixed anyway"`
- **THEN** the command exits non-zero with a plain message naming that the finding was accepted, not deferred, and nothing in `deferred`'s accepted section changes
<!-- proven by test CP1-T04 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: mark-fixed is refused for a finding reference that does not exist
- **GIVEN** CP1's deferred list has only S1 and S2
- **WHEN** the orchestrator runs `ratchet.py mark-fixed CP1 S9="fixed it"`
- **THEN** the command exits non-zero with a plain message naming that S9 does not exist, and `deferred` is unchanged
<!-- proven by test CP1-T05 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: A finding re-raised by the same reviewer in a later round appears once
- **GIVEN** S2 (reviewer host-alt-standards) was deferred in round 3 on README.md, and after a later edit and review the same reviewer id raises the same finding id again in round 5, still deferred
- **WHEN** the orchestrator runs `ratchet.py deferred`
- **THEN** S2 is listed exactly once, as round 5's copy (its text and round number), never twice with two round numbers
<!-- proven by test CP1-T06 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: Two findings that share a kind and file but come from different reviewers or ids are both kept
- **GIVEN** S2 (kind docs-out-of-sync, file README.md) was deferred in round 3 from reviewer host-alt-standards, and a later round records a second, genuinely different finding from a different reviewer id (or a different finding id) that also names kind docs-out-of-sync and file README.md
- **WHEN** the orchestrator runs `ratchet.py deferred --json`
- **THEN** the JSON list has two separate entries, one per reviewer/id, each keeping its own round number, description and reviewer; neither is dropped or merged just because the kind and file match
<!-- proven by test CP1-T07 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: status's final-review count excludes findings fixed later in the run
- **GIVEN** the step is at the final review with S1 and S2 both left for later, then S1 is marked fixed with `mark-fixed`
- **WHEN** the orchestrator runs `ratchet.py status`
- **THEN** the final-review line's count of findings left for later drops by exactly one and no longer includes S1
<!-- proven by test CP1-T08 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: deferred --json carries the fixed record only for the finding marked fixed
- **GIVEN** S1 was marked fixed with `mark-fixed CP1 S1="..."` and S2 was left alone
- **WHEN** the orchestrator runs `ratchet.py deferred --json`
- **THEN** S1's entry carries a fixed record with the round it was marked at and the given reason; S2's entry carries no such record
<!-- proven by test CP1-T09 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: Findings the user accepted still show in their own section, unaffected
- **GIVEN** one finding was accepted via `accept-finding` and S1/S2 are ordinary deferred findings, some later marked fixed
- **WHEN** the orchestrator runs `ratchet.py deferred`
- **THEN** the accepted finding still prints first under its own 'Findings accepted by you' section exactly as before this checkpoint, separate from the 'left for later' and 'fixed later in the run' findings
<!-- proven by test CP1-T10 in tests/test_deferred_fixed.py, commit 13c2509 -->

#### Scenario: Docs describe mark-fixed and the de-duplication rule
- **GIVEN** the shipped README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** the test reads them as text
- **THEN** README's command table lists `mark-fixed`, the orchestrator SKILL.md's fix-rounds/final-review section tells the agent to mark deferred findings a fix also closed, and schemas.md documents the fixed record on a deferred entry
<!-- proven by test CP1-T11 in tests/test_deferred_fixed.py, commit 13c2509 -->

### Requirement: Paste a proof receipt into the pull request
The system SHALL do this: A developer runs `ratchet.py receipt` and gets pasteable markdown with each step's commit and time, the run's time, who reviewed each step (own-company fallbacks marked), accepted findings with reasons, and findings left for later.

- `ratchet.py receipt` on a finished run prints markdown listing each step's title, short commit and elapsed time, and the whole run's elapsed time; there is no cost or token figure anywhere
- A step with no commit says 'no commit recorded', a step not finished says so, and a run not finished yet shows time so far with a plain 'run not finished' note instead of guessing
- Each step lists its review rounds and reviewers; a review by the host's own company is marked as such, with why the outside reviewer was skipped when Ratchet recorded it and 'reason not recorded' when it did not, and a reviewer swapped in mid-round is now recorded with its reason too
- The receipt lists findings the user accepted with their reason, findings left for later, and findings fixed later in the run, using the same rule as CP1's `deferred`
- README (command table and final review), the orchestrator SKILL.md (final review: run the receipt and show it) and schemas.md (receipt sections and `--json` shape) describe it

#### Scenario: Receipt prints each step's title, commit and time, plus the run's total time
- **GIVEN** a finished 3-checkpoint run with known per-step commits and elapsed times and a known run elapsed time
- **WHEN** `ratchet.py receipt` runs
- **THEN** the printed markdown lists CP1/CP2/CP3's titles, each one's short commit and a plain duration (e.g. '4h 17m'), and the whole run's own plain duration; no cost figure, token count or '$' appears anywhere in the output
<!-- proven by test CP2-T01 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: A step with no commit says so plainly
- **GIVEN** CP1's gates all passed but its checkpoint entry has no 'commit' field
- **WHEN** `ratchet.py receipt` runs
- **THEN** CP1's line reads 'no commit recorded' instead of a blank, a crash or a guessed commit
<!-- proven by test CP2-T02 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: A step not finished yet says so instead of a guessed time
- **GIVEN** CP2 is still mid-build (no 'completed_at') inside an otherwise-finished-looking run
- **WHEN** `ratchet.py receipt` runs
- **THEN** CP2's line says the step is not finished and shows its time so far from started_at to now, never a completed elapsed time it doesn't have
<!-- proven by test CP2-T03 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: A run not finished yet shows time so far with a plain note
- **GIVEN** every checkpoint is done but the run has no final 'approved' history event yet
- **WHEN** `ratchet.py receipt` runs
- **THEN** the run's time line shows elapsed time so far (from created_at to now) next to a plain 'run not finished' note, never a final total presented as if the run had ended
<!-- proven by test CP2-T04 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: Reviewers are listed per round, with a host-company fallback marked and its reason shown
- **GIVEN** a round that opened fresh and fell back to host-alt-standards/host-alt-correctness, with its substitutions recording a 'why' per skipped outside reviewer (e.g. codex-standards: out of credits)
- **WHEN** `ratchet.py receipt` runs
- **THEN** that round's line names host-alt-standards/host-alt-correctness, marks each as the host's own company standing in, and prints the recorded reason for each outside reviewer it replaced
<!-- proven by test CP2-T05 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: A host fallback with no recorded reason says so plainly
- **GIVEN** an old-style round, built directly in state.json, whose reviewers include a host-vendor reviewer but carries no substitutions data at all (a run recorded before this field existed)
- **WHEN** `ratchet.py receipt` runs
- **THEN** that round's host reviewer is still marked as a host-company fallback, but its reason reads 'reason not recorded' instead of a blank, a crash, or an invented reason
<!-- proven by test CP2-T06 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: A reviewer swapped in mid-round is now recorded with its reason
- **GIVEN** the real run-4 CP1 round-3 shape: a round launches with codex-standards/codex-correctness, both go unavailable mid-round, and cmd_reviewers' same-round re-resolve branch swaps them for host-alt-standards/host-alt-correctness within that same round
- **WHEN** `ratchet.py receipt` runs
- **THEN** that round's line shows host-alt-standards/host-alt-correctness marked as a host-company fallback with the real unavailability reason for each reviewer it replaced (not 'reason not recorded'), proving the round's own `substitutions` list was populated by the mid-round swap itself, not left empty the way the real round 3 data was before this fix
<!-- proven by test CP2-T07 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: Accepted findings are listed with their reason and who accepted them
- **GIVEN** CP1 has a deferred finding accepted via `accept-finding` with reason 'low risk, fixed-in-next-pass tracked separately' and accepted_by 'human'
- **WHEN** `ratchet.py receipt` runs
- **THEN** the accepted-findings section lists that finding's id, severity, description, its exact accepted reason and 'accepted by: human'
<!-- proven by test CP2-T08 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: Findings left for later are listed, separate from accepted ones
- **GIVEN** CP1 has one accepted finding (CP2-T08's fixture) and one still-open minor finding that was never accepted, with its own 'deferred_because' note
- **WHEN** `ratchet.py receipt` runs
- **THEN** the open finding appears only under a 'left for later' section (never mixed into the accepted section), with its description and deferred_because reason
<!-- proven by test CP2-T09 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: A finding fixed later in the run is shown as fixed, not left for later
- **GIVEN** CP1's deferred finding S1 (round 3) was later marked fixed with `ratchet.py mark-fixed CP1 S1="closed in round 3's own fix, confirmed by round 4's review"` after its file changed and a later round reviewed the change
- **WHEN** `ratchet.py receipt` runs
- **THEN** S1 appears under a 'fixed later in the run' section with its round and reason, and does NOT also appear under 'left for later'
<!-- proven by test CP2-T10 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: No cost or token figure appears anywhere in a receipt with rich data
- **GIVEN** a run carrying every fixture above at once (commits, elapsed times, host fallbacks, accepted/open/fixed findings)
- **WHEN** `ratchet.py receipt` runs
- **THEN** scanning the full output for the substrings 'cost', 'token', '$' and 'USD' (case-insensitive) finds none of them anywhere
<!-- proven by test CP2-T11 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: The receipt's --json output carries the same sections with no cost/token keys
- **GIVEN** the same rich run as CP2-T11
- **WHEN** `ratchet.py receipt --json` runs
- **THEN** the output parses as JSON and its steps/time/reviewers/findings sections match the markdown's own facts (same commits, same durations, same reviewer names and reasons, same finding ids), with no key or value anywhere named cost or token(s)
<!-- proven by test CP2-T12 in tests/test_receipt.py, commit 489e65f -->

#### Scenario: README, the orchestrator SKILL.md and schemas.md describe the receipt command
- **GIVEN** the shipped docs after this checkpoint
- **WHEN** README.md's command table and final-review section, skills/ratchet-orchestrator/SKILL.md's final-review step, and skills/ratchet-orchestrator/references/schemas.md are read as text
- **THEN** README documents `ratchet.py receipt` (and `--json`) in its command table and says the final review shows it; SKILL.md's final-review step tells the orchestrator to run the receipt and show it; schemas.md documents the receipt's sections and its `--json` shape
<!-- proven by test CP2-T13 in tests/test_receipt.py, commit 489e65f -->

### Requirement: Show which tests prove each done-means item
The system SHALL do this: Test cases can name the 'done means' items they prove, and the receipt lists every item with its proving tests, or says plainly that none is linked.

- The receipt lists every 'done means' line of the objective with the test cases that prove it (id, title, file) and whether that step's tests passed; a line with no linked test says 'no test linked'
- A plan with no `proves` fields, or no objective at all (approved before these existed), still prints a receipt with a plain note instead of failing
- `plan-submit` refuses a `proves` number that is not a done_means line, naming the case; `plan-summary` lists any done_means line no test case proves, as a heads-up, without refusing
- The test planner's SKILL.md tells it to fill `proves`, and schemas.md section 4 documents the field

#### Scenario: Receipt shows a done-means line next to the one test that proves it, and that it passed
- **GIVEN** CP1's objective done_means line 1 is proved only by case CP1-T01 (tests/test_login.py), and CP1's latest per-case report marks CP1-T01 passed
- **WHEN** ratchet.py receipt is run on the finished run
- **THEN** the done-means section shows done_means line 1's own text with CP1-T01's id, title and file, marked passed
<!-- proven by test CP3-T01 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: A linked test that failed its last report is shown as failed, not hidden or shown as passed
- **GIVEN** done_means line 2 is proved by CP1-T02, whose latest per-case report entry is failed
- **WHEN** ratchet.py receipt is run
- **THEN** done_means line 2's entry names CP1-T02 and marks it failed; the receipt does not say passed or drop the line
<!-- proven by test CP3-T02 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: A done-means line with no case naming it says plainly that no test is linked
- **GIVEN** done_means line 3 (the lockout line) is named by no case's proves in any checkpoint's test plan
- **WHEN** ratchet.py receipt is run
- **THEN** done_means line 3's entry reads "no test linked", with no test id made up and no crash
<!-- proven by test CP3-T03 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: Two cases that both prove the same done-means line are both listed under it
- **GIVEN** done_means line 1 is proved by both CP1-T01 (proves: [1]) and a second case CP1-T04 (proves: [1]) with its own file and its own last pass/fail state
- **WHEN** ratchet.py receipt is run
- **THEN** done_means line 1's entry lists both CP1-T01 and CP1-T04, each with its own id, title, file and pass state
<!-- proven by test CP3-T04 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: One case that proves two done-means lines appears under both of them
- **GIVEN** a case CP1-T05 carries proves: [1, 3]
- **WHEN** ratchet.py receipt is run
- **THEN** CP1-T05 appears in both done_means line 1's entry and done_means line 3's entry
<!-- proven by test CP3-T05 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: A browser-layer case linked to a done-means line is shown as checked by the UI gate, not passed/failed from a report
- **GIVEN** a case CP1-T06 has layer: "browser" and proves: [2]; CP1 has no per-test report entry for it because browser cases are not run by the behavior command
- **WHEN** ratchet.py receipt is run
- **THEN** done_means line 2's entry includes CP1-T06 marked "checked by the UI gate", never shown as passed, failed or no test linked
<!-- proven by test CP3-T06 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: A plan whose cases carry no proves field at all still gets a receipt, with a plain note instead of a crash
- **GIVEN** every case in every checkpoint's test plan has no "proves" key (an approved plan that predates the field)
- **WHEN** ratchet.py receipt is run
- **THEN** the command exits 0 and prints the done-means section with every line reading "no test linked", plus a plain note that this plan's tests were never linked to done_means lines; nothing crashes
<!-- proven by test CP3-T07 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: A plan with no objective at all still gets a receipt, with a plain note instead of a crash
- **GIVEN** state.json's plan has no objective key (approved before the objective field existed)
- **WHEN** ratchet.py receipt is run
- **THEN** the command exits 0, prints the rest of the receipt (steps, commits, times, reviewers, findings) and a plain note that this run has no objective to show done-means proof for, with no done-means section and no traceback
<!-- proven by test CP3-T08 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: plan-submit refuses a case whose proves number is past the end of done_means, naming the case
- **GIVEN** objective.done_means has 3 lines, and a test plan case CP1-T07 carries proves: [9]
- **WHEN** ratchet.py plan-submit runs on this plan
- **THEN** plan-submit exits non-zero and its message names CP1-T07 and says 9 is not a done_means line
<!-- proven by test CP3-T09 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: plan-submit refuses a case whose proves number is not a valid 1-based line (0), naming the case
- **GIVEN** a test plan case CP1-T08 carries proves: [0]
- **WHEN** ratchet.py plan-submit runs on this plan
- **THEN** plan-submit exits non-zero and its message names CP1-T08 and rejects 0 as not a done_means line
<!-- proven by test CP3-T10 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: plan-summary flags an unproven done-means line as a heads-up, without refusing the plan
- **GIVEN** a validly-submitted, approved plan where done_means line 3 (the lockout line) is proved by no case in any checkpoint
- **WHEN** ratchet.py plan-summary runs, after plan-submit and approve-plan already succeeded on this same plan
- **THEN** plan-submit and approve-plan both already exited 0, and plan-summary's output includes a heads-up line naming done_means line 3 as having no proving test case; nothing is refused anywhere in this flow
<!-- proven by test CP3-T11 in tests/test_receipt_proof.py, commit 57d0eac -->

#### Scenario: The test planner's instructions and the schema both document the proves field
- **GIVEN** the shipped files skills/ratchet-tdd-planner/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** read as text
- **THEN** SKILL.md tells the planner to fill a case's "proves" with the objective's done_means line numbers it proves, and schemas.md section 4 documents the "proves" field (optional, a list of 1-based done_means line numbers) next to the existing spec_ref precedent
<!-- proven by test CP3-T12 in tests/test_receipt_proof.py, commit 57d0eac -->
