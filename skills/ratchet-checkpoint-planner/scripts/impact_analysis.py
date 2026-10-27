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

import json
import os
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


SUBCOMMANDS = {
    "classify": cmd_classify,
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
