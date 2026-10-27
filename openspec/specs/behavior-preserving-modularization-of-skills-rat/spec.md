# Behavior-preserving modularization of skills/ratchet-orchestrator/scripts/ratchet.py into coherent modules, keeping ratchet.py as the stable CLI entry point

## Purpose
What this feature does, as built and checked by Ratchet (run behavior-preserving-modularization-of-skills-rat, 2026-10-27). Each scenario names the test that proves it.

## Requirements

### Requirement: Inventory source-inspecting tests and snapshot behavior
The system SHALL do this: Before any code moves, the project has a written list of every test that reads ratchet.py's text/AST or loads it as a module, plus a saved before-snapshot of its visible behavior.

- A committed list (e.g. .ratchet/runs/.../evidence/test-inventory.md) names every test file that reads ratchet.py's source text, parses its AST, or loads it via spec_from_file_location — grep found 43+ files including test_quick_path.py, test_trust_cp5_d.py, test_trust_cp5_e.py, test_quota_shared.py and test_discover_paid.py named in the brief — and what each one checks for
- A saved before-snapshot captures: ratchet.py --help (top-level and a few subcommands), a sample status --json, a sample prompt/stop hook reply, and at least two known failure exit codes
- The complete local suite passes under the suite lock with no code changes yet

#### Scenario: Inventory lists every test file the live grep finds, not a stale hand-picked list
- **GIVEN** the grep patterns for spec_from_file_location and for ENGINE.read_text/ast.parse/ast.walk are re-run live against every tests/*.py file
- **WHEN** the committed inventory (tests/fixtures/modularize_cp1/test-inventory.md) is parsed for the file names it names
- **THEN** every file the live grep finds is present in the inventory (the set difference live-grep-minus-inventory is empty)
<!-- proven by test CP1-T01 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: Inventory names the 5 brief-called-out files, each with what it actually checks for
- **GIVEN** the inventory file
- **WHEN** the entries for test_quick_path.py, test_trust_cp5_d.py, test_trust_cp5_e.py, test_quota_shared.py and test_discover_paid.py are read
- **THEN** all 5 are present and each entry describes the specific invariant that file checks for (e.g. test_quick_path.py's entry names the classify_changes FunctionDef it walks for), not just the bare filename with no description
<!-- proven by test CP1-T02 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: Inventory distinguishes module-loading files from literal source-text/AST-reading files
- **GIVEN** the inventory file
- **WHEN** a file that only dynamically loads ratchet.py (e.g. test_gates.py, via spec_from_file_location) is compared against one that also parses its source text/AST (e.g. test_quick_path.py)
- **THEN** the inventory records which invariant kind applies to each file (module-load dependency vs. source-text/AST dependency) rather than lumping every file into one undifferentiated list, so a later checkpoint knows exactly which obligation it must keep satisfying for that file
<!-- proven by test CP1-T03 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: A test file that matches neither pattern is not listed as source-inspecting
- **GIVEN** tests/test_brief.py, which calls ratchet.py only as a subprocess and matches neither grep pattern
- **WHEN** the inventory is checked for that file's name
- **THEN** tests/test_brief.py is absent from the inventory (no false positive: the inventory only names files that actually match one of the three patterns)
<!-- proven by test CP1-T04 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: Before-snapshot captures --help at the top level and for at least two subcommands
- **GIVEN** the saved before-snapshot folder (tests/fixtures/modularize_cp1/baseline/)
- **WHEN** its captured --help output is read
- **THEN** it contains the top-level `ratchet.py --help` output plus at least two subcommands' --help output (e.g. status --help and plan-summary --help), each non-empty and captured at exit code 0
<!-- proven by test CP1-T05 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: Before-snapshot captures a sample status --json
- **GIVEN** the before-snapshot folder, taken against this repo's own active run
- **WHEN** the saved status --json sample is parsed
- **THEN** it is valid JSON captured verbatim from a real invocation (not hand-written), with the top-level keys ratchet.py's status --json always returns
<!-- proven by test CP1-T06 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: Before-snapshot captures a sample prompt or stop hook reply
- **GIVEN** the before-snapshot folder
- **WHEN** the saved hook reply sample is read
- **THEN** it is valid JSON matching the hook reply shape documented in schemas.md's hooks section, captured from a real hook invocation
<!-- proven by test CP1-T07 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: Before-snapshot captures at least two distinct failure exit codes
- **GIVEN** the before-snapshot folder
- **WHEN** its recorded failure cases are read
- **THEN** at least two different non-zero exit codes are present (not the same code captured twice under different labels), each paired with the command that produced it and its output
<!-- proven by test CP1-T08 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: The before-snapshot is documented as the baseline every later checkpoint's unchanged-behavior proof diffs against
- **GIVEN** the before-snapshot folder and its manifest/README
- **WHEN** that documentation is read
- **THEN** it states plainly that this snapshot is the fixed baseline later checkpoints' byte-identical --help/status/hook/exit-code comparisons are measured against, and names a stable evidence path so a later checkpoint's test can load it directly instead of re-deriving its own copy
<!-- proven by test CP1-T09 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

#### Scenario: The complete local suite passes with zero code changes made yet
- **GIVEN** git diff of ratchet.py, provider_health.py and spec_sources.py against HEAD is empty
- **WHEN** the complete local suite is run under the director's suite lock (tests/run_all.py)
- **THEN** every suite passes, 0 failed, proving the starting point this run's later byte-identical comparisons build from is itself green
<!-- proven by test CP1-T10 in tests/test_modularize_inventory.py, commit d0f7bd1 -->

### Requirement: Extract leaf utilities into engine_utils.py
The system SHALL do this: The small stateless helpers (now, die, ok, find_root, read_json, write_json, slugify, config_from_user) live in their own module, with no change to any command's output.

- engine_utils.py defines now/die/ok/find_root/read_json/write_json/slugify/config_from_user verbatim; ratchet.py imports them by explicit name (no star import)
- ratchet.py --help, status --json, a sample hook reply and at least two known failure exit codes are byte-identical to CP1's baseline
- The complete local suite passes under the suite lock

#### Scenario: The 8 leaf utilities live in engine_utils.py, verbatim, imported into ratchet.py by explicit name
- **GIVEN** engine_utils.py and ratchet.py after the move, and the pinned pre-move source for now/die/ok/find_root/read_json/write_json/slugify/config_from_user
- **WHEN** each function's body in engine_utils.py is diffed against its pre-move body, and ratchet.py's import statements and top-level AST are inspected
- **THEN** all 8 bodies are byte-for-byte unchanged, ratchet.py imports each one by its own name (no `from engine_utils import *`), and none of the 8 names has its own `def` left behind in ratchet.py's top-level AST
<!-- proven by test CP2-T01 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: engine_utils.py imports cleanly on its own, with no import cycle back to ratchet.py
- **GIVEN** the split engine
- **WHEN** engine_utils.py is imported by itself in a fresh interpreter, before ratchet.py is ever imported, and each of the 8 functions is called
- **THEN** the import succeeds with no ImportError and no partially-initialized-module error; walking both modules' import statements finds no cycle (ratchet.py imports engine_utils.py; engine_utils.py never imports ratchet.py back at module load time)
<!-- proven by test CP2-T02 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: The move adds no new global mutable registry
- **GIVEN** engine_utils.py's module-level assignments
- **WHEN** its top-level names are inspected
- **THEN** no new module-level mutable dict/list/set is introduced to hold state across calls; every one of the 8 functions still takes its data as parameters (path, data, text, user) and keeps no cache or counter of its own between calls
<!-- proven by test CP2-T03 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: now() still returns a well-formed, non-decreasing UTC timestamp
- **GIVEN** engine_utils imported directly
- **WHEN** now() is called twice a few milliseconds apart
- **THEN** each result matches an ISO-8601 timestamp with microsecond precision and a UTC offset, is parseable by datetime.fromisoformat, and the second call's value is not earlier than the first
<!-- proven by test CP2-T04 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: die()/ok() keep their exact message shape and exit codes, proven through a real CLI command
- **GIVEN** a temp project with .ratchet/config.json present
- **WHEN** `ratchet.py config --set suite_watch_seconds=-1` (an invalid value) is run, and separately `ratchet.py config --set human_review=each` (a valid value) is run
- **THEN** the invalid case exits with code 2, prints `RATCHET ✗ suite_watch_seconds must be a number of seconds above 0 (got '-1'); the value was not changed.` to stderr, and leaves config.json unchanged; the valid case exits 0, prints `RATCHET ✓ Set human_review = "each"` to stdout, and config.json now has human_review set to "each"
<!-- proven by test CP2-T05 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: read_json/write_json round-trip a plain JSON file, pretty-printed, with a working default for a missing file
- **GIVEN** an empty temp directory and a path to a JSON file that does not yet exist in it
- **WHEN** read_json(path, default={"a": 1}) is called on the missing file, then write_json(path, {"b": 2}) is called, then read_json(path, {}) is called again
- **THEN** the first call returns exactly {"a": 1} with no exception; the file on disk after write_json is indented, human-readable JSON; the final read returns exactly {"b": 2}, read back from what was actually written
<!-- proven by test CP2-T06 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: read_json's two call shapes (person-authored vs. a Ratchet record) keep their different failure behavior on a corrupted file
- **GIVEN** a file holding deliberately invalid JSON text
- **WHEN** read_json(path) is called with no `record=` kwarg, and separately read_json(path, default={}, record="Ratchet treats it as holding nothing") is called on the same file
- **THEN** the first call exits the process (SystemExit) with a stderr message containing "is not valid JSON"; the second call returns {} (the default) with no SystemExit and nothing printed as a fatal error, proving the two shapes still diverge exactly as before the move
<!-- proven by test CP2-T07 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: find_root still resolves nearest-ancestor .ratchet, then git toplevel, then cwd, in that order
- **GIVEN** three fixtures: (a) a directory tree with a .ratchet/ folder partway up, invoked from a nested child directory below it; (b) a git repo with no .ratchet/ anywhere in it; (c) a plain non-git directory with no .ratchet/ anywhere
- **WHEN** find_root(start=that path) is called for each, and separately RATCHET_ROOT is set in the environment while start is left as None
- **THEN** (a) returns the ancestor holding .ratchet/, not the nested start directory; (b) returns the git repo's toplevel (via git rev-parse --show-toplevel); (c) returns the given directory itself; the RATCHET_ROOT env var takes priority over cwd when set
<!-- proven by test CP2-T08 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: slugify keeps its normalization, truncation and empty-input fallback rules, including through ratchet.py init
- **GIVEN** the strings "Fix the Login Bug!!", "   " (all whitespace), a 100-character string, and "already-a-slug"
- **WHEN** slugify(text) is called directly on each, and separately `ratchet.py init --feature "<same string>"` is run for a couple of them
- **THEN** output is lowercased, runs of non [a-z0-9] characters collapse to a single "-", leading/trailing "-" is stripped, the result is truncated to 48 characters (re-stripped of a trailing "-"), an all-whitespace input falls back to "feature", and the CLI's run-folder slug matches the direct call's result exactly
<!-- proven by test CP2-T09 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: config_from_user keeps its deep-merge and stale-old-default-drop rules, proven directly and via ratchet.py config
- **GIVEN** four user dicts: {} (empty), one with a partial override of a nested dict key (e.g. reviewers.host-both), one with a non-dict override of a normally-dict key (e.g. providers given as a list), and one with review_depth set to the exact OLD_DEFAULT_REVIEW_DEPTH sentinel value
- **WHEN** config_from_user(user) is called directly for each, and separately each user dict is written as a temp project's .ratchet/config.json and `ratchet.py config` (no --set) is run
- **THEN** empty input returns exactly DEFAULT_CONFIG; the nested-dict override updates only that key, leaving its siblings from DEFAULT_CONFIG untouched; the non-dict override fully replaces rather than merges that key; the OLD_DEFAULT_REVIEW_DEPTH sentinel is dropped before merging so the new default review_depth wins; the CLI's printed JSON matches the direct call's result in every case
<!-- proven by test CP2-T10 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: --help, status --json, a hook reply and failure exit codes are still byte-identical to CP1's baseline
- **GIVEN** CP1's saved before-snapshot
- **WHEN** ratchet.py --help (top level and the same couple of subcommands), status --json on the same fixture run, a sample prompt/stop hook reply, and the same two known failure cases are run again after this checkpoint's move
- **THEN** every one of these outputs and exit codes is byte-for-byte identical to CP1's saved baseline
<!-- proven by test CP2-T11 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: No source-inspecting test from the CP1 inventory needed re-pointing for this specific move
- **GIVEN** the CP1 inventory's full file list, re-grepped live for spec_from_file_location / ENGINE.read_text / ast.parse / ast.walk / getsource, plus a targeted check of which of those files reference now/die/ok/find_root/read_json/write_json/slugify/config_from_user by name
- **WHEN** that targeted check is run, and tests/test_trust_cp5_d.py, tests/test_trust_cp5_e.py and tests/test_quota_shared.py (the three that reference read_json/write_json by name) are run unmodified
- **THEN** none of the inventoried files asserts these 8 functions are *defined* inside ratchet.py's own text/AST (they only ever reference them as call sites, which stay unchanged in ratchet.py since call sites are not rewritten by this move), so zero files needed re-pointing for CP2, and the three that reference read_json/write_json by name still pass unmodified
<!-- proven by test CP2-T12 in tests/test_modularize_cp2.py, commit 42409d8 -->

#### Scenario: The complete local suite passes after this checkpoint
- **GIVEN** the repository right after CP2's move
- **WHEN** tests/run_all.py is run under the director's suite lock
- **THEN** every suite passes, with no suite newly failing, crashing or being skipped because of the move
<!-- proven by test CP2-T13 in tests/test_modularize_cp2.py, commit 42409d8 -->

### Requirement: Extract fingerprint/git-tree logic into fingerprint.py
The system SHALL do this: The code-fingerprinting and changed-files logic that gates use to decide staleness lives in its own module, unchanged.

- fingerprint.py defines _is_git/_LookFailed/_git_tree_id/fingerprint/_head_sha/_commit_reviewed/_head_commit_named/changed_files/touches_ui verbatim; ratchet.py imports them by explicit name
- A gate that was stale before this move is still stale after it, and one that was fresh stays fresh, on the same sample commits used for CP1's baseline
- ratchet.py --help, status --json, a sample hook reply and at least two known failure exit codes are byte-identical to CP1's baseline
- The complete local suite passes under the suite lock, including any source-inspection test the CP1 inventory flags against this area

#### Scenario: fingerprint.py defines every git-tree helper verbatim, imported by explicit name, with no star import, no circular import and no new global mutable registry
- **GIVEN** fingerprint.py as extracted this checkpoint, with ratchet.py importing from it
- **WHEN** a test imports _is_git, _LookFailed, _git_tree_id, fingerprint, _head_sha, _commit_reviewed, _head_commit_named, changed_files, touches_ui by explicit name from fingerprint.py, and ratchet.py's source text is grepped
- **THEN** every name resolves to the real function/class (not a stub); ratchet.py and engine_utils.py contain no `from fingerprint import *`; fingerprint.py does not import ratchet.py's module (any constant it needs, e.g. RATCHET_DIR/FINGERPRINT_EXCLUDES, is passed in, duplicated as a local constant, or sourced from engine_utils.py instead) so there is no import cycle; fingerprint.py defines no new module-level mutable dict/list that ratchet.py also reads or writes directly
<!-- proven by test CP3-T01 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: _is_git and fingerprint() tell a git working tree apart from a plain directory
- **GIVEN** one fixture directory that is a git repo with a commit, and one plain temp directory with the same files, no .git
- **WHEN** _is_git(root) and fingerprint(root) are called on each
- **THEN** _is_git is True for the git dir and False for the plain dir; fingerprint() returns {"kind": "git-tree", "id": ...} for the git dir and {"kind": "manifest", "id": ...} for the plain dir, writing the manifest under .ratchet/manifests/<id>.json
<!-- proven by test CP3-T02 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: fingerprint()/_git_tree_id give the same tree id as a manual git write-tree on the same commit
- **GIVEN** a git fixture repo checked out at a known sample commit (the same one used for CP1's baseline)
- **WHEN** fingerprint(root)["id"] is compared against a hand-run git write-tree on an index seeded the same way (tracked + untracked, minus .ratchet and the junk pathspecs)
- **THEN** the two ids are byte-identical, proving the moved function computes exactly the same tree id as before the move
<!-- proven by test CP3-T03 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: changed_files reports nothing for an unedited tree and exactly one path for one edited tracked file
- **GIVEN** fingerprint A taken at the sample commit; one tracked file is then edited; fingerprint B taken after
- **WHEN** changed_files(root, A, A) and changed_files(root, A, B) are both called
- **THEN** the first call returns []; the second returns a list containing exactly the one edited file's repo-relative path and nothing else
<!-- proven by test CP3-T04 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: touches_ui matches a file against a ui_globs pattern and correctly ignores a non-matching file
- **GIVEN** a changed-files list ["src/components/Button.tsx", "src/server/db.py"] and ui_globs ["*.tsx", "components/*"]
- **WHEN** touches_ui(files, ui_globs) is called
- **THEN** it returns exactly ["src/components/Button.tsx"], never including src/server/db.py
<!-- proven by test CP3-T05 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: _commit_reviewed commits exactly the checkpoint's own changes, and a retry recognizes the commit it already made
- **GIVEN** a git fixture repo with a recorded base_tree, one file changed since, and fingerprint() taken now
- **WHEN** _commit_reviewed(root, base_tree, fp, msg) is called once, then _head_sha(root) is read, then _head_commit_named(root, msg) is called again with no further change
- **THEN** the first call returns a short commit id whose tree contains exactly the one changed file and nothing else from the working tree; _head_sha returns that commit's full sha; the second call recognizes HEAD already carries that exact message and returns the same short id instead of making a duplicate commit
<!-- proven by test CP3-T06 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: ratchet.py fingerprint's CLI output is unchanged by the move
- **GIVEN** the same fixture repo at the same sample commit used for CP1's before-snapshot
- **WHEN** `ratchet.py fingerprint` is run before and after this checkpoint's extraction
- **THEN** the two JSON outputs are byte-identical ({"kind": "git-tree", "id": "<same id>"})
<!-- proven by test CP3-T07 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: A gate that was stale before the move is still stale after it
- **GIVEN** a project fixture whose behavior gate already passed and recorded its fingerprint, then a tracked source file is edited afterward (the same edit CP1's baseline used)
- **WHEN** `ratchet.py complete CP1` is run
- **THEN** it exits non-zero, refuses with the exact existing text "code changed since behavior passed", and status --json shows that gate's status flipped to "stale" -- identical to the pre-move baseline
<!-- proven by test CP3-T08 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: A gate that was fresh before the move stays fresh after it
- **GIVEN** the same project fixture, behavior gate passed, with no file changes made afterward
- **WHEN** `ratchet.py complete CP1` is run
- **THEN** it proceeds past the staleness check (no "code changed since behavior passed" refusal) and the gate's status stays "passed", matching the pre-move baseline
<!-- proven by test CP3-T09 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: A UI gate flips stale only when the edited file actually matches ui_globs, true and false
- **GIVEN** a project fixture with the UI gate required and already passed, ui_globs set to ["*.tsx"]
- **WHEN** first a non-UI file (src/server/db.py) is edited and complete is run against one copy of the fixture, then a UI file (src/components/Button.tsx) is edited and complete is run against a fresh copy
- **THEN** the non-UI edit leaves the UI gate "passed" with no "UI files changed" refusal; the UI edit refuses with "UI files changed since UI review" and flips that gate to "stale" -- both outcomes identical to the pre-move baseline
<!-- proven by test CP3-T10 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: ratchet.py --help, status --json, a hook reply and two known failure exit codes stay byte-identical to CP1's baseline
- **GIVEN** CP1's saved before-snapshot (evidence/baseline/) and the same sample commands/fixtures it used
- **WHEN** the same commands are re-run after CP3's extraction
- **THEN** every output byte-matches CP1's baseline text, and the same two known failure exit codes reproduce exactly
<!-- proven by test CP3-T11 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: test_quick_path.py still passes, with its AST check finding classify_changes unmodified in ratchet.py
- **GIVEN** classify_changes and the shared _classify_doc_code helper it calls are outside this checkpoint's move list and stay in ratchet.py verbatim
- **WHEN** `python3 tests/run_all.py tests/test_quick_path.py` is run after CP3's extraction
- **THEN** every case in it passes, including the AST-walk case that locates a top-level FunctionDef named classify_changes in ratchet.py's own source text calling _classify_doc_code -- unmodified and never re-pointed, because the function it inspects never moved
<!-- proven by test CP3-T12 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

#### Scenario: The complete local suite passes under the suite lock after this move
- **GIVEN** fingerprint.py extracted, ratchet.py importing it by explicit name, and tests/test_modularize_cp3.py plus tests/test_quick_path.py in place
- **WHEN** `flock /tmp/ratchet-lanes-suite.lock python3 tests/run_all.py` runs the full suite
- **THEN** every suite passes with 0 failures, including the 43+ source-inspection suites CP1's inventory flags and specifically tests/test_quick_path.py
<!-- proven by test CP3-T13 in tests/test_modularize_cp3.py, commit 8a0b6fd -->

### Requirement: Extract tamper-evidence and record locking into records.py
The system SHALL do this: The chain-of-custody and lock classes that make every shared record tamper-evident live in their own module, with identical locking and detection behavior.

- records.py defines _chain_path/_read_record/_write_record/_append_record/_note_unreadable/_BreakerLock/_ChainLock/_RecordLock/_record_lock_path/update_record/_chain_record/_record_chain_now/record_chain/append_recorded/chain_problem/verify_chain verbatim; ratchet.py imports them by explicit name, and classes defined elsewhere (_EngineNotesLock, _StateLock, _IntegrateLock) still subclass them correctly
- tests/test_trust_cp5_d.py and tests/test_trust_cp5_e.py's lock-stealing, stale-lock-race and per-record-lock checks pass unmodified or re-pointed to records.py without weakening what they check
- The structural scan for unconsumed update_record() results and for direct (lock-bypassing) writes to shared records (test_quota_shared.py, test_trust_cp5_e.py) is re-pointed to scan records.py as well as ratchet.py, so a violation introduced in either file is still caught
- ratchet.py --help, status --json, a sample hook reply and at least two known failure exit codes are byte-identical to CP1's baseline
- The complete local suite passes under the suite lock

#### Scenario: records.py's moved definitions are byte-identical to before the move
- **GIVEN** CP1's saved baseline copy of ratchet.py, which still contains _chain_path, _read_record, _write_record, _append_record, _note_unreadable, _BreakerLock, _ChainLock, _RecordLock, _record_lock_path, update_record, _chain_record, _record_chain_now, record_chain, append_recorded, chain_problem and verify_chain
- **WHEN** each of those names is looked up in records.py after the move and its AST/source text is diffed against the matching definition in the baseline copy
- **THEN** every one of the 16 named definitions exists in records.py with an identical body (no line added, removed or reworded beyond the move itself)
<!-- proven by test CP4-01 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: ratchet.py imports the moved names explicitly, with no star import and no circular import
- **GIVEN** ratchet.py's source after the move
- **WHEN** its import statements for records.py are inspected
- **THEN** it imports each of the 16 names by explicit name (e.g. `from records import update_record, chain_problem, ...`), there is no `from records import *` anywhere, and records.py imports nothing from ratchet.py (so `import records` alone, and `import ratchet` alone, both succeed with no import-time error)
<!-- proven by test CP4-02 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: the move introduces no new module-level mutable registry
- **GIVEN** records.py's and ratchet.py's module-level assignments before and after the move
- **WHEN** the set of module-level mutable collections (dict/list/set literals assigned at module scope) is compared before and after
- **THEN** the only ones carried into records.py are the ones that already existed before the move (_NOTED_UNREADABLE, _CHAIN_REBUILD), unchanged in shape and initial value, and no brand-new shared mutable collection is created to make the move easier
<!-- proven by test CP4-03 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: every lock subclass still subclasses correctly after the move
- **GIVEN** the split engine, loaded as a module
- **WHEN** the class hierarchy is inspected
- **THEN** _ChainLock subclasses _BreakerLock, _RecordLock subclasses _ChainLock, _EngineNotesLock subclasses _RecordLock, _StateLock subclasses _BreakerLock, and _IntegrateLock subclasses _StateLock — all still true with records.py's classes imported into ratchet.py's namespace
<!-- proven by test CP4-04 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: --help is byte-identical to CP1's baseline
- **GIVEN** CP1's saved --help output (top-level and the same few subcommands it captured)
- **WHEN** ratchet.py --help (and those same subcommands' --help) is run again after this checkpoint's move
- **THEN** the output is byte-for-byte identical to CP1's baseline, with exit code 0
<!-- proven by test CP4-05 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: status --json is byte-identical to CP1's baseline sample
- **GIVEN** CP1's saved sample status --json output, taken from a project in the same state
- **WHEN** status --json is run again on a project set up the same way after this checkpoint's move
- **THEN** the JSON is byte-for-byte identical to CP1's baseline (same keys, same values, same key order), with the same exit code
<!-- proven by test CP4-06 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: a sample hook reply is byte-identical to CP1's baseline
- **GIVEN** CP1's saved sample prompt/stop hook reply, taken from the same scripted input
- **WHEN** the same hook input is replayed through `ratchet.py hook prompt` / `hook stop` after this checkpoint's move
- **THEN** stdout, stderr and exit code are all byte-for-byte identical to CP1's baseline
<!-- proven by test CP4-07 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: known failure exit codes are unchanged
- **GIVEN** CP1's baseline record of at least two known failure scenarios and their exact exit codes
- **WHEN** each of those same scenarios is reproduced after this checkpoint's move
- **THEN** each one fails with the identical exit code and the identical (or equivalently worded, if the baseline notes wording is allowed to vary) refusal text as CP1's baseline
<!-- proven by test CP4-08 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: two processes racing to update the same shared record never lose an entry
- **GIVEN** two real subprocesses that both read a shared record already wired through update_record (e.g. active-runs.json via register_active, or the per-user hash record) before either writes
- **WHEN** both are released to write their own entry at the same moment
- **THEN** both entries are present afterward, no exception is raised by either process, and no stray lock or temp file is left behind — exactly the no-lost-entry guarantee the pre-move code gives today
<!-- proven by test CP4-09 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: a stale lock is cleared by exactly one of two competing processes
- **GIVEN** a record's lock file aged past the stale threshold by a process that is really gone, and two fresh processes that both try to take the same lock at once
- **WHEN** both race to judge the lock stale and clear it via the breaker-file protocol
- **THEN** exactly one of the two ends up holding the lock (the one that judged it stale and removed it first); the other finds it already gone and does not also hold it; and once both are done, neither the lock file nor its `.break` file is left behind
<!-- proven by test CP4-10 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: a command that can't take a live lock waits briefly then refuses, claiming nothing was saved
- **GIVEN** a shared record's lock file held by a genuinely live process (a fresh token, not stale)
- **WHEN** a command that needs to update that record is run
- **THEN** it waits a few seconds, then exits non-zero, names the lock file in its refusal, never claims the change was saved, and the record on disk is byte-for-byte unchanged from before the call
<!-- proven by test CP4-11 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: releasing a lock whose token no longer matches this process leaves the file alone
- **GIVEN** a lock file this process created, whose contents were then rewritten to a different process's token (simulating another process having legitimately taken it over after a stale-break)
- **WHEN** the original process calls its own release path
- **THEN** the lock file is left exactly as it is (still holding the other token) — it is never deleted — because a release never removes a lock that might not be its own
<!-- proven by test CP4-12 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: a hand-edited record is caught as tampered
- **GIVEN** a file Ratchet has recorded the hash of in file-hashes.json, then overwritten directly on disk (not through ratchet.py)
- **WHEN** chain_problem (or verify_chain, through a command/hook that calls it) is asked about that file
- **THEN** it reports the mismatch plainly, naming the file and pointing at the accept-state recovery path, and the caller refuses to trust the file (dies, or the hook denies) rather than silently accepting the edit
<!-- proven by test CP4-13 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: a damaged hash record fails safe instead of vouching for anything
- **GIVEN** file-hashes.json replaced with invalid JSON bytes
- **WHEN** chain_problem/verify_chain is asked about any tracked file while the hash record is in that state
- **THEN** it refuses to vouch for the file (a damaged record grants trust to nothing), no hook or command crashes or raises, and the refusal says plainly that the hash record could not be read and names the accept-state recovery path
<!-- proven by test CP4-14 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: the lock-bypassing direct-write scan now also catches a bypass placed inside records.py
- **GIVEN** the structural scan for a direct (lock-bypassing) write to a shared record (cp5r13_shared_write_violations, used by test_quota_shared.py and test_trust_cp5_e.py), re-pointed to parse records.py's own source in addition to ratchet.py's
- **WHEN** a probe function that writes a shared record's path directly (not through update_record) is inserted into records.py's source and the scan is run against it, alongside the existing ratchet.py-only probe
- **THEN** the scan flags the bypass wherever it is written (records.py or ratchet.py), a legitimate update_record-based write in either file is still never flagged, and the real, unmodified records.py plus ratchet.py source together produce zero violations
<!-- proven by test CP4-15 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: the unconsumed-update_record-result scan now also covers records.py
- **GIVEN** the structural scan for a bare, result-discarding call to update_record (cp5r16_unconsumed_update_record_calls), re-pointed to parse records.py's own source in addition to ratchet.py's
- **WHEN** a probe function with a bare `update_record(...)` statement is inserted into records.py's source and the scan is run against it, alongside the existing ratchet.py-only probe
- **THEN** the scan flags the bare call wherever it appears (records.py or ratchet.py), a call whose result is assigned, returned or compared is never flagged, and the real, unmodified records.py plus ratchet.py source together produce zero violations
<!-- proven by test CP4-16 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: test_trust_cp5_d.py's lock-stealing and stale-lock-race checks pass against the split code
- **GIVEN** tests/test_trust_cp5_d.py, re-pointed wherever it reads ratchet.py's source text or AST for anything that moved into records.py (unmodified where it only calls through the loaded module, since those names are still reachable on it by explicit import)
- **WHEN** its lock-stealing check (two processes clearing the same stale lock, [CP5-X54]) and its per-write temp-file-naming check ([CP5-X55]) are run
- **THEN** both pass with the exact same pass/fail meaning as before the split — no assertion loosened, no case skipped
<!-- proven by test CP4-17 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: test_trust_cp5_e.py's per-record-lock, structural and busy-dies checks pass against the split code
- **GIVEN** tests/test_trust_cp5_e.py, re-pointed wherever it reads ratchet.py's source text or AST for anything that moved into records.py
- **WHEN** its forced-interleave per-record-lock race ([CP5-X70]), its shared-write-violation structural scan ([CP5-X71]), its busy-lock-dies-plainly checks ([CP5-X72], [CP5-X73]) and its unconsumed-update_record-result checks ([CP5-X75], [CP5-X76]) are run
- **THEN** all pass with the exact same pass/fail meaning as before the split — no assertion loosened, no case skipped
<!-- proven by test CP4-18 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: test_quota_shared.py's shared-record-lock case passes re-pointed to also cover records.py
- **GIVEN** tests/test_quota_shared.py's [CP2-X11] case, which asserts ratchet.py writes provider-health.json only inside update_record and demonstrates the check has teeth (a locked race keeps both entries; a lock-less race loses one)
- **WHEN** the case's structural scan is re-pointed to also parse records.py's source (so update_record's own definition and helpers are covered too), and the case is run
- **THEN** it still passes, the locked race still keeps both entries, the lock-less race still loses one (proving the check is not vacuous), and the scan over records.py plus ratchet.py together still reports zero violations
<!-- proven by test CP4-19 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

#### Scenario: the complete local suite passes after this checkpoint
- **GIVEN** the repository with records.py extracted and every re-pointed test updated
- **WHEN** tests/run_all.py is run for the whole suite under the director's suite lock
- **THEN** every suite passes (0 failed), matching the fully-green state recorded before this run started — no other checkpoint's or suite's behavior regresses because of this move
<!-- proven by test CP4-20 in tests/test_modularize_cp4.py, commit 9b9dfeb -->

### Requirement: Extract provider discovery and quota logic
The system SHALL do this: The reviewer-provider discovery, probing, quota and fallback bookkeeping moves into provider_discovery.py, alongside the existing provider_health.py; the providers command behaves the same.

- provider_discovery.py defines probe_provider/_record_probe/quota_failure/quota_retest_minutes/provider_share/bench_quota/record_round_failure/review_succeeded/probe_due/probe_shared/update_review_starts/_reserve_under_lock/refund_review_start/provider_bench/shared_unavailable/event_stream and related helpers verbatim; cmd_providers stays in ratchet.py as the thin CLI handler and calls the new module by explicit name
- `ratchet.py providers` output (including --add) is byte-identical to CP1's baseline on the same fixtures
- ratchet.py --help, status --json, a sample hook reply and at least two known failure exit codes are also byte-identical to CP1's baseline
- tests/test_quota_shared.py, tests/test_discover_paid.py, tests/test_discover_omp.py, tests/test_providers.py and tests/test_quota_share.py pass, with any source-inspection check among them re-pointed to cover provider_discovery.py
- The complete local suite passes under the suite lock

#### Scenario: Provider discovery and quota functions live in their own module, called by explicit name
- **GIVEN** the engine after CP5's move
- **WHEN** provider_discovery.py is read and ratchet.py's imports of it are inspected
- **THEN** provider_discovery.py defines probe_provider, _record_probe, quota_failure, quota_retest_minutes, provider_share, bench_quota, record_round_failure, review_succeeded, probe_due, probe_shared, update_review_starts, _reserve_under_lock, refund_review_start, provider_bench, shared_unavailable and event_stream with their bodies unchanged from the pinned pre-move commit, cmd_providers still lives in ratchet.py as a thin handler, and every one of these names is imported into ratchet.py explicitly (no `from provider_discovery import *`)
<!-- proven by test CP5-T01 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: provider_discovery.py imports cleanly on its own, with no import cycle
- **GIVEN** the split engine
- **WHEN** provider_discovery.py is imported by itself, in a fresh interpreter, with no prior import of ratchet.py
- **THEN** the import succeeds with no ImportError, and walking both modules' import statements finds no cycle (ratchet.py may import provider_discovery.py; provider_discovery.py never imports ratchet.py back)
<!-- proven by test CP5-T02 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: The move adds no new global mutable registry
- **GIVEN** provider_discovery.py's module-level assignments
- **WHEN** its top-level names are inspected
- **THEN** no new module-level mutable dict/list/set is introduced to hold provider state across calls; every function still takes its data (ctx, cfg, a provider record) as a parameter and reads/writes provider-health.json and unavailable-reviewers.json only through the existing locked record helpers, matching provider_health.py's own documented 'pure record logic, reads and writes no file itself' shape
<!-- proven by test CP5-T03 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: ratchet.py providers output is unchanged after the move
- **GIVEN** the two-provider fixture config and CP1's saved providers-command transcripts
- **WHEN** `ratchet.py providers` (plain list), `providers --add` for the flat-rate preset, `providers --add` for the per-use preset (with the recorded paid-provider OK), and `providers --check <id>` are each run
- **THEN** each command's stdout, stderr and exit code are byte-identical to CP1's baseline for the same fixture, and provider-status.json/provider-health.json/unavailable-reviewers.json end up byte-for-byte the same as before the move
<!-- proven by test CP5-T04 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: --help, status --json, a hook reply and failure exit codes are still byte-identical
- **GIVEN** CP1's saved before-snapshot
- **WHEN** ratchet.py --help, status --json on this project's active run, a sample prompt/stop hook reply, and the two known failure cases (e.g. no active run, a busy record lock) are run again after CP5's move
- **THEN** every one of these outputs and exit codes matches CP1's baseline exactly, including that a benched provider still shows up the same way in status --json's unavailable_reviewers section
<!-- proven by test CP5-T05 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: Reviewer ranking and order is unchanged: cost tier, prefer_for, then rotation
- **GIVEN** the fixture config's flat-rate and per-use providers, one with --prefer-for set to a lens, and provider-health.json seeded with different recent start counts for each
- **WHEN** a review round is resolved for that lens, before and after the move
- **THEN** the same reviewer is picked in the same order both times: the flat-rate tier is tried before the per-use tier, the provider that prefers this lens is tried before one that doesn't within the same tier, and between two otherwise-equal providers the one with fewer recent starts (then the one picked longest ago) is tried first
<!-- proven by test CP5-T06 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: provider_share's window limit still blocks an over-share, including across two lanes racing to reserve at once
- **GIVEN** a provider already holding provider_share.max review starts inside provider_share.minutes
- **WHEN** one more round tries to open with that provider, and separately two lanes call _reserve_under_lock for the same provider at the same moment
- **THEN** the single round is refused the provider the same way as before the move, and the two racing lanes' reservations are serialized under the shared record's lock so together they never reserve more than provider_share.max starts on it, exactly as before
<!-- proven by test CP5-T07 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: probe_shared: one lane's real test request is reused by a lane that asks at the same moment
- **GIVEN** a provider with no fresh test result, and the fake provider CLI scripted to take a few seconds to answer
- **WHEN** two lanes concurrently need the same provider tested via probe_shared
- **THEN** the fake CLI is called exactly once, the second lane's probe_shared waits on the first lane's claim and returns the same (good, detail) the first lane got, and provider-health.json ends with one recorded result and no leftover claim, matching the pre-move behavior
<!-- proven by test CP5-T08 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: A quota failure benches the provider for its own stated reset time, and only a real review ends it
- **GIVEN** the fake provider CLI replying with quota-exceeded wording that names a reset time
- **WHEN** probe_provider sends a test request, and later a real review by that provider is sent after the bench began and succeeds (review_succeeded)
- **THEN** the provider is benched with quota=True until the time it named (falling back to quota_retest_minutes when it gave none or a past one), a later passing test request alone does not end that bench, and review_succeeded ends it once the real review after the bench began succeeds — the exact pre-move rule
<!-- proven by test CP5-T09 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: record_round_failure records a round's failure once, and leaves a moved-on round alone
- **GIVEN** an open adversarial round with reviewers rids for checkpoint cp_id
- **WHEN** record_round_failure is called for that round, then called again after the round has already moved to a new round number
- **THEN** the first call records the failed providers/reviewers under the state lock exactly as before the move; the second call (a stale round) changes nothing and still reports success, matching the documented idempotency
<!-- proven by test CP5-T10 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: Every provider-discovery source-inspecting test is re-pointed and still catches a real violation
- **GIVEN** tests/test_quota_shared.py, tests/test_discover_paid.py, tests/test_discover_omp.py, tests/test_providers.py and tests/test_quota_share.py after the move
- **WHEN** the full set runs, including test_trust_cp5_e.py's cp5r13_shared_write_violations structural scan for direct, lock-bypassing writes to provider-health.json
- **THEN** every one of these suites passes, and the structural scan is confirmed to also scan provider_discovery.py's source (not only ratchet.py's): a hand-written stray write to provider-health.json planted in provider_discovery.py is flagged by the scan, the same as one planted in ratchet.py
<!-- proven by test CP5-T11 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: The complete local suite passes after this checkpoint
- **GIVEN** the repository right after CP5's move
- **WHEN** tests/run_all.py is run under the director's suite lock
- **THEN** every suite passes, with no suite newly failing, crashing or being skipped because of the move
<!-- proven by test CP5-T12 in tests/test_modularize_cp5.py, commit 65001a8 -->

#### Scenario: run_all.py never lets one report-writing suite's results clobber another's when a gate's tests.command runs several suites together
- **GIVEN** two report-writing test files given to run_all.py in the same invocation with one shared RATCHET_TEST_REPORT path set in the parent environment (the exact shape of CP5's own tests.command, which runs test_modularize_cp5.py alongside the pre-existing test_quota_share.py), each suite writing its own distinct case ids to $RATCHET_TEST_REPORT
- **WHEN** run_all.py runs both suites to completion, with --workers set so their write order is forced in each direction (first-then-second and second-then-first), proven on both this run's live run_all.py and (via `git show`) the pre-fix pinned copy from before this checkpoint's fix
- **THEN** on the pre-fix pinned run_all.py the second suite to write always destroys the first suite's cases from the final report (the exact CP5 defect); on the fixed run_all.py the final report at the gate's RATCHET_TEST_REPORT path contains every case from BOTH suites, regardless of write order, and a pre-existing single-report-suite tests.command (e.g. CP4's own) still produces byte-identical report content to before the fix
<!-- proven by test CP5-T13 in tests/test_modularize_cp5.py, commit 65001a8 -->

### Requirement: Extract run bookkeeping, hook-env and integrity checks
The system SHALL do this: The per-run and per-user bookkeeping — hook-installed detection, active-run registration, reply classification and integrity-snapshot verification — moves into run_records.py, behaving exactly as before.

- run_records.py defines _set_blocked/ratchet_home/the hook-env path & record helpers/record_hook_env/hooks_installed_anywhere/hooks_in_use/hook_env_problem/require_hook_env/the active-run marker helpers/register_active/the reply classifiers (choice_letter, message_intent, hooks_waive_intent, is_listed_yes)/the integrity helpers (approval_snapshot, integrity_problems, verify_integrity) verbatim; ratchet.py imports them by explicit name
- The exact refusal text and exit code for 'hook not installed', 'hook record looks stale', and 'two runs claim the same project' match CP1's baseline
- ratchet.py --help, status --json, a sample hook reply and at least two known failure exit codes are also byte-identical to CP1's baseline
- tests/test_hooks.py, tests/test_hooks_waiver.py, tests/test_trust_cp1.py, tests/test_trust_cp4.py, tests/test_trust_cp5_a.py, tests/test_trust_cp5_b.py, tests/test_trust_cp5_c.py, tests/test_lanes_start_extra.py, tests/test_lanes_request.py and tests/test_signing.py pass, with any source-inspection check among them re-pointed to cover run_records.py
- The complete local suite passes under the suite lock

#### Scenario: Every named function moves to run_records.py byte-identical, and ratchet.py imports each one by its own name
- **GIVEN** The pinned pre-move commit of ratchet.py, and the working tree after this checkpoint's move
- **WHEN** The AST body of each of _set_blocked, ratchet_home, the hook-env path & record helpers (_hook_env_path, _same_path, _current_hook_env, _hook_role, _hook_env_records, _combined_hook_env), record_hook_env, hooks_installed_anywhere, hooks_in_use, hook_env_problem, require_hook_env, the active-run marker helpers (_markers_path, _restriction_record_damaged, _active_runs_repair, _active_marker), register_active, choice_letter, message_intent, hooks_waive_intent, is_listed_yes, approval_snapshot, integrity_problems and verify_integrity is diffed between the pinned commit and run_records.py
- **THEN** every body is textually identical (whitespace-insignificant diff) in its new location, each one is removed from ratchet.py's own top-level AST, and ratchet.py's import of run_records is a plain `from run_records import <name>, <name>, ...` list with none of them wildcarded (`import *` is absent)
<!-- proven by test CP6-T01 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: ratchet.py --help (top-level and a few subcommands) is byte-identical to CP1's baseline
- **GIVEN** CP1's saved --help output for the top-level command and the same handful of subcommands it sampled
- **WHEN** The same --help invocations are run against the post-move ratchet.py
- **THEN** stdout, stderr and exit code are byte-identical to CP1's saved capture
<!-- proven by test CP6-T02 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: status --json is byte-identical to CP1's baseline on the same fixture
- **GIVEN** CP1's saved status --json sample and the exact fixture project it was captured from
- **WHEN** status --json is run again against that same fixture, after the move
- **THEN** the JSON output is byte-identical to CP1's saved sample
<!-- proven by test CP6-T03 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: A sample prompt/stop hook reply is byte-identical to CP1's baseline
- **GIVEN** CP1's saved sample hook reply (the exact input and host that produced it)
- **WHEN** The same hook event is replayed against the post-move engine
- **THEN** the emitted hook reply (block flag and message) and its exit code are byte-identical to CP1's saved sample
<!-- proven by test CP6-T04 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: At least two known failure exit codes are unchanged
- **GIVEN** CP1's two saved failure scenarios and their recorded exit codes
- **WHEN** The same scenarios are reproduced after the move
- **THEN** each one exits with the exact same code as CP1's baseline
<!-- proven by test CP6-T05 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: hook-env.json read/write round-trips identically before and after the move
- **GIVEN** A fresh temp project and a guard hook call with a fixed host, event and start time
- **WHEN** record_hook_env writes its per-run record and hook-env.json is rebuilt, then hook_env_problem reads it back
- **THEN** the written hook-env.json (home, key_file, guard_role, at) and hook_env_problem's verdict (None, since it agrees) are identical to the pre-move engine's output for the same inputs
<!-- proven by test CP6-T06 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: doctor --json's hooks.enforcing and hooks.fresh fields are unchanged for a fresh and a stale heartbeat
- **GIVEN** A temp project with a recent guard/stop heartbeat entry (fresh case) and a second temp project with no heartbeat or a 25-hour-old one (stale case)
- **WHEN** doctor --json is run against each project after the move
- **THEN** hooks.installed, hooks.heartbeat_seen, hooks.fresh, hooks.enforcing and hooks.prompt_hook match the pre-move engine's output field for field, proving the heartbeat logic (which stays in ratchet.py, out of this move's scope) is undisturbed
<!-- proven by test CP6-T07 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: 'Hook not installed' refusal text and exit code are unchanged
- **GIVEN** A project with hooks never installed and never run, where a command requires a hook record
- **WHEN** That command is run after the move
- **THEN** the refusal text matches CP1's baseline word for word and the exit code is unchanged
<!-- proven by test CP6-T08 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: 'Hook record looks stale' refusal text and exit code are unchanged
- **GIVEN** A project with an unresolved hook crash (or unreadable hook-crash state) that makes the existing hook-env.json record untrusted
- **WHEN** A command that calls hook_env_problem(doubt=True) runs after the move
- **THEN** the refusal names the same cause in the same wording as CP1's baseline, including the RECOVERY_CMDS list, and the exit code is unchanged
<!-- proven by test CP6-T09 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: 'Two runs claim the same project' is still detected and reported the same way
- **GIVEN** Two temp runs that each try to register the same project path in the per-user active-runs.json, one while the other holds the record lock
- **WHEN** register_active is called by the second run while the first holds the lock, after the move
- **THEN** the busy-lock stderr message and the resulting active-runs.json content are identical to the pre-move engine's output, and no entry is silently dropped or overwritten without that message
<!-- proven by test CP6-T10 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: Reply classifiers return identical verdicts on a fixed battery of sample replies
- **GIVEN** The fixed battery of sample replies (clear yes, clear no, hedge word mixed with 'yes', bare 'ok', exact hooks-waiver phrase, near-miss waiver phrase, emoji thumbs-up, non-ASCII reply)
- **WHEN** choice_letter, message_intent, hooks_waive_intent and is_listed_yes are called on every sample, before and after the move
- **THEN** every verdict is identical; in particular no sample that is NOT a listed approval or waiver phrase newly reads as approve/waive, and no listed phrase newly reads as unclear
<!-- proven by test CP6-T11 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: Integrity checks still catch a post-approval change and approve-changes still clears it, unchanged
- **GIVEN** An approved plan with its approval_snapshot saved in state.json, then one of: a checkpoint edit in checkpoints.json, a test plan edit in tests/CPn.json, or a changed config rule
- **WHEN** A command that calls verify_integrity runs, then the developer runs approve-changes, after the move
- **THEN** the first run dies with the same 'These changed after the user approved the plan: ...' wording (including the director-enrolled variant) and exit code as CP1's baseline, and approve-changes clears it exactly as before
<!-- proven by test CP6-T12 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: run_records.py has no circular import with ratchet.py
- **GIVEN** The post-move skills/ratchet-orchestrator/scripts/ directory
- **WHEN** run_records.py is imported standalone (python3 -c "import run_records" run from that directory, with no other ratchet module already loaded) and ratchet.py is imported separately
- **THEN** run_records.py imports successfully without importing ratchet.py (its own top-level import statements, read by AST, name no module called ratchet), and ratchet.py's own import of run_records succeeds with no ImportError either direction
<!-- proven by test CP6-T13 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: The move adds no new global mutable registry
- **GIVEN** run_records.py's full top-level AST
- **WHEN** Every top-level assignment in run_records.py is inspected
- **THEN** none of them creates a new module-level dict/list/set used as mutable shared state (constants, regexes and simple re-bindings of already-existing names are fine); any mutable module-level state that existed in ratchet.py before the move (e.g. _IN_HOOK) stays a single definition, not duplicated into both files
<!-- proven by test CP6-T14 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: The hook handlers themselves stayed in ratchet.py, not moved
- **GIVEN** ratchet.py's and run_records.py's post-move source
- **WHEN** run_hook, decide_prompt, decide_guard, decide_stop, emit_hook, normalize_hook, read_heartbeat, _heartbeat, _heartbeat_path, hooks_status, cmd_doctor and cmd_hooks_waive are located by name in both files' top-level AST
- **THEN** every one of them is still defined in ratchet.py and none of them is defined in run_records.py, proving this checkpoint moved only bookkeeping/integrity code and never crossed into the hook handlers (out of scope)
<!-- proven by test CP6-T15 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: Dynamically loading ratchet.py as a module still exposes every moved function by name
- **GIVEN** ratchet.py loaded via importlib.util.spec_from_file_location, the same pattern 37+ existing tests use
- **WHEN** record_hook_env, hook_env_problem, register_active, choice_letter, message_intent, hooks_waive_intent, is_listed_yes, approval_snapshot, integrity_problems, verify_integrity, _set_blocked, ratchet_home, hooks_installed_anywhere, hooks_in_use and require_hook_env are read off the loaded module object and called
- **THEN** each one is present as a module attribute (the explicit-name import re-exports it) and returns the same result as calling the same function directly from run_records.py with the same arguments
<!-- proven by test CP6-T16 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: The ten named regression files pass unmodified after the move
- **GIVEN** tests/test_hooks.py, tests/test_hooks_waiver.py, tests/test_trust_cp1.py, tests/test_trust_cp4.py, tests/test_trust_cp5_a.py, tests/test_trust_cp5_b.py, tests/test_trust_cp5_c.py, tests/test_lanes_start_extra.py, tests/test_lanes_request.py and tests/test_signing.py, none of them edited by this checkpoint
- **WHEN** each one is run under tests/run_all.py after the move, including test_trust_cp4.py's `m.record_hook_env(...)` call and test_trust_cp5_a.py's `m._set_blocked(...)` call through the dynamically loaded ratchet.py module
- **THEN** every one passes exactly as it did before the move, proving CP1's inventory found no source-text/AST check in them that targets a CP6-moved function's literal location (only module-attribute access, which the explicit-name re-export satisfies)
<!-- proven by test CP6-T17 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: test_trust_cp4.py's human_override/log_override caller-set scan is unaffected by the move
- **GIVEN** test_trust_cp4.py's CP4-X06/X14 structural AST scan, which records which function names in ratchet.py call human_override and log_override
- **WHEN** That scan runs after CP6's move
- **THEN** it still finds exactly {_require_approval, cmd_accept_state, cmd_init, cmd_choice} as the call sites (none of them are moved by CP6), proving the move stayed inside its declared boundary and did not disturb code this earlier checkpoint's test depends on
<!-- proven by test CP6-T18 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: Earlier checkpoints' tamper-evidence, locking and provider behavior are unaffected
- **GIVEN** A lock-contention scenario from CP4's area (two processes racing update_record on the same shared record) and a `ratchet.py providers` smoke call from CP5's area
- **WHEN** Both are run after CP6's move, which only touches run bookkeeping/hook-env/integrity, never records.py or provider_discovery.py
- **THEN** the lock-contention outcome and the providers output are unchanged from the CP5-baseline behavior, confirming CP6 did not regress a prior checkpoint's extraction
<!-- proven by test CP6-T19 in tests/test_modularize_cp6.py, commit 69cb405 -->

#### Scenario: The complete local suite passes under the suite lock after this checkpoint
- **GIVEN** The repository state right after CP6's move lands
- **WHEN** flock /tmp/ratchet-lanes-suite.lock python3 tests/run_all.py is run
- **THEN** every suite passes (0 failed), matching or improving on the pre-CP6 green baseline
<!-- proven by test CP6-T20 in tests/test_modularize_cp6.py, commit 69cb405 -->

### Requirement: Extract plan validation and earlier-work digest
The system SHALL do this: The plan/objective validation rules and the 'What earlier work says' digest builder move into plan_support.py; brief and plan-submit behave the same.

- plan_support.py defines objective_problems/_proves_values/proves_problems/objective_brief/_normalize_role and the digest helpers (_digest_words, _learnings_bullets, _related_spec_items, _earlier_work_lines, etc.) verbatim; cmd_brief and cmd_plan_submit stay in ratchet.py as thin handlers calling the new module by explicit name
- ratchet.py brief --role planner output and plan-submit's accept/refuse messages are byte-identical to CP1-style samples on the same fixtures
- ratchet.py --help, status --json, a sample hook reply and at least two known failure exit codes are also byte-identical to CP1's baseline
- tests/test_dm_table.py, tests/test_dm_accept.py, tests/test_digest_docstring.py and tests/test_brief_guard.py pass, with any source-inspection check among them re-pointed to cover plan_support.py
- The complete local suite passes under the suite lock

#### Scenario: A plan with no objective at all is refused with the exact missing-objective message
- **GIVEN** a one-checkpoint plan whose top-level dict has no "objective" key
- **WHEN** plan-submit is run on it
- **THEN** it is refused (non-zero exit) and the message is the literal 'objective: missing. Every plan needs one, e.g. ...' text objective_problems has always produced, unchanged after the move to plan_support.py
<!-- proven by test CP7-T01 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: An objective missing users, goal, done_means or out_of_scope is refused naming each missing field
- **GIVEN** four variant plans, each with an objective dict missing exactly one of users/goal/done_means/out_of_scope (empty string or empty list counts as missing)
- **WHEN** plan-submit is run on each
- **THEN** each is refused, and the refusal's line for that variant is the exact 'objective.<field>: missing or empty (...)' wording objective_problems has always produced for that field, with no other field's line appearing
<!-- proven by test CP7-T02 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: A case's "proves" naming a line that doesn't exist is refused by case id
- **GIVEN** an otherwise-valid plan whose objective.done_means has 3 lines, and one checkpoint's test-plan case carries "proves": 0, another "proves": 99, and another "proves": [1, 99]
- **WHEN** plan-submit is run
- **THEN** it is refused and the message names each offending case by id and the out-of-range number, stating objective.done_means has 3 line(s), exactly as proves_problems has always worded it
<!-- proven by test CP7-T03 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: A valid plan whose every done_means line is proved by some case is accepted
- **GIVEN** the full sample plan where every objective.done_means line is named by some case's "proves" across the plan's test files
- **WHEN** plan-submit is run
- **THEN** it is accepted (phase moves to awaiting_plan_approval / the plan is saved) with the same accept message plan-submit has always printed, and the plan is persisted unchanged
<!-- proven by test CP7-T04 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: A bare scalar "proves": 1 is normalized like [1]; a non-numeric shape is refused by name
- **GIVEN** one case with "proves": 1 (bare int, not a list) and, in a second variant plan, one case with "proves": "1" (string) and another with "proves": {"a": 1} (dict)
- **WHEN** plan-submit is run on each variant
- **THEN** the bare-int variant is accepted exactly as if it had written "proves": [1] (line 1 is linked, no refusal for that case), while the string and dict variants are refused, each naming the offending case id and its exact "proves" value as 'must be a done_means line number or a list of them'
<!-- proven by test CP7-T05 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: brief --role planner prints the objective, proof notes and the earlier-work digest, byte-identical pre/post move
- **GIVEN** the seeded project (8+ learnings bullets, an accepted-limit bullet, a deferred finding in another run, an openspec entry and a done BMAD story) with an approved plan reaching 'building'
- **WHEN** `ratchet.py brief CP1 --role planner` is run before the move (saved as a byte-for-byte reference capture) and again after plan_support.py exists
- **THEN** stdout is byte-identical in both runs: the header, the objective block with its numbered done-means lines and 'Proved by'/'Heads up' notes, the planner's ROLE_BRIEFS rules, and the 'What earlier work says' section with its learnings/open-findings/accepted-limits/related-specs sub-sections, each capped at 5 items with a correct '... and N more' count and the newest-and-most-relevant-first order
<!-- proven by test CP7-T06 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: brief for a non-planner role never prints the earlier-work digest
- **GIVEN** the same seeded project and approved plan
- **WHEN** `ratchet.py brief CP1 --role implementer` is run
- **THEN** stdout has the objective block and the implementer's own ROLE_BRIEFS rules only; the string 'What earlier work says' never appears, matching brief's pre-move behavior
<!-- proven by test CP7-T07 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: _normalize_role still accepts a ratchet-prefixed name and a role alias, and refuses an unknown one the same way
- **GIVEN** no plan needed
- **WHEN** `brief --role ratchet-fixer` (prefix form of 'fixer'), `brief --role adversary` (ROLE_ALIASES renames to 'reviewer') and `brief --role not-a-real-role` are each run
- **THEN** the first two exit 0 and print the fixer's / reviewer's rules respectively (identical text to calling with the bare name), and the third exits non-zero with the exact 'is not a valid role for brief. Valid roles: ...' message listing ROLE_ORDER, unchanged after the move
<!-- proven by test CP7-T08 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: The learnings section of the digest caps at 5 and ranks newest/most-relevant first, identically pre/post move
- **GIVEN** the seeded project's 8+ dated learnings.md bullets, where the active run's own 'feature' text shares a word with some bullets and not others
- **WHEN** `brief CPn --role planner` is run before and after the move
- **THEN** both runs show the same 5 bullets in the same order (word-overlap bullets before non-overlap ones, newest-by-date first within each group) followed by the same '... and N more - see .ratchet/learnings.md' line
<!-- proven by test CP7-T09 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: Accepted limits, other runs' open findings and related specs render identically; an empty project gets the plain fallback line
- **GIVEN** (a) the seeded project's other-run deferred finding, accepted-limit bullet, openspec entry and done BMAD story, and (b) a freshly-initialized project with none of that
- **WHEN** `brief CPn --role planner` is run on both, before and after the move
- **THEN** for (a) each sub-section (open findings from other runs, accepted limits, Related specs:) prints the same items in the same order in both runs, with the BMAD 'done' story included and a not-done one excluded; for (b) the digest is the same single plain 'no earlier work' line in both runs, never empty sub-headings
<!-- proven by test CP7-T10 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: plan_support.py is imported by explicit name, has no circular dependency on ratchet.py, and introduces no new mutable module-level registry
- **GIVEN** the post-move source tree
- **WHEN** ratchet.py's import line(s) for plan_support are parsed, `python3 -c "import plan_support"` is run standalone (plan_support.py's own directory on sys.path, ratchet.py not imported first), and plan_support.py's AST is scanned for `global` statements
- **THEN** ratchet.py's import is a plain `from plan_support import name1, name2, ...` (never `import *`, matching every name objective_problems/_proves_values/proves_problems/objective_brief/_normalize_role/the digest helpers are called by), `import plan_support` alone succeeds with exit 0 and no ImportError (it does not need ratchet.py to already be imported), and the AST scan finds zero `global` statements referencing a module-level dict/list used across multiple functions as shared mutable state
<!-- proven by test CP7-T11 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: test_digest_docstring.py's ast-based docstring check is re-pointed to plan_support.py and still enforces the exact rule
- **GIVEN** the post-move source tree, where `_related_spec_items` is now a top-level FunctionDef in plan_support.py and no longer one in ratchet.py
- **WHEN** test_digest_docstring.py's CP3-T01/CP3-T02 helper (`_func_docstring`) is run
- **THEN** it reads plan_support.py (not ratchet.py) and still finds `_related_spec_items`'s docstring there, and that docstring still states the exact-match, case-insensitive, whitespace-tolerant 'done' rule (no 'contains done' wording); CP3-T03's direct call to `rt._related_spec_items(...)` still passes unmodified, since ratchet.py re-exports the name by explicit import
<!-- proven by test CP7-T12 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: test_dm_table.py, test_dm_accept.py and test_brief_guard.py pass unmodified after the move
- **GIVEN** none of these three files read ratchet.py's source text or AST (confirmed by grep: they only use test_gates.py's subprocess-driven `rx`/`guard`/`approve_plan` helpers and a dynamic module load of ratchet.py by file path to call its already-public `record_chain`, which is unaffected by this move)
- **WHEN** the three suites are run against the post-move tree with no edits to them
- **THEN** all pass: the proven/unproven done-means table (test_dm_table.py), the accept-line flow for an unproven line (test_dm_accept.py), and the sub-agent-launch objective guard plus its brief-output fixtures (test_brief_guard.py) all still work end to end through the relocated objective_brief/proves_problems/_normalize_role functions
<!-- proven by test CP7-T13 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: --help, status --json, a sample hook reply and two known failure exit codes stay byte-identical
- **GIVEN** CP1's saved before-snapshot and the same sample commands/fixtures used to produce it
- **WHEN** `ratchet.py --help` (top-level and the `plan-submit`/`brief` subcommands), `status --json` on the sample run, a sample `hook prompt`/`hook stop` reply, and the two known failure exit codes (e.g. an invalid plan's exit code and an unknown-CPn's exit code) are captured again after this checkpoint's move
- **THEN** every one of them matches the baseline byte-for-byte (stdout, stderr and exit code)
<!-- proven by test CP7-T14 in tests/test_modularize_cp7.py, commit 30b2b55 -->

#### Scenario: The complete local suite passes under the suite lock, and this checkpoint's diff touches only the expected files
- **GIVEN** the repo as left by CP6, with only this checkpoint's edits on top
- **WHEN** `flock ... python3 tests/run_all.py` (the full suite, under the director's suite lock) is run, and the checkpoint's changed-files list is diffed against CP6's tree
- **THEN** the full suite exits clean (0 failed suites) and every changed file is skills/ratchet-orchestrator/scripts/plan_support.py, skills/ratchet-orchestrator/scripts/ratchet.py, or one of tests/test_modularize_cp7.py / test_dm_table.py / test_dm_accept.py / test_digest_docstring.py / test_brief_guard.py -- no other module file is touched
<!-- proven by test CP7-T15 in tests/test_modularize_cp7.py, commit 30b2b55 -->

### Requirement: Extract plain-language status and next-action text
The system SHALL do this: The plain-English status, next-action and blocked-options rendering moves into status_text.py; status and next read identically to a developer's agent.

- status_text.py defines plain_reason/blocked_options/plain_now/waiting_question/question_line/exit_code_only_line/exit_code_only_note and the done-means-link helpers verbatim; cmd_status_text, cmd_plan_summary and next_action stay in ratchet.py as thin handlers calling the new module by explicit name
- ratchet.py status, next and plan-summary print byte-identical text to CP1's baseline on the same fixtures
- ratchet.py --help, a sample hook reply and at least two known failure exit codes are also byte-identical to CP1's baseline
- tests/test_status_plain.py, tests/test_split_check.py and tests/test_related_tests_fixes.py pass, with any source-inspection check among them re-pointed to cover status_text.py
- The complete local suite passes under the suite lock

#### Scenario: status_text.py defines every named function, body unchanged
- **GIVEN** the pinned pre-move ratchet.py source and the built tree's skills/ratchet-orchestrator/scripts/status_text.py
- **WHEN** the test AST-parses both and compares, by name, plain_reason, blocked_options, plain_now, waiting_question, question_line, exit_code_only_line, exit_code_only_note and the done-means-link helpers (_lines_word, done_means_table, _finish_block_lines, _finish_blockers, _finish_held, _finish_legacy_note, _finish_integrity_lines, _unproven_finish_lines, _accepted_lines, _apply_accepted_lines)
- **THEN** every one of those names is defined, exactly once, in status_text.py, with a body identical (verbatim) to the pinned source; none is left behind in ratchet.py as a second definition
<!-- proven by test CP8-T01 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: cmd_status_text, cmd_plan_summary and next_action stay thin handlers in ratchet.py
- **GIVEN** the built tree's ratchet.py
- **WHEN** the test AST-parses ratchet.py and inspects the bodies of cmd_status_text, cmd_plan_summary and next_action
- **THEN** each still lives in ratchet.py (not moved), and each calls into status_text.py's functions by their explicit dotted or imported name rather than reimplementing any of the rendering logic inline
<!-- proven by test CP8-T02 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: status_text.py is imported explicitly, with no circular import
- **GIVEN** the built tree
- **WHEN** the test AST-scans ratchet.py's imports of status_text (none may be `from status_text import *`) and separately imports status_text.py on its own, with ratchet.py not yet imported
- **THEN** ratchet.py's import of status_text names each symbol it uses (or imports the module and qualifies every use), and status_text imports cleanly standalone with no import of ratchet.py, proving the dependency runs one way only
<!-- proven by test CP8-T03 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: status_text.py adds no new global mutable registry
- **GIVEN** status_text.py's module-level statements
- **WHEN** the test AST-walks its top level
- **THEN** every top-level assignment is a constant (str/int/bool/tuple/frozenset) or a plain function/class definition; no top-level dict(), list() or set() literal that other code could later import and mutate is introduced
<!-- proven by test CP8-T04 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: `status` plain text is byte-identical across every phase
- **GIVEN** the fixture run driven through planning, awaiting_plan_approval, building, awaiting_checkpoint_review, awaiting_final_review (0 unproven), done, paused and cancelled
- **WHEN** `ratchet.py status` (no --json) is run at each phase before the move and again after
- **THEN** stdout is byte-for-byte identical at every phase, including the "Step N of M" wording, the review-ready sentence, the paused/cancelled/finished fixed sentences and the hooks-skipped/exit-code-only lines where they apply
<!-- proven by test CP8-T05 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: `next`'s plain-language action string is byte-identical across every phase
- **GIVEN** the same fixture run as CP8-T05
- **WHEN** `ratchet.py next` is run at each phase before and after the move
- **THEN** the human-readable text fields of the response (the plain sentence a non-engineer reads, built from plain_now/waiting_question/question_line) are byte-identical; this case does not check the machine fields (see CP8-T13)
<!-- proven by test CP8-T06 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: `plan-summary` is byte-identical
- **GIVEN** the 6-step mixed-risk fixture plan, both while planning and once approved
- **WHEN** `ratchet.py plan-summary` is run before and after the move
- **THEN** stdout is byte-for-byte identical: each step's title, first done-when line, "UI review: yes|no", "Risk: ..." line, reviewer ids, the "Proved by: ..." / "Heads up" done-means notes, and the exit-code-only note on the step with no per-test report
<!-- proven by test CP8-T07 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: blocked `status`'s lettered options are byte-identical for every block reason
- **GIVEN** the blocked-run fixtures (behavior gate failed, adversarial rounds used with and without raise-limit already used, run-wide round cap reached, generic reason)
- **WHEN** `ratchet.py status` is run blocked on each reason, before and after the move
- **THEN** the quoted plain_reason sentence and every lettered option (A)/B)/C)/D)) are byte-for-byte identical in text, order and count on each reason, including that the raise-limit option is offered only when it has not already been used for that step
<!-- proven by test CP8-T08 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: the finish-pause done-means block is byte-identical, held and clear
- **GIVEN** the awaiting_final_review fixture with 2 unproven lines (one behind an open finding, one with no test linked), the accepted-line and held-again-line fixtures, and the legacy-plan (pre-done-means-rules) fixture with one unproven line
- **WHEN** `ratchet.py status` and `ratchet.py next` are run on each of these four fixtures before and after the move
- **THEN** the held-finish block (count of unproven lines, each line's number/text/reasons, the open-finding mark-fixed pointer, the "ways forward" lines and the accept-line pointer), the accepted-line and held-again text, and the legacy note on the old-style plan are all byte-for-byte identical; the held fixture never prints the invitation to approve, and the 0-unproven fixture from CP8-T05 still does
<!-- proven by test CP8-T09 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: a waiting question's plain line is byte-identical for every kind, and for a director lane
- **GIVEN** the pending-question fixtures (approve-plan, human-approve, accept-finding, raise-limit, accept-line, a non-stopping building-phase choice) asked of a plain user lane and again of a director-enrolled lane
- **WHEN** `ratchet.py status`/`ratchet.py next` are run on each before and after the move
- **THEN** question_line's text (who it waits on, what it is about, the "will not move" vs "can carry on" tail) is byte-for-byte identical for every kind and for both lane types, including the director-only accept-finding/raise-limit command text from _blocked_accept_note
<!-- proven by test CP8-T10 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: the exit-code-only note is byte-identical for both variants
- **GIVEN** the report:none-with-reason step and the older plan with no report key
- **WHEN** `ratchet.py status`/`ratchet.py plan-summary` are run on each before and after the move, while that step is building
- **THEN** exit_code_only_line/exit_code_only_note's text, including the quoted report_reason on the first variant and the generic "does not say" wording on the second, is byte-for-byte identical
<!-- proven by test CP8-T11 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: the hook-crash plain lines on `status` are byte-identical
- **GIVEN** the single-recent-crash, crash-then-clean, two-hooks-broken and damaged-hook-crash-state fixtures
- **WHEN** `ratchet.py status` is run with each hook-errors.log/crash-state fixture in place, before and after the move
- **THEN** plain_hook_errors' lines (how long ago, which check, whether it is still broken, the doctor-check ask, and the unreadable/unsaved-record lines) are byte-for-byte identical on every fixture
<!-- proven by test CP8-T12 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: `status --json` and `next --json` carry the same fields and values
- **GIVEN** every fixture used in CP8-T05 through CP8-T10
- **WHEN** `ratchet.py status --json` and `ratchet.py next` are run before and after the move and both sides' JSON is parsed
- **THEN** done_means_table, waiting_question, blocked_reason, options, finish_blockers and every other machine field have identical values (order-independent) on both sides; this case fails if the module move changed the JSON shape or any value, confirming the move is rendering-only, not a change to the machine contract
<!-- proven by test CP8-T13 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: a sample hook reply is byte-identical
- **GIVEN** a running build with a pending prompt and a pending stop hook check
- **WHEN** `ratchet.py hook prompt` and `ratchet.py hook stop` are invoked with the same input before and after the move
- **THEN** both hooks' replies (JSON payload to the agent) are byte-for-byte identical
<!-- proven by test CP8-T14 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: known failure exit codes are unchanged
- **GIVEN** an empty directory with no active run, and a run with an unknown checkpoint id passed to `complete`
- **WHEN** `ratchet.py status --json` is run in the empty directory, and `ratchet.py complete <bogus-id>` is run on the active fixture, before and after the move
- **THEN** both commands exit with the same non-zero code and print the same plain refusal text ("No active Ratchet run." and the unknown-checkpoint refusal) as before the move
<!-- proven by test CP8-T15 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: test_status_plain.py, test_split_check.py and test_related_tests_fixes.py keep passing, re-pointed if they inspect the moved code
- **GIVEN** the built tree
- **WHEN** `python3 tests/test_status_plain.py`, `python3 tests/test_split_check.py` and `python3 tests/test_related_tests_fixes.py` are run, and the test checks each file's own source for any `ast.parse`/`.read_text()` reference naming the functions moved in CP8-T01
- **THEN** all three suites pass with 0 failed, and any reference among them that names a moved function's location now points at status_text.py instead of ratchet.py; one that only calls the function through a dynamically-loaded module attribute (not a source/AST scan) needs no change
<!-- proven by test CP8-T16 in tests/test_modularize_cp8.py, commit 57897a2 -->

#### Scenario: the complete local suite passes after this checkpoint
- **GIVEN** the built tree with status_text.py in place
- **WHEN** `python3 tests/run_all.py` is run under the director's suite lock
- **THEN** every suite passes, 0 failed
<!-- proven by test CP8-T17 in tests/test_modularize_cp8.py, commit 57897a2 -->

### Requirement: Write the module map for maintainers
The system SHALL do this: One concise doc lists every module created in this run and what it owns, so the next person (or agent) knows where to look.

- A new 'Module map' section (schemas.md or a short references/modules.md) names engine_utils.py, fingerprint.py, records.py, provider_discovery.py, run_records.py, plan_support.py and status_text.py, one line each on what each owns and which ratchet.py commands call into it
- README.md and skills/ratchet-orchestrator/SKILL.md gain a one-line pointer to this module map where they currently describe ratchet.py as a single script
- The doc also names, in one line, which areas (hooks, cmd_init, cmd_lanes_start/cmd_integrate, cmd_ask, director approvals, receipts, spec tools, shortcuts, viewer, CI export) are still inside ratchet.py, deferred to a later feature
- The complete local suite passes under the suite lock with no code changes, only docs

#### Scenario: The module map doc exists somewhere a maintainer would find it
- **GIVEN** CP8's seven new module files already exist under skills/ratchet-orchestrator/scripts/
- **WHEN** the test looks for a 'Module map' section in skills/ratchet-orchestrator/references/schemas.md or a skills/ratchet-orchestrator/references/modules.md file
- **THEN** exactly one of those locations exists and is non-empty
<!-- proven by test CP9-T01 in tests/test_modularize_cp9.py, commit 4c77a2c -->

#### Scenario: The doc names every module this run actually created
- **GIVEN** the module map doc found in CP9-T01
- **WHEN** the test checks its text for each of engine_utils.py, fingerprint.py, records.py, provider_discovery.py, run_records.py, plan_support.py and status_text.py
- **THEN** all seven filenames appear by exact name; none is missing and no eighth 'new module' name is invented
<!-- proven by test CP9-T02 in tests/test_modularize_cp9.py, commit 4c77a2c -->

#### Scenario: Each module gets a real one-line description, not just a bare filename
- **GIVEN** the module map doc
- **WHEN** the test looks at the line(s) naming engine_utils.py, fingerprint.py, records.py, provider_discovery.py, run_records.py, plan_support.py and status_text.py
- **THEN** each of those lines also states what moved into that module (e.g. the function/area it owns) and which ratchet.py command or handler calls into it, so a filename alone without any description fails this case
<!-- proven by test CP9-T03 in tests/test_modularize_cp9.py, commit 4c77a2c -->

#### Scenario: The doc lists what is still deferred and stays inside ratchet.py
- **GIVEN** the module map doc
- **WHEN** the test checks its text for a one-line mention of the out-of-scope areas: hooks, cmd_init, cmd_lanes_start/cmd_integrate, cmd_ask, director approvals, receipts, spec tools, shortcuts, viewer and CI export
- **THEN** each of those areas is named as still living in ratchet.py, deferred to a later feature
<!-- proven by test CP9-T04 in tests/test_modularize_cp9.py, commit 4c77a2c -->

#### Scenario: The doc never claims an out-of-scope area was moved out
- **GIVEN** the module map doc
- **WHEN** the test scans it for any claim that hooks, cmd_init, cmd_lanes_start, cmd_integrate, cmd_ask, director approvals/signing, receipts, spec tools, shortcuts, the viewer or CI export now live in their own module
- **THEN** no such claim is present, and no module name outside the seven real ones from CP9-T02 is listed as created by this run
<!-- proven by test CP9-T05 in tests/test_modularize_cp9.py, commit 4c77a2c -->

#### Scenario: README points a reader to the module map
- **GIVEN** README.md's existing description of skills/ratchet-orchestrator/scripts/ratchet.py
- **WHEN** the test checks README.md for a line that names or links the module map doc's actual location (schemas.md's Module map section, or references/modules.md)
- **THEN** that pointer line is present and names the real location found in CP9-T01, not a made-up path
<!-- proven by test CP9-T06 in tests/test_modularize_cp9.py, commit 4c77a2c -->

#### Scenario: The orchestrator SKILL.md points a reader to the module map
- **GIVEN** skills/ratchet-orchestrator/SKILL.md's existing description of ratchet.py
- **WHEN** the test checks that file for a line that names or links the module map doc's actual location
- **THEN** that pointer line is present and names the real location found in CP9-T01
<!-- proven by test CP9-T07 in tests/test_modularize_cp9.py, commit 4c77a2c -->

#### Scenario: This checkpoint changes docs only, so the complete suite it runs under stays green
- **GIVEN** CP8's tree, with only this checkpoint's edits on top (tracked via git diff --name-only against the CP8 commit)
- **WHEN** the test lists every file this checkpoint changed
- **THEN** every changed file is a doc (schemas.md and/or references/modules.md, README.md, SKILL.md) and no .py file anywhere under skills/ratchet-orchestrator/scripts/ changed, which is exactly why this checkpoint's own gate run of the complete local suite (tests/run_all.py, under the director's suite lock) is expected to stay green with no code behavior at risk
<!-- proven by test CP9-T08 in tests/test_modularize_cp9.py, commit 4c77a2c -->
