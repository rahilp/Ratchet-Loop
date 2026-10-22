# Adversarial reviewers get an honest short summary of a huge DELETED file instead of losing the round (fix rounds). A huge ADDED file is never summarized -- it is shown in full, deprioritized behind real changes within the size budget, or blocks the round -- because round 2 (B1, a similarity-based move) and round 3 (A6, a module-split) both proved an added file's eligibility can never be characterized safely by diff shape alone.

## Purpose
What this feature does, as built and checked by Ratchet (run fix-skills-ratchet-adversarial-loop-scripts-exte, 2026-10-22). Each scenario names the test that proves it.

## Requirements

### Requirement: Fix rounds summarize huge deleted files
The system SHALL do this: A fix round that deletes one huge file shows the reviewer an honest one-line summary for it, counts it as shown, and can be recorded. A huge added file is never summarized: it is shown in full or still blocks the round. Any real modification is still shown in full or still blocks.

- A fix round that deletes a 1MB file shows the reviewer git's delete headers plus one line saying the file was deleted and how many lines were omitted, and the round is recorded instead of refused
- A fix round that adds a huge new file is omitted and named exactly as before this lane -- never summarized -- and the round blocks until the add fits or the step is scoped down
- A fix round with a huge real modification still names that file as not shown, and recording the round is still refused exactly as today
- A file made to look like a delete but with even one added line (or the other way round, or with a second hunk, odd headers or a wrong line count) is never summarized
- A small fix round looks exactly as it did before

#### Scenario: A fix round that deletes a 1 MB file shows git's delete headers and one honest line
- **GIVEN** the big-delete repo: data/big.txt (40,000 lines, about 1 MB) exists at the last reviewed tree and is deleted at the current tree
- **WHEN** scoped_diff_parts is called for that fix round
- **THEN** data/big.txt is in shown and omitted is empty; its part of the text is exactly git's own header lines in git's order ('diff --git a/data/big.txt b/data/big.txt', 'deleted file mode 100644', 'index <sha>..0000000', '--- a/data/big.txt', '+++ /dev/null', copied verbatim from the real git output) followed by exactly one summary line that names data/big.txt, says it was deleted and gives 40000 (or 40,000) as the number of lines omitted, and does not contain the words 'shown' or 'reviewed'; no '@@' line and no '-' body line for it appear, ZQ-FIRST, ZQ-MIDDLE and ZQ-LAST are absent, there is no 'NOT shown' note, and the whole text is under MAX_DIFF. Fails today: data/big.txt is in omitted
<!-- proven by test CP1-T01 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A fix round that deletes a 1 MB file can be recorded
- **GIVEN** an end-to-end project where data/big.txt (1 MB) was present before round 1, round 1 was blocked by codex-standards and triaged fix, and the fix deletes data/big.txt and changes one line of src/alpha.js
- **WHEN** the fix round opens, the codex reviewer's prompt is read with --dry-run, the fake reviewer returns no findings, and adv-record is run with the review aged 60 s
- **THEN** the prompt contains the delete summary line for data/big.txt and the alpha.js change, and no 'NOT shown' note; the signed review has no omitted_files and its files_reviewed includes data/big.txt and src/alpha.js; adv-record exits 0 and the round is recorded (status shows the round recorded, not refused). Fails today: adv-record refuses and names data/big.txt
<!-- proven by test CP1-T02 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A fix round that adds a huge new file gets the same honest summary and counts it as shown
- **GIVEN** the big-add repo: vendor/blob.js (30,000 lines, about 900 KB) is new at the current tree and src/app.py has a one-line real fix
- **WHEN** scoped_diff_parts is called, and separately the same change is run end to end as a fix round through adv-record
- **THEN** library: shown contains vendor/blob.js and src/app.py, omitted is empty; vendor/blob.js's part is exactly git's header lines ('diff --git a/vendor/blob.js b/vendor/blob.js', 'new file mode 100644', 'index 0000000..<sha>', '--- /dev/null', '+++ b/vendor/blob.js') plus one summary line that names the file, says it was added, gives 30000 (or 30,000) lines, says plainly the new content was not included, asks the reviewer to raise a finding if that content matters, and does not say 'shown' or 'reviewed'; ZQ-ADD-FIRST and ZQ-ADD-LAST are absent; src/app.py's full chunk (with ZQ-REAL-FIX on a '+' line) is present verbatim. End to end: the signed review has no omitted_files and adv-record exits 0. Fails today: vendor/blob.js is omitted and adv-record refuses
<!-- proven by test CP1-T03 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: Summarizing a huge added file frees room so the real edits in the same fix round are shown
- **GIVEN** the room-freeing repo: a 2-line edit to src/a.py, a 150 KB pure add gen/out.json and a 50 KB real edit to src/c.py (about 202 KB in all)
- **WHEN** scoped_diff_parts is called
- **THEN** omitted is empty and shown has all three files; src/a.py's and src/c.py's full git chunks appear verbatim (ZQ-A and ZQ-C present); gen/out.json appears only as its git headers plus one summary line; the whole text is at most MAX_DIFF characters. Fails today: src/c.py is omitted because the add used up the room
<!-- proven by test CP1-T04 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A fix round with a huge real modification still names that file as not shown, exactly as today
- **GIVEN** the big-modify repo: every 2nd line of data/big.txt rewritten (diff over 1 MB) plus a 2-line edit to src/app.py
- **WHEN** scoped_diff_parts is called
- **THEN** shown is ['src/app.py'] and omitted is ['data/big.txt']; the text is byte for byte the header, then src/app.py's git chunk, then today's note '\n[diff too large: these changed files are NOT shown and were NOT reviewed by you (do not approve without them; say so in a finding): data/big.txt]'; no ZQ-MOD marker and no summary line for data/big.txt appear
<!-- proven by test CP1-T05 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: Recording a fix round with a huge real modification is still refused
- **GIVEN** an end-to-end project with data/big.txt (1 MB) present before round 1, round 1 blocked and triaged fix, and a fix that rewrites every 2nd line of data/big.txt and changes one line of src/alpha.js
- **WHEN** the fix round opens, the fake reviewer returns no findings, and adv-record is run with the review aged 60 s
- **THEN** the prompt names data/big.txt after 'NOT shown'; the signed review has omitted_files == ['data/big.txt'] and files_reviewed == ['src/alpha.js']; adv-record exits non-zero, its output names data/big.txt and says such a review never counts; the round is not recorded as passed
<!-- proven by test CP1-T06 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A huge delete and a huge modification in one fix round: the delete is summarized, the modification still blocks
- **GIVEN** the mixed repo: data/old.txt (40,000 lines) deleted and every 2nd line of data/keep.txt rewritten, in the same fix round (library), and the same change end to end
- **WHEN** scoped_diff_parts is called, then adv-record is run with a clean review
- **THEN** library: data/old.txt is in shown as headers plus one summary line; omitted == ['data/keep.txt'] and the NOT shown note names data/keep.txt and does not name data/old.txt. End to end: omitted_files == ['data/keep.txt'], adv-record refuses naming data/keep.txt only. Fails today: data/old.txt is also omitted and named
<!-- proven by test CP1-T07 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A file emptied but kept, or an empty file filled, is a modification and is never summarized
- **GIVEN** real-git near-misses: data/big.txt (40,000 lines) truncated to 0 bytes but still tracked (git prints '--- a/data/big.txt' / '+++ b/data/big.txt' and an all '-' hunk, with no 'deleted file mode'); and a tracked 0-byte data/empty.txt grown to 40,000 lines (all '+', no 'new file mode')
- **WHEN** scoped_diff_parts is called for each
- **THEN** in each case the file is in omitted, not in shown, is named in the NOT shown note, and no summary line for it appears; the text equals what today's code returns (header, fitting chunks, today's note)
<!-- proven by test CP1-T08 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A delete with one added line slipped in is never summarized
- **GIVEN** a hand-built diff: data/x.txt with 'deleted file mode 100644', '--- a/data/x.txt', '+++ /dev/null', '@@ -1,40000 +0,0 @@' and 40,000 body lines that are all '-' except line 20,000, which is '+ZQ-SMUGGLED'
- **WHEN** scoped_diff_parts is called with git's diff-tree output stubbed to that text
- **THEN** data/x.txt is in omitted, not in shown; the NOT shown note names it; no summary line for data/x.txt appears and ZQ-SMUGGLED is not reported anywhere as shown. The same chunk with a ' ' context line instead of the '+' line is also omitted
<!-- proven by test CP1-T09 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: An add with one removed line slipped in is never summarized
- **GIVEN** a hand-built diff: vendor/y.js with 'new file mode 100644', '--- /dev/null', '+++ b/vendor/y.js', '@@ -0,0 +1,40000 @@' and 40,000 body lines all '+' except one '-ZQ-SMUGGLED'
- **WHEN** scoped_diff_parts is called with the stubbed diff-tree output
- **THEN** vendor/y.js is in omitted and named in the NOT shown note; no summary line for it appears
<!-- proven by test CP1-T10 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A delete with a second hunk is never summarized
- **GIVEN** a hand-built textbook delete of data/x.txt (40,000 '-' lines) followed by a second line '@@ -40001,5 +0,0 @@' and 5 more lines (one variant all '-', one variant with '+ZQ-SMUGGLED'); and an add with a second '@@ -0,0 +30001,5 @@' hunk
- **WHEN** scoped_diff_parts is called with the stubbed diff-tree output for each variant
- **THEN** in every variant the file is in omitted and named in the NOT shown note, and no summary line for it appears
<!-- proven by test CP1-T11 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A delete or add whose line count does not match its header is never summarized
- **GIVEN** hand-built pure deletes of data/x.txt whose '@@ -1,40000 +0,0 @@' header is followed by 40,001 '-' lines, or by 39,999 '-' lines; a pure add whose '@@ -0,0 +1,30000 @@' is followed by 30,001 '+' lines; and a delete whose header says 40,001 lines with 40,000 '\n'-separated '-' lines, one of which contains U+2028 followed by '-ZQ-SMUGGLED' (the count only matches if U+2028 is treated as a line break)
- **WHEN** scoped_diff_parts is called with the stubbed diff-tree output for each
- **THEN** every one is in omitted, named in the NOT shown note, with no summary line
<!-- proven by test CP1-T12 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A delete or add with odd headers (rename, similarity, copy, mode change, mismatched paths) is never summarized
- **GIVEN** hand-built 40,000-line all '-' deletes of data/x.txt, each with one header oddity: a 'similarity index 100%' line; 'rename from data/x.txt' / 'rename to data/z.txt'; 'copy from' / 'copy to'; 'old mode 100644' / 'new mode 100755'; 'deleted file mode' twice; both 'new file mode' and 'deleted file mode'; no index line; '+++ b/data/x.txt' instead of '+++ /dev/null'; '--- a/data/other.txt' while the diff --git line names data/x.txt; and an all '+' add of vendor/y.js with '--- a/vendor/y.js' instead of '--- /dev/null'
- **WHEN** scoped_diff_parts is called with the stubbed diff-tree output for each variant
- **THEN** in every variant the file is in omitted and named in the NOT shown note, and no summary line for it appears
<!-- proven by test CP1-T13 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: Only one trailing 'No newline at end of file' marker is allowed, and nothing else after the body
- **GIVEN** hand-built 40,000-line textbook deletes of data/x.txt ending with: exactly one '\ No newline at end of file' line; two such lines; one such line in the middle of the body; one extra ' context' line; one extra 'garbage' line
- **WHEN** scoped_diff_parts is called with the stubbed diff-tree output for each
- **THEN** the variant with exactly one trailing marker is summarized (in shown, headers plus one summary line, 40000 lines); every other variant is in omitted, named in the NOT shown note, with no summary line
<!-- proven by test CP1-T14 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A real large delete whose lines contain unusual characters is still summarized with the right count
- **GIVEN** a real-git repo where data/odd.txt has 40,000 lines, some containing U+2028, U+0085, form feed, vertical tab and a carriage return before the newline, and no newline at the end of the last line; the fix round deletes it
- **WHEN** scoped_diff_parts is called
- **THEN** data/odd.txt is in shown, omitted is empty, and its summary line gives 40000 (or 40,000) lines, the count git's own '@@ -1,40000 +0,0 @@' header gives. Fails today: data/odd.txt is omitted
<!-- proven by test CP1-T15 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A small fix round looks exactly as it did before
- **GIVEN** the small repo: a 3-line edit to src/alpha.js, a new 10-line src/new.js and a deleted 10-line src/gone.js
- **WHEN** scoped_diff_parts is called
- **THEN** text is byte for byte '# Changes since the last round actually reviewed (<since[:10]> → <cur[:10]>)\n' followed by the raw output of `git diff-tree -r -p --no-color <since> <cur>`; shown lists the three files in git's order; omitted is empty; the new file's and the deleted file's full '+' / '-' lines are present and no summary line appears
<!-- proven by test CP1-T16 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: A large delete that still fits under the limit is shown in full, and the limit boundary is exact
- **GIVEN** the under-limit repo (a 100 KB pure delete plus a 2-line edit, real git); and hand-built diffs whose header plus chunks are exactly MAX_DIFF characters, and MAX_DIFF + 1 characters, with a pure delete as the last chunk
- **WHEN** scoped_diff_parts is called for each
- **THEN** the real 100 KB delete and the exactly-MAX_DIFF diff return header + the whole diff unchanged with everything in shown and no summary; the MAX_DIFF + 1 diff summarizes that pure delete (headers plus one summary line, in shown, omitted empty), where today's code would omit it
<!-- proven by test CP1-T17 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: Only as many big adds and deletes are summarized as needed, biggest first
- **GIVEN** a fix round with a 120 KB pure delete data/a.txt, a 100 KB pure delete data/b.txt and a 30 KB real edit to src/c.py (about 250 KB in all); and a variant with two pure deletes of exactly the same size (ties) whose combined size forces one to be summarized
- **WHEN** scoped_diff_parts is called
- **THEN** data/a.txt is summarized; data/b.txt and src/c.py are shown in full; omitted is empty. In the tie variant, the one git prints first is summarized and the other is shown in full
<!-- proven by test CP1-T18 in tests/test_review_diff_large_files.py, commit 320a144 -->

#### Scenario: README and schemas.md say a fix round summarizes a huge added or deleted file in one line
- **GIVEN** README.md and skills/ratchet-orchestrator/references/schemas.md after the build
- **WHEN** the fix-round diff paragraphs are read (README around the scoped-diff description, schemas.md near its omitted_files text)
- **THEN** each says that when a fix round's diff is over the size limit a pure added or deleted file is shown as git's headers plus one line saying it was added or deleted and how many lines were left out, that such a file counts as shown, and that a real modification too large to include is still named as not shown and still stops the round from being recorded
<!-- proven by test CP1-T19 in tests/test_review_diff_large_files.py, commit 320a144 -->

### Requirement: Full rounds: summarize deletes, deprioritize huge adds
The system SHALL do this: In a full review round, a huge DELETED file is summarized in one honest line (CP1's rule extended into collect_diff), so the real changed files still reach the reviewer instead of being cut off by the size limit. A huge ADDED file is never summarized: it is shown in full when it fits, or deprioritized so real changed files are packed into MAX_DIFF first and the add is cut last and named, exactly as a too-large modification is today.

- A first-round review of a step that adds a 1MB fixture plus a small real code change shows the reviewer that real change in full; the fixture, if it doesn't fit, is named as added-but-cut, never summarized
- The same holds in a later round (files changed since the last round come first); the 'left out or cut short' note never claims a cut add was summarized or shown
- A huge pure DELETE in a full round is summarized and counted as shown, same as fix rounds (extends CP1's rule into collect_diff)
- A step with a huge real modification is still cut and named in the 'diff truncated' note exactly as today
- A disguised modification (a delete with one added line slipped in, or similar) is never summarized in a full round either
- A normal-size step's review prompt is exactly what it was before

#### Scenario: First review of a step that adds a 1MB fixture and changes a little code shows the code change in full; the fixture is cut last and named, never summarized
- **GIVEN** A step that adds aaa-fixture.txt (25,000 lines, about 1MB) and edits src/app.py (REAL-CHANGE-7c1e); git prints the fixture first and the whole diff is far over 180,000 chars
- **WHEN** collect_diff(root, base_tree, current_tree) is called with no prev_round_tree (round 1)
- **THEN** The output equals, byte for byte, header + src/app.py's full chunk + aaa-fixture.txt's chunk cut at (180000 - len(header) - len(app chunk)) chars + '\n[diff truncated at 180000 chars — left out or cut short (ask about these if they matter): aaa-fixture.txt]'. So: REAL-CHANGE-7c1e is present; the fixture part starts with git's own 'diff --git', 'new file mode', 'index', '--- /dev/null', '+++ b/aaa-fixture.txt' and '@@ -0,0 +1,25000 @@' lines followed by its real '+FIXTURE-LINE-' lines (it is shown as far as it fits, never replaced); no '[file added' or '[file deleted' line appears; the note contains neither 'summar' nor 'shown'; the output contains no 'previous review round' text. Fails today: the blind slice ends inside the fixture and REAL-CHANGE-7c1e is missing.
<!-- proven by test CP2-T01 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: A later review round shows every real change ahead of a huge file added in an earlier round, and names only that file as cut
- **GIVEN** Round 1's tree added aaa-fixture.txt (1MB) and edited src/other.py (OTHER-CHANGE-3b9d); round 2's tree edits only src/app.py (REAL-CHANGE-7c1e); base is the step start
- **WHEN** collect_diff(root, base, round2_tree, prev_round_tree=round1_tree) is called
- **THEN** The output equals, byte for byte, header + '# Files changed since the previous review round (src/app.py): their diffs come first below.\n' + src/app.py's full chunk + src/other.py's full chunk + aaa-fixture.txt's chunk cut at the remaining budget + '\n[diff truncated at 180000 chars — left out or cut short (ask about these if they matter): aaa-fixture.txt]'. OTHER-CHANGE-3b9d is present; src/other.py is not named in the note; the fixture has no summary line; the note never says the fixture was summarized or shown. Fails today: git prints the fixture before src/other.py, so the fixture eats the budget and the note names both aaa-fixture.txt and src/other.py.
<!-- proven by test CP2-T02 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: A real modification that git prints after a huge added file is packed in first, in both kinds of full round
- **GIVEN** A step that adds aaa-fixture.txt (1MB) and rewrites src/zz_big.py (about 100K of diff, MID-MOD-44e1), plus a later round whose only new change is a src/app.py edit (so src/zz_big.py and the fixture are both unchanged since round 1)
- **WHEN** collect_diff is called for round 1, and for the later round with prev_round_tree = round 1's tree
- **THEN** In both outputs src/zz_big.py's full chunk appears verbatim (both MID-MOD-44e1 markers present) and starts before aaa-fixture.txt's 'diff --git' line; in the later round src/app.py's chunk comes first, right after the note; the closing note names exactly aaa-fixture.txt (not src/zz_big.py, not src/app.py); the fixture is shown only as a verbatim prefix of its own chunk, never as a summary line. Fails today: the fixture uses the whole budget and src/zz_big.py is entirely missing (round 1) or left out and named (later round).
<!-- proven by test CP2-T03 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: First review of a step that deletes a 1MB vendored file shows one honest line for it and the code change in full
- **GIVEN** A step that deletes aaa-vendor.js (25,000 lines, about 1MB, present at base) and edits src/app.py (REAL-CHANGE-7c1e)
- **WHEN** collect_diff is called for round 1
- **THEN** The output equals header + aaa-vendor.js's summarized part + src/app.py's full chunk, with no truncated note at all; the summarized part is byte-identical to aaa-vendor.js's part in scoped_diff_parts(root, base_tree, current_tree)'s text (git's 5 delete header lines verbatim, then '[file deleted; 25000 lines of former content omitted]'); no 'VENDOR-LINE-' text and no '@@' line for it appear; the whole output is at most 180000 chars; aaa-vendor.js is never named as cut. Fails today: the blind slice ends inside the deleted file and REAL-CHANGE-7c1e is missing.
<!-- proven by test CP2-T04 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: A later review round summarizes a huge deleted file whether it was deleted in this round or an earlier one
- **GIVEN** Variant A: round 1's tree edits src/app.py; round 2's tree deletes aaa-vendor.js and edits src/zz_late.py (LATE-CHANGE-5a0f), so both are changed since the previous round. Variant B: round 1's tree deletes aaa-vendor.js; round 2's tree edits only src/app.py
- **WHEN** collect_diff is called with prev_round_tree = round 1's tree for each variant
- **THEN** Variant A equals header + the note listing 'aaa-vendor.js, src/zz_late.py' + aaa-vendor.js's summary part + src/zz_late.py's full chunk + src/app.py's full chunk, no truncated note. Variant B equals header + the note listing 'src/app.py' + src/app.py's full chunk + aaa-vendor.js's summary part, no truncated note. In both the summary part is git's delete headers verbatim plus '[file deleted; 25000 lines of former content omitted]', no 'VENDOR-LINE-' text appears and aaa-vendor.js is never named as left out or cut. Fails today: the deleted file's own diff uses the budget and the other files are cut and named.
<!-- proven by test CP2-T05 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: With a huge delete and a huge add in the same step, the delete is summarized and the add still never is
- **GIVEN** A step that deletes aaa-vendor.js (1MB), adds aaa-fixture.txt (1MB) and edits src/app.py; and a later round where only src/app.py changed again
- **WHEN** collect_diff is called for round 1, and for the later round
- **THEN** In both outputs: aaa-vendor.js appears only as its summary part (identical to scoped_diff_parts' part for it); src/app.py's full chunk appears and precedes aaa-fixture.txt's 'diff --git' line; aaa-fixture.txt's part is a verbatim prefix of its own chunk including its '@@ -0,0 +1,25000 @@' line and real '+FIXTURE-LINE-' lines, never a '[file added' line; the closing 'left out or cut short' note names exactly aaa-fixture.txt and never aaa-vendor.js; the output is at most 180000 chars plus that note. Fails today (round 1: the slice ends inside the first huge file and REAL-CHANGE-7c1e is missing).
<!-- proven by test CP2-T06 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: Only as many deleted files are summarized as needed, biggest first
- **GIVEN** A step that deletes aaa-a.txt (about 1MB) and bbb-b.txt (about 100K) and edits src/app.py; summarizing aaa-a.txt alone brings the whole diff under 180000; and a later round where only src/app.py changed again
- **WHEN** collect_diff is called for round 1, and for the later round
- **THEN** In both outputs aaa-a.txt is its summary part only; bbb-b.txt's full chunk (every '-' line) appears verbatim; src/app.py's full chunk appears; there is no truncated note. Fails today (the 1MB delete eats the budget).
<!-- proven by test CP2-T07 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: A first review of a step with only a huge real modification is cut exactly as today
- **GIVEN** A step that rewrites every line of big.txt (over 400K chars of diff, HUGE-MOD-9c2b) and edits src/app.py; no file is a pure add or pure delete
- **WHEN** collect_diff is called for round 1
- **THEN** The output equals, byte for byte, ('# Changes in this checkpoint (<base[:10]> → <current[:10]>)\n' + git's full diff-tree output)[:180000] + '\n\n[diff truncated at 180000 chars — flag anything you could not see]'; no '[file deleted' or '[file added' line appears.
<!-- proven by test CP2-T08 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: A later review round with a huge real modification cuts and names it exactly as today
- **GIVEN** Round 1's tree rewrites every line of big.txt; round 2's tree edits src/app.py; no pure add or delete anywhere
- **WHEN** collect_diff is called with prev_round_tree = round 1's tree
- **THEN** The output equals, byte for byte, header + the note naming src/app.py + src/app.py's full chunk + big.txt's chunk cut at exactly 180000 minus the chars already used + '\n[diff truncated at 180000 chars — left out or cut short (ask about these if they matter): big.txt]'; big.txt has no summary line.
<!-- proven by test CP2-T09 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: With a huge real modification and a huge add, the modification gets the budget first and both are named as cut
- **GIVEN** A step that rewrites every line of big.txt and adds aaa-fixture.txt (1MB, printed first by git), plus a later round that edits src/app.py
- **WHEN** collect_diff is called for round 1, and for the later round
- **THEN** In both outputs big.txt's chunk starts in full git form (its diff --git, index, ---, +++ and @@ lines, then HUGE-MOD-9c2b lines) before any aaa-fixture.txt text and is cut by the size limit; neither file gets a summary line; the closing note is the 'left out or cut short' note naming exactly 'big.txt, aaa-fixture.txt' in that order (later round: src/app.py first in full and not named). Round 1 fails today (the fixture comes first and big.txt gets no budget); the later round fails today (the note lists aaa-fixture.txt before big.txt and big.txt gets no budget).
<!-- proven by test CP2-T10 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: Real files made to look like a delete or an add (emptied, filled from empty, cut to one line) are never summarized in a full round
- **GIVEN** Three steps, each deleting aaa-vendor.js (a textbook 1MB delete, so the new packing applies) and editing src/app.py, plus one disguised file: (a) trunc.txt (25,000 lines) emptied but kept; (b) grown.txt, empty at base, grown to 25,000 lines; (c) onel.txt (25,000 lines) replaced by one different line
- **WHEN** collect_diff is called for round 1 and, with a later round that edits src/app.py again, with prev_round_tree for each step
- **THEN** In all six outputs aaa-vendor.js is its summary part; the disguised file's part is a verbatim prefix of git's own chunk for it, starting with its diff --git, index, ---, +++ and @@ lines followed by its real body lines (no 'deleted file mode' or 'new file mode' is invented for it); it gets no '[file deleted' or '[file added' line; it is named in the closing 'left out or cut short' note; src/app.py's full chunk is present. Fails today (round 1: the slice ends inside the first big file; no named note).
<!-- proven by test CP2-T11 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: A hand-made delete or add with one smuggled line, a second hunk, a wrong line count or an odd header is never summarized in a full round
- **GIVEN** The stubbed 'diff-tree -r -p' returns, one variant per call, chunk (i) x.txt delete with '+SNEAKY-ADD-1d4e'; (ii) y.txt delete with a second hunk '-SNEAKY-HUNK-8f2a'; (iii) z.txt add with '-SNEAKY-MINUS-2e7a'; (iv) w.txt delete whose header count is one short ('-SNEAKY-COUNT-6b3d'); (v) v.txt delete with 'similarity index 100%' ('-SNEAKY-SIM-0a9f'); each followed by a textbook delete chunk and a small src/app.py chunk
- **WHEN** collect_diff is called for each variant once for round 1 and once with a prev_round_tree (name-only diff reports src/app.py)
- **THEN** In all ten outputs the disguised file gets no '[file deleted' or '[file added' line; its part is a verbatim prefix of its hand-built chunk, long enough to include its SNEAKY marker (the marker string is present in the output); it is named in the closing 'left out or cut short' note; the textbook delete next to it is summarized (proving the summarizer is active in that call); src/app.py's chunk is present in full.
<!-- proven by test CP2-T12 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: A normal-size step's full review diff is exactly what it was before, in round 1 and in a later round
- **GIVEN** The normal-size step: aaa-new.js added (20 lines, printed first by git), src/gone.js deleted (30 lines), src/app.py edited; round 2 edits src/app.py again; the whole diff is a few KB
- **WHEN** collect_diff is called for round 1 and for the later round
- **THEN** Round 1 equals header + git's full diff-tree output byte for byte (aaa-new.js still first, not moved after the modification). The later round equals header + '# Files changed since the previous review round (src/app.py): their diffs come first below.\n' + src/app.py's chunk + the rest in git's order (aaa-new.js, then src/gone.js) byte for byte. Neither has a summary line or a truncated note; the small added and deleted files show their full content.
<!-- proven by test CP2-T13 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: The size limit boundary is exact: at the limit nothing changes, one character over it the new packing starts
- **GIVEN** Boundary repos (a) a pure add making the round-1 text exactly 180000 chars; (b) that add one char longer plus a 1-line src/app.py edit printed after it, total one char over 180000 with room for the edit; (c) a pure delete making the round-1 text exactly 180000; (d) that delete one char longer
- **WHEN** collect_diff is called for round 1 on each
- **THEN** (a) equals header + full diff byte for byte (whole add content, no note). (b) equals header + src/app.py's chunk + the add's chunk cut at the remaining budget + the 'left out or cut short' note naming exactly aaa-edge.txt; no summary line. (c) equals header + full diff byte for byte (every '-' line, no summary). (d) equals header + aaa-edge-del.txt's summary part, with no truncated note.
<!-- proven by test CP2-T14 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: An added file that still fits after the real changes is shown whole; only the one that does not fit is cut
- **GIVEN** A step that rewrites src/gen_big.py (about 150K of diff), adds aaa-new-small.js (about 5K) and aab-fixture.txt (1MB); git prints both adds before src/
- **WHEN** collect_diff is called for round 1
- **THEN** The output is header + src/gen_big.py's full chunk + aaa-new-small.js's full chunk (every '+' line) + aab-fixture.txt's chunk cut at the remaining budget + the 'left out or cut short' note naming exactly aab-fixture.txt; aaa-new-small.js is not named; no summary line appears. Fails today (aab-fixture.txt cuts src/gen_big.py off).
<!-- proven by test CP2-T15 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: In a later round the 'changed since the previous round come first' note stays true when a changed file is a huge add
- **GIVEN** Round 1's tree rewrites src/zz_big.py (about 100K of diff); round 2's tree adds aaa-fixture.txt (1MB) and edits src/app.py, so the priority files are aaa-fixture.txt (a pure add) and src/app.py and the rest is src/zz_big.py
- **WHEN** collect_diff is called with prev_round_tree = round 1's tree
- **THEN** src/app.py's full chunk comes first after the header and note; aaa-fixture.txt is never summarized (any part of it shown is a verbatim prefix of its chunk) and is named as cut if cut; every file named as cut or left out is listed in the closing note and nowhere claimed as shown; and the note never says something untrue: if the output carries the wording 'their diffs come first below', then every changed-since file whose chunk appears starts before every other file's chunk, otherwise the note says plainly that a new added file was put after the other changes. The test asserts this invariant, not one particular order (see the open question on where a changed-since add goes).
<!-- proven by test CP2-T16 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: The no-git-trees fallback diff is left exactly as it was
- **GIVEN** The fallback repo: a tracked file rewritten to over 180,000 chars of uncommitted diff against HEAD, plus an untracked 1MB file
- **WHEN** collect_diff(root, None, None) is called
- **THEN** The output equals today's fallback byte for byte: ('# Diff against HEAD\n' + git diff HEAD -- . ':(exclude).ratchet')[:180000] + the generic '[diff truncated at 180000 chars — flag anything you could not see]' note; the untracked 1MB file is not inlined; no summary line.
<!-- proven by test CP2-T17 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: The prompt another company's reviewer actually gets for round 1 shows the real change, one line for the deleted vendor file, and names the cut fixture
- **GIVEN** The end-to-end project: vendor/old.js (1MB) deleted, aaa-fixture.txt (1MB) added, src/a.js changed to carry REAL-CHANGE-7c1e, round 1 opened with one fake codex reviewer
- **WHEN** external_review.py --reviewer <id> --cp CP1 --out <file> --dry-run is run and the written prompt is read
- **THEN** The prompt contains REAL-CHANGE-7c1e; it contains exactly one '[file deleted; 25000 lines of former content omitted]' line, right after vendor/old.js's '+++ /dev/null' line; no 'OLD-VENDOR-' text; no '[file added' line; and a 'left out or cut short' note that names aaa-fixture.txt and not vendor/old.js or src/a.js. Fails today (the slice ends inside the first 1MB file and REAL-CHANGE-7c1e is missing).
<!-- proven by test CP2-T18 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: The existing full-round truncation checks in tests/test_flaky.py still pass with their pure-add filler, unchanged
- **GIVEN** tests/test_flaky.py as committed, whose ~220K filler src/aaa-filler.js is a pure ADD written after plan approval
- **WHEN** This case imports tests/test_flaky.py, routes its module-level check into this file's counter for the duration, and runs t16_prioritizes_files_changed_since_previous_round, t17_names_which_files_were_cut, t18_round1_keeps_plain_order and t19_scoped_small_fix_excludes_the_filler_entirely
- **THEN** Every [CP6-T16], [CP6-T17], [CP6-T18] and [CP6-X10] check inside them passes; and the AST of those four functions plus truncation_project and _truncation_round1_only equals the AST at `git merge-base HEAD main` (ast.dump compared), so the add-last order did not need any locked test or fixture change. If this case fails after the build, the fixer reports which check broke and why before touching tests/test_flaky.py (approve-changes, old->new justification).
<!-- proven by test CP2-T19 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->

#### Scenario: README, the adversarial-loop SKILL.md and collect_diff's docstring describe the full-round rule as the code applies it
- **GIVEN** README.md, skills/ratchet-adversarial-loop/SKILL.md and the loaded module's collect_diff.__doc__ after the build
- **WHEN** Their text is read
- **THEN** Each has one paragraph that says, for a full (unscoped) round over the size limit: a purely deleted file is shown as git's headers plus one line saying how many lines were left out and counts as shown; an added file is never summarized; real changed files are put ahead of added files, and an added file that does not fit is cut and named like any other cut file. None of the three says an added file is summarized or shown in one line in a full round (no paragraph matches both 'add' and 'one line|summar' without a negation like 'never'). Fails today (the docs and docstring say nothing about full-round deletes or add ordering).
<!-- proven by test CP2-T20 in tests/test_review_diff_full_rounds.py, commit 0f7a8b0 -->
