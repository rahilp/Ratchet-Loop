# Director redesign (Lane D) — design

Date: 2026-11-26  
Status: approved  
Approach: checkout identity + director-applies + parallel-by-ownership

## Who this is for

Ratchet is run by **AI agents** and an **AI harness**. People use the product through that harness (chat, buttons, status). **People never type Ratchet commands in a terminal.**

Every flow in this design must work when:

- a director agent talks to the harness,
- a builder agent works in a lane worktree,
- the harness shows status / waits / resumes,

…with **no human copy-paste of tokens, env vars, or shell commands**.

Anything that today looks like “run this in a terminal” is really “the agent or harness must call it.” Docs and agent hints must speak in those terms.

## Context

Today’s director stack is hard for agents and worse for a no-terminal product:

- Role comes from `RATCHET_ROLE=director` plus hook records (easy to get wrong; fragile when hooks are missing).
- Most pauses need a **director token** handoff: director signs → someone pastes a token into the lane. That is a terminal/copy-paste workflow. Users will never do it; agents should not need it either.
- Enrolled lanes have **no working claim path** even though ownership language still exists.
- Multi-lane starts are effectively serial in practice; parallel is not the default when lanes do not conflict.

Lane S already split the engine (`ratchet.py` launcher + `ratchet_engine.py`). D redesigns director/ownership **behavior** on that layout. It does not re-split the engine.

## Goals (plain language)

1. **Know who’s who from the Git checkout.**  
   Main (or the director’s worktree) = director. Lane worktrees created for a request = builders. The harness/agents should not need a special role env when the tree shape is clear.

2. **Drop director tokens and claim.**  
   When a lane is waiting on the director, the director agent (or harness acting as director) **approves against that lane** — same idea as today’s `--apply` / `--root`, for every pause that used to need a token. No pasteable HMAC token, no `claim` / `resume --claim` in enrolled lanes.

3. **Run lanes in parallel when it’s safe.**  
   If lanes own different files and don’t depend on each other, start them together by default. If they overlap or one needs another’s output, wait or run one at a time.

4. **Harness-first UX.**  
   Status, “waiting on director,” and “what to do next” must be machine-readable for the harness and short for agents. Never invent a user-facing “open a terminal and run …” step.

## Non-goals

- Extracting director code into its own Python module (nice later; not required for D).
- Fixing paid-provider consent / OpenCode `!` bypass / other known trust limits unless they block the new flows.
- Changing integrate’s hard git refusals (submodules, lockfile merges, etc.) beyond what’s needed for parallel + ownership.
- Lane T (test speed) and Lane U (measured UI gate).
- Teaching humans to use the CLI.

## Architecture

### Identity: checkout topology

| Checkout | Role |
|----------|------|
| Primary repo checkout used as the director home (usually `main` / the folder `lanes-start` was driven from) | **Director** |
| Worktrees created for named lanes under a request | **Builder** |

How the engine decides (fail closed when ambiguous):

1. Prefer explicit layout records written by `lanes-start` / enrollment (which absolute path is the director home; which paths are lanes).
2. Else: a worktree that is enrolled as a lane under the current request → builder.
3. Else: the common git main worktree for that request → director.
4. If still unclear → refuse director-only actions with a harness-readable reason (do not “trust” a bare `RATCHET_ROLE`).

`RATCHET_ROLE=director` may remain as a **legacy hint** only when no hooks/layout exist; it must not override a clear checkout layout. Prefer deleting dependence on it for the happy path.

Builder sessions must not be able to enroll, sign, start lanes, or integrate — same as today, but proven by checkout + records, not by “did the agent set an env var.”

### Approvals: director applies; tokens gone

**Old:** lane pauses → director `director-sign` → token → lane apply (or a small set of `--apply` actions).

**New:** lane pauses with `waiting_on: director` → director/harness calls one director approval against that lane root → pause clears. No token field, no paste step.

Implications:

- Extend director apply to **all** actions that today need a token (`approve-plan`, `human-approve`, `feedback`, `cancel`, `set-review`, `hooks-waive`, `ui-respec`, `guardrail`, `accept-line`, …), not only the current `--apply` subset.
- Keep a **director key** only if we still want a cryptographic **audit trail** on disk (who approved what). It must never become something a user or agent pastes between chats.
- Remove `claim` / `resume --claim` from enrolled-lane flows. The director already owns routing; builders do not steal runs via claim.
- Agent/SKILL text: “call director approve for this lane” — never “copy this token into the other session.”

### Parallel: ownership on the request

When the director/harness starts several lanes from one request:

1. Each lane declares (or is assigned) **file ownership** (path prefixes / globs) and optional **depends-on** other lane names.
2. Default: start every lane whose deps are satisfied and whose ownership does not overlap an already-running or already-started sibling.
3. Overlap or missing ownership → do not parallelize that pair; serialize or refuse with a clear harness error.
4. Ratchet still does not invent a secret way to launch CLIs; the harness starts agents. Ratchet only records which lanes are clear to run now vs wait.

Ownership lives with the request / lane records so status and integrate can reason about it later.

### Harness contract (required)

For every pause and multi-lane decision, expose enough structured status that the harness can:

- show “waiting on director” without a human reading a shell,
- invoke the matching director approve for the right lane,
- know which lanes may start in parallel next.

Human-facing copy in the product UI is the harness’s job. Ratchet’s job is stable JSON/fields and agent-facing next steps that **do not assume a terminal**.

## Compatibility

- Keep public command *names* where possible (`director-enroll`, `lanes-start`, `integrate`, …) so agents and docs churn less; change **semantics** (no tokens, checkout identity, parallel default).
- Migrate or soft-reject old token-based apply paths: enrolled lanes after D must not accept `--director-token` as the happy path (remove or fail closed with a harness-readable “use director apply”).
- Existing trust tests that encode token handoff / `RATCHET_ROLE` happy paths are rewritten to the new contract (Lane T may help with suite speed; D owns the behavioral rewrite).
- Launcher path invariant from S stays: emitted runnable paths still name `ratchet.py`.

## Out of band for agents (not users)

Commands still exist as argv the **agent or harness** runs. Users never see them. Design and SKILL updates must:

- describe flows as agent/harness actions,
- avoid “ask the user to run … in a terminal,”
- treat any printed “retry command” as for the agent/harness only.

## Rollout (high level)

1. Spec + plan approved (this doc + implementation plan).
2. Identity: layout records + checkout inference; director-only gates use them.
3. Approvals: director-apply for all pause actions; remove token handoff and claim in enrolled lanes; update SKILL / reference / schemas for harness + agents.
4. Parallel: ownership on request; default parallel when safe; status fields for the harness.
5. Full suite + adversarial review (different model), same bar as S.
6. Docs: zero “user runs this in a terminal” for director flows.

## Risks

| Risk | Mitigation |
|------|------------|
| Ambiguous checkout → wrong role | Fail closed; require layout records from `lanes-start` / enroll |
| Harness can’t drive approvals without tokens | One structured approve path + status fields before removing tokens |
| Parallel starts clobber shared files | Ownership required for parallel; overlap → serialize/refuse |
| Agents still emit “paste this token” | SKILL + hint string sweep; adversarial grep |
| Users imagined as CLI operators in docs | Explicit no-terminal rule in D; rewrite director sections |

## Verification

- Director vs builder decided from checkout/layout in fixture worktrees without setting `RATCHET_ROLE` on the happy path.
- Enrolled lane pause → director apply clears it with **no** `--director-token`.
- Token apply path gone or hard-refused for enrolled lanes.
- No claim path required (or offered) for enrolled multi-lane ownership.
- Two disjoint-ownership lanes start in parallel by default; overlapping ownership does not.
- Status JSON tells the harness what is waiting and which approve to run.
- Docs/SKILL for director flows mention agents/harness only — not user terminals.
- Full suite green; adversarial review with no open Critical/Important findings.

## Done when

- Checkout-based identity is the happy path.
- Director tokens and enrolled-lane claim are gone.
- Parallel-by-ownership is the default for safe lane sets.
- The product story is: **users use the harness; agents call Ratchet; nobody needs a terminal.**

## Sequencing

D lands on the post-S engine. T can run in parallel (especially tests for D’s new contract). U stays last.
