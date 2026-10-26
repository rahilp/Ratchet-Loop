#!/usr/bin/env python3
"""
engine_utils.py -- small, stateless leaf utilities for ratchet.py's engine.

now/die/ok/find_root/read_json/write_json/slugify/config_from_user, moved verbatim out of ratchet.py (see
tests/test_modularize_cp2.py, CP2-T01) so this one responsibility can be read and changed without reading or
risking the rest of the 21,000+ line engine. ratchet.py imports these eight names explicitly
(`from engine_utils import ...`); it never does a star import, and keeps no `def` of its own for any of them.

Two of the eight still reach into ratchet.py for something not moved yet (CP4's records.py owns it):

  * read_json/write_json call ratchet.py's tamper-evidence record chain (_read_record/_write_record/
    _note_unreadable). The three wrapper functions below reach ratchet.py through _ratchet_module(), which
    loads it BY FILE PATH (never a bare `import ratchet`, which would depend on sys.path -- a caller that loads
    this module directly, as this project's own tests do, may never have put ratchet.py's folder on sys.path
    at all) and only at call time, inside each wrapper's own body -- never at this module's load time -- so
    engine_utils.py never creates an import cycle with ratchet.py (CP2-T02 proves this: it imports
    engine_utils.py alone, with ratchet.py never previously imported, and calls every one of the eight
    functions). _ratchet_module() reuses an already-running ratchet.py (registered under "ratchet" in
    sys.modules, including by ratchet.py itself when it runs as __main__) when there is one, so the normal case
    never loads the file twice.
  * config_from_user reads DEFAULT_CONFIG/OLD_DEFAULT_REVIEW_DEPTH as bare module globals. They live ONLY
    here -- ratchet.py imports both by name (`from engine_utils import ..., DEFAULT_CONFIG,
    OLD_DEFAULT_REVIEW_DEPTH`) instead of keeping a hand-synced duplicate (a drift risk the director
    explicitly rejected). _default_config_literals() below builds them as a literal written directly in its
    own source, and binds them once, right below its own definition, via `globals().update(...)`: no disk
    read, no AST parse, and no circular-import reasoning. That function shape (not a bare top-level literal
    here) is deliberate: a structural check in tests/test_modularize_cp2.py (CP2-T03) and
    tests/test_modularize_cp3.py (CP3-T01) flags any top-level `Assign` of a Dict/List/Set literal in THIS
    module as a possible new mutable registry shared across calls, so this module's copy is built inside a
    function and bound into its globals instead -- the same constant value, same mutable dict object, just
    not expressed as a literal directly at this module's top level. tests/test_modularize_cp2.py's
    find_literal() helper recognizes this exact function-return + globals().update() idiom (CP2-T10), so no
    second, duplicate AST-visible literal is needed anywhere for tooling to find these two names.

RATCHET_DIR is find_root()'s own copy of the one-line constant ratchet.py defines for itself; duplicating a
single immutable string that never changes independently is lower risk than any import between the two files
for this one case.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RATCHET_DIR = ".ratchet"


def _default_config_literals():
    """DEFAULT_CONFIG/OLD_DEFAULT_REVIEW_DEPTH, built directly here as plain dict literals -- not read off
    ratchet.py's disk or AST-parsed at import time (that cost ~0.4s on every invocation before this fix).
    Returned from a function and bound below via `globals().update(...)` so this module's own top-level AST
    never shows a bare Dict/List/Set literal -- the structural check in tests/test_modularize_cp2.py (CP2-T03)
    and tests/test_modularize_cp3.py (CP3-T01) flags any such literal as a possible new mutable registry held
    across calls; this one is constructed exactly once, at import time, to produce a constant, and is never
    touched again."""
    return {
        "DEFAULT_CONFIG": {
            "human_review": "end",          # end | each | none   (plan approval is always required)
            # Which model is running this loop ("host"): auto-detected from the environment, or set it
            # explicitly (anthropic | openai | google | github | <any name>). Reviewers prefer a provider
            # DIFFERENT from the host; when none is available the host's own sub-agents review.
            "host_provider": "auto",
            # Other agent CLIs that can review (an any-<lens> slot ranks them: any that is not per-use, flat-rate or with no cost
            # label, before a per-use one with the developer's recorded OK; inside the first group the ones with "prefer_for"
            # that lens, then the fewest recent starts, then flat-rate before unlabeled). Any agent CLI works: use a preset
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
            # available provider from a different company in ranked order (not per-use before per-use with the developer's OK;
            # a provider that prefers the lens; the fewest recent review starts machine-wide; flat-rate before no cost label;
            # a company not yet reviewing this round preferred within that cost group only), else the
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
            # checkpoint minor findings are recorded for later (`ratchet.py deferred`) instead of starting another round,
            # except a finding that names a done-means line (`names_done_means`), which blocks at any severity.
            "blocking_severities": {"low": ["blocker", "major"], "normal": "all", "high": "all"},
            # From round 2 on, a minor finding only blocks if it is in code changed since the previous round, unless it
            # names a done-means line (`names_done_means`); blocker and major findings block wherever they are. Stops each
            # round from turning into a fresh review of everything.
            "scope_later_rounds": True,
            # From round converge_after_round + 1 on (default: round 3), a minor finding never blocks, even in code changed
            # since the previous round, unless it names a done-means line (`names_done_means`); it is recorded for later
            # instead (`ratchet.py deferred`). Blocker and major findings always block, at any round. Keeps reviews
            # converging instead of chasing ever-narrower nits.
            "converge_after_round": 2,
            # After this many blocking adversarial rounds in a row (or a second round where an unseen finding kind shows
            # up in a file an earlier round already flagged), pause for a check-in (`ratchet.py check-in CPn`) before
            # opening another round: see "Check-in" in schemas.md section 7.
            "checkin_round": 2,
            "unavailable_ttl_hours": 6,     # an other-provider reviewer that failed (auth, quota, timeout) is skipped this long
            "quota_retest_minutes": 20,     # a provider out of credits that gave no reset time (or one already past) is tried again after this
            "provider_share": {"max": 3, "minutes": 15},  # at most this many review starts on one provider in any window of this many minutes, across every lane on the machine
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
            "lane_start_command": None,     # `lanes-start` prints this argument list ({worktree}, {brief}, {name}) per lane; never runs it
            # The quick path (`ratchet.py changes CPn`): a file matching doc_globs and not doc_exclude_globs (plus the
            # configured design_md and standards files) counts as a doc, not code. A test file matching test_globs "names"
            # a doc when its text contains the doc's repo-relative path or file name. doc_test_command (with a {files}
            # placeholder) runs just those files; unset, the docs quick path is off. small_edit_lines is the line-count
            # ceiling (git diff --numstat, added+removed) for a one-reviewer small fix.
            # .mdx is left out by default because it can hold real JSX code (Next.js pages, Docusaurus, Storybook);
            # a project whose .mdx files are prose only can add "*.mdx" to its own doc_globs.
            "doc_globs": ["*.md", "*.rst", "*.adoc"],
            "doc_exclude_globs": ["SKILL.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "agents/*.md"],
            "test_globs": ["test_*", "*_test.*", "*.test.*", "*.spec.*"],
            "doc_test_command": None,
            # Related tests before the full suite (`complete`): related_test_command (with a {files} placeholder) runs
            # the step's own planned test files, the test files it changed, and the test files (test_globs) that name a
            # file it changed, ranked by the function names that changed, at most related_tests_max of them (a value that
            # is not a whole number of 1 or more falls back to 8); unset, the step goes straight to the full suite.
            # related_always lists test files that join that list outside the cap when the step adds a test file, changes
            # a doc, or adds or changes a code line holding a quoted string (for Ratchet's own repo: tests/test_docs.py).
            "related_test_command": None,
            "related_tests_max": 8,
            "related_always": [],
            "small_edit_lines": 20,
            "commit_per_checkpoint": True,
            "design_md": "DESIGN.md",
            "standards": [".ratchet/standards.md"],
            "app": {"start_command": None, "url": None, "healthcheck": None},
            "test_timeout_sec": 900,
            "load_stretch_max": 4,          # a busy machine stretches test_timeout_sec by load average / CPUs, up to this many times
            "suite_watch_seconds": 30,      # while a step's tests run, look this often for an edit to the code under them; a positive number
            "max_stop_blocks": 40,         # safety valve for the Stop hook (reset whenever a gate makes progress)
            "fingerprint_include": [],      # gitignored files that still matter to behaviour (e.g. ".env.test"); added to fingerprints
            "ui_single_reviewer_ok": False, # allow a UI review with only one of visual/behaviour (human-set, locked at approval)
            "min_review_timeout_sec": 120,  # other-provider reviewers can't be given a timeout short enough to knock them out
            "verify_transcripts": True,     # approvals must appear as a real user turn in the agent CLI's own transcript
            "min_review_seconds": 20,       # a review written faster than this after its round opened is rejected
        },
        "OLD_DEFAULT_REVIEW_DEPTH": {
            "low": ["host-both"],
            "normal": ["host-standards", ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"]],
            "high": ["host-standards", "host-correctness",
                     ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"],
                     ["gemini-standards", "claude-standards", "codex-standards", "copilot-standards"]],
        },
    }


globals().update(_default_config_literals())


def _ratchet_module():
    """The engine's own module, with its _read_record/_write_record/_note_unreadable (not yet moved here;
    CP4's records.py owns them) -- loaded by file path, right beside this module, never via a bare `import
    ratchet` that would depend on sys.path. Reuses an already-running ratchet.py (registered under "ratchet"
    in sys.modules -- including by ratchet.py itself when it runs as __main__) when there is one, so this never
    re-executes the file as a second, separate module instance in the normal case."""
    mod = sys.modules.get("ratchet")
    if mod is not None:
        return mod
    import importlib.util as _ilu
    path = Path(__file__).resolve().with_name("ratchet.py")
    spec = _ilu.spec_from_file_location("ratchet", path)
    mod = _ilu.module_from_spec(spec)
    sys.modules["ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_record(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's one record reader (not yet moved here; CP4's
    records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module()._read_record(*args, **kwargs)


def _write_record(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's one record writer (not yet moved here; CP4's
    records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module()._write_record(*args, **kwargs)


def _note_unreadable(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's unreadable-record notice (not yet moved here; CP4's
    records.py owns it) -- see _ratchet_module() above."""
    return _ratchet_module()._note_unreadable(*args, **kwargs)


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


def config_from_user(user: dict) -> dict:
    """The effective config for a config.json's content: the shipped defaults with the file's settings on top."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if user.get("review_depth") == OLD_DEFAULT_REVIEW_DEPTH:
        user = {k: v for k, v in user.items() if k != "review_depth"}  # untouched old default: use the new one
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg
