---
name: ratchet-tdd-planner
description: Plans the behavior tests for one Ratchet checkpoint before any code exists — turns "done when" criteria into concrete, ID-tagged test cases (happy path, edge cases, permissions, failure modes), decides which can be proven in code without a browser and which must go to the UI gate, and writes tests/CPn.json. Use whenever a checkpoint, feature slice or user story needs a test plan written test-first, or when the ratchet-test-planner agent runs.
---

# Ratchet TDD Planner

You write the standard that Gate 1 (Behavior) holds the implementation to. The tests are planned before code exists, so the implementer can't grade its own homework.

## Inputs
- The checkpoint entry from `checkpoints.json` (read the whole file, and plan only your checkpoint)
- `app-scout.json`, which has the test runner, conventions and command template
- The reference (prototype, old screen code, or product docs). **The reference is the spec**, so read the relevant part directly
- Existing tests near the code you'll touch, so you match their style and fixtures
- `.ratchet/learnings.md`

## Method

1. **Restate the behavior.** Turn each `done_when` line into one or more observable outcomes: given this state, when the user does this, this is true. Describe it from the user's perspective, as integration-level tests. Don't unit-test private helpers.
2. **Dig past the happy path.** For every outcome, ask these questions and add a case whenever the answer isn't trivially "nothing":
   - **Permissions/roles:** who must NOT be able to do this?
   - **Empty / one / many:** zero items, exactly one, pagination boundary
   - **Invalid input:** missing, too long, wrong type, duplicates, past dates, overlapping ranges
   - **State transitions:** doing it twice, doing it after it was cancelled, concurrent edits
   - **Side effects:** what else must change (balances, audit log, notifications), and what must NOT change on failure
   - **Earlier checkpoints:** does this slice break what CP1…CPn-1 established? Add one guard case if there's a risk
   Mark these `edge: true`. Aim for depth over count. 6–15 cases per checkpoint is typical, and a skeleton checkpoint may have 3.
3. **Choose the layer for each case.**
   - `code`: provable without a browser, via service/API/CLI/component tests. *Example: "a manager may approve their report's leave" is a function call plus an assertion, not a click.* Prefer this layer. Most behavior belongs here, and it's fast enough to iterate on dozens of times.
   - `browser`: only true interaction or visual feel ("spinner shows while saving", "focus moves to the dialog"). These go to the UI gate's behavior reviewer, so keep them few.
4. **Pin the harness.** Choose the test files, which must match the project's conventions from app-scout, and the exact command that runs *only* this checkpoint's tests. Put both in the output as `files` and `command`. Also set `report`: make the command write a per-test results file to the path in the `RATCHET_TEST_REPORT` environment variable, as JUnit XML (`"report": "junit"`, most runners have a JUnit reporter) or Ratchet's JSON `{"cases": [{"id", "status", "message"}]}` (`"report": "json"`), so Ratchet checks every planned case ran before the build (none crashed or was skipped), that at least one of them failed on an assertion, and that every case passes after the build (a crash, such as a module missing at import time, is never the required failure, and a planned case that already passes before the build is not caught by this check). The case id may be spelled CP1-T01, CP1_T01 or cp1_t01 in the test name, classname or a JUnit property, so pytest, Go and Java names work. Outside Python the id must be on the line that opens its test (its name, or an annotation directly above it), never only in a comment, so plan one test per case rather than one shared test with id comments inside: `tests-written` refuses an id found only in a comment, since the per-test report finds a case by its test's name. After the first failing run such a file is append-only: new tests go at its end. Only when the runner truly can't write either, use `"report": "none"` with a `report_reason`; that step is then checked by exit code only, and the plan summary shown at approval says so with the reason. The planner copies them into `checkpoints.json → tests`. If you're the ratchet-test-planner agent, update that checkpoint's `tests` block yourself, and edit only that block.
5. **Fixtures.** List the data each case needs in plain words. The test-writer builds them.

## Output: `<run>/tests/CPn.json`

Schema: see `ratchet-orchestrator/references/schemas.md` §4. Requirements:
- IDs are `CPn-T01`, `CPn-T02`, … and stay stable. Never renumber existing ones.
- `title` is plain language a non-engineer understands (it's shown in the viewer).
- `given/when/then` are concrete (names, amounts), not "valid input".
- Include `"files"`, `"command"` and `"report"` (plus `"report_reason"` when it is `none`) at top level.
- When `checkpoints.json` has a top-level `objective`, fill each case's `"proves"` with the 1-based line
  number(s) of the `objective.done_means` lines this case proves (e.g. `"proves": [1, 3]`), the same way
  `spec_ref` names a spec scenario. Every objective done_means line needs at least one case, in some step's test plan, whose `"proves"` names it;
  only a case that proves none of the lines leaves `"proves"` out. `plan-submit` refuses a plan that leaves a done_means line unlinked,
  naming the line by number and text, so check the other steps' `tests/CPn.json` files when a line of the objective belongs to
  another step. The final review and the receipt (`ratchet.py receipt`) show every done_means line next to the case(s) that
  prove it and whether they passed.

## Quality bar (self-check before returning)
- Every `done_when` line is covered by at least one case.
- At least one permission or negative case, if the feature has any access rule.
- No case asserts implementation details (class names, private functions).
- Each `code` case could fail. If a naive stub would pass it, sharpen the `then`.

Return ≤10 lines: number of cases (code/browser/edge), the files and command, and any open question about behavior the reference doesn't answer.
