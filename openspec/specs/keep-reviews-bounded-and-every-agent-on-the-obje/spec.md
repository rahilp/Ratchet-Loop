# Keep reviews bounded and every agent on the objective

## Purpose
What this feature does, as built and checked by Ratchet (run keep-reviews-bounded-and-every-agent-on-the-obje, 2026-10-11). Each scenario names the test that proves it.

## Requirements

### Requirement: Review limits cannot creep
The system SHALL do this: A step's review limit can be raised only once, with the user's own reason, the block menu leads with simplify, accept, change and stop, a high-risk step must name the user harm, and fixers make the smallest fix.

- When a step hits its review limit, the options shown start with simplify, change the step and stop, the user can still accept findings with their own reason, and raising the limit comes last
- Raising the limit needs the user's own reply `raise: <reason>` in the run's chat, the reason lands in learnings, and the same step is never offered a second raise, even after config.json's round limit is edited
- A new plan with a high-risk step and no risk_reason is refused at plan-submit, naming the step; already-approved plans keep working
- The fixer instructions and the adversarial-loop skill say: make the smallest change that resolves each finding, add no new mechanism or file unless the finding needs it, and say so when a proper fix needs a redesign
- README, both skills, the fixer and planner instructions and schemas.md describe these rules

#### Scenario: At the round limit the options lead with simplify, accept, change and stop, with raise last
- **GIVEN** a step whose adversarial round has blocked enough times to hit its round limit for the first time, with its raise not yet used
- **WHEN** the agent reads the blocked-state options Ratchet offers
- **THEN** the lettered options start with simplify, then change the step, then stop, in that order; accepting the open findings appears as its own plain line rather than a lettered pick; raising the limit is the last lettered option
<!-- proven by test CP1-T01 in tests/test_limits.py, commit 1835735 -->

#### Scenario: Raising the limit needs the user's own reason and lets the review continue
- **GIVEN** the step is blocked at its round limit and the agent has asked to raise it
- **WHEN** the owner chat's first reply afterward is exactly "raise: this edge case is worth one more look" and the agent runs the raise command
- **THEN** the step's round limit is raised, the reason is appended to .ratchet/learnings.md, and `ratchet.py reviewers` can now open a further round for this step
<!-- proven by test CP1-T02 in tests/test_limits.py, commit 1835735 -->

#### Scenario: A second raise for the same step is not offered
- **GIVEN** the step already used its one raise (CP1-T02) and later hits its (now higher) round limit again with the same kind of blocking finding
- **WHEN** the agent reads the blocked-state options Ratchet offers this second time
- **THEN** raising the limit is not among them; only simplify, change the step and stop are offered as lettered options, alongside the existing plain accept line
<!-- proven by test CP1-T03 in tests/test_limits.py, commit 1835735 -->

#### Scenario: Editing config.json's max_adversarial_rounds afterward does not lift a step's recorded, already-raised cap
- **GIVEN** the step's round limit was raised once (CP1-T02), recording its own cap
- **WHEN** `ratchet.py config --set max_adversarial_rounds=99` is run and then the step is pushed to block past its recorded cap again
- **THEN** the step still blocks at the cap it was raised to, not at 99; the run-wide cap and other steps' limits are unaffected
<!-- proven by test CP1-T04 in tests/test_limits.py, commit 1835735 -->

#### Scenario: Only the user's own reply in the owning chat, in the exact form, raises the limit
- **GIVEN** the step is blocked at its round limit and the agent has asked to raise it in chat session s-owner
- **WHEN** a message "raise: fine by me" is recorded in a different session s-other, and the agent then runs the raise command
- **THEN** the command is refused for lack of a reply in the owning chat; the step's round limit is unchanged and no reason is appended to learnings
<!-- proven by test CP1-T05 in tests/test_limits.py, commit 1835735 -->

#### Scenario: The fixer and the adversarial-loop skill tell the fixer to make the smallest fix and to stop for a redesign
- **GIVEN** agents/ratchet-fixer.md and skills/ratchet-adversarial-loop/SKILL.md as shipped
- **WHEN** both are searched for the fix-round instructions
- **THEN** both state the fixer must make the smallest change that resolves each finding, must not add a new mechanism or file unless the finding needs it, and must stop and say so (triggering a check-in) when a proper fix would need a redesign
<!-- proven by test CP1-T06 in tests/test_limits.py, commit 1835735 -->

#### Scenario: A new plan with a high-risk step and no risk_reason is refused at plan-submit
- **GIVEN** a draft plan with one checkpoint whose risk is "high" and risk_reason is missing
- **WHEN** `ratchet.py plan-submit` is run
- **THEN** the command is refused, naming the step, and the run does not move to awaiting_plan_approval
<!-- proven by test CP1-T07 in tests/test_limits.py, commit 1835735 -->

#### Scenario: A high-risk step with a risk_reason naming the user harm is accepted
- **GIVEN** the same draft plan, now with risk_reason "a wrong check here would let a corner-cutting agent skip review and ship a bypassed auth check that hurts every signed-in user"
- **WHEN** `ratchet.py plan-submit` is run
- **THEN** the plan is accepted and the run moves to awaiting_plan_approval
<!-- proven by test CP1-T08 in tests/test_limits.py, commit 1835735 -->

#### Scenario: An already-approved plan's old high-risk step without a risk_reason keeps working, but plan-extend still requires one for a new step
- **GIVEN** a plan approved before this rule existed, carrying a high-risk checkpoint with no risk_reason, now mid-build
- **WHEN** the run continues normally, and feedback later appends a NEW high-risk checkpoint with no risk_reason via plan-extend
- **THEN** the existing high-risk step is unaffected and keeps working, while plan-extend refuses only the newly appended step for its missing risk_reason
<!-- proven by test CP1-T09 in tests/test_limits.py, commit 1835735 -->

#### Scenario: A raised cap is scoped to its own step
- **GIVEN** a two-checkpoint plan where CP1's round limit was just raised (CP1-T02)
- **WHEN** the second checkpoint, CP2, independently hits its own (un-raised) round limit
- **THEN** CP2 is offered raise as a lettered option (its own raise is still unused) and blocks at the plan's ordinary configured limit, not at CP1's raised cap
<!-- proven by test CP1-T10 in tests/test_limits.py, commit 1835735 -->

#### Scenario: The docs describe the round-limit, smallest-fix and risk_reason rules
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md, skills/ratchet-adversarial-loop/SKILL.md, schemas.md and agents/ratchet-checkpoint-planner.md as shipped
- **WHEN** the five are searched for these rules
- **THEN** together they describe that a round limit can be raised only once per step with the user's own reason and a later config edit does not lift it, the smallest-fix rule, and that a high-risk step needs a risk_reason naming the user harm while normal is the default risk
<!-- proven by test CP1-T11 in tests/test_limits.py, commit 1835735 -->

### Requirement: Ratchet writes every sub-agent brief header
The system SHALL do this: The orchestrator gets each sub-agent's opening text from `ratchet.py brief`, which prints the plan's objective word for word and that role's standing rules.

- `ratchet.py brief CP1 --role implementer` prints the plan's objective exactly as plan-summary shows it, then the implementer's rules (smallest change, no new mechanisms, no full test suite unless asked)
- Each role gets its own rules: implementer and fixer (smallest change, no new mechanisms, no full suite unless asked), test-writer, reviewer (realistic-use bar, every blocking finding says who is hurt and how), scout and planner
- An unknown role is refused with the list of valid roles; a run whose plan has no objective prints the plain no-objective note instead of failing
- The orchestrator skill tells the orchestrator to start every spawn prompt with the output of `brief` instead of writing the objective by hand, and README and schemas.md mention the command

#### Scenario: Briefing the implementer prints the objective exactly as plan-summary shows it, then the implementer's rules
- **GIVEN** the approved plan with a full objective block
- **WHEN** `ratchet.py brief CP1 --role implementer` is run
- **THEN** the objective lines in its output (who it's for, what they want, done means, out of scope) match, word for word, the objective lines `ratchet.py plan-summary` prints for the same plan, and after them the output states the implementer's rules: make the smallest change, add no new mechanism or file unless needed, and do not run the full test suite unless asked
<!-- proven by test CP2-T01 in tests/test_brief.py, commit 89da488 -->

#### Scenario: Briefing the fixer gives the same smallest-fix rule as the implementer, worded like the fixer's own instructions
- **GIVEN** the approved plan
- **WHEN** `ratchet.py brief CP1 --role fixer` is run
- **THEN** the output states the objective, then the fixer's rules: smallest change that resolves each finding, no new mechanism or file unless the finding needs it, and stop and say so when a proper fix needs a redesign, consistent with agents/ratchet-fixer.md's wording
<!-- proven by test CP2-T02 in tests/test_brief.py, commit 89da488 -->

#### Scenario: Briefing a reviewer states the realistic-use bar and that every blocking finding must name who is hurt and how
- **GIVEN** the approved plan
- **WHEN** `ratchet.py brief CP1 --role reviewer` is run
- **THEN** the output states the objective, then the reviewer's rules: judge findings against a realistic-use bar (the user runs a cooperative agent, not an attacker) and every blocking finding must say which user is hurt and how
<!-- proven by test CP2-T03 in tests/test_brief.py, commit 89da488 -->

#### Scenario: Each role's standing rules are its own text, not a copy of another role's
- **GIVEN** the approved plan
- **WHEN** `brief CP1` is run once for each of implementer, fixer, test-writer, reviewer, scout and planner
- **THEN** every role's rules section differs from every other role's (the test-writer's rules are not the implementer's, the scout's are not the planner's, and so on), so a stub that prints one fixed block for every role fails this case
<!-- proven by test CP2-T04 in tests/test_brief.py, commit 89da488 -->

#### Scenario: Scout and planner can be briefed with no checkpoint yet
- **GIVEN** a freshly approved plan before any checkpoint-specific work has started
- **WHEN** `ratchet.py brief --role scout` and `ratchet.py brief --role planner` are each run with no CPn argument
- **THEN** both succeed (exit 0), each printing the plan's objective followed by that role's own rules, with no crash or missing-CPn error
<!-- proven by test CP2-T05 in tests/test_brief.py, commit 89da488 -->

#### Scenario: An unknown role is refused with the list of valid roles
- **GIVEN** the approved plan
- **WHEN** `ratchet.py brief CP1 --role wizard` is run
- **THEN** the command exits non-zero and its message names 'wizard' as not a valid role and lists the valid role names (implementer, fixer, test-writer, reviewer, scout, planner)
<!-- proven by test CP2-T06 in tests/test_brief.py, commit 89da488 -->

#### Scenario: A plan with no stated objective still briefs, with the plain no-objective note
- **GIVEN** the second project, whose plan has no objective key at all
- **WHEN** `ratchet.py brief CP1 --role implementer` is run
- **THEN** the command does not crash or exit non-zero for this reason, and its output carries the plain no-objective note in place of an objective block, followed by the implementer's rules
<!-- proven by test CP2-T07 in tests/test_brief.py, commit 89da488 -->

#### Scenario: A CPn not in the plan is refused and named
- **GIVEN** the approved plan, which only has checkpoints CP1 and CP2
- **WHEN** `ratchet.py brief CP9 --role implementer` is run
- **THEN** the command exits non-zero with a plain message naming CP9 as not part of this plan; a valid CPn (CP1 or CP2) is named plainly at the top of its own brief output
<!-- proven by test CP2-T08 in tests/test_brief.py, commit 89da488 -->

#### Scenario: The orchestrator skill, README and schemas.md describe briefing sub-agents with `brief` instead of a hand-written objective
- **GIVEN** skills/ratchet-orchestrator/SKILL.md, README.md and skills/ratchet-orchestrator/references/schemas.md as shipped
- **WHEN** the three are searched for the sub-agent spawn instructions
- **THEN** the orchestrator skill tells the orchestrator to start every spawn prompt with the output of `ratchet.py brief CPn --role <role>` rather than writing the objective by hand, and README and schemas.md both mention the `brief` command and what it prints
<!-- proven by test CP2-T09 in tests/test_brief.py, commit 89da488 -->

### Requirement: Sub-agents launched without the objective are refused
The system SHALL do this: On Claude Code, launching a Ratchet sub-agent during a build without the objective header from `brief` is refused with a plain message saying how to fix the prompt; other CLIs are documented as not enforced.

- During a build on Claude Code, launching ratchet-implementer (or any ratchet-* sub-agent, with or without the plugin prefix) with a prompt that lacks the objective from `brief` is refused with a plain message naming the `brief` command to use
- The same launch with `brief`'s output at the top of the prompt goes through
- Launching a non-Ratchet sub-agent, or any sub-agent while no run is building, is never refused
- README's per-CLI table has a row or column saying this check is enforced on Claude Code and not on Codex, Gemini or Copilot, and the orchestrator skill and schemas.md say the same

#### Scenario: Launching the implementer without the objective is refused during a build
- **GIVEN** A run with an approved plan (phase building) and CP1's checkpoint
- **WHEN** Claude Code's guard hook sees a PreToolUse Task call with subagent_type 'ratchet-implementer' and a prompt that does not contain the objective text
- **THEN** the hook blocks (exit 2), the stderr message names the `ratchet.py brief CPn --role implementer` command to fix the prompt with
<!-- proven by test CP3-T01 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: Plugin-prefixed subagent_type is recognized the same way
- **GIVEN** Same building run
- **WHEN** the Task call's subagent_type is 'ratchet-loop:ratchet-fixer' and the prompt lacks the objective
- **THEN** the hook blocks (exit 2) exactly as for the unprefixed name
<!-- proven by test CP3-T02 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: A correct launch, with brief's output at the top, is never refused
- **GIVEN** Same building run
- **WHEN** the Task call's prompt starts with the exact text `ratchet.py brief CP1 --role implementer` prints, followed by the rest of the prompt
- **THEN** the hook allows the call (exit 0, no deny)
<!-- proven by test CP3-T03 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: Whitespace and line-wrapping differences in the objective header never cause a refusal
- **GIVEN** Same building run and the same brief text as CP3-T03
- **WHEN** the prompt carries that same objective text but re-wrapped at different line lengths, with doubled spaces and a mix of tabs/spaces introduced, before the rest of the prompt
- **THEN** the hook still allows the call (exit 0) — comparison collapses whitespace instead of matching it literally
<!-- proven by test CP3-T04 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: A non-Ratchet sub-agent is never refused
- **GIVEN** Same building run
- **WHEN** the Task call's subagent_type is 'general-purpose' (or 'Explore') and the prompt has no objective text at all
- **THEN** the hook allows the call (exit 0) regardless of prompt content
<!-- proven by test CP3-T05 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: No refusal while no run is building
- **GIVEN** A freshly initialized project whose plan has not yet been submitted/approved (phase is not 'building')
- **WHEN** the same Task call for 'ratchet-implementer' with a prompt lacking the objective is sent to the guard hook
- **THEN** the hook allows the call (exit 0) because the phase check does not apply outside an active build
<!-- proven by test CP3-T06 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: Enforcement also applies while the run is blocked
- **GIVEN** The same run with its state.json phase set directly to 'blocked' (fixture writes this file itself, not through the guard)
- **WHEN** the same missing-objective Task call for 'ratchet-implementer' is sent
- **THEN** the hook still blocks (exit 2), the same as during 'building'
<!-- proven by test CP3-T07 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: The check is Claude-Code-only: Codex is never refused for this
- **GIVEN** Same building run
- **WHEN** a Codex-shaped hook payload (turn_id present, --host codex) carries an equivalent sub-agent launch with subagent_type 'ratchet-implementer' and no objective in the prompt
- **THEN** the hook allows the call (exit 0); this guard check does not run on non-Claude-Code hosts
<!-- proven by test CP3-T08 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: A guard crash lets the launch through
- **GIVEN** Same building run, but checkpoints.json is replaced with unreadable/invalid content so reading the plan's objective raises
- **WHEN** the missing-objective Task call for 'ratchet-implementer' is sent to the guard hook
- **THEN** the hook fails open: exit 0, the call is allowed, and the crash is recorded the same way any other hook crash is (hook-errors.log / crash marker), never a silent pass with no record
<!-- proven by test CP3-T09 in tests/test_brief_guard.py, commit f3b64e7 -->

#### Scenario: Docs state which CLIs enforce this
- **GIVEN** The repo's README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** searched for the per-CLI enforcement note for this guard check
- **THEN** all three name Claude Code as enforced and Codex, Gemini and Copilot as not enforced for this specific check
<!-- proven by test CP3-T10 in tests/test_brief_guard.py, commit f3b64e7 -->
