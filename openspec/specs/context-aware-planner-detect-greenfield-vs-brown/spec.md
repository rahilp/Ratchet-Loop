# Context-aware planner: detect greenfield vs brownfield per checkpoint and route to the right planning tools; for brownfield checkpoints add a static impact-analysis pass (re-point sites, registrations, parity gates) so complete stops bouncing on predictable consequences of a code move

## Purpose
What this feature does, as built and checked by Ratchet (run context-aware-planner-detect-greenfield-vs-brown, 2026-10-27). Each scenario names the test that proves it.

## Requirements

### Requirement: Classify a checkpoint greenfield or brownfield
The system SHALL do this: A new script decides, per checkpoint, whether it is greenfield (only new files) or brownfield (touches an existing file), failing safe to brownfield when unsure.

- Given a checkpoint whose files_likely are all paths absent from the repo tree, the classifier reports mode 'greenfield-create'
- Given a checkpoint whose files_likely include at least one path already present in the repo tree, the classifier reports mode 'brownfield-modify'
- Given a mix of new and existing paths, or a repo root the classifier can't read, the classifier reports mode 'brownfield-modify' (fails safe) and says why, never silently picking greenfield
- The classifier is callable as a CLI command the checkpoint-planner agent can invoke via Bash during planning, and prints its verdict plus the paths it checked as JSON

#### Scenario: All-new files classify as greenfield-create
- **GIVEN** A checkpoint's files_likely list names only paths that do not exist anywhere under the given repo root (e.g. skills/ratchet-checkpoint-planner/scripts/impact_analysis.py and tests/test_impact_analysis_mode.py, both absent from a fresh temp repo root)
- **WHEN** The classifier CLI is run against that repo root with that files_likely list
- **THEN** It exits 0 and its JSON output reports mode 'greenfield-create', carrying no re-point/impact items or brownfield reason -- only the mode and the exact paths it checked
<!-- proven by test CP1-T01 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: All-existing files classify as brownfield-modify
- **GIVEN** A files_likely list naming only paths that already exist under the repo root (e.g. two files created ahead of time in the temp root)
- **WHEN** The classifier CLI is run against that repo root with that files_likely list
- **THEN** It exits 0 and reports mode 'brownfield-modify'
<!-- proven by test CP1-T02 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: Mixed new and existing files fail safe to brownfield-modify with a reason
- **GIVEN** A files_likely list with one path that already exists in the repo root and one path that does not
- **WHEN** The classifier CLI is run against that repo root with the mixed list
- **THEN** It exits 0, reports mode 'brownfield-modify', and the JSON includes a non-empty reason naming the specific existing path that triggered it -- it never reports 'greenfield-create' for a mixed list
<!-- proven by test CP1-T03 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: An unreadable or missing repo root fails safe to brownfield-modify
- **GIVEN** A repo root path that does not exist on disk (or one the process has no read permission on), with an otherwise all-new files_likely list
- **WHEN** The classifier CLI is run with that broken repo root
- **THEN** It still exits 0 (no uncaught crash) and reports mode 'brownfield-modify' with a reason naming that the repo root could not be read -- it never reports 'greenfield-create' just because it could not check
<!-- proven by test CP1-T04 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: Verdict JSON names its mode and lists every path it checked
- **GIVEN** A files_likely list of three paths (two existing, one new) under a temp repo root
- **WHEN** The classifier CLI is run against that repo root
- **THEN** stdout is valid, parseable JSON with a mode field and a list of the paths checked that is exactly the files_likely list given (same paths, none added or dropped), so the calling planner agent can show the user exactly what was examined
<!-- proven by test CP1-T05 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: A new file's existing parent directory doesn't make it brownfield
- **GIVEN** A files_likely path that does not itself exist yet but whose parent directory already exists in the repo root (e.g. a new file inside an existing tests/ directory), with every other files_likely path also new
- **WHEN** The classifier CLI is run against that repo root
- **THEN** It reports mode 'greenfield-create' -- the parent directory already existing is never mistaken for the file itself existing
<!-- proven by test CP1-T06 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: An empty files_likely list fails safe to brownfield-modify rather than defaulting to greenfield
- **GIVEN** A checkpoint with an empty files_likely list (nothing to check)
- **WHEN** The classifier CLI is run with that empty list
- **THEN** It reports mode 'brownfield-modify' with a reason that there was nothing to classify -- it never defaults to 'greenfield-create' just because no existing path was found
<!-- proven by test CP1-T07 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: The classifier runs as a standalone CLI command, not only as an importable function
- **GIVEN** The same all-new-paths scenario as CP1-T01
- **WHEN** The classifier is invoked as a separate OS subprocess via its command line (argv/stdin), the same way the checkpoint-planner agent would invoke it with the Bash tool -- never by importing it into the test process
- **THEN** The subprocess exits 0 and its stdout is the JSON verdict, proving the planner agent really can call it as a plain CLI command during planning
<!-- proven by test CP1-T08 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

#### Scenario: Classifying against this project's own real repo root also fails safe on a mixed list
- **GIVEN** This repository's own real root (not a synthetic temp fixture) and a files_likely list naming one real existing file (README.md) and one path that has never existed in it
- **WHEN** The classifier CLI is run against the real project root with that list
- **THEN** It reports mode 'brownfield-modify' with a reason, matching the synthetic-fixture behavior proven in CP1-T03 -- the classifier's real-world use by the planner agent behaves the same as the fixtures predict
<!-- proven by test CP1-T09 in tests/test_impact_analysis_mode.py, commit 61e26b5 -->

### Requirement: Find every re-point site for a moved symbol
The system SHALL do this: Given a symbol moving from one module to another, the pass lists every exact file+line that monkeypatches it by name, scans the old module's source for it, or imports it -- including a deliberately hidden one.

- On the foo-moves-from-A-to-B fixture, the scan lists the test that does `A.foo = ...` / `setattr(A, "foo", ...)` / `patch("pkg.A.foo")` / `patch.object(A, "foo")`
- The scan also lists the fixture's source-inspection check that AST-scans module A's text for the literal name 'foo', even though that check's own name and comments don't mention the move
- The scan finds a deliberately obscure hidden site (a patch wrapped in a helper function one call removed from the obvious test body) that a naive grep-only pass would miss
- The scan lists every importer of A.foo
- A file the scan can't parse (non-Python, or a syntax error) is reported as 'manual check needed', never silently dropped from the list
- Each finding carries the exact file path and line number, so the builder's re-pointing is mechanical, not guesswork

#### Scenario: Finds the plain attribute-assignment monkeypatch site
- **GIVEN** test_moved_symbol.py has a test that does `A.foo = Mock()` where A is pkg.module_a
- **WHEN** the repoint scan is run against the fixture root with module A = pkg.module_a, module B = pkg.module_b, symbol = foo
- **THEN** the findings list includes a 'monkeypatch' entry for test_moved_symbol.py at the exact line of the `A.foo = ...` statement
<!-- proven by test CP2-T01 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Finds the setattr(A, "foo", ...) monkeypatch site
- **GIVEN** another test function in test_moved_symbol.py does `setattr(A, "foo", Mock())`
- **WHEN** the repoint scan runs
- **THEN** the findings list includes a 'monkeypatch' entry for that exact file and line, distinct from the plain-assignment finding
<!-- proven by test CP2-T02 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Finds the string-target patch("pkg.module_a.foo") site
- **GIVEN** another test function does `with patch("pkg.module_a.foo") as m:`
- **WHEN** the repoint scan runs
- **THEN** the findings list includes a 'monkeypatch' entry naming the exact file and line of that patch() call
<!-- proven by test CP2-T03 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Finds the patch.object(A, "foo") site
- **GIVEN** another test function does `with patch.object(A, "foo") as m:`
- **WHEN** the repoint scan runs
- **THEN** the findings list includes a 'monkeypatch' entry naming the exact file and line of that patch.object() call
<!-- proven by test CP2-T04 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Finds the disguised source-inspection scan of module A's text
- **GIVEN** test_source_scan.py has a check, named and commented with no reference to 'foo' or the move, whose body does an AST/text scan of module_a.py's source for the literal name 'foo'
- **WHEN** the repoint scan runs
- **THEN** the findings list includes a 'source-inspection' entry naming test_source_scan.py and the exact line that reads/scans module_a.py's text, even though nothing in that function's name or comments hints at it
<!-- proven by test CP2-T05 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Finds the deliberately hidden patch wrapped one call away
- **GIVEN** test_hidden_patch.py's test body only calls `_apply_legacy_shim()`, and the real `patch.object(A, "foo")` call lives inside that helper in helpers.py, one call removed from the obvious test body
- **WHEN** the repoint scan runs
- **THEN** the findings list still includes a 'monkeypatch' entry for the exact file (helpers.py) and line of the real patch.object() call inside the helper -- a naive scan that only inspects test bodies directly would miss this, so this case must fail against a stub that greps only top-level test functions
<!-- proven by test CP2-T06 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Lists every importer of A.foo, by direct symbol import and by module-attribute access
- **GIVEN** importer_direct.py does `from pkg.module_a import foo` and importer_attr.py does `import pkg.module_a` then later reads `pkg.module_a.foo(...)`
- **WHEN** the repoint scan runs
- **THEN** the findings list includes an 'importer' entry for importer_direct.py at its import line and a separate 'importer' entry for importer_attr.py at the line of its `.foo` attribute access
<!-- proven by test CP2-T07 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: A non-Python file mentioning foo is flagged manual-check-needed, never dropped
- **GIVEN** notes.txt mentions 'foo' in plain prose and is not a Python file
- **WHEN** the repoint scan runs
- **THEN** the findings list includes an entry for notes.txt marked 'manual check needed' (not a 'monkeypatch'/'source-inspection'/'importer' finding, and not silently absent from the output)
<!-- proven by test CP2-T08 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: A Python file with a syntax error is flagged manual-check-needed and does not abort the rest of the scan
- **GIVEN** broken.py contains a deliberate syntax error
- **WHEN** the repoint scan runs over the whole fixture tree, including broken.py alongside every other fixture file
- **THEN** broken.py is reported as 'manual check needed', the scan still completes, and every other file's findings (monkeypatch, source-inspection, hidden, importer) are still present in the same run's output
<!-- proven by test CP2-T09 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Every finding carries an exact file path and line number, not a vague description
- **GIVEN** the full fixture tree is scanned and produces findings across all categories (monkeypatch, source-inspection, importer, manual-check-needed)
- **WHEN** the findings list is inspected
- **THEN** every single finding object has a non-empty exact file path and an integer line number pointing at the real statement (never a directory, a module name alone, or a generic sentence like 're-point source-inspection checks' with no file/line)
<!-- proven by test CP2-T10 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: A substring match on 'foo' inside another identifier is never flagged
- **GIVEN** decoy_substring.py only contains identifiers like `foobar` and `barfoo`, never the standalone symbol `foo`
- **WHEN** the repoint scan runs
- **THEN** no finding in the output names decoy_substring.py -- a naive grep for the substring 'foo' must not produce a false re-point item here
<!-- proven by test CP2-T11 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

#### Scenario: Importing module A without touching .foo is not listed as an importer of foo
- **GIVEN** decoy_import_only.py does `import pkg.module_a` but never reads, assigns, or patches `.foo` anywhere in the file
- **WHEN** the repoint scan runs
- **THEN** no 'importer' finding is emitted for decoy_import_only.py -- importing the module alone is not the same as referencing the symbol foo
<!-- proven by test CP2-T12 in tests/test_impact_analysis_repoint.py, tests/fixtures/impact_analysis_foo_move/, commit 235e240 -->

### Requirement: Flag new test files needing registration
The system SHALL do this: A checkpoint that adds a new test file gets explicit done_when items telling the builder to add it to the README test list, and to the source-scan inventory if it inspects source.

- Given a new test file path not yet present in README's 'Running the tests' list, the pass emits a done_when item naming the exact line to add ('python3 tests/test_x.py')
- Given a new test file that itself does an AST/source scan of project files (the CP5-X78-shaped case), the pass also emits an item to register it wherever the project's own test-inventory fixture enumerates such scans
- Given a test file already correctly listed in both places, the pass emits nothing for it (no duplicate or false-positive item)

#### Scenario: Plain new test file not yet listed in the README gets a README registration item
- **GIVEN** a new plain test file tests/test_widget_thing.py (no source scan) and a fixture README list that does not mention it
- **WHEN** the registration check runs against the test file, the fixture README list, and the fixture inventory
- **THEN** it emits exactly one item naming the exact line to add, 'python3 tests/test_widget_thing.py', and emits no inventory item
<!-- proven by test CP3-T01 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

#### Scenario: Unregistered source-scanning test file gets both a README item and an inventory item
- **GIVEN** a new test file tests/test_scan_thing.py whose body itself AST/source-scans another project file (the CP5-X78 shape), absent from both the fixture README list and the fixture inventory
- **WHEN** the registration check runs
- **THEN** it emits two items: the README line to add ('python3 tests/test_scan_thing.py') and an item to register the file in the fixture test-inventory of source-scanning suites
<!-- proven by test CP3-T02 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

#### Scenario: Already fully registered test file produces no items
- **GIVEN** a source-scanning test file tests/test_known_thing.py already listed with its exact line in the fixture README list and already listed in the fixture inventory
- **WHEN** the registration check runs
- **THEN** no items are emitted for it at all: no duplicate README item, no duplicate inventory item, no false positive
<!-- proven by test CP3-T03 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

#### Scenario: Source-scanning file registered in README but missing from inventory gets only the inventory item
- **GIVEN** a source-scanning test file tests/test_half_registered.py already listed with its exact line in the fixture README list but absent from the fixture inventory
- **WHEN** the registration check runs
- **THEN** only the inventory-registration item is emitted; no README item (it is already correctly listed) and no duplicate of it
<!-- proven by test CP3-T04 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

#### Scenario: Source-scanning file registered in the inventory but missing from README gets only the README item
- **GIVEN** a source-scanning test file tests/test_other_half.py already listed in the fixture inventory but absent from the fixture README list
- **WHEN** the registration check runs
- **THEN** only the README item is emitted, naming the exact line to add; no inventory item is emitted
<!-- proven by test CP3-T05 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

#### Scenario: A near-miss README line for the same filename is never mistaken for registration
- **GIVEN** a plain test file tests/test_mismatch.py and a fixture README list containing a line that mentions the same filename but in the wrong exact form (missing the 'python3 ' prefix, or using 'pytest' instead of 'python3')
- **WHEN** the registration check runs
- **THEN** the check still emits the README item with the exact required line, because no fixture line matches it exactly; a filename substring match never counts as already registered
<!-- proven by test CP3-T06 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

#### Scenario: A test file that can't be parsed is flagged for manual check rather than silently assumed non-scanning
- **GIVEN** a new test file tests/test_broken_syntax.py containing a Python syntax error, absent from the fixture README list
- **WHEN** the registration check runs
- **THEN** it still emits the README registration item (the filename is known regardless of parsing), and separately reports 'manual check needed' for whether the file also needs inventory registration, instead of silently deciding it does not scan source
<!-- proven by test CP3-T07 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

#### Scenario: Running the check twice on the same unregistered file is idempotent, not accumulating
- **GIVEN** the same unregistered source-scanning test file and the same unchanged fixture README list and fixture inventory
- **WHEN** the registration check runs a second time right after the first, with no fixture changed in between
- **THEN** the second run's emitted items are identical to the first run's (the same two items, never duplicated, tripled, or reworded)
<!-- proven by test CP3-T08 in tests/test_impact_analysis_registration.py, commit 90cc781 -->

### Requirement: Attach parity gates and flag report-race hazards
The system SHALL do this: Every extraction checkpoint automatically gets a startup-parity and behavior-parity gate item; a test command combining two report-writing suites is flagged before it can bounce at complete.

- Given a checkpoint classified as an extraction (moves code out of one module into another), the pass always emits a standing startup-parity item (module import/startup cost within a set % of the CP1 baseline) and a behavior byte-identical snapshot item, even when the checkpoint's own done_when forgot to ask for them
- Given a checkpoint that is not an extraction, no parity item is emitted
- Given a planned tests.command that runs two or more suites that would each write to the same structured report path, the pass raises an explicit hazard flag naming the suites, instead of staying silent
- Given a tests.command that runs one suite, or several suites through the project's own fixed multi-suite runner, no hazard flag is raised

#### Scenario: An extraction checkpoint always gets both parity items, even though its own done_when never asked for them
- **GIVEN** a checkpoint classified as moving `foo` from module_a.py to module_b.py, whose done_when list says nothing about startup cost or a byte-identical snapshot
- **WHEN** the parity pass runs on it
- **THEN** its output includes a standing startup-parity item (comparing module import/startup cost to the CP1 baseline within a stated threshold) and a separate behavior byte-identical snapshot item
<!-- proven by test CP4-T01 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: A non-extraction brownfield checkpoint gets no parity item
- **GIVEN** a checkpoint that edits an existing function's body in place, with no symbol moving from one module into another
- **WHEN** the parity pass runs on it
- **THEN** its output contains zero parity items
<!-- proven by test CP4-T02 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: A greenfield checkpoint gets no parity item either
- **GIVEN** a checkpoint whose files_likely are all brand-new paths (greenfield-create)
- **WHEN** the parity pass runs on it
- **THEN** its output contains zero parity items, the same as any other non-extraction checkpoint
<!-- proven by test CP4-T03 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: Renaming a symbol within the same module is not an extraction
- **GIVEN** a checkpoint description where `foo` is renamed/moved within module_a.py itself, never leaving that module
- **WHEN** the parity pass runs on it
- **THEN** its output contains zero parity items, because nothing moved out of one module into another
<!-- proven by test CP4-T04 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: An extraction checkpoint that already lists its own startup-parity line does not get a duplicate
- **GIVEN** the foo-moves-A-to-B checkpoint, but its done_when already contains one line asking for a startup-parity check
- **WHEN** the parity pass runs on it
- **THEN** the output still contains exactly one startup-parity item (not two) plus the behavior byte-identical snapshot item, so nothing is silently dropped and nothing is duplicated
<!-- proven by test CP4-T05 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: The two parity items stay distinct, not merged into one vague line
- **GIVEN** the foo-moves-A-to-B extraction checkpoint
- **WHEN** the parity pass runs on it
- **THEN** the startup-parity item and the behavior byte-identical-snapshot item are two separately identifiable items in the output, each naming what it checks (import/startup cost vs byte-identical behavior), not one combined sentence
<!-- proven by test CP4-T06 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: A tests.command combining two report-writing suites is flagged, naming both
- **GIVEN** a planned tests.command string 'python3 tests/test_fixture_a.py && python3 tests/test_fixture_b.py', where both scripts are the kind that write to RATCHET_TEST_REPORT
- **WHEN** the hazard check runs on that tests.command
- **THEN** it raises an explicit hazard flag that names both tests/test_fixture_a.py and tests/test_fixture_b.py
<!-- proven by test CP4-T07 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: A tests.command combining three report-writing suites names all three
- **GIVEN** a planned tests.command string chaining tests/test_fixture_a.py, tests/test_fixture_b.py and tests/test_fixture_c.py with ';'
- **WHEN** the hazard check runs on that tests.command
- **THEN** it raises one hazard flag naming all three suites, not just the first pair found
<!-- proven by test CP4-T08 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: A tests.command running one suite alone raises no hazard
- **GIVEN** a planned tests.command string 'python3 tests/test_fixture_a.py'
- **WHEN** the hazard check runs on that tests.command
- **THEN** no hazard flag is raised
<!-- proven by test CP4-T09 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: Several suites run through the project's fixed multi-suite runner raise no hazard
- **GIVEN** a planned tests.command string 'python3 tests/run_all.py', the project's own fixed multi-suite runner that already runs many suites into one report safely
- **WHEN** the hazard check runs on that tests.command
- **THEN** no hazard flag is raised, even though run_all.py itself runs many suites
<!-- proven by test CP4-T10 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: Mixing the fixed runner with one extra standalone report-writing suite still raises a hazard
- **GIVEN** a planned tests.command string 'python3 tests/run_all.py && python3 tests/test_fixture_c.py', pairing the safe runner with one more suite outside it that also writes its own report
- **WHEN** the hazard check runs on that tests.command
- **THEN** a hazard flag is raised naming tests/run_all.py and tests/test_fixture_c.py as the colliding pair, since the runner alone does not exempt a report-writer added beside it
<!-- proven by test CP4-T11 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: An empty or suite-less tests.command raises no hazard and does not crash
- **GIVEN** a planned tests.command that is an empty string, or one with no recognizable suite invocation at all
- **WHEN** the hazard check runs on that tests.command
- **THEN** it returns cleanly with no hazard flag, never raising an exception
<!-- proven by test CP4-T12 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

#### Scenario: A single suite wrapped in a harmless shell prefix still reads as one suite, not two
- **GIVEN** a planned tests.command string 'cd tests && python3 test_fixture_a.py'
- **WHEN** the hazard check runs on that tests.command
- **THEN** no hazard flag is raised, because only one report-writing suite is actually being run
<!-- proven by test CP4-T13 in tests/test_impact_analysis_parity_hazard.py, commit aa15f38 -->

### Requirement: Guarantee the pass can only add, never weaken
The system SHALL do this: The merge that folds CP2-CP4's enumerated items into a checkpoint's done_when and planned test cases is proven, by a dedicated test, to be able to add lines but never remove, reorder-away, or reword an already-approved one.

- Given an approved checkpoint's existing done_when list and planned test cases, running the merge with CP2-CP4's enumerated items adds new lines/cases and leaves every existing line and case byte-for-byte unchanged
- A mutation test that tries to make the merge drop an existing done_when line, reorder it away, or reword it fails loudly (the merge function raises/refuses) rather than silently succeeding
- The merge never introduces a duplicate of an item already present (idempotent re-run on the same inputs adds nothing new the second time)
- When the pass finds nothing to add for a checkpoint, the merge is a true no-op (output identical to input)

#### Scenario: Merging adds the new re-point line and new test case without touching what was already there
- **GIVEN** A checkpoint with 3 existing done_when lines and 2 existing planned test cases, plus an enumerated_items payload from CP2-CP4 containing 1 new done_when line and 1 new case dict
- **WHEN** The merge runs
- **THEN** The result has 4 done_when lines and 3 cases; the new line and new case are present, after the existing ones
<!-- proven by test CP5-T01 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: Every pre-existing done_when line and test case comes out byte-for-byte identical
- **GIVEN** The same checkpoint as CP5-T01, with 2 new items to add
- **WHEN** The merge runs
- **THEN** Each of the 3 original done_when strings is '==' identical character-for-character at its original index, and each of the 2 original case dicts has every field (id, given, when, then, layer, edge) identical to the input, not just a matching id
<!-- proven by test CP5-T02 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: The merge refuses to reword an already-approved done_when line
- **GIVEN** A checkpoint whose done_when[1] is an exact existing string, and an enumerated_items payload engineered to supply a slightly reworded replacement text for that same line/position
- **WHEN** The merge runs with that payload
- **THEN** The call raises/refuses (an exception, not a return value) naming the offending line; no output is produced where that line's wording changed
<!-- proven by test CP5-T03 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: The merge refuses to drop an already-approved done_when line
- **GIVEN** A checkpoint with 3 existing done_when lines and an enumerated_items/merge-call variant engineered so the naive result would contain only 2 of them
- **WHEN** The merge runs with that payload
- **THEN** The call raises/refuses rather than silently returning a shorter list; a direct check also confirms the merge never returns fewer existing-line matches than the input had
<!-- proven by test CP5-T04 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: The merge refuses to reorder an existing done_when line away from its position
- **GIVEN** A checkpoint with 3 existing done_when lines in a fixed order, and an enumerated_items/merge-call variant engineered to move the 3rd line ahead of the 1st while keeping all text intact
- **WHEN** The merge runs with that payload
- **THEN** The call raises/refuses; the existing lines' original relative order is preserved by every call that does not raise, with new items only ever appended after them
<!-- proven by test CP5-T05 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: Re-running the merge with the same new items twice adds nothing the second time
- **GIVEN** A checkpoint and an enumerated_items payload with 2 new items
- **WHEN** The merge runs once producing merged_once, then runs again on merged_once with the same enumerated_items payload
- **THEN** merged_twice is exactly equal to merged_once (same done_when list and same cases list, same length, no duplicate lines or case ids introduced)
<!-- proven by test CP5-T06 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: When there is nothing to add, the merge is a true no-op
- **GIVEN** A checkpoint with existing done_when lines and cases, and an empty enumerated_items payload (CP2-CP4's pass found nothing new for this checkpoint)
- **WHEN** The merge runs
- **THEN** The output is identical to the input: the same done_when list and the same cases list, in the same order, with nothing added, removed or changed
<!-- proven by test CP5-T07 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: An enumerated item that duplicates an existing line verbatim is not added again
- **GIVEN** A checkpoint that already has a done_when line whose text is identical to one of CP2-CP4's enumerated items, alongside one genuinely new enumerated item
- **WHEN** The merge runs with both items in the enumerated_items payload
- **THEN** The output adds exactly 1 new line (the genuinely new one); the verbatim duplicate is not appended a second time
<!-- proven by test CP5-T08 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

#### Scenario: A new enumerated test case that reuses an existing case's id is refused, never silently overwritten or renumbered
- **GIVEN** A checkpoint with existing planned cases CPx-T01 and CPx-T02, and an enumerated_items payload containing one case dict with a fresh id (CPx-T03) and one case dict whose id collides with CPx-T01 but has different given/when/then content
- **WHEN** The merge runs with that payload
- **THEN** The fresh-id case (CPx-T03) is appended, but the merge raises/refuses on the id-colliding case rather than overwriting CPx-T01's content or silently renumbering it
<!-- proven by test CP5-T09 in tests/test_impact_analysis_additive.py, commit ecb65e1 -->

### Requirement: Wire mode detection into the planner agents
The system SHALL do this: agents/ratchet-checkpoint-planner.md (and the TDD planner skill) now instruct the planner to classify each checkpoint, record the mode on it, and invoke impact_analysis.py for brownfield/ambiguous checkpoints before writing checkpoints.json.

- agents/ratchet-checkpoint-planner.md tells the planner to classify every checkpoint as greenfield-create or brownfield-modify (fail-safe to brownfield-modify when mixed or unsure) and to record that as a 'lane_mode' (or 'mode') field on the checkpoint, never silently
- The same file tells the planner, for a brownfield/ambiguous checkpoint, to run skills/ratchet-checkpoint-planner/scripts/impact_analysis.py via Bash and fold its enumerated re-point/registration/parity/hazard items into that checkpoint's done_when and notes, additively only
- A greenfield checkpoint's instructions stay exactly the existing forward/spec-first planning method, with an explicit note that the impact pass is skipped for it
- skills/ratchet-tdd-planner/SKILL.md gains a short note that a brownfield checkpoint's re-point items may need their own test-edit cases, so the test planner doesn't have to rediscover that from scratch
- README.md's 'Running the tests' area, or wherever this feature's own new test files are registered, lists every test file this feature's CP1-CP5 added

#### Scenario: Planner doc tells the agent to classify every checkpoint greenfield or brownfield
- **GIVEN** agents/ratchet-checkpoint-planner.md's planning instructions
- **WHEN** a test reads the file's text
- **THEN** it states, for every checkpoint, a required classification into exactly one of the two mode strings 'greenfield-create' or 'brownfield-modify' (both strings present, tied to the classify instruction, not just mentioned elsewhere)
<!-- proven by test CP6-T01 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: Planner doc requires the mode to be recorded explicitly, never left implicit
- **GIVEN** the same file's classify instruction
- **WHEN** a test reads the surrounding sentences
- **THEN** it names the field to write ('lane_mode' or 'mode') on the checkpoint, and says plainly that this must always be recorded (an explicit 'never silently'/'always record' style instruction), not left for the mode to be inferred later without being written down
<!-- proven by test CP6-T02 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: Fail-safe default to brownfield covers both a mixed checkpoint and an unsure one
- **GIVEN** the classify instruction's fail-safe clause
- **WHEN** a test checks the wording for both trigger words
- **THEN** it names BOTH a checkpoint that mixes new and existing files AND one the agent is simply unsure about, and ties each to the same default: 'brownfield-modify' -- a doc that only covers one of the two trigger phrases fails this case
<!-- proven by test CP6-T03 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: Planner doc tells the agent to run impact_analysis.py via Bash for brownfield/ambiguous checkpoints
- **GIVEN** a checkpoint classified brownfield-modify (or ambiguous)
- **WHEN** a test reads the instruction for that path
- **THEN** it names the exact script path 'skills/ratchet-checkpoint-planner/scripts/impact_analysis.py', says to invoke it via Bash, and says this happens before checkpoints.json is written
<!-- proven by test CP6-T04 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: Planner doc tells the agent to fold the script's enumerated items into done_when and notes
- **GIVEN** impact_analysis.py's output for a brownfield checkpoint
- **WHEN** a test reads the fold instruction
- **THEN** it says the enumerated re-point, registration, parity and hazard items are folded into that checkpoint's 'done_when' and 'notes' fields (both named)
<!-- proven by test CP6-T05 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: The fold instruction is explicitly additive-only
- **GIVEN** the same fold instruction
- **WHEN** a test checks the sentence for a non-destructive constraint
- **THEN** it states the fold only ADDS items and never removes, reorders away, or rewords an existing done_when line or planned test case (an 'additively only' claim with no matching 'never remove/reword' guard fails this case)
<!-- proven by test CP6-T06 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: A greenfield checkpoint keeps the existing forward/spec-first planning method unchanged
- **GIVEN** agents/ratchet-checkpoint-planner.md's 'How to slice (PLAN mode)' and 'Starting from a spec' sections, snapshotted before CP6
- **WHEN** a test re-reads the file after CP6's edit
- **THEN** the pre-existing sentences of those sections are still present byte-for-byte (CP6 only added prose, it did not rewrite the existing greenfield method)
<!-- proven by test CP6-T07 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: The greenfield path has an explicit note that the impact pass is skipped
- **GIVEN** the greenfield-create instructions
- **WHEN** a test reads the text right after/near them
- **THEN** it contains an explicit sentence saying the impact-analysis pass is skipped for a greenfield checkpoint
<!-- proven by test CP6-T08 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: The skip note is scoped to greenfield only, not written into the brownfield instructions too
- **GIVEN** both the greenfield-create and brownfield-modify instruction blocks
- **WHEN** a test checks which block contains the 'impact pass is skipped' sentence
- **THEN** the skip sentence appears only in the greenfield-create block; the brownfield-modify block never tells the agent the impact pass can be skipped
<!-- proven by test CP6-T09 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: The TDD planner skill notes that brownfield re-point items may need their own test-edit cases
- **GIVEN** skills/ratchet-tdd-planner/SKILL.md
- **WHEN** a test reads the file's text
- **THEN** it contains a short note saying a brownfield checkpoint's re-point items (from impact_analysis.py) may need their own test-edit cases, so the test planner doesn't have to rediscover that from scratch
<!-- proven by test CP6-T10 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: README's test list names every CP1-CP5 test file this feature added
- **GIVEN** README.md's 'Running the tests' section
- **WHEN** a test reads the fenced command block under that heading
- **THEN** it contains a 'python3 tests/test_X.py' line for each of: test_impact_analysis_mode.py, test_impact_analysis_repoint.py, test_impact_analysis_registration.py, test_impact_analysis_parity_hazard.py, test_impact_analysis_additive.py -- all five, none missing
<!-- proven by test CP6-T11 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

#### Scenario: Those five lines are really registered in the runnable block, not just mentioned in prose
- **GIVEN** the same README section
- **WHEN** a test locates the fenced code block that lists every other 'python3 tests/test_*.py' suite
- **THEN** each of the five new lines sits inside that same fenced block (the one `python3 tests/run_all.py` and `tests/_tmp.py` already run from), not only named in a paragraph elsewhere in the file
<!-- proven by test CP6-T12 in tests/test_impact_analysis_docs.py, commit 26cfa7c -->

### Requirement: Prove the full pass on a replay
The system SHALL do this: Replaying an extraction shaped like lane M's CP3-CP5 through the full pass surfaces the B1 patch-site, the CP5-X78 stale source-scan, the README/inventory registration and the startup-parity gate before any code is written; a matching greenfield fixture gets a clean plan with no spurious re-point items.

- On a fixture shaped like lane M's extraction (a helper moving out of one module into a new sibling module, with a monkeypatch-by-name test and a source-inspection scan of the old module, a new test file, and no standing parity gate), the pass's combined output lists all four classes of item: the re-point site, the stale source-scan site, the README+inventory registration, and the startup+behavior parity gates -- all before any implementation code for that fixture exists
- On a fixture that only adds brand-new files/symbols with no existing reference anywhere in the tree, the pass's combined output contains zero re-point items and classifies the checkpoint greenfield-create
- The end-to-end entry point is the single command the checkpoint-planner agent runs per brownfield/ambiguous checkpoint (CP1's classifier, CP2's scan, CP3's registration check, CP4's parity/hazard check and CP5's additive merge, composed)

#### Scenario: One command runs the whole pass on the M-shaped fixture
- **GIVEN** the impact_analysis_m_shaped fixture tree and its checkpoint JSON
- **WHEN** the checkpoint-planner's single composed CLI command is invoked once, pointed at that fixture tree and checkpoint JSON
- **THEN** it exits 0 and prints one combined JSON result containing mode, re-point findings, registration items, parity items and hazard findings in the same invocation, with no second command required
<!-- proven by test CP7-T01 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: M-shaped fixture is classified brownfield, not greenfield
- **GIVEN** the impact_analysis_m_shaped fixture (new module_b.py plus a reference back to module_a's existing symbol)
- **WHEN** the composed command runs
- **THEN** the combined result's mode is brownfield-modify, proving the mixed new+existing fixture fails safe rather than being classified greenfield
<!-- proven by test CP7-T02 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: The hidden monkeypatch re-point site is in the combined output
- **GIVEN** the impact_analysis_m_shaped fixture's test_moved_helper.py, which monkeypatches module_a._load_state by name
- **WHEN** the composed command runs
- **THEN** the combined result's re-point items include that exact monkeypatch site with test_moved_helper.py's file path and line number
<!-- proven by test CP7-T03 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: The stale source-scan site is found even though its name hides the move
- **GIVEN** test_moved_helper.py's source-inspection check, whose own name and comments never mention module_a or the move
- **WHEN** the composed command runs
- **THEN** the combined result's re-point items include that check's file+line as a source-inspection site for `_load_state`
<!-- proven by test CP7-T04 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: README and inventory registration items appear for the new test file
- **GIVEN** test_moved_helper.py is not yet listed in either fixture copy of the README test list or the source-scan inventory
- **WHEN** the composed command runs
- **THEN** the combined result's registration items include one item naming the README line to add and one naming the inventory-fixture entry to add, both for test_moved_helper.py
<!-- proven by test CP7-T05 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: Startup-parity and behavior-parity gate items are added even though the fixture checkpoint never asked for them
- **GIVEN** the impact_analysis_m_shaped fixture checkpoint's done_when has no parity wording at all
- **WHEN** the composed command runs
- **THEN** the combined result's parity items include both a standing startup-parity item and a behavior byte-identical-snapshot item for this checkpoint
<!-- proven by test CP7-T06 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: All four item classes come back before any fixture implementation code exists
- **GIVEN** module_b.py in the fixture does not yet define `_load_state` (the move has not actually been made)
- **WHEN** the composed command runs against the fixture tree exactly as checked in
- **THEN** the command still succeeds and the combined result contains at least one item of each of the four classes (re-point, stale source-scan, registration, parity), none of them blocked on the symbol already having moved
<!-- proven by test CP7-T07 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: A single-suite tests.command is never flagged as a report-race hazard
- **GIVEN** the fixture checkpoint's tests.command runs only test_moved_helper.py
- **WHEN** the composed command runs
- **THEN** the combined result's hazard findings are empty for this checkpoint
<!-- proven by test CP7-T08 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: Greenfield fixture is classified greenfield-create
- **GIVEN** the impact_analysis_greenfield fixture, whose files_likely are all absent from the fixture tree
- **WHEN** the composed command runs
- **THEN** the combined result's mode is greenfield-create
<!-- proven by test CP7-T09 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: Greenfield fixture gets zero re-point items
- **GIVEN** the impact_analysis_greenfield fixture
- **WHEN** the composed command runs
- **THEN** the combined result's re-point items list is empty
<!-- proven by test CP7-T10 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: Greenfield fixture's plan stays clean of parity and hazard noise too
- **GIVEN** the impact_analysis_greenfield fixture, which is not an extraction and has no multi-suite tests.command
- **WHEN** the composed command runs
- **THEN** the combined result's parity items and hazard findings are both empty, so nothing spurious is attached to a plain new-file checkpoint
<!-- proven by test CP7-T11 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: The composed pass only adds to the M-shaped fixture's existing checkpoint JSON
- **GIVEN** the impact_analysis_m_shaped fixture checkpoint's existing done_when lines and notes, captured before the command runs
- **WHEN** the composed command's output is merged into the checkpoint JSON
- **THEN** every pre-existing done_when line and note is present byte-for-byte unchanged, and only new lines are appended
<!-- proven by test CP7-T12 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->

#### Scenario: Running the composed command twice on the same fixture adds nothing new the second time
- **GIVEN** the impact_analysis_m_shaped fixture and its checkpoint JSON already merged once by CP7-T12
- **WHEN** the composed command is run and merged a second time with no change to the fixture tree
- **THEN** the checkpoint JSON's done_when and notes are identical before and after the second merge (no duplicate items)
<!-- proven by test CP7-T13 in tests/test_impact_analysis_e2e.py, tests/fixtures/impact_analysis_m_shaped/, tests/fixtures/impact_analysis_greenfield/, commit 5d5fa15 -->
