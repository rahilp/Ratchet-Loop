---
name: ratchet-adversarial-loop
description: Runs the Ratchet adversarial code-review gate — independent reviewers (another company's model through any registered agent CLI whenever one is available and working, otherwise the host's company on a different model, otherwise the host's own sub-agents) assume the checkpoint's code is wrong and hunt for violations of the written standards and for correctness/security bugs; a separate fixer fixes every blocking finding; tests (and UI review, if visible code changed) re-run; every reviewer re-reviews the new code; repeat until no blocking findings remain. Risk decides how many reviewers look and, on low-risk steps, that only blocker and major findings block. Use at Gate 3 of the Ratchet loop, or whenever the user wants a hostile, multi-vendor review-and-fix loop on a change rather than a one-shot code review.
---

# Ratchet Adversarial Loop (Gate 3)

At this point the feature works and looks right. This gate checks whether the code underneath is any good. It only works if there's a **written standard** to enforce. A reviewer without a standard just gives opinions; a reviewer with one gives findings.

```bash
RUN=.ratchet/runs/<slug>
ADV="python3 <this skill's base directory>/scripts/external_review.py"
```

## Standard to enforce
Sources come from config `standards`, in order: `.ratchet/standards.md` (drafted by ratchet-app-scout, then edited by the human), `CONTRIBUTING.md`, `ARCHITECTURE.md`, lint configs, `DESIGN.md` §8 for UI code, and `.ratchet/learnings.md`. If `.ratchet/standards.md` is missing, spawn `ratchet-app-scout` in standards-only mode first.

## Who reviews: a different company first, at every risk level
Each checkpoint has a `risk` (`low` / `normal` / `high`, set by the planner). Risk only decides **how many** reviewers look: config `review_depth` gives each risk a list of review slots (defaults: low 1, normal 2, high 4). **Who** fills a slot is the same at every risk level:

1. **Another company's model**, if one is available and capable: any agent CLI registered under config `providers` (Codex, Gemini, Claude, Copilot, Cursor, OpenCode or your own command). A company not yet reviewing this round is preferred.
2. Otherwise **your own company on a different model** (`host-alt-*`): a `ratchet-adversary` sub-agent you spawn with a model other than yours (config `host_models.alternates`, e.g. Sonnet when you are Opus).
3. Otherwise **you, on your own model** (`host-*`).

**The host** is whichever model runs this loop (auto-detected, or `host_provider` in config). A provider counts as available only if its tool is installed, its company is known and differs from the host's, it is allowed for this risk (`for_risk`), and it answered a small **test request** recently: `ratchet.py reviewers` sends one before relying on a provider it hasn't checked in the last `probe_ttl_minutes`, so a tool that is signed out or out of quota hands its slot on at once. A provider that fails mid-review is marked unavailable for `unavailable_ttl_hours` (default 6) together with every reviewer using the same tool. **A missing or failing provider never stalls the loop.** (For a high-risk checkpoint with nothing but the host available, the count of distinct reviewers can be smaller than the slots.) `ratchet.py providers` shows the picture, `providers --discover` finds and tests installed tools, `providers --add` registers one (with `--model` for tools that run several companies' models). Every substitution is logged in the run history, and the plan, the viewer and `reviewers` name who reviews each round.

`$RATCHET reviewers CPn` resolves the slots for this round. It prints who must review, which alternative each one replaced and why, the model for each `host-*` sub-agent, the round's review rules, the fingerprint, each reviewer's output path, and the command or sub-agent to launch. **Always start the round with it, and re-run it if a reviewer exits with code 3** (unavailable): the slot will have moved to the next alternative.

## What blocks
- **Severity bar by risk** (config `blocking_severities`). On a **low-risk** checkpoint only blocker and major findings block; minor ones are recorded for later (`ratchet.py deferred`) and shown to the user at the final review. Normal and high risk: every finding blocks.
- **Later rounds look at what changed** (config `scope_later_rounds`). From round 2 on, a minor finding only blocks if it is in code changed since the previous round; blocker and major findings block anywhere. This keeps each round from turning into a fresh review of everything.
- Severities other than the known minor ones (minor, nit, nitpick, trivial, low, info, suggestion, style) always block, so a made-up severity can't slip past. A finding with no file, or a file Ratchet can't place, counts as in scope.
- A file that had a blocking finding last round stays in scope, so a finding the fixer didn't fix is judged in full again.
- Adding or changing a provider that runs its own command (not a preset), or changing `host_models` / `role_models`, after plan approval needs the user's yes (`ask approve-changes`), like any other rule.
- Pass the round's review rules (printed by `reviewers`) to every `host-*` reviewer you spawn; `external_review.py` adds them to other providers' prompts itself.

## The loop (round k = 1, 2, …)

1. **Plan the round.** `$RATCHET reviewers CPn`. It stamps the fingerprint and lists the reviewers. A review of older code is rejected later.
2. **Launch every listed reviewer in parallel.** Each one starts with a fresh context.
   - `kind: agent` (`host-*`) → spawn a `ratchet-adversary` sub-agent with the model `reviewers` names (for `host-alt-*`: one of the alternates that is NOT your own model), its `lens`, its **reviewer id**, the fingerprint, the round's review rules and its output path. Don't give it the implementer's reasoning, the test results, or earlier rounds' findings.
   - `kind: command` → run `$ADV --reviewer <id> --cp CPn --out <path>` (add `--fixes <fixes.md>` from round 2 on). The script gives the other provider's CLI the adversary instructions, the standards, the learnings and the full diff inline, runs it read-only, and writes normalized findings JSON stamped with the fingerprint. **Exit code 3** means that provider is unavailable and has been marked so. Re-run `$RATCHET reviewers CPn` and launch whichever reviewer now fills that slot (possibly a `host-*` sub-agent).
   - From round 2 on, every reviewer gets `fixes.md` so it can withdraw a finding the fixer disproved with evidence. Otherwise each round is a fresh, hostile look.
3. **Record.** `$RATCHET adv-record CPn --reviews <all output files>`
   - It refuses if any required reviewer is missing, or if any review is of a different fingerprint.
   - It refuses any review that is not directly inside this round's own folder, `$RUN/reviews/CPn/adv-round-NN/` (`reviewers` prints it). Links and `..` paths are resolved first, and a file from an earlier round's folder doesn't count. If a reviewer wrote somewhere else, launch it again with its output in that folder.
   - No required reviewer has a blocking finding (see "What blocks") → **approved**, and `next` says `complete`. Minor findings that didn't block are kept in `deferred-findings.json` for the final review.
   - Otherwise ratchet.py writes `evidence/CPn/adversarial/round-NN/open-findings.json`, with each finding tagged by reviewer. (NN = the current round, zero-padded: 01, 02, ...)
4. **Fix.** Spawn `ratchet-fixer` with that file. It fixes **every** finding, from every vendor, or writes an evidence-backed rebuttal in `$RUN/reviews/CPn/adv-round-NN/fixes.md`, touching nothing unrelated.
5. **Re-prove.** `$RATCHET behavior CPn`. If it fails, send the log to `ratchet-fixer`, since the fix broke it. If the fixer touched UI files, re-run the `ratchet-ui-compare` gate.
6. Go to step 1 with the next round (NN+1). **All** reviewers review again, including the ones that approved last round, because the code changed.

The loop ends only when **all** required reviewers approve the **same, current** code. `ratchet.py complete` re-checks this.

## Quota guards
- `max_adversarial_rounds` (per checkpoint) and `max_adversarial_rounds_run` (whole run) both **block and escalate** to the human or director. They never lower the bar. Blocking is the right outcome when review quota is scarce: the director decides whether to raise the cap, lower a checkpoint's risk, or split the checkpoint.
- Don't spend high-risk depth on skeletons: a checkpoint's risk is set in the plan, and the human sees it at plan approval.

## Guardrails
- Findings are never waived by the orchestrator. The human or director decides at a block, and that decision usually becomes a line in `standards.md` or `learnings.md`.
- When the same `kind` of finding turns up in a second place (another file or checkpoint), `adv-record` prints `REPEAT:`. Offer the user a guardrail straight away (see "When the same mistake repeats" in the orchestrator skill): an automatic check, built as its own step, that then runs with the tests so that mistake fails in seconds instead of costing review rounds. For a kind a check in config `checks` already covers, minor findings are noted instead of fixed; blocker and major ones still block, and also mean the check let a case through: Ratchet adds a step to strengthen it automatically (`STRENGTHEN:`), and you tell the user in one line.
- Adversaries don't fix, and fixers don't review. Keeping the roles separate is what makes the loop adversarial.
- Each reviewer writes its own review file. Never write, copy or edit one yourself, even to transcribe what a reviewer said. Where the host names the acting sub-agent on each tool call (Claude Code does; Codex, Gemini CLI and Copilot CLI currently don't, so there it is on you), the guard blocks anyone but a `ratchet-adversary` sub-agent from writing `adv-round-NN/*.json`. If a reviewer didn't write its file, launch it again.
