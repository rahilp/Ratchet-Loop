# Fix: a review round adv-record REFUSED (never recorded) must not advance since_tree/prev_round_tree, so the next round's scoped diff still shows the real fix instead of an empty diff

## Purpose
What this feature does, as built and checked by Ratchet (run fix-a-review-round-adv-record-refused-never-reco, 2026-10-23). Each scenario names the test that proves it.

## Requirements

### Requirement: Refused review rounds keep the fix visible
The system SHALL do this: After adv-record refuses a review round, the next round's reviewer is shown the fix again from the last recorded round, instead of an empty diff.

- After a fix round whose review is refused for omitted_files, the next round's reviewer prompt shows the fix again, measured from the last recorded round, not an empty diff
- Two refused rounds in a row still leave the baseline at the last recorded round
- After a normally recorded round (approved or blocked), the next round's baseline is that round's tree, exactly as before
- A round replaced before anyone reviewed it is still free, a refused round with a written review still costs one attempt, and hitting the round cap still stops with the visible needs-a-decision message
- An open round already carrying a wrong baseline gets the last recorded round's tree back when the next round opens

#### Scenario: After a recorded blocked round, the next fix round is still measured from that round (unchanged)
- **GIVEN** Round 1 (F1) is recorded blocked by ZA-1; the fixer adds ZQ-FIX-A to src/alpha.js and round 2 (F2) opens; round 2's review is recorded blocked with a new finding ZR-2 on src/bravo.js, triaged fix; the fixer adds ZQ-FIX-C to src/bravo.js
- **WHEN** the next reviewers call opens round 3
- **THEN** round 2's open round had prev_round_tree == F1; round 3's prev_round_tree == F2 exactly; round 3's prompt shows the added ZQ-FIX-C line and finding ZR-2, does not show ZQ-FIX-A, and names F2's first 10 characters, not F1's; counters are (3, 3)
<!-- proven by test CP1-T01 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: After a recorded approval, the next small fix is still measured from the approved round (unchanged)
- **GIVEN** Round 1 (F1) is approved as a full round; a 2-line edit to src/bravo.js opens round 2 as a small fix after approval; round 2's one review approves and is recorded (F2); another 2-line edit to src/charlie.js with marker ZQ-LATE
- **WHEN** the next reviewers call opens round 3
- **THEN** round 2's prev_round_tree == F1; round 3's prev_round_tree == F2; round 3's prompt shows the ZQ-LATE line and not round 2's bravo edit; no open findings are listed
<!-- proven by test CP1-T02 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A fix round refused for leaving files out shows the fix again in the next round
- **GIVEN** _big_fix_round: round 1 (F1) recorded blocked, the oversized fix opens round 2 (F2), its signed review carries omitted_files and adv-record refuses it ('never counts'); then a small edit adds ZQ-LATE to src/charlie.js
- **WHEN** the next reviewers call opens round 3
- **THEN** round 3's prev_round_tree == F1 (not F2); round 3 is still a one-reviewer fix round; its prompt names F1's first 10 characters and not F2's, and names src/bravo.js (the fix file no finding mentions), whether shown or listed as not shown; today's code fails this (baseline F2, prompt shows only src/charlie.js)
<!-- proven by test CP1-T03 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A fix round refused for any other reason (too fast, or an approval with no files listed) also shows the fix again
- **GIVEN** Round 1 (F1) recorded blocked; the fixer adds ZQ-FIX-A to src/alpha.js and round 2 (F2) opens; variant a: its review is dated 0 s after the round opened and adv-record refuses it as too fast; variant b: its review approves with files_reviewed [] and adv-record refuses it; in both, the review file stays in round 2's folder; then ZQ-LATE is appended to src/charlie.js
- **WHEN** the next reviewers call opens round 3
- **THEN** in both variants round 3's prev_round_tree == F1; its prompt has an added line containing ZQ-FIX-A and one containing ZQ-LATE, and names F1 not F2; nothing was appended to gates.adversarial.rounds by the refused attempt (still exactly one entry, round 1)
<!-- proven by test CP1-T04 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: Two refused rounds in a row still measure from the last recorded round
- **GIVEN** Round 1 (F1) recorded blocked; ZQ-FIX-A opens round 2, its review is refused as too fast; ZQ-LATE opens round 3, its review is also refused as too fast; then ZQ-FIX-C is added to src/bravo.js
- **WHEN** the next reviewers call opens round 4
- **THEN** round 3's and round 4's prev_round_tree are both F1; round 4's prompt has added lines for ZQ-FIX-A, ZQ-LATE and ZQ-FIX-C and names neither F2's nor F3's first 10 characters; today's code fails this (baseline F3, only ZQ-FIX-C shown)
<!-- proven by test CP1-T05 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A round recorded after a refused one becomes the new baseline
- **GIVEN** Round 1 (F1) recorded blocked; round 2 (ZQ-FIX-A) refused as too fast; round 3 (ZQ-LATE) opens from F1 and its review is recorded blocked with finding ZR-2 (F3), triaged fix; then ZQ-FIX-C is added
- **WHEN** the next reviewers call opens round 4
- **THEN** round 4's prev_round_tree == F3 (the latest recorded round, not F1 and not F2); its prompt shows ZQ-FIX-C and ZR-2 and not ZQ-FIX-A
<!-- proven by test CP1-T06 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A refused small fix after an approval shows that fix again next round
- **GIVEN** Round 1 (F1) approved as a full round; a 2-line edit to src/bravo.js with marker ZQ-FIX-B opens round 2 (F2) as a small fix after approval; its review is refused as too fast; then a 1-line edit to src/charlie.js with marker ZQ-LATE
- **WHEN** the next reviewers call opens round 3
- **THEN** round 3's prev_round_tree == F1; round 3's prompt has added lines for both ZQ-FIX-B and ZQ-LATE; today's code fails this (only ZQ-LATE shown)
<!-- proven by test CP1-T07 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A refused first round leaves no baseline at all
- **GIVEN** A step at its first adversarial round; round 1 (F1) opens and its review is refused as too fast (the review file stays in the round folder); nothing is recorded yet; then src/f.js is edited
- **WHEN** the next reviewers call opens round 2
- **THEN** round 2's prev_round_tree is null (gates.adversarial.rounds is empty), not F1; round 2 is a full round (not small_fix); round 2 counts as attempt 2
<!-- proven by test CP1-T08 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A refused round with a written review still costs one round; a round nobody reviewed is still free
- **GIVEN** Round 1 recorded blocked (counters (1, 1)); ZQ-FIX-A opens round 2 (2, 2); its review is refused as too fast; ZQ-LATE opens round 3
- **WHEN** round 3 opens, and then src/bravo.js is edited again before anyone reviews round 3 and reviewers opens round 4
- **THEN** after round 3 opens the counters are (3, 3) (the refused round was charged, not refunded); after round 4 replaces the unreviewed round 3 they are still (3, 3); round 4's prev_round_tree is still F1 and its prompt shows ZQ-FIX-A
<!-- proven by test CP1-T09 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: Running out of rounds after refusals stops with the visible needs-a-decision message
- **GIVEN** max_adversarial_rounds=3; round 1 recorded blocked; round 2 (ZQ-FIX-A) refused as too fast; round 3 (ZQ-LATE) opens with prev_round_tree F1 and its prompt shows ZQ-FIX-A; round 3's review is refused as too fast; the fixer edits src/bravo.js on disk
- **WHEN** reviewers is called for round 4
- **THEN** it exits 2; the run phase is blocked and blocked_reason contains '3 adversarial rounds used without approval' and 'human/director decision'; attempts stays 3; no adv-round-04 folder exists; status shows the blocked reason
<!-- proven by test CP1-T10 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A stuck unreviewed round carrying a wrong baseline heals on the next round
- **GIVEN** Round 1 (F1) recorded blocked; round 2 (ZQ-FIX-A) refused as too fast; round 3 (ZQ-LATE) is open with no review written, and the test sets its prev_round_tree in the throwaway state.json to F2 (the wrong value today's code leaves, as in lane R CP4)
- **WHEN** src/bravo.js is edited with ZQ-FIX-C and reviewers opens round 4 (a free replacement of round 3)
- **THEN** round 4's prev_round_tree == F1, not F2; its prompt shows ZQ-FIX-A, ZQ-LATE and ZQ-FIX-C; counters are unchanged by the free replacement; today's code fails this (it carries F2 forward)
<!-- proven by test CP1-T11 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: A stuck round that holds a refused review and a wrong baseline heals on the next round
- **GIVEN** Round 1 (F1) recorded blocked; round 2 (ZQ-FIX-A) refused; round 3 (ZQ-LATE) opened, the test sets its prev_round_tree to F2 in state.json, and round 3's own review is refused as too fast (the file stays in its folder)
- **WHEN** src/bravo.js is edited with ZQ-FIX-C and reviewers opens round 4
- **THEN** round 4's prev_round_tree == F1; its prompt shows ZQ-FIX-A, ZQ-LATE and ZQ-FIX-C; the test never edits state.json after round 4 opens
<!-- proven by test CP1-T12 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: Asking again with no code change keeps the same open round and its baseline
- **GIVEN** Round 1 (F1) recorded blocked; round 2 (ZQ-FIX-A) open with prev_round_tree F1; its review was refused as too fast; the code is not touched
- **WHEN** reviewers is called again
- **THEN** the open round is still round 2 with fingerprint F2 and prev_round_tree F1; counters do not move
<!-- proven by test CP1-T13 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: adv-record still refuses exactly what it refused before
- **GIVEN** Fix rounds set up as in T03 and T04, plus a two-reviewer full round where only one of the two reviews is passed
- **WHEN** adv-record is called with each bad review: one carrying omitted_files, one dated 0 s after the round opened, one approving with files_reviewed [], and the missing-reviewer set
- **THEN** each call exits non-zero with its own message ('never counts', 'too fast to be a real review', 'approves without listing the changed files it reviewed', 'needs reviews from ... missing'); the open round is not marked recorded and gates.adversarial.rounds gains no entry
<!-- proven by test CP1-T14 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: Only the code that sets a new round's baseline changed
- **GIVEN** This run's own base_tree, read dynamically from the run's state.json (currently 4a1836494b7f7ebe37be1d71412efa31535f3f76); never a pinned commit SHA, so later lanes may legitimately edit these files
- **WHEN** the base versions are compared with the working tree
- **THEN** skills/ratchet-adversarial-loop/scripts/external_review.py is byte-identical; in ratchet.py the function bodies of _scope_for_round and cmd_adv_record (compared by AST with docstrings removed, so comment and docstring edits are allowed) are unchanged; in cmd_reviewers the free_replace refund block is unchanged. When the run folder or its base_tree is unavailable (another clone, or after the run is archived) the case does not fail, but prints a distinct note, 'base tree unavailable, scope check skipped structurally', instead of its normal pass message, so a log reader can tell a skipped check from a passed one; the CP1 git commit is the durable record of what changed
<!-- proven by test CP1-T15 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->

#### Scenario: The docs say where the next round's baseline comes from and that a refused round still costs a round
- **GIVEN** skills/ratchet-orchestrator/references/schemas.md and skills/ratchet-adversarial-loop/SKILL.md
- **WHEN** they are read
- **THEN** schemas.md's prev_round_tree / since_tree text says the baseline is the last round adv-record recorded, and that a refused or never-reviewed round never moves it; schemas.md or SKILL.md says a refused round with a written review still counts one attempt and the round cap's needs-a-decision block is the way out; the old 'carried forward through a free replacement' sentence no longer stands as the whole rule; CP6-T22's doc phrases in tests/test_fix_round_diff.py still match
<!-- proven by test CP1-T16 in tests/test_since_tree_rejected_round.py, commit f3d8934 -->
