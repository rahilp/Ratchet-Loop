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
    # Adversarial reviewer roster. kind=agent → a ratchet-adversary sub-agent run by the host model;
    # kind=command → another provider's CLI, run by ratchet-adversarial-loop/scripts/external_review.py.
    "reviewers": {
        "host-standards":   {"kind": "agent", "lens": "standards", "vendor": "host"},
        "host-correctness": {"kind": "agent", "lens": "correctness", "vendor": "host"},
        "host-both":        {"kind": "agent", "lens": "both", "vendor": "host"},
        **{f"{p}-{lens}": {"kind": "command", "preset": p, "lens": lens, "vendor": v}
           for p, v in [("claude", "anthropic"), ("codex", "openai"), ("gemini", "google"), ("copilot", "github")]
           for lens in ("correctness", "standards")},
    },
    # Review SLOTS by checkpoint risk. Every slot must return zero findings.
    # A slot is one reviewer id, or an ordered list of alternatives: the first one AVAILABLE when the
    # review runs is used (not installed, recently failed, or same provider as the host = unavailable;
    # a provider not yet used in this round is preferred). Any slot of only other-provider reviewers
    # falls back to the host's own sub-agents, so a missing provider never stalls the loop.
    # Rigor never changes (every finding is fixed); only how many independent eyes look.
    "review_depth": {
        "low":    ["host-both"],
        "normal": ["host-standards", ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"]],
        "high":   ["host-standards", "host-correctness",
                   ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"],
                   ["gemini-standards", "claude-standards", "codex-standards", "copilot-standards"]],
    },
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


def fingerprint(root: Path) -> dict:
    """Hash of the working tree (tracked + untracked, respecting .gitignore, minus .ratchet)."""
    if _is_git(root):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
            # seed from HEAD if it exists so the add is fast
            subprocess.run(["git", "read-tree", "HEAD"], cwd=root, env=env,
                           capture_output=True)
            r = subprocess.run(["git", "add", "-A", "--", ".", f":(exclude){RATCHET_DIR}"],
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


def changed_files(root: Path, fp_a: dict, fp_b: dict) -> list[str]:
    if fp_a["id"] == fp_b["id"]:
        return []
    if fp_a["kind"] == fp_b["kind"] == "git-tree":
        r = subprocess.run(["git", "diff-tree", "-r", "--name-only", fp_a["id"], fp_b["id"]],
                           cwd=root, capture_output=True, text=True)
        if r.returncode == 0:
            return [l for l in r.stdout.splitlines() if l.strip()]
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


APPROVAL_PHRASES = [
    "approved", "approve", "approval granted", "lgtm", "looks good to me", "looks good", "looks great", "sounds good",
    "go ahead", "go for it", "good to go", "ship it", "build it", "do it", "let's go", "lets go", "proceed", "continue",
    "carry on", "confirmed", "confirm", "yes", "yep", "yeah", "yup", "yes please", "ok", "okay", "sure", "perfect",
    "great", "y", "go", "👍", "✅"]
BENIGN_PHRASES = [
    "no changes needed", "no changes", "no further changes", "no comments", "nothing to change", "nothing else",
    "i don't have any comments", "i don't have comments", "i dont have comments", "no notes", "all good",
    "thanks", "thank you", "thx", "please", "pls", "awesome", "nice", "cool", "for now", "from me", "as is"]
HEDGE_WORDS = {"no", "not", "don't", "dont", "wait", "hold", "stop", "but", "however", "except", "instead", "rather",
               "maybe", "probably", "guess", "think", "hmm", "unsure", "also", "add", "change", "remove", "drop",
               "after", "before", "first", "later", "read", "sec", "second", "minute", "moment", "lunch", "brb",
               "what", "why", "how", "who", "whom", "when", "which", "where", "if", "unless", "until",
               "otherwise", "kill", "fix", "tweak", "make", "move", "rename", "replace", "update", "adjust", "swap",
               "remove", "delete", "less", "more", "bigger", "smaller"}


def message_intent(text: str) -> str:
    """approve | unclear. A reply only counts as an approval if it is essentially nothing BUT an approval:
    no question, no hedge, no extra request (\"yes, and also add X\" is a change request, not a yes)."""
    t = " " + (text or "").strip().lower().replace("’", "'") + " "
    if not t.strip() or "?" in t:
        return "unclear"
    for ph in sorted(BENIGN_PHRASES, key=len, reverse=True):
        t = t.replace(" " + ph + " ", " ").replace(" " + ph + ",", " ").replace(" " + ph + ".", " ").replace(" " + ph + "!", " ")
    words = re.findall(r"[a-z0-9']+|👍|✅", t)
    if any(w in HEDGE_WORDS for w in words):
        return "unclear"
    rest = " " + " ".join(words) + " "
    found = False
    for ph in sorted(APPROVAL_PHRASES, key=len, reverse=True):
        if " " + ph + " " in rest:
            found = True
            rest = rest.replace(" " + ph + " ", " ")
    leftover = [w for w in rest.split() if w not in ("it", "the", "plan", "this", "that", "all", "then", "go", "on",
                                                    "ahead", "and", "a", "i", "me", "we", "you", "can", "now")]
    return "approve" if found and len(leftover) <= 3 else "unclear"


CANCEL_HINT_RE = re.compile(r"\b(cancel|abandon|scrap|kill)\b[^.?!]*\b(run|build|feature|ratchet|loop|work|whole thing|everything)\b"
                            r"|\bforget (it|the whole thing)\b|\bstart over\b", re.I)


RULE_KEYS = ["review_depth", "reviewers", "max_adversarial_rounds", "max_adversarial_rounds_run", "max_ui_rounds",
             "max_behavior_attempts", "ui_block_on_nits", "ui_globs", "regression_command", "standards",
             "fingerprint_include", "approver", "ui_single_reviewer_ok", "min_review_timeout_sec", "test_timeout_sec",
             "host_provider", "ui_reviewers", "design_md", "max_stop_blocks", "unavailable_ttl_hours",
             "verify_transcripts"]


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


def approval_snapshot(ctx, st) -> dict:
    plan = ctx.plan() or {"checkpoints": []}
    cfg = ctx.config()
    return {"plan": _plan_hash(plan, ctx),
            "cps": {c["id"]: _sha(c) for c in plan["checkpoints"]},
            "tests": {c["id"]: _sha(test_plan(ctx, c["id"])) for c in plan["checkpoints"]},
            "rules": {k: _sha(cfg.get(k)) for k in RULE_KEYS},
            "files": _reference_files(ctx),
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
    for k, h in snap.get("rules", {}).items():
        if now_["rules"].get(k) != h:
            out.append(f"config rule '{k}'")
    for f, h in snap.get("files", {}).items():
        if now_["files"].get(f) != h:
            out.append(f"reference file {f}")
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
        return {"phase": phase, "action": st.get("blocked_reason") or phase, "stop_ok": True}

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
    return {"phase": phase, "checkpoint": cur, "step": step, "action": msgs.get(step, step), "stop_ok": False}


# --------------------------------------------------------------------------- commands

def cmd_init(args):
    root = find_root()
    ctx = Ctx(root)
    ctx.ratchet.mkdir(exist_ok=True)
    if not ctx.config_path.exists():
        cfg = {k: DEFAULT_CONFIG[k] for k in ["human_review", "host_provider", "ui_reviewers", "regression_command",
                                               "commit_per_checkpoint", "design_md", "standards", "app"]}
        cfg["review_depth"] = default_review_depth()
        write_json(ctx.config_path, cfg)
        host = detect_host(cfg)
        found = [p for p, b in PRESET_BINARIES.items() if shutil.which(b) and PRESET_VENDORS[p] != host]
        print(f"Host provider: {host}" + (" (set host_provider in .ratchet/config.json)" if host == "unknown" else "")
              + ". Other-provider reviewers: " + (", ".join(found) + " — used first, host sub-agents as fallback"
              if found else "none found — the host's sub-agents review everything"))
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


PRESET_BINARIES = {"claude": "claude", "codex": "codex", "gemini": "gemini", "copilot": "copilot"}
PRESET_VENDORS = {"claude": "anthropic", "codex": "openai", "gemini": "google", "copilot": "github"}
HOST_HINTS = [  # environment variables the common agent CLIs set for the tools they run
    ("anthropic", ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")),
    ("openai", ("CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_SANDBOX_NETWORK_DISABLED")),
    ("google", ("GEMINI_CLI",)),
    ("github", ("COPILOT_CLI", "GITHUB_COPILOT_CLI")),
]


def detect_host(cfg=None) -> str:
    explicit = os.environ.get("RATCHET_HOST_PROVIDER") or (cfg or {}).get("host_provider", "auto")
    if explicit and explicit != "auto":
        return explicit
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


def reviewer_availability(ctx, cfg, rid) -> tuple[bool, str]:
    spec = (cfg.get("reviewers") or {}).get(rid)
    if spec is None:
        return False, "not defined in config 'reviewers'"
    if spec.get("kind") == "agent":
        return True, f"sub-agent of the host ({detect_host(cfg)})"
    host = detect_host(cfg)
    if spec.get("vendor") and spec.get("vendor") == host:
        return False, f"same provider as the host ({host}) — the host's own sub-agents cover it"
    binary = reviewer_binary(cfg, rid) or ""
    if not binary or not shutil.which(binary):
        return False, f"'{binary or spec.get('preset')}' CLI not installed"
    down = unavailable_reviewers(ctx).get(rid) if ctx else None
    if down:
        return False, f"failed recently ({down['reason']}); retry after {down['until'][:16]}Z"
    return True, f"{binary} on PATH"


def _host_fallbacks(cfg, rid) -> list[str]:
    """Same lens first, then the host's other reviewers, so a fallback keeps the reviewer COUNT."""
    lens = ((cfg.get("reviewers") or {}).get(rid) or {}).get("lens", "both")
    first = {"standards": "host-standards", "correctness": "host-correctness"}.get(lens, "host-both")
    rest = [r for r in ("host-both", "host-correctness", "host-standards") if r != first]
    return [first] + [r for r in rest if r in (cfg.get("reviewers") or {})]


def reviewer_binary(cfg, rid):
    spec = (cfg.get("reviewers") or {}).get(rid) or {}
    if spec.get("kind") != "command":
        return None
    return (spec.get("command") or [PRESET_BINARIES.get(spec.get("preset", ""), "")])[0] or None


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
    roster = cfg.get("reviewers") or {}
    chosen, out = set(), []
    for n, slot in enumerate(slots, 1):
        alts = list(slot) if isinstance(slot, list) else [slot]
        unknown = [a for a in alts if a not in roster]
        if unknown:
            die(f"review_depth/checkpoint names unknown reviewer(s) {unknown}; define them under config 'reviewers'.")
        if all(roster[a].get("kind") == "command" for a in alts):
            alts += _host_fallbacks(cfg, alts[0])
        skipped, reasons, pick, candidates = [], {}, None, []
        for a in alts:
            if a in chosen:
                continue
            okay, why = reviewer_availability(ctx, cfg, a)
            if okay:
                candidates.append(a)
            else:
                skipped.append(a)
                reasons[a] = why
        used_vendors = {roster[c].get("vendor") for c in chosen if roster[c].get("kind") == "command"}
        fresh = [c for c in candidates if roster[c].get("kind") == "command" and roster[c].get("vendor") not in used_vendors]
        pick = fresh[0] if fresh else (candidates[0] if candidates else None)
        if pick and candidates and pick != candidates[0]:
            skipped.append(candidates[0])
            reasons[candidates[0]] = "provider already reviewing this round; preferring a different one"
        if pick is None:
            continue  # every alternative already used or down: slot collapses into the others
        chosen.add(pick)
        out.append({"id": pick, "slot": n, "vendor": roster[pick].get("vendor"),
                    "fallback_for": skipped, "reasons": reasons})
    if not out:
        die("No adversarial reviewer is available for this checkpoint — check config 'reviewers'.")
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
    errs = validate_plan(plan)
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
    return "|".join([str(ctx.root.resolve()), st["slug"], st["phase"], str(st.get("awaiting_since")),
                     str(st.get("plan_hash")), action])


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


def _transcript_ok(entry: dict, since: str | None) -> tuple[bool, str]:
    """Is this logged message a real user turn in the agent CLI's own transcript, typed after `since`?"""
    tp = entry.get("transcript_path")
    if entry.get("host") == "copilot" and not tp:
        return True, "copilot hooks carry no transcript"
    if not tp or not Path(tp).exists():
        return False, "no transcript to check it against"
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8_000_000))
            text = f.read().decode("utf-8", "replace")
    except OSError as e:
        return False, f"transcript unreadable ({e})"
    needle = (entry.get("prompt") or "").strip()[:160]
    if not needle:
        return False, "empty message"
    esc = json.dumps(needle)[1:-1]
    for line in text.splitlines():
        if needle not in line and esc not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return True, "found in transcript"
        role = obj.get("type") or obj.get("role") or (obj.get("message") or {}).get("role")
        if role not in (None, "user", "human"):
            continue
        ts = obj.get("timestamp") or (obj.get("message") or {}).get("timestamp")
        if ts and since and _iso(ts) < _iso(since) - 90:
            continue  # an old message replayed as a new one
        return True, "found in transcript"
    return False, "not found as a recent user turn in the transcript"


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
    msgs = _human_since(ctx, st.get("awaiting_since"))
    if msgs:
        entry = msgs[-1]
        last = entry.get("prompt", "")
        if action != "feedback" and message_intent(last) != "approve":
            die(f"The user's latest reply is not a clear yes: \"{last[:120]}\". Answer it or make the change they asked "
                "for, then ask them plainly (e.g. \"Shall I go ahead? Reply yes to continue.\") and try again after "
                "they reply.")
        if ctx.config().get("verify_transcripts", True):
            good, why = _transcript_ok(entry, st.get("awaiting_since"))
            if not good:
                die(f"Couldn't confirm that reply came from the user ({why}). Ask the user to reply again in this "
                    "chat; if it keeps failing, `ratchet.py doctor` shows whether the prompt hook is installed.")
        return {"by": "human", "detail": last[:200], "session": entry.get("session")}
    die("No human message recorded since the loop paused. Approval must come from the user — "
        "stop and wait for them. (Requires the UserPromptSubmit hook; see README.)")


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
            "cancel": ["planning", "awaiting_plan_approval", "building", "blocked", "paused",
                       "awaiting_checkpoint_review", "awaiting_final_review", "planning_feedback"],
            "set-review": ["planning", "awaiting_plan_approval", "building", "blocked", "paused",
                           "awaiting_checkpoint_review", "awaiting_final_review"]}[args.action]
    if st["phase"] not in want:
        die(f"Run {st['slug']} is in phase {st['phase']}; {args.action} applies to {want}.")
    print(_sign(_director_key(), _approval_payload(ctx, st, args.action)))


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
    cps["step"] = "implement"
    ctx.log(st, "red", cp=args.cp, exit=code)
    ctx.save_state(st)
    ok(f"{args.cp}: red run recorded (exit {code}). Next: implement.")


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
    fp = fingerprint(ctx.root)
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
    cps["step"] = _earliest_open_step(cps)
    ctx.log(st, "ui_passed", cp=args.cp, round=g["attempts"])
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"Gate 2 (UI) PASSED for {args.cp}. Next step: {cps['step']}")


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
        spec = (cfg.get("reviewers") or {}).get(rid, {})
        findings = r.get("findings", []) or []
        all_findings += [dict(f, reviewer=rid) for f in findings]
        verdicts.append({"reviewer": rid, "vendor": spec.get("vendor"), "claimed": r.get("verdict"),
                         "findings": len(findings)})
        if Path(rp).resolve().parent != dest.resolve():
            shutil.copy2(rp, dest / f"{rid}.json")
    need = len(required)
    approved = not all_findings
    g["rounds"] = g.get("rounds", []) + [{"at": now(), "fingerprint": fp["id"], "verdicts": verdicts,
                                          "findings": len(all_findings), "substitutions": subs}]
    if subs:
        ctx.log(st, "reviewer_fallback", cp=args.cp,
                detail="; ".join(f"{x['used']} instead of {', '.join(x['instead_of'])}" for x in subs))
    if not approved:
        g["status"] = "failed"
        if g["attempts"] >= cfg["max_adversarial_rounds"]:
            _set_blocked(st, f"{args.cp}: adversarial review still has {len(all_findings)} finding(s) after "
                             f"{g['attempts']} rounds — needs a human/director decision.")
        ctx.log(st, "adversarial_changes_requested", cp=args.cp, findings=len(all_findings))
        ctx.save_state(st)
        build_viewer(ctx)
        write_json(dest / "open-findings.json", all_findings)
        die(f"Gate 3 (adversarial) NOT approved: {len(all_findings)} finding(s) → "
            f"{(dest / 'open-findings.json').relative_to(ctx.root)}. Spawn ratchet-fixer with that file, "
            "re-run behavior (and UI if visible code changed), then re-run the adversaries.", 2)
    g.update(status="passed", fingerprint=fp, passed_at=now(), evidence=str(dest.relative_to(ctx.root)))
    cps["step"] = _earliest_open_step(cps)
    ctx.log(st, "adversarial_approved", cp=args.cp, round=g["attempts"])
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"Gate 3 (adversarial) APPROVED by all {need} reviewer(s) for {args.cp}. Next: ratchet.py complete {args.cp}")


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
    commit = None
    if cfg.get("commit_per_checkpoint") and _is_git(ctx.root):
        subprocess.run(["git", "add", "-A"], cwd=ctx.root, capture_output=True)
        msg = f"ratchet({st['slug']}): {args.cp} {cp['title']}\n\n{cp['summary']}\n\nGates: " + ", ".join(
            f"{n}={cps['gates'][n]['status']}" for n in ["behavior", "ui", "adversarial"])
        r = subprocess.run(["git", "commit", "-m", msg],
                           cwd=ctx.root, capture_output=True, text=True)
        if r.returncode == 0:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ctx.root,
                                    capture_output=True, text=True).stdout.strip()
        elif "nothing to commit" not in (r.stdout + r.stderr):
            die(f"git commit failed: {(r.stderr or r.stdout).strip()[:400]}")
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
            register_active(ctx.root, None)
    ctx.save_state(st)
    build_viewer(ctx)
    ok(f"{args.cp} complete{f' (commit {commit})' if commit else ''}.")
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
           "claim": "continue this Ratchet run in THIS chat"}


def cmd_ask(args):
    """Record the question the agent is about to ask the user. Only a clear yes typed AFTER this counts."""
    ctx = Ctx(find_root())
    st = ctx.state()
    if args.action == "set-review" and not args.mode:
        die("ask set-review needs the mode: end | each | none")
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
    asked = now()
    st["pending"] = {"action": args.action, "mode": args.mode, "asked_at": asked, "prev_awaiting": st.get("awaiting_since")}
    st["awaiting_since"] = asked
    ctx.log(st, "question_asked", action=args.action, mode=args.mode)
    ctx.save_state(st)
    q = f"{detail} Shall I {ASKABLE[args.action]}{' → ' + args.mode if args.mode else ''}? Reply yes to confirm."
    print(f"ASK THE USER: {q.strip()}")


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
    if not getattr(args, "director_token", None):
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
    if not getattr(args, "director_token", None):
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
    if not getattr(args, "director_token", None):
        _require_pending(st, "approve-changes")
    msg = _require_human(ctx, st, "approve-changes", getattr(args, "director_token", None))
    st["approved"] = approval_snapshot(ctx, st)
    _clear_pending(st)
    ctx.log(st, "changes_approved", by=msg["by"], changed="; ".join(probs)[:400])
    ctx.save_state(st)
    build_viewer(ctx)
    ok("Accepted as the new baseline: " + "; ".join(probs))


def cmd_next(args):
    ctx = Ctx(find_root())
    na = next_action(ctx)
    hs = hooks_status(ctx.root, ctx.config())
    hw = hooks_warning(ctx, hs)
    if hw and na.get("phase") not in ("planning",):
        na["warning"] = hw
    na["hooks"] = hs
    print(json.dumps(na, indent=2))


def cmd_status(args):
    ctx = Ctx(find_root())
    if getattr(args, "json", False):
        print(json.dumps(status_json(ctx), indent=2))
        return
    st = ctx.state()
    plan = ctx.plan() or {"checkpoints": []}
    print(f"Run: {st['slug']}  —  {st['feature']}")
    print(f"Phase: {st['phase']}   Current: {st.get('current') or '-'}   Human review: {st.get('human_review')}")
    sym = {"passed": "✓", "failed": "✗", "pending": "·", "skipped": "–", "stale": "~", "invalid": "?"}
    for cp in plan["checkpoints"]:
        s = st["checkpoints"].get(cp["id"])
        if not s:
            print(f"  {cp['id']:<5} (not submitted) {cp['title']}")
            continue
        gates = " ".join(f"{n[0].upper()}{sym.get(s['gates'][n]['status'], '?')}" for n in GATES)
        print(f"  {cp['id']:<5} {s['status']:<11} [{gates}] step={s['step']:<11} {cp['title']}")
    na = next_action(ctx, st)
    print(f"\nNext: {na['action']}")


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
    rows = []
    for res_r in rnd["resolved"]:
        rid = res_r["id"]
        spec = dict(cfg["reviewers"][rid], id=rid, output=str(out_dir / f"{rid}.json"),
                    slot=res_r["slot"], fallback_for=res_r["fallback_for"], reasons=res_r["reasons"])
        if spec["kind"] == "agent":
            spec["how"] = (f"Spawn a ratchet-adversary sub-agent: lens={spec['lens']}, reviewer id '{rid}', "
                           f"fingerprint {fp}, round_nonce {rnd['nonce']}, checkpoint {args.cp}, output {spec['output']}"
                           + (f", fixes.md {fixes}" if rnd["round"] > 1 else ""))
        else:
            spec["how"] = f"python3 {ext_script} --reviewer {rid} --cp {args.cp} --out {spec['output']}{fixes_arg}"
        rows.append(spec)
    res = {"checkpoint": args.cp, "risk": cp.get("risk", "normal"), "round": rnd["round"], "fingerprint": fp,
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
    for rid, spec in (cfg.get("reviewers") or {}).items():
        okay, why = reviewer_availability(ctx, cfg, rid)
        print(f"  {'✓' if okay else '·'} {rid:<22} {spec.get('vendor', ''):<10} {why}")


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
    hs = hooks_status(ctx.root, ctx.config())
    hw = hooks_warning(ctx, hs)
    waiting_on = None
    if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review", "blocked"):
        waiting_on = "director" if approval_mode(ctx) == "director" else "human"
    return {"root": str(ctx.root), "slug": st["slug"], "feature": st["feature"], "phase": st["phase"],
            "current": st.get("current"), "step": na.get("step"), "next": na["action"], "stop_ok": na["stop_ok"],
            "waiting_on": waiting_on, "blocked_reason": st.get("blocked_reason"),
            "awaiting_since": st.get("awaiting_since"), "approval_mode": approval_mode(ctx),
            "adv_rounds_used": st.get("adv_rounds_total", 0),
            "unavailable_reviewers": unavailable_reviewers(ctx), "host_provider": detect_host(ctx.config()),
            "hooks_warning": hw, "hook_heartbeat": read_heartbeat(ctx.root),
            "hooks": hs,
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
        import http.server
        token = os.environ.get("RATCHET_VIEWER_TOKEN") or None
        srv = http.server.ThreadingHTTPServer((args.bind, args.port), _make_handler(_dashboard_routes(base), token))
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
    return {"state": {k: v for k, v in st.items() if k not in ("history", "approved")},
            "prototype": (ctx.run_dir() / "reference" / "prototype.html").exists(),
            "history": st.get("history", [])[-40:], "plan": plan, "tests": tests,
            "learnings": learn.read_text() if learn.exists() else "",
            "next": next_action(ctx, st), "generated_at": now()}


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
        import http.server
        token = os.environ.get("RATCHET_VIEWER_TOKEN") or None
        handler = _make_handler(_viewer_routes(lambda: Ctx(find_root())), token)
        srv = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
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
<td>${l.waiting_on?`<span class="flag">⚑ ${esc(l.waiting_on)}</span>`:""}${l.blocked_reason?`<div class="mono">${esc(l.blocked_reason)}</div>`:""}${l.hooks_warning?`<div class="mono">hooks not firing</div>`:""}</td>
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
            "transcript_path": data.get("transcript_path") or data.get("transcriptPath")}


def _heartbeat(root: Path, event: str, host: str):
    p = root / RATCHET_DIR / "hook-heartbeat.json"
    try:
        hb = read_json(p, {}) or {}
        hb[event] = {"at": now(), "host": host}
        p.write_text(json.dumps(hb))
    except Exception:
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
ENGINE_CALL_RE = re.compile(r"""^\s*(?:\S*python3?(?:\.\d+)?|py)\s+(?:-\S+\s+)*["']?\S*(ratchet|external_review|ui_review)\.py\b""")
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


def _write_allowed(path: str, cwd: str) -> bool:
    rel = _ratchet_rel(path, cwd)
    return rel is None or bool(rel and ALLOWED_RATCHET_WRITES.match(rel))


def _guard_shell(cmd: str, cwd: str) -> str | None:
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
    if (root / RATCHET_DIR).is_dir():
        _heartbeat(root, "guard", n["host"])
    if n["kind"] == "shell" and cmd:
        why = _guard_shell(cmd, cwd)
        if why:
            return True, why
    if n["kind"] == "write":
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
    try:
        n = normalize_hook(data, host)
        block, msg = {"stop": decide_stop, "prompt": decide_prompt, "guard": decide_guard}[event](n)
        return emit_hook(host, event, block, msg)
    except (Exception, SystemExit) as e:  # guard/prompt fail OPEN — never brick the host session
        root = None
        try:
            root = find_root(Path(data.get("cwd") or os.getcwd()))
            if (root / RATCHET_DIR).is_dir():
                with (root / RATCHET_DIR / "hook-errors.log").open("a") as f:
                    f.write(f"{now()} {host} {event}: {type(e).__name__}: {e}\n")
        except Exception:
            pass
        if event == "stop" and root is not None and (root / RATCHET_DIR / "active.json").exists():
            try:  # stop fails CLOSED (a few times) — a corrupted state file must not end the loop quietly
                block, msg = _stop_error(root, f"Ratchet could not read its run state ({type(e).__name__}: {e}).")
                return emit_hook(host, event, block, msg)
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
        print(f"  {h:<8} " + ("; ".join(found) if found else "not installed — ratchet.py install-hooks --host " + h))
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
              " install the hooks for your agent CLI and restart it.")
    errs = root / RATCHET_DIR / "hook-errors.log"
    if errs.exists():
        tail = errs.read_text().strip().splitlines()[-3:]
        print("  recent hook errors (hooks failed open):\n    " + "\n    ".join(tail))


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
    return {"host": host, "installed": installed, "heartbeat_seen": bool(entries), "fresh": fresh,
            "missing": not installed and not fresh}


def hooks_warning(ctx, hs=None) -> str | None:
    """Used by `next`/status: gates without hooks are just advice. Phrased for the agent, not the user."""
    hs = hs or hooks_status(ctx.root, ctx.config())
    if hs["fresh"]:
        return None
    if hs["installed"]:
        return ("Ratchet hooks are installed for this agent CLI but none has fired recently. Tell the user the "
                "agent CLI probably needs a restart (and, for Codex, hooks enabled) so the gates are enforced.")
    return ("No Ratchet hook has fired in this project yet, so the gates are only advice. Offer to install the "
            "hooks for this agent CLI (`ratchet.py install-hooks`), ask the user first, and tell them a restart "
            "is needed afterwards. `ratchet.py doctor` still shows details.")



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
                                  "set-review, approve-changes, resume --claim)")
    a.add_argument("action", choices=list(ASKABLE)); a.add_argument("mode", nargs="?", choices=["end", "each", "none"])
    a.set_defaults(fn=cmd_ask)
    sp.add_parser("accept-state", help=argparse.SUPPRESS).set_defaults(fn=cmd_accept_state)
    a = sp.add_parser("cancel", help="abandon the active run (needs the user's confirmation)")
    a.add_argument("--reason"); a.add_argument("--director-token"); a.set_defaults(fn=cmd_cancel)
    a = sp.add_parser("set-review", help="change how often the user reviews (needs their confirmation)")
    a.add_argument("mode", choices=["end", "each", "none"]); a.add_argument("--director-token")
    a.set_defaults(fn=cmd_set_review)
    a = sp.add_parser("approve-changes", help="accept a user-confirmed change to the approved plan/tests/rules")
    a.add_argument("--director-token"); a.set_defaults(fn=cmd_approve_changes)
    sp.add_parser("next").set_defaults(fn=cmd_next)
    a = sp.add_parser("status"); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_status)
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
                                                                          "approve-changes", "cancel", "set-review"])
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
