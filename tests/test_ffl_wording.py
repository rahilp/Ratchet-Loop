#!/usr/bin/env python3
"""
CP1 tests of the fast-follow cleanups run: honest repair wording and one answer for a full model id.
   python3 tests/test_ffl_wording.py

A developer (or the agent acting for them) reads the docs and the messages of the agent provider/model settings. Two
things must be true there:
  * the docs say exactly what the repair commands do: they always run, and unsetting the offending key in the file that
    holds it repairs it, while a repair aimed at the other file or another key leaves the bad value in place (CP1-T01 to
    CP1-T05; T05 runs the commands for real and checks that what the README sentence says is what happens);
  * a full model id gets one consistent answer: on a host that lists its models (Anthropic) the misspelling hint and the
    "can't use" refusal both name the form the host accepts, `anthropic/claude-opus-4-1`, and every example either
    message names is really accepted (CP1-T06 to CP1-T09).
CP1-T10 checks that this file is listed in the README's "Running the tests" list and, because T09 parses the engine's
source text, in tests/fixtures/modularize_cp1/test-inventory.md.

Design assumptions (the production wording does not exist yet; each case states what it needs):
  * the three docs (README.md, skills/ratchet-orchestrator/SKILL.md, skills/ratchet-orchestrator/references/schemas.md)
    keep the sentence that ends "The model part can't be checked offline"; the repair wording sits in the 700
    characters before that anchor (the same window tests/test_agents_docs.py CP8-T14 reads, which must keep passing);
  * the new wording contains the words "offending key", "file that holds it" and "leaves the bad value in place", keeps
    a truthful "always work" that is followed by a scope (never by ")" or "."), and adds no em dash;
  * a concrete example in a message is a `provider/model` token (not the literal "provider/model") or a bare id after
    "such as"; every such example must be accepted by `config --set agents.orchestrator=<example>` on the same host.

Everything runs in-process through the engine's own entry points (standard 30), with the helpers of
tests/test_config_layers.py and tests/test_agents_session.py: a throwaway git project, a private per-user Ratchet home,
the host pinned per case. No network, no real agent CLI, nothing is spent.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
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
import test_agents_session as se                        # noqa: E402  (fresh(): a project pinned to a host)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP1-[TX]\d+)\]")

README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"
EM = chr(0x2014)
ANCHOR = re.compile(r"model part (?:can(?:no|['’])t|cannot) be checked offline", re.I)
FULL_FORM = "anthropic/claude-opus-4-1"
PROVIDER_PARTS = [r"provider part", r"configured provider or a known company", r"set-time", r"at load",
                  r"repair commands", r"always work"]
BAD = {"agents": {"implementer": "nope/x"}}


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


def flat(md):
    return re.sub(r"\s+", " ", md)


def spans_before_anchor(md, n=700):
    """The n characters before each place the doc says the model part can't be checked offline (whitespace flattened)."""
    f = flat(md)
    return [f[max(0, m.start() - n):m.start() + 60] for m in ANCHOR.finditer(f)]


def project_cfg(d):
    p = cl.project_path(d)
    return json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}


def agents_of(d):
    return (project_cfg(d).get("agents") or {})


def one_line(r, *paths):
    """stderr of a call with the folder paths hidden, as one string."""
    s = r.stderr
    for p in paths:
        for q in {str(p), os.path.realpath(str(p))}:
            s = s.replace(q, "<FILE>")
    return s.strip()


def examples_in(msg):
    """The concrete values a message tells the person to write: provider/model tokens (not the literal words
    provider/model) and a bare id after 'such as'."""
    out = []
    for m in re.finditer(r"(?<![\w/.\-])([a-z][a-z0-9_\-]*/[A-Za-z0-9][\w.\-]*[A-Za-z0-9])", msg):
        tok = m.group(1)
        if tok != "provider/model" and tok not in out:
            out.append(tok)
    for m in re.finditer(r"such as ([A-Za-z0-9][\w.\-/]*[A-Za-z0-9])", msg):
        tok = m.group(1)
        if tok not in out:
            out.append(tok)
    return out


def set_agent(host, value, key="agents.orchestrator"):
    """One `config --set` in a fresh empty project on the host; returns (project dir, result)."""
    d, env = se.fresh({}, host=host)
    return d, run(d, env, "config", "--set", f"{key}={value}")


# ------------------------------------------------------------------------------------------------ docs (T01 to T04)

def repair_wording_checks(tag, name, md):
    spans = spans_before_anchor(md)
    check(f"[{tag}] {name} still has the sentence ending \"the model part can't be checked offline\"", bool(spans), None)
    span = spans[0] if spans else ""
    check(f"[{tag}] {name} says unsetting the offending key in the file that holds it repairs it",
          re.search(r"offending key", span, re.I) is not None and re.search(r"file that holds it", span, re.I) is not None,
          span[-420:])
    check(f"[{tag}] {name} says a repair aimed at a different file or key leaves the bad value in place",
          re.search(r"leaves? the bad value in place", span, re.I) is not None, span[-420:])
    bare = re.findall(r"repair commands[^()]*always work\s*[).]", flat(md), re.I)
    check(f"[{tag}] {name} no longer claims the repair commands 'always work' with nothing after it", not bare, bare[:1])


def t01_readme_says_exactly_what_the_repair_commands_do():
    repair_wording_checks("CP1-T01", "README.md", rd(README))


def t02_orchestrator_skill_says_the_same():
    repair_wording_checks("CP1-T02", "orchestrator SKILL.md", rd(SKILL))


def t03_schemas_says_the_same_and_no_doc_keeps_the_bare_claim():
    repair_wording_checks("CP1-T03", "schemas.md", rd(SCHEMAS))
    docs = [README] + sorted((ROOT / "skills").rglob("*.md"))
    bare = [(p.relative_to(ROOT).as_posix(), m)
            for p in docs for m in re.findall(r"repair commands[^()]*always work\s*[).]", flat(rd(p)), re.I)]
    check("[CP1-T03] no doc anywhere (README and every skill file) still carries the unqualified "
          "'repair commands ... always work)' sentence", not bare, bare[:2])


def t04_new_wording_still_passes_the_earlier_docs_rule():
    for name, path in (("README.md", README), ("orchestrator SKILL.md", SKILL), ("schemas.md", SCHEMAS)):
        spans = spans_before_anchor(rd(path))
        check(f"[CP1-T04] {name} has the sentence the earlier docs rule reads", bool(spans), None)
        for span in spans[:3]:
            miss = [p for p in PROVIDER_PARTS if not re.search(p, span, re.I)]
            check(f"[CP1-T04] {name}: all provider-part phrases are still present (provider part, configured provider "
                  "or a known company, set-time, at load, repair commands, always work)", not miss, miss)
            hits = list(re.finditer(r"always work", span, re.I))
            bad = [span[m.end():m.end() + 25] for m in hits if re.match(r"\s*[).]", span[m.end():m.end() + 6])
                   or not span[m.end():].strip()]
            check(f"[CP1-T04] {name}: every 'always work' is followed by a scope, never by ')' or '.' directly",
                  bool(hits) and not bad, bad or "no 'always work' phrase left")
            sentence = span[span.find("The provider part") if "The provider part" in span else 0:]
            check(f"[CP1-T04] {name}: no em dash in the rewritten sentence", EM not in sentence, sentence.count(EM))
    r = subprocess.run(["git", "status", "--porcelain", "--", "tests/test_agents_docs.py"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    check("[CP1-T04] tests/test_agents_docs.py is unchanged (CP8-T14 keeps passing as written)",
          (HERE / "test_agents_docs.py").is_file() and r.returncode == 0 and r.stdout.strip() == "",
          (r.returncode, r.stdout, r.stderr[:100]))


# ------------------------------------------------------------------------------------------------ T05: the claim is true

def bad_world(where):
    d, env = se.fresh({})
    if where == "project":
        cl.project_path(d).write_text(json.dumps(BAD))
    else:
        cl.put_global(env, BAD)
    return d, env


def holds_bad_value(d, env, where):
    if where == "project":
        return (project_cfg(d).get("agents") or {}).get("implementer") == "nope/x"
    p = cl.global_path(env)
    return p.exists() and ((json.loads(p.read_text()).get("agents") or {}).get("implementer") == "nope/x")


def t05_what_the_docs_claim_matches_what_the_repair_commands_do():
    OWN_REPAIRS = {"project": [("config", "--unset", "agents.implementer"),
                               ("config", "--set", "agents.implementer=opus"),
                               ("config", "--unset", "agents")],
                   "global": [("config", "--global", "--unset", "agents.implementer"),
                              ("config", "--global", "--set", "agents.implementer=opus"),
                              ("config", "--global", "--unset", "agents")]}
    OTHER_FILE = {"project": [("config", "--global", "--unset", "agents.implementer"),
                              ("config", "--global", "--set", "agents.fixer=opus")],
                  "global": [("config", "--unset", "agents.implementer"),
                             ("config", "--set", "agents.fixer=opus")]}
    OTHER_KEY = {"project": [("config", "--unset", "agents.fixer"), ("config", "--set", "agents.fixer=opus")],
                 "global": [("config", "--global", "--unset", "agents.fixer"),
                            ("config", "--global", "--set", "agents.fixer=opus")]}
    for where in ("project", "global"):
        for cmd in OWN_REPAIRS[where]:
            d, env = bad_world(where)
            r = run(d, env, *cmd)
            m = run(d, env, *cmd[:0], "models")
            tag = f"{where} file, `{' '.join(cmd)}`"
            check(f"[CP1-T05] ({tag}) the offending-key repair exits 0 and prints no traceback",
                  r.returncode == 0 and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
            check(f"[CP1-T05] ({tag}) `models` then exits 0 (the file is repaired)",
                  m.returncode == 0 and "Traceback" not in text(m), (m.returncode, text(m)[:200]))
        for cmd in OTHER_FILE[where]:
            d, env = bad_world(where)
            r = run(d, env, *cmd)
            m = run(d, env, "models")
            tag = f"{where} file bad, `{' '.join(cmd)}`"
            path = cl.project_path(d) if where == "project" else cl.global_path(env)
            msg = one_line(m, path)
            check(f"[CP1-T05] ({tag}) a repair aimed at the other file exits 0 and prints no traceback",
                  r.returncode == 0 and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
            check(f"[CP1-T05] ({tag}) `models` still refuses, naming the file that holds it and agents.implementer",
                  m.returncode == 1 and "<FILE>" in msg and "agents.implementer" in msg and holds_bad_value(d, env, where),
                  (m.returncode, msg[:200]))
        for cmd in OTHER_KEY[where]:
            d, env = bad_world(where)
            path = cl.project_path(d) if where == "project" else cl.global_path(env)
            r = run(d, env, *cmd)
            msg = one_line(r, path)
            tag = f"{where} file bad, `{' '.join(cmd)}`"
            check(f"[CP1-T05] ({tag}) another key in the same file is stopped: exit 1, one message naming the file and "
                  "agents.implementer, no traceback",
                  r.returncode == 1 and "Traceback" not in text(r) and "<FILE>" in msg and "agents.implementer" in msg,
                  (r.returncode, msg[:200]))
            check(f"[CP1-T05] ({tag}) the bad value is still in the file", holds_bad_value(d, env, where), None)
    # the README describes exactly those three outcomes
    span = spans_before_anchor(rd(README))
    span = span[0] if span else ""
    at = re.search(r"repair commands", span, re.I)
    sentence = span[at.start():] if at else span
    check("[CP1-T05] the README sentence says the repair commands run", re.search(r"\bruns?\b", sentence, re.I) is not None,
          sentence[:300])
    check("[CP1-T05] the README sentence says the offending key in its own file repairs it (outcome 1)",
          re.search(r"offending key", sentence, re.I) is not None and re.search(r"file that holds it", sentence, re.I) is not None
          and re.search(r"\brepairs?\b", sentence, re.I) is not None, sentence[:300])
    check("[CP1-T05] the README sentence says the other file or key runs but leaves the bad value in place (outcome 2)",
          re.search(r"leaves? the bad value in place", sentence, re.I) is not None, sentence[:300])
    check("[CP1-T05] the README sentence says another key in the same file is stopped and names the offending key "
          "(outcome 3)", re.search(r"\bstop(?:s|ped)?\b|\brefus\w+", sentence, re.I) is not None
          and re.search(r"offending key", sentence, re.I) is not None, sentence[:300])


# ------------------------------------------------------------------------------------------------ messages (T06 to T09)

def t06_full_id_refusal_points_to_the_accepted_form():
    d, r = set_agent("claude", "claude-opus-4-1")
    check("[CP1-T06] a bare full id on an Anthropic host is refused with exit 1", r.returncode == 1, (r.returncode, text(r)[:200]))
    check("[CP1-T06] nothing is written to the project file", "orchestrator" not in agents_of(d), project_cfg(d))
    check(f"[CP1-T06] the refusal names the accepted form {FULL_FORM} (host company, a slash, the same id)",
          FULL_FORM in r.stderr, r.stderr[-250:])


def t07_typo_hint_offers_a_full_id_only_in_the_accepted_form():
    d, r = set_agent("claude", "sonet")
    msg = r.stderr
    check("[CP1-T07] a typo on an Anthropic host is refused with exit 1 and nothing written",
          r.returncode == 1 and "orchestrator" not in agents_of(d), (r.returncode, project_cfg(d)))
    check("[CP1-T07] the hint still lists haiku, inherit, opus, sonnet",
          all(re.search(rf"\b{n}\b", msg) for n in ("haiku", "inherit", "opus", "sonnet")), msg[-250:])
    check(f"[CP1-T07] the hint offers a full id as provider/model with the example {FULL_FORM}", FULL_FORM in msg, msg[-250:])
    check("[CP1-T07] the hint no longer offers the bare full id as if it worked ('a full model id such as claude-opus-4-1')",
          re.search(r"such as claude-opus-4-1", msg) is None, msg[-250:])


def t08_every_example_a_message_names_is_accepted():
    refusals = {}
    for host in ("claude", "codex"):
        host_name = "anthropic" if host == "claude" else "openai"
        d, r = set_agent(host, "sonet")
        hint = r.stderr
        ex = examples_in(hint)
        check(f"[CP1-T08] ({host_name}) the typo hint is refused and names at least one concrete example",
              r.returncode == 1 and bool(ex), (r.returncode, hint[-250:]))
        for e in ex:
            d2, r2 = set_agent(host, e)
            check(f"[CP1-T08] ({host_name}) the hint's example `{e}` is accepted: exit 0 and stored",
                  r2.returncode == 0 and agents_of(d2).get("orchestrator") == e, (r2.returncode, r2.stderr[-200:]))
        refusals[host] = ex
    d, r = set_agent("claude", "claude-opus-4-1")
    ref = examples_in(r.stderr)
    check("[CP1-T08] (anthropic) the full-id refusal names a concrete example", r.returncode == 1 and bool(ref), r.stderr[-250:])
    for e in ref:
        d2, r2 = set_agent("claude", e)
        check(f"[CP1-T08] (anthropic) the refusal's example `{e}` is accepted: exit 0 and stored",
              r2.returncode == 0 and agents_of(d2).get("orchestrator") == e, (r2.returncode, r2.stderr[-200:]))
    check("[CP1-T08] (anthropic) the typo hint and the refusal name the same form",
          bool(ref) and set(refusals["claude"]) == set(ref), (refusals["claude"], ref))
    for host in ("claude", "codex"):
        host_name = "anthropic" if host == "claude" else "openai"
        d, r = set_agent(host, "sonet", key="role_models.ratchet-implementer")
        hint = r.stderr
        check(f"[CP1-T08] ({host_name}) the role_models typo hint is refused and keeps saying a bare full id works",
              r.returncode == 1 and re.search(r"full model id such as claude-opus-4-1", hint) is not None, hint[-250:])
        for e in examples_in(hint):
            d2, r2 = set_agent(host, e, key="role_models.ratchet-implementer")
            check(f"[CP1-T08] ({host_name}) the role_models hint's example `{e}` is accepted",
                  r2.returncode == 0, (r2.returncode, r2.stderr[-200:]))
        d3, r3 = set_agent(host, "claude-opus-4-1", key="role_models.ratchet-implementer")
        check(f"[CP1-T08] ({host_name}) role_models.ratchet-implementer=claude-opus-4-1 still exits 0",
              r3.returncode == 0, (r3.returncode, r3.stderr[-200:]))


def t09_the_function_docstring_describes_the_same_rule():
    tree = ast.parse(ENGINE.read_text())
    doc = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_check_agent_choices":
            doc = ast.get_docstring(node)
            break
    check("[CP1-T09] _check_agent_choices exists and has a docstring", bool(doc), None)
    f = flat(doc or "")
    check("[CP1-T09] the docstring mentions a full model id", re.search(r"full (?:model )?id", f, re.I) is not None, f[-300:])
    check(f"[CP1-T09] the docstring says it is written as provider/model, for example {FULL_FORM}",
          "provider/model" in f and FULL_FORM in f, f[-300:])
    check("[CP1-T09] the docstring says a bare full id is refused on a host that lists its models, with that form named",
          re.search(r"bare[^.]{0,120}refus|refus[^.]{0,120}bare", f, re.I) is not None
          and re.search(r"host[^.]{0,80}(?:lists|declares)[^.]{0,40}models?|models? list", f, re.I) is not None, f[-300:])


def t10_new_file_is_listed_in_readme_and_inventory():
    me = Path(__file__).read_text()
    check("[CP1-T10] the README 'Running the tests' list holds the line `python3 tests/test_ffl_wording.py`",
          "python3 tests/test_ffl_wording.py" in rd(README).splitlines()
          or any(ln.strip() == "python3 tests/test_ffl_wording.py" for ln in rd(README).splitlines()), None)
    # The live pattern of tests/test_modularize_inventory.py, assembled from pieces so this check never matches itself.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(me) or src_pat.search(me))
    inv = INVENTORY.read_text()
    listed = "test_ffl_wording.py" in inv
    check("[CP1-T10] this file reads engine source (it parses ratchet.py in T09), so it must be in the inventory",
          scans and listed, f"scans={scans} listed={listed}")
    m = re.search(r"- `test_ffl_wording\.py`[^\n]*(?:\n(?!- `|## |\n)[^\n]*)*", inv)
    entry = m.group(0) if m else ""
    check("[CP1-T10] its inventory entry is longer than 80 characters and mentions README and ratchet.py",
          len(entry) > 80 and re.search(r"README", entry) is not None and re.search(r"ratchet\.py", entry) is not None,
          entry[:200])


TESTS = [
    ("CP1-T01", t01_readme_says_exactly_what_the_repair_commands_do),
    ("CP1-T02", t02_orchestrator_skill_says_the_same),
    ("CP1-T03", t03_schemas_says_the_same_and_no_doc_keeps_the_bare_claim),
    ("CP1-T04", t04_new_wording_still_passes_the_earlier_docs_rule),
    ("CP1-T05", t05_what_the_docs_claim_matches_what_the_repair_commands_do),
    ("CP1-T06", t06_full_id_refusal_points_to_the_accepted_form),
    ("CP1-T07", t07_typo_hint_offers_a_full_id_only_in_the_accepted_form),
    ("CP1-T08", t08_every_example_a_message_names_is_accepted),
    ("CP1-T09", t09_the_function_docstring_describes_the_same_rule),
    ("CP1-T10", t10_new_file_is_listed_in_readme_and_inventory),
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
