# When I start a new feature in a project where Ratchet ran before, the planner gets a short 'What earlier work says' section from the engine (learnings, open findings saved for later, accepted limits, recorded specs for the parts being changed), so the plan builds on earlier work instead of rediscovering or contradicting it

## Purpose
What this feature does, as built and checked by Ratchet (run when-i-start-a-new-feature-in-a-project-where-ra, 2026-10-11). Each scenario names the test that proves it.

## Requirements

### Requirement: Planner brief says what earlier work learned
The system SHALL do this: `ratchet.py brief --role planner` ends with a short 'What earlier work says' section listing the project's learnings, findings earlier runs saved for later, and limits the user accepted, newest and most relevant first, or a one-line note when there is no earlier work.

- In a project with earlier runs, the planner's brief ends with a 'What earlier work says' section showing recent learnings, findings earlier runs saved for later that were never accepted, and the limits the user accepted, each listed once
- Items that share words with the new feature request come first, then the newest; each section shows only a few one-line items and says how many more there are and which file holds them all
- In a project where Ratchet never ran (no learnings, no earlier runs), the section is one plain line saying there is no earlier work, and the brief still succeeds
- Briefs for every other role look exactly as before
- The planner instructions, the orchestrator skill, README and schemas.md say the planner's brief carries this section and where the full files are

#### Scenario: Planner brief ends with a What earlier work says section when earlier runs exist
- **GIVEN** A project with one earlier (non-active) run holding learnings.md text and at least one deferred finding
- **WHEN** ratchet.py brief --role planner is run
- **THEN** the output ends with a 'What earlier work says' section (after the objective and the planner's role rules) that includes a recent learnings.md line and the open deferred finding's description
<!-- proven by test CP1-T01 in tests/test_digest.py, commit c590721 -->

#### Scenario: Open deferred findings from earlier runs are labelled not fixed
- **GIVEN** An earlier run's state.json has a deferred finding with no accepted flag
- **WHEN** ratchet.py brief --role planner is run
- **THEN** that finding appears in the section worded as saved for later and not marked fixed (not as an accepted limit, and not claimed as resolved)
<!-- proven by test CP1-T02 in tests/test_digest.py, commit c590721 -->

#### Scenario: Accepted findings and accepted-limit learnings both appear as limits the user accepted
- **GIVEN** An earlier run's state.json has a deferred finding with accepted=true, and learnings.md has a separate 'Accepted limit (...)' bullet from a human decision
- **WHEN** ratchet.py brief --role planner is run
- **THEN** both the accepted finding and the accepted-limit bullet show up in the section's accepted-limits part, each listed once
<!-- proven by test CP1-T03 in tests/test_digest.py, commit c590721 -->

#### Scenario: Items sharing words with the new feature come before unrelated newer items
- **GIVEN** Two earlier-run items exist, one older but sharing a distinctive word with the active run's feature text and one newer but unrelated
- **WHEN** ratchet.py brief --role planner is run
- **THEN** the word-matching item is listed before the newer unrelated item in its section
<!-- proven by test CP1-T04 in tests/test_digest.py, commit c590721 -->

#### Scenario: With no relevance match, items fall back to newest first
- **GIVEN** Two earlier-run items in the same section, neither sharing words with the active feature, one from an older run and one from a more recent run
- **WHEN** ratchet.py brief --role planner is run
- **THEN** the newer item is listed first
<!-- proven by test CP1-T05 in tests/test_digest.py, commit c590721 -->

#### Scenario: Each section is capped with a count and a pointer to the full file
- **GIVEN** More learnings bullets, open findings and accepted limits exist than the section's display cap
- **WHEN** ratchet.py brief --role planner is run
- **THEN** each over-full section shows only a few one-line items, then a line naming how many more there are and the path to the file that holds them all (e.g. .ratchet/learnings.md or the run's state.json)
<!-- proven by test CP1-T06 in tests/test_digest.py, commit c590721 -->

#### Scenario: A project with no earlier work gets a one-line note, not an error
- **GIVEN** A freshly initialised project with no learnings.md and no other run folders under .ratchet/runs/
- **WHEN** ratchet.py brief --role planner is run
- **THEN** brief exits 0 and the section is exactly one plain sentence saying there is no earlier work, with no empty sub-headings
<!-- proven by test CP1-T07 in tests/test_digest.py, commit c590721 -->

#### Scenario: Every other role's brief is unchanged
- **GIVEN** The same project used in CP1-T01 (earlier runs and learnings.md present)
- **WHEN** ratchet.py brief --role <role> is run for implementer, fixer, test-writer, reviewer and scout
- **THEN** none of their outputs contain a 'What earlier work says' section or any text from the fabricated earlier-run findings; output matches what brief produced for that role before this feature
<!-- proven by test CP1-T08 in tests/test_digest.py, commit c590721 -->

#### Scenario: A finding accepted via accept-finding is not duplicated between state.json and its own learnings.md marker line
- **GIVEN** An earlier run's state.json has a deferred finding with accepted=true, and learnings.md also has the matching 'Accepted finding <slug>/<cp>/round-<n>/<key> (...)' marker line that accept-finding itself writes for that same finding
- **WHEN** ratchet.py brief --role planner is run
- **THEN** that finding's description appears exactly once in the accepted-limits part of the section, not once from each source
<!-- proven by test CP1-T09 in tests/test_digest.py, commit c590721 -->

#### Scenario: A damaged earlier run's state.json is skipped, not fatal
- **GIVEN** One earlier run folder whose state.json contains invalid JSON, alongside another earlier run folder with valid, readable state.json
- **WHEN** ratchet.py brief --role planner is run
- **THEN** brief exits 0, the section still shows the readable run's items, and the damaged run contributes no crash and no garbled text
<!-- proven by test CP1-T10 in tests/test_digest.py, commit c590721 -->

#### Scenario: An earlier run folder with no state.json at all does not crash the brief
- **GIVEN** A run folder under .ratchet/runs/ that exists but contains no state.json file
- **WHEN** ratchet.py brief --role planner is run
- **THEN** brief exits 0 and the section is produced from the other, readable sources without error
<!-- proven by test CP1-T11 in tests/test_digest.py, commit c590721 -->

#### Scenario: The active run's own findings are not shown back to it as earlier work
- **GIVEN** The active run itself already has an open deferred finding recorded in its own state.json
- **WHEN** ratchet.py brief --role planner is run for that same active run
- **THEN** the section does not list the active run's own finding; it only draws on the other run folders under .ratchet/runs/
<!-- proven by test CP1-T12 in tests/test_digest.py, commit c590721 -->

#### Scenario: brief stays read-only
- **GIVEN** A project with earlier runs and learnings.md as in CP1-T01
- **WHEN** ratchet.py brief --role planner is run twice in a row, with and without a CPn argument
- **THEN** state.json, active.json and learnings.md are byte-for-byte unchanged after both calls, and no lock or new file is left behind
<!-- proven by test CP1-T13 in tests/test_digest.py, commit c590721 -->

#### Scenario: Docs point the planner at the section and the full files
- **GIVEN** The repo's own agents/ratchet-checkpoint-planner.md, skills/ratchet-orchestrator/SKILL.md, README.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** those files are read after the feature is built
- **THEN** each one describes the planner brief's 'What earlier work says' section and says where the full files it summarizes live, and the checkpoint-planner agent no longer only says to read learnings.md first
<!-- proven by test CP1-T14 in tests/test_digest.py, commit c590721 -->

### Requirement: Planner brief lists related recorded specs
The system SHALL do this: The planner's 'What earlier work says' section also names the recorded specs most related to the new feature: OpenSpec specs including Ratchet's own running records, finished Spec Kit features and done BMAD stories, each with its path.

- With specs under openspec/specs/ (including Ratchet's own running records), the planner's brief lists the ones that share words with the new feature request first, each with its title, a one-line purpose and its path
- In a project using Spec Kit, finished features (every task ticked) are listed; in a project using BMAD, stories marked done are listed; open ones are not, since `spec detect` already covers those
- The spec list obeys the same cap: a few entries, then how many more and which folder holds them
- A project with no specs at all shows no spec section, and one with no earlier work of any kind still gets the single plain line
- The planner instructions, README and schemas.md say the brief lists related recorded specs and that the planner still reads the ones it touches in full

#### Scenario: Related OpenSpec specs are listed first, with title, purpose and path
- **GIVEN** two openspec/specs/<cap>/spec.md entries, one whose title and purpose share words with the active feature request and one that does not
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the spec section lists the matching one first, showing its title, a one-line purpose and its path (e.g. openspec/specs/<cap>/spec.md); the unrelated one is listed after it, not omitted
<!-- proven by test CP2-T01 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: Ratchet's own running records are read in their own written shape
- **GIVEN** an openspec/specs/<slug>/spec.md written exactly the way living_spec_text produces it (a '# ' feature line and the 'What this feature does...' line under '## Purpose')
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the entry's title is the '# ' line and its one-line purpose is the first line under '## Purpose', not the raw '## Purpose' heading or a truncated requirement line
<!-- proven by test CP2-T02 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: A finished Spec Kit feature is listed; an unfinished one is not
- **GIVEN** two Spec Kit features under specs/: one whose tasks.md has every task ticked, one with at least one open task
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the finished feature appears in the spec section with its title and path; the unfinished feature's name does not appear anywhere in the spec section
<!-- proven by test CP2-T03 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: A done BMAD story is listed; an open one is not
- **GIVEN** two BMAD v4 stories under docs/stories/: one with '## Status\n\nDone', one with 'Approved' (or 'In Progress')
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the Done story appears in the spec section with its title and path; the Approved/In Progress story's name does not appear anywhere in the spec section
<!-- proven by test CP2-T04 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: The spec list is capped with an 'N more' line and a folder pointer
- **GIVEN** more matching OpenSpec specs than the digest's per-section cap
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** only the first few entries are printed, followed by a line naming how many more there are and the folder (openspec/specs/) that holds the rest; no entry appears twice
<!-- proven by test CP2-T05 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: A project with no specs at all shows no spec section
- **GIVEN** no openspec/specs, no specs/ and no docs/stories directories, but some other earlier-work content exists (e.g. .ratchet/learnings.md has entries)
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the 'What earlier work says' section has no spec sub-section at all (no heading, no empty list, no 'no specs' filler line); the rest of the earlier-work content from CP1 still prints
<!-- proven by test CP2-T06 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: No earlier work of any kind still gets a single plain line, not a crash
- **GIVEN** no specs, no .ratchet/learnings.md, no earlier runs under .ratchet/runs/ other than the active one
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the brief exits 0 and the 'What earlier work says' section is exactly one plain sentence saying there is no earlier work; it does not mention specs separately
<!-- proven by test CP2-T07 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: A malformed spec file does not crash the brief
- **GIVEN** an openspec/specs/<cap>/spec.md that has no '# ' heading at all (e.g. starts straight into prose) alongside one well-formed spec
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the brief still exits 0 and still lists the well-formed spec; the malformed one is skipped rather than shown with a blank or garbled title, and nothing in stderr/stdout looks like a raw traceback
<!-- proven by test CP2-T08 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: Equal relevance ties break newest-first
- **GIVEN** two OpenSpec specs whose titles/purposes share exactly the same number of words with the active feature request, one with an older file mtime and one touched more recently
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** the more recently modified spec is listed before the older one
<!-- proven by test CP2-T09 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: Listing specs takes no lock and writes nothing
- **GIVEN** a project with OpenSpec, Spec Kit and BMAD specs all present
- **WHEN** running `ratchet.py brief --role planner` twice in a row with nothing else changing state.json
- **THEN** both calls succeed, state.json's content and mtime (other than by the brief itself reading, not writing it) are unchanged, and no new file appears under .ratchet/
<!-- proven by test CP2-T10 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: Other roles' briefs do not gain a spec section
- **GIVEN** the same project as CP2-T01, with clearly related OpenSpec specs present
- **WHEN** running `ratchet.py brief --role implementer` and `ratchet.py brief --role fixer`
- **THEN** neither brief prints the spec list or its heading; only `--role planner` does
<!-- proven by test CP2-T11 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: Mixed spec tools are all considered together for relevance and the cap
- **GIVEN** one matching OpenSpec spec, one matching finished Spec Kit feature and one matching done BMAD story, all sharing words with the active feature request, with the per-section cap set low enough that not all three fit
- **WHEN** running `ratchet.py brief --role planner`
- **THEN** entries from all three tools are ranked together by relevance (not one tool automatically before another), and the ones cut by the cap are reflected in the 'N more' count
<!-- proven by test CP2-T12 in tests/test_digest_specs.py, commit b0bf5f3 -->

#### Scenario: Docs describe the spec section and tell the planner to still read specs in full
- **GIVEN** the repo's own agents/ratchet-checkpoint-planner.md, README.md and schemas.md
- **WHEN** reading those files directly in the test (no subprocess)
- **THEN** each mentions that the planner's brief lists related recorded specs with their paths, and ratchet-checkpoint-planner.md keeps telling the planner to read the ones it touches in full and not contradict them without saying so
<!-- proven by test CP2-T13 in tests/test_digest_specs.py, commit b0bf5f3 -->

### Requirement: Spec digest comment states the exact rule
The system SHALL do this: The description of _related_spec_items says a BMAD story counts as done only when its status is exactly 'done', matching what the code already does.

- The docstring of _related_spec_items in ratchet.py no longer says a BMAD story counts as done when its status 'contains' done
- The docstring says the story's status must be exactly 'done' (ignoring case and surrounding spaces), as the code checks
- Nothing else in ratchet.py changes; the brief's output is the same as after CP2
- README.md's test list names tests/test_digest_docstring.py, so tests/test_docs.py still passes

#### Scenario: The spec-digest helper's docstring no longer claims a status that merely contains "done" counts
- **GIVEN** ratchet.py's source as it stands after this checkpoint
- **WHEN** a test reads the docstring of _related_spec_items (by ast or by importing the module, the way tests/test_digest.py does)
- **THEN** the docstring text does not say a BMAD story's status 'contains' or 'containing' the word done (no occurrence of 'contain' describing the BMAD done-status rule)
<!-- proven by test CP3-T01 in tests/test_digest_docstring.py, commit f3db695 -->

#### Scenario: The docstring states the BMAD done rule exactly: status must equal 'done', ignoring case and surrounding spaces
- **GIVEN** the same docstring
- **WHEN** a test scans its text for the BMAD-story sentence
- **THEN** the docstring says the story's status must be exactly 'done' once case and surrounding whitespace are ignored, matching what the code checks at str(status).strip().lower() == 'done' -- wording like '"done" case-insensitively, ignoring surrounding whitespace' or '== "done" after trimming and lowercasing' passes, but a sentence that only says 'contains' or omits the exactness/case/space qualifier fails
<!-- proven by test CP3-T02 in tests/test_digest_docstring.py, commit f3db695 -->

#### Scenario: CP1 and CP2's digest behavior is untouched, and README's test list keeps tests/test_docs.py passing
- **GIVEN** the full repo after this checkpoint's edit
- **WHEN** a test (a) imports/calls _related_spec_items the same way tests/test_digest_specs.py or tests/test_digest.py already do on a fixture with one done BMAD story, one not-done BMAD story (status 'In Progress') and one oddly-cased/spaced done story (status ' DONE '), and (b) reads README.md's reference/test-list section
- **THEN** (a) the done and ' DONE ' stories are still included and the not-done story is still excluded -- the runtime rule is unchanged by the docstring edit; and (b) README.md names tests/test_digest_docstring.py with a runnable 'python3 tests/test_digest_docstring.py' command, in the same place every other tests/test_*.py is listed, so tests/test_docs.py's CP6-T05 check still passes
<!-- proven by test CP3-T03 in tests/test_digest_docstring.py, commit f3db695 -->
