# Fix three root-caused deadlock bugs in Ratchet's own adversarial-review machinery

## Purpose
What this feature does, as built and checked by Ratchet (run fix-three-root-caused-deadlock-bugs-in-ratchet-s, 2026-10-25). Each scenario names the test that proves it.

## Requirements

### Requirement: Scoped review shows real changes first
The system SHALL do this: When a fix round's diff is too big, the reviewer still sees every modified or deleted file before any space goes to brand-new files, smallest new files first.

- A fix round whose diff holds one huge new file listed before a small edited file shows the edited file to the reviewer and names only the huge new file as not shown
- A deleted file (summarized or shown in full) also gets room before any new file
- When several new files compete for the room left, the smaller ones are shown first, and a new file that does not fit is named as not shown while smaller ones after it still get in
- Files actually shown still print in git's own order, and a diff that fits whole is printed exactly as before

#### Scenario: A big new file listed first no longer pushes out a small edited file
- **GIVEN** since_tree has z/edit.py (about 2,000 lines); current_tree edits about 300 lines of z/edit.py (a real modification chunk of roughly 25,000 chars) and adds a/huge.txt whose pure-add chunk fits under MAX_DIFF on its own (about MAX_DIFF - 10,000 chars) but not together with the edit; git prints a/huge.txt before z/edit.py
- **WHEN** scoped_diff_parts(repo, since_tree, current_tree) is called
- **THEN** shown == ['z/edit.py'] and omitted == ['a/huge.txt']; the text contains z/edit.py's full git chunk verbatim, no body line of a/huge.txt appears, and the not-shown note names exactly a/huge.txt (today's git-order fitting gives the reverse: a/huge.txt shown, z/edit.py omitted)
<!-- proven by test CP1-T01 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: A small deleted file shown in full gets room before a big new file
- **GIVEN** since_tree has m/gone.txt with one short line (a pure delete whose summary would not be shorter, so it is never summarized) and nothing else of note; current_tree deletes m/gone.txt and adds a/huge.txt sized so header + a/huge.txt chunk fits under MAX_DIFF but header + a/huge.txt + the m/gone.txt chunk does not; git prints a/huge.txt first
- **WHEN** scoped_diff_parts is called
- **THEN** shown == ['m/gone.txt'] with its full git delete chunk verbatim, omitted == ['a/huge.txt'], and the note names only a/huge.txt
<!-- proven by test CP1-T02 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: A big deleted file (starting summarized) gets room before a big new file
- **GIVEN** since_tree has m/old_data.txt of about 2,500 lines (pure delete chunk about 30,000 chars, so it starts summarized); current_tree deletes it and adds a/huge.txt sized so header + a/huge.txt fits under MAX_DIFF but header + a/huge.txt + the delete's one-line summary does not; git prints a/huge.txt first
- **WHEN** scoped_diff_parts is called
- **THEN** m/old_data.txt is in shown (its part is either its full git chunk verbatim or git's header lines plus '[file deleted; 2500 lines of former content omitted]'), a/huge.txt is in omitted and named in the note, and no line of a/huge.txt's body appears
<!-- proven by test CP1-T03 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: Competing new files: the smallest go in first, the one that does not fit is named, smaller ones after it still get in
- **GIVEN** current_tree, against since_tree, modifies z/edit.py (chunk about 5,000 chars) and adds four new files in git order b/big.txt (about 100,000 chars), c/mid.txt (about 60,000), d/small1.txt (about 10,000), e/small2.txt (about 8,000), sized from MAX_DIFF so that the edit plus the three smallest adds fit but adding b/big.txt does not, and so that fitting the adds in git's order would instead leave out e/small2.txt
- **WHEN** scoped_diff_parts is called
- **THEN** omitted == ['b/big.txt'] exactly; shown == ['c/mid.txt', 'd/small1.txt', 'e/small2.txt', 'z/edit.py'] (git order); each shown add is its full chunk verbatim; the note names only b/big.txt
<!-- proven by test CP1-T04 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: Shown files still print in git's own order, not the order they were fitted
- **GIVEN** the T04 repo (adds fitted smallest-first and the edit fitted before all adds), plus a pure delete m/gone.txt so a non-add sits between adds in git order
- **WHEN** scoped_diff_parts is called
- **THEN** in the returned text the 'diff --git' lines of the shown files appear in git's order (c/mid.txt, d/small1.txt, e/small2.txt, m/gone.txt, z/edit.py), the text starts with the scoped header, and the not-shown note comes after the last shown chunk; the shown list matches that same order
<!-- proven by test CP1-T05 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: A diff that fits whole is printed exactly as git prints it
- **GIVEN** a small diff (well under MAX_DIFF) holding a new file a/new.txt, a modified m/mod.py, a deleted q/gone.txt and a rename r/old.py -> y/moved.py with a one-line edit
- **WHEN** scoped_diff_parts is called
- **THEN** text == scoped header + `git diff-tree -r -p -M --no-color since current` output byte for byte, shown is every changed path in git's order (the rename by its new name), omitted == [], and no '[file deleted;' summary or '[diff too large' note appears
<!-- proven by test CP1-T06 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: Two new files of the same size: the one git lists first wins the last slot
- **GIVEN** current_tree modifies z/edit.py (small) and adds a/one.txt and b/two.txt with identical byte size and same-length paths (so their chunks are equal length), sized so only one of them fits after the edit
- **WHEN** scoped_diff_parts is called
- **THEN** shown == ['a/one.txt', 'z/edit.py'] and omitted == ['b/two.txt'] (a size tie keeps git's order, as the full-round packer does)
<!-- proven by test CP1-T07 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: A moved-and-edited file counts as a real change, ahead of new files
- **GIVEN** since_tree has m/old.py (about 1,500 lines); current_tree moves it to y/moved.py with a few lines edited (git -M pairs it as one rename chunk) and adds a/huge.txt sized so header + a/huge.txt fits under MAX_DIFF but not together with the rename chunk
- **WHEN** scoped_diff_parts is called
- **THEN** y/moved.py is in shown with its full rename chunk verbatim (it contains 'rename from m/old.py'), a/huge.txt is in omitted and named in the note
<!-- proven by test CP1-T08 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: Over the limit with no new files: real changes still fit in git's order as before
- **GIVEN** current_tree modifies three files in git order a/mod1.py (chunk about 100,000 chars), b/mod2.py (about 90,000), c/mod3.py (about 5,000), sized so a/mod1.py fits, b/mod2.py does not fit after it, and c/mod3.py still does
- **WHEN** scoped_diff_parts is called
- **THEN** shown == ['a/mod1.py', 'c/mod3.py'] and omitted == ['b/mod2.py'] (a modification is never cut mid-file, and a later file still gets in), exactly as before this step
<!-- proven by test CP1-T09 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: A new file too big to fit at all is omitted and the edit shows
- **GIVEN** current_tree adds a/enormous.txt whose chunk alone is larger than MAX_DIFF and modifies z/edit.py (small)
- **WHEN** scoped_diff_parts is called
- **THEN** shown == ['z/edit.py'], omitted == ['a/enormous.txt'], and no prefix of a/enormous.txt's body is printed (an add is never cut short)
<!-- proven by test CP1-T10 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

#### Scenario: Real changes that overflow on their own still leave new files out, and the room left goes to small new files
- **GIVEN** current_tree modifies a/mod1.py (chunk about MAX_DIFF - 3,000 chars) and adds b/tiny.txt (chunk about 1,000 chars) and c/medium.txt (chunk about 20,000 chars)
- **WHEN** scoped_diff_parts is called
- **THEN** shown == ['a/mod1.py', 'b/tiny.txt'] and omitted == ['c/medium.txt']: the modification is fitted first, then only the new file that still fits
<!-- proven by test CP1-T11 in tests/test_scoped_diff_priority.py, commit d7fad1b -->

### Requirement: Reviewer pin change reopens the round
The system SHALL do this: Editing a checkpoint's reviewers pin while its review round is open but not yet recorded makes the next reviewers call open a new round with the pinned reviewers.

- With a two-reviewer round open, pinning the checkpoint to one different reviewer, accepting it with approve-changes, then asking for reviewers again shows a new round with only the pinned reviewer
- Asking for reviewers again with no pin, or with the same pin as when the round opened, shows the same round unchanged (same round number and reviewers)
- A round that was already recorded, or whose code changed, behaves exactly as before

#### Scenario: Pinning an open two-reviewer round to one other reviewer opens a new round with only that reviewer
- **GIVEN** A normal-risk CP1 with no reviewers pin at the adversarial step; `reviewers CP1 --json` has opened round 1 naming two reviewers (host-standards and host-correctness); nobody has reviewed it
- **WHEN** CP1's reviewers pin in checkpoints.json is set to ["host-both"], the change is accepted with ask approve-changes + 'yes, go ahead' + approve-changes, and `reviewers CP1 --json` is called again
- **THEN** The output is round 2 (not 1), its reviewers are exactly ["host-both"], neither host-standards nor host-correctness is listed, state.json's adv_round.round is 2 with the same single reviewer, and its nonce differs from round 1's
<!-- proven by test CP2-T01 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: Asking for reviewers again with no pin and no change shows the same round
- **GIVEN** The same unpinned CP1 with round 1 open naming two reviewers, unreviewed
- **WHEN** `reviewers CP1 --json` is called a second and a third time with nothing edited
- **THEN** Each call reports round 1 with the same two reviewer ids in the same order, state.json's adv_round nonce and opened_at are unchanged, and gates.adversarial.attempts and adv_rounds_total are the same numbers as after the first call
<!-- proven by test CP2-T02 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: A plan change that keeps the same pin does not reopen the round
- **GIVEN** CP1 pinned from the start to ["host-standards", "host-correctness"]; round 1 is open naming exactly that pair, unreviewed
- **WHEN** Another CP1 field in checkpoints.json is edited (its summary text) while the reviewers pin stays exactly the same list, the change is accepted with approve-changes, and `reviewers CP1 --json` is called again
- **THEN** The output is still round 1 with the same two reviewer ids, the adv_round nonce is unchanged, and attempts / adv_rounds_total are unchanged (proves the reopen keys on the pin, not on any plan change)
<!-- proven by test CP2-T03 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: An unpinned step whose plan changes elsewhere keeps its open round
- **GIVEN** Unpinned CP1 with round 1 open naming two reviewers, unreviewed
- **WHEN** CP1's summary is edited (no reviewers key added), approve-changes accepts it, and `reviewers CP1 --json` is called again
- **THEN** Still round 1 with the same two reviewer ids and the same nonce (no pin before and no pin now counts as unchanged)
<!-- proven by test CP2-T04 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: After a pin reopens the round, asking again does not keep reopening it
- **GIVEN** The CP2-T01 state: round 2 is open with only host-both after the pin change
- **WHEN** `reviewers CP1 --json` is called twice more with nothing edited
- **THEN** Both calls report round 2 with exactly ["host-both"] and the same nonce; no round 3 folder (reviews/CP1/adv-round-03) exists
<!-- proven by test CP2-T05 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: Removing a pin from an open pinned round reopens it with the step's default reviewers
- **GIVEN** Normal-risk CP1 pinned to ["host-both"]; round 1 is open naming only host-both, unreviewed
- **WHEN** The reviewers key is deleted from CP1 in checkpoints.json, approve-changes accepts it, and `reviewers CP1 --json` is called again
- **THEN** The output is round 2 naming two reviewers (one standards lens, one correctness lens) and host-both is not among them
<!-- proven by test CP2-T06 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: A pin change replacing a round nobody reviewed costs no round, like any unreviewed replacement
- **GIVEN** The CP2-T01 setup, with gates.adversarial.attempts and adv_rounds_total read right after round 1 opened
- **WHEN** The pin change to ["host-both"] is approved and `reviewers CP1 --json` opens round 2
- **THEN** attempts and adv_rounds_total are the same numbers as after round 1 opened (the existing free-replacement rule for a round with no review file applies unchanged), and round 2 still opens
<!-- proven by test CP2-T07 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: Editing a pin without approving it opens no new round
- **GIVEN** Unpinned CP1 with round 1 open naming two reviewers
- **WHEN** CP1.reviewers is set to ["host-both"] in checkpoints.json but approve-changes is NOT run, then `reviewers CP1 --json` is called
- **THEN** No round 2 is opened: state.json's adv_round is still round 1 with the same two reviewers and nonce, and attempts / adv_rounds_total are unchanged (whether the call is refused for the unapproved plan change or prints round 1)
<!-- proven by test CP2-T08 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: An open round from before this change (no stored pin) is never force-reopened
- **GIVEN** CP1 pinned to ["host-standards", "host-correctness"]; round 1 is open naming that pair; the test then deletes whatever pin field the new code stored on checkpoints.CP1.adv_round in state.json (every adv_round key that was not present on a round before this change: keys outside round, nonce, fingerprint, opened_at, reviewers, narrowed, small_fix, kind, fix_round, prev_round_tree, substitutions, resolved, recorded, failed_providers, failed_reviewers), so the round looks like an old-shape round
- **WHEN** (a) `reviewers CP1 --json` is called with nothing edited; (b) then CP1's pin is changed to ["host-both"] and approved, and `reviewers CP1 --json` is called again
- **THEN** In both (a) and (b) the output is still round 1 with host-standards and host-correctness and the same nonce; no round 2 opens because of the pin
<!-- proven by test CP2-T09 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: A recorded blocked round re-pinned before the fix opens its next round as today, with the new pin
- **GIVEN** Unpinned normal-risk CP1; round 1 (two reviewers) recorded with one blocker from its correctness reviewer and triaged fix
- **WHEN** CP1 is pinned to ["host-both"] and approved, the fixer's 30-line edit to src/f.js is made and `behavior CP1` passes, and `reviewers CP1 --json` is called
- **THEN** Round 2 opens (round == 2, exactly one new round, no round 3) naming exactly ["host-both"], it is not a fix round (kind is null, fix_round null, since a pinned step keeps its pin), and attempts rose by exactly 1 from round 1's value (a round after a recorded round is charged as today)
<!-- proven by test CP2-T10 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: A code change and a pin change together open exactly one new round with the new pin
- **GIVEN** Unpinned CP1 with round 1 open naming two reviewers, unreviewed
- **WHEN** src/f.js is edited (behavior CP1 passes) and CP1 is pinned to ["host-both"] and approved, then `reviewers CP1 --json` is called, then called once more
- **THEN** The first call reports round 2 naming exactly ["host-both"]; the second call reports the same round 2 and nonce (the code change and the pin change do not each add a round)
<!-- proven by test CP2-T11 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: An open unreviewed fix round with no pin edit keeps its one reviewer, and still carries it forward on a code change
- **GIVEN** Unpinned CP1; round 1 blocked by its standards reviewer (triaged fix), the fixer's 30-line edit made, round 2 open as a one-reviewer fix round naming that standards reviewer (kind after a block), unreviewed
- **WHEN** (a) `reviewers CP1 --json` is called again with nothing edited; (b) then a 34-line edit is made (behavior passes) and `reviewers CP1 --json` is called
- **THEN** (a) still round 2, same single reviewer, same nonce; (b) round 3 opens as a fix round naming the same single reviewer with kind after a block and fix_round set (the existing carry-forward that CP3 builds on is untouched), and attempts / adv_rounds_total equal their values before (b)
<!-- proven by test CP2-T12 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

#### Scenario: Pinning to a different reviewer during an open, unreviewed fix round opens with the pinned reviewer, not the carried one
- **GIVEN** Unpinned CP1; round 1 blocked by its standards reviewer (triaged fix), the fixer's 30-line edit made, round 2 open as a one-reviewer fix round naming that standards reviewer (kind after a block), unreviewed
- **WHEN** CP1 is pinned to ["host-both"] (a different reviewer than the one round 2 carries) and the change is accepted with approve-changes, then `reviewers CP1 --json` is called
- **THEN** Round 3 opens (not round 2) naming exactly ["host-both"], not the carried standards reviewer; it is not recorded as a fix round (kind is null, fix_round null), and state.json's adv_round agrees
<!-- proven by test CP2-T13 in tests/test_reviewers_pin_reopen.py, commit 9d769d2 -->

### Requirement: Carried fix-round pick respects a fresh pin
The system SHALL do this: A fix round that is replaced before anyone reviewed it only carries its earlier reviewer forward when the checkpoint is still unpinned; once the checkpoint is pinned to specific reviewer(s), the replacement always resolves through the plain pinned path instead, so a fresh pin actually takes effect even inside the carry-forward.

- After a blocked round, a fix round opens, the checkpoint is then pinned to a different reviewer and the pin is accepted with approve-changes: the next reviewers call (whether or not the code changes again) names exactly the pinned reviewer, never the pre-pin one, even though the pre-pin reviewer stays fully available
- Once pinned, the round is no longer marked as a one-reviewer fix round (small_fix / after-a-block kind), consistent with the already-documented rule that a pin opts a checkpoint out of fix-round mechanics entirely
- With no pin at all, the carry-forward is unchanged: same reviewer and slot when it is still available, and the existing bench stand-in path (test_fix_round.py T22/T26) still passes untouched
- No other part of fix-round selection changes

#### Scenario: A fresh pin beats the carry-forward even when the pinned-away reviewer is still perfectly usable
- **GIVEN** Round 2 is an open, unreviewed fix round naming only codex-standards (slot 1); the checkpoint is then pinned to ["host-correctness"] and the pin is accepted with approve-changes
- **WHEN** the fixer makes a further 2-line edit to src/f.js so the code fingerprint changes, `behavior CP1` runs, and `reviewers CP1 --json` is called
- **THEN** the new round names exactly host-correctness and no codex-standards id anywhere in it, even though codex-standards is still on PATH and not benched
<!-- proven by test CP3-T01 in tests/test_fix_round_carry_bench.py, commit 0909b96 -->

#### Scenario: The pin alone reopens the carry-forward's round, with no further code edit, once CP2's reopen-on-pin-change condition is in place
- **GIVEN** Round 2 is an open, unreviewed fix round naming only codex-standards; the checkpoint is then pinned to ["host-correctness"] and the pin is accepted with approve-changes, with NO further code edit
- **WHEN** `reviewers CP1 --json` is called
- **THEN** the round that comes back names exactly host-correctness, never codex-standards (this is the scenario moved here from CP2's plan, since the fix for it lives on this checkpoint's own line, not CP2's)
<!-- proven by test CP3-T02 in tests/test_fix_round_carry_bench.py, commit 0909b96 -->

#### Scenario: A pin to the exact same reviewer the fix round already had still drops the fix-round mechanics
- **GIVEN** Round 2 is an open, unreviewed fix round naming only codex-standards; the checkpoint is then pinned to ["codex-standards"] itself (a no-op-looking pin) and accepted with approve-changes
- **WHEN** a further 2-line edit to src/f.js, `behavior CP1`, then `reviewers CP1 --json`
- **THEN** the new round names codex-standards (consistent either way), but it is NOT marked small_fix and has no after-a-block kind -- once pinned, the checkpoint is resolved the plain pinned way, never through the fix-round carry-forward, matching the already-documented rule that a pin opts the checkpoint out of fix-round mechanics entirely (CP5-T10)
<!-- proven by test CP3-T03 in tests/test_fix_round_carry_bench.py, commit 0909b96 -->

#### Scenario: Control: with no pin at all, the ordinary carry-forward is unchanged -- same reviewer, same slot
- **GIVEN** Round 2 is an open, unreviewed fix round naming codex-standards in slot 1; the checkpoint is never pinned; codex-standards stays fully available
- **WHEN** the fixer rewrites src/f.js again (34 lines), `behavior CP1` runs, and `reviewers CP1 --json` is called
- **THEN** the new round names exactly codex-standards in slot 1, with no fallback, marked small_fix true with the after-a-block kind -- byte-for-byte the pre-existing behavior
<!-- proven by test CP3-T04 in tests/test_fix_round_carry_bench.py, commit 0909b96 -->

#### Scenario: Control: with no pin, a benched carried reviewer still gets a same-lens stand-in (today's already-working path, must not regress)
- **GIVEN** Round 2 is an open, unreviewed fix round naming only codex-standards; the checkpoint is never pinned; codex-standards is then benched with `reviewer-status --mark codex-standards --reason "quota exceeded"`
- **WHEN** src/f.js is restored to exactly the tree round 1 reviewed, `behavior CP1` runs, and `reviewers CP1 --json` is called
- **THEN** the new round names exactly one reviewer, standards lens, not codex-standards, still small_fix true with the after-a-block kind -- this path is already correct today (test_fix_round.py T22/T26) and this checkpoint's fix must not touch it
<!-- proven by test CP3-T05 in tests/test_fix_round_carry_bench.py, commit 0909b96 -->

#### Scenario: Replacing the unreviewed fix round via a fresh pin still costs no extra round, and asking again does not churn it
- **GIVEN** The CP3-T01 sequence; the step's attempts and the run's adv_rounds_total are read just after round 2 opens
- **WHEN** the new (pinned) round opens, then `reviewers CP1 --json` is called a second time with no further change
- **THEN** attempts and adv_rounds_total are exactly the numbers they were just after round 2 opened, and the second call returns the same round number and the same reviewer with no new round folder
<!-- proven by test CP3-T06 in tests/test_fix_round_carry_bench.py, commit 0909b96 -->

### Requirement: approve-changes stamps the prior pin, not resolved reviewers
The system SHALL do this: A final cross-vendor codex review of the integrated lane diff found that cmd_approve_changes's stamp (added by CP2) used a legacy round's resolved reviewers as its baseline instead of the checkpoint's prior approved pin, causing a false reopen when an unrelated checkpoint edit was approved on a narrowed or substituted legacy round. Already fixed and director-verified both ways (red on commit 0909b96, green after). This checkpoint lands it through a proper gate.

- When approve-changes stamps a legacy open round's reviewers_pin baseline, it uses the checkpoint's PRIOR APPROVED pin (recovered by matching the approved checkpoint hash), not the round's resolved reviewers.
- A narrowed or substituted legacy round does NOT false-reopen on an unrelated approved edit (CP2-X05: same round, same nonce).
- When the prior pin can't be recovered (other fields changed too), the round is left legacy (no stamp) -- never stamped from resolved reviewers.

#### Scenario: An unrelated approved edit on a narrowed legacy round does not reopen
- **GIVEN** CP1 pinned to [host-standards, host-correctness]; round 1 blocked; a 30-line fix; round 2 narrowed to one pinned reviewer and stripped to legacy shape (no reviewers_pin stamp)
- **WHEN** an unrelated summary edit to CP1 (pin untouched) is accepted with approve-changes, then `reviewers CP1 --json` is called
- **THEN** the same round 2 comes back naming the same narrowed reviewer with the same round nonce: approve-changes stamped the prior approved pin, not the round's resolved reviewers
<!-- proven by test CP2-X05 in tests/test_reviewers_pin_reopen.py, commit 11e8eaf -->
