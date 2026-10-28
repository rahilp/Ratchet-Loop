#!/usr/bin/env python3
"""
plan_support.py -- plan/objective validation and the planner's "What earlier work says" earlier-work digest for
ratchet.py's engine.

objective_problems/_proves_values/proves_problems/objective_brief/_normalize_role (plus ROLE_BRIEFS/ROLE_ORDER/
_GENERIC_ROLE_RULES/ROLE_ALIASES and the digest helpers _digest_words/_digest_clean_item/_digest_cp_order/
_digest_epoch/_digest_recency/_digest_rank/_learnings_bullets/_digest_bullet_epoch/_digest_cap_lines/
_earlier_runs/_spec_digest_entry/_related_spec_items/_earlier_work_lines, plus their own small constants
OBJECTIVE_EXAMPLE/NO_OBJECTIVE_NOTE/DIGEST_HEADING/DIGEST_ITEM_CAP/DIGEST_ITEM_MAX_CHARS/DIGEST_SPEC_HEAD/
DIGEST_SPEC_PATH_NOTE/_BULLET_HEAD_RE/_BULLET_DATE_RE/_ACCEPTED_FINDING_MARKER_RE), moved verbatim out of
ratchet.py (see tests/test_modularize_cp7.py, CP7-T11) so this one responsibility -- whether a plan's objective
and "proves" links are valid, the plain-text objective block every brief and reviewer prompt opens with, which
role name `--role` accepts, and the planner-only "What earlier work says" digest -- can be read and changed
without reading or risking the rest of the 21,000+ line engine. ratchet.py imports these names explicitly
(`from plan_support import ...`); it never does a star import, and keeps no `def`/`class` of its own for any
of them. cmd_brief and cmd_plan_submit (the CLI handlers) stay in ratchet.py, unchanged except that the names
they call now resolve through this module's explicit import, the same way records.py's/provider_discovery.py's/
run_records.py's own moved names are reached from their callers.

Six ratchet.py-resident names these functions call are NOT one of the moved names and stay in ratchet.py for
now (candidates for a later checkpoint's own module, not this one's responsibility): _objective_fill_ins
(objective_problems), test_plan/_sc_parse/_sc_watch_missing (proves_problems, for the "shortcuts" case layer --
out of this move's scope, see checkpoints.json), _learnings_text (_learnings_bullets) and _finding_key
(_earlier_work_lines). Every one of these is reached lazily, by file path, at call time only, through
_ratchet_module() below -- never a disk read or an AST parse of ratchet.py's text at THIS module's import
time, and never a module-level `import ratchet` that would create an import cycle (ratchet.py already imports
this module). This is the exact shape records.py's/provider_discovery.py's/run_records.py's own wrappers use
for their own forward dependency on ratchet.py.

SHORTCUT_DIR ("tests/shortcuts") is also read by proves_problems, but it is a small, fixed literal that never
varies at runtime (the same shape provider_discovery.py's own docstring describes for PROBE_TOKEN/
_PROBE_CLAIM_SLACK/LENSES) -- not a registry anyone extends -- so it is simply given here too, instead of
wrapped.

_ABSENT (used by _earlier_runs/_earlier_work_lines to tell "missing" from "damaged" when calling records.py's
_read_record) is a fresh sentinel object defined here, the same "a private, never-compared-across-modules
marker" shape run_records.py's own copy of this exact pattern documents: its identity never needs to match
ratchet.py's own, differently-purposed sentinel of a similar kind, since _read_record only ever echoes back
whatever its "missing" argument was passed as.

specsrc (spec_sources.py) is the only other-module name these functions call beyond engine_utils/records;
imported here the ordinary way, exactly as ratchet.py does, since it already has no dependency on ratchet.py or
on this module. _read_record is the only records.py name they call; imported explicitly from records, so there
is no cycle risk from this module's side (records.py does not import this module at its own load time).
"""
from __future__ import annotations

import re
import sys
import datetime as _dt
from pathlib import Path

# A loader that reaches this file directly (spec_from_file_location, the way this project's own
# source-inspecting tests do, and the way ratchet.py never does -- ratchet.py always has its own directory on
# sys.path already) may never have put this file's own directory on sys.path, so the sibling imports just
# below would fail; ratchet.py itself does the same insert before its own sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec_sources as specsrc
from records import _read_record

SHORTCUT_DIR = "tests/shortcuts"  # mirrors ratchet.py's own fixed constant of the same name -- see module docstring

_ABSENT = object()  # tells _read_record's "missing" from "damaged"; see module docstring


def _ratchet_module():
    """The engine's own module, with every ratchet.py-resident name these functions still reach for (not yet
    moved anywhere; most look like candidates for a later checkpoint's own module) -- loaded by file path,
    right beside this module, never via a bare `import ratchet` that would depend on sys.path (and would be a
    real import cycle: ratchet.py already imports this module). Reuses an already-running ratchet.py
    (registered under "ratchet" in sys.modules -- including by ratchet.py itself when it runs as __main__)
    when there is one, so this never re-executes the file as a second, separate module instance in the normal
    case. Mirrors records.py's/provider_discovery.py's/run_records.py's own helper of the same name and
    shape."""
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


def _objective_fill_ins(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own objective-fill-in-placeholder check -- see
    _ratchet_module()."""
    return _ratchet_module()._objective_fill_ins(*args, **kwargs)


def test_plan(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own test-plan reader -- see _ratchet_module()."""
    return _ratchet_module().test_plan(*args, **kwargs)


def _sc_parse(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own shortcut-table parser -- see _ratchet_module()."""
    return _ratchet_module()._sc_parse(*args, **kwargs)


def _sc_watch_missing(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own shortcut-table watch check -- see
    _ratchet_module()."""
    return _ratchet_module()._sc_watch_missing(*args, **kwargs)


def _learnings_text(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own learnings.md text reader -- see _ratchet_module()."""
    return _ratchet_module()._learnings_text(*args, **kwargs)


def _finding_key(*args, **kwargs):
    """Deferred, call-time-only reach into ratchet.py's own finding-key builder -- see _ratchet_module()."""
    return _ratchet_module()._finding_key(*args, **kwargs)


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


def _proves_values(case) -> list:
    """Normalize one test-plan case's "proves" field for every consumer (proves_problems, _done_means_links,
    _done_means_proof): a missing value is [] (nothing to prove), a scalar int counts as [int] (the common
    planner slip of writing "proves": 1 instead of [1]), a list is used as-is. Any other shape is not a number
    or a list of numbers, so there is nothing valid to normalize here; plan-submit's proves_problems is the one
    place that refuses it outright, naming the case, before plan-summary or receipt ever reads it."""
    proves = case.get("proves")
    if proves is None:
        return []
    if isinstance(proves, list):
        return proves
    if isinstance(proves, int) and not isinstance(proves, bool):
        return [proves]
    return []


def proves_problems(ctx, plan) -> list[str]:
    """CP3: a test-plan case's optional "proves" (the spec_ref precedent's sibling) names, 1-based, the lines
    of the objective's done_means it proves. plan-submit refuses any number that is not really a done_means
    line, naming the offending case (plan-extend applies the same check to the appended checkpoints' cases only,
    passing a plan holding just those); a plan with no objective or no done_means lines (objective_problems
    already refuses that on its own) simply has nothing valid to name, so every number is refused. "proves" may
    be a single integer (normalized to a one-item list, see _proves_values) or a list; any other shape (a
    string, a dict, a list containing one) is refused here by name, so plan-summary and receipt never have to
    guess at it."""
    obj = plan.get("objective") if isinstance(plan, dict) else None
    dm = obj.get("done_means") if isinstance(obj, dict) else None
    n = len(dm) if isinstance(dm, list) else 0
    errs = []
    for cp in plan.get("checkpoints") or []:
        for case in (test_plan(ctx, cp["id"]) or {}).get("cases") or []:
            if case.get("layer") == "shortcuts":  # names a shortcut table that exists (tests/shortcuts/<table>.json)
                t = case.get("table")
                if not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", t):
                    errs.append(f"{case.get('id', '?')}: a shortcuts case must name its table in \"table\" "
                                f"(tests/shortcuts/<table>.json)")
                elif not (ctx.root / SHORTCUT_DIR / f"{t}.json").is_file():
                    errs.append(f"{case.get('id', '?')}: shortcut table {t!r} does not exist "
                                f"({SHORTCUT_DIR}/{t}.json)")
                else:
                    try:
                        tdef, twhy = _sc_parse((ctx.root / SHORTCUT_DIR / f"{t}.json").read_text(errors="replace"))
                    except OSError as e:
                        tdef, twhy = None, f"unreadable: {e}"
                    gone = _sc_watch_missing(ctx, None, tdef["watches"]) if tdef else []
                    if twhy or gone:
                        errs.append(f"{case.get('id', '?')}: shortcut table {t!r} cannot be used: "
                                    + (twhy or f"it watches {', '.join(gone)}, which does not exist"))
            proves = case.get("proves")
            if proves is None:
                continue
            if not isinstance(proves, list) and not (isinstance(proves, int) and not isinstance(proves, bool)):
                errs.append(f"{case.get('id', '?')}: proves {proves!r} must be a done_means line number or a "
                            f"list of them (objective.done_means has {n} line(s))")
                continue
            for p in _proves_values(case):
                if not isinstance(p, int) or isinstance(p, bool) or p < 1 or p > n:
                    errs.append(f"{case.get('id', '?')}: proves {p!r} is not a done_means line "
                                f"(objective.done_means has {n} line(s))")
    return errs


NO_OBJECTIVE_NOTE = "no objective was stated for this run (an older plan, approved before this feature existed)"


def objective_brief(plan, proof_notes=None) -> str:
    """Plain text of the plan's objective, for the top of every reviewer prompt and every sub-agent's brief: who
    the result is for, what they want, what done means, what is out of scope. With `proof_notes` (the dict
    `_done_means_proof_notes` builds: {1-based done_means line number: one note line}), each done-means bullet is
    followed by its note (`Proved by: <case ids>`, or the plain `Heads up` for a line no case proves yet); this is
    the one rendering `plan-summary`, `brief` and the launch guard share, so a brief's objective matches
    plan-summary's word for word. Done-means lines are numbered 1..N (`  - 1. <line>`, still a "- " bullet so
    the objective block reads the same as the other bullets), the same 1-based numbers a test case's "proves"
    and a review finding's `done_means` (CP3) use, so a reviewer can name the line a finding contradicts. A
    plan with no objective at all (a run approved before CP1's
    objective_problems() existed) gets a plain note instead of a crash, so a legacy run keeps reviewing normally."""
    obj = plan.get("objective") if isinstance(plan, dict) else None
    if not isinstance(obj, dict) or not obj:
        return f"Objective: {NO_OBJECTIVE_NOTE}."
    lines = ["Objective", f"Who it's for: {obj.get('users', '')}", f"What they want: {obj.get('goal', '')}"]
    dm = obj.get("done_means") or []
    if dm:
        lines.append("Done means:")
        for i, x in enumerate(dm, 1):
            lines.append(f"  - {i}. {x}")
            if proof_notes and proof_notes.get(i):
                lines.append(f"    {proof_notes[i]}")
    oos = obj.get("out_of_scope") or []
    if oos:
        lines.append("Out of scope:")
        lines += [f"  - {x}" for x in oos]
    return "\n".join(lines)


# Each Ratchet sub-agent role's own standing rules, short and plain, printed by `brief` right after the objective
# so the orchestrator never has to remember or retype them. One dict, one source, instead of scattered strings in
# each agent file: keep an agent's own .md file (e.g. agents/ratchet-fixer.md) worded consistently with its entry
# here, but this dict is what every spawn prompt actually gets.
ROLE_BRIEFS = {
    "implementer": ("Rules: make the smallest change that makes this checkpoint's planned tests pass. Add no new "
                     "mechanism or file beyond what this checkpoint needs. Don't run the full test suite unless "
                     "asked; run only this checkpoint's own tests. Never edit, skip or weaken a test."),
    "fixer": ("Rules: make the smallest change that resolves each finding you were handed. Add no new mechanism or "
              "file unless the finding itself needs it. If a proper fix needs a redesign, stop and say so instead "
              "of improvising one. Never weaken, skip or delete a planned test, a test that existed before "
              "the run, or anything a planned test uses; a Python test this lane added that no planned test uses may be "
              "adjusted. Don't run the full test suite unless asked."),
    "test-writer": ("Rules: turn the checkpoint's planned test cases into real, runnable tests tagged with their "
                     "case ids, written before the implementation exists, so at least one fails on an assertion. "
                     "Test behavior through public entry points, not internals. Never write a test that can't fail. "
                     "An extra Python test you add that no planned test uses may be adjusted later; planned "
                     "tests, what they use and tests from before the run stay locked."),
    "reviewer": ("Rules: judge every finding against the realistic-use bar: this project's users run a cooperative "
                 "agent, not an attacker, and a finding must describe real harm to them in realistic, normal use, "
                 "not a case no real user would hit. Every blocking finding (blocker or major) must say who is hurt "
                 "and how; leave that out and it does not block. The one exception: a finding that names a done-means "
                 "line of the objective (done_means) blocks at any severity and any round, with no separate "
                 "user_impact needed, so name a line only when that line is not met in normal use; a plan approved "
                 "before that rule keeps the older severity and round rules, and the round's review rules say so when "
                 "that is the case. Approve only when you genuinely find nothing."),
    "scout": ("Rules: verify the app, its tests and its dev server actually run before anyone else relies on your "
              "report; don't guess. Record what's already green, what's already broken, and what's missing, so a "
              "later checkpoint is never blamed for a problem that was there before the build started."),
    "planner": ("Rules: write the plan's objective first. Slice the work into small checkpoints of increasing "
                "complexity a human can approve in minutes. Ask a question instead of guessing on anything that "
                "changes the plan's shape. A high-risk step needs its own risk_reason naming who could be harmed."),
    "test-planner": ("Rules: plan this checkpoint's test cases before any code exists, through public entry "
                     "points, with every done_when line covered. Every objective done-means line above needs at "
                     "least one case, in some step's tests/CPn.json, whose \"proves\" names it by its 1-based "
                     "number; plan-submit refuses a plan that leaves a done-means line unlinked, naming the line. "
                     "Don't write test code."),
}

ROLE_ORDER = ["implementer", "fixer", "test-writer", "reviewer", "scout", "planner"]

# Every other agent in agents/ gets this same short generic line instead of a custom rule set: it still points the
# agent back at the objective and the smallest-change discipline every role shares, without inventing bespoke
# wording per agent (the fix this round deliberately keeps smaller than a custom brief for each one).
_GENERIC_ROLE_RULES = ("Rules: follow your own agent instructions for this role. Serve the objective above. Make "
                        "the smallest change for the job. Don't run the full test suite unless asked.")
for _generic_role in ("behavior-reviewer", "design-system", "prototyper", "test-runner", "variant-writer",
                      "visual-reviewer"):
    ROLE_BRIEFS[_generic_role] = _GENERIC_ROLE_RULES

# agents/*.md names that don't match a ROLE_BRIEFS key directly (the rest of the agents/ file stems -- adversary,
# behavior-reviewer, checkpoint-planner, design-system, fixer, implementer, prototyper, test-planner (its own
# entry above: the done-means link rule), test-runner,
# test-writer, variant-writer, visual-reviewer, app-scout -- already equal a ROLE_BRIEFS key once the optional
# `ratchet-` prefix is stripped). Keep this to just the renames so `--role` accepts every agent the orchestrator
# spawns without a bespoke rule set per agent.
ROLE_ALIASES = {"adversary": "reviewer", "app-scout": "scout", "checkpoint-planner": "planner"}


def _normalize_role(raw: str) -> str | None:
    """`--role` accepts a ROLE_BRIEFS key directly, any agents/*.md file stem (with or without its `ratchet-`
    prefix), and the ROLE_ALIASES renames for the three agents whose file name doesn't match a role name. Returns
    None for anything else, so `cmd_brief` can refuse it with a plain message instead of argparse's own."""
    tok = raw[len("ratchet-"):] if raw.startswith("ratchet-") else raw
    tok = ROLE_ALIASES.get(tok, tok)
    return tok if tok in ROLE_BRIEFS else None


# ------------------------------------------------------------------------------------------------- earlier-work digest
# So a new feature builds on what earlier work in this project already learned, instead of rediscovering or
# contradicting it, the planner's own brief ends with a short "What earlier work says" section built entirely
# from records already on disk; the orchestrator never has to collect this by hand. Entirely read-only: every
# OTHER run's state.json is read with _read_record directly, never ctx.state() (which verifies the chain and
# binds an owner -- right for the run this process is actually working on, wrong for history it only reads), and
# a damaged or missing record is skipped, never fatal. CP2 adds a recorded-specs section to the same list this
# helper returns, so cmd_brief only ever has one block to print.

DIGEST_HEADING = "What earlier work says"
DIGEST_ITEM_CAP = 5  # a few items per section; the rest are named by count and pointed at their file, not printed
DIGEST_ITEM_MAX_CHARS = 200  # a fixed length per printed item, so one long or multi-line item can't break the list
DIGEST_SPEC_HEAD = "Related specs:"
DIGEST_SPEC_PATH_NOTE = "openspec/specs/, specs/ (Spec Kit) and docs/stories/ (BMAD)"

_BULLET_HEAD_RE = re.compile(r"^-\s*\([^)]*\)\s*(.*)$")  # strips a learnings.md bullet's "(date, source)" head, leaving the text
_BULLET_DATE_RE = re.compile(r"^-\s*\((\d{4}-\d{2}-\d{2})\b")  # the date at the start of that same head, for ranking
_ACCEPTED_FINDING_MARKER_RE = re.compile(r"^Accepted finding (\S+) \(", re.I)  # the marker _append_learning writes


def _digest_words(text: str) -> set:
    return specsrc._words(text or "")


def _digest_clean_item(text: str) -> str:
    """One printed digest item, made safe for the short, plain list `_digest_cap_lines` prints: whitespace (including
    embedded newlines) collapsed to single spaces, non-ASCII replaced, and truncated to DIGEST_ITEM_MAX_CHARS with a
    trailing "..." when cut, so a multi-line or very long description can never break the list or defeat "stays
    short"."""
    one_line = " ".join(str(text or "").split())
    ascii_text = one_line.encode("ascii", "replace").decode("ascii")
    if len(ascii_text) > DIGEST_ITEM_MAX_CHARS:
        ascii_text = ascii_text[:DIGEST_ITEM_MAX_CHARS - 3].rstrip() + "..."
    return ascii_text


def _digest_cp_order(cp) -> int:
    """A checkpoint id ('CP1', 'CP12', ...) as a plain int for ordering, so 'later checkpoint first' is a number
    comparison rather than a string one (where 'CP2' would wrongly sort after 'CP10')."""
    m = re.search(r"(\d+)\s*$", str(cp or ""))
    return int(m.group(1)) if m else 0


def _digest_epoch(text) -> float | None:
    """`text` parsed as an ISO-8601 timestamp (what `now()` writes, e.g. an `accepted_at`) into epoch seconds, or
    None when it isn't one."""
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(str(text)).timestamp()
    except (ValueError, TypeError):
        return None


def _digest_recency(epoch: float, cp: str = "", round_no=0) -> tuple:
    """One comparable recency key, in the same unit (epoch seconds) for every item in a digest section, whatever
    its source: a learnings.md bullet's own date, a finding's own recorded time, or (falling back) the run file's
    mtime as a date. `cp`/`round_no` are a secondary, same-run tie-break only: many findings in one run share that
    run's single mtime, so without this every one of them ties and a stable sort keeps the oldest five instead of
    the newest; a later checkpoint and later round sort as newer when the timestamp itself ties."""
    return (epoch, _digest_cp_order(cp), int(round_no or 0))


def _digest_rank(words: set, relevant_to: set, recency) -> tuple:
    """Sort key for one digest item: an item sharing a word with the active run's feature request comes before
    everything else; within each of those two groups, the newest comes first (the run's own assumption: relevance
    first, newest as the tie-break and the only order left once there is no feature to match against). `recency` is
    a single comparable number or a `_digest_recency` tuple; either way every component sorts newest-first."""
    relevant = bool(relevant_to) and bool(words & relevant_to)
    if not isinstance(recency, tuple):
        recency = (recency,)
    return (0 if relevant else 1, tuple(-r for r in recency))


def _learnings_bullets(ctx) -> list[str]:
    """Every '- (...)' bullet line in learnings.md, oldest first (the file's own append order; age itself is read
    from each bullet's own "(YYYY-MM-DD, ...)" head, see _digest_bullet_epoch, not from this order)."""
    return [l.strip() for l in _learnings_text(ctx).splitlines() if l.strip().startswith("-")]


def _digest_bullet_epoch(raw: str, fallback_index: int) -> float:
    """A learnings.md bullet's own recency, in the same epoch-seconds unit every other digest item uses: the date
    in its "(YYYY-MM-DD, ...)" head, read as UTC midnight. A bullet with no such date (malformed, or hand-edited)
    falls back to its position in the file so it still sorts somewhere instead of crashing the brief."""
    m = _BULLET_DATE_RE.match(raw)
    if m:
        try:
            return _dt.datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc).timestamp()
        except ValueError:
            pass
    return float(fallback_index)


def _digest_cap_lines(items: list[tuple], header: str, path_note: str) -> list[str]:
    """`items` is a list of (sort_key, one-line text) already gathered for one section; prints the best
    DIGEST_ITEM_CAP of them under `header`, then one line always naming the file this section's items come from
    (plus how many more there are, when some were cut), so the planner knows where to read the rest even when
    nothing was cut. Nothing is printed for an empty section (no header with nothing under it). Each item's text is
    passed through _digest_clean_item first, so a multi-line or very long description can't break the list."""
    if not items:
        return []
    items = sorted(items, key=lambda it: it[0])
    shown, rest = items[:DIGEST_ITEM_CAP], items[DIGEST_ITEM_CAP:]
    out = [header] + [f"  - {_digest_clean_item(line)}" for _, line in shown]
    if rest:
        out.append(f"  ... and {len(rest)} more - see {path_note}")
    else:
        out.append(f"  ... see {path_note}")
    return out


def _earlier_runs(ctx, active_slug):
    """(slug, state, path) for every OTHER run folder under .ratchet/runs/: read with _read_record so a damaged
    state.json, or a run folder with none at all, is skipped instead of failing the brief. Never the active run,
    and never through ctx.state()."""
    out = []
    try:
        slugs = sorted(p.name for p in (ctx.ratchet / "runs").iterdir() if p.is_dir())
    except OSError:
        return out
    for slug in slugs:
        if slug == active_slug:
            continue
        p = ctx.ratchet / "runs" / slug / "state.json"
        st, why = _read_record(p, missing=_ABSENT)
        if why is not None or st is _ABSENT or not isinstance(st, dict):
            continue
        out.append((slug, st, p))
    return out


def _spec_digest_entry(title: str, purpose: str, path: str) -> str:
    """One printed 'Related specs:' line: "<title>: <purpose> (<path>)". The path is reserved its own room and
    appended last, after the title/purpose head is truncated to fit -- so a long title can never push the path
    past _digest_clean_item's length cap and drop the one thing the planner needs to open the full spec."""
    title = str(title or "").strip()
    purpose = str(purpose or "").strip()
    path = str(path or "").strip()
    tail = f" ({path})"
    budget = max(DIGEST_ITEM_MAX_CHARS - len(tail), 0)
    head = title + (f": {purpose}" if purpose else "")
    if len(head) > budget:
        head = head[:budget - 3].rstrip() + "..." if budget > 3 else head[:budget]
    return f"{head}{tail}"


def _related_spec_items(ctx, relevant_to: set) -> list[tuple]:
    """(sort_key, line) for every recorded spec worth telling the planner about, ranked with the same
    relevance-then-recency rule as every other digest section so no one spec tool is favoured over another:
    OpenSpec's living records under openspec/specs/ (including Ratchet's own running records written by
    living_spec_text), a finished Spec Kit feature (every task in tasks.md ticked) and a done BMAD story (its
    status is exactly "done", ignoring case and surrounding whitespace). An open or unfinished one is left
    out -- `spec detect` already covers those. Each
    source is read the tolerant way (a bad or missing file is skipped, never fatal), since a brief must never
    fail to print over a spec file someone is still editing."""
    items = []
    try:
        openspec = specsrc.openspec_specs(ctx.root)
    except (OSError, ValueError):
        openspec = []
    for s in openspec:
        words = _digest_words(f"{s['title']} {s['purpose']}")
        line = _spec_digest_entry(s["title"], s["purpose"], s["path"])
        items.append((_digest_rank(words, relevant_to, _digest_recency(s["mtime"])), line))
    try:
        features = specsrc.speckit_features(ctx.root)
    except (OSError, ValueError):
        features = []
    for f in features:
        tasks = f.get("tasks") or []
        if not tasks or not all(t.get("done") for t in tasks):
            continue
        path = f"{f['path']}/spec.md"
        try:
            mtime = (ctx.root / path).stat().st_mtime
        except OSError:
            mtime = 0.0
        purpose = f.get("why") or ""
        words = _digest_words(f"{f['title']} {purpose}")
        line = _spec_digest_entry(f["title"], purpose, path)
        items.append((_digest_rank(words, relevant_to, _digest_recency(mtime)), line))
    try:
        stories = specsrc.bmad_stories(ctx.root)
    except (OSError, ValueError):
        stories = []
    for s in stories:
        if str(s.get("status") or "").strip().lower() != "done":
            continue
        try:
            mtime = (ctx.root / s["path"]).stat().st_mtime
        except OSError:
            mtime = 0.0
        purpose = s.get("why") or ""
        words = _digest_words(f"{s['title']} {purpose}")
        line = _spec_digest_entry(s["title"], purpose, s["path"])
        items.append((_digest_rank(words, relevant_to, _digest_recency(mtime)), line))
    return items


def _earlier_work_lines(ctx) -> list[str]:
    """Everything the planner's brief shows about earlier work: the project's recent learnings, the findings
    earlier (non-active) runs saved for later that nobody accepted yet, and the limits the user already
    accepted (an accepted finding, or a learnings.md "Accepted limit (...)" bullet) -- most relevant to the new
    feature first, newest after that, each section capped. A project with no earlier work of any kind gets one
    plain line instead of empty sub-headings. Takes no lock and writes nothing."""
    active_slug = ctx.active_slug()
    active_feature = ""
    if active_slug:
        # Read with _read_record, the same tolerant path _earlier_runs uses for every OTHER run -- never
        # ctx.state(), which verifies the chain and can die() on a mismatch; a brief must never fail to print
        # over the active run's own record, so any problem here just leaves relevance empty.
        st, why = _read_record(ctx.ratchet / "runs" / active_slug / "state.json", missing=_ABSENT)
        if why is None and st is not _ABSENT and isinstance(st, dict):
            active_feature = st.get("feature") or ""
    relevant_to = _digest_words(active_feature)

    learn_items, open_items, accepted_items = [], [], []
    accepted_keys = set()  # "{slug}/{cp}/round-{round}/{reviewer-or-bare-id}" already shown from a state.json,
                            # so the matching learnings.md marker line for the same finding is never shown twice

    for slug, st, path in _earlier_runs(ctx, active_slug):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        for cp, c in (st.get("checkpoints") or {}).items():
            if not isinstance(c, dict):
                continue
            for f in c.get("deferred") or []:
                if not isinstance(f, dict):
                    continue
                desc = str(f.get("description", "")).strip()
                if not desc:
                    continue
                round_no = f.get("round")
                if f.get("accepted"):
                    who = f.get("accepted_by") or "a human"
                    reason = str(f.get("reason", "")).strip()
                    line = f"{cp} {_finding_key(f)} ({f.get('severity', '?')}), accepted by {who}: {desc}"
                    if reason:
                        line += f" - reason: {reason}"
                    epoch = _digest_epoch(f.get("accepted_at")) or mtime
                    recency = _digest_recency(epoch, cp, round_no)
                    accepted_items.append((_digest_rank(_digest_words(desc), relevant_to, recency), line))
                    accepted_keys.add(f"{slug}/{cp}/round-{round_no}/{_finding_key(f)}")
                else:
                    line = f"{cp} {_finding_key(f)} ({f.get('severity', '?')}): {desc}"
                    epoch = _digest_epoch(f.get("at")) or mtime
                    recency = _digest_recency(epoch, cp, round_no)
                    open_items.append((_digest_rank(_digest_words(desc), relevant_to, recency), line))

    for i, raw in enumerate(_learnings_bullets(ctx)):
        m = _BULLET_HEAD_RE.match(raw)
        rest = m.group(1) if m else raw
        display = raw[1:].strip() if raw.startswith("-") else raw
        recency = _digest_recency(_digest_bullet_epoch(raw, i))
        fm = _ACCEPTED_FINDING_MARKER_RE.match(rest)
        if fm:
            if fm.group(1) in accepted_keys:
                continue  # the matching finding already shown from its own run's state.json
            accepted_items.append((_digest_rank(_digest_words(display), relevant_to, recency), display))
        elif re.match(r"^Accepted limit\b", rest, re.I):
            accepted_items.append((_digest_rank(_digest_words(display), relevant_to, recency), display))
        else:
            learn_items.append((_digest_rank(_digest_words(display), relevant_to, recency), display))

    lines = _digest_cap_lines(learn_items, "Learnings:", ".ratchet/learnings.md")
    lines += _digest_cap_lines(open_items, "Open findings (not fixed):",
                               "each run's state.json under .ratchet/runs/")
    lines += _digest_cap_lines(accepted_items, "Accepted limits:",
                               "each run's state.json under .ratchet/runs/, and .ratchet/learnings.md")
    lines += _digest_cap_lines(_related_spec_items(ctx, relevant_to), DIGEST_SPEC_HEAD, DIGEST_SPEC_PATH_NOTE)
    if not lines:
        return [DIGEST_HEADING, "There is no earlier work recorded for this project yet."]
    return [DIGEST_HEADING] + lines
