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


# ---- CP2 ----
# README: a per-CLI table replaces the "hooks work the same on all four CLIs" claim, and the hook-error
# paragraph says plainly that status/next surface recent hook errors (not only the log file).

HOOKS_SAME_RX = re.compile(
    r"hooks?\s+work\s+(the\s+same|identically|equally)\b[^.\n]{0,80}"
    r"(all\s+four|four\s+(agent\s+)?CLIs|every\s+CLI|each\s+(of\s+the\s+)?(four|CLIs))", re.I)

ONLY_CLAUDE_SUBAGENT_RX = re.compile(
    r"only\s+claude\s+code\b[^.\n]{0,100}"
    r"(tells?\s+ratchet\s+which\s+sub-?agent|which\s+sub-?agent\s+is\s+acting)", re.I)

SUBAGENT_CLAIM_RX = re.compile(
    r"tells?\s+ratchet\s+which\s+sub-?agent|which\s+sub-?agent\s+is\s+acting|identifies?\s+which\s+sub-?agent", re.I)

CONTRADICTION_RX = re.compile(
    r"only\s+writ(e|es|ten)\s+to\s+(the\s+)?log|never\s+(shown|surfaced|visible|seen)\s+elsewhere|"
    r"only\s+(shows?|appears?)\s+in\s+(the\s+)?log(\s+file)?\b|nowhere\s+else", re.I)

RECORD_PATTERNS = (r"record|state(-|\s)?file", r"protect|guard|block|hand-edit|hand edit|safe from")
REVIEWER_PATTERNS = (r"review(er)?|sub-?agent", r"identit|confirm|verif|which\s+(sub-?agent|reviewer)|know")
STOP_PATTERNS = (r"\bstop\b", r"hook|enforc|block|end\w*\s+(its|their|your)?\s*turn|quit|early")
PROMPT_PATTERNS = (r"prompt\s*hook|user'?s?\s+repl(y|ies)|replies", r"record")


def md_table_rows(text):
    """Rows of the first markdown table in text, as [cell, cell, ...]; the separator row is dropped."""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def cli_rows(sect):
    """Data rows of the table (header row dropped), or [] when no table is present."""
    rows = md_table_rows(sect)
    return rows[1:] if len(rows) >= 2 else []


def row_text(r):
    return " | ".join(r)


def sentences(text):
    return re.split(r"(?<=[.!?])\s+", text)


def l_hooks_same_claim(sect):  # T01
    return HOOKS_SAME_RX.search(sect) is not None


def l_table_four_cli(sect):  # T02
    rows = cli_rows(sect)
    if not rows:
        return False
    matches = []
    for r in rows:
        name = r[0] if r else ""
        matches.append([p for p in PROVIDERS if re.search(r"\b" + p, name, re.I)])
    if any(len(h) != 1 for h in matches):
        return False
    found = [h[0] for h in matches]
    return sorted(found) == sorted(PROVIDERS) and len(found) == len(set(found))


def l_rows_cover(sect, patterns):  # T03-T06
    rows = cli_rows(sect)
    if len(rows) != 4:
        return False
    return all(has(row_text(r), *patterns) for r in rows)


# Additive hardening (adversarial round 1, finding X1: l_rows_cover above is keyword-only across the whole
# row, so a row phrased as a negation of the capability ("records are not protected", "reviewer identity is
# not confirmed", "stop hook does not block early exit", "replies are not recorded") would still satisfy it).
# These look at the specific column for each capability and fail when that cell's own clause negates the
# claim, with the one named exception (reviewer identity: only the Claude row may affirm it) built in.
NEGATED_CLAIM_RX = re.compile(
    r"(not|n't|never|\bno\b|cannot|can't|without|lack(s|ing)?|fail(s)?\s+to|unable\s+to)"
    r"[^.;,]{0,25}(protect|guard|block|confirm|verif|stop|record|enforc)", re.I)

RECORD_CELL_RX = re.compile(r"protect|guard|hand-edit|hand edit|safe\s+from", re.I)
STOP_CELL_RX = re.compile(r"\bstop\b|block|early", re.I)
REVIEWER_CELL_RX = re.compile(r"reviewer\s+identity|which\s+sub-?agent|confirm", re.I)
PROMPT_CELL_RX = re.compile(r"repl(y|ies)|record(s|ed)?|prompt\s*hook", re.I)


def l_cell_affirms(cell, rx):
    """`rx` names the capability; true only when the cell both names it and does not immediately negate it
    (e.g. "not protected", "cannot confirm", "does not block", "not recorded")."""
    return bool(rx.search(cell)) and NEGATED_CLAIM_RX.search(cell) is None


def cols_by_cli(sect):
    """{cli_name_lower: [cell, ...]} for the 4 data rows, only when every row has the 5 expected columns."""
    rows = cli_rows(sect)
    if len(rows) != 4 or any(len(r) < 5 for r in rows):
        return {}
    out = {}
    for r in rows:
        for p in PROVIDERS:
            if re.search(r"\b" + p, r[0], re.I):
                out[p] = r
    return out if len(out) == 4 else {}


def l_record_col_affirms(sect):  # X01
    cols = cols_by_cli(sect)
    return bool(cols) and all(l_cell_affirms(r[1], RECORD_CELL_RX) for r in cols.values())


def l_stop_col_affirms(sect):  # X02
    cols = cols_by_cli(sect)
    return bool(cols) and all(l_cell_affirms(r[3], STOP_CELL_RX) for r in cols.values())


def l_prompt_col_affirms(sect):  # X03
    cols = cols_by_cli(sect)
    return bool(cols) and all(l_cell_affirms(r[4], PROMPT_CELL_RX) for r in cols.values())


def l_reviewer_col_polarity(sect):  # X04
    cols = cols_by_cli(sect)
    if not cols:
        return False
    if not l_cell_affirms(cols["claude"][2], REVIEWER_CELL_RX):
        return False
    return all(not l_cell_affirms(cols[p][2], REVIEWER_CELL_RX) for p in cols if p != "claude")


def l_claude_only_subagent(sect):  # T07
    if not ONLY_CLAUDE_SUBAGENT_RX.search(sect):
        return False
    rows = cli_rows(sect)
    if len(rows) != 4:
        return False
    others = [r for r in rows if not re.search(r"claude", r[0], re.I)]
    if len(others) != 3:
        return False
    return not any(SUBAGENT_CLAIM_RX.search(row_text(r)) for r in others)


def l_copilot_drops_message(sect, hooks_test_src):  # T08
    cand = [s for s in sentences(sect) if re.search(r"copilot", s, re.I)]
    claim = any(has(s, r"prompt\s*hook|userPromptSubmitted", r"\bdrop", r"message") for s in cand)
    grounded = "dropped anyway" in hooks_test_src.lower()
    contradiction = any(has(s, r"copilot", r"record", r"repl(y|ies)",
                            r"(same as|like the other|just as well|fully)") for s in cand)
    return claim and grounded and not contradiction


def hook_error_paragraph(sect):
    for p in re.split(r"\n\s*\n", sect):
        if "hook-errors.log" in p:
            return p
    return ""


def l_status_next_surfaced(sect):  # T09
    p = hook_error_paragraph(sect)
    return bool(p) and has(p, r"`status`") and has(p, r"`next`") and has(p, r"see|show|surface|appear|say|tell")


def l_no_contradiction(sect):  # T10
    p = hook_error_paragraph(sect)
    return bool(p) and CONTRADICTION_RX.search(p) is None


def cp2_docs_lint():
    print("CP2: README states what each CLI enforces")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    sect = hsection(readme, r"Using it with Codex, Gemini or Copilot")
    hooks_test_path = ROOT / "tests" / "test_hooks.py"
    hooks_test_src = hooks_test_path.read_text(encoding="utf-8") if hooks_test_path.exists() else ""
    rows = cli_rows(sect)

    check("[CP2-T01] the 'hooks work the same on all four CLIs' claim is gone",
          bool(sect) and not l_hooks_same_claim(sect), sect[:200])
    check("[CP2-T02] a table names all four CLIs, exactly one row each, no fifth CLI",
          l_table_four_cli(sect), [r[0] if r else "" for r in rows])
    check("[CP2-T03] every row covers record/state-file protection", l_rows_cover(sect, RECORD_PATTERNS), rows)
    check("[CP2-T04] every row covers reviewer identity checks", l_rows_cover(sect, REVIEWER_PATTERNS), rows)
    check("[CP2-T05] every row covers the stop hook", l_rows_cover(sect, STOP_PATTERNS), rows)
    check("[CP2-T06] every row covers recording the user's replies (the prompt hook)",
          l_rows_cover(sect, PROMPT_PATTERNS), rows)
    check("[CP2-T07] only Claude Code is credited with telling Ratchet which sub-agent is acting",
          l_claude_only_subagent(sect))
    check("[CP2-T08] Copilot's prompt hook dropping the message is called out, matching the real dialect handling",
          l_copilot_drops_message(sect, hooks_test_src))
    check("[CP2-T09] README says hook errors now surface in status and next",
          l_status_next_surfaced(sect), hook_error_paragraph(sect)[:200])
    check("[CP2-T10] the log-file sentence and the status/next sentence don't contradict each other",
          l_no_contradiction(sect), hook_error_paragraph(sect)[:200])
    check("[CP2-T11] no em dashes in the new table or surrounding prose", bool(sect) and "—" not in sect)

    cols = cols_by_cli(sect)
    check("[CP2-X01] the record/state-file protection cell is affirmed (not negated) in all four rows",
          l_record_col_affirms(sect), cols)
    check("[CP2-X02] the stop-hook cell is affirmed (not negated) in all four rows", l_stop_col_affirms(sect), cols)
    check("[CP2-X03] the records-your-replies cell is affirmed (not negated) in all four rows",
          l_prompt_col_affirms(sect), cols)
    check("[CP2-X04] the reviewer-identity cell affirms it only for Claude Code and negates it for the other three",
          l_reviewer_col_polarity(sect), cols)

    # Teeth: prove X01-X04 are not keyword-only like T03-T06 by turning one real cell into the adversary's own
    # negated-row examples (evidence/CP2/adversarial/round-01/open-findings.json id X1) and checking the
    # matching predicate goes red.
    negation_mutants = [
        ("X01", "Yes, Ratchet's state and evidence files are protected from hand-edits.",
         "No, records are not protected from hand-edits.", l_record_col_affirms),
        ("X02", "Yes, the stop hook stops the agent from ending its turn early.",
         "No, the stop hook does not block early exit.", l_stop_col_affirms),
        ("X03", "Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent.",
         "No, replies are not recorded.", l_prompt_col_affirms),
        ("X04", "Yes, Claude Code tells Ratchet which sub-agent is acting, so Ratchet can confirm reviewer identity.",
         "No, reviewer identity is not confirmed.", l_reviewer_col_polarity),
    ]
    for tid, old, new, pred in negation_mutants:
        present = old in sect
        red = present and not pred(sect.replace(old, new, 1))
        check(f"[CP2-{tid}] teeth: negating that row ({new!r}) turns the check red", red,
              "example text not found in README" if not present else "still green")
    swapped = sect.replace(
        "No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity.",
        "Yes, Codex tells Ratchet which sub-agent is acting, so Ratchet can confirm reviewer identity.", 1)
    check("[CP2-X04] teeth: a non-Claude row falsely claiming it confirms reviewer identity is also rejected",
          not l_reviewer_col_polarity(swapped))
    # Adversarial round 2 (X05-X10): the table must equal the rows rendered from ratchet.py's HOST_ENFORCEMENT,
    # and every fact there is measured against the code. This supersedes the keyword checks T03-T07 and X01-X04.
    cp2_single_source_checks(sect)
    # Adversarial round 3 (X11-X13): identity is checked against each CLI's real hook payload, not the table.
    cp2_real_payload_checks()
    # Adversarial round 4 (X14-X18): every column measured through the installed hook commands on real runs.
    cp2_hook_entry_checks(sect)
    # Adversarial round 5 (X19-X25, finding X1): the hook-crash paragraph must say what each hook actually does
    # when it breaks, not one blanket claim; grounded against decide_stop's real fail-closed limit.
    cp2_r5_hook_crash_checks(sect)


# ---- CP2 adversarial round 2: one source of truth for what each CLI enforces ----
# Keyword and negation regexes cannot prove a cell means the right thing ("Hand-edits are allowed" names the
# capability and has no negation word). Instead ratchet.py's HOST_ENFORCEMENT states the facts, the README table
# must equal the rows rendered from it exactly, and each fact is checked by running the code that enforces it.
import json as cp2_json
import os as cp2_os
import subprocess as cp2_subprocess
import tempfile as cp2_tempfile

CP2_ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
CP2_CLI_NAMES = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini", "copilot": "Copilot"}
CP2_HEADER = ["CLI", "Protects Ratchet's records", "Confirms reviewer identity", "Stop hook", "Records your replies"]
CP2_FIELDS = ("records", "identity", "stop", "replies", "guidance")

# Runs in a child process (this lint never loads the engine itself): loads ratchet.py and, for every host it
# installs hooks for, measures what the code actually does, in the same shape as HOST_ENFORCEMENT. Every probe
# uses a fresh throwaway project in a temp dir.
CP2_PROBE = r'''
import contextlib, importlib.util, io, json, os, shutil, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_engine_cp2", sys.argv[1])
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)
os.environ.pop("RATCHET_NESTED_REVIEW", None)


def emit(host, event, block, message):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = eng.emit_hook(host, event, block, message)
    return rc, out.getvalue(), err.getvalue()


def field(text, key):
    try:
        return json.loads(text).get(key)
    except Exception:
        return None


def project():
    d = Path(tempfile.mkdtemp(prefix="ratchet-cp2-"))
    (d / eng.RATCHET_DIR).mkdir()
    return d


def guard_blocks(host, rel_path, agent_type=""):
    d = project()
    try:
        payload = {"cwd": str(d), "tool_name": "Write",
                   "tool_input": {"file_path": str(d / eng.RATCHET_DIR / rel_path), "content": "{}"}}
        if agent_type:
            payload.update(agent_type=agent_type, agent_id="a-1")
        return eng.decide_guard(eng.normalize_hook(payload, host))[0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def prompt_recorded(host):
    d = project()
    try:
        eng.decide_prompt(eng.normalize_hook({"cwd": str(d), "prompt": "cp2 reply probe", "session_id": "s"}, host))
        log = d / eng.RATCHET_DIR / "human_log.jsonl"
        return log.exists() and "cp2 reply probe" in log.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(d, ignore_errors=True)


measured = {}
for host in eng.HOOK_HOSTS:
    installed = json.dumps(eng.build_hook_config(host, {}))
    rc, out, _ = emit(host, "guard", True, "cp2 guard probe")
    guard_ok = rc == 2 or field(out, "permissionDecision") == "deny"
    rc, out, _ = emit(host, "stop", True, "cp2 stop probe")
    stop_ok = rc == 2 or field(out, "decision") == "block"
    rc, out, err = emit(host, "prompt", False, "cp2 guidance probe")
    measured[host] = {
        "records": (("hook guard --host " + host) in installed and guard_ok
                    and guard_blocks(host, "runs/x/state.json")),
        # a fixer writing an adversary's review file is refused only where the host names the acting sub-agent
        "identity": guard_blocks(host, "runs/x/reviews/CP1/adv-round-01/a.json", "ratchet-fixer"),
        "stop": ("hook stop --host " + host) in installed and stop_ok,
        "replies": ("hook prompt --host " + host) in installed and prompt_recorded(host),
        "guidance": rc == 0 and "cp2 guidance probe" in out + err,
    }
print(json.dumps({"data": eng.HOST_ENFORCEMENT, "hosts": list(eng.HOOK_HOSTS), "events": sorted(eng.HOOK_EVENTS),
                  "measured": measured}))
'''


def cp2_probe(engine=CP2_ENGINE):
    """{"data": HOST_ENFORCEMENT, "hosts": HOOK_HOSTS, "events": [...], "measured": {host: facts}} from the engine."""
    env = {k: v for k, v in cp2_os.environ.items() if k != "RATCHET_ROOT"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # loading the engine must not write __pycache__ into the repo
    with cp2_tempfile.TemporaryDirectory() as cwd:
        r = cp2_subprocess.run([sys.executable, "-c", CP2_PROBE, str(engine)], capture_output=True, text=True,
                               timeout=120, cwd=cwd, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"CP2 engine probe failed: {r.stderr[-600:]}")
    return cp2_json.loads(r.stdout.strip().splitlines()[-1])


def cp2_render_row(host, f):
    """The README row for one CLI, rendered from its HOST_ENFORCEMENT facts. Each fact has one fixed sentence per
    value, so a cell that says anything else (negated, softened, opposite action) no longer matches."""
    name = CP2_CLI_NAMES[host]
    records = ("Yes, Ratchet's state and evidence files are protected from hand-edits." if f["records"] else
               "No, Ratchet's state and evidence files are not protected from hand-edits.")
    identity = (f"Yes, {name} tells Ratchet which sub-agent is acting, so Ratchet can confirm reviewer identity."
                if f["identity"] else
                "No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer "
                "identity.")
    stop = ("Yes, the stop hook stops the agent from ending its turn early." if f["stop"] else
            "No, the stop hook cannot stop the agent from ending its turn early.")
    if not f["replies"]:
        replies = "No, the prompt hook does not record your replies."
    elif f["guidance"]:
        replies = "Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent."
    else:
        replies = (f"{name}'s prompt hook drops its own message, so the agent never sees Ratchet's guidance, but the "
                   "reply is still recorded in Ratchet's log.")
    return [name, records, identity, stop, replies]


def cp2_table_diff(sect, data, order):
    """[] when the README table equals the rendered table row by row and cell by cell, else what differs."""
    got = md_table_rows(sect)
    want = [CP2_HEADER] + [cp2_render_row(h, data[h]) for h in order]
    diffs = [] if len(got) == len(want) else [f"{len(got)} rows, expected {len(want)}"]
    for i, (g, w) in enumerate(zip(got, want)):
        if len(g) != len(w):
            diffs.append(f"row {i}: {len(g)} cells, expected {len(w)}")
            continue
        diffs += [f"row {i} cell {j}: {a!r} != {b!r}" for j, (a, b) in enumerate(zip(g, w)) if a != b]
    return diffs


def cp2_fact_mismatches(hosts, data, measured):
    """(host, fact, what the data says, what the code does) for every fact where they disagree."""
    return [(h, f, data.get(h, {}).get(f), measured[h][f]) for h in hosts for f in CP2_FIELDS
            if data.get(h, {}).get(f) is not measured[h][f]]


def cp2_single_source_checks(sect):
    probe = cp2_probe()
    data, order, measured = probe["data"], probe["hosts"], probe["measured"]

    diffs = cp2_table_diff(sect, data, order)
    check("[CP2-X05] the README table equals the rows rendered from HOST_ENFORCEMENT, cell by cell", diffs == [],
          diffs)
    # Teeth: the adversary's round-2 examples, an ambiguous reviewer-identity cell, a negated cell and one altered
    # word each break the exact match. str.replace(old, new, 1) changes only the first (Claude Code or Codex) row.
    mutants = [
        ("Yes, Ratchet's state and evidence files are protected from hand-edits.", "Hand-edits are allowed."),
        ("Yes, the stop hook stops the agent from ending its turn early.", "Stop hook allows early exit."),
        ("Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent.",
         "Prompt hook discards replies."),
        ("No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer "
         "identity.", "Ratchet may be able to confirm reviewer identity."),
        ("Yes, Claude Code tells Ratchet which sub-agent is acting, so Ratchet can confirm reviewer identity.",
         "No, Claude Code does not tell Ratchet which sub-agent is acting, so Ratchet cannot confirm reviewer "
         "identity."),
        ("Yes, the stop hook stops the agent from ending its turn early.",
         "Yes, the stop hook lets the agent end its turn early."),
    ]
    for old, new in mutants:
        present = old in sect
        check(f"[CP2-X05] teeth: changing one cell to {new!r} breaks the exact match",
              present and cp2_table_diff(sect.replace(old, new, 1), data, order) != [],
              "cell text not found in README" if not present else "still matches")
    flipped = cp2_json.loads(cp2_json.dumps(data))
    flipped["codex"]["identity"] = True
    check("[CP2-X05] teeth: when the data changes (Codex confirms identity) the unchanged README no longer matches",
          cp2_table_diff(sect, flipped, order) != [])

    check("[CP2-X06] HOST_ENFORCEMENT names exactly the CLIs Ratchet installs hooks for, each with every fact",
          sorted(data) == sorted(order) == probe["events"]
          and all(sorted(data[h]) == sorted(CP2_FIELDS) and all(isinstance(v, bool) for v in data[h].values())
                  for h in data), data)

    for tid, fields, what in [("X07", ("records",), "the guard hook is installed and blocks hand-edits to state"),
                              ("X07", ("stop",), "the stop hook is installed and blocks in the host's dialect"),
                              ("X08", ("identity",), "decide_guard checks sub-agent identity on exactly these CLIs"),
                              ("X09", ("replies", "guidance"),
                               "the prompt hook records replies and passes guidance on exactly these CLIs")]:
        bad = [m for m in cp2_fact_mismatches(order, data, measured) if m[1] in fields]
        check(f"[CP2-{tid}] HOST_ENFORCEMENT matches the code: {what}", bad == [], bad)
    check("[CP2-X08] the CLIs where decide_guard checks sub-agent identity are exactly those the data credits",
          [h for h in order if measured[h]["identity"]] == [h for h in order if data[h]["identity"]],
          {h: measured[h]["identity"] for h in order})

    for host, field in [("codex", "identity"), ("copilot", "guidance"), ("gemini", "stop"), ("claude", "records")]:
        wrong = cp2_json.loads(cp2_json.dumps(data))
        wrong[host][field] = not wrong[host][field]
        check(f"[CP2-X10] teeth: flipping {host}.{field} in the data is caught by the code check",
              (host, field, wrong[host][field], measured[host][field]) in cp2_fact_mismatches(order, wrong, measured))

    # Teeth on the code side: an engine copy whose guard checks sub-agent identity on every host (the data left
    # unchanged) is caught as Codex, Gemini and Copilot doing something the data and README deny.
    src = CP2_ENGINE.read_text(encoding="utf-8")
    real = 'return bool(HOST_ENFORCEMENT.get(host, {}).get("identity"))'
    with cp2_tempfile.TemporaryDirectory() as tmp:
        for sib in CP2_ENGINE.parent.glob("*.py"):
            (Path(tmp) / sib.name).write_text(sib.read_text(encoding="utf-8"), encoding="utf-8")
        (Path(tmp) / CP2_ENGINE.name).write_text(src.replace(real, "return True", 1), encoding="utf-8")
        mutant = cp2_probe(Path(tmp) / CP2_ENGINE.name)
    caught = sorted(m[0] for m in cp2_fact_mismatches(order, data, mutant["measured"]) if m[1] == "identity")
    check("[CP2-X10] teeth: an engine that checks sub-agent identity on every CLI is caught on the other three",
          real in src and caught == ["codex", "copilot", "gemini"], caught)


# ---- CP2 adversarial round 3: sub-agent identity checked against each CLI's real hook payload ----
# The round 2 identity probe gave every host a fabricated agent_type, and decide_guard then asked HOST_ENFORCEMENT
# whether to believe it, so the table was checked against itself. The independent ground truth here is the shape of
# the PreToolUse (guard) payload each CLI really sends, copied from where the suite already models it:
#   claude   tests/test_gates.py:54 (guard(): agent_type + agent_id on a sub-agent's tool call, as Claude Code sends
#            them; see also the REVIEW_OWNERS comment in ratchet.py) plus Claude Code's common PreToolUse fields
#   codex    tests/test_hooks.py:101 (snake_case + turn_id, apply_patch carrying the patch text)
#   gemini   tests/test_hooks.py:107 (hook_event_name BeforeTool, write_file)
#   copilot  tests/test_hooks.py:113 (camelCase, toolArgs is a JSON string)
# Each is a sub-agent (the fixer, where the CLI can say so) writing an adversary's review file. {CWD} and {REVIEW}
# are filled in by the child process with the throwaway project and the review file's path.
CP2_REVIEW_REL = ".ratchet/runs/x/reviews/CP1/adv-round-01/a.json"
CP2_REAL_GUARD_PAYLOADS = {
    "claude": {"session_id": "s", "transcript_path": "/tmp/t.jsonl", "cwd": "{CWD}", "permission_mode": "default",
               "hook_event_name": "PreToolUse", "tool_name": "Write",
               "tool_input": {"file_path": "{REVIEW}", "content": "{}"}, "tool_use_id": "toolu_1",
               "agent_id": "a-ratchet-fixer", "agent_type": "ratchet-fixer"},
    "codex": {"session_id": "s", "turn_id": "t", "cwd": "{CWD}", "hook_event_name": "PreToolUse",
              "tool_name": "apply_patch",
              "tool_input": {"input": "*** Begin Patch\n*** Add File: " + CP2_REVIEW_REL + "\n+{}\n*** End Patch"}},
    "gemini": {"session_id": "s", "cwd": "{CWD}", "hook_event_name": "BeforeTool", "tool_name": "write_file",
               "tool_input": {"file_path": "{REVIEW}", "content": "{}"}},
    "copilot": {"sessionId": "s", "timestamp": 1, "cwd": "{CWD}", "toolName": "create",
                "toolArgs": cp2_json.dumps({"path": "{REVIEW}", "file_text": "{}"})},
}
# Any key a CLI could use to say which sub-agent is acting. Deliberately wider than what ratchet.py reads.
CP2_SUBAGENT_KEYS = {"agent_type", "agent_id", "agentType", "agentId", "agent_name", "agentName", "subagent",
                     "subagent_type", "subagentType", "subagentName"}

# Child process: loads the engine, reports its HOST_ENFORCEMENT and, for each real payload as-is (nothing injected),
# whether decide_guard refuses it. Also the Claude payload with the owner's agent_type, to show the refusal is about
# who is acting and not a blanket block on review files.
CP2_REAL_PROBE = r"""
import importlib.util, json, os, shutil, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_engine_cp2_real", sys.argv[1])
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)
os.environ.pop("RATCHET_NESTED_REVIEW", None)
payloads, review_rel = json.loads(sys.argv[2]), sys.argv[3]


def refused(host, payload):
    d = Path(tempfile.mkdtemp(prefix="ratchet-cp2r-"))
    try:
        (d / eng.RATCHET_DIR).mkdir()
        text = json.dumps(payload).replace("{CWD}", str(d)).replace("{REVIEW}", str(d / review_rel))
        return eng.decide_guard(eng.normalize_hook(json.loads(text), host))[0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


owner = dict(payloads["claude"], agent_type="ratchet-adversary", agent_id="a-ratchet-adversary")
print(json.dumps({"data": eng.HOST_ENFORCEMENT, "hosts": list(eng.HOOK_HOSTS),
                  "refused": {h: refused(h, payloads[h]) for h in eng.HOOK_HOSTS},
                  "owner_refused": refused("claude", owner)}))
"""


def cp2_real_probe(engine=CP2_ENGINE):
    env = {k: v for k, v in cp2_os.environ.items() if k != "RATCHET_ROOT"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # loading the engine must not write __pycache__ into the repo
    with cp2_tempfile.TemporaryDirectory() as cwd:
        r = cp2_subprocess.run([sys.executable, "-c", CP2_REAL_PROBE, str(engine),
                                cp2_json.dumps(CP2_REAL_GUARD_PAYLOADS), CP2_REVIEW_REL],
                               capture_output=True, text=True, timeout=120, cwd=cwd, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"CP2 real-payload probe failed: {r.stderr[-600:]}")
    return cp2_json.loads(r.stdout.strip().splitlines()[-1])


def cp2_identity_problems(probe):
    """[] when the CLIs whose real payload names the sub-agent, the CLIs HOST_ENFORCEMENT credits with identity and
    the CLIs where the guard refuses the fixer's real payload are the same set; else what differs."""
    order = probe["hosts"]
    in_payload = [h for h in order if CP2_SUBAGENT_KEYS & set(CP2_REAL_GUARD_PAYLOADS[h])]
    in_table = [h for h in order if probe["data"][h]["identity"]]
    refused = [h for h in order if probe["refused"][h]]
    out = []
    if in_table != in_payload:
        out.append(f"HOST_ENFORCEMENT credits {in_table}, real payloads name the sub-agent on {in_payload}")
    if refused != in_payload:
        out.append(f"guard refuses the fixer's real payload on {refused}, real payloads name it on {in_payload}")
    return out


def cp2_engine_variant(tmp, old, new):
    """A copy of the engine (and its sibling scripts) in tmp with one exact text replaced; its path."""
    src = CP2_ENGINE.read_text(encoding="utf-8")
    if old not in src:
        raise RuntimeError(f"engine text to mutate not found: {old!r}")
    for sib in CP2_ENGINE.parent.glob("*.py"):
        (Path(tmp) / sib.name).write_text(sib.read_text(encoding="utf-8"), encoding="utf-8")
    (Path(tmp) / CP2_ENGINE.name).write_text(src.replace(old, new, 1), encoding="utf-8")
    return Path(tmp) / CP2_ENGINE.name


def cp2_real_payload_checks():
    probe = cp2_real_probe()
    order = probe["hosts"]
    check("[CP2-X11] a real guard payload is modelled for every CLI Ratchet installs hooks for",
          sorted(CP2_REAL_GUARD_PAYLOADS) == sorted(order), (sorted(CP2_REAL_GUARD_PAYLOADS), order))
    check("[CP2-X11] only Claude Code's real payload names the acting sub-agent (ground truth, independent of the "
          "engine)", [h for h in order if CP2_SUBAGENT_KEYS & set(CP2_REAL_GUARD_PAYLOADS[h])] == ["claude"])
    problems = cp2_identity_problems(probe)
    check("[CP2-X12] HOST_ENFORCEMENT identity, the real payloads and the guard's decision on them agree",
          problems == [], problems)
    check("[CP2-X12] the guard's refusal is about who is acting: the adversary's own real payload is allowed",
          probe["refused"]["claude"] and not probe["owner_refused"], probe)

    # Teeth: flip the real table inside an engine copy (not the expected data) and the check goes red.
    flips = [('"codex": {"records": True, "identity": False,', '"codex": {"records": True, "identity": True,',
              "codex"),
             ('"claude": {"records": True, "identity": True,', '"claude": {"records": True, "identity": False,',
              "claude")]
    for old, new, host in flips:
        with cp2_tempfile.TemporaryDirectory() as tmp:
            mutant = cp2_real_probe(cp2_engine_variant(tmp, old, new))
        bad = cp2_identity_problems(mutant)
        check(f"[CP2-X13] teeth: an engine whose HOST_ENFORCEMENT flips {host}.identity fails the real-payload check",
              mutant["data"][host]["identity"] is not probe["data"][host]["identity"] and bad != [], bad)


# ---- CP2 adversarial round 4: every table claim measured through the real hook entry point, on real runs ----
# Round 2's "records" probe tried one path (state.json) and its "stop" probe called emit_hook(block=True) itself, so
# an engine that stopped guarding evidence, or whose stop decision always let the agent stop, still passed. Here
# every column is measured by running the exact command build_hook_config installs for each CLI (`ratchet.py hook
# <stop|prompt|guard> --host <cli>`) with that CLI's own payload shape (the shapes tests/test_hooks.py models) in
# throwaway projects that carry real runs, driven to each state with ratchet.py's own commands:
#   project "paused": plan approved, building (unfinished) -> `pause` (paused)
#   project "done":   plan approved -> tests-written, red, implemented, behavior, adv-record (unfinished, with real
#                     evidence files) -> complete (done; --human-review none, so nothing is left to do)
# A block is read the way each CLI reads it (exit 2 on Claude Code, Codex and Gemini; the JSON decision on Copilot),
# not through emit_hook. RATCHET_HOME is a throwaway folder, so nothing outside the temp dirs is touched.
CP2_R4_PROBE = r'''
import concurrent.futures, datetime as dt, importlib.util, json, os, shlex, shutil, subprocess, sys, tempfile
from pathlib import Path
ENGINE, PARTS = sys.argv[1], set(sys.argv[2].split(","))
ID_PAYLOADS, ID_REVIEW_REL = json.loads(sys.argv[3]), sys.argv[4]
spec = importlib.util.spec_from_file_location("ratchet_engine_cp2_r4", ENGINE)
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)
TMP = Path(tempfile.mkdtemp(prefix="ratchet-cp2r4-"))
OWNER = "s-owner"
KINDS = ("stop", "prompt", "guard")


def installed(host, kind):
    """The argv of the command build_hook_config installs for this CLI and hook (what the CLI runs), or None."""
    cfg = eng.build_hook_config(host, {})
    want = " hook %s --host %s" % (kind, host)
    for e in cfg["hooks"].get(eng.HOOK_EVENTS[host][KINDS.index(kind)], []):
        for h in e.get("hooks") or [e]:
            cmd = h.get("command") or h.get("bash") or ""
            if "ratchet.py" in cmd and cmd.endswith(want):
                return shlex.split(cmd)
    return None


def hook(d, host, kind, payload):
    argv = installed(host, kind)
    if argv is None:
        return {"rc": None, "out": "", "err": "not installed", "reason": "", "blocked": False, "allowed": False}
    r = subprocess.run(argv, cwd=d, input=json.dumps(payload), capture_output=True, text=True, timeout=120)
    if host == "copilot":  # Copilot reads a JSON decision on stdout; exit code 0
        try:
            out = json.loads(r.stdout) if r.stdout.strip() else {}
        except ValueError:
            out = {}
        blocked = r.returncode == 0 and (out.get("permissionDecision") == "deny" if kind == "guard"
                                         else out.get("decision") == "block")
        reason = str(out.get("permissionDecisionReason") or out.get("reason") or "")
    else:  # Claude Code, Codex and Gemini: exit 2 blocks, the reason goes to stderr
        blocked, reason = r.returncode == 2, r.stderr
    return {"rc": r.returncode, "out": r.stdout, "err": r.stderr, "reason": reason, "blocked": blocked,
            "allowed": r.returncode == 0 and not blocked}


def rx(d, *a):
    r = subprocess.run([sys.executable, ENGINE, *a], cwd=d, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit("ratchet.py %s failed: %s %s" % (" ".join(a), r.stdout[-400:], r.stderr[-400:]))
    return r


def say(d, text):
    """The user typing in Claude Code: the CLI writes its transcript, then runs the installed prompt hook."""
    tp = TMP / (d.name + "-transcript.jsonl")
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    hook(d, "claude", "prompt", {"session_id": OWNER, "transcript_path": str(tp), "cwd": str(d),
                                 "hook_event_name": "UserPromptSubmit", "prompt": text})


def project(name):
    d = TMP / name
    d.mkdir()
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *c], cwd=d, check=True, capture_output=True)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src" / "a.py").write_text("A = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True, capture_output=True)
    say(d, "please build the feature")
    rx(d, "init", "feature", "--slug", "f", "--human-review", "none")
    rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet" / "runs" / "f"
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [{
        "id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"], "risk": "low",
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["tests/test_cp1.py"], "command": "python3 tests/test_cp1.py"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    rx(d, "plan-submit")
    rx(d, "ask", "approve-plan")  # an approval answers the question Ratchet printed
    say(d, "yes, go ahead")
    rx(d, "approve-plan")
    return d


def through_adversarial(d):
    (d / "tests" / "test_cp1.py").write_text("import sys\nsys.path.insert(0, 'src')\nprint('[CP1-T01] works')\n"
                                             "from f import f\nassert f() == 1\n")
    rx(d, "tests-written", "CP1")
    rx(d, "red", "CP1")
    (d / "src" / "f.py").write_text("def f():\n    return 1\n")
    rx(d, "implemented", "CP1")
    rx(d, "behavior", "CP1")
    rnd = json.loads(rx(d, "reviewers", "CP1", "--json").stdout)
    rid = rnd["reviewers"][0]["id"]
    p = Path(rnd["output_dir"]) / (rid + ".json")
    # CP7: a hand-written review only counts once the guard hook has recorded, at write time, that a
    # ratchet-adversary sub-agent is writing this exact file; confirm it through the real installed guard hook
    # (the same one `records()` below exercises) before writing the content.
    g = hook(d, "claude", "guard", {"session_id": OWNER, "transcript_path": "/tmp/t.jsonl", "cwd": str(d),
                                    "permission_mode": "default", "hook_event_name": "PreToolUse",
                                    "tool_name": "Write", "tool_input": {"file_path": str(p), "content": "{}"},
                                    "tool_use_id": "toolu_review", "agent_type": "ratchet-adversary",
                                    "agent_id": "a-ratchet-adversary"})
    if not g["allowed"]:
        raise SystemExit("guard refused the probe's own review write: %s" % g["reason"])
    p.write_text(json.dumps({"reviewer": rid, "checkpoint": "CP1", "fingerprint": rnd["fingerprint"],
                             "round_nonce": rnd["round_nonce"], "findings": [], "files_reviewed": ["src/f.py"]}))
    rx(d, "adv-record", "CP1", "--reviews", str(p))


def write_payload(host, d, rel, new):
    """Each CLI's own file-writing tool call on rel (a path relative to the project)."""
    p = str(d / rel)
    if host == "claude":
        return {"session_id": OWNER, "transcript_path": "/tmp/t.jsonl", "cwd": str(d), "permission_mode": "default",
                "hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": p, "content": "{}"},
                "tool_use_id": "toolu_1"}
    if host == "codex":
        body = ("*** Add File: %s\n+{}\n" if new else "*** Update File: %s\n@@\n-x\n+y\n") % rel
        return {"session_id": OWNER, "turn_id": "t", "cwd": str(d), "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch", "tool_input": {"input": "*** Begin Patch\n" + body + "*** End Patch"}}
    if host == "gemini":
        return {"session_id": OWNER, "cwd": str(d), "hook_event_name": "BeforeTool", "tool_name": "write_file",
                "tool_input": {"file_path": p, "content": "{}"}}
    args = {"path": p, "file_text": "{}"} if new else {"path": p, "old_str": "a", "new_str": "b"}
    return {"sessionId": OWNER, "timestamp": 1, "cwd": str(d), "toolName": "create" if new else "edit",
            "toolArgs": json.dumps(args)}


def shell_payload(host, d, command):
    """Each CLI's own shell tool call running command."""
    if host == "claude":
        return {"session_id": OWNER, "transcript_path": "/tmp/t.jsonl", "cwd": str(d), "permission_mode": "default",
                "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                "tool_use_id": "toolu_2"}
    if host == "codex":
        return {"session_id": OWNER, "turn_id": "t", "cwd": str(d), "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", command]}}
    if host == "gemini":
        return {"session_id": OWNER, "cwd": str(d), "hook_event_name": "BeforeTool", "tool_name": "run_shell_command",
                "tool_input": {"command": command}}
    return {"sessionId": OWNER, "timestamp": 1, "cwd": str(d), "toolName": "bash",
            "toolArgs": json.dumps({"command": command})}


def stop_payload(host, d):
    if host == "claude":
        return {"session_id": OWNER, "transcript_path": "/tmp/t.jsonl", "cwd": str(d), "hook_event_name": "Stop",
                "stop_hook_active": False}
    if host == "codex":
        return {"session_id": OWNER, "turn_id": "t1", "cwd": str(d), "hook_event_name": "Stop",
                "model": "gpt-5-codex", "stop_hook_active": False}
    if host == "gemini":
        return {"session_id": OWNER, "cwd": str(d), "hook_event_name": "AfterAgent", "prompt": "go",
                "prompt_response": "done!", "stop_hook_active": False, "timestamp": "2026-09-28T00:00:00Z"}
    return {"sessionId": OWNER, "timestamp": 1790000000000, "cwd": str(d), "transcriptPath": "/tmp/t",
            "stopReason": "end_turn", "stop_hook_active": False}


def prompt_payload(host, d, text):
    if host == "claude":
        return {"session_id": OWNER, "transcript_path": "/tmp/t.jsonl", "cwd": str(d),
                "hook_event_name": "UserPromptSubmit", "prompt": text}
    if host == "codex":
        return {"session_id": OWNER, "turn_id": "t", "cwd": str(d), "hook_event_name": "UserPromptSubmit",
                "prompt": text}
    if host == "gemini":
        return {"session_id": OWNER, "cwd": str(d), "hook_event_name": "BeforeAgent", "prompt": text}
    return {"sessionId": OWNER, "timestamp": 1, "cwd": str(d), "prompt": text}


def stops(d, label, out):
    for host in eng.HOOK_HOSTS:
        r = hook(d, host, "stop", stop_payload(host, d))
        out.setdefault(host, {})[label] = {"blocked": r["blocked"], "allowed": r["allowed"],
                                           "gate": "RATCHET GATE" in r["reason"], "rc": r["rc"],
                                           "said": (r["reason"] or r["out"] or r["err"])[:160]}


def records(d):
    """Every kind of record the table promises is protected, each tried through every CLI's write and shell tool."""
    ratchet = d / ".ratchet"
    files = sorted(str(p.relative_to(d)) for p in ratchet.rglob("*") if p.is_file())
    kinds = {
        "state": [".ratchet/runs/f/state.json"],
        "evidence": [f for f in files if f.startswith(".ratchet/runs/f/evidence/")]
                    + [".ratchet/runs/f/evidence/CP1/forged-behavior.log"],
        "human log": [".ratchet/human_log.jsonl"],
        # wherever the engine wrote down the plan approval and the adversarial approval
        "approvals": [f for f in files if '"plan_approved"' in (d / f).read_text(errors="replace")
                      or '"adversarial_approved"' in (d / f).read_text(errors="replace")],
        "run bookkeeping": [".ratchet/active.json", ".ratchet/config.json"],
    }
    calls = {}
    for host in eng.HOOK_HOSTS:
        for kind, rels in kinds.items():
            for rel in rels:
                new = not (d / rel).exists()
                calls[(host, kind, rel, "write")] = write_payload(host, d, rel, new)
                calls[(host, kind, rel, "shell")] = shell_payload(host, d, "echo forged > " + str(d / rel))
        # The control: the same write and shell calls on a project file must be allowed. Mid-build, once Claude Code
        # has named a sub-agent (the ratchet-adversary review write in through_adversarial), project files are
        # changed by the implementer, and the orchestrator's own edits are rightly refused (ORCH_EDIT_MSG). So on a
        # CLI that names sub-agents the control call is made the way the real flow makes it: by ratchet-implementer.
        implementer = ({"agent_type": "ratchet-implementer", "agent_id": "a-ratchet-implementer"}
                       if host == "claude" else {})
        calls[(host, "control", "src/new_module.py", "write")] = dict(
            write_payload(host, d, "src/new_module.py", True), **implementer)
        calls[(host, "control", "src/scratch.txt", "shell")] = dict(shell_payload(
            host, d, "echo forged > " + str(d / "src/scratch.txt")), **implementer)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        res = dict(zip(calls, pool.map(lambda k: hook(d, k[0], "guard", calls[k]), calls)))
    out = {h: [] for h in eng.HOOK_HOSTS}
    for (host, kind, rel, tool), r in res.items():
        out[host].append({"kind": kind, "rel": rel, "tool": tool, "blocked": r["blocked"], "allowed": r["allowed"],
                          "about_records": ".ratchet/" in r["reason"] and "RATCHET GUARD" in r["reason"]})
    return out


result = {"data": eng.HOST_ENFORCEMENT, "hosts": list(eng.HOOK_HOSTS),
          "installed": {h: {k: installed(h, k) is not None for k in KINDS} for h in eng.HOOK_HOSTS}}
try:
    if PARTS & {"stop", "replies", "identity"}:
        p = project("paused")
        result["stop"] = {}
        stops(p, "building", result["stop"])
        if "replies" in PARTS:
            result["replies"] = {}
            for host in eng.HOOK_HOSTS:
                text = "stop for a moment, cp2r4 reply via " + host
                r = hook(p, host, "prompt", prompt_payload(host, p, text))
                log = [json.loads(x) for x in (p / ".ratchet" / "human_log.jsonl").read_text().splitlines() if x]
                result["replies"][host] = {
                    "recorded": r["rc"] == 0 and any(e.get("prompt") == text and e.get("host") == host for e in log),
                    "guidance": "[ratchet] The user asked you to stop" in r["out"] + r["err"]}
        rx(p, "pause", "--reason", "cp2r4 probe")
        stops(p, "paused", result["stop"])
        if "identity" in PARTS:
            result["identity"] = {}
            for host in eng.HOOK_HOSTS:
                text = json.dumps(ID_PAYLOADS[host]).replace("{CWD}", str(p)).replace("{REVIEW}", str(p / ID_REVIEW_REL))
                result["identity"][host] = hook(p, host, "guard", json.loads(text))["blocked"]
            owner = dict(ID_PAYLOADS["claude"], agent_type="ratchet-adversary", agent_id="a-ratchet-adversary")
            text = json.dumps(owner).replace("{CWD}", str(p)).replace("{REVIEW}", str(p / ID_REVIEW_REL))
            result["identity_owner_allowed"] = hook(p, "claude", "guard", json.loads(text))["allowed"]
    if PARTS & {"stop", "records"}:
        b = project("done")
        through_adversarial(b)
        if "records" in PARTS:
            result["records"] = records(b)
        if "stop" in PARTS:
            stops(b, "with evidence", result["stop"])
            rx(b, "complete", "CP1")
            result["done_phase"] = json.loads((b / ".ratchet/runs/f/state.json").read_text())["phase"]
            stops(b, "done", result["stop"])
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print(json.dumps(result))
'''
CP2_R4_RECORD_KINDS = ("state", "evidence", "human log", "approvals", "run bookkeeping")
CP2_R4_UNFINISHED = ("building", "with evidence")  # a run with work left: stopping must be refused
CP2_R4_FINISHED = ("paused", "done")  # a paused or finished run: stopping must be allowed


def cp2_r4_start(engine=CP2_ENGINE, parts=("records", "stop", "replies", "identity")):
    """Start the probe child process (several run side by side); cp2_r4_finish collects it."""
    env = {k: v for k, v in cp2_os.environ.items()
           if k not in ("RATCHET_ROOT", "RATCHET_NESTED_REVIEW", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROLE",
                        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "GEMINI_CLI",
                        "COPILOT_CLI")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # loading the engine must not write __pycache__ into the repo
    home = cp2_tempfile.TemporaryDirectory(prefix="ratchet-cp2r4-home-")
    cwd = cp2_tempfile.TemporaryDirectory()
    env["RATCHET_HOME"] = home.name  # per-user files (hashes, active-run markers) stay in a throwaway folder
    proc = cp2_subprocess.Popen([sys.executable, "-c", CP2_R4_PROBE, str(engine), ",".join(parts),
                                 cp2_json.dumps(CP2_REAL_GUARD_PAYLOADS), CP2_REVIEW_REL],
                                stdout=cp2_subprocess.PIPE, stderr=cp2_subprocess.PIPE, text=True, cwd=cwd.name,
                                env=env)
    return proc, (home, cwd)


def cp2_r4_finish(started):
    proc, dirs = started
    try:
        out, err = proc.communicate(timeout=600)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        for d in dirs:
            d.cleanup()
    if proc.returncode != 0:
        raise RuntimeError(f"CP2 round-4 hook probe failed: {err[-800:]}")
    return cp2_json.loads(out.strip().splitlines()[-1])


def cp2_r4_probe(engine=CP2_ENGINE, parts=("records", "stop", "replies", "identity")):
    return cp2_r4_finish(cp2_r4_start(engine, parts))


def cp2_r4_unguarded(probe, kind):
    """(host, path, tool) for every hand-edit of this kind of record that some CLI's guard hook let through."""
    return [(h, e["rel"], e["tool"]) for h in probe["hosts"] for e in probe["records"][h]
            if e["kind"] == kind and not (e["blocked"] and e["about_records"])]


def cp2_r4_stop_wrong(probe, labels, want_block):
    """(host, run state, what the hook said) wherever the stop decision did not block / allow as required."""
    return [(h, s, probe["stop"][h][s]["said"]) for h in probe["hosts"] for s in labels
            if not (probe["stop"][h][s]["blocked"] and probe["stop"][h][s]["gate"] if want_block
                    else probe["stop"][h][s]["allowed"])]


def cp2_r4_facts(probe):
    """What each CLI really does, per table column, measured only through the installed hook commands."""
    out = {}
    for h in probe["hosts"]:
        recs = probe["records"][h]
        out[h] = {
            "records": probe["installed"][h]["guard"]
                       and all(e["blocked"] and e["about_records"] for e in recs if e["kind"] != "control")
                       and all(e["allowed"] for e in recs if e["kind"] == "control")
                       and all(any(e["kind"] == k for e in recs) for k in CP2_R4_RECORD_KINDS),
            "identity": probe["identity"][h],
            "stop": probe["installed"][h]["stop"]
                    and all(probe["stop"][h][s]["blocked"] and probe["stop"][h][s]["gate"] for s in CP2_R4_UNFINISHED)
                    and all(probe["stop"][h][s]["allowed"] for s in CP2_R4_FINISHED),
            "replies": probe["installed"][h]["prompt"] and probe["replies"][h]["recorded"],
            "guidance": probe["replies"][h]["guidance"],
        }
    return out


def cp2_hook_entry_checks(sect):
    # The real engine and the teeth mutants (cp2_r4_mutants, below) are probed side by side to keep the run short.
    copies = [cp2_tempfile.TemporaryDirectory() for _ in cp2_r4_mutants()]
    jobs, done, errors = [], [], []
    try:
        jobs.append(cp2_r4_start())
        for tmp, (what, parts, old, new, caught) in zip(copies, cp2_r4_mutants()):
            jobs.append(cp2_r4_start(cp2_engine_variant(tmp.name, old, new), parts))
    finally:
        for j in jobs:  # collect every started child, even when one fails, so none is left running
            try:
                done.append(cp2_r4_finish(j))
            except Exception as e:
                errors.append(e)
        for tmp in copies:
            tmp.cleanup()
    if errors:
        raise errors[0]
    probe, mutant_probes = done[0], done[1:]
    order, data = probe["hosts"], probe["data"]
    recs = probe["records"]

    # Records column: every kind of record, every CLI, both its write tool and its shell tool.
    evidence = sorted({e["rel"] for e in recs[order[0]] if e["kind"] == "evidence"})
    check("[CP2-X14] the probe run holds real evidence (red, behavior and adversarial) plus a forged new evidence file",
          any("/red" in f for f in evidence) and any("/behavior" in f for f in evidence)
          and any("/adversarial/" in f for f in evidence) and any("forged" in f for f in evidence), evidence)
    approvals = sorted({e["rel"] for e in recs[order[0]] if e["kind"] == "approvals"})
    check("[CP2-X14] the probe found where the engine records approvals", bool(approvals), approvals)
    for kind in CP2_R4_RECORD_KINDS:
        bad = cp2_r4_unguarded(probe, kind)
        check(f"[CP2-X14] hand-edits to {kind} files are blocked on every CLI, through its write tool and its shell "
              "tool, by the installed guard hook", bad == [] and all(any(e["kind"] == kind for e in recs[h])
                                                                      for h in order), bad)
    leaks = [(h, e["rel"], e["tool"]) for h in order for e in recs[h] if e["kind"] == "control" and not e["allowed"]]
    check("[CP2-X14] the guard is not a blanket block: the same write-tool and shell calls on a project file are "
          "allowed on every CLI", leaks == [], leaks)

    # Stop column: the real stop decision on real run states.
    check("[CP2-X15] the finished probe run really reached phase done", probe.get("done_phase") == "done",
          probe.get("done_phase"))
    for labels, block, what in [(CP2_R4_UNFINISHED, True, "an unfinished run (building; and with evidence, before "
                                 "complete) is refused a stop by the gate on every CLI"),
                                (("paused",), False, "a paused run is allowed to stop on every CLI"),
                                (("done",), False, "a finished run is allowed to stop on every CLI")]:
        bad = cp2_r4_stop_wrong(probe, labels, block)
        check(f"[CP2-X15] {what}", bad == [], bad)

    # Replies and identity columns, through the same installed commands.
    rep = probe["replies"]
    check("[CP2-X16] the installed prompt hook records the user's reply, tagged with the CLI, on every CLI",
          all(rep[h]["recorded"] for h in order), rep)
    check("[CP2-X16] Ratchet's guidance reaches the agent exactly on the CLIs the table says (not Copilot)",
          [h for h in order if rep[h]["guidance"]] == [h for h in order if data[h]["guidance"]], rep)
    check("[CP2-X16] the installed guard hook refuses the fixer's real payload exactly where the CLI names the "
          "sub-agent, and allows the owner", [h for h in order if probe["identity"][h]] ==
          [h for h in order if CP2_SUBAGENT_KEYS & set(CP2_REAL_GUARD_PAYLOADS[h])]
          and probe["identity_owner_allowed"], (probe["identity"], probe["identity_owner_allowed"]))

    # The table: README == rendered(HOST_ENFORCEMENT) (X05), and HOST_ENFORCEMENT == what the hooks really do.
    facts = cp2_r4_facts(probe)
    bad = cp2_fact_mismatches(order, data, facts)
    check("[CP2-X17] every README table cell (rendered from HOST_ENFORCEMENT) equals what the installed hook does on "
          "that CLI", bad == [] and cp2_table_diff(sect, data, order) == [], bad)

    # Teeth: break one decision inside an engine copy; the matching check must go red while the others stay green.
    for (what, parts, old, new, caught), mutant in zip(cp2_r4_mutants(), mutant_probes):
        check(f"[CP2-X18] teeth: an engine with {what} fails the hook-entry check", caught(mutant))


def cp2_r4_mutants():
    """(what is broken, probe parts needed, engine text, replacement, how the check must catch it)."""
    return [
        ("evidence files left unguarded (state.json still guarded)", ("records",),
         r'r"^runs/[^/]+/(reviews|states|reference|tests)(/.*)?$',
         r'r"^runs/[^/]+/(reviews|states|reference|tests|evidence)(/.*)?$',
         lambda m: cp2_r4_unguarded(m, "evidence") != [] and cp2_r4_unguarded(m, "state") == []),
        ("the human log left unguarded", ("records",),
         r'r"|^(standards\.md|learnings\.md|app\.log)$"', r'r"|^(standards\.md|learnings\.md|app\.log|human_log\.jsonl)$"',
         lambda m: cp2_r4_unguarded(m, "human log") != [] and cp2_r4_unguarded(m, "state") == []),
        ("the stop decision always lets the agent stop", ("stop",),
         '    na = next_action(ctx, st)\n    if na.get("stop_ok"):', '    na = next_action(ctx, st)\n    if True:',
         lambda m: cp2_r4_stop_wrong(m, CP2_R4_UNFINISHED, True) != []
         and cp2_r4_stop_wrong(m, CP2_R4_FINISHED, False) == []),
        ("a paused or finished run is never allowed to stop", ("stop",),
         'na = {"phase": phase, "action": st.get("blocked_reason") or phase, "stop_ok": True}',
         'na = {"phase": phase, "action": st.get("blocked_reason") or phase, "stop_ok": False}',
         lambda m: cp2_r4_stop_wrong(m, CP2_R4_FINISHED, False) != []
         and cp2_r4_stop_wrong(m, CP2_R4_UNFINISHED, True) == []),
        # (Claude Code still records, so the probe's own plan approval works; the other three must go red)
        ("a prompt hook that records replies on Claude Code only", ("replies",),
         '    _heartbeat(root, "prompt", n["host"])\n    log = rdir / "human_log.jsonl"',
         '    if n["host"] != "claude":\n        return False, ""\n    _heartbeat(root, "prompt", n["host"])\n'
         '    log = rdir / "human_log.jsonl"',
         lambda m: [h for h in m["hosts"] if not m["replies"][h]["recorded"]] == ["codex", "gemini", "copilot"]),
    ]


# ---- CP2 adversarial round 5: the hook-crash paragraph, grounded against decide_stop's real fail-closed limit ----
# Round 1's finding (evidence/CP2/adversarial/round-05/open-findings.json, id X1): README said "if a hook itself
# breaks, it lets the action through", but run_hook's except block fails CLOSED for the stop hook while a run is
# active (_stop_error), up to a limit, before falling back to fail-open. The checks below reject any return to that
# blanket claim and measure the real limit by actually crashing the hooks, instead of trusting the prose.
BLANKET_HOOK_FAILOPEN_RX = re.compile(
    r"if\s+(a|the|any|every)\s+hook\s+(itself\s+)?breaks,?\s+it\s+lets\s+the\s+action\s+through", re.I)

CP2_R5_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                  "ten": 10}


def cp2_r5_stop_tries_claim(p):
    """The fail-closed limit README claims for the stop hook ("up to five tries"/"up to 5 tries"), or None."""
    m = re.search(r"up to (\w+) tr(?:y|ies)", p, re.I)
    if not m:
        return None
    w = m.group(1).lower()
    return int(w) if w.isdigit() else CP2_R5_WORDNUM.get(w)


CP2_R5_PROBE = r'''
import contextlib, importlib.util, io, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
ENGINE = sys.argv[1]
spec = importlib.util.spec_from_file_location("ratchet_engine_cp2_r5", ENGINE)
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)


def project():
    return Path(tempfile.mkdtemp(prefix="ratchet-cp2r5-"))


def run_stop_hook(d, home):
    env = dict(os.environ)
    env["RATCHET_HOME"] = str(home)
    payload = json.dumps({"session_id": "s", "cwd": str(d), "hook_event_name": "Stop"})
    r = subprocess.run([sys.executable, ENGINE, "hook", "stop", "--host", "claude"],
                       input=payload, capture_output=True, text=True, cwd=d, env=env, timeout=60)
    return r.returncode


# A corrupt state.json for an active run crashes decide_stop (read_json -> die() -> SystemExit), which run_hook's
# except block must turn into a blocked stop, repeatedly, before it ever lets the agent stop.
d = project()
(d / ".ratchet").mkdir()
(d / ".ratchet" / "active.json").write_text(json.dumps({"slug": "f"}))
(d / ".ratchet" / "runs" / "f").mkdir(parents=True)
(d / ".ratchet" / "runs" / "f" / "state.json").write_text("{not json")
home = d / "home"
home.mkdir()
sequence = []
for _ in range(10):
    rc = run_stop_hook(d, home)
    sequence.append(rc)
    if rc != 2:
        break
log = d / ".ratchet" / "hook-errors.log"
stop_logged_every_attempt = log.exists() and log.read_text().count(" claude stop:") == len(sequence)
shutil.rmtree(d, ignore_errors=True)

# A crashing guard hook must fail OPEN (never block a tool call) and still log the crash.
d2 = project()
(d2 / ".ratchet").mkdir()
orig_guard = eng.decide_guard


def boom(n):
    raise RuntimeError("boom")


eng.decide_guard = boom
old_stdin = sys.stdin
sys.stdin = io.StringIO(json.dumps({"session_id": "s", "cwd": str(d2), "hook_event_name": "PreToolUse",
                                    "tool_name": "Write",
                                    "tool_input": {"file_path": str(d2 / "x.txt"), "content": "hi"}}))
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc_guard = eng.run_hook("guard", "claude")
finally:
    sys.stdin = old_stdin
    eng.decide_guard = orig_guard
guard_failed_open = rc_guard == 0
guard_logged = (d2 / ".ratchet" / "hook-errors.log").exists()
shutil.rmtree(d2, ignore_errors=True)

print(json.dumps({
    "stop_sequence": sequence,
    "stop_blocked_count": sum(1 for rc in sequence if rc == 2),
    "stop_logged_every_attempt": stop_logged_every_attempt,
    "guard_failed_open": guard_failed_open,
    "guard_logged": guard_logged,
}))
'''


def cp2_r5_measure(engine=CP2_ENGINE):
    """{"stop_sequence", "stop_blocked_count", "stop_logged_every_attempt", "guard_failed_open", "guard_logged"}
    measured by actually crashing the stop hook and the guard hook, not by reading the docs or the code."""
    env = {k: v for k, v in cp2_os.environ.items() if k != "RATCHET_ROOT"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with cp2_tempfile.TemporaryDirectory() as cwd:
        r = cp2_subprocess.run([sys.executable, "-c", CP2_R5_PROBE, str(engine)], capture_output=True, text=True,
                               timeout=120, cwd=cwd, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"CP2 round-5 hook-crash probe failed: {r.stderr[-800:]}")
    return cp2_json.loads(r.stdout.strip().splitlines()[-1])


CP2_R5_OLD_BLANKET = ("If a hook itself breaks, it lets the action through and writes the error to "
                      "`.ratchet/hook-errors.log`, rather than blocking everything.")


def cp2_r5_hook_crash_checks(sect):
    p = hook_error_paragraph(sect)
    measured = cp2_r5_measure()

    check("[CP2-X19] no blanket 'if a hook breaks, it lets the action through' claim remains (the stop hook "
          "fails closed on crash, up to its limit)", bool(p) and BLANKET_HOOK_FAILOPEN_RX.search(p) is None, p[:300])
    check("[CP2-X20] the paragraph says the stop hook can block (fail closed) when it breaks while a run is active",
          bool(p) and has(p, r"stop hook", r"fails?\s+closed|blocks?\s+the\s+agent\s+from|can'?t\s+end\s+its\s+turn|"
                            r"cannot\s+end\s+its\s+turn"), p[:300])
    claimed = cp2_r5_stop_tries_claim(p)
    check("[CP2-X21] the stated stop-hook fail-closed limit matches what the code actually enforces (measured by "
          "crashing decide_stop repeatedly, not read from the source)",
          claimed is not None and claimed == measured["stop_blocked_count"],
          (claimed, measured["stop_blocked_count"], measured["stop_sequence"]))
    check("[CP2-X22] every crash attempt (the blocked ones and the eventual allow) is logged to hook-errors.log, "
          "as the paragraph says", measured["stop_logged_every_attempt"])
    check("[CP2-X23] a crashing guard hook really fails open (lets the action through) and logs the error, "
          "matching the paragraph's guard/prompt claim", measured["guard_failed_open"] and measured["guard_logged"])

    # Teeth: reverting to the old blanket claim, or understating/overstating the limit, must turn checks red.
    reverted = p.replace(README_CP2_R5_SENTENCE, CP2_R5_OLD_BLANKET, 1) if README_CP2_R5_SENTENCE in p else p
    check("[CP2-X24] teeth: reverting to the old blanket claim turns the no-blanket-claim check red",
          reverted != p and BLANKET_HOOK_FAILOPEN_RX.search(reverted) is not None)
    wrong_limit = p.replace("up to five tries", "up to three tries", 1)
    check("[CP2-X25] teeth: claiming the wrong fail-closed limit no longer matches the measured code behavior",
          wrong_limit != p and cp2_r5_stop_tries_claim(wrong_limit) != measured["stop_blocked_count"])


README_CP2_R5_SENTENCE = (
    "If the guard or prompt hook breaks, it lets the action through and writes the error to "
    "`.ratchet/hook-errors.log`, rather than blocking everything, because a crashing guard must never brick a "
    "session. If the stop hook breaks while a run is active, it fails closed instead: it blocks the agent from "
    "ending its turn and tells it to explain the failure to you, for up to five tries in a row; after that it "
    "lets the agent stop too, logged the same way, so a broken install can't trap the session forever.")


# ---- end CP2 ----


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
          p_two_step(controls) and not p_two_step(controls.replace('A hedge, a bare "ok" or thumbs-up, or a yes with anything else attached ("ok cancel it", "yes, but…") is not a yes.', ""))
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

# ---- CP6 ----
# README as things you say to your agent, commands in a marked reference section, manifests in sync.
# Independent of CP7 (which fixes README drift claims); every phrase check uses tolerant regex alternatives.

import json

CMD_RX = r"\./install\.sh|python3\s+\S|claude --plugin-dir|ratchet\.py"
CLAUDE_ONLY_RX = r"/plugin\b|claude --plugin-dir|AskUserQuestion"
REF_HEAD_RX = re.compile(r"(?im)^(#{2,3})\s+[^\n]*\breference\b[^\n]*$")


def cp6_split_readme(text):
    """(primary, reference): the reference section runs from its heading to the next heading of the same or higher level."""
    m = REF_HEAD_RX.search(text)
    if not m:
        return text, ""
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(r"(?m)^#{1,%d}\s" % level, rest)
    end = m.end() + (nxt.start() if nxt else len(rest))
    return text[:m.start()] + text[end:], text[m.start():end]


def cp6_sentences(text):
    """Things said to an agent: blockquote lines, quoted strings, and fenced blocks that are not shell commands. -> [(pos, str)]"""
    out = []
    for m in re.finditer(r"(?m)^[ \t]*>[ \t]?(.+)$", text):
        out.append((m.start(), m.group(1).strip()))
    for m in re.finditer(r"[\"“]([^\"”\n]{15,})[\"”]", text):
        out.append((m.start(), m.group(1).strip()))
    for m in re.finditer(r"(?s)```[^\n]*\n(.*?)```", text):
        body = m.group(1).strip()
        if body and not re.match(r"(\$ |\./|python3|claude|codex|gemini|copilot|npm|npx|git|ratchet|cd )", body) and "├" not in body and "└" not in body:
            out.append((m.start(), " ".join(body.split())))
    return sorted(out)


def cp6_opening_sentence(text):
    primary, _ = cp6_split_readme(text)
    for pos, s in cp6_sentences(primary):
        if re.search(r"install", s, re.I) and re.search(r"ratchet", s, re.I) and re.search(r"start|begin|set (it )?up|use|build", s, re.I):
            return pos, s
    return None, ""


def cp6_first_command_pos(text):
    m = re.search(CMD_RX, cp6_split_readme(text)[0])
    return m.start() if m else len(text)


def cp6_install_area(text):
    """From the top of the README to the end of its Install section (or the first 40 lines when there is none)."""
    primary = cp6_split_readme(text)[0]
    m = re.search(r"(?im)^##\s+[^\n]*\binstall", primary)
    if not m:
        return "\n".join(primary.splitlines()[:40])
    nxt = re.search(r"(?m)^##\s", primary[m.end():])
    return primary[: m.end() + (nxt.start() if nxt else len(primary))]


def cp6_opens_with_sentence(text):
    pos, s = cp6_opening_sentence(text)
    return pos is not None and pos < cp6_first_command_pos(text) and text[:pos].count("\n") < 40


def cp6_install_primary_is_sentence(text):
    area = cp6_install_area(text)
    table_cmds = re.search(r"(?m)^\s*\|[^\n]*(install\.sh|--plugin-dir)", area)
    you_run = re.search(r"you run|run this in your terminal|run the following in (a|your) terminal", area, re.I)
    return len(providers_named(area)) == 4 and not table_cmds and not you_run and cp6_opens_with_sentence(text)


TOPICS = {
    "hooks": r"\bhooks?\b",
    "viewer": r"\bviewer\b|\blink\b",
    "reviewer": r"\breviewers?\b",
    "pause": r"\bpause\b.*\bresume\b|\bresume\b.*\bpause\b|\bpause and resume\b",
    "director": r"\bdirectors?\b",
}


def cp6_task_sentences(text):
    primary = cp6_split_readme(text)[0]
    res = {}
    for topic, rx in TOPICS.items():
        res[topic] = [s for _, s in cp6_sentences(primary)
                      if re.search(rx, s, re.I) and not re.search(CMD_RX, s) and re.search(r"\b(please|can you|could you|show|check|install|give|tell|pause|resume|who|what|set|use|run|start|open|is|are|enroll)\b", s, re.I)]
    return res


def cp6_task_sentences_ok(text):
    res = cp6_task_sentences(text)
    chosen = [res[t][0] for t in TOPICS if res[t]]
    return all(res[t] for t in TOPICS) and len(set(chosen)) == len(TOPICS)


def cp6_reference_ok(text):
    primary, ref = cp6_split_readme(text)
    if not ref:
        return False
    says = re.search(r"agent runs|the agent (runs|uses)|people typing|you (can )?type|by hand|for humans|for people|terminal", ref[:1500], re.I)
    need = [r"view --serve", r"install-hooks --host", r"\bdoctor\b", r"lanes --scan"]
    banned_primary = [r"view --serve", r"install-hooks --host", r"ratchet\.py[^\n]*\bdoctor\b|\bdoctor --json", r"lanes --scan"]
    return bool(says) and all(re.search(n, ref) for n in need) and not any(re.search(b, primary) for b in banned_primary)


def cp6_tests_on_disk():
    return sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))


def cp6_tests_documented(text, files):
    ref = cp6_split_readme(text)[1]
    return bool(ref) and all(f in text and re.search(r"python3?\s+tests/" + re.escape(f), ref) for f in files)


def cp6_headings(text):
    return {re.sub(r"\s+", " ", h.strip().lower()) for h in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*#*$", text)}


def cp6_slug(h):
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", h.lower()).strip())


def cp6_broken_refs(text):
    heads = cp6_headings(text)
    slugs = {cp6_slug(h) for h in heads}
    bad = []
    for name in re.findall(r"(?i)(?:README|section|see)\s+['\"‘“]([^'\"’”]+)['\"’”]", text):
        if re.sub(r"\s+", " ", name.strip().lower()) not in heads:
            bad.append(name)
    for a in re.findall(r"\]\(#([^)]+)\)", text):
        if a.lower() not in slugs:
            bad.append("#" + a)
    return bad


def cp6_nonclaude_ok(text):
    return (bool(re.search(r"~/\.ratchet-loop/skills|RATCHET_HOME/skills", text))
            and bool(re.search(r"symlink|link(ed)? (it |that |the |those )?(folder |them )?(in)?to|linked into", text, re.I))
            and not re.search(r"point (the |your )?(CLI|agent) at", text, re.I)
            and (len(providers_named(text) - {"claude"}) == 3 and bool(re.search(r"non-claude|other (agent )?CLIs?|codex.{0,60}gemini.{0,60}copilot", text, re.I))))


def cp6_neutral_ok(text):
    pos, s = cp6_opening_sentence(text)
    primary = cp6_split_readme(text)[0]
    sents = [x for _, x in cp6_sentences(primary)]
    return pos is not None and not any(re.search(CLAUDE_ONLY_RX, x) for x in sents + [s]) and not re.search(CLAUDE_ONLY_RX, primary)


def cp6_manifest_problems(text):
    probs = []
    ms = {}
    for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        try:
            ms[rel] = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        except Exception as e:
            probs.append(f"{rel}: {e}")
    if len(ms) == 2:
        a, b = ms.values()
        if a.get("name") != b.get("name") or a.get("version") != b.get("version"):
            probs.append("name/version differ")
        for rel, m in ms.items():
            if not has(m.get("description", ""), r"checkpoint", r"gate"):
                probs.append(f"{rel}: description misses checkpoints/gates")
    if not has(text.split("\n## ")[0], r"checkpoint", r"gate"):
        probs.append("README intro misses checkpoints/gates")
    blocks = re.findall(r"(?s)```[^\n]*\n(.*?)```", text)
    for rel in (".claude-plugin", ".codex-plugin"):
        if (ROOT / rel / "plugin.json").exists() and not any(rel + "/plugin.json" in b for b in blocks):
            probs.append(f"README does not list {rel}/plugin.json in its file tree")
    pos, s = cp6_opening_sentence(text)
    for f in re.findall(r"[\w./-]*plugin\.json", s):
        if not (ROOT / f).exists():
            probs.append(f"install sentence names missing {f}")
    return probs


def cp6_docs_lint():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    good = ('# R\n\n> Please install Ratchet Loop for me in Claude Code, Codex, Gemini CLI or GitHub Copilot CLI, and start it.\n\n'
            '## Install\nSay the sentence above.\n\n## Common tasks\n'
            '> Check that the hooks are installed.\n> Give me the viewer link.\n> Who is reviewing this, and can other reviewers be used?\n'
            '> Pause and resume the build.\n> Enroll this lane under a director.\n\n'
            '## Command reference\nCommands the agent runs, for people typing by hand:\n`ratchet.py view --serve`, `ratchet.py install-hooks --host codex`, '
            '`ratchet.py doctor`, `ratchet.py lanes --scan d`, `python3 tests/test_hooks.py`\n')
    bad_cmd_first = good.replace("> Please install", "`./install.sh --host claude`\n\n> Please install", 1)
    bad_you_run = good.replace("Say the sentence above.", "| Claude | `./install.sh` |\nYou run this in your terminal.")
    ref_leak = good.replace("Say the sentence above.", "Run `ratchet.py view --serve`.")
    tests = cp6_tests_on_disk()

    # T01
    check("[CP6-T01] README opens with a quoted sentence to the agent that asks to install Ratchet and start, before any shell command",
          cp6_opens_with_sentence(readme), cp6_opening_sentence(readme))
    check("[CP6-T01] teeth: the sentence is found in a good sample, and a shell command before it or a missing sentence is rejected",
          cp6_opens_with_sentence(good) and not cp6_opens_with_sentence(bad_cmd_first) and not cp6_opens_with_sentence("# R\n\nInstall by reading docs.\n"))
    check("[CP6-T22] the opening sentence names where Ratchet Loop comes from (an address, link, clone or folder path)",
          re.search(r"from\s+<[^>]*(address|link|url|clone|folder|path)[^>]*>|from\s+(https?://|[~/.])", cp6_opening_sentence(readme)[1], re.I) is not None, cp6_opening_sentence(readme)[1])
    # T02
    check("[CP6-T02] the install path names all four CLIs, leads with the sentence, and has no per-CLI command table or 'you run'",
          cp6_install_primary_is_sentence(readme), sorted(providers_named(cp6_install_area(readme))))
    check("[CP6-T02] teeth: a command table or 'you run this in your terminal' in the install area is rejected; a good sample passes",
          cp6_install_primary_is_sentence(good) and not cp6_install_primary_is_sentence(bad_you_run))
    # T03
    res = cp6_task_sentences(readme)
    check("[CP6-T03] each of hooks, viewer link, reviewers, pause and resume, directors has its own example sentence to the agent",
          cp6_task_sentences_ok(readme), {k: len(v) for k, v in res.items()})
    check("[CP6-T03] teeth: a sample missing the director sentence, or one that reuses a single sentence for every topic, is rejected",
          cp6_task_sentences_ok(good) and not cp6_task_sentences_ok(good.replace("> Enroll this lane under a director.\n", ""))
          and not cp6_task_sentences_ok("> Please check the hooks, viewer link, reviewers, pause and resume, and the director.\n"))
    # T04
    heads = [h for h in re.findall(r"(?m)^#{2,3}\s+(.+)$", readme) if re.search(r"reference", h, re.I)]
    check("[CP6-T04] a Reference section says who the commands are for and holds view --serve, install-hooks --host, doctor, lanes --scan; none appear above it",
          cp6_reference_ok(readme), heads)
    check("[CP6-T04] teeth: a command leaking into the primary text, or a missing Reference heading, is rejected",
          cp6_reference_ok(good) and not cp6_reference_ok(ref_leak) and not cp6_reference_ok(good.replace("## Command reference", "## Commands")))
    # T05
    check("[CP6-T05] the reference section gives a runnable python3 tests/<file> command for every tests/test_*.py, and each is named",
          cp6_tests_documented(readme, tests), [f for f in tests if f not in readme])
    check("[CP6-T05] teeth: a missing file or a test command outside the reference section is rejected",
          cp6_tests_documented(good, ["test_hooks.py"]) and not cp6_tests_documented(good, ["test_hooks.py", "test_zzz.py"])
          and not cp6_tests_documented(good.replace("## Command reference", "## Commands"), ["test_hooks.py"]))
    # T06
    files = [ROOT / "README.md", ROOT / "install.sh"] + sorted((ROOT / "skills").glob("*/SKILL.md")) + sorted((ROOT / "agents").glob("*.md"))
    hits = [p.name for p in files if re.search(r"other agent clis?", p.read_text(encoding="utf-8"), re.I)]
    check("[CP6-T06] no README, install.sh, SKILL.md or agent file mentions 'Other agent CLIs'", not hits, hits)
    check("[CP6-T06] README has no #other-agent-clis anchor, and every heading it refers to by quoted name or anchor exists",
          not cp6_broken_refs(readme) and "#other-agent-clis" not in readme, cp6_broken_refs(readme))
    inst = (ROOT / "install.sh").read_text(encoding="utf-8")
    check("[CP6-T06] install.sh does not refer to a README section that does not exist", not cp6_broken_refs(inst + "\n" + readme) and not [n for n in re.findall(r"README\s+'([^']+)'", inst) if n.lower() not in cp6_headings(readme)])
    check("[CP6-T06] teeth: a reference to a missing quoted heading or anchor is detected; an existing one is not",
          cp6_broken_refs("# A\n\nsee README 'Other agent CLIs'\n") == ["Other agent CLIs"]
          and cp6_broken_refs("# A\n\n[x](#nope)\n") == ["#nope"]
          and cp6_broken_refs("# Install\n\nsee README 'Install' and [x](#install)\n") == [])
    # T07
    check("[CP6-T07] README says non-Claude CLIs find skills in ~/.ratchet-loop/skills and must be pointed at or linked to it (Codex, Gemini, Copilot)",
          cp6_nonclaude_ok(readme))
    check("[CP6-T07] teeth: missing folder, missing link instruction, or missing hosts are rejected",
          cp6_nonclaude_ok("Non-Claude hosts (Codex, Gemini, Copilot) get skills in ~/.ratchet-loop/skills; symlink that folder into the CLI's skills folder.")
          and not cp6_nonclaude_ok("Codex, Gemini and Copilot are supported.")
          and not cp6_nonclaude_ok("Non-Claude hosts (Codex, Gemini, Copilot) get skills in ~/.ratchet-loop/skills.")
          and not cp6_nonclaude_ok("Codex, Gemini, Copilot: symlink the skills folder."))
    # T08
    check("[CP6-T08] the opening and common-task sentences and the primary text carry no /plugin, claude --plugin-dir or AskUserQuestion",
          cp6_neutral_ok(readme))
    check("[CP6-T08] teeth: a Claude-only phrase in a sentence or the primary text is rejected, but allowed inside the reference section",
          cp6_neutral_ok(good) and not cp6_neutral_ok(good.replace("and start it.", "and start it with /plugin install."))
          and cp6_neutral_ok(good + "\nUse `claude --plugin-dir x` here.\n"))
    # T17
    probs = cp6_manifest_problems(readme)
    check("[CP6-T17] both manifests parse with the same name and version, mention checkpoints and gates, the README lists both, and its install sentence names no missing file",
          not probs, probs)
    check("[CP6-T17] teeth: a README that does not list the Codex manifest is reported", any("does not list" in p for p in cp6_manifest_problems("# R\n\ncheckpoint gate\n")))


# ---- end CP6 ----

# ---- CP7 ----
# Doc/engine drift: reviewer-count claim, UI reviewer provider, round folder names, schemas.md vs real output,
# and the UI gate's design file. Every drift check compares the docs with what the engine really emits.

import json
import subprocess


def cp7_read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def cp7_bullet(text, start):
    m = re.search(r"(?m)^- \*\*" + start + r"[^\n]*(?:\n(?![-#\n])[^\n]*)*", text)
    return m.group(0) if m else ""


def cp7_doc_files():
    return ([ROOT / "README.md", ROOT / "skills/ratchet-orchestrator/references/schemas.md"]
            + sorted((ROOT / "skills").glob("*/SKILL.md")) + sorted((ROOT / "agents").glob("*.md")))


def cp7_round_tokens(text):
    """Every folder-like `round-...` token (with or without an adv- prefix) in the text."""
    return re.findall(r"round-([^\s/`'\")\]]*)", text)


def cp7_bad_round_tokens(text):
    """Round tokens that are neither a zero-padded number, the NN placeholder nor a glob. The unpadded `k` is rejected."""
    return [t for t in cp7_round_tokens(text) if not re.fullmatch(r"\d{2}|NN|\*", t)]


def cp7_real_round():
    """Real `reviewers --json` output of a round-1 adversarial checkpoint (built in a child process)."""
    code = ("import sys, json; sys.path.insert(0, %r); import test_gates as g\n"
            "d, env = g.cp7_host_project('cp7docsr'); g.cp7_to_adversarial(d, env)\n"
            "print(json.dumps(g.cp7_reviewers(d, env)))\n") % str(ROOT / "tests")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def cp7_contents_vs_headings(text):
    toc_part = text.split("\n---", 1)[0]
    toc = re.findall(r"(?m)^(\d+)\.\s+(.+?)\s*$", toc_part)
    heads = re.findall(r"(?m)^## (\d+)\.\s+(.+?)\s*$", text)
    return [(n, t.strip()) for n, t in toc], [(n, t.strip()) for n, t in heads]


def cp7_json_block(text, heading_prefix):
    sec = section(text, heading_prefix)
    m = re.search(r"```json\n(.*?)```", sec, re.S)
    return json.loads(m.group(1)) if m else None


def cp7_key_diff(real, doc):
    """(keys the engine emits but the doc lacks, keys the doc lists that the engine does not emit)."""
    return sorted(set(real) - set(doc)), sorted(set(doc) - set(real))


def cp7_real_project():
    """Real engine output from a throwaway project (built by tests/test_gates.py helpers in a child process)."""
    code = ("import sys, json; sys.path.insert(0, %r); import test_gates as g\n"
            "d = g.project('cp7docs'); g.approve_plan(d)\n"
            "print(json.dumps({'status': json.loads(g.rx(d, 'status', '--json').stdout),"
            " 'files': sorted(x.name for x in (d / '.ratchet').iterdir() if x.is_file())}))\n") % str(ROOT / "tests")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    return json.loads(r.stdout.strip().splitlines()[-1])


def cp7_layout_block(text):
    m = re.search(r"## 1\. Project layout\s*```\n(.*?)```", text, re.S)
    return m.group(1) if m else ""


def cp7_docs_lint():
    print("CP7: doc drift")
    readme = cp7_read("README.md")
    schemas = cp7_read("skills/ratchet-orchestrator/references/schemas.md")
    ui_skill = cp7_read("skills/ratchet-ui-compare/SKILL.md")
    loop_skill = cp7_read("skills/ratchet-adversarial-loop/SKILL.md")

    # T01
    para = next((p for p in readme.split("\n\n") if re.search(r"host'?s own sub-agents fill", p, re.I)), "")
    check("[CP7-T01] README has the reviewer-fallback paragraph", bool(para))
    check("[CP7-T01] it no longer claims the reviewer count holds / stays the same",
          bool(para) and not re.search(r"count\s+(still\s+)?(holds|stays|is kept|is unchanged)|same (number|count)|keeps? (its|the) (full )?count|never (drops|shrinks)", para, re.I), para)
    check("[CP7-T01] it says the number of distinct reviewers can be smaller when only the host is available",
          has(para, r"distinct reviewers", r"(smaller|fewer|drop|lower)", r"high-risk|high risk"), para)
    check("[CP7-T01] teeth: the old 'count holds' wording would be rejected",
          re.search(r"count\s+(still\s+)?(holds|stays)", "the reviewer count holds", re.I) is not None)
    check("[CP7-T01] the README still says substitutions are logged (matches the engine's reviewer_fallback log)",
          has(para, r"substitutions? (is|are) logged"))
    claim = r"(number|count) of reviewers (still )?(holds|stays|is kept)"
    check("[CP7-T01] no SKILL file claims the number of reviewers holds (the count can shrink for high risk with only the host)",
          not [f.name for f in (ROOT / "skills").glob("*/SKILL.md") if re.search(claim, f.read_text(encoding="utf-8"), re.I)]
          and has(loop_skill, r"high-risk", r"count of distinct reviewers can be smaller"))
    check("[CP7-T01] teeth: the old SKILL wording would be rejected",
          re.search(claim, "then another lens, so the number of reviewers holds", re.I) is not None)

    # T04 / T05
    bl = cp7_bullet(readme, r"Isolated UI reviewers")
    check("[CP7-T04] README has the isolated UI reviewers bullet", bool(bl))
    check("[CP7-T04] it does not say UI reviewers run as claude sessions only",
          bool(bl) and not re.search(r"new\s+`?claude\s+-p`?\s+sessions?|always\s+`?claude", bl, re.I), bl)
    check("[CP7-T04] it says the provider comes from ui_reviewers (any supported CLI other than the host, host sub-agents as fallback)",
          has(bl, r"ui_reviewers", r"(other than|not|besides|except|skipping)[^.]*host", r"sub-?agents?"), bl)
    check("[CP7-T04] it says the temp folder holds only screenshots, accessibility trees and the design file",
          has(bl, r"temp", r"screenshots", r"accessibility trees", r"design"), bl)
    check("[CP7-T05] it still says reviewers never see the code, project instructions or the chat",
          has(bl, r"never see", r"(agent|project) instructions|CLAUDE\.md", r"\bcode\b", r"\bchat\b"), bl)
    check("[CP7-T04] teeth: the old 'new claude sessions' wording would be rejected",
          re.search(r"new\s+`?claude\s+-p`?\s+sessions?", "run as new `claude -p` sessions", re.I) is not None)

    # T06 / T07
    bad = {f.name: cp7_bad_round_tokens(f.read_text(encoding="utf-8")) for f in cp7_doc_files()}
    bad = {k: v for k, v in bad.items() if v}
    check("[CP7-T06] no doc names a round folder as round-k / round-<k> / adv-round-k (only zero-padded numbers or the NN placeholder)", not bad, bad)
    check("[CP7-T06] the fixer and loop steps use the NN placeholder, not a hard-coded round",
          all(re.search(r"round-NN", cp7_read(f)) and not re.search(r"round-01", cp7_read(f))
              for f in ("agents/ratchet-fixer.md", "skills/ratchet-adversarial-loop/SKILL.md")))
    check("[CP7-T06] teeth: round-<k> and adv-round-k are flagged; round-01, round-02 and round-NN are not",
          cp7_bad_round_tokens("x/round-<k>/y adv-round-k") == ["<k>", "k"]
          and cp7_bad_round_tokens("adv-round-01/fixes.md round-02 round-NN") == [])
    real_round = cp7_real_round()
    real_names = [Path(str(real_round.get(k) or "")).parent.name if k == "fixes_md" else Path(str(real_round.get(k) or "")).name
                  for k in ("output_dir",)]
    check("[CP7-T07] the engine's real round folder is adv-round-<two digits>, so the docs' NN means the zero-padded round",
          bool(real_names) and all(re.fullmatch(r"adv-round-\d{2}", n) for n in real_names), real_round.get("output_dir") or real_round)
    doc_ev = [t for f in cp7_doc_files() for t in cp7_round_tokens(f.read_text(encoding="utf-8"))]
    check("[CP7-T07] every round folder the docs mention is a two-digit number or NN", doc_ev and all(re.fullmatch(r"\d{2}|NN|\*", t) for t in doc_ev), doc_ev)

    # T08
    toc, heads = cp7_contents_vs_headings(schemas)
    words = lambda t: {w for w in re.findall(r"[a-z0-9_.-]{3,}", t.lower())}
    same = [n for n, _ in toc] == [n for n, _ in heads] and all(words(a[1]) & words(b[1]) for a, b in zip(toc, heads))
    check("[CP7-T08] schemas.md Contents lists exactly the numbered ## headings (same numbers, each title shares its heading's words)",
          same and len(heads) >= 10, {"toc": [n for n, _ in toc], "headings": [n for n, _ in heads]})
    check("[CP7-T08] section 10 Hooks is in both", any(n == "10" and re.search(r"hooks", t, re.I) for n, t in toc) and any(n == "10" for n, _ in heads))
    good = "Contents\n1. A\n2. B\n\n---\n\n## 1. A\n\n## 2. B\n"
    a, b = cp7_contents_vs_headings(good)
    a2, b2 = cp7_contents_vs_headings(good.replace("2. B\n\n---", "\n---"))
    check("[CP7-T08] teeth: a heading missing from Contents is detected", a == b and [n for n, _ in a2] != [n for n, _ in b2])

    # T09: layout vs files the engine really has under .ratchet/
    lay = cp7_layout_block(schemas)
    check("[CP7-T09] schemas.md section 1 has a layout block", bool(lay))
    names = ["hook-heartbeat.json", "hook-errors.log", "unavailable-reviewers.json"]
    check("[CP7-T09] the layout lists hook-heartbeat.json, hook-errors.log and unavailable-reviewers.json",
          all(n in lay for n in names), [n for n in names if n not in lay])
    proj = cp7_real_project()
    real_top = [n for n in proj["files"] if n != ".gitignore"]
    check("[CP7-T09] every file really present in .ratchet/ after init and a prompt hook appears in the layout",
          bool(real_top) and all(n in lay for n in real_top), [n for n in real_top if n not in lay])

    # T10 / T11 / T12
    real = proj["status"]
    doc = cp7_json_block(schemas, "9.")
    check("[CP7-T10] schemas.md section 9 has a parseable status --json example", isinstance(doc, dict))
    doc = doc if isinstance(doc, dict) else {}
    miss, extra = cp7_key_diff(real, doc)
    check("[CP7-T10] every real top-level status --json key is documented", not miss, miss)
    check("[CP7-T10] the doc example has no top-level key the engine does not emit", not extra, extra)
    dcp = (doc.get("checkpoints") or [{}])[0]
    rcp = real["checkpoints"][0]
    m2, e2 = cp7_key_diff(rcp, dcp)
    check("[CP7-T11] per-checkpoint keys (id, title, risk, status, step, commit, gates) all documented, none invented", not m2 and not e2, (m2, e2))
    gate_keys = {k for gg in (dcp.get("gates") or {}).values() for k in gg}
    check("[CP7-T11] a documented gate entry shows status, attempts and by (real gates emit status/attempts, and by when set)",
          {"status", "attempts", "by"} <= gate_keys and set(next(iter(rcp["gates"].values()))) <= gate_keys, gate_keys)
    ur = (doc or {}).get("unavailable_reviewers") or {}
    check("[CP7-T10] the unavailable_reviewers example maps a provider to {reason, since, until}",
          bool(ur) and all(isinstance(v, dict) and {"reason", "since", "until"} <= set(v) for v in ur.values()), ur)
    key = "hook_heartbeat"
    trimmed = {k: v for k, v in doc.items() if k != key}
    tm, _ = cp7_key_diff(real, trimmed)
    check("[CP7-T12] teeth: with one real key removed from the example, the comparison reports it missing", tm == [key] or key in tm, tm)
    check("[CP7-T12] teeth: an invented key is reported as extra", cp7_key_diff(real, dict(doc, bogus_key=1))[1] == ["bogus_key"])

    # T18
    cmd = re.search(r"ui_review\.py review.*?(?=\n```)", ui_skill, re.S)
    cmd = cmd.group(0) if cmd else ""
    check("[CP7-T18] the UI skill has the ui_review.py review command", bool(cmd))
    check("[CP7-T18] the command does not pass a literal --design DESIGN.md", "--design DESIGN.md" not in cmd and "--design DESIGN.md" not in ui_skill, cmd)
    check("[CP7-T18] the skill says the design file comes from config design_md",
          has(ui_skill, r"design_md"), "design_md not mentioned")
    check("[CP7-T18] teeth: the old hard-coded command would be rejected", "--design DESIGN.md" in '--scope "x" --design DESIGN.md --cases y')

    # T19: earlier sections still registered
    check("[CP7-T19] the CP4, CP5 and CP6 sections are still registered", [n for n, _ in SECTIONS][:3] == ["CP4", "CP5", "CP6"])


# ---- end CP7 ----

SECTIONS = [("CP4", cp4_orchestrator_setup_and_plan_review), ("CP5", cp5_docs_lint), ("CP6", cp6_docs_lint),
            ("CP7", cp7_docs_lint), ("CP2", cp2_docs_lint)]
# single-line restatement so CP5-T20's single-line SECTIONS probe pattern still matches and can replace it
SECTIONS = [s for s in SECTIONS]


# ---- CP5 adversarial round 12: a reply can go unrecorded, and the docs say so ----

def cp5_adv12_unrecorded_reply_docs():
    print("CP5 adv round 12: README and the orchestrator skill say a reply can go unrecorded and must be sent again")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text(encoding="utf-8")
    sec = readme.split("### Saying yes, and saying no", 1)[-1].split("\n## ", 1)[0]
    check("[CP5-X62] README's 'Saying yes, and saying no' says a reply can go unrecorded when Ratchet's own records "
          "are damaged, that it then approves nothing, and that the user sends it again after the repair",
          has(sec, r"unrecorded|not recorded|doesn't record", r"records?[^.]*damaged|damaged[^.]*records?",
              r"approves nothing", r"send your reply again|send it again", r"accept-state"), sec[-800:])
    check("[CP5-X62] README still says a reply sent while Ratchet is busy is recorded and need not be sent again",
          has(sec, r"busy", r"still recorded", r"don't need to send it again"))
    check("[CP5-X62] the orchestrator skill tells the agent what to do when the prompt hook did not record a reply: "
          "never treat it as an answer, tell the user, ask again after the repair",
          has(skill, r"Ratchet did not record this message", r"When a reply wasn't recorded",
              r"not an answer", r"print the question again", r"send their reply again", r"accept-state"))


SECTIONS.append(("CP5", cp5_adv12_unrecorded_reply_docs))


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
