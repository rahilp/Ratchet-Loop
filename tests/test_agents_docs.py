#!/usr/bin/env python3
"""
CP8 tests: newcomer guide, change one role and undo it, plus the CP1 to CP7 follow-ups.
   python3 tests/test_agents_docs.py

A developer who reads the README can change which provider and model does one job (one `agents` line, in the
machine-wide file for every project or in one project's file), check it with the read-only `ratchet.py models`, and go
back to the defaults. This file proves the guide is true: it finds the new README section, runs the section's own
copyable `ratchet.py` commands against a throwaway project and a private Ratchet home (never the real ~/.config/ratchet),
and checks the claims the section makes against what the commands print. It also pins the small follow-ups the
director carried over from CP1 to CP7 (a dead f-string, `inherit` handled the same in every case and path, a
provider's company read from its preset before its model, a UI lens choice that names a provider that is not a runner,
reviewer-status judging a chosen-model mark the way `reviewers` does, an out-of-credits mark with no model, stale
docstrings and docs, and a wrong-shaped `host_models` that must never crash a command).

Design assumptions made where the production text or API does not exist yet (each is stated again at its case):
  * the new README section is a heading (any level) whose title names the agent/model/role setting and a verb such as
    change, choose, pick, set or configure; it ends at the next heading of the same or a higher level; it is at most 60
    lines; its copyable commands are fenced code lines holding `ratchet.py ...` (a `python3 path/ratchet.py` prefix is
    fine); a `<placeholder>` in a command is filled by this test (role -> implementer, model -> opus);
  * the section shows the implementer role: a global command (`config --global --set agents.implementer=...`), a project
    command (the same without `--global`), the read-only check `ratchet.py models` and one removal command per level
    (`config --unset agents.implementer`, `config --global --unset agents.implementer`);
  * `models` prints, for a sub-agent role, `  ratchet-implementer   asked X; uses Y; from LEVEL` (the second block);
  * the command table row of `ratchet.py models` names the requested-versus-real view and `--json` (the only extra form
    CP4 shipped; there is no per-role form), and the `config` row names the `agents` key;
  * the rule for provider/model values is stated the same way wherever it appears: the span right after "the model part
    can't be checked offline" says the unknown model loads, `models` marks it not verified or not offered, and it is
    refused where real work would launch (naming at least three of brief, launch guard, reviewers, plan-submit, UI
    review, lanes-start, config --set);
  * every repair command (`config --unset`, `config --global --unset`, `config --global --set`) exits 0 whichever of
    the two files holds the bad provider part or the wrong-shaped host_models (the documented "always work" rule);
  * `inherit` in any case (INHERIT, Inherit, anthropic/INHERIT, codex/inherit with a codex models list) means the
    session's own model everywhere: no start command, no cannot-run note, no "invalid", `{model}` empty;
  * a UI lens choice `openai/gpt-5` that resolves to a configured provider whose id is not a runner name is either
    launched on the matching runner (codex first, model gpt-5) or named plainly as "not a UI review provider" in both
    `models` and the plan (never dropped without a word).

Registration: this file parses the engine's source (T15 and T17), so it IS listed in
tests/fixtures/modularize_cp1/test-inventory.md (CP8-T11 re-derives that live).

Everything runs in-process through the engine's own entry points (standard 30): the engine module is loaded once per
process by tests/trust_helpers.py, each case builds a throwaway git project with a private per-user Ratchet home
(helpers from tests/test_config_layers.py, tests/test_agents_reviewers.py, tests/test_agents_launch.py and
tests/test_agents_session.py). tests/test_docs.py is not re-run here (it takes minutes); the command that runs this file
runs it next to it. No network, no real agent CLI, nothing is spent.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP8-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402,F401
import trust_helpers as th                              # noqa: E402,F401
import test_config_layers as cl                         # noqa: E402  (in-process helpers: project(), run(), ...)
import test_agents_reviewers as ar                      # noqa: E402  (world(), resolve(), models_row(), ui_world(), ...)
import test_agents_launch as al                         # noqa: E402  (run_review(), PARTWAY_STUB, read_proj(), ...)
import test_agents_session as se                        # noqa: E402  (fresh(), models(), about(), lane_world(), ...)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP8-[TX]\d+)\]")

README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
ADV_SKILL = ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md"
UI_SKILL = ROOT / "skills" / "ratchet-ui-compare" / "SKILL.md"
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
UI_REVIEW_PY = ROOT / "skills" / "ratchet-ui-compare" / "scripts" / "ui_review.py"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"
EM = chr(0x2014)


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ helpers

run, text = cl.run, cl.text


def rd(p):
    return Path(p).read_text()


def head_text(rel):
    """A repo file as the last commit has it, or None when git cannot say."""
    try:
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# ---- the new README section

HEAD_NOUN = re.compile(r"\b(models?|agents?|roles?)\b", re.I)
HEAD_VERB = re.compile(r"(chang|choos|pick|configur|setting|\bset\b|which|\bone\b|each)", re.I)


def headings(lines):
    """[(index, level, title)] of the markdown headings of `lines`, ignoring lines inside fenced code."""
    out, fence = [], False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        m = None if fence else re.match(r"^(#{1,6})\s+(.*\S)\s*$", ln)
        if m:
            out.append((i, len(m.group(1)), m.group(2)))
    return out


def find_sections(md):
    """[(title, lines)] of every README section whose heading names the agent/model/role setting and a verb."""
    lines = md.split("\n")
    hs = headings(lines)
    found = []
    for k, (i, level, title) in enumerate(hs):
        if HEAD_NOUN.search(title) and HEAD_VERB.search(title):
            end = next((j for j, lv, _ in hs[k + 1:] if lv <= level), len(lines))
            body = lines[i:end]
            while body and not body[-1].strip():
                body.pop()
            found.append((title, body))
    return found


def the_section():
    """(title, lines, text) of the one new README section, or (None, [], '') when it is missing (or not unique)."""
    secs = find_sections(rd(README))
    if len(secs) != 1:
        return None, [], ""
    return secs[0][0], secs[0][1], "\n".join(secs[0][1])


def fenced_blocks(sec_text):
    return re.findall(r"```[^\n]*\n(.*?)```", sec_text, re.S)


def fill(line):
    """Replace a <placeholder> in a README command with a real value."""
    def one(m):
        w = m.group(1).lower()
        if "/" in w:
            return "anthropic/sonnet"
        if "provider" in w:
            return "anthropic"
        if "role" in w:
            return "implementer"
        if "model" in w:
            return "opus"
        return m.group(0)
    return re.sub(r"<([^<>\n]+)>", one, line)


def readme_commands(sec_text):
    """The ratchet.py commands of the section's fenced blocks, in order: [{line, args, glob, set, unset, sub}]."""
    out = []
    for block in fenced_blocks(sec_text):
        for raw in block.splitlines():
            line = raw.strip()
            if line.startswith("$"):
                line = line[1:].strip()
            if "ratchet.py" not in line or line.startswith("#"):
                continue
            try:
                toks = shlex.split(fill(line), comments=True)
            except ValueError:
                continue
            idx = next((i for i, t in enumerate(toks) if t.endswith("ratchet.py")), None)
            if idx is None:
                continue
            args = toks[idx + 1:]
            sub = next((a for a in args if not a.startswith("-")), None)
            out.append({"line": line, "args": args, "glob": "--global" in args, "set": "--set" in args,
                        "unset": "--unset" in args, "sub": sub})
    return out


def set_pairs(cmd):
    """[(key, value)] of the --set arguments of a command."""
    pairs = []
    if cmd["set"]:
        for a in cmd["args"][cmd["args"].index("--set") + 1:]:
            if a.startswith("-"):
                break
            k, _, v = a.partition("=")
            pairs.append((k, v))
    return pairs


def impl_value(cmd, key="agents.implementer"):
    return next((v for k, v in set_pairs(cmd) if k == key), None)


def group_commands(sec_text):
    cmds = readme_commands(sec_text)
    g_set = [c for c in cmds if c["set"] and c["glob"] and impl_value(c) is not None]
    p_set = [c for c in cmds if c["set"] and not c["glob"] and impl_value(c) is not None]
    g_unset = [c for c in cmds if c["unset"] and c["glob"]]
    p_unset = [c for c in cmds if c["unset"] and not c["glob"]]
    checks = [c for c in cmds if c["sub"] == "models"]
    return cmds, g_set, p_set, g_unset, p_unset, checks


def impl_row(out):
    """(asked, uses, level) of the ratchet-implementer line in the second block of `models` text, or None."""
    m = re.search(r"^\s+ratchet-implementer\s+asked ([^;\n]*); uses ([^;\n]*); from (\w+)\s*$", out, re.M)
    return m.groups() if m else None


def files_of(d, env):
    g, p = cl.global_path(env), cl.project_path(d)
    return (json.loads(g.read_text()) if g.exists() else {}), (json.loads(p.read_text()) if p.exists() else {})


def world():
    return se.fresh({})


# ---- the engine's help, to know which options are real

_HELP = {}


def help_of(d, env, sub):
    if sub not in _HELP:
        r = run(d, env, sub, "--help")
        _HELP[sub] = (r.returncode, r.stdout + r.stderr)
    return _HELP[sub]


def options_in(s):
    return set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", s))


def unknown_options(d, env, sub, opts):
    rc, h = help_of(d, env, sub)
    return sorted(o for o in opts if rc != 0 or o not in h)


# ------------------------------------------------------------------------------------------------ cases

def t01_readme_has_a_short_section_with_a_global_example():
    secs = find_sections(rd(README))
    check("[CP8-T01] the README has exactly one section for changing one role (a heading naming the agent/model/role "
          "setting)", len(secs) == 1, [t for t, _ in secs])
    title, lines, sec = the_section()
    check("[CP8-T01] the section is short (60 lines or fewer)", bool(lines) and len(lines) <= 60, len(lines))
    cmds, g_set, p_set, g_unset, p_unset, checks = group_commands(sec)
    check("[CP8-T01] it holds a fenced command that sets the implementer role in the global file "
          "(ratchet.py config --global --set agents.implementer=<value>)",
          any(c["sub"] == "config" and impl_value(c) for c in g_set), [c["line"] for c in cmds])
    check("[CP8-T01] it names the global file's place (~/.config/ratchet/config.json or RATCHET_HOME)",
          "~/.config/ratchet/config.json" in sec or "RATCHET_HOME" in sec, sec[:200])


def t02_project_example_next_to_the_global_one():
    title, lines, sec = the_section()
    cmds, g_set, p_set, *_ = group_commands(sec)
    check("[CP8-T02] the section has a second fenced command that sets the same role for one project "
          "(ratchet.py config --set agents.implementer=<value>, no --global)",
          any(c["sub"] == "config" and impl_value(c) for c in p_set), [c["line"] for c in cmds])
    check("[CP8-T02] it names the project file (.ratchet/config.json)", ".ratchet/config.json" in sec, sec[:200])
    check("[CP8-T02] the global and the project examples are different commands, not the same text twice",
          bool(g_set) and bool(p_set) and g_set[0]["args"] != p_set[0]["args"]
          and g_set[0]["line"] != p_set[0]["line"], [g_set[:1], p_set[:1]])


def t03_readme_examples_really_work():
    title, lines, sec = the_section()
    cmds, g_set, p_set, g_unset, p_unset, checks = group_commands(sec)
    runnable = [c for c in cmds if not c["unset"]]
    check("[CP8-T03] the section has copyable ratchet.py commands to run (a global set, a project set, a models check)",
          bool(g_set) and bool(p_set) and bool(checks), [c["line"] for c in cmds])
    if not (g_set and p_set):
        return
    gval, pval = impl_value(g_set[0]), impl_value(p_set[0])
    d, env = world()
    results = []
    for c in runnable:
        results.append((c, run(d, env, *c["args"])))
    check("[CP8-T03] every copied command exits 0, in the order the README gives them",
          all(r.returncode == 0 for _, r in results), [(c["line"], r.returncode, text(r)[:150]) for c, r in results
                                                         if r.returncode != 0])
    gfile, pfile = files_of(d, env)
    check("[CP8-T03] the global file holds the role value the README shows",
          (gfile.get("agents") or {}).get("implementer") == gval, (gval, gfile))
    check("[CP8-T03] the project file holds the role value the README shows",
          (pfile.get("agents") or {}).get("implementer") == pval, (pval, pfile))
    shown = [r.stdout for c, r in results if c["sub"] == "models"]
    row = impl_row(shown[-1]) if shown else None
    check("[CP8-T03] the README's own read-only check names the chosen model and says it came from the project level",
          row is not None and pval in row[0] and row[2] == "project", (row, shown[-1][:300] if shown else None))
    d2, env2 = world()
    for c in [c for c in runnable if c["glob"]]:
        run(d2, env2, *c["args"])
    row2 = impl_row(run(d2, env2, "models").stdout)
    check("[CP8-T03] with only the global command run, models names the global value and says it came from the "
          "global level", row2 is not None and gval in row2[0] and row2[2] == "global", row2)


def t04_read_only_check_and_which_level_wins():
    title, lines, sec = the_section()
    flat = re.sub(r"\s+", " ", sec)
    check("[CP8-T04] the section names `ratchet.py models` as the check",
          re.search(r"ratchet\.py models", sec) is not None, sec[:200])
    check("[CP8-T04] it says that check is read-only (changes nothing, spends nothing, contacts no provider)",
          re.search(r"read-only|reads only|only reads|changes nothing|writes nothing|never (?:runs|contacts)", flat, re.I)
          is not None, flat[:300])
    order = (re.search(r"project.{0,100}?(?:beats|wins|overrides|takes (?:priority|precedence)|over).{0,100}?"
                       r"global.{0,140}?(?:built-?in|default)", flat, re.I)
             or re.search(r"built-?in.{0,40}?(?:then|,|<|->|→)\s*global.{0,40}?(?:then|,|<|->|→)\s*project",
                          flat, re.I))
    check("[CP8-T04] it says the project level beats the global level, which beats the built-in defaults",
          order is not None, flat[:300])
    check("[CP8-T04] it says models shows which level each value came from",
          re.search(r"(?:which|what) (?:settings )?level|came from|from (?:builtin|global|project)|each value", flat, re.I)
          is not None, flat[:300])


def t05_going_back_to_the_defaults_works():
    title, lines, sec = the_section()
    cmds, g_set, p_set, g_unset, p_unset, checks = group_commands(sec)
    check("[CP8-T05] the section names a removal command for the project level and one for the global level",
          bool(p_unset) and bool(g_unset), [c["line"] for c in cmds])
    if not (g_set and p_set and g_unset and p_unset):
        return
    gval, pval = impl_value(g_set[0]), impl_value(p_set[0])
    d0, env0 = world()
    default_row = impl_row(run(d0, env0, "models").stdout)
    d, env = world()
    for c in (g_set[0], p_set[0]):
        run(d, env, *c["args"])
    check("[CP8-T05] (setup) both choices are in place: the project one wins",
          (impl_row(run(d, env, "models").stdout) or ("", "", ""))[2] == "project", None)
    r1 = run(d, env, *p_unset[0]["args"])
    row1 = impl_row(run(d, env, "models").stdout)
    check("[CP8-T05] the project-level removal command exits 0 and the project falls back to the global value",
          r1.returncode == 0 and row1 is not None and gval in row1[0] and row1[2] == "global", (r1.returncode, row1, text(r1)[:200]))
    r2 = run(d, env, *g_unset[0]["args"])
    row2 = impl_row(run(d, env, "models").stdout)
    check("[CP8-T05] the global-level removal command exits 0 and models shows the built-in default for that role",
          r2.returncode == 0 and row2 is not None and row2 == default_row and row2[2] == "builtin", (r2.returncode, row2, default_row))
    gfile, pfile = files_of(d, env)
    check("[CP8-T05] the choice no longer appears in either file",
          "implementer" not in json.dumps((gfile.get("agents") or {})) and "implementer" not in json.dumps((pfile.get("agents") or {})),
          (gfile, pfile))


def t06_running_session_cannot_be_switched():
    title, lines, sec = the_section()
    flat = re.sub(r"\s+", " ", sec)
    check("[CP8-T06] the section names the orchestrator and the director", "orchestrator" in flat and "director" in flat, flat[:200])
    check("[CP8-T06] it says Ratchet cannot switch an already-running orchestrator or director session",
          re.search(r"can(?:no|['’])t switch|cannot switch|can not switch|unable to switch|does not switch|doesn['’]t switch",
                    flat, re.I) is not None
          and re.search(r"running|already|current session|live session", flat, re.I) is not None, flat[:300])
    check("[CP8-T06] it says the choice applies when the session is started (next session), not mid-session",
          re.search(r"(?:next|new) session|when (?:you|the session|it) (?:start|begin)|at start|started|start(?:s|ing)? (?:a|the) (?:new )?session",
                    flat, re.I) is not None, flat[:300])
    check("[CP8-T06] it points to the printed start command (models prints it)",
          re.search(r"start command|prints? (?:the )?(?:command|how)|claude --model|codex -m|the command", flat, re.I) is not None,
          flat[:300])


def t07_a_model_choice_never_approves_paid_use_or_weakens_review():
    title, lines, sec = the_section()
    flat = re.sub(r"\s+", " ", sec)
    check("[CP8-T07] it says choosing a model never approves paid per-use spending",
          re.search(r"never (?:approves?|grants?|gives?|counts? as).{0,80}?(?:paid|per-use|pay per use|spend)|"
                    r"(?:paid|per-use|pay per use|spending).{0,80}?(?:never|not) (?:approved|granted)|"
                    r"(?:does|do) not (?:approve|grant).{0,60}?(?:paid|per-use|pay)", flat, re.I) is not None, flat[:300])
    check("[CP8-T07] ... and that the exact reply `pay per use: <model>` is still needed",
          "pay per use:" in flat, flat[:300])
    check("[CP8-T07] it says a choice never removes cross-company review",
          re.search(r"never (?:removes?|drops?|weakens?|lowers?).{0,100}?(?:cross-company|another company|other compan|different company|outside review|independent)|"
                    r"(?:cross-company|another company|other compan|different company|outside review|independent).{0,100}?(?:stays|still|is kept|remains|never)",
                    flat, re.I) is not None, flat[:300])
    check("[CP8-T07] ... and never changes how many reviewers look",
          re.search(r"(?:never|not|nor|no).{0,80}?(?:changes?|alters?).{0,80}?how many reviewers|how many reviewers.{0,80}?(?:unchanged|never|not)|"
                    r"same number of reviewers|number of reviewers.{0,60}?(?:unchanged|never|not)", flat, re.I) is not None, flat[:300])


def _table_row(md, name):
    return next((ln for ln in md.split("\n") if ln.startswith(f"| `ratchet.py {name}")), "")


def t08_command_table_lists_the_new_options():
    md = rd(README)
    mrow, crow = _table_row(md, "models"), _table_row(md, "config")
    check("[CP8-T08] the command table has a `ratchet.py models` row and a `ratchet.py config` row", bool(mrow) and bool(crow), (mrow[:80], crow[:80]))
    check("[CP8-T08] the models row names the requested-versus-real view (what was asked for, what is really used)",
          re.search(r"(?:asked|requested)[^|]{0,60}(?:real|actually|used|differ)|asked for|requested versus|requested vs", mrow, re.I)
          is not None, mrow)
    check("[CP8-T08] the models row names the --json form", "--json" in mrow, mrow)
    check("[CP8-T08] the config row names the level choice (--global, project) and the agents key",
          "--global" in crow and re.search(r"project|\.ratchet/config\.json", crow) is not None
          and re.search(r"\bagents\b", crow) is not None, crow)
    d, env = world()
    for name, row in (("models", mrow), ("config", crow)):
        bad = unknown_options(d, env, name, options_in(row) - {"--help"})
        check(f"[CP8-T08] every option named in the {name} row is accepted by the real argument parser",
              bool(row) and not bad, bad)


def t09_readme_does_not_claim_more_than_the_code_does():
    title, lines, sec = the_section()
    flat = re.sub(r"\s+", " ", sec)
    sentences = re.split(r"(?<=[.;])\s+", flat)
    hosts = [s for s in sentences if all(h in s for h in ("Codex", "Gemini", "Copilot"))]
    check("[CP8-T09] it says Codex, Gemini and Copilot hosts cannot pick a model per sub-agent",
          any(re.search(r"can(?:no|['’])t|cannot|can not|unable|do(?:es)? not|no way", s, re.I)
              and re.search(r"sub-?agent", s, re.I) and re.search(r"model", s, re.I) for s in hosts), hosts or flat[:200])
    at = flat.find(hosts[0]) if hosts else -1
    near = flat[at:at + len(hosts[0]) + 300] if at >= 0 else ""
    check("[CP8-T09] ... and that this is reported, not faked (models says so plainly)",
          re.search(r"report|says? so|say so|shows?|plain|honest|not fak|never pretend|not pretend|no pretend|note", near, re.I)
          is not None, near)
    d, env = world()
    bad, seen = [], 0
    for m in re.finditer(r"ratchet\.py\s+(?:--root\s+\S+\s+)?([a-z][a-z-]*)([^`\n]*)", sec):
        sub, rest = m.group(1), m.group(2)
        rc, _h = help_of(d, env, sub)
        if rc != 0:
            if options_in(rest):
                bad.append(f"{sub}: not a ratchet.py command, yet options are given")
            continue
        seen += 1
        bad += [f"{sub} {o}" for o in unknown_options(d, env, sub, options_in(rest) - {"--help"})]
    check("[CP8-T09] every ratchet.py command and option the section names is accepted by the real parser (none rejected)",
          seen > 0 and not bad, (seen, bad))


def t10_new_file_is_listed_in_running_the_tests():
    readme = rd(README)
    section = readme.split("### Running the tests", 1)[-1]
    blocks = re.findall(r"```[^\n]*\n(.*?)```", section, re.S)
    home = [b for b in blocks if re.search(r"^python3 tests/test_agents_reviewers\.py$", b, re.M)]
    check("[CP8-T10] the 'Running the tests' block that lists the agents test files exists", len(home) == 1, len(home))
    lines = home[0].splitlines() if home else []
    mine = "python3 tests/test_agents_docs.py"
    check("[CP8-T10] that block lists python3 tests/test_agents_docs.py exactly once", lines.count(mine) == 1, lines.count(mine))
    i = lines.index(mine) if mine in lines else -2
    check("[CP8-T10] it sits in alphabetical place among the other test lines (after test_agents_config.py, before "
          "test_agents_launch.py)", 0 < i < len(lines) - 1 and lines[i - 1] < mine < lines[i + 1],
          lines[max(i - 1, 0):i + 2])
    earlier = ["test_config_layers.py", "test_config_scopes.py", "test_agents_config.py", "test_models_explain.py",
               "test_agents_reviewers.py", "test_agents_launch.py", "test_agents_session.py"]
    missing = [n for n in earlier if (HERE / n).exists() and f"python3 tests/{n}" not in lines]
    check("[CP8-T10] every other agent-settings test file from CP1 to CP7 that exists is listed too", not missing, missing)


def t11_inventory_matches_what_the_file_reads():
    src = Path(__file__).read_text()
    # The live pattern of tests/test_modularize_inventory.py, assembled from pieces so this check never matches itself.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(src) or src_pat.search(src))
    inv = INVENTORY.read_text()
    listed = "test_agents_docs.py" in inv
    check("[CP8-T11] the file is in the test inventory exactly when it reads engine source or loads it by spec "
          "(it parses ratchet.py's tree in T15 and T17, so it must be listed)", scans == listed, f"scans={scans} listed={listed}")
    entry = ""
    m = re.search(r"- `test_agents_docs\.py`[^\n]*(?:\n(?!- `|## |\n)[^\n]*)*", inv)
    if m:
        entry = m.group(0)
    check("[CP8-T11] when listed, its entry says what the file checks (README guide and engine source scan)",
          (not scans) or (len(entry) > 80 and re.search(r"README", entry) and re.search(r"ratchet\.py|engine", entry)), entry[:200])
    check("[CP8-T11] the inventory fixture and its checking test exist",
          INVENTORY.is_file() and (HERE / "test_modularize_inventory.py").is_file(), None)


def t12_skill_and_schemas_agree_with_the_readme():
    skill, schemas = rd(SKILL), rd(SCHEMAS)
    check("[CP8-T12] SKILL.md points to the one-line agents setting (config --set agents.<role>=..., --global for every project)",
          re.search(r"config --set agents\.\w+=", skill) is not None and "--global --set" in skill, None)
    check("[CP8-T12] SKILL.md points to the models check (read-only, what was asked for versus really used, which level)",
          re.search(r"\$RATCHET models", skill) is not None and re.search(r"asked for", skill) is not None
          and re.search(r"which level asked", skill) is not None, None)
    check("[CP8-T12] schemas.md documents the global file's place (~/.config/ratchet, the per-user folder)",
          "~/.config/ratchet" in schemas and "per-user folder" in schemas, None)
    check("[CP8-T12] schemas.md documents the layer order: shipped defaults, then the global file, then the project's file",
          re.search(r"shipped defaults, then that file, then the project'?s? `?\.ratchet/config\.json`?", schemas) is not None, None)
    check("[CP8-T12] schemas.md documents the agents key (agents.<role>, model or provider/model)",
          "`agents`" in schemas and "agents.<role>" in schemas and "provider/model" in schemas, None)


def t13_existing_doc_lint_is_untouched_and_no_em_dash():
    r = subprocess.run(["git", "status", "--porcelain", "--", "tests/test_docs.py"], cwd=ROOT, capture_output=True,
                       text=True, timeout=60)
    check("[CP8-T13] tests/test_docs.py is unchanged (it runs next to this file in the same command and must still pass)",
          (HERE / "test_docs.py").is_file() and r.returncode == 0 and r.stdout.strip() == "", (r.returncode, r.stdout, r.stderr[:100]))
    title, lines, sec = the_section()
    check("[CP8-T13] the new README section contains no em dash (when it exists; CP8-T01 needs it to)", EM not in sec, sec.count(EM))
    for rel in ("README.md", "skills/ratchet-orchestrator/SKILL.md", "skills/ratchet-orchestrator/references/schemas.md"):
        before = head_text(rel)
        now = rd(ROOT / rel)
        check(f"[CP8-T13] {rel} gains no em dash", before is None or now.count(EM) <= before.count(EM),
              (now.count(EM), None if before is None else before.count(EM)))


# ---- T14: the validity rule for provider/model values

PROVIDER_PARTS = [r"provider part", r"configured provider or a known company", r"set-time", r"at load",
                  r"repair commands", r"always work"]
WORK_POINTS = [r"`?brief`?", r"launch guard", r"`?reviewers`?", r"plan-submit", r"ui[_ ]review", r"lanes-start",
               r"config --set"]
QUALIFIER = re.compile(r"where work|work starts|before (?:any )?work|real work|set-time|launch|`config --set`|`brief`|"
                       r"plan-submit|lanes-start|`reviewers`|ui review", re.I)
UNKNOWN_MODEL = re.compile(r"\b(?:unknown|wrong|bad|invalid|unrecogni[sz]ed|misspelled)\b[^;]{0,80}\bmodels?\b", re.I)
REFUSED = re.compile(r"\b(?:is|are) (?:refused|rejected)\b|\brefus(?:ed|es)\b", re.I)


def unqualified_refusals(md):
    """Clauses that say an unknown model is refused without saying where (a model name cannot be checked offline)."""
    flat = re.sub(r"\s+", " ", md)
    return [c.strip()[:160] for c in re.split(r"(?<=[.;])\s+", flat)
            if UNKNOWN_MODEL.search(c) and REFUSED.search(c) and not QUALIFIER.search(c)]


def t14_validity_rule_is_stated_once_truthfully_everywhere():
    docs = {"README.md": rd(README), "orchestrator SKILL.md": rd(SKILL), "schemas.md": rd(SCHEMAS)}
    for name, md in docs.items():
        flat = re.sub(r"\s+", " ", md)
        spots = [m.start() for m in re.finditer(r"model part (?:can(?:no|['’])t|cannot) be checked offline", flat, re.I)]
        check(f"[CP8-T14] {name} states the rule's model part (the model part can't be checked offline)", bool(spots), None)
        for pos in spots[:3]:
            before, span = flat[max(0, pos - 700):pos + 60], flat[pos:pos + 700]
            miss = [p for p in PROVIDER_PARTS if not re.search(p, before, re.I)]
            check(f"[CP8-T14] {name}: the provider part must name a configured provider or a known company, refused at "
                  "set-time and at load, with the repair commands always working", not miss, miss)
            check(f"[CP8-T14] {name}: an unknown model loads", re.search(r"\bloads?\b", span, re.I) is not None, span[:200])
            check(f"[CP8-T14] {name}: the view marks it not verified or not offered",
                  re.search(r"not verified|not offered", span, re.I) is not None, span[:200])
            named = [w for w in WORK_POINTS if re.search(w, span, re.I)]
            check(f"[CP8-T14] {name}: it is refused where real work would launch (names at least three of brief, launch "
                  "guard, reviewers, plan-submit, UI review, lanes-start, config --set)",
                  len(named) >= 3 and re.search(r"work|launch", span, re.I) is not None, named)
        bad = unqualified_refusals(md)
        check(f"[CP8-T14] {name} never says an unknown model is refused without saying where (not 'refused at load')", not bad, bad)
    for name, path in (("adversarial-loop SKILL.md", ADV_SKILL), ("ui-compare SKILL.md", UI_SKILL)):
        md = rd(path)
        check(f"[CP8-T14] {name} never says an unknown model is refused at load", not unqualified_refusals(md), unqualified_refusals(md))
    adv = re.sub(r"\s+", " ", rd(ADV_SKILL))
    check("[CP8-T14] adversarial-loop SKILL.md says the model is only refused where work starts (plan-submit, reviewers) "
          "and is otherwise marked not verified", "plan-submit" in adv and "reviewers" in adv and "not verified" in adv, None)

    # the rule, exercised
    prov = [{"id": "codex", "preset": "codex", "models": ["gpt-5"]}]
    d, env = se.fresh({"providers": [dict(p) for p in prov]})
    r = run(d, env, "config", "--set", "agents.implementer=nosuchco/gpt-5")
    check("[CP8-T14] set-time: a provider part that is neither a configured provider nor a known company is refused, "
          "naming the key and the known names", r.returncode != 0 and "agents.implementer" in text(r) and "nosuchco" in text(r)
          and "anthropic" in text(r), text(r)[:300])
    repairs = (("config", "--unset", "agents.implementer"), ("config", "--global", "--unset", "agents.implementer"),
               ("config", "--global", "--set", "agents.implementer=opus"))
    for where in ("project", "global"):
        d, env = se.fresh({"providers": [dict(p) for p in prov]})
        bad = {"agents": {"implementer": "nosuchco/gpt-5"}}

        def put():
            if where == "project":
                cl.project_path(d).write_text(json.dumps(dict(bad, providers=prov)))
                cl.put_global(env, {})
            else:
                cl.project_path(d).write_text(json.dumps({"providers": prov}))
                cl.put_global(env, bad)
        put()
        for cmd in ("config", "models"):
            r = run(d, env, cmd)
            where_path = str(cl.project_path(d) if where == "project" else cl.global_path(env))
            check(f"[CP8-T14] load ({where} file): `{cmd}` refuses the unknown provider part, naming the file and agents.implementer",
                  r.returncode != 0 and "agents.implementer" in text(r) and (where_path in text(r)
                                                                              or os.path.realpath(where_path) in text(r)),
                  text(r)[:300])
        for rep in repairs:
            put()
            r = run(d, env, *rep)
            check(f"[CP8-T14] repair (bad provider in the {where} file): `{' '.join(rep)}` exits 0", r.returncode == 0, text(r)[:300])
    # the model part: it loads, the view says not offered, work start refuses it
    d, env = se.fresh({"providers": [dict(p) for p in prov], "agents": {"reviewer": "codex/model-typo"}})
    rc_cfg, rc_models = run(d, env, "config"), run(d, env, "models", "--json")
    row, _ = ar.models_row(d, env, "reviewer")
    check("[CP8-T14] model part: a model its provider does not list still loads (config and models exit 0) and the view "
          "says the provider does not offer it", rc_cfg.returncode == 0 and rc_models.returncode == 0
          and re.search(r"not offered|does not offer", ar.s(row.get("reason")) + ar.s(row.get("effective")), re.I) is not None, row)
    w = se.lane_world({"providers": [dict(p) for p in prov], "agents": {"orchestrator": "codex/model-typo"},
                       "lane_start_command": se.TEMPLATE_MODEL})
    r = w.start(name="req")
    check("[CP8-T14] model part: ... and is refused the moment real work would start (lanes-start), naming the model",
          r.returncode != 0 and "model-typo" in text(r), text(r)[-300:])
    r = run(*se.fresh({}), "config", "--set", "agents.implementer=sonet")
    check("[CP8-T14] model part: a misspelled bare model is refused by config --set, naming the key", r.returncode != 0
          and "agents.implementer" in text(r), text(r)[:300])


# ---- T15: the CP7 follow-ups

def _engine_tree():
    src = rd(ENGINE)
    return src, ast.parse(src)


def dead_fstrings(tree):
    """JoinedStr nodes with no placeholder (a plain string that carries an f prefix), not counting format specs."""
    specs = {id(n.format_spec) for n in ast.walk(tree) if isinstance(n, ast.FormattedValue) and n.format_spec is not None}
    return [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and id(n) not in specs
            and not any(isinstance(v, ast.FormattedValue) for v in n.values)]


NOTE_TEXT = "the model argument in the start command is empty"


def _models_of(d, env, val, role, host_note=""):
    cl.project_path(d).write_text(json.dumps(val))
    data, out, (rj, rt) = se.models(d, env)
    return data, out, rj, rt


def _inherit_ok(d, env, proj, role, tag):
    data, out, rj, rt = _models_of(d, env, proj, role)
    block, row = se.about(out, role), se.rows(data).get(role, {})
    bad = (se.START_ANYWHERE.search(block) or se.START_ANYWHERE.search(se.jtext(row)) or se.NOT_RUNNABLE.search(block)
           or se.NOT_RUNNABLE.search(se.jtext(row)) or "invalid" in (block + se.jtext(row)).lower() or row.get("start"))
    ok = rj.returncode == 0 and rt.returncode == 0 and not bad and "session's own model" in block
    check(f"[CP8-T15] ({tag}, {role}) means inherit: no start command, no cannot-run note, no 'invalid' (text and --json)",
          ok, block[:300])


def t15_cp7_follow_ups():
    src, tree = _engine_tree()
    dead = dead_fstrings(tree)
    holders = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and any(
        isinstance(v, ast.Constant) and NOTE_TEXT in str(v.value) for v in n.values)]
    plain = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and NOTE_TEXT in n.value]
    check("[CP8-T15] (1) the lanes-start empty-model note is a plain string, not an f-string without a placeholder",
          not [n for n in holders if n in dead] and (bool(plain) or bool(holders)), [n.lineno for n in dead])
    check("[CP8-T15] (1) the count of f-strings with no placeholder in ratchet.py went from 7 to at most 6",
          len(dead) <= 6, (len(dead), sorted(n.lineno for n in dead)))

    # (3) inherit is case-insensitive and the same on every path; (2) codex/inherit with a codex models list
    for host in ("claude", "codex", "gemini"):
        d, env = se.fresh({}, host=host)
        for val in ("inherit", "INHERIT", "Inherit", "anthropic/INHERIT"):
            for role in ("orchestrator", "director"):
                _inherit_ok(d, env, {"agents": {role: val}}, role, f"{host} host, {val}")
    for val in ("INHERIT", "Inherit", "anthropic/INHERIT", "inherit"):
        w = se.lane_world({"lane_start_command": se.TEMPLATE_MODEL, "agents": {"orchestrator": val}})
        r = w.start(name="req", js=True)
        start = se.lane_start_of(r, "a")
        check(f"[CP8-T15] (3) lanes-start with orchestrator = {val}: {{model}} is an empty word and nothing is refused",
              r.returncode == 0 and shlex.split(start or "") == ["mycli", "--model", "", "--cwd", w.wt("a")], (start, text(r)[-300:]))
    prov = [{"id": "codex", "preset": "codex", "models": ["gpt-5"]}]
    d, env = se.fresh({})
    for val in ("codex/inherit", "codex/INHERIT"):
        for role in ("orchestrator", "director"):
            _inherit_ok(d, env, {"providers": [dict(p) for p in prov], "agents": {role: val}}, role, f"codex models list, {val}")
        rc = run(d, env, "config")
        check(f"[CP8-T15] (2) {val} with a codex models list loads", rc.returncode == 0, text(rc)[:200])
        w = se.lane_world({"providers": [dict(p) for p in prov], "lane_start_command": se.TEMPLATE_MODEL,
                           "agents": {"orchestrator": val}})
        r = w.start(name="req", js=True)
        start = se.lane_start_of(r, "a")
        check(f"[CP8-T15] (2) lanes-start runs with orchestrator = {val}: {{model}} is empty",
              r.returncode == 0 and shlex.split(start or "") == ["mycli", "--model", "", "--cwd", w.wt("a")], (start, text(r)[-300:]))

    # (4) a provider's company comes from its preset before its model
    cop, cod = {"id": "copilot", "preset": "copilot", "model": "gpt-5"}, {"id": "codex", "preset": "codex"}
    d, env, _st = ar.world(proj={"agents": {"reviewer": "github/gpt-5"}}, providers=[cop], names=("codex", "copilot", "gemini"))
    se.clean(env)
    res = ar.resolve(d, env, "low")
    row, _ = ar.models_row(d, env)
    check("[CP8-T15] (4) github/gpt-5 matches a configured copilot provider (copilot reviews, on gpt-5)",
          res.ids == ["copilot-both"] and (res.out or [{}])[0].get("model") == "gpt-5", (res.ids, res.err[-200:]))
    check("[CP8-T15] (4) ... and models does not claim no github provider is set up; it names Copilot",
          "no provider of the company" not in ar.s(row.get("reason")) and "copilot" in ar.s(row.get("effective")).lower(), row)
    d, env, _st = ar.world(proj={"agents": {"reviewer": "openai/gpt-5"}}, providers=[cop, cod], names=("codex", "copilot", "gemini"))
    se.clean(env)
    res = ar.resolve(d, env, "low")
    row, _ = ar.models_row(d, env)
    check("[CP8-T15] (4) openai/gpt-5 never resolves to a copilot provider listed before codex: codex reviews, on gpt-5",
          res.ids == ["codex-both"] and (res.out or [{}])[0].get("model") == "gpt-5", (res.ids, res.err[-200:]))
    check("[CP8-T15] (4) ... models names Codex for it, not Copilot", "codex" in ar.s(row.get("effective")).lower()
          and "copilot" not in ar.s(row.get("effective")).lower(), row)
    d2, env2 = se.fresh({"providers": [dict(cop), dict(cod)], "agents": {"orchestrator": "openai/gpt-5"}})
    out = se.models(d2, env2)[1]
    check("[CP8-T15] (4) the start command and the resolution agree: openai/gpt-5 starts a session with `codex -m gpt-5`",
          "codex -m gpt-5" in se.about(out, "orchestrator") and "copilot --model" not in se.about(out, "orchestrator"),
          se.about(out, "orchestrator")[:300])
    d3, env3, _st = ar.world(proj={"agents": {"reviewer": "openai/gpt-5"}}, providers=[cod], names=("codex", "gemini"))
    se.clean(env3)
    res3 = ar.resolve(d3, env3, "low")
    check("[CP8-T15] (4) (control) with only codex configured openai/gpt-5 still picks codex on gpt-5",
          res3.ids == ["codex-both"] and (res3.out or [{}])[0].get("model") == "gpt-5", (res3.ids, res3.err[-200:]))

    # (5) a UI lens choice that resolves to a provider that is not a runner is launched on the runner or shown plainly
    not_runner = re.compile(r"not a (?:UI review )?(?:provider|runner)|not a UI review|is not a runner", re.I)
    for provs, tag in (([{"id": "codex-fast", "preset": "codex"}], "codex-fast (preset codex)"), ([], "no such provider")):
        proj = {"providers": provs, "agents": {"visual-reviewer": "openai/gpt-5"}}
        d, env, stubs, m = ar.ui_world(proj)
        host, order = ar.ui_order(d, env)
        row, _ = ar.models_row(d, env, "ui-reviewer-visual")
        mm = cl.engine()
        with th.InProcessCall(mm, d, env, cl.RESET_GLOBALS, cl.LOCK_WAIT_CAP) as rr:
            print(json.dumps(ar._UI["mod"].role_plan(type("A", (), {"visual": None, "behavior": None})())))
        last = [ln for ln in rr.stdout.splitlines() if ln.strip()]
        plan = json.loads(last[-1]) if rr.returncode == 0 and last else [None, {}, {}, {}]
        skipped = (plan[3] or {}).get("visual", [])
        launched = bool((order or {}).get("visual")) and order["visual"][0] == "codex" and (plan[2] or {}).get("visual") == ["codex", "gpt-5"]
        shown = ("codex-fast" in ar.s(row.get("reason")) and not_runner.search(ar.s(row.get("reason"))) is not None
                 and any("codex-fast" in s and not_runner.search(s) for s in skipped))
        check(f"[CP8-T15] (5) UI lens openai/gpt-5 with {tag}: launched on the codex runner (model gpt-5) or shown plainly "
              "as not a runner in models and the plan, never silently dropped", launched if not provs else (launched or shown),
              (order, row.get("reason"), skipped, plan[2]))


# ---- T16: the CP6 follow-ups

QUOTA_STUB = al.PARTWAY_STUB.replace('echo "error: boom" >&2', 'echo "You have hit your usage limit. Try again in 3 hours." >&2')


def status_line(d, env, rid):
    out = run(d, env, "reviewer-status").stdout
    m = re.search(rf"^\s+(✓|·)\s+{re.escape(rid)}\b(.*)$", out, re.M)
    return (m.group(1), m.group(2)) if m else (None, out[-200:])


def t16_cp6_follow_ups():
    def cfg(st):
        (st.bin / "codex").write_text(al.PARTWAY_STUB.replace("@LOG@", str(st.log)))
        return {"providers": [dict(ar.CODEX)], "agents": {"reviewer": "codex/model-x"}}
    d, env, stubs, data = ar.approved_world(cfg)
    out = Path(data["output_dir"]) / "codex-both.json"
    r = al.run_review(d, env, "codex-both", out)
    marks = json.loads(ar.unavailable_path(d).read_text()) if ar.unavailable_path(d).exists() else {}
    check("[CP8-T16] (setup) a review of model-x that fails partway leaves a mark naming model-x",
          r.returncode == 3 and (marks.get("codex-both") or {}).get("model") == "model-x", (r.returncode, marks))
    res = ar.resolve(d, env, "low")
    check("[CP8-T16] reviewers keeps codex-both out while the choice that failed is still set",
          res.out is not None and "codex-both" not in res.ids, res.ids)
    mark, why = status_line(d, env, "codex-both")
    check("[CP8-T16] reviewer-status judges the mark the same way: codex-both is unavailable (failed recently) while the "
          "choice that failed is still set", mark == "·" and "failed recently" in why, (mark, why))
    base = al.read_proj(d)
    al.write_proj(d, dict(base, agents={"reviewer": "codex/model-y"}))
    mark_y, why_y = status_line(d, env, "codex-both")
    check("[CP8-T16] with the choice changed to model-y, reviewer-status shows codex-both available again",
          mark_y == "✓" and "failed recently" not in why_y, (mark_y, why_y))
    gone = dict(base)
    gone.pop("agents", None)
    al.write_proj(d, gone)
    mark_n, why_n = status_line(d, env, "codex-both")
    back = ar.resolve(d, env, "low")
    check("[CP8-T16] with the choice removed, reviewer-status and reviewers both bring codex-both back",
          mark_n == "✓" and "codex-both" in back.ids, (mark_n, why_n, back.ids))

    # an out-of-credits failure on a chosen model, when the shared record cannot be saved, marks the provider with no model
    def cfg_q(st):
        (st.bin / "codex").write_text(QUOTA_STUB.replace("@LOG@", str(st.log)))
        return {"providers": [dict(ar.CODEX)], "agents": {"reviewer": "codex/model-x"}}
    d2, env2, stubs2, data2 = ar.approved_world(cfg_q)
    m = cl.engine()
    orig = m.bench_quota
    m.bench_quota = lambda *a, **k: False
    try:
        rq = al.run_review(d2, env2, "codex-both", Path(data2["output_dir"]) / "codex-both.json")
    finally:
        m.bench_quota = orig
    marks2 = json.loads(ar.unavailable_path(d2).read_text()) if ar.unavailable_path(d2).exists() else {}
    mk = marks2.get("codex-both") or {}
    check("[CP8-T16] an out-of-credits failure on the chosen model-x is reported unavailable (exit 3)",
          rq.returncode == 3 and "UNAVAILABLE" in rq.stderr, (rq.returncode, rq.stderr[-200:]))
    check("[CP8-T16] ... and leaves a quota mark with no model (a wait for the whole provider)",
          mk.get("quota") is True and "model" not in mk, marks2)
    base2 = al.read_proj(d2)
    for tag, newp in (("choice still model-x", base2), ("choice changed to model-y", dict(base2, agents={"reviewer": "codex/model-y"})),
                      ("choice removed", {k: v for k, v in base2.items() if k != "agents"})):
        al.write_proj(d2, newp)
        res2 = ar.resolve(d2, env2, "low")
        check(f"[CP8-T16] ({tag}) the out-of-credits provider stays out: changing or removing the choice does not put it back",
              res2.out is not None and "codex-both" not in res2.ids, res2.ids)


# ---- T17: the deferred doc-staleness minors

def _docstring(tree, name):
    return next((ast.get_docstring(n) or "" for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name), None)


def t17_deferred_doc_minors_are_corrected():
    readme = re.sub(r"\s+", " ", rd(README))
    schemas = re.sub(r"\s+", " ", rd(SCHEMAS))
    src, tree = _engine_tree()
    check("[CP8-T17] README no longer says an agent session can edit .ratchet/config.json (the guard refuses it)",
          re.search(r"(?:agent|session)[^.;]{0,60}\b(?:can|may)\b[^.;]{0,30}\bedit\b[^.;]{0,50}(?:project'?s? file|\.ratchet/config\.json)", readme, re.I)
          is None, None)
    check("[CP8-T17] ... and names `config --set` as the way an agent changes the project's file",
          re.search(r"an agent uses `ratchet\.py config --set", readme) is not None, None)
    check("[CP8-T17] README's Codex bare-name sentence says a host with no host_models entry accepts any well-formed bare name on "
          "config --set (it does not describe a refusal)",
          "accepts any well-formed bare name on `config --set`" in readme and "that no other host" not in readme, None)
    at = schemas.find("spec keep")
    window = schemas[max(0, at - 250):at + 250] if at >= 0 else ""
    check("[CP8-T17] schemas.md's list of the other writers (the one that names spec keep) also names config --unset, the "
          "project-level removal", at >= 0 and "config --unset" in window, window)
    low = r"first (?:review )?slot[^.]{0,80}low[- ]risk|low[- ]risk[^.]{0,80}first (?:review )?slot"
    check("[CP8-T17] schemas.md says the models reviewer row judges the first slot of a low-risk step",
          re.search(r"reviewer.{0,400}?(?:" + low + ")|(?:" + low + r").{0,400}?reviewer", schemas, re.I) is not None, None)
    doc = _docstring(tree, "_reviewer_detail") or ""
    check("[CP8-T17] the reviewer-row docstring (_reviewer_detail) says it judges the first slot of a low-risk step",
          re.search(low, re.sub(r"\s+", " ", doc), re.I) is not None, doc[:200])
    doc = _docstring(tree, "launch_cfg") or ""
    check("[CP8-T17] launch_cfg's docstring no longer calls itself the one application of the choice",
          bool(doc) and "one per-reviewer application" not in doc, doc[:200])
    doc = re.sub(r"\s+", " ", _docstring(tree, "global_config") or "")
    check("[CP8-T17] global_config's docstring lists the nested shape checks it makes (providers entries, role_models, "
          "host_models with alternates, ui_reviewers, agents, host_provider)",
          all(w in doc for w in ("nested", "providers", "role_models", "host_models", "alternates", "ui_reviewers", "agents", "host_provider")),
          doc[:200])
    ui_src = rd(UI_REVIEW_PY)
    ui_tree = ast.parse(ui_src)
    ui_doc = re.sub(r"\s+", " ", _docstring(ui_tree, "role_providers") or "")
    used = False
    for p in (ROOT / "skills").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "role_providers":
                used = True
    check("[CP8-T17] ui_review.role_providers is used by production code or its docstring says it is test-only",
          used or re.search(r"test-only|only (?:used )?(?:by|in) (?:the )?tests|tests only|used only (?:by|in) tests", ui_doc, re.I) is not None,
          ui_doc[:200])


# ---- T18: a wrong-shaped host_models never crashes a command

HM_SHAPES = [
    ("a bare string per host", {"anthropic": "opus"}, ("host_models.anthropic", "object of tier names to models")),
    ("a string for the whole key", "opus", ("'host_models'", "JSON object")),
    ("a wrong tier value", {"anthropic": {"strong": 5}}, ("host_models.anthropic.strong", "non-empty string")),
    ("alternates not a list", {"anthropic": {"strong": "opus", "alternates": "sonnet"}},
     ("host_models.anthropic.alternates", "list of non-empty model names")),
]
GOOD_HM = {"anthropic": {"strong": "opus", "standard": "sonnet", "light": "haiku", "alternates": ["sonnet", "opus"]}}
HM_COMMANDS = (("config",), ("models",), ("models", "--json"), ("status",), ("next",))
HM_REPAIRS = (("config", "--unset", "host_models"), ("config", "--global", "--unset", "host_models"),
              ("config", "--global", "--set", "host_models.anthropic.strong=opus"))


def hm_world():
    d, env = se.fresh({})
    return d, env


def put_hm(d, env, where, value):
    if where == "project":
        cl.project_path(d).write_text(json.dumps({"host_models": value}))
    else:
        cl.put_global(env, {"host_models": value})


def stderr_lines(r):
    return [ln for ln in r.stderr.splitlines() if ln.strip()]


def norm_message(r, path):
    s = r.stderr
    for p in {str(path), os.path.realpath(str(path))}:
        s = s.replace(p, "<FILE>")
    return s.strip()


def t18_wrong_shaped_host_models_never_crashes():
    d, env = hm_world()
    for label, bad, (key_bit, shape_bit) in HM_SHAPES:
        # what the global file already says, as the reference for the project file's message
        put_hm(d, env, "global", bad)
        put_hm(d, env, "project", {})
        ref = run(d, env, "models")
        ref_msg = norm_message(ref, cl.global_path(env))
        check(f"[CP8-T18] (reference) the global file's message for {label} names the key and the expected shape",
              ref.returncode != 0 and key_bit in ref_msg and shape_bit in ref_msg, ref_msg[:300])
        for where in ("project", "global"):
            d, env = hm_world()
            path = cl.project_path(d) if where == "project" else cl.global_path(env)
            put_hm(d, env, where, bad)
            tag = f"{where} file, {label}"
            for cmd in HM_COMMANDS:
                r = run(d, env, *cmd)
                every = text(r)
                msg = norm_message(r, path)
                check(f"[CP8-T18] ({tag}) `{' '.join(cmd)}` prints no traceback", "Traceback" not in every and "AttributeError" not in every,
                      every[-300:])
                check(f"[CP8-T18] ({tag}) `{' '.join(cmd)}` refuses cleanly: exit 1, one line naming the file, the key and "
                      "the expected shape, the same message the global file gives",
                      r.returncode == 1 and len(stderr_lines(r)) == 1 and ("<FILE>" in msg) and key_bit in msg and shape_bit in msg
                      and msg == ref_msg, (r.returncode, msg[:300]))
            for rep in HM_REPAIRS:
                if "--set" in rep and "alternates" in label:
                    continue    # setting `strong` cannot fix a bad `alternates`: only the unset repairs apply to that shape
                put_hm(d, env, where, bad)
                if where == "project":
                    cl.put_global(env, {})
                else:
                    cl.project_path(d).write_text("{}")
                r = run(d, env, *rep)
                check(f"[CP8-T18] ({tag}) the repair command `{' '.join(rep)}` exits 0 with the wrong shape already present",
                      r.returncode == 0 and "Traceback" not in text(r), (r.returncode, text(r)[:300]))
                fixes_this_file = (where == "project" and rep[1] == "--unset") or (where == "global" and rep[1] == "--global")
                if fixes_this_file:
                    after = run(d, env, "models")
                    gfile, pfile = files_of(d, env)
                    here = pfile if where == "project" else gfile
                    if "--unset" in rep:
                        valid = "host_models" not in here
                    else:
                        valid = here.get("host_models") == {"anthropic": {"strong": "opus"}}
                    check(f"[CP8-T18] ({tag}) after `{' '.join(rep)}` the file is valid: models loads and the file holds "
                          "the repaired value", after.returncode == 0 and valid, (after.returncode, text(after)[:200], here))
    for where in ("project", "global"):
        d, env = hm_world()
        put_hm(d, env, where, GOOD_HM)
        rj = run(d, env, "models", "--json")
        data = se.jload(rj) or {}
        rc = run(d, env, "config")
        check(f"[CP8-T18] a correct host_models in the {where} file still loads exactly as before (models, models --json, config)",
              rj.returncode == 0 and rc.returncode == 0 and (data.get("roles") or {}).get("ratchet-adversary") == "opus"
              and data.get("alternates") == ["sonnet", "opus"], (rj.returncode, text(rj)[:200]))


TESTS = [
    ("CP8-T01", t01_readme_has_a_short_section_with_a_global_example),
    ("CP8-T02", t02_project_example_next_to_the_global_one),
    ("CP8-T03", t03_readme_examples_really_work),
    ("CP8-T04", t04_read_only_check_and_which_level_wins),
    ("CP8-T05", t05_going_back_to_the_defaults_works),
    ("CP8-T06", t06_running_session_cannot_be_switched),
    ("CP8-T07", t07_a_model_choice_never_approves_paid_use_or_weakens_review),
    ("CP8-T08", t08_command_table_lists_the_new_options),
    ("CP8-T09", t09_readme_does_not_claim_more_than_the_code_does),
    ("CP8-T10", t10_new_file_is_listed_in_running_the_tests),
    ("CP8-T11", t11_inventory_matches_what_the_file_reads),
    ("CP8-T12", t12_skill_and_schemas_agree_with_the_readme),
    ("CP8-T13", t13_existing_doc_lint_is_untouched_and_no_em_dash),
    ("CP8-T14", t14_validity_rule_is_stated_once_truthfully_everywhere),
    ("CP8-T15", t15_cp7_follow_ups),
    ("CP8-T16", t16_cp6_follow_ups),
    ("CP8-T17", t17_deferred_doc_minors_are_corrected),
    ("CP8-T18", t18_wrong_shaped_host_models_never_crashes),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
