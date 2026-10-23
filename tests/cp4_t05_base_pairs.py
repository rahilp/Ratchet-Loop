"""
Checked-in pin for CP4-T05 (tests/test_load_determinism.py's static check(...)-call guard over
tests/test_status_plain.py and tests/test_install.py).

Generated once (not hand-written, not hand-maintained) from `git show <BASE_COMMIT>:<path>` for each of the two
files below, via tests/test_status_plain.py's own collect_check_pairs(): every check(name, cond, ...) call site
in that pinned commit's file, as (name-source-text, condition-source-text) pairs. Committed here so CP4-T05 never
needs git history at test time -- a shallow CI clone (fetch-depth 1, actions/checkout's default) has no access
to an ancestor commit like this one once a later commit is checked out, and would otherwise make this guard
permanently and unrelatedly red (codex-standards/X1, codex-correctness/X1, CP4 adversarial round 1).

BASE_COMMIT is this checkpoint's own starting commit (right after CP3 landed on this branch), the same commit
tests/test_load_determinism.py's CP4-T05 freezes against. It is a different commit from, and covers a different
extra file (tests/test_install.py) than, tests/cp2_t11_base_pairs.py's own BASE_COMMIT -- that fixture only pins
tests/test_status_plain.py at CP2's starting commit, so it cannot be reused here.

Do not hand-edit PAIRS_BY_FILE. Regenerate only if BASE_COMMIT itself ever changes (it should not: CP4-T05
exists to freeze exactly this commit's check(...) call sites in both files).
"""

BASE_COMMIT = "0d3fd10acdf24f3c9f1d2dfecd3c559f03c7b9cf"

PAIRS_BY_FILE = {
    "tests/test_status_plain.py": [('"[CP2-T04] the snapshot read here is the genuine pre-CP2 result, not a later overwrite -- a "\n'
  '          "different object than what CP3_T21_RESULTS now holds after cp2_t02()\'s stubbed call"',
  'real_result is not None and real_result is not CP3_T21_RESULTS.get("test_hooks.py")'),
 ('"[CP2-T04] the test_hooks.py result CP3-T21 already produced earlier in this run (real run, or "\n'
  '          "skip when already covered by run_all.py) finished and passed, with no second real invocation '
  'here"',
  'real_result is not None and getattr(real_result, "returncode", None) == 0'),
 ('"[CP2-T06] no subprocess re-run of either suite happens"', 'ok and argvs == []'),
 ('"[CP2-T06] the SAME pre-existing check(...) call per suite still fires and records a pass for each "\n'
  '          "-- not a second check() call added for the skip path"',
  'ok and status_after is True'),
 ('"[CP2-T07] test_gates.py is skipped (no subprocess call) but test_hooks.py is still run as a real "\n'
  '          "subprocess"',
  'ok and names == ["test_hooks.py"]'),
 ('"[CP2-T07] both suites are still proven pass/fail by the block\'s check(...) call"',
  'ok and status_after is True'),
 ('"[CP2-T08] both suites still run as real subprocesses -- the suites list alone, without the flag, "\n'
  '          "never triggers a skip"',
  'ok and names == ["test_gates.py", "test_hooks.py"]'),
 ('"[CP2-T11] setup: the checked-in base fixture (tests/cp2_t11_base_pairs.py) pins the exact base "\n'
  '          "commit this guard freezes"',
  'cp2_t11_base_pairs.BASE_COMMIT == STATUS_PLAIN_BASE_COMMIT'),
 ('"[CP2-T11] setup: the pinned base file contains at least one check(...) call"',
  'sum(pinned_pairs.values()) > 0'),
 ('"[CP2-T11] every check(...) call from before this checkpoint is still present, unreworded, with its "\n'
  '          "original condition (exact multiset match; new calls may be ADDED for this checkpoint\'s own '
  'new "\n'
  '          "cases, but none existing may be removed, reworded, or have its condition narrowed)"',
  'not missing'),
 ('"[CP2-T12] exactly one check(...) call site in the source names CP3-T21 (folding the skip into the "\n'
  '          "pre-existing call, matching the test_trust_cp5_regression.py precedent, never a second call '
  'site "\n'
  '          "added)"',
  'len(sites) == 1'),
 ('"[CP2-T12] a fresh, uncovered CP3-T21 run fires that one call site exactly once for test_gates.py\'s "\n'
  '          "result and exactly once for test_hooks.py\'s result"',
  'ok and after_gates == 1 and after_hooks == 1'),
 ('"[CP3-T01] exit 0 and not JSON"', 'r.returncode == 0 and not is_json'),
 ('"[CP3-T01] first line is \'Step 2 of 6 done: <title of the last finished step>\'"',
  'first.startswith("Step 2 of 6 done:") and TITLES[1] in first'),
 ('"[CP3-T01] at most 12 lines"', '0 < len(out.strip().splitlines()) <= 12'),
 ('"[CP3-T01] says what is happening now, by the current step\'s title"', 'TITLES[2] in out'),
 ('"[CP3-T02] valid JSON object"', 'isinstance(j, dict)'),
 ('"[CP3-T02] pre-existing keys keep their types"',
  'all(isinstance(j.get(k), t) for k, t in want.items())'),
 ('"[CP3-T02] hooks_warning key is still there"', '"hooks_warning" in j'),
 ('"[CP3-T02] values are unchanged (phase building, current CP3, 2 of 6)"',
  'j.get("phase") == "building" and j.get("current") == "CP3" and (j.get("done"), j.get("total")) == (2, '
  '6)'),
 ('"[CP3-T03] 0 done: \'Step 0 of 6 done\' or \'Not started\'"',
  '"Step 0 of 6 done" in z or "not started" in z.lower()'),
 ('"[CP3-T03] 1 done: first line \'Step 1 of 6 done: <title>\'"',
  'o.strip().splitlines()[0].startswith("Step 1 of 6 done:") and TITLES[0] in o.splitlines()[0]'),
 ('"[CP3-T03] all done: says all 6 steps are done, never \'Step 7 of 6\'"',
  're.search(r"all 6 steps", a, re.I) is not None and "Step 7" not in a'),
 ('"[CP3-T04] every phase prints something different"', 'len(set(outs.values())) == len(outs)'),
 ('"[CP3-T04] plan approval waits for the user\'s yes"',
  're.search(r"plan", outs["awaiting_plan_approval"], re.I) and re.search(r"approv|yes|your", '
  'outs["awaiting_plan_approval"], re.I)'),
 ('"[CP3-T04] paused says paused"', '"paused" in outs["paused"].lower()'),
 ('"[CP3-T04] cancelled says cancelled"', '"cancel" in outs["cancelled"].lower()'),
 ('"[CP3-T04] review phase asks the user to look at the step"',
  're.search(r"review|look|try", outs["awaiting_checkpoint_review"], re.I) is not None'),
 ('"[CP3-T05] fresh project: exit 0, no traceback, plain \'no plan yet\' line"',
  'r.returncode == 0 and TRACE not in r.stderr and re.search(r"no plan (yet)?", r.stdout, re.I) is not '
  'None'),
 ('"[CP3-T05] never-initialised dir: non-zero, actionable message, no traceback"',
  'r.returncode != 0 and TRACE not in r.stderr and TRACE not in r.stdout and (r.stderr + r.stdout).strip() '
  '!= ""'),
 ('"[CP3-T06] plan-summary exits 0"', 'r.returncode == 0'),
 ('"[CP3-T06] each of the six titles appears on exactly one line, in plan order"', 'blks is not None'),
 ('"[CP3-T06] each step shows its first done-when line"', 'all(DONE_WHEN[i] in blks[i] for i in range(6))'),
 ('"[CP3-T06] each step states \'UI review: yes|no\' matching the plan"',
  'all(m and (m.group(1).lower() == "yes") == UI[i] for i, m in enumerate(ui_marks))'),
 ('"[CP3-T06] each step states its risk in words"',
  'all(re.search(r"risk\\W+" + RISKS[i] + r"\\b", blks[i], re.I) for i in range(6))'),
 ('"[CP3-T07] low-risk step names its one reviewer in plain words"',
  'len(low) == 1 and "(code standards and bugs)" in low[0]'),
 ('"[CP3-T07] high-risk step lists more reviewers than the low-risk one, incl. one on bugs"',
  'len(high) == len(depth["high"]) > len(low) and any("(bugs and security)" in h for h in high)'),
 ('"[CP3-T07] normal-risk step differs from both"',
  'normal not in (low, high) and len(normal) == len(depth["normal"])'),
 ('"[CP3-T07] no reviewer ids (host-both, codex-correctness) or arrows in the summary"',
  'not re.search(r"host-|-correctness|-standards|any-|→", "\\n".join(blks))'),
 ('"[CP3-T07] the summary names the models and warns when no other provider can review"',
  '"Models:" in r.stdout and "no other provider is available" in r.stdout'),
 ('"[CP3-T07] UI review says yes for exactly the two UI steps"',
  '[bool(m and m.group(1).lower() == "yes") for m in ui_marks] == UI'),
 ('"[CP3-T08] no plan: non-zero, message, no traceback"',
  'r.returncode != 0 and TRACE not in r.stderr and (r.stderr + r.stdout).strip() != ""'),
 ('"[CP3-T08] waived: still lists all six steps"', 'r.returncode == 0 and b2 is not None'),
 ('"[CP3-T08] waived: one line notes that hooks were skipped"',
  'len([l for l in r.stdout.splitlines() if re.search(r"hooks", l, re.I) and re.search(r"skipped", l, '
  're.I)]) == 1'),
 ('"[CP3-T08] not waived: no \'skipped\' note"', '"skipped" not in r.stdout.lower()'),
 ('"[CP3-T09] status quotes the reason in plain words"', 'r.returncode == 0 and quoted(r.stdout, P_BEH)'),
 ('"[CP3-T09] status offers 2 or 3 options lettered A), B)[, C)]"',
  '2 <= len(o) <= 3 and [x[0] for x in o] == ["A", "B", "C"][:len(o)]'),
 ('"[CP3-T09] next also carries 2 or 3 lettered options"',
  'isinstance(nopts, list) and 2 <= len(nopts) <= 3 and [x.get("letter") for x in nopts] == ["A", "B", '
  '"C"][:len(nopts)]'),
 ('"[CP3-T09] next keeps the raw reason and adds a plain one"',
  'R_BEH in json.dumps(nj.get("action", "")) and nj.get("reason_plain") == P_BEH'),
 ('"[CP3-T10] option sets differ by cause"', 'len(set(texts.values())) == 3 and "" not in texts.values()'),
 ('"[CP3-T10] round cap offers raising the limit or stopping"',
  're.search(r"raise|increase|allow more|higher", texts["cap"]) and "stop" in texts["cap"]'),
 ('"[CP3-T10] test failure offers a retry or revising the step"',
  're.search(r"again|retry|different approach", texts["beh"]) and '
  're.search(r"change|revise|rewrite|adjust", texts["beh"])'),
 ('"[CP3-T11] next is JSON with phase=blocked, stop_ok=true, action"',
  'isinstance(nj, dict) and nj.get("phase") == "blocked" and nj.get("stop_ok") is True and '
  'isinstance(nj.get("action"), str)'),
 ('"[CP3-T11] options are a list of {letter, text}"',
  'isinstance(nj.get("options"), list) and all(isinstance(x, dict) and set(("letter", "text")) <= set(x)\n'
  '                                                      and re.fullmatch(r"[A-C]", str(x["letter"])) and '
  'str(x["text"]).strip()\n'
  '                                                      for x in nj["options"]) and len(nj["options"]) >= '
  '2'),
 ('"[CP3-T11] hooks object still present"', 'isinstance(nj.get("hooks"), dict)'),
 ('"[CP3-T12] empty reason: no traceback, generic plain line"',
  'r.returncode == 0 and TRACE not in r.stderr and re.search(r"block|stuck|stopped|need", r.stdout, re.I) '
  'is not None'),
 ('"[CP3-T12] odd reason: no traceback and text kept intact"',
  'r.returncode == 0 and TRACE not in r.stderr and \'He said "hi"\' in r.stdout and "café" in r.stdout and '
  '"second line" in r.stdout'),
 ('"[CP3-T12] odd reason still gets 2-3 options"', '2 <= len(opts_in(r.stdout)) <= 3'),
 ('"[CP3-T12] paused: \'paused\' wording and no failure options"',
  'r.returncode == 0 and "paused" in r.stdout.lower() and not opts_in(r.stdout)'),
 ('"[CP3-T14] says the check must be re-run because the code changed"',
  're.search(r"re-?run|run (it|them|the checks?) again|check again", r.stdout, re.I) and re.search(r"code '
  '.*changed|changed", r.stdout, re.I)'),
 ('"[CP3-T14] no \'stale\' and no fingerprint"',
  'not re.search(r"\\bstale\\b|fingerprint", r.stdout, re.I)'),
 ('"[CP3-T16] waived: a plain line says hooks were skipped at the user\'s request"',
  'len(lines) == 1 and re.search(r"request|choice|chose|asked", lines[0], re.I) is not None'),
 ('"[CP3-T16] hooks missing but not waived: no \'skipped\' line"', '"skipped" not in r.stdout.lower()'),
 ('"[CP3-T16] installing hooks removes the line"',
  'inst.returncode == 0 and "skipped" not in r.stdout.lower()'),
 ('"[CP3-T13] no jargon word or raw phase id in any user-facing output"', 'not bad'),
 ('"[CP3-T13] the outputs were actually produced"',
  'all(v.strip() for k, v in seen_text.items() if "warning" not in k)'),
 ('"[CP3-T24] per-checkpoint limit offers exactly 4 options (CP1 order: simplify, change, stop, raise)"',
  'len(opts) == 4'),
 ('"[CP3-T24] option 1 simplifies the step"', 'len(texts_low) > 0 and "simplify" in texts_low[0]'),
 ('"[CP3-T24] option 2 changes the step so the review notes no longer apply"',
  'len(texts_low) > 1 and "change the step" in texts_low[1]'),
 ('"[CP3-T24] option 3 stops and keeps what is finished"', 'len(texts_low) > 2 and "stop" in texts_low[2]'),
 ('"[CP3-T24] option 4 (last) raises the review limit"',
  'len(texts_low) > 3 and "raise the review limit" in texts_low[3]'),
 ('"[CP3-T24] the run-wide cap branch stays distinct (3 options: simplify, stop, raise; no '
  'change-the-step)"',
  'len(cap_opts) == 3 and "change the step" not in " ".join(cap_texts_low)'),
 ('"[CP3-T24] the run-wide cap branch still leads with simplify and ends with raise"',
  'bool(cap_texts_low) and "simplify" in cap_texts_low[0] and "raise" in cap_texts_low[-1]'),
 ('"[CP3-T25] next prints no accept_note: the recorded round has no open findings"',
  'nj.get("accept_note") is None'),
 ('"[CP3-T25] status prints no \'fresh look\' or accept-finding line either"',
  '"fresh look" not in out25.lower() and "accept-finding" not in out25.lower()'),
 ('f"[CP3-T21] {t} exits 0"', 'r.returncode == 0'),
 ('f"[CP2-T01] {label}: rx()\'s subprocess timeout is exactly 120, byte for byte the same as before "\n'
  '              "this checkpoint"',
  'r.timeout_limit == 120'),
 ('f"[CP2-T01] {label}: the status call still completes normally (exit 0)"', 'r.returncode == 0'),
 ('f"[CP2-T02] {label}: the timeout used for test_hooks.py is exactly 590, unchanged from before "\n'
  '              "this checkpoint"',
  'ok and getattr(result, "timeout_limit", None) == 590'),
 ('f"[CP2-T03] load {load} on {cpus} CPUs: rx()\'s subprocess timeout equals exactly "\n'
  '              f"120*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected}), never less '
  'than 120"',
  'r.timeout_limit == expected'),
 ('f"[CP2-T03] load {load} on {cpus} CPUs: the real status call still completes and passes inside "\n'
  '              "that stretched budget"',
  'r.returncode == 0'),
 ('f"[CP2-T04] load {load} on {cpus} CPUs: the timeout used for test_hooks.py equals exactly "\n'
  '              f"590*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected}), never less '
  'than 590"',
  'ok and getattr(result, "timeout_limit", None) == expected'),
 ('f"[CP2-T05] RATCHET_RUN_ALL_SUITES {label}: both test_gates.py and test_hooks.py are run as "\n'
  '              "real subprocess calls (never skipped)"',
  'ok and names == ["test_gates.py", "test_hooks.py"]'),
 ('f"[CP2-T05] RATCHET_RUN_ALL_SUITES {label}: the block\'s check(...) call records a pass for each, "\n'
  '              "exactly as it did before this checkpoint"',
  'ok and status_after is True'),
 ('f"[CP2-T10] {label}: rx()\'s effective timeout equals exactly "\n'
  '              f"120*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected_rx})"',
  'r.timeout_limit == expected_rx'),
 ('f"[CP2-T10] {label}: CP3-T21\'s effective timeout (for a suite that actually runs) equals exactly "\n'
  '              f"590*run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] ({expected_cp3})"',
  'ok and getattr(result, "timeout_limit", None) == expected_cp3'),
 ('f"[CP2-T10] {label} spike: a load spike mid-call raises the effective timeout to the peak "\n'
  '              f"stretch_factor() reading reached ({base}*DEFAULT_MAX_STRETCH={expected_peak}), '
  're-sampled on "\n'
  '              "every poll, never a one-time snapshot taken before the call and never a later lower '
  'reading "\n'
  '              "taken after the spike drops"',
  'ok and getattr(result, "timeout_limit", None) == expected_peak'),
 ('f"[CP2-T10] {label} spike: the call still finishes and reports success once the scripted "\n'
  '              "process exits"',
  'ok and getattr(result, "returncode", None) == 0'),
 ('f"[CP3-T04] {name}: exit 0, no traceback, no raw phase identifier"',
  'r.returncode == 0 and TRACE not in r.stderr and not RAW_PHASE.search(r.stdout)'),
 ('f"[CP3-T10] {key}: status quotes its own reason in plain words"',
  'quoted(status_text(d).stdout, {"beh": P_BEH, "adv": P_ADV, "cap": P_CAP}[key])'),
 ('f"[CP3-T15] {label}: addressed to the agent (offer to the user, ask first)"',
  're.search(r"offer", txt, re.I) and re.search(r"\\bask\\b", txt, re.I)'),
 ('f"[CP3-T15] {label}: never tells the user to type a ratchet.py command"', '"ratchet.py" not in txt'),
 ('f"[CP3-T22] {label}: no \'another round\' option, offers raising the limit"',
  '"another round" not in low and re.search(r"raise the review limit", low) is not None'),
 ('f"[CP3-T22] {label}: {expected} options (CP1 order), simplify first and raise last"',
  'len(opts) == expected and bool(texts_low) and "simplify" in texts_low[0]\n'
  '              and "raise" in texts_low[-1]'),
 ('f"[CP3-T23] {label}: status text has no jargon"', 'not JARGON.search(out)'),
 ('f"[CP3-T23] {label}: view output has no jargon"', 'not JARGON.search(view)'),
 ('f"[CP3-T23] {label}: --json keeps the raw reason and adds a plain one"',
  'sj.get("blocked_reason") == reason and sj.get("blocked_reason_plain") and not '
  'JARGON.search(sj["blocked_reason_plain"])'),
 ('f"[CP2-T09] combo {i} ({env}): covered_suites() matches run_all_covers() computed on the "\n'
  '                  "same universe and environment"',
  'ok and mine == theirs')],
    "tests/test_install.py": [('"[CP1-T01] load 1 on 8 CPUs (load/cpus=0.125, well under 1) leaves the subprocess timeout at exactly '
  '120"',
  'kw.get("timeout") == 120'),
 ('"[CP1-T01] the call completes normally (exit 0)"', 'kw.get("returncode") == 0'),
 ('"[CP1-T02] RATCHET_FAKE_LOADAVG=unavailable leaves the subprocess timeout at exactly 120, the same as '
  '"\n'
  '          "when load cannot be stretched"',
  'kw.get("timeout") == 120'),
 ('"[CP1-T02] the call completes normally (exit 0)"', 'kw.get("returncode") == 0'),
 ('"[CP1-T03] RATCHET_FAKE_LOADAVG=not-a-number raises no exception, and the subprocess timeout passed is '
  '"\n'
  '          "exactly 120, matching run_all.read_load()\'s own fallback for an unusable reading"',
  'kw.get("timeout") == 120'),
 ('"[CP1-T03] the call completes normally (exit 0)"', 'kw.get("returncode") == 0'),
 ('"[CP1-T04] load 8 on 4 CPUs (load/cpus=2.0, under run_all.DEFAULT_MAX_STRETCH) gives "\n'
  '          "run_all.stretch_factor() a factor of exactly 2.0"',
  'expected_factor == 2.0'),
 ('"[CP1-T04] the subprocess timeout passed equals 120 * '
  'run_all.stretch_factor(run_all.DEFAULT_MAX_STRETCH)[0] "\n'
  '          "computed fresh with the same fake env (240.0), not a hardcoded or independently-derived '
  'number"',
  'kw.get("timeout") == 120 * expected_factor'),
 ('"[CP1-T04] the call completes normally (exit 0) under the simulated load"', 'kw.get("returncode") == 0'),
 ('"[CP1-T05] load 400 on 4 CPUs (load/cpus=100, far past the ceiling) is capped by '
  'run_all.stretch_factor() "\n'
  '          "at exactly DEFAULT_MAX_STRETCH (4.0)"',
  'expected_factor == run_all.DEFAULT_MAX_STRETCH'),
 ('"[CP1-T05] the subprocess timeout passed equals 120 * run_all.DEFAULT_MAX_STRETCH exactly (480.0), "\n'
  '          "matching run_all.stretch_factor() exactly, never 120*100 and never unbounded"',
  'kw.get("timeout") == 120 * run_all.DEFAULT_MAX_STRETCH and kw.get("timeout") != 120 * 100'),
 ('"[CP1-T05] the call completes normally (exit 0) under the simulated load"', 'kw.get("returncode") == 0'),
 ('"[CP1-T06] a hanging call raises subprocess.TimeoutExpired, never completing normally"',
  'exc is not None'),
 ('"[CP1-T06] it is stopped at exactly the stretched limit (0.1s base * stretch_factor(load 8 on 4 "\n'
  '          "CPUs) = 0.2s), computed fresh from run_all.stretch_factor(), never a flat or hardcoded '
  'value"',
  'exc is not None and exc.timeout == expected_timeout'),
 ('"[CP1-T06] run_all.kill_tree is called exactly once, with the exact process object the timed-out "\n'
  '          "call started"',
  'len(made_procs) == 1 and kill_calls == made_procs'),
 ('"[CP1-T07] the call finishes normally once the scripted process exits"', 'result.returncode == 0'),
 ('"[CP1-T07] the limit used is the peak stretch reached mid-call (1.0 * DEFAULT_MAX_STRETCH = 4.0), "\n'
  '          "never the lower reading taken afterward (1.0 * 1.0 = 1.0)"',
  'result.timeout_limit == expected_peak'),
 ('"[CP6-T09] --host all exits 0 and installs the Claude copy (skills and agents) under HOME/.claude"',
  'r.returncode == 0 and (claude_root / "skills/ratchet-orchestrator/SKILL.md").exists() and (claude_root '
  '/ "agents/ratchet-fixer.md").exists()'),
 ('"[CP6-T09] --host all also installs the RATCHET_HOME copy, with all six skill folders in each copy"',
  '(other_root / "skills/ratchet-orchestrator/SKILL.md").exists() and (other_root / '
  '"agents/ratchet-fixer.md").exists() and both'),
 ('"[CP6-T10] --host all writes Ratchet hook entries for Claude, Codex, Gemini and Copilot, each in its '
  'own file under the temp HOME"',
  'not missing'),
 ('"[CP6-T10] the real HOME\'s hook files and ~/.ratchet-loop are untouched"',
  'real_home_marks() == before'),
 ('"[CP6-T12] a second --host all exits 0 and leaves exactly one Ratchet entry per event and host"',
  'r2.returncode == 0 and set(dup) == set(HOOK_FILES) and all(v == once for v in dup.values())'),
 ('"[CP6-T12] a stale file inside an installed skill folder is gone after re-install (both copies)"',
  'not stale.exists() and not stale_c.exists() and r2.returncode == 0'),
 ('"[CP6-T12] hook files are still valid JSON after the second run"',
  'all(v == once for v in dup.values()) and len(dup) == 4'),
 ('"[CP6-T15] closing lines tell the user to restart the CLI and then say a quoted sentence mentioning '
  'ratchet to the agent"',
  're.search(r"restart", tail, re.I) and re.search(r"[\\"\'“][^\\"\'”\\n]*ratchet[^\\"\'”\\n]*[\\"\'”]", '
  'lines[-1] if lines else "", re.I) is not None'),
 ('"[CP6-T15] closing lines do not tell the user to run ratchet.py doctor or a python3 command"',
  'lines != [] and not re.search(r"python3|ratchet\\.py|\\bdoctor\\b", tail)'),
 ('"[CP6-T11] --host claude installs into HOME/.claude only and leaves RATCHET_HOME empty"', 'ok'),
 ('"[CP6-T11] --host codex installs into RATCHET_HOME only and creates no HOME/.claude"', 'ok'),
 ('"[CP6-T16] --host codex and --host all messages name the RATCHET_HOME skills folder and say to link it"',
  'names_folder(rc2.stdout, e.rh) and names_folder(ra.stdout, e.rh)'),
 ('"[CP6-T16] neither message mentions \'Other agent CLIs\'"',
  'not re.search(r"other agent clis?", rc2.stdout + ra.stdout, re.I)'),
 ('"[CP6-T16] a --host claude run does not print the non-Claude link line"',
  'not re.search(r"link|point your|symlink", claude_out, re.I) and str(e.rh) not in claude_out'),
 ('"[CP6-T25] no --host inside Codex installs for Codex (shared copy + Codex hooks, no Claude copy)"',
  'r.returncode == 0 and "Installing for: codex" in r.stdout and (e.home / ".codex/hooks.json").exists()\n'
  '              and not (e.home / ".claude").exists()'),
 ('"[CP6-T25] the skills are linked into ~/.codex/skills and point at the shared copy"',
  'link.is_symlink() and link.resolve() == (e.rh / "skills/ratchet-orchestrator").resolve()\n'
  '              and (link / "SKILL.md").exists()'),
 ('"[CP6-T25] re-running keeps a single working link per skill"',
  'r2.returncode == 0 and link.is_symlink() and (link / "SKILL.md").exists()'),
 ('"[CP6-T25] no --host and no CLI detected falls back to Claude Code and says so"',
  'r.returncode == 0 and "Could not tell" in r.stdout and (e.home / '
  '".claude/skills/ratchet-orchestrator").exists()'),
 ('"[CP6-T25] the closing line matches the README\'s sentence"',
  '"Set up Ratchet and continue" in r.stdout'),
 ('"[CP6-T13] --host all --project exits 0 and puts the Claude copy in the project\'s .claude, not in '
  'HOME/.claude"',
  'rp.returncode == 0 and (e.cwd / ".claude/skills/ratchet-orchestrator/SKILL.md").exists() and not '
  '(e.home / ".claude").exists()'),
 ('"[CP6-T18] --host all --project writes all four hook files under the project dir, each with Ratchet '
  'entries"',
  'not miss'),
 ('"[CP6-T18] --project writes no hook file under HOME"', 'not stray'),
 ('"[CP6-T19] --project keeps the shared non-Claude skills copy in RATCHET_HOME and none in the project"',
  '(e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and not (e.cwd / ".ratchet-loop").exists()'),
 ('"[CP6-T14] --host nonsense exits non-zero with a short message naming the value, no traceback, nothing '
  'copied"',
  'rb.returncode != 0 and "nonsense" in msg(rb) and "Traceback" not in msg(rb) and '
  'len(msg(rb).splitlines()) <= 2 and after_bad == []'),
 ('"[CP6-T14] --bogus exits non-zero with a short message naming the option, and copies nothing"',
  'ro.returncode != 0 and "--bogus" in msg(ro) and "Traceback" not in msg(ro) and '
  'len(msg(ro).splitlines()) <= 2 and e.files(e.home) + e.files(e.rh) == []'),
 ('f"[CP6-T20] --host {host} exits 0, installs RATCHET_HOME skills, writes only its own hook file with one '
  'entry per event"',
  'r1.returncode == 0 and (e.rh / "skills/ratchet-orchestrator/SKILL.md").exists() and f.exists()\n'
  '                  and entry_counts(f, host) == {"stop": 1, "prompt": 1, "guard": 1} and not others and '
  'not (e.home / ".claude").exists()'),
 ('f"[CP6-T21] --host {host} --project puts its hook file in the project, not HOME, and the skills copy in '
  'RATCHET_HOME"',
  'rq.returncode == 0 and pf.exists() and entry_counts(pf, host) == {"stop": 1, "prompt": 1, "guard": 1}\n'
  '                  and not (e.home / rel).exists() and (e.rh / '
  '"skills/ratchet-orchestrator/SKILL.md").exists()')],
}
