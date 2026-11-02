#!/usr/bin/env python3
"""
OpenCode support, step 8: the README, SKILL.md and schemas.md hook copy is honest about OpenCode.

    python3 tests/test_hook_opencode_docs.py      # reads three docs as text, runs install-hooks in throwaway folders

This file reads README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md as
text, plus the README 'Running the tests' block and tests/fixtures/modularize_cp1/test-inventory.md. It also runs the real
`ratchet.py install-hooks --host opencode` (project scope and user scope) in a throwaway git project with a throwaway HOME
and RATCHET_HOME, every folder from tests/_tmp.py, every ambient CODEX_*/OPENCODE_*/XDG_*/RATCHET_*/GEMINI_*/COPILOT_*/
CLAUDE* variable removed. It never loads ratchet.py as a module, never touches the repo's .ratchet/ folder, and starts no
long-lived process. Every check name carries its case id as [CP8-Txx].

How the lints find things (so a renamed heading cannot hide a section)
  * README hook section: the first table whose header row starts '| CLI |' and has a 'Stop' column (the per-CLI table);
    the section is the text from the heading above that table to the next '###' heading.
  * README install text: the table whose header row starts '| Form |', with the heading above it up to the next '###'.
  * SKILL.md hook paragraph: the paragraph that starts 'Hooks (' (else the one holding '**stop** hook').
  * schemas.md section 10: from the line '## 10.' to the line '## 11.'; its OpenCode block runs from '**OpenCode' to
    '**Plain-language'.
  * Every honesty rule is a SENTENCE-LEVEL lint: prose is split into sentences (tables and code spans are handled
    separately) and a rule looks at what one sentence (or its clause) says about OpenCode and stop. A keyword somewhere
    else in the section never satisfies a rule. CP8-T19 runs the same lint functions on dishonest copy to prove they fail.

Wording the lints accept (plain, stable phrases; the implementer should match them). Case-insensitive unless noted.
  T01 table   An OpenCode row beside Claude Code, Codex, Gemini, Copilot. Cells: protects 'Yes ... hand-edits';
              identity 'No ...'; stop cell does not start with 'Yes' and starts with 'No' or says best-effort; replies 'Yes'.
  T02         One paragraph with a sentence naming OpenCode that says the guard hook and the prompt hook are 'enforced',
              and a clause saying the stop hook is 'best-effort' (or 'best effort'), e.g.
              'On OpenCode, the guard and prompt hooks are enforced, and the stop hook is best-effort.'
              The clause with 'best-effort' must hold the word 'stop'; 'enforced' must not sit in a clause with 'stop'
              (clauses split at , ; : and 'and', 'but', 'while').
  T03         In a paragraph that names OpenCode and its stop: the phrase 'you see the turn end'; a sentence that says
              Ratchet sends the agent back (send/sends/sent/re-prompt ... 'reason', 'back to work' or 'new message');
              a sentence with 'in a row', a number (3, three or a few) and 'progress'; a sentence with 'sub-agent' and
              never/not/skip; a sentence with 'cancel' and never/not/skip/'holds off'.
  T04         No sentence naming OpenCode says the stop blocks/prevents/holds/keeps/enforces the end of a turn unless
              that sentence also hedges (cannot, no hook, does not, never, best-effort, advisory, without, only after
              the turn). Code spans are ignored. One README sentence says 'no hook that can hold ... open' or
              'cannot hold ... turn open'.
  T05         README bullet 'No quitting early' names OpenCode with best-effort, or links to a '(#anchor)', or points at
              the per-CLI section ('per-CLI' or 'Using it with'). 'Protected records' still says only Claude Code names
              the acting sub-agent.
  T06         A sentence naming OpenCode with the literal code span `!` and 'not checked', 'never reach', 'bypass',
              'never see' or 'not seen'; the same sentence or the next one says 'known limit'.
  T07         A sentence naming OpenCode with 1.18.27 and built/tested/written/checked; 1.19.5 in that sentence or the
              next one; no other x.y.z version number in a sentence that names OpenCode.
  T08         A sentence with 'v2', 'plugin API', the string @opencode/plugin and 'not supported'; a sentence with 'v1',
              'plugin API' and 'released'; 'later release' (or 'later OpenCode release') + 'update' in the v2
              sentence or the next. No 'works on every version'.
  T09         SKILL.md hook paragraph: a sentence naming OpenCode beside Claude Code and Codex; a sentence naming OpenCode
              and 'install-hooks' with '--host opencode'; a sentence naming OpenCode and 'auto' or 'detect' with not/never/n't;
              a sentence naming OpenCode and 'restart'. (The step-2 hook check may hold the install and restart text.)
  T10         README install table: a row whose first cell holds '--host opencode'; the folders '.opencode/plugins/' and
              '~/.config/opencode/plugins/'; a sentence naming OpenCode (or its plugin) that says running it again
              changes nothing, one that says uninstall removes only Ratchet's own file, one that says a file of the same
              name that is not Ratchet's is never overwritten, one naming doctor and OpenCode. The '--host all' row may
              say 'all four' ONLY when the same row says OpenCode is not part of it (install-hooks --host all really
              installs four CLIs; OpenCode is only installed by an explicit --host opencode).
  T11         schemas.md section 10: an 'opencode' row in the host table holding '.opencode/plugins/ratchet.js' and the
              user path ending 'opencode/plugins/ratchet.js'; the first '--host' line lists opencode; the OpenCode block says
              'exactly one JSON object', 'exit code always 0', 'nothing on stderr', 'fails open' and 'advisory'.
  T12         The project path '.opencode/plugins/ratchet.js' and the user path '.config/opencode/plugins/ratchet.js'
              (relative to HOME, exactly what the real install writes) appear verbatim in the README and in schemas.md
              section 10.
  T13         Count words in the hook text agree with five CLIs: 'the other N' must be four, 'all N' / 'N CLIs' must be
              five (a '--host all' row is the one place 'four' is allowed, see T10); no 'same on all four'; the 'Review
              checks' paragraph and the 'guard also refuses' paragraph name OpenCode wherever they name Codex/Gemini/
              Copilot.
  T14         'Only Claude Code ... sub-agent'; one sentence naming OpenCode, Codex, Gemini and Copilot as unable to
              confirm ('cannot confirm', 'cannot tell', 'do not'); 'not independently confirmed' in a sentence naming
              OpenCode.
  T15         A sentence naming OpenCode and a break/crash that says the tool call goes/passes/is let through and does
              not block (not/never/instead of/rather than/without + 'block'); a sentence naming OpenCode and a broken or
              stopped plugin that 'sends nothing' for the stop re-prompt, with 'advisory' or 'best-effort' in it or the
              next sentence. No sentence naming OpenCode says the stop 'fails closed'.
  T16         In a paragraph naming OpenCode: 'typed' and 'recorded' in a sentence; a sentence naming 'plugin',
              'sub-agent' and 'reviewer' with 'never recorded' or 'not recorded' AND 'approve ... nothing' in that same
              sentence ('... are never recorded and approve nothing'). No 'every message is recorded'.
  T17         Sentences naming OpenCode (README hook-section prose, SKILL.md hook paragraph), code spans removed:
              Flesch-Kincaid grade <= 8.0 over all of them (0.39*words/sentences + 11.8*syllables/words - 15.59;
              words are letters only, so numbers and code spans do not count; syllables are vowel groups minus one
              for a silent final 'e', so 'OpenCode' is 3), none longer than 30 words. Short plain sentences with
              one-syllable words pass: the OpenCode name alone costs three syllables per sentence.
  T18         SKILL.md hook paragraph passes the same lints as the README for guard and prompt enforced, stop
              best-effort, the `!` limit and the version; its sentence 'blocks you from ending your turn' names OpenCode
              or best-effort, or the next sentence names OpenCode and best-effort; the Gates bullet ('A Stop hook
              refuses to let you end your turn') is qualified the same way.
  T20/T21     see their cases; the implementer registers this file in README.md and in test-inventory.md.
  T22         The Codex row is unchanged; no sentence says the Codex reply works on every or older Codex version; a
              sentence that says the Codex reply format was 'checked against' something names stop.rs, 75a4146 and the
              Codex hooks docs page, and says older Codex versions were not checked.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS_NAME = "test_hook_opencode_docs.py"
NEW_TEST_LINE = "python3 tests/" + THIS_NAME

PASS, FAIL = 0, 0
_STRIP_PREFIXES = ("CODEX_", "OPENCODE_", "XDG_", "RATCHET_", "GEMINI_", "COPILOT_", "GITHUB_COPILOT_", "CLAUDE")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {str(detail)[:600]}")


def read(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except OSError:
        return ""


# ------------------------------------------------------------------------------------------ text tools

OC = re.compile(r"open\s?code", re.I)
_ABBR = ("e.g.", "i.e.", "vs.", "etc.")
_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9`*(\[$<@])")


def paragraphs(text):
    """Paragraphs (blank-line separated; a list item or a table starts its own), skipping fenced code."""
    paras, cur, fence = [], [], False

    def flush():
        if cur:
            paras.append(" ".join(cur))
            cur.clear()

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            flush()
            fence = not fence
            continue
        if fence:
            continue
        if not line.strip():
            flush()
        elif re.match(r"\s*(?:[-*] |\d+\. )", line):
            flush()
            cur.append(line.strip())
        elif line.lstrip().startswith("|") != bool(cur and cur[0].startswith("|")):
            flush()
            cur.append(line.strip())
        else:
            cur.append(line.strip())
    flush()
    return paras


def prose_paragraphs(text):
    return [p for p in paragraphs(text) if not p.startswith("|") and not p.startswith("#")]


def split_spans(par):
    """The raw sentence segments of one paragraph (boundaries found on a same-length masked copy)."""
    masked = par
    for a in _ABBR:
        masked = masked.replace(a, a.replace(".", "§"))
    cuts, start = [], 0
    for m in _SPLIT.finditer(masked):
        cuts.append((start, m.start() + 1 if masked[m.start()] in ".!?" else m.start()))
        start = m.end()
    cuts.append((start, len(par)))
    return [par[a:b].strip() for a, b in cuts if par[a:b].strip()]


def sentences(par):
    return [s.replace("**", "") for s in split_spans(par)]


def prose_sentences(text):
    return [s for p in prose_paragraphs(text) for s in sentences(p)]


def oc_sentences(text):
    return [s for s in prose_sentences(text) if OC.search(s)]


def nocode(s):
    return re.sub(r"`[^`]*`", " ", s)


def table_rows(text):
    rows = []
    for line in text.split("\n"):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def units(text):
    """Every prose sentence and every table row (as one string) in the text."""
    return prose_sentences(text) + [" | ".join(r) for r in table_rows(text)]


def lines_of(text):
    return text.split("\n")


def section_around(text, header_pred):
    """(heading-to-next-heading section text, header cells) for the first table whose header row matches."""
    ls = lines_of(text)
    for i, line in enumerate(ls):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header_pred(cells):
            continue
        a = i
        while a > 0 and not ls[a].startswith("#"):
            a -= 1
        b = i + 1
        while b < len(ls) and not re.match(r"#{2,3} ", ls[b]):
            b += 1
        return "\n".join(ls[a:b]), cells
    return "", []


def readme_sec(text):
    return section_around(text, lambda c: bool(c) and c[0] == "CLI" and any("stop" in x.lower() for x in c))[0]


def readme_install(text):
    return section_around(text, lambda c: bool(c) and c[0] == "Form")[0]


def skill_para(text):
    ps = paragraphs(text)
    for p in ps:
        if p.startswith("Hooks ("):
            return p
    for p in ps:
        if "**stop** hook" in p:
            return p
    return ""


def skill_steps(text):
    m = re.search(r"^2\. \*\*Check the hooks yourself.*?(?=^3\. \*\*Check who else)", text, re.S | re.M)
    return m.group(0) if m else ""


def schemas10(text):
    m = re.search(r"^## 10\..*?(?=^## 11\.)", text, re.S | re.M)
    return m.group(0) if m else ""


def oc_block(sch10):
    a = sch10.find("**OpenCode")
    if a < 0:
        return ""
    b = sch10.find("**Plain-language", a)
    return sch10[a:] if b < 0 else sch10[a:b]


class Docs:
    def __init__(self, sec, skill, sch10, install="", readme_full=""):
        self.sec, self.skill, self.sch10, self.install, self.readme_full = sec, skill, sch10, install, readme_full

    def mutate(self, fn):
        return Docs(fn(self.sec), fn(self.skill), fn(self.sch10), fn(self.install), fn(self.readme_full))


def real_docs():
    r, s, c = read(README), read(SKILL), read(SCHEMAS)
    return Docs(readme_sec(r), skill_para(s), schemas10(c), readme_install(r), r)


def seq_after(sents, i):
    return sents[i] + " " + (sents[i + 1] if i + 1 < len(sents) else "")


def hedged(s, rx):
    return bool(re.search(rx, s, re.I))


CLAUSE = re.compile(r"[;:,]|\s+(?:but|while|whereas|and|yet|so)\s+", re.I)
GUARD_RE = re.compile(r"\bguard\b", re.I)
PROMPT_RE = re.compile(r"(?<![\w-])prompt\b", re.I)
STOP_RE = re.compile(r"\bstop\b", re.I)
BEST_RE = re.compile(r"best[- ]effort", re.I)
ENFORCED_POS = re.compile(r"(?<!not )(?<!never )(?<!n't )(?<!no )\benforc(?:ed|es|e|ing)\b", re.I)


# ----------------------------------------------------------------------------------------------- the lints
# Every lint takes a Docs and returns a list of problem strings (empty = honest). The CORE lints are the ones
# CP8-T19 mutates; the others read parts of the docs the mutants do not touch.

def table_cols(cells):
    low = [c.lower() for c in cells]

    def col(word, default):
        return next((i for i, c in enumerate(low) if word in c), default)
    return {"protects": col("protect", 1), "identity": col("identity", 2), "stop": col("stop", 3),
            "replies": col("repl", 4)}


def cli_table(sec):
    rows = table_rows(sec)
    for i, r in enumerate(rows):
        if r and r[0] == "CLI" and any("stop" in x.lower() for x in r):
            body = []
            for rr in rows[i + 1:]:
                if len(rr) != len(r):
                    break
                body.append(rr)
            return r, body
    return [], []


def lint_t01(d):
    hdr, body = cli_table(d.sec)
    if not hdr:
        return ["no per-CLI table (header row '| CLI | ... Stop ...') found"]
    cols, p = table_cols(hdr), []
    names = [re.sub(r"[*`]", "", r[0]).strip() for r in body]
    want = ["Claude Code", "Codex", "Gemini", "Copilot", "OpenCode"]
    if sorted(names) != sorted(want):
        p.append(f"rows are {names}, want exactly {want}")
    oc = [r for r in body if re.sub(r"[*`]", "", r[0]).strip() == "OpenCode"]
    if len(oc) != 1:
        return p + [f"{len(oc)} OpenCode rows, want exactly 1"]
    r = oc[0]
    if not r[cols["protects"]].lower().startswith("yes") or "hand-edit" not in r[cols["protects"]].lower():
        p.append("OpenCode 'protects records' cell must say Yes and name hand-edits: " + r[cols["protects"]][:80])
    if not r[cols["identity"]].lower().startswith("no"):
        p.append("OpenCode identity cell must say No: " + r[cols["identity"]][:80])
    if not r[cols["replies"]].lower().startswith("yes"):
        p.append("OpenCode replies cell must say Yes: " + r[cols["replies"]][:80])
    stop = r[cols["stop"]]
    if stop.lower().startswith("yes"):
        p.append("OpenCode stop cell reads as a plain yes: " + stop[:80])
    elif not (stop.lower().startswith("no") or BEST_RE.search(stop)):
        p.append("OpenCode stop cell is neither a No nor best-effort: " + stop[:80])
    if overclaims(stop):
        p.append("OpenCode stop cell claims the stop holds the turn: " + stop[:80])
    return p


_CLAIM = re.compile(
    r"\b(?:blocks?|blocking|prevents?|holds?(?! off)|keeps?|enforces?|enforcing|refuses? to let|won't let|will not let|"
    r"stops? (?:the agent|you|it|an agent))\b[^.;]{0,60}\b(?:ending|end|ends|turn|stopping|finish|quit)",
    re.I)
_STOP_ENFORCED = re.compile(
    r"\bstop\b[^.;]{0,40}\b(?:is|are)\s+enforced|\benforced\b[^.;]{0,25}\bstop\b|\bstop\b[^.;]{0,20}\bhard\b", re.I)
_HEDGE = re.compile(
    r"\b(?:cannot|can't|can not|can only|no hook|does not|doesn't|do not|don't|not enforced|never|best[- ]effort|"
    r"advisory|no way|without|isn't|is not|are not|aren't|only after|after the turn|not a hard|nothing)\b", re.I)


def overclaims(s):
    t = nocode(s)
    if not (STOP_RE.search(t) or re.search(r"\bturn\b", t, re.I)):
        return False
    if not (_CLAIM.search(t) or _STOP_ENFORCED.search(t)):
        return False
    return not _HEDGE.search(t)


def lint_t02(d):
    p, found = [], False
    for par in prose_paragraphs(d.sec):
        ss = sentences(par)
        if not any(OC.search(s) for s in ss):
            continue
        guard = any(OC.search(s) and GUARD_RE.search(s) and ENFORCED_POS.search(s) for s in ss)
        prompt = any(OC.search(s) and PROMPT_RE.search(s) and ENFORCED_POS.search(s) for s in ss)
        stop_best = any(STOP_RE.search(c) and BEST_RE.search(c) for s in ss for c in CLAUSE.split(s))
        if guard and prompt and stop_best:
            found = True
    if not found:
        p.append("no paragraph says, for OpenCode, that guard and prompt are enforced and the stop hook is best-effort")
    p += _attachment_problems(oc_sentences(d.sec), "README")
    return p


def lint_t02_skill(d):
    p = []
    ss = oc_sentences(d.skill)
    guard = any(GUARD_RE.search(s) and ENFORCED_POS.search(s) for s in ss)
    prompt = any(PROMPT_RE.search(s) and ENFORCED_POS.search(s) for s in ss)
    stop_best = any(STOP_RE.search(c) and BEST_RE.search(c) for s in ss for c in CLAUSE.split(s))
    if not (guard and prompt and stop_best):
        p.append("SKILL.md hook paragraph does not say guard and prompt are enforced and stop is best-effort on OpenCode")
    return p + _attachment_problems(ss, "SKILL.md")


def _attachment_problems(oc_sents, where):
    p = []
    for s in oc_sents:
        for c in CLAUSE.split(nocode(s)):
            if STOP_RE.search(c) and ENFORCED_POS.search(c) and not BEST_RE.search(c) and not _HEDGE.search(c):
                p.append(f"{where}: 'enforced' is attached to the OpenCode stop: {s[:120]}")
            if BEST_RE.search(c) and (GUARD_RE.search(c) or PROMPT_RE.search(c)) and not STOP_RE.search(c):
                p.append(f"{where}: 'best-effort' is attached to the guard or prompt, not the stop: {s[:120]}")
    return p


def lint_t03(d):
    pars = [sentences(par) for par in prose_paragraphs(d.sec)
            if any(OC.search(s) for s in sentences(par)) and any(STOP_RE.search(s) for s in sentences(par))]
    ss = [s for group in pars for s in group]
    p = []
    if not any(re.search(r"you\s+see\s+the\s+turn\s+end", s, re.I) for s in ss):
        p.append("no OpenCode stop paragraph says 'you see the turn end'")
    if not any(re.search(r"\b(?:send|sends|sent|re-?prompt\w*)\b[^.]*?\b(?:reason|back to work|new message)\b", s, re.I)
               for s in ss):
        p.append("no sentence says Ratchet sends the agent back with the reason")
    if not any(re.search(r"in a row", s, re.I) and re.search(r"(?<![\d.])3(?![\d.])|\bthree\b|\ba few\b", s, re.I)
               and re.search(r"progress", s, re.I) for s in ss):
        p.append("no sentence caps the send-back to a few in a row without progress")
    if not any(re.search(r"sub-?agent", s, re.I) and re.search(r"\b(?:never|not|skip\w*)\b", s, re.I) for s in ss):
        p.append("no sentence says a sub-agent's session is skipped")
    if not any(re.search(r"cancel", s, re.I) and re.search(r"\b(?:never|not|skip\w*|holds? off)\b", s, re.I)
               for s in ss):
        p.append("no sentence says a cancelled session is skipped")
    return p


def lint_t04(d):
    p = []
    for where, text in (("README", d.sec), ("SKILL.md", d.skill), ("schemas.md", d.sch10)):
        for s in oc_sentences(text):
            if overclaims(s):
                p.append(f"{where}: sentence implies OpenCode's stop holds the turn: {s[:140]}")
    outright = re.compile(r"(?:cannot|can't|can not|no hook that can)\s+hold\b[^.]{0,40}?\bopen", re.I)
    if not any(outright.search(nocode(s)) for s in oc_sentences(d.sec)):
        p.append("README: no sentence says outright that OpenCode has no hook that can hold a turn open")
    return p


def lint_bang(text, where):
    ss = prose_sentences(text)
    for i, s in enumerate(ss):
        if OC.search(s) and "`!`" in s and re.search(
                r"not checked|never reach|bypass|never see|not seen|aren't checked|are not checked", s, re.I):
            if re.search(r"known limit", seq_after(ss, i), re.I) and \
                    not re.search(r"(?:will be|was|has been) fixed", seq_after(ss, i), re.I):
                return []
            return [f"{where}: the `!` sentence does not call it a known limit"]
    return [f"{where}: no sentence naming OpenCode says commands run with the `!` prefix are not checked"]


def lint_t06(d):
    return lint_bang(d.sec, "README") + lint_bang(d.skill, "SKILL.md")


def lint_version(text, where):
    ss = prose_sentences(text)
    p, ok = [], False
    for i, s in enumerate(ss):
        if OC.search(s) and "1.18.27" in s and re.search(r"built|tested|written|checked", s, re.I):
            if "1.19.5" in seq_after(ss, i):
                ok = True
        if OC.search(s):
            for v in re.findall(r"(?<![\d.])v?(\d+\.\d+\.\d+)(?![\d.])", s):
                if v not in ("1.18.27", "1.19.5"):
                    p.append(f"{where}: other OpenCode version number {v}: {s[:100]}")
    if not ok:
        p.append(f"{where}: no sentence says built and tested against OpenCode 1.18.27 with the released 1.19.5 source")
    return p


def lint_t07(d):
    return lint_version(d.sec, "README") + lint_version(d.skill, "SKILL.md")


def lint_t08(d):
    ss = prose_sentences(d.sec)
    p, v2_ok, v1_ok = [], False, False
    for i, s in enumerate(ss):
        if re.search(r"\bv2\b", s) and re.search(r"plugin API", s, re.I) and "@opencode/plugin" in s \
                and re.search(r"not supported|isn't supported|unsupported", s, re.I):
            if re.search(r"(?:later|newer|future)\s+(?:OpenCode\s+)?release", seq_after(ss, i), re.I) \
                    and re.search(r"update", seq_after(ss, i), re.I):
                v2_ok = True
        if re.search(r"\bv1\b", s) and re.search(r"plugin API", s, re.I) and re.search(r"released", s, re.I):
            v1_ok = True
        if OC.search(s) and re.search(r"(?:works?|runs?)\s+(?:on|with|in)\s+(?:every|any|all)\b", s, re.I) \
                and not re.search(r"\b(?:not|n't|never)\b", s, re.I):
            p.append("README says the plugin works on every OpenCode version: " + s[:100])
    if not v2_ok:
        p.append("no sentence says the v2 plugin API (@opencode/plugin) is not supported, so a later release can need an update")
    if not v1_ok:
        p.append("no sentence says the plugin uses OpenCode's released v1 plugin API")
    return p


def lint_t14(d):
    ss = prose_sentences(d.sec)
    p = []
    if not any(re.search(r"\bonly Claude Code\b", s, re.I) and re.search(r"sub-?agent", s, re.I) for s in ss):
        p.append("no sentence says only Claude Code tells Ratchet which sub-agent is acting")
    if not any(OC.search(s) and "Codex" in s and "Gemini" in s and "Copilot" in s
               and re.search(r"cannot confirm|can't confirm|cannot tell|do not|does not|not confirm", s, re.I)
               for s in ss):
        p.append("no sentence lists OpenCode with Codex, Gemini and Copilot as unable to confirm reviewer identity")
    if not any(OC.search(s) and re.search(r"not independently confirmed", s, re.I) for s in ss):
        p.append("no sentence naming OpenCode carries the 'not independently confirmed' fallback")
    for s in ss:
        if OC.search(s) and re.search(r"\b(?:tells|names|confirms)\b[^.]*sub-?agent", s, re.I) \
                and not re.search(r"\b(?:not|n't|never|only|cannot|none|no)\b", s, re.I):
            p.append("a sentence credits OpenCode with naming the sub-agent: " + s[:100])
    return p


def lint_t15(d):
    ss = prose_sentences(d.sec)
    p, s1, s2 = [], False, False
    in_oc_par = set()
    for par in prose_paragraphs(d.sec):
        if OC.search(par):
            in_oc_par.update(sentences(par))
    for i, s in enumerate(ss):
        if s not in in_oc_par:
            continue
        broke = re.search(r"\b(?:break|breaks|broken|broke|crash\w*|fails?)\b", s, re.I)
        if OC.search(s) and broke and re.search(r"\b(?:goes|go|let|lets|passes|pass|goes)\b[^.]*\bthrough\b", s, re.I) \
                and re.search(r"(?:not|n't|never|instead of|rather than|without)\s+block", s, re.I):
            s1 = True
        if re.search(r"\b(?:broken|breaks|stopped|crash\w*|fails?)\b", s, re.I) \
                and re.search(r"sends? nothing|nothing is sent|sends no", s, re.I) \
                and re.search(r"advisory|best[- ]effort", seq_after(ss, i), re.I):
            s2 = True
        if OC.search(s) and re.search(r"fails? closed", s, re.I) and not re.search(r"\b(?:not|n't|never|cannot)\b", s, re.I):
            p.append("a sentence says OpenCode's stop fails closed: " + s[:100])
    if not s1:
        p.append("no OpenCode sentence says a broken plugin or Ratchet lets the tool call through and does not block every tool")
    if not s2:
        p.append("no OpenCode sentence says a broken or stopped plugin sends nothing, so the stop is advisory")
    return p


def lint_t16(d):
    pars = [sentences(par) for par in prose_paragraphs(d.sec) if any(OC.search(s) for s in sentences(par))]
    ss = [s for g in pars for s in g]
    p = []
    if not any(re.search(r"\btyped\b", s, re.I) and re.search(r"\brecorded\b", s, re.I) for s in ss):
        p.append("no sentence says a message you typed is recorded as your reply")
    ok = False
    for i, s in enumerate(ss):
        if re.search(r"\bplugin\b", s, re.I) and re.search(r"sub-?agent", s, re.I) and re.search(r"reviewer", s, re.I) \
                and re.search(r"(?:never|not) recorded", s, re.I):
            if re.search(r"approv\w*\s+nothing|approve[^.]*nothing", s, re.I):
                ok = True
        if OC.search(s) and re.search(r"(?:every|all)\s+(?:\w+\s+)?messages?[^.]*recorded", s, re.I) \
                and not re.search(r"\b(?:not|n't|never|only)\b", s, re.I):
            p.append("a sentence says every OpenCode message is recorded: " + s[:100])
    if not ok:
        p.append("no sentence says Ratchet's plugin, a sub-agent and a nested reviewer are never recorded and approve nothing")
    return p


def count_words(w):
    return {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}[w.lower()]


_NUM = r"(two|three|four|five|six)"


def lint_t13_counts(text, where):
    p = []
    for u in units(text):
        host_all = "--host all" in u
        for m in re.finditer(r"\bthe other " + _NUM + r"\b(?=\s+(?:CLIs?|can|cannot|do|does|are|hosts)\b|[.,;:])", u, re.I):
            if count_words(m.group(1)) != 4:
                p.append(f"{where}: 'the other {m.group(1)}' (want four) in: {u[:90]}")
        for rx in (r"\ball " + _NUM + r"\b(?!\s+(?:gates|hard|reviewers|checks|tries))", r"\b(?<!other )" + _NUM + r" CLIs?\b"):
            for m in re.finditer(rx, u, re.I):
                n = count_words(m.group(1))
                if n == 5:
                    continue
                if host_all and n == 4 and re.search(
                        r"(?:not|never|except|without|excludes?|separate\w*)[^|.]{0,60}OpenCode|OpenCode[^|.]{0,60}"
                        r"(?:is not|isn't|never|only|separate\w*)", u, re.I):
                    continue
                p.append(f"{where}: count word '{m.group(1)}' disagrees with five CLIs in: {u[:90]}")
        if re.search(r"same on all four", u, re.I):
            p.append(f"{where}: the old 'same on all four' claim is back")
    return p


def lint_t13(d):
    p = []
    for where, text in (("README hook section", d.sec), ("README install", d.install), ("SKILL.md", d.skill),
                        ("schemas.md", d.sch10)):
        p += lint_t13_counts(text, where)
    hdr, body = cli_table(d.sec)
    if hdr and len(body) != 5:
        p.append(f"per-CLI table has {len(body)} rows, want five")
    return p


def lint_t17(d):
    ss = [nocode(s).replace("**", "") for s in oc_sentences(d.sec)] + \
         [nocode(s).replace("**", "") for s in oc_sentences(d.skill)]
    if not ss:
        return ["no sentence names OpenCode, so there is no copy to grade"]
    p, words, syll = [], 0, 0
    for s in ss:
        ws = re.findall(r"[A-Za-z][A-Za-z'’-]*", s)
        words += len(ws)
        syll += sum(syllables(w) for w in ws)
        if len(ws) > 30:
            p.append(f"sentence of {len(ws)} words (limit 30): {s.strip()[:100]}")
    g = fk_grade(words, len(ss), syll)
    if g > 8.0:
        p.append(f"Flesch-Kincaid grade {g:.1f} over {len(ss)} sentences (limit 8.0)")
    return p


def syllables(word):
    """Vowel groups, minus one for a silent final 'e' (not 'le'), at least 1: 'code' is 1, 'OpenCode' is 3."""
    w = word.lower().strip("'’-")
    n = len(re.findall(r"[aeiouy]+", w))
    if n > 1 and w.endswith("e") and not w.endswith("le"):
        n -= 1
    return max(1, n)


def fk_grade(words, sents, syll):
    if not words or not sents:
        return 0.0
    return 0.39 * words / sents + 11.8 * syll / words - 15.59


def lint_t18(d):
    p = lint_t02_skill(d) + lint_bang(d.skill, "SKILL.md") + lint_version(d.skill, "SKILL.md")
    ss = prose_sentences(d.skill)
    found = False
    for i, s in enumerate(ss):
        if re.search(r"blocks you from ending your turn", s, re.I):
            found = True
            nxt = ss[i + 1] if i + 1 < len(ss) else ""
            if not (OC.search(s) or BEST_RE.search(s) or re.search(r"\bexcept\b", s, re.I)
                    or (OC.search(nxt) and BEST_RE.search(nxt))):
                p.append("SKILL.md says the stop hook blocks you from ending your turn with no OpenCode exception")
    if not found:
        p.append("SKILL.md hook paragraph lost its 'blocks you from ending your turn' sentence")
    return p


def lint_t22(d):
    p = []
    hdr, body = cli_table(d.sec)
    cols = table_cols(hdr) if hdr else {}
    codex = [r for r in body if r[0].strip("*` ") == "Codex"]
    if hdr and len(codex) == 1:
        r = codex[0]
        if not r[cols["protects"]].startswith("Yes, Ratchet's state and evidence files are protected from hand-edits."):
            p.append("Codex 'protects records' cell changed")
        if not r[cols["identity"]].startswith("No, Ratchet has no way to know which sub-agent"):
            p.append("Codex identity cell changed")
        if r[cols["stop"]] != "Yes, the stop hook stops the agent from ending its turn early.":
            p.append("Codex stop cell changed: " + r[cols["stop"]][:80])
        if r[cols["replies"]] != ("Yes, the prompt hook records your replies and shows Ratchet's guidance back "
                                  "to the agent."):
            p.append("Codex replies cell changed: " + r[cols["replies"]][:80])
    elif hdr:
        p.append("per-CLI table has no single Codex row")
    for where, text in (("README", d.sec), ("SKILL.md", d.skill), ("schemas.md", d.sch10)):
        ss = prose_sentences(text)
        for i, s in enumerate(ss):
            if "Codex" not in s:
                continue
            if re.search(r"(?:every|all|any|older|earlier|previous|prior)\s+(?:Codex\s+)?versions?", s, re.I) \
                    and not re.search(r"\b(?:not|n't|never|unchecked|unknown|could not|cannot)\b", s, re.I):
                p.append(f"{where}: a Codex sentence claims every or older versions: {s[:100]}")
            if re.search(r"works? (?:on|with|in) (?:every|any|all)\b", s, re.I) \
                    and not re.search(r"\b(?:not|n't|never)\b", s, re.I):
                p.append(f"{where}: a Codex sentence says it works on every version: {s[:100]}")
            if re.search(r"(?:checked|verified|confirmed)\s+(?:against|from|in|with)", s, re.I) \
                    and re.search(r"\b(?:reply|format)\b", s, re.I):
                both = seq_after(ss, i)
                if not ("stop.rs" in both and "75a4146" in both
                        and re.search(r"hooks (?:docs|page|documentation)|codex/hooks", both, re.I)
                        and re.search(r"older Codex versions?[^.]*(?:not|never)\s+(?:been\s+)?checked|"
                                      r"(?:not|never)\s+checked[^.]*older|older Codex versions? (?:were|was) not",
                                      both, re.I)):
                    p.append(f"{where}: a Codex sentence cites a check without stop.rs, 75a4146, the hooks docs and "
                             f"the older-versions caveat: {s[:100]}")
    return p


CORE = [("T01", lint_t01), ("T02", lint_t02), ("T03", lint_t03), ("T04", lint_t04), ("T06", lint_t06),
        ("T07", lint_t07), ("T08", lint_t08), ("T13", lint_t13), ("T14", lint_t14), ("T15", lint_t15),
        ("T16", lint_t16), ("T17", lint_t17), ("T18", lint_t18), ("T22", lint_t22)]


def run_core(d):
    out = set()
    for name, fn in CORE:
        for prob in fn(d):
            out.add(f"{name}: {prob}")
    return out


# ------------------------------------------------------------------- an honest fixture the lints must accept

HONEST_SEC = """### Using it with Codex, Gemini, Copilot or OpenCode

The five CLIs do not all enforce the same things. Here is what each one does today:

| CLI | Protects Ratchet's records | Confirms reviewer identity | Stop hook | Records your replies |
|---|---|---|---|---|
| Claude Code | Yes, Ratchet's state and evidence files are protected from hand-edits. | Yes, Claude Code tells Ratchet which sub-agent is acting, so Ratchet can confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent. |
| Codex | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent. |
| Gemini | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent. |
| Copilot | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Copilot's prompt hook drops its own message, so the agent never sees Ratchet's guidance, but the reply is still recorded in Ratchet's log. |
| OpenCode | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Best-effort only. The turn ends first, then Ratchet sends the agent back to work. | Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent. |

On OpenCode, the guard and prompt hooks are enforced. On OpenCode, the stop hook is best-effort. OpenCode has no hook that can hold a turn open. When the agent ends its turn with a gate still open, you see the turn end first. On OpenCode, you see it end before Ratchet acts. Only then does Ratchet send the agent back to work with the reason. It sends at most 3 in a row with no progress. It never does this for a sub-agent's session or for a session you cancelled.

Commands you run with the `!` prefix on OpenCode are not checked. That is a known limit.

We built and tested it on OpenCode 1.18.27. Its plugin API matches the released 1.19.5 source. The plugin uses the released v1 plugin API. The v2 plugin API (`@opencode/plugin`) is not supported. A later OpenCode release can need a plugin update.

A message you really typed is recorded as your reply on OpenCode. A message from Ratchet's own plugin, a sub-agent's message and a nested reviewer's prompt are never recorded and approve nothing.

Only Claude Code tells Ratchet which sub-agent is acting. Ratchet cannot confirm reviewer identity on Codex, Gemini, Copilot or OpenCode. On OpenCode, a reviewer's file is marked "not independently confirmed".

On OpenCode, a broken plugin lets the tool call through instead of blocking. On OpenCode, a broken plugin sends nothing for the stop re-prompt. That makes the stop advisory.

None of the other four CLIs can name the acting sub-agent.
"""

HONEST_SKILL = ("Hooks are installed by `$RATCHET install-hooks` for Claude Code, Codex, Gemini CLI, Copilot CLI or "
                "OpenCode. The **stop** hook blocks you from ending your turn until `ratchet.py next` says stopping is "
                "OK. On OpenCode, the guard and prompt hooks are enforced, and the stop hook is best-effort. OpenCode is "
                "not detected on its own, so run `install-hooks --host opencode`. Commands the user runs with the `!` "
                "prefix on OpenCode are not checked. That is a known limit. We built and tested it against OpenCode "
                "1.18.27. Its plugin API matches the released 1.19.5 source. Tell the user to restart OpenCode so the "
                "plugin loads.")

HONEST_SCH = """## 10. Hooks

`ratchet.py hook <stop|prompt|guard> --host auto|claude|codex|gemini|copilot|opencode` is the single entry point.

| host | config file (project / user) | stop | prompt | guard |
|---|---|---|---|---|
| opencode | `.opencode/plugins/ratchet.js` / `~/.config/opencode/plugins/ratchet.js` | session.idle | chat.message | tool.execute.before |

**OpenCode (a plugin host).** Writes one plugin file.
- Reply: exactly one JSON object `{"block": ...}`, exit code always 0, nothing on stderr.
- Failure: a crash fails open.
- Stop re-prompt (best-effort, advisory): OpenCode has no hook that can hold a turn open.

**Plain-language output.**
"""


def honest_docs():
    return Docs(HONEST_SEC, HONEST_SKILL, HONEST_SCH)


# -------------------------------------------------------------------------------------------- mutations

def _drop_sentences(text, pred):
    out = text
    for par in prose_paragraphs(text):
        for seg in split_spans(par):
            if pred(seg):
                out = out.replace(seg, "", 1)
    return out


MUTATIONS = [
    ("best-effort swapped for enforced on the OpenCode stop", "T02",
     lambda t: re.sub(r"best[- ]effort", "enforced", t, flags=re.I)),
    ("the `!` sentence removed", "T06", lambda t: _drop_sentences(t, lambda s: "`!`" in s)),
    ("1.18.27 changed to 1.18.2", "T07", lambda t: t.replace("1.18.27", "1.18.2")),
    ("'you see the turn end' removed", "T03",
     lambda t: re.sub(r"\(?you see the turn end(?: first)?\)?[,.]?\s*", "", t, flags=re.I)),
    ("a sentence saying the OpenCode stop hook blocks the agent from ending its turn", "T04",
     lambda t: t.rstrip("\n") + "\n\nOn OpenCode the stop hook blocks the agent from ending its turn.\n"),
    ("the v2 sentence removed", "T08", lambda t: _drop_sentences(t, lambda s: re.search(r"\bv2\b", s) is not None)),
]


# ------------------------------------------------------------------------------------------------- cases

def case_t01():
    print("[CP8-T01] README per-CLI table: the OpenCode row")
    d = real_docs()
    check("[CP8-T01] the README per-CLI table is found by its '| CLI |' header row", bool(d.sec), "no table found")
    p = lint_t01(d)
    check("[CP8-T01] five rows, one OpenCode row, Yes/No/Yes cells right, stop cell not a plain yes", not p, p)
    print("[CP8-T01] teeth: dishonest tables are flagged")
    good = HONEST_SEC
    row = next(l for l in good.split("\n") if l.startswith("| OpenCode"))
    yes_stop = row.replace("Best-effort only. The turn ends first, then Ratchet sends the agent back to work.",
                           "Yes, the stop hook stops the agent from ending its turn early.")
    check("[CP8-T01] teeth: a plain Yes in the OpenCode stop cell is flagged",
          lint_t01(Docs(good.replace(row, yes_stop), "", "")) != [])
    check("[CP8-T01] teeth: a missing OpenCode row is flagged",
          lint_t01(Docs(good.replace(row + "\n", ""), "", "")) != [])
    check("[CP8-T01] teeth: a second OpenCode row is flagged",
          lint_t01(Docs(good.replace(row + "\n", row + "\n" + row + "\n"), "", "")) != [])
    check("[CP8-T01] teeth: identity cell saying Yes is flagged",
          lint_t01(Docs(good.replace(row, row.replace("| No, Ratchet has no way", "| Yes, Ratchet has a way")), "", ""))
          != [])
    check("[CP8-T01] the honest fixture table passes", lint_t01(honest_docs()) == [], lint_t01(honest_docs()))


def case_t02():
    print("[CP8-T02] README: on OpenCode the guard and prompt are enforced, the stop is best-effort")
    p = lint_t02(real_docs())
    check("[CP8-T02] one paragraph says guard and prompt enforced and the stop best-effort; enforced never on the stop",
          not p, p)
    print("[CP8-T02] teeth")
    sw = honest_docs().mutate(MUTATIONS[0][2])
    check("[CP8-T02] teeth: 'best-effort' swapped for 'enforced' is flagged", lint_t02(sw) != [])
    wrong = Docs(HONEST_SEC.replace("the guard and prompt hooks are enforced. On OpenCode, the stop hook is best-effort.",
                                    "the guard and prompt hooks are best-effort. On OpenCode, the stop hook is enforced."), "", "")
    check("[CP8-T02] teeth: enforced and best-effort attached to the wrong hooks is flagged", lint_t02(wrong) != [])
    check("[CP8-T02] teeth: the honest fixture passes", lint_t02(honest_docs()) == [], lint_t02(honest_docs()))


def case_t03():
    print("[CP8-T03] README: the user sees the turn end first, and the send-back is limited")
    p = lint_t03(real_docs())
    check("[CP8-T03] 'you see the turn end', send-back with the reason, a cap in a row, sub-agent and cancel skipped",
          not p, p)
    print("[CP8-T03] teeth")
    sw = honest_docs().mutate(MUTATIONS[3][2])
    check("[CP8-T03] teeth: removing 'you see the turn end' is flagged", lint_t03(sw) != [])
    nocap = Docs(HONEST_SEC.replace("It sends at most 3 in a row with no progress. ", ""), "", "")
    check("[CP8-T03] teeth: removing the cap is flagged", lint_t03(nocap) != [])
    nosub = Docs(HONEST_SEC.replace("a sub-agent's session or for ", ""), "", "")
    check("[CP8-T03] teeth: removing the sub-agent skip is flagged", lint_t03(nosub) != [])
    check("[CP8-T03] teeth: the honest fixture passes", lint_t03(honest_docs()) == [], lint_t03(honest_docs()))


def case_t04():
    print("[CP8-T04] No document claims OpenCode's stop can hold a turn open")
    p = lint_t04(real_docs())
    check("[CP8-T04] no OpenCode sentence implies a hold, and one says outright there is no hook that can hold a turn open",
          not p, p)
    print("[CP8-T04] teeth: sentence-level, not keyword")
    claim = honest_docs().mutate(MUTATIONS[4][2])
    check("[CP8-T04] teeth: 'On OpenCode the stop hook blocks the agent from ending its turn' is flagged",
          lint_t04(claim) != [])
    sw = honest_docs().mutate(MUTATIONS[0][2])
    check("[CP8-T04] teeth: 'the stop hook is enforced' on OpenCode is flagged", lint_t04(sw) != [])
    neg = ["On OpenCode the stop hook cannot block the agent from ending its turn.",
           "OpenCode has no hook that can hold a turn open, so the stop only sends a message after the turn ended."]
    check("[CP8-T04] teeth: negated sentences are not flagged", not any(overclaims(s) for s in neg))
    check("[CP8-T04] teeth: the same words about Claude Code (no OpenCode) are not read as an OpenCode claim",
          lint_t04(Docs(HONEST_SEC + "\nOn Claude Code the stop hook blocks the agent from ending its turn.\n", "", ""))
          == [], lint_t04(Docs(HONEST_SEC + "\nOn Claude Code the stop hook blocks the agent from ending its turn.\n",
                               "", "")))
    check("[CP8-T04] teeth: dropping the 'no hook that can hold a turn open' sentence is flagged",
          lint_t04(Docs(HONEST_SEC.replace("OpenCode has no hook that can hold a turn open. ", ""), "", "")) != [])
    check("[CP8-T04] teeth: the honest fixture passes", lint_t04(honest_docs()) == [], lint_t04(honest_docs()))


def case_t05():
    print("[CP8-T05] the older 'No quitting early' and 'Protected records' bullets do not overclaim")
    text = read(README)
    bullets = {}
    for l in text.split("\n"):
        m = re.match(r"- \*\*(Protected records|No quitting early)\.\*\*(.*)", l)
        if m:
            bullets[m.group(1)] = m.group(2)
    p = []
    nq = bullets.get("No quitting early")
    if nq is None:
        p.append("the 'No quitting early' bullet is gone")
    elif not ((OC.search(nq) and BEST_RE.search(nq)) or re.search(r"\]\(#[^)]+\)", nq)
              or re.search(r"per-CLI|Using it with", nq)):
        p.append("'No quitting early' still reads as a flat promise on every CLI: " + nq[:120])
    check("[CP8-T05] 'No quitting early' names the OpenCode best-effort exception or points to the per-CLI section",
          not p, p)
    pr = bullets.get("Protected records")
    q = []
    if pr is None:
        q.append("the 'Protected records' bullet is gone")
    else:
        if not re.search(r"only\s+(?:in\s+)?Claude Code|In Claude Code[^.]{0,60}(?:tells|names)[^.]{0,40}sub-?agent", pr):
            q.append("'Protected records' no longer says only Claude Code names the acting sub-agent")
        for s in sentences(pr):
            if OC.search(s) and not re.search(r"\b(?:not|n't|never|no|cannot|none|neither)\b", s, re.I):
                q.append("'Protected records' says something positive about OpenCode and sub-agents: " + s[:100])
    check("[CP8-T05] 'Protected records' still credits only Claude Code with naming the acting sub-agent", not q, q)
    print("[CP8-T05] teeth")
    flat = "Another hook stops the agent from ending its turn until a gate is passed, unless it's waiting for you."
    check("[CP8-T05] teeth: the original flat 'No quitting early' wording is not accepted",
          not ((OC.search(flat) and BEST_RE.search(flat)) or re.search(r"\]\(#[^)]+\)", flat)
               or re.search(r"per-CLI|Using it with", flat)))


def case_t06():
    print("[CP8-T06] README and SKILL.md say the user's own ! shell commands on OpenCode are not checked")
    d = real_docs()
    p = lint_t06(d)
    check("[CP8-T06] README and SKILL.md each have a sentence naming OpenCode that says `!` commands are not checked, "
          "as a known limit", not p, p)
    print("[CP8-T06] teeth")
    gone = honest_docs().mutate(MUTATIONS[1][2])
    check("[CP8-T06] teeth: removing the `!` sentence is flagged in both documents", len(lint_t06(gone)) == 2,
          lint_t06(gone))
    claim = Docs(HONEST_SEC.replace("are not checked. That is a known limit.", "are checked by the guard."),
                 HONEST_SKILL, "")
    check("[CP8-T06] teeth: saying the guard checks them is flagged", lint_bang(claim.sec, "README") != [])
    nolimit = Docs(HONEST_SEC.replace("That is a known limit.", "That will be fixed soon."), HONEST_SKILL, "")
    check("[CP8-T06] teeth: a `!` sentence that is not called a known limit is flagged",
          lint_bang(nolimit.sec, "README") != [])
    check("[CP8-T06] teeth: the honest fixture passes", lint_t06(honest_docs()) == [], lint_t06(honest_docs()))


def case_t07():
    print("[CP8-T07] README and SKILL.md name the OpenCode version the plugin was built and tested against")
    p = lint_t07(real_docs())
    check("[CP8-T07] each says built and tested against OpenCode 1.18.27 and 1.19.5, and no other version number",
          not p, p)
    print("[CP8-T07] teeth")
    ch = honest_docs().mutate(MUTATIONS[2][2])
    check("[CP8-T07] teeth: 1.18.27 changed to 1.18.2 is flagged in both documents", len(lint_t07(ch)) >= 2, lint_t07(ch))
    other = Docs(HONEST_SEC.replace("1.19.5", "1.19.6"), HONEST_SKILL, "")
    check("[CP8-T07] teeth: a different released version is flagged", lint_version(other.sec, "README") != [])
    extra = Docs(HONEST_SEC + "\nOpenCode 1.2.3 also works.\n", HONEST_SKILL, "")
    check("[CP8-T07] teeth: an extra OpenCode version number is flagged", lint_version(extra.sec, "README") != [])
    check("[CP8-T07] teeth: the honest fixture passes", lint_t07(honest_docs()) == [], lint_t07(honest_docs()))


def case_t08():
    print("[CP8-T08] README: the unreleased v2 plugin API is not supported")
    p = lint_t08(real_docs())
    check("[CP8-T08] the plugin uses the released v1 plugin API; v2 (@opencode/plugin) is not supported", not p, p)
    print("[CP8-T08] teeth")
    gone = honest_docs().mutate(MUTATIONS[5][2])
    check("[CP8-T08] teeth: removing the v2 sentence is flagged", lint_t08(gone) != [])
    every = Docs(HONEST_SEC + "\nThe OpenCode plugin works on every OpenCode version.\n", "", "")
    check("[CP8-T08] teeth: 'works on every OpenCode version' is flagged", lint_t08(every) != [])
    check("[CP8-T08] teeth: the honest fixture passes", lint_t08(honest_docs()) == [], lint_t08(honest_docs()))


def lint_t09(text, steps):
    para = skill_para(text)
    p = []
    ss = oc_sentences(para)
    if not any("Claude Code" in s and "Codex" in s for s in ss):
        p.append("the hooks paragraph does not list OpenCode among the supported CLIs")
    everything = oc_sentences(para) + oc_sentences(steps)
    if not any("install-hooks" in s and "--host opencode" in s for s in everything):
        p.append("no sentence tells the agent to run install-hooks with --host opencode")
    if not any(re.search(r"\bauto|detect", s, re.I) and re.search(r"\b(?:not|never|n't)\b", s, re.I)
               for s in everything):
        p.append("no sentence says OpenCode is not auto-detected")
    if not any(re.search(r"\brestart", s, re.I) for s in everything):
        p.append("no sentence tells the agent to say the OpenCode session needs a restart")
    d = Docs("", para, "")
    p += lint_t02_skill(d) + lint_bang(para, "SKILL.md") + lint_version(para, "SKILL.md")
    return p


def case_t09():
    print("[CP8-T09] SKILL.md tells the agent to install with --host opencode and says what that gives")
    text = read(SKILL)
    p = lint_t09(text, skill_steps(text))
    check("[CP8-T09] hooks paragraph lists OpenCode; --host opencode (not auto); the same facts; a restart", not p, p)
    check("[CP8-T09] the hooks paragraph and the step-2 hook check were both found",
          bool(skill_para(text)) and bool(skill_steps(text)))
    print("[CP8-T09] teeth")
    check("[CP8-T09] teeth: the honest fixture passes", lint_t09(HONEST_SKILL, "") == [], lint_t09(HONEST_SKILL, ""))
    check("[CP8-T09] teeth: without the --host opencode instruction it is flagged",
          lint_t09(HONEST_SKILL.replace("so run `install-hooks --host opencode`", "so run it"), "") != [])
    check("[CP8-T09] teeth: without the restart sentence it is flagged",
          lint_t09(HONEST_SKILL.replace("Tell the user to restart OpenCode so the plugin loads.", ""), "") != [])


def case_t10():
    print("[CP8-T10] README install section: OpenCode install, twice, uninstall, doctor, counts")
    r = read(README)
    block = readme_install(r)
    check("[CP8-T10] the install table ('| Form |' header row) was found", bool(block))
    p = []
    rows = table_rows(block)
    if not any("--host opencode" in row[0] for row in rows if row):
        p.append("the --host table has no row for `--host opencode`")
    for folder in (".opencode/plugins/", "~/.config/opencode/plugins/"):
        if folder not in block:
            p.append(f"the install text does not name {folder}")
    usage = [l for l in block.split("\n") if "[--host" in l]
    if not usage or "opencode" not in usage[0]:
        p.append("the install.sh usage line does not list opencode")
    check("[CP8-T10] the --host table has an OpenCode form and names both plugin folders", not p, p)
    us = units(block) + units(r)
    q = []

    def has(rx_all):
        return any(OC.search(u) and all(re.search(rx, u, re.I) for rx in rx_all) for u in us)
    if not has([r"\b(?:twice|again|second time)\b", r"nothing|unchanged|no change|not change"]):
        q.append("no OpenCode text says running install again changes nothing")
    if not has([r"uninstall", r"\bonly\b", r"Ratchet"]):
        q.append("no OpenCode text says uninstall removes only Ratchet's own file")
    if not has([r"(?:never|not|won't|will not)\s+(?:be\s+)?overwrit", r"same name|not Ratchet|your own|someone else|isn't Ratchet"]):
        q.append("no OpenCode text says a same-named file that is not Ratchet's is never overwritten")
    if not has([r"\bdoctor\b"]):
        q.append("no text says doctor lists OpenCode")
    check("[CP8-T10] install again changes nothing; uninstall removes only Ratchet's file; no overwrite; doctor lists it",
          not q, q)
    c = lint_t13_counts(block, "README install")
    check("[CP8-T10] count words in the install table agree with the real CLI count (the '--host all' row says it leaves "
          "OpenCode out, or no longer counts)", not c, c)
    print("[CP8-T10] teeth")
    allrow = "| `--host all` | the Claude copy, the shared copy, and hooks for all four CLIs |"
    check("[CP8-T10] teeth: 'all four CLIs' with no OpenCode exclusion is flagged", lint_t13_counts(allrow, "x") != [])
    ok_row = "| `--host all` | the Claude copy, the shared copy, and hooks for all four CLIs; OpenCode is never part of it |"
    check("[CP8-T10] teeth: 'all four CLIs' that says OpenCode is not part of it is accepted",
          lint_t13_counts(ok_row, "x") == [], lint_t13_counts(ok_row, "x"))
    check("[CP8-T10] teeth: 'all five CLIs' on a --host all row is accepted",
          lint_t13_counts("| `--host all` | hooks for all five CLIs |", "x") == [])


def case_t11():
    print("[CP8-T11] schemas.md section 10: an OpenCode row and the plugin's reply, failure and stop rules")
    s = read(SCHEMAS)
    sec = schemas10(s)
    check("[CP8-T11] section 10 was found", bool(sec))
    p = lint_t11(sec)
    check("[CP8-T11] host table row and first line list opencode; one JSON reply, exit 0, fail open, advisory stop", not p, p)
    print("[CP8-T11] teeth")
    check("[CP8-T11] teeth: the honest fixture passes", lint_t11(HONEST_SCH) == [], lint_t11(HONEST_SCH))
    check("[CP8-T11] teeth: a missing opencode row is flagged",
          lint_t11(HONEST_SCH.replace("| opencode |", "| other |")) != [])
    check("[CP8-T11] teeth: a first line without opencode is flagged",
          lint_t11(HONEST_SCH.replace("|gemini|copilot|opencode`", "|gemini|copilot`")) != [])
    check("[CP8-T11] teeth: a missing 'advisory' is flagged",
          lint_t11(HONEST_SCH.replace(", advisory", "")) != [])


def lint_t11(sec):
    p = []
    first = next((l for l in sec.split("\n") if "--host" in l and "hook <" in l), "")
    if "opencode" not in first:
        p.append("the first line's --host list does not include opencode")
    row = next((r for r in table_rows(sec) if r and r[0].strip("` ").lower() == "opencode"), None)
    if row is None:
        p.append("the host table has no opencode row")
    else:
        t = " | ".join(row)
        if ".opencode/plugins/ratchet.js" not in t or t.count("opencode/plugins/ratchet.js") < 2:
            p.append("the opencode row does not hold both the project and the user plugin file paths: " + t[:120])
    b = oc_block(sec)
    if not b:
        return p + ["no '**OpenCode' block in section 10"]
    for rx, what in ((r"exactly one JSON object", "one JSON object on stdout"),
                     (r"exit code always 0|exit 0|exits 0", "exit 0"), (r"nothing on stderr", "nothing on stderr"),
                     (r"fails? open", "fail open"), (r"advisory", "the stop re-prompt is advisory")):
        if not re.search(rx, b, re.I):
            p.append("the OpenCode block does not say: " + what)
    return p


def _install_world():
    proj = Path(_tmp.mkdtemp(prefix="ratchet-ocdocs-p-")).resolve()
    home = Path(_tmp.mkdtemp(prefix="ratchet-ocdocs-h-")).resolve()
    rh = Path(_tmp.mkdtemp(prefix="ratchet-ocdocs-r-")).resolve()
    subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True, capture_output=True, timeout=60)
    env = {k: v for k, v in os.environ.items() if not k.startswith(_STRIP_PREFIXES)}
    env.update(HOME=str(home), RATCHET_HOME=str(rh), PYTHONDONTWRITEBYTECODE="1")
    return proj, home, env


def _files(root):
    res = set()
    for dp, dns, fns in os.walk(str(root)):
        if dp == str(root):
            dns[:] = [x for x in dns if x != ".git"]
        for n in fns:
            res.add(os.path.relpath(os.path.join(dp, n), str(root)))
    return res


def _install(scope):
    """(created relative paths, exit code, created again after a second run) for one throwaway install."""
    proj, home, env = _install_world()
    root = proj if scope == "project" else home
    before = _files(root)
    cmd = [sys.executable, str(ENGINE), "install-hooks", "--host", "opencode", "--scope", scope]
    r1 = subprocess.run(cmd, cwd=str(proj), env=env, capture_output=True, text=True, timeout=120)
    first = _files(root) - before
    r2 = subprocess.run(cmd, cwd=str(proj), env=env, capture_output=True, text=True, timeout=120)
    second = _files(root) - before
    return sorted(first), r1.returncode, r2.returncode, sorted(second), (r1.stdout + r1.stderr)[:200]


def case_t12():
    print("[CP8-T12] the plugin file names the docs give are the ones install-hooks really writes")
    r, s = read(README), read(SCHEMAS)
    sec10 = schemas10(s)
    for scope in ("project", "user"):
        first, rc1, rc2, second, out = _install(scope)
        check(f"[CP8-T12] --scope {scope}: both runs exit 0 and exactly one plugin file is created",
              rc1 == 0 and rc2 == 0 and len(first) == 1 and first == second, (rc1, rc2, first, second, out))
        if len(first) != 1:
            continue
        rel = first[0]
        check(f"[CP8-T12] --scope {scope}: the created path '{rel}' appears verbatim in the README", rel in r, rel)
        check(f"[CP8-T12] --scope {scope}: the created path '{rel}' appears verbatim in schemas.md section 10",
              rel in sec10, rel)
    print("[CP8-T12] teeth")
    check("[CP8-T12] teeth: a doc that names a moved folder does not contain the real path",
          ".opencode/plugins/ratchet.js" not in "plugins live in .opencode/plugin/ratchet.js")


def case_t13():
    print("[CP8-T13] no hook section still counts four CLIs now that there are five")
    d = real_docs()
    p = lint_t13(d)
    check("[CP8-T13] every count word in the hook text, install table, SKILL.md and schemas.md section 10 agrees", not p, p)
    q = []
    r = d.readme_full
    review = next((x for x in prose_paragraphs(r) if x.startswith("Review checks, on all")), None)
    guard = next((x for x in prose_paragraphs(r)
                  if "the guard also refuses commands that set Ratchet's reserved variables" in x), None)
    for label, par in (("'Review checks' paragraph", review), ("'the guard also refuses' paragraph", guard)):
        if par is None:
            q.append(f"the {label} was not found")
        elif any(n in par for n in ("Codex", "Gemini", "Copilot")) and not OC.search(par):
            q.append(f"the {label} names the other CLIs but not OpenCode")
    if re.search(r"same on all four", r, re.I):
        q.append("the old 'hooks work the same on all four CLIs' claim is back")
    check("[CP8-T13] the 'Review checks' and 'guard also refuses' paragraphs name OpenCode where they name the others",
          not q, q)
    print("[CP8-T13] teeth")
    for bad in ("The four CLIs differ.", "Only Claude Code does this; none of the other three can.",
                "Hooks work the same on all four CLIs.", "Review checks, on all four CLIs, count.",
                "The guard also refuses commands, on all four."):
        check(f"[CP8-T13] teeth: '{bad}' is flagged", lint_t13_counts(bad, "x") != [])
    for good in ("Review checks, on all five CLIs, count.", "None of the other four can do that.",
                 "Claude Code runs four hard gates."):
        check(f"[CP8-T13] teeth: '{good}' is accepted", lint_t13_counts(good, "x") == [], lint_t13_counts(good, "x"))


def case_t14():
    print("[CP8-T14] the reviewer-identity limit is stated for OpenCode too")
    p = lint_t14(real_docs())
    check("[CP8-T14] only Claude Code names the sub-agent; OpenCode joins Codex, Gemini, Copilot; fallback applies", not p, p)
    print("[CP8-T14] teeth")
    check("[CP8-T14] teeth: the honest fixture passes", lint_t14(honest_docs()) == [], lint_t14(honest_docs()))
    no_oc = Docs(HONEST_SEC.replace("Codex, Gemini, Copilot or OpenCode", "Codex, Gemini or Copilot"), "", "")
    check("[CP8-T14] teeth: leaving OpenCode out of the cannot-confirm list is flagged", lint_t14(no_oc) != [])
    cred = Docs(HONEST_SEC + "\nOpenCode tells Ratchet which sub-agent is acting.\n", "", "")
    check("[CP8-T14] teeth: crediting OpenCode with naming the sub-agent is flagged", lint_t14(cred) != [])


def case_t15():
    print("[CP8-T15] the hook-failure text says what happens when the OpenCode plugin breaks")
    d = real_docs()
    p = lint_t15(d)
    check("[CP8-T15] a broken plugin lets the tool call through; a broken or stopped plugin sends nothing, so stop is advisory",
          not p, p)
    anchor = "If the guard or prompt hook breaks, it lets the action through"
    check("[CP8-T15] the Claude-style failure paragraph is still in the README", anchor in d.readme_full)
    print("[CP8-T15] teeth")
    check("[CP8-T15] teeth: the honest fixture passes", lint_t15(honest_docs()) == [], lint_t15(honest_docs()))
    closed = Docs(HONEST_SEC + "\nOn OpenCode the stop hook fails closed and blocks the agent from ending its turn.\n",
                  "", "")
    check("[CP8-T15] teeth: 'the OpenCode stop fails closed' is flagged", lint_t15(closed) != [])
    blockall = Docs(HONEST_SEC.replace("a broken plugin lets the tool call through instead of blocking",
                                       "a broken plugin blocks every tool call"), "", "")
    check("[CP8-T15] teeth: dropping the let-through sentence is flagged", lint_t15(blockall) != [])
    noadv = Docs(HONEST_SEC.replace(" That makes the stop advisory.", ""), "", "")
    check("[CP8-T15] teeth: dropping 'advisory' is flagged", lint_t15(noadv) != [])


def case_t16():
    print("[CP8-T16] only messages you typed are recorded as your replies on OpenCode")
    p = lint_t16(real_docs())
    check("[CP8-T16] a typed message is recorded; plugin, sub-agent and nested reviewer are never recorded and approve nothing",
          not p, p)
    print("[CP8-T16] teeth")
    check("[CP8-T16] teeth: the honest fixture passes", lint_t16(honest_docs()) == [], lint_t16(honest_docs()))
    every = Docs(HONEST_SEC + "\nOn OpenCode, every message is recorded.\n", "", "")
    check("[CP8-T16] teeth: 'Every OpenCode message is recorded' is flagged", lint_t16(every) != [])
    noapp = Docs(HONEST_SEC.replace(" and approve nothing", ""), "", "")
    check("[CP8-T16] teeth: dropping 'approve nothing' is flagged", lint_t16(noapp) != [])
    norev = Docs(HONEST_SEC.replace(" and a nested reviewer's prompt", ""), "", "")
    check("[CP8-T16] teeth: dropping the nested reviewer is flagged", lint_t16(norev) != [])


def case_t17():
    print("[CP8-T17] the OpenCode copy reads at an 8th-grade level")
    d = real_docs()
    n = len(oc_sentences(d.sec)) + len(oc_sentences(d.skill))
    p = lint_t17(d)
    check(f"[CP8-T17] {n} OpenCode sentences: grade 8.0 or lower, none over 30 words", not p, p)
    print("[CP8-T17] teeth: the helper can fail")
    jargon = ("Notwithstanding unprecedented interoperability considerations, institutionalized authentication "
              "infrastructure necessitates comprehensive reconfiguration.")
    ws = re.findall(r"[A-Za-z][A-Za-z'’-]*", jargon)
    g = fk_grade(len(ws), 1, sum(syllables(w) for w in ws))
    check("[CP8-T17] teeth: a jargon-heavy probe scores above 8", g > 8.0, g)
    plain = "The turn ends first. You see it end. Then Ratchet sends a note."
    ws = re.findall(r"[A-Za-z][A-Za-z'’-]*", plain)
    gp = fk_grade(len(ws), 3, sum(syllables(w) for w in ws))
    check("[CP8-T17] teeth: a plain probe scores 8 or lower", gp <= 8.0, gp)
    longs = Docs("On OpenCode the " + " and the ".join(["plain word"] * 17) + " stay plain.", "", "")
    check("[CP8-T17] teeth: a plain sentence over 30 words is flagged", any("limit 30" in x for x in lint_t17(longs)),
          lint_t17(longs))
    check("[CP8-T17] teeth: a jargon sentence naming OpenCode is flagged",
          lint_t17(Docs("OpenCode " + jargon, "", "")) != [])
    hd = honest_docs()
    hs = [nocode(x).replace("**", "") for x in oc_sentences(hd.sec) + oc_sentences(hd.skill)]
    hw = [re.findall(r"[A-Za-z][A-Za-z'’-]*", x) for x in hs]
    print("  (honest fixture grade %.1f over %d sentences)" % (
        fk_grade(sum(map(len, hw)), len(hs), sum(syllables(w) for ws2 in hw for w in ws2)), len(hs)))
    check("[CP8-T17] teeth: the honest fixture passes", lint_t17(hd) == [], lint_t17(hd))


def case_t18():
    print("[CP8-T18] SKILL.md's hook paragraph matches the README on every OpenCode fact")
    d = real_docs()
    p = lint_t18(d)
    check("[CP8-T18] guard and prompt enforced, stop best-effort, the `!` limit, the version, and the stop sentence "
          "qualified", not p, p)
    steps_gate = next((l for l in read(SKILL).split("\n") if l.startswith("2. **Gates**")), "")
    gp = []
    for s in sentences(steps_gate):
        if re.search(r"Stop hook (?:refuses|blocks|won't let)", s, re.I):
            nxt = sentences(steps_gate)
            i = nxt.index(s)
            after = nxt[i + 1] if i + 1 < len(nxt) else ""
            if not (OC.search(s) or BEST_RE.search(s) or re.search(r"\bexcept\b", s, re.I) or OC.search(after)):
                gp.append("the Gates bullet still says the Stop hook refuses on every CLI: " + s[:100])
    check("[CP8-T18] the Gates bullet's 'A Stop hook refuses to let you end your turn' is qualified for OpenCode",
          bool(steps_gate) and not gp, gp or "Gates bullet not found")
    print("[CP8-T18] teeth")
    check("[CP8-T18] teeth: the honest fixture passes", lint_t18(honest_docs()) == [], lint_t18(honest_docs()))
    flat = Docs("", "The **stop** hook blocks you from ending your turn until `ratchet.py next` says stopping is OK. "
                "On OpenCode the guard and prompt hooks are enforced and the stop hook is best-effort.", "")
    check("[CP8-T18] teeth: the stop sentence qualified only by a later sentence that names OpenCode passes the "
          "qualification", not any("no OpenCode exception" in x for x in lint_t18(flat)), lint_t18(flat))
    flat2 = Docs("", "The **stop** hook blocks you from ending your turn until `ratchet.py next` says stopping is OK.", "")
    check("[CP8-T18] teeth: a flat 'blocks you from ending your turn' with no OpenCode exception is flagged",
          any("no OpenCode exception" in x for x in lint_t18(flat2)))


def case_t19():
    print("[CP8-T19] the honesty checks fail on dishonest copy")
    base = honest_docs()
    bp = run_core(base)
    check("[CP8-T19] the unchanged honest fixture passes every lint", not bp, sorted(bp)[:4])
    for label, lint_id, fn in MUTATIONS:
        mut = base.mutate(fn)
        changed = (mut.sec, mut.skill) != (base.sec, base.skill)
        newp = run_core(mut) - bp
        check(f"[CP8-T19] fixture copy with '{label}' is changed and flagged by {lint_id}",
              changed and any(x.startswith(lint_id + ":") for x in newp), (changed, sorted(newp)[:3]))
    d = real_docs()
    rp = run_core(d)
    check("[CP8-T19] the real unchanged README hook section and SKILL.md hook paragraph pass every lint", not rp,
          sorted(rp)[:6])
    if rp:
        print("  (real-copy mutants are only judged once the real copy is honest; the fixture mutants above already ran)")
        return
    for label, lint_id, fn in MUTATIONS:
        mut = d.mutate(fn)
        changed = (mut.sec, mut.skill) != (d.sec, d.skill)
        newp = run_core(mut) - rp
        check(f"[CP8-T19] real copy with '{label}' is changed and flagged by {lint_id}",
              changed and any(x.startswith(lint_id + ":") for x in newp), (changed, sorted(newp)[:3]))


def case_t20():
    print("[CP8-T20] the README 'Running the tests' list includes the new docs test")
    ls = lines_of(read(README))
    start = next((i for i, l in enumerate(ls) if l.strip() == "### Running the tests"), None)
    check("[CP8-T20] the README has a '### Running the tests' heading", start is not None)
    block = []
    if start is not None:
        for l in ls[start + 1:]:
            if re.match(r"#{1,3} ", l):
                break
            block.append(l)
    n = sum(1 for l in block if l.strip() == NEW_TEST_LINE)
    check(f"[CP8-T20] `{NEW_TEST_LINE}` appears exactly once, on its own line, in that block", n == 1, f"{n} times")
    check("[CP8-T20] the test file exists", (ROOT / "tests" / THIS_NAME).is_file())
    print("[CP8-T20] teeth")
    check("[CP8-T20] teeth: a line that only mentions the file is not counted",
          sum(1 for l in ["see python3 tests/" + THIS_NAME + " too"] if l.strip() == NEW_TEST_LINE) == 0)


def case_t21():
    print("[CP8-T21] the test inventory registers the new docs test")
    ls = lines_of(read(INVENTORY))
    idx = [i for i, l in enumerate(ls) if THIS_NAME in l]
    check(f"[CP8-T21] {THIS_NAME} is listed in the inventory exactly once", len(idx) == 1, f"{len(idx)} mentions")
    if len(idx) == 1:
        i = idx[0]
        block = [ls[i]]
        for l in ls[i + 1:]:
            if not l.strip() or re.match(r"[-#]", l):
                break
            block.append(l)
        t = " ".join(block)
        check("[CP8-T21] its note says it reads README.md, SKILL.md and schemas.md as text",
              all(x in t for x in ("README.md", "SKILL.md", "schemas.md")) and re.search(r"as text|reads", t) is not None,
              t[:200])
    print("[CP8-T21] teeth")
    check("[CP8-T21] teeth: the inventory does not list a file that does not exist",
          not any("test_hook_opencode_docs_missing.py" in l for l in ls))


def case_t22():
    print("[CP8-T22] hook docs make no claim about Codex versions that the research could not check")
    d = real_docs()
    p = lint_t22(d)
    check("[CP8-T22] the Codex row is unchanged, no every/older-version claim, any citation is complete", not p, p)
    print("[CP8-T22] teeth")
    check("[CP8-T22] teeth: the honest fixture passes", lint_t22(honest_docs()) == [], lint_t22(honest_docs()))
    every = Docs(HONEST_SEC + "\nThe Codex reply works on every Codex version.\n", "", "")
    check("[CP8-T22] teeth: 'works on every Codex version' is flagged", lint_t22(every) != [])
    older = Docs(HONEST_SEC + "\nThe Codex stop reply is valid on older Codex versions.\n", "", "")
    check("[CP8-T22] teeth: 'valid on older Codex versions' is flagged", lint_t22(older) != [])
    half = Docs(HONEST_SEC + "\nThe Codex reply format was checked against stop.rs.\n", "", "")
    check("[CP8-T22] teeth: a citation without 75a4146, the docs page and the older-versions caveat is flagged",
          lint_t22(half) != [])
    full = Docs(HONEST_SEC + "\nThe Codex reply format was checked against the Codex hooks docs page and stop.rs in "
                "openai/codex at 75a4146. Older Codex versions were not checked.\n", "", "")
    check("[CP8-T22] teeth: a complete citation with the older-versions caveat is accepted",
          lint_t22(full) == [], lint_t22(full))
    edited = Docs(HONEST_SEC.replace("| Codex | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Yes, the stop hook stops",
                                     "| Codex | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Best-effort, the stop hook stops"),
                  "", "")
    check("[CP8-T22] teeth: an edited Codex stop cell is flagged", lint_t22(edited) != [])


CASES = [case_t01, case_t02, case_t03, case_t04, case_t05, case_t06, case_t07, case_t08, case_t09, case_t10, case_t11,
         case_t12, case_t13, case_t14, case_t15, case_t16, case_t17, case_t18, case_t19, case_t20, case_t21, case_t22]


def main():
    global FAIL
    for fn in CASES:
        try:
            fn()
        except Exception as e:  # a crash is a failed check with its own line, never a silent skip
            FAIL += 1
            print(f"  ✗ [CP8-T{fn.__name__[-2:]}] case crashed: {type(e).__name__}: {e}")
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
