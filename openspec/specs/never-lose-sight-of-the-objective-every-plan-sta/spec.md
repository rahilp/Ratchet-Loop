# Never lose sight of the objective: every plan states who the users are and what they want, the objective travels with every builder, fixer and reviewer, every finding states its real user impact, drift check-ins when review rounds pile up, final review reported against the objective; then converge reviews cheaply

## Purpose
What this feature does, as built and checked by Ratchet (run never-lose-sight-of-the-objective-every-plan-sta, 2026-10-10). Each scenario names the test that proves it.

## Requirements

### Requirement: Every plan states its objective
The system SHALL do this: A new plan must say who will use the result, what they are trying to do, what done means to them and what is out of scope, and the user sees this first when approving the plan.

- Submitting a plan with no objective, or with any of its four parts empty, is refused with a message that names the missing part
- plan-summary starts with the objective (who it is for, what they want, what done means, what is out of scope) before the list of steps
- A plan drafted from a spec (OpenSpec, Spec Kit, BMAD) comes with a draft objective taken from the spec, so that flow still works
- A run whose plan was approved before this change keeps working, and adding steps from feedback keeps the objective
- schemas.md, the planner agent, the orchestrator skill and the README describe the objective with an example

#### Scenario: A plan with a complete objective submits normally
- **GIVEN** checkpoints.json has one checkpoint and an objective whose users, goal, done_means and out_of_scope are all non-empty
- **WHEN** ratchet.py plan-submit runs
- **THEN** it exits 0 and the run's phase becomes awaiting_plan_approval
<!-- proven by test CP1-T01 in tests/test_objective.py, commit 87782cd -->

#### Scenario: A plan with no objective at all is refused
- **GIVEN** checkpoints.json is otherwise valid but has no top-level 'objective' key
- **WHEN** ratchet.py plan-submit runs
- **THEN** it exits non-zero and the error message names 'objective' as missing
<!-- proven by test CP1-T02 in tests/test_objective.py, commit 87782cd -->

#### Scenario: An objective with no stated users is refused
- **GIVEN** objective.users is an empty string while goal, done_means and out_of_scope are filled in
- **WHEN** ratchet.py plan-submit runs
- **THEN** it exits non-zero and the error message names 'users' as the missing part
<!-- proven by test CP1-T03 in tests/test_objective.py, commit 87782cd -->

#### Scenario: An objective with no stated goal is refused
- **GIVEN** objective.goal is an empty string while the other three parts are filled in
- **WHEN** ratchet.py plan-submit runs
- **THEN** it exits non-zero and the error message names 'goal' as the missing part
<!-- proven by test CP1-T04 in tests/test_objective.py, commit 87782cd -->

#### Scenario: An objective with no done_means is refused
- **GIVEN** objective.done_means is an empty list while the other three parts are filled in
- **WHEN** ratchet.py plan-submit runs
- **THEN** it exits non-zero and the error message names 'done_means' as the missing part
<!-- proven by test CP1-T05 in tests/test_objective.py, commit 87782cd -->

#### Scenario: An objective with no out_of_scope is refused
- **GIVEN** objective.out_of_scope is an empty list while the other three parts are filled in
- **WHEN** ratchet.py plan-submit runs
- **THEN** it exits non-zero and the error message names 'out_of_scope' as the missing part
<!-- proven by test CP1-T06 in tests/test_objective.py, commit 87782cd -->

#### Scenario: plan-summary opens with the objective, before the steps
- **GIVEN** a submitted plan whose objective has users, goal, one done_means line and one out_of_scope line
- **WHEN** ratchet.py plan-summary runs
- **THEN** the users text, the goal text, the done_means line and the out_of_scope line all appear in the output, and every one of them appears before the first numbered step line ('\n1. ')
<!-- proven by test CP1-T07 in tests/test_objective.py, commit 87782cd -->

#### Scenario: plan-summary lists every done_means and out_of_scope line, not just the first
- **GIVEN** an objective with three done_means entries and two out_of_scope entries, all with distinct wording
- **WHEN** ratchet.py plan-summary runs
- **THEN** all three done_means lines and both out_of_scope lines appear in the output
<!-- proven by test CP1-T08 in tests/test_objective.py, commit 87782cd -->

#### Scenario: A plan drafted from an OpenSpec change gets a draft objective from the proposal
- **GIVEN** an OpenSpec change whose proposal.md has a 'Why' paragraph and one requirement with one scenario
- **WHEN** spec_sources.draft_plan() builds the plan
- **THEN** the returned plan has a top-level objective whose four parts are all non-empty, and goal reads from the proposal's Why paragraph
<!-- proven by test CP1-T09 in tests/test_objective.py, commit 87782cd -->

#### Scenario: A plan drafted from a Spec Kit feature also gets a draft objective
- **GIVEN** a specs/<feature>/spec.md with one user story and no explicit non-goals section
- **WHEN** spec_sources.draft_plan() builds the plan
- **THEN** the returned plan has a top-level objective whose four parts are all non-empty, with a sensible default out_of_scope line since the spec names none
<!-- proven by test CP1-T10 in tests/test_objective.py, commit 87782cd -->

#### Scenario: A run already approved before this change keeps working
- **GIVEN** a run folder whose state.json and checkpoints.json were written directly to disk (not through plan-submit), already marked approved, and whose plan has no objective field
- **WHEN** ratchet.py status runs
- **THEN** it exits 0 and nothing in its output complains about a missing objective
<!-- proven by test CP1-T11 in tests/test_objective.py, commit 87782cd -->

#### Scenario: Adding steps from feedback keeps the existing objective
- **GIVEN** an approved plan with an objective, then ratchet.py feedback, then checkpoints.json gains one new checkpoint while the objective block is left untouched
- **WHEN** ratchet.py plan-extend runs
- **THEN** it exits 0 and the saved plan's objective is exactly unchanged from before feedback
<!-- proven by test CP1-T12 in tests/test_objective.py, commit 87782cd -->

#### Scenario: plan-extend refuses a feedback plan that drops the objective
- **GIVEN** the same feedback setup as CP1-T12, but the appended checkpoints.json has its objective emptied (or the key removed) while adding the new checkpoint
- **WHEN** ratchet.py plan-extend runs
- **THEN** it exits non-zero and the message says the objective cannot be dropped, and the previously saved plan is unchanged
<!-- proven by test CP1-T13 in tests/test_objective.py, commit 87782cd -->

#### Scenario: The docs describe the objective, with an example
- **GIVEN** skills/ratchet-orchestrator/references/schemas.md, README.md, agents/ratchet-checkpoint-planner.md and skills/ratchet-orchestrator/SKILL.md read as text from the repo
- **WHEN** each file's text is checked
- **THEN** every one of the four files mentions the objective's four parts (users, goal, done_when/done_means, out_of_scope) and at least one of them shows a worked example value for each part
<!-- proven by test CP1-T14 in tests/test_objective.py, commit 87782cd -->

### Requirement: Reviewers and builders work toward the objective
The system SHALL do this: Every reviewer, builder and fixer is told the plan's objective first, reviewers are told the realistic-use bar, and a blocker or major finding that does not say who is hurt and how is recorded for the final review and never blocks.

- The prompt sent to another company's reviewer (Codex, Gemini, Copilot and so on) starts with the plan's objective, and the reviewer brief Ratchet prints for its own sub-agents includes it too
- The adversary instructions and the adversarial-loop skill state the realistic-use bar: who the users are, that deliberate evasion is out of scope, and that severity follows real harm in normal use
- Each review finding carries a user_impact line (who is hurt and how, in normal use); a blocker or major without one never blocks, at any round and any risk, and is listed at the final review and in ratchet.py deferred with the reason
- The orchestrator skill and the implementer, fixer and test-writer instructions put the objective at the top of every brief
- A run whose plan has no objective still reviews normally, with a note that none was stated

#### Scenario: The prompt sent to an other-company reviewer opens with the plan's objective
- **GIVEN** a plan with an objective and an open adversarial round that picked the fake 'codex' reviewer
- **WHEN** external_review.py --dry-run builds that reviewer's prompt
- **THEN** the kept prompt file contains the objective's users and goal text, appearing before the adversary review instructions
<!-- proven by test CP2-T01 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: The reviewer's required reply shape asks for user_impact
- **GIVEN** the same dry-run prompt as CP2-T01
- **WHEN** this behaviour is exercised
- **THEN** the JSON reply shape shown to the reviewer includes a user_impact field next to severity in the findings example
<!-- proven by test CP2-T02 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: ratchet.py reviewers prints the objective before the reviewer list
- **GIVEN** a plan with an objective and an open adversarial round
- **WHEN** ratchet.py reviewers CPn is run, both in plain text and with --json
- **THEN** the --json output has an 'objective' field with the plan's full objective, and the plain-text output prints it before the per-reviewer rows
<!-- proven by test CP2-T03 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: A host sub-agent reviewer's brief states the objective
- **GIVEN** a plan with an objective, no other-company providers configured, so the round uses a host sub-agent reviewer
- **WHEN** ratchet.py reviewers CPn prints that reviewer's launch instructions ('how')
- **THEN** the instructions name the objective's users and goal, so a freshly spawned ratchet-adversary sub-agent sees it directly
<!-- proven by test CP2-T04 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: A blocker finding with no user_impact never blocks, even on a high-risk step
- **GIVEN** a high-risk step (where normally every severity blocks) with one reviewer returning a single blocker finding that has no user_impact field
- **WHEN** ratchet.py adv-record CPn records that review
- **THEN** the gate is approved (no blocking findings), and the finding is written to the round's deferred list with deferred_because 'no user impact stated'
<!-- proven by test CP2-T05 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: A major finding and one with an unknown severity, both with no user_impact, defer the same way
- **GIVEN** one reviewer returning a major finding and another returning a finding with severity 'critical' (unknown to Ratchet), neither with user_impact
- **WHEN** adv-record records both
- **THEN** both are deferred with 'no user impact stated' and neither blocks, confirming the rule treats an unknown severity like major but still exempts it when user_impact is missing
<!-- proven by test CP2-T06 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: The no-impact rule holds in a later round too
- **GIVEN** a step already on its second adversarial round (first round deferred a no-impact blocker)
- **WHEN** round 2's reviewer again returns a blocker finding with no user_impact
- **THEN** it is deferred again with the same reason and the gate still passes, showing the rule is not limited to round 1
<!-- proven by test CP2-T07 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: A blocker finding that does state user_impact still blocks as before
- **GIVEN** one reviewer returns two findings: a blocker with a user_impact line, and a blocker with none
- **WHEN** adv-record records the review
- **THEN** the gate fails with exactly one blocking finding (the one with user_impact); the other is deferred with 'no user impact stated', proving the new rule narrows to missing user_impact only and does not weaken the existing bar
<!-- proven by test CP2-T08 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: A minor finding with no user_impact keeps following the existing minor rules, not the new one
- **GIVEN** a high-risk step (minors block there under the existing severity bar) with a reviewer returning a minor finding that has no user_impact
- **WHEN** adv-record records the review
- **THEN** the finding still blocks under the existing high-risk rule, and its deferred_because (if any) is not 'no user impact stated' — the missing-user_impact exemption applies only to blocker, major and unknown severities
<!-- proven by test CP2-T09 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: ratchet.py deferred lists the no-impact reason
- **GIVEN** the round from CP2-T05, already recorded
- **WHEN** ratchet.py deferred and ratchet.py deferred --json are run
- **THEN** the finding appears with the plain reason 'no user impact stated', readable by a human and present in the JSON
<!-- proven by test CP2-T10 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: A run with no stated objective still reviews normally
- **GIVEN** a project whose checkpoints.json has no top-level objective (a run approved before this change)
- **WHEN** ratchet.py reviewers CPn opens a round and a reviewer's prompt/brief is built
- **THEN** nothing crashes, the round opens and records as before, and the prompt/brief carries a plain note that no objective was stated for this run
<!-- proven by test CP2-T11 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: The adversary instructions and adversarial-loop skill state the realistic-use bar
- **GIVEN** agents/ratchet-adversary.md and skills/ratchet-adversarial-loop/SKILL.md
- **WHEN** this behaviour is exercised
- **THEN** both files say, in generic terms (not Ratchet-specific), who the project's users are and that severity follows real harm to them in normal use, and that deliberate same-user evasion is out of scope
<!-- proven by test CP2-T12 in tests/test_review_objective.py, commit 618414b -->

#### Scenario: Every sub-agent brief carries the objective near the top
- **GIVEN** agents/ratchet-implementer.md, agents/ratchet-fixer.md, agents/ratchet-test-writer.md and skills/ratchet-orchestrator/SKILL.md
- **WHEN** this behaviour is exercised
- **THEN** each one instructs the orchestrator to hand that sub-agent the plan's objective at the top of its brief, not buried or omitted
<!-- proven by test CP2-T13 in tests/test_review_objective.py, commit 618414b -->

### Requirement: User can accept open review findings
The system SHALL do this: When a review round blocks, the user can accept chosen open findings with their own reason, so the step finishes without another round, and Ratchet records each one and shows it at the final review.

- After a review round blocks, Ratchet can ask the user, word for word, to accept chosen open findings, and the question shows the exact reply that counts: accept: followed by their reason
- Only the user's own first reply in the run's chat, in that form, accepts them; an agent cannot accept a finding by itself, and an unknown or already-closed finding id is refused
- Each accepted finding and the user's reason are written to learnings and the deferred list, and marked accepted in that round's record
- When every blocking finding of the round is accepted, the step passes review without another round; accepting only some leaves it blocked; if the code changes afterwards, the normal review rules apply again
- status, plan-summary and the final review list accepted findings with the reason, blockers and majors first; README, the orchestrator skill and schemas.md explain it

#### Scenario: Asking to accept findings prints the question word for word, with the exact reply that counts
- **GIVEN** a step whose latest adversarial round blocked with open findings F1 (blocker) and F2 (major)
- **WHEN** the agent runs `ratchet.py ask accept-finding CP1 F1,F2`
- **THEN** the printed question names F1 and F2 and states the reply that accepts them is exactly `accept: ` followed by their reason, word for word, with no other phrasing offered as equivalent
<!-- proven by test CP3-T01 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: Cannot ask to accept findings when the round did not block
- **GIVEN** the same step's adversarial gate already passed (no open findings on record)
- **WHEN** the agent runs `ratchet.py ask accept-finding CP1 F1`
- **THEN** the command is refused and nothing is asked; state is unchanged
<!-- proven by test CP3-T02 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: An unknown finding id is refused
- **GIVEN** the round's open findings are only F1 and F2
- **WHEN** the agent runs `ratchet.py ask accept-finding CP1 F1,F9` (F9 never existed)
- **THEN** the command is refused, naming F9 as unknown; neither F1 nor F2 is accepted and open-findings.json is unchanged
<!-- proven by test CP3-T03 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: Accepting the same finding twice is refused the second time
- **GIVEN** F1 was already accepted (via a genuine accept: reply) earlier in this same round
- **WHEN** the agent asks to accept F1 again and the user replies `accept: still fine`
- **THEN** the second attempt is refused as already closed; learnings.md and the deferred list gain no duplicate entry for F1
<!-- proven by test CP3-T04 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: A finding already deferred as minor cannot be "accepted" through this flow
- **GIVEN** F3 is a minor finding of this round already recorded as deferred (never blocked), while F1/F2 are the only open blocking findings
- **WHEN** the agent runs `ratchet.py ask accept-finding CP1 F3`
- **THEN** the command is refused because F3 is not an open blocking finding of the round; F3's existing deferred record is untouched
<!-- proven by test CP3-T05 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: Nothing is accepted before the user has actually replied
- **GIVEN** `ratchet.py ask accept-finding CP1 F1,F2` was just asked and no message has been recorded since
- **WHEN** the agent immediately runs `ratchet.py accept-finding CP1`
- **THEN** the command is refused for lack of a reply; F1 and F2 remain open and the step remains blocked
<!-- proven by test CP3-T06 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: A reply typed in a different chat does not accept the findings
- **GIVEN** the accept-finding question is owned by chat session s-owner
- **WHEN** a message `"accept: because it's fine"` is recorded in a different session s-other, and the agent then runs `ratchet.py accept-finding CP1`
- **THEN** the command is refused (no reply yet in the owning chat); F1 and F2 remain open
<!-- proven by test CP3-T07 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: The user's own first reply accepts the named findings with their reason
- **GIVEN** `ask accept-finding CP1 F1,F2` is waiting, and the owner chat's first message afterward is exactly `"accept: we'll live with this for the beta"`
- **WHEN** the agent runs `ratchet.py accept-finding CP1`
- **THEN** F1 and F2 are each recorded accepted with reason "we'll live with this for the beta", appended to .ratchet/learnings.md, added to the step's deferred list, and marked accepted in this round's record
<!-- proven by test CP3-T08 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: Accepting every open finding of the round passes review without another round
- **GIVEN** the round's only open findings are F1 and F2
- **WHEN** the user's reply accepts F1,F2 and the agent runs `ratchet.py accept-finding CP1`
- **THEN** the adversarial gate for this checkpoint is marked passed for the round's fingerprint, with no new review round opened, and `ratchet.py complete CP1`'s re-check accepts it
<!-- proven by test CP3-T09 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: Accepting only some findings leaves the step blocked
- **GIVEN** the round's open findings are F1 and F2
- **WHEN** the user's reply accepts only F1
- **THEN** F1 is recorded accepted but F2 is still open, the adversarial gate is still not passed, and the step is still blocked on F2
<!-- proven by test CP3-T10 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: A code change after acceptance brings back the normal review rules
- **GIVEN** F1 and F2 were both accepted and the adversarial gate passed for that round's fingerprint
- **WHEN** the code changes (a new fingerprint) and `ratchet.py reviewers CP1` / `adv-record` runs again
- **THEN** the earlier acceptance does not exempt the new round; a finding reintroduced by the new round blocks normally and must go through its own review or its own accept-finding
<!-- proven by test CP3-T11 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: status, plan-summary and the final review list accepted findings, most severe first
- **GIVEN** F1 (blocker) and F2 (major) were accepted with reasons, and a minor finding elsewhere was separately deferred without acceptance
- **WHEN** the agent runs `ratchet.py status`, `ratchet.py plan-summary` and (once the run reaches it) the final review text
- **THEN** each listing shows F1 and F2 with their accepted reason, ordered before the plain deferred minor finding
<!-- proven by test CP3-T12 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: When blocked at the round limit, accepting findings is offered as a lettered option
- **GIVEN** a step hit its adversarial round limit while F1 and F2 are still open
- **WHEN** the agent reads the blocked-state options Ratchet offers
- **THEN** one lettered option is accepting the open findings (leading to the accept-finding flow), alongside the other existing options
<!-- proven by test CP3-T13 in tests/test_accept_finding.py, commit 93c99ba -->

#### Scenario: The docs explain the accept-finding flow
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md as shipped
- **WHEN** the three are searched for the accept-finding flow
- **THEN** all three describe a user accepting open review findings and the exact `accept: <reason>` reply form
<!-- proven by test CP3-T14 in tests/test_accept_finding.py, commit 93c99ba -->

### Requirement: Check in with the objective when reviews drag
The system SHALL do this: When a step's reviews start dragging on, Ratchet makes the orchestrator decide fix or rebut for every blocking finding, pauses to ask whether the work still serves the objective (simplify, accept or stop), and reports the final review against the objective.

- When a step's second review round still blocks, or fix rounds twice bring a new kind of finding into a file that already had blocking findings, Ratchet stops before more fixing and prints a check-in: the objective, the open findings with their user impact, and the options simplify, accept or stop; no new round opens until it is answered (simplify recorded with the reasoning from a strong-model consult, accept only through the user's own accept: reply, stop asks the user how to wrap up)
- After a round blocks, no new round opens until every blocking finding has a decision, fix or rebut, each with a one-line reason tied to the objective; the fixer is handed only the fix findings, the next round's reviewers see the rebuttals and either withdraw or re-raise with a stronger user-impact case, and the orchestrator can never defer or accept a finding
- The final review opens with the objective and lists, against what done means, the steps finished, the findings accepted or set aside, and anything not built; README, both skills and schemas.md describe all of the above

#### Scenario: Second blocking review round in a row pauses for a check-in before any more fixing
- **GIVEN** CP2 blocked at round 1 with a major finding, was fixed, and round 2 still blocks with a different major finding, each finding carrying a user_impact line
- **WHEN** the round-2 review is recorded as not approved
- **THEN** Ratchet does not just open round 3; `ratchet.py status` (or `check-in CP2`) shows the run paused on a check-in that prints the plan's objective goal, every open finding's user_impact, and three options lettered for simplify, accept, and stop
<!-- proven by test CP4-T01 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: A new review round cannot be opened while a check-in is waiting
- **GIVEN** round 2 of CP2 just triggered the check-in from CP4-T01 and it has not been answered yet
- **WHEN** someone runs `ratchet.py reviewers CP2` to start round 3 anyway
- **THEN** the command is refused (non-zero exit) with a message telling them a check-in is open and naming the command to resolve it, and no round 3 is opened
<!-- proven by test CP4-T02 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: check-in with no decision prints the brief for a human/strong-model consult
- **GIVEN** CP2's check-in from CP4-T01 is open
- **WHEN** `ratchet.py check-in CP2` is run with no --decision flag
- **THEN** it prints (and --json returns) the objective, every open finding's user_impact, how many review rounds this step has used, and the finding kinds grouped by the file they are in, without changing phase or deciding anything
<!-- proven by test CP4-T03 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: Simplify is recorded without asking the user, and reviews can then continue
- **GIVEN** CP2's check-in from CP4-T01 is open
- **WHEN** `ratchet.py check-in CP2 --decision simplify --why "dropping the optional retry path keeps the fix small"` is run
- **THEN** the command succeeds immediately with no pending user question, the reasoning is recorded against this check-in, and `ratchet.py reviewers CP2` can now open round 3
<!-- proven by test CP4-T04 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: A round-count check-in fires at most once per step
- **GIVEN** CP2's round-count check-in already fired at round 2 and was answered with simplify (CP4-T04), and round 3 also comes back blocking
- **WHEN** the round-3 review is recorded as not approved
- **THEN** round 3 blocking (by itself, with no new finding kind in an already-blocking file) does not reopen a second round-count check-in; the run proceeds under the normal round-limit rules instead
<!-- proven by test CP4-T05 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: Two new kinds of finding in a row in an already-flagged file also trigger a check-in
- **GIVEN** src/login.js had a blocking finding in round 1; round 2's fix introduces a different, previously-unseen finding kind still in src/login.js (first repeat) and round 3's fix introduces yet another new kind still in src/login.js (second repeat)
- **WHEN** the round-3 review is recorded as not approved
- **THEN** a check-in opens at round 3 even though the round-count trigger is not the cause, because this is the second round where an unseen finding kind shows up in a file an earlier round already blocked
<!-- proven by test CP4-T06 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: check-in cannot itself accept a finding
- **GIVEN** CP2's check-in from CP4-T01 is open
- **WHEN** `ratchet.py check-in CP2 --decision accept` is run
- **THEN** it is refused: accepting an open finding must go through the accept-finding flow (CP3), which needs the user's own reply, so a cooperative agent cannot wave its own findings through via check-in
<!-- proven by test CP4-T07 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: Stop hands the decision to the user
- **GIVEN** CP2's check-in from CP4-T01 is open
- **WHEN** `ratchet.py check-in CP2 --decision stop` is run
- **THEN** no new review round opens and the run moves into the existing ask/blocked flow asking the user how to wrap up, the same way other blocked-run choices are presented
<!-- proven by test CP4-T08 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: The round count for a check-in is configurable
- **GIVEN** config.json sets checkin_round to 3 and CP2 blocks at round 1, is fixed, and blocks again at round 2 with a different major finding
- **WHEN** the round-2 review is recorded as not approved
- **THEN** no check-in opens yet (round 2 < 3) and `ratchet.py reviewers CP2` can open round 3 normally; only a third blocking round would trigger the check-in
<!-- proven by test CP4-T09 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: The final review reports progress against the objective
- **GIVEN** a two-step plan whose objective lists three done_means lines; CP1 finishes clean and CP2 finishes with one finding accepted earlier via CP3's accept-finding flow
- **WHEN** the run reaches awaiting_final_review
- **THEN** `ratchet.py status` (and `status --json`) opens with the objective's goal and users, and lists each done_means line, naming which are met and which are not, plus the finding that was accepted along the way
<!-- proven by test CP4-T10 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: A plan with no objective still reaches check-in and final review without crashing
- **GIVEN** a plan approved before the objective field existed (no top-level objective key) hits the same round-2-blocks-twice pattern as CP4-T01 and later reaches awaiting_final_review
- **WHEN** the round-2 review is recorded as not approved, and later the run completes
- **THEN** the check-in still opens with the open findings and the three options, and the final review still prints, each plainly noting no objective was stated instead of raising an error
<!-- proven by test CP4-T11 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: An open check-in does not let the step complete on its own
- **GIVEN** CP2's check-in from CP4-T01 is open and unanswered
- **WHEN** `ratchet.py complete CP2` is run
- **THEN** it is refused because the adversarial gate is still not passed; a check-in pauses the review loop, it does not substitute for a passed review
<!-- proven by test CP4-T12 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: The next round is refused until every blocking finding from the round that just blocked has a fix-or-rebut decision
- **GIVEN** CP2 round 1 blocks with two blocking findings, reviewer-a/F1 and reviewer-b/F2, each carrying a user_impact
- **WHEN** `ratchet.py triage CP2 reviewer-a/F1=fix:"straightforward null check"` is run, then `ratchet.py reviewers CP2` is run to try to open round 2 while F2 still has no decision
- **THEN** opening round 2 is refused (non-zero exit), naming F2 as the undecided finding; once `ratchet.py triage CP2 reviewer-b/F2=rebut:"this path never runs for a signed-in user"` is also run, `ratchet.py reviewers CP2` opens round 2 normally
<!-- proven by test CP4-T13 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: Triage decisions are restricted to fix or rebut, and a rebut needs a reason
- **GIVEN** CP2 round 1 blocks with one blocking finding, reviewer-a/F1
- **WHEN** `ratchet.py triage CP2 reviewer-a/F1=rebut` is run with no reason text, then `ratchet.py triage CP2 reviewer-a/F1=accept:"looks fine"` is run, then `ratchet.py triage CP2 reviewer-a/F1=defer:"later"` is run
- **THEN** all three are refused: the empty rebut names the missing reason, and accept/defer/waive are refused with a message pointing the orchestrator at the accept-finding flow (CP3), since the orchestrator can never defer or accept a finding through triage; F1 still has no recorded decision afterward
<!-- proven by test CP4-T14 in tests/test_checkin.py, commit cc6452d -->

#### Scenario: The fixer is handed only the fix items, and the next round's reviewers see the rebuttal
- **GIVEN** CP2 round 1 blocks with reviewer-a/F1 (a real bug) and reviewer-b/F2 (disputed)
- **WHEN** `ratchet.py triage CP2 reviewer-a/F1=fix:"add the null check" reviewer-b/F2=rebut:"F2's scenario can't occur for a signed-in user, per the objective"` is run and round 2 is then opened
- **THEN** the fixer's brief (fix-list.json) lists only F1, not F2; round 2's reviewer brief/prompt includes F2's rebuttal text so that round's reviewers can withdraw it or re-raise it with a stronger user_impact
<!-- proven by test CP4-T15 in tests/test_checkin.py, commit cc6452d -->

### Requirement: Later review rounds cost less
The system SHALL do this: After round 1 only reviewers with open findings run again, a reviewer listed twice runs once, a round nobody reviewed is free, and from round 3 minor findings no longer block.

- After round 1, only reviewers whose lens still has an open blocker or major run again; a lens that approved is not re-run, and a code change that leaves a blocker open brings back the full set
- When two review slots would go to the same reviewer (same company, model and lens), it runs once and the approval message gives the real number of distinct reviewers
- A round replaced before any reviewer wrote a review does not count toward the step or run limit; a round where any review was written still counts
- From round 3 a minor finding never blocks; it is listed at the final review and in ratchet.py deferred
- plan-summary, README, schemas.md and the adversarial-loop skill describe these rules in plain words, and the fixer is told to fix only the listed findings without adding new mechanisms

#### Scenario: A lens that approved last round is not asked again
- **GIVEN** normal-risk CP1, round 1: the standards-lens reviewer reports one major finding on src/f.js, the correctness-lens reviewer approves with no findings; the finding still blocks, so the fixer edits src/f.js and `behavior CP1` passes again
- **WHEN** `ratchet.py reviewers CP1` opens round 2
- **THEN** round 2's reviewer list has no entry whose lens is 'correctness'
<!-- proven by test CP5-T01 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: A lens with an open major is asked again
- **GIVEN** the same round 1 as CP5-T01
- **WHEN** round 2 opens
- **THEN** round 2's reviewer list has exactly one entry, and its lens is 'standards'
<!-- proven by test CP5-T02 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: A fix that leaves the blocker open brings back the full reviewer set
- **GIVEN** round 2 from CP5-T01/T02: the lone standards reviewer reports the same (still unresolved) major finding again, so `adv-record` still refuses; the fixer edits src/f.js again and `behavior CP1` passes
- **WHEN** `ratchet.py reviewers CP1` opens round 3
- **THEN** round 3's reviewer list is back to two entries, one per lens (standards and correctness) — the narrowed round did not carry over
<!-- proven by test CP5-T03 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: Two slots that resolve to the same reviewer run once, and the approval message says so
- **GIVEN** two registered providers idA and idB, both company openai and model gpt-5 (different ids, same fake CLI); CP1's plan lists reviewers: ["idA-standards", "idB-standards"] explicitly
- **WHEN** `ratchet.py reviewers CP1` opens the round, that one reviewer approves with no findings, and `ratchet.py adv-record CP1` records it
- **THEN** the opened round required exactly one reviewer (not two), and the success message reports 'all 1 reviewer(s)', naming the true distinct count rather than the two configured slots
<!-- proven by test CP5-T04 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: Replacing a round before anyone wrote a review costs nothing
- **GIVEN** round 1 is opened for CP1 (no reviewer has written any file into its round folder yet); a tracked file is edited so the fingerprint changes
- **WHEN** `ratchet.py reviewers CP1` runs again, opening round 2, and its one reviewer then approves so `adv-record` records it
- **THEN** state.json shows gates.adversarial.attempts == 1 and adv_rounds_total == 1 after round 2 is recorded — round 1 was never counted
<!-- proven by test CP5-T05 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: Replacing a round where a review was already written still counts
- **GIVEN** round 1 is opened for CP1 and one reviewer's review file is written into its round folder (via write_review), but `adv-record` is never called so the round stays open; a tracked file is then edited so the fingerprint changes
- **WHEN** `ratchet.py reviewers CP1` runs again, opening round 2, and both its reviewers approve so `adv-record` records it
- **THEN** state.json shows gates.adversarial.attempts == 2 and adv_rounds_total == 2 — round 1 counted because a review had already been written to its folder
<!-- proven by test CP5-T06 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: From round 3, a minor finding in just-changed code no longer blocks, and shows up in `deferred`
- **GIVEN** normal-risk CP1 carried through round 1 and round 2 with the same minor finding ('needs a comment') reported each round by one lens on src/f.js, which is edited again before each round (so it stays in round 2's scope and round 2 still blocks, matching the existing rule); the file is edited once more before round 3
- **WHEN** round 3 opens, the same minor finding is reported again on src/f.js, and `ratchet.py adv-record CP1` records it
- **THEN** the gate PASSES (no blocking findings) even though src/f.js just changed, and `ratchet.py deferred` lists 'needs a comment' for CP1
<!-- proven by test CP5-T07 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: From round 3, a major finding still blocks
- **GIVEN** the same CP5-T07 setup, but the round-3 finding on the just-changed src/f.js carries severity 'major' instead of 'minor'
- **WHEN** `ratchet.py adv-record CP1` records round 3
- **THEN** it exits non-zero: the round-3 convergence rule only ever waves through minor findings, never majors or blockers
<!-- proven by test CP5-T08 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: The round minors stop blocking from is a config setting, not a hardcoded 3
- **GIVEN** normal-risk CP1 with config converge_after_round=1; round 1 has a blocking major (as in CP5-T01) so round 2 opens
- **WHEN** round 2's lone reviewer reports a fresh minor finding on the just-changed file and `adv-record` records it
- **THEN** round 2 already passes without blocking (minors stop blocking from round converge_after_round+1 = 2), proving the threshold is read from config
<!-- proven by test CP5-T09 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: The round refund never goes below zero
- **GIVEN** round 1 for CP1 is opened and replaced twice in a row with no review ever written to either folder (two tracked-file edits, two `reviewers CP1` calls)
- **WHEN** the resulting round's one reviewer then approves and `adv-record` records it
- **THEN** state.json's gates.adversarial.attempts and adv_rounds_total are 1, never negative or inflated by the two free replacements
<!-- proven by test CP5-T10 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: The later-rounds-cost-less rules are written down in plain words
- **GIVEN** skills/ratchet-adversarial-loop/SKILL.md, skills/ratchet-orchestrator/references/schemas.md and README.md as shipped
- **WHEN** they are read as text
- **THEN** the adversarial-loop SKILL.md no longer claims every reviewer (including ones that approved) reviews every round; instead it states, in plain words, that only a lens with an open blocker/major is re-run, that a round replaced before any review was written is free, that duplicate slots to the same reviewer run once with the real count shown, and that minor findings stop blocking from a configurable round; schemas.md documents the config key(s) and state fields this uses
<!-- proven by test CP5-T11 in tests/test_converge.py, commit 16e6ceb -->

#### Scenario: The fixer is told to fix only the listed findings, not invent new mechanisms
- **GIVEN** agents/ratchet-fixer.md as shipped
- **WHEN** it is read as text
- **THEN** it instructs the fixer to fix every (or rebut every) finding in the given findings file and touch nothing unrelated, with no new instruction asking it to redesign the round/reviewer-selection mechanism itself
<!-- proven by test CP5-T12 in tests/test_converge.py, commit 16e6ceb -->

### Requirement: One flaky failure does not cost a round
The system SHALL do this: A test run that fails once and passes on an immediate re-run, or a reviewer reply cut off mid-answer, no longer uses up an attempt, sidelines that reviewer for hours, or throws away its finished reviews.

- A failing test run that passes on one immediate automatic re-run passes the gate marked flaky (in status, evidence and the final review) without using an attempt, once per step; a second flaky failure or two real failures count normally
- The full regression run when a step completes gets the same one-time allowance
- When another company's reviewer reply is cut off mid-answer, Ratchet retries that review once; if it is cut off again only that reviewer slot falls back for this round, the provider is not sidelined for hours, and reviews it already finished in the round are kept
- A reviewer that really is unavailable (not installed, login or quota failure, time-out) is still skipped as before
- README, schemas.md and the adversarial-loop skill describe both rules
- The prompt hook recognises a sub-agent hand-back by the text it really receives, which starts with `<agent-message from="...">` (the "Another Claude session sent a message:" wording is only the CLI's on-screen rendering), so such a turn is never recorded as the user's reply; a real user message that merely mentions agent-message mid-sentence is still recorded
- When a review prompt's diff is too large to include whole, external_review.py puts the files changed since the previous review round first (then the rest), and says plainly in the prompt which files were cut, so a reviewer always sees what changed since the last round

#### Scenario: A flaky test failure passes on one immediate re-run without spending an attempt
- **GIVEN** the behavior command fails on its first run and passes on its second, and this step has not yet used its flaky allowance
- **WHEN** ratchet.py behavior CP6x is run once
- **THEN** the gate passes, is marked 'flaky', and gates.behavior.attempts is unchanged from before this call
<!-- proven by test CP6-T01 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A second flaky failure in the same step is not forgiven
- **GIVEN** this step already used its one flaky allowance (CP6-T01's scenario already happened)
- **WHEN** ratchet.py behavior CP6x is run again against a command that fails then would pass on an immediate retry
- **THEN** the failure is reported as a normal gate failure (not flaky), and gates.behavior.attempts increases by one
<!-- proven by test CP6-T02 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: Two genuine consecutive failures are never marked flaky
- **GIVEN** the behavior command fails on every invocation
- **WHEN** ratchet.py behavior CP6x is run
- **THEN** the gate is reported failed as before (no 'flaky' mark), gates.behavior.attempts increases by one, and a further real run is needed to retry
<!-- proven by test CP6-T03 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A flaky pass is visible in status, evidence and the final review
- **GIVEN** a step just passed its behavior gate via the flaky allowance (CP6-T01's scenario)
- **WHEN** ratchet.py status --json is read and the evidence folder for this checkpoint is listed
- **THEN** status marks the gate flaky, both the failing and passing behavior logs are kept in evidence, and the text Ratchet prints at the final review lists this step as flaky
<!-- proven by test CP6-T04 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: The regression run at complete gets its own one-time flaky allowance
- **GIVEN** every gate for CP6x has passed (its own behavior-gate flaky allowance, if any, already used earlier in the step) and cfg.regression_command fails once then passes on an immediate re-run
- **WHEN** ratchet.py complete CP6x is run
- **THEN** the run completes, the regression result is marked flaky, and no human-decision block is raised for it
<!-- proven by test CP6-T05 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A second regression failure is not forgiven
- **GIVEN** the regression command's one-time allowance for this step has already been used (CP6-T05's scenario), and it now fails again
- **WHEN** ratchet.py complete CP6x is run
- **THEN** complete is refused with the regression failure reported plainly (not flaky), exactly as before this change
<!-- proven by test CP6-T06 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A reply cut off mid-answer is retried once and the retry's reply is used
- **GIVEN** a reviewer's fake CLI replies with JSON truncated mid-value on its first call (an opening '{"findings"' with no matching close, exit 0) and a complete '{"findings": []}' reply on its second call
- **WHEN** external_review.py is run for that reviewer
- **THEN** it is called exactly twice, the review file written to the round folder is the complete one, and the reviewer is not marked unavailable (reviewer-status still shows it available)
<!-- proven by test CP6-T07 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A second cut-off in the same round falls back only that slot, not the whole provider
- **GIVEN** two review slots use the same CLI binary (same provider, different lens); one slot's fake CLI replies with truncated JSON on every call
- **WHEN** ratchet.py reviewers CP6x is run again in the same round after that slot's two cut-off attempts
- **THEN** only that slot is replaced by the next alternative reviewer for this round; `ratchet.py reviewer-status --json` shows neither that reviewer nor the other slot's reviewer marked unavailable, unlike a genuine quota/auth failure
<!-- proven by test CP6-T08 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A finished review in the round is kept when another slot falls back
- **GIVEN** round 1 has one slot's review file already written in its round folder, and a different slot just fell back after a cut-off
- **WHEN** ratchet.py reviewers CP6x is run again in the same round
- **THEN** the already-written review file is untouched (same content, same path) and that slot is not re-resolved or asked to run again
<!-- proven by test CP6-T09 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A plain no-JSON failure is still marked unavailable immediately, with no retry
- **GIVEN** a reviewer's fake CLI either is missing from PATH or exits 1 printing a plain error with no '{' in its output at all (e.g. 'error: quota exceeded')
- **WHEN** external_review.py is run for that reviewer
- **THEN** it is called exactly once (no retry wasted on a reply that was never JSON), it is marked unavailable for unavailable_ttl_hours, and its slot falls back to the next reviewer, exactly as before this change
<!-- proven by test CP6-T10 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A hung reviewer times out once, not twice
- **GIVEN** a reviewer's fake CLI sleeps past the configured --timeout on every call
- **WHEN** external_review.py is run for that reviewer
- **THEN** it is called exactly once and marked unavailable after the single timeout (the cut-off retry never doubles the wait on a hang)
<!-- proven by test CP6-T11 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: Docs describe both the flaky-test allowance and the cut-off retry
- **GIVEN** README.md, skills/ratchet-orchestrator/references/schemas.md and skills/ratchet-adversarial-loop/SKILL.md as shipped
- **WHEN** their text is read
- **THEN** each names, in plain words, the one-time flaky allowance for a test run (behavior and regression) and the rule that a cut-off reviewer reply is retried once before only its own slot falls back
<!-- proven by test CP6-T12 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A sub-agent hand-back wrapped in the real marker is not recorded as the user's reply
- **GIVEN** a human-approve question is waiting and a prompt arrives whose text starts with `<agent-message from="builder">` followed by a message that itself reads 'yes'
- **WHEN** the prompt hook runs on that turn
- **THEN** the turn is not recorded in human_log.jsonl or prompt-chats.jsonl, the waiting question stays unanswered, and the hook tells the agent this turn was not typed by the user
<!-- proven by test CP6-T13 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: The on-screen rendering string alone is still caught
- **GIVEN** a human-approve question is waiting and a prompt's first line is 'Another Claude session sent a message: yes' with no `<agent-message>` wrapper around it
- **WHEN** the prompt hook runs on that turn
- **THEN** the turn is still not recorded and the waiting question stays unanswered, exactly as before this change
<!-- proven by test CP6-T14 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A real user message that only mentions agent-message mid-sentence is recorded
- **GIVEN** a human-approve question is waiting and the user's own first line reads 'I saw an agent-message come through earlier, but yes, go ahead'
- **WHEN** the prompt hook runs on that turn
- **THEN** the turn is recorded as usual in human_log.jsonl and the waiting question is answered by it
<!-- proven by test CP6-T15 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: A too-large review diff puts files changed since the previous round first
- **GIVEN** round 2 is opening for a step where round 1 already ran, the file reviewed in round 1 is unchanged since, a different file has changed since round 1, and the combined diff is larger than the configured limit
- **WHEN** external_review.py builds the round 2 prompt for a reviewer
- **THEN** the file changed since round 1 appears in the diff section before the rest of the diff, and it is not the part cut by the size limit
<!-- proven by test CP6-T16 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: The prompt names which files were cut
- **GIVEN** the same oversized round 2 diff as CP6-T16, sized so some files still cannot fit even after prioritizing files changed since round 1
- **WHEN** external_review.py builds the round 2 prompt
- **THEN** the prompt states plainly, by name, which files' diffs were left out, rather than only a generic 'diff truncated at N chars' note
<!-- proven by test CP6-T17 in tests/test_flaky.py, commit 4cb426d -->

#### Scenario: Round 1 keeps the plain diff order
- **GIVEN** round 1 is opening for a step (no previous review round exists) and the diff is larger than the configured limit
- **WHEN** external_review.py builds the round 1 prompt
- **THEN** the diff is still produced and truncated as before this change, with no 'previous round' prioritization applied
<!-- proven by test CP6-T18 in tests/test_flaky.py, commit 4cb426d -->
