---
name: ratchet-adversarial-loop
description: Runs the Ratchet adversarial code-review gate — independent reviewers (from a provider different from the model hosting the loop — Claude, Codex, Gemini, Copilot or any configured CLI — whenever one is available, otherwise the host's own sub-agents) assume the checkpoint's code is wrong and hunt for violations of the written standards and for correctness/security bugs; a separate fixer fixes every finding; tests (and UI review, if visible code changed) re-run; every reviewer re-reviews the new code; repeat until all return zero findings. How many reviewers look depends on the checkpoint's risk. Use at Gate 3 of the Ratchet loop, or whenever the user wants a hostile, multi-vendor review-and-fix loop on a change rather than a one-shot code review.
---

# Ratchet Adversarial Loop (Gate 3)

At this point the feature works and looks right. This gate checks whether the code underneath is any good. It only works if there's a **written standard** to enforce. A reviewer without a standard just gives opinions; a reviewer with one gives findings.

```bash
RUN=.ratchet/runs/<slug>
ADV="python3 <this skill's base directory>/scripts/external_review.py"
```

## Standard to enforce
Sources come from config `standards`, in order: `.ratchet/standards.md` (drafted by ratchet-app-scout, then edited by the human), `CONTRIBUTING.md`, `ARCHITECTURE.md`, lint configs, `DESIGN.md` §8 for UI code, and `.ratchet/learnings.md`. If `.ratchet/standards.md` is missing, spawn `ratchet-app-scout` in standards-only mode first.

## Who reviews: risk decides the depth, availability decides the provider
Each checkpoint has a `risk` (`low` / `normal` / `high`, set by the planner). Config `review_depth` maps each risk to a list of **review slots**, and **every slot's reviewer must return zero findings**. Every finding is always fixed; risk only changes how many independent eyes look.

**The host** is whichever model is running this loop (auto-detected, or set with `host_provider` in config). `host-*` reviewers are the host's own `ratchet-adversary` sub-agents. Every other reviewer is a different provider's CLI (`claude-*`, `codex-*`, `gemini-*`, `copilot-*`, or any command you add), called through `external_review.py`.

A slot is either one reviewer id or an ordered list of alternatives. The first alternative that is **available right now** fills the slot, preferring a provider not already reviewing this round. Other providers are listed first, so a different model family reviews whenever one is installed and working. When none is, the host's own sub-agents fill the slot: every slot of only other-provider reviewers automatically falls back to a `host-*` reviewer with the same lens (then another lens, so the number of reviewers holds). **A missing or failing provider never stalls the loop.**

Defaults:
- `low` → `host-both`
- `normal` → `host-standards` + [`codex` → `gemini` → `claude` → `copilot` correctness]
- `high` → `host-standards` + `host-correctness` + [`codex` → `gemini` → `claude` → `copilot` correctness] + [`gemini` → `claude` → `codex` → `copilot` standards]

A reviewer counts as **unavailable** when it's the **same provider as the host** (the host's sub-agents already cover that model family), when its CLI isn't on PATH, or when it failed recently. `external_review.py` marks it (and every reviewer using the same CLI) unavailable for `unavailable_ttl_hours` (default 6) when the CLI errors, hits a quota or auth limit, times out, or returns nothing parseable. `ratchet.py reviewer-status` shows the current picture. `--reset <id>` retries a provider early. Only the director or a person at their own terminal can `--mark` a provider off. Every substitution is logged in the run history and the round's evidence, so you can see when a checkpoint was reviewed by the host's model only.

`$RATCHET reviewers CPn` resolves the slots for this round. It prints who must review, which alternative each one replaced and why, the current fingerprint, each reviewer's output path, and the command or sub-agent to launch. **Always start the round with it, and re-run it if a reviewer exits with code 3** (unavailable): the slot will have moved to the next alternative.

## The loop (round k = 1, 2, …)

1. **Plan the round.** `$RATCHET reviewers CPn`. It stamps the fingerprint and lists the reviewers. A review of older code is rejected later.
2. **Launch every listed reviewer in parallel.** Each one starts with a fresh context.
   - `kind: agent` (`host-*`) → spawn a `ratchet-adversary` sub-agent with its `lens`, its **reviewer id**, the fingerprint and its output path. Don't give it the implementer's reasoning, the test results, or earlier rounds' findings.
   - `kind: command` → run `$ADV --reviewer <id> --cp CPn --out <path>` (add `--fixes <fixes.md>` from round 2 on). The script gives the other provider's CLI the adversary instructions, the standards, the learnings and the full diff inline, runs it read-only, and writes normalized findings JSON stamped with the fingerprint. **Exit code 3** means that provider is unavailable and has been marked so. Re-run `$RATCHET reviewers CPn` and launch whichever reviewer now fills that slot (possibly a `host-*` sub-agent).
   - From round 2 on, every reviewer gets `fixes.md` so it can withdraw a finding the fixer disproved with evidence. Otherwise each round is a fresh, hostile look.
3. **Record.** `$RATCHET adv-record CPn --reviews <all output files>`
   - It refuses if any required reviewer is missing, or if any review is of a different fingerprint.
   - Every required reviewer has zero findings → **approved**, and `next` says `complete`.
   - Otherwise ratchet.py writes `evidence/CPn/adversarial/round-k/open-findings.json`, with each finding tagged by reviewer.
4. **Fix.** Spawn `ratchet-fixer` with that file. It fixes **every** finding, from every vendor, or writes an evidence-backed rebuttal in `$RUN/reviews/CPn/adv-round-k/fixes.md`, touching nothing unrelated.
5. **Re-prove.** `$RATCHET behavior CPn`. If it fails, send the log to `ratchet-fixer`, since the fix broke it. If the fixer touched UI files, re-run the `ratchet-ui-compare` gate.
6. Go to step 1 with k+1. **All** reviewers review again, including the ones that approved last round, because the code changed.

The loop ends only when **all** required reviewers approve the **same, current** code. `ratchet.py complete` re-checks this.

## Quota guards
- `max_adversarial_rounds` (per checkpoint) and `max_adversarial_rounds_run` (whole run) both **block and escalate** to the human or director. They never lower the bar. Blocking is the right outcome when review quota is scarce: the director decides whether to raise the cap, lower a checkpoint's risk, or split the checkpoint.
- Don't spend high-risk depth on skeletons: a checkpoint's risk is set in the plan, and the human sees it at plan approval.

## Guardrails
- Findings are never waived by the orchestrator. The human or director decides at a block, and that decision usually becomes a line in `standards.md` or `learnings.md`.
- If the same rule is violated in 2+ rounds or checkpoints, run `$RATCHET learn "<rule>" --source gate:adversarial` so implementers stop doing it.
- Adversaries don't fix, and fixers don't review. Keeping the roles separate is what makes the loop adversarial.
