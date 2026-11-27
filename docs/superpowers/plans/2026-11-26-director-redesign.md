# Director Redesign (Lane D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign director so (1) director vs builder comes from the Git checkout, (2) enrolled-lane approvals need no pasteable tokens and no claim flow, (3) safe lanes start in parallel by default, and (4) every flow is for the **AI harness + agents** — users never use a terminal.

**Architecture:** Checkout/layout identity → director-apply for all pause actions → drop token handoff and enrolled claim → request-level file ownership + parallel start → harness-readable status. Stay inside `ratchet_engine.py` (no module extract in D). Keep Lane S launcher invariant (`ratchet.py` for emitted paths).

**Tech Stack:** Python 3.8+ stdlib; existing `tests/run_all.py`; in-process / hermetic git fixtures used by director and trust suites.

**Spec:** `docs/superpowers/specs/2026-11-26-director-redesign-design.md`

## Global Constraints

- **No user terminal.** Humans use the harness only. Agents/harness call Ratchet. Hints, SKILL, docs, and `NEXT:` / `_director_next` strings must never tell a *user* to open a shell or paste a token.
- Happy-path director identity = checkout + layout records from `lanes-start` / enroll — not `RATCHET_ROLE=director`.
- Enrolled lanes: no `--director-token` happy path; director applies against `--root <lane>`.
- Enrolled lanes: no `claim` / `resume --claim` ownership transfer.
- Parallel default only when ownership is disjoint and deps are satisfied; otherwise serialize or refuse with a harness-readable reason.
- Do not break Lane S: emitted runnable paths still name `ratchet.py`.
- Stdlib only; no new third-party deps.
- Merge bar: full suite green **and** adversarial review (different model) with no open Critical/Important findings.
- Author every commit as `Rahil Pirani <rahil@piranifam.com>` (never Cursor).

## File map

| Path | D responsibility |
|------|------------------|
| `skills/ratchet-orchestrator/scripts/ratchet_engine.py` | Identity check, director-apply expansion, token removal, claim removal, ownership + parallel in `lanes-start`, status fields, hint strings |
| `skills/ratchet-orchestrator/SKILL.md` | Agent contract: harness/director-apply; no token paste; no user terminal |
| `skills/ratchet-orchestrator/references/schemas.md` | Request/ownership/status JSON shapes |
| `docs/reference.md`, `docs/guide.md`, `docs/how-it-works.md` | Director sections rewritten for harness/agents |
| `tests/test_director_redesign.py` | New guard suite for D done-when bar |
| `tests/test_director_*.py`, `test_lanes_start*.py`, `test_trust_cp*.py` | Retarget to new contract |
| `docs/superpowers/specs/2026-11-26-director-redesign-design.md` | Approved design (status updated) |

---

### Task 1: Guard suite for D (write first; must fail on current main)

**Files:**
- Create: `tests/test_director_redesign.py`

**What “pass” means after D (these fail today):**

- [ ] **Step 1: Write failing guards** covering at least:

```text
[D-T01] Director session from main checkout / layout record without RATCHET_ROLE on happy path
[D-T02] Builder worktree cannot run director-enroll / director-sign / lanes-start
[D-T03] Enrolled pause cleared by director-sign --apply (or successor) for approve-plan with no --director-token
[D-T04] --director-token apply refused (or absent) on enrolled lane after D
[D-T05] claim / resume --claim refused or absent in enrolled lane
[D-T06] Two lanes with disjoint --own paths: lanes-start (or status) marks both clear to run in parallel
[D-T07] Overlapping ownership: not started in parallel; harness-readable error or wait
[D-T08] status --json exposes waiting_on + next director action without token text
[D-T09] SKILL / _director_next / agent hints contain no "--director-token" and no "user … terminal" for director flows
```

- [ ] **Step 2: Run the new file alone; confirm it fails for the right reasons**

```bash
python3 tests/test_director_redesign.py
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_director_redesign.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "test: Lane D guard suite (failing until redesign lands)"
```

---

### Task 2: Checkout / layout identity

**Files:**
- Modify: `ratchet_engine.py` (`director_session_problem` / `_director_session_check` / `require_director_session`, `_enroll_lane`, `cmd_lanes_start` / request JSON writer)
- Modify: schemas + reference as needed for the layout record shape

**Interfaces:**
- Request or home record lists: `director_root` (abs path), `lanes[{name, worktree, ...}]`
- `require_director_session(what)`: succeed when cwd (or `--root` for director home) matches `director_root` / main worktree for that request; fail when cwd is an enrolled lane worktree

- [ ] **Step 1: On `lanes-start`, write layout** into the request JSON (and/or a stable home file) including absolute `director_root` and each lane worktree path.

- [ ] **Step 2: Change `_director_session_check`** so the happy path is: cwd is the recorded director root (or common main worktree), **without** requiring `RATCHET_ROLE=director`. Keep fail-closed when ambiguous. Builder worktree → refuse director-only commands.

- [ ] **Step 3: Downgrade `RATCHET_ROLE`** to legacy-only when no layout/hooks exist; it must not override a clear layout.

- [ ] **Step 4: Make D-T01 / D-T02 pass**; keep unrelated trust tests green or update them to the new proof.

- [ ] **Step 5: Commit**

```bash
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -am "feat(director): infer role from checkout layout"
```

---

### Task 3: Director applies everything; tokens out of the happy path

**Files:**
- Modify: `ratchet_engine.py` (`DIRECTOR_APPLY_ACTIONS`, `cmd_director_sign`, `_require_approval`, `_director_next`, lane apply commands that take `--director-token`)
- Modify: director/sign/apply tests

**Behavior:**
- Every action that today needs a token becomes director-applyable (`--apply` or default apply-from-director-session against `--root <lane>`).
- After D, enrolled lanes **refuse** `--director-token` (hard error with harness-readable message: use director apply). Optional: keep HMAC audit fields written only by the director process (never printed for paste).
- `_director_next` returns harness/agent instructions like: `director-sign approve-plan --root <lane> --apply` — never “paste `--director-token`”.

- [ ] **Step 1: Expand apply set** to all `director-sign` actions (approve-plan, human-approve, feedback, cancel, set-review, hooks-waive, ui-respec, guardrail, accept-line, …). Keep policy checks for the former routine set.

- [ ] **Step 2: Remove or refuse token print / token apply** on enrolled lanes.

- [ ] **Step 3: Rewrite `_director_next` and related ASK/NEXT strings** for agents/harness only.

- [ ] **Step 4: Make D-T03, D-T04, D-T08 pass**; update `test_director_*` / trust suites that asserted token handoff.

- [ ] **Step 5: Commit**

```bash
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -am "feat(director): apply approvals without tokens"
```

---

### Task 4: Remove claim from enrolled lanes

**Files:**
- Modify: `ratchet_engine.py` (`ask claim`, `resume --claim`, ownership notes that say claim moves the run)
- Modify: SKILL / schemas / tests that mention claim under director

- [ ] **Step 1: In director-enrolled lanes, refuse `ask claim` and `resume --claim`** with a clear reason (director owns routing; harness switches sessions).

- [ ] **Step 2: Stop documenting claim as the way to move ownership** under a director.

- [ ] **Step 3: Make D-T05 pass.**

- [ ] **Step 4: Commit**

```bash
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -am "feat(director): drop claim in enrolled lanes"
```

---

### Task 5: Parallel by ownership

**Files:**
- Modify: `cmd_lanes_start` / `_lanes_start_run`, request JSON schema, `lanes` status output
- Modify: `tests/test_lanes_start.py`, `test_lanes_start_extra.py`

**CLI / record shape (agents/harness only):**

```text
lanes-start --request "..." \
  --lane NAME=FEATURE --own NAME=path/prefix [--own ...] \
  [--depend NAME=OTHER] ...
```

(Exact flag names may match existing argparse style; document the chosen names in schemas.md.)

- [ ] **Step 1: Require ownership for multi-lane parallel.** Single-lane start may omit ownership. Multi-lane without ownership → either all serial with an explicit note, or refuse parallel; design choice in code comments: **prefer refuse-to-parallelize (serialize) with a status field**, don’t silently parallelize.

- [ ] **Step 2: Compute ready set:** deps satisfied + no ownership overlap with already-ready/running siblings → `start_ok: true` (or equivalent) for those lanes in JSON; others `start_ok: false` + reason.

- [ ] **Step 3: Default = parallel for the ready set.** Harness starts those agents; Ratchet only records/prints which are clear (still does not launch CLIs itself).

- [ ] **Step 4: Make D-T06 / D-T07 pass.**

- [ ] **Step 5: Commit**

```bash
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -am "feat(lanes): parallel start by file ownership"
```

---

### Task 6: Docs + SKILL (harness-first)

**Files:**
- Modify: `SKILL.md` §“Running under a director”
- Modify: `docs/reference.md`, `docs/guide.md`, `docs/how-it-works.md` as needed
- Modify: `references/schemas.md`

- [ ] **Step 1: Rewrite director sections** so the actor is always the agent or harness. Ban “ask the user to run …” / “open a terminal” for these flows.

- [ ] **Step 2: Remove token-paste and claim recipes** from agent docs.

- [ ] **Step 3: Document ownership flags + parallel rules + status fields.**

- [ ] **Step 4: Make D-T09 pass** (string greps).

- [ ] **Step 5: Commit**

```bash
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -am "docs: harness-first director contract for Lane D"
```

---

### Task 7: Full suite + adversarial review

- [x] **Step 1: Run full suite** — `177 passed, 0 failed (suites)` on HEAD after apply-only / ownership pin retargets.

```bash
python3 tests/run_all.py
```

- [x] **Step 2: Dispatch adversarial reviewer** (different model than the builder). Brief: find ways D still requires tokens, trusts `RATCHET_ROLE` over layout, allows builder director cmds, parallelizes overlapping ownership, or tells users to use a terminal.

- [x] **Step 3: Fix Critical/Important findings; re-run suite; re-review until clean.** Round-3 verify CLEAN (no-own first lane, serial lift, harness-first ask/prompt/SKILL).

- [x] **Step 4: Mark design status `approved; implemented`** if not already; push; update PR.

- [x] **Step 5: Final commit if needed** — suite-pin retargets + inventory Kind 2 placement; full suite green.

```bash
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -am "test: Lane D adversarial clean + suite green"
```

---

## Done when

- [x] [D-T01]–[D-T09] green.
- [x] Checkout/layout identity is the happy path.
- [x] No token handoff or enrolled claim in the product path.
- [x] Parallel-by-ownership default for safe lane sets.
- [x] Docs/SKILL/harness status assume AI + harness only (no user terminal).
- [x] Full suite green; adversarial review clean.
- [x] Commits authored solely by Rahil Pirani.

## Out of scope (do not sneak in)

- Extracting director into a separate module.
- Paid-provider consent hole, OpenCode `!` bypass.
- Broad integrate topology expansions.
- Lane T performance work / Lane U UI gate.
