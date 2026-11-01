#!/usr/bin/env python3
"""
CP4 tests of the fast-follow cleanups run: the `models` view agrees with the reviewer pick.
   python3 tests/test_ffl_company_view.py

A developer (through their agent) sets agents.reviewer to a company name such as `openai` or `openai/gpt-5`. The engine
has two ways to read "which company is this provider": a company-name request is resolved by the provider's tool first
(a Copilot provider answers to `github`), while reviewer selection and cross-company review judge the provider by its
model's company (a Copilot provider on gpt-5 is an `openai` reviewer). Before this checkpoint the `models` view and the
round-opening `reviewers` note therefore said "no provider of the company 'openai' is set up in this project" while the
pick launched copilot-both on gpt-5. These cases pin the repaired behaviour: the view and the note name any configured
provider of that company, say why the company name did not choose it and how to choose it (`copilot` or
`copilot/gpt-5`), and nothing about which provider launches, or how cross-company review is judged, changes:
  * the Copilot-on-gpt-5 project, asked for `openai` or `openai/gpt-5`, in `models --json`, plain `models` and the
    `reviewers` opening note (CP4-T01 to T04); two such providers are named in config order (T05);
  * no provider of that company at all, or a different company, keeps today's message and add-hint (T06, T07);
  * a real openai provider listed after Copilot still wins the name, with no "not applied" text (T08); the forms the
    message suggests are accepted (T09);
  * the pick, the vendor field, cross-company review, the orchestrator start command and the read-only JSON shape are
    exactly as before (T10, T11, T13, T14); a Copilot provider on a Claude model asked for as `anthropic` is named but
    still never an independent reviewer (T12);
  * the three docs describe the company-name rule (T15); this file is listed in the README and the test inventory
    stays consistent (T16).

Design assumptions (the production wording is not pinned beyond what the cases state):
  * "names a provider" means its id appears as a word in the row's reason or in the RATCHET note; "how to choose it"
    means the exact forms `<id>` and `<id>/<model>` appear; "its tool's company name" means the word github appears;
    "did not choose it" means the text mentions openai next to a negation;
  * the reviewer note is the line that starts with "RATCHET note" and mentions agents.reviewer, on the streams of the
    call that opened the round;
  * T11 and T10 are guards that pass on the old code (the pick is unchanged); T06 and T07 are guards for the old
    message; T01 to T05, T09 (its link to the message), T12, T15 and T16 fail on the old code.

Everything runs in-process through the engine's own entry points (standard 30), with the helpers of
tests/test_agents_reviewers.py and tests/test_config_layers.py: a throwaway git project, a private per-user Ratchet home,
the host pinned per case and stub agent CLIs on a private PATH. No network, no real agent CLI, nothing is spent. This
file reads no engine source text, so the test inventory must not list it.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
import test_agents_session as se                        # noqa: E402  (clean(): pin the host)
import test_agents_reviewers as ar                      # noqa: E402  (world, approved_world, resolve, models_row)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP4-[TX]\d+)\]")

README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"
EM = chr(0x2014)
# The number of em dashes each doc holds today (CP3 commit); the new wording may not add one.
EM_NOW = {"README.md": 11, "SKILL.md": 25, "schemas.md": 59}

COPILOT = {"id": "copilot", "preset": "copilot", "model": "gpt-5"}
CODEX = {"id": "codex", "preset": "codex"}
GEMINI = {"id": "gemini", "preset": "gemini"}
OPENCODE = {"id": "opencode", "preset": "opencode", "model": "gpt-5-codex"}
COPILOT_CLAUDE = {"id": "copilot", "preset": "copilot", "model": "claude-sonnet-4"}
AUTO = "the best available provider for each review slot, this agent when none is"
NO_PROVIDER = re.compile(r"no provider of the company", re.I)
NEGATION = re.compile(r"did not|didn't|does not|doesn't|do not|not chosen|is not|isn't|was not|not by", re.I)
ROW_KEYS = {"requested", "effective", "source", "reason"}
CLIS = ("codex", "copilot", "gemini", "claude", "opencode")


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


def pworld(providers, reviewer=None, host="claude", extra=None, names=CLIS):
    """A throwaway git project with the given providers and agents.reviewer, stub CLIs on PATH, the host pinned.
    Returns (dir, env, stubs)."""
    proj = dict(extra or {})
    if reviewer is not None:
        proj.setdefault("agents", {})["reviewer"] = reviewer
    d, env, stubs = ar.world(proj, providers=[dict(p) for p in providers], names=names)
    se.clean(env, host)
    return d, env, stubs


def opened(providers, reviewer, host="claude", names=("codex", "copilot", "gemini", "opencode")):
    """An approved project taken to the review step, as `reviewers CP1 --json` opens the round.
    Returns (dir, env, stubs, round data); stubs.first_call is what the opening call printed."""
    return ar.approved_world({"providers": [dict(p) for p in providers], "agents": {"reviewer": reviewer}},
                             host=host, stub_names=names)


def row_of(d, env):
    row, r = ar.models_row(d, env)
    return row, r


def reason_of(row):
    return str(row.get("reason") or "")


def note_of(stubs):
    """The opening call's RATCHET note lines about agents.reviewer, as a list."""
    return [ln for ln in text(stubs.first_call).splitlines() if "RATCHET note" in ln and "agents.reviewer" in ln]


def word(w, s):
    return re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", s) is not None


def explains(s, pid, model):
    """The text names provider `pid`, its model, its tool's company name (github, for copilot), says the company name
    did not choose it, and gives both forms that choose it."""
    return (word(pid, s) and model in s and "github" in s.lower() and "openai" in s.lower()
            and NEGATION.search(s) is not None and f"`{pid}`" in s and f"{pid}/{model}" in s)


def plain_block(out, role="reviewer"):
    """The role's row in plain `models` and the indented lines under it, as a list."""
    block = []
    for ln in out.splitlines():
        if not block:
            if re.match(rf"^  {re.escape(role)}\s", ln):
                block = [ln]
        elif ln.startswith(" " * 20):
            block.append(ln)
        else:
            break
    return block


def lines_before(out, role="reviewer"):
    keep = []
    for ln in out.splitlines():
        if re.match(rf"^  {re.escape(role)}\s", ln):
            break
        keep.append(ln)
    return keep


def shape(res):
    """[(id, slot, vendor, model)] of a resolve result."""
    return [(x["id"], x["slot"], x.get("vendor"), x.get("model")) for x in res.out or []]


def tree_digest(*bases):
    h = hashlib.sha256()
    for base in bases:
        base = Path(base)
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file() and ".git" not in p.relative_to(base).parts:
                h.update(str(p.relative_to(base)).encode())
                h.update(p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ T01 to T05

def t01_models_view_names_the_copilot_provider():
    d, env, _ = pworld([COPILOT], "openai")
    row, r = row_of(d, env)
    why = reason_of(row)
    check("[CP4-T01] `models --json` exits 0 with no traceback", r.returncode == 0 and "Traceback" not in text(r),
          (r.returncode, text(r)[:200]))
    check("[CP4-T01] the reviewer row's reason no longer claims no provider of the company 'openai' is set up",
          bool(row) and NO_PROVIDER.search(why) is None and why != "", why)
    check("[CP4-T01] the reason names the copilot provider and its model gpt-5", word("copilot", why) and "gpt-5" in why,
          why)
    check("[CP4-T01] the reason says it answers to its tool's company name (github), not openai",
          "github" in why.lower() and "openai" in why.lower() and NEGATION.search(why) is not None, why)
    check("[CP4-T01] the reason says how to choose it: `copilot` or `copilot/gpt-5`",
          "`copilot`" in why and "copilot/gpt-5" in why, why)
    check("[CP4-T01] requested stays 'openai' and effective stays the automatic-pick wording",
          row.get("requested") == "openai" and row.get("effective") == AUTO and row.get("source") == "project", row)


def t02_company_slash_model_gets_the_same_explanation():
    d, env, _ = pworld([COPILOT], "openai/gpt-5")
    row, r = row_of(d, env)
    why = reason_of(row)
    check("[CP4-T02] `models --json` exits 0 and the row asks openai/gpt-5",
          r.returncode == 0 and row.get("requested") == "openai/gpt-5" and row.get("effective") == AUTO, row)
    check("[CP4-T02] the reason never says no provider of that company is set up",
          why != "" and NO_PROVIDER.search(why) is None, why)
    check("[CP4-T02] the reason names the copilot provider and suggests `copilot/gpt-5` to choose it",
          word("copilot", why) and "copilot/gpt-5" in why, why)


def t03_opening_note_says_the_same_as_the_view():
    d, env, stubs, data = opened([COPILOT], "openai")
    note = note_of(stubs)
    joined = " ".join(note)
    check("[CP4-T03] the round-opening call printed exactly one RATCHET note about agents.reviewer = 'openai'",
          len(note) == 1 and "'openai'" in joined, note)
    check("[CP4-T03] the note no longer claims no provider of the company is set up", bool(note)
          and NO_PROVIDER.search(joined) is None, joined)
    check("[CP4-T03] the note names the copilot provider, says why the company name did not choose it and how to "
          "choose it (`copilot` or `copilot/gpt-5`)", explains(joined, "copilot", "gpt-5"), joined)
    check("[CP4-T03] the round still lists exactly copilot-both on model gpt-5",
          [(x["id"], x.get("model")) for x in data.get("reviewers", [])] == [("copilot-both", "gpt-5")],
          [(x["id"], x.get("model")) for x in data.get("reviewers", [])])


def t04_plain_text_view_carries_the_same_lines():
    d, env, _ = pworld([COPILOT], "openai")
    r = run(d, env, "models")
    block = plain_block(r.stdout)
    why = "\n".join(block[1:])
    check("[CP4-T04] plain `models` exits 0 and the reviewer row has a why: line under it",
          r.returncode == 0 and len(block) >= 2 and "why:" in why, block)
    check("[CP4-T04] the why: line names copilot and how to choose it and has no 'no provider of the company' claim",
          word("copilot", why) and "copilot/gpt-5" in why and NO_PROVIDER.search(why) is None
          and NO_PROVIDER.search(r.stdout) is None, why)
    d0, env0, _ = pworld([COPILOT], None)
    r0 = run(d0, env0, "models")
    above, above0 = lines_before(r.stdout), lines_before(r0.stdout)
    roles = [ln for ln in above if re.match(r"^  ratchet-[\w-]+\s+asked ", ln)]
    check("[CP4-T04] the thirteen sub-agent role rows above it are present and equal those of the same project with no "
          "reviewer setting", len(roles) == 13 and above == above0, (len(roles), len(above), len(above0)))


def t05_every_provider_of_that_company_is_named_in_config_order():
    d, env, _ = pworld([COPILOT, OPENCODE], "openai")
    row, r = row_of(d, env)
    why = reason_of(row)
    check("[CP4-T05] the view's reason names copilot before opencode with their own choose-it forms",
          why != "" and NO_PROVIDER.search(why) is None and word("copilot", why) and word("opencode", why)
          and why.find("copilot") < why.find("opencode") and "copilot/gpt-5" in why and "opencode/gpt-5-codex" in why, why)
    d, env, stubs, data = opened([COPILOT, OPENCODE], "openai")
    joined = " ".join(note_of(stubs))
    check("[CP4-T05] the opening note names copilot before opencode with their own choose-it forms and does not claim "
          "nothing of that company exists", joined != "" and NO_PROVIDER.search(joined) is None
          and word("copilot", joined) and word("opencode", joined) and joined.find("copilot") < joined.find("opencode")
          and "copilot/gpt-5" in joined and "opencode/gpt-5-codex" in joined, joined)


# ------------------------------------------------------------------------------------------------ T06 to T09

def t06_with_no_provider_of_that_company_the_old_message_stays():
    worlds = {"(a) no providers": [], "(b) copilot with no model": [{"id": "copilot", "preset": "copilot"}],
              "(c) copilot on a claude model": [COPILOT_CLAUDE]}
    for name, provs in worlds.items():
        d, env, _ = pworld(provs, "openai")
        row, r = row_of(d, env)
        why = reason_of(row)
        check(f"[CP4-T06] {name}: the view says no provider of the company 'openai' is set up in this project and does "
              "not name copilot", "no provider of the company 'openai' is set up in this project" in why
              and "copilot" not in why.lower(), why)
        d, env, stubs, data = opened(provs, "openai")
        joined = " ".join(note_of(stubs))
        check(f"[CP4-T06] {name}: the note says it too, with the `providers --add codex --preset codex` hint, and does "
              "not name copilot", "no provider of the company 'openai' is set up in this project" in joined
              and "providers --add codex --preset codex" in joined and "copilot" not in joined.lower(), joined)


def t07_a_copilot_provider_on_gpt_is_not_offered_for_a_different_company():
    d, env, _ = pworld([COPILOT], "google")
    row, r = row_of(d, env)
    why = reason_of(row)
    check("[CP4-T07] the view keeps 'no provider of the company google is set up' and does not name copilot",
          re.search(r"no provider of the company 'google' is set up", why) is not None and "copilot" not in why.lower(), why)
    d, env, stubs, data = opened([COPILOT], "google")
    joined = " ".join(note_of(stubs))
    check("[CP4-T07] the note keeps it too and does not name copilot",
          re.search(r"no provider of the company 'google' is set up", joined) is not None
          and "copilot" not in joined.lower(), joined)


def t08_a_real_openai_provider_after_copilot_still_wins_the_name():
    d, env, _ = pworld([COPILOT, CODEX], "openai/gpt-5")
    row, r = row_of(d, env)
    got = ar.resolve(d, env, "low")
    check("[CP4-T08] the pick is codex-both on gpt-5", shape(got) == [("codex-both", 1, "openai", "gpt-5")], shape(got))
    check("[CP4-T08] the reviewer row names Codex (gpt-5) and carries no reason about copilot",
          "codex" in str(row.get("effective")).lower() and "gpt-5" in str(row.get("effective"))
          and "copilot" not in json.dumps(row).lower() and reason_of(row) == "", row)
    d, env, stubs, data = opened([COPILOT, CODEX], "openai/gpt-5")
    out = text(stubs.first_call)
    check("[CP4-T08] the opening call lists codex-both on gpt-5 and has no 'not applied' line about agents.reviewer",
          [(x["id"], x.get("model")) for x in data.get("reviewers", [])] == [("codex-both", "gpt-5")]
          and not [ln for ln in out.splitlines() if "agents.reviewer" in ln and "not applied" in ln], note_of(stubs))


def t09_the_forms_the_view_suggests_are_accepted():
    d, env, _ = pworld([COPILOT], "openai")
    suggested = re.findall(r"`([^`]+)`", reason_of(row_of(d, env)[0]))
    suggested = [x for x in suggested if "copilot" in x]
    check("[CP4-T09] the openai message suggests both `copilot` and `copilot/gpt-5`",
          "copilot" in suggested and "copilot/gpt-5" in suggested, suggested)
    for form in dict.fromkeys(["copilot", "github", "copilot/gpt-5"] + suggested):
        d, env, _ = pworld([COPILOT], form)
        row, r = row_of(d, env)
        got = ar.resolve(d, env, "low")
        eff = str(row.get("effective"))
        check(f"[CP4-T09] agents.reviewer = {form!r}: empty reason and the row names Copilot and gpt-5",
              r.returncode == 0 and reason_of(row) == "" and "copilot" in eff.lower() and "gpt-5" in eff, row)
        check(f"[CP4-T09] agents.reviewer = {form!r}: the pick is copilot-both on gpt-5",
              shape(got) == [("copilot-both", 1, "openai", "gpt-5")], shape(got))


# ------------------------------------------------------------------------------------------------ T10 to T14

def t10_which_provider_launches_does_not_change():
    matrix = [
        ("openai", [COPILOT], [("copilot-both", 1, "openai", "gpt-5")]),
        ("github", [COPILOT], [("copilot-both", 1, "openai", "gpt-5")]),
        ("openai/gpt-5", [COPILOT, CODEX], [("codex-both", 1, "openai", "gpt-5")]),
        ("codex", [COPILOT, CODEX], [("codex-both", 1, "openai", None)]),
        ("openai", [], [("host-alt-both", 1, "host", "a different model: sonnet or opus")]),
    ]
    for reviewer, provs, want in matrix:
        d, env, _ = pworld(provs, reviewer)
        got = ar.resolve(d, env, "low")
        check(f"[CP4-T10] agents.reviewer={reviewer!r} providers={[p['id'] for p in provs]}: ids, slots, vendors and "
              "models are the expected literals", got.rc == 0 and shape(got) == want, (shape(got), got.err[-200:]))
    d, env, stubs, data = opened([COPILOT], "openai")
    rv = data.get("reviewers", [])
    check("[CP4-T10] `reviewers --json` for the first row lists copilot-both on gpt-5 with vendor 'openai'",
          [(x["id"], x["slot"], x["vendor"], x["model"]) for x in rv] == [("copilot-both", 1, "openai", "gpt-5")], rv)


def t11_cross_company_review_is_judged_by_the_models_company():
    d, env, _ = pworld([COPILOT_CLAUDE, CODEX], None, host="claude")
    for risk, want in (("low", [("codex-both", 1, "openai", None)]),
                       ("normal", [("codex-standards", 1, "openai", None), ("codex-correctness", 2, "openai", None)])):
        got = ar.resolve(d, env, risk)
        reasons = " ".join(str(v) for x in got.out or [] for k, v in x["reasons"].items() if "copilot" in k)
        check(f"[CP4-T11] Anthropic host, {risk}: codex reviews and the Copilot provider on claude-sonnet-4 is set aside "
              "as the host's company", shape(got) == want and "same company as the host" in reasons
              and not [x for x in got.out or [] if x["id"].startswith("copilot")], (shape(got), reasons))
    d, env, _ = pworld([COPILOT, GEMINI], None, host="codex")
    for risk, want in (("low", [("gemini-both", 1, "google", None)]),
                       ("normal", [("gemini-standards", 1, "google", None), ("gemini-correctness", 2, "google", None)])):
        got = ar.resolve(d, env, risk)
        reasons = " ".join(str(v) for x in got.out or [] for k, v in x["reasons"].items() if "copilot" in k)
        check(f"[CP4-T11] OpenAI host, {risk}: gemini reviews and the Copilot provider on gpt-5 is set aside as the "
              "host's company", shape(got) == want and "same company as the host" in reasons
              and not [x for x in got.out or [] if x["id"].startswith("copilot")], (shape(got), reasons))


def t12_copilot_on_a_claude_model_asked_as_anthropic_is_named_but_not_independent():
    d, env, _ = pworld([COPILOT_CLAUDE, CODEX], "anthropic", host="claude")
    row, r = row_of(d, env)
    why = reason_of(row)
    check("[CP4-T12] the view does not claim no provider of the company anthropic is set up",
          why != "" and NO_PROVIDER.search(why) is None, why)
    check("[CP4-T12] the view names copilot and says it is the same company as the host, never an independent reviewer",
          word("copilot", why) and re.search(r"same company", why, re.I) is not None
          and re.search(r"independent", why, re.I) is not None, why)
    got = ar.resolve(d, env, "low")
    check("[CP4-T12] the pick is unchanged: codex-both", shape(got) == [("codex-both", 1, "openai", None)], shape(got))
    d, env, stubs, data = opened([COPILOT_CLAUDE, CODEX], "anthropic")
    joined = " ".join(note_of(stubs))
    check("[CP4-T12] the opening note does not claim nothing exists, names copilot and says it is the same company as "
          "the host and never independent", joined != "" and NO_PROVIDER.search(joined) is None
          and word("copilot", joined) and re.search(r"same company", joined, re.I) is not None
          and re.search(r"independent", joined, re.I) is not None, joined)
    check("[CP4-T12] the round still lists exactly codex-both",
          [x["id"] for x in data.get("reviewers", [])] == ["codex-both"], [x["id"] for x in data.get("reviewers", [])])


def t13_orchestrator_start_command_resolution_is_unchanged():
    d, env, _ = pworld([COPILOT], None, extra={"agents": {"orchestrator": "openai/gpt-5"}})
    r = run(d, env, "models", "--json")
    data = se.jload(r)
    rows = se.rows(data)
    orch, rev = rows.get("orchestrator", {}), rows.get("reviewer", {})
    check("[CP4-T13] the orchestrator row gives the start command `codex -m gpt-5` and never copilot",
          r.returncode == 0 and orch.get("start") == "codex -m gpt-5" and "copilot" not in json.dumps(orch).lower(), orch)
    check("[CP4-T13] the reviewer row is unchanged: automatic, builtin, empty reason, the automatic-pick wording",
          rev.get("requested") == "automatic" and rev.get("source") == "builtin" and rev.get("reason") == ""
          and rev.get("effective") == AUTO, rev)
    rt = run(d, env, "models")
    check("[CP4-T13] plain `models` prints the same start command", "codex -m gpt-5" in rt.stdout, rt.stdout[-300:])


def t14_the_view_stays_read_only_and_its_json_shape_is_unchanged():
    d, env, stubs = pworld([COPILOT], "openai")
    home = Path(env["HOME"])
    before = (tree_digest(d), tree_digest(home))
    r = run(d, env, "models", "--json")
    after = (tree_digest(d), tree_digest(home))
    data = se.jload(r)
    check("[CP4-T14] `models --json` exits 0 and prints JSON", r.returncode == 0 and isinstance(data, dict), text(r)[:200])
    check("[CP4-T14] no fake CLI ran (copilot, codex, claude, gemini, opencode logged nothing)", stubs.calls() == [],
          stubs.calls())
    check("[CP4-T14] no file in the project or the home changed", before == after, (before, after))
    check("[CP4-T14] the top-level keys are host, roles, alternates and detail",
          set(data or {}) == {"host", "roles", "alternates", "detail"}, sorted(data or {}))
    bad = [e.get("role") for e in (data or {}).get("detail", []) if (set(e) - {"role", "start"}) != ROW_KEYS]
    reviewer = se.rows(data).get("reviewer", {})
    check("[CP4-T14] every row still has exactly role + requested/effective/source/reason (the orchestrator and director "
          "rows may add start); the reviewer row has exactly those four plus role",
          not bad and set(reviewer) == ROW_KEYS | {"role"}, (bad, sorted(reviewer)))


# ------------------------------------------------------------------------------------------------ T15 and T16

def doc_window(path):
    """300 characters before to 900 after the sentence that defines what a company name means for an outside reviewer."""
    t = path.read_text()
    m = re.search(r"first configured provider of that company", t)
    return None if m is None else t[max(0, m.start() - 300): m.start() + 900]


def t15_docs_describe_the_company_name_rule():
    for path in (README, SKILL, SCHEMAS):
        name = path.name
        w = doc_window(path)
        check(f"[CP4-T15] {name}: the 'company name for an outside reviewer' sentence is there", w is not None, name)
        w = w or ""
        check(f"[CP4-T15] {name}: says a provider answers to its tool's company name (github for copilot)",
              re.search(r"\banswers? to\b", w) is not None and re.search(r"tool(?:'s|’s) company name", w) is not None
              and "github" in w.lower(), w[:300])
        check(f"[CP4-T15] {name}: says a Copilot provider on a gpt model is chosen by its id (`copilot` or "
              "`copilot/gpt-5`) rather than by openai", "`copilot`" in w and "`copilot/gpt-5`" in w
              and re.search(r"rather than[^.]{0,40}openai", w) is not None, w[:300])
        check(f"[CP4-T15] {name}: says `reviewers` and `models` name that provider",
              re.search(r"`(?:reviewers|models)`(?: and `(?:reviewers|models)`)?[^.]{0,40}\bnames?\b[^.]{0,60}"
                        r"(?:provider|\bit\b)", w) is not None, w[:300])
        check(f"[CP4-T15] {name}: says cross-company review is still judged by the model's company",
              re.search(r"cross-company", w) is not None and re.search(r"model(?:'s|’s) company", w) is not None, w[:300])
        check(f"[CP4-T15] {name}: no em dash was added to the document",
              path.read_text().count(EM) <= EM_NOW[name], (path.read_text().count(EM), EM_NOW[name]))


def t16_new_file_is_listed_in_readme_and_the_inventory_stays_consistent():
    me_src = Path(__file__).read_text()
    lines = [ln.strip() for ln in README.read_text().splitlines()]
    check("[CP4-T16] the README 'Running the tests' list holds the line `python3 tests/test_ffl_company_view.py`",
          "python3 tests/test_ffl_company_view.py" in lines, None)
    # The live patterns of tests/test_modularize_inventory.py, assembled from pieces so this check never matches itself.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(me_src) or src_pat.search(me_src))
    inv = INVENTORY.read_text()
    named = re.search(r"(?<![A-Za-z0-9_])test_ffl_company_view\.py(?![A-Za-z0-9_])", inv) is not None
    check("[CP4-T16] the inventory names this file if and only if the live grep patterns match it",
          named == scans, f"scans={scans} named={named}")
    parts = re.split(r"(?m)^## Kind (\d)", inv)
    bad = []
    for i in range(1, len(parts), 2):
        kind, body = parts[i], parts[i + 1]
        stated = re.search(r"(?:\): |: )(\d+) files", body[:500])
        bullets = len(re.findall(r"(?m)^- `([^`]+)`", body))
        if kind in ("1", "3", "4") and (stated is None or int(stated.group(1)) != bullets):
            bad.append((kind, stated and stated.group(1), bullets))
    check("[CP4-T16] the inventory's stated counts for Kinds 1, 3 and 4 still equal the entries listed under them",
          not bad and len(parts) >= 9, bad)
    check("[CP4-T16] the earlier files' inventory entries are still there (the inventory stays consistent)",
          "test_ffl_wording.py" in inv and "test_ffl_provider_shape.py" in inv, None)


TESTS = [
    ("CP4-T01", t01_models_view_names_the_copilot_provider),
    ("CP4-T02", t02_company_slash_model_gets_the_same_explanation),
    ("CP4-T03", t03_opening_note_says_the_same_as_the_view),
    ("CP4-T04", t04_plain_text_view_carries_the_same_lines),
    ("CP4-T05", t05_every_provider_of_that_company_is_named_in_config_order),
    ("CP4-T06", t06_with_no_provider_of_that_company_the_old_message_stays),
    ("CP4-T07", t07_a_copilot_provider_on_gpt_is_not_offered_for_a_different_company),
    ("CP4-T08", t08_a_real_openai_provider_after_copilot_still_wins_the_name),
    ("CP4-T09", t09_the_forms_the_view_suggests_are_accepted),
    ("CP4-T10", t10_which_provider_launches_does_not_change),
    ("CP4-T11", t11_cross_company_review_is_judged_by_the_models_company),
    ("CP4-T12", t12_copilot_on_a_claude_model_asked_as_anthropic_is_named_but_not_independent),
    ("CP4-T13", t13_orchestrator_start_command_resolution_is_unchanged),
    ("CP4-T14", t14_the_view_stays_read_only_and_its_json_shape_is_unchanged),
    ("CP4-T15", t15_docs_describe_the_company_name_rule),
    ("CP4-T16", t16_new_file_is_listed_in_readme_and_the_inventory_stays_consistent),
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
