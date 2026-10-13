# A quick path for small changes, including docs-only changes. Small changes, and documentation prose edits (README and other docs) made after a step's review approved, get a lighter check instead of a full adversarial review round plus the full regression suite. Doc content that tests check against code (for example the README's per-CLI table that tests/test_docs.py compares with HOST_ENFORCEMENT) stays tested. The bar for code that can hurt users does not drop.

## Purpose
What this feature does, as built and checked by Ratchet (run a-quick-path-for-small-changes-including-docs-on, 2026-10-12). Each scenario names the test that proves it.

## Requirements

### Requirement: Ratchet shows which changes are docs
The system SHALL do this: A developer can run `ratchet.py changes CPn` to see which files changed since the step started and since review approved, which of them are docs, which test files name each doc, how many code lines changed, and which check (docs quick path, small fix, or full) would apply and why.

- Running `ratchet.py changes CP1` after editing only README.md says the change is docs only and lists the test files that mention README.md
- Editing a .py file as well makes it say the change touches code and how many code lines changed
- A SKILL.md or agents/*.md edit is reported as code, not docs
- When no doc test command is configured it says the docs quick path is off and why
- The command changes nothing: run state and files are the same before and after

#### Scenario: Editing only the README reports a docs-only change and names the test that checks it
- **GIVEN** CP1 is approved (base_tree recorded) and tests/readme_check.test.js mentions README.md by name; tests/cp1.test.js mentions no doc
- **WHEN** README.md is edited (a sentence changed) and `ratchet.py changes CP1` is run
- **THEN** the change since step start is reported as docs only, README.md is listed as the one doc file changed, no code files are listed, and tests/readme_check.test.js is listed as a test file that names it -- tests/cp1.test.js is not listed
<!-- proven by test CP1-T01 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: Editing a doc plus a code file reports code touched and the code line count
- **GIVEN** CP1 is approved; src/a.js's single line is replaced with 4 new lines (a net change of 5 lines by git diff --numstat: 4 added, 1 removed)
- **WHEN** README.md and src/a.js are both edited and `ratchet.py changes CP1` is run
- **THEN** the change since step start is reported as touching code (not docs only), src/a.js is listed among the code files, and the reported code-lines-changed count is exactly 5
<!-- proven by test CP1-T02 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: A SKILL.md edit alone is reported as code, not docs
- **GIVEN** CP1 is approved and the project has a skills/x/SKILL.md file
- **WHEN** only SKILL.md's text is edited and `ratchet.py changes CP1` is run
- **THEN** the change is reported as touching code, not docs only; SKILL.md appears in the code files (or an equivalent 'excluded doc' list), never in the doc files
<!-- proven by test CP1-T03 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: An agents/*.md edit is excluded from docs by its folder, not just its name
- **GIVEN** CP1 is approved and the project has agents/helper.md (not literally named SKILL.md/AGENTS.md etc., but under agents/)
- **WHEN** only agents/helper.md is edited and `ratchet.py changes CP1` is run
- **THEN** the change is reported as touching code, not docs only; agents/helper.md is never listed as a doc file, proving the agents/*.md glob is matched, not a literal-name list
<!-- proven by test CP1-T04 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: The configured design doc is excluded from docs even though it matches *.md
- **GIVEN** CP1 is approved and DESIGN.md exists at the repo root (the default design_md)
- **WHEN** only DESIGN.md is edited and `ratchet.py changes CP1` is run
- **THEN** the change is reported as touching code, not docs only; DESIGN.md is never listed as a doc file
<!-- proven by test CP1-T05 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: No doc test command configured turns the docs quick path off, with the reason shown
- **GIVEN** CP1 is approved and doc_test_command was never set
- **WHEN** README.md is edited and `ratchet.py changes CP1` is run
- **THEN** the output says the docs quick path is off, and the stated reason names doc_test_command as not configured -- this is true even though the diff itself is still reported as docs only
<!-- proven by test CP1-T06 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: Running `changes` changes nothing
- **GIVEN** CP1 is approved and README.md has been edited
- **WHEN** `ratchet.py changes CP1` is run twice in a row
- **THEN** state.json and checkpoints.json under .ratchet/runs/f are byte-identical before and after each run, the git working tree's fingerprint (git write-tree id) is unchanged, no commit is created, and no new file appears anywhere under .ratchet/
<!-- proven by test CP1-T07 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: No changes since step start reports cleanly, with nothing to do
- **GIVEN** CP1 is approved and nothing has been edited since
- **WHEN** `ratchet.py changes CP1` is run
- **THEN** the command exits 0, reports zero doc files and zero code files changed since step start, zero code lines changed, and does not crash or print a stray error
<!-- proven by test CP1-T08 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: Multiple docs are matched to the right test files, with no guessed matches
- **GIVEN** CP1 is approved; docs/guide.md is named in tests/test_guide.py, CHANGELOG.md is named in tests/changelog.test.js, and NOTES.rst is named in no test file at all
- **WHEN** docs/guide.md, CHANGELOG.md and NOTES.rst are all edited (no code file touched) and `ratchet.py changes CP1` is run
- **THEN** the change is reported as docs only; all three files are listed as doc files; the doc-test-files list contains exactly tests/test_guide.py and tests/changelog.test.js (each only because it names its own doc, never because it names the other doc); NOTES.rst contributes no entry and the command does not invent or omit one for it
<!-- proven by test CP1-T09 in tests/test_quick_path.py, commit ede089b -->

#### Scenario: A non-.md doc extension is still classified as docs
- **GIVEN** CP1 is approved and NOTES.rst exists in the project
- **WHEN** only NOTES.rst is edited and `ratchet.py changes CP1` is run
- **THEN** the change is reported as docs only, and NOTES.rst is listed as the doc file changed
<!-- proven by test CP1-T10 in tests/test_quick_path.py, commit ede089b -->

### Requirement: Doc edits after review skip re-review
The system SHALL do this: When only doc files changed after a step's reviewers approved, the step completes without a new review round: Ratchet re-runs the test files that name those docs, records the edit as not re-reviewed, and runs the full suite only if code changed since the suite last passed.

- After reviewers approve, editing a README sentence and running `complete` commits the step with no new review round
- Breaking the README table that a test checks makes `complete` fail with that test file named, and the step does not commit
- The commit message and `status` list the README as edited after review and not re-reviewed, and the final review shows it
- Editing a doc plus any code file after approval still marks the review stale, exactly as before
- When the full suite already passed on a tree that differs from now only in docs, `complete` runs only the doc test files instead of the full suite

#### Scenario: A doc edit after approval does not reopen review
- **GIVEN** CP1's behavior and adversarial gates have already passed and been approved at tree F
- **WHEN** the developer edits only README.md (a sentence, not anything a test checks) and runs `ratchet.py complete CP1`
- **THEN** complete succeeds and commits CP1; no new adversarial round is opened and the gates' stored status stays 'passed', never 'stale'
<!-- proven by test CP2-T01 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: A doc edit that breaks a doc-checked test fails complete, naming the test
- **GIVEN** a test file whose doc_test_command checks a marker string inside README.md, and CP1's gates have already passed and been approved
- **WHEN** the developer deletes that marker from README.md (still the only file touched) and runs `ratchet.py complete CP1`
- **THEN** complete exits non-zero, the error names the specific test file that checks README.md, and CP1 does not commit (git HEAD and the gates' stored status are unchanged)
<!-- proven by test CP2-T02 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: The skipped review is visible in the commit message, status and the final review
- **GIVEN** the same successful doc-only completion as CP2-T01
- **WHEN** `ratchet.py complete CP1` succeeds
- **THEN** the git commit message for CP1 names README.md as edited after review and not re-reviewed; `ratchet.py status` shows the same line for CP1; once the run's last checkpoint completes, the final review text also lists it
<!-- proven by test CP2-T03 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: A doc plus a code edit after approval is still fully stale
- **GIVEN** CP1's behavior and adversarial gates have already passed and been approved at tree F
- **WHEN** the developer edits README.md and also edits src/a.js, then runs `ratchet.py complete CP1`
- **THEN** complete refuses exactly as before this feature: the behavior and adversarial gates are marked stale, the error says code changed and a new review is needed, and nothing commits -- the doc edit does not earn any partial skip
<!-- proven by test CP2-T04 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: SKILL.md and agents/*.md edits are never treated as doc edits
- **GIVEN** CP1's gates have already passed and been approved, and the repo already tracks a SKILL.md file
- **WHEN** the developer edits only SKILL.md after approval and runs `ratchet.py complete CP1`
- **THEN** complete treats this exactly like a code change (CP2-T04's outcome): the gates are marked stale and nothing commits, even though the file ends in .md
<!-- proven by test CP2-T05 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: Only the test files that name the edited doc are re-run
- **GIVEN** two test files exist; only one of their source texts contains the literal path or file name of docs/notes.md, and CP1's gates have already passed and been approved
- **WHEN** the developer edits only docs/notes.md after approval and runs `ratchet.py complete CP1`
- **THEN** complete runs the one test file that names docs/notes.md and does not run the other test file; the step still completes
<!-- proven by test CP2-T06 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: A doc edit that no test file names still completes
- **GIVEN** a doc file that no test file's text mentions, and CP1's gates have already passed and been approved
- **WHEN** the developer edits only that doc after approval and runs `ratchet.py complete CP1`
- **THEN** complete succeeds and commits with no test command run for that doc, and still records the doc as edited after review and not re-reviewed
<!-- proven by test CP2-T07 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: complete skips the full suite when only docs changed since it last passed
- **GIVEN** a two-checkpoint run where regression_command already ran and passed once, recording the tree it passed on, and the second checkpoint's own gates pass and are approved at that exact tree (no new non-doc file beyond it)
- **WHEN** the developer edits only a doc after the second checkpoint's approval and runs `ratchet.py complete` for it
- **THEN** the regression_command is not invoked again (its invocation count does not increase) and only the doc's named test files run; the step still completes
<!-- proven by test CP2-T08 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: complete still runs the full suite when real code changed since it last passed
- **GIVEN** the same two-checkpoint run as CP2-T08, but the second checkpoint's own gates pass at a tree that includes new non-doc code (not only the first checkpoint's tree)
- **WHEN** `ratchet.py complete` is run for the second checkpoint, with no post-approval doc edit at all
- **THEN** the regression_command runs again (its invocation count increases) exactly as before this feature, proving the skip in CP2-T08 does not become an always-skip
<!-- proven by test CP2-T09 in tests/test_quick_path_docs.py, commit 26adab9 -->

#### Scenario: A failed doc-test run never marks the gates passed or commits partial work
- **GIVEN** the same broken-marker setup as CP2-T02
- **WHEN** `ratchet.py complete CP1` fails on the doc-checked test
- **THEN** the behavior and adversarial gates' stored status is left exactly as it was (not silently flipped to 'passed' or 'stale'), the working tree still shows the uncommitted doc edit, and a second `ratchet.py complete CP1` after fixing the marker back succeeds
<!-- proven by test CP2-T10 in tests/test_quick_path_docs.py, commit 26adab9 -->

### Requirement: Small fixes get a one-reviewer check
The system SHALL do this: A code fix of about 20 changed lines or fewer made after approval, in a step that is not high risk, is reviewed by one reviewer looking only at that fix instead of a full review round; the step's tests and the full suite still run.

- After approval, a 5-line code fix makes the next review round open with one reviewer, told to review only the fix since approval
- A 40-line fix, or any fix in a high-risk step, opens the normal full round with every reviewer
- The step's own tests re-run and the full suite runs before the step commits, as before
- `status` and the commit message say the fix was reviewed as a small fix by one reviewer
- The small-fix round counts toward the step's round limit like any other round

#### Scenario: A small code fix after approval opens a one-reviewer round scoped to the fix
- **GIVEN** CP1 is risk=normal and its adversarial gate has a clean, approved round 1
- **WHEN** src/f.js is changed by about 5 lines and `ratchet.py reviewers CP1` is run again
- **THEN** round 2 opens naming exactly one reviewer (not the normal-risk step's usual two), and the round's own focus/scope names only the file(s) changed since round 1, not the whole checkpoint
<!-- proven by test CP3-T01 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: A fix over the small-fix limit opens the normal full round
- **GIVEN** CP1 is risk=normal and its adversarial gate has a clean, approved round 1
- **WHEN** src/f.js is changed by about 25 lines and `ratchet.py reviewers CP1` is run again
- **THEN** round 2 opens naming the full normal-risk reviewer set (two reviewers), not a single small-fix reviewer
<!-- proven by test CP3-T02 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: Any fix in a high-risk step gets the full round, however small
- **GIVEN** CP1 is risk=high (with risk_reason) and its adversarial gate has a clean, approved round 1
- **WHEN** src/f.js is changed by about 3 lines and `ratchet.py reviewers CP1` is run again
- **THEN** round 2 opens naming every reviewer the high-risk depth gives (four reviewers), never the single small-fix reviewer
<!-- proven by test CP3-T03 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: A fix of exactly the small-fix limit still counts as small
- **GIVEN** CP1 is risk=normal and its adversarial gate has a clean, approved round 1
- **WHEN** src/f.js is changed by exactly 20 added+removed lines and `ratchet.py reviewers CP1` is run again
- **THEN** round 2 opens naming exactly one reviewer
<!-- proven by test CP3-T04 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: One line over the limit is a full fix, not a small one
- **GIVEN** CP1 is risk=normal and its adversarial gate has a clean, approved round 1
- **WHEN** src/f.js is changed by exactly 21 added+removed lines and `ratchet.py reviewers CP1` is run again
- **THEN** round 2 opens naming the full normal-risk reviewer set, not one reviewer
<!-- proven by test CP3-T05 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: A small code fix mixed with a doc edit still counts only its code lines
- **GIVEN** CP1 is risk=normal and its adversarial gate has a clean, approved round 1
- **WHEN** src/f.js is changed by about 5 lines AND README.md is also edited by about 50 lines in the same working tree, then `ratchet.py reviewers CP1` is run
- **THEN** round 2 still opens naming exactly one reviewer: the doc file's changed lines are not counted toward the small-fix limit
<!-- proven by test CP3-T06 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: The step's tests and the full suite still run before a small-fix round's step completes
- **GIVEN** CP1's round-2 small-fix review (one reviewer) is clean and recorded as approved, the step's own test command has been re-run and passes, and cfg.regression_command is set to a command that always fails
- **WHEN** `ratchet.py complete CP1` is run
- **THEN** complete fails on that regression command's own failure (proof it actually ran the full suite rather than silently treating this as a docs-only completion that skips it)
<!-- proven by test CP3-T07 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: Status and the commit record say a fix was reviewed as a small fix by one reviewer
- **GIVEN** CP1 completed after a round-2 small-fix review approved by a single reviewer
- **WHEN** `ratchet.py status --json` is read for CP1, and the checkpoint's own commit message is read with `git log`
- **THEN** both say the step's last review round was a one-reviewer small-fix review and name which reviewer did it
<!-- proven by test CP3-T08 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: A small-fix round counts toward the step's round limit like any other round
- **GIVEN** CP1 is risk=normal, approved in round 1, with the gate's attempts count and the run-wide round count both at their round-1 values
- **WHEN** a small fix (about 5 lines) opens and records round 2 as a one-reviewer small-fix round
- **THEN** the gate's own attempts count and the run-wide adversarial round count have each increased by exactly one, same as an ordinary round, so the step's round-limit cap counts it too
<!-- proven by test CP3-T09 in tests/test_quick_path_small.py, commit 91d5f9a -->

#### Scenario: A doc-only edit after approval is still a zero-reviewer skip, not a downgraded one-reviewer round
- **GIVEN** CP1 is risk=normal and its adversarial gate has a clean, approved round 1
- **WHEN** only README.md is edited after approval (no lines of src/f.js change) and the step is driven to complete
- **THEN** the doc-only path from CP2 applies (no adversarial round is opened or required for this edit); CP3's size-based small-fix rule, which would also see 0 changed code lines as 'under the limit', must not instead force open a one-reviewer round for a change that should need no review at all
<!-- proven by test CP3-T10 in tests/test_quick_path_small.py, commit 91d5f9a -->

### Requirement: Docs-only steps skip adversarial review
The system SHALL do this: When a planned step's actual changes are docs only, opening its review instead runs the test files that name those docs and records the review as skipped with the reason, so the step can complete without reviewers.

- A step that only edits README.md reaches complete without any reviewer, and `status`, the commit message and the final review say its review was skipped because it changed only docs
- A step planned as docs-only that also edits a .py file gets the normal review round
- A docs-only step whose README edit breaks a test that checks the README fails and does not commit
- A docs-only step marked high risk still gets its review
- `plan-summary` tells the user, before they approve, that docs-only steps skip review and doc edits after review get tests only

#### Scenario: A docs-only step's review opens without any reviewer round
- **GIVEN** CP1's only change since the step started is editing README.md (its planned test checks README content and already passed behavior)
- **WHEN** the agent asks Ratchet to open CP1's adversarial review (`ratchet.py reviewers CP1`)
- **THEN** no reviewer round is opened (no round folder under reviews/CP1, no reviewer entries returned) and CP1's adversarial gate is recorded passed, with a recorded reason naming README.md and 'docs only'
<!-- proven by test CP4-T01 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: A docs-only step completes without ever calling adv-record
- **GIVEN** CP1's review was opened and skipped as in CP4-T01, and `adv-record` was never called for CP1
- **WHEN** the agent runs `ratchet.py complete CP1`
- **THEN** complete succeeds (exit 0) and the step is recorded done with a commit, with no error about a missing or unpassed adversarial gate
<!-- proven by test CP4-T02 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: The commit message says the review was skipped and names the unreviewed file
- **GIVEN** CP1 completed via the docs-only skip path (CP4-T02)
- **WHEN** the git commit message for CP1's commit is read
- **THEN** it states the review was skipped because the step changed only docs and names README.md as not reviewed
<!-- proven by test CP4-T03 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: status and the final review both show the skipped docs-only review
- **GIVEN** CP1 completed via the docs-only skip path, and (in a single-checkpoint plan) the run has reached its final review
- **WHEN** `ratchet.py status --json` is read right after CP1 completes, and `ratchet.py status` (text) is read once the run reaches the final review
- **THEN** both outputs name CP1's review as skipped because it changed only docs, naming README.md, consistent with each other
<!-- proven by test CP4-T04 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: A step that touches docs and code gets the normal full review, not a skip
- **GIVEN** CP1's changes since step start are README.md plus an edit to src/f.js
- **WHEN** the agent runs `ratchet.py reviewers CP1 --json`
- **THEN** a normal round opens with real reviewer entries (same as the non-docs path) and CP1's adversarial gate is not marked passed by a skip; status does not say the review was skipped
<!-- proven by test CP4-T05 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: A docs-only step whose edit breaks an unrelated doc-checking test fails and does not commit
- **GIVEN** CP1's only change since step start is README.md; CP1's own planned test (checked by behavior) still passes, but the README edit breaks the marker checked by the separate, unplanned test file that also names README.md
- **WHEN** the agent runs `ratchet.py reviewers CP1` (which re-runs every test file naming README.md) and then `ratchet.py complete CP1`
- **THEN** the review-open step fails, naming the failing test file; CP1's adversarial gate is not passed; `complete CP1` also fails and CP1 is not committed
<!-- proven by test CP4-T06 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: A high-risk docs-only step still gets its full review
- **GIVEN** CP1 is planned risk: high (with a risk_reason) and its only change since step start is README.md
- **WHEN** the agent runs `ratchet.py reviewers CP1 --json`
- **THEN** a normal round opens with real reviewer entries; the review is not skipped even though the diff is docs only
<!-- proven by test CP4-T07 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: With the docs quick path off, a docs-only step still gets a full review
- **GIVEN** config has no doc_test_command set, and CP1's only change since step start is README.md
- **WHEN** the agent runs `ratchet.py reviewers CP1 --json`
- **THEN** a normal round opens with real reviewer entries; CP1's adversarial gate is not skipped, and status/the round output says why (docs quick path is off)
<!-- proven by test CP4-T08 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

#### Scenario: plan-summary tells the user up front about the docs-only and small-fix quick paths
- **GIVEN** a plan has been submitted but not yet approved
- **WHEN** the agent runs `ratchet.py plan-summary`
- **THEN** the output states, in plain words, that a step whose actual changes are docs only skips its adversarial review, and that a doc edit made after a step's review gets its naming test files re-run instead of a new review round
<!-- proven by test CP4-T09 in tests/test_quick_path_docs_step.py, commit 0fc0c02 -->

### Requirement: A full review clears skipped-review notes
The system SHALL do this: When a step's review was skipped or lightened and a later full review round approves the step, Ratchet stops saying the step went unreviewed or was only checked by one reviewer, and the orchestrator instructions describe the small-fix limit the way the code measures it.

- A docs-only step whose review was skipped, then given a code edit and approved by a full review round, commits with no 'review skipped' line in the commit message, `status` or the final review
- A step where a doc was edited after approval, then approved again by a full review round, no longer lists that doc as edited after review and not re-reviewed
- A small fix reviewed by one reviewer, followed by a full review round that approves, is no longer described as a one-reviewer small fix
- A doc edited after that later full approval is still listed as not re-reviewed, and a one-reviewer small-fix approval never clears a skipped-review note
- The orchestrator instructions (SKILL.md) say the small-fix limit counts changed lines since the step's last full review approval

#### Scenario: A later full review round clears a docs-only skip note
- **GIVEN** A one-checkpoint, normal-risk project whose step's own diff since it started is docs only (README.md), so its first pass through the adversarial gate is CP4's docs-only skip (no round opened, gate marked passed directly, docs_only_review_skip recorded naming README.md). A src code file is then edited and behavior is re-run; since the docs-only skip never recorded a full-review baseline, the next `reviewers CP1` call opens the step's normal full round (every usual reviewer), never a one-reviewer small-fix round.
- **WHEN** Every reviewer that full round names writes a clean (no-findings) review and `adv-record CP1` records it approved, then `complete CP1` runs.
- **THEN** `complete CP1` succeeds. Neither the commit message, `status`, nor the run's final review say CP1's review was skipped or that README.md went unreviewed any more; they describe a normally reviewed step.
<!-- proven by test CP5-T01 in tests/test_quick_path_records.py, commit cf098ba -->

#### Scenario: A later full review round clears a doc-edited-after-review note
- **GIVEN** A one-checkpoint, normal-risk project whose round 1 (the step's full reviewer set) approves cleanly, recording last_full_review_fingerprint. The checkpoint's own doc_edits_unreviewed record is then primed to name a doc file. A non-doc code file is then edited (large enough, or the step marked high risk, that it is never mistaken for a small fix) and a new round opens, naming the step's full reviewer set.
- **WHEN** Every reviewer in that round writes a clean review and `adv-record CP1` records it approved.
- **THEN** Reading the checkpoint's own state right after `adv-record` shows doc_edits_unreviewed empty/absent. Running `complete CP1` afterward (with no further edits) succeeds, and neither the commit message nor `status` say any doc was edited after review and not re-reviewed.
<!-- proven by test CP5-T02 in tests/test_quick_path_records.py, commit cf098ba -->

#### Scenario: A later full review round clears a small-fix-by-one-reviewer note
- **GIVEN** A one-checkpoint, normal-risk project: round 1 (full, every usual reviewer) approves cleanly. A small code fix (about 5 changed lines, at or under small_edit_lines) then opens and gets approved as a one-reviewer small-fix round, recording small_fix_review. A further code fix that is NOT small on its own (over small_edit_lines measured since round 1's own approval) is made next, opening the step's normal full round again.
- **WHEN** Every reviewer in that full round writes a clean review and `adv-record CP1` records it approved.
- **THEN** The checkpoint's small_fix_review record is gone. `complete CP1` succeeds, and neither the commit message nor `status` say the step was reviewed as a small fix by one reviewer any more.
<!-- proven by test CP5-T03 in tests/test_quick_path_records.py, commit cf098ba -->

#### Scenario: A doc edited after a clearing full approval is freshly flagged, not silently covered by the clearing
- **GIVEN** CP5-T01's own flow: a full round clears an earlier docs-only-skip note and is approved, with the adversarial gate now at the current fingerprint and the checkpoint not yet completed.
- **WHEN** A different doc file (one never part of that full round's own diff) is edited on its own -- no code -- and `complete CP1` is run.
- **THEN** `complete CP1` succeeds via the doc quick path, and the commit message/`status` now say THIS new doc file was edited after review and not re-reviewed: clearing a past note never suppresses a genuinely new one.
<!-- proven by test CP5-T04 in tests/test_quick_path_records.py, commit cf098ba -->

#### Scenario: A one-reviewer small-fix approval never clears a skipped-review note
- **GIVEN** A one-checkpoint, normal-risk project whose round 1 (full) approves cleanly, recording last_full_review_fingerprint. The checkpoint's own doc_edits_unreviewed record is then primed to name a doc file. A small code fix (about 5 lines, at or under small_edit_lines measured since round 1's approval) is made next.
- **WHEN** Reviewers CP1 opens (naming exactly one reviewer, a small-fix round) and that reviewer's clean review is recorded by `adv-record`.
- **THEN** The checkpoint's doc_edits_unreviewed record is unchanged (still names the same doc file) and its small_fix_review record is now set alongside it: a small-fix approval records its own kind of lighter review but never clears any other lighter-review note.
<!-- proven by test CP5-T05 in tests/test_quick_path_records.py, commit cf098ba -->

#### Scenario: A full round that is not approved clears nothing
- **GIVEN** The same primed state as CP5-T02 (doc_edits_unreviewed names a doc file, last_full_review_fingerprint set) plus a non-doc code edit that opens the step's normal full round.
- **WHEN** One of that round's required reviewers writes a review with an open blocking finding (a blocker, with a user_impact) and `adv-record CP1` is run with every required reviewer's review.
- **THEN** `adv-record` reports the round NOT approved (a blocking finding remains). The checkpoint's doc_edits_unreviewed record is still exactly what it was before this round opened: a round that fails to get approved never clears any lighter-review note, only an approved full round does.
<!-- proven by test CP5-T06 in tests/test_quick_path_records.py, commit cf098ba -->

#### Scenario: One full approval clears every lighter-review record present at once
- **GIVEN** A one-checkpoint, normal-risk project with BOTH a doc_edits_unreviewed record (primed, naming a doc file) and a small_fix_review record (set by a real, approved one-reviewer small-fix round) present on the same checkpoint at the same time, then a further, not-small code edit that opens the step's normal full round.
- **WHEN** Every reviewer in that full round writes a clean review and `adv-record CP1` records it approved.
- **THEN** Both records -- doc_edits_unreviewed and small_fix_review -- are gone from the checkpoint's state after this one approval, and `complete CP1` afterward produces a commit message/`status` naming neither.
<!-- proven by test CP5-T07 in tests/test_quick_path_records.py, commit cf098ba -->

#### Scenario: SKILL.md measures the small-fix limit against the step's last full review approval
- **GIVEN** skills/ratchet-orchestrator/SKILL.md's own paragraph describing the small-fix one-reviewer round.
- **WHEN** That paragraph is read as plain text.
- **THEN** It says the small_edit_lines limit is counted against changed lines since the step's last FULL review approval (not simply 'that approval' or 'the last approval', which would misdescribe CP3's own add-up-across-small-fixes rule) -- in some plain-English way naming 'full' review/approval as the baseline.
<!-- proven by test CP5-T08 in tests/test_quick_path_records.py, commit cf098ba -->
