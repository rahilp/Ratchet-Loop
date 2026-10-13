# Reviewer choice that is aware of quota and cost: Ratchet spreads reviews across the providers that work (for example Codex for correctness, a cheaper flat-rate OpenCode Go model for standards) instead of sending every round to the same one, knows when a provider has run out and when it comes back, prefers flat-rate subscriptions, and never spends per-use money without the developer's own OK.

## Purpose
What this feature does, as built and checked by Ratchet (run reviewer-choice-that-is-aware-of-quota-and-cost, 2026-10-13). Each scenario names the test that proves it.

## Requirements

### Requirement: Checking a reviewer name tests its provider
The system SHALL do this: An agent that asks Ratchet to check a reviewer name like codex-correctness gets that provider actually tested, or a plain error naming the known providers.

- Asking to check 'codex-correctness' sends one test request to the codex provider and shows its fresh result
- Asking to check a provider id still works as before
- Asking to check a name that matches no provider or reviewer fails with a message listing the known provider ids, and nothing is tested

#### Scenario: Checking 'codex-correctness' tests the codex provider and shows a fresh result
- **GIVEN** A project with providers codex and gemini. Codex was tested earlier while its fake CLI failed, so its saved result says failing. The fake codex now works again.
- **WHEN** The agent runs `providers --check codex-correctness --json`
- **THEN** The command succeeds; the codex fake's log gains exactly one test request and the gemini fake's log gains none; the codex row in the output is ok (working) instead of the old failing result, and the saved provider result for codex is now ok
<!-- proven by test CP1-T01 in tests/test_quota_check.py, commit 69e7743 -->

#### Scenario: Checking a provider id still works as before
- **GIVEN** A project with providers codex and gemini, both fakes working
- **WHEN** The agent runs `providers --check codex --json`
- **THEN** Exactly one test request reaches the codex fake and none reaches gemini; the codex row is ok
<!-- proven by test CP1-T02 in tests/test_quota_check.py, commit 69e7743 -->

#### Scenario: Checking 'all' still tests every installed provider
- **GIVEN** A project with providers codex and gemini, both fakes working
- **WHEN** The agent runs `providers --check all --json`
- **THEN** One test request reaches each of the codex and gemini fakes, and both rows are ok
<!-- proven by test CP1-T03 in tests/test_quota_check.py, commit 69e7743 -->

#### Scenario: A name that matches no provider or reviewer fails and tests nothing
- **GIVEN** A project with providers codex and gemini; nothing is registered under 'mystery'
- **WHEN** The agent runs `providers --check mystery-correctness` and then `providers --check mystery`
- **THEN** Both commands exit non-zero with a plain message that names the unknown name and lists the known provider ids (codex and gemini); neither fake's log has any call; no provider result is saved for either
<!-- proven by test CP1-T04 in tests/test_quota_check.py, commit 69e7743 -->

#### Scenario: A reviewer name for one model of a shared program tests only that model
- **GIVEN** A project with providers opencode-glm and opencode-kimi, both run by the same fake opencode program, which logs the model in each call
- **WHEN** The agent runs `providers --check opencode-glm-standards --json`
- **THEN** The fake's log has exactly one test request and it names opencode-go/glm-5.3; there is no call naming kimi; the opencode-glm row is ok and opencode-kimi is left as it was (not tested)
<!-- proven by test CP1-T05 in tests/test_quota_check.py, commit 69e7743 -->

#### Scenario: All three lens names reach the provider; an invalid lens does not
- **GIVEN** A project with providers codex and gemini, both fakes working
- **WHEN** The agent runs `providers --check codex-standards`, then `codex-both`, then `codex-bogus`
- **THEN** The first two succeed and each sends one test request to the codex fake (two in total); the third exits non-zero with the known-providers message and the codex log gains no further call
<!-- proven by test CP1-T06 in tests/test_quota_check.py, commit 69e7743 -->

#### Scenario: A failing provider checked by reviewer name is reported as failing with the provider's own reason
- **GIVEN** A project with providers codex and gemini; the gemini fake is set to fail with 'error: rate limited'
- **WHEN** The agent runs `providers --check gemini-correctness --json`
- **THEN** The command still exits 0 (a failing provider is a result, not a crash); the gemini row is not ok and its state names the fake's 'rate limited' reason; the codex fake receives no call
<!-- proven by test CP1-T07 in tests/test_quota_check.py, commit 69e7743 -->

#### Scenario: The docs tell agents that a reviewer name can be checked
- **GIVEN** The repo's skills/ratchet-orchestrator/SKILL.md and references/schemas.md
- **WHEN** The test reads both files
- **THEN** Each one that describes `providers --check` also says it accepts a reviewer name such as codex-correctness (not only a provider id or 'all'), and says an unknown name lists the known provider ids
<!-- proven by test CP1-T08 in tests/test_quota_check.py, commit 69e7743 -->

### Requirement: One shared provider health record
The system SHALL do this: Every lane and project on the machine sees the same provider test results and benches, so one lane's discovery that a provider is down or back reaches all of them.

- When one project's test request finds a provider failing, a second project on the same machine skips that provider without testing it again
- When one lane tests a provider and it works, other lanes do not send their own test request for it within the usual re-test window
- Two lanes recording results at the same moment both end up saved, and neither file is left damaged
- Resetting a provider's reviewer (reviewer-status --reset) clears its shared bench so it is tried again everywhere
- The README and schemas.md say where this shared record lives and what it holds

#### Scenario: A provider one project found failing is skipped by another project without testing it again
- **GIVEN** Lane A and lane B share one machine; fakeA is signed out (fails), fakeB works; both lanes list fakeA and fakeB as reviewers
- **WHEN** Lane A opens a review round, then lane B opens its own round
- **THEN** The call log shows exactly one probe of fakeA in total (lane A's); lane B's round has no fakeA reviewer, still has fakeB, and reviewer-status in lane B lists fakeA as failed recently
<!-- proven by test CP2-T01 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: A provider one lane found working is not tested again by another lane in the usual window
- **GIVEN** Same two lanes; fakeB works
- **WHEN** Lane A opens a round (testing fakeB), then lane B opens a round straight after
- **THEN** The call log shows exactly one probe of fakeB across both lanes, and fakeB is a reviewer in both rounds
<!-- proven by test CP2-T02 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: Lanes recording results at the same moment all end up saved and the record is not damaged
- **GIVEN** Lane A and lane B each have eight fake providers registered; four of them fail, four work
- **WHEN** Eight 'providers --check <id>' processes are started at once, four from lane A and four from lane B, one per provider, and all are waited for
- **THEN** All eight exit 0; the shared record file in the per-user folder parses as JSON and names all eight providers; no lock file is left behind; then lane C opens a round listing all eight and the call log gains zero new probe lines (every result was kept), with the four failing providers absent from its round
<!-- proven by test CP2-T03 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: Resetting a provider's reviewer clears its shared bench so it is tried again everywhere
- **GIVEN** Lane A found fakeA failing, so lane B skips it; then fakeA is fixed (its fail switch removed)
- **WHEN** Lane A runs reviewer-status --reset fakeA-standards, then lane B opens a round
- **THEN** reviewer-status in lane B no longer lists fakeA as failed; lane B's round tests fakeA (the call log gains one fakeA probe, two in total) and fakeA is a reviewer in that round
<!-- proven by test CP2-T04 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: Resetting one provider leaves another provider's bench in place
- **GIVEN** fakeA and fakeC both failed and are benched machine-wide; fakeB works
- **WHEN** Lane A runs reviewer-status --reset fakeA-standards and lane B then opens a round
- **THEN** fakeA is tried again (a new probe) but fakeC is still skipped with no new fakeC probe, and the call log shows fakeC probed once in total
<!-- proven by test CP2-T05 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: An old project-local test result does not stand in for the shared record
- **GIVEN** A project's own .ratchet/provider-status.json says fakeD answered a test request one minute ago; the shared record has nothing for fakeD
- **WHEN** That project opens a round that uses fakeD
- **THEN** fakeD is tested once (one probe line) and its result lands in the shared record in the per-user folder, not in the project's old file
<!-- proven by test CP2-T06 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: A shared bench ends on its own and the provider is tried again
- **GIVEN** unavailable_ttl_hours is set so small the bench lasts about two seconds; lane A found fakeA failing; fakeA has since been fixed
- **WHEN** Lane B opens a round at once (skips fakeA), waits past the bench, then opens another round
- **THEN** The first round has no fakeA and no new probe; the second round tests fakeA again (a second probe) and uses it
<!-- proven by test CP2-T07 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: A damaged shared record is treated as untested and replaced, not a crash
- **GIVEN** The shared record file was found after one probe and then overwritten with text that is not JSON
- **WHEN** Lane B opens a round that uses fakeB
- **THEN** The command exits 0, stderr says the record was unreadable, fakeB is probed again, and the file now parses as JSON with fakeB in it
<!-- proven by test CP2-T08 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: How long a good result counts is a time window, not forever
- **GIVEN** Lane A tested fakeB and it worked; lane B has probe_ttl_minutes set to 0
- **WHEN** Lane B opens a round that uses fakeB
- **THEN** The call log gains a second fakeB probe (a fresh result is only trusted inside the window), and with the default window a later round from lane C adds none
<!-- proven by test CP2-T09 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: A reviewer-level mark stays in its own project
- **GIVEN** Lane A runs reviewer-status --mark fakeB-standards --reason 'rate limited here'
- **WHEN** Lane B runs reviewer-status and opens a round
- **THEN** Lane A lists fakeB-standards as unavailable; lane B still lists it as available and uses it, so only provider test results and benches are shared, not hand marks
<!-- proven by test CP2-T10 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: Two lanes testing the very same provider at the same moment leave one clean entry
- **GIVEN** Lane A and lane B both register fakeE, which works
- **WHEN** Four 'providers --check fakeE' processes are started at once, two from each lane
- **THEN** All exit 0, the shared record parses as JSON with a single fakeE entry marked working, and lane C's round adds no probe for fakeE
<!-- proven by test CP2-T11 in tests/test_quota_shared.py, commit 7dd48db -->

#### Scenario: The README and schemas.md say where the shared record lives and what it holds
- **GIVEN** The record's file name is found by looking in the per-user folder after a probe, as in T06
- **WHEN** README.md and skills/ratchet-orchestrator/references/schemas.md are read
- **THEN** Both name that file, say it is per user and shared by every project and lane, and say it holds provider test results and benches; the schemas.md section also says the old per-project provider-status.json is ignored
<!-- proven by test CP2-T12 in tests/test_quota_shared.py, commit 7dd48db -->

### Requirement: Out of credits waits for the real reset
The system SHALL do this: When a provider says it is out of credits, Ratchet benches it until the reset time the provider gave (or re-tries it after about 20 minutes when it gave none), and a passing tiny test request no longer clears that bench.

- A review that fails with Codex's usage-limit message naming a reset time is benched until that time, not for hours longer
- A quota failure that names no reset time is tried again after about 20 minutes
- While a provider is benched for quota, a passing tiny test request does not put it back in use; the bench ends at its time or when a real review succeeds
- A plain failure that is not about quota (for example not signed in) keeps today's handling
- The README, SKILL.md and schemas.md describe quota benches and reset times

#### Scenario: A Codex usage-limit failure waits for the reset time Codex gave, in the right time zone
- **GIVEN** The codex provider is in the open round. Its fake CLI prints the usage-limit text 'You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at <date>.' where <date> is 90 minutes from now, as 'Oct 14, 2026 2:04 PM' style wall-clock text in the engine's zone. Run once with TZ IST-5:30 and once (separate project) with TZ PST8 and the day written with an ordinal ('Oct 14th, 2026 2:04 PM')
- **WHEN** The agent runs the codex reviewer through external_review.py and then lists reviewer-status
- **THEN** external_review exits 3 and its message names the reset time (not 6 hours later); reviewer-status shows the codex reviewer as failed recently with a 'retry after' time within 3 minutes of now plus 90 minutes (UTC), in BOTH zones; the retry time is clearly not now plus 6 hours and not the zone-misread time
<!-- proven by test CP3-T01 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: A reset time that is only a clock time, like 'try again at 12:41 PM', means later today
- **GIVEN** The engine's zone is the computed midday zone. The fake codex prints the usage-limit text ending 'or try again at 12:41 PM.' written for now plus 40 minutes (local clock text with no date, as Codex prints a same-day reset)
- **WHEN** The codex review fails and the agent checks reviewer-status
- **THEN** The 'retry after' time is within 3 minutes of now plus 40 minutes, not 24 hours later and not the 6-hour default
<!-- proven by test CP3-T02 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: A reset time that has already passed is tried again soon, not benched for hours and not left expired
- **GIVEN** Two projects. In the first the fake codex says 'try again at <date>' with the full date and time 30 minutes in the PAST (the provider's clock ran ahead, or the lane was slow). In the second, the clock-only text 'try again at <time>' with a time 30 minutes earlier today (midday zone). quota_retest_minutes is left at its default of 20
- **WHEN** Each codex review fails and the agent runs reviewer-status straight away
- **THEN** In both, codex is still shown as failed recently (a past reset time never produces an already-expired bench that sends the next review straight back to it) and its 'retry after' is within 3 minutes of now plus 20 minutes; the clock-only past time is NOT read as tomorrow (no bench of about 23.5 hours)
<!-- proven by test CP3-T03 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: A Codex failure that says 'try again in 2 hours 15 minutes' waits that long
- **GIVEN** The fake codex prints 'You've hit your usage limit. Try again in 2 hours 15 minutes.' (no clock time). A second project uses 'try again in 1 day 3 hours'
- **WHEN** The codex review fails and the agent runs reviewer-status
- **THEN** The retry times are within 3 minutes of now plus 135 minutes and now plus 27 hours; neither is the 6-hour default
<!-- proven by test CP3-T04 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: An OpenCode Go limit failure waits for the 'It will reset in ...' time
- **GIVEN** The opencode-glm provider's fake CLI prints 'Rolling usage limit reached. It will reset in 3 hours 12 minutes. To continue using this model now, enable usage from your available balance - https://opencode.ai/workspace/wrk_demo/go'. Further projects use 'It will reset in 1 day 2 hours', 'It will reset in 5 minutes', and the no-time forms 'It will reset in less than a minute.' and 'It will reset in .'
- **WHEN** Each OpenCode review fails and the agent runs reviewer-status
- **THEN** The retry times are within 3 minutes of now plus 192 minutes, now plus 26 hours and now plus 5 minutes; the 'less than a minute' and empty forms are benched for the quota default of about 20 minutes (within 3 minutes) rather than the 6-hour default or no bench
<!-- proven by test CP3-T05 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: Out of credits with no reset time is tried again after about 20 minutes, or the configured number
- **GIVEN** The fake codex prints "You're out of credits. Your workspace is out of credits. Add credits to continue using Codex." (no time at all). One project keeps the default quota_retest_minutes; another sets quota_retest_minutes=45
- **WHEN** The codex review fails and the agent runs reviewer-status
- **THEN** The default project shows a retry time within 3 minutes of now plus 20 minutes; the configured project within 3 minutes of now plus 45 minutes; neither is the 6-hour default
<!-- proven by test CP3-T06 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: The limit message is found even when it is not the last line of the output or is printed on stdout
- **GIVEN** Fake codex output in which the usage-limit sentence with 'try again at <now plus 90 minutes>' is followed by two more lines ('tokens used' and a number). A second project prints the same on stdout instead of stderr
- **WHEN** The codex review fails and the agent runs reviewer-status
- **THEN** Both benches end at the named reset time (within 3 minutes of now plus 90 minutes), not at the 6-hour default
<!-- proven by test CP3-T07 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: While benched for quota, a passing test request does not put the provider back in use
- **GIVEN** Codex was benched by a failed review with a reset 3 hours away. The fake codex is then made healthy (its message file is removed) so a test request would pass
- **WHEN** The agent checks the provider (providers --check all, and then providers --check codex-correctness, which CP1 makes valid) and opens a new review round with reviewers
- **THEN** The check reports the test request passed, but reviewer-status still shows the codex reviewers as failed recently with the SAME retry time (within 1 minute of before); the new round's reviewer list contains no codex reviewer; and a failing test request found by a check puts a quota bench at the provider's own reset time (a fresh project whose providers --check meets the out-of-credits text shows the retry time at the reset moment, not 6 hours)
<!-- proven by test CP3-T08 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: A quota bench ends on its own at its time
- **GIVEN** quota_retest_minutes is set to a tiny number (0.03, about 2 seconds) and the fake codex prints the out-of-credits text, then is made healthy
- **WHEN** The codex review fails, and the agent runs reviewer-status immediately and again after about 4 seconds
- **THEN** Immediately the codex reviewer is shown as failed recently; after the wait it is shown as available again, and a new round from reviewers lists a codex reviewer
<!-- proven by test CP3-T09 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: A real review that succeeds ends the quota bench early
- **GIVEN** Codex is benched for quota with a reset 3 hours away after a failed review in the open round. The fake codex is made healthy and returns an empty-findings review
- **WHEN** The agent runs the codex reviewer through external_review.py again for the same round
- **THEN** external_review exits 0 and writes the signed review; reviewer-status then shows the codex reviewer as available (no longer failed recently)
<!-- proven by test CP3-T10 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: A failure that is not about quota keeps today's handling
- **GIVEN** Three projects. The fake codex prints 'error: not signed in. Run codex login' and exits 1; another prints 'error: service unavailable (503), try again later'; the third is a project with unavailable_ttl_hours=2 and the not-signed-in text
- **WHEN** Each codex review fails and the agent runs reviewer-status
- **THEN** The first two are benched for the usual 6 hours (retry time within 3 minutes of now plus 6 hours) and the third for 2 hours; none gets the 20-minute quota wait
<!-- proven by test CP3-T11 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: A good review that merely quotes the limit message does not bench the provider
- **GIVEN** The fake codex returns a valid review whose one finding's description says "The code shows the message You've hit your usage limit ... try again at 3:00 PM to the user"
- **WHEN** The agent runs the codex reviewer through external_review.py
- **THEN** external_review exits 0, the review is saved with that finding, and reviewer-status shows the codex reviewer as available
<!-- proven by test CP3-T12 in tests/test_quota_reset.py, commit 9ab869d -->

#### Scenario: The docs describe quota benches, reset times and the retest setting
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md after the change (also skills/ratchet-adversarial-loop/SKILL.md if it describes a reviewer being marked unavailable)
- **WHEN** The test reads those files
- **THEN** schemas.md and README.md both contain the config name quota_retest_minutes and its default of 20; README.md, SKILL.md and schemas.md each mention that a provider out of credits waits for the reset time it gave; the schemas.md text also says a passing test request does not end a quota bench
<!-- proven by test CP3-T13 in tests/test_quota_reset.py, commit 9ab869d -->

### Requirement: A failed slot goes to another provider
The system SHALL do this: When a reviewer fails, its slot is refilled by a different working provider (for example Kimi) instead of the same one or the host.

- When the GLM reviewer fails, its slot goes to the Kimi reviewer, not back to GLM and not to the host, even though both run through the same OpenCode program
- A provider that failed a review in this round is not picked again for this round, even if a test request for it passes
- When an explicit slot list's providers are all out, the slot tries other working providers before the host reviews its own work
- Only when no other-company provider works does the slot fall back to the host

#### Scenario: A GLM reviewer that fails hands its slot to Kimi, not back to GLM and not to the host
- **GIVEN** A low-risk step with one 'any-both' slot, providers opencode-glm and opencode-kimi on the same fake opencode program, both working, and a round open whose reviewer is opencode-glm-both; the fake opencode now fails real reviews for the glm model only (non-quota error text)
- **WHEN** external_review.py runs opencode-glm-both and exits 3, then `ratchet.py reviewers CP1 --json` is called again in the same round, then external_review.py runs the reviewer it now names, then `ratchet.py reviewer-status` is read
- **THEN** the second `reviewers` call names exactly opencode-kimi-both for that slot (not any opencode-glm-* id and not host-alt-both or host-both), fallback_for lists opencode-glm-both, and the Kimi review runs, exits 0 and writes its review file; reviewer-status shows every opencode-kimi-* reviewer still available while the glm reviewer that failed is shown as not available (so a stub that simply keeps the old bench-everything-on-the-same-program behavior, or skips benching, fails here)
<!-- proven by test CP4-T01 in tests/test_quota_refill.py, commit 9aea35c -->

#### Scenario: Only the failing model is sidelined; a missing program still takes down everything that runs through it
- **GIVEN** Two projects set up as in CP4-T01 plus a working fake codex. In the first, opencode-glm-both fails a real review (plain error text). In the second, the opencode program is not on PATH at all when external_review.py runs opencode-kimi-both (the engine's own PATH check ran earlier, when it was installed)
- **WHEN** In each project external_review.py runs the reviewer and exits 3, and `ratchet.py reviewer-status` is read once the program is on PATH again
- **THEN** first project: no opencode-kimi-* reviewer is marked unavailable, only the failing glm reviewer is. Second project: both opencode-glm-both and opencode-kimi-both are marked unavailable with a reason saying the program is not installed, and the next `reviewers CP1 --json` call in that round gives the slot to codex-both, so 'not installed' still applies to the whole program
<!-- proven by test CP4-T02 in tests/test_quota_refill.py, commit 9aea35c -->

#### Scenario: A normal-risk round whose standards reviewer fails refills it with Kimi and never reuses GLM for the other lens
- **GIVEN** A normal-risk step with slots any-standards and any-correctness, only opencode-glm and opencode-kimi configured, round opened (glm-standards and kimi-correctness, two different companies); kimi-correctness has already finished and written its review file; the fake opencode fails real reviews for glm
- **WHEN** external_review.py runs opencode-glm-standards and exits 3, then `reviewers CP1 --json` is called again in the same round
- **THEN** the standards slot is now opencode-kimi-standards; the correctness slot is still opencode-kimi-correctness and its already-written review file is untouched; no listed reviewer is an opencode-glm-* id (glm-correctness is not swapped in for the other slot) and none is a host-* id; the round still has two reviewers with the lenses standards and correctness
<!-- proven by test CP4-T03 in tests/test_quota_refill.py, commit 9aea35c -->

#### Scenario: A provider that failed this round stays out for this round even when its test request passes
- **GIVEN** As CP4-T01: opencode-glm fails real reviews with a plain (non-quota) error, but answers the tiny test request normally; opencode-glm-both has just failed in round 1 and the slot went to Kimi
- **WHEN** `ratchet.py providers --check all` is run (the fake glm passes its test request, which clears a normal bench), then `ratchet.py reviewers CP1 --json` is called again in the same round
- **THEN** the reviewers listed are still opencode-kimi-both and not opencode-glm-both, and the fake opencode's log shows the test request for glm was really sent (so the case proves a passing test request did not bring it back for this round; a version that only trusts the bench would pick glm first, config order, and fail)
<!-- proven by test CP4-T04 in tests/test_quota_refill.py, commit 9aea35c -->

#### Scenario: The round-only exclusion ends with the round: a recovered provider is used again in the next round
- **GIVEN** The state at the end of CP4-T04 (glm failed round 1, slot went to Kimi); the fake opencode's glm failure marker is then removed, so glm works again; the source file is edited and `ratchet.py behavior CP1` run so a new round must open
- **WHEN** `ratchet.py reviewers CP1 --json` opens round 2
- **THEN** round is 2 and the 'any-both' slot lists opencode-glm-both again (first provider in config order, now working and past its test request), proving the failed-this-round rule does not stick to the next round
<!-- proven by test CP4-T05 in tests/test_quota_refill.py, commit 9aea35c -->

#### Scenario: An explicit slot whose providers are all out tries other working providers before the host
- **GIVEN** A step whose only slot is the explicit list ['opencode-glm-both'], with opencode-glm marked unavailable (`reviewer-status --mark opencode-glm-both --reason 'out'`), opencode-kimi and codex both configured, installed and working
- **WHEN** `ratchet.py reviewers CP1 --json` opens the round
- **THEN** the slot is given to a working other-company provider with the same lens (opencode-kimi-both or codex-both), never to host-alt-both or host-both; fallback_for contains opencode-glm-both and reasons has a plain entry for it; the host is not used while a working provider exists
<!-- proven by test CP4-T06 in tests/test_quota_refill.py, commit 9aea35c -->

#### Scenario: Only when no other-company provider works does the slot fall back to the host
- **GIVEN** As CP4-T06 but opencode-glm, opencode-kimi and codex all fail (fake opencode fails both models, fake codex has a failure marker, each marked unavailable or failing its test request), and a fake claude provider is configured and answers fine (same company as the anthropic host)
- **WHEN** `ratchet.py reviewers CP1 --json` opens a round for the explicit slot ['opencode-glm-both']
- **THEN** the reviewer is host-alt-both (the host's company on a different model), not claude-both (a same-company provider is never counted as independent), not any of the three failed providers; the run does not stall and the fallback is recorded in the round's substitutions
<!-- proven by test CP4-T07 in tests/test_quota_refill.py, commit 9aea35c -->

#### Scenario: A working explicit slot keeps its written order and does not look for other providers
- **GIVEN** An explicit slot ['opencode-kimi-both', 'opencode-glm-both'] with both providers working and codex also working
- **WHEN** `ratchet.py reviewers CP1 --json` opens the round
- **THEN** the slot is opencode-kimi-both (the first one written, even though glm is first in config order), fallback_for is empty, and codex is not used; an unneeded refill must not reorder or replace a slot whose first choice works
<!-- proven by test CP4-T08 in tests/test_quota_refill.py, commit 9aea35c -->

### Requirement: Spread reviews by lens, flat-rate first
The system SHALL do this: Each round's reviewers are spread across the working providers by lens and recent use, flat-rate subscriptions first, and a per-use provider is only used after the developer's own OK.

- With Codex preferred for correctness and an OpenCode Go model preferred for standards, a normal-risk round sends correctness to Codex and standards to the OpenCode Go model
- Over several rounds with no preference set, reviews rotate across the working providers instead of all going to the first one listed
- A flat-rate provider is always chosen before a per-use one for the same slot
- A provider marked per-use without the developer's recorded OK is never chosen, even when nothing else works
- The README, SKILL.md and schemas.md describe the lens preference and the ordering

#### Scenario: Each provider reviews the lens it is preferred for
- **GIVEN** Normal-risk round. Providers listed in the order opencode-glm (flat, preferred for standards) then codex (flat, preferred for correctness), set with providers --add ... --prefer-for. Both work.
- **WHEN** The reviewers for the round are opened
- **THEN** The correctness reviewer is codex and the standards reviewer is opencode-glm, although codex is listed second and the standards slot is filled first; swapping the order in which the two providers were added gives the same two reviewers
<!-- proven by test CP5-T01 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: With no preference, rounds rotate across the working providers
- **GIVEN** Three working flat-rate providers prov-one, prov-two, prov-three (different companies), none with a lens preference, one low-risk checkpoint (one slot) in one project
- **WHEN** Three rounds are opened and recorded one after another
- **THEN** The three rounds went to three different providers (each used once) instead of all to prov-one, which is listed first; a fourth round goes back to the least recently used one
<!-- proven by test CP5-T02 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: A second project on the same machine continues the rotation
- **GIVEN** Same three providers, one shared RATCHET_HOME, project lane-a has just opened a round that went to prov-one
- **WHEN** Project lane-b (a different folder) opens its first round
- **THEN** lane-b's reviewer is not prov-one; with a separate empty RATCHET_HOME for lane-b it would be prov-one, so the spreading comes from the shared record and not from per-project state
<!-- proven by test CP5-T03 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: A flat-rate provider is chosen before a per-use one, even when per-use is listed first and the flat one was used more
- **GIVEN** A per-use provider with the developer's recorded OK (paid_ok_at) listed first and unused, and a flat-rate provider listed second that already reviewed two earlier rounds
- **WHEN** The next round opens
- **THEN** The slot goes to the flat-rate provider and the per-use provider's marker file is never written (it was not asked, and not sent a test request)
<!-- proven by test CP5-T04 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: A flat-rate provider beats a per-use one even when the per-use one is the one preferred for that lens
- **GIVEN** A per-use provider with paid_ok_at and --prefer-for correctness, and a flat-rate provider with no preference; both work; normal-risk round with a correctness slot
- **WHEN** The correctness reviewer is chosen
- **THEN** It is the flat-rate provider; the per-use one is not asked
<!-- proven by test CP5-T05 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: Order of use: flat-rate, then no cost label, then per-use with the developer's OK
- **GIVEN** Providers listed per-use-with-OK, then unlabeled, then flat (all different companies, all working), one low-risk slot
- **WHEN** A round opens; then the flat provider is marked unavailable and a new round opens; then the unlabeled one is marked unavailable too and another round opens
- **THEN** The three rounds go to flat, then unlabeled, then per-use-with-OK, in that order
<!-- proven by test CP5-T06 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: A per-use provider with no recorded OK is never used, even when nothing else works
- **GIVEN** The only provider is marked cost per-use and has no paid_ok_at; every other-company option is absent (or the flat ones are marked unavailable). Its fake CLI writes a marker if it is run at all.
- **WHEN** A normal-risk round opens, once with the default 'any' slots and once with an explicit slot list that names this provider's reviewer
- **THEN** The round still opens, its reviewers are the host's own (host-alt or host) and say so, the per-use provider's marker file does not exist (no review request and no connectivity test was sent), and nothing asks the developer for money by itself
<!-- proven by test CP5-T07 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: A per-use provider is only used once its recorded OK exists
- **GIVEN** A per-use provider that is the only other-company option; first without paid_ok_at, then the same entry with a paid_ok_at stamp (as lane E's consent leaves it)
- **WHEN** A round opens in each case
- **THEN** Without the stamp the host reviews and the provider is not contacted; with the stamp the provider reviews the slot
<!-- proven by test CP5-T08 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: An explicit slot list keeps the order it was written in
- **GIVEN** A checkpoint whose reviewers are the explicit list [prov-two-correctness, prov-one-correctness] where prov-one is flat and prov-two has no cost label, both working
- **WHEN** The round opens, and again after prov-two has several recent reviews
- **THEN** Both times the correctness slot is prov-two, the first one written; ranking and rotation do not reorder an explicit list
<!-- proven by test CP5-T09 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: When every provider in an explicit list is out, other working providers are tried before the host
- **GIVEN** Explicit slot [prov-one-correctness] with prov-one marked unavailable, and prov-two and prov-three working (prov-three flat, prov-two unlabeled)
- **WHEN** The round opens
- **THEN** The slot goes to prov-three (flat first), not to the host; and with prov-two and prov-three also unavailable, the host fills it
<!-- proven by test CP5-T10 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: A preferred provider that is out does not leave its lens to the host
- **GIVEN** codex preferred for correctness but marked unavailable; opencode-glm (flat) and opencode-kimi (flat) work; normal-risk round
- **WHEN** The round opens
- **THEN** The correctness reviewer is another working other-company provider, not a host reviewer, and the round says codex was skipped
<!-- proven by test CP5-T11 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: Each round still prefers a company that is not yet reviewing
- **GIVEN** codex preferred for both correctness and standards, plus a working flat provider from another company; normal-risk round with two slots
- **WHEN** The round opens
- **THEN** The two reviewers come from two different companies (codex on one lens, the other company's provider on the other) rather than codex twice
<!-- proven by test CP5-T12 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: providers --add accepts a lens preference and refuses an unknown lens
- **GIVEN** A working fake provider
- **WHEN** providers --add is run with --prefer-for correctness, then with --prefer-for speed
- **THEN** The first adds the provider and providers --json shows the preference; the second exits non-zero with a message naming the allowed lenses and adds nothing
<!-- proven by test CP5-T13 in tests/test_quota_spread.py, commit e7da508 -->

#### Scenario: The docs describe the lens preference and the ordering
- **GIVEN** README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** They are searched for the --prefer-for option and the ordering rule
- **THEN** Each file mentions --prefer-for (or the preference field) and says flat-rate comes before per-use and that a per-use provider needs the developer's OK
<!-- proven by test CP5-T14 in tests/test_quota_spread.py, commit e7da508 -->

### Requirement: Share a returning provider across lanes
The system SHALL do this: When several lanes want the same provider at once, each provider takes only a few new reviews per short window across the whole machine, and the rest go to other working providers.

- Four lanes opening review rounds at the same moment never start more than the allowed number of reviews on one provider within the window
- Lanes over the cap get another working provider for that slot, flat-rate first, with a plain note saying why
- When the window passes, the provider takes new reviews again
- A round that is replaced before anyone reviewed it gives its reservations back
- The README and schemas.md describe the per-provider share and its config setting

#### Scenario: Four lanes opening a round at the same moment never start more than 3 reviews on one provider
- **GIVEN** Four low-risk lanes (four projects, one shared RATCHET_HOME) whose only other-company provider is codex, with provider_share unset (default 3 per 15 minutes) and the codex test request answered
- **WHEN** All four lanes run `reviewers CP1 --json` as parallel processes started together
- **THEN** All four commands exit 0 with valid JSON; exactly 3 lanes have codex-both as their reviewer and exactly 1 lane has host-alt-both; never 4 codex
<!-- proven by test CP6-T01 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: Six simultaneous lanes spread over two providers, none over its share, none left unused
- **GIVEN** Six low-risk lanes sharing one RATCHET_HOME, providers codex and opencode-glm (flat) both working, each lane's provider_share set to max 2 per 15 minutes
- **WHEN** All six lanes open a round at the same moment as parallel processes
- **THEN** All six exit 0; codex-both appears in at most 2 lanes and opencode-glm-both in at most 2; exactly 4 lanes use an other-company provider (the capacity is fully used, nothing is wasted by a race) and exactly 2 lanes use host-alt-both
<!-- proven by test CP6-T02 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: A lane over the cap goes to the next working provider, flat-rate first, and a per-use provider without consent is never used
- **GIVEN** Five low-risk lanes opening rounds one after another on a shared RATCHET_HOME, each with provider_share max 1 per 15 minutes and four providers: opencode-glm (flat), codex (no cost label), opencode-kimi (per-use with the developer's recorded OK), opencode-qwen (per-use, no recorded OK)
- **WHEN** Lane 1, then lane 2, then lane 3, then lane 4, then lane 5 each run `reviewers CP1 --json`
- **THEN** Lane 1 gets opencode-glm-both (flat first), lane 2 gets codex-both (the cap is reached on glm, so the next working provider, unlabeled before per-use), lane 3 gets opencode-kimi-both (per-use only after the others are full, and it has consent), lane 4 and lane 5 get host-alt-both; opencode-qwen is never chosen by any lane
<!-- proven by test CP6-T03 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: A lane that was sent elsewhere is told why in plain words
- **GIVEN** Two low-risk lanes on a shared RATCHET_HOME, only provider codex, provider_share max 1 per 15 minutes
- **WHEN** Lane 1 opens its round (codex-both), then lane 2 opens its round
- **THEN** Lane 2's reviewer is host-alt-both and its round records that codex-both was skipped, with a reason that names the cap (1) and the window (15 minutes) in plain words; the plain-text `reviewers CP1` output for lane 2 also shows the line '(instead of codex-both: ...)' with that reason; lane 1 has no such note
<!-- proven by test CP6-T04 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: When the window passes, the provider takes new reviews again
- **GIVEN** Three low-risk lanes on a shared RATCHET_HOME, only provider codex; lanes 1 and 2 have provider_share max 1 per 15 minutes; lane 3's own project has max 1 per 0.001 minutes (so lane 1's earlier reservation is already outside lane 3's window)
- **WHEN** Lane 1 opens a round, then lane 2 opens a round, then lane 3 opens a round
- **THEN** Lane 1 gets codex-both, lane 2 (reservation still inside its window) gets host-alt-both, lane 3 gets codex-both again; the engine counts only reservations newer than the asking lane's window
<!-- proven by test CP6-T05 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: A round replaced before anyone reviewed it gives its reservations back
- **GIVEN** Lane A and lane B on a shared RATCHET_HOME, only provider codex, provider_share max 1 per 15 minutes; lane A has opened round 1 (codex-both) and no review file has been written in it
- **WHEN** Lane A's code changes and lane A runs `reviewers CP1 --json` again (the unreviewed round is replaced), then lane B opens its round
- **THEN** Lane A's replacement round gets codex-both again (its own earlier reservation was given back, not counted against it) and lane B gets host-alt-both (only one reservation exists on codex, not two); also the run's round count is not charged for the replacement
<!-- proven by test CP6-T06 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: A round that was already reviewed keeps its reservation when it is replaced
- **GIVEN** Lane A on a shared RATCHET_HOME, only provider codex, provider_share max 1 per 15 minutes; lane A opened round 1 (codex-both) and a review file was written in that round's folder
- **WHEN** Lane A's code changes and it runs `reviewers CP1 --json` for round 2
- **THEN** Round 2 gets host-alt-both: the reviewed round's reservation is not handed back, so replacing a reviewed round cannot be used to dodge the share
<!-- proven by test CP6-T07 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: Showing the same open round again does not reserve again
- **GIVEN** Lane A and lane B on a shared RATCHET_HOME, only provider codex, provider_share max 2 per 15 minutes; lane A has opened its round (codex-both)
- **WHEN** Lane A runs `reviewers CP1 --json` three more times with no code change (same round), then lane B opens its round, then a lane C opens its round
- **THEN** Lane A still shows codex-both every time; lane B gets codex-both (second reservation) and lane C gets host-alt-both; the repeats never used up the share
<!-- proven by test CP6-T08 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: Every review slot of a round counts as one start on its provider
- **GIVEN** Two normal-risk lanes (two slots each: standards and correctness) on a shared RATCHET_HOME, only provider codex, default share of 3 per 15 minutes
- **WHEN** Lane 1 opens its round, then lane 2 opens its round
- **THEN** Lane 1's round has codex-standards and codex-correctness (2 starts); lane 2's round has exactly one codex reviewer and one host-alt reviewer (only 1 start was left); across the two lanes codex has 3 starts, not 4
<!-- proven by test CP6-T09 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: An explicit reviewer list over its cap tries another working provider before the host
- **GIVEN** Lane A and lane B on a shared RATCHET_HOME, providers codex and opencode-glm (flat) working, provider_share max 1 per 15 minutes, and lane B's checkpoint names its reviewer slot explicitly as ['codex-both']; lane A (any-both) has already taken codex-both
- **WHEN** Lane B runs `reviewers CP1 --json`
- **THEN** Lane B's reviewer is opencode-glm-both, not host-alt-both: the slot still goes to another working other-company provider, and it records that codex-both was skipped for being over its share
<!-- proven by test CP6-T10 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: A damaged shared record does not stall a lane or lose later counting
- **GIVEN** Lane 1 has opened a round on a shared RATCHET_HOME (provider codex, share max 1 per 15 minutes) so the shared record exists; the test then overwrites every file under that RATCHET_HOME whose text mentions 'codex' (other than Ratchet's director key and hook records) with the text '{not json'
- **WHEN** Lane 2 runs `reviewers CP1 --json`, then lane 3 does
- **THEN** Lane 2 exits 0 with a valid round (codex-both or host-alt-both, never a crash or a traceback) and says plainly on stderr that the shared record was unreadable and rebuilt; lane 3 exits 0 and the share counting works again (lane 3 does not get codex-both if lane 2 did, with max 1)
<!-- proven by test CP6-T11 in tests/test_quota_share.py, commit 43ac108 -->

#### Scenario: The README and schemas.md describe the per-provider share and its setting
- **GIVEN** The repository's README.md and skills/ratchet-orchestrator/references/schemas.md
- **WHEN** The tests read both files
- **THEN** Each mentions provider_share, its default of 3 per 15 minutes, and says it applies across all lanes on the machine and that a round replaced before any review gives its reservation back
<!-- proven by test CP6-T12 in tests/test_quota_share.py, commit 43ac108 -->

### Requirement: Big reviews work on OpenCode providers
The system SHALL do this: An OpenCode reviewer such as GLM or Kimi gets the whole review prompt however large it is, an empty or cut-short answer never counts as a pass, and a prompt that is too big never benches a provider.

- A review prompt of about 200 KB reaches an OpenCode reviewer whole: a fake opencode that refuses any single argument over 128 KB and reads every word after -f as a file name receives the full prompt, and the review is recorded
- When an OpenCode reviewer exits 0 with no answer text, or stops because it ran out of output room ('length'), the review is never recorded as an approval or as a pass with no findings; that slot goes to another provider for this round
- A prompt too large for a provider's command line never benches that provider or marks it unhealthy for any lane; only that slot moves on for this round, and the provider's next review is sent as normal
- OpenCode review and test requests ask for capped thinking (--variant low unless the provider sets another value or none), and checking an OpenCode provider still passes with the new call
- The README, SKILL.md and schemas.md say how OpenCode receives big prompts, the empty or 'length' answer rule, the size rule and the thinking cap

#### Scenario: A review of about 200 KB reaches an OpenCode reviewer whole
- **GIVEN** A low-risk throwaway project whose one reviewer slot is opencode-glm (flat, model opencode-go/glm-5.3, preset opencode) and whose diff holds about 190 KB of new text, so the review prompt is over 190,000 bytes; the fake opencode refuses any single argument over 128 KB, treats every word after -f as a file name, merges stdin after the message like the real one, and answers with a clean JSON event stream
- **WHEN** The agent opens the round (`reviewers CP1 --json`) and runs external_review.py for opencode-glm-both; separately, `external_review.py --dry-run` is run on the same round to learn the exact prompt size
- **THEN** external_review exits 0 and the review file is written, signed, with verdict APPROVE and no findings; the fake's log shows the bytes it read from stdin equal the dry-run prompt's bytes and are over 190,000, its last stdin text ends with the prompt's closing instruction, the longest single argument it was given is under 1,000 bytes, no argument is -f, and --format json is among its arguments; the shared provider-health.json holds no bench for the provider and exactly one review start for it
<!-- proven by test CP7-T01 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: A finding the OpenCode reviewer writes in its answer text is recorded
- **GIVEN** The same project and fake; the fake's event stream is step_start, one text event whose text is a findings reply with one major finding (id F1, file src/f.js, line 3, a user_impact sentence), then step_finish with reason stop; exit 0
- **WHEN** The agent runs external_review.py for opencode-glm-both
- **THEN** The review file records exactly that finding F1 with its file and line, the verdict is CHANGES_REQUESTED, and it is signed; the finding text is the reply's own, not any raw event line
<!-- proven by test CP7-T02 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: Findings shown in a reasoning event or echoed input never count as the review
- **GIVEN** The same project and fake; first scenario: the stream holds a reasoning event whose text is the prompt's own example reply (valid JSON with a findings list holding a blocker), a tool_use-style event that echoes input holding a second findings object, and then a text event whose text is a clean reply with an empty findings list; step_finish reason stop; exit 0. Second scenario: the stream holds a reasoning event with a findings object and a text event with prose only (no JSON)
- **WHEN** The agent runs external_review.py for opencode-glm-both in each scenario
- **THEN** First scenario: exit 0, the review has zero findings and verdict APPROVE, and neither the reasoning blocker nor the echoed finding appears anywhere in the review file. Second scenario: the reply has no findings JSON in any text event, so the review is a failed attempt (exit 3, no review file at the output path), not a review built from the reasoning event
<!-- proven by test CP7-T03 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: An empty answer with exit 0 is a failed attempt for this round only, never an approval
- **GIVEN** The same project and fake; the fake's stream is step_start, a reasoning event holding a valid findings JSON, and step_finish with reason stop, but no text event with any text; exit code 0; one other flat OpenCode Go provider (opencode-kimi) is configured and working
- **WHEN** The agent runs external_review.py for opencode-glm-both
- **THEN** external_review exits 3 with a plain message saying the model gave no answer text; no review file exists at the output path and the round's folder holds no APPROVE review; the fake received exactly one review request (no retry); a <reviewer>.cutoff.json marker with a reason exists in the round's folder; provider-health.json holds no bench for opencode-go/glm-5.3 and unavailable-reviewers.json holds no entry for it; the review start the round recorded is still in provider-health.json; `reviewer-status` still lists opencode-glm-both as available
<!-- proven by test CP7-T04 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: After an empty answer the slot goes to a different working provider and the next round uses the first one again
- **GIVEN** The state left by CP7-T04 (opencode-glm answered empty, opencode-kimi working, both flat), with the host as anthropic
- **WHEN** The agent runs `reviewers CP1 --json` again in the same round, runs the new reviewer, then changes the code a little and opens the next round
- **THEN** The same round now has opencode-kimi-both in the slot (fallback_for lists opencode-glm-both), not a host reviewer; its review succeeds; with kimi also failing with an empty answer the slot goes to host-alt-both and never to a repeat of glm in that round; in the next round opencode-glm-both is picked again and its normal answer is recorded as a pass
<!-- proven by test CP7-T05 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: A reply that stopped because it ran out of output room ('length') is a failed attempt, even when what it wrote looks complete
- **GIVEN** The same project and fake. Scenario A: a text event holding the start of a findings reply cut off mid-object, then step_finish with reason length, exit 0. Scenario B: a text event holding a complete, valid reply with an empty findings list, then step_finish with reason length, exit 0. Scenario C (guard): the same complete reply with reason stop
- **WHEN** The agent runs external_review.py for opencode-glm-both in each scenario (a fresh round each)
- **THEN** A and B: exit 3, no review file, no retry (exactly one review request reaches the fake), the round-only cutoff marker names the output limit, no bench or unavailable mark for the provider anywhere, its start is kept; B in particular never becomes a clean pass. C: exit 0, review recorded as APPROVE
<!-- proven by test CP7-T06 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: An error event that names a usage limit still benches the provider; the same words in reasoning do not
- **GIVEN** Two lanes (two projects, one shared RATCHET_HOME), opencode-glm in both. Scenario A: the fake's stream holds an error event saying 'Rolling usage limit reached. It will reset in 3 hours 12 minutes.', exit non-zero. Scenario B (fresh shared home): a reasoning event quoting 'usage limit reached' while the text event is a normal clean answer, exit 0
- **WHEN** In scenario A the agent runs external_review.py in lane 1, then lane 2 opens a round; in scenario B the same review runs in lane 1
- **THEN** A: exit 3, provider-health.json benches opencode:opencode-go/glm-5.3 until about 3 hours 12 minutes ahead (within a minute either way), and lane 2's round does not offer opencode-glm-both. B: exit 0, review recorded APPROVE, and no bench is written
<!-- proven by test CP7-T07 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: A prompt too large for a CLI that takes it as an argument never benches that provider in any lane
- **GIVEN** For each of four argument-delivery providers (cursor, copilot, omp, pi; fake programs cursor-agent, copilot, omp and pi on the private PATH, each configured so it counts as other-company), a low-risk project whose prompt is over 190 KB and whose only other-company provider is that one plus a second working provider (codex fake, stdin) so a refill is possible; one shared RATCHET_HOME per provider
- **WHEN** The agent opens the round and runs external_review.py for that provider's reviewer
- **THEN** For each of the four: exit 3 with a plain 'too large for a command-line argument' reason and no traceback; the fake program is never started; provider-health.json and unavailable-reviewers.json hold no bench, no failed mark and no failed test result for it; the review start is given back (zero starts for it); the round's folder holds a cutoff marker for that reviewer only; `reviewer-status` lists the provider's reviewers as available; `reviewers CP1 --json` in the same round moves that slot to the other working provider, never to a repeat
<!-- proven by test CP7-T08 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: Another lane and the provider's next review are unaffected by a size failure
- **GIVEN** The omp scenario of CP7-T08 after the failure, plus a second lane (second project, same RATCHET_HOME) whose prompt is small
- **WHEN** The second lane opens a round and runs its omp reviewer; then the first lane removes the large file, changes the code, opens its next round and runs omp-both again
- **THEN** The second lane is offered omp-both (not skipped for benching) and its fake omp receives the small prompt and returns a clean answer; the first lane's next round also picks omp-both and records its review; neither run waits for any retest time
<!-- proven by test CP7-T09 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: OpenCode review and test requests ask for low thinking by default
- **GIVEN** A project with opencode-glm added by `providers --add` with no variant, and the logging fake opencode
- **WHEN** The agent runs `providers --check opencode-glm --json`, then opens a round and runs the reviewer
- **THEN** In the fake's logged arguments for both the test request and the review, `--variant` is followed by `low` exactly once, `--format json` is present, `-m opencode-go/glm-5.3` is present, the message argument is short, and the prompt text is on stdin only
<!-- proven by test CP7-T10 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: A provider can set its own thinking level or turn the cap off
- **GIVEN** Three providers added with `providers --add`: opencode-hi with `--variant high`, opencode-off with `--variant none`, and opencode-def with no variant, all preset opencode
- **WHEN** The agent runs a test request (`providers --check`) and a review for each
- **THEN** The fake sees `--variant high` for opencode-hi, no --variant argument at all for opencode-off (test request and review), and `--variant low` for opencode-def; `providers --json` still lists all three; re-adding opencode-hi without --variant keeps high
<!-- proven by test CP7-T11 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: The provider test request still passes with the new call, and an echoed request alone is not a pass
- **GIVEN** The logging fake opencode. Scenario A: its test-request answer is a text event reading RATCHET-PROBE-OK inside a normal event stream. Scenario B: the stream echoes the request (which contains the token) in a non-text event but its text event says it cannot comply
- **WHEN** The agent runs `providers --check opencode-glm --json` in each scenario
- **THEN** A: the row is ok with state working and exactly one request reaches the fake. B: the row is not ok (a failed check), because the token is not in the answer text
<!-- proven by test CP7-T12 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: A CLI that ignores --format json and prints plain text is read as before
- **GIVEN** The same project; the fake ignores --format json and prints a plain-text findings reply with one minor finding (as the fake CLIs in earlier suites do), exit 0
- **WHEN** The agent runs external_review.py for opencode-glm-both
- **THEN** The finding is recorded and the verdict is CHANGES_REQUESTED, exactly as before this step; a plain-text reply with an empty findings list is recorded as APPROVE
<!-- proven by test CP7-T13 in tests/test_opencode_big.py, commit 9b9cc4c -->

#### Scenario: The docs and the test list describe the new OpenCode behavior
- **GIVEN** The repository's README.md, both SKILL.md files (ratchet-orchestrator and ratchet-adversarial-loop), schemas.md and the external_review.py module docstring
- **WHEN** The text of each is read
- **THEN** README's test list contains `python3 tests/test_opencode_big.py`; README, the two SKILL.md files and schemas.md each say that OpenCode gets the review prompt on stdin (not as one argument), that an answer with no text or a 'length' finish counts as a failed attempt for that round and never an approval, that a prompt too large for a command line moves only that slot and never benches a provider, and that --variant low is the default thinking cap (settable per provider, 'none' turns it off); schemas.md lists the provider field variant; the external_review.py docstring no longer names opencode among the CLIs that take the prompt only as an argument
<!-- proven by test CP7-T14 in tests/test_opencode_big.py, commit 9b9cc4c -->
