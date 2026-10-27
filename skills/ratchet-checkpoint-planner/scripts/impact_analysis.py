#!/usr/bin/env python3
"""
impact_analysis.py -- static planning-time helper for the ratchet-checkpoint-planner agent.

Called as a plain OS subprocess (never imported in-process) by the planner agent via the Bash tool
while it is writing a run's checkpoints.json, so it can tell a greenfield checkpoint (only new files)
apart from a brownfield one (touches something that already exists) and, for brownfield/ambiguous
checkpoints, enumerate the predictable re-point/registration/parity consequences of the change up
front. This file is a small subcommand dispatcher; CP1 implements only the `classify` subcommand.
Later checkpoints (CP2-CP7) add `repoint`, `registration`, `parity`, `merge` and an end-to-end
subcommand that composes them -- this module stays stdlib-only (standards.md #1) and out of
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
"""
from __future__ import annotations

import ast
import json
import os
import re
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


SUBCOMMANDS = {
    "classify": cmd_classify,
    "repoint": cmd_repoint,
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
