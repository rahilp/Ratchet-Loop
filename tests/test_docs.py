#!/usr/bin/env python3
"""
Docs lint: the skills must tell the agent (not the user) what to do. Each checkpoint owns one marked section below
(`# ---- CPn ----`); every section runs independently and the script exits non-zero if any check fails.
    python3 tests/test_docs.py

String lint only: it reads repo files, never touches .ratchet/, and matches required concepts with tolerant
regex alternatives so wording can vary.
"""
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


def has(text, *patterns):
    """True when EVERY pattern (a regex with alternatives) matches, case-insensitively."""
    return all(re.search(p, text, re.I | re.S) for p in patterns)


PROVIDERS = ("claude", "codex", "gemini", "copilot")


def providers_named(text):
    return {p for p in PROVIDERS if re.search(r"\b" + p, text, re.I)}


# ---- CP4 ----
# Orchestrator SKILL.md: agent-led hooks check, blockers message, whole-plan review, clear-yes approvals.

STEP_RX = re.compile(r"(?m)^(?=\s*\d+[a-z]?[.)]\s)")


def section(text, prefix):
    for chunk in re.split(r"(?m)^## ", text)[1:]:
        if chunk.lower().startswith(prefix.lower()):
            return chunk
    return ""


def steps(phase):
    return [s for s in STEP_RX.split(phase) if re.match(r"\s*\d+[a-z]?[.)]\s", s)]


def cp4_parts(doc):
    setup, a = section(doc, "Setup"), section(doc, "Phase A")
    st = steps(a)

    def first(pred, start=0):
        for i in range(start, len(st)):
            if pred(st[i]):
                return i
        return None

    init_i = first(lambda s: re.search(r"\binit\b", s) is not None)
    hook_i = first(lambda s: "doctor --json" in s)
    stop_i = first(lambda s: re.search(r"STOP\s*#?\s*1", s[:80], re.I) is not None)
    if stop_i is None:
        stop_i = first(lambda s: re.search(r"STOP\s*#?\s*1", s) is not None and "approve-plan" not in s)
    block_i = first(lambda s: re.search(r"blocker", s, re.I) is not None and s is not (st[hook_i] if hook_i is not None else None))
    hooks = ""
    if hook_i is not None:
        end = min([i for i in (block_i if block_i and block_i > hook_i else None, stop_i) if i is not None] or [len(st)])
        end = max(end, hook_i + 1)
        hooks = "".join(st[hook_i:end])
    blockers = ""
    if block_i is not None:
        blockers = "".join(st[block_i:stop_i if stop_i and stop_i > block_i else block_i + 1])
    stop = st[stop_i] if stop_i is not None else ""
    approve = ""
    if stop_i is not None:
        j = first(lambda s: "approve-plan" in s, stop_i + 1)
        approve = "".join(st[j:]) if j is not None else ""
    pos = {"init": init_i, "hooks": hook_i, "blockers": block_i, "stop": stop_i}
    return dict(setup=setup, phase_a=a, steps=st, hooks=hooks, blockers=blockers, stop=stop, approve=approve, pos=pos)


# --- individual lint predicates (pure functions of the parsed doc, so T16 can aim them at a stub) ---

def l_hooks_after_init(p):  # T01
    h, ps = p["hooks"], p["pos"]
    order = ps["init"] is not None and ps["hooks"] is not None and ps["init"] <= ps["hooks"]
    a = p["phase_a"]
    before_submit = "doctor --json" in a and "plan-submit" in a and a.index("doctor --json") < a.index("plan-submit")
    agent_does = not has(h, r"\b(you run|user runs|ask the user to run|tell the user to run|please run)\b") and bool(h)
    return bool(order and before_submit and agent_does)


def l_install_offer(p):  # T02
    h = p["hooks"]
    return has(h, r"missing|not installed|no hooks|aren.t installed|isn.t installed", r"offer", r"install-hooks",
               r"\b(host|current|this|the) (agent )?CLI|CLI (in use|you are|you.re|the agent is running)|which(ever)? CLI|running in",
               r"(nothing|never|not|n.t)\b[^.]{0,80}install[^.]{0,120}(agree|yes|confirm|consent|say)"
               r"|(after|only after|once|until)[^.]{0,40}(yes|agrees?|confirm|consent|says yes)"
               r"|ask[^.]{0,30}first") and len(providers_named(h)) != 1


def l_restart_note(p):  # T03
    h = p["hooks"]
    return has(h, r"restart|new session|new chat|reopen|start (a )?new", r"take effect|needed|must|required|until|only (start|work)",
               r"resume|continue|pick (it )?up|carry on|come back")


def l_decline_sentence(p):  # T04
    h = p["hooks"]
    if not has(h, r"declin|say(s)? no|would rather not|doesn.t want|refus|turns? (it|them) down"):
        return False
    m = re.search(r"(one|single|1) (short |plain )?(sentence|line)", h, re.I)
    if not m:
        return False
    near = h[max(0, m.start() - 250): m.end() + 250]
    return has(near, r"advice|advisory|not enforced|aren.t enforced|isn.t enforced|only suggest")


def l_decline_goahead(p):  # T05
    h = p["hooks"]
    if not has(h, r"(explicit|clear|exact)[^.]{0,30}(go-?ahead|reply|yes|message)", r"ask hooks-waive",
               r"silence|question|hedg|maybe|bare yes"):
        return False
    i = h.find("ask hooks-waive")
    later = [m for m in re.finditer(r"hooks-waive", h[i + 15:]) if not h[i + 15:][max(0, m.start() - 4):m.start()].endswith("ask ")]
    return bool(later) and has(h, r"(after|only after|once|then)[^.]{0,120}hooks-waive|hooks-waive[^.]{0,80}(after|once)")


def l_old_rule(p):  # T06
    both = p["setup"] + p["phase_a"]
    if re.search(r"tell the user which install command", both, re.I):
        return False
    for m in re.finditer(r"[^.]*(don.t|do not|never) start building until a hook has fired[^.]*\.", both, re.I):
        if not has(m.group(0), r"waiv|go-?ahead|explicit|unless|without hooks"):
            return False
    return bool(p["setup"] and p["phase_a"])


def l_blockers_step(p):  # T07
    b, ps = p["blockers"], p["pos"]
    order = ps["blockers"] is not None and ps["stop"] is not None and ps["blockers"] < ps["stop"]
    return bool(order) and has(b, r"\b(one|single|1|a single|consolidated) (short |plain )?message",
                               r"(won.t|will not|doesn.t|does not|not|fails? to|can.t|cannot) (start|run|boot)|isn.t running|not running",
                               r"playwright", r"test runner|no tests|test framework|test command",
                               r"uncommitted|dirty|unstaged|not committed")


def l_blockers_fix(p):  # T08
    b = p["blockers"]
    return has(b, r"proposed fix|propose a fix|with a fix|each (one )?(with|comes with)|suggest[^.]{0,30}fix|fix for (each|every)",
               r"offer[^.]{0,60}(apply|fix|do it)|(apply|fix)[^.]{0,40}(for them|if they (agree|say yes))|offer to apply",
               r"bare list|just (a )?list|not a list|rather than (a )?(bare|list)|instead of (just )?listing|never (a )?bare|don.t just list")


def l_branch_offer(p):  # T09
    b, ps = p["blockers"], p["pos"]
    if not (ps["blockers"] is not None and ps["stop"] is not None and ps["blockers"] < ps["stop"]):
        return False
    return has(b, r"\bbranch", r"offer|ask|propose|suggest", r"--branch|git (switch|checkout)",
               r"no blockers|nothing (to report|is wrong)|none found|if there are none|skip|nothing blocks|all clear") \
        and not has(b, r"silently (create|switch)s?\b(?![^.]{0,20}(never|not|don.t))") \
        or (has(b, r"\bbranch", r"offer|ask|propose|suggest", r"--branch|git (switch|checkout)",
                r"no blockers|nothing (to report|is wrong)|none found|if there are none|skip|nothing blocks|all clear")
            and has(b, r"(never|don.t|do not|not)[^.]{0,30}(silently|without asking)"))


def l_branch_before_init(p):  # T17: the branch offer is made before init runs, and init --branch is the way it is applied
    st = p["steps"]
    i = p["pos"]["init"]
    if i is None:
        return False
    s = st[i]
    m = re.search(r"\$RATCHET init\b|`init\b", s)
    if not m:
        return False
    before = s[:m.start()]
    return has(before, r"\bbranch", r"offer|ask|propose|suggest", r"git status|dirty|uncommitted", r"default branch|main|master") \
        and "init --branch" in s


def l_no_branch_promise_late(p):  # T18: the blockers message must not promise init --branch (init only works once)
    b = p["blockers"]
    return "init --branch" not in b.replace("`init --branch` only works", "") and not has(b, r"\$RATCHET init --branch")


def l_restart_pauses(p):  # T19: restart end-turn goes through pause, then resume + doctor re-check
    h = p["hooks"]
    if not has(h, r"restart|new session"):
        return False
    m = re.search(r"pause\b", h)
    r = re.search(r"\bresume\b", h[m.start():]) if m else None
    e = re.search(r"end your turn", h[m.start():]) if m else None
    if not (m and r and e):
        return False
    tail = h[m.start():]
    return e.start() < r.start() and has(tail, r"doctor --json") and tail.rfind("doctor --json") > r.start()



def hook_bullets(p):
    return re.split(r"(?m)^\s*-\s+", p["hooks"])


def l_every_restart_pauses(p):  # T20: EVERY restart/silent bullet pauses before ending the turn
    bl = [b for b in hook_bullets(p) if re.match(r"(Hooks missing|Installed but silent)", b.strip(), re.I)]
    return len(bl) >= 2 and all(re.search(r"\bpause\b(?:(?!end your turn).)*end your turn", b, re.I | re.S) for b in bl)


def l_approve_only_clear_yes(p):  # T21: the approve-plan sentence itself is gated on a clear yes
    return has(p["approve"], r"approve-plan`?\s+only\s+(on|after|when|with)\s+(a\s+)?clear(ly)?\s+yes")


def l_branch_never_without_asking(p):  # T22: init step and blockers step both forbid creating/switching a branch unasked
    st, i = p["steps"], p["pos"]["init"]
    if i is None:
        return False
    never = r"(never|don.t|do not)[^.]{0,30}(create|switch)[^.]{0,30}branch[^.]{0,30}without asking"
    s = st[i]
    m = re.search(r"init --branch", s)
    return bool(m) and has(s[:m.start()], never) and has(s[:m.start()], r"if they (agree|say yes)") and has(p["blockers"], never)


def l_stop_pastes_plan_summary(p, engine_src):  # T23
    return has(p["stop"], r"(paste|show|include)[^.]{0,40}plan-summary") and \
        bool(re.search(r'add_parser\(\s*"plan-summary"', engine_src))


def l_no_recorder_guidance(p):  # T24: nothing has ever fired -> replies can't be recorded; say so, recommend install, no faking
    b = [x for x in hook_bullets(p) if re.search(r"no hook has (ever )?fired|never fired", x, re.I)]
    if len(b) != 1:
        return False
    b = b[0]
    return has(b, r"(cannot|can.t|can not) record", r"(skip|waive)[^.]{0,80}(cannot|can.t) be recorded",
               r"recommend[^.]{0,40}install", r"never (build|approve)[^.]{0,40}on their behalf",
               r"no hook support", r"can.t enforce anything", r"\bstop\b", r"one plain sentence", r"No human message recorded")


def l_goahead_strict(p):  # T25: a bare yes is explicitly NOT a go-ahead; hooks-waive gated on "only after that exact reply"
    h = p["hooks"]
    return has(h, r"bare yes is not a go-ahead") and not has(h, r"bare yes is a go-ahead") and \
        has(h, r"Only after that exact reply, run `\$RATCHET hooks-waive`")


def l_no_loop(p):  # T26: the no-recorder bullet forbids retrying the failing commands
    b = [x for x in hook_bullets(p) if re.search(r"no hook has (ever )?fired|never fired", x, re.I)]
    return len(b) == 1 and has(b[0], r"\bDo not loop on them\.") and not has(b[0], r"\bloop on them until")


def l_ask_prints_only(p):  # T27: says accurately which commands fail (C6)
    b = [x for x in hook_bullets(p) if re.search(r"no hook has (ever )?fired|never fired", x, re.I)]
    return len(b) == 1 and has(b[0], r"`hooks-waive` and plan approval[^.]{0,60}fail with .No human message recorded",
                               r"`ask hooks-waive` only prints the question") and not has(b[0], r"`ask hooks-waive`, `hooks-waive`")


def cp4_mutants(doc):
    """(label, predicate name, old text, new text): each mutation of SKILL.md must turn its predicate red."""
    return [
        ("silent bullet loses its pause", "T20", "and pause, end your turn", "and end your turn"),
        ("approve on any reply", "T21", "only on a clear yes", "on any reply"),
        ("branch auto-created (step 1)", "T22", "Never create or switch a branch without asking. If they agree, run", "Create the branch automatically. Run"),
        ("branch auto-created (blockers)", "T22", "never create or switch a branch without asking", "create it"),
        ("plan-summary paste deleted", "T23", "Paste the output of `$RATCHET plan-summary`, so", "So"),
        ("no-recorder guidance deleted", "T24", "Never build or approve on their behalf.", ""),
        ("bare yes becomes a go-ahead", "T25", "a bare yes is not a go-ahead", "a bare yes is a go-ahead"),
        ("hooks-waive no longer gated on exact reply", "T25", "Only after that exact reply, run `$RATCHET hooks-waive`", "Then run `$RATCHET hooks-waive`"),
        ("no-loop instruction inverted", "T26", "Do not loop on them.", "Loop on them until they work."),
        ("no-loop instruction deleted", "T26", " Do not loop on them.", ""),
        ("ask claimed to fail", "T27", "`ask hooks-waive` only prints the question", "`ask hooks-waive` fails"),
        ("no-recorder: no-hook-support stop deleted", "T24", "say plainly that Ratchet can't enforce anything there and stop", "carry on"),
    ]


def l_whole_plan(p):  # T10
    s = p["stop"]
    if has(s, r"4\s*[-–]\s*6 lines", ) or has(s, r"one-line sequence"):
        return False
    return has(s, r"whole plan|entire plan|full plan|every (step|checkpoint)|each (step|checkpoint)|all (the )?(steps|checkpoints)",
               r"\bchat\b|paste|in this message|in your reply|inline",
               r"title|name", r"done|finish|outcome|looks like|done_when", r"UI( review)?", r"risk")


def l_viewer_bonus(p):  # T11
    s = p["stop"]
    if re.search(r"Open the viewer, then reply", s, re.I):
        return False
    return has(s, r"viewer|VIEWER", r"bonus|optional|if you like|extra|nice to have|not required|no need to")


def l_clear_yes_approval(p):  # T12
    a = p["approve"]
    return has(a, r"approve-plan", r"clear(ly)? yes|explicit yes|unambiguous|clear (approval|go-?ahead)",
               r"question|hedg|maybe|i guess|not sure", r"answer", r"confirm") and \
        has(a, r"(do not|don.t|never|not|isn.t)\b[^.]{0,60}approve-plan|approve-plan[^.]{0,60}\bonly\b|\bonly\b[^.]{0,80}approve-plan")


def l_same_standard(p):  # T13
    h, a = p["hooks"], p["approve"]
    return (has(h, r"clear(ly)? yes|explicit|unambiguous", r"silence|question|hedg|maybe")
            and has(a, r"clear(ly)? yes|explicit|unambiguous", r"question|hedg|maybe")
            and not has(h + a, r"silence[^.]{0,30}(counts?|is|means) (as )?(consent|a yes|yes)"))


USER_CMD_RX = r"\b(you run|please run|you (should|can|need to) (run|type))\b|\btype\s+[`'\"*]|\btype\s+(yes|approve)\b"


def l_neutral_agent_led(p):  # T14
    new = p["hooks"] + p["blockers"] + p["stop"] + p["approve"]
    if not (p["hooks"].strip() and p["blockers"].strip() and p["stop"].strip() and p["approve"].strip()):
        return False
    if re.search(USER_CMD_RX, new, re.I) or "AskUserQuestion" in new:
        return False
    return len(providers_named(new)) != 1


def l_guard_existing(doc, p, engine_src):  # T15
    fm = re.match(r"---\n(.*?)\n---", doc, re.S)
    if not (fm and re.search(r"(?m)^name:\s*ratchet-orchestrator\s*$", fm.group(1))):
        return False
    a = p["phase_a"]
    idx = [a.find(x) for x in ("init", "plan-submit", "approve-plan")]
    if -1 in idx or idx != sorted(idx):
        return False
    if not (section(doc, "Phase B") and section(doc, "Phase C")):
        return False
    cp4_text = p["setup"] + a
    for cmd in ("doctor", "install-hooks", "hooks-waive", "plan-submit", "approve-plan"):
        if cmd not in cp4_text or not re.search(r'add_parser\(\s*"%s"' % re.escape(cmd), engine_src):
            return False
    return True


# The pre-change text (kept here so T16 can prove the lint is not vacuous).
OLD_DOC = """---
name: ratchet-orchestrator
description: x
---
## Setup (every run)

Hooks (installed with the plugin, or by `install.sh --host <cli>` / `$RATCHET install-hooks`): the **stop** hook
blocks you. **If `$RATCHET next` shows a `warning`**, run `$RATCHET doctor`. If hooks are not installed, offer to run
`install-hooks` (ask the user first); if they are installed but silent, tell the user to restart the agent CLI.
If the user would rather build without hooks, run `$RATCHET ask hooks-waive`. Otherwise don't start building until a hook has fired.

## Phase A: Plan (ends at human stop #1)

1. `$RATCHET init "<feature>"`. Add `--human-review each|none` if asked.
2. **In parallel**, spawn the app-scout and the planner.
3. If the planner returns `QUESTIONS:`, ask the user with AskUserQuestion (recall memory first per their preferences).
6. **Start the live viewer, then submit the plan.**
   - `$RATCHET plan-submit --open` validates the plan and prints `VIEWER: <URL>`.
7. **STOP #1.** Tell the user, in 4-6 lines: how many checkpoints, the one-line sequence, which ones get a UI review, and
   **the viewer link as a clickable URL on its own line**. Then: "Open the viewer, then reply *approve* or tell me what to change."
8. When the user replies:
   - Approves → `$RATCHET approve-plan`.
   - Asks for changes → re-spawn the planner.

## Phase B: Build

## Phase C: Review
"""


def cp4_orchestrator_setup_and_plan_review():
    print("CP4: orchestrator setup and plan review")
    path = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
    doc = path.read_text(encoding="utf-8") if path.exists() else ""
    engine_src = (ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py").read_text(encoding="utf-8")
    p = cp4_parts(doc)
    check("[CP4-T01] hooks check (doctor --json) sits after init and before plan-submit, done by the agent", l_hooks_after_init(p), p["pos"])
    check("[CP4-T02] missing hooks: agent offers install-hooks for the CLI in use, nothing installs before a yes", l_install_offer(p), p["hooks"][:200])
    check("[CP4-T03] restart note given with the install offer, and the run resumes after", l_restart_note(p))
    check("[CP4-T04] declined hooks: one sentence that gates would only be advice", l_decline_sentence(p))
    check("[CP4-T05] declined hooks: explicit go-ahead first, then hooks-waive; silence/question is not a go-ahead", l_decline_goahead(p))
    check("[CP4-T06] old build-only-after-hook rule is qualified by the waiver path; old install-command text gone", l_old_rule(p))
    check("[CP4-T07] one blockers message before STOP #1 listing all four blockers", l_blockers_step(p), p["pos"])
    check("[CP4-T08] every blocker comes with a proposed fix the agent offers to apply", l_blockers_fix(p))
    check("[CP4-T09] a new branch is offered (never silently) before STOP #1, with a clean skip", l_branch_offer(p))
    check("[CP4-T10] STOP #1 shows every plan step in chat; old 4-6 lines summary gone", l_whole_plan(p), p["stop"][:200])
    check("[CP4-T11] viewer link is a bonus; 'Open the viewer, then reply approve' is gone", l_viewer_bonus(p))
    check("[CP4-T12] approve-plan runs only on a clear yes; questions/hedges get an answer and a confirm request", l_clear_yes_approval(p))
    check("[CP4-T13] hooks go-ahead and plan approval share the clear-yes standard", l_same_standard(p))
    check("[CP4-T14] new text is agent-led and provider-neutral (no 'you run', no AskUserQuestion, not one CLI)", l_neutral_agent_led(p))
    check("[CP4-T17] the branch offer comes before init (step with init), applied via init --branch, nothing without asking", l_branch_before_init(p))
    check("[CP4-T18] the blockers message does not promise init --branch", l_no_branch_promise_late(p))
    check("[CP4-T19] restart: pause first, end turn, then resume and re-check doctor --json (Stop hook blocks ending in planning)", l_restart_pauses(p))
    check("[CP4-T20] every restart/silent bullet pauses before ending the turn", l_every_restart_pauses(p), p["hooks"][:200])
    check("[CP4-T21] the approve-plan sentence is gated on 'only on a clear yes'", l_approve_only_clear_yes(p), p["approve"][:200])
    check("[CP4-T22] init step and blockers step both say never create/switch a branch without asking", l_branch_never_without_asking(p))
    check("[CP4-T23] STOP #1 pastes plan-summary (a real subcommand)", l_stop_pastes_plan_summary(p, engine_src), p["stop"][:200])
    check("[CP4-T24] nothing ever fired: says replies can't be recorded, recommends install, never builds/approves for them, stops if no hook support",
          l_no_recorder_guidance(p))
    check("[CP4-T25] a bare yes is not a go-ahead; hooks-waive only after that exact reply", l_goahead_strict(p))
    check("[CP4-T26] no-recorder bullet says do not loop on the failing commands", l_no_loop(p))
    check("[CP4-T27] no-recorder bullet: hooks-waive/approve-plan fail; ask hooks-waive only prints", l_ask_prints_only(p))
    preds = {"T20": l_every_restart_pauses, "T21": l_approve_only_clear_yes, "T22": l_branch_never_without_asking,
             "T23": lambda q: l_stop_pastes_plan_summary(q, engine_src), "T24": l_no_recorder_guidance,
             "T25": l_goahead_strict, "T26": l_no_loop, "T27": l_ask_prints_only}
    for label, tid, o, n in cp4_mutants(doc):
        applied = o in doc
        red = applied and not preds[tid](cp4_parts(doc.replace(o, n)))
        check(f"[CP4-{tid}] teeth: mutation '{label}' turns the check red", red, "mutation text not found in SKILL.md" if not applied else "still green")
    late = dict(p, steps=[x.replace("init --branch", "init") for x in p["steps"]])
    late["pos"] = p["pos"]
    late_b = dict(p, blockers=p["blockers"] + " Offer a branch with `$RATCHET init --branch`.")
    no_pause = dict(p, hooks=re.sub(r"first run `\$RATCHET pause[^`]*`, then ", "", p["hooks"]))
    only_after = dict(p, steps=[re.sub(r"(?s)^(\s*\d+[.)]\s).*?(?=\$RATCHET init \")", r"\1", x) if re.search(r"\binit\b", x) else x for x in p["steps"]])
    check("[CP4-T17] teeth: branch offer only after init, or without init --branch, fails",
          not l_branch_before_init(late) and not l_branch_before_init(only_after))
    check("[CP4-T18] teeth: a blockers message offering init --branch fails", not l_no_branch_promise_late(late_b))
    check("[CP4-T19] teeth: restart end-turn without pause fails", not l_restart_pauses(no_pause))
    # planted violations: the neutral/agent-led lint must reject each of these
    planted = [q for q in [
        dict(p, hooks="Please run `ratchet.py install-hooks` yourself."),
        dict(p, blockers="Blockers: use AskUserQuestion to list them."),
        dict(p, stop="Type approve to continue. Only Claude Code is supported."),
        dict(p, approve="Then you run approve-plan."),
    ]]
    check("[CP4-T14] planted 'please run' / 'you run' / 'type approve' / AskUserQuestion / single-CLI text is rejected",
          all(not l_neutral_agent_led(q) for q in planted))
    check("[CP4-T15] guard: frontmatter, init->plan-submit->approve-plan order, Phase B/C, named subcommands exist in ratchet.py",
          l_guard_existing(doc, p, engine_src))
    check("[CP4-T15] guard has teeth: a doc naming a nonexistent subcommand is rejected",
          not l_guard_existing(doc, dict(p, setup=p["setup"] + " run `ratchet.py no-such-cmd`"), engine_src.replace('"doctor"', '"zzz"')))
    old = cp4_parts(OLD_DOC)
    stub_results = {"hooks-check": l_hooks_after_init(old) and l_install_offer(old), "blockers": l_blockers_step(old),
                    "whole-plan": l_whole_plan(old), "clear-yes": l_clear_yes_approval(old)}
    check("[CP4-T16] lint has teeth: the pre-change text fails the hooks-check, blockers, whole-plan and clear-yes checks",
          not any(stub_results.values()), stub_results)


# ---- end CP4 ----

SECTIONS = [("CP4", cp4_orchestrator_setup_and_plan_review)]


def main():
    global FAIL
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
