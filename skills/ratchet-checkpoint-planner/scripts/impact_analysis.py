#!/usr/bin/env python3
"""
impact_analysis.py -- static planning-time helper for the ratchet-checkpoint-planner agent.

Called as a plain OS subprocess (never imported in-process) by the planner agent via the Bash tool
while it is writing a run's checkpoints.json, so it can tell a greenfield checkpoint (only new files)
apart from a brownfield one (touches something that already exists) and, for brownfield/ambiguous
checkpoints, enumerate the predictable re-point/registration/parity consequences of the change up
front. This file is a small subcommand dispatcher; it implements `classify`, `repoint`,
`registration`, `parity`, `hazard`, `merge` and `full` (a composed end-to-end subcommand that runs
the other six for you in one call) -- this module stays stdlib-only (standards.md #1) and out of
skills/ratchet-orchestrator/scripts/, which lane M owns.

------------------------------------------------------------------------------------------ `repoint`
Usage:

    python3 impact_analysis.py repoint <fixture_root> --module-a <dotted.module.a> \
        --module-b <dotted.module.b> --symbol <name>

No stdin is read. Walks every file under <fixture_root>, best-effort statically finding every site
that will need to change when `<symbol>` moves from `<module-a>` to `<module-b>`: monkeypatch-by-name
sites (attribute assignment, setattr, patch()/patch.object()), source-inspection scans that read
module A's own file text/AST and search it for the symbol, and importers (direct `from a import
symbol` or a later `a.symbol` attribute read after a bare `import a`).

stdout: one JSON object, nothing else:

    {"module_a": "...", "module_b": "...", "symbol": "...",
     "findings": [{"file": "<root-relative path>", "line": <int >= 1>,
                   "category": "monkeypatch"|"source-inspection"|"importer"|"manual-check-needed",
                   "detail": "..."}, ...]}

Exit code 0 for every completed scan, including one where some files could not be parsed (each
such file gets a "manual-check-needed" finding; the scan continues over the rest of the tree).
Nonzero is reserved for a genuine usage error (missing <fixture_root>, or a missing
--module-a/--module-b/--symbol flag).

Precision bar: every unresolvable reference surfaces as manual-check-needed; precise (named
monkeypatch/importer/source-inspection) resolution covers static and relative imports only.
inspect.getsource() and path constants defined in another helper file are known limits, not
covered.

----------------------------------------------------------------------------------------- `classify`
Usage:

    python3 impact_analysis.py classify <repo_root>

stdin: a single JSON array of strings -- the checkpoint's `files_likely` list of repo-root-relative
paths, exactly as the plan carries them. An empty array (`[]`) is valid input, not a usage error.

stdout: on every successful classification (including every fail-safe case below), stdout is ONE
JSON object and nothing else:

    {
      "mode": "greenfield-create" | "brownfield-modify",
      "reason": "" | "<non-empty string saying why it is brownfield or why it fell back there>",
      "paths_checked": [<the exact files_likely list given, same paths, same order>]
    }

Exit code 0 for every successful classification, including every fail-safe case (mixed list,
unreadable/missing repo root, empty files_likely). A nonzero exit is reserved for a genuine CLI
usage error (missing subcommand, missing <repo_root> argument, or stdin that is not valid JSON) --
never used just because the repo root could not be read or the list was empty.

Fail-safe rule: "greenfield-create" is reported only when every path in files_likely is confirmed
absent from the repo root. Any doubt at all (a path exists, the repo root can't be read, the list is
empty) reports "brownfield-modify" with a non-empty `reason`, never silently defaulting to greenfield.

------------------------------------------------------------------------------------- `registration`
Usage:

    python3 impact_analysis.py registration <repo_root> <new_test_file_relpath> \
        --readme-list <fixture_readme_file> --inventory <fixture_inventory_file>

No stdin is read. Decides whether a brownfield checkpoint's new test file needs to be registered in
the README's "Running the tests" list and, if it itself does an AST- or text-based source scan of
another project file (reads another file's content, then either `ast.parse`s it or runs a
text-based check -- an `in`/`not in` containment check, `re.search`/`re.match`/`re.findall`,
`assertIn`/`assertNotIn` -- against that content), in the inventory/source-scan fixture too -- so
that registration never gets discovered one at a time at `complete`. `<repo_root>`/
`<new_test_file_relpath>` are used only to read the new test file's own source text (`repo_root /
new_test_file_relpath`); `--readme-list`/`--inventory` point at the plain-text fixture files to
check against (never the real README.md or a real inventory file, which callers are expected to
supply copies of).

stdout: one JSON object, nothing else:

    {"test_file": "<new_test_file_relpath, exactly as given>",
     "items": [{"kind": "readme"|"inventory", "line": "..."},
               {"kind": "manual-check-needed", "detail": "..."}, ...]}

`items[*].line` is the exact text to add (present for `"readme"`/`"inventory"`, absent for
`"manual-check-needed"`, which carries `detail` instead). A README/inventory line counts as already
registered only on an exact match (after stripping surrounding whitespace); a substring match, a
missing `python3 ` prefix, or a different runner never counts. When the new test file cannot be
parsed, a single `manual-check-needed` item is emitted instead of guessing whether it source-scans --
the README item (whose line never depends on parsing) is still emitted, and no inventory item is
ever emitted for a file that could not be parsed. The same applies when the file reads another
file's content but pairs it with no recognized scan/check shape (neither `ast.parse` nor a
recognized text-based check): a `manual-check-needed` item is emitted instead of silently deciding
it doesn't source-scan.

A text-based check (as opposed to `ast.parse`, which is unambiguous evidence of source-level
inspection regardless of what it's reading) only counts as a source scan when the thing being read
looks like Python source: a string-literal read target ending in `.py` (directly or through one hop
of a simple variable assignment), reusing the same literal-path tracking the AST-based detection
already does. A read target whose literal extension is an obviously-data one (`.json`, `.md`,
`.txt`, `.log`) is "no-scan", not ambiguous -- e.g. a behavior test that reads a generated JSON
report and checks `"CP1-X01" in results` is not a project-source scan. A text-based check paired
with a read target that can't be confirmed either way (an unresolved expression, a computed path,
an unrecognized extension -- e.g. a loop variable bound by `for path in ROOT.rglob('*.py')`) is
"ambiguous", surfacing a `manual-check-needed` item -- this is exactly the dynamic/unresolvable
reference the module's precision bar (above) requires to surface, never guessed or silently
dropped.

Exit code 0 for every successful run, including every file that could not be parsed. Nonzero is
reserved for a genuine usage error (missing positional argument, or a missing
--readme-list/--inventory flag). Read-only and idempotent: never writes to --readme-list/--inventory,
and two runs against the same unchanged inputs produce identical items.

----------------------------------------------------------------------------------------- `parity`
Usage:

    python3 impact_analysis.py parity

No positional arguments. stdin: a single JSON object `{"move": null|{"symbol",
"module_from", "module_to"}, "done_when": [...], "baseline": null|{"module", "startup_ms",
"threshold_pct"}}`. A checkpoint is an extraction only when `move` is present and
`module_from != module_to` (both non-empty) -- a same-module rename or a null move is not. stdout:
`{"is_extraction": bool, "items": [...]}`; `items` is always `[]` when not an extraction, else always
exactly one `"startup-parity"` item and one `"behavior-parity"` item, each carrying a human-readable
`line` and an `already_present` flag set by a case-insensitive substring match against `done_when`
("startup"+"parity" for the first, "byte-identical"+("parity"|"behavior") for the second) -- never
duplicated or dropped. See tests/test_impact_analysis_parity_hazard.py's module docstring for this
subcommand's full CLI contract.

----------------------------------------------------------------------------------------- `hazard`
Usage:

    python3 impact_analysis.py hazard

No positional arguments. stdin: `{"tests_command": "<string>"}`. A pure text-pattern count of
`python3`/`python <path>.py` suite invocations in the string (split on `&&`/`;`/`||`); stdout:
`{"hazard": bool, "suites": [...], "detail": str}`, where `hazard` is true exactly when two or more
suite invocations are found (so a lone suite, including `tests/run_all.py` alone, never hazards, but
pairing it with one more suite still does). Never raises, including on an empty or suite-less
string. See tests/test_impact_analysis_parity_hazard.py's module docstring for this subcommand's
full CLI contract.

Interpreter recognition covers bare `python`/`python3`, a versioned form (`python3.12`), and an
absolute/relative path-prefixed form (`/usr/bin/python3`). Each shell segment is tokenized with
`shlex.split` (falling back to a plain whitespace split on a `ValueError` from unbalanced quotes,
etc., so this never raises). After the interpreter token, `-W`/`-X` each consume the next token as
their own argument; `-m`/`-c` mean no script path follows in this invocation at all (the scan for
that segment ends with no suite found); every other dash-prefixed token is a bare flag with no
argument. The first non-dash token reached after all flags are consumed is the candidate script
path, counted as a suite only when it ends in `.py`.

Known limits (intentionally out of scope, so a reviewer judges against this stated bar instead of
finding another untested spelling): an environment-variable prefix on the invocation (e.g. `FOO=bar
python3 ...`); an alternate launcher (`env python3 ...`, `uv run ...`, `py -3 ...`); a subshell or a
pipe; and `-m pytest`-style module invocation -- already excluded by the `-m`/`-c` handling above,
called out here explicitly as intentional rather than an oversight.

------------------------------------------------------------------------------------------------ `merge`
Usage:

    python3 impact_analysis.py merge

No positional arguments. stdin: a single JSON object `{"checkpoint": {"done_when": [...],
"cases": [...]}, "enumerated_items": {"done_when": [...], "cases": [...],
"existing_done_when_override": null|[...]}}`. This is the single most safety-critical subcommand in
this module (CP5): it folds CP2-CP4's enumerated findings into an already-approved checkpoint's
existing done_when/cases, and it must never drop, reword or reorder anything already approved.

Before any of that merging logic runs, `merge` validates the SHAPE of its input strictly (this runs
first, before even the `existing_done_when_override` check below): `checkpoint` must be a JSON
object; its `done_when` key must be PRESENT (never silently defaulted when missing) and a list whose
every element is a string; its `cases` key must be PRESENT and a list whose every element is an
object carrying a non-empty string `id`. `enumerated_items` must be a JSON object whose keys are
EXACTLY `done_when`, `cases` and `existing_done_when_override` -- any other key (a typo like
"done-when", or a stale field name like "test_cases") refuses rather than being read as "nothing to
add" and silently dropped. Its `done_when` key, when present and not null, must be a list of strings;
its `cases` key, when present and not null, must be a list of objects, each one using only the known
case keys (`id`, `title`, `given`, `when`, `then`, `layer`, `edge`, `proves`) and carrying non-empty,
non-whitespace string values for `id`, `given`, `when` and `then` -- stricter than `checkpoint.cases`
on purpose, since a newly enumerated case is new content a builder must be able to act on immediately,
unlike an existing case which passes through unchanged no matter what it carries. Its
`existing_done_when_override`, when present and not null, must be a list of strings (never a plain
string, which would otherwise compare character-by-character against `checkpoint.done_when` and
could pass by coincidence). Any shape violation refuses (see below) with reason "invalid_shape" and a
`detail` naming exactly which field had the wrong shape and what was expected -- this is a controlled
refusal, never a bare Python traceback, and it is why a bare `list(...)` coercion is never used on any
of this input: `list("one line")` would silently shred a one-line `done_when` authored unwrapped into
one entry per character, and `list({"id": "x", ...})` would silently replace a `cases` dict's values
with its own key names. Catching that shape up front, before any appending logic runs, is what makes
the "never drop or reword an already-approved line" guarantee hold for a malformed caller too, not
just a well-behaved one.

stdout on SUCCESS: exit code 0, ONE JSON object and nothing else: `{"merged": {"done_when": [...],
"cases": [...]}}`. The existing `checkpoint.done_when` lines come out byte-for-byte, in their
original order; every enumerated `done_when` string not already verbatim-present is appended after
them, in the order given, skipping exact duplicates (so re-running `merge` on its own prior output
with the same enumerated_items is idempotent). Every existing `checkpoint.cases` dict passes through
completely unchanged; each enumerated case is appended when its `id` is new, skipped when an
existing case with that `id` and every field already matches it verbatim (a duplicate), or refused
when its `id` matches an existing case whose content differs in any field.

stdout on REFUSAL: exit code 3 -- reserved exclusively for "merge refused due to corruption or a
malformed shape", distinct from exit code 2 (a plain CLI usage error: malformed stdin JSON, or stdin
missing the top-level "checkpoint" or "enumerated_items" key entirely). On a refusal, stdout is EMPTY
(no partial or speculative output is ever produced -- refusal is all-or-nothing for the whole call,
even when the same enumerated_items also carries a genuinely new, non-colliding item), and stderr
carries ONE JSON object: `{"refused": true, "reason":
"invalid_shape"|"done_when_override_mismatch"|"case_id_collision", "detail": "<human-readable message
naming the offending field, done_when index/text, or case id>"}`. A case dict with no `id` field at
all is always an "invalid_shape" refusal (caught by the shape validation above), never misreported as
a "case_id_collision" against some unrelated existing case. A "case_id_collision" detail distinguishes
the two ways an id can collide: when the id already existed in `checkpoint.cases` before this call,
the detail says so; when the id is only shared between two of the NEW `enumerated_items.cases` (a
sibling collision within the same call, with no such id in `checkpoint.cases` at all), the detail says
that instead, so the caller is never sent looking for an existing case that never existed.

The optional `enumerated_items.existing_done_when_override` field is a real, optional production
feature: a defense-in-depth guard rail any caller (not just a test) may use to assert what it
believes `checkpoint.done_when` currently looks like. When present (not null), BEFORE doing anything
else (after shape validation), `merge` compares it element-by-element against the real
`checkpoint.done_when`. Any divergence at any index (a reworded line, a dropped line, two lines
swapped) refuses with reason "done_when_override_mismatch", naming the first diverging index and the
expected vs. actual text there -- one equality check catches all three corruption shapes uniformly.
It is never required for a normal merge (CP2-CP4's real scans never send it) and never changes a
successful merge's output in any way; it only ever makes `merge` refuse instead of silently trusting
a caller's claim about the existing content. See tests/test_impact_analysis_additive.py's module
docstring for this subcommand's full CLI contract and the design reasoning behind this field.

------------------------------------------------------------------------------------------------ `full`
Usage:

    python3 impact_analysis.py full <fixture_root> --checkpoint <checkpoint_json_path>

No stdin is read. A composed end-to-end subcommand that runs `classify`, `repoint`, `registration`,
`parity`, `hazard` and `merge` -- all of this module's other subcommands -- in one call, for the
common case of a checkpoint describing at most one symbol move and at most one new test file. The
planner MAY call this directly as a shortcut; it should still fall back to calling the individual
subcommands above when it needs finer control (a checkpoint moving several symbols, several new test
files, or anything else `full`'s simplifying assumptions don't fit).

`<fixture_root>`: the root directory to scan, the same meaning `repoint`'s `<fixture_root>` and
`classify`'s `<repo_root>` already carry. `--checkpoint <path>`: a path (absolute, or relative to the
current working directory) to a checkpoint JSON file shaped like a real checkpoints.json entry, plus
three extra optional keys this composed command alone reads:

    {
      "files_likely": [...],      // required; classify() input
      "move": null | {"symbol": "...", "module_from": "...", "module_to": "..."},  // optional
      "baseline": null | {"module": "...", "startup_ms": <num>, "threshold_pct": <num>},  // optional
      "done_when": [...],         // required
      "cases": [...],             // required
      "tests": {"command": "..."},  // optional; omitted -> hazard skipped
      "readme_list": null | "<fixture-root-relative path>",       // optional
      "inventory": null | "<fixture-root-relative path>",         // optional
      "new_test_file": null | "<fixture-root-relative path>"      // optional
    }

In this fixed order:

  1. Runs `classify(<fixture_root>, files_likely)`, deciding `mode`/`reason`.
  2. When `mode != "greenfield-create"` and `move` is not null: runs `repoint_scan` using
     `move.module_from`/`move.module_to`/`move.symbol`. Otherwise `repoint` is `null` in the output
     (never an empty-but-present dict, so a caller can tell "skipped" apart from "ran, found
     nothing").
  3. When `mode != "greenfield-create"` and `new_test_file` is non-null: runs `registration_check`,
     passing `readme_list`'s and `inventory`'s fixture-root-relative paths when given, else `None`
     for whichever is absent (registration_check's own README-item and inventory-item decisions each
     already handle a missing path by emitting a manual-check-needed item in its place instead of
     crashing or silently skipping). Otherwise `registration` is `null`.
  4. Always runs `parity_check(move, done_when, baseline)`.
  5. When `tests.command` is present: runs `hazard_check(tests.command)`. Otherwise `hazard` is
     `{"hazard": false, "suites": [], "detail": ""}`.
  6. Builds `enumerated_items.done_when` (and `enumerated_items.cases: []` -- this composed pass never
     invents new planned cases, only done_when lines), using the same plain-language conversion
     documented in agents/ratchet-checkpoint-planner.md: one line per repoint finding
     (`f"Re-point {file}:{line} ({category}): {detail}"`), one line per registration item
     (`f"Register in the README test list: {line}"` / `f"Register in the test-scan inventory:
     {line}"` / `f"Manual check needed (registration): {detail}"`), one line per parity item
     whose `already_present` is false (its own `line`, verbatim), and -- when step 5's `hazard` is
     true -- one line converting it too (`f"Report-race hazard: {detail}"`), so a true hazard is not
     only reported in this call's own stdout but also persisted into the approved checkpoint's
     done_when list. Then calls
     `merge_checkpoint({"done_when": done_when, "cases": cases}, enumerated_items)`.

A successful merge OVERWRITES the `--checkpoint` file on disk, in place, replacing only its
`done_when` and `cases` keys with the merge's result and leaving every other top-level key's VALUE
unchanged -- the file itself is rewritten whole via `json.dumps`, so whitespace/formatting can
differ even though no other field's value changes; this is not a literal byte-for-byte-unchanged
file. This happens on every successful invocation, unconditionally. A merge refusal is propagated:
exit 3, the refusal JSON on stderr, no stdout, and no write to disk.

stdout on success: exit code 0, ONE JSON object: `{"mode", "reason", "repoint", "registration",
"parity", "hazard", "merged"}`.

Nonzero exit is reserved for a genuine CLI usage error (exit 2 -- missing `<fixture_root>`, missing
`--checkpoint`, a `--checkpoint` file that cannot be read or does not parse as JSON, or one missing
the required `files_likely`/`done_when`/`cases` keys), or a merge refusal (exit 3).

Idempotent: running `full` twice in a row against the same `--checkpoint` path, with no change to the
fixture tree in between, adds nothing new the second time -- the second run's repoint/registration
findings regenerate the identical line text (merge's own exact-text dedup catches them), and the
second run's parity items now report `already_present: true` for any item this pass's own first run
already added.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sys
from pathlib import Path


def _usage_error(message: str) -> None:
    """Prints an actionable message to stderr and exits nonzero. Reserved for genuine CLI usage
    errors only (per the module docstring) -- never for a classification that merely couldn't be
    certain, which must still exit 0 and fail safe to brownfield-modify instead."""
    print(f"impact_analysis.py: {message}", file=sys.stderr)
    sys.exit(2)


def _read_files_likely_from_stdin() -> list:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as exc:
        _usage_error(f"stdin must be a JSON array of path strings, got invalid JSON: {exc}")
        return []  # unreachable; _usage_error exits
    if not isinstance(data, list):
        _usage_error("stdin must be a JSON array of path strings")
    return data


def classify(repo_root_arg: str, files_likely: list) -> dict:
    """Decides whether a checkpoint's files_likely paths are all new (greenfield-create) or whether
    at least one already exists under repo_root (brownfield-modify), failing safe to
    brownfield-modify -- with a non-empty reason -- whenever there is any doubt at all: an empty
    files_likely list, a repo root that cannot be read, or a mix of new and existing paths. Never
    raises; every input shape this function is given for a classification (as opposed to a CLI
    usage error, handled by the caller) produces a verdict dict."""
    paths_checked = list(files_likely)

    if not paths_checked:
        return {
            "mode": "brownfield-modify",
            "reason": "files_likely was empty; nothing to classify, so this fails safe rather than "
                       "defaulting to greenfield-create.",
            "paths_checked": paths_checked,
        }

    repo_root = Path(repo_root_arg)
    try:
        root_ok = repo_root.is_dir()
    except OSError:
        root_ok = False
    if not root_ok:
        return {
            "mode": "brownfield-modify",
            "reason": f"repo root '{repo_root_arg}' could not be read (missing or not a directory); "
                       "falling back to brownfield-modify rather than assuming greenfield-create.",
            "paths_checked": paths_checked,
        }

    existing = []
    for rel in paths_checked:
        try:
            # lstat (not stat/Path.exists) so a dangling symlink -- a directory entry that is
            # present but whose target is absent -- still counts as an existing path: following
            # symlinks would report absence for it, wrongly treating a real existing entry as new.
            # Only a confirmed FileNotFoundError means absent; any other outcome (success, or any
            # other OSError such as PermissionError/NotADirectoryError) is existing/doubt and fails
            # safe to brownfield, never to greenfield.
            os.lstat(repo_root / rel)
            exists = True
        except FileNotFoundError:
            exists = False
        except OSError:
            exists = True
        if exists:
            existing.append(rel)

    if existing:
        return {
            "mode": "brownfield-modify",
            "reason": "already exists in the repo root: " + ", ".join(existing),
            "paths_checked": paths_checked,
        }

    return {
        "mode": "greenfield-create",
        "reason": "",
        "paths_checked": paths_checked,
    }


def cmd_classify(argv: list) -> int:
    if not argv:
        _usage_error("classify requires a <repo_root> argument")
        return 2  # unreachable; _usage_error exits
    repo_root_arg = argv[0]
    files_likely = _read_files_likely_from_stdin()
    verdict = classify(repo_root_arg, files_likely)
    print(json.dumps(verdict))
    return 0


# ------------------------------------------------------------------------------------------------
# `repoint`: find every re-point site for a moved symbol (CP2).
#
# A small, deliberately best-effort static pass (standards.md #1: stdlib only). It never tries to
# do real data-flow analysis -- that is explicitly out of scope (the module docstring and the
# planner's own done_when say so). When genuinely unsure whether something is a real reference, it
# prefers to flag it (or report "manual-check-needed") over silently dropping it.
# ------------------------------------------------------------------------------------------------

def _raw_dotted(node) -> "str | None":
    """Flattens a pure Name/Attribute chain (e.g. `pkg.module_a` or `module_a`) into a dotted
    string, so it can be compared against a module's dotted path. Returns None for anything that
    isn't a plain Name/Attribute chain (a call result, a subscript, etc.) -- those can never denote
    a module reference regardless of alias resolution."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _raw_dotted(node.value)
        if base is None:
            return None
        return base + "." + node.attr
    return None


def _collect_import_aliases(tree: ast.AST, rel: str) -> dict:
    """Walks the whole file (not just top-level statements -- an import can appear anywhere) and
    builds a map of bound local name -> dotted module path, covering `import a.b as x` and
    `from a import b as x` style aliasing. A bare `import a.b` with no `as` needs no entry here: its
    dotted chain `a.b` already matches the module's own dotted path directly.

    A relative ImportFrom (`from . import module_a [as x]`, `from .m import y [as z]`, `from ..
    import module_a`, ...) is resolved against the scanned file's own package context (`rel`) the
    same way `_resolve_relative_import` resolves it for the direct-symbol-import case, so a
    relatively-imported module or symbol binds to its fully dotted path (e.g. 'pkg.module_a')
    instead of being silently skipped -- this used to only work for the `from .module_a import foo`
    spelling; `from . import module_a [as x]` and a deeper `from .. import x` are resolved the same
    way now. An ImportFrom whose relative level cannot be resolved against this file's location
    (node.level too deep for the fixture tree) contributes no alias here; the unresolved-receiver
    fallback in `_scan_file_for_repoint` still catches any site that depended on it, as
    manual-check-needed rather than silently nothing."""
    aliases: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_relative_import(node, rel) if node.level else node.module
            if not resolved_module:
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = f"{resolved_module}.{alias.name}"
    return aliases


def _resolves_to_module(node, aliases: dict, module_dotted: str) -> bool:
    """True when the expression `node` (a Name or Attribute chain) refers to the module object
    itself (not one of its attributes), either directly (`import pkg.module_a` then `pkg.module_a`)
    or through an alias (`import pkg.module_a as module_a` then `module_a`), including an alias
    resolved only on the leading component of a longer attribute chain (`import pkg as p` then
    `p.module_a`)."""
    raw = _raw_dotted(node)
    if raw is None:
        return False
    if raw == module_dotted:
        return True
    if aliases.get(raw) == module_dotted:
        return True
    head, _, rest = raw.partition(".")
    if head in aliases:
        substituted = aliases[head] + ("." + rest if rest else "")
        if substituted == module_dotted:
            return True
    return False


def _package_dotted_for_file(rel: str) -> str:
    """The dotted package path of the directory containing a scanned file (fixture-root-relative,
    forward slashes), used to resolve relative ImportFrom statements (`from .module_a import foo`)
    against the file's own package context. The fixture root stands in for that dotted path's own
    root, matching how `module_a` is expressed relative to the same root (e.g. 'pkg.module_a')."""
    posix_rel = rel.replace("\\", "/")
    if "/" not in posix_rel:
        return ""
    return posix_rel.rsplit("/", 1)[0].replace("/", ".")


def _resolve_relative_import(node: "ast.ImportFrom", rel: str) -> "str | None":
    """Resolves an ImportFrom's dotted module path, honoring `node.level` (the relative-import dots)
    against the scanned file's own package context, so `from .module_a import foo` inside
    pkg/consumer.py resolves to 'pkg.module_a' instead of failing to match module_a's dotted path at
    all. Returns None when the level goes above the package root the fixture tree implies -- the
    caller then reports 'manual-check-needed' rather than guessing or silently dropping it (per the
    no-false-precision rule)."""
    package = _package_dotted_for_file(rel)
    parts = package.split(".") if package else []
    keep = len(parts) - (node.level - 1)
    if keep < 0:
        return None
    base = ".".join(parts[:keep])
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base or None


def _string_consts_in_order(node) -> list:
    """String constants under `node`, in real left-to-right source order. `ast.walk` is
    breadth-first (it would report `"module_a.py"` before `"pkg"` for `HERE / "pkg" /
    "module_a.py"`, since the outer BinOp's right-hand child is shallower than the inner BinOp's
    children), so a depth-first, field-order traversal is needed here instead."""
    out = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    for child in ast.iter_child_nodes(node):
        out.extend(_string_consts_in_order(child))
    return out


def _module_a_relpath(module_a: str) -> str:
    """module_a's dotted path expressed as a posix-style relative file path (e.g. 'pkg.module_a' ->
    'pkg/module_a.py'), so a literal path expression can be normalized and compared against it
    regardless of whether it is spelled as one combined string ("pkg/module_a.py") or as separate
    Path-division operands (`Path("pkg") / "module_a.py"`) -- both must be recognized as the same
    file."""
    return module_a.replace(".", "/") + ".py"


def _path_consts_match_module_a(consts: list, basename: str, relpath: str) -> bool:
    """True when the literal string components of a path expression (in source order, however many
    separate Div-joined segments they came from) denote module A's own file: either the full
    relative path or just the basename, joined back together and compared, not just the final
    string constant's exact spelling."""
    if not consts:
        return False
    joined = "/".join(consts)
    return (joined == relpath or joined.endswith("/" + relpath)
            or joined == basename or joined.endswith("/" + basename))


def _collect_module_a_file_vars(tree: ast.AST, module_a: str) -> set:
    """First pass over the whole file: a bare-name variable is treated as 'the path to module A's
    own file' when the string constants that build its value (e.g. `HERE / "pkg" / "module_a.py"`,
    or a single combined literal `"pkg/module_a.py"`) normalize to module A's relative path or
    basename. Deliberately shallow (no real data-flow) -- it only needs to catch the one hop the
    planned fixtures exercise."""
    basename = module_a.rsplit(".", 1)[-1] + ".py"
    relpath = _module_a_relpath(module_a)
    path_vars = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            consts = _string_consts_in_order(node.value)
            if _path_consts_match_module_a(consts, basename, relpath):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        path_vars.add(target.id)
    return path_vars


def _refers_to_module_a_file(node, path_vars: set, module_a: str) -> bool:
    if isinstance(node, ast.Name) and node.id in path_vars:
        return True
    basename = module_a.rsplit(".", 1)[-1] + ".py"
    relpath = _module_a_relpath(module_a)
    consts = _string_consts_in_order(node)
    return _path_consts_match_module_a(consts, basename, relpath)


def _reads_module_a_source(tree: ast.AST, path_vars: set, module_a: str) -> bool:
    """Best-effort detection of 'this file opens module A's own file and reads its text/source' --
    `<path-to-module_a.py>.read_text()`/`.read()`, or `open(<path-to-module_a.py>).read()`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "read"):
            if _refers_to_module_a_file(func.value, path_vars, module_a):
                return True
        elif isinstance(func, ast.Name) and func.id == "open" and node.args:
            if _refers_to_module_a_file(node.args[0], path_vars, module_a):
                return True
    return False


def _collection_contains_symbol(node, symbol: str) -> bool:
    """True when `node` is a Set/List/Tuple literal whose elements include the symbol name as a
    string constant (e.g. `{'foo'}`, `['foo', 'bar']`), so a containment/subset check against such a
    literal collection is recognized as a source-inspection reference to the symbol."""
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return any(isinstance(e, ast.Constant) and e.value == symbol for e in node.elts)
    return False


def _collect_symbol_literal_vars(tree: ast.AST, symbol: str) -> set:
    """Bare-name variables assigned a literal collection containing the symbol name (e.g. `expected =
    {'foo'}`), so a later containment/subset comparison using the variable name (`expected <=
    defined`) is still recognized even though the literal itself is not a direct Compare operand."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _collection_contains_symbol(node.value, symbol):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _arg(call_node: "ast.Call", position: int, keyword_name: str):
    """Resolves one argument of a Call node by either position or keyword name: the positional
    argument at `position` when the call supplies that many positional args, else the keyword
    argument named `keyword_name` when present, else None. Shared by every setattr/patch/
    patch.object detector below so a call spelled with keyword arguments (`patch(target=...)`,
    `monkeypatch.setattr(target=module_a, name="foo", value=...)`, `patch.object(target=X,
    attribute="foo")`) is read the same as its positional spelling instead of being silently
    missed."""
    if len(call_node.args) > position:
        return call_node.args[position]
    for kw in call_node.keywords:
        if kw.arg == keyword_name:
            return kw.value
    return None


def _is_setattr_call(func) -> bool:
    """True for bare `setattr(...)` or an attribute call ending in `.setattr` (e.g. pytest's
    `monkeypatch.setattr(...)`)."""
    if isinstance(func, ast.Name):
        return func.id == "setattr"
    if isinstance(func, ast.Attribute):
        return func.attr == "setattr"
    return False


def _is_patch_call(func, aliases: dict) -> bool:
    """True for `patch(...)` (a bare name resolving to `patch`, directly or through an import
    alias such as `from unittest.mock import patch as mock_patch`) or an attribute access ending in
    `.patch` (e.g. `mock.patch(...)`)."""
    if isinstance(func, ast.Name):
        resolved = aliases.get(func.id, func.id)
        return resolved.rsplit(".", 1)[-1] == "patch"
    if isinstance(func, ast.Attribute):
        return func.attr == "patch"
    return False


def _file_references_module_a(tree: ast.AST, aliases: dict, module_a: str) -> bool:
    """True when this file has ANY static connection to module_a: an import of it (bare, aliased,
    or a relative form already resolved into `aliases`), or a string literal anywhere in the file
    naming its dotted path or its file name/basename. Used to gate the general string-literal
    fallback (rule 3 below) so it only fires inside a file already confirmed relevant to module_a
    -- never in a file with no connection to it at all, which would reintroduce noise."""
    if any(v == module_a or v.startswith(module_a + ".") for v in aliases.values()):
        return True
    basename = module_a.rsplit(".", 1)[-1] + ".py"
    relpath = _module_a_relpath(module_a)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_a or alias.name.startswith(module_a + "."):
                    return True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if (v == module_a or v == basename or v == relpath
                    or v.endswith("/" + relpath) or v.endswith("/" + basename)):
                return True
    return False


def _is_patch_object_call(func, aliases: dict) -> bool:
    """True for `patch.object(...)` / `mock.patch.object(...)` -- an attribute access named
    `object` whose base resolves (directly or through an import alias) to something ending in
    `patch`."""
    if not (isinstance(func, ast.Attribute) and func.attr == "object"):
        return False
    base_dotted = _raw_dotted(func.value)
    if base_dotted is None:
        return False
    resolved = aliases.get(base_dotted, base_dotted)
    return resolved.rsplit(".", 1)[-1] == "patch"


def _scan_file_for_repoint(tree: ast.AST, filename: str, module_a: str, module_b: str, symbol: str) -> list:
    """Finds every monkeypatch/source-inspection/importer site for `symbol` on `module_a` within a
    single already-parsed file's AST. Returns a list of (lineno, category, detail) tuples."""
    out = []
    aliases = _collect_import_aliases(tree, filename)

    # -- importer: `from <module_a> import <symbol>`, including a relative spelling
    #    (`from .module_a import symbol`) resolved against this file's own package context ------
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                resolved_module = _resolve_relative_import(node, filename)
                if resolved_module is None:
                    out.append((node.lineno, "manual-check-needed",
                                 f"relative import (level={node.level}) whose package context "
                                 "could not be determined"))
                    continue
            else:
                resolved_module = node.module
            if resolved_module == module_a:
                for alias in node.names:
                    dots = "." * node.level
                    if alias.name == "*":
                        # star import from module_a: a bare `foo()` call anywhere after this is
                        # impossible to statically confirm or rule out without re-opening the
                        # scope into a full usage scan of the rest of the file (out of scope -- see
                        # module docstring), so the import statement itself is flagged once, at its
                        # own line, as manual-check-needed rather than silently producing nothing.
                        out.append((node.lineno, "manual-check-needed",
                                     f"star import: from {dots}{node.module or ''} import * -- "
                                     f"cannot statically confirm whether {symbol!r} is used after "
                                     "this without a full usage scan"))
                    elif alias.name == symbol:
                        out.append((node.lineno, "importer",
                                     f"direct import: from {dots}{node.module or ''} import {symbol}"))

    # -- monkeypatch + importer-by-attribute, anywhere in the file (not just top-level/test-body
    #    children -- a real patch can be hidden one call away inside a helper). Whenever the
    #    attribute/setattr-family receiver carries the literal symbol name but does NOT resolve to
    #    module_a through any tracked binding -- meaning it can be neither confirmed nor ruled out
    #    (e.g. a module loaded dynamically via importlib and then patched/read by name) -- this is
    #    reported as 'manual-check-needed' rather than silently dropped. This is a general
    #    safety-net rule, not a per-idiom special case; it never attempts to track
    #    importlib.import_module/spec_from_file_location data flow, which is explicitly out of
    #    scope (see module docstring). --------------------------------------------------------------
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == symbol:
                    if _resolves_to_module(target.value, aliases, module_a):
                        out.append((node.lineno, "monkeypatch",
                                     f"attribute assignment: {module_a}.{symbol} = ..."))
                    else:
                        out.append((node.lineno, "manual-check-needed",
                                     f"unresolved receiver.{symbol} assignment: could not confirm "
                                     f"whether the receiver is {module_a}"))
        elif isinstance(node, ast.Attribute) and node.attr == symbol and isinstance(node.ctx, ast.Load):
            if _resolves_to_module(node.value, aliases, module_a):
                out.append((node.lineno, "importer",
                             f"attribute read of {module_a}.{symbol} after a plain import"))
            else:
                out.append((node.lineno, "manual-check-needed",
                             f"unresolved receiver.{symbol} read: could not confirm whether the "
                             f"receiver is {module_a}"))
        elif isinstance(node, ast.Call):
            func = node.func
            if _is_setattr_call(func) and (node.args or node.keywords):
                # covers bare `setattr(module_a, "foo", ...)` and pytest's
                # `monkeypatch.setattr(module_a, "foo", ...)` / `monkeypatch.setattr("pkg.module_a.foo", ...)`,
                # by position or by the `target`/`name` keyword names (via `_arg`) -- pytest's real
                # MonkeyPatch.setattr signature is `setattr(target, name=notset, value=notset,
                # raising=True)`; bare builtin `setattr()` takes no keywords at all, so only
                # `target` is ever the correct keyword to resolve here.
                first = _arg(node, 0, "target")
                name_arg = _arg(node, 1, "name")
                target_ok = first is not None and _resolves_to_module(first, aliases, module_a)
                name_is_symbol = isinstance(name_arg, ast.Constant) and name_arg.value == symbol
                if target_ok and name_is_symbol:
                    out.append((node.lineno, "monkeypatch",
                                 f"setattr({module_a}, {symbol!r}, ...)"))
                elif (isinstance(first, ast.Constant) and isinstance(first.value, str)
                        and first.value == f"{module_a}.{symbol}"):
                    out.append((node.lineno, "monkeypatch",
                                 f'setattr("{module_a}.{symbol}", ...)'))
                elif name_is_symbol and not target_ok:
                    out.append((node.lineno, "manual-check-needed",
                                 f"setattr(<unresolved receiver>, {symbol!r}, ...): could not "
                                 f"confirm whether the receiver is {module_a}"))
            elif _is_patch_call(func, aliases) and (node.args or node.keywords):
                # `target` is positional-or-keyword (via `_arg`).
                first = _arg(node, 0, "target")
                if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                        and first.value == f"{module_a}.{symbol}"):
                    out.append((node.lineno, "monkeypatch",
                                 f'patch("{module_a}.{symbol}")'))
            elif _is_patch_object_call(func, aliases) and (node.args or node.keywords):
                # `target`/`attribute` are each positional-or-keyword (via `_arg`).
                first = _arg(node, 0, "target")
                name_arg = _arg(node, 1, "attribute")
                target_ok = first is not None and _resolves_to_module(first, aliases, module_a)
                name_is_symbol = isinstance(name_arg, ast.Constant) and name_arg.value == symbol
                if target_ok and name_is_symbol:
                    out.append((node.lineno, "monkeypatch",
                                 f"patch.object({module_a}, {symbol!r})"))
                elif name_is_symbol and not target_ok:
                    out.append((node.lineno, "manual-check-needed",
                                 f"patch.object(<unresolved receiver>, {symbol!r}): could not "
                                 f"confirm whether the receiver is {module_a}"))

    # -- source-inspection: reads module_a's own file text/source and searches it for the literal
    #    symbol name (best-effort; see module docstring for the scope limit) --------------------
    path_vars = _collect_module_a_file_vars(tree, module_a)
    if _reads_module_a_source(tree, path_vars, module_a):
        symbol_literal_vars = _collect_symbol_literal_vars(tree, symbol)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                hit = any(
                    (isinstance(o, ast.Constant) and o.value == symbol)
                    or _collection_contains_symbol(o, symbol)
                    or (isinstance(o, ast.Name) and o.id in symbol_literal_vars)
                    for o in operands
                )
                if hit:
                    out.append((node.lineno, "source-inspection",
                                 f"reads {module_a}'s source text and checks for {symbol!r}"))

    # -- general string-literal fallback: in a file already confirmed relevant to module_a (an
    #    import of it, or a string literal naming its path/filename), ANY OTHER string literal
    #    that exactly equals the symbol name is reported manual-check-needed, unless that same line
    #    was already captured by one of the precise categories above (monkeypatch/source-inspection/
    #    importer). Deliberately cheap and a bit noisy -- it is fine within a file that's already
    #    confirmed relevant to module_a (those are rare); it is never applied to a file with no
    #    connection to module_a at all. Picks up dict-key inventories, assertIn(...), re.search(...)
    #    and similar string-literal idioms without parsing each one individually. ------------------
    if _file_references_module_a(tree, aliases, module_a):
        # dedup against every finding already on a line (not just the three precise categories):
        # a line the unresolved-receiver fallback above already flagged manual-check-needed needs
        # no second, redundant manual-check-needed finding.
        covered_lines = {ln for ln, _, _ in out}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value == symbol and node.lineno not in covered_lines):
                out.append((node.lineno, "manual-check-needed",
                             f"string literal {symbol!r} in a file already tied to {module_a}; "
                             "could not confirm what it refers to"))
                covered_lines.add(node.lineno)

    return out


_NOISE_DIRS = {".git", ".ratchet", "__pycache__", ".venv", "venv", "node_modules"}


def repoint_scan(fixture_root: str, module_a: str, module_b: str, symbol: str) -> dict:
    """Walks every file under fixture_root looking for re-point sites for `symbol` moving from
    module_a to module_b. Never raises on a single bad file -- a file that cannot be parsed (a
    syntax error) or isn't Python at all is reported as a 'manual-check-needed' finding and the
    walk continues over the rest of the tree, per the module docstring's exit-code contract.

    Skips common noise directories (.git, .ratchet, __pycache__, .venv, venv, node_modules) by
    default -- scanning them can never produce a real re-point site and only floods the plan. A
    non-Python file is reported 'manual-check-needed' only when the symbol name appears in it as a
    whole word; a non-Python file that never mentions the symbol produces no finding at all (it
    used to be flagged unconditionally, which buried real findings under noise on any real repo)."""
    root = Path(fixture_root)
    symbol_word = re.compile(r"\b" + re.escape(symbol) + r"\b")
    findings = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _NOISE_DIRS]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if not name.endswith(".py"):
                try:
                    text = path.read_text(errors="replace")
                except OSError as exc:
                    findings.append({"file": rel, "line": 1, "category": "manual-check-needed",
                                      "detail": f"could not be read: {exc}"})
                    continue
                if symbol_word.search(text):
                    findings.append({"file": rel, "line": 1, "category": "manual-check-needed",
                                      "detail": "non-Python file mentions the symbol by name; "
                                                "not statically scanned"})
                continue
            try:
                source = path.read_text()
            except OSError as exc:
                findings.append({"file": rel, "line": 1, "category": "manual-check-needed",
                                  "detail": f"could not be read: {exc}"})
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                findings.append({"file": rel, "line": max(1, exc.lineno or 1),
                                  "category": "manual-check-needed",
                                  "detail": f"syntax error: {exc.msg}"})
                continue
            for lineno, category, detail in _scan_file_for_repoint(tree, rel, module_a, module_b, symbol):
                findings.append({"file": rel, "line": lineno, "category": category, "detail": detail})
    return {"module_a": module_a, "module_b": module_b, "symbol": symbol, "findings": findings}


def cmd_repoint(argv: list) -> int:
    if not argv or argv[0].startswith("--"):
        _usage_error("repoint requires a <fixture_root> argument")
        return 2  # unreachable; _usage_error exits
    fixture_root, rest = argv[0], argv[1:]
    flags = {"--module-a": None, "--module-b": None, "--symbol": None}
    i = 0
    while i < len(rest):
        flag = rest[i]
        if flag in flags and i + 1 < len(rest):
            flags[flag] = rest[i + 1]
            i += 2
        else:
            _usage_error(f"unrecognized or incomplete argument: {flag}")
            return 2  # unreachable; _usage_error exits
    missing = [k for k, v in flags.items() if not v]
    if missing:
        _usage_error("repoint requires " + ", ".join(missing))
        return 2  # unreachable; _usage_error exits
    result = repoint_scan(fixture_root, flags["--module-a"], flags["--module-b"], flags["--symbol"])
    print(json.dumps(result))
    return 0


# ------------------------------------------------------------------------------------------------
# `registration`: flag a brownfield checkpoint's new test file as needing README/inventory
# registration (CP3).
# ------------------------------------------------------------------------------------------------

def _line_already_present(list_path: str, target_line: str) -> bool:
    """True when some line of the fixture file at `list_path`, stripped of surrounding whitespace,
    is EXACTLY equal to `target_line` -- a substring match (missing the "python3 " prefix, a
    different runner) never counts, per the module docstring."""
    text = Path(list_path).read_text()
    return any(raw.strip() == target_line for raw in text.splitlines())


def _has_text_based_source_check(tree: ast.AST) -> bool:
    """True when the file contains a text-based containment/search check anywhere: an `in`/`not in`
    Compare (e.g. `'old_module' not in source`), a `re.search`/`re.match`/`re.findall`-style call (an
    attribute access named one of those, regardless of receiver -- covers both the `re` module and a
    compiled pattern object), or an `assertIn`/`assertNotIn` call (bare or as an attribute, e.g.
    `self.assertIn(...)`). Deliberately does not try to confirm the checked value is the same object
    the file read from disk (that would be real data-flow analysis, out of scope per the module
    docstring) -- the same best-effort approach `_file_source_scans` already used for the
    `ast.parse` case."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in (
                    "search", "match", "findall", "assertIn", "assertNotIn"):
                return True
            if isinstance(func, ast.Name) and func.id in ("assertIn", "assertNotIn"):
                return True
    return False


_PY_SOURCE_EXTENSION = ".py"
_OBVIOUSLY_DATA_EXTENSIONS = (".json", ".md", ".txt", ".log")


def _collect_read_target_path_vars(tree: ast.AST) -> "tuple[set, set]":
    """First pass over the whole file: a bare-name variable assigned from string-literal path
    components (e.g. `TARGET = HERE / "plain_fixture.py"`, or a single combined literal) is
    classified by its literal path's extension as looking like Python source (`.py`) or an
    obviously-data file (`.json`/`.md`/`.txt`/`.log`), mirroring `_collect_module_a_file_vars`'s
    shallow one-hop tracking. Returns (py_path_vars, data_path_vars)."""
    py_vars, data_vars = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        consts = _string_consts_in_order(node.value)
        if not consts:
            continue
        joined = "/".join(consts)
        if joined.endswith(_PY_SOURCE_EXTENSION):
            bucket = py_vars
        elif joined.endswith(_OBVIOUSLY_DATA_EXTENSIONS):
            bucket = data_vars
        else:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bucket.add(target.id)
    return py_vars, data_vars


def _read_target_looks_like_py(node, py_vars: set, data_vars: set) -> "bool | None":
    """Classifies a read call's target expression per the module docstring's narrowed
    precision/recall tradeoff: True when it looks like Python source (a string-literal path ending
    in `.py`, directly or through one hop of a tracked variable assignment), False when it looks
    like an obviously-data file (`.json`/`.md`/`.txt`/`.log`), None when it can't be confirmed
    either way (an unresolved expression, a computed path, an unrecognized extension)."""
    if isinstance(node, ast.Name):
        if node.id in py_vars:
            return True
        if node.id in data_vars:
            return False
        return None
    consts = _string_consts_in_order(node)
    if not consts:
        return None
    joined = "/".join(consts)
    if joined.endswith(_PY_SOURCE_EXTENSION):
        return True
    if joined.endswith(_OBVIOUSLY_DATA_EXTENSIONS):
        return False
    return None


def _file_source_scans(tree: ast.AST) -> str:
    """Best-effort classification of whether this file reads some other file's text (a
    `.read_text()`/`.read()`/`.read_bytes()` method call, or `open(...)`) and inspects it, returning
    one of:

    - "scan": either an `ast.parse(...)`-style call on any read target (an AST source scan is
      unambiguous evidence of source-level inspection regardless of what it's reading -- unchanged
      by the extension narrowing below), or a text-based check (see `_has_text_based_source_check`)
      paired with a read target that looks like Python source (see `_read_target_looks_like_py`) --
      i.e. this test file itself does a source-inspection scan of another project file.
    - "no-scan": no read of another file's content at all; or a text-based check whose read
      target(s) are confirmed to be an obviously-data extension (`.json`/`.md`/`.txt`/`.log`) and
      none looks like Python source.
    - "ambiguous": reads another file's content but pairs it with no recognized scan/check shape at
      all (neither `ast.parse` nor any text-based check); or a text-based check whose read target(s)
      include at least one genuinely unresolved reference (a computed path, an unrecognized
      extension, a loop variable such as `for path in ROOT.rglob('*.py')`) and none confirmed to
      look like Python source. Per the no-false-precision rule (the same one `repoint` already
      follows), neither shape is ever silently decided as "no-scan" -- the caller emits a
      manual-check-needed item for both.

    Deliberately does not try to confirm the read and the check operate on the very same path (real
    data-flow analysis, out of scope per the module docstring)."""
    has_read = False
    has_ast_scan = False
    any_py_read = False
    any_unresolved_read = False
    py_vars, data_vars = _collect_read_target_path_vars(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "read", "read_bytes"):
            has_read = True
            verdict = _read_target_looks_like_py(func.value, py_vars, data_vars)
            if verdict is True:
                any_py_read = True
            elif verdict is None:
                any_unresolved_read = True
        elif isinstance(func, ast.Name) and func.id == "open" and node.args:
            has_read = True
            verdict = _read_target_looks_like_py(node.args[0], py_vars, data_vars)
            if verdict is True:
                any_py_read = True
            elif verdict is None:
                any_unresolved_read = True
        if isinstance(func, ast.Attribute) and func.attr == "parse":
            has_ast_scan = True
        elif isinstance(func, ast.Name) and func.id == "parse":
            has_ast_scan = True
    if not has_read:
        return "no-scan"
    if has_ast_scan:
        return "scan"
    if _has_text_based_source_check(tree):
        if any_py_read:
            return "scan"
        if any_unresolved_read:
            return "ambiguous"
        return "no-scan"
    return "ambiguous"


def registration_check(repo_root_arg: str, new_test_file_relpath: str, readme_list_path: "str | None",
                        inventory_path: "str | None") -> dict:
    """Decides the README/inventory registration items a new test file needs. Never raises and
    never mutates `readme_list_path`/`inventory_path`; a file that cannot be read or parsed still
    gets its README item (the filename is known regardless of parsing) plus a single
    manual-check-needed item in place of a guess about inventory registration -- never an inventory
    item for a file that could not be parsed. `readme_list_path` and `inventory_path` are each
    independently optional (`None` when the checkpoint has no README-list/inventory fixture at all):
    when `readme_list_path` is `None`, a manual-check-needed item is emitted in place of the README
    item instead of guessing whether it is already registered; the README item is otherwise always
    emitted regardless of parsing. `inventory_path` being `None` behaves the same way for the
    inventory side: if the new test file turns out to scan source anyway with no inventory path
    given, a manual-check-needed item is emitted in place of an inventory item instead of raising on
    a missing path."""
    full_path = Path(repo_root_arg) / new_test_file_relpath

    source = None
    read_error = None
    try:
        source = full_path.read_text()
    except OSError as exc:
        read_error = str(exc)

    tree = None
    parse_error = None
    if source is not None:
        try:
            tree = ast.parse(source, filename=new_test_file_relpath)
        except SyntaxError as exc:
            parse_error = f"syntax error: {exc.msg}"
    else:
        parse_error = read_error or "could not be read"

    items = []

    if readme_list_path is None:
        items.append({
            "kind": "manual-check-needed",
            "detail": f"{new_test_file_relpath} needs to be registered in the README test list, "
                      "but no README-list fixture path was given; cannot determine whether it is "
                      "already registered",
        })
    else:
        readme_line = f"python3 {new_test_file_relpath}"
        if not _line_already_present(readme_list_path, readme_line):
            items.append({"kind": "readme", "line": readme_line})

    if tree is not None:
        scan_verdict = _file_source_scans(tree)
        if scan_verdict == "scan":
            if inventory_path is None:
                items.append({
                    "kind": "manual-check-needed",
                    "detail": f"{new_test_file_relpath} scans another file's source, but no "
                              "inventory fixture path was given; cannot determine whether it is "
                              "already registered in the test-scan inventory",
                })
            else:
                basename = Path(new_test_file_relpath).name
                if not _line_already_present(inventory_path, basename):
                    items.append({"kind": "inventory", "line": basename})
        elif scan_verdict == "ambiguous":
            items.append({
                "kind": "manual-check-needed",
                "detail": f"{new_test_file_relpath} reads another file's content but its check "
                          "shape could not be confidently classified as a source scan or not; "
                          "cannot determine whether it needs inventory registration",
            })
    else:
        items.append({
            "kind": "manual-check-needed",
            "detail": f"{new_test_file_relpath} could not be parsed ({parse_error}); cannot "
                       "determine whether it scans another file's source, so inventory "
                       "registration cannot be decided automatically",
        })

    return {"test_file": new_test_file_relpath, "items": items}


def cmd_registration(argv: list) -> int:
    if len(argv) < 2 or argv[0].startswith("--") or argv[1].startswith("--"):
        _usage_error("registration requires <repo_root> and <new_test_file_relpath> arguments")
        return 2  # unreachable; _usage_error exits
    repo_root_arg, new_test_file_relpath, rest = argv[0], argv[1], argv[2:]
    flags = {"--readme-list": None, "--inventory": None}
    i = 0
    while i < len(rest):
        flag = rest[i]
        if flag in flags and i + 1 < len(rest):
            flags[flag] = rest[i + 1]
            i += 2
        else:
            _usage_error(f"unrecognized or incomplete argument: {flag}")
            return 2  # unreachable; _usage_error exits
    missing = [k for k, v in flags.items() if not v]
    if missing:
        _usage_error("registration requires " + ", ".join(missing))
        return 2  # unreachable; _usage_error exits
    result = registration_check(repo_root_arg, new_test_file_relpath, flags["--readme-list"],
                                 flags["--inventory"])
    print(json.dumps(result))
    return 0


# ------------------------------------------------------------------------------------------------
# `parity`: attach standing startup-parity/behavior-parity gate items to an extraction checkpoint
# (CP4).
# ------------------------------------------------------------------------------------------------

_DEFAULT_STARTUP_THRESHOLD_PCT = 10


def _is_extraction_move(move: "dict | None") -> bool:
    """True only when `move` names a symbol leaving one module for a DIFFERENT one -- a same-module
    rename (module_from == module_to) or a null move (no move at all, e.g. an in-place edit or a
    greenfield-create checkpoint) is never an extraction."""
    if not move:
        return False
    module_from = move.get("module_from") or ""
    module_to = move.get("module_to") or ""
    return bool(module_from) and bool(module_to) and module_from != module_to


def _startup_threshold_pct(baseline: "dict | None") -> int:
    """The stated percent threshold the emitted startup-parity item's line names: the baseline's own
    `threshold_pct` when a baseline was supplied, else this module's fixed default -- the pass never
    silently omits a number."""
    if isinstance(baseline, dict) and "threshold_pct" in baseline:
        try:
            return int(baseline["threshold_pct"])
        except (TypeError, ValueError):
            return _DEFAULT_STARTUP_THRESHOLD_PCT
    return _DEFAULT_STARTUP_THRESHOLD_PCT


def _done_when_already_asks_for(done_when: list, required_substrings: tuple) -> bool:
    """True when some done_when line, case-insensitively, contains every substring in
    `required_substrings` -- the shared rule both parity items' `already_present` flags use."""
    for line in done_when or []:
        lowered = str(line).lower()
        if all(sub in lowered for sub in required_substrings):
            return True
    return False


def parity_check(move: "dict | None", done_when: "list | None", baseline: "dict | None") -> dict:
    """Decides whether a checkpoint description is an extraction and, if so, the standing
    startup-parity and behavior-parity items it must always carry -- attached fresh on every run,
    never duplicated when done_when already asks for one and never dropped when it doesn't. Never
    raises; every input shape produces a verdict dict."""
    done_when = list(done_when or [])
    if not _is_extraction_move(move):
        return {"is_extraction": False, "items": []}

    module_from = move.get("module_from") or ""
    module_to = move.get("module_to") or ""
    symbol = move.get("symbol") or "the moved symbol"
    threshold_pct = _startup_threshold_pct(baseline)

    startup_line = (
        f"Startup-parity: importing {module_to} costs within {threshold_pct}% of the baseline "
        f"import/startup time previously recorded for {module_from}."
    )
    behavior_line = (
        f"Behavior-parity: {symbol}'s behavior after moving from {module_from} to {module_to} is "
        f"verified byte-identical across the existing test inputs."
    )

    items = [
        {
            "kind": "startup-parity",
            "line": startup_line,
            "already_present": _done_when_already_asks_for(done_when, ("startup", "parity")),
        },
        {
            "kind": "behavior-parity",
            "line": behavior_line,
            "already_present": _done_when_already_asks_for(
                done_when, ("byte-identical", "parity")
            ) or _done_when_already_asks_for(done_when, ("byte-identical", "behavior")),
        },
    ]
    return {"is_extraction": True, "items": items}


def cmd_parity(argv: list) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        _usage_error(f"parity requires valid JSON on stdin: {exc}")
        return 2  # unreachable; _usage_error exits
    if not isinstance(data, dict):
        data = {}
    result = parity_check(data.get("move"), data.get("done_when"), data.get("baseline"))
    print(json.dumps(result))
    return 0


# ------------------------------------------------------------------------------------------------
# `hazard`: flag a planned tests.command that would combine two or more report-writing suites in
# one invocation, risking a shared RATCHET_TEST_REPORT file race (CP4).
# ------------------------------------------------------------------------------------------------

_HAZARD_SPLIT_PATTERN = re.compile(r"&&|\|\||;")
# Bare python/python3, a versioned form (python3.12), or a path-prefixed form (/usr/bin/python3,
# ./venv/bin/python). Anchored at both ends so e.g. "python-config" is correctly excluded.
_HAZARD_INTERPRETER_PATTERN = re.compile(r"(?:.*/)?python(?:\d+(?:\.\d+)*)?$")

# Flags that consume the NEXT token as their own argument (not a script path).
_HAZARD_ARG_TAKING_FLAGS = {"-W", "-X"}
# Flags that mean this invocation launches a module or inline code, not a .py file: no script path
# follows at all, so the scan for this segment ends here with no suite found.
_HAZARD_NO_SCRIPT_FLAGS = {"-m", "-c"}


def _tokenize_segment(segment: str) -> list:
    """Tokenizes one shell segment with `shlex.split` (so a quoted argument stays one token);
    falls back to a plain whitespace split on a `ValueError` (unbalanced quotes, etc.) so this
    helper never raises regardless of input, per the module docstring's never-raises contract."""
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _hazard_suites_in_segment(segment: str) -> list:
    """Token-aware scan of one shell segment for `python`/`python3`/`python3.12`/path-prefixed
    suite invocations. After an interpreter token, each remaining token is classified by a small
    flag table (see `_HAZARD_ARG_TAKING_FLAGS`/`_HAZARD_NO_SCRIPT_FLAGS`): `-W`/`-X` each consume
    the next token as their own argument and are skipped along with it; `-m`/`-c` end the scan for
    this segment with no suite found; every other dash-prefixed token is a bare flag with no
    argument and is skipped on its own. The first non-dash token reached after all flags are
    consumed is the candidate script path, counted as a suite only when it ends in `.py`."""
    tokens = _tokenize_segment(segment)
    suites = []
    i = 0
    while i < len(tokens):
        if _HAZARD_INTERPRETER_PATTERN.match(tokens[i]):
            j = i + 1
            script = None
            while j < len(tokens):
                tok = tokens[j]
                if tok in _HAZARD_ARG_TAKING_FLAGS:
                    j += 2
                    continue
                if tok in _HAZARD_NO_SCRIPT_FLAGS:
                    j = len(tokens)
                    break
                if tok.startswith("-"):
                    j += 1
                    continue
                script = tok
                j += 1
                break
            if script is not None and script.endswith(".py"):
                suites.append(script)
            i = j
        else:
            i += 1
    return suites


def hazard_check(tests_command: "str | None") -> dict:
    """Pure text-pattern count of `python3`/`python <path>.py` suite invocations in
    `tests_command`, after splitting it on the shell list separators `&&`/`;`/`||`. Never executes
    the command, never touches the filesystem, and never raises -- an empty or suite-less string
    simply yields zero suites. `hazard` is true exactly when two or more suite invocations are
    found, including a lone fixed multi-suite runner (e.g. tests/run_all.py) paired with one more
    suite beside it; a single invocation (one suite alone, or the runner alone) is never a hazard."""
    tests_command = tests_command or ""
    suites = []
    for segment in _HAZARD_SPLIT_PATTERN.split(tests_command):
        suites.extend(_hazard_suites_in_segment(segment))
    hazard = len(suites) >= 2
    detail = (
        "This tests.command invokes more than one report-writing suite in a single run, risking a "
        "shared RATCHET_TEST_REPORT file race: " + ", ".join(suites)
    ) if hazard else ""
    return {"hazard": hazard, "suites": suites, "detail": detail}


def cmd_hazard(argv: list) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        _usage_error(f"hazard requires valid JSON on stdin: {exc}")
        return 2  # unreachable; _usage_error exits
    if not isinstance(data, dict) or "tests_command" not in data:
        _usage_error("hazard requires stdin JSON with a 'tests_command' key")
        return 2  # unreachable; _usage_error exits
    result = hazard_check(data.get("tests_command"))
    print(json.dumps(result))
    return 0


# ------------------------------------------------------------------------------------------------
# `merge`: fold CP2-CP4's enumerated findings into an already-approved checkpoint's done_when/cases,
# guaranteed to only ever add -- never drop, reword or reorder anything already approved (CP5).
# ------------------------------------------------------------------------------------------------

def _find_done_when_divergence(existing_done_when: list, override: list):
    """Compares `override` against the real `existing_done_when` element-by-element and returns the
    first (index, expected, actual) triple where they diverge, or None when every element at every
    index (up to the longer list's length) matches exactly. A single uniform comparison -- not a
    per-corruption-type special case -- is what catches a reworded line, a dropped line and two
    swapped lines alike: all three make the lists stop matching at some position."""
    length = max(len(existing_done_when), len(override))
    for i in range(length):
        expected = existing_done_when[i] if i < len(existing_done_when) else None
        actual = override[i] if i < len(override) else None
        if expected != actual:
            return i, expected, actual
    return None


def _shape_error_for_done_when_list(value, owner: str) -> "str | None":
    """None when `value` is a valid done_when list (every element a string); else a detail string
    naming exactly what was wrong -- never used to coerce, only to decide whether to refuse."""
    if not isinstance(value, list):
        return f"{owner}.done_when must be a list of strings, got {type(value).__name__}"
    for i, item in enumerate(value):
        if not isinstance(item, str):
            return f"{owner}.done_when[{i}] must be a string, got {type(item).__name__}"
    return None


def _shape_error_for_cases_list(value, owner: str) -> "str | None":
    """None when `value` is a valid cases list (every element a dict carrying a non-empty string
    'id'); else a detail string naming exactly what was wrong. Requiring 'id' up front here is what
    lets a case dict with no id at all be reported as a shape violation, never misreported later as
    colliding with some unrelated existing case. Used for `checkpoint.cases` only: existing,
    already-approved cases pass through unchanged regardless of which other fields they carry, so
    this stays deliberately lenient beyond the 'id' check."""
    if not isinstance(value, list):
        return f"{owner}.cases must be a list of case objects, got {type(value).__name__}"
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            return f"{owner}.cases[{i}] must be an object, got {type(item).__name__}"
        case_id = item.get("id")
        if not isinstance(case_id, str) or not case_id:
            return f"{owner}.cases[{i}] must have a non-empty string 'id' field"
    return None


_KNOWN_CASE_KEYS = frozenset({"id", "title", "given", "when", "then", "layer", "edge", "proves"})
_REQUIRED_ENUMERATED_CASE_STRING_FIELDS = ("id", "given", "when", "then")


def _shape_error_for_enumerated_case(item, index: int) -> "str | None":
    """None when `item` (one element of `enumerated_items.cases`) is a dict using only the known
    case keys and carrying non-empty, non-whitespace string values for 'id', 'given', 'when' and
    'then'; else a detail string naming exactly what was wrong. Unlike `checkpoint.cases` (existing,
    already-approved content that must pass through untouched no matter what it carries), a NEWLY
    enumerated case is new content a builder must be able to act on immediately, so it is validated
    strictly up front rather than merged with holes a builder can't implement. The required-field
    check runs before the unexpected-key check, so a case missing 'id' entirely is always reported
    as missing 'id' (never as merely having an unexpected key), matching the existing 'id'-first
    reporting a case with no id at all already got before this closed schema was added."""
    if not isinstance(item, dict):
        return f"enumerated_items.cases[{index}] must be an object, got {type(item).__name__}"
    for field in _REQUIRED_ENUMERATED_CASE_STRING_FIELDS:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"enumerated_items.cases[{index}] must have a non-empty string '{field}' field"
    unexpected = sorted(set(item) - _KNOWN_CASE_KEYS)
    if unexpected:
        return (
            f"enumerated_items.cases[{index}] has unexpected key(s) {unexpected}; expected only "
            f"{sorted(_KNOWN_CASE_KEYS)}"
        )
    return None


def _shape_error_for_enumerated_cases_list(value) -> "str | None":
    """None when `value` is a valid `enumerated_items.cases` list (see
    `_shape_error_for_enumerated_case`); else a detail string naming the first violation found."""
    if not isinstance(value, list):
        return f"enumerated_items.cases must be a list of case objects, got {type(value).__name__}"
    for i, item in enumerate(value):
        err = _shape_error_for_enumerated_case(item, i)
        if err:
            return err
    return None


def _shape_error_for_override(value) -> "str | None":
    """None when `value` is None (absent) or a valid list of strings; else a detail string. A plain
    string is explicitly rejected here (not accepted as a list), since comparing it element-by-element
    against checkpoint.done_when would otherwise compare characters, not lines, and could pass by
    coincidence."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        return (
            "enumerated_items.existing_done_when_override must be null or a list of strings, got "
            f"{type(value).__name__}"
        )
    return None


_ENUMERATED_ITEMS_ALLOWED_KEYS = frozenset({"done_when", "cases", "existing_done_when_override"})


def _validate_merge_shapes(checkpoint, enumerated_items) -> "str | None":
    """Strict shape validation for `merge`'s whole input, run BEFORE any merging logic (including
    the existing_done_when_override divergence check). Returns None when every shape is valid, else
    a detail string naming the first violation found, in a fixed checking order so the message is
    stable. Never coerces anything -- a bare `list(...)` on a string or dict would silently shred a
    one-line done_when into characters, or a cases dict into its own key names, instead of being
    caught and refused."""
    if not isinstance(checkpoint, dict):
        return f"checkpoint must be a JSON object, got {type(checkpoint).__name__}"
    if "done_when" not in checkpoint:
        return "checkpoint.done_when is required (a list of strings) and was missing entirely"
    if "cases" not in checkpoint:
        return "checkpoint.cases is required (a list of case objects) and was missing entirely"
    err = _shape_error_for_done_when_list(checkpoint["done_when"], "checkpoint")
    if err:
        return err
    err = _shape_error_for_cases_list(checkpoint["cases"], "checkpoint")
    if err:
        return err

    if not isinstance(enumerated_items, dict):
        return f"enumerated_items must be a JSON object, got {type(enumerated_items).__name__}"
    unexpected = sorted(set(enumerated_items) - _ENUMERATED_ITEMS_ALLOWED_KEYS)
    if unexpected:
        # A closed schema, not an open one: an unrecognized key (e.g. a typo like "done-when" or a
        # stale field name like "test_cases") must never be read as "nothing to add" and silently
        # dropped -- it is refused instead, so the re-point/registration/parity items the planner
        # meant to add can never disappear without any signal.
        return (
            f"enumerated_items has unexpected key(s) {unexpected}; expected only "
            f"{sorted(_ENUMERATED_ITEMS_ALLOWED_KEYS)}"
        )
    if enumerated_items.get("done_when") is not None:
        err = _shape_error_for_done_when_list(enumerated_items["done_when"], "enumerated_items")
        if err:
            return err
    if enumerated_items.get("cases") is not None:
        err = _shape_error_for_enumerated_cases_list(enumerated_items["cases"])
        if err:
            return err
    return _shape_error_for_override(enumerated_items.get("existing_done_when_override"))


def _refusal(reason: str, detail: str) -> dict:
    """The single shared builder for every `merge` refusal shape, so all three reasons
    (invalid_shape, done_when_override_mismatch, case_id_collision) produce exactly the same JSON
    structure by construction, rather than three separately hand-built dicts that could drift."""
    return {
        "ok": False,
        "refused": {"refused": True, "reason": reason, "detail": detail},
    }


def merge_checkpoint(checkpoint: "dict | None", enumerated_items: "dict | None") -> dict:
    """Merges `enumerated_items` into `checkpoint`, guaranteed additive-only. Returns either
    `{"ok": True, "merged": {"done_when": [...], "cases": [...]}}` or
    `{"ok": False, "refused": {"refused": True, "reason": ..., "detail": ...}}`. Never raises for
    any input -- a malformed shape is refused (reason "invalid_shape") by strict validation before
    any merging logic runs, rather than crashing with a bare traceback or silently coercing it (a
    bare `list(...)` on a string/dict would shred an unwrapped done_when/cases field). Never
    partially applies a payload that is only partly safe -- a refusal anywhere in the computation
    discards the whole result, including any genuinely new, non-colliding item the same payload
    also carried.

    Checking order: (1) strict shape validation of checkpoint/enumerated_items/override, (2) the
    existing_done_when_override divergence guard, when present, (3) append new done_when lines
    (skipping verbatim duplicates), (4) append new cases (skipping verbatim duplicates, refusing on
    any id collision whose content differs)."""
    shape_error = _validate_merge_shapes(checkpoint, enumerated_items)
    if shape_error is not None:
        return _refusal("invalid_shape", shape_error)

    existing_done_when = list(checkpoint["done_when"])
    existing_cases = list(checkpoint["cases"])

    override = enumerated_items.get("existing_done_when_override")
    if override is not None:
        divergence = _find_done_when_divergence(existing_done_when, override)
        if divergence is not None:
            index, expected, actual = divergence
            return _refusal(
                "done_when_override_mismatch",
                f"existing_done_when_override diverges from checkpoint.done_when at index "
                f"{index}: expected {expected!r}, override had {actual!r}",
            )

    merged_done_when = list(existing_done_when)
    for line in enumerated_items.get("done_when") or []:
        if line not in merged_done_when:
            merged_done_when.append(line)

    # ids already in checkpoint.cases, captured BEFORE this call appends anything, so a collision's
    # refusal message can tell "already existed" apart from "duplicated within this same call".
    existing_case_ids = {c["id"] for c in existing_cases}

    merged_cases = list(existing_cases)
    for case in enumerated_items.get("cases") or []:
        case_id = case["id"]  # presence/non-empty/str already guaranteed by shape validation above
        collision = next((c for c in merged_cases if c.get("id") == case_id), None)
        if collision is None:
            merged_cases.append(case)
            continue
        if collision == case:
            continue  # verbatim duplicate -- already present, never appended a second time
        if case_id in existing_case_ids:
            detail = (
                f"case id {case_id!r} already exists in checkpoint.cases with different "
                "content; refusing rather than overwriting or renumbering it"
            )
        else:
            detail = (
                f"case id {case_id!r} appears more than once in enumerated_items.cases with "
                "different content (duplicate id within enumerated_items, not an existing "
                "checkpoint.cases entry); refusing rather than silently keeping only the first one"
            )
        return _refusal("case_id_collision", detail)

    return {"ok": True, "merged": {"done_when": merged_done_when, "cases": merged_cases}}


def cmd_merge(argv: list) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        _usage_error(f"merge requires valid JSON on stdin: {exc}")
        return 2  # unreachable; _usage_error exits
    if not isinstance(data, dict) or "checkpoint" not in data or "enumerated_items" not in data:
        _usage_error("merge requires stdin JSON with 'checkpoint' and 'enumerated_items' keys")
        return 2  # unreachable; _usage_error exits
    result = merge_checkpoint(data.get("checkpoint"), data.get("enumerated_items"))
    if not result["ok"]:
        print(json.dumps(result["refused"]), file=sys.stderr)
        return 3
    print(json.dumps({"merged": result["merged"]}))
    return 0


# ------------------------------------------------------------------------------------------------
# `full`: a composed end-to-end subcommand that runs classify/repoint/registration/parity/hazard/
# merge in one call for the common case -- one `move`, one `new_test_file` (CP7).
# ------------------------------------------------------------------------------------------------

_FULL_REQUIRED_CHECKPOINT_KEYS = ("files_likely", "done_when", "cases")


def _repoint_line(finding: dict) -> str:
    return f"Re-point {finding['file']}:{finding['line']} ({finding['category']}): {finding['detail']}"


def _registration_line(item: dict) -> "str | None":
    kind = item.get("kind")
    if kind == "readme":
        return f"Register in the README test list: {item['line']}"
    if kind == "inventory":
        return f"Register in the test-scan inventory: {item['line']}"
    if kind == "manual-check-needed":
        return f"Manual check needed (registration): {item['detail']}"
    return None


def _hazard_line(hazard_result: dict) -> "str | None":
    if not hazard_result.get("hazard"):
        return None
    return f"Report-race hazard: {hazard_result['detail']}"


def full_scan(fixture_root: str, checkpoint: dict) -> dict:
    """Runs classify -> repoint (skipped if greenfield or move is null) -> registration (skipped
    unless new_test_file is given and not greenfield; readme_list and inventory are each
    independently optional -- when either is absent, registration_check still runs and substitutes a
    manual-check-needed item in its place instead of silently skipping that side, and still emits
    the other side normally) -> parity (always) -> hazard (skipped if no tests.command) ->
    builds enumerated done_when lines from the findings above -> calls `merge_checkpoint`. Returns a
    dict carrying every intermediate result plus the raw `merge_checkpoint` result (under
    "merge_result") so the caller can decide how to report a refusal and whether/what to write back
    to disk. Never raises for a well-formed checkpoint dict (one already confirmed, by the caller, to
    carry files_likely/done_when/cases)."""
    files_likely = checkpoint.get("files_likely") or []
    done_when = checkpoint.get("done_when") or []
    cases = checkpoint.get("cases") or []
    move = checkpoint.get("move")
    baseline = checkpoint.get("baseline")
    tests = checkpoint.get("tests")
    tests_command = tests.get("command") if isinstance(tests, dict) else None
    readme_list = checkpoint.get("readme_list")
    inventory = checkpoint.get("inventory")
    new_test_file = checkpoint.get("new_test_file")

    classify_result = classify(fixture_root, files_likely)
    mode = classify_result["mode"]
    reason = classify_result["reason"]
    is_greenfield = mode == "greenfield-create"

    repoint_result = None
    if not is_greenfield and move:
        repoint_result = repoint_scan(
            fixture_root, move.get("module_from"), move.get("module_to"), move.get("symbol")
        )

    registration_result = None
    if not is_greenfield and new_test_file:
        readme_list_full_path = str(Path(fixture_root) / readme_list) if readme_list else None
        inventory_full_path = str(Path(fixture_root) / inventory) if inventory else None
        registration_result = registration_check(
            fixture_root,
            new_test_file,
            readme_list_full_path,
            inventory_full_path,
        )

    parity_result = parity_check(move, done_when, baseline)

    if tests_command:
        hazard_result = hazard_check(tests_command)
    else:
        hazard_result = {"hazard": False, "suites": [], "detail": ""}

    enumerated_done_when = []
    if repoint_result is not None:
        for finding in repoint_result.get("findings", []):
            enumerated_done_when.append(_repoint_line(finding))
    if registration_result is not None:
        for item in registration_result.get("items", []):
            line = _registration_line(item)
            if line is not None:
                enumerated_done_when.append(line)
    for item in parity_result.get("items", []):
        if not item.get("already_present"):
            enumerated_done_when.append(item["line"])
    hazard_line = _hazard_line(hazard_result)
    if hazard_line is not None:
        enumerated_done_when.append(hazard_line)

    enumerated_items = {"done_when": enumerated_done_when, "cases": []}
    merge_result = merge_checkpoint({"done_when": done_when, "cases": cases}, enumerated_items)

    return {
        "mode": mode,
        "reason": reason,
        "repoint": repoint_result,
        "registration": registration_result,
        "parity": parity_result,
        "hazard": hazard_result,
        "merge_result": merge_result,
    }


def cmd_full(argv: list) -> int:
    if not argv or argv[0].startswith("--"):
        _usage_error("full requires a <fixture_root> argument")
        return 2  # unreachable; _usage_error exits
    fixture_root, rest = argv[0], argv[1:]

    checkpoint_path = None
    i = 0
    while i < len(rest):
        flag = rest[i]
        if flag == "--checkpoint" and i + 1 < len(rest):
            checkpoint_path = rest[i + 1]
            i += 2
        else:
            _usage_error(f"unrecognized or incomplete argument: {flag}")
            return 2  # unreachable; _usage_error exits
    if not checkpoint_path:
        _usage_error("full requires --checkpoint <checkpoint_json_path>")
        return 2  # unreachable; _usage_error exits

    checkpoint_file = Path(checkpoint_path)
    try:
        raw = checkpoint_file.read_text()
    except OSError as exc:
        _usage_error(f"--checkpoint file '{checkpoint_path}' could not be read: {exc}")
        return 2  # unreachable; _usage_error exits
    try:
        checkpoint_data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _usage_error(f"--checkpoint file '{checkpoint_path}' is not valid JSON: {exc}")
        return 2  # unreachable; _usage_error exits
    if not isinstance(checkpoint_data, dict):
        _usage_error(f"--checkpoint file '{checkpoint_path}' must contain a JSON object")
        return 2  # unreachable; _usage_error exits
    missing = [k for k in _FULL_REQUIRED_CHECKPOINT_KEYS if k not in checkpoint_data]
    if missing:
        _usage_error(
            f"--checkpoint file '{checkpoint_path}' is missing required key(s): " + ", ".join(missing)
        )
        return 2  # unreachable; _usage_error exits

    result = full_scan(fixture_root, checkpoint_data)
    merge_result = result["merge_result"]
    if not merge_result["ok"]:
        print(json.dumps(merge_result["refused"]), file=sys.stderr)
        return 3

    merged = merge_result["merged"]
    checkpoint_data["done_when"] = merged["done_when"]
    checkpoint_data["cases"] = merged["cases"]
    checkpoint_file.write_text(json.dumps(checkpoint_data, indent=2) + "\n")

    output = {
        "mode": result["mode"],
        "reason": result["reason"],
        "repoint": result["repoint"],
        "registration": result["registration"],
        "parity": result["parity"],
        "hazard": result["hazard"],
        "merged": merged,
    }
    print(json.dumps(output))
    return 0


SUBCOMMANDS = {
    "classify": cmd_classify,
    "repoint": cmd_repoint,
    "registration": cmd_registration,
    "parity": cmd_parity,
    "hazard": cmd_hazard,
    "merge": cmd_merge,
    "full": cmd_full,
}


def main(argv: list) -> int:
    if not argv:
        _usage_error("missing subcommand; expected one of: " + ", ".join(sorted(SUBCOMMANDS)))
        return 2  # unreachable; _usage_error exits
    subcommand, rest = argv[0], argv[1:]
    handler = SUBCOMMANDS.get(subcommand)
    if handler is None:
        _usage_error(f"unknown subcommand '{subcommand}'; expected one of: " + ", ".join(sorted(SUBCOMMANDS)))
        return 2  # unreachable; _usage_error exits
    return handler(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
