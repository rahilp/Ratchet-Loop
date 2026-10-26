#!/usr/bin/env python3
"""
external_review.py — run a Ratchet adversarial review with ANOTHER PROVIDER's model (Claude Code,
OpenAI Codex, Gemini CLI, GitHub Copilot CLI, or any command you configure) and turn its answer into
the findings JSON that `ratchet.py adv-record` accepts. Whichever model hosts the loop, reviewers
from a different provider are preferred; this script is how they are called.

    python3 external_review.py --reviewer codex-correctness --cp CP3 --out <path>

Why: reviewers from the same model family share blind spots. A different provider finds different bugs.

The reviewer gets everything inline — the adversary instructions, the checkpoint, the standards,
the learnings and the full diff (plus untracked files) — so it needs no tools and runs read-only.
The review is stamped with the code fingerprint taken BEFORE it starts; if the code changes
meanwhile, adv-record rejects it.

Who can review comes from .ratchet/config.json → "providers" (any agent CLI: a preset or your own command,
plus the model it runs and, when the model doesn't say it, the company). Each provider gives three reviewers,
<id>-correctness, <id>-standards and <id>-both:

  "providers": [{"id": "codex", "preset": "codex"},
                {"id": "cursor", "preset": "cursor", "model": "gpt-5"},
                {"id": "acme", "company": "acme", "command": ["acme-cli", "review", "--in", "{prompt_file}"]}]

`ratchet.py providers --discover` finds installed agent CLIs, sends each a test request and adds what works.

If the CLI is missing, or it fails (auth, quota, timeout, no parseable answer), this script marks that
provider (its tool plus model; every reviewer on the program when the program is missing or cannot start) unavailable for
`unavailable_ttl_hours`, records it as failed in the open round so it stays out of that round, and exits with code 3 (a provider that says it is out of credits
is instead benched for every lane until the reset time it gave, or `quota_retest_minutes` when it gave none); `ratchet.py reviewers CPn`
then gives that slot to the next alternative, ending with the host's own sub-agent reviewer.

Placeholders in "command": {prompt_file} {prompt} {out_file} {root} {model}. With "stdin": true the prompt
is piped on stdin. Flags differ between CLI versions — give a provider its own "command" if a preset
doesn't match the version you have installed.

The opencode preset gets the prompt on stdin, after a short message and with `--format json`, a fixed `--title`
(without one OpenCode resends the whole prompt in the background to name the session) and `--variant low` (the
provider's own `variant` replaces low; "none" sends no --variant; OpenCode applies a variant only to models that
define it and ignores it for the others, such as GLM except glm-5.2, and Kimi), so a big prompt never meets a
command-line limit. Its answer is read from the text events of the JSON event stream only (never a reasoning or tool event, which
may hold a findings example or the echoed request). An exit 0 with no answer text, or a step that stopped with reason
"length" (out of output room, even when what it wrote looks complete, and even when an error event comes with it), or
a reply in prose with no findings JSON from a run that finished cleanly (exit 0, no error event), is a failed attempt,
never an approval: only that reviewer's slot falls back for this round (a cutoff marker in the round's folder), the
provider is not benched. A usage-limit message in an error event or on stderr (never in the answer text) benches the
provider, as any quota failure does; any other error event is a provider failure, benched as usual, whatever the answer.

A CLI that takes the prompt only as a command-line argument ({prompt}: the copilot, cursor, omp and pi presets, or a
provider `command` with {prompt}) can't be given a prompt larger than the system allows for one argument (on Linux
128 KiB; a big diff gets there). Such a reviewer is not started and exits 3 with a plain reason, like any other
failure, but a size failure never benches or marks the provider and counts against no lane's shared health: only that
reviewer's slot moves on for this round (a cutoff marker in the round's folder) and its review start is given back.
To review large changes with that CLI, give its provider a "command" that reads {prompt_file} or stdin.

The prompt and the CLI's reply are kept in a temp folder (ratchet-<reviewer>-...) while the review runs; it is
removed when the review ends, pass or fail. With --dry-run it is kept, so the prompt can be read.
"""
from __future__ import annotations

import argparse
import errno
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[2]
ENGINE = PLUGIN_ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
AGENT_DIRS = [PLUGIN_ROOT / "agents", Path.home() / ".claude" / "agents"]

# Presets (how to run each known agent CLI) live in ratchet.py (PRESETS), next to the provider registry.

MAX_DIFF = 180_000
MAX_DOC = 40_000
ARGV_MARGIN = 8_192  # bytes kept free under the system's limits, so a command near the edge is refused, not crashed
WINDOWS_MAX_COMMAND_LINE = 32_767  # characters, the whole command line (CreateProcess)


def load_engine():
    spec = importlib.util.spec_from_file_location("ratchet_engine", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(root, *args):
    # core.quotepath=off: a path with non-ASCII characters is printed as raw UTF-8 (e.g. café.py), not quoted
    # and octal-escaped (e.g. "caf\303\251.py") — so the plain unquoted header regex below matches it too.
    r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


_DIFF_FILE_RE = re.compile(r'^diff --git a/(.*?) b/(.*?)\n', re.M)
_DIFF_FILE_QUOTED_RE = re.compile(r'^diff --git "((?:[^"\\]|\\.)*)" "((?:[^"\\]|\\.)*)"\n', re.M)


def _git_unquote(quoted: str) -> str:
    """Undo git's C-style quoting of a pathname (octal byte escapes, plus \\\\, \\" , \\t, \\n), still produced
    for a path with characters git quotes regardless of core.quotepath (quotes, backslashes, control bytes)."""
    buf, i = bytearray(), 0
    while i < len(quoted):
        c = quoted[i]
        if c == "\\" and i + 1 < len(quoted):
            nxt = quoted[i + 1]
            if nxt.isdigit():
                buf.append(int(quoted[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
            esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt)
            if esc is not None:
                buf.extend(esc.encode())
                i += 2
                continue
        buf.extend(c.encode("utf-8"))
        i += 1
    return buf.decode("utf-8", errors="replace")


def _diff_file_chunks(diff_text: str) -> list[tuple[str, str]]:
    """Split one unified diff into [(path, its own chunk text)], in the order git printed them. `path` is the
    'b/' side (the file's new name), or the 'a/' side for a pure deletion. Handles both git's plain header
    (diff --git a/x b/x) and its quoted header (diff --git "a/x" "b/x"), so a chunk is never silently dropped."""
    out = []
    parts = re.split(r"(?m)^(?=diff --git )", diff_text)
    for part in parts:
        if not part.startswith("diff --git "):
            continue
        m = _DIFF_FILE_RE.match(part)
        if m:
            a_path, b_path = m.group(1), m.group(2)
        else:
            mq = _DIFF_FILE_QUOTED_RE.match(part)
            if not mq:
                continue
            a_path, b_path = _git_unquote(mq.group(1)), _git_unquote(mq.group(2))
        out.append((b_path if b_path != "dev/null" else a_path, part))
    return out


_HUNK_ADD_RE = re.compile(r"@@ -0,0 \+1(?:,([1-9][0-9]*))? @@")
_HUNK_DEL_RE = re.compile(r"@@ -1(?:,([1-9][0-9]*))? \+0,0 @@")
_INDEX_ADD_RE = re.compile(r"index 0+\.\.[0-9a-f]+(?: [0-7]{6})?")
_INDEX_DEL_RE = re.compile(r"index [0-9a-f]+\.\.0+(?: [0-7]{6})?")
_NO_NEWLINE = "\\ No newline at end of file"


def _pure_add_delete(chunk: str) -> tuple[str, int] | None:
    """('added', n) or ('deleted', n) when one file's chunk (from _diff_file_chunks) is a textbook pure add or
    delete, else None. Accepts ONLY, in this order: the diff --git line; one 'new file mode' / 'deleted file mode'
    line; one index line with the null side all zeros; '--- /dev/null' + '+++ b/<path>' (add) or '--- a/<path>'
    + '+++ /dev/null' (delete), naming the diff --git line's own path; exactly one '@@ -0,0 +1[,n] @@' (add) or
    '@@ -1[,n] +0,0 @@' (delete) line; exactly n body lines that all start with '+' (add) or '-' (delete); at most
    one trailing '\\ No newline at end of file'. Anything else (a mixed line, a second hunk, a count that does not
    match, a rename/copy/similarity/mode line, extra text) returns None. Lines are split on '\\n' only, never
    str.splitlines(), which also breaks on characters git does not (U+2028, \\x0b, ...)."""
    if not chunk.endswith("\n"):
        return None
    lines = chunk[:-1].split("\n")
    if len(lines) < 7 or not lines[0].startswith("diff --git "):
        return None
    if re.fullmatch(r"new file mode [0-7]{6}", lines[1]):
        kind, sign, index_re, hunk_re = "added", "+", _INDEX_ADD_RE, _HUNK_ADD_RE
        if lines[3] != "--- /dev/null" or not lines[4].startswith("+++ "):
            return None
        b_tok = lines[4][4:]
        if b_tok.endswith("\t") and " " in b_tok:  # git ends a ---/+++ line with a tab when the path has a space
            b_tok = b_tok[:-1]
        if b_tok.startswith("b/"):
            a_tok = "a/" + b_tok[2:]
        elif b_tok.startswith('"b/'):
            a_tok = '"a/' + b_tok[3:]
        else:
            return None
    elif re.fullmatch(r"deleted file mode [0-7]{6}", lines[1]):
        kind, sign, index_re, hunk_re = "deleted", "-", _INDEX_DEL_RE, _HUNK_DEL_RE
        if not lines[3].startswith("--- ") or lines[4] != "+++ /dev/null":
            return None
        a_tok = lines[3][4:]
        if a_tok.endswith("\t") and " " in a_tok:
            a_tok = a_tok[:-1]
        if a_tok.startswith("a/"):
            b_tok = "b/" + a_tok[2:]
        elif a_tok.startswith('"a/'):
            b_tok = '"b/' + a_tok[3:]
        else:
            return None
    else:
        return None
    if lines[0] != f"diff --git {a_tok} {b_tok}" or not index_re.fullmatch(lines[2]):
        return None
    m = hunk_re.fullmatch(lines[5])
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else 1
    body, tail = lines[6:6 + n], lines[6 + n:]
    if len(body) != n or not all(l.startswith(sign) for l in body):
        return None
    if tail not in ([], [_NO_NEWLINE]):
        return None
    return kind, n


def _summarize_pure(chunk: str, kind: str, n: int) -> str:
    """The short stand-in for a pure delete (kind, n from _pure_add_delete): git's own header lines up to and
    including '+++', copied verbatim, then one honest line in place of the '@@' line and the body. A pure add is
    never summarized (neither caller, scoped_diff_parts nor _pack_full_round, passes one), so any other kind is
    refused."""
    if kind != "deleted":
        raise ValueError(f"only a pure delete may be summarized, not {kind!r}")
    head = "\n".join(chunk.split("\n")[:5]) + "\n"
    return head + f"[file deleted; {n} lines of former content omitted]\n"


def collect_diff(root: Path, base_tree: str | None, current_tree: str | None, prev_round_tree: str | None = None) -> str:
    """Everything that changed since the checkpoint started — committed, uncommitted and untracked alike.

    Both sides are tree ids from ratchet.py's fingerprint (a snapshot of the whole working tree), so a
    checkpoint can't hide code from reviewers by committing it or by naming a commit 'ratchet(...)'.

    `prev_round_tree`: round 2+ only (None on round 1, which keeps git's order, except that a round-1 diff over
    the limit with a pure add in it moves the adds after the other files, smallest first). The tree the
    PREVIOUS review round was stamped with: files that changed since then are put first, so a reviewer always
    sees what is new since it last looked, even when the whole diff is too large to include and the rest gets
    cut. When the size limit still cuts content, the files left out are named, not just a generic char count.

    A full (unscoped) round's diff that fits under MAX_DIFF is returned exactly as git prints it. When it does
    not fit, _pack_full_round packs it: always in a later round (with no pure add or delete in it, that gives
    the same result as before), and in round 1 only when it holds a textbook pure add or pure delete
    (_pure_add_delete). A pure delete may be shown as git's own header lines plus one line saying it was deleted
    and how many lines were left out (_summarize_pure), biggest first and only as many as needed, and it counts
    as shown, never as cut.
    An added file is never summarized: every other changed file (modifications, and summarized deletes) is
    packed ahead of the adds, the adds go last (smallest first, so a huge one is the one cut), and an add that
    does not fit is cut and named in the same 'left out or cut short' note as any other cut file. In a later
    round the two groups (changed since the previous round, then the rest) set the print order, each with its
    adds after its other files, but the budget goes to every non-add in both groups before any add, so an add
    changed since the previous round never takes room a real change in the rest needs. A round-1 diff over the limit with no pure add or delete in it
    is cut at MAX_DIFF exactly as before; the no-base_tree fallback above is unchanged.
    """
    if not (base_tree and current_tree):  # non-git project or an old run: the working-tree diff against HEAD
        parts = ["# Diff against HEAD\n", git(root, "diff", "HEAD", "--", ".", ":(exclude).ratchet")]
        for f in [l for l in git(root, "ls-files", "--others", "--exclude-standard").splitlines()
                  if l and not l.startswith(".ratchet/")]:
            p = root / f
            try:
                if p.stat().st_size < 60_000:
                    parts.append(f"\n# NEW FILE {f}\n" + p.read_text(errors="ignore"))
            except OSError:
                pass
        text = "".join(parts)
        if len(text) > MAX_DIFF:
            text = text[:MAX_DIFF] + f"\n\n[diff truncated at {MAX_DIFF} chars — flag anything you could not see]"
        return text
    header = f"# Changes in this checkpoint ({base_tree[:10]} → {current_tree[:10]})\n"
    full_diff = git(root, "diff-tree", "-r", "-p", "--no-color", base_tree, current_tree)
    if not prev_round_tree or prev_round_tree == current_tree:
        text = header + full_diff
        if len(text) <= MAX_DIFF:
            return text
        chunks = _diff_file_chunks(full_diff)
        if not any(_pure_add_delete(c) for _f, c in chunks):  # no pure add or delete: the plain cut, as before
            return text[:MAX_DIFF] + f"\n\n[diff truncated at {MAX_DIFF} chars — flag anything you could not see]"
        return _pack_full_round(header, [chunks])
    chunks = _diff_file_chunks(full_diff)
    changed_since = {f for f in git(root, "diff-tree", "-r", "--no-renames", "--name-only",
                                    prev_round_tree, current_tree).splitlines() if f}
    priority = [(f, c) for f, c in chunks if f in changed_since]
    rest = [(f, c) for f, c in chunks if f not in changed_since]
    note = (f"# Files changed since the previous review round ({', '.join(f for f, _ in priority) or '(none)'}): "
            "their diffs come first below.\n") if priority else \
           "# No files changed since the previous review round; showing the whole diff below.\n"
    if len(header) + len(note) + sum(len(c) for _f, c in chunks) <= MAX_DIFF:
        return header + note + "".join(c for _f, c in priority + rest)
    return _pack_full_round(header + note, [priority, rest])


def _pack_full_round(lead: str, groups: list[list[tuple[str, str]]]) -> str:
    """collect_diff's packing of a full round's diff that is over MAX_DIFF. `groups` is a list of [(path, chunk)]
    lists printed one after another (round 1: one group in git's order; a later round: the files changed since
    the previous round, then the rest). Within each group the non-adds keep git's order and the pure adds
    (_pure_add_delete) follow them, smallest first (a tie keeps git's order). Every pure delete whose summary
    (_summarize_pure) is shorter than its chunk starts summarized; a pure add is never summarized.
    The groups set the print order only, never the budget: every group's non-adds are fitted first (in print
    order), and only then every group's adds (in print order), so a real change in a later group always gets room
    ahead of an add in an earlier one. A non-add that does not fit whole is shown as far as it fits and cut (so
    nothing after it fits either); an add that does not fit whole is cut and skipped, so the files after it still
    get their room. Then the summaries are swapped back to the full chunk, smallest first, while the total stays
    within MAX_DIFF (once a non-add is cut no room is left, so none is), and only then does any room left go to the
    first cut add in that fitting order, shown as far as it fits in its own place. That uses up all the room
    left, so nothing is printed after it; were another chunk ever to follow it, it is cut back to its last whole
    line, so that chunk's 'diff --git' line starts a line of its own. Every cut or left-out file (including one
    that got no room at all) is named, in print order, in the 'left out or cut short' note."""
    items = []  # (path, full chunk, summary or None, is a pure add), in print order
    for group in groups:
        non_adds, adds = [], []
        for f, c in group:
            pure = _pure_add_delete(c)
            if pure and pure[0] == "added":
                adds.append((f, c, None, True))
                continue
            summary = None
            if pure and pure[0] == "deleted":
                t = _summarize_pure(c, *pure)
                if len(t) < len(c):
                    summary = t
            non_adds.append((f, c, summary, False))
        items += non_adds + sorted(adds, key=lambda it: len(it[1]))
    use = {}  # item index -> the text shown for it (absent: left out)
    cut = []  # item indexes named in the note
    total = len(lead)
    # fitting order: every group's non-adds (in print order) before any group's adds (in print order)
    order = [i for i in range(len(items)) if not items[i][3]] + [i for i in range(len(items)) if items[i][3]]
    for i in order:
        _f, c, summary, is_add = items[i]
        t = summary if summary is not None else c
        remaining = MAX_DIFF - total
        if 0 < remaining and len(t) <= remaining:
            use[i] = t
            total += len(t)
            continue
        cut.append(i)
        if not is_add and remaining > 0:  # this file's own diff is what the size limit cuts into
            use[i] = t[:remaining]
            total += remaining
    # swap the summaries back, smallest first (on a tie, the file printed later first), while all fits;
    # a cut add never keeps a delete summarized (when a non-add was cut, no room is left, so nothing is swapped back)
    for i in sorted((i for i in use if items[i][2] is not None), key=lambda i: (len(items[i][1]), -i)):
        full = items[i][1]
        if total - len(use[i]) + len(full) <= MAX_DIFF:
            total += len(full) - len(use[i])
            use[i] = full
    first = next((i for i in cut if items[i][3] and i not in use), None)
    if first is not None and MAX_DIFF - total > 0:  # the first cut add (in fitting order) gets all the room left
        part = items[first][1][:MAX_DIFF - total]
        if any(i in use for i in range(first + 1, len(items))):  # another file follows it: end on a whole line
            part = part[:part.rfind("\n") + 1]
        if part:
            use[first] = part
            total += len(part)
    cut.sort()  # named in print order
    parts = [lead] + [use[i] for i in range(len(items)) if i in use]
    if cut:
        parts.append(f"\n[diff truncated at {MAX_DIFF} chars — left out or cut short (ask about these if they "
                     f"matter): {', '.join(items[i][0] for i in cut)}]")
    return "".join(parts)


def scoped_diff_parts(root: Path, since_tree: str, current_tree: str) -> tuple[str, list[str], list[str]]:
    """Lane H CP6: the diff for a SCOPED round (a fix round, or a small fix made after an approval) -- ONLY what
    changed since `since_tree` (the last round actually reviewed), never the whole checkpoint. Returns
    (text, shown, omitted), up to MAX_DIFF, never cut mid-file. `shown` is every file the
    reviewer saw either in full or, for a textbook pure delete only, as an honest summary; `omitted` is
    every file that is neither, named in the text. When the whole diff fits, it is returned exactly as git
    prints it with rename detection (-M) on (every file's whole diff, nothing summarized; a file moved with most
    of it unchanged is one rename chunk instead of a delete plus an add). New and deleted files are included as
    git's own diff format marks them. When the whole diff does not fit, a textbook pure delete (_pure_add_delete)
    may always be shown instead as git's own header lines plus one line saying the file was deleted and how many
    lines were left out (_summarize_pure); it counts as shown. A pure add is never summarized, whatever the rest
    of the diff contains: added content is what can ship or run, and a file moved and rewritten, or split out of
    a modified file, reaches git as an add, so an add is shown in full or omitted and named (-M only makes a move
    read better; it is not what keeps an edit from being hidden). Only as many deletes are summarized as needed,
    biggest first (a tie goes to the file git prints first): every pure delete whose summary is shorter than its
    full chunk starts summarized. The fitting loop then tries every non-add (a modification, or a pure delete,
    summarized or not) in git's order before any pure add, so a real change never loses its room to a new file
    listed earlier by git; the pure adds themselves are tried smallest first (a tie keeps git's order), mirroring
    _pack_full_round's own grouping. Afterwards summaries are swapped back to the full chunk, smallest first,
    while the total stays within MAX_DIFF. Any other file that does not fit whole (a real modification, or any
    added file) is omitted and named, exactly as before. Shown files still PRINT in git's own order below,
    whatever order they were fitted in."""
    header = f"# Changes since the last round actually reviewed ({since_tree[:10]} → {current_tree[:10]})\n"
    diff = git(root, "diff-tree", "-r", "-p", "-M", "--no-color", since_tree, current_tree)
    if not diff:
        return header + "(no file content differs here -- the change was likely a rename or mode-only edit)\n", [], []
    chunks = _diff_file_chunks(diff)
    if len(header) + sum(len(c) for _f, c in chunks) <= MAX_DIFF:  # it all fits: git's own -M output, nothing summarized
        return header + "".join(c for _f, c in chunks), [f for f, _c in chunks], []
    # only a pure delete may be summarized; a pure add never is, whatever else the diff holds
    summary = {}  # chunk index -> its summarized form, for each pure delete whose summary is shorter
    for i, (_f, c) in enumerate(chunks):
        pure = _pure_add_delete(c)
        if pure and pure[0] == "deleted":
            t = _summarize_pure(c, *pure)
            if len(t) < len(c):
                summary[i] = t
    is_add = {i: bool((pure := _pure_add_delete(c)) and pure[0] == "added") for i, (_f, c) in enumerate(chunks)}
    # fitting order: every non-add in git's order, then the pure adds smallest first (a tie keeps git's order)
    order = [i for i in range(len(chunks)) if not is_add[i]] + \
        sorted((i for i in range(len(chunks)) if is_add[i]), key=lambda i: len(chunks[i][1]))
    use = {}  # chunk index -> the text shown for it (absent: omitted)
    total = len(header)
    for i in order:
        _f, c = chunks[i]
        t = summary.get(i, c)
        if total + len(t) > MAX_DIFF:
            continue
        use[i] = t
        total += len(t)
    # swap summaries back to the full chunk, smallest first (so the biggest stay summarized; on a tie, the file git
    # prints later is restored first), while everything still fits
    for i in sorted((i for i in summary if i in use), key=lambda i: (len(chunks[i][1]), -i)):
        full = chunks[i][1]
        if total - len(use[i]) + len(full) <= MAX_DIFF:
            total += len(full) - len(use[i])
            use[i] = full
    parts, shown, omitted = [header], [], []
    for i, (f, _c) in enumerate(chunks):
        if i in use:
            parts.append(use[i])
            shown.append(f)
        else:
            omitted.append(f)
    if omitted:
        parts.append(f"\n[diff too large: these changed files are NOT shown and were NOT reviewed by you "
                     f"(do not approve without them; say so in a finding): {', '.join(omitted)}]")
    return "".join(parts), shown, omitted


def collect_scoped_diff(root: Path, since_tree: str, current_tree: str) -> str:
    return scoped_diff_parts(root, since_tree, current_tree)[0]


def read_doc(root: Path, rel: str, drop=None) -> str:
    p = root / rel
    if not p.exists():
        return ""
    t = p.read_text(errors="ignore")
    if drop is not None:  # lines to leave out are removed BEFORE the size cut, so they never use up its room
        t = "\n".join(l for l in t.split("\n") if not drop(l))
    return f"\n\n## {rel}\n" + (t[:MAX_DOC] + "\n[truncated]" if len(t) > MAX_DOC else t)


SCOPED_MARK = "<!-- scoped-round -->"  # lines in the adversary doc that apply only to a fix round's scoped diff


def adversary_instructions(scoped: bool = False) -> str:
    for d in AGENT_DIRS:
        for name in ("ratchet-adversary.md",):
            p = d / name
            if p.exists():
                text = re.sub(r"^---\n.*?\n---\n", "", p.read_text(), count=1, flags=re.S).strip()
                # a full round is not told about fix rounds; a scoped one keeps the line, minus its marker
                return "\n".join(l.replace(SCOPED_MARK, "").lstrip() if scoped else l
                                 for l in text.split("\n") if scoped or SCOPED_MARK not in l)
    return "You are an adversarial code reviewer. Assume the code is wrong and prove it."


def build_prompt(eng, ctx, cfg, cp, rid, spec, fp, fixes_path, base_tree=None, nonce=None, rules=None,
                 prev_round_tree=None, scope=None) -> str:
    root = ctx.root
    docs = "".join(read_doc(root, s) for s in cfg.get("standards", []))
    # a fix round lists the findings to fix itself; learnings lines recording accepted findings are left out (the
    # line format is ratchet.py's own: _BULLET_HEAD_RE plus _ACCEPTED_FINDING_MARKER_RE)
    def _accepted_line(l):
        m = eng._BULLET_HEAD_RE.match(l)
        return bool(m and eng._ACCEPTED_FINDING_MARKER_RE.match(m.group(1)))
    docs += read_doc(root, ".ratchet/learnings.md", _accepted_line if scope is not None else None)
    if cp.get("gates", {}).get("ui"):
        docs += read_doc(root, cfg.get("design_md", "DESIGN.md"))
    fixes = ""
    if fixes_path and Path(fixes_path).exists():
        fixes = "\n\n## Fixer rebuttals from the previous round (fixes.md)\n" + Path(fixes_path).read_text()[:MAX_DOC]
    objective = eng.objective_brief(ctx.plan())
    # Lane H CP6: a scoped round (fix_round, or a small fix made after an approval) gets ONLY the diff since the
    # last round actually reviewed -- never the whole checkpoint diff `collect_diff` would otherwise build. Its
    # open findings (or the fact that there are none, for a small fix) are already in `rules` above (one line
    # per finding from `_render_finding_line`, via review_rules).
    code_under_review = (collect_scoped_diff(root, scope["since_tree"], fp) if scope is not None
                         else collect_diff(root, base_tree, fp, prev_round_tree))
    return f"""## {objective}

---
{adversary_instructions(scope is not None)}

---
# This review (non-interactive — everything you need is below; you have no tools and must not ask questions)

Reviewer id: {rid}
Lens: {spec.get('lens', 'both')}
Checkpoint: {cp['id']} — {cp['title']}
Code fingerprint: {fp}

## Checkpoint entry
{json.dumps(cp, indent=2)}

## Standards, learnings{' and DESIGN.md' if cp.get('gates', {}).get('ui') else ''}
{docs or '(no written standards found — review for correctness and consistency with the surrounding code)'}
{fixes}
{("## Rules for this round" + chr(10) + chr(10).join("- " + r for r in rules)) if rules else ""}

## Code under review
{code_under_review}

---
Reply with ONLY one JSON object, no markdown fences, exactly this shape:
{{"reviewer":"{rid}","lens":"{spec.get('lens', 'both')}","checkpoint":"{cp['id']}","fingerprint":"{fp}",
 "verdict":"APPROVE or CHANGES_REQUESTED",
 "findings":[{{"id":"X1","severity":"blocker|major|minor","kind":"short-kebab-label-for-this-type-of-mistake","file":"path","line":0,"rule":"cited rule or failure scenario","description":"...","fix":"...","user_impact":"who is hurt and how, in normal use — required for a blocker or major finding"}},{{"id":"X2","severity":"minor","kind":"...","file":"path","line":0,"rule":"...","description":"...","fix":"...","done_means":[3]}}]}}
An empty findings list means you approve. Every finding needs a cited rule or a concrete failure scenario. `kind` names the TYPE of mistake (e.g. handler-imports-db, unvalidated-input), the same label whenever that type appears again. `user_impact` names who is hurt and how, in normal use, clearing the realistic-use bar above; a blocker, major or unknown-severity finding with no `user_impact` is recorded for the final review instead of blocking. `done_means` is optional: most findings look like X1 above and have no such key. Add it, as X2 does, only when that finding shows one of the objective's "Done means" lines (numbered above) is not met in normal use, listing those line numbers; leave the field out of every other finding (a copied `done_means` makes a finding block). A finding that names a line always blocks, at any severity and any round, unless the rules for this round say the plan was approved before that rule (then the older rules apply; still name the line); one that names none follows the severity rules.
"""


def argv_problem(cmd: list[str], env: dict) -> str | None:
    """Why `cmd` is too large to start (one argument, or the arguments and environment together, over what the
    system accepts), in plain words; None when it fits. Checked before starting, so the CLI is never half-started."""
    if os.name == "nt":
        chars = sum(len(c) + 3 for c in cmd)  # quotes and a space around each argument
        if chars > WINDOWS_MAX_COMMAND_LINE - ARGV_MARGIN:
            return f"its command line would be about {chars:,} characters, over Windows' limit of {WINDOWS_MAX_COMMAND_LINE:,}"
        return None
    sizes = [len(c.encode("utf-8", "surrogateescape")) + 1 for c in cmd]
    if sys.platform.startswith("linux"):
        try:
            page = os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, ValueError, OSError):
            page = 4096
        one_arg = 32 * (page if isinstance(page, int) and page > 0 else 4096)  # Linux's MAX_ARG_STRLEN
        if max(sizes) > one_arg - ARGV_MARGIN:
            return (f"one argument (the prompt) would be {max(sizes):,} bytes, over the system's limit of "
                    f"{one_arg:,} bytes for a single argument")
    try:
        total_max = os.sysconf("SC_ARG_MAX")
    except (AttributeError, ValueError, OSError):
        total_max = 262_144
    if not isinstance(total_max, int) or total_max <= 0:
        total_max = 262_144
    env_bytes = sum(len(k.encode("utf-8", "surrogateescape")) + len(str(v).encode("utf-8", "surrogateescape")) + 2
                    for k, v in env.items())
    total = sum(sizes) + env_bytes + 8 * (len(cmd) + len(env) + 2)
    if total > total_max - ARGV_MARGIN:
        return (f"its arguments and environment would be {total:,} bytes, over the system's limit of "
                f"{total_max:,} bytes")
    return None


def parse_findings(text: str) -> dict | None:
    """Take the first top-level JSON object in the text whose 'findings' key is a list. Uses the stdlib decoder's
    own `raw_decode` (properly escape-aware, unlike a hand-written brace/string scanner) from each '{' in the
    text that is not nested inside an object already decoded; `raw_decode` parses one complete JSON value
    starting at that index and ignores anything after it, so a brace or quote inside an earlier string value can
    never throw off where a later, real JSON object starts or ends. Once a '{' has been decoded (whether or not
    it qualified), every position inside its span is skipped rather than tried on its own, so a finding's own
    nested object (e.g. an "evidence" dict that happens to contain a literal "findings" key) is never mistaken
    for the real top-level reply."""
    text = re.sub(r"```(?:json)?", "", text)
    dec = json.JSONDecoder()
    end = 0
    for m in re.finditer(r"\{", text):
        start = m.start()
        if start < end:
            continue  # nested inside the object just decoded — never considered on its own
        try:
            obj, end = dec.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
            return obj
    return None


def _looks_cut_off(text: str) -> bool:
    """A reply counts as cut off only if it opens what looks like the findings-reply JSON itself — a '{' followed,
    within a short window, by one of the findings-reply keys 'reviewer', 'verdict' or 'findings' — and
    `json.JSONDecoder.raw_decode` (properly escape-aware; see parse_findings) fails to parse a complete JSON value
    there because the text ran out before the object closed: either an unterminated string (the decoder only
    raises this when it scans from an opening quote to the end of the text without finding the matching one) or
    any other decode error positioned at the very end of the (right-stripped) text, where the parser ran out of
    input expecting more. A decode error positioned earlier in the text is a real syntax mistake in an otherwise
    complete reply, not a truncation, and is not mistaken for one. A complete JSON value such as
    {"error": {"message": "quota exceeded"}} carries none of the findings-reply keys, so it is never mistaken for a
    truncated reply, whether on the first call or a retry: it goes straight to unavailable(), same as a plain-text
    error, with no retry wasted and no cutoff.json marker written. A genuinely truncated findings reply (an opening
    brace with no matching close) is retried once before anything is held against the reviewer."""
    stripped = re.sub(r"```(?:json)?", "", text)
    dec = json.JSONDecoder()
    end = len(stripped.rstrip())
    for m in re.finditer(r"\{", stripped):
        start = m.start()
        if not re.search(r'"(?:reviewer|verdict|findings)"\s*:', stripped[start:start + 2000]):
            continue  # not the start of a findings-reply object — some other brace in the text
        try:
            dec.raw_decode(stripped, start)
        except json.JSONDecodeError as e:
            if e.msg.startswith("Unterminated string") or e.pos >= end:
                return True  # this candidate never closes — a genuine truncation
            continue  # a real syntax mistake mid-text, not a truncation — some other brace, keep looking
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer", required=True, help="reviewer id from config 'reviewers'")
    ap.add_argument("--cp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixes", help="fixes.md from the previous round (round 2+)")
    ap.add_argument("--root")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("RATCHET_REVIEW_TIMEOUT", "1200")))
    ap.add_argument("--dry-run", action="store_true", help="write the prompt, don't call the model")
    a = ap.parse_args()

    eng = load_engine()
    ctx = eng.Ctx(eng.find_root(Path(a.root) if a.root else None))
    cfg = ctx.config()
    spec = eng._roster_spec(cfg, a.reviewer)
    if not spec:
        sys.exit(f"unknown reviewer '{a.reviewer}' (add it under config 'providers' or 'reviewers')")
    if spec.get("kind") != "command":
        sys.exit(f"'{a.reviewer}' is kind={spec.get('kind')} — spawn it as a ratchet-adversary sub-agent instead")
    okay, why = eng.reviewer_availability(ctx, cfg, a.reviewer)
    if not okay and "same company as the host" in why:
        print(f"'{a.reviewer}' is {why}. Run `ratchet.py reviewers {a.cp}` for this round's reviewers.", file=sys.stderr)
        sys.exit(3)
    min_t = int(cfg.get("min_review_timeout_sec", 120))
    if a.timeout < min_t:
        sys.exit(f"--timeout must be at least {min_t}s (a shorter one would just knock the reviewer out).")
    cp = eng.cp_by_id(ctx.plan(), a.cp)
    fp = eng.fingerprint(ctx.root)["id"]
    st = ctx.state()
    eng.verify_integrity(ctx, st)  # the reviewer command itself is part of the approved rules
    cps = st["checkpoints"].get(a.cp) or {}
    rnd = cps.get("adv_round") or {}
    if not rnd or rnd.get("recorded") or rnd.get("fingerprint") != fp or a.reviewer not in rnd.get("reviewers", []):
        sys.exit(f"No open review round for {a.cp} that includes '{a.reviewer}' at the current code. "
                 f"Run `ratchet.py reviewers {a.cp}` first and launch exactly the reviewers it lists.")
    _focus, rules, scope = eng.review_rules(ctx, cfg, cp, cps["gates"]["adversarial"], rnd, fp, cps)
    prompt = build_prompt(eng, ctx, cfg, cp, a.reviewer, spec, fp, a.fixes, cps.get("base_tree"), rnd["nonce"], rules,
                          prev_round_tree=rnd.get("prev_round_tree"), scope=scope)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"ratchet-{a.reviewer}-"))
    prompt_file, out_file = work / "prompt.md", work / "last-message.txt"
    prompt_file.write_text(prompt)
    if a.dry_run:
        print(f"prompt written: {prompt_file} ({len(prompt)} chars)")
        return
    try:
        _review(eng, ctx, cfg, a, spec, cp, cps, rnd, st, fp, prompt, out, work, prompt_file, out_file, scope)
    finally:  # the prompt and the CLI's reply are not kept: the review (or its .raw.txt) is the record
        shutil.rmtree(work, ignore_errors=True)


def _review(eng, ctx, cfg, a, spec, cp, cps, rnd, st, fp, prompt, out, work, prompt_file, out_file, scope=None):
    # This round's signing secret must exist before the reviewer runs, so a paid review is never thrown away unsigned.
    if eng.review_key(ctx, a.cp, "adv", rnd["round"], st=st) is None:
        sys.exit(f"This round's review-signing secret is missing. Run `ratchet.py reviewers {a.cp}` again, then "
                 "re-run this reviewer.")
    cmd_t, use_stdin = eng.reviewer_command(cfg, a.reviewer)
    if not cmd_t:
        sys.exit(f"reviewer '{a.reviewer}' has no command: give its provider a 'preset' ({', '.join(eng.PRESETS)}) "
                 "or a 'command'")
    subs = {"{prompt_file}": str(prompt_file), "{prompt}": prompt, "{out_file}": str(out_file),
            "{root}": str(ctx.root), "{model}": str(spec.get("model") or "")}
    cmd = []
    for part in cmd_t:
        for k, v in subs.items():
            part = part.replace(k, v)
        cmd.append(part)
    def round_only(reason, raw=None, unused=False):
        # A failed attempt that holds nothing against the provider: only this reviewer's slot falls back, for this
        # round (the marker in the round's folder; no unavailable_ttl_hours mark, no bench, nothing in the shared health
        # record), so the provider's other slots and every other lane keep using it. `unused`: the provider never ran
        # (a prompt too large for a command line), so the start this round recorded for the reviewer is given back.
        if raw is not None:
            (out.parent / f"{a.reviewer}.raw.txt").write_text(raw)
        if unused:
            eng.refund_review_start(cfg, rnd["nonce"], a.reviewer)
        (out.parent / f"{a.reviewer}.cutoff.json").write_text(json.dumps({"reason": reason, "at": eng.now()}))
        print(f"{a.reviewer}: {reason}. This slot falls back for round {rnd['round']} only — run "
              f"`ratchet.py reviewers {a.cp}` again.", file=sys.stderr)
        sys.exit(3)

    def unavailable(reason, quota=None, program=False, unused=False):
        # A failure benches the failing provider only (its tool plus model), so GLM failing leaves Kimi, which runs
        # through the same OpenCode program, working. A program that is missing takes down every reviewer that runs
        # through it (`program`). `unused`: the provider was not really used (the CLI never ran, or no run of it, the
        # first or the retry, produced any output), so the start this round recorded for the reviewer is given back; a
        # timeout, a run that produced output (even a reply that could not be read, or a non-zero exit after it) and an
        # out-of-credits provider all keep their start. A prompt too large for a command line is not routed here: it
        # never benches or marks a provider (round_only).
        binary = eng.reviewer_binary(cfg, a.reviewer)
        if program:
            same = [r for r in eng.roster(cfg) if eng.reviewer_binary(cfg, r) == binary] or [a.reviewer]
        else:
            same = eng.same_provider_reviewers(cfg, a.reviewer)
        # This round learns of the failure now, not when `reviewers` next runs: a test request that passes in
        # between clears a plain bench, and must not hand the provider its slot back for the rest of the round.
        eng.record_round_failure(ctx, cfg, a.cp, rnd["round"], same, own=a.reviewer)
        if unused and not quota:  # the CLI never ran or exited before answering: this round's own start goes back
            eng.refund_review_start(cfg, rnd["nonce"], a.reviewer)  # (not after a timeout or a reply, nor out of credits)
        hours = None
        if quota:  # out of credits: every lane skips this provider until it said it is back (or a short re-test)
            reason = f"out of credits: {quota[1]}"
            if eng.bench_quota(cfg, a.reviewer, reason, quota[0]):
                print(f"{a.reviewer} UNAVAILABLE: {reason}\n"
                      f"Out of credits: benched for every lane until {quota[0].strftime('%Y-%m-%dT%H:%M')}Z. Run "
                      f"`ratchet.py reviewers {a.cp}`: its slot now goes to the next provider, or to the host's own "
                      "sub-agents.", file=sys.stderr)
                sys.exit(3)
            now_ = eng._dt.datetime.now(eng._dt.timezone.utc)  # the shared record could not be saved: mark this project
            hours = max((quota[0] - now_).total_seconds(), 60) / 3600
        for r in same:
            until = eng.mark_unavailable(ctx, r, reason if r == a.reviewer else f"{binary} failed: {reason}",
                                         hours=hours, quota=bool(quota))
        print(f"{a.reviewer} UNAVAILABLE: {reason}\n"
              f"Marked unavailable until {(until or 'later')[:16]}Z. Run `ratchet.py reviewers {a.cp}`: its slot now "
              "goes to the next provider, or to the host's own sub-agents.", file=sys.stderr)
        sys.exit(3)

    env = dict(os.environ, RATCHET_NESTED_REVIEW="1")
    too_big = argv_problem(cmd, env)
    if too_big:  # never started: a CLI that takes the prompt as an argument can't be handed one this large
        round_only(f"the review prompt ({len(prompt):,} characters) is too large to pass to '{cmd[0]}' as a "
                   f"command-line argument: {too_big}. Give this provider a 'command' that reads {{prompt_file}} "
                   "or stdin to review changes this large", unused=True)
    shown = " ".join(c if len(c) < 80 else c[:40] + "…" for c in cmd)

    sent_at = []  # when each run of the CLI was started: a success only ends a quota bench that began before it
    streams = []  # what event_stream read from each run's output (None: plain text)

    def quota_of(rr, text):
        # Out of credits, as the provider said it. A JSON event stream is read from its stderr (error events are merged
        # into it) only: the model's own answer text can quote a limit message without the provider being out of credits.
        return eng.quota_failure([rr.stderr] if streams and streams[-1] else [rr.stderr, text], cfg)

    def prose_reply(rr, text):
        # A JSON-event run that finished cleanly (exit 0, no error event, nothing about credits on stderr) but whose
        # answer holds no findings JSON did answer: the provider is not at fault, so only this slot falls back (a
        # failed attempt for this round, no bench).
        ev = streams[-1] if streams else None
        return bool(ev) and rr.returncode == 0 and not ev["errors"] and not quota_of(rr, text)

    def run_once():
        print(f"running {a.reviewer}: {shown}", file=sys.stderr)
        sent_at.append(eng._dt.datetime.now(eng._dt.timezone.utc))
        try:
            # run from an empty folder, flagged as nested: the reviewer needs nothing but the prompt, can't touch
            # the project, and its CLI's own hooks must not log this prompt as if the user had typed it
            rr = subprocess.run(cmd, cwd=work, input=prompt if use_stdin else None, capture_output=True, text=True,
                               stdin=None if use_stdin else subprocess.DEVNULL, timeout=a.timeout, env=env)
        except FileNotFoundError:
            unavailable(f"'{cmd[0]}' is not installed or not on PATH", program=True, unused=True)
        except subprocess.TimeoutExpired:  # a hang is never retried — one timeout is enough to sideline it
            unavailable(f"timed out after {a.timeout}s")
        except OSError as e:  # the system refused to start it (E2BIG: the arguments were too large after all, ...)
            if e.errno == errno.E2BIG:
                round_only(f"the review prompt ({len(prompt):,} characters) is too large to pass to '{cmd[0]}' as a "
                           "command-line argument. Give this provider a 'command' that reads {prompt_file} or "
                           "stdin to review changes this large", unused=True)
            unavailable(f"'{cmd[0]}' could not be started: {e.strerror or e}", program=True, unused=True)
        ev = eng.event_stream(rr.stdout)
        streams.append(ev)
        if ev:  # a JSON event stream: the answer is its text events only; an error event reads like stderr
            rr.stdout, rr.stderr = ev["text"], (rr.stderr or "") + "\n".join(ev["errors"])
            raw = rr.stdout + "\n--- stderr ---\n" + rr.stderr
            # A message about credits (an error event or stderr, never the answer text) is the quota bench (CP3). A
            # 'length' finish is a failed attempt for this round only, error event or not. An exit 0 with an empty
            # answer is too, unless an error event came with it: that is a provider failure, benched as usual.
            quota = quota_of(rr, "")
            if ev["length"]:  # out of output room: even a complete-looking reply is not trusted
                if quota:
                    unavailable("the model stopped at its output limit and the provider is out of credits",
                                quota=quota)
                round_only("the model stopped at its output limit (finish reason 'length'), so its reply is not "
                           "trusted as a review", raw)
            if rr.returncode == 0 and not ev["text"].strip() and not quota and not ev["errors"]:
                round_only("the model gave no answer text (exit 0 with an empty answer)", raw)
        tt = (out_file.read_text() if out_file.exists() else "") + "\n" + (rr.stdout or "")
        return rr, tt

    r, text = run_once()
    review = parse_findings(text)
    if review is None and _looks_cut_off(text):
        # A reply cut off mid-answer (an opening brace with no matching close) — not a genuine failure, just an
        # incomplete one — gets one immediate retry before anything is held against this reviewer.
        print(f"{a.reviewer}: reply cut off mid-answer; retrying once", file=sys.stderr)
        out_file.unlink(missing_ok=True)  # stale truncated output from the first try must not leak into the retry
        r2, text2 = run_once()
        review2 = parse_findings(text2)
        if review2 is not None:
            r, text, review = r2, text2, review2
        elif _looks_cut_off(text2):
            # Cut off twice: fall back only THIS slot for this round — no unavailable_ttl_hours mark, so the
            # provider is never sidelined for hours and its other slots keep running (see cmd_reviewers' same-
            # round branch, which reads this marker from the round's own folder).
            round_only("reply cut off mid-answer twice in a row (the reply never finished)",
                       text2 + "\n--- stderr ---\n" + (r2.stderr or ""))
        else:
            # The retry's reply is not cut off at all — a genuine failure (quota, auth, plain error, ...), not
            # another truncation. This goes through the normal unavailable() path, same as any other real failure.
            if prose_reply(r2, text2):
                round_only("the model answered without any findings JSON (a reply in prose)",
                           text2 + "\n--- stderr ---\n" + (r2.stderr or ""))
            (out.parent / f"{a.reviewer}.raw.txt").write_text(text2 + "\n--- stderr ---\n" + (r2.stderr or ""))
            tail = ((r2.stderr or "") + (r2.stdout or "")).strip().splitlines()[-1:] or [""]
            unavailable(f"exit {r2.returncode}, no findings JSON ({tail[0][:160]})",
                        quota=quota_of(r2, text2))
    if review is None:
        if prose_reply(r, text):
            round_only("the model answered without any findings JSON (a reply in prose)",
                       text + "\n--- stderr ---\n" + (r.stderr or ""))
        (out.parent / f"{a.reviewer}.raw.txt").write_text(text + "\n--- stderr ---\n" + (r.stderr or ""))
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-1:] or [""]
        unavailable(f"exit {r.returncode}, no findings JSON ({tail[0][:160]})",
                    quota=quota_of(r, text), unused=r.returncode != 0 and not text.strip())
    review = eng.clean_review_text(review)  # a cut-off emoji escape in the reply must not break signing or records
    # Stamp identity ourselves — never trust the model to echo these correctly. Lane H CP6: a scoped round's
    # files_reviewed is stamped from ITS OWN scope (since the last round actually reviewed), never the whole
    # checkpoint's base_tree -- the reviewer was never shown anything outside that scope.
    base = cps.get("base_tree")
    if scope is not None:
        _t, shown, omitted = scoped_diff_parts(ctx.root, scope["since_tree"], fp)
        # only the files the reviewer was shown, in full or as an honest pure delete summary; a diff over the
        # size limit names what it left out (neither shown in full nor summarized)
        if omitted:
            review["files_reviewed"], review["omitted_files"] = shown, omitted
        else:
            review["files_reviewed"] = list(scope["changed_files"]) or ["(whole diff)"]
    else:
        review["files_reviewed"] = (eng.changed_files(ctx.root, {"kind": "git-tree", "id": base}, {"kind": "git-tree", "id": fp})
                                    if base else review.get("files_reviewed") or ["(whole diff)"])
    review.update(reviewer=a.reviewer, lens=spec.get("lens"), vendor=spec.get("vendor"), round_nonce=rnd["nonce"],
                  checkpoint=a.cp, fingerprint=fp)
    review["findings"] = [f for f in review.get("findings") or [] if isinstance(f, dict)]
    review["verdict"] = "APPROVE" if not review["findings"] else "CHANGES_REQUESTED"
    # Sign it with this round's secret (kept in Ratchet's per-user folder), so adv-record can tell it from a review
    # typed or edited by hand. The secret itself is never printed or written into the project.
    key = eng.review_key(ctx, a.cp, "adv", rnd["round"])
    if key is None:
        sys.exit(f"This round's review-signing secret is missing. Run `ratchet.py reviewers {a.cp}` again, then "
                 "re-run this reviewer.")
    eng.sign_review(key, review, rnd["nonce"], "external_review.py")
    tmp = out.parent / f".{out.name}.{os.getpid()}.tmp"  # written whole, then put in place: never half-written
    try:
        tmp.write_text(json.dumps(review, indent=2))
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    eng.review_succeeded(cfg, a.reviewer, sent_at[-1], ctx)  # a provider that answers is back: a quota bench ends early
    print(f"{a.reviewer}: {review['verdict']} ({len(review['findings'])} findings) → {out}")


if __name__ == "__main__":
    main()
