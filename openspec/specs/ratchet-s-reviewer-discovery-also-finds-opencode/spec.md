# Ratchet's reviewer discovery also finds OpenCode (and the providers signed in to it, such as OpenCode Go, OpenCode Zen, OpenRouter), OpenRouter, Oh My Pi (omp) and other popular agent CLIs, picks a capable model from a company other than the host's, tests it, and adds what works; it says plainly which options cost money per use, prefers flat-rate subscriptions, and never spends money on a paid per-use provider (including the test request) without the user's OK

## Purpose
What this feature does, as built and checked by Ratchet (run ratchet-s-reviewer-discovery-also-finds-opencode, 2026-10-12). Each scenario names the test that proves it.

## Requirements

### Requirement: Discovery adds a flat-rate OpenCode model
The system SHALL do this: When OpenCode is installed, discovery reads its model list, labels each signed-in provider flat-rate, per use or unknown, tests and adds a capable other-company model from a flat-rate provider, and only lists the paid ones with a plain 'costs money per use; ask the user' line.

- With a fake OpenCode listing OpenCode Go, Zen and OpenRouter models and the host on Anthropic, discovery adds one OpenCode Go model from another company (GLM 5.3 for a list like this machine's) after it answers the test request, and the output says it is flat-rate
- The fake OpenCode's call log shows no request ever went to an `opencode/` (Zen), `openrouter/` or unlisted-provider model; those are listed with 'costs money per use' (or 'cost unknown, treated as paid'), the model discovery would pick, and that the user must be asked first
- A same-company model (Claude on an Anthropic host), a model whose company is unknown, and small or preview variants (flash, mini, nano, lite, haiku, free, preview) are never picked
- If the picked model fails its test request, nothing is added and the output says why; if OpenCode is already registered (like opencode-glm), discovery leaves it alone
- SKILL.md Phase A step 3 and the README say discovery now picks a flat-rate OpenCode model itself and lists paid ones for the agent to ask about

#### Scenario: Discovery adds a flat-rate OpenCode Go model from another company
- **GIVEN** OpenCode is installed and lists the fixed model set above; nothing is registered yet; host is anthropic
- **WHEN** the agent runs `providers --discover --json`
- **THEN** a new provider is added for opencode-go/glm-5.3 only, after the fake CLI's log shows a probe/run call naming that exact model, and the JSON row for it says it is flat-rate
<!-- proven by test CP1-T01 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: Per-use and unknown-cost OpenCode models are listed but never called
- **GIVEN** the same model set, including the Zen model (opencode/gpt-5.1-codex), the OpenRouter model (openrouter/z-ai/glm-5), and the unlisted-provider model (mystery-vendor/some-model)
- **WHEN** discovery runs
- **THEN** the output's paid_options list labels the Zen and OpenRouter entries 'costs money per use' and the unlisted-provider entry 'cost unknown, treated as paid', and the fake CLI's call log contains no probe/run call naming any of those three models
<!-- proven by test CP1-T02 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: A same-company model is never picked even though it is flat-rate
- **GIVEN** opencode-go/claude-sonnet-5 (anthropic) is in the list alongside opencode-go/glm-5.3 (zhipu), host is anthropic
- **WHEN** discovery runs
- **THEN** the added provider's model is glm-5.3, never claude-sonnet-5, and the call log shows no probe/run call naming claude-sonnet-5
<!-- proven by test CP1-T03 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: A model whose company can't be determined is never picked
- **GIVEN** opencode-go/foo-widget, whose name matches none of Ratchet's company patterns, is in the list alongside opencode-go/glm-5.3
- **WHEN** discovery runs
- **THEN** the added provider's model is glm-5.3, never foo-widget, and the call log shows no probe/run call naming foo-widget
<!-- proven by test CP1-T04 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: A small/preview variant loses to its full-size sibling
- **GIVEN** opencode-go/glm-5.3-flash and opencode-go/glm-5.3 are both in the list, same company, same flat-rate provider
- **WHEN** discovery runs
- **THEN** the added provider's model is glm-5.3, never glm-5.3-flash, and the call log shows no probe/run call naming glm-5.3-flash
<!-- proven by test CP1-T05 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: A picked model that fails its test request is not added
- **GIVEN** the model set has only one other-company flat-rate candidate (opencode-go/glm-5.3) and the fake opencode CLI is set to fail (an opencode.fail marker) so the probe/run call for it errors out
- **WHEN** discovery runs
- **THEN** no new opencode-based provider is added, and the output for the opencode tool explains the test request failed (naming the reason the fake CLI reported)
<!-- proven by test CP1-T06 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: An already-registered OpenCode provider is left alone
- **GIVEN** a provider with preset opencode (for example id opencode-glm) is already registered before this run
- **WHEN** discovery runs
- **THEN** no second opencode-based provider is added, the already-registered one is unchanged, and the call log shows no new `opencode models` listing call for this discovery pass
<!-- proven by test CP1-T07 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: Each paid option names the specific model discovery would have picked and says to ask first
- **GIVEN** the Zen, OpenRouter and unlisted-provider models from the fixed list, each the only candidate for its provider
- **WHEN** discovery runs and prints its JSON output
- **THEN** each paid_options entry names its tool, provider and the exact model (gpt-5.1-codex for Zen, z-ai/glm-5 for OpenRouter, some-model for the unlisted provider) and its text says the developer must be asked before it is used
<!-- proven by test CP1-T08 in tests/test_discover.py, commit 8a9e868 -->

#### Scenario: Docs say discovery now picks a flat-rate OpenCode model and lists paid ones
- **GIVEN** skills/ratchet-orchestrator/SKILL.md (Phase A step 3) and README.md on disk
- **WHEN** the test greps both files
- **THEN** both mention OpenCode discovery picking a flat-rate model by itself and listing per-use/unknown-cost options for the agent to ask the user about before they are used
<!-- proven by test CP1-T09 in tests/test_discover.py, commit 8a9e868 -->

### Requirement: Ask before using a paid provider
The system SHALL do this: An agent can ask the developer, in plain words with an exact reply, whether to use a per-use option, and only after that exact yes does Ratchet send the test request and add it.

- Adding an OpenRouter or OpenCode Zen model (or one of unknown cost) with providers --add, checked or --no-check, is refused with a plain message telling the agent to run the paid-provider question first, and the fake CLI's log shows no request was sent
- The question names the tool, the model, that it costs money per use including one small test request now, and the exact reply that counts; it follows the existing ask rules (first reply only, same chat, one question at a time)
- After the developer's exact reply, the same providers --add sends the test request and, if it answers, adds the provider marked as per use with when the OK was given; any other first reply adds nothing and closes the question
- An OK covers only the tool and model it named: a different model, or a run with no active question, is still refused
- SKILL.md step 3, the README and schemas.md (the ask list and the provider entry's new cost fields) describe the question and the exact reply

#### Scenario: Adding a per-use OpenRouter model without asking first is refused and never calls the CLI
- **GIVEN** an active run, no pending question, and no earlier OK for openrouter/z-ai/glm-5
- **WHEN** the agent runs `providers --add opencode-glm --preset opencode --model openrouter/z-ai/glm-5`
- **THEN** the command exits non-zero, the message tells the agent to ask the paid-provider question first, nothing is added to config.json, and the fake opencode's call log is empty (no test request was sent)
<!-- proven by test CP2-T01 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: Adding the same model with --no-check is also refused before any add
- **GIVEN** the same unasked state
- **WHEN** the agent runs the same add with --no-check
- **THEN** it is refused the same way, config.json is unchanged, and nothing is probed
<!-- proven by test CP2-T02 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: The paid-provider question names the tool, the model, the cost and the exact reply
- **GIVEN** the same unasked state
- **WHEN** the agent runs `ask paid-provider opencode openrouter/z-ai/glm-5`
- **THEN** the printed question names 'opencode', names the model openrouter/z-ai/glm-5, says it costs money per use including one small test request now, and states one exact reply text the user must send; the generic listed-yes words are not offered here as an equivalent
<!-- proven by test CP2-T03 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: After the developer's exact reply, --add sends the test request and adds the provider as per-use
- **GIVEN** `ask paid-provider opencode openrouter/z-ai/glm-5` has been asked in the run's owning chat
- **WHEN** the developer's first reply in that chat is the exact text the question named, then the agent runs the same `providers --add`
- **THEN** the fake opencode's call log now shows exactly one request for openrouter/z-ai/glm-5, the add succeeds, and config.json records this provider with cost per-use and a paid_ok_at timestamp
<!-- proven by test CP2-T04 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: Any other first reply adds nothing and closes the question
- **GIVEN** `ask paid-provider opencode openrouter/z-ai/glm-5` has been asked
- **WHEN** the developer's first reply is something else (e.g. plain 'ok') and the agent then runs the same `providers --add`
- **THEN** no question is left pending, the add is still refused, config.json is unchanged, and the fake CLI's call log is still empty
<!-- proven by test CP2-T05 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: An OK for one model does not cover a different model
- **GIVEN** the developer gave the exact OK reply to `ask paid-provider opencode openrouter/z-ai/glm-5`
- **WHEN** the agent runs `providers --add` for a different model (e.g. a different OpenRouter model, or the OpenCode Zen model)
- **THEN** that add is still refused with the same message, and the call log shows no request for the new model
<!-- proven by test CP2-T06 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: An OK does not cover a run with no active question
- **GIVEN** the developer's exact OK reply for openrouter/z-ai/glm-5 was already consumed by a prior successful add (CP2-T04)
- **WHEN** the agent runs `providers --add` again for the same model id with no new pending question
- **THEN** the add is refused (the OK was consumed once) and no second request is sent to the fake CLI
<!-- proven by test CP2-T07 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: A model of unknown cost is treated like per-use and also needs the question first
- **GIVEN** the fake opencode lists a model whose provider is not in the fixed cost table
- **WHEN** the agent runs `providers --add` for that model without asking first
- **THEN** it is refused the same way as a known per-use model, and the call log is empty
<!-- proven by test CP2-T08 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: A flat-rate model needs no question
- **GIVEN** an OpenCode Go (flat-rate) model name from the fake opencode's listing
- **WHEN** the agent runs `providers --add` for it with no question asked
- **THEN** the add proceeds straight to the test request as before (unchanged CP1 behavior), proving the new check only gates per-use/unknown models
<!-- proven by test CP2-T09 in tests/test_discover_paid.py, commit 7b1a62d -->

#### Scenario: SKILL.md, README.md and schemas.md describe the paid-provider question
- **GIVEN** the repo's docs after this checkpoint's change
- **WHEN** skills/ratchet-orchestrator/SKILL.md, README.md and skills/ratchet-orchestrator/references/schemas.md are read
- **THEN** each mentions asking the developer before using a paid-per-use provider, and schemas.md documents the provider entry's new cost/paid_ok_at fields and the ask list's paid-provider action
<!-- proven by test CP2-T10 in tests/test_discover_paid.py, commit 7b1a62d -->

### Requirement: Find Oh My Pi and Pi too
The system SHALL do this: Discovery also finds Oh My Pi (omp), labels and picks its signed-in models the same way as OpenCode (adding a flat-rate one, listing paid ones), and lists Pi as a tool that needs a model.

- With a fake omp whose model list has openai-codex and opencode-go models and the host on Anthropic, discovery adds one capable flat-rate model from a company other than the host and any company already reviewing, after it answers the test request
- omp models from per-use or unknown providers are listed as paid, are never sent a request, and go through the same paid-provider question as OpenCode
- omp's review command is non-interactive and does not save a session; Pi appears under needs_model with its preset, and no preset is added for a CLI whose flags were not checked
- If omp's model listing fails or prints something unreadable, discovery falls back to listing omp under needs_model instead of failing
- schemas.md and the README list the new presets

#### Scenario: Discovery adds a flat-rate model from omp
- **GIVEN** omp is installed and signed in to openai-codex and opencode-go; the host agent is Anthropic; no provider is registered yet
- **WHEN** the agent runs `providers --discover --json`
- **THEN** exactly one new provider is added with preset omp, a model from a company other than anthropic, cost "flat", and omp's call log shows one listing call and one passing test-request call for the picked model only
<!-- proven by test CP3-T01 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: omp prefers a company with no reviewer yet over one already covered
- **GIVEN** opencode-glm (company zhipu, via opencode-go) is already a working provider; omp lists an openai-codex model (company openai) and an opencode-go glm model (company zhipu, same as the existing reviewer)
- **WHEN** the agent runs `providers --discover --json`
- **THEN** discovery adds the openai-codex model (openai), not the zhipu model that already has a working reviewer, and omp's call log never shows a request to the zhipu model
<!-- proven by test CP3-T02 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: omp is skipped once a provider from it is already registered
- **GIVEN** a provider with preset omp (e.g. omp-codex) is already in config.json
- **WHEN** the agent runs `providers --discover --json`
- **THEN** omp's call log stays empty (no listing call, no probe), no second omp provider is added, and the existing one is left untouched
<!-- proven by test CP3-T03 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: omp's per-use and unknown-cost models are listed as paid, never probed
- **GIVEN** omp lists an opencode-go model (flat) alongside a model from a provider the cost table marks per-use and one from a provider not in the table at all
- **WHEN** the agent runs `providers --discover --json`
- **THEN** the output's paid_options list has one entry per paid model naming omp as the tool, each labelled "per use" or "cost unknown, treated as paid" as appropriate, and omp's call log shows no test-request call to any of those models (only the listing call and the one call for the flat pick)
<!-- proven by test CP3-T04 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: A paid omp model goes through the same ask flow as OpenCode
- **GIVEN** an active run; omp lists only per-use/unknown models; the agent calls `providers --add omp-or --preset omp --model <that model>` with no answered question yet
- **WHEN** then the agent asks the paid-provider question naming omp and that model and costing money per use, and the developer sends back the exact reply it printed
- **THEN** the first `--add` before the reply is refused and omp's log stays empty; after the exact reply the same `--add` sends exactly one test request to that model and, once it passes, adds it marked cost "per-use"
<!-- proven by test CP3-T05 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: omp's review command is non-interactive and keeps no session
- **GIVEN** a provider added with preset omp and a chosen model
- **WHEN** Ratchet builds that provider's review command
- **THEN** the command includes omp's print flag and its model flag with the chosen model, includes the no-session flag, and includes no flag that persists or resumes a session
<!-- proven by test CP3-T06 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: omp falls back to needs_model when its model list can't be read
- **GIVEN** omp is installed, but `omp models --json` exits non-zero or prints unreadable output
- **WHEN** the agent runs `providers --discover --json`
- **THEN** the command still exits 0, nothing is added for omp, omp appears in needs_model the way a lister-less tool would, and every other preset in the same run is still processed
<!-- proven by test CP3-T07 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: Pi is listed under needs_model; crush gets no preset at all
- **GIVEN** pi and crush are both installed; pi has a preset (no lister) and crush has none
- **WHEN** the agent runs `providers --discover --json`
- **THEN** pi appears in needs_model with its preset name so the agent can add it with a model, and crush never appears anywhere in the output and is never invoked
<!-- proven by test CP3-T08 in tests/test_discover_omp.py, commit 8706723 -->

#### Scenario: Docs list the new omp and Pi presets
- **GIVEN** schemas.md and README.md in the repo
- **WHEN** a test reads them
- **THEN** schemas.md's provider/preset reference and the README both mention omp (Oh My Pi) and Pi by name, and README states omp is discovered and labelled the same way as OpenCode
<!-- proven by test CP3-T09 in tests/test_discover_omp.py, commit 8706723 -->
