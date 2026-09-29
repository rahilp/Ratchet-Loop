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
    "max_stop_blocks": 40,          # safety valve for the Stop hook
}

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
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        die(f"{path} is not valid JSON: {e}")


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
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
        st = read_json(self.state_path(slug))
        if st is None:
            die("state.json missing for active run")
        return st

    def save_state(self, st):
        st["updated_at"] = now()
        write_json(self.state_path(st["slug"]), st)

    def plan(self, slug=None):
        return read_json(self.run_dir(slug) / "checkpoints.json")

    def evidence(self, cp_id: str, slug=None) -> Path:
        d = self.run_dir(slug) / "evidence" / cp_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log(self, st, event: str, **data):
        st.setdefault("history", []).append({"at": now(), "event": event, **data})


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
        return {"phase": phase, "action": "Run ratchet.py plan-submit (validates checkpoints, builds viewer). "
                "Spawn ratchet-test-planner for each checkpoint in parallel.", "stop_ok": False}
    if phase == "awaiting_plan_approval":
        missing = [c["id"] for c in ctx.plan()["checkpoints"]
                   if c["gates"].get("behavior") and not test_plan(ctx, c["id"])]
        extra = f" (test plans still missing for {', '.join(missing)} — spawn ratchet-test-planner)" if missing else ""
        return {"phase": phase, "action": "Waiting for the human to approve the checkpoint plan in the viewer"
                + extra, "stop_ok": not missing}
    if phase in ("awaiting_checkpoint_review", "awaiting_final_review"):
        return {"phase": phase, "action": "Waiting for the human to review. On approval run ratchet.py "
                "human-approve; on feedback run ratchet.py feedback.", "stop_ok": True}
    if phase == "planning_feedback":
        return {"phase": phase, "action": "Spawn ratchet-checkpoint-planner in EXTEND mode with the human's feedback "
                "(append new checkpoints, never edit completed ones), ratchet-test-planner for each new checkpoint, "
                "then run ratchet.py plan-extend", "stop_ok": False}
    if phase in ("blocked", "paused", "done"):
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
    if not gi.exists():
        gi.write_text("manifests/\nhuman_log.jsonl\nruns/*/evidence/**/*.png\nruns/*/viewer.html\n"
                      "viewer.json\nviewer.log\nhook-heartbeat.json\nhook-errors.log\nunavailable-reviewers.json\n")
    existing = ctx.active_slug()
    if existing and not args.force:
        st = read_json(ctx.state_path(existing))
        if st and st.get("phase") not in ("done",):
            die(f"Run '{existing}' is still active (phase {st['phase']}). Finish it, "
                f"or pass --force to start a new one.")
    slug = args.slug or slugify(args.feature)
    rd = ctx.ratchet / "runs" / slug
    if rd.exists() and not args.force:
        die(f"Run {slug} already exists. Use --slug to pick another name or --force.")
    (rd / "tests").mkdir(parents=True, exist_ok=True)
    (rd / "evidence").mkdir(exist_ok=True)
    st = {
        "slug": slug, "feature": args.feature, "phase": "planning", "created_at": now(),
        "human_review": args.human_review or ctx.config()["human_review"],
        "current": None, "checkpoints": {}, "plan_hash": None, "awaiting_since": None,
        "stop_blocks": 0, "history": [],
    }
    write_json(rd / "state.json", st)
    write_json(ctx.ratchet / "active.json", {"slug": slug})
    ok(f"Initialised run '{slug}' at {rd.relative_to(root)}")
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


def _plan_hash(plan):
    return hashlib.sha1(json.dumps(plan.get("checkpoints"), sort_keys=True).encode()).hexdigest()[:12]


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
    st["plan_hash"] = _plan_hash(plan)
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


def _human_since(ctx: Ctx, since_iso: str | None):
    log = ctx.ratchet / "human_log.jsonl"
    if not log.exists() or not since_iso:
        return []
    out = []
    for line in log.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("at", "") > since_iso:
            out.append(e)
    return out


# ---- director-signed approvals -------------------------------------------------------
# A director (an orchestrator running many lanes) holds a secret key OUTSIDE every repo.
# It signs one specific pause of one specific run; the lane verifies the signature.
# Builder sessions cannot read the key or call the signing commands (PreToolUse guard),
# and an enrolled lane stops accepting chat messages as approvals — because in a
# director setup the "user" messages a builder receives come from the director.

def director_key_path() -> Path:
    return Path(os.environ.get("RATCHET_DIRECTOR_KEY_FILE") or
                Path.home() / ".config" / "ratchet" / "director.key")


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


def approval_mode(ctx) -> str:
    enr = read_json(ctx.ratchet / "director.json")
    if enr:
        key = _director_key(required=False)
        if key and hmac.compare_digest(enr.get("sig", ""), _sign(key, "enroll|" + str(ctx.root.resolve()))):
            return "director"
        if key:
            die(".ratchet/director.json has an invalid signature — re-enroll this lane from the director.")
        return "director"  # enrolled, key not on this machine: still refuse chat approvals
    return ctx.config().get("approver", "human")


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
    msgs = _human_since(ctx, st.get("awaiting_since"))
    if msgs:
        return {"by": "human", "detail": msgs[-1].get("prompt", "")[:200]}
    if os.environ.get("RATCHET_HUMAN_OVERRIDE") == "1":
        return {"by": "human", "detail": "(RATCHET_HUMAN_OVERRIDE)"}
    die("No human message recorded since the loop paused. Approval must come from the user — "
        "stop and wait for them. (Requires the UserPromptSubmit hook; see README.)")


def _require_human(ctx, st, action="human-approve", token=None):
    a = _require_approval(ctx, st, action, token)
    return {"prompt": a["detail"], "by": a["by"]}


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
    write_json(ctx.ratchet / "director.json", {"enrolled_at": now(),
                                               "sig": _sign(key, "enroll|" + str(ctx.root.resolve()))})
    ok(f"Lane {ctx.root} enrolled: approvals now require a director token.")


def cmd_director_sign(args):
    if not _is_director_session():
        die("director-sign must be run from the director's session (RATCHET_ROLE=director).")
    ctx = Ctx(find_root())
    st = ctx.state()
    want = {"approve-plan": ["awaiting_plan_approval"],
            "human-approve": ["awaiting_checkpoint_review", "awaiting_final_review", "blocked"],
            "feedback": ["awaiting_checkpoint_review", "awaiting_final_review", "done"]}[args.action]
    if st["phase"] not in want:
        die(f"Run {st['slug']} is in phase {st['phase']}; {args.action} applies to {want}.")
    print(_sign(_director_key(), _approval_payload(ctx, st, args.action)))


def cmd_approve_plan(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] != "awaiting_plan_approval":
        die(f"Nothing to approve (phase {st['phase']})")
    plan = ctx.plan()
    if _plan_hash(plan) != st["plan_hash"]:
        die("checkpoints.json changed since it was shown to the human. Run plan-submit again and re-review.")
    missing = [c["id"] for c in plan["checkpoints"] if c["gates"].get("behavior") and not test_plan(ctx, c["id"])]
    if missing:
        die(f"Test plans missing for {', '.join(missing)}. Spawn ratchet-test-planner first.")
    msg = _require_human(ctx, st, "approve-plan", getattr(args, "director_token", None))
    st["phase"] = "building"
    st["current"] = plan["checkpoints"][0]["id"]
    first = st["checkpoints"][st["current"]]
    first["status"] = "in_progress"
    first["started_at"] = now()
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
    missing = [c["id"] for c in _planned_cases(ctx, cp["id"]) if c["id"] not in blob]
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
        if cps["step"] == "behavior" and g["attempts"] >= cfg["max_behavior_attempts"]:
            st["phase"] = "blocked"
            st["blocked_reason"] = f"{args.cp}: behavior gate failed {g['attempts']} times — needs a human."
            st["awaiting_since"] = now()
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
        if not fixable:
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
    if len(review.get("reviewers", [])) < 2 and not args.single_reviewer_ok:
        die("UI gate needs both a visual and a behaviour reviewer in the combined review.")
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
            st["phase"] = "blocked"
            st["blocked_reason"] = f"{args.cp}: UI gate failed {g['attempts']} rounds — needs a human."
            st["awaiting_since"] = now()
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
    resolved = resolve_reviewers(ctx, cfg, cp)
    required = [r["id"] for r in resolved]
    subs = [{"used": r["id"], "instead_of": r["fallback_for"], "why": r["reasons"]} for r in resolved if r["fallback_for"]]
    g = cps["gates"]["adversarial"]
    fp = fingerprint(ctx.root)
    reviews = {}
    for rp in args.reviews:
        r = read_json(Path(rp))
        if r is None:
            die(f"Review not found: {rp}")
        rid = r.get("reviewer") or Path(rp).stem
        if not r.get("fingerprint"):
            die(f"{rp} has no 'fingerprint'. Every review must be stamped with `ratchet.py fingerprint` "
                "taken before the reviewer started.")
        if r["fingerprint"] != fp["id"]:
            die(f"{rp} ({rid}) reviewed code {r['fingerprint'][:10]} but code is now {fp['id'][:10]}. "
                "Re-run that reviewer on the current code.")
        reviews[rid] = (rp, r)
    missing = [i for i in required if i not in reviews]
    if missing:
        die(f"{args.cp} (risk={cp.get('risk', 'normal')}) needs reviews from {required}; missing {missing}. "
            f"Run `ratchet.py reviewers {args.cp}` to see how to launch each one.")
    g["attempts"] += 1
    st["adv_rounds_total"] = int(st.get("adv_rounds_total", 0)) + 1
    dest = ctx.evidence(args.cp) / "adversarial" / f"round-{g['attempts']:02d}"
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
            st["phase"] = "blocked"
            st["blocked_reason"] = (f"{args.cp}: adversarial review still has {len(all_findings)} finding(s) after "
                                    f"{g['attempts']} rounds — needs a human/director decision.")
            st["awaiting_since"] = now()
        elif st["adv_rounds_total"] >= cfg["max_adversarial_rounds_run"]:
            st["phase"] = "blocked"
            st["blocked_reason"] = (f"Run-wide adversarial round cap reached ({st['adv_rounds_total']}/"
                                    f"{cfg['max_adversarial_rounds_run']}) — escalating instead of burning more "
                                    "review quota. Raise the cap or lower checkpoint risk to continue.")
            st["awaiting_since"] = now()
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
    commit = None
    if cfg.get("commit_per_checkpoint") and _is_git(ctx.root):
        subprocess.run(["git", "add", "-A"], cwd=ctx.root, capture_output=True)
        msg = f"ratchet({st['slug']}): {args.cp} {cp['title']}\n\n{cp['summary']}\n\nGates: " + ", ".join(
            f"{n}={cps['gates'][n]['status']}" for n in ["behavior", "ui", "adversarial"])
        r = subprocess.run(["git", "commit", "-m", msg, "--no-verify"] if args.no_verify else ["git", "commit", "-m", msg],
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
        _advance(st, remaining[0])
    else:
        st["current"] = None
        st["phase"] = "awaiting_final_review" if mode != "none" else "done"
        st["awaiting_since"] = now()
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


def _advance(st, cp_id):
    st["current"] = cp_id
    st["phase"] = "building"
    st["checkpoints"][cp_id]["status"] = "in_progress"
    st["checkpoints"][cp_id]["started_at"] = now()


def cmd_human_approve(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] not in ("awaiting_checkpoint_review", "awaiting_final_review", "blocked"):
        die(f"Nothing awaiting human approval (phase {st['phase']})")
    msg = _require_human(ctx, st, "human-approve", getattr(args, "director_token", None))
    order = [c["id"] for c in ctx.plan()["checkpoints"]]
    if st["phase"] == "blocked":
        st["phase"] = "building"
        st["blocked_reason"] = None
        # a human unblock resets the attempt counters on the current checkpoint's failing gates
        for g in st["checkpoints"][st["current"]]["gates"].values():
            if g["status"] in ("failed", "invalid", "stale"):
                g["attempts"] = 0
        ctx.log(st, "unblocked", by=msg["by"], note=msg.get("prompt", "")[:200])
    else:
        target = st.get("review_target")
        done_ids = [target] if target else [c for c in order if st["checkpoints"][c]["status"] == "done"]
        for cid in done_ids:
            st["checkpoints"][cid]["gates"]["human"].update(status="passed", passed_at=now(), by=msg["by"])
        remaining = [c for c in order if st["checkpoints"][c]["status"] != "done"]
        st["review_target"] = None
        if remaining:
            _advance(st, remaining[0])
        else:
            st["phase"] = "done"
            st["current"] = None
        ctx.log(st, "approved", by=msg["by"], note=msg.get("prompt", "")[:200])
    st["awaiting_since"] = None
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
    missing = [c for c in new_ids if cp_by_id(plan, c)["gates"].get("behavior") and not test_plan(ctx, c)]
    if missing:
        die(f"Test plans missing for new checkpoints {missing}")
    for cid in new_ids:
        st["checkpoints"][cid] = new_cp_state(cp_by_id(plan, cid))
    st["plan_hash"] = _plan_hash(plan)
    _advance(st, new_ids[0])
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
    st["paused_from"] = st["phase"]
    st["phase"] = "paused"
    st["blocked_reason"] = args.reason or "paused"
    ctx.log(st, "paused", reason=args.reason)
    ctx.save_state(st)
    ok("Paused. The Stop hook will let the agent stop. Resume with ratchet.py resume.")


def cmd_resume(args):
    ctx = Ctx(find_root())
    st = ctx.state()
    if st["phase"] != "paused":
        die("Not paused")
    st["phase"] = st.pop("paused_from", "building")
    st["blocked_reason"] = None
    st["stop_blocks"] = 0
    ctx.save_state(st)
    ok(f"Resumed in phase {st['phase']}")
    print(json.dumps(next_action(ctx, st), indent=2))


def cmd_next(args):
    ctx = Ctx(find_root())
    na = next_action(ctx)
    hw = hooks_warning(ctx)
    if hw and na.get("phase") not in ("planning",):
        na["warning"] = hw
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
    ctx = Ctx(find_root())
    cfg = ctx.config()
    st = ctx.state()
    cp = cp_by_id(ctx.plan(), args.cp)
    cps = st["checkpoints"].get(args.cp, {})
    rnd = (cps.get("gates", {}).get("adversarial", {}).get("attempts", 0) or 0) + 1
    out_dir = ctx.run_dir() / "reviews" / args.cp / f"adv-round-{rnd:02d}"
    fp = fingerprint(ctx.root)["id"]
    ext_script = SKILL_DIR.parent / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
    rows = []
    for res_r in resolve_reviewers(ctx, cfg, cp):
        rid = res_r["id"]
        spec = dict(cfg["reviewers"][rid], id=rid, output=str(out_dir / f"{rid}.json"),
                    slot=res_r["slot"], fallback_for=res_r["fallback_for"], reasons=res_r["reasons"])
        if spec["kind"] == "agent":
            spec["how"] = (f"Spawn ratchet-adversary sub-agent with lens={spec['lens']}, reviewer id '{rid}', "
                           f"fingerprint {fp}, output {spec['output']}")
        else:
            spec["how"] = f"python3 {ext_script} --reviewer {rid} --cp {args.cp} --out {spec['output']}"
        rows.append(spec)
    res = {"checkpoint": args.cp, "risk": cp.get("risk", "normal"), "round": rnd, "fingerprint": fp,
           "run_rounds_used": st.get("adv_rounds_total", 0), "run_rounds_cap": cfg["max_adversarial_rounds_run"],
           "reviewers": rows}
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"{args.cp} risk={res['risk']} round={rnd} fingerprint={fp[:12]} "
          f"(run rounds {res['run_rounds_used']}/{res['run_rounds_cap']})")
    for r in rows:
        print(f"  - {r['id']:<22} [{r['vendor']}/{r['lens']}] {r['how']}")
        for skipped in r["fallback_for"]:
            print(f"      (instead of {skipped}: {r['reasons'][skipped]})")


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
    hw = hooks_warning(ctx)
    waiting_on = None
    if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review", "awaiting_final_review", "blocked"):
        waiting_on = "director" if approval_mode(ctx) == "director" else "human"
    return {"root": str(ctx.root), "slug": st["slug"], "feature": st["feature"], "phase": st["phase"],
            "current": st.get("current"), "step": na.get("step"), "next": na["action"], "stop_ok": na["stop_ok"],
            "waiting_on": waiting_on, "blocked_reason": st.get("blocked_reason"),
            "awaiting_since": st.get("awaiting_since"), "approval_mode": approval_mode(ctx),
            "adv_rounds_used": st.get("adv_rounds_total", 0),
            "unavailable_reviewers": unavailable_reviewers(ctx), "host_provider": detect_host(ctx.config()),
            "hooks_warning": hw, "hook_heartbeat": read_json(ctx.ratchet / "hook-heartbeat.json", {}),
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
    return {"state": {k: v for k, v in st.items() if k != "history"},
            "history": st.get("history", [])[-40:], "plan": plan, "tests": tests,
            "learnings": learn.read_text() if learn.exists() else "",
            "next": next_action(ctx, st), "generated_at": now()}


def build_viewer(ctx: Ctx) -> Path:
    rd = ctx.run_dir()
    data = viewer_data(ctx)
    write_json(rd / "viewer-data.json", data)
    if not VIEWER_TEMPLATE.exists():
        return rd / "viewer-data.json"
    html = VIEWER_TEMPLATE.read_text()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__RATCHET_DATA__*/null", payload)
    out = rd / "viewer.html"
    out.write_text(html)
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
                if not hmac.compare_digest(given, token):
                    return self._send(403, "text/plain", b"Missing or wrong access token (?t=...) for this Ratchet viewer.")
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
            "prompt": data.get("prompt") or "", "session": data.get("session_id") or data.get("sessionId")}


def _heartbeat(root: Path, event: str, host: str):
    p = root / RATCHET_DIR / "hook-heartbeat.json"
    try:
        hb = read_json(p, {}) or {}
        hb[event] = {"at": now(), "host": host}
        p.write_text(json.dumps(hb))
    except Exception:
        pass


def decide_stop(n: dict) -> tuple[bool, str]:
    root = find_root(Path(n["cwd"]))
    ctx = Ctx(root)
    if not ctx.ratchet.is_dir() or not ctx.active_slug():
        return False, ""
    _heartbeat(root, "stop", n["host"])
    st = read_json(ctx.state_path(), None)
    if not st:
        return False, ""
    na = next_action(ctx, st)
    if na.get("stop_ok"):
        if st.get("stop_blocks"):
            st["stop_blocks"] = 0
            ctx.save_state(st)
        return False, ""
    cfg = ctx.config()
    st["stop_blocks"] = int(st.get("stop_blocks", 0)) + 1
    if st["stop_blocks"] > cfg["max_stop_blocks"]:
        st["phase"] = "blocked"
        st["blocked_reason"] = (f"Stop hook fired {st['stop_blocks']} times without progress — "
                                "loop halted for human inspection.")
        st["awaiting_since"] = now()
        ctx.save_state(st)
        return False, f"RATCHET: safety valve — {st['blocked_reason']}"
    ctx.save_state(st)
    return True, ("RATCHET GATE: the work is not done, so you may not stop yet.\n"
                  f"Run: {st['slug']}  phase={na['phase']}"
                  + (f"  checkpoint={na.get('checkpoint')}  step={na.get('step')}" if na.get("checkpoint") else "")
                  + f"\nNext required action: {na['action']}\n"
                  "Gates cannot be skipped or argued past. If you are genuinely stuck, run "
                  "`ratchet.py pause --reason \"...\"` and explain the blocker to the user.")


def decide_prompt(n: dict) -> tuple[bool, str]:
    if os.environ.get("RATCHET_NESTED_REVIEW") == "1":
        return False, ""
    root = find_root(Path(n["cwd"]))
    rdir = root / RATCHET_DIR
    if not rdir.is_dir():
        return False, ""
    _heartbeat(root, "prompt", n["host"])
    with (rdir / "human_log.jsonl").open("a") as f:
        f.write(json.dumps({"at": now(), "prompt": n["prompt"][:2000], "session": n["session"],
                            "host": n["host"]}) + "\n")
    ctx = Ctx(root)
    if ctx.active_slug():
        st = read_json(ctx.state_path(), None)
        if st:
            st["stop_blocks"] = 0
            ctx.save_state(st)
            if st["phase"] in ("awaiting_plan_approval", "awaiting_checkpoint_review",
                               "awaiting_final_review", "blocked"):
                return False, (f"[ratchet] Run '{st['slug']}' is waiting on you (phase {st['phase']}). "
                               "If this message approves, run ratchet.py approve-plan / human-approve; "
                               "if it requests changes, handle them per the orchestrator skill.")
    return False, ""


PROTECTED = [re.compile(r"\.ratchet/runs/[^/\s]+/state\.json"), re.compile(r"\.ratchet/runs/[^/\s]+/evidence/"),
             re.compile(r"\.ratchet/human_log\.jsonl"), re.compile(r"\.ratchet/active\.json"),
             re.compile(r"\.ratchet/director\.json"), re.compile(r"\.ratchet/unavailable-reviewers\.json"),
             re.compile(r"\.ratchet/hook-heartbeat\.json")]
WRITE_VERBS = re.compile(r"(>|\btee\b|\brm\b|\bmv\b|\bcp\b|sed\s+-i|\btruncate\b|open\(|writeFile|json\.dump)")
NESTED_AGENT_RE = re.compile(r"(^|[;&|\s(])(claude|gemini|copilot)\s+(-p|--print|--prompt)\b|(^|[;&|\s(])codex\s+exec\b")


def decide_guard(n: dict) -> tuple[bool, str]:
    blob = json.dumps(n["input"]) + " " + n["command"]
    cmd = n["command"]
    if not _is_director_session():
        if (re.search(r"director-(sign|key|enroll)\b|director\.key|RATCHET_DIRECTOR_KEY_FILE|RATCHET_ROLE", blob)
                or str(director_key_path()) in blob):
            return True, ("RATCHET GUARD: director keys and signing are reserved for the director session. "
                          "Wait for the director to approve this lane.")
        if n["kind"] == "shell" and re.search(r"reviewer-status\b.*--mark", cmd):
            return True, ("RATCHET GUARD: only the director (or you, in your own terminal) can switch a reviewer off. "
                          "Reviewers that genuinely fail are marked unavailable automatically.")
        if n["kind"] == "shell" and "RATCHET_HUMAN_OVERRIDE" in cmd:
            return True, "RATCHET GUARD: RATCHET_HUMAN_OVERRIDE is for a human at their own terminal, not for agents."
    root = find_root(Path(n["cwd"]))
    if not (root / RATCHET_DIR).is_dir():
        return False, ""
    _heartbeat(root, "guard", n["host"])
    targets = []
    if n["kind"] == "write":
        targets += n["paths"] + ([n["patch"]] if n["patch"] else [])
    elif n["kind"] == "shell":
        if NESTED_AGENT_RE.search(cmd) and not re.search(r"ui_review\.py|external_review\.py", cmd):
            return True, ("RATCHET GUARD: launching nested agent sessions (claude -p, codex exec, gemini -p, copilot -p) "
                          "from the shell is blocked while Ratchet is active — they could impersonate a human approval. "
                          "Use sub-agents, external_review.py for adversarial reviews, or ui_review.py for UI reviews.")
        if re.search(r"\bratchet\.py\b", cmd) and not WRITE_VERBS.search(cmd.split("ratchet.py", 1)[0]):
            return False, ""
        if WRITE_VERBS.search(cmd):
            targets.append(cmd)
    for t in targets:
        if any(pat.search(t.replace("\\", "/")) for pat in PROTECTED):
            return True, ("RATCHET GUARD: gate state, evidence and the human-approval log are owned by ratchet.py. "
                          "Record results with ratchet.py commands (behavior / ui-record / adv-record / complete) "
                          "instead of editing these files.")
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
    except (Exception, SystemExit) as e:  # fail OPEN — never brick the host session (die() raises SystemExit)
        try:
            root = find_root(Path(data.get("cwd") or os.getcwd()))
            if (root / RATCHET_DIR).is_dir():
                with (root / RATCHET_DIR / "hook-errors.log").open("a") as f:
                    f.write(f"{now()} {host} {event}: {type(e).__name__}: {e}\n")
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
            entry["matcher"] = "Write|Edit|MultiEdit|NotebookEdit|Bash|Read|Grep|Glob"
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
    host = VENDOR_TO_HOST.get(detect_host({}), "unknown")
    print(f"Host running this command: {host} (provider {detect_host({})})")
    for h in HOOK_HOSTS:
        found = []
        for scope in ("project", "user"):
            p = hook_config_path(h, scope, root)
            if p.exists() and "ratchet.py" in p.read_text(errors="ignore"):
                found.append(f"{scope}: {p}")
        if h in ("claude", "codex"):
            found.append("(or installed as a plugin — hooks/hooks.json)")
        print(f"  {h:<8} " + ("; ".join(found) if found else "not installed — ratchet.py install-hooks --host " + h))
    hb = read_json(root / RATCHET_DIR / "hook-heartbeat.json", {}) or {}
    if hb:
        for ev in ("guard", "prompt", "stop"):
            if ev in hb:
                print(f"  last {ev:<6} hook: {hb[ev]['at'][:19]}Z via {hb[ev]['host']}")
            else:
                print(f"  last {ev:<6} hook: never fired in this project")
    else:
        print("  No Ratchet hook has fired in this project yet. Until one does, the gates are only advice:"
              " install the hooks for your agent CLI and restart it.")
    errs = root / RATCHET_DIR / "hook-errors.log"
    if errs.exists():
        tail = errs.read_text().strip().splitlines()[-3:]
        print("  recent hook errors (hooks failed open):\n    " + "\n    ".join(tail))


def hooks_warning(ctx) -> str | None:
    """Used by `next`/status: gates without hooks are just advice."""
    hb = read_json(ctx.ratchet / "hook-heartbeat.json", {}) or {}
    if not (hb.get("guard") or hb.get("stop") or hb.get("prompt")):
        return ("No Ratchet hook has fired in this project yet — the Stop/guard hooks may not be installed for "
                "this agent CLI. Run `ratchet.py doctor`.")
    return None



# --------------------------------------------------------------------------- main

def main(argv=None):
    try:  # quiet exit when output is piped into head/grep
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="ratchet.py", description="Ratchet loop state engine")
    p.add_argument("--root", help="operate on the project/lane at this path (default: current folder)")
    sp = p.add_subparsers(dest="cmd", required=True)

    a = sp.add_parser("init"); a.add_argument("feature"); a.add_argument("--slug")
    a.add_argument("--human-review", choices=["end", "each", "none"]); a.add_argument("--force", action="store_true")
    a.set_defaults(fn=cmd_init)
    a = sp.add_parser("plan-submit"); a.add_argument("--open", action="store_true"); a.set_defaults(fn=cmd_plan_submit)
    a = sp.add_parser("approve-plan"); a.add_argument("--director-token"); a.set_defaults(fn=cmd_approve_plan)
    a = sp.add_parser("tests-written"); a.add_argument("cp"); a.set_defaults(fn=cmd_tests_written)
    a = sp.add_parser("red"); a.add_argument("cp"); a.add_argument("--allow-green-first", action="store_true")
    a.add_argument("--reason"); a.set_defaults(fn=cmd_red)
    a = sp.add_parser("implemented"); a.add_argument("cp"); a.set_defaults(fn=cmd_implemented)
    a = sp.add_parser("behavior"); a.add_argument("cp"); a.set_defaults(fn=cmd_behavior)
    a = sp.add_parser("ui-record"); a.add_argument("cp"); a.add_argument("--review", required=True)
    a.add_argument("--single-reviewer-ok", action="store_true"); a.set_defaults(fn=cmd_ui_record)
    a = sp.add_parser("adv-record"); a.add_argument("cp"); a.add_argument("--reviews", nargs="+", required=True)
    a.set_defaults(fn=cmd_adv_record)
    a = sp.add_parser("complete"); a.add_argument("cp"); a.add_argument("--no-verify", action="store_true")
    a.set_defaults(fn=cmd_complete)
    a = sp.add_parser("human-approve"); a.add_argument("--director-token"); a.set_defaults(fn=cmd_human_approve)
    a = sp.add_parser("feedback"); a.add_argument("--text"); a.add_argument("--learning", action="append")
    a.add_argument("--director-token")
    a.set_defaults(fn=cmd_feedback)
    sp.add_parser("plan-extend").set_defaults(fn=cmd_plan_extend)
    a = sp.add_parser("learn"); a.add_argument("text"); a.add_argument("--source"); a.set_defaults(fn=cmd_learn)
    a = sp.add_parser("pause"); a.add_argument("--reason"); a.set_defaults(fn=cmd_pause)
    sp.add_parser("resume").set_defaults(fn=cmd_resume)
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
    a = sp.add_parser("director-sign"); a.add_argument("action", choices=["approve-plan", "human-approve", "feedback"])
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
    sp.add_parser("doctor", help="which hosts have Ratchet hooks, and are they firing?").set_defaults(fn=cmd_doctor)

    args = p.parse_args(argv)
    if args.root:
        os.environ["RATCHET_ROOT"] = str(Path(args.root).resolve())
    args.fn(args)


if __name__ == "__main__":
    main()
