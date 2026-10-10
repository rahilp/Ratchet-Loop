#!/usr/bin/env python3
"""
ratchet.py — state engine for the Ratchet loop (checkpoints + gates).

The orchestrator and its sub-agents never edit gate state by hand. They call
this script, which *verifies* evidence before it records anything:

  * Behavior gate  -> this script runs the test command itself and checks exit
                      code + that every planned test-case ID exists in the tests.
  * UI gate        -> this script reads the reviewers' JSON and computes the
                      verdict itself (any code-fixable difference = blocker).
  * Adversarial    -> this script reads the adversaries' JSON; passes only when
                      every reviewer returns zero findings at the CURRENT code
                      fingerprint.
  * Human gate     -> only accepted if a real user message was submitted after
                      the loop paused (recorded by the UserPromptSubmit hook).

Every pass is stamped with a code fingerprint. If code changes afterwards the
pass goes stale and must be re-earned (behavior always; UI when UI files
changed; adversarial always).

Python 3.8+ stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as _dt
import fnmatch
import hashlib
import hmac
import secrets
import json
import os
import re
import unicodedata
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

RATCHET_DIR = ".ratchet"
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
VIEWER_TEMPLATE = SKILL_DIR / "assets" / "viewer.html"
sys.path.insert(0, str(SCRIPT_DIR))
_dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # never litter the installed plugin with __pycache__
import spec_sources as specsrc  # noqa: E402
sys.dont_write_bytecode = _dwb

GATES = ["behavior", "ui", "adversarial", "human"]
STEPS = ["tests", "red", "implement", "behavior", "ui", "adversarial", "complete"]

# Phases in which the Stop hook lets the main agent stop.
STOPPABLE_PHASES = {
    "cancelled",
    "awaiting_plan_approval",
    "awaiting_checkpoint_review",
    "awaiting_final_review",
    "blocked",
    "paused",
    "done",
}

DEFAULT_CONFIG = {
    "human_review": "end",          # end | each | none   (plan approval is always required)
    # Which model is running this loop ("host"): auto-detected from the environment, or set it
    # explicitly (anthropic | openai | google | github | <any name>). Reviewers prefer a provider
    # DIFFERENT from the host; when none is available the host's own sub-agents review.
    "host_provider": "auto",
    # Other agent CLIs that can review, in order of preference. Any agent CLI works: use a preset
    # (claude, codex, gemini, copilot, cursor, opencode) or your own "command". "company" is whose model it runs
    # (worked out from "model" when not given); a reviewer only counts as independent when its company differs
    # from the host's. `ratchet.py providers --discover` checks what is installed and adds what works.
    # Each provider gives three reviewers: <id>-correctness, <id>-standards and <id>-both.
    "providers": [{"id": "codex", "preset": "codex"}, {"id": "gemini", "preset": "gemini"},
                  {"id": "claude", "preset": "claude"}],
    # Reviewers that run as the host's own sub-agents. host-alt-* use a DIFFERENT model from the host's company
    # (see host_models.alternates), the fallback before the host reviews its own work with its own model.
    "reviewers": {
        "host-standards":   {"kind": "agent", "lens": "standards", "vendor": "host"},
        "host-correctness": {"kind": "agent", "lens": "correctness", "vendor": "host"},
        "host-both":        {"kind": "agent", "lens": "both", "vendor": "host"},
        "host-alt-standards":   {"kind": "agent", "lens": "standards", "vendor": "host", "alt": True},
        "host-alt-correctness": {"kind": "agent", "lens": "correctness", "vendor": "host", "alt": True},
        "host-alt-both":        {"kind": "agent", "lens": "both", "vendor": "host", "alt": True},
    },
    # Review SLOTS by checkpoint risk; risk only changes how many reviewers look. "any-<lens>" means: the first
    # available provider from a different company (a company not yet reviewing this round preferred), else the
    # host's company on a different model (host-alt-<lens>), else the host itself (host-<lens>). A slot can also
    # name reviewer ids, or a list of ids as ordered alternatives.
    "review_depth": {
        "low":    ["any-both"],
        "normal": ["any-standards", "any-correctness"],
        "high":   ["any-standards", "any-correctness", "any-correctness", "any-standards"],
    },
    # Models for the roles the host runs as sub-agents, by tier. The tiers map to model names per host company in
    # host_models; "inherit" (or a host not listed) means the session's own model.
    "role_models": {
        "ratchet-checkpoint-planner": "strong", "ratchet-implementer": "strong", "ratchet-fixer": "strong",
        "ratchet-adversary": "strong", "ratchet-visual-reviewer": "strong",
        "ratchet-app-scout": "standard", "ratchet-test-planner": "standard", "ratchet-test-writer": "standard",
        "ratchet-design-system": "standard", "ratchet-prototyper": "standard", "ratchet-behavior-reviewer": "standard",
        "ratchet-test-runner": "light", "ratchet-variant-writer": "alternate",
    },
    "host_models": {
        "anthropic": {"strong": "inherit", "standard": "sonnet", "light": "haiku", "alternates": ["sonnet", "opus"]},
    },
    # Automatic checks added by guardrail steps (lint rules, architecture tests...). They run with the tests at every
    # behavior gate, in this run and later runs, so a mistake reviewers kept finding now fails in seconds.
    # Each: {"kind": "<mistake label>", "command": "<shell command>", "title": "...", "added": "<date>"}
    "checks": [],
    "probe_providers": True,        # send a tiny test request before relying on a provider
    "probe_ttl_minutes": 120,       # a provider that answered a test request this recently isn't tested again
    "probe_timeout_sec": 120,
    # Which finding severities block approval, by checkpoint risk. "all" = every finding must be fixed. On a low-risk
    # checkpoint minor findings are recorded for later (`ratchet.py deferred`) instead of starting another round.
    "blocking_severities": {"low": ["blocker", "major"], "normal": "all", "high": "all"},
    # From round 2 on, a minor finding only blocks if it is in code changed since the previous round; blocker and
    # major findings block wherever they are. Stops each round from turning into a fresh review of everything.
    "scope_later_rounds": True,
    # After this many blocking adversarial rounds in a row (or a second round where an unseen finding kind shows
    # up in a file an earlier round already flagged), pause for a check-in (`ratchet.py check-in CPn`) before
    # opening another round: see "Check-in" in schemas.md section 7.
    "checkin_round": 2,
    "unavailable_ttl_hours": 6,     # an other-provider reviewer that failed (auth, quota, timeout) is skipped this long
    "max_adversarial_rounds": 6,    # per checkpoint; after this the loop blocks and escalates
    "max_adversarial_rounds_run": 30,  # across the whole run (quota guard); then blocks and escalates
    "approver": "human",            # human | director | either  (see director-enroll)
    "max_ui_rounds": 6,
    "max_behavior_attempts": 8,
    # UI gate reviewers, per role: other providers in order of preference; "host" (the host's own
    # sub-agents in isolated folders) is always the final fallback.
    "ui_reviewers": {"visual": ["gemini", "claude", "codex"], "behavior": ["claude", "gemini", "codex"]},
    "ui_block_on_nits": True,       # every code-fixable difference is a blocker
    "ui_globs": [
        "*.tsx", "*.jsx", "*.vue", "*.svelte", "*.astro", "*.html", "*.css", "*.scss",
        "*.sass", "*.less", "*.styl", "*.swift", "*.kt", "*.xml", "*.dart", "*.erb",
        "*.blade.php", "*.hbs", "*.njk", "*.liquid", "tailwind.config.*", "*.svg",
    ],
    "spec_archive": True,
    "spec_keep": None,              # no spec tool: keep a running spec (openspec/specs/<run>/spec.md)? None = not asked yet           # archive an OpenSpec change (merge its deltas into the specs) when the run is approved
    "regression_command": None,     # full suite run before a checkpoint can complete
    "commit_per_checkpoint": True,
    "design_md": "DESIGN.md",
    "standards": [".ratchet/standards.md"],
    "app": {"start_command": None, "url": None, "healthcheck": None},
    "test_timeout_sec": 900,
    "max_stop_blocks": 40,          # safety valve for the Stop hook (reset whenever a gate makes progress)
    "fingerprint_include": [],      # gitignored files that still matter to behaviour (e.g. ".env.test"); added to fingerprints
    "ui_single_reviewer_ok": False, # allow a UI review with only one of visual/behaviour (human-set, locked at approval)
    "min_review_timeout_sec": 120,  # other-provider reviewers can't be given a timeout short enough to knock them out
    "verify_transcripts": True,     # approvals must appear as a real user turn in the agent CLI's own transcript
    "min_review_seconds": 20,       # a review written faster than this after its round opened is rejected
}

PROGRESS_EVENTS = {"tests_written", "red", "implemented", "behavior_passed", "ui_passed", "adversarial_approved",
                   "checkpoint_complete", "plan_submitted", "plan_approved", "plan_extended", "reviewers_launched"}
# A wait (`ratchet.py wait`) also ends when the run is paused, resumed or claimed, or cancelled.
WAIT_ENDING_EVENTS = {"paused", "resumed", "cancelled"}
WAIT_DEFAULT_MINUTES = 30
WAIT_MAX_MINUTES = 120

FINGERPRINT_EXCLUDES = {
    ".git", RATCHET_DIR, "node_modules", ".next", ".nuxt", "dist", "build", "out",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".turbo", ".cache", "coverage",
    "target", ".gradle", "Pods", "DerivedData", ".idea", ".vscode",
}


# --------------------------------------------------------------------------- utils

def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def die(msg: str, code: int = 1):
    print(f"RATCHET ✗ {msg}", file=sys.stderr)
    sys.exit(code)


def ok(msg: str):
    print(f"RATCHET ✓ {msg}")


def find_root(start: Path | None = None) -> Path:
    """Project root = nearest ancestor containing .ratchet, else git root, else cwd."""
    if start is None and os.environ.get("RATCHET_ROOT"):
        start = Path(os.environ["RATCHET_ROOT"])
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / RATCHET_DIR).is_dir():
            return cand
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=p,
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return p


def read_json(path: Path, default=None, record=None):
    """Read a JSON file through _read_record, the one reader.
    Without `record`: a file a person or an agent writes (config.json, a plan, a test plan, a host's settings). A
    damaged one stops the command with a plain message naming it, because only its author can fix it.
    With `record` (a sentence saying what Ratchet does instead): one of Ratchet's own records. A missing, unreadable,
    invalid or non-object file reads as `default`, the sentence goes to stderr, and this never exits."""
    data, why = _read_record(path, kind=dict if record else None, missing=default)
    if why is None:
        return data
    if record:
        _note_unreadable(path, why, record if isinstance(record, str) else "Ratchet treats it as holding nothing")
        return default
    if why.startswith(("JSONDecodeError", "UnicodeDecodeError")):
        die(f"{path} is not valid JSON: {why.split(': ', 1)[-1]}")
    die(f"{path} can't be read ({why}). Check that it is a file you can read, then run this command again.")


def write_json(path: Path, data):
    """Write a JSON file people may read (indented), through _write_record, the one atomic writer."""
    _write_record(path, data, pretty=True)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:48].rstrip("-")) or "feature"


_SAFE_SLUG_RE = re.compile(r"^(?!\.\.?$)[A-Za-z0-9._-]+$")  # a run folder name: no path separators, not . or ..
_STATE_LOCK_HELD = False  # set by main() while this process holds the state lock for its whole command


class Ctx:
    def __init__(self, root: Path):
        self.root = root
        self.ratchet = root / RATCHET_DIR

    # --- config
    @property
    def config_path(self):
        return self.ratchet / "config.json"

    def config(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        user = read_json(self.config_path, {}) or {}
        if user.get("review_depth") == OLD_DEFAULT_REVIEW_DEPTH:
            user = {k: v for k, v in user.items() if k != "review_depth"}  # untouched old default: use the new one
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        return cfg

    # --- active run
    def active_slug(self):
        """The active run's slug from active.json. A damaged active.json names the run this project registered in the
        per-user folder (active-runs.json, written with it at init) when that run's folder is here; otherwise no run.
        accept-state writes it again."""
        p = self.ratchet / "active.json"
        a, why = _read_record(p)
        if why is None:
            return a.get("slug")
        slug = (_active_marker(self.root) or {}).get("slug")
        if not (isinstance(slug, str) and _SAFE_SLUG_RE.match(slug) and (self.ratchet / "runs" / slug).is_dir()):
            slug = None
        _note_unreadable(p, why, f"Ratchet uses the active run recorded in its per-user folder, '{slug}'" if slug
                         else "Ratchet finds no active run recorded for this project in its per-user folder either")
        return slug

    def run_dir(self, slug=None) -> Path:
        slug = slug or self.active_slug()
        if not slug:
            die("No active Ratchet run. Start one with: ratchet.py init \"<feature>\"")
        return self.ratchet / "runs" / slug

    def state_path(self, slug=None):
        return self.run_dir(slug) / "state.json"

    def state(self, slug=None):
        """The run's state. Every command that changes a run holds the state lock from before this read to after its
        last save (see main), so nothing else changes state.json in between. A chat the prompt hook recorded for a
        run no chat owned yet becomes its owner here (the hook itself never writes state.json); a command holding
        the lock saves that at once."""
        p = self.state_path(slug)
        verify_chain(p, "state.json")
        st, why = _read_record(p, missing=None)
        if why is not None:
            die(f"The run's state.json ({p}) can't be read ({why}), so nothing was done. Tell the user; restore it "
                "from a backup or their git history, or, to start the feature over, they can run "
                "`RATCHET_HUMAN_OVERRIDE=1 ratchet.py init --force \"<feature>\"` from their own terminal.")
        if st is None:
            die("state.json missing for active run")
        if _bind_prompt_owner(self, st) and _STATE_LOCK_HELD:
            self.save_state(st)
        return st

    def save_state(self, st):
        st["updated_at"] = now()
        if st.get("hooks_waived") and not _waiver_applies(self.root, self.config(), st):
            st.pop("hooks_waived", None)
            st["hooks_waive_n"] = int(st.get("hooks_waive_n", 0)) + 1  # hooks are installed or fired: the old consent must not resurface later
            self.log(st, "hooks_waive_cleared", note="enforcement hooks are active")
        _write_record(self.state_path(st["slug"]), st, pretty=True)
        record_chain(self.state_path(st["slug"]))

    def plan(self, slug=None):
        return read_json(self.run_dir(slug) / "checkpoints.json")

    def evidence(self, cp_id: str, slug=None) -> Path:
        d = self.run_dir(slug) / "evidence" / cp_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log(self, st, event: str, **data):
        st.setdefault("history", []).append({"at": now(), "event": event, **data})
        # a PROGRESS_EVENTS entry also restarts the stop hook's count of stops without progress (_stop_blocks), and
        # ends a wait set with `ratchet.py wait` (the agent carries on from the progress instead)
        if (event in PROGRESS_EVENTS or event in WAIT_ENDING_EVENTS) and st.pop("wait", None) is not None:
            st["history"].append({"at": now(), "event": "wait_cleared", "by": event})


# --------------------------------------------------------------------------- fingerprint

def _is_git(root: Path) -> bool:
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


# Interpreter caches and OS litter: never part of the reviewed code, never committed by Ratchet.
JUNK_PATHSPECS = [":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc", ":(exclude,glob)**/.DS_Store"]


def fingerprint(root: Path) -> dict:
    """Hash of the working tree (tracked + untracked, respecting .gitignore, minus .ratchet)."""
    if _is_git(root):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
            # seed from HEAD if it exists so the add is fast
            subprocess.run(["git", "read-tree", "HEAD"], cwd=root, env=env,
                           capture_output=True)
            r = subprocess.run(["git", "add", "-A", "--", ".", f":(exclude){RATCHET_DIR}", *JUNK_PATHSPECS],
                               cwd=root, env=env, capture_output=True, text=True)
            if r.returncode != 0:
                die(f"git add for fingerprint failed: {r.stderr.strip()}")
            extra = (read_json(root / RATCHET_DIR / "config.json", {}) or {}).get("fingerprint_include") or []
            if extra:  # gitignored files that tests still depend on
                subprocess.run(["git", "add", "-f", "--", *extra], cwd=root, env=env, capture_output=True, text=True)
            t = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                               capture_output=True, text=True)
            if t.returncode != 0:
                die(f"git write-tree failed: {t.stderr.strip()}")
            return {"kind": "git-tree", "id": t.stdout.strip()}
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
    shared = []  # the .ratchet files people edit and share ride along with the checkpoint (never run state)
    for name in (".gitignore", "config.json", "standards.md", "learnings.md"):
        f = root / RATCHET_DIR / name
        if f.is_file():
            oid = subprocess.run(["git", "hash-object", "-w", "--", str(f)], cwd=root, capture_output=True,
                                 text=True).stdout.strip()
            if oid:
                shared.append((f"{RATCHET_DIR}/{name}", oid))
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
    subprocess.run(["git", "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul"], cwd=root,
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


# --------------------------------------------------------------------------- tamper evidence
# ratchet.py records a hash of every file it owns after each write, in RATCHET_HOME (outside the repo and off-limits
# to agents). A write by any other route — a helper script, an editor, a "quick fix" — is detected on the next read.

def _chain_path() -> Path:
    return ratchet_home() / "file-hashes.json"


_ABSENT = object()  # pass as _read_record's `missing` to tell a file that is not there from an empty one
_FRESH = object()  # _read_record's default for `missing`: a new empty record ({}, or [] for lines)


def _read_record(path: Path, kind=dict, missing=_FRESH, lines: bool = False, tail: int | None = None):
    """The one reader of Ratchet's records (every file under .ratchet/ and in RATCHET_HOME that the hooks or the
    engine read for state, approvals, crashes, notes, hashes, director folders, stop errors or active runs).
    Returns (data, None), or (None, why) when the file can't be read, is not valid JSON, or is not a `kind` (a JSON
    object by default; None accepts any JSON value). A file that does not exist yet returns (`missing`, None): a new
    empty record unless the caller passes its own marker. With `lines`, a JSON-lines file: (the JSON objects on its
    lines, None), skipping lines that are not one; `tail` reads only its last `tail` bytes.
    It never exits or raises, so no hook or command can crash on a damaged record: each caller fails safe on its own
    terms (a damaged record that grants trust grants nothing), and says so with _note_unreadable."""
    try:
        if tail is None:
            raw = path.read_bytes()
        else:
            with open(path, "rb") as f:
                f.seek(max(0, os.fstat(f.fileno()).st_size - tail))
                raw = f.read()
    except FileNotFoundError:
        return (([] if lines else {}) if missing is _FRESH else missing), None
    except OSError as e:  # a folder or a link loop where the file goes, no permission, a parent that is a file
        return None, f"{type(e).__name__}: {e}"
    except Exception as e:  # never raise, whatever the file system does
        return None, f"{type(e).__name__}: {e}"
    if lines:
        out = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
        return out, None
    try:
        data = json.loads(raw.decode("utf-8-sig"))  # a leading byte-order mark is not damage
    except ValueError as e:  # invalid JSON, or bytes that are not UTF-8
        return None, f"{type(e).__name__}: {e}"
    except RecursionError:
        return None, "RecursionError: nested too deeply"
    if kind is not None and not isinstance(data, kind):
        return None, "not a JSON object" if kind is dict else f"not a JSON {getattr(kind, '__name__', kind)}"
    return data, None


def _write_record(path: Path, data, pretty: bool = False):
    """The one writer of Ratchet's records: replace the file in one rename, through a temp file of this process's
    own (pid plus a random token), flushed to disk first, so two writers never share a temp file, a reader never
    sees half a write, and a crash never leaves a half-written record. The temp file is removed on failure.
    Raises OSError when it can't write; every caller handles that on its own terms."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
    text = (json.dumps(data, indent=2, ensure_ascii=False) + "\n") if pretty else json.dumps(data)
    try:
        # backslashreplace: a lone surrogate (from a reviewer's reply) is written as its own JSON escape, "\ud83d",
        # which reads back as the same string; every other character encodes as UTF-8 unchanged
        with open(tmp, "w", encoding="utf-8", errors="backslashreplace") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _append_record(path: Path, text: str):
    """The one appender for Ratchet's append-only records (human_log.jsonl, the run's .jsonl records, hook-errors.log,
    hook-crashes-unsaved.log): whole lines, added at the end, never rewriting what is there. Raises OSError."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


_NOTED_UNREADABLE = set()


def _note_unreadable(path: Path, why: str, outcome: str):
    """Say plainly, on stderr, that a damaged record was set aside and what Ratchet did instead (once per file and
    cause in a process)."""
    try:
        key = (str(path), why, outcome)
        if key in _NOTED_UNREADABLE:
            return
        _NOTED_UNREADABLE.add(key)
        print(f"ratchet: {path} could not be read ({why}); {outcome}", file=sys.stderr)
    except Exception:
        pass


class _BreakerLock:
    """The lock protocol both of Ratchet's lock files share (_ChainLock and its per-user siblings, and _StateLock), so
    they behave the same and a fix is made once. The lock file is created with O_CREAT|O_EXCL and holds this
    process's own token. It is only ever removed (a stale one, or the holder releasing its own) while holding a second
    O_CREAT|O_EXCL file, `<lock>.break`, and judged again under it, so two processes clearing one stale lock can't
    remove each other's fresh lock, and a release never removes another process's lock. Only a regular file is ever
    judged stale or removed (a folder or a link in its place is left alone). A breaker file older than BREAKER_STALE
    seconds was left by a process that died while holding it (it is held for a few system calls) and is removed, so
    a crash there can't disable the lock for good; only the very file judged stale is removed (_remove_stale_breaker),
    and a holder checks that its breaker is still in place before it removes anything under it (_holds_breaker), so
    two waiters behind one stale breaker can't both hold it. Subclasses say how old a lock must be to be stale (_stale_after)
    and how long to pause between tries (_pause)."""

    BREAKER_STALE = 30

    def __init__(self, path: Path, token: str):
        self.path = path
        self.token = token
        self.held = False
        self._breaker_id = None  # (device, inode) of the breaker file this process created, while it holds it

    @property
    def breaker(self) -> Path:
        return self.path.with_name(self.path.name + ".break")

    def _stale_after(self) -> float:
        raise NotImplementedError

    def _pause(self):
        time.sleep(0.005)

    def _create(self, path: Path):
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.token)

    def _is_stale(self, st) -> bool:
        """Whether a lock file with this lstat() result was left by a command that stopped: a regular file older
        than _stale_after() seconds."""
        return stat.S_ISREG(st.st_mode) and time.time() - st.st_mtime > self._stale_after()

    def _take_breaker(self, wait: float) -> bool:
        """True once this process holds the breaker file; False when another process holds it for longer than
        `wait`. Raises OSError when it can't be created at all (the caller decides what that means)."""
        deadline = time.monotonic() + wait
        while True:
            try:
                self._create(self.breaker)
                try:  # the file this process created: what _holds_breaker checks later
                    st = os.lstat(self.breaker)
                except OSError:  # moved away at once by a waiter clearing a stale breaker: try again
                    continue
                self._breaker_id = (st.st_dev, st.st_ino)
                return True
            except FileExistsError:
                try:  # held for a few system calls: one this old was left by a process that died holding it
                    st = os.lstat(self.breaker)
                    if stat.S_ISREG(st.st_mode) and time.time() - st.st_mtime > self.BREAKER_STALE:
                        self._remove_stale_breaker(st)
                        continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                return False
            self._pause()

    def _remove_stale_breaker(self, st):
        """Remove the breaker file judged stale (its lstat() result is `st`), and only that file. Another waiter may
        have removed it and taken a fresh breaker since it was judged, so it is never unlinked by path: it is first
        renamed to a name of this process's own, and only removed when the renamed file is the one judged (same
        device, inode and modification time). Otherwise it is a fresh breaker another process holds, and it is put
        back at once (a hard link that never replaces a newer breaker). Raises OSError when the rename fails (the
        file is gone already: the caller tries to create the breaker again)."""
        mine = self.breaker.with_name(f"{self.breaker.name}.{os.getpid()}-{secrets.token_hex(4)}.stale")
        os.rename(self.breaker, mine)
        try:
            got = os.lstat(mine)
            if (got.st_dev, got.st_ino, got.st_mtime_ns) != (st.st_dev, st.st_ino, st.st_mtime_ns):
                try:  # a live breaker taken by mistake: give it back
                    os.link(mine, self.breaker)
                except OSError:
                    pass
        finally:
            try:
                os.unlink(mine)
            except OSError:
                pass

    def _holds_breaker(self) -> bool:
        """Whether the breaker file in place is still the one this process created (a waiter clearing a stale
        breaker may have moved it away for a moment). Checked right before removing anything under it."""
        try:
            st = os.lstat(self.breaker)
        except OSError:
            return False
        return self._breaker_id is not None and (st.st_dev, st.st_ino) == self._breaker_id

    def _drop_breaker(self):
        try:
            if self._holds_breaker():  # never another process's breaker
                os.unlink(self.breaker)
        except OSError:
            pass
        self._breaker_id = None

    def _clear_stale(self) -> bool:
        """Remove the lock file if it is still stale when judged again while holding the breaker file. True when
        the lock file is gone now (removed, or already released), so the caller can try to take it at once; False
        when another process holds the breaker or the lock is live. Raises OSError for a file-system error."""
        if not self._take_breaker(0):
            return False  # another process is clearing or releasing the lock right now: try again shortly
        try:
            if not self._holds_breaker():
                return False  # moved away by a waiter clearing a stale breaker: judge again later
            if self._is_stale(os.lstat(self.path)):
                os.unlink(self.path)
                return True
            return False  # a newer, live lock (or not a lock file): wait for it
        except FileNotFoundError:
            return True
        finally:
            self._drop_breaker()

    def _release_own(self, wait: float):
        """Remove the lock file if it still holds this process's token, under the breaker file. When the breaker
        can't be had within `wait` seconds the lock is left in place (it goes stale and is cleared later): a lock
        that might not be ours is never removed."""
        try:
            if not self._take_breaker(wait):
                return
        except OSError:
            return
        try:
            if self._holds_breaker() and self.path.read_text(encoding="utf-8") == self.token:
                os.unlink(self.path)
        except OSError:
            pass
        finally:
            self._drop_breaker()


class _ChainLock(_BreakerLock):
    """A short lock (milliseconds) around the hash record: its read-modify-write, an append to a recorded file
    together with its new hash, and a check of a file against its hash. The prompt hook appends to human_log.jsonl
    without the state lock (it never waits on a long command), so a reader must never see that file between the
    append and its new hash, and two writers must never drop each other's hash. Best effort: when it can't be had
    within a few seconds (an unwritable per-user folder, say) the caller goes on without it, as before.
    The breaker protocol is _BreakerLock's; a lock older than STALE seconds was left by a process that died."""

    STALE = 30

    def __init__(self, wait: float = 5.0):
        super().__init__(ratchet_home() / "file-hashes.lock", f"{os.getpid()} {secrets.token_hex(8)}")
        self.wait = wait

    def _stale_after(self) -> float:
        return self.STALE

    def __enter__(self):
        deadline = time.monotonic() + self.wait
        while True:
            try:  # no per-user folder yet: there is no hash record to protect
                self._create(self.path)
                self.held = True
                return self
            except FileExistsError:
                try:  # held for milliseconds: one older than half a minute was left by a process that died
                    if self._is_stale(os.lstat(self.path)) and self._clear_stale():
                        continue
                except OSError:
                    pass
            except OSError:
                return self
            if time.monotonic() >= deadline:
                return self
            time.sleep(0.01)

    def __exit__(self, *exc):
        if self.held:
            self.held = False
            self._release_own(1.0)  # can't be had: leave the lock; it goes stale and is cleared later
        return False


def _record_lock_path(record: Path) -> Path:
    """The lock file a shared record is protected by: named after the record, next to it (`active-runs.lock` for
    active-runs.json, `enrolled.lock`, `hook-heartbeat.lock`, ...). The one place that name is built, so _RecordLock
    and every busy message that names the lock stay in step."""
    return record.with_name(record.stem + ".lock")


class _RecordLock(_ChainLock):
    """The short lock update_record holds by default around one shared record's read-modify-write: a lock file named
    after the record, next to it (`active-runs.lock` for active-runs.json, `enrolled.lock`, `hook-heartbeat.lock`,
    ...). Built like file-hashes.lock: the breaker protocol is _BreakerLock's, it is held for milliseconds, and one
    older than STALE seconds was left by a process that died and is cleared."""

    def __init__(self, record: Path, wait: float = 5.0):
        super().__init__(wait)
        self.path = _record_lock_path(record)


KEEP = object()  # update_record: `fn` or `damaged` returns it to leave the record exactly as it is


def update_record(path: Path, fn, lock=None, damaged=None, pretty: bool = False, need_lock: bool = True,
                  wait: float = 5.0) -> str:
    """The one read-modify-write of a shared record: a file more than one process can update at once (every record in
    RATCHET_HOME, and in .ratchet/ the ones hooks or commands outside the state lock rewrite). Two unlocked writers
    would each read the file, add their own entry and write the whole file back, and the later write would silently
    drop the other's entry. So this holds a _BreakerLock-based lock for that file from the read to the write: it reads
    with _read_record, applies `fn(data)` and writes the result with _write_record. The lock is a _RecordLock next to
    the record, taken here, unless the caller passes `lock`: one it has already entered (its own lock file for this
    record, or a wider step under the same lock), used as it is. The default lock is waited for at most `wait`
    seconds.
    `fn(data)` returns the new data, or KEEP to leave the file as it is.
    `damaged` is the file's own rule for a record that can't be read: a sentence (it reads as empty through read_json,
    which says so on stderr with that sentence, and is replaced), or a function of the reason returning the data to
    start from (the file is then replaced) or KEEP. Default: it is left as it is, and stderr says so.
    Returns "written", "kept" (fn kept it), "damaged" (left as it is) or "busy" (the lock could not be had in time:
    nothing was read or written, unless need_lock is False, when it goes on without it, best effort as before).
    Raises OSError when the folder or the write fails; every caller handles that on its own terms."""
    with contextlib.ExitStack() as stack:
        if lock is None:
            path.parent.mkdir(parents=True, exist_ok=True)  # the lock file goes in this folder
        lk = lock if lock is not None else stack.enter_context(_RecordLock(path, wait))
        if not lk.held and need_lock:
            return "busy"
        if isinstance(damaged, str):
            data = read_json(path, {}, record=damaged)
        else:
            data, why = _read_record(path)
            if why is not None:
                if damaged is None:
                    _note_unreadable(path, why, "Ratchet left it as it is and did not write over it")
                    return "damaged"
                data = damaged(why)
                if data is KEEP:
                    return "damaged"
        new = fn(data)
        if new is KEEP:
            return "kept"
        _write_record(path, new, pretty=pretty)
        return "written"


_CHAIN_REBUILD = False  # set by accept-state (a person confirmed at their terminal): an unreadable record is rebuilt


def _chain_record():
    """(the hash record, None), or (None, why) when it can't be read or is not a JSON object."""
    return _read_record(_chain_path())


def _record_chain_now(p: Path, lock: _ChainLock):
    """Record p's hash, under `lock` (the caller's _ChainLock, already entered; best effort as ever: when it could
    not be had, the hash is still recorded). An unreadable hash record is never replaced here (a fresh record would
    drop every other file's hash, and a file with no recorded hash is trusted): it is left as it is, so every check
    against it keeps refusing, until accept-state, run by a person at their own terminal, rebuilds it. Never exits."""
    cp = _chain_path()

    def damaged(why):
        if not _CHAIN_REBUILD:
            _note_unreadable(cp, why, "the new hash was not recorded and the file was left as it is; every check "
                             "against it refuses until the user runs accept-state")
            return KEEP
        _note_unreadable(cp, why, "accept-state replaces it with a new record")
        return {}

    def add(data):
        raw = p.read_bytes()
        key = str(p.resolve())
        data[key] = hashlib.sha256(raw).hexdigest()
        return data
    try:
        _ = update_record(cp, add, lock=lock, damaged=damaged, need_lock=False)  # need_lock=False: never "busy"
    except OSError:
        pass


def record_chain(p: Path):
    with _ChainLock() as lock:
        _record_chain_now(p, lock)


def append_recorded(p: Path, line: str):
    """Append one line to a file Ratchet keeps a hash of and record its new hash, as one step for every reader."""
    with _ChainLock() as lock:
        _append_record(p, line)
        _record_chain_now(p, lock)


ACCEPT_STATE_CMD = "`RATCHET_HUMAN_OVERRIDE=1 ratchet.py accept-state`"


def chain_problem(p: Path, label: str) -> str | None:
    """Why `p` can't be trusted against Ratchet's record of file hashes (None when it can). Never exits, so a hook can
    use it: a damaged record vouches for nothing (it is a problem, never permission), and so does a file that can't
    be read."""
    try:
        with _ChainLock():
            record, why = _chain_record()
            known = (record or {}).get(str(p.resolve()))
            same = why is None and (not known or not p.exists()
                                    or hashlib.sha256(p.read_bytes()).hexdigest() == known)
    except OSError as e:
        return (f"{label} ({p}) can't be read ({type(e).__name__}: {e.strerror or e}), so Ratchet can't confirm it "
                "was not changed outside ratchet.py, and won't trust it. Tell the user; check that it is a file "
                "Ratchet can read.")
    if why is not None:  # a damaged record vouches for nothing: refuse rather than trust the file
        return (f"Ratchet's record of file hashes ({_chain_path()}) can't be read ({why}), so it can't confirm that "
                f"{label} ({p}) was not changed outside ratchet.py, and won't trust it. Tell the user; only if they "
                f"are sure the file is right, they can rebuild the record from their own terminal with "
                f"{ACCEPT_STATE_CMD} (it asks them to type back a short code).")
    if not same:
        return (f"{label} was changed outside ratchet.py ({p}). Ratchet won't trust it. Tell the user; restore the "
                "file, or, only if they are sure, they can accept it from their own terminal with "
                f"{ACCEPT_STATE_CMD} (it asks them to type back a short code).")
    return None


def verify_chain(p: Path, label: str):
    if not _IN_HOOK:  # the record of hashes lives in the per-user folder: only trust the one the hooks use
        for cand in p.resolve().parents:
            if cand.name == RATCHET_DIR:
                require_hook_env(cand.parent)
                break
    why = chain_problem(p, label)
    if why:
        die(why)


# ---- the human override: a person at their own terminal ------------------------------------------------------
# RATCHET_HUMAN_OVERRIDE=1 alone proves nothing: any agent can set an environment variable. So the override also
# opens the real terminal (/dev/tty; CONIN$/CONOUT$ on Windows), prints a short random code there and reads it back
# in the same process. A command with no terminal (an agent's shell tool runs on pipes) or a wrong or missing
# answer is refused. This is best-effort: an agent that can start its own pseudo-terminal and read the code from it
# can still answer; the PreToolUse guard refuses commands that set the variable, which covers the usual route.

_OVERRIDE_CONFIRMED = False
OVERRIDE_TIMEOUT = 300  # seconds a person has to type the code back
NO_TERMINAL_MSG = ("RATCHET_HUMAN_OVERRIDE is for a person at their own terminal, and this command has no terminal "
                   "attached (an agent's shell tool runs without one), so nothing was approved. If you are the "
                   "person, run the command yourself in a terminal window and type back the code it shows.")


def _read_line_with_timeout(readline, timeout):
    """One line from a blocking `readline` (the Windows console has no select()), or None when nothing arrives
    within `timeout` seconds, at end of input, or when the read fails. The reader thread is a daemon: a refusal
    exits the process even while it still waits on the console."""
    import threading
    box = []

    def run():
        try:
            box.append(readline())
        except Exception:
            box.append(None)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive() or not box:
        return None
    return box[0] or None


def _open_terminal():
    """(read, write) callables bound to the real terminal, or None when there is none."""
    if os.name == "nt":
        try:
            fin = open("CONIN$", "r", encoding="utf-8", errors="replace")
            fout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        except OSError:
            return None

        def w(s):
            fout.write(s)
            fout.flush()
        return (lambda: _read_line_with_timeout(fin.readline, OVERRIDE_TIMEOUT)), w
    try:
        fd = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0))
    except OSError:
        return None
    if not os.isatty(fd):
        os.close(fd)
        return None

    def r():
        import select
        import time
        buf, deadline = b"", time.time() + OVERRIDE_TIMEOUT
        while b"\n" not in buf and b"\r" not in buf:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                ready, _, _ = select.select([fd], [], [], left)
                if not ready:
                    return None
                chunk = os.read(fd, 256)
            except OSError:
                return None  # the terminal went away
            if not chunk:
                return None  # end of input before an answer
            buf += chunk
        return buf.decode("utf-8", "replace")

    def w(s):
        os.write(fd, s.encode("utf-8", "replace"))
    return r, w


def human_override(purpose: str, ctx=None, action: str | None = None) -> bool:
    """False when RATCHET_HUMAN_OVERRIDE is not set. When it is set, the person must type back a printed code at a
    real terminal in this same process; anything else dies with a plain message (never falls back to the bare
    variable). Asked at most once per process.
    The audit line is part of the override itself: before it returns True, it appends `action` to human_log.jsonl
    (log_override) and dies when that line can't be written. Callers check it BEFORE they change anything, so no
    caller can use a confirmed override without its audit line, and a refusal leaves everything unchanged. A caller
    that passes no ctx/action is refused too (fail closed), never approved unaudited."""
    global _OVERRIDE_CONFIRMED
    if os.environ.get("RATCHET_HUMAN_OVERRIDE") != "1":
        return False
    if not _OVERRIDE_CONFIRMED:
        _confirm_override(purpose)
    if ctx is None or not action:
        die("The override was confirmed, but this command did not say what to record in the audit log, so nothing "
            "was approved or changed: an override is only used when it is recorded.")
    log_override(ctx, action)
    return True


def _confirm_override(purpose: str):
    """Ask for the printed code at the real terminal; dies unless it is typed back correctly."""
    global _OVERRIDE_CONFIRMED
    term = _open_terminal()
    if term is None:
        die(NO_TERMINAL_MSG)
    read, write = term
    code = f"{secrets.randbelow(10 ** 6):06d}"
    try:
        write(f"RATCHET: RATCHET_HUMAN_OVERRIDE is set, to {purpose}.\n"
              f"To confirm you are a person at this terminal, type this code and press Enter: {code}\n> ")
        answer = read()
    except OSError:
        answer = None
    if answer is None:
        die("No code was typed back at the terminal, so nothing was approved. RATCHET_HUMAN_OVERRIDE only works "
            "when a person types back the code Ratchet shows.")
    if not hmac.compare_digest(answer.strip().encode(), code.encode()):
        die("The code typed back does not match the one shown, so nothing was approved. Run the command again "
            "and type the new code it shows.")
    _OVERRIDE_CONFIRMED = True


def log_override(ctx, action: str):
    """Leave an audit line in human_log.jsonl. It is not a chat message, so it never answers a question.
    Only human_override calls this (the audit is part of the override), and every caller of human_override checks
    it BEFORE it changes anything, so an override that can't be recorded is refused (die) and
    nothing is approved, accepted or replaced: a confirmed override is never used without its audit line."""
    log = ctx.ratchet / "human_log.jsonl"
    try:
        append_recorded(log, json.dumps({"at": now(), "by": "human", "override": True, "action": action,
                                         "via": "RATCHET_HUMAN_OVERRIDE, code typed back at the terminal"}) + "\n")
    except OSError as e:
        die(f"The override was confirmed, but Ratchet could not write its audit line to {log} "
            f"({type(e).__name__}: {e.strerror or e}), so nothing was approved or changed: an override is only used "
            "when it is recorded. Make that file (and .ratchet/) writable, then run the command again.")


ACCEPT_STATE_PURPOSE = "accept the current state.json and human_log.jsonl as the new baseline"


def _refuse_damaged_baseline(ctx):
    """accept-state's refusal of a damaged state.json (it can't be a baseline): says how to get out and changes
    nothing. Read-only, so main runs it before it asks the person for the code."""
    sp = ctx.state_path()
    _st, why = _read_record(sp, missing=None)
    if why is not None:
        die(f"The run's state.json ({sp}) can't be read ({why}), so it can't be accepted as the new baseline, and "
            "nothing was changed. Restore it (from a backup or your git history) and run accept-state again; or, to "
            "start the feature over, run `RATCHET_HUMAN_OVERRIDE=1 ratchet.py init --force \"<feature>\"` yourself "
            "in a terminal window.")


def cmd_accept_state(args):
    if os.environ.get("RATCHET_HUMAN_OVERRIDE") != "1":
        die("accept-state is for a person at their own terminal: run `RATCHET_HUMAN_OVERRIDE=1 ratchet.py "
            "accept-state` yourself in a terminal window and type back the code it shows.")
    ctx = Ctx(find_root())
    sp = ctx.state_path()
    _refuse_damaged_baseline(ctx)  # main already checked, before asking for the code; checked again under the lock
    purpose = ACCEPT_STATE_PURPOSE
    if not _OVERRIDE_CONFIRMED:  # main confirms first; this keeps any other caller from going on unconfirmed
        _confirm_override(purpose)  # dies unless the person types the code back
    global _CHAIN_REBUILD
    _CHAIN_REBUILD = True  # the person confirmed: an unreadable hash record is rebuilt from these files, starting
    # with the audit line's own write below (so it never says the record waits for the accept-state running now)
    human_override(purpose, ctx, "accept-state")
    _act, act_why = _read_record(ctx.ratchet / "active.json")
    if act_why is not None and ctx.active_slug():  # named again from the per-user marker (Ctx.active_slug)
        try:
            _write_record(ctx.ratchet / "active.json", {"slug": ctx.active_slug()}, pretty=True)
            print(f"RATCHET: {ctx.ratchet / 'active.json'} was damaged; it names run '{ctx.active_slug()}' again.")
        except OSError:
            pass
    for p in (sp, ctx.ratchet / "human_log.jsonl"):
        if p.exists():
            record_chain(p)
    ok("Current state.json and human_log.jsonl accepted as the new baseline.")


# --------------------------------------------------------------------------- run bookkeeping

def _set_blocked(st, reason):
    """Block the run and remember where it was, so an unblock returns to the same place."""
    if st.get("phase") != "blocked":
        st["blocked_from"] = st.get("phase")
    st["phase"] = "blocked"
    st["blocked_reason"] = reason
    st["awaiting_since"] = now()


def ratchet_home() -> Path:
    """Per-user Ratchet data OUTSIDE any repository (director key, active-run markers)."""
    return Path(os.environ.get("RATCHET_HOME") or Path.home() / ".config" / "ratchet")


# ---- the per-user folder the hooks use ------------------------------------------------------------------------
# A hook runs with the agent CLI's own environment, not the environment of a shell the agent controls. So every hook
# records which per-user folder (RATCHET_HOME) and director key file it sees, in .ratchet/hook-env.json (written by
# ratchet.py only; the guard refuses agent writes there). Any other ratchet.py command then refuses to run when its
# own RATCHET_HOME or RATCHET_DIRECTOR_KEY_FILE points somewhere else: a folder an agent made up would hold no
# integrity record and could hold a director key of its own making. The guard also records the role of the session
# that last ran a command here (see "who counts as the director").

_IN_HOOK = False  # set while a hook runs: its own environment is the one that counts


def _hook_env_path(root: Path) -> Path:
    return root / RATCHET_DIR / "hook-env.json"


def _same_path(a, b) -> bool:
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def _current_hook_env() -> dict:
    return {"home": os.path.realpath(str(ratchet_home())), "key_file": os.path.realpath(str(director_key_path()))}


def _hook_role() -> str:
    """The role of the agent CLI a hook runs for, from the hook's own (launch-time) environment."""
    return "director" if os.environ.get("RATCHET_ROLE") == "director" else "builder"


HOOK_ENV_RECORDS = "hook-env"  # a folder of per-run hook records; hook-env.json is their combined view
_HOOK_ENV_REC_RE = re.compile(r"^hook-env\.json\.(guard|env)\.(\d+)\.(director|builder|-)\.[A-Za-z0-9_-]+$")
_HOOK_ENV_CORE = ("home", "key_file", "guard_role")
_HOOK_ENV_TIE_RANK = {"director": 0, "-": 1, "builder": 2}  # same start time: the higher rank is the newer record


def _hook_env_records(root: Path):
    """[(microseconds, kind, role, name, path)] for every per-run hook record in .ratchet/hook-env/, oldest first
    ([] when there is none; None when the folder can't be listed). The start time of the hook run and, for a guard,
    the session role live in the name, so the role is read without opening a file another hook may be deleting."""
    folder = root / RATCHET_DIR / HOOK_ENV_RECORDS
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []
    except OSError:
        return None
    out = []
    for name in names:
        m = _HOOK_ENV_REC_RE.match(name)
        if m:
            out.append((int(m.group(2)), m.group(1), m.group(3), name, folder / name))
    # Same start microsecond: the less privileged role counts as the newer one, so on an exact tie between a director
    # and a builder guard the builder wins (never a random pick by the name's token, which would favour the director
    # half the time). Only then the name, so the order is always the same.
    return sorted(out, key=lambda r: (r[0], _HOOK_ENV_TIE_RANK.get(r[2], 1), r[3]))


def _combined_hook_env(recs) -> dict | None:
    """hook-env.json as the records say: `home` and `key_file` from the newest record, `guard_role` from the newest
    guard record (newest by when each hook run started). None when a listed record vanished while it was read (a
    newer hook removed it): the caller lists again."""
    view = None
    for _us, _kind, _role, _name, path in reversed(recs):
        data, why = _read_record(path, missing=_ABSENT)
        if data is _ABSENT:
            return None
        if why is not None:
            continue  # not a record Ratchet wrote (records are renamed into place whole): skip it
        if data.get("home") and data.get("key_file"):
            view = {"home": data["home"], "key_file": data["key_file"], "at": data.get("at"), "host": data.get("host")}
            break
    if view is None:
        return None
    guards = [r for r in recs if r[1] == "guard"]
    if guards:
        view["guard_role"] = guards[-1][2]
    return view


def record_hook_env(root: Path, host: str, event: str = "", started: str | None = None) -> bool:
    """Called by every hook: remember the per-user folder and key file this agent CLI uses, and (guard only: it runs
    right before every command a session runs) the role of the session that last ran a command here.
    No file is read, changed and written back, so concurrent hooks cannot drop each other's newer values (the same
    design as the crash markers): each run only creates its own uniquely named record in .ratchet/hook-env/ (the
    run's start time and, for a guard, its role in the name; renamed into place whole), removes the records its own
    supersedes, and then makes hook-env.json (rewritten in one rename, so a reader never sees half a file) agree
    with the records: the newest record gives `home` and `key_file`, the newest guard record gives `guard_role`.
    Each writer checks again after its rewrite and repeats while the records say something else, so once the hooks
    finish the file matches the newest records whatever order they finished in (_recorded_guard_role also reads the
    role from the records directly). Returns False when the record could not be saved: run_hook then reports it as
    a hook error (never silently), and the readers refuse instead of reading a missing record as permission
    (hook_env_problem, director_session_problem)."""
    p = _hook_env_path(root)
    folder = root / RATCHET_DIR / HOOK_ENV_RECORDS
    cur = _current_hook_env()
    us = _iso_us(started or now()) or _iso_us(now())
    kind = "guard" if event == "guard" else "env"
    role = _hook_role() if kind == "guard" else "-"
    tag = f"{os.getpid()}-{secrets.token_hex(4)}"
    try:
        folder.mkdir(exist_ok=True)
        _write_record(folder / f"{p.name}.{kind}.{us}.{role}.{tag}", dict(cur, at=now(), host=host))
    except OSError:
        return False
    for r_us, r_kind, _r, _n, path in _hook_env_records(root) or []:
        if r_us < us and (kind == "guard" or r_kind == "env"):  # a guard record supersedes every older record
            try:
                path.unlink()
            except OSError:
                pass  # another run removed it first, or (Windows) it is busy: an old record changes nothing
    for _ in range(50):
        recs = _hook_env_records(root)
        if recs is None:
            return False
        view = _combined_hook_env(recs)
        if view is None:
            continue  # a record vanished while it was read: list again
        disk, _why = _read_record(p, missing=None)  # missing or damaged: rewritten below
        if isinstance(disk, dict) and all(disk.get(k) == view.get(k) for k in _HOOK_ENV_CORE):
            return True
        try:
            _write_record(p, {k: v for k, v in view.items() if v is not None})
        except OSError:
            return False
    return False


def hooks_installed_anywhere(*roots) -> bool:
    """True when Ratchet's hooks are installed for ANY agent CLI, at user scope or in the project scope of any of
    `roots`. Any host, not just the detected one: host detection reads environment variables a builder's script
    can set, so it must not be able to make installed hooks look absent."""
    return any(installed_hook_files(h, Path(r)) for h in HOOK_HOSTS for r in roots if r is not None)


def hooks_in_use(*roots) -> bool:
    """True when Ratchet's hooks are in use for any of these projects: installed for any agent CLI (user scope or a
    project's own config), or delivered some other way (a plugin) and seen working: a guard or stop hook (the ones
    that enforce) has run in one of these projects, or a hook failure there is still unresolved. A prompt hook
    alone does not count (it enforces nothing, as in hooks_status)."""
    if hooks_installed_anywhere(*roots):
        return True
    for r in roots:
        if r is None or not (Path(r) / RATCHET_DIR).is_dir():
            continue
        hb = read_heartbeat(Path(r))
        if isinstance(hb.get("guard"), dict) or isinstance(hb.get("stop"), dict) or unresolved_hook_crashes(Path(r),
                                                                                                         "unknown"):
            return True
    return False


def _hook_record_may_have_failed(root: Path) -> bool:
    """True when a missing hook-env record here may be a failed write rather than "no hook has run yet": a hook that
    cannot save its record records that as a crash (_hook_record_failed), which stays unresolved until a later run
    saves the record cleanly; and while Ratchet cannot read or save its crash state, a failure could go unrecorded."""
    return bool(hook_crash_state_problem(root) or unresolved_hook_crashes(root, "unknown"))


def _hook_record_doubt(root: Path) -> str | None:
    """Why the hook record of this project may be out of date (None when it can be trusted): a hook failure here is
    still unresolved (a crash, or a record it could not save: run_hook records both the same way), or Ratchet can't
    read or save its crash state. A failed write leaves the OLD record in place, which may name a director that no
    longer runs commands here, so no existing record is trusted while any such failure stands."""
    problem = hook_crash_state_problem(root)
    if problem:
        return f"Ratchet can't trust its hook crash records ({problem})"
    crashes = unresolved_hook_crashes(root, "unknown")
    if crashes:
        c = crashes[0]
        return (f"the {c['event']} hook on {c['host']} failed at {c['at'][:19]}Z (it crashed or could not save its "
                "record) and has not run cleanly since")
    return None


def hook_env_problem(root: Path, doubt: bool = True) -> str | None:
    """A plain reason when this process's per-user folder or director key file differs from what the hooks of
    this project recorded; None when they agree. A missing record is accepted only while it can mean "no hook has
    run here yet": hooks not installed, or installed but not yet run here (the agent CLI was not restarted). When a
    hook may have failed to save it (an unresolved hook crash, which is how run_hook records that failure, or crash
    state Ratchet can't read or save), the missing record is refused, never read as "anything goes". With `doubt`
    (the default), an existing record that agrees is refused too while such a failure stands (_hook_record_doubt):
    the failed write may have left it out of date."""
    rec, why = _read_record(_hook_env_path(root), kind=None, missing=_ABSENT)
    if rec is _ABSENT:
        if not (root / RATCHET_DIR).is_dir() or not hooks_in_use(root):
            return None  # no Ratchet state here, or no hooks in use: nothing records (or enforces) anything
        if not _hook_record_may_have_failed(root):
            return None  # hooks installed but none has run here yet (the agent CLI needs a restart)
        return (f"Refused: Ratchet's hooks are installed, but {_hook_env_path(root)} is missing and a hook may have "
                "failed to save it, so Ratchet can't confirm which per-user folder and director key file its hooks "
                "use, or whether this is the director's session. Every command here is refused until a hook saves "
                "it, director commands included (RATCHET_ROLE=director counts for nothing without that record); "
                f"only {', '.join(RECOVERY_CMDS)} still run, to show what is wrong. "
                "Make sure .ratchet/ is writable, then restart your agent CLI (or send it any message) so its hooks "
                "write the record again.")
    if why is not None:
        return (f"{_hook_env_path(root)} is unreadable, so Ratchet can't tell which per-user folder its hooks use. "
                "Delete it; the next hook run (send any message in your agent CLI) writes it again.")
    if not isinstance(rec, dict) or not rec.get("home") or not rec.get("key_file"):
        return (f"{_hook_env_path(root)} is not a complete record (a JSON object with `home` and `key_file`). "
                "Delete it; the next hook run writes it again.")
    cur = _current_hook_env()
    if rec.get("home") and not _same_path(rec["home"], cur["home"]):
        return (f"Ratchet's per-user folder here is {cur['home']}, but the hooks for this project use "
                f"{rec['home']}. The folders do not match, so Ratchet won't trust state, integrity records or "
                "approvals under this one. Unset RATCHET_HOME (or set it back to the hooks' folder). If you moved "
                "the folder on purpose, restart your agent CLI with the new RATCHET_HOME so its hooks record it.")
    if rec.get("key_file") and not _same_path(rec["key_file"], cur["key_file"]):
        return (f"The director key file here is {cur['key_file']}, but the hooks for this project use "
                f"{rec['key_file']}. They do not match, so Ratchet won't use this director key. Unset "
                "RATCHET_DIRECTOR_KEY_FILE (or set it to the same file the agent CLI uses).")
    why = _hook_record_doubt(root) if doubt else None
    if why:
        return (f"Refused: {why}, so {_hook_env_path(root)} may be out of date (a failed write leaves the old "
                "record, which may name a director that no longer runs commands here). Ratchet won't trust it, or "
                "the per-user folder and director key file it names, so every command here is refused until that "
                f"hook runs cleanly, director commands included; only {', '.join(RECOVERY_CMDS)} still run, to show "
                "what is wrong, and accept-state, the repair a person runs at their own terminal when Ratchet names "
                "it. Run the doctor command to see the "
                "failure, make sure .ratchet/ is writable, then send any message or run any command in your agent "
                "CLI so the hook runs again.")
    return None


# Commands that only read, or only repair hooks: they still run when the hook record can't be trusted, so a user
# locked out by it can see what is wrong (doctor, status, next, view) and reinstall the hooks (install-hooks).
# accept-state (the repair Ratchet names for a file or hash record it won't trust) also runs while a hook failure is
# unresolved, so a person is never locked out of it: it needs their code typed back at the terminal, and it is still
# refused when the hook record is missing, damaged or names another per-user folder (see main).
RECOVERY_CMDS = ("doctor", "status", "next", "view", "install-hooks")
_HOOK_ENV_ADVISORY = False  # set by main for a recovery command: a hook-record problem is said, not fatal


def require_hook_env(root: Path):
    why = None if _IN_HOOK else hook_env_problem(root)
    if why and not _HOOK_ENV_ADVISORY:
        die(why)


def _markers_path() -> Path:
    return ratchet_home() / "active-runs.json"


def _restriction_record_damaged(p: Path, holds: str, repair: str):
    """update_record's `damaged` rule for a per-user record `p` that holds a restriction for other projects or lanes
    too (`holds`). Such a record is never replaced by a write that would keep only the writer's entry: that would
    drop every other entry's restriction without a word. The damaged file is left as it is (KEEP) and the person is
    told, on stderr, how to repair it (`repair`)."""
    def keep(why):
        _note_unreadable(p, why, f"it holds {holds}, so Ratchet left it as it is and did not write over it. To "
                                 f"repair it: {repair}")
        return KEEP
    return keep


def _active_runs_repair(p: Path) -> str:
    return (f"restore it from a backup or fix the JSON by hand; or, if you accept that until then deleting a "
            f"project's .ratchet/ folder is not noticed, move it aside (mv \"{p}\" \"{p}.damaged\"): each project's "
            "entry is written again when its next run starts")


def register_active(root: Path, slug: str | None):
    """Remember active runs outside the repo, so deleting .ratchet/ is noticed instead of silently ending the loop.
    A damaged active-runs.json is never written over (it holds other projects' runs): see
    _restriction_record_damaged. It is shared by every project on this machine, so it is only changed through
    update_record, under its own lock: two runs starting or ending at once never drop each other's entry. When the
    lock can't be had, nothing is written and stderr says so."""
    try:
        p = _markers_path()
        key = str(root.resolve())

        def change(data):
            if slug:
                data[key] = {"slug": slug, "since": now()}
            else:
                data.pop(key, None)
            return data
        done = update_record(p, change, pretty=True, damaged=_restriction_record_damaged(
            p, "the active runs of other projects on this machine", _active_runs_repair(p)))
        if done == "busy":
            print(f"ratchet: the per-user record of active runs ({p}) was busy (its lock "
                  f"{_record_lock_path(p)} could not be had), so "
                  + ("this run was not registered there: deleting this project's .ratchet/ folder would not be "
                     "noticed. Run `ratchet.py pause` and then `ratchet.py resume` to register it again."
                     if slug else "this project's entry was not removed; the next run that starts here replaces it."),
                  file=sys.stderr)
    except Exception:
        pass


def _active_marker(root: Path) -> dict | None:
    """This project's entry in the per-user active-runs.json ({"slug", "since"}), or None. The record does two
    things: the stop hook uses it to notice a deleted Ratchet folder (decide_stop), and Ctx.active_slug uses it to
    name the active run when active.json is damaged. A damaged file names no run for either (so it never picks a
    run), and it is never written over until a person repairs it (register_active)."""
    data = read_json(_markers_path(), {}, record="Ratchet treats it as naming no active run, and leaves it as it is "
                     "until it is repaired: " + _active_runs_repair(_markers_path())) or {}
    marker = data.get(str(root.resolve()))
    return marker if isinstance(marker, dict) and marker.get("slug") else None


# A bare "ok", "sure", "great" or thumbs-up is NOT a yes (it is often just an acknowledgement); those words are
# allowed only as fillers around a real approval ("ok, go ahead").
APPROVAL_PHRASES = [
    "approved", "approve", "approval granted", "lgtm", "looks good to me", "looks good", "looks great", "sounds good",
    "go ahead", "go for it", "good to go", "ship it", "build it", "do it", "let's go", "lets go", "proceed", "continue",
    "carry on", "confirmed", "confirm", "yes", "yep", "yeah", "yup", "yes please", "works for me", "that works",
    "that's fine", "thats fine", "fine by me", "happy with it", "happy with that", "moveon"]
BENIGN_PHRASES = [
    "no changes needed", "no changes", "no further changes", "no comments", "nothing to change", "nothing else",
    "i don't have any comments", "i don't have comments", "i dont have comments", "no notes", "all good",
    "thanks", "thank you", "thx", "please", "pls", "awesome", "nice", "cool", "for now", "from me", "as is",
    "without changes", "without any changes"]
HEDGE_WORDS = {"no", "not", "don't", "dont", "wait", "hold", "stop", "but", "however", "except", "instead", "rather",
               "maybe", "probably", "guess", "think", "hmm", "unsure", "also", "add", "change", "remove", "drop",
               "after", "before", "first", "later", "read", "sec", "second", "minute", "moment", "lunch", "brb",
               "what", "why", "how", "who", "whom", "when", "which", "where", "if", "unless", "until",
               "otherwise", "kill", "fix", "tweak", "make", "move", "rename", "replace", "update", "adjust", "swap",
               "delete", "less", "more", "bigger", "smaller", "cancel", "abandon", "abort", "scrap", "pause",
               "restart", "redo", "revert", "undo", "reset", "over", "start", "nah", "nope", "never", "disagree",
               "wrong", "bad", "hate", "dislike", "reject", "rejected", "decline", "declined"}
# Words that may surround an approval without changing its meaning. Anything else left over makes the reply unclear.
APPROVAL_FILLERS = {"it", "the", "plan", "this", "that", "all", "then", "go", "on", "ahead", "and", "a", "i", "me", "we",
                    "you", "can", "now", "ok", "okay", "sure", "great", "perfect", "right", "alright", "fine", "is",
                    "please", "so", "just", "yes", "to", "with", "let's", "lets", "that's",
                    "thats", "👍", "✅", "good", "fully", "totally", "definitely", "absolutely"}


CHOICE_RE = re.compile(r"^(?:(?:option|choice|go with|let'?s go with|let'?s do|i choose|i pick|i'?ll take|choose|pick|take)\s+)?"
                       r"(?:option\s+)?\(?([a-z])\)?(?:\s+(?:please|thanks|thank you))?$")


def choice_letter(text: str, options) -> str | None:
    """The option a reply picks, if the reply is nothing but a pick ("A", "b.", "option C", "go with A please").
    Anything more ("A, but keep the old colour", "A or B?") is not a clear choice."""
    t = unicodedata.normalize("NFKC", text or "").strip().lower().replace("\u2019", "'")
    t = re.sub(r"[.!\s]+$", "", t)
    t = re.sub(r"\s+", " ", t.replace(",", " "))
    m = CHOICE_RE.match(t)
    if not m:
        return None
    letter = m.group(1).upper()
    return letter if letter in options else None


def message_intent(text: str) -> str:
    """approve | unclear. Used only to word the prompt hook's hint; it never decides an approval (an approval needs
    the user's first reply after `ask <action>` to be a listed yes: is_listed_yes).
    A reply only reads as an approval if it is nothing BUT an approval: it must contain a real
    approval phrase ("yes", "go ahead", "approved"...), and every other word must be a filler. No question, no hedge,
    no extra request ("yes, and also add X" or "ok cancel it" is not a yes). A bare "ok" / "sure" / thumbs-up is not
    a yes either."""
    t = " " + (text or "").strip().lower().replace("’", "'") + " "
    if not t.strip() or "?" in t:
        return "unclear"
    for ph in sorted(BENIGN_PHRASES, key=len, reverse=True):
        t = t.replace(" " + ph + " ", " ").replace(" " + ph + ",", " ").replace(" " + ph + ".", " ").replace(" " + ph + "!", " ")
    t = re.sub(r"\b(let's |lets )?move on\b", " moveon ", t)  # "move" alone asks for a change; "move on" is a go-ahead
    # Only plain letters, digits and ordinary punctuation are understood. Anything else (another script, another
    # emoji, full-width letters, a full-width question mark) could carry a "no" we can't read, so it is unclear.
    residue = re.sub("[a-z0-9'\\s.,!;:()\"\\-\u2014\u2013\u2026\ufe0f\U0001F3FB-\U0001F3FF\U0001F44D\u2705]", "", t)
    if residue:
        return "unclear"
    words = re.findall("[a-z0-9']+|\U0001F44D|\u2705", t)
    if any(w in HEDGE_WORDS for w in words):
        return "unclear"
    rest = " " + " ".join(words) + " "
    found = False
    for ph in sorted(APPROVAL_PHRASES, key=len, reverse=True):
        if " " + ph + " " in rest:
            found = True
            rest = rest.replace(" " + ph + " ", " ")
    leftover = [w for w in rest.split() if w not in APPROVAL_FILLERS]
    return "approve" if found and not leftover else "unclear"


# The ONLY replies that waive hooks (compared after normalising: lowercase, punctuation and extra spaces dropped,
# a trailing "please" dropped). Each one says what is being waived. Anything else is not consent, however it is worded.
HOOKS_WAIVE_PHRASES = frozenset({
    "yes go ahead without hooks", "yes skip the hooks", "yes build without hooks",
    "go ahead without hooks", "skip the hooks", "proceed without hooks",
})
HOOKS_WAIVE_REPLY = "yes, go ahead without hooks"


def _norm_reply(text: str) -> str:
    """NFKC + casefold, then drop only ASCII punctuation (except '?') and extra spaces and a trailing "please".
    Anything else (non-ASCII letters, emoji, CJK, '?') is kept, so it can never equal an allow-listed phrase."""
    t = unicodedata.normalize("NFKC", text or "").casefold().replace("\u2019", "'").replace("'", "")
    t = re.sub(r"[!-/:->@-`{-~\[-\]]+", " ", t)   # ASCII punctuation; '?' (0x3f) is deliberately not in this set
    t = " ".join(t.split())
    return re.sub(r"( please)+$", "", t).strip()


def hooks_waive_intent(text: str) -> str:
    """Strict allow-list for the hooks question: approve only when the whole reply is a listed waiver phrase."""
    return "approve" if _norm_reply(text) in HOOKS_WAIVE_PHRASES else "unclear"


# The ONLY replies that say yes to a question Ratchet asked (`ask <action>`): plan approval, a step or final review,
# unblocking, cancel, set-review, approve-changes, claim and ui-respec. Case, commas, extra spaces and a trailing "."
# or "!" are ignored; nothing else may be attached. Only the user's FIRST reply after the question counts.
APPROVAL_REPLIES = ("yes", "yes, go ahead", "go ahead", "approved")


def _norm_yes(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").casefold().strip()
    t = re.sub(r"[.!]+$", "", t).replace(",", " ")
    return " ".join(t.split())


def is_listed_yes(text: str) -> bool:
    return _norm_yes(text) in {_norm_yes(p) for p in APPROVAL_REPLIES}


def listed_yes_text() -> str:
    """The accepted replies, quoted, for the printed question and the refusals."""
    q = [f'"{p}"' for p in APPROVAL_REPLIES]
    return ", ".join(q[:-1]) + " or " + q[-1]


CANCEL_HINT_RE = re.compile(r"\b(cancel|abandon|scrap|kill)\b[^.?!]*\b(run|build|feature|ratchet|loop|work|whole thing|everything)\b"
                            r"|\bforget (it|the whole thing)\b|\bstart over\b", re.I)


RULE_KEYS = ["review_depth", "reviewers", "max_adversarial_rounds", "max_adversarial_rounds_run", "max_ui_rounds",
             "max_behavior_attempts", "ui_block_on_nits", "ui_globs", "regression_command", "standards",
             "fingerprint_include", "approver", "ui_single_reviewer_ok", "min_review_timeout_sec", "test_timeout_sec",
             "host_provider", "ui_reviewers", "design_md", "max_stop_blocks", "unavailable_ttl_hours",
             "verify_transcripts", "min_review_seconds", "commit_per_checkpoint", "blocking_severities",
             "scope_later_rounds", "host_models", "role_models", "checks", "checkin_round"]


def _file_sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def _reference_files(ctx) -> dict:
    """Files that define what 'correct' means: the standards reviewers enforce, the design system, the prototype."""
    cfg = ctx.config()
    files = {f: ctx.root / f for f in cfg.get("standards") or []}
    files[cfg.get("design_md") or "DESIGN.md"] = ctx.root / (cfg.get("design_md") or "DESIGN.md")
    try:
        files["reference/prototype.html"] = ctx.run_dir() / "reference" / "prototype.html"
    except SystemExit:
        pass
    return {k: _file_sha(v) for k, v in files.items()}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _custom_providers_sha(cfg) -> str:
    """Reviewers that run an arbitrary command are trusted code: adding or changing one after approval needs the
    user's yes. Preset tools (and their model choice) are just availability and stay free to add."""
    rows = sorted((pv["id"], json.dumps(pv.get("command")), bool(pv.get("stdin")), norm_company(pv.get("company")))
                  for pv in providers(cfg) if pv.get("command"))
    return _sha(rows)


def approval_snapshot(ctx, st) -> dict:
    plan = ctx.plan() or {"checkpoints": []}
    cfg = ctx.config()
    return {"plan": _plan_hash(plan, ctx),
            "objective": plan.get("objective"),
            "cps": {c["id"]: _sha(c) for c in plan["checkpoints"]},
            "tests": {c["id"]: _sha(test_plan(ctx, c["id"])) for c in plan["checkpoints"]},
            "rules": {k: _sha(cfg.get(k)) for k in RULE_KEYS},
            "files": _reference_files(ctx),
            "custom_providers": _custom_providers_sha(cfg),
            "spec": _spec_snapshot(ctx),
            "host": detect_host(cfg),
            "human_review": st.get("human_review")}


def integrity_problems(ctx, st) -> list[str]:
    """What changed since the human approved the plan (plan, test plans, or the loop's rules)."""
    snap = st.get("approved")
    if not snap:
        return []
    now_ = approval_snapshot(ctx, st)
    out = []
    for cid, h in snap.get("cps", {}).items():
        if now_["cps"].get(cid) != h:
            out.append(f"checkpoint {cid} in checkpoints.json")
    for cid, h in snap.get("tests", {}).items():
        if now_["tests"].get(cid) != h:
            out.append(f"test plan tests/{cid}.json")
    extra = set(now_["cps"]) - set(snap.get("cps", {}))
    if extra:
        out.append(f"new checkpoints {sorted(extra)} (use feedback + plan-extend)")
    user = read_json(ctx.config_path, {}) or {}
    for k, h in snap.get("rules", {}).items():
        if now_["rules"].get(k) != h:
            old_defaults = [_sha(v) for v in SHIPPED_DEFAULT_HISTORY.get(k, [])]
            if h in old_defaults and (k not in user or _sha(user.get(k)) in old_defaults):
                continue  # only Ratchet's shipped default changed (an update), not anything the user or agent set
            out.append(f"config rule '{k}'")
    for f, h in snap.get("files", {}).items():
        if now_["files"].get(f) != h:
            out.append(f"reference file {f}")
    if "custom_providers" in snap and snap["custom_providers"] != now_["custom_providers"]:
        out.append("custom review commands in config 'providers' (a reviewer defined by its own command, or its "
                   "company, was added or changed)")
    if snap.get("spec") and snap["spec"] != now_.get("spec"):
        out.append("the spec this run was built from (" + ", ".join(
            f"{r['tool']} {r['id']}" for r in (spec_source(ctx) or {}).get("items") or []) + ")")
    out += weakened_tests(ctx, st)
    if snap.get("host") and snap["host"] != now_["host"]:
        out.append(f"host provider ({snap['host']} → {now_['host']})")
    if snap.get("human_review") != st.get("human_review"):
        out.append("human_review mode")
    return out


def verify_integrity(ctx, st):
    probs = integrity_problems(ctx, st)
    if probs:
        die("These changed after the user approved the plan: " + "; ".join(probs) + ". "
            "Either undo the change, or show the user exactly what changed and — only after they confirm — "
            "run `ratchet.py approve-changes`.")


# --------------------------------------------------------------------------- plan validation

OBJECTIVE_EXAMPLE = ('{"users": "who will use the result", "goal": "what they are trying to do", '
                      '"done_means": ["what done means to them, one line per point"], '
                      '"out_of_scope": ["what this run deliberately will not build or chase"]}')


def objective_problems(plan) -> list[str]:
    """What is missing from the plan's top-level 'objective' (who it is for, what they want, what done means,
    what is out of scope). Checked at plan-submit, not in validate_plan (shared with plan-extend, which instead
    refuses a feedback plan that drops an objective the approved plan had)."""
    obj = plan.get("objective") if isinstance(plan, dict) else None
    if not isinstance(obj, dict) or not obj:
        return [f"objective: missing. Every plan needs one, e.g. {OBJECTIVE_EXAMPLE}"]
    errs = []
    if not str(obj.get("users") or "").strip():
        errs.append("objective.users: missing or empty (who will use the result)")
    if not str(obj.get("goal") or "").strip():
        errs.append("objective.goal: missing or empty (what they are trying to do)")
    dm = obj.get("done_means")
    if not isinstance(dm, list) or not any(str(x or "").strip() for x in dm):
        errs.append("objective.done_means: missing or empty (what done means to them, as a non-empty list)")
    oos = obj.get("out_of_scope")
    if not isinstance(oos, list) or not any(str(x or "").strip() for x in oos):
        errs.append("objective.out_of_scope: missing or empty (what this run deliberately will not build or "
                    "chase, as a non-empty list)")
    errs += _objective_fill_ins(obj)
    return errs


NO_OBJECTIVE_NOTE = "no objective was stated for this run (an older plan, approved before this feature existed)"


def objective_brief(plan) -> str:
    """Plain text of the plan's objective, for the top of every reviewer prompt and every sub-agent's brief: who
    the result is for, what they want, what done means, what is out of scope. A plan with no objective at all
    (a run approved before CP1's objective_problems() existed) gets a plain note instead of a crash, so a legacy
    run keeps reviewing normally."""
    obj = plan.get("objective") if isinstance(plan, dict) else None
    if not isinstance(obj, dict) or not obj:
        return f"Objective: {NO_OBJECTIVE_NOTE}."
    lines = ["Objective", f"Who it's for: {obj.get('users', '')}", f"What they want: {obj.get('goal', '')}"]
    dm = obj.get("done_means") or []
    if dm:
        lines.append("Done means:")
        lines += [f"  - {x}" for x in dm]
    oos = obj.get("out_of_scope") or []
    if oos:
        lines.append("Out of scope:")
        lines += [f"  - {x}" for x in oos]
    return "\n".join(lines)


def _final_review_lines(plan) -> list[str]:
    """What `status` (and `status --json`'s own fields, read by the human-approve question) opens the final review
    with: the objective's goal and users, then every done_means line, plain, for the user to judge against the
    evidence themselves. Completing every checkpoint never proves a done_means line is actually met (simplifying a
    step, or the user accepting a finding, can leave one unmet), so no line here claims met or not met; `status`
    prints the findings accepted along the way (`_accepted_findings_lines`) and anything left for later right
    after, so the user judges each line with that context, not a bare claim. A plan with no stated objective
    (approved before this existed) gets a plain note instead of a crash."""
    obj = plan.get("objective") if isinstance(plan, dict) else None
    if not isinstance(obj, dict) or not obj:
        return [f"Objective: {NO_OBJECTIVE_NOTE}."]
    lines = [f"Objective: {obj.get('goal', '')}", f"Who it's for: {obj.get('users', '')}",
              "Done means (check each against what was actually built and the findings below; finishing the "
              "checkpoints does not by itself mean these are met):"]
    for dm in obj.get("done_means") or []:
        lines.append(f"  - check: {dm}")
    return lines


def _objective_fill_ins(obj: dict) -> list[str]:
    """Every objective part that still starts with the spec draft's placeholder (specsrc.FILL_IN_PREFIX,
    e.g. 'FILL IN: who will use this?') instead of a real answer, named so the planner or user must fill it in
    before the plan can be submitted. A draft never guesses an audience from a story's title or invents one;
    this is what catches it if one slips through unfilled."""
    out = []
    for key in ("users", "goal"):
        v = str(obj.get(key) or "")
        if v.startswith(specsrc.FILL_IN_PREFIX):
            out.append(f"objective.{key}: still the draft placeholder ({v!r}) — fill it in before submitting")
    for key in ("done_means", "out_of_scope"):
        v = obj.get(key)
        if not isinstance(v, list):
            continue  # objective_problems already reports the invalid shape
        for i, x in enumerate(v):
            if str(x or "").startswith(specsrc.FILL_IN_PREFIX):
                out.append(f"objective.{key}[{i}]: still the draft placeholder ({x!r}) — fill it in before submitting")
    return out


REQUIRED_CP_FIELDS = ["id", "title", "summary", "done_when", "gates"]


def validate_plan(plan: dict) -> list[str]:
    errs = []
    if not isinstance(plan, dict):
        return ["checkpoints.json must be an object"]
    cps = plan.get("checkpoints")
    if not isinstance(cps, list) or not cps:
        return ["checkpoints.json needs a non-empty 'checkpoints' array"]
    seen = set()
    for i, cp in enumerate(cps):
        tag = cp.get("id", f"#{i}")
        for f in REQUIRED_CP_FIELDS:
            if f not in cp:
                errs.append(f"{tag}: missing '{f}'")
        if cp.get("id") in seen:
            errs.append(f"{tag}: duplicate id")
        seen.add(cp.get("id"))
        if not re.fullmatch(r"CP\d+", str(cp.get("id", ""))):
            errs.append(f"{tag}: id must look like CP1, CP2, ...")
        title = str(cp.get("title", ""))
        if len(title.split()) > 8:
            errs.append(f"{tag}: title should be a few words (≤8), got {len(title.split())}")
        g = cp.get("gates", {})
        if not isinstance(g, dict):
            errs.append(f"{tag}: gates must be an object")
            continue
        if g.get("adversarial") is not True:
            errs.append(f"{tag}: adversarial gate cannot be skipped")
        if not g.get("behavior") and not g.get("ui"):
            errs.append(f"{tag}: at least one of behavior/ui gates must be on")
        if g.get("behavior") is False and not cp.get("behavior_skip_reason"):
            errs.append(f"{tag}: behavior gate off requires 'behavior_skip_reason'")
        if g.get("ui"):
            if not cp.get("ui_scope"):
                errs.append(f"{tag}: ui gate on requires 'ui_scope' (what the reviewer may judge)")
            if not cp.get("ui_states"):
                errs.append(f"{tag}: ui gate on requires at least one entry in 'ui_states'")
        if g.get("behavior"):
            t = cp.get("tests") or {}
            if not t.get("command"):
                errs.append(f"{tag}: behavior gate on requires tests.command")
            if "report" in t and t["report"] not in ("junit", "json", "none"):
                errs.append(f"{tag}: tests.report must be junit, json or none")
            if t.get("report") == "none" and not str(t.get("report_reason") or "").strip():
                errs.append(f"{tag}: tests.report none requires tests.report_reason")
        for dep in cp.get("depends_on", []) or []:
            if dep not in seen:
                errs.append(f"{tag}: depends_on {dep} must refer to an EARLIER checkpoint")
        if cp.get("risk", "normal") not in ("low", "normal", "high"):
            errs.append(f"{tag}: risk must be low, normal or high")
        if cp.get("reviewers") is not None and not (isinstance(cp["reviewers"], list) and cp["reviewers"]):
            errs.append(f"{tag}: reviewers, if set, must be a non-empty list of review slots "
                        "(an id, or a list of ids in order of preference)")
        if not isinstance(cp.get("done_when"), list) or not cp.get("done_when"):
            errs.append(f"{tag}: done_when must be a non-empty list of plain-language criteria")
    return errs


def cp_by_id(plan, cp_id):
    for cp in plan.get("checkpoints", []):
        if cp["id"] == cp_id:
            return cp
    die(f"Unknown checkpoint {cp_id}")


def test_plan(ctx: Ctx, cp_id: str):
    return read_json(ctx.run_dir() / "tests" / f"{cp_id}.json")


def new_cp_state(cp):
    g = cp.get("gates", {})
    gates = {}
    for name in ["behavior", "ui", "adversarial"]:
        gates[name] = {"required": bool(g.get(name)), "status": "pending" if g.get(name) else "skipped",
                       "attempts": 0}
    gates["human"] = {"required": False, "status": "pending", "attempts": 0}
    return {"status": "pending", "step": "tests", "gates": gates, "started_at": None,
            "completed_at": None, "commit": None}


# --------------------------------------------------------------------------- next action

def next_action(ctx: Ctx, st=None, compute_accept_note: bool = True) -> dict:
    """`compute_accept_note=False` skips `_blocked_accept_note` (and the working-tree fingerprint it can trigger
    via `_code_changed_since_round`) for callers that never read `accept_note` from the result: the Stop hook
    (`decide_stop`, which only reads `stop_ok`) and the viewer (`viewer_data`, whose `next` the UI never shows
    `accept_note` from). `ratchet.py next` and `status --json`, the only places this line is ever printed, always
    pass the default so their behavior is unchanged."""
    st = st or ctx.state()
    phase = st["phase"]
    if phase == "planning":
        missing = []
        if not (ctx.run_dir() / "app-scout.json").exists():
            missing.append("app scout report (.ratchet/runs/<slug>/app-scout.json) from ratchet-app-scout")
        if not (ctx.run_dir() / "checkpoints.json").exists():
            missing.append("checkpoints.json from ratchet-checkpoint-planner")
        if missing:
            return {"phase": phase, "action": "Spawn in parallel: " + "; ".join(missing),
                    "stop_ok": False}
        missing_tp = [c["id"] for c in (ctx.plan() or {}).get("checkpoints", [])
                      if c.get("gates", {}).get("behavior") and not test_plan(ctx, c["id"])]
        if missing_tp:
            return {"phase": phase, "action": "Spawn ratchet-test-planner for " + ", ".join(missing_tp)
                    + " (in parallel). When every test plan exists, run ratchet.py plan-submit.", "stop_ok": False}
        return {"phase": phase, "action": "Run ratchet.py plan-submit (validates the plan and test plans, "
                "prints the viewer link), then show the user the plan.", "stop_ok": False}
    if phase == "awaiting_plan_approval":
        missing = [c["id"] for c in ctx.plan()["checkpoints"]
                   if c["gates"].get("behavior") and not test_plan(ctx, c["id"])]
        extra = (f" (test plans still missing for {', '.join(missing)} — spawn ratchet-test-planner, then run "
                 "ratchet.py plan-submit again)") if missing else ""
        asked = (st.get("pending") or {}).get("action") == "approve-plan"
        how = ("The approval question is printed: wait for the user's reply. Run ratchet.py approve-plan only if "
               "their first reply after it is one of the listed yes replies; otherwise answer them or make their "
               "change, then print the question again (ratchet.py ask approve-plan)." if asked else
               "Waiting for the user to approve the plan. " + _after_next(PLAN_ASK_NEXT))
        return {"phase": phase, "action": how + extra, "stop_ok": not missing}
    if phase in ("awaiting_checkpoint_review", "awaiting_final_review"):
        asked = (st.get("pending") or {}).get("action") == "human-approve"
        how = ("The review question is printed: wait for the user's reply. Run ratchet.py human-approve only if "
               "their first reply after it is one of the listed yes replies; if they ask for changes, run "
               "ratchet.py feedback." if asked else "Waiting for the human to review. " + REVIEW_ASK_NEXT)
        return {"phase": phase, "action": how, "stop_ok": True}
    if phase == "planning_feedback":
        return {"phase": phase, "action": "Spawn ratchet-checkpoint-planner in EXTEND mode with the human's feedback "
                "(append new checkpoints, never edit completed ones), ratchet-test-planner for each new checkpoint, "
                "then run ratchet.py plan-extend", "stop_ok": False}
    if phase in ("blocked", "paused", "done", "cancelled"):
        na = {"phase": phase, "action": st.get("blocked_reason") or phase, "stop_ok": True}
        if phase == "blocked":
            na["options"] = blocked_options(st.get("blocked_reason"))
            na["reason_plain"] = plain_reason(st.get("blocked_reason"))
            note = _blocked_accept_note(ctx, st) if compute_accept_note else None
            if note:
                na["accept_note"] = note
        return na

    # building
    cur = st.get("current")
    if not cur:
        return {"phase": phase, "action": "No current checkpoint — run ratchet.py status", "stop_ok": False}
    cps = st["checkpoints"][cur]
    step = cps["step"]
    plan = ctx.plan()
    cp = cp_by_id(plan, cur)
    msgs = {
        "tests": f"{cur}: spawn ratchet-test-writer to write the planned tests (tagged [{cur}-Txx]) "
                 f"into {', '.join(cp.get('tests', {}).get('files', []) or ['the files in tests.files'])}, "
                 f"then run: ratchet.py tests-written {cur}",
        "red": f"{cur}: run ratchet.py red {cur}  (tests must FAIL before implementation)",
        "implement": f"{cur}: spawn ratchet-implementer for this checkpoint only, then run: ratchet.py implemented {cur}",
        "behavior": f"{cur}: run ratchet.py behavior {cur}. If it fails, spawn ratchet-implementer with the failure log, then re-run.",
        "ui": f"{cur}: run the ratchet-ui-compare skill (capture matched states → 2 isolated reviewers → combined.json), "
              f"then ratchet.py ui-record {cur} --review <combined.json>",
        "adversarial": f"{cur}: run the ratchet-adversarial-loop skill (adversary review → ratchet.py adv-record → fixer → repeat)",
        "complete": f"{cur}: run ratchet.py complete {cur}",
    }
    gr = cp.get("guardrail") or {}
    if step == "tests" and gr.get("high_risk") and not gr.get("strengthen"):
        msgs["tests"] = (f"{cur}: first spawn ratchet-variant-writer with ONLY the kind '{gr['kind']}', its why and the "
                         f"output of `ratchet.py occurrences {gr['kind']}` (never the check) to write disguised bad "
                         f"variants in {gr['variants_dir']}/bad and look-alike good code in {gr['variants_dir']}/good; "
                         "then " + msgs["tests"].split(": ", 1)[1])
    na = {"phase": phase, "checkpoint": cur, "step": step, "action": msgs.get(step, step), "stop_ok": False}
    reps = repeat_candidates(ctx, st) if approval_mode(ctx) != "director" else []
    if reps:
        na["guardrail_candidates"] = reps
        na["action"] = (f"First offer the user a guardrail for the repeated finding '{reps[0]['kind']}' (ratchet.py ask "
                        f"choice --guardrail ...; see the orchestrator skill). Then: " + na["action"])
    return na


# --------------------------------------------------------------------------- plain language

STEP_PLAIN = {"tests": "writing the tests", "red": "checking the new tests fail before any code exists",
              "implement": "building it", "behavior": "running the tests", "ui": "checking how it looks",
              "adversarial": "getting a second opinion", "complete": "wrapping up"}


PHASE_PLAIN = {"planning": "planning", "awaiting_plan_approval": "waiting for your approval of the plan",
               "building": "building", "awaiting_checkpoint_review": "waiting for your review",
               "awaiting_final_review": "waiting for your final review", "planning_feedback": "updating the plan",
               "blocked": "stuck, needs your decision", "paused": "paused", "done": "done", "cancelled": "cancelled"}


def plain_reason(reason):
    """The block reason in plain words for the user. Unknown reasons pass through unchanged; the raw text stays
    in `status --json` (blocked_reason)."""
    if not reason:
        return reason
    m = re.match(r"(CP\d+): behavior gate failed (\d+) times", reason)
    if m:
        return f"The tests for {m.group(1)} still fail after {m.group(2)} tries."
    m = re.match(r"(CP\d+): UI gate failed (\d+) rounds", reason)
    if m:
        return f"The look-and-feel check for {m.group(1)} still is not passing after {m.group(2)} rounds."
    m = re.match(r"(CP\d+): adversarial review still has (\d+) finding\(s\) after (\d+) rounds", reason)
    if m:
        return (f"The reviewers still have {m.group(2)} open concern(s) about {m.group(1)} after "
                f"{m.group(3)} rounds of fixes, which is the limit.")
    m = re.match(r"(CP\d+): (\d+) adversarial rounds used without approval", reason)
    if m:
        return f"The reviewers have not signed off on {m.group(1)} after {m.group(2)} rounds, which is the limit."
    m = re.match(r"Run-wide adversarial round cap reached \((\d+)/(\d+)\)", reason)
    if m:
        return f"The reviewers have used all the review rounds allowed for this run ({m.group(1)} of {m.group(2)})."
    if reason.startswith("The agent tried to stop"):
        return "The work kept stopping without making progress, so it was halted for you to look at."
    return reason


def blocked_options(reason) -> list:
    """2-3 lettered choices for the user, fitted to why the run is blocked. Offered by the agent, not typed.
    Only offers things that can work: raising a review limit is a user-confirmed config change. Accepting an
    open finding is never one of these: it is never a lettered pick (so a pick followed by `human-approve` can
    never stand in for the user's own `accept: <reason>` reply). When that is a real option here, `next`/`status`
    add it as its own plain line instead (see `_blocked_accept_note`)."""
    r = (reason or "").lower()
    if "round cap" in r or "run-wide" in r:
        texts = ["Raise the review limit so the reviewers can keep going.",
                 "Stop here and keep what is already finished."]
    elif "rounds used" in r or ("adversarial" in r and re.search(r"after \d+ rounds", r)):
        texts = ["Raise the review limit so the reviewers can keep going.",
                 "Change the step so the review notes no longer apply.",
                 "Stop here and keep what is already finished."]
    elif "behavior gate failed" in r:
        texts = ["Try again with a different approach.", "Change or revise this step (its tests or its scope).",
                 "Stop here and keep what is already finished."]
    elif "adversarial" in r or "review" in r:
        texts = ["Change the step so the review notes no longer apply.",
                 "Stop here and keep what is already finished."]
    else:
        texts = ["Try again.", "Change the plan for this step.", "Stop here and keep what is already finished."]
    return [{"letter": "ABC"[i], "text": t} for i, t in enumerate(texts)]


ACCEPT_NOTE_CACHE_NAME = ".accept-note-cache.json"  # see _cached_code_changed_since_round: a performance cache only


def _blocked_accept_note(ctx, st) -> str | None:
    """One plain line (never a lettered option) for `next`/`status` when the run is blocked on an adversarial
    round limit, per-checkpoint or run-wide, and the current checkpoint's last recorded round still has open
    findings: either the user can accept them with their own reason (`ask accept-finding` + their own
    `accept: <reason>` reply, never a lettered pick or `human-approve`, which never accepts anything), or, when
    the code changed since that round, those findings need a fresh review round instead (point at raising the
    limit, option A). None when the block is some other kind, there is no current checkpoint, no round was ever
    recorded for it, or the recorded round simply has nothing open (an approved round, or one already fully
    accepted): a presence check against the round's own open findings always runs FIRST, before the code-change
    check, so neither line is ever printed about findings that do not exist."""
    if st.get("phase") != "blocked":
        return None
    cur = st.get("current")
    cps = (st.get("checkpoints") or {}).get(cur) if cur else None
    if not cur or cps is None or not _adv_round_block_reason_for(cur, st.get("blocked_reason")):
        return None
    rnd = cps.get("adv_round")
    if not rnd or not _adv_open_findings(ctx, cur, rnd, cps):
        return None
    if _cached_code_changed_since_round(ctx, st, rnd):
        return ("The last fix changed the code, so these findings need a fresh look; raise the limit (A) for one "
                "more round.")
    return ("You can also accept open findings yourself: the agent asks with `ask accept-finding`, and only "
            "your reply `accept: <reason>` closes them.")


def _cached_code_changed_since_round(ctx, st, rnd) -> bool:
    """Same answer as `_code_changed_since_round`, but skips its working-tree fingerprint (a `git add -A` into a
    fresh temp index, re-hashing every file) when nothing that could change it has happened since the last time
    THIS exact round was checked. Only `_blocked_accept_note` calls this: while a run sits `blocked` waiting on
    the user, the Stop hook re-runs `next_action` on every turn end, each a fresh process with no memory of the
    last one, so without this the fingerprint was being recomputed on every single stop. The cache key is the
    round's own number and fingerprint plus state.json's own mtime: every command that could add a new round or
    change the code also saves state (standards #12, the one atomic write), so a cache entry still matching all
    three was computed after the same state.json this call is reading, and the working tree cannot have changed
    code Ratchet would recognise since then without some command also updating one of those three. A mismatch
    (a new/replaced round, or state.json touched since) always recomputes rather than trust a stale entry; a
    write race between two callers only costs an extra recompute, never a wrong answer. `ask accept-finding` and
    `accept-finding` never call this cached wrapper: they call `_code_changed_since_round` directly, uncached,
    for their own authoritative, must-be-fresh refusal check."""
    slug = st.get("slug")
    try:
        state_mtime = ctx.state_path(slug).stat().st_mtime
    except OSError:
        state_mtime = None
    key = {"round": rnd.get("round"), "fingerprint": rnd.get("fingerprint"), "state_mtime": state_mtime}
    cache_path = ctx.run_dir(slug) / ACCEPT_NOTE_CACHE_NAME
    cached, why = _read_record(cache_path, missing=None)
    if why is None and isinstance(cached, dict) and all(cached.get(k) == v for k, v in key.items()):
        return bool(cached.get("changed"))
    changed = _code_changed_since_round(ctx, rnd)
    try:
        _write_record(cache_path, dict(key, changed=changed))
    except OSError:
        pass  # a performance cache only: a failed write just means the next call recomputes too
    return changed


def plain_now(ctx: Ctx, st, plan=None) -> str:
    """One plain sentence about what is happening now (no phase ids, no commands)."""
    phase = st.get("phase")
    plan = plan if plan is not None else (ctx.plan() or {})
    titles = {c["id"]: c.get("title", c["id"]) for c in plan.get("checkpoints", [])}
    fixed = {
        "planning": "Planning: splitting the feature into steps.",
        "awaiting_plan_approval": "The plan is ready and waiting for your approval. Say yes to start building, "
                                  "or tell me what to change.",
        "awaiting_final_review": "Everything is built. Please look it over, then approve or tell me what should change.",
        "planning_feedback": "Turning your feedback into new steps.",
        "paused": "The work is paused until you say to continue.",
        "cancelled": "This run was cancelled.",
        "done": "Finished.",
        "blocked": "The work is stuck and needs your decision.",
    }
    if phase in fixed:
        return fixed[phase]
    if phase == "awaiting_checkpoint_review":
        t = titles.get(st.get("review_target"), "the last step")
        return f"\"{t}\" is ready for you to review. Try it, then approve or tell me what should change."
    cur = st.get("current")
    cps = st.get("checkpoints") or {}
    if not cur or cur not in cps:
        return "Getting the next step ready."
    n = list(cps).index(cur) + 1
    return (f"Working on step {n} of {len(cps)}, \"{titles.get(cur, cur)}\": "
            + STEP_PLAIN.get(cps[cur].get("step"), "in progress") + ".")


HOOK_EVENT_PLAIN = {"guard": "the check that runs before each edit or command",
                    "stop": "the check that runs when the agent finishes a turn",
                    "prompt": "the hook that records your replies"}


def _plain_ago(at) -> str:
    t = _parse_iso(at)
    secs = (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() if t else 0
    if secs < 60:
        return "less than a minute ago"
    if secs < 3600:
        n = int(secs // 60)
        return f"{n} minute{'s' if n != 1 else ''} ago"
    n = int(secs // 3600)
    return f"{n} hour{'s' if n != 1 else ''} ago"


def _plain_hook_tail(event: str) -> str:
    return ("so Ratchet's checks are not enforced right now" if event in ("guard", "stop")
            else "so your replies may not be recorded")


def plain_hook_errors(root: Path, host: str) -> list:
    """Plain lines for `status` about hook crashes: the last 24 hours of the log, plus every hook that has not run
    cleanly since its last crash, each named by its own event and crash time (none when there are none). Never shows
    the raw error text: that is in `status --json` / `next` (`hook_errors`) and `doctor` for the agent."""
    errs = read_hook_errors(root, host)
    broken = broken_hooks(root, host, errors=errs)  # newest first, one entry per (host, event)
    ask = "Ask the agent to run the doctor check and fix it."
    lines, last, others, asked = [], None, broken, False
    if errs:
        last = errs[0]
        what = HOOK_EVENT_PLAIN.get(last["event"], "one of its checks")
        if len(errs) == 1:
            lines.append(f"A Ratchet safety hook failed {_plain_ago(last['at'])} ({what}), so it let that action "
                         "through without checking it.")
        else:
            lines.append(f"Ratchet safety hooks failed {len(errs)} times in the last day, most recently "
                         f"{_plain_ago(last['at'])} ({what}), so those actions went through without being checked.")
        last_broken = any((b["host"], b["event"]) == (last["host"], last["event"]) for b in broken)
        others = [b for b in broken if (b["host"], b["event"]) != (last["host"], last["event"])
                  and not (last_broken and b["event"] == last["event"])]
        if last_broken:
            lines.append(f"It has not run cleanly since, {_plain_hook_tail(last['event'])}. {ask}")
            asked = True
        elif not others:
            lines.append("It has run cleanly since then. If it happens again, ask the agent to run the doctor check.")
        else:
            lines.append("It has run cleanly since then.")
    seen = set()
    for b in others:  # a different hook that is still broken: name it, and its own crash time
        if b["event"] in seen:
            continue  # the same check on another host (unknown-host view): one line is enough
        seen.add(b["event"])
        lines.append(f"{'Separately, a' if last else 'A'} Ratchet safety hook failed {_plain_ago(b['at'])} "
                     f"({HOOK_EVENT_PLAIN.get(b['event'], 'one of its checks')}) and has not run cleanly since, "
                     f"{_plain_hook_tail(b['event'])}.")
    if seen and not asked:
        lines[-1] += " " + ask
    if hook_crash_state_unreadable(root):
        lines.append("Ratchet cannot read its record of safety hook failures, so it counts its checks as not "
                     "enforced right now. " + ask)
    elif hook_crash_state_problem(root):
        lines.append("Ratchet cannot save its record of safety hook failures, so a failed check could go unnoticed "
                     "and it counts its checks as not enforced right now. " + ask)
    return lines


def exit_code_only_line(cp):
    """A plain line when a step's tests are checked by exit code only: its plan says it writes no per-test report
    (`tests.report` none), or the plan does not say (no `report` key, e.g. a plan from before reports existed)."""
    t = (cp or {}).get("tests") or {}
    if not ((cp or {}).get("gates") or {}).get("behavior") or t.get("report") in ("junit", "json"):
        return None
    if t.get("report") == "none":
        return (f"{cp['id']} has no per-test report, so its tests are checked by exit code only. "
                f"Reason: {t.get('report_reason')}")
    return (f"{cp['id']}'s plan does not say how its tests report each result, so they are checked by exit code "
            "only.")


def exit_code_only_note(plan, st):
    """exit_code_only_line for the step being built now, or None."""
    if st.get("phase") != "building":
        return None
    return exit_code_only_line(next((c for c in (plan or {}).get("checkpoints") or []
                                     if c.get("id") == st.get("current")), None))


def cmd_status_text(ctx: Ctx):
    st = ctx.state()
    plan = ctx.plan() or {"checkpoints": []}
    if st.get("phase") == "awaiting_final_review":
        for line in _final_review_lines(plan):
            print(line)
        left = [f for c in (st.get("checkpoints") or {}).values() for f in c.get("deferred") or []
                if not f.get("accepted")]
        if left:
            print(f"{len(left)} finding(s) were left for later (never accepted or fixed): run `ratchet.py "
                  "deferred` to see them.")
        print("")
    cps = plan.get("checkpoints", [])
    total = len(cps)
    done = [c for c in cps if (st["checkpoints"].get(c["id"]) or {}).get("status") == "done"]
    if not total:
        print("No plan yet.")
    elif len(done) == total:
        print(f"All {total} steps are done.")
    elif done:
        print(f"Step {len(done)} of {total} done: {done[-1].get('title', done[-1]['id'])}")
    else:
        print(f"Step 0 of {total} done. Not started yet.")
    print(f"Run: {st['slug']}  -  {st['feature']}")
    print(f"Phase: {PHASE_PLAIN.get(st.get('phase'), st.get('phase'))}")
    print(plain_now(ctx, st, plan))
    cur = (st.get("checkpoints") or {}).get(st.get("current")) or {}
    if st.get("phase") == "building" and any(g.get("status") in ("stale", "invalid")
                                             for g in (cur.get("gates") or {}).values()):
        print("The code changed since the checks last passed, so they must be re-run.")
    ci_open = (cur.get("checkin") or {}).get("open")
    if ci_open:
        print(f"A check-in is open for {st.get('current')} (round {ci_open.get('round')}): run "
              f"`ratchet.py check-in {st.get('current')}` to see it, then `--decision simplify|stop` to resolve it.")
    note = exit_code_only_note(plan, st)
    if note:
        print(note)
    for line in _accepted_findings_lines(st):
        print(line)
    w = live_wait(st)
    if w:
        print(f"Waiting on: {w['reason']} (until {w['until'][:16].replace('T', ' ')} UTC).")
    for cid, c in (st.get("checkpoints") or {}).items():
        for gate, what in (("ui", "UI review"), ("adversarial", "review")):
            for line in ((c.get("gates") or {}).get(gate) or {}).get("unconfirmed") or []:
                print(f"{cid} {what} {line}.")
    if st.get("phase") == "blocked":
        print(f"Why: \"{plain_reason(st.get('blocked_reason')) or 'no reason was recorded'}\"")
        print("What would you like to do?")
        for o in blocked_options(st.get("blocked_reason")):
            print(f"  {o['letter']}) {o['text']}")
        note = _blocked_accept_note(ctx, st)
        if note:
            print(note)
    hs = hooks_view(ctx, st)
    if hs.get("waived"):
        print("Hooks were skipped at your request, so the checks are only advice.")
    for line in plain_hook_errors(ctx.root, hs.get("host", "unknown")):
        print(line)
    url = viewer_link(ctx)["url"]
    if url:
        print(f"Live view: {url}")


PLAIN_LENS = {"both": "code standards and bugs", "standards": "code standards", "correctness": "bugs and security"}
PLAIN_MODEL = {"inherit": "your session's model"}


def _plain_model(m) -> str:
    return PLAIN_MODEL.get(str(m), str(m)) if m else "its default model"


def plain_reviewer(cfg, rid, model=None) -> str:
    """'Codex, gpt-5 (bugs and security)' / 'this agent on a different model, sonnet or opus (code standards)'."""
    spec = _roster_spec(cfg, rid) or {}
    lens = PLAIN_LENS.get(spec.get("lens", "both"), spec.get("lens", "review"))
    if spec.get("kind") == "agent":
        if spec.get("alt"):
            alts = host_model_map(cfg).get("alternates") or []
            return f"this agent on a different model, {' or '.join(alts)} ({lens})"
        return f"this agent, {_plain_model(role_model(cfg, 'ratchet-adversary'))} ({lens})"
    name = spec.get("provider") or rid.rsplit("-", 1)[0]
    shown = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "copilot": "Copilot", "cursor": "Cursor",
             "opencode": "OpenCode"}.get(name, name)
    return f"{shown}, {_plain_model(spec.get('model') or model)} ({lens})"


def models_table(cfg) -> list[tuple[str, str]]:
    return [(role, role_model(cfg, role)) for role in sorted(set(ROLE_PLAIN) | set(cfg.get("role_models") or {}))]


ROLE_PLAIN = {"ratchet-checkpoint-planner": "planning the steps", "ratchet-implementer": "writing the code",
              "ratchet-fixer": "fixing review findings", "ratchet-adversary": "this agent's own code reviews",
              "ratchet-visual-reviewer": "visual UI review", "ratchet-behavior-reviewer": "UI behaviour review",
              "ratchet-app-scout": "checking the app runs", "ratchet-test-planner": "planning tests",
              "ratchet-test-writer": "writing tests", "ratchet-design-system": "the design file",
              "ratchet-prototyper": "the prototype", "ratchet-test-runner": "running tests",
              "ratchet-variant-writer": "disguised examples for high-risk checks"}


def cmd_models(args):
    """Which model each role uses on this host, so the orchestrator can pass it when it spawns a sub-agent."""
    cfg = Ctx(find_root()).config()
    rows = models_table(cfg)
    if args.json:
        print(json.dumps({"host": detect_host(cfg), "roles": dict(rows),
                          "alternates": host_model_map(cfg).get("alternates") or []}, indent=2))
        return
    print(f"Host: {detect_host(cfg)}. Spawn each role's sub-agent with this model (inherit = your own model):")
    for role, m in rows:
        print(f"  {role:<28} {m}")


def _review_preview(ctx, cfg, cp) -> tuple[list[str], bool]:
    """(plain names of who would review this step right now, every reviewer is the host's own company?)"""
    try:
        res = resolve_reviewers(ctx, cfg, cp)
    except SystemExit:
        return ["(no reviewer available)"], True
    names = [plain_reviewer(cfg, r["id"], r.get("model")) for r in res]
    host_only = all((_roster_spec(cfg, r["id"]) or {}).get("kind") == "agent" for r in res)
    return names, host_only


def cmd_plan_summary(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    plan = ctx.plan()
    if not plan or not plan.get("checkpoints"):
        die("There is no plan yet. Finish planning first, then ask again.")
    cfg = ctx.config()
    print(f"Plan: {plan.get('feature') or st.get('feature')} ({len(plan['checkpoints'])} steps)")
    obj = plan.get("objective")
    if isinstance(obj, dict) and obj:
        print(f"\nWho it's for: {obj.get('users', '')}")
        print(f"What they want: {obj.get('goal', '')}")
        print("Done means:")
        for line in obj.get("done_means") or []:
            print(f"  - {line}")
        print("Out of scope:")
        for line in obj.get("out_of_scope") or []:
            print(f"  - {line}")
    for line in spec_summary_lines(ctx, plan):
        print(line)
    all_host = True
    for i, cp in enumerate(plan["checkpoints"], 1):
        risk = cp.get("risk", "normal")
        who, host_only = _review_preview(ctx, cfg, cp)
        all_host &= host_only
        bar = (cfg.get("blocking_severities") or {}).get(risk, "all")
        print(f"\n{i}. {cp['title']}")
        print(f"   Done when: {(cp.get('done_when') or [''])[0]}")
        print(f"   UI review: {'yes' if (cp.get('gates') or {}).get('ui') else 'no'}")
        print(f"   Risk: {risk}" + ("" if bar == "all" else f" (only {' and '.join(bar)} findings block; minor ones are noted for later)"))
        print(f"   Reviewed by: {'; '.join(who)}")
        ecl = exit_code_only_line(cp)
        if ecl:
            print(f"   Tests: {ecl}")
        sl = _step_spec_line(cp)
        if sl:
            print(f"   From the spec: {sl}")
    rows = dict(models_table(cfg))
    groups = {}
    for role, m in rows.items():
        groups.setdefault(m, []).append(role)
    print("\nModels: " + "; ".join(f"{_plain_model(m)} for " + ", ".join(ROLE_PLAIN.get(r, r.replace('ratchet-', ''))
                                                                         for r in roles)
                                   for m, roles in groups.items()))
    if all_host:
        print("\nNote: no other provider is available right now, so every review will come from this agent's own "
              "company's models. If you have other agent tools installed (Codex, Gemini, Copilot, Cursor, OpenCode "
              "and so on), I can check them and use them for reviews.")
    if hooks_view(ctx, st).get("waived"):
        print("\nNote: hooks were skipped at your request, so the checks are only advice.")
    acc = _accepted_findings_lines(st)
    if acc:
        print("")
        for line in acc:
            print(line)
    ci_lines = _checkin_decisions_lines(st)
    if ci_lines:
        print("")
        for line in ci_lines:
            print(line)


# ---- providers: discover, test and register other agent CLIs ------------------------------------------------

PROBE_TOKEN = "RATCHET-PROBE-OK"


def _status_path(ctx):
    return ctx.ratchet / "provider-status.json"


_STATUS_DAMAGED = "Ratchet treats every provider as not yet tested"


def provider_status(ctx) -> dict:
    return read_json(_status_path(ctx), {}, record=_STATUS_DAMAGED) or {}


def _note_record_busy(p: Path, lost: str):
    """Say on stderr that a shared record's lock could not be had, so `lost` was not saved. Only for a best-effort
    caller that goes on regardless (e.g. probe_provider): a caller that then refuses outright uses
    _die_record_busy instead, which says this once, accurately, in its own refusal; the two are never both printed
    for the same busy write."""
    print(f"ratchet: {p} was being updated by another Ratchet command (its lock {_record_lock_path(p)} "
          f"could not be had), so {lost} was not saved.", file=sys.stderr)


def _retry_command() -> str:
    """The command this process was run as (this interpreter, this ratchet.py and every argument it was given, each
    quoted for a POSIX shell), so a refusal can say exactly what to run again and running it as written works."""
    import shlex
    args = list(_ENGINE_ARGV) if _ENGINE_ARGV is not None else list(sys.argv[1:])
    return shlex.join([sys.executable or "python3", str(Path(__file__).resolve()), *args])


def _die_record_busy(p: Path, lost: str):
    """The one refusal every update_record caller that must not claim its change was saved uses: names the record
    and the lock update_record itself takes for it (_record_lock_path, so the two can't drift apart), says what
    `lost`, and gives the user's own whole command to run again (_retry_command: every flag kept). Exits non-zero
    through die(); never returns."""
    die(f"{p} is being updated by another Ratchet command right now (its lock {_record_lock_path(p)} could not be "
        f"had), so {lost}. Run the same command again in a moment: `{_retry_command()}`")


def _update_provider_status(ctx, change, note: bool = True, lock=None) -> str:
    """Change provider-status.json (`change(data)` edits it in place). More than one command can update it (a probe
    run by `reviewers`, `providers --add`), so it is changed only through update_record, under its own lock (or
    `lock`, that lock already held by the caller); a damaged one holds no result and is replaced. Returns
    update_record's result. A caller that must not then claim the change was saved checks for "busy" itself and
    refuses with _die_record_busy, passing note=False so this does not also print a stderr note for the same busy
    write: `providers --add --no-check` does this, and a checked `providers --add` takes this lock itself first
    (see _record_probe). A best-effort caller leaves note=True, so the stderr note below speaks for it."""
    p = _status_path(ctx)

    def fn(data):
        change(data)
        return data
    res = update_record(p, fn, lock=lock, damaged=_STATUS_DAMAGED, pretty=True)
    if res == "busy" and note:
        _note_record_busy(p, "this provider check")
    return res


def probe_provider(ctx, cfg, pv, bench=True, note: bool = True, record: bool = True) -> tuple[bool, str, bool]:
    """Send a tiny request through the provider exactly as a review would go, and record the result (_record_probe:
    provider-status.json, then unavailable-reviewers.json).
    Returns (good, detail, saved): `saved` is False when either of those writes could not be made because its lock
    was busy (True when both were saved, or when nothing was sent because the provider has no command or isn't
    installed, so there was nothing to record). Every recording caller is best effort and leaves note=True, so a
    stderr line speaks for a busy write. A caller that must not claim anything was saved unless all of it was (a
    checked `providers --add`) passes record=False (nothing is written, and `saved` is False once a request was
    sent), then takes both records' locks itself and calls _record_probe."""
    cmd_t, use_stdin = provider_command(pv)
    if not cmd_t:
        return False, "no command", True
    if not shutil.which(cmd_t[0]):
        return False, f"'{cmd_t[0]}' is not installed or not on PATH", True
    prompt = f"This is a connectivity check. Reply with exactly this and nothing else: {PROBE_TOKEN}"
    work = Path(tempfile.mkdtemp(prefix="ratchet-probe-"))
    out_file = work / "last-message.txt"
    subs = {"{prompt_file}": str(work / "prompt.md"), "{prompt}": prompt, "{out_file}": str(out_file),
            "{root}": str(ctx.root), "{model}": str(pv.get("model") or "")}
    (work / "prompt.md").write_text(prompt)
    cmd = []
    for part in cmd_t:
        for k, v in subs.items():
            part = part.replace(k, v)
        cmd.append(part)
    try:
        r = subprocess.run(cmd, cwd=work, input=prompt if use_stdin else None, capture_output=True, text=True,
                           stdin=None if use_stdin else subprocess.DEVNULL,  # a CLI waiting on stdin must not hang
                           timeout=int(cfg.get("probe_timeout_sec", 120)),
                           env=dict(os.environ, RATCHET_NESTED_REVIEW="1"))
        text = (out_file.read_text() if out_file.exists() else "") + (r.stdout or "") + (r.stderr or "")
        good = PROBE_TOKEN in text.replace("\n", "")
        detail = "answered the test request" if good else (
            f"exit {r.returncode}: " + ((r.stderr or r.stdout or "no output").strip().splitlines() or ["no output"])[-1][:200])
    except subprocess.TimeoutExpired:
        good, detail = False, f"no answer within {cfg.get('probe_timeout_sec', 120)}s"
    except OSError as e:
        good, detail = False, str(e)[:200]
    shutil.rmtree(work, ignore_errors=True)
    if not record:
        return good, detail, False
    busy = _record_probe(ctx, pv, good, detail, bench=bench, note=note)
    return good, detail, not busy


def _record_probe(ctx, pv, good: bool, detail: str, bench: bool = True, note: bool = True, locks=None) -> list:
    """Record a probe's result: provider-status.json, then unavailable-reviewers.json (bench every lens of a
    failing provider when `bench`, else clear its benched entries). Returns the records whose write was busy, in
    the order tried (empty when every write was saved). `locks` maps a record's path to its _RecordLock the caller
    already holds: a caller that takes both first can't have one written and then find the other busy."""
    locks = locks or {}
    sp, up = _status_path(ctx), _unavailable_path(ctx)
    busy = []
    if _update_provider_status(ctx, lambda data: data.__setitem__(pv["id"], {"ok": good, "at": now(),
                                                                          "detail": detail}),
                               note=note, lock=locks.get(sp)) == "busy":
        busy.append(sp)
    if not good and bench:
        for lens in LENSES:
            if mark_unavailable(ctx, f"{pv['id']}-{lens}", f"test request failed: {detail}", note=note,
                                lock=locks.get(up)) is None and up not in busy:
                busy.append(up)
    else:
        def clear(live):
            for lens in LENSES:
                live.pop(f"{pv['id']}-{lens}", None)
        if _update_unavailable(ctx, clear, note=note, lock=locks.get(up)) == "busy":
            busy.append(up)
    return busy


def probe_due(ctx, cfg, pid) -> bool:
    if not cfg.get("probe_providers", True):
        return False
    st = provider_status(ctx).get(pid) or {}
    t = _parse_iso(st.get("at"))
    fresh = t and (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() < 60 * float(cfg.get("probe_ttl_minutes", 120))
    return not (st.get("ok") and fresh)


def _write_providers(ctx, plist):
    user = read_json(ctx.config_path, {}) or {}
    user["providers"] = plist
    write_json(ctx.config_path, user)


def cmd_providers(args):
    ctx = Ctx(find_root())
    ctx.ratchet.mkdir(exist_ok=True)
    cfg = ctx.config()
    host = detect_host(cfg)
    plist = providers(cfg)
    if args.remove:
        plist = [pv for pv in plist if pv["id"] != args.remove]
        _write_providers(ctx, plist)
        ok(f"Removed provider {args.remove}.")
        return
    if args.add:
        if not (args.preset or args.command):
            die("--add needs --preset <" + "|".join(PRESETS) + "> or --command '<json list>'")
        pv = {"id": args.add}
        if args.preset:
            if args.preset not in PRESETS:
                die(f"unknown preset {args.preset}; known: {', '.join(PRESETS)}. Use --command for anything else.")
            pv["preset"] = args.preset
        if args.command:
            try:
                pv["command"] = json.loads(args.command)
                assert isinstance(pv["command"], list) and pv["command"]
            except (ValueError, AssertionError):
                die("--command must be a JSON list, e.g. '[\"mycli\", \"run\", \"{prompt}\"]'")
            pv["stdin"] = bool(args.stdin)
        if args.model:
            pv["model"] = args.model
        if args.company:
            pv["company"] = args.company
        if args.for_risk:
            pv["for_risk"] = [x.strip() for x in args.for_risk.split(",") if x.strip()]
        if not provider_company(pv):
            die(f"Can't tell which company's model '{args.add}' runs. Give --model (a model name from that tool) "
                "or --company.")
        if args.no_check:
            # an earlier result was about whatever had this id before
            p = _status_path(ctx)
            if _update_provider_status(ctx, lambda data: data.pop(pv["id"], None), note=False) == "busy":
                _die_record_busy(p, f"{args.add}'s old status entry was not cleared and it was not added")
        else:
            # A refused add changes nothing: a failed test request records nothing (an older provider with this id
            # keeps its own status), and a passing one is recorded only once both records' locks are held, so a
            # busy lock on either leaves provider-status.json, unavailable-reviewers.json and config.json as they were
            good, detail = probe_provider(ctx, cfg, pv, record=False)[:2]
            if not good:
                die(f"{args.add} didn't pass the test request ({detail}). Not added. Fix the command or model and "
                    "try again (or add --no-check to add it anyway).")
            busy, locks = None, {}
            with contextlib.ExitStack() as stack:
                for rec in (_status_path(ctx), _unavailable_path(ctx)):
                    lk = stack.enter_context(_RecordLock(rec))
                    if not lk.held:
                        busy = rec
                        break
                    locks[rec] = lk
                if busy is None:
                    left = _record_probe(ctx, pv, good, detail, bench=False, note=False, locks=locks)
                    busy = left[0] if left else None
            if busy is not None:
                _die_record_busy(busy, f"nothing was changed and {args.add} was not added")
        plist = [x for x in plist if x["id"] != pv["id"]] + [pv]
        _write_providers(ctx, plist)
        ok(f"Added provider {args.add} ({provider_company(pv)}, {_plain_model(pv.get('model'))}). It can now review.")
        return
    if args.discover or args.check:
        targets = [pv for pv in plist if not args.check or args.check in ("all", pv["id"])]
        for pv in targets:
            company = provider_company(pv)
            if company == host:
                continue
            if shutil.which(provider_binary(pv) or "") and company:
                probe_provider(ctx, cfg, pv)
    status = provider_status(ctx)
    rows = []
    for pv in plist:
        company = provider_company(pv)
        st_ = status.get(pv["id"]) or {}
        installed = bool(shutil.which(provider_binary(pv) or ""))
        if company == host:
            state = "same company as this agent, so not used as an independent reviewer"
        elif not company:
            state = "needs a model (which company's model it runs is unknown)"
        elif not installed:
            state = f"not installed ('{provider_binary(pv)}' not found)"
        elif not st_:
            state = "installed, not tested yet"
        else:
            state = ("working" if st_.get("ok") else "failing") + f": {st_.get('detail')} ({str(st_.get('at'))[:16]}Z)"
        rows.append({"id": pv["id"], "company": company, "model": pv.get("model"), "installed": installed,
                     "ok": bool(st_.get("ok")) and company not in (None, host), "state": state})
    found = []
    if args.discover:
        known = {provider_binary(pv) for pv in plist}
        for name, pr in PRESETS.items():
            if pr["bin"] in known or not shutil.which(pr["bin"]):
                continue
            if pr["company"] and pr["company"] != host:
                pv = {"id": name, "preset": name}
                good, detail, _saved = probe_provider(ctx, cfg, pv)
                if good:
                    plist.append(pv)
                    _write_providers(ctx, plist)
                    rows.append({"id": name, "company": pr["company"], "model": None, "installed": True, "ok": True,
                                 "state": "working: added"})
                else:
                    rows.append({"id": name, "company": pr["company"], "model": None, "installed": True, "ok": False,
                                 "state": f"installed but failing: {detail}"})
            elif not pr["company"]:
                found.append(name)
    if args.json:
        print(json.dumps({"host": host, "providers": rows, "needs_model": found}, indent=2))
        return
    print(f"This agent runs on: {host}.")
    for r in rows:
        print(f"  {'✓' if r['ok'] else '·'} {r['id']:<10} {str(r['company'] or '?'):<10} {r['state']}")
    for name in found:
        print(f"  + {name:<10} {'?':<10} installed; it can run several companies' models. Pick a capable model from "
              f"a company other than {host} (the tool's own help lists them), then: "
              f"ratchet.py providers --add {name} --preset {name} --model <model>")
    if not any(r["ok"] for r in rows):
        print(f"No working provider from another company: reviews will come from {host}'s own models.")


# --------------------------------------------------------------------------- commands

# Which chat is running an engine command. The guard hook sees the chat (session id) of every shell call. When the
# command it is about to let run really invokes `ratchet.py init` or `ratchet.py ask` (parsed as shell words: the
# ratchet.py call must be a command of its own, never text inside quotes, an echo, a commit message, a comment or a
# heredoc), it notes that chat together with that call's exact arguments in the per-user folder (outside the repo,
# where agents can't write). The engine accepts a note only when the arguments equal its own, it is under
# ENGINE_NOTE_SECONDS old, and no other chat noted the same call; the note is then used up. `init` makes that chat
# the run's owner (on a repo's first run there is no human log yet to go by), and an `ask` while no chat owns the run
# makes the chat that asked the owner, so the user's reply in another chat can't answer the question or take the run.
# Without a usable note the engine falls back to the human log / prompt-hook owner rule (see _bind_owner).
ENGINE_NOTE_SECONDS = 60
_ENGINE_ARGV: list | None = None  # this process's own arguments (set by main)
_SHELL_OPS = set(";&|<>")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _shell_words(cmd: str):
    """Split a shell command into ("word", text) and ("op", text) tokens the way a POSIX shell would for plain
    commands. None when it can't be read safely: unbalanced quotes, a heredoc or here-string, a command
    substitution (`...` / $(...)), or bash's $'...' quoting, whose contents this simple reader doesn't follow."""
    out, cur, i, n = [], None, 0, len(cmd)

    def flush():
        nonlocal cur
        if cur is not None:
            out.append(("word", cur))
        cur = None

    while i < n:
        c = cmd[i]
        if c in " \t\r":
            flush()
            i += 1
        elif c == "\\":
            if i + 1 < n and cmd[i + 1] != "\n":
                cur = (cur or "") + cmd[i + 1]
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return None
            cur = (cur or "") + cmd[i + 1:j]
            i = j + 1
        elif c == '"':
            j, buf = i + 1, ""
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n and cmd[j + 1] in '$`"\\\n':
                    buf += "" if cmd[j + 1] == "\n" else cmd[j + 1]
                    j += 2
                    continue
                if cmd[j] == "`" or cmd.startswith("$(", j):
                    return None
                buf += cmd[j]
                j += 1
            if j >= n:
                return None
            cur = (cur or "") + buf
            i = j + 1
        elif c == "`" or cmd.startswith("$(", i) or cmd.startswith("$'", i):
            return None  # $'...' (bash ANSI-C quoting) ends at an unescaped quote only: never read inside it
        elif c == "#" and cur is None:
            while i < n and cmd[i] != "\n":
                i += 1
        elif c in "\n()":
            flush()
            out.append(("op", c))
            i += 1
        elif c in _SHELL_OPS:
            if c in "<>" and cur is not None and cur.isdigit():
                cur = None  # `2>&1`: the digits name a file descriptor, not an argument
            flush()
            j = i
            while j < n and cmd[j] in _SHELL_OPS:
                j += 1
            op = cmd[i:j]
            if "<<" in op:
                return None
            out.append(("op", op))
            i = j
        else:
            cur = (cur or "") + c
            i += 1
    flush()
    return out


def _simple_commands(words) -> list:
    """The word lists of each simple command in `_shell_words` output (split at operators; redirection targets
    are left out)."""
    cmds, cur, skip = [], [], False
    for kind, text in words:
        if kind == "op" and ("<" in text or ">" in text):
            skip = True  # a redirection: the next word is its target, not an argument
        elif kind == "op":
            cmds.append(cur)
            cur, skip = [], False
        elif skip:
            skip = False
        else:
            cur.append(text)
    cmds.append(cur)
    return cmds


def _engine_invocations(cmd: str) -> list:
    """The argument lists of every `ratchet.py ...` call `cmd` would actually run as a command of its own
    (`python3 .../ratchet.py ...`, `.../ratchet.py ...` or `$RATCHET ...`). Text that only mentions ratchet.py (in
    quotes, after echo or grep, in a comment or heredoc) is not a call. An argument the shell would still expand
    ($VAR) makes the call unknowable, so it is left out."""
    words = _shell_words(cmd or "")
    if not words:
        return []
    found = []
    for w in _simple_commands(words):
        while w and _ASSIGNMENT_RE.match(w[0]):
            w = w[1:]
        if w and re.fullmatch(r"python[0-9.]*", Path(w[0]).name):
            w = w[1:]
            while w and w[0].startswith("-"):
                w = w[1:]
            if not (w and Path(w[0]).name == "ratchet.py"):
                continue
        elif not (w and (Path(w[0]).name == "ratchet.py" or w[0] in ("$RATCHET", "${RATCHET}"))):
            continue
        argv = w[1:]
        if argv and not any("$" in a for a in argv):
            found.append(argv)
    return found


def _engine_subcommand(argv) -> str | None:
    a = list(argv)
    while a and (a[0] == "--root" or a[0].startswith("--root=")):
        a = a[2:] if a[0] == "--root" else a[1:]
    return a[0] if a else None


def _engine_notes_path() -> Path:
    return ratchet_home() / "engine-call-sessions.json"


class _EngineNotesLock(_RecordLock):
    """A _RecordLock on engine-call-sessions.json, named the same way _record_lock_path names every other record's
    lock. update_record takes this lock itself (via its own _RecordLock) when _note_engine_call / _noted_session call
    it without a lock=; this class exists only so a lock on this one record can also be taken directly, the same way
    _RecordLock(path) does for any other record."""

    def __init__(self, wait: float = 2.0):
        super().__init__(_engine_notes_path(), wait)


def _note_fresh(note) -> bool:
    fresh = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=ENGINE_NOTE_SECONDS)).isoformat(
        timespec="microseconds")
    return (isinstance(note, dict) and isinstance(note.get("argv"), list) and isinstance(note.get("session"), str)
            and bool(note["session"]) and fresh < str(note.get("at", "")) <= _in_a_minute())


def _note_engine_call(root: Path, cmd: str, session: str | None):
    """Guard hook: for every `ratchet.py init` / `ask` / `wait` call `cmd` really runs, note which chat is about
    to run it here, with its exact arguments. Best effort; never blocks the call."""
    if not session:
        return
    calls = [a for a in _engine_invocations(cmd) if _engine_subcommand(a) in ("init", "ask", "wait")]
    if not calls:
        return
    try:
        p = _engine_notes_path()

        def add(notes):
            for argv in calls:
                key = f"{root.resolve()}|{_engine_subcommand(argv)}"
                kept = [x for x in (notes.get(key) if isinstance(notes.get(key), list) else []) if _note_fresh(x)]
                notes[key] = kept + [{"session": session, "argv": argv, "at": now()}]
            return notes
        # can't be had: note nothing (the owner rules without a note apply); damaged (a crash mid-write, say): no
        # note in it counts, and this write replaces it
        _ = update_record(p, add, wait=2.0,
                          damaged="Ratchet treats it as holding no notes and replaces it with this call's note")
    except Exception:
        pass


def _noted_session(root: Path, which: str) -> str | None:
    """The chat the guard hook saw run exactly this `ratchet.py <which> ...` call (same arguments) in this project in
    the last ENGINE_NOTE_SECONDS. A matching note is used up; stale or malformed notes are dropped. None when there
    is no such note, or when more than one chat noted the same call (Ratchet can't tell which one this is)."""
    mine = list(_ENGINE_ARGV) if _ENGINE_ARGV is not None else list(sys.argv[1:])
    try:
        p = _engine_notes_path()
        key = f"{root.resolve()}|{which}"
        match = []

        def damaged(why):  # it names no chat; the guard hook's next note replaces it
            _note_unreadable(p, why, "Ratchet uses no note for this call (the rules without a note apply); the next "
                             "note the guard hook takes replaces the file")
            return KEEP

        def use(notes):
            if key not in notes:
                return KEEP
            raw = notes.get(key)
            live = [x for x in (raw if isinstance(raw, list) else []) if _note_fresh(x)]
            match.extend(x for x in live if [str(a) for a in x["argv"]] == mine)
            rest = [x for x in live if x not in match]
            if rest:
                notes[key] = rest
            else:
                notes.pop(key, None)
            return notes
        # can't be had: use no note and change nothing (the rules without a note)
        _ = update_record(p, use, wait=2.0, damaged=damaged)
    except Exception:
        return None
    sessions = {x["session"] for x in match}
    return sessions.pop() if len(sessions) == 1 else None


def cmd_init(args):
    root = find_root()
    ctx = Ctx(root)
    ctx.ratchet.mkdir(exist_ok=True)
    if not ctx.config_path.exists():
        cfg = {k: DEFAULT_CONFIG[k] for k in ["human_review", "host_provider", "ui_reviewers", "regression_command",
                                               "commit_per_checkpoint", "design_md", "standards", "app"]}
        write_json(ctx.config_path, cfg)
        full = ctx.config()
        host = detect_host(full)
        found = [pv["id"] for pv in providers(full) if provider_company(pv) not in (None, host)
                 and shutil.which(provider_binary(pv) or "")]
        print(f"Host provider: {host}" + (" (set host_provider in .ratchet/config.json)" if host == "unknown" else "")
              + ". Other providers installed: " + (", ".join(found) if found else "none found")
              + ". Run `ratchet.py providers --discover` to test them and add other agent CLIs.")
    learn = ctx.ratchet / "learnings.md"
    if not learn.exists():
        learn.write_text("# Ratchet learnings\n\nEvery Ratchet agent reads this file before starting work. "
                         "Entries come from human reviews and repeated gate failures.\n\n")
    gi = ctx.ratchet / ".gitignore"
    if not gi.exists() or gi.read_text(encoding="utf-8").startswith("manifests/"):
        # runtime state stays out of git: git stash/checkout/reset can't roll it back, and checkpoint commits
        # don't carry it. Only the files people edit and share are tracked.
        gi.write_text("*\n!.gitignore\n!config.json\n!standards.md\n!learnings.md\n", encoding="utf-8")
        if _is_git(root):
            tracked = subprocess.run(["git", "ls-files", RATCHET_DIR], cwd=root, capture_output=True,
                                     text=True).stdout.split()
            runtime = [f for f in tracked if f.split("/", 1)[-1] not in (".gitignore", "config.json", "standards.md",
                                                                        "learnings.md")]
            if runtime:
                subprocess.run(["git", "rm", "--cached", "-q", "--", *runtime], cwd=root, capture_output=True)
    existing = ctx.active_slug()
    if existing:
        old_st, why = _read_record(ctx.state_path(existing), missing=None)
        if why is not None:  # a damaged run is still the active one: it is only replaced with --force, by a person
            _note_unreadable(ctx.state_path(existing), why, "Ratchet treats that run as still active")
            old_st = {"phase": "unknown: its state.json can't be read"}
        if old_st and old_st.get("phase") not in ("done", "cancelled"):
            if not (args.force and human_override("replace the run that is still active", ctx,
                                                  f"init --force (replaced run {existing})")):
                die(f"Run '{existing}' is still active (phase {old_st['phase']}). Ask the user whether to resume it "
                    f"(ratchet.py resume) or cancel it (ratchet.py cancel, after they confirm). "
                    "A run is never replaced silently.")
    slug = args.slug or slugify(args.feature)
    rd = ctx.ratchet / "runs" / slug
    if rd.exists() and not args.force:
        die(f"Run {slug} already exists. Use --slug to pick another name.")
    dirty = []
    branch = None
    if _is_git(root):
        dirty = [l[3:] for l in subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                               text=True).stdout.splitlines() if l and not l[3:].startswith(RATCHET_DIR)]
        if args.branch:
            branch = f"ratchet/{slug}"
            r = subprocess.run(["git", "checkout", "-b", branch], cwd=root, capture_output=True, text=True)
            if r.returncode != 0:
                die(f"Could not create branch {branch}: {r.stderr.strip()}")
    (rd / "tests").mkdir(parents=True, exist_ok=True)
    (rd / "evidence").mkdir(exist_ok=True)
    human = [e for e in _human_since(ctx, "0") if e.get("session")]
    st = {
        "slug": slug, "feature": args.feature, "phase": "planning", "created_at": now(),
        "human_review": args.human_review or ctx.config()["human_review"],
        "current": None, "checkpoints": {}, "plan_hash": None, "awaiting_since": None,
        "history": [], "branch": branch, "dirty_at_start": dirty[:50],
        "owner_session": _noted_session(root, "init") or (human[-1]["session"] if human else None),
        "base_tree": fingerprint(root)["id"],
    }
    _write_record(rd / "state.json", st, pretty=True)
    record_chain(rd / "state.json")
    _write_record(ctx.ratchet / "active.json", {"slug": slug}, pretty=True)
    register_active(root, slug)
    ok(f"Initialised run '{slug}' at {rd.relative_to(root)}" + (f" on new branch {branch}" if branch else ""))
    if dirty:
        print(f"WARNING: {len(dirty)} uncommitted change(s) already in the working tree (e.g. {', '.join(dirty[:3])}). "
              "Checkpoint commits would include them — ask the user whether to commit, stash or keep them first.")
    print("NEXT: run `ratchet.py doctor` now to confirm this agent CLI's Ratchet hooks are firing.")
    print(json.dumps(next_action(ctx), indent=2))


OLD_DEFAULT_REVIEWERS = {
    "host-standards": {"kind": "agent", "lens": "standards", "vendor": "host"},
    "host-correctness": {"kind": "agent", "lens": "correctness", "vendor": "host"},
    "host-both": {"kind": "agent", "lens": "both", "vendor": "host"},
    **{f"{p}-{lens}": {"kind": "command", "preset": p, "lens": lens, "vendor": v}
       for p, v in [("claude", "anthropic"), ("codex", "openai"), ("gemini", "google"), ("copilot", "github")]
       for lens in ("correctness", "standards")},
}
OLD_DEFAULT_REVIEW_DEPTH = {
    "low": ["host-both"],
    "normal": ["host-standards", ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"]],
    "high": ["host-standards", "host-correctness",
             ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"],
             ["gemini-standards", "claude-standards", "codex-standards", "copilot-standards"]],
}

# How to run each known agent CLI non-interactively. "company" is None for CLIs that can run several companies'
# models: for those the company comes from the chosen model. Flags differ between CLI versions; a provider entry
# can always give its own "command" instead ({prompt} {prompt_file} {out_file} {root} {model} are filled in).
PRESETS = {
    "claude": {"bin": "claude", "company": "anthropic", "stdin": True, "model_flag": ["--model"], "model_at": 1,
               "command": ["claude", "-p", "--no-session-persistence", "--setting-sources", ""]},
    "codex": {"bin": "codex", "company": "openai", "stdin": True, "model_flag": ["-m"], "model_at": 2,
              "command": ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                          "--output-last-message", "{out_file}", "-"]},
    "gemini": {"bin": "gemini", "company": "google", "stdin": True, "model_flag": ["-m"], "model_at": 1,
               "command": ["gemini", "-p", "Carry out the task described on stdin. Reply with only what it asks for."]},
    "copilot": {"bin": "copilot", "company": None, "stdin": False, "model_flag": ["--model"], "model_at": 1,
                "command": ["copilot", "-p", "{prompt}"]},
    "cursor": {"bin": "cursor-agent", "company": None, "stdin": False, "model_flag": ["--model"], "model_at": 1,
               # --trust: no workspace-trust prompt in headless runs; --mode ask: read-only, cannot edit files
               "command": ["cursor-agent", "-p", "{prompt}", "--output-format", "text", "--trust", "--mode", "ask"]},
    "opencode": {"bin": "opencode", "company": None, "stdin": False, "model_flag": ["-m"], "model_at": 2,
                 "command": ["opencode", "run", "{prompt}"]},
}
SHIPPED_DEFAULT_HISTORY = {"review_depth": [OLD_DEFAULT_REVIEW_DEPTH], "reviewers": [OLD_DEFAULT_REVIEWERS]}
PRESET_BINARIES = {k: v["bin"] for k, v in PRESETS.items()}
PRESET_VENDORS = {k: v["company"] for k, v in PRESETS.items()}
_B = r"(?:^|[^a-z0-9])"   # start of a name part
_E = r"(?:$|[^a-z])"      # end of a name part (digits may follow, as in gpt5 or o3)
MODEL_COMPANIES = [
    (_B + r"(claude|opus|sonnet|haiku|anthropic)" + _E, "anthropic"),
    (_B + r"(gpt|codex|chatgpt|openai)" + _E + r"|" + _B + r"o[1-9](?:$|[^a-z0-9]|-)", "openai"),
    (_B + r"(gemini|gemma|google)" + _E, "google"), (_B + r"(grok|xai)" + _E, "xai"),
    (_B + r"(glm|zhipu|z-ai|zai)" + _E, "zhipu"), (_B + r"(qwen|alibaba)" + _E, "alibaba"),
    (_B + r"deepseek" + _E, "deepseek"), (_B + r"(kimi|moonshot)" + _E, "moonshot"),
    (_B + r"(llama|meta)" + _E, "meta"), (_B + r"(mistral|codestral|devstral|magistral)" + _E, "mistral"),
]
COMPANY_ALIASES = {"claude": "anthropic", "claude-code": "anthropic", "codex": "openai", "gpt": "openai",
                   "chatgpt": "openai", "gemini": "google", "deepmind": "google", "google deepmind": "google",
                   "copilot": "github", "grok": "xai", "x.ai": "xai", "mistral ai": "mistral", "z.ai": "zhipu",
                   "meta ai": "meta"}


def norm_company(c) -> str | None:
    if not c:
        return None
    c = str(c).strip().lower()
    return COMPANY_ALIASES.get(c, c)
LENSES = ("correctness", "standards", "both")


def company_of_model(model) -> str | None:
    m = str(model or "").lower()
    if not m:
        return None
    for rx, company in MODEL_COMPANIES:
        if re.search(rx, m):
            return company
    return None


def providers(cfg) -> list[dict]:
    return [pv for pv in (cfg.get("providers") or []) if isinstance(pv, dict) and pv.get("id")]


def provider_by_id(cfg, pid) -> dict | None:
    return next((pv for pv in providers(cfg) if pv["id"] == pid), None)


def provider_company(pv) -> str | None:
    if pv.get("company"):
        return norm_company(pv["company"])
    from_model = company_of_model(pv.get("model"))
    if from_model:
        return from_model
    return (PRESETS.get(pv.get("preset", "")) or {}).get("company")


def provider_binary(pv) -> str | None:
    if pv.get("command"):
        return str(pv["command"][0])
    return (PRESETS.get(pv.get("preset", "")) or {}).get("bin")


def provider_command(pv) -> tuple[list[str], bool]:
    """(command template, prompt on stdin?) with the model filled in."""
    preset = PRESETS.get(pv.get("preset", "")) or {}
    if pv.get("command"):
        cmd = [str(c).replace("{model}", str(pv.get("model") or "")) for c in pv["command"]]
        return cmd, bool(pv.get("stdin", False))
    cmd = list(preset.get("command") or [])
    if pv.get("model") and cmd:
        at = preset.get("model_at", 1)
        cmd = cmd[:at] + list(preset.get("model_flag") or ["--model"]) + [str(pv["model"])] + cmd[at:]
    return cmd, bool(preset.get("stdin", False))


def roster(cfg) -> dict:
    """Every reviewer id: the host's own sub-agents, any reviewer written in config 'reviewers', and three per
    provider (<id>-correctness / -standards / -both)."""
    out = dict(cfg.get("reviewers") or {})
    for pv in providers(cfg):
        for lens in LENSES:
            out.setdefault(f"{pv['id']}-{lens}", {"kind": "command", "provider": pv["id"], "lens": lens,
                                                  "vendor": provider_company(pv), "model": pv.get("model"),
                                                  "for_risk": pv.get("for_risk")})
    return out


def _roster_spec(cfg, rid):
    r = roster(cfg)
    if rid in r:
        return r[rid]
    m = re.fullmatch(r"([a-z0-9_.-]+)-(correctness|standards|both)", rid or "")
    if m and m.group(1) in PRESETS:  # an older config naming a preset reviewer that isn't a provider any more
        return {"kind": "command", "preset": m.group(1), "lens": m.group(2), "vendor": PRESETS[m.group(1)]["company"]}
    return None


def reviewer_command(cfg, rid) -> tuple[list[str], bool]:
    spec = _roster_spec(cfg, rid) or {}
    if spec.get("provider"):
        return provider_command(provider_by_id(cfg, spec["provider"]) or {})
    if spec.get("command"):
        return [str(c) for c in spec["command"]], bool(spec.get("stdin", False))
    preset = PRESETS.get(spec.get("preset", "")) or {}
    return list(preset.get("command") or []), bool(preset.get("stdin", False))


def host_model_map(cfg) -> dict:
    return ((cfg.get("host_models") or {}).get(detect_host(cfg)) or {})


def role_model(cfg, role) -> str:
    tier = (cfg.get("role_models") or {}).get(role, "inherit")
    if tier in ("strong", "standard", "light"):
        return host_model_map(cfg).get(tier) or "inherit"
    if tier == "alternate":  # a different model from the session's, for work that must not share its blind spots
        return (host_model_map(cfg).get("alternates") or ["inherit"])[0]
    return tier or "inherit"
HOST_HINTS = [  # environment variables the common agent CLIs set for the tools they run
    ("anthropic", ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")),
    ("openai", ("CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_SANDBOX_NETWORK_DISABLED")),
    ("google", ("GEMINI_CLI",)),
    ("github", ("COPILOT_CLI", "GITHUB_COPILOT_CLI")),
]


HOST_ALIASES = {"claude": "anthropic", "claude-code": "anthropic", "codex": "openai", "gpt": "openai",
                "gemini": "google", "copilot": "github"}


def detect_host(cfg=None) -> str:
    explicit = os.environ.get("RATCHET_HOST_PROVIDER") or (cfg or {}).get("host_provider", "auto")
    if explicit and explicit != "auto":
        return norm_company(explicit)
    for vendor, keys in HOST_HINTS:
        if any(os.environ.get(k) for k in keys):
            return vendor
    return "unknown"


def default_review_depth():  # noqa: E302
    return json.loads(json.dumps(DEFAULT_CONFIG["review_depth"]))


def _unavailable_path(ctx):
    return ctx.ratchet / "unavailable-reviewers.json"


_UNAVAILABLE_DAMAGED = "Ratchet treats every reviewer as available"


def _live_unavailable(data: dict) -> dict:
    t = now()
    return {k: v for k, v in data.items() if isinstance(v, dict) and str(v.get("until", "")) > t}


def unavailable_reviewers(ctx) -> dict:
    return _live_unavailable(read_json(_unavailable_path(ctx), {}, record=_UNAVAILABLE_DAMAGED) or {})


def _update_unavailable(ctx, change, note: bool = True, lock=None) -> str:
    """Change unavailable-reviewers.json (`change(live)` edits its unexpired entries in place; expired ones are
    dropped). More than one command can update it (`reviewer-status`, a probe, a failed review), so it is changed
    only through update_record, under its own lock (or `lock`, that lock already held by the caller); a damaged
    one marks no reviewer and is replaced. Returns update_record's result. A caller that must not then claim the
    change was saved checks for "busy" itself and refuses with _die_record_busy, passing note=False so this does not
    also print a stderr note for the same busy write: reviewer-status --reset and --mark (through mark_unavailable)
    do this, and a checked `providers --add` takes this lock itself first (see _record_probe). A best-effort caller
    leaves note=True, so the stderr note below speaks for it."""
    p = _unavailable_path(ctx)

    def fn(data):
        live = _live_unavailable(data)
        change(live)
        return live
    res = update_record(p, fn, lock=lock, damaged=_UNAVAILABLE_DAMAGED, pretty=True)
    if res == "busy" and note:
        _note_record_busy(p, "this change to the unavailable reviewers")
    return res


def mark_unavailable(ctx, rid, reason, hours=None, note: bool = True, lock=None):
    """Marks `rid` unavailable until `hours` from now (returns that time), unless unavailable-reviewers.json's lock
    is busy, when nothing is saved and this returns None (reviewer_status's --mark checks for that and dies, with
    note=False so _update_unavailable's own stderr note does not also print). `lock`: as _update_unavailable."""
    hours = hours if hours is not None else float(ctx.config().get("unavailable_ttl_hours", 6))
    until = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=hours)).isoformat(timespec="microseconds")
    res = _update_unavailable(ctx, lambda live: live.__setitem__(rid, {"reason": reason[:300], "since": now(),
                                                                       "until": until}), note=note, lock=lock)
    return None if res == "busy" else until


def reviewer_availability(ctx, cfg, rid, risk=None) -> tuple[bool, str]:
    spec = _roster_spec(cfg, rid)
    if spec is None:
        return False, "not defined (config 'providers' or 'reviewers')"
    if spec.get("kind") == "agent":
        if spec.get("alt"):
            alts = host_model_map(cfg).get("alternates") or []
            if not alts:
                return False, f"this agent can't pick a different model here (no host_models.alternates for {detect_host(cfg)})"
            return True, f"sub-agent of the host on a different model ({' or '.join(alts)})"
        return True, f"sub-agent of the host ({detect_host(cfg)})"
    host = detect_host(cfg)
    if not spec.get("vendor"):
        return False, "which company's model it runs is unknown — give it a model or a company"
    if spec.get("vendor") == host:
        return False, f"same company as the host ({host}) — not an independent reviewer"
    if risk and spec.get("for_risk") and risk not in spec["for_risk"]:
        return False, f"not set up for {risk}-risk steps (for_risk: {', '.join(spec['for_risk'])})"
    binary = reviewer_binary(cfg, rid) or ""
    if not binary or not shutil.which(binary):
        return False, f"'{binary or spec.get('preset')}' CLI not installed"
    down = unavailable_reviewers(ctx).get(rid) if ctx else None
    if down:
        return False, f"failed recently ({down['reason']}); retry after {down['until'][:16]}Z"
    return True, f"{binary} on PATH"


def _host_fallbacks(cfg, rid) -> list[str]:
    """The host's company on a different model first, then the host itself; same lens first, then the others,
    so a fallback keeps the reviewer COUNT."""
    lens = (_roster_spec(cfg, rid) or {}).get("lens", "both")
    order = [lens] + [x for x in ("both", "correctness", "standards") if x != lens]
    r = roster(cfg)
    return [f"host-alt-{x}" for x in order if f"host-alt-{x}" in r] + [f"host-{x}" for x in order if f"host-{x}" in r]


def expand_slot(cfg, slot) -> list[str]:
    alts = list(slot) if isinstance(slot, list) else [slot]
    out = []
    for a in alts:
        m = re.fullmatch(r"any-(correctness|standards|both)", str(a))
        if m:
            out += [f"{pv['id']}-{m.group(1)}" for pv in providers(cfg)]
            out += [f"host-alt-{m.group(1)}", f"host-{m.group(1)}"]
        else:
            out.append(a)
    return out


def reviewer_binary(cfg, rid):
    spec = _roster_spec(cfg, rid) or {}
    if spec.get("kind") != "command":
        return None
    cmd, _ = reviewer_command(cfg, rid)
    return cmd[0] if cmd else None


def resolve_reviewers(ctx, cfg, cp) -> list[dict]:
    """Turn review slots into concrete reviewer ids for THIS round.

    Returns [{"id", "slot", "fallback_for": [ids skipped], "reasons": {...}}]. Other providers are
    preferred in the order written, a provider not yet used this round before a repeat; the host's own
    sub-agents fill any slot whose other providers are unavailable.
    """
    if cp.get("reviewers"):
        slots = list(cp["reviewers"])
    else:
        depth = cfg.get("review_depth") or {}
        slots = depth.get(cp.get("risk", "normal")) or depth.get("normal")
        if not slots:  # legacy config: "adversaries": N
            slots = ["host-both"] if int(cfg.get("adversaries", 2)) == 1 else ["host-standards", "host-correctness"]
    roster_ = {rid: _roster_spec(cfg, rid) for rid in set(roster(cfg)) | {a for sl in slots for a in expand_slot(cfg, sl)}}
    roster_ = {k: v for k, v in roster_.items() if v is not None}
    risk = cp.get("risk", "normal")
    chosen, out = set(), []
    for n, slot in enumerate(slots, 1):
        alts = expand_slot(cfg, slot)
        unknown = [a for a in alts if a not in roster_]
        if unknown:
            die(f"review_depth/checkpoint names unknown reviewer(s) {unknown}; add them under config 'providers' "
                "or 'reviewers'.")
        if all(roster_[a].get("kind") == "command" for a in alts):
            alts += _host_fallbacks(cfg, alts[0])
        skipped, reasons, pick, candidates = [], {}, None, []
        for a in alts:
            if a in chosen:
                continue
            okay, why = reviewer_availability(ctx, cfg, a, risk)
            if okay:
                candidates.append(a)
            else:
                skipped.append(a)
                reasons[a] = why
        used_vendors = {roster_[c].get("vendor") for c in chosen if roster_[c].get("kind") == "command"}
        fresh = [c for c in candidates if roster_[c].get("kind") == "command" and roster_[c].get("vendor") not in used_vendors]
        pick = fresh[0] if fresh else (candidates[0] if candidates else None)
        if pick and candidates and pick != candidates[0]:
            skipped.append(candidates[0])
            reasons[candidates[0]] = "provider already reviewing this round; preferring a different one"
        if pick is None:
            continue  # every alternative already used or down: slot collapses into the others
        chosen.add(pick)
        spec = roster_[pick]
        model = spec.get("model") if spec.get("kind") == "command" else (
            "a different model: " + " or ".join(host_model_map(cfg).get("alternates") or []) if spec.get("alt")
            else role_model(cfg, "ratchet-adversary"))
        out.append({"id": pick, "slot": n, "vendor": spec.get("vendor"), "model": model,
                    "fallback_for": [x for x in skipped if x in alts and alts.index(x) < alts.index(pick)],
                    "reasons": reasons})
    if not out:
        die("No adversarial reviewer is available for this checkpoint — check config 'providers' and 'reviewers'.")
    return out


def required_reviewers(cfg, cp, ctx=None) -> list[str]:
    return [r["id"] for r in resolve_reviewers(ctx, cfg, cp)]


def _plan_hash(plan, ctx=None):
    body = {"cps": plan.get("checkpoints")}
    if ctx is not None:  # the test plans are part of what the user approves
        body["tests"] = {c["id"]: test_plan(ctx, c["id"]) for c in plan.get("checkpoints", [])}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


PLAN_ASK_NEXT = ("NEXT: settle any other question or offer with the user first, in its own turn. Then show them the "
                 "whole plan, run `ratchet.py ask approve-plan` and end that message with the question it prints, word "
                 "for word, as the only question in it. End your turn. Only their first reply after that question "
                 "counts, and only a listed yes approves; a yes from before the question does not count. Ratchet "
                 "can't see your message, so a yes meant for another question in it would still count.")
def _after_next(text: str) -> str:
    """A `NEXT: ...` line as a sentence of its own (for next_action, which says what is waiting first)."""
    body = text[len("NEXT: "):] if text.startswith("NEXT: ") else text
    return body[:1].upper() + body[1:]


REVIEW_ASK_NEXT = ("Settle any other question or offer first, in its own turn. Then run `ratchet.py ask human-approve` "
                   "and end your message with the question it prints, word for word, as the only question in it, then "
                   "end your turn. Only their first reply after that question counts: run `ratchet.py human-approve` "
                   "only if it is one of the listed yes replies. If they ask for changes instead, run "
                   "`ratchet.py feedback`.")


def cmd_plan_submit(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] not in ("planning", "awaiting_plan_approval"):
        die(f"plan-submit only allowed while planning (phase is {st['phase']}). Use plan-extend for feedback.")
    plan = ctx.plan()
    if plan is None:
        die("checkpoints.json not found in the run folder")
    errs = validate_plan(plan) + objective_problems(plan) + spec_plan_problems(ctx, plan)
    if errs:
        die("Plan invalid:\n  - " + "\n  - ".join(errs))
    st["phase"] = "awaiting_plan_approval"
    st["awaiting_since"] = now()
    if (st.get("pending") or {}).get("action") == "approve-plan":
        st.pop("pending", None)  # a question about an earlier version of the plan no longer applies
    st["plan_hash"] = _plan_hash(plan, ctx)
    st["checkpoints"] = {cp["id"]: st["checkpoints"].get(cp["id"]) or new_cp_state(cp)
                         for cp in plan["checkpoints"]}
    ctx.log(st, "plan_submitted", hash=st["plan_hash"], count=len(plan["checkpoints"]))
    ctx.save_state(st)
    path = build_viewer(ctx)
    link = viewer_link(ctx)
    ok(f"Plan valid ({len(plan['checkpoints'])} checkpoints).")
    print(f"VIEWER: {link['url']}" + ("" if link["live"] else "   (static; `ratchet.py view --serve` for a live link)"))
    print(PLAN_ASK_NEXT)
    if args.open:
        webbrowser.open(link["url"]) if link["live"] else open_viewer(path)


def _in_a_minute():
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=1)).isoformat(timespec="microseconds")


def _human_since(ctx: Ctx, since_iso: str | None):
    log = ctx.ratchet / "human_log.jsonl"
    if not log.exists() or not since_iso:
        return []
    if _IN_HOOK:  # a hook never exits over it: a log Ratchet can't vouch for holds no message (it grants nothing)
        if chain_problem(log, "human_log.jsonl"):
            return []
    else:
        verify_chain(log, "human_log.jsonl")
    out = []
    recs, _why = _read_record(log, lines=True)  # can't be read: no message (it grants nothing)
    for e in recs or []:
        if e.get("override"):
            continue  # an override audit line is not something the user typed in the chat
        at = e.get("at", "")
        if isinstance(at, str) and since_iso < at <= _in_a_minute():  # a timestamp from the future is forged
            out.append(e)
    return out


def _chat_transcript_paths(ctx: Ctx, since_iso: str | None) -> set[str]:
    """Repo-relative paths a genuine, Ratchet-recorded human reply (human_log.jsonl, hash-chained: not a bare
    claim) used as its transcript since `since_iso`. The agent CLI's own chat transcript is never the code under
    review, so a gate must not go stale only because it changed (a reply recorded between a gate passing and
    `complete` otherwise would, if the transcript happens to live inside the project, as the CLI chooses, not
    Ratchet)."""
    out = set()
    for e in _human_since(ctx, since_iso or "0"):
        tp = e.get("transcript_path")
        if not tp:
            continue
        try:
            rel = str(Path(tp).resolve().relative_to(ctx.root.resolve())).replace("\\", "/")
        except (OSError, ValueError):
            continue
        out.add(rel)
    return out


# ---- director-signed approvals -------------------------------------------------------
# A director (an orchestrator running many lanes) holds a secret key OUTSIDE every repo.
# It signs one specific pause of one specific run; the lane verifies the signature.
# Builder sessions cannot read the key or call the signing commands (PreToolUse guard),
# and an enrolled lane stops accepting chat messages as approvals — because in a
# director setup the "user" messages a builder receives come from the director.

def director_key_path() -> Path:
    return Path(os.environ.get("RATCHET_DIRECTOR_KEY_FILE") or ratchet_home() / "director.key")


def _director_key(required=True) -> bytes | None:
    p = director_key_path()
    if not p.exists():
        if required:
            die(f"No director key at {p}. The director creates it with: ratchet.py director-key")
        return None
    try:
        return p.read_bytes().strip()
    except OSError as e:  # a folder where the key goes, say: no key (chat approvals stay refused for enrolled lanes)
        if required:
            die(f"The director key at {p} can't be read ({type(e).__name__}: {e.strerror or e}). The director "
                "creates a new one with: ratchet.py director-key --rotate")
        return None


def _is_director_session() -> bool:
    """The bare environment check. Trusted as is only inside a hook (whose environment is the agent CLI's own);
    director commands go through require_director_session, which also asks what the hooks recorded."""
    return os.environ.get("RATCHET_ROLE") == "director"


# ---- who counts as the director ---------------------------------------------------------------------------
# RATCHET_ROLE=director in the environment proves nothing on its own: a builder can write a script that sets it and
# run that script with a clean-looking command the guard can't see into. The guard hook, which runs right before
# every command any session runs and has that agent CLI's own launch-time environment, records the role of the
# session that last ran a command in each project (.ratchet/hook-env.json, `guard_role`) and, for a director whose
# folder holds no .ratchet/, that folder in the per-user folder (director-folders.json). Director commands then
# need the hooks to agree.

def _director_folders_path() -> Path:
    return ratchet_home() / "director-folders.json"


def record_director_folder(folder: Path) -> bool:
    """Called by the guard of a director session whose folder has no .ratchet/: remember that folder. Returns False
    when it could not be saved (run_hook then says so; the director commands refuse without the record)."""
    try:
        key = os.path.realpath(str(folder))
        p = _director_folders_path()

        def damaged(why):  # invalid JSON, or not a JSON object: it grants nothing; this write replaces it (other
            # folders are recorded again the next time their director's guard runs there)
            _note_unreadable(p, why, "Ratchet treats it as naming no director folder and replaces it now")
            return {}

        def add(data):
            if key in data:
                return KEEP
            data[key] = now()
            return data
        # shared by every director session on this machine: under its own lock, so none drops another's folder; when
        # the lock can't be had, nothing is saved and the hook reports it like any record it could not save
        return update_record(p, add, damaged=damaged) in ("written", "kept")
    except (OSError, ValueError):
        return False


def _recorded_guard_role(root: Path | None) -> str | None:
    """'director' / 'builder' as the guard last recorded for `root`, None when no guard has run a command there
    (or there is no project), 'unreadable' when the record can't be read."""
    if root is None:
        return None
    rec, why = _read_record(_hook_env_path(root), missing=None)
    if rec is None and why is None:
        return None
    if why is not None:  # damaged (or not a JSON object): it confirms no role
        return "unreadable"
    recs = _hook_env_records(root)  # the newest guard record at the moment of reading (record_hook_env)
    if recs is None:
        return "unreadable"
    guards = [r for r in recs if r[1] == "guard"]
    role = guards[-1][2] if guards else rec.get("guard_role")
    return role if role in ("director", "builder") else (None if role is None else "unreadable")


DIRECTOR_NO_HOOKS_NOTE = (
    "RATCHET: note: Ratchet's hooks are not installed for any agent CLI here (and none has enforced anything here), "
    "so nothing records which session is the director and RATCHET_ROLE=director is taken at its word. Nothing else "
    "is enforced without hooks either; install them (the install-hooks command) so director commands need a "
    "hook-recorded director session.")


def director_session_problem(what: str) -> str | None:
    """None when this process may act as the director; otherwise a plain reason. Fail closed: RATCHET_ROLE=director
    counts only with a positive record from a director session's own guard hook. The one exception is when Ratchet's
    hooks are not installed at all (for any agent CLI), because then nothing is recorded or enforced anyway
    (require_director_session says so on stderr). Installed hooks with a missing record (none has run yet, or its
    write failed) are refused."""
    return _director_session_check(what)[0]


def _director_session_check(what: str):
    """(problem or None, note or None) for director_session_problem / require_director_session."""
    if not _is_director_session():
        return f"{what} must be run from the director's session (RATCHET_ROLE=director).", None
    if _IN_HOOK:
        return None, None
    here = Path.cwd().resolve()
    own = _ratchet_root_of(here)
    for r in (own, find_root()):  # a failed record write leaves the OLD role in place: trust no record meanwhile
        doubt = _hook_record_doubt(r) if r is not None and (r / RATCHET_DIR).is_dir() else None
        if doubt:
            return (f"{what} refused: {doubt} in {r}, so the role its hooks recorded may be out of date (a failed "
                    "write leaves the old record, which may name a director that no longer runs commands there). "
                    "Director commands are refused until that hook runs cleanly. Run the doctor command to see the "
                    "failure, make sure .ratchet/ is writable, then send any message or run any command in the "
                    "director's agent CLI so its hooks record the role again."), None
    if own is not None:
        own_role, where = _recorded_guard_role(own), own
    else:
        folders, _why = _read_record(_director_folders_path())  # damaged: it names no director folder
        own_role = "director" if isinstance(folders, dict) and os.path.realpath(str(here)) in folders else None
        where = here
    if own_role == "director":
        return None, None
    target = find_root()
    if own_role is None:
        own_role, where = _recorded_guard_role(target), target
        if own_role is None:
            if not hooks_in_use(here, own, target):
                return None, DIRECTOR_NO_HOOKS_NOTE  # no hooks at all: nothing is recorded or enforced anyway
            return (f"{what} refused: RATCHET_ROLE=director is set, and Ratchet's hooks are installed (or have run "
                    "here), but no hook "
                    f"has recorded a director session in {here} (or in {target}): the record is missing, either "
                    "because the director's agent CLI has not run a command here since its hooks were installed, "
                    "or because a hook could not save it. Ratchet does not take the variable at its word while "
                    "hooks are installed. Restart the director's agent CLI (started with RATCHET_ROLE=director) "
                    "so its hooks record the role, and run director commands from it in its own folder, reaching "
                    "a lane with --root <lane>. If it keeps failing, check that .ratchet/ (or, for a folder with no "
                    ".ratchet/, the per-user folder) is writable: the hook says so when it can't save the record."), None
    if own_role == "unreadable":
        return (f"{what} refused: {_hook_env_path(where)} can't be read, so Ratchet can't confirm this is the "
                "director's session. Delete it; the next hook run (send any message in your agent CLI) writes it "
                "again."), None
    return (f"{what} refused: RATCHET_ROLE=director is set, but Ratchet's guard hook last ran a command in {where} "
            "for a session that was not started as the director, so this is not treated as the director's session "
            "(a builder setting the variable, for example in a script, gets nothing from it). Run director commands "
            "from the director's own agent CLI, started with RATCHET_ROLE=director and with Ratchet's hooks "
            "installed, in its own folder, and reach a lane with --root <lane>."), None


def require_director_session(what: str):
    why, note = _director_session_check(what)
    if why:
        die(why)
    if note:
        print(note, file=sys.stderr)


def _approval_payload(ctx, st, action) -> str:
    parts = [str(ctx.root.resolve()), st["slug"], st["phase"], str(st.get("awaiting_since")),
             str(st.get("plan_hash")), action]
    if action == "hooks-waive":  # single use: the counter moves on every waive/clear, so a spent token is dead
        parts.append(str(st.get("hooks_waive_n", 0)))
    return "|".join(parts)


def _sign(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _enrolled_path() -> Path:
    return ratchet_home() / "enrolled.json"


def _enrolled_repair(p: Path) -> str:
    return (f"restore it from a backup or fix the JSON by hand; or move it aside (mv \"{p}\" \"{p}.damaged\") and "
            "have the director run director-enroll again for every lane it drives")


def _enrolled_outside(ctx) -> str | None:
    """When this lane was enrolled with a director, from the per-user enrolled.json (the backstop that keeps a lane
    enrolled when its own director.json is removed). A damaged file holds a restriction Ratchet can no longer read,
    so it keeps it: while a director key exists on this machine, every project without a director.json of its own
    counts as possibly enrolled (chat replies approve nothing) until a person repairs the file; director-enroll
    never writes over it. With no director key there is no director to approve anything, so it names no lane."""
    p = _enrolled_path()
    data, why = _read_record(p)
    if why is None:
        return data.get(str(ctx.root.resolve()))
    try:
        key_here = director_key_path().exists()
    except OSError:  # can't tell: keep the restriction
        key_here = True
    if key_here:
        _note_unreadable(p, why, "it holds which lanes a director enrolled, so Ratchet treats this project as "
                         "enrolled (chat replies approve nothing; a director token still does) until it is "
                         "repaired. To repair it: " + _enrolled_repair(p))
        return "unreadable"
    _note_unreadable(p, why, "no director key exists on this machine, so Ratchet treats it as naming no enrolled "
                     "lane. To repair it: " + _enrolled_repair(p))
    return None


def approval_mode(ctx) -> str:
    p = ctx.ratchet / "director.json"
    enr, why = _read_record(p, missing=None)
    if why is not None:  # it exists, so this lane was enrolled: a damaged enrollment keeps refusing chat approvals
        _note_unreadable(p, why, "Ratchet still treats this lane as enrolled with a director (chat replies approve "
                         "nothing); the director enrolls it again with director-enroll")
        return "director"
    if not enr and _enrolled_outside(ctx):
        return "director"  # enrollment file deleted from the repo: still enrolled
    if enr:
        key = _director_key(required=False)
        if key and hmac.compare_digest(enr.get("sig", ""), _sign(key, "enroll|" + str(ctx.root.resolve()))):
            return "director"
        if key:
            die(".ratchet/director.json has an invalid signature — re-enroll this lane from the director.")
        return "director"  # enrolled, key not on this machine: still refuse chat approvals
    return ctx.config().get("approver", "human")


def _content_text(c) -> str:
    """Text of a message's content: a string, or a list of text parts (Claude, Codex, Gemini shapes)."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):  # only text parts: tool results, images and the like are not something the user typed
        return "\n".join(_content_text(x) for x in c
                         if isinstance(x, str) or (isinstance(x, dict) and x.get("type", "text") in ("text", "input_text")))
    if isinstance(c, dict):
        for k in ("text", "input_text", "content", "message"):
            if isinstance(c.get(k), (str, list)):
                return _content_text(c[k])
    return ""


def _user_turns(obj):
    """Yield (text, timestamp) for every user turn in one parsed transcript record. Only records that clearly say
    they are the user's count; anything without a user role is ignored."""
    if isinstance(obj, list):
        for x in obj:
            yield from _user_turns(x)
        return
    if not isinstance(obj, dict):
        return
    for key in ("messages", "history", "items"):  # whole-file transcripts (e.g. Gemini's JSON session file)
        if isinstance(obj.get(key), list):
            yield from _user_turns(obj[key])
    msg = obj.get("message")
    ts = obj.get("timestamp") or obj.get("time") or (msg.get("timestamp") if isinstance(msg, dict) else None)
    if isinstance(msg, dict) and msg.get("role") == "user" and obj.get("type") in (None, "user"):  # Claude Code
        yield _content_text(msg.get("content")), ts
        return
    payload = obj.get("payload")
    if isinstance(payload, dict):  # Codex rollout
        if payload.get("type") == "user_message":
            yield _content_text(payload.get("message")), ts
        elif payload.get("type") == "message" and payload.get("role") == "user":
            yield _content_text(payload.get("content")), ts
        return
    if obj.get("role") in ("user", "human") or obj.get("type") in ("user", "human"):
        if "content" in obj or "text" in obj or isinstance(msg, str):
            yield _content_text(obj.get("content", obj.get("text", msg))), ts


def _norm_text(t: str) -> str:
    return " ".join((t or "").split())


def _transcript_ok(entry: dict, since: str | None) -> tuple[bool, str]:
    """Is this logged message a real user turn in the agent CLI's own transcript, typed after `since`?
    The transcript record must parse, say it is the user's, and hold the WHOLE message (whitespace aside)."""
    tp = entry.get("transcript_path")
    if entry.get("host") == "copilot" and not tp:
        return True, "copilot hooks carry no transcript"
    if not tp or not Path(tp).exists():
        return False, "no transcript to check it against"
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8_000_000))
            text = f.read().decode("utf-8", "replace")
    except OSError as e:
        return False, f"transcript unreadable ({e})"
    want = _norm_text(entry.get("prompt") or "")
    if not want:
        return False, "empty message"
    records = []
    try:
        records.append(json.loads(text))  # a whole-file JSON transcript
    except json.JSONDecodeError:
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line that is not a record proves nothing
    seen = False
    for rec in records:
        for said, ts in _user_turns(rec):
            if _norm_text(said) != want:
                continue
            seen = True
            if ts and since and _iso(ts) < _iso(since) - 90:
                continue  # an old message replayed as a new one
            return True, "found in transcript"
    return False, ("only an older copy is in the transcript" if seen
                   else "not found as a user turn in the transcript")


def _iso(ts) -> float:
    try:
        if isinstance(ts, (int, float)):
            return ts / 1000 if ts > 1e12 else float(ts)
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _require_approval(ctx, st, action, token=None):
    """Returns {"by": "human"|"director", "detail": ...} or dies."""
    mode = approval_mode(ctx)
    if token:
        if mode == "human":
            die("This lane only accepts human approvals (approver=human). A director must enroll it first.")
        key = _director_key()
        if not hmac.compare_digest(token, _sign(key, _approval_payload(ctx, st, action))):
            die("Director token does not match this run's current pause (wrong lane, stale token, "
                "or the run moved on). Ask the director to sign again.")
        return {"by": "director", "detail": f"director-signed {action}"}
    if mode == "director":
        die("This lane is director-enrolled: approvals need a director token "
            f"(director runs: ratchet.py --root {ctx.root} director-sign {action}). "
            "Chat messages do not count as approvals here.")
    if human_override(f"approve this pause ({action})", ctx, action):
        return {"by": "human", "detail": "(RATCHET_HUMAN_OVERRIDE, code typed back at the terminal)"}
    p = st.get("pending") or {}
    if action in ASKED_APPROVALS:
        return _asked_reply(ctx, st, action, p)
    # what is left: hooks-waive (its own exact list, first reply only) and feedback (any text). Either way only the
    # run's own chat counts (the hooks question's chat for hooks-waive).
    msgs = _replies(ctx, st, st.get("awaiting_since"), p if p.get("action") == action else None)
    if action == "feedback":
        # When the review question was asked at this pause, the reply that answered (or spent) it is the feedback,
        # not whatever the user typed later.
        q = p if p.get("action") == "human-approve" else (st.get("spent_question") or {})
        if q.get("action") == "human-approve" and q.get("asked_at") and (
                q is p or q["asked_at"] == st.get("awaiting_since")):
            msgs = _replies(ctx, st, q["asked_at"], q)[:1]
    if p.get("action") == "hooks-waive" and action != "hooks-waive":
        # the first reply after the hooks question is its answer, whatever it says ("Any other reply means no"),
        # so it can never double as anything else
        after = [m for m in msgs if m.get("at", "") > p.get("asked_at", "")]
        if after:
            msgs = [m for m in msgs if m is not after[0]]
    if action == "hooks-waive":
        msgs = msgs[:1]  # only the FIRST reply after the hooks question answers it
    if msgs:
        entry = msgs[-1]
        last = entry.get("prompt", "")
        if action == "hooks-waive" and hooks_waive_intent(last) != "approve":
            st.pop("pending", None)  # spent: a later yes must not turn this no into consent
            ctx.save_state(st)
            die(f"The user's reply to the hooks question was not a clear yes: \"{last[:120]}\". Nothing was "
                "recorded. Only the exact reply \"" + HOOKS_WAIVE_REPLY + "\" (or a listed equivalent) counts. If they "
                "still want to skip hooks, run `ratchet.py ask hooks-waive` again and show them the question it "
                "prints word for word.")
        if ctx.config().get("verify_transcripts", True):
            good, why = _transcript_ok(entry, st.get("awaiting_since"))
            if not good:
                die(f"Couldn't confirm that reply came from the user ({why}). Ask the user to reply again in this "
                    "chat; if it keeps failing, `ratchet.py doctor` shows whether the prompt hook is installed.")
        return {"by": "human", "detail": last[:200], "session": entry.get("session")}
    die("No human message recorded since Ratchet started waiting for the user. Ask them, end your turn and wait "
        "for their reply; approval must come from them. If they have already replied, Ratchet's prompt hook is not "
        "running in this agent CLI, so their reply could not be recorded: run `ratchet.py doctor --json` and follow "
        "the hooks steps in the orchestrator skill.")


# Approvals that answer a question Ratchet printed (`ratchet.py ask <action>`). The user's FIRST reply after that
# question is the answer, and only a listed yes (APPROVAL_REPLIES) approves. hooks-waive has its own exact list,
# feedback takes any text and a lettered choice takes a letter, so they are not in here.
ASKED_APPROVALS = ("approve-plan", "human-approve", "cancel", "set-review", "approve-changes", "claim", "ui-respec")


def _ask_cmd(action, mode=None, eng="ratchet.py") -> str:
    return f"`{eng} ask {action}{' ' + mode if mode else ''}`"


def _question_what(action, mode=None) -> str:
    """The question `ask <action>` put to the user, in the words it used ("shall I <this>?")."""
    if action == "set-review":
        return f"change how often they review to: {REVIEW_MODE_WORDS.get(mode, mode)}"
    return ASKABLE.get(action, action)


def _question_session(st, p=None):
    """The chat whose replies can answer a question: the one recorded when it was asked (for `claim`, the chat that
    asked to take the run over), else the run's owner chat. None while no chat owns the run yet."""
    return (p or {}).get("session") or st.get("owner_session")


def _replies(ctx, st, since, p=None):
    """Human messages after `since` typed in the question's own chat. A message from another chat never answers (or
    spends) a question asked here, and so never moves the run to that chat: only `resume --claim` does that. There is
    no fallback to every chat: while no chat is known, only a message that carries no chat id at all counts (from an
    agent CLI that sends none, where Ratchet can't tell chats apart); a message from any identified chat makes that
    chat the owner first (the prompt hook records it, and Ctx.state binds it under the command's lock), so it never
    answers a question unbound."""
    sess = _question_session(st, p)
    return [m for m in _human_since(ctx, since) if m.get("session") == sess]


def _spend_question(ctx, st, reply=""):
    """The question got its one answer and it was not a yes: close it, so a later yes can't count. awaiting_since is
    left at the question, so `feedback` can still record the change request in that same reply."""
    p = st.pop("pending", None) or {}
    st["spent_question"] = {"action": p.get("action"), "asked_at": p.get("asked_at"), "session": p.get("session")}
    ctx.log(st, "question_not_approved", action=p.get("action"), reply=reply[:200])
    ctx.save_state(st)


def _asked_reply(ctx, st, action, p):
    """The user's answer to the question `ask <action>` printed, or die. Only their first reply after it counts."""
    want = p.get("action")
    mode_txt = f" {p['mode']}" if p.get("mode") else ""
    if want != action:
        if want:
            what = _question_what(want, p.get("mode"))
            die(f"A different question is waiting for the user's answer: shall I {what}? Their reply answers THAT "
                f"question, not this one. If they said yes to it, run `ratchet.py {PENDING_COMMAND.get(want, want)}"
                f"{mode_txt}`. If they said no or moved on, run `ratchet.py ask withdraw`. Then print this question "
                f"with {_ask_cmd(action)}, show it to them word for word, and wait for their reply.")
        die(f"Ratchet has not asked the user this question yet, so no reply counts as their answer. Run "
            f"{_ask_cmd(action)}, show them the question it prints word for word, and end your turn. Only their first "
            "reply after the question counts; a yes from before it does not.")
    asked = p.get("asked_at")
    again = _ask_cmd(action, p.get("mode"))
    if p.get("phase") and p["phase"] != st.get("phase"):
        _spend_question(ctx, st)
        die(f"That question was asked while the run was {PHASE_PLAIN.get(p['phase'], p['phase'])}; it is now "
            f"{PHASE_PLAIN.get(st.get('phase'), st.get('phase'))}, so it no longer applies. Nothing was approved. If "
            f"you still need their yes, print the question again ({again}).")
    if p.get("overtook"):
        _spend_question(ctx, st)
        die(f"This question was printed while another one (shall I {_question_what(p['overtook'], p.get('overtook_mode'))}"
            "?) was still waiting for the user's first reply, so their reply could be meant for either. Nothing was "
            f"approved and the question is closed. Print it again on its own ({again}), with no other question or "
            "offer in that message, and wait for their reply.")
    msgs = _replies(ctx, st, asked, p)  # only the chat the question was asked in can answer it
    if not msgs:
        die("No human message recorded in this run's chat since the question was printed (a message typed in another "
            "chat never answers it). End your turn and wait for the user's reply; approval must come from them. If "
            "they have already replied, Ratchet's prompt hook is not running in this agent CLI, so their reply could "
            "not be recorded: run `ratchet.py doctor --json` and follow the hooks steps in the orchestrator skill.")
    entry = msgs[0]  # the first reply answers the question; later messages never do
    reply = entry.get("prompt", "")
    if not is_listed_yes(reply):
        _spend_question(ctx, st, reply)
        die(f"The user's first reply after the question was not one of the listed yes replies ({listed_yes_text()}): "
            f"\"{reply[:120]}\". Nothing was approved, and the question is closed, so a later yes does not count. "
            "Answer what they said or make the change they asked for. If you still need their yes, print the question "
            f"again ({again}), show it to them word for word, and wait for their reply.")
    if ctx.config().get("verify_transcripts", True):
        good, why = _transcript_ok(entry, asked)
        if not good:
            _spend_question(ctx, st, reply)
            die(f"Couldn't confirm that reply came from the user ({why}). Nothing was approved. Print the question "
                f"again ({again}) and ask them to reply in this chat; if it keeps failing, `ratchet.py doctor` shows "
                "whether the prompt hook is installed.")
    return {"by": "human", "detail": reply[:200], "session": entry.get("session")}


def _require_human(ctx, st, action="human-approve", token=None):
    a = _require_approval(ctx, st, action, token)
    return {"prompt": a["detail"], "by": a["by"], "session": a.get("session")}


def cmd_director_key(args):
    require_director_session("director-key")
    p = director_key_path()
    if p.exists() and not args.rotate:
        ok(f"Director key exists: {p}")
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(secrets.token_hex(32) + "\n")
    os.chmod(p, 0o600)
    ok(f"Director key {'rotated' if args.rotate else 'created'}: {p}  (re-enroll lanes after a rotation)")


def cmd_director_enroll(args):
    require_director_session("director-enroll")
    ctx = Ctx(find_root())
    ep = _enrolled_path()
    lane = str(ctx.root.resolve())
    bad, signed = [], []

    def damaged(why):  # it holds other lanes' enrollment: never replace it with this lane's alone
        bad.append(why)
        return KEEP

    def add(outside):
        signed.append(_sign(_director_key(), "enroll|" + lane))
        outside[lane] = signed[0]
        return outside
    # shared by every lane on this machine: changed only under its own lock, so two lanes enrolled at once both stay
    done = update_record(ep, add, damaged=damaged, pretty=True)
    if bad:
        die(f"Ratchet's record of enrolled lanes ({ep}) can't be read ({bad[0]}). It keeps every lane a director "
            "enrolled in director mode even if that lane's .ratchet/director.json is removed, so Ratchet won't write "
            f"over it, and nothing was changed. Tell the user; to repair it: {_enrolled_repair(ep)}.")
    if done == "busy":
        _die_record_busy(ep, "nothing was changed")
    ctx.ratchet.mkdir(exist_ok=True)
    _write_record(ctx.ratchet / "director.json", {"enrolled_at": now(), "sig": signed[0]}, pretty=True)
    ok(f"Lane {ctx.root} enrolled: approvals now require a director token.")


def cmd_director_sign(args):
    require_director_session("director-sign")
    ctx = Ctx(find_root())
    st = ctx.state()
    want = {"approve-plan": ["awaiting_plan_approval"],
            "human-approve": ["awaiting_checkpoint_review", "awaiting_final_review", "blocked"],
            "feedback": ["awaiting_checkpoint_review", "awaiting_final_review", "done"],
            "approve-changes": ["building", "blocked", "awaiting_checkpoint_review", "awaiting_final_review"],
            "ui-respec": ["building", "blocked"],
            "guardrail": ["building", "blocked"],
            "accept-finding": ["building", "blocked"],
            "cancel": ["planning", "awaiting_plan_approval", "building", "blocked", "paused",
                       "awaiting_checkpoint_review", "awaiting_final_review", "planning_feedback"],
            "set-review": ["planning", "awaiting_plan_approval", "building", "blocked", "paused",
                           "awaiting_checkpoint_review", "awaiting_final_review"],
            "hooks-waive": ["planning", "awaiting_plan_approval", "building", "blocked", "paused",
                            "awaiting_checkpoint_review", "awaiting_final_review"]}[args.action]
    if st["phase"] not in want:
        die(f"Run {st['slug']} is in phase {st['phase']}; {args.action} applies to {want}.")
    action = args.action
    if action == "guardrail":
        if not args.kind:
            die("director-sign guardrail needs --kind <kind> (from ratchet.py repeats)")
        action = f"guardrail:{norm_kind(args.kind)}"
    print(_sign(_director_key(), _approval_payload(ctx, st, action)))


def cmd_approve_plan(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] != "awaiting_plan_approval":
        die(f"Nothing to approve (phase {st['phase']})")
    plan = ctx.plan()
    if _plan_hash(plan, ctx) != st["plan_hash"]:
        die("checkpoints.json changed since it was shown to the human. Run plan-submit again and re-review.")
    missing = [c["id"] for c in plan["checkpoints"] if c["gates"].get("behavior") and not test_plan(ctx, c["id"])]
    if missing:
        die(f"Test plans missing for {', '.join(missing)}. Spawn ratchet-test-planner first.")
    msg = _require_human(ctx, st, "approve-plan", getattr(args, "director_token", None))
    if (st.get("pending") or {}).get("action") == "approve-plan":
        st.pop("pending", None)  # answered
    st["approved"] = approval_snapshot(ctx, st)  # the owner chat is unchanged: only `resume --claim` moves a run
    st["phase"] = "building"
    st["current"] = plan["checkpoints"][0]["id"]
    first = st["checkpoints"][st["current"]]
    first["status"] = "in_progress"
    first["started_at"] = now()
    first["base_tree"] = fingerprint(ctx.root)["id"]
    st["awaiting_since"] = None
    ctx.log(st, "plan_approved", by=msg["by"], note=msg.get("prompt", "")[:200])
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"Plan approved. Building {st['current']}.")
    print(json.dumps(next_action(ctx, st), indent=2))


def _cur(ctx, st, cp_id):
    if st["phase"] != "building":
        die(f"Not building (phase {st['phase']})")
    if cp_id != st["current"]:
        die(f"{cp_id} is not the current checkpoint ({st['current']}). Checkpoints run strictly in order.")
    verify_integrity(ctx, st)
    return st["checkpoints"][cp_id]


def _expect_step(cps, allowed, cp_id):
    if cps["step"] not in allowed:
        die(f"{cp_id} is at step '{cps['step']}', expected one of {allowed}. Run ratchet.py next.")


def _planned_cases(ctx, cp_id):
    tp = test_plan(ctx, cp_id) or {}
    return [c for c in tp.get("cases", []) if c.get("layer", "code") != "browser"]


def _coverage(ctx, cp):
    """Every planned (non-browser) case ID must appear in at least one test file (in a non-Python file, the id in
    any usual spelling on a test's opening line, such as Go's TestCP1_T01, counts too)."""
    files = (cp.get("tests") or {}).get("files") or []
    texts = [(p, p.read_text(errors="ignore")) for p in _test_paths(ctx, cp)]
    blob = "".join(t for _, t in texts)
    missing = [c["id"] for c in _planned_cases(ctx, cp["id"])
               if not re.search(r"(?<![A-Za-z0-9-])" + re.escape(c["id"]) + r"(?![0-9])", blob)
               and not _declared(texts, c["id"])]
    return files, missing, bool(blob)


def _declared(texts, cid) -> bool:
    """True when a non-Python test file has `cid` on a line that opens a test (see _test_decls)."""
    return any(_test_decls(t.splitlines(), _case_id_re(cid)) for p, t in texts if p.suffix != ".py")


def _undeclared(ctx, cp) -> str | None:
    """Why a planned test can't be told apart by name: a planned case id that, outside Python files, appears only in
    comments, so no test's opening line holds it (None when every id is placed)."""
    texts = [(p, p.read_text(errors="ignore")) for p in _test_paths(ctx, cp)]
    py = "".join(t for p, t in texts if p.suffix == ".py")
    bad = [c["id"] for c in _planned_cases(ctx, cp["id"])
           if not re.search(r"(?<![A-Za-z0-9-])" + re.escape(c["id"]) + r"(?![0-9])", py)
           and not _declared(texts, c["id"])]
    if not bad:
        return None
    cid = bad[0]
    return (f"Planned case {', '.join(bad)} appears in the test files only inside a comment (in a test's body or "
            f"above it). Outside Python, a planned case id must be on the line that opens its test: in the test's "
            f"name, such as it('[{cid}] ...', () => {{ or func Test{cid.replace('-', '_')}(t *testing.T) {{, or on "
            f"an annotation directly above it, such as @DisplayName(\"[{cid}] ...\"), so the test's own result "
            f"carries it. Have ratchet-test-writer move the id into the test's name.")


def cmd_tests_written(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["tests", "red"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    if not cp["gates"].get("behavior"):
        cps["step"] = "implement"
        ctx.save_state(st)
        ok(f"{args.cp}: behavior gate off ({cp.get('behavior_skip_reason')}); go implement.")
        return
    gr = cp.get("guardrail") or {}
    if gr.get("high_risk") and not gr.get("strengthen"):
        vd = ctx.root / (gr.get("variants_dir") or "")
        nb = len([p for p in (vd / "bad").rglob("*") if p.is_file()]) if (vd / "bad").is_dir() else 0
        ng = len([p for p in (vd / "good").rglob("*") if p.is_file()]) if (vd / "good").is_dir() else 0
        if nb < 3 or ng < 2:
            die(f"{args.cp} guards a high-risk kind of mistake: first spawn ratchet-variant-writer (it gets only the "
                f"kind, the why and `ratchet.py occurrences {gr['kind']}`, never the check) to write at least 3 "
                f"disguised bad variants in {gr.get('variants_dir')}/bad and 2 look-alike good examples in "
                f"{gr.get('variants_dir')}/good (found {nb} and {ng}).")
    files, missing, found = _coverage(ctx, cp)
    if not found:
        die(f"No test files found at {files}. Tests must be written to tests.files.")
    if missing:
        die(f"Planned cases not present in the test files: {', '.join(missing)}. "
            f"Each test must include its case ID (e.g. '[{args.cp}-T01] ...').")
    why = _undeclared(ctx, cp)
    if why:
        die(why)
    cps["step"] = "red"
    ctx.log(st, "tests_written", cp=args.cp, files=files)
    ctx.save_state(st)
    ok(f"{args.cp}: all {len(_planned_cases(ctx, args.cp))} planned cases are present. Next: ratchet.py red {args.cp}")


def _run_cmd(ctx, command, log_path, timeout, report: Path | None = None):
    """Run a test or check command. With `report`, the command gets that path in RATCHET_TEST_REPORT, and any file
    already there is deleted first, so a report left by an earlier run can never count for this one."""
    env = dict(os.environ, CI="1", FORCE_COLOR="0")
    env.pop("RATCHET_TEST_REPORT", None)
    if report is not None:
        try:
            report.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            die(f"Could not delete the old test report at {report.relative_to(ctx.root)} ({e}); remove it and re-run.")
        env["RATCHET_TEST_REPORT"] = str(report)
    t0 = _dt.datetime.now()
    try:
        r = subprocess.run(command, shell=True, cwd=ctx.root, capture_output=True, text=True,
                           timeout=timeout, env=env)
        code, out = r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        code, out = 124, f"TIMEOUT after {timeout}s\n{e.stdout or ''}{e.stderr or ''}"
    dur = (_dt.datetime.now() - t0).total_seconds()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"$ {command}\n# exit={code} duration={dur:.1f}s at {now()}\n\n{out}")
    return code, out, dur


def _report_path(ctx, cp):
    """Where the test command must write its per-test results, or None when the approved plan has none for this
    step (tests.report "none", or a plan from before reports existed): then the exit code alone decides."""
    fmt = (cp.get("tests") or {}).get("report")
    if fmt not in ("junit", "json"):
        return None
    return ctx.evidence(cp["id"]) / ("test-report.xml" if fmt == "junit" else "test-report.json")


def _read_test_report(ctx, path: Path):
    """The per-test results in a JUnit XML or Ratchet JSON report (told apart by content): ([(name, status)], None),
    or (None, why). status is passed, failed (a check failed), error (a crash) or skipped. Never raises."""
    rel = path.relative_to(ctx.root)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, f"no test report was written at {rel}"
    except Exception as e:
        return None, f"the test report at {rel} could not be read ({type(e).__name__}: {e})"
    if raw.startswith(b"\xef\xbb\xbf"):  # a UTF-8 byte-order mark, as some .NET loggers write
        raw = raw[3:]
    try:
        if raw.lstrip().startswith(b"<"):
            import xml.etree.ElementTree as ET
            out = []
            for tc in ET.fromstring(raw).iter("testcase"):
                kids = {k.tag for k in tc}
                status = ("error" if "error" in kids else "failed" if "failure" in kids
                          else "skipped" if "skipped" in kids else "passed")
                props = [pr.get("value") or "" for pr in tc.iter("property")]
                out.append((" ".join([tc.get("classname") or "", tc.get("name") or ""] + props).strip(), status))
            return out, None
        data, why = _read_record(path, kind=dict)  # the one JSON reader; it never raises
        cases = data.get("cases") if why is None else None
        if isinstance(cases, list) and all(isinstance(c, dict) and isinstance(c.get("id"), str) and c.get("status")
                                           in ("passed", "failed", "error", "skipped") for c in cases):
            return [(c["id"], c["status"]) for c in cases], None
        why = why or "it needs cases: [{id, status}] with status passed, failed, error or skipped"
    except Exception as e:  # invalid XML or JSON, bytes that are not UTF-8, nesting too deep
        why = f"{type(e).__name__}: {e}"
    return None, f"the test report at {rel} is malformed ({why})"


def _case_id_re(case_id: str):
    """Finds a case id in a reported test name in any usual spelling: CP1-T01, CP1_T01, cp1_t01 or CP1T01 (pytest,
    Go and Java names can't hold '-'), with no digit run on either side (so CP1-T01 never matches CP11-T01 or
    CP1-T011)."""
    head, _, tail = case_id.rpartition("-")
    body = re.escape(head) + "[-_]?" + re.escape(tail) if head else re.escape(case_id)
    return re.compile(r"(?<![0-9])" + body + r"(?![0-9])", re.I)


def _case_problems(ctx, cp, cases, red: bool):
    """(what stops the planned cases from counting, whether any of them failed a check). Every planned case must
    have run (its id in a comment proves nothing), no two of its results may have the same name, none may crash or be
    skipped, and after the build every one must pass."""
    out, failed = [], False
    for c in _planned_cases(ctx, cp["id"]):
        pat = _case_id_re(c["id"])
        got = sorted({s for name, s in cases if pat.search(name)})
        named = [name for name, _ in cases if pat.search(name)]
        twice = next((n for n in named if named.count(n) > 1), None)
        failed = failed or "failed" in got
        if twice is not None:
            out.append(f"planned case {c['id']} ran more than once (two results are named '{twice}'; give each "
                       f"test its own result name — a test.each/it.each title needs a placeholder such as %s, and "
                       f"a JSON report needs a distinct id per result — or, if a second test stands in for the "
                       f"planned one, give it its own case id and name)")
        elif not got:
            out.append(f"{c['id']} did not run (it has no result in the test report)")
        elif "error" in got:
            out.append(f"{c['id']} crashed with an error, which does not count as a failed check")
        elif "skipped" in got:  # red refuses a skipped planned case, so at behavior a skip is always new
            out.append(f"{c['id']} was skipped" + ("" if red else " (it was not skipped at the red run)"))
        elif not red and got != ["passed"]:
            out.append(f"{c['id']} did not pass")
    return out, failed


def _tail(text, n=60):
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])


def cmd_red(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["red"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    _, missing, _ = _coverage(ctx, cp)
    if missing:
        die(f"Planned cases not present in the test files: {', '.join(missing)}. Each test must include its case ID "
            f"(e.g. '[{args.cp}-T01] ...'); have ratchet-test-writer put the missing tests back and re-run red.")
    why = _undeclared(ctx, cp)
    if why:
        die(why)
    cfg = ctx.config()
    rpath = _report_path(ctx, cp)
    code, out, _ = _run_cmd(ctx, cp["tests"]["command"], ctx.evidence(args.cp) / "red.log", cfg["test_timeout_sec"],
                            rpath)
    cps["red"] = {"exit": code, "at": now()}
    green = code == 0
    if rpath:
        cases, why = _read_test_report(ctx, rpath)
        bad, failed = ([why], False) if why else _case_problems(ctx, cp, cases, red=True)
        if bad:
            ctx.save_state(st)
            die(f"{args.cp}: the red run does not count: " + "; ".join(bad) + ". The test command must write each "
                "planned case's result to the file named in RATCHET_TEST_REPORT; have ratchet-test-writer fix the "
                "tests and re-run.")
        green = not failed  # the report, not the exit code, says whether a planned case failed a check
    if green and not args.allow_green_first:
        ctx.save_state(st)
        die(f"{args.cp}: tests PASSED before implementation — they don't prove anything yet. "
            f"Have ratchet-test-writer strengthen them, or if this checkpoint only preserves existing "
            f"behaviour re-run with --allow-green-first --reason '...'.")
    if green:
        if not args.reason:
            die("--allow-green-first needs --reason")
        if not cp.get("green_first_ok"):
            ctx.save_state(st)
            die(f"{args.cp} is not marked green_first_ok in the approved plan, so its tests must fail before "
                "implementation. (Pure refactors get green_first_ok from the planner, visible to the user at approval.)")
        cps["red"]["green_first_reason"] = args.reason
    cps["red_tests"] = _snapshot_tests(ctx, cp)
    cps["step"] = "implement"
    ctx.log(st, "red", cp=args.cp, exit=code)
    ctx.save_state(st)
    ok(f"{args.cp}: red run recorded (exit {code}). Next: implement.")


def _test_paths(ctx, cp) -> list[Path]:
    out = []
    for f in (cp.get("tests") or {}).get("files") or []:
        out += sorted(ctx.root.glob(f)) if any(ch in f for ch in "*?[") else [ctx.root / f]
    return [p for p in out if p.is_file()]


def _snapshot_tests(ctx, cp) -> dict:
    """Keep a copy of the tests exactly as they failed at the red run (in evidence/, which agents can't write)."""
    snap = {}
    d = ctx.evidence(cp["id"]) / "red-tests"
    d.mkdir(parents=True, exist_ok=True)
    for p in _test_paths(ctx, cp):
        rel = str(p.relative_to(ctx.root))
        (d / rel.replace("/", "__")).write_bytes(p.read_bytes())
        snap[rel] = _file_sha(p)
    return snap


def _additive(old: str, new: str) -> bool:
    """True if every non-blank line of `old` still appears, in order, in `new` (tests were only added to)."""
    it = iter([l.rstrip() for l in new.splitlines() if l.strip()])
    return all(any(line == n for n in it) for line in [l.rstrip() for l in old.splitlines() if l.strip()])


_PY_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FROZEN_WHY = ("A planned test can't change after its first run: an edit that skips it or ends it early would let "
               "it pass without checking anything (if it really must change, ask the user: "
               "`ratchet.py ask approve-changes`)")


def _py_scope_items(body):
    """(name, node) for each definition in one Python scope: defs, classes and assignments, including those inside
    if, try, with and for blocks, but not those inside a nested function or class."""
    for s in body:
        if isinstance(s, _PY_DEFS):
            yield s.name, s
        elif isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (s.targets if isinstance(s, ast.Assign) else [s.target]):
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        yield n.id, s
        else:
            for f in ("body", "orelse", "finalbody", "handlers", "cases"):
                sub = getattr(s, f, None)
                if isinstance(sub, list):
                    yield from _py_scope_items(sub)


def _py_defs(body, path=()):
    """(path, node) for every def and class, nested ones too. A path is ((name, nth of that name in its scope), ...)."""
    seen = {}
    for name, n in _py_scope_items(body):
        if isinstance(n, _PY_DEFS):
            k = seen[name] = seen.get(name, -1) + 1
            yield path + ((name, k),), n
            yield from _py_defs(n.body, path + ((name, k),))


def _py_lookup(tree, path):
    """The defs along `path` in `tree`, outermost first, or None when one of them is missing."""
    body, nodes = tree.body, []
    for name, k in path:
        defs = [n for nm, n in _py_scope_items(body) if nm == name and isinstance(n, _PY_DEFS)]
        if k >= len(defs):
            return None
        nodes.append(defs[k])
        body = defs[k].body
    return nodes


def _py_start(n) -> int:
    return min([n.lineno] + [d.lineno for d in n.decorator_list])


def _py_frozen(old: str, new: str, ids) -> list[str] | None:
    """The structural freeze for a Python test file (None when the red snapshot is not Python). Each planned case
    id belongs to the innermost def or class around it (or right after its comment line); that definition, with its
    decorators and its enclosing classes' decorators, must be unchanged (compared as ast.dump) and no later
    definition in its scope may reuse its name. An id found in no def or class at all (only in a header comment,
    say) makes the file's top-level code the test:
    each old top-level statement must still be there, unchanged and in order, and no old def or class name may be
    defined again. New tests and helpers may be added anywhere else."""
    try:
        t_old = ast.parse(old)
    except (SyntaxError, ValueError):
        return None
    try:
        t_new = ast.parse(new)
    except (SyntaxError, ValueError) as e:
        return [f"it no longer parses as Python ({type(e).__name__}), so its planned tests can't be checked"]
    lines = old.splitlines()
    defs = list(_py_defs(t_old.body))
    starts = {_py_start(n): (p, n) for p, n in defs}
    units, module_ids = {}, []
    for cid in ids:
        pat, placed, loose = _case_id_re(cid), False, False
        for no, line in enumerate(lines, 1):
            if not pat.search(line):
                continue
            inside = [(p, n) for p, n in defs if _py_start(n) <= no <= (n.end_lineno or n.lineno)]
            if not inside and line.lstrip().startswith("#"):
                k = no + 1
                while k <= len(lines) and lines[k - 1].lstrip().startswith("#"):
                    k += 1
                inside = [starts[k]] if k in starts else []
            if inside:
                p, _n = max(inside, key=lambda pn: len(pn[0]))
                units.setdefault(p, cid)
                placed = True
            else:
                loose = True
        if loose and not placed:  # only an id found in no def or class makes the top-level code the test
            module_ids.append(cid)
    out = []

    def count(tree, nodes, name):
        body = nodes[-2].body if nodes and len(nodes) > 1 else tree.body
        return sum(1 for nm, _ in _py_scope_items(body) if nm == name)

    for p, cid in units.items():
        what = f"{'.'.join(x for x, _ in p)} ({cid})"
        olds, news = _py_lookup(t_old, p), _py_lookup(t_new, p)
        if news is None:
            out.append(f"planned test {what} is gone since the red run")
        elif any(count(t_new, news[:j + 1], p[j][0]) > count(t_old, olds[:j + 1], p[j][0]) for j in range(len(p))):
            out.append(f"planned test {what} is defined again since the red run (the later definition replaces it)")
        elif ast.dump(news[-1]) != ast.dump(olds[-1]) or [ast.dump(d) for a in news[:-1] for d in a.decorator_list] \
                != [ast.dump(d) for a in olds[:-1] for d in a.decorator_list]:
            out.append(f"planned test {what} changed since the red run")
    if module_ids:
        what = f"{', '.join(module_ids)} (named outside any function, so the file's existing top-level code is the test)"
        cur = iter([ast.dump(s) for s in t_new.body])
        changed = next((s for s in t_old.body if not any(ast.dump(s) == c for c in cur)), None)
        top = {nm for nm, n in _py_scope_items(t_old.body) if isinstance(n, _PY_DEFS)}
        again = sorted(nm for nm in top if count(t_new, None, nm) > count(t_old, None, nm))
        if changed is not None:
            out.append(f"planned test {what} changed since the red run (at red line {changed.lineno})")
        elif again:
            out.append(f"planned test {what} has {again[0]} defined again since the red run")
    return out


_COMMENT = ("//", "#", "/*", "*", "--")


def _in_comment(line: str, pos: int) -> bool:
    """True when position `pos` of `line` is inside a comment: the line is a comment line, or a //, /*, # or --
    (the last two after a space or at the start) comes before `pos` outside any quoted string. A Rust attribute
    (#[...]) is not a comment."""
    if line.strip().startswith(_COMMENT) and not line.strip().startswith("#["):
        return True
    q, k = None, 0
    while k < pos:
        c = line[k]
        if q:
            if c == "\\":
                k += 1
            elif c == q:
                q = None
        elif c in "\"'`":
            q = c
        elif line.startswith(("//", "/*"), k) or (c == "#" and not line.startswith("#[", k)
                                                   or line.startswith("--", k)) and (k == 0 or line[k - 1].isspace()):
            return True
        k += 1
    return False


def _test_decls(lines, pat):
    """The indexes of each line, in a non-Python test file, that holds the case id outside any comment: either the
    test's own opening line (its name, as in it('[CP1-T01] ...', or Go's func TestCP1_T01) or an annotation directly
    above it (such as @DisplayName("[CP1-T01] ...")). A line holding the id only inside a comment is never counted."""
    out = []
    for i, line in enumerate(lines):
        m = pat.search(line)
        if m and not _in_comment(line, m.start()):
            out.append(i)
    return out


def _planned_name(line: str, pat):
    """The name of the test a line declares with a case id: the quoted name holding the id, or the identifier holding
    it, such as a Go TestCP1T01 (None when it has neither)."""
    q = next((q.group(0) for q in re.finditer(r"([\"'`])(?:\\.|(?!\1).)*\1", line) if pat.search(q.group(0))), None)
    if q is not None:
        return q
    m = pat.search(line)
    if not m:
        return None
    a, b = m.start(), m.end()
    while a and (line[a - 1].isalnum() or line[a - 1] in "_$"):
        a -= 1
    while b < len(line) and (line[b].isalnum() or line[b] in "_$"):
        b += 1
    return line[a:b] if re.fullmatch(r"[A-Za-z_$][\w$]*", line[a:b]) else None


def _text_frozen(old: str, new: str, ids) -> list[str]:
    """The structural freeze for a test file in any other language that holds planned case ids: the file as it was at
    the red run must be an exact prefix of the file now, so new tests and helpers may only be added at its end. An
    added part may not declare a planned test's name again (in a language where a later definition replaces an
    earlier one, it would stand in for the planned test). No language syntax is parsed."""
    pats = [(cid, _case_id_re(cid)) for cid in ids]
    lines = old.splitlines()
    names, decl_lines = [], set()
    for cid, pat in pats:
        decl = _test_decls(lines, pat) or [i for i, l in enumerate(lines) if pat.search(l)]
        decl_lines.update(decl)
        for i in decl:
            names.append((cid, _planned_name(lines[i], pat)))
    if not names:
        return []
    tail = new[len(old):]
    if not new.startswith(old) or (old and not old.endswith("\n") and tail and tail[0] not in "\r\n"):
        cur = new.splitlines()
        at = next((k for k, l in enumerate(lines) if k >= len(cur) or cur[k] != l), len(lines) - 1)
        listed = ", ".join(f"{n} ({cid})" if n else cid for cid, n in names)
        if decl_lines and at < min(decl_lines):
            # The first change sits above any planned test's own line: an ordinary insertion (a new import, a new
            # helper), not an edit to a planned test. Say so plainly instead of pointing at approve-changes.
            return [f"content above the end of the file changed since the red run (first change at red line "
                    f"{at + 1}; this file holds planned tests: {listed}). Move the added lines to the end of the "
                    f"file, or put a test that needs a new import in a new test file"]
        return [f"content above the end of the file changed since the red run (first change at red line {at + 1}; "
                f"this file holds planned tests: {listed}). Outside Python, a test file that holds planned tests "
                f"may only have lines added at its end. {_FROZEN_WHY}"]
    for cid, n in names:
        if not n:
            continue
        rx_ = (re.compile(r"[\"'`]" + re.escape(n[1:-1]) + r"[\"'`]") if n[0] in "\"'`" else
               re.compile(r"(?<![\w$])" + re.escape(n) + r"(?![\w$])"))
        if rx_.search(tail):
            return [f"planned test {n} ({cid}) is declared again in lines added since the red run (a later "
                    f"definition with the same name could replace it; give a new test its own case id and name)"]
    return []


def _frozen_test_problems(rel: str, old: str, new: str, ids) -> list[str]:
    """Why a test file's planned tests no longer match the red run (empty when they all still do)."""
    got = _py_frozen(old, new, ids) if rel.endswith(".py") else None
    return _text_frozen(old, new, ids) if got is None else got


def weakened_tests(ctx, st) -> list[str]:
    cur = st.get("current")
    cps = (st.get("checkpoints") or {}).get(cur) or {}
    if not cps.get("red_tests"):
        return []
    out, ids = [], [c["id"] for c in _planned_cases(ctx, cur)]
    d = ctx.run_dir() / "evidence" / cur / "red-tests"
    for rel in cps["red_tests"]:
        old_p, new_p = d / rel.replace("/", "__"), ctx.root / rel
        if not old_p.exists():
            continue
        old = old_p.read_text(errors="replace")
        new = new_p.read_text(errors="replace") if new_p.exists() else None
        if new is None:
            out.append(f"test file {rel} (lines changed or removed since the red run)")
            continue
        frozen = _frozen_test_problems(rel, old, new, ids)
        if frozen:
            # Non-Python messages (_text_frozen) already carry whatever guidance applies (the approve-changes
            # note only when a planned test itself is at risk); only Python messages (_py_frozen) need it added.
            out.append(f"test file {rel}: {frozen[0]}. {_FROZEN_WHY}" if rel.endswith(".py")
                        else f"test file {rel}: {frozen[0]}")
        elif not _additive(old, new):
            out.append(f"test file {rel} (lines changed or removed since the red run)")
    return out


def cmd_implemented(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["implement"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    cps["step"] = "behavior" if cp["gates"].get("behavior") else ("ui" if cp["gates"].get("ui") else "adversarial")
    ctx.log(st, "implemented", cp=args.cp)
    ctx.save_state(st)
    ok(f"{args.cp}: implementation recorded. Next: {cps['step']} gate.")


def cmd_behavior(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["behavior", "ui", "adversarial", "complete"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    if not cp["gates"].get("behavior"):
        die(f"{args.cp} has no behavior gate")
    cfg = ctx.config()
    g = cps["gates"]["behavior"]
    g["attempts"] += 1
    files, missing, _ = _coverage(ctx, cp)
    if missing:
        g["status"] = "failed"
        ctx.save_state(st)
        die(f"Behavior gate FAILED: planned cases were removed from tests: {', '.join(missing)}. "
            "Tests may not be deleted or weakened to pass the gate.")
    log = ctx.evidence(args.cp) / f"behavior-{g['attempts']:02d}.log"
    rpath = _report_path(ctx, cp)
    code, out, dur = _run_cmd(ctx, cp["tests"]["command"], log, cfg["test_timeout_sec"], rpath)
    bad = []
    if rpath:
        cases, why = _read_test_report(ctx, rpath)
        bad = [why] if why else _case_problems(ctx, cp, cases, red=False)[0]
    failed_check = None
    fp_tests = fingerprint(ctx.root)["id"] if code == 0 and not bad and cfg.get("checks") else None
    if code == 0 and not bad:
        for chk in cfg.get("checks") or []:
            if not isinstance(chk, dict) or not chk.get("command"):
                continue
            clog = ctx.evidence(args.cp) / f"check-{re.sub(r'[^a-z0-9]+', '-', str(chk.get('kind', 'x')))}-{g['attempts']:02d}.log"
            ccode, cout, _ = _run_cmd(ctx, chk["command"], clog, cfg["test_timeout_sec"])
            if ccode != 0:
                code, out, log, failed_check = ccode, cout, clog, chk
                break
    fp = fingerprint(ctx.root)
    if fp_tests and fp["id"] != fp_tests and not failed_check:
        g["status"] = "failed"
        ctx.save_state(st)
        die(f"Behavior gate FAILED for {args.cp}: an automatic check changed files in the project. Checks must only read "
            "the code; restore the files (git) and fix the check's command.", 2)
    if code != 0 and failed_check:
        g["status"] = "failed"
        g["last"] = {"exit": code, "log": str(log.relative_to(ctx.root)), "at": now(), "check": failed_check.get("kind")}
        if g["attempts"] >= cfg["max_behavior_attempts"]:
            _set_blocked(st, f"{args.cp}: behavior gate failed {g['attempts']} times — needs a human decision.")
        ctx.log(st, "behavior_failed", cp=args.cp, attempt=g["attempts"], check=failed_check.get("kind"))
        ctx.save_state(st)
        build_viewer(ctx)
        print(_tail(out))
        die(f"Behavior gate FAILED for {args.cp}: the automatic check '{failed_check.get('kind')}' "
            f"({failed_check.get('command')}) failed. Log: {log.relative_to(ctx.root)}. Give it to ratchet-implementer; "
            "the check stays as it is.", 2)
    if code != 0 or bad:
        g["status"] = "failed"
        g["last"] = {"exit": code, "log": str(log.relative_to(ctx.root)), "at": now()}
        if g["attempts"] >= cfg["max_behavior_attempts"]:
            _set_blocked(st, f"{args.cp}: behavior gate failed {g['attempts']} times — needs a human decision.")
        ctx.log(st, "behavior_failed", cp=args.cp, attempt=g["attempts"])
        ctx.save_state(st)
        build_viewer(ctx)
        print(_tail(out))
        die(f"Behavior gate FAILED for {args.cp} (exit {code}" + ("; " + "; ".join(bad) if bad else "") +
            f"). Log: {log.relative_to(ctx.root)}. "
            "Give the failure to ratchet-implementer (do NOT edit the tests to make them pass) and re-run.", 2)
    g.update(status="passed", fingerprint=fp, passed_at=now(),
             last={"exit": 0, "log": str(log.relative_to(ctx.root)), "at": now(), "duration": dur})
    cps["step"] = _earliest_open_step(cps)
    ctx.log(st, "behavior_passed", cp=args.cp, attempt=g["attempts"])
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"Gate 1 (behavior) PASSED for {args.cp}. Next step: {cps['step']}")


def _ui_verdict(review: dict, cfg) -> tuple[str, list]:
    """Recompute the UI verdict from the differences list (don't trust a self-reported PASS)."""
    if str(review.get("verdict", "")).upper() == "INVALID":
        return "INVALID", []
    blockers = []
    for d in review.get("differences", []) or []:
        sev = str(d.get("severity", "")).lower()
        fixable = d.get("fixable_in_code", True)
        if not fixable and sev in ("minor", "nit"):  # only minor/nit rendering quirks may be exempt
            continue
        if sev == "nit" and not cfg["ui_block_on_nits"]:
            continue
        blockers.append(d)
    return ("PASS" if not blockers else "FAIL"), blockers


MAX_REVIEW_BYTES = 8 * 1024 * 1024  # a reviewer's output or combined UI review larger than this is refused outright


def _review_round_dir(ctx, cp_id: str, kind: str, n: int) -> Path:
    """The one folder a review round's files must sit in: reviews/<CP>/adv-round-NN/ (adversarial) or
    reviews/<CP>/round-NN/ (UI)."""
    return ctx.run_dir() / "reviews" / cp_id / f"{'adv-round' if kind == 'adv' else 'round'}-{n:02d}"


def _read_round_review(ctx, path, folder: Path, what: str, hint: str = ""):
    """Check a review file's location FIRST, then read it exactly once from the opened file.

    Refuses a file whose real location (symlinks and `..` resolved) is not directly inside this round's own folder,
    before anything in it is read, so a file from anywhere else always gets the folder message (even if it is
    malformed or stale). The file is then opened inside that folder without following links (relative to the
    opened folder where the platform allows), checked through the open descriptor to be the same regular,
    single-link file that was located, and read from that descriptor. Returns (resolved path, bytes, fstat): every
    later check and the evidence copy use these bytes, so swapping the file after the check changes nothing."""
    try:
        rel = folder.relative_to(ctx.root).as_posix()
    except ValueError:
        rel = folder.as_posix()
    where = (f"{what} must be written directly inside this round's own folder, {rel}/ "
             f"({folder}). Run the review again with its output in that folder.{hint}")
    try:
        real = Path(os.path.realpath(str(path)))
        base = Path(os.path.realpath(str(folder)))
    except (OSError, ValueError):
        die(f"Can't resolve {path}. {where}")
    if os.path.normcase(str(real.parent)) != os.path.normcase(str(base)):
        die(f"{path} is not in this round's folder (it resolves to {real}). {where}")
    try:
        lst = os.lstat(str(real))
        base_st = os.stat(str(base))
    except OSError:
        die(f"Review not found: {path}. {where}")
    if not stat.S_ISREG(lst.st_mode) or lst.st_nlink > 1:
        die(f"{path} is a link to another file, not a review written here. {where}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_RDONLY | nofollow | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    changed = f"{path} changed while it was being checked (replaced, or turned into a link). {where}"
    dfd = fd = None
    try:
        if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
            dfd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY | nofollow)
            if not os.path.samestat(os.fstat(dfd), base_st):
                die(changed)
            fd = os.open(real.name, flags, dir_fd=dfd)
        else:  # e.g. Windows: no dir_fd; the descriptor identity check below still catches a swap
            fd = os.open(str(real), flags)
    except OSError as e:
        if fd is not None:
            os.close(fd)
        die(f"{changed} ({e.strerror or e})")
    finally:
        if dfd is not None:
            os.close(dfd)
    try:
        try:
            fst = os.fstat(fd)
        except OSError as e:
            die(f"Could not inspect {path} after opening it: {e.strerror or e}. {where}")
        if not stat.S_ISREG(fst.st_mode) or fst.st_nlink > 1 or not os.path.samestat(lst, fst):
            die(changed)
        if fst.st_size > MAX_REVIEW_BYTES:
            die(f"{path} is {fst.st_size} bytes, over the {MAX_REVIEW_BYTES}-byte review size limit. A real "
                f"reviewer output is never this large; trim it (or check it is the right file) and try again. {where}")
        chunks = []
        total = 0
        try:
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                total += len(b)
                if total > MAX_REVIEW_BYTES:
                    die(f"{path} grew past the {MAX_REVIEW_BYTES}-byte review size limit while it was being read "
                        f"(it changed size concurrently). {where}")
                chunks.append(b)
        except OSError as e:
            die(f"Could not read {path}: {e.strerror or e}. {where}")
    finally:
        os.close(fd)
    return real, b"".join(chunks), fst


def _parse_review(raw: bytes, path) -> dict:
    """Parse review bytes already read by _read_round_review (never re-read the file by name)."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        die(f"{path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        die(f"{path} must hold a JSON object (see schemas.md).")
    return data


_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def clean_review_text(v):
    """A parsed review with every lone surrogate (a reviewer's cut-off emoji escape, "\\ud83d") replaced by U+FFFD,
    in every string and key, so no later record, page or message fails to encode it. external_review.py cleans before
    it signs; adv-record and ui-record clean after the signature is checked."""
    if isinstance(v, str):
        return _SURROGATE_RE.sub("\ufffd", v)
    if isinstance(v, list):
        return [clean_review_text(x) for x in v]
    if isinstance(v, dict):
        return {clean_review_text(k): clean_review_text(x) for k, x in v.items()}
    return v


def _write_review_evidence(dst: Path, raw: bytes, fst):
    """Copy the review into evidence from the bytes that were checked, keeping the original timestamps."""
    try:
        dst.write_bytes(raw)
    except OSError as e:
        die(f"Could not write the evidence copy to {dst}: {e.strerror or e}. Check that the evidence folder "
            f"({dst.parent}) exists and is writable with room for the file, then try again.")
    try:
        os.utime(str(dst), ns=(fst.st_atime_ns, fst.st_mtime_ns))
    except OSError:
        pass


# ---- review signing ----------------------------------------------------------------------------------------------
# Every review round has its own secret, kept in Ratchet's per-user folder (never in the project, never printed) and
# removed once the round is recorded. external_review.py and ui_review.py sign what they write with it (HMAC-SHA256
# over the review's canonical JSON, the round's nonce or label, and the code fingerprint); adv-record and ui-record
# refuse a review that is unsigned or whose signature does not match. A review by the host's own reviewer sub-agent
# (ratchet-adversary, ratchet-visual-reviewer, ratchet-behavior-reviewer) is not signed: the guard hook records, in the
# same per-user record, who wrote the file. On Claude Code it names the sub-agent, and a file it did not see that
# sub-agent write with the Write tool is refused. On Codex, Gemini and Copilot, or with the hooks waived, the hook can't
# say who wrote it, so any file in the round folder with the right nonce and code version counts and is marked "not
# independently confirmed". The record is identified by the round itself (an adversarial round's own nonce; for a UI
# round, the run's creation time, its round number and how many UI rounds this step has recorded), so a record left
# by an abandoned run or an earlier round never speaks for a later one. This stops a different process on the same
# machine that has no access to the per-user folder (the guard keeps agents out of it); it is not an operating system
# boundary.

UNCONFIRMED = "not independently confirmed"
# Files the guard records the writer of: the REVIEW_OWNERS entries that name an owner sub-agent (an adversarial
# round's reviews, and a UI round's two per-role reviews). Their patterns carry named groups (slug, cp, n, name).
ADV_REVIEW_RE = re.compile(r"^runs/(?P<slug>[^/]+)/reviews/(?P<cp>[^/]+)/adv-round-(?P<n>\d+)/(?P<name>[^/]+\.json)$", re.I)
UI_ROLES = ("visual", "behavior")


def ui_round_number(st: dict, cp_id) -> int | None:
    """The UI round a checkpoint is on (one more than its recorded UI rounds), or None when it has no UI gate."""
    g = ((((st.get("checkpoints") or {}).get(cp_id) or {}).get("gates") or {}).get("ui")) or {}
    if not g.get("required"):
        return None
    return int(g.get("attempts") or 0) + 1


def _round_id(st: dict, cp_id: str, kind: str, n: int) -> str | None:
    """What makes this round unique, or None when there is no such round in this run."""
    cps = (st.get("checkpoints") or {}).get(cp_id) or {}
    if kind == "adv":
        rnd = cps.get("adv_round") or {}
        if rnd.get("nonce") and int(rnd.get("round") or 0) == int(n):
            return str(rnd["nonce"])
        return None
    g = (cps.get("gates") or {}).get("ui") or {}
    if kind == "ui" and g.get("required") and st.get("created_at"):
        return f"{st['created_at']}#{int(n)}#{int(g.get('rounds_recorded') or 0)}"
    return None


def review_key_file(root, st: dict, cp_id: str, kind: str, n: int) -> Path | None:
    """Where this round's signing record lives under Ratchet's per-user folder (None when there is no such round)."""
    rid = _round_id(st, cp_id, kind, n)
    if rid is None:
        return None
    ident = "|".join([os.path.realpath(str(root)), str(st.get("slug")), cp_id, kind, str(int(n)), rid])
    return ratchet_home() / "review-keys" / (hashlib.sha256(ident.encode()).hexdigest()[:32] + ".json")


def open_review_key(ctx, cp_id: str, kind: str, n: int, st: dict | None = None) -> tuple[dict | None, str]:
    """This round's signing record ({"secret", "writers"}), created when missing (a fresh secret and no writers).
    (None, why) when it can't be: why is "busy" (another Ratchet command holds the record) or a plain reason."""
    st = st if st is not None else ctx.state()
    p = review_key_file(ctx.root, st, cp_id, kind, n)
    if p is None:
        return None, f"{cp_id} has no open {'adversarial' if kind == 'adv' else 'UI'} review round {n} in this run"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(p.parent), 0o700)
        res = update_record(p, lambda d: KEEP if d.get("secret") else {"secret": secrets.token_hex(32), "writers": {}},
                            damaged=lambda _why: {})
        if res == "busy":
            return None, "busy"
        os.chmod(str(p), 0o600)
    except OSError as e:
        return None, f"it could not be written ({e})"
    key = review_key(ctx, cp_id, kind, n, st=st)
    return (key, "") if key else (None, f"it could not be read back from {p}")


def review_key_problem(why: str, retry: str) -> str:
    """The plain message for a round secret that could not be created (open_review_key's reason)."""
    if why == "busy":
        return ("Another Ratchet command is using this round's review-signing record right now. Wait a moment, then "
                f"{retry}.")
    return (f"Could not create this round's review-signing secret under {ratchet_home() / 'review-keys'}: {why}. "
            f"Make that folder writable, then {retry}.")


def review_key(ctx, cp_id: str, kind: str, n: int, create: bool = False, st: dict | None = None) -> dict | None:
    """This round's signing record, created when `create` and missing. None when there is none (or it can't be read
    or written: a review then can't be signed, and is refused, which is the safe side)."""
    if create:
        return open_review_key(ctx, cp_id, kind, n, st)[0]
    p = review_key_file(ctx.root, st if st is not None else ctx.state(), cp_id, kind, n)
    if p is None:
        return None
    data, _why = _read_record(p, missing=None)
    return data if isinstance(data, dict) and data.get("secret") else None


def drop_review_key(p: Path | None):
    """Remove a round's record (from review_key_file, taken before the state changes) once the round is recorded and
    saved, or replaced, so its signatures and writer records can't be reused."""
    if p is None:
        return
    for f in (p, _record_lock_path(p)):
        try:
            f.unlink()
        except OSError:
            pass


def ui_round_label(cp_id: str, n: int) -> str:
    return f"{cp_id}/ui/round-{int(n):02d}"


def _review_mac(secret: str, review: dict, label: str) -> str:
    body = {k: v for k, v in review.items() if k != "signature"}
    # ASCII-escaped canonical JSON: a reviewer's lone surrogate ("\ud83d") can't make the encoding fail
    payload = "\n".join([json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True), str(label),
                         str(review.get("fingerprint"))])
    return hmac.new(secret.encode(), payload.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()


def sign_review(key: dict, review: dict, label: str, by: str) -> dict:
    review.pop("signature", None)
    review["signature"] = {"alg": "hmac-sha256", "by": by, "value": _review_mac(key["secret"], review, label)}
    return review


def signature_well_formed(sig) -> bool:
    """A signature shaped like one sign_review makes. Anything else is an incidental field, not a signing attempt."""
    return (isinstance(sig, dict) and sig.get("alg") == "hmac-sha256" and isinstance(sig.get("value"), str)
            and re.fullmatch(r"[0-9a-f]{64}", sig["value"]) is not None)


def review_signed(key: dict | None, review: dict, label: str) -> bool:
    sig = review.get("signature")
    if not key or not signature_well_formed(sig):
        return False
    return hmac.compare_digest(sig["value"], _review_mac(key["secret"], review, label))


def ui_manifest_label(cp_id: str, n: int) -> str:
    return f"{ui_round_label(cp_id, n)}/manifest"


def stamp_ui_manifest(ctx, st: dict, cp_id: str, n: int, key: dict, manifest: dict) -> str:
    """`ui_review.py review` / `stage`, before writing the capture manifest for this UI round: give it a fresh stamp,
    make that the round's current one (`manifest_stamp` in the round's record), and sign the manifest with the round's
    secret. Per-role reviews written for an earlier stamp stop counting. '' on success, else why (as open_review_key)."""
    p = review_key_file(ctx.root, st, cp_id, "ui", n)
    if p is None:
        return f"{cp_id} has no open UI review round {n} in this run"
    stamp = secrets.token_hex(16)

    def put(d):
        if not d.get("secret"):
            return KEEP
        d["manifest_stamp"] = stamp
        return d
    try:
        res = update_record(p, put)
    except OSError as e:
        return f"it could not be written ({e})"
    if res != "written":
        return "busy" if res == "busy" else f"its record could not be updated ({res})"
    key["manifest_stamp"] = stamp
    manifest.pop("signature", None)
    manifest["stamp"] = stamp
    sign_review(key, manifest, ui_manifest_label(cp_id, n), "ui_review.py")
    return ""


def ui_manifest_problem(key: dict | None, cp_id: str, n: int, man) -> str:
    """Why a capture manifest given to `ui_review.py combine` does not count for this UI round ('' when it does)."""
    if not isinstance(man, dict) or not review_signed(key, man, ui_manifest_label(cp_id, n)):
        return (f"it was not written by `ui_review.py review` or `stage` for {cp_id}'s UI round {n}; a capture manifest "
                "typed, copied or edited by hand, or one from another round, does not count. Run `ui_review.py review` "
                "(or `stage`) on the capture manifest, then have the reviewers review again")
    if man.get("stamp") != (key or {}).get("manifest_stamp"):
        return ("`ui_review.py review` or `stage` has stamped a newer capture manifest for this round since this one; "
                "use the manifest they wrote last, with reviews written after it")
    return ""


STALE_UI_INPUT = ("it was written for an earlier capture manifest (`ui_review.py review` or `stage` has stamped the "
                  "manifest again since), so it may be a review of other code; have that reviewer review again")


def _review_file_match(rel: str | None):
    """(kind, match, owner sub-agent) for a review file whose writer the guard records (a REVIEW_OWNERS entry that
    names an owner), or None."""
    for rx, owners in REVIEW_OWNERS:
        m = rx.match(rel or "")
        if m and owners:
            return ("adv" if rx is ADV_REVIEW_RE else "ui"), m, sorted(owners)[0]
    return None


def _note_writer_failure(root: Path, host: str, rel: str, detail: str):
    """A review file's writer could not be recorded: logged where hook failures go (hook-errors.log), so the later
    refusal and `doctor` can say why. Never raises (the hook fails open)."""
    try:
        _append_record(root / RATCHET_DIR / "hook-errors.log",
                       f"{now()} {host} guard: ReviewWriterNotRecorded: {rel}: {detail}\n")
    except Exception:
        pass


def _note_review_writer(n: dict):
    """Guard hook: remember who wrote a review file into an open review round's folder (Claude Code names the
    sub-agent; other CLIs don't, so their writes are recorded as not confirmed). Never breaks the hook; a write it
    could not record is logged to hook-errors.log."""
    host = n["host"]
    actor = (n.get("agent_type") or "").split(":")[-1]
    for p in n["paths"]:
        rel = root = hit = None
        try:
            rel = _ratchet_rel(p, n["cwd"])
            hit = _review_file_match(rel)
            if not hit:
                continue
            kind, m, owner = hit
            full = Path(os.path.normpath(os.path.join(n["cwd"], os.path.expanduser(p))))
            root = find_root(full.parent)
            st, _why = _read_record(root / RATCHET_DIR / "runs" / m.group("slug") / "state.json", missing=None)
            kp = review_key_file(root, st, m.group("cp"), kind, int(m.group("n"))) if isinstance(st, dict) else None
            if kp is None or not kp.exists():
                continue  # no open round with a record: nothing could ever be confirmed against it
            mine = _host_names_subagent(host) and actor == owner

            def add(data, name=m.group("name"), mine=mine, kind=kind):
                if not data.get("secret"):
                    return KEEP
                prev = (data.get("writers") or {}).get(name) or {}
                w = {"host": host, "agent": actor or None, "at": now(),
                     "confirmed": bool(mine and prev.get("confirmed", True))}
                other = prev.get("other_writer") or (None if mine else (actor or "main session"))
                if other:
                    w["other_writer"] = other
                if kind == "ui":  # the capture manifest it was written against: combine refuses an older one
                    w["manifest_stamp"] = data.get("manifest_stamp")
                data.setdefault("writers", {})[name] = w
                return data
            res = update_record(kp, add)
            if res in ("busy", "damaged"):
                _note_writer_failure(root, host, rel, f"its round's record was {res}")
        except Exception as e:
            if not hit:
                continue  # not a review file: there was no writer to record
            if root is None:
                try:
                    root = _ratchet_root_of(n.get("cwd"))
                except Exception:
                    root = None
            if root is not None:
                _note_writer_failure(Path(root), host, str(rel or p), f"{type(e).__name__}: {e}")


def host_writer_origin(ctx, cfg, st: dict, key: dict | None, name: str, owner: str) -> tuple[str, str]:
    """How an unsigned file written by a host reviewer sub-agent (`owner`) is known to be real: ('confirmed', ''),
    ('unconfirmed', why) or ('refused', why)."""
    w = ((key or {}).get("writers") or {}).get(name)
    if w:
        if w.get("confirmed"):
            return "confirmed", ""
        if _host_names_subagent(w.get("host") or ""):
            other = w.get("other_writer") or w.get("agent")
            who = "the main session (not a sub-agent)" if other in (None, "main session") else f"the {other} sub-agent"
            return "refused", f"Ratchet's guard saw it written or edited by {who}, not only by a {owner} sub-agent"
        return "unconfirmed", "this CLI's hook does not say which sub-agent wrote it"
    if _waiver_applies(ctx.root, cfg, st):
        return "unconfirmed", "Ratchet's hooks are waived for this run, so nothing recorded who wrote it"
    if not _host_names_subagent(VENDOR_TO_HOST.get(detect_host(cfg), "unknown")):
        return "unconfirmed", ("this CLI's hook did not record who wrote it; on this CLI any file in the round folder "
                               "with the right nonce and code version counts")
    return "refused", (f"Ratchet's guard did not see a {owner} sub-agent write it with the Write tool in this round "
                       "(a file written through the shell, or by the main session, is not recorded)")


def _writer_failure_note(root: Path, name: str) -> str:
    hits = [e for e in read_hook_errors(root) if "ReviewWriterNotRecorded" in e["message"] and name in e["message"]]
    return (" The guard could not record who wrote this file (see hook-errors.log in the project's Ratchet folder, "
            "and `ratchet.py doctor`)." if hits else "")


def check_review_origin(ctx, cfg, st: dict, cp_id: str, n: int, rp, rpath: Path, r: dict, rid: str,
                        label: str) -> tuple[str, str]:
    """How an adversarial review is known to be real: ('script-signed', ''), ('confirmed', '') for a host sub-agent
    the guard named, or ('unconfirmed', why) where the guard could not name the writer (Codex, Gemini, Copilot, or
    hooks waived). Dies when it is none of these."""
    key = review_key(ctx, cp_id, "adv", n, st=st)
    if signature_well_formed(r.get("signature")):
        if key is None:
            die(f"{rp} ({rid}) is signed, but this round's signing secret is gone, so no signature can be checked. Run "
                f"`ratchet.py reviewers {cp_id}` and run its reviewers again.")
        if review_signed(key, r, label):
            return "script-signed", ""
        die(f"{rp} ({rid}) has a signature that does not match this round: it was changed after it was signed, or it "
            f"was signed for another round. An edited or copied review does not count; run the reviewer again "
            f"(see `ratchet.py reviewers {cp_id}`).")
    why = "it has no signature from external_review.py for this round"
    if (_roster_spec(cfg, rid) or {}).get("kind") == "agent":
        origin, why = host_writer_origin(ctx, cfg, st, key, rpath.name, "ratchet-adversary")
        if origin != "refused":
            return origin, why
    die(f"{rp} ({rid}) is unsigned and does not count: {why}. Ratchet accepts a review signed by external_review.py "
        "for this round, or one the host's ratchet-adversary sub-agent wrote with the Write tool (not through the "
        "shell) while Ratchet's hooks run. A review typed, copied or edited by hand does not count. Launch the "
        f"reviewer again as `ratchet.py reviewers {cp_id}` says.{_writer_failure_note(ctx.root, rpath.name)}")


def ui_input_origin(ctx, cfg, st: dict, key: dict | None, cp_id: str, n: int, path: Path, r: dict) -> tuple[str, str]:
    """How a per-role UI review given to `ui_review.py combine` is known to be real: ('script-signed', '') when
    `ui_review.py review` signed it for this round, ('confirmed', '') or ('unconfirmed', why) for a host reviewer
    sub-agent's file in this round's folder, or ('refused', why)."""
    role = r.get("reviewer")
    current = (key or {}).get("manifest_stamp")
    if signature_well_formed(r.get("signature")):
        if role in UI_ROLES and review_signed(key, r, f"{ui_round_label(cp_id, n)}/{role}"):
            return ("script-signed", "") if r.get("manifest_stamp") == current else ("refused", STALE_UI_INPUT)
        return "refused", ("its signature does not match this UI round: it was changed after ui_review.py signed it, or "
                           "it was signed for another round")
    folder = _review_round_dir(ctx, cp_id, "ui", n)
    hit = None
    try:
        real = Path(os.path.realpath(str(path)))
        if real.parent == Path(os.path.realpath(str(folder))):
            hit = _review_file_match(real.relative_to(Path(os.path.realpath(str(ctx.ratchet)))).as_posix())
    except (OSError, ValueError):
        real = Path(str(path))
    if not hit or hit[0] != "ui":
        return "refused", (f"it is not signed by `ui_review.py review`, and it is not a reviewer's visual.json or "
                           f"behavior.json in this round's folder ({folder})")
    if role != real.stem.lower():
        return "refused", f"its reviewer field says {role!r}, but {real.name} is the {real.stem.lower()} reviewer's file"
    w = ((key or {}).get("writers") or {}).get(real.name)
    if w and w.get("manifest_stamp") != current:
        return "refused", STALE_UI_INPUT
    return host_writer_origin(ctx, cfg, st, key, real.name, hit[2])


def cmd_ui_record(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["ui", "adversarial", "complete"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    if not cp["gates"].get("ui"):
        die(f"{args.cp} has no UI gate")
    cfg = ctx.config()
    g = cps["gates"]["ui"]
    n = g["attempts"] + 1
    review_path, raw, fst = _read_round_review(ctx, args.review, _review_round_dir(ctx, args.cp, "ui", n),
                                               f"The combined UI review for round {n}")
    review = _parse_review(raw, args.review)
    for key in ("reviewers", "differences"):
        if key not in review:
            die(f"combined review must contain '{key}' (see ratchet-ui-compare skill)")
    if len(review.get("reviewers", [])) < 2 and not cfg.get("ui_single_reviewer_ok"):
        die("UI gate needs both a visual and a behaviour reviewer in the combined review.")
    if review.get("checkpoint") not in (None, args.cp):
        die(f"combined review is for {review.get('checkpoint')}, not {args.cp}.")
    cur_fp = fingerprint(ctx.root)["id"]
    if review.get("fingerprint") != cur_fp:
        die("combined review is not stamped with the current code fingerprint (it was produced for other code, or by "
            "hand). Capture and review again with ui_review.py — it stamps the fingerprint itself.")
    ui_key = review_key(ctx, args.cp, "ui", n, st=st)
    if signature_well_formed(review.get("signature")) and ui_key is None:
        die(f"{args.review} is signed, but UI round {n} has no signing secret (ui_review.py has not opened this round, "
            "or its secret was removed), so the signature can't be checked and the review does not count; one signed "
            "for another round never does. Run ui_review.py (`review`, or `stage` and `combine`) for this round.")
    if not review_signed(ui_key, review, ui_round_label(args.cp, n)):
        die(f"{args.review} is unsigned, or was changed after ui_review.py signed it for round {n}. Only a combined "
            "review written by ui_review.py (`review`, or `combine`) for this round counts; a combined.json typed or "
            "edited by hand does not.")
    review = clean_review_text(review)  # its signature is checked: from here on it only feeds records and messages
    compared = review.get("compared") or {}
    if not compared.get("spec_sha256") or compared.get("scope") is None:
        die("The combined review doesn't say what was compared (capture spec and scope). Produce it with "
            "ui_review.py from a capture.mjs manifest: `review`, or `combine --manifest <manifest.json>`.")
    spec_file = ctx.run_dir() / "states" / f"{args.cp}.json"
    if spec_file.exists() and hashlib.sha256(spec_file.read_bytes()).hexdigest() != compared["spec_sha256"]:
        die(f"states/{args.cp}.json changed after this capture. Capture and review again.")
    frozen = g.get("frozen")
    if frozen and frozen != compared:
        die(f"The UI check found differences in {args.cp}, and since then the capture spec (states/{args.cp}.json) "
            "or the review scope has changed. Differences are fixed in the app's code, not by changing what is "
            "compared. Put the spec and scope back as they were and re-run the capture. If the spec itself was "
            f"wrong, explain to the user what you want to change and why, run `ratchet.py ask ui-respec {args.cp}`, "
            "end your message with the question it prints, word for word, as the only question in it, and end your "
            f"turn. Run `ratchet.py ui-respec {args.cp}` only if their first reply after it is one of the listed yes "
            "replies.")
    key_file = review_key_file(ctx.root, st, args.cp, "ui", n)  # dropped only once this round is saved as recorded
    g["attempts"] += 1
    g["rounds_recorded"] = int(g.get("rounds_recorded") or 0) + 1  # never reset: a later round never reuses a record
    unconfirmed = [x for x in review.get("unconfirmed") or [] if isinstance(x, str)]  # signed with the review
    if unconfirmed:
        g["unconfirmed"] = unconfirmed
    else:
        g.pop("unconfirmed", None)
    verdict, blockers = _ui_verdict(review, cfg)
    dest = ctx.evidence(args.cp) / "ui" / f"round-{g['attempts']:02d}"
    dest.mkdir(parents=True, exist_ok=True)
    if review_path.parent != dest:
        _write_review_evidence(dest / "combined.json", raw, fst)
    if verdict == "INVALID":
        g["status"] = "invalid"
        ctx.log(st, "ui_invalid", cp=args.cp, reason=review.get("invalid_reason"))
        ctx.save_state(st)
        drop_review_key(key_file)  # this round is recorded and saved: its secret goes
        die(f"UI comparison INVALID ({review.get('invalid_reason', 'states did not match')}). "
            "Re-capture prototype and app in the SAME state and review again.", 2)
    if verdict == "FAIL":
        g["status"] = "failed"
        g["blockers"] = len(blockers)
        g["frozen"] = compared  # from now on only the app's code may change until the gate passes
        if g["attempts"] >= cfg["max_ui_rounds"]:
            _set_blocked(st, f"{args.cp}: UI gate failed {g['attempts']} rounds — needs a human decision.")
        ctx.log(st, "ui_failed", cp=args.cp, blockers=len(blockers))
        ctx.save_state(st)
        drop_review_key(key_file)
        build_viewer(ctx)
        for b in blockers:
            print(f"  - [{b.get('severity')}] {b.get('location')}: {b.get('description')}")
        die(f"Gate 2 (UI) FAILED for {args.cp}: {len(blockers)} blocking difference(s). "
            "Send them to ratchet-implementer, then re-run behavior + UI.", 2)
    g.update(status="passed", fingerprint=fingerprint(ctx.root), passed_at=now(),
             evidence=str(dest.relative_to(ctx.root)), blockers=0)
    g.pop("frozen", None)
    cps["step"] = _earliest_open_step(cps)
    ctx.log(st, "ui_passed", cp=args.cp, round=g["attempts"])
    ctx.save_state(st)
    drop_review_key(key_file)
    build_viewer(ctx)
    ok(f"Gate 2 (UI) PASSED for {args.cp}. Next step: {cps['step']}")


def cmd_ui_respec(args):
    """The user agreed that the UI check itself (capture spec or scope) was wrong: allow one change to it."""
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    g = cps["gates"]["ui"]
    if not g.get("frozen"):
        ok(f"{args.cp}'s UI check isn't frozen; the spec can change freely until it finds a difference.")
        return
    _require_pending(st, "ui-respec", args.cp)
    msg = _require_human(ctx, st, "ui-respec", getattr(args, "director_token", None))
    g.pop("frozen", None)
    _clear_pending(st)
    ctx.log(st, "ui_respec_approved", cp=args.cp, by=msg["by"], note=msg.get("prompt", "")[:200])
    ctx.save_state(st)
    ok(f"{args.cp}: the capture spec and scope may be changed once; the next recorded round becomes the new baseline.")


def _dedupe_finding_ids(findings: list) -> list:
    """Gives every finding from ONE reviewer a usable, unique id before `reviewer` is stamped on it: a finding
    with no `id`, or one whose `id` repeats an earlier finding's from the SAME reviewer (two independent
    reviewers sharing an id is normal and handled elsewhere, by `_finding_key`'s 'reviewer/id'; this is about one
    reviewer's own list carrying a duplicate or a blank id), gets a synthetic one instead
    (`<id-or-'F'>-dup<n>`, counting only the repeats, so the first of each id is left exactly as the reviewer
    wrote it). The synthetic id is never just assumed unique: it is checked against every explicit (non-blank) id
    anywhere else in the SAME reviewer's list, and against every synthetic id already handed out, bumping the
    counter until it really is new. Without this, `_finding_key` could still collide for two DIFFERENT findings
    of the same reviewer (a blank id's synthetic default happening to match an id the reviewer wrote by hand,
    e.g. a blank id next to an explicit 'F'): the one the user was shown and asked about, and one they never
    saw, so accepting the shown one would silently also clear the other (and a blank id could never be named
    with `accept-finding` at all)."""
    explicit_ids = {f.get("id") for f in findings if f.get("id") not in (None, "")}
    seen: dict = {}
    assigned: set = set()
    out = []
    for f in findings:
        fid = f.get("id")
        key = fid if fid not in (None, "") else "\x00missing"
        seen[key] = seen.get(key, 0) + 1
        n = seen[key]
        if fid in (None, "") or n > 1:
            base = str(fid) if fid not in (None, "") else "F"
            candidate = base if (fid in (None, "") and n == 1) else f"{base}-dup{n}"
            while candidate in explicit_ids or candidate in assigned:
                n += 1
                candidate = f"{base}-dup{n}"
            assigned.add(candidate)
            f = dict(f, id=candidate)
        out.append(f)
    return out


def _checkin_state(cps) -> dict:
    """The checkpoint's own check-in record (state.json, never a separate file): whether each of the two triggers
    has already fired once for this step, the running count of rounds that hit the 'new kind in an already-flagged
    file' condition, the check-in that is currently open (or None), and every past decision. `cps` is the same
    dict object held inside `state.json`'s `checkpoints`, so mutating what this returns and saving state is enough;
    nothing here is written as its own file."""
    return cps.setdefault("checkin", {"round_count_fired": False, "new_kind_fired": False,
                                      "new_kind_repeats": 0, "open": None, "decided": []})


def _checkin_trigger(ctx, st, cp_id, cps, g, rnd, blocking_findings) -> str | None:
    """Call right after a round is recorded as still blocking (g['rounds'] already carries this round's own entry,
    last in the list). Opens a check-in (see cmd_check_in) at most once per trigger per step: either this is the
    `checkin_round`'th (default 2) round in a row to block, or this is the SECOND round where a finding kind never
    seen before in this step shows up in a file an earlier round already had a blocking finding in. Returns a short
    note for the adv-record refusal message when one just opened, else None. Mutates `cps` in place; the caller
    still has to `ctx.save_state(st)`."""
    ci = _checkin_state(cps)
    if ci.get("open"):
        return None  # already waiting on an earlier check-in; don't stack a second one on top
    cfg = ctx.config()
    # New-kind events are counted every round, whichever trigger (if any) ends up opening a check-in this round;
    # otherwise a round that also satisfies the round-count trigger would never have its own new-kind event
    # counted, undercounting new_kind_repeats for a later round.
    prior_blocking_files: set = set()
    for rr in (g.get("rounds") or [])[:-1]:
        prior_blocking_files |= set(rr.get("blocking_files") or [])
    seen_kinds_before = {k for k, hits in (st.get("finding_kinds") or {}).items()
                         if any(h.get("cp") == cp_id and h.get("round") is not None and h["round"] < rnd["round"]
                                for h in hits)}
    new_kind_hit = any(
        finding_kind(f) and finding_kind(f) not in seen_kinds_before
        and _norm_path(f.get("file") or f.get("path") or f.get("location"), ctx.root) in prior_blocking_files
        for f in blocking_findings)
    if new_kind_hit:
        ci["new_kind_repeats"] = int(ci.get("new_kind_repeats", 0)) + 1
    trigger = None
    consecutive_blocking = 0
    for rr in reversed(g.get("rounds") or []):
        if rr.get("findings", 0) > 0:
            consecutive_blocking += 1
        else:
            break  # an approved round (no blocking findings) resets the consecutive count
    if not ci.get("round_count_fired") and consecutive_blocking >= int(cfg.get("checkin_round", 2)):
        trigger = "round_count"
    elif new_kind_hit and not ci.get("new_kind_fired") and ci["new_kind_repeats"] >= 2:
        trigger = "new_kind_repeat"
    if not trigger:
        return None
    ci["open"] = {"round": rnd["round"], "trigger": trigger, "opened_at": now()}
    ci["round_count_fired" if trigger == "round_count" else "new_kind_fired"] = True
    return f"A check-in opened (reviews have been dragging): run `ratchet.py check-in {cp_id}` before fixing more."


def _checkin_all_open_findings(ctx, cp_id, cps, g) -> list:
    """The still-open (not accepted) findings for the check-in brief: the latest recorded round's own
    open-findings.json, the same current view `reviewers` and the fixer already use (`_adv_open_findings`). A
    finding fixed or withdrawn by a later round never reappears here, and a reviewer reusing an id (e.g. `A1`) for
    a different problem in a later round is never confused with an earlier round's now-resolved one, because only
    the latest round's file is read."""
    return _adv_open_findings(ctx, cp_id, cps.get("adv_round"), cps)


def _checkin_kinds_by_file(ctx, findings: list) -> dict:
    out: dict = {}
    for f in findings:
        where = _norm_path(f.get("file") or f.get("path") or f.get("location"), ctx.root) or "(unknown file)"
        kinds = out.setdefault(where, [])
        k = finding_kind(f) or "?"
        if k not in kinds:
            kinds.append(k)
    return out


CHECKIN_OPTIONS = [
    {"letter": "A", "text": "Simplify: narrow or change the step so the review notes stop applying; record why "
                            "with `ratchet.py check-in CPn --decision simplify --why \"...\"` (no user question)."},
    {"letter": "B", "text": "Accept the open findings as a known limit: only the user's own reply through `ask "
                            "accept-finding` / `accept-finding` does this; check-in can't accept anything itself."},
    {"letter": "C", "text": "Stop here and ask the user how to wrap up (`ratchet.py check-in CPn --decision stop`)."},
]


def _checkin_brief(ctx, plan, cp_id, cps, g, ci) -> dict:
    open_ci = ci.get("open") or {}
    findings = _checkin_all_open_findings(ctx, cp_id, cps, g)
    obj = plan.get("objective") if isinstance(plan, dict) else None
    return {"checkpoint": cp_id, "round": open_ci.get("round"), "trigger": open_ci.get("trigger"),
            "rounds_used": len(g.get("rounds") or []),
            "objective": obj if isinstance(obj, dict) and obj else NO_OBJECTIVE_NOTE,
            "findings": [{"id": _finding_key(f), "severity": f.get("severity"),
                          "file": f.get("file") or f.get("path") or f.get("location"), "kind": finding_kind(f),
                          "description": f.get("description"), "user_impact": f.get("user_impact")}
                         for f in findings],
            "kinds_by_file": _checkin_kinds_by_file(ctx, findings),
            "options": CHECKIN_OPTIONS, "decided": ci.get("decided") or []}


def _print_checkin_brief(brief: dict):
    print(f"Check-in for {brief['checkpoint']} (round {brief['round']}, rounds used: {brief['rounds_used']}, "
          f"trigger: {brief['trigger']}).")
    obj = brief["objective"]
    if isinstance(obj, dict):
        print(f"Who it's for: {obj.get('users', '')}")
        print(f"What they want: {obj.get('goal', '')}")
    else:
        print(f"Note: {obj}.")
    print("Open findings:")
    for f in brief["findings"]:
        line = f"  - {f['id']} ({f.get('severity', '?')}) {f.get('file') or ''}: {f.get('description', '') or ''}"
        print(line.rstrip())
        if f.get("user_impact"):
            print(f"      user impact: {f['user_impact']}")
    if brief["kinds_by_file"]:
        print("Finding kinds by file:")
        for fl, kinds in brief["kinds_by_file"].items():
            print(f"  - {fl}: {', '.join(kinds)}")
    print("What next:")
    for o in brief["options"]:
        print(f"  {o['letter']}) {o['text']}")


def cmd_check_in(args):
    """`ratchet.py check-in CPn` (optionally `--decision simplify|accept|stop [--why "..."]`, `--json`). With no
    decision: prints (or returns as JSON) the open check-in's brief and changes nothing. `simplify` is recorded
    from the reasoning given in `--why` (no user question; the skill requires that reasoning come from a fresh
    strong-model consult, not the orchestrator's own continued reasoning, but this command itself only records
    whatever `--why` carries); `accept` is always refused, pointing at the accept-finding flow (CP3), which needs
    the user's own reply; `stop` hands the run to the existing blocked/ask flow for the user to decide how to wrap
    up."""
    ctx = Ctx(find_root())
    st = ctx.state()
    plan = ctx.plan() or {}
    cp_id = args.cp
    cps = (st.get("checkpoints") or {}).get(cp_id)
    if cps is None:
        die(f"No such checkpoint {cp_id}.")
    ci = _checkin_state(cps)
    g = ((cps.get("gates") or {}).get("adversarial")) or {}
    decision = getattr(args, "decision", None)
    if decision is None:
        if not ci.get("open"):
            if getattr(args, "json", False):
                print(json.dumps({"checkpoint": cp_id, "open": False}, indent=2))
            else:
                print(f"No check-in is open for {cp_id}.")
            return
        brief = _checkin_brief(ctx, plan, cp_id, cps, g, ci)
        if getattr(args, "json", False):
            print(json.dumps(brief, indent=2))
        else:
            _print_checkin_brief(brief)
        return
    open_ci = ci.get("open")
    if not open_ci:
        die(f"No check-in is open for {cp_id}. Nothing to decide.")
    decision = decision.strip().lower()
    if decision == "accept":
        die("check-in cannot accept an open finding itself: a cooperative agent must not wave its own findings "
            f"through here. That needs the user's own reply, through `ratchet.py ask accept-finding {cp_id} <ids>` "
            "then `ratchet.py accept-finding` (CP3's own flow). Nothing was changed.")
    if decision == "simplify":
        why = (getattr(args, "why", None) or "").strip()
        if not why:
            die("check-in --decision simplify needs --why \"...\" explaining the reasoning. Nothing was recorded.")
        ci.setdefault("decided", []).append({"round": open_ci["round"], "trigger": open_ci["trigger"],
                                             "decision": "simplify", "why": why, "decided_at": now()})
        ci["open"] = None
        ctx.log(st, "checkin_simplify", cp=cp_id, round=open_ci["round"], why=why[:300])
        ctx.save_state(st)
        ok(f"Recorded: simplify ({why}). `ratchet.py reviewers {cp_id}` can open the next round now.")
        return
    if decision == "stop":
        ci.setdefault("decided", []).append({"round": open_ci["round"], "trigger": open_ci["trigger"],
                                             "decision": "stop", "decided_at": now()})
        ci["open"] = None
        _set_blocked(st, f"{cp_id}: a check-in paused the review loop after reviews kept dragging — needs the "
                         "user's decision on how to wrap up.")
        ctx.log(st, "checkin_stop", cp=cp_id, round=open_ci["round"])
        ctx.save_state(st)
        ok("Recorded: stop. The run now waits for the user to say how to wrap up.")
        return
    die("check-in --decision must be simplify, accept or stop.")


TRIAGE_DECISIONS = {"fix", "rebut"}
TRIAGE_REDIRECT = {"accept", "defer", "waive", "skip"}


def _read_triage(dest: Path) -> dict:
    return read_json(dest / "triage.json", {}, record="Ratchet treats a damaged triage file as empty")


def cmd_triage(args):
    """`ratchet.py triage CPn <ref>=fix:<reason> ...` or `<ref>=rebut:<reason> ...`, any number of decisions in one
    call, `ref` being a finding's key from the checkpoint's latest recorded (blocked) round's open findings (a bare
    id, or 'reviewer/id' when a bare id is ambiguous — the same keys `open-findings.json` lists). Only fix or rebut
    are accepted: the orchestrator can never defer, accept or waive a finding through triage, since that needs the
    user's own reply through the accept-finding flow (CP3). An empty reason is refused. Writes the round's own
    triage.json (every decision ever recorded for this round; a later call may change one) and fix-list.json (only
    the findings decided 'fix', for the fixer)."""
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = (st.get("checkpoints") or {}).get(args.cp)
    if cps is None:
        die(f"No such checkpoint {args.cp}.")
    rnd = cps.get("adv_round")
    if not rnd or not rnd.get("recorded"):
        die(f"{args.cp} has no recorded blocking round to triage yet. Run `ratchet.py reviewers {args.cp}` and "
            "record a review first.")
    open_findings = _adv_open_findings(ctx, args.cp, rnd, cps)
    by_key = {_finding_key(f): f for f in open_findings}
    if not by_key:
        die(f"{args.cp}'s latest recorded round has no open findings to triage.")
    dest = _adv_round_dir(ctx, args.cp, rnd["round"])
    triage = _read_triage(dest)
    changed = []
    for item in args.decisions:
        ref, sep, rest = item.partition("=")
        ref = ref.strip()
        if not sep:
            die(f"{item!r}: use <id>=fix:<reason> or <id>=rebut:<reason>. Nothing was recorded.")
        # Resolved the same way accept-finding resolves a ref (`_resolve_finding_ref`): a bare id unique among this
        # round's open findings works directly; a bare id two reviewers both used is refused as ambiguous, naming
        # them, instead of silently failing the "not open" check below (by_key is keyed by the qualified
        # 'reviewer/id', so a bare ref never matched it on its own).
        f, err = _resolve_finding_ref(ref, open_findings)
        if err:
            die(f"{err} Nothing was recorded.")
        if f is None:
            die(f"{ref} is not an open finding of {args.cp}'s latest recorded round (open now: "
                f"{', '.join(sorted(by_key)) or '(none)'}). Nothing was recorded for it.")
        key = _finding_key(f)
        decision, _, reason = rest.partition(":")
        decision = decision.strip().lower()
        reason = reason.strip()
        if decision in TRIAGE_REDIRECT:
            die(f"{ref}={decision} is refused: the orchestrator can never {decision} a finding through triage. "
                f"That needs the user's own reply, through `ratchet.py ask accept-finding {args.cp} {ref}` and "
                "their `accept: <reason>`. Nothing was recorded.")
        if decision not in TRIAGE_DECISIONS:
            die(f"{ref}={decision or '(none)'}: triage decisions are 'fix' or 'rebut' only. Nothing was recorded.")
        if not reason:
            die(f"{ref}={decision} needs a one-line reason after ':' (e.g. {ref}={decision}:why this is, or is "
                "not, a real problem for the objective's users). Nothing was recorded for it.")
        triage[key] = {"decision": decision, "reason": reason, "at": now()}
        changed.append(key)
    write_json(dest / "triage.json", triage)
    fix_list = [dict(by_key[k], triage_reason=v["reason"]) for k, v in triage.items()
                if v.get("decision") == "fix" and k in by_key]
    write_json(dest / "fix-list.json", fix_list)
    ctx.log(st, "triage_recorded", cp=args.cp, round=rnd["round"], ids=changed)
    ctx.save_state(st)
    ok(f"Recorded triage for {', '.join(changed)} ({args.cp} round {rnd['round']}).")


def cmd_adv_record(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["adversarial", "complete"], args.cp)
    cfg = ctx.config()
    cp = cp_by_id(ctx.plan(), args.cp)
    g = cps["gates"]["adversarial"]
    fp = fingerprint(ctx.root)
    rnd = cps.get("adv_round")
    if not rnd or rnd.get("recorded"):
        die(f"No open review round for {args.cp}. Start one with `ratchet.py reviewers {args.cp}` — it lists who must "
            "review and gives each reviewer the round nonce to stamp.")
    if rnd["fingerprint"] != fp["id"]:
        die(f"The code changed after review round {rnd['round']} was opened. Run `ratchet.py reviewers {args.cp}` "
            "again so every reviewer looks at the current code.")
    required = rnd["reviewers"]
    subs = rnd.get("substitutions") or []
    reviews = {}
    round_dir = _review_round_dir(ctx, args.cp, "adv", rnd["round"])
    for rp in args.reviews:
        rpath, raw, fst = _read_round_review(
            ctx, rp, round_dir, f"Adversarial reviews for round {rnd['round']}",
            f" `ratchet.py reviewers {args.cp}` gives each reviewer this folder and the round nonce to stamp.")
        r = _parse_review(raw, rp)
        rid = r.get("reviewer") or Path(rp).stem
        if r.get("fingerprint") != fp["id"]:
            die(f"{rp} ({rid}) is not stamped with the current fingerprint {fp['id'][:10]}. Re-run that reviewer.")
        if r.get("round_nonce") != rnd["nonce"]:
            die(f"{rp} ({rid}) does not carry this round's nonce. Reviews must come from reviewers launched for "
                f"round {rnd['round']} (see `ratchet.py reviewers {args.cp}`).")
        if r.get("checkpoint") not in (None, args.cp):
            die(f"{rp} is a review of {r.get('checkpoint')}, not {args.cp}.")
        origin = check_review_origin(ctx, cfg, st, args.cp, rnd["round"], rp, rpath, r, rid, rnd["nonce"])
        r = clean_review_text(r)  # its signature is checked: from here on it only feeds records and messages
        age = fst.st_mtime - _iso(rnd["opened_at"])
        if age < cfg.get("min_review_seconds", 20):
            die(f"{rp} ({rid}) was written {age:.0f}s after the round opened — too fast to be a real review. "
                "Launch the reviewer and let it do the work.")
        if not r.get("findings"):
            seen = set(r.get("files_reviewed") or [])
            changed = set(changed_files(ctx.root, {"kind": "git-tree", "id": cps.get("base_tree")}, fp)) \
                if cps.get("base_tree") else set()
            if not seen or (changed and not (seen & changed)):
                die(f"{rp} ({rid}) approves without listing the changed files it reviewed (files_reviewed). "
                    f"Changed in this checkpoint: {', '.join(sorted(changed)[:8]) or '(none recorded)'}.")
        reviews[rid] = (rpath, r, raw, fst, origin)
    missing = [i for i in required if i not in reviews]
    if missing:
        die(f"{args.cp} (risk={cp.get('risk', 'normal')}) needs reviews from {required}; missing {missing}. "
            f"Run `ratchet.py reviewers {args.cp}` to see how to launch each one.")
    key_file = review_key_file(ctx.root, st, args.cp, "adv", rnd["round"])  # dropped once the round is saved
    rnd["recorded"] = now()
    dest = ctx.evidence(args.cp) / "adversarial" / f"round-{rnd['round']:02d}"
    dest.mkdir(parents=True, exist_ok=True)
    all_findings, verdicts = [], []
    unconfirmed = []
    for rid in required:
        rp, r, raw, fst, (origin, why) = reviews[rid]
        spec = _roster_spec(cfg, rid) or {}
        findings = _dedupe_finding_ids(r.get("findings", []) or [])
        all_findings += [dict(f, reviewer=rid) for f in findings]
        how = {"script-signed": "signed by external_review.py",
               "confirmed": "written by the ratchet-adversary sub-agent, confirmed by the guard hook"}.get(origin, UNCONFIRMED)
        verdicts.append({"reviewer": rid, "vendor": spec.get("vendor"), "claimed": r.get("verdict"),
                         "findings": len(findings), "origin": how})
        if origin == "unconfirmed":
            verdicts[-1]["unconfirmed_because"] = why
            unconfirmed.append(f"{rid}: {UNCONFIRMED} ({why})")
        if rp.parent != dest.resolve():
            _write_review_evidence(dest / f"{rid}.json", raw, fst)
    need = len(required)
    if unconfirmed:
        g["unconfirmed"] = unconfirmed
    else:
        g.pop("unconfirmed", None)
    record_kinds(st, args.cp, rnd["round"], all_findings, ctx.root, fp["id"], cp.get("risk", "normal"))
    covered = enforced_kinds(cfg)
    escaped = sorted({finding_kind(f) for f in all_findings if finding_kind(f) in covered
                      and str(f.get("severity", "")).strip().lower() not in MINOR_SEVERITIES})
    for kind in escaped:
        _auto_strengthen(ctx, st, cfg, kind, args.cp)
    covered_minor = [dict(f, deferred_because=f"an automatic check already covers '{finding_kind(f)}'")
                     for f in all_findings if finding_kind(f) in covered
                     and str(f.get("severity", "")).strip().lower() in MINOR_SEVERITIES]
    # only MINOR findings of a covered kind step aside (their check runs at the behavior gate); blocker and major
    # findings always block, whatever label they carry
    all_findings = [f for f in all_findings if not any(f is c or (f.get("id"), f.get("reviewer")) == (c.get("id"), c.get("reviewer"))
                                                       for c in covered_minor)]
    # Realistic-use bar: a blocker, major or unknown-severity finding that does not say who is hurt and how
    # (user_impact) never blocks, at any round and any risk. It is recorded for the final review instead. This
    # is narrower than the existing minor-severity rules below: minor findings keep following those, whether or
    # not they carry a user_impact.
    no_impact = [f for f in all_findings
                 if str(f.get("severity", "")).strip().lower() not in MINOR_SEVERITIES
                 and not str(f.get("user_impact") or "").strip()]
    if no_impact:
        all_findings = [f for f in all_findings if not any(f is n for n in no_impact)]
    no_impact_deferred = [dict(f, deferred_because="no user impact stated") for f in no_impact]
    changed_since = _changed_since_last_round(ctx, cfg, g, fp["id"])
    raw_count = len(all_findings)
    all_findings, deferred = split_findings(cfg, cp.get("risk", "normal"), all_findings, changed_since, ctx.root)
    deferred = covered_minor + no_impact_deferred + deferred
    if deferred:
        write_json(dest / "deferred-findings.json", deferred)
        keep = [x for x in cps.get("deferred") or [] if x.get("round") != rnd["round"]]
        cps["deferred"] = keep + [dict(x, round=rnd["round"]) for x in deferred]
    approved = not all_findings
    # "round" is stamped on the entry itself (not left to list position) so it is found by identity later
    # (cmd_accept_finding marks it accepted): a round `reviewers` opens and then replaces before recording (the
    # fingerprint changed) advances rnd['round'] without adding an entry here, so position alone drifts from it.
    g["rounds"] = g.get("rounds", []) + [{"round": rnd["round"], "at": now(), "fingerprint": fp["id"], "verdicts": verdicts,
                                          "findings": len(all_findings), "deferred": len(deferred),
                                          "raw_findings": raw_count, "substitutions": subs,
                                          "blocking_files": sorted({_norm_path(f.get("file") or f.get("path") or f.get("location"), ctx.root)
                                                                    for f in all_findings} - {""}),
                                          "reviewers": [{"id": rr["id"], "vendor": rr.get("vendor"),
                                                         "model": rr.get("model")} for rr in rnd.get("resolved") or []]}]
    if not approved:
        g["status"] = "failed"
        ci_note = _checkin_trigger(ctx, st, args.cp, cps, g, rnd, all_findings)
        if g["attempts"] >= cfg["max_adversarial_rounds"]:
            _set_blocked(st, f"{args.cp}: adversarial review still has {len(all_findings)} finding(s) after "
                             f"{g['attempts']} rounds — needs a human/director decision.")
        ctx.log(st, "adversarial_changes_requested", cp=args.cp, findings=len(all_findings))
        ctx.save_state(st)
        drop_review_key(key_file)  # this round is recorded and saved: its secret goes
        build_viewer(ctx)
        write_json(dest / "open-findings.json", all_findings)
        _announce_repeats(ctx, st)
        die(f"Gate 3 (adversarial) NOT approved: {len(all_findings)} blocking finding(s)"
            + (f" ({len(deferred)} minor ones recorded for later)" if deferred else "") + " → "
            f"{(dest / 'open-findings.json').relative_to(ctx.root)}. Decide fix or rebut for each (`ratchet.py "
            f"triage {args.cp} <id>=fix:\"reason\"` or `=rebut:\"reason\"`), then spawn ratchet-fixer with "
            "fix-list.json, re-run behavior (and UI if visible code changed), then re-run the adversaries."
            + (f" {ci_note}" if ci_note else ""), 2)
    g.update(status="passed", fingerprint=fp, passed_at=now(), evidence=str(dest.relative_to(ctx.root)))
    cps["step"] = _earliest_open_step(cps)
    ctx.log(st, "adversarial_approved", cp=args.cp, round=g["attempts"])
    ctx.save_state(st)
    drop_review_key(key_file)
    build_viewer(ctx)
    note = (f" {len(deferred)} minor finding(s) were recorded for later instead of blocking (`ratchet.py deferred`)."
            if deferred else "")
    _announce_repeats(ctx, st)
    ok(f"Gate 3 (adversarial) APPROVED by all {need} reviewer(s) for {args.cp}.{note} Next: ratchet.py complete {args.cp}")


def _adv_round_dir(ctx, cp_id: str, round_no: int) -> Path:
    return ctx.evidence(cp_id) / "adversarial" / f"round-{round_no:02d}"


def _read_findings_file(p: Path) -> list:
    """Reads an evidence findings file (a JSON list) through `_read_record`, the one reader: a damaged one is
    treated as empty, never a crash, like every other Ratchet record."""
    data, why = _read_record(p, kind=list, missing=[])
    if why is not None:
        _note_unreadable(p, why, "Ratchet treats a damaged findings file as empty")
        return []
    return data if isinstance(data, list) else []


def _adv_open_findings(ctx, cp_id: str, rnd, cps=None) -> list:
    """The findings still open (blocking) in a checkpoint's recorded adversarial round, or [] when the round never
    blocked (or never recorded). State, not the evidence file, is the one authoritative record of what was
    accepted (standards #12): open-findings.json is only a mirror. `adv-record` writes it once, when it records a
    round (never rewriting an EARLIER round's own file), and `accept-finding` rewrites its own round's file as its
    own last write; a process killed between the state commit and that last write can leave this one round's file
    stale (still listing an id state already marked accepted), possibly for good if that was the final
    acceptance — the gate has already passed, so nothing ever revisits that file again. When `cps` (the
    checkpoint's own state dict) is given, any finding already
    recorded accepted in cps['deferred'] for this round is filtered out here regardless of what the stale file
    still says, so every caller -- ask accept-finding, accept-finding's own checks -- sees the same, correct,
    state-derived answer. Pass cps whenever it is available; omit it only for callers that merely mirror the
    file's raw contents."""
    if not rnd or not rnd.get("recorded"):
        return []
    raw = _read_findings_file(_adv_round_dir(ctx, cp_id, rnd["round"]) / "open-findings.json")
    if cps is None:
        return raw
    accepted_keys = {_finding_key(f) for f in _round_deferred(cps, rnd["round"]) if f.get("accepted")}
    return [f for f in raw if _finding_key(f) not in accepted_keys]


def _round_deferred(cps, round_no) -> list:
    """The step's deferred findings belonging to one round (cps['deferred'] entries are tagged with their round)."""
    return [f for f in cps.get("deferred") or [] if f.get("round") == round_no]


def _learnings_text(ctx) -> str:
    p = ctx.ratchet / "learnings.md"
    try:
        return p.read_text()
    except OSError:
        return ""


MINOR_SEVERITIES = {"minor", "nit", "nitpick", "trivial", "info", "suggestion", "low", "style"}


def _norm_path(p, root=None) -> str:
    """A finding's file as a repo-relative path: './', backslashes, absolute paths inside the repo, a drive letter,
    and line suffixes (':12', ':12:3', '#L12', ' (line 12)') are all handled."""
    p = str(p or "").strip().replace("\\", "/")
    p = re.sub(r"\s*\((?:line|lines|l\.)\s*[\d\-– ,]+\)\s*$", "", p, flags=re.I)
    p = re.sub(r"#L\d+(-L?\d+)?$", "", p)
    p = re.sub(r":\d+(:\d+)?(-\d+)?$", "", p)
    if root is not None:
        r = str(Path(root).resolve()).replace("\\", "/").rstrip("/") + "/"
        low = p.lower()
        if re.match(r"^[a-z]:/", low) or p.startswith("/"):
            idx = low.find(r.lower()) if r.lower() in low else -1
            if idx >= 0:
                p = p[idx + len(r):]
            else:
                tail = r.rstrip("/").rsplit("/", 1)[-1] + "/"  # the repo's own folder name, e.g. C:/work/app/src/a.js
                if ("/" + tail.lower()) in low:
                    p = p[low.rfind("/" + tail.lower()) + len(tail) + 1:]
    while p.startswith("./"):
        p = p[2:]
    return p


def _in_scope(path: str, scope: set) -> bool:
    if not path:
        return True  # no file given: can't tell where it is, so it counts as in scope (it blocks)
    if path in scope:
        return True
    base = path.rsplit("/", 1)[-1]
    if "/" not in path:  # a bare file name: in scope if any scoped file has that name
        return any(x.rsplit("/", 1)[-1] == base for x in scope)
    return False


def split_findings(cfg, risk: str, findings: list, changed_since: set | None, root=None) -> tuple[list, list]:
    """(blocking, deferred). Severities not known to be minor always block (a reviewer can't escape the bar by
    inventing a severity name). `changed_since` is the round's scope: files changed since the previous round plus
    files that had a blocking finding last round (a finding the fixer ignored can't be waved through)."""
    bar = (cfg.get("blocking_severities") or {}).get(risk, "all")
    bar_l = [str(x).strip().lower() for x in (bar or [])] if bar != "all" else None
    blocking, deferred = [], []
    for f in findings:
        sev = str(f.get("severity", "")).strip().lower()
        minor = sev in MINOR_SEVERITIES
        where = _norm_path(f.get("file") or f.get("path") or f.get("location"), root)
        if minor and bar_l is not None and sev not in bar_l:
            deferred.append(dict(f, deferred_because=f"{risk}-risk step: only {' and '.join(bar)} findings block"))
        elif minor and changed_since is not None and not _in_scope(where, changed_since):
            deferred.append(dict(f, deferred_because="later round: not in code changed since the previous round"))
        else:
            blocking.append(f)
    return blocking, deferred


def _changed_since_last_round(ctx, cfg, g, fp_id) -> set | None:
    """The round's scope: files changed since the previous round, plus every file that had a blocking finding in the
    previous round (so a finding the fixer didn't fix is still judged in full)."""
    if not cfg.get("scope_later_rounds", True) or not g.get("rounds"):
        return None
    last = g["rounds"][-1]
    prev = last.get("fingerprint")
    if not prev:
        return None
    scope = {_norm_path(x) for x in changed_files(ctx.root, {"kind": "git-tree", "id": prev}, {"kind": "git-tree", "id": fp_id})}
    scope |= set(last.get("blocking_files") or [])
    return scope


def norm_kind(k) -> str | None:
    k = re.sub(r"[^a-z0-9]+", "-", str(k or "").strip().lower()).strip("-")[:60]
    return k or None


def finding_kind(f) -> str | None:
    """The reviewer's own label for the TYPE of mistake. Only an explicit `kind` counts: a cited rule is too broad to
    stand for one checkable mistake."""
    return norm_kind(f.get("kind"))


def enforced_kinds(cfg) -> set:
    return {norm_kind(c.get("kind")) for c in (cfg.get("checks") or []) if isinstance(c, dict) and c.get("kind")}


def record_kinds(st, cp_id, round_no, findings, root=None, tree=None, risk=None):
    seen = st.setdefault("finding_kinds", {})
    for f in findings:
        k = finding_kind(f)
        if k:
            seen.setdefault(k, []).append({"cp": cp_id, "round": round_no,
                                           "file": _norm_path(f.get("file") or f.get("path") or f.get("location"), root),
                                           "what": str(f.get("description", ""))[:160], "tree": tree, "risk": risk,
                                           "severity": str(f.get("severity", "")).strip().lower()})


def kind_is_high_risk(st, kind) -> bool:
    """Found on a high-risk checkpoint, or ever rated a blocker."""
    return any(h.get("risk") == "high" or h.get("severity") == "blocker"
               for h in (st.get("finding_kinds") or {}).get(kind, []))


def _next_tree(st, h) -> str | None:
    """The code version after the one this finding was raised on, for the same checkpoint."""
    g = (((st.get("checkpoints") or {}).get(h["cp"]) or {}).get("gates") or {}).get("adversarial") or {}
    fps = [r.get("fingerprint") for r in g.get("rounds") or []]
    if h.get("tree") in fps:
        later = [x for x in fps[fps.index(h["tree"]) + 1:] if x and x != h["tree"]]
        if later:
            return later[0]
    done = (g.get("fingerprint") or {}).get("id")
    return done if done and done != h.get("tree") else None


def _acted_on(ctx, st, h) -> bool:
    """Was this finding acted on, i.e. did the flagged file change in the next version of the code? A finding the
    fixer disproved (no change) isn't evidence that the mistake was really there."""
    nxt = _next_tree(st, h)
    if not nxt:
        return False
    changed = changed_files(ctx.root, {"kind": "git-tree", "id": h["tree"]}, {"kind": "git-tree", "id": nxt})
    if not h.get("file"):
        return bool(changed)
    return _norm_path(h["file"]) in {_norm_path(x) for x in changed}


def kind_occurrences(st, kind, limit=None, ctx=None) -> list[dict]:
    """The real places a kind was found, newest first, one per reviewed code version. Only blocking findings
    (blocker/major) count, and, when `ctx` is given, only ones that were acted on (the flagged file changed next)."""
    out, trees = [], set()
    for h in reversed((st.get("finding_kinds") or {}).get(kind, [])):
        if h.get("severity") in MINOR_SEVERITIES or not h.get("tree") or h["tree"] in trees:
            continue
        if ctx is not None and not _acted_on(ctx, st, h):
            continue
        trees.add(h["tree"])
        out.append(h)
        if limit and len(out) >= limit:
            break
    return out


def kind_places(st, kind) -> set:
    """Distinct places a kind was found: (checkpoint, file). The same unfixed finding raised again in a later round is
    one place, not a repeat."""
    return {(h["cp"], h.get("file") or "") for h in (st.get("finding_kinds") or {}).get(kind, [])}


def repeat_candidates(ctx, st, cfg=None) -> list[dict]:
    """Kinds of mistake found in two or more places that no automatic check covers yet and that the user hasn't been
    asked about. Offer each one as a guardrail as soon as it shows up."""
    cfg = cfg or ctx.config()
    done = enforced_kinds(cfg) | {norm_kind(k) for k in st.get("guardrails_offered") or []}
    out = []
    for k, hits in (st.get("finding_kinds") or {}).items():
        places = kind_places(st, k)
        if len(places) >= 2 and k not in done:
            out.append({"kind": k, "times": len(places), "checkpoints": sorted({h["cp"] for h in hits}),
                        "example": hits[-1]["what"]})
    return sorted(out, key=lambda x: -x["times"])


def cmd_occurrences(args):
    """The real code where reviewers found a kind of mistake, as they saw it (for a variant writer)."""
    ctx = Ctx(find_root())
    st = ctx.state()
    kind = norm_kind(args.kind)
    occ = [h for h in kind_occurrences(st, kind, limit=5) if h.get("file")]
    if not occ:
        die(f"No recorded occurrences of '{kind}' with code to show.")
    for h in occ:
        r = subprocess.run(["git", "show", f"{h['tree']}:{h['file']}"], cwd=ctx.root, capture_output=True, text=True)
        body = r.stdout if r.returncode == 0 else "(file not in that version)"
        lines = body.splitlines()
        print(f"=== {h['cp']} round {h['round']}: {h['file']} — {h['what']}")
        print("\n".join(lines[:200]) + ("\n… (cut)" if len(lines) > 200 else ""))


def cmd_repeats(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if args.offered:
        k = norm_kind(args.offered)
        offered = st.setdefault("guardrails_offered", [])
        if k and k not in offered:
            offered.append(k)
        ctx.save_state(st)
        ok(f"'{k}' marked as offered; it won't be suggested again.")
        return
    rows = repeat_candidates(ctx, st)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No repeated kind of finding waiting for a decision.")
        return
    for r in rows:
        print(f"- {r['kind']}: found {r['times']} times ({', '.join(r['checkpoints'])}); e.g. {r['example']}")


def review_rules(ctx, cfg, cp, g, rnd, fp_id) -> tuple[set | None, list[str]]:
    """What the reviewers of this round are told about the severity bar and the round's focus."""
    focus = _changed_since_last_round(ctx, cfg, g, fp_id) if rnd["round"] > 1 else None
    bar = (cfg.get("blocking_severities") or {}).get(cp.get("risk", "normal"), "all")
    rules = []
    if bar != "all":
        rules.append(f"This is a {cp.get('risk', 'normal')}-risk step: only {' and '.join(bar)} findings block; "
                     "report minor ones too, they are recorded for later.")
    kinds = sorted(enforced_kinds(cfg))
    if kinds:
        rules.append("These kinds of mistake are already caught by automatic checks that run with the tests: "
                     + ", ".join(kinds) + ". Still report a blocker or major problem of these kinds (it still blocks); "
                     "minor ones of these kinds are noted, not fixed.")
    if focus is not None:
        rules.append("Round " + str(rnd["round"]) + ": look hardest at the code changed since the last round ("
                     + (", ".join(sorted(focus)[:20]) or "nothing changed") + "). Re-raise any earlier finding that "
                     "still isn't fixed, wherever it is. Beyond that, a new minor problem outside those files is "
                     "recorded, not fixed; blocker and major problems count anywhere.")
    if rnd["round"] > 1:
        prev_triage = _read_triage(_adv_round_dir(ctx, cp["id"], rnd["round"] - 1))
        for ref, dec in prev_triage.items():
            if dec.get("decision") == "rebut":
                rules.append(f"Rebuttal from the last round on {ref}: {dec.get('reason', '')} — withdraw this "
                             "finding if the rebuttal holds up, or re-raise it with a stronger user_impact if it "
                             "doesn't.")
    return focus, rules


def _announce_repeats(ctx, st):
    director = approval_mode(ctx) == "director"
    for r in repeat_candidates(ctx, st):
        if director:
            print(f"REPEAT: reviewers have now found '{r['kind']}' in {r['times']} places. This lane is "
                  f"director-enrolled: tell the director, who can approve a guardrail with "
                  f"`director-sign guardrail --kind {r['kind']}`; then run ratchet.py repeats --offered {r['kind']}.")
            continue
        print(f"REPEAT: reviewers have now found '{r['kind']}' {r['times']} times (e.g. {r['example']}). Offer the user "
              f"a guardrail now: ratchet.py ask choice --options A,B,C --guardrail \"A={r['kind']}\" "
              "(see 'When the same mistake repeats' in the orchestrator skill).")


def cmd_guardrail(args):
    """Insert a guardrail step right after the current checkpoint: an automatic check (lint rule, architecture test,
    type rule...) that makes a repeated mistake fail fast. Needs the user's pick of the guardrail option (or, in a
    director lane, a director token). The step proves the check red/green and goes through every gate; when it
    completes, the check joins config `checks` and runs with the tests from then on."""
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] not in ("building", "blocked"):
        die(f"A guardrail can only be added while building (phase {st['phase']}).")
    kind = norm_kind(args.kind)
    token = getattr(args, "director_token", None)
    allow = st.get("choice_allow") or {}
    cfg = ctx.config()
    if not kind or len(kind_places(st, kind)) < 2:
        die(f"'{kind}' hasn't been found in two places by the reviewers, so it isn't a repeated mistake. Guardrails are "
            "only for kinds of finding that keep coming back (`ratchet.py repeats`).")
    if kind in enforced_kinds(cfg):
        die(f"An automatic check for '{kind}' already exists. Changing or replacing it is a rule change the user must "
            "confirm (show them the change, then `ratchet.py ask approve-changes`).")
    if "{target}" not in args.check_command:
        die("--check-command must contain {target}, where Ratchet puts what to check: the project's code "
            "(--target, default .) or the bad example (--bad-example). E.g. --check-command \"npx eslint {target}\".")
    bad = (ctx.root / args.bad_example).resolve()
    try:
        bad.relative_to(ctx.root.resolve())
    except ValueError:
        die("--bad-example must be inside the project (a test fixture).")
    if token:
        _require_approval(ctx, st, f"guardrail:{kind}", token)
    elif allow.get("guardrail") != kind or allow.get("cp") != st.get("current"):
        die(f"The user hasn't picked a guardrail option for '{kind}' at this step. Offer it first: ratchet.py ask "
            f"choice --options A,B,C --guardrail \"A={kind}\", then ratchet.py choice after they reply.")
    high = kind_is_high_risk(st, kind)
    gr = {"kind": kind, "check_command": args.check_command, "target": args.target,
          "bad_example": args.bad_example, "why": args.why or "", "test_command": args.test_command,
          "test_files": args.test_file or [], "high_risk": high,
          "variants_dir": (args.variants_dir or f"tests/fixtures/guardrails/{kind}/variants") if high else None}
    gid, title = _insert_guardrail_step(ctx, st, gr, f"Guardrail: {args.title}",
                                        f"Add an automatic check that fails fast on '{kind}', a mistake reviewers kept finding.")
    cur = st.get("current")
    if not token:
        allow["guardrail"] = None
        if not allow.get("files") and not allow.get("unblock"):
            st.pop("choice_allow", None)
    ctx.log(st, "guardrail_added", cp=gid, kind=kind)
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"Added {gid} ({title}) right after {cur}. It is built next, through every gate; its check then runs with the "
       "tests at every later step.")


GUARDRAIL_NOTES = ("Prefer a check that understands the code over one that matches text: an import-graph rule "
                   "(dependency-cruiser, import-linter, ArchUnit), a syntax-aware lint rule (ESLint, Semgrep, Ruff) or a "
                   "type rule, using the project's existing tools first; you may add such a tool as a dev dependency. "
                   "Keep bad examples in test fixtures, never in shipped code. Ratchet will also run the check on the "
                   "real code where the mistake was found, so it must catch those, not only your fixture.")


def _insert_guardrail_step(ctx, st, gr: dict, title: str, summary: str, extra_case: dict | None = None):
    """Put a guardrail (or strengthen) step right after the current checkpoint, with its own red/green test plan.
    The user's pick (or the automatic strengthening rule) is the approval for this one step."""
    plan = ctx.plan()
    ids = [int(c["id"][2:]) for c in plan["checkpoints"] if re.fullmatch(r"CP\d+", c["id"])]
    gid = f"CP{max(ids) + 1}"
    title = " ".join(title.split()[:8])
    kind = gr["kind"]
    done_when = [f"The check fails on a deliberately bad example of {kind} ({gr['bad_example']})",
                 "The check fails on the real code where reviewers found the mistake",
                 f"The check passes on the project's current code ({gr['target']})",
                 "The check runs with the tests at every later step"]
    if gr.get("high_risk"):
        done_when.insert(2, f"It catches every disguised bad variant in {gr['variants_dir']}/bad and none of the "
                            f"look-alike good code in {gr['variants_dir']}/good")
    entry = {"id": gid, "title": title, "summary": summary, "done_when": done_when,
             "gates": {"behavior": True, "ui": False, "adversarial": True},
             "risk": "high" if gr.get("high_risk") else "normal",
             "ui_skip_reason": "an automatic code check, nothing visible changes",
             "tests": {"files": gr.get("test_files") or [], "command": gr["test_command"]},
             "guardrail": gr, "notes": GUARDRAIL_NOTES}
    cur = st.get("current")
    order = [c["id"] for c in plan["checkpoints"]]
    at = order.index(cur) + 1 if cur in order else len(order)
    plan["checkpoints"].insert(at, entry)
    errs = validate_plan(plan)
    if errs:
        die("Guardrail step invalid:\n  - " + "\n  - ".join(errs))
    write_json(ctx.run_dir() / "checkpoints.json", plan)
    cases = [{"id": f"{gid}-T01", "layer": "code", "title": f"the check catches {kind}",
              "given": f"the fixture {gr['bad_example']}, which contains the mistake ({gr.get('why') or kind})",
              "when": f"the check runs on it ({gr['check_command'].replace('{target}', gr['bad_example'])})",
              "then": "it fails and names the problem"},
             {"id": f"{gid}-T02", "layer": "code", "title": "the check passes on the current code",
              "given": "the project as it is now",
              "when": f"the check runs ({gr['check_command'].replace('{target}', gr['target'])})", "then": "it passes"}]
    if extra_case:
        cases.append(dict(extra_case, id=f"{gid}-T03", layer="code"))
    write_json(ctx.run_dir() / "tests" / f"{gid}.json", {"checkpoint": gid, "cases": cases})
    st["checkpoints"][gid] = new_cp_state(entry)
    st["plan_hash"] = _plan_hash(plan, ctx)
    snap = st.get("approved") or {}
    snap.setdefault("cps", {})[gid] = _sha(entry)
    snap.setdefault("tests", {})[gid] = _sha(test_plan(ctx, gid))
    return gid, title


def _auto_strengthen(ctx, st, cfg, kind, cp_id):
    """A reviewer found a blocker/major mistake of a kind an automatic check covers: the check let it through. Add a
    step that strengthens the check (no question: the user asked for this to happen automatically)."""
    plan = ctx.plan()
    pending = [c for c in plan["checkpoints"] if (c.get("guardrail") or {}).get("kind") == kind
               and st["checkpoints"].get(c["id"], {}).get("status") != "done"]
    chk = next((c for c in cfg.get("checks") or [] if isinstance(c, dict) and norm_kind(c.get("kind")) == kind), {})
    occ = kind_occurrences(st, kind, limit=1)
    where = (occ[0].get("file") or occ[0]["cp"]) if occ else "a later step"
    ctx.log(st, "check_missed", kind=kind, cp=cp_id, file=where)
    if pending:
        print(f"STRENGTHEN: the check for '{kind}' missed a case in {where}; step {pending[0]['id']} will cover it too.")
        return
    if not chk.get("template") or not chk.get("test_command"):
        print(f"STRENGTHEN: the check for '{kind}' missed a case in {where}, but it was added without the details "
              "needed to strengthen it automatically. Tell the user in one line.")
        return
    gr = {"kind": kind, "check_command": chk["template"], "target": chk.get("target") or ".",
          "bad_example": chk.get("bad_example") or "", "why": chk.get("why") or "", "strengthen": True,
          "test_command": chk["test_command"], "test_files": chk.get("test_files") or [],
          "high_risk": bool(chk.get("high_risk")), "variants_dir": chk.get("variants_dir")}
    gid, _ = _insert_guardrail_step(
        ctx, st, gr, f"Strengthen check: {kind}",
        f"The automatic check for '{kind}' let a case through in {where}. Make it catch that case too, without "
        "losing any it already catches.",
        {"title": f"the check now catches the case it missed in {where}",
         "given": f"a fixture with the code reviewers flagged in {where}", "when": "the check runs on it",
         "then": "it fails and names the problem"})
    ctx.log(st, "guardrail_strengthen_added", cp=gid, kind=kind)
    print(f"STRENGTHEN: the automatic check for '{kind}' missed a case in {where}. Step {gid} was added right after "
          f"{st.get('current')} to strengthen it (no question needed). Tell the user in one line.")


def _materialize(ctx, tree: str, overlay: list[str]) -> Path | None:
    """A throwaway copy of an earlier reviewed version of the code, with this step's own files (the check, its config,
    fixtures) laid over it and the project's installed dependencies linked in."""
    import io
    import tarfile
    tmp = Path(tempfile.mkdtemp(prefix="ratchet-occurrence-"))
    r = subprocess.run(["git", "archive", "--format=tar", tree], cwd=ctx.root, capture_output=True)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    try:
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
            for m in tf.getmembers():
                name = m.name
                if name.startswith("/") or ".." in Path(name).parts:
                    continue  # never write outside the throwaway folder
                if m.issym() or m.islnk():
                    continue  # links are skipped: a link could point anywhere
                if not (m.isfile() or m.isdir()):
                    continue
                tf.extract(m, tmp, set_attrs=False)
    except (tarfile.TarError, OSError):
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    for rel in overlay:
        src, dst = ctx.root / rel, tmp / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.exists() and dst.is_file():
            dst.unlink()
    for dep in DEP_DIRS:
        if (ctx.root / dep).exists() and not (tmp / dep).exists():
            (tmp / dep).symlink_to(ctx.root / dep)
    return tmp


DEP_DIRS = ("node_modules", ".venv", "venv", "vendor", ".bundle", "target")


def _deps_snapshot(root: Path) -> dict:
    """A cheap picture of the installed-dependency folders (names, sizes and times of their top two levels), to
    notice a check that writes into them through the links."""
    snap = {}
    for dep in DEP_DIRS:
        d = root / dep
        if not d.is_dir():
            continue
        for p in [d] + [x for x in d.iterdir()][:5000]:
            try:
                st_ = p.stat()
                snap[str(p.relative_to(root))] = (st_.st_mtime_ns, st_.st_size)
            except OSError:
                continue
    return snap


def _run_check_in(cwd: Path, command: str, log: Path, timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, CI="1", FORCE_COLOR="0"))
        code, out = r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")
    except subprocess.TimeoutExpired:
        code, out = 124, f"TIMEOUT after {timeout}s"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"$ (in {cwd}) {command}\n# exit={code}\n\n{out}")
    return code, out


def verify_guardrail(ctx, st, cp, cps, fp, cfg) -> list[str]:
    """Ratchet runs the real check itself. It must fail on the bad example, on the real code where the mistake was
    found, and (high-risk kinds) on every disguised bad variant; and pass on today's code and on look-alike good code.
    No run may change a project file."""
    import shlex
    gr = cp["guardrail"]
    ev = ctx.evidence(cp["id"]) / "guardrail"
    t = cfg["test_timeout_sec"]
    probs = []
    bad = ctx.root / (gr.get("bad_example") or "")
    if not gr.get("bad_example") or not bad.exists() or (bad.is_dir() and not any(bad.rglob("*"))):
        return [f"its bad example ({gr.get('bad_example')}) is missing or empty"]
    code, _, _ = _run_cmd(ctx, _check_cmd(gr, gr["bad_example"]), ev / "bad-example.log", t)
    if code == 0:
        probs.append(f"it passes on the bad example ({gr['bad_example']}), so it doesn't catch the mistake")
    code, _, _ = _run_cmd(ctx, _check_cmd(gr, gr.get("target") or "."), ev / "today.log", t)
    if code != 0:
        probs.append("it fails on the current code; it must pass on today's code")
    if gr.get("high_risk"):
        vd = ctx.root / (gr.get("variants_dir") or "")
        bads = sorted(p for p in (vd / "bad").rglob("*") if p.is_file()) if (vd / "bad").is_dir() else []
        goods = sorted(p for p in (vd / "good").rglob("*") if p.is_file()) if (vd / "good").is_dir() else []
        if len(bads) < 3 or len(goods) < 2:
            probs.append(f"a high-risk kind needs at least 3 disguised bad variants and 2 look-alike good examples in "
                         f"{gr.get('variants_dir')}/bad and /good (found {len(bads)} and {len(goods)})")
        for i, v in enumerate(bads):
            rel = str(v.relative_to(ctx.root))
            if _run_cmd(ctx, _check_cmd(gr, rel), ev / f"variant-bad-{i:02d}.log", t)[0] == 0:
                probs.append(f"it misses the disguised bad variant {rel}")
        for i, v in enumerate(goods):
            rel = str(v.relative_to(ctx.root))
            if _run_cmd(ctx, _check_cmd(gr, rel), ev / f"variant-good-{i:02d}.log", t)[0] != 0:
                probs.append(f"it wrongly flags the look-alike good code {rel}")
    if _is_git(ctx.root):
        overlay = changed_files(ctx.root, {"kind": "git-tree", "id": cps.get("base_tree")}, fp) if cps.get("base_tree") else []
        cases = proof_cases(ctx, st, gr)
        cps["guardrail_cases"] = cases
        deps_before = _deps_snapshot(ctx.root)
        for i, h in enumerate(cases):
            where = f"{h['cp']} round {h['round']}" + (f", {h['file']}" if h.get("file") else "")
            tmp = _materialize(ctx, h["tree"], overlay)
            if tmp is None:
                probs.append(f"Ratchet couldn't rebuild the past code it must be proven on ({where}); it won't skip a "
                             "case, so this needs a look")
                continue
            try:
                cmd = gr["check_command"].replace("{target}", shlex.quote(gr.get("target") or "."))
                code, _ = _run_check_in(tmp, cmd, ev / f"real-occurrence-{i:02d}.log", t)
                if code == 0:
                    probs.append(f"it passes on the real code where reviewers found the mistake ({where}), so it would "
                                 "not have caught it")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if _deps_snapshot(ctx.root) != deps_before:
            probs.append("running it on past code changed the project's installed dependencies; a check must only "
                         "read the code")
    if fingerprint(ctx.root)["id"] != fp["id"]:
        probs.append("running it changed files in the project; a check must only read the code")
    return probs


def proof_cases(ctx, st, gr) -> list[dict]:
    """Every past case the check must catch: all cases an earlier version of this check was proven on (kept on the
    check, so strengthening can never drop one), plus every acted-on blocking finding of the kind."""
    cfg = ctx.config()
    old = next((c for c in cfg.get("checks") or [] if isinstance(c, dict) and norm_kind(c.get("kind")) == gr["kind"]), {})
    cases, trees = [], set()
    for h in list(old.get("cases") or []) + kind_occurrences(st, gr["kind"], ctx=ctx):
        if h.get("tree") and h["tree"] not in trees:
            trees.add(h["tree"])
            cases.append({k: h.get(k) for k in ("cp", "round", "file", "tree")})
    return cases


def _check_cmd(gr: dict, target: str) -> str:
    import shlex
    return gr["check_command"].replace("{target}", shlex.quote(target))


def _finish_guardrail(ctx, st, cp):
    """A guardrail step passed every gate: its check becomes permanent (config `checks`) and is noted in learnings."""
    gr = cp.get("guardrail") or {}
    if not gr.get("check_command"):
        return
    user = read_json(ctx.config_path, {}) or {}
    checks = [c for c in (user.get("checks") or []) if isinstance(c, dict)]
    existing = [c for c in checks if norm_kind(c.get("kind")) == gr["kind"]]
    if any(c.get("from") == f"{st['slug']}/{cp['id']}" for c in existing):
        return  # an earlier run of `complete` for this step added it, then failed before it could save
    if existing and not gr.get("strengthen"):
        die(f"An automatic check for '{gr['kind']}' already exists; it is never replaced silently.")
    # a strengthen step replaces its own kind's check, after proving it still catches every past case
    checks = [c for c in checks if norm_kind(c.get("kind")) != gr["kind"]]
    command = _check_cmd(gr, gr.get("target") or ".")
    checks.append({"kind": gr["kind"], "command": command, "title": cp["title"],
                   "template": gr["check_command"], "target": gr.get("target") or ".",
                   "bad_example": gr.get("bad_example"), "why": gr.get("why"), "test_command": gr.get("test_command"),
                   "test_files": gr.get("test_files"), "high_risk": bool(gr.get("high_risk")),
                   "variants_dir": gr.get("variants_dir"),
                   "cases": (st["checkpoints"].get(cp["id"]) or {}).get("guardrail_cases") or [],
                   "added": _dt.date.today().isoformat(), "from": f"{st['slug']}/{cp['id']}"})
    user["checks"] = checks
    write_json(ctx.config_path, user)
    if st.get("approved"):
        st["approved"].setdefault("rules", {})["checks"] = _sha(ctx.config().get("checks"))
    _append_learning(ctx, f"{gr.get('why') or gr['kind']}: enforced by `{command}`", f"guardrail {cp['id']}")


# ---- spec tools: OpenSpec, Spec Kit, BMAD ---------------------------------------------------------------------
# The team's spec says WHAT to build; Ratchet proves it was built. A run can start from a spec item: its scenarios /
# acceptance criteria each need a planned test (case "spec_ref"), its tasks each belong to a step ("spec_refs"),
# and when a step passes every gate Ratchet ticks those tasks in the spec tool's own files.

TOOL_PLAIN = {"openspec": "OpenSpec change", "speckit": "Spec Kit feature", "bmad": "BMAD story"}


def _spec_source_path(ctx) -> Path:
    return ctx.run_dir() / "reference" / "spec-source.json"


def spec_source(ctx) -> dict | None:
    try:
        src = read_json(_spec_source_path(ctx))
    except SystemExit:
        return None
    return src if isinstance(src, dict) and src.get("items") else None


def spec_items(ctx, src=None) -> list[dict]:
    """The spec items this run was built from, read fresh from the tool's files."""
    src = src or spec_source(ctx)
    out = []
    for ref in (src or {}).get("items") or []:
        try:
            out.append(specsrc.load(ctx.root, ref["tool"], ref["id"]))
        except (FileNotFoundError, ValueError) as e:
            out.append({"tool": ref["tool"], "id": ref["id"], "title": ref["id"], "missing": str(e),
                        "requirements": [], "tasks": [], "standards": []})
    return out


def _spec_label(it) -> str:
    return f"{TOOL_PLAIN.get(it['tool'], it['tool'])} {it['id']}" + (f" ({it['title']})" if it.get("title") not in (None, it["id"]) else "")


def _as_list(v) -> list:
    return [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x]


def spec_coverage(ctx, plan, items) -> dict:
    """Which spec refs the plan covers: scenarios need a test case (or a UI-reviewed step), tasks need a step."""
    tests_need = specsrc.scenario_refs(items)
    task_refs = [t["ref"] for it in items for t in it["tasks"] if not t.get("subtask")]
    done_tasks = {t["ref"] for it in items for t in it["tasks"] if t["done"]}
    known = set(tests_need) | {t["ref"] for it in items for t in it["tasks"]} | \
        {r["ref"] for it in items for r in it["requirements"]}
    unmapped, bad_unmapped = {}, []
    for u in plan.get("spec_unmapped") or []:
        ref = u.get("ref") if isinstance(u, dict) else None
        if not ref:
            bad_unmapped.append(str(u)[:80])
        elif not str(u.get("reason") or "").strip():
            bad_unmapped.append(ref)
        else:
            unmapped[ref] = u["reason"].strip()
    by_case, by_step, ui_steps, unknown = {}, {}, {}, set()
    for cp in plan.get("checkpoints") or []:
        for ref in _as_list(cp.get("spec_refs") or []):
            by_step.setdefault(ref, []).append(cp["id"])
            if (cp.get("gates") or {}).get("ui"):
                ui_steps.setdefault(ref, []).append(cp["id"])
            if ref not in known:
                unknown.add(ref)
        for case in (test_plan(ctx, cp["id"]) or {}).get("cases") or []:
            for ref in _as_list(case.get("spec_ref")):
                by_case.setdefault(ref, []).append({"case": case.get("id"), "checkpoint": cp["id"]})
                if ref not in known:
                    unknown.add(ref)
    unknown |= {r for r in unmapped if r not in known}
    no_test = [r for r in tests_need if r not in by_case and r not in ui_steps and r not in unmapped]
    no_step = [r for r in task_refs if r not in by_step and r not in done_tasks and r not in unmapped]
    return {"scenarios": tests_need, "tasks": task_refs, "by_case": by_case, "by_step": by_step,
            "ui_steps": ui_steps, "unmapped": unmapped, "no_test": no_test, "no_step": no_step,
            "unknown": sorted(unknown), "bad_unmapped": bad_unmapped, "done_tasks": sorted(done_tasks),
            "unclear": [r for it in items for r in it.get("unclear") or []],
            "missing": [f"{it['tool']} {it['id']}" for it in items if it.get("missing")]}


def spec_plan_problems(ctx, plan) -> list[str]:
    src = spec_source(ctx)
    if not src:
        return []
    items = spec_items(ctx, src)
    cov = spec_coverage(ctx, plan, items)
    errs = [f"the spec item {m} can't be read any more" for m in cov["missing"]]
    if cov["no_test"]:
        errs.append("no planned test covers these spec scenarios/criteria (give a test case \"spec_ref\", or list "
                    "them in the plan's spec_unmapped with a reason): " + ", ".join(cov["no_test"][:20]))
    if cov["no_step"]:
        errs.append("these spec tasks aren't in any step's spec_refs (add them, or list them in spec_unmapped with "
                    "a reason): " + ", ".join(cov["no_step"][:20]))
    if cov["unknown"]:
        errs.append("these refs aren't in the spec (typo, or the spec changed?): " + ", ".join(cov["unknown"][:20]))
    if cov["bad_unmapped"]:
        errs.append("every spec_unmapped entry needs a ref and a reason: " + ", ".join(cov["bad_unmapped"][:10]))
    return errs


def _spec_snapshot(ctx) -> str | None:
    src = spec_source(ctx)
    return _sha(specsrc.spec_digest(spec_items(ctx, src))) if src else None


def _spec_standards(items) -> list[str]:
    seen = []
    for it in items:
        for s in it.get("standards") or []:
            if s not in seen:
                seen.append(s)
    return seen


def _pick_spec_items(ctx, tool, ids) -> list[tuple[str, str]]:
    found = specsrc.detect(ctx.root)
    if tool == "auto":
        cands = [(t, x["id"]) for t, xs in found.items() for x in xs]
        if ids:
            cands = [c for c in cands if c[1] in ids]
        if len(cands) != 1:
            names = "; ".join(f"{TOOL_PLAIN[t]} {i}" for t, i in cands) or "none"
            die(f"Can't pick a spec item automatically ({len(cands)} open: {names}). Name the tool and item.")
        return cands
    if tool not in TOOL_PLAIN:
        die(f"Unknown spec tool '{tool}'. Use openspec, speckit, bmad or auto.")
    if not ids:
        die(f"Name the {TOOL_PLAIN[tool]} to build (see `ratchet.py spec detect`).")
    return [(tool, i) for i in ids]


def cmd_spec(args):
    ctx = Ctx(find_root())
    if args.spec_cmd == "detect":
        found = specsrc.detect(ctx.root)
        if args.json:
            print(json.dumps(found, indent=2))
            return
        if not found:
            print("No OpenSpec, Spec Kit or BMAD files found in this project.")
            return
        if not any(found.values()):
            print("This project uses " + ", ".join(TOOL_PLAIN[t].rsplit(" ", 1)[0] for t in found) +
                  ", but nothing is open to build right now.")
            return
        for tool, xs in found.items():
            if not xs:
                continue
            print(f"{TOOL_PLAIN[tool]}s still open: {len(xs)}")
            for x in xs:
                bits = [x.get("status") or ""]
                if "scenarios" in x:
                    bits.append(f"{x['scenarios']} scenarios")
                if "criteria" in x:
                    bits.append(f"{x['criteria']} acceptance criteria")
                bits.append(f"{x.get('tasks', 0)} tasks")
                if x.get("current"):
                    bits.append("current feature")
                if x.get("unclear"):
                    bits.append(f"{len(x['unclear'])} requirement(s) marked NEEDS CLARIFICATION")
                if tool == "bmad" and not x.get("approved"):
                    bits.append("not approved yet")
                print(f"  - {x['id']}: {x['title']} ({', '.join(b for b in bits if b)})")
        return
    if args.spec_cmd == "import":
        st = ctx.state()
        if st["phase"] not in ("planning", "awaiting_plan_approval"):
            die(f"Import a spec while planning (phase is {st['phase']}).")
        picks = _pick_spec_items(ctx, args.tool, args.items or [])
        items = []
        for tool, iid in picks:
            try:
                items.append(specsrc.load(ctx.root, tool, iid))
            except (FileNotFoundError, ValueError) as e:
                die(str(e))
        rd = ctx.run_dir()
        (rd / "reference").mkdir(parents=True, exist_ok=True)
        write_json(_spec_source_path(ctx), {"items": [{"tool": it["tool"], "id": it["id"], "path": it["path"]}
                                                       for it in items], "imported_at": now()})
        plan, tests = specsrc.draft_plan(items, st.get("feature") or items[0]["title"])
        wrote_plan = args.force or not (rd / "checkpoints.json").exists()
        if wrote_plan:
            write_json(rd / "checkpoints.json", plan)
            for cid, tp in tests.items():
                if args.force or not (rd / "tests" / f"{cid}.json").exists():
                    write_json(rd / "tests" / f"{cid}.json", tp)
        stds = _spec_standards(items)
        user = read_json(ctx.config_path, {}) or {}
        cur = list(ctx.config().get("standards") or [])
        add = [s for s in stds if s not in cur]
        if add:
            user["standards"] = cur + add
            write_json(ctx.config_path, user)
        ctx.log(st, "spec_imported", items=[f"{it['tool']}:{it['id']}" for it in items])
        ctx.save_state(st)
        cov_scen = sum(len(specsrc.scenario_refs([it])) for it in items)
        out = {"items": [{"tool": it["tool"], "id": it["id"], "title": it["title"], "status": it.get("status"),
                          "approved": it.get("approved"), "unclear": it.get("unclear") or [],
                          "removed": [r["ref"] for r in it.get("removed") or []]} for it in items],
               "scenarios": cov_scen, "tasks": sum(len(it["tasks"]) for it in items),
               "draft_steps": len(plan["checkpoints"]) if wrote_plan else None, "standards_added": add}
        if args.json:
            print(json.dumps(out, indent=2))
            return
        for it in items:
            ok(f"Imported {_spec_label(it)}: {len(specsrc.scenario_refs([it]))} scenario(s) to test, "
               f"{len(it['tasks'])} task(s).")
            if not it.get("approved"):
                print(f"  Note: its status is '{it.get('status')}', so the team may not have approved it yet.")
            if not specsrc.scenario_refs([it]):
                print("  Note: no requirements, scenarios or acceptance criteria could be read from it, so nothing in it "
                      "would need a test. Check how it is written before relying on the coverage check.")
            if it.get("unclear"):
                print(f"  Note: {len(it['unclear'])} requirement(s) are marked NEEDS CLARIFICATION: "
                      + ", ".join(it["unclear"]))
            if it.get("removed"):
                print(f"  Removes {len(it['removed'])} requirement(s): " + ", ".join(r["title"] for r in it["removed"]))
        if wrote_plan:
            print(f"Draft plan written: {len(plan['checkpoints'])} step(s) in checkpoints.json and tests/. "
                  "The planner fills in test commands, files, risk and UI checks, and keeps every spec ref.")
        else:
            print("checkpoints.json already exists, so it was left alone (use --force to replace it with a draft).")
        if add:
            print("Added to the standards reviewers enforce: " + ", ".join(add))
        return
    if args.spec_cmd == "keep":
        user = read_json(ctx.config_path, {}) or {}
        user["spec_keep"] = args.choice == "on"
        write_json(ctx.config_path, user)
        if args.choice == "off":
            ok("Noted: no running spec for this project. You won't be asked again.")
            return
        ok("Noted: Ratchet keeps a running spec for this project, updated at the end of every run.")
        try:
            st = ctx.state()
        except SystemExit:
            return
        if st.get("phase") == "done" and not spec_source(ctx):
            print(write_living_spec(ctx, st))
            ctx.save_state(st)
        return
    if args.spec_cmd == "status":
        src = spec_source(ctx)
        if not src:
            print("This run wasn't started from a spec.")
            return
        st = ctx.state()
        items = spec_items(ctx, src)
        plan = ctx.plan() or {"checkpoints": []}
        cov = spec_coverage(ctx, plan, items)
        done = {cid for cid, c in (st.get("checkpoints") or {}).items() if c.get("status") == "done"}
        rows = []
        for ref in cov["scenarios"]:
            cases = cov["by_case"].get(ref) or []
            steps = sorted({c["checkpoint"] for c in cases} | set(cov["ui_steps"].get(ref) or []))
            state = "not planned" if not steps else ("proven" if all(s in done for s in steps) else "planned")
            if ref in cov["unmapped"]:
                state = "left out: " + cov["unmapped"][ref]
            rows.append({"ref": ref, "tests": [c["case"] for c in cases], "steps": steps, "state": state})
        ticked = set(cov["done_tasks"])
        trows = [{"ref": r, "steps": cov["by_step"].get(r) or [], "ticked": r in ticked} for r in cov["tasks"]]
        if args.json:
            print(json.dumps({"items": [{"tool": it["tool"], "id": it["id"], "title": it.get("title"),
                                         "status": it.get("status")} for it in items],
                              "scenarios": rows, "tasks": trows, "problems": spec_plan_problems(ctx, plan)}, indent=2))
            return
        proven = sum(1 for r in rows if r["state"] == "proven")
        print("Built from: " + "; ".join(_spec_label(it) for it in items))
        print(f"Scenarios proven by passing tests: {proven} of {len(rows)}; tasks ticked: "
              f"{sum(1 for t in trows if t['ticked'])} of {len(trows)}")
        for r in rows:
            print(f"  - {r['ref']}: {r['state']}" + (f" ({', '.join(r['tests'])})" if r["tests"] else ""))
        return


def spec_summary_lines(ctx, plan) -> list[str]:
    src = spec_source(ctx)
    if not src:
        return []
    items = spec_items(ctx, src)
    cov = spec_coverage(ctx, plan, items)
    approved = all(it.get("approved") for it in items)
    lines = ["Built from: " + "; ".join(_spec_label(it) for it in items) +
             (" (already agreed by the team)" if approved else " (not marked approved yet)")]
    n = len(cov["scenarios"])
    lines.append(f"Spec coverage: {n - len(cov['no_test']) - sum(1 for r in cov['scenarios'] if r in cov['unmapped'])}"
                 f" of {n} scenario(s) have a planned test; {len(cov['tasks']) - len(cov['no_step'])} of "
                 f"{len(cov['tasks'])} task(s) are in a step.")
    for ref, why in cov["unmapped"].items():
        lines.append(f"Left out on purpose: {ref} ({why})")
    if cov["unclear"]:
        lines.append("Still marked NEEDS CLARIFICATION in the spec: " + ", ".join(cov["unclear"]))
    return lines


def _step_spec_line(cp) -> str | None:
    refs = _as_list(cp.get("spec_refs") or [])
    if not refs:
        return None
    tasks = [r.split("#", 1)[1] for r in refs if "#task:" in r or re.search(r"#T\d+$", r)]
    scen = [r for r in refs if r not in tasks and not ("#task:" in r or re.search(r"#T\d+$", r))]
    bits = []
    if scen:
        bits.append(f"{len(scen)} requirement(s)/criteria")
    if tasks:
        bits.append("tasks " + ", ".join(t.replace("task:", "") for t in tasks[:8]) + ("…" if len(tasks) > 8 else ""))
    return ", ".join(bits)


def _compose(fns):
    def run(text):
        for fn in fns:
            text = fn(text)
        return text
    return run


def _spec_write_back(ctx, st, cp, fp, base_tree=None) -> tuple[dict, list[str]]:
    """After a step passes every gate: tick its tasks in the spec files, and finish a BMAD story whose steps are all
    done. The working files are updated, and the same edits are applied to the reviewed version of each file for the
    commit, so the user's own unrelated edits to a spec file never ride along. Returns ({file: text to commit},
    plain notes)."""
    src = spec_source(ctx)
    refs = set(_as_list(cp.get("spec_refs") or []))
    if not src or not refs:
        return {}, []
    items = [it for it in spec_items(ctx, src) if not it.get("missing")]
    edits, notes = {}, []
    for rel, (iid, style) in specsrc.task_files(ctx.root, items, refs).items():
        edits.setdefault(rel, []).append(lambda t, iid=iid, style=style: specsrc.tick_text(t, iid, style, refs))
    fresh_ticks = sum(1 for it in items for t in it["tasks"] if t["ref"] in refs and not t["done"])
    plan = ctx.plan() or {"checkpoints": []}
    for it in items:
        if it["tool"] != "bmad":
            continue
        mine = [c for c in plan["checkpoints"] if any(r.startswith(it["id"] + "#") for r in _as_list(c.get("spec_refs") or []))]
        if not mine or cp["id"] not in [c["id"] for c in mine] or \
                any(c["id"] != cp["id"] and st["checkpoints"][c["id"]]["status"] != "done" for c in mine):
            continue
        lines = [f"Built and checked by Ratchet (run {st['slug']}). Every step passed its tests and an independent "
                 "code review before it was committed."]
        for c in mine:
            commit = st["checkpoints"][c["id"]].get("commit")
            lines.append(f"- {c['id']} {c['title']}" + (f" (commit {commit})" if commit else ""))
        for ac in it["requirements"]:
            proof = [x["id"] for c in mine for x in (test_plan(ctx, c["id"]) or {}).get("cases") or []
                     if ac["ref"] in _as_list(x.get("spec_ref"))]
            lines.append(f"- {ac['title']}: " + (", ".join(proof) if proof else "no automated test (see the plan)"))
        evidence, version = "\n".join(lines), it.get("version", "v4")
        edits.setdefault(it["path"], []).append(lambda t, e=evidence, v=version: specsrc.bmad_finish_text(t, v, e)[0])
        ss = specsrc.sprint_status_path(it)
        if ss and (ctx.root / ss).exists():
            key = Path(it["path"]).stem
            edits.setdefault(ss, []).append(lambda t, k=key: specsrc.sprint_done_text(t, k))
        if it.get("status_line"):
            notes.append(f"marked BMAD story {it['id']} done and added Ratchet's evidence to it")
        else:
            notes.append(f"added Ratchet's evidence to BMAD story {it['id']}, but couldn't find its Status line to mark "
                         "it done")
    changed = [rel for rel, fns in edits.items()
               if (ctx.root / rel).is_file() and specsrc._edit_file(ctx.root / rel, _compose(fns))]
    if fresh_ticks:
        notes.insert(0, f"ticked {fresh_ticks} task(s) in " + ", ".join(sorted(changed) or sorted(edits)))
    blobs = {}
    if fp.get("kind") == "git-tree":
        # the version to tick: the reviewed one if this step changed the file, else the committed one (so edits the
        # user had made to it before the step started stay theirs, uncommitted, like any other file)
        stepped = set(changed_files(ctx.root, {"kind": "git-tree", "id": base_tree}, fp)) if base_tree else set()
        for rel, fns in edits.items():
            r = None
            if rel not in stepped:
                r = subprocess.run(["git", "cat-file", "blob", f"HEAD:{rel}"], cwd=ctx.root, capture_output=True)
            if r is None or r.returncode != 0:
                r = subprocess.run(["git", "cat-file", "blob", f"{fp['id']}:{rel}"], cwd=ctx.root, capture_output=True)
            if r.returncode == 0:  # in neither (e.g. ignored by git): nothing to commit
                blobs[rel] = specsrc._edit_text(specsrc._decode(r.stdout), _compose(fns))
    return blobs, notes


def _openspec_files(root: Path) -> dict:
    out = {}
    base = root / "openspec"
    for dp, dns, fns in os.walk(base) if base.is_dir() else []:
        for fn in fns:
            f = Path(dp) / fn
            try:
                out[str(f.relative_to(root))] = hashlib.sha1(f.read_bytes()).hexdigest()
            except OSError:
                continue
    return out


def _commit_paths(root: Path, paths: list[str], msg: str) -> str | None:
    """Commit exactly these paths as they are in the working tree (deleted ones removed), and nothing else: e.g. the
    files an OpenSpec archive moved and merged. The user's other changes and staged files stay as they were."""
    head = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD^{commit}"], cwd=root, capture_output=True,
                          text=True).stdout.strip()
    if not head or not paths:
        return None
    info = []
    for rel in paths:
        f = root / rel
        if f.is_file() and not f.is_symlink():
            oid = subprocess.run(["git", "hash-object", "-w", "--", str(f)], cwd=root, capture_output=True,
                                 text=True).stdout.strip()
            info.append(f"{'100755' if os.access(f, os.X_OK) else '100644'} {oid}\t{rel}")
        else:
            info.append(f"0 {'0' * 40}\t{rel}")
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"), GIT_LITERAL_PATHSPECS="1")
        subprocess.run(["git", "read-tree", head], cwd=root, env=env, capture_output=True)
        subprocess.run(["git", "update-index", "-z", "--index-info"], cwd=root, env=env, capture_output=True,
                       input="\0".join(info).encode("utf-8", "surrogateescape") + b"\0")
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env, capture_output=True, text=True).stdout.strip()
        if not tree or tree == subprocess.run(["git", "rev-parse", f"{head}^{{tree}}"], cwd=root, capture_output=True,
                                              text=True).stdout.strip():
            return None
        new = subprocess.run(["git", "commit-tree", tree, "-p", head, "-m", msg], cwd=root, capture_output=True,
                             text=True).stdout.strip()
    if not new:
        return None
    subprocess.run(["git", "update-ref", "HEAD", new, head], cwd=root, capture_output=True)
    subprocess.run(["git", "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul"], cwd=root,
                   capture_output=True, input="\0".join(paths).encode("utf-8", "surrogateescape"),
                   env=dict(os.environ, GIT_LITERAL_PATHSPECS="1"))
    return new[:7]


OFFER_KEEP = ("Offer: this project doesn't use a spec tool. Ask the user once, in plain words, whether you should keep "
              "a running spec of what has been built: a plain Markdown file per feature saying what the app does, each "
              "rule linked to the test that proves it, written at the end of each run and committed with it (it follows "
              "OpenSpec's file layout, so they could adopt OpenSpec later without losing anything). Then run "
              "`ratchet.py spec keep on` or `spec keep off` with their answer; it is remembered and never asked again.")


def wants_keep_offer(ctx) -> bool:
    return ctx.config().get("spec_keep") is None and not spec_source(ctx) and not specsrc.detect(ctx.root)


def living_spec_text(ctx, st) -> str:
    """The finished run as an OpenSpec-format capability spec: a requirement per step, a scenario per planned test."""
    plan = ctx.plan() or {"checkpoints": []}
    out = [f"# {plan.get('feature') or st.get('feature') or st['slug']}", "", "## Purpose",
           f"What this feature does, as built and checked by Ratchet (run {st['slug']}, {now()[:10]}). Each scenario "
           "names the test that proves it.", "", "## Requirements", ""]
    for cp in plan["checkpoints"]:
        if cp.get("guardrail") or (st.get("checkpoints") or {}).get(cp["id"], {}).get("status") != "done":
            continue  # automatic checks are about the code, not what the app does
        commit = st["checkpoints"][cp["id"]].get("commit")
        files = ", ".join((cp.get("tests") or {}).get("files") or [])
        out += [f"### Requirement: {cp['title']}", f"The system SHALL do this: {cp['summary']}", ""]
        out += [f"- {d}" for d in cp.get("done_when") or []] + [""]
        for c in (test_plan(ctx, cp["id"]) or {}).get("cases") or []:
            out.append(f"#### Scenario: {c.get('title') or c['id']}")
            if c.get("given"):
                out.append(f"- **GIVEN** {c['given']}")
            out.append(f"- **WHEN** {c.get('when') or 'this behaviour is exercised'}")
            out.append(f"- **THEN** {c.get('then') or c.get('title') or 'it works as described'}")
            how = "checked by the UI review" if c.get("layer") == "browser" else f"test {c['id']}" + (f" in {files}" if files else "")
            out += [f"<!-- proven by {how}" + (f", commit {commit}" if commit else "") + " -->", ""]
    return "\n".join(out).rstrip() + "\n"


def write_living_spec(ctx, st) -> str:
    rel = f"openspec/specs/{st['slug']}/spec.md"
    p = ctx.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(living_spec_text(ctx, st), encoding="utf-8")
    commit = None
    cfg = ctx.config()
    if cfg.get("commit_per_checkpoint") and _is_git(ctx.root):
        paths = [rel] + ([f"{RATCHET_DIR}/config.json"] if ctx.config_path.exists() else [])
        commit = _commit_paths(ctx.root, paths, f"ratchet({st['slug']}): record what was built in {rel}")
    st["living_spec"] = rel
    ctx.log(st, "living_spec_written", path=rel, commit=commit)
    return f"Recorded what was built in {rel}" + (f" (commit {commit})." if commit else ".")


def _spec_finish(ctx, st) -> list[str]:
    """When the whole run is approved: archive OpenSpec changes (merging their deltas into the living specs). With no
    spec tool: keep the running spec if the user asked for one, or say to offer it once."""
    src = spec_source(ctx)
    if not src:
        keep = ctx.config().get("spec_keep")
        if keep is True:
            return [write_living_spec(ctx, st)]
        return [OFFER_KEEP] if keep is None and wants_keep_offer(ctx) else []
    if st.get("spec_finished"):
        return []
    cfg = ctx.config()
    notes = []
    for it in spec_items(ctx, src):
        if it["tool"] != "openspec" or it.get("missing") or it.get("archived"):
            continue
        if not cfg.get("spec_archive", True):
            notes.append(f"OpenSpec change {it['id']} is built; archiving is turned off (spec_archive), so it stays open.")
            continue
        open_tasks = [t["ref"] for t in it["tasks"] if not t["done"]]
        if open_tasks:
            notes.append(f"OpenSpec change {it['id']} still has {len(open_tasks)} unticked task(s), so it wasn't archived.")
            continue
        before = _openspec_files(ctx.root)
        done, why = specsrc.openspec_archive(ctx.root, it["id"])
        after = _openspec_files(ctx.root)
        moved = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        if not done:
            notes.append(f"OpenSpec change {it['id']} is built but wasn't archived: {why}.")
            continue
        commit = None
        if cfg.get("commit_per_checkpoint") and _is_git(ctx.root):
            commit = _commit_paths(ctx.root, moved, f"ratchet({st['slug']}): archive OpenSpec change {it['id']}")
        notes.append(f"Archived OpenSpec change {it['id']}: its spec changes are now part of the main specs"
                     + (f" (commit {commit})." if commit else "."))
    st["spec_finished"] = notes or True
    ctx.log(st, "spec_finished", notes="; ".join(notes)[:400])
    return notes


_SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2, "nit": 2, "nitpick": 2, "trivial": 2, "low": 2, "style": 2,
                   "info": 3, "suggestion": 3}


def _accepted_findings_lines(st) -> list[str]:
    """Findings the user accepted with `ratchet.py ask accept-finding` / `accept-finding`, blockers and majors
    first, for `status`, `plan-summary` and `deferred` (the final review's own command) to show in their own
    section: they were reviewed and agreed to on purpose, with a reason and who accepted them, so they must never
    read as a finding that simply 'did not block'."""
    rows = [(cp, f) for cp, c in (st.get("checkpoints") or {}).items() for f in c.get("deferred") or []
            if f.get("accepted")]
    if not rows:
        return []
    rows.sort(key=lambda cf: _SEVERITY_RANK.get(str(cf[1].get("severity", "")).strip().lower(), 4))
    lines = ["Findings accepted by you (blockers and majors first; they no longer block):"]
    for cp, f in rows:
        desc = str(f.get("description", "")).strip()[:200]
        who = f.get("accepted_by") or "human"
        lines.append(f"  {cp} {_finding_key(f)} ({f.get('severity', '?')}): {desc}  -  reason: {f.get('reason', '')}"
                     f"  -  accepted by: {who}")
    return lines


def _checkin_decisions_lines(st) -> list[str]:
    """Check-in decisions recorded so far (a simplify's reasoning, or a stop), for `plan-summary` to show next to
    the findings the user accepted: the orchestrator's own reasoning for narrowing scope is on the record too.
    Never shown by plain `status` (an open check-in prints its own brief instead; a past decision must never make
    `status` look like one is open again when it isn't)."""
    rows = [(cp, d) for cp, c in (st.get("checkpoints") or {}).items() for d in (c.get("checkin") or {}).get("decided") or []]
    if not rows:
        return []
    lines = ["Check-in decisions along the way:"]
    for cp, d in rows:
        if d.get("decision") == "simplify":
            lines.append(f"  {cp} round {d.get('round')}: simplify - {d.get('why', '')}")
        else:
            lines.append(f"  {cp} round {d.get('round')}: {d.get('decision')}")
    return lines


def cmd_deferred(args):
    """Findings that were recorded instead of blocking (minor ones, and any blocker/major/unknown-severity
    finding with no user_impact), for the user to decide on at the end, PLUS, in their own section first,
    findings the user accepted while a round was blocked: these were shown to the user and agreed to on purpose,
    with a reason and who accepted them, so they are never mixed in with ones that simply never blocked."""
    ctx = Ctx(find_root())
    st = ctx.state()
    rows = [(cp, f) for cp, c in (st.get("checkpoints") or {}).items() for f in c.get("deferred") or []]
    if getattr(args, "json", False):
        print(json.dumps([dict(f, checkpoint=cp) for cp, f in rows], indent=2))
        return
    if not rows:
        print("No findings were left for later.")
        return
    other = [(cp, f) for cp, f in rows if not f.get("accepted")]
    acc_lines = _accepted_findings_lines(st)
    if acc_lines:
        for line in acc_lines:
            print(line)
        if other:
            print("")
    if other:
        print(f"{len(other)} finding(s) were left for later (they did not block):")
        for cp, f in other:
            where = f" ({f['file']}{':' + str(f['line']) if f.get('line') else ''})" if f.get("file") else ""
            why = f" [{f['deferred_because']}]" if f.get("deferred_because") else ""
            print(f"- {cp} {_finding_key(f)}{where}: {f.get('description', '').strip()[:240]}{why}")


def cmd_complete(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["complete"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    cfg = ctx.config()
    fp = fingerprint(ctx.root)
    problems, stale = [], []
    for name in ["behavior", "ui", "adversarial"]:
        g = cps["gates"][name]
        if not g["required"]:
            continue
        if g["status"] != "passed":
            problems.append(f"{name} gate is {g['status']}")
            stale.append(name)
            continue
        gfp = g.get("fingerprint") or {}
        if gfp.get("id") == fp["id"]:
            continue
        # The fingerprint never reads Ratchet's folder from the working tree, only from HEAD: a difference there alone
        # is a checkpoint commit (its shared .ratchet files) made by an earlier `complete` that failed before it
        # could save, not a change to the reviewed code. The rerun then finds that commit instead of making another.
        chat_paths = _chat_transcript_paths(ctx, g.get("passed_at"))
        changed = [f for f in changed_files(ctx.root, gfp, fp) if f != RATCHET_DIR and not f.startswith(RATCHET_DIR + "/")
                  and f not in chat_paths]
        if not changed:
            continue
        if name == "behavior":
            problems.append(f"code changed since behavior passed ({len(changed)} files) → re-run ratchet.py behavior {args.cp}")
            stale.append(name)
        elif name == "ui":
            ui_changed = touches_ui(changed, cfg["ui_globs"])
            if ui_changed:
                problems.append(f"UI files changed since UI review ({', '.join(ui_changed[:5])}) → re-run ratchet-ui-compare")
                stale.append(name)
        elif name == "adversarial":
            problems.append("code changed since adversarial approval → adversaries must review the final code")
            stale.append(name)
    if problems:
        for name in stale:
            if cps["gates"][name]["status"] == "passed":
                cps["gates"][name]["status"] = "stale"
        cps["step"] = stale[0]  # behavior < ui < adversarial order is preserved
        ctx.log(st, "complete_rejected", cp=args.cp, stale=stale)
        ctx.save_state(st)
        die(f"{args.cp} cannot complete:\n  - " + "\n  - ".join(problems), 2)
    if cfg.get("regression_command"):
        code, out, _ = _run_cmd(ctx, cfg["regression_command"], ctx.evidence(args.cp) / "regression.log",
                                cfg["test_timeout_sec"])
        if code != 0:
            print(_tail(out))
            die(f"Regression suite failed (exit {code}) — an earlier behaviour broke. Fix before completing.", 2)
        if fingerprint(ctx.root)["id"] != fp["id"]:
            die("The regression command changed files in the project. Those changes were never reviewed — find out "
                "what wrote them (generated files? snapshots?) and either add them to .gitignore or re-run the gates.", 2)
    if cp.get("guardrail"):
        probs = verify_guardrail(ctx, st, cp, cps, fp, cfg)
        if probs:
            cps["guardrail_tries"] = int(cps.get("guardrail_tries", 0)) + 1
            if cps["guardrail_tries"] >= cfg["max_behavior_attempts"]:
                _set_blocked(st, f"{args.cp}: the new automatic check still fails Ratchet's own proof after "
                                 f"{cps['guardrail_tries']} tries — needs a human decision.")
            for g_ in ("behavior", "adversarial"):
                if cps["gates"][g_]["status"] == "passed":
                    cps["gates"][g_]["status"] = "stale"
            cps["step"] = "behavior"
            ctx.log(st, "guardrail_proof_failed", cp=args.cp, problems="; ".join(probs)[:400])
            ctx.save_state(st)
            die(f"{args.cp} cannot complete: Ratchet ran the new check itself and " + "; ".join(probs) + ". Give this "
                "to ratchet-implementer to fix the check (evidence/" + args.cp + "/guardrail has every run), then "
                "re-run the gates.", 2)
        _finish_guardrail(ctx, st, cp)  # before the commit, so the new check's config entry is committed with it
    spec_files, spec_notes = _spec_write_back(ctx, st, cp, fp, cps.get("base_tree"))  # tick the step's tasks in the spec tool's own files
    commit = None
    if cfg.get("commit_per_checkpoint") and _is_git(ctx.root):
        msg = f"ratchet({st['slug']}): {args.cp} {cp['title']}\n\n{cp['summary']}\n\nGates: " + ", ".join(
            f"{n}={cps['gates'][n]['status']}" for n in ["behavior", "ui", "adversarial"])
        if cp.get("spec_refs"):
            msg += "\nSpec: " + ", ".join(_as_list(cp["spec_refs"]))[:600]
        commit = _commit_reviewed(ctx.root, cps.get("base_tree"), fp, msg, extra=spec_files)
        if not commit:  # nothing new to commit: a run of complete that failed after its commit left it at HEAD
            commit = _head_commit_named(ctx.root, msg)
    cps.update(status="done", completed_at=now(), commit=commit)
    ctx.log(st, "checkpoint_complete", cp=args.cp, commit=commit)
    order = [c["id"] for c in ctx.plan()["checkpoints"]]
    remaining = [c for c in order if st["checkpoints"][c]["status"] != "done"]
    mode = st.get("human_review", "end")
    if mode == "each":
        st["phase"] = "awaiting_checkpoint_review"
        st["awaiting_since"] = now()
        st["review_target"] = args.cp
    elif remaining:
        _advance(st, remaining[0], fingerprint(ctx.root)["id"])
    else:
        st["current"] = None
        st["phase"] = "awaiting_final_review" if mode != "none" else "done"
        st["awaiting_since"] = now()
        if st["phase"] == "done":
            spec_notes += _spec_finish(ctx, st)
            register_active(ctx.root, None)
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"{args.cp} complete{f' (commit {commit})' if commit else ''}.")
    for n_ in spec_notes:
        print(n_ if n_.startswith("Offer:") else f"Spec: {n_}")
    print(json.dumps(next_action(ctx, st), indent=2))


def _earliest_open_step(cps):
    for name in ["behavior", "ui", "adversarial"]:
        g = cps["gates"][name]
        if g["required"] and g["status"] != "passed":
            return name
    return "complete"


def _advance(st, cp_id, base_tree=None):
    st["current"] = cp_id
    st["phase"] = "building"
    st["checkpoints"][cp_id]["status"] = "in_progress"
    st["checkpoints"][cp_id]["started_at"] = now()
    if base_tree:
        st["checkpoints"][cp_id]["base_tree"] = base_tree  # what reviewers diff against


def cmd_human_approve(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] not in ("awaiting_checkpoint_review", "awaiting_final_review", "blocked"):
        die(f"Nothing awaiting human approval (phase {st['phase']})")
    allow = st.get("choice_allow") or {}
    if st["phase"] == "blocked" and allow.get("unblock") and not getattr(args, "director_token", None):
        msg = {"by": "human", "prompt": f"picked option {allow['letter']}: {allow.get('reply', '')}"}
    else:
        msg = _require_human(ctx, st, "human-approve", getattr(args, "director_token", None))
    if (st.get("pending") or {}).get("action") == "human-approve":
        st.pop("pending", None)  # answered (or overtaken by the user's pick at a block)
    order = [c["id"] for c in (ctx.plan() or {"checkpoints": []})["checkpoints"]]
    if st["phase"] == "blocked":
        _unblock(ctx, st, msg["by"], msg.get("prompt", "")[:200])
    else:
        target = st.get("review_target")
        done_ids = [target] if target else [c for c in order if st["checkpoints"][c]["status"] == "done"]
        for cid in done_ids:
            st["checkpoints"][cid]["gates"]["human"].update(status="passed", passed_at=now(), by=msg["by"])
        remaining = [c for c in order if st["checkpoints"][c]["status"] != "done"]
        st["review_target"] = None
        if remaining:
            _advance(st, remaining[0], fingerprint(ctx.root)["id"])
        else:
            st["phase"] = "done"
            st["current"] = None
            for n_ in _spec_finish(ctx, st):
                print(n_ if n_.startswith("Offer:") else f"Spec: {n_}")
            register_active(ctx.root, None)
            _stop_server(ctx.ratchet / "viewer.json")
        ctx.log(st, "approved", by=msg["by"], note=msg.get("prompt", "")[:200])
    # "done" still accepts later feedback, so it keeps a pause marker
    st["awaiting_since"] = now() if st["phase"] in ("done", "awaiting_final_review") else None
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"Human approval recorded. Phase → {st['phase']}")
    print(json.dumps(next_action(ctx, st), indent=2))


def cmd_feedback(args):
    """Human gave change requests at a review point: record learnings, then planner adds checkpoints."""
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] not in ("awaiting_checkpoint_review", "awaiting_final_review", "done"):
        die(f"feedback is for review points (phase {st['phase']})")
    msg = _require_human(ctx, st, "feedback", getattr(args, "director_token", None))
    if (st.get("pending") or {}).get("action") == "human-approve":
        st.pop("pending", None)  # their reply asked for changes, which answers the approval question
    for line in args.learning or []:
        _append_learning(ctx, line, source=f"human review of {st['slug']}")
    st["phase"] = "planning_feedback"
    st["feedback"] = st.get("feedback", []) + [{"at": now(), "text": args.text or msg.get("prompt", "")}]
    ctx.log(st, "human_feedback", text=(args.text or msg.get("prompt", ""))[:300])
    ctx.save_state(st)
    ok("Feedback recorded. Spawn ratchet-checkpoint-planner in EXTEND mode to append new checkpoints, "
       "ratchet-test-planner for each new one, then run ratchet.py plan-extend.")


def cmd_plan_extend(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] != "planning_feedback":
        die("plan-extend is only valid after ratchet.py feedback")
    plan = ctx.plan()
    errs = validate_plan(plan)
    if errs:
        die("Plan invalid:\n  - " + "\n  - ".join(errs))
    had_objective = (st.get("approved") or {}).get("objective")
    if had_objective and not objective_problems({"objective": had_objective}) and objective_problems(plan):
        die("Feedback cannot drop the objective the approved plan had (" + "; ".join(objective_problems(plan)) + ").")
    new_ids = [c["id"] for c in plan["checkpoints"] if c["id"] not in st["checkpoints"]]
    if not new_ids:
        die("No new checkpoints were appended to checkpoints.json")
    for cid in st["checkpoints"]:
        if cid not in [c["id"] for c in plan["checkpoints"]]:
            die(f"Existing checkpoint {cid} was removed from the plan — completed work is append-only.")
    approved_cps = (st.get("approved") or {}).get("cps", {})
    edited = [c["id"] for c in plan["checkpoints"] if c["id"] in approved_cps and _sha(c) != approved_cps[c["id"]]]
    if edited:
        die(f"Existing checkpoint(s) {edited} were edited. Feedback only APPENDS new checkpoints; restore the originals.")
    missing = [c for c in new_ids if cp_by_id(plan, c)["gates"].get("behavior") and not test_plan(ctx, c)]
    if missing:
        die(f"Test plans missing for new checkpoints {missing}")
    for cid in new_ids:
        st["checkpoints"][cid] = new_cp_state(cp_by_id(plan, cid))
    st["plan_hash"] = _plan_hash(plan, ctx)
    _advance(st, new_ids[0], fingerprint(ctx.root)["id"])
    st["approved"] = approval_snapshot(ctx, st)  # the user's feedback is the approval for the appended checkpoints
    register_active(ctx.root, st["slug"])
    ctx.log(st, "plan_extended", added=new_ids)
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"Added {', '.join(new_ids)}. Building {new_ids[0]} through all gates.")


def _append_learning(ctx, text, source):
    p = ctx.ratchet / "learnings.md"
    with p.open("a") as f:
        f.write(f"- ({_dt.date.today().isoformat()}, {source}) {text.strip()}\n")


def cmd_learn(args):
    ctx = Ctx(find_root())
    _append_learning(ctx, args.text, args.source or "orchestrator")
    ok("Learning recorded in .ratchet/learnings.md")


def cmd_pause(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] in ("paused", "done", "cancelled"):
        ok(f"Nothing to pause (phase {st['phase']}).")
        return
    st["paused_from"] = st["phase"]
    st["phase"] = "paused"
    st["blocked_reason"] = args.reason or "paused"
    ctx.log(st, "paused", reason=args.reason)
    ctx.save_state(st)
    ok("Paused. The Stop hook now lets the agent stop. Resume with ratchet.py resume.")


def live_wait(st) -> dict | None:
    """The run's wait (`ratchet.py wait`) while it still applies, else None. It applies only while the run is being
    built or planned (not paused, blocked, done or cancelled), to the chat that set it while that chat still owns the
    run, before its `until` time, and until any gate progress is logged after it was set. A record that does not have
    this shape (missing fields, a set time in the future, a window longer than WAIT_MAX_MINUTES) does not apply."""
    w = st.get("wait")
    if not isinstance(w, dict) or st.get("phase") in ("blocked", "paused", "done", "cancelled"):
        return None
    reason, set_at, until, sess = w.get("reason"), w.get("set_at"), w.get("until"), w.get("session")
    if not all(isinstance(v, str) and v for v in (reason, set_at, until, sess)) or sess != st.get("owner_session"):
        return None
    t_set, t_until, t_now = _iso(set_at), _iso(until), time.time()
    if not t_set or not t_until or t_set > t_now + 60 or t_until <= t_now or t_until - t_set > WAIT_MAX_MINUTES * 60 + 60:
        return None
    ends = PROGRESS_EVENTS | WAIT_ENDING_EVENTS
    if any(e.get("event") in ends and _iso(e.get("at", "")) > t_set for e in st.get("history") or []
           if isinstance(e, dict)):
        return None
    return {"reason": reason, "set_at": set_at, "until": until, "session": sess}


def cmd_wait(args):
    """The owner chat says it is waiting on background work (reviewers, sub-agents), so the stop hook lets it end its
    turn until `until` without counting a blocked stop. Any gate progress ends the wait early."""
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] in ("blocked", "paused", "done", "cancelled"):
        die(f"Nothing to wait for: run '{st['slug']}' is {st['phase']}, so the stop hook already lets you stop.")
    reason = " ".join(str(args.reason or "").split())[:300]
    if not reason:
        die('Say what you are waiting on, e.g. `ratchet.py wait --for "the codex review of CP2" --minutes 20`.')
    # The chat running this command is one Ratchet saw itself, never a value the caller supplies: the chat the guard
    # hook noted for this exact call, else (a host whose guard hook can't see the call) the chat the user typed in
    # last, as `ask claim` does. `--session` is only a cross-check: `owner_session` is plain text any local process
    # can read, so a bare --session proves nothing.
    noted = _noted_session(ctx.root, "wait")
    who = noted or _latest_session(ctx)
    owner = st.get("owner_session")
    if not owner:
        die("No chat owns this Ratchet run yet, so Ratchet can't tell whose turn may end. Nothing was recorded; "
            "carry on with `ratchet.py next`.")
    if not who:
        die("Ratchet could not tell which chat ran this command (its guard hook did not see the call, and it has "
            "recorded no message from the user), so nothing was recorded. --session alone is not proof of which chat "
            "this is. Keep working on `ratchet.py next`.")
    if args.session and args.session != who:
        die("The chat named by --session is not the one Ratchet sees running this command ("
            + ("the chat its guard hook noted for this call" if noted else
               "its guard hook did not see the call, so that is the chat the user typed in last")
            + "). --session alone is not proof of which chat this is. Nothing was recorded.")
    if who != owner:
        die(f"Only the chat that owns run '{st['slug']}' may wait on background work; this one does not, and its "
            "stops are not held by this run anyway. Nothing was recorded.")
    try:
        minutes = float(WAIT_DEFAULT_MINUTES if args.minutes is None else args.minutes)
    except (TypeError, ValueError):
        die("--minutes must be a number of minutes, e.g. --minutes 20.")
    if minutes != minutes:  # NaN
        minutes = WAIT_DEFAULT_MINUTES
    minutes = int(round(min(max(minutes, 1), WAIT_MAX_MINUTES)))
    t0 = _dt.datetime.now(_dt.timezone.utc)
    until = (t0 + _dt.timedelta(minutes=minutes)).isoformat(timespec="microseconds")
    st["wait"] = {"reason": reason, "set_at": t0.isoformat(timespec="microseconds"), "until": until,
                  "session": who, "minutes": minutes}
    ctx.log(st, "wait_set", reason=reason, until=until, minutes=minutes)
    ctx.save_state(st)
    ok(f"Waiting on: {reason}. Until {until[:16].replace('T', ' ')} UTC ({minutes} min) the stop hook lets this chat "
       "end its turn, and those stops do not count as blocked stops. Any gate progress ends the wait early. When the "
       "background work reports back, carry on with `ratchet.py next`.")


CONSUMING_EVENTS = {"plan_approved", "approved", "unblocked", "changes_approved", "review_mode_changed",
                    "cancelled", "human_feedback", "resumed"}


def _decision_window(st) -> str:
    """For decisions taken mid-build (approve-changes, cancel, set-review): messages after the user's last decision."""
    times = [e["at"] for e in st.get("history", []) if e.get("event") in CONSUMING_EVENTS]
    return max(times) if times else st.get("created_at", "0")


def _latest_session(ctx):
    human = [e for e in _human_since(ctx, "0") if e.get("session")]
    return human[-1]["session"] if human else None


def cmd_resume(args):
    """Resume a paused run, or claim an active run for THIS session (the Stop hook binds to the owner session)."""
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] in ("done", "cancelled"):
        die(f"Run '{st['slug']}' is {st['phase']}; start a new one with init.")
    if args.claim:
        _require_pending(st, "claim")
        msg = _require_human(ctx, st, "claim")
        st["owner_session"] = msg.get("session") or st.get("owner_session")
        _clear_pending(st)
    if st["phase"] == "paused":
        st["phase"] = st.pop("paused_from", "building")
        st["blocked_reason"] = None
    register_active(ctx.root, st["slug"])
    ctx.log(st, "resumed")  # also restarts the stop hook's count of stops without progress
    ctx.save_state(st)
    ok(f"Resumed '{st['slug']}' in phase {st['phase']}" + (" — this chat now owns the run." if args.claim else "."))
    print(json.dumps(next_action(ctx, st), indent=2))


ASKABLE = {"approve-plan": "start building the plan as it is",
           "human-approve": "mark this as approved and carry on",
           "approve-changes": "accept the changes listed above as the new baseline",
           "cancel": "cancel this Ratchet run (commits already made are kept)",
           "set-review": "change how often you review",
           "claim": "continue this Ratchet run in THIS chat",
           "hooks-waive": "build without the Ratchet hooks (the checks then only advise; nothing enforces them)",
           "ui-respec": "change what the UI check compares for this step (its capture steps or scope), as explained above",
           "choice": "go with one of the lettered options you listed",
           "accept-finding": "accept the open finding(s) named above as a known limit, with your own reason, instead "
                             "of running another review round"}
# the command that carries out a "yes" to each question
PENDING_COMMAND = {"approve-plan": "approve-plan", "human-approve": "human-approve", "approve-changes": "approve-changes", "cancel": "cancel", "set-review": "set-review",
                   "claim": "resume --claim", "hooks-waive": "hooks-waive", "ui-respec": "ui-respec", "choice": "choice",
                   "accept-finding": "accept-finding"}
REVIEW_MODE_WORDS = {"end": "only at the end", "each": "after every step", "none": "never (fully autonomous)"}


def cmd_ask(args):
    """Record (and print) the question the agent is about to ask the user. Only the user's FIRST reply after it
    answers it, and only a listed yes approves (APPROVAL_REPLIES; hooks-waive and choice have their own replies)."""
    ctx = Ctx(find_root())
    st = ctx.state()
    asker = _noted_session(ctx.root, "ask")  # the chat running this command, when the guard hook saw it
    if args.action == "withdraw":
        p = st.get("pending")
        if not p:
            ok("No question is waiting.")
            return
        _clear_pending(st)
        ctx.log(st, "question_withdrawn", action=p.get("action"))
        ctx.save_state(st)
        ok(f"Withdrew the waiting question ({p.get('action')}). Nothing was changed.")
        return
    if args.action == "set-review" and args.mode not in ("end", "each", "none"):
        die("ask set-review needs the mode: end | each | none")
    if args.action == "ui-respec" and not (args.mode and args.mode in (st.get("checkpoints") or {})):
        die("ask ui-respec needs the checkpoint id, e.g. `ratchet.py ask ui-respec CP2`")
    if args.action == "accept-finding" and not (args.mode and args.mode in (st.get("checkpoints") or {})):
        die("ask accept-finding needs the checkpoint id, e.g. `ratchet.py ask accept-finding CP2 F1,F2`")
    if args.action == "accept-finding" and not getattr(args, "ids", None):
        die("ask accept-finding needs the finding ids to accept, e.g. `ratchet.py ask accept-finding CP2 F1,F2`")
    if args.action not in ("set-review", "ui-respec", "accept-finding") and args.mode:
        die(f"ask {args.action} takes no extra value")
    _bind_owner(ctx, st, args.action, asker)
    if args.action == "choice":
        return _ask_choice(ctx, st, args)
    if args.action == "accept-finding":
        return _ask_accept_finding(ctx, st, args)
    detail = ""
    if args.action == "approve-changes":
        probs = integrity_problems(ctx, st)
        if not probs:
            ok("Nothing has changed since approval — no question needed.")
            return
        detail = "Changed since you approved the plan: " + "; ".join(probs) + "."
    if args.action == "set-review":
        detail = {"end": "I'll only check in with you at the end.", "each": "I'll check in after every step.",
                  "none": "I'll build every step without checking in; the checks still run."}[args.mode]
    what = ASKABLE[args.action]
    if args.action in ("approve-plan", "human-approve"):
        what = _approval_question(ctx, st, args.action)
    old = st.get("pending") or {}
    if args.action == "hooks-waive" and old.get("action") not in (None, "hooks-waive"):
        die(f"A different question ({old.get('action')}) is still waiting for the user's answer. Get that answered "
            "first; a hooks question must not take over their reply.")
    overtook = _overtake(ctx, st, args.action, args.mode, asker)
    asked = now()
    st["pending"] = {"action": args.action, "mode": args.mode, "asked_at": asked, "prev_awaiting": st.get("awaiting_since"),
                     "session": _asking_session(ctx, st, args.action, asker), **overtook}
    if args.action in ("approve-plan", "human-approve"):
        st["pending"]["phase"] = st["phase"]  # the answer only counts at the pause it was asked at
    if args.action != "hooks-waive":  # a hooks question must not open (or reset) any approval window
        st["awaiting_since"] = asked
    ctx.log(st, "question_asked", action=args.action, mode=args.mode)
    ctx.save_state(st)
    if overtook:
        print(_not_asked_text(_ask_cmd(args.action, args.mode), overtook))
        return
    if args.action == "hooks-waive":
        print(f"ASK THE USER: Shall I build without the Ratchet hooks? The checks would then only advise; nothing "
              f"enforces them. To agree, reply exactly \"{HOOKS_WAIVE_REPLY}\". Any other reply means no.")
        return
    if args.action == "set-review":
        what = f"change how often you review to: {REVIEW_MODE_WORDS[args.mode]}"
    q = (f"{detail} Shall I {what}? To say yes, reply with just one of these: {listed_yes_text()}. Only your first "
         "reply counts, and any other reply approves nothing.")
    print(f"ASK THE USER: {q.strip()}")
    print(ASK_ALONE)


# Ratchet can't see what the agent shows the user, so it can't tell a yes to this question from a yes to another
# question or offer in the same message. The agent keeps the message to this one question.
ASK_ALONE = ("NEXT: end your message with that question, word for word, as the only question in it: settle any other "
             "question or offer first, in an earlier turn. Then end your turn. Ratchet can't see what you showed, so "
             "a yes meant for something else in the same message would still count.")


def _asking_session(ctx, st, action, asker=None):
    """The chat whose first reply will answer a question being asked now. A claim belongs to the chat that ran
    `ask claim` (`asker`, noted by the guard hook for this exact call); every other question belongs to the run's
    owner chat. None while no chat owns the run (see _bind_owner). Only a claim with no note (an agent CLI whose guard
    hook doesn't run, or a call the guard couldn't read) falls back to the chat the user typed in last."""
    if not st.get("owner_session"):
        return None
    if action != "claim":
        return st["owner_session"]
    return asker or _latest_session(ctx)


def _bind_owner(ctx, st, action, asker):
    """A question needs a chat to answer it. Before any chat owns the run, the chat that runs `ask` (noted by the
    guard hook) becomes the owner, so only its replies answer the question and the run can't be taken by a reply in
    another chat. With no note (an agent CLI whose guard hook doesn't run) the question waits unbound, and the first
    chat the prompt hook records for the run owns it (decide_prompt)."""
    if st.get("owner_session"):
        return
    if action == "claim":
        die("No chat owns this Ratchet run yet, so there is nothing to take over and nothing was asked. Run "
            "`ratchet.py resume` (without --claim) and carry on in this chat.")
    if asker:
        st["owner_session"] = asker
        ctx.log(st, "owner_recorded", session=asker, via="ask")


def _overtake(ctx, st, action, mode, asker=None) -> dict:
    """A new question while a DIFFERENT one still waits for the user's first reply: the user may see both, and one
    reply can't answer two questions. Plan and step approvals are refused until the waiting one is answered or
    withdrawn. Any other question is recorded but marked as overtaking, so no reply approves it; the agent asks it
    again on its own. Either way the replaced question is logged. Returns the marker to store on the new question."""
    old = st.get("pending") or {}
    oa = old.get("action")
    if not oa:
        return {}  # nothing waiting
    first = _replies(ctx, st, old.get("asked_at"), old)[:1]
    answered = bool(first)
    new_sess = _asking_session(ctx, st, action, asker)
    if not answered and old.get("session") and new_sess != old.get("session"):
        # the waiting question belongs to another chat: a question asked here (even the same one) must not drop it
        die(f"A question is waiting for the user's first reply in another chat (shall I {_question_what(oa, old.get('mode'))}?), "
            "so nothing was asked here. Try again after it is answered there. If the user tells you that chat is "
            "gone, run `ratchet.py ask withdraw` first (that drops its question; it is logged, and that chat has to "
            "ask it again).")
    if oa == action and (old.get("mode") or None) == (mode or None):
        return {}  # the same question printed again in its own chat
    if oa == "hooks-waive" and (not answered or hooks_waive_intent(first[0].get("prompt", "")) == "approve"):
        # `hooks-waive` reads its answer from this question: replacing it before the reply, or after the user agreed,
        # would hide that reply from it. A first reply that is not the waiver phrase is a no, so it can be replaced.
        if answered:
            die(f"The user agreed to build without the Ratchet hooks (their reply to the hooks question was "
                f"\"{first[0].get('prompt', '')[:80]}\"), so nothing was asked. Run `ratchet.py hooks-waive` to record "
                "it first, or `ratchet.py ask withdraw` if they have since changed their mind. Then ask this question "
                "on its own.")
        die("The hooks question (shall I build without the Ratchet hooks?) is still waiting for the user's reply, so "
            "nothing was asked. Wait for their reply: if it is exactly \"" + HOOKS_WAIVE_REPLY + "\", run "
            "`ratchet.py hooks-waive`. If you no longer need it, run `ratchet.py ask withdraw`. Then ask this question "
            "on its own.")
    if not answered and action in ("approve-plan", "human-approve"):
        die(f"A different question is still waiting for the user's first reply: shall I "
            f"{_question_what(oa, old.get('mode'))}? One reply can't answer two questions. Get that one answered "
            f"(if they say yes, run `ratchet.py {PENDING_COMMAND.get(oa, oa)}{' ' + old['mode'] if old.get('mode') else ''}`"
            ") or run `ratchet.py ask withdraw` if you no longer need it, then ask this question on its own.")
    ctx.log(st, "question_overtaken", action=oa, mode=old.get("mode"), by=action, answered=answered)
    if answered:
        return {}  # the old question already had its one reply; this one starts clean
    return {"overtook": oa, "overtook_mode": old.get("mode")}


def _not_asked_text(again, overtook) -> str:
    return (f"NOT ASKED: a different question (shall I {_question_what(overtook['overtook'], overtook.get('overtook_mode'))}"
            "?) was still waiting for the user's first reply. It is now withdrawn, and this question is recorded as "
            "overtaking it, so no reply approves it: if the user saw both, their reply could be meant for either. Do "
            f"not show it. Run {again} again to ask it on its own, then show the user only that "
            "question.")


def _approval_question(ctx, st, action) -> str:
    """The question `ask approve-plan` / `ask human-approve` puts to the user at this pause, or die when there is
    nothing to approve here."""
    if approval_mode(ctx) == "director":
        die("This lane is director-enrolled: a chat reply can't approve anything here. The director signs this "
            f"approval instead (director-sign {action}).")
    phase = st["phase"]
    if action == "approve-plan":
        if phase != "awaiting_plan_approval":
            die(f"There is no plan waiting for approval (the run is {PHASE_PLAIN.get(phase, phase)}).")
        return "start building the plan shown above, as it is"
    if phase == "awaiting_checkpoint_review":
        titles = {c["id"]: c.get("title", c["id"]) for c in (ctx.plan() or {}).get("checkpoints", [])}
        left = [c for c, s in (st.get("checkpoints") or {}).items() if s.get("status") != "done"]
        t = titles.get(st.get("review_target"), "the last step")
        return f"mark the step \"{t}\" as approved and " + ("go on to the next step" if left else "finish the run")
    if phase == "awaiting_final_review":
        return "mark the finished work as approved and close this run"
    if phase == "blocked":
        return "carry on past the problem explained above"
    die(f"Nothing is waiting for the user's approval (the run is {PHASE_PLAIN.get(phase, phase)}).")


def _ask_choice(ctx, st, args):
    """A lettered-options question. An option may approve a change to locked files: then picking it is the approval."""
    if approval_mode(ctx) == "director":
        die("This lane is director-enrolled: a chat reply can't approve anything here. Put the options to the director.")
    opts = [o.strip().upper() for o in (args.options or "").split(",") if o.strip()]
    if len(opts) < 2 or len(set(opts)) != len(opts) or not all(re.fullmatch(r"[A-Z]", o) for o in opts):
        die("ask choice needs --options with two or more single letters, e.g. --options A,B,C")
    lockable = set(_reference_files(ctx))
    cur = st.get("current")
    lockable |= set(((st.get("checkpoints") or {}).get(cur) or {}).get("red_tests") or [])
    locked = {}
    for spec in args.locked or []:
        letter, _, files = spec.partition("=")
        letter = letter.strip().upper()
        if letter not in opts:
            die(f"--locked {spec}: {letter} is not one of the options {','.join(opts)}")
        paths = [f.strip() for f in files.split(",") if f.strip()]
        bad = [f for f in paths if f not in lockable]
        if not paths or bad:
            die(f"--locked {spec}: name the locked files exactly as Ratchet lists them. Locked now: "
                f"{', '.join(sorted(lockable)) or '(none)'}")
        locked[letter] = paths
    guards = {}
    for spec in getattr(args, "guardrail", None) or []:
        letter, _, kind = spec.partition("=")
        letter, kind = letter.strip().upper(), norm_kind(kind)
        if letter not in opts or not kind:
            die(f"--guardrail {spec}: use LETTER=<kind>, with LETTER one of {','.join(opts)}")
        guards[letter] = kind
        offered = st.setdefault("guardrails_offered", [])
        if kind not in offered:
            offered.append(kind)  # asked once; not offered again whatever the answer
    overtook = _overtake(ctx, st, "choice", None)
    asked = now()
    st["pending"] = {"action": "choice", "mode": None, "options": opts, "locked": locked, "guardrail": guards,
                     "asked_at": asked, "prev_awaiting": st.get("awaiting_since"),
                     "session": _asking_session(ctx, st, "choice"), **overtook}
    st["awaiting_since"] = asked
    ctx.log(st, "question_asked", action="choice", options=opts, locked=locked)
    ctx.save_state(st)
    if overtook:
        print(_not_asked_text("the same `ratchet.py ask choice ...` command", overtook))
        return
    lines = [f"ASK THE USER: give them options {', '.join(opts)} in your own words, with your recommendation, and ask "
             f"them to reply with just the letter."]
    for letter, paths in locked.items():
        lines.append(f"  In option {letter}, say plainly that choosing it approves changing {', '.join(paths)} "
                     f"(locked since they approved the plan), and what the change is. Their pick of {letter} is then "
                     "the approval: no second yes is needed.")
    for letter, kind in guards.items():
        lines.append(f"  Option {letter} adds an automatic check for '{kind}' as its own step right after the current "
                     "one (it may add a lint or architecture-test tool to the project). Say so in the option.")
    lines.append(f"After they reply, run `ratchet.py choice`. It records which option they picked.")
    print("\n".join(lines))


ACCEPT_FINDING_RE = re.compile(r"^accept:\s*(.+)$", re.I | re.S)


def _accept_reply_reason(text: str) -> str | None:
    """The reason in a reply of the exact form `accept: <reason>`, or None when the reply is not that form (or
    gives no reason)."""
    m = ACCEPT_FINDING_RE.match((text or "").strip())
    if not m:
        return None
    reason = m.group(1).strip()
    return reason or None


def _finding_key(f) -> str:
    """The identity shown and recorded for a finding: 'reviewer/id' (e.g. 'codex-correctness/A1') when it carries a
    reviewer (every finding cmd_adv_record records does, tagged there), else the bare id. A finding's own `id` field
    is only unique per reviewer, not per round: two independent reviewers routinely pick the same id (their shared
    prompt gives every one of them the identical example "id":"A1"), so the bare id alone is never enough on its own
    to say WHICH finding is meant."""
    fid = f.get("id") or "?"
    rev = f.get("reviewer")
    return f"{rev}/{fid}" if rev else fid


def _resolve_finding_ref(ref: str, findings: list):
    """The finding `ref` (a bare id, or 'reviewer/id') names among `findings`. Returns (finding, error): `finding`
    is the single match, or None when `ref` names none of them. `error` is set instead, only for a bare id that
    matches more than one reviewer's finding here (normal in any round with more than one reviewer: nothing stops
    two of them picking the same id) — it names the colliding reviewers so the agent can ask again precisely, as
    'reviewer/id'. A qualified ref ('reviewer/id') is never ambiguous: it is matched exactly or not at all."""
    if "/" in ref:
        rev, _, fid = ref.partition("/")
        for f in findings:
            if f.get("id") == fid and str(f.get("reviewer") or "") == rev:
                return f, None
        return None, None
    matches = [f for f in findings if f.get("id") == ref]
    reviewers = sorted({str(f.get("reviewer")) for f in matches})
    if len(reviewers) > 1:
        return None, (f"\"{ref}\" is ambiguous: {', '.join(reviewers)} each reported a finding with id \"{ref}\" "
                      f"here. Say which one, e.g. `{reviewers[0]}/{ref}`.")
    return (matches[0] if matches else None), None


def _why_not_open(f) -> str:
    """The real reason a non-open (deferred) finding doesn't block, for a refusal message (standards #11: name the
    actual reason, not a guess): its own `deferred_because` when cmd_adv_record recorded one (covered by an
    automatic check, or no user_impact stated), else 'deferred as minor' only when its severity really is minor."""
    why = str(f.get("deferred_because") or "").strip()
    if why:
        return why
    if str(f.get("severity", "")).strip().lower() in MINOR_SEVERITIES:
        return "deferred as minor, not blocking"
    return "deferred, not blocking"


def _ask_accept_finding(ctx, st, args):
    """`ask accept-finding CPn F1,F2` (or 'reviewer/F1' when a bare id is ambiguous): only when that checkpoint's
    latest adversarial round blocked, and only for ids that are still open (blocking) findings of that round. An id
    that never existed, or that was only ever a deferred/minor finding, is refused here; an id already accepted is
    left to `accept-finding` itself to refuse (so this can be asked again without first re-deriving which ids are
    already done). A bare id matching more than one reviewer's finding here is refused too, naming the colliding
    reviewers: say which one as 'reviewer/id'."""
    cp = args.mode
    ids = list(dict.fromkeys(i.strip() for i in (args.ids or "").split(",") if i.strip()))  # de-dupe repeated ids
    if not ids:
        die("ask accept-finding needs the finding ids to accept, e.g. `ratchet.py ask accept-finding CP2 F1,F2`")
    cps = (st.get("checkpoints") or {}).get(cp)
    if cps is None:
        die(f"No such checkpoint {cp}.")
    rnd = cps.get("adv_round")
    if rnd and _code_changed_since_round(ctx, rnd):
        die(f"{cp}: the code changed since this review round; run a new review round first. Nothing was asked.")
    open_findings = _adv_open_findings(ctx, cp, rnd, cps)  # state-filtered: a stale file can't un-accept an id
    if not open_findings:
        die(f"There are no open findings to accept for {cp} - the review already passed (or no round has blocked "
            "there yet). Nothing was asked.")
    deferred = _round_deferred(cps, rnd["round"])
    open_now = ', '.join(sorted(_finding_key(f) for f in open_findings)) or '(none)'
    resolved = []  # [(canonical key, finding shown in the question)], in ids' own order
    for ref in ids:
        f, err = _resolve_finding_ref(ref, open_findings)
        if err:
            die(f"{err} Nothing was asked.")
        if f is not None:
            resolved.append((_finding_key(f), f))
            continue
        d, derr = _resolve_finding_ref(ref, deferred)
        if derr:
            die(f"{derr} Nothing was asked.")
        if d is not None and d.get("accepted"):
            resolved.append((_finding_key(d), d))  # already accepted earlier: `accept-finding` refuses this clearly
            continue
        if d is not None:
            die(f"{ref} is not an open finding of this round for {cp}; it was already {_why_not_open(d)} "
                f"(open now: {open_now}). Nothing was asked.")
        die(f"{ref} is unknown: not an open finding of this round for {cp} (open now: {open_now}). Nothing was "
            "asked.")
    by_key = {}
    for key, f in resolved:
        by_key.setdefault(key, f)
    canon_ids = list(by_key)
    overtook = _overtake(ctx, st, "accept-finding", cp)
    asked = now()
    st["pending"] = {"action": "accept-finding", "mode": cp, "ids": canon_ids, "round": rnd["round"],
                     "fingerprint": rnd["fingerprint"], "asked_at": asked, "prev_awaiting": st.get("awaiting_since"),
                     "session": _asking_session(ctx, st, "accept-finding"), **overtook}
    st["awaiting_since"] = asked
    ctx.log(st, "question_asked", action="accept-finding", cp=cp, ids=canon_ids)
    ctx.save_state(st)
    if overtook:
        print(_not_asked_text(f"`ratchet.py ask accept-finding {cp} {','.join(canon_ids)}`", overtook))
        return
    named = "; ".join(f"{key} ({by_key[key].get('severity', '?')}): "
                      f"{str(by_key[key].get('description', '')).strip()[:160]}" for key in canon_ids)
    print(f"ASK THE USER: {cp}'s review is blocked on: {named}. Shall I accept {', '.join(canon_ids)} as a known "
          "limit, with their reason, instead of running another review round? To accept them, their reply must be "
          "exactly: accept: <their reason>. Only their first reply counts, and only that exact form (starting with "
          "\"accept: \") accepts them; any other reply accepts nothing.")
    print(ASK_ALONE)


def cmd_choice(args):
    """Record which lettered option the user picked, from their first reply after `ask choice`."""
    ctx = Ctx(find_root())
    st = ctx.state()
    _require_pending(st, "choice")
    p = st["pending"]
    if p.get("overtook"):
        _clear_pending(st)
        ctx.log(st, "choice_unclear", reply="(asked while another question was waiting)")
        ctx.save_state(st)
        die("These options were printed while another question was still waiting for the user's first reply, so their "
            "reply could be meant for either. Nothing was recorded. Run `ratchet.py ask choice ...` again on its own "
            "and wait for their pick.")
    msgs = _replies(ctx, st, p.get("asked_at"), p)  # only the chat the options were put to can pick one
    if not msgs:
        die("No reply from the user yet. End your turn and wait for them to pick an option.")
    entry = msgs[0]  # the first reply answers the question
    letter = choice_letter(entry.get("prompt", ""), p.get("options") or [])
    if ctx.config().get("verify_transcripts", True) and not human_override(
            "record the option the user picked", ctx, "choice (recorded without the transcript check)"):
        good, why = _transcript_ok(entry, p.get("asked_at"))
        if not good:
            die(f"Couldn't confirm that reply came from the user ({why}). Ask them to reply again in this chat.")
    if not letter:
        _clear_pending(st)
        ctx.log(st, "choice_unclear", reply=entry.get("prompt", "")[:200])
        ctx.save_state(st)
        die(f"The user's reply isn't a clear pick of one option: \"{entry.get('prompt', '')[:120]}\". Nothing was "
            "recorded. Answer what they said; if you still need a decision, ask again (`ratchet.py ask choice ...`).")
    files = (p.get("locked") or {}).get(letter) or []
    guard = (p.get("guardrail") or {}).get(letter)
    unblock = st["phase"] == "blocked"
    if files or unblock or guard:
        st["choice_allow"] = {"letter": letter, "files": files, "cp": st.get("current"), "at": now(),
                              "reply": entry.get("prompt", "")[:200], "unblock": unblock, "guardrail": guard}
    _clear_pending(st)
    ctx.log(st, "choice_made", letter=letter, approves=files)
    ctx.save_state(st)
    msg = f"The user picked option {letter}."
    if files:
        msg += (f" That approves changing {', '.join(files)}: make that change (through the sub-agent that owns it), "
                "then run `ratchet.py approve-changes`. No further question is needed.")
    if guard:
        msg += (f" That adds a guardrail step for '{guard}': run `ratchet.py guardrail --kind {guard} --title \"...\" "
                "--why \"...\" --check-command \"<check> {target}\" --bad-example <fixture path> "
                "--test-command \"...\" --test-file <path>`.")
    if unblock:
        msg += " The run is blocked: run `ratchet.py human-approve` to resume it, then act on their choice."
    ok(msg)


def _choice_covers(allow: dict, probs: list[str]) -> bool:
    files = allow.get("files") or []
    if not files:
        return False
    return bool(probs) and all(any(pr == f"reference file {f}" or pr.startswith(f"test file {f} ") for f in files)
                               for pr in probs)


def _require_pending(st, action, mode=None):
    p = st.get("pending") or {}
    if p.get("action") != action or (mode and p.get("mode") != mode):
        die(f"First run {_ask_cmd(action, mode)}, show the user the question it prints, "
            "and wait for their reply. Replies from before the question don't count.")


def _clear_pending(st):
    p = st.pop("pending", None) or {}
    if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review", "blocked", "done"):
        st["awaiting_since"] = now()
    else:
        st["awaiting_since"] = None


def cmd_cancel(args):
    """Abandon the active run — only after the user confirms. Commits already made are kept."""
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] in ("done", "cancelled"):
        ok(f"Run '{st['slug']}' is already {st['phase']}.")
        return
    _require_pending(st, "cancel")
    msg = _require_human(ctx, st, "cancel", getattr(args, "director_token", None))
    st.pop("pending", None)
    st["cancelled_from"] = st["phase"]
    st["phase"] = "cancelled"
    st["current"] = None
    st["blocked_reason"] = args.reason or "cancelled by the user"
    ctx.log(st, "cancelled", by=msg["by"], note=msg.get("prompt", "")[:200])
    ctx.save_state(st)
    register_active(ctx.root, None)
    stopped = _stop_server(ctx.ratchet / "viewer.json")
    commits = [c.get("commit") for c in st["checkpoints"].values() if c.get("commit")]
    ok(f"Run '{st['slug']}' cancelled." + (" Viewer server stopped." if stopped else "")
       + (f" Checkpoint commits kept: {', '.join(commits)} — ask the user whether to keep or revert them."
          if commits else ""))


def cmd_set_review(args):
    """Change how often the user reviews (end | each | none) — the user's call, so it needs their confirmation."""
    ctx = Ctx(find_root())
    st = ctx.state()
    _require_pending(st, "set-review", args.mode)
    msg = _require_human(ctx, st, "set-review", getattr(args, "director_token", None))
    old = st.get("human_review")
    st["human_review"] = args.mode
    if st.get("approved"):
        st["approved"]["human_review"] = args.mode
    _clear_pending(st)
    ctx.log(st, "review_mode_changed", by=msg["by"], old=old, new=args.mode)
    ctx.save_state(st)
    ok(f"Review mode {old} → {args.mode}.")


def cmd_approve_changes(args):
    """The user confirmed a change to the plan, test plans or rules after approval: accept it as the new baseline."""
    ctx = Ctx(find_root())
    st = ctx.state()
    probs = integrity_problems(ctx, st)
    if not probs:
        ok("Nothing has changed since approval.")
        return
    allow = st.get("choice_allow") or {}
    if allow and allow.get("cp") == st.get("current") and not getattr(args, "director_token", None) \
            and _choice_covers(allow, probs):
        msg = {"by": "human", "prompt": f"picked option {allow['letter']}: {allow.get('reply', '')}"}
        allow["files"] = []
        if not allow.get("unblock") and not allow.get("guardrail"):
            st.pop("choice_allow", None)
    else:
        if allow.get("files") and allow.get("cp") == st.get("current") \
                and (st.get("pending") or {}).get("action") != "approve-changes":
            extra = [pr for pr in probs if not _choice_covers(allow, [pr])]
            die(f"The user's pick of option {allow['letter']} approved changing {', '.join(allow['files'])}, but these "
                f"changed too: {'; '.join(extra)}. Undo them, or show the user exactly what changed and ask "
                "(`ratchet.py ask approve-changes`).")
        _require_pending(st, "approve-changes")
        msg = _require_human(ctx, st, "approve-changes", getattr(args, "director_token", None))
    st["approved"] = approval_snapshot(ctx, st)
    cur = st.get("current")
    if cur and (st["checkpoints"].get(cur) or {}).get("red_tests") is not None:
        st["checkpoints"][cur]["red_tests"] = _snapshot_tests(ctx, cp_by_id(ctx.plan(), cur))
    _clear_pending(st)
    ctx.log(st, "changes_approved", by=msg["by"], changed="; ".join(probs)[:400])
    ctx.save_state(st)
    build_viewer(ctx)
    ok("Accepted as the new baseline: " + "; ".join(probs))


def hooks_view(ctx, st=None) -> dict:
    """hooks_status plus `waived`: the user chose to build without hooks and nothing enforces the gates yet."""
    hs = hooks_status(ctx.root, ctx.config())
    st = st or ctx.state()
    if _waiver_applies(ctx.root, ctx.config(), st):
        hs["waived"] = True
    return hs


def _enforcement_beats(root: Path, cfg) -> list:
    """Timestamps of guard/stop heartbeats from this host. The prompt hook is only the consent channel."""
    host = VENDOR_TO_HOST.get(detect_host(cfg or {}), "unknown")
    hb = read_heartbeat(root)
    out = []
    for ev in ("guard", "stop"):
        e = hb.get(ev)
        if isinstance(e, dict) and (host == "unknown" or e.get("host") == host):
            t = _parse_iso(e.get("at"))
            if t and t <= _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=5):
                out.append(t)
    return out


def _enforcement_active(root: Path, cfg) -> bool:
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)
    return bool(hooks_status(root, cfg)["installed"]) or any(t > cutoff for t in _enforcement_beats(root, cfg))


def _waiver_applies(root: Path, cfg, st) -> bool:
    """A recorded waiver counts only while the enforcement hooks (guard/stop) are neither installed nor seen firing
    since it was given, so it cannot resurface after an uninstall without new consent."""
    w = st.get("hooks_waived")
    if not w or _enforcement_active(root, cfg):
        return False
    since = _parse_iso(w.get("at")) if isinstance(w, dict) else None
    return not (since and any(t > since for t in _enforcement_beats(root, cfg)))


def cmd_hooks_waive(args):
    """Record that the USER chose to build without hooks (after `ask hooks-waive` and their clear yes)."""
    ctx = Ctx(find_root())
    st = ctx.state()
    token = getattr(args, "director_token", None)
    if not token:
        _require_pending(st, "hooks-waive")
    if _enforcement_active(ctx.root, ctx.config()):
        st.pop("pending", None)
        ctx.save_state(st)
        ok("Ratchet hooks are already active here, so there is nothing to waive; nothing was recorded.")
        return
    # only a reply typed after the hooks question counts, and it is spent on this question alone
    view = dict(st, awaiting_since=st["pending"]["asked_at"]) if not token else st
    msg = _require_human(ctx, view, "hooks-waive", token)
    already = bool(st.get("hooks_waived"))
    if token:
        st["hooks_waive_n"] = int(st.get("hooks_waive_n", 0)) + 1  # spend the token
    if not already:
        st["hooks_waived"] = {"at": now(), "by": msg["by"], "prompt": msg.get("prompt", "")[:200]}
        ctx.log(st, "hooks_waived", by=msg["by"], note=msg.get("prompt", "")[:200])
    _clear_pending(st)
    ctx.save_state(st)
    build_viewer(ctx)
    ok("Already recorded: the user chose to build without hooks." if already else
       "Recorded: the user chose to build without hooks. The checks are advice only; missing hooks never block work.")


def _adv_round_block_reason_for(cp, reason) -> bool:
    """True when `reason` is the kind of block that accepting `cp`'s open findings resolves: either `cp`'s own
    adversarial round limit (`cmd_adv_record`'s 'still has N finding(s) after R rounds', or `cmd_reviewers`'s 'R
    adversarial rounds used without approval'), or the run-wide round cap (`cmd_reviewers`), which also blocks
    before a new round opens while `cp`'s last recorded round still has open findings of its own. Any other
    block (behavior, UI, a different checkpoint's reason) is left alone: accepting a finding here does not fix
    it, so the run should stay exactly as blocked as it was."""
    r = reason or ""
    if r.startswith(f"{cp}:") and "adversarial" in r and "rounds" in r:
        return True
    return r.startswith("Run-wide adversarial round cap reached")


def _code_changed_since_round(ctx, rnd) -> bool:
    """True when the code has changed since this adversarial round's own fingerprint was captured, excluding
    `.ratchet/` and any chat transcript recorded since the round opened (the same exclusions `complete` applies
    to its own gates): `ask accept-finding` and `accept-finding` must refuse once this is true, rather than act
    on a round that no longer matches the code (the round's own `fingerprint`/`round` identity check only catches
    a NEW round having opened since the question was asked; it says nothing about code edited while the stale
    round just sits there, still the current one, still blocked)."""
    fp = fingerprint(ctx.root)
    if fp["id"] == rnd["fingerprint"]:
        return False
    chat_paths = _chat_transcript_paths(ctx, rnd.get("opened_at"))
    changed = [f for f in changed_files(ctx.root, {"kind": fp.get("kind", "git-tree"), "id": rnd["fingerprint"]}, fp)
               if f != RATCHET_DIR and not f.startswith(RATCHET_DIR + "/") and f not in chat_paths]
    return bool(changed)


def _unblock(ctx, st, by, note) -> str:
    """Resolves a `blocked` run back to the phase it was blocked from (building, or the review phase it reaches
    when nothing is left to build), exactly as `human-approve`'s own unblock does: resets the run-wide
    adversarial round budget when that cap was the cause ("the user chose to continue: the run's budget starts
    again"), resets the current checkpoint's own failing-gate attempt counters, picks up the next unfinished
    checkpoint when none is current, and consumes any stored `choice_allow['unblock']` so a stale pick from an
    earlier block can never unblock a LATER one without a fresh question. The one place both `human-approve` and
    `accept-finding` resolve a block, so a fix to how unblocking works is made once, not twice. Returns the phase
    the run is in afterward."""
    allow = st.get("choice_allow") or {}
    if allow.get("unblock"):
        allow["unblock"] = False
        if not allow.get("files") and not allow.get("guardrail"):
            st.pop("choice_allow", None)
    order = [c["id"] for c in (ctx.plan() or {"checkpoints": []})["checkpoints"]]
    back = st.pop("blocked_from", None) or "building"
    if back == "blocked":
        back = "building"
    st["phase"] = back
    st["blocked_reason"] = None
    cur = st.get("current")
    cfg = ctx.config()
    if int(st.get("adv_rounds_total", 0)) >= cfg["max_adversarial_rounds_run"]:
        st["adv_rounds_total"] = 0  # the user chose to continue: the run's budget starts again
    if back == "building" and not cur:  # nothing current: pick up the next unfinished checkpoint
        pending = [c for c in order if st["checkpoints"][c]["status"] != "done"]
        if pending:
            _advance(st, pending[0], fingerprint(ctx.root)["id"])
        else:
            st["phase"] = "awaiting_final_review"
    elif cur:
        # resets the attempt counters on the current checkpoint's failing gates
        for g in st["checkpoints"][cur]["gates"].values():
            if g["status"] != "passed":
                g["attempts"] = 0
        st["checkpoints"][cur]["guardrail_tries"] = 0
    ctx.log(st, "unblocked", by=by, note=note, back_to=st["phase"])
    return st["phase"]


def cmd_accept_finding(args):
    """Record the open findings the user accepted: only their FIRST reply after `ask accept-finding`, from the
    owning chat, in the exact form `accept: <reason>`, accepts them (reuses the same consent machinery and rules
    as every other approval: _require_pending, _replies, the transcript check). In a director-enrolled lane a chat
    reply never counts (same rule as every other approval here): a director token signed for this action is
    required instead, with --reason carrying the director's own reason since there is no chat reply to read one
    from. When every open finding of that round is then accepted, the adversarial gate passes for that round's
    fingerprint without another round; a later code change (a new fingerprint) brings back the normal review
    rules. When no open finding remains, an open check-in for this step (reviews dragging) is also cleared: it had
    nothing left to pause on."""
    ctx = Ctx(find_root())
    st = ctx.state()
    _require_pending(st, "accept-finding", args.cp)
    p = st["pending"]
    cp = p["mode"]
    ids = p.get("ids") or []
    if p.get("overtook"):
        _clear_pending(st)
        ctx.log(st, "accept_finding_unclear", cp=cp, ids=ids)
        ctx.save_state(st)
        die(f"This question was printed while another one (shall I {_question_what(p['overtook'], p.get('overtook_mode'))}"
            "?) was still waiting for the user's first reply, so their reply could be meant for either. Nothing was "
            f"accepted. Run `ratchet.py ask accept-finding {cp} {','.join(ids)}` again on its own.")
    token = getattr(args, "director_token", None)
    if token or approval_mode(ctx) == "director":
        # --reason is checked BEFORE the token is verified or anything is cleared (standards #11): a token is not
        # a one-time secret by itself here (unlike a chat reply), but _require_approval's own verification is
        # bound to st['awaiting_since'], which _clear_pending resets once the question is answered, so clearing
        # pending over a merely-forgotten --reason would make the director's own still-valid token stale, costing
        # a second signature for one missing flag. Dying here first, with the question still pending and nothing
        # saved, means the SAME command with --reason added simply works.
        reason = (getattr(args, "reason", None) or "").strip()
        if not reason:
            die(f"accept-finding needs --reason \"...\" when a director token approves it (there is no chat reply "
                f"to read a reason from). Nothing was accepted, and the question is still waiting: run the same "
                f"command again with --reason \"...\" added.")
        # Reuses _require_approval's own director check and token verification (standards #2/#19: don't re-implement
        # the same gate): dies here, exactly as every other approval does, when this lane is director-enrolled and
        # no (or a bad) director token was given; a chat "accept: ..." reply is never enough in that lane.
        appr = _require_approval(ctx, st, "accept-finding", token)
        by = appr["by"]
    else:
        msgs = _replies(ctx, st, p.get("asked_at"), p)  # only the chat the question was asked in can answer it
        if not msgs:
            die("No reply has been recorded in the owning chat since this question was asked. End your turn and wait "
                "for the user's reply, exactly `accept: <reason>`; a reply typed in another chat does not count.")
        entry = msgs[0]  # the first reply answers the question; later messages never do
        reply = entry.get("prompt", "")
        reason = _accept_reply_reason(reply)
        if reason is None:
            _spend_question(ctx, st, reply)
            die(f"The user's first reply after the question was not the exact form `accept: <reason>`: \"{reply[:120]}\". "
                "Nothing was accepted, and the question is closed, so a later reply in that form does not count. Run "
                f"`ratchet.py ask accept-finding {cp} {','.join(ids)}` again if they still want to accept them.")
        if ctx.config().get("verify_transcripts", True):
            good, why = _transcript_ok(entry, p.get("asked_at"))
            if not good:
                _spend_question(ctx, st, reply)
                die(f"Couldn't confirm that reply came from the user ({why}). Nothing was accepted. Run `ratchet.py ask "
                    f"accept-finding {cp} {','.join(ids)}` again and ask them to reply in this chat.")
        by = "human"
    cps = (st.get("checkpoints") or {}).get(cp)
    if cps is None:
        _clear_pending(st)
        ctx.save_state(st)
        die(f"No such checkpoint {cp} any more. Nothing was accepted.")
    rnd = cps.get("adv_round")
    if not rnd or rnd.get("round") != p.get("round") or rnd.get("fingerprint") != p.get("fingerprint"):
        # the round itself moved on (a new one was opened, e.g. after a real code change): `reviewers` already
        # re-checks the fingerprint when that happens, so this is never fooled by anything that only touches the
        # chat transcript (a reply does not change the code, so it never opens a new round on its own)
        _clear_pending(st)
        ctx.save_state(st)
        die(f"The review round for {cp} has moved on since this question was asked (a new round opened, most "
            "likely because the code changed). Nothing was accepted. Run "
            f"`ratchet.py ask accept-finding {cp} ...` again against the current round.")
    if _code_changed_since_round(ctx, rnd):
        _clear_pending(st)
        ctx.save_state(st)
        die(f"{cp}: the code changed since this review round; run a new review round first. Nothing was accepted.")
    g = cps["gates"]["adversarial"]
    # State-filtered (not the raw file): a crash between the state commit and the evidence-file writes of an
    # earlier accept-finding call can leave open-findings.json stale, still listing an id state already marked
    # accepted. Checking the state-derived list (and deferred_by_key) rather than the raw file means a retry of
    # that same id is always caught as already-accepted here, never re-recorded as a second acceptance.
    open_findings = _adv_open_findings(ctx, cp, rnd, cps)
    open_by_key = {_finding_key(f): f for f in open_findings}
    deferred_by_key = {_finding_key(f): f for f in _round_deferred(cps, rnd["round"])}
    already = [i for i in ids if (deferred_by_key.get(i) or {}).get("accepted")]
    if already:
        _clear_pending(st)
        ctx.save_state(st)
        die(f"{', '.join(already)} already closed/accepted earlier for {cp}; nothing new to do. Nothing was changed.")
    missing = [i for i in ids if i not in open_by_key]
    if missing:
        _clear_pending(st)
        ctx.save_state(st)
        die(f"{', '.join(missing)} is not an open finding of {cp}'s current round. Nothing was accepted.")
    at = now()
    accepted_entries = [dict(open_by_key[i], accepted=True, reason=reason, accepted_at=at, accepted_by=by,
                            round=rnd["round"]) for i in ids]
    remaining_open = [f for f in open_findings if _finding_key(f) not in ids]
    dest = _adv_round_dir(ctx, cp, rnd["round"])

    # Simplified write order (no "heal from state" machinery): everything below the state commit (c) is written
    # BEFORE it, so a crash before (c) leaves AT MOST a harmless extra learnings.md note or an un-committed
    # deferred-findings.json write -- the accept itself never happened (state still shows every id open), so a
    # plain retry (the question is still pending) starts clean and finishes once. A crash can never leave a
    # COMMITTED accept without its own notes, because both are written first.
    # (a) .ratchet/learnings.md: one line per accepted entry, keyed by a marker unique to this run, checkpoint,
    # round AND reviewer/id (never just reviewer/id, which repeats across rounds and checkpoints) so a retry
    # after an earlier crash that already wrote the line never duplicates it, and two DIFFERENT findings that
    # happen to share a reviewer/id (a later round, or a later checkpoint) both get their own line.
    learnings = _learnings_text(ctx)
    for i in ids:
        f = open_by_key[i]
        marker = f"Accepted finding {st['slug']}/{cp}/round-{rnd['round']}/{_finding_key(f)} ("
        if marker in learnings:
            continue
        # One learnings.md bullet is one line: a multi-line description or reason (the reason especially, since
        # ACCEPT_FINDING_RE accepts one spanning several lines) is collapsed to a single line here, in the
        # learnings text only; the reason kept verbatim everywhere else (accepted_entries, round_rec, state) is
        # untouched.
        desc_line = " ".join(str(f.get("description", "")).split())[:200]
        reason_line = " ".join(reason.split())
        _append_learning(ctx, f"{marker}{cp}, {f.get('severity', '?')}): "
                               f"{desc_line}. Reason: {reason_line}",
                          f"{by}, accept-finding")
        learnings += marker  # a second id accepted in this same call never duplicates its own line either
    # (b) the round's own deferred-findings.json, written WHOLE for this round (never appended) from the list
    # state is about to commit.
    new_deferred = (cps.get("deferred") or []) + accepted_entries
    round_deferred = [f for f in new_deferred if f.get("round") == rnd["round"]]
    dest.mkdir(parents=True, exist_ok=True)
    write_json(dest / "deferred-findings.json", round_deferred)
    cps["deferred"] = new_deferred
    rounds = g.get("rounds") or []
    # Found by the round number stamped on the entry, not by list position: a round `reviewers` opens and then
    # replaces before recording (the fingerprint changed) advances rnd['round'] without adding an entry, so
    # position alone can name the wrong entry, or none. A round recorded before that stamp existed falls back to
    # position for that one entry only.
    round_rec = next((rr for rr in rounds if rr.get("round") == rnd["round"]), None)
    if round_rec is None and rounds and 0 < rnd["round"] <= len(rounds):
        round_rec = rounds[rnd["round"] - 1]
    if round_rec is not None:
        round_rec.setdefault("accepted", []).extend(
            [{"id": i, "reason": reason, "at": at, "by": by} for i in ids])
    ctx.log(st, "finding_accepted", cp=cp, ids=ids, reason=reason[:200], by=by)
    unblocked_to = None
    if not remaining_open:
        g["status"] = "passed"
        # stamped with the round's OWN fingerprint (what was actually reviewed), not a fresh one: a reply itself
        # changes nothing in the code, but recomputing now would still pick up whatever it touched meanwhile
        # (e.g. the chat transcript, if the agent CLI keeps it inside the project).
        g["fingerprint"] = {"kind": fingerprint(ctx.root).get("kind", "git-tree"), "id": rnd["fingerprint"]}
        # the round's OWN opened_at, not now(): that is when this fingerprint was actually captured, so `complete`
        # correctly looks for chat replies (never the code) from then on, the ask/reply this acceptance needed
        # included, when it decides whether anything that matters has changed since.
        g["passed_at"] = rnd.get("opened_at") or at
        cps["step"] = _earliest_open_step(cps)
        # The user's accept IS their decision on the block when the run is stuck here only because of this
        # checkpoint's own adversarial round limit OR the run-wide round cap (never a lettered option: the only
        # way here is the plain accept_note line `next`/`status` print, pointing at `ask accept-finding` plus the
        # user's own `accept: <reason>` reply): the gate just passed, so there is nothing left to be stuck on.
        # Leaving phase at "blocked" would make `complete` refuse ("Not building") and `status`/`next` keep
        # showing a block whose cause, the open findings, is already resolved. Any OTHER block reason (behavior,
        # UI, a different checkpoint) is left exactly as it was: accepting a finding here does not resolve it.
        # `_unblock` is the same helper `human-approve` uses to resolve a block: it resets the run-wide round
        # budget when that cap was the cause, resets this checkpoint's own gate attempt counters, and consumes
        # any stored choice_allow so a later, unrelated block can't be unblocked by this same stale pick.
        if st.get("phase") == "blocked" and st.get("current") == cp \
                and _adv_round_block_reason_for(cp, st.get("blocked_reason")):
            unblocked_to = _unblock(ctx, st, by, "accept-finding resolved the round limit")
        # No open blocking finding remains for this step, so any check-in paused on this gate dragging has nothing
        # left to pause on: clear it the same as `reviewers` requires it resolved before another round, otherwise
        # it would sit open forever (check-in only ever clears itself through `check-in --decision simplify/stop`,
        # never through acceptance) and `status`/`next` would keep reporting one that is already moot.
        ci = _checkin_state(cps)
        if ci.get("open"):
            ctx.log(st, "checkin_resolved_by_accept", cp=cp, round=ci["open"]["round"], ids=ids)
            ci["open"] = None
    _clear_pending(st)
    # (c) commit state: the one atomic, authoritative write (standards #12) that says these findings are accepted.
    ctx.save_state(st)

    build_viewer(ctx)
    # (d) open-findings.json last: the file that says these findings still block, now rewritten to drop them.
    # Readers never need this file alone to know what is open: `_adv_open_findings` always computes it as the
    # file's own contents minus whatever state already records accepted for that round, so a stale copy left by a
    # crash between (c) and (d) is never mistaken for a reopened finding.
    write_json(dest / "open-findings.json", remaining_open)

    msg = f"Accepted {', '.join(ids)} for {cp} with reason: {reason}."
    if remaining_open:
        left = sorted(_finding_key(f) for f in remaining_open)
        msg += f" Still open: {', '.join(left)} - {cp} remains blocked until those are resolved or accepted too."
    else:
        msg += f" No open findings remain for {cp}: the adversarial gate is now passed without another round."
        if unblocked_to:
            msg += " The run is no longer blocked."
        if st.get("phase") == "building" and st.get("current") == cp and cps.get("step") == "complete":
            msg += f" Next: ratchet.py complete {cp}"
        elif st.get("phase") == "blocked":
            msg += (f" {cp}'s review now passes, but the run is still blocked "
                    f"({plain_reason(st.get('blocked_reason')) or st.get('blocked_reason')}); resolve that first "
                    "(see `ratchet.py next`).")
        else:
            msg += " Run `ratchet.py next` to see what happens now."
    ok(msg)


def cmd_next(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    na = next_action(ctx, st)
    na["wait"] = live_wait(st)  # what the owner chat said it is waiting on (`ratchet.py wait`), until when
    hs = hooks_view(ctx)
    hw = hooks_warning(ctx, hs)
    if hw and na.get("phase") not in ("planning",):
        na["warning"] = hw
    na["hooks"] = hs
    na["hook_errors"] = hook_errors_view(ctx.root, hs.get("host", "unknown"))
    note = exit_code_only_note(ctx.plan(), ctx.state())
    if note:
        na["test_check"] = note
    print(json.dumps(na, indent=2))


def cmd_status(args):
    ctx = Ctx(find_root())
    if getattr(args, "json", False):
        print(json.dumps(status_json(ctx), indent=2))
        return
    cmd_status_text(ctx)


def cmd_reviewers(args):
    """Open (or show) the adversarial review round for a checkpoint. Opening a round is what counts toward the
    retry caps, so re-rolling reviewers until one approves still costs rounds."""
    ctx = Ctx(find_root())
    cfg = ctx.config()
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["adversarial", "complete"], args.cp)
    plan = ctx.plan()
    cp = cp_by_id(plan, args.cp)
    obj = plan.get("objective") if isinstance(plan, dict) else None
    obj_oneline = re.sub(r"\s*\n\s*", " ", objective_brief(plan))
    fp = fingerprint(ctx.root)["id"]
    g = cps["gates"]["adversarial"]
    rnd = cps.get("adv_round")
    if not rnd or rnd.get("recorded") or rnd["fingerprint"] != fp:
        ci = _checkin_state(cps)
        if ci.get("open"):
            die(f"{args.cp}: a check-in is open from round {ci['open']['round']} (reviews have been dragging). "
                f"Run `ratchet.py check-in {args.cp}` to see it, then `ratchet.py check-in {args.cp} --decision "
                "simplify --why \"...\"` or `--decision stop` to resolve it before another round opens.")
        if rnd and rnd.get("recorded"):
            open_findings = _adv_open_findings(ctx, args.cp, rnd, cps)
            if open_findings:  # every remaining blocking finding needs its own fix-or-rebut decision, even one
                triage = _read_triage(_adv_round_dir(ctx, args.cp, rnd["round"]))
                missing = sorted({_finding_key(f) for f in open_findings} - set(triage))
                if missing:
                    die(f"{args.cp} round {rnd['round']} still has blocking finding(s) with no fix-or-rebut "
                        f"decision: {', '.join(missing)}. Run `ratchet.py triage {args.cp} "
                        f"{missing[0]}=fix:\"reason\"` (or `=rebut:\"reason\"`) for each, then try again.")
        if g["attempts"] >= cfg["max_adversarial_rounds"]:
            _set_blocked(st, f"{args.cp}: {g['attempts']} adversarial rounds used without approval — needs a "
                             "human/director decision.")
            ctx.save_state(st)
            die(st["blocked_reason"], 2)
        if int(st.get("adv_rounds_total", 0)) >= cfg["max_adversarial_rounds_run"]:
            _set_blocked(st, f"Run-wide adversarial round cap reached ({st.get('adv_rounds_total')}/"
                             f"{cfg['max_adversarial_rounds_run']}). Raising it is the user's call "
                             "(config change + approve-changes).")
            ctx.save_state(st)
            die(st["blocked_reason"], 2)
        resolved = resolve_reviewers(ctx, cfg, cp)
        for _ in range(4):  # test the providers we are about to rely on; a failing one hands its slot on
            due = {(_roster_spec(cfg, r["id"]) or {}).get("provider") for r in resolved}
            due = [pid for pid in due if pid and probe_due(ctx, cfg, pid)]
            if not due:
                break
            for pid in due:
                good, detail, _saved = probe_provider(ctx, cfg, provider_by_id(cfg, pid))
                print(f"  checked {pid}: {'working' if good else 'not usable now (' + detail + ')'}", file=sys.stderr)
            resolved = resolve_reviewers(ctx, cfg, cp)
        g["attempts"] += 1
        st["adv_rounds_total"] = int(st.get("adv_rounds_total", 0)) + 1
        prev = rnd["round"] if rnd else 0
        old_key = review_key_file(ctx.root, st, args.cp, "adv", prev) if prev else None
        rnd = {"round": prev + 1, "nonce": secrets.token_hex(8), "fingerprint": fp, "opened_at": now(),
               "reviewers": [r["id"] for r in resolved],
               "substitutions": [{"used": r["id"], "instead_of": r["fallback_for"], "why": r["reasons"]}
                                 for r in resolved if r["fallback_for"]],
               "resolved": resolved}
        cps["adv_round"] = rnd
        ctx.log(st, "reviewers_launched", cp=args.cp, round=rnd["round"], reviewers=",".join(rnd["reviewers"]))
        if rnd["substitutions"]:
            ctx.log(st, "reviewer_fallback", cp=args.cp, detail="; ".join(
                f"{x['used']} instead of {', '.join(x['instead_of'])}" for x in rnd["substitutions"]))
        ctx.save_state(st)
        drop_review_key(old_key)  # the replaced round (now saved as replaced) stops counting
    else:
        # same round: if a listed reviewer has become unavailable (its CLI failed), give its slot to the next choice
        fresh = {r["slot"]: r for r in resolve_reviewers(ctx, cfg, cp)}
        changed = False
        for i, r in enumerate(rnd["resolved"]):
            okay, _why = reviewer_availability(ctx, cfg, r["id"])
            if not okay and r["slot"] in fresh and fresh[r["slot"]]["id"] != r["id"]:
                rnd["resolved"][i] = fresh[r["slot"]]
                changed = True
        if changed:
            rnd["reviewers"] = [r["id"] for r in rnd["resolved"]]
            ctx.log(st, "reviewer_fallback", cp=args.cp, detail="re-resolved within round " + str(rnd["round"]))
            ctx.save_state(st)
    _key, why = open_review_key(ctx, args.cp, "adv", rnd["round"], st)
    if _key is None:
        die(review_key_problem(why, f"run `ratchet.py reviewers {args.cp}` again"))
    out_dir = _review_round_dir(ctx, args.cp, "adv", rnd["round"])
    out_dir.mkdir(parents=True, exist_ok=True)
    fixes = ctx.run_dir() / "reviews" / args.cp / f"adv-round-{rnd['round'] - 1:02d}" / "fixes.md"
    fixes_arg = f" --fixes {fixes}" if rnd["round"] > 1 else ""
    ext_script = SKILL_DIR.parent / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
    focus, rules = review_rules(ctx, cfg, cp, g, rnd, fp)
    rows = []
    for res_r in rnd["resolved"]:
        rid = res_r["id"]
        spec = dict(_roster_spec(cfg, rid) or {}, id=rid, output=str(out_dir / f"{rid}.json"),
                    slot=res_r["slot"], fallback_for=res_r["fallback_for"], reasons=res_r["reasons"])
        if spec["kind"] == "agent":
            want = ("a model DIFFERENT from yours: " + " or ".join(host_model_map(cfg).get("alternates") or [])
                    if spec.get("alt") else role_model(cfg, "ratchet-adversary"))
            spec["how"] = (f"Objective: {obj_oneline} | Spawn a ratchet-adversary sub-agent (model: {want}): "
                           f"lens={spec['lens']}, reviewer id '{rid}', "
                           f"fingerprint {fp}, round_nonce {rnd['nonce']}, checkpoint {args.cp}, output {spec['output']}"
                           + (f", fixes.md {fixes}" if rnd["round"] > 1 else "")
                           + ("" if not rules else ". Review rules for this round (tell it): " + " ".join(rules)))
        else:
            spec["how"] = f"python3 {ext_script} --reviewer {rid} --cp {args.cp} --out {spec['output']}{fixes_arg}"
        rows.append(spec)
    res = {"checkpoint": args.cp, "objective": obj if isinstance(obj, dict) and obj else NO_OBJECTIVE_NOTE,
           "risk": cp.get("risk", "normal"), "round": rnd["round"], "fingerprint": fp,
           "review_rules": rules, "focus_files": sorted(focus) if focus is not None else None,
           "round_nonce": rnd["nonce"], "output_dir": str(out_dir),
           "fixes_md": str(fixes) if rnd["round"] > 1 else None,
           "run_rounds_used": st.get("adv_rounds_total", 0), "run_rounds_cap": cfg["max_adversarial_rounds_run"],
           "reviewers": rows}
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"{args.cp} risk={res['risk']} round={rnd['round']} fingerprint={fp[:12]} nonce={rnd['nonce']} "
          f"(run rounds {res['run_rounds_used']}/{res['run_rounds_cap']})")
    if isinstance(obj, dict) and obj:
        print(f"Who it's for: {obj.get('users', '')}")
        print(f"What they want: {obj.get('goal', '')}")
    else:
        print(f"Note: {NO_OBJECTIVE_NOTE}.")
    for r in rows:
        print(f"  - {r['id']:<22} [{r['vendor']}/{r['lens']}] {r['how']}")
        for skipped in r["fallback_for"]:
            print(f"      (instead of {skipped}: {r['reasons'][skipped]})")
    for rule in rules:
        print(f"  rule: {rule}")
    print(f"Reviews must be written in this round's folder: {out_dir} (adv-record refuses a file from anywhere else)")
    print(f"Then: ratchet.py adv-record {args.cp} --reviews {out_dir}/*.json")


def cmd_reviewer_status(args):
    ctx = Ctx(find_root())
    cfg = ctx.config()
    if args.reset:
        p = _unavailable_path(ctx)
        if _update_unavailable(ctx, lambda live: live.pop(args.reset, None), note=False) == "busy":
            _die_record_busy(p, f"{args.reset} was not reset")
        ok(f"{args.reset} will be tried again")
    if args.mark:
        until = mark_unavailable(ctx, args.mark, args.reason or "marked unavailable", note=False)
        if until is None:
            p = _unavailable_path(ctx)
            _die_record_busy(p, f"{args.mark} was not marked unavailable")
        ok(f"{args.mark} skipped until {until[:16]}Z; the next provider, or the host's sub-agents, fill its slot")
    print(f"Host provider: {detect_host(cfg)}")
    for rid, spec in roster(cfg).items():
        okay, why = reviewer_availability(ctx, cfg, rid)
        print(f"  {'✓' if okay else '·'} {rid:<22} {str(spec.get('vendor') or '?'):<10} {why}")


def status_json(ctx: Ctx) -> dict:
    st = ctx.state()
    plan = ctx.plan() or {"checkpoints": []}
    cps = []
    for cp in plan["checkpoints"]:
        s = st["checkpoints"].get(cp["id"]) or {}
        cps.append({"id": cp["id"], "title": cp["title"], "risk": cp.get("risk", "normal"),
                    "status": s.get("status", "unsubmitted"), "step": s.get("step"), "commit": s.get("commit"),
                    "gates": {n: {k: g.get(k) for k in ("status", "attempts", "by", "unconfirmed") if g.get(k) is not None}
                              for n, g in (s.get("gates") or {}).items()}})
    na = next_action(ctx, st)
    hs = hooks_view(ctx, st)
    hw = hooks_warning(ctx, hs)
    waiting_on = None
    if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review", "blocked"):
        waiting_on = "director" if approval_mode(ctx) == "director" else "human"
    obj = plan.get("objective") if isinstance(plan, dict) else None
    return {"root": str(ctx.root), "slug": st["slug"], "feature": st["feature"], "phase": st["phase"],
            "current": st.get("current"), "step": na.get("step"), "next": na["action"], "stop_ok": na["stop_ok"],
            "objective": obj if isinstance(obj, dict) and obj else NO_OBJECTIVE_NOTE,
            "waiting_on": waiting_on, "blocked_reason": st.get("blocked_reason"),
            "blocked_reason_plain": plain_reason(st.get("blocked_reason")),
            "accept_note": na.get("accept_note"),
            "awaiting_since": st.get("awaiting_since"), "approval_mode": approval_mode(ctx),
            "wait": live_wait(st),
            "adv_rounds_used": st.get("adv_rounds_total", 0),
            "unavailable_reviewers": unavailable_reviewers(ctx), "host_provider": detect_host(ctx.config()),
            "hooks_warning": hw, "hook_heartbeat": read_heartbeat(ctx.root),
            "hooks": hs, "hook_errors": hook_errors_view(ctx.root, hs.get("host", "unknown")),
            "viewer_url": viewer_link(ctx)["url"], "viewer_live": viewer_link(ctx)["live"],
            "done": sum(1 for c in cps if c["status"] == "done"), "total": len(cps),
            "checkpoints": cps, "updated_at": st.get("updated_at")}


def cmd_lanes(args):
    base = Path(args.scan).resolve()
    state_file = base / ".ratchet-lanes.json"
    if args.stop:
        ok("Lanes dashboard stopped" if _stop_server(state_file) else "No lanes dashboard was running")
        return
    if args.serve and args.foreground:
        token = os.environ.get("RATCHET_VIEWER_TOKEN") or None
        srv = _http_server(args.bind, args.port, _make_handler(_dashboard_routes(base), token))
        srv.serve_forever()
        return
    if args.serve:
        info, started = _start_server("lanes", base, state_file, args.bind, args.port, args.token or "auto",
                                      args.public_host, extra=("--scan", str(base)))
        print(f"Lanes dashboard {'started' if started else 'already running'}: {info['url']}")
        if args.open:
            webbrowser.open(info["url"])
        return
    out = []
    for helper in sorted(base.rglob(f"{RATCHET_DIR}/active.json")):
        root = helper.parent.parent
        if any(part in FINGERPRINT_EXCLUDES - {RATCHET_DIR} for part in root.relative_to(base).parts):
            continue
        try:
            out.append(status_json(Ctx(root)))
        except SystemExit:
            out.append({"root": str(root), "error": "unreadable state"})
    if args.json:
        print(json.dumps(out, indent=2))
        return
    for l in out:
        if "error" in l:
            print(f"{l['root']}: {l['error']}")
            continue
        flag = f"  ⚑ waiting on {l['waiting_on']}" if l["waiting_on"] else ""
        print(f"{l['slug']:<28} {l['phase']:<26} {l['done']}/{l['total']}  {l['current'] or '-':<5} {l['root']}{flag}")
        print(f"{'':<28} viewer: {l['viewer_url']}")


def cmd_fingerprint(args):
    print(json.dumps(fingerprint(find_root())))


def cmd_config(args):
    ctx = Ctx(find_root())
    if args.set:
        user = read_json(ctx.config_path, {}) or {}
        for kv in args.set:
            k, _, v = kv.partition("=")
            try:
                val = json.loads(v)
            except json.JSONDecodeError:
                val = v
            cur = user
            parts = k.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = val
        write_json(ctx.config_path, user)
        cfg = ctx.config()
        for kv in args.set:
            k = kv.partition("=")[0]
            cur = cfg
            for part in k.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            ok(f"Set {k} = {json.dumps(cur)}")
        return
    print(json.dumps(ctx.config(), indent=2))


# --------------------------------------------------------------------------- viewer

def viewer_data(ctx: Ctx):
    st = ctx.state()
    plan = ctx.plan() or {"checkpoints": []}
    tests = {}
    for cp in plan.get("checkpoints", []):
        tp = test_plan(ctx, cp["id"])
        if tp:
            tests[cp["id"]] = tp
    learn = ctx.ratchet / "learnings.md"
    cfg = ctx.config()
    reviews = {}
    for cp in plan.get("checkpoints", []):
        g = ((st.get("checkpoints") or {}).get(cp["id"]) or {}).get("gates", {}).get("adversarial") or {}
        rounds = []
        for i, rd in enumerate(g.get("rounds") or [], 1):
            who = [plain_reviewer(cfg, x["id"], x.get("model")) for x in rd.get("reviewers") or []] or \
                  [plain_reviewer(cfg, v["reviewer"]) for v in rd.get("verdicts") or []]
            own = all((_roster_spec(cfg, x["id"]) or {}).get("kind") == "agent" for x in rd.get("reviewers") or []) \
                if rd.get("reviewers") else None
            rounds.append({"round": i, "who": who, "blocking": rd.get("findings", 0),
                           "deferred": rd.get("deferred", 0), "own_company_only": own,
                           "unconfirmed": [v["reviewer"] for v in rd.get("verdicts") or []
                                           if v.get("origin") == UNCONFIRMED]})
        if rounds:
            reviews[cp["id"]] = {"rounds": rounds, "deferred_total": len(((st.get("checkpoints") or {}).get(cp["id"]) or {}).get("deferred") or [])}
    spec = None
    src = spec_source(ctx)
    if src:
        items = spec_items(ctx, src)
        cov = spec_coverage(ctx, plan, items)
        done = {cid for cid, c in (st.get("checkpoints") or {}).items() if c.get("status") == "done"}
        rows = []
        for it in items:
            for r in it["requirements"]:
                for sc in r.get("scenarios") or [{"ref": r["ref"], "name": r["title"]}]:
                    steps = sorted({c["checkpoint"] for c in cov["by_case"].get(sc["ref"]) or []} |
                                   set(cov["ui_steps"].get(sc["ref"]) or []))
                    rows.append({"ref": sc["ref"], "requirement": r["title"], "name": sc.get("name"),
                                 "tests": [c["case"] for c in cov["by_case"].get(sc["ref"]) or []], "steps": steps,
                                 "state": "left out" if sc["ref"] in cov["unmapped"] else
                                 ("not planned" if not steps else ("proven" if all(x in done for x in steps) else "planned"))})
        spec = {"items": [{"tool": it["tool"], "label": _spec_label(it), "status": it.get("status")} for it in items],
                "rows": rows, "tasks_total": len(cov["tasks"]), "tasks_ticked": len(cov["done_tasks"]),
                "unmapped": cov["unmapped"]}
    return {"reviews": reviews, "spec": spec,
            "state": dict({k: v for k, v in st.items() if k not in ("history", "approved")},
                         blocked_reason_plain=plain_reason(st.get("blocked_reason"))),
            "prototype": (ctx.run_dir() / "reference" / "prototype.html").exists(),
            "history": st.get("history", [])[-40:], "plan": plan, "tests": tests,
            "learnings": learn.read_text() if learn.exists() else "",
            # compute_accept_note=False: the viewer never shows accept_note from this "next" object (only `label`
            # and `action`), so it must not pay _blocked_accept_note's working-tree fingerprint on every rebuild.
            "next": dict(next_action(ctx, st, compute_accept_note=False), label=plain_now(ctx, st, plan)),
            "hooks": hooks_view(ctx, st), "generated_at": now()}


def build_viewer(ctx: Ctx) -> Path:
    rd = ctx.run_dir()
    data = viewer_data(ctx)
    write_json(rd / "viewer-data.json", data)
    if not VIEWER_TEMPLATE.exists():
        return rd / "viewer-data.json"
    html = VIEWER_TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__RATCHET_DATA__*/null", payload)
    out = rd / "viewer.html"
    out.write_text(html, encoding="utf-8")
    return out


def open_viewer(path: Path):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(path.as_uri())
    except Exception as e:
        print(f"(could not open browser automatically: {e}) Open {path} manually.")


# ---- viewer delivery ---------------------------------------------------------------------
# `view`            print the viewer link (live URL if a server is running, else file:// link)
# `view --serve`    start a live server IN THE BACKGROUND on a free port; URL saved to
#                   .ratchet/viewer.json and shown by `status --json` (viewer_url)
# `view --bind 0.0.0.0`  reachable from other machines; a random token is added to the URL
# `view --stop`     stop this project's server
# `lanes --serve`   one dashboard for every lane under a folder, each lane's viewer proxied under /lane/<n>/

VIEWER_PORT_BASE = 4477
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _free_port(bind: str, start: int) -> int:
    import socket
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((bind if bind != "localhost" else "127.0.0.1", port))
                return port
            except OSError:
                continue
    die(f"No free port found from {start}")


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _port_open(port, host="127.0.0.1") -> bool:
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _public_host(bind: str, override: str | None) -> str:
    if override:
        return override
    if bind in LOOPBACK:
        return "127.0.0.1"
    import socket
    try:  # the address other machines on the network would use
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname()


def server_info(state_file: Path) -> dict | None:
    info = read_json(state_file, None, record="Ratchet treats it as naming no running server")
    if info and _pid_alive(info.get("pid")) and _port_open(info.get("port")):
        return info
    return None


def viewer_link(ctx) -> dict:
    live = server_info(ctx.ratchet / "viewer.json")
    path = ctx.run_dir() / "viewer.html"
    return {"url": live["url"] if live else path.resolve().as_uri(), "live": bool(live),
            "file": str(path), "remote": bool(live and live.get("bind") not in LOOPBACK)}


def _spawn_background(argv: list, log: Path, env_extra=None) -> int:
    kw = {"stdout": open(log, "a"), "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
          "env": dict(os.environ, **(env_extra or {}))}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *argv], **kw).pid


def _start_server(kind, root: Path, state_file: Path, bind, port, token_mode, public_host, extra=()):
    info = server_info(state_file)
    if info and info.get("bind") == bind:
        return info, False
    if info:
        _stop_server(state_file)
    port = port or _free_port(bind, VIEWER_PORT_BASE)
    token = None
    if token_mode == "on" or (token_mode == "auto" and bind not in LOOPBACK):
        token = secrets.token_urlsafe(16)
    argv = ["--root", str(root), kind, "--serve", "--foreground", "--port", str(port), "--bind", bind, *extra]
    log = state_file.parent / f"{state_file.stem}.log"
    # the token travels by environment, not argv, so it never shows up in `ps`
    pid = _spawn_background(argv, log, {"RATCHET_VIEWER_TOKEN": token or ""})
    for _ in range(50):
        if _port_open(port):
            break
        import time
        time.sleep(0.1)
    else:
        try:  # don't leave a half-started server behind
            import signal
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        die(f"Viewer server did not start — see {log}")
    host = _public_host(bind, public_host)
    url = f"http://{host}:{port}/" + (f"?t={token}" if token else "")
    info = {"url": url, "pid": pid, "port": port, "bind": bind, "token": token, "started_at": now()}
    _write_record(state_file, info, pretty=True)
    return info, True


def _stop_server(state_file: Path) -> bool:
    info = read_json(state_file, None, record="Ratchet treats it as naming no running server")
    if not info:
        return False
    try:
        import signal
        import time
        os.kill(int(info["pid"]), signal.SIGTERM)
        for _ in range(30):  # wait for the port to be released
            if not _pid_alive(info["pid"]):
                break
            time.sleep(0.1)
    except (OSError, ValueError, KeyError):
        pass
    state_file.unlink(missing_ok=True)
    return True


def _http_server(bind, port, handler):
    """ThreadingHTTPServer without its reverse-DNS lookup of the bind address. That lookup can take
    5s or more (0.0.0.0 on a machine with slow DNS), long enough to outlast the start-up wait."""
    import http.server
    import socketserver

    class Server(http.server.ThreadingHTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = str(self.server_address[0]), self.server_address[1]
    return Server((bind, port), handler)


def _make_handler(routes, token):
    """routes(path) -> (status, content_type, bytes) or None."""
    import http.server
    from urllib.parse import urlparse, parse_qs

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if token:
                given = (parse_qs(u.query).get("t") or [""])[0]
                if not hmac.compare_digest(given.encode("utf-8", "replace"), token.encode()):
                    return self._send(403, "text/plain", b"Missing or wrong access token (?t=...) for this Ratchet viewer.")
            elif self.server.server_address[0] in ("127.0.0.1", "::1"):
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
                if host not in ("127.0.0.1", "localhost", "::1"):  # blocks DNS-rebinding reads of the plan
                    return self._send(403, "text/plain", b"Unexpected Host header.")
            res = routes(u.path)
            if res is None:
                return self._send(404, "text/plain", b"Not found")
            self._send(*res)

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return H


def _viewer_routes(ctx_factory, prefix=""):
    def routes(path):
        ctx = ctx_factory()
        if ctx is None:
            return None
        rel = path[len(prefix):] if path.startswith(prefix) else None
        if rel is None:
            return None
        if rel in ("", "/", "/viewer.html"):
            build_viewer(ctx)
            return 200, "text/html; charset=utf-8", (ctx.run_dir() / "viewer.html").read_bytes()
        if rel == "/viewer-data.json":
            return 200, "application/json", json.dumps(viewer_data(ctx)).encode()
        if rel == "/reference/prototype.html":
            p = ctx.run_dir() / "reference" / "prototype.html"
            return (200, "text/html; charset=utf-8", p.read_bytes()) if p.exists() else None
        return None  # nothing else in the run folder is served
    return routes


def cmd_view(args):
    ctx = Ctx(find_root())
    state_file = ctx.ratchet / "viewer.json"
    if args.stop:
        ok("Viewer server stopped" if _stop_server(state_file) else "No viewer server was running")
        return
    if args.serve and args.foreground:
        token = os.environ.get("RATCHET_VIEWER_TOKEN") or None
        handler = _make_handler(_viewer_routes(lambda: Ctx(find_root())), token)
        srv = _http_server(args.bind, args.port, handler)
        srv.serve_forever()
        return
    path = build_viewer(ctx)
    if args.serve:
        info, started = _start_server("view", ctx.root, state_file, args.bind, args.port, args.token or "auto",
                                      args.public_host)
        print(f"Ratchet viewer {'started' if started else 'already running'}: {info['url']}")
        if info.get("token"):
            print("  (reachable from other machines on this network; the ?t= token is required — share the whole URL)")
        if args.open:
            webbrowser.open(info["url"])
        return
    link = viewer_link(ctx)
    print(link["url"])
    if not link["live"]:
        print(f"  (static snapshot — for a live, auto-refreshing page run: ratchet.py view --serve)")
    if args.open:
        webbrowser.open(link["url"]) if link["live"] else open_viewer(path)


# ---- director dashboard --------------------------------------------------------------------

DASHBOARD_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ratchet Lanes</title>
<style>
:root{--bg:#f6f6f4;--surface:#fff;--text:#1b1b19;--muted:#6b6b66;--line:#e3e3de;--accent:#3b5bdb;
--ok:#2f9e44;--bad:#d6336c;--warn:#e67700;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--surface:#1b1b1a;--text:#ecece8;--muted:#9a9a93;--line:#2f2f2c;
--accent:#7c93ff;--ok:#51cf66;--bad:#f06595;--warn:#ffa94d;color-scheme:dark}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:24px;margin:0 0 4px}.meta{color:var(--muted);font-size:13px;margin-bottom:18px}
.tbl{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;min-width:640px}th,td{text-align:left;padding:11px 14px;border-top:1px solid var(--line);vertical-align:middle}
th{border-top:0;color:var(--muted);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.pill{font:600 11px/1 var(--mono);padding:5px 8px;border-radius:999px;background:var(--line);color:var(--muted);white-space:nowrap}
.pill.building{color:var(--accent)}.pill.done{color:var(--ok)}.pill.blocked,.pill.paused{color:var(--bad)}.pill.wait{color:var(--warn)}
.bar{display:inline-block;width:90px;height:6px;border-radius:3px;background:var(--line);vertical-align:middle;margin-right:8px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--ok)}
a{color:var(--accent);font-weight:600;text-decoration:none;white-space:nowrap}td.prog{white-space:nowrap}.mono{font-family:var(--mono);font-size:12.5px;color:var(--muted)}
.flag{color:var(--warn);font-weight:600}
</style></head><body><div class="wrap"><h1>Ratchet lanes</h1><div class="meta" id="meta">Loading…</div>
<div class="tbl"><table><thead><tr><th>Lane</th><th>Phase</th><th>Progress</th><th>Now</th><th>Needs</th><th></th></tr></thead>
<tbody id="rows"></tbody></table></div></div>
<script>
const T=new URLSearchParams(location.search).get("t");const q=T?"?t="+encodeURIComponent(T):"";
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function load(){try{const r=await fetch("lanes.json"+q+(q?"&":"?")+Date.now(),{cache:"no-store"});const L=await r.json();
document.getElementById("meta").textContent=L.length+" lane(s) · refreshed "+new Date().toLocaleTimeString();
document.getElementById("rows").innerHTML=L.map((l,i)=>{if(l.error)return `<tr><td colspan=6 class="mono">${esc(l.root)} — ${esc(l.error)}</td></tr>`;
const cls=l.waiting_on?"wait":l.phase==="building"?"building":l.phase==="done"?"done":(l.phase==="blocked"||l.phase==="paused")?"blocked":"";
const pct=l.total?Math.round(100*l.done/l.total):0;
return `<tr><td><b>${esc(l.slug)}</b><div class="mono">${esc(l.root)}</div></td><td><span class="pill ${cls}">${esc(l.phase)}</span></td>
<td class="prog"><span class="bar"><i style="width:${pct}%"></i></span>${l.done}/${l.total}</td><td class="mono">${esc(l.current||"–")} ${esc(l.step||"")}</td>
<td>${l.waiting_on?`<span class="flag">⚑ ${esc(l.waiting_on)}</span>`:""}${l.blocked_reason?`<div class="mono">${esc(l.blocked_reason_plain||l.blocked_reason)}</div>`:""}${l.hooks_warning?`<div class="mono">hooks not firing</div>`:""}</td>
<td><a href="lane/${i}/${q}">Open →</a></td></tr>`}).join("")}catch(e){document.getElementById("meta").textContent="Could not load lanes: "+e}
setTimeout(load,5000)}load();
</script></body></html>"""


def _scan_lanes(base: Path) -> list[Path]:
    roots = []
    for helper in sorted(base.rglob(f"{RATCHET_DIR}/active.json")):
        root = helper.parent.parent
        if any(part in FINGERPRINT_EXCLUDES - {RATCHET_DIR} for part in root.relative_to(base).parts):
            continue
        roots.append(root)
    return roots


def _dashboard_routes(base: Path):
    def routes(path):
        if path in ("", "/"):
            return 200, "text/html; charset=utf-8", DASHBOARD_HTML.encode()
        if path == "/lanes.json":
            out = []
            for root in _scan_lanes(base):
                try:
                    out.append(status_json(Ctx(root)))
                except SystemExit:
                    out.append({"root": str(root), "error": "unreadable state"})
            return 200, "application/json", json.dumps(out).encode()
        m = re.match(r"^/lane/(\d+)(/.*)?$", path)
        if m:
            lanes = _scan_lanes(base)
            i = int(m.group(1))
            if i >= len(lanes):
                return None
            sub = m.group(2) or "/"
            return _viewer_routes(lambda: Ctx(lanes[i]), "")(sub)
        return None
    return routes


# --------------------------------------------------------------------------- hooks (provider-agnostic)
#
# Ratchet needs three things from whatever agent CLI hosts the loop:
#   stop   — refuse to let the agent end its turn while gates are open
#   prompt — record that a real person typed a message (approvals depend on it)
#   guard  — refuse tool calls that would tamper with gate state or director material
# Every host has these hooks under different names, payload shapes and reply formats.
# `ratchet.py hook <stop|prompt|guard> --host auto|claude|codex|gemini|copilot` normalizes the
# payload, runs ONE shared decision, and answers in the host's dialect.
#
#   host     stop event     prompt event          guard event   block reply
#   claude   Stop           UserPromptSubmit      PreToolUse    exit 2 + stderr
#   codex    Stop           UserPromptSubmit      PreToolUse    exit 2 + stderr
#   gemini   AfterAgent     BeforeAgent           BeforeTool    exit 2 + stderr (AfterAgent retries the turn)
#   copilot  agentStop      userPromptSubmitted   preToolUse    JSON on stdout ({"decision":"block"} /
#                                                               {"permissionDecision":"deny"})
#
# Hooks fail OPEN on internal errors (logged to .ratchet/hook-errors.log): Copilot denies every
# tool call when a preToolUse hook crashes, and a crashing guard must never brick a session.

HOOK_HOSTS = ("claude", "codex", "gemini", "copilot")
HOOK_EVENTS = {  # host -> (stop, prompt, guard) event names in that host's config
    "claude": ("Stop", "UserPromptSubmit", "PreToolUse"),
    "codex": ("Stop", "UserPromptSubmit", "PreToolUse"),
    "gemini": ("AfterAgent", "BeforeAgent", "BeforeTool"),
    "copilot": ("agentStop", "userPromptSubmitted", "preToolUse"),
}
VENDOR_TO_HOST = {"anthropic": "claude", "openai": "codex", "google": "gemini", "github": "copilot"}

# What Ratchet enforces on each host CLI today. This is the one source for the per-CLI table in README.md
# ("Using it with Codex, Gemini or Copilot"); tests/test_docs.py renders that table from it cell by cell and
# checks every fact against the code that does the work:
#   records   the guard hook blocks hand-edits to .ratchet/ state and evidence (build_hook_config, decide_guard)
#   identity  the host names the acting sub-agent, so review files are checked against their owner (decide_guard)
#   stop      the stop hook keeps the agent from ending its turn early (build_hook_config, emit_hook)
#   replies   the prompt hook records what the user typed (decide_prompt)
#   guidance  the prompt hook's message reaches the agent (emit_hook; Copilot drops it)
HOST_ENFORCEMENT = {
    "claude": {"records": True, "identity": True, "stop": True, "replies": True, "guidance": True},
    "codex": {"records": True, "identity": False, "stop": True, "replies": True, "guidance": True},
    "gemini": {"records": True, "identity": False, "stop": True, "replies": True, "guidance": True},
    "copilot": {"records": True, "identity": False, "stop": True, "replies": True, "guidance": False},
}


def _host_names_subagent(host: str) -> bool:
    return bool(HOST_ENFORCEMENT.get(host, {}).get("identity"))


TOOL_KINDS = {
    "shell": {"bash", "powershell", "run_shell_command", "shell", "exec_command", "local_shell",
              "container.exec", "terminal", "run_in_terminal"},
    "write": {"write", "edit", "multiedit", "notebookedit", "apply_patch", "write_file", "replace", "create",
              "str_replace_editor", "str_replace_based_edit_tool", "create_file", "edit_file", "insert"},
    "read": {"read", "grep", "glob", "ls", "read_file", "read_many_files", "view", "search_file_content",
             "list_directory", "find_files"},
}
PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)


def _hook_input():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def detect_hook_host(data: dict, explicit: str | None = None) -> str:
    if explicit and explicit != "auto":
        return explicit
    if any(k in data for k in ("toolName", "toolArgs", "sessionId", "stopReason")):
        return "copilot"
    if data.get("hook_event_name") in ("AfterAgent", "BeforeAgent", "BeforeTool", "AfterTool", "BeforeModel"):
        return "gemini"
    if "turn_id" in data:
        return "codex"
    return VENDOR_TO_HOST.get(detect_host({}), "claude")


def _tool_kind(name: str) -> str:
    n = (name or "").lower()
    for kind, names in TOOL_KINDS.items():
        if n in names:
            return kind
    if n.startswith("mcp__") or n.startswith("mcp_"):
        return "mcp"
    return "other"


def normalize_hook(data: dict, host: str) -> dict:
    tool = data.get("tool_name") or data.get("toolName") or ""
    ti = data.get("tool_input", data.get("toolArgs", {}))
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except json.JSONDecodeError:
            ti = {"raw": ti}
    if not isinstance(ti, dict):
        ti = {"value": ti}
    cmd = ti.get("command") or ti.get("cmd") or ti.get("script") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    paths = [str(ti[k]) for k in ("file_path", "path", "absolute_path", "notebook_path", "filePath", "target_file")
             if isinstance(ti.get(k), str)]
    patch = ""
    for k in ("patch", "input", "raw"):
        if isinstance(ti.get(k), str) and "*** " in ti[k]:
            patch = ti[k]
            paths += [a or b for a, b in PATCH_FILE_RE.findall(patch)]
    kind = _tool_kind(tool)
    if kind in ("other", "mcp"):
        kind = "shell" if cmd else ("write" if paths or patch else kind)
    return {"host": host, "tool": tool, "kind": kind, "input": ti, "command": str(cmd), "paths": paths,
            "patch": patch, "cwd": data.get("cwd") or os.getcwd(),
            "prompt": data.get("prompt") or "", "session": data.get("session_id") or data.get("sessionId"),
            "agent_type": data.get("agent_type") or "", "agent_id": data.get("agent_id") or "",
            "transcript_path": data.get("transcript_path") or data.get("transcriptPath")}


def _heartbeat_path(root: Path) -> Path:
    return root / RATCHET_DIR / "hook-heartbeat.json"


def _heartbeat(root: Path, event: str, host: str):
    """Record that a hook fired (liveness only). It never carries crash state: crash records live only in per-run
    marker files and appended lines (see _record_hook_run). Concurrent hooks rewrite this shared file only through
    update_record, under its own lock, so none drops another's entry; a beat skipped because the lock could not be
    had within a second only makes a hook look less healthy until its next run.
    The rewrite goes to a per-process temp file that then replaces the heartbeat in one rename, so a concurrent
    hook never reads a truncated, half-written heartbeat (that read would fail and crash the reading hook)."""
    p = _heartbeat_path(root)

    def beat(hb):
        hb[event] = {"at": now(), "host": host}
        return hb
    try:  # every hook rewrites it: under its own lock (a second at most: a hook never waits long), so a concurrent
        # hook never drops this hook's entry; when the lock can't be had, this beat is skipped (liveness only)
        _ = update_record(p, beat, wait=1.0,
                          damaged="Ratchet treats it as recording no hook run and replaces it now")  # writes use open()
    except Exception:
        pass


ENGINE_PATH = str(Path(__file__).resolve())

# The prompt and stop hooks keep their own append-only records in the run folder (never state.json, which only a
# command holding the state lock writes): the engine reads them.
PROMPT_CHATS_FILE = "prompt-chats.jsonl"  # a chat the user typed in while no chat owned the run: {"at", "session"}
STOP_BLOCKS_FILE = "stop-blocks.jsonl"  # each stop the stop hook refused {"at", "phase"}, or a reset {"at", "reset"}


def _jsonl(p: Path) -> list:
    """The JSON objects on the lines of one of the run's .jsonl records ([] when it is missing or can't be read;
    a line that is not one is skipped, so a damaged line grants nothing)."""
    recs, _why = _read_record(p, lines=True)
    return recs or []


def _append_jsonl(p: Path, rec: dict) -> bool:
    try:
        _append_record(p, json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _prompt_owner(ctx, st):
    """(session, at, records read) for the first chat the prompt hook recorded for this run while no chat owned it,
    after the records already used (`prompt_chats_seen`). Only a record matching a message in human_log.jsonl (same
    time, same chat) counts. None when there is none."""
    recs = _jsonl(ctx.run_dir(st["slug"]) / PROMPT_CHATS_FILE)
    fresh = recs[int(st.get("prompt_chats_seen") or 0):]
    if not fresh:
        return None
    typed = {(e.get("at"), e.get("session")) for e in _human_since(ctx, "0") if e.get("session")}
    for r in fresh:
        if isinstance(r.get("session"), str) and r["session"] and (r.get("at"), r["session"]) in typed:
            return r["session"], r.get("at"), len(recs)
    return None


def _bind_prompt_owner(ctx, st) -> bool:
    """Make the chat the prompt hook recorded (see _prompt_owner) the owner of a run no chat owns yet. Each record
    is used once, so an owner set later is never replaced from an old record. True when the state changed."""
    if st.get("owner_session") or st.get("phase") in ("done", "cancelled"):
        return False
    got = _prompt_owner(ctx, st)
    if not got:
        return False
    st["owner_session"], typed_at, st["prompt_chats_seen"] = got
    ctx.log(st, "owner_recorded", session=got[0], via="prompt", typed_at=typed_at)
    return True


def _last_human_at(ctx) -> str:
    """When the user last typed a message (human_log.jsonl's last chat line; an override audit line is not one)."""
    recs, _why = _read_record(ctx.ratchet / "human_log.jsonl", lines=True, tail=65536)
    for e in reversed(recs or []):
        if not e.get("override") and isinstance(e.get("at"), str):
            return e["at"]
    return ""


def _stop_blocks(ctx, st) -> int:
    """How many stops the stop hook refused since the run last made progress: a PROGRESS_EVENTS or `resumed` entry
    in its history, a message the user typed, or a stop the hook allowed (a reset line)."""
    since = max([e.get("at", "") for e in st.get("history", [])
                 if e.get("event") in PROGRESS_EVENTS or e.get("event") == "resumed"] + [_last_human_at(ctx), ""])
    n = 0
    for r in _jsonl(ctx.run_dir(st["slug"]) / STOP_BLOCKS_FILE):
        if str(r.get("at", "")) <= since:
            continue
        n = 0 if r.get("reset") else n + 1
    return n


def _settle_waiver(ctx):
    """The enforcement hooks fired after the user chose to build without them: record the waiver's end in state.json
    now (Ctx.save_state drops it and counts the clearing), so hooks that later go silent can't bring it back. One
    try at the state lock, never a wait: if a command holds it, that command's own save clears the waiver."""
    lock = _StateLock(ctx.root, wait=0, cmd="the stop hook")
    if lock.acquire() or not lock.held:
        return
    try:
        st = ctx.state()
        if st.get("hooks_waived"):
            ctx.save_state(st)
    except (Exception, SystemExit):
        pass  # the next command's save clears it instead; the stop decision goes on from what was read
    finally:
        lock.release()


def decide_stop(n: dict) -> tuple[bool, str]:
    root = find_root(Path(n["cwd"]))
    ctx = Ctx(root)
    marker = _active_marker(root)
    if not ctx.ratchet.is_dir() or not ctx.active_slug():
        if marker:  # .ratchet/ vanished under an active run: never let that end the loop silently
            return _stop_error(root, f"Ratchet run '{marker['slug']}' was active here but its .ratchet/ state is gone. "
                                     "Tell the user; restore it (e.g. from git history or backup) or ask them to cancel.")
        return False, ""
    _heartbeat(root, "stop", n["host"])
    # Reading state.json needs no lock (it is replaced in one rename); this hook never waits on, or writes, the
    # state a command holds: its count of refused stops is its own file (STOP_BLOCKS_FILE).
    st, why = _read_record(ctx.state_path(), missing=None)
    if why is not None:
        # A damaged run state breaks this hook's check: logged to hook-errors.log like a crash (status, next and
        # doctor show it, and the hook does not count as working until it runs cleanly), and the stop fails closed a
        # few times. No crash marker: it is not this hook's own failure, so the commands that repair the run (accept-
        # state, init --force) are never locked out behind it.
        try:
            _append_record(root / RATCHET_DIR / "hook-errors.log",
                           f"{now()} {n['host']} stop: StateUnreadable: {ctx.state_path()}: {why}\n")
        except OSError:
            pass
        return _stop_error(root, f"Ratchet could not read its run state ({ctx.state_path()}: {why}).")
    if not st:
        return _stop_error(root, "Ratchet's state.json for the active run is missing or unreadable.")
    owner = st.get("owner_session") or (_prompt_owner(ctx, st) or (None,))[0]
    if owner and n.get("session") and n["session"] != owner:
        _reset_stop_errors(root)
        return False, ""  # another session in the same repo: not bound by this run (the prompt hook tells it)
    holder = _StateLock(root).live_holder()
    if holder:  # a Ratchet command is changing the run right now: what it leaves is not known yet
        return _stop_error(root, f"Ratchet is busy running {holder}, so this stop could not be checked. Wait for it "
                                 "to finish.")
    _reset_stop_errors(root)
    if st.get("hooks_waived") and not _waiver_applies(root, ctx.config(), st):
        _settle_waiver(ctx)
    blocks_file = ctx.run_dir(st["slug"]) / STOP_BLOCKS_FILE
    na = next_action(ctx, st)
    if na.get("stop_ok"):
        if _stop_blocks(ctx, st):
            _append_jsonl(blocks_file, {"at": now(), "reset": True})
        return False, ""
    w = live_wait(st)
    if w:  # the owner chat said it is waiting on background work (`ratchet.py wait`): this stop is not counted
        return False, (f"RATCHET: waiting on {w['reason']} until {w['until'][:16].replace('T', ' ')} UTC, so this "
                       "stop is allowed and not counted. When the background work reports back, carry on with "
                       "`ratchet.py next`.")
    cfg = ctx.config()
    count = _stop_blocks(ctx, st) + 1
    _append_jsonl(blocks_file, {"at": now(), "phase": st.get("phase")})
    if count > cfg["max_stop_blocks"]:
        reason = (f"The agent tried to stop {count} times without any gate progress — loop halted for the user to "
                  "look at.")
        lock = _StateLock(root, wait=0, cmd="the stop hook")  # one try: never waits behind a command
        if not lock.acquire():
            try:
                st = ctx.state()
                _set_blocked(st, reason)
                ctx.save_state(st)
            except (Exception, SystemExit):
                pass  # state Ratchet won't trust (Ctx.state refused it): the stop is still let through, said below
            finally:
                lock.release()
        return False, f"RATCHET: safety valve — {reason}"
    return True, ("RATCHET GATE: the work is not done, so you may not stop yet.\n"
                  f"Run: {st['slug']}  phase={na['phase']}"
                  + (f"  checkpoint={na.get('checkpoint')}  step={na.get('step')}" if na.get("checkpoint") else "")
                  + f"\nNext required action: {na['action']}\n"
                  f"(Engine: python3 \"{ENGINE_PATH}\" — load the ratchet-orchestrator skill if you haven't.)\n"
                  "Gates cannot be skipped or argued past. If the user asked you to stop, or you are genuinely "
                  "stuck, run `ratchet.py pause --reason \"...\"` and tell the user why.")


def _stop_errors_path() -> Path:
    return ratchet_home() / "stop-errors.json"


def _reset_stop_errors(root: Path):
    """Forget this project's count of refused stops on unreadable state. A damaged per-user file holds no count and
    is replaced now (it only lets a session stop sooner, after a few refusals, so it grants nothing)."""
    p = _stop_errors_path()
    key = str(root.resolve())
    data, why = _read_record(p)  # a quick look first: nothing to forget, nothing to lock or write
    if why is None and key not in data:
        return

    def forget(data):
        return data if data.pop(key, None) is not None or bad else KEEP
    bad = why is not None
    try:  # shared by every project: under its own lock, so no other project's count is dropped
        _ = update_record(p, forget, damaged="Ratchet treats it as holding no count and replaces it now", pretty=True)
    except OSError:
        pass


def _stop_error(root: Path, why: str) -> tuple[bool, str]:
    """Fail CLOSED for stop when state is unreadable — but only a few times, so a broken install can't trap a session.
    A damaged per-user count reads as no count (one more refusal at most) and is replaced."""
    p = _stop_errors_path()
    key = str(root.resolve())
    count = [1]  # when the count can't be read or saved, this is one refusal more (fail closed)

    def bump(data):
        n = data.get(key)
        count[0] = data[key] = (n if isinstance(n, int) and not isinstance(n, bool) else 0) + 1
        return data
    try:  # shared by every project: under its own lock, so no other project's count is dropped
        _ = update_record(p, bump, damaged="Ratchet treats it as holding no count and replaces it now", pretty=True)
    except Exception:
        pass
    if count[0] > 5:
        return False, f"RATCHET: {why} (letting you stop after repeated failures — tell the user.)"
    return True, f"RATCHET: {why} You may not stop silently: explain this to the user first."


# Turns an agent CLI sends through the prompt hook on its own, not typed by the user: a background sub-agent's
# hand-back, a background task's notice. A prompt where one of these exact markers STARTS any line of it is never
# recorded as the user's message, so it answers no waiting question and approves nothing. Before a line is checked,
# any leading whitespace (Unicode and zero-width included) and any leading HOST_TURN_WRAPPER_TAGS (opening or
# closing, repeated in any mix) are set aside on that line alone. A marker only mentioned mid-line changes nothing.
# A real reply with a marker at the start of one of its lines is dropped too (the safe side): the user sends it
# again. Every line of the text is checked, in one linear pass, so no amount of ordinary preamble text ahead of the
# marker line changes the answer: there is no fixed line count, block count or blank-line cap to tune or bypass.
HOST_TURN_MARKERS = ("Another Claude session sent a message:",   # Claude Code: a background sub-agent's hand-back
                     "[SYSTEM NOTIFICATION - NOT USER INPUT]",    # a host notice about background work
                     "<task-notification>")                       # Claude Code: a background task finished
HOST_TURN_WRAPPER_TAGS = ("system-reminder",)
_HOST_TURN_SPACE = "[\\s\\u180e\\u200b\\u200c\\u200d\\u2060\\ufeff]"  # \s is Unicode-aware; plus the zero-width ones
_HOST_TURN_TAGS = "|".join(map(re.escape, HOST_TURN_WRAPPER_TAGS))
HOST_TURN_PAD_RE = re.compile(r"\A(?:%s|</?(?:%s)\b[^>]*>)+" % (_HOST_TURN_SPACE, _HOST_TURN_TAGS), re.I)
_HOST_TURN_EOL_RE = re.compile("\r\n|[\n\r\v\f\x1c\x1d\x1e\x85  ]")  # the breaks str.splitlines uses


def host_generated_turn(prompt) -> bool:
    """Is this prompt one the agent CLI generated itself: does a HOST_TURN_MARKERS entry start any line of it, once
    that line's own leading whitespace and leading wrapper tags are set aside? One linear pass over the text, line
    by line; no fixed line count, block count or blank-line cap, so no amount of preamble text ahead of the marker
    line changes the answer."""
    text = prompt if isinstance(prompt, str) else ""
    pos = 0
    while True:
        m = _HOST_TURN_EOL_RE.search(text, pos)
        end = m.start() if m else len(text)
        line = text[pos:end]
        pad = HOST_TURN_PAD_RE.match(line)
        if pad:
            line = line[pad.end():]
        if line.startswith(HOST_TURN_MARKERS):
            return True
        if not m:
            return False
        pos = m.end()


def decide_prompt(n: dict) -> tuple[bool, str]:
    if os.environ.get("RATCHET_NESTED_REVIEW") == "1":
        return False, ""
    root = find_root(Path(n["cwd"]))
    rdir = root / RATCHET_DIR
    if not rdir.is_dir():
        return False, ""
    if host_generated_turn(n.get("prompt")):  # nothing is recorded: not the user's message, not a chat they typed in
        _heartbeat(root, "prompt", n["host"])
        return False, ("[ratchet] This turn was sent by the agent CLI itself (a background sub-agent's hand-back or "
                       "a task notice), not typed by the user, so Ratchet did not record it: it answers no waiting "
                       "question and approves nothing. Carry on; if it was really the user's reply, ask them to send "
                       "it again.")
    # This hook never takes the state lock and never writes state.json, so it never waits behind a long command
    # (a test run) and can't undo what a command wrote. It only appends to its own records: the message to
    # human_log.jsonl (with its hash, as one step), and, while no chat owns the run, this chat to PROMPT_CHATS_FILE;
    # the next command reads them (Ctx.state). A user message also restarts the stop hook's count (_stop_blocks).
    _heartbeat(root, "prompt", n["host"])
    log = rdir / "human_log.jsonl"
    problem = chain_problem(log, "human_log.jsonl")
    if problem:  # never a crash: the message is not recorded, so it approves and answers nothing
        return False, ("[ratchet] Ratchet did not record this message, so it approves nothing and answers no waiting "
                       f"question. {problem}")
    at = now()
    append_recorded(log, json.dumps({"at": at, "prompt": n["prompt"][:2000], "session": n["session"],
                                     "host": n["host"], "transcript_path": n.get("transcript_path")},
                                    ensure_ascii=False) + "\n")
    ctx = Ctx(root)
    if ctx.active_slug():
        st, why = _read_record(ctx.state_path(), missing=None)
        if why is not None:  # never a crash: say what is wrong (every command refuses until the file is fixed)
            return False, (f"[ratchet] Ratchet can't read the active run's state.json ({why}), so it can't say "
                           "what this message means for the run, and its commands refuse until the file is fixed. "
                           "Tell the user; restore the file from a backup or their git history, or, to start the "
                           "feature over, they can run `RATCHET_HUMAN_OVERRIDE=1 ratchet.py init --force "
                           "\"<feature>\"` from their own terminal.")
        if st and st.get("phase") not in ("done", "cancelled"):
            here = n.get("session")
            if not st.get("owner_session"):
                # no chat owned the run yet (the first run in a repo starts before the prompt hook can log anything):
                # the first chat the user types in for this run owns it, and only `resume --claim` moves it later
                st["owner_session"] = (_prompt_owner(ctx, st) or (None,))[0]
                if here and not st["owner_session"]:
                    _append_jsonl(ctx.run_dir(st["slug"]) / PROMPT_CHATS_FILE, {"at": at, "session": here})
                    st["owner_session"] = here  # this copy only: the hints below treat it as the owner
            eng = f'python3 "{ENGINE_PATH}"'
            p = st.get("pending") or {}
            claim_here = p.get("action") == "claim" and here and p.get("session") == here
            if st.get("owner_session") and here and here != st["owner_session"] and not claim_here:
                return False, (f"[ratchet] This repo has an unfinished Ratchet run '{st['slug']}' (phase {st['phase']}) "
                               "started in another chat. If the user wants to continue it here, run "
                               f"{_ask_cmd('claim', eng=eng)}, end your message with the question it prints, word for "
                               "word, as the only question in it, and end your turn; run "
                               f"`{eng} resume --claim` only if their first reply after it is one of the listed yes "
                               "replies. Otherwise leave the run alone and do what they asked.")
            if p.get("action") and p["action"] != "hooks-waive":
                cmd = PENDING_COMMAND.get(p["action"], p["action"]) + (" " + p["mode"] if p.get("mode") else "")
                what = _question_what(p["action"], p.get("mode"))
                again = _ask_cmd(p["action"], p.get("mode"), eng=eng)
                asked_in = _question_session(st, p)
                if asked_in and here and here != asked_in:
                    return False, (f"[ratchet] A Ratchet question (shall I {what}?) is waiting for a reply in another "
                                   "chat. This message does not answer it and approves nothing; do what the user asked "
                                   "here.")
                if p.get("overtook"):
                    return False, (f"[ratchet] Your waiting question (shall I {what}?) was printed while another one was "
                                   "still waiting, so this reply approves nothing, whatever it says. Answer what they "
                                   f"said; if you still need their answer, run {again} again and show them only that "
                                   "question.")
                if p["action"] == "choice":
                    letter = choice_letter(n["prompt"], p.get("options") or [])
                    return False, (f"[ratchet] This reply answers your lettered question ({', '.join(p.get('options') or [])}). "
                                   + (f"It reads as option {letter}. " if letter else "It is NOT a clear pick of one option. ")
                                   + f"Run `{eng} choice` to record it, then continue. It does NOT approve the plan or a "
                                   "checkpoint.")
                try:
                    first = len(_replies(ctx, st, p.get("asked_at"), p)) <= 1
                except (Exception, SystemExit):
                    first = True
                if not first:
                    return False, (f"[ratchet] The user's first reply after your waiting question (shall I {what}?) "
                                   "already answered it, so this later message is not their answer: it does NOT "
                                   "approve the plan, a checkpoint or anything else. If you still need their yes, "
                                   f"print the question again ({again}).")
                verdict = ("It is one of the listed yes replies" if is_listed_yes(n["prompt"])
                           else "It is NOT one of the listed yes replies, so it approves nothing")
                if p["action"] in ("approve-plan", "human-approve"):
                    return False, (f"[ratchet] This reply answers your waiting question: shall I {what}? {verdict}. "
                                   f"Only if it is a listed yes, run `{eng} {cmd}`. Otherwise answer what they said or "
                                   f"make their change"
                                   + (f" (at a review, record change requests with `{eng} feedback`)"
                                      if p["action"] == "human-approve" else "")
                                   + f", and ask again later with {again}.")
                return False, (f"[ratchet] This reply answers your waiting question: shall I {what}? {verdict}. "
                               f"Only after a listed yes run `{eng} {cmd}`; otherwise run `{eng} ask withdraw` and do "
                               "what they asked. This reply does NOT approve the plan, a checkpoint or anything else.")
            if st["phase"] == "building" and re.match(r"^\s*(stop|pause|hold on|wait|halt|cancel)\b", n["prompt"], re.I):
                return False, (f"[ratchet] The user asked you to stop. Run `{eng} pause --reason \"user asked\"`, "
                               "confirm in one line, and ask whether they want to resume later or cancel the run.")
            if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review",
                               "awaiting_final_review", "blocked"):
                if CANCEL_HINT_RE.search(n["prompt"]):
                    hint = (f"This may be a request to cancel the run — run {_ask_cmd('cancel', eng=eng)}, end your "
                            "message with the question it prints, word for word, as the only question in it, and end "
                            f"your turn; run `{eng} cancel` only if their first reply after it is one of the listed "
                            "yes replies.")
                else:
                    ask = {"awaiting_plan_approval": "approve-plan"}.get(st["phase"], "human-approve")
                    hint = ("Ratchet has not asked its approval question yet, so this reply approves nothing"
                            + (" even though it reads like a yes. " if message_intent(n["prompt"]) == "approve"
                               else ". Answer it or make the requested change. ")
                            + "When you need their approval, "
                            f"run {_ask_cmd(ask, eng=eng)}"
                            + (" (or offer the lettered options from `next` with `ask choice`)"
                               if st["phase"] == "blocked" else "")
                            + ", end your message with the question it prints, word for word, as the only question "
                            "in it, and end your turn; only their first reply after that question counts.")
                return False, (f"[ratchet] Run '{st['slug']}' is waiting on the user (phase {st['phase']}). {hint} "
                               f"(Engine: {eng})")
    return False, ""


# Inside .ratchet/, agents may only write these (everything else is written by ratchet.py itself).
ALLOWED_RATCHET_WRITES = re.compile(
    r"^runs/[^/]+/(reviews|states|reference|tests)(/.*)?$|^runs/[^/]+/(checkpoints|app-scout)\.json$"
    r"|^(standards\.md|learnings\.md|app\.log)$", re.I)
READ_ONLY_PROGRAMS = {"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "jq", "ls", "wc",
                      "stat", "file", "diff", "cmp", "md5sum", "sha1sum", "sha256sum", "cd", "pushd", "popd", "pwd",
                      "true", "tree", "du"}
READ_ONLY_GIT = {"diff", "log", "show", "status", "ls-files", "blame", "rev-parse", "cat-file"}
RATCHET_TOKEN_RE = re.compile(r"""[^\s'"<>|;&()`]*\.ratchet(?:[/\\][^\s'"<>|;&()`]*)?""", re.I)
REDIRECT_RE = re.compile(r"(?<![0-9&])>>?\s*(?!&)(\S+)")
ENGINE_CALL_RE = re.compile(r"""^\s*(?:(?:\S*python3?(?:\.\d+)?|py)\s+(?:-\S+\s+)*["']?\S*(ratchet|external_review|ui_review)\.py\b"""
                            r"""|\S*node\s+(?:-\S+\s+)*["']?\S*capture\.mjs\b)""")
NESTED_AGENT_RE = re.compile(r"(^|[\s;&|(/])(claude|gemini|copilot)(?![\w.-])[^;&|\n]*\s(-p|--print|--prompt)\b"
                             r"|(^|[\s;&|(/])codex(?![\w.-])[^;&|\n]*\sexec\b")
SELF_HOOK_RE = re.compile(r"\bhook\s+(stop|prompt|guard)\b|\bhook-(stop|prompt|guard)\b")
ASSIGN_RE = re.compile(r"""(?:^|[\s;&|(])(?:export\s+)?([A-Za-z_]\w*)=("[^"]*"|'[^']*'|[^\s;&|]+)""")
HEREDOC_RE = re.compile(r"""<<-?\s*(['"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2\s*(?=\n|$)""", re.S)


def _expand(cmd: str) -> str:
    """Drop heredoc bodies (they're data, not commands) and expand shell variables assigned in the same command,
    so `RUN=.ratchet/runs/x; echo > $RUN/state.json` is judged as what it really writes."""
    cmd = HEREDOC_RE.sub(lambda m: "<<" + m.group(3), cmd)
    for name, val in ASSIGN_RE.findall(cmd):
        val = val.strip("\"'")
        cmd = re.sub(r"\$\{" + name + r"\}|\$" + name + r"(?![A-Za-z0-9_])", lambda _m: val, cmd)
    return cmd
DIRECTOR_CALL_RE = re.compile(r"""ratchet\.py["']?\s+(?:--root\s+\S+\s+)?director-(sign|key|enroll)\b""")
# Variables a builder session may read but never set. Each is matched however it is set: VAR=, export/declare/
# typeset/local/readonly/setenv/fish `set -x`, a dict or kwargs entry (python3 -c os.environ / env=...), putenv-style
# calls, or indirectly (the name stored in another variable, or built from pieces, then used by eval, env -S,
# printf -v, ${!v}, sourcing, or an os.environ write). A plain read ($VAR, printenv VAR, grep VAR docs) is fine.
RESERVED_ENV = (
    ("RATCHET_HUMAN_OVERRIDE", "RATCHET GUARD: RATCHET_HUMAN_OVERRIDE is for a person at their own terminal, not "
                               "for agents."),
    ("RATCHET_DIRECTOR_KEY_FILE", "RATCHET GUARD: the director key path (RATCHET_DIRECTOR_KEY_FILE) cannot be "
                                  "redirected by a builder session."),
    ("RATCHET_ROLE", "RATCHET GUARD: RATCHET_ROLE and the director commands are reserved for the director session."),
    ("RATCHET_HOME", "RATCHET GUARD: Ratchet's per-user folder (RATCHET_HOME) is off-limits to builder sessions; it "
                     "cannot be redirected from here."),
)
RESERVED_PIECES_MSG = ("RATCHET GUARD: this command builds one of Ratchet's reserved variable names "
                       "(RATCHET_HUMAN_OVERRIDE, RATCHET_ROLE, RATCHET_HOME, RATCHET_DIRECTOR_KEY_FILE) from pieces "
                       "and sets it indirectly. Builder sessions may read those variables but never set them.")
ENV_INDIRECT_RE = re.compile(
    r"\beval\b|\benv\b[^;&|\n]*\s(-[A-Za-z]*S|--split-string)|\b(export|declare|typeset|local|readonly|setenv)\b"
    r"|\bset\s+-[A-Za-z]*x|\bprintf\s+-v\b|\$\{!|\bsource\s|(^|[;&|(]\s*)\.\s+\S"
    r"|environ\s*\[[^\]]*\]\s*\|?=(?!=)|environ\s*\.\s*(update|setdefault|__setitem__)\b|environ\s*\|?=(?!=)"
    r"|\b(putenv|setenv|Setenv|SetEnvironmentVariable)\s*\(|process\.env\s*\[[^\]]*\]\s*=(?!=)"
    r"|Object\.assign\s*\(\s*process\.env|\bENV\s*\[[^\]]*\]\s*=(?!=)|\$ENV\{[^}]*\}\s*=(?!=)|--(set)?env\b")
ENV_TOKEN_RE = re.compile(r"(?<![\w$])(?<!\$\{)(?<!\$\{!)(?<!\$\{#)RATCHET\w*", re.I)


def _reserved_env_problem(cmd: str) -> str | None:
    """The guard message when a shell command sets (directly or indirectly) a variable only a person or the
    director may set; None otherwise. The one detector for every reserved variable (plain reads stay allowed).
    Best-effort: a script written to a file first and run later is not seen here, which is why the engine also
    distrusts these values on its own (terminal code, hook-recorded folder, key file and director role)."""
    indirect = bool(ENV_INDIRECT_RE.search(cmd) or ENV_INDIRECT_RE.search(_expand(cmd)))
    texts = [cmd] + ([_expand(cmd)] if _expand(cmd) != cmd else [])
    for text in texts:
        for seg in _segments(text):
            prog, _sub = _program(seg)
            if prog in READ_ONLY_PROGRAMS and "$(" not in seg and "`" not in seg:
                continue  # grep/cat/... only read; mentioning a name there sets nothing
            for m in ENV_TOKEN_RE.finditer(seg):
                tok = m.group(0)
                full = next(((n, msg) for n, msg in RESERVED_ENV if tok.upper() == n), None)
                if full:
                    name, msg = full
                    after = seg[m.end():]
                    before = seg[:m.start()]
                    if re.match(r"""['"\]}]*\s*\+?=(?!=)|['"]\s*:""", after) or \
                            re.search(r"""(?:^|[\s;&|(])[A-Za-z_]\w*=\s*['"]?$""", before) or \
                            re.search(r"\b(export|declare|typeset|local|readonly|setenv)\b|\bset\s+-[A-Za-z]*x",
                                      before) or \
                            re.search(r"\b(putenv|setenv|Setenv|SetEnvironmentVariable|setdefault|update|"
                                      r"__setitem__)\s*\(\s*['\"]?$", before) or indirect:
                        return msg
                elif tok.startswith("RATCHET") and indirect and \
                        any(n.startswith(tok) and n != tok for n, _m in RESERVED_ENV):
                    return RESERVED_PIECES_MSG
    return None


DANGEROUS_GIT_RE = re.compile(r"\bgit\s+(clean\b[^;&|\n]*\s-[a-zA-Z]*[xX]|stash\b[^;&|\n]*(\s--all\b|\s-a\b))")


def _segments(cmd: str) -> list[str]:
    return [p for p in re.split(r"\|\||&&|;|\||\n", cmd) if p.strip()]


def _program(seg: str) -> tuple[str, str]:
    toks = seg.strip().split()
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):  # leading VAR=value
        toks.pop(0)
    while toks and toks[0] in ("sudo", "env", "command", "exec", "time", "nice", "nohup"):
        toks.pop(0)
    if not toks:
        return "", ""
    return os.path.basename(toks[0]), (toks[1] if len(toks) > 1 else "")


def _ratchet_rel(path: str, cwd: str) -> str | None:
    """Path relative to the nearest .ratchet/ it lies in (case-insensitive), or None if it isn't inside one."""
    full = os.path.normpath(os.path.join(cwd, os.path.expanduser(path))).replace("\\", "/")
    m = re.search(r"(^|[/=:])\.ratchet(/|$)(.*)$", full, re.I)
    if not m:
        return None
    return m.group(3)


# Review files that only their reviewer may write (checked where the host says which sub-agent is acting: Claude
# Code puts agent_type on every tool call a sub-agent makes). An empty owner set means "only the script writes it".
REVIEW_OWNERS = [
    (ADV_REVIEW_RE, {"ratchet-adversary"}),  # one pattern with the writer record (_note_review_writer)
    # a UI round's per-role reviews (the guard also records their writer; named groups as in ADV_REVIEW_RE)
    (re.compile(r"^runs/(?P<slug>[^/]+)/reviews/(?P<cp>[^/]+)/round-(?P<n>\d+)/(?P<name>visual\.json)$", re.I),
     {"ratchet-visual-reviewer"}),
    (re.compile(r"^runs/(?P<slug>[^/]+)/reviews/(?P<cp>[^/]+)/round-(?P<n>\d+)/(?P<name>behavior\.json)$", re.I),
     {"ratchet-behavior-reviewer"}),
    (re.compile(r"^runs/[^/]+/reviews/[^/]+/round-\d+/combined\.json$", re.I), set()),
    (re.compile(r"^runs/[^/]+/reviews/[^/]+/capture[^/]*/manifest\.json$", re.I), set()),
]
INTERPRETER_RE = re.compile(r"(^|[\s;&|(/])(python[\d.]*|py|node|deno|bun|bash|sh|zsh|dash|ksh|perl|ruby|php|osascript)(?=\s|$)")


def _owner_problem(path: str, cwd: str, who: dict | None) -> str | None:
    """Why this acting agent may not write this review file, or None."""
    rel = _ratchet_rel(path, cwd)
    if not rel or not who or not who.get("enforce"):
        return None
    for rx, owners in REVIEW_OWNERS:
        if rx.match(rel):
            actor = (who.get("agent_type") or "").split(":")[-1]
            if actor and actor in owners:
                return None
            if not owners:
                return (f"RATCHET GUARD: {rel} is written by ui_review.py only. Run the script (combine) instead of "
                        "writing the file yourself.")
            return (f"RATCHET GUARD: {rel} is a reviewer's own output. Only a {' or '.join(sorted(owners))} sub-agent "
                    "may write it: launch that reviewer and let it write its file. Never write or edit a review "
                    "yourself, not even to copy what the reviewer said.")
    return None


WRITE_CODE_RE = re.compile(r"""open\([^)]*,\s*['"][wax]|write_text\(|write_bytes\(|writeFileSync|appendFileSync|\.write\(""")
ORCH_EDIT_MSG = ("RATCHET GUARD: during the build, project files are changed by Ratchet's sub-agents (implementer, fixer, "
                 "test writer, design system), not by the orchestrator, so that every change goes through the gates by "
                 "the role that owns it. Spawn ratchet-implementer (code or UI fixes), ratchet-fixer (review findings) "
                 "or ratchet-design-system (DESIGN.md) with the change you want. Files under .ratchet/ that you own "
                 "(checkpoints, capture specs, reference) are fine. If the user asked for something unrelated to the build, "
                 "pause the run first (ratchet.py pause), make the change, then resume.")


def _in_project_outside_ratchet(path: str, cwd: str, root: Path) -> bool:
    full = Path(os.path.normpath(os.path.join(cwd, os.path.expanduser(path))))
    try:
        full.relative_to(root.resolve())
    except ValueError:
        return False  # /tmp, /dev/null, another folder: not the project
    return _ratchet_rel(str(full), cwd) is None


def _orchestrator_edit(n: dict, root: Path) -> bool:
    """Is the main session (not a sub-agent) about to change a project file outside .ratchet/?"""
    cwd = n["cwd"]
    if n["kind"] == "write":
        return any(_in_project_outside_ratchet(p, cwd, root) for p in n["paths"])
    if n["kind"] != "shell" or not n["command"]:
        return False
    raw = n["command"]
    if INTERPRETER_RE.search(raw):
        for m in HEREDOC_RE.finditer(raw):
            body = m.group(0)
            if WRITE_CODE_RE.search(body) and not RATCHET_TOKEN_RE.search(body):
                return True
    cmd = _expand(raw)
    for seg in _segments(cmd):
        if ENGINE_CALL_RE.match(seg):
            continue
        targets = [t for t in REDIRECT_RE.findall(seg) if not t.startswith("&")]
        prog, _ = _program(seg)
        if prog == "sed" and re.search(r"\s-[a-zA-Z]*i", seg):
            targets += [t for t in seg.split()[1:] if not t.startswith("-") and ("/" in t or "." in t)][-1:]
        if prog == "tee":
            targets += [t for t in seg.split()[1:] if not t.startswith("-")]
        if any(_in_project_outside_ratchet(t.strip("'\""), cwd, root) for t in targets):
            return True
    return False


def _write_allowed(path: str, cwd: str) -> bool:
    rel = _ratchet_rel(path, cwd)
    return rel is None or bool(rel and ALLOWED_RATCHET_WRITES.match(rel))


_WRAPPERS = {"sudo", "env", "command", "exec", "time", "nice", "nohup", "timeout", "xargs", "stdbuf"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_HOOK_SUBCOMMANDS = ("hook", "hook-stop", "hook-prompt", "hook-guard")


def _calls_own_hook(cmd: str, depth: int = 0) -> bool:
    """True when `cmd` really runs `ratchet.py hook ...` (directly, through python, a wrapper such as env or
    timeout, an unknown $PROGRAM, `bash -c` or eval). Text that only mentions a hook (a grep pattern, an echo, a
    commit message, a comment) is not a call. A command the shell-word reader can't follow (a command substitution,
    a heredoc, $'...') is blocked whenever it mentions a hook at all, so what can't be read fails safe."""
    words = _shell_words(cmd)
    if words is None:
        return bool(SELF_HOOK_RE.search(cmd))
    for w in _simple_commands(words):
        while w and _ASSIGNMENT_RE.match(w[0]):
            w = w[1:]
        while w and Path(w[0]).name in _WRAPPERS:
            w = w[1:]
            while w and (w[0].startswith("-") or _ASSIGNMENT_RE.match(w[0]) or re.fullmatch(r"[0-9.]+[smhd]?", w[0])):
                w = w[1:]
        if not w:
            continue
        prog = Path(w[0]).name
        if prog == "eval" or prog in _SHELLS:
            code = [" ".join(w[1:])] if prog == "eval" else \
                [w[i + 1] for i in range(1, len(w) - 1) if re.fullmatch(r"-[a-z]*c[a-z]*", w[i])]
            if depth < 3 and any(_calls_own_hook(c, depth + 1) for c in code):
                return True
            continue
        if re.fullmatch(r"python[0-9.]*|py", prog):
            w = w[1:]
            while w and w[0].startswith("-"):
                w = w[1:]
            if not w:
                continue
            prog = Path(w[0]).name
        if (prog == "ratchet.py" or "$" in w[0]) and _engine_subcommand(w[1:]) in _HOOK_SUBCOMMANDS:
            return True
    return False


def _guard_shell(cmd: str, cwd: str, who: dict | None = None) -> str | None:
    raw = cmd
    if INTERPRETER_RE.search(raw):  # heredoc bodies fed to an interpreter are code, not data: check what they touch
        for m in HEREDOC_RE.finditer(raw):
            for t in RATCHET_TOKEN_RE.findall(m.group(0)):
                if not _write_allowed(t, cwd):
                    return ("RATCHET GUARD: this script touches Ratchet's run state (.ratchet/). Gate state, evidence, "
                            "approvals and config are owned by ratchet.py — use its commands instead. To read those "
                            "files, use cat or jq.")
                why = _owner_problem(t, cwd, who)
                if why:
                    return why
    cmd = _expand(cmd)
    if NESTED_AGENT_RE.search(cmd):
        return ("RATCHET GUARD: launching another agent session (claude -p, codex exec, gemini -p, copilot -p) from "
                "the shell is blocked while Ratchet is active — it could impersonate the user. Use sub-agents, "
                "external_review.py for adversarial reviews, or ui_review.py for UI reviews.")
    if _calls_own_hook(cmd):
        return ("RATCHET GUARD: Ratchet's hooks are called by your agent CLI, not by you. Calling `ratchet.py hook` "
                "yourself would forge a user message or a gate event.")
    if DANGEROUS_GIT_RE.search(cmd):
        return "RATCHET GUARD: this git command would delete or stash Ratchet's run state (ignored files)."
    inside = any(re.match(r"^\s*(cd|pushd)\s+\S*\.ratchet", seg, re.I) for seg in _segments(cmd))
    for seg in _segments(cmd):
        if re.fullmatch(r"\s*(export\s+)?[A-Za-z_]\w*=\S*\s*", seg):
            continue  # a bare assignment writes nothing; its uses were expanded above
        prog, sub = _program(seg)
        mentions = RATCHET_TOKEN_RE.findall(seg)
        if not mentions and not inside:
            continue
        if ENGINE_CALL_RE.match(seg):
            if not REDIRECT_RE.search(seg):
                continue  # ratchet.py / reviewer scripts write their own files
        for t in mentions + REDIRECT_RE.findall(seg):
            if prog in READ_ONLY_PROGRAMS and t not in REDIRECT_RE.findall(seg):
                continue  # reading a review is fine
            why = _owner_problem(t, cwd, who)
            if why:
                return why
        read_only = prog in READ_ONLY_PROGRAMS or (prog == "git" and sub in READ_ONLY_GIT) or \
            (prog == "find" and not re.search(r"\s-(delete|exec|execdir|ok|fprint)\b", seg)) or \
            (prog in ("mkdir",) and all(_write_allowed(t, cwd) for t in mentions))
        targets = [t for t in REDIRECT_RE.findall(seg) if t not in ("/dev/null", "/dev/stderr", "/dev/stdout")]
        if read_only and not targets:
            continue
        if read_only and targets and all(_write_allowed(t, cwd) and not inside for t in targets):
            continue
        if not inside and mentions and all(_write_allowed(t, cwd) for t in mentions) and \
                all(_write_allowed(t, cwd) for t in targets):
            continue  # e.g. writing a review JSON or a capture spec
        return ("RATCHET GUARD: this command could change Ratchet's run state (.ratchet/). Gate state, evidence, "
                "approvals and config are owned by ratchet.py — use its commands (behavior / ui-record / adv-record "
                "/ complete / config) instead. Reading those files is fine.")
    return None


def _run_phase(root: Path) -> str | None:
    try:
        ctx = Ctx(root)
        if not (ctx.ratchet / "active.json").exists():
            return None
        st, _why = _read_record(ctx.state_path())
        return (st or {}).get("phase")
    except (Exception, SystemExit):
        return None


def decide_guard(n: dict) -> tuple[bool, str]:
    cmd, cwd = n["command"], n["cwd"]
    if not _is_director_session():
        if n["kind"] == "shell" and cmd:
            why = _reserved_env_problem(cmd)
            if why:
                return True, why
        key_bits = [str(director_key_path()), str(ratchet_home()), "director.key", ".config/ratchet", ".config/rat"]
        scan = cmd + " " + " ".join(n["paths"]) + " " + json.dumps({k: v for k, v in n["input"].items()
                                                                     if k in ("path", "pattern", "glob", "file_path",
                                                                              "absolute_path", "dir_path")})
        wide = n["kind"] in ("read", "shell", "other", "mcp") and ".config" in scan and re.search(r"\brat", scan)
        if any(b in scan for b in key_bits) or wide:
            return True, ("RATCHET GUARD: the director's key and Ratchet's per-user files are off-limits to builder "
                          "sessions. Wait for the director to approve this lane.")
        if n["kind"] == "shell" and DIRECTOR_CALL_RE.search(cmd):
            return True, "RATCHET GUARD: director commands and RATCHET_ROLE are reserved for the director session."
        if n["kind"] == "shell" and re.search(r"reviewer-status\b[^;&|\n]*--mark", cmd):
            return True, ("RATCHET GUARD: only the director (or the user, in their own terminal) can switch a "
                          "reviewer off. Reviewers that genuinely fail are marked unavailable automatically.")
    root = find_root(Path(cwd))
    marks = False
    if (root / RATCHET_DIR).is_dir():
        _heartbeat(root, "guard", n["host"])
        if n.get("agent_type") and _host_names_subagent(n["host"]):
            _heartbeat(root, "agent_marks", n["host"])  # this CLI names the sub-agent on its tool calls
        marks = isinstance(read_heartbeat(root).get("agent_marks"), dict)
    who = {"enforce": marks and _host_names_subagent(n["host"]), "agent_type": n.get("agent_type")}
    if n["kind"] == "shell" and cmd:
        why = _guard_shell(cmd, cwd, who)
        if why:
            return True, why
        _note_engine_call(root, cmd, n.get("session"))  # which chat runs `init` / `ask` / `wait` here (_noted_session)
    if who["enforce"] and not n.get("agent_type") and _run_phase(root) == "building" and _orchestrator_edit(n, root):
        return True, ORCH_EDIT_MSG
    if n["kind"] == "write":
        for p in n["paths"]:
            why = _owner_problem(p, cwd, who)
            if why:
                return True, why
        bad = [p for p in n["paths"] if not _write_allowed(p, cwd)]
        if n["patch"] and not bad:
            bad = [p for p in re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", n["patch"], re.M)
                   if not _write_allowed(p, cwd)]
        if bad:
            return True, ("RATCHET GUARD: gate state, evidence, approvals and config under .ratchet/ are written by "
                          "ratchet.py only (you may write reviews/, states/, tests/, reference/, checkpoints.json, "
                          f"app-scout.json, standards.md, learnings.md). Blocked: {bad[0]}")
        _note_review_writer(n)
    return False, ""


def emit_hook(host: str, event: str, block: bool, message: str) -> int:
    """Answer in the host's dialect. Returns the process exit code."""
    if host == "copilot":
        if block and event == "guard":
            print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": message}))
        elif block and event == "stop":
            print(json.dumps({"decision": "block", "reason": message}))
        elif message and event != "prompt":
            print(message, file=sys.stderr)
        return 0
    if block:
        print(message, file=sys.stderr)
        return 2
    if message:
        if host == "claude" and event == "prompt":
            print(message)  # Claude Code adds UserPromptSubmit stdout to the model's context
        else:
            print(message, file=sys.stderr)
    return 0


def _hook_record_failed(n: dict, host: str, event: str, detail: str) -> str:
    """A hook could not save its hook-env record (or a director's folder record). Record it the way a hook crash is
    recorded (a crash marker, or the unsaved-crash fallback, plus a hook-errors.log line), so the hook counts as
    broken until a later run saves the record cleanly, and return the plain line the host shows."""
    saved = False
    root = _ratchet_root_of(n.get("cwd"))
    if root is not None:
        try:
            saved = _record_hook_run(root, host, event, crashed=True) or _record_unsaved_crash(root, host, event)
            _append_record(root / RATCHET_DIR / "hook-errors.log", f"{now()} {host} {event}: HookRecordError: {detail}\n")
        except Exception:
            pass
    line = (f"RATCHET: the {event} hook could not save which per-user folder, director key file and session role "
            f"this agent CLI uses ({detail}). Until a hook saves it, Ratchet refuses director commands here and "
            "won't trust a per-user folder it can't confirm. Make that file's folder writable; the next hook run "
            "writes it again.")
    if root is not None and not saved:
        line += (f" Ratchet could not save this failure either (it cannot write to {RATCHET_DIR}/{HOOK_CRASHES}/), "
                 "so run the doctor command.")
    return line


def run_hook(event: str, host_arg: str | None) -> int:
    data = _hook_input()
    host = detect_hook_host(data, host_arg)
    started = now()  # a clean run resolves only crashes recorded before it began
    global _IN_HOOK
    _IN_HOOK = True  # this process has the agent CLI's own environment: it is the one the engine compares against
    try:
        n = normalize_hook(data, host)
        record_failed = None  # remembering the folder must never break the hook itself, nor fail silently
        try:
            env_root = _ratchet_root_of(n.get("cwd"))
            if env_root is not None:
                if not record_hook_env(env_root, host, event, started):
                    record_failed = f"could not save {_hook_env_path(env_root)}"
            elif event == "guard" and _hook_role() == "director" and n.get("cwd"):
                if not record_director_folder(Path(str(n["cwd"]))):
                    record_failed = f"could not save {_director_folders_path()}"
        except Exception as e:
            record_failed = f"{type(e).__name__}: {e}"
        block, msg = {"stop": decide_stop, "prompt": decide_prompt, "guard": decide_guard}[event](n)
        if record_failed:  # the decision above still stands; the failure is recorded and said, never swallowed
            note = _hook_record_failed(n, host, event, record_failed)
            msg = f"{msg}\n{note}" if msg else note
        rc = emit_hook(host, event, block, msg)
        clean_root = _ratchet_root_of(n.get("cwd"))
        if clean_root is not None and not record_failed:
            _record_hook_run(clean_root, host, event, crashed=False, started=started)  # a clean run resolves this hook's crash
        return rc
    except (Exception, SystemExit) as e:  # guard/prompt fail OPEN — never brick the host session
        root, saved = None, True
        try:
            root = find_root(Path(data.get("cwd") or os.getcwd()))
            if (root / RATCHET_DIR).is_dir():
                saved = False
                saved = (_record_hook_run(root, host, event, crashed=True)  # first: an unwritable log must not hide it
                         or _record_unsaved_crash(root, host, event))  # durable fallback: unresolved, whatever its age
                _append_record(root / RATCHET_DIR / "hook-errors.log", f"{now()} {host} {event}: {type(e).__name__}: {e}\n")
        except Exception:
            pass
        if event == "stop" and root is not None and (root / RATCHET_DIR / "active.json").exists():
            try:  # stop fails CLOSED (a few times) — a corrupted state file must not end the loop quietly
                block, msg = _stop_error(root, f"Ratchet could not read its run state ({type(e).__name__}: {e}).")
                return emit_hook(host, event, block, msg)
            except Exception:
                pass
        if not saved:  # say it plainly where the host shows hook output; never block (fail open)
            try:
                return emit_hook(host, event, False, (
                    f"RATCHET: the {event} hook failed ({type(e).__name__}) and Ratchet could not save that failure "
                    f"(it cannot write to {RATCHET_DIR}/{HOOK_CRASHES}/), so its checks are not enforced. Make that "
                    "folder writable and run the doctor command."))
            except Exception:
                pass
        return 0


# ---- installing the hooks into each host ------------------------------------------------

def _hook_cmd(host, event):
    return f'python3 "{Path(__file__).resolve()}" hook {event} --host {host}'


def hook_config_path(host: str, scope: str, root: Path) -> Path:
    home = Path.home()
    return {
        ("claude", "project"): root / ".claude" / "settings.json",
        ("claude", "user"): home / ".claude" / "settings.json",
        ("codex", "project"): root / ".codex" / "hooks.json",
        ("codex", "user"): Path(os.environ.get("CODEX_HOME", home / ".codex")) / "hooks.json",
        ("gemini", "project"): root / ".gemini" / "settings.json",
        ("gemini", "user"): home / ".gemini" / "settings.json",
        ("copilot", "project"): root / ".github" / "hooks" / "ratchet.json",
        ("copilot", "user"): Path(os.environ.get("COPILOT_HOME", home / ".copilot")) / "hooks" / "ratchet.json",
    }[(host, scope)]


def _strip_ratchet(entries: list) -> list:
    def is_ours(e):
        return "ratchet.py" in json.dumps(e)
    out = []
    for e in entries or []:
        if isinstance(e, dict) and isinstance(e.get("hooks"), list):
            e = dict(e, hooks=[h for h in e["hooks"] if not is_ours(h)])
            if e["hooks"]:
                out.append(e)
        elif not is_ours(e):
            out.append(e)
    return out


def build_hook_config(host: str, existing: dict | None) -> dict:
    cfg = json.loads(json.dumps(existing or {}))
    stop_ev, prompt_ev, guard_ev = HOOK_EVENTS[host]
    if host == "copilot":
        cfg.setdefault("version", 1)
        hooks = cfg.setdefault("hooks", {})
        for ev, kind in ((stop_ev, "stop"), (prompt_ev, "prompt"), (guard_ev, "guard")):
            cmd = _hook_cmd(host, kind)
            hooks[ev] = _strip_ratchet(hooks.get(ev)) + [{
                "type": "command", "bash": cmd, "powershell": cmd.replace("python3 ", "python ", 1),
                "timeoutSec": 30}]
        return cfg
    hooks = cfg.setdefault("hooks", {})
    timeout = 30000 if host == "gemini" else 30  # Gemini counts milliseconds
    for ev, kind in ((stop_ev, "stop"), (prompt_ev, "prompt"), (guard_ev, "guard")):
        entry = {"hooks": [{"type": "command", "command": _hook_cmd(host, kind), "timeout": timeout}]}
        if host == "gemini":
            entry["hooks"][0]["name"] = f"ratchet-{kind}"
            if kind == "guard":
                entry["matcher"] = ".*"
        elif kind == "guard" and host == "claude":
            entry["matcher"] = ".*"
        hooks[ev] = _strip_ratchet(hooks.get(ev)) + [entry]
    return cfg


def cmd_install_hooks(args):
    root = find_root()
    hosts = HOOK_HOSTS if args.host == "all" else [
        VENDOR_TO_HOST.get(detect_host({}), "claude") if args.host == "auto" else args.host]
    for host in hosts:
        path = hook_config_path(host, args.scope, root)
        existing = read_json(path, {}) or {}
        if args.uninstall:
            new = json.loads(json.dumps(existing))
            for ev in list((new.get("hooks") or {}).keys()):
                new["hooks"][ev] = _strip_ratchet(new["hooks"][ev])
                if not new["hooks"][ev]:
                    del new["hooks"][ev]
        else:
            new = build_hook_config(host, existing)
        if args.dry_run:
            print(f"# {host}: {path}\n{json.dumps(new, indent=2)}")
            continue
        if host == "copilot" and args.uninstall and not (new.get("hooks")):
            if path.exists():
                path.unlink()
            ok(f"{host}: removed {path}")
            continue
        write_json(path, new)
        ok(f"{host}: {'removed Ratchet hooks from' if args.uninstall else 'hooks written to'} {path}")
        if host == "codex" and not args.uninstall:
            for toml in (root / ".codex" / "config.toml", Path.home() / ".codex" / "config.toml"):
                if toml.exists() and re.search(r"^\s*hooks\s*=\s*false", toml.read_text(), re.M):
                    print(f"  ! {toml} sets hooks = false — Codex will ignore these hooks until you remove it.")


def cmd_doctor(args):
    root = find_root()
    if getattr(args, "json", False):
        print(json.dumps({"hooks": hooks_status(root, _safe_config(root))}, indent=2))
        return
    cfg = _safe_config(root)
    hs = hooks_status(root, cfg)
    print(f"Host running this command: {hs['host']} (provider {detect_host(cfg)})")
    for h in HOOK_HOSTS:
        found = [f"{scope}: {p}" for scope, p in installed_hook_files(h, root)]
        if h in ("claude", "codex"):
            found.append("(or installed as a plugin — hooks/hooks.json)")
        print(f"  {h:<8} " + ("; ".join(found) if found else "not installed"))
    hb = read_heartbeat(root)
    hb_file = root / RATCHET_DIR / "hook-heartbeat.json"
    try:
        hb_raw = hb_file.read_text(errors="ignore").strip() if hb_file.exists() else ""
    except OSError:
        hb_raw = "?"  # directory / permission problem: report as unreadable below
    if not hb and hb_raw not in ("", "{}"):
        print("  hook-heartbeat.json unreadable (corrupt or not a JSON object) - delete it; the next hook run rewrites it.")
    elif hb:
        now = _dt.datetime.now(_dt.timezone.utc)
        for ev in ("guard", "prompt", "stop"):
            e = hb.get(ev)
            if isinstance(e, dict) and isinstance(e.get("at"), str) and isinstance(e.get("host"), str):
                t = _parse_iso(e["at"])
                fut = "  (future-dated: clock skew or bad entry; not counted as fresh)" if t and t > now + _dt.timedelta(minutes=5) else ""
                print(f"  last {ev:<6} hook: {e['at'][:19]}Z via {e['host']}{fut}")
            elif ev in hb:
                print(f"  last {ev:<6} hook: unreadable entry in hook-heartbeat.json")
            else:
                print(f"  last {ev:<6} hook: never fired in this project")
    else:
        print("  No Ratchet hook has fired in this project yet. Until one does, the gates are only advice:"
              " offer to set up the hooks for the user's agent CLI (ask first; a restart is needed afterwards).")
    if not _enforcement_active(root, cfg):
        print("  For the agent: offer to set up the hooks for the user's agent CLI (ask first; a restart is needed "
              "afterwards). Do not ask the user to run commands.")
    recent = hook_errors_view(root, hs["host"])
    if recent:
        broken = {h: crashed_hook_events(root, h) for h in {e["host"] for e in recent}}  # judged per host
        print("  recent hook errors, last 24 hours, newest first (hooks failed open):")
        for e in recent:
            still = "  (still broken: no clean run since)" if e["event"] in broken[e["host"]] else ""
            print(f"    {e['at'][:19]}Z {e['host']} {e['event']}: {e['message']}{still}")
    shown = {(e["host"], e["event"]) for e in recent}
    for e in unresolved_hook_crashes(root, hs["host"]):
        if (e["host"], e["event"]) not in shown:
            print(f"  {e['host']} {e['event']} hook crashed at {e['at'][:19]}Z and has not run cleanly since "
                  "(not among the recent errors listed); the next clean run of that hook on that host clears this.")
    problem = hook_crash_state_problem(root)
    if problem:
        print(f"  hook crash records: {problem}. Until it is fixed every hook counts as not working; fix its "
              "permissions (it must be readable and writable), or remove it if it is not a folder of crash markers.")
    elif any(((read_hook_crashes(root).get(h) or {}).get(ev) or {}).get("unresolved")
             for h, ev, _ in _unsaved_crashes(root) or []):
        print(f"  some hook crashes could not be saved in {RATCHET_DIR}/{HOOK_CRASHES}/ and were kept in "
              f"{RATCHET_DIR}/{HOOK_CRASHES_UNSAVED}; make that folder writable. Each clears on that hook's next "
              "clean run.")
    for rp, repair in ((_markers_path(), _active_runs_repair), (_enrolled_path(), _enrolled_repair)):
        _rd, rwhy = _read_record(rp)
        if rwhy is not None:  # per-user records that hold restrictions: never written over (_restriction_record_damaged)
            print(f"  {rp} can't be read ({rwhy}). It holds restrictions for other projects or lanes too, so Ratchet "
                  f"won't write over it. To repair it: {repair(rp)}.")


def _safe_config(root: Path) -> dict:
    """Config for read-only diagnostics: a corrupt config.json (read_json dies) counts as empty."""
    _cfg, why = _read_record(root / RATCHET_DIR / "config.json", kind=None)  # quiet pre-check: read_json die()s
    if why is not None:
        return {}
    try:
        return Ctx(root).config()
    except (Exception, SystemExit):
        return {}


def installed_hook_files(host: str, root: Path) -> list:
    """[(scope, path)] of config files that already contain Ratchet hooks for `host`. Single definition of 'installed'."""
    out = []
    if host not in HOOK_HOSTS:
        return out
    for scope in ("project", "user"):
        try:
            p = hook_config_path(host, scope, root)
            if p.exists() and "ratchet.py" in p.read_text(errors="ignore"):
                out.append((scope, p))
        except OSError:
            pass
    return out


def read_heartbeat(root: Path) -> dict:
    """Tolerant read: a corrupt or odd heartbeat file counts as empty."""
    hb, _why = _read_record(root / RATCHET_DIR / "hook-heartbeat.json")
    return hb or {}


def _parse_iso(v):
    try:
        t = _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)
    except Exception:
        return None


HOOK_ERROR_RE = re.compile(r"^(\S+) (\S+) (\w+): (.*)$")  # run_hook's line: "<iso time> <host> <event>: <detail>"
HOOK_ERRORS_SHOWN = 3


def read_hook_errors(root: Path, host: str = "unknown", hours: int = 24) -> list:
    """Hook crashes from .ratchet/hook-errors.log in the last `hours` (not more than 5 minutes in the future), for
    `host` (every host when 'unknown'), newest first: [{event, host, at, message}]. Tolerant: a missing, unreadable
    or garbage file, or any line that is not run_hook's format, counts as no error."""
    p = root / RATCHET_DIR / "hook-errors.log"
    try:
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            raw = f.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if size > 65536 and lines:
        lines = lines[1:]  # the first line may be cut in half by the seek
    now_t = _dt.datetime.now(_dt.timezone.utc)
    cutoff, latest = now_t - _dt.timedelta(hours=hours), now_t + _dt.timedelta(minutes=5)
    out = []
    for line in lines:
        m = HOOK_ERROR_RE.match(line.strip())
        if not m:
            continue
        at, h, ev, msg = m.groups()
        t = _parse_iso(at) if "T" in at else None
        if not t or not (cutoff < t <= latest) or (host != "unknown" and h != host):
            continue
        out.append({"event": ev, "host": h, "at": at, "message": msg[:300], "_t": t})
    out.sort(key=lambda e: e["_t"], reverse=True)
    for e in out:
        e.pop("_t")
    return out


def hook_errors_view(root: Path, host: str) -> list:
    """The newest few recent hook errors, as carried by `next` and `status --json` (key `hook_errors`)."""
    return read_hook_errors(root, host)[:HOOK_ERRORS_SHOWN]


HOOK_CRASHES = "hook-crashes"  # a folder of empty marker files: one per recorded crash or clean run of a hook
HOOK_CRASHES_UNSAVED = "hook-crashes-unsaved.log"  # fallback, one line per crash whose marker could not be saved
_UNSAVED_RE = re.compile(r"^(\S+) ([A-Za-z0-9_-]+) (guard|stop|prompt)$")
_HOOK_MARK_RE = re.compile(r"^([A-Za-z0-9_-]+)\.(guard|stop|prompt)\.(crash|clean)\.(\d+)\.[A-Za-z0-9_-]+$")
_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def _iso_us(v) -> int:
    """An ISO time as whole microseconds since 1970 (exact; 0 when unreadable)."""
    t = _parse_iso(v)
    return (t - _EPOCH) // _dt.timedelta(microseconds=1) if t else 0


def _hook_marks(root: Path):
    """[(host, event, kind, microseconds, path)] for every marker in .ratchet/hook-crashes/; other names are ignored.
    The time lives in the name, so reading never opens a file another process may be creating or deleting.
    A missing folder means no crash was ever recorded ([]); a folder that cannot be listed gives None."""
    folder = root / RATCHET_DIR / HOOK_CRASHES
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []
    except OSError:
        return None
    out = []
    for n in names:
        m = _HOOK_MARK_RE.match(n)
        if m:
            out.append((m.group(1), m.group(2), m.group(3), int(m.group(4)), folder / n))
    return out


def _unsaved_crashes(root: Path):
    """[(host, event, microseconds)] from .ratchet/hook-crashes-unsaved.log: crashes whose marker could not be saved
    in hook-crashes/. [] when the file is missing; None when it exists but cannot be read."""
    try:
        text = (root / RATCHET_DIR / HOOK_CRASHES_UNSAVED).read_text(errors="replace")
    except FileNotFoundError:
        return []
    except OSError:
        return None
    out = []
    for line in text.splitlines():
        m = _UNSAVED_RE.match(line.strip())
        if m and _iso_us(m.group(1)):
            out.append((m.group(2), m.group(3), _iso_us(m.group(1))))
    return out


def _record_unsaved_crash(root: Path, host: str, event: str) -> bool:
    """Fallback when run_hook cannot save a crash marker: append one line (no read-modify-write), so the crash still
    counts as unresolved after its log line ages out. Returns False when this fails too: run_hook then tells the
    host, and readers count every hook as broken while the crash store refuses writes (_crash_store_unwritable)."""
    try:
        _append_record(root / RATCHET_DIR / HOOK_CRASHES_UNSAVED, f"{now()} {_mark_host(host)} {event}\n")
        return True
    except Exception:
        return False


def hook_crash_state_unreadable(root: Path):
    """Why the recorded crash state cannot be read (None when it can)."""
    if _hook_marks(root) is None:
        return f"{RATCHET_DIR}/{HOOK_CRASHES}/ cannot be read (not a folder, or no permission)"
    if _unsaved_crashes(root) is None:
        return f"{RATCHET_DIR}/{HOOK_CRASHES_UNSAVED} cannot be read"
    return None


def _crash_store_unwritable(root: Path):
    """Why Ratchet could not save a hook crash right now (None when it can), proven with a real write: create and
    delete a probe file where run_hook saves crash markers (in .ratchet/ itself, where that folder would be made,
    before the first crash). The probe name never matches a marker, so readers ignore it."""
    rdir = root / RATCHET_DIR
    if not rdir.is_dir():
        return None  # no Ratchet state here: nothing to record
    folder = rdir / HOOK_CRASHES
    where = folder if folder.is_dir() else rdir
    probe = where / f".write-probe.{os.getpid()}-{secrets.token_hex(4)}"
    try:
        with open(probe, "x"):
            pass
    except OSError:
        return f"{RATCHET_DIR}/{HOOK_CRASHES}/ cannot be written (no permission, read-only or full disk)"
    try:
        probe.unlink()
    except OSError:
        pass
    return None


def hook_crash_state_problem(root: Path):
    """Why Ratchet cannot trust its crash records (None when it can). Fail closed: a hook counts as working only while
    Ratchet can both read and save its crash state, so while either fails every hook counts as broken: the records
    cannot be read, or they cannot be written right now (a crash would be lost). Known limit (schemas.md section 10):
    a crash at a moment when neither crash store can be written leaves no lasting record, so it counts as broken
    only while the writes are refused; no shared file is ever rewritten to remember it."""
    return hook_crash_state_unreadable(root) or _crash_store_unwritable(root)


def read_hook_crashes(root: Path) -> dict:
    """{host: {event: {crashed_at?, clean_at?, unresolved}}} from the markers run_hook leaves in
    .ratchet/hook-crashes/ (plus the fallback hook-crashes-unsaved.log): the newest crash and the newest clean run of
    each hook on each host. A crash is unresolved until a clean run that started after it. Missing or unreadable
    files count as empty here; hook_crash_state_problem reports unreadable ones, and they make every hook broken."""
    newest: dict = {}
    marks = [m[:4] for m in _hook_marks(root) or []]
    marks += [(h, ev, "crash", us) for h, ev, us in _unsaved_crashes(root) or []]
    for h, ev, kind, us in marks:
        rec = newest.setdefault(h, {}).setdefault(ev, {})
        rec[kind] = max(rec.get(kind, 0), us)

    def iso(us):
        return (_EPOCH + _dt.timedelta(microseconds=us)).isoformat(timespec="microseconds")
    out: dict = {}
    for h, evs in newest.items():
        for ev, rec in evs.items():
            crash, clean = rec.get("crash"), rec.get("clean")
            r = {"unresolved": bool(crash) and not (clean and clean > crash)}
            if crash:
                r["crashed_at"] = iso(crash)
            if clean:
                r["clean_at"] = iso(clean)
            out.setdefault(h, {})[ev] = r
    return out


def _mark_host(host) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(host)) or "unknown"


def _record_hook_run(root: Path, host: str, event: str, crashed: bool, started: str | None = None) -> bool:
    """Crash state per (host, event), kept apart from the 24-hour log window: a crash stays unresolved until that
    same hook, on that same host, runs cleanly in a call that started after it. No file is ever read, changed and
    written back, so concurrent hooks cannot erase each other's records: each run only creates its own uniquely
    named empty marker (exclusive create, the same on every OS), then deletes markers it supersedes, which never
    changes the outcome: a crash removes that hook's older crash markers; a clean run removes that hook's older
    crash and clean markers. `started` is when the clean run began, so a crash recorded while it ran stays
    unresolved. A clean run writes only when it resolves a recorded crash (the hot path stays read-only otherwise).
    Returns False when the record could not be saved (run_hook then falls back to hook-crashes-unsaved.log)."""
    try:
        h = _mark_host(host)
        at = _iso_us(started or now())
        if not at:
            return False
        if not crashed:
            rec = (read_hook_crashes(root).get(h) or {}).get(event) or {}
            if not rec.get("unresolved") or _iso_us(rec.get("crashed_at")) >= at:
                return True  # nothing to resolve, or that crash came after this run started
        kind = "crash" if crashed else "clean"
        folder = root / RATCHET_DIR / HOOK_CRASHES
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / f"{h}.{event}.{kind}.{at}.{os.getpid()}-{secrets.token_hex(4)}", "x"):
            pass
        for mh, mev, mkind, us, path in _hook_marks(root) or []:
            if (mh, mev) == (h, event) and us < at and (mkind == "crash" or not crashed):
                try:
                    path.unlink()
                except OSError:
                    pass  # another run removed it first, or (Windows) it is busy: a stale marker changes nothing
        return True
    except Exception:
        return False


def _ratchet_root_of(cwd) -> Path | None:
    """The nearest ancestor of `cwd` holding .ratchet (no git call: this runs on every clean hook)."""
    try:
        p = Path(str(cwd or os.getcwd())).resolve()
        return next((c for c in [p, *p.parents] if (c / RATCHET_DIR).is_dir()), None)
    except Exception:
        return None


def unresolved_hook_crashes(root: Path, host: str, crashes=None) -> list:
    """Crashes run_hook recorded that the same hook on the same host has not yet run cleanly after, whatever their
    age, for `host` (every host when 'unknown'), newest first: [{event, host, at}]."""
    crashes = read_hook_crashes(root) if crashes is None else crashes
    out = [{"event": ev, "host": h, "at": str(r.get("crashed_at") or "")}
           for h, evs in crashes.items() if host in ("unknown", h)
           for ev, r in evs.items() if r.get("unresolved") is True]
    old = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
    return sorted(out, key=lambda e: _parse_iso(e["at"]) or old, reverse=True)


def broken_hooks(root: Path, host: str, hb=None, errors=None, crashes=None) -> list:
    """Hooks that broke on their last run, judged per (host, event) so one host never clears another's crash:
    (a) a crash recorded by run_hook with no clean run of that hook on that host since, however old; or (b) a recent
    logged crash with no later clean run (hook-crashes/) or heartbeat from that same host (the heartbeat is
    written before the decision, so a crash in the same call is logged after it). Newest first: [{event, host, at}],
    `at` being its latest known crash."""
    hb = read_heartbeat(root) if hb is None else hb
    errors = read_hook_errors(root, host) if errors is None else errors
    crashes = read_hook_crashes(root) if crashes is None else crashes
    old = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
    out = {(e["host"], e["event"]): e for e in unresolved_hook_crashes(root, host, crashes)}
    done = set()
    for e in errors:  # newest first, so the first error per (host, event) is its latest
        h, ev = e["host"], e["event"]
        if (h, ev) in done:
            continue
        done.add((h, ev))
        et = _parse_iso(e["at"])
        ct = _parse_iso(((crashes.get(h) or {}).get(ev) or {}).get("clean_at"))
        if ct and et and ct > et:
            continue  # that hook ran cleanly on that host after this crash
        beat = hb.get(ev)
        bt = _parse_iso(beat.get("at")) if isinstance(beat, dict) and beat.get("host") == h else None
        if not bt or (et and et >= bt):
            prev = out.get((h, ev))
            if not prev or (et or old) > (_parse_iso(prev["at"]) or old):
                out[(h, ev)] = {"event": ev, "host": h, "at": e["at"]}
    return sorted(out.values(), key=lambda e: _parse_iso(e["at"]) or old, reverse=True)


def crashed_hook_events(root: Path, host: str, hb=None, errors=None, crashes=None) -> set:
    """Events whose hook broke on its last run (see broken_hooks). Fail safe: while the recorded crash state cannot
    be read, every hook counts as broken."""
    if hook_crash_state_problem(root):
        return set(HOOK_EVENT_PLAIN)
    return {b["event"] for b in broken_hooks(root, host, hb, errors, crashes)}


def hooks_status(root: Path, cfg=None) -> dict:
    """Read-only: {host, installed, heartbeat_seen, fresh, missing} for the CLI running this command."""
    # Unknown host (e.g. unrecognised host_provider): we cannot tell which CLI this is, so a heartbeat from ANY
    # host counts as evidence that hooks run in this project. Documented; doctor prints the host as "unknown".
    host = VENDOR_TO_HOST.get(detect_host(cfg or {}), "unknown")
    installed = bool(installed_hook_files(host, root))
    hb = read_heartbeat(root)
    entries = [e for e in hb.values()
               if isinstance(e, dict) and (host == "unknown" or e.get("host") == host)]
    now = _dt.datetime.now(_dt.timezone.utc)
    cutoff, latest = now - _dt.timedelta(hours=24), now + _dt.timedelta(minutes=5)  # future-dated is not fresh
    fresh = False
    for e in entries:
        t = _parse_iso(e.get("at"))
        if t and cutoff < t <= latest:
            fresh = True

    def _fresh(ev):
        e = hb.get(ev)
        if not (isinstance(e, dict) and (host == "unknown" or e.get("host") == host)):
            return False
        t = _parse_iso(e.get("at"))
        return bool(t and cutoff < t <= latest)
    # enforcing: the guard or stop hook fired recently (these are what enforce the gates) and neither broke on its
    # last run; prompt_hook: the prompt hook fired recently and did not break (it only records the user's replies)
    crashed = crashed_hook_events(root, host, hb)
    enforcing = (_fresh("guard") or _fresh("stop")) and not (crashed & {"guard", "stop"})
    return {"host": host, "installed": installed, "heartbeat_seen": bool(entries), "fresh": fresh,
            "enforcing": enforcing, "prompt_hook": _fresh("prompt") and "prompt" not in crashed,
            "missing": not installed and not enforcing}


def hooks_warning(ctx, hs=None) -> str | None:
    """Used by `next`/status: gates without hooks are just advice. Phrased for the agent, not the user."""
    hs = hs or hooks_status(ctx.root, ctx.config())
    if hs.get("waived"):
        return None  # the user already chose to build without hooks; do not re-offer
    if hs.get("enforcing"):
        return None  # only guard/stop heartbeats count: the prompt hook alone enforces nothing
    problem = hook_crash_state_problem(ctx.root)
    if problem and hook_crash_state_unreadable(ctx.root):
        return (f"Ratchet cannot read its hook crash records ({problem}), so it counts the safety hooks as not "
                "working and the gates as not enforced. Run the doctor command, fix or remove that file, and tell "
                "the user in one plain sentence.")
    if problem:
        return (f"Ratchet cannot save its hook crash records ({problem}), so a hook crash could go unrecorded and it "
                "counts the safety hooks as not working and the gates as not enforced. Run the doctor command, make "
                "that folder writable, and tell the user in one plain sentence.")
    if crashed_hook_events(ctx.root, hs.get("host", "unknown")) & {"guard", "stop"}:
        return ("A Ratchet safety hook broke on its last run and let the action through (see hook_errors), so the "
                "gates are not enforced right now. Run the doctor command to see the error, fix the cause, and tell "
                "the user in one plain sentence. The next clean hook run restores enforcement.")
    if hs["installed"]:
        return ("Ratchet hooks are installed for this agent CLI but none has fired recently. Tell the user the "
                "agent CLI probably needs a restart (and, for Codex, hooks enabled) so the gates are enforced.")
    return ("No Ratchet hook has fired in this project yet, so the gates are only advice. Offer to set up the "
            "hooks for this agent CLI (the install-hooks command), ask the user first, and tell them a restart "
            "is needed afterwards. If they would rather not, ask the hooks-waive question, tell them in one plain "
            "sentence that the gates would then only be advice, and after their clear yes record it with the "
            "hooks-waive command. The doctor command shows the details.")



# --------------------------------------------------------------------------- main

# Every command that changes a run holds one exclusive lock file, state.lock in the Ratchet folder, for its WHOLE run:
# from before its first read of state.json to after its last write, its test runs, checks, regression suite and
# provider probes included. So two commands never interleave and none can overwrite what another wrote meanwhile (a
# double submit, an approval made during a test run): the second one waits for a short command to finish
# (STATE_LOCK_WAIT_SECONDS), then reads what it wrote; behind a long one (LONG_LOCKED_CMDS) it refuses after a moment
# (STATE_LOCK_BUSY_WAIT_SECONDS) with "Ratchet is busy running <command>; try again when it finishes" and changes
# nothing. The lock file names the command holding it. The hooks never wait on it and never write state.json.
# A lock not refreshed for STATE_LOCK_STALE_SECONDS (more than the longest test run, test_timeout_sec, plus a margin)
# is left over from a crashed command and is removed; a live command refreshes its lock while it runs.
STATE_LOCKED_CMDS = ("init", "plan-submit", "approve-plan", "tests-written", "red", "implemented", "behavior",
                     "ui-record", "adv-record", "complete", "human-approve", "feedback", "plan-extend", "pause",
                     "resume", "ask", "accept-state", "hooks-waive", "cancel", "set-review", "choice", "ui-respec",
                     "approve-changes", "guardrail", "reviewers", "repeats", "spec", "wait", "accept-finding",
                     "check-in", "triage")
LONG_LOCKED_CMDS = ("red", "behavior", "complete", "reviewers")  # tests, checks, a regression suite, provider probes
# The commands that may take a person's RATCHET_HUMAN_OVERRIDE code: asked BEFORE the lock, so nothing waits on them.
OVERRIDE_CMDS = ("approve-plan", "human-approve", "approve-changes", "cancel", "set-review", "resume", "hooks-waive",
                 "ui-respec", "choice", "feedback", "accept-state", "init")
STATE_LOCK_WAIT_SECONDS = 30
STATE_LOCK_BUSY_WAIT_SECONDS = 2
STATE_LOCK_STALE_MARGIN_SECONDS = 300
STATE_LOCK_STALE_SECONDS = DEFAULT_CONFIG["test_timeout_sec"] + STATE_LOCK_STALE_MARGIN_SECONDS
STATE_LOCK_REFRESH_SECONDS = 30


def _changes_state(args) -> bool:
    """Whether this command may change the run (and so runs under the state lock)."""
    if args.cmd == "repeats":
        return bool(getattr(args, "offered", None))
    if args.cmd == "spec":
        return getattr(args, "spec_cmd", None) in ("import", "keep")
    return args.cmd in STATE_LOCKED_CMDS


def _lock_stale_seconds(root: Path) -> float:
    """A live command can hold the lock through a whole test run (test_timeout_sec): only a lock older than that,
    plus a margin, is stale."""
    cfg, _why = _read_record(root / RATCHET_DIR / "config.json")  # a damaged config: the default stale age
    try:
        t = float((cfg or {}).get("test_timeout_sec") or 0)
    except (TypeError, ValueError):
        t = 0
    return max(STATE_LOCK_STALE_SECONDS, t + STATE_LOCK_STALE_MARGIN_SECONDS)


def _lock_pause():
    time.sleep(0.05)


class _StateLock(_BreakerLock):
    """O_CREAT|O_EXCL lock file, state.lock in the Ratchet folder (works the same on POSIX and Windows), around one
    command. It holds the process id, a random token and the command's name. Every failure to take it (another
    command holds it, the folder can't be written, something other than a lock file sits at that path) is retried
    after a pause until the wait runs out, then refused with a plain message; behind a long command the wait is cut
    short. The breaker protocol is _BreakerLock's, shared with the per-user locks: the lock file is only ever removed
    (a stale one, or an owner releasing its own) while holding a second O_EXCL file, state.lock.break, so the file
    judged stale is the file removed: two waiters can't both clear it, and none can remove a newer, live lock; a
    breaker file left by a process that died is cleared after BREAKER_STALE seconds. With `refresh`, the holder
    touches its lock while it runs, so a long, live command is never judged stale. With `create_dir` (init: the
    first command of a project), the Ratchet folder is made first, so two first inits are serialised like every
    other command instead of both running unlocked."""

    def __init__(self, root: Path, wait: float | None = None, cmd: str | None = None, refresh: bool = False,
                 create_dir: bool = False):
        super().__init__(root / RATCHET_DIR / "state.lock",
                         f"{os.getpid()} {secrets.token_hex(8)}" + (f" {cmd}" if cmd else ""))
        self.root = root
        self.create_dir = create_dir
        self.wait = wait
        self.refresh = refresh
        self._beat = None
        self.holder = None  # the command holding the lock at the last try (None: not known)

    def _stale_after(self) -> float:
        return _lock_stale_seconds(self.root)

    def _pause(self):
        _lock_pause()

    def _cant(self, e: OSError) -> str:
        return (f"Ratchet could not use its lock file {self.path} ({type(e).__name__}: {e.strerror or e}), so nothing "
                f"was done. Check that {self.path.parent} is a folder you can write to, then run this command again.")

    def _holder_cmd(self) -> str | None:
        """The command named in the lock file (None for a lock that names none, or no regular lock file)."""
        try:
            if not stat.S_ISREG(os.lstat(self.path).st_mode):
                return None
            parts = self.path.read_text(encoding="utf-8", errors="replace").split()
        except OSError:
            return None
        return " ".join(parts[2:]) if len(parts) > 2 else None

    def live_holder(self) -> str | None:
        """Who holds a live (not stale) lock right now, without waiting or changing anything; None when no command
        does."""
        try:
            st = os.lstat(self.path)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode) or self._is_stale(st):
            return None
        return self._holder_cmd() or "another Ratchet command"

    def _why_busy(self) -> str:
        """Why the lock file exists. A stale lock is cleared first (under the breaker file); the caller retries."""
        self.holder = self._holder_cmd()
        stale_after = _lock_stale_seconds(self.root)
        busy = (f"Ratchet is busy running {self.holder or 'another command'}; try again when it finishes. Nothing was "
                f"done: another Ratchet command is still changing this run (lock file {self.path}). If no Ratchet "
                f"command is running, the lock clears itself {int(stale_after // 60)} minutes after its command last "
                f"refreshed it, or remove {self.path}.")
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            return busy  # released just now: the next try takes it
        except OSError as e:
            return self._cant(e)
        if not stat.S_ISREG(st.st_mode):
            return (f"Ratchet's lock file {self.path} is not a file Ratchet made (a folder or a link sits there), so "
                    "nothing was done. Remove it, then run this command again.")
        if not self._is_stale(st):
            return busy
        try:  # judged again while holding the breaker file: nobody else can remove it meanwhile
            self._clear_stale()
        except OSError as e:
            return self._cant(e)
        return (f"A lock file left by a Ratchet command that stopped ({self.path}) could not be cleared, so nothing "
                f"was done. If no other Ratchet command is running, remove {self.path} and {self.breaker} (if it "
                "is there), then run this command again.")

    def acquire(self) -> str | None:
        """None once the lock is held (or there is no Ratchet folder, so no state to protect); otherwise, once the
        wait runs out, the reason it could not be taken. Never raises for a file-system error."""
        if self.create_dir:
            try:
                self.path.parent.mkdir(exist_ok=True)
            except OSError as e:
                return self._cant(e)
        if not self.path.parent.is_dir():
            return None  # no Ratchet folder: there is no state to protect (the command says so itself)
        start = time.monotonic()
        deadline = start + (STATE_LOCK_WAIT_SECONDS if self.wait is None else self.wait)
        while True:
            try:
                self._create(self.path)
                self.held = True
                self._start_refresh()
                return None
            except FileExistsError:
                why = self._why_busy()
                if self.holder in LONG_LOCKED_CMDS:  # it may run for many minutes: refuse rather than wait it out
                    deadline = min(deadline, start + STATE_LOCK_BUSY_WAIT_SECONDS)
            except OSError as e:
                why = self._cant(e)
            if time.monotonic() >= deadline:
                return why
            _lock_pause()

    def _start_refresh(self):
        if not self.refresh:
            return
        import threading
        done = threading.Event()

        def beat():
            while not done.wait(STATE_LOCK_REFRESH_SECONDS):
                try:
                    if self.path.read_text(encoding="utf-8") == self.token:
                        os.utime(self.path, None)
                except OSError:
                    pass
        threading.Thread(target=beat, daemon=True).start()
        self._beat = done

    def release(self):
        """Remove the lock file if it is still this command's own, under the breaker file. When the breaker can't be
        had within a few seconds the lock is left in place (it goes stale and is cleared later): a lock that might
        not be ours is never removed."""
        if self._beat is not None:
            self._beat.set()
            self._beat = None
        if not self.held:
            return
        self.held = False
        self._release_own(5)

    def __enter__(self):
        why = self.acquire()
        if why:
            die(why)
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _may_use_override(args) -> bool:
    """Whether this command may ask for the RATCHET_HUMAN_OVERRIDE code (it reaches human_override): it is asked
    before the state lock is taken."""
    if os.environ.get("RATCHET_HUMAN_OVERRIDE") != "1" or args.cmd not in OVERRIDE_CMDS:
        return False
    if args.cmd == "init":  # only replacing a run that is still active asks for it
        return bool(getattr(args, "force", False)) and bool(Ctx(find_root()).active_slug())
    return not getattr(args, "director_token", None) and not (args.cmd == "resume" and not args.claim)


def main(argv=None):
    try:  # quiet exit when output is piped into head/grep
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass
    for stream in (sys.stdout, sys.stderr):  # ✓/✗ must not crash a cp1252 console
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="ratchet.py", description="Ratchet loop state engine")
    p.add_argument("--root", help="operate on the project/lane at this path (default: current folder)")
    sp = p.add_subparsers(dest="cmd", required=True)

    a = sp.add_parser("init"); a.add_argument("feature"); a.add_argument("--slug")
    a.add_argument("--human-review", choices=["end", "each", "none"]); a.add_argument("--force", action="store_true")
    a.add_argument("--branch", action="store_true", help="create and switch to branch ratchet/<slug>")
    a.set_defaults(fn=cmd_init)
    a = sp.add_parser("plan-submit"); a.add_argument("--open", action="store_true"); a.set_defaults(fn=cmd_plan_submit)
    a = sp.add_parser("approve-plan"); a.add_argument("--director-token"); a.set_defaults(fn=cmd_approve_plan)
    a = sp.add_parser("tests-written"); a.add_argument("cp"); a.set_defaults(fn=cmd_tests_written)
    a = sp.add_parser("red"); a.add_argument("cp"); a.add_argument("--allow-green-first", action="store_true")
    a.add_argument("--reason"); a.set_defaults(fn=cmd_red)
    a = sp.add_parser("implemented"); a.add_argument("cp"); a.set_defaults(fn=cmd_implemented)
    a = sp.add_parser("behavior"); a.add_argument("cp"); a.set_defaults(fn=cmd_behavior)
    a = sp.add_parser("ui-record"); a.add_argument("cp"); a.add_argument("--review", required=True)
    a.set_defaults(fn=cmd_ui_record)
    a = sp.add_parser("adv-record"); a.add_argument("cp"); a.add_argument("--reviews", nargs="+", required=True)
    a.set_defaults(fn=cmd_adv_record)
    a = sp.add_parser("complete"); a.add_argument("cp")
    a.set_defaults(fn=cmd_complete)
    a = sp.add_parser("human-approve"); a.add_argument("--director-token"); a.set_defaults(fn=cmd_human_approve)
    a = sp.add_parser("feedback"); a.add_argument("--text"); a.add_argument("--learning", action="append")
    a.add_argument("--director-token")
    a.set_defaults(fn=cmd_feedback)
    sp.add_parser("plan-extend").set_defaults(fn=cmd_plan_extend)
    a = sp.add_parser("learn"); a.add_argument("text"); a.add_argument("--source"); a.set_defaults(fn=cmd_learn)
    a = sp.add_parser("pause"); a.add_argument("--reason"); a.set_defaults(fn=cmd_pause)
    a = sp.add_parser("wait", help="end your turn while background reviewers or sub-agents run, without a blocked stop")
    a.add_argument("--for", dest="reason", required=True, help="what you are waiting on")
    a.add_argument("--minutes", type=float, help=f"how long (1 to {WAIT_MAX_MINUTES}; default {WAIT_DEFAULT_MINUTES})")
    a.add_argument("--session", help="optional cross-check: this chat's id; refused unless it is the chat Ratchet sees running the call (never proof on its own)")
    a.set_defaults(fn=cmd_wait)
    a = sp.add_parser("resume", help="resume a paused run; --claim moves the run to this chat (after `ask claim`)")
    a.add_argument("--claim", action="store_true"); a.set_defaults(fn=cmd_resume)
    a = sp.add_parser("ask", help="record and print the question you're about to ask the user (needed before approve-plan, "
                                  "human-approve, cancel, set-review, approve-changes, hooks-waive, resume --claim, "
                                  "ui-respec, accept-finding)")
    a.add_argument("action", choices=list(ASKABLE) + ["withdraw"])
    a.add_argument("mode", nargs="?", help="set-review: end | each | none; ui-respec/accept-finding: the checkpoint id")
    a.add_argument("ids", nargs="?", help="accept-finding: the finding ids to accept, comma-separated (e.g. F1,F2)")
    a.add_argument("--options", help="choice: the option letters, e.g. A,B,C")
    a.add_argument("--guardrail", action="append", metavar="LETTER=KIND",
                   help="choice: picking LETTER adds a guardrail step (an automatic check) for this kind of finding")
    a.add_argument("--locked", action="append", metavar="LETTER=FILE[,FILE]",
                   help="choice: picking LETTER approves changing these locked files (repeatable)")
    a.set_defaults(fn=cmd_ask)
    sp.add_parser("accept-state", help=argparse.SUPPRESS).set_defaults(fn=cmd_accept_state)
    a = sp.add_parser("accept-finding", help="record the open findings the user accepted (after `ask accept-finding` "
                                             "and their 'accept: <reason>' reply, or a director token)")
    a.add_argument("cp"); a.add_argument("--director-token")
    a.add_argument("--reason", help="director mode only: the director's reason (there is no chat reply to read one from)")
    a.set_defaults(fn=cmd_accept_finding)
    a = sp.add_parser("hooks-waive", help="record the user's choice to build without hooks (after `ask hooks-waive`)")
    a.add_argument("--director-token")
    a.set_defaults(fn=cmd_hooks_waive)
    a = sp.add_parser("cancel", help="abandon the active run (needs the user's confirmation)")
    a.add_argument("--reason"); a.add_argument("--director-token"); a.set_defaults(fn=cmd_cancel)
    a = sp.add_parser("set-review", help="change how often the user reviews (needs their confirmation)")
    a.add_argument("mode", choices=["end", "each", "none"]); a.add_argument("--director-token")
    a.set_defaults(fn=cmd_set_review)
    a = sp.add_parser("choice", help="after `ask choice`: record which option the user picked")
    a.set_defaults(fn=cmd_choice)
    a = sp.add_parser("ui-respec", help="after `ask ui-respec CPn` and the user's yes: allow one change to a frozen UI check")
    a.add_argument("cp"); a.add_argument("--director-token"); a.set_defaults(fn=cmd_ui_respec)
    a = sp.add_parser("approve-changes", help="accept a user-confirmed change to the approved plan/tests/rules")
    a.add_argument("--director-token"); a.set_defaults(fn=cmd_approve_changes)
    a = sp.add_parser("providers", help="list, test (--check/--discover) and register (--add) other agent CLIs")
    a.add_argument("--discover", action="store_true", help="find installed agent CLIs, test them, add what works")
    a.add_argument("--check", nargs="?", const="all", help="send a test request to one provider (or all)")
    a.add_argument("--add", metavar="ID"); a.add_argument("--preset"); a.add_argument("--command")
    a.add_argument("--stdin", action="store_true"); a.add_argument("--model"); a.add_argument("--company")
    a.add_argument("--for-risk", help="comma list of risks it may review (default all)")
    a.add_argument("--no-check", action="store_true"); a.add_argument("--remove", metavar="ID")
    a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_providers)
    a = sp.add_parser("models", help="which model each role's sub-agent uses on this host")
    a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_models)
    a = sp.add_parser("guardrail", help="after the user picks a guardrail option: add a step that builds an automatic check")
    a.add_argument("--kind", required=True); a.add_argument("--title", required=True)
    a.add_argument("--check-command", required=True,
                   help="the check, with {target} where the code to check goes, e.g. \"npx eslint {target}\"")
    a.add_argument("--bad-example", required=True, help="a fixture inside the project that contains the mistake")
    a.add_argument("--target", default=".", help="what the check runs on in the real project (default .)")
    a.add_argument("--test-command", required=True, help="command that runs the step's tests (red/green proof)")
    a.add_argument("--test-file", action="append", required=True, help="test file(s) the step's tests go in")
    a.add_argument("--variants-dir", help="high-risk kinds: where the disguised bad/ and look-alike good/ variants go")
    a.add_argument("--why", help="the mistake in one plain sentence (goes into learnings.md)")
    a.add_argument("--director-token"); a.set_defaults(fn=cmd_guardrail)
    a = sp.add_parser("occurrences", help="the real code where reviewers found a kind of mistake")
    a.add_argument("kind"); a.set_defaults(fn=cmd_occurrences)
    a = sp.add_parser("repeats", help="kinds of review finding seen twice or more, not yet guarded or asked about")
    a.add_argument("--json", action="store_true")
    a.add_argument("--offered", metavar="KIND", help="record that this kind was put to the user or director")
    a.set_defaults(fn=cmd_repeats)
    a = sp.add_parser("spec", help="build from an OpenSpec change, Spec Kit feature or BMAD story")
    ssp = a.add_subparsers(dest="spec_cmd", required=True)
    b = ssp.add_parser("detect", help="which spec tools this project uses and what is still open")
    b.add_argument("--json", action="store_true")
    b = ssp.add_parser("import", help="start this run's plan from spec items")
    b.add_argument("tool", help="openspec | speckit | bmad | auto"); b.add_argument("items", nargs="*")
    b.add_argument("--force", action="store_true", help="replace an existing checkpoints.json with the draft")
    b.add_argument("--json", action="store_true")
    b = ssp.add_parser("keep", help="the user's answer: keep a running spec of what was built (no spec tool), or not")
    b.add_argument("choice", choices=["on", "off"])
    b = ssp.add_parser("status", help="which spec scenarios are proven by passing tests")
    b.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_spec)
    a = sp.add_parser("deferred", help="findings recorded for later instead of blocking (minor, or no user_impact)")
    a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_deferred)
    sp.add_parser("next").set_defaults(fn=cmd_next)
    a = sp.add_parser("status"); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_status)
    sp.add_parser("plan-summary", help="plain per-step summary of the plan, to show the user before they approve"
                  ).set_defaults(fn=cmd_plan_summary)
    a = sp.add_parser("reviewers"); a.add_argument("cp"); a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_reviewers)
    a = sp.add_parser("check-in", help="print (or decide) the check-in opened when reviews drag")
    a.add_argument("cp"); a.add_argument("--decision", choices=["simplify", "accept", "stop"])
    a.add_argument("--why"); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_check_in)
    a = sp.add_parser("triage", help="record fix-or-rebut decisions for a blocked round's open findings")
    a.add_argument("cp"); a.add_argument("decisions", nargs="+", metavar="ref=fix|rebut:reason")
    a.set_defaults(fn=cmd_triage)
    a = sp.add_parser("reviewer-status"); a.add_argument("--reset", metavar="ID")
    a.add_argument("--mark", metavar="ID"); a.add_argument("--reason"); a.set_defaults(fn=cmd_reviewer_status)
    a = sp.add_parser("lanes"); a.add_argument("--scan", default="."); a.add_argument("--json", action="store_true")
    a.add_argument("--serve", action="store_true"); a.add_argument("--stop", action="store_true")
    a.add_argument("--open", action="store_true"); a.add_argument("--port", type=int)
    a.add_argument("--bind", default="127.0.0.1"); a.add_argument("--public-host")
    a.add_argument("--token", choices=["auto", "on", "none"], default=None)
    a.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    a.set_defaults(fn=cmd_lanes)
    a = sp.add_parser("director-key"); a.add_argument("--rotate", action="store_true"); a.set_defaults(fn=cmd_director_key)
    sp.add_parser("director-enroll").set_defaults(fn=cmd_director_enroll)
    a = sp.add_parser("director-sign"); a.add_argument("action", choices=["approve-plan", "human-approve", "feedback",
                                                                          "approve-changes", "cancel", "set-review",
                                                                          "hooks-waive", "ui-respec", "guardrail",
                                                                          "accept-finding"])
    a.add_argument("--kind", help="guardrail: the kind of finding it covers")
    a.set_defaults(fn=cmd_director_sign)
    sp.add_parser("fingerprint").set_defaults(fn=cmd_fingerprint)
    a = sp.add_parser("config"); a.add_argument("--set", nargs="*"); a.set_defaults(fn=cmd_config)
    a = sp.add_parser("view", help="print/open the viewer link; --serve starts a live server in the background")
    a.add_argument("--open", action="store_true"); a.add_argument("--serve", action="store_true")
    a.add_argument("--stop", action="store_true"); a.add_argument("--port", type=int)
    a.add_argument("--bind", default="127.0.0.1", help="0.0.0.0 to reach it from other machines (adds a token)")
    a.add_argument("--public-host", help="hostname to put in the URL (e.g. a Tailscale or LAN name)")
    a.add_argument("--token", choices=["auto", "on", "none"], default=None)
    a.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    a.set_defaults(fn=cmd_view)
    a = sp.add_parser("hook", help="entry point for agent-CLI hooks")
    a.add_argument("event", choices=["stop", "prompt", "guard"])
    a.add_argument("--host", default="auto", choices=["auto", *HOOK_HOSTS])
    a.set_defaults(fn=lambda a: sys.exit(run_hook(a.event, a.host)))
    for legacy, ev in (("hook-stop", "stop"), ("hook-prompt", "prompt"), ("hook-guard", "guard")):
        sp.add_parser(legacy).set_defaults(fn=lambda a, ev=ev: sys.exit(run_hook(ev, "auto")))
    a = sp.add_parser("install-hooks", help="write Ratchet's hooks into an agent CLI's config")
    a.add_argument("--host", default="auto", choices=["auto", "all", *HOOK_HOSTS])
    a.add_argument("--scope", default="project", choices=["project", "user"])
    a.add_argument("--dry-run", action="store_true"); a.add_argument("--uninstall", action="store_true")
    a.set_defaults(fn=cmd_install_hooks)
    a = sp.add_parser("doctor", help="which hosts have Ratchet hooks, and are they firing?")
    a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_doctor)

    args = p.parse_args(argv)
    global _ENGINE_ARGV, _STATE_LOCK_HELD
    _ENGINE_ARGV = list(sys.argv[1:] if argv is None else argv)
    if args.root:
        os.environ["RATCHET_ROOT"] = str(Path(args.root).resolve())
    global _HOOK_ENV_ADVISORY
    _HOOK_ENV_ADVISORY = False
    if args.cmd in RECOVERY_CMDS:  # reads or hook repair: run anyway and say plainly what is wrong
        why = None if _IN_HOOK else hook_env_problem(find_root())
        if why:
            _HOOK_ENV_ADVISORY = True
            why = why[len("Refused: "):] if why.startswith("Refused: ") else why
            print(f"RATCHET: {args.cmd} still runs (it only reads, or only repairs hooks), but Ratchet can't trust "
                  f"this project's hook record, so what it shows is not confirmed: {why}", file=sys.stderr)
            args.fn(args)
            # A read exits 1 when the record itself is missing, unreadable or names another folder (what it shows
            # may come from a folder the hooks don't use). A record that only may be out of date (a hook failure
            # is unresolved: status and doctor report it themselves) and the hook repair exit 0 after the note.
            if args.cmd != "install-hooks" and hook_env_problem(find_root(), doubt=False):
                sys.exit(1)
            return
    elif args.cmd == "accept-state":
        # the repair Ratchet names: it runs even while a hook failure is unresolved (that doubt is what it repairs
        # after, and a person types back the code), but never with a per-user folder the hooks don't use
        why = None if _IN_HOOK else hook_env_problem(find_root(), doubt=False)
        if why:
            die(why)
        if os.environ.get("RATCHET_HUMAN_OVERRIDE") == "1":  # a damaged state.json is refused before the code is asked
            _refuse_damaged_baseline(Ctx(find_root()))
        if not _IN_HOOK and _hook_record_doubt(find_root()):  # said in a few words (no times: the code comes next)
            print("RATCHET: accept-state still runs, although a hook failure here is unresolved; the doctor command "
                  "shows it, and the next clean run of that hook clears it.", file=sys.stderr)
        _HOOK_ENV_ADVISORY = True
    elif args.cmd not in ("hook", "hook-stop", "hook-prompt", "hook-guard"):
        require_hook_env(find_root())  # a per-user folder or key file the hooks don't use is not trusted
    if _changes_state(args):
        if _may_use_override(args) and not _OVERRIDE_CONFIRMED:
            # before the lock: a person may take a while to type the code, and nothing may wait on them meanwhile
            _confirm_override(ACCEPT_STATE_PURPOSE if args.cmd == "accept-state"
                              else f"use it for `ratchet.py {args.cmd}`")
        # held for the whole command, its test runs included: nothing else changes the run meanwhile
        with _StateLock(find_root(), cmd=args.cmd, refresh=True, create_dir=args.cmd == "init"):
            _STATE_LOCK_HELD = True
            try:
                args.fn(args)
            finally:
                _STATE_LOCK_HELD = False
        return
    args.fn(args)


if __name__ == "__main__":
    main()
