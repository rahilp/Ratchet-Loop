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
import subprocess
import sys
import tempfile
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


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        die(f"{path} is not valid JSON: {e}")


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:48].rstrip("-")) or "feature"


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
        a = read_json(self.ratchet / "active.json", {}) or {}
        return a.get("slug")

    def run_dir(self, slug=None) -> Path:
        slug = slug or self.active_slug()
        if not slug:
            die("No active Ratchet run. Start one with: ratchet.py init \"<feature>\"")
        return self.ratchet / "runs" / slug

    def state_path(self, slug=None):
        return self.run_dir(slug) / "state.json"

    def state(self, slug=None):
        p = self.state_path(slug)
        verify_chain(p, "state.json")
        st = read_json(p)
        if st is None:
            die("state.json missing for active run")
        return st

    def save_state(self, st):
        st["updated_at"] = now()
        if st.get("hooks_waived") and not _waiver_applies(self.root, self.config(), st):
            st.pop("hooks_waived", None)
            st["hooks_waive_n"] = int(st.get("hooks_waive_n", 0)) + 1  # hooks are installed or fired: the old consent must not resurface later
            self.log(st, "hooks_waive_cleared", note="enforcement hooks are active")
        write_json(self.state_path(st["slug"]), st)
        record_chain(self.state_path(st["slug"]))

    def plan(self, slug=None):
        return read_json(self.run_dir(slug) / "checkpoints.json")

    def evidence(self, cp_id: str, slug=None) -> Path:
        d = self.run_dir(slug) / "evidence" / cp_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log(self, st, event: str, **data):
        st.setdefault("history", []).append({"at": now(), "event": event, **data})
        if event in PROGRESS_EVENTS:
            st["stop_blocks"] = 0  # the safety valve counts stops WITHOUT progress


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
    return Path(os.environ.get("RATCHET_HOME") or Path.home() / ".config" / "ratchet") / "file-hashes.json"


def record_chain(p: Path):
    try:
        cp = _chain_path()
        data = read_json(cp, {}) or {}
        data[str(p.resolve())] = hashlib.sha256(p.read_bytes()).hexdigest()
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, cp)
    except OSError:
        pass


def verify_chain(p: Path, label: str):
    known = (read_json(_chain_path(), {}) or {}).get(str(p.resolve()))
    if not known or not p.exists():
        return
    if hashlib.sha256(p.read_bytes()).hexdigest() != known:
        die(f"{label} was changed outside ratchet.py ({p}). Ratchet won't trust it. Tell the user; restore the file, "
            "or — only if they are sure — they can accept it from their own terminal with "
            "`RATCHET_HUMAN_OVERRIDE=1 ratchet.py accept-state`.")


def cmd_accept_state(args):
    if os.environ.get("RATCHET_HUMAN_OVERRIDE") != "1":
        die("accept-state is for a person at their own terminal (RATCHET_HUMAN_OVERRIDE=1).")
    ctx = Ctx(find_root())
    for p in (ctx.state_path(), ctx.ratchet / "human_log.jsonl"):
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


def _markers_path() -> Path:
    return ratchet_home() / "active-runs.json"


def register_active(root: Path, slug: str | None):
    """Remember active runs outside the repo, so deleting .ratchet/ is noticed instead of silently ending the loop."""
    try:
        p = _markers_path()
        data = read_json(p, {}) or {}
        key = str(root.resolve())
        if slug:
            data[key] = {"slug": slug, "since": now()}
        else:
            data.pop(key, None)
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json(p, data)
    except Exception:
        pass


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
    """approve | unclear. A reply only counts as an approval if it is nothing BUT an approval: it must contain a real
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


CANCEL_HINT_RE = re.compile(r"\b(cancel|abandon|scrap|kill)\b[^.?!]*\b(run|build|feature|ratchet|loop|work|whole thing|everything)\b"
                            r"|\bforget (it|the whole thing)\b|\bstart over\b", re.I)


RULE_KEYS = ["review_depth", "reviewers", "max_adversarial_rounds", "max_adversarial_rounds_run", "max_ui_rounds",
             "max_behavior_attempts", "ui_block_on_nits", "ui_globs", "regression_command", "standards",
             "fingerprint_include", "approver", "ui_single_reviewer_ok", "min_review_timeout_sec", "test_timeout_sec",
             "host_provider", "ui_reviewers", "design_md", "max_stop_blocks", "unavailable_ttl_hours",
             "verify_transcripts", "min_review_seconds", "commit_per_checkpoint", "blocking_severities",
             "scope_later_rounds", "host_models", "role_models", "checks"]


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

def next_action(ctx: Ctx, st=None) -> dict:
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
        return {"phase": phase, "action": "Waiting for the user to approve the plan. When they reply, only a clear "
                "approval counts — otherwise answer their question or make their change and ask again." + extra,
                "stop_ok": not missing}
    if phase in ("awaiting_checkpoint_review", "awaiting_final_review"):
        return {"phase": phase, "action": "Waiting for the human to review. On approval run ratchet.py "
                "human-approve; on feedback run ratchet.py feedback.", "stop_ok": True}
    if phase == "planning_feedback":
        return {"phase": phase, "action": "Spawn ratchet-checkpoint-planner in EXTEND mode with the human's feedback "
                "(append new checkpoints, never edit completed ones), ratchet-test-planner for each new checkpoint, "
                "then run ratchet.py plan-extend", "stop_ok": False}
    if phase in ("blocked", "paused", "done", "cancelled"):
        na = {"phase": phase, "action": st.get("blocked_reason") or phase, "stop_ok": True}
        if phase == "blocked":
            na["options"] = blocked_options(st.get("blocked_reason"))
            na["reason_plain"] = plain_reason(st.get("blocked_reason"))
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
    Only offers things that can work: raising a review limit is a user-confirmed config change."""
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


def cmd_status_text(ctx: Ctx):
    st = ctx.state()
    plan = ctx.plan() or {"checkpoints": []}
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
    if st.get("phase") == "blocked":
        print(f"Why: \"{plain_reason(st.get('blocked_reason')) or 'no reason was recorded'}\"")
        print("What would you like to do?")
        for o in blocked_options(st.get("blocked_reason")):
            print(f"  {o['letter']}) {o['text']}")
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


# ---- providers: discover, test and register other agent CLIs ------------------------------------------------

PROBE_TOKEN = "RATCHET-PROBE-OK"


def _status_path(ctx):
    return ctx.ratchet / "provider-status.json"


def provider_status(ctx) -> dict:
    return read_json(_status_path(ctx), {}) or {}


def probe_provider(ctx, cfg, pv, bench=True) -> tuple[bool, str]:
    """Send a tiny request through the provider exactly as a review would go, and record the result."""
    cmd_t, use_stdin = provider_command(pv)
    if not cmd_t:
        return False, "no command"
    if not shutil.which(cmd_t[0]):
        return False, f"'{cmd_t[0]}' is not installed or not on PATH"
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
    data = provider_status(ctx)
    data[pv["id"]] = {"ok": good, "at": now(), "detail": detail}
    write_json(_status_path(ctx), data)
    if not good and bench:
        for lens in LENSES:
            mark_unavailable(ctx, f"{pv['id']}-{lens}", f"test request failed: {detail}")
    else:
        live = unavailable_reviewers(ctx)
        for lens in LENSES:
            live.pop(f"{pv['id']}-{lens}", None)
        write_json(_unavailable_path(ctx), live)
    return good, detail


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
            data = provider_status(ctx)
            data.pop(pv["id"], None)  # an earlier result was about whatever had this id before
            write_json(_status_path(ctx), data)
        else:
            good, detail = probe_provider(ctx, cfg, pv, bench=False)
            if not good:
                die(f"{args.add} didn't pass the test request ({detail}). Not added. Fix the command or model and "
                    "try again (or add --no-check to add it anyway).")
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
                good, detail = probe_provider(ctx, cfg, pv)
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
        old_st = read_json(ctx.state_path(existing), None)
        if old_st and old_st.get("phase") not in ("done", "cancelled"):
            if not (args.force and os.environ.get("RATCHET_HUMAN_OVERRIDE") == "1"):
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
        "stop_blocks": 0, "history": [], "branch": branch, "dirty_at_start": dirty[:50],
        "owner_session": human[-1]["session"] if human else None,
        "base_tree": fingerprint(root)["id"],
    }
    write_json(rd / "state.json", st)
    record_chain(rd / "state.json")
    write_json(ctx.ratchet / "active.json", {"slug": slug})
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


def unavailable_reviewers(ctx) -> dict:
    data = read_json(_unavailable_path(ctx), {}) or {}
    t = now()
    return {k: v for k, v in data.items() if v.get("until", "") > t}


def mark_unavailable(ctx, rid, reason, hours=None):
    hours = hours if hours is not None else float(ctx.config().get("unavailable_ttl_hours", 6))
    until = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=hours)).isoformat(timespec="microseconds")
    data = unavailable_reviewers(ctx)
    data[rid] = {"reason": reason[:300], "since": now(), "until": until}
    write_json(_unavailable_path(ctx), data)
    return until


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


def cmd_plan_submit(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] not in ("planning", "awaiting_plan_approval"):
        die(f"plan-submit only allowed while planning (phase is {st['phase']}). Use plan-extend for feedback.")
    plan = ctx.plan()
    if plan is None:
        die("checkpoints.json not found in the run folder")
    errs = validate_plan(plan) + spec_plan_problems(ctx, plan)
    if errs:
        die("Plan invalid:\n  - " + "\n  - ".join(errs))
    st["phase"] = "awaiting_plan_approval"
    st["awaiting_since"] = now()
    st["plan_hash"] = _plan_hash(plan, ctx)
    st["checkpoints"] = {cp["id"]: st["checkpoints"].get(cp["id"]) or new_cp_state(cp)
                         for cp in plan["checkpoints"]}
    ctx.log(st, "plan_submitted", hash=st["plan_hash"], count=len(plan["checkpoints"]))
    ctx.save_state(st)
    path = build_viewer(ctx)
    link = viewer_link(ctx)
    ok(f"Plan valid ({len(plan['checkpoints'])} checkpoints).")
    print(f"VIEWER: {link['url']}" + ("" if link["live"] else "   (static; `ratchet.py view --serve` for a live link)"))
    if args.open:
        webbrowser.open(link["url"]) if link["live"] else open_viewer(path)


def _in_a_minute():
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=1)).isoformat(timespec="microseconds")


def _human_since(ctx: Ctx, since_iso: str | None):
    log = ctx.ratchet / "human_log.jsonl"
    if not log.exists() or not since_iso:
        return []
    verify_chain(log, "human_log.jsonl")
    out = []
    for line in log.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        at = e.get("at", "")
        if since_iso < at <= _in_a_minute():  # a timestamp from the future is forged, not typed
            out.append(e)
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
    return p.read_text().strip().encode()


def _is_director_session() -> bool:
    return os.environ.get("RATCHET_ROLE") == "director"


def _approval_payload(ctx, st, action) -> str:
    parts = [str(ctx.root.resolve()), st["slug"], st["phase"], str(st.get("awaiting_since")),
             str(st.get("plan_hash")), action]
    if action == "hooks-waive":  # single use: the counter moves on every waive/clear, so a spent token is dead
        parts.append(str(st.get("hooks_waive_n", 0)))
    return "|".join(parts)


def _sign(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _enrolled_outside(ctx) -> str | None:
    return (read_json(ratchet_home() / "enrolled.json", {}) or {}).get(str(ctx.root.resolve()))


def approval_mode(ctx) -> str:
    enr = read_json(ctx.ratchet / "director.json")
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
    if os.environ.get("RATCHET_HUMAN_OVERRIDE") == "1":
        return {"by": "human", "detail": "(RATCHET_HUMAN_OVERRIDE)"}
    p = st.get("pending") or {}
    if action in ("approve-plan", "human-approve") and p.get("action") not in (None, "hooks-waive"):
        die(f"A different question is waiting for the user's answer: shall I {ASKABLE.get(p.get('action'), p.get('action'))}"
            f"{' -> ' + p['mode'] if p.get('mode') else ''}? Their reply answers THAT question, not this approval. "
            f"If they said yes to it, run `ratchet.py {PENDING_COMMAND.get(p.get('action'), p.get('action'))}"
            f"{' ' + p['mode'] if p.get('mode') else ''}`. If they said no or moved on, run `ratchet.py ask withdraw`, "
            "then ask them plainly about this approval and wait for their reply.")
    msgs = _human_since(ctx, st.get("awaiting_since"))
    if p.get("action") == "hooks-waive" and action != "hooks-waive":
        # the first reply after the hooks question answers THAT question; it is not an approval of anything else
        # the first reply after the hooks question is its answer, whatever it says ("Any other reply means no"),
        # so it can never double as an approval of the plan or a checkpoint
        after = [m for m in msgs if m.get("at", "") > p.get("asked_at", "")]
        if after:
            msgs = [m for m in msgs if m is not after[0]]
    if action == "hooks-waive":
        msgs = msgs[:1]  # only the FIRST reply after the hooks question answers it
    if msgs:
        entry = msgs[-1]
        last = entry.get("prompt", "")
        intent = hooks_waive_intent if action == "hooks-waive" else message_intent
        if action != "feedback" and intent(last) != "approve":
            if action == "hooks-waive":
                st.pop("pending", None)  # spent: a later yes must not turn this no into consent
                ctx.save_state(st)
                die(f"The user's reply to the hooks question was not a clear yes: \"{last[:120]}\". Nothing was "
                    "recorded. Only the exact reply \"" + HOOKS_WAIVE_REPLY + "\" (or a listed equivalent) counts. If they "
                    "still want to skip hooks, run `ratchet.py ask hooks-waive` again and show them the question it "
                    "prints word for word.")
            die(f"The user's latest reply is not a clear yes: \"{last[:120]}\". Answer it or make the change they asked "
                "for, then ask them plainly (e.g. \"Shall I go ahead? Reply yes to continue.\") and try again after "
                "they reply.")
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


def _require_human(ctx, st, action="human-approve", token=None):
    a = _require_approval(ctx, st, action, token)
    return {"prompt": a["detail"], "by": a["by"], "session": a.get("session")}


def cmd_director_key(args):
    if not _is_director_session():
        die("director-key must be run from the director's session (RATCHET_ROLE=director).")
    p = director_key_path()
    if p.exists() and not args.rotate:
        ok(f"Director key exists: {p}")
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(secrets.token_hex(32) + "\n")
    os.chmod(p, 0o600)
    ok(f"Director key {'rotated' if args.rotate else 'created'}: {p}  (re-enroll lanes after a rotation)")


def cmd_director_enroll(args):
    if not _is_director_session():
        die("director-enroll must be run from the director's session (RATCHET_ROLE=director).")
    ctx = Ctx(find_root())
    ctx.ratchet.mkdir(exist_ok=True)
    key = _director_key()
    sig = _sign(key, "enroll|" + str(ctx.root.resolve()))
    write_json(ctx.ratchet / "director.json", {"enrolled_at": now(), "sig": sig})
    outside = read_json(ratchet_home() / "enrolled.json", {}) or {}
    outside[str(ctx.root.resolve())] = sig
    write_json(ratchet_home() / "enrolled.json", outside)
    ok(f"Lane {ctx.root} enrolled: approvals now require a director token.")


def cmd_director_sign(args):
    if not _is_director_session():
        die("director-sign must be run from the director's session (RATCHET_ROLE=director).")
    ctx = Ctx(find_root())
    st = ctx.state()
    want = {"approve-plan": ["awaiting_plan_approval"],
            "human-approve": ["awaiting_checkpoint_review", "awaiting_final_review", "blocked"],
            "feedback": ["awaiting_checkpoint_review", "awaiting_final_review", "done"],
            "approve-changes": ["building", "blocked", "awaiting_checkpoint_review", "awaiting_final_review"],
            "ui-respec": ["building", "blocked"],
            "guardrail": ["building", "blocked"],
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
    st["approved"] = approval_snapshot(ctx, st)
    if msg.get("session"):
        st["owner_session"] = msg["session"]  # the session where the user said yes owns the build
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
    """Every planned (non-browser) case ID must appear in at least one test file."""
    files = (cp.get("tests") or {}).get("files") or []
    blob = ""
    for f in files:
        for p in ctx.root.glob(f) if any(ch in f for ch in "*?[") else [ctx.root / f]:
            if p.is_file():
                blob += p.read_text(errors="ignore")
    missing = [c["id"] for c in _planned_cases(ctx, cp["id"])
               if not re.search(r"(?<![A-Za-z0-9-])" + re.escape(c["id"]) + r"(?![0-9])", blob)]
    return files, missing, bool(blob)


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
    cps["step"] = "red"
    ctx.log(st, "tests_written", cp=args.cp, files=files)
    ctx.save_state(st)
    ok(f"{args.cp}: all {len(_planned_cases(ctx, args.cp))} planned cases are present. Next: ratchet.py red {args.cp}")


def _run_cmd(ctx, command, log_path, timeout):
    t0 = _dt.datetime.now()
    try:
        r = subprocess.run(command, shell=True, cwd=ctx.root, capture_output=True, text=True,
                           timeout=timeout, env=dict(os.environ, CI="1", FORCE_COLOR="0"))
        code, out = r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        code, out = 124, f"TIMEOUT after {timeout}s\n{e.stdout or ''}{e.stderr or ''}"
    dur = (_dt.datetime.now() - t0).total_seconds()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"$ {command}\n# exit={code} duration={dur:.1f}s at {now()}\n\n{out}")
    return code, out, dur


def _tail(text, n=60):
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])


def cmd_red(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["red"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    cfg = ctx.config()
    code, out, _ = _run_cmd(ctx, cp["tests"]["command"], ctx.evidence(args.cp) / "red.log", cfg["test_timeout_sec"])
    cps["red"] = {"exit": code, "at": now()}
    if code == 0 and not args.allow_green_first:
        ctx.save_state(st)
        die(f"{args.cp}: tests PASSED before implementation — they don't prove anything yet. "
            f"Have ratchet-test-writer strengthen them, or if this checkpoint only preserves existing "
            f"behaviour re-run with --allow-green-first --reason '...'.")
    if code == 0:
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


def weakened_tests(ctx, st) -> list[str]:
    cur = st.get("current")
    cps = (st.get("checkpoints") or {}).get(cur) or {}
    if not cps.get("red_tests"):
        return []
    out = []
    d = ctx.run_dir() / "evidence" / cur / "red-tests"
    for rel in cps["red_tests"]:
        old_p, new_p = d / rel.replace("/", "__"), ctx.root / rel
        if not old_p.exists():
            continue
        if not new_p.exists() or not _additive(old_p.read_text(errors="replace"), new_p.read_text(errors="replace")):
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
    code, out, dur = _run_cmd(ctx, cp["tests"]["command"], log, cfg["test_timeout_sec"])
    failed_check = None
    fp_tests = fingerprint(ctx.root)["id"] if code == 0 and cfg.get("checks") else None
    if code == 0:
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
    if code != 0:
        g["status"] = "failed"
        g["last"] = {"exit": code, "log": str(log.relative_to(ctx.root)), "at": now()}
        if g["attempts"] >= cfg["max_behavior_attempts"]:
            _set_blocked(st, f"{args.cp}: behavior gate failed {g['attempts']} times — needs a human decision.")
        ctx.log(st, "behavior_failed", cp=args.cp, attempt=g["attempts"])
        ctx.save_state(st)
        build_viewer(ctx)
        print(_tail(out))
        die(f"Behavior gate FAILED for {args.cp} (exit {code}). Log: {log.relative_to(ctx.root)}. "
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


def cmd_ui_record(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    cps = _cur(ctx, st, args.cp)
    _expect_step(cps, ["ui", "adversarial", "complete"], args.cp)
    cp = cp_by_id(ctx.plan(), args.cp)
    if not cp["gates"].get("ui"):
        die(f"{args.cp} has no UI gate")
    cfg = ctx.config()
    review_path = Path(args.review).resolve()
    review = read_json(review_path)
    if review is None:
        die(f"Review file not found: {args.review}")
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
    g = cps["gates"]["ui"]
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
            f"wrong, run `ratchet.py ask ui-respec {args.cp}`, explain to the user what you want to change and "
            f"why, and after their clear yes run `ratchet.py ui-respec {args.cp}`.")
    g["attempts"] += 1
    verdict, blockers = _ui_verdict(review, cfg)
    dest = ctx.evidence(args.cp) / "ui" / f"round-{g['attempts']:02d}"
    dest.mkdir(parents=True, exist_ok=True)
    if review_path.parent != dest:
        shutil.copy2(review_path, dest / "combined.json")
    if verdict == "INVALID":
        g["status"] = "invalid"
        ctx.log(st, "ui_invalid", cp=args.cp, reason=review.get("invalid_reason"))
        ctx.save_state(st)
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
    for rp in args.reviews:
        r = read_json(Path(rp))
        if r is None:
            die(f"Review not found: {rp}")
        rid = r.get("reviewer") or Path(rp).stem
        if r.get("fingerprint") != fp["id"]:
            die(f"{rp} ({rid}) is not stamped with the current fingerprint {fp['id'][:10]}. Re-run that reviewer.")
        if r.get("round_nonce") != rnd["nonce"]:
            die(f"{rp} ({rid}) does not carry this round's nonce. Reviews must come from reviewers launched for "
                f"round {rnd['round']} (see `ratchet.py reviewers {args.cp}`).")
        if r.get("checkpoint") not in (None, args.cp):
            die(f"{rp} is a review of {r.get('checkpoint')}, not {args.cp}.")
        age = Path(rp).stat().st_mtime - _iso(rnd["opened_at"])
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
        reviews[rid] = (rp, r)
    missing = [i for i in required if i not in reviews]
    if missing:
        die(f"{args.cp} (risk={cp.get('risk', 'normal')}) needs reviews from {required}; missing {missing}. "
            f"Run `ratchet.py reviewers {args.cp}` to see how to launch each one.")
    rnd["recorded"] = now()
    dest = ctx.evidence(args.cp) / "adversarial" / f"round-{rnd['round']:02d}"
    dest.mkdir(parents=True, exist_ok=True)
    all_findings, verdicts = [], []
    for rid in required:
        rp, r = reviews[rid]
        spec = _roster_spec(cfg, rid) or {}
        findings = r.get("findings", []) or []
        all_findings += [dict(f, reviewer=rid) for f in findings]
        verdicts.append({"reviewer": rid, "vendor": spec.get("vendor"), "claimed": r.get("verdict"),
                         "findings": len(findings)})
        if Path(rp).resolve().parent != dest.resolve():
            shutil.copy2(rp, dest / f"{rid}.json")
    need = len(required)
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
    changed_since = _changed_since_last_round(ctx, cfg, g, fp["id"])
    raw_count = len(all_findings)
    all_findings, deferred = split_findings(cfg, cp.get("risk", "normal"), all_findings, changed_since, ctx.root)
    deferred = covered_minor + deferred
    if deferred:
        write_json(dest / "deferred-findings.json", deferred)
        keep = [x for x in cps.get("deferred") or [] if x.get("round") != rnd["round"]]
        cps["deferred"] = keep + [dict(x, round=rnd["round"]) for x in deferred]
    approved = not all_findings
    g["rounds"] = g.get("rounds", []) + [{"at": now(), "fingerprint": fp["id"], "verdicts": verdicts,
                                          "findings": len(all_findings), "deferred": len(deferred),
                                          "raw_findings": raw_count, "substitutions": subs,
                                          "blocking_files": sorted({_norm_path(f.get("file") or f.get("path") or f.get("location"), ctx.root)
                                                                    for f in all_findings} - {""}),
                                          "reviewers": [{"id": rr["id"], "vendor": rr.get("vendor"),
                                                         "model": rr.get("model")} for rr in rnd.get("resolved") or []]}]
    if not approved:
        g["status"] = "failed"
        if g["attempts"] >= cfg["max_adversarial_rounds"]:
            _set_blocked(st, f"{args.cp}: adversarial review still has {len(all_findings)} finding(s) after "
                             f"{g['attempts']} rounds — needs a human/director decision.")
        ctx.log(st, "adversarial_changes_requested", cp=args.cp, findings=len(all_findings))
        ctx.save_state(st)
        build_viewer(ctx)
        write_json(dest / "open-findings.json", all_findings)
        _announce_repeats(ctx, st)
        die(f"Gate 3 (adversarial) NOT approved: {len(all_findings)} blocking finding(s)"
            + (f" ({len(deferred)} minor ones recorded for later)" if deferred else "") + " → "
            f"{(dest / 'open-findings.json').relative_to(ctx.root)}. Spawn ratchet-fixer with that file, "
            "re-run behavior (and UI if visible code changed), then re-run the adversaries.", 2)
    g.update(status="passed", fingerprint=fp, passed_at=now(), evidence=str(dest.relative_to(ctx.root)))
    cps["step"] = _earliest_open_step(cps)
    ctx.log(st, "adversarial_approved", cp=args.cp, round=g["attempts"])
    ctx.save_state(st)
    build_viewer(ctx)
    note = (f" {len(deferred)} minor finding(s) were recorded for later instead of blocking (`ratchet.py deferred`)."
            if deferred else "")
    _announce_repeats(ctx, st)
    ok(f"Gate 3 (adversarial) APPROVED by all {need} reviewer(s) for {args.cp}.{note} Next: ratchet.py complete {args.cp}")


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


def cmd_deferred(args):
    """Minor findings that were recorded instead of blocking, for the user to decide on at the end."""
    ctx = Ctx(find_root())
    st = ctx.state()
    rows = [(cp, f) for cp, c in (st.get("checkpoints") or {}).items() for f in c.get("deferred") or []]
    if getattr(args, "json", False):
        print(json.dumps([dict(f, checkpoint=cp) for cp, f in rows], indent=2))
        return
    if not rows:
        print("No findings were left for later.")
        return
    print(f"{len(rows)} minor finding(s) were left for later (they did not block):")
    for cp, f in rows:
        where = f" ({f['file']}{':' + str(f['line']) if f.get('line') else ''})" if f.get("file") else ""
        print(f"- {cp}{where}: {f.get('description', '').strip()[:240]}")


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
        changed = changed_files(ctx.root, gfp, fp)
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
        allow["unblock"] = False
        if not allow.get("files") and not allow.get("guardrail"):
            st.pop("choice_allow", None)
    else:
        msg = _require_human(ctx, st, "human-approve", getattr(args, "director_token", None))
    order = [c["id"] for c in (ctx.plan() or {"checkpoints": []})["checkpoints"]]
    if st["phase"] == "blocked":
        back = st.pop("blocked_from", None) or "building"
        if back == "blocked":
            back = "building"
        st["phase"] = back
        st["blocked_reason"] = None
        cur = st.get("current")
        cfg = ctx.config()
        if int(st.get("adv_rounds_total", 0)) >= cfg["max_adversarial_rounds_run"]:
            st["adv_rounds_total"] = 0  # the user chose to continue: the run-wide budget starts again
        if back == "building" and not cur:  # nothing current: pick up the next unfinished checkpoint
            pending = [c for c in order if st["checkpoints"][c]["status"] != "done"]
            if pending:
                _advance(st, pending[0], fingerprint(ctx.root)["id"])
            else:
                st["phase"] = "awaiting_final_review"
        elif cur:
            # a human unblock resets the attempt counters on the current checkpoint's failing gates
            for g in st["checkpoints"][cur]["gates"].values():
                if g["status"] != "passed":
                    g["attempts"] = 0
            st["checkpoints"][cur]["guardrail_tries"] = 0
        ctx.log(st, "unblocked", by=msg["by"], note=msg.get("prompt", "")[:200], back_to=st["phase"])
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
    st["stop_blocks"] = 0
    register_active(ctx.root, st["slug"])
    ctx.log(st, "resumed")
    ctx.save_state(st)
    ok(f"Resumed '{st['slug']}' in phase {st['phase']}" + (" — this chat now owns the run." if args.claim else "."))
    print(json.dumps(next_action(ctx, st), indent=2))


ASKABLE = {"approve-changes": "accept the changes listed above as the new baseline",
           "cancel": "cancel this Ratchet run (commits already made are kept)",
           "set-review": "change how often you review",
           "claim": "continue this Ratchet run in THIS chat",
           "hooks-waive": "build without the Ratchet hooks (the checks then only advise; nothing enforces them)",
           "ui-respec": "change what the UI check compares for this step (its capture steps or scope), as explained above",
           "choice": "go with one of the lettered options you listed"}
# the command that carries out a "yes" to each question
PENDING_COMMAND = {"approve-changes": "approve-changes", "cancel": "cancel", "set-review": "set-review",
                   "claim": "resume --claim", "hooks-waive": "hooks-waive", "ui-respec": "ui-respec", "choice": "choice"}
REVIEW_MODE_WORDS = {"end": "only at the end", "each": "after every step", "none": "never (fully autonomous)"}


def cmd_ask(args):
    """Record the question the agent is about to ask the user. Only a clear yes typed AFTER this counts."""
    ctx = Ctx(find_root())
    st = ctx.state()
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
    if args.action not in ("set-review", "ui-respec") and args.mode:
        die(f"ask {args.action} takes no extra value")
    if args.action == "choice":
        return _ask_choice(ctx, st, args)
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
    old = st.get("pending") or {}
    if args.action == "hooks-waive" and old.get("action") not in (None, "hooks-waive"):
        die(f"A different question ({old.get('action')}) is still waiting for the user's answer. Get that answered "
            "first; a hooks question must not take over their reply.")
    asked = now()
    st["pending"] = {"action": args.action, "mode": args.mode, "asked_at": asked, "prev_awaiting": st.get("awaiting_since")}
    if args.action != "hooks-waive":  # a hooks question must not open (or reset) any approval window
        st["awaiting_since"] = asked
    ctx.log(st, "question_asked", action=args.action, mode=args.mode)
    ctx.save_state(st)
    if args.action == "hooks-waive":
        print(f"ASK THE USER: Shall I build without the Ratchet hooks? The checks would then only advise; nothing "
              f"enforces them. To agree, reply exactly \"{HOOKS_WAIVE_REPLY}\". Any other reply means no.")
        return
    what = ASKABLE[args.action]
    if args.action == "set-review":
        what = f"change how often you review to: {REVIEW_MODE_WORDS[args.mode]}"
    q = f"{detail} Shall I {what}? Reply yes to confirm."
    print(f"ASK THE USER: {q.strip()}")


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
    asked = now()
    st["pending"] = {"action": "choice", "mode": None, "options": opts, "locked": locked, "guardrail": guards,
                     "asked_at": asked, "prev_awaiting": st.get("awaiting_since")}
    st["awaiting_since"] = asked
    ctx.log(st, "question_asked", action="choice", options=opts, locked=locked)
    ctx.save_state(st)
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


def cmd_choice(args):
    """Record which lettered option the user picked, from their first reply after `ask choice`."""
    ctx = Ctx(find_root())
    st = ctx.state()
    _require_pending(st, "choice")
    p = st["pending"]
    msgs = _human_since(ctx, p.get("asked_at"))
    if not msgs:
        die("No reply from the user yet. End your turn and wait for them to pick an option.")
    entry = msgs[0]  # the first reply answers the question
    letter = choice_letter(entry.get("prompt", ""), p.get("options") or [])
    if ctx.config().get("verify_transcripts", True) and os.environ.get("RATCHET_HUMAN_OVERRIDE") != "1":
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
        die(f"First run `ratchet.py ask {action}{' ' + mode if mode else ''}`, show the user the question it prints, "
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


def cmd_next(args):
    ctx = Ctx(find_root())
    na = next_action(ctx)
    hs = hooks_view(ctx)
    hw = hooks_warning(ctx, hs)
    if hw and na.get("phase") not in ("planning",):
        na["warning"] = hw
    na["hooks"] = hs
    na["hook_errors"] = hook_errors_view(ctx.root, hs.get("host", "unknown"))
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
    cp = cp_by_id(ctx.plan(), args.cp)
    fp = fingerprint(ctx.root)["id"]
    g = cps["gates"]["adversarial"]
    rnd = cps.get("adv_round")
    if not rnd or rnd.get("recorded") or rnd["fingerprint"] != fp:
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
                good, detail = probe_provider(ctx, cfg, provider_by_id(cfg, pid))
                print(f"  checked {pid}: {'working' if good else 'not usable now (' + detail + ')'}", file=sys.stderr)
            resolved = resolve_reviewers(ctx, cfg, cp)
        g["attempts"] += 1
        st["adv_rounds_total"] = int(st.get("adv_rounds_total", 0)) + 1
        prev = rnd["round"] if rnd else 0
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
    out_dir = ctx.run_dir() / "reviews" / args.cp / f"adv-round-{rnd['round']:02d}"
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
            spec["how"] = (f"Spawn a ratchet-adversary sub-agent (model: {want}): lens={spec['lens']}, reviewer id '{rid}', "
                           f"fingerprint {fp}, round_nonce {rnd['nonce']}, checkpoint {args.cp}, output {spec['output']}"
                           + (f", fixes.md {fixes}" if rnd["round"] > 1 else "")
                           + ("" if not rules else ". Review rules for this round (tell it): " + " ".join(rules)))
        else:
            spec["how"] = f"python3 {ext_script} --reviewer {rid} --cp {args.cp} --out {spec['output']}{fixes_arg}"
        rows.append(spec)
    res = {"checkpoint": args.cp, "risk": cp.get("risk", "normal"), "round": rnd["round"], "fingerprint": fp,
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
    for r in rows:
        print(f"  - {r['id']:<22} [{r['vendor']}/{r['lens']}] {r['how']}")
        for skipped in r["fallback_for"]:
            print(f"      (instead of {skipped}: {r['reasons'][skipped]})")
    for rule in rules:
        print(f"  rule: {rule}")
    print(f"Then: ratchet.py adv-record {args.cp} --reviews {out_dir}/*.json")


def cmd_reviewer_status(args):
    ctx = Ctx(find_root())
    cfg = ctx.config()
    if args.reset:
        data = unavailable_reviewers(ctx)
        data.pop(args.reset, None)
        write_json(_unavailable_path(ctx), data)
        ok(f"{args.reset} will be tried again")
    if args.mark:
        until = mark_unavailable(ctx, args.mark, args.reason or "marked unavailable")
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
                    "gates": {n: {k: g.get(k) for k in ("status", "attempts", "by") if g.get(k) is not None}
                              for n, g in (s.get("gates") or {}).items()}})
    na = next_action(ctx, st)
    hs = hooks_view(ctx, st)
    hw = hooks_warning(ctx, hs)
    waiting_on = None
    if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review", "blocked"):
        waiting_on = "director" if approval_mode(ctx) == "director" else "human"
    return {"root": str(ctx.root), "slug": st["slug"], "feature": st["feature"], "phase": st["phase"],
            "current": st.get("current"), "step": na.get("step"), "next": na["action"], "stop_ok": na["stop_ok"],
            "waiting_on": waiting_on, "blocked_reason": st.get("blocked_reason"),
            "blocked_reason_plain": plain_reason(st.get("blocked_reason")),
            "awaiting_since": st.get("awaiting_since"), "approval_mode": approval_mode(ctx),
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
                           "deferred": rd.get("deferred", 0), "own_company_only": own})
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
            "next": dict(next_action(ctx, st), label=plain_now(ctx, st, plan)), "hooks": hooks_view(ctx, st), "generated_at": now()}


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
    info = read_json(state_file, None)
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
    write_json(state_file, info)
    return info, True


def _stop_server(state_file: Path) -> bool:
    info = read_json(state_file, None)
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


def _heartbeat(root: Path, event: str, host: str):
    """Record that a hook fired (liveness only). It never carries crash state: crash records live only in per-run
    marker files and appended lines (see _record_hook_run), so a stale rewrite of this shared file by a concurrent
    hook can at worst roll an entry back to an older time, which only makes a hook look less healthy.
    The rewrite goes to a per-process temp file that then replaces the heartbeat in one rename, so a concurrent
    hook never reads a truncated, half-written heartbeat (that read would fail and crash the reading hook)."""
    p = root / RATCHET_DIR / "hook-heartbeat.json"
    tmp = p.with_name(f"{p.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
    try:
        hb = read_json(p, {}) or {}
        hb[event] = {"at": now(), "host": host}
        with open(tmp, "w") as f:  # every hook-state write uses open(), one name a test can fault
            f.write(json.dumps(hb))
        os.replace(tmp, p)
    except Exception:
        pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


ENGINE_PATH = str(Path(__file__).resolve())


def decide_stop(n: dict) -> tuple[bool, str]:
    root = find_root(Path(n["cwd"]))
    ctx = Ctx(root)
    marker = (read_json(_markers_path(), {}) or {}).get(str(root.resolve()))
    if not ctx.ratchet.is_dir() or not ctx.active_slug():
        if marker:  # .ratchet/ vanished under an active run: never let that end the loop silently
            return _stop_error(root, f"Ratchet run '{marker['slug']}' was active here but its .ratchet/ state is gone. "
                                     "Tell the user; restore it (e.g. from git history or backup) or ask them to cancel.")
        return False, ""
    _heartbeat(root, "stop", n["host"])
    st = read_json(ctx.state_path(), None)
    if not st:
        return _stop_error(root, "Ratchet's state.json for the active run is missing or unreadable.")
    _reset_stop_errors(root)
    if st.get("owner_session") and n.get("session") and n["session"] != st["owner_session"]:
        return False, ""  # another session in the same repo: not bound by this run (the prompt hook tells it)
    na = next_action(ctx, st)
    if na.get("stop_ok"):
        if st.get("stop_blocks"):
            st["stop_blocks"] = 0
            ctx.save_state(st)
        return False, ""
    cfg = ctx.config()
    st["stop_blocks"] = int(st.get("stop_blocks", 0)) + 1
    if st["stop_blocks"] > cfg["max_stop_blocks"]:
        _set_blocked(st, f"The agent tried to stop {st['stop_blocks']} times without any gate progress — "
                         "loop halted for the user to look at.")
        ctx.save_state(st)
        return False, f"RATCHET: safety valve — {st['blocked_reason']}"
    ctx.save_state(st)
    return True, ("RATCHET GATE: the work is not done, so you may not stop yet.\n"
                  f"Run: {st['slug']}  phase={na['phase']}"
                  + (f"  checkpoint={na.get('checkpoint')}  step={na.get('step')}" if na.get("checkpoint") else "")
                  + f"\nNext required action: {na['action']}\n"
                  f"(Engine: python3 \"{ENGINE_PATH}\" — load the ratchet-orchestrator skill if you haven't.)\n"
                  "Gates cannot be skipped or argued past. If the user asked you to stop, or you are genuinely "
                  "stuck, run `ratchet.py pause --reason \"...\"` and tell the user why.")


def _reset_stop_errors(root: Path):
    p = ratchet_home() / "stop-errors.json"
    data = read_json(p, {}) or {}
    if data.pop(str(root.resolve()), None) is not None:
        write_json(p, data)


def _stop_error(root: Path, why: str) -> tuple[bool, str]:
    """Fail CLOSED for stop when state is unreadable — but only a few times, so a broken install can't trap a session."""
    p = ratchet_home() / "stop-errors.json"
    data = read_json(p, {}) or {}
    key = str(root.resolve())
    data[key] = int(data.get(key, 0)) + 1
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json(p, data)
    except Exception:
        pass
    if data[key] > 5:
        return False, f"RATCHET: {why} (letting you stop after repeated failures — tell the user.)"
    return True, f"RATCHET: {why} You may not stop silently: explain this to the user first."


def decide_prompt(n: dict) -> tuple[bool, str]:
    if os.environ.get("RATCHET_NESTED_REVIEW") == "1":
        return False, ""
    root = find_root(Path(n["cwd"]))
    rdir = root / RATCHET_DIR
    if not rdir.is_dir():
        return False, ""
    _heartbeat(root, "prompt", n["host"])
    log = rdir / "human_log.jsonl"
    verify_chain(log, "human_log.jsonl")
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now(), "prompt": n["prompt"][:2000], "session": n["session"], "host": n["host"],
                            "transcript_path": n.get("transcript_path")}, ensure_ascii=False) + "\n")
    record_chain(log)
    ctx = Ctx(root)
    if ctx.active_slug():
        st = read_json(ctx.state_path(), None)
        if st and st.get("phase") not in ("done", "cancelled"):
            st["stop_blocks"] = 0
            ctx.save_state(st)
            eng = f'python3 "{ENGINE_PATH}"'
            if st.get("owner_session") and n.get("session") and n["session"] != st["owner_session"]:
                return False, (f"[ratchet] This repo has an unfinished Ratchet run '{st['slug']}' (phase {st['phase']}) "
                               "started in another chat. If the user wants to continue it here, run "
                               f"`{eng} ask claim`, ask them, and after a clear yes run `{eng} resume --claim`; "
                               "otherwise leave it alone and do what they asked.")
            p = st.get("pending") or {}
            if p.get("action") and p["action"] != "hooks-waive":
                cmd = PENDING_COMMAND.get(p["action"], p["action"]) + (" " + p["mode"] if p.get("mode") else "")
                what = (f"change how often they review to: {REVIEW_MODE_WORDS.get(p.get('mode'), p.get('mode'))}"
                        if p["action"] == "set-review" else ASKABLE.get(p["action"], p["action"]))
                if p["action"] == "choice":
                    letter = choice_letter(n["prompt"], p.get("options") or [])
                    return False, (f"[ratchet] This reply answers your lettered question ({', '.join(p.get('options') or [])}). "
                                   + (f"It reads as option {letter}. " if letter else "It is NOT a clear pick of one option. ")
                                   + f"Run `{eng} choice` to record it, then continue. It does NOT approve the plan or a "
                                   "checkpoint.")
                verdict = ("It reads as a clear yes" if message_intent(n["prompt"]) == "approve"
                           else "It is NOT a clear yes")
                return False, (f"[ratchet] This reply answers your waiting question: shall I {what}? {verdict}. "
                               f"Only after a clear yes run `{eng} {cmd}`; otherwise run `{eng} ask withdraw` and do "
                               "what they asked. This reply does NOT approve the plan, a checkpoint or anything else.")
            if st["phase"] == "building" and re.match(r"^\s*(stop|pause|hold on|wait|halt|cancel)\b", n["prompt"], re.I):
                return False, (f"[ratchet] The user asked you to stop. Run `{eng} pause --reason \"user asked\"`, "
                               "confirm in one line, and ask whether they want to resume later or cancel the run.")
            if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review",
                               "awaiting_final_review", "blocked"):
                if CANCEL_HINT_RE.search(n["prompt"]):
                    hint = (f"This may be a request to cancel the run — run `{eng} ask cancel`, ask them, and cancel "
                            "only after a clear yes.")
                elif message_intent(n["prompt"]) == "approve":
                    hint = "This reads as a clear approval — record it with the matching command."
                else:
                    hint = ("This is NOT a clear approval: answer it or make the requested change, then ask the user "
                            "to confirm plainly.")
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
ROLE_SET_RE = re.compile(r"(^|[\s;&|(])(export\s+)?RATCHET_ROLE\s*=|env\s+[^;&|]*RATCHET_ROLE=")
OVERRIDE_SET_RE = re.compile(r"(^|[\s;&|(])(export\s+)?RATCHET_HUMAN_OVERRIDE\s*=|env\s+[^;&|]*RATCHET_HUMAN_OVERRIDE=")
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
    (re.compile(r"^runs/[^/]+/reviews/[^/]+/adv-round-\d+/[^/]+\.json$", re.I), {"ratchet-adversary"}),
    (re.compile(r"^runs/[^/]+/reviews/[^/]+/round-\d+/visual\.json$", re.I), {"ratchet-visual-reviewer"}),
    (re.compile(r"^runs/[^/]+/reviews/[^/]+/round-\d+/behavior\.json$", re.I), {"ratchet-behavior-reviewer"}),
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
    if SELF_HOOK_RE.search(cmd):
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
        return (read_json(ctx.state_path(), {}) or {}).get("phase")
    except (Exception, SystemExit):
        return None


def decide_guard(n: dict) -> tuple[bool, str]:
    cmd, cwd = n["command"], n["cwd"]
    if not _is_director_session():
        key_bits = [str(director_key_path()), str(ratchet_home()), "director.key", ".config/ratchet", ".config/rat"]
        scan = cmd + " " + " ".join(n["paths"]) + " " + json.dumps({k: v for k, v in n["input"].items()
                                                                     if k in ("path", "pattern", "glob", "file_path",
                                                                              "absolute_path", "dir_path")})
        wide = n["kind"] in ("read", "shell", "other", "mcp") and ".config" in scan and re.search(r"\brat", scan)
        if any(b in scan for b in key_bits) or wide:
            return True, ("RATCHET GUARD: the director's key and Ratchet's per-user files are off-limits to builder "
                          "sessions. Wait for the director to approve this lane.")
        if n["kind"] == "shell" and (DIRECTOR_CALL_RE.search(cmd) or ROLE_SET_RE.search(cmd)):
            return True, "RATCHET GUARD: director commands and RATCHET_ROLE are reserved for the director session."
        if n["kind"] == "shell" and re.search(r"reviewer-status\b[^;&|\n]*--mark", cmd):
            return True, ("RATCHET GUARD: only the director (or the user, in their own terminal) can switch a "
                          "reviewer off. Reviewers that genuinely fail are marked unavailable automatically.")
        if n["kind"] == "shell" and OVERRIDE_SET_RE.search(cmd):
            return True, "RATCHET GUARD: RATCHET_HUMAN_OVERRIDE is for a person at their own terminal, not for agents."
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


def run_hook(event: str, host_arg: str | None) -> int:
    data = _hook_input()
    host = detect_hook_host(data, host_arg)
    started = now()  # a clean run resolves only crashes recorded before it began
    try:
        n = normalize_hook(data, host)
        block, msg = {"stop": decide_stop, "prompt": decide_prompt, "guard": decide_guard}[event](n)
        rc = emit_hook(host, event, block, msg)
        clean_root = _ratchet_root_of(n.get("cwd"))
        if clean_root is not None:
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
                with open(root / RATCHET_DIR / "hook-errors.log", "a") as f:
                    f.write(f"{now()} {host} {event}: {type(e).__name__}: {e}\n")
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


def _safe_config(root: Path) -> dict:
    """Config for read-only diagnostics: a corrupt config.json (read_json dies) counts as empty."""
    try:
        json.loads((root / RATCHET_DIR / "config.json").read_text())  # quiet pre-check: read_json die()s noisily
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
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
    try:
        hb = json.loads((root / RATCHET_DIR / "hook-heartbeat.json").read_text())
    except (OSError, ValueError):
        return {}
    return hb if isinstance(hb, dict) else {}


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
        with open(root / RATCHET_DIR / HOOK_CRASHES_UNSAVED, "a") as f:
            f.write(f"{now()} {_mark_host(host)} {event}\n")
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
    a = sp.add_parser("resume", help="resume a paused run; --claim moves the run to this chat (after `ask claim`)")
    a.add_argument("--claim", action="store_true"); a.set_defaults(fn=cmd_resume)
    a = sp.add_parser("ask", help="record the question you're about to ask the user (needed before cancel, "
                                  "set-review, approve-changes, hooks-waive, resume --claim, ui-respec)")
    a.add_argument("action", choices=list(ASKABLE) + ["withdraw"])
    a.add_argument("mode", nargs="?", help="set-review: end | each | none; ui-respec: the checkpoint id")
    a.add_argument("--options", help="choice: the option letters, e.g. A,B,C")
    a.add_argument("--guardrail", action="append", metavar="LETTER=KIND",
                   help="choice: picking LETTER adds a guardrail step (an automatic check) for this kind of finding")
    a.add_argument("--locked", action="append", metavar="LETTER=FILE[,FILE]",
                   help="choice: picking LETTER approves changing these locked files (repeatable)")
    a.set_defaults(fn=cmd_ask)
    sp.add_parser("accept-state", help=argparse.SUPPRESS).set_defaults(fn=cmd_accept_state)
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
    a = sp.add_parser("deferred", help="minor findings recorded for later instead of blocking")
    a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_deferred)
    sp.add_parser("next").set_defaults(fn=cmd_next)
    a = sp.add_parser("status"); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_status)
    sp.add_parser("plan-summary", help="plain per-step summary of the plan, to show the user before they approve"
                  ).set_defaults(fn=cmd_plan_summary)
    a = sp.add_parser("reviewers"); a.add_argument("cp"); a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_reviewers)
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
                                                                          "hooks-waive", "ui-respec", "guardrail"])
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
    if args.root:
        os.environ["RATCHET_ROOT"] = str(Path(args.root).resolve())
    args.fn(args)


if __name__ == "__main__":
    main()
