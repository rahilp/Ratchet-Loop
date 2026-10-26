# CP1 evidence: test files that inspect ratchet.py's on-disk source or load it as a module

Re-derived live, not a hand-picked list. This file lists what the live grep below found, by name,
but deliberately carries no frozen "raw output" snapshot of grep's own stdout: a frozen snapshot
would contradict the whole point of being re-derived live, and would go stale (e.g. when
__pycache__ isn't clean, or a new test file starts/stops matching). CP1-T01..T04 re-run the two
greps below against the live working tree every time and check this doc against THAT result, not
against any number written here. Re-run this exact pair of commands yourself to see today's real
file lists:

```
grep -rlE 'spec_from_file_location' tests/.            # module-load pattern
grep -rlE 'ENGINE\.read_text|ast\.parse|ast\.walk|getsource' tests/.   # source-text/AST pattern
```

(`tests/test_modularize_inventory.py` itself is excluded from both results: it only mentions
these pattern strings as documentation/constants, with no real dependency on ratchet.py's
on-disk shape. This very file, `tests/fixtures/modularize_cp1/test-inventory.md`, is NOT
excluded from the live grep — it legitimately matches the source-text/AST pattern too, purely
because it quotes the regex literals above and names the matching tokens (`ast.parse`,
`getsource`, etc.) throughout the Kind 2/3 entries below as documentation, with no real
dependency on ratchet.py's on-disk shape either. `tests/__pycache__/` must be clean when this
grep runs — a stale `.pyc` left by an unrelated concurrent test run, or by importing a test
module directly, can itself contain the pattern strings as embedded constants and falsely
match; this doc's counts were taken right after `rm -rf tests/__pycache__
tests/shortcuts/__pycache__`.)

42 files match one or both live patterns today (Kind 1 + Kind 2 + Kind 3 below). Plus 2 more
files the brief names explicitly (`test_quota_shared.py`, `test_discover_paid.py`, Kind 4 below)
that carry the identical real obligation but don't literally match either regex today — **44
files total**, consistent with the brief's "43+".

## Kind 1 — module-load only (loads ratchet.py dynamically via `importlib.util.spec_from_file_location`,
never inspects its on-disk text directly): 31 files

- `test_gates.py` — gate-integrity suite; the shared `ENGINE`/`g` module alias almost every other
  suite below imports instead of loading ratchet.py a second time itself. Module-load-only known
  fixture (CP1-T03's own `MODULE_LOAD_ONLY_EXAMPLE`): loads ratchet.py once (`ratchet_engine_t`)
  and never inspects its on-disk text or tree shape at all.
- `feedback_fixes_cases.py` — CP7 extra fix-round cases for `test_feedback_fixes.py` (imported, not
  run as its own `test_*.py`); its generated child-process snippet calls
  `spec_from_file_location` to load ratchet.py inside a sub-case.
- `shortcuts/guard_runner.py` — runner for `tests/shortcuts/guard.json`; loads the *named version's*
  ratchet.py via `spec_from_file_location` to call `_guard_shell` directly for one shortcut-table row.
- `stale_lock_ns_cases.py` — CP2 extra cases for `test_stale_lock.py` (imported, not its own
  `test_*.py`); same generated-snippet dynamic-load pattern, for lock pid-namespace edge cases.
- `test_accept_finding.py` — CP3: the user accepts open review findings; loads ratchet.py
  dynamically twice (`ratchet_cp3x02`, `ratchet_cp3x05`) to drive engine internals directly.
- `test_brief_guard.py` — CP3: sub-agents launched without the objective are refused; loads
  ratchet.py dynamically inside a generated snippet to exercise the brief guard.
- `test_discover_omp.py` — CP3: provider discovery also finds Oh My Pi; loads ratchet.py
  dynamically (`ratchet_cp3_omp`) to call discovery functions against a fake `omp` CLI.
- `test_dm_accept.py` — CP5: the user accepts an unproven done-means line; loads ratchet.py
  dynamically (`ratchet_cp5x18`).
- `test_dm_table.py` — CP1: the final review shows a proven/unproven done-means table; loads
  ratchet.py dynamically inside a generated snippet.
- `test_flaky.py` — CP6: one flaky failure doesn't cost a round; loads ratchet.py dynamically
  (`ratchet_external_review_test`, via an `_ilu` alias) to call the gate-retry logic directly.
- `test_guardrails.py` — a repeated finding becomes an automatic guardrail check; loads ratchet.py
  dynamically twice (`eng_rm` via a `probs` alias, `eng_g` via an `iu` alias).
- `test_hooks_waiver.py` — CP2: the hooks-waive choice is recorded only from a real reply; loads
  ratchet.py dynamically inside a generated snippet.
- `test_integrate_files.py` — CP3: `integrate <lane>` merges Ratchet's own shared files by
  Ratchet's rules; loads ratchet.py dynamically (`ratchet_engine_for_cp3`).
- `test_integrate_safety.py` — CP2 safety tests for `integrate <lane>`; loads ratchet.py
  dynamically twice (`ratchet_engine_x10`, `ratchet_engine_safety`, via a `th` alias) to check the
  scratch-copy/fast-forward safety rules.
- `test_lanes_request.py` — CP8: `lanes --request` tracks a request's lanes through to merged;
  loads ratchet.py dynamically inside a generated snippet.
- `test_lanes_start_extra.py` — CP7 fix-round: a failed `git worktree add` leaves nothing behind;
  loads ratchet.py dynamically (`eng`) to call the cleanup path directly.
- `test_providers.py` — reviewer-choice and review-rules suite; loads ratchet.py dynamically
  (`ratchet_engine_tp`) to call provider-discovery/choice internals.
- `test_quick_path_records.py` — CP5: a full review clears skipped-review notes; loads ratchet.py
  dynamically inside a generated snippet.
- `test_quota_share.py` — CP6: a returning provider's health is shared across lanes; loads
  ratchet.py dynamically (`ratchet_engine_stale_view`) to check the shared provider-health record.
- `test_receipt.py` — CP2: `ratchet.py receipt` output; loads ratchet.py dynamically inside a
  generated snippet.
- `test_related_tests_fixes.py` — CP3 fix-round: related tests run before the full suite; loads
  ratchet.py dynamically (`ratchet_engine_cp3_fixes`, via an `rt` alias).
- `test_shortcuts_extra.py` — CP6 extra structural test for the shortcut table; loads ratchet.py
  dynamically (`ratchet_engine_under_test`, via `g.ENGINE`).
- `test_signing.py` — CP7: review scripts sign the reviews they write; loads ratchet.py dynamically
  twice (`ratchet_engine_x12` directly, and `ratchet_engine` inside a generated snippet).
- `test_stale_lock.py` — CP2: a provably-gone lock clears itself, a live one is respected; loads
  ratchet.py dynamically (`ratchet_cp2_stale`) to call the lock classes directly.
- `test_status_plain.py` — CP3: plain-language status/plan-summary/blocked-options text; loads
  ratchet.py dynamically twice (`ratchet_engine_sp` directly, and inside a generated snippet).
- `test_stop_on_change_fixes.py` — CP4 fix-round: the code watch under a running suite; loads
  ratchet.py dynamically (`ratchet_engine_cp4_fixes`, via a `tt` alias).
- `test_trust_cp1.py` — trust-holes CP1: a crashed safety hook shows up in status/next/doctor;
  loads ratchet.py dynamically inside two generated snippets (`ratchet_child`).
- `test_trust_cp5_a.py` / `test_trust_cp5_b.py` / `test_trust_cp5_c.py` — trust-holes CP5 parts
  a/b/c: approvals answer the exact question Ratchet asked; each loads ratchet.py dynamically
  inside a generated snippet (`module_from_spec`), sharing helpers from `trust_helpers.py`.
- `trust_helpers.py` — shared helpers for every trust-hole suite (cp1, cp4, cp5_a..e) and for
  `test_results.py`/`test_signing.py`; loads ratchet.py dynamically twice itself
  (`ratchet_engine_trust`, `ratchet_cp5_round2`), and is itself `import`ed (not run directly) by
  every file above that uses it.

## Kind 2 — source-text/AST only (reads ratchet.py's literal on-disk text and/or walks its parsed
tree, never loads it as a module itself): 5 files

- `test_quick_path.py` — CP2 (quick-path change classification). Source-text known fixture
  (CP1-T03's own `SOURCE_TEXT_EXAMPLE`): line 478-480, `src = g.ENGINE.read_text(encoding="utf-8")`;
  `ast.parse(src, ...)`; walks for the top-level `FunctionDef` literally named `classify_changes`.
  Breaks if that function moves to another module without updating this test.
- `test_cleanup.py` — CP1: every suite cleans up its temp folders; `ast.parse`+`ast.walk` over
  whichever test file's source it is given (including its own) to find raw
  `tempfile.mkdtemp`/`mkstemp` calls that should use the shared `_tmp` helper instead. Its target
  isn't ratchet.py itself, but it is a real source/AST reader by the same grep pattern, so a
  module split must not break this scan's own assumptions about which files it walks.
- `test_digest_docstring.py` — CP3: `_related_spec_items`'s docstring states the exact BMAD "done"
  rule; `ast.parse(ENGINE.read_text(encoding="utf-8"))` + `ast.walk` to find that one `FunctionDef`
  and check its docstring text word for word.
- `test_shortcuts_guard.py` — CP7: Ratchet's own guard gets a shortcut table;
  `ast.walk(ast.parse(src))` collects every string constant in the given source to check the
  shortcut table's commands against the guard's own literals.
- `test_split_check.py` — CP2 of an **earlier, unrelated** run: proves the old `test_trust.py`
  source split into `trust_helpers.py` + parts lost nothing, via `ast.parse`/`ast.walk` against the
  **pinned pre-split commit** (`git show a24a31d:tests/test_trust.py`), never the working tree. Not
  itself about ratchet.py's split, but the structural precedent pattern this run's own CPn tests
  reuse to prove a mechanical move lost nothing.

## Kind 3 — both (loads ratchet.py dynamically AND reads/parses its literal on-disk text): 6 files

- `test_trust_cp4.py` — trust-holes CP4: agent-set overrides no longer bypass checks. Loads
  ratchet.py dynamically (`ratchet_engine_slow_hook`) AND reads `ENGINE.read_text()` +
  `ast.parse`/`ast.walk` to collect specific `FunctionDef`s, plus `inspect.getsource(m._open_terminal)`
  to inspect that one function's real source.
- `test_trust_cp5_d.py` — trust-holes CP5 part d: approvals answer the question Ratchet asked.
  Loads ratchet.py dynamically (`ratchet_cp5r10`, `ratchet_cp5r10t`) AND reads `ENGINE.read_text()`,
  regex-matching a `CP4_CODE_RE` pattern against it, plus `ast.parse`/`ast.walk` to collect
  `FunctionDef`s by name.
- `test_trust_cp5_e.py` — trust-holes CP5 part e: approvals answer the question Ratchet asked.
  Loads ratchet.py dynamically (`ratchet_cp5r13`) AND `ast.walk(ast.parse(src))` to collect every
  top-level `FunctionDef` by name, including `cp5r13_shared_write_violations(src)` — the
  shared-record-write scan that `test_quota_shared.py` imports and calls against ratchet.py's own
  on-disk text (see Kind 4 below).
- `test_docs.py` — docs-lint suite. Loads ratchet.py dynamically twice (`ratchet_engine_cp2`,
  `ratchet_engine_cp2_real`) AND separately reads `CP2_ENGINE.read_text(encoding="utf-8")` plus
  `ast.walk(ast.parse(Path(path).read_text(...)))` over other files for doc-accuracy checks.
- `test_docs_accuracy.py` — CP7: the docs, the timeout message and the code comments say what
  Ratchet actually does. Loads ratchet.py dynamically (`ratchet_engine_docs_accuracy`) AND
  separately `ast.parse(src)` + `ast.walk` over its on-disk text for comment/behavior checks.
- `test_lane_added_tests.py` — CP4: a lane may adjust tests it added during the run. Loads a target
  test file dynamically (`cart_tests`, via `spec_from_file_location`) AND
  `ast.parse(open(rel).read())` to inspect that file's own structure (not ratchet.py's, but the
  same two patterns live in one file).
- `test_modularize_cp2.py` — CP2: the 8 leaf utilities (now/die/ok/find_root/read_json/write_json/
  slugify/config_from_user) move verbatim into `engine_utils.py`. Loads `engine_utils.py`
  dynamically (`load_eu`, via `spec_from_file_location`) and, in CP2-T12, re-loads each of the
  three Kind-4-named fixture files the same way to prove they still import cleanly. Separately
  reads `ENGINE.read_text()` and `ast.parse`/`ast.walk`s both that live text and the pinned
  pre-move commit's text (via `git show`) to compare each function's body byte-for-byte, find
  leftover top-level `def`s, check for star imports, and locate `DEFAULT_CONFIG`/
  `OLD_DEFAULT_REVIEW_DEPTH` wherever they now live.

## Kind 4 — brief-named, but don't literally match either live-grep pattern today: 2 files

The brief names 5 files; 3 are in the Kind 1-3 lists above. These 2 depend on ratchet.py's on-disk
text exactly the same way, but via a path expression that isn't the literal variable name `ENGINE`
the `ENGINE\.read_text` regex looks for, and they call no `ast.*`/`getsource`/
`spec_from_file_location` themselves — so the live grep run genuinely does not list them. They are
documented here anyway, per the brief's explicit instruction, because a module split must still
keep them working:

- `test_quota_shared.py` — CP2: one shared provider-health record across every lane on a machine.
  At case CP2-X11 (around line 857-867) it imports `test_trust_cp5_e` as `tt` and reads
  `(ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py").read_text(encoding="utf-8")`,
  then calls `tt.cp5r13_shared_write_violations(src)` against that text to assert ratchet.py writes
  `provider-health.json` ONLY inside `update_record`. The actual tree walk lives in
  `test_trust_cp5_e.py` (Kind 3 above); this file only supplies the on-disk text. A module split
  that moves that write call without updating `cp5r13_shared_write_violations`'s target file list
  would silently stop checking the real file — CP4/CP5's done_when call this out explicitly.
- `test_discover_paid.py` — CP2: Ratchet never spends the developer's money on a per-use or
  unknown-cost provider without an exact yes first. At line 448:
  `engine_src = (ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py").read_text()`,
  to grep/assert strings are present in the engine's own on-disk text (and separately reads
  SKILL.md/README.md/schemas.md the same way for doc-accuracy checks). Once CP5 moves the
  provider-cost/question logic into `provider_discovery.py`, this file's text-based check must be
  re-pointed to also cover that new module.

A file this checkpoint's own fixture deliberately matches neither live-grep pattern (confirmed
live, with its own check) and is correctly absent from every list above.
