# Several features as one request: first an integrate command (a director action) that merges a finished lane into main, handles Ratchet's own config files by Ratchet's rules, runs the suite, stops and rolls back on failure, and records what it did; then a director policy file so routine approvals are signed under rules the developer sets once; then a command that starts and tracks several lanes from one request.

## Purpose
What this feature does, as built and checked by Ratchet (run several-features-as-one-request-first-an-integra, 2026-10-19). Each scenario names the test that proves it.

## Requirements

### Requirement: Integrate checks a lane, changes nothing
The system SHALL do this: A director can ask Ratchet whether a lane is ready to merge into main and see exactly what integrate would do, with plain refusals for every case that is not safe, and nothing on disk changes.

- From the director's session, `integrate <lane> --dry-run` on a ready lane exits 0 and names the lane's run, the commits it would bring to main, which of Ratchet's own files differ (changed on either side since the lane branched), which uncommitted Ratchet files on main it would carry across (never commit), and the suite it would run
- A lane whose run is not finished, a lane with uncommitted project changes, a merge in progress in either checkout, a main checkout with uncommitted project changes, and a main checkout whose own run is mid-step are each refused with a non-zero exit and one plain line on what to do next; uncommitted changes only under the lane's .ratchet/ do not block
- The same command from a builder session is refused by the guard, like the other director commands
- Nothing in main, the lane or either run's state changes, and no git command that writes or pushes is run

#### Scenario: Dry run on a finished lane says what integrate would do
- **GIVEN** Lane-a (worktree path given) is a finished run 'feature-a', two commits ahead of main ('add feature a', 'add tests for a'), and the config's regression_command is `python3 tests/run_all.py`
- **WHEN** The director runs `integrate <lane path> --dry-run` with --root pointing at main
- **THEN** Exit code 0; the output names the run 'feature-a', lists both commit subjects, names the suite command `python3 tests/run_all.py` it would run, and says plainly that it is a dry run and nothing was changed
<!-- proven by test CP1-T01 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: The lane can be named by its branch instead of its folder
- **GIVEN** The same finished lane, which exists only as a worktree on branch lane-a
- **WHEN** The director runs `integrate lane-a --dry-run`
- **THEN** The result is the same as naming the folder: it finds the lane's worktree, names run 'feature-a' and both commits, exit 0
<!-- proven by test CP1-T02 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: It lists which of Ratchet's own files differ
- **GIVEN** The lane changed .ratchet/learnings.md and .ratchet/config.json; neither side changed .ratchet/standards.md or .ratchet/.gitignore
- **WHEN** The director runs the dry run
- **THEN** The output names learnings.md and config.json as differing and does not name standards.md or .gitignore as differing; a lane that changed none of the four shows a line saying so
<!-- proven by test CP1-T03 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: A lane whose run is still building is refused
- **GIVEN** Lane-a's run 'feature-a' is in phase building
- **WHEN** The director runs the dry run
- **THEN** Non-zero exit; one plain line says the lane's run is not finished (naming its phase) and what to do next (finish it with the lane's agent, then try again); no traceback; nothing on disk changes
<!-- proven by test CP1-T04 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: A lane with no run, or a folder that is not a lane, is refused
- **GIVEN** A) a worktree whose .ratchet has no active run; B) a branch name with no worktree; C) a plain folder that is not a git worktree of this repo
- **WHEN** The director runs the dry run for each
- **THEN** Each exits non-zero with one plain line that names what is missing and how to point at a real lane (give the lane's folder or branch); no traceback; nothing changes
<!-- proven by test CP1-T05 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: A lane with uncommitted project changes is refused
- **GIVEN** Lane-a's run is done but src/b.js in the lane worktree is edited and not committed
- **WHEN** The director runs the dry run
- **THEN** Non-zero exit; one plain line names src/b.js (or says the lane has uncommitted changes) and tells the director to have the lane's agent commit or discard them; nothing changes
<!-- proven by test CP1-T06 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Uncommitted changes under .ratchet in the lane do not block
- **GIVEN** Lane-a's run is done and the only uncommitted change in its worktree is a run evidence file under .ratchet/
- **WHEN** The director runs the dry run
- **THEN** Exit 0 and the normal plan is shown, because the lane is finished apart from Ratchet's own bookkeeping
<!-- proven by test CP1-T07 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: A lane with a merge in progress is refused
- **GIVEN** Lane-a's run is done but its worktree is in the middle of a merge (MERGE_HEAD exists in its git dir)
- **WHEN** The director runs the dry run
- **THEN** Non-zero exit; one plain line says a merge is in progress in the lane and to finish or abort it there first; nothing changes
<!-- proven by test CP1-T08 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Main with uncommitted project work is refused
- **GIVEN** A finished lane, and main has src/a.js edited and not committed (a developer's own work)
- **WHEN** The director runs the dry run
- **THEN** Non-zero exit; one plain line names src/a.js and tells the director to ask the developer to commit or stash it (it is not touched); main's file content is byte for byte what it was
<!-- proven by test CP1-T09 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Main with only edits to Ratchet's own shared files is carried, not refused
- **GIVEN** A finished lane, and main has only .ratchet/learnings.md edited and not committed
- **WHEN** The director runs the dry run
- **THEN** Exit 0; the dry run lists .ratchet/learnings.md as carried across, never committed; main's HEAD and the file's bytes are unchanged and the file is still uncommitted
<!-- proven by test CP1-T10 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Main with a merge in progress is refused
- **GIVEN** A finished lane, and main is in the middle of a merge (MERGE_HEAD exists)
- **WHEN** The director runs the dry run
- **THEN** Non-zero exit; one plain line says main has a merge in progress and to finish or abort it before integrating; nothing changes
<!-- proven by test CP1-T11 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Main whose own run is mid-step is refused
- **GIVEN** A finished lane; main's own active run is in phase building (and in a second variant, blocked)
- **WHEN** The director runs the dry run for each variant
- **THEN** Each exits non-zero with one plain line naming main's run and its phase and telling the director to wait for the step to finish (or finish or pause it) and then integrate; main's run state is unchanged. With main's run in phase done, or no run at all, the same command succeeds
<!-- proven by test CP1-T12 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: A lane with nothing new is reported, not merged
- **GIVEN** Lane-a's run is done and its branch has zero commits ahead of main
- **WHEN** The director runs the dry run
- **THEN** The output says there is nothing to bring to main (no commit list pretended); nothing changes
<!-- proven by test CP1-T13 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Integrate only runs from the director's session
- **GIVEN** A finished lane and a session with RATCHET_ROLE unset (a builder or a plain terminal)
- **WHEN** The same `integrate <lane> --dry-run` command is run
- **THEN** Non-zero exit; the message says integrate must be run from the director's session; nothing changes and no plan is printed
<!-- proven by test CP1-T14 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: The guard blocks a builder from running integrate
- **GIVEN** A project with Ratchet's guard hook, called as a builder tool call
- **WHEN** A Bash tool call runs `python3 ratchet.py integrate ../lane-a --dry-run`, and again `python3 ratchet.py --root /x integrate lane-a --dry-run`
- **THEN** Both are blocked by the guard (exit code 2, message that director commands are reserved for the director session); a Bash call that only mentions integrate in prose (a commit message 'integrate the lane work', a pause --reason) is still allowed
<!-- proven by test CP1-T15 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Nothing changes in main, the lane, either run or the remote
- **GIVEN** A finished lane, a bare remote with recorded refs, and a before-snapshot of main HEAD and branches, status, the lane HEAD and status, both checkouts' .ratchet files (state.json, active.json, config.json, evidence folders, human log), the worktree list, and the remote refs
- **WHEN** The director runs the dry run (success) and then every refusal case above
- **THEN** After each command the snapshot is identical: same commits and branches, no new files, no new scratch worktree left behind, no changed run state in either run, and the remote's refs untouched
<!-- proven by test CP1-T16 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: No git command that writes or pushes is run
- **GIVEN** A git shim first on PATH that logs every git call before running the real git
- **WHEN** The director runs the dry run on a finished lane
- **THEN** The log contains only read-only subcommands (for example status, rev-parse, rev-list, log, diff, show, worktree list, branch --list, config --get, ls-files); none of add, commit, merge, checkout, switch, reset, update-ref, branch with a name, worktree add or remove, fetch, pull, push, stash, apply, clean or gc appears
<!-- proven by test CP1-T17 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: The docs describe the integrate command
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** They are searched for the integrate command
- **THEN** Each names `integrate` and `--dry-run` as a director-only command, in the same places that list director-sign and director-enroll
<!-- proven by test CP1-T18 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: A shared file changed only on main is listed as differing
- **GIVEN** A finished lane that changed none of Ratchet's four shared files, and main committed a change to .ratchet/standards.md after the lane branched
- **WHEN** The director runs the dry run
- **THEN** Exit 0; the output names standards.md as differing (changed on main's side since the merge base) and does not name learnings.md, config.json or .gitignore; main's HEAD and the file are unchanged
<!-- proven by test CP1-T19 in tests/test_integrate_check.py, commit fd2a677 -->

#### Scenario: Main's uncommitted run config is carried, not refused or committed
- **GIVEN** A finished lane, and main has .ratchet/config.json edited and not committed (for example max_behavior_attempts changed), with no other uncommitted file
- **WHEN** The director runs the dry run
- **THEN** Exit 0; the dry run lists .ratchet/config.json as carried across, never committed; main's HEAD and commit count are unchanged and config.json still holds the uncommitted value byte for byte. With a tracked project file also edited on main, the same command is refused (as in T09) and the message does not name config.json as the reason
<!-- proven by test CP1-T20 in tests/test_integrate_check.py, commit fd2a677 -->

### Requirement: Merge a finished lane, roll back on failure
The system SHALL do this: A director can merge a finished lane into local main with one command; the suite runs on the merged code first, and if anything fails main is left exactly as it was and the attempt is recorded.

- Integrating a finished lane with no conflicts leaves main with one merge commit naming the lane and its run, and the suite passed on exactly that commit before main moved; integrating a lane already in main changes nothing, exits 0 and says so
- When the suite fails or anything else rolls back, main's branch and files are exactly as before, its uncommitted Ratchet files byte for byte and still uncommitted, and the message names the failing output and the next step
- A conflict in any file stops integrate with main unchanged and lists the conflicting files (Ratchet's own files are handled in the next step)
- If main moved while the suite ran, integrate stops without changing main and says to run it again; with no suite command configured, integrate refuses with a plain message to set one
- Every attempt is appended to an integration record with time, lane, run, commits, suite, result (merged, rolled-back, refused, or no-op for a lane already in main) and a plain detail; a second integrate while one runs is refused as busy; nothing is pushed

#### Scenario: A finished lane merges into main as one merge commit naming the lane and its run
- **GIVEN** Main is one commit ahead of lane-x's base; lane-x (run 'lane-x-run', phase done) adds src/lane.js; the suite passes
- **WHEN** the director runs integrate on lane-x
- **THEN** the command succeeds; main's branch is still 'main' and its tip is a merge commit whose first parent is main's old tip and whose second parent is lane-x's tip; the commit message names 'lane-x' and 'lane-x-run'; main's files include both src/lane.js and src/main_only.js with the lane's exact contents; the lane's branch and worktree are untouched
<!-- proven by test CP2-T01 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: The suite passed on exactly the commit that lands on main, in a copy, not in main's folder
- **GIVEN** the same clean setup; the suite script logs its working folder and the commit checked out there
- **WHEN** the director integrates lane-x
- **THEN** the suite ran exactly once; its working folder is not main's checkout folder; the commit it saw is the same hash as main's new tip; it saw both src/lane.js and src/main_only.js; and after the command no extra worktree remains (git worktree list shows only main and lane-x)
<!-- proven by test CP2-T02 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: Main does not move until the suite has finished and passed
- **GIVEN** the suite script logs main's tip at the moment it runs and then pauses until released
- **WHEN** integrate is running and the suite is paused
- **THEN** main's tip, branch files and working tree are still exactly the old ones (checked while the suite is paused); only after release and a pass does main's tip change to the merge commit
<!-- proven by test CP2-T03 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A failing suite leaves main exactly as it was, and the message says what failed and what to do
- **GIVEN** the suite is set to fail printing SUITE-BOOM-LINE; main is clean
- **WHEN** the director integrates lane-x
- **THEN** the command exits non-zero; main's tip hash, branch name, `git status --porcelain` and every tracked file's bytes are identical to before; no branch, tag or stash was added; the output contains SUITE-BOOM-LINE and a plain next step (fix the lane and finish it again, then integrate again); no scratch worktree is left in git worktree list
<!-- proven by test CP2-T04 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: Main's uncommitted changes to Ratchet's own files are left uncommitted and untouched by a rolled-back attempt
- **GIVEN** main has an uncommitted extra line in .ratchet/learnings.md (the lane does not touch that file) and the suite is set to fail
- **WHEN** the director integrates lane-x
- **THEN** integrate is rolled back; .ratchet/learnings.md in main has exactly the bytes it had before, including the developer's extra line, and is still shown as modified and uncommitted by git status; main's tip is the old tip, so no commit of any kind was made (nothing reset, checked out or cleaned in main's folder)
<!-- proven by test CP2-T05 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: Main's uncommitted changes to Ratchet's own files are carried through a successful merge, still uncommitted
- **GIVEN** main has an uncommitted extra line in .ratchet/learnings.md; the lane does not touch that file; the suite passes
- **WHEN** the director integrates lane-x
- **THEN** main gained exactly one new commit, the merge commit (its first parent is the old tip and no other commit sits between them, so nothing was committed on the developer's behalf); .ratchet/learnings.md in main's folder has exactly the bytes it had before, including the developer's extra line, and is still shown as modified and uncommitted by git status
<!-- proven by test CP2-T06 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A conflict in project files stops the merge, changes nothing and lists the files
- **GIVEN** lane-x and main changed src/a.js and src/b.js differently; the lane also adds src/lane.js with no conflict
- **WHEN** the director integrates lane-x
- **THEN** the command exits non-zero; main is identical to before (tip, status, files); the output lists src/a.js and src/b.js as conflicting and does not list src/lane.js; it tells the agent to merge main into the lane (the merged command) and integrate again; the suite never ran; no scratch worktree and no merge state (MERGE_HEAD) is left in main
<!-- proven by test CP2-T07 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: If main moved while the suite ran, nothing lands and the director is told to run it again
- **GIVEN** while the suite runs, it commits a new file src/other.js to main's checkout
- **WHEN** the director integrates lane-x
- **THEN** the command exits non-zero and says to run integrate again; main's tip is the commit made during the suite (not the old tip, not a merge commit); src/lane.js is not in main; main's files and status are exactly what that new commit left; the scratch worktree is gone; running integrate again (suite no longer touching main) merges cleanly on top of the new tip
<!-- proven by test CP2-T08 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: An uncommitted edit made in main while the suite ran is never overwritten
- **GIVEN** while the suite runs, it edits src/a.js in main's checkout without committing, and the lane also changed src/a.js (in a way that merges cleanly with main's committed version)
- **WHEN** the director integrates lane-x
- **THEN** main's tip is unchanged (no fast-forward over the changed file); src/a.js in main holds exactly the edit made during the suite, byte for byte; the command exits non-zero with a plain message to commit or set aside that change and integrate again; the scratch worktree is gone
<!-- proven by test CP2-T09 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A suite that hangs past the time limit counts as a failure and rolls back
- **GIVEN** the config's test_timeout_sec is a few seconds and the suite is set to hang longer than that
- **WHEN** the director integrates lane-x
- **THEN** the command exits non-zero within a bounded time, saying the suite timed out; main is identical to before; the suite process is not left running; no scratch worktree remains
<!-- proven by test CP2-T10 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: With no suite command configured, integrate refuses rather than merging untested code
- **GIVEN** main's config has no regression_command
- **WHEN** the director integrates lane-x
- **THEN** the command exits non-zero with a plain message saying to set the suite command first (regression_command); main is unchanged; the suite never ran; a record line is appended with result 'refused' and suite 'not-run'
<!-- proven by test CP2-T11 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A lane that is not finished is refused before any merge work, and the refusal is recorded
- **GIVEN** lane-x's run is in phase building
- **WHEN** the director integrates lane-x
- **THEN** the command exits non-zero with one plain line on what to do next; main is unchanged; the suite never ran; no scratch worktree was created; a record line exists with result 'refused' for lane-x
<!-- proven by test CP2-T12 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: Every attempt, merged, rolled back or refused, is one record line with the facts
- **GIVEN** in one project, four attempts in order: a refused one (unfinished lane), a suite failure, a conflict, then a clean merge
- **WHEN** the attempts have run and .ratchet/integrations.jsonl in main is read
- **THEN** the file has exactly four lines, each valid JSON with exactly the fields time (parseable timestamp), lane (lane-x), run (the run slug), commits (the lane's commits that would come to main), suite ('passed', 'failed' or 'not-run'), result ('refused', 'rolled-back', 'rolled-back', 'merged' in that order; the allowed values are merged, rolled-back, refused, no-op) and detail (plain text of what was done); the merged line's commits include the merge commit hash that is main's tip
<!-- proven by test CP2-T13 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: The record is append-only and stays out of git
- **GIVEN** an earlier record line exists in .ratchet/integrations.jsonl
- **WHEN** a later attempt runs (any result)
- **THEN** the earlier line is byte-identical and the new line follows it; `git status --porcelain` in main shows no integrations.jsonl (it is ignored run state), so the record never makes main look dirty or enters a merge commit
<!-- proven by test CP2-T14 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A second integrate while one is running is refused as busy and the first still finishes
- **GIVEN** a first integrate is running with its suite paused at a started marker
- **WHEN** the director runs integrate on the same lane (and on a second finished lane) from another process
- **THEN** each second call exits non-zero quickly with a plain 'busy, an integrate is already running, try again when it finishes' message and changes nothing; after the first is released it succeeds with exactly one merge commit on main; an integrate run after that works normally (the lock was released)
<!-- proven by test CP2-T15 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A killed integrate leaves nothing that blocks the next one
- **GIVEN** an integrate is killed hard (SIGKILL) while its suite is paused, leaving its lock and scratch worktree behind; main is untouched
- **WHEN** the director runs integrate on lane-x again
- **THEN** it does not report busy, removes the leftover scratch worktree (git worktree list shows no stale entry, no leftover folder), and merges cleanly; main's history has exactly one merge commit for the lane
<!-- proven by test CP2-T16 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: Nothing is ever pushed, on success or failure
- **GIVEN** main has a bare origin whose pre-receive hook leaves a marker if it runs; origin/main was pushed once
- **WHEN** the director integrates a clean lane, then a failing-suite lane, then a conflicting lane
- **THEN** the origin repository's branches and tags are identical to before all three attempts, its hook never ran, main's remote-tracking ref origin/main is unchanged, and local main is ahead of it only after the clean merge
<!-- proven by test CP2-T17 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: The suite in the copy can use the project's installed dependencies without touching them
- **GIVEN** main has an ignored node_modules folder holding probe.txt; the suite script reads node_modules/probe.txt and tries to write node_modules/written.txt
- **WHEN** the director integrates lane-x
- **THEN** the suite saw probe.txt; after the run main's real node_modules still holds probe.txt, and no scratch folder or dangling link remains; the merge result does not contain node_modules
<!-- proven by test CP2-T18 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A builder session cannot run the real merge
- **GIVEN** the same finished lane and passing suite, in a session without the director role
- **WHEN** integrate is run on lane-x (no --dry-run)
- **THEN** the command is refused as a director-only action; main's tip and files are unchanged; the suite never ran; nothing is added to the record as a merge
<!-- proven by test CP2-T19 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: A lane that already is in main is a no-op, not a second merge commit
- **GIVEN** lane-x's tip is an ancestor of main's tip (it was integrated successfully once)
- **WHEN** the director integrates lane-x again
- **THEN** the command exits 0 with a plain 'already merged' message; main's tip is unchanged, no new merge commit or empty commit is made, the suite does not run, and a record line is appended with result 'no-op'
<!-- proven by test CP2-T20 in tests/test_integrate_merge.py, commit 0813a14 -->

#### Scenario: Guard for CP1: the dry run still changes nothing after the real merge exists
- **GIVEN** a finished lane and the same setup as CP2-T01
- **WHEN** the director runs integrate on lane-x with --dry-run
- **THEN** main's tip, status, worktree list and the record file are unchanged and the suite never ran, so adding the real merge did not make the check-only mode write
<!-- proven by test CP2-T21 in tests/test_integrate_merge.py, commit 0813a14 -->

### Requirement: Ratchet's own files merge by Ratchet's rules
The system SHALL do this: When a lane and main both changed Ratchet's shared files, integrate merges them itself by fixed rules, so neither the director nor the developer ever has to resolve a run config conflict by hand.

- A lane whose run config conflicts with main's integrates without anyone typing a git command: a setting changed on one side only takes that side, a setting changed on both keeps main's, and the lane's own reviewer, provider and run settings always keep main's; a guardrail check the lane added is kept even when main added checks of its own; the output and record list every lane setting not taken
- Learnings and the Ratchet .gitignore keep every line from both sides; a standards change from one side is taken, and a standards change on both sides stops integrate with main unchanged
- Uncommitted changes to Ratchet's own files on main are carried across the merge and re-applied by these rules, still uncommitted; where the developer's uncommitted value and the merge both changed a setting, the uncommitted value wins and is listed
- With main carrying an uncommitted review_depth change and a lane that changed providers, after integrate main's uncommitted review_depth is intact, the lane's providers are handled by the lane-local rule (main's kept, listed), and the only new commit on main is the merge
- Integrate handles exactly the four files lane F's merge guard treats as Ratchet's shared files, and a lane that later takes main through F's `merged` accepts the result with no approval question

#### Scenario: A lane whose run config conflicts with main's integrates with no git command typed, merged key by key
- **GIVEN** Main changed config.json (test_timeout_sec 60 to 600, max_ui_rounds 3 to 5, ui_reviewers now gemini first); the lane changed the same file (test_timeout_sec 60 to 900, review_depth.normal has a different split); the lane's run is done and the suite passes
- **WHEN** The director runs integrate on the lane
- **THEN** The command exits 0 and main has a merge commit naming the lane; main's config.json has test_timeout_sec 600 (both sides changed, main's kept), max_ui_rounds 5 (main-only change kept), main's ui_reviewers and review_depth (lane-local keys keep main's); the lane's src/feature.txt change is on main; the printed output and the newest integrations.jsonl entry both list test_timeout_sec and review_depth as lane settings not taken; no conflict markers anywhere in main
<!-- proven by test CP3-T01 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A lane-local reviewer or provider setting changed only on the lane keeps main's value and is listed
- **GIVEN** Main did not touch config.json since the lane branched; the lane changed reviewers, providers (including a paid_ok_at consent stamp), review_depth.low, probe_providers and quota_retest_minutes to different values; git alone would merge that cleanly
- **WHEN** The director runs integrate on the lane
- **THEN** Main's reviewers, providers (no paid_ok_at added), review_depth, probe_providers and quota_retest_minutes are exactly as before; the output and the integrations.jsonl entry list each of them as not taken
<!-- proven by test CP3-T02 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A project-wide setting changed only in the lane is taken
- **GIVEN** Main did not touch config.json since the lane branched; the lane changed regression_command's timeout (test_timeout_sec 60 to 900) and set commit_per_checkpoint to false, and added an unknown key lane_note
- **WHEN** The director runs integrate on the lane
- **THEN** Main's config.json now has test_timeout_sec 900, commit_per_checkpoint false and lane_note (an unknown key counts as project-wide), every other key as before; none of these three is listed as not taken
<!-- proven by test CP3-T18 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A guardrail check the lane added survives even when main added checks of its own
- **GIVEN** The merge base has one guardrail check (command 'python3 check_a.py'); main added 'python3 check_main.py' and the lane added 'python3 check_lane.py' to the config's checks list
- **WHEN** The director runs integrate on the lane
- **THEN** Main's config.json checks holds all three commands, each exactly once, main's entries first and the lane's added entry kept; the suite still runs and the merge lands
<!-- proven by test CP3-T19 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Guardrail checks are matched by command, so none is duplicated or lost
- **GIVEN** Both sides kept the base check 'python3 check_a.py'; main changed its text description, the lane changed the same check's description differently and also removed nothing; the lane added 'python3 check_lane.py'; main's list was reordered
- **WHEN** The director runs integrate on the lane
- **THEN** The result has one entry for check_a (main's version), one for check_lane, no entry dropped and none duplicated, and the lane's differing description for check_a is listed as not taken
<!-- proven by test CP3-T20 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Every named lane-local key keeps main's value when only the lane changed it
- **GIVEN** For each of review_depth, reviewers, providers, host_provider, host_models, role_models, ui_reviewers, approver, human_review, max_adversarial_rounds, max_adversarial_rounds_run, unavailable_ttl_hours, probe_providers, probe_ttl_minutes, probe_timeout_sec, quota_retest_minutes and provider_share (the exact lane-local list in CP3's notes), the lane (alone) sets a value different from main's
- **WHEN** The director runs integrate on the lane (one lane per key, or all keys at once)
- **THEN** Every one of those keys in main's config.json keeps main's value and each is named in the output and the record as not taken
<!-- proven by test CP3-T21 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: When the lane's config has no differing settings, nothing is reported as left out
- **GIVEN** The lane's config.json differs from main's only in whitespace and key order (same settings), and also a lane where main and lane made the identical change
- **WHEN** The director runs integrate on each
- **THEN** Both integrate cleanly; main's config.json settings are unchanged; the record's list of settings not taken is empty and the output does not claim any setting was dropped
<!-- proven by test CP3-T03 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Learnings from main and from the lane are both kept, in order, without duplicates
- **GIVEN** Main appended 'main lesson 1' and 'shared lesson' to learnings.md; the lane appended 'lane lesson 1' and the same 'shared lesson' (both appended at the end, so git alone conflicts)
- **WHEN** The director runs integrate on the lane
- **THEN** Main's learnings.md contains the original 3 lines once each, then 'main lesson 1', 'shared lesson' (once), 'lane lesson 1', no conflict markers, no line lost, and git status is clean afterwards
<!-- proven by test CP3-T04 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: The Ratchet .gitignore keeps every line from both sides, including when both created it
- **GIVEN** Case A: main added '*.log' and the lane added '.ratchet/viewer-data/'. Case B: main has no .ratchet/.gitignore, the lane created one containing the init lines, and main (uncommitted-then-committed in its own commit) created a different one with '*.tmp'
- **WHEN** The director runs integrate on the lane in each case
- **THEN** In both cases main's .ratchet/.gitignore contains every line from both versions exactly once and nothing is lost; the merge commit lands
<!-- proven by test CP3-T05 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A standards change made only in the lane is taken
- **GIVEN** The lane added rule 6 to standards.md; main did not touch standards.md since the lane branched
- **WHEN** The director runs integrate on the lane
- **THEN** Main's standards.md equals the lane's version (with rule 6) and the merge lands
<!-- proven by test CP3-T06 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A standards change made only on main is kept while the lane's other work still lands
- **GIVEN** Main reworded rule 2 of standards.md after the lane branched; the lane did not touch standards.md but changed src/feature.txt and learnings.md
- **WHEN** The director runs integrate on the lane
- **THEN** Main's standards.md is still main's reworded version, the lane's src/feature.txt and learnings line are on main
<!-- proven by test CP3-T07 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A standards change on both sides stops integrate with main untouched and a plain next step
- **GIVEN** The lane changed rule 3 of standards.md one way and main changed it another way; learnings.md also differs on both sides
- **WHEN** The director runs integrate on the lane
- **THEN** The command exits non-zero with one plain message that names .ratchet/standards.md, says both sides changed it, and says what to do next (decide which wording to keep, change one side, then integrate again); main's HEAD, branch, every tracked file and git status are exactly as before (including the learnings.md main had); no merge commit and no scratch worktree is left (git worktree list shows only main and the lane); the newest integrations.jsonl entry says the attempt was stopped, names the lane and standards.md
<!-- proven by test CP3-T08 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Main's uncommitted Ratchet files are carried across the merge and stay uncommitted
- **GIVEN** Main's working tree has an uncommitted new line in learnings.md and an uncommitted project-wide change in config.json (test_timeout_sec 60 to 600), and nothing else uncommitted; the lane added a different learnings line and changed src/feature.txt
- **WHEN** The director runs integrate on the lane
- **THEN** The only new commit on main is the merge commit (git log shows the old HEAD then the merge, nothing else, no commit naming Ratchet's files); config.json still has test_timeout_sec 600 and learnings.md holds main's uncommitted line plus the lane's line; git status shows exactly .ratchet/config.json and .ratchet/learnings.md as modified and unstaged, nothing staged; the lane's src/feature.txt is on main
<!-- proven by test CP3-T09 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Uncommitted project files on main are still refused, and the uncommitted Ratchet files are left alone
- **GIVEN** Main has an uncommitted edit to learnings.md and also an uncommitted edit to src/notes.txt
- **WHEN** The director runs integrate on the lane
- **THEN** The command is refused with a plain message naming src/notes.txt and saying to commit or stash it; no new commit exists on main, learnings.md and src/notes.txt keep their uncommitted bytes, HEAD is unchanged, and no saved copy or scratch worktree is left behind
<!-- proven by test CP3-T10 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: If the suite fails, main's uncommitted Ratchet files are exactly as they were
- **GIVEN** Main has an uncommitted learnings line and an uncommitted config.json change; the lane's code makes the suite fail
- **WHEN** The director runs integrate on the lane
- **THEN** The merge does not land (the lane's src/feature.txt is not on main, no merge commit, HEAD unchanged); the bytes of main's learnings.md and config.json are identical to before the command and git status still lists them as modified and unstaged; the message names the failing suite output; the record shows a rolled-back attempt
<!-- proven by test CP3-T11 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Main's uncommitted reviewer split stays intact while the lane's provider change is handled by the lane-local rule
- **GIVEN** Main has an uncommitted review_depth change in config.json (a different normal split); the lane changed providers (including a paid_ok_at stamp) and src/feature.txt; the suite passes
- **WHEN** The director runs integrate on the lane
- **THEN** Main's uncommitted review_depth bytes are intact and still uncommitted; providers keep main's value (no paid_ok_at added) and are listed as not taken in the output and the record; the only new commit on main is the merge commit
<!-- proven by test CP3-T22 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: When the uncommitted value and the lane both changed the same setting, the uncommitted value wins and is listed
- **GIVEN** Main has an uncommitted change test_timeout_sec 60 to 600 and an uncommitted change max_ui_rounds 3 to 5; the lane changed test_timeout_sec to 900 and left max_ui_rounds alone and changed commit_per_checkpoint to false
- **WHEN** The director runs integrate on the lane
- **THEN** Main's config.json has test_timeout_sec 600 and max_ui_rounds 5 (still uncommitted) and commit_per_checkpoint false (taken from the lane); the output and record list test_timeout_sec as not taken; the only new commit is the merge
<!-- proven by test CP3-T23 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A re-apply that cannot be done restores main's saved bytes and rolls back
- **GIVEN** Main has an uncommitted standards.md rewording of rule 3; the lane changed rule 3 differently; so the merged standards.md and the uncommitted copy clash when re-applied (also run once with the re-apply forced to fail after main moved, for example by making the Ratchet file read-only)
- **WHEN** The director runs integrate on the lane
- **THEN** The command exits non-zero with a plain message naming standards.md; main's HEAD is back at (or never left) its old commit, the lane's changes are not on main, the bytes of main's uncommitted standards.md are exactly what they were, git status is as before, no saved copy needing restore is left, and the newest integrations.jsonl entry says rolled-back
<!-- proven by test CP3-T24 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A project-file conflict still stops integrate and lists only the project file, even when Ratchet files also differ
- **GIVEN** Main and the lane both edited src/shared.txt on the same line; learnings.md, .gitignore and config.json also differ on both sides
- **WHEN** The director runs integrate on the lane
- **THEN** The command stops; the message lists src/shared.txt as the conflict and does not list the Ratchet files as conflicts; it points at merging main into the lane (F's path) and integrating again; main's HEAD, files and git status are exactly as before
<!-- proven by test CP3-T12 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Main's own run does not ask for approve-changes because integrate merged the Ratchet files by rule
- **GIVEN** Main has its own run at a step boundary with an approved plan; the lane changed standards.md (taken by rule) and learnings.md
- **WHEN** Integrate finishes, then the director reads main's status and next
- **THEN** Neither status nor next on main mentions approve-changes or an unapproved change to rules, and the main run's integrity check reports no problem; main's own gates are unchanged
<!-- proven by test CP3-T13 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Integrate treats exactly the files lane F's merge guard treats as Ratchet's shared files
- **GIVEN** The engine's two lists: F's MERGE_SHARED (ratchet.py, the set the merge guard lets an agent resolve between steps) and the shared-file list integrate uses; a lane and main that both change each of those four paths, plus a lane and main that both change a fifth file under .ratchet/ (for example .ratchet/notes.txt)
- **WHEN** The two lists are compared (by loading the engine module and reading the two names), and integrate is run on the lane
- **THEN** The two lists hold exactly the same paths (.ratchet/config.json, standards.md, learnings.md, .gitignore); the four listed files are merged by Ratchet's rules without stopping; the fifth file is treated as a project file, so integrate stops on it with main unchanged and lists it as a conflict
<!-- proven by test CP3-T14 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: A lane that takes an integrated main between steps gets no approval question for Ratchet's shared files
- **GIVEN** A lane integrated into main with config.json, learnings.md, .gitignore and standards.md all changed on both sides (so main now holds rule-merged versions); a second lane in the middle of its run at a step boundary with an approved plan, branched before that merge
- **WHEN** The second lane merges that main with plain git between steps and runs `ratchet.py merged`
- **THEN** The merge guard lets the agent do the merge; `merged` succeeds and takes main's committed standards.md and config.json as the new approved baseline; the lane's next, status and integrity check mention no approve-changes and no approval question for any of the four shared files; the lane's own gates are unchanged
<!-- proven by test CP3-T15 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: Nothing is pushed while Ratchet's files are merged
- **GIVEN** Main has a bare remote origin; the lane conflicts with main in config.json and learnings.md
- **WHEN** The director runs integrate on the lane (merged outcome), and again on a lane that stops on standards
- **THEN** The remote's refs and objects are identical to before both runs
<!-- proven by test CP3-T16 in tests/test_integrate_files.py, commit 2d0b330 -->

#### Scenario: The docs state the rules for each Ratchet shared file
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** Their text is read
- **THEN** Each describes integrate's handling of config.json (key by key: one-side change taken, both changed keeps main's, lane-local reviewer and provider keys keep main's, guardrail checks from both sides, lane settings not taken are listed), learnings.md and .gitignore (lines from both sides), standards.md (the changed side, stop if both), and that main's uncommitted Ratchet files are carried across the merge and stay uncommitted (the uncommitted value wins a clash and is listed)
<!-- proven by test CP3-T17 in tests/test_integrate_files.py, commit 2d0b330 -->

### Requirement: Director signs raise-limit and lettered choices
The system SHALL do this: A director can sign a one-round raise of a step's review limit (with its reason) and a lettered choice, so an enrolled lane stuck at either no longer needs the developer.

- In an enrolled lane blocked at a step's review limit, `director-sign raise-limit CPn --reason "..."` gives a token that raises that step's limit by exactly one round, once, with the reason saved to learnings as a chat raise is today
- An enrolled lane can put a lettered question to the director (`ask choice` runs there, as `ask accept-finding` does), and `director-sign choice <L>` gives a token that `choice <L> --director-token T` uses to record that letter exactly as a developer's pick would, including what the option unlocks
- `raise-limit CPn --director-token T` with no `--reason` is refused and the token stays usable; in an enrolled lane a chat `raise:` reply or a typed letter approves nothing
- A token for another lane, another step, another letter or an older pause is refused, a builder session cannot sign, and the run-wide review budget is never reset
- README, the orchestrator skill's director section and schemas.md section 9 list both actions

#### Scenario: A director raises a stuck step's review limit by one round with a signed token
- **GIVEN** Lane f is director-enrolled; CP1 (max_adversarial_rounds=2) used both blocking rounds and the run is blocked on CP1's own round limit; the lane ran `ask raise-limit CP1`
- **WHEN** The director runs `director-sign raise-limit CP1 --reason "edge case worth one more look"` and the lane runs `raise-limit CP1 --director-token <t> --reason "edge case worth one more look"`
- **THEN** The command succeeds with no chat reply recorded; CP1's gates.adversarial.cap_raised_to is 3 (configured 2 plus exactly one); the run phase is no longer blocked; CP1's attempts count is still 2 (not reset); the message says the limit was raised and names the reason
<!-- proven by test CP4-T01 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: The raise reason is saved exactly as a chat raise saves it
- **GIVEN** The same blocked, enrolled lane as T01 and a second, non-enrolled lane blocked the same way where a human replies `raise: edge case worth one more look`
- **WHEN** Each lane's raise-limit completes (token with --reason in the first, chat reply in the second)
- **THEN** Both learnings.md files gain one line of the form "CP1's review-round limit was raised to 3 rounds: edge case worth one more look" (identical text), and the enrolled lane's log has a round_limit_raised event with cp CP1, cap 3 and the reason
<!-- proven by test CP4-T02 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A signed raise grants exactly one more review round, not a fresh budget
- **GIVEN** The enrolled lane from T01 after the director-signed raise
- **WHEN** The lane opens a review round (triaging open findings first), the reviewer again records a blocking finding, and the lane asks for another round
- **THEN** Exactly one extra round opens (round 3); after it records blocking the run is blocked again at 3 rounds; a fourth `reviewers CP1` is refused
<!-- proven by test CP4-T03 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A step's raise can be used only once, signed or not
- **GIVEN** The enrolled lane after one successful director-signed raise on CP1, blocked again at 3 rounds
- **WHEN** The lane runs `ask raise-limit CP1` again, then replays the first (already used) token, then asks the director for a new `director-sign raise-limit CP1 --reason ...` and applies that
- **THEN** `ask raise-limit CP1` is refused as already raised; the replayed token and the new token both fail; cap_raised_to stays 3, the run stays blocked, and learnings.md has the reason once
<!-- proven by test CP4-T04 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A raise token without --reason is refused and not spent
- **GIVEN** A blocked, enrolled lane with a valid raise-limit token for CP1
- **WHEN** The lane runs `raise-limit CP1 --director-token <t>` with no --reason, then the same command with --reason "needs one more pass"
- **THEN** The first run fails naming --reason as missing and changes nothing (cap_raised_to unset, still blocked, pending question still waiting, learnings.md unchanged); the second run with the SAME token succeeds
<!-- proven by test CP4-T05 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A raise token for another step cannot raise this one
- **GIVEN** A two-step plan, enrolled lane blocked on CP1's round limit; CP2 has not started
- **WHEN** The director tries `director-sign raise-limit CP2 --reason ...` and, if a token is printed, the lane applies it to `raise-limit CP1 --director-token <t> --reason ...`
- **THEN** Either signing for the unblocked CP2 is refused, or its token is refused on CP1; in both cases CP1's cap_raised_to is unset, CP2's too, and the run is still blocked
<!-- proven by test CP4-T06 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A raise token from an older pause is refused
- **GIVEN** A blocked, enrolled lane; the director signed a raise-limit token, then the lane ran `ask raise-limit CP1` again (moving the pause)
- **WHEN** The lane applies the old token
- **THEN** It is refused as stale (no raise, still blocked); a freshly signed token for the new pause works
<!-- proven by test CP4-T07 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A raise token from another lane is refused
- **GIVEN** Two enrolled lanes, A and B, both blocked on CP1's round limit with their own pending raise question
- **WHEN** The director signs a raise-limit token in lane A and the agent applies it in lane B
- **THEN** Lane B refuses it and stays blocked with cap_raised_to unset; lane A is untouched until its own token is applied
<!-- proven by test CP4-T08 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A builder session cannot sign or forge a raise, and a chat reply raises nothing in an enrolled lane
- **GIVEN** A blocked, enrolled lane with the raise question pending
- **WHEN** (a) `director-sign raise-limit CP1 --reason x` runs from a session that is not the director's; (b) the lane applies a made-up token and a token signed with a different director key; (c) a human chat reply `raise: x` is recorded and the lane runs `raise-limit CP1` with no token
- **THEN** (a) is refused and prints no token; (b) and (c) are refused (the refusal for (c) names the director token); CP1's cap_raised_to stays unset, the run stays blocked and learnings.md is unchanged
<!-- proven by test CP4-T09 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A token for a different action cannot raise a limit, and a raise token cannot do anything else
- **GIVEN** A blocked, enrolled lane with the raise question pending
- **WHEN** The lane applies a signed human-approve token to `raise-limit CP1`; then applies a signed raise-limit token to `human-approve`, `approve-changes` and `accept-finding CP1 --reason x`
- **THEN** Every attempt is refused; cap_raised_to stays unset and the run stays blocked (a raise token never unblocks it as a plain human-approve would)
<!-- proven by test CP4-T10 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A signed raise at the run-wide cap raises the step but never resets the run budget
- **GIVEN** An enrolled lane with max_adversarial_rounds_run=1, blocked on the RUN-WIDE round cap after CP1's first blocking round
- **WHEN** The director signs raise-limit CP1 --reason ... and the lane applies it
- **THEN** CP1's cap_raised_to is set, the output does not say the run is unblocked, the phase is still blocked, and adv_rounds_total is exactly what it was before (not reset to 0)
<!-- proven by test CP4-T11 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A raise is refused when the step is not blocked on its round limit
- **GIVEN** An enrolled lane still building CP1 with no review round used
- **WHEN** The director runs `director-sign raise-limit CP1 --reason ...` and the lane also tries `ask raise-limit CP1`
- **THEN** Each is refused with a plain message that raise-limit applies only when the step is blocked at its review limit; no token is printed (or if one is, applying it is refused); cap_raised_to stays unset
<!-- proven by test CP4-T12 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: Without an enrolled director nothing about the chat raise changes
- **GIVEN** A non-enrolled lane (approver human) blocked on CP1's round limit, and a director key on the machine
- **WHEN** (a) The lane runs `ask raise-limit CP1`, the human replies `raise: need one more look`, the lane runs `raise-limit CP1`; (b) in a second non-enrolled blocked lane the agent applies a director-signed raise token
- **THEN** (a) works exactly as before (cap_raised_to 3, reason in learnings, run unblocked, no token needed); (b) is refused because the lane only accepts human approvals
<!-- proven by test CP4-T13 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A director picks a lettered option that unlocks a file exactly as a human's pick would
- **GIVEN** An enrolled lane building CP1 with reference/prototype.html locked; the lane ran `ask choice --options A,B,C --locked A=reference/prototype.html` (the ask is allowed in an enrolled lane and prints the options). A non-enrolled twin lane with the same question gets a chat reply `A`
- **WHEN** The director runs `director-sign choice A` and the lane runs `choice A --director-token <t>`
- **THEN** Succeeds with no chat reply; the message says option A was picked, that it approves changing reference/prototype.html and to run approve-changes; the pending question is gone; a choice_made event names letter A and the file; state.choice_allow has the same letter, files, cp, unblock and guardrail values as the twin lane's
<!-- proven by test CP4-T14 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A signed pick of an option that unlocks nothing records the letter and unlocks nothing
- **GIVEN** The same enrolled lane and question as T14
- **WHEN** The director signs choice B and the lane applies it
- **THEN** The pick is recorded (choice_made letter B, pending cleared) with no choice_allow in state; changing reference/prototype.html still fails the test-lock check and approve-changes still refuses it
<!-- proven by test CP4-T15 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A signed pick at a review-limit block resumes the run but gives no extra review round
- **GIVEN** An enrolled lane blocked on CP1's round limit (max 2) after its one raise was spent, with `ask choice --options A,B,C` pending
- **WHEN** The director signs choice B, the lane applies it, then the director signs human-approve and the lane applies that
- **THEN** The choice records B with choice_allow.unblock true and the run is still blocked until human-approve; after human-approve the run is resumed, CP1's attempts count is unchanged, cap_raised_to is unchanged, and `reviewers CP1` refuses to open another round (same as the human path in test_limits CP1-X02)
<!-- proven by test CP4-T16 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A choice token for another letter is refused
- **GIVEN** An enrolled lane with `ask choice --options A,B,C --locked A=reference/prototype.html` pending
- **WHEN** The director signs choice A and the lane applies that token as `choice B --director-token <t>`, then as `choice C --director-token <t>`
- **THEN** Both are refused; nothing is recorded (no choice_made, no choice_allow, pending still waiting); the same token with `choice A` then succeeds
<!-- proven by test CP4-T17 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A pick that is not an offered letter, or with no question waiting, cannot be signed or applied
- **GIVEN** (a) an enrolled lane with options A,B,C pending; (b) an enrolled lane with no question pending
- **WHEN** (a) the director runs `director-sign choice D`; (b) the director runs `director-sign choice A` and the lane applies whatever it printed
- **THEN** (a) D is refused naming the offered letters; (b) nothing is recorded and the message says to run `ask choice` first; no choice_allow appears in either lane
<!-- proven by test CP4-T18 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A choice token from an older question, or already used, is refused
- **GIVEN** An enrolled lane where the director signed choice A, then the lane ran `ask choice --options A,B,C` again (a new question); separately a token that was applied once
- **WHEN** The lane applies the old token to the new question; later replays the already-used token after a pick
- **THEN** The stale token is refused and the new question stays pending; the replay is refused (no question waiting) and the recorded pick is unchanged; a freshly signed token works
<!-- proven by test CP4-T19 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A choice token from another lane is refused
- **GIVEN** Two enrolled lanes, A and B, each with `ask choice --options A,B` pending
- **WHEN** A token signed in lane A is applied in lane B
- **THEN** Lane B refuses it with nothing recorded; lane A can still use its own
<!-- proven by test CP4-T20 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A builder session cannot pick for the human, and a chat reply picks nothing in an enrolled lane
- **GIVEN** An enrolled lane with options A,B,C pending
- **WHEN** `director-sign choice A` is run from a session that is not the director's; the lane applies a made-up token; a human chat reply `A` is recorded and the lane runs `choice A` with no token
- **THEN** All three are refused (the last names the director token); no choice_made event, no choice_allow, and the question is still pending
<!-- proven by test CP4-T21 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: The docs list both new director actions
- **GIVEN** The shipped README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** Their director sections are read as text
- **THEN** The README director paragraph, the orchestrator skill's "Running under a director" section and schemas.md section 9 each name `director-sign raise-limit` (with --reason) and `director-sign choice`, state that `ask` runs before signing (a token signed before a re-ask is stale), and the existing actions (approve-plan, accept-finding, ...) are still listed
<!-- proven by test CP4-T22 in tests/test_director_sign_more.py, commit d941ad6 -->

#### Scenario: A lettered question can be put in an enrolled lane and waits for the director, not for a chat reply
- **GIVEN** A director-enrolled lane building CP1 with no question pending
- **WHEN** The lane runs `ask choice --options A,B`, then a human chat reply `A` is recorded, then the lane runs `choice A` with no token
- **THEN** `ask choice` succeeds (no 'put the options to the director' refusal) and the question is recorded as pending with options A and B and an asked-at time; the chat reply and the tokenless `choice A` are refused naming the director token; nothing is recorded (no choice_made, no choice_allow) and the question is still pending until a director token for A or B is applied
<!-- proven by test CP4-T23 in tests/test_director_sign_more.py, commit d941ad6 -->

### Requirement: Sign and apply in one step
The system SHALL do this: A director can sign and apply any routine approval with one command from any phase it applies to, in one order the docs state, and an enrolled lane's messages name the director, never 'ask the user'.

- `director-sign <action> [target] [--ids F1,F2] [--reason R] --apply` signs and applies approve-changes, accept-finding, raise-limit and choice in one call (target is the step for raise-limit and accept-finding, the letter for choice), running any question step the action needs itself
- It works on a lane that paused itself for the director, and the lane carries on afterward
- In an enrolled lane, next, status and refusals name the director's token as the next action and never tell the agent to ask the user
- README and the orchestrator skill state one order for every director-signed action, and the old separate steps still work

#### Scenario: Director approves changed test files in one command
- **GIVEN** an enrolled lane is building CP1 and tests/cp1.test.js was edited after the plan was approved, so `next`/the build refuse with a changed-files problem; the director has not run ask or signed anything
- **WHEN** the director runs `director-sign approve-changes --apply` once
- **THEN** it exits 0; the edited file is the new approved baseline (integrity_problems is empty, `approved` holds the new hash); history has one changes_approved event with by=director; no pending question is left; the lane's very next step command (e.g. tests-written CP1) is no longer refused for the changed file
<!-- proven by test CP5-T01 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Director accepts open review findings in one command
- **GIVEN** an enrolled lane is blocked at CP1's review limit with open findings F1 and F2 in the last round and the code unchanged since that round
- **WHEN** the director runs `director-sign accept-finding CP1 --ids F1,F2 --reason "known limit, tracked for later" --apply`
- **THEN** it exits 0; both findings are recorded accepted with accepted_by=director and the director's reason in the round's deferred findings; learnings.md gets one line per finding; the adversarial gate passes for that round without another round; no pending question is left
<!-- proven by test CP5-T02 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Director raises a review limit by one round in one command
- **GIVEN** an enrolled lane is blocked because CP1 used all of its 2 allowed review rounds, and CP1's one raise is unused
- **WHEN** the director runs `director-sign raise-limit CP1 --reason "edge case worth one more look" --apply`
- **THEN** it exits 0; CP1's recorded cap is the configured cap plus exactly one (cap_raised_to == 3); the reason is saved to learnings.md; the run is no longer blocked and `reviewers CP1` opens exactly one more round; the step's earlier attempt count is kept (not reset to a fresh budget)
<!-- proven by test CP5-T03 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Director picks a lettered option in one command
- **GIVEN** a lane has a lettered question pending with options A and B, where A approves changing the planned test file tests/cp1.test.js, and the lane is then enrolled
- **WHEN** the director runs `director-sign choice A --apply`
- **THEN** it exits 0; the pick is recorded exactly as a developer's reply of A would be (history choice_made letter A, choice_allow listing the file A unlocks, pending cleared); the follow-up `approve-changes` for that file then works under the pick without a second signature question
<!-- proven by test CP5-T04 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: A lane that paused itself for the director carries on
- **GIVEN** an enrolled lane ran `pause --reason "waiting for the director to approve test changes"` after editing a planned test file, so its phase is paused (paused_from building)
- **WHEN** the director runs `director-sign approve-changes --apply`, and the lane then runs `resume`
- **THEN** the apply exits 0 even though the lane is paused; the edited file is the approved baseline; after resume the phase is building with the step it was on, `next` gives the normal build action (not a pause or a refusal), and that step's command runs without the changed-files refusal
<!-- proven by test CP5-T05 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Approve-changes applies from every phase it applies to
- **GIVEN** enrolled lanes with a changed planned test file sitting in each of: blocked, and awaiting_checkpoint_review
- **WHEN** the director runs `director-sign approve-changes --apply` in each
- **THEN** each exits 0 and records one changes_approved event by director without changing the phase to anything else; no `ask approve-changes` was needed first
<!-- proven by test CP5-T06 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: An enrolled lane's next and status name the director, not the user
- **GIVEN** enrolled lanes sitting at awaiting_plan_approval, awaiting_checkpoint_review and blocked at a review limit with open findings
- **WHEN** an agent runs `next`, `status` and `status --json` in each
- **THEN** the next-action text, the plain status line and the json `next` field each mention the director and a `director-sign ...` command with --apply (or --director-token); none of them tells the agent to ask the user, wait for the user's reply, or run `ask approve-plan` / `ask human-approve`; in an ordinary non-enrolled lane the same commands still say to ask the user (unchanged)
<!-- proven by test CP5-T07 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Refusals in an enrolled lane point at the director's command
- **GIVEN** an enrolled lane with a changed planned test file, and another enrolled lane blocked with open findings and no question asked
- **WHEN** an agent runs a gated step (e.g. tests-written), `approve-changes`, and `accept-finding CP1` with no token
- **THEN** each refusal exits non-zero, changes nothing, names `director-sign <that action>` (with --apply) as the next step, and does not say to show the user the changes or the question or to wait for their reply
<!-- proven by test CP5-T08 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Docs state one order for every director-signed action
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md section 9
- **WHEN** their director sections are read
- **THEN** each states the one order (ask the question if the action needs one, sign, then apply with the token, or all three at once with `director-sign <action> --apply`), lists approve-changes, accept-finding, raise-limit and choice as --apply actions, and says the old separate steps still work
<!-- proven by test CP5-T09 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: The old separate steps still work
- **GIVEN** an enrolled lane with a changed planned test file
- **WHEN** the director runs `director-sign approve-changes` without --apply (the last stdout line must be the bare token), the agent runs `ask approve-changes` and then `approve-changes --director-token <token>`, signed after the ask as today
- **THEN** the token is the only thing on the last line, sign alone changes no state, and the old three-step flow approves the change exactly as before; the existing hooks-waive and approve-plan token flows are unaffected
<!-- proven by test CP5-T10 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: A builder session cannot sign and apply
- **GIVEN** an enrolled lane with a changed planned test file; a builder session without RATCHET_ROLE=director
- **WHEN** the builder runs `director-sign approve-changes --apply`, and also tries the same command through the Bash guard hook
- **THEN** the command is refused as director-only and the guard hook blocks it; the file stays unapproved, nothing is asked or left pending, and no history event is added
<!-- proven by test CP5-T11 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Apply refuses a lane that is not director-enrolled
- **GIVEN** an ordinary human-approver lane (not enrolled) with a changed planned test file
- **WHEN** the director session runs `director-sign approve-changes --apply` against it with --root
- **THEN** it is refused with a plain message that this lane takes the developer's approval; the file stays unapproved, no question is left pending, and the normal human `ask approve-changes` + reply flow still works afterwards
<!-- proven by test CP5-T12 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Apply refuses an action that does not apply to the lane right now
- **GIVEN** an enrolled lane at awaiting_plan_approval; an enrolled lane that is building and not blocked; a lane that is done
- **WHEN** the director runs accept-finding, raise-limit and choice with --apply there, and approve-changes --apply at awaiting_plan_approval
- **THEN** each exits non-zero with one plain line naming the phase or missing condition (not blocked, no open findings, no lettered question waiting, plan not yet approved); state.json is byte-identical before and after, with no question left pending
<!-- proven by test CP5-T13 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: A bad request leaves the lane exactly as it was
- **GIVEN** the blocked lane with open findings F1 and F2, and the lane with a pending A/B choice
- **WHEN** the director applies accept-finding with no --reason, accept-finding for an id that is not open (F9), raise-limit with no --reason, and choice C (not an option)
- **THEN** each is refused naming what to add or fix; state.json is byte-identical before and after (no half-asked question, no stale pending); and the same command corrected (reason added, valid id, valid letter) then succeeds at once on the first try
<!-- proven by test CP5-T14 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Applying twice never double-applies
- **GIVEN** the lane after one successful accept-finding --apply, one successful raise-limit --apply, and one approve-changes --apply
- **WHEN** the director repeats each exact command
- **THEN** accept-finding says the findings are already accepted, raise-limit says the one raise is spent, approve-changes says nothing has changed; each adds no second history event or learnings line and the cap stays at configured plus one
<!-- proven by test CP5-T15 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Apply is only for routine approvals, never plans, cancel or hooks
- **GIVEN** an enrolled lane at awaiting_plan_approval and another in building
- **WHEN** the director runs `director-sign approve-plan --apply`, `cancel --apply`, `hooks-waive --apply`, `set-review end --apply` and `human-approve --apply`
- **THEN** none of them approves the plan, cancels the run, waives hooks, changes review mode or approves a step: the phase, `approved` hash, hooks_waived and review mode are all unchanged afterwards (a refusal or a plain token print are both fine)
<!-- proven by test CP5-T16 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Sign and apply run under one hold of the lane's lock
- **GIVEN** an enrolled lane blocked at the review limit; another process holds .ratchet/state.lock for about a second; then two `director-sign raise-limit CP1 --reason ... --apply` started at the same moment
- **WHEN** the first apply starts while the lock is held, and later the two concurrent applies run
- **THEN** the apply waits for the lock and then succeeds with its token not stale (ask, sign and apply all happened inside one hold); for the concurrent pair exactly one succeeds and the other refuses plainly, the cap is raised once, learnings.md has one line, and no lock file is left behind; plain `director-sign <action>` without --apply still works while the lock is held
<!-- proven by test CP5-T17 in tests/test_director_apply.py, commit b50ac5c -->

#### Scenario: Raise-limit apply never resets the run-wide review budget
- **GIVEN** an enrolled lane blocked because the run-wide review budget (not CP1's own cap) is used up, with attempts and adv_rounds_total recorded
- **WHEN** the director runs `director-sign raise-limit CP1 --reason ... --apply`
- **THEN** the run stays blocked with the same adv_rounds_total and attempts, the output does not claim the run is unblocked, and CP1's gate state is otherwise unchanged (the same rule tests/test_limits.py CP1-X05 pins for the chat flow)
<!-- proven by test CP5-T18 in tests/test_director_apply.py, commit b50ac5c -->

### Requirement: Routine approvals follow the developer's policy
The system SHALL do this: The developer tells their director once which routine approvals it may give and on what terms; the director then signs those under the policy, every signature names the rule it used, and anything outside the policy goes back to the developer.

- The director can show and change the developer's policy (approve-changes for test files only, raise-limit up to N per run, lettered choices that unlock no locked file, accept-finding up to a chosen severity), and every change is logged with its time
- With a policy, `director-sign` (with or without --apply) signs a routine approval the policy allows and records the rule in the lane's log and receipt; one no rule allows is refused with a plain message to ask the developer, and there is no other way around it
- raise-limit's limit counts signed raises across the whole run; past it the raise is refused and the run-wide review budget is untouched
- A policy can never cover plan approval, scope changes, final approval, cancel, review settings, hooks waivers or paid providers; Ratchet refuses such a rule, and those director approvals work exactly as before whether a policy exists or not
- A damaged or unreadable policy file refuses every routine approval instead of acting like no policy; with no policy file director-sign works exactly as before; builder sessions cannot read or change the policy file

#### Scenario: With no policy file, director-sign works exactly as it does today
- **GIVEN** A director-enrolled lane with an open accept-finding question on a major finding and no director-policy.json in RATCHET_HOME
- **WHEN** The director looks at the policy, then runs `director-sign accept-finding`
- **THEN** The view says no policy is set and creates no file; director-sign prints a token as before; applying it accepts the finding and the log entry has no policy_rule
<!-- proven by test CP6-T01 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: The director sets, shows and clears rules, and every change is logged with its time
- **GIVEN** A director session and an empty RATCHET_HOME
- **WHEN** It sets approve-changes=test-files, raise-limit=max:2, choice=no-unlock and accept-finding=max:minor, shows the policy, clears raise-limit, shows it again
- **THEN** Each show lists exactly the rules then in force; `--json` changes lists four sets and one clear, each with a timestamp, the action and the rule; the file is director-policy.json next to director.key, never inside the project (git status of the project is unchanged)
<!-- proven by test CP6-T02 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: A test-file-only change is approved under the approve-changes rule and the rule is recorded
- **GIVEN** Policy approve-changes=test-files; the lane's integrity check lists only 'test file tests/cp1.test.js (...)'
- **WHEN** The director runs `director-sign approve-changes --apply`
- **THEN** The change becomes the new baseline and the lane carries on; the changes_approved log entry has policy_rule 'approve-changes: test-files' and `receipt` shows that rule against the approval
<!-- proven by test CP6-T03 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: A change that also touches the plan, a test plan or the rules is refused even under the rule
- **GIVEN** Policy approve-changes=test-files; the lane has a changed test file AND an edited checkpoint in checkpoints.json (second variant: a changed config rule)
- **WHEN** The director runs `director-sign approve-changes --apply`
- **THEN** It is refused with a plain message naming the non-test item and telling the director to ask the developer; the baseline, the log and the approval stay unchanged
<!-- proven by test CP6-T04 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: Raise-limit is signed up to N per run, then refused, and never resets the run budget
- **GIVEN** Policy raise-limit=max:1; two steps in the run each stuck at their review limit
- **WHEN** The director signs and applies raise-limit on the first step, then on the second
- **THEN** The first raises that step's limit by exactly one round with policy_rule 'raise-limit: max:1 (1 of 1)'; the second is refused naming the used-up rule and telling the director to ask the developer; the run-wide review budget is untouched (tests/test_limits.py CP1-X02..X05 stay green)
<!-- proven by test CP6-T05 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: A lettered choice that unlocks nothing is signed; one that unlocks a locked file is refused
- **GIVEN** Policy choice=no-unlock; a pending lettered question where option A unlocks no file and option B unlocks tests/cp1.test.js
- **WHEN** The director signs and applies A, then (in a fresh pending question) tries B
- **THEN** A is recorded as the developer's pick would be, with policy_rule 'choice: no-unlock'; B is refused with a message to ask the developer and no locked file becomes editable; an option that carries a guardrail is refused the same way
<!-- proven by test CP6-T06 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: Accept-finding signs findings up to the chosen severity, never a blocker
- **GIVEN** Policy accept-finding=max:major; a blocked round with F1 major, F2 minor
- **WHEN** The director signs and applies accept-finding for F1 and F2 with a reason
- **THEN** Both are recorded accepted by the director with policy_rule 'accept-finding: max:major' on each; with the policy at max:minor, accepting the major F1 is refused instead
<!-- proven by test CP6-T07 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: One blocker in the set refuses the whole acceptance
- **GIVEN** Policy accept-finding=max:major; the round has F1 major and F3 blocker
- **WHEN** The director signs and applies accept-finding for F1 and F3
- **THEN** It is refused naming F3 and telling the director to ask the developer; neither finding is marked accepted and the question is still open
<!-- proven by test CP6-T08 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: Once a policy exists, a routine approval no rule covers is refused
- **GIVEN** Policy has only raise-limit=max:1; the lane waits on an accept-finding question (plan assumption, pending the director's confirmation)
- **WHEN** The director runs `director-sign accept-finding --apply`
- **THEN** It is refused, the message names the missing accept-finding rule and says to ask the developer; no token is printed and nothing is accepted
<!-- proven by test CP6-T09 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: The plain token path (no --apply) follows the same policy
- **GIVEN** Policy has no choice rule; a lettered question is pending
- **WHEN** The director runs `director-sign choice A` without --apply
- **THEN** It is refused exactly as --apply would be, so a token cannot be minted around the policy
<!-- proven by test CP6-T10 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: Ratchet refuses a rule for anything a human must approve
- **GIVEN** A director session
- **WHEN** It tries to set a rule for approve-plan, human-approve (final approval), feedback (scope), cancel, set-review, hooks-waive, ui-respec, a paid provider, an unknown action, accept-finding=max:blocker, raise-limit=max:0 and raise-limit=max:abc
- **THEN** Each is refused with a plain message saying why or what is allowed; director-policy.json is not created or changed and no change is logged
<!-- proven by test CP6-T11 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: Approvals outside the policy's four actions behave as today when a policy exists
- **GIVEN** Policy exists with all four rules; the lane awaits plan approval, then a final review
- **WHEN** The director signs approve-plan and human-approve
- **THEN** Tokens are printed exactly as without a policy, carry no policy_rule, and the policy neither grants nor blocks them
<!-- proven by test CP6-T12 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: A builder session cannot read or change the policy
- **GIVEN** A policy file exists; a builder session (no director role)
- **WHEN** It runs the policy command to show, set and clear, and its tool calls cat, edit or redirect into RATCHET_HOME/director-policy.json
- **THEN** The commands are refused as director-only and the guard blocks the file access; the file's bytes are unchanged, and director-sign by the builder is still refused
<!-- proven by test CP6-T13 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: A damaged policy file stops routine signing instead of acting like no policy
- **GIVEN** director-policy.json holds text that is not valid policy JSON
- **WHEN** The director runs `director-sign raise-limit CP1 --reason ... --apply`
- **THEN** It is refused with a plain message that the policy file is unreadable and the developer must reset it; nothing is signed or raised
<!-- proven by test CP6-T14 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: Policy approvals and chat approvals stay distinguishable in the receipt
- **GIVEN** One run with an accept-finding signed under the policy, and a separate run with an approve-changes the developer gave by chat reply (no director mode)
- **WHEN** `receipt` is produced for each run
- **THEN** The policy run lists the rule text for its signature; the chat-approved run shows no rule and is still labelled as the human's
<!-- proven by test CP6-T15 in tests/test_director_policy.py, commit ca58bf7 -->

#### Scenario: The docs describe the policy command, file location, menu of rules and the refusal
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and references/schemas.md
- **WHEN** They are searched for director-policy and policy_rule
- **THEN** All three mention the command, the per-user file next to the director key, the four rules and that plan, scope, final approval, cancel, review settings, hooks waivers and paid providers can never be covered; schemas.md section 9 lists policy_rule on the log entry
<!-- proven by test CP6-T16 in tests/test_director_policy.py, commit ca58bf7 -->

### Requirement: Start several lanes from one request
The system SHALL do this: A director can turn one request into several ready lanes with one command: each gets its own branch, plain git worktree, director enrollment and start instructions for whatever agent CLI the developer uses.

- From the director's session, `lanes-start --request "<text>" --lane NAME=FEATURE [--lane NAME=FEATURE ...] [--json]` (one lane or more) creates a branch and git worktree per lane from main, enrolls each with the director, and writes each lane's brief
- It prints, per lane, the command to start it from the developer's own start-command setting, or plain words for opening any agent CLI in that worktree; nothing needs Paseo
- Main's uncommitted Ratchet settings are copied into each new worktree as uncommitted changes (nothing is committed on main), so every lane starts with main's reviewer settings
- A lane name, branch or worktree folder that already exists, or a main with uncommitted project changes, is refused before anything is created and leaves no request record; a request that starts is recorded with its lanes

#### Scenario: One command creates a branch and a plain git worktree per lane, from main
- **GIVEN** main is clean at commit M; the request "Add search and export" with lanes search and export
- **WHEN** the director runs lanes-start with both lanes
- **THEN** the command succeeds; two new local branches exist, one named for each lane, both pointing at main's HEAD; `git worktree list --porcelain` lists a second and a third worktree on those branches, each a sibling folder of the main checkout and named for its lane; each worktree's HEAD file tree equals main's; main's own branch and HEAD are unchanged
<!-- proven by test CP7-T01 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Each lane is enrolled with the director and has no run yet
- **GIVEN** the same two-lane request
- **WHEN** lanes-start finishes
- **THEN** each worktree has .ratchet/director.json with a signature, the per-user enrolled.json under RATCHET_HOME lists both worktree paths, and neither worktree has an active run (no .ratchet/active.json, no .ratchet/runs entry); main's own .ratchet/ run state is not changed
<!-- proven by test CP7-T02 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: The lane agent can run init itself and its plan approval waits on the director
- **GIVEN** lanes-start has made the search lane; a prompt hook for session s-lane-search has logged a user message in that worktree
- **WHEN** that session runs `init` in the worktree, and the status is read
- **THEN** init succeeds with no refusal about enrollment; status --json in the lane shows approval_mode "director"; a chat reply "yes, go ahead" from the lane's session does not approve the plan (approve-plan is refused and asks for a director token)
<!-- proven by test CP7-T03 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Each lane gets its own brief naming the request and its own feature
- **GIVEN** the two-lane request
- **WHEN** lanes-start --json runs
- **THEN** each lane entry names a brief file that exists, is outside git's tracked files (git status in the worktree stays clean), contains the whole request text, that lane's name and its own feature text ("Search box on the list page" for search), and does not contain the other lane's feature text
<!-- proven by test CP7-T04 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: The start command comes from the developer's own template, one per lane
- **GIVEN** config lane_start_command is ["mycli", "--cwd", "{worktree}", "--brief", "{brief}", "--name", "{name}"]
- **WHEN** the director runs lanes-start for search and export (text and --json)
- **THEN** the output shows, for each lane, exactly that command with {worktree}, {brief} and {name} replaced by that lane's own worktree path, brief path and name (the two commands differ in all three); --json gives the same string in each lane's start field; the fake mycli marker file is never written, so Ratchet only printed the command
<!-- proven by test CP7-T05 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: With no template, the output says in plain words how to open any agent CLI in each worktree
- **GIVEN** lane_start_command is not set (the default config)
- **WHEN** the director runs lanes-start
- **THEN** the command succeeds and, per lane, the output names the worktree path and the brief path and says to open the developer's agent CLI in that folder and give it the brief; it contains no made-up launch command for a specific agent CLI (no 'claude -p', 'codex exec' and the like), and no agent CLI process is started
<!-- proven by test CP7-T06 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Nothing needs Paseo
- **GIVEN** PATH has no paseo binary (a fake paseo script exists elsewhere and logs any run), and no Paseo setting anywhere
- **WHEN** the director runs lanes-start in both modes (default output and --json), with and without a template
- **THEN** every run succeeds; the fake paseo marker file is never written; neither stdout nor stderr contains the word 'paseo' in any letter case
<!-- proven by test CP7-T07 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: A start command with a space in the worktree path stays one argument
- **GIVEN** the main checkout's folder name contains a space, and a template ["mycli", "--cwd", "{worktree}"]
- **WHEN** the director runs lanes-start and the printed start command is split the way a shell would (shlex)
- **THEN** the split gives exactly ["mycli", "--cwd", <the worktree path>] with the path as a single argument, and the worktree exists at that path
<!-- proven by test CP7-T08 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: A template Ratchet cannot use is refused before anything is created
- **GIVEN** lane_start_command is set to a bad value in three runs: a plain string rather than a list, a list with an unknown placeholder {nope}, and an empty list
- **WHEN** the director runs lanes-start each time
- **THEN** each run exits non-zero with a plain message naming lane_start_command and what a valid one looks like; no branch, worktree, enrollment or request record exists afterward, and main's HEAD is unchanged
<!-- proven by test CP7-T09 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Main's uncommitted Ratchet settings are copied into each new worktree, uncommitted, so every lane starts with main's reviewer settings
- **GIVEN** main has an uncommitted edit to .ratchet/config.json (a reviewer split not in HEAD) and an uncommitted new line in .ratchet/learnings.md, and nothing else uncommitted; the bytes of both files and main's HEAD are noted first
- **WHEN** the director runs lanes-start
- **THEN** main gains no commit (HEAD is the noted commit) and both of its files are byte for byte as before and still uncommitted (git status in main lists exactly those two paths as modified); each worktree's .ratchet/config.json holds the reviewer split and its learnings.md the new line, byte for byte equal to main's copies; in each worktree git status lists those paths as uncommitted changes, and each lane branch's HEAD is still main's HEAD (neither file is committed on the lane branch)
<!-- proven by test CP7-T10 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: A main with uncommitted project changes is refused before anything is created
- **GIVEN** main has a modified tracked file src/a.js (run once), then separately a staged new file src/b.js, with Ratchet's shared files also uncommitted-edited in both runs
- **WHEN** the director runs lanes-start
- **THEN** each run exits non-zero with a plain message saying to commit or set aside the named project file and run again; no branch, worktree, enrollment, request record or new commit exists afterward (Ratchet's uncommitted shared files stay as they were on main and are not copied anywhere); the developer's edit and staged file are byte for byte as they were
<!-- proven by test CP7-T11 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: A lane branch that already exists is refused before anything is created, even for a later lane
- **GIVEN** a local branch named export already exists, and search does not
- **WHEN** the director runs lanes-start with lanes search then export
- **THEN** the run exits non-zero naming the branch export and saying to pick another lane name; the search branch and worktree were NOT created either (all checks happen before anything is created); the existing export branch is untouched; no enrollment and no request record
<!-- proven by test CP7-T12 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: A worktree folder that already exists is refused and left alone
- **GIVEN** the sibling folder where the export worktree would go already exists and holds a file notes.txt
- **WHEN** the director runs lanes-start
- **THEN** the run exits non-zero naming that folder; notes.txt is unchanged; no branch, worktree, enrollment or request record was created for either lane
<!-- proven by test CP7-T13 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: A request with just one lane starts normally
- **GIVEN** main is clean; the request "Add search" with the single lane --lane search="Search box on the list page"
- **WHEN** the director runs lanes-start (text and --json)
- **THEN** the command succeeds like the multi-lane case: one branch and one sibling worktree for search, enrolled with the director, a brief, a start command or plain words, and a request record listing exactly that one lane; --json has a lanes list of length one
<!-- proven by test CP7-T14 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: The request is recorded with its lanes, and main stays clean afterward
- **GIVEN** a successful two-lane request
- **WHEN** lanes-start finishes
- **THEN** .ratchet/requests/<slug>.json in the main checkout is valid JSON holding the request text, the time, and for each lane in the order given its name, feature, branch, worktree path and brief path, matching what git shows; `git status --porcelain` in main is empty afterward (the record is git-ignored run state), so a later integrate is not blocked by it
<!-- proven by test CP7-T15 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Only the director can start lanes
- **GIVEN** a builder session (no RATCHET_ROLE=director) and the guard hook configured for it
- **WHEN** it runs lanes-start through a Bash tool call, and separately the engine is run directly without the director role
- **THEN** the guard hook blocks the tool call like the other director commands (director-sign, director-enroll); the direct run exits non-zero with a plain refusal that says only the director session can start lanes; in both cases no branch, worktree, enrollment or request record exists
<!-- proven by test CP7-T16 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Without a director key nothing is half created
- **GIVEN** RATCHET_HOME has no director.key, run as the director session
- **WHEN** lanes-start runs
- **THEN** it exits non-zero with a plain message to create the key first (director-key); no branch, worktree, enrollment or request record exists afterward
<!-- proven by test CP7-T17 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: If enrolling fails part way, no lane is left behind
- **GIVEN** the per-user enrolled.json under RATCHET_HOME is damaged (not valid JSON), so enrollment is refused
- **WHEN** the director runs lanes-start for two lanes
- **THEN** the run exits non-zero with a plain message about the damaged record; `git worktree list` shows only the main checkout, no lane branch exists, no request record exists, and the damaged file is not overwritten
<!-- proven by test CP7-T18 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Lanes start from the branch checked out in main, not from a branch named main
- **GIVEN** the main checkout has branch develop checked out, one commit ahead of main
- **WHEN** the director runs lanes-start with --root pointing at it
- **THEN** both lane branches point at develop's HEAD, and the branch named main is untouched
<!-- proven by test CP7-T19 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Starting lanes touches nothing else: no push, no agent files, no change to main's own run
- **GIVEN** main has a bare origin remote and its own approved run mid-step (phase building), with its .ratchet/config.json carrying an uncommitted edit; the status of that run is read before
- **WHEN** the director runs lanes-start
- **THEN** the bare remote's refs are identical (no lane branch pushed, nothing pushed); the worktrees hold no .claude, .codex, .gemini or .opencode folder or other agent CLI permission file that main did not track, and `git status --porcelain` in each worktree lists only the copied .ratchet/config.json; main's own run has the same phase, current step and next action as before, main's HEAD and config.json bytes are unchanged, and there is no new approve-changes or integrity demand
<!-- proven by test CP7-T20 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Docs name the new command and key, and Paseo is only an optional launcher
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** each is read
- **THEN** each mentions lanes-start; schemas.md lists the lane_start_command config key with its three placeholders and the request record under .ratchet/requests/; the director section of SKILL.md says the lane agent runs init itself and Ratchet only prints the start command; any mention of Paseo in these files presents it as one optional way to launch a lane, never a requirement; the guard's director-reserved command list (DIRECTOR_CALL_RE) includes lanes-start
<!-- proven by test CP7-T21 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: Bad lane lists are refused: repeated names, unsafe names, an empty feature, no lanes
- **GIVEN** four requests: two lanes both named search, a lane named ../escape, a lane with an empty feature ("search="), and no --lane at all
- **WHEN** the director runs lanes-start for each
- **THEN** each exits non-zero with a plain message saying what to fix (at least one lane, distinct short plain names, each with a feature); nothing is created outside the main checkout's own folder or its sibling worktree folders, and no branch, worktree, enrollment or request record exists afterward
<!-- proven by test CP7-T22 in tests/test_lanes_start.py, commit de9620a -->

#### Scenario: The request is named from its text, or by --name, and a refused request leaves no record
- **GIVEN** three runs: a request "Add search and export" with no --name; a second request with two new lane names and --name nightly; then a request refused for an existing branch
- **WHEN** the director runs lanes-start each time
- **THEN** the first run's record file under .ratchet/requests/ is named from a slug of the request text and --json's request field equals that name; the second run's record is .ratchet/requests/nightly.json and --json's request field is nightly; after the refused third run the requests folder holds exactly the two earlier records, with none for the refused request
<!-- proven by test CP7-T23 in tests/test_lanes_start.py, commit de9620a -->

### Requirement: Track a request's lanes to merged
The system SHALL do this: A director can see every lane of a request in one place, in request order, with what each is waiting on and whether it is finished or already merged into main.

- `lanes --request <name>` lists each lane of that request with its phase, steps done, what it waits on and since when, and whether it is merged into main, including a lane someone merged by hand
- It works from the main checkout or from inside any lane's worktree, from any session (no director needed), like plain `lanes`
- A finished lane that is not merged yet shows integrate as its next director action, and a lane whose worktree is gone says so instead of failing
- `lanes --request <name> --json` gives the same facts for agents, and plain `lanes` behaves as before

#### Scenario: The request view lists every lane with its phase, steps done and what it waits on
- **GIVEN** Request 'checkout-flow' has lanes zeta (done 2/2), alpha (done, merged) and mid (waiting for the director to approve its plan since a recorded time)
- **WHEN** The director runs `lanes --request checkout-flow` from the main checkout
- **THEN** Exit 0. Each of the three lane names appears with its phase (done, done, awaiting_plan_approval), its steps done as 2/2, 2/2 and 0/N, and mid is shown as waiting on the director together with its awaiting_since time; lanes of the request are found even though their worktrees sit outside the main checkout folder
<!-- proven by test CP8-T01 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: Lanes are listed in the request's own order
- **GIVEN** The request was started with lanes in the order zeta, alpha, mid (not alphabetical, and created in a different order on disk)
- **WHEN** The director runs `lanes --request checkout-flow --json` and then the plain text form
- **THEN** The JSON "lanes" array is exactly [zeta, alpha, mid] by "lane", and the text output mentions the lane names in that same order
<!-- proven by test CP8-T02 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A lane merged by integrate shows as merged, an unmerged finished lane does not
- **GIVEN** alpha was merged into main by integrate; zeta is finished but never integrated
- **WHEN** The director runs `lanes --request checkout-flow --json`
- **THEN** alpha has merged true and zeta has merged false; the text form says merged for alpha and not merged for zeta
<!-- proven by test CP8-T03 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A finished lane that is not merged yet names integrate as the director's next action
- **GIVEN** zeta is finished (phase done) and not merged; alpha is finished and merged; mid is still waiting for plan approval
- **WHEN** The director runs `lanes --request checkout-flow --json` and the text form
- **THEN** zeta's next_director_action is "integrate" and the text line for zeta names integrate (with the lane); alpha and mid have next_director_action null, so integrate is never offered for a merged lane or a lane that is not finished
<!-- proven by test CP8-T04 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A lane whose worktree is gone says so instead of failing
- **GIVEN** Lane ghost's worktree folder was removed with git worktree remove, its branch is still there and it was never merged; the other lanes of its request are fine
- **WHEN** The director runs `lanes --request` for that request, text and --json
- **THEN** Exit 0 with no traceback; ghost appears in its place in the order with worktree_missing true and text saying its worktree is gone and what to do (recreate it from the branch or drop the lane); its next_director_action is null; the other lanes still show their phases
<!-- proven by test CP8-T05 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A merged lane whose worktree was cleaned up still shows merged
- **GIVEN** alpha was merged by integrate (branch tip is in main) and its worktree was then removed
- **WHEN** The director runs `lanes --request checkout-flow --json`
- **THEN** alpha has merged true and worktree_missing true; the text form shows merged first and does not call it a problem
<!-- proven by test CP8-T06 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A lane that has not started yet is listed, not skipped or crashed on
- **GIVEN** Lane fresh was just created by the start command and its agent has not run init, so there is no run in its worktree
- **WHEN** The director runs `lanes --request` for its request, text and --json
- **THEN** Exit 0; fresh is present in request order with phase "not_started", done 0, total 0, waiting_on null, merged false, next_director_action null, and the text says it has not started
<!-- proven by test CP8-T07 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: Rolled-back and refused attempts do not count as merged
- **GIVEN** The integration record holds, for zeta, a rolled-back attempt (suite failed) and a refused attempt, and zeta's branch is not in main; alpha has a failed attempt followed later by a real merge
- **WHEN** The director runs `lanes --request checkout-flow --json`
- **THEN** zeta has merged false and next_director_action "integrate" still; alpha has merged true
<!-- proven by test CP8-T08 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A missing or damaged integration record does not break the view
- **GIVEN** First no .ratchet/integrations.jsonl exists in main; then the file holds one half-written last line and one line that is not JSON, plus a valid merged line for alpha
- **WHEN** The director runs `lanes --request checkout-flow --json` in each state
- **THEN** Both runs exit 0 with no traceback; merged comes from git, so alpha (merged into main by integrate) is merged true in both runs and zeta is false; the damaged file never changes a lane's merged value
<!-- proven by test CP8-T09 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: Only the named request's lanes are shown, and an unknown request is refused plainly
- **GIVEN** Main has two requests, 'checkout-flow' (zeta, alpha, mid) and 'search-fix' (late)
- **WHEN** The director runs `lanes --request search-fix --json`, then `lanes --request nope`
- **THEN** The first lists only late; the second exits non-zero with one plain line that says no request named 'nope' exists and names 'checkout-flow' and 'search-fix' as the ones that do, with no traceback
<!-- proven by test CP8-T10 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A lane waiting on the director or the developer says which and since when
- **GIVEN** A blocked lane (review limit reached, enrolled, so it waits on the director) and a lane waiting on a lettered question, each with a recorded awaiting_since
- **WHEN** The director runs `lanes --request` for their request with --json
- **THEN** Both have waiting_on "director" and their own awaiting_since value; neither has next_director_action "integrate"; the text for each names the director (never 'ask the user') for an enrolled lane
<!-- proven by test CP8-T11 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: The request view changes nothing and never touches the network
- **GIVEN** Main has a bare remote 'origin'; hashes were taken of every lane's .ratchet/runs state, main's .ratchet/ files (including requests/ and integrations.jsonl), each lane's branch tip, main's HEAD and the remote's refs
- **WHEN** The director runs `lanes --request checkout-flow` and `--json` several times
- **THEN** Every hash is unchanged afterward: no state, record, branch, commit or remote ref moved, and no lock file is left behind
<!-- proven by test CP8-T12 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: The JSON form carries the same facts as the text form
- **GIVEN** The three-lane request in its mixed states (finished, merged, waiting on the director)
- **WHEN** The director compares `lanes --request checkout-flow --json` with the text form line by line
- **THEN** For each lane, the phase, steps done of total, waiting_on, awaiting_since and merged status in the JSON all appear in that lane's block of the text, and the JSON parses with exactly the keys request and lanes at the top
<!-- proven by test CP8-T13 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: Plain `lanes` behaves exactly as before
- **GIVEN** The same main checkout and worktrees as above
- **WHEN** The director runs `lanes --scan <folder holding main and its lanes>`, with and without --json
- **THEN** The output is the same shape as on main before this step: a JSON list (each entry still has root, slug, phase, done, total, waiting_on) and, in text, one line per run with slug, phase, done/total and root followed by its viewer line; no request, merged or next_director_action fields or lines are added, and `lanes --serve` and `--stop` still start and stop as before
<!-- proven by test CP8-T14 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: Run from inside a lane, the request view still finds the request
- **GIVEN** A session is in the zeta worktree (a lane of the request), not the main checkout; main's checkout holds the request record
- **WHEN** It runs `lanes --request checkout-flow --json`, and again from the mid worktree
- **THEN** The request record is read from the main checkout (found through the shared git directory) and the output is identical to running from main; if the record cannot be found it says so in one plain line naming the main checkout, not a traceback
<!-- proven by test CP8-T15 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: A lane merged by hand with plain git shows as merged
- **GIVEN** Lane zeta is finished and someone ran `git merge` of its branch into main themselves, so .ratchet/integrations.jsonl has no line for zeta (or does not exist); lane mid's branch has commits not in main
- **WHEN** The director runs `lanes --request checkout-flow --json` and the text form
- **THEN** zeta has merged true and next_director_action null (integrate is not offered again); mid has merged false; the text says merged for zeta. A fast-forward and a squash-free ordinary merge both count, a branch with new commits after the merge does not
<!-- proven by test CP8-T16 in tests/test_lanes_request.py, commit d5b9365 -->

#### Scenario: Any session can run the request view, no director needed
- **GIVEN** The same request, run once with the director environment and once as a builder session (RATCHET_ROLE of a builder, no director key access)
- **WHEN** Each runs `lanes --request checkout-flow --json` from main and from a lane worktree
- **THEN** All four runs exit 0 with the same lanes and facts and are not refused by the guard or by a director check, like plain `lanes`
<!-- proven by test CP8-T17 in tests/test_lanes_request.py, commit d5b9365 -->
