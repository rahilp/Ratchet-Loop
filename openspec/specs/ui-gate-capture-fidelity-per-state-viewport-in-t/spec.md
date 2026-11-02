# UI-gate capture fidelity: per-state viewport in the capture tool, capture-vs-plan lint with stub scaffolding, and a per-case insufficient-evidence verdict that routes to the capture spec instead of the implementer

## Purpose
What this feature does, as built and checked by Ratchet (run ui-gate-capture-fidelity-per-state-viewport-in-t, 2026-11-02). Each scenario names the test that proves it.

## Requirements

### Requirement: Each capture state can set its width
The system SHALL do this: Someone writing a capture spec can give one state its own width (for example a phone width) next to desktop states, and each state is captured and shown to reviewers at its own width.

- A capture spec with one phone-width state and other desktop states captures each state at its own width in one run
- A spec with no per-state width captures every state exactly as before (the spec's width, or 1280x800)
- The capture record and the reviewers' input name the width each state was captured at
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it loads or scans engine source, so it is counted with the rest

#### Scenario: The capture script can be asked which width a state gets, without starting a capture
- **GIVEN** skills/ratchet-ui-compare/scripts/capture.mjs on disk; node on PATH; Playwright not installed; no spec file
- **WHEN** a node ES-module one-liner (`node --input-type=module -e`) imports capture.mjs and calls the exported stateViewport(spec, state) with spec.viewport 1280x800 and a state whose viewport is 390x844
- **THEN** the import prints nothing and exits 0 (it does not run a capture or print the usage text), and the call returns {width:390,height:844}. Fails today: capture.mjs exports nothing and importing it runs main(), which prints usage and exits 2
<!-- proven by test CP1-T01 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: A state's own width beats the spec's width, which beats the 1280x800 default
- **GIVEN** four spec/state pairs: (a) state 390x844 + spec 1440x900, (b) state with no viewport + spec 1440x900, (c) state with no viewport + spec with no viewport, (d) state 820x1180 + spec with no viewport
- **WHEN** stateViewport is called for each pair through the same node one-liner
- **THEN** (a) 390x844, (b) 1440x900, (c) 1280x800, (d) 820x1180; the spec object passed in is unchanged afterwards (deep-equal to a copy taken before the call), so one state's width cannot leak into the next call
<!-- proven by test CP1-T02 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: One run captures a phone-width state and desktop states each at their own width
- **GIVEN** a capture spec with spec viewport 1440x900 and three states in this order: `list` (no viewport), `phone` (viewport 390x844, with a 2-step journey), `detail` (no viewport); real capture.mjs run as a script with PLAYWRIGHT_MODULE pointing at a stub Playwright module the test writes to a temp folder (stub chromium.launch() returns a fake browser; fake newContext(opts) logs opts.viewport, fake pages write a small file at every screenshot path and log which context took which screenshot, goto and locator calls succeed); the module is found through the script's own PLAYWRIGHT_MODULE hook, so no browser and no network
- **WHEN** `node capture.mjs --spec <spec> --out <dir>` runs once
- **THEN** exit code 0 and manifest.json is written; the stub saw 6 contexts (reference and app per state); for `list` and `detail` both the reference and the app context were opened at 1440x900, for `phone` both at 390x844 (so the phone width does not carry over into `detail` after it); every screenshot of the phone state (page shot and both journey shots, both sides) came from a 390x844 context. Fails today: all six contexts use 1440x900
<!-- proven by test CP1-T03 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: The capture record names the width each state was captured at
- **GIVEN** the manifest.json produced by the T03 run
- **WHEN** the test reads manifest.json
- **THEN** states[] entries `list`, `phone`, `detail` each carry a `viewport` object equal to {1440,900}, {390,844}, {1440,900} respectively; the manifest's top-level `viewport` is still the spec-level {1440,900} (unchanged field), and the existing per-state fields (name, scope, dir, reference, app) are still present. Fails today: no per-state viewport in the record
<!-- proven by test CP1-T04 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: A spec with only a spec-level width captures every state at that width, as before
- **GIVEN** a capture spec with spec viewport 1000x700 and two states with no viewport of their own; same stub-Playwright run as T03
- **WHEN** `node capture.mjs --spec ... --out ...` runs
- **THEN** all four contexts open at 1000x700; manifest top-level viewport is 1000x700 as before; every manifest state entry also records 1000x700; the console summary lines and manifest keys that existed before are still there. Fails today only on the new per-state record, so the old-behavior half is guarded while the new field is required
<!-- proven by test CP1-T05 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: A spec with no width anywhere still captures at 1280x800
- **GIVEN** a capture spec with no viewport at spec level or state level and one state; stub-Playwright run
- **WHEN** `node capture.mjs --spec ... --out ...` runs
- **THEN** both contexts open at 1280x800; manifest top-level viewport is {1280,800}; the state entry records {1280,800}. Fails today on the missing per-state record
<!-- proven by test CP1-T06 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: Only one state sets a width and the spec sets none: the others stay at 1280x800
- **GIVEN** a capture spec with no spec-level viewport and states `phone` (viewport 390x844) first, then `desktop` (none); stub-Playwright run
- **WHEN** `node capture.mjs --spec ... --out ...` runs
- **THEN** phone contexts are 390x844 and desktop contexts are 1280x800 (the default, not the phone width); manifest top-level viewport is {1280,800}; the manifest state entries record 390x844 and 1280x800 in that order. Phone-first order catches a script that keeps the first state's width for later states
<!-- proven by test CP1-T07 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: Importing the capture script is safe but running it still works
- **GIVEN** capture.mjs on disk, plus a symlink to it in a temp folder
- **WHEN** (a) the module is imported from node with no arguments; (b) `node capture.mjs` is run with no arguments; (c) `node <symlink> --spec <spec with targets.app missing> --out <dir>` is run; (d) a normal stub-Playwright capture is started through the symlink path
- **THEN** (a) exit 0, empty stdout and stderr; (b) exit 2 and the usage line, same as today; (c) exit 2 with `spec.targets.app missing` before any browser is needed; (d) the capture runs and writes manifest.json (the guard that stops import from running main() does not also stop a script started by a relative path or a symlink). Fails today on (a)
<!-- proven by test CP1-T08 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: Reviewers' input names each state's own width, for both reviewers
- **GIVEN** a hand-made capture manifest (written the way cp7_manifest in tests/test_gates.py does, tiny PNG files) with top-level viewport 1280x800 and two states: `home` with viewport {1280,800} and `phone-menu` with viewport {390,844}
- **WHEN** `python3 skills/ratchet-ui-compare/scripts/ui_review.py stage --manifest <m> --scope home` runs (its public CLI, which prints the visual and behavior folders)
- **THEN** in BOTH folders' INPUT.md, the text of the `## State `phone-menu`` section (up to the next `## ` heading or the end) contains 390 and 844 and not 1280; the `home` section contains 1280 and 800 and not 390; the screenshot lines and scope text of each section are still there. Fails today: only one global Viewport line exists
<!-- proven by test CP1-T09 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: A capture record from before this change (no per-state width) is still staged, with the one width it has
- **GIVEN** a hand-made manifest in the old shape: top-level viewport 1000x700, two states with no `viewport` key
- **WHEN** ui_review.py stage runs on it
- **THEN** exit 0; INPUT.md still has the `Viewport:` line with 1000 and 700, the `## State` heading, scope text and screenshot lines for each state exactly as the old format wrote them (each old line still present, in order); and each state's section now names 1000 and 700 (falls back to the manifest width). Fails today only on the per-state naming
<!-- proven by test CP1-T10 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: The new test file is listed in the README and the inventory rule is respected
- **GIVEN** README.md, tests/fixtures/modularize_cp1/test-inventory.md and tests/test_ui_capture_viewport.py as written
- **WHEN** the test reads README.md and its own source
- **THEN** README.md's 'Running the tests' list has the exact line `python3 tests/test_ui_capture_viewport.py`; and the inventory rule holds both ways: if this file's text contains a module-load or engine-source-scan pattern (the two greps at the top of test-inventory.md) it must be named in test-inventory.md with an entry, and if it contains neither it must not need one (the file is written to contain neither: it runs node and ui_review.py through subprocess only). Fails today: the README line is absent
<!-- proven by test CP1-T11 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: A state that sets only a width or only a height takes the other side from the spec, else 1280x800
- **GIVEN** four pairs: (a) state {width:390} + spec 1440x900, (b) state {height:844} + spec 1440x900, (c) state {width:390} + spec with no viewport, (d) state {height:844} + spec with no viewport
- **WHEN** stateViewport(spec, state) is called for each pair through the node ES-module one-liner
- **THEN** (a) 390x900, (b) 1440x844, (c) 390x800, (d) 1280x844; both numbers are always set (never undefined or NaN) and nothing is refused or thrown; the spec object is unchanged afterwards. Fails today: no exported helper
<!-- proven by test CP1-T12 in tests/test_ui_capture_viewport.py, commit 98938ac -->

#### Scenario: A partly set state width is captured with the missing side filled in, in a real run
- **GIVEN** a capture spec with spec viewport 1440x900 and states `narrow` (viewport {width:390}), `short` (viewport {height:600}), `plain` (none); same stub-Playwright run as T03
- **WHEN** `node capture.mjs --spec ... --out ...` runs
- **THEN** exit 0; contexts for `narrow` open at 390x900, for `short` at 1440x600, for `plain` at 1440x900 (reference and app alike); the manifest state entries record the same full widths and heights, with no undefined or missing side. Fails today: the whole state viewport is ignored, so all contexts use 1440x900
<!-- proven by test CP1-T13 in tests/test_ui_capture_viewport.py, commit 98938ac -->

### Requirement: Warn when a browser case lacks a capture
The system SHALL do this: Before capture, a check names every browser-only test case that the capture spec has no state, no journey steps or the wrong width for, and says what is missing.

- Running the check on a plan whose browser-only case has no capture state prints a warning naming the case and saying no state covers it
- A covered case whose state has no journey steps, or is captured at a different width than the case asks for, is warned about the same way, naming what is missing
- A plan whose browser cases are all covered, or that has only code cases, prints no warning
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it loads or scans engine source, so it is counted with the rest

#### Scenario: A browser case with no capture state is named in a warning
- **GIVEN** a test plan with browser case CP2-T07 'Approve button shows a spinner while saving' (no ui_state, no viewport) and a capture spec whose only state 'empty' lists cases ['CP2-T09'] and has a journey
- **WHEN** capture_gaps(plan, spec) runs, and separately `ui_review.py lint --spec states/CP2.json --cases tests/CP2.json` runs in a temp folder with no .ratchet folder
- **THEN** the function returns exactly one gap: case 'CP2-T07', title as in the plan, missing 'no-state', with a detail string; the command exits 0 and prints exactly one line starting WARN that contains 'CP2-T07' and the words 'no state'
<!-- proven by test CP2-T01 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A covered case whose state has no journey steps is warned about
- **GIVEN** a test plan with browser case CP2-T07 'Approve button shows a spinner while saving' and a spec with state 'saving' listing cases ['CP2-T07'] whose journey is [] (and a second case-less state with a journey)
- **WHEN** capture_gaps runs, and the lint command runs
- **THEN** one gap for CP2-T07 with missing 'no-journey' naming state 'saving'; the command prints one WARN line naming CP2-T07, the state 'saving' and the word 'journey', and exits 0
<!-- proven by test CP2-T02 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A state with the journey key missing altogether counts as no journey steps
- **GIVEN** browser case CP2-T07 and state 'saving' with cases ['CP2-T07'] and no 'journey' key at all
- **WHEN** capture_gaps runs
- **THEN** one gap for CP2-T07 with missing 'no-journey' (no KeyError)
<!-- proven by test CP2-T03 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A case that asks for a phone width is warned about when its state is captured at desktop width
- **GIVEN** browser case CP2-T07 with viewport {width:390,height:844}; the spec has viewport {1280,800} and state 'saving' (cases ['CP2-T07'], journey with 2 steps, no own viewport)
- **WHEN** capture_gaps runs, and the lint command runs
- **THEN** one gap for CP2-T07 with missing 'viewport'; its detail and the WARN line name both widths (390x844 wanted, 1280x800 captured) and say 'width'; exit 0
<!-- proven by test CP2-T04 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A phone-width state beside desktop states covers a phone-width case
- **GIVEN** browser case CP2-T07 with viewport 390x844; spec viewport 1280x800 with a desktop state 'list' (journey, cases ['CP2-T03']) and a state 'saving-phone' with its own viewport 390x844, cases ['CP2-T07'] and a journey
- **WHEN** the lint command runs
- **THEN** no WARN line and exit 0 (uses the state's own width, not the spec's)
<!-- proven by test CP2-T05 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: The state width falls back to the spec width, then to 1280x800, when judging a case
- **GIVEN** three runs: (a) case asks 1440x900, spec viewport 1440x900, state has none; (b) case asks 1280x800, spec and state have no viewport; (c) case asks 1280x800, spec 390x844, state has none
- **WHEN** capture_gaps runs for each
- **THEN** (a) and (b) return no gap; (c) returns a 'viewport' gap
<!-- proven by test CP2-T06 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A plan whose browser cases are all covered prints no warning
- **GIVEN** browser cases CP2-T07 (covered through the state's cases list, journey present) and CP2-T08 (covered through its ui_state naming a state, journey present), both with matching or no widths
- **WHEN** capture_gaps runs, and the lint command runs
- **THEN** the function returns an empty list; the command prints no line containing WARN, exits 0
<!-- proven by test CP2-T07 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A plan that has only code cases prints no warning, even with an empty capture spec
- **GIVEN** a test plan whose cases are all layer 'code' (one of them carries a ui_state that matches no state) and a capture spec {} with no states
- **WHEN** capture_gaps runs, and the lint command runs with that spec file
- **THEN** an empty list, no WARN line, exit 0
<!-- proven by test CP2-T08 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: Code cases are not warned about when a browser case in the same plan is
- **GIVEN** a plan with code case CP2-T01, code case CP2-T02 with ui_state 'ghost', and browser case CP2-T07; the spec has no states
- **WHEN** capture_gaps runs
- **THEN** exactly one gap, for CP2-T07; neither code case is named
<!-- proven by test CP2-T09 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A plan with no cases at all, or a spec with no states key, is handled
- **GIVEN** (a) test plan {'cases': []} with a spec with states; (b) test plan {} ; (c) a plan with a browser case and a spec {} (no states key)
- **WHEN** capture_gaps runs for each
- **THEN** (a) and (b) return []; (c) returns a 'no-state' gap for the case
<!-- proven by test CP2-T10 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A state covers a case when its cases list holds the case id, in any usual spelling
- **GIVEN** browser case 'CP2-T07' in the plan; states listing 'cp2_t07' (run 1), 'CP2_T07' (run 2), 'CP2-T07' (run 3); each has a journey
- **WHEN** capture_gaps runs for each
- **THEN** no gap in any run; a state listing 'CP2-T70' instead yields a 'no-state' gap
<!-- proven by test CP2-T11 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A state covers a case when the case's ui_state names that state
- **GIVEN** browser case CP2-T08 with ui_state 'checkout-error'; state 'checkout-error' has no cases list but has a journey
- **WHEN** capture_gaps runs
- **THEN** no gap
<!-- proven by test CP2-T12 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A case whose ui_state names a state that does not exist is warned about, naming that state
- **GIVEN** browser case CP2-T08 with ui_state 'checkout-error'; the spec has only a state 'checkout-ok' (journey present) that lists cases ['CP2-T01']
- **WHEN** capture_gaps runs, and the lint command runs
- **THEN** one 'no-state' gap whose detail and WARN line name 'checkout-error'
<!-- proven by test CP2-T13 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A case with no width of its own does not care about the state's width
- **GIVEN** browser case CP2-T07 with no viewport; its state 'saving-phone' has its own viewport 390x844 and a journey
- **WHEN** capture_gaps runs
- **THEN** no gap
<!-- proven by test CP2-T14 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: With several covering states, one good one is enough; if all are bad the case is warned
- **GIVEN** browser case CP2-T07 asking 390x844; states A (cases [CP2-T07], journey [], phone width) and B (ui_state match, journey present, phone width); then a variant where B is at 1280x800
- **WHEN** capture_gaps runs for both variants
- **THEN** first variant: no gap; second variant: a gap for CP2-T07 (not silent)
<!-- proven by test CP2-T15 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: A case that is both journey-less and the wrong width reports both problems
- **GIVEN** browser case CP2-T07 asking 390x844; its only state has an empty journey and is captured at 1280x800
- **WHEN** capture_gaps runs, and the lint command runs
- **THEN** two gaps for CP2-T07, 'no-journey' and 'viewport'; the output holds one WARN line per gap and each says what is missing
<!-- proven by test CP2-T16 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: Every gap is warned about by name, one line each, in plan order; covered cases are not named
- **GIVEN** a plan with browser cases CP2-T05 (covered), CP2-T06 (no state), CP2-T07 (state without journey), CP2-T08 (wrong width) and code case CP2-T01
- **WHEN** the lint command runs
- **THEN** exactly three WARN lines in order CP2-T06, CP2-T07, CP2-T08 each naming its case id and what is missing; CP2-T05 and CP2-T01 appear in no WARN line; exit 0
<!-- proven by test CP2-T17 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: The check only reads: it writes no file and changes neither input
- **GIVEN** a spec file and a plan file with known bytes, and in-memory copies of both dicts with a gap in them
- **WHEN** the lint command runs, and capture_gaps runs on deep copies
- **THEN** both files are byte-identical afterwards, no new file is created in the folder, the dicts equal their copies
<!-- proven by test CP2-T18 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: Missing or unreadable input files fail cleanly, without a traceback
- **GIVEN** (a) --spec points at a file that does not exist; (b) --cases points at a missing file; (c) --spec holds invalid JSON; (d) --cases holds invalid JSON; (e) --spec holds a JSON list instead of an object
- **WHEN** the lint command runs for each
- **THEN** each run exits non-zero, prints a one-line message that names the offending file, and prints no 'Traceback' (decision pinned: unreadable input is a usage error, unlike a gap, which is only a warning)
<!-- proven by test CP2-T19 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: Oddly shaped but valid JSON does not crash the check
- **GIVEN** specs and plans with: states not a list, state entries that are strings or null, journey given as a string, viewport given as a string or with a missing height, cases entry that is not an object, a browser case with no id, a state's cases given as the string 'CP2-T07'
- **WHEN** capture_gaps runs for each shape, and the lint command runs for one of them
- **THEN** no exception and no traceback; a list of gaps comes back; a browser case whose only 'state' is junk is still reported (never silently covered); a case with no id is reported under a placeholder name instead of being dropped
<!-- proven by test CP2-T20 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: The command line lists the new check next to the existing ones
- **GIVEN** the ui_review.py command line
- **WHEN** `ui_review.py --help` and `ui_review.py lint --help` run
- **THEN** the help lists lint together with review, stage and combine; lint's help shows --spec and --cases; the command with no subcommand still exits non-zero
<!-- proven by test CP2-T21 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: The new test file is listed in the README's 'Running the tests' list
- **GIVEN** README.md
- **WHEN** the lines of the 'Running the tests' code block are read
- **THEN** the block holds the line 'python3 tests/test_ui_capture_lint.py' exactly once, and tests/test_ui_capture_lint.py exists
<!-- proven by test CP2-T22 in tests/test_ui_capture_lint.py, commit 6cca741 -->

#### Scenario: The new test file is counted in the test inventory if it loads or scans engine source
- **GIVEN** tests/test_ui_capture_lint.py, and the two inventory greps from tests/fixtures/modularize_cp1/test-inventory.md
- **WHEN** the test file's text is checked against both grep patterns
- **THEN** either it matches neither pattern (it imports ui_review through sys.path) or test-inventory.md names it
<!-- proven by test CP2-T23 in tests/test_ui_capture_lint.py, commit 6cca741 -->

### Requirement: Scaffold marked stubs for missing cases
The system SHALL do this: The check can add a ready-to-fill stub capture state for each missing case, plainly marked as a stub and unreviewed, and the capture keeps that mark.

- Asking the check to scaffold adds one stub state per missing case to the capture spec, named after the case, marked as a stub and unreviewed, with the case's width when it has one
- Running it again adds no duplicates, and it writes nothing (and says why) while the step's capture spec is locked after a failed UI round
- A captured stub state still carries its stub mark in the capture record
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it loads or scans engine source, so it is counted with the rest

#### Scenario: Scaffold adds exactly one stub per case the capture is missing, named after the case
- **GIVEN** Project A with the plan above: CP1-T02 and CP1-T03 have no state, CP1-T04's state has no journey, CP1-T05's state is at the wrong width, CP1-T01 is covered, CP1-T06 is a code case
- **WHEN** `ui_review.py lint --spec states/CP1.json --cases tests/CP1.json --scaffold` is run
- **THEN** the spec file gains exactly four new states named stub-CP1-T02, stub-CP1-T03, stub-CP1-T04 and stub-CP1-T05, one per gap, and none for CP1-T01 (covered) or CP1-T06 (code case); the command exits 0 and prints each stub's name
<!-- proven by test CP3-T01 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: Each stub is plainly marked as a stub and unreviewed, and ready to fill in
- **GIVEN** the spec after the scaffold run of CP3-T01
- **WHEN** the stub state for CP1-T02 is read
- **THEN** it has stub true, cases ["CP1-T02"], an empty journey list, reference and app entries that each have a path key (empty), and a scope that starts with 'STUB, UNREVIEWED' and names CP1-T02, the case's title and says to add the journey and then remove the stub mark
<!-- proven by test CP3-T02 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: A stub takes the case's width when the case has one, and none otherwise
- **GIVEN** the spec after the scaffold run: CP1-T03 asks for viewport 390x844, CP1-T02 asks for no width
- **WHEN** stub-CP1-T03 and stub-CP1-T02 are read
- **THEN** stub-CP1-T03 has viewport {width: 390, height: 844}; stub-CP1-T02 has no viewport key at all (it inherits the spec width), and the wrong-width stub-CP1-T05 also carries 390x844
<!-- proven by test CP3-T03 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: Existing states and spec settings are never altered
- **GIVEN** the spec of Project A before and after the scaffold run
- **WHEN** the original states ('home', 'cart', 'checkout') and every top-level key (checkpoint, viewport, targets, login) are compared before and after
- **THEN** each is deeply equal to what it was, in the same order, and the new stubs come after them at the end of the states list; a spec with no per-state width keeps its meaning
<!-- proven by test CP3-T04 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: A gap on an existing state gets its own stub instead of editing that state
- **GIVEN** state 'cart' has no journey (covers CP1-T04) and state 'checkout' is at 1280x800 but CP1-T05 asks for 390x844
- **WHEN** scaffold runs
- **THEN** stub-CP1-T04 and stub-CP1-T05 are added as separate states while 'cart' still has its empty journey and 'checkout' still has no 390x844 width (the real states are not edited, not given a journey and not re-widthed)
<!-- proven by test CP3-T05 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: Running scaffold again adds nothing
- **GIVEN** the spec after one scaffold run
- **WHEN** the same scaffold command is run a second time (and a third)
- **THEN** the spec file is byte-for-byte identical to after the first run, there are still exactly four stubs (no duplicate names), the command exits 0 and says there is nothing to add
<!-- proven by test CP3-T06 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: A stub the author already started filling in is never overwritten or duplicated
- **GIVEN** the spec already holds a state named stub-CP1-T02 that the author edited (journey with two steps, a reworded scope, stub still true)
- **WHEN** scaffold runs
- **THEN** that state is unchanged byte for byte and no second stub-CP1-T02 appears, while stubs for the other missing cases are still added
<!-- proven by test CP3-T07 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: A case added to the plan later gets a stub on the next run, only for it
- **GIVEN** scaffold has run once; the plan then gains a new browser case CP1-T07 with no state
- **WHEN** scaffold runs again
- **THEN** exactly one new state stub-CP1-T07 is appended and the four earlier stubs and all real states are unchanged
<!-- proven by test CP3-T08 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: While the capture spec is locked after a failed UI round, scaffold writes nothing and says why
- **GIVEN** Project B: the run's CP1 UI gate is frozen (a UI round found differences); states/CP1.json has gaps
- **WHEN** scaffold is run for states/CP1.json
- **THEN** the spec file is byte-for-byte unchanged, the command exits non-zero (a refusal, not a silent skip), and the message says the capture spec is locked after a failed UI round and names `ask ui-respec CP1` as the way to unlock it
<!-- proven by test CP3-T09 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: A locked spec still gets the warnings, only the writing is refused
- **GIVEN** the same frozen Project B
- **WHEN** scaffold is run
- **THEN** the output still contains the WARN line for each uncovered case, naming it, so the author sees what is missing while the write is refused
<!-- proven by test CP3-T10 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: Another step's lock, or no lock, does not stop scaffolding
- **GIVEN** Project B where CP2's UI gate is frozen but CP1's is not (and a second run with nothing frozen at all)
- **WHEN** scaffold is run for states/CP1.json
- **THEN** the stubs are written as in CP3-T01 and the command exits 0: the lock is per step
<!-- proven by test CP3-T11 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: Outside any Ratchet run there is no lock
- **GIVEN** Project A has no .ratchet folder (an ad-hoc comparison)
- **WHEN** scaffold is run
- **THEN** it writes the stubs and exits 0, with no error about a missing run or state
<!-- proven by test CP3-T12 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: The check without --scaffold never writes the spec
- **GIVEN** Project A with gaps
- **WHEN** `lint` is run without --scaffold
- **THEN** the spec file is byte-for-byte unchanged, the gaps are still warned about by case name, and the exit is 0
<!-- proven by test CP3-T13 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: Scaffold with nothing missing leaves the spec alone
- **GIVEN** a spec and plan where every browser case is covered with a journey and the right width (and a second plan with only code cases)
- **WHEN** scaffold is run
- **THEN** the spec file is byte-for-byte unchanged, no stub is added, no warning is printed and the exit is 0
<!-- proven by test CP3-T14 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: A captured stub keeps its stub mark, its cases and its width in the capture record
- **GIVEN** a spec with one real state ('home') and one stub state (stub true, cases [CP1-T03], viewport 390x844, empty journey), captured by the real capture.mjs under node with a fake playwright module
- **WHEN** capture.mjs --spec ... --out ... finishes and manifest.json is read
- **THEN** the manifest entry for the stub state has stub true, cases ["CP1-T03"] and viewport 390x844, and keeps its 'STUB, UNREVIEWED' scope text; the entry for 'home' has no stub mark (absent or false) and still has name, scope, dir, reference and app as before
<!-- proven by test CP3-T15 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: A scaffolded spec goes through capture and the stubs arrive marked in the record
- **GIVEN** Project A's spec after a real scaffold run
- **WHEN** capture.mjs is run on that scaffolded spec with the fake playwright module
- **THEN** capture exits 0, manifest.json lists all original states plus the four stubs, each stub entry carries stub true and its case id, and the manifest's spec_sha256 equals the sha256 of the scaffolded file (so ui-record can still match the spec on disk)
<!-- proven by test CP3-T16 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: The new test file is listed in the README's 'Running the tests' list
- **GIVEN** README.md
- **WHEN** its 'Running the tests' section is read
- **THEN** it contains the exact line `python3 tests/test_ui_capture_stub.py`
<!-- proven by test CP3-T17 in tests/test_ui_capture_stub.py, commit 2b81603 -->

#### Scenario: The test file is in the test inventory if it loads or scans engine source
- **GIVEN** tests/test_ui_capture_stub.py and tests/fixtures/modularize_cp1/test-inventory.md
- **WHEN** the file is checked for spec_from_file_location use or reading/parsing engine source
- **THEN** either it does neither (it only runs commands and imports ui_review through sys.path), or it is listed in the inventory; it never ends up in neither state
<!-- proven by test CP3-T18 in tests/test_ui_capture_stub.py, commit 2b81603 -->

### Requirement: Check the capture before reviewers run
The system SHALL do this: When the UI review starts, the same check runs first, warns about every uncovered browser case, and refuses to start paid reviewers while a stub is still unfilled.

- Starting a review whose capture misses a browser case prints the same warning, naming the case and what is missing, before any reviewer starts
- A review whose capture still holds an unfilled stub stops before any reviewer starts and says which stub to fill in
- Reviewers' input marks any stub state plainly as a stub with no evidence; a capture with no gaps reviews exactly as before
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it loads or scans engine source, so it is counted with the rest

#### Scenario: A browser case with no capture state is warned about, by name, before any reviewer starts
- **GIVEN** the gap-free manifest minus the 'menu' state, so browser case CP1-T04 'Menu opens' has no state, and tests/CP1.json passed as --cases; stub claude and codex on PATH; stderr going to a file the stub snapshots
- **WHEN** `ui_review.py review --manifest m.json --out out --scope all --cases tests/CP1.json` runs to the end
- **THEN** the stub's snapshot of stderr taken when the first reviewer started already holds exactly one line starting WARN that contains 'CP1-T04' and the words 'no state'; no other case id appears in a WARN line; the warning does not stop the review (exit 0, both reviewers were started once each, out/combined.json exists)
<!-- proven by test CP4-T01 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: A covered case whose state has no journey steps is warned about, naming the case and the state
- **GIVEN** the gap-free manifest with 'menu' kept but its journey empty (state-level and app.journey), so CP1-T04 is covered by a state with no journey steps
- **WHEN** `review ... --cases tests/CP1.json` runs
- **THEN** before the first reviewer started, stderr already held exactly one WARN line naming 'CP1-T04', the state 'menu' and the word 'journey'; exit 0; no other case is named
<!-- proven by test CP4-T02 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: A phone-width case whose state was captured at desktop width is warned about, naming both widths
- **GIVEN** the gap-free manifest with state 'checkout-phone' recorded at 1280x800 instead of 390x844, while CP1-T02 asks for 390x844
- **WHEN** `review ... --cases tests/CP1.json` runs
- **THEN** before the first reviewer started, stderr already held exactly one WARN line naming 'CP1-T02', the word 'width' (or 'viewport') and both '390' and '1280'; exit 0; and the same manifest with 'checkout-phone' at 390x844 gives no WARN line at all
<!-- proven by test CP4-T03 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: Every gap gets its own named warning; covered cases and code cases are never named
- **GIVEN** the manifest with all three gaps at once (CP1-T04 has no state, CP1-T02 at the wrong width, CP1-T03's named state 'checkout-error' has no journey), CP1-T01 covered, and CP1-T05 a code case that no state mentions
- **WHEN** `review ... --cases tests/CP1.json` runs
- **THEN** the pre-launch stderr snapshot holds exactly three WARN lines, one naming CP1-T02, one CP1-T03 and one CP1-T04, each saying what is missing; no WARN line contains CP1-T01 or CP1-T05; exit 0
<!-- proven by test CP4-T04 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: A state inherits the capture's width, and a case can name its state, without a false warning
- **GIVEN** the gap-free manifest where 'cart' has no viewport of its own (inherits the manifest's 1280x800) and CP1-T03 is covered only through ui_state 'checkout-error'; twin: the same manifest with 'menu' removed
- **WHEN** `review ... --cases tests/CP1.json` runs on each
- **THEN** the gap-free run prints no line containing WARN (state-less width inherits, ui_state routing works) and the twin prints exactly one WARN, for CP1-T04, so the check is live and not just silent
<!-- proven by test CP4-T05 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: A capture with no gaps reviews exactly as before
- **GIVEN** the gap-free manifest, tests/CP1.json, stub claude and codex; and a twin run on the same manifest and plan with the 'menu' state removed (one gap)
- **WHEN** `review ... --cases tests/CP1.json --keep` runs on each, plus once on the gap-free manifest with no --cases
- **THEN** gap-free run: exit 0, no WARN and no 'stub' or 'unreviewed' text on stderr, exactly one visual and one behavior reviewer were started with the same arguments as in the no --cases run, out/ holds visual.json, behavior.json and combined.json and nothing else, combined verdict PASS with 0 differences, the manifest file's states are deeply equal to what they were (only review_scope added), and each reviewer's INPUT.md has no STUB/unreviewed/no-evidence text and keeps today's stable lines ('# UI review input (visual|behavior)', 'Checkpoint: CP1', the overall-scope line, one '## State `<name>`' line per state in manifest order). The twin run warns once (CP1-T04) yet starts the same reviewers with the same arguments and writes the same output files, so only the warning differs
<!-- proven by test CP4-T06 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: An unfilled stub stops the review before any reviewer starts and says which stub to fill in
- **GIVEN** the stub manifest (stub state 'stub-CP1-T04' beside real covered states), tests/CP1.json, stub claude and codex that log every start
- **WHEN** `review --manifest m.json --out out --scope all --cases tests/CP1.json` runs
- **THEN** the exit code is neither 0 nor 4 and there is no traceback; the stub log is empty (neither claude nor codex was started, no network call); stderr names 'stub-CP1-T04', says it is a stub to fill in (the word 'stub'), and points at the capture spec 'states/CP1.json'; out/ holds no visual.json, behavior.json or combined.json, so nothing a gate could count as a pass exists
<!-- proven by test CP4-T07 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: The stub refusal does not depend on --cases and beats the hand-off to the host's own reviewers
- **GIVEN** the stub manifest; run (a) without --cases; run (b) in a project whose PATH has no claude, codex or gemini key, so reviewers would normally fall to the host (exit 4); run (c) with --visual claude --behavior codex given explicitly
- **WHEN** `review` runs each way
- **THEN** all three refuse the same way as CP4-T07 (exit not 0 and not 4, stub named, stub log empty); in run (b) stdout holds no 'host_roles' hand-off and no staged-folder paths, so no host sub-agent is ever told to review a stub
<!-- proven by test CP4-T08 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: A refused review changes nothing: the capture manifest is left exactly as it was
- **GIVEN** the stub manifest file with its bytes recorded, and an empty out folder
- **WHEN** `review` refuses it (as in CP4-T07)
- **THEN** the manifest file is byte-for-byte identical afterwards (no review_scope, no stamp or fingerprint written), no staged ratchet-visual-* or ratchet-behavior-* folder was left in the private TMPDIR, and out/ holds no file; decision pinned: the refusal comes before the manifest is touched
<!-- proven by test CP4-T09 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: Only a state that is marked as a stub blocks; a filled-in state that kept stub false does not
- **GIVEN** two copies of the stub manifest: one with 'stub-CP1-T04' stub true, one where the state was filled in (journey added, 'menu' semantics) and carries stub false; a third where the stub key is absent
- **WHEN** `review ... --cases tests/CP1.json` runs on each
- **THEN** stub true: refused, no reviewer started; stub false and stub absent: not refused, exit 0, both reviewers started once each, combined verdict PASS
<!-- proven by test CP4-T10 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: The reviewers' input marks a stub state plainly as a stub with no evidence
- **GIVEN** the stub manifest (stub state 'stub-CP1-T04' with empty reference and app paths beside real states 'cart' and 'checkout-phone'), tests/CP1.json
- **WHEN** `ui_review.py stage --manifest m.json --scope all --cases tests/CP1.json` runs, and the staged visual and behavior INPUT.md are read
- **THEN** stage exits 0 (it stages and warns; only `review` refuses); in BOTH INPUT.md files the section that starts at the '## State `stub-CP1-T04`' line and ends at the next '## State' line contains 'STUB, UNREVIEWED' and 'no evidence' and says it must not be judged as passing; the 'cart' and 'checkout-phone' sections contain none of those words; the stub section lists no screenshot or journey file as evidence
<!-- proven by test CP4-T11 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: Staging for the host's own reviewers warns the same way and keeps its output machine-readable
- **GIVEN** the gap manifest with 'menu' removed and tests/CP1.json; twin: the gap-free manifest
- **WHEN** `ui_review.py stage ... --cases tests/CP1.json` runs on each
- **THEN** the gap run exits 0 and prints exactly one WARN line naming CP1-T04 on stderr, while stdout is still exactly the JSON object {"visual": ..., "behavior": ...} (parses, no WARN text in it) and both staged folders exist; the gap-free run prints no WARN
<!-- proven by test CP4-T12 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: The warning also comes before the hand-off to the host, without disturbing the hand-off
- **GIVEN** the manifest with 'menu' removed, tests/CP1.json, and a PATH with no claude or codex and no gemini key, so no outside reviewer can run
- **WHEN** `review ... --cases tests/CP1.json` runs
- **THEN** it exits 4 as today, stdout still parses as the JSON hand-off with 'host_roles' for visual and behavior, and stderr holds one WARN line naming CP1-T04 (not on stdout); the staged folders named in the hand-off exist
<!-- proven by test CP4-T13 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: No browser cases, no plan, or an unreadable plan path gives no warning and no change
- **GIVEN** the manifest with 'menu' removed; run (a) with a plan of only code cases; run (b) with no --cases; run (c) with --cases pointing at a file that does not exist; the gapped run with the full plan as control
- **WHEN** `review` runs in each way
- **THEN** the control prints one WARN naming CP1-T04; runs (a), (b) and (c) print no WARN line, exit 0, start both reviewers once each, show no traceback, and write the same output files as today
<!-- proven by test CP4-T14 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: Starting a review prints the same warning wording as the standalone check
- **GIVEN** the all-gaps manifest and a states/CP1.json capture spec with the same states, widths and journeys, and tests/CP1.json
- **WHEN** `ui_review.py lint --spec states/CP1.json --cases tests/CP1.json` and `review ... --cases tests/CP1.json` each run
- **THEN** the set of WARN lines printed by `review` (stderr, before the first reviewer) equals the set printed by `lint` line for line, ignoring order and surrounding whitespace, with three lines naming CP1-T02, CP1-T03 and CP1-T04
<!-- proven by test CP4-T15 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: The new test file is listed in the README's 'Running the tests' list
- **GIVEN** README.md in the repository
- **WHEN** the 'Running the tests' block is read
- **THEN** it holds the line `python3 tests/test_ui_review_precheck.py` exactly once, and tests/test_ui_review_precheck.py exists
<!-- proven by test CP4-T16 in tests/test_ui_review_precheck.py, commit 56e016a -->

#### Scenario: The new test file is in the test inventory if it loads or scans engine source
- **GIVEN** tests/test_ui_review_precheck.py and tests/fixtures/modularize_cp1/test-inventory.md
- **WHEN** the test file is searched for spec_from_file_location or for reads/ast-parses of engine source, and the inventory is read
- **THEN** either the file does neither (it only runs commands and imports ui_review through sys.path) or the inventory names it; it never ends up in neither state
<!-- proven by test CP4-T17 in tests/test_ui_review_precheck.py, commit 56e016a -->

### Requirement: Undemonstrable cases go back to the capture
The system SHALL do this: When reviewers say a case could not be judged from the captured evidence, the UI gate stops and sends it back to the capture spec, naming the case, never to the implementer, and it still fails on every real difference.

- When a reviewer says a case could not be judged from the evidence, the gate does not pass: it stops, names each such case and what is missing, says to fix the capture spec, and sends nothing to the implementer
- When a real difference was also reported, the gate still fails with every reported difference sent to the implementer, unchanged; the unjudged cases are listed separately for the capture spec
- A review with an unfilled stub state never passes, and reviews with no unjudged case give exactly the same result as before
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it loads or scans engine source, so it is counted with the rest

#### Scenario: Reviewers who cannot judge cases block the gate and name each one
- **GIVEN** Visual reviewer: PASS, no differences. Behaviour reviewer: PASS, no differences, with two unjudged entries: {case:'UI-C2', state:'phone-checkout', missing:'no journey steps reach the confirm dialog'} and {case:'UI-C5', state:'desktop-empty', missing:'captured at 1280 wide, case needs 390'}. Checkpoint CP5.
- **WHEN** The reviews are merged with the real combine
- **THEN** Verdict is INVALID, never PASS. invalid_reason starts with 'INSUFFICIENT-EVIDENCE:' and names UI-C2, phone-checkout, 'no journey steps reach the confirm dialog', UI-C5, desktop-empty and 'captured at 1280 wide, case needs 390', and tells the reader to fix the capture spec (states/CP5.json). differences is empty, so nothing exists for the implementer; the summary does not say PASS or count the unjudged cases as code-fixable differences.
<!-- proven by test CP5-T01 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A real difference plus an unjudged case still fails, with every difference kept
- **GIVEN** Visual reviewer: FAIL with two differences (id V1 blocker 'Pay button is grey', id V2 minor 'card padding 8px not 16px', both fixable_in_code true). Behaviour reviewer: FAIL with one difference (id B1 major 'focus does not move to dialog') and one unjudged entry {case:'UI-C2', state:'phone-checkout', missing:'no journey steps'}.
- **WHEN** The reviews are merged with the real combine
- **THEN** Verdict is FAIL (not INVALID). differences holds exactly V1, V2 and B1, each with its severity, location, description and reviewer name unchanged. The combined review carries the UI-C2 entry in its own insufficient_evidence list, and UI-C2 appears nowhere in differences. invalid_reason is empty (null) so the INSUFFICIENT-EVIDENCE path is not taken.
<!-- proven by test CP5-T02 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: Adding unjudged cases never changes the differences that go to the implementer
- **GIVEN** The same two reviews (three differences across two reviewers, severities blocker/major/minor), merged once with no unjudged entries and once with an unjudged entry added to the behaviour review.
- **WHEN** Both pairs are merged with the real combine and the differences compared
- **THEN** The 'differences' lists of the two results are byte-for-byte identical JSON (same order, same fields, same reviewer stamp) and both verdicts are FAIL. The only added thing in the second result is the separate insufficient_evidence list. The result without unjudged entries has exactly today's keys and summary text ('3 code-fixable difference(s) across 2 reviewer(s)').
<!-- proven by test CP5-T03 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: An unjudged case cannot downgrade or drop a difference of any severity
- **GIVEN** Four runs, each with one reviewer difference of severity blocker, major, minor and nit (all fixable_in_code true) and one unjudged entry, the other reviewer PASS.
- **WHEN** Each run is merged with the real combine
- **THEN** Every run is FAIL, never INVALID and never PASS; the difference is present with its original severity (not lowered, not removed, not marked not-fixable); the unjudged entry is listed separately; no run's invalid_reason mentions INSUFFICIENT-EVIDENCE. (Run under the default ui_block_on_nits=true, so the nit blocks as it does today.)
<!-- proven by test CP5-T04 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: One reviewer passing does not cancel another reviewer's unjudged case
- **GIVEN** Visual reviewer verdict PASS with no differences and no unjudged list. Behaviour reviewer says verdict PASS (a self-reported pass) but lists unjudged case UI-C2. A second run: behaviour reviewer self-reports verdict FAIL with zero differences and one unjudged case. A third run: the reviewer order swapped.
- **WHEN** Each pair is merged with the real combine
- **THEN** No run is PASS: each is INVALID with 'INSUFFICIENT-EVIDENCE:' naming UI-C2, whatever either reviewer self-reported and whichever reviewer comes first.
<!-- proven by test CP5-T05 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: Reviews with nothing unjudged merge exactly as before
- **GIVEN** Visual and behaviour reviewers both PASS with no differences; variants where insufficient_evidence is absent, null or an empty list; variants where the manifest has states with stub false or no stub key; and a call made the old way, combine(reviews, 'CP5', fingerprint, compared) with no extra argument.
- **WHEN** Each variant is merged with the real combine (and the old-style call is made unchanged)
- **THEN** Every variant is PASS with invalid_reason null, differences empty, the summary '0 code-fixable difference(s) across 2 reviewer(s)', and the same set of keys as today's result. The old-style call does not raise.
<!-- proven by test CP5-T06 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A reviewer's own INVALID verdict keeps winning, and unjudged cases are not lost
- **GIVEN** Visual reviewer: verdict INVALID with invalid_reason 'app and reference are in different states'. Behaviour reviewer: PASS with unjudged case UI-C2.
- **WHEN** The reviews are merged with the real combine
- **THEN** Verdict is INVALID, invalid_reason still contains the visual reviewer's 'app and reference are in different states' text, UI-C2 is still carried in the combined unjudged list, and the verdict is neither PASS nor FAIL.
<!-- proven by test CP5-T07 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A case id that is both a difference and an unjudged case is reported both ways
- **GIVEN** Visual reviewer reports difference id 'UI-C2' (blocker, 'Total row is missing'). Behaviour reviewer lists unjudged case 'UI-C2' with state 'phone-checkout' and missing 'no journey steps'.
- **WHEN** The reviews are merged with the real combine
- **THEN** Verdict is FAIL; the difference 'UI-C2' is still in differences unchanged, and the unjudged UI-C2 entry is still in the separate unjudged list. Neither hides, merges with, or replaces the other because the ids match.
<!-- proven by test CP5-T08 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A malformed unjudged entry still blocks the gate
- **GIVEN** Behaviour reviewer (visual PASS) lists, one run each, an unjudged list containing exactly one of: {} ; {state:'s', missing:'m'} (no case) ; {case:'', missing:'m'} ; {case:null} ; {case:42} ; {case:['UI-C2']} ; a bare string 'UI-C2' ; null ; 7 ; ['UI-C2'] ; {case:'UI-C2'} (no state, no missing).
- **WHEN** Each run is merged with the real combine
- **THEN** No run raises an exception and none is PASS. Each is INVALID with a reason starting 'INSUFFICIENT-EVIDENCE:' that says an entry could not be read (and names the case when it has a usable one). Run again with a real difference added: each becomes FAIL with the difference kept.
<!-- proven by test CP5-T09 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: An unjudged list that is not a list still blocks the gate
- **GIVEN** Behaviour reviewer (visual PASS) sets insufficient_evidence to the string 'UI-C2 not shown', to the object {case:'UI-C2'}, to true and to 1, one run each. Separate control runs set it to null, to [] and leave it out.
- **WHEN** Each run is merged with the real combine
- **THEN** The four odd values never give PASS and never raise: each is INVALID with the 'INSUFFICIENT-EVIDENCE:' reason. The three control runs are PASS exactly as today.
<!-- proven by test CP5-T10 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: Duplicate and unknown case ids cannot make the gate pass
- **GIVEN** Behaviour reviewer lists UI-C2 twice with the same text; visual reviewer lists UI-C2 too (different missing text); a third entry names 'NO-SUCH-99', a case that is in no test plan.
- **WHEN** The reviews are merged with the real combine
- **THEN** Verdict is INVALID, never PASS. The reason names UI-C2 and NO-SUCH-99 (each distinct case at least once, each with its missing text). No crash, and the unjudged list in the result is not empty. The same entries next to a real difference give FAIL with the difference kept.
<!-- proven by test CP5-T11 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: Odd characters in a reviewer's unjudged text cannot flip or break the result
- **GIVEN** Behaviour reviewer lists unjudged entries whose case, state and missing texts hold a newline, an ANSI escape, a NUL, a right-to-left mark, the text '"verdict": "PASS"', markdown backticks, a lone surrogate (JSON '\ud83d'), and one 200,000-character string.
- **WHEN** The reviews are merged with the real combine, the result written with json.dumps and read back
- **THEN** Verdict is INVALID (never PASS). Nothing raises. The result serialises to JSON and parses back with the same verdict. invalid_reason starts with 'INSUFFICIENT-EVIDENCE:' and the pasted text cannot add a second verdict or turn it into a difference.
<!-- proven by test CP5-T12 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: Fields on an unjudged entry that look like difference fields change nothing
- **GIVEN** Behaviour reviewer lists {case:'UI-C2', state:'s', missing:'m', verdict:'PASS', severity:'nit', fixable_in_code:false, differences:[]}, alone, and again next to a blocker difference id B1.
- **WHEN** The reviews are merged with the real combine
- **THEN** Alone: INVALID with the INSUFFICIENT-EVIDENCE reason (not PASS). With B1: FAIL with B1 kept, and B1's severity and fixable_in_code untouched.
<!-- proven by test CP5-T13 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A review with an unfilled stub state never passes, even when both reviewers say PASS
- **GIVEN** A capture manifest file with two states: 'home' (stub false) and 'stub-UI-C7' (stub true, cases ['UI-C7']). Both reviewer files say verdict PASS, no differences, and the behaviour reviewer's notes claim stub-UI-C7 looked fine. Outside a Ratchet run.
- **WHEN** `ui_review.py combine --inputs <visual> <behavior> --manifest <manifest> --out <combined>` is run
- **THEN** combined.json has verdict INVALID with a reason starting 'INSUFFICIENT-EVIDENCE:' that names the state 'stub-UI-C7' (and its case UI-C7) and says to fill in or remove the stub in the capture spec. A manifest with the same states but stub false everywhere gives PASS (control).
<!-- proven by test CP5-T14 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A stub state next to a real difference still fails with the difference kept
- **GIVEN** The same manifest with stub state 'stub-UI-C7'; the visual reviewer reports blocker difference V1.
- **WHEN** `ui_review.py combine --manifest` is run outside a run
- **THEN** Verdict FAIL; V1 is in differences unchanged; the stub is named in a separate list, not in differences, not as a code fix.
<!-- proven by test CP5-T15 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: The unjudged-only review is blocked by the real ui-record without freezing or sending anything to the implementer
- **GIVEN** A project at the UI gate for CP1 (gates behavior/ui/adversarial all on). A combined review signed by the real combine, from two reviews: visual PASS, behaviour PASS with unjudged case UI-C2 (state 'phone-checkout', missing 'no journey steps'). compared = {spec_sha256:'s1', scope:'checkout'}.
- **WHEN** `ratchet.py ui-record CP1 --review <combined>` is run, then the capture spec is fixed (a signed PASS combined with compared spec_sha256 's2') and ui-record is run again
- **THEN** First run exits 2 and its output contains 'INSUFFICIENT-EVIDENCE:', UI-C2 and 'no journey steps'; it does not contain 'Gate 2 (UI) FAILED' or 'Send them to ratchet-implementer', and prints no '[severity] location: description' line. state.json: gates.ui.status is 'invalid' (not 'passed'), there is no 'frozen' value, 'blockers' is not set, and the checkpoint's step has not moved past the UI step. The recorded evidence copy keeps the unjudged entry. The second run (changed spec, no ui-respec asked) is accepted and PASSES, because the invalid round froze nothing.
<!-- proven by test CP5-T16 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A real difference plus an unjudged case, through the real ui-record, fails with every difference sent unchanged
- **GIVEN** Two identical projects at the UI gate for CP1. Project A: combined from visual FAIL (blocker 'Pay button is grey' at 'checkout/pay', minor 'card padding' at 'checkout/card') and behaviour FAIL (major 'focus not in dialog' at 'checkout/dialog'). Project B: the same three differences plus unjudged case UI-C2.
- **WHEN** `ratchet.py ui-record CP1 --review <combined>` is run in each project, then a changed-spec round is tried in B
- **THEN** Both exit 2 with the same 'Gate 2 (UI) FAILED for CP1: 3 blocking difference(s). Send them to ratchet-implementer' message and the same three '[severity] location: description' lines in the same order; B's output never lists UI-C2 as a blocking line and the count is 3, not 4. In both, gates.ui.status is 'failed', blockers is 3 and frozen equals the compared block. B's evidence combined.json also holds UI-C2 in the separate unjudged list. In B, a later round with a changed spec is refused with the 'ask ui-respec CP1' wording (the existing path).
<!-- proven by test CP5-T17 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: An unjudged case beside only a difference ui-record would not block on is INVALID, never a pass
- **GIVEN** A project at the UI gate for CP1 with config ui_block_on_nits=false. Combined review (real combine): visual reports one nit (fixable_in_code true, 'icon 1px off'), behaviour lists unjudged case UI-C2. Control 2: the same review under the default ui_block_on_nits=true is FAIL, with the nit listed as a blocking line and gates.ui.status 'failed'.
- **WHEN** `ratchet.py ui-record CP1 --review <combined>` is run
- **THEN** The combined review has verdict INVALID with a reason starting 'INSUFFICIENT-EVIDENCE:' naming UI-C2; ui-record exits 2 with that text, gates.ui.status is 'invalid' (not 'passed', not 'failed'), nothing is frozen, and the step does not advance past the UI gate. The nit is still present in differences of the recorded evidence copy (kept, not dropped). Rule pinned: combine emits INVALID with an unjudged case unless some difference would make ui-record itself record FAIL; combine applies the same blocking test as ui-record, including the same ui_block_on_nits setting: with ui_block_on_nits=false a nit-only review plus unjudged is INVALID, with the default true it is FAIL with the nit sent to the implementer. (see T19, T04). Control: the same nit with no unjudged case records exactly what it does today.
<!-- proven by test CP5-T18 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A difference ui-record counts as blocking is never turned into a capture problem by an unjudged case
- **GIVEN** A project at the UI gate for CP1. Combined review (real combine): visual reports a blocker with fixable_in_code false ('Pay button missing') and behaviour lists unjudged case UI-C2.
- **WHEN** `ratchet.py ui-record CP1 --review <combined>` is run
- **THEN** Exit 2 with 'Gate 2 (UI) FAILED for CP1: 1 blocking difference(s)' and the '[blocker] ...: Pay button missing' line; gates.ui.status is 'failed' and frozen is set; the output is not the INSUFFICIENT-EVIDENCE message. The evidence copy keeps UI-C2 in the separate unjudged list.
<!-- proven by test CP5-T19 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: Reviews with no unjudged case record exactly as before, through the real ui-record
- **GIVEN** A project at the UI gate for CP1. Combined review (real combine) from visual PASS and behaviour PASS, no differences, no unjudged list, no stub.
- **WHEN** `ratchet.py ui-record CP1 --review <combined>` is run
- **THEN** Exit 0, 'Gate 2 (UI) PASSED for CP1', gates.ui.status 'passed', frozen cleared, step advanced, as in the existing UI-gate tests.
<!-- proven by test CP5-T20 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: An unfilled stub through the real in-run combine is blocked by ui-record
- **GIVEN** An active run at the UI round for CP1; a capture manifest stamped and signed for the round with a state 'stub-UI-C7' marked stub true; two signed reviewer files (both PASS, no differences) made the way tests/test_signing.py does (see run_combine at :124 and the combine case at :1285-1325).
- **WHEN** `ui_review.py combine --manifest <manifest> --checkpoint CP1` is run inside the run, then `ratchet.py ui-record CP1 --review <combined>`
- **THEN** combine exits 0 and writes a signed combined review with verdict INVALID naming stub-UI-C7; ui-record exits 2 with 'INSUFFICIENT-EVIDENCE:' and the stub name, gates.ui.status 'invalid', no frozen value, nothing recorded as passed.
<!-- proven by test CP5-T21 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: A reviewer's unjudged list survives the real review step
- **GIVEN** A project at the UI round with a fake codex CLI on PATH (no real provider) whose reply is a review JSON with verdict 'PASS', no differences and insufficient_evidence [{case:'UI-C2', state:'phone-checkout', missing:'no journey steps'}] for each role, in the style of tests/test_signing.py's fake reviewer.
- **WHEN** `ui_review.py review --manifest <manifest> --scope checkout --out <dir>` is run
- **THEN** The role files visual.json and behavior.json keep the insufficient_evidence entry, and the signed combined.json has verdict INVALID with the 'INSUFFICIENT-EVIDENCE:' reason naming UI-C2, even though the reviewer said PASS.
<!-- proven by test CP5-T22 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: The combine command tells the reader where to fix an unjudged case
- **GIVEN** Two reviewer files: (a) PASS with an unjudged case UI-C2; (b) a blocker difference plus an unjudged case UI-C2. Outside a run.
- **WHEN** `ui_review.py combine --checkpoint CP5 --inputs ... --out ...` is run for each
- **THEN** For (a) the printed line shows INVALID and says the capture spec for CP5 (states/CP5.json) must be fixed, naming UI-C2. For (b) it shows FAIL and counts only the real difference in the code-fixable count, lists UI-C2 separately for the capture spec, and says changing the spec after a failed round goes through `ask ui-respec`.
<!-- proven by test CP5-T23 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: Reviewers are told about the unjudged list and old reviews still fit the schema
- **GIVEN** The reviewer output schema in ui_review.py (REVIEW_SCHEMA).
- **WHEN** Its properties and required list are read
- **THEN** It declares an optional insufficient_evidence array whose items name case, state and missing; the required list is still reviewer, verdict and differences, so a review without the list stays valid.
<!-- proven by test CP5-T24 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: The new test file is listed in the README's 'Running the tests' list
- **GIVEN** README.md under the 'Running the tests' heading.
- **WHEN** The section is read
- **THEN** It contains a line for 'python3 tests/test_ui_insufficient_evidence.py'.
<!-- proven by test CP5-T25 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

#### Scenario: The test-inventory list covers this test file when it loads or scans engine source
- **GIVEN** This test file's own text and tests/fixtures/modularize_cp1/test-inventory.md.
- **WHEN** Both are read
- **THEN** If this file uses spec_from_file_location or reads or parses ratchet.py or other engine source, test-inventory.md lists it; if it does neither, the check passes without a listing (the planned style is sys.path.insert plus import ui_review).
<!-- proven by test CP5-T26 in tests/test_ui_insufficient_evidence.py, commit 381e313 -->

### Requirement: Tell reviewers and planners the new rules
The system SHALL do this: Reviewers are told to list a case they cannot judge from the evidence as unjudged instead of as a code fix, and the skill and planner docs describe per-state widths, the capture check, stubs and the new verdict.

- The reviewers' input tells them to list a browser case they cannot judge from the evidence as unjudged, never as a difference, and never to use it for a difference they saw
- The behaviour reviewer's own instructions no longer tell it to report an undemonstrable case as a code fix
- The UI compare skill, the test planner's browser-case note and the file formats doc describe per-state width, the capture check, stubs and the unjudged verdict
- The new test file is listed in the README's 'Running the tests' list so anyone can run it
- The new test file is checked against the test-inventory list and added there if it loads or scans engine source, so it is counted with the rest

#### Scenario: Reviewers' input tells them to list a case they cannot judge as unjudged
- **GIVEN** A capture manifest with two states and a test plan holding one browser case (CP2-T07) and one code case
- **WHEN** ui_review.stage() is called for the behavior reviewer with the plan, and INPUT.md is read
- **THEN** Next to the 'Browser-only test cases you must also judge' line, INPUT.md names the insufficient_evidence list and tells the reviewer to put a browser case it cannot judge from the captured evidence there, by case id, with the state and what is missing; a plain 'not demonstrable ... fixable_in_code' wording is not what it asks for
<!-- proven by test CP6-T01 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: Reviewers are told the unjudged list is never a difference and never for one they saw
- **GIVEN** Same staged behavior INPUT.md as the case above
- **WHEN** The instruction text next to the browser-cases line is read
- **THEN** It says a case the reviewer cannot judge is not reported as a difference, and that the list must never be used for a difference the reviewer did see (a seen failure stays a normal difference); the two sentences are matched loosely (a 'never/not' next to 'difference', and 'saw/seen/shows' for the second) so rewording does not break it
<!-- proven by test CP6-T02 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: Only a behavior review with browser cases gets the unjudged instruction; everything else is staged as before
- **GIVEN** The same manifest staged four ways: behavior with a plan that has a browser case, behavior with a plan that has only code cases, behavior with no --cases, and the visual reviewer with the browser plan
- **WHEN** stage() is called each time and INPUT.md is read
- **THEN** Only the first has the insufficient_evidence instruction and browser-cases.json; the other three have neither, and their INPUT.md still lists each state, its screenshots and (for behavior) the existing 'Browser-only test cases' line exactly where it was
<!-- proven by test CP6-T03 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The behaviour reviewer is no longer told to report an undemonstrable case as a code fix
- **GIVEN** agents/ratchet-behavior-reviewer.md as it is now
- **WHEN** The browser-cases.json bullet and the rest of the prompt are read
- **THEN** No sentence pairs 'not demonstrable' (or 'add a journey step for it') with fixable_in_code: true or with a minor difference; the old wording 'case CPn-T07 not demonstrable' as a code fix is gone
<!-- proven by test CP6-T04 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The behaviour reviewer is told to put a case it cannot judge in insufficient_evidence
- **GIVEN** The same prompt file
- **WHEN** Its browser-cases.json bullet and its Output JSON block are read
- **THEN** The bullet sends a case the evidence cannot show to insufficient_evidence (naming case, state and what is missing, and saying the capture spec is what gets fixed), and the Output block lists an insufficient_evidence field next to differences
<!-- proven by test CP6-T05 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The behaviour reviewer prompt keeps its teeth: a seen failure is still a difference
- **GIVEN** The same prompt file
- **WHEN** The browser-cases bullet and the Rules section are read
- **THEN** A case the evidence shows failing is still reported as a difference (with fixable_in_code), insufficient_evidence is stated to be only for a case the evidence cannot show and never a way to turn a seen difference into something else, and the rule 'PASS only when no difference has fixable_in_code: true' is still there
<!-- proven by test CP6-T06 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The UI compare skill explains a state can have its own width
- **GIVEN** skills/ratchet-ui-compare/SKILL.md as it is now
- **WHEN** The capture-spec section (section 2) and the capture section are read
- **THEN** They say a state may carry its own viewport (for example a phone width) that overrides the spec-level width for that state only, that other states keep the spec width or the default, and that the reviewers' input shows each state's width
<!-- proven by test CP6-T07 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The UI compare skill adds the capture check, and scaffold, before capture
- **GIVEN** The same skill file
- **WHEN** The steps between writing the capture spec and capturing are read
- **THEN** A step before capture runs the ui_review.py lint check on the capture spec and the test plan (--spec, --cases), says it prints one warning per browser case with no state, no journey steps or the wrong width, naming the case, and that it only warns; --scaffold is described as adding stub states for what is missing
<!-- proven by test CP6-T08 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The UI compare skill says a stub is marked, unreviewed and never passes
- **GIVEN** The same skill file
- **WHEN** The text about scaffolded states and about starting a review is read
- **THEN** It says a stub state is marked as a stub and unreviewed, that a review whose capture still holds an unfilled stub stops before any reviewer runs, and that a stub never lets the gate pass; the way to clear it (fill in the journey and remove the stub mark) is named
<!-- proven by test CP6-T09 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The UI compare skill sends an unjudged case to the capture spec and keeps real differences going to the implementer
- **GIVEN** The same skill file, section 5 (Record the gate) and the INVALID and FAIL bullets
- **WHEN** The INVALID and FAIL bullets are read
- **THEN** INVALID with INSUFFICIENT-EVIDENCE is said to list the cases case by case and to mean fix states/CPn.json and capture again, not spawn the implementer; FAIL still spawns ratchet-implementer with every reported difference unchanged, and says that when both happen the differences go to the implementer and the unjudged cases are listed separately for the capture spec; 'a gate with an unjudged case never passes' is stated
<!-- proven by test CP6-T10 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The test planner skill's browser-case note tells planners to name the capture state and width
- **GIVEN** skills/ratchet-tdd-planner/SKILL.md as it is now
- **WHEN** Its 'browser' layer bullet (and nothing else in the planner core) is read
- **THEN** The bullet still says to keep browser cases few, and now says a browser case names its ui_state (the capture state that shows it) and its viewport when the width is not the default, and that the capture check (lint) holds the case to that and warns by name when it does not match; the rest of the skill (method, output rules, quality bar) is byte-identical to before
<!-- proven by test CP6-T11 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The file formats doc describes a state's own width, its cases list and the stub mark
- **GIVEN** schemas.md section 5 (capture spec) as it is now
- **WHEN** Section 5's text and its first JSON example are read
- **THEN** The example still parses as JSON, and the section says a state may carry viewport, cases (the test-case ids it demonstrates) and stub, with the stub mark meaning unreviewed and never passing
<!-- proven by test CP6-T12 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The file formats doc describes the insufficient_evidence entry and where it ends up
- **GIVEN** schemas.md section 6 (UI review JSON) as it is now
- **WHEN** Section 6's text is read
- **THEN** A reviewer file may hold an optional insufficient_evidence list of {case, state, missing} kept apart from differences, and combined.json carries a separate insufficient_evidence list; a malformed entry (no case) still blocks
<!-- proven by test CP6-T13 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The file formats doc states the combined verdict order and that real differences keep their teeth
- **GIVEN** schemas.md section 6
- **WHEN** The verdict rule text is read
- **THEN** It says reviewer INVALID stays INVALID; any difference gives FAIL with all differences kept unchanged for the implementer; only with no difference, any insufficient_evidence entry or unfilled stub gives INVALID starting 'INSUFFICIENT-EVIDENCE:'; otherwise PASS; and the existing sentence that ratchet.py recomputes and any fixable_in_code difference blocks is still there
<!-- proven by test CP6-T14 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The orchestrator skill's UI step says to run the capture check and how to read INSUFFICIENT-EVIDENCE
- **GIVEN** skills/ratchet-orchestrator/SKILL.md Step 4 as it is now
- **WHEN** The Gate 2 step and its INVALID and FAIL bullets are read
- **THEN** The flow runs the capture check before capture.mjs; INVALID with INSUFFICIENT-EVIDENCE is said to mean fix the capture spec (not the app code, not the implementer); the existing FAIL bullet still spawns ratchet-implementer with the blocking differences; the change is one line, so the rest of Step 4 is unchanged
<!-- proven by test CP6-T15 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The new test file is listed in the README and is counted correctly against the inventory
- **GIVEN** README.md 'Running the tests' list; tests/fixtures/modularize_cp1/test-inventory.md; this test file
- **WHEN** The list is read and the file's own text is checked for the engine-loading patterns
- **THEN** 'python3 tests/test_ui_gate_fidelity_docs.py' is on its own line of the list; and the file either contains none of the inventory's patterns (it must build any pattern text at run time, and import ui_review via sys.path as tests/test_gates.py does) or is named in the inventory
<!-- proven by test CP6-T16 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The existing UI-gate and doc tests still pass together with the new docs
- **GIVEN** The existing suites that cover the UI gate and the docs: test_gates, test_agents_reviewers, test_agents_docs, test_docs, test_docs_accuracy, plus the five sibling UI-gate suites of this feature (viewport, lint, stub, precheck, insufficient evidence); each is on the README list
- **WHEN** This case runs them as separate processes in parallel with a generous per-suite time limit (never run_all.py and never the whole suite), each in its own temp folder
- **THEN** Every one exits 0 with no 'failed' count, each is named in the README list together with the new docs test file (so this case, like the one before it, is red until the README is updated), and a suite that is missing from the README list or from disk fails the case with its name; slow or hung suites time out with their name
<!-- proven by test CP6-T17 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->

#### Scenario: The docs name exactly what the script now has, so they cannot drift from the code
- **GIVEN** ui_review.py after the earlier steps (imported, not run) and the three skill/format docs
- **WHEN** The script's subcommands, its review schema fields and its verdict text are compared with the docs
- **THEN** Every ui_review.py subcommand that exists (including lint) and the --scaffold flag are named in the UI compare skill, the insufficient_evidence field and the INSUFFICIENT-EVIDENCE verdict prefix are spelled the same in the code, the skill, the reviewer prompt, schemas.md and the orchestrator line, and the stub marker word used in the docs matches the one the code writes into INPUT.md
<!-- proven by test CP6-T18 in tests/test_ui_gate_fidelity_docs.py, commit 7429208 -->
