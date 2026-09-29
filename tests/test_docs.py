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


# ---- CP5 ----
# All skills and agents: controls, blocks, fallback, director/viewer/reviewer actions, no user-run commands.
import ast
import os
import subprocess
import shutil
import tempfile

ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
BUILDERS = ("ratchet-implementer", "ratchet-fixer", "ratchet-test-writer")
ENTRY_SPLIT = re.compile(r"(?m)^(?=\s*(?:[-*]\s|\d+[.)]\s|\||\*\*|#{3,6}\s))")
# user-directed command phrasing; every hit is reported as file:line
USER_CMD_DENY = [
    r"\byou\s+(?:can|should|must|need\s+to|will)\s+run\b|\byou\s+run\s+(?:`|\$|ratchet|this\b|it\b|that\b|the\s+command)", r"in\s+your\s+(?:own\s+)?(?:terminal|shell)",
    r"run\s+this\s+in", r"\btype\s+`", r"tell\s+the\s+user\s+which\s+install\s+command",
    r"(?:ask|tell|have|get|let)\s+the\s+user\s+(?:to\s+)?(?:run|type|execute)\b",
    r"the\s+user\s+(?:should|must|needs?\s+to|can|has\s+to)\s+(?:run|type|execute)\b", r"\bplease\s+run\b",
    r"\byourself\b[^.\n]{0,20}`|run\s+`[^`]+`\s+yourself",
]


def cp5_docs():
    """[(relative name, text)] for every skill and agent, read-only."""
    out = []
    for f in sorted((ROOT / "skills").glob("*/SKILL.md")) + sorted((ROOT / "agents").glob("*.md")):
        out.append((str(f.relative_to(ROOT)), f.read_text(encoding="utf-8")))
    return out


def hsection(text, kw):
    """Body of the first markdown heading (any level) whose title matches kw, up to the next heading of the same or higher level."""
    heads = list(re.finditer(r"(?m)^(#{1,6})\s+(.*)$", text))
    for i, m in enumerate(heads):
        if re.search(kw, m.group(2), re.I):
            end = len(text)
            for n in heads[i + 1:]:
                if len(n.group(1)) <= len(m.group(1)):
                    end = n.start()
                    break
            return text[m.end():end]
    return ""


def entries(text):
    return [e.strip() for e in ENTRY_SPLIT.split(text) if e.strip()]


def units(text):
    """Paragraphs and list items."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        out += [u.strip() for u in ENTRY_SPLIT.split(para) if u.strip()]
    return out


KEYS = {"pause": r"\bpause\b|hold on", "resume": r"\bresume\b|\bcontinue\b|carry on|keep going",
        "cancel": r"\bcancel\b|abandon", "status": r"\bstatus\b|where things stand",
        "checkin": r"check[- ]?in|only\s.{0,25}\bend\b|set-review"}


def control_entries(controls):
    """{key: entry text}: each entry is classified by the earliest control keyword in its first 110 characters."""
    found = {}
    for e in entries(controls):
        head = e[:110]
        pos = {k: m.start() for k, rx in KEYS.items() for m in [re.search(rx, head, re.I)] if m}
        if pos:
            k = min(pos, key=pos.get)
            found.setdefault(k, e)
    return found


def engine_commands():
    r = subprocess.run([sys.executable, str(ENGINE), "--help"], capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir())
    m = re.search(r"\{([a-z][^}]*)\}", r.stdout)
    return set(m.group(1).split(",")) if m else set()


def named_commands(text):
    return re.findall(r"`[^`\n]*?(?:\$RATCHET|ratchet\.py)\s+(?:--root\s+\S+\s+)?([a-z][a-z-]*)", text)


def bt(text, rx):
    """A backticked snippet matching rx."""
    return any(re.search(rx, m, re.I) for m in re.findall(r"`([^`\n]+)`", text))


NEG = r"(?:never|not|no|don't|do\s+not|without|neither)"


def p_controls_five(c):
    return len(control_entries(c)) == 5


def p_pause_resume(c, cmds):
    e = control_entries(c)
    a, b = e.get("pause", ""), e.get("resume", "")
    return (bt(a, r"\bpause\b") and has(a, r"reason") and bt(b, r"\bresume\b") and bt(b, r"ask\s+claim|--claim")
            and has(b, r"\bclaim") and has(b, r"(this|current|new|your)\s+(session|chat|conversation)")
            and {"pause", "resume"} <= cmds)


def p_cancel_status(c):
    e = control_entries(c)
    a, b = e.get("cancel", ""), e.get("status", "")
    return (bt(a, r"ask\s+cancel") and bt(a, r"\$RATCHET\s+cancel|ratchet\.py\s+cancel|^cancel\b|\bcancel\b(?!\s*$)")
            and has(a, r"confirm|clear\s+yes|check\s+with\s+the\s+user|ask\s+(the\s+user\s+)?first|are\s+you\s+sure")
            and bt(b, r"\bstatus\b") and has(b, r"plain") and has(b, r"short|summary|brief")
            and has(b, NEG + r"[^.\n]{0,50}(raw\s+)?JSON"))


def p_checkin(c, help_txt):
    a = control_entries(c).get("checkin", "")
    return bt(a, r"set-review\s+end\b") and bt(a, r"ask\s+set-review") and re.search(r"\bend\b", help_txt) is not None


def p_cmds_real(c, cmds):
    named = set(named_commands(c))
    return len(named) >= 5 and named <= cmds


def blocks_text(doc):
    return [u for u in units(doc) if re.search(r"\bblocked\b|won't\s+converge|stuck", u, re.I)]


def p_blocks(doc):
    return any(has(u, r"\bquot(e|ed|ing)\b", r"\blettered\b|\bA\b[^.]{0,20}\bB\b", r"\boptions?\b",
                   r"\b(2|two)\b[^.]{0,8}(3|three)|two or three",
                   r"wait(s|ing)?\s+(for|until)|end\s+your\s+turn|until\s+(they|the\s+user)\s+(answer|repl|choos|decid|pick)")
               for u in blocks_text(doc))


def p_progress(doc):
    return any(has(u, r"\bprogress\b", r"\bplain\b", r"\b(done|finished|completed)\b", r"\b(next|left to do|still to do)\b",
                   NEG + r"[^.\n]{0,60}JSON", r"jargon|gate\s+names?|internal\s+(state|terms)")
               for u in units(doc))


def fb_text(doc):
    return hsection(doc, r"fallback|without\s+sub-?agents?|no\s+sub-?agents?|hosts?\s+without")


def p_fallback(f):
    return has(f, r"(without|no|lack|doesn't\s+(have|support)|cannot|can't|don't\s+(have|support))\s+(any\s+)?sub-?agents?",
               r"one\s+role\s+at\s+a\s+time",
               r"(less|not\s+as|reduced|weaker)\s+(\w+\s+){0,3}independen|independen\w*\s+(is\s+)?(less|reduced|weaker|lower)")


def p_fallback_bar(f):
    return has(f, r"still\s+(proceed|run|go|work|build|apply|enforce)|keeps?\s+(going|the\s+bar)|proceeds?",
               NEG + r"\s+(skip|lower|weaken|drop|relax)[^.\n]{0,50}(gate|bar|check|standard)",
               r"(record|log|tell|say|note|disclos|mention|state|flag)\w*[^.\n]{0,60}(user|evidence|log|run\s+notes|plain)")


def dir_text(doc):
    return hsection(doc, r"director")


def p_director_convo(d):
    return has(d, r"(when|if)\s+(the\s+)?user\s+(asks?|wants|says|requests?)|on\s+request|when\s+asked",
               r"director-enroll|director-sign|\benroll",
               r"plain\s+(questions?|words)|ask\w*\s+(the\s+user|them)",
               NEG + r"[^.\n]{0,60}(hand|give|paste|tell)\w*[^.\n]{0,40}command",
               NEG + r"[^.\n]{0,90}(builder|lane)\s+session")


def p_lane_forbid(d):
    for u in re.split(r"(?<=[.!?])\s+", d):
        if re.search(r"lane\s+orchestrator", u, re.I) and has(u, NEG, r"\bkey\b", r"\bsign", r"enroll", r"RATCHET_ROLE"):
            return True
    return False


def p_viewer_reviewer(doc):
    req = r"(when|if|whenever)\s+(the\s+)?(user|they)\s+(asks?|wants|requests?|says)|on\s+request|when\s+asked|if\s+asked"
    us = units(doc)
    v = any(has(u, r"\bview\b|viewer", r"link", r"share|give|send|paste|show", req) and has(u, NEG + r"[^.\n]{0,60}(builder|lane|sub-?agent)\s+(session|context)?|builder")
            for u in us)
    r = any(has(u, r"reviewer-status", r"plain|own\s+words", req) and has(u, r"builder") for u in us)
    return v and r


def p_hooks_offer(doc):
    us = [u for u in units(doc) if re.search(r"hooks?", u, re.I) and re.search(r"install", u, re.I)]
    offers = any(re.search(r"offer\w*[^.\n]{0,80}install|install[^.\n]{0,40}(offer|if\s+they\s+agree|with\s+their\s+ok)", u, re.I) for u in us)
    bad = re.search(r"tell\s+the\s+user\s+(which|what|the)[^.\n]{0,40}(command|install)|(ask|tell)\s+the\s+user\s+to\s+(run|install|type)", doc, re.I)
    return offers and not bad


def p_questions_chat(doc):
    return any(has(u, r"QUESTIONS", r"ask\s+the\s+user", r"directly|in\s+chat|right\s+here|in\s+the\s+(conversation|chat)") for u in units(doc))


def user_cmd_hits(name, text):
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(re.search(rx, line, re.I) for rx in USER_CMD_DENY):
            hits.append(f"{name}:{i}")
    return hits


def neutrality_hits(name, text):
    out = []
    for u in units(text):
        if re.search(r"\bclaude\b(?![-.]\w)", u, re.I) and not re.search(r"codex|gemini|copilot|\bother\b|\banother\b|\bany\b|\bevery\b|whichever|host_provider", u, re.I):
            out.append(f"{name}: {u[:80]}")
    return out


def fm_parse(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    d = {}
    for line in m.group(1).splitlines():
        k = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if k:
            d[k.group(1)] = k.group(2).strip()
    return d


def cp5_frontmatter_problems(docs):
    bad = []
    for name, text in docs:
        fm = fm_parse(text)
        f = Path(name)
        if fm is None:
            bad.append(f"{name}: no frontmatter")
        elif name.startswith("skills"):
            if fm.get("name") != f.parent.name or not fm.get("description"):
                bad.append(f"{name}: name/description")
        else:
            if not re.fullmatch(r"ratchet-[a-z][a-z-]*", fm.get("name", "")) or fm.get("name") != f.stem or not fm.get("description") or not fm.get("tools"):
                bad.append(f"{name}: name/description/tools")
            if not re.search(r"\bReturn\b[^\n]{0,20}(≤|<=|up to|at most|under)\s*\d+|(≤|<=)\s*\d+\s+lines|return\s+only\s+an?\s+\d+-line|\bverdict\b", text, re.I):
                bad.append(f"{name}: no Return bound")
    return bad


def stdlib_only(path):
    ok = {"re", "sys", "traceback", "pathlib", "ast", "os", "subprocess", "tempfile", "json", "shutil", "textwrap", "hashlib", "time"}
    bad = []
    for n in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
        bad += [m for m in mods if m.split(".")[0] not in ok]
    return bad


def tree_snapshot(d):
    """(path, mtime, size) of every file under d. Only ever pointed at a private temp copy, never the live .ratchet/."""
    d = Path(d)
    return sorted((str(f.relative_to(d)), f.stat().st_mtime_ns, f.stat().st_size) for f in d.rglob("*") if f.is_file())


def private_repo_copy(dest):
    """Copy of the repo without .git and .ratchet (hooks rewrite the live .ratchet/ concurrently)."""
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", ".ratchet", "__pycache__"))
    return Path(dest)


def tree_snapshot_detects_ratchet():
    with tempfile.TemporaryDirectory() as tmp:
        c = private_repo_copy(Path(tmp) / "r")
        b = tree_snapshot(c)
        (c / ".ratchet").mkdir()
        (c / ".ratchet" / "x.json").write_text("{}")
        return tree_snapshot(c) != b and (c / ".ratchet").exists()


def snip_pos(text, rx):
    """Offset of the first backticked snippet that fully matches rx, else -1."""
    for m in re.finditer(r"`([^`\n]+)`", text):
        if re.fullmatch(rx, m.group(1).strip()):
            return m.start()
    return -1


def p_yes_gated(text, ask_rx, act_rx):
    """ask snippet, then a 'clear yes', then the exact action snippet, in that order."""
    a, act = snip_pos(text, ask_rx), snip_pos(text, act_rx)
    y = [m.start() for m in re.finditer(r"clear\s+yes", text, re.I)]
    return a >= 0 and act > a and any(a < i < act for i in y)


def p_gated_cancel(c):
    return p_yes_gated(control_entries(c).get("cancel", ""), r"\$RATCHET ask cancel", r"\$RATCHET cancel")


def p_gated_setreview(c):
    return p_yes_gated(control_entries(c).get("checkin", ""), r"\$RATCHET ask set-review end", r"\$RATCHET set-review end")


def p_gated_resume(c):
    return p_yes_gated(control_entries(c).get("resume", ""), r"\$RATCHET ask claim", r"\$RATCHET resume --claim")


def p_two_step(c):
    return has(c, r"two-step", r"\$RATCHET ask <action>", r"only after their clear yes", r"hedge[^.\n]*bare[^.\n]*not a yes")


def p_approve_changes(text):
    m = re.search(r"(?m)^.*approve-changes.*$", hsection(text, r"hard rules"))
    if not m:
        return False
    t = m.group(0)
    a, act = snip_pos(t, r"\$RATCHET ask approve-changes"), snip_pos(t, r"\$RATCHET approve-changes")
    return 0 <= a < act and re.search(r"only\s+after\s+their\s+clear\s+yes", t[act:], re.I) is not None


def p_enrolled(c):
    return (has(c, r"director-enrolled lane[^.\n]*does not apply", r"chat\s+\"yes\"\s+does\s+not\s+count",
                r"`director-sign cancel`", r"`director-sign set-review`", r"`director-sign approve-changes`",
                r"`resume --claim`\s+is\s+not\s+available", r"do\s+not\s+run\s+the\s+ask/confirm\s+flow",
                r"do\s+not\s+attempt\s+the\s+command\s+without\s+a\s+token", r"--director-token")
            and bt(c, r"director-sign\s+cancel"))


def p_enrolled_wait(c):
    # controls paragraph: pause escape, exact-wait message, conditional waiting_on claim, plain resume; no unconditional claim
    para = re.search(r"(?m)^\*\*In a director-enrolled lane.*$", c)
    t = para.group(0) if para else c
    return (para is not None and has(t, r"say exactly what you are waiting for \(the action and why\)", r"\$RATCHET pause --reason",
                    r"Do not claim `waiting_on: director` unless `status --json` shows it",
                    r"Plain `\$RATCHET resume` \(no `--claim`\) needs no approval and still resumes a paused enrolled lane")
                and not re.search(r"end your turn[^.]*saying the lane is `waiting_on: director`", t, re.I))


def p_director_wait(c):
    d = hsection(c, r"running under a director")
    return has(d, r"says exactly what you are waiting for|say exactly what you are waiting for", r"only in the phases where the engine sets it",
               r"`waiting_on: null`")


def p_enrolled_bullet(orch):
    d = hsection(orch, r"running under a director")
    return has(d, r"cancel,\s+set-review,\s+approve-changes[^.\n]*need a director token", r"resume --claim`?\s+is not available")


def cp5_docs_lint():
    docs = cp5_docs()
    by = dict(docs)
    orch = by.get("skills/ratchet-orchestrator/SKILL.md", "")
    controls = hsection(orch, r"control")
    cmds = engine_commands()
    sub = subprocess.run([sys.executable, str(ENGINE), "set-review", "--help"], capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir())
    setrev_help = sub.stdout

    def teeth(tid, label, pred_ok, pred_mut_red):
        check(f"[CP5-{tid}] teeth: {label}", pred_ok and pred_mut_red)

    # T01
    ents = control_entries(controls)
    check("[CP5-T01] orchestrator has a controls section with pause, resume/continue, cancel, status and 'only check in at the end' as five separate entries",
          bool(controls) and p_controls_five(controls), f"section found={bool(controls)}, entries={sorted(ents)}")
    check("[CP5-T01] teeth: merging two entries into one leaves fewer than five",
          p_controls_five(controls) and not p_controls_five(re.sub(r"(?m)^\s*(?:[-*]\s|\d+[.)]\s)\**\s*(resume|continue)", "", controls, count=1, flags=re.I)))
    # T02
    check("[CP5-T02] pause names `pause` with a reason; resume names `resume`, `ask claim`, claims the run for this session; both are engine commands",
          p_pause_resume(controls, cmds), {k: v[:80] for k, v in ents.items() if k in ("pause", "resume")})
    check("[CP5-T02] teeth: dropping the claim wording or the reason turns it red",
          p_pause_resume(controls, cmds) and not p_pause_resume(re.sub(r"(?i)claim\w*", "move", controls), cmds)
          and not p_pause_resume(re.sub(r"(?i)reason", "note", controls), cmds))
    # T03
    check("[CP5-T03] cancel: `ask cancel` then cancel, after a clear yes; status: short plain summary, never raw JSON",
          p_cancel_status(controls), {k: v[:80] for k, v in ents.items() if k in ("cancel", "status")})
    check("[CP5-T03] teeth: telling the agent to show raw JSON, or cancel without confirming, is red",
          p_cancel_status(controls) and not p_cancel_status(re.sub(r"(?i)(never|not|don't|do not|no)([^.\n]{0,50})JSON", r"always\2JSON", controls))
          and not p_cancel_status(re.sub(r"(?i)confirm\w*|clear yes|check with the user|ask (the user )?first|are you sure", "", controls)))
    # T04
    check("[CP5-T04] 'only check in at the end' names `ask set-review` + `set-review end`, and the engine accepts `end`",
          p_checkin(controls, setrev_help), ents.get("checkin", "")[:120])
    check("[CP5-T04] teeth: `set-review none` (autonomous) instead of `end` is red",
          p_checkin(controls, setrev_help) and not p_checkin(re.sub(r"set-review\s+end\b", "set-review none", controls), setrev_help))
    # T05
    named = set(named_commands(controls))
    check("[CP5-T05] every `$RATCHET <cmd>` named in the controls section is a real subcommand (and at least five are named)",
          p_cmds_real(controls, cmds), f"named={sorted(named)} unknown={sorted(named - cmds)}")
    check("[CP5-T05] teeth: a renamed/nonexistent command in the section is red",
          p_cmds_real(controls, cmds) and not p_cmds_real(controls + "\n- `$RATCHET frobnicate`", cmds))
    # T06
    check("[CP5-T06] blocked run: quote the finding, offer 2-3 lettered options, wait for the answer", p_blocks(orch),
          [u[:100] for u in blocks_text(orch)][:3])
    check("[CP5-T06] teeth: unlettered options / no quoting / no waiting turns it red",
          p_blocks(orch) and all(not p_blocks(re.sub(rx, "", orch, flags=re.I)) for rx in (r"lettered|\bA\b[^.]{0,20}\bB\b", r"\bquot\w*", r"wait\w*\s+(for|until)|end\s+your\s+turn")))
    # T07
    check("[CP5-T07] progress updates are plain (done and next), never raw JSON or gate jargon", p_progress(orch))
    check("[CP5-T07] teeth: 'paste raw JSON' wording is red",
          p_progress(orch) and not p_progress(re.sub(NEG + r"([^.\n]{0,60})JSON", r"always\1JSON", orch, flags=re.I)))
    # T08 / T09
    fb = fb_text(orch)
    check("[CP5-T08] fallback section for hosts without sub-agents: one role at a time, reviews less independent",
          bool(fb) and p_fallback(fb), f"fallback section found={bool(fb)}")
    check("[CP5-T08] teeth: 'all roles at once' / 'fully independent' is red",
          p_fallback(fb) and not p_fallback(re.sub(r"(?i)one role at a time", "all roles at once", fb))
          and not p_fallback(re.sub(r"(?i)less|reduced|weaker", "fully", fb)))
    check("[CP5-T09] fallback keeps the bar (no gate skipped) and records or tells the user about the substitution",
          bool(fb) and p_fallback_bar(fb))
    check("[CP5-T09] teeth: allowing a skipped gate, or staying silent, is red",
          p_fallback_bar(fb) and not p_fallback_bar(re.sub(r"(?i)\b(never|do not|don't|without|no)\b", "may", fb))
          and not p_fallback_bar(re.sub(r"(?i)record\w*|log\w*|tell\w*|disclos\w*|mention\w*|flag\w*|say\b|note\b|state\b", "ignore", fb)))
    # T10 / T11
    d = dir_text(orch)
    check("[CP5-T10] director setup is a conversation the agent leads: enrolls/signs when asked, asks plain questions, hands over no commands, never in a builder or lane session",
          bool(d) and p_director_convo(d))
    check("[CP5-T10] teeth: 'hand the user the command' or removing the builder/lane exclusion is red",
          p_director_convo(d) and not p_director_convo(re.sub(r"(?i)(builder|lane)\s+session", "any session", d))
          and not p_director_convo(re.sub(NEG + r"([^.\n]{0,60}(hand|give|paste|tell)\w*[^.\n]{0,40}command)", r"always\1", d, flags=re.I)))
    check("[CP5-T11] a lane orchestrator still never reads the key, signs, enrolls or sets RATCHET_ROLE", p_lane_forbid(d))
    check("[CP5-T11] teeth: turning that 'never' into permission is red",
          p_lane_forbid(d) and not p_lane_forbid(re.sub(r"(?i)(lane\s+orchestrator[^.]*?)\b(never|not)\b", r"\1may", d)))
    # T12
    check("[CP5-T12] viewer link and reviewer-status are things the agent does when the user asks (plain-words report), never in a builder session",
          p_viewer_reviewer(orch))
    check("[CP5-T12] teeth: 'ask the user to run reviewer-status' / dropping the builder exclusion is red",
          p_viewer_reviewer(orch) and not p_viewer_reviewer(re.sub(r"(?i)builder", "anyone", orch)))
    # T13
    bad13 = []
    for name in BUILDERS:
        t = by.get(f"agents/{name}.md", "")
        if not t:
            bad13.append(f"{name}: missing")
        if re.search(r"director-(enroll|sign|key)|reviewer-status|\bview\s+--serve|\bviewer\b|RATCHET_ROLE", t, re.I):
            bad13.append(name)
    check("[CP5-T13] implementer, fixer and test-writer carry no director, viewer or reviewer-status actions", not bad13, bad13)
    check("[CP5-T13] teeth: a builder agent told to run reviewer-status is detected",
          bool(re.search(r"director-(enroll|sign|key)|reviewer-status", by.get("agents/ratchet-fixer.md", "") + "\nRun `$RATCHET reviewer-status`.", re.I)))
    # T14
    banned14 = [n for n, t in docs if re.search(r"AskUserQuestion|recall[\s-]+memory", t, re.I)]
    check("[CP5-T14] 'AskUserQuestion' and 'recall memory' appear in no skill or agent", not banned14, banned14)
    check("[CP5-T14] planner QUESTIONS step says to ask the user directly in chat", p_questions_chat(orch))
    check("[CP5-T14] teeth: reintroducing 'recall memory' is caught",
          bool(re.search(r"recall[\s-]+memory", "ask (recall memory first)", re.I)) and not p_questions_chat("If the planner returns `QUESTIONS:`, ask the user in plain words (recall memory first)."))
    # T15 / T16
    hits = [h for n, t in docs for h in user_cmd_hits(n, t)]
    check("[CP5-T15] no skill or agent tells the user to run a command", not hits, hits[:6])
    with tempfile.TemporaryDirectory() as td:
        tf = Path(td) / "SKILL.md"
        tf.write_text(orch + "\nRun `ratchet.py status` in your terminal.\n", encoding="utf-8")
        planted = user_cmd_hits(str(tf), tf.read_text(encoding="utf-8"))
    variants = ["You run `ratchet.py status`.", "Type `approve` to go on.", "Tell the user which install command to use.",
                "Ask the user to run doctor.", "Please run `ratchet.py doctor`.", "Run this in your terminal."]
    check("[CP5-T16] the user-command lint reports a planted 'Run `ratchet.py status` in your terminal' with file:line",
          len(planted) == 1 and re.search(r"SKILL\.md:\d+$", planted[0]) is not None, planted)
    check("[CP5-T16] every denylist phrase variant is caught", all(user_cmd_hits("x", v) for v in variants),
          [v for v in variants if not user_cmd_hits("x", v)])
    # T17
    check("[CP5-T17] missing hooks: the agent offers to install; no 'tell the user which install command to run'",
          p_hooks_offer(orch))
    check("[CP5-T17] teeth: adding 'tell the user which install command to run' is red",
          p_hooks_offer(orch) and not p_hooks_offer(orch + "\nTell the user which install command to run.\n"))
    # T18
    n18 = [h for n, t in docs for h in neutrality_hits(n, t)]
    check("[CP5-T18] no paragraph names only Claude / Claude Code as the host or sub-agent mechanism", not n18, n18[:4])
    check("[CP5-T18] teeth: a Claude-only sentence is flagged, a multi-CLI one is not",
          bool(neutrality_hits("x", "Spawn sub-agents with the Claude Code Agent tool.")) and not neutrality_hits("x", "In Claude Code use the Agent tool; in other CLIs use theirs."))
    # T19
    bad19 = cp5_frontmatter_problems(docs)
    check("[CP5-T19] frontmatter valid: skills name==folder + description; agents ratchet-<role> + description + tools + Return bound",
          len(docs) >= 18 and not bad19, bad19 or len(docs))
    check("[CP5-T19] teeth: a skill whose name differs from its folder, or an agent without tools, is flagged",
          bool(cp5_frontmatter_problems([("skills/x/SKILL.md", "---\nname: y\ndescription: d\n---\nbody\n"), ("agents/ratchet-a.md", "---\nname: ratchet-a\ndescription: d\n---\nReturn ≤5 lines\n")])))
    # T21-T27: safety wording of the controls (yes gate, exact commands, enrolled lanes)
    rx = lambda a, b: (lambda t: re.sub(a, b, t))
    check("[CP5-T21] cancel: `ask cancel`, then a clear yes, then exactly `$RATCHET cancel`", p_gated_cancel(controls), ents.get("cancel", "")[:120])
    check("[CP5-T21] teeth: cancel without the clear-yes gate, or cancel before asking, is red",
          p_gated_cancel(controls) and not p_gated_cancel(controls.replace("only after a clear yes", "right away"))
          and not p_gated_cancel(controls.replace("`$RATCHET ask cancel`", "`$RATCHET cancel`").replace("Ask first: ", "")))
    check("[CP5-T22] 'only at the end': `ask set-review end`, then a clear yes, then exactly `$RATCHET set-review end`", p_gated_setreview(controls), ents.get("checkin", "")[:120])
    check("[CP5-T22] teeth: dropping the yes gate, running `set-review none|each` instead of end, or `ask set-review` with no mode is red",
          p_gated_setreview(controls)
          and not p_gated_setreview(controls.replace("After a clear yes, run", "Right away, run"))
          and not p_gated_setreview(controls.replace("run `$RATCHET set-review end`", "run `$RATCHET set-review none`"))
          and not p_gated_setreview(controls.replace("run `$RATCHET set-review end`", "run `$RATCHET set-review each`"))
          and not p_gated_setreview(controls.replace("`$RATCHET ask set-review end`", "`$RATCHET ask set-review`")))
    check("[CP5-T23] resume: `ask claim`, then a clear yes, then exactly `$RATCHET resume --claim`", p_gated_resume(controls), ents.get("resume", "")[:120])
    check("[CP5-T23] teeth: `resume --force`, or resume without the yes gate, is red",
          p_gated_resume(controls) and not p_gated_resume(controls.replace("`$RATCHET resume --claim`", "`$RATCHET resume --force`"))
          and not p_gated_resume(controls.replace("After a clear yes, run", "Run")))
    check("[CP5-T24] the two-step flow is defined: ask first, act only after a clear yes, a hedge or bare ok is not a yes", p_two_step(controls))
    check("[CP5-T24] teeth: removing the hedge sentence or the yes condition is red",
          p_two_step(controls) and not p_two_step(controls.replace('A hedge or a bare "ok" is not a yes.', ""))
          and not p_two_step(controls.replace("only after their clear yes", "at once")))
    check("[CP5-T25] approve-changes rule: `ask approve-changes`, then a clear yes, then `$RATCHET approve-changes`", p_approve_changes(orch))
    check("[CP5-T25] teeth: deleting the rule, or running it without asking, is red",
          p_approve_changes(orch) and not p_approve_changes(re.sub(r"(?m)^.*approve-changes.*$", "", orch))
          and not p_approve_changes(orch.replace("`$RATCHET ask approve-changes`", "`$RATCHET approve-changes`"))
          and not p_approve_changes(orch.replace("only after their clear yes", "at once")))
    check("[CP5-T26] controls say that in a director-enrolled lane chat yes does not count, name the director-sign actions, "
          "say resume --claim is unavailable, forbid the ask flow", p_enrolled(controls))
    check("[CP5-T26] teeth: deleting the enrolled-lane paragraph, or any of its load-bearing claims, is red",
          p_enrolled(controls)
          and not p_enrolled(re.sub(r"(?m)^\*\*In a director-enrolled lane.*$", "", controls))
          and not p_enrolled(controls.replace("`resume --claim` is not available", "`resume --claim` works"))
          and not p_enrolled(controls.replace("`director-sign approve-changes`", "`director-sign x`"))
          and not p_enrolled(controls.replace("do not run the ask/confirm flow", "run the ask/confirm flow"))
          )
    check("[CP5-T29] enrolled lane: final message names the action and why; waiting_on: director is not claimed unconditionally; plain resume still works",
          p_enrolled_wait(controls) and p_director_wait(orch))
    check("[CP5-T29] teeth: unconditional waiting_on claim, dropped final-message rule, or dropped plain-resume is red",
          p_enrolled_wait(controls) and p_director_wait(orch)
          and not p_director_wait(orch.replace("only in the phases where the engine sets it", "in every phase"))
          and not p_enrolled_wait(controls.replace("Do not claim `waiting_on: director` unless `status --json` shows it", "You are `waiting_on: director`"))
          and not p_enrolled_wait(controls.replace("say exactly what you are waiting for (the action and why)", "say you are done"))
          and not p_enrolled_wait(controls.replace("Plain `$RATCHET resume` (no `--claim`) needs no approval and still resumes", "Resume is unavailable for"))
          and not p_enrolled_wait(controls.replace("`$RATCHET pause --reason", "`$RATCHET stop --reason"))
          and not p_enrolled_wait(controls.replace("Tell the user in one line", "End your turn saying the lane is `waiting_on: director`. Tell the user in one line")))
    check("[CP5-T27] the director section lists cancel, set-review, approve-changes and says resume --claim is unavailable", p_enrolled_bullet(orch))
    check("[CP5-T27] teeth: reverting to the old three-item list is red",
          p_enrolled_bullet(orch) and not p_enrolled_bullet(orch.replace("cancel, set-review, approve-changes and the hooks waiver", "nothing else")))
    # T20
    if os.environ.get("RATCHET_DOCS_NESTED"):
        check("[CP5-T20] standalone run (nested run: skipped to avoid recursion)", True)
        return
    me = Path(__file__).resolve()
    env = dict(os.environ, RATCHET_DOCS_NESTED="1")
    with tempfile.TemporaryDirectory() as tmp:
        copy = private_repo_copy(Path(tmp) / "repo")
        before = tree_snapshot(copy)
        with tempfile.TemporaryDirectory() as cwd:
            r = subprocess.run([sys.executable, str(copy / "tests" / "test_docs.py")], capture_output=True, text=True,
                               timeout=600, cwd=cwd, env=env)
        after = tree_snapshot(copy)
        (copy / "tests" / "zz_probe").write_text("x")
        probe_seen = tree_snapshot(copy) != after
        made_ratchet = (copy / ".ratchet").exists()
    ids_missing = [f"T{n:02d}" for n in range(1, 20) if f"[CP5-T{n:02d}]" not in r.stdout]
    consistent = (r.returncode == 0) == ("✗" not in r.stdout) and re.search(r"\d+ passed, \d+ failed", r.stdout) is not None
    check("[CP5-T20] runs from another cwd, prints a line for every CP5 id, exit code matches the failures shown, and it exits 0 on current docs",
          not ids_missing and consistent and r.returncode == 0, f"rc={r.returncode} missing={ids_missing}")
    src = me.read_text(encoding="utf-8")
    forced = {}
    for label, val in (("fail", "False"), ("pass", "True")):
        code = src.replace("ROOT = Path(__file__).resolve().parents[1]", f"ROOT = Path({str(ROOT)!r})")
        code = re.sub(r"(?m)^SECTIONS = \[.*\]$", f"SECTIONS = [('X', lambda: check('forced', {val}))]", code)
        with tempfile.TemporaryDirectory() as cwd:
            f = Path(cwd) / "t.py"
            f.write_text(code, encoding="utf-8")
            forced[label] = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, timeout=120, cwd=cwd, env=env).returncode
    check("[CP5-T20] exit code is non-zero when a check fails and zero when all pass", forced == {"fail": 1, "pass": 0}, forced)
    check("[CP5-T20] imports only the stdlib and writes nothing under the repo's .ratchet/", not stdlib_only(me) and after == before and not made_ratchet,
          stdlib_only(me))
    check("[CP5-T28] the write check runs on a private temp copy and never reads the live .ratchet/, so hook writes cannot flip it",
          "ROOT" not in src.split("def tree_snapshot")[1].split("def private_repo_copy")[0] and ".ratchet" in src.split("def private_repo_copy")[1].split("def tree_snapshot_detects")[0] and probe_seen)
    check("[CP5-T28] teeth: a write inside the copy, or a .ratchet/ created in it, is detected; a change to the live .ratchet/ is not looked at",
          probe_seen and tree_snapshot_detects_ratchet())


# ---- end CP5 ----

SECTIONS = [("CP4", cp4_orchestrator_setup_and_plan_review), ("CP5", cp5_docs_lint)]


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
