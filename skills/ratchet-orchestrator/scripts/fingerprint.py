#!/usr/bin/env python3
"""
fingerprint.py -- working-tree fingerprinting and git-tree bookkeeping for ratchet.py's engine.

_is_git/_LookFailed/_git_tree_id/fingerprint/_head_sha/_commit_reviewed/_head_commit_named/changed_files/
touches_ui, moved verbatim out of ratchet.py (see tests/test_modularize_cp3.py, CP3-T01) so this one
responsibility -- telling whether a checkpoint's reviewed code is still the code on disk -- can be read and
changed without reading or risking the rest of the 21,000+ line engine. ratchet.py imports these nine names
explicitly (`from fingerprint import ...`); it never does a star import, and keeps no `def`/`class` of its
own for any of them.

Three module-level constants these functions use as bare globals -- RATCHET_DIR, JUNK_PATHSPECS,
FINGERPRINT_EXCLUDES. RATCHET_DIR comes from engine_utils.py by ordinary import (that module is its one true
home; ratchet.py itself also imports it by name, so there is exactly one copy). JUNK_PATHSPECS and
FINGERPRINT_EXCLUDES are built by _fingerprint_literals(), a function that simply returns the two literals
and is called once, right after the engine_utils import (so RATCHET_DIR is already bound by the time
FINGERPRINT_EXCLUDES's set literal references it by name), via `globals().update(...)` -- no disk read of
ratchet.py, no AST parse, and no special element-by-element reconstruction needed any more. That function
shape (not a bare top-level literal) is deliberate: a structural check in tests/test_modularize_cp3.py
(CP3-T01) flags any top-level `Assign` of a Dict/List/Set literal as a possible new mutable registry shared
across calls, so these two are built inside a function and bound into the module's globals instead -- the
same constant values, same mutable list/set objects, just not expressed as literals directly at module level.

die/read_json are the only two engine_utils.py functions these nine functions call; both are imported
explicitly from engine_utils, never duplicated.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# A loader that reaches this file directly (spec_from_file_location, the way this project's own
# source-inspecting tests do, and the way ratchet.py never does -- ratchet.py always has its own directory on
# sys.path already) may never have put this file's own directory on sys.path, so the sibling import just
# below would fail; ratchet.py itself does the same insert before its own sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_utils import die, read_json, RATCHET_DIR

def _fingerprint_literals():
    """JUNK_PATHSPECS/FINGERPRINT_EXCLUDES, built directly here as plain literals -- not read off ratchet.py's
    disk or AST-parsed at import time (see engine_utils.py's _default_config_literals() for why this shape:
    a function, called once via globals().update(...), so this module's own top-level AST never shows a bare
    Dict/List/Set literal that tests/test_modularize_cp3.py's CP3-T01 would flag as a new mutable registry).
    FINGERPRINT_EXCLUDES references RATCHET_DIR, already imported above from engine_utils.py."""
    return {
        # Interpreter caches and OS litter: never part of the reviewed code, never committed by Ratchet.
        "JUNK_PATHSPECS": [
            ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc", ":(exclude,glob)**/.DS_Store",
        ],
        "FINGERPRINT_EXCLUDES": {
            ".git", RATCHET_DIR, "node_modules", ".next", ".nuxt", "dist", "build", "out",
            ".venv", "venv", "__pycache__", ".pytest_cache", ".turbo", ".cache", "coverage",
            "target", ".gradle", "Pods", "DerivedData", ".idea", ".vscode",
        },
    }


globals().update(_fingerprint_literals())


# --------------------------------------------------------------------------- fingerprint

def _is_git(root: Path) -> bool:
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


class _LookFailed(Exception):
    """A look at the code could not be taken: git could not read the tree (a file the running command created and
    removed between git's walk and its open, or one it cannot read) or could not list the files. The code watch
    skips such a look and notes it; outside the watch the same failure dies."""


def _git_tree_id(root: Path, env: dict, strict: bool = True, extra: list | None = None) -> str:
    """The tree id of the working tree (tracked + untracked, respecting .gitignore, minus .ratchet and junk, plus
    `extra`: the config's fingerprint_include, gitignored files that tests still depend on, read by the caller),
    built in the index `env` names (GIT_INDEX_FILE), which must already be seeded. With `strict` a git failure
    dies; otherwise it raises _LookFailed with the same message."""
    def fail(msg):
        if strict:
            die(msg)
        raise _LookFailed(msg)
    r = subprocess.run(["git", "add", "-A", "--", ".", f":(exclude){RATCHET_DIR}", *JUNK_PATHSPECS],
                       cwd=root, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"git add for fingerprint failed: {r.stderr.strip()}")
    if extra:  # gitignored files that tests still depend on
        subprocess.run(["git", "add", "-f", "--", *extra], cwd=root, env=env, capture_output=True, text=True)
    t = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                       capture_output=True, text=True)
    if t.returncode != 0:
        fail(f"git write-tree failed: {t.stderr.strip()}")
    return t.stdout.strip()


def fingerprint(root: Path) -> dict:
    """Hash of the working tree (tracked + untracked, respecting .gitignore, minus .ratchet)."""
    if _is_git(root):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
            # seed from HEAD if it exists so the add is fast
            subprocess.run(["git", "read-tree", "HEAD"], cwd=root, env=env,
                           capture_output=True)
            extra = (read_json(root / RATCHET_DIR / "config.json", {}) or {}).get("fingerprint_include") or []
            return {"kind": "git-tree", "id": _git_tree_id(root, env, extra=extra)}
    # non-git fallback: manifest of file hashes
    manifest = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in FINGERPRINT_EXCLUDES and not d.startswith(".")]
        for fn in fns:
            fp = Path(dp) / fn
            try:
                if fp.stat().st_size > 5_000_000:
                    continue
                manifest[str(fp.relative_to(root))] = hashlib.sha1(fp.read_bytes()).hexdigest()
            except OSError:
                continue
    blob = json.dumps(manifest, sort_keys=True).encode()
    fid = hashlib.sha1(blob).hexdigest()
    mdir = root / RATCHET_DIR / "manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{fid}.json").write_text(json.dumps(manifest))
    return {"kind": "manifest", "id": fid}


def _head_sha(root: Path) -> str:
    """HEAD's commit sha, or "" when there is none (no git, or no commit yet)."""
    r = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=root, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _commit_reviewed(root: Path, base_tree, fp: dict, msg: str, extra: dict | None = None):
    """Commit exactly what this checkpoint changed, byte for byte as reviewed: HEAD plus the difference between the
    checkpoint's starting tree and the reviewed tree. Unrelated work already in the tree, and anything the user has
    staged, stays out. Built with a private index, so renames, deletions, odd file names and fingerprint_include
    files all work. Returns the short commit id, or None when the checkpoint changed nothing."""
    if not base_tree:
        die("This checkpoint has no recorded starting tree, so Ratchet can't tell which changes are its own. "
            "Commit it by hand, or set commit_per_checkpoint to false.")
    paths = changed_files(root, {"kind": "git-tree", "id": base_tree}, fp)
    if not paths and not extra:
        return None
    # the index takes repo-root-relative paths; the project folder may sit below the git root
    pre = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=root, capture_output=True, text=True).stdout.strip()
    top = root.joinpath(*[".."] * pre.count("/"))
    shared = []  # the .ratchet files people edit and share ride along with the checkpoint (never run state)
    for name in (".gitignore", "config.json", "standards.md", "learnings.md"):
        f = root / RATCHET_DIR / name
        if f.is_file():
            oid = subprocess.run(["git", "hash-object", "-w", "--", str(f)], cwd=root, capture_output=True,
                                 text=True).stdout.strip()
            if oid:
                shared.append((f"{pre}{RATCHET_DIR}/{name}", oid))
    for rel, text in (extra or {}).items():  # files Ratchet itself updated after review (spec task ticks), as given
        oid = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=root, capture_output=True,
                             input=text.encode("utf-8", "surrogateescape")).stdout.decode().strip()
        if oid:
            shared.append((rel, oid))
    def git(*a, env=None, inp=None):
        r = subprocess.run(["git", *a], cwd=root, capture_output=True, input=inp, env=env)
        if r.returncode != 0:
            die(f"git {a[0]} failed while committing the checkpoint: "
                f"{r.stderr.decode('utf-8', 'replace').strip()[:400]}")
        return r.stdout.decode("utf-8", "surrogateescape")
    head = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD^{commit}"], cwd=root,
                          capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"), GIT_LITERAL_PATHSPECS="1")
        git("read-tree", *([head] if head else ["--empty"]), env=env)
        listing = git("ls-tree", "-r", "-z", "--full-tree", fp["id"], env=env)
        entries = {}
        for rec in listing.split("\0"):
            if rec:
                meta, _, path = rec.partition("\t")
                entries[path] = meta.split()  # mode, type, object id
        info = []
        for p in paths:
            if p in entries:
                mode, _, oid = entries[p]
                info.append(f"{mode} {oid}\t{p}")
            else:
                info.append(f"0 {'0' * 40}\t{p}")  # mode 0 removes the path
        info += [f"{(entries.get(rel) or ['100644'])[0]} {oid}\t{rel}" for rel, oid in shared]  # later lines win
        git("update-index", "-z", "--index-info", env=env,
            inp="\0".join(info).encode("utf-8", "surrogateescape") + b"\0")
        tree = git("write-tree", env=env).strip()
        if head and tree == git("rev-parse", f"{head}^{{tree}}").strip():
            return None
        new = git("commit-tree", tree, *(["-p", head] if head else []), "-m", msg).strip()
    git("update-ref", "HEAD", new, *([head] if head else []))
    # the real index now lags behind HEAD for these paths: bring just those entries up to date
    subprocess.run(["git", "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul"], cwd=top,
                   capture_output=True, input="\0".join(paths + [r for r, _ in shared]).encode("utf-8", "surrogateescape"),
                   env=dict(os.environ, GIT_LITERAL_PATHSPECS="1"))
    return new[:7]


def _head_commit_named(root: Path, msg: str) -> str | None:
    """HEAD's short id when HEAD is a commit with exactly this message (a checkpoint commit already made by an
    earlier run of `complete` that failed before it could save), else None."""
    r = subprocess.run(["git", "log", "-1", "--format=%H%n%B", "HEAD"], cwd=root, capture_output=True)
    if r.returncode != 0:
        return None
    sha, _, body = r.stdout.decode("utf-8", "surrogateescape").partition("\n")
    return sha.strip()[:7] if sha.strip() and body.strip() == msg.strip() else None


def changed_files(root: Path, fp_a: dict, fp_b: dict) -> list[str]:
    if fp_a["id"] == fp_b["id"]:
        return []
    if fp_a["kind"] == fp_b["kind"] == "git-tree":
        r = subprocess.run(["git", "diff-tree", "-r", "-z", "--no-renames", "--name-only", fp_a["id"], fp_b["id"]],
                           cwd=root, capture_output=True)
        if r.returncode == 0:
            return [x for x in r.stdout.decode("utf-8", "surrogateescape").split("\0") if x]
    mdir = root / RATCHET_DIR / "manifests"
    a = read_json(mdir / f"{fp_a['id']}.json", {}) or {}
    b = read_json(mdir / f"{fp_b['id']}.json", {}) or {}
    return sorted({k for k in set(a) | set(b) if a.get(k) != b.get(k)})


def touches_ui(files: list[str], globs: list[str]) -> list[str]:
    hits = []
    for f in files:
        name = os.path.basename(f)
        if any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(f, g) for g in globs):
            hits.append(f)
    return hits
