"""
Checked-in pin for CP2-T11 (tests/test_status_plain.py's static check(...)-call guard).

Generated once (not hand-written, not hand-maintained) from `git show <BASE_COMMIT>:tests/test_status_plain.py`
via tests/test_status_plain.py's own collect_check_pairs(): every check(name, cond, ...) call site in that
pinned commit's file, as (name-source-text, condition-source-text) pairs. Committed here so CP2-T11 never
needs git history at test time -- a shallow CI clone (fetch-depth 1, actions/checkout's default) has no access
to BASE_COMMIT and would otherwise make this guard permanently and unrelatedly red.

Do not hand-edit PAIRS. Regenerate only if BASE_COMMIT itself ever changes (it should not: CP2-T11 exists to
freeze exactly this commit's check(...) call sites).
"""

BASE_COMMIT = "699da723422e890bac8f3eebbc9bbc0e63bd549d"

PAIRS = [('"[CP3-T01] exit 0 and not JSON"', 'r.returncode == 0 and not is_json'),
 ('"[CP3-T01] first line is \'Step 2 of 6 done: <title of the last finished step>\'"',
  'first.startswith("Step 2 of 6 done:") and TITLES[1] in first'),
 ('"[CP3-T01] at most 12 lines"', '0 < len(out.strip().splitlines()) <= 12'),
 ('"[CP3-T01] says what is happening now, by the current step\'s title"', 'TITLES[2] in out'),
 ('"[CP3-T02] valid JSON object"', 'isinstance(j, dict)'),
 ('"[CP3-T02] pre-existing keys keep their types"', 'all(isinstance(j.get(k), t) for k, t in want.items())'),
 ('"[CP3-T02] hooks_warning key is still there"', '"hooks_warning" in j'),
 ('"[CP3-T02] values are unchanged (phase building, current CP3, 2 of 6)"',
  'j.get("phase") == "building" and j.get("current") == "CP3" and (j.get("done"), j.get("total")) == (2, 6)'),
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
  'r.returncode == 0 and TRACE not in r.stderr and re.search(r"no plan (yet)?", r.stdout, re.I) is not None'),
 ('"[CP3-T05] never-initialised dir: non-zero, actionable message, no traceback"',
  'r.returncode != 0 and TRACE not in r.stderr and TRACE not in r.stdout and (r.stderr + r.stdout).strip() != ""'),
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
  'len([l for l in r.stdout.splitlines() if re.search(r"hooks", l, re.I) and re.search(r"skipped", l, re.I)]) == '
  '1'),
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
  're.search(r"again|retry|different approach", texts["beh"]) and re.search(r"change|revise|rewrite|adjust", '
  'texts["beh"])'),
 ('"[CP3-T11] next is JSON with phase=blocked, stop_ok=true, action"',
  'isinstance(nj, dict) and nj.get("phase") == "blocked" and nj.get("stop_ok") is True and '
  'isinstance(nj.get("action"), str)'),
 ('"[CP3-T11] options are a list of {letter, text}"',
  'isinstance(nj.get("options"), list) and all(isinstance(x, dict) and set(("letter", "text")) <= set(x)\n'
  '                                                      and re.fullmatch(r"[A-C]", str(x["letter"])) and '
  'str(x["text"]).strip()\n'
  '                                                      for x in nj["options"]) and len(nj["options"]) >= 2'),
 ('"[CP3-T11] hooks object still present"', 'isinstance(nj.get("hooks"), dict)'),
 ('"[CP3-T12] empty reason: no traceback, generic plain line"',
  'r.returncode == 0 and TRACE not in r.stderr and re.search(r"block|stuck|stopped|need", r.stdout, re.I) is not '
  'None'),
 ('"[CP3-T12] odd reason: no traceback and text kept intact"',
  'r.returncode == 0 and TRACE not in r.stderr and \'He said "hi"\' in r.stdout and "café" in r.stdout and "second '
  'line" in r.stdout'),
 ('"[CP3-T12] odd reason still gets 2-3 options"', '2 <= len(opts_in(r.stdout)) <= 3'),
 ('"[CP3-T12] paused: \'paused\' wording and no failure options"',
  'r.returncode == 0 and "paused" in r.stdout.lower() and not opts_in(r.stdout)'),
 ('"[CP3-T14] says the check must be re-run because the code changed"',
  're.search(r"re-?run|run (it|them|the checks?) again|check again", r.stdout, re.I) and re.search(r"code '
  '.*changed|changed", r.stdout, re.I)'),
 ('"[CP3-T14] no \'stale\' and no fingerprint"', 'not re.search(r"\\bstale\\b|fingerprint", r.stdout, re.I)'),
 ('"[CP3-T16] waived: a plain line says hooks were skipped at the user\'s request"',
  'len(lines) == 1 and re.search(r"request|choice|chose|asked", lines[0], re.I) is not None'),
 ('"[CP3-T16] hooks missing but not waived: no \'skipped\' line"', '"skipped" not in r.stdout.lower()'),
 ('"[CP3-T16] installing hooks removes the line"', 'inst.returncode == 0 and "skipped" not in r.stdout.lower()'),
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
 ('"[CP3-T24] the run-wide cap branch stays distinct (3 options: simplify, stop, raise; no change-the-step)"',
  'len(cap_opts) == 3 and "change the step" not in " ".join(cap_texts_low)'),
 ('"[CP3-T24] the run-wide cap branch still leads with simplify and ends with raise"',
  'bool(cap_texts_low) and "simplify" in cap_texts_low[0] and "raise" in cap_texts_low[-1]'),
 ('"[CP3-T25] next prints no accept_note: the recorded round has no open findings"',
  'nj.get("accept_note") is None'),
 ('"[CP3-T25] status prints no \'fresh look\' or accept-finding line either"',
  '"fresh look" not in out25.lower() and "accept-finding" not in out25.lower()'),
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
 ('f"[CP3-T21] {t} exits 0"', 'r.returncode == 0')]
